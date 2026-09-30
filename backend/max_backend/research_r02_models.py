from __future__ import annotations

from typing import Any, Protocol

import numpy as np

from .research_r02_contract import (
    CHEAP_SCREEN_POLICY,
    TEMPORAL_MODEL_FAMILIES,
    _canonical_preprocessing,
    _canonical_topology_spec,
    _canonical_training_configuration,
)

CLASS_IDS = (0, 1, 2)


class _FamilyAdapter(Protocol):
    family: str

    def create_estimator(self, spec: dict[str, Any]) -> Any: ...


class _AdapterBase:
    family = ""

    def create_estimator(self, spec: dict[str, Any]) -> Any:
        raise NotImplementedError

    def fit_predict(
        self,
        spec: dict[str, Any],
        X_train: Any,
        y_train: Any,
        X_validation: Any,
    ) -> np.ndarray:
        from sklearn.preprocessing import StandardScaler
        from sklearn.utils.class_weight import compute_sample_weight

        candidate = _validate_spec(spec, expected_family=self.family)
        train_x, train_y, validation_x = _validate_arrays(
            X_train, y_train, X_validation
        )
        if candidate["preprocessing"]["scaling"] == "STANDARD":
            scaler = StandardScaler()
            train_x = scaler.fit_transform(train_x)
            validation_x = scaler.transform(validation_x)
        if not np.all(np.isfinite(train_x)) or not np.all(np.isfinite(validation_x)):
            raise ValueError("R02_EXECUTOR_PREPROCESSING_NONFINITE")

        sample_weight = compute_sample_weight(
            class_weight="balanced",
            y=train_y,
        )
        estimator = self.create_estimator(candidate)
        estimator.fit(train_x, train_y, sample_weight=sample_weight)
        classes = np.asarray(getattr(estimator, "classes_", []))
        if not np.array_equal(classes, np.asarray(CLASS_IDS)):
            raise ValueError("R02_EXECUTOR_MODEL_CLASS_ORDER_INVALID")
        probabilities = np.asarray(
            estimator.predict_proba(validation_x),
            dtype=np.float64,
        )
        if probabilities.shape != (len(validation_x), len(CLASS_IDS)):
            raise ValueError("R02_EXECUTOR_PREDICTION_SHAPE_INVALID")
        if not np.all(np.isfinite(probabilities)):
            raise ValueError("R02_EXECUTOR_PREDICTION_NONFINITE")
        if (
            np.any(probabilities < 0.0)
            or np.any(probabilities > 1.0)
            or not np.allclose(probabilities.sum(axis=1), 1.0, rtol=0.0, atol=1e-6)
        ):
            raise ValueError("R02_EXECUTOR_PREDICTION_PROBABILITY_INVALID")
        row_sums = probabilities.sum(axis=1, keepdims=True)
        if np.any(row_sums <= 0.0):
            raise ValueError("R02_EXECUTOR_PREDICTION_PROBABILITY_INVALID")
        return probabilities / row_sums


class _LightGBMAdapter(_AdapterBase):
    family = "lightgbm"

    def create_estimator(self, spec: dict[str, Any]) -> Any:
        from lightgbm import LGBMClassifier

        policy = CHEAP_SCREEN_POLICY["executor_policy"]
        params = dict(policy["family_parameters"][self.family])
        params.update(spec["topology_spec"])
        training = spec["training_configuration"]
        if training["accelerator"] == "GPU_OPENCL":
            params.update({
                "device_type": "gpu",
                "gpu_device_id": training["device_id"],
                "gpu_platform_id": training["platform_id"],
            })
        else:
            params["device_type"] = "cpu"
            params["deterministic"] = True
            params["force_col_wise"] = True
        params["n_jobs"] = policy["threads"]
        params["random_state"] = spec["seed"]
        return LGBMClassifier(**params)


class _XGBoostAdapter(_AdapterBase):
    family = "xgboost"

    def create_estimator(self, spec: dict[str, Any]) -> Any:
        from xgboost import XGBClassifier

        policy = CHEAP_SCREEN_POLICY["executor_policy"]
        params = dict(policy["family_parameters"][self.family])
        params.update(spec["topology_spec"])
        training = spec["training_configuration"]
        params["device"] = (
            f"cuda:{training['device_id']}"
            if training["accelerator"] == "GPU_CUDA"
            else "cpu"
        )
        params["n_jobs"] = policy["threads"]
        params["random_state"] = spec["seed"]
        return XGBClassifier(**params)


