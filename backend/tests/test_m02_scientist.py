from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

import max_backend.optimizer_core as core
from max_backend.workflow_contract import OPTIMIZER_REQUEST_SCHEMA_CURRENT
import max_backend.optimizer_worker as worker
import max_backend.optimizer_scientist_transition as transition
from max_backend.optimizer_core import (
    DEFAULT_KPI,
    DEFAULT_SPACE,
    OptimizationPass,
    deterministic_refine,
)
from max_backend.optimizer_scientist import (
    ScientistAdvisoryError,
    ScientistCallResult,
    build_bounded_payload,
    parse_proposal,
    sanitize_recursive,
    validate_proposal,
)


def full_params(**overrides):
    values = core.read_ea_optimizer_defaults()
    values.update(overrides)
    return values


def no_winner_row(pass_no: int = 1) -> OptimizationPass:
    return OptimizationPass(
        round_no=1,
        pass_no=pass_no,
        profit_factor=1.20,
        recovery_factor=0.40,
        expectancy_r=0.20,
        weighted_r=0.10,
        profit=10.0,
        trades=20,
        params=full_params(InpEntryThreshold=0.20),
        raw={},
        minimum_trades_required=30,
        min_profit_factor_required=1.0,
        min_recovery_factor_required=0.0,
        min_expectancy_r_required=0.0,
        min_weighted_r_required=0.0,
    )


def winner_row() -> OptimizationPass:
    row = no_winner_row()
    row.trades = 35
    return row


def scientist_request(
    *,
    enabled: bool = True,
    max_rounds: int = 2,
    selected: list[str] | None = None,
    api_key_env: str = "M02_TEST_API_KEY",
) -> dict:
    return {
        "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V2",
        "scientist_assist": enabled,
        "scientist": {
            "provider": "test-provider",
            "base_url": "https://example.invalid/v1/",
            "model": "test-model",
            "api_key_env": api_key_env,
            "timeout_sec": 5,
        },
        "max_rounds": max_rounds,
        "search_space": deepcopy(DEFAULT_SPACE),
        "optimize_params": selected or ["InpEntryThreshold"],
        "fixed_param_values": full_params(),
        "kpi": deepcopy(DEFAULT_KPI),
        "trade_sample": {"minimum_trades": 30},
        "ea": {"sha256": "sha"},
    }


class TransitionStore:
    def __init__(self, tmp_path: Path, space: dict | None = None):
        self.tmp_path = tmp_path
        self.state = {
            "phase": "PARSED",
            "search_space": deepcopy(space or DEFAULT_SPACE),
        }
        self.job_updates: list[dict] = []

    def get_round(self, job_id: str, round_no: int):
        return {
            "job_id": job_id,
            "round_no": round_no,
            "phase": "PARSED",
            "state": deepcopy(self.state),
            "report_path": "report.xml",
            "report_sha256": "reportsha",
            "sidecar_path": "metrics.csv",
            "sidecar_sha256": "metricsha",
            "parsed_passes": 1,
            "eligible_passes": 0,
            "winner_pass": None,
        }

    def upsert_round(self, job_id, round_no, *, phase, state, **kwargs):
        self.state = deepcopy(state)
        self.state["phase"] = phase
        return {
            "job_id": job_id,
            "round_no": round_no,
            "phase": phase,
            "state": deepcopy(self.state),
            **kwargs,
        }

    def update_job(self, job_id: str, **kwargs):
        self.job_updates.append(dict(kwargs))
        return kwargs


def patch_transition_store(
    monkeypatch: pytest.MonkeyPatch,
    store: TransitionStore,
) -> None:
    monkeypatch.setattr(transition, "get_round", store.get_round)
    monkeypatch.setattr(transition, "upsert_round", store.upsert_round)
    monkeypatch.setattr(transition, "update_job", store.update_job)
    monkeypatch.setattr(
        transition,
        "round_evidence_dir",
        lambda job_id, round_no: store.tmp_path,
    )


