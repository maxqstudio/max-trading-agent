"""Offline G18 replication tests: synthetic histories only, never broker calls."""
import csv
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from max_trading_agent.disjoint_robustness import disjoint_dataset_robustness
from max_trading_agent.market_store import MarketDataError, MarketStore
from test_market_evidence import generate_rows


class DisjointRobustnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MarketStore(self.root / "evidence.sqlite3")
        self.options = dict(
            spread=0.0002, slippage=0.00003, fee_per_side=0.00001,
            folds=2, test_bars=24, epochs=2, alpha=0.2, gamma=0.6,
        )

    def dataset(self, shift=0, *, count=300, symbol="EURUSD",
                basis="MID", constant=False, gap=False):
        rows = generate_rows(count, gap=gap)
        for row in rows:
            dt = datetime.fromisoformat(row["time_utc"])
            row["time_utc"] = (dt + timedelta(minutes=15 * shift)).isoformat()
            if constant:
                row.update(open="1.20000000", high="1.20010000",
                           low="1.19990000", close="1.20000000")
        path = self.root / f"data-{shift}-{symbol}-{basis}-{count}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["time_utc", "open", "high", "low", "close"]
            )
            writer.writeheader()
            writer.writerows(rows)
        return self.store.import_csv(
            path, symbol=symbol, timeframe="M15",
            quote_basis=basis, source=f"synthetic-fixture-{shift}",
            rights="TEST_ONLY",
        )["dataset_id"]

    def run_batch(self, ids, **kw):
        return disjoint_dataset_robustness(self.store, ids, **{**self.options, **kw})

    def experiment_count(self):
        with closing(sqlite3.connect(self.store.path)) as db, db:
            return db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]

    def test_disjoint_replay_hash_and_no_trade_authority(self):
        ids = [self.dataset(0), self.dataset(400)]
        a = self.run_batch(ids)
        b = self.run_batch(ids)
        self.assertEqual(a, b)
        self.assertEqual(a["decision"], "NO_TRADE")
        self.assertEqual(a["execution"], "DISABLED")
        self.assertFalse(a["eligible_for_trading"])
        self.assertFalse(a["broker_execution"])
        self.assertFalse(a["dataset_independence_verified"])
        self.assertFalse(a["profit_values_pooled"])
        self.assertEqual(a["owner_acceptance"], "PENDING")
        self.assertEqual(a["dataset_count"], 2)
        self.assertEqual(len(a["report_sha256"]), 64)
        self.assertEqual(self.experiment_count(), 2)
        self.assertEqual([d["dataset_id"] for d in a["datasets"]], ids)
        self.assertTrue(all(len(d["constituent_experiment_id"]) == 64
                            for d in a["datasets"]))
        for item in a["datasets"]:
            record = self.store.experiment(item["constituent_experiment_id"])
            self.assertEqual(record["dataset_id"], item["dataset_id"])
            self.assertEqual(len(item["fold_scores_price_units_per_one_unit"]), 2)

    def test_all_losses_and_abstentions_remain_in_report(self):
        ids = [self.dataset(0, constant=True), self.dataset(400, constant=True)]
        report = self.run_batch(ids)
        self.assertEqual(report["datasets_all_folds_positive"], 0)
        self.assertEqual(report["datasets_surviving_doubled_cost"], 0)
        self.assertEqual(len(report["datasets"]), 2)
        for item in report["datasets"]:
            self.assertFalse(item["all_folds_positive_after_cost"])
            self.assertEqual(item["folds_positive_after_cost"], 0)
            for fold in item["fold_scores_price_units_per_one_unit"]:
                self.assertEqual(fold["q_net"], 0)
                self.assertEqual(fold["no_trade_net"], 0)

    def test_duplicate_or_bad_batch_fails_before_experiment_writes(self):
        a = self.dataset(0)
        b = self.dataset(400)
        bad = ([a], [a, a], [], [a, b, a], [a, "x"],
               [a, None], a, [a] * 6)
        for ids in bad:
            with self.subTest(ids=ids):
                with self.assertRaises(MarketDataError):
                    self.run_batch(ids)
        self.assertEqual(self.experiment_count(), 0)

    def test_overlap_and_reversed_order_fail_before_writes(self):
        a, overlapping, distinct = (
            self.dataset(0), self.dataset(100), self.dataset(400)
        )
        with self.assertRaisesRegex(MarketDataError, "OVERLAP"):
            self.run_batch([a, overlapping])
        with self.assertRaisesRegex(MarketDataError, "NOT_CHRONOLOGICAL"):
            self.run_batch([distinct, a])
        self.assertEqual(self.experiment_count(), 0)

    def test_mixed_symbol_is_rejected_for_common_price_costs(self):
        a, b = self.dataset(0), self.dataset(400, symbol="GBPUSD")
        with self.assertRaisesRegex(MarketDataError, "MIXED_INSTRUMENT"):
            self.run_batch([a, b])
        self.assertEqual(self.experiment_count(), 0)

    def test_non_mid_gaps_and_too_short_rejected_preflight(self):
        first = self.dataset(0)
        for kwargs in ({"basis": "BID"}, {"gap": True}, {"count": 160}):
            with self.subTest(kwargs=kwargs):
                later = self.dataset(400, **kwargs)
                with self.assertRaises(MarketDataError):
                    self.run_batch([first, later])
        self.assertEqual(self.experiment_count(), 0)

    def test_corrupted_second_dataset_does_not_log_first_as_batch(self):
        first, second = self.dataset(0), self.dataset(400)
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute(
                "UPDATE candles SET close=close+0.00001 "
                "WHERE dataset_id=? AND idx=99", (second,)
            )
        with self.assertRaisesRegex(MarketDataError, "DATASET_INTEGRITY_FAILED"):
            self.run_batch([first, second])
        self.assertEqual(self.experiment_count(), 0)

    def test_costs_hyperparameters_and_policy_budget_are_fail_closed(self):
        ids = [self.dataset(0), self.dataset(400)]
        for kw in ({"spread": -1}, {"spread": float("nan")},
                   {"fee_per_side": float("inf")}, {"alpha": float("nan")},
                   {"gamma": 1}, {"folds": 1}, {"folds": True},
                   {"test_bars": 200}, {"epochs": 0}, {"spread": 100}):
            with self.subTest(kwargs=kw):
                with self.assertRaises(MarketDataError):
                    self.run_batch(ids, **kw)
        self.assertEqual(self.experiment_count(), 0)

    def test_mutating_later_history_does_not_change_earlier_record(self):
        first, second = self.dataset(0), self.dataset(400)
        original = self.run_batch([first, second])
        # A separate, later history changes. No earlier model training may use it.
        later_changed = self.dataset(800, constant=True)
        replay = self.run_batch([first, later_changed])
        self.assertEqual(original["datasets"][0], replay["datasets"][0])
        self.assertNotEqual(original["report_sha256"], replay["report_sha256"])

    def test_cli_uses_private_store_and_never_executes(self):
        ids = [self.dataset(0), self.dataset(400)]
        proc = subprocess.run([
            sys.executable, "-m", "max_trading_agent", "rl-robustness",
            "--market-db", str(self.store.path), "--datasets", ",".join(ids),
            "--spread", "0.0002", "--slippage", "0.00003",
            "--fee-per-side", "0.00001", "--folds", "2",
            "--test-bars", "24", "--epochs", "2",
        ], text=True, capture_output=True, timeout=25)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[:300])
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["decision"], "NO_TRADE")
        self.assertEqual(payload["dataset_count"], 2)
        self.assertNotIn("order_send", proc.stdout)


if __name__ == "__main__":
    unittest.main()
