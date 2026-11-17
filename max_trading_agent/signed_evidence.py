"""G24: signable T0 Docker evidence and strict GitHub Sigstore verification.

This module never executes candidate source on the host; G21's trusted reviewer
runs code only in explicitly invoked isolated Docker. This is provenance, NOT
mathematical proof, tool registration or broker authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from .review_package import _check_property_receipt
from .tool_factory import FactoryError, validate_candidate
from .tool_properties import review_t0_properties
from .tool_review import _digest

_REPO = "maxqstudio/max-trading-agent"
_WORKFLOW = ".github/workflows/g24-signed-t0.yml"
_SIGNER = f"{_REPO}/{_WORKFLOW}"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_NUMBER = re.compile(r"[1-9][0-9]{0,19}\Z")
_PREDICATE = "https://slsa.dev/provenance/v1"
_REQUIRED_FIELDS = frozenset({
    "schema_version", "kind", "tool_id", "tool_version",
    "candidate_sha256", "source_repository", "source_commit",
    "source_ref", "signer_workflow", "workflow_run_id", "workflow_attempt",
    "property_receipt_id", "property_receipt_sha256",
    "trusted_contract_sha256", "trusted_golden_sha256",
    "property_challenge_sha256", "challenge_seed_hex",
    "reference", "golden_cases", "property_cases",
    "status", "decision", "execution", "broker_execution",
    "owner_acceptance", "eligible_for_demo_or_live", "candidate_verified",
    "evidence_only", "attestation_verification_required",
})


def _context(env: dict) -> dict:
    """Require the scoped branch and dedicated GitHub-hosted push workflow."""
    names = ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_REF",
             "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
             "GITHUB_EVENT_NAME", "RUNNER_ENVIRONMENT")
    if any(type(env.get(name)) is not str for name in names):
        raise FactoryError("G24_UNTRUSTED_CI_CONTEXT")
    repo, sha, ref = (env["GITHUB_REPOSITORY"], env["GITHUB_SHA"],
                      env["GITHUB_REF"])
    if (repo != _REPO or not _SHA.fullmatch(sha)
            or not ref.startswith("refs/heads/feat/g24-")
            or len(ref) > 128 or not re.fullmatch(r"refs/heads/feat/g24-[a-z0-9-]+", ref)
            or env["GITHUB_WORKFLOW_REF"] != f"{_SIGNER}@{ref}"
            or env["GITHUB_EVENT_NAME"] != "push"
            or env["RUNNER_ENVIRONMENT"] != "github-hosted"
            or not _NUMBER.fullmatch(env["GITHUB_RUN_ID"])
            or not _NUMBER.fullmatch(env["GITHUB_RUN_ATTEMPT"])):
        raise FactoryError("G24_UNTRUSTED_CI_CONTEXT")
    return {
        "source_repository": repo, "source_commit": sha,
        "source_ref": ref, "signer_workflow": _SIGNER,
        "workflow_run_id": env["GITHUB_RUN_ID"],
        "workflow_attempt": env["GITHUB_RUN_ATTEMPT"],
    }


def generate_evidence(candidate: dict, env: dict) -> dict:
    """Run G21's genuine two-stage independent Docker review before evidence."""
    ci = _context(env)
    identity = validate_candidate(candidate)
    if identity["tool_id"] != "range_span_v1":
        raise FactoryError("G24_CURATED_TOOL_ONLY")
    receipt = review_t0_properties(candidate)
    if type(receipt) is not dict:
        raise FactoryError("G24_DOCKER_REVIEW_FAILED")
    # Reject forged/malformed G21 receipts and status/counter changes.
    try:
        reviewed = _check_property_receipt(candidate, receipt)
    except (FactoryError, ValueError, TypeError):
        raise FactoryError("G24_DOCKER_REVIEW_FAILED") from None
    if (reviewed["property_receipt_integrity"] != "STRUCTURAL_ONLY_UNATTESTED"
            or receipt["status"] != "T0_PROPERTY_REVIEWED"
            or receipt["golden_cases"] != 5
            or receipt["property_cases"] != 32
            or receipt["reference"] != "NUMERIC_RANGE"):
        raise FactoryError("G24_DOCKER_REVIEW_FAILED")
    evidence = {
        "schema_version": 1,
        "kind": "GITHUB_ACTIONS_ISOLATED_DOCKER_T0_EVIDENCE",
        "tool_id": identity["tool_id"], "tool_version": identity["version"],
        "candidate_sha256": identity["sha256"],
        **ci,
        "property_receipt_id": receipt["review_id"],
        "property_receipt_sha256": _digest(receipt),
        "trusted_contract_sha256": receipt["contract_sha256"],
        "trusted_golden_sha256": receipt["golden_sha256"],
        "property_challenge_sha256": receipt["property_sha256"],
        "challenge_seed_hex": receipt["challenge_seed_hex"],
        "reference": receipt["reference"],
        "golden_cases": receipt["golden_cases"],
        "property_cases": receipt["property_cases"],
        "status": "T0_DOCKER_TEST_EVIDENCE",
        "decision": "NO_TRADE", "execution": "DISABLED",
        "broker_execution": False, "owner_acceptance": "PENDING",
        "eligible_for_demo_or_live": False,
        "candidate_verified": False,
        "evidence_only": True, "attestation_verification_required": True,
    }
    return evidence


