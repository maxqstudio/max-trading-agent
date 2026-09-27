from __future__ import annotations

import argparse
import json
import math
import traceback
from pathlib import Path
from typing import Any

from .challenger_registry import ensure_challenger_for_winner
from .config import EA_BASELINE
from .optimizer_core import (
    OptimizationPass,
    apply_frozen_gates,
    eligibility_audit,
    optimizer_fitness_for_request,
    parse_optimization_xml,
    select_winner,
    sha256_file,
    unresolved_weighted_contenders,
)
from .optimizer_scientist_transition import _refine_for_next_round
from .optimizer_runtime import (
    ReportPending,
    clear_stale_sidecar,
    commit_round_evidence,
    compile_ea,
    job_evidence_dir,
    launch_mt5,
    prepare_round,
    round_evidence_dir,
    snapshot_compatible_reports,
    stage_raw_round_evidence,
    wait_for_fresh_report,
    write_json,
    write_state_snapshot,
)
from .workflow_contract import (
    OPTIMIZER_TERMINAL_QUALIFIED_POOL,
    optimizer_uses_owner_selection,
)
from .optimizer_store import (
    get_job,
    get_round,
    update_job,
    upsert_round,
)


def _first_blocker(exc: Exception) -> str:
    message = str(exc)
    known = (
        "EA_CHANGED_AFTER_REQUEST_FREEZE",
        "EA_DEPLOYMENT_HASH_MISMATCH",
        "METAEDITOR_UNAVAILABLE",
        "METAEDITOR_COMPILE_SUMMARY_MISSING",
        "METAEDITOR_COMPILE_ERRORS",
        "METAEDITOR_EX5_MISSING",
        "MT5_EXECUTABLE_UNAVAILABLE",
        "MT5_TERMINAL_ALREADY_RUNNING",
        "MT5_EXECUTION_FAILURE",
        "STALE_SIDECAR_CANNOT_BE_CLEARED",
        "UNRESOLVED_WEIGHTED_R_EVIDENCE",
        "ELIGIBILITY_INVARIANT_FAILURE",
    )
    for token in known:
        if token in message:
            return token
    if isinstance(exc, FileNotFoundError):
        if "sidecar" in message.lower() or "weighted-r" in message.lower():
            return "WEIGHTED_R_SIDECAR_MISSING"
        return "REQUIRED_FILE_MISSING"
    if "zero parseable" in message.lower():
        return "XML_ZERO_PARSEABLE_PASSES"
    if "Recovery Factor" in message:
        return "XML_RECOVERY_FACTOR_MISSING"
    if "XML" in message or "xml" in message:
        return "XML_EVIDENCE_INVALID"
    if "nonce" in message.lower():
        return "WEIGHTED_R_NONCE_MISMATCH"
    if "Weighted-R" in message or "weighted" in message.lower():
        return "WEIGHTED_R_EVIDENCE_INVALID"
    return "OPTIMIZER_RUNTIME_FAILURE"


def _diagnostic(job_id: str, stage: str, exc: Exception, round_no: int) -> Path:
    evidence = job_evidence_dir(job_id)
    path = evidence / "diagnostic.json"
    payload = {
        "schema": "MAX_REBUILD_OPTIMIZER_DIAGNOSTIC_V1",
        "job_id": job_id,
        "round": round_no,
        "stage": stage,
        "first_failed_gate": _first_blocker(exc),
        "error": repr(exc),
        "message": str(exc),
        "traceback": traceback.format_exc(),
        "evidence_dir": str(evidence),
    }
    if path.exists():
        path = evidence / f"diagnostic_{len(list(evidence.glob('diagnostic*.json'))) + 1:03d}.json"
    write_json(path, payload)
    return path


