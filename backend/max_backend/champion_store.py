from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .challenger_store import migrate_m03
from .config import DATABASE_PATH
from .db import connect
from .optimizer_store import utc_now

SCHEMA_VERSION = 5
PROMOTION_ACTIVE_STATES = ("PREPARED", "ARTIFACTS_STAGED", "FILES_COMMITTED")
PROMOTION_TERMINAL_STATES = ("COMMITTED", "ROLLED_BACK", "FAILED")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _decode_json(raw: str | None) -> Any:
    return json.loads(raw) if raw else None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone()
    return row is not None


def _challenger_table_supports_promoted(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='strategy_challengers'"
    ).fetchone()
    return bool(row and "PROMOTED" in str(row["sql"] or ""))


def _upgrade_challenger_status_constraint(conn: sqlite3.Connection) -> None:
    if _challenger_table_supports_promoted(conn):
        return
    conn.executescript(
        """
        ALTER TABLE strategy_challengers RENAME TO strategy_challengers_m03;

        CREATE TABLE strategy_challengers (
            challenger_id TEXT PRIMARY KEY,
            status TEXT NOT NULL CHECK (status IN ('REGISTERING','CHALLENGER','PROMOTED')),
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

        INSERT INTO strategy_challengers
        SELECT * FROM strategy_challengers_m03;

        DROP TABLE strategy_challengers_m03;

        CREATE INDEX IF NOT EXISTS ix_strategy_challengers_created
        ON strategy_challengers(created_utc, challenger_id);
        """
    )



def _create_champion_tenure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_champions (
            champion_tenure_id TEXT PRIMARY KEY,
            strategy_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('CURRENT','FORMER')),
            source_challenger_id TEXT NOT NULL,
            source_job_id TEXT NOT NULL,
            source_round INTEGER NOT NULL,
            source_pass INTEGER NOT NULL,
            params_json TEXT NOT NULL,
            kpi_json TEXT NOT NULL,
            hard_gates_json TEXT NOT NULL,
            champion_ea_sha256 TEXT NOT NULL,
            champion_set_sha256 TEXT NOT NULL,
            deployed_ea_sha256 TEXT NOT NULL,
            deployed_ex5_sha256 TEXT NOT NULL,
            tester_set_sha256 TEXT NOT NULL,
            promoted_utc TEXT NOT NULL,
            demoted_utc TEXT,
            promotion_id TEXT NOT NULL UNIQUE,
            authority_source TEXT NOT NULL
                CHECK (authority_source = 'OWNER_MANUAL_STRATEGY_PROMOTION'),
            artifact_path TEXT NOT NULL,
            evidence_path TEXT NOT NULL,
            deployment_json TEXT NOT NULL,
            former_archive_id TEXT,
            replaced_by TEXT,
            FOREIGN KEY(source_challenger_id)
                REFERENCES strategy_challengers(challenger_id),
            FOREIGN KEY(promotion_id)
                REFERENCES strategy_promotions(promotion_id)
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_strategy_champions_current
        ON strategy_champions(status)
        WHERE status='CURRENT'
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_strategy_champions_promoted
        ON strategy_champions(promoted_utc, strategy_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_strategy_champions_strategy
        ON strategy_champions(strategy_id, promoted_utc)
        """
    )


def _upgrade_champion_tenure_schema(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='strategy_champions'"
    ).fetchone()
    if row is None:
        _create_champion_tenure_table(conn)
        return
    sql = str(row["sql"] or "")
    if "champion_tenure_id" in sql:
        _create_champion_tenure_table(conn)
        return

    conn.execute("DROP INDEX IF EXISTS ux_strategy_champions_current")
    conn.execute("DROP INDEX IF EXISTS ix_strategy_champions_promoted")
    conn.execute(
        "ALTER TABLE strategy_champions RENAME TO strategy_champions_m04"
    )
    _create_champion_tenure_table(conn)
    conn.execute(
        """
        INSERT INTO strategy_champions(
            champion_tenure_id,strategy_id,status,source_challenger_id,
            source_job_id,source_round,source_pass,
            params_json,kpi_json,hard_gates_json,
            champion_ea_sha256,champion_set_sha256,
            deployed_ea_sha256,deployed_ex5_sha256,tester_set_sha256,
            promoted_utc,demoted_utc,promotion_id,authority_source,
            artifact_path,evidence_path,deployment_json,
            former_archive_id,replaced_by
        )
        SELECT
            promotion_id,strategy_id,status,source_challenger_id,
            source_job_id,source_round,source_pass,
            params_json,kpi_json,hard_gates_json,
            champion_ea_sha256,champion_set_sha256,
            deployed_ea_sha256,deployed_ex5_sha256,tester_set_sha256,
            promoted_utc,demoted_utc,promotion_id,authority_source,
            artifact_path,evidence_path,deployment_json,
            former_archive_id,replaced_by
        FROM strategy_champions_m04
        """
    )
    conn.execute("DROP TABLE strategy_champions_m04")

def migrate_m04(path: Path = DATABASE_PATH) -> None:
    migrate_m03(path)
    with connect(path) as conn:
        _upgrade_challenger_status_constraint(conn)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategy_promotions (
                promotion_id TEXT PRIMARY KEY,
                challenger_id TEXT NOT NULL,
                previous_champion_id TEXT,
                new_champion_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN (
                        'PREPARED','ARTIFACTS_STAGED','FILES_COMMITTED',
                        'COMMITTED','ROLLED_BACK','FAILED'
                    )
                ),
                active_key INTEGER NOT NULL DEFAULT 1 CHECK (active_key = 1),

                created_utc TEXT NOT NULL,
                completed_utc TEXT,
                failed_utc TEXT,

                expected_challenger_manifest_sha256 TEXT NOT NULL,
                expected_previous_champion_id TEXT,

                before_state_json TEXT NOT NULL,
                post_state_json TEXT,
                baseline_archive_id TEXT,
                former_champion_archive_id TEXT,
                compile_json TEXT,
                parity_json TEXT,
                deployment_json TEXT,
                evidence_path TEXT,
                recovery_path TEXT NOT NULL,

                error TEXT,
                rollback_status TEXT,

                FOREIGN KEY(challenger_id) REFERENCES strategy_challengers(challenger_id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS ux_strategy_promotions_active
            ON strategy_promotions(active_key)
            WHERE state IN ('PREPARED','ARTIFACTS_STAGED','FILES_COMMITTED');

            CREATE INDEX IF NOT EXISTS ix_strategy_promotions_created
            ON strategy_promotions(created_utc, promotion_id);

            """
        )
        conn.execute("BEGIN IMMEDIATE")
        _upgrade_champion_tenure_schema(conn)
        current = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        current_version = int(current["value"]) if current is not None else 0
        if current_version < SCHEMA_VERSION:
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )


