from __future__ import annotations

import csv
import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .mtf_geometry import SUPPORTED_MT5_ROLE_TIMEFRAMES
from .research_contract import FEATURE_CONTRACT, stable_hash
from .research_cp32 import (
    FEATURE_NAMES,
    build_cp32,
    evaluate_role,
    fuse_roles,
    latest_fully_closed_index,
    market_snapshot,
    prepare_role_series,
    rule_meta_score,
    signal_relative,
)

SOURCE_BUNDLE_SCHEMA = "MAX_RESEARCH_SOURCE_BUNDLE_R01_V1"
SOURCE_PRODUCER_SCHEMA = "MAX_RESEARCH_SOURCE_PRODUCER_R01_V1"
SOURCE_PRODUCER_VERSION = "R01_MANAGED_MT5_SOURCE_V1"
DATASET_SCHEMA = "MAX_RESEARCH_DATASET_R01_V1"
FEATURE_MANIFEST_SCHEMA = "MAX_RESEARCH_FEATURE_MANIFEST_R01_V1"
LABEL_MANIFEST_SCHEMA = "MAX_RESEARCH_LABEL_MANIFEST_R01_V1"
DEPENDENCY_SCHEMA = "MAX_RESEARCH_DEPENDENCY_R01_V1"
DATA_QUALITY_SCHEMA = "MAX_RESEARCH_DATA_QUALITY_R01_V1"
CHRONOLOGY_SCHEMA = "MAX_RESEARCH_CHRONOLOGY_R01_V1"
LABEL_CONTRACT_ID = "MAX_PARENT_FIRST_BARRIER_3CLASS_R01_V1"
DATASET_BUILDER_VERSION = "R01_DATASET_BUILDER_V1"
CP32_PARITY_ABS_TOL = 5e-6

EA_PREFIX_COLUMNS = (
    "contract","signal_time","decision_bar_time","symbol","period",
    "open","high","low","close","atr","decision_bid","decision_ask","spread_points",
    "sl_atr","tp_atr","max_hold_bars","consensus",
)
EA_COLUMNS = EA_PREFIX_COLUMNS + FEATURE_NAMES
RAW_REQUIRED_COLUMNS = (
    "open_time","open","high","low","close","tick_volume","spread_points",
)
ROLE_ORDER = ("context","structure","main","timing")
TARGET_ONLY_FIELDS = {
    "label",
    "long_r",
    "short_r",
    "target_valid",
    "target_reason",
    "supervised_eligible",
}
FEATURE_INFORMATION_CONTRACT = {
    "window_alignment": "TRAILING_OR_POINT_IN_TIME",
    "maximum_future_offset": 0,
    "preprocessing_fit_scope": "NONE_OR_TRAINING_ONLY",
    "source_fill": "NONE",
    "target_dependency": False,
}

FEATURE_INPUT_FIELDS = {
    "source_row_id",
    "signal_time",
    "decision_time",
    "symbol",
    "period",
    *EA_PREFIX_COLUMNS[5:],
    *FEATURE_NAMES,
}