def _save_phase(
    job_id: str,
    round_no: int,
    state: dict[str, Any],
    phase: str,
    **fields: Any,
) -> dict[str, Any]:
    payload = {**state, **fields, "phase": phase}
    record = upsert_round(
        job_id,
        round_no,
        phase=phase,
        state=payload,
        report_path=fields.get("report_path"),
        report_sha256=fields.get("report_sha256"),
        sidecar_path=fields.get("sidecar_path"),
        sidecar_sha256=fields.get("sidecar_sha256"),
        parsed_passes=fields.get("parsed_passes"),
        eligible_passes=fields.get("eligible_passes"),
        winner_pass=fields.get("winner_pass"),
    )
    write_state_snapshot(job_id, round_no, record["state"])
    return record["state"]


def _pass_from_payload(payload: dict[str, Any]) -> OptimizationPass:
    data = dict(payload)
    data.pop("eligibility", None)
    for key in (
        "weighted_r",
        "total_initial_risk",
        "r_sum_net",
        "custom_fitness",
        "r_sum_r",
    ):
        if key in data and data.get(key) is None:
            data[key] = float("nan")
    return OptimizationPass(**data)


def _load_parsed_round(
    job_id: str,
    round_no: int,
) -> tuple[list[OptimizationPass], dict[str, Any], OptimizationPass | None]:
    evidence = round_evidence_dir(job_id, round_no)
    passes_obj = json.loads((evidence / "passes.json").read_text(encoding="utf-8"))
    audit = json.loads((evidence / "eligibility_audit.json").read_text(encoding="utf-8"))
    rows = [_pass_from_payload(item) for item in passes_obj["passes"]]
    winner = select_winner(rows)
    return rows, audit, winner


