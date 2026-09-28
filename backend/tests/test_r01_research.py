from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

import max_backend.research_r01_service as r01
import max_backend.research_r01_store as r01_store
import max_backend.research_service as r00
import max_backend.scientist_context as scientist_context
from max_backend.artifact_control import register_artifact
from max_backend.db import connect, ensure_baseline_registered, initialize_database
from max_backend.research_contract import (
    FEATURE_CONTRACT,
    OWNER_CUMULATIVE_E2E_AUTHORITY,
    stable_hash,
)
from max_backend.research_r01_store import (
    create_r01_run,
    get_r01_run,
    stage_r01_artifacts,
)
from max_backend.research_settings import (
    get_research_sample_configuration,
    set_research_sample_configuration,
)
from max_backend.research_store import (
    create_authorization,
    create_research,
    list_memory_events,
    protected_memory_attack_probe,
    update_gate_state,
)
from max_backend.workflow_store import migrate_current


TEST_R00_RESEARCH_ID = "RSRCH-R01-TEST-CURRENT"
TEST_R00_PARENT_ID = "RPAR-R01-TEST-CURRENT"
TEST_R00_CHAMPION_ID = "STRAT-R01-TEST-CURRENT"


def _parent() -> dict:
    return {
        "research_id": TEST_R00_RESEARCH_ID,
        "research_parent_id": TEST_R00_PARENT_ID,
        "parent_strategy_id": TEST_R00_CHAMPION_ID,
        "parent_authority_sha256": "a" * 64,
        "r00_parent_manifest_sha256": "b" * 64,
        "strategy_contract": "MAX_TRUE_MTF_DYNAMIC_V1",
        "feature_contract": FEATURE_CONTRACT,
        "mtf_resolver_version": "TRUE_MTF_LOG_RATIO_V1",
        "strategy_geometry": {
            "contract": "MAX_TRUE_MTF_DYNAMIC_V1",
            "context_tf": "H12",
            "structure_tf": "H4",
            "main_tf": "H1",
            "timing_tf": "M20",
            "context_minutes": 720,
            "structure_minutes": 240,
            "main_minutes": 60,
            "timing_minutes": 20,
            "minimum_main_tf": "M15",
            "minimum_role_tf": "M5",
            "closed_bar_only": True,
            "decision_cadence": "MAIN_TF",
            "resolver_version": "TRUE_MTF_LOG_RATIO_V1",
            "resolver": {
                "timing_target": "MAIN/3",
                "structure_target": "MAIN*4",
                "context_target": "MAIN*16",
                "distance": "ABS_LOG_RATIO",
                "tie_break": "SMALLER_TIMEFRAME",
            },
        },
        "historical_r00_h1_minimum_trades_per_month": 4,
    }


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict]:
    db = tmp_path / "state" / "max.db"
    initialize_database(db)
    ensure_baseline_registered(db)
    migrate_current(db)
    set_research_sample_configuration(4, path=db)
    parent = _parent()

    r00_auth = create_authorization(
        {
            "authorization_id": "RAUTH-R00-R01-TEST",
            "gate": "R00",
            "action": "START",
            "confirmed": True,
            "expected_parent_strategy_id": parent["parent_strategy_id"],
            "expected_parent_authority_sha256": parent["parent_authority_sha256"],
            "cumulative_strategy_e2e_authority": OWNER_CUMULATIVE_E2E_AUTHORITY,
            "h1_minimum_trades_per_month": 4,
            "payload_sha256": stable_hash({"r00": "accepted"}),
            "authorized_utc": "2026-09-25T00:00:00+00:00",
        },
        path=db,
    )
    create_research(
        {
            "research_id": parent["research_id"],
            "research_parent_id": parent["research_parent_id"],
            "parent_strategy_id": parent["parent_strategy_id"],
            "parent_authority_sha256": parent["parent_authority_sha256"],
            "parent_manifest_path": "artifacts/research/r00/parent.json",
            "parent_manifest_sha256": parent["r00_parent_manifest_sha256"],
            "feature_contract": FEATURE_CONTRACT,
            "current_gate": "R00",
            "gate_state": "STARTING",
            "gate_input_manifest_sha": "c" * 64,
            "gate_output_manifest_sha": None,
            "owner_authorization_id": r00_auth["authorization_id"],
            "authorized_utc": r00_auth["authorized_utc"],
            "hardware_snapshot_path": "artifacts/research/r00/hardware.json",
            "hardware_snapshot_sha256": "d" * 64,
            "label_authority": {},
            "candidate_identity_contract": {},
            "artifact_lineage_contract": {},
            "research_policy": {
                "sample_policy": {
                    "h1_minimum_sample_trade_policy": {
                        "status": "FROZEN_OWNER_AUTHORITY",
                        "value": 4,
                        "unit": "TRADES_PER_H1_MONTH",
                    }
                }
            },
            "unresolved_authority": [],
            "candidate_ids": [],
            "qualification_states": {},
            "system_recommendation": None,
            "owner_selected_ids": [],
            "training_count": 0,
            "onnx_count": 0,
            "research_challenger_count": 0,
            "created_utc": "2026-09-25T00:00:00+00:00",
        },
        path=db,
    )
    update_gate_state(
        parent["research_id"],
        gate_state="PASS_WAITING_OWNER",
        path=db,
    )

    monkeypatch.setattr(r01, "ROOT", tmp_path)
    monkeypatch.setattr(r01, "RESEARCH_ARTIFACT_ROOT", tmp_path / "artifacts" / "research")
    monkeypatch.setattr(
        r01_store,
        "RESEARCH_ARTIFACT_ROOT",
        tmp_path / "artifacts" / "research",
    )
    monkeypatch.setattr(r01, "_normalized_r00_parent", lambda **_kwargs: deepcopy(parent))
    source_bundle = tmp_path / "managed_source_bundle.json"
    source_bundle.write_text('{"managed":true}\n', encoding="utf-8")
    monkeypatch.setattr(
        r01,
        "resolve_prepared_source",
        lambda source_id, **_kwargs: {
            "source_id": str(source_id),
            "bundle_path": str(source_bundle),
            "bundle_sha256": "e" * 64,
            "source_identity_sha256": "f" * 64,
            "broker": "TEST-BROKER",
            "feed": "TEST-FEED",
            "source_timezone": "UTC",
        },
    )
    return db, parent


