"""Expanding-window, leakage-bounded offline Q research; NO_TRADE always.

Each chronological fold trains only on earlier verified MID bars. Synthetic
holdout roundtrips do not overlap, and stress pricing reuses the frozen model.
No MetaTrader5, network, provider, or real order dependency exists here.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from statistics import median

from .market_store import MarketDataError, MarketStore
from .offline_rl import ACTIONS, _cost, _params, _policy_score, _state, _reward

STRATEGY = "OFFLINE_Q_WALK_FORWARD_V1"
MAX_BARS = 5000
MIN_TRAIN = 128


def _fit(candles: list[dict], boundary: int, cost: float, *,
         epochs: int, alpha: float, gamma: float) -> tuple[dict, float, str]:
    """Learn only from bars whose *outcomes* end before the holdout boundary."""
    # The final training action is boundary-3; its exit is boundary-1.
    changes = [abs(candles[i]["close"] - candles[i - 1]["close"])
               for i in range(1, boundary)]
    threshold = float(median(changes))
    table = defaultdict(lambda: {name: 0.0 for name in ACTIONS})
    for _ in range(epochs):
        for i in range(8, boundary - 2):
            state = _state(candles, i, threshold)
            following = _state(candles, i + 1, threshold)
            for action in ACTIONS:
                gain = _reward(candles, i, action, cost)
                target = gain + gamma * max(table[following].values())
                table[state][action] = (
                    (1 - alpha) * table[state][action] + alpha * target
                )
    frozen = {name: {action: round(values[action], 14)
                     for action in ACTIONS}
              for name, values in sorted(table.items())}
    model_hash = hashlib.sha256(json.dumps({
        "q": frozen, "threshold": threshold,
        "train_end_exclusive": boundary,
    }, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    return frozen, threshold, model_hash


def walk_forward_q(store: MarketStore, dataset_id: str, *,
                   spread: float, slippage: float, fee_per_side: float,
                   folds: int = 3, test_bars: int = 32,
                   epochs: int = 16, alpha: float = 0.2,
                   gamma: float = 0.6) -> dict:
    """Research-only walk-forward run; no automatic strategy promotion.

    Test blocks are chronologically disjoint. Once a block has been
    evaluated, its historic bars may enter the NEXT fold's expanding
    training set, as in walk-forward retraining. No fold sees its own future.
    """
    if not isinstance(dataset_id, str) or len(dataset_id) != 64 or any(
        ch not in "0123456789abcdef" for ch in dataset_id
    ):
        raise MarketDataError("INVALID_DATASET_ID")
    if type(folds) is not int or not 2 <= folds <= 6:
        raise MarketDataError("INVALID_WALK_FORWARD_FOLDS")
    if type(test_bars) is not int or not 24 <= test_bars <= 120:
        raise MarketDataError("INVALID_WALK_FORWARD_WINDOW")
    _params(epochs, alpha, gamma)
    parts = (_cost(spread, "SPREAD"), _cost(slippage, "SLIPPAGE"),
             _cost(fee_per_side, "FEE"))
    roundtrip = parts[0] + 2 * parts[1] + 2 * parts[2]
    manifest = store.manifest(dataset_id)
    if manifest["quote_basis"] != "MID":
        raise MarketDataError("MID_OHLC_REQUIRED")
    if manifest["gap_count"] or manifest["missing_intervals"]:
        raise MarketDataError("CONTIGUOUS_CANDLES_REQUIRED")
    candles = store.candles(dataset_id)  # checks immutable content SHA-256
    n = len(candles)
    if n > MAX_BARS or n - folds * test_bars < MIN_TRAIN:
        raise MarketDataError("INSUFFICIENT_OR_EXCESSIVE_WALK_FORWARD_DATA")
    if roundtrip > min(bar["open"] for bar in candles):
        raise MarketDataError("COST_EXCEEDS_PRICE")

    fold_results = []
    first_boundary = n - folds * test_bars
    for k in range(folds):
        boundary = first_boundary + k * test_bars
        end = boundary + test_bars
        frozen, threshold, model_hash = _fit(
            candles, boundary, roundtrip, epochs=epochs, alpha=alpha, gamma=gamma
        )
        # Non-overlapping positions: action at completed close i, hypothetical
        # entry at i+1 open and exit at i+2 open; next decision is i+2.
        indices = range(boundary, end - 2, 2)

        def choose(i):
            state = _state(candles, i, threshold)
            values = frozen.get(state, {action: 0.0 for action in ACTIONS})
            return max(ACTIONS, key=lambda action: values[action])

        def momentum(i):
            difference = candles[i]["close"] - candles[i - 2]["close"]
            if difference > roundtrip:
                return "LONG"
            if difference < -roundtrip:
                return "SHORT"
            return "FLAT"

        base = _policy_score(candles, indices, choose, roundtrip)
        stressed = _policy_score(candles, indices, choose, 2 * roundtrip)
        baseline = _policy_score(candles, indices, momentum, roundtrip)
        if (base["hypothetical_roundtrips"] != stressed["hypothetical_roundtrips"]
                or stressed["net_price_units_per_one_unit"]
                > base["net_price_units_per_one_unit"] + 1e-10):
            raise MarketDataError("STRESS_SCORE_INTEGRITY_FAILED")
        fold_results.append({
            "fold": k + 1,
            "train_end_exclusive": boundary,
            "last_training_outcome_index": boundary - 1,
            "test_start_index": boundary,
            "test_end_exclusive": end,
            "test_start_utc": candles[boundary]["time_utc"],
            "test_end_utc": candles[end - 1]["time_utc"],
            "holdout_decisions": len(indices),
            "holdout_step": 2,
            "model_sha256": model_hash,
            "trained_q": False,
            "q_policy": base,
            "q_policy_doubled_cost": stressed,
            "momentum_baseline": baseline,
            "no_trade_baseline_net_price_units": 0.0,
            "outperforms_no_trade": (
                base["net_price_units_per_one_unit"] > 0
            ),
            "outperforms_momentum": (
                base["net_price_units_per_one_unit"]
                > baseline["net_price_units_per_one_unit"]
            ),
        })

    result = {
        "strategy": STRATEGY,
        "decision": "NO_TRADE", "execution": "DISABLED",
        "broker_execution": False, "owner_acceptance": "PENDING",
        "dataset_id": dataset_id, "symbol": manifest["symbol"],
        "timeframe": manifest["timeframe"],
        "folds_requested": folds, "folds_completed": len(fold_results),
        "tests_are_chronological": True, "holdout_results": fold_results,
        "folds_positive_after_cost": sum(
            f["outperforms_no_trade"] for f in fold_results
        ),
        "folds_beating_momentum": sum(
            f["outperforms_momentum"] for f in fold_results
        ),
        "all_folds_positive_after_cost": all(
            f["outperforms_no_trade"] for f in fold_results
        ),
        "all_folds_survive_doubled_cost": all(
            f["q_policy_doubled_cost"]["net_price_units_per_one_unit"] > 0
            for f in fold_results
        ),
        "evidence_grade": "DESCRIPTIVE_SYNTHETIC_ONLY",
        "eligible_for_trading": False,
        "limitations": (
            "Single provenance-controlled MID dataset, no independent market "
            "regimes, actual bid/ask fills, overnight gaps, margin, swaps, "
            "account-currency returns or confidence intervals. Repeated "
            "folds and positive simulated P/L do not prove strategy alpha."
        ),
    }
    config = {
        "algorithm": STRATEGY, "folds": folds, "test_bars": test_bars,
        "min_train_bars": MIN_TRAIN, "epochs": epochs,
        "alpha": alpha, "gamma": gamma,
        "roundtrip_spread": parts[0],
        "slippage_per_side": parts[1],
        "fee_per_side": parts[2],
        "doubled_cost_stress": True,
        "nonoverlapping_holdout_step": 2,
        "data_vintage": manifest["end_utc"],
        "training_rule": "EXPANDING_WINDOW_WITH_TWO_BAR_OUTCOME_EMBARGO",
        "holdout_rule": "MODELED_NEXT_OPEN_NONOVERLAPPING",
    }
    record = store.record_experiment(dataset_id, STRATEGY, config, result)
    return {"experiment_id": record["experiment_id"], "config": config,
            "result": result}
