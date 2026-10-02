from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import max_backend.optimizer_jobs as optimizer_jobs
import max_backend.optimizer_worker as optimizer_worker
from max_backend.db import ensure_baseline_registered, initialize_database
from max_backend.optimizer_jobs import reconcile_optimizer_startup
from max_backend.optimizer_store import (
    claim_initial_worker_launch,
    claim_resume_worker_launch,
    confirm_worker_launch,
    create_job,
    get_job,
    get_round,
    update_job,
    upsert_round,
)
from max_backend.workflow_store import migrate_current


def make_db(tmp_path: Path) -> Path:
    path = tmp_path / "state" / "max.db"
    initialize_database(path)
    ensure_baseline_registered(path)
    migrate_current(path)
    return path


def persisted_worker_identity(
    job_id: str,
    *,
    pid: int = 4321,
    token: str = "launch-token",
    executable: str = r"C:\Python313\python.exe",
    creation_time: str = "worker-created",
) -> dict[str, object]:
    return {
        "pid": pid,
        "job_id": job_id,
        "launch_token": token,
        "worker_module": "max_backend.optimizer_worker",
        "worker_executable_path": r"D:\max\.venv\Scripts\python.exe",
        "identity_protocol": "named-pipe-hmac-v1",
        "startup_identity": "a" * 64,
        "executable_path": executable,
        "creation_time": creation_time,
    }


def test_startup_reconciles_worker_loss_before_mt5_to_resumable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    result = reconcile_optimizer_startup(path=path)

    current = get_job(job["job_id"], path=path)
    assert result["action"] == "STALE_ACTIVE_JOB_RECONCILED"
    assert current is not None
    assert current["status"] == "INTERRUPTED_SAFE_TO_RESUME"
    assert current["active"] is False
    assert current["first_blocker"] == "WORKER_INTERRUPTED_SAFE_TO_RESUME"


def test_startup_never_converts_mt5_launch_intent_to_blind_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    update_job(job["job_id"], status="MT5_RUNNING", path=path)
    upsert_round(
        job["job_id"],
        1,
        phase="LAUNCH_INTENT",
        state={"phase": "LAUNCH_INTENT", "ini_path": str(tmp_path / "unique.ini")},
        path=path,
    )
    result = reconcile_optimizer_startup(path=path)

    current = get_job(job["job_id"], path=path)
    assert result["status"] == "EXECUTION_UNCERTAIN"
    assert current is not None
    assert current["status"] == "EXECUTION_UNCERTAIN"
    assert current["active"] is False
    with pytest.raises(RuntimeError, match="requires resume or explicit stop"):
        create_job(
            {"max_rounds": 1},
            evidence_root=tmp_path / "artifacts" / "optimizer",
            path=path,
        )


def test_resume_launch_claim_is_single_flight(tmp_path: Path) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    update_job(
        job["job_id"],
        status="INTERRUPTED_SAFE_TO_RESUME",
        active=False,
        path=path,
    )
    claim_resume_worker_launch(
        job["job_id"],
        "first-token",
        allowed_statuses={"INTERRUPTED_SAFE_TO_RESUME"},
        path=path,
    )
    with pytest.raises(RuntimeError, match="OPTIMIZER_WORKER_LAUNCH_ALREADY_ACTIVE"):
        claim_resume_worker_launch(
            job["job_id"],
            "second-token",
            allowed_statuses={"INTERRUPTED_SAFE_TO_RESUME"},
            path=path,
        )
    current = get_job(job["job_id"], path=path)
    assert current is not None
    assert current["active"] is True
    assert current["launch_token"] == "first-token"


def test_worker_launch_confirmation_cannot_reactivate_a_stopped_claim(tmp_path: Path) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    claim_initial_worker_launch(job["job_id"], "launch-token", path=path)
    update_job(
        job["job_id"],
        status="STOPPED",
        active=False,
        path=path,
    )

    with pytest.raises(RuntimeError, match="OPTIMIZER_WORKER_LAUNCH_CLAIM_LOST"):
        confirm_worker_launch(
            job["job_id"],
            "launch-token",
            worker_pid=4321,
            worker_identity={"pid": 4321},
            path=path,
        )

    current = get_job(job["job_id"], path=path)
    assert current is not None
    assert current["status"] == "STOPPED"
    assert current["active"] is False


