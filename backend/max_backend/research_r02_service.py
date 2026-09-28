from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH
from .research_r01_service import validate_r01_integrity
from .research_r01_store import get_r01_run
from .research_service import verify_no_training_side_effects
from .research_store import latest_research
from .research_r02_integrity import verify_r02_authority_integrity
from .research_r02_contract import (
    R02_SCHEMA,
    build_discovery_plan,
    r02_discovery_contract,
)
from .research_r02_store import (
    authorize_and_freeze_r02_discovery,
    get_r02_authorization,
    get_r02_discovery_block,
    get_r02_terminal,
)
from .optimizer_store import utc_now
from .research_contract import stable_hash


def _blocked(reason: str, *, research_id: str | None = None, r01_state: str | None = None) -> dict[str, Any]:
    return {
        "schema": R02_SCHEMA,
        "stage": "MODEL_DISCOVERY",
        "status": "BLOCKED",
        "reason": reason,
        "research_id": research_id,
        "r01_state": r01_state,
        "r01_integrity": "NOT_PROVEN",
        "source_foundation_ready": False,
        "owner_authorization_required": True,
        "owner_authorized": False,
        "runtime_start_available": False,
        "r02_executable": False,
        "model_training": 0,
        "onnx": 0,
        "research_challenger": 0,
        "champion_mutation": "NONE",
        "contract": r02_discovery_contract(),
    }


