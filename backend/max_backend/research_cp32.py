from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

FEATURE_NAMES = (
    "ret1_atr","ret3_atr","ret6_atr","fast_ma_gap_atr","slow_ma_gap_atr",
    "fast_ma_slope_atr","adx_scaled","plus_di_scaled","minus_di_scaled",
    "rsi_centered","atr_percent","atr_ratio","bollinger_z","bollinger_width_pct",
    "efficiency10","candle_body_atr","upper_wick_atr","lower_wick_atr",
    "range_atr","tick_volume_z","hour_sin","hour_cos","weekday_sin","weekday_cos",
    "trend_family","range_family","breakout_family","pullback_family","session_family",
    "shock_family","relative_family","rule_meta_score",
)

FAMILIES = ("trend","range","breakout","pullback","session","shock","relative")
SESSION_ROLES = {"main", "timing"}


def clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def clamp01(value: float) -> float:
    return clamp(value, 0.0, 1.0)


def safe_div(a: float, b: float, fallback: float = 0.0) -> float:
    return fallback if abs(b) < 1e-12 else a / b


def sign(value: float) -> float:
    return 1.0 if value > 0.0 else -1.0 if value < 0.0 else 0.0


def _sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0:
        raise ValueError("period must be positive")
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def _ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    alpha = 2.0 / (period + 1.0)
    previous = seed
    for i in range(period, len(values)):
        previous = alpha * values[i] + (1.0 - alpha) * previous
        out[i] = previous
    return out


