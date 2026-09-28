from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from .research_contract import stable_hash

R02_OUTCOME_SCHEMA = "MAX_RESEARCH_CHEAP_SCREEN_OUTCOME_R02_V1"
R02_TERMINAL_SCHEMA = "MAX_RESEARCH_CHEAP_SCREEN_TERMINAL_R02_V1"

OUTCOME_STATUSES = ("SCREEN_PASS", "SCREEN_FAIL", "EXECUTION_ERROR")


def _require_text(value: Any, code: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError(code)
    return token


def _canonical_json(value: Any, *, path: str) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return deepcopy(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("R02_OUTCOME_NONFINITE_VALUE:" + path)
        return value
    if isinstance(value, list):
        return [
            _canonical_json(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("R02_OUTCOME_JSON_KEY_INVALID:" + path)
            result[key] = _canonical_json(item, path=f"{path}.{key}")
        return result
    raise ValueError("R02_OUTCOME_JSON_VALUE_INVALID:" + path)


def _canonical_compute_consumed(value: Any, *, budget_unit: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"value", "unit"}:
        raise ValueError("R02_OUTCOME_COMPUTE_FIELDS_INVALID")
    amount = value["value"]
    try:
        finite_amount = float(amount)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("R02_OUTCOME_COMPUTE_VALUE_INVALID") from exc
    if (
        isinstance(amount, bool)
        or not isinstance(amount, (int, float))
        or not math.isfinite(finite_amount)
        or amount < 0
    ):
        raise ValueError("R02_OUTCOME_COMPUTE_VALUE_INVALID")
    unit = _require_text(value["unit"], "R02_OUTCOME_COMPUTE_UNIT_REQUIRED")
    if unit != budget_unit:
        raise ValueError("R02_OUTCOME_COMPUTE_UNIT_MISMATCH")
    return {"value": amount, "unit": unit}


def build_candidate_outcome(
    request: dict[str, Any],
    *,
    block_id: str,
    candidate_ids: list[str],
    budget_unit: str,
) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ValueError("R02_OUTCOME_REQUEST_OBJECT_REQUIRED")
    required = {
        "candidate_id",
        "status",
        "metrics",
        "compute_consumed",
        "failure_code",
    }
    if set(request) != required:
        raise ValueError("R02_OUTCOME_FIELDS_INVALID")

    canonical_block_id = _require_text(block_id, "R02_OUTCOME_BLOCK_ID_REQUIRED")
    candidate_id = _require_text(
        request["candidate_id"],
        "R02_OUTCOME_CANDIDATE_ID_REQUIRED",
    )
    if candidate_id not in candidate_ids:
        raise ValueError("R02_OUTCOME_CANDIDATE_UNKNOWN")

    status = _require_text(request["status"], "R02_OUTCOME_STATUS_REQUIRED")
    if status not in OUTCOME_STATUSES:
        raise ValueError("R02_OUTCOME_STATUS_INVALID")

    metrics = request["metrics"]
    if not isinstance(metrics, dict):
        raise ValueError("R02_OUTCOME_METRICS_OBJECT_REQUIRED")
    canonical_metrics = _canonical_json(metrics, path="metrics")

    compute_consumed = _canonical_compute_consumed(
        request["compute_consumed"],
        budget_unit=budget_unit,
    )

    failure_code = request["failure_code"]
    if status == "SCREEN_PASS":
        if failure_code is not None:
            raise ValueError("R02_OUTCOME_PASS_FAILURE_CODE_FORBIDDEN")
        canonical_failure = None
    else:
        canonical_failure = _require_text(
            failure_code,
            "R02_OUTCOME_FAILURE_CODE_REQUIRED",
        )

    body = {
        "schema": R02_OUTCOME_SCHEMA,
        "block_id": canonical_block_id,
        "candidate_id": candidate_id,
        "status": status,
        "metrics": canonical_metrics,
        "compute_consumed": compute_consumed,
        "failure_code": canonical_failure,
        "cheap_screen_qualification_authority": False,
        "qualified_pool_admission_authority": "R03_FULL_WFA_ONLY",
        "scientific_qualification": False,
    }
    result = deepcopy(body)
    result["outcome_id"] = "ROUT-" + stable_hash(body)[:24]
    result["outcome_sha256"] = stable_hash(result)
    return result


def build_terminal_manifest(
    *,
    block_id: str,
    candidate_ids: list[str],
    compute_budget: dict[str, Any],
    outcome_requests: list[dict[str, Any]],
) -> dict[str, Any]:
    canonical_block_id = _require_text(block_id, "R02_TERMINAL_BLOCK_ID_REQUIRED")
    if not candidate_ids or len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("R02_TERMINAL_CANDIDATE_AUTHORITY_INVALID")
    if not isinstance(compute_budget, dict):
        raise ValueError("R02_TERMINAL_COMPUTE_BUDGET_REQUIRED")
    budget_value = compute_budget.get("value")
    budget_unit = _require_text(
        compute_budget.get("unit"),
        "R02_TERMINAL_COMPUTE_BUDGET_UNIT_REQUIRED",
    )
    try:
        finite_budget = float(budget_value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("R02_TERMINAL_COMPUTE_BUDGET_INVALID") from exc
    if (
        isinstance(budget_value, bool)
        or not isinstance(budget_value, (int, float))
        or not math.isfinite(finite_budget)
        or budget_value <= 0
    ):
        raise ValueError("R02_TERMINAL_COMPUTE_BUDGET_INVALID")
    if not isinstance(outcome_requests, list):
        raise ValueError("R02_TERMINAL_OUTCOMES_LIST_REQUIRED")

    raw_ids = [
        str(item.get("candidate_id") or "")
        if isinstance(item, dict)
        else ""
        for item in outcome_requests
    ]
    if len(raw_ids) != len(set(raw_ids)):
        raise ValueError("R02_TERMINAL_DUPLICATE_CANDIDATE_OUTCOME")
    if set(raw_ids) != set(candidate_ids):
        raise ValueError("R02_TERMINAL_CANDIDATE_SET_MISMATCH")

    by_candidate: dict[str, dict[str, Any]] = {}
    for request in outcome_requests:
        outcome = build_candidate_outcome(
            request,
            block_id=canonical_block_id,
            candidate_ids=candidate_ids,
            budget_unit=budget_unit,
        )
        by_candidate[str(outcome["candidate_id"])] = outcome

    outcomes = [by_candidate[candidate_id] for candidate_id in candidate_ids]
    total = math.fsum(
        float(outcome["compute_consumed"]["value"])
        for outcome in outcomes
    )
    if total > finite_budget + 1e-9:
        raise ValueError("R02_TERMINAL_COMPUTE_BUDGET_EXCEEDED")

    status_counts = {
        status: sum(1 for outcome in outcomes if outcome["status"] == status)
        for status in OUTCOME_STATUSES
    }
    body = {
        "schema": R02_TERMINAL_SCHEMA,
        "block_id": canonical_block_id,
        "state": "COMPLETE_WAITING_OWNER",
        "candidate_count": len(candidate_ids),
        "outcomes": [
            {
                "candidate_id": outcome["candidate_id"],
                "outcome_id": outcome["outcome_id"],
                "outcome_sha256": outcome["outcome_sha256"],
                "status": outcome["status"],
            }
            for outcome in outcomes
        ],
        "screen_pass_count": status_counts["SCREEN_PASS"],
        "screen_fail_count": status_counts["SCREEN_FAIL"],
        "execution_error_count": status_counts["EXECUTION_ERROR"],
        "compute_consumed": {
            "value": total,
            "unit": budget_unit,
        },
        "cheap_screen_qualification_authority": False,
        "qualified_pool_admission_authority": "R03_FULL_WFA_ONLY",
        "scientific_qualification": False,
    }
    manifest_sha = stable_hash(body)
    result = deepcopy(body)
    result["terminal_id"] = "RTERM-" + manifest_sha[:24]
    result["outcome_manifest_sha256"] = manifest_sha
    result["candidate_outcomes"] = outcomes
    return result
