"""G23: offline, content-addressed T0 evidence packet for independent review.

Reproduces G22 audit findings and G21 receipt *integrity* without importing or
executing any candidate code. An unsigned receipt does NOT attest sandbox
execution. All outputs remain non-executable, incomplete and NO_TRADE.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .oracle_quarantine import audit_hypothesis
from .tool_factory import FactoryError, independent_cases, validate_candidate
from .tool_properties import (
    _CONTRACTS, _ORACLES, _contract, _property_cases, _validate_goldens,
)
from .tool_review import _digest, _write_receipt

_SEED_HEX = re.compile(r"[0-9a-f]{32}\Z")
_PROPERTY_FIELDS = frozenset({
    "tool_id", "version", "candidate_sha256", "tier", "decision", "execution",
    "broker_execution", "eligible_for_demo_or_live", "owner_acceptance",
    "status", "evidence_only", "reviewer", "contract_sha256", "reference",
    "golden_sha256", "property_sha256", "golden_cases", "property_cases",
    "property_checks", "challenge_seed_hex", "limitations", "review_id",
})


def _check_property_receipt(candidate: dict, receipt: Any) -> dict:
    """Validate JSON consistency only, NOT the origin or execution of a receipt."""
    identity = validate_candidate(candidate)
    if type(receipt) is not dict or set(receipt) != _PROPERTY_FIELDS:
        raise FactoryError("PROPERTY_RECEIPT_INVALID")
    contract = _contract(identity["tool_id"])
    if contract is None:
        raise FactoryError("PROPERTY_REFERENCE_MISSING")
    golden = independent_cases(identity["tool_id"], _ORACLES)
    if golden is None:
        raise FactoryError("PROPERTY_REFERENCE_MISSING")
    kind = contract["reference"]
    _validate_goldens(kind, golden)
    seed = receipt.get("challenge_seed_hex")
    if type(seed) is not str or not _SEED_HEX.fullmatch(seed):
        raise FactoryError("PROPERTY_RECEIPT_INVALID")
    expected = {
        "tool_id": identity["tool_id"], "version": identity["version"],
        "candidate_sha256": identity["sha256"], "tier": "T0_RESEARCH_ONLY",
        "decision": "NO_TRADE", "execution": "DISABLED",
        "broker_execution": False, "eligible_for_demo_or_live": False,
        "owner_acceptance": "PENDING", "status": "T0_PROPERTY_REVIEWED",
        "evidence_only": True, "reviewer": "TRUSTED_PROPERTY_CONTRACT_V1",
        "contract_sha256": _digest(contract), "reference": kind,
        "golden_sha256": _digest(golden),
        "property_sha256": _digest(_property_cases(kind, int(seed, 16))),
        "golden_cases": len(golden), "property_cases": 32,
        "property_checks": ["permutation", "translation", "positive_scale"],
        "challenge_seed_hex": seed,
    }
    for key, value in expected.items():
        if type(receipt[key]) is not type(value) or receipt[key] != value:
            raise FactoryError("PROPERTY_RECEIPT_MISMATCH")
    if (type(receipt["limitations"]) is not str
            or not 20 <= len(receipt["limitations"]) <= 500
            or type(receipt["review_id"]) is not str
            or receipt["review_id"] != _digest({
                key: value for key, value in receipt.items()
                if key != "review_id"
            })):
        raise FactoryError("PROPERTY_RECEIPT_MISMATCH")
    return {
        "property_receipt_sha256": _digest(receipt),
        "property_receipt_id": receipt["review_id"],
        "property_receipt_integrity": "STRUCTURAL_ONLY_UNATTESTED",
        "property_receipt_reference": kind,
    }


def assemble_review_package(
    candidate: dict,
    oracle_proposal: dict,
    oracle_audit: dict,
    *,
    property_receipt: dict | None = None,
    package_dir: Path | None = None,
) -> dict:
    """Collect a non-authorizing review manifest; never execute candidate code.

    Every G22 audit field is independently recomputed and compared. A G21
    receipt is checked against repository-owned reference material and its
    seed, but this cannot authenticate that an isolated Docker run occurred.
    """
    identity = validate_candidate(candidate)
    expected_audit = audit_hypothesis(candidate, oracle_proposal)
    if type(oracle_audit) is not dict or oracle_audit != expected_audit:
        raise FactoryError("ORACLE_AUDIT_MISMATCH")
    contract = _contract(identity["tool_id"])
    property_info = {
        "property_receipt_sha256": None,
        "property_receipt_id": None,
        "property_receipt_integrity": "ABSENT",
        "property_receipt_reference": None,
    }
    if property_receipt is not None:
        property_info = _check_property_receipt(candidate, property_receipt)
    missing = []
    if contract is None:
        missing.extend(("INDEPENDENT_REFERENCE_CONTRACT",
                        "INDEPENDENT_GOLDEN_ORACLES"))
    if property_receipt is None:
        missing.append("T0_PROPERTY_REVIEW_RECEIPT")
    missing.extend(("SIGNED_SANDBOX_EXECUTION_ATTESTATION",
                    "INDEPENDENT_HUMAN_REVIEW",
                    "OWNER_ACCEPTANCE"))
    report = {
        "schema_version": 1,
        "status": "REVIEW_PACKAGE_INCOMPLETE",
        "tool_id": identity["tool_id"],
        "tool_version": identity["version"],
        "candidate_sha256": identity["sha256"],
        "purpose_sha256": _digest(candidate["purpose"]),
        "oracle_hypothesis_sha256": _digest(oracle_proposal),
        "oracle_audit_sha256": _digest(expected_audit),
        "oracle_audit_id": expected_audit["review_id"],
        "oracle_audit_status": expected_audit["status"],
        "trusted_contract_sha256": _digest(contract) if contract else None,
        "trusted_reference_available": contract is not None,
        **property_info,
        "missing_evidence": missing,
        "package_is_execution_attestation": False,
        "candidate_verified": False,
        "trusted_oracle_adopted": False,
        "tier": "T0_EVIDENCE_PACKAGE_ONLY",
        "evidence_only": True,
        "decision": "NO_TRADE",
        "execution": "DISABLED",
        "broker_execution": False,
        "eligible_for_demo_or_live": False,
        "owner_acceptance": "PENDING",
        "limitations": (
            "The packet verifies document consistency only. Neither a "
            "replayed audit nor a hash-consistent unsigned G21 receipt "
            "proves that Docker executed, reference ownership was reviewed, "
            "or the candidate is mathematically correct or trading-safe."
        ),
    }
    report["review_id"] = _digest(report)
    if package_dir is not None:
        _write_receipt(report, Path(package_dir))
    return report
