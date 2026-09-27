#!/usr/bin/env python3
"""Shared PROJECT_PROFILE.yaml parser and governance rules.

No PyYAML dependency is required. The accepted YAML subset is intentionally
small: top-level scalar keys and one-level mappings with scalar values.
"""

from __future__ import annotations

from pathlib import Path

PROFILE_FILE = "PROJECT_PROFILE.yaml"

PROFILE_REQUIRED = {
    "lite": {
        PROFILE_FILE,
        "PROJECT_MANIFEST.md",
        "CURRENT_STATE.md",
        "MODULE_MAP.md",
        "TEST_ACCEPTANCE_MATRIX.md",
    },
    "standard": {
        PROFILE_FILE,
        "PROJECT_MANIFEST.md",
        "CURRENT_STATE.md",
        "SOURCE_AUTHORITY_MAP.md",
        "ARCHITECTURE.md",
        "WORKFLOW_STATE_MACHINE.md",
        "MODULE_MAP.md",
        "SYMBOL_INDEX.md",
        "FLOW_INDEX.md",
        "TEST_ACCEPTANCE_MATRIX.md",
        "DOC_SYNC_MATRIX.md",
    },
    "strict": {
        PROFILE_FILE,
        "PROJECT_MANIFEST.md",
        "CURRENT_STATE.md",
        "SOURCE_AUTHORITY_MAP.md",
        "ARCHITECTURE.md",
        "WORKFLOW_STATE_MACHINE.md",
        "MODULE_MAP.md",
        "SYMBOL_INDEX.md",
        "FLOW_INDEX.md",
        "TEST_ACCEPTANCE_MATRIX.md",
        "DOC_SYNC_MATRIX.md",
        "PROJECT_TRUTH_SYNC.md",
    },
}

CONTRACT_DOCS = {
    "api_contracts": "API_CONTRACTS.md",
    "data_contracts": "DATA_CONTRACTS.md",
    "ui_information_architecture": "UI_INFORMATION_ARCHITECTURE.md",
    "runbook": "RUNBOOK.md",
    "decisions": "DECISIONS.md",
    "known_defects": "KNOWN_DEFECTS.md",
    "glossary": "GLOSSARY.md",
    "changelog": "CHANGELOG.md",
}

VALID_CONTRACT_VALUES = {"required", "optional", "not_applicable"}
STRICT_EXPLICIT_CONTRACTS = {
    "api_contracts",
    "data_contracts",
    "ui_information_architecture",
    "runbook",
    "decisions",
    "known_defects",
}


def _clean(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.strip()


def parse_profile(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)

    data: dict[str, object] = {}
    current_section: str | None = None

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if ":" not in stripped:
            raise ValueError(f"Unsupported PROJECT_PROFILE syntax: {raw}")

        key, value = stripped.split(":", 1)
        key = key.strip()
        value = _clean(value)

        if indent == 0:
            if value == "":
                current_section = key
                data[current_section] = {}
            else:
                current_section = None
                data[key] = value
        elif indent == 2 and current_section:
            section = data.setdefault(current_section, {})
            if not isinstance(section, dict):
                raise ValueError(f"Invalid section collision: {current_section}")
            section[key] = value
        else:
            raise ValueError(
                "PROJECT_PROFILE supports only top-level keys and one-level "
                f"2-space mappings; invalid line: {raw}"
            )

    return data


def normalized_profile(data: dict) -> str:
    value = str(data.get("profile", "standard")).strip().lower()
    if value not in PROFILE_REQUIRED:
        raise ValueError(f"Unknown governance profile: {value}")
    return value


def contract_settings(data: dict) -> dict[str, str]:
    raw = data.get("contracts", {})
    if not isinstance(raw, dict):
        raise ValueError("contracts must be a mapping")

    result: dict[str, str] = {}
    for key in CONTRACT_DOCS:
        value = str(raw.get(key, "optional")).strip().lower()
        if value not in VALID_CONTRACT_VALUES:
            raise ValueError(f"Invalid contract setting {key}: {value}")
        result[key] = value
    return result


def _bool_value(value: object, key: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    raise ValueError(f"Invalid boolean setting {key}: {value}")


def runtime_settings(data: dict) -> dict[str, bool]:
    raw = data.get("runtime", {})
    if not isinstance(raw, dict):
        raise ValueError("runtime must be a mapping")
    return {
        "e2e_required": _bool_value(raw.get("e2e_required", "true"), "runtime.e2e_required"),
    }


def required_docs(data: dict) -> set[str]:
    profile = normalized_profile(data)
    required = set(PROFILE_REQUIRED[profile])
    settings = contract_settings(data)
    for key, value in settings.items():
        if value == "required":
            required.add(CONTRACT_DOCS[key])
    return required


def validate_profile(data: dict) -> list[str]:
    failures: list[str] = []
    try:
        profile = normalized_profile(data)
    except ValueError as exc:
        return [str(exc)]

    try:
        settings = contract_settings(data)
        runtime_settings(data)
    except ValueError as exc:
        return [str(exc)]

    if profile == "strict":
        for key in sorted(STRICT_EXPLICIT_CONTRACTS):
            if settings[key] == "optional":
                failures.append(
                    f"STRICT_PROFILE_REQUIRES_EXPLICIT_APPLICABILITY:{key}:"
                    "set required or not_applicable"
                )

    return failures
