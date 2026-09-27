from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH, STRATEGY_CONTRACT
from .ea import verify_baseline_snapshot

SCHEMA_VERSION = 1
MIN_SUPPORTED_SCHEMA = 1


def connect(path: Path = DATABASE_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def initialize_database(path: Path = DATABASE_PATH) -> None:
    with connect(path) as conn:
        schema_meta_preexisting = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='schema_meta'"
        ).fetchone() is not None
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ea_baseline (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                ea_version TEXT NOT NULL,
                source_project TEXT NOT NULL,
                source_candidate_build_id TEXT NOT NULL,
                source_tree_signature TEXT NOT NULL,
                source_path TEXT NOT NULL,
                snapshot_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status = 'BASELINE_NOT_CHAMPION'),
                imported_at_utc TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_meta(key,value) VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )
        if not schema_meta_preexisting:
            manifest = verify_baseline_snapshot()
            conn.execute(
                "INSERT OR IGNORE INTO schema_meta(key,value) "
                "VALUES('strategy_epoch',?)",
                (STRATEGY_CONTRACT,),
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_meta(key,value) "
                "VALUES('strategy_epoch_baseline_sha256',?)",
                (str(manifest["snapshot_sha256"]),),
            )


def ensure_baseline_registered(path: Path = DATABASE_PATH) -> dict[str, Any]:
    manifest = verify_baseline_snapshot()
    initialize_database(path)
    values = (
        manifest["ea_version"],
        manifest["source_project"],
        manifest["source_candidate_build_id"],
        manifest["source_tree_signature"],
        manifest["source_path"],
        manifest["snapshot_relative_path"],
        manifest["snapshot_sha256"],
        "BASELINE_NOT_CHAMPION",
        manifest["imported_at_utc"],
    )
    with connect(path) as conn:
        current = conn.execute("SELECT * FROM ea_baseline WHERE id=1").fetchone()
        if current is not None and current["sha256"] != manifest["snapshot_sha256"]:
            raise RuntimeError("Registered EA baseline differs; explicit re-registration required")
        if current is None:
            conn.execute(
                """
                INSERT INTO ea_baseline(
                    id,ea_version,source_project,source_candidate_build_id,
                    source_tree_signature,source_path,snapshot_path,sha256,
                    status,imported_at_utc
                ) VALUES(1,?,?,?,?,?,?,?,?,?)
                """,
                values,
            )
        row = conn.execute("SELECT * FROM ea_baseline WHERE id=1").fetchone()
        return dict(row)


def read_baseline(path: Path = DATABASE_PATH) -> dict[str, Any]:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM ea_baseline WHERE id=1").fetchone()
    if row is None:
        raise RuntimeError("EA baseline row missing")
    return dict(row)


def database_status(path: Path = DATABASE_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "FAIL", "reason": "DATABASE_FILE_MISSING"}
    try:
        with connect(path) as conn:
            schema = conn.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
            baseline = conn.execute(
                "SELECT status FROM ea_baseline WHERE id=1"
            ).fetchone()
        if schema is None:
            return {"status": "FAIL", "reason": "SCHEMA_VERSION_MISSING"}
        actual_schema = int(schema["value"])
        if actual_schema < MIN_SUPPORTED_SCHEMA:
            return {
                "status": "FAIL",
                "reason": "SCHEMA_VERSION_MISMATCH",
                "schema_version": actual_schema,
            }
        if baseline is None:
            return {"status": "FAIL", "reason": "EA_BASELINE_ROW_MISSING"}
        if baseline["status"] != "BASELINE_NOT_CHAMPION":
            return {
                "status": "FAIL",
                "reason": "EA_BASELINE_STATUS_INVALID",
                "baseline_status": baseline["status"],
            }
        return {
            "status": "READY",
            "schema_version": actual_schema,
            "baseline_status": baseline["status"],
        }
    except Exception as exc:
        return {
            "status": "FAIL",
            "reason": "DATABASE_UNAVAILABLE",
            "detail": str(exc),
        }
