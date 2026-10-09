from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import max_backend.challenger_api as challenger_api
import max_backend.challenger_deployment as challenger_deployment
import max_backend.challenger_operations as operations
import max_backend.challenger_registry as registry
from max_backend.challenger_bundle import write_challenger_set
from max_backend.challenger_operations_store import (
    challenger_operations_database_status,
    create_backtest_record,
    get_backtest,
    list_registry_page,
    list_retirements,
    migrate_m06,
    recover_incomplete_backtests,
    retire_registry_row,
    update_backtest,
)
from max_backend.challenger_store import (
    finalize_challenger,
    get_challenger,
    reserve_challenger,
)
from max_backend.champion_store import (
    commit_promotion_authority,
    create_prepared_promotion,
    current_champion,
    get_promotion,
    migrate_m04,
    update_promotion,
)
from max_backend.db import connect, ensure_baseline_registered, initialize_database
from max_backend.optimizer_core import (
    ABSOLUTE_BOUNDS,
    CURRENT_OPTIMIZER_SCHEMA,
    LEGACY_ABSOLUTE_BOUNDS,
    optimizer_fixed_execution_authority,
    parse_set_optimizer_entries,
    read_ea_optimizer_defaults,
)
from max_backend.optimizer_store import create_job
from max_backend.scientist_store import migrate_m05
from max_backend.mtf_geometry import STRATEGY_CONTRACT, resolve_strategy_geometry


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request_payload(root: Path) -> dict:
    return {
        "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V3",
        "strategy_contract": STRATEGY_CONTRACT,
        "strategy_geometry": resolve_strategy_geometry("M30"),
        "symbol": "XAUUSD.m",
        "relative_symbol": "EURUSD.m",
        "period": "M30",
        "from_date": "2026.08.01",
        "to_date": "2026.08.15",
        "model": 1,
        "deposit": 10000.0,
        "leverage": 100,
        "max_rounds": 1,
        "kpi": {},
        "trade_sample": {},
        "optimize_params": ["InpMinConsensus"],
        "scientist_assist": False,
        "mt5": {
            "terminal": str(root / "terminal64.exe"),
            "metaeditor": str(root / "metaeditor64.exe"),
            "data_root": str(root / "mt5"),
        },
        "ea": {"version": "2.10", "sha256": "baseline"},
    }


def current_request_payload(root: Path) -> dict:
    request = request_payload(root)
    request["schema"] = CURRENT_OPTIMIZER_SCHEMA
    request["fixed_execution_authority"] = optimizer_fixed_execution_authority(
        CURRENT_OPTIMIZER_SCHEMA
    )
    return request


def add_challenger(
    db: Path,
    root: Path,
    *,
    challenger_id: str,
    source_pass: int,
    created_utc: str,
) -> tuple[str, dict]:
    request = request_payload(root)
    job = create_job(request, evidence_root=root / "optimizer", path=db)
    defaults = read_ea_optimizer_defaults()
    params = {name: defaults[name] for name in LEGACY_ABSOLUTE_BOUNDS}
    bundle_rel = Path("artifacts") / "strategy_challengers" / challenger_id
    bundle = root / bundle_rel
    bundle.mkdir(parents=True, exist_ok=True)
    ea = bundle / f"Max_Challenger_{challenger_id}.mq5"
    ea.write_text("// fixture challenger\n", encoding="utf-8")
    set_path = bundle / f"Max_Challenger_{challenger_id}.set"
    write_challenger_set(set_path, params, request)

    reserve_challenger(
        {
            "challenger_id": challenger_id,
            "source_job_id": job["job_id"],
            "source_round": 1,
            "source_pass": source_pass,
            "created_utc": created_utc,
            "ea_version": "2.10",
            "baseline_ea_sha256": "baseline",
            "bundle_path": bundle_rel.as_posix(),
            "params": params,
            "kpi": {
                "profit_factor": 1.5 + source_pass / 100,
                "recovery_factor": 1.2,
                "mean_r": 0.2,
                "weighted_r": 0.3,
                "trades": 25,
                "required_trades": 20,
            },
            "hard_gates": {"min_profit_factor": 1.0},
            "source_request": request,
            "winner": {"job_id": job["job_id"], "round": 1, "mt5_pass": source_pass},
            "provenance": {"run_nonce": source_pass},
            "winning_xml_sha256": f"xml-{source_pass}",
            "winning_sidecar_sha256": f"sidecar-{source_pass}",
        },
        path=db,
    )
    row = finalize_challenger(
        challenger_id,
        challenger_ea_sha256=sha(ea),
        set_sha256=sha(set_path),
        metadata_sha256=f"meta-{source_pass}",
        manifest_sha256=f"manifest-{source_pass}",
        path=db,
    )
    return job["job_id"], row