def legal_response() -> ScientistCallResult:
    return ScientistCallResult(
        text=json.dumps(
            {
                "ranges": {
                    "InpEntryThreshold": {
                        "start": 0.18,
                        "step": 0.02,
                        "stop": 0.20,
                    }
                },
                "reason": "Keep the bounded range around the strongest completed passes.",
            }
        ),
        provenance={
            "configured_provider": "test-provider",
            "configured_model": "test-model",
            "actual_provider": "test-provider",
            "actual_model": "test-model",
            "base_url": "https://example.invalid/v1/",
            "phase": "STRATEGY_OPTIMIZER_RANGE_PROPOSAL",
            "temperature": 0.10,
            "status": "PASS",
            "usage": {"prompt_tokens": 100, "completion_tokens": 40},
            "cost": None,
            "fallback_route_used": False,
        },
    )


def test_bounded_payload_max_12_and_allowed_context_only() -> None:
    req = scientist_request(selected=["InpEntryThreshold", "InpSL_ATR"])
    rows = [no_winner_row(pass_no=i) for i in range(1, 20)]
    payload = build_bounded_payload(
        req["search_space"],
        rows,
        req,
        source_round=1,
        target_round=2,
    )

    assert len(payload["top_mt5_passes"]) == 12
    assert payload["selected_parameters"] == ["InpEntryThreshold", "InpSL_ATR"]
    assert set(payload["current_ranges"]) == set(payload["selected_parameters"])
    assert set(payload["hard_bounds"]) == set(payload["selected_parameters"])
    assert "symbol" not in payload
    assert "relative_symbol" not in payload
    assert "from_date" not in payload
    assert "to_date" not in payload
    assert "champion" not in json.dumps(payload).lower()
    assert all(
        set(item) == {
            "mt5_pass",
            "pf",
            "rf",
            "mean_r",
            "weighted_r",
            "trades",
            "failed_gates",
            "params",
        }
        for item in payload["top_mt5_passes"]
    )


def test_parse_and_validate_legal_proposal() -> None:
    proposal = parse_proposal(legal_response().text)
    merged, selected = validate_proposal(
        deepcopy(DEFAULT_SPACE),
        proposal,
        ["InpEntryThreshold"],
    )
    assert selected["InpEntryThreshold"] == {
        "start": 0.18,
        "step": 0.02,
        "stop": 0.20,
    }
    assert merged["InpEntryThreshold"] == selected["InpEntryThreshold"]
    assert merged["InpWeightTrend"] == DEFAULT_SPACE["InpWeightTrend"]


@pytest.mark.parametrize(
    "proposal,match",
    [
        (
            {"ranges": {"InpEntryThreshold": {"start": 0.10, "step": 0.02, "stop": 0.20}}},
            "SCIENTIST_OUT_OF_BOUNDS",
        ),
        (
            {"ranges": {"InpEntryThreshold": {"start": 0.18, "step": 0.01, "stop": 0.20}}},
            "finer than base step",
        ),
        (
            {
                "ranges": {
                    "InpEntryThreshold": {"start": 0.18, "step": 0.02, "stop": 0.20},
                    "InpTP_ATR": {"start": 1.2, "step": 0.2, "stop": 2.0},
                }
            },
            "SCIENTIST_PARAMETER_SET_MISMATCH",
        ),
        (
            {"ranges": {}},
            "SCIENTIST_PARAMETER_SET_MISMATCH",
        ),
        (
            {
                "ranges": {"InpEntryThreshold": {"start": 0.18, "step": 0.02, "stop": 0.20}},
                "min_profit_factor": 0.5,
            },
            "unexpected top-level",
        ),
    ],
)
def test_proposal_rejects_authority_or_range_escape(proposal, match) -> None:
    with pytest.raises(ValueError, match=match):
        validate_proposal(
            deepcopy(DEFAULT_SPACE),
            proposal,
            ["InpEntryThreshold"],
        )


