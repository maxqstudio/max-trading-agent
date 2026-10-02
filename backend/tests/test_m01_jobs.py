from __future__ import annotations

import pytest

import max_backend.optimizer_jobs as jobs


def active_job(pid: int = 1234, status: str = "MT5_RUNNING") -> dict:
    return {
        "job_id": "JOB1",
        "status": status,
        "active": True,
        "worker_pid": pid,
    }


def test_stop_refuses_unrelated_process_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    job = active_job()
    job["launch_token"] = "token-1"
    job["worker_identity"] = {
        "pid": 1234,
        "job_id": "JOB1",
        "launch_token": "token-1",
        "worker_module": "max_backend.optimizer_worker",
        "worker_executable_path": r"D:\MAX_REBUILD\.venv\Scripts\python.exe",
        "identity_protocol": "named-pipe-hmac-v1",
        "startup_identity": "a" * 64,
        "executable_path": r"D:\MAX_REBUILD\.venv\Scripts\python.exe",
        "creation_time": "created",
    }
    monkeypatch.setattr(jobs, "get_job", lambda job_id, **_kwargs: job)
    monkeypatch.setattr(jobs, "update_job", lambda _job_id, **changes: changes)
    monkeypatch.setattr(
        jobs,
        "_process_identity",
        lambda pid: {
            "pid": pid,
            "executable_path": r"D:\Other\python.exe",
            "creation_time": "other-process",
        },
    )

    with pytest.raises(RuntimeError, match="could not be proven"):
        jobs.stop_optimizer("JOB1")


def test_stop_kills_only_verified_optimizer_process_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = {
        "pid": 1234,
        "job_id": "JOB1",
        "launch_token": "token-1",
        "worker_module": "max_backend.optimizer_worker",
        "worker_executable_path": r"D:\MAX_REBUILD\.venv\Scripts\python.exe",
        "identity_protocol": "named-pipe-hmac-v1",
        "startup_identity": "a" * 64,
        "executable_path": r"D:\MAX_REBUILD\.venv\Scripts\python.exe",
        "creation_time": "created",
    }
    job = active_job()
    job["launch_token"] = "token-1"
    job["worker_identity"] = identity
    monkeypatch.setattr(jobs, "get_job", lambda job_id, **_kwargs: job)
    monkeypatch.setattr(
        jobs,
        "_process_identity",
        lambda _pid: {
            "pid": identity["pid"],
            "executable_path": identity["executable_path"],
            "creation_time": identity["creation_time"],
        },
    )
    terminated: list[tuple[int, dict]] = []

    def fake_terminate(pid: int, *, expected: dict, **_kwargs: object) -> bool:
        terminated.append((pid, expected))
        return True

    monkeypatch.setattr(jobs, "terminate_process_verified", fake_terminate)
    updated: dict = {}

    def fake_update(job_id: str, **kwargs):
        updated.update(kwargs)
        return {**active_job(), **kwargs}

    monkeypatch.setattr(jobs, "update_job", fake_update)

    result = jobs.stop_optimizer("JOB1")

    assert terminated == [(1234, identity)]
    assert updated["status"] == "STOPPED"
    assert updated["active"] is False
    assert updated["terminal_result"] == "STOPPED"
    assert result["status"] == "STOPPED"


def test_resume_refuses_while_verified_worker_is_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = active_job(status="WAITING_FOR_REPORT")
    job["launch_token"] = "token-1"
    job["worker_identity"] = {
        "pid": job["worker_pid"],
        "job_id": job["job_id"],
        "launch_token": "token-1",
        "worker_module": "max_backend.optimizer_worker",
        "worker_executable_path": r"D:\MAX_REBUILD\.venv\Scripts\python.exe",
        "identity_protocol": "named-pipe-hmac-v1",
        "startup_identity": "a" * 64,
        "executable_path": r"D:\MAX_REBUILD\.venv\Scripts\python.exe",
        "creation_time": "created",
    }
    monkeypatch.setattr(jobs, "get_job", lambda job_id, **_kwargs: job)
    monkeypatch.setattr(
        jobs,
        "_process_identity",
        lambda _pid: {
            "pid": job["worker_pid"],
            "executable_path": job["worker_identity"]["executable_path"],
            "creation_time": job["worker_identity"]["creation_time"],
        },
    )

    with pytest.raises(RuntimeError, match="still running"):
        jobs.resume_optimizer("JOB1")
