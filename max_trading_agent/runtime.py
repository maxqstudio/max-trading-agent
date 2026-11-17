"""Persistent reference-only agent missions and observable structured events.

This milestone proves the event, tool-plan and NO_TRADE path. It does NOT invoke
an LLM, access an MT5 terminal, backtest a strategy or execute an order.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .core import RECIPES, build_index, load_catalog, plan, search

STATES = frozenset({"RUNNING", "COMPLETED", "FAILED"})
EVENT_STATES = frozenset({"RUNNING", "PASS", "BLOCKED", "SKIPPED", "FAILED", "INFO"})
ALLOWED_TASKS = frozenset(RECIPES)
DEFAULT_JOURNAL = Path(".local/agent_events.sqlite3")
DEFAULT_KNOWLEDGE = Path(".local/mt5_knowledge.sqlite3")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class EventJournal:
    """SQLite-backed append-only event journal with monotonic global cursors."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs ("
                "run_id TEXT PRIMARY KEY, task TEXT NOT NULL, mode TEXT NOT NULL, "
                "status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "event_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "run_id TEXT NOT NULL REFERENCES runs(run_id), "
                "role TEXT NOT NULL, event_type TEXT NOT NULL, "
                "status TEXT NOT NULL, message TEXT NOT NULL, "
                "details TEXT NOT NULL, occurred_at TEXT NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS ix_events_run_id ON events (run_id, event_id)")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def start(self, task: str, *, mode: str = "REFERENCE_ONLY") -> str:
        # A real-terminal observation may only originate from the opt-in CLI.
        if not ((mode == "REFERENCE_ONLY" and task in ALLOWED_TASKS)
                or (mode == "READ_ONLY" and task == "observe_mt5")
                or (mode == "SIGNAL" and task == "research_signal")
                or (mode == "BACKTEST" and task == "evaluate_historical")):
            raise ValueError("Unsupported task or mode")
        run_id = uuid.uuid4().hex
        with self._db() as db:
            db.execute(
                "INSERT INTO runs (run_id, task, mode, status, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, task, mode, "RUNNING", _utc_now()),
            )
        return run_id

    def append(self, run_id: str, role: str, event_type: str,
               status: str, message: str, details: dict | None = None) -> int:
        if status not in EVENT_STATES or not role or not event_type or not message:
            raise ValueError("Invalid journal event")
        payload = json.dumps(details or {}, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._db() as db:
            result = db.execute(
                "INSERT INTO events (run_id, role, event_type, status, message, details, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, role, event_type, status, message, payload, _utc_now()),
            )
            return int(result.lastrowid)

    def finish(self, run_id: str, status: str) -> None:
        if status not in STATES or status == "RUNNING":
            raise ValueError("Invalid final run status")
        with self._db() as db:
            row = db.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None or row[0] != "RUNNING":
                raise ValueError("Run does not exist or already finished")
            db.execute(
                "UPDATE runs SET status = ?, completed_at = ? WHERE run_id = ?",
                (status, _utc_now(), run_id),
            )

    def runs(self, limit: int = 25) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("Invalid limit")
        with self._db() as db:
            rows = db.execute(
                "SELECT run_id, task, mode, status, created_at, completed_at "
                "FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        keys = ("run_id", "task", "mode", "status", "created_at", "completed_at")
        return [dict(zip(keys, row)) for row in rows]

    def events(self, run_id: str, after: int = 0, limit: int = 200) -> list[dict]:
        if not isinstance(after, int) or after < 0 or not 1 <= limit <= 500:
            raise ValueError("Invalid event cursor")
        with self._db() as db:
            rows = db.execute(
                "SELECT event_id, run_id, role, event_type, status, message, details, occurred_at "
                "FROM events WHERE run_id = ? AND event_id > ? "
                "ORDER BY event_id ASC LIMIT ?",
                (run_id, after, limit),
            ).fetchall()
        keys = ("event_id", "run_id", "role", "event_type", "status", "message", "details", "occurred_at")
        result = []
        for row in rows:
            entry = dict(zip(keys, row))
            entry["details"] = json.loads(entry["details"])
            result.append(entry)
        return result


class ReferenceMission:
    """One observable, deliberately non-trading vertical slice of the runtime."""

    def __init__(self, journal: EventJournal, knowledge_db: Path):
        self.journal = journal
        self.knowledge_db = Path(knowledge_db)
        self._index_lock = threading.Lock()

    def execute(self, run_id: str, task: str) -> None:
        try:
            self.journal.append(
                run_id, "Supervisor", "MISSION_STARTED", "RUNNING",
                "Starting reference-only investigation. No LLM or MT5 connection is active.",
                {"task": task, "mode": "REFERENCE_ONLY", "real_market_data": False},
            )

            catalog = load_catalog()
            self.journal.append(
                run_id, "MT5 Specialist", "KNOWLEDGE_VALIDATED", "PASS",
                "Validated curated MT5 function catalog.",
                {"entries": len(catalog), "source": "knowledge/mt5_tools.json"},
            )

            workflow = plan(task)
            self.journal.append(
                run_id, "MT5 Specialist", "TOOL_PLAN", "PASS",
                "Resolved MT5 prerequisites from the tool registry; these are PLANNED, not called.",
                {"required_tools": [step["tool"] for step in workflow["steps"]],
                 "execution": "DISABLED"},
            )

            with self._index_lock:
                count = build_index(self.knowledge_db)
                matches = search(self.knowledge_db, "bid ask symbol account", limit=5)
            self.journal.append(
                run_id, "RAG Curator", "TOOL_RESULT", "PASS",
                "Built and queried actual local FTS5 MT5 knowledge index.",
                {"tools_executed": ["build_index", "search"],
                 "documents_indexed": count, "references": matches},
            )

            self.journal.append(
                run_id, "Market Analyst", "DATA_UNAVAILABLE", "BLOCKED",
                "MT5 terminal is not connected; no real quote, candle or account snapshot exists.",
                {"required": ["account_info", "symbol_info_tick", "copy_rates_from_pos"],
                 "missing": "live_market_and_broker_data"},
            )
            self.journal.append(
                run_id, "Bull / Bear Researchers", "DEBATE_SKIPPED", "SKIPPED",
                "No defensible directional debate without actual dated market evidence.",
                {"reason": "missing_market_snapshot", "model_invoked": False},
            )
            self.journal.append(
                run_id, "Evidence Lab", "BACKTEST_UNAVAILABLE", "BLOCKED",
                "No real dataset or reproducible strategy experiment is available.",
                {"tests_executed": 0, "research_claim": "UNKNOWN"},
            )
            self.journal.append(
                run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
                "NO_TRADE: market evidence is missing and execution is disabled.",
                {"action": "NO_TRADE",
                 "vetoes": ["NO_LIVE_MARKET_DATA", "NO_STRATEGY_EVIDENCE",
                            "REFERENCE_ONLY_MODE"],
                 "order_send_called": False},
            )
            self.journal.append(
                run_id, "Supervisor", "REPORT_READY", "PASS",
                "Mission recorded; evidence available for inspection.",
                {"decision": "NO_TRADE",
                 "report": "Reference investigation only. The tool registry was validated and "
                           "searched, but no LLM, broker, backtest or real-world "
                           "trading evidence was used. Trading remains disabled."},
            )
            self.journal.finish(run_id, "COMPLETED")
        except Exception as exc:
            # Error output is bounded; only exception class is persisted to avoid leaking secrets.
            self.journal.append(
                run_id, "Supervisor", "MISSION_FAILED", "FAILED",
                "Reference mission failed; trading remains disabled.",
                {"error_class": type(exc).__name__},
            )
            self.journal.finish(run_id, "FAILED")
            raise