def champion_database_status(path: Path = DATABASE_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "FAIL", "reason": "DATABASE_FILE_MISSING"}
    try:
        migrate_m04(path)
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
            current_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM strategy_champions WHERE status='CURRENT'"
                ).fetchone()["n"]
            )
        required = {
            "ea_baseline",
            "optimizer_jobs",
            "optimizer_rounds",
            "strategy_challengers",
            "strategy_champions",
            "strategy_promotions",
        }
        actual_schema = int(schema["value"]) if schema is not None else None
        if actual_schema is None or actual_schema < SCHEMA_VERSION:
            return {
                "status": "FAIL",
                "reason": "SCHEMA_VERSION_MISMATCH",
                "schema_version": actual_schema,
            }
        if not required.issubset(tables):
            return {"status": "FAIL", "reason": "M04_TABLES_MISSING"}
        if baseline is None or baseline["status"] != "BASELINE_NOT_CHAMPION":
            return {"status": "FAIL", "reason": "EA_BASELINE_AUTHORITY_INVALID"}
        if current_count > 1:
            return {"status": "FAIL", "reason": "MULTIPLE_CURRENT_CHAMPIONS"}
        return {
            "status": "READY",
            "schema_version": actual_schema,
            "baseline_status": baseline["status"],
            "current_champion_count": current_count,
        }
    except Exception as exc:
        return {
            "status": "FAIL",
            "reason": "DATABASE_UNAVAILABLE",
            "detail": str(exc),
        }


