from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from max_backend.db import connect, ensure_baseline_registered, initialize_database
from max_backend.research_contract import (
    FEATURE_CONTRACT,
    OWNER_CUMULATIVE_E2E_AUTHORITY,
    stable_hash,
)
from max_backend.research_r01_store import create_r01_authorization, create_r01_run
from max_backend.research_r02_contract import build_discovery_plan
from max_backend.research_r02_store import (
    create_r02_authorization,
    freeze_r02_discovery_block,
    get_r02_discovery_block,
)
from max_backend.research_store import create_authorization, create_research, update_gate_state
from max_backend.workflow_store import migrate_current


RESEARCH_ID = "RSRCH-R02-FREEZE-TEST"


def _database(tmp_path: Path) -> Path:
    db = tmp_path / "state" / "max.db"
    initialize_database(db)
    ensure_baseline_registered(db)
    migrate_current(db)

    r00_auth = create_authorization(
        {
            "authorization_id": "RAUTH-R00-R02-FREEZE",
            "gate": "R00",
            "action": "START",
            "confirmed": True,
            "expected_parent_strategy_id": "STRAT-R02-FREEZE",
            "expected_parent_authority_sha256": "a" * 64,
            "cumulative_strategy_e2e_authority": OWNER_CUMULATIVE_E2E_AUTHORITY,
            "h1_minimum_trades_per_month": 4,
            "payload_sha256": stable_hash({"r00": "r02-freeze"}),
            "authorized_utc": "2026-09-28T00:00:00+00:00",
        },
        path=db,
    )
    create_research(
        {
            "research_id": RESEARCH_ID,
            "research_parent_id": "RPAR-R02-FREEZE",
            "parent_strategy_id": "STRAT-R02-FREEZE",
            "parent_authority_sha256": "a" * 64,
            "parent_manifest_path": "synthetic/parent.json",
            "parent_manifest_sha256": "b" * 64,
            "feature_contract": FEATURE_CONTRACT,
            "current_gate": "R00",
            "gate_state": "STARTING",
            "gate_input_manifest_sha": "c" * 64,
            "gate_output_manifest_sha": None,
            "owner_authorization_id": r00_auth["authorization_id"],
            "authorized_utc": r00_auth["authorized_utc"],
            "hardware_snapshot_path": "synthetic/hardware.json",
            "hardware_snapshot_sha256": "d" * 64,
            "label_authority": {},
            "candidate_identity_contract": {},
            "artifact_lineage_contract": {},
            "research_policy": {},
            "unresolved_authority": [],
            "candidate_ids": [],
            "qualification_states": {},
            "system_recommendation": None,
            "owner_selected_ids": [],
            "training_count": 0,
            "onnx_count": 0,
            "research_challenger_count": 0,
            "created_utc": "2026-09-28T00:00:00+00:00",
        },
        path=db,
    )
    update_gate_state(RESEARCH_ID, gate_state="PASS_WAITING_OWNER", path=db)

    r01_payload = {
        "gate": "R01",
        "action": "START",
        "confirmed": True,
        "research_id": RESEARCH_ID,
    }
    r01_auth = create_r01_authorization(
        {
            "authorization_id": "RAUTH-R01-R02-FREEZE",
            "research_id": RESEARCH_ID,
            "gate": "R01",
            "action": "START",
            "confirmed": True,
            "payload_sha256": stable_hash(r01_payload),
            "payload": r01_payload,
            "authorized_utc": "2026-09-28T00:01:00+00:00",
        },
        path=db,
    )
    create_r01_run(
        run_id="RRUN-R01-R02-FREEZE",
        research_id=RESEARCH_ID,
        authorization_id=r01_auth["authorization_id"],
        input_manifest_sha="e" * 64,
        path=db,
    )
    with connect(db) as conn:
        conn.execute(
            """
            UPDATE research_r01_runs
            SET state='PASS_WAITING_OWNER',
                output_manifest_sha=?,
                updated_utc=?
            WHERE research_id=?
            """,
            ("f" * 64, "2026-09-28T00:02:00+00:00", RESEARCH_ID),
        )
    return db


def _plan() -> dict:
    parent = {
        "research_parent_id": "RPAR-R02-FREEZE",
        "parent_strategy_id": "STRAT-R02-FREEZE",
        "dataset_id": "RDATA-R02-FREEZE",
        "r01_output_manifest_sha256": "f" * 64,
    }
    candidates = []
    for family, seed in (
        ("lightgbm", 11),
        ("xgboost", 42),
        ("random_forest", 7),
    ):
        candidates.append(
            {
                "research_id": RESEARCH_ID,
                "model_family": family,
                "topology_spec": {"depth": 3},
                "feature_contract": FEATURE_CONTRACT,
                "label_contract": "MAX_RESEARCH_FIRST_BARRIER_LABEL_R01_V1",
                "seed": seed,
                "preprocessing": {"scaling": "NONE"},
                "training_configuration": {"objective": "MULTICLASS"},
                "parent_lineage": deepcopy(parent),
            }
        )
    return build_discovery_plan(
        {
            "research_id": RESEARCH_ID,
            "r01_output_manifest_sha256": "f" * 64,
            "feature_contract": FEATURE_CONTRACT,
            "label_contract": "MAX_RESEARCH_FIRST_BARRIER_LABEL_R01_V1",
            "parent_lineage": parent,
            "candidate_count": 3,
            "compute_budget": {"value": 120, "unit": "FIT_SECONDS"},
            "candidates": candidates,
        }
    )


