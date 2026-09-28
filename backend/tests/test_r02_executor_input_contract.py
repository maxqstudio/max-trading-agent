from __future__ import annotations

import csv
import json
from copy import deepcopy
from pathlib import Path

import pytest

from max_backend.artifact_control import register_artifact
from max_backend.db import initialize_database
from max_backend.research_contract import FEATURE_CONTRACT, stable_hash
from max_backend.research_cp32 import FEATURE_NAMES
from max_backend.research_dataset import sha256_file
from max_backend import research_r02_executor_input as executor


RESEARCH_ID = "RSRCH-R02-EXECUTOR-INPUT"
RUN_ID = "RRUN-R01-R02-EXECUTOR-INPUT"
DATASET_ID = "RDATA-R02-EXECUTOR-INPUT"
BLOCK_ID = "RDISC-R02-EXECUTOR-INPUT"
LABEL_CONTRACT = "MAX_PARENT_FIRST_BARRIER_3CLASS_R01_V1"


def _seal(body: dict) -> dict:
    result = deepcopy(body)
    result["manifest_sha256"] = stable_hash(result)
    return result


def _dataset_manifest() -> dict:
    return _seal(
        {
            "schema": "MAX_RESEARCH_DATASET_R01_V1",
            "research_id": RESEARCH_ID,
            "dataset_id": DATASET_ID,
            "feature_contract": FEATURE_CONTRACT,
            "feature_ordering": list(FEATURE_NAMES),
            "label_contract": LABEL_CONTRACT,
            "sealed_immutable": True,
        }
    )


def _feature_manifest() -> dict:
    return {
        "schema": "MAX_RESEARCH_FEATURE_MANIFEST_R01_V1",
        "feature_contract": FEATURE_CONTRACT,
        "feature_count": len(FEATURE_NAMES),
        "feature_order": list(FEATURE_NAMES),
        "finite_value_required": True,
        "feature_information_boundary": (
            "CAUSAL_MARKET_HISTORY_AT_OR_BEFORE_DECISION_ONLY"
        ),
    }


def _protected_manifest() -> dict:
    return {
        "schema": "MAX_RESEARCH_PROTECTED_DATA_R01_V1",
        "partition_identity": {
            "discovery": {
                "from": "2021-01-01T00:00:00+00:00",
                "to": "2024-01-01T00:00:00+00:00",
            },
            "locked_oos": {
                "from": "2024-01-01T00:00:00+00:00",
                "to": "2026-01-01T00:00:00+00:00",
            },
            "fresh_forward": {
                "from": "2026-01-01T00:00:00+00:00",
                "to": "2026-06-30T00:00:00+00:00",
            },
        },
        "row_assignment": {
            "partitions": {
                "discovery": {"row_count": 2},
                "locked_oos": {"row_count": 1},
                "fresh_forward": {"row_count": 1},
            },
            "total_rows": 4,
            "assigned_rows": 4,
            "overlap_count": 0,
            "unassigned_count": 0,
        },
        "target_dependency_authority": {
            "schema": "MAX_RESEARCH_TARGET_DEPENDENCY_R01_V1",
            "status": "PASS",
            "discovery_to_locked": {
                "overlap_check": "PASS",
                "eligibility_cutoff_source_row_id_exclusive": 2,
            },
            "locked_to_fresh": {
                "overlap_check": "PASS",
                "eligibility_cutoff_source_row_id_exclusive": 3,
            },
        },
    }


