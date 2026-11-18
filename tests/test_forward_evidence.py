"""M08 read-only forward evidence & HTTP E2E. Synthetic data, no broker calls."""
from __future__ import annotations

import hashlib
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from max_trading_agent.forward_evidence import EvidenceError, ForwardEvidence
from max_trading_agent.runtime import EventJournal
from max_trading_agent.shadow import ShadowLedger
from max_trading_agent.watcher import WatchState
from max_trading_agent.web import create_server


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def timestamp(minutes: int = 0) -> str:
    return (datetime(2026, 10, 1, tzinfo=timezone.utc)
            + timedelta(minutes=minutes)).isoformat()


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = Path(home.name)
        self.file = self.home / "journal.sqlite3"

    def initialize(self):
        self.journal = EventJournal(self.file)
        WatchState(self.file)
        ShadowLedger(self.file)
        self.reporter = ForwardEvidence(self.file)

    def add_job(self, idx: int, *, symbol: str = "EURUSD",
                status: str = "COMPLETED", requested: int = 1) -> str:
        key = digest(f"job-{symbol}-{idx}")
        rid = "a" * 32
        with closing(sqlite3.connect(self.file)) as db, db:
            db.execute("""INSERT INTO watch_jobs (
                job_id,symbol,timeframe,bar_utc,snapshot_hash,status,
                research_run_id,created_utc,shadow_requested
            ) VALUES (?,?,?,?,?,?,?,?,?)""", (
                key, symbol, "M1", timestamp(idx), digest("snapshot"+str(idx)),
                status, rid, timestamp(idx), requested,
            ))
        return key

    def add_event(self, job_id: str, event_type: str):
        run_id = self.journal.start("monitor_market", mode="READ_ONLY")
        self.journal.append(
            run_id, "Hypothesis Strategist", event_type, "INFO",
            "Test-only synthetic hypothesis observation.",
            {"watch_job_id": job_id, "broker_execution": False},
        )
        self.journal.finish(run_id, "COMPLETED")

    def add_episode(self, job_id: str, *, name: str = "EURUSD",
                    scope: str = "account_one", side: str = "LONG",
                    pnl: float | None = 0.01, idx: int = 1,
                    origin: str = "LLM_RESEARCH", signed: bool = True,
                    status: str | None = None):
        binding = digest(scope) if signed else None
        status = status or ("SETTLED" if pnl is not None else "PENDING")
        price = 1.0
        fee = 0.0001
        exit_price = (price + pnl + 2 * fee if side == "LONG"
                      else price - pnl - 2 * fee) if pnl is not None else None
        entry_bid = price - 0.00012 if side == "LONG" else price + 0.00002
        entry_ask = price - 0.00002 if side == "LONG" else price + 0.00012
        exit_bid = (exit_price + 0.00002 if side == "LONG"
                    else exit_price - 0.00012) if pnl is not None else None
        exit_ask = (exit_price + 0.00012 if side == "LONG"
                    else exit_price - 0.00002) if pnl is not None else None
        with closing(sqlite3.connect(self.file)) as db, db:
            db.execute("""INSERT INTO shadow_episodes (
                candidate_id,origin,symbol,side,status,entry_snapshot_hash,
                entry_quote_utc,entry_bid,entry_ask,entry_price,slippage,
                fee_per_side,created_utc,exit_snapshot_hash,exit_quote_utc,
                exit_bid,exit_ask,exit_price,net_pnl_price_units,recorded_utc,
                account_binding_id,research_run_id,watch_job_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                "hyp_" + str(idx), origin, name, side, status,
                digest("entry-"+str(idx)), timestamp(idx), entry_bid, entry_ask,
                price, 0.00002, fee, timestamp(idx + 1),
                digest("exit-"+str(idx)) if pnl is not None else None,
                timestamp(idx+2) if pnl is not None else None,
                exit_bid, exit_ask,
                exit_price, pnl,
                timestamp(idx+3) if pnl is not None else None,
                binding, "a" * 32 if origin == "LLM_RESEARCH" else None,
                job_id if origin == "LLM_RESEARCH" else None,
            ))

    def test_missing_path_not_created_by_read(self):
        report = ForwardEvidence(self.file).summary()
        self.assertEqual(report["status"], "NO_DATA")
        self.assertFalse(self.file.exists())
        self.assertEqual(report["symbols"], [])
        self.assertFalse(report["broker_execution"])
        self.assertEqual(report["decision"], "NO_TRADE")

    def test_empty_journal_explicit_no_data(self):
        self.journal = EventJournal(self.file)
        report = ForwardEvidence(self.file).summary()
        self.assertEqual(report["status"], "NO_DATA")
        self.assertEqual(report["totals"]["attempted_cycles"], 0)

    def test_all_attempts_losses_abstain_rejection_failure_and_pending(self):
        self.initialize()
        good = self.add_job(1)
        bad = self.add_job(2)
        abstain = self.add_job(3)
        reject = self.add_job(4, status="FAILED")
        pending = self.add_job(5, status="REVIEW_REQUIRED")
        skipped = self.add_job(6, status="COMPLETED", requested=0)
        self.add_episode(good, pnl=-0.015, idx=1)
        self.add_episode(bad, pnl=0.008, idx=2, side="SHORT")
        self.add_episode(pending, pnl=None, idx=5)
        self.add_event(abstain, "HYPOTHESIS_ABSTAINED")
        self.add_event(reject, "HYPOTHESIS_REJECTED")
        self.add_event(reject, "HYPOTHESIS_REJECTED")  # duplicates never add attempts
        self.add_event(skipped, "HYPOTHESIS_ABSTAINED")  # non-opt-in ignored

        report = self.reporter.summary()
        total = report["totals"]
        self.assertEqual(report["status"], "OBSERVED")
        self.assertEqual(total["attempted_cycles"], 5)
        self.assertEqual(total["abstained"], 1)
        self.assertEqual(total["rejected"], 1)
        self.assertEqual(total["failed_jobs"], 1)
        self.assertEqual(total["review_required_jobs"], 1)
        self.assertEqual(total["settled_episodes"], 2)
        self.assertEqual(total["pending_episodes"], 1)
        self.assertEqual(total["unmatched_cycles"], 0)
        group = report["symbols"][0]
        self.assertEqual(group["evidence_grade"], "INSUFFICIENT_FORWARD_SAMPLE")
        self.assertEqual(group["attempts"], 5)
        self.assertEqual(group["hypothetical_pnl"]["winners"], 1)
        self.assertEqual(group["hypothetical_pnl"]["losers"], 1)
        self.assertAlmostEqual(
            group["hypothetical_pnl"]["net_price_units_per_unit"], -0.007
        )
        self.assertEqual(group["hypothetical_pnl"]["no_trade_baseline_price_units"], 0)
        self.assertFalse(report["financial_profit_proven"])
        self.assertNotIn("a" * 32, json.dumps(report))

    def test_unmatched_and_pending_are_not_omitted(self):
        self.initialize()
        self.add_job(1, status="FAILED")
        self.add_job(2, status="BLOCKED")
        self.add_job(3, status="RUNNING")
        self.add_job(4, status="PENDING")
        totals = self.reporter.summary()["totals"]
        self.assertEqual(totals["attempted_cycles"], 4)
        self.assertEqual(totals["unmatched_cycles"], 4)
        self.assertEqual(totals["failed_jobs"], 1)
        self.assertEqual(totals["blocked_jobs"], 1)
        self.assertEqual(totals["running_jobs"], 1)
        self.assertEqual(totals["pending_jobs"], 1)

    def test_multiple_accounts_same_symbol_withhold_aggregated_pnl(self):
        self.initialize()
        j1 = self.add_job(1)
        j2 = self.add_job(2)
        self.add_episode(j1, idx=1, scope="a", pnl=0.5)
        self.add_episode(j2, idx=2, scope="b", pnl=-0.5)
        report = self.reporter.summary()
        item = report["symbols"][0]
        self.assertEqual(item["account_scope"], "MIXED")
        self.assertEqual(item["evidence_grade"], "ACCOUNT_MIXED")
        self.assertIsNone(item["hypothetical_pnl"])
        self.assertIsNone(item["execution_assumptions"])
        self.assertNotIn(digest("a"), json.dumps(report))
        self.assertNotIn(digest("b"), json.dumps(report))

    def test_multi_symbol_is_not_aggregated_as_portfolio_pnl(self):
        self.initialize()
        a = self.add_job(1, symbol="EURUSD")
        b = self.add_job(2, symbol="XAUUSD")
        self.add_episode(a, name="EURUSD", pnl=0.01, idx=1)
        self.add_episode(b, name="XAUUSD", pnl=10.0, idx=2)
        report = self.reporter.summary()
        self.assertEqual([r["symbol"] for r in report["symbols"]],
                         ["EURUSD", "XAUUSD"])
        self.assertEqual(report["totals"]["settled_episodes"], 2)
        self.assertNotIn("net_price_units_per_unit", report["totals"])
        self.assertAlmostEqual(report["symbols"][0]["hypothetical_pnl"]["net_price_units_per_unit"], 0.01)
        self.assertAlmostEqual(report["symbols"][1]["hypothetical_pnl"]["net_price_units_per_unit"], 10.0)

    def test_predeclared_sample_gate_still_never_approves_orders(self):
        self.initialize()
        for i in range(1, 31):
            job = self.add_job(i)
            self.add_episode(job, pnl=0.0005 if i % 3 else -0.0006, idx=i)
        report = self.reporter.summary()
        self.assertEqual(report["symbols"][0]["settled"], 30)
        self.assertEqual(report["symbols"][0]["evidence_grade"], "DESCRIPTIVE_ONLY")
        self.assertFalse(report["financial_profit_proven"])
        self.assertFalse(report["broker_execution"])
        self.assertEqual(report["decision"], "NO_TRADE")

    def test_legacy_manual_episode_excluded_and_counted(self):
        self.initialize()
        with closing(sqlite3.connect(self.file)) as db, db:
            db.execute("""INSERT INTO shadow_episodes (
                candidate_id,origin,symbol,side,status,entry_snapshot_hash,
                entry_quote_utc,entry_bid,entry_ask,entry_price,slippage,
                fee_per_side,created_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                "manual_001", "USER_RESEARCH", "EURUSD", "LONG", "PENDING",
                digest("manual"), timestamp(1), 0.9999, 1.0001, 1.0001,
                0, 0, timestamp(1),
            ))
        report = self.reporter.summary()
        self.assertEqual(report["totals"]["legacy_manual_excluded"], 1)
        self.assertEqual(report["totals"]["attempted_cycles"], 0)
        self.assertEqual(report["symbols"], [])
        self.assertEqual(report["status"], "OBSERVED")

    def test_legacy_watch_schema_cannot_disappear_as_no_data(self):
        self.journal = EventJournal(self.file)
        with closing(sqlite3.connect(self.file)) as db, db:
            db.execute("""CREATE TABLE watch_jobs(
                job_id TEXT PRIMARY KEY, symbol TEXT, status TEXT
            )""")
            db.execute("INSERT INTO watch_jobs VALUES(?,?,?)",
                       (digest("old"), "EURUSD", "RUNNING"))
        with self.assertRaisesRegex(EvidenceError, "LEGACY_SCHEMA"):
            ForwardEvidence(self.file).summary()

    def test_invalid_shadow_opt_in_is_not_silently_excluded(self):
        self.initialize()
        self.add_job(1, status="FAILED", requested=2)
        with self.assertRaisesRegex(EvidenceError, "INTEGRITY"):
            self.reporter.summary()
        with closing(sqlite3.connect(self.file)) as db, db:
            db.execute("UPDATE watch_jobs SET shadow_requested=-1")
        with self.assertRaises(EvidenceError):
            self.reporter.summary()

    def test_settled_outcome_requires_all_exit_provenance(self):
        self.initialize()
        jid = self.add_job(1)
        self.add_episode(jid, pnl=0.01, idx=1)
        for column in ("exit_bid", "exit_snapshot_hash", "recorded_utc"):
            with self.subTest(column=column):
                with closing(sqlite3.connect(self.file)) as db, db:
                    db.execute(f"UPDATE shadow_episodes SET {column}=NULL")
                with self.assertRaisesRegex(EvidenceError, "INTEGRITY"):
                    self.reporter.summary()
                with closing(sqlite3.connect(self.file)) as db, db:
                    db.execute("DELETE FROM shadow_episodes")
                self.add_episode(jid, pnl=0.01, idx=1)

    def test_pending_outcome_cannot_hide_partial_exit(self):
        self.initialize()
        jid = self.add_job(1)
        self.add_episode(jid, pnl=None, idx=1)
        with closing(sqlite3.connect(self.file)) as db, db:
            db.execute("UPDATE shadow_episodes SET exit_quote_utc=?",
                       (timestamp(3),))
        with self.assertRaises(EvidenceError):
            self.reporter.summary()

    def test_recomputed_pnl_cannot_launder_inconsistent_fill(self):
        self.initialize()
        jid = self.add_job(1)
        self.add_episode(jid, pnl=0.01, idx=1)
        with closing(sqlite3.connect(self.file)) as db, db:
            db.execute("UPDATE shadow_episodes SET entry_price=0.9,"
                       "net_pnl_price_units=0.11")
        with self.assertRaises(EvidenceError):
            self.reporter.summary()

    def test_cost_assumptions_disclosed_without_account_identity(self):
        self.initialize()
        jid = self.add_job(1)
        self.add_episode(jid, pnl=0.01, idx=1)
        data = self.reporter.summary()["symbols"][0]
        costs = data["execution_assumptions"]
        self.assertEqual(costs["modeled_fee_per_side"]["min"], 0.0001)
        self.assertEqual(costs["modeled_slippage_per_side"]["max"], 0.00002)
        self.assertEqual(costs["recorded_entry_spread"]["min"], 0.0001)
        self.assertEqual(costs["recorded_exit_spread"]["max"], 0.0001)
        self.assertNotIn(digest("account_one"), json.dumps(data))

    def test_unknown_episode_origin_is_integrity_violation(self):
        self.initialize()
        jid = self.add_job(1)
        self.add_episode(jid, pnl=0.1, idx=1)
        with closing(sqlite3.connect(self.file)) as db, db:
            db.execute("UPDATE shadow_episodes SET origin='LIVE'")
        with self.assertRaises(EvidenceError):
            self.reporter.summary()

    def test_corrupted_net_price_and_future_time_are_blocked(self):
        self.initialize()
        jid = self.add_job(1)
        self.add_episode(jid, pnl=0.01, idx=1)
        with closing(sqlite3.connect(self.file)) as db, db:
            db.execute("UPDATE shadow_episodes SET net_pnl_price_units=9999")
        with self.assertRaisesRegex(EvidenceError, "INTEGRITY"):
            self.reporter.summary()
        with closing(sqlite3.connect(self.file)) as db, db:
            db.execute("UPDATE shadow_episodes SET net_pnl_price_units=0.01,"
                       "exit_quote_utc=entry_quote_utc")
        with self.assertRaises(EvidenceError):
            self.reporter.summary()

    def test_unbound_fake_autonomous_episode_fails_closed(self):
        self.initialize()
        jid = self.add_job(1)
        self.add_episode(jid, pnl=1.0, idx=1, signed=False)
        with self.assertRaises(EvidenceError):
            self.reporter.summary()

    def test_duplicate_episode_for_one_job_and_conflicting_reports_blocked(self):
        self.initialize()
        jid = self.add_job(1)
        self.add_episode(jid, pnl=0.005, idx=1)
        self.add_episode(jid, pnl=0.005, idx=2)
        with self.assertRaises(EvidenceError):
            self.reporter.summary()

    def test_http_evidence_projection_read_only_and_security(self):
        self.initialize()
        jid = self.add_job(1)
        self.add_episode(jid, pnl=-0.002, idx=1)
        server = create_server(
            port=0, journal_path=self.file, knowledge_path=self.home / "kb.sqlite3"
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def fetch(path, host=None):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=6)
            try:
                headers = {"Host": host} if host else {}
                conn.request("GET", path, headers=headers)
                result = conn.getresponse()
                return result.status, result.read()
            finally:
                conn.close()

        try:
            status, raw = fetch("/api/evidence")
            self.assertEqual(status, 200)
            data = json.loads(raw)
            self.assertEqual(data["totals"]["attempted_cycles"], 1)
            self.assertLess(data["symbols"][0]["hypothetical_pnl"]["net_price_units_per_unit"], 0)
            self.assertNotIn(digest("account_one"), raw.decode())
            self.assertNotIn("PRIVATE_BROKER", raw.decode())
            before = self.file.stat().st_size
            for _ in range(3):
                self.assertEqual(fetch("/api/evidence")[0], 200)
            self.assertEqual(self.file.stat().st_size, before)
            self.assertEqual(fetch("/api/evidence", host="attacker.invalid")[0], 403)
            _, script = fetch("/app.js")
            self.assertIn(b"/api/evidence", script)
            _, html = fetch("/")
            self.assertIn(b"FORWARD SHADOW EVIDENCE", html)

            with closing(sqlite3.connect(self.file)) as db, db:
                db.execute("UPDATE shadow_episodes SET net_pnl_price_units=999")
            code, blocked = fetch("/api/evidence")
            self.assertEqual(code, 409)
            self.assertEqual(json.loads(blocked)["decision"], "NO_TRADE")
            self.assertNotIn("999", blocked.decode())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
