from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

import max_backend.research_hardware as hardware
import max_backend.research_service as service
from max_backend.db import ensure_baseline_registered, initialize_database
from max_backend.research_contract import (
    FEATURE_CONTRACT,
    OWNER_CUMULATIVE_E2E_AUTHORITY,
    OWNER_R00_CONFIRMATION,
    candidate_id,
    research_sample_policy,
    stable_hash,
)
from max_backend.research_settings import (
    get_research_sample_configuration,
    set_research_sample_configuration,
)
from max_backend.research_store import (
    create_authorization,
    append_memory_event,
    create_research,
    get_research,
    list_memory_events,
    recover_incomplete_research,
)
from max_backend.workflow_store import migrate_current


def _fake_parent() -> dict:
    core = {
        "strategy_champion_id": "STRAT-R00-PARENT",
        "champion_tenure_id": "TENURE-R00",
        "source_challenger_id": "STRAT-R00-PARENT",
        "source_job_id": "JOB-R00",
        "source_round": 1,
        "source_pass": 7,
        "promotion_id": "PROMOTE-R00",
        "ea_sha256": "1" * 64,
        "ea_semantic_version": "2.11",
        "strategy_parameters": {
            "InpWeightTrend": 1.7,
            "InpWeightRange": 1.7,
            "InpWeightBreakout": 0.2,
            "InpWeightPullback": 1.5,
            "InpWeightSession": 0.3,
            "InpWeightShock": 1.5,
            "InpWeightRelative": 0.8,
            "InpEntryThreshold": 0.18,
            "InpExitReverseThreshold": 0.25,
            "InpMinConsensus": 0.7,
            "InpSL_ATR": 3.2,
            "InpTP_ATR": 4.8,
            "InpMaxHoldBars": 54,
            "InpShockHaltATR": 3.5,
            "InpRelativeLookback": 60,
            "InpMinRelativeCorr": 0.25,
        },
        "strategy_contract": "MAX_TRUE_MTF_DYNAMIC_V1",
        "feature_contract": FEATURE_CONTRACT,
        "mtf_resolver_version": "TRUE_MTF_LOG_RATIO_V1",
        "strategy_geometry": {
            "contract": "MAX_TRUE_MTF_DYNAMIC_V1",
            "context_tf": "D1",
            "structure_tf": "H4",
            "main_tf": "H1",
            "timing_tf": "M20",
            "resolver_version": "TRUE_MTF_LOG_RATIO_V1",
        },
        "main_timeframe": "H1",
        "main_symbol": "XAUUSD.m",
        "relative_symbol": "EURUSD.m",
        "deterministic_risk": {
            "authority": "DETERMINISTIC_EA_RUNTIME",
            "risk_percent_equity": 0.5,
            "daily_loss_limit_percent": 3.0,
            "sl_atr": 3.2,
            "tp_atr": 4.8,
            "max_hold_bars": 54,
            "lot_sizing": "BROKER_VALID_MAX_VOLUME_WITH_ORDER_CALC_PROFIT_STOP_RISK_CAP",
            "minimum_lot_over_risk_behavior": "FAIL_CLOSED",
            "model_owns_risk": False,
        },
        "source_identity": {
            "symbol": "XAUUSD.m",
            "relative_symbol": "EURUSD.m",
            "main_timeframe": "H1",
            "from_date": "2024.01.01",
            "to_date": "2026.06.30",
            "broker": None,
            "feed": None,
            "source": None,
            "broker_feed_status": "UNRESOLVED_AVAILABLE_AT_R01_DATA_SOURCE_FREEZE",
        },
        "parent_artifact_hashes": {
            "baseline_ea_sha256": "2" * 64,
            "project_ea_sha256": "1" * 64,
            "project_set_sha256": "3" * 64,
            "deployed_ea_sha256": "1" * 64,
            "deployed_ex5_sha256": "4" * 64,
            "tester_set_sha256": "5" * 64,
            "challenger_manifest_sha256": "6" * 64,
            "challenger_metadata_sha256": "7" * 64,
            "challenger_set_sha256": "8" * 64,
            "winning_xml_sha256": "9" * 64,
            "winning_sidecar_sha256": "a" * 64,
        },
        "champion_authority_source": "OWNER_MANUAL_STRATEGY_PROMOTION",
        "champion_integrity": "VERIFIED",
        "champion_live_authority": "NONE",
    }
    authority_sha = stable_hash(core)
    return {
        **core,
        "research_parent_id": "RPAR-" + authority_sha[:24],
        "parent_authority_sha256": authority_sha,
    }


