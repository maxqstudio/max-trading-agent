from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .backtest_control import (
    challenger_delete_preflight,
    clean_backtest_runtime,
    delete_backtest,
    delete_challenger,
)
from .config import (
    ARTIFACT_ROOT,
    BACKTEST_ARTIFACT_ROOT,
    CHALLENGER_ARTIFACT_ROOT,
    CHALLENGER_OPERATION_ARTIFACT_ROOT,
    CHAMPION_CURRENT_ROOT,
    DATABASE_PATH,
    EA_BASELINE,
    EVIDENCE_DIR,
    LEGACY_M06_BACKTEST_EVIDENCE_ROOT,
    OPTIMIZER_ARTIFACT_ROOT,
    PROMOTION_RECOVERY_ROOT,
    ROOT,
    STRATEGY_HISTORY_ROOT,
)
from .db import connect
from .workflow_store import migrate_current
from .mt5 import detect_mt5
from .optimizer_core import sha256_file
from .path_safety import assert_owned_path, file_size_tree, remove_owned_path
from .scientist_store import purge_chat
from .optimizer_store import utc_now

RETENTION = {
    "ACTIVE_AUTHORITY",
    "USER_GENERATED",
    "TEMPORARY_RUNTIME",
    "REGENERABLE",
}
PAGE_SIZES = {25, 50, 100}
SORTS = {
    "created",
    "size",
    "type",
    "owner",
    "status",
    "retention",
    "storage",
}


def artifact_id(
    artifact_type: str,
    owner_type: str,
    owner_id: str,
    canonical_path: str,
) -> str:
    raw = "|".join(
        (artifact_type, owner_type, owner_id, canonical_path)
    ).encode("utf-8")
    return "ART-" + hashlib.sha256(raw).hexdigest()[:24]


def _storage(path: str) -> str:
    try:
        resolved = Path(path).resolve()
        return "PROJECT" if resolved.is_relative_to(ROOT.resolve()) else "MT5_RUNTIME"
    except Exception:
        return "DATABASE"


