from __future__ import annotations

import csv
import hashlib
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import EA_BASELINE, EA_VERSION
from .ea import verify_baseline_snapshot
from .mt5 import detect_mt5
from .mtf_geometry import (
    ALLOWED_MAIN_TIMEFRAMES,
    STRATEGY_CONTRACT,
    SUPPORTED_MT5_ROLE_TIMEFRAMES,
    frozen_geometry_inputs,
    resolve_strategy_geometry,
)
from .optimizer_scientist import route_config_from_environment, scientist_route_status
from .workflow_contract import (
    OPTIMIZER_REQUEST_SCHEMA_CURRENT,
    OPTIMIZER_WORKFLOW_OWNER_EXPLICIT,
)

EA_STEM = "Max_MTF"
EXPERT_SUBDIR = "MaxMTF"
TESTER_SET = "Max_MTF.set"
OPTIMIZER_REPORT_XML = "Max_MTF.xml"
OPTIMIZER_METRICS_CSV = "Max_MTF_metrics.csv"

OPTIMIZER_FITNESS_SCHEMA = "MAX_OPTIMIZER_FITNESS_V2"
OPTIMIZER_FITNESS_FORMULA = "MEAN_R_X_TRADES_POW_ALPHA"
OPTIMIZER_METRICS_SCHEMA_V2 = "MAX_OPTIMIZER_METRICS_V2"
OPTIMIZER_FAIL_CLOSED_SENTINEL = -1.0e9
DEFAULT_OPTIMIZER_TRADE_EXPONENT_ALPHA = 0.50

FAMILY_WEIGHT_PARAMS = (
    "InpWeightTrend",
    "InpWeightRange",
    "InpWeightBreakout",
    "InpWeightPullback",
    "InpWeightSession",
    "InpWeightShock",
    "InpWeightRelative",
)

LEGACY_ABSOLUTE_BOUNDS: dict[str, tuple[float, float, float, str]] = {
    "InpWeightTrend": (0.20, 2.00, 0.10, "float"),
    "InpWeightRange": (0.20, 2.00, 0.10, "float"),
    "InpWeightBreakout": (0.20, 2.00, 0.10, "float"),
    "InpWeightPullback": (0.20, 2.00, 0.10, "float"),
    "InpWeightSession": (0.10, 1.50, 0.10, "float"),
    "InpWeightShock": (0.10, 1.50, 0.10, "float"),
    "InpWeightRelative": (0.10, 1.50, 0.10, "float"),
    "InpEntryThreshold": (0.18, 0.60, 0.02, "float"),
    "InpExitReverseThreshold": (0.20, 0.75, 0.05, "float"),
    "InpMinConsensus": (0.10, 0.70, 0.05, "float"),
    "InpSL_ATR": (1.00, 3.20, 0.20, "float"),
    "InpTP_ATR": (1.20, 5.00, 0.20, "float"),
    "InpMaxHoldBars": (6, 72, 6, "int"),
    "InpShockHaltATR": (2.50, 7.00, 0.50, "float"),
    "InpRelativeLookback": (8, 60, 4, "int"),
    "InpMinRelativeCorr": (0.10, 0.80, 0.05, "float"),
}

ABSOLUTE_BOUNDS: dict[str, tuple[float, float, float, str]] = {
    **LEGACY_ABSOLUTE_BOUNDS,
    "InpRiskPct": (0.50, 5.00, 0.50, "float"),
}

DEFAULT_SPACE = {
    name: {"start": lo, "step": step, "stop": hi}
    for name, (lo, hi, step, _kind) in ABSOLUTE_BOUNDS.items()
}

LEGACY_OPTIMIZER_SCHEMAS = {
    "MAX_REBUILD_OPTIMIZER_REQUEST_V1",
    "MAX_REBUILD_OPTIMIZER_REQUEST_V2",
    "MAX_REBUILD_OPTIMIZER_REQUEST_V3",
    "MAX_REBUILD_OPTIMIZER_REQUEST_V4",
    "MAX_REBUILD_OPTIMIZER_REQUEST_V5",
}
CURRENT_OPTIMIZER_SCHEMA = "MAX_REBUILD_OPTIMIZER_REQUEST_V6"
CURRENT_DAILY_LOSS_LIMIT_PCT = 5.0
HISTORICAL_DAILY_LOSS_LIMIT_PCT = 3.0

def optimizer_parameter_bounds_for_schema(
    schema: str,
) -> dict[str, tuple[float, float, float, str]]:
    token = str(schema or "")
    if token == CURRENT_OPTIMIZER_SCHEMA:
        return ABSOLUTE_BOUNDS
    if token in LEGACY_OPTIMIZER_SCHEMAS:
        return LEGACY_ABSOLUTE_BOUNDS
    raise ValueError(f"unsupported optimizer request schema: {token}")

def optimizer_parameter_bounds_for_keys(
    values: dict[str, Any],
) -> dict[str, tuple[float, float, float, str]]:
    keys = set(values)
    if keys == set(ABSOLUTE_BOUNDS):
        return ABSOLUTE_BOUNDS
    if keys == set(LEGACY_ABSOLUTE_BOUNDS):
        return LEGACY_ABSOLUTE_BOUNDS
    missing_current = sorted(set(ABSOLUTE_BOUNDS) - keys)
    extra_current = sorted(keys - set(ABSOLUTE_BOUNDS))
    raise ValueError(
        f"optimizer parameter universe mismatch; missing={missing_current} extra={extra_current}"
    )

def optimizer_fixed_execution_authority(schema: str) -> dict[str, Any]:
    if str(schema) == CURRENT_OPTIMIZER_SCHEMA:
        return {
            "schema": "MAX_STRATEGY_FIXED_EXECUTION_AUTHORITY_V2",
            "InpMaxDailyLossPct": CURRENT_DAILY_LOSS_LIMIT_PCT,
            "risk_pct_upper_bound": 5.0,
            "relationship": "0 < InpRiskPct <= InpMaxDailyLossPct",
            "applies_to": "NEW_OPTIMIZER_JOBS_ONLY",
        }
    if str(schema) in LEGACY_OPTIMIZER_SCHEMAS:
        return {
            "schema": "MAX_STRATEGY_FIXED_EXECUTION_AUTHORITY_V1",
            "InpMaxDailyLossPct": HISTORICAL_DAILY_LOSS_LIMIT_PCT,
            "risk_pct_upper_bound": 0.5,
            "relationship": "HISTORICAL_ACCEPTED_AUTHORITY",
            "applies_to": "HISTORICAL_OPTIMIZER_JOB",
        }
    raise ValueError(f"unsupported optimizer request schema: {schema}")


SUPPORTED_ROLE_TIMEFRAMES = SUPPORTED_MT5_ROLE_TIMEFRAMES

TICK_MODELS = {
    0: "Every tick",
    1: "1 minute OHLC",
    2: "Open prices only",
    4: "Every tick based on real ticks",
}

OPTIMIZATION_MODES = {
    1: "Slow Complete",
    2: "Fast Genetic",
}

DEFAULT_KPI = {
    "schema": "MAX_STRATEGY_OPTIMIZER_KPI_V2",
    "min_profit_factor": 1.0,
    "min_recovery_factor": 0.0,
    "min_expectancy_r": 0.0,
    "min_weighted_r": 0.0,
    "base_h1_trades_per_month": 20,
    "timeframe_scaling": "SQRT",
    "min_timeframe_factor": 0.20,
    "max_timeframe_factor": 4.00,
    "rounding": "CEIL_MONTHLY_RATE_AND_FINAL_REQUIREMENT",
}

