from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .challenger_operations_store import migrate_m06
from .config import DATABASE_PATH
from .db import connect
from .optimizer_store import utc_now
from .schema import CURRENT_SCHEMA_VERSION
from .workflow_contract import (
    ROLE_OPTIMIZER_WINNER,
    ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE,
)

ROLE_OWNER_SELECTED = ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE
ROLE_LEGACY_WINNER = ROLE_OPTIMIZER_WINNER
BATCH_STATES = {"PREPARED", "VALIDATED", "STAGED", "COMMITTED", "FAILED", "RECOVERY_REQUIRED"}
RETENTION_CLASSES = {"ACTIVE_AUTHORITY", "USER_GENERATED", "TEMPORARY_RUNTIME", "REGENERABLE"}


def _challenger_role_schema_ready(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='strategy_challengers'"
    ).fetchone()
    return row is not None and ROLE_OWNER_SELECTED in str(row["sql"] or "")


def _upgrade_challenger_roles(conn: sqlite3.Connection) -> None:
    if _challenger_role_schema_ready(conn):
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            f"""
            CREATE TABLE strategy_challengers_upgrade (
                challenger_id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK (
                    status IN ('REGISTERING','CHALLENGER','PROMOTED','RETIRED')
                ),
                role_origin TEXT NOT NULL CHECK (
                    role_origin IN ('{ROLE_LEGACY_WINNER}','{ROLE_OWNER_SELECTED}')
                ),
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
            INSERT INTO strategy_challengers_upgrade(
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
                created_utc,updated_utc,retired_utc,
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
        conn.execute("ALTER TABLE strategy_challengers_upgrade RENAME TO strategy_challengers")
        conn.execute(
            "CREATE INDEX ix_strategy_challengers_created "
            "ON strategy_challengers(created_utc, challenger_id)"
        )
        conn.execute(
            "CREATE INDEX ix_strategy_challengers_status_created "
            "ON strategy_challengers(status, created_utc DESC, challenger_id DESC)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("CURRENT_SCHEMA_FOREIGN_KEY_INTEGRITY_FAILURE")


def _ensure_backtest_runtime_status(conn: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(strategy_challenger_backtests)").fetchall()
    }
    if "runtime_status" not in columns:
        conn.execute(
            "ALTER TABLE strategy_challenger_backtests "
            "ADD COLUMN runtime_status TEXT NOT NULL DEFAULT 'PRESENT' "
            "CHECK(runtime_status IN ('PRESENT','CLEANED','UNKNOWN'))"
        )


def migrate_current(path: Path = DATABASE_PATH) -> None:
    migrate_m06(path)
    with connect(path) as conn:
        _upgrade_challenger_roles(conn)
        _ensure_backtest_runtime_status(conn)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategy_challenger_batches (
                batch_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN (
                        'PREPARED','VALIDATED','STAGED','COMMITTED',
                        'FAILED','RECOVERY_REQUIRED'
                    )
                ),
                request_json TEXT NOT NULL,
                result_json TEXT,
                created_utc TEXT NOT NULL,
                updated_utc TEXT NOT NULL,
                error TEXT,
                FOREIGN KEY(job_id) REFERENCES optimizer_jobs(job_id)
            );
            CREATE TABLE IF NOT EXISTS strategy_challenger_batch_items (
                batch_id TEXT NOT NULL,
                source_round INTEGER NOT NULL,
                source_pass INTEGER NOT NULL,
                challenger_id TEXT NOT NULL,
                state TEXT NOT NULL,
                PRIMARY KEY(batch_id,source_round,source_pass),
                FOREIGN KEY(batch_id)
                    REFERENCES strategy_challenger_batches(batch_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS ix_challenger_batches_job
            ON strategy_challenger_batches(job_id, created_utc DESC);

            CREATE TABLE IF NOT EXISTS research_authorizations (
                authorization_id TEXT PRIMARY KEY,
                gate TEXT NOT NULL CHECK (
                    gate IN ('R00','R01','R02','R03','R04','R05','R06','R07','R08','R09','R10')
                ),
                action TEXT NOT NULL,
                confirmed INTEGER NOT NULL CHECK(confirmed IN (0,1)),
                expected_parent_strategy_id TEXT NOT NULL,
                expected_parent_authority_sha256 TEXT NOT NULL,
                cumulative_strategy_e2e_authority TEXT NOT NULL,
                h1_minimum_trades_per_month INTEGER NOT NULL
                    CHECK(h1_minimum_trades_per_month > 0),
                payload_sha256 TEXT NOT NULL,
                authorized_utc TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS research_projects (
                research_id TEXT PRIMARY KEY,
                research_parent_id TEXT NOT NULL,
                parent_strategy_id TEXT NOT NULL,
                parent_authority_sha256 TEXT NOT NULL,
                parent_manifest_path TEXT NOT NULL,
                parent_manifest_sha256 TEXT NOT NULL,
                feature_contract TEXT NOT NULL,
                current_gate TEXT NOT NULL CHECK (
                    current_gate IN ('R00','R01','R02','R03','R04','R05','R06','R07','R08','R09','R10')
                ),
                gate_state TEXT NOT NULL CHECK (
                    gate_state IN (
                        'STARTING',
                        'PASS_WAITING_OWNER',
                        'FAIL_WAITING_OWNER',
                        'ERROR_WAITING_OWNER'
                    )
                ),
                gate_input_manifest_sha TEXT,
                gate_output_manifest_sha TEXT,
                owner_authorization_id TEXT NOT NULL,
                authorized_utc TEXT NOT NULL,
                hardware_snapshot_path TEXT NOT NULL,
                hardware_snapshot_sha256 TEXT NOT NULL,
                label_authority_json TEXT NOT NULL,
                candidate_identity_contract_json TEXT NOT NULL,
                artifact_lineage_contract_json TEXT NOT NULL,
                research_policy_json TEXT NOT NULL,
                unresolved_authority_json TEXT NOT NULL,
                candidate_ids_json TEXT NOT NULL DEFAULT '[]',
                qualification_states_json TEXT NOT NULL DEFAULT '{}',
                system_recommendation_json TEXT,
                owner_selected_ids_json TEXT NOT NULL DEFAULT '[]',
                training_count INTEGER NOT NULL DEFAULT 0 CHECK(training_count >= 0),
                onnx_count INTEGER NOT NULL DEFAULT 0 CHECK(onnx_count >= 0),
                research_challenger_count INTEGER NOT NULL DEFAULT 0 CHECK(research_challenger_count >= 0),
                champion_mutation TEXT NOT NULL DEFAULT 'NONE'
                    CHECK(champion_mutation = 'NONE'),
                created_utc TEXT NOT NULL,
                updated_utc TEXT NOT NULL,
                FOREIGN KEY(owner_authorization_id)
                    REFERENCES research_authorizations(authorization_id)
            );
            CREATE INDEX IF NOT EXISTS ix_research_projects_updated
            ON research_projects(updated_utc DESC, research_id DESC);
            CREATE INDEX IF NOT EXISTS ix_research_projects_parent
            ON research_projects(parent_strategy_id, created_utc DESC);

            CREATE TABLE IF NOT EXISTS research_gate_events (
                event_id TEXT PRIMARY KEY,
                research_id TEXT NOT NULL,
                gate TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT NOT NULL,
                authority TEXT NOT NULL,
                owner_authorization_id TEXT,
                input_manifest_sha TEXT,
                output_manifest_sha TEXT,
                created_utc TEXT NOT NULL,
                FOREIGN KEY(research_id)
                    REFERENCES research_projects(research_id)
            );
            CREATE INDEX IF NOT EXISTS ix_research_gate_events_research
            ON research_gate_events(research_id, created_utc, event_id);

            CREATE TABLE IF NOT EXISTS research_memory_events (
                event_id TEXT PRIMARY KEY,
                research_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                learning_zone TEXT NOT NULL CHECK (
                    learning_zone IN (
                        'FOUNDATION','ADAPTIVE','VALIDATION','PROTECTED','UNKNOWN'
                    )
                ),
                adaptive_eligible INTEGER NOT NULL CHECK(adaptive_eligible IN (0,1)),
                source_manifest_sha256 TEXT,
                payload_json TEXT NOT NULL,
                created_utc TEXT NOT NULL,
                FOREIGN KEY(research_id)
                    REFERENCES research_projects(research_id)
            );
            CREATE INDEX IF NOT EXISTS ix_research_memory_research
            ON research_memory_events(research_id, created_utc, event_id);

            CREATE TABLE IF NOT EXISTS research_gate_authorizations_v2 (
                authorization_id TEXT PRIMARY KEY,
                research_id TEXT NOT NULL,
                gate TEXT NOT NULL CHECK(gate='R01'),
                action TEXT NOT NULL CHECK(action='START'),
                confirmed INTEGER NOT NULL CHECK(confirmed IN (0,1)),
                payload_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                authorized_utc TEXT NOT NULL,
                FOREIGN KEY(research_id) REFERENCES research_projects(research_id)
            );
            CREATE INDEX IF NOT EXISTS ix_research_gate_authorizations_v2_research
            ON research_gate_authorizations_v2(research_id, authorized_utc, authorization_id);

            CREATE TABLE IF NOT EXISTS research_r01_runs (
                run_id TEXT PRIMARY KEY,
                research_id TEXT NOT NULL UNIQUE,
                authorization_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK(
                    state IN (
                        'STARTING',
                        'PASS_WAITING_OWNER',
                        'FAIL_WAITING_OWNER',
                        'ERROR_WAITING_OWNER'
                    )
                ),
                dataset_id TEXT,
                input_manifest_sha TEXT,
                output_manifest_sha TEXT,
                artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                created_utc TEXT NOT NULL,
                updated_utc TEXT NOT NULL,
                FOREIGN KEY(research_id) REFERENCES research_projects(research_id),
                FOREIGN KEY(authorization_id)
                    REFERENCES research_gate_authorizations_v2(authorization_id)
            );
            CREATE INDEX IF NOT EXISTS ix_research_r01_runs_state
            ON research_r01_runs(state, updated_utc DESC, run_id DESC);

            CREATE TABLE IF NOT EXISTS research_r02_authorizations (
                authorization_id TEXT PRIMARY KEY,
                research_id TEXT NOT NULL UNIQUE,
                confirmed INTEGER NOT NULL CHECK(confirmed IN (0,1)),
                payload_sha256 TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                authorized_utc TEXT NOT NULL,
                FOREIGN KEY(research_id) REFERENCES research_projects(research_id)
            );
            CREATE INDEX IF NOT EXISTS ix_research_r02_authorizations_research
            ON research_r02_authorizations(research_id, authorized_utc, authorization_id);

            CREATE TABLE IF NOT EXISTS research_r02_discovery_blocks (
                block_id TEXT PRIMARY KEY,
                research_id TEXT NOT NULL UNIQUE,
                authorization_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK(state='FROZEN_WAITING_EXECUTION'),
                r01_output_manifest_sha256 TEXT NOT NULL,
                plan_id TEXT NOT NULL UNIQUE,
                plan_sha256 TEXT NOT NULL UNIQUE,
                candidate_count INTEGER NOT NULL CHECK(candidate_count > 0),
                compute_budget_json TEXT NOT NULL,
                created_utc TEXT NOT NULL,
                FOREIGN KEY(research_id) REFERENCES research_projects(research_id),
                FOREIGN KEY(authorization_id)
                    REFERENCES research_r02_authorizations(authorization_id)
            );
            CREATE INDEX IF NOT EXISTS ix_research_r02_blocks_created
            ON research_r02_discovery_blocks(created_utc DESC, block_id DESC);

            CREATE TABLE IF NOT EXISTS research_r02_candidate_specs (
                candidate_id TEXT PRIMARY KEY,
                block_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                model_family TEXT NOT NULL,
                seed INTEGER NOT NULL CHECK(seed >= 0),
                spec_sha256 TEXT NOT NULL UNIQUE,
                spec_json TEXT NOT NULL,
                created_utc TEXT NOT NULL,
                UNIQUE(block_id, ordinal),
                FOREIGN KEY(block_id)
                    REFERENCES research_r02_discovery_blocks(block_id)
            );
            CREATE INDEX IF NOT EXISTS ix_research_r02_candidates_block
            ON research_r02_candidate_specs(block_id, ordinal, candidate_id);

            CREATE TRIGGER IF NOT EXISTS research_r02_authorization_no_update
            BEFORE UPDATE ON research_r02_authorizations
            BEGIN
                SELECT RAISE(ABORT, 'R02_AUTHORIZATION_IMMUTABLE');
            END;
            CREATE TRIGGER IF NOT EXISTS research_r02_authorization_no_delete
            BEFORE DELETE ON research_r02_authorizations
            BEGIN
                SELECT RAISE(ABORT, 'R02_AUTHORIZATION_APPEND_ONLY');
            END;
            CREATE TRIGGER IF NOT EXISTS research_r02_block_no_update
            BEFORE UPDATE ON research_r02_discovery_blocks
            BEGIN
                SELECT RAISE(ABORT, 'R02_DISCOVERY_BLOCK_IMMUTABLE');
            END;
            CREATE TRIGGER IF NOT EXISTS research_r02_block_no_delete
            BEFORE DELETE ON research_r02_discovery_blocks
            BEGIN
                SELECT RAISE(ABORT, 'R02_DISCOVERY_BLOCK_APPEND_ONLY');
            END;
            CREATE TRIGGER IF NOT EXISTS research_r02_candidate_no_update
            BEFORE UPDATE ON research_r02_candidate_specs
            BEGIN
                SELECT RAISE(ABORT, 'R02_CANDIDATE_SPEC_IMMUTABLE');
            END;
            CREATE TRIGGER IF NOT EXISTS research_r02_candidate_no_delete
            BEFORE DELETE ON research_r02_candidate_specs
            BEGIN
                SELECT RAISE(ABORT, 'R02_CANDIDATE_SPEC_APPEND_ONLY');
            END;

            CREATE TABLE IF NOT EXISTS research_r01_sources (
                source_id TEXT PRIMARY KEY,
                research_id TEXT NOT NULL,
                parent_strategy_id TEXT NOT NULL,
                parent_authority_sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status='READY'),
                bundle_path TEXT NOT NULL UNIQUE,
                bundle_sha256 TEXT NOT NULL UNIQUE,
                source_identity_sha256 TEXT NOT NULL,
                broker TEXT NOT NULL,
                feed TEXT NOT NULL,
                source_timezone TEXT NOT NULL,
                main_symbol TEXT NOT NULL,
                relative_symbol TEXT NOT NULL,
                data_start_utc TEXT NOT NULL,
                data_end_utc TEXT NOT NULL,
                row_coverage_json TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                created_utc TEXT NOT NULL,
                FOREIGN KEY(research_id) REFERENCES research_projects(research_id)
            );
            CREATE INDEX IF NOT EXISTS ix_research_r01_sources_research
            ON research_r01_sources(research_id, created_utc DESC, source_id DESC);

            CREATE TRIGGER IF NOT EXISTS research_r01_source_no_update
            BEFORE UPDATE ON research_r01_sources
            BEGIN
                SELECT RAISE(ABORT, 'R01_SOURCE_IMMUTABLE');
            END;
            CREATE TRIGGER IF NOT EXISTS research_r01_source_no_delete
            BEFORE DELETE ON research_r01_sources
            BEGIN
                SELECT RAISE(ABORT, 'R01_SOURCE_IMMUTABLE');
            END;

            CREATE TRIGGER IF NOT EXISTS research_r01_authorization_no_update
            BEFORE UPDATE ON research_gate_authorizations_v2
            BEGIN
                SELECT RAISE(ABORT, 'R01_AUTHORIZATION_IMMUTABLE');
            END;
            CREATE TRIGGER IF NOT EXISTS research_r01_authorization_no_delete
            BEFORE DELETE ON research_gate_authorizations_v2
            BEGIN
                SELECT RAISE(ABORT, 'R01_AUTHORIZATION_APPEND_ONLY');
            END;
            CREATE TRIGGER IF NOT EXISTS research_r01_run_no_delete
            BEFORE DELETE ON research_r01_runs
            BEGIN
                SELECT RAISE(ABORT, 'R01_RUN_APPEND_ONLY');
            END;
            CREATE TRIGGER IF NOT EXISTS research_r01_terminal_immutable
            BEFORE UPDATE ON research_r01_runs
            WHEN OLD.state IN (
                'PASS_WAITING_OWNER',
                'FAIL_WAITING_OWNER',
                'ERROR_WAITING_OWNER'
            )
            BEGIN
                SELECT RAISE(ABORT, 'R01_TERMINAL_IMMUTABLE');
            END;
            CREATE TRIGGER IF NOT EXISTS research_r01_state_transition_guard
            BEFORE UPDATE OF state ON research_r01_runs
            WHEN NOT (
                OLD.state='STARTING'
                AND NEW.state IN (
                    'PASS_WAITING_OWNER',
                    'FAIL_WAITING_OWNER',
                    'ERROR_WAITING_OWNER'
                )
            )
            BEGIN
                SELECT RAISE(ABORT, 'R01_STATE_TRANSITION_INVALID');
            END;

            CREATE TRIGGER IF NOT EXISTS research_authorizations_no_update
            BEFORE UPDATE ON research_authorizations
            BEGIN
                SELECT RAISE(ABORT, 'RESEARCH_AUTHORIZATION_IMMUTABLE');
            END;
            CREATE TRIGGER IF NOT EXISTS research_authorizations_no_delete
            BEFORE DELETE ON research_authorizations
            BEGIN
                SELECT RAISE(ABORT, 'RESEARCH_AUTHORIZATION_APPEND_ONLY');
            END;
            CREATE TRIGGER IF NOT EXISTS research_memory_no_update
            BEFORE UPDATE ON research_memory_events
            BEGIN
                SELECT RAISE(ABORT, 'RESEARCH_MEMORY_APPEND_ONLY');
            END;
            CREATE TRIGGER IF NOT EXISTS research_memory_no_delete
            BEFORE DELETE ON research_memory_events
            BEGIN
                SELECT RAISE(ABORT, 'RESEARCH_MEMORY_APPEND_ONLY');
            END;
            CREATE TRIGGER IF NOT EXISTS research_parent_authority_immutable
            BEFORE UPDATE OF
                research_parent_id,
                parent_strategy_id,
                parent_authority_sha256,
                parent_manifest_path,
                parent_manifest_sha256,
                feature_contract,
                owner_authorization_id,
                authorized_utc,
                hardware_snapshot_path,
                hardware_snapshot_sha256
            ON research_projects
            BEGIN
                SELECT RAISE(ABORT, 'RESEARCH_PARENT_AUTHORITY_IMMUTABLE');
            END;
            CREATE TRIGGER IF NOT EXISTS research_r00_initial_state_guard
            BEFORE INSERT ON research_projects
            WHEN NEW.current_gate='R00' AND NEW.gate_state<>'STARTING'
            BEGIN
                SELECT RAISE(ABORT, 'R00_INITIAL_STATE_MUST_BE_STARTING');
            END;
            CREATE TRIGGER IF NOT EXISTS research_r00_current_gate_guard
            BEFORE UPDATE OF current_gate ON research_projects
            WHEN OLD.current_gate='R00' AND NEW.current_gate<>'R00'
            BEGIN
                SELECT RAISE(ABORT, 'R00_CURRENT_GATE_IMMUTABLE');
            END;
            CREATE TRIGGER IF NOT EXISTS research_r00_gate_state_guard
            BEFORE UPDATE OF gate_state ON research_projects
            WHEN OLD.current_gate='R00'
              AND NEW.current_gate='R00'
              AND NOT (
                  OLD.gate_state='STARTING'
                  AND NEW.gate_state IN (
                      'PASS_WAITING_OWNER',
                      'FAIL_WAITING_OWNER',
                      'ERROR_WAITING_OWNER'
                  )
              )
            BEGIN
                SELECT RAISE(ABORT, 'R00_GATE_STATE_TRANSITION_INVALID');
            END;
            CREATE TRIGGER IF NOT EXISTS research_r00_terminal_manifest_guard
            BEFORE UPDATE OF gate_input_manifest_sha,gate_output_manifest_sha
            ON research_projects
            WHEN OLD.current_gate='R00'
              AND NEW.current_gate='R00'
              AND OLD.gate_state IN (
                  'PASS_WAITING_OWNER',
                  'FAIL_WAITING_OWNER',
                  'ERROR_WAITING_OWNER'
              )
            BEGIN
                SELECT RAISE(ABORT, 'R00_TERMINAL_AUTHORITY_IMMUTABLE');
            END;

            CREATE TABLE IF NOT EXISTS artifact_registry (
                artifact_id TEXT PRIMARY KEY,
                artifact_type TEXT NOT NULL,
                producer TEXT NOT NULL,
                owner_type TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                source_type TEXT,
                source_id TEXT,
                created_utc TEXT NOT NULL,
                updated_utc TEXT NOT NULL,
                canonical_path TEXT NOT NULL,
                runtime_paths_json TEXT NOT NULL DEFAULT '[]',
                size_bytes INTEGER NOT NULL DEFAULT 0,
                sha256 TEXT,
                status TEXT NOT NULL,
                in_use INTEGER NOT NULL DEFAULT 0 CHECK(in_use IN (0,1)),
                retention_class TEXT NOT NULL CHECK (
                    retention_class IN (
                        'ACTIVE_AUTHORITY','USER_GENERATED',
                        'TEMPORARY_RUNTIME','REGENERABLE'
                    )
                ),
                deletable INTEGER NOT NULL DEFAULT 0 CHECK(deletable IN (0,1)),
                cleanable INTEGER NOT NULL DEFAULT 0 CHECK(cleanable IN (0,1)),
                dependencies_json TEXT NOT NULL DEFAULT '[]',
                UNIQUE(canonical_path, owner_type, owner_id)
            );
            CREATE INDEX IF NOT EXISTS ix_artifact_registry_owner
            ON artifact_registry(owner_type, owner_id, artifact_type);
            CREATE INDEX IF NOT EXISTS ix_artifact_registry_retention
            ON artifact_registry(retention_class, in_use);
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
            (str(CURRENT_SCHEMA_VERSION),),
        )


def get_batch(batch_id: str, *, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    migrate_current(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM strategy_challenger_batches WHERE batch_id=?",
            (str(batch_id),),
        ).fetchone()
        if row is None:
            return None
        items = conn.execute(
            """
            SELECT source_round,source_pass,challenger_id,state
            FROM strategy_challenger_batch_items
            WHERE batch_id=?
            ORDER BY source_round,source_pass
            """,
            (str(batch_id),),
        ).fetchall()
    result = dict(row)
    result["request"] = json.loads(result.pop("request_json"))
    raw_result = result.pop("result_json")
    result["result"] = json.loads(raw_result) if raw_result else None
    result["items"] = [dict(item) for item in items]
    return result


def prepare_batch(
    batch_id: str,
    *,
    job_id: str,
    selections: list[dict[str, int]],
    challenger_ids: list[str],
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_current(path)
    if len(selections) != len(challenger_ids):
        raise ValueError("batch selection/id mismatch")
    payload = {"job_id": str(job_id), "selections": selections}
    now = utc_now()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT request_json FROM strategy_challenger_batches WHERE batch_id=?",
            (str(batch_id),),
        ).fetchone()
        encoded = json.dumps(payload, sort_keys=True)
        if existing is not None:
            if str(existing["request_json"]) != encoded:
                raise RuntimeError("CHALLENGER_BATCH_ID_COLLISION")
        else:
            conn.execute(
                """
                INSERT INTO strategy_challenger_batches(
                    batch_id,job_id,state,request_json,created_utc,updated_utc
                ) VALUES(?,?,'PREPARED',?,?,?)
                """,
                (str(batch_id), str(job_id), encoded, now, now),
            )
            for selection, challenger_id in zip(selections, challenger_ids, strict=True):
                conn.execute(
                    """
                    INSERT INTO strategy_challenger_batch_items(
                        batch_id,source_round,source_pass,challenger_id,state
                    ) VALUES(?,?,?,?, 'PREPARED')
                    """,
                    (
                        str(batch_id),
                        int(selection["round"]),
                        int(selection["pass"]),
                        str(challenger_id),
                    ),
                )
    result = get_batch(batch_id, path=path)
    if result is None:
        raise RuntimeError("CHALLENGER_BATCH_PREPARE_FAILED")
    return result


def update_batch(
    batch_id: str,
    state: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    item_state: str | None = None,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if state not in BATCH_STATES:
        raise ValueError("invalid challenger batch state")
    migrate_current(path)
    fields = ["state=?", "updated_utc=?"]
    values: list[Any] = [state, utc_now()]
    if result is not None:
        fields.append("result_json=?")
        values.append(json.dumps(result, sort_keys=True))
    if error is not None:
        fields.append("error=?")
        values.append(str(error)[:2000])
    values.append(str(batch_id))
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            f"UPDATE strategy_challenger_batches SET {', '.join(fields)} WHERE batch_id=?",
            tuple(values),
        )
        if cursor.rowcount != 1:
            raise FileNotFoundError(batch_id)
        if item_state is not None:
            conn.execute(
                "UPDATE strategy_challenger_batch_items SET state=? WHERE batch_id=?",
                (str(item_state), str(batch_id)),
            )
    result_row = get_batch(batch_id, path=path)
    if result_row is None:
        raise RuntimeError("CHALLENGER_BATCH_UPDATE_FAILED")
    return result_row


def insert_challenger_batch_rows(
    batch_id: str,
    rows: list[dict[str, Any]],
    *,
    path: Path = DATABASE_PATH,
) -> list[dict[str, Any]]:
    migrate_current(path)
    now = utc_now()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = conn.execute(
            "SELECT state FROM strategy_challenger_batches WHERE batch_id=?",
            (str(batch_id),),
        ).fetchone()
        if batch is None:
            raise FileNotFoundError(batch_id)
        if str(batch["state"]) == "COMMITTED":
            existing = conn.execute(
                """
                SELECT c.* FROM strategy_challengers c
                JOIN strategy_challenger_batch_items i
                  ON i.challenger_id=c.challenger_id
                WHERE i.batch_id=?
                ORDER BY i.source_round,i.source_pass
                """,
                (str(batch_id),),
            ).fetchall()
            return [dict(row) for row in existing]
        for payload in rows:
            prior = conn.execute(
                """
                SELECT challenger_id FROM strategy_challengers
                WHERE source_job_id=? AND source_round=? AND source_pass=?
                """,
                (
                    str(payload["source_job_id"]),
                    int(payload["source_round"]),
                    int(payload["source_pass"]),
                ),
            ).fetchone()
            if prior is not None:
                raise RuntimeError(
                    "QUALIFIED_CANDIDATE_ALREADY_REGISTERED:"
                    + str(prior["challenger_id"])
                )
        for payload in rows:
            conn.execute(
                """
                INSERT INTO strategy_challengers(
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
                ) VALUES(
                    ?,'CHALLENGER',?,?,?,?,?,?,NULL,?,?,?,?,?,?,?,
                    ?,?,?,?,?,?,?,?,'NONE',NULL
                )
                """,
                (
                    str(payload["challenger_id"]),
                    ROLE_OWNER_SELECTED,
                    str(payload["source_job_id"]),
                    int(payload["source_round"]),
                    int(payload["source_pass"]),
                    str(payload.get("created_utc") or now),
                    str(payload.get("created_utc") or now),
                    str(payload["ea_version"]),
                    str(payload["baseline_ea_sha256"]),
                    str(payload["challenger_ea_sha256"]),
                    str(payload["set_sha256"]),
                    str(payload["metadata_sha256"]),
                    str(payload["manifest_sha256"]),
                    str(payload["bundle_path"]),
                    json.dumps(payload["params"], sort_keys=True),
                    json.dumps(payload["kpi"], sort_keys=True),
                    json.dumps(payload["hard_gates"], sort_keys=True),
                    json.dumps(payload["source_request"], sort_keys=True),
                    json.dumps(payload["candidate"], sort_keys=True),
                    json.dumps(payload["provenance"], sort_keys=True),
                    str(payload["winning_xml_sha256"]),
                    str(payload["winning_sidecar_sha256"]),
                ),
            )
        conn.execute(
            """
            UPDATE strategy_challenger_batch_items
            SET state='COMMITTED'
            WHERE batch_id=?
            """,
            (str(batch_id),),
        )
        result_payload = {
            "challenger_ids": [str(row["challenger_id"]) for row in rows],
            "count": len(rows),
        }
        conn.execute(
            """
            UPDATE strategy_challenger_batches
            SET state='COMMITTED',updated_utc=?,result_json=?,error=NULL
            WHERE batch_id=?
            """,
            (now, json.dumps(result_payload, sort_keys=True), str(batch_id)),
        )
    from .challenger_store import get_challenger
    return [
        item
        for row in rows
        if (item := get_challenger(str(row["challenger_id"]), path=path)) is not None
    ]


def set_backtest_runtime_status(
    backtest_id: str,
    status: str,
    *,
    path: Path = DATABASE_PATH,
) -> None:
    if status not in {"PRESENT", "CLEANED", "UNKNOWN"}:
        raise ValueError("invalid runtime status")
    migrate_current(path)
    with connect(path) as conn:
        cursor = conn.execute(
            "UPDATE strategy_challenger_backtests SET runtime_status=? WHERE backtest_id=?",
            (status, str(backtest_id)),
        )
        if cursor.rowcount != 1:
            raise FileNotFoundError(backtest_id)


def delete_backtest_row(backtest_id: str, *, path: Path = DATABASE_PATH) -> None:
    migrate_current(path)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "DELETE FROM strategy_challenger_backtests WHERE backtest_id=?",
            (str(backtest_id),),
        )
        if cursor.rowcount != 1:
            raise FileNotFoundError(backtest_id)


def delete_challenger_row(challenger_id: str, *, path: Path = DATABASE_PATH) -> None:
    migrate_current(path)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "DELETE FROM strategy_challengers WHERE challenger_id=?",
            (str(challenger_id),),
        )
        if cursor.rowcount != 1:
            raise FileNotFoundError(challenger_id)
