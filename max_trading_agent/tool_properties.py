"""G21: reviewer-owned T0 numerical contracts and randomized properties.

Research-only. Candidate code is executed exclusively by the existing isolated
Docker runner. Reference implementations and oracle catalog are trusted,
repository-reviewed code/data, never provided by a candidate or LLM.
"""
from __future__ import annotations

import json
import math
import random
import secrets
from pathlib import Path

from .tool_factory import FactoryError, independent_cases, sandbox_evaluate, validate_candidate
from .tool_review import _digest, _safe_passed, _write_receipt

_CONTRACTS = Path(__file__).resolve().parent.parent / "knowledge" / "tool_review_contracts.json"
_ORACLES = Path(__file__).resolve().parent.parent / "knowledge" / "tool_factory_oracles.json"
_REFERENCES = frozenset({"ARITHMETIC_MEAN", "NUMERIC_RANGE"})


def _no_duplicate_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate contract key")
        obj[key] = value
    return obj


def _contract(tool_id: str) -> dict | None:
    """Fail closed on malformed repository-trusted catalog, including duplicates."""
    try:
        if _CONTRACTS.is_symlink() or not 1 <= _CONTRACTS.stat().st_size <= 8192:
            raise ValueError("invalid contract file")
        items = json.loads(_CONTRACTS.read_text(encoding="utf-8"),
                           object_pairs_hook=_no_duplicate_keys)
        if not isinstance(items, dict) or not 1 <= len(items) <= 32:
            raise ValueError("invalid contracts")
        for name, spec in items.items():
            if (not isinstance(name, str) or not 3 <= len(name) <= 48
                    or not name[0].islower() or not all(
                        ch.islower() or ch.isdigit() or ch == "_" for ch in name)
                    or type(spec) is not dict
                    or set(spec) != {"schema_version", "reference"}
                    or type(spec["schema_version"]) is not int
                    or spec["schema_version"] != 1
                    or spec["reference"] not in _REFERENCES):
                raise ValueError("invalid contract entry")
        return items.get(tool_id)
    except (OSError, UnicodeError, ValueError, TypeError):
        raise FactoryError("TRUSTED_CONTRACT_INVALID") from None


def _reference(kind: str, values: list[float]) -> float:
    if kind == "ARITHMETIC_MEAN":
        return sum(values) / len(values)
    if kind == "NUMERIC_RANGE":
        return max(values) - min(values)
    raise FactoryError("TRUSTED_CONTRACT_INVALID")


def _validate_goldens(kind: str, cases: list[dict]) -> None:
    """Do not accept even a trusted-file typo as an independent expected value."""
    for case in cases:
        expected = _reference(kind, case["input"])
        if not math.isclose(float(case["expected"]), expected,
                            rel_tol=1e-9, abs_tol=1e-9):
            raise FactoryError("TRUSTED_ORACLE_MISMATCH")


def _property_cases(kind: str, seed: int) -> list[dict]:
    """Reviewer-owned deterministic replay given a private fresh seed.

    Each independent reference outcome covers base, permutation,
    additive translation and positive scaling. 32 bounded tests total.
    """
    if type(seed) is not int or not 0 <= seed < 2**128:
        raise FactoryError("INVALID_CHALLENGE_SEED")
    rng = random.Random(seed)
    out = []
    for size in (1, 2, 3, 5, 8, 13, 21, 31):
        values = [rng.randrange(-100_000, 100_001) / 1000
                  for _ in range(size)]
        reordered = list(values)
        rng.shuffle(reordered)
        offset = rng.randrange(-10000, 10001) / 100
        multiplier = rng.randrange(2, 6)
        variations = (
            values, reordered, [x + offset for x in values],
            [x * multiplier for x in values],
        )
        out.extend({"input": case, "expected": _reference(kind, case)}
                   for case in variations)
    return out


def review_t0_properties(candidate: dict, *, review_dir: Path | None = None,
                         seed: int | None = None) -> dict:
    """Require a vetted numerical contract and both isolated Docker stages.

    The optional seed is for trusted tests/replay; CLI never exposes it.
    Runtime uses fresh OS entropy. Receipts are immutable per review, not a
    callable registry or permission grant.
    """
    identity = validate_candidate(candidate)
    report = {
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
    contract = _contract(identity["tool_id"])
    if contract is None:
        return {**report, "status": "PENDING_INDEPENDENT_ORACLE"}
    golden = independent_cases(identity["tool_id"], _ORACLES)
    if golden is None:
        return {**report, "status": "PENDING_INDEPENDENT_ORACLE"}
    kind = contract["reference"]
    _validate_goldens(kind, golden)
    if seed is None:
        seed = secrets.randbits(128)
    challenge = _property_cases(kind, seed)

    for stage, cases in (("BASELINE", golden), ("PROPERTIES", challenge)):
        outcome = sandbox_evaluate(candidate, cases)
        if (not _safe_passed(outcome, identity["sha256"])
                or type(outcome.get("oracle_cases")) is not int
                or outcome["oracle_cases"] != len(cases)):
            return {**report, "status": "T0_PROPERTY_REJECTED",
                    "failed_stage": stage}
    receipt = {
        **report,
        "status": "T0_PROPERTY_REVIEWED",
        "evidence_only": True,
        "reviewer": "TRUSTED_PROPERTY_CONTRACT_V1",
        "contract_sha256": _digest(contract),
        "reference": kind,
        "golden_sha256": _digest(golden),
        "property_sha256": _digest(challenge),
        "golden_cases": len(golden),
        "property_cases": len(challenge),
        "property_checks": ["permutation", "translation", "positive_scale"],
        "challenge_seed_hex": f"{seed:032x}",
        "limitations": (
            "Reviewer-owned mean/range only. Randomized cases are finite "
            "samples; disclosed seed permits replay, not universal proof. "
            "No runtime registration, trading access or owner acceptance."
        ),
    }
    receipt["review_id"] = _digest(receipt)
    if review_dir is not None:
        _write_receipt(receipt, review_dir)
    return receipt
