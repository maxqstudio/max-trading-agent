from __future__ import annotations

from copy import deepcopy

import pytest

from max_backend.research_r02_outcome import (
    build_candidate_outcome,
    build_terminal_manifest,
)


CANDIDATES = ["RCAND-A", "RCAND-B", "RCAND-C"]


def _outcome(candidate_id: str, status: str = "SCREEN_PASS") -> dict:
    return {
        "candidate_id": candidate_id,
        "status": status,
        "metrics": {"proxy_score": 0.75, "folds_seen": [0, 1]},
        "compute_consumed": {"value": 10, "unit": "FIT_SECONDS"},
        "failure_code": None if status == "SCREEN_PASS" else "SCREEN_REJECT",
    }


def test_candidate_outcome_is_deterministic_and_has_no_qualification_authority() -> None:
    first = build_candidate_outcome(
        _outcome("RCAND-A"),
        block_id="RDISC-TEST",
        candidate_ids=CANDIDATES,
        budget_unit="FIT_SECONDS",
    )
    second = build_candidate_outcome(
        _outcome("RCAND-A"),
        block_id="RDISC-TEST",
        candidate_ids=CANDIDATES,
        budget_unit="FIT_SECONDS",
    )
    assert first == second
    assert first["cheap_screen_qualification_authority"] is False
    assert first["scientific_qualification"] is False
    assert first["qualified_pool_admission_authority"] == "R03_FULL_WFA_ONLY"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_metric_is_rejected(value: float) -> None:
    request = _outcome("RCAND-A")
    request["metrics"]["proxy_score"] = value
    with pytest.raises(ValueError, match="R02_OUTCOME_NONFINITE_VALUE"):
        build_candidate_outcome(
            request,
            block_id="RDISC-TEST",
            candidate_ids=CANDIDATES,
            budget_unit="FIT_SECONDS",
        )


def test_screen_pass_forbids_failure_code() -> None:
    request = _outcome("RCAND-A")
    request["failure_code"] = "SHOULD_NOT_EXIST"
    with pytest.raises(
        ValueError,
        match="R02_OUTCOME_PASS_FAILURE_CODE_FORBIDDEN",
    ):
        build_candidate_outcome(
            request,
            block_id="RDISC-TEST",
            candidate_ids=CANDIDATES,
            budget_unit="FIT_SECONDS",
        )


@pytest.mark.parametrize("status", ["SCREEN_FAIL", "EXECUTION_ERROR"])
def test_nonpass_outcome_requires_failure_code(status: str) -> None:
    request = _outcome("RCAND-A", status)
    request["failure_code"] = None
    with pytest.raises(ValueError, match="R02_OUTCOME_FAILURE_CODE_REQUIRED"):
        build_candidate_outcome(
            request,
            block_id="RDISC-TEST",
            candidate_ids=CANDIDATES,
            budget_unit="FIT_SECONDS",
        )


def test_outcome_root_fields_are_exact() -> None:
    request = _outcome("RCAND-A")
    request["qualified"] = True
    with pytest.raises(ValueError, match="R02_OUTCOME_FIELDS_INVALID"):
        build_candidate_outcome(
            request,
            block_id="RDISC-TEST",
            candidate_ids=CANDIDATES,
            budget_unit="FIT_SECONDS",
        )


def test_terminal_manifest_orders_by_frozen_candidate_authority() -> None:
    requests = [
        _outcome("RCAND-C", "EXECUTION_ERROR"),
        _outcome("RCAND-A"),
        _outcome("RCAND-B", "SCREEN_FAIL"),
    ]
    first = build_terminal_manifest(
        block_id="RDISC-TEST",
        candidate_ids=CANDIDATES,
        compute_budget={"value": 100, "unit": "FIT_SECONDS"},
        outcome_requests=requests,
    )
    second = build_terminal_manifest(
        block_id="RDISC-TEST",
        candidate_ids=CANDIDATES,
        compute_budget={"value": 100, "unit": "FIT_SECONDS"},
        outcome_requests=list(reversed(requests)),
    )
    assert first == second
    assert [row["candidate_id"] for row in first["outcomes"]] == CANDIDATES
    assert first["screen_pass_count"] == 1
    assert first["screen_fail_count"] == 1
    assert first["execution_error_count"] == 1
    assert first["state"] == "COMPLETE_WAITING_OWNER"
    assert first["scientific_qualification"] is False


def test_terminal_manifest_requires_every_candidate_exactly_once() -> None:
    requests = [_outcome("RCAND-A"), _outcome("RCAND-B", "SCREEN_FAIL")]
    with pytest.raises(ValueError, match="R02_TERMINAL_CANDIDATE_SET_MISMATCH"):
        build_terminal_manifest(
            block_id="RDISC-TEST",
            candidate_ids=CANDIDATES,
            compute_budget={"value": 100, "unit": "FIT_SECONDS"},
            outcome_requests=requests,
        )


def test_terminal_manifest_enforces_budget_and_unit() -> None:
    requests = [_outcome(candidate_id) for candidate_id in CANDIDATES]
    too_much = deepcopy(requests)
    for request in too_much:
        request["compute_consumed"]["value"] = 40
    with pytest.raises(ValueError, match="R02_TERMINAL_COMPUTE_BUDGET_EXCEEDED"):
        build_terminal_manifest(
            block_id="RDISC-TEST",
            candidate_ids=CANDIDATES,
            compute_budget={"value": 100, "unit": "FIT_SECONDS"},
            outcome_requests=too_much,
        )

    wrong_unit = deepcopy(requests)
    wrong_unit[0]["compute_consumed"]["unit"] = "GPU_SECONDS"
    with pytest.raises(ValueError, match="R02_OUTCOME_COMPUTE_UNIT_MISMATCH"):
        build_terminal_manifest(
            block_id="RDISC-TEST",
            candidate_ids=CANDIDATES,
            compute_budget={"value": 100, "unit": "FIT_SECONDS"},
            outcome_requests=wrong_unit,
        )