def _validate_evidence(data: dict, env: dict) -> None:
    ci = _context(env)
    if type(data) is not dict or set(data) != _REQUIRED_FIELDS:
        raise FactoryError("G24_EVIDENCE_INVALID")
    checks = {
        "schema_version": 1, "kind": "GITHUB_ACTIONS_ISOLATED_DOCKER_T0_EVIDENCE",
        **ci,
        "tool_id": "range_span_v1", "status": "T0_DOCKER_TEST_EVIDENCE",
        "reference": "NUMERIC_RANGE", "golden_cases": 5,
        "property_cases": 32, "decision": "NO_TRADE",
        "execution": "DISABLED", "broker_execution": False,
        "owner_acceptance": "PENDING", "eligible_for_demo_or_live": False,
        "candidate_verified": False, "evidence_only": True,
        "attestation_verification_required": True,
    }
    for key, expected in checks.items():
        if type(data[key]) is not type(expected) or data[key] != expected:
            raise FactoryError("G24_EVIDENCE_INVALID")
    for key in ("candidate_sha256", "property_receipt_sha256",
                "trusted_contract_sha256", "trusted_golden_sha256",
                "property_challenge_sha256", "property_receipt_id"):
        value = data[key]
        if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise FactoryError("G24_EVIDENCE_INVALID")
    if (type(data["tool_version"]) is not str or not re.fullmatch(
            r"0\.[0-9]{1,3}\.[0-9]{1,3}", data["tool_version"])
            or type(data["challenge_seed_hex"]) is not str
            or not re.fullmatch(r"[0-9a-f]{32}", data["challenge_seed_hex"])):
        raise FactoryError("G24_EVIDENCE_INVALID")


def verify_signed_evidence(path: Path, bundle: Path, env: dict) -> dict:
    """Ask gh CLI to validate Sigstore cert, signer workflow and subject.

    Never take a caller-supplied 'verified: true' JSON at face value. The CLI
    performs cryptographic verification against a trusted Sigstore root.
    """
    path = Path(path)
    bundle = Path(bundle)
    if (path.is_symlink() or bundle.is_symlink()
            or not path.is_file() or not bundle.is_file()
            or not 1 <= path.stat().st_size <= 20_000
            or not 1 <= bundle.stat().st_size <= 2_000_000):
        raise FactoryError("G24_EVIDENCE_UNAVAILABLE")
    try:
        raw = path.read_bytes()
        evidence = json.loads(raw)
        _validate_evidence(evidence, env)
    except (OSError, UnicodeError, ValueError, TypeError):
        raise FactoryError("G24_EVIDENCE_INVALID") from None
    cmd = [
        "gh", "attestation", "verify", str(path),
        "--repo", _REPO, "--signer-workflow", _SIGNER,
        "--source-digest", env["GITHUB_SHA"], "--source-ref", env["GITHUB_REF"],
        "--deny-self-hosted-runners", "--bundle", str(bundle),
        "--format", "json",
    ]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise FactoryError("G24_SIGNATURE_VERIFICATION_UNAVAILABLE") from None
    if completed.returncode != 0:
        raise FactoryError("G24_SIGNATURE_VERIFICATION_FAILED")
    try:
        verified = json.loads(completed.stdout)
        if type(verified) is not list or not verified:
            raise ValueError()
        digest = hashlib.sha256(raw).hexdigest()
        matched = False
        for entry in verified:
            item = entry["verificationResult"]
            statement = item["statement"]
            if statement["predicateType"] != _PREDICATE:
                continue
            if not item["signature"]["certificate"]:
                continue
            matched |= any(
                subject.get("name") == path.name
                and subject.get("digest", {}).get("sha256") == digest
                for subject in statement["subject"]
            )
        if not matched:
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise FactoryError("G24_VERIFIED_SUBJECT_MISMATCH") from None
    return {
        "status": "G24_SIGSTORE_PROVENANCE_VERIFIED",
        "source_commit": env["GITHUB_SHA"],
        "candidate_sha256": evidence["candidate_sha256"],
        "subject_sha256": digest,
        "signer_workflow": _SIGNER,
        "cryptographic_attestation_verified": True,
        "tool_admitted": False,
        "decision": "NO_TRADE",
        "execution": "DISABLED",
        "owner_acceptance": "PENDING",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("create", "verify"))
    parser.add_argument("--candidate")
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--bundle")
    args = parser.parse_args()
    try:
        if args.command == "create":
            if not args.candidate or args.bundle:
                raise FactoryError("G24_INVALID_CLI_ARGUMENTS")
            candidate_path = Path(args.candidate)
            if (candidate_path.is_symlink() or not candidate_path.is_file()
                    or not 1 <= candidate_path.stat().st_size <= 10_000):
                raise FactoryError("G24_INVALID_CANDIDATE_FILE")
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            document = generate_evidence(candidate, os.environ)
            output = Path(args.evidence)
            if output.is_symlink() or output.exists():
                raise FactoryError("G24_EVIDENCE_ALREADY_EXISTS")
            with output.open("x", encoding="utf-8") as handle:
                json.dump(document, handle, sort_keys=True, indent=2, allow_nan=False)
                handle.write("\n")
            result = {"status": "G24_EVIDENCE_READY_FOR_SIGNING",
                      "candidate_sha256": document["candidate_sha256"],
                      "decision": "NO_TRADE"}
        else:
            if not args.bundle or args.candidate:
                raise FactoryError("G24_INVALID_CLI_ARGUMENTS")
            result = verify_signed_evidence(Path(args.evidence),
                                            Path(args.bundle), os.environ)
    except (FactoryError, ValueError, OSError, UnicodeError):
        raise SystemExit("G24_BLOCKED") from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
