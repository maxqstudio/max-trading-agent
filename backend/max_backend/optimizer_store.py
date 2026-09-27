from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH
from .db import connect
from .workflow_contract import OPTIMIZER_TERMINAL_QUALIFIED_POOL

ACTIVE_STATES = {
    "QUEUED",
    "COMPILING_EA",
    "PREPARING_MT5",
    "MT5_RUNNING",
    "MT5_COMPLETE",
    "MT5_COMPLETE_UNCONFIRMED",
    "WAITING_FOR_REPORT",
    "REPORT_READY",
    "PARSING_RESULTS",
    "ROUND_COMPLETE_NO_WINNER",
    "SCIENTIST_REQUESTING",
    "REGISTERING_CHALLENGER",
    "RESUMING",
}

TERMINAL_STATES = {
    OPTIMIZER_TERMINAL_QUALIFIED_POOL,
    "ELIGIBLE_WINNER_FOUND",
    "STRATEGY_CHALLENGER_FOUND",
    "CHALLENGER_REGISTRATION_FAILED",
    "NO_ELIGIBLE_WINNER_MAX_ROUNDS",
    "FAILED",
    "STOPPED",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def migrate_m01(path: Path = DATABASE_PATH) -> None:
    with connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS optimizer_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                active INTEGER NOT NULL CHECK (active IN (0,1)),
                created_utc TEXT NOT NULL,
                updated_utc TEXT NOT NULL,
                started_utc TEXT,
                stopped_utc TEXT,
                completed_utc TEXT,
                current_round INTEGER NOT NULL DEFAULT 0,
                max_rounds INTEGER NOT NULL,
                request_json TEXT NOT NULL,
                evidence_dir TEXT NOT NULL,
                worker_pid INTEGER,
                message TEXT NOT NULL DEFAULT '',
                first_blocker TEXT,
                terminal_result TEXT,
                winner_json TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS ux_optimizer_single_active
            ON optimizer_jobs(active)
            WHERE active = 1;

            CREATE TABLE IF NOT EXISTS optimizer_rounds (
                job_id TEXT NOT NULL,
                round_no INTEGER NOT NULL,
                phase TEXT NOT NULL,
                updated_utc TEXT NOT NULL,
                state_json TEXT NOT NULL,
                report_path TEXT,
                report_sha256 TEXT,
                sidecar_path TEXT,
                sidecar_sha256 TEXT,
                parsed_passes INTEGER,
                eligible_passes INTEGER,
                winner_pass INTEGER,
                PRIMARY KEY(job_id, round_no),
                FOREIGN KEY(job_id) REFERENCES optimizer_jobs(job_id)
            );
            """
        )
        current = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        current_version = int(current["value"]) if current is not None else 0
        if current_version < 2:
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version','2')"
            )


def migrate_m02(path: Path = DATABASE_PATH) -> None:
    """M02 uses existing optimizer_rounds.state_json for advisory checkpoints.

    No schema bump is required. Running this migration proves the accepted M01
    schema is present without rewriting historical optimizer job/round rows.
    """
    migrate_m01(path)
    with connect(path) as conn:
        schema = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    if schema is None or int(schema["value"]) < 2:
        raise RuntimeError("M02 requires optimizer schema version 2 or newer")
    if not {"optimizer_jobs", "optimizer_rounds"}.issubset(tables):
        raise RuntimeError("M02 requires accepted M01 optimizer tables")


def optimizer_database_status(path: Path = DATABASE_PATH) -> dict[str, Any]:
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
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        if schema is None or int(schema["value"]) < 2:
            return {
                "status": "FAIL",
                "reason": "SCHEMA_VERSION_MISMATCH",
                "schema_version": schema["value"] if schema else None,
            }
        if baseline is None or baseline["status"] != "BASELINE_NOT_CHAMPION":
            return {"status": "FAIL", "reason": "EA_BASELINE_AUTHORITY_INVALID"}
        required = {"optimizer_jobs", "optimizer_rounds"}
        if not required.issubset(tables):
            return {"status": "FAIL", "reason": "M01_TABLES_MISSING"}
        return {
            "status": "READY",
            "schema_version": int(schema["value"]),
            "baseline_status": baseline["status"],
        }
    except Exception as exc:
        return {
            "status": "FAIL",
            "reason": "DATABASE_UNAVAILABLE",
            "detail": str(exc),
        }


def _decode_job(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["active"] = bool(result["active"])
    result["request"] = json.loads(result.pop("request_json"))
    winner_json = result.pop("winner_json")
    result["winner"] = json.loads(winner_json) if winner_json else None
    return result


def _decode_round(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["state"] = json.loads(result.pop("state_json"))
    return result


def create_job(
    request: dict[str, Any],
    *,
    evidence_root: str | Path,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_m01(path)
    job_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_"
        + uuid.uuid4().hex[:8]
    )
    now = utc_now()
    evidence_dir = str(Path(evidence_root) / job_id)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            "SELECT job_id,status FROM optimizer_jobs WHERE active=1 LIMIT 1"
        ).fetchone()
        if active is not None:
            raise RuntimeError(
                f"Strategy Optimizer already active: {active['job_id']} ({active['status']})"
            )
        conn.execute(
            """
            INSERT INTO optimizer_jobs(
                job_id,status,active,created_utc,updated_utc,current_round,
                max_rounds,request_json,evidence_dir,message
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id,
                "QUEUED",
                1,
                now,
                now,
                0,
                int(request["max_rounds"]),
                json.dumps(request, sort_keys=True),
                evidence_dir,
                "Queued deterministic MT5 optimizer",
            ),
        )
    job = get_job(job_id, path=path)
    if job is None:
        raise RuntimeError("Optimizer job creation failed")
    return job


