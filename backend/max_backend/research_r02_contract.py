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

CHEAP_SCREEN_POLICY = {
    "policy_id": "R02_DISCOVERY_CHRONOLOGICAL_PURGED_80_20_V1",
    "purpose": "DIAGNOSTIC_SCREEN_ONLY",
    "reproducibility_scope": "SAME_DATA_SPEC_SEED_SOFTWARE_BUILD_AND_DEVICE",
    "split": {
        "kind": "CHRONOLOGICAL_HOLDOUT",
        "train_fraction": 0.8,
        "shuffle": False,
        "train_target_purge": "R01.minimum_legal_purge_main_bars",
    },
    "minimum_training_rows": 24,
    "minimum_validation_rows": 12,
    "required_class_ids": [0, 1, 2],
    "metrics": [
        "balanced_accuracy",
        "macro_f1",
        "log_loss",
        "multiclass_brier",
    ],
    "screen_thresholds": {
        "balanced_accuracy_min": 0.4,
        "log_loss_max": 1.5,
    },
    "executor_policy": {
        "threads": 1,
        "retry_count": 0,
        "accelerators": {
            "lightgbm": ["CPU", "GPU_OPENCL"],
            "xgboost": ["CPU", "GPU_CUDA"],
            "random_forest": ["CPU"],
        },
        "sample_weight_policy": "BALANCED_FROM_TRAIN_LABELS_ONLY",
        "family_parameters": {
            "lightgbm": {
                "objective": "multiclass",
                "num_class": 3,
                "subsample": 1.0,
                "subsample_freq": 0,
                "colsample_bytree": 1.0,
                "subsample_for_bin": 200000,
                "min_child_samples": 20,
                "min_child_weight": 0.001,
                "reg_alpha": 0.0,
                "reg_lambda": 0.0,
                "verbosity": -1,
            },
            "xgboost": {
                "objective": "multi:softprob",
                "num_class": 3,
                "tree_method": "hist",
                "eval_metric": "mlogloss",
                "min_child_weight": 1.0,
                "reg_alpha": 0.0,
                "reg_lambda": 1.0,
                "gamma": 0.0,
                "max_delta_step": 0.0,
                "sampling_method": "uniform",
                "verbosity": 0,
            },
            "random_forest": {
                "criterion": "gini",
                "max_features": "sqrt",
                "bootstrap": True,
                "class_weight": None,
                "min_samples_split": 2,
                "min_weight_fraction_leaf": 0.0,
                "max_leaf_nodes": None,
                "min_impurity_decrease": 0.0,
                "ccp_alpha": 0.0,
                "oob_score": False,
                "warm_start": False,
            },
        },
    },
    "qualification_authority": False,
    "qualified_pool_admission_authority": "R03_FULL_WFA_ONLY",
}

_TOPOLOGY_FIELDS = {
    "lightgbm": {
        "n_estimators",
        "max_depth",
        "num_leaves",
        "learning_rate",
    },
    "xgboost": {
        "n_estimators",
        "max_depth",
        "learning_rate",
        "subsample",
        "colsample_bytree",
    },
    "random_forest": {
        "n_estimators",
        "max_depth",
        "min_samples_leaf",
    },
}


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
        "cheap_screen_policy": deepcopy(CHEAP_SCREEN_POLICY),
        "executor_source_implemented": True,
        "synthetic_model_fitting_authorized": True,
        "real_runtime_execution_authorized": False,
        "real_scientific_execution_proven": False,
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
    try:
        finite_amount = float(amount)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("R02_COMPUTE_BUDGET_VALUE_INVALID") from exc
    if (
        isinstance(amount, bool)
        or not isinstance(amount, (int, float))
        or not math.isfinite(finite_amount)
        or amount <= 0
    ):
        raise ValueError("R02_COMPUTE_BUDGET_VALUE_INVALID")
    unit = _require_nonempty_text(value["unit"], "R02_COMPUTE_BUDGET_UNIT_REQUIRED")
    if unit != "FIT_SECONDS":
        raise ValueError("R02_COMPUTE_BUDGET_UNIT_UNSUPPORTED")
    return {
        "value": amount,
        "unit": unit,
        "execution_semantics": "EXECUTOR_BOUNDED_FIT_SECONDS_V1",
    }


