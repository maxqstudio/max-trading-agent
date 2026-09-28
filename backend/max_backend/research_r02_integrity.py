from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH
from .db import connect
from .research_contract import candidate_id as derive_candidate_id, stable_hash
from .research_r02_contract import build_discovery_plan
from .research_r02_outcome import build_candidate_outcome, build_terminal_manifest
from .workflow_store import migrate_current


def _int_value(value: Any, code: str) -> int:
    try:
        result = int(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise RuntimeError(code) from exc
    if isinstance(value, bool):
        raise RuntimeError(code)
    return result


def _json_object(raw: Any, code: str) -> dict[str, Any]:
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(code) from exc
    if not isinstance(value, dict):
        raise RuntimeError(code)
    return value


def _authorization_payload(row: Any, research_id: str) -> dict[str, Any]:
    if row is None:
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_MISSING")
    payload = _json_object(
        row["payload_json"],
        "R02_INTEGRITY_AUTHORIZATION_PAYLOAD_INVALID",
    )
    payload_sha = stable_hash(payload)
    if payload_sha != str(row["payload_sha256"]):
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_PAYLOAD_HASH_MISMATCH")
    if str(row["authorization_id"]) != "RAUTH-R02-" + payload_sha[:24]:
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_ID_MISMATCH")
    if str(row["research_id"]) != research_id:
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_RESEARCH_MISMATCH")
    if _int_value(
        row["confirmed"],
        "R02_INTEGRITY_AUTHORIZATION_CONFIRMATION_MISMATCH",
    ) != 1 or payload.get("confirmed") is not True:
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_CONFIRMATION_MISMATCH")

    expected_fields = {
        "schema",
        "gate",
        "action",
        "confirmed",
        "owner_confirmation",
        "research_id",
        "r01_output_manifest_sha256",
        "plan_id",
        "plan_sha256",
        "candidate_count",
        "candidate_ids",
        "compute_budget",
        "cheap_screen_qualification_authority",
        "automatic_second_discovery_block",
        "execution_available",
    }
    if set(payload) != expected_fields:
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_FIELDS_MISMATCH")
    if payload.get("schema") != "MAX_RESEARCH_OWNER_AUTHORIZATION_R02_V1":
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_SCHEMA_MISMATCH")
    if str(payload.get("research_id") or "") != research_id:
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_RESEARCH_MISMATCH")
    if str(payload.get("gate") or "") != "R02":
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_GATE_MISMATCH")
    if str(payload.get("action") or "") != "AUTHORIZE_DISCOVERY":
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_ACTION_MISMATCH")
    if (
        payload.get("owner_confirmation")
        != "OWNER_EXPLICIT_R02_DISCOVERY_AUTHORIZE"
    ):
        raise RuntimeError("R02_INTEGRITY_OWNER_CONFIRMATION_MISMATCH")
    if payload.get("cheap_screen_qualification_authority") is not False:
        raise RuntimeError("R02_INTEGRITY_QUALIFICATION_AUTHORITY_MISMATCH")
    if payload.get("automatic_second_discovery_block") is not False:
        raise RuntimeError("R02_INTEGRITY_SECOND_BLOCK_POLICY_MISMATCH")
    if payload.get("execution_available") is not False:
        raise RuntimeError("R02_INTEGRITY_EXECUTION_AUTHORITY_MISMATCH")
    return payload


def _candidate_specs(
    rows: list[Any],
    *,
    block_id: str,
    research_id: str,
    r01_output_sha: str,
) -> list[dict[str, Any]]:
    if not rows:
        raise RuntimeError("R02_INTEGRITY_CANDIDATE_SPECS_MISSING")
    specs: list[dict[str, Any]] = []
    for expected_ordinal, row in enumerate(rows):
        if _int_value(
            row["ordinal"],
            "R02_INTEGRITY_CANDIDATE_ORDINAL_MISMATCH",
        ) != expected_ordinal:
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_ORDINAL_MISMATCH")
        if str(row["block_id"]) != block_id:
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_BLOCK_MISMATCH")
        spec = _json_object(
            row["spec_json"],
            "R02_INTEGRITY_CANDIDATE_SPEC_JSON_INVALID",
        )
        if stable_hash(spec) != str(row["spec_sha256"]):
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_SPEC_HASH_MISMATCH")
        try:
            candidate_id = derive_candidate_id(spec)
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_ID_REBUILD_FAILED") from exc
        if candidate_id != str(row["candidate_id"]):
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_ID_MISMATCH")
        if str(spec.get("research_id") or "") != research_id:
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_RESEARCH_MISMATCH")
        if str(spec.get("model_family") or "") != str(row["model_family"]):
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_FAMILY_MISMATCH")
        if _int_value(
            spec.get("seed"),
            "R02_INTEGRITY_CANDIDATE_SEED_MISMATCH",
        ) != _int_value(
            row["seed"],
            "R02_INTEGRITY_CANDIDATE_SEED_MISMATCH",
        ):
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_SEED_MISMATCH")
        parent = spec.get("parent_lineage")
        if not isinstance(parent, dict):
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_PARENT_MISSING")
        if str(parent.get("r01_output_manifest_sha256") or "") != r01_output_sha:
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_R01_OUTPUT_MISMATCH")
        canonical = deepcopy(spec)
        canonical["candidate_id"] = candidate_id
        specs.append(canonical)
    return specs


def _rebuild_plan(
    *,
    block: Any,
    authorization_payload: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    candidate_count = _int_value(
        block["candidate_count"],
        "R02_INTEGRITY_CANDIDATE_COUNT_MISMATCH",
    )
    if len(candidates) != candidate_count:
        raise RuntimeError("R02_INTEGRITY_CANDIDATE_COUNT_MISMATCH")
    first = candidates[0]
    parent_lineage = first.get("parent_lineage")
    if not isinstance(parent_lineage, dict):
        raise RuntimeError("R02_INTEGRITY_PARENT_LINEAGE_MISSING")
    budget = _json_object(
        block["compute_budget_json"],
        "R02_INTEGRITY_COMPUTE_BUDGET_INVALID",
    )
    if set(budget) != {"value", "unit", "execution_semantics"}:
        raise RuntimeError("R02_INTEGRITY_COMPUTE_BUDGET_FIELDS_MISMATCH")
    if budget.get("execution_semantics") != "FROZEN_ONLY_NOT_EXECUTED":
        raise RuntimeError("R02_INTEGRITY_COMPUTE_BUDGET_SEMANTICS_MISMATCH")

    request = {
        "research_id": str(block["research_id"]),
        "r01_output_manifest_sha256": str(block["r01_output_manifest_sha256"]),
        "feature_contract": str(first.get("feature_contract") or ""),
        "label_contract": str(first.get("label_contract") or ""),
        "parent_lineage": deepcopy(parent_lineage),
        "candidate_count": candidate_count,
        "compute_budget": {
            "value": budget["value"],
            "unit": budget["unit"],
        },
        "candidates": [
            {key: deepcopy(value) for key, value in candidate.items() if key != "candidate_id"}
            for candidate in candidates
        ],
    }
    try:
        plan = build_discovery_plan(request)
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError("R02_INTEGRITY_PLAN_REBUILD_FAILED") from exc

    if str(plan["plan_id"]) != str(block["plan_id"]):
        raise RuntimeError("R02_INTEGRITY_PLAN_ID_MISMATCH")
    if str(plan["plan_sha256"]) != str(block["plan_sha256"]):
        raise RuntimeError("R02_INTEGRITY_PLAN_SHA_MISMATCH")
    if plan["compute_budget"] != budget:
        raise RuntimeError("R02_INTEGRITY_COMPUTE_BUDGET_MISMATCH")

    candidate_ids = [str(item["candidate_id"]) for item in plan["candidates"]]
    if candidate_ids != list(authorization_payload.get("candidate_ids") or []):
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_CANDIDATE_IDS_MISMATCH")
    if _int_value(
        authorization_payload.get("candidate_count"),
        "R02_INTEGRITY_AUTHORIZATION_CANDIDATE_COUNT_MISMATCH",
    ) != candidate_count:
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_CANDIDATE_COUNT_MISMATCH")
    if authorization_payload.get("compute_budget") != budget:
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_COMPUTE_BUDGET_MISMATCH")
    if str(authorization_payload.get("plan_id") or "") != str(block["plan_id"]):
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_PLAN_ID_MISMATCH")
    if str(authorization_payload.get("plan_sha256") or "") != str(
        block["plan_sha256"]
    ):
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_PLAN_SHA_MISMATCH")
    if str(authorization_payload.get("r01_output_manifest_sha256") or "") != str(
        block["r01_output_manifest_sha256"]
    ):
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_R01_OUTPUT_MISMATCH")
    return plan


def _verify_block_id(block: Any) -> None:
    body = {
        "research_id": str(block["research_id"]),
        "authorization_id": str(block["authorization_id"]),
        "r01_output_manifest_sha256": str(block["r01_output_manifest_sha256"]),
        "plan_id": str(block["plan_id"]),
        "plan_sha256": str(block["plan_sha256"]),
    }
    expected = "RDISC-" + stable_hash(body)[:24]
    if expected != str(block["block_id"]):
        raise RuntimeError("R02_INTEGRITY_BLOCK_ID_MISMATCH")
    if str(block["state"]) != "FROZEN_WAITING_EXECUTION":
        raise RuntimeError("R02_INTEGRITY_BLOCK_STATE_MISMATCH")


def _outcome_request(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": payload.get("candidate_id"),
        "status": payload.get("status"),
        "metrics": deepcopy(payload.get("metrics")),
        "compute_consumed": deepcopy(payload.get("compute_consumed")),
        "failure_code": payload.get("failure_code"),
    }


def _verify_complete_ledger(
    *,
    block: Any,
    plan: dict[str, Any],
    outcome_rows: list[Any],
    terminal: Any,
) -> dict[str, Any]:
    if terminal is None:
        if outcome_rows:
            raise RuntimeError("R02_INTEGRITY_PARTIAL_OUTCOME_STATE")
        return {
            "status": "VERIFIED_FROZEN",
            "outcome_count": 0,
            "terminal_id": None,
        }

    candidate_ids = [str(item["candidate_id"]) for item in plan["candidates"]]
    if len(outcome_rows) != len(candidate_ids):
        raise RuntimeError("R02_INTEGRITY_OUTCOME_COUNT_MISMATCH")

    requests: list[dict[str, Any]] = []
    for expected_candidate_id, row in zip(candidate_ids, outcome_rows, strict=True):
        if str(row["block_id"]) != str(block["block_id"]):
            raise RuntimeError("R02_INTEGRITY_OUTCOME_BLOCK_MISMATCH")
        if str(row["candidate_id"]) != expected_candidate_id:
            raise RuntimeError("R02_INTEGRITY_OUTCOME_CANDIDATE_ORDER_MISMATCH")
        payload = _json_object(
            row["outcome_json"],
            "R02_INTEGRITY_OUTCOME_JSON_INVALID",
        )
        request = _outcome_request(payload)
        try:
            rebuilt = build_candidate_outcome(
                request,
                block_id=str(block["block_id"]),
                candidate_ids=candidate_ids,
                budget_unit=str(plan["compute_budget"]["unit"]),
            )
        except (RuntimeError, ValueError) as exc:
            raise RuntimeError("R02_INTEGRITY_OUTCOME_REBUILD_FAILED") from exc
        if rebuilt != payload:
            raise RuntimeError("R02_INTEGRITY_OUTCOME_PAYLOAD_MISMATCH")
        if str(row["outcome_id"]) != str(rebuilt["outcome_id"]):
            raise RuntimeError("R02_INTEGRITY_OUTCOME_ID_MISMATCH")
        if str(row["outcome_sha256"]) != str(rebuilt["outcome_sha256"]):
            raise RuntimeError("R02_INTEGRITY_OUTCOME_SHA_MISMATCH")
        if str(row["status"]) != str(rebuilt["status"]):
            raise RuntimeError("R02_INTEGRITY_OUTCOME_STATUS_MISMATCH")
        requests.append(request)

    try:
        rebuilt_terminal = build_terminal_manifest(
            block_id=str(block["block_id"]),
            candidate_ids=candidate_ids,
            compute_budget=deepcopy(plan["compute_budget"]),
            outcome_requests=requests,
        )
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError("R02_INTEGRITY_TERMINAL_REBUILD_FAILED") from exc

    expected_fields = {
        "terminal_id": rebuilt_terminal["terminal_id"],
        "block_id": rebuilt_terminal["block_id"],
        "state": rebuilt_terminal["state"],
        "outcome_manifest_sha256": rebuilt_terminal["outcome_manifest_sha256"],
        "candidate_count": rebuilt_terminal["candidate_count"],
        "screen_pass_count": rebuilt_terminal["screen_pass_count"],
        "screen_fail_count": rebuilt_terminal["screen_fail_count"],
        "execution_error_count": rebuilt_terminal["execution_error_count"],
    }
    for field, expected in expected_fields.items():
        actual = terminal[field]
        if str(actual) != str(expected):
            raise RuntimeError(
                "R02_INTEGRITY_TERMINAL_FIELD_MISMATCH:" + field
            )
    compute_consumed = _json_object(
        terminal["compute_consumed_json"],
        "R02_INTEGRITY_TERMINAL_COMPUTE_INVALID",
    )
    if compute_consumed != rebuilt_terminal["compute_consumed"]:
        raise RuntimeError("R02_INTEGRITY_TERMINAL_COMPUTE_MISMATCH")
    return {
        "status": "VERIFIED_COMPLETE",
        "outcome_count": len(outcome_rows),
        "terminal_id": str(terminal["terminal_id"]),
        "outcome_manifest_sha256": str(terminal["outcome_manifest_sha256"]),
    }


def verify_r02_authority_integrity(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_current(path)
    research_id = str(research_id or "").strip()
    if not research_id:
        raise ValueError("R02_INTEGRITY_RESEARCH_ID_REQUIRED")

    with connect(path) as conn:
        block = conn.execute(
            "SELECT * FROM research_r02_discovery_blocks WHERE research_id=?",
            (research_id,),
        ).fetchone()
        if block is None:
            return {
                "status": "ABSENT",
                "research_id": research_id,
                "block_id": None,
                "candidate_count": 0,
                "outcome_count": 0,
            }
        authorization = conn.execute(
            "SELECT * FROM research_r02_authorizations WHERE authorization_id=?",
            (str(block["authorization_id"]),),
        ).fetchone()
        candidates = conn.execute(
            """
            SELECT * FROM research_r02_candidate_specs
            WHERE block_id=?
            ORDER BY ordinal,candidate_id
            """,
            (str(block["block_id"]),),
        ).fetchall()
        outcomes = conn.execute(
            """
            SELECT outcome.*
            FROM research_r02_candidate_outcomes AS outcome
            JOIN research_r02_candidate_specs AS spec
              ON spec.candidate_id=outcome.candidate_id
            WHERE outcome.block_id=?
            ORDER BY spec.ordinal,spec.candidate_id
            """,
            (str(block["block_id"]),),
        ).fetchall()
        terminal = conn.execute(
            "SELECT * FROM research_r02_block_terminals WHERE block_id=?",
            (str(block["block_id"]),),
        ).fetchone()
        r01 = conn.execute(
            "SELECT state,output_manifest_sha FROM research_r01_runs WHERE research_id=?",
            (research_id,),
        ).fetchone()

    _verify_block_id(block)
    r01_output_sha = str(block["r01_output_manifest_sha256"])
    if (
        r01 is None
        or str(r01["state"]) != "PASS_WAITING_OWNER"
        or str(r01["output_manifest_sha"] or "").lower() != r01_output_sha.lower()
    ):
        raise RuntimeError("R02_INTEGRITY_R01_AUTHORITY_MISMATCH")

    payload = _authorization_payload(authorization, research_id)
    if str(block["authorization_id"]) != str(authorization["authorization_id"]):
        raise RuntimeError("R02_INTEGRITY_BLOCK_AUTHORIZATION_MISMATCH")

    canonical_candidates = _candidate_specs(
        list(candidates),
        block_id=str(block["block_id"]),
        research_id=research_id,
        r01_output_sha=r01_output_sha,
    )
    plan = _rebuild_plan(
        block=block,
        authorization_payload=payload,
        candidates=canonical_candidates,
    )
    terminal_result = _verify_complete_ledger(
        block=block,
        plan=plan,
        outcome_rows=list(outcomes),
        terminal=terminal,
    )
    return {
        "status": terminal_result["status"],
        "research_id": research_id,
        "block_id": str(block["block_id"]),
        "authorization_id": str(block["authorization_id"]),
        "plan_id": str(block["plan_id"]),
        "plan_sha256": str(block["plan_sha256"]),
        "candidate_count": len(canonical_candidates),
        "outcome_count": int(terminal_result["outcome_count"]),
        "terminal_id": terminal_result.get("terminal_id"),
        "outcome_manifest_sha256": terminal_result.get(
            "outcome_manifest_sha256"
        ),
    }