FIXED_INPUTS: dict[str, Any] = {
    "InpAllowLiveTrading": True,
    "InpOneDecisionPerBar": True,
    "InpMaxDailyLossPct": CURRENT_DAILY_LOSS_LIMIT_PCT,
    "InpUseOnnxChampion": False,
    "InpUseOnnxChallenger": False,
    "InpWriteTelemetry": False,
    "InpWriteTrainingData": False,
    "InpWriteChampionTrades": False,
    "InpWriteShadowTrades": False,
}

LEGACY_FIXED_INPUTS: dict[str, Any] = {
    "InpAllowLiveTrading": True,
    "InpOneDecisionPerBar": True,
    "InpRiskPct": 0.50,
    "InpUseOnnxChampion": False,
    "InpUseOnnxChallenger": False,
    "InpWriteTelemetry": False,
    "InpWriteTrainingData": False,
    "InpWriteChampionTrades": False,
    "InpWriteShadowTrades": False,
}


@dataclass
class OptimizationPass:
    round_no: int
    pass_no: int
    profit_factor: float
    recovery_factor: float
    expectancy_r: float
    weighted_r: float
    profit: float
    trades: int
    params: dict[str, Any]
    raw: dict[str, str]
    frame_pass_id: int | None = None
    r_accounted_trades: int = 0
    total_initial_risk: float = float("nan")
    r_sum_net: float = float("nan")
    custom_fitness: float = float("nan")
    fitness_schema: str | None = None
    trade_exponent_alpha: float | None = None
    r_sum_r: float = float("nan")
    minimum_trades_required: int = 1
    min_profit_factor_required: float = 1.0
    min_recovery_factor_required: float = 0.0
    min_expectancy_r_required: float = 0.0
    min_weighted_r_required: float = 0.0

    @property
    def passes_nonweighted(self) -> bool:
        return (
            self.trades >= self.minimum_trades_required
            and math.isfinite(self.profit_factor)
            and self.profit_factor >= self.min_profit_factor_required
            and math.isfinite(self.recovery_factor)
            and self.recovery_factor >= self.min_recovery_factor_required
            and math.isfinite(self.expectancy_r)
            and self.expectancy_r >= self.min_expectancy_r_required
        )

    @property
    def eligible(self) -> bool:
        return (
            self.passes_nonweighted
            and math.isfinite(self.weighted_r)
            and self.weighted_r >= self.min_weighted_r_required
        )

    def payload(self) -> dict[str, Any]:
        obj = asdict(self)
        for key in (
            "weighted_r",
            "total_initial_risk",
            "r_sum_net",
            "custom_fitness",
            "r_sum_r",
        ):
            if not math.isfinite(float(obj[key])):
                obj[key] = None
        if self.fitness_schema is None:
            for key in (
                "custom_fitness",
                "fitness_schema",
                "trade_exponent_alpha",
                "r_sum_r",
            ):
                obj.pop(key, None)
        obj["eligibility"] = "ELIGIBLE" if self.eligible else "INELIGIBLE"
        return obj


RISK_GRID_START = 0.5
RISK_GRID_STOP = 5.0
RISK_GRID_STEP = 0.5
RISK_GRID_VALUES = tuple(
    round(RISK_GRID_START + index * RISK_GRID_STEP, 10)
    for index in range(
        int(round((RISK_GRID_STOP - RISK_GRID_START) / RISK_GRID_STEP)) + 1
    )
)


def _risk_grid_aligned(value: float) -> bool:
    index = (float(value) - RISK_GRID_START) / RISK_GRID_STEP
    return math.isclose(index, round(index), rel_tol=0.0, abs_tol=1e-9)


def _snap_risk_grid_window(start: float, stop: float, center: float) -> tuple[float, float]:
    legal = [
        value for value in RISK_GRID_VALUES
        if value >= start - 1e-12 and value <= stop + 1e-12
    ]
    if not legal:
        nearest = min(RISK_GRID_VALUES, key=lambda value: (abs(value - center), value))
        return nearest, nearest
    return legal[0], legal[-1]


