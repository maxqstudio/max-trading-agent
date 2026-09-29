from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH
from .db import connect
from .optimizer_store import utc_now
from .research_contract import candidate_id as derive_candidate_id, stable_hash
from .research_r02_contract import build_discovery_plan
from .research_r02_outcome import build_candidate_outcome, build_terminal_manifest
from .workflow_store import migrate_current


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _load_persisted_json(raw: Any, *, component: str) -> Any:
    prefix = "R02_LEDGER_INTEGRITY_" + component
    if not isinstance(raw, str):
        raise RuntimeError(prefix + "_JSON_INVALID")
    try:
        value = json.loads(raw, parse_constant=_reject_json_constant)
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (json.JSONDecodeError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(prefix + "_JSON_INVALID") from exc
    if encoded != raw:
        raise RuntimeError(prefix + "_JSON_NOT_CANONICAL")
    return value


def _decode_authorization(row: Any) -> dict[str, Any]:
    result = dict(row)
    if result["confirmed"] != 1:
        raise RuntimeError("R02_LEDGER_INTEGRITY_AUTHORIZATION_CONFIRMATION_INVALID")
    result["confirmed"] = True
    payload = _load_persisted_json(
        result.pop("payload_json"),
        component="AUTHORIZATION",
    )
    if not isinstance(payload, dict):
        raise RuntimeError("R02_LEDGER_INTEGRITY_AUTHORIZATION_PAYLOAD_INVALID")
    result["payload"] = payload
    return result


def get_r02_authorization(
    authorization_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    try:
        migrate_current(path)
        with connect(path) as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT * FROM research_r02_authorizations WHERE authorization_id=?",
                (str(authorization_id),),
            ).fetchone()
            if row is None:
                block = conn.execute(
                    "SELECT research_id FROM research_r02_discovery_blocks "
                    "WHERE authorization_id=?",
                    (str(authorization_id),),
                ).fetchone()
                if block is None:
                    return None
                _read_validated_r02_ledger(conn, str(block["research_id"]))
                raise RuntimeError("R02_LEDGER_INTEGRITY_AUTHORIZATION_MISSING")
            ledger = _read_validated_r02_ledger(conn, str(row["research_id"]))
    except RuntimeError as exc:
        if str(exc).startswith("R02_LEDGER_INTEGRITY_"):
            raise
        raise RuntimeError("R02_LEDGER_INTEGRITY_READ_FAILED") from exc
    except Exception as exc:
        raise RuntimeError("R02_LEDGER_INTEGRITY_READ_FAILED") from exc
    if ledger is None or ledger["authorization"]["authorization_id"] != str(
        authorization_id
    ):
        raise RuntimeError("R02_LEDGER_INTEGRITY_AUTHORIZATION_IDENTITY_MISMATCH")
    return ledger["authorization"]


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
            _validate_authorization_record(decoded)
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
            )
        )
    created = deepcopy(record)
    created["payload"] = payload
    return created


