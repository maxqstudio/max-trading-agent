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
        "_process_command_line",
        lambda pid: "python.exe unrelated_script.py",
    )

    with pytest.raises(RuntimeError, match="refusing to kill"):
        jobs.stop_optimizer("JOB1")


def test_stop_kills_only_verified_optimizer_process_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "get_job", lambda job_id: active_job())
    monkeypatch.setattr(
        jobs,
        "_process_command_line",
        lambda pid: (
            r"D:\MAX_REBUILD\.venv\Scripts\python.exe "
            "-m max_backend.optimizer_worker --job-id JOB1"
        ),
    )
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

    assert calls == [["taskkill", "/PID", "1234", "/T", "/F"]]
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
    monkeypatch.setattr(jobs, "_worker_is_alive", lambda job_id, pid: True)

    with pytest.raises(RuntimeError, match="still running"):
        jobs.resume_optimizer("JOB1")
