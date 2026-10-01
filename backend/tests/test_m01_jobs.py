from __future__ import annotations

from types import SimpleNamespace

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
    monkeypatch.setattr(jobs, "get_job", lambda job_id: active_job())
    monkeypatch.setattr(
        jobs,
        "_process_identity",
        lambda pid: {
            "pid": pid,
            "executable_path": r"D:\MAX_REBUILD\.venv\Scripts\python.exe",
            "command_line": "python.exe unrelated_script.py",
            "creation_time": "created",
        },
    )

    with pytest.raises(RuntimeError, match="could not be proven"):
        jobs.stop_optimizer("JOB1")


def test_stop_kills_only_verified_optimizer_process_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "get_job", lambda job_id: active_job())
    identity = {
        "pid": 1234,
        "executable_path": r"D:\MAX_REBUILD\.venv\Scripts\python.exe",
        "command_line": (
            r"D:\MAX_REBUILD\.venv\Scripts\python.exe "
            "-m max_backend.optimizer_worker --job-id JOB1"
        ),
        "creation_time": "created",
    }
    identities = iter([identity, identity, None])
    monkeypatch.setattr(jobs, "_process_identity", lambda _pid: next(identities))
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="SUCCESS", stderr="")

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    updated: dict = {}

    def fake_update(job_id: str, **kwargs):
        updated.update(kwargs)
        return {**active_job(), **kwargs}

    monkeypatch.setattr(jobs, "update_job", fake_update)

    result = jobs.stop_optimizer("JOB1")

    assert calls == [["taskkill", "/PID", "1234", "/F"]]
    assert updated["status"] == "STOPPED"
    assert updated["active"] is False
    assert updated["terminal_result"] == "STOPPED"
    assert result["status"] == "STOPPED"


def test_resume_refuses_while_verified_worker_is_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jobs,
        "get_job",
        lambda job_id: active_job(status="WAITING_FOR_REPORT"),
    )
    monkeypatch.setattr(jobs, "_worker_is_alive", lambda job_id, pid, **_kwargs: True)

    with pytest.raises(RuntimeError, match="still running"):
        jobs.resume_optimizer("JOB1")
