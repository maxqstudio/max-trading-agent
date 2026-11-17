"""Offline-only tabular Q research on immutable MID OHLC, never live learning.

The reward is a synthetic next-two-open round trip after modeled costs.
This is counterfactual Q-learning from *market history*, not logged agent
behavior or evidence of realizable broker orders.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from statistics import median

from .market_store import MarketDataError, MarketStore

ACTIONS = ("FLAT", "LONG", "SHORT")
STRATEGY = "OFFLINE_TABULAR_Q_V1"
TRAIN_FRACTION = 0.70


def _cost(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise MarketDataError("INVALID_" + label)
    return float(value)


def _params(epochs, alpha, gamma):
    if type(epochs) is not int or not 1 <= epochs <= 100:
        raise MarketDataError("INVALID_RL_EPOCHS")
    if (type(alpha) not in (int, float) or not 0 < alpha <= 1
            or type(gamma) not in (int, float) or not 0 <= gamma < 1
            or not math.isfinite(alpha) or not math.isfinite(gamma)):
        raise MarketDataError("INVALID_RL_HYPERPARAMETERS")


def _state(candles: list[dict], i: int, threshold: float) -> str:
    """All prices used are from completed candles with index <= i."""
    if not 8 <= i < len(candles):
        raise MarketDataError("STATE_NOT_AVAILABLE")
    closes = [float(candles[j]["close"]) for j in range(i-8, i+1)]
    short_mean = sum(closes[-3:]) / 3
    long_mean = sum(closes[:-3]) / 6
    direction = "UP" if short_mean - long_mean > threshold else (
        "DOWN" if long_mean - short_mean > threshold else "FLAT"
    )
    recent_vol = sum(abs(closes[j] - closes[j-1])
                     for j in range(1, len(closes))) / 8
    return direction + ("_VOL_HIGH" if recent_vol > threshold else "_VOL_LOW")


def _reward(candles, i, action, roundtrip_cost):
    """State formed at close(i); entry/exit at opens(i+1) and (i+2)."""
    entry = float(candles[i + 1]["open"])
    exit_ = float(candles[i + 2]["open"])
    if action == "FLAT":
        return 0.0
    if action == "LONG":
        return exit_ - entry - roundtrip_cost
    if action == "SHORT":
        return entry - exit_ - roundtrip_cost
    raise MarketDataError("INVALID_RL_ACTION")


def _policy_score(candles, indices, choose, cost) -> dict:
    net = peak = drawdown = 0.0
    executed = wins = losses = 0
    for i in indices:
        action = choose(i)
        if action not in ACTIONS:
            raise MarketDataError("INVALID_RL_ACTION")
        gain = _reward(candles, i, action, cost)
        if action != "FLAT":
            executed += 1
            wins += gain > 0
            losses += gain < 0
        net += gain
        peak = max(peak, net)
        drawdown = max(drawdown, peak - net)
    return {
        "observations": len(indices), "hypothetical_roundtrips": executed,
        "wins": wins, "losses": losses,
        "net_price_units_per_one_unit": round(net, 12),
        "drawdown_price_units_per_one_unit": round(drawdown, 12),
        "fills": "MODELED_NEXT_OPEN_ONLY",
    }


def offline_q_research(store: MarketStore, dataset_id: str, *,
                       spread: float, slippage: float, fee_per_side: float,
                       epochs: int = 16, alpha: float = 0.2,
                       gamma: float = 0.6) -> dict:
    """Train exclusively on development data; freeze Q for OOS holdout."""
    costs = (_cost(spread, "SPREAD"), _cost(slippage, "SLIPPAGE"),
             _cost(fee_per_side, "FEE"))
    _params(epochs, alpha, gamma)
    if len(dataset_id) != 64 or any(x not in "0123456789abcdef" for x in dataset_id):
        raise MarketDataError("INVALID_DATASET_ID")
    manifest = store.manifest(dataset_id)
    if manifest["quote_basis"] != "MID":
        raise MarketDataError("MID_OHLC_REQUIRED")
    candles = store.candles(dataset_id)
    n = len(candles)
    if n < 180:
        raise MarketDataError("INSUFFICIENT_RL_HISTORY")
    split = int(n * TRAIN_FRACTION)
    if split < 64 or n - split < 32:
        raise MarketDataError("INSUFFICIENT_HOLDOUT")
    cost = costs[0] + 2 * costs[1] + 2 * costs[2]
    if cost > min(float(x["open"]) for x in candles):
        raise MarketDataError("COST_EXCEEDS_PRICE")
    # Only development bars define the volatility threshold. Holdout NEVER
    # affects this transform or any Q update.
    training_changes = [
        abs(candles[i]["close"] - candles[i-1]["close"])
        for i in range(1, split)
    ]
    threshold = float(median(training_changes))
    if not math.isfinite(threshold):
        raise MarketDataError("INVALID_HISTORY")
    training = list(range(8, split-2))
    holdout = list(range(split, n-2))  # 2-bar embargo, no crossed reward.
    if not training or not holdout:
        raise MarketDataError("INSUFFICIENT_RL_WINDOWS")
    q: dict[str, dict[str, float]] = defaultdict(
        lambda: {a: 0.0 for a in ACTIONS}
    )
    for _ in range(epochs):
        for i in training:
            state = _state(candles, i, threshold)
            following = _state(candles, i+1, threshold)
            for action in ACTIONS:
                gain = _reward(candles, i, action, cost)
                target = gain + gamma * max(q[following].values())
                q[state][action] = (1-alpha)*q[state][action] + alpha*target
    frozen = {key: {a: round(v, 14) for a, v in sorted(q[key].items())}
              for key in sorted(q)}
    model_digest = hashlib.sha256(json.dumps(
        frozen, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()

    def choose(i):
        state = _state(candles, i, threshold)
        values = frozen.get(state, {a: 0.0 for a in ACTIONS})
        return max(ACTIONS, key=lambda a: values[a])

    def momentum(i):
        # Two already completed closes; entry occurs at i+1 open.
        diff = candles[i]["close"] - candles[i-2]["close"]
        return "LONG" if diff > cost else "SHORT" if diff < -cost else "FLAT"

    result = {
        "strategy": STRATEGY, "decision": "NO_TRADE",
        "execution": "DISABLED", "broker_execution": False,
        "dataset_id": dataset_id, "train_split_index": split,
        "train_count": len(training), "holdout_count": len(holdout),
        "embargo_bars": 2, "model_digest": model_digest,
        "development": _policy_score(candles, training, choose, cost),
        "holdout": _policy_score(candles, holdout, choose, cost),
        "momentum_baseline_holdout": _policy_score(candles, holdout, momentum, cost),
        "no_trade_baseline_holdout": 0.0,
        "training_method": "OFFLINE_SIMULATED_TABULAR_Q_ALL_ACTIONS",
        "evidence_grade": "DESCRIPTIVE_ONLY",
        "limitations": "Counterfactual simulated MID fills; no broker spreads, contract conversion, swap, account exposure, regime proof or statistical edge.",
    }
    config = {
        "algorithm": STRATEGY, "epochs": epochs,
        "alpha": alpha, "gamma": gamma, "train_fraction": TRAIN_FRACTION,
        "split_index": split, "cost_spread": costs[0],
        "cost_slippage_per_side": costs[1],
        "cost_fee_per_side": costs[2],
        "data_vintage": manifest["end_utc"],
        "action_space": list(ACTIONS),
        "state_features": ["completed_close_direction", "completed_close_volatility"],
    }
    record = store.record_experiment(dataset_id, STRATEGY, config, result)
    return {"experiment_id": record["experiment_id"], "config": config,
            "result": result}