def execute_round(
    request: dict[str, Any],
    *,
    job_id: str,
    round_no: int,
    search_space: dict[str, Any],
    resume: bool,
) -> tuple[list[OptimizationPass], dict[str, Any], OptimizationPass | None]:
    record = get_round(job_id, round_no)
    if record is None:
        state = prepare_round(
            request,
            job_id=job_id,
            round_no=round_no,
            search_space=search_space,
        )
        state = _save_phase(job_id, round_no, state, "PREPARED")
    else:
        state = dict(record["state"])
        search_space = state.get("search_space") or search_space

    phase = str(state.get("phase") or "PREPARED")
    if phase == "PARSED":
        return _load_parsed_round(job_id, round_no)

    if phase == "MT5_RUNNING":
        state = _save_phase(
            job_id,
            round_no,
            state,
            "MT5_COMPLETE_UNCONFIRMED",
            recovery_reason="WORKER_RESTART_DURING_MT5_RUNNING_NO_RELAUNCH",
        )
        phase = "MT5_COMPLETE_UNCONFIRMED"

    if phase == "PREPARED":
        if sha256_file(EA_BASELINE) != request["ea"]["sha256"]:
            raise RuntimeError("EA_CHANGED_AFTER_REQUEST_FREEZE")
        prelaunch = snapshot_compatible_reports(request)
        clear_stale_sidecar(state)
        state = _save_phase(
            job_id,
            round_no,
            state,
            "MT5_RUNNING",
            prelaunch_report_snapshot=prelaunch,
        )
        update_job(
            job_id,
            status="MT5_RUNNING",
            active=True,
            current_round=round_no,
            message=f"Real MT5 native optimization round {round_no}/{request['max_rounds']}",
            mark_started=True,
        )
        returncode = launch_mt5(
            request,
            ini_path=state["ini_path"],
            timeout_sec=21600,
        )
        state = _save_phase(
            job_id,
            round_no,
            state,
            "MT5_COMPLETE",
            mt5_returncode=returncode,
        )
        phase = "MT5_COMPLETE"

    if phase in {
        "MT5_COMPLETE",
        "MT5_COMPLETE_UNCONFIRMED",
        "WAITING_FOR_REPORT",
        "REPORT_READY",
    }:
        report: Path | None = None
        selection_mode = str(state.get("report_selection_mode") or "")
        checkpointed = str(state.get("report_path") or "")
        if checkpointed and Path(checkpointed).is_file():
            report = Path(checkpointed)
            selection_mode = selection_mode or "CHECKPOINTED_REPORT"

        if report is None:
            prelaunch = state.get("prelaunch_report_snapshot")
            if not isinstance(prelaunch, list):
                prelaunch = []
            report, selection_mode = wait_for_fresh_report(
                request,
                prelaunch_snapshot=prelaunch,
                timeout_sec=90,
            )
        if report is None:
            state = _save_phase(
                job_id,
                round_no,
                state,
                "WAITING_FOR_REPORT",
            )
            update_job(
                job_id,
                status="WAITING_FOR_REPORT",
                active=True,
                current_round=round_no,
                message=(
                    "MT5 returned but no fresh compatible Max_MTF.xml is available. "
                    "Resume will resolve evidence without rerunning this round."
                ),
                first_blocker="REPORT_READY",
            )
            raise ReportPending("WAITING_FOR_REPORT")

        state = _save_phase(
            job_id,
            round_no,
            state,
            "REPORT_READY",
            report_path=str(report),
            report_identity={},
            report_selection_mode=selection_mode,
        )
        phase = "REPORT_READY"

    if phase != "REPORT_READY":
        raise RuntimeError(f"Unsupported round recovery phase: {phase}")

    metrics_path = Path(state["optimizer_metrics_path"])
    if not metrics_path.is_file():
        raise FileNotFoundError(
            f"Optimizer Weighted-R sidecar missing: {metrics_path}"
        )

    raw_evidence = stage_raw_round_evidence(
        job_id=job_id,
        round_no=round_no,
        report=Path(state["report_path"]),
        metrics_path=metrics_path,
    )
    state = _save_phase(
        job_id,
        round_no,
        state,
        "REPORT_READY",
        **raw_evidence,
    )
    update_job(
        job_id,
        status="PARSING_RESULTS",
        active=True,
        current_round=round_no,
        message=f"Parsing real MT5 round {round_no} evidence",
    )
    rows = parse_optimization_xml(
        state["report_path"],
        round_no=round_no,
        metrics_path=metrics_path,
        expected_nonce=int(state["optimizer_run_nonce"]),
        fixed_param_values=request["fixed_param_values"],
        optimize_params=request["optimize_params"],
        optimizer_fitness=optimizer_fitness_for_request(request),
    )
    apply_frozen_gates(rows, request)
    audit = eligibility_audit(rows)
    unresolved = unresolved_weighted_contenders(rows)
    winner = select_winner(rows)
    current_pool_workflow = optimizer_uses_owner_selection(request)
    if current_pool_workflow:
        audit = {
            **audit,
            "winner_pass": None,
            "selection_authority": "OWNER_EXPLICIT_QUALIFIED_CANDIDATE_SELECTION",
        }

    if unresolved:
        raise RuntimeError(
            "UNRESOLVED_WEIGHTED_R_EVIDENCE: "
            f"{len(unresolved)} contender(s) pass native gates without Weighted-R evidence"
        )
    if audit["eligible_passes"] > 0 and winner is None:
        raise RuntimeError("ELIGIBILITY_INVARIANT_FAILURE")

    evidence = commit_round_evidence(
        job_id=job_id,
        round_no=round_no,
        report=Path(state["report_path"]),
        metrics_path=metrics_path,
        audit=audit,
        passes_payload=[row.payload() for row in rows],
        report_selection_mode=str(state.get("report_selection_mode") or "IDENTITY_MATCH"),
    )
    _save_phase(
        job_id,
        round_no,
        state,
        "PARSED",
        report_path=evidence["report_path"],
        report_sha256=evidence["report_sha256"],
        sidecar_path=evidence["sidecar_path"],
        sidecar_sha256=evidence["sidecar_sha256"],
        report_identity=evidence["report_identity"],
        report_selection_mode=evidence["report_selection_mode"],
        parsed_passes=audit["parsed_passes"],
        eligible_passes=audit["eligible_passes"],
        winner_pass=(
            None if current_pool_workflow else winner.pass_no if winner else None
        ),
    )
    return rows, audit, winner


