from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from max_backend.challenger_store import (
    finalize_challenger,
    get_challenger,
    list_challengers,
    migrate_m03,
    reserve_challenger,
)
from max_backend.champion_store import (
    champion_database_status,
    commit_promotion_authority,
    create_prepared_promotion,
    current_champion,
    get_champion,
    get_promotion,
    list_champions,
    list_promotions,
    migrate_m04,
    update_promotion,
)
from max_backend.db import ensure_baseline_registered, initialize_database
from max_backend.mtf_geometry import STRATEGY_CONTRACT, resolve_strategy_geometry
from max_backend.optimizer_store import create_job, migrate_m02, update_job


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


def make_schema3_db(path: Path) -> tuple[str, str]:
    initialize_database(path)
    ensure_baseline_registered(path)
    migrate_m02(path)
    job = create_job(
        request_payload(),
        evidence_root=path.parent / "optimizer",
        path=path,
    )
    update_job(
        job["job_id"],
        status="STRATEGY_CHALLENGER_FOUND",
        terminal_result="STRATEGY_CHALLENGER_FOUND",
        active=False,
        path=path,
    )
    migrate_m03(path)
    cid = "STRAT-20260922-101450-R01-P7"
    row = reserve_challenger(
        {
            "challenger_id": cid,
            "source_job_id": job["job_id"],
            "source_round": 1,
            "source_pass": 7,
            "created_utc": "2026-09-22T10:15:00+00:00",
            "ea_version": "2.10",
            "baseline_ea_sha256": EA_SHA,
            "bundle_path": f"artifacts/strategy_challengers/{cid}",
            "params": {"P": 1},
            "kpi": {"profit_factor": 1.5},
            "hard_gates": {"min_profit_factor": 1.0},
            "source_request": request_payload(),
            "winner": {"job_id": job["job_id"], "round": 1, "mt5_pass": 7},
            "provenance": {"run_nonce": 77},
            "winning_xml_sha256": "xmlsha",
            "winning_sidecar_sha256": "sidecarsha",
        },
        path=path,
    )
    finalize_challenger(
        cid,
        challenger_ea_sha256="ea",
        set_sha256="set",
        metadata_sha256="meta",
        manifest_sha256="manifest",
        path=path,
    )
    return job["job_id"], row["challenger_id"]


def add_second_challenger(path: Path) -> tuple[str, str]:
    job = create_job(
        request_payload(),
        evidence_root=path.parent / "optimizer2",
        path=path,
    )
    update_job(
        job["job_id"],
        status="STRATEGY_CHALLENGER_FOUND",
        terminal_result="STRATEGY_CHALLENGER_FOUND",
        active=False,
        path=path,
    )
    cid = "STRAT-20260922-111500-R01-P8"
    reserve_challenger(
        {
            "challenger_id": cid,
            "source_job_id": job["job_id"],
            "source_round": 1,
            "source_pass": 8,
            "created_utc": "2026-09-22T11:15:00+00:00",
            "ea_version": "2.10",
            "baseline_ea_sha256": EA_SHA,
            "bundle_path": f"artifacts/strategy_challengers/{cid}",
            "params": {"P": 2},
            "kpi": {"profit_factor": 1.6},
            "hard_gates": {"min_profit_factor": 1.0},
            "source_request": request_payload(),
            "winner": {"job_id": job["job_id"], "round": 1, "mt5_pass": 8},
            "provenance": {"run_nonce": 88},
            "winning_xml_sha256": "xmlsha2",
            "winning_sidecar_sha256": "sidecarsha2",
        },
        path=path,
    )
    finalize_challenger(
        cid,
        challenger_ea_sha256="ea2",
        set_sha256="set2",
        metadata_sha256="meta2",
        manifest_sha256="manifest2",
        path=path,
    )
    return job["job_id"], cid