def _write_dataset(path: Path, *, bad_bool: bool = False) -> None:
    fields = (
        "source_row_id",
        "signal_time",
        "decision_time",
        "symbol",
        "period",
        *FEATURE_NAMES,
        "label",
        "long_r",
        "short_r",
        "target_valid",
        "target_reason",
        "supervised_eligible",
    )
    safe_features = {name: str(index + 0.25) for index, name in enumerate(FEATURE_NAMES)}

    rows = []
    for row_id, signal, label in (
        (0, "2021-01-01T00:00:00+00:00", "0"),
        (1, "2022-01-01T00:00:00+00:00", "2"),
    ):
        rows.append(
            {
                "source_row_id": str(row_id),
                "signal_time": signal,
                "decision_time": signal.replace("00:00:00", "01:00:00"),
                "symbol": "XAUUSD",
                "period": "16385",
                **safe_features,
                "label": label,
                "long_r": "1.25",
                "short_r": "-1.25",
                "target_valid": "maybe" if (bad_bool and row_id == 0) else "True",
                "target_reason": "",
                "supervised_eligible": "True",
            }
        )

    # Protected rows deliberately contain values that would fail feature/label
    # parsing. A legal R02 input loader must never parse them as training data.
    for row_id, signal in (
        (2, "2024-01-01T00:00:00+00:00"),
        (3, "2026-01-01T00:00:00+00:00"),
    ):
        rows.append(
            {
                "source_row_id": str(row_id),
                "signal_time": signal,
                "decision_time": signal.replace("00:00:00", "01:00:00"),
                "symbol": "XAUUSD",
                "period": "16385",
                **{name: "PROTECTED_NOT_FOR_TRAINING" for name in FEATURE_NAMES},
                "label": "PROTECTED_LABEL",
                "long_r": "PROTECTED_LONG_R",
                "short_r": "PROTECTED_SHORT_R",
                "target_valid": "PROTECTED_BOOL",
                "target_reason": "PROTECTED",
                "supervised_eligible": "PROTECTED_BOOL",
            }
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _artifact_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict, dict]:
    db = tmp_path / "state" / "max.db"
    initialize_database(db)

    root = tmp_path / "artifacts" / "research"
    monkeypatch.setattr(executor, "RESEARCH_ARTIFACT_ROOT", root)
    run_root = root / RESEARCH_ID / "r01" / RUN_ID / DATASET_ID
    run_root.mkdir(parents=True)

    dataset_path = run_root / "dataset.csv"
    dataset_manifest_path = run_root / "dataset_manifest.json"
    feature_path = run_root / "feature_manifest.json"
    protected_path = run_root / "protected_data_manifest.json"
    output_path = run_root / "r01_output_manifest.json"

    _write_dataset(dataset_path)
    dataset_manifest = _dataset_manifest()
    feature_manifest = _feature_manifest()
    protected_manifest = _protected_manifest()
    dataset_manifest_path.write_text(json.dumps(dataset_manifest, sort_keys=True), encoding="utf-8")
    feature_path.write_text(json.dumps(feature_manifest, sort_keys=True), encoding="utf-8")
    protected_path.write_text(json.dumps(protected_manifest, sort_keys=True), encoding="utf-8")

    output = _seal(
        {
            "schema": "MAX_RESEARCH_OUTPUT_R01_V1",
            "research_id": RESEARCH_ID,
            "gate": "R01",
            "gate_state": "PASS_WAITING_OWNER",
            "dataset_id": DATASET_ID,
            "file_hashes": {
                "dataset": sha256_file(dataset_path),
                "dataset_manifest": sha256_file(dataset_manifest_path),
                "feature_manifest": sha256_file(feature_path),
                "protected_data_manifest": sha256_file(protected_path),
            },
        }
    )
    output_path.write_text(json.dumps(output, sort_keys=True), encoding="utf-8")

    artifacts = []
    for artifact_type, file_path in (
        ("RESEARCH_DATASET", dataset_path),
        ("RESEARCH_DATASET_MANIFEST", dataset_manifest_path),
        ("FEATURE_MANIFEST", feature_path),
        ("PROTECTED_DATA_MANIFEST", protected_path),
        ("RESEARCH_AUTHORITY_MANIFEST", output_path),
    ):
        artifacts.append(
            register_artifact(
                artifact_type=artifact_type,
                producer="RESEARCH_R01",
                owner_type="RESEARCH_R01",
                owner_id=RESEARCH_ID,
                source_type="RESEARCH_R01_RUN",
                source_id=RUN_ID,
                canonical_path=file_path,
                status="PASS_WAITING_OWNER",
                in_use=True,
                retention_class="ACTIVE_AUTHORITY",
                path=db,
            )
        )

    run = {
        "research_id": RESEARCH_ID,
        "run_id": RUN_ID,
        "state": "PASS_WAITING_OWNER",
        "dataset_id": DATASET_ID,
        "output_manifest_sha": output["manifest_sha256"],
        "artifact_ids": sorted(item["artifact_id"] for item in artifacts),
    }
    block = {
        "block_id": BLOCK_ID,
        "research_id": RESEARCH_ID,
        "state": "FROZEN_WAITING_EXECUTION",
        "r01_output_manifest_sha256": output["manifest_sha256"],
        "candidate_count": 3,
        "compute_budget": {
            "value": 120,
            "unit": "FIT_SECONDS",
            "execution_semantics": "FROZEN_ONLY_NOT_EXECUTED",
        },
    }
    return db, run, block


