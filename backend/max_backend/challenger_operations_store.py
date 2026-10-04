from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH
from .db import connect
from .optimizer_store import utc_now
from .scientist_store import migrate_m05

SCHEMA_VERSION = 7
MIN_SUPPORTED_SCHEMA = 7

BACKTEST_STATES = (
    "PREPARED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "UNCONFIRMED",
)
ACTIVE_BACKTEST_STATES = ("PREPARED", "RUNNING")
ACTIVE_PROMOTION_STATES = ("PREPARED", "ARTIFACTS_STAGED", "FILES_COMMITTED")
RETIREMENT_STATES = ("PREPARED", "COMMITTED", "FAILED")

_BACKTEST_TRANSITIONS: dict[str, set[str]] = {
    "PREPARED": {"RUNNING", "FAILED"},
    "RUNNING": {"COMPLETED", "FAILED", "UNCONFIRMED"},
    "COMPLETED": set(),
    "FAILED": set(),
    "UNCONFIRMED": set(),
}


def _challenger_schema_ready(conn: sqlite3.Connection) -> bool:
    table = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='strategy_challengers'"
    ).fetchone()
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(strategy_challengers)").fetchall()
    }
    sql = str(table["sql"] or "") if table is not None else ""
    return "RETIRED" in sql and "retired_utc" in columns


def _upgrade_challenger_registry(conn: sqlite3.Connection) -> None:
    if _challenger_schema_ready(conn):
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            CREATE TABLE strategy_challengers_m06 (
                challenger_id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK (
                    status IN ('REGISTERING','CHALLENGER','PROMOTED','RETIRED')
                ),
                role_origin TEXT NOT NULL CHECK (role_origin = 'OPTIMIZER_WINNER'),

                source_job_id TEXT NOT NULL,
                source_round INTEGER NOT NULL,
                source_pass INTEGER NOT NULL,

                created_utc TEXT NOT NULL,
                updated_utc TEXT NOT NULL,
                retired_utc TEXT,

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
            )
            """
        )
        conn.execute(
            """
            INSERT INTO strategy_challengers_m06(
                challenger_id,status,role_origin,
                source_job_id,source_round,source_pass,
                created_utc,updated_utc,retired_utc,
                ea_version,baseline_ea_sha256,
                challenger_ea_sha256,set_sha256,metadata_sha256,manifest_sha256,
                bundle_path,
                params_json,kpi_json,hard_gates_json,source_request_json,
                winner_json,provenance_json,
                winning_xml_sha256,winning_sidecar_sha256,
                champion_mutation,registration_error
            )
            SELECT
                challenger_id,status,role_origin,
                source_job_id,source_round,source_pass,
                created_utc,updated_utc,NULL,
                ea_version,baseline_ea_sha256,
                challenger_ea_sha256,set_sha256,metadata_sha256,manifest_sha256,
                bundle_path,
                params_json,kpi_json,hard_gates_json,source_request_json,
                winner_json,provenance_json,
                winning_xml_sha256,winning_sidecar_sha256,
                champion_mutation,registration_error
            FROM strategy_challengers
            """
        )
        conn.execute("DROP TABLE strategy_challengers")
        conn.execute("ALTER TABLE strategy_challengers_m06 RENAME TO strategy_challengers")
        conn.execute(
            """
            CREATE INDEX ix_strategy_challengers_created
            ON strategy_challengers(created_utc, challenger_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX ix_strategy_challengers_status_created
            ON strategy_challengers(status, created_utc DESC, challenger_id DESC)
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError("M06_FOREIGN_KEY_INTEGRITY_FAILURE")


def _backtest_schema_ready(conn: sqlite3.Connection) -> bool:
    table = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='strategy_challenger_backtests'"
    ).fetchone()
    if table is None:
        return True
    return "UNCONFIRMED" in str(table["sql"] or "")


