from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

import max_backend.challenger_bundle as bundle
import max_backend.challenger_registry as registry
import max_backend.challenger_store as store
from max_backend.config import EA_BASELINE
from max_backend.db import ensure_baseline_registered, initialize_database
from max_backend.optimizer_core import (
    ABSOLUTE_BOUNDS,
    LEGACY_ABSOLUTE_BOUNDS,
    read_ea_optimizer_defaults,
    sha256_file,
)
from max_backend.optimizer_store import create_job
from max_backend.mtf_geometry import STRATEGY_CONTRACT, resolve_strategy_geometry


EA_SHA = "10fadcd986a93cc075e13a6a383f1b00ee5c097c2a70d6edee315668111d5e20"


def _request() -> dict:
    return {
        "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V3",
        "strategy_contract": STRATEGY_CONTRACT,
        "strategy_geometry": resolve_strategy_geometry("H4"),
        "max_rounds": 1,
        "symbol": "XAUUSD.m",
        "relative_symbol": "EURUSD.m",
        "period": "H4",
        "from_date": "2026.08.01",
        "to_date": "2026.09.15",
        "model": 1,
        "optimization": 1,
        "ea": {"sha256": EA_SHA, "version": "2.11"},
        "scientist_assist": False,
    }


def _patch_store(monkeypatch: pytest.MonkeyPatch, db: Path) -> None:
    monkeypatch.setattr(
        registry,
        "get_challenger",
        lambda challenger_id, **_kwargs: store.get_challenger(challenger_id, path=db),
    )
    monkeypatch.setattr(
        bundle,
        "get_challenger",
        lambda challenger_id, **_kwargs: store.get_challenger(challenger_id, path=db),
    )
    monkeypatch.setattr(
        registry,
        "get_challenger_by_source",
        lambda job_id, round_no, pass_no: store.get_challenger_by_source(
            job_id, round_no, pass_no, path=db
        ),
    )
    monkeypatch.setattr(
        registry,
        "list_registry_rows",
        lambda: store.list_challengers(path=db),
    )
    monkeypatch.setattr(
        registry,
        "reserve_challenger",
        lambda payload: store.reserve_challenger(payload, path=db),
    )
    monkeypatch.setattr(
        registry,
        "finalize_challenger",
        lambda challenger_id, **kwargs: store.finalize_challenger(
            challenger_id, path=db, **kwargs
        ),
    )
    monkeypatch.setattr(
        registry,
        "mark_registration_error",
        lambda challenger_id, error: store.mark_registration_error(
            challenger_id, error, path=db
        ),
    )
    monkeypatch.setattr(
        registry,
        "challenger_id_exists",
        lambda challenger_id: store.challenger_id_exists(challenger_id, path=db),
    )