def test_spawn_persists_verified_worker_pid_not_venv_launcher_pid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    identity = {
        "pid": 222,
        "job_id": job["job_id"],
        "launch_token": "launch-token",
        "worker_module": "max_backend.optimizer_worker",
        "worker_executable_path": r"D:\max\.venv\Scripts\python.exe",
        "identity_protocol": "named-pipe-hmac-v1",
        "startup_identity": "a" * 64,
        "executable_path": r"C:\Python313\python.exe",
        "creation_time": "worker-created",
    }
    evidence = tmp_path / "worker-evidence"
    evidence.mkdir()
    persisted_get_job = optimizer_jobs.get_job
    persisted_update_job = optimizer_jobs.update_job
    persisted_claim = optimizer_jobs.claim_initial_worker_launch
    persisted_confirm = optimizer_jobs.confirm_worker_launch

    monkeypatch.setattr(
        optimizer_jobs,
        "get_job",
        lambda job_id, **_kwargs: persisted_get_job(job_id, path=path),
    )
    monkeypatch.setattr(
        optimizer_jobs,
        "claim_initial_worker_launch",
        lambda job_id, token: persisted_claim(job_id, token, path=path),
    )

    def update_in_test_db(job_id: str, **changes: object) -> dict[str, object]:
        changes["path"] = path
        return persisted_update_job(job_id, **changes)

    def confirm_in_test_db(job_id: str, token: str, **changes: object) -> dict[str, object]:
        changes["path"] = path
        return persisted_confirm(job_id, token, **changes)

    class FakeProcess:
        pid = 111

        @staticmethod
        def poll() -> None:
            return None

    monkeypatch.setattr(optimizer_jobs, "update_job", update_in_test_db)
    monkeypatch.setattr(optimizer_jobs, "confirm_worker_launch", confirm_in_test_db)
    monkeypatch.setattr(optimizer_jobs, "job_evidence_dir", lambda _job_id: evidence)
    monkeypatch.setattr(optimizer_jobs.subprocess, "Popen", lambda *_a, **_k: FakeProcess())
    server = SimpleNamespace(address=r"\\.\pipe\synthetic-worker", close=lambda: None)
    monkeypatch.setattr(optimizer_jobs, "WorkerIdentityServer", lambda: server)
    monkeypatch.setattr(
        optimizer_jobs,
        "_wait_for_worker_identity",
        lambda *_args, **_kwargs: identity,
    )
    monkeypatch.setattr(optimizer_jobs.uuid, "uuid4", lambda: SimpleNamespace(hex="launch-token"))

    optimizer_jobs._spawn_worker(job["job_id"], resume=False)

    current = get_job(job["job_id"], path=path)
    assert current is not None
    assert current["worker_pid"] == 222
    assert current["worker_identity"]["pid"] == 222
    assert current["worker_identity"]["job_id"] == job["job_id"]
    assert current["worker_identity"]["launch_token"] == "launch-token"


