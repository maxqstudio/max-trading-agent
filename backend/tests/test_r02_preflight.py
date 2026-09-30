from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from max_backend import research_api
from max_backend import research_r02_service as r02


def _run(state: str = "PASS_WAITING_OWNER") -> dict:
    return {
        "run_id": "RRUN-R02-TEST",
        "research_id": "RSRCH-R02-TEST",
        "state": state,
        "output_manifest_sha": "a" * 64,
    }


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    latest: dict | None = None,
    run: dict | None = None,
    integrity: dict | None = None,
    side_effects: dict | None = None,
) -> None:
    current = latest if latest is not None else {"research_id": "RSRCH-R02-TEST"}
    current_run = run if run is not None else _run()
    current_integrity = integrity if integrity is not None else {
        "research_id": "RSRCH-R02-TEST",
        "status": "VERIFIED_DISCOVERY_ONLY",
        "verification_scope": "DISCOVERY_ONLY",
    }
    current_side_effects = side_effects if side_effects is not None else {
        "training_count": 0,
        "onnx_count": 0,
        "research_challenger_count": 0,
        "champion_mutation": "NONE",
        "status": "PASS",
    }
    monkeypatch.setattr(r02, "latest_research", lambda **_kwargs: deepcopy(current))
    monkeypatch.setattr(r02, "get_r01_run", lambda *_args, **_kwargs: deepcopy(current_run))
    monkeypatch.setattr(r02, "validate_r01_integrity", lambda **_kwargs: deepcopy(current_integrity))
    monkeypatch.setattr(r02, "verify_no_training_side_effects", lambda *_args, **_kwargs: deepcopy(current_side_effects))
    monkeypatch.setattr(r02, "get_r02_outcome_ledger", lambda *_args, **_kwargs: None)


def test_fresh_epoch_without_research_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(r02, "latest_research", lambda **_kwargs: None)
    result = r02.r02_preflight(path=Path("unused.db"))
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "R02_CURRENT_R00_AND_R01_REQUIRED"
    assert result["r02_executable"] is False
    assert result["runtime_start_available"] is False
    assert result["model_training"] == 0


def test_missing_r01_run_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, run={})
    monkeypatch.setattr(r02, "get_r01_run", lambda *_args, **_kwargs: None)
    result = r02.r02_preflight(path=Path("unused.db"))
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "R02_ACCEPTED_R01_REQUIRED"
    assert result["r01_state"] == "NOT_STARTED"


@pytest.mark.parametrize("state", ["STARTING", "FAIL_WAITING_OWNER", "ERROR_WAITING_OWNER"])
def test_nonpassing_r01_is_blocked(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    _install(monkeypatch, run=_run(state))
    result = r02.r02_preflight(path=Path("unused.db"))
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "R02_ACCEPTED_R01_REQUIRED"
    assert result["r01_state"] == state


def test_r01_pass_without_output_authority_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run()
    run["output_manifest_sha"] = None
    _install(monkeypatch, run=run)
    with pytest.raises(RuntimeError, match="R02_R01_OUTPUT_AUTHORITY_MISSING"):
        r02.r02_preflight(path=Path("unused.db"))



def test_r01_pass_with_malformed_output_authority_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run()
    run["output_manifest_sha"] = "not-a-sha"
    _install(monkeypatch, run=run)
    with pytest.raises(RuntimeError, match="R02_R01_OUTPUT_AUTHORITY_INVALID"):
        r02.r02_preflight(path=Path("unused.db"))


@pytest.mark.parametrize(
    "integrity",
    [
        {"research_id": "RSRCH-R02-TEST", "status": "INTEGRITY_FAIL"},
        {
            "research_id": "OTHER",
            "status": "VERIFIED_DISCOVERY_ONLY",
            "verification_scope": "DISCOVERY_ONLY",
        },
        {
            "research_id": "RSRCH-R02-TEST",
            "status": "VERIFIED_DISCOVERY_ONLY",
            "verification_scope": "FULL",
        },
    ],
)
def test_r01_integrity_or_identity_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    integrity: dict,
) -> None:
    _install(monkeypatch, integrity=integrity)
    with pytest.raises(RuntimeError, match="R02_R01_INTEGRITY_REQUIRED"):
        r02.r02_preflight(path=Path("unused.db"))


def test_prior_training_or_promotion_side_effect_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        side_effects={
            "training_count": 1,
            "onnx_count": 0,
            "research_challenger_count": 0,
            "champion_mutation": "NONE",
            "status": "FAIL",
        },
    )
    with pytest.raises(RuntimeError, match="R02_PREVIOUS_SIDE_EFFECT_REGRESSION"):
        r02.r02_preflight(path=Path("unused.db"))


