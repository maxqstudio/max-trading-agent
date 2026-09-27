from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH, RESEARCH_ARTIFACT_ROOT
from .db import connect
from .optimizer_store import utc_now
from .research_contract import memory_learning_zone, stable_hash
from .workflow_store import migrate_current

R01_TERMINAL_STATES = {
    "PASS_WAITING_OWNER",
    "FAIL_WAITING_OWNER",
    "ERROR_WAITING_OWNER",
}


def create_r01_authorization(
    payload: dict[str, Any],
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_current(path)
    if str(payload.get("gate")) != "R01" or str(payload.get("action")) != "START":
        raise ValueError("R01_AUTHORIZATION_INVALID")
    if payload.get("confirmed") is not True:
        raise ValueError("R01_AUTHORIZATION_CONFIRMATION_REQUIRED")
    required = {
        "authorization_id","research_id","gate","action","confirmed",
        "payload_sha256","payload","authorized_utc",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError("R01_AUTHORIZATION_FIELDS_MISSING:" + ",".join(missing))
    encoded = json.dumps(payload["payload"], sort_keys=True, separators=(",", ":"))
    if stable_hash(payload["payload"]) != str(payload["payload_sha256"]):
        raise ValueError("R01_AUTHORIZATION_PAYLOAD_HASH_MISMATCH")
    with connect(path) as conn:
        existing = conn.execute(
            "SELECT * FROM research_gate_authorizations_v2 WHERE authorization_id=?",
            (str(payload["authorization_id"]),),
        ).fetchone()
        if existing is not None:
            row = dict(existing)
            if (
                row["research_id"] != str(payload["research_id"])
                or row["payload_sha256"] != str(payload["payload_sha256"])
                or row["payload_json"] != encoded
            ):
                raise RuntimeError("R01_AUTHORIZATION_IDENTITY_COLLISION")
            row["confirmed"] = bool(row["confirmed"])
            row["payload"] = json.loads(row.pop("payload_json"))
            return row
        conn.execute(
            """
            INSERT INTO research_gate_authorizations_v2(
                authorization_id,research_id,gate,action,confirmed,
                payload_sha256,payload_json,authorized_utc
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                str(payload["authorization_id"]),
                str(payload["research_id"]),
                "R01","START",1,
                str(payload["payload_sha256"]),
                encoded,
                str(payload["authorized_utc"]),
            ),
        )
    return get_r01_authorization(str(payload["authorization_id"]), path=path) or {}


def get_r01_authorization(
    authorization_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    migrate_current(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM research_gate_authorizations_v2 WHERE authorization_id=?",
            (str(authorization_id),),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["confirmed"] = bool(result["confirmed"])
    result["payload"] = json.loads(result.pop("payload_json"))
    return result


def get_r01_run(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    migrate_current(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM research_r01_runs WHERE research_id=?",
            (str(research_id),),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["artifact_ids"] = json.loads(result.pop("artifact_ids_json"))
    return result


def create_r01_run(
    *,
    run_id: str,
    research_id: str,
    authorization_id: str,
    input_manifest_sha: str,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_current(path)
    now = utc_now()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        base = conn.execute(
            """
            SELECT research_id,current_gate,gate_state,training_count,onnx_count,
                   research_challenger_count,champion_mutation
            FROM research_projects WHERE research_id=?
            """,
            (str(research_id),),
        ).fetchone()
        if base is None:
            raise FileNotFoundError(research_id)
        if (
            str(base["current_gate"]) != "R00"
            or str(base["gate_state"]) != "PASS_WAITING_OWNER"
            or int(base["training_count"]) != 0
            or int(base["onnx_count"]) != 0
            or int(base["research_challenger_count"]) != 0
            or str(base["champion_mutation"]) != "NONE"
        ):
            raise RuntimeError("R01_R00_ACCEPTED_AUTHORITY_REQUIRED")
        existing = conn.execute(
            "SELECT * FROM research_r01_runs WHERE research_id=?",
            (str(research_id),),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["run_id"]) != str(run_id)
                or str(existing["authorization_id"]) != str(authorization_id)
                or str(existing["input_manifest_sha"]) != str(input_manifest_sha)
            ):
                raise RuntimeError("R01_RUN_IDENTITY_COLLISION")
        else:
            conn.execute(
                """
                INSERT INTO research_r01_runs(
                    run_id,research_id,authorization_id,state,input_manifest_sha,
                    created_utc,updated_utc
                ) VALUES(?,?,?,'STARTING',?,?,?)
                """,
                (
                    str(run_id),str(research_id),str(authorization_id),
                    str(input_manifest_sha),now,now,
                ),
            )
            start_payload = {
                "research_id": str(research_id),
                "gate": "R01",
                "from_state": "PASS_WAITING_OWNER",
                "to_state": "STARTING",
                "authority": "OWNER_R01_AUTHORIZATION",
                "owner_authorization_id": str(authorization_id),
                "input_manifest_sha": str(input_manifest_sha),
                "output_manifest_sha": None,
            }
            event_id = "RGE-" + stable_hash(start_payload)[:24]
            conn.execute(
                """
                INSERT OR IGNORE INTO research_gate_events(
                    event_id,research_id,gate,from_state,to_state,authority,
                    owner_authorization_id,input_manifest_sha,output_manifest_sha,created_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id,str(research_id),"R01","PASS_WAITING_OWNER","STARTING",
                    "OWNER_R01_AUTHORIZATION",str(authorization_id),
                    str(input_manifest_sha),None,now,
                ),
            )
    result = get_r01_run(research_id, path=path)
    if result is None:
        raise RuntimeError("R01_RUN_CREATE_FAILED")
    return result


def stage_r01_artifacts(
    research_id: str,
    artifact_ids: list[str],
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_current(path)
    normalized = sorted({str(value) for value in artifact_ids})
    if not normalized or len(normalized) != len(artifact_ids):
        raise ValueError("R01_ARTIFACT_STAGE_SET_INVALID")
    placeholders = ",".join("?" for _ in normalized)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute(
            "SELECT run_id,state FROM research_r01_runs WHERE research_id=?",
            (str(research_id),),
        ).fetchone()
        if run is None or str(run["state"]) != "STARTING":
            raise RuntimeError("R01_ARTIFACT_STAGE_STATE_INVALID")
        rows = conn.execute(
            f"""
            SELECT artifact_id,owner_type,owner_id,source_type,source_id,status,
                   canonical_path
            FROM artifact_registry
            WHERE artifact_id IN ({placeholders})
            """,
            tuple(normalized),
        ).fetchall()
        by_id = {str(row["artifact_id"]): dict(row) for row in rows}
        if set(by_id) != set(normalized):
            raise RuntimeError("R01_ARTIFACT_STAGE_MISSING")
        expected_root = (
            RESEARCH_ARTIFACT_ROOT
            / str(research_id)
            / "r01"
            / str(run["run_id"])
        ).resolve()
        for aid, row in by_id.items():
            artifact_path = Path(str(row["canonical_path"])).resolve()
            if (
                str(row["owner_type"]) != "RESEARCH_R01"
                or str(row["owner_id"]) != str(research_id)
                or str(row["source_type"]) != "RESEARCH_R01_RUN"
                or str(row["source_id"]) != str(run["run_id"])
                or str(row["status"]) != "R01_STAGED"
                or not artifact_path.is_relative_to(expected_root)
            ):
                raise RuntimeError(f"R01_ARTIFACT_STAGE_INVALID:{aid}")
        extra = conn.execute(
            """
            SELECT artifact_id FROM artifact_registry
            WHERE owner_type='RESEARCH_R01'
              AND owner_id=?
              AND source_type='RESEARCH_R01_RUN'
              AND source_id=?
              AND status='R01_STAGED'
            """,
            (str(research_id), str(run["run_id"])),
        ).fetchall()
        extra_ids = {str(row["artifact_id"]) for row in extra}
        if extra_ids != set(normalized):
            raise RuntimeError("R01_ARTIFACT_STAGE_ORPHAN_SET")
        cursor = conn.execute(
            """
            UPDATE research_r01_runs
            SET artifact_ids_json=?,updated_utc=?
            WHERE research_id=? AND state='STARTING'
            """,
            (json.dumps(normalized), utc_now(), str(research_id)),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("R01_ARTIFACT_STAGE_STATE_INVALID")
    result = get_r01_run(research_id, path=path)
    if result is None:
        raise RuntimeError("R01_ARTIFACT_STAGE_READBACK_FAILED")
    return result


def discard_staged_r01_artifacts(
    research_id: str,
    run_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> list[str]:
    migrate_current(path)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute(
            "SELECT state FROM research_r01_runs WHERE research_id=? AND run_id=?",
            (str(research_id), str(run_id)),
        ).fetchone()
        if run is None or str(run["state"]) != "STARTING":
            raise RuntimeError("R01_STAGING_DISCARD_STATE_INVALID")
        rows = conn.execute(
            """
            SELECT artifact_id FROM artifact_registry
            WHERE owner_type='RESEARCH_R01'
              AND owner_id=?
              AND source_type='RESEARCH_R01_RUN'
              AND source_id=?
              AND status='R01_STAGED'
            """,
            (str(research_id), str(run_id)),
        ).fetchall()
        ids = [str(row["artifact_id"]) for row in rows]
        conn.execute(
            """
            DELETE FROM artifact_registry
            WHERE owner_type='RESEARCH_R01'
              AND owner_id=?
              AND source_type='RESEARCH_R01_RUN'
              AND source_id=?
              AND status='R01_STAGED'
            """,
            (str(research_id), str(run_id)),
        )
        conn.execute(
            """
            UPDATE research_r01_runs
            SET artifact_ids_json='[]',updated_utc=?
            WHERE research_id=? AND run_id=? AND state='STARTING'
            """,
            (utc_now(), str(research_id), str(run_id)),
        )
    return ids


def commit_r01_terminal_authority(
    *,
    research_id: str,
    state: str,
    dataset_id: str | None,
    input_manifest_sha: str,
    output_manifest_sha: str,
    artifact_ids: list[str],
    authorization_id: str,
    memory_payload: dict[str, Any],
    error: str | None = None,
    fault_at: str | None = None,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if state not in R01_TERMINAL_STATES:
        raise ValueError("R01_TERMINAL_STATE_INVALID")
    if not artifact_ids:
        raise ValueError("R01_TERMINAL_ARTIFACTS_REQUIRED")
    if fault_at == "before_terminal_commit":
        raise RuntimeError("R01_FAULT_BEFORE_TERMINAL_COMMIT")
    now = utc_now()
    terminal_event = {
        "research_id": str(research_id),
        "gate": "R01",
        "from_state": "STARTING",
        "to_state": str(state),
        "authority": "DETERMINISTIC_R01_VALIDATORS",
        "owner_authorization_id": str(authorization_id),
        "input_manifest_sha": str(input_manifest_sha),
        "output_manifest_sha": str(output_manifest_sha),
    }
    event_id = "RGE-" + stable_hash(terminal_event)[:24]
    memory_body = {
        "research_id": str(research_id),
        "event_type": "R01_DATA_FOUNDATION_SEALED",
        "stage": "R01",
        "status": str(state),
        "learning_zone": memory_learning_zone("R01"),
        "source_manifest_sha256": str(output_manifest_sha),
        "payload": memory_payload,
    }
    memory_event_id = "RME-" + stable_hash(memory_body)[:24]

    placeholders = ",".join("?" for _ in artifact_ids)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute(
            "SELECT * FROM research_r01_runs WHERE research_id=?",
            (str(research_id),),
        ).fetchone()
        if run is None:
            raise FileNotFoundError(research_id)
        if str(run["state"]) != "STARTING":
            raise RuntimeError("R01_TERMINAL_ALREADY_PUBLISHED")
        if (
            str(run["authorization_id"]) != str(authorization_id)
            or str(run["input_manifest_sha"]) != str(input_manifest_sha)
        ):
            raise RuntimeError("R01_TERMINAL_AUTHORITY_MISMATCH")
        bound_artifact_ids = set(json.loads(str(run["artifact_ids_json"] or "[]")))
        if bound_artifact_ids != {str(value) for value in artifact_ids}:
            raise RuntimeError("R01_TERMINAL_ARTIFACT_BINDING_MISMATCH")
        extra_staged = conn.execute(
            """
            SELECT artifact_id FROM artifact_registry
            WHERE owner_type='RESEARCH_R01'
              AND owner_id=?
              AND source_type='RESEARCH_R01_RUN'
              AND source_id=?
              AND status='R01_STAGED'
            """,
            (str(research_id), str(run["run_id"])),
        ).fetchall()
        if {str(row["artifact_id"]) for row in extra_staged} != bound_artifact_ids:
            raise RuntimeError("R01_TERMINAL_ORPHAN_STAGED_ARTIFACT")
        rows = conn.execute(
            f"""
            SELECT artifact_id,owner_type,owner_id,status
            FROM artifact_registry
            WHERE artifact_id IN ({placeholders})
            """,
            tuple(str(value) for value in artifact_ids),
        ).fetchall()
        by_id = {str(row["artifact_id"]): dict(row) for row in rows}
        if set(by_id) != {str(value) for value in artifact_ids}:
            raise RuntimeError("R01_TERMINAL_ARTIFACT_MISSING")
        for aid, row in by_id.items():
            if (
                str(row["owner_type"]) != "RESEARCH_R01"
                or str(row["owner_id"]) != str(research_id)
                or str(row["status"]) != "R01_STAGED"
            ):
                raise RuntimeError(f"R01_TERMINAL_ARTIFACT_INVALID:{aid}")
        updated = conn.execute(
            f"""
            UPDATE artifact_registry
            SET status=?,updated_utc=?
            WHERE artifact_id IN ({placeholders}) AND status='R01_STAGED'
            """,
            (str(state), now, *tuple(str(value) for value in artifact_ids)),
        )
        if updated.rowcount != len(artifact_ids):
            raise RuntimeError("R01_TERMINAL_ARTIFACT_COMMIT_FAILED")
        if fault_at == "after_artifact_update":
            raise RuntimeError("R01_FAULT_AFTER_ARTIFACT_UPDATE")
        cursor = conn.execute(
            """
            UPDATE research_r01_runs
            SET state=?,dataset_id=?,output_manifest_sha=?,artifact_ids_json=?,
                error=?,updated_utc=?
            WHERE research_id=? AND state='STARTING'
            """,
            (
                str(state),str(dataset_id) if dataset_id is not None else None,str(output_manifest_sha),
                json.dumps(sorted(str(value) for value in artifact_ids)),
                str(error)[:2000] if error else None,now,str(research_id),
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("R01_TERMINAL_STATE_COMMIT_FAILED")
        conn.execute(
            """
            INSERT OR IGNORE INTO research_gate_events(
                event_id,research_id,gate,from_state,to_state,authority,
                owner_authorization_id,input_manifest_sha,output_manifest_sha,created_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id,str(research_id),"R01","STARTING",str(state),
                "DETERMINISTIC_R01_VALIDATORS",str(authorization_id),
                str(input_manifest_sha),str(output_manifest_sha),now,
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
                memory_event_id,str(research_id),"R01_DATA_FOUNDATION_SEALED",
                "R01",str(state),memory_learning_zone("R01"),0,
                str(output_manifest_sha),
                json.dumps(memory_payload, sort_keys=True),now,
            ),
        )
    result = get_r01_run(research_id, path=path)
    if result is None or result["state"] != state:
        raise RuntimeError("R01_TERMINAL_READBACK_FAILED")
    return result


def incomplete_r01_runs(*, path: Path = DATABASE_PATH) -> list[dict[str, Any]]:
    migrate_current(path)
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM research_r01_runs
            WHERE state='STARTING'
            ORDER BY created_utc,run_id
            """
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["artifact_ids"] = json.loads(item.pop("artifact_ids_json"))
        result.append(item)
    return result


def recover_incomplete_r01(*, path: Path = DATABASE_PATH) -> list[str]:
    """Compatibility probe only.

    Recovery must be materialized by research_r01_service so a sealed output
    manifest, artifact lineage, gate event, and Research Memory event are
    published together. This function intentionally performs no mutation.
    """
    return [row["research_id"] for row in incomplete_r01_runs(path=path)]
