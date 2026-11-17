"""Operator readiness cannot be promoted by local runs or synthetic status."""
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

from max_trading_agent.readiness import ReadinessError, TradingReadiness
from max_trading_agent.runtime import EventJournal
from max_trading_agent.web import create_server


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "private-journal.sqlite3"
        self.readiness = TradingReadiness(self.path)

    def test_missing_journal_never_created_or_considered_ready(self):
        report = self.readiness.summary()
        self.assertFalse(self.path.exists())
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["decision"], "NO_TRADE")
        self.assertEqual(report["observations_status"], "NOT_OBSERVED")
        self.assertFalse(report["demo_authorized"])
        self.assertFalse(report["live_authorized"])
        self.assertFalse(report["broker_execution"])
        self.assertEqual(len(report["gates"]), 7)

    def test_counts_real_journal_activity_but_never_approves(self):
        journal = EventJournal(self.path)
        observed = journal.start("accept_mt5_terminal", mode="READ_ONLY")
        journal.append(
            observed, "Market Data Auditor", "ACCEPTANCE_REPORT", "PASS",
            "Local observations only",
            {"status": "OBSERVED_CONSISTENT", "owner_acceptance": "PENDING"},
        )
        journal.finish(observed, "COMPLETED")
        paper = journal.start("paper_demo", mode="PAPER")
        journal.finish(paper, "COMPLETED")
        signal = journal.start("research_signal", mode="SIGNAL")
        journal.finish(signal, "FAILED")
        report = self.readiness.summary()
        self.assertEqual(report["observations_status"],
                         "LOCAL_RUNS_NOT_INDEPENDENTLY_VERIFIED")
        self.assertEqual(report["observations"]["terminal_checks"]["completed"], 1)
        self.assertEqual(report["observations"]["paper_drills"]["completed"], 1)
        self.assertEqual(report["observations"]["signal_research"]["failed"], 1)
        self.assertEqual(report["owner_acceptance"], "PENDING")
        self.assertEqual(report["decision"], "NO_TRADE")
        self.assertFalse(report["demo_authorized"])
        self.assertFalse(report["live_authorized"])
        data = json.dumps(report)
        self.assertNotIn("account_binding_id", data)
        self.assertNotIn("snapshot_mac", data)
        self.assertNotIn("broker_server", data)
        self.assertNotIn("OBSERVED_CONSISTENT", data)
        self.assertEqual(report["gates"][-1]["status"], "LIVE_NOT_IMPLEMENTED")

    def test_read_only_query_never_mutates_journal_file(self):
        journal = EventJournal(self.path)
        run = journal.start("paper_demo", mode="PAPER")
        journal.finish(run, "COMPLETED")
        before = self.path.read_bytes()
        self.readiness.summary()
        self.assertEqual(before, self.path.read_bytes())

    def test_invalid_status_and_forged_live_mode_fail_closed(self):
        journal = EventJournal(self.path)
        run = journal.start("accept_mt5_terminal", mode="READ_ONLY")
        journal.finish(run, "COMPLETED")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE runs SET status='OWNER_APPROVED' WHERE run_id=?", (run,))
        with self.assertRaisesRegex(ReadinessError, "STATUS_INTEGRITY"):
            self.readiness.summary()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE runs SET status='COMPLETED', mode='AUTO_LIVE' WHERE run_id=?", (run,))
        with self.assertRaisesRegex(ReadinessError, "MODE_INTEGRITY"):
            self.readiness.summary()

    def test_malformed_journal_and_symlink_never_report_no_data(self):
        self.path.write_text("not a SQLite database", encoding="utf-8")
        with self.assertRaises(ReadinessError):
            self.readiness.summary()
        self.path.unlink()
        try:
            self.path.symlink_to(self.root / "missing.sqlite3")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ReadinessError, "SYMLINK"):
            self.readiness.summary()

    def test_api_readiness_is_read_only_loopback_and_integrity_409(self):
        server = create_server(port=0, journal_path=self.path,
                               knowledge_path=self.root / "knowledge.sqlite3")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def get():
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=4)
            conn.request("GET", "/api/readiness")
            resp = conn.getresponse()
            status, headers, data = resp.status, dict(resp.getheaders()), resp.read()
            conn.close()
            return status, headers, json.loads(data)

        status, headers, out = get()
        self.assertEqual(status, 200)
        self.assertEqual(out["decision"], "NO_TRADE")
        self.assertEqual(out["status"], "BLOCKED")
        self.assertFalse(out["live_authorized"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("ALTER TABLE runs RENAME TO unexpected_runs")
        status, _, out = get()
        self.assertEqual(status, 409)
        self.assertEqual(out["error"], "readiness_integrity_blocked")
        self.assertEqual(out["decision"], "NO_TRADE")
        self.assertFalse(out["broker_execution"])


if __name__ == "__main__":
    unittest.main()