def _upgrade_backtest_schema(conn: sqlite3.Connection) -> None:
    if _backtest_schema_ready(conn):
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            CREATE TABLE strategy_challenger_backtests_m06 (
                backtest_id TEXT PRIMARY KEY,
                challenger_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN (
                        'PREPARED','RUNNING','COMPLETED','FAILED','UNCONFIRMED'
                    )
                ),
                created_utc TEXT NOT NULL,
                started_utc TEXT,
                completed_utc TEXT,
                source_manifest_sha256 TEXT NOT NULL,
                request_json TEXT NOT NULL,
                ea_sha256 TEXT NOT NULL,
                set_sha256 TEXT NOT NULL,
                ex5_sha256 TEXT,
                report_path TEXT,
                report_sha256 TEXT,
                result_json TEXT,
                evidence_path TEXT NOT NULL,
                error TEXT,
                FOREIGN KEY(challenger_id)
                    REFERENCES strategy_challengers(challenger_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO strategy_challenger_backtests_m06(
                backtest_id,challenger_id,state,created_utc,started_utc,completed_utc,
                source_manifest_sha256,request_json,ea_sha256,set_sha256,
                ex5_sha256,report_path,report_sha256,result_json,evidence_path,error
            )
            SELECT
                backtest_id,challenger_id,state,created_utc,started_utc,completed_utc,
                source_manifest_sha256,request_json,ea_sha256,set_sha256,
                ex5_sha256,report_path,report_sha256,result_json,evidence_path,error
            FROM strategy_challenger_backtests
            """
        )
        conn.execute("DROP TABLE strategy_challenger_backtests")
        conn.execute(
            "ALTER TABLE strategy_challenger_backtests_m06 "
            "RENAME TO strategy_challenger_backtests"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _create_m06_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS strategy_challenger_backtests (
            backtest_id TEXT PRIMARY KEY,
            challenger_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN (
                    'PREPARED','RUNNING','COMPLETED','FAILED','UNCONFIRMED'
                )
            ),
            created_utc TEXT NOT NULL,
            started_utc TEXT,
            completed_utc TEXT,
            source_manifest_sha256 TEXT NOT NULL,
            request_json TEXT NOT NULL,
            ea_sha256 TEXT NOT NULL,
            set_sha256 TEXT NOT NULL,
            ex5_sha256 TEXT,
            report_path TEXT,
            report_sha256 TEXT,
            result_json TEXT,
            evidence_path TEXT NOT NULL,
            error TEXT,
            FOREIGN KEY(challenger_id)
                REFERENCES strategy_challengers(challenger_id)
        );

        CREATE INDEX IF NOT EXISTS ix_strategy_backtests_challenger_created
        ON strategy_challenger_backtests(
            challenger_id, created_utc DESC, backtest_id DESC
        );

        CREATE INDEX IF NOT EXISTS ix_strategy_backtests_state
        ON strategy_challenger_backtests(state, created_utc DESC);

        CREATE UNIQUE INDEX IF NOT EXISTS ux_strategy_backtests_single_running
        ON strategy_challenger_backtests(state)
        WHERE state='RUNNING';

        CREATE TABLE IF NOT EXISTS strategy_challenger_retirements (
            retirement_id TEXT PRIMARY KEY,
            challenger_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN ('PREPARED','COMMITTED','FAILED')
            ),
            before_status TEXT NOT NULL CHECK (before_status='CHALLENGER'),
            after_status TEXT NOT NULL CHECK (after_status='RETIRED'),
            expected_manifest_sha256 TEXT NOT NULL,
            created_utc TEXT NOT NULL,
            completed_utc TEXT,
            evidence_path TEXT NOT NULL,
            before_state_json TEXT NOT NULL,
            after_state_json TEXT,
            error TEXT,
            FOREIGN KEY(challenger_id)
                REFERENCES strategy_challengers(challenger_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_strategy_retirements_active
        ON strategy_challenger_retirements(challenger_id)
        WHERE state='PREPARED';

        CREATE UNIQUE INDEX IF NOT EXISTS ux_strategy_retirements_committed
        ON strategy_challenger_retirements(challenger_id)
        WHERE state='COMMITTED';

        CREATE INDEX IF NOT EXISTS ix_strategy_retirements_created
        ON strategy_challenger_retirements(
            created_utc DESC, retirement_id DESC
        );
        """
    )


