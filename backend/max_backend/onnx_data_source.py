"""Safe access and immutable publication for the MAX EA Common Files CSV."""

from __future__ import annotations

import ctypes
import hashlib
import json
import msvcrt
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator
from ctypes import wintypes

from .onnx_data_contract import (
    DATASET_SCHEMA_ID,
    DATASET_SCHEMA_VERSION,
    FEATURE_CONTRACT,
    STRATEGY_CONTRACT,
    DataQualityReport,
    audit_training_csv,
)


CANONICAL_FILENAME = "Max_MTF_Training.csv"
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_LOCKED_CAPTURE_SECONDS = 1.0
CAPTURE_CHUNK_BYTES = 1024 * 1024
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_ATTRIBUTE_TAG_INFO = 9
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class OnnxDataSourceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class SourceFingerprint:
    file_id: str
    size_bytes: int
    modified_ns: str
    created_ns: str


@dataclass(frozen=True)
class SourceCapture:
    path: Path
    filename: str
    sha256: str
    raw: bytes
    fingerprint: SourceFingerprint


@dataclass(frozen=True)
class PublishedSnapshot:
    snapshot_id: str
    dataset_id: str
    sha256: str
    size_bytes: int
    path: Path
    source_path: str
    source_fingerprint: SourceFingerprint
    report: DataQualityReport
    parent_snapshot_id: str | None = None
    correction: dict[str, object] | None = None
    evidence_class: str = "OWNER_SELECTED_SOURCE_NOT_EXECUTED_IN_SOURCE_CI"


class _FILE_ATTRIBUTE_TAG_INFO_STRUCT(ctypes.Structure):
    _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]


def _kernel32():
    if os.name != "nt":
        raise OnnxDataSourceError("WINDOWS_ONLY_SOURCE_ACCESS", "MAX Common Files snapshot access is supported only on Windows.", status_code=501)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    return kernel32


def _raise_win_error(code: str, message: str, *, lock: bool = False) -> None:
    error = ctypes.get_last_error()
    if lock and error in (32, 33):
        raise OnnxDataSourceError("WRITER_ACTIVE", "The MAX training writer currently holds its exclusive .lock file.")
    if lock and error == 2:
        raise OnnxDataSourceError("WRITER_LOCK_MISSING", "The EA writer lock file is absent; source consistency cannot be proven.")
    raise OnnxDataSourceError(code, f"{message} (Windows error {error}).")