def _source_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    entry_threshold: float = 0.20,
) -> tuple[Path, str, dict]:
    root = tmp_path / "project"
    baseline = root / "ea" / "baseline" / "Max_MTF.mq5"
    baseline.parent.mkdir(parents=True)
    shutil.copy2(EA_BASELINE, baseline)

    db = root / "state" / "max.db"
    initialize_database(db)
    # Baseline registration reads the canonical project manifest, not the temp EA.
    ensure_baseline_registered(db)
    store.migrate_m03(db)
    job = create_job(_request(), evidence_root=root / "evidence" / "optimizer", path=db)

    _patch_store(monkeypatch, db)
    monkeypatch.setattr(registry, "ROOT", root)
    monkeypatch.setattr(registry, "EA_BASELINE", baseline)
    monkeypatch.setattr(bundle, "ROOT", root)
    monkeypatch.setattr(bundle, "EA_BASELINE", baseline)
    monkeypatch.setattr(
        bundle,
        "CHALLENGER_ARTIFACT_ROOT",
        root / "artifacts" / "strategy_challengers",
    )
    monkeypatch.setattr(
        registry,
        "CHALLENGER_ARTIFACT_ROOT",
        root / "artifacts" / "strategy_challengers",
    )

    params = read_ea_optimizer_defaults(baseline, bounds=LEGACY_ABSOLUTE_BOUNDS)
    params["InpEntryThreshold"] = entry_threshold

    source_dir = root / "source-input"
    source_dir.mkdir(parents=True)
    request_path = source_dir / "request.json"
    winner_path = source_dir / "eligible_winner.json"
    passes_path = source_dir / "passes.json"
    audit_path = source_dir / "eligibility_audit.json"
    provenance_path = source_dir / "report_provenance.json"
    xml_path = source_dir / "Max_MTF.xml"
    sidecar_path = source_dir / "Max_MTF_metrics.csv"

    request = {
        **_request(),
        "fixed_param_values": read_ea_optimizer_defaults(baseline, bounds=LEGACY_ABSOLUTE_BOUNDS),
        "optimize_params": ["InpEntryThreshold"],
    }
    winner = {
        "schema": "MAX_REBUILD_ELIGIBLE_WINNER_V1",
        "job_id": job["job_id"],
        "round": 1,
        "mt5_pass": 7,
        "ea_sha256": EA_SHA,
        "strategy_contract": request["strategy_contract"],
        "strategy_geometry": request["strategy_geometry"],
        "params": params,
        "profit_factor": 1.2,
        "recovery_factor": 0.4,
        "mean_r": 0.2,
        "weighted_r": 0.1,
        "trades": 25,
        "minimum_required_trades": 20,
        "frozen_gates": {
            "min_profit_factor": 1.0,
            "min_recovery_factor": 0.0,
            "min_expectancy_r": 0.0,
            "min_weighted_r": 0.0,
        },
        "ranking_authority": registry.WINNER_RANKING,
    }
    request_path.write_text(json.dumps(request), encoding="utf-8")
    winner_path.write_text(json.dumps(winner), encoding="utf-8")
    passes_path.write_text(json.dumps({"passes": []}), encoding="utf-8")
    audit_path.write_text(json.dumps({"eligible_passes": 1, "winner_pass": 7}), encoding="utf-8")
    provenance_path.write_text(json.dumps({"selection_mode": "TEST"}), encoding="utf-8")
    xml_path.write_bytes(b"<xml-real-authority-placeholder-for-bundle-copy/>")
    sidecar_path.write_text("metrics-authority\n", encoding="utf-8")

    source = {
        "job": job,
        "job_id": job["job_id"],
        "round": 1,
        "pass": 7,
        "request": request,
        "winner": winner,
        "params": params,
        "kpi": {
            "profit_factor": 1.2,
            "recovery_factor": 0.4,
            "mean_r": 0.2,
            "weighted_r": 0.1,
            "trades": 25,
            "required_trades": 20,
        },
        "hard_gates": {
            "minimum_trades": 20,
            "min_profit_factor": 1.0,
            "min_recovery_factor": 0.0,
            "min_expectancy_r": 0.0,
            "min_weighted_r": 0.0,
        },
        "xml_path": xml_path,
        "xml_sha256": sha256_file(xml_path),
        "sidecar_path": sidecar_path,
        "sidecar_sha256": sha256_file(sidecar_path),
        "passes_path": passes_path,
        "audit_path": audit_path,
        "report_provenance_path": provenance_path,
        "winner_path": winner_path,
        "request_path": request_path,
        "run_nonce": 123456,
        "round_lineage": [
            {
                "round": 1,
                "report_sha256": sha256_file(xml_path),
                "sidecar_sha256": sha256_file(sidecar_path),
                "parsed_passes": 1,
                "eligible_passes": 1,
                "winner_pass": 7,
                "decision": "ELIGIBLE_WINNER",
            }
        ],
    }
    monkeypatch.setattr(
        registry,
        "verify_optimizer_winner",
        lambda job_id, expected_round=None, expected_pass=None: deepcopy(source),
    )
    return db, job["job_id"], source


def test_challenger_id_uses_source_job_timestamp() -> None:
    assert (
        registry.challenger_id_for_source("20260922_101450_dc549869", 2, 17)
        == "STRAT-20260922-101450-R02-P17"
    )