def _fake_hardware(*, free_ram: int = 16_000_000_000) -> dict:
    stable = {
        "os": {"system": "Windows", "release": "11", "machine": "AMD64"},
        "cpu": {
            "name": "Test CPU",
            "physical_cores": 6,
            "logical_threads": 12,
            "planning_cores": 6,
            "core_count_source": "TEST",
        },
        "memory_total_bytes": 32_000_000_000,
        "disk_total_bytes": 1_000_000_000_000,
        "gpus": [
            {
                "index": 0,
                "name": "Test GPU",
                "memory_total_mb": 8192,
                "driver_version": "test",
            }
        ],
        "python": {
            "implementation": "CPython",
            "version": "3.13.0",
            "architecture": "64bit",
        },
    }
    return {
        "schema": "MAX_RESEARCH_HARDWARE_SNAPSHOT_V1",
        "captured_utc": "2026-09-25T00:00:00+00:00",
        "profile_hash": stable_hash(stable),
        "stable_identity": stable,
        "dynamic_resources": {
            "memory_available_bytes": free_ram,
            "gpu_memory_free_mb": [{"index": 0, "memory_free_mb": 7000}],
            "disk_free_bytes": 500_000_000_000,
        },
        "capacity_authority": {
            "schema": "MAX_RESEARCH_CAPACITY_AUTHORITY_R00_V1",
            "equation": "EXECUTABLE_CAPACITY=MIN(LEGAL,RESOURCE,SCIENTIFIC)",
            "global_parameter_hard_ceiling": None,
        },
        "training_started": False,
        "model_capacity_decision_performed": False,
    }


def _setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict]:
    db = tmp_path / "state" / "max.db"
    initialize_database(db)
    ensure_baseline_registered(db)
    migrate_current(db)
    set_research_sample_configuration(24, path=db)
    parent = _fake_parent()
    monkeypatch.setattr(service, "ROOT", tmp_path)
    monkeypatch.setattr(
        service,
        "RESEARCH_ARTIFACT_ROOT",
        tmp_path / "artifacts" / "research",
    )
    monkeypatch.setattr(service, "_parent_authority", lambda **_kwargs: deepcopy(parent))
    monkeypatch.setattr(
        service,
        "collect_hardware_snapshot",
        lambda: deepcopy(_fake_hardware()),
    )
    return db, parent


def _request(parent: dict) -> dict:
    return {
        "expected_parent_strategy_id": parent["strategy_champion_id"],
        "expected_parent_authority_sha256": parent["parent_authority_sha256"],
        "owner_confirmation": OWNER_R00_CONFIRMATION,
        "cumulative_strategy_e2e_authority": OWNER_CUMULATIVE_E2E_AUTHORITY,
        "h1_minimum_trades_per_month": 24,
        "confirmed": True,
    }


def test_r00_requires_explicit_owner_h1_sample_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    request = _request(parent)
    request.pop("h1_minimum_trades_per_month")
    from max_backend.db import connect
    with connect(db) as conn:
        conn.execute(
            "DELETE FROM schema_meta WHERE key='research_sample_configuration_v1'"
        )
    with pytest.raises(
        RuntimeError,
        match="RESEARCH_H1_SAMPLE_CONFIGURATION_REQUIRED",
    ):
        service.start_r00(request, path=db)
    current = service.current_research(path=db)
    assert current["status"] == "NOT_STARTED"
    assert current["owner_view"]["sample_requirement"]["value"] is None