def _has_reparse_attribute(handle: int) -> bool:
    info = _FILE_ATTRIBUTE_TAG_INFO_STRUCT()
    ok = _kernel32().GetFileInformationByHandleEx(
        wintypes.HANDLE(handle), _FILE_ATTRIBUTE_TAG_INFO, ctypes.byref(info), ctypes.sizeof(info)
    )
    if not ok:
        _raise_win_error("FILE_IDENTITY_UNVERIFIED", "Unable to inspect file attributes")
    return bool(info.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _path_has_reparse_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as exc:
            if isinstance(exc, FileNotFoundError):
                return False
            raise OnnxDataSourceError("SOURCE_PATH_INACCESSIBLE", "A source path component cannot be inspected.", status_code=404) from exc
        attributes = getattr(metadata, "st_file_attributes", 0)
        if current.is_symlink() or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            return True
    return False


def resolve_training_source(
    source_path: str | None = None,
    *,
    common_files_root: Path | None = None,
) -> Path:
    if common_files_root is None:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise OnnxDataSourceError("COMMON_FILES_ROOT_UNAVAILABLE", "Windows APPDATA is unavailable; MAX Common Files cannot be resolved.", status_code=503)
        common_files_root = Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"
    if not common_files_root.is_absolute():
        raise OnnxDataSourceError("COMMON_FILES_ROOT_INVALID", "MAX Common Files root must be absolute.", status_code=400)
    if _path_has_reparse_component(common_files_root):
        raise OnnxDataSourceError("COMMON_FILES_ROOT_UNSAFE", "MAX Common Files root contains a link or reparse point.")
    try:
        root = common_files_root.resolve(strict=True)
    except OSError as exc:
        raise OnnxDataSourceError("COMMON_FILES_ROOT_UNAVAILABLE", "MAX Common Files directory is missing or inaccessible.", status_code=404) from exc
    if not root.is_dir():
        raise OnnxDataSourceError("COMMON_FILES_ROOT_UNSAFE", "MAX Common Files root must be a real directory without links or reparse points.")
    if source_path is None or not source_path.strip():
        candidate = root / CANONICAL_FILENAME
    else:
        candidate = Path(source_path.strip())
        if not candidate.is_absolute():
            raise OnnxDataSourceError("SOURCE_PATH_MUST_BE_ABSOLUTE", "Owner path override must be an absolute file path.", status_code=400)
    lexical_candidate = Path(os.path.abspath(candidate))
    try:
        lexical_candidate.relative_to(root)
    except ValueError as exc:
        raise OnnxDataSourceError("SOURCE_PATH_OUTSIDE_COMMON_FILES", "Training CSV must be inside the approved MAX Common Files directory.", status_code=400) from exc
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except FileNotFoundError as exc:
        raise OnnxDataSourceError("SOURCE_MISSING", "The selected MAX training CSV does not exist.", status_code=404) from exc
    except PermissionError as exc:
        raise OnnxDataSourceError("SOURCE_INACCESSIBLE", "The selected MAX training CSV cannot be accessed with current filesystem permissions.", status_code=403) from exc
    except ValueError as exc:
        raise OnnxDataSourceError("SOURCE_PATH_OUTSIDE_COMMON_FILES", "Training CSV must be inside the approved MAX Common Files directory.", status_code=400) from exc
    except OSError as exc:
        raise OnnxDataSourceError("SOURCE_INACCESSIBLE", "The selected MAX training CSV path cannot be inspected.", status_code=503) from exc
    if _path_has_reparse_component(candidate):
        raise OnnxDataSourceError("SOURCE_PATH_UNSAFE", "Training CSV path contains a link or reparse point.", status_code=400)
    if not _SAFE_FILENAME.fullmatch(resolved.name) or not resolved.is_file():
        raise OnnxDataSourceError("SOURCE_PATH_UNSAFE", "Training CSV must be a regular, link-free file with a supported filename.", status_code=400)
    return resolved


@contextmanager
def _exclusive_writer_lock(lock_path: Path) -> Iterator[None]:
    kernel32 = _kernel32()
    handle = kernel32.CreateFileW(
        str(lock_path), _GENERIC_READ, 0, None,
        _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT, None,
    )
    handle_value = ctypes.cast(handle, ctypes.c_void_p).value
    if handle_value in (None, _INVALID_HANDLE_VALUE):
        _raise_win_error("WRITER_LOCK_UNAVAILABLE", "Unable to acquire the EA writer lock", lock=True)
    try:
        if _has_reparse_attribute(handle_value):
            raise OnnxDataSourceError("WRITER_LOCK_UNSAFE", "EA writer lock is a link or reparse point.")
        yield
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle_value))