def test_integer_parameter_never_silently_rounds() -> None:
    proposal = {
        "ranges": {
            "InpMaxHoldBars": {
                "start": 13.5,
                "step": 6,
                "stop": 30,
            }
        }
    }
    with pytest.raises(ValueError, match="integer values"):
        validate_proposal(DEFAULT_SPACE, proposal, ["InpMaxHoldBars"])


def test_malformed_json_is_rejected_without_eval() -> None:
    with pytest.raises(ValueError, match="SCIENTIST_MALFORMED_RESPONSE"):
        parse_proposal("not JSON")
    with pytest.raises(ValueError):
        parse_proposal("{'ranges': {'InpEntryThreshold': {'start': 0.18}}}")


def test_sanitizer_removes_secret_bearing_keys_recursively() -> None:
    clean = sanitize_recursive(
        {
            "api_key": "SECRET",
            "nested": {
                "Authorization": "Bearer SECRET",
                "token": "SECRET",
                "model": "ok",
            },
        }
    )
    serialized = json.dumps(clean)
    assert "SECRET" not in serialized
    assert clean == {"nested": {"model": "ok"}}


def test_scientist_off_uses_exact_m01_deterministic_refine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = scientist_request(enabled=False)
    store = TransitionStore(tmp_path)
    patch_transition_store(monkeypatch, store)
    row = no_winner_row()
    expected = deterministic_refine(
        req["search_space"],
        [row],
        req["optimize_params"],
    )

    next_space, decision = transition._refine_for_next_round(
        req,
        job_id="JOB_OFF",
        source_round=1,
        current_space=req["search_space"],
        rows=[row],
    )

    assert next_space == expected
    assert decision["mode"] == "DETERMINISTIC_ONLY"
    assert decision["actual_llm_call"] is False
    assert decision["fallbacks"] == 0


def test_legal_scientist_proposal_called_once_and_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "M02_SECRET_MUST_NOT_PERSIST_LEGAL"
    monkeypatch.setenv("M02_TEST_API_KEY", secret)
    req = scientist_request()
    original = deepcopy(req)
    store = TransitionStore(tmp_path)
    patch_transition_store(monkeypatch, store)
    calls = {"count": 0}

    def fake_call(route, payload):
        calls["count"] += 1
        return legal_response()

    monkeypatch.setattr(transition, "propose_optimizer_ranges", fake_call)

    next_space, decision = transition._refine_for_next_round(
        req,
        job_id="JOB_LEGAL",
        source_round=1,
        current_space=req["search_space"],
        rows=[no_winner_row()],
    )

    assert calls["count"] == 1
    assert decision["mode"] == "SCIENTIST_PROPOSAL"
    assert decision["actual_llm_call"] is True
    assert decision["accepted"] is True
    assert decision["validation"]["status"] == "ACCEPTED"
    assert decision["actual_provider_calls"] == 1
    assert decision["confirmed_provider_calls"] == 1
    assert decision["unconfirmed_provider_attempts"] == 0
    assert decision["provider_call_state"] == "CONFIRMED"
    assert decision["effective_range_source"] == "SCIENTIST_PROPOSAL"
    assert next_space["InpEntryThreshold"] == {
        "start": 0.18,
        "step": 0.02,
        "stop": 0.20,
    }
    assert req["kpi"] == original["kpi"]
    assert req["optimize_params"] == original["optimize_params"]
    files = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in tmp_path.iterdir()
        if path.is_file()
    )
    assert secret not in files
    request_evidence = json.loads(
        (tmp_path / "scientist_request.json").read_text(encoding="utf-8")
    )
    assert "symbol" not in request_evidence["payload"]
    assert "api_key" not in request_evidence["route"]
    assert request_evidence["route"]["api_key_env"] == "M02_TEST_API_KEY"
    assert secret not in json.dumps(request_evidence)