def r02_preflight(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    latest = latest_research(path=path)
    if latest is None:
        return _blocked("R02_CURRENT_R00_AND_R01_REQUIRED")

    research_id = str(latest.get("research_id") or "").strip()
    if not research_id:
        raise RuntimeError("R02_CURRENT_RESEARCH_ID_MISSING")

    run = get_r01_run(research_id, path=path)
    if run is None:
        return _blocked(
            "R02_ACCEPTED_R01_REQUIRED",
            research_id=research_id,
            r01_state="NOT_STARTED",
        )

    r01_state = str(run.get("state") or "")
    if r01_state != "PASS_WAITING_OWNER":
        return _blocked(
            "R02_ACCEPTED_R01_REQUIRED",
            research_id=research_id,
            r01_state=r01_state,
        )

    output_sha = str(run.get("output_manifest_sha") or "").strip().lower()
    if not output_sha:
        raise RuntimeError("R02_R01_OUTPUT_AUTHORITY_MISSING")
    if len(output_sha) != 64 or any(
        char not in "0123456789abcdef" for char in output_sha
    ):
        raise RuntimeError("R02_R01_OUTPUT_AUTHORITY_INVALID")

    integrity = validate_r01_integrity(path=path)
    if (
        str(integrity.get("research_id") or "") != research_id
        or str(integrity.get("status") or "") != "VERIFIED"
    ):
        raise RuntimeError("R02_R01_INTEGRITY_REQUIRED")

    side_effects = verify_no_training_side_effects(research_id, path=path)
    if side_effects["status"] != "PASS":
        raise RuntimeError("R02_PREVIOUS_SIDE_EFFECT_REGRESSION")

    frozen = get_r02_discovery_block(research_id, path=path)
    if frozen is not None:
        r02_integrity = verify_r02_authority_integrity(
            research_id,
            path=path,
        )
        terminal = get_r02_terminal(research_id, path=path)
        expected_integrity = (
            "VERIFIED_COMPLETE" if terminal is not None else "VERIFIED_FROZEN"
        )
        if str(r02_integrity.get("status") or "") != expected_integrity:
            raise RuntimeError("R02_AUTHORITY_INTEGRITY_REQUIRED")
        if terminal is not None:
            return {
                "schema": R02_SCHEMA,
                "stage": "MODEL_DISCOVERY",
                "status": "COMPLETE_WAITING_OWNER",
                "reason": None,
                "research_id": research_id,
                "r01_state": r01_state,
                "r01_output_manifest_sha256": output_sha,
                "r01_integrity": "VERIFIED",
                "r02_authority_integrity": "VERIFIED",
                "source_foundation_ready": True,
                "owner_authorization_required": False,
                "owner_authorized": True,
                "authorization_id": frozen["authorization_id"],
                "block_id": frozen["block_id"],
                "plan_id": frozen["plan_id"],
                "plan_sha256": frozen["plan_sha256"],
                "candidate_count": frozen["candidate_count"],
                "compute_budget": frozen["compute_budget"],
                "outcome_manifest_sha256": terminal[
                    "outcome_manifest_sha256"
                ],
                "screen_pass_count": terminal["screen_pass_count"],
                "screen_fail_count": terminal["screen_fail_count"],
                "execution_error_count": terminal["execution_error_count"],
                "compute_consumed": terminal["compute_consumed"],
                "cheap_screen_qualification_authority": False,
                "qualified_pool_admission_authority": "R03_FULL_WFA_ONLY",
                "runtime_start_available": False,
                "r02_executable": False,
                "model_training": int(side_effects["training_count"]),
                "onnx": int(side_effects["onnx_count"]),
                "research_challenger": int(
                    side_effects["research_challenger_count"]
                ),
                "champion_mutation": str(
                    side_effects["champion_mutation"]
                ),
                "contract": r02_discovery_contract(),
            }
        return {
            "schema": R02_SCHEMA,
            "stage": "MODEL_DISCOVERY",
            "status": "FROZEN_WAITING_EXECUTION",
            "reason": None,
            "research_id": research_id,
            "r01_state": r01_state,
            "r01_output_manifest_sha256": output_sha,
        return {
            "schema": R02_SCHEMA,
            "stage": "MODEL_DISCOVERY",
            "status": "FROZEN_WAITING_EXECUTION",
            "reason": None,
            "research_id": research_id,
            "r01_state": r01_state,
            "r01_output_manifest_sha256": output_sha,
            "r01_integrity": "VERIFIED",
            "r02_authority_integrity": "VERIFIED",
            "source_foundation_ready": True,
            "owner_authorization_required": False,
            "owner_authorized": True,
            "authorization_id": frozen["authorization_id"],
            "block_id": frozen["block_id"],
            "plan_id": frozen["plan_id"],
            "plan_sha256": frozen["plan_sha256"],
            "candidate_count": frozen["candidate_count"],
            "compute_budget": frozen["compute_budget"],
            "runtime_start_available": False,
            "r02_executable": False,
            "model_training": int(side_effects["training_count"]),
            "onnx": int(side_effects["onnx_count"]),
            "research_challenger": int(side_effects["research_challenger_count"]),
            "champion_mutation": str(side_effects["champion_mutation"]),
            "contract": r02_discovery_contract(),
        }

    return {
        "schema": R02_SCHEMA,
        "stage": "MODEL_DISCOVERY",
        "status": "READY_FOR_OWNER_AUTHORIZATION",
        "reason": None,
        "research_id": research_id,
        "r01_state": r01_state,
        "r01_output_manifest_sha256": output_sha,
        "r01_integrity": "VERIFIED",
        "source_foundation_ready": True,
        "owner_authorization_required": True,
        "owner_authorized": False,
        "runtime_start_available": False,
        "r02_executable": False,
        "model_training": int(side_effects["training_count"]),
        "onnx": int(side_effects["onnx_count"]),
        "research_challenger": int(side_effects["research_challenger_count"]),
        "champion_mutation": str(side_effects["champion_mutation"]),
        "contract": r02_discovery_contract(),
    }


OWNER_R02_CONFIRMATION = "OWNER_EXPLICIT_R02_DISCOVERY_AUTHORIZE"
R02_AUTHORIZATION_SCHEMA = "MAX_RESEARCH_OWNER_AUTHORIZATION_R02_V1"


def authorize_r02_discovery(
    request: dict[str, Any],
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ValueError("R02_AUTHORIZATION_REQUEST_OBJECT_REQUIRED")
    if set(request) != {"confirmed", "owner_confirmation", "plan"}:
        raise ValueError("R02_AUTHORIZATION_REQUEST_FIELDS_INVALID")
    if request.get("confirmed") is not True:
        raise RuntimeError("R02_OWNER_CONFIRMATION_REQUIRED")
    if str(request.get("owner_confirmation") or "") != OWNER_R02_CONFIRMATION:
        raise RuntimeError("R02_OWNER_AUTHORIZATION_INVALID")

    plan = build_discovery_plan(request.get("plan"))
    preflight = r02_preflight(path=path)
    research_id = str(plan["research_id"])
    if str(preflight.get("research_id") or "") != research_id:
        raise RuntimeError("R02_RESEARCH_ID_STALE")
    if str(preflight.get("r01_output_manifest_sha256") or "") != str(
        plan["r01_output_manifest_sha256"]
    ):
        raise RuntimeError("R02_R01_OUTPUT_AUTHORITY_STALE")

    if preflight["status"] in {
        "FROZEN_WAITING_EXECUTION",
        "COMPLETE_WAITING_OWNER",
    }:
        existing = get_r02_discovery_block(research_id, path=path)
        if existing is None:
            raise RuntimeError("R02_FROZEN_BLOCK_MISSING")
        if str(existing["plan_sha256"]) != str(plan["plan_sha256"]):
            raise RuntimeError("R02_DISCOVERY_BLOCK_ALREADY_FROZEN")
        authorization = get_r02_authorization(
            str(existing["authorization_id"]),
            path=path,
        )
        if authorization is None:
            raise RuntimeError("R02_FROZEN_AUTHORIZATION_MISSING")
        return {
            "status": str(preflight["status"]),
            "idempotent": True,
            "authorization": authorization,
            "block": existing,
            "execution_available": False,
            "scientific_result": False,
            "model_training": 0,
            "onnx": 0,
            "research_challenger": 0,
            "champion_mutation": "NONE",
        }

    if preflight["status"] != "READY_FOR_OWNER_AUTHORIZATION":
        raise RuntimeError("R02_OWNER_AUTHORIZATION_NOT_READY")

    body = {
        "schema": R02_AUTHORIZATION_SCHEMA,
        "gate": "R02",
        "action": "AUTHORIZE_DISCOVERY",
        "confirmed": True,
        "owner_confirmation": OWNER_R02_CONFIRMATION,
        "research_id": research_id,
        "r01_output_manifest_sha256": str(plan["r01_output_manifest_sha256"]),
        "plan_id": str(plan["plan_id"]),
        "plan_sha256": str(plan["plan_sha256"]),
        "candidate_count": int(plan["candidate_count"]),
        "candidate_ids": list(plan["candidate_ids"]),
        "compute_budget": deepcopy(plan["compute_budget"]),
        "cheap_screen_qualification_authority": False,
        "automatic_second_discovery_block": False,
        "execution_available": False,
    }
    payload_sha = stable_hash(body)
    authorization, block = authorize_and_freeze_r02_discovery(
        authorization_record={
            "authorization_id": "RAUTH-R02-" + payload_sha[:24],
            "research_id": research_id,
            "confirmed": True,
            "payload_sha256": payload_sha,
            "payload": body,
            "authorized_utc": utc_now(),
        },
        plan=plan,
        path=path,
    )
    return {
        "status": "FROZEN_WAITING_EXECUTION",
        "idempotent": False,
        "authorization": authorization,
        "block": block,
        "execution_available": False,
        "scientific_result": False,
        "model_training": 0,
        "onnx": 0,
        "research_challenger": 0,
        "champion_mutation": "NONE",
    }