def _read_source_locked(path: Path) -> SourceCapture:
    lock_path = path.with_name(path.name + ".lock")
    if not lock_path.is_file() or _path_has_reparse_component(lock_path):
        raise OnnxDataSourceError("WRITER_LOCK_MISSING", "The EA writer lock file is missing or unsafe; a stable read cannot be proven.")
    with _exclusive_writer_lock(lock_path):
        kernel32 = _kernel32()
        handle = kernel32.CreateFileW(
            str(path), _GENERIC_READ, _FILE_SHARE_READ, None,
            _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT, None,
        )
        handle_value = ctypes.cast(handle, ctypes.c_void_p).value
        if handle_value in (None, _INVALID_HANDLE_VALUE):
            _raise_win_error("SOURCE_OPEN_FAILED", "Unable to open the training CSV read-only")
        if _has_reparse_attribute(handle_value):
            kernel32.CloseHandle(wintypes.HANDLE(handle_value))
            raise OnnxDataSourceError("SOURCE_PATH_UNSAFE", "Training CSV is a link or reparse point.", status_code=400)
        descriptor = msvcrt.open_osfhandle(handle_value, os.O_RDONLY | os.O_BINARY)
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            before = os.fstat(source.fileno())
            if before.st_size > MAX_SOURCE_BYTES:
                raise OnnxDataSourceError("SOURCE_SIZE_UNSUPPORTED", f"CSV exceeds the supported {MAX_SOURCE_BYTES}-byte safe-audit bound.", status_code=413)
            capture_started = time.monotonic()
            chunks: list[bytes] = []
            captured_size = 0
            while captured_size <= MAX_SOURCE_BYTES:
                if time.monotonic() - capture_started > MAX_LOCKED_CAPTURE_SECONDS:
                    raise OnnxDataSourceError(
                        "SOURCE_CAPTURE_LOCK_BUDGET_EXCEEDED",
                        "Training CSV could not be copied within the EA writer-lock safety budget; no snapshot was published.",
                    )
                chunk = source.read(min(CAPTURE_CHUNK_BYTES, MAX_SOURCE_BYTES + 1 - captured_size))
                if not chunk:
                    break
                chunks.append(chunk)
                captured_size += len(chunk)
            raw = b"".join(chunks)
            after = os.fstat(source.fileno())
        current = path.stat()
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        identity_current = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
        if len(raw) != before.st_size or identity_before != identity_after or identity_before != identity_current:
            raise OnnxDataSourceError("SOURCE_UNSTABLE", "Training CSV identity or size changed during the locked read; no snapshot was published.")
        if len(raw) > MAX_SOURCE_BYTES:
            raise OnnxDataSourceError("SOURCE_SIZE_UNSUPPORTED", f"CSV exceeds the supported {MAX_SOURCE_BYTES}-byte safe-audit bound.", status_code=413)
        fingerprint = SourceFingerprint(
            file_id=f"{before.st_dev}:{before.st_ino}",
            size_bytes=before.st_size,
            modified_ns=str(before.st_mtime_ns),
            created_ns=str(getattr(before, "st_birthtime_ns", 0)),
        )
        return SourceCapture(path, path.name, hashlib.sha256(raw).hexdigest(), raw, fingerprint)


def capture_training_source(
    source_path: str | None = None,
    *,
    common_files_root: Path | None = None,
    timezone_provenance: str | None,
) -> tuple[SourceCapture, DataQualityReport]:
    path = resolve_training_source(source_path, common_files_root=common_files_root)
    capture = _read_source_locked(path)
    report = audit_training_csv(capture.raw, timezone_provenance=timezone_provenance)
    return capture, report


def verify_ea_authority(manifest_path: Path, source_path: Path) -> dict[str, str]:
    try:
        raw_manifest = manifest_path.read_bytes()
        manifest = json.loads(raw_manifest.decode("utf-8"))
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OnnxDataSourceError("EA_AUTHORITY_UNAVAILABLE", "Accepted EA source manifest or source file is unreadable.", status_code=503) from exc
    if not isinstance(manifest, dict) or (
        manifest.get("strategy_contract") != STRATEGY_CONTRACT
        or manifest.get("feature_contract") != FEATURE_CONTRACT
        or manifest.get("source_sha256") != source_hash
        or manifest.get("snapshot_sha256") != source_hash
    ):
        raise OnnxDataSourceError("EA_AUTHORITY_MISMATCH", "EA source manifest does not match the current strategy/CP32 authority and source hash.")
    return {
        "strategy_contract": STRATEGY_CONTRACT,
        "feature_contract": FEATURE_CONTRACT,
        "schema_id": DATASET_SCHEMA_ID,
        "schema_version": DATASET_SCHEMA_VERSION,
        "ea_source_sha256": source_hash,
        "ea_manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
    }


