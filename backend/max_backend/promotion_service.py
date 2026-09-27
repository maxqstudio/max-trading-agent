from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .challenger_bundle import (
    _same_params,
    current_baseline_sha256,
    verify_challenger_bundle,
)
from .challenger_registry import verify_optimizer_winner
from .optimizer_candidates import revalidate_candidate_for_registration
from .challenger_store import get_challenger
from .champion_bundle import (
    verify_champion_parity,
    verify_champion_set,
    verify_hash_manifest,
    write_champion_set,
    write_hash_manifest,
)
from .champion_store import (
    PROMOTION_ACTIVE_STATES,
    active_promotion,
    commit_promotion_authority,
    create_prepared_promotion,
    current_champion,
    get_champion,
    get_promotion,
    list_promotions,
    migrate_m04,
    update_promotion,
)
from .config import (
    CHAMPION_CURRENT_ROOT,
    DATABASE_PATH,
    EA_BASELINE,
    PROMOTION_RECOVERY_ROOT,
    ROOT,
    STRATEGY_HISTORY_ROOT,
)
from .optimizer_core import EA_STEM, EXPERT_SUBDIR, TESTER_SET, sha256_file
from .optimizer_runtime import _read_text_flexible, compile_summary
from .optimizer_store import active_job, utc_now
from .workflow_contract import (
    ROLE_OPTIMIZER_WINNER,
    ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE,
    optimizer_uses_owner_selection,
)


def _sha(path: Path) -> str:
    return sha256_file(path)


def _retained_owner_selected_source(
    challenger: dict[str, Any],
) -> dict[str, Any]:
    request = challenger.get("source_request")
    if not isinstance(request, dict) or not optimizer_uses_owner_selection(request):
        raise RuntimeError("PROMOTION_OWNER_SELECTED_WORKFLOW_INVALID")

    candidate = challenger.get("winner")
    if not isinstance(candidate, dict):
        raise RuntimeError("PROMOTION_QUALIFIED_CANDIDATE_EVIDENCE_MISSING")
    if candidate.get("schema") != "MAX_REBUILD_QUALIFIED_CANDIDATE_V1":
        raise RuntimeError("PROMOTION_QUALIFIED_CANDIDATE_SCHEMA_INVALID")
    if candidate.get("selection_authority") != "OWNER_EXPLICIT_SELECTION":
        raise RuntimeError("PROMOTION_QUALIFIED_SELECTION_AUTHORITY_INVALID")

    expected_identity = {
        "challenger_id": str(challenger["challenger_id"]),
        "job_id": str(challenger["source_job_id"]),
        "round": int(challenger["source_round"]),
        "pass": int(challenger["source_pass"]),
    }
    for key, expected in expected_identity.items():
        actual = candidate.get(key)
        if key in {"round", "pass"}:
            try:
                actual = int(actual)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"PROMOTION_QUALIFIED_CANDIDATE_IDENTITY_INVALID:{key}"
                ) from exc
        else:
            actual = str(actual or "")
        if actual != expected:
            raise RuntimeError(
                f"PROMOTION_QUALIFIED_CANDIDATE_IDENTITY_MISMATCH:{key}"
            )

    if str(candidate.get("ea_sha256") or "") != str(challenger["baseline_ea_sha256"]):
        raise RuntimeError("PROMOTION_QUALIFIED_CANDIDATE_EA_SHA_MISMATCH")
    if str(candidate.get("report_sha256") or "") != str(
        challenger["winning_xml_sha256"]
    ):
        raise RuntimeError("PROMOTION_QUALIFIED_CANDIDATE_REPORT_SHA_MISMATCH")
    if str(candidate.get("sidecar_sha256") or "") != str(
        challenger["winning_sidecar_sha256"]
    ):
        raise RuntimeError("PROMOTION_QUALIFIED_CANDIDATE_SIDECAR_SHA_MISMATCH")
    if candidate.get("strategy_contract") != request.get("strategy_contract"):
        raise RuntimeError("PROMOTION_QUALIFIED_CANDIDATE_STRATEGY_CONTRACT_MISMATCH")
    if candidate.get("strategy_geometry") != request.get("strategy_geometry"):
        raise RuntimeError("PROMOTION_QUALIFIED_CANDIDATE_GEOMETRY_MISMATCH")
    if not _same_params(candidate.get("params") or {}, challenger["params"]):
        raise RuntimeError("PROMOTION_QUALIFIED_CANDIDATE_PARAM_MISMATCH")

    kpi = {
        "profit_factor": candidate.get("profit_factor"),
        "recovery_factor": candidate.get("recovery_factor"),
        "mean_r": candidate.get("mean_r"),
        "weighted_r": candidate.get("weighted_r"),
        "trades": candidate.get("trades"),
        "required_trades": candidate.get("required_trades"),
    }
    if kpi != challenger["kpi"]:
        raise RuntimeError("PROMOTION_QUALIFIED_CANDIDATE_KPI_MISMATCH")
    if candidate.get("hard_gates") != challenger["hard_gates"]:
        raise RuntimeError("PROMOTION_QUALIFIED_CANDIDATE_GATES_MISMATCH")

    provenance = challenger.get("provenance")
    if not isinstance(provenance, dict):
        raise RuntimeError("PROMOTION_QUALIFIED_PROVENANCE_MISSING")
    if provenance.get("selection_authority") != "OWNER_EXPLICIT_SELECTION":
        raise RuntimeError("PROMOTION_QUALIFIED_PROVENANCE_AUTHORITY_INVALID")
    if str(provenance.get("report_sha256") or "") != str(
        challenger["winning_xml_sha256"]
    ):
        raise RuntimeError("PROMOTION_QUALIFIED_PROVENANCE_REPORT_SHA_MISMATCH")
    if str(provenance.get("sidecar_sha256") or "") != str(
        challenger["winning_sidecar_sha256"]
    ):
        raise RuntimeError("PROMOTION_QUALIFIED_PROVENANCE_SIDECAR_SHA_MISMATCH")

    return {
        "job_id": expected_identity["job_id"],
        "round": expected_identity["round"],
        "pass": expected_identity["pass"],
        "request": request,
        "params": challenger["params"],
        "kpi": challenger["kpi"],
        "hard_gates": challenger["hard_gates"],
        "role_origin": ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE,
    }


