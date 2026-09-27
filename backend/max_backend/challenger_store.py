from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH
from .db import connect
from .optimizer_store import migrate_m02, utc_now

SCHEMA_VERSION = 3
MIN_SUPPORTED_SCHEMA = 3
VISIBLE_STATUS = "CHALLENGER"
REGISTERING_STATUS = "REGISTERING"


def migrate_m03(path: Path = DATABASE_PATH) -> None:
    migrate_m02(path)
    with connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategy_challengers (
                challenger_id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK (status IN ('REGISTERING','CHALLENGER')),
                role_origin TEXT NOT NULL CHECK (role_origin = 'OPTIMIZER_WINNER'),

                source_job_id TEXT NOT NULL,
                source_round INTEGER NOT NULL,
                source_pass INTEGER NOT NULL,

                created_utc TEXT NOT NULL,
                updated_utc TEXT NOT NULL,

                ea_version TEXT NOT NULL,
                baseline_ea_sha256 TEXT NOT NULL,
                challenger_ea_sha256 TEXT,
                set_sha256 TEXT,
                metadata_sha256 TEXT,
                manifest_sha256 TEXT,

                bundle_path TEXT NOT NULL,

                params_json TEXT NOT NULL,
                kpi_json TEXT NOT NULL,
                hard_gates_json TEXT NOT NULL,
                source_request_json TEXT NOT NULL,
                winner_json TEXT NOT NULL,
                provenance_json TEXT NOT NULL,

                winning_xml_sha256 TEXT NOT NULL,
                winning_sidecar_sha256 TEXT NOT NULL,

                champion_mutation TEXT NOT NULL DEFAULT 'NONE'
                    CHECK (champion_mutation = 'NONE'),
                registration_error TEXT,

                UNIQUE(source_job_id, source_round, source_pass),
                FOREIGN KEY(source_job_id) REFERENCES optimizer_jobs(job_id)
            );

            CREATE INDEX IF NOT EXISTS ix_strategy_challengers_created
            ON strategy_challengers(created_utc, challenger_id);
            """
        )
        current = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        current_version = int(current["value"]) if current is not None else 0
        if current_version < SCHEMA_VERSION:
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) "
                "VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )


def challenger_database_status(path: Path = DATABASE_PATH) -> dict[str, Any]:
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
        actual_schema = int(schema["value"]) if schema is not None else None
        if actual_schema is None or actual_schema < MIN_SUPPORTED_SCHEMA:
            return {
                "status": "FAIL",
                "reason": "SCHEMA_VERSION_MISMATCH",
                "schema_version": actual_schema,
            }
        required = {
            "ea_baseline",
            "optimizer_jobs",
            "optimizer_rounds",
            "strategy_challengers",
        }
        if not required.issubset(tables):
            return {"status": "FAIL", "reason": "M03_TABLES_MISSING"}
        if baseline is None or baseline["status"] != "BASELINE_NOT_CHAMPION":
            return {"status": "FAIL", "reason": "EA_BASELINE_AUTHORITY_INVALID"}
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


_JSON_COLUMNS = (
    "params_json",
    "kpi_json",
    "hard_gates_json",
    "source_request_json",
    "winner_json",
    "provenance_json",
)


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in _JSON_COLUMNS:
        raw = result.pop(key)
        result[key.removesuffix("_json")] = json.loads(raw)
    result["source_round"] = int(result["source_round"])
    result["source_pass"] = int(result["source_pass"])
    return result


def get_challenger(
    challenger_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    migrate_m03(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM strategy_challengers WHERE challenger_id=?",
            (str(challenger_id),),
        ).fetchone()
    return _decode(row)


def get_challenger_by_source(
    source_job_id: str,
    source_round: int,
    source_pass: int,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    migrate_m03(path)
    with connect(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM strategy_challengers
            WHERE source_job_id=? AND source_round=? AND source_pass=?
            """,
            (str(source_job_id), int(source_round), int(source_pass)),
        ).fetchone()
    return _decode(row)


def consumed_source_identities(
    source_job_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> set[tuple[int, int]]:
    migrate_m03(path)
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT source_round,source_pass
            FROM strategy_challengers
            WHERE source_job_id=?
            """,
            (str(source_job_id),),
        ).fetchall()
        consumed = {
            (int(row["source_round"]), int(row["source_pass"]))
            for row in rows
        }
        batch_tables = {
            str(row["name"])
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table'
                  AND name IN (
                    'strategy_challenger_batches',
                    'strategy_challenger_batch_items'
                  )
                """
            ).fetchall()
        }
        if batch_tables == {
            "strategy_challenger_batches",
            "strategy_challenger_batch_items",
        }:
            batch_rows = conn.execute(
                """
                SELECT i.source_round,i.source_pass
                FROM strategy_challenger_batch_items i
                JOIN strategy_challenger_batches b
                  ON b.batch_id=i.batch_id
                WHERE b.job_id=? AND b.state='COMMITTED'
                """,
                (str(source_job_id),),
            ).fetchall()
            consumed.update(
                (int(row["source_round"]), int(row["source_pass"]))
                for row in batch_rows
            )
    return consumed