def test_r00_requires_persisted_owner_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    request = _request(parent)
    request["confirmed"] = False
    with pytest.raises(RuntimeError, match="R00_OWNER_CONFIRMATION_REQUIRED"):
        service.start_r00(request, path=db)
    assert service.current_research(path=db)["status"] == "NOT_STARTED"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("expected_parent_strategy_id", "STRAT-STALE", "R00_PARENT_STRATEGY_STALE"),
        ("expected_parent_authority_sha256", "0" * 64, "R00_PARENT_AUTHORITY_STALE"),
    ],
)
def test_r00_stale_parent_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
    message: str,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    request = _request(parent)
    request[field] = value
    with pytest.raises(RuntimeError, match=message):
        service.start_r00(request, path=db)


def test_identical_owner_authorization_retry_preserves_original_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    payload = {
        "authorization_id": "RAUTH-R00-idempotent",
        "gate": "R00",
        "action": "START",
        "confirmed": True,
        "expected_parent_strategy_id": parent["strategy_champion_id"],
        "expected_parent_authority_sha256": parent["parent_authority_sha256"],
        "cumulative_strategy_e2e_authority": OWNER_CUMULATIVE_E2E_AUTHORITY,
        "h1_minimum_trades_per_month": 24,
        "payload_sha256": "e" * 64,
        "authorized_utc": "2026-09-25T00:00:00+00:00",
    }
    first = create_authorization(payload, path=db)
    retry = dict(payload)
    retry["authorized_utc"] = "2026-09-25T00:01:00+00:00"
    second = create_authorization(retry, path=db)

    assert second == first
    assert second["authorized_utc"] == "2026-09-25T00:00:00+00:00"


def test_r00_freezes_authority_and_stops_waiting_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    result = service.start_r00(_request(parent), path=db)

    assert result["current_gate"] == "R00"
    assert result["gate_state"] == "PASS_WAITING_OWNER"
    assert result["parent_strategy_id"] == parent["strategy_champion_id"]
    assert result["feature_contract"] == FEATURE_CONTRACT
    assert result["integrity"]["status"] == "VERIFIED"
    assert result["training_count"] == 0
    assert result["onnx_count"] == 0
    assert result["research_challenger_count"] == 0
    assert result["champion_mutation"] == "NONE"
    assert result["r01_executable"] is False
    assert result["next_gate_authorized"] is False
    assert result["artifact_count"] >= 5
    assert result["research_policy"]["sample_policy"][
        "strategy_optimizer_policy_inherited"
    ] is False
    assert result["research_policy"]["sample_policy"][
        "h1_minimum_sample_trade_policy"
    ]["status"] == "FROZEN_OWNER_AUTHORITY"
    assert result["research_policy"]["sample_policy"][
        "h1_minimum_sample_trade_policy"
    ]["value"] == 24
    assert result["research_policy"]["sample_policy"][
        "h1_minimum_sample_trade_policy"
    ]["unit"] == "TRADES_PER_H1_MONTH"
    assert result["owner_authorization"]["h1_minimum_trades_per_month"] == 24

    memory = list_memory_events(result["research_id"], path=db)
    assert len(memory) == 1
    assert memory[0]["stage"] == "R00"
    assert memory[0]["learning_zone"] == "FOUNDATION"
    assert memory[0]["adaptive_eligible"] is False


def test_r00_duplicate_start_is_idempotent_and_does_not_advance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    first = service.start_r00(_request(parent), path=db)
    second = service.start_r00(_request(parent), path=db)

    assert second["research_id"] == first["research_id"]
    assert second["idempotent"] is True
    assert second["current_gate"] == "R00"
    assert second["gate_state"] == "PASS_WAITING_OWNER"

    set_research_sample_configuration(25, path=db)
    changed = _request(parent)
    changed["h1_minimum_trades_per_month"] = 25
    third = service.start_r00(changed, path=db)
    assert third["idempotent"] is True
    assert third["research_policy"]["sample_policy"][
        "h1_minimum_sample_trade_policy"
    ]["value"] == 24
    assert get_research_sample_configuration(path=db)[
        "h1_minimum_trades_per_month"
    ] == 25


