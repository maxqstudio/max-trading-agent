from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pytest

from max_backend.research_cp32 import FEATURE_NAMES
from max_backend.research_r02_contract import build_discovery_plan
from max_backend.research_contract import (
    candidate_id as derive_candidate_id,
    stable_hash,
)
import max_backend.research_r02_executor as executor
import max_backend.research_r02_models as models
from max_backend.research_r02_executor import (
    build_discovery_split,
    evaluate_cheap_screen,
)
from max_backend.research_r02_models import fit_predict_candidate
from max_backend.research_contract import FEATURE_CONTRACT


def _rows(count: int) -> list[dict]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = []
    for source_row_id in range(count):
        label = source_row_id % 3
        result.append({
            "source_row_id": source_row_id,
            "signal_time": start + timedelta(hours=source_row_id),
            **{
                feature: float((source_row_id * (index + 1) + label) % 113)
                for index, feature in enumerate(FEATURE_NAMES)
            },
            "label": label,
        })
    return result


def _candidate_specs() -> dict[str, dict]:
    parent_lineage = {
        "research_parent_id": "RPAR-R02-SYNTHETIC",
        "parent_strategy_id": "STRAT-R02-SYNTHETIC",
        "dataset_id": "RDATA-R02-SYNTHETIC",
        "r01_output_manifest_sha256": "a" * 64,
    }
    models = (
        ("lightgbm", 11, {
            "n_estimators": 8,
            "max_depth": 3,
            "num_leaves": 7,
            "learning_rate": 0.1,
        }),
        ("xgboost", 42, {
            "n_estimators": 8,
            "max_depth": 3,
            "learning_rate": 0.1,
            "subsample": 1.0,
            "colsample_bytree": 1.0,
        }),
        ("random_forest", 7, {
            "n_estimators": 8,
            "max_depth": 3,
            "min_samples_leaf": 1,
        }),
    )
    candidates = [{
        "research_id": "RSRCH-R02-SYNTHETIC",
        "model_family": family,
        "topology_spec": topology,
        "feature_contract": FEATURE_CONTRACT,
        "label_contract": "MAX_PARENT_FIRST_BARRIER_3CLASS_R01_V1",
        "seed": seed,
        "preprocessing": {"scaling": "NONE"},
        "training_configuration": {
            "objective": "MULTICLASS",
            "class_weighting": "BALANCED",
            "accelerator": "CPU",
            "device_id": None,
            "platform_id": None,
        },
        "parent_lineage": dict(parent_lineage),
    } for family, seed, topology in models]
    plan = build_discovery_plan({
        "research_id": "RSRCH-R02-SYNTHETIC",
        "r01_output_manifest_sha256": "a" * 64,
        "feature_contract": FEATURE_CONTRACT,
        "label_contract": "MAX_PARENT_FIRST_BARRIER_3CLASS_R01_V1",
        "parent_lineage": parent_lineage,
        "candidate_count": len(candidates),
        "compute_budget": {"value": 120, "unit": "FIT_SECONDS"},
        "candidates": candidates,
    })
    return {item["model_family"]: item for item in plan["candidates"]}


def _arrays() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = _rows(96)
    features = np.asarray(
        [[row[name] for name in FEATURE_NAMES] for row in rows],
        dtype=np.float64,
    )
    labels = np.asarray([row["label"] for row in rows], dtype=np.int64)
    return features[:72], labels[:72], features[72:]