def validate_optimizer_trade_exponent(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("optimizer trade exponent alpha must be numeric")
    try:
        alpha = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("optimizer trade exponent alpha must be numeric") from exc
    if not math.isfinite(alpha):
        raise ValueError("optimizer trade exponent alpha must be finite")
    if alpha < 0.0 or alpha > 1.0:
        raise ValueError("optimizer trade exponent alpha must be between 0 and 1")
    return alpha


def optimizer_fitness_contract(alpha: Any) -> dict[str, Any]:
    value = validate_optimizer_trade_exponent(alpha)
    return {
        "schema": OPTIMIZER_FITNESS_SCHEMA,
        "formula": OPTIMIZER_FITNESS_FORMULA,
        "trade_exponent_alpha": value,
        "mean_r_authority": "R_ACCOUNTED_ARITHMETIC_MEAN",
        "trade_count_authority": "R_ACCOUNTED_CLOSED_TRADES",
    }


def validate_optimizer_fitness_contract(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("optimizer fitness contract missing")
    expected = optimizer_fitness_contract(payload.get("trade_exponent_alpha"))
    for key in (
        "schema",
        "formula",
        "mean_r_authority",
        "trade_count_authority",
    ):
        if payload.get(key) != expected[key]:
            raise ValueError(f"optimizer fitness contract mismatch: {key}")
    return expected


def optimizer_fitness_for_request(request: dict[str, Any]) -> dict[str, Any] | None:
    schema = str(request.get("schema") or "")
    payload = request.get("optimizer_fitness")
    legacy_mean_r_schemas = {
        "MAX_REBUILD_OPTIMIZER_REQUEST_V1",
        "MAX_REBUILD_OPTIMIZER_REQUEST_V2",
        "MAX_REBUILD_OPTIMIZER_REQUEST_V3",
        "MAX_REBUILD_OPTIMIZER_REQUEST_V4",
    }
    if schema in legacy_mean_r_schemas:
        if payload is not None:
            raise ValueError("legacy optimizer request must not contain optimizer_fitness")
        return None
    if schema in {"MAX_REBUILD_OPTIMIZER_REQUEST_V5", CURRENT_OPTIMIZER_SCHEMA}:
        if payload is None:
            raise ValueError("optimizer fitness contract missing for V5/V6 request")
        return validate_optimizer_fitness_contract(payload)
    raise ValueError(f"unsupported optimizer request schema for fitness semantics: {schema}")


def optimizer_fitness_value(mean_r: Any, closed_trades: Any, alpha: Any) -> float:
    try:
        mean = float(mean_r)
        trades = int(closed_trades)
    except (TypeError, ValueError) as exc:
        raise ValueError("optimizer fitness inputs invalid") from exc
    exponent = validate_optimizer_trade_exponent(alpha)
    if not math.isfinite(mean):
        raise ValueError("optimizer Mean R must be finite")
    if trades <= 0:
        raise ValueError("optimizer R-accounted closed trades must be positive")
    result = mean * math.pow(float(trades), exponent)
    if not math.isfinite(result):
        raise ValueError("optimizer fitness must be finite")
    return result


def _is_fail_closed_sentinel(value: Any) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric) and math.isclose(
        numeric,
        OPTIMIZER_FAIL_CLOSED_SENTINEL,
        rel_tol=0.0,
        abs_tol=1e-9,
    )


def _is_v2_zero_trade_sentinel(record: dict[str, Any]) -> bool:
    return (
        int(record["mt5_trades"]) == 0
        and int(record["r_accounted_trades"]) == 0
        and int(record["accounting_errors"]) == 0
        and math.isclose(float(record["sum_r"]), 0.0, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(float(record["sum_net"]), 0.0, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(
            float(record["sum_initial_risk"]),
            0.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        and _is_fail_closed_sentinel(record["mean_expectancy_r"])
        and _is_fail_closed_sentinel(record["weighted_r"])
        and _is_fail_closed_sentinel(record["custom_fitness"])
    )


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_ea_optimizer_defaults(
    source: str | Path = EA_BASELINE,
    *,
    bounds: dict[str, tuple[float, float, float, str]] | None = None,
) -> dict[str, Any]:
    text = Path(source).read_text(encoding="utf-8", errors="strict")
    result: dict[str, Any] = {}
    universe = bounds or ABSOLUTE_BOUNDS
    for name, (_lo, _hi, _step, kind) in universe.items():
        match = re.search(
            rf"input\s+(?:double|int)\s+{re.escape(name)}\s*=\s*([-+0-9.eE]+)\s*;",
            text,
        )
        if not match:
            raise ValueError(f"EA optimizer input declaration not found: {name}")
        numeric = float(match.group(1))
        result[name] = int(round(numeric)) if kind == "int" else numeric
    return result


def validate_search_space(space: dict[str, Any]) -> dict[str, dict[str, float | int]]:
    if not isinstance(space, dict):
        raise ValueError("search space must be an object")
    bounds = optimizer_parameter_bounds_for_keys(space)

    validated: dict[str, dict[str, float | int]] = {}
    for name, spec in space.items():
        if not isinstance(spec, dict):
            raise ValueError(f"{name}: range must be an object")
        lo, hi, floor_step, kind = bounds[name]
        try:
            start = float(spec["start"])
            step = float(spec["step"])
            stop = float(spec["stop"])
        except Exception as exc:
            raise ValueError(f"{name}: invalid numeric range") from exc

        if not all(math.isfinite(value) for value in (start, step, stop)):
            raise ValueError(f"{name}: non-finite range")
        if start < lo - 1e-12 or stop > hi + 1e-12:
            raise ValueError(f"{name}: range outside deterministic hard bounds")
        if stop < start:
            raise ValueError(f"{name}: stop below start")
        if step <= 0:
            raise ValueError(f"{name}: step must be positive")
        minimum_step = floor_step if name == "InpRiskPct" else floor_step * 0.5
        if step < minimum_step - 1e-12:
            raise ValueError(f"{name}: step finer than deterministic floor")
        if name == "InpRiskPct":
            if not math.isclose(step, RISK_GRID_STEP, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("InpRiskPct: step must remain exactly 0.5")
            if not _risk_grid_aligned(start) or not _risk_grid_aligned(stop):
                raise ValueError("InpRiskPct: start/stop must align to Owner 0.5 grid")
        if name in FAMILY_WEIGHT_PARAMS and start <= 0.0:
            raise ValueError(f"{name}: seven-family contract forbids zero/negative weight")

        if kind == "int":
            if any(abs(value - round(value)) > 1e-9 for value in (start, step, stop)):
                raise ValueError(f"{name}: integer range contains non-integer values")
            values = {
                "start": int(round(start)),
                "step": int(round(step)),
                "stop": int(round(stop)),
            }
        else:
            values = {
                "start": round(start, 10),
                "step": round(step, 10),
                "stop": round(stop, 10),
            }
        validated[name] = values
    return validated


def normalize_optimize_params(
    values: Any,
    *,
    bounds: dict[str, tuple[float, float, float, str]] | None = None,
) -> list[str]:
    universe = bounds or ABSOLUTE_BOUNDS
    if values is None:
        return list(universe)
    if not isinstance(values, (list, tuple)):
        raise ValueError("optimize_params must be a list")
    result: list[str] = []
    for raw in values:
        name = str(raw)
        if name not in universe:
            raise ValueError(f"unsupported optimizer parameter: {name}")
        if name not in result:
            result.append(name)
    if not result:
        raise ValueError("Select at least one parameter to optimize")
    return result


def search_space_cardinality(space: dict[str, Any], optimize_params: Any) -> dict[str, Any]:
    validated = validate_search_space(space)
    bounds = optimizer_parameter_bounds_for_keys(validated)
    selected = set(normalize_optimize_params(optimize_params, bounds=bounds))
    counts: dict[str, int] = {}
    total = 1
    for name, spec in validated.items():
        if name not in selected:
            continue
        start = float(spec["start"])
        stop = float(spec["stop"])
        step = float(spec["step"])
        count = max(1, int(math.floor(((stop - start) / step) + 1e-9)) + 1)
        counts[name] = count
        total *= count
    return {
        "optimized_inputs": len(counts),
        "values_per_input": counts,
        "raw_complete_grid_combinations": int(total),
        "authority": "RAW_CARTESIAN_GRID_ONLY_NOT_MT5_GENETIC_TASK_COUNT",
    }


def _parse_date(value: Any) -> datetime:
    text = str(value or "")
    if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", text):
        raise ValueError("dates must use YYYY.MM.DD")
    try:
        return datetime.strptime(text, "%Y.%m.%d")
    except ValueError as exc:
        raise ValueError(f"invalid calendar date: {text}") from exc


def trade_sample(period: str, from_date: str, to_date: str, kpi: dict[str, Any]) -> dict[str, Any]:
    start = _parse_date(from_date)
    end = _parse_date(to_date)
    if start >= end:
        raise ValueError("from_date must be earlier than to_date")
    tf_minutes = SUPPORTED_ROLE_TIMEFRAMES[period]
    days = max(1.0, (end - start).total_seconds() / 86400.0)
    months = days / 30.436875
    ratio = 60.0 / float(tf_minutes)
    scaling = str(kpi.get("timeframe_scaling", "SQRT")).upper()
    if scaling == "LINEAR":
        factor = ratio
    elif scaling == "NONE":
        factor = 1.0
    else:
        factor = math.sqrt(ratio)
    factor = max(
        float(kpi.get("min_timeframe_factor", 0.20)),
        min(float(kpi.get("max_timeframe_factor", 4.00)), factor),
    )
    monthly = int(math.ceil(float(kpi["base_h1_trades_per_month"]) * factor - 1e-12))
    prorated = months * monthly
    minimum = int(math.ceil(prorated - 1e-12))
    return {
        "schema": "MAX_STRATEGY_OPTIMIZER_TRADE_SAMPLE_V1",
        "timeframe": period,
        "timeframe_minutes": tf_minutes,
        "calendar_months": months,
        "effective_months": months,
        "base_h1_trades_per_month": int(kpi["base_h1_trades_per_month"]),
        "timeframe_scaling": scaling,
        "timeframe_factor": factor,
        "scaled_trades_per_month": monthly,
        "prorated_required_before_roundup": prorated,
        "minimum_trades": minimum,
        "rounding": "CEIL_MONTHLY_RATE_AND_FINAL_REQUIREMENT",
        "kpi_profile": dict(kpi),
    }


def freeze_request(raw: dict[str, Any]) -> dict[str, Any]:
    request = dict(raw or {})
    symbol = str(request.get("symbol") or "").strip()
    relative = str(request.get("relative_symbol") or request.get("confirm_symbol") or "").strip()
    if not symbol or not relative:
        raise ValueError("Main Symbol and Relative reference symbol are required")
    if symbol.casefold() == relative.casefold():
        raise ValueError("Relative reference symbol must differ from Main Symbol")

    period = str(request.get("period") or "H1").upper()
    if period not in ALLOWED_MAIN_TIMEFRAMES:
        if period in SUPPORTED_ROLE_TIMEFRAMES:
            raise ValueError("main timeframe must be M15 or higher and resolve four distinct MTF roles")
        raise ValueError("unsupported main timeframe")
    strategy_geometry = resolve_strategy_geometry(period)

    from_date = str(request.get("from_date") or "")
    to_date = str(request.get("to_date") or "")
    start = _parse_date(from_date)
    end = _parse_date(to_date)
    if start >= end:
        raise ValueError("from_date must be earlier than to_date")

    model = int(request.get("model", 1))
    if model not in TICK_MODELS:
        raise ValueError("unsupported MT5 tick model")
    optimization = int(request.get("optimization", 2))
    if optimization not in OPTIMIZATION_MODES:
        raise ValueError("optimization must be complete(1) or genetic(2)")
    max_rounds = int(request.get("max_rounds", 3))
    if not 1 <= max_rounds <= 5:
        raise ValueError("max_rounds must be between 1 and 5")

    scientist_assist = bool(request.get("scientist_assist", False))
    if "scientist" in request or "scientist_llm" in request:
        raise ValueError("Scientist route is server-configured and cannot be supplied by the frontend")
    scientist_route = route_config_from_environment()

    deposit = float(request.get("deposit", 10000.0))
    leverage = int(request.get("leverage", 100))
    if not math.isfinite(deposit) or deposit <= 0:
        raise ValueError("deposit must be positive")
    if leverage <= 0:
        raise ValueError("leverage must be positive")

    trade_exponent_alpha = validate_optimizer_trade_exponent(
        request.get(
            "optimizer_trade_exponent_alpha",
            DEFAULT_OPTIMIZER_TRADE_EXPONENT_ALPHA,
        )
    )
    fitness_contract = optimizer_fitness_contract(trade_exponent_alpha)

    bounds = optimizer_parameter_bounds_for_schema(OPTIMIZER_REQUEST_SCHEMA_CURRENT)
    space = validate_search_space(request.get("search_space") or DEFAULT_SPACE)
    if set(space) != set(bounds):
        raise ValueError("current optimizer search space must contain exactly 17 parameters")
    selected = normalize_optimize_params(request.get("optimize_params"), bounds=bounds)
    fixed_values = read_ea_optimizer_defaults(bounds=bounds)
    for name, value in fixed_values.items():
        lo, hi, _step, kind = bounds[name]
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < lo - 1e-12 or numeric > hi + 1e-12:
            raise RuntimeError(f"EA default outside optimizer hard bounds: {name}")
        if name in FAMILY_WEIGHT_PARAMS and numeric <= 0:
            raise RuntimeError(f"EA default violates seven-family contract: {name}")
        if kind == "int" and abs(numeric - round(numeric)) > 1e-9:
            raise RuntimeError(f"EA integer optimizer default is not integral: {name}")

    kpi = dict(DEFAULT_KPI)
    supplied_kpi = request.get("kpi") if isinstance(request.get("kpi"), dict) else {}
    for key in (
        "min_profit_factor",
        "min_recovery_factor",
        "min_expectancy_r",
        "min_weighted_r",
        "base_h1_trades_per_month",
    ):
        if key in supplied_kpi:
            kpi[key] = supplied_kpi[key]
    for key in ("min_profit_factor", "min_recovery_factor", "min_expectancy_r", "min_weighted_r"):
        value = float(kpi[key])
        if not math.isfinite(value):
            raise ValueError(f"{key} must be finite")
        kpi[key] = value
    kpi["base_h1_trades_per_month"] = int(kpi["base_h1_trades_per_month"])
    if kpi["base_h1_trades_per_month"] < 1:
        raise ValueError("base_h1_trades_per_month must be >= 1")

    mt5 = detect_mt5()
    if mt5.get("status") != "READY_EXECUTABLE_AND_DATA_ROOT":
        raise RuntimeError(f"MT5 unavailable: {mt5.get('reason')}")

    manifest = verify_baseline_snapshot()
    ea_sha = sha256_file(EA_BASELINE)
    if ea_sha != manifest["snapshot_sha256"]:
        raise RuntimeError("EA snapshot hash mismatch")

    sample = trade_sample(period, from_date, to_date, kpi)
    cardinality = search_space_cardinality(space, selected)
    return {
        "schema": OPTIMIZER_REQUEST_SCHEMA_CURRENT,
        "optimizer_result_workflow": OPTIMIZER_WORKFLOW_OWNER_EXPLICIT,
        "symbol": symbol,
        "relative_symbol": relative,
        "period": period,
        "strategy_contract": STRATEGY_CONTRACT,
        "strategy_geometry": strategy_geometry,
        "from_date": from_date,
        "to_date": to_date,
        "deposit": deposit,
        "leverage": leverage,
        "model": model,
        "tick_model_name": TICK_MODELS[model],
        "optimization": optimization,
        "optimization_name": OPTIMIZATION_MODES[optimization],
        "max_rounds": max_rounds,
        "scientist_assist": scientist_assist,
        "scientist": scientist_route,
        "optimizer_fitness": fitness_contract,
        "search_space": space,
        "optimize_params": selected,
        "fixed_param_values": fixed_values,
        "fixed_execution_authority": optimizer_fixed_execution_authority(
            OPTIMIZER_REQUEST_SCHEMA_CURRENT
        ),
        "search_space_cardinality": cardinality,
        "kpi": kpi,
        "trade_sample": sample,
        "mt5": {
            "terminal": mt5["terminal"],
            "metaeditor": mt5["metaeditor"],
            "data_root": mt5["data_root"],
        },
        "ea": {
            "version": EA_VERSION,
            "path": str(EA_BASELINE),
            "sha256": ea_sha,
        },
        "mt5_optimization_criterion": {
            "code": 6,
            "name": "CUSTOM_MAX",
            "fitness": OPTIMIZER_FITNESS_SCHEMA,
            "formula": OPTIMIZER_FITNESS_FORMULA,
            "mean_r_authority": "R_ACCOUNTED_ARITHMETIC_MEAN",
            "trade_count_authority": "R_ACCOUNTED_CLOSED_TRADES",
            "weighted_r_authority": "MAX_MTF_METRICS_CSV_PARAMETER_VECTOR",
            "recovery_factor_authority": "DEDICATED_RECOVERY_FACTOR_COLUMN",
        },
    }


def _format_set_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def build_set_text(
    request: dict[str, Any],
    space: dict[str, Any],
    *,
    optimizer_run_nonce: int,
) -> str:
    schema = str(request.get("schema") or "")
    bounds = optimizer_parameter_bounds_for_schema(schema)
    validated = validate_search_space(space)
    if set(validated) != set(bounds):
        raise ValueError("search space does not match request parameter universe")
    selected = set(normalize_optimize_params(request["optimize_params"], bounds=bounds))
    fixed_values = dict(request["fixed_param_values"])
    lines = [
        "; MAX Rebuild Strategy Optimizer M01",
        "; MT5 native optimization; seven strategy families remain active",
    ]
    for name, spec in validated.items():
        if name in selected:
            default = (
                fixed_values[name]
                if name == "InpRiskPct"
                else (float(spec["start"]) + float(spec["stop"])) / 2.0
            )
            if bounds[name][3] == "int":
                default = int(round(default))
            lines.append(
                f"{name}={_format_set_value(default)}||"
                f"{_format_set_value(spec['start'])}||"
                f"{_format_set_value(spec['step'])}||"
                f"{_format_set_value(spec['stop'])}||Y"
            )
        else:
            value = fixed_values[name]
            lines.append(
                f"{name}={_format_set_value(value)}||"
                f"{_format_set_value(value)}||0||{_format_set_value(value)}||N"
            )

    fitness_contract = optimizer_fitness_for_request(request)
    if fitness_contract is None:
        raise ValueError("current optimizer request requires V2 fitness contract")
    alpha = fitness_contract["trade_exponent_alpha"]
    lines.append(
        "InpOptimizerTradeExponent="
        f"{_format_set_value(alpha)}||{_format_set_value(alpha)}||0||"
        f"{_format_set_value(alpha)}||N"
    )

    fixed = dict(
        FIXED_INPUTS
        if schema == CURRENT_OPTIMIZER_SCHEMA
        else LEGACY_FIXED_INPUTS
    )
    fixed.update(frozen_geometry_inputs(request))
    fixed["InpConfirmSymbol"] = request["relative_symbol"]
    fixed["InpOptimizerMetricsFile"] = OPTIMIZER_METRICS_CSV
    fixed["InpOptimizerRunNonce"] = int(optimizer_run_nonce)
    for name, value in fixed.items():
        lines.append(f"{name}={_format_set_value(value)}")
    return "\n".join(lines) + "\n"


def parse_set_optimizer_entries(
    text: str,
    *,
    bounds: dict[str, tuple[float, float, float, str]] | None = None,
) -> dict[str, dict[str, Any]]:
    universe = bounds or ABSOLUTE_BOUNDS
    result: dict[str, dict[str, Any]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or "=" not in line:
            continue
        name, rhs = line.split("=", 1)
        if name not in universe:
            continue
        parts = rhs.split("||")
        if len(parts) != 5:
            raise ValueError(f"{name}: malformed MT5 set row")
        kind = universe[name][3]
        result[name] = {
            "value": int(round(float(parts[0]))) if kind == "int" else float(parts[0]),
            "start": parts[1],
            "step": parts[2],
            "stop": parts[3],
            "optimize": parts[4].strip().upper(),
        }
    return result


def build_tester_ini(
    request: dict[str, Any],
    *,
    expert_name: str,
    report_name: str = OPTIMIZER_REPORT_XML,
) -> str:
    optimization = int(request["optimization"])
    if optimization not in OPTIMIZATION_MODES:
        raise ValueError("invalid native optimization mode")
    return "\n".join(
        [
            "[Tester]",
            f"Expert={expert_name}",
            f"ExpertParameters={TESTER_SET}",
            f"Symbol={request['symbol']}",
            f"Period={request['period']}",
            f"Deposit={float(request['deposit']):.2f}",
            f"Leverage=1:{int(request['leverage'])}",
            f"Model={int(request['model'])}",
            "ExecutionMode=0",
            f"Optimization={optimization}",
            "OptimizationCriterion=6",
            f"FromDate={request['from_date']}",
            f"ToDate={request['to_date']}",
            "ForwardMode=0",
            f"Report={report_name}",
            "ReplaceReport=1",
            "ShutdownTerminal=1",
            "UseCloud=0",
            "Visual=0",
            "",
        ]
    )


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _row_values(row: ET.Element) -> list[str]:
    return [
        (elem.text or "").strip()
        for elem in row.iter()
        if _strip_ns(elem.tag) == "Data"
    ]


def _float(value: Any, default: float = float("nan")) -> float:
    text = str(value or "").strip().replace("\u00a0", " ").replace(" ", "")
    if not text:
        return default
    if text.endswith("%"):
        text = text[:-1]
    if text.count(",") == 1 and "." not in text:
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return float(text)
    except Exception:
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(round(_float(value, float(default))))
    except Exception:
        return default


def parameter_signature(params: dict[str, Any]) -> tuple[tuple[str, int | float], ...]:
    bounds = optimizer_parameter_bounds_for_keys(params)
    signature: list[tuple[str, int | float]] = []
    for name, (_lo, _hi, _step, kind) in bounds.items():
        if name not in params:
            raise ValueError(f"Optimizer parameter signature missing {name}")
        numeric = float(params[name])
        if not math.isfinite(numeric):
            raise ValueError(f"Non-finite optimizer parameter: {name}")
        value: int | float
        if kind == "int":
            value = int(round(numeric))
        else:
            value = round(numeric, 10)
        signature.append((name, value))
    return tuple(signature)


def _parse_frame_inputs(
    blob: str,
    *,
    bounds: dict[str, tuple[float, float, float, str]],
) -> dict[str, Any]:
    raw: dict[str, str] = {}
    for token in str(blob or "").split("|"):
        if "=" not in token:
            continue
        name, value = token.split("=", 1)
        raw[name.strip()] = value.strip()
    params: dict[str, Any] = {}
    for name, (_lo, _hi, _step, kind) in bounds.items():
        if name not in raw:
            raise ValueError(f"Optimizer FrameInputs missing {name}")
        value = _int(raw[name]) if kind == "int" else _float(raw[name])
        if not math.isfinite(float(value)):
            raise ValueError(f"Non-finite optimizer FrameInputs value for {name}")
        params[name] = value
    return params


def parse_optimizer_metrics_csv(
    path: str | Path,
    *,
    expected_nonce: int,
    optimizer_fitness: dict[str, Any] | None = None,
    parameter_bounds: dict[str, tuple[float, float, float, str]] | None = None,
) -> dict[tuple[tuple[str, int | float], ...], dict[str, Any]]:
    source = Path(path)
    if not source.is_file() or source.stat().st_size < 20:
        raise FileNotFoundError(f"Optimizer Weighted-R sidecar missing/empty: {source}")

    fitness_contract = (
        validate_optimizer_fitness_contract(optimizer_fitness)
        if optimizer_fitness is not None
        else None
    )
    required = {
        "frame_pass_id",
        "frame_inputs",
        "mean_expectancy_r",
        "weighted_r",
        "mt5_trades",
        "r_accounted_trades",
        "sum_net",
        "sum_initial_risk",
        "accounting_errors",
        "run_nonce",
    }
    if fitness_contract is not None:
        required.update(
            {
                "evidence_schema",
                "fitness_schema",
                "custom_fitness",
                "trade_exponent_alpha",
                "sum_r",
            }
        )

    result: dict[tuple[tuple[str, int | float], ...], dict[str, Any]] = {}
    frame_ids: set[int] = set()

    def retain_metric(
        signature: tuple[tuple[str, int | float], ...],
        record: dict[str, Any],
    ) -> None:
        previous = result.get(signature)
        if previous is None:
            result[signature] = record
            return

        previous_metrics = {
            key: value for key, value in previous.items() if key != "frame_pass_id"
        }
        current_metrics = {
            key: value for key, value in record.items() if key != "frame_pass_id"
        }
        if previous_metrics != current_metrics:
            raise ValueError(
                "Conflicting optimizer parameter-vector metrics evidence"
            )

        # MT5 may emit repeated frames for one genetic-search vector. The report
        # joins by vector, so identical metric evidence is safe to collapse. Keep
        # a stable representative frame identity for deterministic replay.
        if int(record["frame_pass_id"]) < int(previous["frame_pass_id"]):
            result[signature] = record

    with source.open("r", encoding="utf-8-sig", errors="strict", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = {str(name).strip() for name in (reader.fieldnames or [])}
        if not required.issubset(fields):
            if fitness_contract is not None:
                raise ValueError("Optimizer V2 fitness sidecar schema mismatch")
            raise ValueError("Optimizer Weighted-R sidecar schema mismatch")
        for row in reader:
            try:
                frame_pass_id = int(str(row["frame_pass_id"]).strip(), 10)
                run_nonce = int(str(row["run_nonce"]).strip(), 10)
                params = _parse_frame_inputs(
                    str(row["frame_inputs"]),
                    bounds=parameter_bounds or ABSOLUTE_BOUNDS,
                )
                record = {
                    "frame_pass_id": frame_pass_id,
                    "frame_params": params,
                    "mean_expectancy_r": float(row["mean_expectancy_r"]),
                    "weighted_r": float(row["weighted_r"]),
                    "mt5_trades": int(str(row["mt5_trades"]).strip(), 10),
                    "r_accounted_trades": int(str(row["r_accounted_trades"]).strip(), 10),
                    "sum_net": float(row["sum_net"]),
                    "sum_initial_risk": float(row["sum_initial_risk"]),
                    "accounting_errors": int(str(row["accounting_errors"]).strip(), 10),
                    "run_nonce": run_nonce,
                }
                if fitness_contract is not None:
                    record.update(
                        {
                            "evidence_schema": str(row["evidence_schema"]).strip(),
                            "fitness_schema": str(row["fitness_schema"]).strip(),
                            "custom_fitness": float(row["custom_fitness"]),
                            "trade_exponent_alpha": float(row["trade_exponent_alpha"]),
                            "sum_r": float(row["sum_r"]),
                        }
                    )
            except Exception as exc:
                raise ValueError(f"Malformed optimizer evidence row: {row}") from exc

            if run_nonce != int(expected_nonce):
                raise ValueError(
                    f"Optimizer Weighted-R run nonce mismatch: expected={expected_nonce} actual={run_nonce}"
                )
            if frame_pass_id in frame_ids:
                raise ValueError(f"Duplicate exact optimizer frame pass id: {frame_pass_id}")
            frame_ids.add(frame_pass_id)

            signature = parameter_signature(params)

            expected_alpha: float | None = None
            if fitness_contract is not None:
                if record["evidence_schema"] != OPTIMIZER_METRICS_SCHEMA_V2:
                    raise ValueError(f"Optimizer V2 evidence schema mismatch in frame {frame_pass_id}")
                if record["fitness_schema"] != OPTIMIZER_FITNESS_SCHEMA:
                    raise ValueError(f"Optimizer fitness schema mismatch in frame {frame_pass_id}")
                if not all(
                    math.isfinite(float(record[key]))
                    for key in ("custom_fitness", "trade_exponent_alpha", "sum_r")
                ):
                    raise ValueError(f"Non-finite optimizer fitness evidence in frame {frame_pass_id}")
                expected_alpha = float(fitness_contract["trade_exponent_alpha"])
                if not math.isclose(
                    record["trade_exponent_alpha"],
                    expected_alpha,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                ):
                    raise ValueError(f"Optimizer fitness alpha mismatch in frame {frame_pass_id}")

                if _is_v2_zero_trade_sentinel(record):
                    record["accounting_valid"] = False
                    retain_metric(signature, record)
                    continue

            if record["accounting_errors"] != 0:
                raise ValueError(f"R-accounting errors in frame {frame_pass_id}")
            if record["mt5_trades"] != record["r_accounted_trades"]:
                raise ValueError(f"R-accounting trade-count parity failure in frame {frame_pass_id}")
            if record["r_accounted_trades"] <= 0:
                raise ValueError(f"Invalid R-accounted trade count in frame {frame_pass_id}")
            if record["sum_initial_risk"] <= 0:
                raise ValueError(f"Invalid summed initial risk in frame {frame_pass_id}")
            if not all(
                math.isfinite(float(record[key]))
                for key in ("mean_expectancy_r", "weighted_r", "sum_net", "sum_initial_risk")
            ):
                raise ValueError(f"Non-finite optimizer R metrics in frame {frame_pass_id}")

            derived_weighted = record["sum_net"] / record["sum_initial_risk"]
            if abs(derived_weighted - record["weighted_r"]) > 1e-9 * max(
                1.0, abs(derived_weighted), abs(record["weighted_r"])
            ):
                raise ValueError(f"Weighted-R arithmetic mismatch in frame {frame_pass_id}")

            if fitness_contract is not None:
                record["accounting_valid"] = True
                derived_mean = record["sum_r"] / record["r_accounted_trades"]
                if not math.isclose(
                    derived_mean,
                    record["mean_expectancy_r"],
                    rel_tol=5e-9,
                    abs_tol=5e-9,
                ):
                    raise ValueError(f"Mean-R arithmetic mismatch in frame {frame_pass_id}")

                expected_fitness = optimizer_fitness_value(
                    record["mean_expectancy_r"],
                    record["r_accounted_trades"],
                    expected_alpha,
                )
                if not math.isclose(
                    record["custom_fitness"],
                    expected_fitness,
                    rel_tol=5e-6,
                    abs_tol=5e-6,
                ):
                    raise ValueError(f"Optimizer fitness arithmetic mismatch in frame {frame_pass_id}")

            retain_metric(signature, record)

    if not result:
        raise ValueError("Optimizer Weighted-R sidecar contains no rows")
    return result

def optimization_report_identity(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file() or source.stat().st_size < 50:
        raise FileNotFoundError(source)
    root = ET.parse(source).getroot()
    title = ""
    for elem in root.iter():
        if _strip_ns(elem.tag) == "Title":
            title = (elem.text or "").strip()
            if title:
                break
    match = re.match(
        r"^(?P<expert>.+?)\s+(?P<symbol>\S+),(?P<period>[A-Z0-9]+)\s+"
        r"(?P<from>\d{4}\.\d{2}\.\d{2})-(?P<to>\d{4}\.\d{2}\.\d{2})$",
        title,
    )
    identity: dict[str, Any] = {
        "path": str(source),
        "title": title,
        "size": source.stat().st_size,
        "mtime_ns": source.stat().st_mtime_ns,
    }
    if match:
        identity.update(match.groupdict())
    return identity


def report_matches_request(path: str | Path, request: dict[str, Any]) -> bool:
    try:
        identity = optimization_report_identity(path)
    except Exception:
        return False
    expected = {
        "expert": EA_STEM,
        "symbol": request["symbol"],
        "period": request["period"],
        "from": request["from_date"],
        "to": request["to_date"],
    }
    return all(str(identity.get(key) or "") == str(value) for key, value in expected.items())


def parse_optimization_xml(
    path: str | Path,
    *,
    round_no: int,
    metrics_path: str | Path,
    expected_nonce: int,
    fixed_param_values: dict[str, Any] | None = None,
    optimize_params: Any = None,
    optimizer_fitness: dict[str, Any] | None = None,
) -> list[OptimizationPass]:
    source = Path(path)
    if not source.is_file() or source.stat().st_size < 50:
        raise FileNotFoundError(f"MT5 optimization report missing/empty: {source}")
    try:
        root = ET.parse(source).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Malformed MT5 optimization XML: {source}") from exc

    fitness_contract = (
        validate_optimizer_fitness_contract(optimizer_fitness)
        if optimizer_fitness is not None
        else None
    )
    if fixed_param_values is not None:
        parameter_bounds = optimizer_parameter_bounds_for_keys(fixed_param_values)
    else:
        parameter_bounds = ABSOLUTE_BOUNDS
    metrics = parse_optimizer_metrics_csv(
        metrics_path,
        expected_nonce=expected_nonce,
        optimizer_fitness=fitness_contract,
        parameter_bounds=parameter_bounds,
    )
    rows = [_row_values(row) for row in root.iter() if _strip_ns(row.tag) == "Row"]
    rows = [row for row in rows if row]
    header_index = None
    for index, row in enumerate(rows):
        lowered = [value.strip().lower() for value in row]
        if "pass" in lowered and "profit factor" in lowered:
            header_index = index
            break
    if header_index is None:
        raise ValueError("MT5 XML header not recognized")

    headers = rows[header_index]
    normalized = {
        re.sub(r"\s+", " ", value.strip().lower()): index
        for index, value in enumerate(headers)
    }

    def column(*names: str) -> int | None:
        for name in names:
            if name.lower() in normalized:
                return normalized[name.lower()]
        return None

    pass_col = column("pass")
    pf_col = column("profit factor")
    rf_col = column("recovery factor")
    custom_col = column("custom")
    result_col = column("result")
    profit_col = column("profit")
    trades_col = column("trades", "total trades")
    inputs_col = column("inputs", "optimized inputs")

    if pass_col is None or pf_col is None or (custom_col is None and result_col is None):
        raise ValueError("MT5 XML lacks required Pass/PF/Custom optimizer columns")
    if rf_col is None:
        raise ValueError("MT5 XML lacks dedicated Recovery Factor column")
    if trades_col is None:
        raise ValueError("MT5 XML lacks Trades column")
    if profit_col is None:
        raise ValueError("MT5 XML lacks Profit column")

    parsed: list[OptimizationPass] = []
    for row in rows[header_index + 1 :]:
        if pass_col >= len(row):
            continue
        pass_no = _int(row[pass_col], -1)
        if pass_no < 0:
            continue

        def at(index: int | None) -> str:
            return row[index] if index is not None and index < len(row) else ""

        xml_custom = _float(at(custom_col if custom_col is not None else result_col))
        if not math.isfinite(xml_custom) and result_col is not None:
            xml_custom = _float(at(result_col))

        frozen = dict(fixed_param_values or {})
        selected = (
            set(normalize_optimize_params(optimize_params, bounds=parameter_bounds))
            if optimize_params is not None
            else set(parameter_bounds)
        )
        params: dict[str, Any] = {
            name: frozen[name]
            for name in parameter_bounds
            if name not in selected and name in frozen
        }
        seen_selected: set[str] = set()
        for name, (_lo, _hi, _step, kind) in parameter_bounds.items():
            index = normalized.get(name.lower())
            if index is None:
                index = normalized.get(name.replace("Inp", "").lower())
            if index is not None and index < len(row):
                params[name] = _int(row[index]) if kind == "int" else _float(row[index])
                if name in selected:
                    seen_selected.add(name)

        if len(params) < len(parameter_bounds) and inputs_col is not None:
            blob = at(inputs_col)
            for name, (_lo, _hi, _step, kind) in parameter_bounds.items():
                match = re.search(
                    rf"(?:^|[;,\s]){re.escape(name)}\s*=\s*([-+0-9.eE]+)",
                    blob,
                )
                if match:
                    params[name] = (
                        _int(match.group(1)) if kind == "int" else _float(match.group(1))
                    )
                    if name in selected:
                        seen_selected.add(name)

        if fixed_param_values is not None and optimize_params is not None:
            missing_selected = sorted(selected - seen_selected)
            if missing_selected:
                raise ValueError(
                    f"MT5 XML row {pass_no} missing selected optimizer parameters: {missing_selected}"
                )
        if fixed_param_values is not None:
            selected = set(normalize_optimize_params(optimize_params, bounds=parameter_bounds))
            missing_selected = sorted(selected - set(params))
            if missing_selected:
                raise ValueError(
                    f"MT5 XML missing selected optimizer parameter(s) in pass {pass_no}: "
                    f"{missing_selected}"
                )
            for name in parameter_bounds:
                if name not in params and name not in selected:
                    if name not in fixed_param_values:
                        raise ValueError(f"Frozen fixed optimizer value missing for {name}")
                    params[name] = fixed_param_values[name]

        if len(params) != len(parameter_bounds):
            continue
        signature = parameter_signature(params)
        metric = metrics.get(signature)

        profit_factor = _float(at(pf_col))
        recovery_factor = _float(at(rf_col))
        profit = _float(at(profit_col))
        trades = _int(at(trades_col), -1)

        if fitness_contract is not None and metric is None:
            raise ValueError(f"Optimizer V2 fitness evidence missing in pass {pass_no}")

        zero_trade_sentinel = bool(
            fitness_contract is not None
            and metric is not None
            and metric.get("accounting_valid") is False
        )
        if zero_trade_sentinel:
            if (
                trades != 0
                or not _is_fail_closed_sentinel(xml_custom)
                or not math.isfinite(recovery_factor)
                or not math.isfinite(profit)
                or abs(profit) > 0.011
            ):
                raise ValueError(f"Malformed zero-trade fail-closed XML evidence in pass {pass_no}")
            if not math.isfinite(profit_factor):
                profit_factor = 0.0
        elif not all(
            math.isfinite(value)
            for value in (profit_factor, recovery_factor, xml_custom, profit)
        ):
            raise ValueError(f"Non-finite XML metric in pass {pass_no}")
        if trades < 0:
            raise ValueError(f"Invalid trade count in pass {pass_no}")

        expectancy = xml_custom
        custom_fitness = xml_custom
        weighted_r = float("nan")
        frame_pass_id = None
        accounted = 0
        initial_risk = float("nan")
        sum_net = float("nan")
        sum_r = float("nan")
        if metric is not None:
            if metric["mt5_trades"] != trades:
                raise ValueError(f"Trade-count XML/frame mismatch in pass {pass_no}")
            if abs(metric["sum_net"] - profit) > 0.011:
                raise ValueError(f"Net-profit XML/frame mismatch in pass {pass_no}")

            if fitness_contract is None:
                if abs(metric["mean_expectancy_r"] - xml_custom) > 5e-6 * max(
                    1.0, abs(metric["mean_expectancy_r"]), abs(xml_custom)
                ):
                    raise ValueError(f"Mean-R XML/frame mismatch in pass {pass_no}")
            elif zero_trade_sentinel:
                if not (
                    _is_fail_closed_sentinel(xml_custom)
                    and _is_fail_closed_sentinel(metric["custom_fitness"])
                ):
                    raise ValueError(f"Zero-trade fail-closed sentinel mismatch in pass {pass_no}")
            else:
                expected_fitness = optimizer_fitness_value(
                    metric["mean_expectancy_r"],
                    metric["r_accounted_trades"],
                    fitness_contract["trade_exponent_alpha"],
                )
                if not math.isclose(
                    xml_custom,
                    metric["custom_fitness"],
                    rel_tol=5e-6,
                    abs_tol=5e-6,
                ):
                    raise ValueError(f"Custom-fitness XML/frame mismatch in pass {pass_no}")
                if not math.isclose(
                    xml_custom,
                    expected_fitness,
                    rel_tol=5e-6,
                    abs_tol=5e-6,
                ):
                    raise ValueError(f"Custom-fitness XML/recomputed mismatch in pass {pass_no}")

            expectancy = metric["mean_expectancy_r"]
            custom_fitness = (
                metric["custom_fitness"]
                if fitness_contract is not None
                else xml_custom
            )
            weighted_r = metric["weighted_r"]
            frame_pass_id = metric["frame_pass_id"]
            accounted = metric["r_accounted_trades"]
            initial_risk = metric["sum_initial_risk"]
            sum_net = metric["sum_net"]
            sum_r = metric.get("sum_r", float("nan"))

        parsed.append(
            OptimizationPass(
                round_no=round_no,
                pass_no=pass_no,
                profit_factor=profit_factor,
                recovery_factor=recovery_factor,
                expectancy_r=expectancy,
                weighted_r=weighted_r,
                profit=profit,
                trades=trades,
                params=params,
                raw={
                    headers[index]: row[index] if index < len(row) else ""
                    for index in range(len(headers))
                },
                frame_pass_id=frame_pass_id,
                r_accounted_trades=accounted,
                total_initial_risk=initial_risk,
                r_sum_net=sum_net,
                custom_fitness=custom_fitness,
                fitness_schema=(
                    fitness_contract["schema"] if fitness_contract is not None else None
                ),
                trade_exponent_alpha=(
                    fitness_contract["trade_exponent_alpha"]
                    if fitness_contract is not None
                    else None
                ),
                r_sum_r=sum_r,
            )
        )

    if not parsed:
        raise ValueError("MT5 optimization report contains zero parseable passes")

    xml_signatures = {parameter_signature(row.params) for row in parsed}
    extra = set(metrics) - xml_signatures
    if extra:
        raise ValueError("Weighted-R sidecar contains parameter vector not present in XML")
    return parsed

def apply_frozen_gates(rows: list[OptimizationPass], request: dict[str, Any]) -> None:
    sample = request["trade_sample"]
    kpi = request["kpi"]
    for row in rows:
        row.minimum_trades_required = int(sample["minimum_trades"])
        row.min_profit_factor_required = float(kpi["min_profit_factor"])
        row.min_recovery_factor_required = float(kpi["min_recovery_factor"])
        row.min_expectancy_r_required = float(kpi["min_expectancy_r"])
        row.min_weighted_r_required = float(kpi["min_weighted_r"])


def unresolved_weighted_contenders(rows: list[OptimizationPass]) -> list[OptimizationPass]:
    return [
        row
        for row in rows
        if row.passes_nonweighted and not math.isfinite(row.weighted_r)
    ]


def select_winner(rows: list[OptimizationPass]) -> OptimizationPass | None:
    if unresolved_weighted_contenders(rows):
        return None
    eligible = [row for row in rows if row.eligible]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda row: (
            row.weighted_r,
            row.expectancy_r,
            row.profit_factor,
            row.recovery_factor,
            -row.pass_no,
        ),
    )


def eligibility_audit(rows: list[OptimizationPass]) -> dict[str, Any]:
    if not rows:
        raise ValueError("eligibility audit requires parsed passes")
    winner = select_winner(rows)
    unresolved = unresolved_weighted_contenders(rows)
    eligible = [row for row in rows if row.eligible]
    first = rows[0]
    return {
        "parsed_passes": len(rows),
        "eligible_passes": len(eligible),
        "weighted_evidence_complete_passes": sum(
            1 for row in rows if math.isfinite(row.weighted_r)
        ),
        "weighted_evidence_incomplete_passes": sum(
            1 for row in rows if not math.isfinite(row.weighted_r)
        ),
        "unresolved_nonweighted_contenders": len(unresolved),
        "thresholds": {
            "minimum_trades": first.minimum_trades_required,
            "min_profit_factor": first.min_profit_factor_required,
            "min_recovery_factor": first.min_recovery_factor_required,
            "min_expectancy_r": first.min_expectancy_r_required,
            "min_weighted_r": first.min_weighted_r_required,
        },
        "gate_counts": {
            "minimum_trades": sum(
                1 for row in rows if row.trades >= row.minimum_trades_required
            ),
            "profit_factor": sum(
                1 for row in rows
                if math.isfinite(row.profit_factor)
                and row.profit_factor >= row.min_profit_factor_required
            ),
            "recovery_factor": sum(
                1 for row in rows
                if math.isfinite(row.recovery_factor)
                and row.recovery_factor >= row.min_recovery_factor_required
            ),
            "expectancy_r": sum(
                1 for row in rows
                if math.isfinite(row.expectancy_r)
                and row.expectancy_r >= row.min_expectancy_r_required
            ),
            "weighted_r": sum(
                1 for row in rows
                if math.isfinite(row.weighted_r)
                and row.weighted_r >= row.min_weighted_r_required
            ),
        },
        "winner_pass": winner.pass_no if winner else None,
    }


def best_near_miss(rows: list[OptimizationPass]) -> OptimizationPass | None:
    finite = [
        row for row in rows
        if all(
            math.isfinite(value)
            for value in (row.profit_factor, row.recovery_factor, row.expectancy_r)
        )
    ]
    if not finite:
        return None
    return max(
        finite,
        key=lambda row: (
            int(row.trades >= row.minimum_trades_required)
            + int(row.profit_factor >= row.min_profit_factor_required)
            + int(row.recovery_factor >= row.min_recovery_factor_required)
            + int(row.expectancy_r >= row.min_expectancy_r_required),
            row.expectancy_r,
            row.profit_factor,
            row.recovery_factor,
            int(math.isfinite(row.weighted_r) and row.weighted_r >= row.min_weighted_r_required),
            row.weighted_r if math.isfinite(row.weighted_r) else float("-inf"),
            -row.pass_no,
        ),
    )


def deterministic_refine(
    space: dict[str, Any],
    rows: list[OptimizationPass],
    optimize_params: Any,
) -> dict[str, dict[str, float | int]]:
    current = validate_search_space(space)
    bounds = optimizer_parameter_bounds_for_keys(current)
    best = best_near_miss(rows)
    if best is None:
        return current
    selected = set(normalize_optimize_params(optimize_params, bounds=bounds))
    refined = {name: dict(spec) for name, spec in current.items()}
    for name, spec in current.items():
        if name not in selected:
            continue
        lo, hi, floor_step, kind = bounds[name]
        center = float(best.params[name])
        old_width = float(spec["stop"]) - float(spec["start"])
        width = max(floor_step * 4.0, old_width * 0.50)
        start = max(lo, center - width / 2.0)
        stop = min(hi, center + width / 2.0)
        step = max(floor_step, float(spec["step"]))
        if name == "InpRiskPct":
            start, stop = _snap_risk_grid_window(start, stop, center)
            step = RISK_GRID_STEP
        elif kind == "int":
            start = int(round(start))
            stop = int(round(stop))
            step = max(1, int(round(step)))
            if stop < start:
                stop = start
        else:
            start = round(start, 10)
            stop = round(stop, 10)
            step = round(step, 10)
        refined[name] = {"start": start, "step": step, "stop": stop}
    return validate_search_space(refined)


def contract_payload() -> dict[str, Any]:
    defaults = read_ea_optimizer_defaults()
    return {
        "schema": "MAX_REBUILD_OPTIMIZER_CONTRACT_V2",
        "ea_version": EA_VERSION,
        "parameters": [
            {
                "name": name,
                "hard_min": lo,
                "hard_max": hi,
                "base_step": step,
                "type": kind,
                "current_ea_default": defaults[name],
            }
            for name, (lo, hi, step, kind) in ABSOLUTE_BOUNDS.items()
        ],
        "default_search_space": DEFAULT_SPACE,
        "default_optimize_params": list(ABSOLUTE_BOUNDS),
        "default_kpi": DEFAULT_KPI,
        "fixed_execution_authority": optimizer_fixed_execution_authority(
            CURRENT_OPTIMIZER_SCHEMA
        ),
        "optimizer_parameter_count": len(ABSOLUTE_BOUNDS),
        "main_timeframes": list(ALLOWED_MAIN_TIMEFRAMES),
        "role_timeframes": list(SUPPORTED_ROLE_TIMEFRAMES),
        "strategy_contract": STRATEGY_CONTRACT,
        "tick_models": [
            {"value": value, "label": label}
            for value, label in TICK_MODELS.items()
        ],
        "optimization_modes": [
            {"value": value, "label": label}
            for value, label in OPTIMIZATION_MODES.items()
        ],
        "defaults": {
            "period": "H1",
            "model": 1,
            "optimization": 2,
            "max_rounds": 3,
            "deposit": 10000.0,
            "leverage": 100,
            "optimizer_trade_exponent_alpha": DEFAULT_OPTIMIZER_TRADE_EXPONENT_ALPHA,
        },
        "scientist": {
            "advisory_only": True,
            "route": scientist_route_status(),
            "temperature": 0.10,
            "top_pass_limit": 12,
            "unavailable_behavior": "DETERMINISTIC_FALLBACK",
        },
        "winner_term": "ELIGIBLE_WINNER",
    }