@pytest.mark.parametrize(
    ("stop_before_failure", "expected_status"),
    [(False, "INTERRUPTED_SAFE_TO_RESUME"), (True, "STOPPED")],
)
def test_lost_launch_confirmation_terminates_only_the_verified_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop_before_failure: bool,
    expected_status: str,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    evidence = tmp_path / "worker-evidence-lost-confirmation"
    evidence.mkdir()
    identity = persisted_worker_identity(job["job_id"], pid=222)
    persisted_get_job = optimizer_jobs.get_job
    persisted_update_job = optimizer_jobs.update_job
    persisted_claim = optimizer_jobs.claim_initial_worker_launch

    monkeypatch.setattr(
        optimizer_jobs,
        "get_job",
        lambda job_id, **_kwargs: persisted_get_job(job_id, path=path),
    )
    monkeypatch.setattr(
        optimizer_jobs,
        "claim_initial_worker_launch",
        lambda job_id, token: persisted_claim(job_id, token, path=path),
    )

    def update_in_test_db(job_id: str, **changes: object) -> dict[str, object]:
        changes["path"] = path
        return persisted_update_job(job_id, **changes)

    def lose_confirmation(job_id: str, token: str, **_changes: object) -> dict[str, object]:
        if stop_before_failure:
            persisted_update_job(job_id, status="STOPPED", active=False, path=path)
        raise RuntimeError("OPTIMIZER_WORKER_LAUNCH_CLAIM_LOST")

    class FakeProcess:
        pid = 111

    server = SimpleNamespace(address=r"\\.\pipe\synthetic-worker", close=lambda: None)
    monkeypatch.setattr(optimizer_jobs, "update_job", update_in_test_db)
    monkeypatch.setattr(optimizer_jobs, "confirm_worker_launch", lose_confirmation)
    monkeypatch.setattr(optimizer_jobs, "job_evidence_dir", lambda _job_id: evidence)
    monkeypatch.setattr(optimizer_jobs.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(optimizer_jobs, "WorkerIdentityServer", lambda: server)
    monkeypatch.setattr(optimizer_jobs, "_wait_for_worker_identity", lambda *_args, **_kwargs: identity)
    monkeypatch.setattr(optimizer_jobs, "_process_identity", lambda _pid: {
        "pid": identity["pid"],
        "executable_path": identity["executable_path"],
        "creation_time": identity["creation_time"],
    })
    terminated: list[tuple[int, dict[str, object]]] = []

    def terminate(pid: int, *, expected: dict[str, object], **_kwargs: object) -> bool:
        terminated.append((pid, expected))
        return True

    monkeypatch.setattr(optimizer_jobs, "terminate_process_verified", terminate)
    monkeypatch.setattr(optimizer_jobs.uuid, "uuid4", lambda: SimpleNamespace(hex="launch-token"))

    with pytest.raises(RuntimeError, match="OPTIMIZER_WORKER_LAUNCH_CLAIM_LOST"):
        optimizer_jobs._spawn_worker(job["job_id"], resume=False)

    current = get_job(job["job_id"], path=path)
    assert terminated == [(222, identity)]
    assert current is not None
    assert current["status"] == expected_status
    assert current["active"] is False


def test_spawn_marks_reconciliation_required_when_worker_identity_is_unverified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    evidence = tmp_path / "worker-evidence-unverified"
    evidence.mkdir()
    persisted_get_job = optimizer_jobs.get_job
    persisted_update_job = optimizer_jobs.update_job
    persisted_claim = optimizer_jobs.claim_initial_worker_launch

    monkeypatch.setattr(
        optimizer_jobs,
        "get_job",
        lambda job_id, **_kwargs: persisted_get_job(job_id, path=path),
    )
    monkeypatch.setattr(
        optimizer_jobs,
        "claim_initial_worker_launch",
        lambda job_id, token: persisted_claim(job_id, token, path=path),
    )

    def update_in_test_db(job_id: str, **changes: object) -> dict[str, object]:
        changes["path"] = path
        return persisted_update_job(job_id, **changes)

    class FakeProcess:
        pid = 111

        @staticmethod
        def poll() -> None:
            return None

    monkeypatch.setattr(optimizer_jobs, "update_job", update_in_test_db)
    monkeypatch.setattr(optimizer_jobs, "job_evidence_dir", lambda _job_id: evidence)
    monkeypatch.setattr(optimizer_jobs.subprocess, "Popen", lambda *_a, **_k: FakeProcess())
    server = SimpleNamespace(address=r"\\.\pipe\synthetic-worker", close=lambda: None)
    monkeypatch.setattr(optimizer_jobs, "WorkerIdentityServer", lambda: server)
    monkeypatch.setattr(
        optimizer_jobs,
        "_wait_for_worker_identity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("no proof")),
    )
    monkeypatch.setattr(optimizer_jobs.uuid, "uuid4", lambda: SimpleNamespace(hex="launch-token"))

    with pytest.raises(RuntimeError, match="OPTIMIZER_WORKER_LAUNCH_RECONCILIATION_REQUIRED"):
        optimizer_jobs._spawn_worker(job["job_id"], resume=False)

    current = get_job(job["job_id"], path=path)
    assert current is not None
    assert current["status"] == "RECONCILIATION_REQUIRED"
    assert current["active"] is False
    assert current["worker_pid"] is None
    assert current["first_blocker"] == "OPTIMIZER_WORKER_LAUNCH_IDENTITY_UNVERIFIED"


def test_stop_blocks_when_worker_is_gone_but_terminal_ownership_is_unproven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    worker_identity = {
        "pid": 4321,
        "job_id": job["job_id"],
        "launch_token": "worker-token",
        "worker_module": "max_backend.optimizer_worker",
        "worker_executable_path": r"D:\max\.venv\Scripts\python.exe",
        "identity_protocol": "named-pipe-hmac-v1",
        "startup_identity": "a" * 64,
        "executable_path": r"C:\Python313\python.exe",
        "creation_time": "worker-created",
    }
    upsert_round(
        job["job_id"],
        1,
        phase="LAUNCH_INTENT",
        state={"phase": "LAUNCH_INTENT", "ini_path": str(tmp_path / "owned.ini")},
        path=path,
    )
    update_job(
        job["job_id"],
        status="MT5_RUNNING",
        active=True,
        current_round=1,
        worker_pid=4321,
        launch_token="worker-token",
        worker_identity=worker_identity,
        path=path,
    )

    persisted_get_job = optimizer_jobs.get_job
    persisted_get_round = optimizer_jobs.get_round
    persisted_update_job = optimizer_jobs.update_job
    monkeypatch.setattr(
        optimizer_jobs,
        "get_job",
        lambda job_id, **_kwargs: persisted_get_job(job_id, path=path),
    )
    monkeypatch.setattr(
        optimizer_jobs,
        "get_round",
        lambda job_id, round_no: persisted_get_round(job_id, round_no, path=path),
    )

    def update_in_test_db(job_id: str, **changes: object) -> dict[str, object]:
        changes["path"] = path
        return persisted_update_job(job_id, **changes)

    monkeypatch.setattr(optimizer_jobs, "update_job", update_in_test_db)
    monkeypatch.setattr(optimizer_jobs, "_process_identity", lambda _pid: None)
    monkeypatch.setattr(
        optimizer_jobs,
        "optimizer_terminal_process_state",
        lambda *_args, **_kwargs: {"status": "UNRELATED_MT5_RUNNING", "processes": []},
    )

    with pytest.raises(RuntimeError, match="MT5 ownership is not proven"):
        optimizer_jobs.stop_optimizer(job["job_id"])

    current = get_job(job["job_id"], path=path)
    assert current is not None
    assert current["status"] == "MT5_RUNNING"
    assert current["active"] is True


def test_stop_terminates_only_the_uniquely_verified_optimizer_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    ini_path = str(tmp_path / "exact-run.ini")
    upsert_round(
        job["job_id"],
        1,
        phase="LAUNCH_INTENT",
        state={"phase": "LAUNCH_INTENT", "ini_path": ini_path},
        path=path,
    )
    update_job(
        job["job_id"],
        status="EXECUTION_UNCERTAIN",
        active=False,
        current_round=1,
        path=path,
    )
    persisted_get_job = optimizer_jobs.get_job
    persisted_get_round = optimizer_jobs.get_round
    persisted_update_job = optimizer_jobs.update_job
    monkeypatch.setattr(
        optimizer_jobs,
        "get_job",
        lambda job_id, **_kwargs: persisted_get_job(job_id, path=path),
    )
    monkeypatch.setattr(
        optimizer_jobs,
        "get_round",
        lambda job_id, round_no: persisted_get_round(job_id, round_no, path=path),
    )

    def update_in_test_db(job_id: str, **changes: object) -> dict[str, object]:
        changes["path"] = path
        return persisted_update_job(job_id, **changes)

    monkeypatch.setattr(optimizer_jobs, "update_job", update_in_test_db)
    terminal_reads = iter([
        {"status": "OPTIMIZER_OWNED_RUNNING", "processes": [{"pid": 6123}]},
        {"status": "OPTIMIZER_OWNED_RUNNING", "processes": [{"pid": 6123}]},
        {"status": "NOT_RUNNING", "processes": []},
    ])
    monkeypatch.setattr(
        optimizer_jobs,
        "optimizer_terminal_process_state",
        lambda *_args, **_kwargs: next(terminal_reads),
    )
    stopped: list[list[str]] = []
    monkeypatch.setattr(
        optimizer_jobs.subprocess,
        "run",
        lambda command, **_kwargs: (stopped.append(command) or SimpleNamespace(returncode=0)),
    )

    result = optimizer_jobs.stop_optimizer(job["job_id"])

    current = get_job(job["job_id"], path=path)
    assert stopped == [["taskkill", "/PID", "6123", "/F"]]
    assert result["status"] == "STOPPED"
    assert current is not None
    assert current["active"] is False


def test_startup_process_query_failure_keeps_job_active_and_blocks_new_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    update_job(
        job["job_id"],
        status="STARTING",
        worker_pid=4321,
        launch_token="launch-token",
        worker_identity=persisted_worker_identity(job["job_id"]),
        path=path,
    )
    monkeypatch.setattr(
        optimizer_jobs,
        "_process_identity",
        lambda _pid: (_ for _ in ()).throw(RuntimeError("synthetic query failure")),
    )

    result = reconcile_optimizer_startup(path=path)

    current = get_job(job["job_id"], path=path)
    assert result == {
        "status": "BLOCKED",
        "job_id": job["job_id"],
        "action": "OPTIMIZER_PROCESS_OWNERSHIP_UNVERIFIED",
    }
    assert current is not None
    assert current["status"] == "RECONCILIATION_REQUIRED"
    assert current["active"] is True
    with pytest.raises(RuntimeError, match="already active"):
        create_job(
            {"max_rounds": 1},
            evidence_root=tmp_path / "artifacts" / "optimizer",
            path=path,
        )


def test_startup_does_not_treat_unverifiable_occupied_pid_as_worker_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    update_job(
        job["job_id"],
        status="STARTING",
        worker_pid=4321,
        launch_token="launch-token",
        worker_identity=persisted_worker_identity(job["job_id"]),
        path=path,
    )
    monkeypatch.setattr(
        optimizer_jobs,
        "_process_identity",
        lambda pid: {
            "pid": pid,
            "executable_path": r"C:\Python313\python.exe",
            "creation_time": "",
        },
    )

    result = reconcile_optimizer_startup(path=path)

    current = get_job(job["job_id"], path=path)
    assert result["status"] == "BLOCKED"
    assert result["action"] == "OPTIMIZER_PROCESS_IDENTITY_MISMATCH"
    assert current is not None
    assert current["status"] == "RECONCILIATION_REQUIRED"
    assert current["active"] is True


def test_startup_rejects_pid_reuse_when_creation_identity_differs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    executable = r"C:\Python313\python.exe"
    update_job(
        job["job_id"],
        status="STARTING",
        worker_pid=4321,
        launch_token="launch-token",
        worker_identity=persisted_worker_identity(
            job["job_id"],
            executable=executable,
            creation_time="old-process",
        ),
        path=path,
    )
    monkeypatch.setattr(
        optimizer_jobs,
        "_process_identity",
        lambda pid: {
            "pid": pid,
            "executable_path": executable,
            "creation_time": "new-process",
        },
    )

    result = reconcile_optimizer_startup(path=path)

    current = get_job(job["job_id"], path=path)
    assert result["status"] == "BLOCKED"
    assert result["action"] == "OPTIMIZER_PROCESS_IDENTITY_MISMATCH"
    assert current is not None
    assert current["status"] == "RECONCILIATION_REQUIRED"
    assert current["active"] is True


def test_stopped_launch_token_cannot_enter_optimizer_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        optimizer_worker,
        "get_job",
        lambda _job_id: {
            "job_id": "job-1",
            "launch_token": "token-1",
            "active": False,
            "status": "STOPPED",
            "request": {},
        },
    )
    monkeypatch.setattr(
        optimizer_worker,
        "_load_committed_winner",
        lambda *_args: (_ for _ in ()).throw(AssertionError("worker continued after STOPPED")),
    )

    with pytest.raises(RuntimeError, match="OPTIMIZER_WORKER_LAUNCH_CLAIM_LOST"):
        optimizer_worker.run_job("job-1", launch_token="token-1")