def _verify_challenger_optimizer_source(
    challenger: dict[str, Any],
    *,
    path: Path = DATABASE_PATH,
    deep_owner_selection: bool = True,
) -> dict[str, Any]:
    role = str(challenger.get("role_origin") or ROLE_OPTIMIZER_WINNER)
    job_id = str(challenger["source_job_id"])
    round_no = int(challenger["source_round"])
    pass_no = int(challenger["source_pass"])

    if role == ROLE_OPTIMIZER_WINNER:
        return verify_optimizer_winner(
            job_id,
            expected_round=round_no,
            expected_pass=pass_no,
        )

    if role != ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE:
        raise RuntimeError("PROMOTION_CHALLENGER_ROLE_ORIGIN_UNSUPPORTED")

    if not deep_owner_selection:
        return _retained_owner_selected_source(challenger)

    candidate = revalidate_candidate_for_registration(
        job_id,
        round_no,
        pass_no,
        path=path,
    )
    return {
        "job_id": str(candidate["job_id"]),
        "round": int(candidate["round"]),
        "pass": int(candidate["pass"]),
        "request": candidate["request"],
        "params": candidate["params"],
        "kpi": {
            "profit_factor": candidate["profit_factor"],
            "recovery_factor": candidate["recovery_factor"],
            "mean_r": candidate["mean_r"],
            "weighted_r": candidate["weighted_r"],
            "trades": candidate["trades"],
            "required_trades": candidate["required_trades"],
        },
        "hard_gates": candidate["hard_gates"],
        "role_origin": role,
    }


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".promotion-tmp")
    shutil.copy2(source, temp)
    os.replace(temp, destination)


def _promotion_id(challenger_id: str) -> str:
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    suffix = hashlib.sha256(
        f"{challenger_id}:{now.isoformat(timespec='microseconds')}".encode("utf-8")
    ).hexdigest()[:8]
    return f"PROMOTE-{stamp}-{suffix}"


def _archive_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_UTC")


def _snapshot_file(name: str, path: Path, recovery: Path) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": name,
        "path": str(path),
        "exists": path.is_file(),
        "sha256": _sha(path) if path.is_file() else None,
        "backup": None,
    }
    if path.is_file():
        backup = recovery / "before" / name
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        item["backup"] = str(backup)
    return item


def _restore_file(item: dict[str, Any]) -> None:
    path = Path(str(item["path"]))
    if item["exists"]:
        backup = Path(str(item["backup"]))
        if not backup.is_file():
            raise RuntimeError(f"PROMOTION_ROLLBACK_BACKUP_MISSING:{item['name']}")
        _atomic_copy(backup, path)
        if _sha(path) != item["sha256"]:
            raise RuntimeError(f"PROMOTION_ROLLBACK_HASH_MISMATCH:{item['name']}")
    else:
        path.unlink(missing_ok=True)


def _compile_champion(
    *,
    champion_ea: Path,
    source_request: dict[str, Any],
    promotion_id: str,
    evidence_stage: Path,
) -> dict[str, Any]:
    mt5 = source_request["mt5"]
    data_root = Path(mt5["data_root"])
    metaeditor = Path(mt5["metaeditor"])
    if not metaeditor.is_file():
        raise RuntimeError("PROMOTION_METAEDITOR_UNAVAILABLE")
    compile_dir = (
        data_root / "MQL5" / "Experts" / EXPERT_SUBDIR / f".promotion-{promotion_id}"
    )
    if compile_dir.exists():
        shutil.rmtree(compile_dir)
    compile_dir.mkdir(parents=True, exist_ok=False)
    compile_source = compile_dir / f"{EA_STEM}.mq5"
    shutil.copy2(champion_ea, compile_source)
    expected_source_sha = _sha(champion_ea)
    if _sha(compile_source) != expected_source_sha:
        raise RuntimeError("PROMOTION_COMPILE_SOURCE_HASH_MISMATCH")

    ex5 = compile_source.with_suffix(".ex5")
    log_path = compile_source.with_suffix(".log")
    ex5.unlink(missing_ok=True)
    log_path.unlink(missing_ok=True)
    started_ns = time.time_ns()
    command = [str(metaeditor), f"/compile:{compile_source}", "/log"]
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log_text = _read_text_flexible(log_path)
    if not log_text:
        log_text = (process.stdout or "") + (process.stderr or "")
    summary = compile_summary(log_text)
    if not summary["found"]:
        raise RuntimeError("PROMOTION_METAEDITOR_COMPILE_SUMMARY_MISSING")
    if int(summary["errors"]) != 0 or int(summary["warnings"]) != 0:
        raise RuntimeError(
            "PROMOTION_METAEDITOR_COMPILE_NOT_CLEAN:"
            f"errors={summary['errors']}:warnings={summary['warnings']}"
        )
    if not ex5.is_file():
        raise RuntimeError("PROMOTION_METAEDITOR_EX5_MISSING")
    if int(ex5.stat().st_mtime_ns) < int(started_ns):
        raise RuntimeError("PROMOTION_METAEDITOR_EX5_NOT_FRESH")

    compile_evidence = evidence_stage / "compile"
    compile_evidence.mkdir(parents=True, exist_ok=True)
    (compile_evidence / "compile_command.txt").write_text(
        subprocess.list2cmdline(command) + "\n",
        encoding="utf-8",
    )
    (compile_evidence / "metaeditor_compile.txt").write_text(
        log_text,
        encoding="utf-8",
    )
    shutil.copy2(compile_source, compile_evidence / "compiled_source.mq5")
    shutil.copy2(ex5, compile_evidence / "compiled_ea.ex5")
    result = {
        "status": "PASS",
        "metaeditor": str(metaeditor),
        "command": subprocess.list2cmdline(command),
        "process_returncode": process.returncode,
        "returncode_authority": "DIAGNOSTIC_ONLY",
        "compile_summary": summary,
        "source_sha256": expected_source_sha,
        "compiled_source_sha256": _sha(compile_source),
        "ex5_sha256": _sha(ex5),
        "ex5_mtime_ns": int(ex5.stat().st_mtime_ns),
        "compile_started_ns": int(started_ns),
        "fresh_ex5": True,
        "compile_temp_dir": str(compile_dir),
        "compiled_ex5": str(ex5),
    }
    _json_write(compile_evidence / "compile_context.json", result)
    return result


