from __future__ import annotations

import json
import math
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH
from .db import connect
from .optimizer_core import (
    ABSOLUTE_BOUNDS,
    DEFAULT_KPI,
    DEFAULT_OPTIMIZER_TRADE_EXPONENT_ALPHA,
    DEFAULT_SPACE,
)
from .optimizer_store import utc_now

MAX_DRAFT_BYTES = 128 * 1024
DRAFT_FIELDS = {
    "symbol",
    "relative_symbol",
    "period",
    "from_date",
    "to_date",
    "model",
    "optimization",
    "max_rounds",
    "deposit",
    "leverage",
    "optimizer_trade_exponent_alpha",
    "optimize_params",
    "search_space",
    "kpi",
    "scientist_assist",
}
KPI_FIELDS = {
    "min_profit_factor",
    "min_recovery_factor",
    "min_expectancy_r",
    "min_weighted_r",
    "base_h1_trades_per_month",
}


def default_optimizer_draft() -> dict[str, Any]:
    return {
        "symbol": "",
        "relative_symbol": "",
        "period": "H1",
        "from_date": "2021.01.01",
        "to_date": "2024.12.31",
        "model": 1,
        "optimization": 2,
        "max_rounds": 3,
        "deposit": 10000.0,
        "leverage": 100,
        "optimizer_trade_exponent_alpha": DEFAULT_OPTIMIZER_TRADE_EXPONENT_ALPHA,
        "optimize_params": list(ABSOLUTE_BOUNDS),
        "search_space": deepcopy(DEFAULT_SPACE),
        "kpi": {key: DEFAULT_KPI[key] for key in KPI_FIELDS},
        "scientist_assist": False,
    }


def _validate_draft(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != DRAFT_FIELDS:
        raise ValueError("OPTIMIZER_DRAFT_SHAPE_INVALID")
    draft = deepcopy(value)
    for key in ("symbol", "relative_symbol", "period", "from_date", "to_date"):
        if not isinstance(draft[key], str) or len(draft[key]) > 128:
            raise ValueError(f"OPTIMIZER_DRAFT_FIELD_INVALID:{key}")
    for key in ("model", "optimization", "max_rounds", "leverage"):
        raw = draft[key]
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError(f"OPTIMIZER_DRAFT_FIELD_INVALID:{key}")
    if isinstance(draft["deposit"], bool) or not isinstance(draft["deposit"], (int, float)):
        raise ValueError("OPTIMIZER_DRAFT_FIELD_INVALID:deposit")
    if isinstance(draft["optimizer_trade_exponent_alpha"], bool) or not isinstance(
        draft["optimizer_trade_exponent_alpha"], (int, float)
    ):
        raise ValueError("OPTIMIZER_DRAFT_FIELD_INVALID:optimizer_trade_exponent_alpha")
    for key in ("deposit", "optimizer_trade_exponent_alpha"):
        if not math.isfinite(float(draft[key])):
            raise ValueError(f"OPTIMIZER_DRAFT_FIELD_NONFINITE:{key}")
    if not isinstance(draft["scientist_assist"], bool):
        raise ValueError("OPTIMIZER_DRAFT_FIELD_INVALID:scientist_assist")

    names = set(ABSOLUTE_BOUNDS)
    enabled = draft["optimize_params"]
    if (
        not isinstance(enabled, list)
        or any(not isinstance(name, str) or name not in names for name in enabled)
        or len(enabled) != len(set(enabled))
    ):
        raise ValueError("OPTIMIZER_DRAFT_ENABLED_PARAMETERS_INVALID")
    ranges = draft["search_space"]
    if not isinstance(ranges, dict) or set(ranges) != names:
        raise ValueError("OPTIMIZER_DRAFT_PARAMETER_RANGES_INVALID")
    for name, spec in ranges.items():
        if not isinstance(spec, dict) or set(spec) != {"start", "step", "stop"}:
            raise ValueError(f"OPTIMIZER_DRAFT_RANGE_INVALID:{name}")
        for field, number in spec.items():
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise ValueError(f"OPTIMIZER_DRAFT_RANGE_INVALID:{name}:{field}")
            if not math.isfinite(float(number)):
                raise ValueError(f"OPTIMIZER_DRAFT_RANGE_NONFINITE:{name}:{field}")

    kpi = draft["kpi"]
    if not isinstance(kpi, dict) or set(kpi) != KPI_FIELDS:
        raise ValueError("OPTIMIZER_DRAFT_KPI_INVALID")
    for key, number in kpi.items():
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError(f"OPTIMIZER_DRAFT_KPI_INVALID:{key}")
        if not math.isfinite(float(number)):
            raise ValueError(f"OPTIMIZER_DRAFT_KPI_NONFINITE:{key}")

    encoded = json.dumps(draft, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_DRAFT_BYTES:
        raise ValueError("OPTIMIZER_DRAFT_TOO_LARGE")
    return draft


def _decode(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {
            "status": "READY",
            "reason": None,
            "revision": 0,
            "updated_utc": None,
            "draft": default_optimizer_draft(),
        }
    revision = int(row["revision"])
    try:
        raw = json.loads(str(row["draft_json"]))
        draft = _validate_draft(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {
            "status": "RECOVERY_REQUIRED",
            "reason": "DRAFT_CORRUPT",
            "revision": revision,
            "updated_utc": str(row["updated_utc"]),
            "draft": default_optimizer_draft(),
        }
    return {
        "status": "READY",
        "reason": None,
        "revision": revision,
        "updated_utc": str(row["updated_utc"]),
        "draft": draft,
    }


def get_optimizer_draft(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT revision,draft_json,updated_utc FROM optimizer_drafts WHERE draft_id=1"
        ).fetchone()
    return _decode(row)


def put_optimizer_draft(
    raw_draft: Any,
    *,
    revision: int,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    draft = _validate_draft(raw_draft)
    if isinstance(revision, bool) or int(revision) < 1:
        raise ValueError("OPTIMIZER_DRAFT_REVISION_INVALID")
    payload = json.dumps(draft, sort_keys=True, separators=(",", ":"), allow_nan=False)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT revision,draft_json,updated_utc FROM optimizer_drafts WHERE draft_id=1"
        ).fetchone()
        current_revision = int(current["revision"]) if current is not None else 0
        if int(revision) <= current_revision:
            result = _decode(current)
            result["status"] = "STALE_WRITE_IGNORED"
            return result
        updated = utc_now()
        conn.execute(
            """
            INSERT INTO optimizer_drafts(draft_id,revision,draft_json,updated_utc)
            VALUES(1,?,?,?)
            ON CONFLICT(draft_id) DO UPDATE SET
                revision=excluded.revision,
                draft_json=excluded.draft_json,
                updated_utc=excluded.updated_utc
            """,
            (int(revision), payload, updated),
        )
    return {
        "status": "READY",
        "reason": None,
        "revision": int(revision),
        "updated_utc": updated,
        "draft": draft,
    }
