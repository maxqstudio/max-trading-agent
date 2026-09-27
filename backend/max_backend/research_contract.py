from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .config import STRATEGY_CONTRACT
from .mtf_geometry import RESOLVER_VERSION

RESEARCH_SCHEMA = "MAX_RESEARCH_AUTHORITY_R00_V1"
PARENT_SCHEMA = "MAX_RESEARCH_PARENT_MANIFEST_V1"
AUTHORIZATION_SCHEMA = "MAX_RESEARCH_OWNER_AUTHORIZATION_V1"
HARDWARE_SCHEMA = "MAX_RESEARCH_HARDWARE_SNAPSHOT_V1"
LABEL_AUTHORITY_SCHEMA = "MAX_RESEARCH_LABEL_AUTHORITY_R00_V1"
CANDIDATE_ID_SCHEMA = "MAX_RESEARCH_CANDIDATE_ID_V1"
ARTIFACT_LINEAGE_SCHEMA = "MAX_RESEARCH_ARTIFACT_LINEAGE_V1"
SAMPLE_POLICY_SCHEMA = "MAX_RESEARCH_SAMPLE_POLICY_R00_V1"
RESEARCH_MEMORY_SCHEMA = "MAX_RESEARCH_MEMORY_EVENT_V1"
R00_INPUT_SCHEMA = "MAX_RESEARCH_R00_INPUT_MANIFEST_V1"
R00_OUTPUT_SCHEMA = "MAX_RESEARCH_R00_OUTPUT_MANIFEST_V1"

FEATURE_CONTRACT = "CP32_TRUE_MTF_V1"
OLD_MAX_SOURCE_REPO = "maxqstudio/max_research_agent"
OLD_MAX_SOURCE_SHA = "3e969efcdeb4ca6a2ae63acbd80592e378d2a446"
R00_ACCEPTED_MAIN_PARENT_SHA = "e2256491249123a46b0aa45d6d11e5380c7312a5"
R01_ACCEPTED_BASE_MAIN_SHA = "3e224c8b6a493fadd1dabeba6b76bd9eb1f6bb7a"
R01_SCHEMA = "MAX_RESEARCH_DATA_FOUNDATION_R01_V1"
R01_AUTHORIZATION_SCHEMA = "MAX_RESEARCH_OWNER_AUTHORIZATION_R01_V1"
R01_INPUT_SCHEMA = "MAX_RESEARCH_R01_INPUT_MANIFEST_V1"
R01_OUTPUT_SCHEMA = "MAX_RESEARCH_R01_OUTPUT_MANIFEST_V1"
OWNER_R01_CONFIRMATION = "OWNER_EXPLICIT_R01_START"

RESEARCH_GATES = tuple(f"R{i:02d}" for i in range(11))
R00_TERMINAL_STATES = (
    "PASS_WAITING_OWNER",
    "FAIL_WAITING_OWNER",
    "ERROR_WAITING_OWNER",
)
RESEARCH_STATES = ("STARTING",) + R00_TERMINAL_STATES

OWNER_R00_CONFIRMATION = "OWNER_EXPLICIT_R00_START"
OWNER_CUMULATIVE_E2E_AUTHORITY = "OWNER_EXPLICIT_STRATEGY_E2E_SATISFIED"
OWNER_AUTHORITY = "OWNER"
SYSTEM_QUALIFICATION_AUTHORITY = "DETERMINISTIC_SYSTEM"
SCIENTIST_AUTHORITY = "ADVISORY_ONLY"

CURRENT_PARENT_CONTRACT = {
    "strategy_contract": STRATEGY_CONTRACT,
    "feature_contract": FEATURE_CONTRACT,
    "resolver_version": RESOLVER_VERSION,
}

FUTURE_RESEARCH_ARTIFACT_TYPES = (
    "RESEARCH_DATASET",
    "FEATURE_MANIFEST",
    "LABEL_MANIFEST",
    "DATA_QUALITY_REPORT",
    "CANDIDATE_SPEC",
    "CHEAP_SCREEN_RUN",
    "FULL_WFA_RUN",
    "MODEL_CHECKPOINT",
    "CPCV_REPORT",
    "TOURNAMENT_REPORT",
    "MONTE_CARLO_REPORT",
    "LOCKED_OOS_REPORT",
    "FORWARD_REPORT",
    "SCIENTIST_ANALYSIS",
    "ONNX_MODEL",
    "ONNX_POLICY",
    "RESEARCH_MODEL_PACKAGE",
    "STRATEGY_CHALLENGER_PACKAGE",
)

R00_ARTIFACT_TYPES = (
    "RESEARCH_PARENT_MANIFEST",
    "RESEARCH_HARDWARE_SNAPSHOT",
    "RESEARCH_AUTHORITY_MANIFEST",
    "RESEARCH_AUTHORITY_STATE",
)

