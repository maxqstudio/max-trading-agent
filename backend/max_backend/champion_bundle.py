from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .challenger_bundle import (
    _same_number,
    _same_params,
    current_baseline_sha256,
    validate_full_params,
)
from .config import EA_BASELINE
from .mtf_geometry import frozen_geometry_inputs
from .optimizer_core import (
    ABSOLUTE_BOUNDS,
    CURRENT_OPTIMIZER_SCHEMA,
    FIXED_INPUTS,
    LEGACY_OPTIMIZER_SCHEMAS,
    LEGACY_FIXED_INPUTS,
    OPTIMIZER_METRICS_CSV,
    optimizer_parameter_bounds_for_keys,
    parse_set_optimizer_entries,
    read_ea_optimizer_defaults,
    sha256_file,
)


def _sha(path: Path) -> str:
    return sha256_file(path)


def _parse_simple_set(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or "=" not in line:
            continue
        name, rhs = line.split("=", 1)
        values[name.strip()] = rhs.strip()
    return values


def _format_set_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def _fixed_inputs_for_request(source_request: dict[str, Any]) -> dict[str, Any]:
    schema = str(source_request.get("schema") or "")
    if schema == CURRENT_OPTIMIZER_SCHEMA:
        return dict(FIXED_INPUTS)
    if schema in LEGACY_OPTIMIZER_SCHEMAS:
        return dict(LEGACY_FIXED_INPUTS)
    raise RuntimeError(f"CHAMPION_OPTIMIZER_SCHEMA_UNSUPPORTED:{schema}")


def _daily_loss_is_promoted_fixed_input(source_request: dict[str, Any]) -> bool:
    schema = str(source_request.get("schema") or "")
    if schema == CURRENT_OPTIMIZER_SCHEMA:
        return True
    if schema in LEGACY_OPTIMIZER_SCHEMAS:
        return False
    raise RuntimeError(f"CHAMPION_OPTIMIZER_SCHEMA_UNSUPPORTED:{schema}")


def write_champion_set(
    path: Path,
    source_request: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    validated = validate_full_params(params)
    bounds = optimizer_parameter_bounds_for_keys(validated)
    lines = [
        "; MAX Rebuild Strategy Champion",
        "; fixed Owner-promoted point; optimization disabled",
    ]
    for name in bounds:
        value = _format_set_value(validated[name])
        lines.append(f"{name}={value}||{value}||0||{value}||N")

    fixed = _fixed_inputs_for_request(source_request)
    fixed.update(frozen_geometry_inputs(source_request))
    fixed["InpConfirmSymbol"] = source_request["relative_symbol"]
    fixed["InpOptimizerMetricsFile"] = OPTIMIZER_METRICS_CSV
    fixed["InpOptimizerRunNonce"] = 0
    for name, value in fixed.items():
        lines.append(f"{name}={_format_set_value(value)}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return verify_champion_set(path, validated, source_request)


def verify_champion_set(
    path: Path,
    params: dict[str, Any],
    source_request: dict[str, Any],
) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError("CHAMPION_SET_MISSING")
    validated = validate_full_params(params)
    bounds = optimizer_parameter_bounds_for_keys(validated)
    text = path.read_text(encoding="utf-8")
    parsed = parse_set_optimizer_entries(text, bounds=bounds)
    if set(parsed) != set(bounds):
        raise RuntimeError("CHAMPION_SET_PARAM_UNIVERSE_MISMATCH")
    for name in bounds:
        if parsed[name]["optimize"] != "N":
            raise RuntimeError(f"CHAMPION_SET_OPTIMIZATION_FLAG_ACTIVE:{name}")
        if not _same_number(parsed[name]["value"], validated[name]):
            raise RuntimeError(f"CHAMPION_SET_PARAM_MISMATCH:{name}")

    simple = _parse_simple_set(text)
    expected_fixed = _fixed_inputs_for_request(source_request)
    expected_fixed.update(frozen_geometry_inputs(source_request))
    expected_fixed["InpConfirmSymbol"] = source_request["relative_symbol"]
    expected_fixed["InpOptimizerMetricsFile"] = OPTIMIZER_METRICS_CSV
    expected_fixed["InpOptimizerRunNonce"] = 0
    for name, expected in expected_fixed.items():
        raw = simple.get(name)
        if raw is None:
            raise RuntimeError(f"CHAMPION_SET_FIXED_INPUT_MISSING:{name}")
        expected_text = (
            "true"
            if expected is True
            else "false"
            if expected is False
            else str(expected)
        )
        if str(raw).lower() != expected_text.lower():
            try:
                if not _same_number(raw, expected):
                    raise RuntimeError(
                        f"CHAMPION_SET_FIXED_INPUT_MISMATCH:{name}"
                    )
            except Exception as exc:
                raise RuntimeError(
                    f"CHAMPION_SET_FIXED_INPUT_MISMATCH:{name}"
                ) from exc

    return {
        "status": "VERIFIED",
        "sha256": _sha(path),
        "parameter_count": len(parsed),
        "all_optimizer_flags": "N",
        "fixed_runtime_inputs": "VERIFIED",
        "allow_live_trading_semantics": (
            "STRATEGY_TESTER_EXECUTION_ONLY_NOT_LIVE_AUTHORIZATION"
        ),
    }


def verify_strategy_logic_unchanged(
    champion_ea: Path,
    *,
    params: dict[str, Any] | None = None,
    source_request: dict[str, Any] | None = None,
    baseline: Path = EA_BASELINE,
) -> dict[str, Any]:
    if _sha(baseline) != current_baseline_sha256():
        raise RuntimeError("BASELINE_EA_SHA_MISMATCH")
    before = baseline.read_text(encoding="utf-8")
    after = champion_ea.read_text(encoding="utf-8")
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    if len(before_lines) != len(after_lines):
        raise RuntimeError("CHAMPION_EA_NONWHITELISTED_STRUCTURE_CHANGE")

    bounds = (
        optimizer_parameter_bounds_for_keys(params)
        if isinstance(params, dict)
        else ABSOLUTE_BOUNDS
    )
    allowed_changes = list(bounds)
    if isinstance(source_request, dict) and _daily_loss_is_promoted_fixed_input(source_request):
        allowed_changes.append("InpMaxDailyLossPct")

    changed: list[str] = []
    for line_no, (left, right) in enumerate(
        zip(before_lines, after_lines, strict=True),
        start=1,
    ):
        if left == right:
            continue
        matches = [
            name
            for name in allowed_changes
            if re.search(rf"\b{re.escape(name)}\b", left)
            and re.search(r"^\s*input\s+(?:double|int)\s+", left)
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"CHAMPION_EA_NONWHITELISTED_CHANGE_LINE:{line_no}"
            )
        changed.append(matches[0])

    baseline_normalized = before
    champion_normalized = after
    for name in allowed_changes:
        pattern = re.compile(
            rf"(^\s*input\s+(?:double|int)\s+{re.escape(name)}\s*=\s*)"
            rf"([-+0-9.eE]+)(\s*;.*$)",
            re.MULTILINE,
        )
        baseline_normalized = pattern.sub(r"\g<1><VALUE>\g<3>", baseline_normalized)
        champion_normalized = pattern.sub(r"\g<1><VALUE>\g<3>", champion_normalized)

    baseline_logic_sha = hashlib.sha256(
        baseline_normalized.encode("utf-8")
    ).hexdigest()
    champion_logic_sha = hashlib.sha256(
        champion_normalized.encode("utf-8")
    ).hexdigest()
    if baseline_logic_sha != champion_logic_sha:
        raise RuntimeError("CHAMPION_STRATEGY_LOGIC_CHANGED")
    return {
        "status": "VERIFIED",
        "baseline_sha256": _sha(baseline),
        "champion_sha256": _sha(champion_ea),
        "strategy_logic_sha256": champion_logic_sha,
        "changed_optimizer_defaults": sorted(set(changed)),
        "non_whitelisted_changes": 0,
    }


def verify_champion_parity(
    champion_ea: Path,
    champion_set: Path,
    params: dict[str, Any],
    source_request: dict[str, Any],
) -> dict[str, Any]:
    expected = validate_full_params(params)
    bounds = optimizer_parameter_bounds_for_keys(expected)
    ea_params = read_ea_optimizer_defaults(champion_ea, bounds=bounds)
    if not _same_params(expected, ea_params):
        raise RuntimeError("CHAMPION_EA_PARAM_PARITY_FAILURE")
    set_result = verify_champion_set(
        champion_set,
        expected,
        source_request,
    )
    set_entries = parse_set_optimizer_entries(
        champion_set.read_text(encoding="utf-8"),
        bounds=bounds,
    )
    set_params = {
        name: set_entries[name]["value"]
        for name in bounds
    }
    if not _same_params(expected, set_params):
        raise RuntimeError("CHAMPION_SET_PARAM_PARITY_FAILURE")
    if not _same_params(ea_params, set_params):
        raise RuntimeError("CHAMPION_EA_SET_PARITY_FAILURE")
    logic = verify_strategy_logic_unchanged(
        champion_ea,
        params=expected,
        source_request=source_request,
    )
    return {
        "status": "VERIFIED",
        "parameter_count": len(bounds),
        "challenger_to_champion_params": True,
        "champion_params_to_ea": True,
        "champion_params_to_project_set": True,
        "ea_to_project_set": True,
        "all_optimizer_flags": "N",
        "set_sha256": set_result["sha256"],
        "ea_sha256": _sha(champion_ea),
        "strategy_logic": logic,
    }


def write_hash_manifest(
    root: Path,
    *,
    schema: str,
    identity: dict[str, Any],
    manifest_name: str = "manifest.json",
) -> dict[str, Any]:
    manifest_path = root / manifest_name
    files = []
    for path in sorted(
        item
        for item in root.rglob("*")
        if item.is_file() and item.resolve() != manifest_path.resolve()
    ):
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha(path),
                "size": path.stat().st_size,
            }
        )
    payload = {
        "schema": schema,
        **identity,
        "file_count": len(files),
        "files": files,
    }
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "payload": payload,
        "path": manifest_path,
        "sha256": _sha(manifest_path),
    }


