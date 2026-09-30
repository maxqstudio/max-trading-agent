from __future__ import annotations

from datetime import datetime
import multiprocessing
import math
from pathlib import Path
import time
from typing import Any

import numpy as np

from .research_cp32 import FEATURE_NAMES
from .research_contract import (
    candidate_id as derive_candidate_id,
    stable_hash,
)
from .research_r02_contract import (
    CHEAP_SCREEN_POLICY,
    candidate_identity_contract,
)
from .research_r02_models import _validate_spec, fit_predict_candidate
from .research_r02_store import (
    _execute_r02_attempt_and_commit,
    begin_r02_execution_attempt,
    validate_r02_outcome_ledger_integrity,
)
from .config import DATABASE_PATH


def build_discovery_split(
    rows: Any,
    *,
    purge_bars: int,
) -> dict[str, Any]:
    if not isinstance(rows, list) or not rows:
        raise ValueError("R02_CHEAP_SCREEN_SPLIT_MINIMUM_ROWS_UNMET")
    if isinstance(purge_bars, bool) or not isinstance(purge_bars, int) or purge_bars < 0:
        raise ValueError("R02_EXECUTOR_PURGE_AUTHORITY_INVALID")

    expected_fields = {"source_row_id", "signal_time", "label", *FEATURE_NAMES}
    prior_row_id: int | None = None
    prior_time: datetime | None = None
    labels: list[int] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != expected_fields:
            raise ValueError("R02_EXECUTOR_DISCOVERY_ROW_SCHEMA_INVALID")
        row_id = row.get("source_row_id")
        signal_time = row.get("signal_time")
        label = row.get("label")
        if (
            isinstance(row_id, bool)
            or not isinstance(row_id, int)
            or row_id < 0
            or not isinstance(signal_time, datetime)
            or signal_time.tzinfo is None
            or signal_time.utcoffset() is None
            or isinstance(label, bool)
            or not isinstance(label, int)
            or label not in CHEAP_SCREEN_POLICY["required_class_ids"]
        ):
            raise ValueError("R02_EXECUTOR_DISCOVERY_ROW_INVALID")
        if (
            prior_row_id is not None
            and row_id <= prior_row_id
        ) or (
            prior_time is not None
            and signal_time <= prior_time
        ):
            raise ValueError("R02_EXECUTOR_DISCOVERY_ROW_ORDER_INVALID")
        prior_row_id = row_id
        prior_time = signal_time
        labels.append(label)
        for feature in FEATURE_NAMES:
            value = row[feature]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("R02_EXECUTOR_DISCOVERY_FEATURE_INVALID")
            if not math.isfinite(float(value)):
                raise ValueError("R02_EXECUTOR_DISCOVERY_FEATURE_NONFINITE")

    split = CHEAP_SCREEN_POLICY["split"]
    split_index = int(len(rows) * float(split["train_fraction"]))
    validation_rows = rows[split_index:]
    if not validation_rows:
        raise ValueError("R02_CHEAP_SCREEN_SPLIT_MINIMUM_ROWS_UNMET")
    first_validation_source_row_id = int(
        validation_rows[0]["source_row_id"]
    )
    train_rows = [
        row
        for row in rows[:split_index]
        if int(row["source_row_id"]) + purge_bars
        < first_validation_source_row_id
    ]
    if (
        len(train_rows) < int(CHEAP_SCREEN_POLICY["minimum_training_rows"])
        or len(validation_rows)
        < int(CHEAP_SCREEN_POLICY["minimum_validation_rows"])
    ):
        raise ValueError("R02_CHEAP_SCREEN_SPLIT_MINIMUM_ROWS_UNMET")
    required_classes = set(CHEAP_SCREEN_POLICY["required_class_ids"])
    if {int(row["label"]) for row in train_rows} != required_classes:
        raise ValueError("R02_EXECUTOR_TRAINING_CLASSES_INCOMPLETE")

    return {
        "policy_id": CHEAP_SCREEN_POLICY["policy_id"],
        "split_index": split_index,
        "purge_bars": purge_bars,
        "purged_training_rows": split_index - len(train_rows),
        "first_validation_source_row_id": first_validation_source_row_id,
        "train_rows": train_rows,
        "validation_rows": validation_rows,
    }


