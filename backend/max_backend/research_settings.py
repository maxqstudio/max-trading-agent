from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH
from .db import connect, initialize_database
from .optimizer_store import utc_now

_CONFIG_KEY = "research_sample_configuration_v1"


def get_research_sample_configuration(
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    initialize_database(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key=?",
            (_CONFIG_KEY,),
        ).fetchone()
    if row is None:
        return {
            "configured": False,
            "h1_minimum_trades_per_month": None,
            "updated_utc": None,
        }
    try:
        payload = json.loads(str(row["value"]))
    except Exception as exc:
        raise RuntimeError("RESEARCH_SAMPLE_CONFIGURATION_CORRUPT") from exc
    value = payload.get("h1_minimum_trades_per_month")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuntimeError("RESEARCH_SAMPLE_CONFIGURATION_INVALID")
    return {
        "configured": True,
        "h1_minimum_trades_per_month": int(value),
        "updated_utc": str(payload.get("updated_utc") or ""),
    }


def set_research_sample_configuration(
    value: int,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("RESEARCH_H1_SAMPLE_CONFIGURATION_INVALID")
    initialize_database(path)
    payload = {
        "h1_minimum_trades_per_month": int(value),
        "updated_utc": utc_now(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with connect(path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES(?,?)",
            (_CONFIG_KEY, encoded),
        )
    return get_research_sample_configuration(path=path)


def require_research_sample_configuration(
    *,
    path: Path = DATABASE_PATH,
) -> int:
    config = get_research_sample_configuration(path=path)
    value = config["h1_minimum_trades_per_month"]
    if value is None:
        raise RuntimeError("RESEARCH_H1_SAMPLE_CONFIGURATION_REQUIRED")
    return int(value)
