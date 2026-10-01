from __future__ import annotations

import json
from pathlib import Path

import pytest

import max_backend.optimizer_worker as worker
from max_backend.optimizer_core import OptimizationPass
from max_backend.optimizer_runtime import (
    ReportPending,
    is_new_or_changed,
)
from max_backend.optimizer_core import report_matches_request


def request_payload() -> dict:
    return {
        "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V1",
        "max_rounds": 3,
        "search_space": {},
        "optimize_params": ["InpEntryThreshold"],
        "fixed_param_values": {},
        "ea": {"sha256": "sha"},
        "kpi": {
            "min_profit_factor": 1.0,
            "min_recovery_factor": 0.0,
            "min_expectancy_r": 0.0,
            "min_weighted_r": 0.0,
        },
        "trade_sample": {"minimum_trades": 20},
    }


def good_pass() -> OptimizationPass:
    return OptimizationPass(
        round_no=1,
        pass_no=1,
        profit_factor=1.2,
        recovery_factor=0.5,
        expectancy_r=0.2,
        weighted_r=0.1,
        profit=10.0,
        trades=25,
        params={"InpEntryThreshold": 0.2},
        raw={},
    )


class Harness:
    def __init__(self, tmp_path: Path, state: dict):
        self.tmp_path = tmp_path
        self.state = dict(state)
        self.launch_count = 0
        self.wait_count = 0
        self.parse_count = 0
        self.metrics = tmp_path / "Max_MTF_metrics.csv"
        self.metrics.write_text("fixture", encoding="utf-8")
        self.report = tmp_path / "Max_MTF.xml"
        self.report.write_text("fixture", encoding="utf-8")

    def get_round(self, job_id: str, round_no: int):
        return {
            "job_id": job_id,
            "round_no": round_no,
            "phase": self.state["phase"],
            "state": dict(self.state),
            "report_sha256": None,
            "sidecar_sha256": None,
        }

    def upsert_round(self, job_id, round_no, *, phase, state, **kwargs):
        self.state = {**state, "phase": phase}
        return {
            "job_id": job_id,
            "round_no": round_no,
            "phase": phase,
            "state": dict(self.state),
            **kwargs,
        }

    def update_job(self, *args, **kwargs):
        return {}

    def launch_mt5(self, *args, **kwargs):
        self.launch_count += 1
        return 0

    def wait_for_fresh_report(self, *args, **kwargs):
        self.wait_count += 1
        return self.report, "FRESH_FINGERPRINT"

    def parse(self, *args, **kwargs):
        self.parse_count += 1
        return [good_pass()]

    def commit(self, **kwargs):
        return {
            "report_path": str(self.report),
            "report_sha256": "reportsha",
            "sidecar_path": str(self.metrics),
            "sidecar_sha256": "metricsha",
            "report_identity": {"title": "fixture"},
            "report_selection_mode": kwargs["report_selection_mode"],
            "bundle_path": str(self.tmp_path / "committed"),
            "manifest_sha256": "manifestsha",
        }