def migrate_m06(path: Path = DATABASE_PATH) -> None:
    migrate_m05(path)
    with connect(path) as conn:
        _upgrade_challenger_registry(conn)
    with connect(path) as conn:
        _upgrade_backtest_schema(conn)
        _create_m06_tables(conn)
        current = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        version = int(current["value"]) if current is not None else 0
        if version < SCHEMA_VERSION:
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("M06_FOREIGN_KEY_INTEGRITY_FAILURE")


def challenger_operations_database_status(
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "FAIL", "reason": "DATABASE_FILE_MISSING"}
    try:
        migrate_m06(path)
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
            retired_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM strategy_challengers WHERE status='RETIRED'"
                ).fetchone()["n"]
            )
            unconfirmed_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM strategy_challenger_backtests "
                    "WHERE state='UNCONFIRMED'"
                ).fetchone()["n"]
            )
        actual = int(schema["value"]) if schema is not None else None
        required = {
            "strategy_challengers",
            "strategy_challenger_backtests",
            "strategy_challenger_retirements",
            "strategy_champions",
            "strategy_promotions",
            "scientist_threads",
            "scientist_messages",
            "scientist_chat_requests",
        }
        if actual is None or actual < MIN_SUPPORTED_SCHEMA:
            return {
                "status": "FAIL",
                "reason": "SCHEMA_VERSION_MISMATCH",
                "schema_version": actual,
            }
        if not required.issubset(tables):
            return {"status": "FAIL", "reason": "M06_TABLES_MISSING"}
        if baseline is None or baseline["status"] != "BASELINE_NOT_CHAMPION":
            return {"status": "FAIL", "reason": "EA_BASELINE_AUTHORITY_INVALID"}
        if current_count > 1:
            return {"status": "FAIL", "reason": "MULTIPLE_CURRENT_CHAMPIONS"}
        return {
            "status": "READY",
            "schema_version": actual,
            "baseline_status": baseline["status"],
            "current_champion_count": current_count,
            "retired_challenger_count": retired_count,
            "unconfirmed_backtest_count": unconfirmed_count,
        }
    except Exception as exc:
        return {
            "status": "FAIL",
            "reason": "DATABASE_UNAVAILABLE",
            "detail": str(exc),
        }


def _compact_row(row: sqlite3.Row) -> dict[str, Any]:
    kpi = json.loads(str(row["kpi_json"]))
    return {
        "challenger_id": row["challenger_id"],
        "status": row["status"],
        "role_origin": row["role_origin"],
        "created_utc": row["created_utc"],
        "updated_utc": row["updated_utc"],
        "retired_utc": row["retired_utc"],
        "source_job_id": row["source_job_id"],
        "source_round": int(row["source_round"]),
        "source_pass": int(row["source_pass"]),
        "manifest_sha256": row["manifest_sha256"],
        "kpi": kpi,
    }


def list_registry_page(
    *,
    view: str,
    query: str = "",
    sort: str = "created",
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_m06(path)
    normalized_view = str(view or "active").strip().lower()
    if normalized_view not in {"active", "retired"}:
        raise ValueError("view must be active or retired")
    normalized_order = str(order or "desc").strip().lower()
    if normalized_order not in {"asc", "desc"}:
        raise ValueError("order must be asc or desc")

    sort_map = {
        "created": "created_utc",
        "retired": "COALESCE(retired_utc,'')",
        "id": "challenger_id",
        "source_job": "source_job_id",
        "profit_factor": "CAST(json_extract(kpi_json,'$.profit_factor') AS REAL)",
        "recovery_factor": "CAST(json_extract(kpi_json,'$.recovery_factor') AS REAL)",
        "mean_r": "CAST(json_extract(kpi_json,'$.mean_r') AS REAL)",
        "weighted_r": "CAST(json_extract(kpi_json,'$.weighted_r') AS REAL)",
        "trades": "CAST(json_extract(kpi_json,'$.trades') AS REAL)",
    }
    if sort not in sort_map:
        raise ValueError("unsupported Challenger sort")

    bounded_page = max(1, int(page))
    bounded_size = max(1, min(int(page_size), 100))
    status = "CHALLENGER" if normalized_view == "active" else "RETIRED"
    needle = str(query or "").strip()
    where = ["status=?"]
    params: list[Any] = [status]
    if needle:
        where.append("(challenger_id LIKE ? ESCAPE '\\' OR source_job_id LIKE ? ESCAPE '\\')")
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        params.extend([like, like])

    where_sql = " AND ".join(where)
    direction = "ASC" if normalized_order == "asc" else "DESC"
    ordering = f"{sort_map[sort]} {direction}, challenger_id {direction}"
    offset = (bounded_page - 1) * bounded_size

    with connect(path) as conn:
        total = int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM strategy_challengers WHERE {where_sql}",
                tuple(params),
            ).fetchone()["n"]
        )
        rows = conn.execute(
            f"""
            SELECT * FROM strategy_challengers
            WHERE {where_sql}
            ORDER BY {ordering}
            LIMIT ? OFFSET ?
            """,
            tuple(params + [bounded_size, offset]),
        ).fetchall()

    pages = max(1, int(math.ceil(total / bounded_size))) if total else 1
    return {
        "view": normalized_view,
        "query": needle,
        "sort": sort,
        "order": normalized_order,
        "page": bounded_page,
        "page_size": bounded_size,
        "pages": pages,
        "total": total,
        "items": [_compact_row(row) for row in rows],
    }


