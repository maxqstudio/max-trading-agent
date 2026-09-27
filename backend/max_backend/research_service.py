from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .artifact_control import register_artifact
from .challenger_bundle import validate_full_params
from .challenger_store import get_challenger
from .champion_store import current_champion
from .config import (
    CHAMPION_CURRENT_ROOT,
    DATABASE_PATH,
    RESEARCH_ARTIFACT_ROOT,
    ROOT,
    STRATEGY_CONTRACT,
)
from .db import connect
from .optimizer_core import sha256_file
from .optimizer_store import utc_now
from .promotion_service import verify_current_strategy_champion
from .research_contract import (
    ARTIFACT_LINEAGE_SCHEMA,
    AUTHORIZATION_SCHEMA,
    CANDIDATE_ID_SCHEMA,
    CURRENT_PARENT_CONTRACT,
    FEATURE_CONTRACT,
    LABEL_AUTHORITY_SCHEMA,
    OLD_MAX_SOURCE_REPO,
    OLD_MAX_SOURCE_SHA,
    OWNER_CUMULATIVE_E2E_AUTHORITY,
    OWNER_R00_CONFIRMATION,
    PARENT_SCHEMA,
    R00_ACCEPTED_MAIN_PARENT_SHA,
    R00_INPUT_SCHEMA,
    R00_OUTPUT_SCHEMA,
    R00_TERMINAL_STATES,
    RESEARCH_SCHEMA,
    SAMPLE_POLICY_SCHEMA,
    artifact_lineage_contract,
    candidate_identity_contract,
    capacity_authority_contract,
    label_design_authority,
    research_memory_contract,
    research_sample_policy,
    scientist_research_contract,
    stable_hash,
    unresolved_authority_items,
)
from .research_hardware import collect_hardware_snapshot, hardware_summary
from .research_owner_view import research_owner_view
from .research_r01_store import get_r01_authorization, get_r01_run
from .research_settings import (
    get_research_sample_configuration,
    require_research_sample_configuration,
)
from .research_store import (
    append_gate_event,
    append_memory_event,
    commit_r00_terminal_authority,
    create_authorization,
    create_research,
    get_authorization,
    get_research,
    latest_research,
    list_memory_events,
    update_gate_state,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DEFINE_RE = re.compile(r'^\s*#define\s+{name}\s+"([^"]+)"\s*$', re.MULTILINE)
_VERSION_RE = re.compile(r'^\s*#property\s+version\s+"([^"]+)"\s*$', re.MULTILINE)


def _manifest_hash(payload: dict[str, Any], hash_field: str) -> str:
    body = deepcopy(payload)
    body.pop(hash_field, None)
    return stable_hash(body)


def _seal(payload: dict[str, Any], hash_field: str) -> dict[str, Any]:
    result = deepcopy(payload)
    result[hash_field] = stable_hash(result)
    return result


def _verify_sealed(payload: dict[str, Any], hash_field: str) -> bool:
    expected = str(payload.get(hash_field) or "")
    return bool(expected) and expected == _manifest_hash(payload, hash_field)


def _relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise RuntimeError("RESEARCH_ARTIFACT_OUTSIDE_PROJECT") from exc


def _write_immutable_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise RuntimeError(f"RESEARCH_IMMUTABLE_ARTIFACT_MUTATION:{path.name}")
        return
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    temp.replace(path)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"RESEARCH_ARTIFACT_UNREADABLE:{path.name}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"RESEARCH_ARTIFACT_NOT_OBJECT:{path.name}")
    return value


def _define(text: str, name: str) -> str:
    match = re.search(_DEFINE_RE.pattern.format(name=re.escape(name)), text, re.MULTILINE)
    if match is None:
        raise RuntimeError(f"RESEARCH_EA_DEFINE_MISSING:{name}")
    return str(match.group(1))


def _ea_version(text: str) -> str:
    match = _VERSION_RE.search(text)
    if match is None:
        raise RuntimeError("RESEARCH_EA_VERSION_MISSING")
    return str(match.group(1))


def _ea_input_number(text: str, name: str) -> float:
    match = re.search(
        rf"^\s*input\s+(?:double|int)\s+{re.escape(name)}\s*=\s*([-+0-9.eE]+)\s*;",
        text,
        re.MULTILINE,
    )
    if match is None:
        raise RuntimeError(f"RESEARCH_EA_INPUT_MISSING:{name}")
    value = float(match.group(1))
    if not math.isfinite(value):
        raise RuntimeError(f"RESEARCH_EA_INPUT_NONFINITE:{name}")
    return value


def _hash_value(value: Any, label: str) -> str:
    raw = str(value or "").lower()
    if not _HEX64.fullmatch(raw):
        raise RuntimeError(f"RESEARCH_PARENT_HASH_INVALID:{label}")
    return raw


def _parent_authority(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    integrity = verify_current_strategy_champion(path=path)
    if integrity.get("status") != "VERIFIED":
        raise RuntimeError("R00_CURRENT_CHAMPION_NOT_VERIFIED")
    champion = deepcopy(integrity["current"])
    challenger = get_challenger(str(champion["source_challenger_id"]), path=path)
    if challenger is None:
        raise RuntimeError("R00_PARENT_CHALLENGER_MISSING")
    if str(challenger.get("status")) != "PROMOTED":
        raise RuntimeError("R00_PARENT_CHALLENGER_NOT_PROMOTED")

    source_request = deepcopy(challenger.get("source_request") or {})
    params = validate_full_params(champion.get("params"))
    project_ea = CHAMPION_CURRENT_ROOT / "Max_MTF.mq5"
    if not project_ea.is_file():
        raise RuntimeError("R00_CURRENT_CHAMPION_EA_MISSING")
    text = project_ea.read_text(encoding="utf-8")

    feature_contract = _define(text, "MAX_MTF_FEATURE_CONTRACT")
    strategy_contract = _define(text, "MAX_MTF_STRATEGY_CONTRACT")
    resolver_version = _define(text, "MAX_MTF_RESOLVER_VERSION")
    semantic_version = _ea_version(text)
    if strategy_contract != STRATEGY_CONTRACT:
        raise RuntimeError("R00_STRATEGY_CONTRACT_MISMATCH")
    if feature_contract != FEATURE_CONTRACT:
        raise RuntimeError("R00_FEATURE_CONTRACT_MISMATCH")
    if resolver_version != CURRENT_PARENT_CONTRACT["resolver_version"]:
        raise RuntimeError("R00_RESOLVER_VERSION_MISMATCH")
    if source_request.get("strategy_contract") != strategy_contract:
        raise RuntimeError("R00_SOURCE_REQUEST_STRATEGY_CONTRACT_MISMATCH")
    geometry = deepcopy(source_request.get("strategy_geometry") or {})
    if geometry.get("resolver_version") != resolver_version:
        raise RuntimeError("R00_SOURCE_REQUEST_RESOLVER_MISMATCH")
    if str(source_request.get("period") or "") != str(geometry.get("main_tf") or ""):
        raise RuntimeError("R00_PARENT_MAIN_TIMEFRAME_MISMATCH")
    if str(challenger.get("ea_version") or "") != semantic_version:
        raise RuntimeError("R00_PARENT_EA_VERSION_MISMATCH")

    risk = {
        "authority": "DETERMINISTIC_EA_RUNTIME",
        "risk_percent_equity": _ea_input_number(text, "InpRiskPct"),
        "daily_loss_limit_percent": _ea_input_number(text, "InpMaxDailyLossPct"),
        "sl_atr": float(params["InpSL_ATR"]),
        "tp_atr": float(params["InpTP_ATR"]),
        "max_hold_bars": int(params["InpMaxHoldBars"]),
        "lot_sizing": "BROKER_VALID_MAX_VOLUME_WITH_ORDER_CALC_PROFIT_STOP_RISK_CAP",
        "minimum_lot_over_risk_behavior": "FAIL_CLOSED",
        "model_owns_risk": False,
    }
    for key, ea_name in (
        ("sl_atr", "InpSL_ATR"),
        ("tp_atr", "InpTP_ATR"),
        ("max_hold_bars", "InpMaxHoldBars"),
    ):
        if abs(float(risk[key]) - _ea_input_number(text, ea_name)) > 1e-12:
            raise RuntimeError(f"R00_PARENT_RISK_PARAM_MISMATCH:{ea_name}")

    artifact_hashes = {
        "baseline_ea_sha256": _hash_value(integrity["baseline_sha256"], "baseline_ea"),
        "project_ea_sha256": _hash_value(integrity["project_ea_sha256"], "project_ea"),
        "project_set_sha256": _hash_value(integrity["project_set_sha256"], "project_set"),
        "deployed_ea_sha256": _hash_value(integrity["deployed_ea_sha256"], "deployed_ea"),
        "deployed_ex5_sha256": _hash_value(integrity["deployed_ex5_sha256"], "deployed_ex5"),
        "tester_set_sha256": _hash_value(integrity["tester_set_sha256"], "tester_set"),
        "challenger_manifest_sha256": _hash_value(challenger["manifest_sha256"], "challenger_manifest"),
        "challenger_metadata_sha256": _hash_value(challenger["metadata_sha256"], "challenger_metadata"),
        "challenger_set_sha256": _hash_value(challenger["set_sha256"], "challenger_set"),
        "winning_xml_sha256": _hash_value(challenger["winning_xml_sha256"], "winning_xml"),
        "winning_sidecar_sha256": _hash_value(challenger["winning_sidecar_sha256"], "winning_sidecar"),
    }
    champion_ea = _hash_value(champion["champion_ea_sha256"], "champion_ea")
    if champion_ea != artifact_hashes["project_ea_sha256"]:
        raise RuntimeError("R00_CHAMPION_PROJECT_EA_HASH_MISMATCH")

    source_identity = {
        "symbol": str(source_request.get("symbol") or ""),
        "relative_symbol": str(source_request.get("relative_symbol") or ""),
        "main_timeframe": str(source_request.get("period") or ""),
        "from_date": source_request.get("from_date"),
        "to_date": source_request.get("to_date"),
        "broker": source_request.get("broker"),
        "feed": source_request.get("feed"),
        "source": source_request.get("source"),
        "broker_feed_status": (
            "AVAILABLE"
            if any(source_request.get(key) for key in ("broker", "feed", "source"))
            else "UNRESOLVED_AVAILABLE_AT_R01_DATA_SOURCE_FREEZE"
        ),
    }
    if not source_identity["symbol"] or not source_identity["relative_symbol"]:
        raise RuntimeError("R00_PARENT_SYMBOL_AUTHORITY_MISSING")

    core = {
        "strategy_champion_id": str(champion["strategy_id"]),
        "champion_tenure_id": str(champion["champion_tenure_id"]),
        "source_challenger_id": str(champion["source_challenger_id"]),
        "source_job_id": str(champion["source_job_id"]),
        "source_round": int(champion["source_round"]),
        "source_pass": int(champion["source_pass"]),
        "promotion_id": str(champion["promotion_id"]),
        "ea_sha256": champion_ea,
        "ea_semantic_version": semantic_version,
        "strategy_parameters": params,
        "strategy_contract": strategy_contract,
        "feature_contract": feature_contract,
        "mtf_resolver_version": resolver_version,
        "strategy_geometry": geometry,
        "main_timeframe": source_identity["main_timeframe"],
        "main_symbol": source_identity["symbol"],
        "relative_symbol": source_identity["relative_symbol"],
        "deterministic_risk": risk,
        "source_identity": source_identity,
        "parent_artifact_hashes": artifact_hashes,
        "champion_authority_source": str(champion["authority_source"]),
        "champion_integrity": "VERIFIED",
        "champion_live_authority": "NONE",
    }
    authority_sha = stable_hash(core)
    return {
        **core,
        "research_parent_id": "RPAR-" + authority_sha[:24],
        "parent_authority_sha256": authority_sha,
    }


def assert_no_orphaned_research_authority(*, path: Path = DATABASE_PATH) -> None:
    authority_queries = (
        "SELECT 1 FROM research_authorizations LIMIT 1",
        "SELECT 1 FROM research_projects LIMIT 1",
        "SELECT 1 FROM research_gate_events LIMIT 1",
        "SELECT 1 FROM research_memory_events LIMIT 1",
        "SELECT 1 FROM research_gate_authorizations_v2 LIMIT 1",
        "SELECT 1 FROM research_r01_runs LIMIT 1",
        "SELECT 1 FROM research_r01_sources LIMIT 1",
        """
        SELECT 1 FROM artifact_registry
        WHERE owner_type IN ('RESEARCH','RESEARCH_STATE','RESEARCH_R01')
        LIMIT 1
        """,
    )
    with connect(path) as conn:
        if any(conn.execute(query).fetchone() is not None for query in authority_queries):
            raise RuntimeError("RESEARCH_AUTHORITY_WITHOUT_CURRENT_PROJECT")


def _research_id(parent: dict[str, Any]) -> str:
    return "RSRCH-" + stable_hash(
        {
            "schema": RESEARCH_SCHEMA,
            "research_parent_id": parent["research_parent_id"],
            "parent_authority_sha256": parent["parent_authority_sha256"],
            "strategy_contract": parent["strategy_contract"],
            "feature_contract": parent["feature_contract"],
            "accepted_source_parent_sha": R00_ACCEPTED_MAIN_PARENT_SHA,
        }
    )[:24]


def _research_policy(
    h1_minimum_trades_per_month: int | None = None,
) -> dict[str, Any]:
    return {
        "schema": "MAX_RESEARCH_POLICY_R00_V1",
        "sample_policy": research_sample_policy(h1_minimum_trades_per_month),
        "capacity_authority": capacity_authority_contract(),
        "research_memory": research_memory_contract(),
        "scientist": scientist_research_contract(),
        "gate_governance": {
            "automation_inside_gate": True,
            "human_authorization_between_gates": True,
            "system_determines_scientific_qualification": True,
            "system_may_recommend": True,
            "owner_selects_qualified_candidates": True,
            "automatic_next_gate": False,
            "automatic_challenger": False,
            "automatic_champion": False,
        },
    }


def research_contract_overview() -> dict[str, Any]:
    return {
        "schema": RESEARCH_SCHEMA,
        "gate": "R00",
        "feature_contract": FEATURE_CONTRACT,
        "label_authority": label_design_authority(),
        "candidate_identity_contract": candidate_identity_contract(),
        "artifact_lineage_contract": artifact_lineage_contract(),
        "research_policy": _research_policy(),
        "unresolved_authority": unresolved_authority_items(),
        "old_max_source_audit": {
            "repository": OLD_MAX_SOURCE_REPO,
            "commit": OLD_MAX_SOURCE_SHA,
            "source_inspected": True,
            "docs_used_as_substitute": False,
        },
        "accepted_source_parent_sha": R00_ACCEPTED_MAIN_PARENT_SHA,
        "r01_executable": False,
    }


def r00_preflight(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    existing = latest_research(path=path)
    if current_champion(path=path) is None and existing is None:
        assert_no_orphaned_research_authority(path=path)
        sample_config = get_research_sample_configuration(path=path)
        return {
            "schema": RESEARCH_SCHEMA,
            "status": "BLOCKED",
            "gate": "R00",
            "current_champion": None,
            "research_parent_id": None,
            "parent_authority_sha256": None,
            "prerequisites": {},
            "latest_research": None,
            "hardware_snapshot": "CAPTURED_ON_R00_START",
            "sample_configuration": sample_config,
            "unresolved_authority": unresolved_authority_items(
                h1_sample_policy_frozen=bool(sample_config["configured"])
            ),
            "blockers": ["CURRENT_STRATEGY_CHAMPION_REQUIRED"],
            "r01_executable": False,
        }
    parent = _parent_authority(path=path)
    sample_config = get_research_sample_configuration(path=path)
    return {
        "schema": RESEARCH_SCHEMA,
        "status": "READY_TO_START",
        "gate": "R00",
        "current_champion": {
            "strategy_id": parent["strategy_champion_id"],
            "status": parent["champion_integrity"],
            "source_challenger_id": parent["source_challenger_id"],
            "ea_sha256": parent["ea_sha256"],
            "ea_semantic_version": parent["ea_semantic_version"],
        },
        "research_parent_id": parent["research_parent_id"],
        "parent_authority_sha256": parent["parent_authority_sha256"],
        "feature_contract": parent["feature_contract"],
        "strategy_contract": parent["strategy_contract"],
        "mtf_resolver_version": parent["mtf_resolver_version"],
        "prerequisites": {
            "m08_accepted_merged": {
                "status": "SATISFIED_BY_ACCEPTED_SOURCE_BASELINE",
                "source_parent_sha": R00_ACCEPTED_MAIN_PARENT_SHA,
            },
            "cumulative_strategy_e2e": {
                "status": "OWNER_CONFIRMATION_REQUIRED",
                "accepted_authority": OWNER_CUMULATIVE_E2E_AUTHORITY,
            },
            "exact_frozen_parent": {
                "status": "READY_TO_FREEZE",
                "parent_authority_sha256": parent["parent_authority_sha256"],
            },
            "owner_r00_authorization": {
                "status": "OWNER_ACTION_REQUIRED",
                "confirmation": OWNER_R00_CONFIRMATION,
            },
        },
        "latest_research": (
            {
                "research_id": existing["research_id"],
                "parent_strategy_id": existing["parent_strategy_id"],
                "historical_r00": {
                    "internal_gate": existing["current_gate"],
                    "state": existing["gate_state"],
                },
                "current_stage": canonical_research_stage(
                    existing["research_id"],
                    path=path,
                ),
            }
            if existing
            else None
        ),
        "hardware_snapshot": "CAPTURED_ON_R00_START",
        "sample_configuration": sample_config,
        "unresolved_authority": unresolved_authority_items(
            h1_sample_policy_frozen=bool(sample_config["configured"])
        ),
        "r01_executable": False,
    }


def _validate_r00_start_request(
    request: dict[str, Any],
    parent: dict[str, Any],
    *,
    path: Path,
) -> int:
    if request.get("confirmed") is not True:
        raise RuntimeError("R00_OWNER_CONFIRMATION_REQUIRED")
    if str(request.get("owner_confirmation") or "") != OWNER_R00_CONFIRMATION:
        raise RuntimeError("R00_OWNER_AUTHORIZATION_INVALID")
    if (
        str(request.get("cumulative_strategy_e2e_authority") or "")
        != OWNER_CUMULATIVE_E2E_AUTHORITY
    ):
        raise RuntimeError("R00_CUMULATIVE_STRATEGY_E2E_AUTHORITY_REQUIRED")
    if str(request.get("expected_parent_strategy_id") or "") != str(
        parent["strategy_champion_id"]
    ):
        raise RuntimeError("R00_PARENT_STRATEGY_STALE")
    if str(request.get("expected_parent_authority_sha256") or "") != str(
        parent["parent_authority_sha256"]
    ):
        raise RuntimeError("R00_PARENT_AUTHORITY_STALE")
    configured = require_research_sample_configuration(path=path)
    requested = request.get("h1_minimum_trades_per_month")
    if requested is not None:
        if (
            isinstance(requested, bool)
            or not isinstance(requested, int)
            or int(requested) != configured
        ):
            raise RuntimeError("R00_H1_SAMPLE_CONFIGURATION_STALE")
    return configured


def _authorization(
    request: dict[str, Any],
    parent: dict[str, Any],
    *,
    path: Path,
) -> dict[str, Any]:
    h1_minimum_trades_per_month = _validate_r00_start_request(
        request,
        parent,
        path=path,
    )
    authorized_utc = utc_now()
    body = {
        "schema": AUTHORIZATION_SCHEMA,
        "gate": "R00",
        "action": "START",
        "confirmed": True,
        "owner_confirmation": OWNER_R00_CONFIRMATION,
        "expected_parent_strategy_id": parent["strategy_champion_id"],
        "expected_parent_authority_sha256": parent["parent_authority_sha256"],
        "cumulative_strategy_e2e_authority": OWNER_CUMULATIVE_E2E_AUTHORITY,
        "h1_minimum_trades_per_month": h1_minimum_trades_per_month,
    }
    payload_sha = stable_hash(body)
    record = {
        "authorization_id": "RAUTH-R00-" + payload_sha[:24],
        "gate": "R00",
        "action": "START",
        "confirmed": True,
        "expected_parent_strategy_id": parent["strategy_champion_id"],
        "expected_parent_authority_sha256": parent["parent_authority_sha256"],
        "cumulative_strategy_e2e_authority": OWNER_CUMULATIVE_E2E_AUTHORITY,
        "h1_minimum_trades_per_month": h1_minimum_trades_per_month,
        "payload_sha256": payload_sha,
        "authorized_utc": authorized_utc,
    }
    return create_authorization(record, path=path)


def _parent_manifest(
    *,
    research_id: str,
    parent: dict[str, Any],
    authorization: dict[str, Any],
    frozen_utc: str,
) -> dict[str, Any]:
    body = {
        "schema": PARENT_SCHEMA,
        "research_id": research_id,
        "research_parent_id": parent["research_parent_id"],
        "immutable": True,
        "frozen_utc": frozen_utc,
        "owner_authorization": {
            "authorization_id": authorization["authorization_id"],
            "authority": "OWNER",
            "authorized_utc": authorization["authorized_utc"],
            "confirmation": OWNER_R00_CONFIRMATION,
            "h1_minimum_trades_per_month": authorization[
                "h1_minimum_trades_per_month"
            ],
        },
        **deepcopy(parent),
    }
    return _seal(body, "manifest_sha256")


def _hardware_manifest(snapshot: dict[str, Any]) -> dict[str, Any]:
    return _seal(snapshot, "snapshot_sha256")


def _input_manifest(
    *,
    research_id: str,
    parent_manifest: dict[str, Any],
    hardware_manifest: dict[str, Any],
    h1_minimum_trades_per_month: int,
) -> dict[str, Any]:
    body = {
        "schema": R00_INPUT_SCHEMA,
        "research_id": research_id,
        "gate": "R00",
        "parent_manifest_sha256": parent_manifest["manifest_sha256"],
        "hardware_snapshot_sha256": hardware_manifest["snapshot_sha256"],
        "hardware_profile_hash": hardware_manifest["profile_hash"],
        "feature_contract": FEATURE_CONTRACT,
        "label_authority": label_design_authority(),
        "candidate_identity_contract": candidate_identity_contract(),
        "artifact_lineage_contract": artifact_lineage_contract(),
        "research_policy": _research_policy(h1_minimum_trades_per_month),
        "unresolved_authority": unresolved_authority_items(
            h1_sample_policy_frozen=True
        ),
        "old_max_source_audit": {
            "repository": OLD_MAX_SOURCE_REPO,
            "commit": OLD_MAX_SOURCE_SHA,
            "source_inspected": True,
        },
    }
    return _seal(body, "manifest_sha256")


def _foundation_validators(
    *,
    parent_manifest: dict[str, Any],
    hardware_manifest: dict[str, Any],
    input_manifest: dict[str, Any],
    authorization: dict[str, Any],
) -> list[dict[str, Any]]:
    checks = [
        (
            "PARENT_CHAMPION_VERIFIED",
            parent_manifest.get("champion_integrity") == "VERIFIED",
        ),
        (
            "PARENT_MANIFEST_IMMUTABLE_HASH",
            _verify_sealed(parent_manifest, "manifest_sha256"),
        ),
        (
            "FEATURE_CONTRACT",
            parent_manifest.get("feature_contract") == FEATURE_CONTRACT,
        ),
        (
            "STRATEGY_CONTRACT",
            parent_manifest.get("strategy_contract") == STRATEGY_CONTRACT,
        ),
        (
            "MTF_RESOLVER",
            parent_manifest.get("mtf_resolver_version")
            == CURRENT_PARENT_CONTRACT["resolver_version"],
        ),
        (
            "HARDWARE_PROFILE_HASH",
            hardware_manifest.get("profile_hash")
            == stable_hash(hardware_manifest.get("stable_identity") or {}),
        ),
        (
            "HARDWARE_SNAPSHOT_HASH",
            _verify_sealed(hardware_manifest, "snapshot_sha256"),
        ),
        (
            "INPUT_MANIFEST_HASH",
            _verify_sealed(input_manifest, "manifest_sha256"),
        ),
        (
            "OWNER_AUTHORIZATION",
            bool(authorization.get("confirmed"))
            and authorization.get("cumulative_strategy_e2e_authority")
            == OWNER_CUMULATIVE_E2E_AUTHORITY,
        ),
        (
            "RESEARCH_SAMPLE_POLICY_IS_DISTINCT",
            input_manifest["research_policy"]["sample_policy"].get(
                "strategy_optimizer_policy_inherited"
            )
            is False,
        ),
        (
            "RESEARCH_H1_SAMPLE_AUTHORITY_FROZEN_OWNER",
            input_manifest["research_policy"]["sample_policy"][
                "h1_minimum_sample_trade_policy"
            ].get("status")
            == "FROZEN_OWNER_AUTHORITY"
            and input_manifest["research_policy"]["sample_policy"][
                "h1_minimum_sample_trade_policy"
            ].get("value")
            == int(authorization["h1_minimum_trades_per_month"])
            and input_manifest["research_policy"]["sample_policy"][
                "h1_minimum_sample_trade_policy"
            ].get("unit")
            == "TRADES_PER_H1_MONTH",
        ),
        (
            "NO_GLOBAL_PARAMETER_CEILING",
            input_manifest["research_policy"]["capacity_authority"].get(
                "global_parameter_hard_ceiling"
            )
            is None,
        ),
        (
            "SCIENTIST_ADVISORY_ONLY",
            input_manifest["research_policy"]["scientist"].get("authority")
            == "ADVISORY_ONLY",
        ),
        (
            "NO_ORPHAN_ARTIFACT_AUTHORITY",
            input_manifest["artifact_lineage_contract"].get(
                "orphan_authority_allowed"
            )
            is False,
        ),
    ]
    return [
        {
            "validator": name,
            "status": "PASS" if passed else "FAIL",
        }
        for name, passed in checks
    ]


def _output_manifest(
    *,
    research_id: str,
    input_manifest: dict[str, Any],
    validators: list[dict[str, Any]],
) -> dict[str, Any]:
    passed = all(row["status"] == "PASS" for row in validators)
    body = {
        "schema": R00_OUTPUT_SCHEMA,
        "research_id": research_id,
        "gate": "R00",
        "gate_state": "PASS_WAITING_OWNER" if passed else "FAIL_WAITING_OWNER",
        "gate_input_manifest_sha": input_manifest["manifest_sha256"],
        "validators": deepcopy(validators),
        "unresolved_authority": unresolved_authority_items(
            h1_sample_policy_frozen=True
        ),
        "training_count": 0,
        "onnx_count": 0,
        "research_challenger_count": 0,
        "champion_mutation": "NONE",
        "r01_authorized": False,
        "automatic_next_gate": False,
    }
    return _seal(body, "manifest_sha256")



def _register_artifacts(
    *,
    research_id: str,
    parent_strategy_id: str,
    parent_path: Path,
    hardware_path: Path,
    input_path: Path,
    output_path: Path,
    path: Path,
    state: str,
) -> dict[str, str]:
    parent = register_artifact(
        artifact_type="RESEARCH_PARENT_MANIFEST",
        producer="RESEARCH_R00",
        owner_type="RESEARCH",
        owner_id=research_id + ":parent",
        source_type="STRATEGY_CHAMPION",
        source_id=parent_strategy_id,
        canonical_path=parent_path,
        status="FROZEN",
        in_use=True,
        retention_class="ACTIVE_AUTHORITY",
        deletable=False,
        cleanable=False,
        dependencies=[parent_strategy_id],
        path=path,
    )
    hardware = register_artifact(
        artifact_type="RESEARCH_HARDWARE_SNAPSHOT",
        producer="RESEARCH_R00",
        owner_type="RESEARCH",
        owner_id=research_id + ":hardware",
        source_type="RESEARCH",
        source_id=research_id,
        canonical_path=hardware_path,
        status="FROZEN",
        in_use=True,
        retention_class="ACTIVE_AUTHORITY",
        deletable=False,
        cleanable=False,
        dependencies=[str(parent["artifact_id"])],
        path=path,
    )
    gate_input = register_artifact(
        artifact_type="RESEARCH_AUTHORITY_MANIFEST",
        producer="RESEARCH_R00",
        owner_type="RESEARCH",
        owner_id=research_id + ":input",
        source_type="RESEARCH",
        source_id=research_id,
        canonical_path=input_path,
        status="FROZEN_INPUT",
        in_use=True,
        retention_class="ACTIVE_AUTHORITY",
        deletable=False,
        cleanable=False,
        dependencies=[str(parent["artifact_id"]), str(hardware["artifact_id"])],
        path=path,
    )
    gate_output = register_artifact(
        artifact_type="RESEARCH_AUTHORITY_MANIFEST",
        producer="RESEARCH_R00",
        owner_type="RESEARCH",
        owner_id=research_id + ":output",
        source_type="RESEARCH",
        source_id=research_id,
        canonical_path=output_path,
        status="R00_STAGED",
        in_use=True,
        retention_class="ACTIVE_AUTHORITY",
        deletable=False,
        cleanable=False,
        dependencies=[str(gate_input["artifact_id"])],
        path=path,
    )
    state_artifact = register_artifact(
        artifact_type="RESEARCH_AUTHORITY_STATE",
        producer="RESEARCH_R00",
        owner_type="RESEARCH_STATE",
        owner_id=research_id,
        source_type="RESEARCH",
        source_id=research_id,
        canonical_path=path,
        sha256=stable_hash(
            {
                "schema": RESEARCH_SCHEMA,
                "research_id": research_id,
                "gate": "R00",
                "state": state,
            }
        ),
        size_bytes_override=0,
        status="R00_STAGED",
        in_use=True,
        retention_class="ACTIVE_AUTHORITY",
        deletable=False,
        cleanable=False,
        dependencies=[str(gate_output["artifact_id"])],
        path=path,
    )
    return {
        "parent": str(parent["artifact_id"]),
        "hardware": str(hardware["artifact_id"]),
        "input": str(gate_input["artifact_id"]),
        "output": str(gate_output["artifact_id"]),
        "state": str(state_artifact["artifact_id"]),
    }

def _research_paths(research_id: str) -> dict[str, Path]:
    root = RESEARCH_ARTIFACT_ROOT / research_id
    return {
        "root": root,
        "parent": root / "parent_manifest.json",
        "hardware": root / "hardware_snapshot.json",
        "input": root / "r00_input_manifest.json",
        "output": root / "r00_output_manifest.json",
    }



def start_r00(
    request: dict[str, Any],
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    parent = _parent_authority(path=path)
    research_id = _research_id(parent)
    existing = get_research(research_id, path=path)
    if existing is not None:
        if (
            existing["parent_strategy_id"] != parent["strategy_champion_id"]
            or existing["parent_authority_sha256"]
            != parent["parent_authority_sha256"]
        ):
            raise RuntimeError("RESEARCH_IDENTITY_COLLISION")
        if existing["gate_state"] == "STARTING":
            raise RuntimeError("R00_START_ALREADY_IN_PROGRESS")
        result = research_detail(research_id, path=path)
        result["idempotent"] = True
        return result

    h1_minimum_trades_per_month = _validate_r00_start_request(
        request,
        parent,
        path=path,
    )
    authorization = _authorization(request, parent, path=path)
    frozen_utc = str(authorization["authorized_utc"])
    hardware = _hardware_manifest(collect_hardware_snapshot())
    parent_manifest = _parent_manifest(
        research_id=research_id,
        parent=parent,
        authorization=authorization,
        frozen_utc=frozen_utc,
    )
    input_manifest = _input_manifest(
        research_id=research_id,
        parent_manifest=parent_manifest,
        hardware_manifest=hardware,
        h1_minimum_trades_per_month=h1_minimum_trades_per_month,
    )
    paths = _research_paths(research_id)

    create_research(
        {
            "research_id": research_id,
            "research_parent_id": parent["research_parent_id"],
            "parent_strategy_id": parent["strategy_champion_id"],
            "parent_authority_sha256": parent["parent_authority_sha256"],
            "parent_manifest_path": _relative(paths["parent"]),
            "parent_manifest_sha256": parent_manifest["manifest_sha256"],
            "feature_contract": FEATURE_CONTRACT,
            "current_gate": "R00",
            "gate_state": "STARTING",
            "gate_input_manifest_sha": input_manifest["manifest_sha256"],
            "gate_output_manifest_sha": None,
            "owner_authorization_id": authorization["authorization_id"],
            "authorized_utc": authorization["authorized_utc"],
            "hardware_snapshot_path": _relative(paths["hardware"]),
            "hardware_snapshot_sha256": hardware["snapshot_sha256"],
            "label_authority": label_design_authority(),
            "candidate_identity_contract": candidate_identity_contract(),
            "artifact_lineage_contract": artifact_lineage_contract(),
            "research_policy": _research_policy(h1_minimum_trades_per_month),
            "unresolved_authority": unresolved_authority_items(
                h1_sample_policy_frozen=True
            ),
            "candidate_ids": [],
            "qualification_states": {},
            "system_recommendation": None,
            "owner_selected_ids": [],
            "training_count": 0,
            "onnx_count": 0,
            "research_challenger_count": 0,
            "created_utc": frozen_utc,
        },
        path=path,
    )
    append_gate_event(
        research_id=research_id,
        gate="R00",
        from_state=None,
        to_state="STARTING",
        authority="OWNER_EXPLICIT_R00_START",
        owner_authorization_id=authorization["authorization_id"],
        input_manifest_sha=input_manifest["manifest_sha256"],
        output_manifest_sha=None,
        created_utc=frozen_utc,
        path=path,
    )

    try:
        _write_immutable_json(paths["parent"], parent_manifest)
        _write_immutable_json(paths["hardware"], hardware)
        _write_immutable_json(paths["input"], input_manifest)
        validators = _foundation_validators(
            parent_manifest=parent_manifest,
            hardware_manifest=hardware,
            input_manifest=input_manifest,
            authorization=authorization,
        )
        output = _output_manifest(
            research_id=research_id,
            input_manifest=input_manifest,
            validators=validators,
        )
        _write_immutable_json(paths["output"], output)
        final_state = str(output["gate_state"])
        artifacts = _register_artifacts(
            research_id=research_id,
            parent_strategy_id=parent["strategy_champion_id"],
            parent_path=paths["parent"],
            hardware_path=paths["hardware"],
            input_path=paths["input"],
            output_path=paths["output"],
            path=path,
            state=final_state,
        )
        commit_r00_terminal_authority(
            research_id=research_id,
            gate_state=final_state,
            gate_input_manifest_sha=input_manifest["manifest_sha256"],
            gate_output_manifest_sha=output["manifest_sha256"],
            output_artifact_id=artifacts["output"],
            state_artifact_id=artifacts["state"],
            owner_authorization_id=authorization["authorization_id"],
            memory_payload={
                "parent_manifest_sha256": parent_manifest["manifest_sha256"],
                "hardware_snapshot_sha256": hardware["snapshot_sha256"],
                "validators": validators,
                "unresolved_authority": unresolved_authority_items(
                    h1_sample_policy_frozen=True
                ),
            },
            path=path,
        )
        result = research_detail(research_id, path=path)
        result["idempotent"] = False
        return result
    except Exception:
        current = get_research(research_id, path=path)
        if current is not None and current["gate_state"] == "STARTING":
            try:
                update_gate_state(
                    research_id,
                    gate_state="ERROR_WAITING_OWNER",
                    path=path,
                )
                append_gate_event(
                    research_id=research_id,
                    gate="R00",
                    from_state="STARTING",
                    to_state="ERROR_WAITING_OWNER",
                    authority="R00_FAIL_CLOSED_ERROR",
                    owner_authorization_id=authorization["authorization_id"],
                    input_manifest_sha=input_manifest["manifest_sha256"],
                    output_manifest_sha=None,
                    path=path,
                )
                append_memory_event(
                    research_id=research_id,
                    event_type="R00_ERROR",
                    stage="R00",
                    status="ERROR_WAITING_OWNER",
                    payload={"reason": "R00_START_FAILED"},
                    source_manifest_sha256=input_manifest["manifest_sha256"],
                    path=path,
                )
            finally:
                raise
        raise


def _research_artifact_lineage_status(
    research_id: str,
    *,
    path: Path,
    expected_state: str | None = None,
) -> dict[str, Any]:
    owner_ids = {
        "parent": research_id + ":parent",
        "hardware": research_id + ":hardware",
        "input": research_id + ":input",
        "output": research_id + ":output",
        "state": research_id,
    }
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT artifact_id,artifact_type,owner_type,owner_id,source_type,
                   source_id,dependencies_json,status,retention_class,in_use,
                   canonical_path,sha256
            FROM artifact_registry
            WHERE
                (owner_type='RESEARCH' AND owner_id IN (?,?,?,?))
                OR (owner_type='RESEARCH_STATE' AND owner_id=?)
            """,
            (
                owner_ids["parent"],
                owner_ids["hardware"],
                owner_ids["input"],
                owner_ids["output"],
                owner_ids["state"],
            ),
        ).fetchall()
    by_owner = {str(row["owner_id"]): dict(row) for row in rows}
    missing = [value for value in owner_ids.values() if value not in by_owner]
    if missing:
        return {
            "status": "FAIL",
            "reason": "RESEARCH_ARTIFACT_LINEAGE_MISSING",
            "missing": missing,
        }

    parent = by_owner[owner_ids["parent"]]
    hardware = by_owner[owner_ids["hardware"]]
    gate_input = by_owner[owner_ids["input"]]
    gate_output = by_owner[owner_ids["output"]]
    state = by_owner[owner_ids["state"]]

    def deps(row: dict[str, Any]) -> list[str]:
        return list(json.loads(str(row["dependencies_json"] or "[]")))

    output_path = Path(str(gate_output["canonical_path"]))
    output_hash_matches = (
        output_path.is_file()
        and bool(gate_output["sha256"])
        and str(gate_output["sha256"]) == sha256_file(output_path)
    )
    state_hash_matches = (
        expected_state is None
        or str(state["sha256"] or "")
        == stable_hash(
            {
                "schema": RESEARCH_SCHEMA,
                "research_id": research_id,
                "gate": "R00",
                "state": expected_state,
            }
        )
    )

    checks = {
        "parent_type": parent["artifact_type"] == "RESEARCH_PARENT_MANIFEST",
        "hardware_depends_parent": str(parent["artifact_id"]) in deps(hardware),
        "input_depends_parent": str(parent["artifact_id"]) in deps(gate_input),
        "input_depends_hardware": str(hardware["artifact_id"]) in deps(gate_input),
        "output_depends_input": str(gate_input["artifact_id"]) in deps(gate_output),
        "state_depends_output": str(gate_output["artifact_id"]) in deps(state),
        "active_authority_retention": all(
            str(row["retention_class"]) == "ACTIVE_AUTHORITY"
            for row in (parent, hardware, gate_input, gate_output, state)
        ),
        "research_source_identity": all(
            str(row["source_id"]) == research_id
            for row in (hardware, gate_input, gate_output, state)
        ),
        "output_artifact_file_hash": output_hash_matches,
        "output_status_matches_gate_state": (
            expected_state is None or str(gate_output["status"]) == expected_state
        ),
        "state_status_matches_gate_state": (
            expected_state is None or str(state["status"]) == expected_state
        ),
        "state_authority_hash": state_hash_matches,
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "status": "VERIFIED" if not failures else "FAIL",
        "checks": checks,
        "failures": failures,
        "artifact_ids": {
            "parent": str(parent["artifact_id"]),
            "hardware": str(hardware["artifact_id"]),
            "input": str(gate_input["artifact_id"]),
            "output": str(gate_output["artifact_id"]),
            "state": str(state["artifact_id"]),
        },
        "artifact_statuses": {
            "output": str(gate_output["status"]),
            "state": str(state["status"]),
        },
    }


def validate_frozen_research(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    row = get_research(research_id, path=path)
    if row is None:
        raise FileNotFoundError(research_id)
    parent_path = ROOT / str(row["parent_manifest_path"])
    hardware_path = ROOT / str(row["hardware_snapshot_path"])
    output_path = _research_paths(research_id)["output"]
    input_path = _research_paths(research_id)["input"]

    parent = _load_json(parent_path)
    hardware = _load_json(hardware_path)
    gate_input = _load_json(input_path)
    gate_output = _load_json(output_path) if output_path.is_file() else None
    gate_state = str(row["gate_state"])
    expected_artifact_state = (
        gate_state if gate_state in R00_TERMINAL_STATES else None
    )
    artifact_lineage = _research_artifact_lineage_status(
        research_id,
        path=path,
        expected_state=expected_artifact_state,
    )
    output_state_binding = (
        (
            gate_output is None
            and row["gate_output_manifest_sha"] is None
            and gate_state in {"STARTING", "ERROR_WAITING_OWNER"}
        )
        or (
            gate_output is not None
            and gate_state in R00_TERMINAL_STATES
            and str(gate_output.get("gate_state")) == gate_state
        )
    )
    checks = {
        "parent_manifest_hash": (
            _verify_sealed(parent, "manifest_sha256")
            and parent.get("manifest_sha256") == row["parent_manifest_sha256"]
        ),
        "hardware_snapshot_hash": (
            _verify_sealed(hardware, "snapshot_sha256")
            and hardware.get("snapshot_sha256") == row["hardware_snapshot_sha256"]
        ),
        "hardware_profile_hash": (
            hardware.get("profile_hash")
            == stable_hash(hardware.get("stable_identity") or {})
        ),
        "input_manifest_hash": (
            _verify_sealed(gate_input, "manifest_sha256")
            and gate_input.get("manifest_sha256")
            == row["gate_input_manifest_sha"]
        ),
        "output_manifest_hash": (
            gate_output is not None
            and _verify_sealed(gate_output, "manifest_sha256")
            and gate_output.get("manifest_sha256")
            == row["gate_output_manifest_sha"]
        )
        if row["gate_output_manifest_sha"]
        else gate_output is None,
        "gate_state_output_binding": output_state_binding,
        "research_id_parent_binding": (
            str(parent.get("research_id")) == research_id
            and str(parent.get("research_parent_id")) == row["research_parent_id"]
            and str(parent.get("strategy_champion_id")) == row["parent_strategy_id"]
        ),
        "feature_contract": row["feature_contract"] == FEATURE_CONTRACT,
        "gate_is_r00": row["current_gate"] == "R00",
        "no_training": row["training_count"] == 0,
        "no_onnx": row["onnx_count"] == 0,
        "no_research_challenger": row["research_challenger_count"] == 0,
        "no_champion_mutation": row["champion_mutation"] == "NONE",
        "r01_not_authorized": (
            gate_output is None or gate_output.get("r01_authorized") is False
        ),
        "artifact_lineage": artifact_lineage["status"] == "VERIFIED",
    }
    failures = [key for key, passed in checks.items() if not passed]
    return {
        "status": "VERIFIED" if not failures else "INTEGRITY_FAIL",
        "research_id": research_id,
        "checks": checks,
        "failures": failures,
        "artifact_lineage": artifact_lineage,
    }

def _artifact_count(research_id: str, *, path: Path) -> int:
    owner_ids = (
        research_id + ":parent",
        research_id + ":hardware",
        research_id + ":input",
        research_id + ":output",
    )
    with connect(path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM artifact_registry
            WHERE
                (owner_type='RESEARCH' AND owner_id IN (?,?,?,?))
                OR (owner_type='RESEARCH_STATE' AND owner_id=?)
            """,
            (*owner_ids, str(research_id)),
        ).fetchone()
    return int(row["n"]) if row is not None else 0


def research_detail(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    row = get_research(research_id, path=path)
    if row is None:
        raise FileNotFoundError(research_id)
    paths = _research_paths(research_id)
    parent = _load_json(ROOT / str(row["parent_manifest_path"]))
    hardware = _load_json(ROOT / str(row["hardware_snapshot_path"]))
    output = _load_json(paths["output"]) if paths["output"].is_file() else None
    authorization = get_authorization(row["owner_authorization_id"], path=path)
    integrity = validate_frozen_research(research_id, path=path)
    current_stage = canonical_research_stage(research_id, path=path)
    presented_row = deepcopy(row)
    historical_r00 = {
        "internal_gate": str(row["current_gate"]),
        "state": str(row["gate_state"]),
    }
    if current_stage["internal_gate"] != "R00":
        presented_row.pop("current_gate", None)
        presented_row.pop("gate_state", None)
    return {
        "schema": RESEARCH_SCHEMA,
        **presented_row,
        "historical_r00": historical_r00,
        "parent_manifest": parent,
        "hardware_snapshot": hardware,
        "hardware_summary": hardware_summary(hardware),
        "gate_output": output,
        "owner_authorization": authorization,
        "memory_events": list_memory_events(research_id, path=path),
        "integrity": integrity,
        "artifact_count": _artifact_count(research_id, path=path),
        "current_stage": current_stage,
        "r01_executable": False,
        "next_gate_authorized": False,
    }


def canonical_research_stage(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    historical = get_research(research_id, path=path)
    if historical is None:
        raise FileNotFoundError(research_id)
    if str(historical["current_gate"]) != "R00":
        raise RuntimeError("RESEARCH_HISTORICAL_R00_GATE_MUTATED")
    r01 = get_r01_run(research_id, path=path)
    if r01 is not None:
        return {
            "schema": "MAX_RESEARCH_CURRENT_STAGE_V1",
            "research_id": str(research_id),
            "stage": "DATA_FOUNDATION",
            "internal_gate": "R01",
            "state": str(r01["state"]),
            "authority": "DERIVED_FROM_IMMUTABLE_R00_PLUS_R01_RUN",
            "historical_r00_state": str(historical["gate_state"]),
            "r02_executable": False,
        }
    return {
        "schema": "MAX_RESEARCH_CURRENT_STAGE_V1",
        "research_id": str(research_id),
        "stage": "FOUNDATION",
        "internal_gate": "R00",
        "state": str(historical["gate_state"]),
        "authority": "IMMUTABLE_R00_PROJECT_AUTHORITY",
        "historical_r00_state": str(historical["gate_state"]),
        "r02_executable": False,
    }


def current_research(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    sample_config = get_research_sample_configuration(path=path)
    configured_sample = sample_config["h1_minimum_trades_per_month"]
    current = latest_research(path=path)
    if current is None:
        preflight = r00_preflight(path=path)
        return {
            "schema": RESEARCH_SCHEMA,
            "status": "NOT_STARTED",
            "current": None,
            "preflight": preflight,
            "sample_configuration": sample_config,
            "r01_executable": False,
            "owner_view": research_owner_view(
                research_initialized=False,
                foundation_state=str(preflight.get("status") or ""),
                data_state=None,
                sample_requirement=(
                    int(configured_sample)
                    if configured_sample is not None
                    else None
                ),
                execution_sample_requirement=None,
                execution_sample_source="No Research execution is running",
                unresolved_authority=preflight.get("unresolved_authority") or [],
            ),
        }
    detail = research_detail(current["research_id"], path=path)
    r01 = get_r01_run(current["research_id"], path=path)
    historical_sample = detail["research_policy"]["sample_policy"][
        "h1_minimum_sample_trade_policy"
    ].get("value")
    execution_sample = (
        int(configured_sample)
        if configured_sample is not None
        else None
    )
    execution_source = "Next execution will use current configuration"
    if str(current.get("gate_state") or "") == "STARTING":
        execution_sample = (
            int(historical_sample)
            if historical_sample is not None
            else None
        )
        execution_source = "Current running Research initialization"
    if r01 is not None and str(r01["state"]) == "STARTING":
        authorization = get_r01_authorization(
            r01["authorization_id"],
            path=path,
        )
        if authorization is None:
            raise RuntimeError("R01_RUNNING_SAMPLE_AUTHORITY_MISSING")
        execution_sample = int(
            authorization["payload"]["research_h1_minimum_trades_per_month"]
        )
        execution_source = "Current running Data validation"
    return {
        "schema": RESEARCH_SCHEMA,
        "status": "ACTIVE_AUTHORITY",
        "current": detail,
        "current_stage": detail["current_stage"],
        "sample_configuration": sample_config,
        "r01_executable": False,
        "owner_view": research_owner_view(
            research_initialized=True,
            foundation_state=str(
                (detail.get("historical_r00") or {}).get("state") or ""
            ),
            data_state=str(r01["state"]) if r01 is not None else None,
            sample_requirement=(
                int(configured_sample)
                if configured_sample is not None
                else None
            ),
            execution_sample_requirement=execution_sample,
            execution_sample_source=execution_source,
            unresolved_authority=detail.get("unresolved_authority") or [],
        ),
    }


def attempt_gate_transition(
    research_id: str,
    target_gate: str,
    *,
    authority: str,
    path: Path = DATABASE_PATH,
) -> None:
    row = get_research(research_id, path=path)
    if row is None:
        raise FileNotFoundError(research_id)
    if str(authority).upper().startswith("SCIENTIST"):
        raise RuntimeError("RESEARCH_GATE_AUTHORITY_FORBIDDEN:SCIENTIST")
    target = str(target_gate or "").upper()
    if target == "R01":
        raise RuntimeError("R01_REQUIRES_EXPLICIT_R01_START_AUTHORIZATION")
    if target == "R02":
        raise RuntimeError("R02_BLOCKED_OWNER_AUTHORIZATION_REQUIRED")
    if target != "R00":
        raise RuntimeError("RESEARCH_GATE_TRANSITION_UNSUPPORTED")
    raise RuntimeError("R00_ALREADY_TERMINAL_NO_TRANSITION")


def verify_no_training_side_effects(
    research_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    row = get_research(research_id, path=path)
    if row is None:
        raise FileNotFoundError(research_id)
    return {
        "training_count": row["training_count"],
        "onnx_count": row["onnx_count"],
        "research_challenger_count": row["research_challenger_count"],
        "champion_mutation": row["champion_mutation"],
        "status": (
            "PASS"
            if (
                row["training_count"] == 0
                and row["onnx_count"] == 0
                and row["research_challenger_count"] == 0
                and row["champion_mutation"] == "NONE"
            )
            else "FAIL"
        ),
    }
