from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from max_backend import strategy_reset
from max_backend.challenger_operations_store import migrate_m06
from max_backend.champion_store import current_champion_summary, migrate_m04
from max_backend.config import ROOT
from max_backend.db import ensure_baseline_registered, initialize_database
from max_backend.scientist_store import migrate_m05
from max_backend.schema import CURRENT_SCHEMA_VERSION
from max_backend.workflow_store import migrate_current


def _current_database(path: Path, root: Path) -> None:
    baseline = root / "ea" / "baseline"
    baseline.mkdir(parents=True)
    shutil.copy2(ROOT / "ea" / "baseline" / "Max_MTF.mq5", baseline / "Max_MTF.mq5")
    shutil.copy2(ROOT / "ea" / "baseline" / "manifest.json", baseline / "manifest.json")
    initialize_database(path)
    ensure_baseline_registered(path)
    migrate_m04(path)
    migrate_m05(path)
    migrate_m06(path)
    migrate_current(path)


def _seed_corrupt_strategy_state(path: Path, root: Path) -> None:
    generated_roots = (
        root / "artifacts" / "optimizer" / "JOB-RESET",
        root / "artifacts" / "challengers" / "CH-RESET",
        root / "artifacts" / "backtests" / "BT-RESET",
        root / "artifacts" / "challenger_operations" / "RET-RESET",
        root / "artifacts" / "strategy_history" / "TENURE-RESET",
        root / "state" / "promotion_recovery" / "PROMO-RESET",
        root / "ea" / "champion" / "current",
    )
    for directory in generated_roots:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "generated.bin").write_bytes(b"generated")

    with sqlite3.connect(path) as conn:
        baseline_sha = conn.execute(
            "SELECT sha256 FROM ea_baseline WHERE id=1"
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO optimizer_jobs(
                job_id,status,active,created_utc,updated_utc,max_rounds,
                request_json,evidence_dir
            ) VALUES('JOB-RESET','COMPLETED',0,'now','now',1,'{','artifacts/optimizer/JOB-RESET')
            """
        )
        conn.execute(
            """
            INSERT INTO optimizer_rounds(
                job_id,round_no,phase,updated_utc,state_json
            ) VALUES('JOB-RESET',1,'COMPLETED','now','{')
            """
        )
        conn.execute(
            """
            INSERT INTO strategy_challengers(
                challenger_id,status,role_origin,source_job_id,source_round,source_pass,
                created_utc,updated_utc,ea_version,baseline_ea_sha256,bundle_path,
                params_json,kpi_json,hard_gates_json,source_request_json,winner_json,
                provenance_json,winning_xml_sha256,winning_sidecar_sha256
            ) VALUES(
                'CH-RESET','PROMOTED','OPTIMIZER_WINNER','JOB-RESET',1,1,
                'now','now','2.11',?,'artifacts/challengers/CH-RESET',
                '{','{','{','{','{','{','deadbeef','deadbeef'
            )
            """,
            (baseline_sha,),
        )
        conn.execute(
            """
            INSERT INTO strategy_promotions(
                promotion_id,challenger_id,new_champion_id,state,created_utc,
                expected_challenger_manifest_sha256,before_state_json,recovery_path
            ) VALUES(
                'PROMO-RESET','CH-RESET','STRAT-RESET','COMMITTED','now',
                'deadbeef','{','state/promotion_recovery/PROMO-RESET'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO strategy_champions(
                champion_tenure_id,strategy_id,status,source_challenger_id,
                source_job_id,source_round,source_pass,params_json,kpi_json,
                hard_gates_json,champion_ea_sha256,champion_set_sha256,
                deployed_ea_sha256,deployed_ex5_sha256,tester_set_sha256,
                promoted_utc,promotion_id,authority_source,artifact_path,
                evidence_path,deployment_json
            ) VALUES(
                'TENURE-RESET','STRAT-RESET','CURRENT','CH-RESET','JOB-RESET',
                1,1,'{','{','{','deadbeef','deadbeef','deadbeef','deadbeef',
                'deadbeef','now','PROMO-RESET','OWNER_MANUAL_STRATEGY_PROMOTION',
                'artifacts/strategy_history/TENURE-RESET','evidence/m04/PROMO-RESET','{'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO strategy_challenger_backtests(
                backtest_id,challenger_id,state,created_utc,source_manifest_sha256,
                request_json,ea_sha256,set_sha256,evidence_path
            ) VALUES('BT-RESET','CH-RESET','COMPLETED','now','deadbeef','{',
                    'deadbeef','deadbeef','artifacts/backtests/BT-RESET')
            """
        )
        conn.execute(
            """
            INSERT INTO strategy_challenger_retirements(
                retirement_id,challenger_id,state,before_status,after_status,
                expected_manifest_sha256,created_utc,evidence_path,before_state_json
            ) VALUES('RET-RESET','CH-RESET','COMMITTED','CHALLENGER','RETIRED',
                    'deadbeef','now','artifacts/challenger_operations/RET-RESET','{')
            """
        )
        conn.execute(
            """
            INSERT INTO strategy_challenger_batches(
                batch_id,job_id,state,request_json,created_utc,updated_utc
            ) VALUES('BATCH-RESET','JOB-RESET','COMMITTED','{','now','now')
            """
        )
        conn.execute(
            """
            INSERT INTO strategy_challenger_batch_items(
                batch_id,source_round,source_pass,challenger_id,state
            ) VALUES('BATCH-RESET',1,1,'CH-RESET','COMMITTED')
            """
        )
        conn.execute(
            "INSERT INTO scientist_threads(thread_id,created_utc,updated_utc,title) "
            "VALUES('THREAD-RESET','now','now','test')"
        )
        conn.execute(
            """
            INSERT INTO scientist_chat_requests(
                request_id,thread_id,state,created_utc,updated_utc
            ) VALUES('REQ-RESET','THREAD-RESET','COMPLETED','now','now')
            """
        )
        conn.execute(
            """
            INSERT INTO scientist_messages(
                message_id,thread_id,sequence,role,content,created_utc,
                evidence_refs_json,request_id
            ) VALUES('MSG-RESET','THREAD-RESET',1,'user','test','now','{','REQ-RESET')
            """
        )
        conn.execute(
            """
            INSERT INTO artifact_registry(
                artifact_id,artifact_type,producer,owner_type,owner_id,created_utc,
                updated_utc,canonical_path,status,retention_class,deletable,cleanable
            ) VALUES(
                'ART-BASELINE','BASELINE_EA','STRATEGY_AUTHORITY','STRATEGY_BASELINE',
                'Max_MTF','now','now',?,'ACTIVE','ACTIVE_AUTHORITY',0,0
            )
            """,
            (str(root / "ea" / "baseline" / "Max_MTF.mq5"),),
        )
        conn.execute(
            """
            INSERT INTO artifact_registry(
                artifact_id,artifact_type,producer,owner_type,owner_id,created_utc,
                updated_utc,canonical_path,status,retention_class,deletable,cleanable
            ) VALUES(
                'ART-CHAMPION','CHAMPION_EA','STRATEGY','CHAMPION','STRAT-RESET',
                'now','now',?,'ACTIVE','ACTIVE_AUTHORITY',0,0
            )
            """,
            (str(root / "ea" / "champion" / "current" / "Max_MTF.mq5"),),
        )


@pytest.fixture
def reset_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "max"
    database = root / "state" / "max.db"
    _current_database(database, root)
    _seed_corrupt_strategy_state(database, root)
    monkeypatch.setattr(
        strategy_reset,
        "detect_mt5",
        lambda: {"status": "UNAVAILABLE", "reason": "TEST_FIXTURE"},
    )
    monkeypatch.setattr(strategy_reset, "_settings_fingerprint", lambda: {"fixture": {"sha256": "stable"}})
    return database, root


def test_preflight_and_reset_handle_malformed_generated_json_and_current_champion(
    reset_environment: tuple[Path, Path],
) -> None:
    database, root = reset_environment
    preflight = strategy_reset.strategy_reset_preflight(path=database, root=root)
    assert preflight["status"] == "READY"
    assert preflight["current_champion_count"] == 1
    assert preflight["generated_artifact_rows"] == 1
    assert preflight["confirmation_required"] == strategy_reset.STRATEGY_RESET_CONFIRMATION

    baseline_before = preflight["baseline"]
    result = strategy_reset.reset_strategy_workspace(
        confirmed=strategy_reset.STRATEGY_RESET_CONFIRMATION,
        path=database,
        root=root,
    )

    backup = Path(result["backup"]["path"])
    assert backup.is_file()
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == result["backup"]["sha256"]
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM strategy_champions").fetchone()[0] == 1
    with sqlite3.connect(database) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM strategy_champions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM strategy_challengers").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM optimizer_jobs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM strategy_challenger_backtests").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM artifact_registry").fetchone()[0] == 1
        assert conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == str(CURRENT_SCHEMA_VERSION)
        assert conn.execute("SELECT sha256 FROM ea_baseline WHERE id=1").fetchone()[0] == baseline_before["sha256"]
    assert result["current_champion_count"] == 0
    assert result["generated_artifact_rows"] == 0
    assert result["baseline_preserved"] is True
    assert all(
        not (root / relative).exists() or not any((root / relative).iterdir())
        for relative in strategy_reset._PROJECT_ROOTS
    )


def test_reset_rejects_wrong_confirmation_and_active_optimizer_without_mutation(
    reset_environment: tuple[Path, Path],
) -> None:
    database, root = reset_environment
    with pytest.raises(strategy_reset.StrategyResetError, match="EXPLICIT_STRATEGY_WORKSPACE_RESET_CONFIRMATION_REQUIRED"):
        strategy_reset.reset_strategy_workspace(confirmed="WRONG", path=database, root=root)
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE optimizer_jobs SET active=1 WHERE job_id='JOB-RESET'")

    preflight = strategy_reset.strategy_reset_preflight(path=database, root=root)
    assert preflight["status"] == "BLOCKED"
    assert preflight["blockers"]["optimizer_jobs"] == 1
    with pytest.raises(strategy_reset.StrategyResetError, match="STRATEGY_WORKSPACE_RESET_BLOCKED"):
        strategy_reset.reset_strategy_workspace(
            confirmed=strategy_reset.STRATEGY_RESET_CONFIRMATION,
            path=database,
            root=root,
        )
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM strategy_champions").fetchone()[0] == 1
    assert not (database.parent / "reset_backups").exists()


def test_recovery_quarantines_unopenable_database_and_bootstraps_current_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "max"
    database = root / "state" / "max.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"not a sqlite database")
    expected_sha = hashlib.sha256(database.read_bytes()).hexdigest()
    monkeypatch.setattr(strategy_reset, "_settings_fingerprint", lambda: {"fixture": {"sha256": "stable"}})

    result = strategy_reset.backup_and_reset_corrupt_database(
        confirmed=strategy_reset.RECOVERY_RESET_CONFIRMATION,
        path=database,
    )

    quarantined = Path(result["quarantine"]) / database.name
    assert quarantined.is_file()
    assert hashlib.sha256(quarantined.read_bytes()).hexdigest() == expected_sha
    assert result["status"] == "RECOVERED_EMPTY_OPERATIONAL_STATE"
    assert strategy_reset.database_recovery_status(path=database)["status"] == "READY"
    with sqlite3.connect(database) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == str(CURRENT_SCHEMA_VERSION)
        assert conn.execute("SELECT status FROM ea_baseline WHERE id=1").fetchone()[0] == "BASELINE_NOT_CHAMPION"
        assert conn.execute("SELECT COUNT(*) FROM optimizer_jobs").fetchone()[0] == 0


def test_recovery_refuses_healthy_database_and_wrong_confirmation(
    reset_environment: tuple[Path, Path],
) -> None:
    database, _root = reset_environment
    with pytest.raises(strategy_reset.StrategyResetError, match="EXPLICIT_CORRUPT_STATE_RECOVERY_CONFIRMATION_REQUIRED"):
        strategy_reset.backup_and_reset_corrupt_database(
            confirmed="WRONG",
            path=database,
        )
    with pytest.raises(strategy_reset.StrategyResetError, match="DATABASE_RECOVERY_NOT_REQUIRED"):
        strategy_reset.backup_and_reset_corrupt_database(
            confirmed=strategy_reset.RECOVERY_RESET_CONFIRMATION,
            path=database,
        )


def test_champion_summary_does_not_decode_or_scan_current_artifacts(
    reset_environment: tuple[Path, Path],
) -> None:
    database, _root = reset_environment
    summary = current_champion_summary(path=database)
    assert summary["current"] == {"strategy_id": "STRAT-RESET", "status": "CURRENT"}
    assert summary["integrity"]["status"] == "NOT_CHECKED"