def _request(tmp_path: Path, parent: dict) -> dict:
    return {
        "research_id": parent["research_id"],
        "expected_parent_strategy_id": parent["parent_strategy_id"],
        "owner_confirmation": "OWNER_EXPLICIT_R01_START",
        "source_id": "RSRC-R01-TEST",
        "discovery_from": "2021-01-01T00:00:00+00:00",
        "discovery_to": "2024-01-01T00:00:00+00:00",
        "locked_oos_from": "2024-01-01T00:00:00+00:00",
        "locked_oos_to": "2026-01-01T00:00:00+00:00",
        "fresh_forward_from": "2026-01-01T00:00:00+00:00",
        "fresh_forward_to": "2026-06-30T00:00:00+00:00",
        "confirmed": True,
    }


def _fake_dataset() -> dict:
    dataset_id = "RDATA-R01-TEST"
    dataset_manifest = {
        "schema": "MAX_RESEARCH_DATASET_R01_V1",
        "dataset_id": dataset_id,
        "research_id": TEST_R00_RESEARCH_ID,
        "source_identity_sha256": "1" * 64,
        "source_data_hashes": {"source_bundle": "2" * 64},
        "sealed_immutable": True,
    }
    dataset_manifest["manifest_sha256"] = stable_hash(dataset_manifest)
    quality = {
        "schema": "MAX_RESEARCH_DATA_QUALITY_R01_V1",
        "status": "PASS",
        "physical_rows": 5,
        "supervised_rows": 5,
        "context_only_rows": 0,
        "target_invalid_rows": 0,
        "ambiguous_rows": 0,
        "incomplete_horizon_rows": 0,
        "duplicate_timestamps": 0,
        "monotonicity": "PASS",
        "invalid_ohlc": 0,
        "non_finite_values": 0,
        "missing_source_data": 0,
        "mtf_alignment_status": "PASS",
        "relative_symbol_alignment_status": "PASS",
        "cp32_completeness": "PASS",
        "dataset_start": "2021-01-01T00:00:00+00:00",
        "dataset_end": "2026-06-30T00:00:00+00:00",
        "sell": 10,
        "skip": 20,
        "buy": 12,
        "sell_ratio": 10 / 42,
        "skip_ratio": 20 / 42,
        "buy_ratio": 12 / 42,
        "feature_hash": "e" * 64,
        "label_hash": "f" * 64,
        "manifest_hash": dataset_manifest["manifest_sha256"],
    }

    def row(
        source_row_id: int,
        signal_time: datetime,
        label: int,
        long_r: float,
        short_r: float,
    ) -> dict:
        return {
            "source_row_id": source_row_id,
            "signal_time": signal_time,
            "label": label,
            "long_r": long_r,
            "short_r": short_r,
            "target_valid": True,
            "target_reason": None,
            "supervised_eligible": True,
        }

    return {
        "dataset_id": dataset_id,
        "rows": [
            row(0, datetime(2021, 1, 1, tzinfo=timezone.utc), 0, -1.0, 1.0),
            row(1, datetime(2023, 12, 31, tzinfo=timezone.utc), 2, 1.0, -1.0),
            row(2, datetime(2024, 1, 1, tzinfo=timezone.utc), 1, 404.125, -404.125),
            row(3, datetime(2026, 1, 1, tzinfo=timezone.utc), 2, 808.25, -808.25),
            row(4, datetime(2026, 6, 30, tzinfo=timezone.utc), 2, 909.5, -909.5),
        ],
        "dataset_manifest": dataset_manifest,
        "feature_manifest": {
            "schema": "MAX_RESEARCH_FEATURE_MANIFEST_R01_V1",
            "feature_contract": FEATURE_CONTRACT,
            "feature_count": 32,
            "status": "PASS",
        },
        "label_manifest": {
            "schema": "MAX_RESEARCH_LABEL_MANIFEST_R01_V1",
            "contract_id": r01.LABEL_CONTRACT_ID,
            "classes": {"0": "SELL", "1": "SKIP", "2": "BUY"},
            "supervised_rows": 5,
            "ambiguous_rows": 0,
            "incomplete_horizon_rows": 0,
            "thresholds": None,
            "ambiguous_same_bar_policy": "CONTEXT_ONLY_TARGET_INVALID",
            "incomplete_horizon_policy": "CONTEXT_ONLY_TARGET_INVALID",
            "status": "PASS",
        },
        "chronology_report": {
            "schema": "MAX_RESEARCH_CHRONOLOGY_R01_V1",
            "status": "PASS",
        },
        "data_quality_report": quality,
        "dependency_report": {
            "schema": "MAX_RESEARCH_DEPENDENCY_R01_V1",
            "label_dependency_main_bars": 1,
            "full_base_dependency_main_bars": 1,
            "minimum_legal_purge_main_bars": 1,
            "minimum_legal_embargo_main_bars": 1,
            "future_candidate_rule": "EXTEND_DEPENDENCY_WHEN_REQUIRED",
        },
        "feature_parity_report": {
            "schema": "MAX_RESEARCH_CP32_PARITY_R01_V1",
            "status": "PASS",
            "feature_count": 32,
            "compared_rows": 5,
            "absolute_tolerance": 1e-9,
            "max_abs_error": 0.0,
        },
    }


