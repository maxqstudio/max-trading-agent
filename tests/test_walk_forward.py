"""Walk-forward historical tests: synthetic only, no MetaTrader5 or order APIs."""
import csv
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from max_trading_agent.market_store import MarketDataError, MarketStore
from max_trading_agent.walk_forward import walk_forward_q
from test_market_evidence import generate_rows


class WalkForwardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = MarketStore(self.root / "evidence.sqlite3")
        self.rows = generate_rows(360)
        self.csv = self.root / "fixture.csv"

    def dataset(self, *, basis="MID", gap=False):
        rows = generate_rows(360, gap=True) if gap else self.rows
        with self.csv.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "time_utc", "open", "high", "low", "close"
            ])
            writer.writeheader()
            writer.writerows(rows)
        return self.store.import_csv(
            self.csv, symbol="EURUSD", timeframe="M15",
            quote_basis=basis, source="fully-synthetic-evidence",
            rights="TEST_ONLY",
        )["dataset_id"]

    def run_test(self, dataset_id, **kw):
        options = {
            "spread": 0.0002,
            "slippage": 0.00003,
            "fee_per_side": 0.00001,
            "folds": 3, "test_bars": 32,
            "epochs": 8, "alpha": 0.2, "gamma": 0.6,
        }
        options.update(kw)
        return walk_forward_q(self.store, dataset_id, **options)

    def test_replay_provenance_and_never_broker_authority(self):
        dataset_id = self.dataset()
        first = self.run_test(dataset_id)
        repeat = self.run_test(dataset_id)
        self.assertEqual(first, repeat)
        self.assertEqual(first["result"]["decision"], "NO_TRADE")
        self.assertEqual(first["result"]["execution"], "DISABLED")
        self.assertFalse(first["result"]["broker_execution"])
        self.assertFalse(first["result"]["eligible_for_trading"])
        self.assertEqual(first["result"]["owner_acceptance"], "PENDING")
        self.assertEqual(first["result"]["evidence_grade"],
                         "DESCRIPTIVE_SYNTHETIC_ONLY")
        self.assertEqual(first["result"]["folds_completed"], 3)
        self.assertEqual(len(first["experiment_id"]), 64)
        disk_record = MarketStore(self.store.path).experiment(first["experiment_id"])
        self.assertEqual(disk_record["result"], first["result"])
        self.assertEqual(disk_record["config"], first["config"])

    def test_train_outcomes_never_touch_test_and_holdouts_disjoint(self):
        r = self.run_test(self.dataset())["result"]
        windows = r["holdout_results"]
        self.assertEqual([w["test_start_index"] for w in windows],
                         [264, 296, 328])
        self.assertEqual([w["test_end_exclusive"] for w in windows],
                         [296, 328, 360])
        self.assertEqual([w["train_end_exclusive"] for w in windows],
                         [264, 296, 328])
        for k, w in enumerate(windows):
            self.assertEqual(w["last_training_outcome_index"],
                             w["test_start_index"] - 1)
            self.assertEqual(w["holdout_step"], 2)
            self.assertEqual(w["holdout_decisions"], 15)
            self.assertEqual(w["q_policy"]["observations"], 15)
            self.assertEqual(w["q_policy"]["fills"], "MODELED_NEXT_OPEN_ONLY")
            self.assertEqual(w["q_policy"]["hypothetical_roundtrips"],
                             w["q_policy_doubled_cost"]["hypothetical_roundtrips"])
            self.assertEqual(w["no_trade_baseline_net_price_units"], 0.0)
            if k:
                self.assertEqual(windows[k - 1]["test_end_exclusive"],
                                 w["test_start_index"])

    def test_last_holdout_future_mutation_cannot_change_trained_models(self):
        a = self.run_test(self.dataset())["result"]
        for i in range(328, len(self.rows)):
            offset = 0.0005 * (i - 328)
            original = float(self.rows[i]["open"])
            self.rows[i]["open"] = f"{original + offset:.8f}"
            self.rows[i]["close"] = f"{original + offset + 0.0002:.8f}"
            self.rows[i]["high"] = f"{original + 0.04:.8f}"
            self.rows[i]["low"] = f"{original - 0.04:.8f}"
        b = self.run_test(self.dataset())["result"]
        a_windows, b_windows = a["holdout_results"], b["holdout_results"]
        self.assertEqual([w["model_sha256"] for w in a_windows],
                         [w["model_sha256"] for w in b_windows])
        self.assertEqual(a_windows[0], b_windows[0])
        self.assertEqual(a_windows[1], b_windows[1])
        self.assertNotEqual(a_windows[2]["momentum_baseline"],
                            b_windows[2]["momentum_baseline"])
        self.assertNotEqual(a["dataset_id"], b["dataset_id"])

    def test_doubled_cost_never_boosts_frozen_model_score(self):
        r = self.run_test(self.dataset(), spread=0.0001)["result"]
        for w in r["holdout_results"]:
            base = w["q_policy"]
            twice = w["q_policy_doubled_cost"]
            self.assertEqual(base["hypothetical_roundtrips"],
                             twice["hypothetical_roundtrips"])
            self.assertLessEqual(twice["net_price_units_per_one_unit"],
                                 base["net_price_units_per_one_unit"] + 1e-10)
            self.assertEqual(base["observations"], twice["observations"])
        self.assertEqual(r["folds_positive_after_cost"],
                         sum(w["outperforms_no_trade"]
                             for w in r["holdout_results"]))

    def test_expanding_training_can_use_prior_fold_but_not_future_fold(self):
        a = self.run_test(self.dataset())["result"]["holdout_results"]
        # Change *second* test block: model 1 and 2 must stay fixed,
        # but the third fold is allowed to learn from that now-historical data.
        for i in range(296, 328):
            original = float(self.rows[i]["open"])
            self.rows[i]["open"] = f"{original + 0.0002 * (i - 296):.8f}"
            self.rows[i]["close"] = f"{original + 0.0002 * (i - 296) + 0.0002:.8f}"
            self.rows[i]["high"] = f"{original + 0.04:.8f}"
            self.rows[i]["low"] = f"{original - 0.04:.8f}"
        b = self.run_test(self.dataset())["result"]["holdout_results"]
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[1]["model_sha256"], b[1]["model_sha256"])
        self.assertNotEqual(a[2]["model_sha256"], b[2]["model_sha256"])

    def test_invalid_folds_windows_model_budget_costs_and_basis_fail_closed(self):
        dataset_id = self.dataset()
        examples = (
            {"folds": 1}, {"folds": True}, {"folds": 7},
            {"test_bars": 0}, {"test_bars": True}, {"test_bars": 121},
            {"folds": 6, "test_bars": 100}, {"epochs": 0},
            {"alpha": float("nan")}, {"gamma": 1.0},
            {"spread": -0.1}, {"spread": float("nan")},
            {"slippage": float("inf")}, {"fee_per_side": True},
            {"spread": 100},
        )
        for kw in examples:
            with self.subTest(kw=kw):
                with self.assertRaises(MarketDataError):
                    self.run_test(dataset_id, **kw)
        # MarketStore correctly rejects the same bars under conflicting
        # quote-basis provenance. Keep the basis negative test in its own DB.
        bid_store = MarketStore(self.root / "bid-only.sqlite3")
        with self.csv.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=[
                "time_utc", "open", "high", "low", "close"
            ])
            writer.writeheader()
            writer.writerows(self.rows)
        non_mid = bid_store.import_csv(
            self.csv, symbol="EURUSD", timeframe="M15",
            quote_basis="BID", source="fully-synthetic-evidence",
            rights="TEST_ONLY",
        )["dataset_id"]
        with self.assertRaisesRegex(MarketDataError, "MID_OHLC_REQUIRED"):
            walk_forward_q(
                bid_store, non_mid, spread=0.0002,
                slippage=0.00003, fee_per_side=0.00001,
            )
        gapped = self.dataset(gap=True)
        with self.assertRaisesRegex(MarketDataError, "CONTIGUOUS_CANDLES_REQUIRED"):
            self.run_test(gapped)

    def test_sqlite_tampering_before_analysis_is_rejected(self):
        dataset_id = self.dataset()
        self.run_test(dataset_id)
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute("UPDATE candles SET close=close+0.0001 "
                       "WHERE dataset_id=? AND idx=?", (dataset_id, 80))
        with self.assertRaisesRegex(MarketDataError, "DATASET_INTEGRITY_FAILED"):
            self.run_test(dataset_id)

    def test_cli_reads_private_dataset_and_cannot_execute_order(self):
        dataset_id = self.dataset()
        args = [
            sys.executable, "-m", "max_trading_agent", "rl-walk-forward",
            "--market-db", str(self.store.path), "--dataset", dataset_id,
            "--spread", "0.0002", "--slippage", "0.00003",
            "--fee-per-side", "0.00001", "--folds", "3",
            "--test-bars", "32", "--epochs", "4",
        ]
        result = subprocess.run(args, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, msg=result.stderr[:300])
        import json
        payload = json.loads(result.stdout)
        self.assertEqual(payload["result"]["decision"], "NO_TRADE")
        self.assertEqual(payload["result"]["folds_completed"], 3)
        self.assertNotIn("order_send", result.stdout)

    def test_too_short_dataset_and_wrong_id_never_produce_favorable_report(self):
        dataset_id = self.dataset()
        for bad in (None, 1234, "deadbeef", "z" * 64):
            with self.subTest(bad=bad):
                with self.assertRaises(MarketDataError):
                    self.run_test(bad)
        with self.assertRaisesRegex(MarketDataError, "INSUFFICIENT"):
            self.run_test(dataset_id, folds=6, test_bars=50)


if __name__ == "__main__":
    unittest.main()
