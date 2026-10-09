from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

import max_backend.challenger_bundle as challenger_bundle
import max_backend.challenger_deployment as challenger_deployment
import max_backend.champion_bundle as champion_bundle
import max_backend.challenger_operations as challenger_operations
import max_backend.promotion_service as service
from max_backend.challenger_store import (
    finalize_challenger,
    get_challenger,
    list_challengers,
    migrate_m03,
    reserve_challenger,
)
from max_backend.challenger_operations_store import (
    list_registry_page,
    retire_registry_row,
)
from max_backend.champion_store import (
    create_prepared_promotion,
    current_champion,
    get_promotion,
    list_champions,
    migrate_m04,
    update_promotion,
)
from max_backend.db import ensure_baseline_registered, initialize_database
from max_backend.optimizer_core import sha256_file
from max_backend.optimizer_store import create_job, update_job
from max_backend.mtf_geometry import STRATEGY_CONTRACT, resolve_strategy_geometry
from types import SimpleNamespace


REAL_ROOT = Path(__file__).resolve().parents[2]
BASELINE = REAL_ROOT / "ea" / "baseline" / "Max_MTF.mq5"
M03_ID = "STRAT-20260922-120735-R01-P11"
M03_BUNDLE = REAL_ROOT / "backend" / "tests" / "fixtures" / "m03_challenger"
M03_REQUEST = M03_BUNDLE / "request.json"
M03_META = M03_BUNDLE / "challenger.json"
BASELINE_SHA = "10fadcd986a93cc075e13a6a383f1b00ee5c097c2a70d6edee315668111d5e20"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    challenger_id: str = M03_ID,
) -> dict:
    root = tmp_path
    baseline = root / "ea" / "baseline" / "Max_MTF.mq5"
    baseline.parent.mkdir(parents=True)
    baseline.write_bytes(BASELINE.read_bytes())
    assert sha(baseline) == BASELINE_SHA

    request = json.loads(M03_REQUEST.read_text(encoding="utf-8-sig"))
    meta = json.loads(M03_META.read_text(encoding="utf-8-sig"))
    params = dict(meta["params"])
    kpi = dict(meta["kpi"])
    hard_gates = dict(meta["hard_gates"])

    data_root = root / "mt5"
    metaeditor = root / "metaeditor64.exe"
    terminal = root / "terminal64.exe"
    metaeditor.write_bytes(b"fake")
    terminal.write_bytes(b"fake")
    request = copy.deepcopy(request)
    request["schema"] = "MAX_REBUILD_OPTIMIZER_REQUEST_V3"
    request["strategy_contract"] = STRATEGY_CONTRACT
    request["strategy_geometry"] = resolve_strategy_geometry(request["period"])
    request["mt5"] = {
        "data_root": str(data_root),
        "metaeditor": str(metaeditor),
        "terminal": str(terminal),
    }
    request["ea"]["version"] = "2.11"
    request["ea"]["sha256"] = BASELINE_SHA
    request["ea"]["path"] = str(baseline)

    db = root / "state" / "max.db"
    initialize_database(db)
    ensure_baseline_registered(db)
    job = create_job(
        request,
        evidence_root=root / "evidence" / "optimizer",
        path=db,
    )
    update_job(
        job["job_id"],
        status="STRATEGY_CHALLENGER_FOUND",
        terminal_result="STRATEGY_CHALLENGER_FOUND",
        active=False,
        path=db,
    )
    migrate_m03(db)

    bundle_rel = Path("artifacts") / "strategy_challengers" / challenger_id
    bundle = root / bundle_rel
    bundle.mkdir(parents=True)
    ea = bundle / f"Max_Challenger_{challenger_id}.mq5"
    challenger_bundle.apply_params_to_challenger_ea(baseline, ea, params)
    set_path = bundle / f"Max_Challenger_{challenger_id}.set"
    challenger_bundle.write_challenger_set(set_path, params, request)
    manifest_sha = "manifest-fixture"

    reserve_challenger(
        {
            "challenger_id": challenger_id,
            "source_job_id": job["job_id"],
            "source_round": 1,
            "source_pass": 11,
            "created_utc": "2026-09-22T12:07:35+00:00",
            "ea_version": "2.11",
            "baseline_ea_sha256": BASELINE_SHA,
            "bundle_path": bundle_rel.as_posix(),
            "params": params,
            "kpi": kpi,
            "hard_gates": hard_gates,
            "source_request": request,
            "winner": {
                "job_id": job["job_id"],
                "round": 1,
                "mt5_pass": 11,
            },
            "provenance": {"run_nonce": 123},
            "winning_xml_sha256": "xml",
            "winning_sidecar_sha256": "sidecar",
        },
        path=db,
    )
    finalize_challenger(
        challenger_id,
        challenger_ea_sha256=sha(ea),
        set_sha256=sha(set_path),
        metadata_sha256="meta",
        manifest_sha256=manifest_sha,
        path=db,
    )
    migrate_m04(db)

    expert = data_root / "MQL5" / "Experts" / "MaxMTF"
    tester = data_root / "MQL5" / "Profiles" / "Tester"
    expert.mkdir(parents=True, exist_ok=True)
    tester.mkdir(parents=True, exist_ok=True)
    old_source = expert / "Max_MTF.mq5"
    old_ex5 = expert / "Max_MTF.ex5"
    old_set = tester / "Max_MTF.set"
    old_source.write_bytes(baseline.read_bytes())
    old_ex5.write_bytes(b"old-ex5")
    old_set.write_text("old-set\n", encoding="utf-8")

    monkeypatch.setattr(service, "ROOT", root)
    monkeypatch.setattr(service, "EA_BASELINE", baseline)
    monkeypatch.setattr(service, "CHAMPION_CURRENT_ROOT", root / "ea" / "champion" / "current")
    monkeypatch.setattr(service, "STRATEGY_HISTORY_ROOT", root / "artifacts" / "strategy_history")
    monkeypatch.setattr(service, "PROMOTION_RECOVERY_ROOT", root / "state" / "promotion_recovery")
    monkeypatch.setattr(champion_bundle, "EA_BASELINE", baseline)

    def fake_metaeditor_run(command, **_kwargs):
        compile_arg = next(
            item for item in command if str(item).startswith("/compile:")
        )
        compile_source = Path(str(compile_arg).split(":", 1)[1])
        compile_source.with_suffix(".ex5").write_bytes(
            b"fake-ex5:" + compile_source.read_bytes()
        )
        compile_source.with_suffix(".log").write_text(
            "Result: 0 errors, 0 warnings\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        challenger_deployment,
        "detect_mt5",
        lambda: {
            "status": "READY_EXECUTABLE_AND_DATA_ROOT",
            "terminal": str(terminal),
            "metaeditor": str(metaeditor),
            "data_root": str(data_root),
        },
    )
    monkeypatch.setattr(challenger_deployment.subprocess, "run", fake_metaeditor_run)

    source = {
        "job_id": job["job_id"],
        "round": 1,
        "pass": 11,
        "request": request,
        "params": params,
        "kpi": kpi,
        "hard_gates": hard_gates,
    }
    monkeypatch.setattr(
        service,
        "verify_optimizer_winner",
        lambda *_args, **_kwargs: copy.deepcopy(source),
    )
    monkeypatch.setattr(
        service,
        "verify_challenger_bundle",
        lambda *_args, **_kwargs: {
            "status": "VERIFIED",
            "manifest_sha256": manifest_sha,
            "champion_mutation": "NONE",
        },
    )

    def fake_compile(**kwargs):
        evidence = kwargs["evidence_stage"]
        temp = root / "compile-temp" / kwargs["promotion_id"]
        temp.mkdir(parents=True, exist_ok=True)
        ex5 = temp / "Max_MTF.ex5"
        ex5.write_bytes(b"fresh-ex5-" + sha(kwargs["champion_ea"]).encode())
        compile_dir = evidence / "compile"
        compile_dir.mkdir(parents=True, exist_ok=True)
        (compile_dir / "metaeditor_compile.txt").write_text(
            "Result: 0 errors, 0 warnings\n",
            encoding="utf-8",
        )
        (compile_dir / "compiled_ea.ex5").write_bytes(ex5.read_bytes())
        return {
            "status": "PASS",
            "compile_summary": {
                "found": True,
                "errors": 0,
                "warnings": 0,
                "line": "Result: 0 errors, 0 warnings",
            },
            "fresh_ex5": True,
            "compiled_ex5": str(ex5),
            "compile_temp_dir": str(temp),
            "ex5_sha256": sha(ex5),
        }

    monkeypatch.setattr(service, "_compile_champion", fake_compile)
    return {
        "root": root,
        "db": db,
        "job_id": job["job_id"],
        "challenger_id": challenger_id,
        "manifest_sha": manifest_sha,
        "source": source,
        "baseline": baseline,
        "old_source": old_source,
        "old_source_bytes": old_source.read_bytes(),
        "old_ex5": old_ex5,
        "old_ex5_bytes": old_ex5.read_bytes(),
        "old_set": old_set,
        "old_set_bytes": old_set.read_bytes(),
    }