def patch_harness(monkeypatch: pytest.MonkeyPatch, harness: Harness) -> None:
    monkeypatch.setattr(worker, "get_round", harness.get_round)
    monkeypatch.setattr(worker, "upsert_round", harness.upsert_round)
    monkeypatch.setattr(worker, "update_job", harness.update_job)
    monkeypatch.setattr(worker, "write_state_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "sha256_file", lambda *args, **kwargs: "sha")
    monkeypatch.setattr(
        worker,
        "optimization_report_identity",
        lambda path: {"path": str(path), "fixture": True},
    )
    monkeypatch.setattr(worker, "report_matches_request", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(worker, "snapshot_compatible_reports", lambda *args, **kwargs: [])
    monkeypatch.setattr(worker, "clear_stale_sidecar", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "launch_mt5", harness.launch_mt5)
    monkeypatch.setattr(worker, "wait_for_fresh_report", harness.wait_for_fresh_report)
    monkeypatch.setattr(worker, "parse_optimization_xml", harness.parse)
    monkeypatch.setattr(
        worker,
        "stage_raw_round_evidence",
        lambda **kwargs: {
            "raw_report_path": str(harness.report),
            "raw_report_sha256": "sha",
            "raw_sidecar_path": str(harness.metrics),
            "raw_sidecar_sha256": "sha",
        },
    )
    monkeypatch.setattr(worker, "commit_round_evidence", harness.commit)
    monkeypatch.setattr(
        worker,
        "candidate_projection_payload",
        lambda **_kwargs: {"projection_sha256": "projectionsha", "items": []},
    )
    monkeypatch.setattr(worker, "job_evidence_dir", lambda *args, **kwargs: harness.tmp_path)
    monkeypatch.setattr(worker, "round_evidence_dir", lambda *args, **kwargs: harness.tmp_path)


def state_for(tmp_path: Path, phase: str) -> dict:
    return {
        "phase": phase,
        "search_space": {},
        "ini_path": str(tmp_path / "round.ini"),
        "optimizer_metrics_path": str(tmp_path / "Max_MTF_metrics.csv"),
        "optimizer_run_nonce": 123,
        "prelaunch_report_snapshot": [],
    }


def test_prepared_restart_may_launch_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(tmp_path, state_for(tmp_path, "PREPARED"))
    patch_harness(monkeypatch, harness)

    rows, audit, winner = worker.execute_round(
        request_payload(),
        job_id="J1",
        round_no=1,
        search_space={},
        resume=True,
    )

    assert harness.launch_count == 1
    assert harness.wait_count == 1
    assert harness.parse_count == 1
    assert audit["eligible_passes"] == 1
    assert winner is not None


def test_mt5_running_restart_never_relaunches_same_round(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(tmp_path, state_for(tmp_path, "MT5_RUNNING"))
    patch_harness(monkeypatch, harness)

    worker.execute_round(
        request_payload(),
        job_id="J2",
        round_no=1,
        search_space={},
        resume=True,
    )

    assert harness.launch_count == 0
    assert harness.wait_count == 1
    assert any(
        token in str(harness.state)
        for token in ("PARSED", "WORKER_RESTART_DURING_MT5_RUNNING_NO_RELAUNCH")
    )


def test_mt5_complete_with_report_does_not_relaunch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = state_for(tmp_path, "MT5_COMPLETE")
    state["report_path"] = str(tmp_path / "Max_MTF.xml")
    harness = Harness(tmp_path, state)
    patch_harness(monkeypatch, harness)

    worker.execute_round(
        request_payload(),
        job_id="J3",
        round_no=1,
        search_space={},
        resume=True,
    )

    assert harness.launch_count == 0
    assert harness.parse_count == 1


def test_mt5_complete_without_report_enters_waiting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(tmp_path, state_for(tmp_path, "MT5_COMPLETE"))
    patch_harness(monkeypatch, harness)
    monkeypatch.setattr(
        worker,
        "wait_for_fresh_report",
        lambda *args, **kwargs: (None, ""),
    )

    with pytest.raises(ReportPending):
        worker.execute_round(
            request_payload(),
            job_id="J4",
            round_no=1,
            search_space={},
            resume=True,
        )

    assert harness.launch_count == 0
    assert harness.state["phase"] == "WAITING_FOR_REPORT"


def test_waiting_for_report_resume_never_relaunches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = Harness(tmp_path, state_for(tmp_path, "WAITING_FOR_REPORT"))
    patch_harness(monkeypatch, harness)

    worker.execute_round(
        request_payload(),
        job_id="J5",
        round_no=1,
        search_space={},
        resume=True,
    )

    assert harness.launch_count == 0
    assert harness.wait_count == 1


def test_report_ready_resume_reuses_frozen_raw_evidence_after_source_disappears(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = state_for(tmp_path, "REPORT_READY")
    state["report_path"] = str(tmp_path / "removed-original.xml")
    state["report_fingerprint"] = {"size": 99, "mtime_ns": 1}
    freeze_manifest = tmp_path / ".staging" / "raw-freeze" / "raw-manifest.json"
    freeze_manifest.parent.mkdir(parents=True)
    freeze_manifest.write_text("{}", encoding="utf-8")
    harness = Harness(tmp_path, state)
    patch_harness(monkeypatch, harness)
    staged: list[dict] = []

    def reuse_frozen_raw(**kwargs):
        staged.append(kwargs)
        return {
            "raw_report_path": str(harness.report),
            "raw_report_sha256": "sha",
            "raw_sidecar_path": str(harness.metrics),
            "raw_sidecar_sha256": "sha",
            "raw_manifest_path": str(freeze_manifest),
        }

    monkeypatch.setattr(worker, "stage_raw_round_evidence", reuse_frozen_raw)
    monkeypatch.setattr(
        worker,
        "wait_for_fresh_report",
        lambda *_args, **_kwargs: pytest.fail("frozen evidence must bypass report discovery"),
    )
    monkeypatch.setattr(
        worker,
        "report_matches_request",
        lambda *_args, **_kwargs: pytest.fail("frozen evidence must bypass source revalidation"),
    )

    rows, audit, winner = worker.execute_round(
        request_payload(),
        job_id="J7",
        round_no=1,
        search_space={},
        resume=True,
    )

    assert harness.launch_count == 0
    assert harness.parse_count == 1
    assert len(staged) == 1
    assert audit["eligible_passes"] == 1
    assert winner is not None


def test_stale_report_fingerprint_rejected(tmp_path: Path) -> None:
    report = tmp_path / "Max_MTF.xml"
    report.write_text("same", encoding="utf-8")
    stat = report.stat()
    snapshot = [
        {
            "path": str(report.resolve()),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    ]
    assert is_new_or_changed(report, snapshot) is False


def test_wrong_report_identity_rejected(tmp_path: Path) -> None:
    report = tmp_path / "Max_MTF.xml"
    report.write_text(
        '<?xml version="1.0"?><Workbook><Title>Max_MTF WRONG,H4 2026.01.01-2026.02.01</Title></Workbook>',
        encoding="utf-8",
    )
    request = {
        "symbol": "XAUUSD.m",
        "period": "H4",
        "from_date": "2026.01.01",
        "to_date": "2026.02.01",
    }
    assert report_matches_request(report, request) is False


def test_checkpointed_parsed_round_not_relaunched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = state_for(tmp_path, "PARSED")
    harness = Harness(tmp_path, state)
    patch_harness(monkeypatch, harness)

    payload = good_pass().payload()
    (tmp_path / "passes.json").write_text(
        json.dumps({"passes": [payload]}),
        encoding="utf-8",
    )
    (tmp_path / "eligibility_audit.json").write_text(
        json.dumps(
            {
                "parsed_passes": 1,
                "eligible_passes": 1,
                "weighted_evidence_complete_passes": 1,
                "weighted_evidence_incomplete_passes": 0,
                "unresolved_nonweighted_contenders": 0,
                "thresholds": {},
                "gate_counts": {},
                "winner_pass": 1,
            }
        ),
        encoding="utf-8",
    )

    rows, audit, winner = worker.execute_round(
        request_payload(),
        job_id="J6",
        round_no=1,
        search_space={},
        resume=True,
    )

    assert harness.launch_count == 0
    assert harness.parse_count == 0
    assert audit["winner_pass"] == 1
    assert winner is not None
