from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .challenger_store import get_challenger_by_source, migrate_m03
from .config import DATABASE_PATH, ROOT
from .optimizer_core import freeze_request
from .optimizer_runtime import OPTIMIZER_EVIDENCE_ROOT, job_evidence_dir, write_json
from .workflow_contract import optimizer_result_workflow, optimizer_uses_owner_selection
from .optimizer_store import (
    TERMINAL_STATES,
    attach_rounds,
    create_job,
    get_job,
    latest_job,
    update_job,
)


def _process_command_line(pid: int) -> str:
    if pid <= 0:
        return ""
    if os.name == "nt":
        command = (
            "$p=Get-CimInstance Win32_Process -Filter 'ProcessId="
            + str(pid)
            + "' -ErrorAction SilentlyContinue;"
            + "if($p){$p.CommandLine}"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (result.stdout or "").strip()
    proc_cmd = Path(f"/proc/{pid}/cmdline")
    if proc_cmd.is_file():
        return proc_cmd.read_text(encoding="utf-8", errors="ignore").replace("\x00", " ")
    return ""


def _worker_is_alive(job_id: str, pid: int) -> bool:
    command = _process_command_line(pid)
    return bool(
        command
        and "max_backend.optimizer_worker" in command
        and job_id in command
    )


def _spawn_worker(job_id: str, *, resume: bool) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    evidence = job_evidence_dir(job_id)
    stdout = (evidence / "worker_stdout.log").open("a", encoding="utf-8")
    stderr = (evidence / "worker_stderr.log").open("a", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "max_backend.optimizer_worker",
        "--job-id",
        job_id,
    ]
    if resume:
        command.append("--resume")

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            creationflags=creationflags,
            stdout=stdout,
            stderr=stderr,
        )
    finally:
        stdout.close()
        stderr.close()

    return update_job(
        job_id,
        worker_pid=process.pid,
        active=True,
        status="RESUMING" if resume else "QUEUED",
        message=(
            "Resuming checkpointed optimizer evidence without blind MT5 relaunch"
            if resume
            else "Queued deterministic Strategy Optimizer worker"
        ),
    )


def start_optimizer(raw_request: dict[str, Any]) -> dict[str, Any]:
    migrate_m03()
    request = freeze_request(raw_request)
    job = create_job(
        request,
        evidence_root=OPTIMIZER_EVIDENCE_ROOT,
    )
    evidence = job_evidence_dir(job["job_id"])
    write_json(evidence / "request.json", request)
    return _spawn_worker(job["job_id"], resume=False)


