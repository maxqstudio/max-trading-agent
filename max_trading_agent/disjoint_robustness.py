"""Research-only, predeclared disjoint-history replication of walk-forward Q.

Do not treat chronological separation or a positive simulated score as proof of
independent regimes, broker fills, alpha, or an authorization to trade.
"""
from __future__ import annotations

import hashlib
import json

from .market_store import MarketDataError, MarketStore
from .offline_rl import _cost, _params
from .walk_forward import MAX_BARS, MIN_TRAIN, walk_forward_q

STRATEGY = "OFFLINE_Q_DISJOINT_DATASETS_V1"
MAX_DATASETS = 5


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def disjoint_dataset_robustness(
    store: MarketStore, dataset_ids: list[str] | tuple[str, ...], *,
    spread: float, slippage: float, fee_per_side: float,
    folds: int = 3, test_bars: int = 32,
    epochs: int = 16, alpha: float = 0.2, gamma: float = 0.6,
) -> dict:
    """Check all inputs *before* writing any constituent experiment records.

    Fixed costs and hyperparameters are reused in each dataset. No policy is
    transferred between datasets. Reports never combine raw price-unit P/L.
    """
    if type(dataset_ids) not in (list, tuple) or not 2 <= len(dataset_ids) <= MAX_DATASETS:
        raise MarketDataError("INVALID_DATASET_BATCH_SIZE")
    for dataset_id in dataset_ids:
        if (not isinstance(dataset_id, str) or len(dataset_id) != 64
                or any(ch not in "0123456789abcdef" for ch in dataset_id)):
            raise MarketDataError("INVALID_DATASET_ID")
    if len(set(dataset_ids)) != len(dataset_ids):
        raise MarketDataError("DUPLICATE_DATASET_ID")
    if type(folds) is not int or not 2 <= folds <= 6:
        raise MarketDataError("INVALID_WALK_FORWARD_FOLDS")
    if type(test_bars) is not int or not 24 <= test_bars <= 120:
        raise MarketDataError("INVALID_WALK_FORWARD_WINDOW")
    _params(epochs, alpha, gamma)
    costs = (_cost(spread, "SPREAD"), _cost(slippage, "SLIPPAGE"),
             _cost(fee_per_side, "FEE"))
    roundtrip = costs[0] + 2 * costs[1] + 2 * costs[2]

    checked = []
    for dataset_id in dataset_ids:
        meta = store.manifest(dataset_id)
        if meta["quote_basis"] != "MID":
            raise MarketDataError("MID_OHLC_REQUIRED")
        if meta["gap_count"] or meta["missing_intervals"]:
            raise MarketDataError("CONTIGUOUS_CANDLES_REQUIRED")
        candles = store.candles(dataset_id)  # immutable SHA and count validation
        if (len(candles) > MAX_BARS
                or len(candles) - folds * test_bars < MIN_TRAIN):
            raise MarketDataError("INSUFFICIENT_OR_EXCESSIVE_WALK_FORWARD_DATA")
        if roundtrip > min(bar["open"] for bar in candles):
            raise MarketDataError("COST_EXCEEDS_PRICE")
        checked.append(meta)

    original = checked[0]
    for index, current in enumerate(checked[1:], start=1):
        if (current["symbol"], current["timeframe"]) != (
                original["symbol"], original["timeframe"]):
            raise MarketDataError("MIXED_INSTRUMENT_OR_TIMEFRAME")
        previous = checked[index - 1]
        if current["start_utc"] <= previous["start_utc"]:
            raise MarketDataError("DATASETS_NOT_CHRONOLOGICAL")
        if current["start_utc"] <= previous["end_utc"]:
            raise MarketDataError("DATASET_TIME_WINDOWS_OVERLAP")

    results = []
    for meta in checked:
        run = walk_forward_q(
            store, meta["dataset_id"], spread=costs[0], slippage=costs[1],
            fee_per_side=costs[2], folds=folds, test_bars=test_bars,
            epochs=epochs, alpha=alpha, gamma=gamma,
        )
        report = run["result"]
        folds_out = report["holdout_results"]
        # Make losing folds and the fixed reference scores inspectable.
        results.append({
            "dataset_id": meta["dataset_id"],
            "manifest_provenance_sha256": _digest({
                key: meta[key] for key in (
                    "dataset_id", "symbol", "timeframe", "quote_basis",
                    "source", "rights", "raw_sha256", "start_utc", "end_utc"
                )
            }),
            "start_utc": meta["start_utc"], "end_utc": meta["end_utc"],
            "constituent_experiment_id": run["experiment_id"],
            "folds_completed": report["folds_completed"],
            "folds_positive_after_cost": report["folds_positive_after_cost"],
            "folds_beating_momentum": report["folds_beating_momentum"],
            "all_folds_positive_after_cost": report["all_folds_positive_after_cost"],
            "all_folds_survive_doubled_cost": report["all_folds_survive_doubled_cost"],
            "fold_scores_price_units_per_one_unit": [{
                "fold": fold["fold"],
                "q_net": fold["q_policy"]["net_price_units_per_one_unit"],
                "momentum_net": fold["momentum_baseline"]["net_price_units_per_one_unit"],
                "q_doubled_cost_net":
                    fold["q_policy_doubled_cost"]["net_price_units_per_one_unit"],
                "no_trade_net": fold["no_trade_baseline_net_price_units"],
            } for fold in folds_out],
        })

    result = {
        "strategy": STRATEGY,
        "decision": "NO_TRADE", "execution": "DISABLED",
        "broker_execution": False, "eligible_for_trading": False,
        "owner_acceptance": "PENDING",
        "dataset_independence_verified": False,
        "historical_windows_disjoint": True,
        "profit_values_pooled": False,
        "symbol": original["symbol"], "timeframe": original["timeframe"],
        "dataset_count": len(results),
        "datasets_all_folds_positive": sum(
            x["all_folds_positive_after_cost"] for x in results),
        "datasets_surviving_doubled_cost": sum(
            x["all_folds_survive_doubled_cost"] for x in results),
        "datasets": results,
        "fixed_config": {
            "folds": folds, "test_bars": test_bars, "epochs": epochs,
            "alpha": alpha, "gamma": gamma, "spread": costs[0],
            "slippage": costs[1], "fee_per_side": costs[2],
        },
        "evidence_grade": "DESCRIPTIVE_DISJOINT_HISTORIES_ONLY",
        "limitations": (
            "User-provided source/licensing and true statistical independence "
            "are unverified. Disjoint history is not independent broker/market "
            "regime evidence. Synthetic MID next-open fills and prices per one "
            "unit are not actual account returns; no confidence intervals or "
            "multiple-trial correction. No trade/promotion authority."
        ),
    }
    result["report_sha256"] = _digest(result)
    return result
