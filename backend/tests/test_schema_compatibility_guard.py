from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from max_backend import artifact_api, db as db_module, main, strategy_reset
from max_backend.challenger_operations import recover_retired_challenger_deployments
from max_backend.challenger_operations_store import (
    migrate_m06,
    recover_incomplete_backtests,
)
from max_backend.challenger_store import migrate_m03
from max_backend.champion_store import migrate_m04
from max_backend.db import (
    DatabaseSchemaGuardError,
    assert_database_schema_compatible,
    database_status,
    ensure_baseline_registered,
    initialize_database,
)
from max_backend.optimizer_jobs import reconcile_optimizer_startup
from max_backend.optimizer_store import migrate_m01, migrate_m02
from max_backend.promotion_service import recover_incomplete_promotions
from max_backend.scientist_store import migrate_m05, recover_unconfirmed_requests
from max_backend.schema import CURRENT_SCHEMA_VERSION
from max_backend.strategy_reset import (
    RECOVERY_RESET_CONFIRMATION,
    STRATEGY_RESET_CONFIRMATION,
    backup_and_reset_corrupt_database,
    reset_strategy_workspace,
    database_recovery_status,
    strategy_reset_preflight,
)
from max_backend.workflow_store import migrate_current


def _create_schema_fixture(
    path: Path,
    version: int | str | None,
    *,
    metadata_table: bool = True,
    onnx_rows: bool = False,
    research_rows: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        if metadata_table:
            conn.execute("CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            if version is not None:
                conn.execute(
                    "INSERT INTO schema_meta(key,value) VALUES('schema_version',?)",
                    (str(version),),
                )
        conn.execute(
            """CREATE TABLE artifact_registry(
                artifact_id TEXT PRIMARY KEY, artifact_type TEXT NOT NULL,
                producer TEXT NOT NULL, owner_type TEXT NOT NULL, owner_id TEXT NOT NULL,
                created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL,
                canonical_path TEXT NOT NULL, status TEXT NOT NULL,
                retention_class TEXT NOT NULL
            )"""
        )
        if onnx_rows:
            conn.execute(
                "CREATE TABLE onnx_data_snapshots(snapshot_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO onnx_data_snapshots VALUES('SNAPSHOT-16','a' || printf('%063d',1))"
            )
        if research_rows:
            conn.executescript(
                """
                CREATE TABLE research_projects(research_id TEXT PRIMARY KEY, status TEXT NOT NULL);
                CREATE TABLE research_r02_outcomes(
                    outcome_id TEXT PRIMARY KEY,
                    research_id TEXT NOT NULL REFERENCES research_projects(research_id),
                    payload TEXT NOT NULL
                );
                CREATE INDEX research_outcome_research_idx
                    ON research_r02_outcomes(research_id);
                CREATE TRIGGER research_outcome_immutable
                    BEFORE DELETE ON research_r02_outcomes
                    BEGIN SELECT RAISE(ABORT,'RESEARCH_IMMUTABLE'); END;
                INSERT INTO research_projects VALUES('RESEARCH-16','COMPLETE');
                INSERT INTO research_r02_outcomes VALUES('OUTCOME-16','RESEARCH-16','retained');
                """
            )
            conn.execute(
                """INSERT INTO artifact_registry VALUES(
                    'ARTIFACT-RESEARCH-16','RESEARCH_EVIDENCE','RESEARCH_R02',
                    'RESEARCH_R02','RESEARCH-16','now','now','evidence/immutable',
                    'RETAINED','ACTIVE_AUTHORITY'
                )"""
            )


def _file_hashes(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in sorted(path.parent.iterdir()):
        if item.name == path.name or item.name.startswith(path.name + "-"):
            if item.is_file():
                result[item.name] = hashlib.sha256(item.read_bytes()).hexdigest()
    return result


def _db_snapshot(path: Path) -> dict:
    files_before = _file_hashes(path)
    uri = path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        objects = [
            tuple(row)
            for row in conn.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
            )
        ]
        tables = [
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        rows: dict[str, list[list[object]]] = {}
        for table in tables:
            escaped = table.replace('"', '""')
            table_rows = conn.execute(f'SELECT * FROM "{escaped}"').fetchall()
            normalized = [
                [value.hex() if isinstance(value, bytes) else value for value in tuple(row)]
                for row in table_rows
            ]
            rows[table] = sorted(
                normalized,
                key=lambda item: json.dumps(item, sort_keys=True, default=str),
            )
        foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
    files_after = _file_hashes(path)
    assert files_after == files_before, "read-only inspection changed database or journal bytes"
    return {
        "files": files_after,
        "objects": objects,
        "rows": rows,
        "foreign_key_check": foreign_keys,
    }


def _set_schema_version(path: Path, value: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE schema_meta SET value=? WHERE key='schema_version'",
            (value,),
        )


def _run_startup(path: Path) -> None:
    main._run_startup_migrations(path)


def _create_rich_schema_fixture(
    path: Path, root: Path, version: int = CURRENT_SCHEMA_VERSION
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    baseline_root = root / "ea" / "baseline"
    baseline_root.mkdir(parents=True)
    shutil.copy2(
        project_root / "ea" / "baseline" / "Max_MTF.mq5",
        baseline_root / "Max_MTF.mq5",
    )
    shutil.copy2(
        project_root / "ea" / "baseline" / "manifest.json",
        baseline_root / "manifest.json",
    )
    _run_startup(path)

    with sqlite3.connect(path) as conn:
        baseline_sha = str(
            conn.execute("SELECT sha256 FROM ea_baseline WHERE id=1").fetchone()[0]
        )
        conn.executescript(
            """
            CREATE TABLE onnx_data_snapshots(
                snapshot_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, payload BLOB NOT NULL
            );
            INSERT INTO onnx_data_snapshots VALUES(
                'ONNX-SNAPSHOT-PROTECTED','snapshot-sha','sealed snapshot bytes'
            );
            CREATE TABLE research_projects(
                research_id TEXT PRIMARY KEY, status TEXT NOT NULL
            );
            CREATE TABLE research_r02_outcomes(
                outcome_id TEXT PRIMARY KEY,
                research_id TEXT NOT NULL REFERENCES research_projects(research_id),
                payload TEXT NOT NULL
            );
            CREATE INDEX research_outcome_research_idx
                ON research_r02_outcomes(research_id);
            CREATE TRIGGER research_outcome_immutable
                BEFORE DELETE ON research_r02_outcomes
                BEGIN SELECT RAISE(ABORT,'RESEARCH_IMMUTABLE'); END;
            INSERT INTO research_projects VALUES('RESEARCH-PROTECTED','SEALED');
            INSERT INTO research_r02_outcomes VALUES(
                'OUTCOME-PROTECTED','RESEARCH-PROTECTED','immutable evidence'
            );
            """
        )
        conn.execute(
            """INSERT INTO optimizer_jobs(
                job_id,status,active,created_utc,updated_utc,max_rounds,
                request_json,evidence_dir
            ) VALUES('JOB-PROTECTED','COMPLETED',0,'now','now',1,'{}','artifacts/optimizer/JOB-PROTECTED')"""
        )
        conn.execute(
            """INSERT INTO optimizer_rounds(
                job_id,round_no,phase,updated_utc,state_json
            ) VALUES('JOB-PROTECTED',1,'COMPLETED','now','{}')"""
        )
        conn.execute(
            """INSERT INTO strategy_challengers(
                challenger_id,status,role_origin,source_job_id,source_round,source_pass,
                created_utc,updated_utc,ea_version,baseline_ea_sha256,bundle_path,
                params_json,kpi_json,hard_gates_json,source_request_json,winner_json,
                provenance_json,winning_xml_sha256,winning_sidecar_sha256
            ) VALUES(
                'CHALLENGER-PROTECTED','PROMOTED','OPTIMIZER_WINNER','JOB-PROTECTED',1,1,
                'now','now','2.11',?,'artifacts/challengers/CHALLENGER-PROTECTED',
                '{}','{}','{}','{}','{}','{}','xml-sha','sidecar-sha'
            )""",
            (baseline_sha,),
        )
        conn.execute(
            """INSERT INTO strategy_promotions(
                promotion_id,challenger_id,new_champion_id,state,created_utc,
                expected_challenger_manifest_sha256,before_state_json,recovery_path
            ) VALUES(
                'PROMOTION-PROTECTED','CHALLENGER-PROTECTED','STRATEGY-PROTECTED',
                'COMMITTED','now','manifest-sha','{}',
                'state/promotion_recovery/PROMOTION-PROTECTED'
            )"""
        )
        conn.execute(
            """INSERT INTO strategy_champions(
                champion_tenure_id,strategy_id,status,source_challenger_id,
                source_job_id,source_round,source_pass,params_json,kpi_json,
                hard_gates_json,champion_ea_sha256,champion_set_sha256,
                deployed_ea_sha256,deployed_ex5_sha256,tester_set_sha256,
                promoted_utc,promotion_id,authority_source,artifact_path,
                evidence_path,deployment_json
            ) VALUES(
                'TENURE-PROTECTED','STRATEGY-PROTECTED','CURRENT',
                'CHALLENGER-PROTECTED','JOB-PROTECTED',1,1,'{}','{}','{}',
                'ea-sha','set-sha','deployed-ea-sha','ex5-sha','tester-set-sha',
                'now','PROMOTION-PROTECTED','OWNER_MANUAL_STRATEGY_PROMOTION',
                'artifacts/strategy_history/TENURE-PROTECTED',
                'evidence/PROMOTION-PROTECTED','{}'
            )"""
        )
        conn.execute(
            """INSERT INTO artifact_registry(
                artifact_id,artifact_type,producer,owner_type,owner_id,
                created_utc,updated_utc,canonical_path,status,retention_class
            ) VALUES(
                'ARTIFACT-RESEARCH-PROTECTED','RESEARCH_EVIDENCE','RESEARCH_R02',
                'RESEARCH_R02','RESEARCH-PROTECTED','now','now',
                'evidence/research/RESEARCH-PROTECTED','RETAINED','ACTIVE_AUTHORITY'
            )"""
        )
        conn.execute(
            "UPDATE schema_meta SET value=? WHERE key='schema_version'",
            (str(version),),
        )


def _load_epoch_reset_script():
    script = Path(__file__).resolve().parents[2] / "scripts" / "reset_strategy_epoch.py"
    spec = importlib.util.spec_from_file_location("p0_schema_guard_epoch_reset", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fresh_install_and_supported_schema_startup_pass(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh" / "max.db"
    assert not fresh.exists()
    _run_startup(fresh)
    assert fresh.is_file()
    assert assert_database_schema_compatible(fresh)["schema_version"] == CURRENT_SCHEMA_VERSION

    # This is a fully initialized schema-15 database, not just metadata labeled 15.
    _run_startup(fresh)
    assert assert_database_schema_compatible(fresh)["schema_version"] == CURRENT_SCHEMA_VERSION


@pytest.mark.parametrize(
    "migration",
    [migrate_m01, migrate_m02, migrate_m03, migrate_m04, migrate_m05, migrate_m06, migrate_current],
    ids=["m01", "m02", "m03", "m04", "m05", "m06", "current"],
)
def test_every_direct_migration_entrypoint_blocks_schema_16_without_mutation(
    tmp_path: Path, migration: Callable[[Path], None]
) -> None:
    database = tmp_path / "schema-16.db"
    _create_schema_fixture(database, 16, onnx_rows=True, research_rows=True)
    before = _db_snapshot(database)

    with pytest.raises(DatabaseSchemaGuardError) as error:
        migration(database)

    assert error.value.status == "INCOMPATIBLE_NEWER_SCHEMA"
    assert _db_snapshot(database) == before


@pytest.mark.parametrize(
    "operation",
    [
        lambda path: recover_incomplete_promotions(path=path),
        lambda path: recover_unconfirmed_requests(path=path),
        lambda path: recover_incomplete_backtests(path),
        lambda path: recover_retired_challenger_deployments(path=path),
        lambda path: reconcile_optimizer_startup(path=path),
        lambda path: backup_and_reset_corrupt_database(
            confirmed=RECOVERY_RESET_CONFIRMATION, path=path
        ),
        lambda path: reset_strategy_workspace(
            confirmed=STRATEGY_RESET_CONFIRMATION, path=path, root=path.parent
        ),
        lambda path: _load_epoch_reset_script().reset_strategy_epoch(
            database=path,
            root=path.parent,
            dry_run=False,
            confirmation="RESET_TO_MAX_TRUE_MTF_DYNAMIC_V1",
        ),
    ],
    ids=[
        "promotion-recovery",
        "scientist-recovery",
        "backtest-recovery",
        "retirement-recovery",
        "optimizer-reconciliation",
        "corrupt-state-reset",
        "strategy-reset",
        "epoch-reset",
    ],
)
def test_direct_startup_recovery_and_reset_entrypoints_block_schema_16(
    tmp_path: Path, operation: Callable[[Path], object]
) -> None:
    database = tmp_path / "schema-16.db"
    _create_schema_fixture(database, 16, onnx_rows=True, research_rows=True)
    before = _db_snapshot(database)

    with pytest.raises(DatabaseSchemaGuardError) as error:
        operation(database)

    assert error.value.status == "INCOMPATIBLE_NEWER_SCHEMA"
    assert _db_snapshot(database) == before
    assert not (database.parent / "recovery_quarantine").exists()
    assert not (database.parent / "reset_backups").exists()


@pytest.mark.parametrize("version", [16, 17])
def test_application_startup_exposes_schema_block_and_never_offers_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int
) -> None:
    database = tmp_path / f"schema-{version}.db"
    _create_schema_fixture(database, version, onnx_rows=True, research_rows=True)
    before = _db_snapshot(database)
    monkeypatch.setattr(main, "DATABASE_PATH", database)
    monkeypatch.setattr(main, "STARTUP_SCHEMA_BLOCKER", None)
    monkeypatch.setattr(main, "RECOVERY_REQUIRED", False)
    monkeypatch.setattr(main, "RECOVERY_REASON", None)

    with TestClient(main.app) as client:
        diagnostic = client.get("/api/recovery/status")
        assert diagnostic.status_code == 200
        assert diagnostic.json()["status"] == "INCOMPATIBLE_NEWER_SCHEMA"
        assert diagnostic.json()["database_schema_version"] == version
        assert diagnostic.json()["reset_available"] is False
        assert "reset_confirmation" not in diagnostic.json()
        assert client.get("/api/health").status_code == 503
        assert client.get("/api/overview").status_code == 503
        reset = client.post(
            "/api/recovery/reset",
            json={"confirmation": RECOVERY_RESET_CONFIRMATION},
        )
        assert reset.status_code == 503
        assert reset.json()["status"] == "INCOMPATIBLE_NEWER_SCHEMA"

    assert _db_snapshot(database) == before


@pytest.mark.parametrize(
    ("version", "metadata_table"),
    [(None, False), (None, True), ("not-a-version", True), ("0", True)],
    ids=["missing-table", "missing-version", "malformed-version", "zero-version"],
)
def test_existing_database_with_invalid_schema_metadata_fails_closed(
    tmp_path: Path, version: int | str | None, metadata_table: bool
) -> None:
    database = tmp_path / "invalid-metadata.db"
    _create_schema_fixture(database, version, metadata_table=metadata_table)
    before = _db_snapshot(database)

    with pytest.raises(DatabaseSchemaGuardError) as error:
        assert_database_schema_compatible(database)

    assert error.value.status == "DATABASE_SCHEMA_METADATA_INVALID"
    assert _db_snapshot(database) == before


def test_schema_16_crash_and_retry_never_starts_migration_or_leaves_partial_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "schema-16.db"
    _create_schema_fixture(database, 16, onnx_rows=True, research_rows=True)
    before = _db_snapshot(database)
    monkeypatch.setattr(main, "DATABASE_PATH", database)
    monkeypatch.setattr(main, "STARTUP_SCHEMA_BLOCKER", None)
    monkeypatch.setattr(main, "RECOVERY_REQUIRED", False)
    monkeypatch.setattr(main, "RECOVERY_REASON", None)

    def migration_must_not_start(*_args, **_kwargs):
        pytest.fail("startup entered a migration before rejecting schema 16")

    monkeypatch.setattr(main, "initialize_database", migration_must_not_start)
    monkeypatch.setattr(
        main,
        "database_recovery_status",
        lambda **_kwargs: pytest.fail("schema mismatch was routed into automatic recovery"),
    )

    for _ in range(2):
        with TestClient(main.app) as client:
            assert client.get("/api/recovery/status").json()["status"] == "INCOMPATIBLE_NEWER_SCHEMA"
        assert _db_snapshot(database) == before


def test_supported_schema_crash_after_read_only_preflight_retries_without_partial_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "schema-15.db"
    _run_startup(database)
    before = _db_snapshot(database)
    real_preflight = main.assert_database_schema_compatible

    class SimulatedStartupCrash(RuntimeError):
        pass

    def crash_after_preflight(path: Path, *, allow_missing: bool = True) -> dict:
        result = real_preflight(path, allow_missing=allow_missing)
        raise SimulatedStartupCrash("fault injected after read-only schema preflight")

    monkeypatch.setattr(main, "assert_database_schema_compatible", crash_after_preflight)
    with pytest.raises(SimulatedStartupCrash, match="after read-only schema preflight"):
        _run_startup(database)

    assert _db_snapshot(database) == before
    monkeypatch.setattr(main, "assert_database_schema_compatible", real_preflight)
    _run_startup(database)
    after_retry = _db_snapshot(database)
    assert after_retry["objects"] == before["objects"]
    assert after_retry["rows"] == before["rows"]
    assert after_retry["foreign_key_check"] == before["foreign_key_check"]
    assert assert_database_schema_compatible(database)["schema_version"] == CURRENT_SCHEMA_VERSION


def test_invalid_schema_metadata_startup_is_diagnostic_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "missing-schema-meta.db"
    _create_schema_fixture(database, None, metadata_table=False)
    before = _db_snapshot(database)
    monkeypatch.setattr(main, "DATABASE_PATH", database)
    monkeypatch.setattr(main, "STARTUP_SCHEMA_BLOCKER", None)
    monkeypatch.setattr(main, "RECOVERY_REQUIRED", False)
    monkeypatch.setattr(main, "RECOVERY_REASON", None)
    monkeypatch.setattr(
        main,
        "database_recovery_status",
        lambda **_kwargs: pytest.fail("invalid metadata was routed into automatic recovery"),
    )

    with TestClient(main.app) as client:
        diagnostic = client.get("/api/recovery/status")
        assert diagnostic.status_code == 200
        assert diagnostic.json()["status"] == "DATABASE_SCHEMA_METADATA_INVALID"
        assert diagnostic.json()["reset_available"] is False
        assert client.post(
            "/api/recovery/reset",
            json={"confirmation": RECOVERY_RESET_CONFIRMATION},
        ).status_code == 503

    assert _db_snapshot(database) == before


def test_read_only_guard_observes_committed_wal_schema_without_mutation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "schema-16-wal.db"
    writer = sqlite3.connect(database)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        writer.execute("CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        writer.execute("INSERT INTO schema_meta VALUES('schema_version','16')")
        writer.execute("CREATE TABLE onnx_data_snapshots(snapshot_id TEXT PRIMARY KEY)")
        writer.execute("INSERT INTO onnx_data_snapshots VALUES('WAL-SNAPSHOT')")
        writer.commit()
        before = _file_hashes(database)
        assert f"{database.name}-wal" in before

        with pytest.raises(DatabaseSchemaGuardError) as error:
            assert_database_schema_compatible(database)

        assert error.value.status == "DATABASE_SCHEMA_WAL_PENDING"
        assert _file_hashes(database) == before
    finally:
        writer.close()


def test_hot_rollback_journal_is_not_replayed_during_schema_preflight(
    tmp_path: Path,
) -> None:
    database = tmp_path / "schema-16-journal.db"
    _create_schema_fixture(database, 16, onnx_rows=True, research_rows=True)
    journal = Path(str(database) + "-journal")
    journal.write_bytes(b"synthetic pending journal marker")
    before = _file_hashes(database)

    with pytest.raises(DatabaseSchemaGuardError) as error:
        assert_database_schema_compatible(database)

    assert error.value.status == "DATABASE_SCHEMA_RECOVERY_PENDING"
    assert _file_hashes(database) == before


def test_initialize_database_rejects_existing_newer_schema_without_side_effects(
    tmp_path: Path,
) -> None:
    database = tmp_path / "schema-16.db"
    _create_schema_fixture(database, 16, onnx_rows=True, research_rows=True)
    before = _db_snapshot(database)

    with pytest.raises(DatabaseSchemaGuardError):
        initialize_database(database)

    assert _db_snapshot(database) == before


def test_baseline_registration_rejects_existing_newer_schema_before_baseline_reads(
    tmp_path: Path,
) -> None:
    database = tmp_path / "schema-16.db"
    _create_schema_fixture(database, 16, onnx_rows=True, research_rows=True)
    before = _db_snapshot(database)

    with pytest.raises(DatabaseSchemaGuardError) as error:
        ensure_baseline_registered(database)

    assert error.value.status == "INCOMPATIBLE_NEWER_SCHEMA"
    assert _db_snapshot(database) == before


def test_missing_database_is_allowed_only_for_initialization(tmp_path: Path) -> None:
    database = tmp_path / "new" / "max.db"
    with pytest.raises(DatabaseSchemaGuardError) as error:
        migrate_current(database)
    assert error.value.status == "DATABASE_SCHEMA_METADATA_INVALID"
    assert not database.exists()

    initialize_database(database)
    assert database.is_file()
    assert assert_database_schema_compatible(database)["schema_version"] == 1


def test_launcher_reports_schema_guard_instead_of_generic_backend_timeout() -> None:
    launcher = Path(__file__).resolve().parents[2] / "scripts" / "run_max.ps1"
    source = launcher.read_text(encoding="utf-8")
    assert '"/api/recovery/status"' in source
    assert "Get-StartupSchemaBlocker" in source
    assert '"INCOMPATIBLE_NEWER_SCHEMA"' in source
    assert "No reset or downgrade was attempted." in source
    assert "Backend startup blocked:" in source


@pytest.mark.parametrize("version", [16, 17, 23])
def test_database_status_rejects_newer_schema_before_rw_open_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int
) -> None:
    root = tmp_path / "max"
    database = root / "state" / "max.db"
    _create_rich_schema_fixture(database, root, version)
    before = _db_snapshot(database)

    def unexpected_rw_open(_path: Path):
        pytest.fail("database_status opened a read-write connection before schema rejection")

    monkeypatch.setattr(db_module, "connect", unexpected_rw_open)
    result = database_status(database)

    assert result["status"] == "INCOMPATIBLE_NEWER_SCHEMA"
    assert result["database_schema_version"] == version
    assert result["supported_schema_version"] == CURRENT_SCHEMA_VERSION
    assert result["reset_available"] is False
    assert _db_snapshot(database) == before
    assert not (root / "recovery_quarantine").exists()
    assert not (root / "reset_backups").exists()


@pytest.mark.parametrize("version", [16, 17, 23])
def test_database_recovery_status_separates_schema_from_physical_integrity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int
) -> None:
    root = tmp_path / "max"
    database = root / "state" / "max.db"
    _create_rich_schema_fixture(database, root, version)
    before = _db_snapshot(database)

    def unexpected_rw_open(_path: Path):
        pytest.fail("database_recovery_status opened a read-write connection before schema rejection")

    monkeypatch.setattr(strategy_reset, "_connect", unexpected_rw_open)
    result = database_recovery_status(path=database)

    assert result["status"] == "INCOMPATIBLE_NEWER_SCHEMA"
    assert result["reason"] == "INCOMPATIBLE_NEWER_SCHEMA"
    assert result["database_schema_version"] == version
    assert result["supported_schema_version"] == CURRENT_SCHEMA_VERSION
    assert result["application_schema_compatibility"] == "INCOMPATIBLE"
    assert result["physical_integrity"] == "NOT_CHECKED"
    assert result["reset_available"] is False
    assert _db_snapshot(database) == before
    assert not (root / "recovery_quarantine").exists()
    assert not (root / "reset_backups").exists()


@pytest.mark.parametrize("version", [16, 17, 23])
def test_strategy_reset_preflight_guards_before_readwrite_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int
) -> None:
    root = tmp_path / "max"
    database = root / "state" / "max.db"
    _create_rich_schema_fixture(database, root, version)
    before = _db_snapshot(database)

    def unexpected_rw_open(_path: Path):
        pytest.fail("reset preflight reached _connect before rejecting schema")

    monkeypatch.setattr(strategy_reset, "_connect", unexpected_rw_open)
    with pytest.raises(DatabaseSchemaGuardError) as error:
        strategy_reset_preflight(path=database, root=root)

    assert error.value.status == "INCOMPATIBLE_NEWER_SCHEMA"
    assert error.value.database_schema_version == version
    assert _db_snapshot(database) == before
    assert not (root / "recovery_quarantine").exists()
    assert not (root / "reset_backups").exists()


def test_strategy_reset_low_level_connect_is_guarded_before_sqlite_rw_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "max"
    database = root / "state" / "max.db"
    _create_rich_schema_fixture(database, root, 16)
    before = _db_snapshot(database)
    real_connect = sqlite3.connect
    calls: list[tuple[str, bool]] = []

    def track_connection(database_arg, *args, **kwargs):
        uri = bool(kwargs.get("uri"))
        calls.append((str(database_arg), uri))
        if not uri:
            pytest.fail("schema 16 reached SQLite through a read-write connection")
        return real_connect(database_arg, *args, **kwargs)

    monkeypatch.setattr(strategy_reset.sqlite3, "connect", track_connection)
    with pytest.raises(DatabaseSchemaGuardError) as error:
        strategy_reset._connect(database)

    assert error.value.status == "INCOMPATIBLE_NEWER_SCHEMA"
    assert calls and all(uri for _, uri in calls)
    assert _db_snapshot(database) == before


def test_all_three_direct_entrypoints_preserve_supported_schema_15_semantics(
    tmp_path: Path,
) -> None:
    root = tmp_path / "max"
    database = root / "state" / "max.db"
    _create_rich_schema_fixture(database, root, CURRENT_SCHEMA_VERSION)
    before = _db_snapshot(database)

    status = database_status(database)
    recovery = database_recovery_status(path=database)
    preflight = strategy_reset_preflight(path=database, root=root)

    assert status == {
        "status": "READY",
        "schema_version": CURRENT_SCHEMA_VERSION,
        "baseline_status": "BASELINE_NOT_CHAMPION",
    }
    assert recovery["status"] == "READY"
    assert recovery["reason"] == "DATABASE_INTEGRITY_OK"
    assert recovery["application_schema_compatibility"] == "SUPPORTED"
    assert recovery["application_schema_version"] == CURRENT_SCHEMA_VERSION
    assert recovery["supported_schema_version"] == CURRENT_SCHEMA_VERSION
    assert recovery["physical_integrity"] == "ok"
    assert preflight["status"] == "READY"
    assert preflight["schema_version"] == str(CURRENT_SCHEMA_VERSION)
    assert preflight["confirmation_required"] == STRATEGY_RESET_CONFIRMATION
    assert _db_snapshot(database) == before


@pytest.mark.parametrize("metadata_state", ["missing", "malformed"])
def test_direct_status_and_preflight_calls_fail_closed_on_invalid_schema_metadata(
    tmp_path: Path, metadata_state: str
) -> None:
    root = tmp_path / "max"
    database = root / "state" / "max.db"
    _create_rich_schema_fixture(database, root, CURRENT_SCHEMA_VERSION)
    with sqlite3.connect(database) as conn:
        if metadata_state == "missing":
            conn.execute("DELETE FROM schema_meta WHERE key='schema_version'")
        else:
            conn.execute(
                "UPDATE schema_meta SET value='15x' WHERE key='schema_version'"
            )
    before = _db_snapshot(database)

    status = database_status(database)
    recovery = database_recovery_status(path=database)
    with pytest.raises(DatabaseSchemaGuardError) as error:
        strategy_reset_preflight(path=database, root=root)

    assert status["status"] == "DATABASE_SCHEMA_METADATA_INVALID"
    assert recovery["status"] == "DATABASE_SCHEMA_METADATA_INVALID"
    assert recovery["reset_available"] is False
    assert recovery["physical_integrity"] == "NOT_CHECKED"
    assert error.value.status == "DATABASE_SCHEMA_METADATA_INVALID"
    assert _db_snapshot(database) == before
    assert not (root / "recovery_quarantine").exists()
    assert not (root / "reset_backups").exists()


def test_direct_entrypoints_refuse_wal_sidecars_without_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "max"
    database = root / "state" / "max.db"
    _create_rich_schema_fixture(database, root, 16)
    writer = sqlite3.connect(database)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        writer.execute(
            "UPDATE onnx_data_snapshots SET payload='committed in WAL' "
            "WHERE snapshot_id='ONNX-SNAPSHOT-PROTECTED'"
        )
        writer.commit()
        before_files = _file_hashes(database)
        assert f"{database.name}-wal" in before_files
        assert f"{database.name}-shm" in before_files

        status = database_status(database)
        recovery = database_recovery_status(path=database)
        with pytest.raises(DatabaseSchemaGuardError) as error:
            strategy_reset_preflight(path=database, root=root)

        assert status["status"] == "DATABASE_SCHEMA_WAL_PENDING"
        assert recovery["status"] == "DATABASE_SCHEMA_WAL_PENDING"
        assert recovery["physical_integrity"] == "NOT_CHECKED"
        assert error.value.status == "DATABASE_SCHEMA_WAL_PENDING"
        assert _file_hashes(database) == before_files
        assert not (root / "recovery_quarantine").exists()
        assert not (root / "reset_backups").exists()
    finally:
        writer.close()


def test_direct_entrypoints_do_not_replay_pending_rollback_journal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "max"
    database = root / "state" / "max.db"
    _create_rich_schema_fixture(database, root, 16)
    before = _db_snapshot(database)
    journal = Path(str(database) + "-journal")
    journal.write_bytes(b"synthetic pending rollback journal")
    before_files = _file_hashes(database)

    status = database_status(database)
    recovery = database_recovery_status(path=database)
    with pytest.raises(DatabaseSchemaGuardError) as error:
        strategy_reset_preflight(path=database, root=root)

    assert status["status"] == "DATABASE_SCHEMA_RECOVERY_PENDING"
    assert recovery["status"] == "DATABASE_SCHEMA_RECOVERY_PENDING"
    assert recovery["physical_integrity"] == "NOT_CHECKED"
    assert error.value.status == "DATABASE_SCHEMA_RECOVERY_PENDING"
    assert _file_hashes(database) == before_files
    assert before["files"][database.name] == before_files[database.name]
    assert not (root / "recovery_quarantine").exists()
    assert not (root / "reset_backups").exists()


@pytest.mark.parametrize("version", [16, 17])
def test_repeated_direct_schema_blocks_are_idempotent_and_non_mutating(
    tmp_path: Path, version: int
) -> None:
    root = tmp_path / "max"
    database = root / "state" / "max.db"
    _create_rich_schema_fixture(database, root, version)
    before = _db_snapshot(database)

    for _ in range(3):
        assert database_status(database)["status"] == "INCOMPATIBLE_NEWER_SCHEMA"
        assert database_recovery_status(path=database)["status"] == "INCOMPATIBLE_NEWER_SCHEMA"
        with pytest.raises(DatabaseSchemaGuardError) as error:
            strategy_reset_preflight(path=database, root=root)
        assert error.value.status == "INCOMPATIBLE_NEWER_SCHEMA"
        assert _db_snapshot(database) == before

    assert not (root / "recovery_quarantine").exists()
    assert not (root / "reset_backups").exists()


def test_strategy_reset_preflight_api_returns_conflict_for_newer_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "max"
    database = root / "state" / "max.db"
    _create_rich_schema_fixture(database, root, 16)
    before = _db_snapshot(database)

    monkeypatch.setattr(
        artifact_api,
        "strategy_reset_preflight",
        lambda: strategy_reset_preflight(path=database, root=root),
    )
    api = FastAPI()
    api.include_router(artifact_api.router)
    with TestClient(api) as client:
        response = client.get("/api/artifacts/strategy-reset/preflight")

    assert response.status_code == 409
    assert "Database schema 16" in response.json()["detail"]
    assert f"supports ({CURRENT_SCHEMA_VERSION})" in response.json()["detail"]
    assert _db_snapshot(database) == before
