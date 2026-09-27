from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path
from typing import Any

from .challenger_store import REGISTERING_STATUS, VISIBLE_STATUS, get_challenger
from .config import (
    CHALLENGER_ARTIFACT_ROOT,
    DATABASE_PATH,
    EA_BASELINE,
    LEGACY_CHALLENGER_ARTIFACT_ROOT,
    ROOT,
)
from .ea import verify_baseline_snapshot
from .mtf_geometry import (
    STRATEGY_CONTRACT,
    assert_geometry_matches_main,
    frozen_geometry_inputs,
)
from .workflow_contract import (
    ROLE_OPTIMIZER_WINNER,
    ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE,
)
from .optimizer_core import (
    ABSOLUTE_BOUNDS,
    CURRENT_DAILY_LOSS_LIMIT_PCT,
    CURRENT_OPTIMIZER_SCHEMA,
    LEGACY_OPTIMIZER_SCHEMAS,
    FAMILY_WEIGHT_PARAMS,
    optimizer_parameter_bounds_for_keys,
    parse_set_optimizer_entries,
    read_ea_optimizer_defaults,
    sha256_file,
)

def current_baseline_sha256() -> str:
    return str(verify_baseline_snapshot()["snapshot_sha256"])
WINNER_RANKING = [
    "Weighted R DESC",
    "Mean R DESC",
    "Profit Factor DESC",
    "Recovery Factor DESC",
    "MT5 pass ASC",
]