MT5_PERIOD_ENUM = {
    "M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5, "M6": 6,
    "M10": 10, "M12": 12, "M15": 15, "M20": 20, "M30": 30,
    "H1": 16385, "H2": 16386, "H3": 16387, "H4": 16388,
    "H6": 16390, "H8": 16392, "H12": 16396,
    "D1": 16408, "W1": 32769, "MN1": 49153,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_tz(value: str):
    token = str(value or "").strip()
    if not token:
        raise ValueError("R01_SOURCE_TIMEZONE_REQUIRED")
    if token.upper() == "UTC":
        return timezone.utc
    if token[0:1] in {"+", "-"} and len(token) == 6 and token[3] == ":":
        sign = 1 if token[0] == "+" else -1
        try:
            hours = int(token[1:3])
            minutes = int(token[4:6])
        except ValueError as exc:
            raise ValueError("R01_SOURCE_TIMEZONE_INVALID") from exc
        if hours > 23 or minutes > 59:
            raise ValueError("R01_SOURCE_TIMEZONE_INVALID")
        return timezone(sign * timedelta(hours=hours, minutes=minutes))
    try:
        return ZoneInfo(token)
    except Exception as exc:
        raise ValueError("R01_SOURCE_TIMEZONE_INVALID") from exc


def parse_source_time(value: Any, source_timezone: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("R01_TIMESTAMP_MISSING")
    normalized = text.replace(".", "-", 2) if "." in text[:10] else text
    parsed: datetime | None = None
    for candidate in (normalized, normalized.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError(f"R01_TIMESTAMP_INVALID:{text}")
    source_tz = _source_tz(source_timezone)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=source_tz)
    return parsed.astimezone(source_tz)


def parse_time(value: Any, source_timezone: str) -> datetime:
    return parse_source_time(value, source_timezone).astimezone(timezone.utc)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as handle:
        first = handle.readline()
        handle.seek(0)
        delimiter = ";" if first.count(";") >= first.count(",") else ","
        return list(csv.DictReader(handle, delimiter=delimiter))


def _resolve_file(bundle_path: Path, raw: Any) -> Path:
    value = Path(str(raw or ""))
    path = value if value.is_absolute() else bundle_path.parent / value
    if not path.is_file():
        raise FileNotFoundError(f"R01_SOURCE_FILE_MISSING:{path}")
    return path.resolve()


def _verify_file_entry(bundle_path: Path, entry: Any) -> Path:
    if not isinstance(entry, dict):
        raise ValueError("R01_SOURCE_FILE_ENTRY_INVALID")
    path = _resolve_file(bundle_path, entry.get("path"))
    expected = str(entry.get("sha256") or "").lower()
    actual = sha256_file(path)
    if expected != actual:
        raise RuntimeError(f"R01_SOURCE_HASH_MISMATCH:{path.name}")
    return path


def load_source_bundle(
    bundle_path: Path,
    *,
    parent: dict[str, Any],
) -> dict[str, Any]:
    try:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("R01_SOURCE_BUNDLE_UNREADABLE") from exc
    if not isinstance(bundle, dict) or bundle.get("schema") != SOURCE_BUNDLE_SCHEMA:
        raise RuntimeError("R01_SOURCE_BUNDLE_SCHEMA_INVALID")
    if (
        bundle.get("producer_schema") != SOURCE_PRODUCER_SCHEMA
        or bundle.get("producer_version") != SOURCE_PRODUCER_VERSION
    ):
        raise RuntimeError("R01_SOURCE_BUNDLE_PRODUCER_INVALID")
    expected = {
        "research_id": parent["research_id"],
        "research_parent_id": parent["research_parent_id"],
        "parent_strategy_id": parent["parent_strategy_id"],
        "parent_authority_sha256": parent["parent_authority_sha256"],
        "ea_sha256": parent["ea_sha256"],
        "ea_semantic_version": parent["ea_semantic_version"],
        "feature_contract": FEATURE_CONTRACT,
        "strategy_contract": parent["strategy_contract"],
        "resolver_version": parent["mtf_resolver_version"],
        "main_symbol": parent["main_symbol"],
        "relative_symbol": parent["relative_symbol"],
    }
    for key, value in expected.items():
        if bundle.get(key) != value:
            raise RuntimeError(f"R01_SOURCE_BUNDLE_PARENT_MISMATCH:{key}")
    if bundle.get("strategy_geometry") != parent["strategy_geometry"]:
        raise RuntimeError("R01_SOURCE_BUNDLE_GEOMETRY_MISMATCH")
    source_timezone = str(bundle.get("source_timezone") or "")
    _source_tz(source_timezone)
    timezone_authority = str(bundle.get("source_timezone_authority") or "").strip()
    if not timezone_authority:
        raise RuntimeError("R01_SOURCE_TIMEZONE_AUTHORITY_REQUIRED")
    if not str(bundle.get("broker") or "").strip() or not str(bundle.get("feed") or "").strip():
        raise RuntimeError("R01_BROKER_FEED_IDENTITY_REQUIRED")
    identity = bundle.get("mt5_source_identity")
    identity_sha = str(bundle.get("source_identity_sha256") or "")
    if not isinstance(identity, dict) or not identity_sha:
        raise RuntimeError("R01_MT5_SOURCE_IDENTITY_REQUIRED")
    identity_body = dict(identity)
    embedded_identity_sha = str(identity_body.pop("source_identity_sha256", "") or "")
    main_identity = identity.get("main_symbol")
    relative_identity = identity.get("relative_symbol")
    if not isinstance(main_identity, dict) or not isinstance(relative_identity, dict):
        raise RuntimeError("R01_MT5_SOURCE_IDENTITY_MISMATCH")
    if (
        embedded_identity_sha != identity_sha
        or stable_hash(identity_body) != identity_sha
        or not str(identity.get("provider") or "").strip()
        or not str(identity.get("terminal_authority") or "").strip()
        or not str(identity.get("terminal_company") or "").strip()
        or not str(identity.get("commondata_authority") or "").strip()
        or str(identity.get("account_server") or "") != str(bundle.get("feed") or "")
        or str(identity.get("account_company") or "") != str(bundle.get("broker") or "")
        or str(identity.get("source_timezone") or "") != source_timezone
        or str(identity.get("source_timezone_authority") or "") != timezone_authority
        or str(main_identity.get("name") or "") != str(bundle.get("main_symbol") or "")
        or str(relative_identity.get("name") or "") != str(bundle.get("relative_symbol") or "")
    ):
        raise RuntimeError("R01_MT5_SOURCE_IDENTITY_MISMATCH")
    point_size = float(bundle.get("point_size") or 0.0)
    if not math.isfinite(point_size) or point_size <= 0.0:
        raise RuntimeError("R01_POINT_SIZE_INVALID")
    identity_point = float(main_identity.get("point") or 0.0)
    if (
        not math.isfinite(identity_point)
        or identity_point <= 0.0
        or abs(identity_point - point_size) > 1e-15
    ):
        raise RuntimeError("R01_POINT_SIZE_IDENTITY_MISMATCH")
    files = bundle.get("files")
    if not isinstance(files, dict):
        raise RuntimeError("R01_SOURCE_FILES_INVALID")
    training_path = _verify_file_entry(bundle_path, files.get("ea_cp32"))
    role_files = files.get("role_bars")
    if not isinstance(role_files, dict):
        raise RuntimeError("R01_ROLE_FILES_INVALID")
    geometry = parent["strategy_geometry"]
    required_tf = {role: str(geometry[f"{role}_tf"]) for role in ROLE_ORDER}
    resolved_roles: dict[str, dict[str, Path]] = {"main": {}, "relative": {}}
    for side, symbol in (("main", parent["main_symbol"]), ("relative", parent["relative_symbol"])):
        side_map = role_files.get(side)
        if not isinstance(side_map, dict):
            raise RuntimeError(f"R01_ROLE_FILES_MISSING:{side}")
        for role, timeframe in required_tf.items():
            entry = side_map.get(role)
            path = _verify_file_entry(bundle_path, entry)
            if str(entry.get("timeframe") or "").upper() != timeframe:
                raise RuntimeError(f"R01_ROLE_TIMEFRAME_MISMATCH:{side}:{role}")
            if str(entry.get("symbol") or "") != symbol:
                raise RuntimeError(f"R01_ROLE_SYMBOL_MISMATCH:{side}:{role}")
            resolved_roles[side][role] = path
    return {
        "manifest": bundle,
        "bundle_sha256": sha256_file(bundle_path),
        "training_path": training_path,
        "role_paths": resolved_roles,
        "source_timezone": source_timezone,
        "source_timezone_authority": timezone_authority,
        "source_identity_sha256": identity_sha,
        "point_size": point_size,
        "broker": str(bundle["broker"]),
        "feed": str(bundle["feed"]),
    }


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"R01_NON_NUMERIC:{label}") from exc
    if not math.isfinite(number):
        raise ValueError(f"R01_NONFINITE:{label}")
    return number


def load_raw_bars(
    path: Path,
    *,
    source_timezone: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = _read_csv(path)
    if not rows:
        raise RuntimeError(f"R01_RAW_BARS_EMPTY:{path.name}")
    missing = sorted(set(RAW_REQUIRED_COLUMNS) - set(rows[0]))
    if missing:
        raise RuntimeError(f"R01_RAW_COLUMNS_MISSING:{path.name}:{','.join(missing)}")
    bars: list[dict[str, Any]] = []
    timestamps: set[datetime] = set()
    duplicates = 0
    invalid_ohlc = 0
    prior: datetime | None = None
    non_monotonic = 0
    for index, row in enumerate(rows):
        source_open_time = parse_source_time(row["open_time"], source_timezone)
        open_time = source_open_time.astimezone(timezone.utc)
        if open_time in timestamps:
            duplicates += 1
        timestamps.add(open_time)
        if prior is not None and open_time <= prior:
            non_monotonic += 1
        prior = open_time
        o = _finite_number(row["open"], f"open:{index}")
        h = _finite_number(row["high"], f"high:{index}")
        l = _finite_number(row["low"], f"low:{index}")
        c = _finite_number(row["close"], f"close:{index}")
        volume = _finite_number(row["tick_volume"], f"tick_volume:{index}")
        spread = _finite_number(row["spread_points"], f"spread_points:{index}")
        if h < max(o, c) or l > min(o, c) or h < l or spread < 0 or volume < 0:
            invalid_ohlc += 1
        bars.append({
            "open_time": open_time,
            "source_open_time": source_open_time,
            "open": o, "high": h, "low": l, "close": c,
            "tick_volume": volume,
            "spread_points": spread,
        })
    if duplicates:
        raise RuntimeError(f"R01_DUPLICATE_TIMESTAMPS:{path.name}:{duplicates}")
    if non_monotonic:
        raise RuntimeError(f"R01_NON_MONOTONIC_SOURCE:{path.name}:{non_monotonic}")
    if invalid_ohlc:
        raise RuntimeError(f"R01_INVALID_OHLC:{path.name}:{invalid_ohlc}")
    return bars, {
        "path": str(path),
        "sha256": sha256_file(path),
        "rows": len(bars),
        "start_utc": bars[0]["open_time"].isoformat(),
        "end_utc": bars[-1]["open_time"].isoformat(),
        "duplicates": duplicates,
        "non_monotonic": non_monotonic,
        "invalid_ohlc": invalid_ohlc,
        "finite": True,
        "future_fill": False,
        "backward_fill": False,
        "silent_duplicate_removal": False,
    }


def load_ea_cp32(
    path: Path,
    *,
    source_timezone: str,
    parent: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_rows = _read_csv(path)
    if not raw_rows:
        raise RuntimeError("R01_EA_CP32_EMPTY")
    if tuple(raw_rows[0].keys()) != EA_COLUMNS:
        raise RuntimeError("R01_EA_CP32_COLUMN_ORDER_MISMATCH")
    rows: list[dict[str, Any]] = []
    seen: set[datetime] = set()
    previous: datetime | None = None
    for index, raw in enumerate(raw_rows):
        if raw["contract"] != FEATURE_CONTRACT:
            raise RuntimeError("R01_EA_FEATURE_CONTRACT_MISMATCH")
        if raw["symbol"] != parent["main_symbol"]:
            raise RuntimeError("R01_EA_SYMBOL_MISMATCH")
        signal_time = parse_time(raw["signal_time"], source_timezone)
        decision_time = parse_time(raw["decision_bar_time"], source_timezone)
        if signal_time in seen:
            raise RuntimeError("R01_EA_DUPLICATE_SIGNAL_TIME")
        if previous is not None and signal_time <= previous:
            raise RuntimeError("R01_EA_NON_MONOTONIC")
        if decision_time <= signal_time:
            raise RuntimeError("R01_EA_DECISION_TIME_INVALID")
        seen.add(signal_time)
        previous = signal_time
        period = int(raw["period"])
        expected_period = MT5_PERIOD_ENUM.get(
            str(parent["strategy_geometry"]["main_tf"])
        )
        if expected_period is None or period != expected_period:
            raise RuntimeError("R01_EA_MAIN_TIMEFRAME_MISMATCH")
        row: dict[str, Any] = {
            "source_row_id": index,
            "signal_time": signal_time,
            "decision_time": decision_time,
            "symbol": raw["symbol"],
            "period": period,
        }
        for name in EA_PREFIX_COLUMNS[5:]:
            row[name] = _finite_number(raw[name], f"{name}:{index}")
        risk = parent["deterministic_risk"]
        if abs(float(row["sl_atr"]) - float(risk["sl_atr"])) > 1e-12:
            raise RuntimeError("R01_EA_SL_GEOMETRY_MISMATCH")
        if abs(float(row["tp_atr"]) - float(risk["tp_atr"])) > 1e-12:
            raise RuntimeError("R01_EA_TP_GEOMETRY_MISMATCH")
        max_hold = float(row["max_hold_bars"])
        if (
            abs(max_hold - round(max_hold)) > 1e-12
            or int(round(max_hold)) != int(risk["max_hold_bars"])
        ):
            raise RuntimeError("R01_EA_MAX_HOLD_GEOMETRY_MISMATCH")
        if float(row["decision_ask"]) < float(row["decision_bid"]):
            raise RuntimeError("R01_EA_ENTRY_SPREAD_INVALID")
        for name in FEATURE_NAMES:
            row[name] = _finite_number(raw[name], f"{name}:{index}")
        rows.append(row)
    return rows, {
        "path": str(path),
        "sha256": sha256_file(path),
        "rows": len(rows),
        "start_utc": rows[0]["signal_time"].isoformat(),
        "end_utc": rows[-1]["signal_time"].isoformat(),
        "duplicates": 0,
        "monotonic": True,
        "feature_contract": FEATURE_CONTRACT,
        "feature_order": list(FEATURE_NAMES),
    }


def _role_indices(
    prepared: dict[str, Any],
    relative_prepared: dict[str, Any],
    decision_time: datetime,
) -> tuple[dict[str, int], dict[str, int]]:
    main_indices = {
        role: latest_fully_closed_index(prepared[role], decision_time)
        for role in ROLE_ORDER
    }
    relative_indices = {
        role: latest_fully_closed_index(relative_prepared[role], decision_time)
        for role in ROLE_ORDER
    }
    return main_indices, relative_indices


def validate_feature_information_contract(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("R01_FEATURE_INFORMATION_CONTRACT_INVALID")
    if payload.get("window_alignment") != "TRAILING_OR_POINT_IN_TIME":
        raise RuntimeError("R01_CENTERED_FEATURE_WINDOW_FORBIDDEN")
    if int(payload.get("maximum_future_offset", 1)) > 0:
        raise RuntimeError("R01_FUTURE_FEATURE_OFFSET_FORBIDDEN")
    if payload.get("preprocessing_fit_scope") != "NONE_OR_TRAINING_ONLY":
        raise RuntimeError("R01_PREPROCESSING_FIT_SCOPE_FORBIDDEN")
    if payload.get("source_fill") != "NONE":
        raise RuntimeError("R01_SOURCE_FILL_FORBIDDEN")
    if payload.get("target_dependency") is not False:
        raise RuntimeError("R01_TARGET_DERIVED_FEATURE_FORBIDDEN")
    return dict(FEATURE_INFORMATION_CONTRACT)


def validate_feature_input_contract(row: dict[str, Any]) -> None:
    keys = set(row)
    contaminated = sorted(keys & TARGET_ONLY_FIELDS)
    if contaminated:
        raise RuntimeError(
            "R01_DIRECT_TARGET_CONTAMINATION:"
            + ",".join(contaminated)
        )
    unexpected = sorted(keys - FEATURE_INPUT_FIELDS)
    if unexpected:
        raise RuntimeError(
            "R01_FEATURE_INPUT_CONTRACT_EXTRA_FIELD:"
            + ",".join(unexpected)
        )
    missing_features = [name for name in FEATURE_NAMES if name not in row]
    if missing_features:
        raise RuntimeError(
            "R01_FEATURE_INPUT_CONTRACT_MISSING:"
            + ",".join(missing_features)
        )


def validate_source_row_contiguity(
    rows: list[dict[str, Any]],
    *,
    minimum_rows: int = 1,
) -> dict[str, Any]:
    if len(rows) < int(minimum_rows):
        raise RuntimeError("R01_SOURCE_SEQUENCE_TOO_SHORT")
    ids = [int(row["source_row_id"]) for row in rows]
    if len(set(ids)) != len(ids):
        raise RuntimeError("R01_SOURCE_ROW_ID_DUPLICATE")
    gaps = [
        {"left": ids[index - 1], "right": ids[index]}
        for index in range(1, len(ids))
        if ids[index] != ids[index - 1] + 1
    ]
    if gaps:
        raise RuntimeError(
            "R01_SOURCE_ROW_ID_GAP:"
            + ",".join(f"{item['left']}->{item['right']}" for item in gaps)
        )
    return {
        "status": "PASS",
        "row_count": len(ids),
        "first_source_row_id": ids[0],
        "last_source_row_id": ids[-1],
        "gap_count": 0,
    }


def validate_causal_feature_invariance(
    baseline: tuple[float, ...],
    perturbed: tuple[float, ...],
    *,
    tolerance: float = 1e-12,
) -> dict[str, Any]:
    if len(baseline) != len(FEATURE_NAMES) or len(perturbed) != len(FEATURE_NAMES):
        raise RuntimeError("R01_FEATURE_INVARIANCE_VECTOR_INVALID")
    deltas = {
        name: abs(float(baseline[index]) - float(perturbed[index]))
        for index, name in enumerate(FEATURE_NAMES)
    }
    changed = {
        name: value
        for name, value in deltas.items()
        if value > float(tolerance)
    }
    if changed:
        raise RuntimeError(
            "R01_FUTURE_INFORMATION_FEATURE_DEPENDENCY:"
            + ",".join(sorted(changed))
        )
    return {
        "status": "PASS",
        "tolerance": float(tolerance),
        "max_abs_change": max(deltas.values(), default=0.0),
        "max_abs_change_by_feature": deltas,
    }


def alignment_audit(
    rows: list[dict[str, Any]],
    *,
    prepared: dict[str, Any],
    relative_prepared: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    lookback = max(8, int(params["InpRelativeLookback"]))
    role_checks = 0
    missing_closed_role_bars = 0
    insufficient_relative_history = 0
    relative_windows_checked = 0
    relative_timestamp_mismatches = 0
    examples: list[dict[str, Any]] = []
    for row in rows:
        decision_time = row["decision_time"]
        for role in ROLE_ORDER:
            role_checks += 1
            try:
                main_index = latest_fully_closed_index(
                    prepared[role],
                    decision_time,
                )
                relative_index = latest_fully_closed_index(
                    relative_prepared[role],
                    decision_time,
                )
            except ValueError:
                missing_closed_role_bars += 1
                if len(examples) < 20:
                    examples.append({
                        "source_row_id": row["source_row_id"],
                        "role": role,
                        "reason": "NO_FULLY_CLOSED_ROLE_BAR",
                    })
                continue
            if main_index < lookback or relative_index < lookback:
                insufficient_relative_history += 1
                if len(examples) < 20:
                    examples.append({
                        "source_row_id": row["source_row_id"],
                        "role": role,
                        "reason": "INSUFFICIENT_RELATIVE_HISTORY",
                    })
                continue
            main_times = [
                item["open_time"]
                for item in prepared[role].bars[
                    main_index - lookback : main_index + 1
                ]
            ]
            relative_times = [
                item["open_time"]
                for item in relative_prepared[role].bars[
                    relative_index - lookback : relative_index + 1
                ]
            ]
            relative_windows_checked += 1
            if main_times != relative_times:
                relative_timestamp_mismatches += 1
                if len(examples) < 20:
                    examples.append({
                        "source_row_id": row["source_row_id"],
                        "role": role,
                        "reason": "RELATIVE_TIMESTAMP_WINDOW_MISMATCH",
                        "main_window_start": main_times[0].isoformat(),
                        "main_window_end": main_times[-1].isoformat(),
                        "relative_window_start": relative_times[0].isoformat(),
                        "relative_window_end": relative_times[-1].isoformat(),
                    })
    mtf_status = "PASS" if missing_closed_role_bars == 0 else "FAIL"
    relative_status = (
        "PASS"
        if (
            missing_closed_role_bars == 0
            and insufficient_relative_history == 0
            and relative_timestamp_mismatches == 0
        )
        else "FAIL"
    )
    return {
        "schema": "MAX_RESEARCH_ALIGNMENT_AUDIT_R01_V1",
        "mtf_alignment_status": mtf_status,
        "relative_symbol_alignment_status": relative_status,
        "role_checks": role_checks,
        "missing_closed_role_bars": missing_closed_role_bars,
        "relative_lookback_bars": lookback,
        "relative_windows_checked": relative_windows_checked,
        "insufficient_relative_history": insufficient_relative_history,
        "relative_timestamp_mismatches": relative_timestamp_mismatches,
        "examples": examples,
    }


def reproduce_row_cp32(
    row: dict[str, Any],
    *,
    prepared: dict[str, Any],
    relative_prepared: dict[str, Any],
    params: dict[str, Any],
) -> tuple[float, ...]:
    decision_time = row["decision_time"]
    main_indices, relative_indices = _role_indices(prepared, relative_prepared, decision_time)
    snapshots = {
        role: market_snapshot(prepared[role], main_indices[role])
        for role in ROLE_ORDER
    }
    if snapshots["main"]["open_time_utc"] != row["signal_time"]:
        raise RuntimeError("R01_MAIN_SIGNAL_TIME_PARITY_MISMATCH")
    role_families: dict[str, dict[str, tuple[float, float]]] = {}
    for role in ROLE_ORDER:
        relative = signal_relative(
            prepared[role],
            relative_prepared[role],
            main_indices[role],
            relative_indices[role],
            lookback=int(params["InpRelativeLookback"]),
            min_corr=float(params["InpMinRelativeCorr"]),
        )
        role_families[role] = evaluate_role(
            snapshots[role],
            role=role,
            shock_halt_atr=float(params["InpShockHaltATR"]),
            relative_signal=relative,
        )
    fused = fuse_roles(role_families)
    rule_score, _consensus = rule_meta_score(fused, params)
    return build_cp32(snapshots["main"], fused, rule_score)


def feature_parity(
    rows: list[dict[str, Any]],
    *,
    prepared: dict[str, Any],
    relative_prepared: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    compared = 0
    warmup_rows = 0
    mismatches: list[dict[str, Any]] = []
    max_error = 0.0
    per_feature = {name: 0.0 for name in FEATURE_NAMES}
    for row in rows:
        validate_feature_input_contract(row)
        try:
            reproduced = reproduce_row_cp32(
                row,
                prepared=prepared,
                relative_prepared=relative_prepared,
                params=params,
            )
        except ValueError as exc:
            if str(exc).startswith("FEATURE_") or str(exc) == "NO_FULLY_CLOSED_ROLE_BAR":
                warmup_rows += 1
                continue
            raise
        compared += 1
        for index, name in enumerate(FEATURE_NAMES):
            actual = float(row[name])
            error = abs(actual - reproduced[index])
            max_error = max(max_error, error)
            per_feature[name] = max(per_feature[name], error)
            if error > CP32_PARITY_ABS_TOL and len(mismatches) < 50:
                mismatches.append({
                    "source_row_id": row["source_row_id"],
                    "signal_time": row["signal_time"].isoformat(),
                    "feature": name,
                    "ea": actual,
                    "python": reproduced[index],
                    "abs_error": error,
                })
    if compared == 0:
        raise RuntimeError("R01_CP32_PARITY_NO_COMPARABLE_ROWS")
    return {
        "schema": "MAX_RESEARCH_CP32_PARITY_R01_V1",
        "status": "PASS" if not mismatches else "FAIL",
        "feature_contract": FEATURE_CONTRACT,
        "feature_count": len(FEATURE_NAMES),
        "feature_order": list(FEATURE_NAMES),
        "compared_rows": compared,
        "warmup_context_rows": warmup_rows,
        "absolute_tolerance": CP32_PARITY_ABS_TOL,
        "max_abs_error": max_error,
        "max_abs_error_by_feature": per_feature,
        "mismatch_count_capped": len(mismatches),
        "mismatch_examples": mismatches,
        "authority": "CURRENT_EA_SEMANTICS_VS_INDEPENDENT_PYTHON_REPRODUCTION",
    }


def _future_bar_outcome(
    future: list[dict[str, Any]],
    *,
    time_exit_bar: dict[str, Any],
    direction: str,
    entry_bid: float,
    entry_ask: float,
    stop_distance: float,
    take_distance: float,
    point_size: float,
) -> tuple[float | None, bool]:
    if direction == "long":
        stop = entry_ask - stop_distance
        take = entry_ask + take_distance
    else:
        stop = entry_bid + stop_distance
        take = entry_bid - take_distance
    reward_r = take_distance / stop_distance
    for bar in future:
        spread = float(bar["spread_points"]) * point_size
        if direction == "long":
            hit_stop = float(bar["low"]) <= stop
            hit_take = float(bar["high"]) >= take
        else:
            ask_high = float(bar["high"]) + spread
            ask_low = float(bar["low"]) + spread
            hit_stop = ask_high >= stop
            hit_take = ask_low <= take
        if hit_stop and hit_take:
            return None, True
        if hit_stop:
            return -1.0, False
        if hit_take:
            return reward_r, False
    exit_bid = float(time_exit_bar["open"])
    if direction == "long":
        return (exit_bid - entry_ask) / stop_distance, False
    exit_ask = exit_bid + float(time_exit_bar["spread_points"]) * point_size
    return (entry_bid - exit_ask) / stop_distance, False


def build_labels(
    rows: list[dict[str, Any]],
    *,
    main_bars: list[dict[str, Any]],
    point_size: float,
    parent: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    risk = parent["deterministic_risk"]
    sl_atr = float(risk["sl_atr"])
    tp_atr = float(risk["tp_atr"])
    max_hold = int(risk["max_hold_bars"])
    by_open = {bar["open_time"]: index for index, bar in enumerate(main_bars)}
    counts = {0: 0, 1: 0, 2: 0}
    ambiguous = incomplete = supervised = 0
    labeled: list[dict[str, Any]] = []
    for row in rows:
        item = deepcopy(row)
        start = by_open.get(row["decision_time"])
        if start is None or start + max_hold >= len(main_bars):
            item.update({
                "label": None, "long_r": None, "short_r": None,
                "target_valid": False, "target_reason": "INCOMPLETE_FUTURE_HORIZON",
                "supervised_eligible": False,
            })
            incomplete += 1
            labeled.append(item)
            continue
        future = main_bars[start : start + max_hold]
        time_exit_bar = main_bars[start + max_hold]
        atr = float(row["atr"])
        stop_distance = atr * sl_atr
        take_distance = atr * tp_atr
        if stop_distance <= 0 or take_distance <= 0:
            raise RuntimeError("R01_LABEL_GEOMETRY_INVALID")
        entry_bid = float(row["decision_bid"])
        entry_ask = float(row["decision_ask"])
        if entry_ask < entry_bid:
            raise RuntimeError("R01_ENTRY_SPREAD_INVALID")
        implied_spread_points = (entry_ask - entry_bid) / point_size
        if abs(implied_spread_points - float(row["spread_points"])) > 0.011:
            raise RuntimeError("R01_ENTRY_SPREAD_PARITY_MISMATCH")
        long_r, long_ambiguous = _future_bar_outcome(
            future, time_exit_bar=time_exit_bar,
            direction="long", entry_bid=entry_bid, entry_ask=entry_ask,
            stop_distance=stop_distance, take_distance=take_distance, point_size=point_size,
        )
        short_r, short_ambiguous = _future_bar_outcome(
            future, time_exit_bar=time_exit_bar,
            direction="short", entry_bid=entry_bid, entry_ask=entry_ask,
            stop_distance=stop_distance, take_distance=take_distance, point_size=point_size,
        )
        if long_ambiguous or short_ambiguous:
            item.update({
                "label": None, "long_r": long_r, "short_r": short_r,
                "target_valid": False, "target_reason": "AMBIGUOUS_TP_SL_SAME_BAR",
                "supervised_eligible": False,
            })
            ambiguous += 1
            labeled.append(item)
            continue
        assert long_r is not None and short_r is not None
        if long_r > 0.0 and long_r > short_r:
            label = 2
        elif short_r > 0.0 and short_r > long_r:
            label = 0
        else:
            label = 1
        counts[label] += 1
        supervised += 1
        item.update({
            "label": label, "long_r": float(long_r), "short_r": float(short_r),
            "target_valid": True, "target_reason": "VALID",
            "supervised_eligible": True,
        })
        labeled.append(item)
    return labeled, {
        "schema": LABEL_MANIFEST_SCHEMA,
        "contract_id": LABEL_CONTRACT_ID,
        "classes": {"0": "SELL", "1": "SKIP", "2": "BUY"},
        "future_information_features": False,
        "future_information_targets_only": True,
        "thresholds": None,
        "threshold_authority": "NO_ARBITRARY_EDGE_OR_MARGIN_THRESHOLD",
        "sl_atr": sl_atr,
        "tp_atr": tp_atr,
        "max_hold_bars": max_hold,
        "parent_geometry_row_parity": "REQUIRED",
        "decision_spread_parity": "ASK_MINUS_BID_DIV_POINT_WITH_0.011_POINT_TOLERANCE",
        "entry_semantics": "LONG_ASK_SHORT_BID_AT_DECISION",
        "future_bar_semantics": "BID_OHLC_BARRIERS_WITH_BAR_SPREAD_FOR_SHORT_ASK",
        "time_exit_semantics": "NEXT_MAIN_DECISION_BAR_OPEN_BID_OR_ASK_AFTER_MAX_HOLD",
        "ambiguous_same_bar_policy": "CONTEXT_ONLY_TARGET_INVALID",
        "incomplete_horizon_policy": "CONTEXT_ONLY_TARGET_INVALID",
        "supervised_rows": supervised,
        "ambiguous_rows": ambiguous,
        "incomplete_horizon_rows": incomplete,
        "sell": counts[0], "skip": counts[1], "buy": counts[2],
    }


def dependency_report(parent: dict[str, Any]) -> dict[str, Any]:
    geometry = parent["strategy_geometry"]
    params = parent["strategy_parameters"]
    main_minutes = int(geometry["main_minutes"])
    warmup = 120
    relative = max(8, int(params["InpRelativeLookback"])) + 1
    per_role: dict[str, Any] = {}
    main_equivalent = 0
    for role in ROLE_ORDER:
        minutes = int(geometry[f"{role}_minutes"])
        role_bars = max(warmup, relative)
        equivalent = int(math.ceil(role_bars * minutes / main_minutes))
        per_role[role] = {
            "timeframe": geometry[f"{role}_tf"],
            "timeframe_minutes": minutes,
            "historical_bars": role_bars,
            "main_bar_equivalent": equivalent,
        }
        main_equivalent = max(main_equivalent, equivalent)
    label_horizon = int(parent["deterministic_risk"]["max_hold_bars"])
    base = max(main_equivalent, label_horizon)
    return {
        "schema": DEPENDENCY_SCHEMA,
        "feature_lookback_dependency": per_role,
        "mtf_dependency": "LATEST_FULLY_CLOSED_ROLE_BAR_ONLY",
        "relative_symbol_dependency": {
            "lookback_bars": relative,
            "timestamp_pairing": "EXACT_N_PLUS_1_OPEN_TIMES",
        },
        "label_dependency_main_bars": label_horizon,
        "full_base_dependency_main_bars": base,
        "minimum_legal_purge_main_bars": base,
        "minimum_legal_embargo_main_bars": base,
        "future_candidate_rule": (
            "effective_purge_embargo_main_bars="
            "MAX(R01_BASE_DEPENDENCY_HORIZON,candidate_temporal_sequence_dependency)"
        ),
        "recursive_indicator_seed_authority": (
            "SEALED_SOURCE_HISTORY; no fold-fitted normalization or target information"
        ),
    }


def validate_source_time_coverage(
    source_manifest: dict[str, Any],
    *,
    ea_audit: dict[str, Any],
    source_reports: dict[str, Any],
) -> dict[str, Any]:
    declared = source_manifest.get("source_time_coverage")
    if not isinstance(declared, dict):
        raise RuntimeError("R01_SOURCE_TIME_COVERAGE_REQUIRED")

    actual: dict[str, dict[str, Any]] = {
        "ea_cp32": {
            "rows": int(ea_audit["rows"]),
            "start_utc": str(ea_audit["start_utc"]),
            "end_utc": str(ea_audit["end_utc"]),
        }
    }
    for key, report in source_reports.items():
        actual[key] = {
            "rows": int(report["rows"]),
            "start_utc": str(report["start_utc"]),
            "end_utc": str(report["end_utc"]),
        }

    if set(declared) != set(actual):
        raise RuntimeError("R01_SOURCE_TIME_COVERAGE_SET_MISMATCH")
    mismatches: list[str] = []
    for key, expected in actual.items():
        item = declared.get(key)
        if not isinstance(item, dict):
            mismatches.append(key)
            continue
        try:
            declared_rows = int(item.get("rows"))
        except (TypeError, ValueError):
            mismatches.append(key)
            continue
        if (
            declared_rows != expected["rows"]
            or str(item.get("start_utc") or "") != expected["start_utc"]
            or str(item.get("end_utc") or "") != expected["end_utc"]
        ):
            mismatches.append(key)
    if mismatches:
        raise RuntimeError(
            "R01_SOURCE_TIME_COVERAGE_MISMATCH:" + ",".join(sorted(mismatches))
        )
    return {
        "status": "PASS",
        "entries": actual,
    }


def build_dataset(
    bundle_path: Path,
    *,
    parent: dict[str, Any],
) -> dict[str, Any]:
    source = load_source_bundle(bundle_path, parent=parent)
    information_contract = validate_feature_information_contract(
        FEATURE_INFORMATION_CONTRACT
    )
    ea_rows, ea_audit = load_ea_cp32(
        source["training_path"],
        source_timezone=source["source_timezone"],
        parent=parent,
    )
    sequence_audit = validate_source_row_contiguity(ea_rows)
    role_bars: dict[str, dict[str, list[dict[str, Any]]]] = {"main": {}, "relative": {}}
    source_reports: dict[str, Any] = {}
    for side in ("main", "relative"):
        for role in ROLE_ORDER:
            bars, report = load_raw_bars(
                source["role_paths"][side][role],
                source_timezone=source["source_timezone"],
            )
            role_bars[side][role] = bars
            source_reports[f"{side}:{role}"] = report
    coverage_audit = validate_source_time_coverage(
        source["manifest"],
        ea_audit=ea_audit,
        source_reports=source_reports,
    )
    prepared = {role: prepare_role_series(role_bars["main"][role]) for role in ROLE_ORDER}
    relative_prepared = {role: prepare_role_series(role_bars["relative"][role]) for role in ROLE_ORDER}
    alignment = alignment_audit(
        ea_rows,
        prepared=prepared,
        relative_prepared=relative_prepared,
        params=parent["strategy_parameters"],
    )
    if (
        alignment["mtf_alignment_status"] != "PASS"
        or alignment["relative_symbol_alignment_status"] != "PASS"
    ):
        raise RuntimeError("R01_SOURCE_ALIGNMENT_FAIL")
    parity = feature_parity(
        ea_rows,
        prepared=prepared,
        relative_prepared=relative_prepared,
        params=parent["strategy_parameters"],
    )
    if parity["status"] != "PASS":
        raise RuntimeError("R01_CP32_FEATURE_PARITY_FAIL")
    labeled, label_manifest = build_labels(
        ea_rows,
        main_bars=role_bars["main"]["main"],
        point_size=source["point_size"],
        parent=parent,
    )
    dependency = dependency_report(parent)
    supervised = [row for row in labeled if row["supervised_eligible"]]
    counts = {0: 0, 1: 0, 2: 0}
    for row in supervised:
        counts[int(row["label"])] += 1
    n = len(supervised)
    feature_hash = stable_hash([
        [row[name] for name in FEATURE_NAMES]
        for row in labeled
    ])
    label_hash = stable_hash([
        {
            "source_row_id": row["source_row_id"],
            "label": row["label"],
            "long_r": row["long_r"],
            "short_r": row["short_r"],
            "target_valid": row["target_valid"],
        }
        for row in labeled
    ])
    chronology = {
        "schema": CHRONOLOGY_SCHEMA,
        "status": "PASS",
        "source_timezone": source["source_timezone"],
        "timezone_authority": source["source_timezone_authority"],
        "source_identity_sha256": source["source_identity_sha256"],
        "source_time_coverage": coverage_audit,
        "ea": ea_audit,
        "raw_sources": source_reports,
        "alignment": alignment,
        "source_row_sequence": sequence_audit,
        "unique_timestamps": True,
        "monotonic": True,
        "closed_bars_only": True,
        "future_fill": False,
        "backward_fill": False,
        "silent_duplicate_removal": False,
    }
    data_quality = {
        "schema": DATA_QUALITY_SCHEMA,
        "status": "PASS",
        "physical_rows": len(labeled),
        "usable_rows": len(labeled),
        "context_only_rows": len(labeled) - n,
        "supervised_rows": n,
        "target_invalid_rows": len(labeled) - n,
        "ambiguous_rows": label_manifest["ambiguous_rows"],
        "incomplete_horizon_rows": label_manifest["incomplete_horizon_rows"],
        "duplicate_timestamps": 0,
        "monotonicity": "PASS",
        "invalid_ohlc": 0,
        "non_finite_values": 0,
        "missing_source_data": (
            alignment["missing_closed_role_bars"]
            + alignment["insufficient_relative_history"]
            + alignment["relative_timestamp_mismatches"]
        ),
        "mtf_alignment_status": alignment["mtf_alignment_status"],
        "relative_symbol_alignment_status": alignment["relative_symbol_alignment_status"],
        "alignment_audit": alignment,
        "cp32_completeness": "PASS",
        "sell": counts[0], "skip": counts[1], "buy": counts[2],
        "sell_ratio": counts[0] / n if n else 0.0,
        "skip_ratio": counts[1] / n if n else 0.0,
        "buy_ratio": counts[2] / n if n else 0.0,
        "dataset_start": labeled[0]["signal_time"].isoformat(),
        "dataset_end": labeled[-1]["signal_time"].isoformat(),
        "source_hashes": {
            "source_bundle": source["bundle_sha256"],
            "ea_cp32": ea_audit["sha256"],
            **{key: value["sha256"] for key, value in source_reports.items()},
        },
        "feature_hash": feature_hash,
        "label_hash": label_hash,
    }
    feature_manifest = {
        "schema": FEATURE_MANIFEST_SCHEMA,
        "feature_contract": FEATURE_CONTRACT,
        "feature_count": 32,
        "feature_order": list(FEATURE_NAMES),
        "strategy_geometry": deepcopy(parent["strategy_geometry"]),
        "resolver_version": parent["mtf_resolver_version"],
        "closed_bar_only": True,
        "relative_alignment": "EXACT_TIMESTAMP_PAIRING",
        "warmup_semantics": "MAX(InpWarmupBars,80)=120",
        "missing_data_semantics": "FAIL_OR_CONTEXT_ONLY; NEVER FUTURE_FILL",
        "finite_value_required": True,
        "target_derived_features": [],
        "feature_information_boundary": "CAUSAL_MARKET_HISTORY_AT_OR_BEFORE_DECISION_ONLY",
        "information_contract": information_contract,
        "parity": parity,
        "feature_hash": feature_hash,
    }
    manifest_core = {
        "schema": DATASET_SCHEMA,
        "research_id": parent["research_id"],
        "research_parent_id": parent["research_parent_id"],
        "parent_strategy_id": parent["parent_strategy_id"],
        "parent_authority_sha256": parent["parent_authority_sha256"],
        "ea_version": parent["ea_semantic_version"],
        "ea_sha256": parent["ea_sha256"],
        "strategy_contract": parent["strategy_contract"],
        "feature_contract": FEATURE_CONTRACT,
        "mtf_resolver": parent["mtf_resolver_version"],
        "strategy_geometry": deepcopy(parent["strategy_geometry"]),
        "primary_symbol": parent["main_symbol"],
        "relative_symbol": parent["relative_symbol"],
        "broker": source["broker"],
        "feed": source["feed"],
        "source_timezone": source["source_timezone"],
        "timezone_authority": source["source_timezone_authority"],
        "source_identity_sha256": source["source_identity_sha256"],
        "source_date_range": {
            "start": labeled[0]["signal_time"].isoformat(),
            "end": labeled[-1]["signal_time"].isoformat(),
        },
        "source_data_hashes": data_quality["source_hashes"],
        "feature_data_hash": feature_hash,
        "label_data_hash": label_hash,
        "row_count": len(labeled),
        "feature_ordering": list(FEATURE_NAMES),
        "label_contract": LABEL_CONTRACT_ID,
        "label_manifest_sha256": stable_hash(label_manifest),
        "label_geometry": {
            "sl_atr": label_manifest["sl_atr"],
            "tp_atr": label_manifest["tp_atr"],
            "max_hold_bars": label_manifest["max_hold_bars"],
            "entry_semantics": label_manifest["entry_semantics"],
            "future_bar_semantics": label_manifest["future_bar_semantics"],
            "time_exit_semantics": label_manifest["time_exit_semantics"],
            "ambiguity_policy": label_manifest["ambiguous_same_bar_policy"],
            "incomplete_horizon_policy": label_manifest["incomplete_horizon_policy"],
        },
        "label_horizon_main_bars": label_manifest["max_hold_bars"],
        "feature_dependency_horizon": dependency["feature_lookback_dependency"],
        "full_dependency_horizon_main_bars": dependency["full_base_dependency_main_bars"],
        "minimum_legal_purge_main_bars": dependency["minimum_legal_purge_main_bars"],
        "minimum_legal_embargo_main_bars": dependency["minimum_legal_embargo_main_bars"],
        "dataset_builder_version": DATASET_BUILDER_VERSION,
        "deterministic_provenance": True,
    }
    dataset_id = "RDATA-" + stable_hash(manifest_core)[:24]
    dataset_manifest = {
        **manifest_core,
        "dataset_id": dataset_id,
        "sealed_immutable": True,
    }
    dataset_manifest["manifest_sha256"] = stable_hash(dataset_manifest)
    data_quality["manifest_hash"] = dataset_manifest["manifest_sha256"]
    return {
        "dataset_id": dataset_id,
        "rows": labeled,
        "dataset_manifest": dataset_manifest,
        "feature_manifest": feature_manifest,
        "label_manifest": label_manifest,
        "chronology_report": chronology,
        "data_quality_report": data_quality,
        "dependency_report": dependency,
        "feature_parity_report": parity,
        "_adversarial_context": {
            "ea_rows": ea_rows,
            "main_role_bars": role_bars["main"],
            "relative_role_bars": role_bars["relative"],
            "strategy_parameters": deepcopy(parent["strategy_parameters"]),
            "parent": deepcopy(parent),
            "point_size": source["point_size"],
        },
    }


def write_dataset_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "source_row_id","signal_time","decision_time","symbol","period",
        *FEATURE_NAMES,
        "label","long_r","short_r","target_valid","target_reason","supervised_eligible",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: (
                    row[key].isoformat()
                    if isinstance(row.get(key), datetime)
                    else row.get(key)
                )
                for key in fields
            })
