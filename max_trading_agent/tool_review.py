"""Independent, opt-in T0-only PURE Tool Factory review.

Only the trusted fixed oracle and a second reviewer-owned known-contract challenge
can yield a write-once *non-executable* receipt. A receipt is not a tool registry,
broker capability, deployment approval or proof of general functional correctness.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from pathlib import Path

from .tool_factory import (
    FactoryError, independent_cases, sandbox_evaluate, validate_candidate,
)

_ORACLES = Path(__file__).resolve().parent.parent / "knowledge" / "tool_factory_oracles.json"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def _known_challenge(tool_id: str, candidate_sha: str) -> list[dict] | None:
    """Trusted correctness oracle for ONE known contract; never LLM examples.

    Deterministic and public, NOT a secret or proof against adaptive cheating.
    Novel tools require a separately reviewed independent oracle implementation.
    """
    if tool_id != "rolling_mean_v1":
        return None
    rng = random.Random(int(candidate_sha, 16))
    cases = []
    for size in (1, 2, 3, 5, 8, 13, 21, 31):
        values = [rng.randrange(-100_000, 100_001) / 1000 for _ in range(size)]
        cases.append({"input": values, "expected": sum(values) / len(values)})
    return cases


def _safe_passed(result: dict, candidate_sha: str) -> bool:
    return (type(result) is dict
            and result.get("status") == "SANDBOX_PASSED_REVIEW_REQUIRED"
            and result.get("sha256") == candidate_sha
            and result.get("tier") == "T0_PROPOSED"
            and result.get("execution") == "DISABLED"
            and result.get("decision") == "NO_TRADE"
            and result.get("broker_execution") is False)


def _write_receipt(report: dict, root: Path) -> None:
    """Write once, never mutate runtime registry or policy files."""
    root = Path(root)
    if root.is_symlink():
        raise FactoryError("UNSAFE_REVIEW_DIRECTORY")
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink():
            raise FactoryError("UNSAFE_REVIEW_DIRECTORY")
        path = root / (report["review_id"] + ".json")
        if path.is_symlink():
            raise FactoryError("UNSAFE_REVIEW_PATH")
        raw = json.dumps(report, sort_keys=True, indent=2,
                         allow_nan=False).encode("utf-8")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise FactoryError("REVIEW_RECEIPT_CONFLICT")
        else:
            with os.fdopen(fd, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
    except FactoryError:
        raise
    except OSError:
        raise FactoryError("REVIEW_STORAGE_UNAVAILABLE") from None


def review_t0(candidate: dict, *, review_dir: Path | None = None) -> dict:
    """Run two independent Docker oracle batches before issuing T0-only evidence."""
    identity = validate_candidate(candidate)
    result = {
        "tool_id": identity["tool_id"],
        "version": identity["version"],
        "candidate_sha256": identity["sha256"],
        "tier": "T0_RESEARCH_ONLY",
        "decision": "NO_TRADE",
        "execution": "DISABLED",
        "broker_execution": False,
        "eligible_for_demo_or_live": False,
        "owner_acceptance": "PENDING",
    }
    baseline = independent_cases(identity["tool_id"], _ORACLES)
    challenge = _known_challenge(identity["tool_id"], identity["sha256"])
    if baseline is None or challenge is None:
        return {**result, "status": "PENDING_INDEPENDENT_ORACLE"}
    baseline_sha = _digest(baseline)
    challenge_sha = _digest(challenge)
    for stage, cases in (("BASELINE", baseline), ("CHALLENGE", challenge)):
        outcome = sandbox_evaluate(candidate, cases)
        if not _safe_passed(outcome, identity["sha256"]):
            return {**result, "status": "T0_REVIEW_REJECTED",
                    "failed_stage": stage}
    receipt = {
        **result,
        "status": "T0_SANDBOX_REVIEWED",
        "oracle_sha256": baseline_sha,
        "challenge_sha256": challenge_sha,
        "oracle_cases": len(baseline),
        "challenge_cases": len(challenge),
        "reviewer": "INDEPENDENT_FIXED_KNOWN_CONTRACT_V1",
        "evidence_only": True,
        "limitations": (
            "Public deterministic challenges are not secret holdouts. "
            "Known PURE rolling mean only. No runtime tool registration, "
            "statistical completeness or trade authority."
        ),
    }
    receipt["review_id"] = _digest(receipt)
    if review_dir is not None:
        _write_receipt(receipt, review_dir)
    return receipt