def test_restart_preserves_pass_waiting_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    result = service.start_r00(_request(parent), path=db)

    assert recover_incomplete_research(path=db) == []
    persisted = get_research(result["research_id"], path=db)
    assert persisted is not None
    assert persisted["current_gate"] == "R00"
    assert persisted["gate_state"] == "PASS_WAITING_OWNER"


def test_r00_parent_authority_and_memory_are_sql_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    result = service.start_r00(_request(parent), path=db)

    from max_backend.db import connect

    memory_id = list_memory_events(result["research_id"], path=db)[0]["event_id"]
    with connect(db) as conn:
        with pytest.raises(Exception, match="RESEARCH_PARENT_AUTHORITY_IMMUTABLE"):
            conn.execute(
                "UPDATE research_projects SET parent_strategy_id='TAMPERED' "
                "WHERE research_id=?",
                (result["research_id"],),
            )
    with connect(db) as conn:
        with pytest.raises(Exception, match="RESEARCH_MEMORY_APPEND_ONLY"):
            conn.execute(
                "DELETE FROM research_memory_events WHERE event_id=?",
                (memory_id,),
            )
    with connect(db) as conn:
        with pytest.raises(Exception, match="RESEARCH_AUTHORIZATION_IMMUTABLE"):
            conn.execute(
                "UPDATE research_authorizations SET action='TAMPERED' "
                "WHERE authorization_id=?",
                (result["owner_authorization_id"],),
            )


def test_restart_converts_uncommitted_r00_to_error_waiting_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    authorization = create_authorization(
        {
            "authorization_id": "RAUTH-R00-recovery",
            "gate": "R00",
            "action": "START",
            "confirmed": True,
            "expected_parent_strategy_id": parent["strategy_champion_id"],
            "expected_parent_authority_sha256": parent["parent_authority_sha256"],
            "cumulative_strategy_e2e_authority": OWNER_CUMULATIVE_E2E_AUTHORITY,
            "h1_minimum_trades_per_month": 24,
            "payload_sha256": "f" * 64,
            "authorized_utc": "2026-09-25T00:00:00+00:00",
        },
        path=db,
    )
    row = create_research(
        {
            "research_id": "RSRCH-recovery",
            "research_parent_id": parent["research_parent_id"],
            "parent_strategy_id": parent["strategy_champion_id"],
            "parent_authority_sha256": parent["parent_authority_sha256"],
            "parent_manifest_path": "artifacts/research/recovery/parent.json",
            "parent_manifest_sha256": "1" * 64,
            "feature_contract": FEATURE_CONTRACT,
            "current_gate": "R00",
            "gate_state": "STARTING",
            "gate_input_manifest_sha": "2" * 64,
            "gate_output_manifest_sha": None,
            "owner_authorization_id": authorization["authorization_id"],
            "authorized_utc": authorization["authorized_utc"],
            "hardware_snapshot_path": "artifacts/research/recovery/hardware.json",
            "hardware_snapshot_sha256": "3" * 64,
            "label_authority": {},
            "candidate_identity_contract": {},
            "artifact_lineage_contract": {},
            "research_policy": {},
            "unresolved_authority": [],
            "created_utc": "2026-09-25T00:00:00+00:00",
        },
        path=db,
    )
    assert row["gate_state"] == "STARTING"

    assert recover_incomplete_research(path=db) == ["RSRCH-recovery"]
    recovered = get_research("RSRCH-recovery", path=db)
    assert recovered is not None
    assert recovered["current_gate"] == "R00"
    assert recovered["gate_state"] == "ERROR_WAITING_OWNER"


