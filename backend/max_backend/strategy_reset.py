from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH, ROOT
from .mt5 import detect_mt5
from .path_safety import assert_owned_path, file_size_tree, remove_owned_path
from .schema import CURRENT_SCHEMA_VERSION

STRATEGY_RESET_CONFIRMATION = "RESET_MAX_STRATEGY_WORKSPACE"
RECOVERY_RESET_CONFIRMATION = "BACKUP_AND_RESET_CORRUPT_MAX_STATE"
_RESET_TABLES = (
    "optimizer_jobs",
    "optimizer_rounds",
    "strategy_challengers",
    "strategy_challenger_backtests",
    "strategy_challenger_retirements",
    "strategy_promotions",
    "strategy_champions",
    "strategy_challenger_batches",
    "strategy_challenger_batch_items",
    "scientist_threads",
    "scientist_messages",
    "scientist_chat_requests",
    "artifact_registry",
)
_GENERATED_OWNER_TYPES = (
    "OPTIMIZER_JOB",
    "OPTIMIZER_FILE",
    "CHALLENGER",
    "CHALLENGER_FILE",
    "BACKTEST",
    "BACKTEST_FILE",
    "BACKTEST_RUNTIME",
    "ORPHAN_RUNTIME",
    "PROMOTION",
    "PROMOTION_RECOVERY",
    "CHAMPION",
    "CHAMPION_TENURE",
    "CHALLENGER_RETIREMENT",
)
_PROJECT_ROOTS = (
    "artifacts/optimizer",
    "artifacts/challengers",
    "artifacts/backtests",
    "artifacts/challenger_operations",
    "artifacts/strategy_history",
    "state/promotion_recovery",
    "ea/champion/current",
)
_REQUIRED_TABLES = {
    "schema_meta",
    "ea_baseline",
    *_RESET_TABLES,
}
_SETTINGS_FILES = ("settings.json", "settings.backup.json", "llm_api_key.dpapi")


class StrategyResetError(RuntimeError):
    pass


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _settings_fingerprint() -> dict[str, dict[str, Any]]:
    local_app_data = os.environ.get("LOCALAPPDATA")
    settings_root = (
        Path(local_app_data) / "MAX_REBUILD"
        if local_app_data
        else Path.home() / "AppData" / "Local" / "MAX_REBUILD"
    )
    result: dict[str, dict[str, Any]] = {}
    for name in _SETTINGS_FILES:
        path = settings_root / name
        result[name] = {
            "exists": path.is_file(),
            "sha256": _hash_file(path) if path.is_file() else None,
        }
    return result


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _count(conn: sqlite3.Connection, table: str) -> int:
    safe = '"' + table.replace('"', '""') + '"'
    return int(conn.execute(f"SELECT COUNT(*) FROM {safe}").fetchone()[0])


def _runtime_paths(data_root: Path) -> list[tuple[Path, Path]]:
    expert_root = data_root / "MQL5" / "Experts" / "MaxMTF" / "ChallengerBacktests"
    tester_root = data_root / "MQL5" / "Profiles" / "Tester"
    report_root = data_root / "reports"
    found: list[tuple[Path, Path]] = []
    if expert_root.is_dir():
        found.extend(
            (item, expert_root)
            for item in expert_root.iterdir()
            if item.name.startswith("BT-")
        )
    for base, prefixes, suffixes in (
        (tester_root, ("MaxMTF_Backtest_BT-", "MAX_M06_BT-"), (".set",)),
        (report_root, ("MaxMTF_Backtest_BT-", "MAX_M06_BT-"), (".htm", ".html")),
    ):
        if not base.is_dir():
            continue
        found.extend(
            (item, base)
            for item in base.iterdir()
            if item.is_file()
            and item.name.startswith(prefixes)
            and item.name.lower().endswith(suffixes)
        )
    return sorted(found, key=lambda pair: str(pair[0]).casefold())


def _owned_paths(root: Path) -> tuple[list[tuple[Path, Path]], dict[str, Any]]:
    candidates: list[tuple[Path, Path]] = []
    root_summary: dict[str, Any] = {}
    for relative in _PROJECT_ROOTS:
        owned_root = root / Path(relative)
        if not owned_root.exists():
            root_summary[relative] = {"objects": 0, "bytes": 0}
            continue
        children = sorted(owned_root.iterdir(), key=lambda item: item.name.casefold())
        root_summary[relative] = {
            "objects": len(children),
            "bytes": file_size_tree(owned_root),
        }
        candidates.extend((item, owned_root) for item in children)

    mt5 = detect_mt5()
    runtime: list[tuple[Path, Path]] = []
    if mt5.get("status") == "READY_EXECUTABLE_AND_DATA_ROOT":
        runtime = _runtime_paths(Path(str(mt5["data_root"])).resolve())
    candidates.extend(runtime)
    root_summary["mt5_generated_runtime"] = {
        "status": "DETECTED" if mt5.get("status") == "READY_EXECUTABLE_AND_DATA_ROOT" else "NOT_AVAILABLE",
        "objects": len(runtime),
        "bytes": sum(file_size_tree(item) for item, _ in runtime),
    }
    unique: dict[str, tuple[Path, Path]] = {}
    for item, allowed_root in candidates:
        key = os.path.normcase(str(item.absolute()))
        unique[key] = (item, allowed_root)
    return list(unique.values()), root_summary


