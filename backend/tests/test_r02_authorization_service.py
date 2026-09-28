from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from max_backend import research_api
from max_backend import research_r02_service as r02
from max_backend.research_contract import FEATURE_CONTRACT


def _plan_request() -> dict:
    parent = {
        "research_parent_id": "RPAR-R02-AUTH",
        "parent_strategy_id": "STRAT-R02-AUTH",
        "dataset_id": "RDATA-R02-AUTH",
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
                "research_id": "RSRCH-R02-AUTH",
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
    return {
        "research_id": "RSRCH-R02-AUTH",
        "r01_output_manifest_sha256": "f" * 64,
        "feature_contract": FEATURE_CONTRACT,
        "label_contract": "MAX_RESEARCH_FIRST_BARRIER_LABEL_R01_V1",
        "parent_lineage": parent,
        "candidate_count": 3,
        "compute_budget": {"value": 120, "unit": "FIT_SECONDS"},
        "candidates": candidates,
    }


def _request() -> dict:
    return {
        "confirmed": True,
        "owner_confirmation": r02.OWNER_R02_CONFIRMATION,
        "plan": _plan_request(),
    }


def _ready() -> dict:
    return {
        "status": "READY_FOR_OWNER_AUTHORIZATION",
        "research_id": "RSRCH-R02-AUTH",
        "r01_output_manifest_sha256": "f" * 64,
    }


def test_authorization_requires_exact_owner_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(r02, "r02_preflight", lambda **_kwargs: _ready())
    request = _request()
    request["confirmed"] = False
    with pytest.raises(RuntimeError, match="R02_OWNER_CONFIRMATION_REQUIRED"):
        r02.authorize_r02_discovery(request, path=Path("unused.db"))

    request = _request()
    request["owner_confirmation"] = "WRONG"
    with pytest.raises(RuntimeError, match="R02_OWNER_AUTHORIZATION_INVALID"):
        r02.authorize_r02_discovery(request, path=Path("unused.db"))


def test_stale_research_or_r01_output_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        r02,
        "r02_preflight",
        lambda **_kwargs: {
            **_ready(),
            "research_id": "OTHER",
        },
    )
    with pytest.raises(RuntimeError, match="R02_RESEARCH_ID_STALE"):
        r02.authorize_r02_discovery(_request(), path=Path("unused.db"))

    monkeypatch.setattr(
        r02,
        "r02_preflight",
        lambda **_kwargs: {
            **_ready(),
            "r01_output_manifest_sha256": "e" * 64,
        },
    )
    with pytest.raises(RuntimeError, match="R02_R01_OUTPUT_AUTHORITY_STALE"):
        r02.authorize_r02_discovery(_request(), path=Path("unused.db"))


def test_authorization_freezes_plan_without_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(r02, "r02_preflight", lambda **_kwargs: _ready())
    captured: dict = {}

    def fake_create(record: dict, **_kwargs) -> dict:
        captured["authorization"] = deepcopy(record)
        return deepcopy(record)

    def fake_freeze(*, authorization: dict, plan: dict, **_kwargs) -> dict:
        captured["plan"] = deepcopy(plan)
        return {
            "block_id": "RDISC-AUTH",
            "authorization_id": authorization["authorization_id"],
            "state": "FROZEN_WAITING_EXECUTION",
            "research_id": plan["research_id"],
            "plan_id": plan["plan_id"],
            "plan_sha256": plan["plan_sha256"],
            "candidate_count": plan["candidate_count"],
            "compute_budget": plan["compute_budget"],
            "candidates": [],
        }

    def fake_atomic(*, authorization_record: dict, plan: dict, **_kwargs):
        authorization = fake_create(authorization_record)
        block = fake_freeze(authorization=authorization, plan=plan)
        return authorization, block

    monkeypatch.setattr(r02, "authorize_and_freeze_r02_discovery", fake_atomic)

    result = r02.authorize_r02_discovery(_request(), path=Path("unused.db"))

    assert result["status"] == "FROZEN_WAITING_EXECUTION"
    assert result["idempotent"] is False
    assert result["execution_available"] is False
    assert result["scientific_result"] is False
    assert result["model_training"] == 0
    assert result["onnx"] == 0
    assert captured["authorization"]["payload"]["cheap_screen_qualification_authority"] is False
    assert captured["authorization"]["payload"]["automatic_second_discovery_block"] is False
    assert captured["authorization"]["payload"]["execution_available"] is False
    assert captured["plan"]["candidate_count"] == 3


def test_exact_replay_is_idempotent_but_different_plan_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = r02.build_discovery_plan(_plan_request())
    frozen = {
        "block_id": "RDISC-AUTH",
        "authorization_id": "RAUTH-R02-AUTH",
        "state": "FROZEN_WAITING_EXECUTION",
        "research_id": plan["research_id"],
        "r01_output_manifest_sha256": plan["r01_output_manifest_sha256"],
        "plan_id": plan["plan_id"],
        "plan_sha256": plan["plan_sha256"],
        "candidate_count": plan["candidate_count"],
        "compute_budget": plan["compute_budget"],
        "candidates": [],
    }
    monkeypatch.setattr(
        r02,
        "r02_preflight",
        lambda **_kwargs: {
            "status": "FROZEN_WAITING_EXECUTION",
            "research_id": plan["research_id"],
            "r01_output_manifest_sha256": plan["r01_output_manifest_sha256"],
        },
    )
    monkeypatch.setattr(
        r02,
        "get_r02_discovery_block",
        lambda research_id, **_kwargs: deepcopy(frozen),
    )
    monkeypatch.setattr(
        r02,
        "get_r02_authorization",
        lambda authorization_id, **_kwargs: {
            "authorization_id": authorization_id,
            "research_id": plan["research_id"],
        },
    )

    result = r02.authorize_r02_discovery(_request(), path=Path("unused.db"))
    assert result["idempotent"] is True

    changed = _request()
    changed["plan"]["compute_budget"] = {"value": 121, "unit": "FIT_SECONDS"}
    with pytest.raises(RuntimeError, match="R02_DISCOVERY_BLOCK_ALREADY_FROZEN"):
        r02.authorize_r02_discovery(changed, path=Path("unused.db"))


def test_authorize_api_surface_exists_but_start_surface_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"status": "FROZEN_WAITING_EXECUTION"}
    monkeypatch.setattr(
        research_api,
        "authorize_r02_discovery",
        lambda payload: deepcopy(expected),
    )
    model = research_api.R02AuthorizeRequest(**_request())
    assert research_api.authorize_research_r02(model) == expected
    assert not hasattr(research_api, "start_research_r02")