def _stage_baseline_archive(
    stage_root: Path,
    *,
    promotion_id: str,
    new_champion_id: str,
) -> tuple[str, Path]:
    archive_id = f"BASELINE-MTF-V2_{_archive_stamp()}"
    root = stage_root / "history" / archive_id
    root.mkdir(parents=True, exist_ok=False)
    ea = root / "Max_MTF.mq5"
    shutil.copy2(EA_BASELINE, ea)
    if _sha(ea) != current_baseline_sha256():
        raise RuntimeError("BASELINE_ARCHIVE_HASH_MISMATCH")
    metadata = {
        "schema": "MAX_REBUILD_BASELINE_ARCHIVE_V1",
        "archive_id": archive_id,
        "status": "ARCHIVED_PRE_FIRST_STRATEGY_CHAMPION",
        "source": "ea/baseline/Max_MTF.mq5",
        "ea_sha256": _sha(ea),
        "archived_utc": utc_now(),
        "reason": "FIRST_STRATEGY_CHAMPION_PROMOTION",
        "promotion_id": promotion_id,
        "new_champion_id": new_champion_id,
    }
    _json_write(root / "baseline.json", metadata)
    write_hash_manifest(
        root,
        schema="MAX_REBUILD_BASELINE_ARCHIVE_MANIFEST_V1",
        identity={"archive_id": archive_id},
    )
    return archive_id, root


def _stage_former_champion_archive(
    stage_root: Path,
    *,
    promotion_id: str,
    current: dict[str, Any],
    replaced_by: str,
) -> tuple[str, Path]:
    archive_id = f"CHAMPION-{current['strategy_id']}_{_archive_stamp()}"
    root = stage_root / "history" / archive_id
    root.mkdir(parents=True, exist_ok=False)
    source = CHAMPION_CURRENT_ROOT / "Max_MTF.mq5"
    preset = CHAMPION_CURRENT_ROOT / "Max_MTF.set"
    if not source.is_file() or not preset.is_file():
        raise RuntimeError("CURRENT_CHAMPION_PROJECT_ARTIFACT_MISSING")
    shutil.copy2(source, root / "Max_MTF.mq5")
    shutil.copy2(preset, root / "Max_MTF.set")
    metadata = {
        "schema": "MAX_REBUILD_FORMER_CHAMPION_ARCHIVE_V1",
        "archive_id": archive_id,
        "status": "FORMER_CHAMPION",
        "strategy_id": current["strategy_id"],
        "source_challenger_id": current["source_challenger_id"],
        "source_job_id": current["source_job_id"],
        "source_round": current["source_round"],
        "source_pass": current["source_pass"],
        "params": current["params"],
        "kpi": current["kpi"],
        "hard_gates": current["hard_gates"],
        "promoted_utc": current["promoted_utc"],
        "demoted_utc": utc_now(),
        "replaced_by": replaced_by,
        "promotion_id": promotion_id,
        "ea_sha256": _sha(root / "Max_MTF.mq5"),
        "set_sha256": _sha(root / "Max_MTF.set"),
    }
    _json_write(root / "former_champion.json", metadata)
    write_hash_manifest(
        root,
        schema="MAX_REBUILD_FORMER_CHAMPION_ARCHIVE_MANIFEST_V1",
        identity={"archive_id": archive_id, "strategy_id": current["strategy_id"]},
    )
    return archive_id, root


def _capture_before_state(
    *,
    promotion_id: str,
    source_request: dict[str, Any],
    current: dict[str, Any] | None,
    recovery: Path,
    baseline_archive_final: Path | None,
    former_archive_final: Path | None,
) -> dict[str, Any]:
    data_root = Path(source_request["mt5"]["data_root"])
    expert_dir = data_root / "MQL5" / "Experts" / EXPERT_SUBDIR
    tester_dir = data_root / "MQL5" / "Profiles" / "Tester"
    paths = {
        "project_champion_ea": CHAMPION_CURRENT_ROOT / "Max_MTF.mq5",
        "project_champion_set": CHAMPION_CURRENT_ROOT / "Max_MTF.set",
        "mt5_source": expert_dir / f"{EA_STEM}.mq5",
        "mt5_ex5": expert_dir / f"{EA_STEM}.ex5",
        "tester_set": tester_dir / TESTER_SET,
    }
    files = {
        name: _snapshot_file(name, file_path, recovery)
        for name, file_path in paths.items()
    }
    return {
        "schema": "MAX_REBUILD_PROMOTION_BEFORE_STATE_V1",
        "promotion_id": promotion_id,
        "captured_utc": utc_now(),
        "current_champion_id": current["strategy_id"] if current else None,
        "baseline_sha256": _sha(EA_BASELINE),
        "files": files,
        "planned_baseline_archive": (
            str(baseline_archive_final) if baseline_archive_final else None
        ),
        "planned_former_archive": (
            str(former_archive_final) if former_archive_final else None
        ),
        "live_authority": "NONE",
    }


