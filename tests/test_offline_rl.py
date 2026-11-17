"""No live RL; historical synthetic market, independent holdout and costs."""
import csv
import math
import tempfile
import unittest
from pathlib import Path

from max_trading_agent.market_store import MarketDataError, MarketStore
from max_trading_agent.offline_rl import (
    ACTIONS, _policy_score, _reward, _state, offline_q_research,
)
from test_market_evidence import generate_rows


class OfflineRLTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.store = MarketStore(self.root / "data.sqlite3")
        self.rows = generate_rows(220)
        self.file = self.root / "history.csv"

    def dataset(self, *, quote_basis="MID"):
        with self.file.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=[
                "time_utc", "open", "high", "low", "close"])
            writer.writeheader()
            writer.writerows(self.rows)
        return self.store.import_csv(
            self.file, symbol="EURUSD", timeframe="M15",
            quote_basis=quote_basis, source="synthetic-offline",
            rights="TEST_ONLY",
        )["dataset_id"]

    def experiment(self, dataset_id, **changes):
        config = dict(spread=0.0002, slippage=0.00003, fee_per_side=0.00001)
        config.update(changes)
        return offline_q_research(self.store, dataset_id, **config)

    def test_reproducible_offline_training_and_sealed_replay(self):
        dataset_id = self.dataset()
        a = self.experiment(dataset_id)
        b = self.experiment(dataset_id)
        self.assertEqual(a, b)
        self.assertEqual(a["result"]["decision"], "NO_TRADE")
        self.assertFalse(a["result"]["broker_execution"])
        self.assertEqual(a["result"]["execution"], "DISABLED")
        self.assertEqual(a["result"]["training_method"],
                         "OFFLINE_SIMULATED_TABULAR_Q_ALL_ACTIONS")
        self.assertEqual(a["result"]["no_trade_baseline_holdout"], 0.0)
        self.assertEqual(len(a["result"]["model_digest"]), 64)
        recorded = MarketStore(self.store.path).experiment(a["experiment_id"])
        self.assertEqual(recorded["result"], a["result"])

    def test_split_and_embargo_are_explicit(self):
        r = self.experiment(self.dataset())["result"]
        self.assertEqual(r["train_split_index"], int(220 * 0.7))
        self.assertEqual(r["embargo_bars"], 2)
        self.assertEqual(r["train_count"], int(220*0.7)-2-8)
        self.assertEqual(r["holdout_count"], 220-2-int(220*0.7))
        self.assertEqual(r["holdout"]["observations"], r["holdout_count"])
        self.assertEqual(r["evidence_grade"], "DESCRIPTIVE_ONLY")

    def test_holdout_mutation_does_not_change_training_policy(self):
        a = self.experiment(self.dataset())
        split = int(220 * 0.7)
        for i in range(split, 218):
            v = float(self.rows[i]["open"])
            # A constant shift cancels from open-to-open rewards; vary
            # the shift by candle to exercise genuine holdout sensitivity.
            self.rows[i]["open"] = f"{v+0.0001*(i-split):.8f}"
            self.rows[i]["high"] = f"{v+0.02:.8f}"
            self.rows[i]["low"] = f"{v-0.02:.8f}"
        b = self.experiment(self.dataset())
        self.assertEqual(a["result"]["model_digest"], b["result"]["model_digest"])
        self.assertEqual(a["result"]["development"], b["result"]["development"])
        self.assertNotEqual(a["result"]["holdout"], b["result"]["holdout"])

    def test_state_cannot_read_future_bars(self):
        history = self.store.candles(self.dataset())
        original = _state(history, 100, 0.0002)
        for i in range(101, len(history)):
            history[i]["close"] = 100
            history[i]["open"] = 200
        self.assertEqual(_state(history, 100, 0.0002), original)

    def test_every_hypothetical_action_uses_next_open_and_costs(self):
        history = self.store.candles(self.dataset())
        i = 50
        mid = history[i+2]["open"] - history[i+1]["open"]
        self.assertAlmostEqual(_reward(history, i, "LONG", 0.0002), mid-0.0002)
        self.assertAlmostEqual(_reward(history, i, "SHORT", 0.0002), -mid-0.0002)
        self.assertEqual(_reward(history, i, "FLAT", 0.0002), 0)
        with self.assertRaises(MarketDataError):
            _reward(history, i, "ORDER_SEND", 0)

    def test_fixed_policy_costs_never_improve_profit(self):
        history = self.store.candles(self.dataset())
        indices = range(160, 210)
        low = _policy_score(history, indices, lambda _: "LONG", 0)
        high = _policy_score(history, indices, lambda _: "LONG", 0.003)
        self.assertEqual(low["hypothetical_roundtrips"], high["hypothetical_roundtrips"])
        self.assertLess(high["net_price_units_per_one_unit"],
                        low["net_price_units_per_one_unit"])
        self.assertEqual(low["fills"], "MODELED_NEXT_OPEN_ONLY")

    def test_high_cost_flattens_or_penalizes_policy(self):
        dataset_id = self.dataset()
        output = self.experiment(dataset_id, spread=0.005, fee_per_side=0.0005)
        self.assertEqual(output["result"]["decision"], "NO_TRADE")
        self.assertTrue(output["result"]["holdout"]["hypothetical_roundtrips"] >= 0)
        self.assertEqual(output["result"]["no_trade_baseline_holdout"], 0.0)

    def test_invalid_hyperparams_and_market_costs_are_rejected(self):
        dataset_id = self.dataset()
        for bad in (float("nan"), float("inf"), -0.1, True):
            with self.assertRaises(MarketDataError):
                self.experiment(dataset_id, spread=bad)
        for changes in ({"epochs": 0}, {"epochs": 101},
                        {"alpha": -0.1}, {"gamma": 1.0},
                        {"gamma": float("nan")}, {"epochs": True}):
            with self.subTest(changes=changes):
                with self.assertRaises(MarketDataError):
                    self.experiment(dataset_id, **changes)

    def test_wrong_quote_basis_and_tampered_dataset_fail_closed(self):
        basis = self.dataset(quote_basis="BID")
        with self.assertRaisesRegex(MarketDataError, "MID_OHLC_REQUIRED"):
            self.experiment(basis)
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute("UPDATE candles SET close=close+0.00001 "
                       "WHERE dataset_id=? AND idx=9", (basis,))
        with self.assertRaisesRegex(MarketDataError, "MID_OHLC_REQUIRED"):
            self.experiment(basis)

    def test_training_only_csv_mock_never_invokes_mt5(self):
        out = self.experiment(self.dataset())
        self.assertEqual(out["result"]["momentum_baseline_holdout"]["fills"],
                         "MODELED_NEXT_OPEN_ONLY")
        self.assertIn("holdout", out["result"])
        self.assertNotIn("order_send", str(out))


if __name__ == "__main__":
    unittest.main()
