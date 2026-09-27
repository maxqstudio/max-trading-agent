from __future__ import annotations

import json
from pathlib import Path

from max_backend.db import ensure_baseline_registered, initialize_database
from max_backend.optimizer_store import (
    create_job,
    get_job,
    get_round,
    migrate_m01,
    migrate_m02,
    optimizer_database_status,
    upsert_round,
)


def m01_request() -> dict:
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


def make_m01_db(path: Path) -> tuple[str, str]:
    initialize_database(path)
    ensure_baseline_registered(path)
    migrate_m01(path)
    job = create_job(
        m01_request(),
        evidence_root=path.parent / "evidence",
        path=path,
    )
    upsert_round(
        job["job_id"],
        1,
        phase="PARSED",
        state={
            "phase": "PARSED",
            "search_space": {"authority": "m01"},
            "optimizer_run_nonce": 123,
        },
        report_path="Max_MTF.xml",
        report_sha256="reportsha",
        sidecar_path="Max_MTF_metrics.csv",
        sidecar_sha256="metricsha",
        parsed_passes=2,
        eligible_passes=0,
        winner_pass=None,
        path=path,
    )
    return job["job_id"], json.dumps(get_job(job["job_id"], path=path), sort_keys=True)


def test_m01_to_m02_no_bump_migration_preserves_accepted_rows(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    job_id, before_job = make_m01_db(db)
    before_round = json.dumps(get_round(job_id, 1, path=db), sort_keys=True)

    migrate_m02(db)
    migrate_m02(db)

    assert optimizer_database_status(db) == {
        "status": "READY",
        "schema_version": 2,
        "baseline_status": "BASELINE_NOT_CHAMPION",
    }
    assert json.dumps(get_job(job_id, path=db), sort_keys=True) == before_job
    assert json.dumps(get_round(job_id, 1, path=db), sort_keys=True) == before_round


def test_m02_transition_checkpoint_persists_in_existing_round_state(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    job_id, _ = make_m01_db(db)
    migrate_m02(db)

    original = get_round(job_id, 1, path=db)
    assert original is not None
    transition = {
        "transition_id": f"{job_id}:1:2",
        "status": "COMMITTED",
        "proposal_attempts": 1,
        "actual_provider_calls": 1,
        "decision": {
            "mode": "SCIENTIST_PROPOSAL",
            "accepted": True,
            "effective_ranges": {"InpEntryThreshold": {"start": 0.18, "step": 0.02, "stop": 0.20}},
        },
    }
    state = dict(original["state"])
    state["scientist_transition"] = transition
    upsert_round(
        job_id,
        1,
        phase="PARSED",
        state=state,
        path=db,
    )

    reloaded = get_round(job_id, 1, path=db)
    assert reloaded is not None
    assert reloaded["phase"] == "PARSED"
    assert reloaded["state"]["scientist_transition"] == transition
    assert reloaded["report_sha256"] == "reportsha"
    assert reloaded["sidecar_sha256"] == "metricsha"
    assert reloaded["parsed_passes"] == 2
    assert reloaded["eligible_passes"] == 0
