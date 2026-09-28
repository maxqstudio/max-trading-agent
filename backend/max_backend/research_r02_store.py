from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH
from .db import connect
from .optimizer_store import utc_now
from .research_contract import candidate_id as derive_candidate_id, stable_hash
from .research_r02_outcome import build_candidate_outcome, build_terminal_manifest
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


def _validate_authorization_record(
    record: dict[str, Any],
) -> tuple[dict[str, Any], str]:
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
    payload_sha = stable_hash(payload)
    if payload_sha != str(record["payload_sha256"]):
        raise ValueError("R02_AUTHORIZATION_PAYLOAD_HASH_MISMATCH")
    if str(record["authorization_id"]) != "RAUTH-R02-" + payload_sha[:24]:
        raise ValueError("R02_AUTHORIZATION_ID_INVALID")
    if str(payload.get("research_id") or "") != str(record["research_id"]):
        raise ValueError("R02_AUTHORIZATION_RESEARCH_ID_MISMATCH")
    if str(payload.get("gate") or "") != "R02":
        raise ValueError("R02_AUTHORIZATION_GATE_INVALID")
    if str(payload.get("action") or "") != "AUTHORIZE_DISCOVERY":
        raise ValueError("R02_AUTHORIZATION_ACTION_INVALID")
    if payload.get("confirmed") is not True:
        raise ValueError("R02_AUTHORIZATION_CONFIRMATION_REQUIRED")
    return payload, json.dumps(payload, sort_keys=True, separators=(",", ":"))


