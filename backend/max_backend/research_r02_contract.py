from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from .research_contract import (
    FEATURE_CONTRACT,
    candidate_id,
    candidate_identity_contract,
    stable_hash,
)

R02_SCHEMA = "MAX_RESEARCH_DISCOVERY_R02_V1"
R02_PLAN_SCHEMA = "MAX_RESEARCH_DISCOVERY_PLAN_R02_V1"

BASELINE_MODEL_FAMILIES = ("lightgbm", "xgboost")
CONTROL_MODEL_FAMILIES = ("random_forest",)
SOURCE_FOUNDATION_MODEL_FAMILIES = BASELINE_MODEL_FAMILIES + CONTROL_MODEL_FAMILIES

TEMPORAL_MODEL_FAMILIES = (
    "gru",
    "patchtst",
    "causal_transformer_encoder",
    "lstm",
    "tcn",
    "itransformer",
    "tft",
    "transformer_moe",
)


def r02_discovery_contract() -> dict[str, Any]:
    return {
        "schema": R02_SCHEMA,
        "stage": "MODEL_DISCOVERY",
        "owner_start_required": True,
        "bounded_single_block": True,
        "automatic_second_discovery_block": False,
        "candidate_count_must_be_frozen": True,
        "compute_budget_must_be_frozen": True,
        "cheap_screen_qualification_authority": False,
        "qualified_pool_admission_authority": "R03_FULL_WFA_ONLY",
        "baseline_model_families": list(BASELINE_MODEL_FAMILIES),
        "optional_control_model_families": list(CONTROL_MODEL_FAMILIES),
        "temporal_model_families_source_foundation_enabled": False,
        "terminal_behavior": "STOP_WAITING_OWNER",
        "source_foundation_only": True,
        "runtime_execution_implemented": False,
        "model_training_authorized": False,
        "onnx_authorized": False,
        "research_challenger_authorized": False,
        "champion_mutation_authorized": False,
    }


