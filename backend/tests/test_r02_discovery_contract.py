from __future__ import annotations

from copy import deepcopy

import pytest

from max_backend.research_contract import FEATURE_CONTRACT
from max_backend.research_r02_contract import (
    build_discovery_plan,
    r02_discovery_contract,
)


def _candidate(family: str, seed: int) -> dict:
    return {
        "research_id": "RSRCH-R02-TEST",
        "model_family": family,
        "topology_spec": {"depth": 3, "width": 64},
        "feature_contract": FEATURE_CONTRACT,
        "label_contract": "MAX_RESEARCH_FIRST_BARRIER_LABEL_R01_V1",
        "seed": seed,
        "preprocessing": {"scaling": "NONE"},
        "training_configuration": {"objective": "MULTICLASS"},
        "parent_lineage": {
            "research_parent_id": "RPAR-R02-TEST",
            "parent_strategy_id": "STRAT-R02-TEST",
            "dataset_id": "RDATA-R02-TEST",
        },
    }


def _request() -> dict:
    return {
        "research_id": "RSRCH-R02-TEST",
        "r01_output_manifest_sha256": "a" * 64,
        "feature_contract": FEATURE_CONTRACT,
        "label_contract": "MAX_RESEARCH_FIRST_BARRIER_LABEL_R01_V1",
        "parent_lineage": {
            "research_parent_id": "RPAR-R02-TEST",
            "parent_strategy_id": "STRAT-R02-TEST",
            "dataset_id": "RDATA-R02-TEST",
        },
        "candidate_count": 3,
        "compute_budget": {"value": 120, "unit": "FIT_SECONDS"},
        "candidates": [
            _candidate("xgboost", 42),
            _candidate("lightgbm", 11),
            _candidate("random_forest", 7),
        ],
    }


def test_discovery_contract_preserves_gate_boundaries() -> None:
    contract = r02_discovery_contract()
    assert contract["owner_start_required"] is True
    assert contract["bounded_single_block"] is True
    assert contract["automatic_second_discovery_block"] is False
    assert contract["cheap_screen_qualification_authority"] is False
    assert contract["qualified_pool_admission_authority"] == "R03_FULL_WFA_ONLY"
    assert contract["runtime_execution_implemented"] is False
    assert contract["model_training_authorized"] is False
    assert contract["onnx_authorized"] is False


def test_discovery_plan_is_deterministic_and_order_independent() -> None:
    request = _request()
    first = build_discovery_plan(request)
    second_request = deepcopy(request)
    second_request["candidates"] = list(reversed(second_request["candidates"]))
    second = build_discovery_plan(second_request)

    assert first == second
    assert first["candidate_count"] == 3
    assert first["candidate_ids"] == sorted(first["candidate_ids"])
    assert len(set(first["candidate_ids"])) == 3
    assert first["compute_budget"]["execution_semantics"] == "FROZEN_ONLY_NOT_EXECUTED"
    assert first["cheap_screen_qualification_authority"] is False
    assert first["model_training_performed"] is False
    assert first["runtime_execution_authorized"] is False


@pytest.mark.parametrize("candidate_count", [0, -1, True])
def test_candidate_count_must_be_explicit_positive_integer(candidate_count: object) -> None:
    request = _request()
    request["candidate_count"] = candidate_count
    with pytest.raises(ValueError, match="R02_CANDIDATE_COUNT_INVALID"):
        build_discovery_plan(request)


def test_candidate_count_mismatch_fails_closed() -> None:
    request = _request()
    request["candidate_count"] = 2
    with pytest.raises(ValueError, match="R02_CANDIDATE_COUNT_MISMATCH"):
        build_discovery_plan(request)


@pytest.mark.parametrize(
    "budget",
    [
        None,
        {},
        {"value": 0, "unit": "FIT_SECONDS"},
        {"value": -1, "unit": "FIT_SECONDS"},
        {"value": True, "unit": "FIT_SECONDS"},
        {"value": 1, "unit": ""},
        {"value": 1, "unit": "FIT_SECONDS", "extra": 1},
    ],
)
def test_compute_budget_has_no_hidden_default(budget: object) -> None:
    request = _request()
    request["compute_budget"] = budget
    with pytest.raises(ValueError, match="R02_COMPUTE_BUDGET"):
        build_discovery_plan(request)


def test_duplicate_candidate_identity_is_rejected() -> None:
    request = _request()
    request["candidates"][1] = deepcopy(request["candidates"][0])
    with pytest.raises(ValueError, match="R02_DUPLICATE_CANDIDATE_ID"):
        build_discovery_plan(request)


@pytest.mark.parametrize(
    ("family", "error"),
    [
        ("gru", "R02_TEMPORAL_MODEL_FAMILY_NOT_YET_AUTHORIZED"),
        ("transformer_moe", "R02_TEMPORAL_MODEL_FAMILY_NOT_YET_AUTHORIZED"),
        ("svm", "R02_MODEL_FAMILY_UNSUPPORTED"),
        ("LightGBM", "R02_MODEL_FAMILY_NOT_CANONICAL"),
    ],
)
def test_unopened_or_noncanonical_model_family_is_rejected(family: str, error: str) -> None:
    request = _request()
    request["candidates"][0]["model_family"] = family
    with pytest.raises(ValueError, match=error):
        build_discovery_plan(request)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("research_id", "OTHER", "R02_CANDIDATE_RESEARCH_ID_MISMATCH"),
        ("feature_contract", "OTHER", "R02_CANDIDATE_FEATURE_CONTRACT_MISMATCH"),
        ("label_contract", "OTHER", "R02_CANDIDATE_LABEL_CONTRACT_MISMATCH"),
        ("parent_lineage", {"wrong": True}, "R02_CANDIDATE_PARENT_LINEAGE_MISMATCH"),
    ],
)
def test_candidate_lineage_mismatch_is_rejected(field: str, value: object, error: str) -> None:
    request = _request()
    request["candidates"][0][field] = value
    with pytest.raises(ValueError, match=error):
        build_discovery_plan(request)


def test_candidate_identity_fields_are_exact_and_explicit() -> None:
    request = _request()
    request["candidates"][0]["unexpected"] = True
    with pytest.raises(ValueError, match="R02_CANDIDATE_SPEC_FIELDS_INVALID"):
        build_discovery_plan(request)


def test_plan_rejects_unknown_top_level_fields() -> None:
    request = _request()
    request["hidden_default"] = True
    with pytest.raises(ValueError, match="R02_PLAN_FIELDS_INVALID"):
        build_discovery_plan(request)