def install_runtime_challenger_ea(env: dict, challenger_id: str) -> Path:
    row = get_challenger(challenger_id, path=env["db"])
    assert row is not None
    integrity = service.verify_challenger_bundle(
        challenger_id,
        allow_promoted=row["status"] == "PROMOTED",
        path=env["db"],
    )
    bundle = env["root"] / row["bundle_path"]
    authority = service.resolve_mt5_authority(row["source_request"])
    staging = challenger_deployment.new_challenger_staging_root(
        expert_root=authority["expert_root"],
        operation_id=f"TEST-{challenger_id}",
    )
    try:
        prepared = challenger_deployment.prepare_challenger_deployment(
            challenger_id=challenger_id,
            source_request=row["source_request"],
            source_identity={
                "job_id": row["source_job_id"],
                "round": row["source_round"],
                "pass": row["source_pass"],
            },
            bundle_manifest_sha256=integrity["manifest_sha256"],
            source_ea=bundle / f"Max_Challenger_{challenger_id}.mq5",
            source_set=bundle / f"Max_Challenger_{challenger_id}.set",
            authority=authority,
            staging_root=staging,
        )
        challenger_deployment.commit_challenger_deployment(prepared)
    finally:
        challenger_deployment.cleanup_challenger_staging_root(staging)
    return (
        authority["expert_root"] / "Challengers" / challenger_id
    ).resolve()


