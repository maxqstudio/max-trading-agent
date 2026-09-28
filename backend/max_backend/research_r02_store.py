from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH
from .db import connect
from .optimizer_store import utc_now
from .research_contract import stable_hash
from .workflow_store import migrate_current


def _decode_authorization(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["confirmed"] = bool(result["confirmed"])
    result["payload"] = json.loads(result.pop("payload_json"))
    return result


def get_r02_authorization(
    authorization_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    migrate_current(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM research_r02_authorizations WHERE authorization_id=?",
            (str(authorization_id),),
        ).fetchone()
    return _decode_authorization(row) if row is not None else None


def create_r02_authorization(
    record: dict[str, Any],
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_current(path)
    required = {
        "authorization_id",
        "research_id",
        "confirmed",
        "payload_sha256",
        "payload",
        "authorized_utc",
    }
    if set(record) != required:
        raise ValueError("R02_AUTHORIZATION_FIELDS_INVALID")
    if record["confirmed"] is not True:
        raise ValueError("R02_AUTHORIZATION_CONFIRMATION_REQUIRED")
    payload = record["payload"]
    if not isinstance(payload, dict):
        raise ValueError("R02_AUTHORIZATION_PAYLOAD_REQUIRED")
    if stable_hash(payload) != str(record["payload_sha256"]):
        raise ValueError("R02_AUTHORIZATION_PAYLOAD_HASH_MISMATCH")
    if str(payload.get("research_id") or "") != str(record["research_id"]):
        raise ValueError("R02_AUTHORIZATION_RESEARCH_ID_MISMATCH")
    if str(payload.get("gate") or "") != "R02":
        raise ValueError("R02_AUTHORIZATION_GATE_INVALID")
    if str(payload.get("action") or "") != "AUTHORIZE_DISCOVERY":
        raise ValueError("R02_AUTHORIZATION_ACTION_INVALID")
    if payload.get("confirmed") is not True:
        raise ValueError("R02_AUTHORIZATION_CONFIRMATION_REQUIRED")

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing_id = conn.execute(
            "SELECT * FROM research_r02_authorizations WHERE authorization_id=?",
            (str(record["authorization_id"]),),
        ).fetchone()
        if existing_id is not None:
            decoded = _decode_authorization(existing_id)
            if (
                str(decoded["research_id"]) != str(record["research_id"])
                or str(decoded["payload_sha256"]) != str(record["payload_sha256"])
                or decoded["payload"] != payload
            ):
                raise RuntimeError("R02_AUTHORIZATION_IDENTITY_COLLISION")
            return decoded

        existing_research = conn.execute(
            "SELECT * FROM research_r02_authorizations WHERE research_id=?",
            (str(record["research_id"]),),
        ).fetchone()
        if existing_research is not None:
            raise RuntimeError("R02_AUTHORIZATION_ALREADY_FROZEN")

        conn.execute(
            """
            INSERT INTO research_r02_authorizations(
                authorization_id,research_id,confirmed,payload_sha256,
                payload_json,authorized_utc
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                str(record["authorization_id"]),
                str(record["research_id"]),
                1,
                str(record["payload_sha256"]),
                encoded,
                str(record["authorized_utc"]),
            ),
        )
    created = get_r02_authorization(str(record["authorization_id"]), path=path)
    if created is None:
        raise RuntimeError("R02_AUTHORIZATION_CREATE_FAILED")
    return created


def get_r02_discovery_block(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    migrate_current(path)
    with connect(path) as conn:
        block = conn.execute(
            "SELECT * FROM research_r02_discovery_blocks WHERE research_id=?",
            (str(research_id),),
        ).fetchone()
        if block is None:
            return None
        candidates = conn.execute(
            """
            SELECT * FROM research_r02_candidate_specs
            WHERE block_id=?
            ORDER BY ordinal,candidate_id
            """,
            (str(block["block_id"]),),
        ).fetchall()
    result = dict(block)
    result["compute_budget"] = json.loads(result.pop("compute_budget_json"))
    result["candidates"] = []
    for row in candidates:
        item = dict(row)
        item["spec"] = json.loads(item.pop("spec_json"))
        result["candidates"].append(item)
    return result


def freeze_r02_discovery_block(
    *,
    authorization: dict[str, Any],
    plan: dict[str, Any],
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_current(path)
    research_id = str(plan.get("research_id") or "")
    if not research_id:
        raise ValueError("R02_RESEARCH_ID_REQUIRED")
    if str(authorization.get("research_id") or "") != research_id:
        raise RuntimeError("R02_AUTHORIZATION_RESEARCH_ID_MISMATCH")
    payload = authorization.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("R02_AUTHORIZATION_PAYLOAD_REQUIRED")
    if str(payload.get("plan_id") or "") != str(plan.get("plan_id") or ""):
        raise RuntimeError("R02_AUTHORIZATION_PLAN_ID_MISMATCH")
    if str(payload.get("plan_sha256") or "") != str(plan.get("plan_sha256") or ""):
        raise RuntimeError("R02_AUTHORIZATION_PLAN_SHA_MISMATCH")
    if str(payload.get("r01_output_manifest_sha256") or "") != str(
        plan.get("r01_output_manifest_sha256") or ""
    ):
        raise RuntimeError("R02_AUTHORIZATION_R01_OUTPUT_MISMATCH")

    block_body = {
        "research_id": research_id,
        "authorization_id": str(authorization["authorization_id"]),
        "r01_output_manifest_sha256": str(plan["r01_output_manifest_sha256"]),
        "plan_id": str(plan["plan_id"]),
        "plan_sha256": str(plan["plan_sha256"]),
    }
    block_id = "RDISC-" + stable_hash(block_body)[:24]
    candidates = list(plan.get("candidates") or [])
    if len(candidates) != int(plan.get("candidate_count") or 0):
        raise RuntimeError("R02_PLAN_CANDIDATE_COUNT_MISMATCH")

    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        project = conn.execute(
            """
            SELECT training_count,onnx_count,research_challenger_count,champion_mutation
            FROM research_projects WHERE research_id=?
            """,
            (research_id,),
        ).fetchone()
        if project is None:
            raise FileNotFoundError(research_id)
        if (
            int(project["training_count"]) != 0
            or int(project["onnx_count"]) != 0
            or int(project["research_challenger_count"]) != 0
            or str(project["champion_mutation"]) != "NONE"
        ):
            raise RuntimeError("R02_PREVIOUS_SIDE_EFFECT_REGRESSION")

        r01 = conn.execute(
            """
            SELECT state,output_manifest_sha
            FROM research_r01_runs WHERE research_id=?
            """,
            (research_id,),
        ).fetchone()
        if (
            r01 is None
            or str(r01["state"]) != "PASS_WAITING_OWNER"
            or str(r01["output_manifest_sha"] or "").lower()
            != str(plan["r01_output_manifest_sha256"]).lower()
        ):
            raise RuntimeError("R02_ACCEPTED_R01_AUTHORITY_REQUIRED")

        existing = conn.execute(
            "SELECT * FROM research_r02_discovery_blocks WHERE research_id=?",
            (research_id,),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["block_id"]) != block_id
                or str(existing["authorization_id"])
                != str(authorization["authorization_id"])
                or str(existing["plan_sha256"]) != str(plan["plan_sha256"])
            ):
                raise RuntimeError("R02_DISCOVERY_BLOCK_ALREADY_FROZEN")
        else:
            now = utc_now()
            conn.execute(
                """
                INSERT INTO research_r02_discovery_blocks(
                    block_id,research_id,authorization_id,state,
                    r01_output_manifest_sha256,plan_id,plan_sha256,
                    candidate_count,compute_budget_json,created_utc
                ) VALUES(?,?,?,'FROZEN_WAITING_EXECUTION',?,?,?,?,?,?)
                """,
                (
                    block_id,
                    research_id,
                    str(authorization["authorization_id"]),
                    str(plan["r01_output_manifest_sha256"]),
                    str(plan["plan_id"]),
                    str(plan["plan_sha256"]),
                    int(plan["candidate_count"]),
                    json.dumps(
                        plan["compute_budget"],
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                ),
            )
            for ordinal, candidate in enumerate(candidates):
                spec = deepcopy(candidate)
                candidate_id = str(spec.pop("candidate_id"))
                spec_sha = stable_hash(spec)
                conn.execute(
                    """
                    INSERT INTO research_r02_candidate_specs(
                        candidate_id,block_id,ordinal,model_family,seed,
                        spec_sha256,spec_json,created_utc
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        candidate_id,
                        block_id,
                        ordinal,
                        str(candidate["model_family"]),
                        int(candidate["seed"]),
                        spec_sha,
                        json.dumps(
                            spec,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        now,
                    ),
                )

    result = get_r02_discovery_block(research_id, path=path)
    if result is None:
        raise RuntimeError("R02_DISCOVERY_BLOCK_CREATE_FAILED")
    if len(result["candidates"]) != int(plan["candidate_count"]):
        raise RuntimeError("R02_DISCOVERY_BLOCK_CANDIDATE_PERSISTENCE_MISMATCH")
    return result
