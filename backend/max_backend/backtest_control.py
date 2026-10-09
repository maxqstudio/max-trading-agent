from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .challenger_operations_store import get_backtest
from .challenger_deployment import challenger_deployment_directory, resolve_frozen_mt5_expert_root
from .challenger_store import get_challenger
from .config import (
    BACKTEST_ARTIFACT_ROOT,
    CHALLENGER_ARTIFACT_ROOT,
    DATABASE_PATH,
    LEGACY_M06_BACKTEST_EVIDENCE_ROOT,
    ROOT,
)
from .db import connect
from .workflow_store import (
    delete_backtest_row,
    delete_challenger_row,
    set_backtest_runtime_status,
)
from .optimizer_core import sha256_file
from .path_safety import assert_owned_path, file_size_tree, remove_owned_path


def _project_path(value: str) -> Path:
    raw = Path(str(value))
    return raw.resolve() if raw.is_absolute() else (ROOT / raw).resolve()


def verified_retained_report(
    backtest_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> Path:
    record = get_backtest(backtest_id, path=path)
    if record is None:
        raise FileNotFoundError(backtest_id)
    report_value = str(record.get("report_path") or "")
    expected_sha = str(record.get("report_sha256") or "")
    if not report_value or not expected_sha:
        raise RuntimeError("BACKTEST_RETAINED_REPORT_UNAVAILABLE")
    report = _project_path(report_value)
    roots = (
        BACKTEST_ARTIFACT_ROOT.resolve(),
        LEGACY_M06_BACKTEST_EVIDENCE_ROOT.resolve(),
    )
    if not any(report.is_relative_to(root) for root in roots):
        raise RuntimeError("BACKTEST_RETAINED_REPORT_OUTSIDE_AUTHORITY")
    if not report.is_file():
        raise RuntimeError("BACKTEST_RETAINED_REPORT_MISSING")
    if sha256_file(report) != expected_sha:
        raise RuntimeError("BACKTEST_RETAINED_REPORT_HASH_MISMATCH")
    return report


def _runtime_paths(record: dict[str, Any]) -> dict[str, Any]:
    request = record.get("request") if isinstance(record.get("request"), dict) else {}
    mt5 = request.get("mt5") if isinstance(request.get("mt5"), dict) else {}
    data_root_text = str(mt5.get("data_root") or "").strip()
    if not data_root_text:
        raise RuntimeError("BACKTEST_RUNTIME_DATA_ROOT_UNAVAILABLE")
    data_root = Path(data_root_text).resolve()
    backtest_id = str(record["backtest_id"])
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    runtime = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
    expert_reused = runtime.get("expert_reused") is True
    if expert_reused:
        authority = resolve_frozen_mt5_expert_root({"mt5": mt5})
        challenger_id = str(runtime.get("challenger_id") or record.get("challenger_id") or "")
        expert = challenger_deployment_directory(
            expert_root=authority["expert_root"],
            challenger_id=challenger_id,
        )
        recorded_expert = Path(str(runtime.get("expert_dir") or ""))
        if recorded_expert.is_symlink() or recorded_expert.resolve() != expert.resolve():
            raise RuntimeError("BACKTEST_RUNTIME_EXPERT_IDENTITY_MISMATCH")
        expert_root = expert.parent
    else:
        expert_root = data_root / "MQL5" / "Experts" / "MaxMTF" / "ChallengerBacktests"
        expert = Path(
            str(runtime.get("expert_dir") or (expert_root / backtest_id))
        )
    tester_root = data_root / "MQL5" / "Profiles" / "Tester"
    report_root = data_root / "reports"

    tester = Path(
        str(
            runtime.get("tester_set")
            or (tester_root / f"MaxMTF_Backtest_{backtest_id}.set")
        )
    )
    source_report = Path(
        str(
            runtime.get("source_report")
            or result.get("report_source")
            or (report_root / f"MaxMTF_Backtest_{backtest_id}.htm")
        )
    )

    # Registered identity must agree with the exact Backtest naming contract.
    if not expert_reused and expert.name != backtest_id:
        raise RuntimeError("BACKTEST_RUNTIME_EXPERT_IDENTITY_MISMATCH")
    allowed_tester_names = {
        f"MaxMTF_Backtest_{backtest_id}.set",
        f"MAX_M06_{backtest_id}.set",
    }
    if tester.name not in allowed_tester_names:
        raise RuntimeError("BACKTEST_RUNTIME_SET_IDENTITY_MISMATCH")
    allowed_report_names = {
        f"MaxMTF_Backtest_{backtest_id}.htm",
        f"MaxMTF_Backtest_{backtest_id}.html",
        f"MAX_M06_{backtest_id}.htm",
        f"MAX_M06_{backtest_id}.html",
    }
    if source_report.name not in allowed_report_names:
        raise RuntimeError("BACKTEST_RUNTIME_REPORT_IDENTITY_MISMATCH")

    return {
        "data_root": data_root,
        "expert_root": expert_root,
        "tester_root": tester_root,
        "report_root": report_root,
        "expert_dir": expert,
        "expert_reused": expert_reused,
        "tester_set": tester,
        "source_report": source_report,
    }


def runtime_inventory(
    backtest_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    record = get_backtest(backtest_id, path=path)
    if record is None:
        raise FileNotFoundError(backtest_id)
    paths = _runtime_paths(record)
    items = []
    inventory_specs = []
    if paths["expert_reused"]:
        inventory_specs.append(("EXPERT_DEPLOYMENT_REUSED", "expert_root", "expert_dir", False))
    else:
        inventory_specs.append(("EXPERT_DEPLOYMENT", "expert_root", "expert_dir", True))
    inventory_specs.extend([
        ("TESTER_SET", "tester_root", "tester_set", True),
        ("MT5_REPORT", "report_root", "source_report", True),
    ])
    for kind, root_key, value_key, removable in inventory_specs:
        value = assert_owned_path(paths[value_key], roots=[paths[root_key]])
        items.append(
            {
                "type": kind,
                "path": str(value),
                "exists": value.exists(),
                "size_bytes": file_size_tree(value),
                "removable": removable,
            }
        )
    return {
        "backtest_id": backtest_id,
        "runtime_status": record.get("runtime_status", "UNKNOWN"),
        "items": items,
        "size_bytes": sum(int(item["size_bytes"]) for item in items if item["removable"]),
    }


def clean_backtest_runtime(
    backtest_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    record = get_backtest(backtest_id, path=path)
    if record is None:
        raise FileNotFoundError(backtest_id)
    if str(record.get("state")) in {"PREPARED", "RUNNING"}:
        raise RuntimeError("BACKTEST_RUNTIME_CLEAN_BLOCKED_ACTIVE")
    paths = _runtime_paths(record)
    removed = 0
    if not paths["expert_reused"]:
        removed += remove_owned_path(paths["expert_dir"], roots=[paths["expert_root"]])
    removed += remove_owned_path(paths["tester_set"], roots=[paths["tester_root"]])
    removed += remove_owned_path(paths["source_report"], roots=[paths["report_root"]])
    set_backtest_runtime_status(backtest_id, "CLEANED", path=path)
    with connect(path) as conn:
        conn.execute(
            "DELETE FROM artifact_registry "
            "WHERE owner_type='BACKTEST_RUNTIME' AND source_id=?",
            (str(backtest_id),),
        )
    inventory = runtime_inventory(backtest_id, path=path)
    if any(item["exists"] and item["removable"] for item in inventory["items"]):
        raise RuntimeError("BACKTEST_RUNTIME_CLEAN_VERIFICATION_FAILED")
    retained_report: str | None = None
    if str(record.get("state")) == "COMPLETED":
        retained_report = str(verified_retained_report(backtest_id, path=path))
    return {
        "backtest_id": backtest_id,
        "runtime_status": "CLEANED",
        "removed_bytes": removed,
        "retained_report": retained_report,
    }


def delete_backtest(
    backtest_id: str,
    *,
    confirmed: bool,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if not confirmed:
        raise RuntimeError("EXPLICIT_BACKTEST_DELETE_CONFIRMATION_REQUIRED")
    record = get_backtest(backtest_id, path=path)
    if record is None:
        raise FileNotFoundError(backtest_id)
    if str(record.get("state")) in {"PREPARED", "RUNNING"}:
        raise RuntimeError("BACKTEST_DELETE_BLOCKED_ACTIVE")
    evidence = _project_path(str(record["evidence_path"]))
    legacy_root = LEGACY_M06_BACKTEST_EVIDENCE_ROOT.resolve()
    if evidence.is_relative_to(legacy_root):
        raise RuntimeError("BACKTEST_DELETE_BLOCKED_ACCEPTED_LEGACY_EVIDENCE")
    current_root = BACKTEST_ARTIFACT_ROOT.resolve()
    if not evidence.is_relative_to(current_root):
        raise RuntimeError("BACKTEST_DELETE_EVIDENCE_OUTSIDE_AUTHORITY")

    runtime = clean_backtest_runtime(backtest_id, path=path)
    removed_project_bytes = remove_owned_path(evidence, roots=[current_root])
    delete_backtest_row(backtest_id, path=path)
    with connect(path) as conn:
        conn.execute(
            """
            DELETE FROM artifact_registry
            WHERE (owner_type='BACKTEST' AND owner_id=?)
               OR (owner_type IN ('BACKTEST_FILE','BACKTEST_RUNTIME') AND source_id=?)
            """,
            (str(backtest_id), str(backtest_id)),
        )
    if get_backtest(backtest_id, path=path) is not None:
        raise RuntimeError("BACKTEST_DELETE_DB_VERIFICATION_FAILED")
    if evidence.exists():
        raise RuntimeError("BACKTEST_DELETE_PROJECT_VERIFICATION_FAILED")
    return {
        "backtest_id": backtest_id,
        "status": "DELETED",
        "removed_project_bytes": removed_project_bytes,
        "removed_runtime_bytes": runtime["removed_bytes"],
    }


def challenger_delete_preflight(
    challenger_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    row = get_challenger(challenger_id, path=path)
    if row is None:
        raise FileNotFoundError(challenger_id)
    blockers: list[str] = []
    with connect(path) as conn:
        champion = conn.execute(
            """
            SELECT strategy_id FROM strategy_champions
            WHERE status='CURRENT' AND source_challenger_id=?
            LIMIT 1
            """,
            (str(challenger_id),),
        ).fetchone()
        if champion is not None:
            blockers.append("CURRENT_CHAMPION_DEPENDS_ON_CHALLENGER")
        active_backtest = conn.execute(
            """
            SELECT backtest_id,state FROM strategy_challenger_backtests
            WHERE challenger_id=? AND state IN ('PREPARED','RUNNING')
            LIMIT 1
            """,
            (str(challenger_id),),
        ).fetchone()
        if active_backtest is not None:
            blockers.append(
                "ACTIVE_BACKTEST:"
                + str(active_backtest["backtest_id"])
                + ":"
                + str(active_backtest["state"])
            )
        backtest_count = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_challenger_backtests "
                "WHERE challenger_id=?",
                (str(challenger_id),),
            ).fetchone()["n"]
        )
        if backtest_count:
            blockers.append("BACKTEST_HISTORY_EXISTS_DELETE_BACKTESTS_FIRST")
        active_promotion = conn.execute(
            """
            SELECT promotion_id,state FROM strategy_promotions
            WHERE challenger_id=?
              AND state IN ('PREPARED','ARTIFACTS_STAGED','FILES_COMMITTED')
            LIMIT 1
            """,
            (str(challenger_id),),
        ).fetchone()
        if active_promotion is not None:
            blockers.append(
                "ACTIVE_PROMOTION:"
                + str(active_promotion["promotion_id"])
                + ":"
                + str(active_promotion["state"])
            )
        committed_promotion_count = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM strategy_promotions "
                "WHERE challenger_id=? AND state='COMMITTED'",
                (str(challenger_id),),
            ).fetchone()["n"]
        )
        if committed_promotion_count:
            blockers.append("PROMOTION_HISTORY_PROTECTED")
    bundle = _project_path(str(row["bundle_path"]))
    current_root = CHALLENGER_ARTIFACT_ROOT.resolve()
    if not bundle.is_relative_to(current_root):
        blockers.append("LEGACY_OR_FOREIGN_CHALLENGER_ARTIFACT_PROTECTED")
    return {
        "challenger_id": challenger_id,
        "deletable": not blockers,
        "blockers": blockers,
        "bundle_path": str(bundle),
        "size_bytes": file_size_tree(bundle),
    }


def delete_challenger(
    challenger_id: str,
    *,
    confirmed: bool,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if not confirmed:
        raise RuntimeError("EXPLICIT_CHALLENGER_DELETE_CONFIRMATION_REQUIRED")
    preflight = challenger_delete_preflight(challenger_id, path=path)
    if preflight["blockers"]:
        raise RuntimeError(
            "CHALLENGER_DELETE_BLOCKED:" + ",".join(preflight["blockers"])
        )
    bundle = Path(preflight["bundle_path"])
    removed = remove_owned_path(bundle, roots=[CHALLENGER_ARTIFACT_ROOT])
    delete_challenger_row(challenger_id, path=path)
    with connect(path) as conn:
        conn.execute(
            """
            DELETE FROM artifact_registry
            WHERE (owner_type='CHALLENGER' AND owner_id=?)
               OR (owner_type='CHALLENGER_FILE' AND source_id=?)
            """,
            (str(challenger_id), str(challenger_id)),
        )
    if get_challenger(challenger_id, path=path) is not None:
        raise RuntimeError("CHALLENGER_DELETE_DB_VERIFICATION_FAILED")
    return {
        "challenger_id": challenger_id,
        "status": "DELETED",
        "removed_bytes": removed,
    }

def backtest_action_preflight(
    backtest_ids: list[str],
    *,
    action: str,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if action not in {"clean", "delete"}:
        raise ValueError("backtest action must be clean or delete")
    unique = list(dict.fromkeys(str(value) for value in backtest_ids))
    if not unique:
        raise ValueError("no Backtests selected")
    items: list[dict[str, Any]] = []
    project_bytes = 0
    runtime_bytes = 0
    for backtest_id in unique:
        record = get_backtest(backtest_id, path=path)
        if record is None:
            raise FileNotFoundError(backtest_id)
        blockers: list[str] = []
        if str(record.get("state")) in {"PREPARED", "RUNNING"}:
            blockers.append("ACTIVE_BACKTEST")
        evidence = _project_path(str(record["evidence_path"]))
        if action == "delete":
            if evidence.is_relative_to(LEGACY_M06_BACKTEST_EVIDENCE_ROOT.resolve()):
                blockers.append("ACCEPTED_LEGACY_EVIDENCE")
            elif not evidence.is_relative_to(BACKTEST_ARTIFACT_ROOT.resolve()):
                blockers.append("PROJECT_EVIDENCE_OUTSIDE_AUTHORITY")
        project_size = file_size_tree(evidence)
        try:
            inventory = runtime_inventory(backtest_id, path=path)
            runtime_size = int(inventory["size_bytes"])
        except Exception as exc:
            inventory = {"items": [], "size_bytes": 0}
            runtime_size = 0
            if action == "clean":
                blockers.append("RUNTIME_INVENTORY_UNAVAILABLE:" + str(exc))
        project_bytes += project_size
        runtime_bytes += runtime_size
        items.append(
            {
                "backtest_id": backtest_id,
                "state": record["state"],
                "blockers": blockers,
                "project_bytes": project_size,
                "runtime_bytes": runtime_size,
                "runtime": inventory,
            }
        )
    return {
        "action": action,
        "selected": len(items),
        "deletable": sum(1 for item in items if not item["blockers"])
        if action == "delete" else 0,
        "cleanable": sum(1 for item in items if not item["blockers"])
        if action == "clean" else 0,
        "blocked": sum(1 for item in items if item["blockers"]),
        "project_bytes": project_bytes,
        "runtime_bytes": runtime_bytes,
        "items": items,
    }


def execute_backtest_action(
    backtest_ids: list[str],
    *,
    action: str,
    confirmed: bool,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if not confirmed:
        raise RuntimeError("EXPLICIT_BACKTEST_BULK_CONFIRMATION_REQUIRED")
    preflight = backtest_action_preflight(
        backtest_ids,
        action=action,
        path=path,
    )
    blocked = [item for item in preflight["items"] if item["blockers"]]
    if blocked:
        raise RuntimeError(
            "BACKTEST_BULK_ACTION_BLOCKED:"
            + ";".join(
                item["backtest_id"] + ":" + ",".join(item["blockers"])
                for item in blocked
            )
        )
    results = []
    for item in sorted(
        preflight["items"],
        key=lambda value: value["backtest_id"],
    ):
        try:
            if action == "clean":
                result = clean_backtest_runtime(item["backtest_id"], path=path)
            else:
                result = delete_backtest(
                    item["backtest_id"],
                    confirmed=True,
                    path=path,
                )
            results.append(result)
        except Exception as exc:
            completed = [
                str(result.get("backtest_id") or "")
                for result in results
            ]
            raise RuntimeError(
                "BACKTEST_BULK_PARTIAL_FAILED:"
                + "completed=" + ",".join(completed)
                + ";failed=" + str(item["backtest_id"])
                + ";reason=" + str(exc)
            ) from exc
    return {
        "action": action,
        "status": "COMPLETED",
        "results": results,
    }