def _authorization(plan: dict) -> dict:
    body = {
        "schema": "MAX_RESEARCH_OWNER_AUTHORIZATION_R02_V1",
        "gate": "R02",
        "action": "AUTHORIZE_DISCOVERY",
        "confirmed": True,
        "owner_confirmation": "OWNER_EXPLICIT_R02_DISCOVERY_AUTHORIZE",
        "research_id": RESEARCH_ID,
        "r01_output_manifest_sha256": plan["r01_output_manifest_sha256"],
        "plan_id": plan["plan_id"],
        "plan_sha256": plan["plan_sha256"],
        "candidate_count": plan["candidate_count"],
        "candidate_ids": plan["candidate_ids"],
        "compute_budget": plan["compute_budget"],
        "cheap_screen_qualification_authority": False,
        "automatic_second_discovery_block": False,
        "execution_available": False,
    }
    sha = stable_hash(body)
    return {
        "authorization_id": "RAUTH-R02-" + sha[:24],
        "research_id": RESEARCH_ID,
        "confirmed": True,
        "payload_sha256": sha,
        "payload": body,
        "authorized_utc": "2026-09-28T00:03:00+00:00",
    }


def test_freeze_persists_one_immutable_block_and_all_candidate_specs(tmp_path: Path) -> None:
    db = _database(tmp_path)
    plan = _plan()
    authorization = create_r02_authorization(_authorization(plan), path=db)

    block = freeze_r02_discovery_block(
        authorization=authorization,
        plan=plan,
        path=db,
    )

    assert block["state"] == "FROZEN_WAITING_EXECUTION"
    assert block["candidate_count"] == 3
    assert [item["candidate_id"] for item in block["candidates"]] == plan["candidate_ids"]
    assert block["compute_budget"] == plan["compute_budget"]
    assert all(item["spec"]["research_id"] == RESEARCH_ID for item in block["candidates"])

    with connect(db) as conn:
        project = conn.execute(
            "SELECT training_count,onnx_count,research_challenger_count,champion_mutation "
            "FROM research_projects WHERE research_id=?",
            (RESEARCH_ID,),
        ).fetchone()
    assert int(project["training_count"]) == 0
    assert int(project["onnx_count"]) == 0
    assert int(project["research_challenger_count"]) == 0
    assert project["champion_mutation"] == "NONE"


def test_exact_freeze_replay_is_idempotent(tmp_path: Path) -> None:
    db = _database(tmp_path)
    plan = _plan()
    authorization = create_r02_authorization(_authorization(plan), path=db)
    first = freeze_r02_discovery_block(authorization=authorization, plan=plan, path=db)
    second = freeze_r02_discovery_block(authorization=authorization, plan=plan, path=db)
    assert first == second


def test_second_different_discovery_block_is_rejected(tmp_path: Path) -> None:
    db = _database(tmp_path)
    plan = _plan()
    authorization = create_r02_authorization(_authorization(plan), path=db)
    freeze_r02_discovery_block(authorization=authorization, plan=plan, path=db)

    other = deepcopy(plan)
    other["compute_budget"] = {
        "value": 121,
        "unit": "FIT_SECONDS",
        "execution_semantics": "FROZEN_ONLY_NOT_EXECUTED",
    }
    body = {key: value for key, value in other.items() if key not in {"plan_id", "plan_sha256"}}
    other["plan_id"] = "RPLAN-" + stable_hash(body)[:24]
    other["plan_sha256"] = stable_hash(other)
    with pytest.raises(RuntimeError, match="R02_AUTHORIZATION_ALREADY_FROZEN"):
        create_r02_authorization(_authorization(other), path=db)


def test_authorization_id_must_bind_payload_hash(tmp_path: Path) -> None:
    db = _database(tmp_path)
    plan = _plan()
    record = _authorization(plan)
    record["authorization_id"] = "RAUTH-R02-WRONG"
    with pytest.raises(ValueError, match="R02_AUTHORIZATION_ID_INVALID"):
        create_r02_authorization(record, path=db)


def test_plan_content_tamper_with_stale_hash_is_rejected(tmp_path: Path) -> None:
    db = _database(tmp_path)
    plan = _plan()
    authorization = create_r02_authorization(_authorization(plan), path=db)
    tampered = deepcopy(plan)
    tampered["compute_budget"]["value"] = 999
    with pytest.raises(RuntimeError, match="R02_PLAN_INTEGRITY_MISMATCH"):
        freeze_r02_discovery_block(
            authorization=authorization,
            plan=tampered,
            path=db,
        )


