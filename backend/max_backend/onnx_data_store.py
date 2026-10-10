"""Append-only persistence for ONNX-02 snapshot, windows, and readiness evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH
from .db import connect
from .onnx_data_contract import WindowValidation
from .onnx_data_source import PublishedSnapshot, report_payload


def migrate_onnx_data(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS onnx_data_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            snapshot_sha256 TEXT NOT NULL UNIQUE CHECK(length(snapshot_sha256)=64),
            parent_snapshot_id TEXT,
            source_path TEXT NOT NULL,
            source_fingerprint_json TEXT NOT NULL,
            source_size_bytes INTEGER NOT NULL CHECK(source_size_bytes > 0),
            row_count INTEGER NOT NULL CHECK(row_count >= 0),
            schema_id TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            strategy_contract TEXT NOT NULL,
            feature_contract TEXT NOT NULL,
            ea_source_sha256 TEXT NOT NULL,
            ea_manifest_sha256 TEXT NOT NULL,
            symbol TEXT,
            timeframe TEXT,
            period_enum INTEGER,
            timestamp_timezone TEXT NOT NULL,
            timezone_provenance TEXT,
            timestamp_min TEXT,
            timestamp_max TEXT,
            timestamp_discontinuity_status TEXT NOT NULL,
            broker_reconciliation_status TEXT NOT NULL,
            dq_status TEXT NOT NULL CHECK(dq_status IN ('PASS','BLOCKED')),
            dq_json TEXT NOT NULL,
            correction_json TEXT,
            evidence_class TEXT NOT NULL CHECK(evidence_class IN (
                'OWNER_SELECTED_SOURCE_NOT_EXECUTED_IN_SOURCE_CI',
                'SYNTHETIC_TEST_EVIDENCE'
            )),
            creation_evidence_json TEXT NOT NULL,
            created_utc TEXT NOT NULL,
            FOREIGN KEY(parent_snapshot_id) REFERENCES onnx_data_snapshots(snapshot_id)
        );
        CREATE INDEX IF NOT EXISTS ix_onnx_data_snapshots_created
        ON onnx_data_snapshots(created_utc DESC);

        CREATE TABLE IF NOT EXISTS onnx_data_window_configs (
            window_config_id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL,
            snapshot_sha256 TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision > 0),
            timezone_provenance TEXT NOT NULL,
            windows_json TEXT NOT NULL,
            validation_json TEXT NOT NULL,
            created_utc TEXT NOT NULL,
            UNIQUE(snapshot_id, revision),
            FOREIGN KEY(snapshot_id) REFERENCES onnx_data_snapshots(snapshot_id)
        );
        CREATE INDEX IF NOT EXISTS ix_onnx_data_windows_snapshot
        ON onnx_data_window_configs(snapshot_id, revision DESC);

        CREATE TABLE IF NOT EXISTS onnx_data_readiness_evidence (
            readiness_id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL,
            snapshot_sha256 TEXT NOT NULL,
            window_config_id TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_utc TEXT NOT NULL,
            UNIQUE(snapshot_id, window_config_id),
            FOREIGN KEY(snapshot_id) REFERENCES onnx_data_snapshots(snapshot_id),
            FOREIGN KEY(window_config_id) REFERENCES onnx_data_window_configs(window_config_id)
        );

        CREATE TRIGGER IF NOT EXISTS onnx_data_snapshots_no_update
        BEFORE UPDATE ON onnx_data_snapshots
        BEGIN SELECT RAISE(ABORT, 'ONNX_DATA_SNAPSHOT_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS onnx_data_snapshots_no_delete
        BEFORE DELETE ON onnx_data_snapshots
        BEGIN SELECT RAISE(ABORT, 'ONNX_DATA_SNAPSHOT_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS onnx_data_windows_no_update
        BEFORE UPDATE ON onnx_data_window_configs
        BEGIN SELECT RAISE(ABORT, 'ONNX_DATA_WINDOW_CONFIG_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS onnx_data_windows_no_delete
        BEFORE DELETE ON onnx_data_window_configs
        BEGIN SELECT RAISE(ABORT, 'ONNX_DATA_WINDOW_CONFIG_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS onnx_data_readiness_no_update
        BEFORE UPDATE ON onnx_data_readiness_evidence
        BEGIN SELECT RAISE(ABORT, 'ONNX_DATA_READINESS_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS onnx_data_readiness_no_delete
        BEFORE DELETE ON onnx_data_readiness_evidence
        BEGIN SELECT RAISE(ABORT, 'ONNX_DATA_READINESS_IMMUTABLE'); END;
        """
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _snapshot_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for field in ("source_fingerprint_json", "dq_json", "correction_json", "creation_evidence_json"):
        value = item.pop(field)
        item[field.removesuffix("_json")] = json.loads(value) if value is not None else None
    return item