def test_valid_r01_pass_is_ready_but_cannot_start_r02(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch)
    result = r02.r02_preflight(path=Path("unused.db"))
    assert result["status"] == "READY_FOR_OWNER_AUTHORIZATION"
    assert result["research_id"] == "RSRCH-R02-TEST"
    assert result["r01_state"] == "PASS_WAITING_OWNER"
    assert result["r01_integrity"] == "VERIFIED_DISCOVERY_ONLY"
    assert result["source_foundation_ready"] is True
    assert result["owner_authorization_required"] is True
    assert result["owner_authorized"] is False
    assert result["runtime_start_available"] is False
    assert result["r02_executable"] is False
    assert result["model_training"] == 0
    assert result["onnx"] == 0
    assert result["research_challenger"] == 0
    assert result["champion_mutation"] == "NONE"
    assert result["contract"]["cheap_screen_qualification_authority"] is False


def test_api_preflight_maps_contract_without_start_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {"schema": "MAX_RESEARCH_DISCOVERY_R02_V1", "status": "BLOCKED"}
    monkeypatch.setattr(research_api, "r02_preflight", lambda: deepcopy(expected))
    assert research_api.get_r02_preflight() == expected
    assert not hasattr(research_api, "start_research_r02")



def test_frozen_discovery_block_prevents_second_authorization_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch)
    monkeypatch.setattr(
        r02,
        "get_r02_outcome_ledger",
        lambda research_id, **_kwargs: {
            "integrity_status": "VERIFIED",
            "block_id": "RDISC-TEST",
            "block": {
                "block_id": "RDISC-TEST",
                "authorization_id": "RAUTH-R02-TEST",
                "plan_id": "RPLAN-TEST",
                "plan_sha256": "b" * 64,
                "candidate_count": 3,
                "compute_budget": {
                    "value": 120,
                    "unit": "FIT_SECONDS",
                    "execution_semantics": "EXECUTOR_BOUNDED_FIT_SECONDS_V1",
                },
            },
            "terminal": None,
        },
    )
    result = r02.r02_preflight(path=Path("unused.db"))
    assert result["status"] == "FROZEN_WAITING_EXECUTION"
    assert result["owner_authorization_required"] is False
    assert result["owner_authorized"] is True
    assert result["runtime_start_available"] is False
    assert result["r02_executable"] is False



def test_terminal_outcome_ledger_reports_complete_without_qualification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch)
    monkeypatch.setattr(
        r02,
        "get_r02_outcome_ledger",
        lambda research_id, **_kwargs: {
            "integrity_status": "VERIFIED",
            "block": {
                "block_id": "RDISC-COMPLETE",
                "authorization_id": "RAUTH-R02-COMPLETE",
                "plan_id": "RPLAN-COMPLETE",
                "plan_sha256": "b" * 64,
                "candidate_count": 3,
                "compute_budget": {
                    "value": 120,
                    "unit": "FIT_SECONDS",
                    "execution_semantics": "EXECUTOR_BOUNDED_FIT_SECONDS_V1",
                },
            },
            "terminal": {
                "terminal_id": "RTERM-COMPLETE",
                "outcome_manifest_sha256": "c" * 64,
                "screen_pass_count": 1,
                "screen_fail_count": 1,
                "execution_error_count": 1,
                "compute_consumed": {"value": 60.0, "unit": "FIT_SECONDS"},
            },
        },
    )
    result = r02.r02_preflight(path=Path("unused.db"))
    assert result["status"] == "COMPLETE_WAITING_OWNER"
    assert result["cheap_screen_qualification_authority"] is False
    assert result["qualified_pool_admission_authority"] == "R03_FULL_WFA_ONLY"
    assert result["r02_executable"] is False
    assert result["model_training"] == 0


def test_terminal_preflight_requires_verified_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch)
    monkeypatch.setattr(
        r02,
        "get_r02_outcome_ledger",
        lambda *_args, **_kwargs: {
            "integrity_status": "FAILED",
            "block": {"block_id": "RDISC-CORRUPT"},
            "terminal": {"state": "COMPLETE_WAITING_OWNER"},
        },
    )
    with pytest.raises(RuntimeError, match="R02_LEDGER_INTEGRITY_VERIFICATION_REQUIRED"):
        r02.r02_preflight(path=Path("unused.db"))
