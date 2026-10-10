"""Application service for the ONNX-02 data-intake workflow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .config import DATABASE_PATH, EA_BASELINE, EA_MANIFEST, STATE_DIR
from .onnx_data_contract import (
    DATASET_SCHEMA_ID,
    DATASET_SCHEMA_VERSION,
    FEATURE_CONTRACT,
    STRATEGY_CONTRACT,
    audit_training_csv,
    remove_identical_duplicates,
    validate_research_windows,
)
from .onnx_data_source import (
    OnnxDataSourceError,
    PublishedSnapshot,
    SourceCapture,
    SourceFingerprint,
    capture_training_source,
    publish_snapshot,
    report_payload,
    structurally_admissible_for_snapshot,
    verify_ea_authority,
    verify_snapshot_file,
)
from .onnx_data_store import (
    get_snapshot,
    latest_snapshot,
    latest_window_config,
    persist_data_ready_evidence,
    persist_snapshot,
    readiness_for_snapshot,
    recent_snapshots,
    save_window_config,
)


class OnnxDataService:
    def __init__(
        self,
        *,
        common_files_root: Path | None = None,
        snapshot_root: Path | None = None,
        database_path: Path = DATABASE_PATH,
        manifest_path: Path = EA_MANIFEST,
        baseline_path: Path = EA_BASELINE,
        synthetic_test_evidence: bool = False,
    ) -> None:
        self.common_files_root = common_files_root
        self.snapshot_root = snapshot_root or (STATE_DIR / "onnx_data" / "snapshots")
        self.database_path = database_path
        self.manifest_path = manifest_path
        self.baseline_path = baseline_path
        self.synthetic_test_evidence = synthetic_test_evidence

    def _authority(self) -> dict[str, str]:
        return verify_ea_authority(self.manifest_path, self.baseline_path)

    @staticmethod
    def _verify_window_config(config: dict[str, Any], *, snapshot_id: str, snapshot_sha256: str) -> None:
        windows_json = json.dumps(config["windows"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        identity = hashlib.sha256("|".join((snapshot_sha256, windows_json, config["timezone_provenance"])).encode("utf-8")).hexdigest()
        if (
            config["snapshot_id"] != snapshot_id
            or config["snapshot_sha256"] != snapshot_sha256
            or config["window_config_id"] != "WIN-" + identity
            or not isinstance(config["revision"], int)
            or config["revision"] < 1
        ):
            raise OnnxDataSourceError("WINDOW_CONFIG_IDENTITY_MISMATCH", "Persisted research-window configuration does not match its immutable snapshot identity.")

    def preflight(self, *, source_path: str | None, timezone_provenance: str | None) -> dict[str, Any]:
        authority = self._authority()
        capture, report = capture_training_source(
            source_path,
            common_files_root=self.common_files_root,
            timezone_provenance=timezone_provenance,
        )
        return {
            "contract_version": "2.0",
            "source": "BACKEND_ONNX_02_DATA_API",
            "status": "PREFLIGHT_PASS" if report.status == "PASS" else "PREFLIGHT_BLOCKED",
            "snapshot_permitted": structurally_admissible_for_snapshot(report),
            "source_identity": {
                "filename": capture.filename,
                "path_sha256": hashlib.sha256(str(capture.path).encode("utf-8")).hexdigest(),
                "sha256": capture.sha256,
                "fingerprint": asdict(capture.fingerprint),
                "size_bytes": len(capture.raw),
            },
            "authority": authority,
            "data_quality": report_payload(report),
            "first_blocker": next((issue.code for issue in report.issues if issue.severity == "BLOCKER"), None),
            "evidence_class": "SYNTHETIC_TEST_EVIDENCE" if self.synthetic_test_evidence else "OWNER_SELECTED_SOURCE_NOT_EXECUTED_IN_SOURCE_CI",
        }

    def create_snapshot(
        self,
        *,
        source_path: str | None,
        timezone_provenance: str | None,
        expected_source_sha256: str,
    ) -> dict[str, Any]:
        if len(expected_source_sha256) != 64:
            raise ValueError("PREFLIGHT_SOURCE_SHA256_REQUIRED")
        authority = self._authority()
        capture, report = capture_training_source(
            source_path,
            common_files_root=self.common_files_root,
            timezone_provenance=timezone_provenance,
        )
        if capture.sha256 != expected_source_sha256:
            raise OnnxDataSourceError("SOURCE_CHANGED_SINCE_PREFLIGHT", "Training CSV bytes changed after preflight; repeat source review before snapshot publication.")
        return self._publish_and_persist(capture, report, authority)

    def resolve_identical_duplicates(
        self,
        *,
        snapshot_id: str,
        expected_snapshot_sha256: str,
        confirmation: str,
    ) -> dict[str, Any]:
        if confirmation != "REMOVE_IDENTICAL_DUPLICATES":
            raise ValueError("EXPLICIT_DUPLICATE_CORRECTION_CONFIRMATION_REQUIRED")
        source = self._verified_snapshot(snapshot_id, expected_snapshot_sha256)
        source_path = verify_snapshot_file(self.snapshot_root, expected_snapshot_sha256)
        raw = source_path.read_bytes()
        corrected_raw, removed = remove_identical_duplicates(raw)
        if hashlib.sha256(corrected_raw).hexdigest() == expected_snapshot_sha256:
            raise RuntimeError("DUPLICATE_CORRECTION_DID_NOT_CHANGE_SNAPSHOT")
        report = audit_training_csv(corrected_raw, timezone_provenance=source["timezone_provenance"])
        original_fingerprint = source["source_fingerprint"]
        capture = SourceCapture(
            path=Path(source["source_path"]),
            filename=Path(source["source_path"]).name,
            sha256=hashlib.sha256(corrected_raw).hexdigest(),
            raw=corrected_raw,
            fingerprint=SourceFingerprint(**original_fingerprint),
        )
        authority = {
            "schema_id": source["schema_id"],
            "schema_version": source["schema_version"],
            "strategy_contract": source["strategy_contract"],
            "feature_contract": source["feature_contract"],
            "ea_source_sha256": source["ea_source_sha256"],
            "ea_manifest_sha256": source["ea_manifest_sha256"],
        }
        correction = {
            "operation": "REMOVE_IDENTICAL_DUPLICATE_ROWS_FROM_DERIVED_SNAPSHOT",
            "confirmed_phrase": confirmation,
            "confirmation_scope": "LOCAL_EXPLICIT_REQUEST; CALLER IDENTITY IS NOT CRYPTOGRAPHICALLY ATTESTED",
            "source_snapshot_id": snapshot_id,
            "before_sha256": expected_snapshot_sha256,
            "backup_snapshot_sha256": expected_snapshot_sha256,
            "after_sha256": capture.sha256,
            "removed_rows": removed,
        }
        return self._publish_and_persist(
            capture,
            report,
            authority,
            parent_snapshot_id=snapshot_id,
            correction=correction,
        )

    def validate_windows(
        self,
        *,
        snapshot_id: str,
        snapshot_sha256: str,
        timezone_provenance: str,
        windows: Mapping[str, Mapping[str, str]],
    ) -> dict[str, Any]:
        snapshot = self._verified_snapshot(snapshot_id, snapshot_sha256)
        artifact = verify_snapshot_file(self.snapshot_root, snapshot_sha256)
        report = audit_training_csv(artifact.read_bytes(), timezone_provenance=snapshot["timezone_provenance"])
        if report_payload(report) != snapshot["dq"]:
            raise OnnxDataSourceError("SNAPSHOT_AUDIT_EVIDENCE_MISMATCH", "Recomputed snapshot audit differs from persisted data-quality evidence.")
        validation = validate_research_windows(report, windows, timezone_provenance=timezone_provenance)
        if validation.status != "PASS":
            return {
                "contract_version": "2.0",
                "source": "BACKEND_ONNX_02_DATA_API",
                "status": "WINDOWS_BLOCKED",
                "window_config": None,
                "validation": self._window_payload(validation),
                "readiness": None,
                "first_blocker": validation.issues[0].code if validation.issues else "WINDOWS_BLOCKED",
            }
        config = save_window_config(
            snapshot_id=snapshot_id,
            snapshot_sha256=snapshot_sha256,
            timezone_provenance=timezone_provenance,
            windows={name: dict(value) for name, value in windows.items()},
            validation=validation,
            path=self.database_path,
        )
        config["windows"] = json.loads(config.pop("windows_json"))
        config["validation"] = json.loads(config.pop("validation_json"))
        self._verify_window_config(config, snapshot_id=snapshot_id, snapshot_sha256=snapshot_sha256)
        readiness = persist_data_ready_evidence(
            snapshot=snapshot,
            window_config=config,
            evidence_class=snapshot["evidence_class"],
            path=self.database_path,
        )
        return {
            "contract_version": "2.0",
            "source": "BACKEND_ONNX_02_DATA_API",
            "status": "SYNTHETIC_TEST_EVIDENCE" if snapshot["evidence_class"] == "SYNTHETIC_TEST_EVIDENCE" else ("DATA_READY" if readiness else "WINDOWS_VALIDATED"),
            "window_config": config,
            "validation": self._window_payload(validation),
            "readiness": readiness,
            "first_blocker": None if readiness or snapshot["evidence_class"] == "SYNTHETIC_TEST_EVIDENCE" else "BROKER_RECONCILIATION_PENDING",
        }

    def workspace(self) -> dict[str, Any]:
        latest = latest_snapshot(path=self.database_path)
        snapshot_payload = None
        windows_payload = None
        readiness_payload = None
        if latest is not None:
            latest_artifact = verify_snapshot_file(self.snapshot_root, latest["snapshot_sha256"])
            self._verify_snapshot_ledger(latest, artifact_path=latest_artifact)
            recomputed = audit_training_csv(latest_artifact.read_bytes(), timezone_provenance=latest["timezone_provenance"])
            if report_payload(recomputed) != latest["dq"]:
                raise OnnxDataSourceError("SNAPSHOT_AUDIT_EVIDENCE_MISMATCH", "Latest snapshot no longer matches its persisted DQ evidence.")
            snapshot_payload = self._public_snapshot(latest)
            windows_payload = latest_window_config(latest["snapshot_id"], path=self.database_path)
            readiness_payload = readiness_for_snapshot(latest["snapshot_id"], path=self.database_path)
            if windows_payload is not None:
                self._verify_window_config(
                    windows_payload,
                    snapshot_id=latest["snapshot_id"],
                    snapshot_sha256=latest["snapshot_sha256"],
                )
                if windows_payload["snapshot_sha256"] != latest["snapshot_sha256"]:
                    raise OnnxDataSourceError("WINDOW_SNAPSHOT_HASH_MISMATCH", "Persisted windows refer to a different snapshot hash.")
                window_report = validate_research_windows(
                    recomputed,
                    windows_payload["windows"],
                    timezone_provenance=windows_payload["timezone_provenance"],
                )
                if window_report.status != "PASS" or self._window_payload(window_report) != windows_payload["validation"]:
                    raise OnnxDataSourceError("WINDOW_EVIDENCE_MISMATCH", "Persisted window validation does not reproduce from the immutable snapshot.")
            if readiness_payload is not None:
                expected = hashlib.sha256("|".join((latest["snapshot_id"], latest["snapshot_sha256"], windows_payload["window_config_id"] if windows_payload else "")).encode("utf-8")).hexdigest()
                if (
                    windows_payload is None
                    or latest["evidence_class"] == "SYNTHETIC_TEST_EVIDENCE"
                    or latest["broker_reconciliation_status"] not in {"BROKER_CONFIRMED_NOT_MISSING", "REPAIR_VERIFIED"}
                    or readiness_payload["readiness_id"] != "READY-" + expected
                    or readiness_payload["snapshot_id"] != latest["snapshot_id"]
                    or readiness_payload["snapshot_sha256"] != latest["snapshot_sha256"]
                    or readiness_payload["window_config_id"] != windows_payload["window_config_id"]
                    or readiness_payload["evidence"] != {
                        "status": "DATA_READY",
                        "snapshot_id": latest["snapshot_id"],
                        "snapshot_sha256": latest["snapshot_sha256"],
                        "window_config_id": windows_payload["window_config_id"],
                        "dq_status": latest["dq_status"],
                        "timestamp_discontinuity_status": latest["timestamp_discontinuity_status"],
                        "broker_reconciliation_status": latest["broker_reconciliation_status"],
                        "evidence_class": latest["evidence_class"],
                        "owner_runtime_execution": "NOT_PERFORMED_BY_SOURCE_CI",
                    }
                ):
                    raise OnnxDataSourceError("DATA_READY_EVIDENCE_INVALID", "Persisted DATA_READY evidence does not match the verified snapshot/window lineage.")
        snapshots = recent_snapshots(path=self.database_path)
        for item in snapshots:
            item_artifact = verify_snapshot_file(self.snapshot_root, item["snapshot_sha256"])
            self._verify_snapshot_ledger(item, artifact_path=item_artifact)
            item_report = audit_training_csv(item_artifact.read_bytes(), timezone_provenance=item["timezone_provenance"])
            if report_payload(item_report) != item["dq"]:
                raise OnnxDataSourceError("SNAPSHOT_AUDIT_EVIDENCE_MISMATCH", "Snapshot history contains content that does not reproduce its persisted DQ evidence.")
        status = "NOT_STARTED" if latest is None else "SNAPSHOT_AUDITED"
        if readiness_payload is not None:
            status = "DATA_READY"
        elif latest is not None and latest["evidence_class"] == "SYNTHETIC_TEST_EVIDENCE":
            status = "SYNTHETIC_TEST_EVIDENCE"
        elif latest is not None and latest["dq_status"] == "BLOCKED":
            status = "DQ_BLOCKED"
        elif latest is not None and windows_payload is not None:
            status = "WINDOWS_VALIDATED"
        return {
            "contract_version": "2.0",
            "source": "BACKEND_ONNX_02_DATA_API",
            "status": status,
            "implementation_status": "IMPLEMENTED",
            "real_data_readiness": "NOT_PROVEN" if readiness_payload is None else "DATA_READY",
            "latest_snapshot": snapshot_payload,
            "window_config": windows_payload,
            "readiness_evidence": readiness_payload,
            "snapshots": [self._public_snapshot(item) for item in snapshots],
            "scientific_execution": "NOT_IMPLEMENTED",
            "first_blocker": self._first_blocker(latest, windows_payload, readiness_payload),
        }

    def _publish_and_persist(
        self,
        capture: SourceCapture,
        report,
        authority: dict[str, str],
        *,
        parent_snapshot_id: str | None = None,
        correction: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        evidence_class = "SYNTHETIC_TEST_EVIDENCE" if self.synthetic_test_evidence else "OWNER_SELECTED_SOURCE_NOT_EXECUTED_IN_SOURCE_CI"
        snapshot = publish_snapshot(
            capture,
            report,
            snapshot_root=self.snapshot_root,
            authority=authority,
            parent_snapshot_id=parent_snapshot_id,
            correction=correction,
            evidence_class=evidence_class,
        )
        stored = persist_snapshot(snapshot, authority=authority, path=self.database_path)
        verify_snapshot_file(self.snapshot_root, snapshot.sha256)
        return {
            "contract_version": "2.0",
            "source": "BACKEND_ONNX_02_DATA_API",
            "status": "SYNTHETIC_TEST_EVIDENCE" if evidence_class == "SYNTHETIC_TEST_EVIDENCE" else ("SNAPSHOT_AUDITED" if report.status == "PASS" else "DQ_BLOCKED"),
            "snapshot": self._public_snapshot(stored),
            "data_quality": stored["dq"],
            "first_blocker": next((issue.code for issue in report.issues if issue.severity == "BLOCKER"), None),
            "readiness": None,
        }

    def _verified_snapshot(self, snapshot_id: str, snapshot_sha256: str) -> dict[str, Any]:
        if not snapshot_id.startswith("SNP-") or snapshot_id[4:] != snapshot_sha256:
            raise ValueError("SNAPSHOT_IDENTITY_MISMATCH")
        snapshot = get_snapshot(snapshot_id, path=self.database_path)
        if snapshot is None or snapshot["snapshot_sha256"] != snapshot_sha256:
            raise OnnxDataSourceError("SNAPSHOT_NOT_FOUND", "Snapshot identity was not found in the immutable ledger.", status_code=404)
        artifact = verify_snapshot_file(self.snapshot_root, snapshot_sha256)
        self._verify_snapshot_ledger(snapshot, artifact_path=artifact)
        return snapshot

    @staticmethod
    def _verify_snapshot_ledger(snapshot: dict[str, Any], *, artifact_path: Path) -> None:
        """Reject database metadata that does not reproduce from immutable bytes/DQ evidence."""
        dq = snapshot.get("dq")
        if not isinstance(dq, dict):
            raise OnnxDataSourceError("SNAPSHOT_LEDGER_BINDING_MISMATCH", "Persisted snapshot data-quality evidence is malformed.")
        bindings = {
            "dq_status": "status",
            "row_count": "row_count",
            "symbol": "symbol",
            "timeframe": "timeframe",
            "period_enum": "period_enum",
            "timestamp_timezone": "timestamp_timezone",
            "timezone_provenance": "timezone_provenance",
            "timestamp_min": "timestamp_min",
            "timestamp_max": "timestamp_max",
            "timestamp_discontinuity_status": "timestamp_discontinuity_status",
            "broker_reconciliation_status": "broker_reconciliation_status",
        }
        if any(snapshot.get(column) != dq.get(field) for column, field in bindings.items()):
            raise OnnxDataSourceError("SNAPSHOT_LEDGER_BINDING_MISMATCH", "Persisted snapshot metadata disagrees with its reproducible data-quality report.")
        digest = snapshot.get("snapshot_sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or snapshot.get("snapshot_id") != f"SNP-{digest}"
            or snapshot.get("dataset_id") != "DS-" + hashlib.sha256(
                "|".join((
                    str(snapshot.get("strategy_contract")),
                    str(snapshot.get("feature_contract")),
                    str(dq.get("symbol") or "UNKNOWN"),
                    str(dq.get("timeframe") or "UNKNOWN"),
                )).encode("utf-8")
            ).hexdigest()
            or snapshot.get("schema_id") != DATASET_SCHEMA_ID
            or snapshot.get("schema_version") != DATASET_SCHEMA_VERSION
            or snapshot.get("strategy_contract") != STRATEGY_CONTRACT
            or snapshot.get("feature_contract") != FEATURE_CONTRACT
            or snapshot.get("source_size_bytes") != artifact_path.stat().st_size
        ):
            raise OnnxDataSourceError("SNAPSHOT_LEDGER_BINDING_MISMATCH", "Persisted snapshot identity or schema metadata does not match the immutable artifact contract.")
        evidence = snapshot.get("creation_evidence")
        correction = snapshot.get("correction")
        expected_source_hash = correction.get("before_sha256") if isinstance(correction, dict) else digest
        if evidence != {
            "capture_protocol": "EA_EXCLUSIVE_LOCK_AND_READ_ONLY_SOURCE_HANDLE",
            "source_sha256": expected_source_hash,
            "snapshot_sha256": digest,
            "writer_lock": "ACQUIRED",
            "atomic_publication": "SAME_DIRECTORY_RENAME",
            "readback_hash_verified": True,
        }:
            raise OnnxDataSourceError("SNAPSHOT_LEDGER_BINDING_MISMATCH", "Persisted snapshot creation evidence does not match its content identity.")

    def snapshot_details(self, snapshot_id: str) -> dict[str, Any]:
        snapshot = get_snapshot(snapshot_id, path=self.database_path)
        if snapshot is None:
            raise OnnxDataSourceError("SNAPSHOT_NOT_FOUND", "Snapshot identity was not found in the immutable ledger.", status_code=404)
        artifact = verify_snapshot_file(self.snapshot_root, snapshot["snapshot_sha256"])
        self._verify_snapshot_ledger(snapshot, artifact_path=artifact)
        report = audit_training_csv(artifact.read_bytes(), timezone_provenance=snapshot["timezone_provenance"])
        if report_payload(report) != snapshot["dq"]:
            raise OnnxDataSourceError("SNAPSHOT_AUDIT_EVIDENCE_MISMATCH", "Snapshot content does not reproduce its persisted data-quality report.")
        return {
            "contract_version": "2.0",
            "source": "BACKEND_ONNX_02_DATA_API",
            "status": "SYNTHETIC_TEST_EVIDENCE" if snapshot["evidence_class"] == "SYNTHETIC_TEST_EVIDENCE" else ("SNAPSHOT_AUDITED" if snapshot["dq_status"] == "PASS" else "DQ_BLOCKED"),
            "snapshot": self._public_snapshot(snapshot),
            "data_quality": snapshot["dq"],
            "first_blocker": next((
                issue["code"] for issue in snapshot["dq"].get("issues", [])
                if issue.get("severity") == "BLOCKER"
            ), None),
        }

    @staticmethod
    def _public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "snapshot_id": snapshot["snapshot_id"],
            "dataset_id": snapshot["dataset_id"],
            "sha256": snapshot["snapshot_sha256"],
            "parent_snapshot_id": snapshot["parent_snapshot_id"],
            "filename": Path(snapshot["source_path"]).name,
            "source_path_sha256": hashlib.sha256(snapshot["source_path"].encode("utf-8")).hexdigest(),
            "source_fingerprint": snapshot["source_fingerprint"],
            "size_bytes": snapshot["source_size_bytes"],
            "row_count": snapshot["row_count"],
            "schema_id": snapshot["schema_id"],
            "schema_version": snapshot["schema_version"],
            "strategy_contract": snapshot["strategy_contract"],
            "feature_contract": snapshot["feature_contract"],
            "ea_source_sha256": snapshot["ea_source_sha256"],
            "ea_manifest_sha256": snapshot["ea_manifest_sha256"],
            "symbol": snapshot["symbol"],
            "timeframe": snapshot["timeframe"],
            "period_enum": snapshot["period_enum"],
            "timestamp_timezone": snapshot["timestamp_timezone"],
            "timezone_provenance": snapshot["timezone_provenance"],
            "timestamp_min": snapshot["timestamp_min"],
            "timestamp_max": snapshot["timestamp_max"],
            "timestamp_discontinuity_status": snapshot["timestamp_discontinuity_status"],
            "broker_reconciliation_status": snapshot["broker_reconciliation_status"],
            "dq_status": snapshot["dq_status"],
            "dq": snapshot["dq"],
            "correction": snapshot["correction"],
            "evidence_class": snapshot["evidence_class"],
            "created_utc": snapshot["created_utc"],
        }

    @staticmethod
    def _window_payload(validation) -> dict[str, Any]:
        return {
            "status": validation.status,
            "timezone_provenance": validation.timezone_provenance,
            "windows": [
                {"name": item.name, "from": item.start, "to": item.end, "row_count": item.row_count}
                for item in validation.windows
            ],
            "issues": [asdict(item) for item in validation.issues],
        }

    @staticmethod
    def _first_blocker(snapshot, windows, readiness) -> str | None:
        if snapshot is None:
            return "NO_IMMUTABLE_SNAPSHOT"
        if snapshot["dq_status"] != "PASS":
            issues = snapshot["dq"].get("issues", [])
            return next((issue["code"] for issue in issues if issue.get("severity") == "BLOCKER"), "DATA_QUALITY_BLOCKED")
        if windows is None:
            return "THREE_WINDOWS_NOT_VALIDATED"
        if readiness is None:
            if snapshot["evidence_class"] == "SYNTHETIC_TEST_EVIDENCE":
                return "SYNTHETIC_TEST_EVIDENCE_NOT_REAL_DATA"
            return snapshot["broker_reconciliation_status"]
        return None
