from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import DATABASE_PATH
from .research_r01_service import validate_r01_integrity
from .research_r01_store import get_r01_run
from .research_service import verify_no_training_side_effects
from .research_store import latest_research
from .research_r02_contract import R02_SCHEMA, r02_discovery_contract


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