def _read_counts(path: Path, *, root: Path) -> dict[str, Any]:
    if not path.is_file():
        raise StrategyResetError("STRATEGY_RESET_DATABASE_MISSING")
    try:
        with closing(_connect(path)) as conn:
            tables = _tables(conn)
            missing = sorted(_REQUIRED_TABLES - tables)
            if missing:
                raise StrategyResetError("STRATEGY_RESET_REQUIRED_TABLES_MISSING")
            counts = {table: _count(conn, table) for table in _RESET_TABLES}
            active = {
                "optimizer_jobs": int(conn.execute(
                    "SELECT COUNT(*) FROM optimizer_jobs WHERE active=1"
                ).fetchone()[0]),
                "backtests": int(conn.execute(
                    "SELECT COUNT(*) FROM strategy_challenger_backtests "
                    "WHERE state IN ('PREPARED','RUNNING')"
                ).fetchone()[0]),
                "promotions": int(conn.execute(
                    "SELECT COUNT(*) FROM strategy_promotions "
                    "WHERE state IN ('PREPARED','ARTIFACTS_STAGED','FILES_COMMITTED')"
                ).fetchone()[0]),
                "scientist_requests": int(conn.execute(
                    "SELECT COUNT(*) FROM scientist_chat_requests "
                    "WHERE state='CALL_IN_FLIGHT'"
                ).fetchone()[0]),
            }
            champion_count = int(conn.execute(
                "SELECT COUNT(*) FROM strategy_champions WHERE status='CURRENT'"
            ).fetchone()[0])
            generated_rows = int(conn.execute(
                "SELECT COUNT(*) FROM artifact_registry "
                "WHERE retention_class!='ACTIVE_AUTHORITY' "
                "OR upper(owner_type) IN (" + ",".join("?" for _ in _GENERATED_OWNER_TYPES) + ")",
                _GENERATED_OWNER_TYPES,
            ).fetchone()[0])
            generated_bytes = int(conn.execute(
                "SELECT COALESCE(SUM(size_bytes),0) FROM artifact_registry "
                "WHERE retention_class!='ACTIVE_AUTHORITY' "
                "OR upper(owner_type) IN (" + ",".join("?" for _ in _GENERATED_OWNER_TYPES) + ")",
                _GENERATED_OWNER_TYPES,
            ).fetchone()[0])
            baseline = conn.execute("SELECT * FROM ea_baseline WHERE id=1").fetchone()
            schema = conn.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
            baseline_summary = dict(baseline) if baseline is not None else None
            schema_version = str(schema["value"]) if schema is not None else None
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    except StrategyResetError:
        raise
    except sqlite3.Error as exc:
        raise StrategyResetError("STRATEGY_RESET_DATABASE_UNAVAILABLE") from exc

    paths, roots = _owned_paths(root)
    blockers = {
        key: value for key, value in active.items() if value
    }
    baseline_path = root / "ea" / "baseline" / "Max_MTF.mq5"
    baseline_sha = (
        str(baseline_summary.get("sha256") or "")
        if isinstance(baseline_summary, dict)
        else ""
    )
    if (
        not baseline_path.is_file()
        or not baseline_sha
        or baseline_sha.casefold() != _hash_file(baseline_path).casefold()
        or str((baseline_summary or {}).get("status") or "") != "BASELINE_NOT_CHAMPION"
    ):
        blockers["baseline_authority"] = 1
    if schema_version != str(CURRENT_SCHEMA_VERSION):
        blockers["schema_version"] = 1
    unsafe_paths = 0
    for item, allowed_root in paths:
        try:
            assert_owned_path(item.absolute(), roots=[allowed_root])
        except (OSError, RuntimeError, ValueError):
            unsafe_paths += 1
    if unsafe_paths:
        blockers["unsafe_generated_paths"] = unsafe_paths
    if integrity != "ok":
        blockers["database_integrity"] = 1
    return {
        "status": "BLOCKED" if blockers or integrity != "ok" else "READY",
        "blockers": blockers,
        "schema_version": schema_version,
        "database_integrity": integrity,
        "current_champion_count": champion_count,
        "table_counts": counts,
        "generated_artifact_rows": generated_rows,
        "generated_artifact_bytes": generated_bytes,
        "roots": roots,
        "deletion_plan": [
            "Optimizer jobs and rounds",
            "Challengers, backtests, batches, retirements and promotions",
            "Current/former Champion history and generated Champion files",
            "Scientist conversation and request history",
            "Generated Strategy artifact registry rows and owned generated roots",
            "Recognized MAX-generated MT5 runtime files only",
        ],
        "preserved": [
            "EA baseline source and manifest",
            "SQLite schema and non-generated settings/authority rows",
            "Scientist provider settings and credentials outside operational state",
            "Installed application and GitHub configuration",
        ],
        "baseline": baseline_summary,
        "_owned_paths": paths,
    }