def champion_payload(job_id: str, source_pass: int, marker: str) -> dict:
    return {
        "source_job_id": job_id,
        "source_round": 1,
        "source_pass": source_pass,
        "params": {"P": source_pass},
        "kpi": {"profit_factor": 1.5},
        "hard_gates": {"min_profit_factor": 1.0},
        "champion_ea_sha256": f"ea-{marker}",
        "champion_set_sha256": f"set-{marker}",
        "deployed_ea_sha256": f"dep-{marker}",
        "deployed_ex5_sha256": f"ex5-{marker}",
        "tester_set_sha256": f"tester-{marker}",
        "promoted_utc": f"2026-09-22T12:{source_pass:02d}:00+00:00",
        "artifact_path": "ea/champion/current",
    }


def prepare_files_committed(
    db: Path,
    *,
    promotion_id: str,
    challenger_id: str,
    previous: str | None,
    manifest: str,
) -> None:
    create_prepared_promotion(
        promotion_id=promotion_id,
        challenger_id=challenger_id,
        previous_champion_id=previous,
        expected_manifest_sha256=manifest,
        before_state={"files": {}},
        recovery_path="state/recovery",
        path=db,
    )
    update_promotion(promotion_id, state="FILES_COMMITTED", path=db)


def commit(
    db: Path,
    *,
    promotion_id: str,
    challenger_id: str,
    champion: dict,
    baseline_archive: str | None = None,
    former_archive: str | None = None,
) -> dict:
    return commit_promotion_authority(
        promotion_id,
        champion=champion,
        post_state={"current_champion_id": challenger_id},
        baseline_archive_id=baseline_archive,
        former_champion_archive_id=former_archive,
        compile_result={"status": "PASS"},
        parity={"status": "VERIFIED"},
        deployment={"mt5_source": "source"},
        evidence_path=f"evidence/m04/promotions/{promotion_id}",
        path=db,
    )


def test_schema3_to_schema5_preserves_challenger_without_auto_promotion(
    tmp_path: Path,
) -> None:
    db = tmp_path / "max.db"
    job_id, cid = make_schema3_db(db)

    migrate_m04(db)
    migrate_m04(db)

    status = champion_database_status(db)
    assert status["status"] == "READY"
    assert status["schema_version"] == 5
    assert status["current_champion_count"] == 0
    row = get_challenger(cid, path=db)
    assert row is not None and row["status"] == "CHALLENGER"
    assert row["source_job_id"] == job_id
    assert current_champion(path=db) is None


def test_first_promotion_commits_exactly_one_current_and_consumes_challenger(
    tmp_path: Path,
) -> None:
    db = tmp_path / "max.db"
    job_id, cid = make_schema3_db(db)
    migrate_m04(db)

    prepare_files_committed(
        db,
        promotion_id="PROMOTE-FIRST",
        challenger_id=cid,
        previous=None,
        manifest="manifest",
    )
    row = commit(
        db,
        promotion_id="PROMOTE-FIRST",
        challenger_id=cid,
        champion=champion_payload(job_id, 7, "a"),
        baseline_archive="BASELINE-A",
    )

    assert row["strategy_id"] == cid
    assert row["status"] == "CURRENT"
    assert current_champion(path=db)["strategy_id"] == cid
    assert get_challenger(cid, path=db)["status"] == "PROMOTED"
    assert get_promotion("PROMOTE-FIRST", path=db)["state"] == "COMMITTED"
    assert len([r for r in list_champions(path=db) if r["status"] == "CURRENT"]) == 1