def test_parent_manifest_mutation_is_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    result = service.start_r00(_request(parent), path=db)
    parent_path = tmp_path / result["parent_manifest_path"]
    payload = json.loads(parent_path.read_text(encoding="utf-8"))
    payload["main_symbol"] = "TAMPERED"
    parent_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    integrity = service.validate_frozen_research(result["research_id"], path=db)
    assert integrity["status"] == "INTEGRITY_FAIL"
    assert "parent_manifest_hash" in integrity["failures"]


def test_r01_and_scientist_transitions_are_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    result = service.start_r00(_request(parent), path=db)
    with pytest.raises(RuntimeError, match="R01_REQUIRES_EXPLICIT_R01_START_AUTHORIZATION"):
        service.attempt_gate_transition(
            result["research_id"],
            "R01",
            authority="OWNER",
            path=db,
        )
    with pytest.raises(RuntimeError, match="RESEARCH_GATE_AUTHORITY_FORBIDDEN:SCIENTIST"):
        service.attempt_gate_transition(
            result["research_id"],
            "R01",
            authority="SCIENTIST",
            path=db,
        )


def test_protected_research_memory_is_never_adaptive_feedback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    result = service.start_r00(_request(parent), path=db)
    event = append_memory_event(
        research_id=result["research_id"],
        event_type="LOCKED_OOS_RESULT",
        stage="LOCKED_OOS",
        status="FAIL",
        payload={"note": "retained evaluation evidence only"},
        source_manifest_sha256=result["gate_output_manifest_sha"],
        path=db,
    )
    assert event["learning_zone"] == "PROTECTED"
    assert event["adaptive_eligible"] is False


def test_research_candidate_identity_is_deterministic_and_parent_bound() -> None:
    spec = {
        "research_id": "RSRCH-A",
        "model_family": "lightgbm",
        "topology_spec": {"leaves": 31},
        "feature_contract": FEATURE_CONTRACT,
        "label_contract": "LABEL-V1",
        "seed": 42,
        "preprocessing": {"scaler": "none"},
        "training_configuration": {"rounds": 100},
        "parent_lineage": {"research_parent_id": "RPAR-A"},
    }
    first = candidate_id(spec)
    assert candidate_id(deepcopy(spec)) == first
    changed = deepcopy(spec)
    changed["parent_lineage"]["research_parent_id"] = "RPAR-B"
    assert candidate_id(changed) != first


def test_research_sample_policy_does_not_inherit_strategy_optimizer() -> None:
    preview = research_sample_policy()
    assert preview["strategy_optimizer_policy_inherited"] is False
    assert preview["h1_minimum_sample_trade_policy"]["value"] is None
    assert preview["h1_minimum_sample_trade_policy"]["status"] == "OWNER_DECISION_REQUIRED"

    frozen = research_sample_policy(24)
    assert frozen["strategy_optimizer_policy_inherited"] is False
    assert frozen["h1_minimum_sample_trade_policy"]["value"] == 24
    assert frozen["h1_minimum_sample_trade_policy"]["status"] == "FROZEN_OWNER_AUTHORITY"
    assert frozen["kpi_policy"]["status"] == "FROZEN_AUTHORITY_CONTRACT"
    assert frozen["kpi_policy"]["strategy_optimizer_kpi_reuse"] is False


