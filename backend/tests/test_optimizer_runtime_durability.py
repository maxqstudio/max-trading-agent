from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import max_backend.optimizer_runtime as runtime


def _sources(root: Path) -> tuple[Path, Path]:
    report = root / "report.xml"
    metrics = root / "metrics.csv"
    report.write_text(
        "<Report><Title>MAX_MTF XAUUSD.m,H1 2026.01.01-2026.02.01</Title>"
        "<Passes>synthetic evidence only</Passes></Report>",
        encoding="utf-8",
    )
    metrics.write_text("synthetic,sidecar\n1,only\n", encoding="utf-8")
    return report, metrics


def _commit(report: Path, metrics: Path) -> dict:
    return runtime.commit_round_evidence(
        job_id="synthetic-job",
        round_no=1,
        report=report,
        metrics_path=metrics,
        audit={"synthetic": True},
        passes_payload=[],
        report_selection_mode="SYNTHETIC_TEST",
    )


def test_committed_round_replay_returns_identical_verified_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "OPTIMIZER_EVIDENCE_ROOT", tmp_path / "optimizer")
    report, metrics = _sources(tmp_path)

    first = _commit(report, metrics)
    replay = _commit(report, metrics)

    assert replay == first
    assert first["report_identity"]["path"] == str(
        (Path(first["bundle_path"]) / runtime.OPTIMIZER_REPORT_XML).resolve()
    )


def test_concurrent_identical_round_publication_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "OPTIMIZER_EVIDENCE_ROOT", tmp_path / "optimizer")
    report, metrics = _sources(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: _commit(report, metrics), range(2)))

    assert results[0] == results[1]
    assert runtime.verify_committed_round_bundle(
        Path(results[0]["bundle_path"]),
        job_id="synthetic-job",
        round_no=1,
    )["files"][runtime.OPTIMIZER_REPORT_XML] == results[0]["report_sha256"]


def test_committed_round_replay_rejects_changed_source_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "OPTIMIZER_EVIDENCE_ROOT", tmp_path / "optimizer")
    report, metrics = _sources(tmp_path)
    _commit(report, metrics)
    report.write_text(
        "<Report><Title>MAX_MTF XAUUSD.m,H1 2026.01.01-2026.02.01</Title>"
        "<Passes>different synthetic bytes</Passes></Report>",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="COMMITTED_ROUND_BUNDLE_MISMATCH"):
        _commit(report, metrics)