def _write_winner(
    request: dict[str, Any],
    *,
    job_id: str,
    round_no: int,
    winner: OptimizationPass,
) -> dict[str, Any]:
    round_record = get_round(job_id, round_no)
    if round_record is None:
        raise RuntimeError("Winner round record missing")
    payload = {
        "schema": "MAX_REBUILD_ELIGIBLE_WINNER_V1",
        "job_id": job_id,
        "round": round_no,
        "mt5_pass": winner.pass_no,
        "ea_sha256": request["ea"]["sha256"],
        "strategy_contract": request["strategy_contract"],
        "strategy_geometry": request["strategy_geometry"],
        "params": winner.params,
        "profit_factor": winner.profit_factor,
        "recovery_factor": winner.recovery_factor,
        "mean_r": winner.expectancy_r,
        "weighted_r": winner.weighted_r,
        "trades": winner.trades,
        "minimum_required_trades": winner.minimum_trades_required,
        "frozen_gates": {
            "min_profit_factor": winner.min_profit_factor_required,
            "min_recovery_factor": winner.min_recovery_factor_required,
            "min_expectancy_r": winner.min_expectancy_r_required,
            "min_weighted_r": winner.min_weighted_r_required,
        },
        "source_report_sha256": round_record["report_sha256"],
        "sidecar_sha256": round_record["sidecar_sha256"],
        "run_nonce": int(round_record["state"]["optimizer_run_nonce"]),
        "ranking_authority": [
            "Weighted R DESC",
            "Mean R DESC",
            "Profit Factor DESC",
            "Recovery Factor DESC",
            "MT5 pass ASC",
        ],
        "scientist": {
            "advisory_only": True,
            "provenance_authority": "ROUND_LINEAGE",
        },
        "challenger_registration": "PENDING_AFTER_WINNER_EVIDENCE",
        "champion_mutation": "NONE",
    }
    target = job_evidence_dir(job_id) / "eligible_winner.json"
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing != payload:
            raise RuntimeError("Existing eligible winner evidence mismatch")
    else:
        write_json(target, payload)
    return payload