def test_hardware_profile_hash_ignores_dynamic_free_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        hardware,
        "_physical_cores",
        lambda: {
            "physical_cores": 6,
            "logical_threads": 12,
            "planning_cores": 6,
            "source": "TEST",
        },
    )
    monkeypatch.setattr(hardware, "_cpu_name", lambda: "Test CPU")
    memory_values = iter(
        [
            {"total_bytes": 32_000, "available_bytes": 16_000, "source": "TEST"},
            {"total_bytes": 32_000, "available_bytes": 8_000, "source": "TEST"},
        ]
    )
    monkeypatch.setattr(hardware, "_memory", lambda: next(memory_values))
    gpu_values = iter(
        [
            [{
                "index": 0,
                "name": "Test GPU",
                "memory_total_mb": 8192,
                "memory_free_mb": 7000,
                "driver_version": "test",
            }],
            [{
                "index": 0,
                "name": "Test GPU",
                "memory_total_mb": 8192,
                "memory_free_mb": 4000,
                "driver_version": "test",
            }],
        ]
    )
    monkeypatch.setattr(hardware, "_nvidia", lambda: next(gpu_values))
    disk_values = iter(
        [
            SimpleNamespace(total=1_000_000, free=500_000),
            SimpleNamespace(total=1_000_000, free=300_000),
        ]
    )
    monkeypatch.setattr(hardware.shutil, "disk_usage", lambda _root: next(disk_values))

    first = hardware.collect_hardware_snapshot(tmp_path)
    second = hardware.collect_hardware_snapshot(tmp_path)

    assert first["profile_hash"] == second["profile_hash"]
    assert (
        first["dynamic_resources"]["memory_available_bytes"]
        != second["dynamic_resources"]["memory_available_bytes"]
    )
    assert first["capacity_authority"]["global_parameter_hard_ceiling"] is None


def _create_starting_r00_for_transition_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    research_id: str,
) -> tuple[Path, dict]:
    db, parent = _setup(tmp_path, monkeypatch)
    authorization = create_authorization(
        {
            "authorization_id": "RAUTH-" + research_id,
            "gate": "R00",
            "action": "START",
            "confirmed": True,
            "expected_parent_strategy_id": parent["strategy_champion_id"],
            "expected_parent_authority_sha256": parent["parent_authority_sha256"],
            "cumulative_strategy_e2e_authority": OWNER_CUMULATIVE_E2E_AUTHORITY,
            "h1_minimum_trades_per_month": 24,
            "payload_sha256": stable_hash({"research_id": research_id}),
            "authorized_utc": "2026-09-25T00:00:00+00:00",
        },
        path=db,
    )
    row = create_research(
        {
            "research_id": research_id,
            "research_parent_id": parent["research_parent_id"],
            "parent_strategy_id": parent["strategy_champion_id"],
            "parent_authority_sha256": parent["parent_authority_sha256"],
            "parent_manifest_path": f"artifacts/research/{research_id}/parent.json",
            "parent_manifest_sha256": "1" * 64,
            "feature_contract": FEATURE_CONTRACT,
            "current_gate": "R00",
            "gate_state": "STARTING",
            "gate_input_manifest_sha": "2" * 64,
            "gate_output_manifest_sha": None,
            "owner_authorization_id": authorization["authorization_id"],
            "authorized_utc": authorization["authorized_utc"],
            "hardware_snapshot_path": f"artifacts/research/{research_id}/hardware.json",
            "hardware_snapshot_sha256": "3" * 64,
            "label_authority": {},
            "candidate_identity_contract": {},
            "artifact_lineage_contract": {},
            "research_policy": {},
            "unresolved_authority": [],
            "created_utc": "2026-09-25T00:00:00+00:00",
        },
        path=db,
    )
    return db, row


@pytest.mark.parametrize(
    "terminal_state",
    ["PASS_WAITING_OWNER", "FAIL_WAITING_OWNER", "ERROR_WAITING_OWNER"],
)
def test_r00_state_machine_allows_starting_to_each_terminal_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_state: str,
) -> None:
    db, row = _create_starting_r00_for_transition_test(
        tmp_path,
        monkeypatch,
        research_id="RSRCH-transition-" + terminal_state,
    )
    updated = service.update_gate_state(
        row["research_id"],
        gate_state=terminal_state,
        path=db,
    )
    assert updated["gate_state"] == terminal_state


