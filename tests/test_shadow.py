"""Forward-only shadow research regression; does not import or contact MT5."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from max_trading_agent.shadow import ShadowError, ShadowLedger
from max_trading_agent.shadow_mission import ShadowMission
from max_trading_agent.runtime import EventJournal


def fake_snapshot(*, now=None, symbol="EURUSD", bid=1.05, ask=1.0502,
                  quote_age=1, mode="READ_ONLY"):
    now = now or datetime.now(timezone.utc)
    candles = [
        {"time_utc": (now - timedelta(minutes=15 * (i + 2))).isoformat(),
         "open": 1.04, "high": 1.06, "low": 1.03, "close": 1.05}
        for i in reversed(range(4))
    ]
    return {
        "source": "MT5_LOCAL_TERMINAL", "mode": mode, "execution": "DISABLED",
        "captured_at_utc": now.isoformat(),
        "account": {"login": 123123123, "password": "PRIVATE"},
        "symbol": {"name": symbol}, "timeframe": "M15",
        "quote": {"bid": bid, "ask": ask,
                  "time_utc": (now - timedelta(seconds=quote_age)).isoformat()},
        "candles": candles,
    }


class ShadowLedgerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "ledger.sqlite3"
        self.ledger = ShadowLedger(self.path)
        self.before = datetime.now(timezone.utc) - timedelta(seconds=9)
        self.entry = fake_snapshot(now=self.before)
        self.later = fake_snapshot(now=datetime.now(timezone.utc),
                                   bid=1.052, ask=1.0522)

    def record(self, candidate="idea_0001", side="LONG", slip=0.00002, fee=0.00001):
        return self.ledger.record(
            candidate, self.entry, side=side,
            slippage=slip, fee_per_side=fee,
        )

    def test_long_forward_outcome_price_units_no_real_trade(self):
        opened = self.record()
        self.assertEqual(opened["status"], "PENDING")
        self.assertEqual(opened["entry_price"], 1.05022)
        result = self.ledger.settle("idea_0001", self.later)
        self.assertEqual(result["status"], "SETTLED")
        self.assertEqual(result["mode"], "SHADOW")
        self.assertEqual(result["fill_type"], "HYPOTHETICAL")
        self.assertFalse(result["broker_execution"])
        self.assertGreater(result["net_pnl_price_units"], 0)
        self.assertEqual(result["baseline_no_trade_pnl"], 0)
        self.assertNotIn("123123123", json.dumps(result))
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(ShadowLedger(self.path).get("idea_0001"), result)

    def test_short_uses_bid_entry_ask_exit(self):
        self.record(side="SHORT")
        result = self.ledger.settle("idea_0001", self.later)
        self.assertAlmostEqual(result["entry_price"], 1.04998)
        self.assertAlmostEqual(result["exit_price"], 1.05222)
        self.assertLess(result["net_pnl_price_units"], 0)

    def test_costs_cannot_improve_same_observation(self):
        self.record("idea_low", slip=0, fee=0)
        self.record("idea_high", slip=0.00004, fee=0.00003)
        low = self.ledger.settle("idea_low", self.later)
        high = self.ledger.settle("idea_high", self.later)
        self.assertLess(high["net_pnl_price_units"], low["net_pnl_price_units"])

    def test_repeated_open_and_settlement_idempotent(self):
        first = self.record()
        again = self.record()
        self.assertEqual(first, again)
        settled = self.ledger.settle("idea_0001", self.later)
        self.assertEqual(settled, self.ledger.settle("idea_0001", self.later))
        changed = fake_snapshot(now=datetime.now(timezone.utc),
                                bid=1.054, ask=1.0542)
        with self.assertRaisesRegex(ShadowError, "CONFLICTING_SETTLEMENT"):
            self.ledger.settle("idea_0001", changed)
        with self.assertRaisesRegex(ShadowError, "CANDIDATE_REUSE_CONFLICT"):
            self.record(side="SHORT")

    def test_stale_snapshot_and_retrospective_settlement_rejected(self):
        old = fake_snapshot(now=datetime.now(timezone.utc) - timedelta(minutes=2))
        with self.assertRaisesRegex(ShadowError, "SNAPSHOT_NOT_FRESH"):
            self.ledger.record("idea_old", old, side="LONG",
                               slippage=0, fee_per_side=0)
        self.record()
        with self.assertRaisesRegex(ShadowError, "NOT_A_FORWARD_OBSERVATION"):
            self.ledger.settle("idea_0001", self.entry)
        self.assertEqual(self.ledger.get("idea_0001")["status"], "PENDING")

    def test_mismatched_symbol_wrong_mode_bad_quote_and_missing_candidate(self):
        self.record()
        with self.assertRaisesRegex(ShadowError, "SYMBOL_MISMATCH"):
            self.ledger.settle("idea_0001", fake_snapshot(symbol="GBPUSD"))
        with self.assertRaisesRegex(ShadowError, "UNTRUSTED_OR_INVALID_MARKET_SNAPSHOT"):
            self.ledger.settle("idea_0001", fake_snapshot(mode="AUTO_LIVE"))
        with self.assertRaisesRegex(ShadowError, "UNTRUSTED_OR_INVALID_MARKET_SNAPSHOT"):
            self.ledger.settle("idea_0001", fake_snapshot(ask=1.0, bid=1.2))
        with self.assertRaisesRegex(ShadowError, "CANDIDATE_NOT_FOUND"):
            self.ledger.settle("missing_candidate", self.later)

    def test_invalid_costs_or_identity_never_create_episode(self):
        for slip, fee in ((float("nan"), 0), (0, float("inf")), (-1, 0)):
            with self.assertRaises(ShadowError):
                self.record("idea_bad", slip=slip, fee=fee)
        for side in ("BUY", "NO_TRADE", "LIVE"):
            with self.assertRaisesRegex(ShadowError, "INVALID_RESEARCH_PROPOSAL"):
                self.record("idea_bad", side=side)
        with self.assertRaisesRegex(ShadowError, "INVALID_CANDIDATE_ID"):
            self.record("../../leak")
        self.assertEqual(self.path.exists(), True)

    def test_shadow_mission_journals_episodes_and_veto(self):
        journal = EventJournal(self.path.parent / "events.sqlite3")
        mission = ShadowMission(journal, self.ledger)
        first = mission.open("hypothesis_01", self.entry, side="LONG",
                             slippage=0.00002, fee_per_side=0.00001)
        second = mission.settle("hypothesis_01", self.later)
        self.assertEqual(second["episode"]["status"], "SETTLED")
        first_events = journal.events(first["run_id"])
        second_events = journal.events(second["run_id"])
        self.assertEqual(first_events[0]["event_type"], "SHADOW_OPENED")
        self.assertEqual(second_events[0]["event_type"], "SHADOW_SETTLED")
        self.assertFalse(second_events[0]["details"]["broker_execution"])
        self.assertEqual(second_events[-1]["details"]["action"], "NO_TRADE")
        self.assertTrue(all(x["mode"] == "SHADOW" for x in journal.runs()))

    def test_failure_still_no_trade_and_no_broker_calls(self):
        journal = EventJournal(self.path.parent / "events.sqlite3")
        mission = ShadowMission(journal, self.ledger)
        with self.assertRaises(ShadowError):
            mission.settle("missing_candidate", self.later)
        run = journal.runs()[0]
        self.assertEqual(run["status"], "FAILED")
        self.assertEqual(journal.events(run["run_id"])[-1]["details"]["action"], "NO_TRADE")


if __name__ == "__main__":
    unittest.main()