def promote(env: dict) -> dict:
    return service.promote_strategy_challenger(
        env["challenger_id"],
        expected_challenger_manifest_sha256=env["manifest_sha"],
        expected_current_champion_id=None,
        confirmed=True,
        path=env["db"],
    )



def test_owner_selected_promotion_source_uses_qualified_revalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenger = {
        "challenger_id": "STRAT-V5",
        "role_origin": "OWNER_SELECTED_QUALIFIED_CANDIDATE",
        "source_job_id": "JOB-V5",
        "source_round": 2,
        "source_pass": 17,
    }
    request = {
        "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V5",
        "optimizer_result_workflow": "QUALIFIED_POOL_OWNER_SELECTION",
    }
    candidate = {
        "job_id": "JOB-V5",
        "round": 2,
        "pass": 17,
        "request": request,
        "params": {"InpEntryThreshold": 0.2},
        "profit_factor": 1.4,
        "recovery_factor": 3.0,
        "mean_r": 0.2,
        "weighted_r": 0.18,
        "trades": 400,
        "required_trades": 324,
        "hard_gates": {"minimum_trades": 324},
    }
    calls: list[tuple[str, int, int, str, Path]] = []

    monkeypatch.setattr(
        service,
        "verify_optimizer_winner",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("legacy winner verifier must not run for Owner-selected V5")
        ),
    )

    def revalidate(job_id, round_no, pass_no, *, challenger_id, path):
        calls.append((job_id, round_no, pass_no, challenger_id, path))
        return copy.deepcopy(candidate)

    monkeypatch.setattr(service, "revalidate_candidate_for_promotion", revalidate)

    result = service._verify_challenger_optimizer_source(
        challenger,
        path=tmp_path / "state" / "max.db",
    )

    assert calls == [
        ("JOB-V5", 2, 17, "STRAT-V5", tmp_path / "state" / "max.db")
    ]
    assert result["job_id"] == "JOB-V5"
    assert result["round"] == 2
    assert result["pass"] == 17
    assert result["request"] == request
    assert result["params"] == candidate["params"]
    assert result["kpi"] == {
        "profit_factor": 1.4,
        "recovery_factor": 3.0,
        "mean_r": 0.2,
        "weighted_r": 0.18,
        "trades": 400,
        "required_trades": 324,
    }




def test_owner_selected_current_read_uses_retained_lineage_without_deep_reparse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = {
        "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V5",
        "optimizer_result_workflow": "QUALIFIED_POOL_OWNER_SELECTION",
        "strategy_contract": "MAX_TRUE_MTF_DYNAMIC_V1",
        "strategy_geometry": {"contract": "MAX_TRUE_MTF_DYNAMIC_V1"},
    }
    params = dict(
        json.loads(M03_META.read_text(encoding="utf-8-sig"))["params"]
    )
    kpi = {
        "profit_factor": 1.4,
        "recovery_factor": 3.0,
        "mean_r": 0.2,
        "weighted_r": 0.18,
        "trades": 400,
        "required_trades": 324,
    }
    gates = {"minimum_trades": 324}
    report_sha = "a" * 64
    sidecar_sha = "b" * 64
    baseline_sha = "c" * 64
    challenger = {
        "challenger_id": "STRAT-V5",
        "role_origin": "OWNER_SELECTED_QUALIFIED_CANDIDATE",
        "source_job_id": "JOB-V5",
        "source_round": 2,
        "source_pass": 17,
        "baseline_ea_sha256": baseline_sha,
        "winning_xml_sha256": report_sha,
        "winning_sidecar_sha256": sidecar_sha,
        "source_request": request,
        "params": params,
        "kpi": kpi,
        "hard_gates": gates,
        "winner": {
            "schema": "MAX_REBUILD_QUALIFIED_CANDIDATE_V1",
            "selection_authority": "OWNER_EXPLICIT_SELECTION",
            "challenger_id": "STRAT-V5",
            "job_id": "JOB-V5",
            "round": 2,
            "pass": 17,
            "ea_sha256": baseline_sha,
            "report_sha256": report_sha,
            "sidecar_sha256": sidecar_sha,
            "strategy_contract": request["strategy_contract"],
            "strategy_geometry": request["strategy_geometry"],
            "params": params,
            "profit_factor": 1.4,
            "recovery_factor": 3.0,
            "mean_r": 0.2,
            "weighted_r": 0.18,
            "trades": 400,
            "required_trades": 324,
            "hard_gates": gates,
        },
        "provenance": {
            "selection_authority": "OWNER_EXPLICIT_SELECTION",
            "report_sha256": report_sha,
            "sidecar_sha256": sidecar_sha,
        },
    }

    monkeypatch.setattr(
        service,
        "revalidate_candidate_for_promotion",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("current Champion read must not full-reparse optimizer evidence")
        ),
    )

    result = service._verify_challenger_optimizer_source(
        challenger,
        path=tmp_path / "state" / "max.db",
        deep_owner_selection=False,
    )
    assert result["job_id"] == "JOB-V5"
    assert result["params"] == params
    assert result["kpi"] == kpi

    tampered = copy.deepcopy(challenger)
    tampered["winner"]["report_sha256"] = "d" * 64
    with pytest.raises(
        RuntimeError,
        match="PROMOTION_QUALIFIED_CANDIDATE_REPORT_SHA_MISMATCH",
    ):
        service._verify_challenger_optimizer_source(
            tampered,
            path=tmp_path / "state" / "max.db",
            deep_owner_selection=False,
        )


