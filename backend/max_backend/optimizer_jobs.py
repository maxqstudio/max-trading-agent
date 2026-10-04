from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from .challenger_store import get_challenger_by_source, migrate_m03
from .config import DATABASE_PATH, ROOT
from .optimizer_core import (
    freeze_request,
    optimizer_fitness_for_request,
    parse_optimizer_report_preview,
    report_matches_request,
    sha256_file,
)
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
from .optimizer_worker_identity import (
    WORKER_IDENTITY_PROTOCOL,
    WorkerIdentityServer,
    get_process_identity,
    terminate_process_verified,
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
    "RESOURCE_STOPPED",
}

WORKER_IDENTITY_WAIT_SECONDS = 12.0
def _process_identity(pid: int) -> dict[str, Any] | None:
    return get_process_identity(int(pid))


def _worker_identity_matches(
    identity: dict[str, Any] | None,
    *,
    job_id: str,
    launch_token: str | None,
    expected: dict[str, Any] | None,
) -> bool:
    if not isinstance(identity, dict):
        return False
    authority = expected if isinstance(expected, dict) else identity
    executable = str(identity.get("executable_path") or "")
    authority_executable = str(authority.get("executable_path") or "")
    worker_executable = str(authority.get("worker_executable_path") or "")
    if not executable or not authority_executable or not worker_executable:
        return False
    if Path(executable).name.casefold() not in {"python.exe", "pythonw.exe", "python", "pythonw"}:
        return False
    if Path(worker_executable).name.casefold() not in {"python.exe", "pythonw.exe", "python", "pythonw"}:
        return False
    if str(authority.get("identity_protocol") or "") != WORKER_IDENTITY_PROTOCOL:
        return False
    if str(authority.get("worker_module") or "") != "max_backend.optimizer_worker":
        return False
    if not str(authority.get("startup_identity") or ""):
        return False
    if str(authority.get("job_id") or "") != str(job_id):
        return False
    if launch_token is None or str(authority.get("launch_token") or "") != str(launch_token):
        return False
    try:
        current_pid = int(identity.get("pid") or 0)
        authority_pid = int(authority.get("pid") or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    if current_pid <= 0 or current_pid != authority_pid:
        return False
    try:
        authority_path = os.path.normcase(os.path.abspath(authority_executable))
        current_path = os.path.normcase(os.path.abspath(executable))
    except (TypeError, ValueError, OSError):
        return False
    if authority_path != current_path:
        return False
    creation_time = str(authority.get("creation_time") or "")
    if not creation_time or str(identity.get("creation_time") or "") != creation_time:
        return False
    for field in ("job_id", "launch_token", "worker_module", "startup_identity"):
        if field in identity and identity.get(field) != authority.get(field):
            return False
    return True


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
    if not isinstance(expected, dict):
        raise RuntimeError("Optimizer worker launch identity is missing; refusing to terminate it")
    return terminate_process_verified(pid, expected=expected)


def _wait_for_worker_identity(
    process: subprocess.Popen[Any],
    server: WorkerIdentityServer,
    *,
    job_id: str,
    launch_token: str,
) -> dict[str, Any]:
    """Accept only the authenticated process attached to this exact launch channel."""
    identity = server.receive(
        job_id=job_id,
        launch_token=launch_token,
        expected_executable=sys.executable,
        expected_pid=None if os.name == "nt" else int(process.pid),
        timeout_seconds=WORKER_IDENTITY_WAIT_SECONDS,
    )
    if not _worker_identity_matches(
        identity,
        job_id=job_id,
        launch_token=launch_token,
        expected=None,
    ):
        raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_UNVERIFIED")
    return identity


def _mark_reconciliation_required(
    job_id: str,
    *,
    path: Path = DATABASE_PATH,
    reason: str,
    active: bool = True,
) -> dict[str, Any]:
    update_job(
        job_id,
        status="RECONCILIATION_REQUIRED",
        active=active,
        message="Optimizer process ownership could not be proven; new starts remain blocked.",
        first_blocker=reason,
        path=path,
    )
    return {"status": "BLOCKED", "job_id": job_id, "action": reason}


def _mark_unverified_worker_launch(
    job_id: str,
    launch_token: str,
    *,
    path: Path = DATABASE_PATH,
    reason: str,
) -> dict[str, Any]:
    """Reconcile unconfirmed launches without leaving a false active job."""
    current = get_job(job_id, path=path)
    if current is None:
        return {"status": "BLOCKED", "job_id": job_id, "action": reason}
    if (
        str(current.get("launch_token") or "") != str(launch_token)
        or str(current.get("status") or "") not in {"STARTING", "RESUMING", "RECONCILIATION_REQUIRED"}
    ):
        return {"status": "BLOCKED", "job_id": job_id, "action": reason}
    try:
        persisted_pid = int(current.get("worker_pid") or 0)
    except (TypeError, ValueError, OverflowError):
        persisted_pid = -1
    return _mark_reconciliation_required(
        job_id,
        path=path,
        reason=reason,
        active=persisted_pid != 0,
    )


def _revoke_unconfirmed_worker_launch(job_id: str, launch_token: str) -> None:
    current = get_job(job_id)
    if current is None:
        return
    if (
        str(current.get("launch_token") or "") == str(launch_token)
        and str(current.get("status") or "") in {"STARTING", "RESUMING"}
        and bool(current.get("active"))
        and not current.get("worker_pid")
    ):
        update_job(
            job_id,
            status="INTERRUPTED_SAFE_TO_RESUME",
            active=False,
            message="Worker launch was stopped before its PID could be durably confirmed; the checkpoint is safe to resume.",
            first_blocker="WORKER_INTERRUPTED_SAFE_TO_RESUME",
            terminal_result="INTERRUPTED",
            mark_completed=True,
        )


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
    try:
        identity_server = WorkerIdentityServer()
    except Exception as exc:
        update_job(
            job_id,
            status="FAILED",
            active=False,
            message="Optimizer worker identity channel could not be created",
            first_blocker="OPTIMIZER_WORKER_IDENTITY_CHANNEL_CREATE_FAILED",
            terminal_result="FAIL",
            mark_completed=True,
        )
        raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_CHANNEL_CREATE_FAILED") from exc
    command.extend(["--identity-pipe", identity_server.address])
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
        identity_server.close()
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
            identity_server,
            job_id=job_id,
            launch_token=launch_token,
        )
    except Exception as identity_error:
        _mark_unverified_worker_launch(
            job_id,
            launch_token,
            reason="OPTIMIZER_WORKER_LAUNCH_IDENTITY_UNVERIFIED",
        )
        raise RuntimeError("OPTIMIZER_WORKER_LAUNCH_RECONCILIATION_REQUIRED") from identity_error
    finally:
        identity_server.close()
    if not _worker_identity_matches(
        identity,
        job_id=job_id,
        launch_token=launch_token,
        expected=None,
    ):
        _mark_unverified_worker_launch(
            job_id,
            launch_token,
            reason="OPTIMIZER_WORKER_LAUNCH_IDENTITY_UNVERIFIED",
        )
        raise RuntimeError("OPTIMIZER_WORKER_LAUNCH_RECONCILIATION_REQUIRED")
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
            _mark_unverified_worker_launch(
                job_id,
                launch_token,
                reason="OPTIMIZER_WORKER_LAUNCH_OWNERSHIP_UNVERIFIED",
            )
            raise RuntimeError("OPTIMIZER_WORKER_LAUNCH_RECONCILIATION_REQUIRED") from stop_error
        _revoke_unconfirmed_worker_launch(job_id, launch_token)
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

    if (
        str(job.get("status") or "") == "RECONCILIATION_REQUIRED"
        and not bool(job.get("active"))
        and not job.get("worker_pid")
        and job.get("launch_token")
    ):
        return _public_job(update_job(
            job_id,
            status="STOPPED",
            active=False,
            message="Stopped by revoking an unconfirmed launch claim; no unverified process was terminated.",
            first_blocker="",
            terminal_result="STOPPED",
            mark_stopped=True,
            mark_completed=True,
        ))

    try:
        pid = int(job.get("worker_pid") or 0)
    except (TypeError, ValueError, OverflowError) as exc:
        _mark_reconciliation_required(
            job_id,
            reason="OPTIMIZER_WORKER_PID_INVALID",
        )
        raise RuntimeError("Optimizer worker PID is invalid; no process was stopped.") from exc
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
            _mark_reconciliation_required(
                job_id,
                reason="OPTIMIZER_WORKER_STOP_OWNERSHIP_UNVERIFIED",
            )
            raise RuntimeError("Optimizer worker ownership could not be proven; no process was stopped.") from exc
    elif token or not (
        str(job.get("status") or "") == "QUEUED"
        or (
            str(job.get("status") or "") == "EXECUTION_UNCERTAIN"
            and not bool(job.get("active"))
        )
    ):
        _mark_reconciliation_required(
            job_id,
            reason="OPTIMIZER_WORKER_STOP_OWNERSHIP_UNVERIFIED",
            active=False,
        )
        raise RuntimeError("Optimizer worker ownership could not be proven; no process was stopped.")

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

    try:
        pid = int(job.get("worker_pid") or 0)
    except (TypeError, ValueError, OverflowError) as exc:
        _mark_reconciliation_required(
            job_id,
            reason="OPTIMIZER_WORKER_PID_INVALID",
        )
        raise RuntimeError("Optimizer worker PID is invalid; resume is blocked.") from exc
    if pid > 0:
        if not isinstance(job.get("worker_identity"), dict) or not _worker_identity_matches(
            job.get("worker_identity"),
            job_id=job_id,
            launch_token=job.get("launch_token"),
            expected=None,
        ):
            _mark_reconciliation_required(
                job_id,
                reason="OPTIMIZER_PROCESS_IDENTITY_MISSING",
            )
            raise RuntimeError("Optimizer worker identity is unverified; resume is blocked.")
        try:
            process_identity = _process_identity(pid)
        except Exception as exc:
            _mark_reconciliation_required(
                job_id,
                reason="OPTIMIZER_PROCESS_OWNERSHIP_UNVERIFIED",
            )
            raise RuntimeError(
                "Optimizer worker ownership could not be checked; resume is blocked."
            ) from exc
        if process_identity is not None:
            if _worker_identity_matches(
                process_identity,
                job_id=job_id,
                launch_token=job.get("launch_token"),
                expected=job.get("worker_identity"),
            ):
                raise RuntimeError("Optimizer worker is still running; resume is not allowed")
            _mark_reconciliation_required(
                job_id,
                reason="OPTIMIZER_PROCESS_IDENTITY_MISMATCH",
            )
            raise RuntimeError("Optimizer worker ownership could not be proven; resume is blocked.")

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
    try:
        pid = int(job.get("worker_pid") or 0)
    except (TypeError, ValueError, OverflowError):
        return _mark_reconciliation_required(
            job_id,
            path=path,
            reason="OPTIMIZER_WORKER_PID_INVALID",
        )
    identity = job.get("worker_identity")
    token = job.get("launch_token")
    if pid > 0 and (
        not isinstance(identity, dict)
        or not _worker_identity_matches(
            identity,
            job_id=job_id,
            launch_token=token,
            expected=None,
        )
    ):
        return _mark_reconciliation_required(
            job_id,
            path=path,
            reason="OPTIMIZER_PROCESS_IDENTITY_MISSING",
        )
    try:
        pid_identity = _process_identity(pid) if pid else None
    except Exception:
        return _mark_reconciliation_required(
            job_id,
            path=path,
            reason="OPTIMIZER_PROCESS_OWNERSHIP_UNVERIFIED",
        )
    if pid_identity is not None:
        if _worker_identity_matches(
            pid_identity,
            job_id=job_id,
            launch_token=token,
            expected=identity,
        ):
            return {"status": "ACTIVE", "job_id": job_id, "action": "VERIFIED_WORKER_PRESENT"}
        return _mark_reconciliation_required(
            job_id,
            path=path,
            reason="OPTIMIZER_PROCESS_IDENTITY_MISMATCH",
        )
    if pid > 0 and not isinstance(identity, dict):
        return _mark_reconciliation_required(
            job_id,
            path=path,
            reason="OPTIMIZER_PROCESS_IDENTITY_MISSING",
        )
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
        if pid <= 0 and (
            isinstance(identity, dict)
            or str(job.get("status") or "") not in {"QUEUED", "STARTING", "RESUMING"}
        ):
            return _mark_reconciliation_required(
                job_id,
                path=path,
                reason="OPTIMIZER_PROCESS_IDENTITY_MISSING",
            )
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