def register_artifact(
    *,
    artifact_type: str,
    producer: str,
    owner_type: str,
    owner_id: str,
    canonical_path: str | Path,
    source_type: str | None = None,
    source_id: str | None = None,
    status: str = "READY",
    in_use: bool = False,
    retention_class: str = "USER_GENERATED",
    deletable: bool = False,
    cleanable: bool = False,
    runtime_paths: list[str] | None = None,
    dependencies: list[str] | None = None,
    created_utc: str | None = None,
    sha256: str | None = None,
    size_bytes_override: int | None = None,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if retention_class not in RETENTION:
        raise ValueError("invalid artifact retention class")
    migrate_current(path)
    canonical = str(Path(canonical_path).resolve())
    aid = artifact_id(artifact_type, owner_type, owner_id, canonical)
    filesystem = Path(canonical)
    size = (
        int(size_bytes_override)
        if size_bytes_override is not None
        else file_size_tree(filesystem)
    )
    hash_value = sha256
    if hash_value is None and filesystem.is_file():
        hash_value = sha256_file(filesystem)
    now = utc_now()
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO artifact_registry(
                artifact_id,artifact_type,producer,owner_type,owner_id,
                source_type,source_id,created_utc,updated_utc,
                canonical_path,runtime_paths_json,size_bytes,sha256,status,
                in_use,retention_class,deletable,cleanable,dependencies_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                artifact_type=excluded.artifact_type,
                producer=excluded.producer,
                source_type=excluded.source_type,
                source_id=excluded.source_id,
                updated_utc=excluded.updated_utc,
                runtime_paths_json=excluded.runtime_paths_json,
                size_bytes=excluded.size_bytes,
                sha256=excluded.sha256,
                status=excluded.status,
                in_use=excluded.in_use,
                retention_class=excluded.retention_class,
                deletable=excluded.deletable,
                cleanable=excluded.cleanable,
                dependencies_json=excluded.dependencies_json
            """,
            (
                aid,
                artifact_type,
                producer,
                owner_type,
                owner_id,
                source_type,
                source_id,
                str(created_utc or now),
                now,
                canonical,
                json.dumps(runtime_paths or [], sort_keys=True),
                int(size),
                hash_value,
                status,
                1 if in_use else 0,
                retention_class,
                1 if deletable else 0,
                1 if cleanable else 0,
                json.dumps(dependencies or [], sort_keys=True),
            ),
        )
    return get_artifact(aid, path=path) or {}


def _decode(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["runtime_paths"] = json.loads(result.pop("runtime_paths_json"))
    result["dependencies"] = json.loads(result.pop("dependencies_json"))
    result["in_use"] = bool(result["in_use"])
    result["deletable"] = bool(result["deletable"])
    result["cleanable"] = bool(result["cleanable"])
    result["storage"] = _storage(str(result["canonical_path"]))
    return result


def get_artifact(
    aid: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    migrate_current(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM artifact_registry WHERE artifact_id=?",
            (str(aid),),
        ).fetchone()
    return _decode(row) if row is not None else None


def _current_champion_id(conn: Any) -> str | None:
    row = conn.execute(
        "SELECT strategy_id FROM strategy_champions WHERE status='CURRENT' LIMIT 1"
    ).fetchone()
    return str(row["strategy_id"]) if row is not None else None


def _file_artifact_type(value: Path) -> str:
    name = value.name.casefold()
    suffix = value.suffix.casefold()
    if suffix == ".xml":
        return "MT5_XML"
    if suffix == ".csv":
        return "CSV_SIDECAR"
    if suffix == ".set":
        return "TESTER_SET"
    if suffix == ".mq5":
        return "MQ5_SOURCE"
    if suffix == ".ex5":
        return "EX5_BINARY"
    if suffix in {".htm", ".html"}:
        return "MT5_REPORT"
    if suffix == ".log":
        return "COMPILE_LOG"
    if suffix == ".ini":
        return "TESTER_INI"
    if suffix == ".json":
        return "JSON_EVIDENCE"
    if name.endswith(".txt"):
        return "TEXT_EVIDENCE"
    return "GENERATED_FILE"


def _register_tree_files(
    root: Path,
    *,
    producer: str,
    owner_type: str,
    owner_id: str,
    source_type: str,
    source_id: str,
    retention_class: str,
    in_use: bool,
    created_utc: str,
    path: Path,
) -> None:
    current_ids: set[str] = set()
    if root.is_dir():
        for item in sorted(root.rglob("*")):
            if not item.is_file() or item.is_symlink():
                continue
            registered = register_artifact(
                artifact_type=_file_artifact_type(item),
                producer=producer,
                owner_type=owner_type,
                owner_id=owner_id + ":" + item.relative_to(root).as_posix(),
                source_type=source_type,
                source_id=source_id,
                canonical_path=item,
                status="READY",
                in_use=in_use,
                retention_class=retention_class,
                deletable=False,
                cleanable=False,
                created_utc=created_utc,
                path=path,
            )
            current_ids.add(str(registered["artifact_id"]))
    with connect(path) as conn:
        stale = conn.execute(
            "SELECT artifact_id FROM artifact_registry "
            "WHERE owner_type=? AND source_id=?",
            (str(owner_type), str(source_id)),
        ).fetchall()
        for row in stale:
            if str(row["artifact_id"]) not in current_ids:
                conn.execute(
                    "DELETE FROM artifact_registry WHERE artifact_id=?",
                    (str(row["artifact_id"]),),
                )


def _register_backtest_runtime_items(
    backtest_id: str,
    inventory: dict[str, Any],
    *,
    active: bool,
    created_utc: str,
    path: Path,
) -> None:
    current_ids: set[str] = set()
    for item in inventory.get("items") or []:
        if not item.get("exists"):
            continue
        registered = register_artifact(
            artifact_type=str(item["type"]),
            producer="MT5_RUNTIME",
            owner_type="BACKTEST_RUNTIME",
            owner_id=backtest_id + ":" + str(item["type"]),
            source_type="BACKTEST",
            source_id=backtest_id,
            canonical_path=str(item["path"]),
            status="ACTIVE" if active else "PRESENT",
            in_use=active,
            retention_class="TEMPORARY_RUNTIME",
            deletable=False,
            cleanable=not active,
            created_utc=created_utc,
            path=path,
        )
        current_ids.add(str(registered["artifact_id"]))
    with connect(path) as conn:
        stale = conn.execute(
            "SELECT artifact_id FROM artifact_registry "
            "WHERE owner_type='BACKTEST_RUNTIME' AND source_id=?",
            (str(backtest_id),),
        ).fetchall()
        for row in stale:
            if str(row["artifact_id"]) not in current_ids:
                conn.execute(
                    "DELETE FROM artifact_registry WHERE artifact_id=?",
                    (str(row["artifact_id"]),),
                )


def _register_db_object(
    *,
    artifact_type: str,
    producer: str,
    owner_type: str,
    owner_id: str,
    source_type: str | None = None,
    source_id: str | None = None,
    status: str = "READY",
    in_use: bool = False,
    retention_class: str = "USER_GENERATED",
    dependencies: list[str] | None = None,
    created_utc: str | None = None,
    path: Path,
) -> str:
    item = register_artifact(
        artifact_type=artifact_type,
        producer=producer,
        owner_type=owner_type,
        owner_id=owner_id,
        source_type=source_type,
        source_id=source_id,
        canonical_path=path,
        status=status,
        in_use=in_use,
        retention_class=retention_class,
        deletable=False,
        cleanable=False,
        dependencies=dependencies or [],
        created_utc=created_utc,
        size_bytes_override=0,
        path=path,
    )
    return str(item["artifact_id"])


def _reconcile_db_objects(path: Path) -> None:
    """Expose persistent database-created objects in the Artifact control plane."""
    specs: list[dict[str, Any]] = []
    with connect(path) as conn:
        for row in conn.execute(
            "SELECT job_id,round_no,phase,updated_utc,eligible_passes FROM optimizer_rounds "
            "ORDER BY job_id,round_no"
        ).fetchall():
            specs.append({
                "artifact_type": "OPTIMIZER_ROUND",
                "producer": "STRATEGY_OPTIMIZER",
                "owner_type": "OPTIMIZER_ROUND",
                "owner_id": f"{row['job_id']}:{int(row['round_no'])}",
                "source_type": "OPTIMIZER_JOB",
                "source_id": str(row["job_id"]),
                "status": str(row["phase"]),
                "created_utc": str(row["updated_utc"]),
            })
            if int(row["eligible_passes"] or 0) > 0:
                specs.append({
                    "artifact_type": "QUALIFIED_CANDIDATE_POOL",
                    "producer": "STRATEGY_OPTIMIZER",
                    "owner_type": "QUALIFIED_CANDIDATE_POOL",
                    "owner_id": f"{row['job_id']}:{int(row['round_no'])}",
                    "source_type": "OPTIMIZER_ROUND",
                    "source_id": f"{row['job_id']}:{int(row['round_no'])}",
                    "status": "OWNER_SELECTABLE",
                    "created_utc": str(row["updated_utc"]),
                    "dependencies": [str(row["job_id"])],
                })

        for row in conn.execute(
            "SELECT batch_id,job_id,state,created_utc FROM strategy_challenger_batches "
            "ORDER BY created_utc,batch_id"
        ).fetchall():
            specs.append({
                "artifact_type": "CHALLENGER_BATCH_JOURNAL",
                "producer": "OWNER_QUALIFIED_SELECTION",
                "owner_type": "CHALLENGER_BATCH",
                "owner_id": str(row["batch_id"]),
                "source_type": "OPTIMIZER_JOB",
                "source_id": str(row["job_id"]),
                "status": str(row["state"]),
                "in_use": str(row["state"]) in {"PREPARED","VALIDATED","STAGED","RECOVERY_REQUIRED"},
                "created_utc": str(row["created_utc"]),
            })
        for row in conn.execute(
            """
            SELECT i.batch_id,i.source_round,i.source_pass,i.challenger_id,i.state,
                   b.created_utc
            FROM strategy_challenger_batch_items i
            JOIN strategy_challenger_batches b ON b.batch_id=i.batch_id
            ORDER BY b.created_utc,i.source_round,i.source_pass
            """
        ).fetchall():
            specs.append({
                "artifact_type": "CHALLENGER_BATCH_ITEM",
                "producer": "OWNER_QUALIFIED_SELECTION",
                "owner_type": "CHALLENGER_BATCH_ITEM",
                "owner_id": (
                    f"{row['batch_id']}:{int(row['source_round'])}:"
                    f"{int(row['source_pass'])}"
                ),
                "source_type": "CHALLENGER_BATCH",
                "source_id": str(row["batch_id"]),
                "status": str(row["state"]),
                "created_utc": str(row["created_utc"]),
                "dependencies": [str(row["challenger_id"])],
            })

        for row in conn.execute(
            """
            SELECT retirement_id,challenger_id,state,created_utc,evidence_path
            FROM strategy_challenger_retirements
            ORDER BY created_utc,retirement_id
            """
        ).fetchall():
            specs.append({
                "artifact_type": "CHALLENGER_RETIREMENT",
                "producer": "OWNER_CHALLENGER_LIFECYCLE",
                "owner_type": "RETIREMENT",
                "owner_id": str(row["retirement_id"]),
                "source_type": "CHALLENGER",
                "source_id": str(row["challenger_id"]),
                "status": str(row["state"]),
                "in_use": str(row["state"]) == "PREPARED",
                "created_utc": str(row["created_utc"]),
            })

        for row in conn.execute(
            """
            SELECT promotion_id,challenger_id,state,created_utc,recovery_path
            FROM strategy_promotions
            ORDER BY created_utc,promotion_id
            """
        ).fetchall():
            specs.append({
                "artifact_type": "STRATEGY_PROMOTION",
                "producer": "OWNER_MANUAL_STRATEGY_PROMOTION",
                "owner_type": "PROMOTION",
                "owner_id": str(row["promotion_id"]),
                "source_type": "CHALLENGER",
                "source_id": str(row["challenger_id"]),
                "status": str(row["state"]),
                "in_use": str(row["state"]) in {"PREPARED","ARTIFACTS_STAGED","FILES_COMMITTED"},
                "retention_class": (
                    "ACTIVE_AUTHORITY" if str(row["state"]) == "COMMITTED"
                    else "USER_GENERATED"
                ),
                "created_utc": str(row["created_utc"]),
            })

        for row in conn.execute(
            """
            SELECT champion_tenure_id,strategy_id,status,source_challenger_id,
                   promotion_id,promoted_utc
            FROM strategy_champions
            ORDER BY promoted_utc,champion_tenure_id
            """
        ).fetchall():
            current = str(row["status"]) == "CURRENT"
            specs.append({
                "artifact_type": "STRATEGY_HISTORY",
                "producer": "OWNER_MANUAL_STRATEGY_PROMOTION",
                "owner_type": "STRATEGY_HISTORY",
                "owner_id": str(row["champion_tenure_id"]),
                "source_type": "PROMOTION",
                "source_id": str(row["promotion_id"]),
                "status": str(row["status"]),
                "in_use": current,
                "retention_class": "ACTIVE_AUTHORITY" if current else "USER_GENERATED",
                "created_utc": str(row["promoted_utc"]),
                "dependencies": [str(row["source_challenger_id"])],
            })

        for row in conn.execute(
            "SELECT thread_id,created_utc,updated_utc FROM scientist_threads "
            "ORDER BY created_utc,thread_id"
        ).fetchall():
            specs.append({
                "artifact_type": "SCIENTIST_THREAD",
                "producer": "SCIENTIST",
                "owner_type": "SCIENTIST_THREAD",
                "owner_id": str(row["thread_id"]),
                "status": "OPERATIONAL",
                "created_utc": str(row["created_utc"]),
            })
        for row in conn.execute(
            """
            SELECT request_id,thread_id,state,created_utc
            FROM scientist_chat_requests
            ORDER BY created_utc,request_id
            """
        ).fetchall():
            specs.append({
                "artifact_type": "SCIENTIST_REQUEST",
                "producer": "SCIENTIST",
                "owner_type": "SCIENTIST_REQUEST",
                "owner_id": str(row["request_id"]),
                "source_type": "SCIENTIST_THREAD",
                "source_id": str(row["thread_id"]),
                "status": str(row["state"]),
                "in_use": str(row["state"]) == "CALL_IN_FLIGHT",
                "created_utc": str(row["created_utc"]),
            })
        for row in conn.execute(
            """
            SELECT message_id,thread_id,role,created_utc,request_id
            FROM scientist_messages
            ORDER BY created_utc,message_id
            """
        ).fetchall():
            specs.append({
                "artifact_type": "SCIENTIST_MESSAGE",
                "producer": "SCIENTIST",
                "owner_type": "SCIENTIST_MESSAGE",
                "owner_id": str(row["message_id"]),
                "source_type": (
                    "SCIENTIST_REQUEST" if row["request_id"]
                    else "SCIENTIST_THREAD"
                ),
                "source_id": str(row["request_id"] or row["thread_id"]),
                "status": str(row["role"]).upper(),
                "created_utc": str(row["created_utc"]),
            })

    owner_types = {
        "OPTIMIZER_ROUND","QUALIFIED_CANDIDATE_POOL",
        "CHALLENGER_BATCH","CHALLENGER_BATCH_ITEM",
        "RETIREMENT","PROMOTION","STRATEGY_HISTORY",
        "SCIENTIST_THREAD","SCIENTIST_REQUEST","SCIENTIST_MESSAGE",
    }
    current_ids: set[str] = set()
    for spec in specs:
        current_ids.add(_register_db_object(path=path, **spec))
    with connect(path) as conn:
        placeholders = ",".join("?" for _ in owner_types)
        rows = conn.execute(
            f"SELECT artifact_id FROM artifact_registry "
            f"WHERE owner_type IN ({placeholders})",
            tuple(sorted(owner_types)),
        ).fetchall()
        for row in rows:
            if str(row["artifact_id"]) not in current_ids:
                conn.execute(
                    "DELETE FROM artifact_registry WHERE artifact_id=?",
                    (str(row["artifact_id"]),),
                )


def _is_protected_strategy_history_entry(entry: Path) -> bool:
    """Recognize retained baseline archive authority by its semantic schema."""
    if not entry.is_dir() or entry.is_symlink():
        return False
    marker = entry / "baseline.json"
    if not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload.get("schema") == "MAX_REBUILD_BASELINE_ARCHIVE_V1"


def _reconcile_project_objects(path: Path) -> None:
    with connect(path) as conn:
        champion_id = _current_champion_id(conn)
        jobs = conn.execute(
            """
            SELECT job_id,status,active,created_utc,evidence_dir
            FROM optimizer_jobs
            ORDER BY created_utc
            """
        ).fetchall()
        challengers = conn.execute(
            """
            SELECT challenger_id,status,role_origin,source_job_id,created_utc,bundle_path
            FROM strategy_challengers
            ORDER BY created_utc
            """
        ).fetchall()
        backtests = conn.execute(
            """
            SELECT backtest_id,challenger_id,state,created_utc,evidence_path,
                   runtime_status
            FROM strategy_challenger_backtests
            ORDER BY created_utc
            """
        ).fetchall()

    for row in jobs:
        value = (ROOT / str(row["evidence_dir"])).resolve()
        current = value.is_relative_to(OPTIMIZER_ARTIFACT_ROOT.resolve())
        with connect(path) as dep_conn:
            challenger_count = int(dep_conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_challengers WHERE source_job_id=?",
                (str(row["job_id"]),),
            ).fetchone()["n"])
        dependencies = (
            ["CHALLENGER_DEPENDENCY"] if challenger_count else []
        )
        in_use = bool(row["active"]) or bool(dependencies)
        register_artifact(
            artifact_type="OPTIMIZER_JOB",
            producer="STRATEGY_OPTIMIZER",
            owner_type="OPTIMIZER_JOB",
            owner_id=str(row["job_id"]),
            canonical_path=value,
            status=str(row["status"]),
            in_use=in_use,
            retention_class="USER_GENERATED" if current else "ACTIVE_AUTHORITY",
            deletable=current and not in_use,
            cleanable=False,
            dependencies=dependencies,
            created_utc=str(row["created_utc"]),
            size_bytes_override=0,
            path=path,
        )
        _register_tree_files(
            value,
            producer="STRATEGY_OPTIMIZER",
            owner_type="OPTIMIZER_FILE",
            owner_id=str(row["job_id"]),
            source_type="OPTIMIZER_JOB",
            source_id=str(row["job_id"]),
            retention_class="USER_GENERATED" if current else "ACTIVE_AUTHORITY",
            in_use=in_use,
            created_utc=str(row["created_utc"]),
            path=path,
        )

    for row in challengers:
        value = (ROOT / str(row["bundle_path"])).resolve()
        current = value.is_relative_to(CHALLENGER_ARTIFACT_ROOT.resolve())
        is_champion = champion_id == str(row["challenger_id"])
        blockers = []
        if current:
            try:
                blockers = challenger_delete_preflight(
                    str(row["challenger_id"]),
                    path=path,
                )["blockers"]
            except Exception as exc:
                blockers = [str(exc)]
        register_artifact(
            artifact_type="STRATEGY_CHALLENGER",
            producer="OWNER_QUALIFIED_SELECTION"
            if str(row["role_origin"]) == "OWNER_SELECTED_QUALIFIED_CANDIDATE"
            else "LEGACY_OPTIMIZER_WINNER",
            owner_type="CHALLENGER",
            owner_id=str(row["challenger_id"]),
            source_type="OPTIMIZER_JOB",
            source_id=str(row["source_job_id"]),
            canonical_path=value,
            status=str(row["status"]),
            in_use=is_champion or bool(blockers),
            retention_class=(
                "ACTIVE_AUTHORITY"
                if is_champion or not current
                else "USER_GENERATED"
            ),
            deletable=current and not blockers,
            cleanable=False,
            dependencies=blockers,
            created_utc=str(row["created_utc"]),
            size_bytes_override=0,
            path=path,
        )
        _register_tree_files(
            value,
            producer=(
                "OWNER_QUALIFIED_SELECTION"
                if str(row["role_origin"]) == "OWNER_SELECTED_QUALIFIED_CANDIDATE"
                else "LEGACY_OPTIMIZER_WINNER"
            ),
            owner_type="CHALLENGER_FILE",
            owner_id=str(row["challenger_id"]),
            source_type="CHALLENGER",
            source_id=str(row["challenger_id"]),
            retention_class=(
                "ACTIVE_AUTHORITY"
                if is_champion or not current
                else "USER_GENERATED"
            ),
            in_use=is_champion or bool(blockers),
            created_utc=str(row["created_utc"]),
            path=path,
        )

    for row in backtests:
        value = (ROOT / str(row["evidence_path"])).resolve()
        current = value.is_relative_to(BACKTEST_ARTIFACT_ROOT.resolve())
        active = str(row["state"]) in {"PREPARED", "RUNNING"}
        runtime_paths: list[str] = []
        inventory: dict[str, Any] = {"items": []}
        try:
            from .backtest_control import runtime_inventory
            inventory = runtime_inventory(str(row["backtest_id"]), path=path)
            runtime_paths = [
                str(item["path"]) for item in inventory["items"] if item["exists"]
            ]
        except Exception:
            inventory = {"items": []}
        register_artifact(
            artifact_type="CHALLENGER_BACKTEST",
            producer="MT5_STRATEGY_TESTER",
            owner_type="BACKTEST",
            owner_id=str(row["backtest_id"]),
            source_type="CHALLENGER",
            source_id=str(row["challenger_id"]),
            canonical_path=value,
            status=str(row["state"]),
            in_use=active,
            retention_class="USER_GENERATED" if current else "ACTIVE_AUTHORITY",
            deletable=current and not active,
            cleanable=bool(runtime_paths) and not active,
            runtime_paths=runtime_paths,
            created_utc=str(row["created_utc"]),
            size_bytes_override=0,
            path=path,
        )
        _register_tree_files(
            value,
            producer="MT5_STRATEGY_TESTER",
            owner_type="BACKTEST_FILE",
            owner_id=str(row["backtest_id"]),
            source_type="BACKTEST",
            source_id=str(row["backtest_id"]),
            retention_class="USER_GENERATED" if current else "ACTIVE_AUTHORITY",
            in_use=active,
            created_utc=str(row["created_utc"]),
            path=path,
        )
        _register_backtest_runtime_items(
            str(row["backtest_id"]),
            inventory,
            active=active,
            created_utc=str(row["created_utc"]),
            path=path,
        )

    # MAX-owned lifecycle/recovery files are first-class generated artifacts.
    for aux_root, producer, owner_type, source_type in (
        (
            CHALLENGER_OPERATION_ARTIFACT_ROOT,
            "CHALLENGER_LIFECYCLE",
            "CHALLENGER_OPERATION_FILE",
            "CHALLENGER_OPERATION",
        ),
        (
            PROMOTION_RECOVERY_ROOT,
            "OWNER_MANUAL_STRATEGY_PROMOTION",
            "PROMOTION_RECOVERY_FILE",
            "PROMOTION_RECOVERY",
        ),
    ):
        if not aux_root.resolve().is_relative_to(ROOT.resolve()):
            continue
        _register_tree_files(
            aux_root,
            producer=producer,
            owner_type=owner_type,
            owner_id=aux_root.name,
            source_type=source_type,
            source_id=aux_root.name,
            retention_class="USER_GENERATED",
            in_use=False,
            created_utc=utc_now(),
            path=path,
        )

    # Strategy history mixes generated Champion tenure history with retained
    # baseline archive authority. Classify each top-level object semantically
    # so global clean can never erase a protected baseline archive.
    with connect(path) as conn:
        conn.execute(
            "DELETE FROM artifact_registry WHERE owner_type='STRATEGY_HISTORY_FILE'"
        )
    if (
        STRATEGY_HISTORY_ROOT.is_dir()
        and STRATEGY_HISTORY_ROOT.resolve().is_relative_to(ROOT.resolve())
    ):
        for history_entry in sorted(
            STRATEGY_HISTORY_ROOT.iterdir(),
            key=lambda value: value.name,
        ):
            if history_entry.is_symlink():
                continue
            protected = _is_protected_strategy_history_entry(history_entry)
            retention = "ACTIVE_AUTHORITY" if protected else "USER_GENERATED"
            producer = (
                "STRATEGY_BASELINE_ARCHIVE"
                if protected
                else "OWNER_MANUAL_STRATEGY_PROMOTION"
            )
            if history_entry.is_dir():
                _register_tree_files(
                    history_entry,
                    producer=producer,
                    owner_type="STRATEGY_HISTORY_FILE",
                    owner_id=history_entry.name,
                    source_type=(
                        "STRATEGY_BASELINE"
                        if protected
                        else "STRATEGY_HISTORY"
                    ),
                    source_id=history_entry.name,
                    retention_class=retention,
                    in_use=protected,
                    created_utc=utc_now(),
                    path=path,
                )
            elif history_entry.is_file():
                register_artifact(
                    artifact_type=_file_artifact_type(history_entry),
                    producer=producer,
                    owner_type="STRATEGY_HISTORY_FILE",
                    owner_id=history_entry.name,
                    source_type=(
                        "STRATEGY_BASELINE"
                        if protected
                        else "STRATEGY_HISTORY"
                    ),
                    source_id=history_entry.name,
                    canonical_path=history_entry,
                    status="PROTECTED" if protected else "READY",
                    in_use=protected,
                    retention_class=retention,
                    deletable=False,
                    cleanable=False,
                    created_utc=utc_now(),
                    path=path,
                )

    # Source-controlled milestone evidence is visible and protected without
    # coupling normal runtime to the current milestone number.
    for evidence in sorted(
        item
        for item in EVIDENCE_DIR.glob("m[0-9][0-9]")
        if item.is_dir()
    ):
        name = evidence.name
        register_artifact(
            artifact_type="ACCEPTED_MILESTONE_EVIDENCE",
            producer="CONTROL_ROOM_ACCEPTANCE",
            owner_type="MILESTONE",
            owner_id=name.upper(),
            canonical_path=evidence,
            status="PROTECTED",
            in_use=True,
            retention_class="ACTIVE_AUTHORITY",
            deletable=False,
            cleanable=False,
            path=path,
        )
    if EA_BASELINE.is_file():
        register_artifact(
            artifact_type="BASELINE_EA",
            producer="STRATEGY_AUTHORITY",
            owner_type="STRATEGY_BASELINE",
            owner_id="Max_MTF",
            canonical_path=EA_BASELINE,
            status="ACTIVE",
            in_use=True,
            retention_class="ACTIVE_AUTHORITY",
            deletable=False,
            cleanable=False,
            path=path,
        )


def _runtime_roots(data_root: Path) -> dict[str, Path]:
    return {
        "experts": data_root / "MQL5" / "Experts" / "MaxMTF" / "ChallengerBacktests",
        "tester": data_root / "MQL5" / "Profiles" / "Tester",
        "reports": data_root / "reports",
    }


def _extract_backtest_id(name: str) -> str | None:
    candidates = (
        ("MaxMTF_Backtest_", ".set"),
        ("MAX_M06_", ".set"),
        ("MaxMTF_Backtest_", ".htm"),
        ("MaxMTF_Backtest_", ".html"),
        ("MAX_M06_", ".htm"),
        ("MAX_M06_", ".html"),
    )
    for prefix, suffix in candidates:
        if name.startswith(prefix) and name.endswith(suffix):
            value = name[len(prefix) : -len(suffix)]
            return value if value.startswith("BT-") else None
    return name if name.startswith("BT-") else None


def _reconcile_runtime(path: Path) -> dict[str, Any]:
    mt5 = detect_mt5()
    if mt5.get("status") != "READY_EXECUTABLE_AND_DATA_ROOT":
        return {
            "status": str(mt5.get("status") or "UNAVAILABLE"),
            "reason": str(mt5.get("reason") or "UNKNOWN"),
            "data_root": mt5.get("data_root"),
        }
    data_root = Path(str(mt5["data_root"])).resolve()
    roots = _runtime_roots(data_root)
    with connect(path) as conn:
        known = {
            str(row["backtest_id"])
            for row in conn.execute(
                "SELECT backtest_id FROM strategy_challenger_backtests"
            ).fetchall()
        }

    discovered: list[tuple[str, Path, str]] = []
    expert_root = roots["experts"]
    if expert_root.is_dir():
        for item in expert_root.iterdir():
            if item.is_dir() and item.name.startswith("BT-"):
                discovered.append((item.name, item, "MT5_EXPERT_RUNTIME"))
    for root_key, patterns in (
        ("tester", ("MaxMTF_Backtest_BT-*.set", "MAX_M06_BT-*.set")),
        ("reports", (
            "MaxMTF_Backtest_BT-*.htm",
            "MaxMTF_Backtest_BT-*.html",
            "MAX_M06_BT-*.htm",
            "MAX_M06_BT-*.html",
        )),
    ):
        root = roots[root_key]
        if not root.is_dir():
            continue
        for pattern in patterns:
            for item in root.glob(pattern):
                backtest_id = _extract_backtest_id(item.name)
                if backtest_id:
                    discovered.append(
                        (
                            backtest_id,
                            item,
                            "MT5_TESTER_SET" if root_key == "tester"
                            else "MT5_REPORT_RUNTIME",
                        )
                    )

    current_orphan_ids: set[str] = set()
    for backtest_id, item, artifact_type in discovered:
        if backtest_id in known:
            continue
        root = (
            roots["experts"]
            if artifact_type == "MT5_EXPERT_RUNTIME"
            else roots["tester"]
            if artifact_type == "MT5_TESTER_SET"
            else roots["reports"]
        )
        safe = assert_owned_path(item.resolve(), roots=[root])
        registered = register_artifact(
            artifact_type="ORPHAN_RUNTIME",
            producer=artifact_type,
            owner_type="ORPHAN_RUNTIME",
            owner_id=backtest_id + ":" + artifact_type,
            canonical_path=safe,
            source_type="BACKTEST",
            source_id=backtest_id,
            status="ORPHAN_RUNTIME",
            in_use=False,
            retention_class="TEMPORARY_RUNTIME",
            deletable=True,
            cleanable=True,
            path=path,
        )
        current_orphan_ids.add(str(registered["artifact_id"]))
    with connect(path) as conn:
        stale = conn.execute(
            "SELECT artifact_id FROM artifact_registry WHERE owner_type='ORPHAN_RUNTIME'"
        ).fetchall()
        for row in stale:
            if str(row["artifact_id"]) not in current_orphan_ids:
                conn.execute(
                    "DELETE FROM artifact_registry WHERE artifact_id=?",
                    (str(row["artifact_id"]),),
                )
    return {
        "status": "READY",
        "reason": "READY",
        "data_root": str(data_root),
    }


def reconcile_artifacts(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    migrate_current(path)
    _reconcile_project_objects(path)
    _reconcile_db_objects(path)
    runtime = _reconcile_runtime(path)
    return {"status": "READY", "runtime": runtime}


def artifact_page(
    *,
    query: str = "",
    artifact_type: str = "",
    producer: str = "",
    status: str = "",
    retention: str = "",
    in_use: str = "",
    storage: str = "",
    sort: str = "created",
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if sort not in SORTS:
        raise ValueError("unsupported artifact sort")
    direction = str(order).lower()
    if direction not in {"asc", "desc"}:
        raise ValueError("order must be asc or desc")
    size = int(page_size)
    if size not in PAGE_SIZES:
        raise ValueError("page_size must be 25, 50, or 100")
    needle = str(query or "").strip().casefold()
    where: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("artifact_type", artifact_type),
        ("producer", producer),
        ("status", status),
        ("retention_class", retention),
    ):
        if value:
            where.append(f"{column}=?")
            params.append(value)
    if in_use:
        normalized_in_use = str(in_use).strip().lower()
        if normalized_in_use not in {"true", "false"}:
            raise ValueError("in_use must be true or false")
        where.append("in_use=?")
        params.append(1 if normalized_in_use == "true" else 0)
    if storage:
        if storage not in {"PROJECT", "MT5_RUNTIME", "DATABASE"}:
            raise ValueError("unsupported artifact storage filter")
        where.append("artifact_storage(canonical_path)=?")
        params.append(storage)
    if needle:
        where.append(
            "artifact_casefold(artifact_id || ' ' || artifact_type || ' ' "
            "|| owner_id || ' ' || COALESCE(source_id,'') || ' ' "
            "|| canonical_path) LIKE ? ESCAPE '\\'"
        )
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
    where_sql = " AND ".join(where) if where else "1=1"
    sort_columns = {
        "created": "created_utc",
        "size": "size_bytes",
        "type": "artifact_type",
        "owner": "owner_id",
        "status": "status",
        "retention": "retention_class",
        "storage": "artifact_storage(canonical_path)",
    }
    sort_column = sort_columns[sort]
    sort_direction = direction.upper()
    with connect(path) as conn:
        conn.create_function("artifact_casefold", 1, lambda value: str(value or "").casefold())
        conn.create_function("artifact_storage", 1, _storage)
        total = int(conn.execute(
            f"SELECT COUNT(*) AS n FROM artifact_registry WHERE {where_sql}",
            tuple(params),
        ).fetchone()["n"])
        pages = max(1, math.ceil(total / size)) if total else 1
        bounded_page = min(max(1, int(page)), pages)
        offset = (bounded_page - 1) * size
        rows = conn.execute(
            f"""
            SELECT * FROM artifact_registry
            WHERE {where_sql}
            ORDER BY {sort_column} {sort_direction}, artifact_id {sort_direction}
            LIMIT ? OFFSET ?
            """,
            tuple(params + [size, offset]),
        ).fetchall()
        summary_row = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN retention_class!='ACTIVE_AUTHORITY'
                    THEN size_bytes ELSE 0 END),0) AS total_generated_storage,
                COALESCE(SUM(CASE WHEN retention_class!='ACTIVE_AUTHORITY'
                    AND owner_type='OPTIMIZER_FILE' THEN size_bytes ELSE 0 END),0)
                    AS optimizer_storage,
                COALESCE(SUM(CASE WHEN retention_class!='ACTIVE_AUTHORITY'
                    AND owner_type='CHALLENGER_FILE' THEN size_bytes ELSE 0 END),0)
                    AS challenger_storage,
                COALESCE(SUM(CASE WHEN retention_class!='ACTIVE_AUTHORITY'
                    AND owner_type='BACKTEST_FILE' THEN size_bytes ELSE 0 END),0)
                    AS backtest_storage,
                COALESCE(SUM(CASE WHEN retention_class!='ACTIVE_AUTHORITY'
                    AND owner_type IN ('BACKTEST_RUNTIME','ORPHAN_RUNTIME')
                    THEN size_bytes ELSE 0 END),0) AS runtime_storage,
                COALESCE(SUM(CASE WHEN retention_class!='ACTIVE_AUTHORITY' AND (
                    (owner_type='OPTIMIZER_FILE' AND EXISTS(
                        SELECT 1 FROM artifact_registry src
                        WHERE src.owner_type='OPTIMIZER_JOB'
                          AND src.owner_id=artifact_registry.source_id AND src.deletable=1))
                    OR (owner_type='CHALLENGER_FILE' AND EXISTS(
                        SELECT 1 FROM artifact_registry src
                        WHERE src.owner_type='CHALLENGER'
                          AND src.owner_id=artifact_registry.source_id AND src.deletable=1))
                    OR (owner_type='BACKTEST_FILE' AND EXISTS(
                        SELECT 1 FROM artifact_registry src
                        WHERE src.owner_type='BACKTEST'
                          AND src.owner_id=artifact_registry.source_id AND src.deletable=1))
                    OR (owner_type IN ('BACKTEST_RUNTIME','ORPHAN_RUNTIME') AND cleanable=1)
                    ) THEN size_bytes ELSE 0 END),0) AS safe_cleanup_bytes,
                COALESCE(SUM(CASE WHEN retention_class!='ACTIVE_AUTHORITY' AND in_use=1
                    THEN size_bytes ELSE 0 END),0) AS active_in_use_bytes,
                COALESCE(SUM(CASE WHEN retention_class='ACTIVE_AUTHORITY'
                    THEN size_bytes ELSE 0 END),0) AS protected_bytes
            FROM artifact_registry
            """
        ).fetchone()
    page_items = [_decode(row) for row in rows]
    summary = {key: int(summary_row[key]) for key in summary_row.keys()}
    return {
        "runtime": {
            "status": "NOT_CHECKED",
            "reason": "EXPLICIT_RECONCILE_REQUIRED",
            "data_root": None,
        },
        "summary": summary,
        "page": bounded_page,
        "page_size": size,
        "pages": pages,
        "total": total,
        "items": page_items,
    }


def artifact_trace(
    aid: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    item = get_artifact(aid, path=path)
    if item is None:
        raise FileNotFoundError(aid)
    owner_type = str(item["owner_type"])
    owner_id = str(item["owner_id"])
    with connect(path) as conn:
        lineage: dict[str, Any] = {"artifact": item}
        if owner_type == "OPTIMIZER_JOB":
            lineage["optimizer_job"] = owner_id
            lineage["challengers"] = [
                dict(row) for row in conn.execute(
                    """
                    SELECT challenger_id,source_round,source_pass,status
                    FROM strategy_challengers WHERE source_job_id=?
                    ORDER BY source_round,source_pass
                    """,
                    (owner_id,),
                ).fetchall()
            ]
        elif owner_type == "CHALLENGER":
            challenger = conn.execute(
                """
                SELECT challenger_id,source_job_id,source_round,source_pass,status
                FROM strategy_challengers WHERE challenger_id=?
                """,
                (owner_id,),
            ).fetchone()
            lineage["challenger"] = dict(challenger) if challenger else None
            lineage["backtests"] = [
                dict(row) for row in conn.execute(
                    """
                    SELECT backtest_id,state,report_sha256
                    FROM strategy_challenger_backtests WHERE challenger_id=?
                    ORDER BY created_utc
                    """,
                    (owner_id,),
                ).fetchall()
            ]
        elif owner_type == "BACKTEST":
            backtest = conn.execute(
                """
                SELECT backtest_id,challenger_id,state,report_path,report_sha256
                FROM strategy_challenger_backtests WHERE backtest_id=?
                """,
                (owner_id,),
            ).fetchone()
            lineage["backtest"] = dict(backtest) if backtest else None
            if backtest is not None:
                challenger = conn.execute(
                    """
                    SELECT challenger_id,source_job_id,source_round,source_pass
                    FROM strategy_challengers WHERE challenger_id=?
                    """,
                    (str(backtest["challenger_id"]),),
                ).fetchone()
                lineage["challenger"] = dict(challenger) if challenger else None
        if item.get("source_type") or item.get("source_id"):
            lineage["source"] = {
                "type": item.get("source_type"),
                "id": item.get("source_id"),
            }
        return lineage


def _orphan_delete(item: dict[str, Any], *, path: Path) -> dict[str, Any]:
    if item["owner_type"] != "ORPHAN_RUNTIME":
        raise RuntimeError("ARTIFACT_NOT_ORPHAN_RUNTIME")
    mt5 = detect_mt5()
    if mt5.get("status") != "READY_EXECUTABLE_AND_DATA_ROOT":
        raise RuntimeError("MT5_RUNTIME_AUTHORITY_UNAVAILABLE")
    roots = _runtime_roots(Path(str(mt5["data_root"])).resolve())
    value = Path(str(item["canonical_path"]))
    backtest_id = str(item.get("source_id") or "")
    if not backtest_id.startswith("BT-"):
        raise RuntimeError("ORPHAN_RUNTIME_IDENTITY_INVALID")
    if value.is_dir():
        allowed = [roots["experts"]]
        if value.name != backtest_id:
            raise RuntimeError("ORPHAN_RUNTIME_DIRECTORY_IDENTITY_MISMATCH")
    elif value.suffix.lower() == ".set":
        allowed = [roots["tester"]]
        if _extract_backtest_id(value.name) != backtest_id:
            raise RuntimeError("ORPHAN_RUNTIME_SET_IDENTITY_MISMATCH")
    else:
        allowed = [roots["reports"]]
        if _extract_backtest_id(value.name) != backtest_id:
            raise RuntimeError("ORPHAN_RUNTIME_REPORT_IDENTITY_MISMATCH")
    removed = remove_owned_path(value, roots=allowed)
    with connect(path) as conn:
        conn.execute(
            "DELETE FROM artifact_registry WHERE artifact_id=?",
            (str(item["artifact_id"]),),
        )
    return {"artifact_id": item["artifact_id"], "status": "DELETED", "removed_bytes": removed}


def delete_optimizer_job(
    job_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_current(path)
    with connect(path) as conn:
        job = conn.execute(
            "SELECT job_id,active,evidence_dir FROM optimizer_jobs WHERE job_id=?",
            (str(job_id),),
        ).fetchone()
        if job is None:
            raise FileNotFoundError(job_id)
        if bool(job["active"]):
            raise RuntimeError("OPTIMIZER_DELETE_BLOCKED_ACTIVE")
        challenger_count = int(conn.execute(
            "SELECT COUNT(*) AS n FROM strategy_challengers WHERE source_job_id=?",
            (str(job_id),),
        ).fetchone()["n"])
        if challenger_count:
            raise RuntimeError("OPTIMIZER_DELETE_BLOCKED_CHALLENGER_DEPENDENCY")
    evidence = (ROOT / str(job["evidence_dir"])).resolve()
    if not evidence.is_relative_to(OPTIMIZER_ARTIFACT_ROOT.resolve()):
        raise RuntimeError("OPTIMIZER_DELETE_BLOCKED_LEGACY_OR_FOREIGN_ARTIFACT")
    removed = remove_owned_path(evidence, roots=[OPTIMIZER_ARTIFACT_ROOT])
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch_ids = [
            str(row["batch_id"])
            for row in conn.execute(
                "SELECT batch_id FROM strategy_challenger_batches WHERE job_id=?",
                (str(job_id),),
            ).fetchall()
        ]
        for batch_id in batch_ids:
            conn.execute(
                "DELETE FROM strategy_challenger_batch_items WHERE batch_id=?",
                (batch_id,),
            )
        conn.execute(
            "DELETE FROM strategy_challenger_batches WHERE job_id=?",
            (str(job_id),),
        )
        conn.execute("DELETE FROM optimizer_rounds WHERE job_id=?", (str(job_id),))
        cursor = conn.execute(
            "DELETE FROM optimizer_jobs WHERE job_id=?",
            (str(job_id),),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("OPTIMIZER_DELETE_DB_FAILED")
        conn.execute(
            """
            DELETE FROM artifact_registry
            WHERE (owner_type='OPTIMIZER_JOB' AND owner_id=?)
               OR (owner_type='OPTIMIZER_FILE' AND source_id=?)
            """,
            (str(job_id), str(job_id)),
        )
    return {
        "job_id": job_id,
        "status": "DELETED",
        "removed_bytes": removed,
    }


def artifact_preflight(
    artifact_ids: list[str],
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    unique = list(dict.fromkeys(str(value) for value in artifact_ids))
    if not unique:
        raise ValueError("no artifacts selected")
    items = []
    for aid in unique:
        item = get_artifact(aid, path=path)
        if item is None:
            raise FileNotFoundError(aid)
        reason = None
        if item["retention_class"] == "ACTIVE_AUTHORITY":
            reason = "ACTIVE_AUTHORITY"
        elif item["in_use"]:
            reason = "IN_USE"
        elif not (item["deletable"] or item["cleanable"]):
            reason = "NOT_DELETABLE_OR_CLEANABLE"
        elif item["storage"] == "PROJECT":
            try:
                canonical = assert_owned_path(
                    str(item["canonical_path"]),
                    roots=[ROOT],
                )
                if not canonical.exists():
                    reason = "ARTIFACT_PATH_MISSING"
            except RuntimeError as exc:
                reason = str(exc)
        items.append({**item, "blocked_reason": reason})
    return {
        "selected": len(items),
        "deletable": sum(1 for item in items if item["deletable"] and not item["blocked_reason"]),
        "cleanable": sum(1 for item in items if item["cleanable"] and not item["blocked_reason"]),
        "blocked": sum(1 for item in items if item["blocked_reason"]),
        "project_bytes": sum(
            int(item["size_bytes"]) for item in items if item["storage"] == "PROJECT"
        ),
        "runtime_bytes": sum(
            int(item["size_bytes"]) for item in items if item["storage"] == "MT5_RUNTIME"
        ),
        "items": items,
    }


def execute_artifact_action(
    artifact_ids: list[str],
    *,
    action: str,
    confirmed: bool,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if not confirmed:
        raise RuntimeError("EXPLICIT_ARTIFACT_ACTION_CONFIRMATION_REQUIRED")
    preflight = artifact_preflight(artifact_ids, path=path)
    blocked = [
        item for item in preflight["items"] if item["blocked_reason"]
    ]
    if blocked:
        raise RuntimeError(
            "ARTIFACT_ACTION_BLOCKED:"
            + ",".join(
                str(item["artifact_id"]) + ":" + str(item["blocked_reason"])
                for item in blocked
            )
        )
    priorities = {
        "BACKTEST": 0,
        "CHALLENGER": 1,
        "OPTIMIZER_JOB": 2,
        "ORPHAN_RUNTIME": 3,
    }
    ordered_items = sorted(
        preflight["items"],
        key=lambda item: (
            priorities.get(str(item["owner_type"]), 9),
            str(item["owner_id"]),
        ),
    )
    results = []
    for item in ordered_items:
        owner = str(item["owner_type"])
        owner_id = str(item["owner_id"])
        try:
            if action == "clean":
                if owner == "BACKTEST":
                    result = clean_backtest_runtime(owner_id, path=path)
                elif owner == "ORPHAN_RUNTIME":
                    result = _orphan_delete(item, path=path)
                elif item["cleanable"]:
                    result = _orphan_delete(item, path=path)
                else:
                    raise RuntimeError("ARTIFACT_CLEAN_NOT_SUPPORTED")
            elif action == "delete":
                if owner == "BACKTEST":
                    result = delete_backtest(owner_id, confirmed=True, path=path)
                elif owner == "CHALLENGER":
                    result = delete_challenger(owner_id, confirmed=True, path=path)
                elif owner == "OPTIMIZER_JOB":
                    result = delete_optimizer_job(owner_id, path=path)
                elif owner == "ORPHAN_RUNTIME":
                    result = _orphan_delete(item, path=path)
                else:
                    raise RuntimeError("ARTIFACT_DELETE_NOT_SUPPORTED")
            else:
                raise ValueError("artifact action must be clean or delete")
            results.append(result)
        except Exception as exc:
            completed = [
                str(result.get("artifact_id") or result.get("backtest_id")
                    or result.get("challenger_id") or result.get("job_id") or "")
                for result in results
            ]
            raise RuntimeError(
                "ARTIFACT_BULK_PARTIAL_FAILED:"
                + "completed=" + ",".join(completed)
                + ";failed=" + str(item["artifact_id"])
                + ";reason=" + str(exc)
            ) from exc
    return {
        "action": action,
        "status": "COMPLETED",
        "results": results,
    }


def _resolved_project_path(value: str | Path) -> Path:
    raw = Path(value)
    return raw.resolve() if raw.is_absolute() else (ROOT / raw).resolve()


def _count_root_objects(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(1 for _item in root.iterdir())


def _count_generated_strategy_history_objects() -> int:
    if not STRATEGY_HISTORY_ROOT.exists():
        return 0
    return sum(
        1
        for item in STRATEGY_HISTORY_ROOT.iterdir()
        if not _is_protected_strategy_history_entry(item)
    )


def _clear_owned_root(root: Path) -> int:
    if not root.exists():
        return 0
    removed = 0
    for item in sorted(root.iterdir(), key=lambda value: value.name):
        removed += remove_owned_path(item.resolve(), roots=[root])
    return removed


def _clear_generated_strategy_history_root() -> int:
    if not STRATEGY_HISTORY_ROOT.exists():
        return 0
    removed = 0
    for item in sorted(
        STRATEGY_HISTORY_ROOT.iterdir(),
        key=lambda value: value.name,
    ):
        if _is_protected_strategy_history_entry(item):
            continue
        removed += remove_owned_path(
            item.resolve(),
            roots=[STRATEGY_HISTORY_ROOT],
        )
    return removed


def _baseline_authority_snapshot(path: Path) -> dict[str, Any]:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM ea_baseline WHERE id=1").fetchone()
    return {
        "file_sha256": sha256_file(EA_BASELINE) if EA_BASELINE.is_file() else None,
        "db": dict(row) if row is not None else None,
    }


def _provider_settings_snapshot(path: Path) -> dict[str, list[dict[str, Any]]]:
    snapshot: dict[str, list[dict[str, Any]]] = {}
    with connect(path) as conn:
        tables = [
            str(row["name"])
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table'
                  AND (lower(name) LIKE '%provider%' OR lower(name) LIKE '%setting%')
                ORDER BY name
                """
            ).fetchall()
        ]
        for table in tables:
            safe = '"' + table.replace('"', '""') + '"'
            rows = conn.execute(f"SELECT * FROM {safe}").fetchall()
            snapshot[table] = [dict(row) for row in rows]
    return snapshot


def _cleanup_final_state(path: Path) -> dict[str, Any]:
    with connect(path) as conn:
        values: dict[str, Any] = {
            "optimizer_jobs": int(conn.execute(
                "SELECT COUNT(*) AS n FROM optimizer_jobs"
            ).fetchone()["n"]),
            "optimizer_rounds": int(conn.execute(
                "SELECT COUNT(*) AS n FROM optimizer_rounds"
            ).fetchone()["n"]),
            "strategy_challenger_batches": int(conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_challenger_batches"
            ).fetchone()["n"]),
            "strategy_challenger_batch_items": int(conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_challenger_batch_items"
            ).fetchone()["n"]),
            "challengers": int(conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_challengers"
            ).fetchone()["n"]),
            "backtests": int(conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_challenger_backtests"
            ).fetchone()["n"]),
            "retirements": int(conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_challenger_retirements"
            ).fetchone()["n"]),
            "promotions": int(conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_promotions"
            ).fetchone()["n"]),
            "strategy_history": int(conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_champions"
            ).fetchone()["n"]),
            "scientist_threads": int(conn.execute(
                "SELECT COUNT(*) AS n FROM scientist_threads"
            ).fetchone()["n"]),
            "scientist_messages": int(conn.execute(
                "SELECT COUNT(*) AS n FROM scientist_messages"
            ).fetchone()["n"]),
            "scientist_chat_requests": int(conn.execute(
                "SELECT COUNT(*) AS n FROM scientist_chat_requests"
            ).fetchone()["n"]),
            "generated_artifact_registry_rows": int(conn.execute(
                """
                SELECT COUNT(*) AS n FROM artifact_registry
                WHERE retention_class!='ACTIVE_AUTHORITY'
                """
            ).fetchone()["n"]),
            "registered_orphan_runtime": int(conn.execute(
                """
                SELECT COUNT(*) AS n FROM artifact_registry
                WHERE owner_type='ORPHAN_RUNTIME'
                """
            ).fetchone()["n"]),
        }
        current = conn.execute(
            "SELECT strategy_id FROM strategy_champions WHERE status='CURRENT' LIMIT 1"
        ).fetchone()
        values["champion"] = str(current["strategy_id"]) if current else None

    roots = {
        "optimizer_root": OPTIMIZER_ARTIFACT_ROOT,
        "challenger_root": CHALLENGER_ARTIFACT_ROOT,
        "backtest_root": BACKTEST_ARTIFACT_ROOT,
        "challenger_operation_root": CHALLENGER_OPERATION_ARTIFACT_ROOT,
        "promotion_recovery_root": PROMOTION_RECOVERY_ROOT,
        "champion_current_root": CHAMPION_CURRENT_ROOT,
    }
    values["generated_semantic_root_objects"] = (
        sum(_count_root_objects(root) for root in roots.values())
        + _count_generated_strategy_history_objects()
    )
    values["promotion_recovery_residue"] = _count_root_objects(
        PROMOTION_RECOVERY_ROOT
    )
    values["cleanup_staging_recovery_residue"] = (
        values["promotion_recovery_residue"]
    )
    return values


def global_cleanup_preflight(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    migrate_current(path)
    reconcile_artifacts(path=path)
    blockers: list[str] = []
    with connect(path) as conn:
        active_job = conn.execute(
            "SELECT job_id FROM optimizer_jobs WHERE active=1 LIMIT 1"
        ).fetchone()
        if active_job is not None:
            blockers.append("ACTIVE_OPTIMIZER:" + str(active_job["job_id"]))

        active_backtest = conn.execute(
            """
            SELECT backtest_id FROM strategy_challenger_backtests
            WHERE state IN ('PREPARED','RUNNING') LIMIT 1
            """
        ).fetchone()
        if active_backtest is not None:
            blockers.append("ACTIVE_BACKTEST:" + str(active_backtest["backtest_id"]))

        active_promotion = conn.execute(
            """
            SELECT promotion_id FROM strategy_promotions
            WHERE state IN ('PREPARED','ARTIFACTS_STAGED','FILES_COMMITTED') LIMIT 1
            """
        ).fetchone()
        if active_promotion is not None:
            blockers.append("ACTIVE_PROMOTION:" + str(active_promotion["promotion_id"]))

        current_champion = _current_champion_id(conn)
        if current_champion is not None:
            blockers.append("CURRENT_CHAMPION:" + current_champion)

        in_flight = conn.execute(
            """
            SELECT request_id FROM scientist_chat_requests
            WHERE state='CALL_IN_FLIGHT' LIMIT 1
            """
        ).fetchone()
        if in_flight is not None:
            blockers.append("SCIENTIST_CALL_IN_FLIGHT:" + str(in_flight["request_id"]))

        for row in conn.execute(
            "SELECT job_id,evidence_dir FROM optimizer_jobs ORDER BY job_id"
        ).fetchall():
            resolved = _resolved_project_path(str(row["evidence_dir"]))
            if not resolved.is_relative_to(OPTIMIZER_ARTIFACT_ROOT.resolve()):
                blockers.append("UNSAFE_OPTIMIZER_PATH:" + str(row["job_id"]))

        for row in conn.execute(
            "SELECT challenger_id,bundle_path FROM strategy_challengers ORDER BY challenger_id"
        ).fetchall():
            resolved = _resolved_project_path(str(row["bundle_path"]))
            if not resolved.is_relative_to(CHALLENGER_ARTIFACT_ROOT.resolve()):
                blockers.append("UNSAFE_CHALLENGER_PATH:" + str(row["challenger_id"]))

        for row in conn.execute(
            "SELECT backtest_id,evidence_path FROM strategy_challenger_backtests "
            "ORDER BY backtest_id"
        ).fetchall():
            resolved = _resolved_project_path(str(row["evidence_path"]))
            if resolved.is_relative_to(LEGACY_M06_BACKTEST_EVIDENCE_ROOT.resolve()):
                blockers.append("PROTECTED_LEGACY_BACKTEST:" + str(row["backtest_id"]))
            elif not resolved.is_relative_to(BACKTEST_ARTIFACT_ROOT.resolve()):
                blockers.append("UNSAFE_BACKTEST_PATH:" + str(row["backtest_id"]))

        counts = {
            "optimizer_jobs": int(conn.execute(
                "SELECT COUNT(*) AS n FROM optimizer_jobs"
            ).fetchone()["n"]),
            "optimizer_rounds": int(conn.execute(
                "SELECT COUNT(*) AS n FROM optimizer_rounds"
            ).fetchone()["n"]),
            "challengers": int(conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_challengers"
            ).fetchone()["n"]),
            "backtests": int(conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_challenger_backtests"
            ).fetchone()["n"]),
            "challenger_batches": int(conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_challenger_batches"
            ).fetchone()["n"]),
            "challenger_batch_items": int(conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_challenger_batch_items"
            ).fetchone()["n"]),
            "retirements": int(conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_challenger_retirements"
            ).fetchone()["n"]),
            "promotions": int(conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_promotions"
            ).fetchone()["n"]),
            "strategy_history": int(conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_champions"
            ).fetchone()["n"]),
            "scientist_threads": int(conn.execute(
                "SELECT COUNT(*) AS n FROM scientist_threads"
            ).fetchone()["n"]),
            "scientist_messages": int(conn.execute(
                "SELECT COUNT(*) AS n FROM scientist_messages"
            ).fetchone()["n"]),
            "scientist_chat_requests": int(conn.execute(
                "SELECT COUNT(*) AS n FROM scientist_chat_requests"
            ).fetchone()["n"]),
            "runtime_artifacts": int(conn.execute(
                """
                SELECT COUNT(*) AS n FROM artifact_registry
                WHERE owner_type IN ('BACKTEST_RUNTIME','ORPHAN_RUNTIME')
                """
            ).fetchone()["n"]),
        }
        generated_rows = [
            _decode(row)
            for row in conn.execute(
                """
                SELECT * FROM artifact_registry
                WHERE retention_class!='ACTIVE_AUTHORITY'
                ORDER BY artifact_id
                """
            ).fetchall()
        ]

    project_bytes = sum(
        int(item["size_bytes"]) for item in generated_rows
        if item["storage"] == "PROJECT"
    )
    runtime_bytes = sum(
        int(item["size_bytes"]) for item in generated_rows
        if item["storage"] == "MT5_RUNTIME"
    )
    counts["generated_files"] = sum(
        1 for item in generated_rows
        if item["owner_type"] in {
            "OPTIMIZER_FILE","CHALLENGER_FILE","BACKTEST_FILE",
            "BACKTEST_RUNTIME","ORPHAN_RUNTIME"
        }
    )
    counts["generated_bytes"] = project_bytes + runtime_bytes
    selected = (
        counts["optimizer_jobs"] + counts["optimizer_rounds"]
        + counts["challengers"] + counts["backtests"]
        + counts["challenger_batches"] + counts["challenger_batch_items"]
        + counts["retirements"] + counts["promotions"]
        + counts["strategy_history"] + counts["scientist_threads"]
        + counts["scientist_messages"] + counts["scientist_chat_requests"]
        + counts["runtime_artifacts"]
    )
    return {
        "status": "BLOCKED" if blockers else "READY",
        "blockers": sorted(set(blockers)),
        "selected": selected,
        "project_bytes": project_bytes,
        "runtime_bytes": runtime_bytes,
        "artifact_ids": [item["artifact_id"] for item in generated_rows],
        "plan": counts,
        "deletion_order": [
            "Backtests",
            "Retirements / disposable lifecycle children",
            "Promotion recovery / non-authoritative Strategy history",
            "Challengers",
            "Challenger batch/journal state",
            "Optimizer rounds",
            "Optimizer jobs",
            "Scientist operational state",
            "Orphan runtime / derived generated roots",
        ],
    }


def clean_generated_data(
    *,
    confirmed: bool,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if not confirmed:
        raise RuntimeError("EXPLICIT_GLOBAL_CLEAN_CONFIRMATION_REQUIRED")
    preflight = global_cleanup_preflight(path=path)
    if preflight["blockers"]:
        raise RuntimeError(
            "GLOBAL_CLEAN_BLOCKED:" + ",".join(preflight["blockers"])
        )

    settings_before = _provider_settings_snapshot(path)
    baseline_before = _baseline_authority_snapshot(path)
    results: list[dict[str, Any]] = []

    with connect(path) as conn:
        backtest_ids = [
            str(row["backtest_id"])
            for row in conn.execute(
                "SELECT backtest_id FROM strategy_challenger_backtests "
                "ORDER BY created_utc,backtest_id"
            ).fetchall()
        ]
    for backtest_id in backtest_ids:
        results.append(delete_backtest(backtest_id, confirmed=True, path=path))

    with connect(path) as conn:
        retirements = [
            dict(row) for row in conn.execute(
                """
                SELECT retirement_id,evidence_path
                FROM strategy_challenger_retirements
                ORDER BY created_utc,retirement_id
                """
            ).fetchall()
        ]
    for row in retirements:
        evidence = _resolved_project_path(str(row["evidence_path"]))
        if evidence.exists():
            remove_owned_path(
                evidence,
                roots=[CHALLENGER_OPERATION_ARTIFACT_ROOT],
            )
    with connect(path) as conn:
        conn.execute("DELETE FROM strategy_challenger_retirements")

    # A current Champion is a preflight blocker. Without one, all remaining
    # Champion tenures/promotions are generated history and can be removed
    # before deleting their source Challengers.
    with connect(path) as conn:
        history_rows = [
            dict(row) for row in conn.execute(
                """
                SELECT champion_tenure_id,artifact_path,evidence_path
                FROM strategy_champions
                ORDER BY promoted_utc,champion_tenure_id
                """
            ).fetchall()
        ]
        promotion_rows = [
            dict(row) for row in conn.execute(
                """
                SELECT promotion_id,recovery_path
                FROM strategy_promotions
                ORDER BY created_utc,promotion_id
                """
            ).fetchall()
        ]
    for row in history_rows:
        artifact = _resolved_project_path(str(row["artifact_path"]))
        if artifact.exists() and artifact.is_relative_to(STRATEGY_HISTORY_ROOT.resolve()):
            remove_owned_path(artifact, roots=[STRATEGY_HISTORY_ROOT])
    for row in promotion_rows:
        recovery = _resolved_project_path(str(row["recovery_path"]))
        if recovery.exists() and recovery.is_relative_to(PROMOTION_RECOVERY_ROOT.resolve()):
            remove_owned_path(recovery, roots=[PROMOTION_RECOVERY_ROOT])
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM strategy_champions")
        conn.execute("DELETE FROM strategy_promotions")

    with connect(path) as conn:
        challenger_ids = [
            str(row["challenger_id"])
            for row in conn.execute(
                "SELECT challenger_id FROM strategy_challengers "
                "ORDER BY created_utc,challenger_id"
            ).fetchall()
        ]
    for challenger_id in challenger_ids:
        results.append(delete_challenger(challenger_id, confirmed=True, path=path))

    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM strategy_challenger_batch_items")
        conn.execute("DELETE FROM strategy_challenger_batches")

    with connect(path) as conn:
        job_ids = [
            str(row["job_id"])
            for row in conn.execute(
                "SELECT job_id FROM optimizer_jobs ORDER BY created_utc,job_id"
            ).fetchall()
        ]
    for job_id in job_ids:
        results.append(delete_optimizer_job(job_id, path=path))

    # Scientist cleanup is fail-closed. Any exception aborts CLEAN status.
    scientist_result = purge_chat(path=path)
    results.append({"scientist": scientist_result})

    reconcile_artifacts(path=path)
    with connect(path) as conn:
        orphan_rows = [
            _decode(row) for row in conn.execute(
                """
                SELECT * FROM artifact_registry
                WHERE owner_type='ORPHAN_RUNTIME'
                ORDER BY artifact_id
                """
            ).fetchall()
        ]
    for item in orphan_rows:
        results.append(_orphan_delete(item, path=path))

    removed_root_bytes = 0
    for root in (
        OPTIMIZER_ARTIFACT_ROOT,
        CHALLENGER_ARTIFACT_ROOT,
        BACKTEST_ARTIFACT_ROOT,
        CHALLENGER_OPERATION_ARTIFACT_ROOT,
        PROMOTION_RECOVERY_ROOT,
        CHAMPION_CURRENT_ROOT,
    ):
        removed_root_bytes += _clear_owned_root(root)
    removed_root_bytes += _clear_generated_strategy_history_root()

    # Reconcile stale file rows once filesystem/database state is clean, then
    # retain only protected baseline/accepted authority registry records.
    reconcile_artifacts(path=path)
    with connect(path) as conn:
        conn.execute(
            "DELETE FROM artifact_registry WHERE retention_class!='ACTIVE_AUTHORITY'"
        )

    final = _cleanup_final_state(path)
    expected_zero = {
        "optimizer_jobs",
        "optimizer_rounds",
        "strategy_challenger_batches",
        "strategy_challenger_batch_items",
        "challengers",
        "backtests",
        "retirements",
        "promotions",
        "strategy_history",
        "scientist_threads",
        "scientist_messages",
        "scientist_chat_requests",
        "generated_artifact_registry_rows",
        "registered_orphan_runtime",
        "generated_semantic_root_objects",
        "promotion_recovery_residue",
        "cleanup_staging_recovery_residue",
    }
    invalid = {
        key: final[key]
        for key in expected_zero
        if int(final[key]) != 0
    }
    if final["champion"] is not None:
        invalid["champion"] = final["champion"]
    if invalid:
        raise RuntimeError(
            "GLOBAL_CLEAN_FINAL_STATE_INVALID:"
            + json.dumps(invalid, sort_keys=True)
        )

    settings_after = _provider_settings_snapshot(path)
    if settings_before != settings_after:
        raise RuntimeError("GLOBAL_CLEAN_PROVIDER_SETTINGS_MUTATED")
    baseline_after = _baseline_authority_snapshot(path)
    if baseline_before != baseline_after:
        raise RuntimeError("GLOBAL_CLEAN_BASELINE_MUTATED")

    return {
        "status": "CLEAN",
        "results": results,
        "removed_root_bytes": removed_root_bytes,
        "final": final,
        "provider_settings_unchanged": True,
        "baseline_unchanged": True,
        "plan": preflight["plan"],
    }