def _pass_leakage(*_args, **_kwargs) -> dict:
    return {
        "schema": "MAX_RESEARCH_ADVERSARIAL_LEAKAGE_R01_V1",
        "status": "PASS",
        "legal_pipeline": "PASS",
        "deliberately_leaky_pipeline": "DETECTED_FAIL",
        "gate_count": 24,
        "gates": [],
        "model_training_performed": False,
        "onnx_export_performed": False,
    }


def _install_success_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(r01, "build_dataset", lambda *_args, **_kwargs: deepcopy(_fake_dataset()))
    monkeypatch.setattr(r01, "run_adversarial_suite", _pass_leakage)
    monkeypatch.setattr(
        r01,
        "write_dataset_csv",
        lambda path, _rows: path.write_text("source_row_id\n", encoding="utf-8"),
    )


def _protected_for_fake_dataset(dataset: dict) -> dict:
    manifest = r01.protected_partition_manifest(
        discovery_from="2021-01-01T00:00:00+00:00",
        discovery_to="2024-01-01T00:00:00+00:00",
        locked_oos_from="2024-01-01T00:00:00+00:00",
        locked_oos_to="2026-01-01T00:00:00+00:00",
        fresh_forward_from="2026-01-01T00:00:00+00:00",
        fresh_forward_to="2026-06-30T00:00:00+00:00",
    )
    bound = r01.bind_protected_partition_rows(dataset["rows"], manifest)
    return r01.bind_protected_target_dependency_authority(
        dataset["rows"],
        bound,
        dataset["dependency_report"],
    )


