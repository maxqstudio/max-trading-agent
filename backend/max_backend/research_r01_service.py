from __future__ import annotations

import json
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_control import register_artifact
from .config import DATABASE_PATH, RESEARCH_ARTIFACT_ROOT, ROOT
from .db import connect
from .optimizer_core import sha256_file
from .optimizer_store import utc_now
from .research_contract import (
    OWNER_R01_CONFIRMATION,
    R01_AUTHORIZATION_SCHEMA,
    R01_INPUT_SCHEMA,
    R01_OUTPUT_SCHEMA,
    R01_SCHEMA,
    stable_hash,
)
from .research_dataset import (
    DATASET_BUILDER_VERSION,
    LABEL_CONTRACT_ID,
    build_dataset,
    write_dataset_csv,
)
from .research_leakage import (
    TARGET_DEPENDENCY_SCHEMA,
    bind_protected_partition_rows,
    bind_protected_target_dependency_authority,
    boundary_safe_partition_rows,
    partition_supervised_eligible,
    protected_partition_manifest,
    run_adversarial_suite,
)
from .research_source import (
    list_prepared_sources,
    prepare_r01_source as prepare_managed_r01_source,
    resolve_prepared_source,
)
from .research_owner_view import data_owner_view
from .research_r01_store import (
    commit_r01_terminal_authority,
    create_r01_authorization,
    create_r01_run,
    discard_staged_r01_artifacts,
    get_r01_authorization,
    get_r01_run,
    stage_r01_artifacts,
)
from .research_service import (
    assert_no_orphaned_research_authority,
    research_detail,
    verify_no_training_side_effects,
)
from .research_settings import (
    get_research_sample_configuration,
    require_research_sample_configuration,
)
from .research_store import latest_research

R01_OLD_MAX_AUDIT = "docs/audits/R01_OLD_MAX_SOURCE_AUDIT.md"
DISCOVERY_LABEL_SUMMARY_SCHEMA = "MAX_RESEARCH_DISCOVERY_LABEL_SUMMARY_R01_V1"


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(payload)
    result["manifest_sha256"] = stable_hash(result)
    return result