def _require_nonempty_text(value: Any, code: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError(code)
    return token


def _require_sha256(value: Any, code: str) -> str:
    token = _require_nonempty_text(value, code)
    if len(token) != 64 or any(ch not in "0123456789abcdef" for ch in token.lower()):
        raise ValueError(code)
    return token.lower()


def _canonical_compute_budget(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("R02_COMPUTE_BUDGET_OBJECT_REQUIRED")
    if set(value) != {"value", "unit"}:
        raise ValueError("R02_COMPUTE_BUDGET_FIELDS_INVALID")
    amount = value["value"]
    if (
        isinstance(amount, bool)
        or not isinstance(amount, (int, float))
        or not math.isfinite(float(amount))
        or amount <= 0
    ):
        raise ValueError("R02_COMPUTE_BUDGET_VALUE_INVALID")
    unit = _require_nonempty_text(value["unit"], "R02_COMPUTE_BUDGET_UNIT_REQUIRED")
    return {
        "value": amount,
        "unit": unit,
        "execution_semantics": "FROZEN_ONLY_NOT_EXECUTED",
    }


def _canonical_candidate_spec(
    raw: Any,
    *,
    research_id: str,
    feature_contract: str,
    label_contract: str,
    parent_lineage: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("R02_CANDIDATE_SPEC_OBJECT_REQUIRED")

    required = set(candidate_identity_contract()["required_components"])
    if set(raw) != required:
        missing = sorted(required - set(raw))
        extra = sorted(set(raw) - required)
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra:
            details.append("extra=" + ",".join(extra))
        raise ValueError("R02_CANDIDATE_SPEC_FIELDS_INVALID:" + ";".join(details))

    spec = deepcopy(raw)
    family = _require_nonempty_text(
        spec["model_family"],
        "R02_MODEL_FAMILY_REQUIRED",
    ).lower()
    if family in TEMPORAL_MODEL_FAMILIES:
        raise ValueError("R02_TEMPORAL_MODEL_FAMILY_NOT_YET_AUTHORIZED")
    if family not in SOURCE_FOUNDATION_MODEL_FAMILIES:
        raise ValueError("R02_MODEL_FAMILY_UNSUPPORTED")
    if str(spec["model_family"]) != family:
        raise ValueError("R02_MODEL_FAMILY_NOT_CANONICAL")

    if str(spec["research_id"]) != research_id:
        raise ValueError("R02_CANDIDATE_RESEARCH_ID_MISMATCH")
    if str(spec["feature_contract"]) != feature_contract:
        raise ValueError("R02_CANDIDATE_FEATURE_CONTRACT_MISMATCH")
    if str(spec["label_contract"]) != label_contract:
        raise ValueError("R02_CANDIDATE_LABEL_CONTRACT_MISMATCH")
    if spec["parent_lineage"] != parent_lineage:
        raise ValueError("R02_CANDIDATE_PARENT_LINEAGE_MISMATCH")

    seed = spec["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("R02_CANDIDATE_SEED_INVALID")
    if not isinstance(spec["topology_spec"], dict) or not spec["topology_spec"]:
        raise ValueError("R02_CANDIDATE_TOPOLOGY_REQUIRED")
    if not isinstance(spec["preprocessing"], dict):
        raise ValueError("R02_CANDIDATE_PREPROCESSING_INVALID")
    if (
        not isinstance(spec["training_configuration"], dict)
        or not spec["training_configuration"]
    ):
        raise ValueError("R02_CANDIDATE_TRAINING_CONFIGURATION_REQUIRED")

    spec["model_family"] = family
    spec["candidate_id"] = candidate_id(spec)
    return spec


def build_discovery_plan(request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ValueError("R02_PLAN_REQUEST_OBJECT_REQUIRED")

    required_fields = {
        "research_id",
        "r01_output_manifest_sha256",
        "feature_contract",
        "label_contract",
        "parent_lineage",
        "candidate_count",
        "compute_budget",
        "candidates",
    }
    if set(request) != required_fields:
        missing = sorted(required_fields - set(request))
        extra = sorted(set(request) - required_fields)
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra:
            details.append("extra=" + ",".join(extra))
        raise ValueError("R02_PLAN_FIELDS_INVALID:" + ";".join(details))

    research_id = _require_nonempty_text(
        request["research_id"],
        "R02_RESEARCH_ID_REQUIRED",
    )
    r01_output_sha = _require_sha256(
        request["r01_output_manifest_sha256"],
        "R02_R01_OUTPUT_MANIFEST_SHA_INVALID",
    )
    feature_contract = _require_nonempty_text(
        request["feature_contract"],
        "R02_FEATURE_CONTRACT_REQUIRED",
    )
    if feature_contract != FEATURE_CONTRACT:
        raise ValueError("R02_FEATURE_CONTRACT_UNSUPPORTED")
    label_contract = _require_nonempty_text(
        request["label_contract"],
        "R02_LABEL_CONTRACT_REQUIRED",
    )
    parent_lineage = request["parent_lineage"]
    if not isinstance(parent_lineage, dict) or not parent_lineage:
        raise ValueError("R02_PARENT_LINEAGE_REQUIRED")

    candidate_count = request["candidate_count"]
    if (
        isinstance(candidate_count, bool)
        or not isinstance(candidate_count, int)
        or candidate_count <= 0
    ):
        raise ValueError("R02_CANDIDATE_COUNT_INVALID")

    candidates = request["candidates"]
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("R02_CANDIDATES_REQUIRED")
    if len(candidates) != candidate_count:
        raise ValueError("R02_CANDIDATE_COUNT_MISMATCH")

    canonical = [
        _canonical_candidate_spec(
            candidate,
            research_id=research_id,
            feature_contract=feature_contract,
            label_contract=label_contract,
            parent_lineage=parent_lineage,
        )
        for candidate in candidates
    ]
    ids = [str(candidate["candidate_id"]) for candidate in canonical]
    if len(set(ids)) != len(ids):
        raise ValueError("R02_DUPLICATE_CANDIDATE_ID")

    canonical.sort(key=lambda item: str(item["candidate_id"]))
    budget = _canonical_compute_budget(request["compute_budget"])
    body = {
        "schema": R02_PLAN_SCHEMA,
        "research_id": research_id,
        "r01_output_manifest_sha256": r01_output_sha,
        "feature_contract": feature_contract,
        "label_contract": label_contract,
        "parent_lineage": deepcopy(parent_lineage),
        "candidate_count": candidate_count,
        "compute_budget": budget,
        "candidates": canonical,
        "candidate_ids": [str(item["candidate_id"]) for item in canonical],
        "cheap_screen_qualification_authority": False,
        "qualified_pool_admission_authority": "R03_FULL_WFA_ONLY",
        "automatic_second_discovery_block": False,
        "model_training_performed": False,
        "runtime_execution_authorized": False,
    }
    result = deepcopy(body)
    result["plan_id"] = "RPLAN-" + stable_hash(body)[:24]
    result["plan_sha256"] = stable_hash(result)
    return result