def test_discovery_label_summary_ignores_protected_outcomes_and_tracks_discovery() -> None:
    baseline = _fake_dataset()
    protected = _protected_for_fake_dataset(baseline)
    first = r01._discovery_label_summary(dataset=baseline, protected=protected)

    assert first["scope"] == "DISCOVERY_ONLY"
    assert first["protected_outcome_rows_included"] == 0
    assert first["physical_rows"] == 2
    assert first["boundary_safe_physical_rows"] == 1
    assert first["boundary_excluded_from_supervision_rows"] == 1
    assert first["supervised_rows"] == 1
    assert first["context_only_rows"] == 1
    assert first["sell"] == 1
    assert first["skip"] == 0
    assert first["buy"] == 0
    assert first["sell_ratio"] == pytest.approx(1.0)
    assert first["skip_ratio"] == pytest.approx(0.0)
    assert first["buy_ratio"] == pytest.approx(0.0)
    assert first["last_eligible_discovery_source_row_id"] == 0
    assert first["latest_eligible_discovery_target_end_source_row_id"] == 1
    assert first["locked_oos_first_source_row_id"] == 2
    assert first["target_overlap_check"] == "PASS"
    assert baseline["rows"][1]["label"] == 2
    assert r01.partition_supervised_eligible(
        baseline["rows"][1],
        protected,
        partition="discovery",
    ) is False

    cross_boundary_changed = deepcopy(baseline)
    cross_boundary_changed["rows"][1].update({
        "label": 0,
        "long_r": 7777.0,
        "short_r": -7777.0,
        "target_valid": True,
        "target_reason": "VALID",
        "supervised_eligible": True,
    })
    cross_boundary_changed["dataset_manifest"]["manifest_sha256"] = "5" * 64
    cross_boundary_summary = r01._discovery_label_summary(
        dataset=cross_boundary_changed,
        protected=protected,
    )
    for key in (
        "scope",
        "protected_outcome_rows_included",
        "physical_rows",
        "boundary_safe_physical_rows",
        "boundary_excluded_from_supervision_rows",
        "supervised_rows",
        "context_only_rows",
        "target_invalid_rows",
        "ambiguous_rows",
        "incomplete_horizon_rows",
        "sell",
        "skip",
        "buy",
        "sell_ratio",
        "skip_ratio",
        "buy_ratio",
        "last_eligible_discovery_source_row_id",
        "latest_eligible_discovery_target_end_source_row_id",
        "locked_oos_first_source_row_id",
        "target_overlap_check",
    ):
        assert cross_boundary_summary[key] == first[key]

    locked_changed = deepcopy(baseline)
    locked_changed["rows"][2].update({
        "label": 0,
        "long_r": 4404.125,
        "short_r": -4404.125,
        "target_valid": False,
        "target_reason": "AMBIGUOUS_TP_SL_SAME_BAR",
        "supervised_eligible": False,
    })
    locked_changed["dataset_manifest"]["manifest_sha256"] = "3" * 64
    locked_summary = r01._discovery_label_summary(
        dataset=locked_changed,
        protected=protected,
    )
    for key in (
        "scope",
        "protected_outcome_rows_included",
        "physical_rows",
        "supervised_rows",
        "context_only_rows",
        "target_invalid_rows",
        "ambiguous_rows",
        "incomplete_horizon_rows",
        "sell",
        "skip",
        "buy",
        "sell_ratio",
        "skip_ratio",
        "buy_ratio",
    ):
        assert locked_summary[key] == first[key]
    assert locked_summary["source_dataset_lineage_sha256"] != first[
        "source_dataset_lineage_sha256"
    ]

    fresh_changed = deepcopy(baseline)
    fresh_changed["rows"][3].update({
        "label": 0,
        "long_r": 8808.25,
        "short_r": -8808.25,
        "target_valid": False,
        "target_reason": "INCOMPLETE_FUTURE_HORIZON",
        "supervised_eligible": False,
    })
    fresh_changed["rows"][4].update({
        "label": 1,
        "long_r": 9909.5,
        "short_r": -9909.5,
    })
    fresh_changed["dataset_manifest"]["manifest_sha256"] = "4" * 64
    fresh_summary = r01._discovery_label_summary(
        dataset=fresh_changed,
        protected=protected,
    )
    for key in (
        "scope",
        "protected_outcome_rows_included",
        "physical_rows",
        "supervised_rows",
        "context_only_rows",
        "target_invalid_rows",
        "ambiguous_rows",
        "incomplete_horizon_rows",
        "sell",
        "skip",
        "buy",
        "sell_ratio",
        "skip_ratio",
        "buy_ratio",
    ):
        assert fresh_summary[key] == first[key]
    assert fresh_summary["source_dataset_lineage_sha256"] != first[
        "source_dataset_lineage_sha256"
    ]

    discovery_changed = deepcopy(baseline)
    discovery_changed["rows"][0].update({
        "label": 1,
        "long_r": -0.25,
        "short_r": -0.50,
    })
    changed = r01._discovery_label_summary(
        dataset=discovery_changed,
        protected=protected,
    )
    assert changed["sell"] == 0
    assert changed["skip"] == 1
    assert changed["buy"] == 0
    assert changed["manifest_sha256"] != first["manifest_sha256"]


def test_discovery_boundary_touch_is_excluded_but_strictly_before_is_eligible() -> None:
    dataset = _fake_dataset()
    protected = _protected_for_fake_dataset(dataset)

    strictly_before = dataset["rows"][0]
    touching_boundary = dataset["rows"][1]

    assert strictly_before["source_row_id"] + 1 < 2
    assert touching_boundary["source_row_id"] + 1 == 2
    assert r01.partition_supervised_eligible(
        strictly_before,
        protected,
        partition="discovery",
    ) is True
    assert r01.partition_supervised_eligible(
        touching_boundary,
        protected,
        partition="discovery",
    ) is False

    discovery_ids = [
        row["source_row_id"]
        for row in dataset["rows"]
        if row["signal_time"] < datetime(2024, 1, 1, tzinfo=timezone.utc)
    ]
    assert discovery_ids == [0, 1]
    assert [row["source_row_id"] for row in dataset["rows"]] == [0, 1, 2, 3, 4]