def _restore_before_state(before_state: dict[str, Any]) -> None:
    errors: list[str] = []
    for item in before_state["files"].values():
        try:
            _restore_file(item)
        except Exception as exc:
            errors.append(str(exc))
    for key in ("planned_baseline_archive", "planned_former_archive"):
        raw = before_state.get(key)
        if raw:
            try:
                history = Path(str(raw))
                if history.exists():
                    shutil.rmtree(history)
            except Exception as exc:
                errors.append(str(exc))
    if errors:
        raise RuntimeError("PROMOTION_ROLLBACK_FAILED:" + "|".join(errors))


def _commit_history_archive(staged: Path, final: Path) -> None:
    if final.exists():
        raise RuntimeError(f"PROMOTION_HISTORY_ARCHIVE_EXISTS:{final.name}")
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged, final)
    verify_hash_manifest(final)


def _cleanup_compile_temp(compile_result: dict[str, Any] | None) -> None:
    if compile_result and compile_result.get("compile_temp_dir"):
        shutil.rmtree(
            Path(str(compile_result["compile_temp_dir"])),
            ignore_errors=True,
        )


def _post_deployment_state(
    *,
    source_request: dict[str, Any],
    params: dict[str, Any],
    champion_ea_sha: str,
    champion_set_sha: str,
    ex5_sha: str,
) -> dict[str, Any]:
    data_root = Path(source_request["mt5"]["data_root"])
    expert_dir = data_root / "MQL5" / "Experts" / EXPERT_SUBDIR
    deployed_source = expert_dir / f"{EA_STEM}.mq5"
    deployed_ex5 = expert_dir / f"{EA_STEM}.ex5"
    tester_set = data_root / "MQL5" / "Profiles" / "Tester" / TESTER_SET
    if _sha(EA_BASELINE) != current_baseline_sha256():
        raise RuntimeError("BASELINE_EA_SHA_MISMATCH_AFTER_DEPLOYMENT")
    if _sha(deployed_source) != champion_ea_sha:
        raise RuntimeError("PROMOTION_DEPLOYED_SOURCE_HASH_MISMATCH")
    if _sha(deployed_ex5) != ex5_sha:
        raise RuntimeError("PROMOTION_DEPLOYED_EX5_HASH_MISMATCH")
    if _sha(tester_set) != champion_set_sha:
        raise RuntimeError("PROMOTION_TESTER_SET_HASH_MISMATCH")
    verify_champion_set(tester_set, params, source_request)
    return {
        "project_ea": _relative(CHAMPION_CURRENT_ROOT / "Max_MTF.mq5"),
        "project_ea_sha256": champion_ea_sha,
        "project_set": _relative(CHAMPION_CURRENT_ROOT / "Max_MTF.set"),
        "project_set_sha256": champion_set_sha,
        "mt5_source": str(deployed_source),
        "mt5_source_sha256": _sha(deployed_source),
        "mt5_ex5": str(deployed_ex5),
        "mt5_ex5_sha256": _sha(deployed_ex5),
        "tester_set": str(tester_set),
        "tester_set_sha256": _sha(tester_set),
        "baseline_sha256": _sha(EA_BASELINE),
        "strategy_contract": source_request["strategy_contract"],
        "strategy_geometry": source_request["strategy_geometry"],
        "fixed_execution_authority": source_request.get("fixed_execution_authority"),
        "risk_pct": params.get("InpRiskPct"),
        "live_authority": "NONE",
    }


def _stage_evidence(
    evidence_stage: Path,
    *,
    promotion_id: str,
    challenger_id: str,
    expected_manifest_sha256: str,
    expected_current_champion_id: str | None,
    before_state: dict[str, Any],
    challenger_integrity: dict[str, Any],
    source: dict[str, Any],
    deployment_plan: dict[str, Any],
) -> None:
    evidence_stage.mkdir(parents=True, exist_ok=False)
    _json_write(
        evidence_stage / "request.json",
        {
            "schema": "MAX_REBUILD_PROMOTION_REQUEST_V1",
            "promotion_id": promotion_id,
            "challenger_id": challenger_id,
            "expected_challenger_manifest_sha256": expected_manifest_sha256,
            "expected_current_champion_id": expected_current_champion_id,
            "confirmation": "OWNER_EXPLICIT_CONFIRM_PROMOTION",
            "request_utc": utc_now(),
        },
    )
    _json_write(
        evidence_stage / "preflight.json",
        {
            "schema": "MAX_REBUILD_PROMOTION_PREFLIGHT_V1",
            "baseline_sha256": _sha(EA_BASELINE),
            "challenger_integrity": challenger_integrity["status"],
            "challenger_manifest_sha256": challenger_integrity["manifest_sha256"],
            "current_champion_id": expected_current_champion_id,
            "source_job_id": source["job_id"],
            "source_round": source["round"],
            "source_pass": source["pass"],
            "schema_version": 5,
            "active_optimizer": None,
            "scientist_authority": "NONE",
            "live_authority": "NONE",
        },
    )
    _json_write(evidence_stage / "before_state.json", before_state)
    _json_write(evidence_stage / "challenger_integrity.json", challenger_integrity)
    _json_write(evidence_stage / "deployment_plan.json", deployment_plan)


