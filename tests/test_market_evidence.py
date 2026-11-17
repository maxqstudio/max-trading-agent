"""Regression tests for historical data leakage, costs and immutable evidence.

Fixtures are synthetic and offline. These tests never import MetaTrader5,
connect to broker servers or use real account credentials.
"""
import csv
import math
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from max_trading_agent.backtest_mission import BacktestMission
from max_trading_agent.evidence import desired_long, evaluate_window, run_evidence
from max_trading_agent.market_store import MarketDataError, MarketStore
from max_trading_agent.runtime import EventJournal

START = datetime(2023, 1, 1, tzinfo=timezone.utc)


def generate_rows(count=180, gap=False):
    rows = []
    for i in range(count):
        index = i if not gap or i < 60 else i + 1
        price = 1.2 + 0.006 * math.sin(i / 4) + 0.0001 * i
        rows.append({
            "time_utc": (START + timedelta(minutes=15 * index)).isoformat(),
            "open": f"{price:.8f}", "high": f"{price + 0.002:.8f}",
            "low": f"{price - 0.002:.8f}", "close": f"{price + 0.0002:.8f}",
        })
    return rows


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        workdir = tempfile.TemporaryDirectory()
        self.addCleanup(workdir.cleanup)
        self.root = Path(workdir.name)
        self.store = MarketStore(self.root / "db.sqlite3")
        self.rows = generate_rows()
        self.file = self.root / "bars.csv"
        self._write()
        self.metadata = {"symbol": "EURUSD", "timeframe": "M15",
                         "quote_basis": "MID", "source": "synthetic-fixture",
                         "rights": "TEST_ONLY"}

    def _write(self):
        with self.file.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "time_utc", "open", "high", "low", "close"])
            writer.writeheader()
            writer.writerows(self.rows)

    def _import(self):
        return self.store.import_csv(self.file, **self.metadata)

    def _run(self, spread=0.0002, slippage=0.00003, fee=0.00001):
        ds = self._import()
        return run_evidence(
            self.store, ds["dataset_id"], spread=spread,
            slippage=slippage, fee_per_side=fee,
        )

    def test_import_idempotent_and_cross_process_replay(self):
        first = self._import()
        again = self._import()
        self.assertFalse(first["existing"])
        self.assertTrue(again["existing"])
        self.assertEqual(first["dataset_id"], again["dataset_id"])
        reopened = MarketStore(self.store.path)
        self.assertEqual(reopened.manifest(first["dataset_id"])["count"], 180)
        self.assertEqual(reopened.candles(first["dataset_id"]),
                         self.store.candles(first["dataset_id"]))

    def test_provenance_conflict_for_identical_candles(self):
        self._import()
        with self.assertRaisesRegex(MarketDataError, "PROVENANCE_CONFLICT"):
            self.store.import_csv(
                self.file, **{**self.metadata, "source": "untrusted-other-source"}
            )

    def test_real_gaps_are_reported_not_interpolated(self):
        self.rows = generate_rows(gap=True)
        self._write()
        ds = self._import()
        self.assertEqual(ds["count"], 180)
        self.assertEqual(ds["gap_count"], 1)
        self.assertEqual(ds["missing_intervals"], 1)

    def test_invalid_data_never_partially_imports(self):
        variants = [
            (0, "time_utc", "2023-01-01T00:00:00"),
            (11, "time_utc", self.rows[10]["time_utc"]),
            (10, "open", "NaN"),
            (10, "low", "100.00"),
            (10, "close", "-1"),
            (12, "time_utc", "2099-01-01T00:00:00+00:00"),
        ]
        for idx, key, bad in variants:
            with self.subTest(idx=idx, key=key, bad=bad):
                original = self.rows[idx][key]
                self.rows[idx][key] = bad
                self._write()
                with self.assertRaises(MarketDataError):
                    self._import()
                self.rows[idx][key] = original
        with sqlite3.connect(self.store.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM datasets").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM candles").fetchone()[0], 0)

    def test_non_mid_basis_rejected_for_simulator(self):
        self.metadata["quote_basis"] = "BID"
        ds = self._import()
        with self.assertRaisesRegex(MarketDataError, "MID_OHLC_REQUIRED"):
            run_evidence(self.store, ds["dataset_id"], spread=0, slippage=0, fee_per_side=0)

    def test_train_holdout_no_future_leakage(self):
        original = self._run()
        original_dev = original["result"]["development"]
        # Modify only holdout candles. Train result must be bit-identical.
        for i in range(135, 150):
            self.rows[i]["close"] = self.rows[i]["high"]
        self._write()
        mutated = self._run()
        self.assertEqual(original_dev, mutated["result"]["development"])
        self.assertNotEqual(original["result"]["holdout"],
                            mutated["result"]["holdout"])

    def test_bar_current_close_cannot_change_action_at_its_open(self):
        ds = self._import()
        candles = self.store.candles(ds["dataset_id"])
        chosen = desired_long(candles, 60)
        candles[60]["close"] = candles[60]["close"] * 100
        self.assertEqual(chosen, desired_long(candles, 60))
        self.assertNotEqual(chosen, desired_long(candles, 61)) if (
            sum(x["close"] for x in candles[53:61]) / 8 >
            sum(x["close"] for x in candles[58:61]) / 3
        ) else self.assertIsInstance(desired_long(candles, 61), bool)

    def test_cost_monotonicity_and_no_trade_baseline(self):
        low = self._run(spread=0, slippage=0, fee=0)
        high = self._run(spread=0.0004, slippage=0.0001, fee=0.0001)
        for section in ("development", "holdout"):
            a = low["result"][section]
            b = high["result"][section]
            self.assertEqual(a["trades"], b["trades"])
            self.assertLessEqual(b["net_pnl_price_units_per_unit"],
                                 a["net_pnl_price_units_per_unit"])
            self.assertEqual(a["baseline_no_trade_pnl"], 0.0)
        self.assertEqual(high["result"]["decision"], "NO_TRADE")
        self.assertFalse(high["result"]["trade_authorization"])

    def test_untrusted_overfit_returns_research_only(self):
        experiment = self._run()
        data = experiment["result"]["holdout"]
        self.assertIn(data["evidence_grade"], {"DESCRIPTIVE_ONLY", "INSUFFICIENT_TRADES"})
        self.assertTrue(all(t["reason"] in ("NEXT_OPEN_EXIT", "END_MARK_NOT_FILL")
                            for t in data["trade_records"]))
        self.assertEqual(data["all_fills"], "SYNTHETIC_MODEL_ONLY")
        self.assertEqual(experiment["result"]["decision"], "NO_TRADE")

    def test_persisted_experiment_replay_and_distinct_cost_configs(self):
        e1 = self._run()
        e2 = self._run()
        e3 = self._run(spread=0.0003)
        self.assertEqual(e1["experiment_id"], e2["experiment_id"])
        self.assertNotEqual(e1["experiment_id"], e3["experiment_id"])
        recreated = MarketStore(self.store.path).experiment(e1["experiment_id"])
        self.assertEqual(recreated["result"], e1["result"])
        self.assertEqual(recreated["config"], e1["config"])

    def test_tampered_candle_dataset_is_not_accepted(self):
        ds = self._import()
        with sqlite3.connect(self.store.path) as db:
            db.execute("UPDATE candles SET close=close+0.00001 "
                       "WHERE dataset_id=? AND idx=5", (ds["dataset_id"],))
        with self.assertRaisesRegex(MarketDataError, "INTEGRITY"):
            run_evidence(self.store, ds["dataset_id"],
                         spread=0, slippage=0, fee_per_side=0)

    def test_backtest_mission_records_real_experiment_and_independent_veto(self):
        ds = self._import()
        journal = EventJournal(self.root / "events.sqlite3")
        mission = BacktestMission(journal, self.store)
        report = mission.execute(ds["dataset_id"],
                                 spread=0.0002, slippage=0.00003, fee_per_side=0.00001)
        self.assertEqual(report["decision"], "NO_TRADE")
        self.assertEqual(journal.runs()[0]["mode"], "BACKTEST")
        events = EventJournal(journal.path).events(report["run_id"])
        self.assertEqual([x["event_type"] for x in events],
                         ["DATASET_VERIFIED", "HYPOTHESIS_TESTED", "DECISION", "REPORT_READY"])
        self.assertEqual(events[1]["details"]["experiment_id"], report["experiment_id"])
        self.assertFalse(events[2]["details"]["order_send_called"])

    def test_invalid_evidence_mission_fails_and_vetoes(self):
        journal = EventJournal(self.root / "events.sqlite3")
        mission = BacktestMission(journal, self.store)
        with self.assertRaises(MarketDataError):
            mission.execute("missing_dataset", spread=0, slippage=0, fee_per_side=0)
        run_id = journal.runs()[0]["run_id"]
        self.assertEqual(journal.runs()[0]["status"], "FAILED")
        self.assertEqual(journal.events(run_id)[-1]["details"]["action"], "NO_TRADE")


if __name__ == "__main__":
    unittest.main()