def test_bundle_creation_is_idempotent_and_preserves_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, job_id, source = _source_fixture(tmp_path, monkeypatch)
    before = sha256_file(registry.EA_BASELINE)

    first = registry.ensure_challenger_for_winner(
        job_id, expected_round=1, expected_pass=7
    )
    second = registry.ensure_challenger_for_winner(
        job_id, expected_round=1, expected_pass=7
    )

    assert first["status"] == "CHALLENGER"
    assert first["role_origin"] == "OPTIMIZER_WINNER"
    assert second["challenger_id"] == first["challenger_id"]
    assert second["manifest_sha256"] == first["manifest_sha256"]
    assert len(store.list_challengers(path=db)) == 1
    assert sha256_file(registry.EA_BASELINE) == before == EA_SHA

    integrity = registry.verify_challenger_bundle(first["challenger_id"])
    assert integrity["status"] == "VERIFIED"
    assert integrity["winner_evidence"] == "VERIFIED"
    assert integrity["mt5_xml"] == "VERIFIED"
    assert integrity["weighted_r_sidecar"] == "VERIFIED"
    assert integrity["ea_set_parity"] == "VERIFIED"
    assert integrity["parameter_parity"]["parameter_count"] == 16
    assert integrity["parameter_parity"]["winner_to_ea"] is True
    assert integrity["parameter_parity"]["winner_to_set"] is True
    assert integrity["parameter_parity"]["ea_to_set"] is True
    assert integrity["champion_mutation"] == "NONE"

    row = store.get_challenger(first["challenger_id"], path=db)
    assert row is not None
    assert set(row["params"]) == set(LEGACY_ABSOLUTE_BOUNDS)
    assert row["bundle_path"].startswith("artifacts/strategy_challengers/")
    assert ":" not in row["bundle_path"]

    metadata = json.loads(
        (
            registry.ROOT
            / row["bundle_path"]
            / "challenger.json"
        ).read_text(encoding="utf-8")
    )
    assert metadata["champion"]["mutation"] == "NONE"
    assert metadata["params"] == source["params"]
    assert metadata["source"]["optimizer_job"] == job_id


def test_ea_mutation_is_whitelisted_and_set_is_fixed(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline.mq5"
    shutil.copy2(EA_BASELINE, baseline)
    params = read_ea_optimizer_defaults(baseline, bounds=LEGACY_ABSOLUTE_BOUNDS)
    params["InpEntryThreshold"] = 0.20
    params["InpMaxHoldBars"] = 60

    challenger = tmp_path / "Max_Challenger_TEST.mq5"
    set_path = tmp_path / "Max_Challenger_TEST.set"
    ea_audit = bundle.apply_params_to_challenger_ea(
        baseline, challenger, params
    )
    set_audit = bundle.write_challenger_set(set_path, params, _request())
    parity = bundle.verify_parameter_parity(challenger, set_path, params, _request())

    assert ea_audit["non_whitelisted_changes"] == 0
    assert "InpEntryThreshold" in ea_audit["changed_parameter_defaults"]
    assert "InpMaxHoldBars" in ea_audit["changed_parameter_defaults"]
    assert set_audit["parameter_count"] == 16
    assert set_audit["all_optimizer_flags"] == "N"
    assert parity["winner_to_ea"] is True
    assert parity["winner_to_set"] is True
    assert parity["ea_to_set"] is True
    assert parity["parameter_count"] == 16
    assert parity["all_optimizer_flags"] == "N"
    assert parity["strategy_geometry"]["strategy_contract"] == "MAX_TRUE_MTF_DYNAMIC_V1"
    assert parity["strategy_geometry"]["strategy_geometry"] == _request()["strategy_geometry"]
    assert sha256_file(baseline) == EA_SHA


@pytest.mark.parametrize(
    "target",
    ["ea", "set", "metadata", "manifest"],
)
def test_committed_bundle_tamper_fails_closed_and_is_not_regenerated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    _db, job_id, _source = _source_fixture(tmp_path, monkeypatch)
    row = registry.ensure_challenger_for_winner(job_id)
    bundle = registry.ROOT / row["bundle_path"]
    mapping = {
        "ea": bundle / f"Max_Challenger_{row['challenger_id']}.mq5",
        "set": bundle / f"Max_Challenger_{row['challenger_id']}.set",
        "metadata": bundle / "challenger.json",
        "manifest": bundle / "manifest.json",
    }
    path = mapping[target]
    path.write_bytes(path.read_bytes() + b"\nTAMPER")
    tampered_sha = sha256_file(path)

    with pytest.raises(RuntimeError):
        registry.verify_challenger_bundle(row["challenger_id"])
    with pytest.raises(RuntimeError):
        registry.ensure_challenger_for_winner(job_id)

    assert sha256_file(path) == tampered_sha


def test_registering_row_without_bundle_recovers_to_one_challenger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, job_id, source = _source_fixture(tmp_path, monkeypatch)
    original_build = registry._build_bundle

    def fail_before_bundle(*args, **kwargs):
        raise RuntimeError("CRASH_AFTER_REGISTERING_ROW")

    monkeypatch.setattr(registry, "_build_bundle", fail_before_bundle)
    with pytest.raises(RuntimeError, match="CRASH_AFTER_REGISTERING_ROW"):
        registry.ensure_challenger_for_winner(job_id)

    rows = store.list_challengers(include_registering=True, path=db)
    assert len(rows) == 1
    assert rows[0]["status"] == "REGISTERING"

    # Simulate an incomplete staging directory left by a process crash.
    staging = registry.CHALLENGER_ARTIFACT_ROOT / (
        ".staging-" + rows[0]["challenger_id"]
    )
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "partial.txt").write_text("partial", encoding="utf-8")

    monkeypatch.setattr(registry, "_build_bundle", original_build)
    recovered = registry.ensure_challenger_for_winner(job_id)

    assert recovered["status"] == "CHALLENGER"
    assert recovered["params"] == source["params"]
    assert len(store.list_challengers(path=db)) == 1
    assert not staging.exists()