def test_locked_target_touching_fresh_is_not_future_evaluation_eligible() -> None:
    dataset = _fake_dataset()
    protected = _protected_for_fake_dataset(dataset)
    locked_row = dataset["rows"][2]

    assert locked_row["source_row_id"] + 1 == 3
    assert locked_row["label"] == 1
    assert r01.partition_supervised_eligible(
        locked_row,
        protected,
        partition="locked_oos",
    ) is False
    authority = protected["target_dependency_authority"]["locked_to_fresh"]
    assert authority["first_downstream_source_row_id"] == 3
    assert authority["overlap_check"] == "PASS"


def test_discovery_label_summary_fails_closed_on_partition_count_mismatch() -> None:
    dataset = _fake_dataset()
    protected = _protected_for_fake_dataset(dataset)
    protected["row_assignment"]["partitions"]["discovery"]["row_count"] += 1
    with pytest.raises(RuntimeError, match="R01_DISCOVERY_LABEL_SUMMARY_PARTITION_MISMATCH"):
        r01._discovery_label_summary(dataset=dataset, protected=protected)


def test_protected_supervised_rows_cannot_rescue_empty_discovery_supervision() -> None:
    dataset = _fake_dataset()
    protected = _protected_for_fake_dataset(dataset)
    for row in dataset["rows"][:2]:
        row.update({
            "label": None,
            "long_r": None,
            "short_r": None,
            "target_valid": False,
            "target_reason": "AMBIGUOUS_TP_SL_SAME_BAR",
            "supervised_eligible": False,
        })
    assert any(row["supervised_eligible"] for row in dataset["rows"][2:])
    with pytest.raises(RuntimeError, match="R01_DISCOVERY_NO_SUPERVISED_ELIGIBLE_ROWS"):
        r01._discovery_label_summary(dataset=dataset, protected=protected)


@pytest.mark.parametrize(
    "message",
    [
        "R01_SOURCE_ALIGNMENT_FAIL",
        "R01_DISCOVERY_NO_SUPERVISED_ELIGIBLE_ROWS",
        "R01_EA_SL_GEOMETRY_MISMATCH",
        "R01_PROTECTED_PARTITIONS_MUST_COVER_DATASET_CONTIGUOUSLY",
        "R01_ADVERSARIAL_LEAKAGE_FAIL",
    ],
)
def test_r01_scientific_data_defects_classify_as_fail(message: str) -> None:
    assert r01._failure_state(RuntimeError(message)) == "FAIL_WAITING_OWNER"


def test_r01_internal_fault_classifies_as_error() -> None:
    assert (
        r01._failure_state(RuntimeError("R01_INTERNAL_UNEXPECTED_FAULT"))
        == "ERROR_WAITING_OWNER"
    )


def test_timezone_naive_partition_api_input_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    request = _request(tmp_path, parent)
    request["discovery_from"] = "2021-01-01T00:00:00"
    with pytest.raises(RuntimeError, match="PARTITION_TIMEZONE_REQUIRED"):
        r01.start_r01(request, path=db)
    assert get_r01_run(parent["research_id"], path=db) is None


def test_canonical_current_stage_has_no_r00_r01_divergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    before = r00.canonical_research_stage(parent["research_id"], path=db)
    assert before["stage"] == "FOUNDATION"
    assert before["internal_gate"] == "R00"
    assert before["state"] == "PASS_WAITING_OWNER"

    request = _request(tmp_path, parent)
    authorization, _validated = r01._authorization(request, parent=parent, path=db)
    input_manifest = r01._input_manifest(authorization, parent=parent)
    create_r01_run(
        run_id="RRUN-R01-STAGE-TEST",
        research_id=parent["research_id"],
        authorization_id=authorization["authorization_id"],
        input_manifest_sha=input_manifest["manifest_sha256"],
        path=db,
    )
    starting = r00.canonical_research_stage(parent["research_id"], path=db)
    assert starting["stage"] == "DATA_FOUNDATION"
    assert starting["internal_gate"] == "R01"
    assert starting["state"] == "STARTING"
    assert starting["historical_r00_state"] == "PASS_WAITING_OWNER"

    with connect(db) as conn:
        historical = conn.execute(
            "SELECT current_gate,gate_state FROM research_projects WHERE research_id=?",
            (parent["research_id"],),
        ).fetchone()
    assert historical["current_gate"] == "R00"
    assert historical["gate_state"] == "PASS_WAITING_OWNER"


@pytest.mark.parametrize("stage", ["LOCKED_OOS", "FRESH_FORWARD"])
def test_protected_memory_adaptive_attack_is_rejected_without_insertion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    before = len(list_memory_events(parent["research_id"], path=db))
    result = protected_memory_attack_probe(
        parent["research_id"],
        stage=stage,
        path=db,
    )
    after = len(list_memory_events(parent["research_id"], path=db))
    assert result["passed"] is True
    assert result["rejected"] is True
    assert result["no_insertion"] is True
    assert before == after


def test_r01_requires_explicit_owner_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    request = _request(tmp_path, parent)
    request["confirmed"] = False
    with pytest.raises(RuntimeError, match="R01_OWNER_CONFIRMATION_REQUIRED"):
        r01.start_r01(request, path=db)
    assert get_r01_run(parent["research_id"], path=db) is None