def strategy_reset_preflight(
    *, path: Path = DATABASE_PATH, root: Path = ROOT
) -> dict[str, Any]:
    report = _read_counts(path, root=root)
    report.pop("_owned_paths", None)
    report["confirmation_required"] = STRATEGY_RESET_CONFIRMATION
    return report


def _make_database_backup(source: sqlite3.Connection, path: Path) -> dict[str, Any]:
    backup_root = path.parent / "reset_backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_root / f"strategy-workspace-{stamp}-{uuid.uuid4().hex[:8]}.sqlite"
    temporary = destination.with_suffix(".tmp.sqlite")
    target = sqlite3.connect(temporary)
    try:
        source.backup(target)
        target.commit()
        result = str(target.execute("PRAGMA integrity_check").fetchone()[0])
        if result != "ok":
            raise StrategyResetError("STRATEGY_RESET_BACKUP_INTEGRITY_FAILED")
    except Exception:
        target.close()
        temporary.unlink(missing_ok=True)
        raise
    target.close()
    os.replace(temporary, destination)
    return {
        "path": str(destination),
        "sha256": _hash_file(destination),
        "size_bytes": destination.stat().st_size,
    }


def _delete_rows(conn: sqlite3.Connection) -> None:
    for table in (
        "scientist_messages",
        "scientist_chat_requests",
        "scientist_threads",
        "strategy_challenger_backtests",
        "strategy_challenger_retirements",
        "strategy_champions",
        "strategy_promotions",
        "strategy_challengers",
        "strategy_challenger_batch_items",
        "strategy_challenger_batches",
        "optimizer_rounds",
        "optimizer_jobs",
    ):
        conn.execute(f'DELETE FROM "{table}"')
    conn.execute(
        "DELETE FROM artifact_registry WHERE retention_class!='ACTIVE_AUTHORITY' "
        "OR upper(owner_type) IN (" + ",".join("?" for _ in _GENERATED_OWNER_TYPES) + ")",
        _GENERATED_OWNER_TYPES,
    )


def reset_strategy_workspace(
    *,
    confirmed: str,
    path: Path = DATABASE_PATH,
    root: Path = ROOT,
) -> dict[str, Any]:
    if confirmed != STRATEGY_RESET_CONFIRMATION:
        raise StrategyResetError("EXPLICIT_STRATEGY_WORKSPACE_RESET_CONFIRMATION_REQUIRED")
    before = _read_counts(path, root=root)
    if before["status"] != "READY":
        raise StrategyResetError("STRATEGY_WORKSPACE_RESET_BLOCKED")
    baseline_path = root / "ea" / "baseline" / "Max_MTF.mq5"
    baseline_sha = _hash_file(baseline_path) if baseline_path.is_file() else None
    baseline_before = before["baseline"]
    settings_before = _settings_fingerprint()

    conn = _connect(path)
    try:
        version_before_backup = int(conn.execute("PRAGMA data_version").fetchone()[0])
        backup = _make_database_backup(conn, path)
        version_after_backup = int(conn.execute("PRAGMA data_version").fetchone()[0])
        conn.execute("BEGIN IMMEDIATE")
        version_at_lock = int(conn.execute("PRAGMA data_version").fetchone()[0])
        if version_before_backup != version_after_backup or version_after_backup != version_at_lock:
            raise StrategyResetError("STRATEGY_RESET_DATABASE_CHANGED_DURING_BACKUP")
        # Recheck active work under the write lock; do not reset against a stale preflight.
        locked = _read_counts(path, root=root)
        if locked["status"] != "READY":
            raise StrategyResetError("STRATEGY_WORKSPACE_RESET_BLOCKED")
        paths: list[tuple[Path, Path]] = locked["_owned_paths"]
        for item, allowed_root in paths:
            assert_owned_path(item.absolute(), roots=[allowed_root])
        removed_bytes = 0
        for item, allowed_root in paths:
            removed_bytes += remove_owned_path(item.absolute(), roots=[allowed_root])
        _delete_rows(conn)
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise StrategyResetError("STRATEGY_WORKSPACE_RESET_FOREIGN_KEY_FAILURE")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    after = _read_counts(path, root=root)
    remaining_operational = {
        table: count for table, count in after["table_counts"].items()
        if table != "artifact_registry" and count
    }
    if after["current_champion_count"] != 0 or remaining_operational:
        raise StrategyResetError("STRATEGY_WORKSPACE_RESET_FINAL_STATE_INVALID")
    if after["generated_artifact_rows"] != 0:
        raise StrategyResetError("STRATEGY_WORKSPACE_RESET_ARTIFACT_ROWS_REMAIN")
    if after["baseline"] != baseline_before:
        raise StrategyResetError("STRATEGY_WORKSPACE_RESET_BASELINE_ROW_CHANGED")
    if baseline_path.is_file() and _hash_file(baseline_path) != baseline_sha:
        raise StrategyResetError("STRATEGY_WORKSPACE_RESET_BASELINE_FILE_CHANGED")
    settings_after = _settings_fingerprint()
    if settings_after != settings_before:
        raise StrategyResetError("STRATEGY_WORKSPACE_RESET_PROVIDER_SETTINGS_CHANGED")
    return {
        "status": "RESET",
        "backup": backup,
        "removed_generated_bytes": removed_bytes,
        "schema_version": after["schema_version"],
        "post_reset_table_counts": after["table_counts"],
        "current_champion_count": after["current_champion_count"],
        "generated_artifact_rows": after["generated_artifact_rows"],
        "baseline_preserved": True,
        "provider_settings_preserved": True,
    }