def _finalize_committed_evidence(
    promotion: dict[str, Any],
    champion: dict[str, Any],
) -> Path:
    final = ROOT / str(promotion["evidence_path"])
    if final.is_dir():
        verify_hash_manifest(final)
        return final
    recovery = Path(str(promotion["recovery_path"]))
    evidence_stage = recovery / "evidence"
    if not evidence_stage.is_dir():
        raise RuntimeError("PROMOTION_COMMITTED_EVIDENCE_STAGING_MISSING")
    after = {
        "schema": "MAX_REBUILD_PROMOTION_AFTER_STATE_V1",
        "promotion_id": promotion["promotion_id"],
        "promotion_state": "COMMITTED",
        "current_champion_id": champion["strategy_id"],
        "champion_status": champion["status"],
        "source_challenger_id": champion["source_challenger_id"],
        "source_job_id": champion["source_job_id"],
        "source_round": champion["source_round"],
        "source_pass": champion["source_pass"],
        "champion_ea_sha256": champion["champion_ea_sha256"],
        "champion_set_sha256": champion["champion_set_sha256"],
        "deployed_ea_sha256": champion["deployed_ea_sha256"],
        "deployed_ex5_sha256": champion["deployed_ex5_sha256"],
        "tester_set_sha256": champion["tester_set_sha256"],
        "baseline_sha256": _sha(EA_BASELINE),
        "live_authority": "NONE",
    }
    _json_write(evidence_stage / "after_state.json", after)
    _json_write(
        evidence_stage / "promotion.json",
        {
            "schema": "MAX_REBUILD_STRATEGY_PROMOTION_V1",
            "promotion_id": promotion["promotion_id"],
            "challenger_id": promotion["challenger_id"],
            "previous_champion_id": promotion["previous_champion_id"],
            "new_champion_id": champion["strategy_id"],
            "state": "COMMITTED",
            "created_utc": promotion["created_utc"],
            "completed_utc": promotion["completed_utc"],
            "baseline_archive_id": promotion["baseline_archive_id"],
            "former_champion_archive_id": promotion["former_champion_archive_id"],
            "authority_source": "OWNER_MANUAL_STRATEGY_PROMOTION",
            "scientist_calls": 0,
            "live_authority": "NONE",
        },
    )
    write_hash_manifest(
        evidence_stage,
        schema="MAX_REBUILD_STRATEGY_PROMOTION_MANIFEST_V1",
        identity={
            "promotion_id": promotion["promotion_id"],
            "champion_id": champion["strategy_id"],
        },
    )
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(evidence_stage, final)
    verify_hash_manifest(final)
    return final


def _promotion_public(promotion: dict[str, Any]) -> dict[str, Any]:
    return {
        "promotion_id": promotion["promotion_id"],
        "challenger_id": promotion["challenger_id"],
        "previous_champion_id": promotion["previous_champion_id"],
        "new_champion_id": promotion["new_champion_id"],
        "state": promotion["state"],
        "created_utc": promotion["created_utc"],
        "completed_utc": promotion["completed_utc"],
        "failed_utc": promotion["failed_utc"],
        "baseline_archive_id": promotion["baseline_archive_id"],
        "former_champion_archive_id": promotion["former_champion_archive_id"],
        "compile": promotion["compile"],
        "parity": promotion["parity"],
        "deployment": promotion["deployment"],
        "error": promotion["error"],
        "rollback_status": promotion["rollback_status"],
    }