def test_unknown_challenger_role_cannot_enter_promotion_lineage(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        RuntimeError,
        match="PROMOTION_CHALLENGER_ROLE_ORIGIN_UNSUPPORTED",
    ):
        service._verify_challenger_optimizer_source(
            {
                "role_origin": "FUTURE_UNKNOWN_ROLE",
                "source_job_id": "JOB",
                "source_round": 1,
                "source_pass": 1,
            },
            path=tmp_path / "state" / "max.db",
        )


def test_first_promotion_service_commits_archive_and_preserves_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = fixture_env(tmp_path, monkeypatch)
    before = sha(env["baseline"])

    result = promote(env)

    assert result["status"] == "COMMITTED"
    assert result["scientist_calls"] == 0
    assert result["live_authority"] == "NONE"
    assert sha(env["baseline"]) == before == BASELINE_SHA
    champion = current_champion(path=env["db"])
    assert champion is not None
    assert champion["strategy_id"] == env["challenger_id"]
    assert champion["status"] == "CURRENT"
    assert get_challenger(env["challenger_id"], path=env["db"])["status"] == "PROMOTED"
    history = service.STRATEGY_HISTORY_ROOT / result["baseline_archive"]
    assert history.is_dir()
    archive = json.loads((history / "baseline.json").read_text(encoding="utf-8"))
    assert archive["status"] == "ARCHIVED_PRE_FIRST_STRATEGY_CHAMPION"
    assert archive["ea_sha256"] == BASELINE_SHA
    assert (env["root"] / result["evidence_path"] / "manifest.json").is_file()


def test_confirmation_false_and_stale_confirmation_do_not_mutate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = fixture_env(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="EXPLICIT_CONFIRMATION_REQUIRED"):
        service.promote_strategy_challenger(
            env["challenger_id"],
            expected_challenger_manifest_sha256=env["manifest_sha"],
            expected_current_champion_id=None,
            confirmed=False,
            path=env["db"],
        )
    with pytest.raises(RuntimeError, match="PROMOTION_CONFIRMATION_STALE"):
        service.promote_strategy_challenger(
            env["challenger_id"],
            expected_challenger_manifest_sha256="stale",
            expected_current_champion_id=None,
            confirmed=True,
            path=env["db"],
        )
    assert current_champion(path=env["db"]) is None
    assert get_challenger(env["challenger_id"], path=env["db"])["status"] == "CHALLENGER"


def test_active_optimizer_blocks_promotion_without_file_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = fixture_env(tmp_path, monkeypatch)
    update_job(env["job_id"], active=True, path=env["db"])
    with pytest.raises(RuntimeError, match="PROMOTION_BLOCKED_OPTIMIZER_ACTIVE"):
        promote(env)
    assert env["old_source"].read_bytes() == env["old_source_bytes"]
    assert current_champion(path=env["db"]) is None


def test_compile_failure_rolls_back_all_authority_and_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = fixture_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        service,
        "_compile_champion",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("COMPILE_FAIL")),
    )
    with pytest.raises(RuntimeError, match="COMPILE_FAIL"):
        promote(env)

    assert current_champion(path=env["db"]) is None
    assert get_challenger(env["challenger_id"], path=env["db"])["status"] == "CHALLENGER"
    assert env["old_source"].read_bytes() == env["old_source_bytes"]
    assert env["old_ex5"].read_bytes() == env["old_ex5_bytes"]
    assert env["old_set"].read_bytes() == env["old_set_bytes"]
    rows = service.list_promotions(path=env["db"])
    assert rows[-1]["state"] == "ROLLED_BACK"
    assert rows[-1]["rollback_status"] == "RESTORED_PREVIOUS_AUTHORITY"


def test_db_commit_failure_restores_files_and_keeps_challenger_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = fixture_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        service,
        "commit_promotion_authority",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("DB_COMMIT_FAIL")),
    )
    with pytest.raises(RuntimeError, match="DB_COMMIT_FAIL"):
        promote(env)

    assert current_champion(path=env["db"]) is None
    assert get_challenger(env["challenger_id"], path=env["db"])["status"] == "CHALLENGER"
    assert env["old_source"].read_bytes() == env["old_source_bytes"]
    assert env["old_ex5"].read_bytes() == env["old_ex5_bytes"]
    assert env["old_set"].read_bytes() == env["old_set_bytes"]