def test_adapter_contract_is_deterministic_single_thread_and_nonqualifying() -> None:
    contract = executor.executor_adapter_contract()
    assert set(contract["families"]) == {"lightgbm", "xgboost", "random_forest"}
    assert contract["seed_required"] is True
    assert contract["max_threads_per_fit"] == 1
    assert contract["cheap_screen_qualification_authority"] is False
    assert contract["qualified_pool_admission_authority"] == "R03_FULL_WFA_ONLY"
    assert contract["onnx_authorized"] is False


def test_fit_budget_is_monotonic_and_rejects_unsupported_or_excess_usage() -> None:
    budget = {"value": 120, "unit": "FIT_SECONDS"}
    first = executor.remaining_fit_budget(budget, [10, 15.5])
    second = executor.remaining_fit_budget(budget, [10, 15.5, 20])
    assert first == {"value": 94.5, "unit": "FIT_SECONDS", "exhausted": False}
    assert second["value"] == 74.5
    assert second["value"] < first["value"]

    with pytest.raises(ValueError, match="R02_EXECUTOR_BUDGET_UNIT_UNSUPPORTED"):
        executor.remaining_fit_budget({"value": 1, "unit": "GPU_SECONDS"}, [])
    with pytest.raises(ValueError, match="R02_EXECUTOR_BUDGET_CONSUMED_INVALID"):
        executor.remaining_fit_budget(budget, [-1])
    with pytest.raises(ValueError, match="R02_EXECUTOR_BUDGET_EXCEEDED"):
        executor.remaining_fit_budget(budget, [121])


def test_resolver_accepts_only_bound_r01_terminal_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, run, block = _artifact_fixture(tmp_path, monkeypatch)
    resolved = executor.resolve_r01_executor_artifacts(run, block, path=db)
    assert set(resolved) == {
        "dataset",
        "dataset_manifest",
        "feature_manifest",
        "protected_data_manifest",
        "output_manifest",
    }
    assert all(item["status"] == "PASS_WAITING_OWNER" for item in resolved.values())


def test_resolver_rejects_path_escape_even_when_registry_row_is_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, run, block = _artifact_fixture(tmp_path, monkeypatch)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    escaped = register_artifact(
        artifact_type="FEATURE_MANIFEST",
        producer="RESEARCH_R01",
        owner_type="RESEARCH_R01",
        owner_id=RESEARCH_ID,
        source_type="RESEARCH_R01_RUN",
        source_id=RUN_ID,
        canonical_path=outside,
        status="PASS_WAITING_OWNER",
        in_use=True,
        retention_class="ACTIVE_AUTHORITY",
        path=db,
    )
    run["artifact_ids"].append(escaped["artifact_id"])
    with pytest.raises(RuntimeError, match="R02_EXECUTOR_ARTIFACT_PATH_OUTSIDE_AUTHORITY"):
        executor.resolve_r01_executor_artifacts(run, block, path=db)