def _execution_authority_fixture() -> tuple[dict, dict]:
    specs = list(_candidate_specs().values())
    first_spec = specs[0]
    research_id = first_spec["research_id"]
    lineage = deepcopy(first_spec["parent_lineage"])
    output_sha = lineage["r01_output_manifest_sha256"]
    block_id = "RDISC-R02-SYNTHETIC"
    candidates = []
    for ordinal, item in enumerate(specs):
        spec = deepcopy(item)
        candidate_id = spec.pop("candidate_id")
        candidates.append({
            "candidate_id": candidate_id,
            "block_id": block_id,
            "ordinal": ordinal,
            "model_family": spec["model_family"],
            "seed": spec["seed"],
            "spec_sha256": stable_hash(spec),
            "spec": spec,
        })
    candidate_ids = [item["candidate_id"] for item in candidates]
    authorization_id = "RAUTH-R02-SYNTHETIC"
    payload = {
        "schema": "MAX_RESEARCH_OWNER_AUTHORIZATION_R02_V1",
        "gate": "R02",
        "action": "AUTHORIZE_DISCOVERY",
        "confirmed": True,
        "owner_confirmation": "OWNER_EXPLICIT_R02_DISCOVERY_AUTHORIZE",
        "execution_available": False,
        "cheap_screen_qualification_authority": False,
        "research_id": research_id,
        "r01_output_manifest_sha256": output_sha,
        "plan_id": "RPLAN-R02-SYNTHETIC",
        "plan_sha256": "b" * 64,
        "candidate_ids": candidate_ids,
        "candidate_count": len(candidate_ids),
    }
    ledger = {
        "integrity_status": "VERIFIED",
        "authorization": {
            "authorization_id": authorization_id,
            "research_id": research_id,
            "confirmed": True,
            "payload": payload,
        },
        "block": {
            "block_id": block_id,
            "research_id": research_id,
            "authorization_id": authorization_id,
            "state": "FROZEN_WAITING_EXECUTION",
            "plan_id": payload["plan_id"],
            "plan_sha256": payload["plan_sha256"],
            "r01_output_manifest_sha256": output_sha,
            "candidate_count": len(candidates),
            "candidates": candidates,
        },
        "terminal": None,
    }
    dataset = {
        "research_id": research_id,
        "dataset_id": lineage["dataset_id"],
        "r01_output_manifest_sha256": output_sha,
        "feature_contract": FEATURE_CONTRACT,
        "feature_order": list(FEATURE_NAMES),
        "label_contract": first_spec["label_contract"],
        "parent_lineage": lineage,
    }
    return ledger, dataset


def test_discovery_split_is_chronological_and_purges_training_targets() -> None:
    rows = _rows(100)

    split = build_discovery_split(rows, purge_bars=4)

    assert split["split_index"] == 80
    assert len(split["validation_rows"]) == 20
    assert split["validation_rows"][0]["source_row_id"] == 80
    assert split["train_rows"][-1]["source_row_id"] == 75
    assert split["train_rows"][-1]["source_row_id"] + 4 < 80
    assert split["train_rows"][-1]["signal_time"] < split["validation_rows"][0]["signal_time"]


@pytest.mark.parametrize("rows", [[], _rows(35)])
def test_discovery_split_rejects_empty_or_too_small_dataset(rows: list[dict]) -> None:
    with pytest.raises(ValueError, match="R02_CHEAP_SCREEN_SPLIT_MINIMUM_ROWS_UNMET"):
        build_discovery_split(rows, purge_bars=0)


def test_discovery_split_rejects_out_of_order_or_nonfinite_rows() -> None:
    out_of_order = _rows(100)
    out_of_order[20], out_of_order[21] = out_of_order[21], out_of_order[20]
    with pytest.raises(ValueError, match="R02_EXECUTOR_DISCOVERY_ROW_ORDER_INVALID"):
        build_discovery_split(out_of_order, purge_bars=0)

    nonfinite = _rows(100)
    nonfinite[4][FEATURE_NAMES[0]] = float("nan")
    with pytest.raises(ValueError, match="R02_EXECUTOR_DISCOVERY_FEATURE_NONFINITE"):
        build_discovery_split(nonfinite, purge_bars=0)


def test_discovery_split_rejects_malformed_rows_and_single_class_training() -> None:
    malformed = _rows(100)
    malformed[2].pop(FEATURE_NAMES[0])
    with pytest.raises(ValueError, match="R02_EXECUTOR_DISCOVERY_ROW_SCHEMA_INVALID"):
        build_discovery_split(malformed, purge_bars=0)

    single_class = _rows(100)
    for row in single_class[:80]:
        row["label"] = 0
    with pytest.raises(ValueError, match="R02_EXECUTOR_TRAINING_CLASSES_INCOMPLETE"):
        build_discovery_split(single_class, purge_bars=0)


def test_discovery_split_rejects_protected_partition_fields() -> None:
    rows = _rows(100)
    rows[0]["partition"] = "LOCKED_OOS"

    with pytest.raises(ValueError, match="R02_EXECUTOR_DISCOVERY_ROW_SCHEMA_INVALID"):
        build_discovery_split(rows, purge_bars=0)