def test_precommit_crash_recovery_restores_old_file_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = fixture_env(tmp_path, monkeypatch)
    runtime_dir = install_runtime_challenger_ea(env, env["challenger_id"])
    recovery = service.PROMOTION_RECOVERY_ROOT / "PROMOTE-CRASH"
    recovery.mkdir(parents=True)
    authority = service.resolve_mt5_authority(env["source"]["request"])
    runtime_staging = challenger_deployment.new_challenger_staging_root(
        expert_root=authority["expert_root"],
        operation_id="PROMOTE-CRASH",
    )
    challenger = get_challenger(env["challenger_id"], path=env["db"])
    integrity = service.verify_challenger_bundle(
        env["challenger_id"],
        path=env["db"],
    )
    runtime_state = {
        "mt5": {
            key: str(authority[key])
            for key in ("terminal", "metaeditor", "data_root")
        },
        "staging_root": str(runtime_staging),
        "selected": service._promotion_runtime_spec(
            challenger,
            integrity,
            authority=authority,
            quarantine_dir=runtime_staging / "promoted-challenger",
        ),
        "previous": None,
    }
    before = service._capture_before_state(
        promotion_id="PROMOTE-CRASH",
        source_request=env["source"]["request"],
        current=None,
        recovery=recovery,
        baseline_archive_final=None,
        former_archive_final=None,
        challenger_runtime=runtime_state,
    )
    create_prepared_promotion(
        promotion_id="PROMOTE-CRASH",
        challenger_id=env["challenger_id"],
        previous_champion_id=None,
        expected_manifest_sha256=env["manifest_sha"],
        before_state=before,
        recovery_path=str(recovery),
        path=env["db"],
    )
    selected = runtime_state["selected"]
    service.quarantine_challenger_deployment(
        challenger_id=selected["challenger_id"],
        source_identity=selected["source_identity"],
        bundle_manifest_sha256=selected["bundle_manifest_sha256"],
        source_sha256=selected["source_sha256"],
        set_sha256=selected["set_sha256"],
        authority=authority,
        staging_root=runtime_staging,
        quarantine_path=Path(selected["quarantine_dir"]),
    )
    assert not runtime_dir.exists()
    update_promotion("PROMOTE-CRASH", state="FILES_COMMITTED", path=env["db"])
    env["old_source"].write_bytes(b"new-uncommitted")
    env["old_ex5"].write_bytes(b"new-ex5")
    env["old_set"].write_bytes(b"new-set")

    recovered = service.recover_incomplete_promotions(path=env["db"])

    assert recovered[0]["state"] == "ROLLED_BACK"
    assert env["old_source"].read_bytes() == env["old_source_bytes"]
    assert env["old_ex5"].read_bytes() == env["old_ex5_bytes"]
    assert env["old_set"].read_bytes() == env["old_set_bytes"]
    assert current_champion(path=env["db"]) is None
    assert runtime_dir.is_dir()
    assert not runtime_staging.exists()


def test_post_db_commit_crash_recovery_keeps_new_champion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = fixture_env(tmp_path, monkeypatch)
    runtime_dir = install_runtime_challenger_ea(env, env["challenger_id"])
    original_runtime_finalize = service._finalize_promotion_runtime_state
    original_finalize = service._finalize_committed_evidence
    monkeypatch.setattr(
        service,
        "_finalize_promotion_runtime_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("CRASH_DURING_RUNTIME_EA_FINALIZE")
        ),
    )
    with pytest.raises(
        RuntimeError,
        match="PROMOTION_RUNTIME_COMMIT_RECONCILIATION_REQUIRED",
    ):
        promote(env)

    champion = current_champion(path=env["db"])
    assert champion is not None
    assert champion["strategy_id"] == env["challenger_id"]
    assert get_challenger(env["challenger_id"], path=env["db"])["status"] == "PROMOTED"
    assert not runtime_dir.exists()

    monkeypatch.setattr(service, "_finalize_promotion_runtime_state", original_runtime_finalize)
    monkeypatch.setattr(service, "_finalize_committed_evidence", original_finalize)
    recovered = service.recover_incomplete_promotions(path=env["db"])
    assert recovered == []
    assert service.verify_current_strategy_champion(path=env["db"])["status"] == "VERIFIED"
    promotion = get_promotion(champion["promotion_id"], path=env["db"])
    assert promotion["state"] == "COMMITTED"
    assert (env["root"] / promotion["evidence_path"] / "manifest.json").is_file()
    assert not runtime_dir.exists()


def test_tampered_or_nonactive_challenger_is_rejected_before_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = fixture_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        service,
        "verify_challenger_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("CHALLENGER_MANIFEST_HASH_MISMATCH")
        ),
    )
    with pytest.raises(RuntimeError, match="CHALLENGER_MANIFEST_HASH_MISMATCH"):
        promote(env)
    assert current_champion(path=env["db"]) is None
    assert get_challenger(env["challenger_id"], path=env["db"])["status"] == "CHALLENGER"


def test_parity_failure_rolls_back_without_consuming_challenger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = fixture_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        service,
        "verify_champion_parity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("PARITY_FAIL")
        ),
    )
    with pytest.raises(RuntimeError, match="PARITY_FAIL"):
        promote(env)

    assert current_champion(path=env["db"]) is None
    assert get_challenger(env["challenger_id"], path=env["db"])["status"] == "CHALLENGER"
    assert env["old_source"].read_bytes() == env["old_source_bytes"]
    assert env["old_ex5"].read_bytes() == env["old_ex5_bytes"]
    assert env["old_set"].read_bytes() == env["old_set_bytes"]


