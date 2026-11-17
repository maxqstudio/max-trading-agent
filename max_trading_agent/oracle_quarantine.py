"""G22: untrusted LLM oracle hypotheses are quarantined, never independent proof.

No candidate Python is imported or executed here. No source text is promoted into
trusted G21 contracts, goldens, runtime registry, policy, or broker permissions.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

from .tool_factory import FactoryError, _number, validate_candidate
from .tool_properties import _contract, _reference, _no_duplicate_keys
from .tool_review import _digest, _write_receipt

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_TEXT = re.compile(r"[ -~]{12,300}\Z")
_PROPERTIES = frozenset({
    "PERMUTATION_INVARIANT",
    "TRANSLATION_EQUIVARIANT",
    "TRANSLATION_INVARIANT",
    "POSITIVE_SCALE_EQUIVARIANT",
    "NON_NEGATIVE",
})
_KNOWN_PROPERTIES = {
    "ARITHMETIC_MEAN": frozenset({
        "PERMUTATION_INVARIANT", "TRANSLATION_EQUIVARIANT",
        "POSITIVE_SCALE_EQUIVARIANT",
    }),
    "NUMERIC_RANGE": frozenset({
        "PERMUTATION_INVARIANT", "TRANSLATION_INVARIANT",
        "POSITIVE_SCALE_EQUIVARIANT", "NON_NEGATIVE",
    }),
}


def _validate_hypothesis(pack: Any, identity: dict) -> list[tuple[float, ...]]:
    if (type(pack) is not dict
            or set(pack) != {"tool_id", "candidate_sha256", "hypothesis",
                             "cases", "properties"}
            or pack["tool_id"] != identity["tool_id"]
            or type(pack["candidate_sha256"]) is not str
            or not _HEX.fullmatch(pack["candidate_sha256"])
            or pack["candidate_sha256"] != identity["sha256"]):
        raise FactoryError("ORACLE_CANDIDATE_MISMATCH")
    if type(pack["hypothesis"]) is not str or not _TEXT.fullmatch(pack["hypothesis"]):
        raise FactoryError("INVALID_ORACLE_HYPOTHESIS")
    props = pack["properties"]
    if (type(props) is not list or len(props) > 5
            or any(type(x) is not str or x not in _PROPERTIES for x in props)
            or len(set(props)) != len(props)):
        raise FactoryError("INVALID_ORACLE_PROPERTIES")
    cases = pack["cases"]
    if type(cases) is not list or not 3 <= len(cases) <= 12:
        raise FactoryError("INVALID_ORACLE_CASES")
    seen = set()
    signatures = []
    for row in cases:
        if type(row) is not dict or set(row) != {"input", "expected"}:
            raise FactoryError("INVALID_ORACLE_CASES")
        values = row["input"]
        if type(values) is not list or not 1 <= len(values) <= 128:
            raise FactoryError("INVALID_ORACLE_CASES")
        signature = tuple(_number(v) for v in values)
        _number(row["expected"])
        if signature in seen:
            raise FactoryError("DUPLICATE_ORACLE_INPUT")
        seen.add(signature)
        signatures.append(signature)
    return signatures


def _safe_record(identity: dict, pack_sha: str) -> dict:
    return {
        "tool_id": identity["tool_id"],
        "candidate_sha256": identity["sha256"],
        "oracle_hypothesis_sha256": pack_sha,
        "tier": "T0_QUARANTINE_ONLY",
        "decision": "NO_TRADE",
        "execution": "DISABLED",
        "broker_execution": False,
        "owner_acceptance": "PENDING",
        "eligible_for_demo_or_live": False,
        "trusted_oracle_adopted": False,
        "candidate_verified": False,
        "evidence_only": True,
    }


def _write_proposal(pack: dict, root: Path, digest: str) -> None:
    """Content-addressed, non-overwriting private storage, not trusted fixtures."""
    root = Path(root)
    if root.is_symlink():
        raise FactoryError("UNSAFE_ORACLE_DIRECTORY")
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink():
            raise FactoryError("UNSAFE_ORACLE_DIRECTORY")
        path = root / (digest + ".json")
        if path.is_symlink():
            raise FactoryError("UNSAFE_ORACLE_PATH")
        raw = json.dumps(pack, sort_keys=True, indent=2,
                         allow_nan=False).encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise FactoryError("ORACLE_PROPOSAL_CONFLICT")
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
    except FactoryError:
        raise
    except OSError:
        raise FactoryError("ORACLE_STORAGE_UNAVAILABLE") from None


def propose_oracle_hypothesis(candidate: dict, client: Any) -> dict:
    """One opt-in provider call; intentionally withhold candidate code/examples.

    This reduces copying but DOES NOT make model hypotheses independent evidence.
    """
    identity = validate_candidate(candidate)
    prompt = json.dumps({
        "task": "Propose untrusted numerical oracle HYPOTHESES for a PURE tool.",
        "tool_id": identity["tool_id"],
        "candidate_sha256": identity["sha256"],
        "purpose": candidate["purpose"],
        "interface": "analyze(values: list[number]) -> number",
        "output_schema": {
            "tool_id": identity["tool_id"],
            "candidate_sha256": identity["sha256"],
            "hypothesis": "ASCII factual description (12-300 characters)",
            "cases": [{"input": [1, 2, 3], "expected": 2}],
            "properties": ["PERMUTATION_INVARIANT"],
        },
        "allowed_properties": sorted(_PROPERTIES),
        "restrictions": [
            "Exactly one JSON object, max 4500 characters",
            "3-12 distinct input/expected cases, each input 1-128 finite numbers",
            "No Python, source code, file paths, URLs, credentials or trade orders",
            "A hypothesis is quarantined and NEVER validates itself",
            "Do not claim independent proofs, tool approval or trading permission",
        ],
    }, separators=(",", ":"))
    try:
        answer = client.complete("Oracle Hypothesis Author (UNTRUSTED)", prompt)
        if type(answer) is not str or len(answer) > 4500:
            raise ValueError()
        pack = json.loads(answer, object_pairs_hook=_no_duplicate_keys)
    except (ValueError, TypeError, AttributeError, KeyError):
        raise FactoryError("MODEL_ORACLE_SPEC_INVALID") from None
    _validate_hypothesis(pack, identity)
    return pack


def quarantine_hypothesis(candidate: dict, pack: dict, directory: Path) -> dict:
    identity = validate_candidate(candidate)
    _validate_hypothesis(pack, identity)
    digest = _digest(pack)
    _write_proposal(pack, Path(directory), digest)
    return {
        **_safe_record(identity, digest),
        "status": "QUARANTINED_UNVERIFIED",
        "proposal_filename": digest + ".json",
        "model_proposal_is_independent_evidence": False,
    }


def audit_hypothesis(candidate: dict, pack: dict,
                     *, audit_dir: Path | None = None) -> dict:
    """Offline structural and known-reference audit only: ALWAYS quarantined."""
    identity = validate_candidate(candidate)
    signatures = _validate_hypothesis(pack, identity)
    proposal_digest = _digest(pack)
    examples = {tuple(_number(v) for v in row["input"])
                for row in candidate["examples"]}
    novel = sum(signature not in examples for signature in signatures)
    contract = _contract(identity["tool_id"])  # repository-trusted, not from pack
    reference_kind = contract["reference"] if contract is not None else None

    mismatch = False
    if reference_kind is not None:
        expected_props = _KNOWN_PROPERTIES[reference_kind]
        if not set(pack["properties"]).issubset(expected_props):
            mismatch = True
        for row in pack["cases"]:
            expectation = _reference(reference_kind, row["input"])
            if not math.isclose(float(row["expected"]), expectation,
                                rel_tol=1e-9, abs_tol=1e-9):
                mismatch = True
    if novel < 3:
        status = "QUARANTINED_INSUFFICIENT_COVERAGE"
    elif mismatch:
        status = "QUARANTINED_REFERENCE_MISMATCH"
    elif reference_kind is None:
        status = "QUARANTINED_UNVERIFIED"
    else:
        status = "QUARANTINED_REFERENCE_CONSISTENT"

    report = {
        **_safe_record(identity, proposal_digest),
        "status": status,
        "trusted_reference_checked": reference_kind is not None,
        "reference": reference_kind,
        "hypothesis_cases": len(signatures),
        "novel_cases_vs_candidate_examples": novel,
        "property_claims": list(pack["properties"]),
        "trusted_contract_sha256": _digest(contract) if contract else None,
        "model_proposal_is_independent_evidence": False,
        "reviewer": "STRUCTURAL_QUARANTINE_AUDIT_V1",
        "limitations": (
            "LLM cases and property claims are not independent test oracles. "
            "Known-reference consistency never admits candidate code. Novel "
            "mathematical functions require independently reviewed reference "
            "code and golden test cases before G21 property review."
        ),
    }
    report["review_id"] = _digest(report)
    if audit_dir is not None:
        _write_receipt(report, audit_dir)
    return report