PROTECTED_MEMORY_STAGES = {
    "LOCKED_OOS",
    "FRESH_FORWARD",
    "FORWARD",
    "FRESH",
    "SHADOW",
    "PROMOTION",
    "CHALLENGER",
    "CHAMPION",
}
VALIDATION_MEMORY_STAGES = {"CPCV", "TOURNAMENT", "MONTE_CARLO"}
ADAPTIVE_MEMORY_STAGES = {"DISCOVERY", "CHEAP_SCREEN", "FULL_WFA", "OOF_POLICY_DISCOVERY", "POOL"}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def label_design_authority() -> dict[str, Any]:
    return {
        "schema": LABEL_AUTHORITY_SCHEMA,
        "status": "UNRESOLVED_R01_REQUIRED",
        "authority": "R01_EXPLICIT_LABEL_CONTRACT_REQUIRED",
        "feature_future_information_allowed": False,
        "target_future_information_allowed": True,
        "purge_embargo_authority": "DERIVED_FROM_DEPENDENCY_HORIZON",
        "unresolved": [
            "TARGET_DEFINITION",
            "LABEL_HORIZON",
            "DEPENDENCY_HORIZON",
            "AMBIGUOUS_TARGET_POLICY",
            "CLASS_BALANCE_POLICY",
            "PURGE_BARS",
            "EMBARGO_BARS",
        ],
    }


def research_sample_policy(
    h1_minimum_trades_per_month: int | None = None,
) -> dict[str, Any]:
    frozen = (
        isinstance(h1_minimum_trades_per_month, int)
        and not isinstance(h1_minimum_trades_per_month, bool)
        and h1_minimum_trades_per_month > 0
    )
    return {
        "schema": SAMPLE_POLICY_SCHEMA,
        "authority": "RESEARCH_ONLY_NOT_STRATEGY_OPTIMIZER",
        "strategy_optimizer_policy_inherited": False,
        "h1_minimum_sample_trade_policy": {
            "status": (
                "FROZEN_OWNER_AUTHORITY"
                if frozen
                else "OWNER_DECISION_REQUIRED"
            ),
            "value": int(h1_minimum_trades_per_month) if frozen else None,
            "unit": "TRADES_PER_H1_MONTH",
            "evidence_status": (
                "OWNER_AUTHORIZATION_BOUND"
                if frozen
                else "NOT_YET_FROZEN"
            ),
            "note": (
                "R00 never inherits the Strategy Optimizer H1 sample policy. "
                "Owner must provide the Research H1 minimum explicitly."
            ),
        },
        "kpi_policy": {
            "status": "FROZEN_AUTHORITY_CONTRACT",
            "strategy_optimizer_kpi_reuse": False,
            "hard_gate_dimensions": [
                "SAMPLE_SUFFICIENCY",
                "PROFIT_FACTOR",
                "RECOVERY_FACTOR",
                "EXPECTANCY_R",
                "DRAWDOWN_R",
                "TEMPORAL_STABILITY",
            ],
            "numeric_threshold_authority": (
                "GATE_SCOPED_OWNER_FREEZE_BEFORE_FIRST_SCIENTIFIC_QUALIFICATION"
            ),
            "ranking_cannot_override_hard_fail": True,
            "note": (
                "R00 freezes Research KPI governance without guessing numeric "
                "thresholds before the gate that consumes them."
            ),
        },
    }


def candidate_identity_contract() -> dict[str, Any]:
    return {
        "schema": CANDIDATE_ID_SCHEMA,
        "deterministic": True,
        "immutable": True,
        "required_components": [
            "research_id",
            "model_family",
            "topology_spec",
            "feature_contract",
            "label_contract",
            "seed",
            "preprocessing",
            "training_configuration",
            "parent_lineage",
        ],
        "id_format": "RCAND-<sha256-prefix-24>",
        "actual_candidates_created_in_r00": False,
    }


def candidate_id(spec: dict[str, Any]) -> str:
    required = candidate_identity_contract()["required_components"]
    missing = [key for key in required if key not in spec]
    if missing:
        raise ValueError("RESEARCH_CANDIDATE_IDENTITY_MISSING:" + ",".join(missing))
    body = {key: deepcopy(spec[key]) for key in required}
    return "RCAND-" + stable_hash({"schema": CANDIDATE_ID_SCHEMA, "body": body})[:24]