def fresh_schema6(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "root"
    db = root / "state" / "max.db"
    initialize_database(db)
    ensure_baseline_registered(db)
    _job, row = add_challenger(
        db,
        root,
        challenger_id="STRAT-20260923-120000-R01-P7",
        source_pass=7,
        created_utc="2026-09-23T12:00:00+00:00",
    )
    migrate_m04(db)
    migrate_m05(db)
    with connect(db) as conn:
        assert int(
            conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()["value"]
        ) == 6
    return root, db, row["challenger_id"]


def install_verified_challenger_deployment(
    row: dict,
    *,
    source_ea: Path,
    source_set: Path,
) -> Path:
    challenger_id = str(row["challenger_id"])
    authority = challenger_deployment.resolve_frozen_mt5_expert_root(
        row["source_request"]
    )
    directory = challenger_deployment.challenger_deployment_directory(
        expert_root=authority["expert_root"],
        challenger_id=challenger_id,
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"Max_Challenger_{challenger_id}.mq5").write_bytes(source_ea.read_bytes())
    (directory / f"Max_Challenger_{challenger_id}.set").write_bytes(source_set.read_bytes())
    (directory / f"Max_Challenger_{challenger_id}.ex5").write_bytes(b"synthetic compiled EA")
    challenger_deployment._deployment_payload(
        directory,
        challenger_id=challenger_id,
        source_identity={
            "job_id": str(row["source_job_id"]),
            "round": int(row["source_round"]),
            "pass": int(row["source_pass"]),
        },
        bundle_manifest_sha256=str(row["manifest_sha256"]),
        source_sha256=sha(source_ea),
        set_sha256=sha(source_set),
        metaeditor_sha256="synthetic-metaeditor-sha",
        compile_result={"found": True, "errors": 0, "warnings": 0},
    )
    return directory


def test_schema6_to7_preserves_challenger_and_foreign_keys(tmp_path: Path) -> None:
    root, db, cid = fresh_schema6(tmp_path)
    before = get_challenger(cid, path=db)
    assert before is not None
    identity = {
        key: before[key]
        for key in (
            "challenger_id",
            "bundle_path",
            "manifest_sha256",
            "challenger_ea_sha256",
            "set_sha256",
            "source_job_id",
            "source_round",
            "source_pass",
        )
    }

    migrate_m06(db)
    migrate_m06(db)

    status = challenger_operations_database_status(db)
    assert status["status"] == "READY"
    assert status["schema_version"] == 7
    after = get_challenger(cid, path=db)
    assert after is not None
    assert {key: after[key] for key in identity} == identity
    assert after["status"] == "CHALLENGER"
    assert after["retired_utc"] is None
    with connect(db) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_rejected_candidate_schema7_backtest_table_upgrades_in_place(
    tmp_path: Path,
) -> None:
    root, db, cid = fresh_schema6(tmp_path)
    row = get_challenger(cid, path=db)
    assert row is not None

    with connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE strategy_challenger_backtests (
                backtest_id TEXT PRIMARY KEY,
                challenger_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN ('PREPARED','RUNNING','COMPLETED','FAILED')
                ),
                created_utc TEXT NOT NULL,
                started_utc TEXT,
                completed_utc TEXT,
                source_manifest_sha256 TEXT NOT NULL,
                request_json TEXT NOT NULL,
                ea_sha256 TEXT NOT NULL,
                set_sha256 TEXT NOT NULL,
                ex5_sha256 TEXT,
                report_path TEXT,
                report_sha256 TEXT,
                result_json TEXT,
                evidence_path TEXT NOT NULL,
                error TEXT,
                FOREIGN KEY(challenger_id)
                    REFERENCES strategy_challengers(challenger_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO strategy_challenger_backtests(
                backtest_id,challenger_id,state,created_utc,
                source_manifest_sha256,request_json,
                ea_sha256,set_sha256,evidence_path
            ) VALUES(
                'BT-OLD-CANDIDATE',?,'RUNNING','2026-09-23T10:00:00+00:00',
                ?, '{}', ?, ?, 'evidence/m06/old'
            )
            """,
            (
                cid,
                row["manifest_sha256"],
                row["challenger_ea_sha256"],
                row["set_sha256"],
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version','7')"
        )

    migrate_m06(db)

    with connect(db) as conn:
        sql = str(
            conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='strategy_challenger_backtests'"
            ).fetchone()["sql"]
        )
        tables = {
            item["name"]
            for item in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "UNCONFIRMED" in sql
    assert "strategy_challenger_retirements" in tables
    preserved = get_backtest("BT-OLD-CANDIDATE", path=db)
    assert preserved is not None
    assert preserved["state"] == "RUNNING"

    assert recover_incomplete_backtests(db) == 1
    recovered = get_backtest("BT-OLD-CANDIDATE", path=db)
    assert recovered is not None
    assert recovered["state"] == "UNCONFIRMED"


def test_schema6_to7_preserves_current_champion_and_promotion_history(tmp_path: Path) -> None:
    root, db, cid = fresh_schema6(tmp_path)
    row = get_challenger(cid, path=db)
    assert row is not None
    promotion_id = "PROMOTE-M06-MIGRATION"
    create_prepared_promotion(
        promotion_id=promotion_id,
        challenger_id=cid,
        previous_champion_id=None,
        expected_manifest_sha256=row["manifest_sha256"],
        before_state={"files": {}},
        recovery_path="state/recovery/m06",
        path=db,
    )
    update_promotion(promotion_id, state="FILES_COMMITTED", path=db)
    commit_promotion_authority(
        promotion_id,
        champion={
            "source_job_id": row["source_job_id"],
            "source_round": row["source_round"],
            "source_pass": row["source_pass"],
            "params": row["params"],
            "kpi": row["kpi"],
            "hard_gates": row["hard_gates"],
            "champion_ea_sha256": "champion-ea",
            "champion_set_sha256": "champion-set",
            "deployed_ea_sha256": "deployed-ea",
            "deployed_ex5_sha256": "deployed-ex5",
            "tester_set_sha256": "tester-set",
            "promoted_utc": "2026-09-23T12:10:00+00:00",
            "artifact_path": "ea/champion/current",
        },
        post_state={"current_champion_id": cid},
        baseline_archive_id="BASELINE-M06",
        former_champion_archive_id=None,
        compile_result={"status": "PASS"},
        parity={"status": "VERIFIED"},
        deployment={"mt5_source": "fixture"},
        evidence_path="evidence/m04/promotions/PROMOTE-M06-MIGRATION",
        path=db,
    )
    assert current_champion(path=db)["strategy_id"] == cid
    assert get_promotion(promotion_id, path=db)["state"] == "COMMITTED"
    migrate_m05(db)

    migrate_m06(db)

    current = current_champion(path=db)
    assert current is not None and current["strategy_id"] == cid
    assert get_challenger(cid, path=db)["status"] == "PROMOTED"
    assert get_promotion(promotion_id, path=db)["state"] == "COMMITTED"
    with connect(db) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_retirement_removes_compiled_ea_and_preserves_bundle_and_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, db, cid = fresh_schema6(tmp_path)
    migrate_m06(db)
    row = get_challenger(cid, path=db)
    assert row is not None
    bundle = root / row["bundle_path"]
    before = {
        path.relative_to(bundle).as_posix(): path.read_bytes()
        for path in bundle.rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(operations, "ROOT", root)
    monkeypatch.setattr(
        operations,
        "RETIREMENT_EVIDENCE_ROOT",
        root / "evidence" / "m06" / "challenger_retirements",
    )
    monkeypatch.setattr(
        operations,
        "verify_challenger_bundle",
        lambda *_a, **_k: {
            "status": "VERIFIED",
            "manifest_sha256": row["manifest_sha256"],
            "bundle_path": row["bundle_path"],
        },
    )

    request = row["source_request"]
    data_root = Path(request["mt5"]["data_root"])
    terminal = Path(request["mt5"]["terminal"])
    metaeditor = Path(request["mt5"]["metaeditor"])
    terminal.parent.mkdir(parents=True, exist_ok=True)
    terminal.write_bytes(b"synthetic terminal placeholder")
    metaeditor.write_bytes(b"synthetic MetaEditor placeholder")
    (data_root / "MQL5").mkdir(parents=True, exist_ok=True)

    def fake_metaeditor_run(command, **_kwargs):
        compile_arg = next(
            item for item in command if str(item).startswith("/compile:")
        )
        compile_source = Path(str(compile_arg).split(":", 1)[1])
        compile_source.with_suffix(".ex5").write_bytes(b"synthetic compiled EA")
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
    authority = challenger_deployment.resolve_mt5_authority(request)
    staging = challenger_deployment.new_challenger_staging_root(
        expert_root=authority["expert_root"],
        operation_id=f"TEST-{cid}",
    )
    try:
        prepared = challenger_deployment.prepare_challenger_deployment(
            challenger_id=cid,
            source_request=request,
            source_identity={
                "job_id": row["source_job_id"],
                "round": row["source_round"],
                "pass": row["source_pass"],
            },
            bundle_manifest_sha256=row["manifest_sha256"],
            source_ea=bundle / f"Max_Challenger_{cid}.mq5",
            source_set=bundle / f"Max_Challenger_{cid}.set",
            authority=authority,
            staging_root=staging,
        )
        challenger_deployment.commit_challenger_deployment(prepared)
    finally:
        challenger_deployment.cleanup_challenger_staging_root(staging)
    runtime_dir = authority["expert_root"] / "Challengers" / cid
    assert (runtime_dir / f"Max_Challenger_{cid}.ex5").is_file()

    remove_runtime_ea = operations._remove_retired_runtime_ea
    monkeypatch.setattr(
        operations,
        "_remove_retired_runtime_ea",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("SYNTHETIC_REMOVAL_INTERRUPTED")
        ),
    )
    with pytest.raises(RuntimeError, match="CHALLENGER_RETIRED_RUNTIME_EA_REMOVAL_PENDING"):
        operations.retire_challenger(
            cid,
            expected_manifest_sha256=row["manifest_sha256"],
            confirmed=True,
            path=db,
        )
    assert get_challenger(cid, path=db)["status"] == "RETIRED"
    assert runtime_dir.is_dir()
    monkeypatch.setattr(operations, "_remove_retired_runtime_ea", remove_runtime_ea)
    recovered = operations.recover_retired_challenger_deployments(path=db)
    assert recovered == [
        {
            "challenger_id": cid,
            "retirement_id": next(
                item["retirement_id"] for item in list_retirements(cid, path=db)
            ),
            "runtime_ea": "REMOVED",
        }
    ]
    assert not runtime_dir.exists()

    retired = operations.retire_challenger(
        cid,
        expected_manifest_sha256=row["manifest_sha256"],
        confirmed=True,
        path=db,
    )
    assert retired["status"] == "RETIRED"
    assert retired["retired_utc"]
    assert retired["retirement"] == "COMPILED_EA_REMOVED_BUNDLE_PRESERVED"
    assert retired["runtime_ea"] == "REMOVED"
    assert retired["bundle_preserved"] is True
    assert retired["parameters_preserved"] is True
    assert not runtime_dir.exists()
    assert retired["retirement_state"] == "COMMITTED"
    assert retired["retirement_before_status"] == "CHALLENGER"
    assert retired["retirement_after_status"] == "RETIRED"
    journals = list_retirements(cid, path=db)
    assert len(journals) == 1
    assert journals[0]["retirement_id"] == retired["retirement_id"]
    assert journals[0]["state"] == "COMMITTED"
    assert journals[0]["expected_manifest_sha256"] == row["manifest_sha256"]
    after = {
        path.relative_to(bundle).as_posix(): path.read_bytes()
        for path in bundle.rglob("*")
        if path.is_file()
    }
    assert after == before

    repeated = operations.retire_challenger(
        cid,
        expected_manifest_sha256=row["manifest_sha256"],
        confirmed=True,
        path=db,
    )
    assert repeated["retired_utc"] == retired["retired_utc"]
    assert not runtime_dir.exists()
    with pytest.raises(RuntimeError, match="EXPLICIT_RETIREMENT_CONFIRMATION_REQUIRED"):
        operations.retire_challenger(
            cid,
            expected_manifest_sha256=row["manifest_sha256"],
            confirmed=False,
            path=db,
        )


def test_verified_challenger_ea_and_set_downloads_work_in_retired_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, db, cid = fresh_schema6(tmp_path)
    row = get_challenger(cid, path=db)
    assert row is not None
    bundle = root / row["bundle_path"]
    ea = bundle / f"Max_Challenger_{cid}.mq5"
    set_path = bundle / f"Max_Challenger_{cid}.set"
    monkeypatch.setattr(operations, "ROOT", root)
    monkeypatch.setattr(
        operations,
        "CHALLENGER_ARTIFACT_ROOT",
        root / "artifacts" / "strategy_challengers",
    )
    monkeypatch.setattr(
        operations,
        "LEGACY_CHALLENGER_ARTIFACT_ROOT",
        root / "artifacts" / "challengers",
    )
    verified_statuses: list[bool] = []

    def verified_bundle(_challenger_id, *, allow_retired=False, path):
        verified_statuses.append(allow_retired)
        return {"status": "VERIFIED"}

    monkeypatch.setattr(operations, "verify_challenger_bundle", verified_bundle)
    monkeypatch.setattr(
        challenger_api,
        "challenger_bundle_artifact_path",
        lambda challenger_id, artifact: operations.challenger_bundle_artifact_path(
            challenger_id,
            artifact,
            path=db,
        ),
    )

    ea_response = challenger_api.download_challenger_ea(cid)
    set_response = challenger_api.download_challenger_set(cid)
    assert Path(ea_response.path) == ea
    assert Path(set_response.path) == set_path
    assert 'attachment; filename="Max_Challenger_' + cid + '.mq5"' in (
        ea_response.headers["content-disposition"]
    )

    retire_registry_row(
        cid,
        retirement_id="RETIRE-DOWNLOAD-TEST",
        expected_manifest_sha256=row["manifest_sha256"],
        evidence_path="evidence/test/RETIRE-DOWNLOAD-TEST",
        path=db,
    )
    archived_response = challenger_api.download_challenger_ea(cid)
    assert Path(archived_response.path) == ea
    assert verified_statuses == [False, False, True]


def test_registry_active_and_retired_views_are_paginated_searchable_and_sortable(
    tmp_path: Path,
) -> None:
    root, db, first = fresh_schema6(tmp_path)
    migrate_m06(db)
    source = get_challenger(first, path=db)
    assert source is not None
    with connect(db) as conn:
        for index in range(55):
            cid = f"STRAT-20260923-13{index:04d}-R99-P{index + 100}"
            status = "RETIRED" if index % 4 == 0 else "CHALLENGER"
            retired = f"2026-09-23T14:{index % 60:02d}:00+00:00" if status == "RETIRED" else None
            conn.execute(
                """
                INSERT INTO strategy_challengers(
                    challenger_id,status,role_origin,
                    source_job_id,source_round,source_pass,
                    created_utc,updated_utc,retired_utc,
                    ea_version,baseline_ea_sha256,
                    challenger_ea_sha256,set_sha256,metadata_sha256,manifest_sha256,
                    bundle_path,params_json,kpi_json,hard_gates_json,source_request_json,
                    winner_json,provenance_json,winning_xml_sha256,winning_sidecar_sha256,
                    champion_mutation,registration_error
                ) VALUES(
                    ?,?,'OPTIMIZER_WINNER',
                    ?,99,?,
                    ?,?,?, '2.00',?,
                    'ea','set','meta',?,
                    ?,?,?,?,?,?,
                    ?,'xml','sidecar','NONE',NULL
                )
                """,
                (
                    cid,
                    status,
                    source["source_job_id"],
                    index + 100,
                    f"2026-09-23T13:{index % 60:02d}:00+00:00",
                    f"2026-09-23T13:{index % 60:02d}:00+00:00",
                    retired,
                    source["baseline_ea_sha256"],
                    f"manifest-{index}",
                    f"artifacts/strategy_challengers/{cid}",
                    json.dumps(source["params"]),
                    json.dumps({
                        "profit_factor": 1.0 + index / 100,
                        "recovery_factor": 1.0,
                        "mean_r": 0.1,
                        "weighted_r": index / 100,
                        "trades": 20 + index,
                    }),
                    json.dumps(source["hard_gates"]),
                    json.dumps(source["source_request"]),
                    json.dumps(source["winner"]),
                    json.dumps(source["provenance"]),
                ),
            )

    active = list_registry_page(
        view="active",
        sort="weighted_r",
        order="desc",
        page=2,
        page_size=10,
        path=db,
    )
    assert active["total"] > 30
    assert active["page"] == 2
    assert len(active["items"]) == 10
    weights = [item["kpi"]["weighted_r"] for item in active["items"]]
    assert weights == sorted(weights, reverse=True)

    retired = list_registry_page(
        view="retired",
        query="R99-P100",
        page=1,
        page_size=25,
        path=db,
    )
    assert retired["total"] == 1
    assert retired["items"][0]["status"] == "RETIRED"


def test_backtest_uses_retained_bundle_mt5_truth_and_history_survives_retirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, db, cid = fresh_schema6(tmp_path)
    migrate_m06(db)
    row = get_challenger(cid, path=db)
    assert row is not None
    bundle = root / row["bundle_path"]
    ea = bundle / f"Max_Challenger_{cid}.mq5"
    set_path = bundle / f"Max_Challenger_{cid}.set"
    before_ea = ea.read_bytes()
    before_set = set_path.read_bytes()
    retained_entries = parse_set_optimizer_entries(
        set_path.read_text(encoding="utf-8"),
        bounds=ABSOLUTE_BOUNDS,
    )
    assert set(retained_entries) == set(LEGACY_ABSOLUTE_BOUNDS)
    assert len(retained_entries) == 16
    assert "InpRiskPct" not in retained_entries

    terminal = root / "terminal64.exe"
    metaeditor = root / "metaeditor64.exe"
    terminal.write_bytes(b"terminal")
    metaeditor.write_bytes(b"metaeditor")
    data_root = root / "mt5"
    data_root.mkdir(parents=True, exist_ok=True)
    deployed_dir = install_verified_challenger_deployment(
        row,
        source_ea=ea,
        source_set=set_path,
    )

    monkeypatch.setattr(operations, "ROOT", root)
    monkeypatch.setattr(
        operations,
        "CHALLENGER_ARTIFACT_ROOT",
        root / "artifacts" / "strategy_challengers",
    )
    monkeypatch.setattr(
        operations,
        "BACKTEST_EVIDENCE_ROOT",
        root / "evidence" / "m06" / "challenger_backtests",
    )
    monkeypatch.setattr(
        operations,
        "RETIREMENT_EVIDENCE_ROOT",
        root / "evidence" / "m06" / "challenger_retirements",
    )
    monkeypatch.setattr(
        operations,
        "detect_mt5",
        lambda: {
            "status": "READY_EXECUTABLE_AND_DATA_ROOT",
            "terminal": str(terminal),
            "metaeditor": str(metaeditor),
            "data_root": str(data_root),
        },
    )
    monkeypatch.setattr(operations, "active_job", lambda **_k: None)
    monkeypatch.setattr(
        operations,
        "verify_challenger_bundle",
        lambda *_a, **_k: {
            "status": "VERIFIED",
            "manifest_sha256": row["manifest_sha256"],
            "ea_sha256": sha(ea),
            "set_sha256": sha(set_path),
            "bundle_path": row["bundle_path"],
        },
    )
    def fake_launch(_request: dict, *, ini_path: str | Path, timeout_sec: int) -> int:
        assert timeout_sec == 21600
        ini_text = Path(ini_path).read_text(encoding="utf-8")
        assert "Optimization=0" in ini_text
        assert (
            "Expert=MaxMTF\\Challengers\\"
            + cid
            + "\\Max_Challenger_"
            + cid
        ) in ini_text
        assert not (data_root / "MQL5" / "Experts" / "MaxMTF" / "ChallengerBacktests").exists()
        report_line = next(
            line for line in ini_text.splitlines() if line.startswith("Report=")
        )
        report_setting = report_line.split("=", 1)[1].replace("\\\\", "/")
        report = Path(_request["mt5"]["data_root"]) / (report_setting + ".htm")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            """<html><body><table>
            <tr><td>Total Net Profit:</td><td>125.50</td></tr>
            <tr><td>Gross Profit:</td><td>240.00</td></tr>
            <tr><td>Gross Loss:</td><td>-114.50</td></tr>
            <tr><td>Profit Factor:</td><td>2.096</td></tr>
            <tr><td>Expected Payoff:</td><td>5.02</td></tr>
            <tr><td>Recovery Factor:</td><td>1.75</td></tr>
            <tr><td>Sharpe Ratio:</td><td>1.31</td></tr>
            <tr><td>Total Trades:</td><td>25</td></tr>
            <tr><td>Profit Trades (% of total):</td><td>15 (60.00%)</td></tr>
            <tr><td>Loss Trades (% of total):</td><td>10 (40.00%)</td></tr>
            <tr><td>Balance Drawdown Absolute:</td><td>12.00</td></tr>
            <tr><td>Balance Drawdown Maximal:</td><td>71.70 (0.72%)</td></tr>
            <tr><td>Balance Drawdown Relative:</td><td>0.72% (71.70)</td></tr>
            <tr><td>Equity Drawdown Maximal:</td><td>81.20 (0.81%)</td></tr>
            <tr><td>Equity Drawdown Relative:</td><td>0.81% (81.20)</td></tr>
            </table></body></html>""",
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(operations, "launch_mt5", fake_launch)

    result = operations.run_challenger_backtest(
        cid,
        request_payload={},
        path=db,
    )
    assert result["state"] == "COMPLETED"
    assert result["result"]["execution_truth"] == "MT5_STRATEGY_TESTER"
    assert result["result"]["parameter_mutation"] == "NONE"
    assert result["result"]["live_authority"] == "NONE"
    assert result["result"]["deployment_reused"] is True
    assert result["result"]["deployment_manifest_sha256"]
    assert result["result"]["runtime"]["expert_reused"] is True
    assert Path(result["result"]["runtime"]["expert_dir"]) == deployed_dir
    assert (deployed_dir / f"Max_Challenger_{cid}.ex5").is_file()
    assert result["result"]["terminal_sha256"] == sha(terminal)
    assert result["result"]["runtime_set_sha256"]
    runtime_text = Path(
        result["result"]["runtime"]["tester_set"]
    ).read_text(encoding="utf-8")
    runtime_entries = parse_set_optimizer_entries(
        runtime_text,
        bounds=ABSOLUTE_BOUNDS,
    )
    assert set(runtime_entries) == set(LEGACY_ABSOLUTE_BOUNDS)
    assert len(runtime_entries) == 16
    assert "InpRiskPct" not in runtime_entries
    assert "InpMaxDailyLossPct=3" in runtime_text
    assert "InpMaxDailyLossPct=5" not in runtime_text
    assert result["result"]["tester_ini_sha256"]
    assert result["result"]["report_file"] == "backtest_report.htm"
    assert result["result"]["metrics"]["total_net_profit"] == 125.5
    assert result["result"]["metrics"]["profit_factor"] == pytest.approx(2.096)
    assert result["result"]["metrics"]["total_trades"] == 25
    assert result["result"]["metrics"]["profit_trades_pct"] == 60.0
    assert result["report_path"].endswith("/backtest_report.htm")
    assert result["request"]["contract_authority"] == "RETAINED_SOURCE_REQUEST"
    assert result["request"]["from_date"] == "2026.08.01"
    assert result["request"]["to_date"] == "2026.08.15"
    assert ea.read_bytes() == before_ea
    assert set_path.read_bytes() == before_set

    retired = operations.retire_challenger(
        cid,
        expected_manifest_sha256=row["manifest_sha256"],
        confirmed=True,
        path=db,
    )
    assert retired["status"] == "RETIRED"
    history = operations.backtest_history(cid, path=db)
    assert len(history) == 1
    assert history[0]["backtest_id"] == result["backtest_id"]
    with pytest.raises(RuntimeError, match="CHALLENGER_NOT_ACTIVE"):
        operations.run_challenger_backtest(cid, request_payload={}, path=db)


def test_runtime_set_accepts_current_true_17d_without_mutating_retained_set(
    tmp_path: Path,
) -> None:
    source_request = current_request_payload(tmp_path)
    params = read_ea_optimizer_defaults()
    assert set(params) == set(ABSOLUTE_BOUNDS)
    assert len(params) == 17
    assert "InpRiskPct" in params

    retained_set = tmp_path / "current-17d.set"
    write_challenger_set(retained_set, params, source_request)
    before = retained_set.read_bytes()

    runtime_text = operations._runtime_set_text(
        retained_set,
        {"relative_symbol": source_request["relative_symbol"]},
        source_request,
    )

    runtime_entries = parse_set_optimizer_entries(
        runtime_text,
        bounds=ABSOLUTE_BOUNDS,
    )
    assert set(runtime_entries) == set(ABSOLUTE_BOUNDS)
    assert len(runtime_entries) == 17
    assert runtime_entries["InpRiskPct"]["optimize"] == "N"
    assert runtime_entries["InpRiskPct"]["value"] == pytest.approx(
        float(params["InpRiskPct"])
    )
    assert "InpMaxDailyLossPct=5" in runtime_text
    assert retained_set.read_bytes() == before


def test_runtime_set_rejects_missing_extra_mixed_and_active_optimizer_rows(
    tmp_path: Path,
) -> None:
    legacy_request = request_payload(tmp_path)
    defaults = read_ea_optimizer_defaults()
    legacy_params = {
        name: defaults[name]
        for name in LEGACY_ABSOLUTE_BOUNDS
    }
    legacy_set = tmp_path / "legacy-16d.set"
    write_challenger_set(legacy_set, legacy_params, legacy_request)
    legacy_text = legacy_set.read_text(encoding="utf-8")

    current_request = current_request_payload(tmp_path)
    current_set = tmp_path / "current-17d.set"
    write_challenger_set(current_set, defaults, current_request)
    current_text = current_set.read_text(encoding="utf-8")

    mixed = tmp_path / "mixed-legacy-plus-risk.set"
    mixed.write_text(
        legacy_text + "InpRiskPct=0.5||0.5||0||0.5||N\n",
        encoding="utf-8",
    )
    with pytest.raises(
        RuntimeError,
        match="CHALLENGER_SET_PARAMETER_UNIVERSE_MISMATCH",
    ):
        operations._runtime_set_text(
            mixed,
            {"relative_symbol": legacy_request["relative_symbol"]},
            legacy_request,
        )

    missing = tmp_path / "current-missing-risk.set"
    missing.write_text(
        "\n".join(
            line
            for line in current_text.splitlines()
            if not line.startswith("InpRiskPct=")
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        RuntimeError,
        match="CHALLENGER_SET_PARAMETER_UNIVERSE_MISMATCH",
    ):
        operations._runtime_set_text(
            missing,
            {"relative_symbol": current_request["relative_symbol"]},
            current_request,
        )

    extra = tmp_path / "legacy-extra-unknown.set"
    extra.write_text(
        legacy_text + "InpUnknownOptimizer=1||1||0||1||N\n",
        encoding="utf-8",
    )
    with pytest.raises(
        RuntimeError,
        match="CHALLENGER_SET_PARAMETER_UNIVERSE_MISMATCH",
    ):
        operations._runtime_set_text(
            extra,
            {"relative_symbol": legacy_request["relative_symbol"]},
            legacy_request,
        )

    active = tmp_path / "legacy-active-optimization.set"
    first_name = next(iter(LEGACY_ABSOLUTE_BOUNDS))
    active.write_text(
        legacy_text.replace(
            f"{first_name}={legacy_params[first_name]}||"
            f"{legacy_params[first_name]}||0||"
            f"{legacy_params[first_name]}||N",
            f"{first_name}={legacy_params[first_name]}||"
            f"{legacy_params[first_name]}||0||"
            f"{legacy_params[first_name]}||Y",
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        RuntimeError,
        match="CHALLENGER_SET_CONTAINS_ACTIVE_OPTIMIZATION",
    ):
        operations._runtime_set_text(
            active,
            {"relative_symbol": legacy_request["relative_symbol"]},
            legacy_request,
        )


def test_returned_mt5_without_report_becomes_unconfirmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, db, cid = fresh_schema6(tmp_path)
    migrate_m06(db)
    row = get_challenger(cid, path=db)
    assert row is not None
    bundle = root / row["bundle_path"]
    ea = bundle / f"Max_Challenger_{cid}.mq5"
    set_path = bundle / f"Max_Challenger_{cid}.set"

    terminal = root / "terminal64.exe"
    metaeditor = root / "metaeditor64.exe"
    terminal.write_bytes(b"terminal")
    metaeditor.write_bytes(b"metaeditor")
    data_root = root / "mt5"
    data_root.mkdir(parents=True, exist_ok=True)
    install_verified_challenger_deployment(
        row,
        source_ea=ea,
        source_set=set_path,
    )

    monkeypatch.setattr(operations, "ROOT", root)
    monkeypatch.setattr(
        operations,
        "CHALLENGER_ARTIFACT_ROOT",
        root / "artifacts" / "strategy_challengers",
    )
    monkeypatch.setattr(
        operations,
        "BACKTEST_EVIDENCE_ROOT",
        root / "evidence" / "m06" / "challenger_backtests",
    )
    monkeypatch.setattr(
        operations,
        "detect_mt5",
        lambda: {
            "status": "READY_EXECUTABLE_AND_DATA_ROOT",
            "terminal": str(terminal),
            "metaeditor": str(metaeditor),
            "data_root": str(data_root),
        },
    )
    monkeypatch.setattr(operations, "active_job", lambda **_k: None)
    monkeypatch.setattr(
        operations,
        "verify_challenger_bundle",
        lambda *_a, **_k: {
            "status": "VERIFIED",
            "manifest_sha256": row["manifest_sha256"],
            "ea_sha256": sha(ea),
            "set_sha256": sha(set_path),
        },
    )
    monkeypatch.setattr(operations, "launch_mt5", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        operations,
        "_wait_for_report",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("CHALLENGER_BACKTEST_REPORT_MISSING")
        ),
    )

    with pytest.raises(RuntimeError, match="CHALLENGER_BACKTEST_REPORT_MISSING"):
        operations.run_challenger_backtest(cid, request_payload={}, path=db)

    history = operations.backtest_history(cid, path=db)
    assert len(history) == 1
    assert history[0]["state"] == "UNCONFIRMED"
    assert history[0]["error"] == "CHALLENGER_BACKTEST_REPORT_MISSING"


def test_backtest_is_frozen_to_retained_contract_and_bundle_is_confined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, db, cid = fresh_schema6(tmp_path)
    migrate_m06(db)
    row = get_challenger(cid, path=db)
    assert row is not None

    monkeypatch.setattr(
        operations,
        "detect_mt5",
        lambda: {
            "status": "READY_EXECUTABLE_AND_DATA_ROOT",
            "terminal": str(root / "terminal64.exe"),
            "metaeditor": str(root / "metaeditor64.exe"),
            "data_root": str(root / "mt5"),
        },
    )
    frozen = operations.freeze_backtest_request(row, {})
    assert frozen["contract_authority"] == "RETAINED_SOURCE_REQUEST"
    assert frozen["symbol"] == row["source_request"]["symbol"]
    assert frozen["period"] == row["source_request"]["period"]
    assert frozen["from_date"] == row["source_request"]["from_date"]
    assert frozen["to_date"] == row["source_request"]["to_date"]

    selected_window = operations.freeze_backtest_request(
        row,
        {"from_date": "2020.01.01", "to_date": "2025.01.01"},
    )
    assert selected_window["from_date"] == "2020.01.01"
    assert selected_window["to_date"] == "2025.01.01"
    assert selected_window["date_range_authority"] == "OWNER_SELECTED_BACKTEST_RANGE"
    assert selected_window["optimizer_source_date_range"] == {
        "from_date": row["source_request"]["from_date"],
        "to_date": row["source_request"]["to_date"],
    }
    ini = operations._build_backtest_ini(
        selected_window,
        expert_name="MaxMTF\\Challenger\\Max_Challenger",
        set_name="challenger.set",
        report_name="backtest.html",
    )
    assert "FromDate=2020.01.01" in ini
    assert "ToDate=2025.01.01" in ini

    with pytest.raises(ValueError, match="BACKTEST_DATE_RANGE_INVALID"):
        operations.freeze_backtest_request(
            row,
            {"from_date": "2025.01.01", "to_date": "2020.01.01"},
        )
    with pytest.raises(ValueError, match="BACKTEST_DATE_RANGE_INVALID"):
        operations.freeze_backtest_request(
            row,
            {"from_date": "2025.02.30", "to_date": "2025.03.01"},
        )

    for field, value in (
        ("symbol", "EURUSD.m"),
        ("relative_symbol", "GBPUSD.m"),
        ("period", "H1"),
        ("model", 0),
        ("deposit", 20000),
        ("leverage", 200),
    ):
        with pytest.raises(
            ValueError,
            match=f"BACKTEST_RETAINED_CONTRACT_OVERRIDE_FORBIDDEN:{field}",
        ):
            operations.freeze_backtest_request(row, {field: value})

    poisoned = dict(row)
    poisoned["source_request"] = {
        **row["source_request"],
        "symbol": "XAUUSD.m\nReport=escape",
    }
    with pytest.raises(
        ValueError,
        match="retained Main Symbol contains unsupported characters",
    ):
        operations.freeze_backtest_request(poisoned, {})

    monkeypatch.setattr(operations, "ROOT", root)
    monkeypatch.setattr(
        operations,
        "CHALLENGER_ARTIFACT_ROOT",
        root / "artifacts" / "strategy_challengers",
    )
    escaped = dict(row)
    escaped["bundle_path"] = "../outside"
    with pytest.raises(RuntimeError, match="CHALLENGER_BUNDLE_PATH_OUTSIDE_AUTHORITY"):
        operations._bundle_paths(escaped)



def test_restart_marks_running_backtest_unconfirmed_without_relaunch(
    tmp_path: Path,
) -> None:
    root, db, cid = fresh_schema6(tmp_path)
    migrate_m06(db)
    row = get_challenger(cid, path=db)
    assert row is not None

    prepared = create_backtest_record(
        backtest_id="BT-PREPARED-RESTART",
        challenger_id=cid,
        source_manifest_sha256=row["manifest_sha256"],
        request={"schema": "fixture"},
        ea_sha256=row["challenger_ea_sha256"],
        set_sha256=row["set_sha256"],
        evidence_path="evidence/m06/challenger_backtests/BT-PREPARED-RESTART",
        path=db,
    )
    running = create_backtest_record(
        backtest_id="BT-RUNNING-RESTART",
        challenger_id=cid,
        source_manifest_sha256=row["manifest_sha256"],
        request={"schema": "fixture"},
        ea_sha256=row["challenger_ea_sha256"],
        set_sha256=row["set_sha256"],
        evidence_path="evidence/m06/challenger_backtests/BT-RUNNING-RESTART",
        path=db,
    )
    update_backtest(running["backtest_id"], state="RUNNING", path=db)

    recovered = recover_incomplete_backtests(db)
    assert recovered == 2

    prepared_final = get_backtest(prepared["backtest_id"], path=db)
    assert prepared_final is not None
    assert prepared_final["state"] == "FAILED"
    assert (
        prepared_final["error"]
        == "BACKTEST_INTERRUPTED_BEFORE_MT5_LAUNCH_NO_RELAUNCH"
    )

    running_final = get_backtest(running["backtest_id"], path=db)
    assert running_final is not None
    assert running_final["state"] == "UNCONFIRMED"
    assert (
        running_final["error"]
        == "BACKTEST_INTERRUPTED_AFTER_MT5_LAUNCH_EXECUTION_UNKNOWN_NO_RELAUNCH"
    )
    with pytest.raises(RuntimeError, match="BACKTEST_STATE_TRANSITION_INVALID"):
        update_backtest(
            running["backtest_id"],
            state="RUNNING",
            path=db,
        )


def test_retirement_blocks_active_backtest_and_active_promotion_atomically(
    tmp_path: Path,
) -> None:
    root, db, cid = fresh_schema6(tmp_path)
    migrate_m06(db)
    row = get_challenger(cid, path=db)
    assert row is not None

    backtest = create_backtest_record(
        backtest_id="BT-ACTIVE-RETIRE-RACE",
        challenger_id=cid,
        source_manifest_sha256=row["manifest_sha256"],
        request={"schema": "fixture"},
        ea_sha256=row["challenger_ea_sha256"],
        set_sha256=row["set_sha256"],
        evidence_path="evidence/m06/challenger_backtests/BT-ACTIVE-RETIRE-RACE",
        path=db,
    )
    with pytest.raises(
        RuntimeError,
        match="CHALLENGER_RETIREMENT_BLOCKED_ACTIVE_BACKTEST",
    ):
        retire_registry_row(
            cid,
            retirement_id="RETIRE-BLOCK-BACKTEST",
            expected_manifest_sha256=row["manifest_sha256"],
            evidence_path="evidence/m06/challenger_retirements/RETIRE-BLOCK-BACKTEST",
            path=db,
        )
    assert get_challenger(cid, path=db)["status"] == "CHALLENGER"
    update_backtest(backtest["backtest_id"], state="FAILED", path=db)

    create_prepared_promotion(
        promotion_id="PROMOTE-M06-RETIRE-RACE",
        challenger_id=cid,
        previous_champion_id=None,
        expected_manifest_sha256=row["manifest_sha256"],
        before_state={"files": {}},
        recovery_path="state/recovery/m06-race",
        path=db,
    )
    with pytest.raises(
        RuntimeError,
        match="CHALLENGER_RETIREMENT_BLOCKED_ACTIVE_PROMOTION",
    ):
        retire_registry_row(
            cid,
            retirement_id="RETIRE-BLOCK-PROMOTION",
            expected_manifest_sha256=row["manifest_sha256"],
            evidence_path="evidence/m06/challenger_retirements/RETIRE-BLOCK-PROMOTION",
            path=db,
        )
    assert get_challenger(cid, path=db)["status"] == "CHALLENGER"


def test_retirement_wins_race_then_promotion_and_backtest_fail_closed(
    tmp_path: Path,
) -> None:
    root, db, cid = fresh_schema6(tmp_path)
    migrate_m06(db)
    row = get_challenger(cid, path=db)
    assert row is not None

    retired = retire_registry_row(
        cid,
        retirement_id="RETIRE-RACE-WINNER",
        expected_manifest_sha256=row["manifest_sha256"],
        evidence_path="evidence/m06/challenger_retirements/RETIRE-RACE-WINNER",
        path=db,
    )
    assert retired["challenger"]["status"] == "RETIRED"
    assert retired["retirement"]["state"] == "COMMITTED"

    with pytest.raises(RuntimeError, match="PROMOTION_CHALLENGER_NOT_ACTIVE"):
        create_prepared_promotion(
            promotion_id="PROMOTE-AFTER-RETIRE",
            challenger_id=cid,
            previous_champion_id=None,
            expected_manifest_sha256=row["manifest_sha256"],
            before_state={"files": {}},
            recovery_path="state/recovery/after-retire",
            path=db,
        )

    with pytest.raises(
        RuntimeError,
        match="CHALLENGER_BACKTEST_CHALLENGER_NOT_ACTIVE",
    ):
        create_backtest_record(
            backtest_id="BT-AFTER-RETIRE",
            challenger_id=cid,
            source_manifest_sha256=row["manifest_sha256"],
            request={"schema": "fixture"},
            ea_sha256=row["challenger_ea_sha256"],
            set_sha256=row["set_sha256"],
            evidence_path="evidence/m06/challenger_backtests/BT-AFTER-RETIRE",
            path=db,
        )


@pytest.mark.parametrize(
    ("bounds", "expected_count", "risk_present"),
    [
        (LEGACY_ABSOLUTE_BOUNDS, 16, False),
        (ABSOLUTE_BOUNDS, 17, True),
    ],
)
def test_challenger_detail_uses_exact_retained_parameter_universe(
    monkeypatch: pytest.MonkeyPatch,
    bounds: dict,
    expected_count: int,
    risk_present: bool,
) -> None:
    params = {
        name: (int(lo) if kind == "int" else float(lo))
        for name, (lo, _hi, _step, kind) in bounds.items()
    }
    baseline = {
        name: (int(lo) if kind == "int" else float(lo))
        for name, (lo, _hi, _step, kind) in ABSOLUTE_BOUNDS.items()
    }
    row = {
        "challenger_id": "STRAT-DETAIL-FIXTURE",
        "status": "CHALLENGER",
        "params": params,
    }
    monkeypatch.setattr(registry, "get_challenger", lambda *_a, **_k: row)
    monkeypatch.setattr(
        registry,
        "verify_challenger_bundle",
        lambda *_a, **_k: {"status": "VERIFIED"},
    )
    monkeypatch.setattr(
        registry,
        "read_ea_optimizer_defaults",
        lambda *_a, **_k: baseline,
    )

    detail = registry.challenger_detail("STRAT-DETAIL-FIXTURE")

    comparison = detail["parameter_comparison"]
    assert len(comparison) == expected_count
    names = {item["parameter"] for item in comparison}
    assert ("InpRiskPct" in names) is risk_present
    assert detail["params"] == params


@pytest.mark.parametrize(
    "mutate",
    [
        lambda params: params.pop("InpMinConsensus"),
        lambda params: params.update({"InpUnknown": 1.0}),
        lambda params: (
            params.pop("InpMinConsensus"),
            params.update({"InpRiskPct": 1.0}),
        ),
    ],
)
def test_challenger_detail_rejects_malformed_parameter_universe(
    monkeypatch: pytest.MonkeyPatch,
    mutate,
) -> None:
    params = {
        name: (int(lo) if kind == "int" else float(lo))
        for name, (lo, _hi, _step, kind) in LEGACY_ABSOLUTE_BOUNDS.items()
    }
    mutate(params)
    row = {
        "challenger_id": "STRAT-DETAIL-MALFORMED",
        "status": "CHALLENGER",
        "params": params,
    }
    monkeypatch.setattr(registry, "get_challenger", lambda *_a, **_k: row)
    monkeypatch.setattr(
        registry,
        "verify_challenger_bundle",
        lambda *_a, **_k: {"status": "VERIFIED"},
    )
    monkeypatch.setattr(
        registry,
        "read_ea_optimizer_defaults",
        lambda *_a, **_k: {
            name: (int(lo) if kind == "int" else float(lo))
            for name, (lo, _hi, _step, kind) in ABSOLUTE_BOUNDS.items()
        },
    )

    with pytest.raises(
        RuntimeError,
        match="CHALLENGER_PARAMETER_UNIVERSE_INVALID",
    ):
        registry.challenger_detail("STRAT-DETAIL-MALFORMED")