def get_job(job_id: str, *, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    migrate_m01(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM optimizer_jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
    return _decode_job(row)


def latest_job(*, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    migrate_m01(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM optimizer_jobs ORDER BY created_utc DESC LIMIT 1"
        ).fetchone()
    return _decode_job(row)


def active_job(*, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    migrate_m01(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM optimizer_jobs WHERE active=1 LIMIT 1"
        ).fetchone()
    return _decode_job(row)


def update_job(
    job_id: str,
    *,
    status: str | None = None,
    active: bool | None = None,
    current_round: int | None = None,
    worker_pid: int | None = None,
    message: str | None = None,
    first_blocker: str | None = None,
    terminal_result: str | None = None,
    winner: dict[str, Any] | None = None,
    mark_started: bool = False,
    mark_stopped: bool = False,
    mark_completed: bool = False,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    fields = ["updated_utc=?"]
    values: list[Any] = [utc_now()]
    if status is not None:
        fields.append("status=?")
        values.append(status)
    if active is not None:
        fields.append("active=?")
        values.append(1 if active else 0)
    if current_round is not None:
        fields.append("current_round=?")
        values.append(int(current_round))
    if worker_pid is not None:
        fields.append("worker_pid=?")
        values.append(int(worker_pid))
    if message is not None:
        fields.append("message=?")
        values.append(str(message))
    if first_blocker is not None:
        fields.append("first_blocker=?")
        values.append(str(first_blocker))
    if terminal_result is not None:
        fields.append("terminal_result=?")
        values.append(str(terminal_result))
    if winner is not None:
        fields.append("winner_json=?")
        values.append(json.dumps(winner, sort_keys=True))
    if mark_started:
        fields.append("started_utc=COALESCE(started_utc,?)")
        values.append(utc_now())
    if mark_stopped:
        fields.append("stopped_utc=?")
        values.append(utc_now())
    if mark_completed:
        fields.append("completed_utc=?")
        values.append(utc_now())
    values.append(job_id)

    with connect(path) as conn:
        cursor = conn.execute(
            f"UPDATE optimizer_jobs SET {', '.join(fields)} WHERE job_id=?",
            values,
        )
        if cursor.rowcount != 1:
            raise FileNotFoundError(job_id)
    job = get_job(job_id, path=path)
    if job is None:
        raise FileNotFoundError(job_id)
    return job


def upsert_round(
    job_id: str,
    round_no: int,
    *,
    phase: str,
    state: dict[str, Any],
    report_path: str | None = None,
    report_sha256: str | None = None,
    sidecar_path: str | None = None,
    sidecar_sha256: str | None = None,
    parsed_passes: int | None = None,
    eligible_passes: int | None = None,
    winner_pass: int | None = None,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    payload = dict(state)
    payload["phase"] = phase
    payload["round"] = int(round_no)
    payload["updated_utc"] = utc_now()
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO optimizer_rounds(
                job_id,round_no,phase,updated_utc,state_json,
                report_path,report_sha256,sidecar_path,sidecar_sha256,
                parsed_passes,eligible_passes,winner_pass
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(job_id,round_no) DO UPDATE SET
                phase=excluded.phase,
                updated_utc=excluded.updated_utc,
                state_json=excluded.state_json,
                report_path=COALESCE(excluded.report_path,optimizer_rounds.report_path),
                report_sha256=COALESCE(excluded.report_sha256,optimizer_rounds.report_sha256),
                sidecar_path=COALESCE(excluded.sidecar_path,optimizer_rounds.sidecar_path),
                sidecar_sha256=COALESCE(excluded.sidecar_sha256,optimizer_rounds.sidecar_sha256),
                parsed_passes=COALESCE(excluded.parsed_passes,optimizer_rounds.parsed_passes),
                eligible_passes=COALESCE(excluded.eligible_passes,optimizer_rounds.eligible_passes),
                winner_pass=COALESCE(excluded.winner_pass,optimizer_rounds.winner_pass)
            """,
            (
                job_id,
                int(round_no),
                phase,
                payload["updated_utc"],
                json.dumps(payload, sort_keys=True),
                report_path,
                report_sha256,
                sidecar_path,
                sidecar_sha256,
                parsed_passes,
                eligible_passes,
                winner_pass,
            ),
        )
    row = get_round(job_id, round_no, path=path)
    if row is None:
        raise RuntimeError("Optimizer round persistence failed")
    return row


def get_round(
    job_id: str,
    round_no: int,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    migrate_m01(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM optimizer_rounds WHERE job_id=? AND round_no=?",
            (job_id, int(round_no)),
        ).fetchone()
    return _decode_round(row) if row is not None else None


def get_rounds(job_id: str, *, path: Path = DATABASE_PATH) -> list[dict[str, Any]]:
    migrate_m01(path)
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM optimizer_rounds WHERE job_id=? ORDER BY round_no",
            (job_id,),
        ).fetchall()
    return [_decode_round(row) for row in rows]


def attach_rounds(job: dict[str, Any] | None, *, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    if job is None:
        return None
    result = dict(job)
    result["rounds"] = get_rounds(result["job_id"], path=path)
    return result