def _load_committed_winner(job_id: str) -> dict[str, Any] | None:
    path = job_evidence_dir(job_id) / "eligible_winner.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("ELIGIBLE_WINNER_EVIDENCE_INVALID")
    if str(payload.get("job_id") or "") != job_id:
        raise RuntimeError("ELIGIBLE_WINNER_JOB_ID_MISMATCH")
    job = get_job(job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    request = job["request"]
    if payload.get("strategy_contract") != request.get("strategy_contract"):
        raise RuntimeError("ELIGIBLE_WINNER_STRATEGY_CONTRACT_MISMATCH")
    if payload.get("strategy_geometry") != request.get("strategy_geometry"):
        raise RuntimeError("ELIGIBLE_WINNER_STRATEGY_GEOMETRY_MISMATCH")
    return payload


def _register_committed_winner(
    job_id: str,
    winner_payload: dict[str, Any],
) -> int:
    round_no = int(winner_payload["round"])
    pass_no = int(winner_payload["mt5_pass"])
    update_job(
        job_id,
        status="REGISTERING_CHALLENGER",
        active=True,
        current_round=round_no,
        message=(
            f"Eligible deterministic winner retained; registering Strategy Challenger "
            f"from round {round_no}, MT5 pass {pass_no}."
        ),
        first_blocker="",
        terminal_result="ELIGIBLE_WINNER_FOUND",
        winner=winner_payload,
    )
    try:
        challenger = ensure_challenger_for_winner(
            job_id,
            expected_round=round_no,
            expected_pass=pass_no,
        )
    except Exception as exc:
        _diagnostic(job_id, "CHALLENGER_REGISTRATION", exc, round_no)
        update_job(
            job_id,
            status="CHALLENGER_REGISTRATION_FAILED",
            active=False,
            current_round=round_no,
            message=str(exc)[:1000],
            first_blocker="CHALLENGER_REGISTRATION",
            terminal_result="ELIGIBLE_WINNER_FOUND",
            winner=winner_payload,
        )
        return 4

    update_job(
        job_id,
        status="STRATEGY_CHALLENGER_FOUND",
        active=False,
        current_round=round_no,
        message=(
            f"Strategy Challenger {challenger['challenger_id']} registered from "
            f"round {round_no}, MT5 pass {pass_no}. Strategy Champion unchanged."
        ),
        first_blocker="",
        terminal_result="STRATEGY_CHALLENGER_FOUND",
        winner=winner_payload,
        mark_completed=True,
    )
    return 0


def run_job(job_id: str, *, resume: bool = False) -> int:
    job = get_job(job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    request = job["request"]

    current_pool_workflow = optimizer_uses_owner_selection(request)
    committed_winner = _load_committed_winner(job_id)
    if committed_winner is not None:
        if current_pool_workflow:
            raise RuntimeError("AUTO_CHALLENGER_EVIDENCE_FORBIDDEN")
        return _register_committed_winner(job_id, committed_winner)

    round_no = max(1, int(job.get("current_round") or 1))
    try:
        if int(job.get("current_round") or 0) == 0:
            update_job(
                job_id,
                status="COMPILING_EA",
                active=True,
                current_round=1,
                message="Deploying frozen EA and compiling with MetaEditor",
                mark_started=True,
            )
            compile_result = compile_ea(request, job_id)
            write_json(
                job_evidence_dir(job_id) / "compile_result.json",
                compile_result,
            )
            round_no = 1

        round_record = get_round(job_id, round_no)
        if round_record is not None:
            search_space = round_record["state"].get("search_space") or request["search_space"]
        else:
            search_space = request["search_space"]

        while True:
            rows, audit, winner = execute_round(
                request,
                job_id=job_id,
                round_no=round_no,
                search_space=search_space,
                resume=resume,
            )
            if winner is not None:
                if current_pool_workflow:
                    update_job(
                        job_id,
                        status=OPTIMIZER_TERMINAL_QUALIFIED_POOL,
                        active=False,
                        current_round=round_no,
                        message=(
                            f"Qualified candidate pool ready with "
                            f"{int(audit['eligible_passes'])} candidate(s) from "
                            f"round {round_no}. Owner selection is required."
                        ),
                        first_blocker="",
                        terminal_result=OPTIMIZER_TERMINAL_QUALIFIED_POOL,
                        mark_completed=True,
                    )
                    return 0
                winner_payload = _write_winner(
                    request,
                    job_id=job_id,
                    round_no=round_no,
                    winner=winner,
                )
                return _register_committed_winner(job_id, winner_payload)

            if round_no >= int(request["max_rounds"]):
                update_job(
                    job_id,
                    status="NO_ELIGIBLE_WINNER_MAX_ROUNDS",
                    active=False,
                    current_round=round_no,
                    message=(
                        f"No eligible winner after {round_no} real MT5 round(s). "
                        "Maximum frozen round budget exhausted."
                    ),
                    first_blocker="OPTIMIZER_KPI_ELIGIBILITY",
                    terminal_result="NO_ELIGIBLE_WINNER_MAX_ROUNDS",
                    mark_completed=True,
                )
                return 0

            next_space, scientist_decision = _refine_for_next_round(
                request,
                job_id=job_id,
                source_round=round_no,
                current_space=search_space,
                rows=rows,
            )
            update_job(
                job_id,
                status="ROUND_COMPLETE_NO_WINNER",
                active=True,
                current_round=round_no,
                message=(
                    f"Round {round_no} complete with no eligible winner; "
                    f"{scientist_decision['effective_range_source']} will drive "
                    f"real MT5 round {round_no + 1}."
                ),
                first_blocker="OPTIMIZER_KPI_ELIGIBILITY",
            )
            round_no += 1
            search_space = next_space
            resume = False

    except ReportPending:
        return 3
    except Exception as exc:
        current = get_job(job_id)
        stage = str((current or {}).get("status") or "UNKNOWN")
        diagnostic = _diagnostic(job_id, stage, exc, round_no)
        update_job(
            job_id,
            status="FAILED",
            active=False,
            current_round=round_no,
            message=str(exc)[:1000],
            first_blocker=_first_blocker(exc),
            terminal_result="FAIL",
            mark_completed=True,
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    return run_job(args.job_id, resume=bool(args.resume))


if __name__ == "__main__":
    raise SystemExit(main())
