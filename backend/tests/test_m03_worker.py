from __future__ import annotations

from pathlib import Path

import pytest

import max_backend.optimizer_worker as worker


WINNER = {
    "schema": "MAX_REBUILD_ELIGIBLE_WINNER_V1",
    "job_id": "JOB_M03",
    "round": 2,
    "mt5_pass": 17,
    "ea_sha256": "sha",
    "params": {},
}


class JobHarness:
    def __init__(self, *, status: str = "CHALLENGER_REGISTRATION_FAILED"):
        self.job = {
            "job_id": "JOB_M03",
            "status": status,
            "active": False,
            "current_round": 2,
            "request": {"schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V3", "max_rounds": 3, "search_space": {}, "optimize_params": []},
            "winner": dict(WINNER),
            "terminal_result": "ELIGIBLE_WINNER_FOUND",
        }
        self.updates: list[dict] = []

    def get_job(self, job_id: str):
        return dict(self.job)

    def update_job(self, job_id: str, **kwargs):
        for key in (
            "status",
            "active",
            "current_round",
            "message",
            "first_blocker",
            "terminal_result",
            "winner",
        ):
            if key in kwargs:
                self.job[key] = kwargs[key]
        self.updates.append(dict(kwargs))
        return dict(self.job)


def test_registration_failure_preserves_winner_and_is_resumable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = JobHarness(status="REGISTERING_CHALLENGER")
    monkeypatch.setattr(worker, "update_job", harness.update_job)
    monkeypatch.setattr(worker, "_diagnostic", lambda *args, **kwargs: tmp_path / "diag.json")
    monkeypatch.setattr(
        worker,
        "ensure_challenger_for_winner",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("BUNDLE_FAIL")),
    )

    rc = worker._register_committed_winner("JOB_M03", dict(WINNER))

    assert rc == 4
    assert harness.job["status"] == "CHALLENGER_REGISTRATION_FAILED"
    assert harness.job["terminal_result"] == "ELIGIBLE_WINNER_FOUND"
    assert harness.job["winner"] == WINNER
    assert harness.job["active"] is False
    assert "mark_completed" not in harness.updates[-1]


def test_resume_after_registration_failure_retries_registration_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = JobHarness()
    calls = {"registration": 0}

    monkeypatch.setattr(worker, "get_job", harness.get_job)
    monkeypatch.setattr(worker, "update_job", harness.update_job)
    monkeypatch.setattr(worker, "_load_committed_winner", lambda job_id: dict(WINNER))

    def register(job_id, *, expected_round=None, expected_pass=None):
        calls["registration"] += 1
        assert expected_round == 2
        assert expected_pass == 17
        return {
            "challenger_id": "STRAT-20260922-101450-R02-P17",
            "status": "CHALLENGER",
        }

    monkeypatch.setattr(worker, "ensure_challenger_for_winner", register)

    def forbidden(*args, **kwargs):
        raise AssertionError("scientific/external execution must not run during registration resume")

    monkeypatch.setattr(worker, "compile_ea", forbidden)
    monkeypatch.setattr(worker, "execute_round", forbidden)
    monkeypatch.setattr(worker, "_refine_for_next_round", forbidden)
    monkeypatch.setattr(worker, "_write_winner", forbidden)

    rc = worker.run_job("JOB_M03", resume=True)

    assert rc == 0
    assert calls["registration"] == 1
    assert harness.job["status"] == "STRATEGY_CHALLENGER_FOUND"
    assert harness.job["terminal_result"] == "STRATEGY_CHALLENGER_FOUND"
    assert harness.job["active"] is False


def test_committed_winner_before_registering_row_goes_directly_to_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = JobHarness(status="PARSING_RESULTS")
    calls = {"registration": 0}
    monkeypatch.setattr(worker, "get_job", harness.get_job)
    monkeypatch.setattr(worker, "update_job", harness.update_job)
    monkeypatch.setattr(worker, "_load_committed_winner", lambda job_id: dict(WINNER))
    monkeypatch.setattr(
        worker,
        "ensure_challenger_for_winner",
        lambda *args, **kwargs: calls.__setitem__(
            "registration", calls["registration"] + 1
        )
        or {
            "challenger_id": "STRAT-20260922-101450-R02-P17",
            "status": "CHALLENGER",
        },
    )
    monkeypatch.setattr(
        worker,
        "execute_round",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("MT5 round must not rerun after winner evidence is committed")
        ),
    )

    assert worker.run_job("JOB_M03", resume=True) == 0
    assert calls["registration"] == 1


def test_no_winner_path_never_calls_challenger_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = {
        "job_id": "JOB_NOWINNER",
        "status": "QUEUED",
        "active": True,
        "current_round": 0,
        "request": {
            "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V3",
            "max_rounds": 1,
            "search_space": {"x": 1},
            "optimize_params": ["InpEntryThreshold"],
            "ea": {"sha256": "sha"},
        },
        "winner": None,
    }
    updates: list[dict] = []

    monkeypatch.setattr(worker, "get_job", lambda job_id: dict(job))
    monkeypatch.setattr(
        worker,
        "update_job",
        lambda job_id, **kwargs: updates.append(dict(kwargs)) or {
            **job,
            **kwargs,
        },
    )
    monkeypatch.setattr(worker, "_load_committed_winner", lambda job_id: None)
    monkeypatch.setattr(worker, "compile_ea", lambda *args, **kwargs: {"status": "PASS"})
    monkeypatch.setattr(worker, "write_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "job_evidence_dir", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(worker, "get_round", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        worker,
        "execute_round",
        lambda *args, **kwargs: ([], {"eligible_passes": 0}, None),
    )
    monkeypatch.setattr(
        worker,
        "ensure_challenger_for_winner",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("no-winner job must create zero Challengers")
        ),
    )

    assert worker.run_job("JOB_NOWINNER") == 0
    assert updates[-1]["status"] == "NO_ELIGIBLE_WINNER_MAX_ROUNDS"