def create_r02_authorization(
    record: dict[str, Any],
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_current(path)
    payload, encoded = _validate_authorization_record(record)
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


def _validate_plan_integrity(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict):
        raise ValueError("R02_PLAN_REQUIRED")
    supplied_sha = str(plan.get("plan_sha256") or "")
    supplied_id = str(plan.get("plan_id") or "")
    if not supplied_sha or not supplied_id:
        raise RuntimeError("R02_PLAN_AUTHORITY_MISSING")

    sha_body = deepcopy(plan)
    sha_body.pop("plan_sha256", None)
    if stable_hash(sha_body) != supplied_sha:
        raise RuntimeError("R02_PLAN_INTEGRITY_MISMATCH")

    id_body = deepcopy(sha_body)
    id_body.pop("plan_id", None)
    if "RPLAN-" + stable_hash(id_body)[:24] != supplied_id:
        raise RuntimeError("R02_PLAN_ID_INTEGRITY_MISMATCH")

    candidates = list(plan.get("candidates") or [])
    ids = [str(item.get("candidate_id") or "") for item in candidates]
    if ids != list(plan.get("candidate_ids") or []):
        raise RuntimeError("R02_PLAN_CANDIDATE_IDS_MISMATCH")
    if len(ids) != int(plan.get("candidate_count") or 0):
        raise RuntimeError("R02_PLAN_CANDIDATE_COUNT_MISMATCH")
    for candidate in candidates:
        spec = deepcopy(candidate)
        supplied_candidate_id = str(spec.pop("candidate_id", ""))
        if not supplied_candidate_id:
            raise RuntimeError("R02_CANDIDATE_ID_MISSING")
        if derive_candidate_id(spec) != supplied_candidate_id:
            raise RuntimeError("R02_CANDIDATE_ID_INTEGRITY_MISMATCH")


def _validated_freeze_identity(
    *,
    authorization: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[str, str, list[dict[str, Any]]]:
    _validate_plan_integrity(plan)
    research_id = str(plan.get("research_id") or "")
    if not research_id:
        raise ValueError("R02_RESEARCH_ID_REQUIRED")
    if str(authorization.get("research_id") or "") != research_id:
        raise RuntimeError("R02_AUTHORIZATION_RESEARCH_ID_MISMATCH")
    payload = authorization.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("R02_AUTHORIZATION_PAYLOAD_REQUIRED")
    if stable_hash(payload) != str(authorization.get("payload_sha256") or ""):
        raise RuntimeError("R02_AUTHORIZATION_PAYLOAD_HASH_MISMATCH")
    if str(authorization.get("authorization_id") or "") != (
        "RAUTH-R02-" + stable_hash(payload)[:24]
    ):
        raise RuntimeError("R02_AUTHORIZATION_ID_INVALID")
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
    return research_id, block_id, candidates


def _freeze_r02_discovery_block_in_connection(
    conn: Any,
    *,
    authorization: dict[str, Any],
    plan: dict[str, Any],
    research_id: str,
    block_id: str,
    candidates: list[dict[str, Any]],
) -> None:
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
        return

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


def freeze_r02_discovery_block(
    *,
    authorization: dict[str, Any],
    plan: dict[str, Any],
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_current(path)
    research_id, block_id, candidates = _validated_freeze_identity(
        authorization=authorization,
        plan=plan,
    )
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _freeze_r02_discovery_block_in_connection(
            conn,
            authorization=authorization,
            plan=plan,
            research_id=research_id,
            block_id=block_id,
            candidates=candidates,
        )

    result = get_r02_discovery_block(research_id, path=path)
    if result is None:
        raise RuntimeError("R02_DISCOVERY_BLOCK_CREATE_FAILED")
    if len(result["candidates"]) != int(plan["candidate_count"]):
        raise RuntimeError("R02_DISCOVERY_BLOCK_CANDIDATE_PERSISTENCE_MISMATCH")
    return result



def authorize_and_freeze_r02_discovery(
    *,
    authorization_record: dict[str, Any],
    plan: dict[str, Any],
    path: Path = DATABASE_PATH,
) -> tuple[dict[str, Any], dict[str, Any]]:
    migrate_current(path)
    payload, encoded = _validate_authorization_record(authorization_record)
    authorization = deepcopy(authorization_record)
    authorization["payload"] = payload
    research_id, block_id, candidates = _validated_freeze_identity(
        authorization=authorization,
        plan=plan,
    )

    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing_id = conn.execute(
            "SELECT * FROM research_r02_authorizations WHERE authorization_id=?",
            (str(authorization["authorization_id"]),),
        ).fetchone()
        if existing_id is not None:
            decoded = _decode_authorization(existing_id)
            if (
                str(decoded["research_id"]) != research_id
                or str(decoded["payload_sha256"])
                != str(authorization["payload_sha256"])
                or decoded["payload"] != payload
            ):
                raise RuntimeError("R02_AUTHORIZATION_IDENTITY_COLLISION")
            authorization = decoded
        else:
            existing_research = conn.execute(
                "SELECT * FROM research_r02_authorizations WHERE research_id=?",
                (research_id,),
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
                    str(authorization["authorization_id"]),
                    research_id,
                    1,
                    str(authorization["payload_sha256"]),
                    encoded,
                    str(authorization["authorized_utc"]),
                ),
            )

        _freeze_r02_discovery_block_in_connection(
            conn,
            authorization=authorization,
            plan=plan,
            research_id=research_id,
            block_id=block_id,
            candidates=candidates,
        )

    persisted_authorization = get_r02_authorization(
        str(authorization["authorization_id"]),
        path=path,
    )
    block = get_r02_discovery_block(research_id, path=path)
    if persisted_authorization is None or block is None:
        raise RuntimeError("R02_ATOMIC_FREEZE_PERSISTENCE_MISSING")
    if len(block["candidates"]) != int(plan["candidate_count"]):
        raise RuntimeError("R02_DISCOVERY_BLOCK_CANDIDATE_PERSISTENCE_MISMATCH")
    return persisted_authorization, block



def get_r02_terminal(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    migrate_current(path)
    with connect(path) as conn:
        row = conn.execute(
            """
            SELECT terminal.*
            FROM research_r02_block_terminals AS terminal
            JOIN research_r02_discovery_blocks AS block
              ON block.block_id=terminal.block_id
            WHERE block.research_id=?
            """,
            (str(research_id),),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["compute_consumed"] = json.loads(
        result.pop("compute_consumed_json")
    )
    return result


def get_r02_outcome_ledger(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    migrate_current(path)
    block = get_r02_discovery_block(research_id, path=path)
    if block is None:
        return None
    with connect(path) as conn:
        rows = conn.execute(
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
    outcomes = []
    for row in rows:
        item = dict(row)
        item["outcome"] = json.loads(item.pop("outcome_json"))
        outcomes.append(item)
    return {
        "block": block,
        "outcomes": outcomes,
        "terminal": get_r02_terminal(research_id, path=path),
    }


def validate_r02_discovery_block_integrity(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_current(path)
    research_id = str(research_id or "").strip()
    if not research_id:
        raise ValueError("R02_INTEGRITY_RESEARCH_ID_REQUIRED")

    with connect(path) as conn:
        block_row = conn.execute(
            "SELECT * FROM research_r02_discovery_blocks WHERE research_id=?",
            (research_id,),
        ).fetchone()
        if block_row is None:
            raise RuntimeError("R02_INTEGRITY_BLOCK_REQUIRED")
        auth_row = conn.execute(
            "SELECT * FROM research_r02_authorizations WHERE authorization_id=?",
            (str(block_row["authorization_id"]),),
        ).fetchone()
        if auth_row is None:
            raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_MISSING")
        candidate_rows = conn.execute(
            """
            SELECT * FROM research_r02_candidate_specs
            WHERE block_id=?
            ORDER BY ordinal,candidate_id
            """,
            (str(block_row["block_id"]),),
        ).fetchall()

    block = dict(block_row)
    authorization = _decode_authorization(auth_row)
    payload = authorization["payload"]
    try:
        _validate_authorization_record(
            {
                "authorization_id": authorization["authorization_id"],
                "research_id": authorization["research_id"],
                "confirmed": authorization["confirmed"],
                "payload_sha256": authorization["payload_sha256"],
                "payload": payload,
                "authorized_utc": authorization["authorized_utc"],
            }
        )
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_INVALID") from exc

    if str(block["research_id"]) != research_id:
        raise RuntimeError("R02_INTEGRITY_BLOCK_RESEARCH_MISMATCH")
    if str(authorization["research_id"]) != research_id:
        raise RuntimeError("R02_INTEGRITY_AUTHORIZATION_RESEARCH_MISMATCH")
    if str(block["state"]) != "FROZEN_WAITING_EXECUTION":
        raise RuntimeError("R02_INTEGRITY_BLOCK_STATE_INVALID")

    block_body = {
        "research_id": research_id,
        "authorization_id": str(block["authorization_id"]),
        "r01_output_manifest_sha256": str(block["r01_output_manifest_sha256"]),
        "plan_id": str(block["plan_id"]),
        "plan_sha256": str(block["plan_sha256"]),
    }
    expected_block_id = "RDISC-" + stable_hash(block_body)[:24]
    if str(block["block_id"]) != expected_block_id:
        raise RuntimeError("R02_INTEGRITY_BLOCK_ID_MISMATCH")

    if str(payload.get("r01_output_manifest_sha256") or "") != str(
        block["r01_output_manifest_sha256"]
    ):
        raise RuntimeError("R02_INTEGRITY_R01_OUTPUT_MISMATCH")
    if str(payload.get("plan_id") or "") != str(block["plan_id"]):
        raise RuntimeError("R02_INTEGRITY_PLAN_ID_MISMATCH")
    if str(payload.get("plan_sha256") or "") != str(block["plan_sha256"]):
        raise RuntimeError("R02_INTEGRITY_PLAN_SHA_MISMATCH")

    try:
        compute_budget = json.loads(str(block["compute_budget_json"]))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("R02_INTEGRITY_COMPUTE_BUDGET_INVALID") from exc
    if compute_budget != payload.get("compute_budget"):
        raise RuntimeError("R02_INTEGRITY_COMPUTE_BUDGET_MISMATCH")

    expected_count = int(block["candidate_count"])
    if expected_count <= 0 or len(candidate_rows) != expected_count:
        raise RuntimeError("R02_INTEGRITY_CANDIDATE_COUNT_MISMATCH")
    if int(payload.get("candidate_count") or 0) != expected_count:
        raise RuntimeError("R02_INTEGRITY_AUTH_CANDIDATE_COUNT_MISMATCH")

    candidates: list[dict[str, Any]] = []
    candidate_ids: list[str] = []
    for ordinal, row in enumerate(candidate_rows):
        item = dict(row)
        if int(item["ordinal"]) != ordinal:
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_ORDINAL_MISMATCH")
        if str(item["block_id"]) != str(block["block_id"]):
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_BLOCK_MISMATCH")
        try:
            spec = json.loads(str(item["spec_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_SPEC_JSON_INVALID") from exc
        if not isinstance(spec, dict):
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_SPEC_INVALID")
        expected_spec_sha = stable_hash(spec)
        if str(item["spec_sha256"]) != expected_spec_sha:
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_SPEC_SHA_MISMATCH")
        expected_candidate_id = derive_candidate_id(spec)
        if str(item["candidate_id"]) != expected_candidate_id:
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_ID_MISMATCH")
        if str(item["model_family"]) != str(spec.get("model_family") or ""):
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_FAMILY_MISMATCH")
        if int(item["seed"]) != int(spec.get("seed")):
            raise RuntimeError("R02_INTEGRITY_CANDIDATE_SEED_MISMATCH")
        candidate_ids.append(str(item["candidate_id"]))
        item["spec"] = spec
        candidates.append(item)

    if candidate_ids != list(payload.get("candidate_ids") or []):
        raise RuntimeError("R02_INTEGRITY_AUTH_CANDIDATE_IDS_MISMATCH")

    return {
        "status": "VERIFIED",
        "research_id": research_id,
        "authorization": authorization,
        "block": {
            **block,
            "compute_budget": compute_budget,
        },
        "candidates": candidates,
        "candidate_ids": candidate_ids,
    }


def validate_r02_outcome_integrity(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    authority = validate_r02_discovery_block_integrity(
        research_id,
        path=path,
    )
    block = authority["block"]
    candidate_ids = authority["candidate_ids"]

    with connect(path) as conn:
        outcome_rows = conn.execute(
            """
            SELECT outcome.*, spec.ordinal
            FROM research_r02_candidate_outcomes AS outcome
            JOIN research_r02_candidate_specs AS spec
              ON spec.candidate_id=outcome.candidate_id
            WHERE outcome.block_id=?
            ORDER BY spec.ordinal,spec.candidate_id
            """,
            (str(block["block_id"]),),
        ).fetchall()
        terminal_row = conn.execute(
            "SELECT * FROM research_r02_block_terminals WHERE block_id=?",
            (str(block["block_id"]),),
        ).fetchone()

    if terminal_row is None:
        if outcome_rows:
            raise RuntimeError("R02_INTEGRITY_PARTIAL_OUTCOME_STATE")
        return {
            "status": "VERIFIED",
            "terminal_state": "NOT_COMMITTED",
            "research_id": research_id,
            "block_id": block["block_id"],
            "candidate_count": len(candidate_ids),
            "outcome_count": 0,
        }

    if len(outcome_rows) != len(candidate_ids):
        raise RuntimeError("R02_INTEGRITY_OUTCOME_COUNT_MISMATCH")

    outcome_requests: list[dict[str, Any]] = []
    reconstructed_outcomes: list[dict[str, Any]] = []
    for ordinal, row in enumerate(outcome_rows):
        item = dict(row)
        expected_candidate_id = candidate_ids[ordinal]
        if int(item["ordinal"]) != ordinal:
            raise RuntimeError("R02_INTEGRITY_OUTCOME_ORDINAL_MISMATCH")
        if str(item["block_id"]) != str(block["block_id"]):
            raise RuntimeError("R02_INTEGRITY_OUTCOME_BLOCK_MISMATCH")
        if str(item["candidate_id"]) != expected_candidate_id:
            raise RuntimeError("R02_INTEGRITY_OUTCOME_CANDIDATE_MISMATCH")
        try:
            payload = json.loads(str(item["outcome_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("R02_INTEGRITY_OUTCOME_JSON_INVALID") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("R02_INTEGRITY_OUTCOME_PAYLOAD_INVALID")

        request = {
            "candidate_id": payload.get("candidate_id"),
            "status": payload.get("status"),
            "metrics": payload.get("metrics"),
            "compute_consumed": payload.get("compute_consumed"),
            "failure_code": payload.get("failure_code"),
        }
        try:
            rebuilt = build_candidate_outcome(
                request,
                block_id=str(block["block_id"]),
                candidate_ids=candidate_ids,
                budget_unit=str(block["compute_budget"]["unit"]),
            )
        except ValueError as exc:
            raise RuntimeError("R02_INTEGRITY_OUTCOME_REBUILD_FAILED") from exc

        if rebuilt != payload:
            raise RuntimeError("R02_INTEGRITY_OUTCOME_PAYLOAD_MISMATCH")
        if str(item["outcome_id"]) != str(rebuilt["outcome_id"]):
            raise RuntimeError("R02_INTEGRITY_OUTCOME_ID_MISMATCH")
        if str(item["outcome_sha256"]) != str(rebuilt["outcome_sha256"]):
            raise RuntimeError("R02_INTEGRITY_OUTCOME_SHA_MISMATCH")
        if str(item["status"]) != str(rebuilt["status"]):
            raise RuntimeError("R02_INTEGRITY_OUTCOME_STATUS_MISMATCH")
        outcome_requests.append(request)
        reconstructed_outcomes.append(rebuilt)

    try:
        terminal = build_terminal_manifest(
            block_id=str(block["block_id"]),
            candidate_ids=candidate_ids,
            compute_budget=block["compute_budget"],
            outcome_requests=outcome_requests,
        )
    except ValueError as exc:
        raise RuntimeError("R02_INTEGRITY_TERMINAL_REBUILD_FAILED") from exc

    stored_terminal = dict(terminal_row)
    try:
        stored_compute = json.loads(
            str(stored_terminal["compute_consumed_json"])
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("R02_INTEGRITY_TERMINAL_COMPUTE_INVALID") from exc

    terminal_checks = {
        "terminal_id": str(terminal["terminal_id"]),
        "block_id": str(block["block_id"]),
        "state": str(terminal["state"]),
        "outcome_manifest_sha256": str(
            terminal["outcome_manifest_sha256"]
        ),
        "candidate_count": int(terminal["candidate_count"]),
        "screen_pass_count": int(terminal["screen_pass_count"]),
        "screen_fail_count": int(terminal["screen_fail_count"]),
        "execution_error_count": int(terminal["execution_error_count"]),
    }
    for key, expected in terminal_checks.items():
        actual = stored_terminal[key]
        if isinstance(expected, int):
            actual = int(actual)
        else:
            actual = str(actual)
        if actual != expected:
            raise RuntimeError(
                "R02_INTEGRITY_TERMINAL_FIELD_MISMATCH:" + key
            )
    if stored_compute != terminal["compute_consumed"]:
        raise RuntimeError("R02_INTEGRITY_TERMINAL_COMPUTE_MISMATCH")

    return {
        "status": "VERIFIED",
        "terminal_state": "COMPLETE_WAITING_OWNER",
        "research_id": research_id,
        "block_id": block["block_id"],
        "candidate_count": len(candidate_ids),
        "outcome_count": len(reconstructed_outcomes),
        "outcome_manifest_sha256": terminal[
            "outcome_manifest_sha256"
        ],
        "screen_pass_count": terminal["screen_pass_count"],
        "screen_fail_count": terminal["screen_fail_count"],
        "execution_error_count": terminal["execution_error_count"],
        "compute_consumed": terminal["compute_consumed"],
    }


def commit_r02_terminal_outcomes(
    research_id: str,
    outcome_requests: list[dict[str, Any]],
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_current(path)
    research_id = str(research_id or "").strip()
    if not research_id:
        raise ValueError("R02_OUTCOME_RESEARCH_ID_REQUIRED")

    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        block = conn.execute(
            "SELECT * FROM research_r02_discovery_blocks WHERE research_id=?",
            (research_id,),
        ).fetchone()
        if block is None:
            raise RuntimeError("R02_FROZEN_BLOCK_REQUIRED")

        candidates = conn.execute(
            """
            SELECT candidate_id
            FROM research_r02_candidate_specs
            WHERE block_id=?
            ORDER BY ordinal,candidate_id
            """,
            (str(block["block_id"]),),
        ).fetchall()
        candidate_ids = [str(row["candidate_id"]) for row in candidates]
        if len(candidate_ids) != int(block["candidate_count"]):
            raise RuntimeError("R02_FROZEN_CANDIDATE_AUTHORITY_INCOMPLETE")

        manifest = build_terminal_manifest(
            block_id=str(block["block_id"]),
            candidate_ids=candidate_ids,
            compute_budget=json.loads(str(block["compute_budget_json"])),
            outcome_requests=outcome_requests,
        )

        existing_terminal = conn.execute(
            "SELECT * FROM research_r02_block_terminals WHERE block_id=?",
            (str(block["block_id"]),),
        ).fetchone()
        if existing_terminal is not None:
            if str(existing_terminal["outcome_manifest_sha256"]) != str(
                manifest["outcome_manifest_sha256"]
            ):
                raise RuntimeError("R02_TERMINAL_ALREADY_COMMITTED")
        else:
            existing_outcomes = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM research_r02_candidate_outcomes
                WHERE block_id=?
                """,
                (str(block["block_id"]),),
            ).fetchone()
            if int(existing_outcomes["n"]) != 0:
                raise RuntimeError("R02_OUTCOME_PARTIAL_STATE_DETECTED")

            now = utc_now()
            for outcome in manifest["candidate_outcomes"]:
                conn.execute(
                    """
                    INSERT INTO research_r02_candidate_outcomes(
                        outcome_id,block_id,candidate_id,status,
                        outcome_sha256,outcome_json,created_utc
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        str(outcome["outcome_id"]),
                        str(block["block_id"]),
                        str(outcome["candidate_id"]),
                        str(outcome["status"]),
                        str(outcome["outcome_sha256"]),
                        json.dumps(
                            outcome,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        now,
                    ),
                )
            conn.execute(
                """
                INSERT INTO research_r02_block_terminals(
                    terminal_id,block_id,state,outcome_manifest_sha256,
                    candidate_count,screen_pass_count,screen_fail_count,
                    execution_error_count,compute_consumed_json,created_utc
                ) VALUES(?,?,'COMPLETE_WAITING_OWNER',?,?,?,?,?,?,?)
                """,
                (
                    str(manifest["terminal_id"]),
                    str(block["block_id"]),
                    str(manifest["outcome_manifest_sha256"]),
                    int(manifest["candidate_count"]),
                    int(manifest["screen_pass_count"]),
                    int(manifest["screen_fail_count"]),
                    int(manifest["execution_error_count"]),
                    json.dumps(
                        manifest["compute_consumed"],
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                ),
            )

    integrity = validate_r02_outcome_integrity(
        research_id,
        path=path,
    )
    if integrity["terminal_state"] != "COMPLETE_WAITING_OWNER":
        raise RuntimeError("R02_TERMINAL_PERSISTENCE_MISSING")
    ledger = get_r02_outcome_ledger(research_id, path=path)
    if ledger is None or ledger["terminal"] is None:
        raise RuntimeError("R02_TERMINAL_PERSISTENCE_MISSING")
    return ledger
