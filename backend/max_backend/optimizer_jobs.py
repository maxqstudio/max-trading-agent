from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .challenger_store import get_challenger_by_source, migrate_m03
from .config import DATABASE_PATH, ROOT
from .optimizer_core import freeze_request
from .optimizer_runtime import (
    OPTIMIZER_EVIDENCE_ROOT,
    compatible_reports,
    is_new_or_changed,
    job_evidence_dir,
    optimizer_terminal_process_state,
    write_json,
)
from .workflow_contract import optimizer_result_workflow, optimizer_uses_owner_selection
from .optimizer_store import (
    TERMINAL_STATES,
    attach_rounds,
    active_job,
    claim_initial_worker_launch,
    claim_resume_worker_launch,
    confirm_worker_launch,
    create_job,
    get_job,
    get_round,
    get_rounds,
    latest_job,
    update_job,
)

RESUMABLE_JOB_STATUSES = {
    "QUEUED",
    "STARTING",
    "COMPILING_EA",
    "PREPARING_MT5",
    "MT5_COMPLETE",
    "MT5_COMPLETE_UNCONFIRMED",
    "WAITING_FOR_REPORT",
    "REPORT_READY",
    "PARSING_RESULTS",
    "ROUND_COMPLETE_NO_WINNER",
    "SCIENTIST_REQUESTING",
    "REGISTERING_CHALLENGER",
    "CHALLENGER_REGISTRATION_FAILED",
    "RESUMING",
    "INTERRUPTED_SAFE_TO_RESUME",
    "EXECUTION_UNCERTAIN",
}

WORKER_IDENTITY_WAIT_SECONDS = 12.0
WORKER_IDENTITY_POLL_SECONDS = 0.1


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


