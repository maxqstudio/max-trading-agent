from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load_script("build_m05_final_evidence")
verifier = load_script("verify_m05_evidence")


def test_query_three_classification_gate_is_strict_existing() -> None:
    assert [allowed for _, allowed in builder.PRIMARY] == [
        {"EXISTING"},
        {"EXISTING"},
        {"EXISTING"},
        {"EXTENSION"},
    ]
    assert verifier.EXPECTED_PRIMARY_CLASSES == (
        {"EXISTING"},
        {"EXISTING"},
        {"EXISTING"},
        {"EXTENSION"},
    )


def test_query_two_lifecycle_gate_returns_former_source_to_active_challengers() -> None:
    verifier.validate_query_two_lifecycle_answer(
        "The prior Champion tenure becomes FORMER historical authority, its "
        "source returns as an ACTIVE CHALLENGER, and the selected strategy "
        "becomes the new Strategy Champion."
    )
    with pytest.raises(verifier.EvidenceError, match="must return to active Challenger"):
        verifier.validate_query_two_lifecycle_answer(
            "The prior Champion tenure becomes FORMER historical authority, "
            "its source remains PROMOTED but is not an ACTIVE CHALLENGER, and "
            "the selected strategy becomes the new Champion."
        )


def test_query_three_semantic_gate_accepts_explicit_equivalent_denial() -> None:
    verifier.validate_query_three_read_only_answer(
        "No. The Scientist role is read-only and promotion is not permitted."
    )
    with pytest.raises(verifier.EvidenceError, match="explicit denial"):
        verifier.validate_query_three_read_only_answer(
            "The Scientist role is read-only."
        )


def good_secret_scan(candidate_sha: str) -> dict:
    return {
        "status": "PASS",
        "scope": "EXACT_GIT_CANDIDATE_TREE",
        "candidate_sha": candidate_sha,
        "scanned_tracked_text_files": 10,
        "matches": 0,
        "high_confidence_matches": [],
        "dpapi_or_settings_file_tracked": False,
        "forbidden_secret_storage_tracked": [],
        "plaintext_secret_in_db": False,
        "plaintext_secret_in_settings_json": False,
        "plaintext_secret_in_frontend": False,
    }


def test_secret_scan_contract_rejects_non_candidate_scope_and_sha() -> None:
    candidate = "a" * 40
    stale_scope = good_secret_scan(candidate)
    stale_scope["scope"] = "OWNER_PC_ACCEPTANCE_WORKTREE"
    with pytest.raises(verifier.EvidenceError, match="exact candidate tree"):
        verifier.validate_secret_scan_contract(stale_scope, candidate)

    stale_sha = good_secret_scan("b" * 40)
    with pytest.raises(verifier.EvidenceError, match="candidate SHA mismatch"):
        verifier.validate_secret_scan_contract(stale_sha, candidate)

    verifier.validate_secret_scan_contract(good_secret_scan(candidate), candidate)


def good_repair_evidence(candidate_sha: str) -> dict:
    return {
        "schema": "MAX_REBUILD_M05_CONTROL_ROOM_FINAL_REPAIR_EVIDENCE_V2",
        "repository": "maxqstudio/max_rebuild",
        "branch": "work/m05-control-room-store-repair",
        "source_candidate_sha": candidate_sha,
        "not_final_acceptance_authority": False,
        "primary_request_ids": ["q1", "q2", "q3", "q4"],
        "secondary_request_id": "q5",
        "protected_domain_authority_sha256": "d" * 64,
        "knowledge_sha256": "k" * 64,
        "real_requests": [
            {"unconfirmed_provider_attempts": 0}
            for _ in range(5)
        ],
    }


def test_repair_evidence_rejects_stale_candidate_and_request_generation() -> None:
    candidate = "a" * 40
    kwargs = {
        "source_candidate_sha": candidate,
        "primary_request_ids": ["q1", "q2", "q3", "q4"],
        "secondary_request_id": "q5",
        "authority_sha": "d" * 64,
        "knowledge_sha": "k" * 64,
    }

    stale_sha = good_repair_evidence("b" * 40)
    with pytest.raises(verifier.EvidenceError, match="source SHA mismatch"):
        verifier.validate_repair_evidence_contract(stale_sha, **kwargs)

    stale_ids = good_repair_evidence(candidate)
    stale_ids["primary_request_ids"] = ["old1", "old2", "old3", "old4"]
    with pytest.raises(verifier.EvidenceError, match="primary request IDs mismatch"):
        verifier.validate_repair_evidence_contract(stale_ids, **kwargs)

    verifier.validate_repair_evidence_contract(
        good_repair_evidence(candidate),
        **kwargs,
    )