def test_r01_success_is_atomic_terminal_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    _install_success_stubs(monkeypatch)
    request = _request(tmp_path, parent)

    first = r01.start_r01(request, path=db)
    assert first["run"]["state"] == "PASS_WAITING_OWNER"
    assert first["run"]["dataset_id"] == "RDATA-R01-TEST"
    assert first["integrity"]["status"] == "VERIFIED"
    assert first["model_training"] == 0
    assert first["onnx"] == 0
    assert first["research_challenger"] == 0
    assert first["champion_mutation"] == "NONE"
    assert first["r02_executable"] is False
    current_stage = r00.canonical_research_stage(parent["research_id"], path=db)
    assert current_stage["stage"] == "DATA_FOUNDATION"
    assert current_stage["internal_gate"] == "R01"
    assert current_stage["state"] == "PASS_WAITING_OWNER"
    assert current_stage["historical_r00_state"] == "PASS_WAITING_OWNER"
    with connect(db) as conn:
        r00_row = conn.execute(
            "SELECT current_gate,gate_state,training_count,onnx_count,"
            "research_challenger_count,champion_mutation "
            "FROM research_projects WHERE research_id=?",
            (parent["research_id"],),
        ).fetchone()
    assert r00_row["current_gate"] == "R00"
    assert r00_row["gate_state"] == "PASS_WAITING_OWNER"
    assert int(r00_row["training_count"]) == 0
    assert int(r00_row["onnx_count"]) == 0
    assert int(r00_row["research_challenger_count"]) == 0
    assert r00_row["champion_mutation"] == "NONE"

    memory = [
        item
        for item in list_memory_events(parent["research_id"], path=db)
        if item["stage"] == "R01"
    ]
    assert len(memory) == 1
    assert memory[0]["adaptive_eligible"] is False
    memory_payload = memory[0]["payload"]
    assert "label_hash" not in memory_payload
    assert "sell" not in memory_payload
    assert "skip" not in memory_payload
    assert "buy" not in memory_payload
    assert "long_r" not in memory_payload
    assert "short_r" not in memory_payload
    assert "discovery_label_summary_sha256" in memory_payload

    owner = r01.r01_preflight(path=db)
    assert owner["class_distribution"]["scope"] == "DISCOVERY_ONLY"
    assert owner["class_distribution"]["sell"] == 1
    assert owner["class_distribution"]["skip"] == 0
    assert owner["class_distribution"]["buy"] == 0

    with connect(db) as conn:
        terminal_events = conn.execute(
            """
            SELECT * FROM research_gate_events
            WHERE research_id=? AND gate='R01' AND from_state='STARTING'
            """,
            (parent["research_id"],),
        ).fetchall()
        artifact_rows = conn.execute(
            """
            SELECT * FROM artifact_registry
            WHERE owner_type='RESEARCH_R01' AND owner_id=?
            """,
            (parent["research_id"],),
        ).fetchall()
    assert len(terminal_events) == 1
    assert terminal_events[0]["to_state"] == "PASS_WAITING_OWNER"
    assert artifact_rows
    assert all(row["status"] == "PASS_WAITING_OWNER" for row in artifact_rows)

    second = r01.start_r01(request, path=db)
    assert second["idempotent"] is True
    assert second["run"]["state"] == "PASS_WAITING_OWNER"
    assert second["integrity"]["status"] == "VERIFIED"
    assert len([
        item
        for item in list_memory_events(parent["research_id"], path=db)
        if item["stage"] == "R01"
    ]) == 1