@pytest.mark.parametrize(
    "terminal_state",
    ["PASS_WAITING_OWNER", "FAIL_WAITING_OWNER", "ERROR_WAITING_OWNER"],
)
def test_r00_terminal_state_is_immutable_for_all_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_state: str,
) -> None:
    from max_backend.db import connect
    from max_backend.research_contract import RESEARCH_STATES

    db, row = _create_starting_r00_for_transition_test(
        tmp_path,
        monkeypatch,
        research_id="RSRCH-terminal-" + terminal_state,
    )
    service.update_gate_state(
        row["research_id"],
        gate_state=terminal_state,
        path=db,
    )

    for target in RESEARCH_STATES:
        with pytest.raises(RuntimeError, match="R00_GATE_STATE_TRANSITION_INVALID"):
            service.update_gate_state(
                row["research_id"],
                gate_state=target,
                path=db,
            )

    with connect(db) as conn:
        with pytest.raises(Exception, match="R00_GATE_STATE_TRANSITION_INVALID"):
            conn.execute(
                "UPDATE research_projects SET gate_state='STARTING' WHERE research_id=?",
                (row["research_id"],),
            )

    with connect(db) as conn:
        with pytest.raises(Exception, match="R00_CURRENT_GATE_IMMUTABLE"):
            conn.execute(
                """
                UPDATE research_projects
                SET current_gate='R01',gate_state='STARTING'
                WHERE research_id=?
                """,
                (row["research_id"],),
            )


def test_r00_integrity_detects_db_state_vs_sealed_output_state_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    result = service.start_r00(_request(parent), path=db)
    output_path = (
        tmp_path
        / "artifacts"
        / "research"
        / result["research_id"]
        / "r00_output_manifest.json"
    )
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    payload["gate_state"] = "FAIL_WAITING_OWNER"
    payload = service._seal(payload, "manifest_sha256")
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    integrity = service.validate_frozen_research(result["research_id"], path=db)
    assert integrity["status"] == "INTEGRITY_FAIL"
    assert integrity["checks"]["gate_state_output_binding"] is False


def test_r00_integrity_detects_output_artifact_status_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from max_backend.db import connect

    db, parent = _setup(tmp_path, monkeypatch)
    result = service.start_r00(_request(parent), path=db)
    with connect(db) as conn:
        conn.execute(
            "UPDATE artifact_registry SET status='TAMPERED' "
            "WHERE owner_type='RESEARCH' AND owner_id=?",
            (result["research_id"] + ":output",),
        )

    integrity = service.validate_frozen_research(result["research_id"], path=db)
    assert integrity["status"] == "INTEGRITY_FAIL"
    assert (
        "output_status_matches_gate_state"
        in integrity["artifact_lineage"]["failures"]
    )


def test_r00_integrity_detects_state_artifact_status_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from max_backend.db import connect

    db, parent = _setup(tmp_path, monkeypatch)
    result = service.start_r00(_request(parent), path=db)
    with connect(db) as conn:
        conn.execute(
            "UPDATE artifact_registry SET status='TAMPERED' "
            "WHERE owner_type='RESEARCH_STATE' AND owner_id=?",
            (result["research_id"],),
        )

    integrity = service.validate_frozen_research(result["research_id"], path=db)
    assert integrity["status"] == "INTEGRITY_FAIL"
    assert "state_status_matches_gate_state" in integrity["artifact_lineage"]["failures"]


def test_r00_integrity_detects_output_manifest_hash_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    result = service.start_r00(_request(parent), path=db)
    output_path = (
        tmp_path
        / "artifacts"
        / "research"
        / result["research_id"]
        / "r00_output_manifest.json"
    )
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    payload["r01_authorized"] = True
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    integrity = service.validate_frozen_research(result["research_id"], path=db)
    assert integrity["status"] == "INTEGRITY_FAIL"
    assert integrity["checks"]["output_manifest_hash"] is False