def test_later_promotion_demotes_a_preserves_lineage_and_has_one_current(
    tmp_path: Path,
) -> None:
    db = tmp_path / "max.db"
    job_a, a = make_schema3_db(db)
    migrate_m04(db)
    prepare_files_committed(
        db,
        promotion_id="PROMOTE-A",
        challenger_id=a,
        previous=None,
        manifest="manifest",
    )
    commit(
        db,
        promotion_id="PROMOTE-A",
        challenger_id=a,
        champion=champion_payload(job_a, 7, "a"),
        baseline_archive="BASELINE-A",
    )
    job_b, b = add_second_challenger(db)
    prepare_files_committed(
        db,
        promotion_id="PROMOTE-B",
        challenger_id=b,
        previous=a,
        manifest="manifest2",
    )
    commit(
        db,
        promotion_id="PROMOTE-B",
        challenger_id=b,
        champion=champion_payload(job_b, 8, "b"),
        former_archive="FORMER-A",
    )

    assert current_champion(path=db)["strategy_id"] == b
    former = get_champion(a, path=db)
    assert former["status"] == "FORMER"
    assert former["former_archive_id"] == "FORMER-A"
    assert former["replaced_by"] == b
    assert get_challenger(a, path=db)["status"] == "PROMOTED"
    assert get_challenger(b, path=db)["status"] == "PROMOTED"
    rows = list_champions(path=db)
    assert len([r for r in rows if r["status"] == "CURRENT"]) == 1
    assert len([r for r in rows if r["status"] == "FORMER"]) == 1


def test_stale_previous_champion_fails_without_consuming_challenger(
    tmp_path: Path,
) -> None:
    db = tmp_path / "max.db"
    job_id, cid = make_schema3_db(db)
    migrate_m04(db)
    prepare_files_committed(
        db,
        promotion_id="PROMOTE-STALE",
        challenger_id=cid,
        previous="NOT-CURRENT",
        manifest="manifest",
    )

    with pytest.raises(RuntimeError, match="PROMOTION_CONFIRMATION_STALE"):
        commit(
            db,
            promotion_id="PROMOTE-STALE",
            challenger_id=cid,
            champion=champion_payload(job_id, 7, "a"),
        )

    assert current_champion(path=db) is None
    assert get_challenger(cid, path=db)["status"] == "CHALLENGER"


