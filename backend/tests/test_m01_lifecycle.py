from __future__ import annotations

from pathlib import Path

import max_backend.optimizer_worker as worker
from max_backend.optimizer_core import OptimizationPass


def request(max_rounds: int = 3) -> dict:
    return {
        "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V1",
        "scientist_assist": False,
        "max_rounds": max_rounds,
        "search_space": {"round": 1},
        "optimize_params": ["InpEntryThreshold"],
        "ea": {"sha256": "sha"},
    }


def winner_row(round_no: int = 1) -> OptimizationPass:
    return OptimizationPass(
        round_no=round_no,
        pass_no=7,
        profit_factor=1.2,
        recovery_factor=0.4,
        expectancy_r=0.2,
        weighted_r=0.1,
        profit=10.0,
        trades=25,
        params={"InpEntryThreshold": 0.2},
        raw={},
        minimum_trades_required=20,
        min_profit_factor_required=1.0,
        min_recovery_factor_required=0.0,
        min_expectancy_r_required=0.0,
        min_weighted_r_required=0.0,
    )


def no_winner_row(round_no: int) -> OptimizationPass:
    row = winner_row(round_no)
    row.profit_factor = 0.8
    return row


class LifecycleHarness:
    def __init__(self, tmp_path: Path, req: dict):
        self.tmp_path = tmp_path
        self.job = {
            "job_id": "JOB",
            "status": "QUEUED",
            "active": True,
            "current_round": 0,
            "request": req,
        }
        self.updates = []
        self.execute_round_calls = []
        self.refine_calls = 0
        self.winner_writes = 0
        self.round_results = []

    def get_job(self, job_id: str):
        return dict(self.job)

    def update_job(self, job_id: str, **kwargs):
        self.job.update({key: value for key, value in kwargs.items() if key in {
            "status", "active", "current_round", "message", "first_blocker",
            "terminal_result", "winner"
        }})
        self.updates.append(dict(kwargs))
        return dict(self.job)

    def execute_round(self, req, *, job_id, round_no, search_space, resume):
        self.execute_round_calls.append(round_no)
        return self.round_results.pop(0)

    def refine(self, search_space, rows, optimize_params):
        self.refine_calls += 1
        return {"round": self.refine_calls + 1}

    def write_winner(self, req, *, job_id, round_no, winner):
        self.winner_writes += 1
        return {
            "round": round_no,
            "mt5_pass": winner.pass_no,
            "profit_factor": winner.profit_factor,
            "recovery_factor": winner.recovery_factor,
            "mean_r": winner.expectancy_r,
            "weighted_r": winner.weighted_r,
            "trades": winner.trades,
        }


def patch_common(monkeypatch, harness: LifecycleHarness) -> None:
    monkeypatch.setattr(worker, "get_job", harness.get_job)
    monkeypatch.setattr(worker, "update_job", harness.update_job)
    monkeypatch.setattr(worker, "get_round", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "compile_ea", lambda *args, **kwargs: {"status": "PASS"})
    monkeypatch.setattr(worker, "execute_round", harness.execute_round)
    monkeypatch.setattr(
        worker,
        "_refine_for_next_round",
        lambda req, *, job_id, source_round, current_space, rows: (
            harness.refine(current_space, rows, req["optimize_params"]),
            {
                "effective_range_source": "DETERMINISTIC_REFINEMENT_SCIENTIST_OFF",
                "actual_provider_calls": 0,
            },
        ),
    )
    monkeypatch.setattr(worker, "_write_winner", harness.write_winner)
    monkeypatch.setattr(
        worker,
        "ensure_challenger_for_winner",
        lambda job_id, expected_round=None, expected_pass=None: {
            "challenger_id": "STRAT-TEST-R01-P7",
            "status": "CHALLENGER",
        },
    )
    monkeypatch.setattr(
        worker,
        "_load_committed_winner",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(worker, "job_evidence_dir", lambda *args, **kwargs: harness.tmp_path)
    monkeypatch.setattr(worker, "round_evidence_dir", lambda *args, **kwargs: harness.tmp_path)
    monkeypatch.setattr(worker, "write_json", lambda *args, **kwargs: None)


def test_winner_stops_immediately_without_next_round(tmp_path, monkeypatch) -> None:
    harness = LifecycleHarness(tmp_path, request(max_rounds=3))
    row = winner_row(1)
    harness.round_results = [
        ([row], {"eligible_passes": 1}, row),
    ]
    patch_common(monkeypatch, harness)

    rc = worker.run_job("JOB")

    assert rc == 0
    assert harness.execute_round_calls == [1]
    assert harness.refine_calls == 0
    assert harness.winner_writes == 1
    assert harness.job["status"] == "STRATEGY_CHALLENGER_FOUND"
    assert harness.job["active"] is False
    assert harness.job["terminal_result"] == "STRATEGY_CHALLENGER_FOUND"


def test_no_winner_refines_deterministically_then_runs_next_round(
    tmp_path,
    monkeypatch,
) -> None:
    harness = LifecycleHarness(tmp_path, request(max_rounds=2))
    row1 = no_winner_row(1)
    row2 = no_winner_row(2)
    harness.round_results = [
        ([row1], {"eligible_passes": 0}, None),
        ([row2], {"eligible_passes": 0}, None),
    ]
    patch_common(monkeypatch, harness)

    rc = worker.run_job("JOB")

    assert rc == 0
    assert harness.execute_round_calls == [1, 2]
    assert harness.refine_calls == 1
    assert harness.winner_writes == 0
    assert harness.job["status"] == "NO_ELIGIBLE_WINNER_MAX_ROUNDS"
    assert harness.job["active"] is False


def test_max_round_one_never_refines(tmp_path, monkeypatch) -> None:
    harness = LifecycleHarness(tmp_path, request(max_rounds=1))
    row = no_winner_row(1)
    harness.round_results = [
        ([row], {"eligible_passes": 0}, None),
    ]
    patch_common(monkeypatch, harness)

    rc = worker.run_job("JOB")

    assert rc == 0
    assert harness.execute_round_calls == [1]
    assert harness.refine_calls == 0
    assert harness.job["status"] == "NO_ELIGIBLE_WINNER_MAX_ROUNDS"


def test_m01_worker_still_has_no_challenger_or_promotion_runtime_dependency() -> None:
    source = Path(worker.__file__).read_text(encoding="utf-8")
    lowered = source.lower()

    assert "register_optimizer_challenger" not in lowered
    assert "promote_strategy" not in lowered