def test_r00_fault_after_artifact_registration_fails_closed_without_reexecution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    original_register = service._register_artifacts
    original_validators = service._foundation_validators
    calls = {"validators": 0}

    def counted_validators(**kwargs):
        calls["validators"] += 1
        return original_validators(**kwargs)

    def fail_after_registration(**kwargs):
        original_register(**kwargs)
        raise RuntimeError("INJECTED_AFTER_ARTIFACT_REGISTRATION")

    monkeypatch.setattr(service, "_foundation_validators", counted_validators)
    monkeypatch.setattr(service, "_register_artifacts", fail_after_registration)

    with pytest.raises(RuntimeError, match="INJECTED_AFTER_ARTIFACT_REGISTRATION"):
        service.start_r00(_request(parent), path=db)

    research_id = service._research_id(parent)
    persisted = get_research(research_id, path=db)
    assert persisted is not None
    assert persisted["current_gate"] == "R00"
    assert persisted["gate_state"] == "ERROR_WAITING_OWNER"
    assert persisted["gate_output_manifest_sha"] is None
    assert persisted["training_count"] == 0
    assert persisted["onnx_count"] == 0
    assert persisted["research_challenger_count"] == 0
    assert persisted["champion_mutation"] == "NONE"
    assert (
        service.validate_frozen_research(research_id, path=db)["status"]
        == "INTEGRITY_FAIL"
    )
    assert recover_incomplete_research(path=db) == []

    retry = service.start_r00(_request(parent), path=db)
    assert retry["idempotent"] is True
    assert retry["current_gate"] == "R00"
    assert retry["r01_executable"] is False
    assert retry["next_gate_authorized"] is False
    assert retry["training_count"] == 0
    assert retry["onnx_count"] == 0
    assert retry["research_challenger_count"] == 0
    assert retry["champion_mutation"] == "NONE"
    assert calls["validators"] == 1


def test_r00_fault_after_terminal_commit_cannot_downgrade_terminal_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    original_commit = service.commit_r00_terminal_authority
    original_validators = service._foundation_validators
    calls = {"validators": 0}

    def counted_validators(**kwargs):
        calls["validators"] += 1
        return original_validators(**kwargs)

    def fail_after_terminal_commit(**kwargs):
        original_commit(**kwargs)
        raise RuntimeError("INJECTED_AFTER_TERMINAL_COMMIT")

    monkeypatch.setattr(service, "_foundation_validators", counted_validators)
    monkeypatch.setattr(
        service,
        "commit_r00_terminal_authority",
        fail_after_terminal_commit,
    )

    with pytest.raises(RuntimeError, match="INJECTED_AFTER_TERMINAL_COMMIT"):
        service.start_r00(_request(parent), path=db)

    research_id = service._research_id(parent)
    persisted = get_research(research_id, path=db)
    assert persisted is not None
    assert persisted["gate_state"] == "PASS_WAITING_OWNER"
    assert service.validate_frozen_research(research_id, path=db)["status"] == "VERIFIED"
    assert recover_incomplete_research(path=db) == []

    retry = service.start_r00(_request(parent), path=db)
    assert retry["idempotent"] is True
    assert retry["gate_state"] == "PASS_WAITING_OWNER"
    assert retry["r01_executable"] is False
    assert retry["training_count"] == 0
    assert retry["onnx_count"] == 0
    assert retry["research_challenger_count"] == 0
    assert retry["champion_mutation"] == "NONE"
    assert calls["validators"] == 1


def test_research_sample_configuration_persists_and_is_editable_after_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    first = service.start_r00(_request(parent), path=db)
    assert first["research_policy"]["sample_policy"][
        "h1_minimum_sample_trade_policy"
    ]["value"] == 24

    saved = set_research_sample_configuration(31, path=db)
    assert saved["h1_minimum_trades_per_month"] == 31
    reloaded = get_research_sample_configuration(path=db)
    assert reloaded["configured"] is True
    assert reloaded["h1_minimum_trades_per_month"] == 31

    historical = service.research_detail(first["research_id"], path=db)
    assert historical["research_policy"]["sample_policy"][
        "h1_minimum_sample_trade_policy"
    ]["value"] == 24
    assert historical["owner_authorization"][
        "h1_minimum_trades_per_month"
    ] == 24

    current = service.current_research(path=db)
    assert current["owner_view"]["sample_requirement"]["value"] == 31
    assert current["owner_view"]["execution_sample_requirement"]["value"] == 31
    assert (
        current["owner_view"]["execution_sample_requirement"]["source"]
        == "Next execution will use current configuration"
    )