def persist_snapshot(snapshot: PublishedSnapshot, *, authority: dict[str, str], path: Path = DATABASE_PATH) -> dict[str, Any]:
    report = snapshot.report
    created = _utc_now()
    fingerprint = {
        "file_id": snapshot.source_fingerprint.file_id,
        "size_bytes": snapshot.source_fingerprint.size_bytes,
        "modified_ns": snapshot.source_fingerprint.modified_ns,
        "created_ns": snapshot.source_fingerprint.created_ns,
    }
    correction = snapshot.correction
    evidence = {
        "capture_protocol": "EA_EXCLUSIVE_LOCK_AND_READ_ONLY_SOURCE_HANDLE",
        "source_sha256": snapshot.sha256 if not snapshot.parent_snapshot_id else correction.get("before_sha256"),
        "snapshot_sha256": snapshot.sha256,
        "writer_lock": "ACQUIRED",
        "atomic_publication": "SAME_DIRECTORY_RENAME",
        "readback_hash_verified": True,
    }
    values = (
        snapshot.snapshot_id, snapshot.dataset_id, snapshot.sha256, snapshot.parent_snapshot_id,
        snapshot.source_path, _canonical_json(fingerprint), snapshot.size_bytes, report.row_count,
        authority["schema_id"], authority["schema_version"], authority["strategy_contract"],
        authority["feature_contract"], authority["ea_source_sha256"], authority["ea_manifest_sha256"],
        report.symbol, report.timeframe, report.period_enum, report.timestamp_timezone,
        report.timezone_provenance, report.timestamp_min, report.timestamp_max,
        report.timestamp_discontinuity_status, report.broker_reconciliation_status,
        report.status, _canonical_json(report_payload(report)),
        _canonical_json(correction) if correction is not None else None,
        snapshot.evidence_class, _canonical_json(evidence), created,
    )
    with connect(path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO onnx_data_snapshots (
                snapshot_id,dataset_id,snapshot_sha256,parent_snapshot_id,source_path,
                source_fingerprint_json,source_size_bytes,row_count,schema_id,schema_version,
                strategy_contract,feature_contract,ea_source_sha256,ea_manifest_sha256,symbol,
                timeframe,period_enum,timestamp_timezone,timezone_provenance,timestamp_min,
                timestamp_max,timestamp_discontinuity_status,broker_reconciliation_status,
                dq_status,dq_json,correction_json,evidence_class,
                creation_evidence_json,created_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            values,
        )
        row = conn.execute("SELECT * FROM onnx_data_snapshots WHERE snapshot_id=?", (snapshot.snapshot_id,)).fetchone()
        if row is None:
            raise RuntimeError("SNAPSHOT_PERSISTENCE_IDENTITY_CONFLICT")
        expected = {
            "snapshot_id": snapshot.snapshot_id,
            "dataset_id": snapshot.dataset_id,
            "snapshot_sha256": snapshot.sha256,
            "parent_snapshot_id": snapshot.parent_snapshot_id,
            "source_path": snapshot.source_path,
            "source_fingerprint_json": _canonical_json(fingerprint),
            "source_size_bytes": snapshot.size_bytes,
            "row_count": report.row_count,
            "schema_id": authority["schema_id"],
            "schema_version": authority["schema_version"],
            "strategy_contract": authority["strategy_contract"],
            "feature_contract": authority["feature_contract"],
            "ea_source_sha256": authority["ea_source_sha256"],
            "ea_manifest_sha256": authority["ea_manifest_sha256"],
            "symbol": report.symbol,
            "timeframe": report.timeframe,
            "period_enum": report.period_enum,
            "timestamp_timezone": report.timestamp_timezone,
            "timezone_provenance": report.timezone_provenance,
            "timestamp_min": report.timestamp_min,
            "timestamp_max": report.timestamp_max,
            "timestamp_discontinuity_status": report.timestamp_discontinuity_status,
            "broker_reconciliation_status": report.broker_reconciliation_status,
            "dq_status": report.status,
            "dq_json": _canonical_json(report_payload(report)),
            "correction_json": _canonical_json(correction) if correction is not None else None,
            "evidence_class": snapshot.evidence_class,
            "creation_evidence_json": _canonical_json(evidence),
        }
        if any(row[key] != expected_value for key, expected_value in expected.items()):
            raise RuntimeError("SNAPSHOT_PERSISTENCE_IDENTITY_CONFLICT")
        return _snapshot_row(row)


def get_snapshot(snapshot_id: str, *, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM onnx_data_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    return _snapshot_row(row) if row is not None else None


def latest_snapshot(*, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM onnx_data_snapshots ORDER BY created_utc DESC,snapshot_id DESC LIMIT 1"
        ).fetchone()
    return _snapshot_row(row) if row is not None else None


def recent_snapshots(*, limit: int = 10, path: Path = DATABASE_PATH) -> list[dict[str, Any]]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM onnx_data_snapshots ORDER BY created_utc DESC,snapshot_id DESC LIMIT ?",
            (max(1, min(int(limit), 50)),),
        ).fetchall()
    return [_snapshot_row(row) for row in rows]


def save_window_config(
    *,
    snapshot_id: str,
    snapshot_sha256: str,
    timezone_provenance: str,
    windows: dict[str, dict[str, str]],
    validation: WindowValidation,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if validation.status != "PASS":
        raise ValueError("WINDOW_CONFIG_NOT_VALID")
    windows_json = _canonical_json(windows)
    validation_payload = {
        "status": validation.status,
        "timezone_provenance": validation.timezone_provenance,
        "windows": [
            {"name": item.name, "from": item.start, "to": item.end, "row_count": item.row_count}
            for item in validation.windows
        ],
        "issues": [item.__dict__ for item in validation.issues],
    }
    validation_json = _canonical_json(validation_payload)
    config_hash = hashlib.sha256("|".join((snapshot_sha256, windows_json, timezone_provenance)).encode("utf-8")).hexdigest()
    config_id = "WIN-" + config_hash
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT * FROM onnx_data_window_configs WHERE window_config_id=?", (config_id,)).fetchone()
        if existing is not None:
            expected = {
                "snapshot_id": snapshot_id,
                "snapshot_sha256": snapshot_sha256,
                "timezone_provenance": timezone_provenance,
                "windows_json": windows_json,
                "validation_json": validation_json,
            }
            if any(existing[key] != value for key, value in expected.items()):
                raise RuntimeError("WINDOW_CONFIG_PERSISTENCE_IDENTITY_CONFLICT")
            return dict(existing)
        snapshot = conn.execute("SELECT snapshot_sha256 FROM onnx_data_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        if snapshot is None or snapshot["snapshot_sha256"] != snapshot_sha256:
            raise ValueError("WINDOW_SNAPSHOT_IDENTITY_MISMATCH")
        revision = int(conn.execute(
            "SELECT COALESCE(MAX(revision),0)+1 AS next_revision FROM onnx_data_window_configs WHERE snapshot_id=?",
            (snapshot_id,),
        ).fetchone()["next_revision"])
        created = _utc_now()
        conn.execute(
            """INSERT INTO onnx_data_window_configs
            (window_config_id,snapshot_id,snapshot_sha256,revision,timezone_provenance,
             windows_json,validation_json,created_utc) VALUES (?,?,?,?,?,?,?,?)""",
            (config_id, snapshot_id, snapshot_sha256, revision, timezone_provenance, windows_json, validation_json, created),
        )
        return dict(conn.execute("SELECT * FROM onnx_data_window_configs WHERE window_config_id=?", (config_id,)).fetchone())


def latest_window_config(snapshot_id: str, *, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM onnx_data_window_configs WHERE snapshot_id=? ORDER BY revision DESC LIMIT 1",
            (snapshot_id,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["windows"] = json.loads(item.pop("windows_json"))
    item["validation"] = json.loads(item.pop("validation_json"))
    return item


def persist_data_ready_evidence(
    *,
    snapshot: dict[str, Any],
    window_config: dict[str, Any],
    evidence_class: str,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    if evidence_class == "SYNTHETIC_TEST_EVIDENCE":
        return None
    if snapshot["dq_status"] != "PASS" or snapshot["broker_reconciliation_status"] not in {
        "BROKER_CONFIRMED_NOT_MISSING",
        "REPAIR_VERIFIED",
    }:
        return None
    if snapshot["snapshot_sha256"] != window_config["snapshot_sha256"]:
        raise ValueError("DATA_READY_SNAPSHOT_HASH_MISMATCH")
    validation = window_config.get("validation")
    if validation is None:
        validation = json.loads(window_config["validation_json"])
    if validation.get("status") != "PASS":
        return None
    readiness_hash = hashlib.sha256("|".join((snapshot["snapshot_id"], snapshot["snapshot_sha256"], window_config["window_config_id"])).encode("utf-8")).hexdigest()
    readiness_id = "READY-" + readiness_hash
    evidence = {
        "status": "DATA_READY",
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "window_config_id": window_config["window_config_id"],
        "dq_status": snapshot["dq_status"],
        "timestamp_discontinuity_status": snapshot["timestamp_discontinuity_status"],
        "broker_reconciliation_status": snapshot["broker_reconciliation_status"],
        "evidence_class": evidence_class,
        "owner_runtime_execution": "NOT_PERFORMED_BY_SOURCE_CI",
    }
    with connect(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO onnx_data_readiness_evidence VALUES (?,?,?,?,?,?)",
            (readiness_id, snapshot["snapshot_id"], snapshot["snapshot_sha256"], window_config["window_config_id"], _canonical_json(evidence), _utc_now()),
        )
        row = conn.execute("SELECT * FROM onnx_data_readiness_evidence WHERE readiness_id=?", (readiness_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    expected = {
        "readiness_id": readiness_id,
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "window_config_id": window_config["window_config_id"],
        "evidence_json": _canonical_json(evidence),
    }
    if any(result[key] != value for key, value in expected.items()):
        raise RuntimeError("DATA_READY_PERSISTENCE_IDENTITY_CONFLICT")
    result["evidence"] = json.loads(result.pop("evidence_json"))
    return result


def readiness_for_snapshot(snapshot_id: str, *, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM onnx_data_readiness_evidence WHERE snapshot_id=? ORDER BY created_utc DESC LIMIT 1",
            (snapshot_id,),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["evidence"] = json.loads(result.pop("evidence_json"))
    return result