def _process_identity(pid: int) -> dict[str, Any] | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        command = (
            "$p=Get-CimInstance Win32_Process -Filter 'ProcessId="
            + str(int(pid))
            + "' -ErrorAction SilentlyContinue;"
            + "if($p){$p | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine,CreationDate "
            + "| ConvertTo-Json -Compress}"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            raise RuntimeError("OPTIMIZER_PROCESS_IDENTITY_QUERY_FAILED")
        raw = (result.stdout or "").strip()
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("OPTIMIZER_PROCESS_IDENTITY_QUERY_INVALID") from exc
        if not isinstance(value, dict):
            raise RuntimeError("OPTIMIZER_PROCESS_IDENTITY_QUERY_INVALID")
        return {
            "pid": int(value.get("ProcessId") or 0),
            "parent_pid": int(value.get("ParentProcessId") or 0),
            "executable_path": str(value.get("ExecutablePath") or ""),
            "command_line": str(value.get("CommandLine") or ""),
            "creation_time": str(value.get("CreationDate") or ""),
        }
    proc = Path(f"/proc/{int(pid)}")
    if not proc.is_dir():
        return None
    try:
        command = proc.joinpath("cmdline").read_text(encoding="utf-8", errors="ignore")
        executable = str(proc.joinpath("exe").resolve())
    except OSError as exc:
        raise RuntimeError("OPTIMIZER_PROCESS_IDENTITY_QUERY_FAILED") from exc
    return {
        "pid": int(pid),
        "executable_path": executable,
        "command_line": command.replace("\x00", " "),
        "creation_time": "",
    }


def _worker_identity_matches(
    identity: dict[str, Any] | None,
    *,
    job_id: str,
    launch_token: str | None,
    expected: dict[str, Any] | None,
) -> bool:
    if not isinstance(identity, dict):
        return False
    executable = str(identity.get("executable_path") or "")
    command = str(identity.get("command_line") or "")
    if not executable or not command:
        return False
    if Path(executable).name.casefold() not in {Path(sys.executable).name.casefold(), "python.exe", "pythonw.exe"}:
        return False
    if "max_backend.optimizer_worker" not in command:
        return False
    if not re.search(r"(?:^|\s)--job-id\s+(?:\"?" + re.escape(job_id) + r"\"?)(?:\s|$)", command):
        return False
    if launch_token and not re.search(
        r"(?:^|\s)--launch-token\s+(?:\"?" + re.escape(launch_token) + r"\"?)(?:\s|$)",
        command,
    ):
        return False
    if expected:
        expected_executable = str(expected.get("executable_path") or "")
        expected_created = str(expected.get("creation_time") or "")
        if expected_executable and Path(expected_executable).as_posix().casefold() != Path(executable).as_posix().casefold():
            return False
        if expected_created and str(identity.get("creation_time") or "") != expected_created:
            return False
    return True


def _worker_is_alive(
    job_id: str,
    pid: int,
    *,
    launch_token: str | None = None,
    expected: dict[str, Any] | None = None,
) -> bool:
    return _worker_identity_matches(
        _process_identity(pid),
        job_id=job_id,
        launch_token=launch_token,
        expected=expected,
    )


def _terminate_verified_worker(
    pid: int,
    *,
    job_id: str,
    launch_token: str | None,
    expected: dict[str, Any] | None,
) -> bool:
    identity = _process_identity(pid)
    if identity is None:
        return False
    if not _worker_identity_matches(
        identity,
        job_id=job_id,
        launch_token=launch_token,
        expected=expected,
    ):
        raise RuntimeError("Stored optimizer worker PID is owned by another process; refusing to kill it")
    current = _process_identity(pid)
    if current is None:
        return False
    if not _worker_identity_matches(
        current,
        job_id=job_id,
        launch_token=launch_token,
        expected=expected,
    ):
        raise RuntimeError("Optimizer worker identity changed before stop; refusing to kill it")
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode not in (0, 128):
            raise RuntimeError("Unable to terminate verified optimizer worker process")
    else:
        os.kill(pid, 15)
    remaining = _process_identity(pid)
    if remaining and _worker_identity_matches(
        remaining,
        job_id=job_id,
        launch_token=launch_token,
        expected=expected,
    ):
        raise RuntimeError("Verified optimizer worker is still running after stop request")
    return True


def _is_venv_python_launcher(identity: dict[str, Any]) -> bool:
    executable = Path(str(identity.get("executable_path") or ""))
    return bool(
        os.name == "nt"
        and executable.parent.name.casefold() == "scripts"
        and (executable.parent.parent / "pyvenv.cfg").is_file()
    )


def _select_worker_candidate(
    matches: list[dict[str, Any]],
    *,
    launcher_pid: int | None,
) -> dict[str, Any] | None:
    """Resolve a Python venv launcher/child pair without trusting the shim PID."""
    by_pid = {int(row["pid"]): row for row in matches if int(row.get("pid") or 0) > 0}
    candidates = [row for row in by_pid.values() if not _is_venv_python_launcher(row)]
    if not candidates:
        return None

    def descends_from(row: dict[str, Any], ancestor_pid: int) -> bool:
        parent_pid = int(row.get("parent_pid") or 0)
        visited: set[int] = set()
        while parent_pid > 0 and parent_pid not in visited:
            if parent_pid == ancestor_pid:
                return True
            visited.add(parent_pid)
            parent = by_pid.get(parent_pid)
            if parent is None:
                return False
            parent_pid = int(parent.get("parent_pid") or 0)
        return False

    if launcher_pid is not None:
        descendants = [row for row in candidates if descends_from(row, launcher_pid)]
        if descendants:
            candidates = descendants

    if len(candidates) == 1:
        return candidates[0]

    candidate_pids = {int(row["pid"]) for row in candidates}
    parent_pids = {int(row.get("parent_pid") or 0) for row in candidates}
    leaves = [row for row in candidates if int(row["pid"]) not in parent_pids]
    if len(leaves) == 1:
        return leaves[0]
    if len(candidate_pids) > 1:
        raise RuntimeError("OPTIMIZER_WORKER_OWNERSHIP_AMBIGUOUS")
    return None


def _find_worker_by_token(
    launch_token: str | None,
    job_id: str,
    *,
    launcher_pid: int | None = None,
) -> dict[str, Any] | None:
    if os.name != "nt":
        return None
    command = (
        "$rows=Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
        "-ErrorAction SilentlyContinue | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine,CreationDate;"
        "if($rows){$rows | ConvertTo-Json -Compress}"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=8,
    )
    if result.returncode != 0:
        raise RuntimeError("OPTIMIZER_WORKER_PROCESS_QUERY_FAILED")
    raw = (result.stdout or "").strip()
    if not raw:
        return None
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OPTIMIZER_WORKER_PROCESS_QUERY_INVALID") from exc
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return None
    matches: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        identity = {
            "pid": int(row.get("ProcessId") or 0),
            "parent_pid": int(row.get("ParentProcessId") or 0),
            "executable_path": str(row.get("ExecutablePath") or ""),
            "command_line": str(row.get("CommandLine") or ""),
            "creation_time": str(row.get("CreationDate") or ""),
        }
        if _worker_identity_matches(
            identity,
            job_id=job_id,
            launch_token=launch_token,
            expected=None,
        ):
            matches.append(identity)
    return _select_worker_candidate(matches, launcher_pid=launcher_pid)


def _wait_for_worker_identity(
    process: subprocess.Popen[Any],
    *,
    job_id: str,
    launch_token: str,
) -> dict[str, Any]:
    """Wait for the actual worker, then re-read its OS identity before persisting it."""
    launcher_pid = int(process.pid)
    deadline = time.monotonic() + WORKER_IDENTITY_WAIT_SECONDS
    while True:
        if os.name == "nt":
            discovered = _find_worker_by_token(
                launch_token,
                job_id,
                launcher_pid=launcher_pid,
            )
            discovered_pid = int((discovered or {}).get("pid") or 0)
        else:
            discovered_pid = launcher_pid
            discovered = {"pid": launcher_pid}

        if discovered_pid > 0:
            identity = _process_identity(discovered_pid)
            if _worker_identity_matches(
                identity,
                job_id=job_id,
                launch_token=launch_token,
                expected=discovered,
            ):
                return identity or {}

        if time.monotonic() >= deadline:
            raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_UNVERIFIED")
        time.sleep(WORKER_IDENTITY_POLL_SECONDS)


def _mark_reconciliation_required(job_id: str, *, path: Path, reason: str) -> dict[str, Any]:
    update_job(
        job_id,
        status="RECONCILIATION_REQUIRED",
        active=True,
        message="Optimizer process ownership could not be proven; new starts remain blocked.",
        first_blocker=reason,
        path=path,
    )
    return {"status": "BLOCKED", "job_id": job_id, "action": reason}


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    result = dict(job)
    for field in ("launch_token", "worker_identity", "worker_pid"):
        result.pop(field, None)
    return result


def _spawn_worker(job_id: str, *, resume: bool) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    launch_token = uuid.uuid4().hex
    if resume:
        claim_resume_worker_launch(
            job_id,
            launch_token,
            allowed_statuses=RESUMABLE_JOB_STATUSES,
        )
    else:
        claim_initial_worker_launch(job_id, launch_token)
    stdout = None
    stderr = None
    try:
        evidence = job_evidence_dir(job_id)
        stdout = (evidence / "worker_stdout.log").open("a", encoding="utf-8")
        stderr = (evidence / "worker_stderr.log").open("a", encoding="utf-8")
    except Exception as exc:
        if stdout is not None:
            stdout.close()
        if stderr is not None:
            stderr.close()
        update_job(
            job_id,
            status="FAILED",
            active=False,
            message="Optimizer worker logs could not be opened",
            first_blocker="OPTIMIZER_WORKER_LOG_OPEN_FAILED",
            terminal_result="FAIL",
            mark_completed=True,
        )
        raise RuntimeError("OPTIMIZER_WORKER_LOG_OPEN_FAILED") from exc
    command = [
        sys.executable,
        "-m",
        "max_backend.optimizer_worker",
        "--job-id",
        job_id,
        "--launch-token",
        launch_token,
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
    except Exception as exc:
        update_job(
            job_id,
            status="FAILED",
            active=False,
            message="Optimizer worker could not be launched",
            first_blocker="OPTIMIZER_WORKER_SPAWN_FAILED",
            terminal_result="FAIL",
            mark_completed=True,
        )
        raise RuntimeError("OPTIMIZER_WORKER_SPAWN_FAILED") from exc
    finally:
        if stdout is not None:
            stdout.close()
        if stderr is not None:
            stderr.close()
    try:
        identity = _wait_for_worker_identity(
            process,
            job_id=job_id,
            launch_token=launch_token,
        )
    except Exception as identity_error:
        update_job(
            job_id,
            status="RECONCILIATION_REQUIRED",
            active=True,
            message="Optimizer worker was launched but its process identity could not be verified.",
            first_blocker="OPTIMIZER_WORKER_LAUNCH_IDENTITY_UNVERIFIED",
        )
        raise RuntimeError("OPTIMIZER_WORKER_LAUNCH_RECONCILIATION_REQUIRED") from identity_error
    try:
        job = confirm_worker_launch(
            job_id,
            launch_token,
            worker_pid=int(identity["pid"]),
            worker_identity=identity,
        )
    except Exception as claim_error:
        try:
            _terminate_verified_worker(
                int(identity["pid"]),
                job_id=job_id,
                launch_token=launch_token,
                expected=identity,
            )
        except Exception as stop_error:
            update_job(
                job_id,
                status="RECONCILIATION_REQUIRED",
                active=True,
                message="Worker launch confirmation was lost and its process could not be verified as stopped.",
                first_blocker="OPTIMIZER_WORKER_LAUNCH_OWNERSHIP_UNVERIFIED",
            )
            raise RuntimeError("OPTIMIZER_WORKER_LAUNCH_RECONCILIATION_REQUIRED") from stop_error
        raise RuntimeError("OPTIMIZER_WORKER_LAUNCH_CLAIM_LOST") from claim_error
    return _public_job(job)


def start_optimizer(raw_request: dict[str, Any]) -> dict[str, Any]:
    migrate_m03()
    request = freeze_request(raw_request)
    job = create_job(
        request,
        evidence_root=OPTIMIZER_EVIDENCE_ROOT,
    )
    evidence = job_evidence_dir(job["job_id"])
    try:
        write_json(evidence / "request.json", request)
    except Exception as exc:
        update_job(
            job["job_id"],
            status="FAILED",
            active=False,
            message="Optimizer request snapshot could not be written atomically",
            first_blocker="OPTIMIZER_REQUEST_SNAPSHOT_FAILED",
            terminal_result="FAIL",
            mark_completed=True,
        )
        raise RuntimeError("OPTIMIZER_REQUEST_SNAPSHOT_FAILED") from exc
    return _spawn_worker(job["job_id"], resume=False)


def _round_has_mt5_launch_authority(job: dict[str, Any]) -> bool:
    round_no = int(job.get("current_round") or 0)
    if round_no <= 0:
        return str(job.get("status") or "") == "EXECUTION_UNCERTAIN"
    record = get_round(str(job["job_id"]), round_no)
    state = (record or {}).get("state") or {}
    phase = str((record or {}).get("phase") or state.get("phase") or "")
    return phase in {
        "LAUNCH_INTENT",
        "MT5_RUNNING",
        "MT5_PROCESS_CONFIRMED",
        "MT5_COMPLETE_UNCONFIRMED",
    } or str(job.get("status") or "") == "EXECUTION_UNCERTAIN"


def _stop_verified_optimizer_mt5(job: dict[str, Any]) -> None:
    job_id = str(job["job_id"])
    round_no = int(job.get("current_round") or 0)
    record = get_round(job_id, round_no) if round_no > 0 else None
    state = (record or {}).get("state") or {}
    ini_path = str(state.get("ini_path") or "")
    if not ini_path:
        raise RuntimeError(
            "MT5 ownership is not proven; the round has no exact Optimizer INI identity."
        )

    process_state = optimizer_terminal_process_state(job["request"], ini_path=ini_path)
    if process_state["status"] == "OPTIMIZER_OWNED_RUNNING":
        processes = process_state.get("processes")
        if not isinstance(processes, list) or len(processes) != 1:
            raise RuntimeError("MT5 ownership is ambiguous; no terminal was stopped.")
        pid = int(processes[0].get("pid") or 0)
        if pid <= 0:
            raise RuntimeError("MT5 ownership is invalid; no terminal was stopped.")
        rechecked = optimizer_terminal_process_state(job["request"], ini_path=ini_path)
        current = rechecked.get("processes")
        if rechecked.get("status") != "OPTIMIZER_OWNED_RUNNING" or not isinstance(current, list) or len(current) != 1:
            raise RuntimeError("MT5 ownership changed before stop; no terminal was stopped.")
        current_process = current[0]
        if (
            int(current_process.get("pid") or 0) != pid
            or str(current_process.get("executable_path") or "").casefold()
            != str(processes[0].get("executable_path") or "").casefold()
            or str(current_process.get("command_line") or "")
            != str(processes[0].get("command_line") or "")
            or str(current_process.get("creation_time") or "")
            != str(processes[0].get("creation_time") or "")
        ):
            raise RuntimeError("MT5 process identity changed before stop; no terminal was stopped.")
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode not in (0, 128):
            raise RuntimeError("Unable to stop verified Optimizer-owned MT5 process")
        process_state = optimizer_terminal_process_state(job["request"], ini_path=ini_path)

    if process_state["status"] != "NOT_RUNNING":
        raise RuntimeError(
            "MT5 ownership is not proven; no terminal was stopped. Review the running session before continuing."
        )


def stop_optimizer(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    if str(job["status"]) in TERMINAL_STATES:
        return _public_job(job)

    pid = int(job.get("worker_pid") or 0)
    token = str(job.get("launch_token") or "") or None
    expected = job.get("worker_identity")
    if not isinstance(expected, dict):
        expected = None
    worker_stopped = False
    if pid > 0:
        try:
            worker_stopped = _terminate_verified_worker(
                pid,
                job_id=job_id,
                launch_token=token,
                expected=expected,
            )
        except Exception as exc:
            raise RuntimeError("Optimizer worker ownership could not be proven; no process was stopped.") from exc

    try:
        if not worker_stopped:
            discovered = _find_worker_by_token(token, job_id) if token else None
            if discovered is None:
                discovered = _find_worker_by_token(None, job_id)
                if discovered is not None and token:
                    raise RuntimeError("Optimizer worker launch token differs from the saved authority; stop is blocked.")
            if discovered is not None:
                worker_stopped = _terminate_verified_worker(
                    int(discovered["pid"]),
                    job_id=job_id,
                    launch_token=token,
                    expected=discovered,
                )
    except Exception as exc:
        raise RuntimeError("Optimizer worker ownership could not be proven; no process was stopped.") from exc

    if (
        not worker_stopped
        and not token
        and str(job.get("status") or "") in {"STARTING", "RESUMING"}
    ):
        raise RuntimeError("Optimizer launch has no durable process identity; stop is blocked for review.")

    if _round_has_mt5_launch_authority(job):
        _stop_verified_optimizer_mt5(job)

    return _public_job(update_job(
        job_id,
        status="STOPPED",
        active=False,
        message=(
            "Stopped after exact Optimizer process reconciliation; checkpoints were preserved."
            if str(job["status"]) == "EXECUTION_UNCERTAIN" or worker_stopped
            else "Stopped by Owner; committed evidence/checkpoints preserved"
        ),
        first_blocker="",
        terminal_result="STOPPED",
        mark_stopped=True,
        mark_completed=True,
    ))


def resume_optimizer(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    if str(job["status"]) == "EXECUTION_UNCERTAIN":
        _reconcile_uncertain_round(job)
        job = get_job(job_id)
        if job is None:
            raise FileNotFoundError(job_id)
    if str(job["status"]) not in RESUMABLE_JOB_STATUSES:
        raise RuntimeError(f"Optimizer job is not resumable: {job['status']}")

    pid = int(job.get("worker_pid") or 0)
    if pid > 0:
        try:
            worker_alive = _worker_is_alive(
                job_id,
                pid,
                launch_token=job.get("launch_token"),
                expected=job.get("worker_identity"),
            )
        except Exception as exc:
            raise RuntimeError(
                "Optimizer worker ownership could not be checked; resume is blocked."
            ) from exc
        if worker_alive:
            raise RuntimeError("Optimizer worker is still running; resume is not allowed")

    _ensure_request_snapshot(job)

    return _spawn_worker(job_id, resume=True)


def _reconcile_uncertain_round(job: dict[str, Any]) -> None:
    job_id = str(job["job_id"])
    round_no = int(job.get("current_round") or 0)
    record = get_round(job_id, round_no)
    if record is None:
        raise RuntimeError("The uncertain MT5 execution has no round checkpoint; manual review is required.")
    state = dict(record.get("state") or {})
    ini_path = str(state.get("ini_path") or "")
    if not ini_path:
        raise RuntimeError("The uncertain MT5 execution has no owned INI identity; it cannot be resumed safely.")
    process_state = optimizer_terminal_process_state(job["request"], ini_path=ini_path)
    if process_state["status"] == "OPTIMIZER_OWNED_RUNNING":
        raise RuntimeError("MT5 is still running from this Optimizer; wait for it to exit before resuming.")
    if process_state["status"] != "NOT_RUNNING":
        raise RuntimeError("MT5 ownership is not proven; no process was changed and resume is blocked.")

    prior = state.get("prelaunch_report_snapshot")
    if not isinstance(prior, list):
        prior = []
    candidates = [
        path for path in compatible_reports(job["request"])
        if is_new_or_changed(path, prior)
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "The previous MT5 execution is uncertain; a unique fresh report was not found, so it will not be relaunched."
        )
    report = candidates[0]
    metrics = Path(str(state.get("optimizer_metrics_path") or ""))
    if not metrics.is_file():
        raise RuntimeError("The previous MT5 execution report exists but its Weighted-R sidecar is missing.")
    state.update({
        "phase": "MT5_COMPLETE_UNCONFIRMED",
        "report_path": str(report),
        "report_selection_mode": "STARTUP_EXACT_IDENTITY_RECONCILIATION",
        "recovery_reason": "PROCESS_EXITED_FRESH_REPORT_FOUND_NO_RELAUNCH",
    })
    upsert_round(
        job_id,
        round_no,
        phase="MT5_COMPLETE_UNCONFIRMED",
        state=state,
        report_path=str(report),
        path=DATABASE_PATH,
    )
    update_job(
        job_id,
        status="MT5_COMPLETE_UNCONFIRMED",
        active=False,
        message="The exact MT5 process exited and a unique fresh report was found; resume will parse without relaunch.",
        first_blocker="",
    )


def _ensure_request_snapshot(job: dict[str, Any]) -> None:
    target = job_evidence_dir(str(job["job_id"])) / "request.json"
    if not target.exists():
        write_json(target, job["request"])
        return
    try:
        persisted = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("OPTIMIZER_REQUEST_SNAPSHOT_CORRUPT") from exc
    if persisted != job["request"]:
        raise RuntimeError("OPTIMIZER_REQUEST_SNAPSHOT_MISMATCH")


def reconcile_optimizer_startup(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    """Resolve only the one DB-authorized active job; never scan artifact trees."""
    job = active_job(path=path)
    if job is None:
        return {"status": "READY", "job_id": None, "action": "NO_ACTIVE_JOB"}
    job_id = str(job["job_id"])
    pid = int(job.get("worker_pid") or 0)
    identity = job.get("worker_identity")
    token = job.get("launch_token")
    try:
        pid_identity = _process_identity(pid) if pid else None
    except Exception:
        return _mark_reconciliation_required(
            job_id,
            path=path,
            reason="OPTIMIZER_PROCESS_OWNERSHIP_UNVERIFIED",
        )
    if pid_identity and _worker_identity_matches(
        pid_identity,
        job_id=job_id,
        launch_token=token,
        expected=identity,
    ):
        return {"status": "ACTIVE", "job_id": job_id, "action": "VERIFIED_WORKER_PRESENT"}

    expected_executable = str((identity or {}).get("executable_path") or "")
    expected_created = str((identity or {}).get("creation_time") or "")
    actual_executable = str((pid_identity or {}).get("executable_path") or "")
    actual_created = str((pid_identity or {}).get("creation_time") or "")
    proven_pid_reuse = bool(
        pid_identity
        and expected_executable
        and actual_executable
        and Path(expected_executable).as_posix().casefold() != Path(actual_executable).as_posix().casefold()
    ) or bool(pid_identity and expected_created and actual_created and expected_created != actual_created)
    if pid_identity and not proven_pid_reuse:
        return _mark_reconciliation_required(
            job_id,
            path=path,
            reason="OPTIMIZER_PROCESS_IDENTITY_MISMATCH",
        )

    if token:
        try:
            discovered = _find_worker_by_token(str(token), job_id)
        except Exception:
            return _mark_reconciliation_required(
                job_id,
                path=path,
                reason="OPTIMIZER_PROCESS_OWNERSHIP_UNVERIFIED",
            )
        if discovered is not None:
            update_job(
                job_id,
                worker_pid=int(discovered["pid"]),
                worker_identity=discovered,
                path=path,
            )
            return {"status": "ACTIVE", "job_id": job_id, "action": "WORKER_ADOPTED_BY_TOKEN"}

    try:
        worker_for_job = _find_worker_by_token(None, job_id)
    except Exception:
        return _mark_reconciliation_required(
            job_id,
            path=path,
            reason="OPTIMIZER_PROCESS_OWNERSHIP_UNVERIFIED",
        )
    if worker_for_job is not None:
        if token:
            return _mark_reconciliation_required(
                job_id,
                path=path,
                reason="OPTIMIZER_WORKER_LAUNCH_TOKEN_MISMATCH",
            )
        update_job(
            job_id,
            worker_pid=int(worker_for_job["pid"]),
            worker_identity=worker_for_job,
            path=path,
        )
        return {"status": "ACTIVE", "job_id": job_id, "action": "WORKER_ADOPTED_BY_JOB_ID"}

    rounds = get_rounds(job_id, path=path)
    latest = rounds[-1] if rounds else None
    phase = str((latest or {}).get("phase") or "")
    uncertain_external = phase in {
        "LAUNCH_INTENT",
        "MT5_RUNNING",
        "MT5_PROCESS_CONFIRMED",
        "MT5_COMPLETE_UNCONFIRMED",
    }
    if uncertain_external:
        status = "EXECUTION_UNCERTAIN"
        blocker = "MT5_EXECUTION_REQUIRES_RECONCILIATION"
        message = (
            "MT5 may have started before interruption. Resume is blocked until the "
            "exact terminal process and report evidence are reconciled."
        )
    else:
        status = "INTERRUPTED_SAFE_TO_RESUME"
        blocker = "WORKER_INTERRUPTED_SAFE_TO_RESUME"
        message = (
            "Optimizer worker stopped before an uncertain MT5 launch. The saved "
            "checkpoint can be resumed without creating a second job."
        )
    update_job(
        job_id,
        status=status,
        active=False,
        message=message,
        first_blocker=blocker,
        terminal_result="INTERRUPTED",
        mark_completed=True,
        path=path,
    )
    return {"status": status, "job_id": job_id, "action": "STALE_ACTIVE_JOB_RECONCILED"}


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
    for item in job["rounds"]:
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        if "mt5_process_identity" in state:
            item["state"] = {
                key: value
                for key, value in state.items()
                if key != "mt5_process_identity"
            }
    return _public_job(job)


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