def test_candidate_identity_tamper_is_rejected_even_with_resealed_plan(tmp_path: Path) -> None:
    db = _database(tmp_path)
    plan = _plan()
    tampered = deepcopy(plan)
    tampered["candidates"][0]["seed"] = 999
    id_body = deepcopy(tampered)
    id_body.pop("plan_id")
    id_body.pop("plan_sha256")
    tampered["plan_id"] = "RPLAN-" + stable_hash(id_body)[:24]
    sha_body = deepcopy(tampered)
    sha_body.pop("plan_sha256")
    tampered["plan_sha256"] = stable_hash(sha_body)
    authorization_record = _authorization(tampered)
    authorization = create_r02_authorization(authorization_record, path=db)
    with pytest.raises(RuntimeError, match="R02_CANDIDATE_ID_INTEGRITY_MISMATCH"):
        freeze_r02_discovery_block(
            authorization=authorization,
            plan=tampered,
            path=db,
        )


def test_r01_output_authority_mismatch_is_rejected(tmp_path: Path) -> None:
    db = _database(tmp_path)
    plan = _plan()
    tampered = deepcopy(plan)
    tampered["r01_output_manifest_sha256"] = "9" * 64
    id_body = deepcopy(tampered)
    id_body.pop("plan_id")
    id_body.pop("plan_sha256")
    tampered["plan_id"] = "RPLAN-" + stable_hash(id_body)[:24]
    sha_body = deepcopy(tampered)
    sha_body.pop("plan_sha256")
    tampered["plan_sha256"] = stable_hash(sha_body)

    record = _authorization(tampered)
    record["payload"]["r01_output_manifest_sha256"] = plan[
        "r01_output_manifest_sha256"
    ]
    payload_sha = stable_hash(record["payload"])
    record["payload_sha256"] = payload_sha
    record["authorization_id"] = "RAUTH-R02-" + payload_sha[:24]
    authorization = create_r02_authorization(record, path=db)

    with pytest.raises(RuntimeError, match="R02_AUTHORIZATION_R01_OUTPUT_MISMATCH"):
        freeze_r02_discovery_block(
            authorization=authorization,
            plan=tampered,
            path=db,
        )


@pytest.mark.parametrize(
    ("table", "column", "value", "error"),
    [
        ("research_r02_authorizations", "confirmed", 0, "R02_AUTHORIZATION_IMMUTABLE"),
        ("research_r02_discovery_blocks", "state", "FROZEN_WAITING_EXECUTION", "R02_DISCOVERY_BLOCK_IMMUTABLE"),
        ("research_r02_candidate_specs", "seed", 999, "R02_CANDIDATE_SPEC_IMMUTABLE"),
    ],
)
def test_r02_frozen_authority_rows_are_update_immutable(
    tmp_path: Path,
    table: str,
    column: str,
    value: object,
    error: str,
) -> None:
    db = _database(tmp_path)
    plan = _plan()
    authorization = create_r02_authorization(_authorization(plan), path=db)
    freeze_r02_discovery_block(authorization=authorization, plan=plan, path=db)
    with connect(db) as conn, pytest.raises(Exception, match=error):
        conn.execute(f"UPDATE {table} SET {column}=?", (value,))


def test_r02_frozen_rows_are_append_only(tmp_path: Path) -> None:
    db = _database(tmp_path)
    plan = _plan()
    authorization = create_r02_authorization(_authorization(plan), path=db)
    block = freeze_r02_discovery_block(authorization=authorization, plan=plan, path=db)

    with connect(db) as conn:
        with pytest.raises(Exception, match="R02_CANDIDATE_SPEC_APPEND_ONLY"):
            conn.execute(
                "DELETE FROM research_r02_candidate_specs WHERE candidate_id=?",
                (plan["candidate_ids"][0],),
            )
        with pytest.raises(Exception, match="R02_DISCOVERY_BLOCK_APPEND_ONLY"):
            conn.execute(
                "DELETE FROM research_r02_discovery_blocks WHERE block_id=?",
                (block["block_id"],),
            )
        with pytest.raises(Exception, match="R02_AUTHORIZATION_APPEND_ONLY"):
            conn.execute(
                "DELETE FROM research_r02_authorizations WHERE authorization_id=?",
                (authorization["authorization_id"],),
            )


def test_r02_persistence_advances_cumulative_schema_to_11(tmp_path: Path) -> None:
    db = _database(tmp_path)
    with connect(db) as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        tables = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert version is not None
    assert int(version["value"]) == 11
    assert {
        "research_r02_authorizations",
        "research_r02_discovery_blocks",
        "research_r02_candidate_specs",
    }.issubset(tables)


def test_get_block_returns_none_before_authorization(tmp_path: Path) -> None:
    db = _database(tmp_path)
    assert get_r02_discovery_block(RESEARCH_ID, path=db) is None