def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise RuntimeError(f"INVALID_JSON_EVIDENCE:{path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"INVALID_JSON_OBJECT:{path}")
    return value


def _sha(path: Path) -> str:
    return sha256_file(path)


def _same_number(a: Any, b: Any, tolerance: float = 1e-9) -> bool:
    try:
        aa = float(a)
        bb = float(b)
    except Exception:
        return False
    return (
        math.isfinite(aa)
        and math.isfinite(bb)
        and abs(aa - bb) <= tolerance * max(1.0, abs(aa), abs(bb))
    )


def _same_params(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if set(a) != set(b):
        return False
    try:
        bounds = optimizer_parameter_bounds_for_keys(a)
    except ValueError:
        return False
    for name, (_lo, _hi, _step, kind) in bounds.items():
        if kind == "int":
            try:
                if int(a[name]) != int(b[name]):
                    return False
            except Exception:
                return False
        elif not _same_number(a[name], b[name]):
            return False
    return True


def validate_full_params(params: Any) -> dict[str, int | float]:
    if not isinstance(params, dict):
        raise RuntimeError("CHALLENGER_PARAMS_NOT_OBJECT")
    try:
        bounds = optimizer_parameter_bounds_for_keys(params)
    except ValueError as exc:
        raise RuntimeError(f"CHALLENGER_PARAMS_INCOMPLETE:{exc}") from exc
    result: dict[str, int | float] = {}
    for name, (lo, hi, _step, kind) in bounds.items():
        try:
            value = float(params[name])
        except Exception as exc:
            raise RuntimeError(f"CHALLENGER_PARAM_INVALID:{name}") from exc
        if not math.isfinite(value):
            raise RuntimeError(f"CHALLENGER_PARAM_NONFINITE:{name}")
        if value < lo - 1e-12 or value > hi + 1e-12:
            raise RuntimeError(f"CHALLENGER_PARAM_OUT_OF_BOUND:{name}")
        if name in FAMILY_WEIGHT_PARAMS and value <= 0:
            raise RuntimeError(f"CHALLENGER_FAMILY_WEIGHT_NONPOSITIVE:{name}")
        if kind == "int":
            if abs(value - round(value)) > 1e-9:
                raise RuntimeError(f"CHALLENGER_INTEGER_PARAM_INVALID:{name}")
            result[name] = int(round(value))
        else:
            result[name] = float(value)
    return result

def _format_value(name: str, value: Any) -> str:
    kind = ABSOLUTE_BOUNDS[name][3]
    if kind == "int":
        return str(int(value))
    return f"{float(value):.10g}"


def _new_daily_loss_authority(source_request: dict[str, Any] | None) -> float | None:
    if not isinstance(source_request, dict):
        return None
    schema = str(source_request.get("schema") or "")
    if schema in LEGACY_OPTIMIZER_SCHEMAS:
        return None
    if schema != CURRENT_OPTIMIZER_SCHEMA:
        raise RuntimeError(f"CHALLENGER_OPTIMIZER_SCHEMA_UNSUPPORTED:{schema}")
    authority = source_request.get("fixed_execution_authority")
    if not isinstance(authority, dict):
        raise RuntimeError("CHALLENGER_FIXED_EXECUTION_AUTHORITY_MISSING")
    value = float(authority.get("InpMaxDailyLossPct"))
    if not math.isfinite(value) or abs(value - CURRENT_DAILY_LOSS_LIMIT_PCT) > 1e-12:
        raise RuntimeError("CHALLENGER_DAILY_LOSS_AUTHORITY_INVALID")
    return value


def _ea_input_number(text: str, name: str) -> float:
    match = re.search(
        rf"^\s*input\s+(?:double|int)\s+{re.escape(name)}\s*=\s*([-+0-9.eE]+)\s*;",
        text,
        re.MULTILINE,
    )
    if match is None:
        raise RuntimeError(f"EA_INPUT_DECLARATION_MISSING:{name}")
    return float(match.group(1))


def apply_params_to_challenger_ea(
    baseline: Path,
    destination: Path,
    params: dict[str, Any],
    source_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validated = validate_full_params(params)
    bounds = optimizer_parameter_bounds_for_keys(validated)
    daily_loss = _new_daily_loss_authority(source_request)
    baseline_text = baseline.read_bytes().decode("utf-8", errors="strict")
    text = baseline_text
    changed: list[str] = []
    for name in bounds:
        pattern = re.compile(
            rf"(^\s*input\s+(?:double|int)\s+{re.escape(name)}\s*=\s*)"
            rf"([-+0-9.eE]+)(\s*;.*$)",
            re.MULTILINE,
        )
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise RuntimeError(f"EA_DEFAULT_DECLARATION_COUNT_INVALID:{name}")
        replacement = rf"\g<1>{_format_value(name, validated[name])}\g<3>"
        updated, count = pattern.subn(replacement, text, count=1)
        if count != 1:
            raise RuntimeError(f"EA_DEFAULT_REPLACEMENT_FAILED:{name}")
        if updated != text:
            changed.append(name)
        text = updated

    if daily_loss is not None:
        daily_pattern = re.compile(
            r"(^\s*input\s+double\s+InpMaxDailyLossPct\s*=\s*)"
            r"([-+0-9.eE]+)(\s*;.*$)",
            re.MULTILINE,
        )
        updated, count = daily_pattern.subn(
            rf"\g<1>{daily_loss:.10g}\g<3>",
            text,
            count=1,
        )
        if count != 1:
            raise RuntimeError("EA_DAILY_LOSS_DEFAULT_REPLACEMENT_FAILED")
        if updated != text:
            changed.append("InpMaxDailyLossPct")
        text = updated

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(text.encode("utf-8"))
    embedded = read_ea_optimizer_defaults(destination, bounds=bounds)
    if not _same_params(embedded, validated):
        raise RuntimeError("CHALLENGER_EA_PARAM_PARITY_FAILURE")

    baseline_lines = baseline_text.splitlines()
    challenger_lines = text.splitlines()
    if len(baseline_lines) != len(challenger_lines):
        raise RuntimeError("CHALLENGER_EA_NONWHITELISTED_STRUCTURE_CHANGE")
    unexpected: list[int] = []
    allowed_changed: list[str] = []
    for index, (before, after) in enumerate(
        zip(baseline_lines, challenger_lines, strict=True),
        start=1,
    ):
        if before == after:
            continue
        allowed = list(bounds)
        if daily_loss is not None:
            allowed.append("InpMaxDailyLossPct")
        names = [
            name
            for name in allowed
            if re.search(
                rf"\b{re.escape(name)}\b",
                before,
            )
            and re.search(r"^\s*input\s+(?:double|int)\s+", before)
        ]
        if len(names) != 1:
            unexpected.append(index)
        else:
            allowed_changed.append(names[0])
    if unexpected:
        raise RuntimeError(
            f"CHALLENGER_EA_NONWHITELISTED_CHANGE_LINES:{unexpected}"
        )
    return {
        "baseline_sha256": _sha(baseline),
        "challenger_sha256": _sha(destination),
        "changed_parameter_defaults": sorted(set(allowed_changed)),
        "unchanged_parameter_defaults": sorted(
            set(bounds) - set(allowed_changed)
        ),
        "fixed_daily_loss_percent": daily_loss,
        "non_whitelisted_changes": 0,
    }


def _parse_simple_set(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or "=" not in line:
            continue
        name, rhs = line.split("=", 1)
        result[name.strip()] = rhs.strip()
    return result


def _format_fixed_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def verify_geometry_set(path: Path, source_request: dict[str, Any]) -> dict[str, Any]:
    expected = frozen_geometry_inputs(source_request)
    simple = _parse_simple_set(path.read_text(encoding="utf-8"))
    for name, value in expected.items():
        actual = simple.get(name)
        if actual is None:
            raise RuntimeError(f"CHALLENGER_SET_MTF_INPUT_MISSING:{name}")
        if str(actual) != _format_fixed_value(value):
            raise RuntimeError(f"CHALLENGER_SET_MTF_INPUT_MISMATCH:{name}")
    confirm = simple.get("InpConfirmSymbol")
    if confirm != str(source_request["relative_symbol"]):
        raise RuntimeError("CHALLENGER_SET_RELATIVE_SYMBOL_MISMATCH")
    daily_loss = _new_daily_loss_authority(source_request)
    if daily_loss is not None:
        actual = simple.get("InpMaxDailyLossPct")
        if actual is None or not _same_number(actual, daily_loss):
            raise RuntimeError("CHALLENGER_SET_DAILY_LOSS_MISMATCH")
    return {
        "strategy_contract": STRATEGY_CONTRACT,
        "strategy_geometry": dict(source_request["strategy_geometry"]),
        "geometry_inputs": expected,
        "relative_symbol": confirm,
        "fixed_daily_loss_percent": daily_loss,
    }


def write_challenger_set(
    path: Path,
    params: dict[str, Any],
    source_request: dict[str, Any],
) -> dict[str, Any]:
    validated = validate_full_params(params)
    assert_geometry_matches_main(
        dict(source_request["strategy_geometry"]),
        str(source_request["period"]),
    )
    bounds = optimizer_parameter_bounds_for_keys(validated)
    lines = [
        "; MAX Rebuild Strategy Challenger",
        "; fixed winner values; no optimization sweep",
        "; TRUE_MTF_DYNAMIC_V1 geometry is immutable Challenger lineage",
    ]
    for name in bounds:
        value = _format_value(name, validated[name])
        lines.append(f"{name}={value}||{value}||0||{value}||N")
    fixed = frozen_geometry_inputs(source_request)
    fixed["InpConfirmSymbol"] = source_request["relative_symbol"]
    daily_loss = _new_daily_loss_authority(source_request)
    if daily_loss is not None:
        fixed["InpMaxDailyLossPct"] = daily_loss
    for name, value in fixed.items():
        lines.append(f"{name}={_format_fixed_value(value)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    parsed = parse_set_optimizer_entries(
        path.read_text(encoding="utf-8"),
        bounds=bounds,
    )
    if set(parsed) != set(bounds):
        raise RuntimeError("CHALLENGER_SET_PARAM_UNIVERSE_MISMATCH")
    for name in bounds:
        if parsed[name]["optimize"] != "N":
            raise RuntimeError(f"CHALLENGER_SET_OPTIMIZATION_FLAG_INVALID:{name}")
        if not _same_number(parsed[name]["value"], validated[name]):
            raise RuntimeError(f"CHALLENGER_SET_PARAM_MISMATCH:{name}")
    geometry = verify_geometry_set(path, source_request)
    return {
        "set_sha256": _sha(path),
        "all_optimizer_flags": "N",
        "parameter_count": len(parsed),
        **geometry,
    }

def verify_parameter_parity(
    ea_path: Path,
    set_path: Path,
    params: dict[str, Any],
    source_request: dict[str, Any],
) -> dict[str, Any]:
    expected = validate_full_params(params)
    bounds = optimizer_parameter_bounds_for_keys(expected)
    ea = read_ea_optimizer_defaults(ea_path, bounds=bounds)
    set_text = set_path.read_text(encoding="utf-8")
    entries = parse_set_optimizer_entries(set_text, bounds=bounds)
    set_values = {name: entries[name]["value"] for name in bounds}
    winner_ea = _same_params(expected, ea)
    winner_set = _same_params(expected, set_values)
    ea_set = _same_params(ea, set_values)
    if not (winner_ea and winner_set and ea_set):
        raise RuntimeError("CHALLENGER_PARAMETER_PARITY_FAILURE")
    if any(entries[name]["optimize"] != "N" for name in bounds):
        raise RuntimeError("CHALLENGER_SET_CONTAINS_ACTIVE_OPTIMIZATION")
    daily_loss = _new_daily_loss_authority(source_request)
    if daily_loss is not None:
        ea_daily = _ea_input_number(
            ea_path.read_text(encoding="utf-8"),
            "InpMaxDailyLossPct",
        )
        if not _same_number(ea_daily, daily_loss):
            raise RuntimeError("CHALLENGER_EA_DAILY_LOSS_MISMATCH")
        simple = _parse_simple_set(set_text)
        if not _same_number(simple.get("InpMaxDailyLossPct"), daily_loss):
            raise RuntimeError("CHALLENGER_SET_DAILY_LOSS_MISMATCH")
    geometry = verify_geometry_set(set_path, source_request)
    return {
        "winner_to_ea": winner_ea,
        "winner_to_set": winner_set,
        "ea_to_set": ea_set,
        "parameter_count": len(expected),
        "all_optimizer_flags": "N",
        "fixed_daily_loss_percent": daily_loss,
        "strategy_geometry": geometry,
    }


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except Exception as exc:
        raise RuntimeError(f"CHALLENGER_PATH_NOT_PROJECT_RELATIVE:{path}") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _bundle_manifest(
    bundle: Path,
    challenger_id: str,
    created_utc: str,
    source_request: dict[str, Any],
) -> dict[str, Any]:
    files = []
    for path in sorted(
        item for item in bundle.rglob("*")
        if item.is_file() and item.name != "manifest.json"
    ):
        files.append(
            {
                "path": path.relative_to(bundle).as_posix(),
                "sha256": _sha(path),
                "size": path.stat().st_size,
            }
        )
    if not files:
        raise RuntimeError("CHALLENGER_BUNDLE_EMPTY")
    return {
        "schema": "MAX_REBUILD_STRATEGY_CHALLENGER_MANIFEST_V1",
        "challenger_id": challenger_id,
        "created_utc": created_utc,
        "strategy_contract": source_request["strategy_contract"],
        "strategy_geometry": source_request["strategy_geometry"],
        "fixed_execution_authority": source_request.get("fixed_execution_authority"),
        "files": files,
    }


def _copy_source_evidence(
    source: dict[str, Any],
    target: Path,
) -> dict[str, str]:
    target.mkdir(parents=True, exist_ok=True)
    role = str(source.get("role_origin") or ROLE_OPTIMIZER_WINNER)
    copies = {
        "request.json": source["request_path"],
        "passes.json": source["passes_path"],
        "eligibility_audit.json": source["audit_path"],
        "report_provenance.json": source["report_provenance_path"],
        "Max_MTF.xml": source["xml_path"],
        "Max_MTF_metrics.csv": source["sidecar_path"],
    }
    if role == ROLE_OPTIMIZER_WINNER:
        copies["eligible_winner.json"] = source["winner_path"]
    elif role == ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE:
        candidate = source.get("candidate")
        if not isinstance(candidate, dict):
            raise RuntimeError("QUALIFIED_CANDIDATE_EVIDENCE_MISSING")
        candidate_path = target / "qualified_candidate.json"
        _write_json(candidate_path, candidate)
    else:
        raise RuntimeError("CHALLENGER_ROLE_ORIGIN_INVALID")

    result: dict[str, str] = {}
    for name, origin in copies.items():
        destination = target / name
        shutil.copy2(Path(origin), destination)
        result[name] = _sha(destination)
    if role == ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE:
        result["qualified_candidate.json"] = _sha(target / "qualified_candidate.json")
    return result


def _metadata_payload(
    challenger_id: str,
    created_utc: str,
    source: dict[str, Any],
    *,
    ea_path: Path,
    set_path: Path,
    ea_audit: dict[str, Any],
    set_audit: dict[str, Any],
    parity: dict[str, Any],
    source_hashes: dict[str, str],
) -> dict[str, Any]:
    scientist_lineage = [
        item["scientist"]
        for item in source["round_lineage"]
        if isinstance(item.get("scientist"), dict)
    ]
    return {
        "schema": "MAX_REBUILD_STRATEGY_CHALLENGER_V1",
        "challenger_id": challenger_id,
        "status": "CHALLENGER",
        "role_origin": str(source.get("role_origin") or ROLE_OPTIMIZER_WINNER),
        "created_utc": created_utc,
        "strategy_contract": source["request"]["strategy_contract"],
        "strategy_geometry": source["request"]["strategy_geometry"],
        "ea": {
            "version": source["request"]["ea"]["version"],
            "baseline_sha256": current_baseline_sha256(),
            "challenger_file": ea_path.name,
            "challenger_sha256": _sha(ea_path),
            "mutation_whitelist": (
                list(source["params"])
                + (
                    ["InpMaxDailyLossPct"]
                    if str(source["request"].get("schema") or "") == CURRENT_OPTIMIZER_SCHEMA
                    else []
                )
            ),
            "mutation_audit": ea_audit,
        },
        "set": {
            "file": set_path.name,
            "sha256": _sha(set_path),
            **set_audit,
        },
        "params": source["params"],
        "kpi": source["kpi"],
        "hard_gates": source["hard_gates"],
        "source": {
            "optimizer_job": source["job_id"],
            "round": source["round"],
            "mt5_pass": source["pass"],
            "request_schema": source["request"].get("schema"),
            "strategy_contract": source["request"]["strategy_contract"],
            "strategy_geometry": source["request"]["strategy_geometry"],
            "fixed_execution_authority": source["request"].get("fixed_execution_authority"),
            "candidate_evidence_sha256": (
                _sha(source["winner_path"])
                if str(source.get("role_origin") or ROLE_OPTIMIZER_WINNER)
                == ROLE_OPTIMIZER_WINNER
                else source_hashes.get("qualified_candidate.json")
            ),
            "xml_sha256": source["xml_sha256"],
            "sidecar_sha256": source["sidecar_sha256"],
            "run_nonce": source["run_nonce"],
            "selection_authority": (
                WINNER_RANKING
                if str(source.get("role_origin") or ROLE_OPTIMIZER_WINNER)
                == ROLE_OPTIMIZER_WINNER
                else "OWNER_EXPLICIT_QUALIFIED_CANDIDATE_SELECTION"
            ),
            "bundle_source_hashes": source_hashes,
        },
        "round_lineage": source["round_lineage"],
        "scientist_provenance": {
            "advisory_only": True,
            "transitions": scientist_lineage,
            "authority_mutation": "NONE",
        },
        "parameter_parity": parity,
        "champion": {
            "before": None,
            "after": None,
            "mutation": "NONE",
        },
    }


def _build_bundle(
    challenger_id: str,
    created_utc: str,
    source: dict[str, Any],
    staging: Path,
) -> dict[str, Any]:
    staging.mkdir(parents=True, exist_ok=False)
    ea_name = f"Max_Challenger_{challenger_id}.mq5"
    set_name = f"Max_Challenger_{challenger_id}.set"
    ea_path = staging / ea_name
    set_path = staging / set_name

    baseline_before = _sha(EA_BASELINE)
    if baseline_before != current_baseline_sha256():
        raise RuntimeError("BASELINE_EA_SHA_MISMATCH_BEFORE_REGISTRATION")
    ea_audit = apply_params_to_challenger_ea(
        EA_BASELINE,
        ea_path,
        source["params"],
        source["request"],
    )
    set_audit = write_challenger_set(
        set_path,
        source["params"],
        source["request"],
    )
    parity = verify_parameter_parity(
        ea_path,
        set_path,
        source["params"],
        source["request"],
    )
    source_hashes = _copy_source_evidence(source, staging / "source")
    baseline_after = _sha(EA_BASELINE)
    if baseline_after != baseline_before:
        raise RuntimeError("BASELINE_EA_MUTATED_DURING_REGISTRATION")

    metadata = _metadata_payload(
        challenger_id,
        created_utc,
        source,
        ea_path=ea_path,
        set_path=set_path,
        ea_audit=ea_audit,
        set_audit=set_audit,
        parity=parity,
        source_hashes=source_hashes,
    )
    metadata_path = staging / "challenger.json"
    _write_json(metadata_path, metadata)
    manifest = _bundle_manifest(
        staging,
        challenger_id,
        created_utc,
        source["request"],
    )
    manifest_path = staging / "manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "ea_sha256": _sha(ea_path),
        "set_sha256": _sha(set_path),
        "metadata_sha256": _sha(metadata_path),
        "manifest_sha256": _sha(manifest_path),
        "baseline_before_sha256": baseline_before,
        "baseline_after_sha256": baseline_after,
        "parameter_parity": parity,
    }


def _verify_manifest(bundle: Path) -> tuple[dict[str, Any], dict[str, str]]:
    manifest_path = bundle / "manifest.json"
    manifest = _json(manifest_path)
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("CHALLENGER_MANIFEST_EMPTY")
    hashes: dict[str, str] = {}
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("CHALLENGER_MANIFEST_ENTRY_INVALID")
        rel = str(entry.get("path") or "")
        if not rel or rel in seen or rel == "manifest.json":
            raise RuntimeError("CHALLENGER_MANIFEST_PATH_INVALID")
        seen.add(rel)
        path = bundle / rel
        if not path.is_file():
            raise RuntimeError(f"CHALLENGER_MANIFEST_FILE_MISSING:{rel}")
        actual = _sha(path)
        expected = str(entry.get("sha256") or "")
        if actual != expected:
            raise RuntimeError(f"CHALLENGER_MANIFEST_HASH_MISMATCH:{rel}")
        if int(entry.get("size") or -1) != path.stat().st_size:
            raise RuntimeError(f"CHALLENGER_MANIFEST_SIZE_MISMATCH:{rel}")
        hashes[rel] = actual

    actual_files = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if actual_files != seen:
        raise RuntimeError("CHALLENGER_MANIFEST_FILE_SET_MISMATCH")
    return manifest, hashes


def verify_challenger_bundle(
    challenger_id: str,
    *,
    allow_registering: bool = False,
    allow_promoted: bool = False,
    allow_retired: bool = False,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    row = get_challenger(challenger_id, path=path)
    if row is None:
        raise RuntimeError("CHALLENGER_UNKNOWN")
    allowed_status = row["status"] == VISIBLE_STATUS
    allowed_status = allowed_status or (
        allow_registering and row["status"] == REGISTERING_STATUS
    )
    allowed_status = allowed_status or (
        allow_promoted and row["status"] == "PROMOTED"
    )
    allowed_status = allowed_status or (
        allow_retired and row["status"] == "RETIRED"
    )
    if not allowed_status:
        raise RuntimeError("CHALLENGER_NOT_ACTIVE")

    bundle = (ROOT / str(row["bundle_path"])).resolve()
    authority_roots = (
        CHALLENGER_ARTIFACT_ROOT.resolve(),
        LEGACY_CHALLENGER_ARTIFACT_ROOT.resolve(),
    )
    if not any(bundle.is_relative_to(root) for root in authority_roots):
        raise RuntimeError("CHALLENGER_BUNDLE_PATH_OUTSIDE_AUTHORITY")
    if not bundle.is_dir():
        raise RuntimeError("CHALLENGER_BUNDLE_MISSING")
    manifest, hashes = _verify_manifest(bundle)
    if manifest.get("challenger_id") != challenger_id:
        raise RuntimeError("CHALLENGER_MANIFEST_ID_MISMATCH")
    source_request = dict(row["source_request"])
    expected_geometry = assert_geometry_matches_main(
        dict(source_request.get("strategy_geometry") or {}),
        str(source_request.get("period") or ""),
    )
    if source_request.get("strategy_contract") != STRATEGY_CONTRACT:
        raise RuntimeError("CHALLENGER_SOURCE_STRATEGY_CONTRACT_INVALID")
    if manifest.get("strategy_contract") != STRATEGY_CONTRACT:
        raise RuntimeError("CHALLENGER_MANIFEST_STRATEGY_CONTRACT_MISMATCH")
    if manifest.get("strategy_geometry") != expected_geometry:
        raise RuntimeError("CHALLENGER_MANIFEST_STRATEGY_GEOMETRY_MISMATCH")

    metadata_path = bundle / "challenger.json"
    metadata = _json(metadata_path)
    if metadata.get("challenger_id") != challenger_id:
        raise RuntimeError("CHALLENGER_METADATA_ID_MISMATCH")
    if metadata.get("status") != "CHALLENGER":
        raise RuntimeError("CHALLENGER_METADATA_STATUS_INVALID")
    if metadata.get("role_origin") != row.get("role_origin"):
        raise RuntimeError("CHALLENGER_METADATA_ORIGIN_INVALID")
    if metadata.get("role_origin") not in {
        ROLE_OPTIMIZER_WINNER,
        ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE,
    }:
        raise RuntimeError("CHALLENGER_METADATA_ORIGIN_UNSUPPORTED")
    if metadata.get("strategy_contract") != STRATEGY_CONTRACT:
        raise RuntimeError("CHALLENGER_METADATA_STRATEGY_CONTRACT_MISMATCH")
    if metadata.get("strategy_geometry") != expected_geometry:
        raise RuntimeError("CHALLENGER_METADATA_STRATEGY_GEOMETRY_MISMATCH")
    if not _same_params(
        validate_full_params(metadata.get("params")),
        validate_full_params(row["params"]),
    ):
        raise RuntimeError("CHALLENGER_METADATA_PARAMS_MISMATCH")
    if metadata.get("kpi") != row["kpi"]:
        raise RuntimeError("CHALLENGER_METADATA_KPI_MISMATCH")
    if metadata.get("hard_gates") != row["hard_gates"]:
        raise RuntimeError("CHALLENGER_METADATA_GATES_MISMATCH")
    source_meta = metadata.get("source")
    if not isinstance(source_meta, dict):
        raise RuntimeError("CHALLENGER_METADATA_SOURCE_MISSING")
    expected_source = {
        "optimizer_job": row["source_job_id"],
        "round": row["source_round"],
        "mt5_pass": row["source_pass"],
        "xml_sha256": row["winning_xml_sha256"],
        "sidecar_sha256": row["winning_sidecar_sha256"],
        "strategy_contract": STRATEGY_CONTRACT,
        "strategy_geometry": expected_geometry,
    }
    for key, expected in expected_source.items():
        if source_meta.get(key) != expected:
            raise RuntimeError(f"CHALLENGER_METADATA_SOURCE_MISMATCH:{key}")
    if str(source_request.get("schema") or "") == CURRENT_OPTIMIZER_SCHEMA:
        if source_meta.get("fixed_execution_authority") != source_request.get(
            "fixed_execution_authority"
        ):
            raise RuntimeError("CHALLENGER_METADATA_FIXED_EXECUTION_AUTHORITY_MISMATCH")
        if manifest.get("fixed_execution_authority") != source_request.get(
            "fixed_execution_authority"
        ):
            raise RuntimeError("CHALLENGER_MANIFEST_FIXED_EXECUTION_AUTHORITY_MISMATCH")
    champion_meta = metadata.get("champion")
    if not isinstance(champion_meta, dict) or champion_meta.get("mutation") != "NONE":
        raise RuntimeError("CHALLENGER_METADATA_CHAMPION_MUTATION_INVALID")

    ea_path = bundle / f"Max_Challenger_{challenger_id}.mq5"
    set_path = bundle / f"Max_Challenger_{challenger_id}.set"
    params = validate_full_params(row["params"])
    parity = verify_parameter_parity(
        ea_path,
        set_path,
        params,
        source_request,
    )

    if _sha(EA_BASELINE) != str(row["baseline_ea_sha256"]):
        raise RuntimeError("BASELINE_EA_CHANGED_SINCE_CHALLENGER_REGISTRATION")
    if _sha(EA_BASELINE) != current_baseline_sha256():
        raise RuntimeError("BASELINE_EA_SHA_MISMATCH")
    if row.get("challenger_ea_sha256") and _sha(ea_path) != row["challenger_ea_sha256"]:
        raise RuntimeError("CHALLENGER_EA_DB_HASH_MISMATCH")
    if row.get("set_sha256") and _sha(set_path) != row["set_sha256"]:
        raise RuntimeError("CHALLENGER_SET_DB_HASH_MISMATCH")
    if row.get("metadata_sha256") and _sha(metadata_path) != row["metadata_sha256"]:
        raise RuntimeError("CHALLENGER_METADATA_DB_HASH_MISMATCH")
    if row.get("manifest_sha256") and _sha(bundle / "manifest.json") != row["manifest_sha256"]:
        raise RuntimeError("CHALLENGER_MANIFEST_DB_HASH_MISMATCH")

    source_dir = bundle / "source"
    source_xml = source_dir / "Max_MTF.xml"
    source_sidecar = source_dir / "Max_MTF_metrics.csv"
    if row["role_origin"] == ROLE_OPTIMIZER_WINNER:
        candidate_evidence = source_dir / "eligible_winner.json"
    else:
        candidate_evidence = source_dir / "qualified_candidate.json"
    if not candidate_evidence.is_file():
        raise RuntimeError("CHALLENGER_SOURCE_CANDIDATE_EVIDENCE_MISSING")
    if str(source_meta.get("candidate_evidence_sha256") or "") != _sha(candidate_evidence):
        raise RuntimeError("CHALLENGER_SOURCE_CANDIDATE_EVIDENCE_HASH_MISMATCH")
    if _sha(source_xml) != row["winning_xml_sha256"]:
        raise RuntimeError("CHALLENGER_SOURCE_XML_HASH_MISMATCH")
    if _sha(source_sidecar) != row["winning_sidecar_sha256"]:
        raise RuntimeError("CHALLENGER_SOURCE_SIDECAR_HASH_MISMATCH")

    return {
        "status": "VERIFIED",
        "challenger_id": challenger_id,
        "bundle_path": row["bundle_path"],
        "manifest_sha256": _sha(bundle / "manifest.json"),
        "ea_sha256": _sha(ea_path),
        "set_sha256": _sha(set_path),
        "metadata_sha256": _sha(metadata_path),
        "candidate_evidence": "VERIFIED",
        "winner_evidence": (
            "VERIFIED" if row["role_origin"] == ROLE_OPTIMIZER_WINNER else None
        ),
        "qualified_candidate_evidence": (
            "VERIFIED"
            if row["role_origin"] == ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE
            else None
        ),
        "mt5_xml": "VERIFIED",
        "weighted_r_sidecar": "VERIFIED",
        "ea_artifact": "VERIFIED",
        "set_artifact": "VERIFIED",
        "ea_set_parity": "VERIFIED" if all(
            (
                parity["winner_to_ea"],
                parity["winner_to_set"],
                parity["ea_to_set"],
            )
        ) else "FAIL",
        "manifest": "VERIFIED",
        "parameter_parity": parity,
        "manifest_files": hashes,
        "baseline_sha256": _sha(EA_BASELINE),
        "strategy_contract": STRATEGY_CONTRACT,
        "strategy_geometry": expected_geometry,
        "champion_mutation": row["champion_mutation"],
    }