def _decode_promotion(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for column in (
        "before_state_json",
        "post_state_json",
        "compile_json",
        "parity_json",
        "deployment_json",
    ):
        result[column.removesuffix("_json")] = _decode_json(result.pop(column))
    return result


def _decode_champion(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for column in ("params_json", "kpi_json", "hard_gates_json", "deployment_json"):
        result[column.removesuffix("_json")] = _decode_json(result.pop(column))
    result["source_round"] = int(result["source_round"])
    result["source_pass"] = int(result["source_pass"])
    return result


def current_champion(*, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    migrate_m04(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM strategy_champions WHERE status='CURRENT'"
        ).fetchone()
    return _decode_champion(row)


def current_champion_summary(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    """Return the persisted current identity without verifying its artifact tree."""
    migrate_m04(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT strategy_id,status FROM strategy_champions WHERE status='CURRENT'"
        ).fetchone()
    current = dict(row) if row is not None else None
    return {
        "status": "CURRENT_STRATEGY_CHAMPION" if current else "NONE",
        "current": current,
        "integrity": {"status": "NOT_CHECKED"},
        "live_authority": "NONE",
    }


def get_champion(strategy_id: str, *, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    migrate_m04(path)
    with connect(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM strategy_champions
            WHERE strategy_id=?
            ORDER BY promoted_utc DESC, champion_tenure_id DESC
            LIMIT 1
            """,
            (str(strategy_id),),
        ).fetchone()
    return _decode_champion(row)


def list_champions(*, path: Path = DATABASE_PATH) -> list[dict[str, Any]]:
    migrate_m04(path)
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM strategy_champions ORDER BY promoted_utc DESC, strategy_id DESC"
        ).fetchall()
    return [item for row in rows if (item := _decode_champion(row)) is not None]


def active_promotion(*, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    migrate_m04(path)
    with connect(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM strategy_promotions
            WHERE state IN ('PREPARED','ARTIFACTS_STAGED','FILES_COMMITTED')
            LIMIT 1
            """
        ).fetchone()
    return _decode_promotion(row)


def get_promotion(promotion_id: str, *, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    migrate_m04(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM strategy_promotions WHERE promotion_id=?",
            (str(promotion_id),),
        ).fetchone()
    return _decode_promotion(row)


def list_promotions(*, path: Path = DATABASE_PATH) -> list[dict[str, Any]]:
    migrate_m04(path)
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM strategy_promotions ORDER BY created_utc DESC, promotion_id DESC"
        ).fetchall()
    return [item for row in rows if (item := _decode_promotion(row)) is not None]


def create_prepared_promotion(
    *,
    promotion_id: str,
    challenger_id: str,
    previous_champion_id: str | None,
    expected_manifest_sha256: str,
    before_state: dict[str, Any],
    recovery_path: str,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_m04(path)
    now = utc_now()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            """
            SELECT promotion_id FROM strategy_promotions
            WHERE state IN ('PREPARED','ARTIFACTS_STAGED','FILES_COMMITTED')
            LIMIT 1
            """
        ).fetchone()
        if active is not None:
            raise RuntimeError("PROMOTION_ALREADY_ACTIVE")

        challenger = conn.execute(
            """
            SELECT status,manifest_sha256
            FROM strategy_challengers
            WHERE challenger_id=?
            """,
            (str(challenger_id),),
        ).fetchone()
        if challenger is None or challenger["status"] != "CHALLENGER":
            raise RuntimeError("PROMOTION_CHALLENGER_NOT_ACTIVE")
        if str(challenger["manifest_sha256"] or "") != str(expected_manifest_sha256):
            raise RuntimeError("PROMOTION_CHALLENGER_MANIFEST_STALE")

        if _table_exists(conn, "strategy_challenger_retirements"):
            active_retirement = conn.execute(
                """
                SELECT retirement_id
                FROM strategy_challenger_retirements
                WHERE challenger_id=? AND state='PREPARED'
                LIMIT 1
                """,
                (str(challenger_id),),
            ).fetchone()
            if active_retirement is not None:
                raise RuntimeError("PROMOTION_BLOCKED_RETIREMENT_ACTIVE")

        conn.execute(
            """
            INSERT INTO strategy_promotions(
                promotion_id,challenger_id,previous_champion_id,new_champion_id,
                state,created_utc,
                expected_challenger_manifest_sha256,
                expected_previous_champion_id,
                before_state_json,recovery_path
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                promotion_id,
                challenger_id,
                previous_champion_id,
                challenger_id,
                "PREPARED",
                now,
                expected_manifest_sha256,
                previous_champion_id,
                _json(before_state),
                recovery_path,
            ),
        )
        row = conn.execute(
            "SELECT * FROM strategy_promotions WHERE promotion_id=?",
            (promotion_id,),
        ).fetchone()
    decoded = _decode_promotion(row)
    if decoded is None:
        raise RuntimeError("PROMOTION_PREPARE_PERSIST_FAILED")
    return decoded


def update_promotion(
    promotion_id: str,
    *,
    state: str | None = None,
    post_state: dict[str, Any] | None = None,
    baseline_archive_id: str | None = None,
    former_champion_archive_id: str | None = None,
    compile_result: dict[str, Any] | None = None,
    parity: dict[str, Any] | None = None,
    deployment: dict[str, Any] | None = None,
    evidence_path: str | None = None,
    error: str | None = None,
    rollback_status: str | None = None,
    mark_completed: bool = False,
    mark_failed: bool = False,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_m04(path)
    allowed = {
        "PREPARED",
        "ARTIFACTS_STAGED",
        "FILES_COMMITTED",
        "COMMITTED",
        "ROLLED_BACK",
        "FAILED",
    }
    fields: list[str] = []
    values: list[Any] = []
    if state is not None:
        if state not in allowed:
            raise ValueError("PROMOTION_STATE_INVALID")
        fields.append("state=?")
        values.append(state)
    mapping = (
        ("post_state_json", post_state),
        ("compile_json", compile_result),
        ("parity_json", parity),
        ("deployment_json", deployment),
    )
    for column, value in mapping:
        if value is not None:
            fields.append(f"{column}=?")
            values.append(_json(value))
    for column, value in (
        ("baseline_archive_id", baseline_archive_id),
        ("former_champion_archive_id", former_champion_archive_id),
        ("evidence_path", evidence_path),
        ("error", error),
        ("rollback_status", rollback_status),
    ):
        if value is not None:
            fields.append(f"{column}=?")
            values.append(str(value))
    if mark_completed:
        fields.append("completed_utc=?")
        values.append(utc_now())
    if mark_failed:
        fields.append("failed_utc=?")
        values.append(utc_now())
    if not fields:
        current = get_promotion(promotion_id, path=path)
        if current is None:
            raise FileNotFoundError(promotion_id)
        return current
    values.append(promotion_id)
    with connect(path) as conn:
        cursor = conn.execute(
            f"UPDATE strategy_promotions SET {','.join(fields)} WHERE promotion_id=?",
            values,
        )
        if cursor.rowcount != 1:
            raise FileNotFoundError(promotion_id)
        row = conn.execute(
            "SELECT * FROM strategy_promotions WHERE promotion_id=?",
            (promotion_id,),
        ).fetchone()
    decoded = _decode_promotion(row)
    if decoded is None:
        raise RuntimeError("PROMOTION_UPDATE_FAILED")
    return decoded


def commit_promotion_authority(
    promotion_id: str,
    *,
    champion: dict[str, Any],
    post_state: dict[str, Any],
    baseline_archive_id: str | None,
    former_champion_archive_id: str | None,
    compile_result: dict[str, Any],
    parity: dict[str, Any],
    deployment: dict[str, Any],
    evidence_path: str,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_m04(path)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        promotion = conn.execute(
            "SELECT * FROM strategy_promotions WHERE promotion_id=?",
            (promotion_id,),
        ).fetchone()
        if promotion is None:
            raise FileNotFoundError(promotion_id)
        if promotion["state"] != "FILES_COMMITTED":
            raise RuntimeError("PROMOTION_NOT_READY_FOR_AUTHORITY_COMMIT")

        challenger_id = str(promotion["challenger_id"])
        challenger = conn.execute(
            "SELECT status FROM strategy_challengers WHERE challenger_id=?",
            (challenger_id,),
        ).fetchone()
        if challenger is None or challenger["status"] != "CHALLENGER":
            raise RuntimeError("PROMOTION_CHALLENGER_NOT_ACTIVE")

        current = conn.execute(
            """
            SELECT champion_tenure_id,strategy_id,source_challenger_id
            FROM strategy_champions
            WHERE status='CURRENT'
            """
        ).fetchone()
        actual_previous = str(current["strategy_id"]) if current else None
        expected_previous = promotion["expected_previous_champion_id"]
        if actual_previous != expected_previous:
            raise RuntimeError("PROMOTION_CONFIRMATION_STALE")

        promoted_utc = str(champion["promoted_utc"])
        if actual_previous:
            previous_challenger_id = str(current["source_challenger_id"])
            conn.execute(
                """
                UPDATE strategy_champions
                SET status='FORMER', demoted_utc=?, former_archive_id=?, replaced_by=?
                WHERE champion_tenure_id=? AND status='CURRENT'
                """,
                (
                    promoted_utc,
                    former_champion_archive_id,
                    challenger_id,
                    str(current["champion_tenure_id"]),
                ),
            )
            previous_source = conn.execute(
                """
                SELECT status FROM strategy_challengers
                WHERE challenger_id=?
                """,
                (previous_challenger_id,),
            ).fetchone()
            if (
                previous_source is None
                or str(previous_source["status"]) != "PROMOTED"
            ):
                raise RuntimeError(
                    "PREVIOUS_CHAMPION_SOURCE_LINEAGE_INVALID"
                )

        conn.execute(
            """
            INSERT INTO strategy_champions(
                champion_tenure_id,strategy_id,status,source_challenger_id,
                source_job_id,source_round,source_pass,
                params_json,kpi_json,hard_gates_json,
                champion_ea_sha256,champion_set_sha256,
                deployed_ea_sha256,deployed_ex5_sha256,tester_set_sha256,
                promoted_utc,promotion_id,authority_source,
                artifact_path,evidence_path,deployment_json
            ) VALUES(
                ?,?,'CURRENT',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                ?,?,?
            )
            """,
            (
                promotion_id,
                challenger_id,
                challenger_id,
                str(champion["source_job_id"]),
                int(champion["source_round"]),
                int(champion["source_pass"]),
                _json(champion["params"]),
                _json(champion["kpi"]),
                _json(champion["hard_gates"]),
                str(champion["champion_ea_sha256"]),
                str(champion["champion_set_sha256"]),
                str(champion["deployed_ea_sha256"]),
                str(champion["deployed_ex5_sha256"]),
                str(champion["tester_set_sha256"]),
                promoted_utc,
                promotion_id,
                "OWNER_MANUAL_STRATEGY_PROMOTION",
                str(champion["artifact_path"]),
                evidence_path,
                _json(deployment),
            ),
        )
        conn.execute(
            """
            UPDATE strategy_challengers
            SET status='PROMOTED', updated_utc=?
            WHERE challenger_id=? AND status='CHALLENGER'
            """,
            (promoted_utc, challenger_id),
        )
        conn.execute(
            """
            UPDATE strategy_promotions SET
                state='COMMITTED',
                completed_utc=?,
                post_state_json=?,
                baseline_archive_id=?,
                former_champion_archive_id=?,
                compile_json=?,
                parity_json=?,
                deployment_json=?,
                evidence_path=?,
                error=NULL,
                rollback_status=NULL
            WHERE promotion_id=? AND state='FILES_COMMITTED'
            """,
            (
                promoted_utc,
                _json(post_state),
                baseline_archive_id,
                former_champion_archive_id,
                _json(compile_result),
                _json(parity),
                _json(deployment),
                evidence_path,
                promotion_id,
            ),
        )
        current_count = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_champions WHERE status='CURRENT'"
            ).fetchone()["n"]
        )
        if current_count != 1:
            raise RuntimeError("CURRENT_CHAMPION_CARDINALITY_INVALID")
        row = conn.execute(
            "SELECT * FROM strategy_champions WHERE champion_tenure_id=?",
            (promotion_id,),
        ).fetchone()
    decoded = _decode_champion(row)
    if decoded is None:
        raise RuntimeError("CHAMPION_AUTHORITY_COMMIT_FAILED")
    return decoded
