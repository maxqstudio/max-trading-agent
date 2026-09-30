from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .research_cp32 import (
    FEATURE_NAMES,
    latest_fully_closed_index,
    prepare_role_series,
)

LEAKAGE_SCHEMA = "MAX_RESEARCH_ADVERSARIAL_LEAKAGE_R01_V1"
TARGET_DEPENDENCY_SCHEMA = "MAX_RESEARCH_PROTECTED_TARGET_DEPENDENCY_R01_V1"
OLD_MAX_TEMPORAL_AUTHORITY = (
    "maxqstudio/max_research_agent@3e969efcdeb4ca6a2ae63acbd80592e378d2a446:"
    "ModelLab/core/temporal_index.py"
)


def _gate(name: str, passed: bool, evidence: Any) -> dict[str, Any]:
    return {
        "attack": name,
        "status": "PASS" if passed else "FAIL",
        "evidence": evidence,
    }


def _synthetic_future_perturbation(causal: bool) -> bool:
    values = [float(i) for i in range(64)]
    cut = 40

    def feature(data: list[float]) -> list[float]:
        if causal:
            return [
                sum(data[max(0, i - 3) : i + 1]) / len(data[max(0, i - 3) : i + 1])
                for i in range(len(data))
            ]
        return [
            sum(data[max(0, i - 1) : min(len(data), i + 2)])
            / len(data[max(0, i - 1) : min(len(data), i + 2)])
            for i in range(len(data))
        ]

    before = feature(values)[:cut]
    perturbed = list(values)
    for i in range(cut, len(perturbed)):
        perturbed[i] += 1_000_000.0
    after = feature(perturbed)[:cut]
    return before == after


def validate_feature_offset(offset: int) -> None:
    from .research_dataset import (
        FEATURE_INFORMATION_CONTRACT,
        validate_feature_information_contract,
    )

    payload = deepcopy(FEATURE_INFORMATION_CONTRACT)
    payload["maximum_future_offset"] = int(offset)
    try:
        validate_feature_information_contract(payload)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc


def validate_preprocessing_fit_scope(scope: str) -> None:
    from .research_dataset import (
        FEATURE_INFORMATION_CONTRACT,
        validate_feature_information_contract,
    )

    payload = deepcopy(FEATURE_INFORMATION_CONTRACT)
    payload["preprocessing_fit_scope"] = (
        "NONE_OR_TRAINING_ONLY" if str(scope) == "TRAINING_ONLY" else str(scope)
    )
    try:
        validate_feature_information_contract(payload)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc


def validate_source_fill_method(method: str) -> None:
    from .research_dataset import (
        FEATURE_INFORMATION_CONTRACT,
        validate_feature_information_contract,
    )

    payload = deepcopy(FEATURE_INFORMATION_CONTRACT)
    payload["source_fill"] = str(method)
    try:
        validate_feature_information_contract(payload)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc


def _information_contract_attack(
    *,
    mutation: dict[str, Any],
    expected_error: str,
) -> dict[str, Any]:
    from .research_dataset import (
        FEATURE_INFORMATION_CONTRACT,
        validate_feature_information_contract,
    )

    payload = deepcopy(FEATURE_INFORMATION_CONTRACT)
    payload.update(mutation)
    rejected = False
    reason = ""
    try:
        validate_feature_information_contract(payload)
    except RuntimeError as exc:
        reason = str(exc)
        rejected = reason.startswith(expected_error)
    return {
        "passed": rejected,
        "mutation": mutation,
        "production_validator": "validate_feature_information_contract",
        "reason": reason,
    }


def feature_contract_is_legal(names: Any) -> bool:
    try:
        return tuple(str(value) for value in names) == FEATURE_NAMES
    except Exception:
        return False


