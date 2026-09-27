from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH
from .db import connect
from .optimizer_store import utc_now
from .research_contract import (
    RESEARCH_GATES,
    RESEARCH_SCHEMA,
    RESEARCH_STATES,
    R00_TERMINAL_STATES,
    memory_adaptive_eligible,
    memory_learning_zone,
    stable_hash,
)
from .workflow_store import migrate_current

_JSON_COLUMNS = (
    "label_authority_json",
    "candidate_identity_contract_json",
    "artifact_lineage_contract_json",
    "research_policy_json",
    "unresolved_authority_json",
    "candidate_ids_json",
    "qualification_states_json",
    "system_recommendation_json",
    "owner_selected_ids_json",
)


def migrate_r00(path: Path = DATABASE_PATH) -> None:
    migrate_current(path)


def _decode_research(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in _JSON_COLUMNS:
        raw = result.pop(key)
        result[key.removesuffix("_json")] = json.loads(raw) if raw else None
    for key in ("training_count", "onnx_count", "research_challenger_count"):
        result[key] = int(result[key])
    return result


def create_authorization(
    payload: dict[str, Any],
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_r00(path)
    required = {
        "authorization_id",
        "gate",
        "action",
        "confirmed",
        "expected_parent_strategy_id",
        "expected_parent_authority_sha256",
        "cumulative_strategy_e2e_authority",
        "h1_minimum_trades_per_month",
        "payload_sha256",
        "authorized_utc",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError("RESEARCH_AUTHORIZATION_FIELDS_MISSING:" + ",".join(missing))
    if str(payload["gate"]) not in RESEARCH_GATES:
        raise ValueError("RESEARCH_AUTHORIZATION_GATE_INVALID")
    encoded = {
        "authorization_id": str(payload["authorization_id"]),
        "gate": str(payload["gate"]),
        "action": str(payload["action"]),
        "confirmed": 1 if bool(payload["confirmed"]) else 0,
        "expected_parent_strategy_id": str(payload["expected_parent_strategy_id"]),
        "expected_parent_authority_sha256": str(payload["expected_parent_authority_sha256"]),
        "cumulative_strategy_e2e_authority": str(payload["cumulative_strategy_e2e_authority"]),
        "h1_minimum_trades_per_month": int(payload["h1_minimum_trades_per_month"]),
        "payload_sha256": str(payload["payload_sha256"]),
        "authorized_utc": str(payload["authorized_utc"]),
    }
    with connect(path) as conn:
        existing = conn.execute(
            "SELECT * FROM research_authorizations WHERE authorization_id=?",
            (encoded["authorization_id"],),
        ).fetchone()
        if existing is not None:
            current = dict(existing)
            identity_keys = (
                "authorization_id",
                "gate",
                "action",
                "confirmed",
                "expected_parent_strategy_id",
                "expected_parent_authority_sha256",
                "cumulative_strategy_e2e_authority",
                "h1_minimum_trades_per_month",
                "payload_sha256",
            )
            if any(current[key] != encoded[key] for key in identity_keys):
                raise RuntimeError("RESEARCH_AUTHORIZATION_IDENTITY_COLLISION")
            return current
        conn.execute(
            """
            INSERT INTO research_authorizations(
                authorization_id,gate,action,confirmed,
                expected_parent_strategy_id,expected_parent_authority_sha256,
                cumulative_strategy_e2e_authority,h1_minimum_trades_per_month,
                payload_sha256,authorized_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            tuple(encoded[key] for key in (
                "authorization_id",
                "gate",
                "action",
                "confirmed",
                "expected_parent_strategy_id",
                "expected_parent_authority_sha256",
                "cumulative_strategy_e2e_authority",
                "h1_minimum_trades_per_month",
                "payload_sha256",
                "authorized_utc",
            )),
        )
    return encoded


def get_authorization(
    authorization_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    migrate_r00(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM research_authorizations WHERE authorization_id=?",
            (str(authorization_id),),
        ).fetchone()
    return dict(row) if row is not None else None


def create_research(
    payload: dict[str, Any],
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_r00(path)
    research_id = str(payload["research_id"])
    state = str(payload["gate_state"])
    gate = str(payload["current_gate"])
    if state not in RESEARCH_STATES:
        raise ValueError("RESEARCH_GATE_STATE_INVALID")
    if gate not in RESEARCH_GATES:
        raise ValueError("RESEARCH_GATE_INVALID")
    if gate == "R00" and state != "STARTING":
        raise RuntimeError("R00_INITIAL_STATE_MUST_BE_STARTING")
    now = str(payload.get("created_utc") or utc_now())
    values = {
        "research_id": research_id,
        "research_parent_id": str(payload["research_parent_id"]),
        "parent_strategy_id": str(payload["parent_strategy_id"]),
        "parent_authority_sha256": str(payload["parent_authority_sha256"]),
        "parent_manifest_path": str(payload["parent_manifest_path"]),
        "parent_manifest_sha256": str(payload["parent_manifest_sha256"]),
        "feature_contract": str(payload["feature_contract"]),
        "current_gate": gate,
        "gate_state": state,
        "gate_input_manifest_sha": payload.get("gate_input_manifest_sha"),
        "gate_output_manifest_sha": payload.get("gate_output_manifest_sha"),
        "owner_authorization_id": str(payload["owner_authorization_id"]),
        "authorized_utc": str(payload["authorized_utc"]),
        "hardware_snapshot_path": str(payload["hardware_snapshot_path"]),
        "hardware_snapshot_sha256": str(payload["hardware_snapshot_sha256"]),
        "label_authority_json": json.dumps(payload["label_authority"], sort_keys=True),
        "candidate_identity_contract_json": json.dumps(payload["candidate_identity_contract"], sort_keys=True),
        "artifact_lineage_contract_json": json.dumps(payload["artifact_lineage_contract"], sort_keys=True),
        "research_policy_json": json.dumps(payload["research_policy"], sort_keys=True),
        "unresolved_authority_json": json.dumps(payload["unresolved_authority"], sort_keys=True),
        "candidate_ids_json": json.dumps(payload.get("candidate_ids") or [], sort_keys=True),
        "qualification_states_json": json.dumps(payload.get("qualification_states") or {}, sort_keys=True),
        "system_recommendation_json": (
            json.dumps(payload["system_recommendation"], sort_keys=True)
            if payload.get("system_recommendation") is not None
            else None
        ),
        "owner_selected_ids_json": json.dumps(payload.get("owner_selected_ids") or [], sort_keys=True),
        "training_count": int(payload.get("training_count", 0)),
        "onnx_count": int(payload.get("onnx_count", 0)),
        "research_challenger_count": int(payload.get("research_challenger_count", 0)),
        "champion_mutation": "NONE",
        "created_utc": now,
        "updated_utc": now,
    }
    columns = tuple(values)
    placeholders = ",".join("?" for _ in columns)
    with connect(path) as conn:
        existing = conn.execute(
            "SELECT * FROM research_projects WHERE research_id=?",
            (research_id,),
        ).fetchone()
        if existing is not None:
            decoded = _decode_research(existing)
            if (
                decoded is None
                or decoded["research_parent_id"] != values["research_parent_id"]
                or decoded["parent_manifest_sha256"] != values["parent_manifest_sha256"]
            ):
                raise RuntimeError("RESEARCH_IDENTITY_COLLISION")
            return decoded
        conn.execute(
            f"INSERT INTO research_projects({','.join(columns)}) VALUES({placeholders})",
            tuple(values[column] for column in columns),
        )
    current = get_research(research_id, path=path)
    if current is None:
        raise RuntimeError("RESEARCH_CREATE_COMMIT_FAILED")
    return current


def get_research(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    migrate_r00(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM research_projects WHERE research_id=?",
            (str(research_id),),
        ).fetchone()
    return _decode_research(row)


def latest_research(*, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    migrate_r00(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM research_projects ORDER BY created_utc DESC,research_id DESC LIMIT 1"
        ).fetchone()
    return _decode_research(row)



def update_gate_state(
    research_id: str,
    *,
    gate_state: str,
    gate_input_manifest_sha: str | None = None,
    gate_output_manifest_sha: str | None = None,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if gate_state not in RESEARCH_STATES:
        raise ValueError("RESEARCH_GATE_STATE_INVALID")
    migrate_r00(path)
    now = utc_now()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT current_gate,gate_state FROM research_projects WHERE research_id=?",
            (str(research_id),),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(research_id)
        if str(row["current_gate"]) != "R00":
            raise RuntimeError("R00_CANNOT_MUTATE_LATER_GATE")
        current_state = str(row["gate_state"])
        if current_state != "STARTING" or gate_state not in R00_TERMINAL_STATES:
            raise RuntimeError(
                f"R00_GATE_STATE_TRANSITION_INVALID:{current_state}->{gate_state}"
            )
        cursor = conn.execute(
            """
            UPDATE research_projects
            SET gate_state=?,
                gate_input_manifest_sha=COALESCE(?,gate_input_manifest_sha),
                gate_output_manifest_sha=COALESCE(?,gate_output_manifest_sha),
                updated_utc=?
            WHERE research_id=? AND current_gate='R00' AND gate_state='STARTING'
            """,
            (
                str(gate_state),
                gate_input_manifest_sha,
                gate_output_manifest_sha,
                now,
                str(research_id),
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("R00_GATE_STATE_TRANSITION_RACE")
    current = get_research(research_id, path=path)
    if current is None:
        raise RuntimeError("RESEARCH_STATE_UPDATE_FAILED")
    return current

def commit_r00_terminal_authority(
    *,
    research_id: str,
    gate_state: str,
    gate_input_manifest_sha: str,
    gate_output_manifest_sha: str,
    output_artifact_id: str,
    state_artifact_id: str,
    owner_authorization_id: str,
    memory_payload: dict[str, Any],
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if gate_state not in R00_TERMINAL_STATES:
        raise ValueError("R00_GATE_STATE_TARGET_INVALID")
    migrate_r00(path)
    now = utc_now()
    gate_event = {
        "research_id": str(research_id),
        "gate": "R00",
        "from_state": "STARTING",
        "to_state": str(gate_state),
        "authority": "DETERMINISTIC_R00_VALIDATORS",
        "owner_authorization_id": str(owner_authorization_id),
        "input_manifest_sha": str(gate_input_manifest_sha),
        "output_manifest_sha": str(gate_output_manifest_sha),
    }
    gate_event_id = "RGE-" + stable_hash(gate_event)[:24]
    memory_body = {
        "research_id": str(research_id),
        "event_type": "R00_AUTHORITY_FROZEN",
        "stage": "R00",
        "status": str(gate_state),
        "learning_zone": memory_learning_zone("R00"),
        "source_manifest_sha256": str(gate_output_manifest_sha),
        "payload": deepcopy(memory_payload),
    }
    memory_event_id = "RME-" + stable_hash(memory_body)[:24]
    expected_state_sha = stable_hash(
        {
            "schema": RESEARCH_SCHEMA,
            "research_id": str(research_id),
            "gate": "R00",
            "state": str(gate_state),
        }
    )

    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT current_gate,gate_state FROM research_projects WHERE research_id=?",
            (str(research_id),),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(research_id)
        if str(row["current_gate"]) != "R00":
            raise RuntimeError("R00_CANNOT_MUTATE_LATER_GATE")
        current_state = str(row["gate_state"])
        if current_state != "STARTING":
            raise RuntimeError(
                f"R00_GATE_STATE_TRANSITION_INVALID:{current_state}->{gate_state}"
            )

        artifacts = conn.execute(
            """
            SELECT artifact_id,owner_type,owner_id,status,sha256
            FROM artifact_registry
            WHERE artifact_id IN (?,?)
            """,
            (str(output_artifact_id), str(state_artifact_id)),
        ).fetchall()
        by_id = {str(item["artifact_id"]): dict(item) for item in artifacts}
        output_artifact = by_id.get(str(output_artifact_id))
        state_artifact = by_id.get(str(state_artifact_id))
        if output_artifact is None or state_artifact is None:
            raise RuntimeError("R00_TERMINAL_ARTIFACT_MISSING")
        if (
            str(output_artifact["owner_type"]) != "RESEARCH"
            or str(output_artifact["owner_id"]) != research_id + ":output"
            or str(state_artifact["owner_type"]) != "RESEARCH_STATE"
            or str(state_artifact["owner_id"]) != research_id
        ):
            raise RuntimeError("R00_TERMINAL_ARTIFACT_IDENTITY_MISMATCH")
        if (
            str(output_artifact["status"]) != "R00_STAGED"
            or str(state_artifact["status"]) != "R00_STAGED"
        ):
            raise RuntimeError("R00_TERMINAL_ARTIFACT_NOT_STAGED")
        if str(state_artifact["sha256"] or "") != expected_state_sha:
            raise RuntimeError("R00_TERMINAL_STATE_ARTIFACT_HASH_MISMATCH")

        artifact_cursor = conn.execute(
            """
            UPDATE artifact_registry
            SET status=?,updated_utc=?
            WHERE artifact_id IN (?,?) AND status='R00_STAGED'
            """,
            (
                str(gate_state),
                now,
                str(output_artifact_id),
                str(state_artifact_id),
            ),
        )
        if artifact_cursor.rowcount != 2:
            raise RuntimeError("R00_TERMINAL_ARTIFACT_COMMIT_FAILED")

        state_cursor = conn.execute(
            """
            UPDATE research_projects
            SET gate_state=?,
                gate_input_manifest_sha=?,
                gate_output_manifest_sha=?,
                updated_utc=?
            WHERE research_id=? AND current_gate='R00' AND gate_state='STARTING'
            """,
            (
                str(gate_state),
                str(gate_input_manifest_sha),
                str(gate_output_manifest_sha),
                now,
                str(research_id),
            ),
        )
        if state_cursor.rowcount != 1:
            raise RuntimeError("R00_TERMINAL_STATE_COMMIT_FAILED")

        conn.execute(
            """
            INSERT OR IGNORE INTO research_gate_events(
                event_id,research_id,gate,from_state,to_state,authority,
                owner_authorization_id,input_manifest_sha,output_manifest_sha,created_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                gate_event_id,
                str(research_id),
                "R00",
                "STARTING",
                str(gate_state),
                "DETERMINISTIC_R00_VALIDATORS",
                str(owner_authorization_id),
                str(gate_input_manifest_sha),
                str(gate_output_manifest_sha),
                now,
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO research_memory_events(
                event_id,research_id,event_type,stage,status,learning_zone,
                adaptive_eligible,source_manifest_sha256,payload_json,created_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                memory_event_id,
                str(research_id),
                "R00_AUTHORITY_FROZEN",
                "R00",
                str(gate_state),
                memory_learning_zone("R00"),
                1 if memory_adaptive_eligible("R00") else 0,
                str(gate_output_manifest_sha),
                json.dumps(memory_payload, sort_keys=True),
                now,
            ),
        )

    current = get_research(research_id, path=path)
    if current is None:
        raise RuntimeError("R00_TERMINAL_COMMIT_READBACK_FAILED")
    return current

def append_gate_event(
    *,
    research_id: str,
    gate: str,
    from_state: str | None,
    to_state: str,
    authority: str,
    owner_authorization_id: str | None,
    input_manifest_sha: str | None,
    output_manifest_sha: str | None,
    created_utc: str | None = None,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_r00(path)
    if gate not in RESEARCH_GATES or to_state not in RESEARCH_STATES:
        raise ValueError("RESEARCH_GATE_EVENT_INVALID")
    payload = {
        "research_id": str(research_id),
        "gate": str(gate),
        "from_state": from_state,
        "to_state": str(to_state),
        "authority": str(authority),
        "owner_authorization_id": owner_authorization_id,
        "input_manifest_sha": input_manifest_sha,
        "output_manifest_sha": output_manifest_sha,
    }
    event_id = "RGE-" + stable_hash(payload)[:24]
    when = str(created_utc or utc_now())
    with connect(path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO research_gate_events(
                event_id,research_id,gate,from_state,to_state,authority,
                owner_authorization_id,input_manifest_sha,output_manifest_sha,created_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id,
                str(research_id),
                str(gate),
                from_state,
                str(to_state),
                str(authority),
                owner_authorization_id,
                input_manifest_sha,
                output_manifest_sha,
                when,
            ),
        )
        row = conn.execute(
            "SELECT * FROM research_gate_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
    return dict(row) if row is not None else {}


def require_adaptive_memory_stage(stage: str) -> None:
    if not memory_adaptive_eligible(stage):
        raise RuntimeError(
            "RESEARCH_PROTECTED_MEMORY_ADAPTIVE_USE_FORBIDDEN:"
            + str(stage).upper()
        )


def protected_memory_attack_probe(
    research_id: str,
    *,
    stage: str,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_r00(path)
    token = str(stage or "").strip().upper()
    with connect(path) as conn:
        before = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM research_memory_events WHERE research_id=?",
                (str(research_id),),
            ).fetchone()["n"]
        )
    rejected = False
    reason = ""
    try:
        append_memory_event(
            research_id=research_id,
            event_type="R01_PROTECTED_MEMORY_ATTACK",
            stage=token,
            status="ATTACK",
            payload={"attack": True},
            adaptive_use=True,
            path=path,
        )
    except RuntimeError as exc:
        reason = str(exc)
        rejected = reason.startswith(
            "RESEARCH_PROTECTED_MEMORY_ADAPTIVE_USE_FORBIDDEN:"
        )
    with connect(path) as conn:
        after = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM research_memory_events WHERE research_id=?",
                (str(research_id),),
            ).fetchone()["n"]
        )
    return {
        "stage": token,
        "rejected": rejected,
        "reason": reason,
        "memory_rows_before": before,
        "memory_rows_after": after,
        "no_insertion": before == after,
        "passed": rejected and before == after,
    }


def append_memory_event(
    *,
    research_id: str,
    event_type: str,
    stage: str,
    status: str,
    payload: dict[str, Any],
    source_manifest_sha256: str | None = None,
    created_utc: str | None = None,
    adaptive_use: bool = False,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_r00(path)
    zone = memory_learning_zone(stage)
    adaptive = memory_adaptive_eligible(stage)
    if adaptive_use:
        require_adaptive_memory_stage(stage)
    body = {
        "research_id": str(research_id),
        "event_type": str(event_type),
        "stage": str(stage).upper(),
        "status": str(status),
        "learning_zone": zone,
        "source_manifest_sha256": source_manifest_sha256,
        "payload": deepcopy(payload),
    }
    event_id = "RME-" + stable_hash(body)[:24]
    when = str(created_utc or utc_now())
    with connect(path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO research_memory_events(
                event_id,research_id,event_type,stage,status,learning_zone,
                adaptive_eligible,source_manifest_sha256,payload_json,created_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id,
                str(research_id),
                str(event_type),
                str(stage).upper(),
                str(status),
                zone,
                1 if adaptive else 0,
                source_manifest_sha256,
                json.dumps(payload, sort_keys=True),
                when,
            ),
        )
        row = conn.execute(
            "SELECT * FROM research_memory_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
    result = dict(row) if row is not None else {}
    if result:
        result["adaptive_eligible"] = bool(result["adaptive_eligible"])
        result["payload"] = json.loads(result.pop("payload_json"))
    return result


def list_memory_events(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> list[dict[str, Any]]:
    migrate_r00(path)
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM research_memory_events
            WHERE research_id=?
            ORDER BY created_utc,event_id
            """,
            (str(research_id),),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["adaptive_eligible"] = bool(item["adaptive_eligible"])
        item["payload"] = json.loads(item.pop("payload_json"))
        out.append(item)
    return out


def recover_incomplete_research(*, path: Path = DATABASE_PATH) -> list[str]:
    migrate_r00(path)
    recovered: list[str] = []
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT research_id,owner_authorization_id,gate_input_manifest_sha
            FROM research_projects
            WHERE current_gate='R00' AND gate_state='STARTING'
            ORDER BY created_utc,research_id
            """
        ).fetchall()
    for row in rows:
        research_id = str(row["research_id"])
        update_gate_state(
            research_id,
            gate_state="ERROR_WAITING_OWNER",
            path=path,
        )
        append_gate_event(
            research_id=research_id,
            gate="R00",
            from_state="STARTING",
            to_state="ERROR_WAITING_OWNER",
            authority="SYSTEM_RECOVERY_FAIL_CLOSED",
            owner_authorization_id=str(row["owner_authorization_id"]),
            input_manifest_sha=row["gate_input_manifest_sha"],
            output_manifest_sha=None,
            path=path,
        )
        recovered.append(research_id)
    return recovered


def research_database_status(path: Path = DATABASE_PATH) -> dict[str, Any]:
    try:
        migrate_r00(path)
        with connect(path) as conn:
            tables = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        required = {
            "research_authorizations",
            "research_projects",
            "research_gate_events",
            "research_memory_events",
            "artifact_registry",
        }
        missing = sorted(required - tables)
        if missing:
            return {
                "status": "FAIL",
                "reason": "R00_TABLES_MISSING",
                "missing": missing,
            }
        return {"status": "READY", "tables": sorted(required)}
    except Exception as exc:
        return {
            "status": "FAIL",
            "reason": "R00_DATABASE_UNAVAILABLE",
            "detail": str(exc),
        }