def test_final_bundle_before_db_activation_recovers_without_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, job_id, _source = _source_fixture(tmp_path, monkeypatch)
    original_finalize = registry.finalize_challenger
    calls = {"finalize": 0, "build": 0}
    original_build = registry._build_bundle

    def counting_build(*args, **kwargs):
        calls["build"] += 1
        return original_build(*args, **kwargs)

    def crash_finalize(*args, **kwargs):
        calls["finalize"] += 1
        raise RuntimeError("CRASH_BEFORE_DB_ACTIVATION")

    monkeypatch.setattr(registry, "_build_bundle", counting_build)
    monkeypatch.setattr(registry, "finalize_challenger", crash_finalize)
    with pytest.raises(RuntimeError, match="CRASH_BEFORE_DB_ACTIVATION"):
        registry.ensure_challenger_for_winner(job_id)

    rows = store.list_challengers(include_registering=True, path=db)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "REGISTERING"
    assert (registry.ROOT / row["bundle_path"]).is_dir()
    assert calls["build"] == 1

    monkeypatch.setattr(registry, "finalize_challenger", original_finalize)
    recovered = registry.ensure_challenger_for_winner(job_id)

    assert recovered["status"] == "CHALLENGER"
    assert calls["build"] == 1
    assert len(store.list_challengers(path=db)) == 1


@pytest.mark.parametrize(
    "message",
    [
        "CHALLENGER_SOURCE_JOB_UNKNOWN",
        "CHALLENGER_SOURCE_NOT_ELIGIBLE_WINNER",
        "ELIGIBLE_WINNER_EVIDENCE_MISSING",
        "WINNER_NOT_DETERMINISTICALLY_ELIGIBLE",
        "WINNER_EA_SHA_MISMATCH",
        "WINNER_XML_SHA_MISMATCH",
        "WINNER_SIDECAR_SHA_MISMATCH",
        "CHALLENGER_PARAMS_INCOMPLETE",
    ],
)
def test_invalid_source_never_creates_registry_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    db, job_id, _source = _source_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        registry,
        "verify_optimizer_winner",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(message)),
    )

    with pytest.raises(RuntimeError, match=message):
        registry.ensure_challenger_for_winner(job_id)

    assert store.list_challengers(include_registering=True, path=db) == []


@pytest.mark.parametrize(
    "status,terminal",
    [
        ("FAILED", "ELIGIBLE_WINNER_FOUND"),
        ("STOPPED", "ELIGIBLE_WINNER_FOUND"),
        ("NO_ELIGIBLE_WINNER_MAX_ROUNDS", "ELIGIBLE_WINNER_FOUND"),
        ("ELIGIBLE_WINNER_FOUND", "FAIL"),
    ],
)
def test_winner_source_state_guard_rejects_inconsistent_terminal_state(
    status: str,
    terminal: str,
) -> None:
    assert registry._winner_job_allowed(
        {"status": status, "terminal_result": terminal}
    ) is False


@pytest.mark.parametrize(
    "mutator",
    [
        lambda p: p.pop("InpEntryThreshold"),
        lambda p: p.__setitem__("EXTRA", 1),
        lambda p: p.__setitem__("InpEntryThreshold", float("nan")),
        lambda p: p.__setitem__("InpEntryThreshold", 99.0),
        lambda p: p.__setitem__("InpMaxHoldBars", 13.5),
        lambda p: p.__setitem__("InpWeightTrend", 0.0),
    ],
)
def test_full_params_fail_closed(mutator) -> None:
    params = read_ea_optimizer_defaults(EA_BASELINE)
    mutator(params)
    with pytest.raises(RuntimeError):
        registry.validate_full_params(params)