def promote_strategy_challenger(
    challenger_id: str,
    *,
    expected_challenger_manifest_sha256: str,
    expected_current_champion_id: str | None,
    confirmed: bool,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if not confirmed:
        raise RuntimeError("PROMOTION_EXPLICIT_CONFIRMATION_REQUIRED")
    migrate_m04(path)
    if active_job(path=path) is not None:
        raise RuntimeError("PROMOTION_BLOCKED_OPTIMIZER_ACTIVE")
    if active_promotion(path=path) is not None:
        raise RuntimeError("PROMOTION_ALREADY_ACTIVE")
    if _sha(EA_BASELINE) != current_baseline_sha256():
        raise RuntimeError("PROMOTION_BASELINE_SHA_MISMATCH")

    challenger = get_challenger(challenger_id, path=path)
    if challenger is None:
        raise FileNotFoundError("PROMOTION_CHALLENGER_UNKNOWN")
    if challenger["status"] != "CHALLENGER":
        raise RuntimeError("PROMOTION_CHALLENGER_NOT_ACTIVE")
    current = current_champion(path=path)
    actual_current_id = current["strategy_id"] if current else None
    if actual_current_id != expected_current_champion_id:
        raise RuntimeError("PROMOTION_CONFIRMATION_STALE")
    if challenger["manifest_sha256"] != expected_challenger_manifest_sha256:
        raise RuntimeError("PROMOTION_CONFIRMATION_STALE")

    challenger_integrity = verify_challenger_bundle(challenger_id)
    if challenger_integrity["status"] != "VERIFIED":
        raise RuntimeError("PROMOTION_CHALLENGER_INTEGRITY_FAIL")
    if challenger_integrity["manifest_sha256"] != expected_challenger_manifest_sha256:
        raise RuntimeError("PROMOTION_CONFIRMATION_STALE")
    source = _verify_challenger_optimizer_source(challenger, path=path)
    if not _same_params(source["params"], challenger["params"]):
        raise RuntimeError("PROMOTION_WINNER_CHALLENGER_PARAM_MISMATCH")
    if challenger["champion_mutation"] != "NONE":
        raise RuntimeError("PROMOTION_CHALLENGER_METADATA_MUTATION_INVALID")

    promotion_id = _promotion_id(challenger_id)
    recovery = PROMOTION_RECOVERY_ROOT / promotion_id
    if recovery.exists():
        raise RuntimeError("PROMOTION_RECOVERY_ID_COLLISION")
    recovery.mkdir(parents=True, exist_ok=False)
    stage_root = recovery / "stage"
    stage_root.mkdir(parents=True, exist_ok=False)
    evidence_stage = recovery / "evidence"

    baseline_archive_id: str | None = None
    baseline_archive_stage: Path | None = None
    former_archive_id: str | None = None
    former_archive_stage: Path | None = None
    if current is None:
        baseline_archive_id, baseline_archive_stage = _stage_baseline_archive(
            stage_root,
            promotion_id=promotion_id,
            new_champion_id=challenger_id,
        )
    else:
        former_archive_id, former_archive_stage = _stage_former_champion_archive(
            stage_root,
            promotion_id=promotion_id,
            current=current,
            replaced_by=challenger_id,
        )

    baseline_archive_final = (
        STRATEGY_HISTORY_ROOT / baseline_archive_id
        if baseline_archive_id
        else None
    )
    former_archive_final = (
        STRATEGY_HISTORY_ROOT / former_archive_id
        if former_archive_id
        else None
    )
    before_state = _capture_before_state(
        promotion_id=promotion_id,
        source_request=source["request"],
        current=current,
        recovery=recovery,
        baseline_archive_final=baseline_archive_final,
        former_archive_final=former_archive_final,
    )
    try:
        create_prepared_promotion(
            promotion_id=promotion_id,
            challenger_id=challenger_id,
            previous_champion_id=actual_current_id,
            expected_manifest_sha256=expected_challenger_manifest_sha256,
            before_state=before_state,
            recovery_path=str(recovery),
            path=path,
        )
    except Exception:
        shutil.rmtree(recovery, ignore_errors=True)
        raise

    compile_result: dict[str, Any] | None = None
    try:
        challenger_bundle = ROOT / challenger["bundle_path"]
        challenger_ea = challenger_bundle / f"Max_Challenger_{challenger_id}.mq5"
        stage_ea = stage_root / "champion" / "Max_MTF.mq5"
        stage_set = stage_root / "champion" / "Max_MTF.set"
        stage_ea.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(challenger_ea, stage_ea)
        if _sha(stage_ea) != challenger["challenger_ea_sha256"]:
            raise RuntimeError("PROMOTION_CHAMPION_SOURCE_NOT_CHALLENGER_BYTE_EXACT")
        write_champion_set(stage_set, source["request"], source["params"])
        parity = verify_champion_parity(
            stage_ea,
            stage_set,
            source["params"],
            source["request"],
        )
        data_root = Path(source["request"]["mt5"]["data_root"])
        deployment_plan = {
            "project_champion_ea": _relative(CHAMPION_CURRENT_ROOT / "Max_MTF.mq5"),
            "project_champion_set": _relative(CHAMPION_CURRENT_ROOT / "Max_MTF.set"),
            "mt5_source": str(
                data_root / "MQL5" / "Experts" / EXPERT_SUBDIR / f"{EA_STEM}.mq5"
            ),
            "mt5_ex5": str(
                data_root / "MQL5" / "Experts" / EXPERT_SUBDIR / f"{EA_STEM}.ex5"
            ),
            "tester_set": str(
                data_root / "MQL5" / "Profiles" / "Tester" / TESTER_SET
            ),
            "baseline_archive": (
                _relative(baseline_archive_final) if baseline_archive_final else None
            ),
            "former_champion_archive": (
                _relative(former_archive_final) if former_archive_final else None
            ),
            "live_authority": "NONE",
        }
        _stage_evidence(
            evidence_stage,
            promotion_id=promotion_id,
            challenger_id=challenger_id,
            expected_manifest_sha256=expected_challenger_manifest_sha256,
            expected_current_champion_id=actual_current_id,
            before_state=before_state,
            challenger_integrity=challenger_integrity,
            source=source,
            deployment_plan=deployment_plan,
        )
        champion_evidence = evidence_stage / "champion"
        champion_evidence.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stage_ea, champion_evidence / "Max_MTF.mq5")
        shutil.copy2(stage_set, champion_evidence / "Max_MTF.set")
        _json_write(evidence_stage / "parity.json", parity)
        update_promotion(
            promotion_id,
            state="ARTIFACTS_STAGED",
            parity=parity,
            baseline_archive_id=baseline_archive_id,
            former_champion_archive_id=former_archive_id,
            path=path,
        )

        compile_result = _compile_champion(
            champion_ea=stage_ea,
            source_request=source["request"],
            promotion_id=promotion_id,
            evidence_stage=evidence_stage,
        )
        _json_write(evidence_stage / "compile_result.json", compile_result)
        compiled_ex5 = Path(str(compile_result["compiled_ex5"]))

        project_ea = CHAMPION_CURRENT_ROOT / "Max_MTF.mq5"
        project_set = CHAMPION_CURRENT_ROOT / "Max_MTF.set"
        mt5_source = Path(deployment_plan["mt5_source"])
        mt5_ex5 = Path(deployment_plan["mt5_ex5"])
        tester_set = Path(deployment_plan["tester_set"])
        _atomic_copy(stage_ea, project_ea)
        _atomic_copy(stage_set, project_set)
        _atomic_copy(stage_ea, mt5_source)
        _atomic_copy(compiled_ex5, mt5_ex5)
        _atomic_copy(stage_set, tester_set)
        if baseline_archive_stage and baseline_archive_final:
            _commit_history_archive(baseline_archive_stage, baseline_archive_final)
        if former_archive_stage and former_archive_final:
            _commit_history_archive(former_archive_stage, former_archive_final)

        deployment = _post_deployment_state(
            source_request=source["request"],
            params=source["params"],
            champion_ea_sha=_sha(project_ea),
            champion_set_sha=_sha(project_set),
            ex5_sha=_sha(mt5_ex5),
        )
        parity = verify_champion_parity(
            project_ea,
            project_set,
            source["params"],
            source["request"],
        )
        verify_champion_set(tester_set, source["params"], source["request"])
        _json_write(evidence_stage / "deployment.json", deployment)
        update_promotion(
            promotion_id,
            state="FILES_COMMITTED",
            compile_result=compile_result,
            parity=parity,
            deployment=deployment,
            baseline_archive_id=baseline_archive_id,
            former_champion_archive_id=former_archive_id,
            path=path,
        )

        promoted_utc = utc_now()
        evidence_relative = (
            Path("evidence") / "m04" / "promotions" / promotion_id
        ).as_posix()
        post_state = {
            "current_champion_id": challenger_id,
            "baseline_sha256": _sha(EA_BASELINE),
            "challenger_status": "PROMOTED",
            "previous_champion_final_status": (
                "FORMER" if actual_current_id else None
            ),
            "previous_challenger_final_status": (
                "CHALLENGER" if actual_current_id else None
            ),
            "promotion_status": "COMMITTED",
            "deployment": deployment,
        }
        champion_payload = {
            "source_job_id": challenger["source_job_id"],
            "source_round": challenger["source_round"],
            "source_pass": challenger["source_pass"],
            "params": source["params"],
            "kpi": challenger["kpi"],
            "hard_gates": challenger["hard_gates"],
            "champion_ea_sha256": _sha(project_ea),
            "champion_set_sha256": _sha(project_set),
            "deployed_ea_sha256": _sha(mt5_source),
            "deployed_ex5_sha256": _sha(mt5_ex5),
            "tester_set_sha256": _sha(tester_set),
            "promoted_utc": promoted_utc,
            "artifact_path": _relative(CHAMPION_CURRENT_ROOT),
        }
        champion = commit_promotion_authority(
            promotion_id,
            champion=champion_payload,
            post_state=post_state,
            baseline_archive_id=baseline_archive_id,
            former_champion_archive_id=former_archive_id,
            compile_result=compile_result,
            parity=parity,
            deployment=deployment,
            evidence_path=evidence_relative,
            path=path,
        )
        promotion = get_promotion(promotion_id, path=path)
        if promotion is None or promotion["state"] != "COMMITTED":
            raise RuntimeError("PROMOTION_COMMIT_STATE_MISSING")
        final_evidence = _finalize_committed_evidence(promotion, champion)
        integrity = verify_current_strategy_champion(path=path)
        _cleanup_compile_temp(compile_result)
        if recovery.exists():
            shutil.rmtree(recovery, ignore_errors=True)
        return {
            "promotion_id": promotion_id,
            "status": "COMMITTED",
            "previous_champion": actual_current_id,
            "new_champion": challenger_id,
            "baseline_archive": baseline_archive_id,
            "former_champion_archive": former_archive_id,
            "compile": compile_result,
            "parity": parity,
            "deployment": deployment,
            "champion": champion,
            "champion_integrity": integrity,
            "evidence_path": _relative(final_evidence),
            "live_authority": "NONE",
            "scientist_calls": 0,
        }
    except Exception as exc:
        current_promotion = get_promotion(promotion_id, path=path)
        if current_promotion and current_promotion["state"] == "COMMITTED":
            champion = current_champion(path=path)
            if champion is None:
                raise RuntimeError(
                    "PROMOTION_AUTHORITY_INTEGRITY_FAILURE:NO_CURRENT_CHAMPION"
                ) from exc
            _finalize_committed_evidence(current_promotion, champion)
            _cleanup_compile_temp(compile_result)
            if recovery.exists():
                shutil.rmtree(recovery, ignore_errors=True)
            return {
                "promotion_id": promotion_id,
                "status": "COMMITTED",
                "previous_champion": actual_current_id,
                "new_champion": champion["strategy_id"],
                "champion": champion,
                "champion_integrity": verify_current_strategy_champion(path=path),
                "recovered_after_commit": True,
                "live_authority": "NONE",
                "scientist_calls": 0,
            }

        rollback_error: Exception | None = None
        try:
            _restore_before_state(before_state)
            _cleanup_compile_temp(compile_result)
        except Exception as restore_exc:
            rollback_error = restore_exc
        if rollback_error is None:
            update_promotion(
                promotion_id,
                state="ROLLED_BACK",
                error=str(exc),
                rollback_status="RESTORED_PREVIOUS_AUTHORITY",
                mark_failed=True,
                path=path,
            )
        else:
            update_promotion(
                promotion_id,
                state="FAILED",
                error=f"{exc}; rollback={rollback_error}",
                rollback_status="FAILED",
                mark_failed=True,
                path=path,
            )
            raise RuntimeError(
                f"PROMOTION_ROLLBACK_FAILED:{rollback_error}"
            ) from exc
        raise


