from __future__ import annotations

import json
from pathlib import Path

import pytest

from max_backend.challenger_store import (
    challenger_database_status,
    finalize_challenger,
    get_challenger,
    get_challenger_by_source,
    list_challengers,
    migrate_m03,
    reserve_challenger,
)
from max_backend.db import connect, ensure_baseline_registered, initialize_database
from max_backend.mtf_geometry import STRATEGY_CONTRACT, resolve_strategy_geometry
from max_backend.optimizer_store import (
    create_job,
    get_job,
    get_round,
    migrate_m02,
    update_job,
    upsert_round,
)


EA_SHA = "b5555f6741c60af3b60e920105caec859f7acf106c10f2ee80a1bdada50aba31"


def request_payload() -> dict:
    return {
        "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V3",
        "strategy_contract": STRATEGY_CONTRACT,
        "strategy_geometry": resolve_strategy_geometry("H1"),
        "max_rounds": 1,
        "symbol": "XAUUSD.m",
        "relative_symbol": "EURUSD.m",
        "period": "H1",
        "from_date": "2026.08.01",
        "to_date": "2026.08.31",
        "ea": {"sha256": EA_SHA, "version": "2.10"},
        "scientist_assist": False,
    }


def make_schema2_db(path: Path) -> tuple[str, str, str]:
    initialize_database(path)
    ensure_baseline_registered(path)
    migrate_m02(path)
    job = create_job(
        request_payload(),
        evidence_root=path.parent / "optimizer",
        path=path,
    )
    upsert_round(
        job["job_id"],
        1,
        phase="PARSED",
        state={"phase": "PARSED", "optimizer_run_nonce": 77},
        report_path="Max_MTF.xml",
        report_sha256="xmlsha",
        sidecar_path="Max_MTF_metrics.csv",
        sidecar_sha256="sidecarsha",
        parsed_passes=2,
        eligible_passes=1,
        winner_pass=0,
        path=path,
    )
    return (
        job["job_id"],
        json.dumps(get_job(job["job_id"], path=path), sort_keys=True),
        json.dumps(get_round(job["job_id"], 1, path=path), sort_keys=True),
    )


def challenger_payload(job_id: str, challenger_id: str = "STRAT-20260922-101450-R01-P0") -> dict:
    params = {f"P{i:02d}": i for i in range(16)}
    return {
        "challenger_id": challenger_id,
        "source_job_id": job_id,
        "source_round": 1,
        "source_pass": 0,
        "created_utc": "2026-09-22T10:15:00+00:00",
        "ea_version": "2.10",
        "baseline_ea_sha256": EA_SHA,
        "bundle_path": f"artifacts/strategy_challengers/{challenger_id}",
        "params": params,
        "kpi": {
            "profit_factor": 1.5,
            "recovery_factor": 1.0,
            "mean_r": 0.2,
            "weighted_r": 0.2,
            "trades": 25,
            "required_trades": 20,
        },
        "hard_gates": {
            "minimum_trades": 20,
            "min_profit_factor": 1.0,
            "min_recovery_factor": 0.0,
            "min_expectancy_r": 0.0,
            "min_weighted_r": 0.0,
        },
        "source_request": request_payload(),
        "winner": {"job_id": job_id, "round": 1, "mt5_pass": 0},
        "provenance": {"run_nonce": 77},
        "winning_xml_sha256": "xmlsha",
        "winning_sidecar_sha256": "sidecarsha",
    }