def test_cheap_screen_metrics_are_fixed_class_order_and_diagnostic_only() -> None:
    labels = np.asarray([0, 1, 2, 0, 1, 2], dtype=np.int64)
    probabilities = np.asarray([
        [0.8, 0.1, 0.1],
        [0.1, 0.8, 0.1],
        [0.1, 0.1, 0.8],
        [0.7, 0.2, 0.1],
        [0.1, 0.7, 0.2],
        [0.2, 0.1, 0.7],
    ])

    result = evaluate_cheap_screen(labels, probabilities)

    assert result["status"] == "SCREEN_PASS"
    assert result["failure_code"] is None
    assert result["metrics"]["balanced_accuracy"] == 1.0
    assert result["metrics"]["macro_f1"] == 1.0
    assert result["cheap_screen_qualification_authority"] is False
    assert result["qualified_pool_admission_authority"] == "R03_FULL_WFA_ONLY"
    assert result["scientific_qualification"] is False


def test_cheap_screen_threshold_failure_and_nonfinite_prediction_fail_closed() -> None:
    labels = np.asarray([0, 1, 2] * 4, dtype=np.int64)
    poor_probabilities = np.tile([0.9, 0.05, 0.05], (len(labels), 1))
    result = evaluate_cheap_screen(labels, poor_probabilities)
    assert result["status"] == "SCREEN_FAIL"
    assert result["failure_code"] == "CHEAP_SCREEN_THRESHOLD_NOT_MET"

    invalid = poor_probabilities.copy()
    invalid[0, 0] = np.nan
    with pytest.raises(ValueError, match="R02_EXECUTOR_PREDICTION_NONFINITE"):
        evaluate_cheap_screen(labels, invalid)

    invalid_target = np.asarray([0, 1, np.nan] * 4)
    with pytest.raises(ValueError, match="R02_EXECUTOR_METRIC_SHAPE_INVALID"):
        evaluate_cheap_screen(invalid_target, poor_probabilities)


@pytest.mark.parametrize("family", ["lightgbm", "xgboost", "random_forest"])
def test_family_adapters_fit_predict_deterministically_on_synthetic_data(
    family: str,
) -> None:
    specs = _candidate_specs()
    spec = specs[family]
    X_train, y_train, X_validation = _arrays()

    first = fit_predict_candidate(spec, X_train, y_train, X_validation)
    replay = fit_predict_candidate(spec, X_train, y_train, X_validation)

    assert first.shape == (len(X_validation), 3)
    assert np.array_equal(first, replay)
    assert np.all(np.isfinite(first))
    assert np.allclose(first.sum(axis=1), 1.0)


@pytest.mark.parametrize(
    ("family", "accelerator", "device_id", "platform_id"),
    [
        ("lightgbm", "GPU_OPENCL", 2, 1),
        ("xgboost", "GPU_CUDA", 2, None),
    ],
)
def test_gpu_family_adapters_select_explicit_native_backend(
    family: str,
    accelerator: str,
    device_id: int,
    platform_id: int | None,
) -> None:
    spec = deepcopy(_candidate_specs()[family])
    spec["training_configuration"].update({
        "accelerator": accelerator,
        "device_id": device_id,
        "platform_id": platform_id,
    })

    estimator = models._ADAPTERS[family].create_estimator(spec)
    params = estimator.get_params(deep=False)

    if family == "lightgbm":
        assert params["device_type"] == "gpu"
        assert params["gpu_device_id"] == 2
        assert params["gpu_platform_id"] == 1
        assert "deterministic" not in params
        assert "force_col_wise" not in params
    else:
        assert params["device"] == "cuda:2"
        assert params["tree_method"] == "hist"


@pytest.mark.parametrize(
    ("family", "accelerator", "expected"),
    [
        ("lightgbm", "CPU", "cpu"),
        ("xgboost", "CPU", "cpu"),
    ],
)
def test_cpu_synthetic_ci_target_is_explicit_and_has_no_gpu_fallback(
    family: str,
    accelerator: str,
    expected: str,
) -> None:
    spec = deepcopy(_candidate_specs()[family])
    spec["training_configuration"]["accelerator"] = accelerator

    estimator = models._ADAPTERS[family].create_estimator(spec)

    if family == "lightgbm":
        assert estimator.get_params(deep=False)["device_type"] == expected
        assert estimator.get_params(deep=False)["deterministic"] is True
        assert estimator.get_params(deep=False)["force_col_wise"] is True
    else:
        assert estimator.get_params(deep=False)["device"] == expected