def _wilder(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    previous = sum(values[:period]) / period
    out[period - 1] = previous
    for i in range(period, len(values)):
        previous = ((period - 1.0) * previous + values[i]) / period
        out[i] = previous
    return out


def _atr(bars: list[dict[str, Any]], period: int) -> list[float | None]:
    tr: list[float] = []
    for i, bar in enumerate(bars):
        high, low = float(bar["high"]), float(bar["low"])
        if i == 0:
            tr.append(high - low)
        else:
            prev = float(bars[i - 1]["close"])
            tr.append(max(high - low, abs(high - prev), abs(low - prev)))
    return _wilder(tr, period)


def _rsi(closes: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def value(gain: float, loss: float) -> float:
        if loss <= 1e-20:
            return 100.0 if gain > 0 else 50.0
        rs = gain / loss
        return 100.0 - 100.0 / (1.0 + rs)

    out[period] = value(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        gain = gains[i - 1]
        loss = losses[i - 1]
        avg_gain = ((period - 1.0) * avg_gain + gain) / period
        avg_loss = ((period - 1.0) * avg_loss + loss) / period
        out[i] = value(avg_gain, avg_loss)
    return out


def _adx(
    bars: list[dict[str, Any]],
    period: int,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    n = len(bars)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    tr = [0.0] * n
    for i, bar in enumerate(bars):
        high, low = float(bar["high"]), float(bar["low"])
        if i == 0:
            tr[i] = high - low
            continue
        prev = bars[i - 1]
        up = high - float(prev["high"])
        down = float(prev["low"]) - low
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
        prev_close = float(prev["close"])
        tr[i] = max(high - low, abs(high - prev_close), abs(low - prev_close))

    atr = _wilder(tr, period)
    plus_smoothed = _wilder(plus_dm, period)
    minus_smoothed = _wilder(minus_dm, period)
    plus_di: list[float | None] = [None] * n
    minus_di: list[float | None] = [None] * n
    dx: list[float | None] = [None] * n
    for i in range(n):
        if atr[i] is None or plus_smoothed[i] is None or minus_smoothed[i] is None:
            continue
        base = float(atr[i])
        plus = 100.0 * safe_div(float(plus_smoothed[i]), base, 0.0)
        minus = 100.0 * safe_div(float(minus_smoothed[i]), base, 0.0)
        plus_di[i], minus_di[i] = plus, minus
        dx[i] = 100.0 * safe_div(abs(plus - minus), plus + minus, 0.0)

    adx: list[float | None] = [None] * n
    valid = [(i, float(v)) for i, v in enumerate(dx) if v is not None]
    if len(valid) >= period:
        seed_slice = valid[:period]
        seed_index = seed_slice[-1][0]
        current = sum(v for _, v in seed_slice) / period
        adx[seed_index] = current
        for i, value in valid[period:]:
            current = ((period - 1.0) * current + value) / period
            adx[i] = current
    return adx, plus_di, minus_di


def _bands(
    closes: list[float],
    period: int = 20,
    deviations: float = 2.0,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    mid = _sma(closes, period)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        mean = float(mid[i])
        variance = sum((value - mean) ** 2 for value in window) / period
        std = math.sqrt(max(0.0, variance))
        upper[i] = mean + deviations * std
        lower[i] = mean - deviations * std
    return mid, upper, lower


@dataclass(frozen=True)
class RoleSeries:
    bars: list[dict[str, Any]]
    atr14: list[float | None]
    atr50: list[float | None]
    ema20: list[float | None]
    ema50: list[float | None]
    adx14: list[float | None]
    plus_di14: list[float | None]
    minus_di14: list[float | None]
    rsi14: list[float | None]
    bb_mid20: list[float | None]
    bb_upper20: list[float | None]
    bb_lower20: list[float | None]


def prepare_role_series(bars: list[dict[str, Any]]) -> RoleSeries:
    if not bars:
        raise ValueError("empty role series")
    closes = [float(row["close"]) for row in bars]
    adx, plus_di, minus_di = _adx(bars, 14)
    mid, upper, lower = _bands(closes)
    return RoleSeries(
        bars=bars,
        atr14=_atr(bars, 14),
        atr50=_atr(bars, 50),
        ema20=_ema(closes, 20),
        ema50=_ema(closes, 50),
        adx14=adx,
        plus_di14=plus_di,
        minus_di14=minus_di,
        rsi14=_rsi(closes, 14),
        bb_mid20=mid,
        bb_upper20=upper,
        bb_lower20=lower,
    )


def latest_fully_closed_index(series: RoleSeries, decision_time: datetime) -> int:
    bars = series.bars
    eligible = -1
    for i in range(len(bars) - 1):
        if bars[i + 1]["open_time"] <= decision_time:
            eligible = i
        else:
            break
    if eligible < 0:
        raise ValueError("NO_FULLY_CLOSED_ROLE_BAR")
    return eligible


def _required(value: float | None, label: str) -> float:
    if value is None or not math.isfinite(float(value)):
        raise ValueError(f"FEATURE_INDICATOR_NOT_READY:{label}")
    return float(value)


def market_snapshot(series: RoleSeries, index: int) -> dict[str, Any]:
    bars = series.bars
    if index < 119:
        raise ValueError("FEATURE_WARMUP_NOT_READY")
    bar = bars[index]
    close = float(bar["close"])
    atr = _required(series.atr14[index], "ATR14")
    atr_slow = _required(series.atr50[index], "ATR50")
    ma_fast = _required(series.ema20[index], "EMA20")
    ma_fast_prev = _required(series.ema20[index - 1], "EMA20_PREV")
    ma_slow = _required(series.ema50[index], "EMA50")
    adx = _required(series.adx14[index], "ADX14")
    plus_di = _required(series.plus_di14[index], "PLUS_DI14")
    minus_di = _required(series.minus_di14[index], "MINUS_DI14")
    rsi = _required(series.rsi14[index], "RSI14")
    bb_mid = _required(series.bb_mid20[index], "BB_MID")
    bb_upper = _required(series.bb_upper20[index], "BB_UPPER")
    bb_lower = _required(series.bb_lower20[index], "BB_LOWER")
    if atr <= 0.0 or close <= 0.0:
        raise ValueError("FEATURE_INVALID_ATR_OR_CLOSE")

    ret1 = close / float(bars[index - 1]["close"]) - 1.0
    ret3 = close / float(bars[index - 3]["close"]) - 1.0
    ret6 = close / float(bars[index - 6]["close"]) - 1.0
    path = sum(
        abs(float(bars[j]["close"]) - float(bars[j - 1]["close"]))
        for j in range(index - 9, index + 1)
    )
    efficiency10 = safe_div(
        abs(close - float(bars[index - 10]["close"])),
        path,
        0.0,
    )
    previous20 = bars[index - 20 : index]
    highest20 = max(float(row["high"]) for row in previous20)
    lowest20 = min(float(row["low"]) for row in previous20)
    open1, high1, low1 = float(bar["open"]), float(bar["high"]), float(bar["low"])
    body = abs(close - open1)
    range_value = high1 - low1
    upper_wick = high1 - max(open1, close)
    lower_wick = min(open1, close) - low1
    half_width = (bb_upper - bb_lower) / 2.0

    volumes = [float(row["tick_volume"]) for row in bars[index - 30 : index]]
    mean_volume = sum(volumes) / 30.0
    variance = sum((value - mean_volume) ** 2 for value in volumes) / 29.0
    std_volume = math.sqrt(max(0.0, variance))

    return {
        "bar_time": bar.get("source_open_time", bar["open_time"]),
        "open_time_utc": bar["open_time"],
        "open1": open1, "high1": high1, "low1": low1, "close1": close,
        "atr": atr, "atr_slow": atr_slow,
        "ma_fast": ma_fast, "ma_fast_prev": ma_fast_prev, "ma_slow": ma_slow,
        "adx": adx, "plus_di": plus_di, "minus_di": minus_di, "rsi": rsi,
        "ret1": ret1, "ret3": ret3, "ret6": ret6,
        "efficiency10": efficiency10, "highest20": highest20, "lowest20": lowest20,
        "body_atr": safe_div(body, atr, 0.0),
        "upper_wick_atr": safe_div(max(0.0, upper_wick), atr, 0.0),
        "lower_wick_atr": safe_div(max(0.0, lower_wick), atr, 0.0),
        "range_atr": safe_div(max(0.0, range_value), atr, 0.0),
        "atr_ratio": safe_div(atr, atr_slow, 1.0),
        "bb_z": safe_div(close - bb_mid, half_width, 0.0),
        "bb_width_pct": safe_div(bb_upper - bb_lower, close, 0.0) * 100.0,
        "volume_z": safe_div(float(bar["tick_volume"]) - mean_volume, std_volume, 0.0),
    }


def make_signal(signal_value: float, quality: float) -> tuple[float, float]:
    return clamp(signal_value, -1.0, 1.0), clamp01(quality)


def signal_trend(s: dict[str, Any]) -> tuple[float, float]:
    direction = sign(s["ma_fast"] - s["ma_slow"])
    separation = abs(s["ma_fast"] - s["ma_slow"]) / s["atr"]
    adx_q = clamp01((s["adx"] - 18.0) / 22.0)
    eff_q = clamp01((s["efficiency10"] - 0.20) / 0.45)
    di_q = clamp01(abs(s["plus_di"] - s["minus_di"]) / 35.0)
    q = 0.45 * adx_q + 0.30 * eff_q + 0.25 * di_q
    return make_signal(direction * min(1.0, separation / 1.5), q)


def signal_range(s: dict[str, Any]) -> tuple[float, float]:
    range_q = clamp01((28.0 - s["adx"]) / 16.0)
    extreme = clamp(s["bb_z"], -1.5, 1.5) / 1.5
    q = range_q * clamp01((abs(s["bb_z"]) - 0.25) / 0.75)
    if s["atr_ratio"] > 1.45:
        q *= 0.35
    return make_signal(-extreme, q)


def signal_breakout(s: dict[str, Any]) -> tuple[float, float]:
    value = 1.0 if s["close1"] > s["highest20"] else -1.0 if s["close1"] < s["lowest20"] else 0.0
    if value == 0.0:
        top = (s["highest20"] - s["close1"]) / s["atr"]
        bottom = (s["close1"] - s["lowest20"]) / s["atr"]
        if 0.0 <= top < 0.20:
            value = 0.35
        if 0.0 <= bottom < 0.20:
            value = -0.35
    vol_q = clamp01((s["atr_ratio"] - 0.90) / 0.70)
    adx_q = clamp01((s["adx"] - 16.0) / 24.0)
    body_q = clamp01(s["body_atr"] / 1.20)
    q = (0.40 * vol_q + 0.35 * adx_q + 0.25 * body_q) * min(1.0, abs(value) + 0.25)
    return make_signal(value, q)


def signal_pullback(s: dict[str, Any]) -> tuple[float, float]:
    trend = sign(s["ma_fast"] - s["ma_slow"])
    adx_q = clamp01((s["adx"] - 18.0) / 22.0)
    value = q = 0.0
    if trend > 0.0 and s["close1"] < s["ma_fast"] and s["close1"] > s["ma_slow"]:
        depth = (s["ma_fast"] - s["close1"]) / s["atr"]
        value = clamp(0.40 + depth / 1.5, 0.0, 1.0)
        q = adx_q * clamp01((depth + 0.15) / 0.90)
    elif trend < 0.0 and s["close1"] > s["ma_fast"] and s["close1"] < s["ma_slow"]:
        depth = (s["close1"] - s["ma_fast"]) / s["atr"]
        value = -clamp(0.40 + depth / 1.5, 0.0, 1.0)
        q = adx_q * clamp01((depth + 0.15) / 0.90)
    return make_signal(value, q)


def signal_session(s: dict[str, Any]) -> tuple[float, float]:
    hour = s["bar_time"].hour
    value = q = 0.0
    if 0 <= hour < 7:
        value = -clamp(s["bb_z"] / 1.5, -1.0, 1.0)
        q = 0.30 * clamp01((28.0 - s["adx"]) / 18.0)
    elif 7 <= hour < 13:
        value = clamp(safe_div(s["ret3"], s["atr"] / s["close1"], 0.0) / 3.0, -1.0, 1.0)
        q = 0.45 * clamp01((s["adx"] - 15.0) / 25.0 + 0.25)
    elif 13 <= hour < 21:
        value = clamp(safe_div(s["ret3"], s["atr"] / s["close1"], 0.0) / 3.0, -1.0, 1.0)
        q = 0.40 * clamp01((s["adx"] - 15.0) / 25.0 + 0.25)
        if s["range_atr"] > 2.5:
            q *= 0.45
    return make_signal(value, q)


def signal_shock(s: dict[str, Any], shock_halt_atr: float) -> tuple[float, float]:
    candle_dir = sign(s["close1"] - s["open1"])
    shock = clamp01((s["range_atr"] - 1.35) / 1.75)
    body_ratio = safe_div(abs(s["close1"] - s["open1"]), s["high1"] - s["low1"], 0.0)
    vol_q = clamp01((s["atr_ratio"] - 1.0) / 0.65)
    value = candle_dir * shock
    q = shock * (0.55 * clamp01(body_ratio / 0.70) + 0.45 * vol_q)
    if s["range_atr"] >= shock_halt_atr:
        value = q = 0.0
    return make_signal(value, q)


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _corr(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or len(a) < 3:
        return 0.0
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    da, db = [x - ma for x in a], [x - mb for x in b]
    va, vb = sum(x * x for x in da), sum(x * x for x in db)
    return 0.0 if va <= 1e-20 or vb <= 1e-20 else sum(x * y for x, y in zip(da, db, strict=True)) / math.sqrt(va * vb)


def signal_relative(
    main: RoleSeries,
    relative: RoleSeries,
    main_index: int,
    relative_index: int,
    *,
    lookback: int,
    min_corr: float,
) -> tuple[float, float]:
    n = max(8, int(lookback))
    if main_index < n or relative_index < n:
        return make_signal(0.0, 0.0)
    main_rows = main.bars[main_index - n : main_index + 1]
    rel_rows = relative.bars[relative_index - n : relative_index + 1]
    if [row["open_time"] for row in main_rows] != [row["open_time"] for row in rel_rows]:
        return make_signal(0.0, 0.0)
    main_close = [float(row["close"]) for row in reversed(main_rows)]
    rel_close = [float(row["close"]) for row in reversed(rel_rows)]
    ra = [main_close[i] / main_close[i + 1] - 1.0 for i in range(n)]
    rb = [rel_close[i] / rel_close[i + 1] - 1.0 for i in range(n)]
    corr = _corr(ra, rb)
    if abs(corr) < float(min_corr):
        return make_signal(0.0, 0.0)
    norm = math.sqrt(float(n))
    za = safe_div(sum(ra), _std(ra) * norm, 0.0)
    zb = safe_div(sum(rb), _std(rb) * norm, 0.0)
    divergence = za - zb
    value = -clamp(divergence / 2.0, -1.0, 1.0)
    if corr < 0.0:
        value = -value
    q = clamp01(abs(corr)) * clamp01(abs(divergence) / 1.5)
    return make_signal(value, q)


def evaluate_role(
    snapshot: dict[str, Any],
    *,
    role: str,
    shock_halt_atr: float,
    relative_signal: tuple[float, float],
) -> dict[str, tuple[float, float]]:
    return {
        "trend": signal_trend(snapshot),
        "range": signal_range(snapshot),
        "breakout": signal_breakout(snapshot),
        "pullback": signal_pullback(snapshot),
        "session": signal_session(snapshot) if role in SESSION_ROLES else (0.0, 0.0),
        "shock": signal_shock(snapshot, shock_halt_atr),
        "relative": relative_signal,
    }


def fuse_roles(role_families: dict[str, dict[str, tuple[float, float]]]) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for family in FAMILIES:
        observations: list[tuple[float, float]] = []
        for role in ("context", "structure", "main", "timing"):
            if family == "session" and role not in SESSION_ROLES:
                continue
            observations.append(role_families[role][family])
        weight_sum = sum(q for _, q in observations)
        value = safe_div(sum(signal_value * q for signal_value, q in observations), weight_sum, 0.0)
        quality = clamp01(weight_sum / len(observations)) if observations else 0.0
        out[family] = make_signal(value, quality)
    return out


def rule_meta_score(
    fused: dict[str, tuple[float, float]],
    params: dict[str, Any],
) -> tuple[float, float]:
    weight_names = {
        "trend": "InpWeightTrend", "range": "InpWeightRange",
        "breakout": "InpWeightBreakout", "pullback": "InpWeightPullback",
        "session": "InpWeightSession", "shock": "InpWeightShock",
        "relative": "InpWeightRelative",
    }
    signed_sum = abs_sum = capacity = 0.0
    for family in FAMILIES:
        weight = max(0.0, float(params[weight_names[family]]))
        signal_value, quality = fused[family]
        contribution = weight * quality * signal_value
        signed_sum += contribution
        abs_sum += abs(contribution)
        capacity += weight * quality
    consensus = abs(signed_sum) / abs_sum if abs_sum > 1e-12 else 0.0
    score = 0.0 if capacity <= 1e-12 else clamp(signed_sum / capacity, -1.0, 1.0)
    return score, consensus


def build_cp32(
    main_snapshot: dict[str, Any],
    fused: dict[str, tuple[float, float]],
    rule_score: float,
) -> tuple[float, ...]:
    s = main_snapshot
    atr_pct = safe_div(s["atr"], s["close1"], 0.0)
    values = [
        clamp(safe_div(s["ret1"], atr_pct, 0.0), -5.0, 5.0),
        clamp(safe_div(s["ret3"], atr_pct, 0.0), -8.0, 8.0),
        clamp(safe_div(s["ret6"], atr_pct, 0.0), -12.0, 12.0),
        clamp((s["ma_fast"] - s["close1"]) / s["atr"], -5.0, 5.0),
        clamp((s["ma_slow"] - s["close1"]) / s["atr"], -8.0, 8.0),
        clamp((s["ma_fast"] - s["ma_fast_prev"]) / s["atr"], -2.0, 2.0),
        clamp(s["adx"] / 100.0, 0.0, 1.0),
        clamp(s["plus_di"] / 100.0, 0.0, 1.0),
        clamp(s["minus_di"] / 100.0, 0.0, 1.0),
        clamp((s["rsi"] - 50.0) / 50.0, -1.0, 1.0),
        clamp(atr_pct * 100.0, 0.0, 10.0),
        clamp(s["atr_ratio"], 0.0, 4.0),
        clamp(s["bb_z"], -3.0, 3.0),
        clamp(s["bb_width_pct"], 0.0, 20.0),
        clamp01(s["efficiency10"]),
        clamp(s["body_atr"], 0.0, 5.0),
        clamp(s["upper_wick_atr"], 0.0, 5.0),
        clamp(s["lower_wick_atr"], 0.0, 5.0),
        clamp(s["range_atr"], 0.0, 8.0),
        clamp(s["volume_z"], -5.0, 5.0),
    ]
    bar_time = s["bar_time"]
    pi = math.pi
    values.extend([
        math.sin(2.0 * pi * bar_time.hour / 24.0),
        math.cos(2.0 * pi * bar_time.hour / 24.0),
        math.sin(2.0 * pi * ((bar_time.weekday() + 1) % 7) / 7.0),
        math.cos(2.0 * pi * ((bar_time.weekday() + 1) % 7) / 7.0),
    ])
    values.extend(fused[family][0] * fused[family][1] for family in FAMILIES)
    values.append(clamp(rule_score, -1.0, 1.0))
    if len(values) != 32 or not all(math.isfinite(value) for value in values):
        raise ValueError("CP32_REPRODUCTION_INVALID")
    return tuple(float(value) for value in values)