@pytest.mark.parametrize(
    "response_text,category",
    [
        (
            json.dumps(
                {
                    "ranges": {
                        "InpEntryThreshold": {
                            "start": 0.10,
                            "step": 0.02,
                            "stop": 0.20,
                        }
                    },
                    "reason": "illegal lower bound",
                }
            ),
            "SCIENTIST_OUT_OF_BOUNDS",
        ),
        (
            json.dumps(
                {
                    "ranges": {
                        "InpEntryThreshold": {
                            "start": 0.18,
                            "step": 0.02,
                            "stop": 0.20,
                        },
                        "InpTP_ATR": {
                            "start": 1.2,
                            "step": 0.2,
                            "stop": 2.0,
                        },
                    }
                }
            ),
            "SCIENTIST_PARAMETER_SET_MISMATCH",
        ),
        ("not JSON", "SCIENTIST_MALFORMED_RESPONSE"),
    ],
)
def test_invalid_scientist_proposal_falls_back_without_clipping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response_text: str,
    category: str,
) -> None:
    monkeypatch.setenv("M02_TEST_API_KEY", "secret")
    req = scientist_request()
    store = TransitionStore(tmp_path)
    patch_transition_store(monkeypatch, store)
    row = no_winner_row()
    expected = deterministic_refine(
        req["search_space"],
        [row],
        req["optimize_params"],
    )
    monkeypatch.setattr(
        transition,
        "propose_optimizer_ranges",
        lambda route, payload: ScientistCallResult(
            text=response_text,
            provenance=legal_response().provenance,
        ),
    )

    next_space, decision = transition._refine_for_next_round(
        req,
        job_id="JOB_BAD",
        source_round=1,
        current_space=req["search_space"],
        rows=[row],
    )

    assert next_space == expected
    assert decision["accepted"] is False
    assert decision["actual_llm_call"] is True
    assert decision["validation"]["status"] == "REJECTED"
    assert decision["validation"]["reason"] == category
    assert decision["effective_range_source"] == (
        "DETERMINISTIC_REFINEMENT_AFTER_REJECTED_SCIENTIST_PROPOSAL"
    )


def test_missing_route_and_missing_credential_do_not_call_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"count": 0}

    def must_not_call(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(transition, "propose_optimizer_ranges", must_not_call)

    for suffix, route in (
        (
            "route",
            {
                "provider": "",
                "base_url": "",
                "model": "",
                "api_key_env": "",
                "timeout_sec": 5,
            },
        ),
        (
            "credential",
            {
                "provider": "test",
                "base_url": "https://example.invalid/v1/",
                "model": "model",
                "api_key_env": "M02_MISSING_KEY",
                "timeout_sec": 5,
            },
        ),
    ):
        work = tmp_path / suffix
        work.mkdir()
        req = scientist_request()
        req["scientist"] = route
        store = TransitionStore(work)
        patch_transition_store(monkeypatch, store)
        _, decision = transition._refine_for_next_round(
            req,
            job_id=f"JOB_{suffix}",
            source_round=1,
            current_space=req["search_space"],
            rows=[no_winner_row()],
        )
        assert decision["mode"] == "DETERMINISTIC_FALLBACK"
        assert decision["actual_llm_call"] is False
        assert decision["fallbacks"] == 1
    assert called["count"] == 0


@pytest.mark.parametrize(
    "category",
    ["SCIENTIST_TIMEOUT", "SCIENTIST_PROVIDER_ERROR"],
)
def test_provider_failure_is_deterministic_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
) -> None:
    monkeypatch.setenv("M02_TEST_API_KEY", "secret")
    req = scientist_request()
    store = TransitionStore(tmp_path)
    patch_transition_store(monkeypatch, store)
    monkeypatch.setattr(
        transition,
        "propose_optimizer_ranges",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ScientistAdvisoryError(
                category,
                category,
                actual_llm_call=True,
                provenance={"status": "FAIL", "error_category": category},
            )
        ),
    )

    _, decision = transition._refine_for_next_round(
        req,
        job_id="JOB_PROVIDER_DOWN",
        source_round=1,
        current_space=req["search_space"],
        rows=[no_winner_row()],
    )

    assert decision["mode"] == "DETERMINISTIC_FALLBACK"
    assert decision["actual_llm_call"] is True
    assert decision["actual_provider_calls"] == 1
    assert decision["confirmed_provider_calls"] == 1
    assert decision["unconfirmed_provider_attempts"] == 0
    assert decision["provider_call_state"] == "CONFIRMED"
    assert decision["fallbacks"] == 1
    assert decision["error_category"] == category