def test_set_creation_failure_rolls_back_without_authority_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = fixture_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        service,
        "write_champion_set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("SET_FAIL")
        ),
    )
    with pytest.raises(RuntimeError, match="SET_FAIL"):
        promote(env)
    assert current_champion(path=env["db"]) is None
    assert get_challenger(env["challenger_id"], path=env["db"])["status"] == "CHALLENGER"


def test_filesystem_commit_failure_rolls_back_deployed_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = fixture_env(tmp_path, monkeypatch)
    challenger_runtime = install_runtime_challenger_ea(
        env,
        env["challenger_id"],
    )
    original_copy = service._atomic_copy
    failed = {"done": False}

    def fail_once(source: Path, destination: Path) -> None:
        if not failed["done"] and destination.name == "Max_MTF.ex5":
            failed["done"] = True
            raise RuntimeError("FILESYSTEM_COMMIT_FAIL")
        original_copy(source, destination)

    monkeypatch.setattr(service, "_atomic_copy", fail_once)
    with pytest.raises(RuntimeError, match="FILESYSTEM_COMMIT_FAIL"):
        promote(env)

    assert current_champion(path=env["db"]) is None
    assert get_challenger(env["challenger_id"], path=env["db"])["status"] == "CHALLENGER"
    assert env["old_source"].read_bytes() == env["old_source_bytes"]
    assert env["old_ex5"].read_bytes() == env["old_ex5_bytes"]
    assert env["old_set"].read_bytes() == env["old_set_bytes"]
    assert challenger_runtime.is_dir()


def _add_second_service_challenger(
    env: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, str, dict]:
    request = copy.deepcopy(env["source"]["request"])
    job = create_job(
        request,
        evidence_root=env["root"] / "evidence" / "optimizer-b",
        path=env["db"],
    )
    update_job(
        job["job_id"],
        status="STRATEGY_CHALLENGER_FOUND",
        terminal_result="STRATEGY_CHALLENGER_FOUND",
        active=False,
        path=env["db"],
    )
    cid = "STRAT-20260922-131500-R01-P12"
    bundle_rel = Path("artifacts") / "strategy_challengers" / cid
    bundle = env["root"] / bundle_rel
    bundle.mkdir(parents=True)
    ea = bundle / f"Max_Challenger_{cid}.mq5"
    params_b = copy.deepcopy(env["source"]["params"])
    params_b["InpEntryThreshold"] = 0.21
    challenger_bundle.apply_params_to_challenger_ea(
        env["baseline"], ea, params_b, request
    )
    set_path = bundle / f"Max_Challenger_{cid}.set"
    challenger_bundle.write_challenger_set(set_path, params_b, request)
    reserve_challenger(
        {
            "challenger_id": cid,
            "source_job_id": job["job_id"],
            "source_round": 1,
            "source_pass": 12,
            "created_utc": "2026-09-22T13:15:00+00:00",
            "ea_version": "2.00",
            "baseline_ea_sha256": BASELINE_SHA,
            "bundle_path": bundle_rel.as_posix(),
            "params": params_b,
            "kpi": {**env["source"]["kpi"], "profit_factor": 3.2},
            "hard_gates": env["source"]["hard_gates"],
            "source_request": request,
            "winner": {"job_id": job["job_id"], "round": 1, "mt5_pass": 12},
            "provenance": {"run_nonce": 124},
            "winning_xml_sha256": "xml-b",
            "winning_sidecar_sha256": "sidecar-b",
        },
        path=env["db"],
    )
    finalize_challenger(
        cid,
        challenger_ea_sha256=sha(ea),
        set_sha256=sha(set_path),
        metadata_sha256="meta-b",
        manifest_sha256="manifest-b",
        path=env["db"],
    )
    source_b = {
        "job_id": job["job_id"],
        "round": 1,
        "pass": 12,
        "request": request,
        "params": params_b,
        "kpi": {**env["source"]["kpi"], "profit_factor": 3.2},
        "hard_gates": copy.deepcopy(env["source"]["hard_gates"]),
    }
    sources = {
        env["job_id"]: env["source"],
        job["job_id"]: source_b,
    }
    monkeypatch.setattr(
        service,
        "verify_optimizer_winner",
        lambda job_id, **_kwargs: copy.deepcopy(sources[job_id]),
    )
    monkeypatch.setattr(
        service,
        "verify_challenger_bundle",
        lambda challenger_id, **_kwargs: {
            "status": "VERIFIED",
            "manifest_sha256": (
                env["manifest_sha"]
                if challenger_id == env["challenger_id"]
                else "manifest-b"
            ),
            "champion_mutation": "NONE",
        },
    )
    return job["job_id"], cid, source_b