def _canonical_topology_spec(family: str, value: Any) -> dict[str, Any]:
    fields = _TOPOLOGY_FIELDS[family]
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("R02_CANDIDATE_TOPOLOGY_FIELDS_INVALID")
    result = deepcopy(value)
    for field in ("n_estimators", "max_depth"):
        item = result[field]
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise ValueError("R02_CANDIDATE_TOPOLOGY_VALUE_INVALID:" + field)
    if result["n_estimators"] > 256 or result["max_depth"] > 32:
        raise ValueError("R02_CANDIDATE_TOPOLOGY_VALUE_OUT_OF_RANGE")
    if family == "lightgbm":
        leaves = result["num_leaves"]
        if isinstance(leaves, bool) or not isinstance(leaves, int) or not 2 <= leaves <= 256:
            raise ValueError("R02_CANDIDATE_TOPOLOGY_VALUE_INVALID:num_leaves")
        if leaves > 2 ** min(result["max_depth"], 8):
            raise ValueError("R02_CANDIDATE_TOPOLOGY_VALUE_OUT_OF_RANGE")
    if family == "random_forest":
        leaf = result["min_samples_leaf"]
        if isinstance(leaf, bool) or not isinstance(leaf, int) or not 1 <= leaf <= 128:
            raise ValueError("R02_CANDIDATE_TOPOLOGY_VALUE_INVALID:min_samples_leaf")
    if family in {"lightgbm", "xgboost"}:
        learning_rate = result["learning_rate"]
        try:
            rate = float(learning_rate)
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError("R02_CANDIDATE_TOPOLOGY_VALUE_INVALID:learning_rate") from exc
        if (
            isinstance(learning_rate, bool)
            or not isinstance(learning_rate, (int, float))
            or not math.isfinite(rate)
            or not 0 < rate <= 1
        ):
            raise ValueError("R02_CANDIDATE_TOPOLOGY_VALUE_INVALID:learning_rate")
        result["learning_rate"] = rate
    if family == "xgboost":
        for field in ("subsample", "colsample_bytree"):
            item = result[field]
            try:
                amount = float(item)
            except (OverflowError, TypeError, ValueError) as exc:
                raise ValueError("R02_CANDIDATE_TOPOLOGY_VALUE_INVALID:" + field) from exc
            if (
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(amount)
                or not 0 < amount <= 1
            ):
                raise ValueError("R02_CANDIDATE_TOPOLOGY_VALUE_INVALID:" + field)
            result[field] = amount
    return result


def _canonical_preprocessing(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"scaling"}:
        raise ValueError("R02_CANDIDATE_PREPROCESSING_INVALID")
    scaling = value.get("scaling")
    if not isinstance(scaling, str) or scaling not in ("NONE", "STANDARD"):
        raise ValueError("R02_CANDIDATE_PREPROCESSING_INVALID")
    return {"scaling": scaling}


def _canonical_training_configuration(
    value: Any,
    *,
    family: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "objective",
        "class_weighting",
        "accelerator",
        "device_id",
        "platform_id",
    }:
        raise ValueError("R02_CANDIDATE_TRAINING_CONFIGURATION_INVALID")
    if value.get("objective") != "MULTICLASS" or value.get("class_weighting") != "BALANCED":
        raise ValueError("R02_CANDIDATE_TRAINING_CONFIGURATION_INVALID")

    accelerator = value.get("accelerator")
    allowed = {
        "lightgbm": {"CPU", "GPU_OPENCL"},
        "xgboost": {"CPU", "GPU_CUDA"},
        "random_forest": {"CPU"},
    }.get(family)
    if not isinstance(accelerator, str) or allowed is None or accelerator not in allowed:
        raise ValueError("R02_CANDIDATE_TRAINING_CONFIGURATION_INVALID")

    device_id = value.get("device_id")
    platform_id = value.get("platform_id")
    if accelerator == "CPU":
        if device_id is not None or platform_id is not None:
            raise ValueError("R02_CANDIDATE_TRAINING_CONFIGURATION_INVALID")
    else:
        if (
            isinstance(device_id, bool)
            or not isinstance(device_id, int)
            or device_id < 0
        ):
            raise ValueError("R02_CANDIDATE_TRAINING_CONFIGURATION_INVALID")
        if accelerator == "GPU_OPENCL":
            if (
                isinstance(platform_id, bool)
                or not isinstance(platform_id, int)
                or platform_id < 0
            ):
                raise ValueError("R02_CANDIDATE_TRAINING_CONFIGURATION_INVALID")
        elif platform_id is not None:
            raise ValueError("R02_CANDIDATE_TRAINING_CONFIGURATION_INVALID")

    return {
        "objective": "MULTICLASS",
        "class_weighting": "BALANCED",
        "accelerator": accelerator,
        "device_id": device_id,
        "platform_id": platform_id,
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
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 0xFFFFFFFF:
        raise ValueError("R02_CANDIDATE_SEED_INVALID")
    spec["topology_spec"] = _canonical_topology_spec(family, spec["topology_spec"])
    spec["preprocessing"] = _canonical_preprocessing(spec["preprocessing"])
    spec["training_configuration"] = _canonical_training_configuration(
        spec["training_configuration"],
        family=family,
    )

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
    if not isinstance(parent_lineage, dict):
        raise ValueError("R02_PARENT_LINEAGE_REQUIRED")
    required_parent_lineage = {
        "research_parent_id",
        "parent_strategy_id",
        "dataset_id",
        "r01_output_manifest_sha256",
    }
    if set(parent_lineage) != required_parent_lineage:
        raise ValueError("R02_PARENT_LINEAGE_FIELDS_INVALID")
    for field in ("research_parent_id", "parent_strategy_id", "dataset_id"):
        _require_nonempty_text(
            parent_lineage[field],
            "R02_PARENT_LINEAGE_VALUE_REQUIRED:" + field,
        )
    lineage_output_sha = _require_sha256(
        parent_lineage["r01_output_manifest_sha256"],
        "R02_PARENT_LINEAGE_OUTPUT_SHA_INVALID",
    )
    if lineage_output_sha != r01_output_sha:
        raise ValueError("R02_PARENT_LINEAGE_OUTPUT_SHA_MISMATCH")
    parent_lineage = deepcopy(parent_lineage)
    parent_lineage["r01_output_manifest_sha256"] = lineage_output_sha

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
        "cheap_screen_policy": deepcopy(CHEAP_SCREEN_POLICY),
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