def test_only_one_active_promotion_transaction(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    _job, cid = make_schema3_db(db)
    migrate_m04(db)
    create_prepared_promotion(
        promotion_id="PROMOTE-ONE",
        challenger_id=cid,
        previous_champion_id=None,
        expected_manifest_sha256="manifest",
        before_state={"files": {}},
        recovery_path="state/a",
        path=db,
    )
    with pytest.raises(RuntimeError, match="PROMOTION_ALREADY_ACTIVE"):
        create_prepared_promotion(
            promotion_id="PROMOTE-TWO",
            challenger_id=cid,
            previous_champion_id=None,
            expected_manifest_sha256="manifest",
            before_state={"files": {}},
            recovery_path="state/b",
            path=db,
        )


def test_db_partial_unique_index_rejects_two_current_rows(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    job_a, a = make_schema3_db(db)
    migrate_m04(db)
    prepare_files_committed(
        db,
        promotion_id="PROMOTE-A",
        challenger_id=a,
        previous=None,
        manifest="manifest",
    )
    commit(
        db,
        promotion_id="PROMOTE-A",
        challenger_id=a,
        champion=champion_payload(job_a, 7, "a"),
    )

    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
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
                    'OTHER-TENURE','OTHER','CURRENT','OTHER-C',?,1,99,
                    '{}','{}','{}','ea','set','dep','ex5','tester',
                    '2026-09-22T13:00:00+00:00','OTHER-P',
                    'OWNER_MANUAL_STRATEGY_PROMOTION',
                    'ea/champion/current','evidence/m04','{}'
                )
                """,
                (job_a,),
            )

def test_replacement_keeps_former_champion_source_historical_and_inactive(
    tmp_path: Path,
) -> None:
    db = tmp_path / "max.db"
    job_a, a = make_schema3_db(db)
    migrate_m04(db)
    original_a = get_challenger(a, path=db)
    assert original_a is not None
    a_identity = {
        "challenger_id": original_a["challenger_id"],
        "bundle_path": original_a["bundle_path"],
        "manifest_sha256": original_a["manifest_sha256"],
        "ea_version": original_a["ea_version"],
    }

    prepare_files_committed(
        db,
        promotion_id="PROMOTE-P1-A",
        challenger_id=a,
        previous=None,
        manifest="manifest",
    )
    p1_payload = champion_payload(job_a, 7, "a1")
    p1_payload["promoted_utc"] = "2026-09-22T12:07:00+00:00"
    commit(
        db,
        promotion_id="PROMOTE-P1-A",
        challenger_id=a,
        champion=p1_payload,
        baseline_archive="BASELINE-A",
    )

    job_b, b = add_second_challenger(db)
    original_b = get_challenger(b, path=db)
    assert original_b is not None
    b_identity = {
        "challenger_id": original_b["challenger_id"],
        "bundle_path": original_b["bundle_path"],
        "manifest_sha256": original_b["manifest_sha256"],
        "ea_version": original_b["ea_version"],
    }

    prepare_files_committed(
        db,
        promotion_id="PROMOTE-P2-B",
        challenger_id=b,
        previous=a,
        manifest="manifest2",
    )
    p2_payload = champion_payload(job_b, 8, "b1")
    p2_payload["promoted_utc"] = "2026-09-22T12:08:00+00:00"
    commit(
        db,
        promotion_id="PROMOTE-P2-B",
        challenger_id=b,
        champion=p2_payload,
        former_archive="FORMER-A-T1",
    )

    current = current_champion(path=db)
    assert current is not None and current["strategy_id"] == b
    former = get_champion(a, path=db)
    assert former is not None and former["status"] == "FORMER"
    assert former["replaced_by"] == b
    assert get_challenger(a, path=db)["status"] == "PROMOTED"
    assert get_challenger(b, path=db)["status"] == "PROMOTED"
    assert list_challengers(path=db) == []

    a_after = get_challenger(a, path=db)
    b_after = get_challenger(b, path=db)
    assert a_after is not None and b_after is not None
    assert {
        "challenger_id": a_after["challenger_id"],
        "bundle_path": a_after["bundle_path"],
        "manifest_sha256": a_after["manifest_sha256"],
        "ea_version": a_after["ea_version"],
    } == a_identity
    assert {
        "challenger_id": b_after["challenger_id"],
        "bundle_path": b_after["bundle_path"],
        "manifest_sha256": b_after["manifest_sha256"],
        "ea_version": b_after["ea_version"],
    } == b_identity

    tenures = list_champions(path=db)
    assert len(tenures) == 2
    assert len([row for row in tenures if row["status"] == "CURRENT"]) == 1
    assert len([row for row in tenures if row["status"] == "FORMER"]) == 1
    history = list_promotions(path=db)
    assert {row["promotion_id"] for row in history} >= {
        "PROMOTE-P1-A",
        "PROMOTE-P2-B",
    }

def test_schema4_to_schema5_preserves_current_champion_and_promotion_history(
    tmp_path: Path,
) -> None:
    db = tmp_path / "max.db"
    job_id, cid = make_schema3_db(db)
    migrate_m04(db)
    prepare_files_committed(
        db,
        promotion_id="PROMOTE-SCHEMA4-A",
        challenger_id=cid,
        previous=None,
        manifest="manifest",
    )
    payload = champion_payload(job_id, 7, "schema4")
    payload["promoted_utc"] = "2026-09-22T12:07:00+00:00"
    commit(
        db,
        promotion_id="PROMOTE-SCHEMA4-A",
        challenger_id=cid,
        champion=payload,
        baseline_archive="BASELINE-SCHEMA4",
    )

    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            DROP INDEX IF EXISTS ux_strategy_champions_current;
            DROP INDEX IF EXISTS ix_strategy_champions_promoted;
            DROP INDEX IF EXISTS ix_strategy_champions_strategy;
            ALTER TABLE strategy_champions RENAME TO strategy_champions_v5_fixture;

            CREATE TABLE strategy_champions (
                strategy_id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK (status IN ('CURRENT','FORMER')),
                source_challenger_id TEXT NOT NULL UNIQUE,
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
                authority_source TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                evidence_path TEXT NOT NULL,
                deployment_json TEXT NOT NULL,
                former_archive_id TEXT,
                replaced_by TEXT
            );

            INSERT INTO strategy_champions(
                strategy_id,status,source_challenger_id,
                source_job_id,source_round,source_pass,
                params_json,kpi_json,hard_gates_json,
                champion_ea_sha256,champion_set_sha256,
                deployed_ea_sha256,deployed_ex5_sha256,tester_set_sha256,
                promoted_utc,demoted_utc,promotion_id,authority_source,
                artifact_path,evidence_path,deployment_json,
                former_archive_id,replaced_by
            )
            SELECT
                strategy_id,status,source_challenger_id,
                source_job_id,source_round,source_pass,
                params_json,kpi_json,hard_gates_json,
                champion_ea_sha256,champion_set_sha256,
                deployed_ea_sha256,deployed_ex5_sha256,tester_set_sha256,
                promoted_utc,demoted_utc,promotion_id,authority_source,
                artifact_path,evidence_path,deployment_json,
                former_archive_id,replaced_by
            FROM strategy_champions_v5_fixture;

            DROP TABLE strategy_champions_v5_fixture;
            CREATE UNIQUE INDEX ux_strategy_champions_current
            ON strategy_champions(status) WHERE status='CURRENT';
            CREATE INDEX ix_strategy_champions_promoted
            ON strategy_champions(promoted_utc, strategy_id);
            UPDATE schema_meta SET value='4' WHERE key='schema_version';
            """
        )

    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "4"
        before = conn.execute(
            "SELECT strategy_id,status,promotion_id FROM strategy_champions"
        ).fetchone()
        assert before == (cid, "CURRENT", "PROMOTE-SCHEMA4-A")

    migrate_m04(db)
    status = champion_database_status(db)
    assert status["schema_version"] == 5
    assert status["current_champion_count"] == 1
    current = current_champion(path=db)
    assert current is not None
    assert current["strategy_id"] == cid
    assert current["status"] == "CURRENT"
    assert current["promotion_id"] == "PROMOTE-SCHEMA4-A"
    assert current["champion_tenure_id"] == "PROMOTE-SCHEMA4-A"
    assert get_challenger(cid, path=db)["status"] == "PROMOTED"
    assert get_challenger(cid, path=db)["ea_version"] == "2.10"
    promotion = get_promotion("PROMOTE-SCHEMA4-A", path=db)
    assert promotion is not None and promotion["state"] == "COMMITTED"