def test_later_promotion_service_preserves_former_champion_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = fixture_env(tmp_path, monkeypatch)
    a_runtime = install_runtime_challenger_ea(env, env["challenger_id"])
    first = promote(env)
    assert first["status"] == "COMMITTED"
    assert not a_runtime.exists()
    a = current_champion(path=env["db"])
    assert a is not None

    _job_b, b, _source_b = _add_second_service_challenger(env, monkeypatch)
    b_runtime = install_runtime_challenger_ea(env, b)
    second = service.promote_strategy_challenger(
        b,
        expected_challenger_manifest_sha256="manifest-b",
        expected_current_champion_id=a["strategy_id"],
        confirmed=True,
        path=env["db"],
    )
    assert second["status"] == "COMMITTED"
    assert not b_runtime.exists()
    assert a_runtime.is_dir()
    assert (a_runtime / f"Max_Challenger_{a['strategy_id']}.ex5").is_file()
    assert second["previous_champion"] == a["strategy_id"]
    assert second["new_champion"] == b
    assert second["former_champion_archive"] is not None
    current = current_champion(path=env["db"])
    assert current is not None and current["strategy_id"] == b
    old = service.get_champion(a["strategy_id"], path=env["db"])
    assert old is not None and old["status"] == "FORMER"
    assert old["replaced_by"] == b
    assert get_challenger(a["strategy_id"], path=env["db"])["status"] == "CHALLENGER"
    assert get_challenger(b, path=env["db"])["status"] == "PROMOTED"
    persisted = get_promotion(second["promotion_id"], path=env["db"])
    assert persisted is not None
    assert persisted["post_state"]["previous_challenger_final_status"] == "CHALLENGER"
    assert [row["challenger_id"] for row in list_challengers(path=env["db"])] == [
        a["strategy_id"]
    ]


def test_later_promotion_failure_keeps_a_current_and_b_challenger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = fixture_env(tmp_path, monkeypatch)
    promote(env)
    a = current_champion(path=env["db"])
    assert a is not None
    _job_b, b, _source_b = _add_second_service_challenger(env, monkeypatch)
    b_runtime = install_runtime_challenger_ea(env, b)

    original_copy = service._atomic_copy
    failed = {"done": False}

    def fail_after_challenger_transition(source: Path, destination: Path) -> None:
        if not failed["done"] and destination.name == "Max_MTF.ex5":
            failed["done"] = True
            raise RuntimeError("REPLACEMENT_FILESYSTEM_COMMIT_FAIL")
        original_copy(source, destination)

    monkeypatch.setattr(service, "_atomic_copy", fail_after_challenger_transition)
    with pytest.raises(RuntimeError, match="REPLACEMENT_FILESYSTEM_COMMIT_FAIL"):
        service.promote_strategy_challenger(
            b,
            expected_challenger_manifest_sha256="manifest-b",
            expected_current_champion_id=a["strategy_id"],
            confirmed=True,
            path=env["db"],
        )

    current = current_champion(path=env["db"])
    assert current is not None and current["strategy_id"] == a["strategy_id"]
    assert get_challenger(a["strategy_id"], path=env["db"])["status"] == "PROMOTED"
    assert get_challenger(b, path=env["db"])["status"] == "CHALLENGER"
    assert b_runtime.is_dir()
    assert not (b_runtime.parent / a["strategy_id"]).exists()
    assert service.get_champion(a["strategy_id"], path=env["db"])["status"] == "CURRENT"