def get_r02_discovery_block(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    ledger = _read_r02_ledger_snapshot(research_id, path=path)
    return ledger["block"] if ledger is not None else None


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


def _read_validated_r02_ledger(
    conn: Any,
    research_id: str,
) -> dict[str, Any] | None:
    authorization_row = conn.execute(
        "SELECT * FROM research_r02_authorizations WHERE research_id=?",
        (research_id,),
    ).fetchone()
    block_row = conn.execute(
        "SELECT * FROM research_r02_discovery_blocks WHERE research_id=?",
        (research_id,),
    ).fetchone()
    if authorization_row is None and block_row is None:
        orphaned_rows = conn.execute(
            """
            SELECT
                EXISTS(
                    SELECT 1
                    FROM research_r02_candidate_specs AS candidate
                    LEFT JOIN research_r02_discovery_blocks AS block
                      ON block.block_id=candidate.block_id
                    WHERE block.block_id IS NULL
                )
                OR EXISTS(
                    SELECT 1
                    FROM research_r02_candidate_outcomes AS outcome
                    LEFT JOIN research_r02_discovery_blocks AS block
                      ON block.block_id=outcome.block_id
                    WHERE block.block_id IS NULL
                )
                OR EXISTS(
                    SELECT 1
                    FROM research_r02_block_terminals AS terminal
                    LEFT JOIN research_r02_discovery_blocks AS block
                      ON block.block_id=terminal.block_id
                    WHERE block.block_id IS NULL
                )
            """
        ).fetchone()
        if orphaned_rows is not None and orphaned_rows[0]:
            raise RuntimeError("R02_LEDGER_INTEGRITY_ORPHANED_ROWS")
        return None
    if authorization_row is None:
        raise RuntimeError("R02_LEDGER_INTEGRITY_AUTHORIZATION_MISSING")
    if block_row is None:
        raise RuntimeError("R02_LEDGER_INTEGRITY_FROZEN_BLOCK_MISSING")

    try:
        authorization = _decode_authorization(authorization_row)
        payload, _encoded_payload = _validate_authorization_record(authorization)
    except (KeyError, TypeError, OverflowError, ValueError, RuntimeError) as exc:
        raise RuntimeError("R02_LEDGER_INTEGRITY_AUTHORIZATION_INVALID") from exc

    block = dict(block_row)
    if (
        str(authorization["research_id"]) != research_id
        or str(block.get("research_id") or "") != research_id
        or str(block.get("authorization_id") or "")
        != str(authorization["authorization_id"])
        or block.get("state") != "FROZEN_WAITING_EXECUTION"
    ):
        raise RuntimeError("R02_LEDGER_INTEGRITY_BLOCK_BINDING_MISMATCH")

    try:
        candidate_count = block["candidate_count"]
        if type(candidate_count) is not int or candidate_count <= 0:
            raise ValueError("candidate_count")
        payload_count = payload.get("candidate_count")
        payload_candidate_ids = payload.get("candidate_ids")
        if (
            type(payload_count) is not int
            or payload_count != candidate_count
            or not isinstance(payload_candidate_ids, list)
            or len(payload_candidate_ids) != candidate_count
            or any(not isinstance(item, str) or not item for item in payload_candidate_ids)
            or len(set(payload_candidate_ids)) != candidate_count
        ):
            raise ValueError("authorized candidate universe")

        compute_budget = _load_persisted_json(
            block["compute_budget_json"],
            component="BLOCK_COMPUTE_BUDGET",
        )
        if not isinstance(compute_budget, dict):
            raise ValueError("compute budget")

        persisted_block_id = str(block["block_id"])
        block_r01_output_sha = str(block["r01_output_manifest_sha256"])
        block_plan_id = str(block["plan_id"])
        block_plan_sha = str(block["plan_sha256"])
        if not persisted_block_id:
            raise ValueError("block id")

        candidate_rows = conn.execute(
            """
            SELECT * FROM research_r02_candidate_specs
            WHERE block_id=?
            ORDER BY ordinal,candidate_id
            """,
            (persisted_block_id,),
        ).fetchall()
        if len(candidate_rows) != candidate_count:
            raise ValueError("candidate count")

        candidates: list[dict[str, Any]] = []
        for row in candidate_rows:
            item = dict(row)
            spec = _load_persisted_json(
                item["spec_json"],
                component="CANDIDATE_SPEC",
            )
            if not isinstance(spec, dict):
                raise ValueError("candidate spec")
            if str(item["block_id"]) != persisted_block_id:
                raise ValueError("candidate block binding")
            if stable_hash(spec) != str(item["spec_sha256"]):
                raise ValueError("candidate spec hash")
            if derive_candidate_id(spec) != str(item["candidate_id"]):
                raise ValueError("candidate identity")
            if (
                not isinstance(spec.get("model_family"), str)
                or item["model_family"] != spec["model_family"]
                or type(spec.get("seed")) is not int
                or type(item["seed"]) is not int
                or item["seed"] != spec["seed"]
                or type(item["ordinal"]) is not int
            ):
                raise ValueError("candidate row binding")
            candidates.append(
                {
                    "row": item,
                    "spec": spec,
                }
            )

        ordinals = [item["row"]["ordinal"] for item in candidates]
        if ordinals != list(range(candidate_count)):
            raise ValueError("candidate ordinal order")
        candidate_ids = [str(item["row"]["candidate_id"]) for item in candidates]
        if len(set(candidate_ids)) != candidate_count:
            raise ValueError("duplicate candidate id")

        first_spec = candidates[0]["spec"]
        plan_request = {
            "research_id": research_id,
            "r01_output_manifest_sha256": block_r01_output_sha,
            "feature_contract": first_spec.get("feature_contract"),
            "label_contract": first_spec.get("label_contract"),
            "parent_lineage": first_spec.get("parent_lineage"),
            "candidate_count": candidate_count,
            "compute_budget": {
                "value": compute_budget.get("value"),
                "unit": compute_budget.get("unit"),
            },
            "candidates": [item["spec"] for item in candidates],
        }
        plan = build_discovery_plan(plan_request)
        identity_research_id, expected_block_id, _frozen_candidates = (
            _validated_freeze_identity(
                authorization=authorization,
                plan=plan,
            )
        )
        if (
            identity_research_id != research_id
            or expected_block_id != persisted_block_id
            or plan["plan_id"] != block_plan_id
            or plan["plan_sha256"] != block_plan_sha
            or plan["candidate_ids"] != candidate_ids
            or payload_candidate_ids != plan["candidate_ids"]
            or payload["compute_budget"] != plan["compute_budget"]
            or compute_budget != plan["compute_budget"]
        ):
            raise ValueError("reconstructed plan mismatch")

        outcome_rows = conn.execute(
            """
            SELECT * FROM research_r02_candidate_outcomes
            WHERE block_id=?
            ORDER BY candidate_id,outcome_id
            """,
            (expected_block_id,),
        ).fetchall()
        terminal_rows = conn.execute(
            "SELECT * FROM research_r02_block_terminals WHERE block_id=?",
            (expected_block_id,),
        ).fetchall()
        if len(terminal_rows) > 1:
            raise ValueError("duplicate terminal")
        if outcome_rows and len(outcome_rows) != candidate_count:
            raise ValueError("partial outcome rows")
        if not terminal_rows and outcome_rows:
            raise ValueError("outcomes without terminal")
        if terminal_rows and len(outcome_rows) != candidate_count:
            raise ValueError("terminal without exact outcomes")

        outcome_by_candidate: dict[str, dict[str, Any]] = {}
        outcome_requests: list[dict[str, Any]] = []
        for row in outcome_rows:
            item = dict(row)
            outcome = _load_persisted_json(
                item["outcome_json"],
                component="OUTCOME",
            )
            if not isinstance(outcome, dict):
                raise ValueError("outcome object")
            candidate_id = str(item["candidate_id"])
            if (
                candidate_id not in candidate_ids
                or candidate_id in outcome_by_candidate
                or str(item["block_id"]) != expected_block_id
            ):
                raise ValueError("outcome identity binding")
            request = {
                "candidate_id": outcome["candidate_id"],
                "status": outcome["status"],
                "metrics": outcome["metrics"],
                "compute_consumed": outcome["compute_consumed"],
                "failure_code": outcome["failure_code"],
            }
            expected_outcome = build_candidate_outcome(
                request,
                block_id=expected_block_id,
                candidate_ids=candidate_ids,
                budget_unit=str(plan["compute_budget"]["unit"]),
            )
            if (
                outcome != expected_outcome
                or str(item["outcome_id"]) != expected_outcome["outcome_id"]
                or str(item["outcome_sha256"])
                != expected_outcome["outcome_sha256"]
                or str(item["status"]) != expected_outcome["status"]
                or str(outcome.get("block_id") or "") != expected_block_id
                or candidate_id != expected_outcome["candidate_id"]
            ):
                raise ValueError("outcome reconstruction mismatch")
            item.pop("outcome_json")
            item["outcome"] = expected_outcome
            outcome_by_candidate[candidate_id] = item
            outcome_requests.append(request)

        if outcome_by_candidate and set(outcome_by_candidate) != set(candidate_ids):
            raise ValueError("outcome candidate universe")
        outcomes = [outcome_by_candidate[item] for item in candidate_ids] if outcome_by_candidate else []

        terminal: dict[str, Any] | None = None
        if terminal_rows:
            manifest = build_terminal_manifest(
                block_id=expected_block_id,
                candidate_ids=candidate_ids,
                compute_budget=plan["compute_budget"],
                outcome_requests=outcome_requests,
            )
            terminal_candidates = conn.execute(
                """
                SELECT * FROM research_r02_block_terminals
                WHERE block_id=? OR terminal_id=?
                """,
                (expected_block_id, manifest["terminal_id"]),
            ).fetchall()
            if len(terminal_candidates) != 1:
                raise ValueError("terminal identity")
            item = dict(terminal_candidates[0])
            compute_consumed = _load_persisted_json(
                item["compute_consumed_json"],
                component="TERMINAL_COMPUTE_CONSUMED",
            )
            if (
                not isinstance(compute_consumed, dict)
                or str(item["terminal_id"]) != manifest["terminal_id"]
                or str(item["block_id"]) != expected_block_id
                or str(item["state"]) != manifest["state"]
                or str(item["outcome_manifest_sha256"])
                != manifest["outcome_manifest_sha256"]
                or type(item["candidate_count"]) is not int
                or item["candidate_count"] != manifest["candidate_count"]
                or type(item["screen_pass_count"]) is not int
                or item["screen_pass_count"] != manifest["screen_pass_count"]
                or type(item["screen_fail_count"]) is not int
                or item["screen_fail_count"] != manifest["screen_fail_count"]
                or type(item["execution_error_count"]) is not int
                or item["execution_error_count"]
                != manifest["execution_error_count"]
                or compute_consumed != manifest["compute_consumed"]
            ):
                raise ValueError("terminal reconstruction mismatch")
            item.pop("compute_consumed_json")
            item["compute_consumed"] = compute_consumed
            terminal = item

    except RuntimeError as exc:
        if str(exc).startswith("R02_LEDGER_INTEGRITY_"):
            raise
        raise RuntimeError("R02_LEDGER_INTEGRITY_RECONSTRUCTION_FAILED") from exc
    except (KeyError, TypeError, OverflowError, ValueError) as exc:
        raise RuntimeError("R02_LEDGER_INTEGRITY_RECONSTRUCTION_FAILED") from exc

    authorization["payload"] = payload
    block_result = dict(block)
    block_result.pop("compute_budget_json")
    block_result["compute_budget"] = compute_budget
    block_result["candidates"] = []
    for candidate in candidates:
        row = dict(candidate["row"])
        row.pop("spec_json")
        row["spec"] = candidate["spec"]
        block_result["candidates"].append(row)
    return {
        "integrity_status": "VERIFIED",
        "authorization": authorization,
        "block": block_result,
        "outcomes": outcomes,
        "terminal": terminal,
    }


def validate_r02_outcome_ledger_integrity(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    return _read_r02_ledger_snapshot(research_id, path=path)


def _read_r02_ledger_snapshot(
    research_id: str,
    *,
    path: Path,
) -> dict[str, Any] | None:
    try:
        migrate_current(path)
        with connect(path) as conn:
            conn.execute("BEGIN")
            return _read_validated_r02_ledger(conn, str(research_id))
    except RuntimeError as exc:
        if str(exc).startswith("R02_LEDGER_INTEGRITY_"):
            raise
        raise RuntimeError("R02_LEDGER_INTEGRITY_READ_FAILED") from exc
    except Exception as exc:
        raise RuntimeError("R02_LEDGER_INTEGRITY_READ_FAILED") from exc


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

    ledger = get_r02_outcome_ledger(research_id, path=path)
    if ledger is None:
        raise RuntimeError("R02_ATOMIC_FREEZE_PERSISTENCE_MISSING")
    if len(ledger["block"]["candidates"]) != int(plan["candidate_count"]):
        raise RuntimeError("R02_DISCOVERY_BLOCK_CANDIDATE_PERSISTENCE_MISMATCH")
    return ledger["authorization"], ledger["block"]



def get_r02_terminal(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    ledger = _read_r02_ledger_snapshot(research_id, path=path)
    return ledger["terminal"] if ledger is not None else None


def get_r02_outcome_ledger(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    return _read_r02_ledger_snapshot(research_id, path=path)


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

    ledger = get_r02_outcome_ledger(research_id, path=path)
    if ledger is None or ledger["terminal"] is None:
        raise RuntimeError("R02_TERMINAL_PERSISTENCE_MISSING")
    if len(ledger["outcomes"]) != int(ledger["block"]["candidate_count"]):
        raise RuntimeError("R02_OUTCOME_LEDGER_INCOMPLETE")
    return ledger