def test_committed_transition_is_idempotent_and_never_calls_provider_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("M02_TEST_API_KEY", "secret")
    req = scientist_request()
    store = TransitionStore(tmp_path)
    patch_transition_store(monkeypatch, store)
    calls = {"count": 0}

    def fake_call(*args, **kwargs):
        calls["count"] += 1
        return legal_response()

    monkeypatch.setattr(transition, "propose_optimizer_ranges", fake_call)
    first_space, first = transition._refine_for_next_round(
        req,
        job_id="JOB_IDEMPOTENT",
        source_round=1,
        current_space=req["search_space"],
        rows=[no_winner_row()],
    )
    second_space, second = transition._refine_for_next_round(
        req,
        job_id="JOB_IDEMPOTENT",
        source_round=1,
        current_space=req["search_space"],
        rows=[no_winner_row()],
    )

    assert calls["count"] == 1
    assert second == first
    assert second_space == first_space


@pytest.mark.parametrize(
    "status,provider_call_state,confirmed_calls,unconfirmed_attempts,actual_llm_call",
    [
        ("CALL_RESERVED", "NOT_STARTED", 0, 0, False),
        ("CALL_IN_FLIGHT", "UNCONFIRMED", 0, 1, False),
        ("CALL_IN_FLIGHT", "CONFIRMED", 1, 0, True),
    ],
)
def test_restart_with_uncommitted_call_never_retries_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    provider_call_state: str,
    confirmed_calls: int,
    unconfirmed_attempts: int,
    actual_llm_call: bool,
) -> None:
    req = scientist_request()
    store = TransitionStore(tmp_path)
    store.state["scientist_transition"] = {
        "transition_id": "JOB_CRASH:1:2",
        "status": status,
        "proposal_attempts": 1,
        "actual_provider_calls": confirmed_calls,
        "confirmed_provider_calls": confirmed_calls,
        "unconfirmed_provider_attempts": unconfirmed_attempts,
        "provider_call_state": provider_call_state,
        "request_payload_sha256": "hash",
        "provider_provenance": {"status": status},
    }
    patch_transition_store(monkeypatch, store)
    monkeypatch.setattr(
        transition,
        "propose_optimizer_ranges",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("provider must not be retried")
        ),
    )

    _, decision = transition._refine_for_next_round(
        req,
        job_id="JOB_CRASH",
        source_round=1,
        current_space=req["search_space"],
        rows=[no_winner_row()],
    )

    assert decision["error_category"] == "SCIENTIST_UNCONFIRMED_CALL_AFTER_RESTART"
    assert decision["actual_provider_calls"] == confirmed_calls
    assert decision["confirmed_provider_calls"] == confirmed_calls
    assert decision["unconfirmed_provider_attempts"] == unconfirmed_attempts
    assert decision["provider_call_state"] == provider_call_state
    assert decision["actual_llm_call"] is actual_llm_call
    assert decision["effective_range_source"] == (
        "DETERMINISTIC_FALLBACK_AFTER_UNCONFIRMED_SCIENTIST_CALL"
    )


def test_legacy_inflight_crash_window_is_unconfirmed_not_false_confirmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = scientist_request()
    store = TransitionStore(tmp_path)
    store.state["scientist_transition"] = {
        "transition_id": "JOB_LEGACY_CRASH:1:2",
        "status": "CALL_IN_FLIGHT",
        "proposal_attempts": 1,
        # Historical defect: this value was written before dispatch was provable.
        "actual_provider_calls": 1,
        "request_payload_sha256": "hash",
        "provider_provenance": {"status": "CALL_IN_FLIGHT"},
    }
    patch_transition_store(monkeypatch, store)
    monkeypatch.setattr(
        transition,
        "propose_optimizer_ranges",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("provider must not be retried after uncertain crash")
        ),
    )

    _, decision = transition._refine_for_next_round(
        req,
        job_id="JOB_LEGACY_CRASH",
        source_round=1,
        current_space=req["search_space"],
        rows=[no_winner_row()],
    )

    assert decision["provider_call_state"] == "UNCONFIRMED"
    assert decision["confirmed_provider_calls"] == 0
    assert decision["unconfirmed_provider_attempts"] == 1
    assert decision["actual_provider_calls"] == 0
    assert decision["actual_llm_call"] is False
    assert decision["fallbacks"] == 1