def test_worker_waits_for_parent_to_persist_its_launch_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_pid = 9876
    job = {
        "job_id": "job-1",
        "launch_token": "token-1",
        "active": True,
        "status": "STARTING",
        "worker_pid": None,
        "request": {"max_rounds": 1},
    }
    reads = iter([dict(job), {**job, "worker_pid": worker_pid}])
    monkeypatch.setattr(optimizer_worker, "get_job", lambda _job_id: next(reads))
    monkeypatch.setattr(optimizer_worker.os, "getpid", lambda: worker_pid)

    result = optimizer_worker._await_worker_launch_confirmation(
        "job-1",
        "token-1",
        resume=False,
    )

    assert result["worker_pid"] == worker_pid


def test_worker_log_open_failure_does_not_leave_an_active_launch_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    blocked_evidence = tmp_path / "not-a-directory"
    blocked_evidence.write_text("synthetic", encoding="utf-8")
    persisted_get_job = optimizer_jobs.get_job
    persisted_claim = optimizer_jobs.claim_initial_worker_launch
    persisted_update = optimizer_jobs.update_job
    monkeypatch.setattr(
        optimizer_jobs,
        "get_job",
        lambda job_id: persisted_get_job(job_id, path=path),
    )
    monkeypatch.setattr(
        optimizer_jobs,
        "claim_initial_worker_launch",
        lambda job_id, token: persisted_claim(job_id, token, path=path),
    )

    def update_in_test_db(job_id: str, **changes: object) -> dict[str, object]:
        changes["path"] = path
        return persisted_update(job_id, **changes)

    monkeypatch.setattr(optimizer_jobs, "update_job", update_in_test_db)
    monkeypatch.setattr(
        optimizer_jobs,
        "job_evidence_dir",
        lambda _job_id: blocked_evidence,
    )

    with pytest.raises(RuntimeError, match="OPTIMIZER_WORKER_LOG_OPEN_FAILED"):
        optimizer_jobs._spawn_worker(job["job_id"], resume=False)

    current = get_job(job["job_id"], path=path)
    assert current is not None
    assert current["status"] == "FAILED"
    assert current["active"] is False
    assert current["first_blocker"] == "OPTIMIZER_WORKER_LOG_OPEN_FAILED"


