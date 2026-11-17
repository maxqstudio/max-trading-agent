"""G25 independent consumer of G24 Sigstore evidence and G23 review packets.

Only a second read-only runner (or a user independently obtaining the signed
artifact) may compare cryptographic *workflow provenance* to an existing G23
review packet. Never registers or runs model-generated code and never upgrades
mathematical correctness, owner approval or trade permissions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .review_package import assemble_review_package
from .signed_evidence import _REPO, _SIGNER, _context, verify_signed_evidence
from .tool_factory import FactoryError, validate_candidate
from .tool_properties import _no_duplicate_keys
from .tool_review import _digest, _write_receipt

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REF = re.compile(r"refs/heads/feat/g2[45]-[a-z0-9-]+\Z")
_INTEGER = re.compile(r"[1-9][0-9]{0,19}\Z")


def expected_signer_context(commit: str, ref: str, run_id: str,
                            attempt: str = "1") -> dict:
    """Explicit expected signing-run identity, NOT taken from subject JSON."""
    if (type(commit) is not str or not _SHA.fullmatch(commit)
            or type(ref) is not str or not _REF.fullmatch(ref)
            or type(run_id) is not str or not _INTEGER.fullmatch(run_id)
            or type(attempt) is not str or not _INTEGER.fullmatch(attempt)):
        raise FactoryError("G25_INVALID_EXPECTED_SOURCE")
    env = {
        "GITHUB_REPOSITORY": _REPO, "GITHUB_SHA": commit,
        "GITHUB_REF": ref, "GITHUB_WORKFLOW_REF": f"{_SIGNER}@{ref}",
        "GITHUB_RUN_ID": run_id, "GITHUB_RUN_ATTEMPT": attempt,
        "GITHUB_EVENT_NAME": "push", "RUNNER_ENVIRONMENT": "github-hosted",
    }
    _context(env)
    return env


def link_review_evidence(
    candidate: dict,
    oracle_proposal: dict,
    oracle_audit: dict,
    review_package: dict,
    property_receipt: dict,
    signed_evidence_file: Path,
    sigstore_bundle_file: Path,
    *,
    expected_commit: str,
    expected_ref: str,
    expected_run_id: str,
    expected_attempt: str = "1",
    link_dir: Path | None = None,
) -> dict:
    """Cross-check all documents and cryptographic provenance; NO_TRADE forever.

    The signing workflow is trusted only as an attested GitHub workflow claim.
    Neither its signature nor the G21 receipt mathematically proves tool code.
    """
    env = expected_signer_context(expected_commit, expected_ref,
                                  expected_run_id, expected_attempt)
    identity = validate_candidate(candidate)

    # Every G22 field, G23 review ID and G21 independent reference challenge
    # is independently recomputed from the original submitted documents.
    computed = assemble_review_package(
        candidate, oracle_proposal, oracle_audit,
        property_receipt=property_receipt,
    )
    if type(review_package) is not dict or review_package != computed:
        raise FactoryError("G25_REVIEW_PACKAGE_MISMATCH")
    if (computed["status"] != "REVIEW_PACKAGE_INCOMPLETE"
            or computed["property_receipt_integrity"] != "STRUCTURAL_ONLY_UNATTESTED"
            or computed["property_receipt_id"] != property_receipt["review_id"]
            or not computed["trusted_reference_available"]):
        raise FactoryError("G25_INDEPENDENT_REFERENCE_REQUIRED")

    path = Path(signed_evidence_file)
    bundle = Path(sigstore_bundle_file)
    if (path.is_symlink() or bundle.is_symlink()
            or not path.is_file() or not bundle.is_file()
            or not 1 <= path.stat().st_size <= 20_000
            or not 1 <= bundle.stat().st_size <= 2_000_000):
        raise FactoryError("G25_MISSING_ATTESTATION")
    try:
        raw = path.read_bytes()
        evidence = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
        if type(evidence) is not dict:
            raise ValueError()
        expected = {
            "tool_id": identity["tool_id"],
            "tool_version": identity["version"],
            "candidate_sha256": identity["sha256"],
            "property_receipt_id": property_receipt["review_id"],
            "property_receipt_sha256": _digest(property_receipt),
            "trusted_contract_sha256": property_receipt["contract_sha256"],
            "trusted_golden_sha256": property_receipt["golden_sha256"],
            "property_challenge_sha256": property_receipt["property_sha256"],
            "challenge_seed_hex": property_receipt["challenge_seed_hex"],
            "reference": property_receipt["reference"],
            "golden_cases": property_receipt["golden_cases"],
            "property_cases": property_receipt["property_cases"],
        }
        for key, value in expected.items():
            if type(evidence.get(key)) is not type(value) or evidence[key] != value:
                raise FactoryError("G25_SIGNED_RECEIPT_MISMATCH")
    except FactoryError:
        raise
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        raise FactoryError("G25_SIGNED_SUBJECT_INVALID") from None

    # This is the *actual* gh Sigstore verifier; arbitrary JSON with
    # "cryptographic_attestation_verified": true is never accepted.
    verified = verify_signed_evidence(path, bundle, env)
    digest = hashlib.sha256(raw).hexdigest()
    if (verified.get("status") != "G24_SIGSTORE_PROVENANCE_VERIFIED"
            or verified.get("cryptographic_attestation_verified") is not True
            or verified.get("subject_sha256") != digest
            or verified.get("candidate_sha256") != identity["sha256"]
            or verified.get("source_commit") != expected_commit
            or verified.get("tool_admitted") is not False
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
        raise FactoryError("G25_PROVENANCE_MISMATCH")

    link = {
        "schema_version": 1,
        "status": "SIGNED_WORKFLOW_EVIDENCE_LINKED_REVIEW_PENDING",
        "candidate_sha256": identity["sha256"],
        "tool_id": identity["tool_id"],
        "tool_version": identity["version"],
        "g23_package_review_id": computed["review_id"],
        "g23_package_sha256": _digest(computed),
        "g23_package_status": "REVIEW_PACKAGE_INCOMPLETE",
        "g21_property_receipt_id": property_receipt["review_id"],
        "g21_property_receipt_sha256": _digest(property_receipt),
        "g24_signed_subject_sha256": digest,
        "g24_sigstore_bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "signer_workflow": _SIGNER,
        "source_commit": expected_commit,
        "source_ref": expected_ref,
        "source_run_id": expected_run_id,
        "source_run_attempt": expected_attempt,
        "verified_provenance_scope": "GITHUB_WORKFLOW_SIGNED_CLAIM_ONLY",
        "cryptographic_attestation_verified": True,
        "signed_claim_matches_g23_and_g21": True,
        "mathematical_correctness_proven": False,
        "independent_code_review_completed": False,
        "missing_evidence": ["INDEPENDENT_CODE_AND_HUMAN_REVIEW", "OWNER_ACCEPTANCE"],
        "evidence_only": True,
        "candidate_verified": False,
        "trusted_oracle_adopted": False,
        "tool_admitted": False,
        "decision": "NO_TRADE",
        "execution": "DISABLED",
        "broker_execution": False,
        "eligible_for_demo_or_live": False,
        "owner_acceptance": "PENDING",
        "limitations": (
            "A Sigstore signature authenticates workflow-produced claims and file "
            "digest, not arbitrary mathematical truth, independence of the code "
            "author or safety of the workflow. The immutable G23 packet remains "
            "incomplete; no broker execution or registry admission occurs."
        ),
    }
    link["review_id"] = _digest(link)
    if link_dir is not None:
        _write_receipt(link, Path(link_dir))
    return link


def _read_json_file(filename: str, max_bytes: int = 20_000) -> dict:
    path = Path(filename)
    if (path.is_symlink() or not path.is_file()
            or not 1 <= path.stat().st_size <= max_bytes):
        raise FactoryError("G25_INVALID_FILE")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"),
                         object_pairs_hook=_no_duplicate_keys)
        if type(obj) is not dict:
            raise ValueError()
        return obj
    except (OSError, UnicodeError, ValueError, TypeError):
        raise FactoryError("G25_INVALID_FILE") from None


def main() -> None:
    parser = argparse.ArgumentParser(prog="g25-link-signed-evidence")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--oracle", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-ref", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-attempt", default="1")
    parser.add_argument("--link-dir", default=".local/tool-factory/signed-links")
    args = parser.parse_args()
    try:
        candidate = _read_json_file(args.candidate)
        oracle = _read_json_file(args.oracle)
        audit = _read_json_file(args.audit)
        package = _read_json_file(args.package)
        receipt = _read_json_file(args.receipt)
        link = link_review_evidence(
            candidate, oracle, audit, package, receipt,
            Path(args.evidence), Path(args.bundle),
            expected_commit=args.expected_commit,
            expected_ref=args.expected_ref,
            expected_run_id=args.expected_run_id,
            expected_attempt=args.expected_attempt,
            link_dir=Path(args.link_dir),
        )
    except (FactoryError, OSError, ValueError, UnicodeError):
        raise SystemExit("G25_BLOCKED") from None
    print(json.dumps(link, sort_keys=True))
    # Linked provenance must NEVER be mistaken for tool or trading approval.
    raise SystemExit(3)


if __name__ == "__main__":
    main()