def _write_immutable_json(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise RuntimeError(f"R01_IMMUTABLE_ARTIFACT_MUTATION:{path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    temp.replace(path)


def _normalized_r00_parent(*, path: Path) -> dict[str, Any]:
    latest = latest_research(path=path)
    if latest is None:
        raise RuntimeError("R01_ACCEPTED_CURRENT_R00_REQUIRED")

    research_id = str(latest.get("research_id") or "").strip()
    if not research_id:
        raise RuntimeError("R01_CURRENT_R00_RESEARCH_ID_MISSING")

    detail = research_detail(research_id, path=path)
    if str(detail.get("research_id") or "") != research_id:
        raise RuntimeError("R01_R00_RESEARCH_ID_MISMATCH")
    if detail["integrity"]["status"] != "VERIFIED":
        raise RuntimeError("R01_R00_INTEGRITY_REQUIRED")

    historical_r00 = detail.get("historical_r00") or {}
    if str(historical_r00.get("state") or "") != "PASS_WAITING_OWNER":
        raise RuntimeError("R01_R00_TERMINAL_PASS_REQUIRED")

    parent_manifest = detail.get("parent_manifest") or {}
    if str(parent_manifest.get("research_id") or "") != research_id:
        raise RuntimeError("R01_R00_RESEARCH_ID_MISMATCH")
    if str(detail.get("research_parent_id") or "") != str(
        parent_manifest.get("research_parent_id") or ""
    ):
        raise RuntimeError("R01_R00_PARENT_ID_MISMATCH")
    if str(detail.get("parent_strategy_id") or "") != str(
        parent_manifest.get("strategy_champion_id") or ""
    ):
        raise RuntimeError("R01_R00_CHAMPION_ID_MISMATCH")
    if str(detail.get("parent_authority_sha256") or "") != str(
        parent_manifest.get("parent_authority_sha256") or ""
    ):
        raise RuntimeError("R01_R00_PARENT_AUTHORITY_MISMATCH")

    owner_authorization = detail.get("owner_authorization") or {}
    if (
        owner_authorization.get("confirmed") is not True
        or str(owner_authorization.get("gate") or "") != "R00"
        or str(owner_authorization.get("action") or "") != "START"
        or str(owner_authorization.get("expected_parent_strategy_id") or "")
        != str(detail.get("parent_strategy_id") or "")
        or str(owner_authorization.get("expected_parent_authority_sha256") or "")
        != str(detail.get("parent_authority_sha256") or "")
    ):
        raise RuntimeError("R01_R00_OWNER_AUTHORITY_MISMATCH")

    h1 = detail["research_policy"]["sample_policy"][
        "h1_minimum_sample_trade_policy"
    ]["value"]
    authorization_h1 = owner_authorization.get("h1_minimum_trades_per_month")
    parent_h1 = parent_manifest.get("owner_authorization", {}).get(
        "h1_minimum_trades_per_month"
    )
    if (
        isinstance(h1, bool)
        or not isinstance(h1, int)
        or int(h1) <= 0
        or int(authorization_h1 or 0) != int(h1)
        or int(parent_h1 or 0) != int(h1)
    ):
        raise RuntimeError("R01_R00_H1_AUTHORITY_MISMATCH")

    side_effects = verify_no_training_side_effects(research_id, path=path)
    if side_effects["status"] != "PASS":
        raise RuntimeError("R01_R00_SIDE_EFFECT_REGRESSION")

    parent = deepcopy(parent_manifest)
    parent["parent_strategy_id"] = str(detail["parent_strategy_id"])
    parent["research_id"] = research_id
    parent["research_parent_id"] = str(detail["research_parent_id"])
    parent["parent_authority_sha256"] = str(detail["parent_authority_sha256"])
    parent["historical_r00_h1_minimum_trades_per_month"] = int(h1)
    parent["r00_parent_manifest_sha256"] = str(detail["parent_manifest_sha256"])
    return parent

def r01_preflight(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    if latest_research(path=path) is None:
        assert_no_orphaned_research_authority(path=path)
        sample_config = get_research_sample_configuration(path=path)
        configured_sample = sample_config["h1_minimum_trades_per_month"]
        owner_view = data_owner_view(
            state="NOT_INITIALIZED",
            source_status="NOT_READY",
            dataset_status="NOT_STARTED",
            feature_readiness="NOT_STARTED",
            label_readiness="NOT_STARTED",
            leakage_status="NOT_RUN",
            data_quality_status="NOT_RUN",
            protected_data_state="NOT_STARTED",
            integrity_status="NOT_STARTED",
            sample_requirement=(
                int(configured_sample)
                if configured_sample is not None
                else None
            ),
            execution_sample_requirement=None,
            execution_sample_source="No Research execution is running",
            physical_integrity=None,
        )
        owner_view["state"] = "Research not initialized"
        owner_view["next_step"] = (
            "Establish a verified Strategy Champion and initialize Research "
            "before preparing Data."
        )
        return {
            "schema": R01_SCHEMA,
            "status": "BLOCKED",
            "stage": "DATA_FOUNDATION",
            "research_id": None,
            "parent_strategy_id": None,
            "parent_integrity": "NOT_STARTED",
            "research_h1_minimum_trades_per_month": configured_sample,
            "historical_r00_h1_minimum_trades_per_month": None,
            "execution_h1_minimum_trades_per_month": None,
            "dataset_status": "NOT_STARTED",
            "feature_readiness": "NOT_STARTED",
            "label_readiness": "NOT_STARTED",
            "leakage_status": "NOT_RUN",
            "data_quality_status": "NOT_RUN",
            "date_window": None,
            "class_distribution": None,
            "verified_source": _owner_source_summary([]),
            "protected_data_state": "NOT_STARTED",
            "integrity_status": "NOT_STARTED",
            "physical_integrity": None,
            "owner_confirmation": None,
            "hidden_partition_defaults": False,
            "model_training": 0,
            "onnx": 0,
            "research_challenger": 0,
            "champion_mutation": "NONE",
            "r02_executable": False,
            "existing_run": None,
            "owner_view": owner_view,
        }
    parent = _normalized_r00_parent(path=path)
    _recover_incomplete_r01(parent=parent, path=path)
    existing = get_r01_run(parent["research_id"], path=path)
    sample_config = get_research_sample_configuration(path=path)
    configured_sample = sample_config["h1_minimum_trades_per_month"]
    execution_sample = (
        int(configured_sample)
        if configured_sample is not None
        else None
    )
    execution_sample_source = "Next Data validation will use current configuration"
    if existing is not None:
        existing_authorization = get_r01_authorization(
            existing["authorization_id"],
            path=path,
        )
        if existing_authorization is None:
            raise RuntimeError("R01_SAMPLE_SNAPSHOT_AUTHORITY_MISSING")
        execution_sample = int(
            existing_authorization["payload"][
                "research_h1_minimum_trades_per_month"
            ]
        )
        execution_sample_source = (
            "Current running Data validation"
            if str(existing["state"]) == "STARTING"
            else "Recorded Data validation execution snapshot"
        )
    prepared_sources = list_prepared_sources(parent["research_id"], path=path)
    source_summary = _owner_source_summary(prepared_sources)
    owner_summary = _r01_owner_summary(
        parent["research_id"],
        existing,
        path=path,
    )
    state = "READY_TO_CONFIGURE" if existing is None else str(existing["state"])
    owner_view = data_owner_view(
        state=state,
        source_status=str(source_summary["status"]),
        dataset_status=str(owner_summary["dataset_status"]),
        feature_readiness=str(owner_summary["feature_readiness"]),
        label_readiness=str(owner_summary["label_readiness"]),
        leakage_status=str(owner_summary["leakage_status"]),
        data_quality_status=str(owner_summary["data_quality_status"]),
        protected_data_state=str(owner_summary["protected_data_state"]),
        integrity_status=str(owner_summary["integrity_status"]),
        sample_requirement=(
            int(configured_sample)
            if configured_sample is not None
            else None
        ),
        execution_sample_requirement=execution_sample,
        execution_sample_source=execution_sample_source,
        physical_integrity=owner_summary["physical_integrity"],
    )
    geometry = parent.get("strategy_geometry") or {}
    owner_view["timeframes"] = list(
        dict.fromkeys(
            str(value)
            for key, value in geometry.items()
            if str(key).endswith("_tf") and value
        )
    )
    return {
        "schema": R01_SCHEMA,
        "status": state,
        "stage": "DATA_FOUNDATION",
        "research_id": parent["research_id"],
        "parent_strategy_id": parent["parent_strategy_id"],
        "parent_integrity": "VERIFIED",
        "research_h1_minimum_trades_per_month": (
            int(configured_sample)
            if configured_sample is not None
            else None
        ),
        "historical_r00_h1_minimum_trades_per_month": parent[
            "historical_r00_h1_minimum_trades_per_month"
        ],
        "execution_h1_minimum_trades_per_month": execution_sample,
        "dataset_status": owner_summary["dataset_status"],
        "feature_readiness": owner_summary["feature_readiness"],
        "label_readiness": owner_summary["label_readiness"],
        "leakage_status": owner_summary["leakage_status"],
        "data_quality_status": owner_summary["data_quality_status"],
        "date_window": owner_summary["date_window"],
        "class_distribution": owner_summary["class_distribution"],
        "verified_source": source_summary,
        "protected_data_state": owner_summary["protected_data_state"],
        "integrity_status": owner_summary["integrity_status"],
        "physical_integrity": owner_summary["physical_integrity"],
        "owner_confirmation": OWNER_R01_CONFIRMATION,
        "hidden_partition_defaults": False,
        "model_training": 0,
        "onnx": 0,
        "research_challenger": 0,
        "champion_mutation": "NONE",
        "r02_executable": False,
        "existing_run": existing,
        "owner_view": owner_view,
    }


def _owner_source_summary(sources: list[dict[str, Any]]) -> dict[str, Any]:
    if not sources:
        return {
            "status": "NOT_READY",
            "source_id": None,
            "broker": None,
            "feed": None,
            "source_timezone": None,
            "main_symbol": None,
            "relative_symbol": None,
            "data_start_utc": None,
            "data_end_utc": None,
            "row_coverage": None,
        }
    source = sources[0]
    return {
        "status": "VERIFIED",
        "source_id": source["source_id"],
        "broker": source["broker"],
        "feed": source["feed"],
        "source_timezone": source["source_timezone"],
        "main_symbol": source["main_symbol"],
        "relative_symbol": source["relative_symbol"],
        "data_start_utc": source["data_start_utc"],
        "data_end_utc": source["data_end_utc"],
        "row_coverage": source["row_coverage"],
    }


def r01_source_overview(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    if latest_research(path=path) is None:
        assert_no_orphaned_research_authority(path=path)
        return {
            "schema": "MAX_RESEARCH_SOURCE_OVERVIEW_R01_V1",
            "status": "NOT_INITIALIZED",
            "research_id": None,
            "parent_strategy_id": None,
            "source": _owner_source_summary([]),
            "source_count": 0,
        }
    parent = _normalized_r00_parent(path=path)
    sources = list_prepared_sources(parent["research_id"], path=path)
    return {
        "schema": "MAX_RESEARCH_SOURCE_OVERVIEW_R01_V1",
        "research_id": parent["research_id"],
        "parent_strategy_id": parent["parent_strategy_id"],
        "source": _owner_source_summary(sources),
        "source_count": len(sources),
    }


def prepare_r01_verified_source(
    request: dict[str, Any],
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    parent = _normalized_r00_parent(path=path)
    if get_r01_run(parent["research_id"], path=path) is not None:
        raise RuntimeError("R01_SOURCE_PREPARATION_CLOSED_AFTER_R01_START")
    source = prepare_managed_r01_source(
        request,
        parent=parent,
        path=path,
    )
    return {
        "schema": "MAX_RESEARCH_SOURCE_PREPARE_RESULT_R01_V1",
        "research_id": parent["research_id"],
        "source": _owner_source_summary([source]),
        "verified": True,
    }


def _r01_owner_summary(
    research_id: str,
    run: dict[str, Any] | None,
    *,
    path: Path,
) -> dict[str, Any]:
    empty = {
        "dataset_status": "NOT_STARTED",
        "feature_readiness": "CP32_PARITY_REQUIRED",
        "label_readiness": "PARENT_FIRST_BARRIER_CONTRACT",
        "leakage_status": "NOT_RUN",
        "data_quality_status": "NOT_RUN",
        "date_window": None,
        "class_distribution": None,
        "protected_data_state": "OWNER_PARTITION_BOUNDARIES_REQUIRED",
        "integrity_status": "NOT_STARTED",
        "physical_integrity": None,
        "dependency_authority": None,
        "discovery_supervision": None,
        "protected_windows": None,
    }
    if run is None:
        return empty
    integrity = validate_r01_integrity(path=path)
    result = {
        **empty,
        "dataset_status": str(run["state"]),
        "protected_data_state": "BOUND_IN_R01_AUTHORIZATION",
        "integrity_status": integrity["status"],
    }
    if run["state"] != "PASS_WAITING_OWNER" or integrity["status"] != "VERIFIED":
        result["feature_readiness"] = (
            "VALIDATION_FAILED" if run["state"] != "STARTING" else "VALIDATING"
        )
        result["label_readiness"] = result["feature_readiness"]
        result["leakage_status"] = result["feature_readiness"]
        result["data_quality_status"] = result["feature_readiness"]
        return result

    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT canonical_path FROM artifact_registry
            WHERE owner_type='RESEARCH_R01'
              AND owner_id=?
              AND source_type='RESEARCH_R01_RUN'
              AND source_id=?
              AND status='PASS_WAITING_OWNER'
            """,
            (str(research_id), str(run["run_id"])),
        ).fetchall()
    by_name = {Path(str(row["canonical_path"])).name: Path(str(row["canonical_path"])) for row in rows}
    quality_path = by_name.get("data_quality_report.json")
    discovery_summary_path = by_name.get("discovery_label_summary.json")
    dependency_path = by_name.get("dependency_report.json")
    protected_path = by_name.get("protected_data_manifest.json")
    if (
        quality_path is None
        or not quality_path.is_file()
        or discovery_summary_path is None
        or not discovery_summary_path.is_file()
        or dependency_path is None
        or not dependency_path.is_file()
        or protected_path is None
        or not protected_path.is_file()
    ):
        result["integrity_status"] = "INTEGRITY_FAIL"
        return result
    quality = _read_json(quality_path)
    discovery_summary = _read_json(discovery_summary_path)
    dependency = _read_json(dependency_path)
    protected = _read_json(protected_path)
    if (
        discovery_summary.get("schema") != DISCOVERY_LABEL_SUMMARY_SCHEMA
        or discovery_summary.get("scope") != "DISCOVERY_ONLY"
        or discovery_summary.get("supervision_scope")
        != "DISCOVERY_OWNED_TARGET_VALID_BOUNDARY_SAFE"
        or discovery_summary.get("target_overlap_check") != "PASS"
        or int(discovery_summary.get("protected_outcome_rows_included", -1)) != 0
        or not _verify_sealed_manifest(discovery_summary)
    ):
        result["integrity_status"] = "INTEGRITY_FAIL"
        return result
    result.update({
        "dataset_status": "SEALED",
        "feature_readiness": "READY",
        "label_readiness": "READY",
        "leakage_status": "PASS",
        "data_quality_status": str(quality.get("status") or "FAIL"),
        "date_window": {
            "start": quality.get("dataset_start"),
            "end": quality.get("dataset_end"),
        },
        "class_distribution": {
            "scope": "DISCOVERY_ONLY",
            "sell": discovery_summary.get("sell"),
            "skip": discovery_summary.get("skip"),
            "buy": discovery_summary.get("buy"),
            "sell_ratio": discovery_summary.get("sell_ratio"),
            "skip_ratio": discovery_summary.get("skip_ratio"),
            "buy_ratio": discovery_summary.get("buy_ratio"),
        },
        "protected_data_state": "PROTECTED",
        "physical_integrity": {
            "chronology_status": quality.get("monotonicity"),
            "duplicate_timestamps": quality.get("duplicate_timestamps"),
            "missing_source_data": quality.get("missing_source_data"),
            "mtf_alignment_status": quality.get("mtf_alignment_status"),
            "relative_symbol_alignment_status": quality.get(
                "relative_symbol_alignment_status"
            ),
            "cp32_completeness": quality.get("cp32_completeness"),
        },
        "dependency_authority": {
            "label_dependency_main_bars": dependency.get("label_dependency_main_bars"),
            "full_base_dependency_main_bars": dependency.get("full_base_dependency_main_bars"),
            "minimum_legal_purge_main_bars": dependency.get("minimum_legal_purge_main_bars"),
            "minimum_legal_embargo_main_bars": dependency.get("minimum_legal_embargo_main_bars"),
            "future_candidate_rule": dependency.get("future_candidate_rule"),
        },
        "discovery_supervision": {
            "scope": discovery_summary.get("scope"),
            "physical_rows": discovery_summary.get("physical_rows"),
            "boundary_safe_physical_rows": discovery_summary.get("boundary_safe_physical_rows"),
            "boundary_excluded_from_supervision_rows": discovery_summary.get(
                "boundary_excluded_from_supervision_rows"
            ),
            "supervised_rows": discovery_summary.get("supervised_rows"),
            "context_only_rows": discovery_summary.get("context_only_rows"),
            "effective_boundary_purge_main_bars": discovery_summary.get(
                "effective_boundary_purge_main_bars"
            ),
            "target_overlap_check": discovery_summary.get("target_overlap_check"),
            "original_row_identity_authority": discovery_summary.get(
                "original_row_identity_authority"
            ),
        },
        "protected_windows": {
            "boundary_semantics": deepcopy(protected.get("boundary_semantics") or {}),
            "discovery": deepcopy(
                (protected.get("partition_identity") or {}).get("discovery") or {}
            ),
            "locked_oos": deepcopy(
                (protected.get("partition_identity") or {}).get("locked_oos") or {}
            ),
            "fresh_forward": deepcopy(
                (protected.get("partition_identity") or {}).get("fresh_forward") or {}
            ),
        },
    })
    return result


def _canonical_partition(value: str, label: str) -> str:
    token = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"R01_PARTITION_DATE_INVALID:{label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError(f"R01_PARTITION_TIMEZONE_REQUIRED:{label}")
    return parsed.astimezone(timezone.utc).isoformat()


def _validate_start_request(
    request: dict[str, Any],
    *,
    parent: dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    if request.get("confirmed") is not True:
        raise RuntimeError("R01_OWNER_CONFIRMATION_REQUIRED")
    if str(request.get("owner_confirmation") or "") != OWNER_R01_CONFIRMATION:
        raise RuntimeError("R01_OWNER_AUTHORIZATION_INVALID")
    if str(request.get("research_id") or "") != parent["research_id"]:
        raise RuntimeError("R01_RESEARCH_ID_STALE")
    if str(request.get("expected_parent_strategy_id") or "") != parent["parent_strategy_id"]:
        raise RuntimeError("R01_PARENT_STRATEGY_STALE")
    source_id = str(request.get("source_id") or "").strip()
    if not source_id:
        raise RuntimeError("R01_VERIFIED_SOURCE_REQUIRED")
    source = resolve_prepared_source(
        source_id,
        parent=parent,
        path=path,
    )
    partitions = {
        "discovery_from": _canonical_partition(request.get("discovery_from"), "discovery_from"),
        "discovery_to": _canonical_partition(request.get("discovery_to"), "discovery_to"),
        "locked_oos_from": _canonical_partition(request.get("locked_oos_from"), "locked_oos_from"),
        "locked_oos_to": _canonical_partition(request.get("locked_oos_to"), "locked_oos_to"),
        "fresh_forward_from": _canonical_partition(request.get("fresh_forward_from"), "fresh_forward_from"),
        "fresh_forward_to": _canonical_partition(request.get("fresh_forward_to"), "fresh_forward_to"),
    }
    protected = protected_partition_manifest(**partitions)
    return {
        "source_id": source["source_id"],
        "source_bundle_path": source["bundle_path"],
        "source_bundle_sha256": source["bundle_sha256"],
        "source_identity_sha256": source["source_identity_sha256"],
        "broker": source["broker"],
        "feed": source["feed"],
        "source_timezone": source["source_timezone"],
        "partitions": partitions,
        "protected_manifest": protected,
    }


def _authorization(
    request: dict[str, Any],
    *,
    parent: dict[str, Any],
    path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    validated = _validate_start_request(request, parent=parent, path=path)
    sample_requirement = require_research_sample_configuration(path=path)
    body = {
        "schema": R01_AUTHORIZATION_SCHEMA,
        "research_id": parent["research_id"],
        "research_parent_id": parent["research_parent_id"],
        "parent_strategy_id": parent["parent_strategy_id"],
        "r00_parent_manifest_sha256": parent["r00_parent_manifest_sha256"],
        "gate": "R01",
        "action": "START",
        "confirmed": True,
        "owner_confirmation": OWNER_R01_CONFIRMATION,
        "source_id": validated["source_id"],
        "source_bundle_sha256": validated["source_bundle_sha256"],
        "source_identity_sha256": validated["source_identity_sha256"],
        "broker": validated["broker"],
        "feed": validated["feed"],
        "source_timezone": validated["source_timezone"],
        "protected_partitions": validated["protected_manifest"],
        "research_h1_minimum_trades_per_month": sample_requirement,
    }
    payload_sha = stable_hash(body)
    record = {
        "authorization_id": "RAUTH-R01-" + payload_sha[:24],
        "research_id": parent["research_id"],
        "gate": "R01",
        "action": "START",
        "confirmed": True,
        "payload_sha256": payload_sha,
        "payload": body,
        "authorized_utc": utc_now(),
    }
    return create_r01_authorization(record, path=path), validated


def _input_manifest(
    authorization: dict[str, Any],
    *,
    parent: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "schema": R01_INPUT_SCHEMA,
        "research_id": parent["research_id"],
        "gate": "R01",
        "authorization_id": authorization["authorization_id"],
        "authorization_sha256": authorization["payload_sha256"],
        "r00_parent_manifest_sha256": parent["r00_parent_manifest_sha256"],
        "parent_strategy_id": parent["parent_strategy_id"],
        "strategy_contract": parent["strategy_contract"],
        "feature_contract": parent["feature_contract"],
        "resolver_version": parent["mtf_resolver_version"],
        "strategy_geometry": deepcopy(parent["strategy_geometry"]),
        "research_h1_minimum_trades_per_month": int(
            authorization["payload"][
                "research_h1_minimum_trades_per_month"
            ]
        ),
        "dataset_builder_version": DATASET_BUILDER_VERSION,
        "label_contract": LABEL_CONTRACT_ID,
        "old_max_source_audit": R01_OLD_MAX_AUDIT,
        "source_id": authorization["payload"]["source_id"],
        "source_bundle_sha256": authorization["payload"]["source_bundle_sha256"],
        "source_identity_sha256": authorization["payload"]["source_identity_sha256"],
        "source_timezone": authorization["payload"]["source_timezone"],
        "protected_partitions": authorization["payload"]["protected_partitions"],
        "model_training_authorized": False,
        "onnx_authorized": False,
        "research_challenger_authorized": False,
        "champion_mutation_authorized": False,
        "r02_authorized": False,
    }
    return _seal(body)


def _paths(research_id: str, run_id: str, dataset_id: str | None = None) -> dict[str, Path]:
    run_root = RESEARCH_ARTIFACT_ROOT / research_id / "r01" / run_id
    root = (
        run_root / dataset_id
        if dataset_id
        else run_root
    )
    return {
        "root": root,
        "input": root / "r01_input_manifest.json",
        "dataset": root / "dataset.csv",
        "dataset_manifest": root / "dataset_manifest.json",
        "feature": root / "feature_manifest.json",
        "label": root / "label_manifest.json",
        "chronology": root / "chronology_report.json",
        "quality": root / "data_quality_report.json",
        "discovery_label_summary": root / "discovery_label_summary.json",
        "dependency": root / "dependency_report.json",
        "parity": root / "feature_parity_report.json",
        "protected": root / "protected_data_manifest.json",
        "leakage": root / "leakage_report.json",
        "output": root / "r01_output_manifest.json",
        "failure": root / "r01_failure.json",
    }


def _register_file(
    file_path: Path,
    *,
    artifact_type: str,
    research_id: str,
    run_id: str,
    dependencies: list[str],
    path: Path,
) -> str:
    artifact = register_artifact(
        artifact_type=artifact_type,
        producer="RESEARCH_R01",
        owner_type="RESEARCH_R01",
        owner_id=research_id,
        source_type="RESEARCH_R01_RUN",
        source_id=run_id,
        canonical_path=file_path,
        status="R01_STAGED",
        in_use=True,
        retention_class="ACTIVE_AUTHORITY",
        deletable=False,
        cleanable=False,
        dependencies=dependencies,
        path=path,
    )
    return str(artifact["artifact_id"])


def _validate_partition_coverage(
    protected: dict[str, Any],
    *,
    dataset: dict[str, Any],
) -> None:
    start = datetime.fromisoformat(dataset["data_quality_report"]["dataset_start"])
    end = datetime.fromisoformat(dataset["data_quality_report"]["dataset_end"])
    identity = protected["partition_identity"]
    discovery_start = datetime.fromisoformat(identity["discovery"]["from"])
    discovery_end = datetime.fromisoformat(identity["discovery"]["to"])
    locked_start = datetime.fromisoformat(identity["locked_oos"]["from"])
    locked_end = datetime.fromisoformat(identity["locked_oos"]["to"])
    fresh_start = datetime.fromisoformat(identity["fresh_forward"]["from"])
    fresh_end = datetime.fromisoformat(identity["fresh_forward"]["to"])
    if (
        discovery_start != start
        or fresh_end != end
        or discovery_end != locked_start
        or locked_end != fresh_start
    ):
        raise RuntimeError("R01_PROTECTED_PARTITIONS_MUST_COVER_DATASET_CONTIGUOUSLY")


def _aware_utc(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError(f"R01_DISCOVERY_LABEL_SUMMARY_TIMESTAMP_INVALID:{label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError(f"R01_DISCOVERY_LABEL_SUMMARY_TIMEZONE_REQUIRED:{label}")
    return parsed.astimezone(timezone.utc)


def _discovery_label_summary(
    *,
    dataset: dict[str, Any],
    protected: dict[str, Any],
) -> dict[str, Any]:
    identity = protected.get("partition_identity") or {}
    discovery_identity = identity.get("discovery") or {}
    discovery_from = _aware_utc(discovery_identity.get("from"), "discovery_from")
    discovery_to = _aware_utc(discovery_identity.get("to"), "discovery_to")
    if discovery_from >= discovery_to:
        raise RuntimeError("R01_DISCOVERY_LABEL_SUMMARY_BOUNDARY_INVALID")

    assignment = protected.get("row_assignment") or {}
    partition_rows = (assignment.get("partitions") or {}).get("discovery") or {}
    expected_physical_rows = int(partition_rows.get("row_count", -1))
    discovery_rows = [
        row
        for row in dataset["rows"]
        if discovery_from <= _aware_utc(row.get("signal_time"), "row_signal_time") < discovery_to
    ]
    if expected_physical_rows < 0 or len(discovery_rows) != expected_physical_rows:
        raise RuntimeError("R01_DISCOVERY_LABEL_SUMMARY_PARTITION_MISMATCH")

    target_authority = protected.get("target_dependency_authority") or {}
    if (
        target_authority.get("schema") != TARGET_DEPENDENCY_SCHEMA
        or target_authority.get("status") != "PASS"
    ):
        raise RuntimeError("R01_DISCOVERY_TARGET_DEPENDENCY_AUTHORITY_REQUIRED")
    boundary = target_authority.get("discovery_to_locked") or {}
    if boundary.get("overlap_check") != "PASS":
        raise RuntimeError("R01_DISCOVERY_TARGET_DEPENDENCY_OVERLAP")

    boundary_safe_rows = boundary_safe_partition_rows(
        dataset["rows"],
        protected,
        partition="discovery",
    )
    if len(boundary_safe_rows) != int(boundary.get("boundary_safe_physical_rows", -1)):
        raise RuntimeError("R01_DISCOVERY_TARGET_DEPENDENCY_BINDING_MISMATCH")

    supervised = [
        row
        for row in boundary_safe_rows
        if partition_supervised_eligible(
            row,
            protected,
            partition="discovery",
        )
    ]
    supervised_rows = len(supervised)
    if supervised_rows == 0:
        raise RuntimeError("R01_DISCOVERY_NO_SUPERVISED_ELIGIBLE_ROWS")

    counts = {0: 0, 1: 0, 2: 0}
    for row in supervised:
        label = row.get("label")
        if label not in counts:
            raise RuntimeError("R01_DISCOVERY_LABEL_SUMMARY_LABEL_INVALID")
        counts[int(label)] += 1

    target_invalid_rows = sum(
        row.get("target_valid") is not True
        for row in boundary_safe_rows
    )
    ambiguous_rows = sum(
        str(row.get("target_reason") or "") == "AMBIGUOUS_TP_SL_SAME_BAR"
        for row in boundary_safe_rows
    )
    incomplete_horizon_rows = sum(
        str(row.get("target_reason") or "") == "INCOMPLETE_FUTURE_HORIZON"
        for row in boundary_safe_rows
    )

    label_horizon = int(target_authority["label_horizon_main_bars"])
    last_supervised = supervised[-1]
    last_supervised_id = int(last_supervised["source_row_id"])
    latest_supervised_target_end = last_supervised_id + label_horizon
    first_locked_id = int(boundary["first_downstream_source_row_id"])
    if latest_supervised_target_end >= first_locked_id:
        raise RuntimeError("R01_DISCOVERY_TARGET_DEPENDENCY_OVERLAP")

    row_by_id = {
        int(row["source_row_id"]): row
        for row in dataset["rows"]
    }
    latest_target_row = row_by_id.get(latest_supervised_target_end)
    dataset_manifest = dataset["dataset_manifest"]
    source_dataset_lineage_sha256 = stable_hash({
        "research_id": dataset_manifest.get("research_id"),
        "dataset_id": dataset["dataset_id"],
        "dataset_manifest_sha256": dataset_manifest.get("manifest_sha256"),
        "source_identity_sha256": dataset_manifest.get("source_identity_sha256"),
        "source_data_hashes": dataset_manifest.get("source_data_hashes"),
    })
    body = {
        "schema": DISCOVERY_LABEL_SUMMARY_SCHEMA,
        "scope": "DISCOVERY_ONLY",
        "supervision_scope": "DISCOVERY_OWNED_TARGET_VALID_BOUNDARY_SAFE",
        "protected_outcome_rows_included": 0,
        "research_id": dataset_manifest.get("research_id"),
        "dataset_id": dataset["dataset_id"],
        "protected_partition_manifest_sha256": stable_hash(protected),
        "protected_partition_identity": deepcopy(identity),
        "discovery_from": discovery_from.isoformat(),
        "discovery_to": discovery_to.isoformat(),
        "physical_rows": len(discovery_rows),
        "boundary_safe_physical_rows": len(boundary_safe_rows),
        "boundary_excluded_from_supervision_rows": int(
            boundary["boundary_excluded_from_supervision_rows"]
        ),
        "supervised_rows": supervised_rows,
        "context_only_rows": len(discovery_rows) - supervised_rows,
        "target_invalid_rows": target_invalid_rows,
        "ambiguous_rows": ambiguous_rows,
        "incomplete_horizon_rows": incomplete_horizon_rows,
        "label_horizon_main_bars": label_horizon,
        "effective_boundary_purge_main_bars": int(
            target_authority["applied_boundary_purge_main_bars"]
        ),
        "original_row_identity_authority": "ORIGINAL_SOURCE_ROW_ID",
        "last_eligible_discovery_source_row_id": last_supervised_id,
        "last_eligible_discovery_signal_time": last_supervised["signal_time"].isoformat(),
        "latest_eligible_discovery_target_end_source_row_id": latest_supervised_target_end,
        "latest_eligible_discovery_target_end_signal_time": (
            latest_target_row["signal_time"].isoformat()
            if latest_target_row is not None
            else None
        ),
        "locked_oos_first_source_row_id": first_locked_id,
        "locked_oos_first_signal_time": boundary["first_downstream_signal_time"],
        "target_overlap_check": "PASS",
        "sell": counts[0],
        "skip": counts[1],
        "buy": counts[2],
        "sell_ratio": counts[0] / supervised_rows,
        "skip_ratio": counts[1] / supervised_rows,
        "buy_ratio": counts[2] / supervised_rows,
        "source_identity_sha256": dataset_manifest.get("source_identity_sha256"),
        "dataset_manifest_sha256": dataset_manifest.get("manifest_sha256"),
        "source_dataset_lineage_sha256": source_dataset_lineage_sha256,
    }
    return _seal(body)


def _output_manifest(
    *,
    input_manifest: dict[str, Any],
    dataset: dict[str, Any],
    protected: dict[str, Any],
    discovery_label_summary: dict[str, Any],
    leakage: dict[str, Any],
    file_hashes: dict[str, str],
    side_effects: dict[str, Any],
) -> dict[str, Any]:
    validators = {
        "R00_IMMUTABLE_INTEGRITY": True,
        "CHRONOLOGY": dataset["chronology_report"]["status"] == "PASS",
        "CP32_FEATURE_PARITY": dataset["feature_parity_report"]["status"] == "PASS",
        "DATA_QUALITY": dataset["data_quality_report"]["status"] == "PASS",
        "LABEL_CONTRACT": dataset["label_manifest"]["contract_id"] == LABEL_CONTRACT_ID,
        "DEPENDENCY_DERIVED": int(dataset["dependency_report"]["full_base_dependency_main_bars"]) > 0,
        "PROTECTED_DATA_BOUND": (
            protected["hidden_default"] is False
            and int(protected.get("overlap_count", -1)) == 0
            and int(protected.get("unassigned_count", -1)) == 0
            and protected.get("boundary_semantics") == {
                "discovery": "[from,to)",
                "locked_oos": "[from,to)",
                "fresh_forward": "[from,to]",
            }
            and (protected.get("target_dependency_authority") or {}).get("schema")
            == TARGET_DEPENDENCY_SCHEMA
            and (protected.get("target_dependency_authority") or {}).get("status")
            == "PASS"
            and (
                (protected.get("target_dependency_authority") or {})
                .get("discovery_to_locked", {})
                .get("overlap_check")
                == "PASS"
            )
            and (
                (protected.get("target_dependency_authority") or {})
                .get("locked_to_fresh", {})
                .get("overlap_check")
                == "PASS"
            )
        ),
        "DISCOVERY_LABEL_SUMMARY_ISOLATED": (
            discovery_label_summary.get("schema") == DISCOVERY_LABEL_SUMMARY_SCHEMA
            and discovery_label_summary.get("scope") == "DISCOVERY_ONLY"
            and int(discovery_label_summary.get("protected_outcome_rows_included", -1)) == 0
            and discovery_label_summary.get("research_id") == input_manifest["research_id"]
            and discovery_label_summary.get("dataset_id") == dataset["dataset_id"]
            and discovery_label_summary.get("dataset_manifest_sha256")
            == dataset["dataset_manifest"]["manifest_sha256"]
            and discovery_label_summary.get("protected_partition_manifest_sha256")
            == stable_hash(protected)
            and _verify_sealed_manifest(discovery_label_summary)
        ),
        "LEAKAGE_SUITE": leakage["status"] == "PASS",
        "DELIBERATE_LEAK_DETECTED": leakage["deliberately_leaky_pipeline"] == "DETECTED_FAIL",
        "NO_TRAINING_SIDE_EFFECTS": side_effects["status"] == "PASS",
        "R02_BLOCKED": True,
    }
    passed = all(validators.values())
    body = {
        "schema": R01_OUTPUT_SCHEMA,
        "research_id": input_manifest["research_id"],
        "gate": "R01",
        "gate_state": "PASS_WAITING_OWNER" if passed else "FAIL_WAITING_OWNER",
        "input_manifest_sha256": input_manifest["manifest_sha256"],
        "dataset_id": dataset["dataset_id"],
        "dataset_manifest_sha256": dataset["dataset_manifest"]["manifest_sha256"],
        "file_hashes": file_hashes,
        "validators": [
            {"validator": key, "status": "PASS" if value else "FAIL"}
            for key, value in validators.items()
        ],
        "base_dependency_horizon_main_bars": dataset["dependency_report"][
            "full_base_dependency_main_bars"
        ],
        "purge_main_bars": dataset["dependency_report"]["minimum_legal_purge_main_bars"],
        "embargo_main_bars": dataset["dependency_report"]["minimum_legal_embargo_main_bars"],
        "model_training": 0,
        "onnx": 0,
        "research_challenger": 0,
        "champion_mutation": "NONE",
        "r02_executable": False,
        "automatic_next_gate": False,
    }
    return _seal(body)


def _materialize_success(
    *,
    run_id: str,
    parent: dict[str, Any],
    input_manifest: dict[str, Any],
    validated: dict[str, Any],
    path: Path,
) -> tuple[dict[str, Any], list[str]]:
    dataset = build_dataset(
        Path(validated["source_bundle_path"]),
        parent=parent,
    )
    protected = bind_protected_partition_rows(
        dataset["rows"],
        validated["protected_manifest"],
    )
    protected = bind_protected_target_dependency_authority(
        dataset["rows"],
        protected,
        dataset["dependency_report"],
    )
    _validate_partition_coverage(protected, dataset=dataset)
    discovery_label_summary = _discovery_label_summary(
        dataset=dataset,
        protected=protected,
    )
    leakage = run_adversarial_suite(
        dataset,
        protected_manifest=protected,
        research_id=parent["research_id"],
        memory_path=path,
    )
    if leakage["status"] != "PASS":
        raise RuntimeError("R01_ADVERSARIAL_LEAKAGE_FAIL")
    side_effects = verify_no_training_side_effects(parent["research_id"], path=path)
    if side_effects["status"] != "PASS":
        raise RuntimeError("R01_MODEL_SIDE_EFFECT_DETECTED")

    paths = _paths(parent["research_id"], run_id, dataset["dataset_id"])
    if paths["root"].exists() and any(paths["root"].iterdir()):
        raise RuntimeError("R01_DATASET_ID_ALREADY_MATERIALIZED")
    paths["root"].mkdir(parents=True, exist_ok=False)
    _write_immutable_json(paths["input"], input_manifest)
    write_dataset_csv(paths["dataset"], dataset["rows"])
    _write_immutable_json(paths["dataset_manifest"], dataset["dataset_manifest"])
    _write_immutable_json(paths["feature"], dataset["feature_manifest"])
    _write_immutable_json(paths["label"], dataset["label_manifest"])
    _write_immutable_json(paths["chronology"], dataset["chronology_report"])
    _write_immutable_json(paths["quality"], dataset["data_quality_report"])
    _write_immutable_json(paths["discovery_label_summary"], discovery_label_summary)
    _write_immutable_json(paths["dependency"], dataset["dependency_report"])
    _write_immutable_json(paths["parity"], dataset["feature_parity_report"])
    _write_immutable_json(paths["protected"], protected)
    _write_immutable_json(paths["leakage"], leakage)

    hash_names = {
        "input": paths["input"], "dataset": paths["dataset"],
        "dataset_manifest": paths["dataset_manifest"], "feature_manifest": paths["feature"],
        "label_manifest": paths["label"], "chronology_report": paths["chronology"],
        "data_quality_report": paths["quality"],
        "discovery_label_summary": paths["discovery_label_summary"],
        "dependency_report": paths["dependency"],
        "feature_parity_report": paths["parity"], "protected_data_manifest": paths["protected"],
        "leakage_report": paths["leakage"],
    }
    file_hashes = {name: sha256_file(file_path) for name, file_path in hash_names.items()}
    output = _output_manifest(
        input_manifest=input_manifest,
        dataset=dataset,
        protected=protected,
        discovery_label_summary=discovery_label_summary,
        leakage=leakage,
        file_hashes=file_hashes,
        side_effects=side_effects,
    )
    _write_immutable_json(paths["output"], output)

    artifact_ids: list[str] = []
    dependency_ids: list[str] = []
    type_map = {
        "input": "RESEARCH_AUTHORITY_MANIFEST",
        "dataset": "RESEARCH_DATASET",
        "dataset_manifest": "RESEARCH_DATASET_MANIFEST",
        "feature_manifest": "FEATURE_MANIFEST",
        "label_manifest": "LABEL_MANIFEST",
        "chronology_report": "CHRONOLOGY_REPORT",
        "data_quality_report": "DATA_QUALITY_REPORT",
        "discovery_label_summary": "DISCOVERY_LABEL_SUMMARY",
        "dependency_report": "DEPENDENCY_REPORT",
        "feature_parity_report": "FEATURE_PARITY_REPORT",
        "protected_data_manifest": "PROTECTED_DATA_MANIFEST",
        "leakage_report": "LEAKAGE_REPORT",
    }
    for name, file_path in hash_names.items():
        aid = _register_file(
            file_path,
            artifact_type=type_map[name],
            research_id=parent["research_id"],
            run_id=run_id,
            dependencies=list(dependency_ids),
            path=path,
        )
        artifact_ids.append(aid)
        dependency_ids.append(aid)
    output_id = _register_file(
        paths["output"],
        artifact_type="RESEARCH_AUTHORITY_MANIFEST",
        research_id=parent["research_id"],
        run_id=run_id,
        dependencies=list(dependency_ids),
        path=path,
    )
    artifact_ids.append(output_id)
    return {
        "dataset": dataset,
        "protected": protected,
        "discovery_label_summary": discovery_label_summary,
        "leakage": leakage,
        "output": output,
        "paths": {key: str(value) for key, value in paths.items()},
    }, artifact_ids


def _failure_state(exc: Exception) -> str:
    text = str(exc)
    scientific_tokens = (
        "CP32",
        "DUPLICATE",
        "NON_MONOTONIC",
        "INVALID_OHLC",
        "FEATURE_",
        "LABEL_",
        "LEAKAGE",
        "CHRONOLOGY",
        "RELATIVE",
        "R01_SOURCE_",
        "R01_RAW_",
        "R01_EA_",
        "R01_ENTRY_",
        "R01_ROLE_",
        "R01_BROKER_",
        "R01_POINT_",
        "R01_TIMESTAMP_",
        "R01_NON_NUMERIC",
        "R01_NONFINITE",
        "R01_NO_SUPERVISED",
        "R01_PROTECTED_",
        "R01_DISCOVERY_",
        "R01_ADVERSARIAL_",
        "ROLE_TIMEFRAME_MISMATCH",
        "ROLE_SYMBOL_MISMATCH",
    )
    return (
        "FAIL_WAITING_OWNER"
        if any(token in text for token in scientific_tokens)
        else "ERROR_WAITING_OWNER"
    )


def _materialize_failure(
    *,
    run_id: str,
    parent: dict[str, Any],
    input_manifest: dict[str, Any],
    exc: Exception,
    state: str,
    path: Path,
) -> tuple[dict[str, Any], list[str]]:
    paths = _paths(parent["research_id"], run_id)
    paths["root"].mkdir(parents=True, exist_ok=True)
    _write_immutable_json(paths["input"], input_manifest)
    failure = {
        "schema": "MAX_RESEARCH_R01_FAILURE_V1",
        "research_id": parent["research_id"],
        "gate": "R01",
        "state": state,
        "reason": str(exc),
        "model_training": 0,
        "onnx": 0,
        "research_challenger": 0,
        "champion_mutation": "NONE",
        "r02_executable": False,
    }
    _write_immutable_json(paths["failure"], failure)
    output = _seal({
        "schema": R01_OUTPUT_SCHEMA,
        "research_id": parent["research_id"],
        "gate": "R01",
        "gate_state": state,
        "input_manifest_sha256": input_manifest["manifest_sha256"],
        "dataset_id": None,
        "failure": failure,
        "automatic_next_gate": False,
        "r02_executable": False,
    })
    _write_immutable_json(paths["output"], output)
    artifact_ids = [
        _register_file(
            paths["input"], artifact_type="RESEARCH_AUTHORITY_MANIFEST",
            research_id=parent["research_id"], run_id=run_id,
            dependencies=[], path=path,
        )
    ]
    artifact_ids.append(
        _register_file(
            paths["failure"], artifact_type="DATA_QUALITY_REPORT",
            research_id=parent["research_id"], run_id=run_id,
            dependencies=list(artifact_ids), path=path,
        )
    )
    artifact_ids.append(
        _register_file(
            paths["output"], artifact_type="RESEARCH_AUTHORITY_MANIFEST",
            research_id=parent["research_id"], run_id=run_id,
            dependencies=list(artifact_ids), path=path,
        )
    )
    return {"failure": failure, "output": output, "paths": paths}, artifact_ids


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"R01_JSON_ARTIFACT_UNREADABLE:{path.name}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"R01_JSON_ARTIFACT_NOT_OBJECT:{path.name}")
    return value


def _verify_sealed_manifest(payload: dict[str, Any]) -> bool:
    expected = str(payload.get("manifest_sha256") or "")
    if not expected:
        return False
    body = deepcopy(payload)
    body.pop("manifest_sha256", None)
    return stable_hash(body) == expected


def validate_r01_integrity(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    parent = _normalized_r00_parent(path=path)
    run = get_r01_run(parent["research_id"], path=path)
    if run is None:
        return {
            "status": "NOT_STARTED",
            "research_id": parent["research_id"],
            "checks": {},
            "failures": [],
        }
    if run["state"] == "STARTING":
        return {
            "status": "IN_PROGRESS",
            "research_id": parent["research_id"],
            "checks": {"terminal_state": False},
            "failures": ["terminal_state"],
        }

    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT artifact_id,artifact_type,owner_type,owner_id,source_type,
                   source_id,canonical_path,sha256,status,retention_class,
                   in_use,dependencies_json
            FROM artifact_registry
            WHERE owner_type='RESEARCH_R01'
              AND owner_id=?
              AND source_type='RESEARCH_R01_RUN'
              AND source_id=?
            ORDER BY artifact_id
            """,
            (parent["research_id"], run["run_id"]),
        ).fetchall()
        terminal_events = conn.execute(
            """
            SELECT * FROM research_gate_events
            WHERE research_id=? AND gate='R01' AND from_state='STARTING'
            ORDER BY created_utc,event_id
            """,
            (parent["research_id"],),
        ).fetchall()
        memory_rows = conn.execute(
            """
            SELECT * FROM research_memory_events
            WHERE research_id=? AND stage='R01'
              AND event_type='R01_DATA_FOUNDATION_SEALED'
            ORDER BY created_utc,event_id
            """,
            (parent["research_id"],),
        ).fetchall()

    artifacts = [dict(row) for row in rows]
    by_name = {Path(str(row["canonical_path"])).name: row for row in artifacts}
    registered_ids = {str(row["artifact_id"]) for row in artifacts}
    bound_ids = {str(value) for value in run["artifact_ids"]}

    files_ok = True
    file_hashes_ok = True
    for row in artifacts:
        item = Path(str(row["canonical_path"]))
        if not item.is_file():
            files_ok = False
            continue
        expected = str(row["sha256"] or "")
        if not expected or sha256_file(item) != expected:
            file_hashes_ok = False

    output_row = by_name.get("r01_output_manifest.json")
    input_row = by_name.get("r01_input_manifest.json")
    output: dict[str, Any] | None = None
    gate_output_file_hash_ok = False
    if output_row is not None:
        output_path = Path(str(output_row["canonical_path"]))
        if output_path.is_file():
            output = _read_json(output_path)
            gate_output_file_hash_ok = (
                str(output_row["sha256"] or "") == sha256_file(output_path)
            )

    gate_input: dict[str, Any] | None = None
    if input_row is not None:
        input_path = Path(str(input_row["canonical_path"]))
        if input_path.is_file():
            gate_input = _read_json(input_path)

    terminal_event_ok = (
        len(terminal_events) == 1
        and str(terminal_events[0]["to_state"]) == str(run["state"])
        and str(terminal_events[0]["input_manifest_sha"] or "")
        == str(run["input_manifest_sha"] or "")
        and str(terminal_events[0]["output_manifest_sha"] or "")
        == str(run["output_manifest_sha"] or "")
    )
    memory_ok = (
        len(memory_rows) == 1
        and str(memory_rows[0]["status"]) == str(run["state"])
        and int(memory_rows[0]["adaptive_eligible"]) == 0
        and str(memory_rows[0]["source_manifest_sha256"] or "")
        == str(run["output_manifest_sha"] or "")
    )
    artifact_status_ok = all(
        str(row["status"]) == str(run["state"]) for row in artifacts
    )
    artifact_authority_ok = all(
        str(row["retention_class"]) == "ACTIVE_AUTHORITY"
        and int(row["in_use"]) == 1
        for row in artifacts
    )
    output_dependencies_ok = False
    if output_row is not None:
        dependencies = set(json.loads(str(output_row["dependencies_json"] or "[]")))
        output_dependencies_ok = dependencies == (registered_ids - {str(output_row["artifact_id"])})

    output_sealed_ok = bool(output and _verify_sealed_manifest(output))
    input_sealed_ok = bool(gate_input and _verify_sealed_manifest(gate_input))
    output_state_ok = bool(
        output
        and output.get("gate_state") == run["state"]
        and output.get("input_manifest_sha256") == run["input_manifest_sha"]
        and output.get("manifest_sha256") == run["output_manifest_sha"]
        and output.get("r02_executable") is False
    )
    input_state_ok = bool(
        gate_input
        and gate_input.get("manifest_sha256") == run["input_manifest_sha"]
        and gate_input.get("research_id") == parent["research_id"]
        and gate_input.get("r02_authorized") is False
        and gate_input.get("model_training_authorized") is False
        and gate_input.get("onnx_authorized") is False
        and gate_input.get("research_challenger_authorized") is False
        and gate_input.get("champion_mutation_authorized") is False
    )

    dataset_binding_ok = False
    success_evidence_ok = True
    if run["state"] == "PASS_WAITING_OWNER":
        dataset_manifest_row = by_name.get("dataset_manifest.json")
        quality_row = by_name.get("data_quality_report.json")
        leakage_row = by_name.get("leakage_report.json")
        protected_row = by_name.get("protected_data_manifest.json")
        discovery_summary_row = by_name.get("discovery_label_summary.json")
        if all(row is not None for row in (
            dataset_manifest_row, quality_row, leakage_row, protected_row,
            discovery_summary_row,
        )):
            dataset_manifest = _read_json(Path(str(dataset_manifest_row["canonical_path"])))
            quality = _read_json(Path(str(quality_row["canonical_path"])))
            leakage = _read_json(Path(str(leakage_row["canonical_path"])))
            protected = _read_json(Path(str(protected_row["canonical_path"])))
            discovery_summary = _read_json(
                Path(str(discovery_summary_row["canonical_path"]))
            )
            dataset_binding_ok = bool(
                output
                and run["dataset_id"]
                and output.get("dataset_id") == run["dataset_id"]
                and dataset_manifest.get("dataset_id") == run["dataset_id"]
                and _verify_sealed_manifest(dataset_manifest)
                and output.get("dataset_manifest_sha256")
                == dataset_manifest.get("manifest_sha256")
                and quality.get("manifest_hash")
                == dataset_manifest.get("manifest_sha256")
            )
            success_evidence_ok = bool(
                leakage.get("status") == "PASS"
                and leakage.get("legal_pipeline") == "PASS"
                and leakage.get("deliberately_leaky_pipeline") == "DETECTED_FAIL"
                and protected.get("hidden_default") is False
                and protected.get("locked_oos", {}).get("adaptive_access") is False
                and protected.get("fresh_forward", {}).get("adaptive_access") is False
                and int(protected.get("overlap_count", -1)) == 0
                and int(protected.get("unassigned_count", -1)) == 0
                and protected.get("boundary_semantics") == {
                    "discovery": "[from,to)",
                    "locked_oos": "[from,to)",
                    "fresh_forward": "[from,to]",
                }
                and (protected.get("target_dependency_authority") or {}).get("schema")
                == TARGET_DEPENDENCY_SCHEMA
                and (protected.get("target_dependency_authority") or {}).get("status")
                == "PASS"
                and (
                    (protected.get("target_dependency_authority") or {})
                    .get("discovery_to_locked", {})
                    .get("overlap_check")
                    == "PASS"
                )
                and (
                    (protected.get("target_dependency_authority") or {})
                    .get("locked_to_fresh", {})
                    .get("overlap_check")
                    == "PASS"
                )
                and discovery_summary.get("schema") == DISCOVERY_LABEL_SUMMARY_SCHEMA
                and discovery_summary.get("scope") == "DISCOVERY_ONLY"
                and discovery_summary.get("supervision_scope")
                == "DISCOVERY_OWNED_TARGET_VALID_BOUNDARY_SAFE"
                and discovery_summary.get("target_overlap_check") == "PASS"
                and int(discovery_summary.get("protected_outcome_rows_included", -1)) == 0
                and discovery_summary.get("dataset_id") == run["dataset_id"]
                and discovery_summary.get("dataset_manifest_sha256")
                == dataset_manifest.get("manifest_sha256")
                and discovery_summary.get("protected_partition_manifest_sha256")
                == stable_hash(protected)
                and _verify_sealed_manifest(discovery_summary)
            )
            if output and isinstance(output.get("file_hashes"), dict):
                filename_by_key = {
                    "input": "r01_input_manifest.json",
                    "dataset": "dataset.csv",
                    "dataset_manifest": "dataset_manifest.json",
                    "feature_manifest": "feature_manifest.json",
                    "label_manifest": "label_manifest.json",
                    "chronology_report": "chronology_report.json",
                    "data_quality_report": "data_quality_report.json",
                    "discovery_label_summary": "discovery_label_summary.json",
                    "dependency_report": "dependency_report.json",
                    "feature_parity_report": "feature_parity_report.json",
                    "protected_data_manifest": "protected_data_manifest.json",
                    "leakage_report": "leakage_report.json",
                }
                for key, filename in filename_by_key.items():
                    row = by_name.get(filename)
                    if row is None:
                        file_hashes_ok = False
                        continue
                    if output["file_hashes"].get(key) != str(row["sha256"] or ""):
                        file_hashes_ok = False
            else:
                file_hashes_ok = False
        else:
            dataset_binding_ok = False
            success_evidence_ok = False
    else:
        dataset_binding_ok = bool(
            output
            and run["dataset_id"] is None
            and output.get("dataset_id") is None
            and "r01_failure.json" in by_name
        )

    side_effects = verify_no_training_side_effects(parent["research_id"], path=path)
    checks = {
        "artifact_set_binding": registered_ids == bound_ids and bool(bound_ids),
        "artifact_terminal_state": artifact_status_ok,
        "artifact_authority": artifact_authority_ok,
        "artifact_files_exist": files_ok,
        "artifact_file_hashes": file_hashes_ok,
        "output_artifact_hash": gate_output_file_hash_ok,
        "output_dependency_lineage": output_dependencies_ok,
        "input_manifest_sealed": input_sealed_ok,
        "input_manifest_authority": input_state_ok,
        "output_manifest_sealed": output_sealed_ok,
        "db_output_gate_state_binding": output_state_ok,
        "dataset_identity_binding": dataset_binding_ok,
        "terminal_gate_event_binding": terminal_event_ok,
        "research_memory_binding": memory_ok,
        "protected_and_leakage_evidence": success_evidence_ok,
        "no_training_onnx_challenger_champion_side_effect": side_effects["status"] == "PASS",
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "status": "VERIFIED" if not failures else "INTEGRITY_FAIL",
        "research_id": parent["research_id"],
        "run_id": run["run_id"],
        "gate_state": run["state"],
        "dataset_id": run["dataset_id"],
        "checks": checks,
        "failures": failures,
        "artifact_count": len(artifacts),
    }


def _uncommitted_run_root(research_id: str, run_id: str) -> Path:
    root = _paths(research_id, run_id)["root"].resolve()
    authority = RESEARCH_ARTIFACT_ROOT.resolve()
    if not root.is_relative_to(authority):
        raise RuntimeError("R01_STAGING_PATH_OUTSIDE_AUTHORITY")
    return root


def _discard_uncommitted_materialization(
    *,
    research_id: str,
    run_id: str,
    path: Path,
) -> None:
    discard_staged_r01_artifacts(research_id, run_id, path=path)
    root = _uncommitted_run_root(research_id, run_id)
    if root.exists():
        shutil.rmtree(root)


def _recover_incomplete_r01(
    *,
    parent: dict[str, Any],
    path: Path,
) -> bool:
    run = get_r01_run(parent["research_id"], path=path)
    if run is None or run["state"] != "STARTING":
        return False
    authorization = get_r01_authorization(run["authorization_id"], path=path)
    if authorization is None:
        raise RuntimeError("R01_RECOVERY_AUTHORIZATION_MISSING")
    if authorization["research_id"] != parent["research_id"]:
        raise RuntimeError("R01_RECOVERY_AUTHORIZATION_MISMATCH")
    input_manifest = _input_manifest(authorization, parent=parent)
    if input_manifest["manifest_sha256"] != run["input_manifest_sha"]:
        raise RuntimeError("R01_RECOVERY_INPUT_MANIFEST_MISMATCH")

    _discard_uncommitted_materialization(
        research_id=parent["research_id"],
        run_id=run["run_id"],
        path=path,
    )
    recovery_error = RuntimeError("RECOVERED_INCOMPLETE_R01_WITHOUT_RERUN")
    failure, artifact_ids = _materialize_failure(
        run_id=run["run_id"],
        parent=parent,
        input_manifest=input_manifest,
        exc=recovery_error,
        state="ERROR_WAITING_OWNER",
        path=path,
    )
    stage_r01_artifacts(parent["research_id"], artifact_ids, path=path)
    commit_r01_terminal_authority(
        research_id=parent["research_id"],
        state="ERROR_WAITING_OWNER",
        dataset_id=None,
        input_manifest_sha=input_manifest["manifest_sha256"],
        output_manifest_sha=failure["output"]["manifest_sha256"],
        artifact_ids=artifact_ids,
        authorization_id=authorization["authorization_id"],
        memory_payload={
            "dataset_id": None,
            "failure_reason": str(recovery_error),
            "recovery": "FAIL_CLOSED_WITHOUT_DATASET_RERUN",
            "protected_feedback_adaptive_eligible": False,
        },
        error=str(recovery_error),
        path=path,
    )
    return True


def start_r01(
    request: dict[str, Any],
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    parent = _normalized_r00_parent(path=path)
    _recover_incomplete_r01(parent=parent, path=path)
    authorization, validated = _authorization(
        request,
        parent=parent,
        path=path,
    )
    input_manifest = _input_manifest(authorization, parent=parent)
    run_id = "RRUN-R01-" + stable_hash({
        "research_id": parent["research_id"],
        "authorization_id": authorization["authorization_id"],
        "input_manifest_sha": input_manifest["manifest_sha256"],
    })[:24]
    existing = get_r01_run(parent["research_id"], path=path)
    if existing is not None:
        if existing["run_id"] != run_id:
            raise RuntimeError("R01_ALREADY_BOUND_TO_DIFFERENT_AUTHORITY")
        result = r01_detail(path=path)
        result["idempotent"] = True
        return result

    create_r01_run(
        run_id=run_id,
        research_id=parent["research_id"],
        authorization_id=authorization["authorization_id"],
        input_manifest_sha=input_manifest["manifest_sha256"],
        path=path,
    )
    try:
        result, artifact_ids = _materialize_success(
            run_id=run_id,
            parent=parent,
            input_manifest=input_manifest,
            validated=validated,
            path=path,
        )
        stage_r01_artifacts(parent["research_id"], artifact_ids, path=path)
        output = result["output"]
        terminal = str(output["gate_state"])
        committed = commit_r01_terminal_authority(
            research_id=parent["research_id"],
            state=terminal,
            dataset_id=result["dataset"]["dataset_id"],
            input_manifest_sha=input_manifest["manifest_sha256"],
            output_manifest_sha=output["manifest_sha256"],
            artifact_ids=artifact_ids,
            authorization_id=authorization["authorization_id"],
            memory_payload={
                "dataset_id": result["dataset"]["dataset_id"],
                "dataset_manifest_sha256": result["dataset"]["dataset_manifest"]["manifest_sha256"],
                "feature_hash": result["dataset"]["data_quality_report"]["feature_hash"],
                "discovery_label_summary_sha256": result["discovery_label_summary"]["manifest_sha256"],
                "leakage_status": result["leakage"]["status"],
                "protected_feedback_adaptive_eligible": False,
            },
            path=path,
        )
        return {**r01_detail(path=path), "commit": committed, "idempotent": False}
    except Exception as exc:
        current = get_r01_run(parent["research_id"], path=path)
        if current is not None and current["state"] == "STARTING":
            _discard_uncommitted_materialization(
                research_id=parent["research_id"],
                run_id=run_id,
                path=path,
            )
            state = _failure_state(exc)
            failure, artifact_ids = _materialize_failure(
                run_id=run_id,
                parent=parent,
                input_manifest=input_manifest,
                exc=exc,
                state=state,
                path=path,
            )
            stage_r01_artifacts(parent["research_id"], artifact_ids, path=path)
            commit_r01_terminal_authority(
                research_id=parent["research_id"],
                state=state,
                dataset_id=None,
                input_manifest_sha=input_manifest["manifest_sha256"],
                output_manifest_sha=failure["output"]["manifest_sha256"],
                artifact_ids=artifact_ids,
                authorization_id=authorization["authorization_id"],
                memory_payload={
                    "dataset_id": None,
                    "failure_reason": str(exc),
                    "protected_feedback_adaptive_eligible": False,
                },
                error=str(exc),
                path=path,
            )
        raise


def r01_detail(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    if latest_research(path=path) is None:
        assert_no_orphaned_research_authority(path=path)
        sample_config = get_research_sample_configuration(path=path)
        return {
            "schema": R01_SCHEMA,
            "status": "NOT_STARTED",
            "stage": "DATA_FOUNDATION",
            "research_id": None,
            "parent_strategy_id": None,
            "parent_integrity": "NOT_STARTED",
            "research_h1_minimum_trades_per_month": sample_config[
                "h1_minimum_trades_per_month"
            ],
            "historical_r00_h1_minimum_trades_per_month": None,
            "execution_h1_minimum_trades_per_month": None,
            "run": None,
            "integrity": {"status": "NOT_STARTED", "failures": []},
            "model_training": 0,
            "onnx": 0,
            "research_challenger": 0,
            "champion_mutation": "NONE",
            "r02_executable": False,
            "next_gate_authorized": False,
        }
    parent = _normalized_r00_parent(path=path)
    run = get_r01_run(parent["research_id"], path=path)
    sample_config = get_research_sample_configuration(path=path)
    execution_sample = None
    if run is not None:
        authorization = get_r01_authorization(
            run["authorization_id"],
            path=path,
        )
        if authorization is None:
            raise RuntimeError("R01_SAMPLE_SNAPSHOT_AUTHORITY_MISSING")
        execution_sample = int(
            authorization["payload"][
                "research_h1_minimum_trades_per_month"
            ]
        )
    integrity = validate_r01_integrity(path=path) if run is not None else {
        "status": "NOT_STARTED",
        "failures": [],
    }
    return {
        "schema": R01_SCHEMA,
        "stage": "DATA_FOUNDATION",
        "research_id": parent["research_id"],
        "parent_strategy_id": parent["parent_strategy_id"],
        "parent_integrity": "VERIFIED",
        "research_h1_minimum_trades_per_month": sample_config[
            "h1_minimum_trades_per_month"
        ],
        "historical_r00_h1_minimum_trades_per_month": parent[
            "historical_r00_h1_minimum_trades_per_month"
        ],
        "execution_h1_minimum_trades_per_month": execution_sample,
        "run": run,
        "integrity": integrity,
        "model_training": 0,
        "onnx": 0,
        "research_challenger": 0,
        "champion_mutation": "NONE",
        "r02_executable": False,
        "next_gate_authorized": False,
    }