def verify_hash_manifest(root: Path, manifest_name: str = "manifest.json") -> dict[str, Any]:
    manifest_path = root / manifest_name
    if not manifest_path.is_file():
        raise RuntimeError("MANIFEST_MISSING")
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError("MANIFEST_INVALID")
    seen: set[str] = set()
    for item in payload.get("files", []):
        rel = str(item["path"])
        path = root / rel
        if not path.is_file():
            raise RuntimeError(f"MANIFEST_FILE_MISSING:{rel}")
        if _sha(path) != str(item["sha256"]):
            raise RuntimeError(f"MANIFEST_HASH_MISMATCH:{rel}")
        if path.stat().st_size != int(item["size"]):
            raise RuntimeError(f"MANIFEST_SIZE_MISMATCH:{rel}")
        seen.add(rel)
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.resolve() != manifest_path.resolve()
    }
    if actual != seen:
        raise RuntimeError("MANIFEST_FILE_SET_MISMATCH")
    if len(seen) != int(payload.get("file_count", -1)):
        raise RuntimeError("MANIFEST_COUNT_MISMATCH")
    return {
        "status": "VERIFIED",
        "manifest_sha256": _sha(manifest_path),
        "file_count": len(seen),
        "files": {item["path"]: item["sha256"] for item in payload["files"]},
    }
