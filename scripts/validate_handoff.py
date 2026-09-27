#!/usr/bin/env python3
"""Profile-aware structural handoff validator for Skill Workflow projects.

A PASS proves structural handoff discipline only. It does not prove semantic
correctness of project documentation or runtime behavior.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from project_profile import (
    CONTRACT_DOCS,
    PROFILE_FILE,
    contract_settings,
    normalized_profile,
    parse_profile,
    required_docs,
    validate_profile,
)

PLACEHOLDER_PATTERNS = (
    re.compile(r"<[^>]+>"),
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bTBD\b", re.IGNORECASE),
)


def git_root(start: Path) -> Path:
    try:
        value = subprocess.check_output(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return Path(value)
    except Exception:
        return start.resolve()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def has_placeholder(text: str) -> bool:
    return any(pattern.search(text) for pattern in PLACEHOLDER_PATTERNS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--allow-placeholders", action="store_true")
    args = ap.parse_args()

    root = git_root(Path(args.root).resolve())
    failures: list[str] = []
    warnings: list[str] = []

    profile_path = root / PROFILE_FILE
    if not profile_path.is_file():
        print(f"HANDOFF_ROOT={root}")
        print(f"FAIL MISSING_REQUIRED_DOC:{PROFILE_FILE}")
        print("RESULT=FAIL failures=1 warnings=0")
        return 1

    try:
        profile_data = parse_profile(profile_path)
        profile_name = normalized_profile(profile_data)
        failures.extend(validate_profile(profile_data))
        required = required_docs(profile_data)
        settings = contract_settings(profile_data)
    except Exception as exc:
        print(f"HANDOFF_ROOT={root}")
        print(f"FAIL PROJECT_PROFILE_INVALID:{exc}")
        print("RESULT=FAIL failures=1 warnings=0")
        return 1

    print(f"HANDOFF_ROOT={root}")
    print(f"GOVERNANCE_PROFILE={profile_name}")

    for rel in sorted(required):
        path = root / rel
        if not path.is_file():
            failures.append(f"MISSING_REQUIRED_DOC:{rel}")
            continue

        text = read(path)
        if not text.strip():
            failures.append(f"EMPTY_REQUIRED_DOC:{rel}")
            continue

        if rel in {"SYMBOL_INDEX.md", "FLOW_INDEX.md"}:
            if re.search(r"^Status\s*:\s*STALE\s*$", text, re.MULTILINE | re.IGNORECASE):
                failures.append(f"STALE_INDEX:{rel}")

        if rel.endswith(".md") and not args.allow_placeholders and has_placeholder(text):
            warnings.append(f"PLACEHOLDER_TOKEN_PRESENT:{rel}")

    profile_text = read(profile_path)
    if "replace-me" in profile_text.lower():
        failures.append("PROJECT_PROFILE_REASON_NOT_SET")

    for key, value in settings.items():
        contract_doc = CONTRACT_DOCS[key]
        if value == "not_applicable" and (root / contract_doc).is_file():
            failures.append(f"NOT_APPLICABLE_DOC_PRESENT:{contract_doc}")

    current = root / "CURRENT_STATE.md"
    if current.is_file():
        text = read(current)
        for field in ("Authoritative SHA:", "Status:", "Next authorized action", "Governance profile:"):
            if field not in text:
                failures.append(f"CURRENT_STATE_FIELD_MISSING:{field}")
        match = re.search(r"^Governance profile:\s*(.*?)\s*$", text, re.MULTILINE | re.IGNORECASE)
        if not match or not match.group(1).strip():
            failures.append("CURRENT_STATE_GOVERNANCE_PROFILE_MISSING")
        elif match.group(1).strip().lower() != profile_name:
            failures.append(
                "CURRENT_STATE_PROFILE_CONFLICT:"
                + match.group(1).strip()
                + "!="
                + profile_name
            )

    manifest = root / "PROJECT_MANIFEST.md"
    if manifest.is_file():
        text = read(manifest)
        match = re.search(r"^Governance profile:\s*(.*?)\s*$", text, re.MULTILINE | re.IGNORECASE)
        if not match or not match.group(1).strip():
            failures.append("PROJECT_MANIFEST_GOVERNANCE_PROFILE_MISSING")
        elif match.group(1).strip().lower() != profile_name:
            failures.append(
                "PROJECT_MANIFEST_PROFILE_CONFLICT:"
                + match.group(1).strip()
                + "!="
                + profile_name
            )

    authority = root / "SOURCE_AUTHORITY_MAP.md"
    if authority.is_file() and "Canonical authority" not in read(authority):
        failures.append("SOURCE_AUTHORITY_MAP_STRUCTURE_INVALID")

    symbol = root / "SYMBOL_INDEX.md"
    if symbol.is_file():
        text = read(symbol)
        if "Authority SHA:" not in text:
            failures.append("SYMBOL_INDEX_AUTHORITY_SHA_MISSING")
        if "| File | Symbol |" not in text:
            failures.append("SYMBOL_INDEX_TABLE_MISSING")

    flow = root / "FLOW_INDEX.md"
    if flow.is_file():
        text = read(flow)
        if "Authority SHA:" not in text:
            failures.append("FLOW_INDEX_AUTHORITY_SHA_MISSING")
        if "Flow inventory" not in text and "| Flow |" not in text:
            failures.append("FLOW_INDEX_INVENTORY_MISSING")

    matrix = root / "TEST_ACCEPTANCE_MATRIX.md"
    if matrix.is_file():
        text = read(matrix)
        if "Evidence boundary" not in text:
            failures.append("TEST_ACCEPTANCE_EVIDENCE_BOUNDARY_MISSING")
        if "Final tested source" not in text:
            failures.append("TEST_ACCEPTANCE_TESTED_SOURCE_MISSING")

    truth = root / "PROJECT_TRUTH_SYNC.md"
    if truth.is_file():
        text = read(truth)
        if "## Truth gates" not in text:
            failures.append("PROJECT_TRUTH_GATES_MISSING")
        if "## Critical claim traceability" not in text:
            failures.append("PROJECT_TRUTH_TRACEABILITY_MISSING")
        if "PROJECT_STATE_SYNC" not in text:
            failures.append("PROJECT_TRUTH_FINAL_GATE_MISSING")

    for warning in warnings:
        print(f"WARN {warning}")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        print(f"RESULT=FAIL failures={len(failures)} warnings={len(warnings)}")
        return 1

    print(
        f"RESULT=PASS profile={profile_name} required_docs={len(required)} "
        f"failures=0 warnings={len(warnings)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
