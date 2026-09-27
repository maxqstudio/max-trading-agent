from __future__ import annotations

from pathlib import Path

import pytest

from max_backend.db import database_status, ensure_baseline_registered, initialize_database
from max_backend.optimizer_store import (
    create_job,
    get_job,
    get_round,
    get_rounds,
    migrate_m01,
    optimizer_database_status,
    update_job,
    upsert_round,
)


def request_payload() -> dict:
    return {
        "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V1",
        "max_rounds": 3,
        "symbol": "XAUUSD.m",
        "relative_symbol": "EURUSD.m",
        "period": "H1",
        "from_date": "2026.01.01",
        "to_date": "2026.02.01",
        "ea": {
            "sha256": "b5555f6741c60af3b60e920105caec859f7acf106c10f2ee80a1bdada50aba31",
        },
        "scientist_assist": False,
    }


def m00_db(path: Path) -> None:
    initialize_database(path)
    ensure_baseline_registered(path)


def test_m00_to_m01_migration_preserves_baseline(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    m00_db(db)
    before = database_status(db)
    assert before["status"] == "READY"
    assert before["schema_version"] == 1

    migrate_m01(db)
    after = optimizer_database_status(db)
    assert after == {
        "status": "READY",
        "schema_version": 2,
        "baseline_status": "BASELINE_NOT_CHAMPION",
    }


def test_single_active_optimizer_constraint(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    m00_db(db)
    migrate_m01(db)
    evidence = tmp_path / "evidence"

    first = create_job(request_payload(), evidence_root=evidence, path=db)
    assert first["active"] is True

    with pytest.raises(RuntimeError, match="already active"):
        create_job(request_payload(), evidence_root=evidence, path=db)

    update_job(first["job_id"], active=False, status="STOPPED", path=db)
    second = create_job(request_payload(), evidence_root=evidence, path=db)
    assert second["job_id"] != first["job_id"]


def test_frozen_request_persists_across_restart(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    m00_db(db)
    migrate_m01(db)
    request = request_payload()
    job = create_job(request, evidence_root=tmp_path / "evidence", path=db)

    request["symbol"] = "MUTATED_AFTER_START"
    reloaded = get_job(job["job_id"], path=db)

    assert reloaded is not None
    assert reloaded["request"]["symbol"] == "XAUUSD.m"
    assert reloaded["request"]["ea"]["sha256"].startswith("b5555f67")


def test_round_transitions_survive_restart(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    m00_db(db)
    migrate_m01(db)
    job = create_job(request_payload(), evidence_root=tmp_path / "evidence", path=db)

    upsert_round(
        job["job_id"],
        1,
        phase="PREPARED",
        state={"search_space": {"a": 1}},
        path=db,
    )
    upsert_round(
        job["job_id"],
        1,
        phase="MT5_RUNNING",
        state={"search_space": {"a": 1}, "prelaunch_report_snapshot": []},
        path=db,
    )
    update_job(
        job["job_id"],
        status="MT5_RUNNING",
        current_round=1,
        path=db,
    )

    reloaded = get_round(job["job_id"], 1, path=db)
    assert reloaded is not None
    assert reloaded["phase"] == "MT5_RUNNING"
    assert reloaded["state"]["prelaunch_report_snapshot"] == []
    assert get_job(job["job_id"], path=db)["current_round"] == 1


def test_winner_persistence_is_not_champion_state(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    m00_db(db)
    migrate_m01(db)
    job = create_job(request_payload(), evidence_root=tmp_path / "evidence", path=db)
    winner = {
        "round": 1,
        "mt5_pass": 7,
        "profit_factor": 1.2,
        "recovery_factor": 0.4,
        "mean_r": 0.1,
        "weighted_r": 0.2,
    }

    update_job(
        job["job_id"],
        status="ELIGIBLE_WINNER_FOUND",
        active=False,
        terminal_result="ELIGIBLE_WINNER_FOUND",
        winner=winner,
        mark_completed=True,
        path=db,
    )
    reloaded = get_job(job["job_id"], path=db)

    assert reloaded["winner"] == winner
    assert reloaded["terminal_result"] == "ELIGIBLE_WINNER_FOUND"
    assert "champion" not in reloaded


def test_stop_and_resume_state_persistence(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    m00_db(db)
    migrate_m01(db)
    job = create_job(request_payload(), evidence_root=tmp_path / "evidence", path=db)

    update_job(
        job["job_id"],
        status="WAITING_FOR_REPORT",
        active=True,
        current_round=1,
        path=db,
    )
    assert get_job(job["job_id"], path=db)["status"] == "WAITING_FOR_REPORT"

    update_job(
        job["job_id"],
        status="RESUMING",
        active=True,
        current_round=1,
        path=db,
    )
    assert get_job(job["job_id"], path=db)["status"] == "RESUMING"

    update_job(
        job["job_id"],
        status="STOPPED",
        active=False,
        mark_stopped=True,
        mark_completed=True,
        path=db,
    )
    stopped = get_job(job["job_id"], path=db)
    assert stopped["status"] == "STOPPED"
    assert stopped["active"] is False
    assert stopped["stopped_utc"]
    assert stopped["completed_utc"]


def test_round_evidence_indexes_persist(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    m00_db(db)
    migrate_m01(db)
    job = create_job(request_payload(), evidence_root=tmp_path / "evidence", path=db)

    upsert_round(
        job["job_id"],
        1,
        phase="PARSED",
        state={"optimizer_run_nonce": 123},
        report_path="r.xml",
        report_sha256="abc",
        sidecar_path="m.csv",
        sidecar_sha256="def",
        parsed_passes=4,
        eligible_passes=1,
        winner_pass=2,
        path=db,
    )

    rounds = get_rounds(job["job_id"], path=db)
    assert len(rounds) == 1
    assert rounds[0]["phase"] == "PARSED"
    assert rounds[0]["report_sha256"] == "abc"
    assert rounds[0]["sidecar_sha256"] == "def"
    assert rounds[0]["winner_pass"] == 2