def test_stop_without_persisted_worker_identity_requires_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    update_job(
        job["job_id"],
        status="STARTING",
        active=True,
        launch_token="token-1",
        path=path,
    )
    persisted_get_job = optimizer_jobs.get_job
    persisted_update_job = optimizer_jobs.update_job
    monkeypatch.setattr(
        optimizer_jobs,
        "get_job",
        lambda job_id, **_kwargs: persisted_get_job(job_id, path=path),
    )

    def update_in_test_db(job_id: str, **changes: object) -> dict[str, object]:
        changes["path"] = path
        return persisted_update_job(job_id, **changes)

    monkeypatch.setattr(optimizer_jobs, "update_job", update_in_test_db)
    with pytest.raises(RuntimeError, match="Optimizer worker ownership could not be proven"):
        optimizer_jobs.stop_optimizer(job["job_id"])

    current = get_job(job["job_id"], path=path)
    assert current is not None
    assert current["status"] == "RECONCILIATION_REQUIRED"
    assert current["active"] is False
    assert current["worker_pid"] is None
    assert current["first_blocker"] == "OPTIMIZER_WORKER_STOP_OWNERSHIP_UNVERIFIED"
    stopped = optimizer_jobs.stop_optimizer(job["job_id"])
    assert stopped["status"] == "STOPPED"
    assert "revoking an unconfirmed launch claim" in stopped["message"]


