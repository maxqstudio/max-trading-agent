from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from max_backend.config import DATABASE_PATH, EA_MANIFEST
from max_backend.ea import verify_baseline_snapshot
from max_backend.mtf_geometry import STRATEGY_CONTRACT

ACCEPTED_PREVIOUS_EPOCH_SHA = "3386bc4a58d3a805dd3a972a7188a8101ef87dd3"
CONFIRMATION = "RESET_TO_MAX_TRUE_MTF_DYNAMIC_V1"
RESET_SCHEMA = "MAX_REBUILD_M07_STRATEGY_EPOCH_RESET_V1"
ACTIVE_PROMOTION_STATES = ("PREPARED", "ARTIFACTS_STAGED", "FILES_COMMITTED")
ACTIVE_BACKTEST_STATES = ("PREPARED", "RUNNING")
ACTIVE_SCIENTIST_STATES = ("PREPARED", "CALL_IN_FLIGHT")


class ResetError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ResetError(f"INVALID_JSON:{path}") from exc
    if not isinstance(value, dict):
        raise ResetError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _count(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> int:
    return int(conn.execute(sql, args).fetchone()[0])


def _settings_paths() -> list[Path]:
    base = os.environ.get("LOCALAPPDATA")
    root = (
        Path(base) / "MAX_REBUILD"
        if base
        else Path.home() / "AppData" / "Local" / "MAX_REBUILD"
    )
    return [
        root / "settings.json",
        root / "settings.backup.json",
        root / "llm_api_key.dpapi",
    ]


def settings_fingerprint() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for path in _settings_paths():
        result[path.name] = {
            "exists": path.is_file(),
            "sha256": sha256_file(path) if path.is_file() else None,
        }
    return result


def inspect_state(database: Path) -> dict[str, Any]:
    if not database.is_file():
        raise ResetError(f"DATABASE_MISSING:{database}")
    conn = _connect(database)
    try:
        required = {
            "ea_baseline",
            "optimizer_jobs",
            "optimizer_rounds",
            "strategy_challengers",
            "strategy_challenger_backtests",
            "strategy_challenger_retirements",
            "strategy_promotions",
            "strategy_champions",
            "scientist_threads",
            "scientist_messages",
            "scientist_chat_requests",
        }
        missing = sorted(table for table in required if not _table_exists(conn, table))
        if missing:
            raise ResetError(f"RESET_REQUIRED_TABLES_MISSING:{missing}")
        baseline = conn.execute("SELECT * FROM ea_baseline WHERE id=1").fetchone()
        current = conn.execute(
            "SELECT * FROM strategy_champions WHERE status='CURRENT'"
        ).fetchone()
        return {
            "baseline": dict(baseline) if baseline else None,
            "current_champion": dict(current) if current else None,
            "counts": {
                "optimizer_jobs": _count(conn, "SELECT COUNT(*) FROM optimizer_jobs"),
                "optimizer_active": _count(
                    conn, "SELECT COUNT(*) FROM optimizer_jobs WHERE active=1"
                ),
                "challengers": _count(
                    conn, "SELECT COUNT(*) FROM strategy_challengers"
                ),
                "backtests": _count(
                    conn, "SELECT COUNT(*) FROM strategy_challenger_backtests"
                ),
                "backtests_active": _count(
                    conn,
                    "SELECT COUNT(*) FROM strategy_challenger_backtests "
                    "WHERE state IN ('PREPARED','RUNNING')",
                ),
                "retirements": _count(
                    conn, "SELECT COUNT(*) FROM strategy_challenger_retirements"
                ),
                "promotions": _count(
                    conn, "SELECT COUNT(*) FROM strategy_promotions"
                ),
                "promotions_active": _count(
                    conn,
                    "SELECT COUNT(*) FROM strategy_promotions "
                    "WHERE state IN ('PREPARED','ARTIFACTS_STAGED','FILES_COMMITTED')",
                ),
                "champions": _count(
                    conn, "SELECT COUNT(*) FROM strategy_champions"
                ),
                "scientist_threads": _count(
                    conn, "SELECT COUNT(*) FROM scientist_threads"
                ),
                "scientist_requests_active": _count(
                    conn,
                    "SELECT COUNT(*) FROM scientist_chat_requests "
                    "WHERE state IN ('PREPARED','CALL_IN_FLIGHT')",
                ),
            },
        }
    finally:
        conn.close()


def assert_quiescent(state: dict[str, Any]) -> None:
    counts = state["counts"]
    blockers = {
        "optimizer_active": counts["optimizer_active"],
        "backtests_active": counts["backtests_active"],
        "promotions_active": counts["promotions_active"],
        "scientist_requests_active": counts["scientist_requests_active"],
    }
    active = {key: value for key, value in blockers.items() if int(value) != 0}
    if active:
        raise ResetError(f"STRATEGY_EPOCH_RESET_ACTIVE_WORK_BLOCKED:{active}")


def _consistent_backup(database: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    source = _connect(database)
    target = sqlite3.connect(temporary)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()
    backup_sha = sha256_file(temporary)
    if destination.exists():
        if sha256_file(destination) != backup_sha:
            temporary.unlink(missing_ok=True)
            raise ResetError(f"BACKUP_PATH_CONFLICT:{destination}")
        temporary.unlink(missing_ok=True)
    else:
        os.replace(temporary, destination)
    return {
        "path": destination.relative_to(ROOT).as_posix()
        if destination.resolve().is_relative_to(ROOT.resolve())
        else str(destination),
        "sha256": sha256_file(destination),
        "size": destination.stat().st_size,
    }


def _exact_database_rollback_image(database: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    source_sha_before = sha256_file(database)
    shutil.copy2(database, temporary)
    source_sha_after = sha256_file(database)
    copied_sha = sha256_file(temporary)
    if not (source_sha_before == source_sha_after == copied_sha):
        temporary.unlink(missing_ok=True)
        raise ResetError("DATABASE_CHANGED_DURING_ROLLBACK_SNAPSHOT")
    os.replace(temporary, destination)
    return {"path": str(destination), "sha256": copied_sha, "size": destination.stat().st_size}


def _safe_archive_name(key: str, path: Path) -> str:
    suffix = path.suffix.lower()
    return f"{key}{suffix or '.bin'}"


def _runtime_authority_files(
    state: dict[str, Any],
    *,
    root: Path,
) -> dict[str, Path]:
    champion = state.get("current_champion")
    if not isinstance(champion, dict):
        return {}
    try:
        deployment = json.loads(str(champion.get("deployment_json") or "{}"))
    except Exception as exc:
        raise ResetError("CURRENT_CHAMPION_DEPLOYMENT_JSON_INVALID") from exc
    files: dict[str, Path] = {
        "project_champion_ea": root / "ea" / "champion" / "current" / "Max_MTF.mq5",
        "project_champion_set": root / "ea" / "champion" / "current" / "Max_MTF.set",
    }
    for key in ("mt5_source", "mt5_ex5", "tester_set"):
        raw = str(deployment.get(key) or "").strip()
        if raw:
            files[key] = Path(raw)
    return files


def archive_runtime_authority(
    state: dict[str, Any],
    *,
    root: Path,
    archive_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    archive_root.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    originals = _runtime_authority_files(state, root=root)
    for key, source in originals.items():
        record: dict[str, Any] = {
            "key": key,
            "original_path": str(source),
            "exists": source.is_file(),
            "sha256": None,
            "archive_file": None,
        }
        if source.is_file():
            digest = sha256_file(source)
            destination = archive_root / _safe_archive_name(key, source)
            shutil.copy2(source, destination)
            if sha256_file(destination) != digest:
                raise ResetError(f"RUNTIME_ARCHIVE_HASH_MISMATCH:{key}")
            record["sha256"] = digest
            record["archive_file"] = destination.name
        items.append(record)
    return items, originals


def _restore_database_backup(backup_path: Path, database: Path) -> None:
    expected_sha = sha256_file(backup_path)
    for suffix in ("-wal", "-shm"):
        Path(str(database) + suffix).unlink(missing_ok=True)
    temporary = database.with_suffix(database.suffix + ".restore-tmp")
    shutil.copy2(backup_path, temporary)
    if sha256_file(temporary) != expected_sha:
        temporary.unlink(missing_ok=True)
        raise ResetError("DATABASE_RESTORE_STAGING_HASH_MISMATCH")
    os.replace(temporary, database)
    if sha256_file(database) != expected_sha:
        raise ResetError("DATABASE_RESTORE_HASH_MISMATCH")


def _restore_runtime_files(
    records: list[dict[str, Any]],
    originals: dict[str, Path],
    *,
    archive_root: Path,
) -> None:
    for item in records:
        key = str(item["key"])
        destination = originals[key]
        if not item["exists"]:
            if destination.exists():
                destination.unlink()
            continue
        source = archive_root / str(item["archive_file"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    for item in records:
        key = str(item["key"])
        destination = originals[key]
        if not item["exists"]:
            if destination.exists():
                raise ResetError(f"RUNTIME_RESTORE_ABSENCE_MISMATCH:{key}")
            continue
        if not destination.is_file():
            raise ResetError(f"RUNTIME_RESTORE_MISSING:{key}")
        if sha256_file(destination) != str(item["sha256"]):
            raise ResetError(f"RUNTIME_RESTORE_HASH_MISMATCH:{key}")


def _unlink_runtime_file(path: Path) -> None:
    path.unlink()


def neutralize_runtime_authority(
    records: list[dict[str, Any]],
    originals: dict[str, Path],
    *,
    archive_root: Path,
) -> None:
    try:
        for item in records:
            key = str(item["key"])
            path = originals[key]
            if path.is_file():
                _unlink_runtime_file(path)
            if path.exists():
                raise ResetError(f"RUNTIME_AUTHORITY_REMOVE_FAILED:{key}")
    except Exception as exc:
        try:
            _restore_runtime_files(records, originals, archive_root=archive_root)
        except Exception as rollback_exc:
            raise ResetError(
                f"RUNTIME_NEUTRALIZATION_ROLLBACK_FAILED:{rollback_exc}"
            ) from exc
        raise


def _baseline_values(manifest: dict[str, Any]) -> tuple[Any, ...]:
    return (
        manifest["ea_version"],
        manifest["source_project"],
        manifest["source_candidate_build_id"],
        manifest["source_tree_signature"],
        manifest["source_path"],
        manifest["snapshot_relative_path"],
        manifest["snapshot_sha256"],
        "BASELINE_NOT_CHAMPION",
        manifest["imported_at_utc"],
    )


def mutate_database(database: Path, manifest: dict[str, Any]) -> None:
    conn = _connect(database)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Chat is epoch-sensitive evidence context. Provider/UI settings are
            # stored separately under LOCALAPPDATA and are deliberately untouched.
            conn.execute("DELETE FROM scientist_messages")
            conn.execute("DELETE FROM scientist_chat_requests")
            conn.execute("DELETE FROM scientist_threads")

            conn.execute("DELETE FROM strategy_challenger_retirements")
            conn.execute("DELETE FROM strategy_challenger_backtests")
            conn.execute("DELETE FROM strategy_champions")
            conn.execute("DELETE FROM strategy_promotions")
            conn.execute("DELETE FROM strategy_challengers")
            conn.execute("DELETE FROM optimizer_rounds")
            conn.execute("DELETE FROM optimizer_jobs")

            conn.execute("DELETE FROM ea_baseline")
            conn.execute(
                """
                INSERT INTO ea_baseline(
                    id,ea_version,source_project,source_candidate_build_id,
                    source_tree_signature,source_path,snapshot_path,sha256,
                    status,imported_at_utc
                ) VALUES(1,?,?,?,?,?,?,?,?,?)
                """,
                _baseline_values(manifest),
            )
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES(?,?)",
                ("strategy_epoch", STRATEGY_CONTRACT),
            )
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES(?,?)",
                ("strategy_epoch_source_sha", ACCEPTED_PREVIOUS_EPOCH_SHA),
            )
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES(?,?)",
                ("strategy_epoch_baseline_sha256", manifest["snapshot_sha256"]),
            )
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise ResetError("STRATEGY_EPOCH_RESET_FOREIGN_KEY_FAILURE")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def verify_reset(database: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    state = inspect_state(database)
    counts = state["counts"]
    for key in (
        "optimizer_jobs",
        "challengers",
        "backtests",
        "retirements",
        "promotions",
        "champions",
        "scientist_threads",
    ):
        if int(counts[key]) != 0:
            raise ResetError(f"POST_RESET_NONZERO:{key}:{counts[key]}")
    baseline = state["baseline"]
    if not isinstance(baseline, dict):
        raise ResetError("POST_RESET_BASELINE_MISSING")
    if baseline["sha256"] != manifest["snapshot_sha256"]:
        raise ResetError("POST_RESET_BASELINE_SHA_MISMATCH")
    if baseline["ea_version"] != manifest["ea_version"]:
        raise ResetError("POST_RESET_BASELINE_VERSION_MISMATCH")
    if baseline["status"] != "BASELINE_NOT_CHAMPION":
        raise ResetError("POST_RESET_BASELINE_STATUS_INVALID")
    conn = _connect(database)
    try:
        epoch = conn.execute(
            "SELECT value FROM schema_meta WHERE key='strategy_epoch'"
        ).fetchone()
    finally:
        conn.close()
    if epoch is None or epoch["value"] != STRATEGY_CONTRACT:
        raise ResetError("POST_RESET_STRATEGY_EPOCH_INVALID")
    return state


def reset_strategy_epoch(
    *,
    database: Path = DATABASE_PATH,
    root: Path = ROOT,
    evidence_root: Path | None = None,
    dry_run: bool,
    confirmation: str | None = None,
) -> dict[str, Any]:
    manifest_path = root / "ea" / "baseline" / "manifest.json"
    baseline_path = root / "ea" / "baseline" / "Max_MTF.mq5"
    manifest = _json(manifest_path)
    verified = verify_baseline_snapshot(
        ea_path=baseline_path,
        manifest_path=manifest_path,
    )
    if verified.get("strategy_contract") != STRATEGY_CONTRACT:
        raise ResetError("NEW_BASELINE_STRATEGY_CONTRACT_INVALID")

    before = inspect_state(database)
    assert_quiescent(before)
    settings_before = settings_fingerprint()
    plan = {
        "schema": RESET_SCHEMA,
        "dry_run": bool(dry_run),
        "previous_accepted_sha": ACCEPTED_PREVIOUS_EPOCH_SHA,
        "previous_baseline": before["baseline"],
        "previous_current_champion": (
            before["current_champion"]["strategy_id"]
            if before.get("current_champion")
            else None
        ),
        "previous_counts": before["counts"],
        "new_baseline": {
            "ea_version": manifest["ea_version"],
            "sha256": manifest["snapshot_sha256"],
            "status": "BASELINE_NOT_CHAMPION",
            "strategy_contract": manifest["strategy_contract"],
        },
        "provider_settings_fingerprint": settings_before,
    }
    if dry_run:
        return plan
    if confirmation != CONFIRMATION:
        raise ResetError("EXPLICIT_STRATEGY_EPOCH_RESET_CONFIRMATION_REQUIRED")

    evidence = (
        Path(evidence_root)
        if evidence_root is not None
        else root / "evidence" / "m07" / "epoch_reset"
    )
    evidence.mkdir(parents=True, exist_ok=True)
    backup_path = evidence / (
        "previous_operational_db_"
        + ACCEPTED_PREVIOUS_EPOCH_SHA[:8]
        + ".sqlite"
    )
    backup = _consistent_backup(database, backup_path)
    runtime_archive_root = evidence / "previous_champion_runtime"
    runtime_records, originals = archive_runtime_authority(
        before,
        root=root,
        archive_root=runtime_archive_root,
    )

    rollback_path = evidence / ".strategy_epoch_reset.rollback.sqlite"
    rollback = _exact_database_rollback_image(database, rollback_path)
    runtime_restore_required = False
    database_restore_required = False
    try:
        runtime_restore_required = True
        neutralize_runtime_authority(
            runtime_records,
            originals,
            archive_root=runtime_archive_root,
        )
        database_restore_required = True
        mutate_database(database, manifest)
        after = verify_reset(database, manifest)
        settings_after = settings_fingerprint()
        if settings_after != settings_before:
            raise ResetError("PROVIDER_SETTINGS_CHANGED_DURING_STRATEGY_RESET")
    except Exception as exc:
        rollback_errors: list[str] = []
        if database_restore_required:
            try:
                _restore_database_backup(rollback_path, database)
                if sha256_file(database) != str(rollback["sha256"]):
                    raise ResetError("DATABASE_ROLLBACK_SHA_MISMATCH")
            except Exception as rollback_exc:
                rollback_errors.append(f"database:{rollback_exc}")
        if runtime_restore_required:
            try:
                _restore_runtime_files(
                    runtime_records,
                    originals,
                    archive_root=runtime_archive_root,
                )
            except Exception as rollback_exc:
                rollback_errors.append(f"runtime:{rollback_exc}")
        try:
            if settings_fingerprint() != settings_before:
                raise ResetError("PROVIDER_SETTINGS_CHANGED_DURING_ROLLBACK")
        except Exception as rollback_exc:
            rollback_errors.append(f"settings:{rollback_exc}")
        if rollback_errors:
            raise ResetError(
                "STRATEGY_EPOCH_RESET_ROLLBACK_FAILED:" + "|".join(rollback_errors)
            ) from exc
        rollback_path.unlink(missing_ok=True)
        raise
    rollback_path.unlink(missing_ok=True)

    result = {
        **plan,
        "dry_run": False,
        "backup": backup,
        "runtime_authority_archive": runtime_records,
        "post_reset": {
            "baseline": after["baseline"],
            "counts": after["counts"],
            "current_champion": None,
            "provider_settings_fingerprint": settings_after,
        },
    }
    reset_manifest = evidence / "reset_manifest.json"
    reset_manifest.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    result["reset_manifest"] = (
        reset_manifest.relative_to(root).as_posix()
        if reset_manifest.resolve().is_relative_to(root.resolve())
        else str(reset_manifest)
    )
    result["reset_manifest_sha256"] = sha256_file(reset_manifest)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explicit MAX M07 Strategy epoch reset"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect/reset plan only; no files or DB are mutated.",
    )
    parser.add_argument(
        "--confirm",
        default=None,
        help=f"Required exact value for mutation: {CONFIRMATION}",
    )
    args = parser.parse_args()
    if not args.dry_run and args.confirm is None:
        raise ResetError("USE_DRY_RUN_OR_EXPLICIT_CONFIRMATION")
    result = reset_strategy_epoch(
        dry_run=bool(args.dry_run),
        confirmation=args.confirm,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ResetError as exc:
        print(f"M07 STRATEGY EPOCH RESET: FAIL: {exc}")
        raise SystemExit(1)