def test_family_adapter_rejects_unknown_family_and_missing_topology() -> None:
    spec = dict(_candidate_specs()["random_forest"])
    X_train, y_train, X_validation = _arrays()
    spec["model_family"] = "gru"
    with pytest.raises(
        ValueError,
        match="R02_TEMPORAL_MODEL_FAMILY_NOT_YET_AUTHORIZED",
    ):
        fit_predict_candidate(spec, X_train, y_train, X_validation)

    spec = dict(_candidate_specs()["random_forest"])
    spec["topology_spec"] = {"n_estimators": 8}
    with pytest.raises(ValueError, match="R02_CANDIDATE_TOPOLOGY_FIELDS_INVALID"):
        fit_predict_candidate(spec, X_train, y_train, X_validation)

    spec = dict(_candidate_specs()["random_forest"])
    spec.pop("training_configuration")
    with pytest.raises(
        ValueError,
        match="R02_CANDIDATE_TRAINING_CONFIGURATION_INVALID",
    ):
        fit_predict_candidate(spec, X_train, y_train, X_validation)


@pytest.mark.parametrize("failure_stage", ["fit", "predict"])
def test_model_worker_retains_fit_and_prediction_exceptions_as_failure(
    failure_stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingEstimator:
        classes_ = np.asarray([0, 1, 2])

        def fit(self, *_args: Any, **_kwargs: Any) -> None:
            if failure_stage == "fit":
                raise RuntimeError("synthetic fit failure")

        def predict_proba(self, *_args: Any, **_kwargs: Any) -> None:
            if failure_stage == "predict":
                raise RuntimeError("synthetic prediction failure")

    adapter = models._ADAPTERS["random_forest"]
    monkeypatch.setattr(
        adapter,
        "create_estimator",
        lambda _spec: FailingEstimator(),
    )
    X_train, y_train, X_validation = _arrays()

    class CaptureConnection:
        messages: list[dict[str, Any]]
        closed: bool

        def __init__(self) -> None:
            self.messages = []
            self.closed = False

        def send(self, message: dict[str, Any]) -> None:
            self.messages.append(message)

        def close(self) -> None:
            self.closed = True

    connection = CaptureConnection()
    executor._fit_predict_worker(
        connection,
        _candidate_specs()["random_forest"],
        X_train,
        y_train,
        X_validation,
    )

    assert connection.messages == [{"ok": False}]
    assert connection.closed is True


def test_bounded_fit_predict_stops_timed_out_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReceivePipe:
        def poll(self, _timeout: float) -> bool:
            return False

        def close(self) -> None:
            pass

    class SendPipe:
        def close(self) -> None:
            pass

    class FakeProcess:
        def __init__(self) -> None:
            self.alive = False
            self.terminated = False

        def start(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            self.terminated = True
            self.alive = False

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def kill(self) -> None:
            self.alive = False

    process = FakeProcess()

    class FakeContext:
        def Pipe(self, *, duplex: bool) -> tuple[ReceivePipe, SendPipe]:
            assert duplex is False
            return ReceivePipe(), SendPipe()

        def Process(self, **_kwargs: Any) -> FakeProcess:
            return process

    monkeypatch.setattr(
        executor.multiprocessing,
        "get_context",
        lambda _method: FakeContext(),
    )
    X_train, y_train, X_validation = _arrays()

    probabilities, elapsed, failure_code = executor._bounded_fit_predict(
        _candidate_specs()["random_forest"],
        X_train,
        y_train,
        X_validation,
        timeout_seconds=0.01,
    )

    assert probabilities is None
    assert elapsed <= 0.01
    assert failure_code == "COMPUTE_BUDGET_EXHAUSTED"
    assert process.terminated is True


@pytest.mark.parametrize(
    "mutation",
    [
        "research_id",
        "dataset_id",
        "r01_manifest",
        "block_research_id",
        "plan_binding",
        "candidate_spec",
        "candidate_id",
        "candidate_block_id",
        "model_family_column",
        "seed_column",
        "ordinal",
        "missing_candidate",
        "extra_candidate",
        "missing_training_configuration",
        "temporal_family",
        "malformed_lineage",
        "malformed_payload",
    ],
)
def test_executor_rejects_tampered_or_substituted_authority(mutation: str) -> None:
    ledger, dataset = _execution_authority_fixture()
    ledger = deepcopy(ledger)
    dataset = deepcopy(dataset)
    candidate = ledger["block"]["candidates"][0]
    if mutation == "research_id":
        dataset["research_id"] = "RSRCH-OTHER"
    elif mutation == "dataset_id":
        dataset["dataset_id"] = "RDATA-OTHER"
    elif mutation == "r01_manifest":
        dataset["r01_output_manifest_sha256"] = "c" * 64
    elif mutation == "block_research_id":
        ledger["block"]["research_id"] = "RSRCH-OTHER"
    elif mutation == "plan_binding":
        ledger["block"]["plan_id"] = "RPLAN-OTHER"
    elif mutation == "candidate_spec":
        candidate["spec"]["seed"] += 1
    elif mutation == "candidate_id":
        candidate["candidate_id"] = "RCAND-OTHER"
    elif mutation == "candidate_block_id":
        candidate["block_id"] = "RDISC-OTHER"
    elif mutation == "model_family_column":
        candidate["model_family"] = (
            "xgboost"
            if candidate["spec"]["model_family"] != "xgboost"
            else "lightgbm"
        )
    elif mutation == "seed_column":
        candidate["seed"] += 1
    elif mutation == "ordinal":
        candidate["ordinal"] = 2
    elif mutation == "missing_candidate":
        ledger["block"]["candidates"].pop()
    elif mutation == "extra_candidate":
        ledger["block"]["candidates"].append(deepcopy(candidate))
    elif mutation == "missing_training_configuration":
        candidate["spec"].pop("training_configuration")
    elif mutation == "temporal_family":
        candidate["spec"]["model_family"] = "tft"
    elif mutation == "malformed_lineage":
        dataset["parent_lineage"] = None
        candidate_ids = []
        for candidate_row in ledger["block"]["candidates"]:
            candidate_row["spec"]["parent_lineage"] = None
            candidate_row["candidate_id"] = derive_candidate_id(
                candidate_row["spec"]
            )
            candidate_row["spec_sha256"] = stable_hash(candidate_row["spec"])
            candidate_ids.append(candidate_row["candidate_id"])
        ledger["authorization"]["payload"]["candidate_ids"] = candidate_ids
    elif mutation == "malformed_payload":
        ledger["authorization"]["payload"] = []

    with pytest.raises(
        RuntimeError,
        match="R02_EXECUTOR_AUTHORITY_BINDING_INVALID",
    ):
        executor._validate_execution_authority(
            ledger=ledger,
            dataset=dataset,
            research_id="RSRCH-R02-SYNTHETIC",
            dataset_id="RDATA-R02-SYNTHETIC",
            output_sha="a" * 64,
        )


def test_executor_compute_budget_exhaustion_retains_all_candidates_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, _dataset = _execution_authority_fixture()
    ledger["block"]["compute_budget"] = {
        "value": 0.5,
        "unit": "FIT_SECONDS",
    }
    split = build_discovery_split(_rows(100), purge_bars=0)
    calls: list[float] = []

    def timeout(*_args: Any, timeout_seconds: float):
        calls.append(timeout_seconds)
        return None, timeout_seconds, "COMPUTE_BUDGET_EXHAUSTED"

    monkeypatch.setattr(executor, "_bounded_fit_predict", timeout)
    outcomes = executor._build_outcome_requests(
        ledger,
        split,
        split_failure_code=None,
    )

    assert len(outcomes) == 3
    assert calls == [0.5]
    assert all(item["status"] == "EXECUTION_ERROR" for item in outcomes)
    assert outcomes[0]["compute_consumed"]["value"] == 0.5
    assert sum(item["compute_consumed"]["value"] for item in outcomes) <= 0.5


def test_executor_retains_every_candidate_after_worker_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, _dataset = _execution_authority_fixture()
    ledger["block"]["compute_budget"] = {
        "value": 120,
        "unit": "FIT_SECONDS",
    }
    split = build_discovery_split(_rows(100), purge_bars=0)
    monkeypatch.setattr(
        executor,
        "_bounded_fit_predict",
        lambda *_args, **_kwargs: (None, 0.25, "MODEL_EXECUTION_FAILED"),
    )

    outcomes = executor._build_outcome_requests(
        ledger,
        split,
        split_failure_code=None,
    )

    assert [item["candidate_id"] for item in outcomes] == [
        item["candidate_id"] for item in ledger["block"]["candidates"]
    ]
    assert all(item["status"] == "EXECUTION_ERROR" for item in outcomes)
    assert all(item["failure_code"] == "MODEL_EXECUTION_FAILED" for item in outcomes)
    assert sum(item["compute_consumed"]["value"] for item in outcomes) == 0.75