def test_replacement_authority_transaction_rolls_back_old_champion_reactivation(
    tmp_path: Path,
) -> None:
    db = tmp_path / "max.db"
    job_a, a = make_schema3_db(db)
    migrate_m04(db)
    prepare_files_committed(
        db,
        promotion_id="PROMOTE-ATOMIC-A",
        challenger_id=a,
        previous=None,
        manifest="manifest",
    )
    commit(
        db,
        promotion_id="PROMOTE-ATOMIC-A",
        challenger_id=a,
        champion=champion_payload(job_a, 7, "a"),
        baseline_archive="BASELINE-A",
    )
    job_b, b = add_second_challenger(db)
    prepare_files_committed(
        db,
        promotion_id="PROMOTE-ATOMIC-B",
        challenger_id=b,
        previous=a,
        manifest="manifest2",
    )
    broken = champion_payload(job_b, 8, "b")
    del broken["champion_set_sha256"]

    with pytest.raises(KeyError, match="champion_set_sha256"):
        commit(
            db,
            promotion_id="PROMOTE-ATOMIC-B",
            challenger_id=b,
            champion=broken,
            former_archive="FORMER-A-SHOULD-NOT-COMMIT",
        )

    current = current_champion(path=db)
    assert current is not None and current["strategy_id"] == a
    assert get_challenger(a, path=db)["status"] == "PROMOTED"
    assert get_challenger(b, path=db)["status"] == "CHALLENGER"
    tenures = list_champions(path=db)
    assert len(tenures) == 1
    assert tenures[0]["status"] == "CURRENT"
    assert tenures[0]["former_archive_id"] is None