def challenger_id_exists(
    challenger_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> bool:
    migrate_m03(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT 1 FROM strategy_challengers WHERE challenger_id=?",
            (str(challenger_id),),
        ).fetchone()
    return row is not None


def list_challengers(
    *,
    include_registering: bool = False,
    path: Path = DATABASE_PATH,
) -> list[dict[str, Any]]:
    migrate_m03(path)
    query = "SELECT * FROM strategy_challengers"
    params: tuple[Any, ...] = ()
    if not include_registering:
        query += " WHERE status=?"
        params = (VISIBLE_STATUS,)
    query += " ORDER BY created_utc DESC, challenger_id DESC"
    with connect(path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_decode(row) for row in rows if row is not None]


def reserve_challenger(
    payload: dict[str, Any],
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_m03(path)
    source = (
        str(payload["source_job_id"]),
        int(payload["source_round"]),
        int(payload["source_pass"]),
    )
    now = str(payload.get("created_utc") or utc_now())
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """
            SELECT * FROM strategy_challengers
            WHERE source_job_id=? AND source_round=? AND source_pass=?
            """,
            source,
        ).fetchone()
        if existing is not None:
            return _decode(existing)  # type: ignore[arg-type]

        collision = conn.execute(
            "SELECT source_job_id,source_round,source_pass "
            "FROM strategy_challengers WHERE challenger_id=?",
            (str(payload["challenger_id"]),),
        ).fetchone()
        if collision is not None:
            raise RuntimeError("CHALLENGER_ID_COLLISION")

        conn.execute(
            """
            INSERT INTO strategy_challengers(
                challenger_id,status,role_origin,
                source_job_id,source_round,source_pass,
                created_utc,updated_utc,
                ea_version,baseline_ea_sha256,
                challenger_ea_sha256,set_sha256,metadata_sha256,manifest_sha256,
                bundle_path,
                params_json,kpi_json,hard_gates_json,source_request_json,
                winner_json,provenance_json,
                winning_xml_sha256,winning_sidecar_sha256,
                champion_mutation,registration_error
            ) VALUES(
                ?,'REGISTERING','OPTIMIZER_WINNER',
                ?,?,?,?,?,
                ?,?,
                NULL,NULL,NULL,NULL,
                ?,
                ?,?,?,?,?,?,
                ?,?,
                'NONE',NULL
            )
            """,
            (
                str(payload["challenger_id"]),
                source[0],
                source[1],
                source[2],
                now,
                now,
                str(payload["ea_version"]),
                str(payload["baseline_ea_sha256"]),
                str(payload["bundle_path"]),
                json.dumps(payload["params"], sort_keys=True),
                json.dumps(payload["kpi"], sort_keys=True),
                json.dumps(payload["hard_gates"], sort_keys=True),
                json.dumps(payload["source_request"], sort_keys=True),
                json.dumps(payload["winner"], sort_keys=True),
                json.dumps(payload["provenance"], sort_keys=True),
                str(payload["winning_xml_sha256"]),
                str(payload["winning_sidecar_sha256"]),
            ),
        )
        row = conn.execute(
            "SELECT * FROM strategy_challengers WHERE challenger_id=?",
            (str(payload["challenger_id"]),),
        ).fetchone()
    decoded = _decode(row)
    if decoded is None:
        raise RuntimeError("Challenger reservation failed")
    return decoded


def finalize_challenger(
    challenger_id: str,
    *,
    challenger_ea_sha256: str,
    set_sha256: str,
    metadata_sha256: str,
    manifest_sha256: str,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_m03(path)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM strategy_challengers WHERE challenger_id=?",
            (str(challenger_id),),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(challenger_id)
        if row["status"] == VISIBLE_STATUS:
            decoded = _decode(row)
            if decoded is None:
                raise RuntimeError("Challenger decode failed")
            expected = {
                "challenger_ea_sha256": challenger_ea_sha256,
                "set_sha256": set_sha256,
                "metadata_sha256": metadata_sha256,
                "manifest_sha256": manifest_sha256,
            }
            for key, value in expected.items():
                if str(decoded.get(key) or "") != str(value):
                    raise RuntimeError("IMMUTABLE_CHALLENGER_HASH_MISMATCH")
            return decoded

        conn.execute(
            """
            UPDATE strategy_challengers SET
                status='CHALLENGER',
                updated_utc=?,
                challenger_ea_sha256=?,
                set_sha256=?,
                metadata_sha256=?,
                manifest_sha256=?,
                registration_error=NULL
            WHERE challenger_id=? AND status='REGISTERING'
            """,
            (
                utc_now(),
                str(challenger_ea_sha256),
                str(set_sha256),
                str(metadata_sha256),
                str(manifest_sha256),
                str(challenger_id),
            ),
        )
        row = conn.execute(
            "SELECT * FROM strategy_challengers WHERE challenger_id=?",
            (str(challenger_id),),
        ).fetchone()
    decoded = _decode(row)
    if decoded is None or decoded["status"] != VISIBLE_STATUS:
        raise RuntimeError("Challenger finalization failed")
    return decoded


def mark_registration_error(
    challenger_id: str,
    error: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_m03(path)
    with connect(path) as conn:
        cursor = conn.execute(
            """
            UPDATE strategy_challengers
            SET updated_utc=?, registration_error=?
            WHERE challenger_id=? AND status='REGISTERING'
            """,
            (utc_now(), str(error)[:1000], str(challenger_id)),
        )
        if cursor.rowcount != 1:
            row = conn.execute(
                "SELECT * FROM strategy_challengers WHERE challenger_id=?",
                (str(challenger_id),),
            ).fetchone()
            if row is None:
                raise FileNotFoundError(challenger_id)
        row = conn.execute(
            "SELECT * FROM strategy_challengers WHERE challenger_id=?",
            (str(challenger_id),),
        ).fetchone()
    decoded = _decode(row)
    if decoded is None:
        raise RuntimeError("Challenger registration error persistence failed")
    return decoded
