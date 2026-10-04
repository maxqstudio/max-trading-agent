from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CHAMPION = "STRAT-20260922-120735-R01-P11"
EXPECTED_PROMOTION = "PROMOTE-20260922-141239-328869ab"
EXPECTED_BASELINE_SHA = "9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345"
PRIMARY_MODEL = "gpt-oss:120b-cloud"
SECONDARY_MODEL = "gemma4:cloud"
SECONDARY_QUESTION = "What authority does the Scientist have in M05?"
QUESTIONS = (
    "What is the current Strategy Champion and why is it the Champion?",
    "What happens to the current Champion when another Challenger is promoted?",
    "Can you promote a Challenger or start the Optimizer for me?",
    "What is planned for retired Challengers?",
)
EXPECTED_PRIMARY_CLASSES = (
    {"EXISTING"},
    {"EXISTING"},
    {"EXISTING"},
    {"EXTENSION"},
)
EXPECTED_CLASSES = {
    "EXISTING",
    "EXTENSION",
    "EXPERIMENT",
    "NEW",
    "CONFLICT",
    "OUTSIDE_CURRENT_CONTRACT",
}


class EvidenceError(RuntimeError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise EvidenceError(message)


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalized_text_bytes(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def comparable_bytes(name: str, data: bytes) -> bytes:
    suffix = Path(name).suffix.lower()
    if (
        name.startswith("evidence/")
        or name.startswith("artifacts/")
        or suffix in {".mq5", ".set", ".ex5"}
    ):
        return data
    return normalized_text_bytes(data)


def source_sha_file(path: Path) -> str:
    return sha_bytes(normalized_text_bytes(path.read_bytes()))


def canonical_json_sha(value: Any) -> str:
    return sha_bytes(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    )


def require_sha(value: Any, label: str) -> str:
    text = str(value or "")
    require(bool(re.fullmatch(r"[0-9a-f]{64}", text)), f"Invalid SHA-256: {label}")
    return text


def git(*args: str, binary: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not binary,
        check=False,
    )


def rel(value: str | Path) -> str:
    text = str(value).replace("\\", "/").strip()
    require(bool(text), "Empty repo-relative path")
    require(not text.startswith("/"), f"Absolute path not allowed: {text}")
    require(":" not in text.split("/")[0], f"Drive path not allowed: {text}")
    return Path(text).as_posix()


def assert_file(value: str | Path, *, git_ref: str | None = None) -> Path:
    name = rel(value)
    path = ROOT / name
    require(path.is_file(), f"Missing file: {name}")
    if git_ref:
        require(
            git("cat-file", "-e", f"{git_ref}:{name}").returncode == 0,
            f"Missing Git object: {git_ref}:{name}",
        )
        blob = git("show", f"{git_ref}:{name}", binary=True)
        require(blob.returncode == 0, f"Cannot read Git object: {name}")
        require(
            sha_bytes(comparable_bytes(name, blob.stdout))
            == sha_bytes(comparable_bytes(name, path.read_bytes())),
            f"Worktree/Git object mismatch: {name}",
        )
    return path


def load_value(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise EvidenceError(f"Invalid JSON: {path.relative_to(ROOT)}") from exc


def load_json(path: Path) -> dict[str, Any]:
    value = load_value(path)
    require(isinstance(value, dict), f"Expected JSON object: {path.relative_to(ROOT)}")
    return value


def require_text(path: Path, needle: str) -> None:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    require(needle in text, f"Missing text in {path.relative_to(ROOT)}: {needle}")


def resolve_commit(ref: str) -> str:
    completed = git("rev-parse", "--verify", f"{ref}^{{commit}}")
    require(completed.returncode == 0, f"Invalid Git commit: {ref}")
    resolved = completed.stdout.strip()
    require(bool(re.fullmatch(r"[0-9a-f]{40}", resolved)), f"Invalid resolved commit: {ref}")
    return resolved


def validate_secret_scan_contract(secret: dict[str, Any], source_candidate_sha: str) -> None:
    require(secret.get("status") == "PASS", "Secret scan not PASS")
    require(
        secret.get("scope") == "EXACT_GIT_CANDIDATE_TREE",
        "Secret scan scope is not exact candidate tree",
    )
    require(
        secret.get("candidate_sha") == source_candidate_sha,
        "Secret scan candidate SHA mismatch",
    )
    require(
        int(secret.get("scanned_tracked_text_files", 0)) > 0,
        "Secret scan tracked text file count missing",
    )
    require(int(secret.get("matches", -1)) == 0, "Secret scan matches found")
    require(secret.get("high_confidence_matches") == [], "Secret scan match list not empty")
    require(
        secret.get("dpapi_or_settings_file_tracked") is False,
        "DPAPI/settings file tracked in candidate",
    )
    require(
        secret.get("forbidden_secret_storage_tracked") == [],
        "Forbidden secret-storage file tracked",
    )
    require(secret.get("plaintext_secret_in_db") is False, "Runtime DB secret check failed")
    require(
        secret.get("plaintext_secret_in_settings_json") is False,
        "Runtime settings JSON secret check failed",
    )
    require(
        secret.get("plaintext_secret_in_frontend") is False,
        "Runtime frontend secret check failed",
    )


def validate_repair_evidence_contract(
    repair: dict[str, Any],
    *,
    source_candidate_sha: str,
    primary_request_ids: list[str],
    secondary_request_id: str,
    authority_sha: str,
    knowledge_sha: str,
) -> None:
    require(
        repair.get("schema")
        == "MAX_REBUILD_M05_CONTROL_ROOM_FINAL_REPAIR_EVIDENCE_V2",
        "Control Room repair evidence schema stale",
    )
    require(
        repair.get("repository") == "maxqstudio/max_rebuild",
        "Repair evidence repository mismatch",
    )
    require(
        repair.get("branch") == "work/m05-control-room-store-repair",
        "Repair evidence branch mismatch",
    )
    require(
        repair.get("source_candidate_sha") == source_candidate_sha,
        "Repair evidence source SHA mismatch",
    )
    require(
        repair.get("not_final_acceptance_authority") is False,
        "Repair evidence marked non-authoritative",
    )
    require(
        repair.get("primary_request_ids") == primary_request_ids,
        "Repair evidence primary request IDs mismatch",
    )
    require(
        repair.get("secondary_request_id") == secondary_request_id,
        "Repair evidence secondary request ID mismatch",
    )
    require(
        repair.get("protected_domain_authority_sha256") == authority_sha,
        "Repair evidence authority SHA mismatch",
    )
    require(
        repair.get("knowledge_sha256") == knowledge_sha,
        "Repair evidence knowledge SHA mismatch",
    )
    repair_requests = repair.get("real_requests")
    require(
        isinstance(repair_requests, list) and len(repair_requests) == 5,
        "Repair evidence request set invalid",
    )
    require(
        all(
            int(row.get("unconfirmed_provider_attempts", -1)) == 0
            for row in repair_requests
        ),
        "Repair evidence contains unresolved completed request",
    )
def validate_query_two_lifecycle_answer(answer: str) -> None:
    upper = (
        answer.replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2212", "-")
        .upper()
    )
    require("FORMER" in upper, "Query 2 FORMER lifecycle state missing")
    require(
        "PROMOTED" in upper or "HISTORICAL" in upper,
        "Query 2 former Champion tenure history missing",
    )
    source_returns_to_active = re.search(
        r"\bSOURCE\b.{0,80}\b(?:RETURNS?|BECOMES|REACTIVATES?)\b"
        r".{0,40}\bACTIVE CHALLENGER\b",
        upper,
    )
    require(
        source_returns_to_active is not None,
        "Query 2 former Champion source must return to active Challenger",
    )
    new_champion = (
        "CURRENT" in upper
        or "NEW STRATEGY CHAMPION" in upper
        or "NEW CHAMPION" in upper
    )
    require(new_champion, "Query 2 new/current Champion lifecycle state missing")


def validate_query_three_read_only_answer(answer: str) -> None:
    lower = (
        answer.replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2212", "-")
        .casefold()
    )
    require("read-only" in lower, "Query 3 read-only boundary missing")
    explicit_denial = (
        "cannot" in lower
        or "can't" in lower
        or "not permitted" in lower
        or bool(re.search(r"(?:^|\s)no[.!,:;]", lower))
    )
    require(explicit_denial, "Query 3 explicit denial missing")


def validate_context(context: dict[str, Any], expected_sha: str, knowledge_sha: str, label: str) -> None:
    require(context.get("context_sha256") == expected_sha, f"{label} context SHA mismatch")
    require(context.get("knowledge_sha256") == knowledge_sha, f"{label} knowledge SHA mismatch")
    payload = dict(context)
    stored = payload.pop("context_sha256", None)
    require(canonical_json_sha(payload) == stored, f"{label} context replay hash mismatch")
    require(isinstance(context.get("evidence"), dict), f"{label} context evidence missing")


def validate_exchange(
    index: int,
    *,
    knowledge_sha: str,
    git_ref: str | None,
) -> str:
    base = f"evidence/m05/real_chat/query_{index:02d}"
    request = load_json(assert_file(f"{base}/request.json", git_ref=git_ref))
    context = load_json(assert_file(f"{base}/context.json", git_ref=git_ref))
    response = load_json(assert_file(f"{base}/response.json", git_ref=git_ref))
    provenance = load_json(assert_file(f"{base}/provenance.json", git_ref=git_ref))

    require(request.get("question") == QUESTIONS[index - 1], f"Query {index} text mismatch")
    require(request.get("state") == "COMPLETED", f"Query {index} not completed")
    require(int(request.get("confirmed_provider_calls", -1)) == 1, f"Query {index} provider call count")
    require(
        int(request.get("unconfirmed_provider_attempts", -1)) == 0,
        f"Query {index} unresolved provider-attempt accounting",
    )
    require(request.get("credential_material_retained") is False, f"Query {index} retained credential")
    request_id = str(request.get("request_id") or "")
    require(bool(request_id), f"Query {index} request id missing")
    context_sha = require_sha(request.get("context_sha256"), f"query {index} context")
    require_sha(request.get("knowledge_sha256"), f"query {index} knowledge")
    require_sha(request.get("response_sha256"), f"query {index} response")
    validate_context(context, context_sha, knowledge_sha, f"Query {index}")

    require(response.get("request_id") == request_id, f"Query {index} response linkage mismatch")
    require(
        response.get("classification") in EXPECTED_PRIMARY_CLASSES[index - 1],
        f"Query {index} classification mismatch",
    )
    refs = response.get("evidence_refs")
    require(isinstance(refs, list) and refs, f"Query {index} evidence refs missing")
    require(len(refs) == len(set(refs)), f"Query {index} duplicate evidence refs")
    evidence = context["evidence"]
    require(all(ref in evidence for ref in refs), f"Query {index} evidence ref not in exact context")
    require(response.get("response_sha256") == request.get("response_sha256"), f"Query {index} response SHA mismatch")

    require(provenance.get("configured_provider") == "ollama", f"Query {index} provider mismatch")
    require(provenance.get("configured_model") == PRIMARY_MODEL, f"Query {index} configured model mismatch")
    require(provenance.get("phase") == "OWNER_READ_ONLY_PROJECT_SCIENTIST", f"Query {index} phase mismatch")
    require(provenance.get("status") == "PASS", f"Query {index} provider status mismatch")
    require(provenance.get("route_source") == "SAVED_SETTINGS", f"Query {index} route source mismatch")
    require(provenance.get("credential_material_retained") is False, f"Query {index} provenance retained credential")

    answer = str(response.get("answer") or "")
    require(answer.strip() != "", f"Query {index} empty answer")
    normalized = (
        answer.replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2212", "-")
    )
    if index == 1:
        require(EXPECTED_CHAMPION in normalized, "Query 1 Champion ID missing")
    elif index == 2:
        validate_query_two_lifecycle_answer(normalized)
    elif index == 3:
        validate_query_three_read_only_answer(normalized)
    elif index == 4:
        lower = normalized.casefold()
        require("m06" in lower, "Query 4 M06 missing")
        require("non" in lower and "destructive" in lower, "Query 4 non-destructive semantics missing")
        require("not" in lower and "implement" in lower, "Query 4 not-implemented semantics missing")
    return request_id


def validate_secondary(*, knowledge_sha: str, authority_sha: str, git_ref: str | None) -> None:
    item = load_json(assert_file("evidence/m05/real_chat/secondary_gemma4.json", git_ref=git_ref))
    require(item.get("status") == "PASS", "Secondary Gemma acceptance not PASS")
    require(item.get("question") == SECONDARY_QUESTION, "Secondary question mismatch")
    request = item.get("request")
    context = item.get("context")
    response = item.get("response")
    provenance = item.get("provenance")
    require(isinstance(request, dict), "Secondary request missing")
    require(isinstance(context, dict), "Secondary context missing")
    require(isinstance(response, dict), "Secondary response missing")
    require(isinstance(provenance, dict), "Secondary provenance missing")
    require(request.get("state") == "COMPLETED", "Secondary request not completed")
    require(int(request.get("confirmed_provider_calls", -1)) == 1, "Secondary provider call count")
    require(
        int(request.get("unconfirmed_provider_attempts", -1)) == 0,
        "Secondary unresolved provider-attempt accounting",
    )
    require(provenance.get("configured_provider") == "ollama", "Secondary provider mismatch")
    require(provenance.get("configured_model") == SECONDARY_MODEL, "Secondary configured model mismatch")
    require(provenance.get("phase") == "OWNER_READ_ONLY_PROJECT_SCIENTIST", "Secondary phase mismatch")
    require(provenance.get("status") == "PASS", "Secondary provider status mismatch")
    require(response.get("classification") in EXPECTED_CLASSES, "Secondary classification invalid")
    refs = response.get("evidence_refs")
    require(isinstance(refs, list) and refs, "Secondary evidence refs missing")
    validate_context(context, str(request.get("context_sha256")), knowledge_sha, "Secondary")
    require(all(ref in context["evidence"] for ref in refs), "Secondary evidence refs invalid")
    require(item.get("structured_response") is True, "Secondary structured response failed")
    require(item.get("evidence_refs_validated_by_runtime") is True, "Secondary evidence validation missing")
    require(item.get("domain_authority_unchanged") is True, "Secondary authority mutation flag")
    actual_model = provenance.get("actual_model")
    if actual_model:
        require(
            item.get("displayed_model") == actual_model,
            "Secondary displayed model does not match actual model",
        )
    require(
        item.get("source_candidate_sha"),
        "Secondary source candidate SHA missing",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--git-ref")
    args = parser.parse_args()
    git_ref = args.git_ref

    acceptance = load_json(assert_file("evidence/m05/acceptance.json", git_ref=git_ref))
    require(acceptance.get("schema") == "MAX_REBUILD_M05_ACCEPTANCE_V2", "Wrong acceptance schema")
    require(acceptance.get("status") == "BUILDER_PASS_CONTROL_ROOM_PENDING", "M05 is not Builder PASS")
    require(acceptance.get("control_room_status") == "PENDING", "Control Room status must remain PENDING")
    require(acceptance.get("real_provider_acceptance") == "PASS", "Real provider acceptance missing")
    require(acceptance.get("responses_fabricated") is False, "Acceptance marked fabricated")
    require(acceptance.get("primary_provider") == "ollama", "Primary provider mismatch")
    require(acceptance.get("primary_model") == PRIMARY_MODEL, "Primary model mismatch")
    require(int(acceptance.get("primary_real_queries", -1)) == 4, "Primary query count mismatch")
    require(acceptance.get("secondary_model") == SECONDARY_MODEL, "Secondary model mismatch")
    require(acceptance.get("secondary_compatibility") == "PASS", "Secondary compatibility missing")
    require(acceptance.get("domain_authority_unchanged") is True, "Authority changed")
    require(acceptance.get("current_champion") == EXPECTED_CHAMPION, "Champion mismatch")
    require(acceptance.get("promotion_id") == EXPECTED_PROMOTION, "Promotion mismatch")
    require(acceptance.get("ea_version") == "2.00", "EA version mismatch")
    require(acceptance.get("baseline_sha256") == EXPECTED_BASELINE_SHA, "Baseline mismatch")
    require(int(acceptance.get("database_schema", -1)) == 6, "Database schema mismatch")
    require(acceptance.get("m06") == "BLOCKED_PENDING_CONTROL_ROOM_M05_ACCEPTANCE", "M06 gate mismatch")
    source_candidate_sha = str(acceptance.get("source_candidate_sha") or "")
    require(
        bool(re.fullmatch(r"[0-9a-f]{40}", source_candidate_sha)),
        "Source candidate SHA missing/invalid",
    )
    require(resolve_commit(source_candidate_sha) == source_candidate_sha, "Source candidate SHA did not resolve exactly")
    if git_ref:
        evidence_commit_sha = resolve_commit(git_ref)
        require(
            git("merge-base", "--is-ancestor", source_candidate_sha, evidence_commit_sha).returncode == 0,
            "Source candidate is not an ancestor of evidence commit",
        )

    knowledge = load_json(assert_file("scientist/knowledge/phase1_knowledge.json", git_ref=git_ref))
    manifest = load_value(assert_file("scientist/knowledge/source_manifest.json", git_ref=git_ref))
    require(isinstance(manifest, list), "Knowledge manifest invalid")
    require(knowledge.get("source_manifest") == manifest, "Knowledge manifest mismatch")
    require(set(knowledge.get("classification_legend", {})) == EXPECTED_CLASSES, "Classification legend mismatch")
    for item in manifest:
        require(isinstance(item, dict), "Knowledge manifest row invalid")
        source = assert_file(item["path"], git_ref=git_ref)
        require(source_sha_file(source) == item["sha256"], f"Stale knowledge source: {item['path']}")
    knowledge_sha = canonical_json_sha(knowledge)

    evidence_knowledge = load_json(assert_file("evidence/m05/knowledge/phase1_knowledge.json", git_ref=git_ref))
    evidence_manifest = load_value(assert_file("evidence/m05/knowledge/source_manifest.json", git_ref=git_ref))
    require(evidence_knowledge == knowledge, "Knowledge evidence copy mismatch")
    require(evidence_manifest == manifest, "Knowledge manifest evidence mismatch")
    kv = load_json(assert_file("evidence/m05/knowledge/verification.json", git_ref=git_ref))
    require(kv.get("status") == "READY", "Knowledge verification not READY")
    require(kv.get("knowledge_sha256") == knowledge_sha, "Knowledge SHA mismatch")

    schema = load_json(assert_file("evidence/m05/verification/schema_compatibility.json", git_ref=git_ref))
    require(schema.get("status") == "PASS", "Schema compatibility not PASS")
    for layer in ("foundation", "optimizer", "challenger", "champion", "scientist"):
        row = schema.get(layer, {})
        require(row.get("status") == "READY", f"{layer} not READY")
        require(int(row.get("schema_version", -1)) == 6, f"{layer} schema != 6")
    current = schema.get("current_champion", {})
    require(current.get("strategy_id") == EXPECTED_CHAMPION, "Schema Champion mismatch")
    require(int(current.get("current_count", -1)) == 1, "Champion count mismatch")
    require(current.get("source_challenger_status") == "PROMOTED", "Source Challenger mismatch")
    require(current.get("ea_version") == "2.00", "Schema EA version mismatch")
    require(current.get("promotion_id") == EXPECTED_PROMOTION, "Schema promotion mismatch")
    require(schema.get("baseline_sha256") == EXPECTED_BASELINE_SHA, "Schema baseline mismatch")

    provider = load_json(assert_file("evidence/m05/provider_settings/ollama_configuration.json", git_ref=git_ref))
    require(provider.get("provider_key") == "ollama", "Provider evidence mismatch")
    require(provider.get("base_url") == "http://127.0.0.1:11434/v1", "Ollama endpoint mismatch")
    require(provider.get("auth_mode") == "OLLAMA_LOCAL", "Ollama auth mismatch")
    require(provider.get("primary_model") == PRIMARY_MODEL, "Provider primary model mismatch")
    require(provider.get("credential_status") == "NOT_REQUIRED", "Ollama credential state mismatch")
    require(provider.get("secret_value_retained") is False, "Provider evidence retained secret")

    primary_connect = load_json(assert_file("evidence/m05/provider_settings/test_connection_gpt_oss.json", git_ref=git_ref))
    require(primary_connect.get("status") == "CONNECTED", "Primary Connect not CONNECTED")
    require(primary_connect.get("via_ui") is True, "Primary Connect not via UI")
    require(primary_connect.get("primary_model") == PRIMARY_MODEL, "Primary Connect model mismatch")
    require(int(primary_connect.get("model_count", 0)) >= 2, "Model discovery insufficient")
    require(primary_connect.get("secret_value_retained") is False, "Connect evidence retained secret")

    restart = load_json(assert_file("evidence/m05/provider_settings/restart_persistence.json", git_ref=git_ref))
    require(restart.get("status") == "PASS", "Restart persistence not PASS")
    require(restart.get("provider_key") == "ollama", "Restart provider mismatch")
    require(restart.get("primary_model") == PRIMARY_MODEL, "Restart primary mismatch")
    require(restart.get("route_status") == "READY", "Restart route not READY")
    require(restart.get("plaintext_secret_retained") is False, "Restart evidence retained secret")

    real = load_json(assert_file("evidence/m05/real_chat/status.json", git_ref=git_ref))
    require(real.get("schema") == "MAX_REBUILD_M05_REAL_CHAT_STATUS_V2", "Real chat schema mismatch")
    require(real.get("status") == "PASS", "Real chat not PASS")
    require(real.get("provider_call_mode") == "REAL_UI", "Real chat not UI")
    require(int(real.get("query_count", -1)) == 4, "Real chat query count mismatch")
    require(int(real.get("provider_calls", -1)) == 4, "Real provider call count mismatch")
    require(real.get("responses_fabricated") is False, "Real chat fabricated")
    require(real.get("primary_provider") == "ollama", "Real chat provider mismatch")
    require(real.get("primary_model") == PRIMARY_MODEL, "Real chat model mismatch")
    require(real.get("source_candidate_sha") == source_candidate_sha, "Real chat source SHA mismatch")
    require(real.get("domain_authority_unchanged") is True, "Real chat authority changed")

    before = load_json(assert_file("evidence/m05/real_chat/authority_before.json", git_ref=git_ref))
    after = load_json(assert_file("evidence/m05/real_chat/authority_after.json", git_ref=git_ref))
    authority_sha = require_sha(before.get("sha256"), "authority before")
    require(after.get("sha256") == authority_sha, "Domain authority fingerprint changed")
    require(real.get("domain_authority_before") == authority_sha, "Real status before SHA mismatch")
    require(real.get("domain_authority_after") == authority_sha, "Real status after SHA mismatch")

    request_ids = [
        validate_exchange(index, knowledge_sha=knowledge_sha, git_ref=git_ref)
        for index in range(1, 5)
    ]
    require(real.get("primary_request_ids") == request_ids, "Primary request IDs mismatch")

    ui = load_json(assert_file("evidence/m05/real_chat/ui_acceptance.json", git_ref=git_ref))
    require(ui.get("schema") == "MAX_REBUILD_M05_UI_ACCEPTANCE_V2", "UI acceptance schema mismatch")
    require(ui.get("status") == "PASS", "UI acceptance not PASS")
    require(ui.get("source_candidate_sha") == source_candidate_sha, "UI acceptance source SHA mismatch")
    require(ui.get("single_room_clear_verified") is True, "Single-room Clear Chat not verified")
    require(ui.get("thinking_indicator_observed") is True, "Thinking indicator not observed")
    require(ui.get("responses_fabricated") is False, "UI evidence fabricated")
    rows = ui.get("responses")
    require(isinstance(rows, list) and len(rows) == 4, "UI response count mismatch")
    require([row.get("question") for row in rows] == list(QUESTIONS), "UI question order mismatch")
    require([row.get("request_id") for row in rows] == request_ids, "UI request IDs mismatch")
    require(
        [row.get("classification") for row in rows]
        == ["EXISTING", "EXISTING", "EXISTING", "EXTENSION"],
        "UI classification contract mismatch",
    )
    require(all(row.get("configured_model") == PRIMARY_MODEL for row in rows), "UI configured model mismatch")
    require(all(row.get("actual_model") for row in rows), "UI actual model missing")
    require(
        all(row.get("displayed_model") == row.get("actual_model") for row in rows),
        "UI displayed model does not match actual model",
    )
    require(all(int(row.get("confirmed_provider_calls", 0)) == 1 for row in rows), "UI provider call count mismatch")
    require(
        all(int(row.get("unconfirmed_provider_attempts", -1)) == 0 for row in rows),
        "UI unresolved provider-attempt accounting",
    )

    validate_secondary(knowledge_sha=knowledge_sha, authority_sha=authority_sha, git_ref=git_ref)
    secondary = load_json(assert_file("evidence/m05/provider_settings/test_connection_gemma4.json", git_ref=git_ref))
    require(secondary.get("status") == "PASS", "Secondary provider evidence not PASS")
    require(secondary.get("provider") == "ollama", "Secondary provider mismatch")
    require(secondary.get("configured_model") == SECONDARY_MODEL, "Secondary model mismatch")
    if secondary.get("actual_model"):
        require(
            secondary.get("displayed_model") == secondary.get("actual_model"),
            "Secondary provider displayed model mismatch",
        )
    require(secondary.get("source_candidate_sha") == source_candidate_sha, "Secondary provider source SHA mismatch")
    require(secondary.get("structured_response") is True, "Secondary structured response failed")
    require(isinstance(secondary.get("evidence_refs"), list) and secondary["evidence_refs"], "Secondary evidence refs missing")

    for filename, needle in (
        ("backend_pytest.txt", "PYTEST_EXIT=0"),
        ("pip_check.txt", "No broken requirements found."),
        ("frontend_tests.txt", "passed"),
        ("frontend_lint.txt", "Found 0 warnings and 0 errors."),
        ("frontend_build.txt", "built in"),
        ("npm_audit.txt", "found 0 vulnerabilities"),
        ("run_max.txt", "MAX_READY"),
        ("restart.txt", "MAX_READY"),
        ("verify_m01.txt", "M01_EVIDENCE_INTEGRITY=PASS"),
        ("verify_m02.txt", "M02_EVIDENCE_INTEGRITY=PASS"),
        ("verify_m03.txt", "M03_EVIDENCE_INTEGRITY=PASS"),
        ("verify_m04.txt", "M04_EVIDENCE_INTEGRITY=PASS"),
    ):
        require_text(assert_file(f"evidence/m05/verification/{filename}", git_ref=git_ref), needle)

    secret = load_json(assert_file("evidence/m05/verification/secret_scan.json", git_ref=git_ref))
    validate_secret_scan_contract(secret, source_candidate_sha)
    chat_source = assert_file("backend/max_backend/scientist_chat.py", git_ref=git_ref)
    require_text(chat_source, "You are the read-only Scientist for MAX.")
    require_text(chat_source, "SCIENTIST_RESPONSE_EVIDENCE_INVALID")
    require_text(chat_source, "SCIENTIST_SECRET_ECHO_BLOCKED")
    require_text(chat_source, "SCIENTIST_DOMAIN_AUTHORITY_MUTATED")

    store_source = assert_file("backend/max_backend/scientist_store.py", git_ref=git_ref)
    require_text(store_source, "SCHEMA_VERSION = 6")
    require_text(store_source, "state='UNCONFIRMED'")
    require_text(store_source, "def clear_chat(")
    require_text(store_source, "SCIENTIST_CLEAR_BLOCKED_CALL_IN_FLIGHT")
    store_tree = ast.parse(store_source.read_text(encoding="utf-8"))
    store_defs = [
        node.name
        for node in store_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    duplicate_store_defs = sorted({
        name for name in store_defs
        if store_defs.count(name) > 1
    })
    require(
        duplicate_store_defs == [],
        f"Duplicate Scientist store function authority: {duplicate_store_defs}",
    )
    lifecycle_defs = (
        "get_thread",
        "list_threads",
        "list_messages",
        "prepare_request",
        "get_request",
        "complete_request",
        "fail_request",
        "recover_unconfirmed_requests",
    )
    require(
        all(store_defs.count(name) == 1 for name in lifecycle_defs),
        "Scientist lifecycle function authority count invalid",
    )

    provider_source = assert_file("backend/max_backend/scientist_provider.py", git_ref=git_ref)
    require_text(provider_source, "SCIENTIST_OLLAMA_LOCALHOST_REQUIRED")
    require_text(provider_source, "SCIENTIST_REMOTE_PRIVATE_HOST_REJECTED")
    require_text(provider_source, "CryptProtectData")

    drawer = assert_file("frontend/src/ScientistPage.tsx", git_ref=git_ref)
    drawer_text = drawer.read_text(encoding="utf-8")
    require("ReactMarkdown" in drawer_text and "remarkGfm" in drawer_text, "Markdown renderer missing")
    require("Clear Chat" in drawer_text, "Clear Chat missing")
    require("Scientist is reviewing committed evidence" in drawer_text, "Thinking UX missing")
    model_match = re.search(
        r"function assistantModel\(message: Message, selectedModel: string\) \{(?P<body>.*?)\n\}",
        drawer_text,
        re.DOTALL,
    )
    require(model_match is not None, "assistantModel function missing")
    model_body = model_match.group("body")
    actual_pos = model_body.find("message.provider_provenance?.actual_model")
    configured_pos = model_body.find("message.provider_provenance?.configured_model")
    selected_pos = model_body.find("selectedModel")
    scientist_pos = model_body.find("'Scientist'")
    require(actual_pos >= 0, "actual_model badge source missing")
    require(configured_pos > actual_pos, "configured_model precedes actual_model")
    require(selected_pos > configured_pos, "selectedModel fallback precedence invalid")
    require(scientist_pos > selected_pos, "Scientist fallback precedence invalid")
    require("classification" not in model_body, "Classification used as message badge")
    require("Scientist chat room" not in drawer_text, "Room selector returned")
    require("+ New Chat" not in drawer_text, "Multi-room New Chat returned")
    require("dangerouslySetInnerHTML" not in drawer_text, "Unsafe HTML rendering found")

    scan_source = assert_file("scripts/scan_m05_candidate_tree.py", git_ref=git_ref)
    require_text(scan_source, '"EXACT_GIT_CANDIDATE_TREE"')
    require_text(scan_source, '"ls-tree"')

    repair = load_json(assert_file("evidence/m05/verification/control_room_store_repair.json", git_ref=git_ref))
    validate_repair_evidence_contract(
        repair,
        source_candidate_sha=source_candidate_sha,
        primary_request_ids=request_ids,
        secondary_request_id=str(real.get("secondary_request_id") or ""),
        authority_sha=authority_sha,
        knowledge_sha=knowledge_sha,
    )

    settings = assert_file("frontend/src/SettingsPage.tsx", git_ref=git_ref)
    settings_text = settings.read_text(encoding="utf-8")
    require('label="Autonomous fallback · ordered"' in settings_text, "Ordered autonomous fallback UI missing")
    require('label="Scientist Chat fallback · explicit"' in settings_text, "Explicit Chat fallback UI missing")
    require("{providerSpec.description}" not in settings_text, "Legacy provider description rendered in Settings")
    require("Search discovered models" in settings_text, "Searchable model selector missing")

    css = assert_file("frontend/src/App.css", git_ref=git_ref)
    css_text = css.read_text(encoding="utf-8")
    require(".scientist-drawer .markdown-table-wrap" in css_text, "Drawer table scope missing")
    require("@media (prefers-reduced-motion: reduce)" in css_text, "Reduced-motion support missing")
    require(".scientist-layout" not in css_text, "Legacy Scientist page CSS returned")

    print("M05_EVIDENCE_INTEGRITY=PASS")
    print(f"CHAMPION_ID={EXPECTED_CHAMPION}")
    print(f"PROMOTION_ID={EXPECTED_PROMOTION}")
    print(f"BASELINE_SHA={EXPECTED_BASELINE_SHA}")
    print(f"DOMAIN_AUTHORITY_SHA256={authority_sha}")
    print(f"GIT_OBJECT_REF={git_ref or 'INDEX/WORKTREE'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EvidenceError as exc:
        print(f"M05_EVIDENCE_INTEGRITY=FAIL: {exc}")
        raise SystemExit(1)