def evaluate_cheap_screen(
    labels: Any,
    probabilities: Any,
) -> dict[str, Any]:
    from sklearn.metrics import balanced_accuracy_score, f1_score, log_loss

    try:
        y_true = np.asarray(labels)
        probs = np.asarray(probabilities, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("R02_EXECUTOR_METRIC_INPUT_INVALID") from exc
    class_ids = np.asarray(CHEAP_SCREEN_POLICY["required_class_ids"], dtype=np.int64)
    if (
        y_true.ndim != 1
        or y_true.size == 0
        or probs.shape != (y_true.size, class_ids.size)
        or not np.issubdtype(y_true.dtype, np.integer)
        or not set(np.unique(y_true).tolist()).issubset(set(class_ids.tolist()))
    ):
        raise ValueError("R02_EXECUTOR_METRIC_SHAPE_INVALID")
    if not np.all(np.isfinite(probs)):
        raise ValueError("R02_EXECUTOR_PREDICTION_NONFINITE")
    if (
        np.any(probs < 0.0)
        or np.any(probs > 1.0)
        or not np.allclose(probs.sum(axis=1), 1.0, rtol=0.0, atol=1e-6)
    ):
        raise ValueError("R02_EXECUTOR_PREDICTION_PROBABILITY_INVALID")

    predicted = class_ids[np.argmax(probs, axis=1)]
    one_hot = np.eye(len(class_ids), dtype=np.float64)[y_true.astype(np.int64)]
    metrics = {
        "balanced_accuracy": round(
            float(balanced_accuracy_score(y_true, predicted)), 12
        ),
        "macro_f1": round(
            float(f1_score(
                y_true,
                predicted,
                labels=class_ids.tolist(),
                average="macro",
                zero_division=0,
            )),
            12,
        ),
        "log_loss": round(
            float(log_loss(
                y_true,
                probs,
                labels=class_ids.tolist(),
                normalize=True,
            )),
            12,
        ),
        "multiclass_brier": round(
            float(np.mean(np.sum((probs - one_hot) ** 2, axis=1))),
            12,
        ),
        "validation_rows": int(y_true.size),
    }
    if any(not math.isfinite(value) for value in metrics.values()):
        raise ValueError("R02_EXECUTOR_METRIC_NONFINITE")

    thresholds = CHEAP_SCREEN_POLICY["screen_thresholds"]
    screen_pass = (
        metrics["balanced_accuracy"]
        >= float(thresholds["balanced_accuracy_min"])
        and metrics["log_loss"] <= float(thresholds["log_loss_max"])
    )
    return {
        "status": "SCREEN_PASS" if screen_pass else "SCREEN_FAIL",
        "metrics": metrics,
        "failure_code": None if screen_pass else "CHEAP_SCREEN_THRESHOLD_NOT_MET",
        "cheap_screen_qualification_authority": False,
        "qualified_pool_admission_authority": "R03_FULL_WFA_ONLY",
        "scientific_qualification": False,
    }


def _fit_predict_worker(
    connection: Any,
    spec: dict[str, Any],
    train_x: np.ndarray,
    train_y: np.ndarray,
    validation_x: np.ndarray,
) -> None:
    try:
        probabilities = fit_predict_candidate(
            spec,
            train_x,
            train_y,
            validation_x,
        )
        connection.send({"ok": True, "probabilities": probabilities.tolist()})
    except Exception:
        try:
            connection.send({"ok": False})
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


def _stop_worker(process: Any) -> None:
    try:
        alive = process.is_alive()
    except (AssertionError, ValueError):
        return
    if not alive:
        return
    try:
        process.terminate()
        process.join(timeout=1.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=1.0)
    except (AssertionError, OSError, ValueError):
        return


def _bounded_fit_predict(
    spec: dict[str, Any],
    train_x: np.ndarray,
    train_y: np.ndarray,
    validation_x: np.ndarray,
    *,
    timeout_seconds: float,
) -> tuple[np.ndarray | None, float, str | None]:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        return None, 0.0, "COMPUTE_BUDGET_EXHAUSTED"

    started = time.monotonic()
    receive = None
    send = None
    process = None
    try:
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        process = context.Process(
            target=_fit_predict_worker,
            args=(send, spec, train_x, train_y, validation_x),
            daemon=True,
        )
        process.start()
        send.close()
        wait_seconds = max(0.0, timeout_seconds - (time.monotonic() - started))
        if not receive.poll(wait_seconds):
            _stop_worker(process)
            elapsed = time.monotonic() - started
            return None, min(timeout_seconds, elapsed), "COMPUTE_BUDGET_EXHAUSTED"
        try:
            message = receive.recv()
        except (EOFError, OSError):
            message = None
        process.join(timeout=1.0)
        if process.is_alive():
            _stop_worker(process)
        elapsed = time.monotonic() - started
        if elapsed > timeout_seconds:
            return None, timeout_seconds, "COMPUTE_BUDGET_EXHAUSTED"
        if (
            process.exitcode != 0
            or not isinstance(message, dict)
            or message.get("ok") is not True
        ):
            return None, elapsed, "MODEL_EXECUTION_FAILED"
        try:
            probabilities = np.asarray(message["probabilities"], dtype=np.float64)
        except (KeyError, TypeError, ValueError, OverflowError):
            return None, elapsed, "MODEL_EXECUTION_FAILED"
        return probabilities, elapsed, None
    except Exception:
        if process is not None:
            _stop_worker(process)
        elapsed = min(timeout_seconds, time.monotonic() - started)
        return None, elapsed, "MODEL_EXECUTION_FAILED"
    finally:
        if receive is not None:
            receive.close()
        if send is not None:
            try:
                send.close()
            except OSError:
                pass


def _validate_execution_authority(
    *,
    ledger: dict[str, Any],
    dataset: dict[str, Any],
    research_id: str,
    dataset_id: str,
    output_sha: str,
) -> None:
    try:
        block = ledger["block"]
        authorization = ledger["authorization"]
        candidates = block["candidates"]
        payload = authorization["payload"]
        lineage = dataset["parent_lineage"]
        expected_ids = payload["candidate_ids"]
        if (
            ledger.get("integrity_status") != "VERIFIED"
            or ledger.get("terminal") is not None
            or authorization.get("confirmed") is not True
            or str(authorization.get("research_id") or "") != research_id
            or payload.get("schema")
            != "MAX_RESEARCH_OWNER_AUTHORIZATION_R02_V1"
            or payload.get("gate") != "R02"
            or payload.get("action") != "AUTHORIZE_DISCOVERY"
            or payload.get("confirmed") is not True
            or payload.get("owner_confirmation")
            != "OWNER_EXPLICIT_R02_DISCOVERY_AUTHORIZE"
            or payload.get("execution_available") is not False
            or payload.get("cheap_screen_qualification_authority") is not False
            or payload.get("research_id") != research_id
            or payload.get("r01_output_manifest_sha256") != output_sha
            or str(block.get("research_id") or "") != research_id
            or block.get("state") != "FROZEN_WAITING_EXECUTION"
            or block.get("authorization_id") != authorization.get(
                "authorization_id"
            )
            or block.get("plan_id") != payload.get("plan_id")
            or block.get("plan_sha256") != payload.get("plan_sha256")
            or str(dataset.get("research_id") or "") != research_id
            or str(dataset.get("dataset_id") or "") != dataset_id
            or str(dataset.get("r01_output_manifest_sha256") or "") != output_sha
            or str(block.get("r01_output_manifest_sha256") or "") != output_sha
            or dataset.get("feature_contract") != block["candidates"][0]["spec"][
                "feature_contract"
            ]
            or list(dataset.get("feature_order") or []) != list(FEATURE_NAMES)
            or str(dataset.get("label_contract") or "")
            != str(block["candidates"][0]["spec"].get("label_contract") or "")
            or type(block.get("candidate_count")) is not int
            or len(candidates) != int(block["candidate_count"])
            or type(payload.get("candidate_count")) is not int
            or payload["candidate_count"] != block["candidate_count"]
            or not isinstance(expected_ids, list)
            or len(expected_ids) != len(candidates)
            or dataset.get("parent_lineage") != candidates[0]["spec"].get(
                "parent_lineage"
            )
        ):
            raise ValueError("top-level binding")

        candidate_ids: list[str] = []
        required_spec_fields = set(
            candidate_identity_contract()["required_components"]
        )
        for ordinal, candidate in enumerate(candidates):
            candidate_row = candidate["candidate_id"]
            spec = candidate["spec"]
            if (
                set(spec) != required_spec_fields
                or spec.get("model_family")
                not in {"lightgbm", "xgboost", "random_forest"}
                or _validate_spec(spec) != spec
                or derive_candidate_id(spec) != candidate_row
                or candidate["block_id"] != block["block_id"]
                or type(candidate.get("ordinal")) is not int
                or candidate["ordinal"] != ordinal
                or candidate.get("model_family") != spec["model_family"]
                or type(candidate.get("seed")) is not int
                or candidate["seed"] != spec["seed"]
                or candidate.get("spec_sha256") != stable_hash(spec)
                or spec.get("research_id") != research_id
                or spec.get("parent_lineage") != lineage
                or spec.get("feature_contract") != dataset["feature_contract"]
                or spec.get("label_contract") != dataset["label_contract"]
            ):
                raise ValueError("candidate binding")
            candidate_ids.append(str(candidate_row))
        if (
            len(set(candidate_ids)) != len(candidate_ids)
            or candidate_ids != expected_ids
            or lineage.get("dataset_id") != dataset_id
            or lineage.get("r01_output_manifest_sha256") != output_sha
        ):
            raise ValueError("candidate universe")
    except (
        AttributeError,
        KeyError,
        IndexError,
        TypeError,
        OverflowError,
        ValueError,
    ) as exc:
        raise RuntimeError("R02_EXECUTOR_AUTHORITY_BINDING_INVALID") from exc


def _execution_error(
    candidate_id: str,
    failure_code: str,
    *,
    compute_seconds: float = 0.0,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "status": "EXECUTION_ERROR",
        "metrics": {},
        "compute_consumed": {
            "value": max(0.0, float(compute_seconds)),
            "unit": "FIT_SECONDS",
        },
        "failure_code": failure_code,
    }


def _build_outcome_requests(
    ledger: dict[str, Any],
    split: dict[str, Any] | None,
    *,
    split_failure_code: str | None,
) -> list[dict[str, Any]]:
    block = ledger["block"]
    candidates = block["candidates"]
    budget = block["compute_budget"]
    budget_seconds = float(budget["value"])
    if split is None:
        code = split_failure_code or "DISCOVERY_DATASET_UNTRAINABLE"
        return [
            _execution_error(
                str(candidate["candidate_id"]),
                code,
            )
            for candidate in candidates
        ]

    train_x = np.asarray(
        [[row[name] for name in FEATURE_NAMES] for row in split["train_rows"]],
        dtype=np.float64,
    )
    train_y = np.asarray(
        [row["label"] for row in split["train_rows"]],
        dtype=np.int64,
    )
    validation_x = np.asarray(
        [
            [row[name] for name in FEATURE_NAMES]
            for row in split["validation_rows"]
        ],
        dtype=np.float64,
    )

    outcomes: list[dict[str, Any]] = []
    compute_used = 0.0
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        remaining = max(0.0, budget_seconds - compute_used)
        if remaining <= 0.0:
            outcomes.append(
                _execution_error(candidate_id, "COMPUTE_BUDGET_EXHAUSTED")
            )
            continue

        probabilities, elapsed, worker_error = _bounded_fit_predict(
            candidate["spec"],
            train_x,
            train_y,
            validation_x,
            timeout_seconds=remaining,
        )
        elapsed = min(remaining, max(0.0, float(elapsed)))
        compute_used += elapsed
        if worker_error is not None or probabilities is None:
            outcomes.append(
                _execution_error(
                    candidate_id,
                    worker_error or "MODEL_EXECUTION_FAILED",
                    compute_seconds=elapsed,
                )
            )
            continue
        try:
            screen = evaluate_cheap_screen(
                [row["label"] for row in split["validation_rows"]],
                probabilities,
            )
        except (TypeError, ValueError, OverflowError):
            outcomes.append(
                _execution_error(
                    candidate_id,
                    "CHEAP_SCREEN_EVALUATION_FAILED",
                    compute_seconds=elapsed,
                )
            )
            continue
        outcomes.append({
            "candidate_id": candidate_id,
            "status": screen["status"],
            "metrics": screen["metrics"],
            "compute_consumed": {
                "value": elapsed,
                "unit": "FIT_SECONDS",
            },
            "failure_code": screen["failure_code"],
        })
    return outcomes


def execute_r02_discovery_block(
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    from .research_r01_service import read_r01_discovery_training_dataset
    from .research_r01_store import get_r01_run
    from .research_store import latest_research

    current = latest_research(path=path)
    if current is None:
        raise RuntimeError("R02_EXECUTOR_CURRENT_RESEARCH_REQUIRED")
    research_id = str(current.get("research_id") or "").strip()
    if not research_id:
        raise RuntimeError("R02_EXECUTOR_CURRENT_RESEARCH_REQUIRED")

    ledger = validate_r02_outcome_ledger_integrity(research_id, path=path)
    if ledger is None:
        raise RuntimeError("R02_EXECUTOR_FROZEN_BLOCK_REQUIRED")
    if ledger.get("terminal") is not None:
        return ledger
    if ledger.get("integrity_status") != "VERIFIED":
        raise RuntimeError("R02_EXECUTOR_LEDGER_INTEGRITY_REQUIRED")

    run = get_r01_run(research_id, path=path)
    if run is None or run.get("state") != "PASS_WAITING_OWNER":
        raise RuntimeError("R02_EXECUTOR_ACCEPTED_R01_REQUIRED")
    dataset_id = str(run.get("dataset_id") or "")
    output_sha = str(run.get("output_manifest_sha") or "")
    if not dataset_id or not output_sha:
        raise RuntimeError("R02_EXECUTOR_R01_DATASET_AUTHORITY_INVALID")

    dataset = read_r01_discovery_training_dataset(
        research_id=research_id,
        dataset_id=dataset_id,
        r01_output_manifest_sha256=output_sha,
        block_id=str(ledger["block"]["block_id"]),
        path=path,
    )
    _validate_execution_authority(
        ledger=ledger,
        dataset=dataset,
        research_id=research_id,
        dataset_id=dataset_id,
        output_sha=output_sha,
    )

    try:
        split = build_discovery_split(
            dataset["rows"],
            purge_bars=int(dataset["minimum_legal_purge_main_bars"]),
        )
        split_failure_code = None
    except ValueError as exc:
        code = str(exc)
        if code == "R02_CHEAP_SCREEN_SPLIT_MINIMUM_ROWS_UNMET":
            split = None
            split_failure_code = "DISCOVERY_DATASET_UNTRAINABLE"
        elif code == "R02_EXECUTOR_TRAINING_CLASSES_INCOMPLETE":
            split = None
            split_failure_code = "DISCOVERY_TRAINING_CLASSES_INCOMPLETE"
        else:
            raise RuntimeError("R02_EXECUTOR_DISCOVERY_DATA_INVALID") from exc

    attempt = begin_r02_execution_attempt(research_id, path=path)
    if attempt.get("completed") is True:
        return attempt["ledger"]
    if attempt.get("block_id") != ledger["block"]["block_id"]:
        raise RuntimeError("R02_EXECUTOR_FROZEN_AUTHORITY_CHANGED")

    expected_candidate_ids = [
        item["candidate_id"] for item in ledger["block"]["candidates"]
    ]
    outcome_requests = _build_outcome_requests(
        ledger,
        split,
        split_failure_code=split_failure_code,
    )

    return _execute_r02_attempt_and_commit(
        research_id,
        str(attempt["attempt_id"]),
        outcome_requests,
        expected_block_id=str(ledger["block"]["block_id"]),
        expected_plan_sha256=str(ledger["block"]["plan_sha256"]),
        expected_candidate_ids=expected_candidate_ids,
        path=path,
    )