def verify_current_strategy_champion(
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_m04(path)
    champion = current_champion(path=path)
    if champion is None:
        return {
            "status": "NONE",
            "current": None,
            "baseline_sha256": _sha(EA_BASELINE),
            "live_authority": "NONE",
        }
    if _sha(EA_BASELINE) != current_baseline_sha256():
        raise RuntimeError("CHAMPION_BASELINE_AUTHORITY_TAMPERED")
    challenger = get_challenger(champion["source_challenger_id"], path=path)
    if challenger is None or challenger["status"] != "PROMOTED":
        raise RuntimeError("CHAMPION_SOURCE_CHALLENGER_STATE_INVALID")
    challenger_integrity = verify_challenger_bundle(
        champion["source_challenger_id"],
        allow_promoted=True,
    )
    source = _verify_challenger_optimizer_source(
        challenger,
        path=path,
        deep_owner_selection=False,
    )
    if not _same_params(champion["params"], source["params"]):
        raise RuntimeError("CHAMPION_SOURCE_PARAM_LINEAGE_MISMATCH")

    project_ea = CHAMPION_CURRENT_ROOT / "Max_MTF.mq5"
    project_set = CHAMPION_CURRENT_ROOT / "Max_MTF.set"
    if not project_ea.is_file() or not project_set.is_file():
        raise RuntimeError("CURRENT_CHAMPION_PROJECT_ARTIFACT_MISSING")
    if _sha(project_ea) != champion["champion_ea_sha256"]:
        raise RuntimeError("CURRENT_CHAMPION_EA_HASH_MISMATCH")
    if _sha(project_set) != champion["champion_set_sha256"]:
        raise RuntimeError("CURRENT_CHAMPION_SET_HASH_MISMATCH")
    parity = verify_champion_parity(
        project_ea,
        project_set,
        champion["params"],
        source["request"],
    )
    deployment = champion["deployment"]
    if deployment.get("strategy_contract") != source["request"]["strategy_contract"]:
        raise RuntimeError("CURRENT_CHAMPION_STRATEGY_CONTRACT_MISMATCH")
    if deployment.get("strategy_geometry") != source["request"]["strategy_geometry"]:
        raise RuntimeError("CURRENT_CHAMPION_STRATEGY_GEOMETRY_MISMATCH")
    mt5_source = Path(deployment["mt5_source"])
    mt5_ex5 = Path(deployment["mt5_ex5"])
    tester_set = Path(deployment["tester_set"])
    if not mt5_source.is_file() or _sha(mt5_source) != champion["deployed_ea_sha256"]:
        raise RuntimeError("CURRENT_CHAMPION_DEPLOYED_EA_MISMATCH")
    if _sha(mt5_source) != _sha(project_ea):
        raise RuntimeError("CURRENT_CHAMPION_PROJECT_DEPLOYED_EA_MISMATCH")
    if not mt5_ex5.is_file() or _sha(mt5_ex5) != champion["deployed_ex5_sha256"]:
        raise RuntimeError("CURRENT_CHAMPION_EX5_MISMATCH")
    if not tester_set.is_file() or _sha(tester_set) != champion["tester_set_sha256"]:
        raise RuntimeError("CURRENT_CHAMPION_TESTER_SET_MISMATCH")
    tester = verify_champion_set(
        tester_set,
        champion["params"],
        source["request"],
    )
    return {
        "status": "VERIFIED",
        "current": champion,
        "challenger_integrity": challenger_integrity["status"],
        "parameter_parity": parity,
        "tester_set": tester,
        "baseline_sha256": _sha(EA_BASELINE),
        "project_ea_sha256": _sha(project_ea),
        "project_set_sha256": _sha(project_set),
        "deployed_ea_sha256": _sha(mt5_source),
        "deployed_ex5_sha256": _sha(mt5_ex5),
        "tester_set_sha256": _sha(tester_set),
        "live_authority": "NONE",
    }


def recover_incomplete_promotions(*, path: Path = DATABASE_PATH) -> list[dict[str, Any]]:
    migrate_m04(path)
    recovered: list[dict[str, Any]] = []
    for promotion in list_promotions(path=path):
        if promotion["state"] in PROMOTION_ACTIVE_STATES:
            try:
                _restore_before_state(promotion["before_state"])
                _cleanup_compile_temp(promotion.get("compile"))
                updated = update_promotion(
                    promotion["promotion_id"],
                    state="ROLLED_BACK",
                    error="STARTUP_RECOVERY_BEFORE_DB_AUTHORITY_COMMIT",
                    rollback_status="RESTORED_PREVIOUS_AUTHORITY",
                    mark_failed=True,
                    path=path,
                )
                recovered.append(_promotion_public(updated))
            except Exception as exc:
                update_promotion(
                    promotion["promotion_id"],
                    state="FAILED",
                    error=f"STARTUP_RECOVERY_FAILED:{exc}",
                    rollback_status="FAILED",
                    mark_failed=True,
                    path=path,
                )
                raise RuntimeError(
                    f"PROMOTION_AUTHORITY_INTEGRITY_FAILURE:{promotion['promotion_id']}"
                ) from exc

    current = current_champion(path=path)
    if current is not None:
        integrity = verify_current_strategy_champion(path=path)
        committed = get_promotion(current["promotion_id"], path=path)
        if committed is None or committed["state"] != "COMMITTED":
            raise RuntimeError("PROMOTION_AUTHORITY_INTEGRITY_FAILURE:JOURNAL")
        final = ROOT / str(committed["evidence_path"])
        if not final.is_dir():
            _finalize_committed_evidence(committed, current)
        if integrity["status"] != "VERIFIED":
            raise RuntimeError("PROMOTION_AUTHORITY_INTEGRITY_FAILURE:CHAMPION")
    return recovered


def champion_detail(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    integrity = verify_current_strategy_champion(path=path)
    if integrity["status"] == "NONE":
        return {
            "current": None,
            "status": "NONE",
            "seed_authority": "BASELINE_NOT_CHAMPION",
            "baseline_sha256": integrity["baseline_sha256"],
            "live_authority": "NONE",
        }
    champion = integrity["current"]
    source_challenger = get_challenger(champion["source_challenger_id"], path=path)
    if source_challenger is None:
        raise RuntimeError("CHAMPION_SOURCE_CHALLENGER_UNKNOWN")
    source = _verify_challenger_optimizer_source(
        source_challenger,
        path=path,
        deep_owner_selection=False,
    )
    return {
        "current": champion,
        "status": "CURRENT_STRATEGY_CHAMPION",
        "integrity": integrity,
        "source_kpi": champion["kpi"],
        "source_request": {
            "symbol": source["request"]["symbol"],
            "relative_symbol": source["request"]["relative_symbol"],
            "period": source["request"]["period"],
            "from_date": source["request"]["from_date"],
            "to_date": source["request"]["to_date"],
        },
        "live_authority": "NONE",
    }


def promotion_history(*, path: Path = DATABASE_PATH) -> list[dict[str, Any]]:
    return [_promotion_public(row) for row in list_promotions(path=path)]


def promotion_detail(promotion_id: str, *, path: Path = DATABASE_PATH) -> dict[str, Any]:
    row = get_promotion(promotion_id, path=path)
    if row is None:
        raise FileNotFoundError(promotion_id)
    return _promotion_public(row)