@pytest.mark.parametrize(
    "fault",
    ["before_terminal_commit", "after_artifact_update"],
)
def test_r01_terminal_fault_injection_rolls_back_then_seals_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    _install_success_stubs(monkeypatch)
    request = _request(tmp_path, parent)
    original = r01.commit_r01_terminal_authority
    calls = {"n": 0}

    def injected(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return original(**kwargs, fault_at=fault)
        return original(**kwargs)

    monkeypatch.setattr(r01, "commit_r01_terminal_authority", injected)
    with pytest.raises(RuntimeError, match="R01_FAULT"):
        r01.start_r01(request, path=db)

    run = get_r01_run(parent["research_id"], path=db)
    assert run is not None
    assert run["state"] == "ERROR_WAITING_OWNER"
    assert run["dataset_id"] is None
    integrity = r01.validate_r01_integrity(path=db)
    assert integrity["status"] == "VERIFIED"


def test_r01_fault_after_artifact_staging_has_no_orphan_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    _install_success_stubs(monkeypatch)
    request = _request(tmp_path, parent)
    original = r01.stage_r01_artifacts
    calls = {"n": 0}

    def injected(research_id, artifact_ids, *, path):
        calls["n"] += 1
        result = original(research_id, artifact_ids, path=path)
        if calls["n"] == 1:
            raise RuntimeError("INJECTED_AFTER_ARTIFACT_STAGING")
        return result

    monkeypatch.setattr(r01, "stage_r01_artifacts", injected)
    with pytest.raises(RuntimeError, match="INJECTED_AFTER_ARTIFACT_STAGING"):
        r01.start_r01(request, path=db)

    run = get_r01_run(parent["research_id"], path=db)
    assert run is not None and run["state"] == "ERROR_WAITING_OWNER"
    integrity = r01.validate_r01_integrity(path=db)
    assert integrity["status"] == "VERIFIED"
    with connect(db) as conn:
        staged = conn.execute(
            """
            SELECT COUNT(*) AS n FROM artifact_registry
            WHERE owner_type='RESEARCH_R01' AND owner_id=? AND status='R01_STAGED'
            """,
            (parent["research_id"],),
        ).fetchone()
    assert int(staged["n"]) == 0


def test_r01_restart_recovers_starting_without_dataset_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    request = _request(tmp_path, parent)
    authorization, _validated = r01._authorization(request, parent=parent, path=db)
    input_manifest = r01._input_manifest(authorization, parent=parent)
    run_id = "RRUN-R01-RECOVERY"
    create_r01_run(
        run_id=run_id,
        research_id=parent["research_id"],
        authorization_id=authorization["authorization_id"],
        input_manifest_sha=input_manifest["manifest_sha256"],
        path=db,
    )
    stage_file = (
        tmp_path
        / "artifacts"
        / "research"
        / parent["research_id"]
        / "r01"
        / run_id
        / "partial.json"
    )
    stage_file.parent.mkdir(parents=True, exist_ok=True)
    stage_file.write_text("{}\n", encoding="utf-8")
    artifact = register_artifact(
        artifact_type="DATA_QUALITY_REPORT",
        producer="RESEARCH_R01",
        owner_type="RESEARCH_R01",
        owner_id=parent["research_id"],
        source_type="RESEARCH_R01_RUN",
        source_id=run_id,
        canonical_path=stage_file,
        status="R01_STAGED",
        in_use=True,
        retention_class="ACTIVE_AUTHORITY",
        deletable=False,
        cleanable=False,
        dependencies=[],
        path=db,
    )
    stage_r01_artifacts(
        parent["research_id"],
        [artifact["artifact_id"]],
        path=db,
    )

    monkeypatch.setattr(
        r01,
        "build_dataset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dataset rerun forbidden during restart recovery")
        ),
    )
    preflight = r01.r01_preflight(path=db)
    assert preflight["existing_run"]["state"] == "ERROR_WAITING_OWNER"
    assert preflight["existing_run"]["dataset_id"] is None
    assert r01.validate_r01_integrity(path=db)["status"] == "VERIFIED"


def test_scientist_reads_only_committed_r01_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    _install_success_stubs(monkeypatch)
    request = _request(tmp_path, parent)
    result = r01.start_r01(request, path=db)
    assert result["run"]["state"] == "PASS_WAITING_OWNER"

    research_root = tmp_path / "artifacts" / "research"
    monkeypatch.setattr(scientist_context, "RESEARCH_ARTIFACT_ROOT", research_root)

    before = scientist_context.domain_authority_fingerprint(path=db)
    context = scientist_context.build_scientist_context(
        "Explain the current Research Data Foundation evidence.",
        scope="RESEARCH",
        path=db,
    )
    after = scientist_context.domain_authority_fingerprint(path=db)

    assert before["sha256"] == after["sha256"]
    ref = f"db:research:{parent['research_id']}"
    assert ref in context["evidence"]
    facts = context["evidence"][ref]["facts"]
    assert facts["current_stage"]["stage"] == "DATA_FOUNDATION"
    assert facts["current_stage"]["internal_gate"] == "R01"
    assert facts["current_stage"]["state"] == "PASS_WAITING_OWNER"
    assert facts["historical_r00"]["internal_gate"] == "R00"
    assert facts["historical_r00"]["state"] == "PASS_WAITING_OWNER"
    assert facts["training_count"] == 0
    assert facts["onnx_count"] == 0
    assert facts["research_challenger_count"] == 0
    assert facts["champion_mutation"] == "NONE"
    assert facts["r01"]["state"] == "PASS_WAITING_OWNER"
    assert "dataset_id" not in facts["r01"]
    assert facts["r01"]["evidence"]["data_quality"]["status"] == "PASS"
    data_quality = facts["r01"]["evidence"]["data_quality"]
    for protected_key in (
        "supervised_rows",
        "context_only_rows",
        "ambiguous_rows",
        "sell",
        "skip",
        "buy",
        "sell_ratio",
        "skip_ratio",
        "buy_ratio",
    ):
        assert protected_key not in data_quality
    label_evidence = facts["r01"]["evidence"]["label"]
    assert "supervised_rows" not in label_evidence
    assert "ambiguous_rows" not in label_evidence
    assert "incomplete_horizon_rows" not in label_evidence
    discovery = facts["r01"]["evidence"]["discovery_label_summary"]
    assert discovery["scope"] == "DISCOVERY_ONLY"
    assert discovery["protected_outcome_rows_included"] == 0
    assert discovery["sell"] == 1
    assert discovery["skip"] == 0
    assert discovery["buy"] == 0
    assert discovery["boundary_safe_physical_rows"] == 1
    assert discovery["boundary_excluded_from_supervision_rows"] == 1
    assert discovery["effective_boundary_purge_main_bars"] == 1
    assert discovery["last_eligible_discovery_source_row_id"] == 0
    assert discovery["latest_eligible_discovery_target_end_source_row_id"] == 1
    assert discovery["locked_oos_first_source_row_id"] == 2
    assert discovery["target_overlap_check"] == "PASS"
    assert facts["r01"]["evidence"]["leakage"]["legal_pipeline"] == "PASS"
    assert facts["r01"]["evidence"]["leakage"]["deliberately_leaky_pipeline"] == "DETECTED_FAIL"
    assert facts["r01"]["evidence"]["protected_data"]["locked_oos_access"]["scientist_access"] is False
    encoded = json.dumps(context, sort_keys=True)
    assert "dataset.csv" not in encoded
    assert "source_bundle_path" not in encoded
    r01_encoded = json.dumps(facts["r01"], sort_keys=True)
    assert '"source_row_id":' not in r01_encoded
    assert "long_r" not in r01_encoded
    assert "short_r" not in r01_encoded
    assert "404.125" not in r01_encoded
    assert "808.25" not in r01_encoded
    assert "909.5" not in r01_encoded
    assert "source_dataset_lineage_sha256" not in r01_encoded
    assert "target_dependency_authority" not in r01_encoded
    assert "discovery_to_locked" not in r01_encoded
    assert "locked_to_fresh" not in r01_encoded
    assert "manifest_sha256" not in facts["r01"]["evidence"]["discovery_label_summary"]


