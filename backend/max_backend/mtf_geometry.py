from __future__ import annotations

import math
from typing import Any

from .config import STRATEGY_CONTRACT
RESOLVER_VERSION = "TRUE_MTF_LOG_RATIO_V1"
MINIMUM_MAIN_TF = "M15"
MINIMUM_MAIN_MINUTES = 15
MINIMUM_ROLE_TF = "M5"
MINIMUM_ROLE_MINUTES = 5

SUPPORTED_MT5_ROLE_TIMEFRAMES: dict[str, int] = {
    "M1": 1,
    "M2": 2,
    "M3": 3,
    "M4": 4,
    "M5": 5,
    "M6": 6,
    "M10": 10,
    "M12": 12,
    "M15": 15,
    "M20": 20,
    "M30": 30,
    "H1": 60,
    "H2": 120,
    "H3": 180,
    "H4": 240,
    "H6": 360,
    "H8": 480,
    "H12": 720,
    "D1": 1440,
    "W1": 10080,
    "MN1": 43200,
}


def _closest_supported(
    target_minutes: float,
    *,
    minimum_inclusive: int | None = None,
    minimum_exclusive: int | None = None,
    maximum_exclusive: int | None = None,
) -> tuple[str, int]:
    candidates: list[tuple[str, int]] = []
    for name, minutes in SUPPORTED_MT5_ROLE_TIMEFRAMES.items():
        if minimum_inclusive is not None and minutes < minimum_inclusive:
            continue
        if minimum_exclusive is not None and minutes <= minimum_exclusive:
            continue
        if maximum_exclusive is not None and minutes >= maximum_exclusive:
            continue
        candidates.append((name, minutes))
    if not candidates:
        raise ValueError("MTF_GEOMETRY_NO_LEGAL_ROLE_TIMEFRAME")

    # Scale-aware distance. Ties resolve to the smaller period so behavior remains
    # deterministic across runtimes and never drifts merely because dict order changes.
    return min(
        candidates,
        key=lambda item: (
            abs(math.log(float(item[1]) / float(target_minutes))),
            item[1],
            item[0],
        ),
    )


def resolve_strategy_geometry(main_tf: str) -> dict[str, Any]:
    main = str(main_tf or "").strip().upper()
    if main not in SUPPORTED_MT5_ROLE_TIMEFRAMES:
        raise ValueError("MTF_MAIN_TIMEFRAME_UNSUPPORTED")
    main_minutes = SUPPORTED_MT5_ROLE_TIMEFRAMES[main]
    if main_minutes < MINIMUM_MAIN_MINUTES:
        raise ValueError("MTF_MAIN_TIMEFRAME_BELOW_M15")

    timing_tf, timing_minutes = _closest_supported(
        main_minutes / 3.0,
        minimum_inclusive=MINIMUM_ROLE_MINUTES,
        maximum_exclusive=main_minutes,
    )
    structure_tf, structure_minutes = _closest_supported(
        main_minutes * 4.0,
        minimum_exclusive=main_minutes,
    )
    context_tf, context_minutes = _closest_supported(
        main_minutes * 16.0,
        minimum_exclusive=structure_minutes,
    )

    ordered = [timing_minutes, main_minutes, structure_minutes, context_minutes]
    if not (
        MINIMUM_ROLE_MINUTES <= timing_minutes
        < main_minutes
        < structure_minutes
        < context_minutes
    ):
        raise ValueError("MTF_GEOMETRY_ORDER_INVALID")
    if len(set(ordered)) != 4:
        raise ValueError("MTF_GEOMETRY_ROLE_DUPLICATION")

    return {
        "contract": STRATEGY_CONTRACT,
        "context_tf": context_tf,
        "structure_tf": structure_tf,
        "main_tf": main,
        "timing_tf": timing_tf,
        "context_minutes": context_minutes,
        "structure_minutes": structure_minutes,
        "main_minutes": main_minutes,
        "timing_minutes": timing_minutes,
        "minimum_main_tf": MINIMUM_MAIN_TF,
        "minimum_role_tf": MINIMUM_ROLE_TF,
        "closed_bar_only": True,
        "decision_cadence": "MAIN_TF",
        "resolver_version": RESOLVER_VERSION,
        "resolver": {
            "timing_target": "MAIN/3",
            "structure_target": "MAIN*4",
            "context_target": "MAIN*16",
            "distance": "ABS_LOG_RATIO",
            "tie_break": "SMALLER_TIMEFRAME",
        },
    }


def _valid_main_timeframes() -> tuple[str, ...]:
    valid: list[str] = []
    for name, minutes in SUPPORTED_MT5_ROLE_TIMEFRAMES.items():
        if minutes < MINIMUM_MAIN_MINUTES:
            continue
        try:
            resolve_strategy_geometry(name)
        except ValueError:
            continue
        valid.append(name)
    return tuple(valid)


ALLOWED_MAIN_TIMEFRAMES = _valid_main_timeframes()


def assert_geometry_matches_main(
    geometry: dict[str, Any],
    main_tf: str,
) -> dict[str, Any]:
    expected = resolve_strategy_geometry(main_tf)
    if geometry != expected:
        raise ValueError("MTF_STRATEGY_GEOMETRY_MISMATCH")
    return expected


def frozen_geometry_inputs(request: dict[str, Any]) -> dict[str, Any]:
    main_tf = str(request.get("period") or "").strip().upper()
    geometry = request.get("strategy_geometry")
    if not isinstance(geometry, dict):
        raise ValueError("MTF_STRATEGY_GEOMETRY_MISSING")
    expected = assert_geometry_matches_main(geometry, main_tf)
    if str(request.get("strategy_contract") or "") != STRATEGY_CONTRACT:
        raise ValueError("MTF_STRATEGY_CONTRACT_MISMATCH")
    return {
        "InpStrategyContract": STRATEGY_CONTRACT,
        "InpMTFResolverVersion": expected["resolver_version"],
        "InpMTFContextMinutes": expected["context_minutes"],
        "InpMTFStructureMinutes": expected["structure_minutes"],
        "InpMTFMainMinutes": expected["main_minutes"],
        "InpMTFTimingMinutes": expected["timing_minutes"],
    }


def latest_fully_closed_open_time(
    open_times: list[int],
    *,
    timeframe_minutes: int,
    decision_time: int,
) -> int:
    duration = int(timeframe_minutes) * 60
    if duration <= 0:
        raise ValueError("MTF_TIMEFRAME_DURATION_INVALID")
    eligible = [
        int(open_time)
        for open_time in open_times
        if int(open_time) + duration <= int(decision_time)
    ]
    if not eligible:
        raise ValueError("MTF_NO_FULLY_CLOSED_BAR")
    return max(eligible)


def fuse_role_observations(
    observations: list[tuple[float, float]],
) -> dict[str, float]:
    if not observations:
        return {"signal": 0.0, "quality": 0.0}
    weight_sum = 0.0
    signed = 0.0
    for signal, quality in observations:
        q = max(0.0, min(1.0, float(quality)))
        signed += float(signal) * q
        weight_sum += q
    signal = signed / weight_sum if weight_sum > 0.0 else 0.0
    quality = max(0.0, min(1.0, weight_sum / len(observations)))
    return {"signal": signal, "quality": quality}
