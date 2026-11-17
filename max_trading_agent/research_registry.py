"""G27: repository-owned offline numeric reference catalog + disabled T1 inbox.

Never import/execute a model-proposed tool. Built-in operations refer only to
G21's reviewer-owned functions and separately reviewed golden cases. The
staging inbox records SHA IDs, NOT code or executable registry entries.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from .tool_factory import (
    FactoryError, _number, independent_cases, validate_candidate,
)
from .tool_properties import (
    _ORACLES, _contract, _reference, _validate_goldens, _no_duplicate_keys,
)
from .tool_review import _digest, _write_receipt

# Explicit implementation allowlist, independent of data/LLM input. Adding a
# JSON contract does NOT automatically load or register a generated algorithm.
_STATIC = {
    "rolling_mean_v1": "ARITHMETIC_MEAN",
    "range_span_v1": "NUMERIC_RANGE",
}


def _built_in_spec(tool_id: str) -> dict:
    if type(tool_id) is not str or tool_id not in _STATIC:
        raise FactoryError("R0_REFERENCE_NOT_REGISTERED")
    trusted = _contract(tool_id)
    if (type(trusted) is not dict
            or trusted.get("schema_version") != 1
            or trusted.get("reference") != _STATIC[tool_id]):
        raise FactoryError("R0_TRUSTED_REFERENCE_CHANGED")
    cases = independent_cases(tool_id, _ORACLES)
    if cases is None:
        raise FactoryError("R0_GOLDENS_MISSING")
    _validate_goldens(trusted["reference"], cases)
    return {
        "tool_id": tool_id,
        "reference": trusted["reference"],
        "trusted_contract_sha256": _digest(trusted),
        "trusted_golden_sha256": _digest(cases),
        "golden_cases": len(cases),
        "source": "REPOSITORY_OWNED_STATIC_REFERENCE",
        "mode": "R0_BUILTIN_OFFLINE_ONLY",
        "candidate_code_loaded": False,
    }


def list_references() -> dict:
    items = [_built_in_spec(name) for name in sorted(_STATIC)]
    return {
        "status": "R0_BUILTIN_REFERENCES_AVAILABLE",
        "entries": items,
        "generated_t1_tool_registration": "DISABLED",
        "decision": "NO_TRADE",
        "broker_execution": False,
        "owner_acceptance": "PENDING",
    }


def evaluate_reference(tool_id: str, values: Any) -> dict:
    """Only reviewed built-in numerical semantics, no candidate source."""
    spec = _built_in_spec(tool_id)
    if type(values) is not list or not 1 <= len(values) <= 128:
        raise FactoryError("R0_INVALID_VALUES")
    finite_values = [_number(x) for x in values]
    result = _reference(spec["reference"], finite_values)
    if not math.isfinite(result) or abs(result) > 1e9:
        raise FactoryError("R0_RESULT_OUT_OF_BOUNDS")
    return {
        "status": "R0_TRUSTED_REFERENCE_EVALUATED",
        **spec,
        "input_sha256": _digest(values),
        "input_count": len(values),
        "result": result,
        "deterministic_built_in_calculation": True,
        "generated_candidate_executed": False,
        "generated_t1_tool_registration": "DISABLED",
        "execution": "BUILTIN_REFERENCE_ONLY",
        "broker_execution": False,
        "eligible_for_demo_or_live": False,
        "decision": "NO_TRADE",
        "owner_acceptance": "PENDING",
    }


def stage_untrusted_candidate(candidate: dict, *, directory: Path | None = None) -> dict:
    """Prepare review inbox metadata, never store or import candidate code."""
    identity = validate_candidate(candidate)
    receipt = {
        "schema_version": 1,
        "status": "T1_CANDIDATE_NOT_REGISTERED",
        "tool_id": identity["tool_id"],
        "tool_version": identity["version"],
        "candidate_sha256": identity["sha256"],
        "purpose_sha256": _digest(candidate["purpose"]),
        "required_gates": [
            "INDEPENDENT_SOURCE_CODE_AND_HUMAN_REVIEW",
            "G25_AUTHENTICATED_SIGNED_CANDIDATE_EVIDENCE",
            "G26_SHA_SCOPED_OWNER_ACCEPTANCE",
            "MERGED_PRODUCTION_POLICY_AND_REGISTRY_REVIEW",
        ],
        "mode": "REVIEW_INBOX_ONLY",
        "candidate_source_stored": False,
        "candidate_source_executed": False,
        "generated_t1_tool_registration": "DISABLED",
        "candidate_verified": False,
        "tool_admitted": False,
        "evidence_only": True,
        "execution": "DISABLED",
        "broker_execution": False,
        "eligible_for_demo_or_live": False,
        "owner_acceptance": "PENDING",
        "decision": "NO_TRADE",
        "limitations": (
            "Staging the candidate SHA is not approval or executable "
            "registration. Real source-code review and owner acceptance "
            "must be independently completed before any future T1 loader."
        ),
    }
    receipt["review_id"] = _digest(receipt)
    if directory is not None:
        _write_receipt(receipt, Path(directory))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(prog="r0-research-registry")
    parser.add_argument("command", choices=("list", "evaluate", "stage"))
    parser.add_argument("--tool-id", help="Explicit built-in read-only reference name")
    parser.add_argument("--values", help="JSON list of 1-128 finite numbers")
    parser.add_argument("--file", help="PURE candidate JSON for non-executable staging")
    parser.add_argument("--stage-dir", default=".local/tool-factory/t1-pending")
    args = parser.parse_args()
    try:
        if args.command == "list":
            if any((args.tool_id, args.values, args.file)):
                raise FactoryError("R0_INVALID_ARGUMENTS")
            result = list_references()
        elif args.command == "evaluate":
            if not args.tool_id or not args.values or args.file:
                raise FactoryError("R0_INVALID_ARGUMENTS")
            if len(args.values) > 6000:
                raise FactoryError("R0_INVALID_VALUES")
            values = json.loads(args.values, object_pairs_hook=_no_duplicate_keys)
            result = evaluate_reference(args.tool_id, values)
        else:
            if not args.file or args.tool_id or args.values:
                raise FactoryError("R0_INVALID_ARGUMENTS")
            source = Path(args.file)
            if (source.is_symlink() or not source.is_file()
                    or not 1 <= source.stat().st_size <= 10_000):
                raise FactoryError("R0_INVALID_CANDIDATE_FILE")
            candidate = json.loads(source.read_text(encoding="utf-8"),
                                   object_pairs_hook=_no_duplicate_keys)
            result = stage_untrusted_candidate(
                candidate, directory=Path(args.stage_dir))
    except (FactoryError, OSError, UnicodeError, ValueError, TypeError):
        raise SystemExit("R0_REGISTRY_BLOCKED") from None
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    if args.command == "stage":
        raise SystemExit(3)  # a staged candidate never has runtime authority


if __name__ == "__main__":
    main()