def _decode_retirement(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["before_state"] = json.loads(result.pop("before_state_json"))
    raw_after = result.pop("after_state_json")
    result["after_state"] = json.loads(raw_after) if raw_after else None
    return result


def get_retirement(
    retirement_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    migrate_m06(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM strategy_challenger_retirements WHERE retirement_id=?",
            (str(retirement_id),),
        ).fetchone()
    return _decode_retirement(row)


def list_retirements(
    challenger_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> list[dict[str, Any]]:
    migrate_m06(path)
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM strategy_challenger_retirements
            WHERE challenger_id=?
            ORDER BY created_utc DESC, retirement_id DESC
            """,
            (str(challenger_id),),
        ).fetchall()
    return [
        item
        for row in rows
        if (item := _decode_retirement(row)) is not None
    ]


def retire_registry_row(
    challenger_id: str,
    *,
    retirement_id: str,
    expected_manifest_sha256: str,
    evidence_path: str,
    before_state: dict[str, Any] | None = None,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_m06(path)
    now = utc_now()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM strategy_challengers WHERE challenger_id=?",
            (str(challenger_id),),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(challenger_id)

        actual_manifest = str(row["manifest_sha256"] or "")
        if actual_manifest != str(expected_manifest_sha256):
            raise RuntimeError("CHALLENGER_RETIREMENT_STALE")

        if row["status"] == "RETIRED":
            journal = conn.execute(
                """
                SELECT * FROM strategy_challenger_retirements
                WHERE challenger_id=? AND state='COMMITTED'
                ORDER BY created_utc DESC, retirement_id DESC
                LIMIT 1
                """,
                (str(challenger_id),),
            ).fetchone()
            if journal is None:
                raise RuntimeError("CHALLENGER_RETIRED_WITHOUT_RETIREMENT_JOURNAL")
            return {
                "challenger": _compact_row(row),
                "retirement": _decode_retirement(journal),
            }

        if row["status"] != "CHALLENGER":
            raise RuntimeError("CHALLENGER_NOT_ACTIVE")

        active_backtest = conn.execute(
            """
            SELECT backtest_id,state
            FROM strategy_challenger_backtests
            WHERE challenger_id=? AND state IN ('PREPARED','RUNNING')
            ORDER BY created_utc DESC, backtest_id DESC
            LIMIT 1
            """,
            (str(challenger_id),),
        ).fetchone()
        if active_backtest is not None:
            raise RuntimeError(
                "CHALLENGER_RETIREMENT_BLOCKED_ACTIVE_BACKTEST:"
                f"{active_backtest['backtest_id']}:{active_backtest['state']}"
            )

        active_promotion = conn.execute(
            """
            SELECT promotion_id,state
            FROM strategy_promotions
            WHERE challenger_id=?
              AND state IN ('PREPARED','ARTIFACTS_STAGED','FILES_COMMITTED')
            ORDER BY created_utc DESC, promotion_id DESC
            LIMIT 1
            """,
            (str(challenger_id),),
        ).fetchone()
        if active_promotion is not None:
            raise RuntimeError(
                "CHALLENGER_RETIREMENT_BLOCKED_ACTIVE_PROMOTION:"
                f"{active_promotion['promotion_id']}:{active_promotion['state']}"
            )

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
            raise RuntimeError("CHALLENGER_RETIREMENT_ALREADY_ACTIVE")

        journal_before_state = {
            "challenger_id": str(challenger_id),
            "status": "CHALLENGER",
            "manifest_sha256": actual_manifest,
            "bundle_path": str(row["bundle_path"]),
            "active_backtest": None,
            "active_promotion": None,
        }
        if before_state is not None:
            if not isinstance(before_state, dict):
                raise RuntimeError("CHALLENGER_RETIREMENT_BEFORE_STATE_INVALID")
            for key, expected in journal_before_state.items():
                if before_state.get(key) != expected:
                    raise RuntimeError(
                        f"CHALLENGER_RETIREMENT_BEFORE_STATE_MISMATCH:{key}"
                    )
            journal_before_state.update(before_state)
        conn.execute(
            """
            INSERT INTO strategy_challenger_retirements(
                retirement_id,challenger_id,state,
                before_status,after_status,
                expected_manifest_sha256,
                created_utc,evidence_path,before_state_json
            ) VALUES(?,?,'PREPARED','CHALLENGER','RETIRED',?,?,?,?)
            """,
            (
                str(retirement_id),
                str(challenger_id),
                actual_manifest,
                now,
                str(evidence_path),
                json.dumps(journal_before_state, sort_keys=True),
            ),
        )

        cursor = conn.execute(
            """
            UPDATE strategy_challengers
            SET status='RETIRED', retired_utc=?, updated_utc=?
            WHERE challenger_id=? AND status='CHALLENGER'
              AND manifest_sha256=?
            """,
            (now, now, str(challenger_id), actual_manifest),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("CHALLENGER_RETIREMENT_RACE")

        after_state = {
            "challenger_id": str(challenger_id),
            "status": "RETIRED",
            "manifest_sha256": actual_manifest,
            "retired_utc": now,
            "bundle_path": str(row["bundle_path"]),
        }
        conn.execute(
            """
            UPDATE strategy_challenger_retirements
            SET state='COMMITTED',
                completed_utc=?,
                after_state_json=?
            WHERE retirement_id=? AND state='PREPARED'
            """,
            (
                now,
                json.dumps(after_state, sort_keys=True),
                str(retirement_id),
            ),
        )
        retired_row = conn.execute(
            "SELECT * FROM strategy_challengers WHERE challenger_id=?",
            (str(challenger_id),),
        ).fetchone()
        journal = conn.execute(
            "SELECT * FROM strategy_challenger_retirements WHERE retirement_id=?",
            (str(retirement_id),),
        ).fetchone()

    if retired_row is None or journal is None:
        raise RuntimeError("CHALLENGER_RETIREMENT_PERSISTENCE_FAILED")
    return {
        "challenger": _compact_row(retired_row),
        "retirement": _decode_retirement(journal),
    }


def create_backtest_record(
    *,
    backtest_id: str,
    challenger_id: str,
    source_manifest_sha256: str,
    request: dict[str, Any],
    ea_sha256: str,
    set_sha256: str,
    evidence_path: str,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_m06(path)
    now = utc_now()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        challenger = conn.execute(
            """
            SELECT status,manifest_sha256
            FROM strategy_challengers
            WHERE challenger_id=?
            """,
            (str(challenger_id),),
        ).fetchone()
        if challenger is None:
            raise FileNotFoundError(challenger_id)
        if challenger["status"] != "CHALLENGER":
            raise RuntimeError("CHALLENGER_BACKTEST_CHALLENGER_NOT_ACTIVE")
        if str(challenger["manifest_sha256"] or "") != str(source_manifest_sha256):
            raise RuntimeError("CHALLENGER_BACKTEST_SOURCE_MANIFEST_STALE")

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
            raise RuntimeError("CHALLENGER_BACKTEST_BLOCKED_RETIREMENT_ACTIVE")

        conn.execute(
            """
            INSERT INTO strategy_challenger_backtests(
                backtest_id,challenger_id,state,created_utc,
                source_manifest_sha256,request_json,
                ea_sha256,set_sha256,evidence_path
            ) VALUES(?,?,'PREPARED',?,?,?,?,?,?)
            """,
            (
                str(backtest_id),
                str(challenger_id),
                now,
                str(source_manifest_sha256),
                json.dumps(request, sort_keys=True),
                str(ea_sha256),
                str(set_sha256),
                str(evidence_path),
            ),
        )
        row = conn.execute(
            "SELECT * FROM strategy_challenger_backtests WHERE backtest_id=?",
            (str(backtest_id),),
        ).fetchone()
    decoded = _decode_backtest(row)
    if decoded is None:
        raise RuntimeError("BACKTEST_RECORD_CREATE_FAILED")
    return decoded


def update_backtest(
    backtest_id: str,
    *,
    state: str,
    ex5_sha256: str | None = None,
    report_path: str | None = None,
    report_sha256: str | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_m06(path)
    if state not in BACKTEST_STATES:
        raise ValueError("invalid backtest state")

    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT state FROM strategy_challenger_backtests WHERE backtest_id=?",
            (str(backtest_id),),
        ).fetchone()
        if current is None:
            raise FileNotFoundError(backtest_id)
        current_state = str(current["state"])
        if state != current_state and state not in _BACKTEST_TRANSITIONS[current_state]:
            raise RuntimeError(
                f"BACKTEST_STATE_TRANSITION_INVALID:{current_state}->{state}"
            )

        fields = ["state=?"]
        values: list[Any] = [state]
        if state == "RUNNING" and current_state != "RUNNING":
            fields.append("started_utc=COALESCE(started_utc,?)")
            values.append(utc_now())
        if state in {"COMPLETED", "FAILED", "UNCONFIRMED"}:
            fields.append("completed_utc=COALESCE(completed_utc,?)")
            values.append(utc_now())
        if ex5_sha256 is not None:
            fields.append("ex5_sha256=?")
            values.append(str(ex5_sha256))
        if report_path is not None:
            fields.append("report_path=?")
            values.append(str(report_path))
        if report_sha256 is not None:
            fields.append("report_sha256=?")
            values.append(str(report_sha256))
        if result is not None:
            fields.append("result_json=?")
            values.append(json.dumps(result, sort_keys=True))
        if error is not None:
            fields.append("error=?")
            values.append(str(error)[:2000])
        values.append(str(backtest_id))

        try:
            cursor = conn.execute(
                f"UPDATE strategy_challenger_backtests "
                f"SET {', '.join(fields)} WHERE backtest_id=?",
                tuple(values),
            )
        except sqlite3.IntegrityError as exc:
            if state == "RUNNING":
                raise RuntimeError("CHALLENGER_BACKTEST_ALREADY_RUNNING") from exc
            raise RuntimeError("BACKTEST_STATE_CONSTRAINT_FAILED") from exc
        if cursor.rowcount != 1:
            raise FileNotFoundError(backtest_id)
        row = conn.execute(
            "SELECT * FROM strategy_challenger_backtests WHERE backtest_id=?",
            (str(backtest_id),),
        ).fetchone()

    decoded = _decode_backtest(row)
    if decoded is None:
        raise RuntimeError("BACKTEST_UPDATE_FAILED")
    return decoded


def _decode_backtest(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["request"] = json.loads(result.pop("request_json"))
    raw_result = result.pop("result_json")
    result["result"] = json.loads(raw_result) if raw_result else None
    return result


def get_backtest(
    backtest_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    migrate_m06(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM strategy_challenger_backtests WHERE backtest_id=?",
            (str(backtest_id),),
        ).fetchone()
    return _decode_backtest(row)


def list_backtests(
    challenger_id: str,
    *,
    limit: int = 50,
    path: Path = DATABASE_PATH,
) -> list[dict[str, Any]]:
    migrate_m06(path)
    bounded = max(1, min(int(limit), 200))
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM strategy_challenger_backtests
            WHERE challenger_id=?
            ORDER BY created_utc DESC, backtest_id DESC
            LIMIT ?
            """,
            (str(challenger_id), bounded),
        ).fetchall()
    return [
        item
        for row in rows
        if (item := _decode_backtest(row)) is not None
    ]



def list_backtests_page(
    *,
    challenger_id: str | None = None,
    query: str = "",
    state: str = "",
    sort: str = "created",
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_m06(path)
    normalized_order = str(order or "desc").strip().lower()
    if normalized_order not in {"asc", "desc"}:
        raise ValueError("order must be asc or desc")
    if int(page_size) not in {25, 50, 100}:
        raise ValueError("page_size must be 25, 50, or 100")
    sort_map = {
        "created": "created_utc",
        "id": "backtest_id",
        "state": "state",
        "net_profit": "CAST(json_extract(result_json,'$.metrics.total_net_profit') AS REAL)",
        "profit_factor": "CAST(json_extract(result_json,'$.metrics.profit_factor') AS REAL)",
        "recovery_factor": "CAST(json_extract(result_json,'$.metrics.recovery_factor') AS REAL)",
        "sharpe": "CAST(json_extract(result_json,'$.metrics.sharpe_ratio') AS REAL)",
        "trades": "CAST(json_extract(result_json,'$.metrics.total_trades') AS REAL)",
    }
    if sort not in sort_map:
        raise ValueError("unsupported Backtest sort")
    where: list[str] = []
    params: list[Any] = []
    if challenger_id:
        where.append("challenger_id=?")
        params.append(str(challenger_id))
    if state:
        where.append("state=?")
        params.append(str(state))
    needle = str(query or "").strip()
    if needle:
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        where.append(
            "(backtest_id LIKE ? ESCAPE '\\' "
            "OR challenger_id LIKE ? ESCAPE '\\' "
            "OR json_extract(request_json,'$.symbol') LIKE ? ESCAPE '\\')"
        )
        params.extend([like, like, like])
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    direction = "ASC" if normalized_order == "asc" else "DESC"
    bounded_page = max(1, int(page))
    bounded_size = int(page_size)
    offset = (bounded_page - 1) * bounded_size
    with connect(path) as conn:
        total = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_challenger_backtests" + where_sql,
                tuple(params),
            ).fetchone()["n"]
        )
        rows = conn.execute(
            f"""
            SELECT * FROM strategy_challenger_backtests
            {where_sql}
            ORDER BY {sort_map[sort]} {direction}, backtest_id {direction}
            LIMIT ? OFFSET ?
            """,
            tuple(params + [bounded_size, offset]),
        ).fetchall()
    pages = max(1, int(math.ceil(total / bounded_size))) if total else 1
    return {
        "challenger_id": challenger_id,
        "query": needle,
        "state": state,
        "sort": sort,
        "order": normalized_order,
        "page": bounded_page,
        "page_size": bounded_size,
        "pages": pages,
        "total": total,
        "items": [
            item for row in rows if (item := _decode_backtest(row)) is not None
        ],
    }


def recover_incomplete_backtests(
    path: Path = DATABASE_PATH,
) -> int:
    migrate_m06(path)
    now = utc_now()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        prepared = conn.execute(
            """
            UPDATE strategy_challenger_backtests
            SET state='FAILED',
                completed_utc=?,
                error=COALESCE(
                    error,
                    'BACKTEST_INTERRUPTED_BEFORE_MT5_LAUNCH_NO_RELAUNCH'
                )
            WHERE state='PREPARED'
            """,
            (now,),
        )
        running = conn.execute(
            """
            UPDATE strategy_challenger_backtests
            SET state='UNCONFIRMED',
                completed_utc=?,
                error=COALESCE(
                    error,
                    'BACKTEST_INTERRUPTED_AFTER_MT5_LAUNCH_EXECUTION_UNKNOWN_NO_RELAUNCH'
                )
            WHERE state='RUNNING'
            """,
            (now,),
        )
        return int(prepared.rowcount) + int(running.rowcount)