def publish_snapshot(
    capture: SourceCapture,
    report: DataQualityReport,
    *,
    snapshot_root: Path,
    authority: dict[str, str],
    parent_snapshot_id: str | None = None,
    correction: dict[str, object] | None = None,
    evidence_class: str = "OWNER_SELECTED_SOURCE_NOT_EXECUTED_IN_SOURCE_CI",
) -> PublishedSnapshot:
    snapshot_root = snapshot_root.absolute()
    if _path_has_reparse_component(snapshot_root):
        raise OnnxDataSourceError("SNAPSHOT_ROOT_UNSAFE", "Private snapshot directory contains a link or reparse point.")
    snapshot_root.mkdir(parents=True, exist_ok=True)
    if _path_has_reparse_component(snapshot_root):
        raise OnnxDataSourceError("SNAPSHOT_ROOT_UNSAFE", "Private snapshot directory contains a link or reparse point.")
    snapshot_root = snapshot_root.resolve(strict=True)
    final_path = snapshot_root / f"{capture.sha256}.csv"
    if final_path.exists() and _path_has_reparse_component(final_path):
        raise OnnxDataSourceError("SNAPSHOT_PATH_UNSAFE", "Content-addressed snapshot path is a link or reparse point.")
    if final_path.exists():
        if hashlib.sha256(final_path.read_bytes()).hexdigest() != capture.sha256:
            raise OnnxDataSourceError("IMMUTABLE_SNAPSHOT_HASH_MISMATCH", "An existing snapshot path does not match its content-addressed hash.")
    else:
        descriptor, stage_name = tempfile.mkstemp(prefix=".snapshot-", suffix=".tmp", dir=snapshot_root)
        stage_path = Path(stage_name)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as staged:
                staged.write(capture.raw)
                staged.flush()
                os.fsync(staged.fileno())
            os.chmod(stage_path, 0o444)
            if hashlib.sha256(stage_path.read_bytes()).hexdigest() != capture.sha256:
                raise OnnxDataSourceError("SNAPSHOT_STAGE_HASH_MISMATCH", "Staged snapshot bytes differ from the locked source capture.")
            try:
                os.rename(stage_path, final_path)
            except FileExistsError:
                if hashlib.sha256(final_path.read_bytes()).hexdigest() != capture.sha256:
                    raise OnnxDataSourceError("IMMUTABLE_SNAPSHOT_HASH_MISMATCH", "Concurrent snapshot publication produced conflicting content.")
                os.chmod(stage_path, 0o666)
                stage_path.unlink()
        except Exception:
            # A leftover staging file is intentionally not exposed as a snapshot.
            raise
    os.chmod(final_path, 0o444)
    snapshot_id = f"SNP-{capture.sha256}"
    dataset_identity = "|".join((authority["strategy_contract"], authority["feature_contract"], report.symbol or "UNKNOWN", report.timeframe or "UNKNOWN"))
    dataset_id = "DS-" + hashlib.sha256(dataset_identity.encode("utf-8")).hexdigest()
    return PublishedSnapshot(
        snapshot_id=snapshot_id,
        dataset_id=dataset_id,
        sha256=capture.sha256,
        size_bytes=len(capture.raw),
        path=final_path,
        source_path=str(capture.path),
        source_fingerprint=capture.fingerprint,
        report=report,
        parent_snapshot_id=parent_snapshot_id,
        correction=correction,
        evidence_class=evidence_class,
    )


def report_payload(report: DataQualityReport) -> dict[str, object]:
    payload = asdict(report)
    payload.pop("_timestamps", None)
    payload["issues"] = [asdict(issue) for issue in report.issues]
    return payload


def verify_snapshot_file(snapshot_root: Path, sha256: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise OnnxDataSourceError("SNAPSHOT_ID_INVALID", "Snapshot hash format is invalid.", status_code=404)
    path = snapshot_root / f"{sha256}.csv"
    if _path_has_reparse_component(path):
        raise OnnxDataSourceError("SNAPSHOT_PATH_UNSAFE", "Content-addressed snapshot path is a link or reparse point.")
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise OnnxDataSourceError("SNAPSHOT_MISSING", "Immutable snapshot artifact is missing or unreadable.", status_code=404) from exc
    if digest != sha256:
        raise OnnxDataSourceError("IMMUTABLE_SNAPSHOT_HASH_MISMATCH", "Snapshot content no longer matches its persisted SHA-256.")
    return path
