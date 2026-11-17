"""Read-only MT5 reference catalog, local full-text search and task planner.

M00 deliberately has no MT5 package import and no broker execution path.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "knowledge" / "mt5_tools.json"
RECIPES = {
    "analyze_symbol": [
        "initialize",
        "terminal_info",
        "account_info",
        "symbol_select",
        "symbol_info",
        "symbol_info_tick",
        "copy_rates_from_pos",
        "positions_get",
        "orders_get"
    ],
    "prepare_entry": [
        "initialize",
        "terminal_info",
        "account_info",
        "symbol_select",
        "symbol_info",
        "symbol_info_tick",
        "copy_rates_from_pos",
        "positions_get",
        "orders_get",
        "order_calc_margin",
        "order_calc_profit",
        "order_check"
    ],
    "inspect_positions": [
        "initialize",
        "account_info",
        "positions_get",
        "orders_get"
    ],
    "diagnose_connection": [
        "initialize",
        "last_error",
        "terminal_info"
    ]
}
ALLOWED_EFFECTS = {"read_only", "local_mutation", "calculation", "broker_preflight", "trade_mutation"}
APPROVED_FOR_PLAN = ALLOWED_EFFECTS - {"trade_mutation"}


def load_catalog(path: Path = CATALOG) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Catalog must be a JSON array")
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Catalog entries must be objects")
        for name in ("id", "title", "summary", "effect", "source_url", "tags"):
            if name not in item:
                raise ValueError(f"Missing {name}")
        key = item["id"]
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise ValueError("Invalid tool id")
        if key in seen:
            raise ValueError(f"Duplicate tool id: {key}")
        seen.add(key)
        if item["effect"] not in ALLOWED_EFFECTS:
            raise ValueError(f"Unknown effect: {key}")
        if not str(item["source_url"]).startswith("https://www.mql5.com/en/docs/python_metatrader5/"):
            raise ValueError(f"Unsupported source URL: {key}")
        if not isinstance(item["tags"], list):
            raise ValueError(f"Invalid tags: {key}")
    missing = set(tool for steps in RECIPES.values() for tool in steps) - seen
    if missing:
        raise ValueError(f"Unknown recipe dependencies: {sorted(missing)}")
    return data


def plan(task: str) -> dict:
    """Plans are read-only descriptions, never actual MT5 invocations."""
    if task not in RECIPES:
        raise ValueError(f"Unknown task: {task}")
    index = {item["id"]: item for item in load_catalog()}
    steps = []
    for tool_id in RECIPES[task]:
        tool = index[tool_id]
        if tool["effect"] not in APPROVED_FOR_PLAN:
            raise ValueError(f"Unsafe recipe tool: {tool_id}")
        steps.append({"tool": tool_id, "purpose": tool["summary"], "effect": tool["effect"]})
    return {"task": task, "execution": "DISABLED", "mode": "REFERENCE_ONLY", "steps": steps}


def build_index(database: Path, catalog: Path = CATALOG) -> int:
    entries = load_catalog(catalog)
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS mt5_docs USING fts5(id UNINDEXED, title, summary, tags, source_url UNINDEXED)")
        connection.execute("DELETE FROM mt5_docs")
        connection.executemany(
            "INSERT INTO mt5_docs (id, title, summary, tags, source_url) VALUES (?, ?, ?, ?, ?)",
            [(x["id"], x["title"], x["summary"], " ".join(x["tags"]), x["source_url"]) for x in entries],
        )
    return len(entries)


def search(database: Path, query: str, limit: int = 10) -> list[dict]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    terms = re.findall(r"[a-zA-Z0-9_]+", query)
    if not terms:
        return []
    safe_query = " OR ".join('"' + t + '"' for t in terms[:15])
    with closing(sqlite3.connect(database)) as connection, connection:
        rows = connection.execute(
            "SELECT id, title, summary, source_url FROM mt5_docs WHERE mt5_docs MATCH ? ORDER BY rank LIMIT ?",
            (safe_query, limit),
        ).fetchall()
    return [{"id": x[0], "title": x[1], "summary": x[2], "source_url": x[3]} for x in rows]