def test_r02_remains_blocked_after_r01_source_implementation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _parent = _setup(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="R02_BLOCKED_OWNER_AUTHORIZATION_REQUIRED"):
        r00.attempt_gate_transition(
            TEST_R00_RESEARCH_ID,
            "R02",
            authority="OWNER",
            path=db,
        )


def test_r01_running_execution_keeps_sample_snapshot_when_current_config_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    request = _request(tmp_path, parent)
    authorization, _validated = r01._authorization(
        request,
        parent=parent,
        path=db,
    )
    input_manifest = r01._input_manifest(authorization, parent=parent)
    assert authorization["payload"][
        "research_h1_minimum_trades_per_month"
    ] == 4
    assert input_manifest["research_h1_minimum_trades_per_month"] == 4

    run = create_r01_run(
        run_id="RRUN-R01-SAMPLE-SNAPSHOT",
        research_id=parent["research_id"],
        authorization_id=authorization["authorization_id"],
        input_manifest_sha=input_manifest["manifest_sha256"],
        path=db,
    )
    assert run["state"] == "STARTING"

    set_research_sample_configuration(9, path=db)
    assert get_research_sample_configuration(path=db)[
        "h1_minimum_trades_per_month"
    ] == 9

    frozen = r01_store.get_r01_authorization(
        authorization["authorization_id"],
        path=db,
    )
    assert frozen is not None
    assert frozen["payload"][
        "research_h1_minimum_trades_per_month"
    ] == 4
    rebuilt = r01._input_manifest(frozen, parent=parent)
    assert rebuilt["research_h1_minimum_trades_per_month"] == 4
    assert rebuilt["manifest_sha256"] == input_manifest["manifest_sha256"]


def test_next_r01_execution_snapshots_new_current_sample_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, parent = _setup(tmp_path, monkeypatch)
    set_research_sample_configuration(9, path=db)

    authorization, _validated = r01._authorization(
        _request(tmp_path, parent),
        parent=parent,
        path=db,
    )
    input_manifest = r01._input_manifest(authorization, parent=parent)

    assert authorization["payload"][
        "research_h1_minimum_trades_per_month"
    ] == 9
    assert input_manifest["research_h1_minimum_trades_per_month"] == 9


def test_research_sample_path_has_no_silent_numeric_fallback_or_ui_literal() -> None:
    root = Path(r01.__file__).resolve().parents[2]
    backend_text = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in (
            "backend/max_backend/research_service.py",
            "backend/max_backend/research_r01_service.py",
            "backend/max_backend/research_settings.py",
        )
    )
    frontend_text = (
        root / "frontend/src/ResearchPage.tsx"
    ).read_text(encoding="utf-8")

    assert "ACCEPTED_R00_H1_MINIMUM" not in backend_text
    assert "4 trades/month" not in frontend_text
    assert "8 trades/month" not in frontend_text
    assert "get(\"h1_minimum_trades_per_month\", 4)" not in backend_text
    assert "get(\"h1_minimum_trades_per_month\", 8)" not in backend_text