def test_replaced_champion_returns_as_challenger_and_can_reenter_champion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = fixture_env(tmp_path, monkeypatch)
    a_before = get_challenger(env["challenger_id"], path=env["db"])
    assert a_before is not None
    a_bundle = env["root"] / a_before["bundle_path"]
    a_ea = a_bundle / f"Max_Challenger_{env['challenger_id']}.mq5"
    a_set = a_bundle / f"Max_Challenger_{env['challenger_id']}.set"
    a_identity = {
        "challenger_id": a_before["challenger_id"],
        "bundle_path": a_before["bundle_path"],
        "manifest_sha256": a_before["manifest_sha256"],
        "ea_version": a_before["ea_version"],
        "ea_sha256": sha(a_ea),
        "set_sha256": sha(a_set),
    }

    assert promote(env)["status"] == "COMMITTED"
    a_champion_set = env["old_set"].read_bytes()
    assert b"InpEntryThreshold=0.18||0.18||0||0.18||N" in a_champion_set
    _job_b, b, _source_b = _add_second_service_challenger(env, monkeypatch)
    b_before = get_challenger(b, path=env["db"])
    assert b_before is not None
    b_bundle = env["root"] / b_before["bundle_path"]
    b_ea = b_bundle / f"Max_Challenger_{b}.mq5"
    b_set = b_bundle / f"Max_Challenger_{b}.set"
    b_identity = {
        "challenger_id": b_before["challenger_id"],
        "bundle_path": b_before["bundle_path"],
        "manifest_sha256": b_before["manifest_sha256"],
        "ea_version": b_before["ea_version"],
        "ea_sha256": sha(b_ea),
        "set_sha256": sha(b_set),
    }

    p2 = service.promote_strategy_challenger(
        b,
        expected_challenger_manifest_sha256="manifest-b",
        expected_current_champion_id=env["challenger_id"],
        confirmed=True,
        path=env["db"],
    )
    assert p2["status"] == "COMMITTED"
    a_runtime = (
        env["root"]
        / "mt5"
        / "MQL5"
        / "Experts"
        / "MaxMTF"
        / "Challengers"
        / env["challenger_id"]
    )
    b_runtime = a_runtime.parent / b
    assert a_runtime.is_dir()
    assert not b_runtime.exists()
    assert current_champion(path=env["db"])["strategy_id"] == b
    assert get_challenger(env["challenger_id"], path=env["db"])["status"] == "CHALLENGER"
    assert get_challenger(b, path=env["db"])["status"] == "PROMOTED"
    current_champion_set = env["old_set"].read_bytes()
    assert b"InpEntryThreshold=0.21||0.21||0||0.21||N" in current_champion_set
    assert re.search(
        rb"(?m)^\s*input double\s+InpEntryThreshold\s*=\s*0\.21\s*;",
        env["old_source"].read_bytes(),
    )
    assert env["old_ex5"].read_bytes() == b"fresh-ex5-" + sha(b_ea).encode()
    assert (
        service.CHAMPION_CURRENT_ROOT / "Max_MTF.set"
    ).read_bytes() == current_champion_set
    assert [row["challenger_id"] for row in list_challengers(path=env["db"])] == [
        env["challenger_id"]
    ]

    p3 = service.promote_strategy_challenger(
        env["challenger_id"],
        expected_challenger_manifest_sha256=env["manifest_sha"],
        expected_current_champion_id=b,
        confirmed=True,
        path=env["db"],
    )

    assert p3["status"] == "COMMITTED"
    assert not a_runtime.exists()
    assert b_runtime.is_dir()
    assert (b_runtime / f"Max_Challenger_{b}.ex5").is_file()
    assert current_champion(path=env["db"])["strategy_id"] == env["challenger_id"]
    a_after = get_challenger(env["challenger_id"], path=env["db"])
    b_after = get_challenger(b, path=env["db"])
    assert a_after is not None and b_after is not None
    assert a_after["status"] == "PROMOTED"
    assert b_after["status"] == "CHALLENGER"
    assert env["old_set"].read_bytes() == a_champion_set
    assert re.search(
        rb"(?m)^\s*input double\s+InpEntryThreshold\s*=\s*0\.18\s*;",
        env["old_source"].read_bytes(),
    )
    assert env["old_ex5"].read_bytes() == b"fresh-ex5-" + sha(a_ea).encode()
    assert {
        "challenger_id": a_after["challenger_id"],
        "bundle_path": a_after["bundle_path"],
        "manifest_sha256": a_after["manifest_sha256"],
        "ea_version": a_after["ea_version"],
        "ea_sha256": sha(a_ea),
        "set_sha256": sha(a_set),
    } == a_identity
    assert {
        "challenger_id": b_after["challenger_id"],
        "bundle_path": b_after["bundle_path"],
        "manifest_sha256": b_after["manifest_sha256"],
        "ea_version": b_after["ea_version"],
        "ea_sha256": sha(b_ea),
        "set_sha256": sha(b_set),
    } == b_identity

    tenures = list_champions(path=env["db"])
    assert len(tenures) == 3
    assert len([row for row in tenures if row["status"] == "CURRENT"]) == 1
    assert len([row for row in tenures if row["status"] == "FORMER"]) == 2
    history = service.promotion_history(path=env["db"])
    committed = [row for row in history if row["state"] == "COMMITTED"]
    assert len(committed) == 3


def test_archived_former_champion_stays_out_of_active_and_promotion_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = fixture_env(tmp_path, monkeypatch)
    promote(env)
    _job_b, b, _source_b = _add_second_service_challenger(env, monkeypatch)
    service.promote_strategy_challenger(
        b,
        expected_challenger_manifest_sha256="manifest-b",
        expected_current_champion_id=env["challenger_id"],
        confirmed=True,
        path=env["db"],
    )
    assert current_champion(path=env["db"])["strategy_id"] == b
    with pytest.raises(RuntimeError, match="CHALLENGER_NOT_ACTIVE"):
        challenger_operations.retire_challenger(
            b,
            expected_manifest_sha256="manifest-b",
            confirmed=True,
            path=env["db"],
        )
    former_source = get_challenger(env["challenger_id"], path=env["db"])
    assert former_source is not None and former_source["status"] == "CHALLENGER"
    retired = retire_registry_row(
        env["challenger_id"],
        retirement_id="RETIRE-FORMER-CHALLENGER",
        expected_manifest_sha256=former_source["manifest_sha256"],
        evidence_path="evidence/test/RETIRE-FORMER-CHALLENGER",
        path=env["db"],
    )
    assert retired["challenger"]["status"] == "RETIRED"
    assert [row["challenger_id"] for row in list_registry_page(
        view="retired", path=env["db"]
    )["items"]] == [env["challenger_id"]]

    tenure_count_before = len(list_champions(path=env["db"]))
    compile_calls = {"count": 0}

    def forbidden_compile(**_kwargs):
        compile_calls["count"] += 1
        raise AssertionError("compile must not run for an archived Challenger")

    monkeypatch.setattr(service, "_compile_champion", forbidden_compile)
    with pytest.raises(RuntimeError, match="PROMOTION_CHALLENGER_NOT_ACTIVE"):
        service.promote_strategy_challenger(
            env["challenger_id"],
            expected_challenger_manifest_sha256=env["manifest_sha"],
            expected_current_champion_id=b,
            confirmed=True,
            path=env["db"],
        )

    assert compile_calls["count"] == 0
    assert current_champion(path=env["db"])["strategy_id"] == b
    assert get_challenger(b, path=env["db"])["status"] == "PROMOTED"
    assert get_challenger(env["challenger_id"], path=env["db"])["status"] == "RETIRED"
    assert len(list_champions(path=env["db"])) == tenure_count_before