def test_schema2_to_schema3_migration_preserves_optimizer_history(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    job_id, before_job, before_round = make_schema2_db(db)

    migrate_m03(db)
    migrate_m03(db)

    status = challenger_database_status(db)
    assert status == {
        "status": "READY",
        "schema_version": 3,
        "baseline_status": "BASELINE_NOT_CHAMPION",
    }
    assert json.dumps(get_job(job_id, path=db), sort_keys=True) == before_job
    assert json.dumps(get_round(job_id, 1, path=db), sort_keys=True) == before_round


def test_m01_helpers_do_not_downgrade_schema3(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    job_id, _, _ = make_schema2_db(db)
    migrate_m03(db)

    assert get_job(job_id, path=db) is not None
    assert get_round(job_id, 1, path=db) is not None
    assert challenger_database_status(db)["schema_version"] == 3


def test_source_winner_unique_constraint_and_finalize_restart(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    job_id, _, _ = make_schema2_db(db)
    migrate_m03(db)
    payload = challenger_payload(job_id)

    first = reserve_challenger(payload, path=db)
    second = reserve_challenger(
        {**payload, "challenger_id": "SHOULD-NOT-BE-CREATED"},
        path=db,
    )
    assert first["challenger_id"] == second["challenger_id"]
    assert first["status"] == "REGISTERING"

    finalized = finalize_challenger(
        first["challenger_id"],
        challenger_ea_sha256="easha",
        set_sha256="setsha",
        metadata_sha256="metasha",
        manifest_sha256="manifestsha",
        path=db,
    )
    assert finalized["status"] == "CHALLENGER"

    restarted = get_challenger(first["challenger_id"], path=db)
    assert restarted is not None
    assert restarted["status"] == "CHALLENGER"
    by_source = get_challenger_by_source(job_id, 1, 0, path=db)
    assert by_source is not None
    assert by_source["challenger_id"] == first["challenger_id"]
    assert len(list_challengers(path=db)) == 1

    same = finalize_challenger(
        first["challenger_id"],
        challenger_ea_sha256="easha",
        set_sha256="setsha",
        metadata_sha256="metasha",
        manifest_sha256="manifestsha",
        path=db,
    )
    assert same["challenger_id"] == first["challenger_id"]

    with pytest.raises(RuntimeError, match="IMMUTABLE_CHALLENGER_HASH_MISMATCH"):
        finalize_challenger(
            first["challenger_id"],
            challenger_ea_sha256="tampered",
            set_sha256="setsha",
            metadata_sha256="metasha",
            manifest_sha256="manifestsha",
            path=db,
        )


def test_different_winner_sources_may_create_different_challengers(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    job_id, _, _ = make_schema2_db(db)
    migrate_m03(db)

    first = reserve_challenger(challenger_payload(job_id), path=db)
    update_job(
        job_id,
        status="STOPPED",
        active=False,
        terminal_result="STOPPED",
        path=db,
    )

    second_job = create_job(
        request_payload(),
        evidence_root=tmp_path / "optimizer",
        path=db,
    )
    second = reserve_challenger(
        {
            **challenger_payload(
                second_job["job_id"],
                "STRAT-20260922-101451-R01-P0",
            ),
            "source_job_id": second_job["job_id"],
        },
        path=db,
    )

    assert first["challenger_id"] != second["challenger_id"]
    assert len(list_challengers(include_registering=True, path=db)) == 2

def test_challenger_status_accepts_cumulative_schema_versions(tmp_path: Path) -> None:
    for schema_version in (3, 4, 5):
        db = tmp_path / f"schema-{schema_version}.db"
        make_schema2_db(db)
        migrate_m03(db)
        with connect(db) as conn:
            conn.execute(
                "UPDATE schema_meta SET value=? WHERE key='schema_version'",
                (str(schema_version),),
            )
        assert challenger_database_status(db) == {
            "status": "READY",
            "schema_version": schema_version,
            "baseline_status": "BASELINE_NOT_CHAMPION",
        }


def test_challenger_status_rejects_schema_below_minimum_without_migrating(
    tmp_path: Path,
) -> None:
    db = tmp_path / "schema-2.db"
    make_schema2_db(db)

    result = challenger_database_status(db)

    assert result["status"] == "FAIL"
    assert result["reason"] == "SCHEMA_VERSION_MISMATCH"
    assert result["schema_version"] == 2
    with connect(db) as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "strategy_challengers" not in tables


def test_challenger_status_rejects_missing_required_table(tmp_path: Path) -> None:
    db = tmp_path / "missing-table.db"
    make_schema2_db(db)
    migrate_m03(db)
    with connect(db) as conn:
        conn.execute("DROP TABLE strategy_challengers")

    assert challenger_database_status(db) == {
        "status": "FAIL",
        "reason": "M03_TABLES_MISSING",
    }


def test_challenger_status_rejects_invalid_baseline_authority(tmp_path: Path) -> None:
    db = tmp_path / "missing-baseline.db"
    make_schema2_db(db)
    migrate_m03(db)
    with connect(db) as conn:
        conn.execute("DELETE FROM ea_baseline WHERE id=1")

    assert challenger_database_status(db) == {
        "status": "FAIL",
        "reason": "EA_BASELINE_AUTHORITY_INVALID",
    }