def _aware_datetime(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"R01_PROTECTED_PARTITION_INVALID:{label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"R01_PROTECTED_PARTITION_TIMEZONE_REQUIRED:{label}")
    return parsed.astimezone(timezone.utc)


def assign_protected_partition_rows(
    rows: list[dict[str, Any]],
    protected_manifest: dict[str, Any],
) -> dict[str, Any]:
    identity = protected_manifest.get("partition_identity") or {}
    discovery = identity.get("discovery") or {}
    locked = identity.get("locked_oos") or {}
    fresh = identity.get("fresh_forward") or {}
    d0 = _aware_datetime(discovery.get("from"), "discovery_from")
    d1 = _aware_datetime(discovery.get("to"), "discovery_to")
    l0 = _aware_datetime(locked.get("from"), "locked_oos_from")
    l1 = _aware_datetime(locked.get("to"), "locked_oos_to")
    f0 = _aware_datetime(fresh.get("from"), "fresh_forward_from")
    f1 = _aware_datetime(fresh.get("to"), "fresh_forward_to")

    if not (d0 < d1 == l0 < l1 == f0 < f1):
        raise ValueError("R01_PROTECTED_PARTITION_NOT_CONTIGUOUS")

    owned: dict[str, list[dict[str, Any]]] = {
        "discovery": [],
        "locked_oos": [],
        "fresh_forward": [],
    }
    overlap_count = 0
    unassigned_count = 0
    for row in rows:
        timestamp = _aware_datetime(row.get("signal_time"), "row_signal_time")
        membership = []
        if d0 <= timestamp < d1:
            membership.append("discovery")
        if l0 <= timestamp < l1:
            membership.append("locked_oos")
        if f0 <= timestamp <= f1:
            membership.append("fresh_forward")
        if len(membership) == 0:
            unassigned_count += 1
            continue
        if len(membership) > 1:
            overlap_count += 1
            continue
        owned[membership[0]].append(row)

    def summary(name: str) -> dict[str, Any]:
        items = owned[name]
        return {
            "row_count": len(items),
            "first_owned_row": (
                {
                    "source_row_id": int(items[0]["source_row_id"]),
                    "signal_time": items[0]["signal_time"].isoformat(),
                }
                if items else None
            ),
            "last_owned_row": (
                {
                    "source_row_id": int(items[-1]["source_row_id"]),
                    "signal_time": items[-1]["signal_time"].isoformat(),
                }
                if items else None
            ),
        }

    return {
        "row_timestamp_authority": "signal_time",
        "boundary_semantics": {
            "discovery": "[from,to)",
            "locked_oos": "[from,to)",
            "fresh_forward": "[from,to]",
        },
        "partitions": {
            "discovery": summary("discovery"),
            "locked_oos": summary("locked_oos"),
            "fresh_forward": summary("fresh_forward"),
        },
        "overlap_count": overlap_count,
        "unassigned_count": unassigned_count,
        "total_rows": len(rows),
        "assigned_rows": sum(len(value) for value in owned.values()),
    }


def bind_protected_partition_rows(
    rows: list[dict[str, Any]],
    protected_manifest: dict[str, Any],
) -> dict[str, Any]:
    assignment = assign_protected_partition_rows(rows, protected_manifest)
    if assignment["overlap_count"] != 0:
        raise RuntimeError("R01_PROTECTED_PARTITION_ROW_OVERLAP")
    if assignment["unassigned_count"] != 0:
        raise RuntimeError("R01_PROTECTED_PARTITION_ROW_UNASSIGNED")
    if assignment["assigned_rows"] != assignment["total_rows"]:
        raise RuntimeError("R01_PROTECTED_PARTITION_ROW_COVERAGE_MISMATCH")
    result = deepcopy(protected_manifest)
    result["row_assignment"] = assignment
    result["boundary_semantics"] = assignment["boundary_semantics"]
    result["row_timestamp_authority"] = assignment["row_timestamp_authority"]
    result["overlap_count"] = 0
    result["unassigned_count"] = 0
    return result


def _partition_rows(
    rows: list[dict[str, Any]],
    protected_manifest: dict[str, Any],
    partition: str,
) -> list[dict[str, Any]]:
    identity = (protected_manifest.get("partition_identity") or {}).get(partition) or {}
    start = _aware_datetime(identity.get("from"), f"{partition}_from")
    end = _aware_datetime(identity.get("to"), f"{partition}_to")
    if start >= end:
        raise RuntimeError(f"R01_PROTECTED_TARGET_BOUNDARY_INVALID:{partition}")
    inclusive_end = partition == "fresh_forward"
    selected = []
    for row in rows:
        timestamp = _aware_datetime(row.get("signal_time"), "row_signal_time")
        if start <= timestamp < end or (inclusive_end and timestamp == end):
            selected.append(row)
    return selected


def bind_protected_target_dependency_authority(
    rows: list[dict[str, Any]],
    protected_manifest: dict[str, Any],
    dependency_report: dict[str, Any],
) -> dict[str, Any]:
    """Bind MAX MTF Old target-purge semantics without removing physical context rows."""
    assignment = protected_manifest.get("row_assignment")
    if not isinstance(assignment, dict):
        raise RuntimeError("R01_PROTECTED_TARGET_ROW_ASSIGNMENT_REQUIRED")

    row_ids = [int(row["source_row_id"]) for row in rows]
    if row_ids != sorted(row_ids) or len(set(row_ids)) != len(row_ids):
        raise RuntimeError("R01_PROTECTED_TARGET_SOURCE_ROW_ID_INVALID")
    if any(right != left + 1 for left, right in zip(row_ids, row_ids[1:])):
        raise RuntimeError("R01_PROTECTED_TARGET_SOURCE_ROW_ID_GAP")
    row_by_id = {int(row["source_row_id"]): row for row in rows}

    label_horizon = int(dependency_report.get("label_dependency_main_bars", 0))
    requested_purge = int(dependency_report.get("minimum_legal_purge_main_bars", 0))
    embargo = int(dependency_report.get("minimum_legal_embargo_main_bars", 0))
    if label_horizon <= 0 or requested_purge <= 0 or embargo <= 0:
        raise RuntimeError("R01_PROTECTED_TARGET_DEPENDENCY_AUTHORITY_INVALID")
    effective_purge = max(label_horizon, requested_purge)

    def boundary(upstream: str, downstream: str) -> dict[str, Any]:
        upstream_rows = _partition_rows(rows, protected_manifest, upstream)
        downstream_rows = _partition_rows(rows, protected_manifest, downstream)
        expected_upstream = int(
            ((assignment.get("partitions") or {}).get(upstream) or {}).get("row_count", -1)
        )
        expected_downstream = int(
            ((assignment.get("partitions") or {}).get(downstream) or {}).get("row_count", -1)
        )
        if (
            expected_upstream < 0
            or expected_downstream < 0
            or len(upstream_rows) != expected_upstream
            or len(downstream_rows) != expected_downstream
            or not upstream_rows
            or not downstream_rows
        ):
            raise RuntimeError("R01_PROTECTED_TARGET_PARTITION_BINDING_MISMATCH")

        first_downstream = int(downstream_rows[0]["source_row_id"])
        eligibility_cutoff_exclusive = first_downstream - effective_purge
        boundary_safe = [
            row
            for row in upstream_rows
            if int(row["source_row_id"]) < eligibility_cutoff_exclusive
        ]
        excluded = len(upstream_rows) - len(boundary_safe)
        last_safe = boundary_safe[-1] if boundary_safe else None
        latest_target_end = (
            int(last_safe["source_row_id"]) + label_horizon
            if last_safe is not None
            else None
        )
        overlap_ok = latest_target_end is None or latest_target_end < first_downstream
        if not overlap_ok:
            raise RuntimeError("R01_PROTECTED_TARGET_DEPENDENCY_OVERLAP")

        latest_target_row = (
            row_by_id.get(latest_target_end)
            if latest_target_end is not None
            else None
        )
        return {
            "upstream_partition": upstream,
            "downstream_partition": downstream,
            "row_identity_authority": "ORIGINAL_SOURCE_ROW_ID",
            "upstream_physical_rows": len(upstream_rows),
            "boundary_safe_physical_rows": len(boundary_safe),
            "boundary_excluded_from_supervision_rows": excluded,
            "first_downstream_source_row_id": first_downstream,
            "first_downstream_signal_time": downstream_rows[0]["signal_time"].isoformat(),
            "eligibility_rule": (
                "source_row_id < first_downstream_source_row_id - "
                "applied_boundary_purge_main_bars"
            ),
            "eligibility_cutoff_source_row_id_exclusive": eligibility_cutoff_exclusive,
            "last_boundary_safe_source_row_id": (
                int(last_safe["source_row_id"]) if last_safe is not None else None
            ),
            "last_boundary_safe_signal_time": (
                last_safe["signal_time"].isoformat() if last_safe is not None else None
            ),
            "latest_boundary_safe_target_end_source_row_id": latest_target_end,
            "latest_boundary_safe_target_end_signal_time": (
                latest_target_row["signal_time"].isoformat()
                if latest_target_row is not None
                else None
            ),
            "overlap_check": "PASS" if overlap_ok else "FAIL",
            "physical_context_preserved": True,
        }

    authority = {
        "schema": TARGET_DEPENDENCY_SCHEMA,
        "status": "PASS",
        "classification": "INHERIT + DEFECT REPAIR",
        "historical_authority": OLD_MAX_TEMPORAL_AUTHORITY,
        "historical_functions": [
            "purged_internal_earlystop_split",
            "purge_train_before_validation",
            "expanding_folds",
        ],
        "row_identity_authority": "ORIGINAL_SOURCE_ROW_ID",
        "label_horizon_main_bars": label_horizon,
        "requested_purge_main_bars": requested_purge,
        "applied_boundary_purge_main_bars": effective_purge,
        "minimum_legal_embargo_main_bars": embargo,
        "physical_partition_ownership_preserved": True,
        "physical_context_rows_preserved": True,
        "discovery_to_locked": boundary("discovery", "locked_oos"),
        "locked_to_fresh": boundary("locked_oos", "fresh_forward"),
    }
    result = deepcopy(protected_manifest)
    result["target_dependency_authority"] = authority
    return result


def boundary_safe_partition_rows(
    rows: list[dict[str, Any]],
    protected_manifest: dict[str, Any],
    *,
    partition: str,
) -> list[dict[str, Any]]:
    authority = protected_manifest.get("target_dependency_authority")
    if not isinstance(authority, dict) or authority.get("schema") != TARGET_DEPENDENCY_SCHEMA:
        raise RuntimeError("R01_PROTECTED_TARGET_DEPENDENCY_AUTHORITY_REQUIRED")
    if authority.get("status") != "PASS":
        raise RuntimeError("R01_PROTECTED_TARGET_DEPENDENCY_AUTHORITY_FAIL")
    key = {
        "discovery": "discovery_to_locked",
        "locked_oos": "locked_to_fresh",
    }.get(partition)
    if key is None:
        raise ValueError(f"R01_PROTECTED_TARGET_PARTITION_UNSUPPORTED:{partition}")
    boundary = authority.get(key)
    if not isinstance(boundary, dict) or boundary.get("overlap_check") != "PASS":
        raise RuntimeError("R01_PROTECTED_TARGET_DEPENDENCY_BOUNDARY_FAIL")
    cutoff = int(boundary["eligibility_cutoff_source_row_id_exclusive"])
    return [
        row
        for row in _partition_rows(rows, protected_manifest, partition)
        if int(row["source_row_id"]) < cutoff
    ]


def partition_supervised_eligible(
    row: dict[str, Any],
    protected_manifest: dict[str, Any],
    *,
    partition: str,
) -> bool:
    if row.get("target_valid") is not True or not bool(row.get("supervised_eligible")):
        return False
    authority = protected_manifest.get("target_dependency_authority")
    if not isinstance(authority, dict) or authority.get("schema") != TARGET_DEPENDENCY_SCHEMA:
        raise RuntimeError("R01_PROTECTED_TARGET_DEPENDENCY_AUTHORITY_REQUIRED")
    key = {
        "discovery": "discovery_to_locked",
        "locked_oos": "locked_to_fresh",
    }.get(partition)
    if key is None:
        raise ValueError(f"R01_PROTECTED_TARGET_PARTITION_UNSUPPORTED:{partition}")
    boundary = authority.get(key)
    if not isinstance(boundary, dict) or boundary.get("overlap_check") != "PASS":
        raise RuntimeError("R01_PROTECTED_TARGET_DEPENDENCY_BOUNDARY_FAIL")

    identity = (protected_manifest.get("partition_identity") or {}).get(partition) or {}
    start = _aware_datetime(identity.get("from"), f"{partition}_from")
    end = _aware_datetime(identity.get("to"), f"{partition}_to")
    timestamp = _aware_datetime(row.get("signal_time"), "row_signal_time")
    if not (start <= timestamp < end):
        return False
    cutoff = int(boundary["eligibility_cutoff_source_row_id_exclusive"])
    return int(row["source_row_id"]) < cutoff


def dependency_boundary_is_legal(
    *,
    base_dependency: int,
    purge: int,
    embargo: int,
    sequence_dependency: int = 0,
) -> bool:
    effective = max(int(base_dependency), int(sequence_dependency))
    return int(purge) >= effective and int(embargo) >= effective


FEATURE_ATTACK_FAMILIES = {
    "returns": ("ret1_atr", "ret3_atr", "ret6_atr"),
    "trend_indicators": (
        "fast_ma_gap_atr","slow_ma_gap_atr","fast_ma_slope_atr",
        "adx_scaled","plus_di_scaled","minus_di_scaled",
    ),
    "range_volatility": (
        "rsi_centered","atr_percent","atr_ratio","bollinger_z",
        "bollinger_width_pct","efficiency10",
    ),
    "candle_volume": (
        "candle_body_atr","upper_wick_atr","lower_wick_atr",
        "range_atr","tick_volume_z",
    ),
    "calendar": ("hour_sin","hour_cos","weekday_sin","weekday_cos"),
    "strategy_families": (
        "trend_family","range_family","breakout_family","pullback_family",
        "session_family","shock_family","relative_family",
    ),
    "meta": ("rule_meta_score",),
}


def _reproducible_rows(
    context: dict[str, Any],
) -> tuple[list[tuple[dict[str, Any], tuple[float, ...]]], dict[str, Any], dict[str, Any]]:
    from .research_dataset import reproduce_row_cp32

    main_prepared = {
        role: prepare_role_series(context["main_role_bars"][role])
        for role in ("context", "structure", "main", "timing")
    }
    relative_prepared = {
        role: prepare_role_series(context["relative_role_bars"][role])
        for role in ("context", "structure", "main", "timing")
    }
    candidates: list[tuple[dict[str, Any], tuple[float, ...]]] = []
    for row in context["ea_rows"]:
        try:
            baseline = reproduce_row_cp32(
                row,
                prepared=main_prepared,
                relative_prepared=relative_prepared,
                params=context["strategy_parameters"],
            )
        except (ValueError, RuntimeError):
            continue
        candidates.append((row, baseline))
    if not candidates:
        raise RuntimeError("R01_LEAKAGE_NO_REPRODUCIBLE_ROW")
    return candidates, main_prepared, relative_prepared


def _representative_indices(count: int) -> list[int]:
    if count <= 0:
        return []
    desired = min(5, count)
    if desired == 1:
        return [0]
    indexes = {
        int(round(position * (count - 1) / (desired - 1)))
        for position in range(desired)
    }
    return sorted(indexes)



def _shift_future_prices(
    role_bars: dict[str, list[dict[str, Any]]],
    prepared: dict[str, Any],
    *,
    decision_time: datetime,
    roles: tuple[str, ...],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    mutated = deepcopy(role_bars)
    changed: dict[str, int] = {}
    for role in roles:
        closed_index = latest_fully_closed_index(prepared[role], decision_time)
        count = 0
        for index in range(closed_index + 1, len(mutated[role])):
            shift = 1_000_000.0 + float(index - closed_index)
            for field in ("open", "high", "low", "close"):
                mutated[role][index][field] = float(mutated[role][index][field]) + shift
            mutated[role][index]["tick_volume"] = (
                float(mutated[role][index]["tick_volume"]) + 1_000_000.0
            )
            count += 1
        changed[role] = count
    if not any(changed.values()):
        raise RuntimeError("R01_LEAKAGE_ATTACK_HAS_NO_FUTURE_BARS")
    return mutated, changed


def _actual_future_attack(
    context: dict[str, Any],
    *,
    mutate_main_roles: tuple[str, ...] = (),
    mutate_relative_roles: tuple[str, ...] = (),
) -> dict[str, Any]:
    from .research_dataset import (
        reproduce_row_cp32,
        validate_causal_feature_invariance,
    )

    candidates, main_prepared, relative_prepared = _reproducible_rows(context)
    selected = _representative_indices(len(candidates))
    evidence_rows: list[dict[str, Any]] = []
    max_by_feature = {name: 0.0 for name in FEATURE_NAMES}
    all_passed = True

    for candidate_index in selected:
        row, baseline = candidates[candidate_index]
        main_bars = context["main_role_bars"]
        relative_bars = context["relative_role_bars"]
        changed_main: dict[str, int] = {}
        changed_relative: dict[str, int] = {}
        if mutate_main_roles:
            main_bars, changed_main = _shift_future_prices(
                main_bars,
                main_prepared,
                decision_time=row["decision_time"],
                roles=mutate_main_roles,
            )
        if mutate_relative_roles:
            relative_bars, changed_relative = _shift_future_prices(
                relative_bars,
                relative_prepared,
                decision_time=row["decision_time"],
                roles=mutate_relative_roles,
            )

        mutated_main = {
            role: prepare_role_series(main_bars[role])
            for role in ("context", "structure", "main", "timing")
        }
        mutated_relative = {
            role: prepare_role_series(relative_bars[role])
            for role in ("context", "structure", "main", "timing")
        }
        after = reproduce_row_cp32(
            row,
            prepared=mutated_main,
            relative_prepared=mutated_relative,
            params=context["strategy_parameters"],
        )
        try:
            invariance = validate_causal_feature_invariance(
                baseline,
                after,
                tolerance=1e-12,
            )
            row_passed = True
        except RuntimeError:
            row_passed = False
            deltas = {
                name: abs(float(baseline[index]) - float(after[index]))
                for index, name in enumerate(FEATURE_NAMES)
            }
            invariance = {
                "status": "FAIL",
                "tolerance": 1e-12,
                "max_abs_change": max(deltas.values(), default=0.0),
                "max_abs_change_by_feature": deltas,
            }
        all_passed = all_passed and row_passed
        for name, value in invariance["max_abs_change_by_feature"].items():
            max_by_feature[name] = max(max_by_feature[name], float(value))
        evidence_rows.append({
            "source_row_id": int(row["source_row_id"]),
            "decision_time": row["decision_time"].isoformat(),
            "mutated_main_future_bars": changed_main,
            "mutated_relative_future_bars": changed_relative,
            "status": invariance["status"],
            "max_abs_feature_change": invariance["max_abs_change"],
        })

    family_max = {
        family: max((max_by_feature[name] for name in names), default=0.0)
        for family, names in FEATURE_ATTACK_FAMILIES.items()
    }
    return {
        "passed": all_passed and bool(evidence_rows),
        "representative_strategy": "DETERMINISTIC_CHRONOLOGY_ENDPOINTS_AND_QUANTILES",
        "representative_row_count": len(evidence_rows),
        "rows": evidence_rows,
        "max_abs_change_by_feature": max_by_feature,
        "max_abs_change_by_family": family_max,
        "all_32_features_checked": set(max_by_feature) == set(FEATURE_NAMES),
    }


def _direct_target_contamination_attack(context: dict[str, Any]) -> dict[str, Any]:
    from .research_dataset import validate_feature_input_contract

    row = deepcopy(context["ea_rows"][len(context["ea_rows"]) // 2])
    rejected: dict[str, bool] = {}
    reasons: dict[str, str] = {}
    for field, value in (("label", 2), ("long_r", 1.25), ("short_r", -1.0)):
        attacked = deepcopy(row)
        attacked[field] = value
        try:
            validate_feature_input_contract(attacked)
            rejected[field] = False
            reasons[field] = ""
        except RuntimeError as exc:
            rejected[field] = str(exc).startswith("R01_DIRECT_TARGET_CONTAMINATION:")
            reasons[field] = str(exc)
    return {
        "passed": all(rejected.values()),
        "production_validator": "validate_feature_input_contract",
        "rejected_fields": rejected,
        "reasons": reasons,
    }


def _indirect_target_contamination_attack(
    context: dict[str, Any],
    labeled_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    from .research_dataset import validate_feature_input_contract

    eligible = [row for row in labeled_rows if row.get("target_valid")]
    if not eligible:
        return {"passed": False, "reason": "NO_VALID_TARGET_FOR_ATTACK"}
    target = eligible[len(eligible) // 2]
    source_id = int(target["source_row_id"])
    source = next(
        (row for row in context["ea_rows"] if int(row["source_row_id"]) == source_id),
        None,
    )
    if source is None:
        return {"passed": False, "reason": "SOURCE_ROW_NOT_FOUND"}
    attacked = deepcopy(source)
    attacked["future_target_proxy"] = (
        float(target["long_r"]) - float(target["short_r"])
    )
    try:
        validate_feature_input_contract(attacked)
        rejected = False
        reason = ""
    except RuntimeError as exc:
        rejected = str(exc).startswith("R01_FEATURE_INPUT_CONTRACT_EXTRA_FIELD:")
        reason = str(exc)
    provenance_attack = _information_contract_attack(
        mutation={"target_dependency": True},
        expected_error="R01_TARGET_DERIVED_FEATURE_FORBIDDEN",
    )
    return {
        "passed": rejected and provenance_attack["passed"],
        "production_validators": [
            "validate_feature_input_contract",
            "validate_feature_information_contract",
        ],
        "derived_feature": "future_target_proxy=long_r-short_r",
        "source_row_id": source_id,
        "row_contract_rejected": rejected,
        "row_contract_reason": reason,
        "information_boundary": provenance_attack,
    }


def _fake_adjacency_attack(context: dict[str, Any]) -> dict[str, Any]:
    from .research_dataset import validate_source_row_contiguity

    rows = context["ea_rows"]
    if len(rows) < 3:
        return {"passed": False, "reason": "INSUFFICIENT_ROWS"}
    legal = [deepcopy(rows[0]), deepcopy(rows[1]), deepcopy(rows[2])]
    legal_result = validate_source_row_contiguity(legal, minimum_rows=3)
    discontinuous = [deepcopy(rows[0]), deepcopy(rows[2])]
    rejected = False
    reason = ""
    try:
        validate_source_row_contiguity(discontinuous, minimum_rows=2)
    except RuntimeError as exc:
        rejected = str(exc).startswith("R01_SOURCE_ROW_ID_GAP:")
        reason = str(exc)
    return {
        "passed": legal_result["status"] == "PASS" and rejected,
        "legal_ids": [int(row["source_row_id"]) for row in legal],
        "attacked_ids": [int(row["source_row_id"]) for row in discontinuous],
        "compressed_array_length": len(discontinuous),
        "production_validator": "validate_source_row_contiguity",
        "gap_rejected": rejected,
        "reason": reason,
    }


def _protected_memory_attack(
    research_id: str,
    *,
    stage: str,
    path: Path,
) -> dict[str, Any]:
    from .research_store import protected_memory_attack_probe

    return protected_memory_attack_probe(
        research_id,
        stage=stage,
        path=path,
    )


def _unclosed_bar_selector_attack() -> dict[str, Any]:
    origin = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
    bars = [
        {"open_time": origin + timedelta(hours=index)}
        for index in range(4)
    ]
    decision = origin + timedelta(hours=2)
    series = SimpleNamespace(bars=bars)
    causal = latest_fully_closed_index(series, decision)
    naive = max(
        index
        for index, bar in enumerate(bars)
        if bar["open_time"] <= decision
    )
    return {
        "passed": causal == 1 and naive == 2 and causal != naive,
        "causal_index": causal,
        "naive_open_bar_index": naive,
    }


def _relative_timestamp_attack(context: dict[str, Any]) -> dict[str, Any]:
    from .research_dataset import alignment_audit

    candidates, main_prepared, relative_prepared = _reproducible_rows(context)
    row = candidates[len(candidates) // 2][0]
    attacked_relative_bars = deepcopy(context["relative_role_bars"])
    changed_roles: list[str] = []
    for role in ("context", "structure", "main", "timing"):
        relative_index = latest_fully_closed_index(
            relative_prepared[role],
            row["decision_time"],
        )
        lookback = max(8, int(context["strategy_parameters"]["InpRelativeLookback"]))
        target = relative_index - max(1, lookback // 2)
        if target < 0:
            continue
        attacked_relative_bars[role][target]["open_time"] = (
            attacked_relative_bars[role][target]["open_time"] + timedelta(seconds=1)
        )
        attacked_relative_bars[role][target]["source_open_time"] = (
            attacked_relative_bars[role][target]["source_open_time"] + timedelta(seconds=1)
        )
        changed_roles.append(role)
    attacked_relative = {
        role: prepare_role_series(attacked_relative_bars[role])
        for role in ("context", "structure", "main", "timing")
    }
    report = alignment_audit(
        [row],
        prepared=main_prepared,
        relative_prepared=attacked_relative,
        params=context["strategy_parameters"],
    )
    return {
        "passed": bool(changed_roles)
        and report["relative_symbol_alignment_status"] == "FAIL"
        and int(report["relative_timestamp_mismatches"]) > 0,
        "production_validator": "alignment_audit",
        "source_row_id": int(row["source_row_id"]),
        "changed_roles": changed_roles,
        "relative_timestamp_mismatches": report["relative_timestamp_mismatches"],
        "status": report["relative_symbol_alignment_status"],
    }


def _incomplete_horizon_attack(context: dict[str, Any]) -> dict[str, Any]:
    from .research_dataset import build_labels

    main_bars = context["main_role_bars"]["main"]
    if len(main_bars) < 2:
        return {"passed": False, "reason": "INSUFFICIENT_MAIN_BARS"}
    last = main_bars[-1]
    row = {
        "source_row_id": 0,
        "signal_time": main_bars[-2]["open_time"],
        "decision_time": last["open_time"],
        "atr": max(1e-6, float(last["high"]) - float(last["low"])),
        "decision_bid": float(last["open"]),
        "decision_ask": float(last["open"]) + float(last["spread_points"]) * float(context["point_size"]),
        "spread_points": float(last["spread_points"]),
    }
    labeled, manifest = build_labels(
        [row],
        main_bars=main_bars,
        point_size=float(context["point_size"]),
        parent=context["parent"],
    )
    attacked = labeled[0]
    return {
        "passed": (
            attacked["target_reason"] == "INCOMPLETE_FUTURE_HORIZON"
            and attacked["target_valid"] is False
            and attacked["supervised_eligible"] is False
            and manifest["incomplete_horizon_rows"] == 1
        ),
        "production_function": "build_labels",
        "target_reason": attacked["target_reason"],
        "target_valid": attacked["target_valid"],
        "supervised_eligible": attacked["supervised_eligible"],
    }



def _ambiguous_bar_attack() -> dict[str, Any]:
    from .research_dataset import _future_bar_outcome

    outcome, ambiguous = _future_bar_outcome(
        [{
            "open": 100.0,
            "high": 102.0,
            "low": 98.0,
            "close": 100.0,
            "spread_points": 0.0,
        }],
        time_exit_bar={
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "spread_points": 0.0,
        },
        direction="long",
        entry_bid=100.0,
        entry_ask=100.0,
        stop_distance=1.0,
        take_distance=1.0,
        point_size=0.01,
    )
    return {
        "passed": outcome is None and ambiguous is True,
        "outcome": outcome,
        "ambiguous": ambiguous,
    }


def _protected_target_boundary_attack(
    context: dict[str, Any],
    dependency: dict[str, Any],
    *,
    upstream_partition: str,
) -> dict[str, Any]:
    """Change only the first downstream target bar and exercise production eligibility."""
    from .research_dataset import build_labels

    horizon = int(dependency["label_dependency_main_bars"])
    requested_purge = int(dependency["minimum_legal_purge_main_bars"])
    effective_purge = max(horizon, requested_purge)
    if horizon <= 0 or effective_purge <= 0:
        return {"passed": False, "reason": "TARGET_DEPENDENCY_INVALID"}

    discovery_boundary = effective_purge + horizon + 5
    fresh_boundary = discovery_boundary + effective_purge + horizon + 5
    total_rows = fresh_boundary + horizon + 5
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    main_minutes = int(context["parent"]["strategy_geometry"]["main_minutes"])
    step = timedelta(minutes=main_minutes)
    bars = [
        {
            "open_time": start + step * index,
            "open": 100.0,
            "high": 100.1,
            "low": 99.9,
            "close": 100.0,
            "tick_volume": 1000.0,
            "spread_points": 0.0,
        }
        for index in range(total_rows)
    ]
    physical_rows = [
        {
            "source_row_id": index,
            "signal_time": bars[index]["open_time"],
            "label": 1,
            "long_r": 0.0,
            "short_r": 0.0,
            "target_valid": True,
            "target_reason": "VALID",
            "supervised_eligible": True,
        }
        for index in range(total_rows)
    ]
    manifest = protected_partition_manifest(
        discovery_from=physical_rows[0]["signal_time"].isoformat(),
        discovery_to=physical_rows[discovery_boundary]["signal_time"].isoformat(),
        locked_oos_from=physical_rows[discovery_boundary]["signal_time"].isoformat(),
        locked_oos_to=physical_rows[fresh_boundary]["signal_time"].isoformat(),
        fresh_forward_from=physical_rows[fresh_boundary]["signal_time"].isoformat(),
        fresh_forward_to=physical_rows[-1]["signal_time"].isoformat(),
    )
    bound = bind_protected_partition_rows(physical_rows, manifest)
    bound = bind_protected_target_dependency_authority(
        physical_rows,
        bound,
        dependency,
    )

    if upstream_partition == "discovery":
        downstream_first = discovery_boundary
        boundary_key = "discovery_to_locked"
    elif upstream_partition == "locked_oos":
        downstream_first = fresh_boundary
        boundary_key = "locked_to_fresh"
    else:
        return {"passed": False, "reason": "UNSUPPORTED_PARTITION"}

    touching_id = downstream_first - horizon
    strictly_safe_id = downstream_first - effective_purge - 1
    if strictly_safe_id < 0 or touching_id < 0:
        return {"passed": False, "reason": "SYNTHETIC_BOUNDARY_TOO_SHORT"}

    decision_row = {
        "source_row_id": touching_id,
        "signal_time": physical_rows[touching_id]["signal_time"],
        "decision_time": bars[touching_id]["open_time"],
        "atr": 1.0,
        "decision_bid": 100.0,
        "decision_ask": 100.0,
        "spread_points": 0.0,
    }
    baseline_labeled, _ = build_labels(
        [decision_row],
        main_bars=bars,
        point_size=float(context["point_size"]),
        parent=context["parent"],
    )
    attacked_bars = deepcopy(bars)
    attacked_bars[downstream_first]["open"] = 102.0
    attacked_bars[downstream_first]["high"] = 102.0
    attacked_bars[downstream_first]["low"] = 102.0
    attacked_bars[downstream_first]["close"] = 102.0
    attacked_labeled, _ = build_labels(
        [decision_row],
        main_bars=attacked_bars,
        point_size=float(context["point_size"]),
        parent=context["parent"],
    )
    baseline_target = baseline_labeled[0]
    attacked_target = attacked_labeled[0]

    baseline_rows = deepcopy(physical_rows)
    attacked_rows = deepcopy(physical_rows)
    for target_rows, target in (
        (baseline_rows, baseline_target),
        (attacked_rows, attacked_target),
    ):
        target_rows[touching_id].update({
            "label": target["label"],
            "long_r": target["long_r"],
            "short_r": target["short_r"],
            "target_valid": target["target_valid"],
            "target_reason": target["target_reason"],
            "supervised_eligible": target["supervised_eligible"],
        })

    safe_before = [
        (int(row["source_row_id"]), row["label"])
        for row in boundary_safe_partition_rows(
            baseline_rows,
            bound,
            partition=upstream_partition,
        )
        if partition_supervised_eligible(
            row,
            bound,
            partition=upstream_partition,
        )
    ]
    safe_after = [
        (int(row["source_row_id"]), row["label"])
        for row in boundary_safe_partition_rows(
            attacked_rows,
            bound,
            partition=upstream_partition,
        )
        if partition_supervised_eligible(
            row,
            bound,
            partition=upstream_partition,
        )
    ]
    touching_eligible = partition_supervised_eligible(
        attacked_rows[touching_id],
        bound,
        partition=upstream_partition,
    )
    strictly_safe_eligible = partition_supervised_eligible(
        attacked_rows[strictly_safe_id],
        bound,
        partition=upstream_partition,
    )
    boundary = bound["target_dependency_authority"][boundary_key]
    internal_target_changed = (
        baseline_target["label"] != attacked_target["label"]
        or baseline_target["long_r"] != attacked_target["long_r"]
        or baseline_target["short_r"] != attacked_target["short_r"]
    )
    strict_target_end = strictly_safe_id + horizon
    return {
        "passed": (
            internal_target_changed
            and safe_before == safe_after
            and touching_eligible is False
            and strictly_safe_eligible is True
            and strict_target_end < downstream_first
            and touching_id + horizon == downstream_first
            and boundary["overlap_check"] == "PASS"
            and int(boundary["first_downstream_source_row_id"]) == downstream_first
            and int(boundary["eligibility_cutoff_source_row_id_exclusive"])
            == downstream_first - effective_purge
        ),
        "production_functions": [
            "build_labels",
            "bind_protected_target_dependency_authority",
            "boundary_safe_partition_rows",
            "partition_supervised_eligible",
        ],
        "upstream_partition": upstream_partition,
        "downstream_first_source_row_id": downstream_first,
        "strictly_safe_source_row_id": strictly_safe_id,
        "strictly_safe_target_end_source_row_id": strict_target_end,
        "touching_source_row_id": touching_id,
        "label_horizon_main_bars": horizon,
        "requested_purge_main_bars": requested_purge,
        "applied_boundary_purge_main_bars": effective_purge,
        "baseline_touching_label": baseline_target["label"],
        "attacked_touching_label": attacked_target["label"],
        "internal_target_changed": internal_target_changed,
        "touching_boundary_eligible": touching_eligible,
        "strictly_before_boundary_eligible": strictly_safe_eligible,
        "public_adaptive_projection_invariant": safe_before == safe_after,
        "overlap_check": boundary["overlap_check"],
        "changed_downstream_bar_source_row_id": downstream_first,
        "changed_downstream_bar_only": True,
    }


def run_adversarial_suite(
    dataset: dict[str, Any],
    *,
    protected_manifest: dict[str, Any],
    research_id: str,
    memory_path: Path,
) -> dict[str, Any]:
    chronology = dataset["chronology_report"]
    quality = dataset["data_quality_report"]
    dependency = dataset["dependency_report"]
    feature = dataset["feature_manifest"]
    label = dataset["label_manifest"]
    rows = dataset["rows"]
    context = dataset.get("_adversarial_context")
    if not isinstance(context, dict):
        raise RuntimeError("R01_ADVERSARIAL_CONTEXT_MISSING")

    row_ids = [int(row["source_row_id"]) for row in rows]
    physical_identity_ok = row_ids == list(range(len(rows)))
    ambiguous_policy_ok = all(
        not row["supervised_eligible"] and not row["target_valid"]
        for row in rows
        if row["target_reason"] == "AMBIGUOUS_TP_SL_SAME_BAR"
    )
    tail_policy_ok = all(
        not row["supervised_eligible"] and not row["target_valid"]
        for row in rows
        if row["target_reason"] == "INCOMPLETE_FUTURE_HORIZON"
    )
    protected_locked = (
        protected_manifest.get("locked_oos", {}).get("adaptive_access") is False
        and protected_manifest.get("locked_oos", {}).get("training_access") is False
        and protected_manifest.get("locked_oos", {}).get("scientist_access") is False
    )
    protected_fresh = (
        protected_manifest.get("fresh_forward", {}).get("adaptive_access") is False
        and protected_manifest.get("fresh_forward", {}).get("training_access") is False
        and protected_manifest.get("fresh_forward", {}).get("scientist_access") is False
    )
    discovery_access = protected_manifest.get("discovery", {})
    protected_discovery = (
        discovery_access.get("adaptive_access") is True
        and discovery_access.get("training_access") is True
        and discovery_access.get("training_access_scope") == "DISCOVERY_ONLY"
        and discovery_access.get("training_authorization_required")
        == "VALIDATED_OWNER_AUTHORIZED_R02_FROZEN_BLOCK"
    )
    base = int(dependency["full_base_dependency_main_bars"])
    label_horizon = int(dependency["label_dependency_main_bars"])
    purge = int(dependency["minimum_legal_purge_main_bars"])
    embargo = int(dependency["minimum_legal_embargo_main_bars"])
    target_authority = protected_manifest.get("target_dependency_authority") or {}
    target_authority_ok = (
        target_authority.get("schema") == TARGET_DEPENDENCY_SCHEMA
        and target_authority.get("status") == "PASS"
        and int(target_authority.get("label_horizon_main_bars", -1)) == label_horizon
        and int(target_authority.get("applied_boundary_purge_main_bars", -1))
        == max(label_horizon, purge)
        and (target_authority.get("discovery_to_locked") or {}).get("overlap_check")
        == "PASS"
        and (target_authority.get("locked_to_fresh") or {}).get("overlap_check")
        == "PASS"
    )

    future_all = _actual_future_attack(
        context,
        mutate_main_roles=("context", "structure", "main", "timing"),
        mutate_relative_roles=("context", "structure", "main", "timing"),
    )
    future_higher = _actual_future_attack(
        context,
        mutate_main_roles=("context", "structure", "timing"),
    )
    future_relative = _actual_future_attack(
        context,
        mutate_relative_roles=("context", "structure", "main", "timing"),
    )
    asof_attack = _unclosed_bar_selector_attack()
    relative_mismatch = _relative_timestamp_attack(context)
    ambiguous_attack = _ambiguous_bar_attack()
    incomplete_horizon_attack = _incomplete_horizon_attack(context)
    centered_attack = _information_contract_attack(
        mutation={"window_alignment": "CENTERED"},
        expected_error="R01_CENTERED_FEATURE_WINDOW_FORBIDDEN",
    )
    future_offset_attack = _information_contract_attack(
        mutation={"maximum_future_offset": 1},
        expected_error="R01_FUTURE_FEATURE_OFFSET_FORBIDDEN",
    )
    full_fit_attack = _information_contract_attack(
        mutation={"preprocessing_fit_scope": "FULL_DATASET"},
        expected_error="R01_PREPROCESSING_FIT_SCOPE_FORBIDDEN",
    )
    validation_fit_attack = _information_contract_attack(
        mutation={"preprocessing_fit_scope": "TRAIN_PLUS_VALIDATION"},
        expected_error="R01_PREPROCESSING_FIT_SCOPE_FORBIDDEN",
    )
    protected_fit_attack = _information_contract_attack(
        mutation={"preprocessing_fit_scope": "LOCKED_OOS"},
        expected_error="R01_PREPROCESSING_FIT_SCOPE_FORBIDDEN",
    )
    future_fill_contract_attack = _information_contract_attack(
        mutation={"source_fill": "BACKWARD_FILL_FROM_FUTURE"},
        expected_error="R01_SOURCE_FILL_FORBIDDEN",
    )
    direct_target_attack = _direct_target_contamination_attack(context)
    indirect_target_attack = _indirect_target_contamination_attack(context, rows)
    fake_adjacency_attack = _fake_adjacency_attack(context)
    locked_memory_attack = _protected_memory_attack(
        research_id,
        stage="LOCKED_OOS",
        path=memory_path,
    )
    fresh_memory_attack = _protected_memory_attack(
        research_id,
        stage="FRESH_FORWARD",
        path=memory_path,
    )
    discovery_target_boundary_attack = _protected_target_boundary_attack(
        context,
        dependency,
        upstream_partition="discovery",
    )
    locked_target_boundary_attack = _protected_target_boundary_attack(
        context,
        dependency,
        upstream_partition="locked_oos",
    )

    causal_control = _synthetic_future_perturbation(True)

    legal_boundary = dependency_boundary_is_legal(
        base_dependency=base,
        purge=purge,
        embargo=embargo,
    )
    insufficient_boundary_rejected = not dependency_boundary_is_legal(
        base_dependency=base,
        purge=max(0, base - 1),
        embargo=max(0, base - 1),
    )
    sequence_extension = base + 7
    sequence_boundary = dependency_boundary_is_legal(
        base_dependency=base,
        purge=sequence_extension,
        embargo=sequence_extension,
        sequence_dependency=sequence_extension,
    )
    base_only_rejected_for_sequence = not dependency_boundary_is_legal(
        base_dependency=base,
        purge=base,
        embargo=base,
        sequence_dependency=sequence_extension,
    )

    gates = [
        _gate(
            "1_FUTURE_PERTURBATION_EVERY_FEATURE_FAMILY",
            future_all["passed"]
            and future_all["all_32_features_checked"]
            and causal_control
            and feature["parity"]["status"] == "PASS",
            future_all,
        ),
        _gate(
            "2_FUTURE_HIGHER_TIMEFRAME_BAR_INJECTION",
            future_higher["passed"],
            future_higher,
        ),
        _gate(
            "3_FUTURE_RELATIVE_SYMBOL_BAR_INJECTION",
            future_relative["passed"],
            future_relative,
        ),
        _gate(
            "4_CENTERED_ROLLING_CALCULATION",
            centered_attack["passed"],
            centered_attack,
        ),
        _gate(
            "5_FUTURE_SHIFTED_FEATURE",
            future_offset_attack["passed"],
            future_offset_attack,
        ),
        _gate(
            "6_LOOKAHEAD_NORMALIZATION",
            full_fit_attack["passed"],
            full_fit_attack,
        ),
        _gate(
            "7_VALIDATION_FITTED_PREPROCESSING",
            validation_fit_attack["passed"],
            validation_fit_attack,
        ),
        _gate(
            "8_PROTECTED_DATA_FITTED_PREPROCESSING",
            protected_fit_attack["passed"],
            protected_fit_attack,
        ),
        _gate(
            "9_FUTURE_FILLED_SOURCE_DATA",
            future_fill_contract_attack["passed"]
            and chronology["future_fill"] is False
            and chronology["backward_fill"] is False,
            future_fill_contract_attack,
        ),
        _gate(
            "10_DIRECT_TARGET_CONTAMINATION",
            direct_target_attack["passed"],
            direct_target_attack,
        ),
        _gate(
            "11_INDIRECT_TARGET_DERIVED_FEATURE_CONTAMINATION",
            indirect_target_attack["passed"],
            indirect_target_attack,
        ),
        _gate(
            "12_LABEL_HORIZON_OVERLAP",
            purge >= label_horizon and insufficient_boundary_rejected,
            {
                "label_horizon": label_horizon,
                "purge": purge,
                "insufficient_purge_negative_control_rejected": insufficient_boundary_rejected,
            },
        ),
        _gate(
            "13_TRAIN_VALIDATION_DEPENDENCY_OVERLAP",
            legal_boundary and insufficient_boundary_rejected,
            {"base_dependency": base, "purge": purge, "embargo": embargo},
        ),
        _gate(
            "14_TEMPORAL_SEQUENCE_CROSSES_PURGED_BOUNDARY",
            sequence_boundary
            and base_only_rejected_for_sequence
            and "MAX(R01_BASE_DEPENDENCY_HORIZON" in dependency["future_candidate_rule"],
            {
                "base_dependency": base,
                "synthetic_sequence_dependency": sequence_extension,
                "base_only_rejected": base_only_rejected_for_sequence,
            },
        ),
        _gate(
            "15_COMPRESSED_INDEX_FAKE_ADJACENCY",
            physical_identity_ok and fake_adjacency_attack["passed"],
            {
                **fake_adjacency_attack,
                "physical_source_row_ids_preserved": physical_identity_ok,
            },
        ),
        _gate(
            "16_MTF_ASOF_ALIGNMENT_DEFECT",
            asof_attack["passed"] and quality["mtf_alignment_status"] == "PASS",
            asof_attack,
        ),
        _gate(
            "17_RELATIVE_SYMBOL_TIMESTAMP_MISMATCH",
            relative_mismatch["passed"]
            and quality["relative_symbol_alignment_status"] == "PASS",
            relative_mismatch,
        ),
        _gate(
            "18_INCOMPLETE_TARGET_HORIZON_TAIL",
            tail_policy_ok
            and label["incomplete_horizon_policy"] == "CONTEXT_ONLY_TARGET_INVALID"
            and incomplete_horizon_attack["passed"],
            incomplete_horizon_attack,
        ),
        _gate(
            "19_AMBIGUOUS_TP_SL_SAME_BAR_ORDERING",
            ambiguous_policy_ok
            and ambiguous_attack["passed"]
            and label["ambiguous_same_bar_policy"] == "CONTEXT_ONLY_TARGET_INVALID",
            ambiguous_attack,
        ),
        _gate(
            "20_LOCKED_OOS_FEEDBACK_TO_ADAPTIVE_RESEARCH",
            protected_locked
            and protected_manifest.get("research_memory_adaptive_feedback_from_protected") is False
            and locked_memory_attack["passed"],
            locked_memory_attack,
        ),
        _gate(
            "21_FRESH_FORWARD_FEEDBACK_TO_SAME_RESEARCH",
            protected_fresh
            and protected_manifest.get("research_memory_adaptive_feedback_from_protected") is False
            and fresh_memory_attack["passed"],
            fresh_memory_attack,
        ),
        _gate(
            "22_DISCOVERY_TO_LOCKED_TARGET_DEPENDENCY",
            target_authority_ok
            and discovery_target_boundary_attack["passed"],
            discovery_target_boundary_attack,
        ),
        _gate(
            "23_LOCKED_TO_FRESH_TARGET_DEPENDENCY",
            target_authority_ok
            and locked_target_boundary_attack["passed"],
            locked_target_boundary_attack,
        ),
        _gate(
            "24_DISCOVERY_TRAINING_OWNER_SCOPE",
            protected_discovery,
            {
                "training_access": discovery_access.get("training_access"),
                "training_access_scope": discovery_access.get("training_access_scope"),
                "training_authorization_required": discovery_access.get(
                    "training_authorization_required"
                ),
            },
        ),
    ]

    from .research_dataset import validate_causal_feature_invariance

    deliberate_baseline = tuple(0.0 for _ in FEATURE_NAMES)
    deliberate_perturbed = list(deliberate_baseline)
    deliberate_perturbed[0] = 1.0
    deliberate_reason = ""
    try:
        validate_causal_feature_invariance(
            deliberate_baseline,
            tuple(deliberate_perturbed),
            tolerance=1e-12,
        )
        deliberate_detected = False
    except RuntimeError as exc:
        deliberate_detected = str(exc).startswith(
            "R01_FUTURE_INFORMATION_FEATURE_DEPENDENCY:"
        )
        deliberate_reason = str(exc)
    gates.append({
        "attack": "24_DELIBERATELY_LEAKY_SYNTHETIC_FEATURE",
        "status": "DETECTED" if deliberate_detected else "FAIL",
        "evidence": {
            "synthetic_feature": "ret1_atr_depends_on_future_outcome",
            "production_validator": "validate_causal_feature_invariance",
            "detected": deliberate_detected,
            "reason": deliberate_reason,
        },
    })

    legal_pass = all(item["status"] == "PASS" for item in gates[:-1])
    status = "PASS" if legal_pass and deliberate_detected else "FAIL"
    return {
        "schema": LEAKAGE_SCHEMA,
        "status": status,
        "legal_pipeline": "PASS" if legal_pass else "FAIL",
        "deliberately_leaky_pipeline": "DETECTED_FAIL" if deliberate_detected else "NOT_DETECTED",
        "gate_count": len(gates),
        "gates": gates,
        "model_training_performed": False,
        "onnx_export_performed": False,
    }


def protected_partition_manifest(
    *,
    discovery_from: str,
    discovery_to: str,
    locked_oos_from: str,
    locked_oos_to: str,
    fresh_forward_from: str,
    fresh_forward_to: str,
) -> dict[str, Any]:
    values = {
        "discovery": (discovery_from, discovery_to),
        "locked_oos": (locked_oos_from, locked_oos_to),
        "fresh_forward": (fresh_forward_from, fresh_forward_to),
    }
    parsed: dict[str, tuple[str, str]] = {}
    previous_end: datetime | None = None
    for name in ("discovery", "locked_oos", "fresh_forward"):
        start, end = values[name]
        if not start or not end:
            raise ValueError(f"R01_PROTECTED_PARTITION_INVALID:{name}")
        start_dt = _aware_datetime(start, f"{name}_from")
        end_dt = _aware_datetime(end, f"{name}_to")
        if start_dt >= end_dt:
            raise ValueError(f"R01_PROTECTED_PARTITION_INVALID:{name}")
        if previous_end is not None and start_dt != previous_end:
            raise ValueError("R01_PROTECTED_PARTITION_NOT_CONTIGUOUS")
        parsed[name] = (
            start_dt.isoformat(),
            end_dt.isoformat(),
        )
        previous_end = end_dt
    return {
        "schema": "MAX_RESEARCH_PROTECTED_DATA_R01_V1",
        "authority": "OWNER_EXPLICIT_PARTITION_BOUNDARIES",
        "partition_identity": {
            name: {"from": start, "to": end}
            for name, (start, end) in parsed.items()
        },
        "discovery": {
            "from": parsed["discovery"][0], "to": parsed["discovery"][1],
            "adaptive_access": True,
            "training_access": True,
            "training_access_scope": "DISCOVERY_ONLY",
            "training_authorization_required": (
                "VALIDATED_OWNER_AUTHORIZED_R02_FROZEN_BLOCK"
            ),
            "scientist_access": True,
        },
        "locked_oos": {
            "from": parsed["locked_oos"][0], "to": parsed["locked_oos"][1],
            "adaptive_access": False,
            "training_access": False,
            "scientist_access": False,
        },
        "fresh_forward": {
            "from": parsed["fresh_forward"][0], "to": parsed["fresh_forward"][1],
            "adaptive_access": False,
            "training_access": False,
            "scientist_access": False,
        },
        "research_memory_adaptive_feedback_from_protected": False,
        "coverage_contract": "CONTIGUOUS_DISCOVERY_LOCKED_OOS_FRESH_FORWARD",
        "boundary_semantics": {
            "discovery": "[from,to)",
            "locked_oos": "[from,to)",
            "fresh_forward": "[from,to]",
        },
        "row_timestamp_authority": "signal_time",
        "hidden_default": False,
    }
