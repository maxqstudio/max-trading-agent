"""Copy-only staging and explicitly authorized activation for schema-16 databases."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import (
    DatabaseSchemaGuardError,
    SCHEMA16_ARCHIVE_COMPATIBILITY,
    assert_database_schema_compatible,
    is_schema16_archive_compatible,
    open_read_only,
    verify_schema16_database_integrity,
)

STAGE_REPORT_SCHEMA = "MAX_SCHEMA16_CUTOVER_STAGE_V1"
ACTIVATION_REPORT_SCHEMA = "MAX_SCHEMA16_CUTOVER_ACTIVATION_V1"
ACTIVATION_CONFIRMATION = "ACTIVATE_VERIFIED_SCHEMA16_CANDIDATE"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stat_fingerprint(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    return (int(info.st_size), int(info.st_mtime_ns), int(info.st_ctime_ns), int(info.st_ino))


def _assert_regular_nonlink(path: Path, *, must_exist: bool) -> Path:
    candidate = Path(path)
    current = candidate.absolute()
    for ancestor in (current, *current.parents):
        is_junction = bool(getattr(ancestor, "is_junction", lambda: False)())
        if ancestor.is_symlink() or is_junction:
            raise ValueError("CUTOVER_PATH_LINK_NOT_ALLOWED")
    resolved = candidate.resolve(strict=must_exist)
    if must_exist and (not resolved.is_file() or not stat.S_ISREG(resolved.stat().st_mode)):
        raise ValueError("CUTOVER_SOURCE_NOT_REGULAR_FILE")
    return resolved


def _assert_directory_nonlink(path: Path, *, must_exist: bool) -> Path:
    candidate = Path(path)
    current = candidate.absolute()
    for ancestor in (current, *current.parents):
        is_junction = bool(getattr(ancestor, "is_junction", lambda: False)())
        if ancestor.is_symlink() or is_junction:
            raise ValueError("CUTOVER_PATH_LINK_NOT_ALLOWED")
    resolved = candidate.resolve(strict=must_exist)
    if must_exist and not resolved.is_dir():
        raise ValueError("CUTOVER_STAGE_DIRECTORY_INVALID")
    return resolved


def _sidecars(path: Path) -> dict[str, bool]:
    return {
        suffix: Path(str(path) + suffix).exists()
        for suffix in ("-wal", "-shm", "-journal")
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"blob_sha256": hashlib.sha256(value).hexdigest(), "size_bytes": len(value)}
    return value


def _logical_fingerprint(path: Path) -> dict[str, Any]:
    conn = open_read_only(path)
    try:
        objects = [
            tuple(row)
            for row in conn.execute(
                "SELECT type,name,tbl_name,COALESCE(sql,'') FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        ]
        schema_digest = hashlib.sha256(
            json.dumps(objects, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        tables = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        table_fingerprints: dict[str, str] = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            column_names = [
                str(row[1]) for row in conn.execute(f"PRAGMA table_info({quoted})")
            ]
            row_hashes: list[str] = []
            for row in conn.execute(f"SELECT * FROM {quoted}"):
                payload = [
                    _json_value(row[index]) for index in range(len(column_names))
                ]
                encoded = json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
                row_hashes.append(hashlib.sha256(encoded).hexdigest())
            table_fingerprints[table] = hashlib.sha256(
                "\n".join(sorted(row_hashes)).encode("ascii")
            ).hexdigest()
        foreign_key_errors = [
            tuple(row) for row in conn.execute("PRAGMA foreign_key_check")
        ]
        integrity = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
        return {
            "schema_sha256": schema_digest,
            "table_row_sha256": table_fingerprints,
            "foreign_key_violations": len(foreign_key_errors),
            "integrity": integrity,
        }
    finally:
        conn.close()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def stage_schema16_candidate(
    source_path: Path,
    staging_root: Path,
    *,
    source_quiesced: bool,
    source_authorization_reference: str,
) -> dict[str, Any]:
    """Stage an explicitly selected, quiesced source without changing it."""
    source = _assert_regular_nonlink(source_path, must_exist=True)
    root = _assert_directory_nonlink(staging_root, must_exist=False)
    root.mkdir(parents=True, exist_ok=True)
    stage_dir = root / f"schema16-cutover-{uuid.uuid4().hex}"
    stage_dir.mkdir(exist_ok=False)
    report_path = stage_dir / "stage-report.json"
    report: dict[str, Any] = {
        "schema": STAGE_REPORT_SCHEMA,
        "status": "STAGING",
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stage_directory": str(stage_dir),
        "source_path": str(source),
        "source_quiesced_attested": source_quiesced,
        "source_authorization_reference": source_authorization_reference,
        "compatibility_profile": SCHEMA16_ARCHIVE_COMPATIBILITY,
        "activation": "NOT_PERFORMED",
        "activation_requires_explicit_owner_authorization": True,
        "candidate_path": None,
        "rollback_path": None,
        "source_sha256": None,
        "candidate_sha256": None,
        "rollback_sha256": None,
        "logical_fingerprint": None,
        "blocker": None,
    }
    _write_json_atomic(report_path, report)

    if source_quiesced is not True:
        report.update(status="BLOCKED", blocker="SOURCE_MUST_BE_QUIESCED")
        _write_json_atomic(report_path, report)
        return report
    if (
        not isinstance(source_authorization_reference, str)
        or not source_authorization_reference.strip()
    ):
        report.update(status="BLOCKED", blocker="SOURCE_AUTHORIZATION_REFERENCE_REQUIRED")
        _write_json_atomic(report_path, report)
        return report

    sidecars = _sidecars(source)
    if any(sidecars.values()):
        report.update(status="BLOCKED", blocker="SQLITE_SIDECAR_PRESENT", sidecars=sidecars)
        _write_json_atomic(report_path, report)
        return report

    try:
        schema = assert_database_schema_compatible(source, allow_missing=False)
        if not is_schema16_archive_compatible(schema):
            report.update(status="BLOCKED", blocker="EXACT_SCHEMA16_PROFILE_REQUIRED")
            _write_json_atomic(report_path, report)
            return report
        verify_schema16_database_integrity(source)
    except DatabaseSchemaGuardError as exc:
        report.update(status="BLOCKED", blocker=exc.status)
        _write_json_atomic(report_path, report)
        return report

    source_before_hash = _sha256(source)
    source_before_stat = _stat_fingerprint(source)
    rollback_partial = stage_dir / "rollback.sqlite.partial"
    candidate_partial = stage_dir / "candidate.sqlite.partial"
    rollback_path = stage_dir / "rollback.sqlite"
    candidate_path = stage_dir / "candidate.sqlite"
    try:
        shutil.copy2(source, rollback_partial)
        if _sha256(rollback_partial) != source_before_hash:
            raise RuntimeError("CUTOVER_ROLLBACK_IMAGE_HASH_MISMATCH")
        source_conn = open_read_only(source)
        candidate_conn = sqlite3.connect(candidate_partial)
        try:
            source_conn.backup(candidate_conn)
            candidate_conn.commit()
        finally:
            candidate_conn.close()
            source_conn.close()

        source_after_hash = _sha256(source)
        source_after_stat = _stat_fingerprint(source)
        if (
            source_after_hash != source_before_hash
            or source_after_stat != source_before_stat
            or any(_sidecars(source).values())
        ):
            raise RuntimeError("CUTOVER_SOURCE_CHANGED_DURING_STAGING")
        logical_source = _logical_fingerprint(source)
        logical_candidate = _logical_fingerprint(candidate_partial)
        if logical_candidate != logical_source:
            raise RuntimeError("CUTOVER_CANDIDATE_LOGICAL_FINGERPRINT_MISMATCH")
        candidate_schema = assert_database_schema_compatible(candidate_partial, allow_missing=False)
        if not is_schema16_archive_compatible(candidate_schema):
            raise RuntimeError("CUTOVER_CANDIDATE_SCHEMA_PROFILE_MISMATCH")
        verify_schema16_database_integrity(candidate_partial)
        if any(_sidecars(candidate_partial).values()):
            raise RuntimeError("CUTOVER_CANDIDATE_SIDECAR_PRESENT")
        os.replace(rollback_partial, rollback_path)
        os.replace(candidate_partial, candidate_path)
        report.update(
            status="STAGED",
            source_sha256=source_before_hash,
            candidate_sha256=_sha256(candidate_path),
            rollback_sha256=_sha256(rollback_path),
            candidate_path=str(candidate_path),
            rollback_path=str(rollback_path),
            logical_fingerprint=logical_source,
            source_file_identity={"size_bytes": source_before_stat[0], "modified_ns": source_before_stat[1]},
        )
    except Exception as exc:
        report.update(
            status="STAGING_FAILED",
            blocker=str(exc) if isinstance(exc, RuntimeError) else "CUTOVER_STAGING_VALIDATION_FAILED",
        )
    _write_json_atomic(report_path, report)
    return report


def activation_status(stage_directory: Path, active_path: Path) -> dict[str, Any]:
    """Classify an interrupted activation without changing either database."""
    stage_dir = _assert_directory_nonlink(stage_directory, must_exist=True)
    report_path = _assert_regular_nonlink(stage_dir / "stage-report.json", must_exist=True)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    active = _assert_regular_nonlink(active_path, must_exist=True)
    if any(_sidecars(active).values()):
        return {
            "status": "ACTIVE_SQLITE_SIDECAR_PRESENT",
            "current_sha256": _sha256(active),
            "sidecars": _sidecars(active),
        }
    current_sha = _sha256(active)
    if report.get("status") != "STAGED":
        return {"status": "STAGING_NOT_READY", "current_sha256": current_sha}
    if current_sha == report.get("source_sha256"):
        rollback_report = stage_dir / "rollback-report.json"
        if rollback_report.is_file():
            return {"status": "ROLLED_BACK", "current_sha256": current_sha}
        return {"status": "NOT_ACTIVATED", "current_sha256": current_sha}
    if current_sha == report.get("candidate_sha256"):
        activation_report = stage_dir / "activation-report.json"
        if activation_report.is_file():
            recorded = json.loads(activation_report.read_text(encoding="utf-8"))
            if recorded.get("activated_sha256") == current_sha:
                return {"status": "ACTIVATED", "current_sha256": current_sha}
        return {"status": "ACTIVATED_AWAITING_REPORT", "current_sha256": current_sha}
    return {"status": "ACTIVE_DATABASE_DIVERGED", "current_sha256": current_sha}


def activate_schema16_candidate(
    stage_directory: Path,
    active_path: Path,
    *,
    confirmation: str,
    application_stopped: bool,
    authorization_reference: str,
    expected_source_sha256: str,
    expected_candidate_sha256: str,
) -> dict[str, Any]:
    """Explicitly replace a synthetic/authorized active DB from a verified staged copy."""
    stage_dir = _assert_directory_nonlink(stage_directory, must_exist=True)
    report_path = stage_dir / "stage-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "STAGED":
        raise RuntimeError("CUTOVER_CANDIDATE_NOT_STAGED")
    if confirmation != ACTIVATION_CONFIRMATION:
        raise RuntimeError("EXPLICIT_OWNER_CUTOVER_AUTHORIZATION_REQUIRED")
    if application_stopped is not True:
        raise RuntimeError("APPLICATION_MUST_BE_STOPPED_FOR_CUTOVER")
    if not authorization_reference.strip():
        raise RuntimeError("OWNER_AUTHORIZATION_REFERENCE_REQUIRED")
    active = _assert_regular_nonlink(active_path, must_exist=True)
    if active != Path(str(report.get("source_path"))).resolve(strict=True):
        raise RuntimeError("CUTOVER_ACTIVE_PATH_IDENTITY_MISMATCH")
    if (
        expected_source_sha256 != report.get("source_sha256")
        or expected_candidate_sha256 != report.get("candidate_sha256")
    ):
        raise RuntimeError("CUTOVER_AUTHORIZATION_HASH_MISMATCH")
    candidate = _assert_regular_nonlink(Path(str(report["candidate_path"])), must_exist=True)
    rollback = _assert_regular_nonlink(Path(str(report["rollback_path"])), must_exist=True)
    if candidate.parent != stage_dir or rollback.parent != stage_dir:
        raise RuntimeError("CUTOVER_ARTIFACT_PATH_OUTSIDE_STAGE")
    if _sha256(candidate) != expected_candidate_sha256:
        raise RuntimeError("CUTOVER_CANDIDATE_HASH_MISMATCH")
    if _sha256(rollback) != expected_source_sha256:
        raise RuntimeError("CUTOVER_ROLLBACK_IMAGE_HASH_MISMATCH")
    if not is_schema16_archive_compatible(
        assert_database_schema_compatible(candidate, allow_missing=False)
    ):
        raise RuntimeError("CUTOVER_CANDIDATE_SCHEMA_PROFILE_MISMATCH")
    verify_schema16_database_integrity(candidate)

    active_sha256 = _sha256(active)
    if any(_sidecars(active).values()):
        raise RuntimeError("CUTOVER_ACTIVE_SQLITE_SIDECAR_PRESENT")
    if active_sha256 == expected_candidate_sha256:
        existing_report_path = stage_dir / "activation-report.json"
        existing_report = (
            json.loads(existing_report_path.read_text(encoding="utf-8"))
            if existing_report_path.is_file()
            else None
        )
        if (
            existing_report is not None
            and existing_report.get("activated_sha256") != expected_candidate_sha256
        ):
            raise RuntimeError("CUTOVER_ACTIVATION_REPORT_CONFLICT")
        activation_report = {
            "schema": ACTIVATION_REPORT_SCHEMA,
            "status": (
                "ALREADY_ACTIVATED"
                if existing_report is not None
                else "RECOVERED_AFTER_INTERRUPTED_ACTIVATION"
            ),
            "active_path": str(active),
            "source_sha256": expected_source_sha256,
            "candidate_sha256": expected_candidate_sha256,
            "activated_sha256": active_sha256,
            "rollback_path": str(rollback),
            "rollback_sha256": _sha256(rollback),
            "authorization": {
                "confirmation": confirmation,
                "reference": authorization_reference,
                "application_stopped_attested": application_stopped,
                "basis": "EXPLICIT_OWNER_CONFIRMATION_AND_EXACT_SOURCE_CANDIDATE_HASHES",
            },
            "activated_utc": (
                existing_report.get("activated_utc")
                if existing_report is not None
                else datetime.now(timezone.utc).isoformat(timespec="seconds")
            ),
        }
        _write_json_atomic(stage_dir / "activation-report.json", activation_report)
        return activation_report
    if active_sha256 != expected_source_sha256:
        raise RuntimeError("CUTOVER_ACTIVE_SOURCE_CHANGED")

    temporary = active.with_name(active.name + f".{uuid.uuid4().hex}.cutover")
    try:
        shutil.copy2(candidate, temporary)
        if _sha256(temporary) != expected_candidate_sha256:
            raise RuntimeError("CUTOVER_ACTIVATION_COPY_HASH_MISMATCH")
        if _sha256(active) != expected_source_sha256:
            raise RuntimeError("CUTOVER_ACTIVE_SOURCE_CHANGED")
        if any(_sidecars(active).values()):
            raise RuntimeError("CUTOVER_ACTIVE_SQLITE_SIDECAR_PRESENT")
        os.replace(temporary, active)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    activated_sha = _sha256(active)
    if activated_sha != expected_candidate_sha256:
        raise RuntimeError("CUTOVER_ACTIVATION_READBACK_MISMATCH")
    activation_report = {
        "schema": ACTIVATION_REPORT_SCHEMA,
        "status": "ACTIVATED",
        "active_path": str(active),
        "source_sha256": expected_source_sha256,
        "candidate_sha256": expected_candidate_sha256,
        "activated_sha256": activated_sha,
        "rollback_path": str(rollback),
        "rollback_sha256": _sha256(rollback),
        "authorization": {
            "confirmation": confirmation,
            "reference": authorization_reference,
            "application_stopped_attested": application_stopped,
            "basis": "EXPLICIT_OWNER_CONFIRMATION_AND_EXACT_SOURCE_CANDIDATE_HASHES",
        },
        "activated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _write_json_atomic(stage_dir / "activation-report.json", activation_report)
    return activation_report


def restore_schema16_rollback_image(
    stage_directory: Path,
    active_path: Path,
    *,
    confirmation: str,
    application_stopped: bool,
    authorization_reference: str,
    expected_active_sha256: str,
    expected_rollback_sha256: str,
) -> dict[str, Any]:
    """Restore only the exact staged source image after explicit authorization."""
    if confirmation != "RESTORE_VERIFIED_SCHEMA16_ROLLBACK":
        raise RuntimeError("EXPLICIT_OWNER_ROLLBACK_AUTHORIZATION_REQUIRED")
    if application_stopped is not True:
        raise RuntimeError("APPLICATION_MUST_BE_STOPPED_FOR_ROLLBACK")
    if not authorization_reference.strip():
        raise RuntimeError("OWNER_AUTHORIZATION_REFERENCE_REQUIRED")
    stage_dir = _assert_directory_nonlink(stage_directory, must_exist=True)
    report_path = _assert_regular_nonlink(stage_dir / "stage-report.json", must_exist=True)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "STAGED":
        raise RuntimeError("CUTOVER_ROLLBACK_IMAGE_NOT_STAGED")
    active = _assert_regular_nonlink(active_path, must_exist=True)
    rollback = _assert_regular_nonlink(Path(str(report["rollback_path"])), must_exist=True)
    if rollback.parent != stage_dir:
        raise RuntimeError("CUTOVER_ARTIFACT_PATH_OUTSIDE_STAGE")
    if expected_active_sha256 != report.get("candidate_sha256"):
        raise RuntimeError("CUTOVER_ROLLBACK_ACTIVE_IDENTITY_MISMATCH")
    if expected_rollback_sha256 != report.get("source_sha256"):
        raise RuntimeError("CUTOVER_ROLLBACK_HASH_AUTHORITY_MISMATCH")
    active_sha256 = _sha256(active)
    if active_sha256 not in {expected_active_sha256, expected_rollback_sha256}:
        raise RuntimeError("CUTOVER_ACTIVE_HASH_MISMATCH")
    if _sha256(rollback) != expected_rollback_sha256:
        raise RuntimeError("CUTOVER_ROLLBACK_IMAGE_HASH_MISMATCH")
    if any(_sidecars(active).values()):
        raise RuntimeError("CUTOVER_ACTIVE_SQLITE_SIDECAR_PRESENT")

    if not is_schema16_archive_compatible(
        assert_database_schema_compatible(rollback, allow_missing=False)
    ):
        raise RuntimeError("CUTOVER_ROLLBACK_SCHEMA_PROFILE_MISMATCH")
    verify_schema16_database_integrity(rollback)

    if active_sha256 == expected_rollback_sha256:
        rollback_report = {
            "schema": ACTIVATION_REPORT_SCHEMA,
            "status": "ALREADY_ROLLED_BACK",
            "active_path": str(active),
            "previous_active_sha256": expected_active_sha256,
            "restored_sha256": expected_rollback_sha256,
            "rollback_sha256": _sha256(rollback),
            "authorization": {
                "confirmation": confirmation,
                "reference": authorization_reference,
                "application_stopped_attested": application_stopped,
            },
            "restored_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _write_json_atomic(stage_dir / "rollback-report.json", rollback_report)
        return rollback_report

    temporary = active.with_name(active.name + f".{uuid.uuid4().hex}.rollback")
    try:
        shutil.copy2(rollback, temporary)
        if _sha256(temporary) != expected_rollback_sha256:
            raise RuntimeError("CUTOVER_ROLLBACK_COPY_HASH_MISMATCH")
        if _sha256(active) != expected_active_sha256 or any(_sidecars(active).values()):
            raise RuntimeError("CUTOVER_ACTIVE_CHANGED_DURING_ROLLBACK")
        os.replace(temporary, active)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    restored_sha = _sha256(active)
    if restored_sha != expected_rollback_sha256:
        raise RuntimeError("CUTOVER_ROLLBACK_READBACK_MISMATCH")
    rollback_report = {
        "schema": ACTIVATION_REPORT_SCHEMA,
        "status": "ROLLED_BACK",
        "active_path": str(active),
        "previous_active_sha256": expected_active_sha256,
        "restored_sha256": restored_sha,
        "rollback_sha256": _sha256(rollback),
        "authorization": {
            "confirmation": confirmation,
            "reference": authorization_reference,
            "application_stopped_attested": application_stopped,
        },
        "restored_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _write_json_atomic(stage_dir / "rollback-report.json", rollback_report)
    return rollback_report