def _verified_raw_report_preview(
    job: dict[str, Any],
    round_record: dict[str, Any],
    *,
    evidence_root: Path,
) -> dict[str, Any] | None:
    if (
        str(job.get("status") or "") != "FAILED"
        or str(job.get("first_blocker") or "") != "OPTIMIZER_RUNTIME_FAILURE"
    ):
        return None
    job_id = str(job.get("job_id") or "")
    if not job_id or Path(job_id).name != job_id or ".." in job_id:
        return {"status": "UNAVAILABLE", "message": "Raw optimizer evidence cannot be verified."}
    try:
        round_no = int(round_record["round_no"])
        state = round_record.get("state")
        if not isinstance(state, dict):
            raise ValueError("round checkpoint missing")
        base = evidence_root.resolve(strict=True)
        raw_dir = (
            evidence_root / job_id / f"round_{round_no:02d}" / ".staging" / "raw-freeze"
        )
        resolved_dir = raw_dir.resolve(strict=True)
        resolved_dir.relative_to(base)
        if any(part.is_symlink() for part in (
            evidence_root / job_id,
            evidence_root / job_id / f"round_{round_no:02d}",
            evidence_root / job_id / f"round_{round_no:02d}" / ".staging",
            raw_dir,
        )):
            raise ValueError("raw evidence path contains a symlink")

        manifest_path = raw_dir / "raw-manifest.json"
        report_path = raw_dir / "raw_Max_MTF.xml"
        metrics_path = raw_dir / "raw_Max_MTF_metrics.csv"
        for file_path in (manifest_path, report_path, metrics_path):
            if file_path.is_symlink() or not file_path.is_file():
                raise ValueError("raw evidence file is missing or unsafe")
            file_path.resolve(strict=True).relative_to(resolved_dir)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest.get("files") if isinstance(manifest, dict) else None
        expected_names = {report_path.name, metrics_path.name}
        if (
            manifest.get("schema") != "MAX_OPTIMIZER_RAW_FREEZE_V1"
            or manifest.get("job_id") != job_id
            or int(manifest.get("round", -1)) != round_no
            or not isinstance(files, dict)
            or set(files) != expected_names
            or manifest.get("source_report_fingerprint") != state.get("report_fingerprint")
        ):
            raise ValueError("raw evidence manifest identity mismatch")
        if (
            files.get(report_path.name) != sha256_file(report_path)
            or files.get(metrics_path.name) != sha256_file(metrics_path)
            or state.get("raw_report_sha256") != files.get(report_path.name)
            or state.get("raw_sidecar_sha256") != files.get(metrics_path.name)
        ):
            raise ValueError("raw evidence hash mismatch")
        request = job.get("request")
        if not isinstance(request, dict) or not report_matches_request(report_path, request):
            raise ValueError("raw report does not match the frozen job request")
        fitness = optimizer_fitness_for_request(request)
        if fitness is None:
            raise ValueError("frozen fitness authority missing")
        preview = parse_optimizer_report_preview(
            report_path,
            round_no=round_no,
            request=request,
            metrics_path=metrics_path,
            expected_nonce=int(state["optimizer_run_nonce"]),
            limit=25,
        )
        preview["message"] = (
            "MT5 report rows are retained for review. R evidence is incomplete; "
            "these rows are not validated candidates and cannot be promoted."
        )
        return preview
    except Exception:
        return {
            "status": "UNAVAILABLE",
            "message": "Raw optimizer evidence could not be verified; no pass data is shown.",
        }


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
        report_preview = _verified_raw_report_preview(
            job,
            item,
            evidence_root=evidence_root,
        )
        if report_preview is not None:
            item["report_preview"] = report_preview
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
