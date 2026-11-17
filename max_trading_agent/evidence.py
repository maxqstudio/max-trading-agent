"""Deterministic long/flat SMA evidence lab. No order execution.

All fills are simulated from user-supplied MID OHLC. Next candle *open*
uses ONLY prior completed closes. Last position is marked out at the last
window close (NOT a broker fill). P&L is price units per unit of asset,
not broker account-currency P&L or a live strategy recommendation.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .market_store import MarketDataError, MarketStore

STRATEGY_ID = "SMA_3_8_LONG_FLAT_V1"
STRATEGY_VERSION = "m03-research-v1"
TRAIN_FRACTION = 0.70
MIN_HISTORY = 128
MIN_EVIDENCE_TRADES = 20


def desired_long(candles: list[dict], index: int) -> bool:
    """At bar index open, use closes strictly before index; no lookahead."""
    if index < 8:
        raise MarketDataError("INSUFFICIENT_INDICATOR_WARMUP")
    recent = [float(candles[j]["close"]) for j in range(index - 8, index)]
    return sum(recent[-3:]) / 3 > sum(recent) / 8


def _cost(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarketDataError("INVALID_" + name)
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise MarketDataError("INVALID_" + name)
    return number


def evaluate_window(candles: list[dict], start: int, end: int, *,
                    spread: float, slippage: float, fee_per_side: float) -> dict:
    """Evaluate one split independently; never carry positions across split."""
    if not 0 <= start < end <= len(candles) or end - start < 30:
        raise MarketDataError("INVALID_EVALUATION_WINDOW")
    spread = _cost(spread, "SPREAD")
    slippage = _cost(slippage, "SLIPPAGE")
    fee_per_side = _cost(fee_per_side, "FEE")
    if spread + 2 * slippage > min(bar["open"] for bar in candles[start:end]):
        raise MarketDataError("COST_EXCEEDS_PRICE")

    trades = []
    closed_net = 0.0
    entry_mid = None
    entry_fill = None
    entry_idx = None
    equity_peak = 0.0
    max_drawdown = 0.0
    equity_path = []

    def close_position(idx: int, mid: float, reason: str) -> None:
        nonlocal entry_mid, entry_fill, entry_idx, closed_net
        exit_fill = mid - spread / 2 - slippage
        gross = mid - entry_mid
        net = exit_fill - entry_fill - 2 * fee_per_side
        trades.append({
            "entry_idx": entry_idx, "exit_idx": idx,
            "entry_time_utc": candles[entry_idx]["time_utc"],
            "exit_time_utc": candles[idx]["time_utc"],
            "gross_price_units": round(gross, 12),
            "net_price_units": round(net, 12),
            "reason": reason,
        })
        closed_net += net
        entry_mid = None
        entry_fill = None
        entry_idx = None

    for index in range(max(8, start), end):
        candle = candles[index]
        long_signal = desired_long(candles, index)
        if long_signal and entry_fill is None:
            entry_mid = candle["open"]
            entry_fill = entry_mid + spread / 2 + slippage
            entry_idx = index
        elif not long_signal and entry_fill is not None:
            close_position(index, candle["open"], "NEXT_OPEN_EXIT")

        # Conservative mark for informational drawdown; not an executed trade.
        equity = closed_net
        if entry_fill is not None:
            equity += candle["close"] - spread / 2 - slippage - entry_fill - 2 * fee_per_side
        equity_peak = max(equity_peak, equity)
        max_drawdown = max(max_drawdown, equity_peak - equity)
        equity_path.append(round(equity, 12))

    if entry_fill is not None:
        # Explicit terminal mark, not an actual fill. Needed for a flat
        # comparison of independent train and holdout segments.
        close_position(end - 1, candles[end - 1]["close"], "END_MARK_NOT_FILL")
        equity_peak = max(equity_peak, closed_net)
        max_drawdown = max(max_drawdown, equity_peak - closed_net)

    net = sum(t["net_price_units"] for t in trades)
    gross = sum(t["gross_price_units"] for t in trades)
    wins = sum(t["net_price_units"] > 0 for t in trades)
    losses = sum(t["net_price_units"] < 0 for t in trades)
    positive_sum = sum(t["net_price_units"] for t in trades if t["net_price_units"] > 0)
    negative_sum = -sum(t["net_price_units"] for t in trades if t["net_price_units"] < 0)
    return {
        "bars": end - start,
        "from_utc": candles[start]["time_utc"],
        "through_utc": candles[end - 1]["time_utc"],
        "trades": len(trades),
        "wins": wins, "losses": losses,
        "net_pnl_price_units_per_unit": round(net, 12),
        "gross_pnl_price_units_per_unit": round(gross, 12),
        "max_drawdown_price_units_per_unit": round(max_drawdown, 12),
        "win_rate": round(wins / len(trades), 6) if trades else None,
        "profit_factor": round(positive_sum / negative_sum, 6) if negative_sum > 0 else None,
        "baseline_no_trade_pnl": 0.0,
        "evidence_grade": "DESCRIPTIVE_ONLY" if len(trades) >= MIN_EVIDENCE_TRADES
                          else "INSUFFICIENT_TRADES",
        "trade_records": trades,
        "equity_marks": equity_path,
        "all_fills": "SYNTHETIC_MODEL_ONLY",
    }


def run_evidence(store: MarketStore, dataset_id: str, *, spread: float,
                 slippage: float, fee_per_side: float) -> dict:
    """Register a fully reproducible predeclared hypothesis; no optimization."""
    # Explicit cost inputs are required. Never silently assume real execution
    # is free or that a broker's contract size and P&L currency are known.
    spread = _cost(spread, "SPREAD")
    slippage = _cost(slippage, "SLIPPAGE")
    fee_per_side = _cost(fee_per_side, "FEE")
    manifest = store.manifest(dataset_id)
    if manifest["quote_basis"] != "MID":
        raise MarketDataError("MID_OHLC_REQUIRED")
    candles = store.candles(dataset_id)
    if len(candles) < MIN_HISTORY:
        raise MarketDataError("INSUFFICIENT_HISTORICAL_BARS")
    split = int(len(candles) * TRAIN_FRACTION)
    if split < 64 or len(candles) - split < 32:
        raise MarketDataError("INSUFFICIENT_HOLDOUT_BARS")
    config = {
        "strategy": STRATEGY_ID, "version": STRATEGY_VERSION,
        "short": 3, "long": 8, "method": "LONG_FLAT_NEXT_OPEN",
        "train_fraction": TRAIN_FRACTION, "split_index": split,
        "spread_price_units": spread, "slippage_price_units": slippage,
        "fee_per_side_price_units": fee_per_side,
        "position_size": "ONE_UNIT", "quote_basis": "MID",
        "end_of_window": "MARK_NOT_FILL",
    }
    development = evaluate_window(
        candles, 0, split, spread=spread, slippage=slippage,
        fee_per_side=fee_per_side,
    )
    holdout = evaluate_window(
        candles, split, len(candles), spread=spread, slippage=slippage,
        fee_per_side=fee_per_side,
    )
    result = {
        "strategy": STRATEGY_ID,
        "dataset_id": dataset_id,
        "dataset_source": manifest["source"],
        "dataset_count": manifest["count"],
        "gap_count": manifest["gap_count"],
        "development": development,
        "holdout": holdout,
        "baseline": "NO_TRADE",
        "decision": "NO_TRADE",
        "trade_authorization": False,
        "interpretation": "Research only: no live spread, margin, partial fill, "
                          "swap, broker contract, or execution model validated.",
        "no_leakage_contract": "Signal at bar i open reads only closes with index < i.",
    }
    pointer = store.record_experiment(dataset_id, STRATEGY_ID, config, result)
    return {"experiment_id": pointer["experiment_id"], "config": config, "result": result}