class RunJobHarness:
    def __init__(self, tmp_path: Path, req: dict, result):
        self.tmp_path = tmp_path
        self.req = req
        self.result = result
        self.job = {
            "job_id": "RUNJOB",
            "status": "QUEUED",
            "active": True,
            "current_round": 0,
            "request": req,
        }
        self.execute_calls: list[int] = []
        self.updates: list[dict] = []

    def get_job(self, job_id: str):
        return deepcopy(self.job)

    def update_job(self, job_id: str, **kwargs):
        self.job.update(
            {
                key: value
                for key, value in kwargs.items()
                if key in {
                    "status",
                    "active",
                    "current_round",
                    "message",
                    "first_blocker",
                    "terminal_result",
                    "winner",
                }
            }
        )
        self.updates.append(dict(kwargs))
        return deepcopy(self.job)

    def execute_round(self, request, *, job_id, round_no, search_space, resume):
        self.execute_calls.append(round_no)
        return self.result


def patch_run_job(monkeypatch: pytest.MonkeyPatch, harness: RunJobHarness) -> None:
    monkeypatch.setattr(worker, "get_job", harness.get_job)
    monkeypatch.setattr(worker, "update_job", harness.update_job)
    monkeypatch.setattr(worker, "get_round", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "compile_ea", lambda *args, **kwargs: {"status": "PASS"})
    monkeypatch.setattr(worker, "write_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "job_evidence_dir", lambda *args, **kwargs: harness.tmp_path)
    monkeypatch.setattr(worker, "execute_round", harness.execute_round)
    monkeypatch.setattr(
        worker,
        "_write_winner",
        lambda request, *, job_id, round_no, winner: {
            "job_id": job_id,
            "round": round_no,
            "mt5_pass": winner.pass_no,
        },
    )
    monkeypatch.setattr(
        worker,
        "ensure_challenger_for_winner",
        lambda job_id, *, expected_round, expected_pass: {
            "challenger_id": (
                f"STRAT-TEST-R{int(expected_round):02d}-P{int(expected_pass)}"
            ),
            "status": "CHALLENGER",
        },
    )


def test_direct_winner_with_scientist_on_has_zero_call_and_no_round2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = scientist_request(enabled=True, max_rounds=3)
    row = winner_row()
    harness = RunJobHarness(
        tmp_path,
        req,
        ([row], {"eligible_passes": 1}, row),
    )
    patch_run_job(monkeypatch, harness)
    monkeypatch.setattr(
        worker,
        "_refine_for_next_round",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Scientist refinement must not run after winner")
        ),
    )

    rc = worker.run_job("RUNJOB")

    assert rc == 0
    assert harness.execute_calls == [1]
    assert harness.job["status"] == "STRATEGY_CHALLENGER_FOUND"
    assert harness.job["terminal_result"] == "STRATEGY_CHALLENGER_FOUND"


def test_max_round_exhausted_with_scientist_on_has_zero_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = scientist_request(enabled=True, max_rounds=1)
    row = no_winner_row()
    harness = RunJobHarness(
        tmp_path,
        req,
        ([row], {"eligible_passes": 0}, None),
    )
    patch_run_job(monkeypatch, harness)
    monkeypatch.setattr(
        worker,
        "_refine_for_next_round",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Scientist refinement must not run after max round")
        ),
    )

    rc = worker.run_job("RUNJOB")

    assert rc == 0
    assert harness.execute_calls == [1]
    assert harness.job["status"] == "NO_ELIGIBLE_WINNER_MAX_ROUNDS"


