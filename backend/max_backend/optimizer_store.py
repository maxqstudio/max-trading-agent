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
    "STARTING",
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
                winner_json TEXT,
                launch_token TEXT,
                worker_identity_json TEXT
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
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(optimizer_jobs)").fetchall()
        }
        if "launch_token" not in columns:
            conn.execute("ALTER TABLE optimizer_jobs ADD COLUMN launch_token TEXT")
        if "worker_identity_json" not in columns:
            conn.execute(
                "ALTER TABLE optimizer_jobs ADD COLUMN worker_identity_json TEXT"
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
    identity_json = result.pop("worker_identity_json", None)
    result["worker_identity"] = json.loads(identity_json) if identity_json else None
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
        uncertain = conn.execute(
            """
            SELECT job_id,status FROM optimizer_jobs
            WHERE status IN (
                'EXECUTION_UNCERTAIN','RECONCILIATION_REQUIRED',
                'INTERRUPTED_SAFE_TO_RESUME','WAITING_FOR_REPORT',
                'MT5_COMPLETE_UNCONFIRMED','CHALLENGER_REGISTRATION_FAILED'
            )
            ORDER BY updated_utc DESC LIMIT 1
            """
        ).fetchone()
        if uncertain is not None:
            raise RuntimeError(
                "Strategy Optimizer previous execution requires resume or explicit stop: "
                f"{uncertain['job_id']} ({uncertain['status']})"
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
    launch_token: str | None = None,
    worker_identity: dict[str, Any] | None = None,
    clear_worker_identity: bool = False,
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
    if launch_token is not None:
        fields.append("launch_token=?")
        values.append(str(launch_token))
    if worker_identity is not None:
        fields.append("worker_identity_json=?")
        values.append(json.dumps(worker_identity, sort_keys=True))
    elif clear_worker_identity:
        fields.append("worker_identity_json=NULL")
        fields.append("worker_pid=NULL")
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


def claim_initial_worker_launch(
    job_id: str,
    launch_token: str,
    *,
    path: Path = DATABASE_PATH,
) -> None:
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status,active,launch_token FROM optimizer_jobs WHERE job_id=?",
            (str(job_id),),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(job_id)
        if (
            str(row["status"]) != "QUEUED"
            or not bool(row["active"])
            or row["launch_token"] is not None
        ):
            raise RuntimeError("OPTIMIZER_INITIAL_WORKER_LAUNCH_ALREADY_CLAIMED")
        conn.execute(
            """
            UPDATE optimizer_jobs
            SET status='STARTING',launch_token=?,worker_pid=NULL,
                worker_identity_json=NULL,updated_utc=?
            WHERE job_id=?
            """,
            (str(launch_token), utc_now(), str(job_id)),
        )


def claim_resume_worker_launch(
    job_id: str,
    launch_token: str,
    *,
    allowed_statuses: set[str],
    path: Path = DATABASE_PATH,
) -> None:
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status,active FROM optimizer_jobs WHERE job_id=?",
            (str(job_id),),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(job_id)
        if bool(row["active"]):
            raise RuntimeError("OPTIMIZER_WORKER_LAUNCH_ALREADY_ACTIVE")
        if str(row["status"]) not in allowed_statuses:
            raise RuntimeError(f"Optimizer job is not resumable: {row['status']}")
        conn.execute(
            """
            UPDATE optimizer_jobs
            SET status='RESUMING',active=1,launch_token=?,worker_pid=NULL,
                worker_identity_json=NULL,message=?,updated_utc=?
            WHERE job_id=? AND active=0
            """,
            (
                str(launch_token),
                "Recovery planner claimed one bounded worker launch",
                utc_now(),
                str(job_id),
            ),
        )


def confirm_worker_launch(
    job_id: str,
    launch_token: str,
    *,
    worker_pid: int,
    worker_identity: dict[str, Any],
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE optimizer_jobs
            SET worker_pid=?,worker_identity_json=?,updated_utc=?
            WHERE job_id=? AND launch_token=? AND active=1
              AND status IN ('STARTING','RESUMING')
            """,
            (
                int(worker_pid),
                json.dumps(worker_identity, sort_keys=True),
                utc_now(),
                str(job_id),
                str(launch_token),
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("OPTIMIZER_WORKER_LAUNCH_CLAIM_LOST")
        row = conn.execute(
            "SELECT * FROM optimizer_jobs WHERE job_id=?",
            (str(job_id),),
        ).fetchone()
    result = _decode_job(row)
    if result is None:
        raise FileNotFoundError(job_id)
    return result


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
    candidate_projection: dict[str, Any] | None = None,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    payload = dict(state)
    payload["phase"] = phase
    payload["round"] = int(round_no)
    payload["updated_utc"] = utc_now()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
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
        if candidate_projection is not None:
            _persist_candidate_projection(
                conn,
                job_id=job_id,
                round_no=int(round_no),
                projection=candidate_projection,
            )
    row = get_round(job_id, round_no, path=path)
    if row is None:
        raise RuntimeError("Optimizer round persistence failed")
    return row


def _persist_candidate_projection(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    round_no: int,
    projection: dict[str, Any],
) -> None:
    existing = conn.execute(
        """
        SELECT source_report_sha256,source_sidecar_sha256,projection_sha256,candidate_count
        FROM optimizer_candidate_projection_rounds
        WHERE job_id=? AND round_no=?
        """,
        (job_id, int(round_no)),
    ).fetchone()
    rows = projection.get("candidates")
    if not isinstance(rows, list):
        raise ValueError("OPTIMIZER_CANDIDATE_PROJECTION_INVALID")
    identity = (
        str(projection.get("report_sha256") or ""),
        str(projection.get("sidecar_sha256") or ""),
        str(projection.get("projection_sha256") or ""),
        len(rows),
    )
    if existing is not None:
        current = (
            str(existing["source_report_sha256"]),
            str(existing["source_sidecar_sha256"]),
            str(existing["projection_sha256"]),
            int(existing["candidate_count"]),
        )
        if current != identity:
            raise RuntimeError("OPTIMIZER_CANDIDATE_PROJECTION_REPLAY_MISMATCH")
        return

    conn.execute(
        """
        INSERT INTO optimizer_candidate_projection_rounds(
            job_id,round_no,source_report_sha256,source_sidecar_sha256,
            projection_sha256,candidate_count,projected_utc
        ) VALUES(?,?,?,?,?,?,?)
        """,
        (
            job_id,
            int(round_no),
            identity[0],
            identity[1],
            identity[2],
            identity[3],
            utc_now(),
        ),
    )
    conn.executemany(
        """
        INSERT INTO optimizer_candidate_projection(
            job_id,round_no,pass_no,rank_value,mean_r,custom_fitness,weighted_r,
            profit_factor,recovery_factor,trades,required_trades,search_text,candidate_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                job_id,
                int(round_no),
                int(item["pass"]),
                0,
                float(item["mean_r"]),
                (
                    float(item["custom_fitness"])
                    if item.get("custom_fitness") is not None else None
                ),
                float(item["weighted_r"]),
                float(item["profit_factor"]),
                float(item["recovery_factor"]),
                int(item["trades"]),
                int(item["required_trades"]),
                str(item["search_text"]),
                json.dumps(item["candidate"], sort_keys=True, separators=(",", ":")),
            )
            for item in rows
        ],
    )
    conn.execute(
        """
        WITH ranked AS (
            SELECT job_id,round_no,pass_no,
                ROW_NUMBER() OVER (
                    ORDER BY mean_r DESC,weighted_r DESC,profit_factor DESC,
                        recovery_factor DESC,round_no ASC,pass_no ASC
                ) AS value
            FROM optimizer_candidate_projection
            WHERE job_id=?
        )
        UPDATE optimizer_candidate_projection
        SET rank_value=(
            SELECT value FROM ranked
            WHERE ranked.job_id=optimizer_candidate_projection.job_id
              AND ranked.round_no=optimizer_candidate_projection.round_no
              AND ranked.pass_no=optimizer_candidate_projection.pass_no
        )
        WHERE job_id=?
        """,
        (job_id, job_id),
    )


def persist_candidate_projection(
    job_id: str,
    round_no: int,
    projection: dict[str, Any],
    *,
    path: Path = DATABASE_PATH,
) -> None:
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _persist_candidate_projection(
            conn,
            job_id=job_id,
            round_no=int(round_no),
            projection=projection,
        )


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
