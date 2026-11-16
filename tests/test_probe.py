"""Read-only observation journal: no sensitive account data in persisted events."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from max_trading_agent.mt5_readonly import MT5ReadError
from max_trading_agent.probe import ProbeMission
from max_trading_agent.runtime import EventJournal


def snapshot():
    return {
        "symbol": {"name": "EURUSD"},
        "timeframe": "M15",
        "captured_at_utc": "2026-11-16T17:00:00+00:00",
        "quote": {"time_utc": "2026-11-16T16:59:58+00:00",
                  "age_seconds": 2.0},
        "account": {"trade_mode": "DEMO", "login": 123456,
                    "server": "NOT_FOR_PUBLIC_LOG", "balance": 5000},
        "candles": [{"open": 1.0}] * 3,
        "positions": [{"ticket": 123456}],
        "orders": [],
        "private_token": "NEVER_EXPORT",
    }


class ProbeJournalTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "events.sqlite3"
        self.journal = EventJournal(self.path)

    def test_read_only_probe_event_and_no_trade(self):
        client = Mock()
        client.capture.return_value = snapshot()
        run_id, full = ProbeMission(self.journal, client).execute("EURUSD", bars=3)
        self.assertEqual(full["private_token"], "NEVER_EXPORT")
        events = EventJournal(self.path).events(run_id)
        self.assertEqual(self.journal.runs()[0]["mode"], "READ_ONLY")
        self.assertEqual(self.journal.runs()[0]["status"], "COMPLETED")
        self.assertEqual([e["event_type"] for e in events],
                         ["PROBE_STARTED", "BROKER_SNAPSHOT", "DECISION"])
        safe = json.dumps(events)
        for secret in ("123456", "NEVER_EXPORT", "NOT_FOR_PUBLIC_LOG", "5000"):
            self.assertNotIn(secret, safe)
        self.assertEqual(events[-1]["details"]["action"], "NO_TRADE")
        self.assertFalse(events[-1]["details"]["order_send_called"])
        client.capture.assert_called_once_with("EURUSD", timeframe="M15", bars=3)

    def test_failure_persists_safe_error_and_no_trade(self):
        client = Mock()
        client.capture.side_effect = MT5ReadError("STALE_OR_FUTURE_TICK")
        with self.assertRaises(MT5ReadError):
            ProbeMission(self.journal, client).execute("EURUSD")
        run = self.journal.runs()[0]
        self.assertEqual(run["status"], "FAILED")
        events = self.journal.events(run["run_id"])
        self.assertEqual(events[-1]["details"]["action"], "NO_TRADE")
        self.assertEqual(events[1]["details"]["error"]["code"], "STALE_OR_FUTURE_TICK")

    def test_runtime_refuses_unapproved_mode_transitions(self):
        with self.assertRaises(ValueError):
            self.journal.start("observe_mt5")
        with self.assertRaises(ValueError):
            self.journal.start("analyze_symbol", mode="READ_ONLY")
        with self.assertRaises(ValueError):
            self.journal.start("trade_live", mode="READ_ONLY")
        self.assertEqual(self.journal.runs(), [])


if __name__ == "__main__":
    unittest.main()
