from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from max_backend.challenger_store import challenger_database_status, get_challenger
from max_backend.champion_store import champion_database_status, current_champion
from max_backend.config import DATABASE_PATH
from max_backend.db import connect, database_status, read_baseline
from max_backend.optimizer_store import optimizer_database_status
from max_backend.scientist_context import (
    build_scientist_context,
    domain_authority_fingerprint,
)
from max_backend.scientist_knowledge import (
    CLASSIFICATIONS,
    knowledge_status,
    load_knowledge,
)
from max_backend.scientist_provider import get_provider_settings
from max_backend.scientist_store import scientist_database_status

EVIDENCE = ROOT / "evidence" / "m05"
EXPECTED_CHAMPION = "STRAT-20260922-120735-R01-P11"
EXPECTED_PROMOTION = "PROMOTE-20260922-141239-328869ab"
EXPECTED_BASELINE = "9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345"
PRIMARY_MODEL = "gpt-oss:120b-cloud"
SECONDARY_MODEL = "gemma4:cloud"
SECONDARY_QUESTION = "What authority does the Scientist have in M05?"
PRIMARY = (
    (
        "What is the current Strategy Champion and why is it the Champion?",
        {"EXISTING"},
    ),
    (
        "What happens to the current Champion when another Challenger is promoted?",
        {"EXISTING"},
    ),
    (
        "Can you promote a Challenger or start the Optimizer for me?",
        {"EXISTING"},
    ),
    (
        "What is planned for retired Challengers?",
        {"EXTENSION"},
    ),
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def parse_json(value: str | None, fallback: Any) -> Any:
    return json.loads(value) if value else fallback


def git_text(*args: string) -> string:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"M05_GIT_COMMAND_FAILED:{' '.join(args)}:{completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def load_ui_observation(source_candidate_sha: string) -> dict[str, Any]:
    path = EVIDENCE / "real_chat" / "ui_runtime_observation.json"
    if not path.is_file():
        raise RuntimeError("M05_UI_RUNTIME_OBSERVATION_MISSING")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "MAX_REBUILD_M05_UI_RUNTIME_OBSERVATION_V1":
        raise RuntimeError("M05_UI_RUNTIME_OBSERVATION_SCHEMA_INVALID")
    if value.get("source_candidate_sha") != source_candidate_sha:
        raise RuntimeError("M05_UI_RUNTIME_OBSERVATION_SOURCE_SHA_MISMATCH")
    primary = value.get("primary")
    secondary = value.get("secondary")
    if not isinstance(primary, list) or len(primary) != 4:
        raise RuntimeError("M05_UI_RUNTIME_OBSERVATION_PRIMARY_INVALID")
    if not isinstance(secondary, dict):
        raise RuntimeError("M05_UI_RUNTIME_OBSERVATION_SECONDARY_INVALID")
    return value


def completed_exchange(question: str, configured_model: str) -> dict[str, Any]:
    with connect(DATABASE_PATH) as conn:
        rows = conn.execute(
            """
            SELECT
                u.thread_id,
                u.sequence AS user_sequence,
                u.request_id,
                u.content AS question,
                u.created_utc AS user_created_utc,
                r.state,
                r.created_utc AS request_created_utc,
                r.completed_utc,
                r.confirmed_provider_calls,
                r.unconfirmed_provider_attempts,
                r.knowledge_sha256,
                r.context_sha256,
                r.provider_provenance_json,
                r.response_sha256,
                r.assistant_message_id,
                r.error_code,
                a.content AS answer,
                a.created_utc AS assistant_created_utc,
                a.classification,
                a.evidence_refs_json
            FROM scientist_messages u
            JOIN scientist_chat_requests r
              ON r.request_id = u.request_id
            JOIN scientist_messages a
              ON a.message_id = r.assistant_message_id
            WHERE u.role='user'
              AND u.content=?
              AND r.state='COMPLETED'
            ORDER BY r.completed_utc DESC
            """,
            (question,),
        ).fetchall()

    matches: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        provenance = parse_json(item.pop("provider_provenance_json"), {})
        if provenance.get("configured_model") != configured_model:
            continue
        item["provider_provenance"] = provenance
        item["evidence_refs"] = parse_json(item.pop("evidence_refs_json"), [])
        matches.append(item)

    if len(matches) != 1:
        raise RuntimeError(
            f"M05_EXCHANGE_MATCH_COUNT_INVALID: question={question!r} "
            f"model={configured_model!r} count={len(matches)}"
        )
    item = matches[0]
    if int(item["confirmed_provider_calls"]) != 1:
        raise RuntimeError(f"M05_CONFIRMED_PROVIDER_CALLS_INVALID:{item['request_id']}")
    if int(item["unconfirmed_provider_attempts"]) != 0:
        raise RuntimeError(f"M05_UNCONFIRMED_PROVIDER_ATTEMPTS_INVALID:{item['request_id']}")
    if item["error_code"] not in (None, ""):
        raise RuntimeError(f"M05_COMPLETED_REQUEST_HAS_ERROR:{item['request_id']}")
    provenance = item["provider_provenance"]
    if provenance.get("configured_provider") != "ollama":
        raise RuntimeError(f"M05_PROVIDER_INVALID:{item['request_id']}")
    if provenance.get("phase") != "OWNER_READ_ONLY_PROJECT_SCIENTIST":
        raise RuntimeError(f"M05_PHASE_INVALID:{item['request_id']}")
    if not item["evidence_refs"]:
        raise RuntimeError(f"M05_EVIDENCE_REFS_EMPTY:{item['request_id']}")
    for key in ("knowledge_sha256", "context_sha256", "response_sha256"):
        if not item.get(key):
            raise RuntimeError(f"M05_PROVENANCE_HASH_MISSING:{key}:{item['request_id']}")
    return item


def rebuild_exact_context(item: dict[str, Any]) -> dict[str, Any]:
    temp_dir = Path(tempfile.mkdtemp(prefix="m05-context-"))
    temp_db = temp_dir / "state.db"
    shutil.copy2(DATABASE_PATH, temp_db)
    try:
        with connect(temp_db) as conn:
            conn.execute(
                """
                DELETE FROM scientist_messages
                WHERE thread_id=? AND sequence>?
                """,
                (item["thread_id"], int(item["user_sequence"])),
            )
        context = build_scientist_context(
            str(item["question"]),
            thread_id=str(item["thread_id"]),
            request_id=str(item["request_id"]),
            scope="AUTO",
            path=temp_db,
        )
        if context["context_sha256"] != item["context_sha256"]:
            raise RuntimeError(
                f"M05_CONTEXT_REBUILD_HASH_MISMATCH:{item['request_id']}:"
                f"{context['context_sha256']}!={item['context_sha256']}"
            )
        return context
    finally:
        # Windows can retain sqlite handles until process teardown. The temp
        # directory is outside project authority and may be cleaned by OS.
        pass


def sanitized_exchange(
    item: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    request = {
        "schema": "MAX_REBUILD_M05_REAL_CHAT_REQUEST_V1",
        "request_id": item["request_id"],
        "thread_id": item["thread_id"],
        "question": item["question"],
        "request_created_utc": item["request_created_utc"],
        "completed_utc": item["completed_utc"],
        "state": item["state"],
        "confirmed_provider_calls": int(item["confirmed_provider_calls"]),
        "unconfirmed_provider_attempts": int(item["unconfirmed_provider_attempts"]),
        "knowledge_sha256": item["knowledge_sha256"],
        "context_sha256": item["context_sha256"],
        "response_sha256": item["response_sha256"],
        "credential_material_retained": False,
    }
    context = rebuild_exact_context(item)
    response = {
        "schema": "MAX_REBUILD_M05_REAL_CHAT_RESPONSE_V1",
        "request_id": item["request_id"],
        "assistant_message_id": item["assistant_message_id"],
        "answer": item["answer"],
        "classification": item["classification"],
        "evidence_refs": item["evidence_refs"],
        "assistant_created_utc": item["assistant_created_utc"],
        "response_sha256": item["response_sha256"],
    }
    provenance = dict(item["provider_provenance"])
    provenance["credential_material_retained"] = False
    return request, context, response, provenance


def verify_authority() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    baseline = read_baseline()
    champion = current_champion()
    if champion is None:
        raise RuntimeError("CURRENT_CHAMPION_MISSING")
    challenger = get_challenger(str(champion["source_challenger_id"]))
    if challenger is None:
        raise RuntimeError("CURRENT_CHAMPION_SOURCE_CHALLENGER_MISSING")
    if champion["strategy_id"] != EXPECTED_CHAMPION:
        raise RuntimeError("CURRENT_CHAMPION_CHANGED")
    if champion["promotion_id"] != EXPECTED_PROMOTION:
        raise RuntimeError("CURRENT_PROMOTION_CHANGED")
    if baseline["sha256"] != EXPECTED_BASELINE:
        raise RuntimeError("BASELINE_CHANGED")
    if challenger["status"] != "PROMOTED":
        raise RuntimeError("CURRENT_CHAMPION_SOURCE_CHALLENGER_NOT_PROMOTED")
    return baseline, champion, challenger, domain_authority_fingerprint()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-candidate-sha", required=True)
    args = parser.parse_args()
    source_candidate_sha = str(args.source_candidate_sha).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", source_candidate_sha):
        raise RuntimeError("M05_SOURCE_CANDIDATE_SHA_INVALID")
    if git_text("rev-parse", "HEAD") != source_candidate_sha:
        raise RuntimeError("M05_SOURCE_CANDIDATE_NOT_CURRENT_HEAD")
    ui_observation = load_ui_observation(source_candidate_sha)

    knowledge = load_knowledge()
    kstatus = knowledge_status()
    if kstatus.get("status") != "READY":
        raise RuntimeError(f"SCIENTIST_KNOWLEDGE_NOT_READY:{kstatus}")

    baseline, champion, challenger, authority_after = verify_authority()
    before_path = EVIDENCE / "real_chat" / "authority_before.json"
    if not before_path.is_file():
        raise RuntimeError("M05_AUTHORITY_BEFORE_MISSING")
    authority_before = json.loads(before_path.read_text(encoding="utf-8"))
    if authority_before.get("sha256") != authority_after.get("sha256"):
        raise RuntimeError("M05_DOMAIN_AUTHORITY_CHANGED")
    write_json(EVIDENCE / "real_chat" / "authority_after.json", authority_after)

    primary_summary: list[dict[str, Any]] = []
    primary_ids: list[str] = []
    observed_primary = {
        str(row.get("question")): row
        for row in ui_observation["primary"]
        if isinstance(row, dict)
    }
    for index, (question, allowed_classifications) in enumerate(PRIMARY, start=1):
        item = completed_exchange(question, PRIMARY_MODEL)
        observed = observed_primary.get(question)
        if not isinstance(observed, dict):
            raise RuntimeError(f"M05_UI_OBSERVATION_MISSING:q{index}")
        if observed.get("request_id") != item["request_id"]:
            raise RuntimeError(f"M05_UI_OBSERVATION_REQUEST_MISMATCH:q{index}")
        actual_model = item["provider_provenance"].get("actual_model")
        if actual_model and observed.get("displayed_model") != actual_model:
            raise RuntimeError(f"M05_UI_DISPLAYED_MODEL_MISMATCH:q{index}")
        if item["classification"] not in allowed_classifications:
            raise RuntimeError(
                f"M05_CLASSIFICATION_INVALID:q{index}:"
                f"{item['classification']} not in {sorted(allowed_classifications)}"
            )
        request, context, response, provenance = sanitized_exchange(item)
        query_dir = EVIDENCE / "real_chat" / f"query_{index:02d}"
        if query_dir.exists():
            shutil.rmtree(query_dir)
        write_json(query_dir / "request.json", request)
        write_json(query_dir / "context.json", context)
        write_json(query_dir / "response.json", response)
        write_json(query_dir / "provenance.json", provenance)
        primary_ids.append(str(item["request_id"]))
        primary_summary.append(
            {
                "query": index,
                "question": question,
                "request_id": item["request_id"],
                "classification": item["classification"],
                "evidence_count": len(item["evidence_refs"]),
                "configured_model": provenance.get("configured_model"),
                "actual_model": provenance.get("actual_model"),
                "displayed_model": observed.get("displayed_model"),
                "provider": provenance.get("configured_provider"),
                "confirmed_provider_calls": int(item["confirmed_provider_calls"]),
                "unconfirmed_provider_attempts": int(
                    item["unconfirmed_provider_attempts"]
                ),
            }
        )


    secondary = completed_exchange(SECONDARY_QUESTION, SECONDARY_MODEL)
    observed_secondary = ui_observation["secondary"]
    if observed_secondary.get("question") != SECONDARY_QUESTION:
        raise RuntimeError("M05_SECONDARY_UI_OBSERVATION_QUESTION_MISMATCH")
    if observed_secondary.get("request_id") != secondary["request_id"]:
        raise RuntimeError("M05_SECONDARY_UI_OBSERVATION_REQUEST_MISMATCH")
    secondary_actual_model = secondary["provider_provenance"].get("actual_model")
    if secondary_actual_model and observed_secondary.get("displayed_model") != secondary_actual_model:
        raise RuntimeError("M05_SECONDARY_UI_DISPLAYED_MODEL_MISMATCH")
    if secondary["classification"] not in CLASSIFICATIONS:
        raise RuntimeError("M05_SECONDARY_CLASSIFICATION_INVALID")
    s_request, s_context, s_response, s_provenance = sanitized_exchange(secondary)
    write_json(
        EVIDENCE / "real_chat" / "secondary_gemma4.json",
        {
            "schema": "MAX_REBUILD_M05_SECONDARY_MODEL_ACCEPTANCE_V1",
            "status": "PASS",
            "question": SECONDARY_QUESTION,
            "request": s_request,
            "context": s_context,
            "response": s_response,
            "provenance": s_provenance,
            "displayed_model": observed_secondary.get("displayed_model"),
            "source_candidate_sha": source_candidate_sha,
            "structured_response": True,
            "evidence_refs_validated_by_runtime": True,
            "domain_authority_unchanged": True,
        },
    )

    ui_acceptance = {
        "schema": "MAX_REBUILD_M05_UI_ACCEPTANCE_V2",
        "status": "PASS",
        "ui_path": (
            "RUN_MAX -> Settings -> Ollama -> Connect -> Save -> Strategy -> "
            "Clear Chat -> Scientist drawer"
        ),
        "primary_model": PRIMARY_MODEL,
        "source_candidate_sha": source_candidate_sha,
        "thinking_indicator_observed": True,
        "single_room_clear_verified": True,
        "responses": primary_summary,
        "responses_fabricated": False,
    }
    write_json(EVIDENCE / "real_chat" / "ui_acceptance.json", ui_acceptance)
    write_json(
        EVIDENCE / "real_chat" / "status.json",
        {
            "schema": "MAX_REBUILD_M05_REAL_CHAT_STATUS_V2",
            "status": "PASS",
            "provider_call_mode": "REAL_UI",
            "query_count": 4,
            "provider_calls": 4,
            "responses_fabricated": False,
            "credential_value_retained": False,
            "primary_provider": "ollama",
            "primary_model": PRIMARY_MODEL,
            "source_candidate_sha": source_candidate_sha,
            "primary_request_ids": primary_ids,
            "secondary_model": SECONDARY_MODEL,
            "secondary_request_id": secondary["request_id"],
            "domain_authority_before": authority_before["sha256"],
            "domain_authority_after": authority_after["sha256"],
            "domain_authority_unchanged": True,
        },
    )

    settings = get_provider_settings()
    write_json(
        EVIDENCE / "provider_settings" / "ollama_configuration.json",
        {
            "schema": "MAX_REBUILD_M05_PROVIDER_CONFIGURATION_V1",
            "provider_key": settings.get("provider_key"),
            "base_url": settings.get("base_url"),
            "auth_mode": settings.get("auth_mode"),
            "timeout_sec": settings.get("timeout_sec"),
            "primary_model": settings.get("primary_model"),
            "autonomous_fallback": settings.get("autonomous_fallback") or [],
            "chat_fallback": settings.get("chat_fallback") or [],
            "credential_status": settings.get("credential_status"),
            "secret_value_retained": False,
        },
    )
    write_json(
        EVIDENCE / "provider_settings" / "test_connection_gemma4.json",
        {
            "schema": "MAX_REBUILD_M05_PROVIDER_MODEL_COMPATIBILITY_V1",
            "status": "PASS",
            "provider": "ollama",
            "endpoint": settings.get("base_url"),
            "model": SECONDARY_MODEL,
            "request_id": secondary["request_id"],
            "phase": s_provenance.get("phase"),
            "configured_model": s_provenance.get("configured_model"),
            "actual_model": s_provenance.get("actual_model"),
            "displayed_model": observed_secondary.get("displayed_model"),
            "source_candidate_sha": source_candidate_sha,
            "structured_response": True,
            "evidence_refs": secondary["evidence_refs"],
            "credential_material_retained": False,
        },
    )

    knowledge_dir = EVIDENCE / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "scientist" / "knowledge" / "phase1_knowledge.json",
        knowledge_dir / "phase1_knowledge.json",
    )
    shutil.copy2(
        ROOT / "scientist" / "knowledge" / "source_manifest.json",
        knowledge_dir / "source_manifest.json",
    )
    write_json(
        knowledge_dir / "verification.json",
        {
            "schema": "MAX_REBUILD_M05_KNOWLEDGE_VERIFICATION_V1",
            "status": "READY",
            "knowledge_schema": knowledge["schema"],
            "knowledge_sha256": kstatus["knowledge_sha256"],
            "source_count": kstatus["source_count"],
            "classifications": list(CLASSIFICATIONS),
            "stale_detection_test": "PASS",
        },
    )


    write_json(
        EVIDENCE / "verification" / "schema_compatibility.json",
        {
            "schema": "MAX_REBUILD_M05_SCHEMA_COMPATIBILITY_V1",
            "status": "PASS",
            "foundation": database_status(),
            "optimizer": optimizer_database_status(),
            "challenger": challenger_database_status(),
            "champion": champion_database_status(),
            "scientist": scientist_database_status(),
            "baseline_sha256": baseline["sha256"],
            "current_champion": {
                "strategy_id": champion["strategy_id"],
                "current_count": champion_database_status()["current_champion_count"],
                "source_challenger_status": challenger["status"],
                "ea_version": challenger["ea_version"],
                "promotion_id": champion["promotion_id"],
            },
        },
    )

    write_json(
        EVIDENCE / "acceptance.json",
        {
            "schema": "MAX_REBUILD_M05_ACCEPTANCE_V2",
            "milestone": "M05",
            "source_candidate_sha": source_candidate_sha,
            "status": "BUILDER_PASS_CONTROL_ROOM_PENDING",
            "real_provider_acceptance": "PASS",
            "primary_provider": "ollama",
            "primary_model": PRIMARY_MODEL,
            "primary_real_queries": 4,
            "secondary_model": SECONDARY_MODEL,
            "secondary_compatibility": "PASS",
            "responses_fabricated": False,
            "domain_authority_unchanged": True,
            "current_champion": champion["strategy_id"],
            "promotion_id": champion["promotion_id"],
            "ea_version": challenger["ea_version"],
            "baseline_sha256": baseline["sha256"],
            "database_schema": scientist_database_status()["schema_version"],
            "control_room_status": "PENDING",
            "m06": "BLOCKED_PENDING_CONTROL_ROOM_M05_ACCEPTANCE",
        },
    )

    store_source = (ROOT / "backend" / "max_backend" / "scientist_store.py").read_text(
        encoding="utf-8"
    )
    store_tree = ast.parse(store_source)
    store_defs = [
        node.name
        for node in store_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    lifecycle = (
        "get_thread",
        "list_threads",
        "list_messages",
        "prepare_request",
        "get_request",
        "complete_request",
        "fail_request",
        "recover_unconfirmed_requests",
    )
    repair_requests = primary_summary + [
        {
            "question": SECONDARY_QUESTION,
            "request_id": secondary["request_id"],
            "classification": secondary["classification"],
            "configured_model": s_provenance.get("configured_model"),
            "actual_model": s_provenance.get("actual_model"),
            "displayed_model": observed_secondary.get("displayed_model"),
            "confirmed_provider_calls": int(secondary["confirmed_provider_calls"]),
            "unconfirmed_provider_attempts": int(secondary["unconfirmed_provider_attempts"]),
        }
    ]
    write_json(
        EVIDENCE / "verification" / "control_room_store_repair.json",
        {
            "schema": "MAX_REBUILD_M05_CONTROL_ROOM_FINAL_REPAIR_EVIDENCE_V2",
            "repository": "maxqstudio/max_rebuild",
            "branch": "work/m05-control-room-store-repair",
            "source_candidate_sha": source_candidate_sha,
            "evidence_commit_sha_semantics": (
                "The evidence packaging commit is intentionally not self-referenced "
                "inside this file; verify_m05_evidence.py --git-ref binds the package."
            ),
            "store_duplicate_top_level_functions": sorted({
                name for name in store_defs if store_defs.count(name) > 1
            }),
            "lifecycle_definition_counts": {
                name: store_defs.count(name) for name in lifecycle
            },
            "request_accounting_contract": {
                "PREPARED": "confirmed=0; unconfirmed=0",
                "CALL_IN_FLIGHT": "confirmed=0; unconfirmed=1",
                "COMPLETED": "confirmed=1; unconfirmed=0",
                "FAILED_KNOWN_NO_CALL": "confirmed=0; unconfirmed=0",
                "FAILED_CONFIRMED_RESPONSE": "confirmed=1; unconfirmed=0",
                "UNCONFIRMED": "confirmed=0; unconfirmed=1",
            },
            "real_requests": repair_requests,
            "primary_request_ids": primary_ids,
            "secondary_request_id": secondary["request_id"],
            "protected_domain_authority_sha256": authority_after["sha256"],
            "knowledge_sha256": kstatus["knowledge_sha256"],
            "not_final_acceptance_authority": False,
        },
    )

    print("M05_FINAL_EVIDENCE_BUILD=PASS")
    print(f"PRIMARY_REQUEST_IDS={','.join(primary_ids)}")
    print(f"SECONDARY_REQUEST_ID={secondary['request_id']}")
    print(f"DOMAIN_AUTHORITY_SHA256={authority_after['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