def test_resume_pid_reuse_requires_reconciliation_instead_of_new_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    identity = persisted_worker_identity(job["job_id"])
    update_job(
        job["job_id"],
        status="WAITING_FOR_REPORT",
        worker_pid=4321,
        launch_token="launch-token",
        worker_identity=identity,
        path=path,
    )
    persisted_get_job = optimizer_jobs.get_job
    persisted_update_job = optimizer_jobs.update_job
    monkeypatch.setattr(
        optimizer_jobs,
        "get_job",
        lambda job_id, **_kwargs: persisted_get_job(job_id, path=path),
    )

    def update_in_test_db(job_id: str, **changes: object) -> dict[str, object]:
        changes["path"] = path
        return persisted_update_job(job_id, **changes)

    monkeypatch.setattr(optimizer_jobs, "update_job", update_in_test_db)
    monkeypatch.setattr(
        optimizer_jobs,
        "_process_identity",
        lambda _pid: {
            "pid": 4321,
            "executable_path": identity["executable_path"],
            "creation_time": "different-process-creation",
        },
    )
    monkeypatch.setattr(
        optimizer_jobs,
        "_spawn_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("resume launched an unverified replacement")),
    )

    with pytest.raises(RuntimeError, match="ownership could not be proven; resume is blocked"):
        optimizer_jobs.resume_optimizer(job["job_id"])

    current = get_job(job["job_id"], path=path)
    assert current is not None
    assert current["status"] == "RECONCILIATION_REQUIRED"
    assert current["active"] is True
    assert current["first_blocker"] == "OPTIMIZER_PROCESS_IDENTITY_MISMATCH"