def _fake_mt5() -> dict:
    return {
        "status": "READY_EXECUTABLE_AND_DATA_ROOT",
        "reason": "READY",
        "terminal": r"C:\Fake\MetaTrader 5\terminal64.exe",
        "metaeditor": r"C:\Fake\MetaTrader 5\metaeditor64.exe",
        "data_root": r"C:\Fake\TerminalData",
    }


def _raw_freeze_request(**overrides) -> dict:
    payload = {
        "symbol": "XAUUSD.m",
        "relative_symbol": "EURUSD.m",
        "period": "H1",
        "from_date": "2026.01.01",
        "to_date": "2026.02.01",
        "deposit": 10000,
        "leverage": 100,
        "model": 1,
        "optimization": 2,
        "max_rounds": 2,
        "optimize_params": ["InpEntryThreshold"],
        "search_space": deepcopy(DEFAULT_SPACE),
        "kpi": {
            "min_profit_factor": 1.0,
            "min_recovery_factor": 0.0,
            "min_expectancy_r": 0.0,
            "min_weighted_r": 0.0,
            "base_h1_trades_per_month": 20,
        },
        "scientist_assist": True,
    }
    payload.update(overrides)
    return payload


def test_request_freezes_server_scientist_route_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = {
        "provider": "provider",
        "base_url": "https://provider.example/v1/",
        "model": "model",
        "api_key_env": "M02_ROUTE_KEY",
        "timeout_sec": 17,
    }
    monkeypatch.setattr(core, "detect_mt5", _fake_mt5)
    monkeypatch.setattr(core, "route_config_from_environment", lambda: deepcopy(route))
    monkeypatch.setenv("M02_ROUTE_KEY", "M02_SECRET_MUST_NOT_PERSIST_FREEZE")

    frozen = core.freeze_request(_raw_freeze_request())

    assert frozen["schema"] == OPTIMIZER_REQUEST_SCHEMA_CURRENT
    assert frozen["scientist_assist"] is True
    assert frozen["scientist"] == route
    serialized = json.dumps(frozen, sort_keys=True)
    assert "M02_SECRET_MUST_NOT_PERSIST_FREEZE" not in serialized
    assert "api_key" not in frozen["scientist"]
    assert frozen["scientist"]["api_key_env"] == "M02_ROUTE_KEY"


@pytest.mark.parametrize("field", ["scientist", "scientist_llm"])
def test_frontend_cannot_supply_scientist_route(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    monkeypatch.setattr(core, "detect_mt5", _fake_mt5)
    payload = _raw_freeze_request(
        **{field: {"provider": "attacker", "api_key": "secret"}}
    )
    with pytest.raises(ValueError, match="server-configured"):
        core.freeze_request(payload)


def test_provider_secret_echo_is_blocked_and_not_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "M02_SECRET_MUST_NOT_PERSIST_ECHO"
    monkeypatch.setenv("M02_TEST_API_KEY", secret)
    req = scientist_request()
    store = TransitionStore(tmp_path)
    patch_transition_store(monkeypatch, store)
    monkeypatch.setattr(
        transition,
        "propose_optimizer_ranges",
        lambda *args, **kwargs: ScientistCallResult(
            text=secret + legal_response().text,
            provenance=legal_response().provenance,
        ),
    )

    _, decision = transition._refine_for_next_round(
        req,
        job_id="JOB_SECRET_ECHO",
        source_round=1,
        current_space=req["search_space"],
        rows=[no_winner_row()],
    )

    assert decision["accepted"] is False
    assert decision["actual_llm_call"] is True
    assert decision["error_category"] == "SCIENTIST_SECRET_ECHO_BLOCKED"
    assert not (tmp_path / "scientist_raw_response.txt").exists()
    retained = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in tmp_path.iterdir()
        if path.is_file()
    )
    assert secret not in retained