class _RandomForestAdapter(_AdapterBase):
    family = "random_forest"

    def create_estimator(self, spec: dict[str, Any]) -> Any:
        from sklearn.ensemble import RandomForestClassifier

        policy = CHEAP_SCREEN_POLICY["executor_policy"]
        params = dict(policy["family_parameters"][self.family])
        params.update(spec["topology_spec"])
        params["n_jobs"] = policy["threads"]
        params["random_state"] = spec["seed"]
        return RandomForestClassifier(**params)


_ADAPTERS: dict[str, _FamilyAdapter] = {
    "lightgbm": _LightGBMAdapter(),
    "xgboost": _XGBoostAdapter(),
    "random_forest": _RandomForestAdapter(),
}


def _validate_spec(spec: Any, *, expected_family: str | None = None) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError("R02_EXECUTOR_CANDIDATE_SPEC_REQUIRED")
    family = spec.get("model_family")
    if not isinstance(family, str):
        raise ValueError("R02_MODEL_FAMILY_UNSUPPORTED")
    family = family.lower()
    if family in TEMPORAL_MODEL_FAMILIES:
        raise ValueError("R02_TEMPORAL_MODEL_FAMILY_NOT_YET_AUTHORIZED")
    if family not in _ADAPTERS:
        raise ValueError("R02_MODEL_FAMILY_UNSUPPORTED")
    if expected_family is not None and family != expected_family:
        raise ValueError("R02_EXECUTOR_ADAPTER_FAMILY_MISMATCH")
    seed = spec.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 0xFFFFFFFF:
        raise ValueError("R02_CANDIDATE_SEED_INVALID")
    topology = _canonical_topology_spec(family, spec.get("topology_spec"))
    preprocessing = _canonical_preprocessing(spec.get("preprocessing"))
    training = _canonical_training_configuration(
        spec.get("training_configuration"),
        family=family,
    )
    return {
        **spec,
        "model_family": family,
        "seed": seed,
        "topology_spec": topology,
        "preprocessing": preprocessing,
        "training_configuration": training,
    }


def _validate_arrays(
    X_train: Any,
    y_train: Any,
    X_validation: Any,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    try:
        train_x = np.asarray(X_train, dtype=np.float64)
        raw_y = np.asarray(y_train, dtype=np.float64)
        validation_x = np.asarray(X_validation, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("R02_EXECUTOR_DATA_ARRAY_INVALID") from exc
    if (
        train_x.ndim != 2
        or validation_x.ndim != 2
        or raw_y.ndim != 1
        or train_x.shape[0] != raw_y.shape[0]
        or train_x.shape[0] == 0
        or validation_x.shape[0] == 0
        or train_x.shape[1] != validation_x.shape[1]
        or train_x.shape[1] == 0
    ):
        raise ValueError("R02_EXECUTOR_DATA_SHAPE_INVALID")
    if not (
        np.all(np.isfinite(train_x))
        and np.all(np.isfinite(validation_x))
        and np.all(np.isfinite(raw_y))
    ):
        raise ValueError("R02_EXECUTOR_DATA_NONFINITE")
    if not np.all(raw_y == np.floor(raw_y)):
        raise ValueError("R02_EXECUTOR_TARGET_INVALID")
    train_y = raw_y.astype(np.int64)
    if not set(np.unique(train_y).tolist()).issubset(CLASS_IDS):
        raise ValueError("R02_EXECUTOR_TARGET_INVALID")
    if tuple(np.unique(train_y).tolist()) != CLASS_IDS:
        raise ValueError("R02_EXECUTOR_TRAINING_CLASSES_INCOMPLETE")
    return train_x, train_y, validation_x


def fit_predict_candidate(
    spec: dict[str, Any],
    X_train: Any,
    y_train: Any,
    X_validation: Any,
) -> np.ndarray:
    if not isinstance(spec, dict):
        raise ValueError("R02_EXECUTOR_CANDIDATE_SPEC_REQUIRED")
    family = spec.get("model_family")
    if not isinstance(family, str):
        raise ValueError("R02_MODEL_FAMILY_UNSUPPORTED")
    normalized_family = family.lower()
    if normalized_family in TEMPORAL_MODEL_FAMILIES:
        raise ValueError("R02_TEMPORAL_MODEL_FAMILY_NOT_YET_AUTHORIZED")
    adapter = _ADAPTERS.get(normalized_family)
    if adapter is None:
        raise ValueError("R02_MODEL_FAMILY_UNSUPPORTED")
    return adapter.fit_predict(spec, X_train, y_train, X_validation)