def test_discovery_input_never_parses_locked_or_fresh_features_or_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, run, block = _artifact_fixture(tmp_path, monkeypatch)
    prepared = executor.build_discovery_executor_input(run, block, path=db)

    assert prepared["research_id"] == RESEARCH_ID
    assert prepared["block_id"] == BLOCK_ID
    assert prepared["dataset_id"] == DATASET_ID
    assert prepared["feature_names"] == list(FEATURE_NAMES)
    assert prepared["row_ids"] == [0, 1]
    assert prepared["labels"] == [0, 2]
    assert len(prepared["features"]) == 2
    assert all(len(row) == len(FEATURE_NAMES) for row in prepared["features"])
    assert prepared["protected_rows_exposed"] == 0
    assert prepared["locked_oos_rows_exposed"] == 0
    assert prepared["fresh_forward_rows_exposed"] == 0
    assert prepared["cheap_screen_qualification_authority"] is False


def test_discovery_input_strict_boolean_parser_rejects_ambiguous_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, run, block = _artifact_fixture(tmp_path, monkeypatch)
    resolved = executor.resolve_r01_executor_artifacts(run, block, path=db)
    dataset = Path(resolved["dataset"]["canonical_path"])
    _write_dataset(dataset, bad_bool=True)
    # Keep registry/output hashes aligned so this test reaches the parser.
    with pytest.raises(RuntimeError, match="R02_EXECUTOR_ARTIFACT_HASH_MISMATCH"):
        executor.build_discovery_executor_input(run, block, path=db)

    # Re-registering the changed dataset proves ambiguous booleans are rejected
    # independently of the outer artifact hash guard.
    changed = register_artifact(
        artifact_type="RESEARCH_DATASET",
        producer="RESEARCH_R01",
        owner_type="RESEARCH_R01",
        owner_id=RESEARCH_ID,
        source_type="RESEARCH_R01_RUN",
        source_id=RUN_ID,
        canonical_path=dataset,
        status="PASS_WAITING_OWNER",
        in_use=True,
        retention_class="ACTIVE_AUTHORITY",
        path=db,
    )
    assert changed["artifact_id"] in run["artifact_ids"]
    output_path = Path(resolved["output_manifest"]["canonical_path"])
    output = json.loads(output_path.read_text(encoding="utf-8"))
    output["file_hashes"]["dataset"] = sha256_file(dataset)
    output.pop("manifest_sha256")
    output["manifest_sha256"] = stable_hash(output)
    output_path.write_text(json.dumps(output, sort_keys=True), encoding="utf-8")
    register_artifact(
        artifact_type="RESEARCH_AUTHORITY_MANIFEST",
        producer="RESEARCH_R01",
        owner_type="RESEARCH_R01",
        owner_id=RESEARCH_ID,
        source_type="RESEARCH_R01_RUN",
        source_id=RUN_ID,
        canonical_path=output_path,
        status="PASS_WAITING_OWNER",
        in_use=True,
        retention_class="ACTIVE_AUTHORITY",
        path=db,
    )
    run["output_manifest_sha"] = output["manifest_sha256"]
    block["r01_output_manifest_sha256"] = output["manifest_sha256"]

    with pytest.raises(ValueError, match="R02_EXECUTOR_BOOLEAN_INVALID"):
        executor.build_discovery_executor_input(run, block, path=db)


def test_tampered_feature_manifest_fails_before_training_input_is_returned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, run, block = _artifact_fixture(tmp_path, monkeypatch)
    resolved = executor.resolve_r01_executor_artifacts(run, block, path=db)
    feature_path = Path(resolved["feature_manifest"]["canonical_path"])
    payload = json.loads(feature_path.read_text(encoding="utf-8"))
    payload["feature_order"] = list(reversed(payload["feature_order"]))
    feature_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    with pytest.raises(RuntimeError, match="R02_EXECUTOR_ARTIFACT_HASH_MISMATCH"):
        executor.build_discovery_executor_input(run, block, path=db)