def test_run_job_resume_after_committed_scientist_decision_runs_round2_without_duplicate_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = scientist_request(enabled=True, max_rounds=2)
    effective = deepcopy(req["search_space"])
    effective["InpEntryThreshold"] = {
        "start": 0.18,
        "step": 0.02,
        "stop": 0.20,
    }
    decision = {
        "schema": "MAX_REBUILD_SCIENTIST_DECISION_V1",
        "job_id": "JOB_RESUME_DECISION",
        "transition_id": "JOB_RESUME_DECISION:1:2",
        "source_round": 1,
        "target_round": 2,
        "mode": "SCIENTIST_PROPOSAL",
        "actual_llm_call": True,
        "proposal_attempts": 1,
        "actual_provider_calls": 1,
        "accepted_proposals": 1,
        "rejected_proposals": 0,
        "fallbacks": 0,
        "proposal": {
            "ranges": {
                "InpEntryThreshold": {
                    "start": 0.18,
                    "step": 0.02,
                    "stop": 0.20,
                }
            },
            "reason": "committed before restart",
        },
        "accepted": True,
        "validation": {"status": "ACCEPTED", "reason": "validated"},
        "effective_range_source": "SCIENTIST_PROPOSAL",
        "effective_ranges": effective,
        "request_payload_sha256": "payload-sha",
        "provider_provenance": {"status": "PASS"},
        "created_utc": "2026-09-22T00:00:00+00:00",
    }
    source_record = {
        "job_id": "JOB_RESUME_DECISION",
        "round_no": 1,
        "phase": "PARSED",
        "state": {
            "phase": "PARSED",
            "round": 1,
            "search_space": deepcopy(req["search_space"]),
            "scientist_transition": {
                "transition_id": "JOB_RESUME_DECISION:1:2",
                "status": "COMMITTED",
                "proposal_attempts": 1,
                "actual_provider_calls": 1,
                "decision": deepcopy(decision),
                "committed_utc": "2026-09-22T00:00:01+00:00",
            },
        },
    }
    job = {
        "job_id": "JOB_RESUME_DECISION",
        "status": "SCIENTIST_REQUESTING",
        "active": True,
        "current_round": 1,
        "request": req,
    }
    calls: list[tuple[int, dict, bool]] = []
    updates: list[dict] = []

    monkeypatch.setattr(worker, "get_job", lambda job_id: deepcopy(job))
    round_lookup = (
        lambda job_id, round_no: deepcopy(source_record) if round_no == 1 else None
    )
    monkeypatch.setattr(worker, "get_round", round_lookup)
    monkeypatch.setattr(transition, "get_round", round_lookup)
    monkeypatch.setattr(
        transition,
        "round_evidence_dir",
        lambda job_id, round_no: tmp_path / f"round_{round_no:02d}",
    )
    (tmp_path / "round_01").mkdir(parents=True)
    monkeypatch.setattr(
        transition,
        "propose_optimizer_ranges",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("committed transition must never call provider again")
        ),
    )

    row1 = no_winner_row(1)
    row2 = no_winner_row(2)

    def fake_execute(request, *, job_id, round_no, search_space, resume):
        calls.append((round_no, deepcopy(search_space), bool(resume)))
        if round_no == 1:
            return [row1], {"eligible_passes": 0}, None
        assert search_space == effective
        return [row2], {"eligible_passes": 0}, None

    monkeypatch.setattr(worker, "execute_round", fake_execute)

    def fake_update(job_id, **kwargs):
        updates.append(dict(kwargs))
        job.update(
            {
                key: value
                for key, value in kwargs.items()
                if key in {
                    "status",
                    "active",
                    "current_round",
                    "message",
                    "first_blocker",
                    "terminal_result",
                }
            }
        )
        return deepcopy(job)

    monkeypatch.setattr(worker, "update_job", fake_update)

    rc = worker.run_job("JOB_RESUME_DECISION", resume=True)

    assert rc == 0
    assert [item[0] for item in calls] == [1, 2]
    assert calls[0][2] is True
    assert calls[1][1] == effective
    assert updates[-1]["status"] == "NO_ELIGIBLE_WINNER_MAX_ROUNDS"