def stop_optimizer(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    if str(job["status"]) in TERMINAL_STATES:
        return job

    pid = int(job.get("worker_pid") or 0)
    if pid > 0:
        command = _process_command_line(pid)
        if command:
            if not _worker_is_alive(job_id, pid):
                raise RuntimeError(
                    "Stored optimizer worker PID is owned by another process; refusing to kill it"
                )
            if os.name == "nt":
                result = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if result.returncode not in (0, 128):
                    raise RuntimeError(
                        "Unable to terminate optimizer-owned process tree: "
                        + (result.stderr or result.stdout or "")
                    )
            else:
                os.kill(pid, 15)

    return update_job(
        job_id,
        status="STOPPED",
        active=False,
        message="Stopped by Owner; committed evidence/checkpoints preserved",
        first_blocker="",
        terminal_result="STOPPED",
        mark_stopped=True,
        mark_completed=True,
    )


def resume_optimizer(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    allowed = {
        "PREPARED",
        "MT5_RUNNING",
        "MT5_COMPLETE",
        "MT5_COMPLETE_UNCONFIRMED",
        "WAITING_FOR_REPORT",
        "REPORT_READY",
        "ROUND_COMPLETE_NO_WINNER",
        "SCIENTIST_REQUESTING",
        "REGISTERING_CHALLENGER",
        "CHALLENGER_REGISTRATION_FAILED",
        "RESUMING",
    }
    if str(job["status"]) not in allowed:
        raise RuntimeError(f"Optimizer job is not resumable: {job['status']}")

    pid = int(job.get("worker_pid") or 0)
    if pid > 0 and _worker_is_alive(job_id, pid):
        raise RuntimeError("Optimizer worker is still running; resume is not allowed")

    return _spawn_worker(job_id, resume=True)


def _round_passes(
    job_id: str,
    round_no: int,
    *,
    evidence_root: Path = OPTIMIZER_EVIDENCE_ROOT,
) -> list[dict[str, Any]]:
    path = (
        evidence_root
        / job_id
        / f"round_{round_no:02d}"
        / "passes.json"
    )
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("passes")
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def job_detail(
    job_id: str,
    *,
    path: Path = DATABASE_PATH,
    evidence_root: Path = OPTIMIZER_EVIDENCE_ROOT,
) -> dict[str, Any] | None:
    job = attach_rounds(get_job(job_id, path=path), path=path)
    if job is None:
        return None

    job["optimizer_result_workflow"] = optimizer_result_workflow(job["request"])

    rounds = []
    counters = {
        "proposal_transitions": 0,
        "actual_provider_calls": 0,
        "confirmed_provider_calls": 0,
        "unconfirmed_provider_attempts": 0,
        "accepted_proposals": 0,
        "rejected_proposals": 0,
        "fallbacks": 0,
    }
    for record in job["rounds"]:
        item = dict(record)
        item["passes"] = (
            []
            if optimizer_uses_owner_selection(job["request"])
            else _round_passes(
                job_id,
                int(item["round_no"]),
                evidence_root=evidence_root,
            )
        )
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        transition = state.get("scientist_transition")
        decision = None
        if isinstance(transition, dict):
            raw_decision = transition.get("decision")
            if isinstance(raw_decision, dict):
                decision = dict(raw_decision)
                counters["proposal_transitions"] += int(
                    decision.get("proposal_attempts", 0) > 0
                )
                legacy_actual = int(decision.get("actual_provider_calls") or 0)
                confirmed = int(
                    decision.get("confirmed_provider_calls")
                    if decision.get("confirmed_provider_calls") is not None
                    else legacy_actual
                )
                counters["actual_provider_calls"] += confirmed
                counters["confirmed_provider_calls"] += confirmed
                counters["unconfirmed_provider_attempts"] += int(
                    decision.get("unconfirmed_provider_attempts") or 0
                )
                counters["accepted_proposals"] += int(
                    decision.get("accepted_proposals") or 0
                )
                counters["rejected_proposals"] += int(
                    decision.get("rejected_proposals") or 0
                )
                counters["fallbacks"] += int(decision.get("fallbacks") or 0)
        item["scientist_decision"] = decision
        rounds.append(item)

    job["rounds"] = rounds
    job["scientist_calls"] = counters["confirmed_provider_calls"]
    job["scientist_counters"] = counters
    challenger = None
    winner = job.get("winner")
    if isinstance(winner, dict):
        source_round = winner.get("round")
        source_pass = winner.get("mt5_pass")
        if source_round is not None and source_pass is not None:
            challenger = get_challenger_by_source(
                job_id,
                int(source_round),
                int(source_pass),
                path=path,
            )
    job["challenger_created"] = int(
        isinstance(challenger, dict) and challenger.get("status") == "CHALLENGER"
    )
    job["challenger_id"] = (
        challenger.get("challenger_id") if isinstance(challenger, dict) else None
    )
    job["champion_mutation"] = "NONE"
    return job


def latest_job_detail(
    *,
    path: Path = DATABASE_PATH,
    evidence_root: Path = OPTIMIZER_EVIDENCE_ROOT,
) -> dict[str, Any] | None:
    job = latest_job(path=path)
    return (
        job_detail(job["job_id"], path=path, evidence_root=evidence_root)
        if job else None
    )