def database_recovery_status(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "RECOVERY_REQUIRED", "reason": "DATABASE_FILE_MISSING"}
    try:
        with closing(_connect(path)) as conn:
            result = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            if result == "ok":
                return {"status": "READY", "reason": "DATABASE_INTEGRITY_OK"}
            return {"status": "RECOVERY_REQUIRED", "reason": "DATABASE_INTEGRITY_FAILED"}
    except sqlite3.Error:
        return {"status": "RECOVERY_REQUIRED", "reason": "DATABASE_UNOPENABLE"}


def _bootstrap_database(path: Path) -> None:
    from .champion_store import migrate_m04
    from .challenger_operations_store import migrate_m06
    from .db import ensure_baseline_registered, initialize_database
    from .scientist_store import migrate_m05
    from .workflow_store import migrate_current

    initialize_database(path)
    ensure_baseline_registered(path)
    migrate_m04(path)
    migrate_m05(path)
    migrate_m06(path)
    migrate_current(path)
    with closing(_connect(path)) as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        if version is None or int(version["value"]) != CURRENT_SCHEMA_VERSION:
            raise StrategyResetError("RECOVERY_SCHEMA_BOOTSTRAP_FAILED")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise StrategyResetError("RECOVERY_DATABASE_INTEGRITY_FAILED")


def backup_and_reset_corrupt_database(
    *,
    confirmed: str,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if confirmed != RECOVERY_RESET_CONFIRMATION:
        raise StrategyResetError("EXPLICIT_CORRUPT_STATE_RECOVERY_CONFIRMATION_REQUIRED")
    status = database_recovery_status(path=path)
    if status["status"] != "RECOVERY_REQUIRED":
        raise StrategyResetError("DATABASE_RECOVERY_NOT_REQUIRED")
    settings_before = _settings_fingerprint()
    recovery_root = path.parent / "recovery_quarantine"
    recovery_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine = recovery_root / f"max-corrupt-{stamp}-{uuid.uuid4().hex[:8]}"
    quarantine.mkdir()
    preserved: list[dict[str, Any]] = []
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if not candidate.exists():
            continue
        destination = quarantine / candidate.name
        shutil.copy2(candidate, destination)
        preserved.append({
            "path": str(destination),
            "sha256": _hash_file(destination),
            "size_bytes": destination.stat().st_size,
        })
    if path.exists():
        path.unlink()
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    try:
        _bootstrap_database(path)
    except Exception as exc:
        raise StrategyResetError("CORRUPT_STATE_RESET_BOOTSTRAP_FAILED") from exc
    if _settings_fingerprint() != settings_before:
        raise StrategyResetError("RECOVERY_PROVIDER_SETTINGS_CHANGED")
    return {
        "status": "RECOVERED_EMPTY_OPERATIONAL_STATE",
        "reason": status["reason"],
        "quarantine": str(quarantine),
        "preserved_files": preserved,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "baseline_registered": True,
        "provider_settings_preserved": True,
        "generated_strategy_state": "EMPTY",
    }