def artifact_lineage_contract() -> dict[str, Any]:
    return {
        "schema": ARTIFACT_LINEAGE_SCHEMA,
        "authority": "M08_ARTIFACT_CONTROL_PLANE",
        "r00_types": list(R00_ARTIFACT_TYPES),
        "future_types": list(FUTURE_RESEARCH_ARTIFACT_TYPES),
        "required_lineage": [
            "PARENT_STRATEGY",
            "DATASET",
            "FEATURE_LABEL_CONTRACT",
            "CANDIDATE",
            "FOLD_SEED_TRAINING",
            "MODEL",
            "VALIDATION",
            "CPCV",
            "TOURNAMENT",
            "MONTE_CARLO",
            "LOCKED_OOS",
            "FORWARD",
            "FINAL_MODEL_PACKAGE",
            "STRATEGY_CHALLENGER",
        ],
        "orphan_authority_allowed": False,
    }


def capacity_authority_contract() -> dict[str, Any]:
    return {
        "schema": "MAX_RESEARCH_CAPACITY_AUTHORITY_R00_V1",
        "equation": "EXECUTABLE_CAPACITY=MIN(LEGAL,RESOURCE,SCIENTIFIC)",
        "legal": "IMPLEMENTED_ARCHITECTURE_RUNTIME_ONNX_CONSTRAINTS",
        "resource": "FROZEN_HARDWARE_AND_RUNTIME_BUDGET",
        "scientific": "EFFECTIVE_TRAINING_INFORMATION_AND_DEPENDENCY_LOSS",
        "global_parameter_hard_ceiling": None,
        "arbitrary_global_parameter_ceiling_allowed": False,
        "r00_training_authority": False,
    }


def scientist_research_contract() -> dict[str, Any]:
    return {
        "schema": "MAX_RESEARCH_SCIENTIST_R00_V1",
        "authority": SCIENTIST_AUTHORITY,
        "may": [
            "ANALYZE_AUTHORIZED_EVIDENCE",
            "COMPARE_EXPERIMENTS",
            "ANALYZE_CAPACITY",
            "EXPLAIN_FAILURE_TOPOLOGY",
            "PROPOSE_HYPOTHESES",
        ],
        "may_not": [
            "DECLARE_HARD_PASS",
            "OPEN_NEXT_GATE",
            "SELECT_FOR_OWNER",
            "MUTATE_CHAMPION",
            "CHANGE_RISK",
            "ACCESS_LOCKED_OOS_EARLY",
            "MUTATE_DATASET",
            "MUTATE_RESEARCH_STATE",
        ],
        "python_sandbox_foundation": {
            "implemented_in_r00": False,
            "future_allowed": ["numpy", "pandas", "scipy", "sklearn_analytics"],
            "forbidden": [
                "shell",
                "network",
                "arbitrary_filesystem",
                "package_install",
                "git",
                "mt5_execution",
                "champion_mutation",
                "research_state_mutation",
            ],
            "output_authority": "ANALYTICAL_EVIDENCE_ONLY",
        },
    }


def research_memory_contract() -> dict[str, Any]:
    return {
        "schema": "MAX_RESEARCH_MEMORY_CONTRACT_R00_V1",
        "append_only": True,
        "retains": [
            "PASS",
            "FAIL",
            "NEAR_MISS",
            "LEAKAGE_REJECTION",
            "CAPACITY_REJECTION",
            "INSTABILITY",
            "NAN_INF",
            "OVERFIT_SIGNATURE",
            "FAILED_HYPOTHESIS",
            "SUCCESSFUL_HYPOTHESIS",
            "PREDICTED_EFFECT_VS_ACTUAL_RESULT",
        ],
        "protected_evaluation_stages": sorted(PROTECTED_MEMORY_STAGES),
        "protected_feedback_adaptive_eligible": False,
        "adaptive_self_training": False,
        "authority": "SCIENTIFIC_MEMORY_NOT_GATE_PASS_AUTHORITY",
    }


def memory_learning_zone(stage: str) -> str:
    token = str(stage or "").strip().upper()
    if token in {"R00", "R01"}:
        return "FOUNDATION"
    if token in PROTECTED_MEMORY_STAGES:
        return "PROTECTED"
    if token in VALIDATION_MEMORY_STAGES:
        return "VALIDATION"
    if token in ADAPTIVE_MEMORY_STAGES:
        return "ADAPTIVE"
    return "UNKNOWN"


def memory_adaptive_eligible(stage: str) -> bool:
    return memory_learning_zone(stage) in {"ADAPTIVE", "VALIDATION"}


def unresolved_authority_items(
    *,
    h1_sample_policy_frozen: bool = False,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if not h1_sample_policy_frozen:
        items.append({
            "item": "RESEARCH_H1_MINIMUM_SAMPLE_POLICY",
            "status": "OWNER_DECISION_REQUIRED",
        })
    items.extend([
        {
            "item": "RESEARCH_KPI_NUMERIC_THRESHOLDS",
            "status": "GATE_SPECIFIC_OWNER_FREEZE_REQUIRED",
        },
        {
            "item": "LABEL_CONTRACT",
            "status": "R01_REQUIRED",
        },
    ])
    return items
