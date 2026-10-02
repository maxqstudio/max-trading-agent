from __future__ import annotations

import csv
import json
import inspect
from copy import deepcopy
from pathlib import Path

import pytest

import max_backend.artifact_control as artifact_control
import max_backend.optimizer_candidates as optimizer_candidates
import max_backend.backtest_control as backtest_control
import max_backend.challenger_selection as selection
import max_backend.challenger_operations as challenger_operations
import max_backend.workflow_contract as workflow_contract
import max_backend.optimizer_core as core
import max_backend.optimizer_runtime as runtime
import max_backend.optimizer_worker as worker
from max_backend.backtest_report import parse_mt5_backtest_report
from max_backend.challenger_bundle import _ea_input_number
from max_backend.challenger_operations_store import (
    create_backtest_record,
    get_backtest,
    update_backtest,
)
from max_backend.challenger_store import get_challenger
from max_backend.db import connect, ensure_baseline_registered, initialize_database
from max_backend.workflow_store import get_batch, migrate_current
from max_backend.optimizer_candidates import (
    qualification_check,
    qualified_candidate,
    qualified_candidates_page,
    revalidate_candidate_for_registration,
)
from max_backend.optimizer_core import (
    ABSOLUTE_BOUNDS,
    OPTIMIZER_FITNESS_SCHEMA,
    OPTIMIZER_METRICS_SCHEMA_V2,
    apply_frozen_gates,
    eligibility_audit,
    freeze_request,
    optimizer_fitness_value,
    parse_optimization_xml,
    sha256_file,
)
from max_backend.optimizer_jobs import job_detail
from max_backend.optimizer_runtime import commit_round_evidence
from max_backend.optimizer_store import create_job, update_job, upsert_round
from max_backend.path_safety import assert_owned_path, remove_owned_path
from max_backend.workflow_contract import (
    OPTIMIZER_REQUEST_SCHEMA_CURRENT,
    OPTIMIZER_TERMINAL_QUALIFIED_POOL,
    OPTIMIZER_WORKFLOW_OWNER_EXPLICIT,
    ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE,
)


def fake_mt5(tmp_path: Path) -> dict:
    return {
        "status": "READY_EXECUTABLE_AND_DATA_ROOT",
        "reason": "READY",
        "terminal": str(tmp_path / "terminal64.exe"),
        "metaeditor": str(tmp_path / "metaeditor64.exe"),
        "data_root": str(tmp_path / "mt5"),
    }


def raw_request() -> dict:
    return {
        "symbol": "XAUUSD.m",
        "relative_symbol": "EURUSD.m",
        "period": "H1",
        "from_date": "2026.01.01",
        "to_date": "2026.02.01",
        "deposit": 10000,
        "leverage": 100,
        "model": 1,
        "optimization": 2,
        "max_rounds": 3,
        "optimize_params": ["InpEntryThreshold"],
        "search_space": deepcopy(core.DEFAULT_SPACE),
        "kpi": {
            "min_profit_factor": 1.0,
            "min_recovery_factor": 0.0,
            "min_expectancy_r": 0.0,
            "min_weighted_r": 0.0,
            "base_h1_trades_per_month": 20,
        },
        "scientist_assist": False,
    }


def params_for(index: int) -> dict:
    params = core.read_ea_optimizer_defaults()
    params["InpEntryThreshold"] = round(0.20 + index * 0.005, 6)
    return params


def pass_row(
    pass_no: int,
    *,
    pf: float = 1.4,
    rf: float = 0.8,
    mean_r: float = 0.20,
    weighted_r: float = 0.10,
    profit: float = 10.0,
    trades: int = 40,
) -> dict:
    if weighted_r < 0 and profit > 0:
        profit = -abs(profit)
    return {
        "pass": pass_no,
        "pf": pf,
        "rf": rf,
        "mean_r": mean_r,
        "weighted_r": weighted_r,
        "profit": profit,
        "trades": trades,
        "params": params_for(pass_no),
    }


def write_xml(path: Path, rows: list[dict], *, alpha: float = 0.5) -> None:
    headers = [
        "Pass",
        "Profit Factor",
        "Recovery Factor",
        "Custom",
        "Profit",
        "Trades",
        *ABSOLUTE_BOUNDS.keys(),
    ]

    def xml_row(values: list[object]) -> str:
        return "<Row>" + "".join(
            f'<Cell><Data ss:Type="String">{value}</Data></Cell>'
            for value in values
        ) + "</Row>"

    table = [xml_row(list(headers))]
    for item in rows:
        table.append(
            xml_row(
                [
                    item["pass"],
                    item["pf"],
                    item["rf"],
                    item.get(
                        "custom_fitness",
                        optimizer_fitness_value(item["mean_r"], item["trades"], alpha),
                    ),
                    item["profit"],
                    item["trades"],
                    *[item["params"][name] for name in ABSOLUTE_BOUNDS],
                ]
            )
        )
    path.write_text(
        '<?xml version="1.0"?>'
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
        'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
        '<DocumentProperties><Title>'
        'Max_MTF XAUUSD.m,H1 2026.01.01-2026.02.01'
        '</Title></DocumentProperties>'
        '<Worksheet><Table>'
        + "".join(table)
        + "</Table></Worksheet></Workbook>",
        encoding="utf-8",
    )


def write_metrics(
    path: Path,
    rows: list[dict],
    *,
    nonce: int,
    alpha: float = 0.5,
) -> None:
    fields = [
        "evidence_schema",
        "fitness_schema",
        "frame_pass_id",
        "frame_inputs",
        "custom_fitness",
        "trade_exponent_alpha",
        "mean_expectancy_r",
        "weighted_r",
        "mt5_trades",
        "r_accounted_trades",
        "sum_r",
        "sum_net",
        "sum_initial_risk",
        "accounting_errors",
        "run_nonce",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, item in enumerate(rows, start=1):
            weighted = float(item["weighted_r"])
            profit = float(item["profit"])
            if weighted == 0:
                risk = 100.0
                profit = 0.0
            else:
                risk = profit / weighted
            trades = int(item["trades"])
            mean_r = float(item["mean_r"])
            writer.writerow(
                {
                    "evidence_schema": OPTIMIZER_METRICS_SCHEMA_V2,
                    "fitness_schema": OPTIMIZER_FITNESS_SCHEMA,
                    "frame_pass_id": 90_000_000_000 + index,
                    "frame_inputs": "|".join(
                        f"{name}={item['params'][name]}" for name in ABSOLUTE_BOUNDS
                    ),
                    "custom_fitness": item.get(
                        "custom_fitness",
                        optimizer_fitness_value(mean_r, trades, alpha),
                    ),
                    "trade_exponent_alpha": alpha,
                    "mean_expectancy_r": mean_r,
                    "weighted_r": weighted,
                    "mt5_trades": trades,
                    "r_accounted_trades": trades,
                    "sum_r": mean_r * trades,
                    "sum_net": profit,
                    "sum_initial_risk": risk,
                    "accounting_errors": 0,
                    "run_nonce": nonce,
                }
            )


def build_optimizer_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict],
) -> tuple[Path, dict, dict, Path]:
    db = tmp_path / "state" / "max.db"
    initialize_database(db)
    ensure_baseline_registered(db)
    migrate_current(db)

    terminal = tmp_path / "terminal64.exe"
    editor = tmp_path / "metaeditor64.exe"
    terminal.write_bytes(b"terminal")
    editor.write_bytes(b"metaeditor")
    (tmp_path / "mt5").mkdir()
    monkeypatch.setattr(core, "detect_mt5", lambda: fake_mt5(tmp_path))
    monkeypatch.setattr(
        core,
        "build_resource_preflight",
        lambda *_args, **_kwargs: {"schema": "MAX_OPTIMIZER_RESOURCE_POLICY_V1", "status": "SAFE", "resolved_max_local_agents": 1},
    )
    request = freeze_request(raw_request())
    assert request["schema"] == OPTIMIZER_REQUEST_SCHEMA_CURRENT

    evidence_root = tmp_path / "artifacts" / "optimizer"
    monkeypatch.setattr(runtime, "OPTIMIZER_EVIDENCE_ROOT", evidence_root)
    job = create_job(request, evidence_root=evidence_root, path=db)
    job_root = Path(job["evidence_dir"])
    job_root.mkdir(parents=True, exist_ok=True)
    (job_root / "request.json").write_text(
        json.dumps(request, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report = tmp_path / "source_report.xml"
    sidecar = tmp_path / "source_metrics.csv"
    nonce = 424242
    alpha = float(request["optimizer_fitness"]["trade_exponent_alpha"])
    write_xml(report, rows, alpha=alpha)
    write_metrics(sidecar, rows, nonce=nonce, alpha=alpha)
    parsed = parse_optimization_xml(
        report,
        round_no=1,
        metrics_path=sidecar,
        expected_nonce=nonce,
        fixed_param_values=request["fixed_param_values"],
        optimize_params=request["optimize_params"],
        optimizer_fitness=request["optimizer_fitness"],
    )
    apply_frozen_gates(parsed, request)
    audit = {
        **eligibility_audit(parsed),
        "winner_pass": None,
        "selection_authority": "OWNER_EXPLICIT_QUALIFIED_CANDIDATE_SELECTION",
    }
    evidence = commit_round_evidence(
        job_id=job["job_id"],
        round_no=1,
        report=report,
        metrics_path=sidecar,
        audit=audit,
        passes_payload=[item.payload() for item in parsed],
        report_selection_mode="IDENTITY_MATCH",
    )
    upsert_round(
        job["job_id"],
        1,
        phase="PARSED",
        state={
            "optimizer_run_nonce": nonce,
            "report_selection_mode": "IDENTITY_MATCH",
            "search_space": request["search_space"],
        },
        report_path=evidence["report_path"],
        report_sha256=evidence["report_sha256"],
        sidecar_path=evidence["sidecar_path"],
        sidecar_sha256=evidence["sidecar_sha256"],
        parsed_passes=audit["parsed_passes"],
        eligible_passes=audit["eligible_passes"],
        winner_pass=None,
        path=db,
    )
    update_job(
        job["job_id"],
        status=OPTIMIZER_TERMINAL_QUALIFIED_POOL,
        active=False,
        current_round=1,
        terminal_result=OPTIMIZER_TERMINAL_QUALIFIED_POOL,
        mark_completed=True,
        path=db,
    )
    return db, job, request, evidence_root


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"trades": 0}, "TRADES_BELOW_MINIMUM"),
        ({"profit_factor": 0.5}, "PROFIT_FACTOR_BELOW_MINIMUM"),
        ({"recovery_factor": -0.1}, "RECOVERY_FACTOR_BELOW_MINIMUM"),
        ({"expectancy_r": -0.01}, "MEAN_R_BELOW_MINIMUM"),
        ({"weighted_r": -0.01}, "WEIGHTED_R_BELOW_MINIMUM"),
    ],
)
def test_qualified_candidate_requires_every_frozen_gate(
    mutation: dict,
    reason: str,
) -> None:
    request = {
        "trade_sample": {"minimum_trades": 20},
        "kpi": {
            "min_profit_factor": 1.0,
            "min_recovery_factor": 0.0,
            "min_expectancy_r": 0.0,
            "min_weighted_r": 0.0,
        },
    }
    payload = {
        "pass_no": 1,
        "profit_factor": 1.4,
        "recovery_factor": 0.8,
        "expectancy_r": 0.2,
        "weighted_r": 0.1,
        "trades": 40,
        "params": params_for(1),
        **mutation,
    }
    qualified, reasons, _normalized = qualification_check(payload, request)
    assert qualified is False
    assert reason in reasons


def test_qualified_endpoint_uses_projection_and_strict_mutation_revalidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        pass_row(
            index,
            mean_r=0.10 + index / 1000,
            weighted_r=0.05 + index / 2000,
        )
        for index in range(1, 31)
    ]
    rows.extend(
        [
            pass_row(31, trades=1),
            pass_row(32, pf=0.5),
            pass_row(33, rf=-0.1),
            pass_row(34, mean_r=-0.1),
            pass_row(35, weighted_r=-0.1),
        ]
    )
    db, job, _request, _root = build_optimizer_fixture(
        tmp_path, monkeypatch, rows
    )

    page = qualified_candidates_page(
        job["job_id"],
        page=1,
        page_size=25,
        path=db,
    )
    assert page["raw_count"] == 35
    assert page["qualified_count"] == 30
    assert page["rejected_count"] == 5
    assert page["total"] == 30
    assert page["pages"] == 2
    assert len(page["items"]) == 25
    assert page["sort"] == "mean_r"
    assert page["order"] == "desc"
    assert page["items"][0]["mean_r"] > page["items"][-1]["mean_r"]

    second = qualified_candidates_page(
        job["job_id"],
        sort="pass",
        order="asc",
        page=2,
        page_size=25,
        path=db,
    )
    assert [item["pass"] for item in second["items"]] == [26, 27, 28, 29, 30]

    filtered = qualified_candidates_page(
        job["job_id"],
        query="30",
        page_size=25,
        path=db,
    )
    assert any(item["pass"] == 30 for item in filtered["items"])

    with pytest.raises(ValueError, match="page_size"):
        qualified_candidates_page(job["job_id"], page_size=200, path=db)

    # Read pages from the persisted projection; mutation still checks the
    # retained canonical report and fails closed after tampering.
    report_path = Path(page["items"][0]["report_path"])
    report_path.write_text(report_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with monkeypatch.context() as no_evidence_read:
        no_evidence_read.setattr(
            optimizer_candidates,
            "_verify_round_files",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("candidate list reread canonical evidence")
            ),
        )
        replay = qualified_candidates_page(job["job_id"], path=db)
    assert replay["total"] == 30
    with pytest.raises(RuntimeError, match="ROUND_BUNDLE_FILE_INVALID|REPORT_HASH_MISMATCH"):
        revalidate_candidate_for_registration(
            job["job_id"],
            int(page["items"][0]["round"]),
            int(page["items"][0]["pass"]),
            path=db,
        )


def test_new_optimizer_terminal_never_auto_registers_challenger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = {
        "job_id": "20260924_080000_deadbeef",
        "request": {
            "schema": OPTIMIZER_REQUEST_SCHEMA_CURRENT,
            "optimizer_result_workflow": OPTIMIZER_WORKFLOW_OWNER_EXPLICIT,
            "search_space": {},
            "max_rounds": 3,
        },
        "current_round": 1,
    }
    updates: list[dict] = []
    monkeypatch.setattr(worker, "get_job", lambda *_a, **_k: job)
    monkeypatch.setattr(worker, "_load_committed_winner", lambda *_a, **_k: None)
    monkeypatch.setattr(worker, "get_round", lambda *_a, **_k: None)
    monkeypatch.setattr(
        worker,
        "execute_round",
        lambda *_a, **_k: (
            [],
            {"eligible_passes": 3},
            object(),
        ),
    )
    monkeypatch.setattr(
        worker,
        "update_job",
        lambda _job_id, **kwargs: updates.append(kwargs) or {},
    )
    monkeypatch.setattr(
        worker,
        "_write_winner",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("winner evidence must not be written for V5 owner-selection workflow")
        ),
    )
    monkeypatch.setattr(
        worker,
        "_register_committed_winner",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("automatic Challenger registration forbidden")
        ),
    )

    assert worker.run_job(job["job_id"]) == 0
    assert updates[-1]["status"] == OPTIMIZER_TERMINAL_QUALIFIED_POOL
    assert updates[-1]["terminal_result"] == OPTIMIZER_TERMINAL_QUALIFIED_POOL
    assert updates[-1]["active"] is False


def test_owner_selection_creates_independent_challengers_and_retry_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        pass_row(1, mean_r=0.31, weighted_r=0.11),
        pass_row(2, mean_r=0.29, weighted_r=0.12),
        pass_row(3, mean_r=0.27, weighted_r=0.13),
    ]
    db, job, _request, _root = build_optimizer_fixture(
        tmp_path, monkeypatch, rows
    )
    artifact_root = tmp_path / "artifacts" / "challengers"

    one = selection.create_selected_challengers(
        job["job_id"],
        [{"round": 1, "pass": 1}],
        path=db,
        artifact_root=artifact_root,
        project_root=tmp_path,
    )
    assert one["state"] == "COMMITTED"
    assert len(one["challengers"]) == 1
    first = one["challengers"][0]
    assert first["role_origin"] == ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE
    assert first["params"]["InpRiskPct"] == pytest.approx(0.5)
    assert first["source_request"]["fixed_execution_authority"]["InpMaxDailyLossPct"] == 5.0
    assert "InpMaxDailyLossPct" not in ABSOLUTE_BOUNDS
    bundle = tmp_path / first["bundle_path"]
    challenger_set = (
        bundle / f"Max_Challenger_{first['challenger_id']}.set"
    ).read_text(encoding="utf-8")
    challenger_ea = (
        bundle / f"Max_Challenger_{first['challenger_id']}.mq5"
    ).read_text(encoding="utf-8")
    assert "InpRiskPct=0.5||0.5||0||0.5||N" in challenger_set
    assert "InpMaxDailyLossPct=5" in challenger_set
    assert _ea_input_number(challenger_ea, "InpRiskPct") == pytest.approx(0.5)
    assert _ea_input_number(
        challenger_ea,
        "InpMaxDailyLossPct",
    ) == pytest.approx(5.0)

    two = selection.create_selected_challengers(
        job["job_id"],
        [{"round": 1, "pass": 2}, {"round": 1, "pass": 3}],
        path=db,
        artifact_root=artifact_root,
        project_root=tmp_path,
    )
    assert two["state"] == "COMMITTED"
    assert len(two["challengers"]) == 2
    with connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM strategy_challengers"
        ).fetchone()["n"] == 3


def test_batch_prevalidation_failure_registers_zero_and_recovery_retry_no_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        pass_row(1, mean_r=0.31),
        pass_row(2, mean_r=0.29),
    ]
    db, job, _request, _root = build_optimizer_fixture(
        tmp_path, monkeypatch, rows
    )
    artifact_root = tmp_path / "artifacts" / "challengers"

    original_revalidate = selection.revalidate_candidate_for_registration
    calls = {"n": 0}

    def fail_second(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("INJECTED_PREVALIDATION_FAILURE")
        return original_revalidate(*args, **kwargs)

    monkeypatch.setattr(
        selection,
        "revalidate_candidate_for_registration",
        fail_second,
    )
    with pytest.raises(RuntimeError, match="INJECTED_PREVALIDATION_FAILURE"):
        selection.create_selected_challengers(
            job["job_id"],
            [{"round": 1, "pass": 1}, {"round": 1, "pass": 2}],
            path=db,
            artifact_root=artifact_root,
            project_root=tmp_path,
        )
    with connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM strategy_challengers"
        ).fetchone()["n"] == 0

    monkeypatch.setattr(
        selection,
        "revalidate_candidate_for_registration",
        original_revalidate,
    )
    original_insert = selection.insert_challenger_batch_rows
    mutation_calls = {"n": 0}

    def fail_first_db_commit(*args, **kwargs):
        mutation_calls["n"] += 1
        if mutation_calls["n"] == 1:
            raise RuntimeError("INJECTED_DB_COMMIT_FAILURE")
        return original_insert(*args, **kwargs)

    monkeypatch.setattr(
        selection,
        "insert_challenger_batch_rows",
        fail_first_db_commit,
    )
    with pytest.raises(RuntimeError, match="INJECTED_DB_COMMIT_FAILURE"):
        selection.create_selected_challengers(
            job["job_id"],
            [{"round": 1, "pass": 1}, {"round": 1, "pass": 2}],
            path=db,
            artifact_root=artifact_root,
            project_root=tmp_path,
        )
    with connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM strategy_challengers"
        ).fetchone()["n"] == 0
        batch = conn.execute(
            "SELECT state FROM strategy_challenger_batches"
        ).fetchone()
        assert batch["state"] == "RECOVERY_REQUIRED"
    assert len([item for item in artifact_root.iterdir() if item.is_dir() and not item.name.startswith(".")]) == 2

    monkeypatch.setattr(
        selection,
        "insert_challenger_batch_rows",
        original_insert,
    )
    retry = selection.create_selected_challengers(
        job["job_id"],
        [{"round": 1, "pass": 1}, {"round": 1, "pass": 2}],
        path=db,
        artifact_root=artifact_root,
        project_root=tmp_path,
    )
    assert retry["state"] == "COMMITTED"
    again = selection.create_selected_challengers(
        job["job_id"],
        [{"round": 1, "pass": 1}, {"round": 1, "pass": 2}],
        path=db,
        artifact_root=artifact_root,
        project_root=tmp_path,
    )
    assert again["state"] == "COMMITTED"
    with connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM strategy_challengers"
        ).fetchone()["n"] == 2


def test_historical_optimizer_job_keeps_legacy_pass_evidence(
    tmp_path: Path,
) -> None:
    db = tmp_path / "legacy.db"
    initialize_database(db)
    ensure_baseline_registered(db)
    migrate_current(db)
    evidence_root = tmp_path / "legacy_optimizer"
    request = {
        "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V3",
        "max_rounds": 1,
    }
    job = create_job(request, evidence_root=evidence_root, path=db)
    round_dir = evidence_root / job["job_id"] / "round_01"
    round_dir.mkdir(parents=True, exist_ok=True)
    (round_dir / "passes.json").write_text(
        json.dumps({"passes": [{"pass_no": 7, "eligibility": "ELIGIBLE"}]}),
        encoding="utf-8",
    )
    upsert_round(
        job["job_id"],
        1,
        phase="PARSED",
        state={},
        parsed_passes=1,
        eligible_passes=1,
        winner_pass=7,
        path=db,
    )
    detail = job_detail(
        job["job_id"],
        path=db,
        evidence_root=evidence_root,
    )
    assert detail is not None
    assert detail["rounds"][0]["passes"][0]["pass_no"] == 7


def valid_backtest_html() -> str:
    return """<html><body><table>
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
    </table></body></html>"""


def test_mt5_backtest_report_parser_and_hash_fail_closed(tmp_path: Path) -> None:
    report = tmp_path / "report.htm"
    report.write_text(valid_backtest_html(), encoding="utf-8")
    parsed = parse_mt5_backtest_report(
        report,
        expected_sha256=sha256_file(report),
    )
    metrics = parsed["metrics"]
    assert metrics["total_net_profit"] == 125.5
    assert metrics["profit_factor"] == pytest.approx(2.096)
    assert metrics["recovery_factor"] == pytest.approx(1.75)
    assert metrics["sharpe_ratio"] == pytest.approx(1.31)
    assert metrics["total_trades"] == 25
    assert metrics["profit_trades_count"] == 15
    assert metrics["profit_trades_pct"] == 60.0
    assert metrics["balance_drawdown_maximal_pct"] == pytest.approx(0.72)
    assert metrics["equity_drawdown_relative_amount"] == pytest.approx(81.2)

    with pytest.raises(RuntimeError, match="HASH_MISMATCH"):
        parse_mt5_backtest_report(report, expected_sha256="0" * 64)

    malformed = tmp_path / "malformed.htm"
    malformed.write_text("<html><table><tr><td>Profit Factor:</td><td>2</td></tr></table></html>", encoding="utf-8")
    with pytest.raises(RuntimeError, match="REQUIRED_METRICS_MISSING"):
        parse_mt5_backtest_report(malformed)

def create_one_challenger_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict, dict, Path]:
    db, job, _request, _root = build_optimizer_fixture(
        tmp_path,
        monkeypatch,
        [pass_row(1, mean_r=0.31, weighted_r=0.11)],
    )
    challenger_root = tmp_path / "artifacts" / "challengers"
    result = selection.create_selected_challengers(
        job["job_id"],
        [{"round": 1, "pass": 1}],
        path=db,
        artifact_root=challenger_root,
        project_root=tmp_path,
    )
    challenger = result["challengers"][0]
    return db, job, challenger, challenger_root


def create_completed_backtest_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict, dict, Path, dict[str, Path]]:
    db, _job, challenger, challenger_root = create_one_challenger_fixture(
        tmp_path,
        monkeypatch,
    )
    current_backtest_root = tmp_path / "artifacts" / "backtests"
    legacy_root = tmp_path / "legacy_backtests"
    monkeypatch.setattr(backtest_control, "ROOT", tmp_path)
    monkeypatch.setattr(
        backtest_control,
        "BACKTEST_ARTIFACT_ROOT",
        current_backtest_root,
    )
    monkeypatch.setattr(
        backtest_control,
        "LEGACY_M06_BACKTEST_EVIDENCE_ROOT",
        legacy_root,
    )
    monkeypatch.setattr(
        backtest_control,
        "CHALLENGER_ARTIFACT_ROOT",
        challenger_root,
    )

    backtest_id = "BT-20260924-081500-deadbeef"
    evidence = current_backtest_root / backtest_id
    evidence.mkdir(parents=True)
    retained_report = evidence / "backtest_report.htm"
    retained_report.write_text(valid_backtest_html(), encoding="utf-8")

    data_root = tmp_path / "mt5"
    expert_root = (
        data_root / "MQL5" / "Experts" / "MaxMTF" / "ChallengerBacktests"
    )
    expert_dir = expert_root / backtest_id
    expert_dir.mkdir(parents=True, exist_ok=True)
    (expert_dir / "Max_Challenger.mq5").write_text("source", encoding="utf-8")
    tester_root = data_root / "MQL5" / "Profiles" / "Tester"
    tester_root.mkdir(parents=True, exist_ok=True)
    tester_set = tester_root / f"MaxMTF_Backtest_{backtest_id}.set"
    tester_set.write_text("set", encoding="utf-8")
    report_root = data_root / "reports"
    report_root.mkdir(parents=True, exist_ok=True)
    runtime_report = report_root / f"MaxMTF_Backtest_{backtest_id}.htm"
    runtime_report.write_text(valid_backtest_html(), encoding="utf-8")

    request = deepcopy(challenger["source_request"])
    request["mt5"] = {
        **dict(request.get("mt5") or {}),
        "data_root": str(data_root),
    }
    record = create_backtest_record(
        backtest_id=backtest_id,
        challenger_id=challenger["challenger_id"],
        source_manifest_sha256=str(challenger["manifest_sha256"]),
        request=request,
        ea_sha256=str(challenger["challenger_ea_sha256"]),
        set_sha256=str(challenger["set_sha256"]),
        evidence_path=evidence.relative_to(tmp_path).as_posix(),
        path=db,
    )
    update_backtest(backtest_id, state="RUNNING", path=db)
    parsed = parse_mt5_backtest_report(retained_report)
    record = update_backtest(
        backtest_id,
        state="COMPLETED",
        ex5_sha256="e" * 64,
        report_path=retained_report.relative_to(tmp_path).as_posix(),
        report_sha256=sha256_file(retained_report),
        result={
            "schema": "MAX_REBUILD_CHALLENGER_BACKTEST_RESULT_V2",
            "metrics": parsed["metrics"],
            "metrics_schema": parsed["schema"],
            "available_metrics": parsed["available_metrics"],
            "runtime": {
                "expert_dir": str(expert_dir),
                "tester_set": str(tester_set),
                "source_report": str(runtime_report),
            },
        },
        path=db,
    )
    return db, challenger, record, evidence, {
        "expert_dir": expert_dir,
        "tester_set": tester_set,
        "runtime_report": runtime_report,
        "retained_report": retained_report,
    }


def test_runtime_cleanup_preserves_result_then_backtest_delete_is_physical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, challenger, record, evidence, runtime_paths = create_completed_backtest_fixture(
        tmp_path,
        monkeypatch,
    )
    before = get_backtest(record["backtest_id"], path=db)
    assert before is not None
    assert before["result"]["metrics"]["total_net_profit"] == 125.5

    blocker = backtest_control.challenger_delete_preflight(
        challenger["challenger_id"],
        path=db,
    )
    assert blocker["deletable"] is False
    assert "BACKTEST_HISTORY_EXISTS_DELETE_BACKTESTS_FIRST" in blocker["blockers"]

    cleaned = backtest_control.clean_backtest_runtime(
        record["backtest_id"],
        path=db,
    )
    assert cleaned["runtime_status"] == "CLEANED"
    assert runtime_paths["expert_dir"].exists() is False
    assert runtime_paths["tester_set"].exists() is False
    assert runtime_paths["runtime_report"].exists() is False
    assert runtime_paths["retained_report"].is_file()
    after_clean = get_backtest(record["backtest_id"], path=db)
    assert after_clean is not None
    assert after_clean["result"]["metrics"]["profit_factor"] == pytest.approx(2.096)

    deleted = backtest_control.delete_backtest(
        record["backtest_id"],
        confirmed=True,
        path=db,
    )
    assert deleted["status"] == "DELETED"
    assert get_backtest(record["backtest_id"], path=db) is None
    assert evidence.exists() is False

    now = backtest_control.challenger_delete_preflight(
        challenger["challenger_id"],
        path=db,
    )
    assert now["deletable"] is True
    challenger_deleted = backtest_control.delete_challenger(
        challenger["challenger_id"],
        confirmed=True,
        path=db,
    )
    assert challenger_deleted["status"] == "DELETED"
    assert get_challenger(challenger["challenger_id"], path=db) is None




def test_failed_backtest_without_report_can_clean_and_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _job, challenger, challenger_root = create_one_challenger_fixture(
        tmp_path,
        monkeypatch,
    )
    current_backtest_root = tmp_path / "artifacts" / "backtests"
    legacy_root = tmp_path / "legacy_backtests"
    monkeypatch.setattr(backtest_control, "ROOT", tmp_path)
    monkeypatch.setattr(
        backtest_control,
        "BACKTEST_ARTIFACT_ROOT",
        current_backtest_root,
    )
    monkeypatch.setattr(
        backtest_control,
        "LEGACY_M06_BACKTEST_EVIDENCE_ROOT",
        legacy_root,
    )
    monkeypatch.setattr(
        backtest_control,
        "CHALLENGER_ARTIFACT_ROOT",
        challenger_root,
    )

    backtest_id = "BT-20260924-081501-failed00"
    evidence = current_backtest_root / backtest_id
    evidence.mkdir(parents=True)
    (evidence / "request.json").write_text("{}", encoding="utf-8")

    data_root = tmp_path / "mt5"
    expert_dir = (
        data_root
        / "MQL5"
        / "Experts"
        / "MaxMTF"
        / "ChallengerBacktests"
        / backtest_id
    )
    expert_dir.mkdir(parents=True, exist_ok=True)
    (expert_dir / "failed.mq5").write_text("source", encoding="utf-8")
    tester_set = (
        data_root
        / "MQL5"
        / "Profiles"
        / "Tester"
        / f"MaxMTF_Backtest_{backtest_id}.set"
    )
    tester_set.parent.mkdir(parents=True, exist_ok=True)
    tester_set.write_text("set", encoding="utf-8")

    request = deepcopy(challenger["source_request"])
    request["mt5"] = {
        **dict(request.get("mt5") or {}),
        "data_root": str(data_root),
    }
    create_backtest_record(
        backtest_id=backtest_id,
        challenger_id=challenger["challenger_id"],
        source_manifest_sha256=str(challenger["manifest_sha256"]),
        request=request,
        ea_sha256=str(challenger["challenger_ea_sha256"]),
        set_sha256=str(challenger["set_sha256"]),
        evidence_path=evidence.relative_to(tmp_path).as_posix(),
        path=db,
    )
    update_backtest(backtest_id, state="RUNNING", path=db)
    update_backtest(
        backtest_id,
        state="FAILED",
        error="MT5_TERMINAL_ALREADY_RUNNING",
        path=db,
    )

    cleaned = backtest_control.clean_backtest_runtime(backtest_id, path=db)
    assert cleaned["runtime_status"] == "CLEANED"
    assert cleaned["retained_report"] is None
    assert expert_dir.exists() is False
    assert tester_set.exists() is False

    deleted = backtest_control.delete_backtest(
        backtest_id,
        confirmed=True,
        path=db,
    )
    assert deleted["status"] == "DELETED"
    assert get_backtest(backtest_id, path=db) is None
    assert evidence.exists() is False


def test_path_safety_rejects_traversal_foreign_and_symlink_escape(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    owned = root / "file.txt"
    owned.write_text("owned", encoding="utf-8")
    assert assert_owned_path(owned.resolve(), roots=[root]) == owned.resolve()

    traversal = root / ".." / "foreign.txt"
    with pytest.raises(RuntimeError, match="TRAVERSAL"):
        assert_owned_path(traversal, roots=[root])

    foreign = tmp_path / "foreign.txt"
    foreign.write_text("foreign", encoding="utf-8")
    with pytest.raises(RuntimeError, match="OUTSIDE_ROOT"):
        assert_owned_path(foreign.resolve(), roots=[root])

    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable on this Windows environment")
    with pytest.raises(RuntimeError, match="ESCAPE|REPARSE"):
        assert_owned_path((link / "x.txt").absolute(), roots=[root])

    removed = remove_owned_path(owned.resolve(), roots=[root])
    assert removed == len("owned")
    assert not owned.exists()
    assert foreign.exists()


def test_orphan_runtime_reconciliation_registers_but_does_not_auto_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "state" / "max.db"
    initialize_database(db)
    ensure_baseline_registered(db)
    migrate_current(db)
    data_root = tmp_path / "mt5data"
    orphan = (
        data_root
        / "MQL5"
        / "Experts"
        / "MaxMTF"
        / "ChallengerBacktests"
        / "BT-ORPHAN-001"
    )
    orphan.mkdir(parents=True)
    (orphan / "orphan.ex5").write_bytes(b"orphan")

    monkeypatch.setattr(
        artifact_control,
        "detect_mt5",
        lambda: {
            "status": "READY_EXECUTABLE_AND_DATA_ROOT",
            "reason": "READY",
            "terminal": str(tmp_path / "terminal64.exe"),
            "metaeditor": str(tmp_path / "metaeditor64.exe"),
            "data_root": str(data_root),
        },
    )
    status = artifact_control._reconcile_runtime(db)
    assert status["status"] == "READY"
    assert orphan.is_dir()
    with connect(db) as conn:
        row = conn.execute(
            """
            SELECT artifact_type,status,retention_class,deletable,cleanable
            FROM artifact_registry
            WHERE owner_type='ORPHAN_RUNTIME'
            """
        ).fetchone()
    assert row is not None
    assert row["artifact_type"] == "ORPHAN_RUNTIME"
    assert row["status"] == "ORPHAN_RUNTIME"
    assert row["retention_class"] == "TEMPORARY_RUNTIME"
    assert row["deletable"] == 1
    assert row["cleanable"] == 1


def test_artifact_registry_ownership_and_bulk_blocking(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state" / "max.db"
    initialize_database(db)
    ensure_baseline_registered(db)
    migrate_current(db)
    generated = tmp_path / "generated.json"
    generated.write_text('{"x":1}', encoding="utf-8")
    active = tmp_path / "authority.json"
    active.write_text('{"authority":true}', encoding="utf-8")

    user_item = artifact_control.register_artifact(
        artifact_type="TEST_GENERATED",
        producer="TEST",
        owner_type="TEST_OWNER",
        owner_id="U1",
        canonical_path=generated,
        retention_class="USER_GENERATED",
        deletable=True,
        path=db,
    )
    authority_item = artifact_control.register_artifact(
        artifact_type="TEST_AUTHORITY",
        producer="TEST",
        owner_type="TEST_OWNER",
        owner_id="A1",
        canonical_path=active,
        retention_class="ACTIVE_AUTHORITY",
        in_use=True,
        deletable=False,
        path=db,
    )
    user_read = artifact_control.get_artifact(user_item["artifact_id"], path=db)
    assert user_read is not None
    assert user_read["owner_id"] == "U1"
    assert user_read["sha256"] == sha256_file(generated)
    assert user_read["size_bytes"] == generated.stat().st_size

    # Isolate preflight from project/runtime reconciliation; this test targets
    # registry dependency semantics only.
    original_reconcile = artifact_control.reconcile_artifacts
    artifact_control.reconcile_artifacts = lambda **_kwargs: {"status": "READY", "runtime": {}}
    try:
        preflight = artifact_control.artifact_preflight(
            [user_item["artifact_id"], authority_item["artifact_id"]],
            path=db,
        )
    finally:
        artifact_control.reconcile_artifacts = original_reconcile
    assert preflight["selected"] == 2
    assert preflight["blocked"] == 1
    blocked = next(
        item for item in preflight["items"]
        if item["artifact_id"] == authority_item["artifact_id"]
    )
    assert blocked["blocked_reason"] == "ACTIVE_AUTHORITY"


def test_artifact_reconciliation_indexes_generated_files_without_double_counting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, job, _request, evidence_root = build_optimizer_fixture(
        tmp_path,
        monkeypatch,
        [pass_row(1, mean_r=0.31, weighted_r=0.11)],
    )
    job_root = Path(job["evidence_dir"])
    compile_log = job_root / "round_01" / "compile.log"
    compile_log.write_text("0 errors, 0 warnings", encoding="utf-8")

    monkeypatch.setattr(artifact_control, "ROOT", tmp_path)
    monkeypatch.setattr(
        artifact_control,
        "OPTIMIZER_ARTIFACT_ROOT",
        evidence_root,
    )
    monkeypatch.setattr(
        artifact_control,
        "CHALLENGER_ARTIFACT_ROOT",
        tmp_path / "artifacts" / "challengers",
    )
    monkeypatch.setattr(
        artifact_control,
        "BACKTEST_ARTIFACT_ROOT",
        tmp_path / "artifacts" / "backtests",
    )
    monkeypatch.setattr(
        artifact_control,
        "EVIDENCE_DIR",
        tmp_path / "accepted_evidence",
    )
    baseline = tmp_path / "ea" / "baseline" / "Max_MTF.mq5"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text("baseline", encoding="utf-8")
    monkeypatch.setattr(artifact_control, "EA_BASELINE", baseline)
    monkeypatch.setattr(
        artifact_control,
        "detect_mt5",
        lambda: {"status": "UNAVAILABLE", "reason": "FIXTURE_NO_MT5"},
    )

    artifact_control.reconcile_artifacts(path=db)
    result = artifact_control.artifact_page(
        page=1,
        page_size=100,
        path=db,
    )
    with connect(db) as conn:
        parent = conn.execute(
            """
            SELECT size_bytes,deletable
            FROM artifact_registry
            WHERE owner_type='OPTIMIZER_JOB' AND owner_id=?
            """,
            (job["job_id"],),
        ).fetchone()
        child_rows = conn.execute(
            """
            SELECT artifact_type,canonical_path,size_bytes,sha256
            FROM artifact_registry
            WHERE owner_type='OPTIMIZER_FILE' AND source_id=?
            ORDER BY canonical_path
            """,
            (job["job_id"],),
        ).fetchall()

    assert parent is not None
    assert parent["size_bytes"] == 0
    assert parent["deletable"] == 1
    assert child_rows
    child_types = {str(row["artifact_type"]) for row in child_rows}
    assert "MT5_XML" in child_types
    assert "CSV_SIDECAR" in child_types
    assert "JSON_EVIDENCE" in child_types
    assert "COMPILE_LOG" in child_types
    child_bytes = sum(int(row["size_bytes"]) for row in child_rows)
    assert result["summary"]["optimizer_storage"] == child_bytes
    assert result["summary"]["total_generated_storage"] == child_bytes
    assert result["summary"]["safe_cleanup_bytes"] == child_bytes
    assert all(str(row["sha256"] or "") for row in child_rows)

    compile_log.unlink()
    artifact_control.reconcile_artifacts(path=db)
    artifact_control.artifact_page(page=1, page_size=100, path=db)
    with connect(db) as conn:
        stale_log_count = int(conn.execute(
            """
            SELECT COUNT(*) AS n FROM artifact_registry
            WHERE owner_type='OPTIMIZER_FILE'
              AND source_id=?
              AND artifact_type='COMPILE_LOG'
            """,
            (job["job_id"],),
        ).fetchone()["n"])
    assert stale_log_count == 0


def test_artifact_inventory_read_does_not_reconcile_or_probe_mt5(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _job, _request, _root = build_optimizer_fixture(
        tmp_path,
        monkeypatch,
        [pass_row(1, mean_r=0.31, weighted_r=0.11)],
    )
    artifact = tmp_path / "artifacts" / "inventory-only.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("synthetic", encoding="utf-8")
    artifact_control.register_artifact(
        artifact_type="TEST_ARTIFACT",
        producer="TEST",
        owner_type="TEST_OWNER",
        owner_id="synthetic-owner",
        canonical_path=artifact,
        path=db,
    )

    def forbidden(**_kwargs):
        raise AssertionError("ordinary inventory read performed reconciliation")

    monkeypatch.setattr(artifact_control, "reconcile_artifacts", forbidden)
    monkeypatch.setattr(
        artifact_control,
        "detect_mt5",
        lambda: (_ for _ in ()).throw(AssertionError("ordinary read probed MT5")),
    )
    page = artifact_control.artifact_page(
        query="SYNTHETIC-OWNER",
        artifact_type="TEST_ARTIFACT",
        page=1,
        page_size=25,
        path=db,
    )

    assert page["total"] == 1
    assert page["items"][0]["owner_id"] == "synthetic-owner"
    assert page["runtime"] == {
        "status": "NOT_CHECKED",
        "reason": "EXPLICIT_RECONCILE_REQUIRED",
        "data_root": None,
    }


def test_challenger_registry_list_does_not_verify_each_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _job, challenger, _root = create_one_challenger_fixture(tmp_path, monkeypatch)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("registry read performed bundle verification")

    monkeypatch.setattr(challenger_operations, "verify_challenger_bundle", forbidden)
    page = challenger_operations.challenger_registry_page(
        view="active",
        page=1,
        page_size=25,
        path=db,
    )

    assert page["total"] == 1
    assert page["items"][0]["challenger_id"] == challenger["challenger_id"]
    assert page["items"][0]["integrity"] == "NOT_CHECKED"


def test_backtest_bulk_preflight_blocks_before_mutation_and_clean_is_confirmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _challenger, record, _evidence, runtime_paths = create_completed_backtest_fixture(
        tmp_path,
        monkeypatch,
    )
    backtest_id = str(record["backtest_id"])

    ready = backtest_control.backtest_action_preflight(
        [backtest_id],
        action="clean",
        path=db,
    )
    assert ready["selected"] == 1
    assert ready["cleanable"] == 1
    assert ready["blocked"] == 0
    assert ready["runtime_bytes"] > 0

    with pytest.raises(
        RuntimeError,
        match="EXPLICIT_BACKTEST_BULK_CONFIRMATION_REQUIRED",
    ):
        backtest_control.execute_backtest_action(
            [backtest_id],
            action="clean",
            confirmed=False,
            path=db,
        )
    assert runtime_paths["expert_dir"].exists()

    completed = backtest_control.execute_backtest_action(
        [backtest_id],
        action="clean",
        confirmed=True,
        path=db,
    )
    assert completed["status"] == "COMPLETED"
    assert len(completed["results"]) == 1
    assert runtime_paths["expert_dir"].exists() is False
    assert runtime_paths["tester_set"].exists() is False
    assert runtime_paths["runtime_report"].exists() is False

    # An active Backtest blocks the complete batch before mutation.
    second_id = "BT-20260924-091500-feedface"
    second_evidence = tmp_path / "artifacts" / "backtests" / second_id
    second_evidence.mkdir(parents=True)
    create_backtest_record(
        backtest_id=second_id,
        challenger_id=record["challenger_id"],
        source_manifest_sha256=record["source_manifest_sha256"],
        request=record["request"],
        ea_sha256="e" * 64,
        set_sha256="s" * 64,
        evidence_path=second_evidence.relative_to(tmp_path).as_posix(),
        path=db,
    )
    update_backtest(second_id, state="RUNNING", path=db)
    blocked = backtest_control.backtest_action_preflight(
        [second_id],
        action="delete",
        path=db,
    )
    assert blocked["blocked"] == 1
    assert "ACTIVE_BACKTEST" in blocked["items"][0]["blockers"]
    with pytest.raises(RuntimeError, match="BACKTEST_BULK_ACTION_BLOCKED"):
        backtest_control.execute_backtest_action(
            [second_id],
            action="delete",
            confirmed=True,
            path=db,
        )
    assert get_backtest(second_id, path=db) is not None



def test_backtest_bulk_partial_failure_reports_completed_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = ["BT-A", "BT-B"]
    monkeypatch.setattr(
        backtest_control,
        "backtest_action_preflight",
        lambda *_args, **_kwargs: {
            "items": [
                {"backtest_id": "BT-A", "blockers": []},
                {"backtest_id": "BT-B", "blockers": []},
            ]
        },
    )

    def fake_clean(backtest_id: str, **_kwargs):
        if backtest_id == "BT-B":
            raise RuntimeError("INJECTED_SECOND_FAILURE")
        return {"backtest_id": backtest_id, "runtime_status": "CLEANED"}

    monkeypatch.setattr(
        backtest_control,
        "clean_backtest_runtime",
        fake_clean,
    )
    with pytest.raises(
        RuntimeError,
        match=(
            "BACKTEST_BULK_PARTIAL_FAILED:"
            "completed=BT-A;failed=BT-B;reason=INJECTED_SECOND_FAILURE"
        ),
    ):
        backtest_control.execute_backtest_action(
            ids,
            action="clean",
            confirmed=True,
        )



def _patch_global_cleanup_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    optimizer_root = tmp_path / "artifacts" / "optimizer"
    challenger_root = tmp_path / "artifacts" / "challengers"
    backtest_root = tmp_path / "artifacts" / "backtests"
    operation_root = tmp_path / "artifacts" / "challenger_operations"
    history_root = tmp_path / "artifacts" / "strategy_history"
    recovery_root = tmp_path / "state" / "promotion_recovery"
    champion_root = tmp_path / "ea" / "champion" / "current"
    evidence_root = tmp_path / "evidence"
    legacy_root = tmp_path / "evidence" / "m06" / "challenger_backtests"
    baseline = tmp_path / "ea" / "baseline" / "Max_MTF.mq5"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    if not baseline.exists():
        baseline.write_text("baseline", encoding="utf-8")

    for name, value in {
        "ROOT": tmp_path,
        "OPTIMIZER_ARTIFACT_ROOT": optimizer_root,
        "CHALLENGER_ARTIFACT_ROOT": challenger_root,
        "BACKTEST_ARTIFACT_ROOT": backtest_root,
        "CHALLENGER_OPERATION_ARTIFACT_ROOT": operation_root,
        "STRATEGY_HISTORY_ROOT": history_root,
        "PROMOTION_RECOVERY_ROOT": recovery_root,
        "CHAMPION_CURRENT_ROOT": champion_root,
        "EVIDENCE_DIR": evidence_root,
        "LEGACY_M06_BACKTEST_EVIDENCE_ROOT": legacy_root,
        "EA_BASELINE": baseline,
    }.items():
        monkeypatch.setattr(artifact_control, name, value)

    monkeypatch.setattr(artifact_control, "detect_mt5", lambda: fake_mt5(tmp_path))


def _insert_complex_generated_state(
    db: Path,
    *,
    tmp_path: Path,
    challenger: dict,
) -> None:
    now = "2026-09-24T03:00:00+00:00"
    operation_root = tmp_path / "artifacts" / "challenger_operations"
    retirement_dir = operation_root / "retirements" / "RETIRE-TEST"
    retirement_dir.mkdir(parents=True, exist_ok=True)
    (retirement_dir / "retirement.json").write_text("{}", encoding="utf-8")

    recovery_dir = tmp_path / "state" / "promotion_recovery" / "PROMOTE-TEST"
    recovery_dir.mkdir(parents=True, exist_ok=True)
    (recovery_dir / "journal.json").write_text("{}", encoding="utf-8")

    history_dir = tmp_path / "artifacts" / "strategy_history" / "HISTORY-TEST"
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / "history.json").write_text("{}", encoding="utf-8")

    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO scientist_threads(thread_id,created_utc,updated_utc,title)
            VALUES(?,?,?,?)
            """,
            ("SCI-TEST", now, now, "Fixture"),
        )
        conn.execute(
            """
            INSERT INTO scientist_chat_requests(
                request_id,thread_id,state,created_utc,updated_utc
            ) VALUES(?,?,?,?,?)
            """,
            ("REQ-TEST", "SCI-TEST", "COMPLETED", now, now),
        )
        conn.execute(
            """
            INSERT INTO scientist_messages(
                message_id,thread_id,sequence,role,content,created_utc,request_id
            ) VALUES(?,?,?,?,?,?,?)
            """,
            ("MSG-U", "SCI-TEST", 1, "user", "fixture", now, "REQ-TEST"),
        )
        conn.execute(
            """
            INSERT INTO scientist_messages(
                message_id,thread_id,sequence,role,content,created_utc,request_id
            ) VALUES(?,?,?,?,?,?,?)
            """,
            ("MSG-A", "SCI-TEST", 2, "assistant", "fixture", now, "REQ-TEST"),
        )
        conn.execute(
            """
            INSERT INTO strategy_challenger_retirements(
                retirement_id,challenger_id,state,before_status,after_status,
                expected_manifest_sha256,created_utc,evidence_path,before_state_json
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                "RETIRE-TEST",
                challenger["challenger_id"],
                "FAILED",
                "CHALLENGER",
                "RETIRED",
                challenger["manifest_sha256"],
                now,
                retirement_dir.relative_to(tmp_path).as_posix(),
                "{}",
            ),
        )
        conn.execute(
            """
            INSERT INTO strategy_promotions(
                promotion_id,challenger_id,previous_champion_id,new_champion_id,
                state,created_utc,expected_challenger_manifest_sha256,
                expected_previous_champion_id,before_state_json,recovery_path,
                error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "PROMOTE-TEST",
                challenger["challenger_id"],
                None,
                challenger["challenger_id"],
                "FAILED",
                now,
                challenger["manifest_sha256"],
                None,
                "{}",
                recovery_dir.relative_to(tmp_path).as_posix(),
                "fixture",
            ),
        )

    orphan = (
        tmp_path / "mt5" / "MQL5" / "Experts" / "MaxMTF"
        / "ChallengerBacktests" / "BT-ORPHAN-REPAIR"
    )
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "orphan.ex5").write_bytes(b"orphan")


def test_v6_owner_workflow_survives_future_v7_current_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, job, request, evidence_root = build_optimizer_fixture(
        tmp_path,
        monkeypatch,
        [
            pass_row(1, mean_r=0.31, weighted_r=0.11),
            pass_row(2, mean_r=0.29, weighted_r=0.12),
        ],
    )
    assert request["schema"] == "MAX_REBUILD_OPTIMIZER_REQUEST_V6"
    assert request["optimizer_result_workflow"] == OPTIMIZER_WORKFLOW_OWNER_EXPLICIT

    monkeypatch.setattr(
        workflow_contract,
        "OPTIMIZER_REQUEST_SCHEMA_CURRENT",
        "MAX_REBUILD_OPTIMIZER_REQUEST_V7",
    )

    detail = job_detail(job["job_id"], path=db, evidence_root=evidence_root)
    assert detail is not None
    assert detail["status"] == OPTIMIZER_TERMINAL_QUALIFIED_POOL
    assert detail["challenger_created"] == 0
    assert not (Path(job["evidence_dir"]) / "eligible_winner.json").exists()

    page = qualified_candidates_page(job["job_id"], path=db)
    assert page["qualified_count"] == 2

    result = selection.create_selected_challengers(
        job["job_id"],
        [{"round": 1, "pass": 2}],
        path=db,
        artifact_root=tmp_path / "artifacts" / "challengers",
        project_root=tmp_path,
    )
    assert result["state"] == "COMMITTED"
    assert len(result["challengers"]) == 1
    assert result["challengers"][0]["source_pass"] == 2
    assert not (Path(job["evidence_dir"]) / "eligible_winner.json").exists()


def test_job_detail_exposes_resolved_workflow_and_future_schema_fails_closed(
    tmp_path: Path,
) -> None:
    def detail_for(request: dict, name: str) -> dict:
        db = tmp_path / name / "state" / "max.db"
        initialize_database(db)
        ensure_baseline_registered(db)
        migrate_current(db)
        job = create_job(
            request,
            evidence_root=tmp_path / name / "artifacts" / "optimizer",
            path=db,
        )
        detail = job_detail(
            job["job_id"],
            path=db,
            evidence_root=tmp_path / name / "artifacts" / "optimizer",
        )
        assert detail is not None
        return detail

    historical_v4 = detail_for(
        {
            "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V4",
            "max_rounds": 1,
        },
        "v4",
    )
    assert (
        historical_v4["optimizer_result_workflow"]
        == OPTIMIZER_WORKFLOW_OWNER_EXPLICIT
    )

    future_v5 = detail_for(
        {
            "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V5",
            "optimizer_result_workflow": OPTIMIZER_WORKFLOW_OWNER_EXPLICIT,
            "max_rounds": 1,
        },
        "v5-explicit",
    )
    assert (
        future_v5["optimizer_result_workflow"]
        == OPTIMIZER_WORKFLOW_OWNER_EXPLICIT
    )

    db = tmp_path / "v5-missing" / "state" / "max.db"
    initialize_database(db)
    ensure_baseline_registered(db)
    migrate_current(db)
    job = create_job(
        {
            "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V5",
            "max_rounds": 1,
        },
        evidence_root=tmp_path / "v5-missing" / "artifacts" / "optimizer",
        path=db,
    )
    with pytest.raises(RuntimeError, match="OPTIMIZER_RESULT_WORKFLOW_MISSING"):
        job_detail(
            job["job_id"],
            path=db,
            evidence_root=tmp_path / "v5-missing" / "artifacts" / "optimizer",
        )


def test_global_clean_dependency_chain_and_idempotency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _challenger, _record, _evidence, _runtime_paths = (
        create_completed_backtest_fixture(tmp_path, monkeypatch)
    )
    _patch_global_cleanup_roots(tmp_path, monkeypatch)

    preflight = artifact_control.global_cleanup_preflight(path=db)
    assert preflight["status"] == "READY"
    assert preflight["plan"]["optimizer_jobs"] == 1
    assert preflight["plan"]["optimizer_rounds"] == 1
    assert preflight["plan"]["challengers"] == 1
    assert preflight["plan"]["backtests"] == 1
    assert preflight["plan"]["challenger_batches"] == 1
    assert preflight["blockers"] == []

    first = artifact_control.clean_generated_data(confirmed=True, path=db)
    assert first["status"] == "CLEAN"
    assert first["provider_settings_unchanged"] is True
    assert first["baseline_unchanged"] is True
    assert all(
        first["final"][key] == 0
        for key in (
            "optimizer_jobs",
            "optimizer_rounds",
            "strategy_challenger_batches",
            "strategy_challenger_batch_items",
            "challengers",
            "backtests",
            "scientist_threads",
            "scientist_messages",
            "scientist_chat_requests",
            "generated_artifact_registry_rows",
            "registered_orphan_runtime",
            "generated_semantic_root_objects",
        )
    )
    assert first["final"]["champion"] is None

    second = artifact_control.clean_generated_data(confirmed=True, path=db)
    assert second["status"] == "CLEAN"
    assert second["final"] == first["final"]


def test_global_clean_complex_state_and_artifact_producer_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, challenger, _record, _evidence, _runtime_paths = (
        create_completed_backtest_fixture(tmp_path, monkeypatch)
    )
    _patch_global_cleanup_roots(tmp_path, monkeypatch)
    _insert_complex_generated_state(db, tmp_path=tmp_path, challenger=challenger)

    artifact_control.reconcile_artifacts(path=db)
    page = artifact_control.artifact_page(page=1, page_size=100, path=db)
    owner_types = {item["owner_type"] for item in page["items"]}
    required = {
        "OPTIMIZER_JOB",
        "OPTIMIZER_ROUND",
        "QUALIFIED_CANDIDATE_POOL",
        "CHALLENGER",
        "CHALLENGER_BATCH",
        "CHALLENGER_BATCH_ITEM",
        "BACKTEST",
        "BACKTEST_RUNTIME",
        "SCIENTIST_THREAD",
        "SCIENTIST_REQUEST",
        "SCIENTIST_MESSAGE",
        "PROMOTION",
        "RETIREMENT",
        "CHALLENGER_OPERATION_FILE",
        "STRATEGY_HISTORY_FILE",
        "PROMOTION_RECOVERY_FILE",
        "ORPHAN_RUNTIME",
    }
    assert required.issubset(owner_types)

    challenger_item = next(
        item for item in page["items"]
        if item["owner_type"] == "CHALLENGER"
    )
    trace = artifact_control.artifact_trace(
        challenger_item["artifact_id"],
        path=db,
    )
    assert trace["challenger"]["source_round"] == 1
    assert trace["challenger"]["source_pass"] == 1
    assert trace["challenger"]["source_job_id"]

    preflight = artifact_control.global_cleanup_preflight(path=db)
    assert preflight["status"] == "READY"
    assert preflight["plan"]["retirements"] == 1
    assert preflight["plan"]["promotions"] == 1
    assert preflight["plan"]["scientist_threads"] == 1
    assert preflight["plan"]["scientist_messages"] == 2
    assert preflight["plan"]["scientist_chat_requests"] == 1
    assert preflight["plan"]["runtime_artifacts"] >= 1

    result = artifact_control.clean_generated_data(confirmed=True, path=db)
    assert result["status"] == "CLEAN"
    assert result["final"]["retirements"] == 0
    assert result["final"]["promotions"] == 0
    assert result["final"]["scientist_threads"] == 0
    assert result["final"]["scientist_messages"] == 0
    assert result["final"]["scientist_chat_requests"] == 0
    assert result["final"]["registered_orphan_runtime"] == 0
    assert result["final"]["promotion_recovery_residue"] == 0


def test_global_clean_scientist_failure_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "state" / "max.db"
    initialize_database(db)
    ensure_baseline_registered(db)
    migrate_current(db)
    _patch_global_cleanup_roots(tmp_path, monkeypatch)

    def fail_purge(*_args, **_kwargs):
        raise RuntimeError("INJECTED_SCIENTIST_PURGE_FAILURE")

    monkeypatch.setattr(artifact_control, "purge_chat", fail_purge)
    with pytest.raises(RuntimeError, match="INJECTED_SCIENTIST_PURGE_FAILURE"):
        artifact_control.clean_generated_data(confirmed=True, path=db)


def test_new_backtest_runtime_naming_never_emits_legacy_m06() -> None:
    source = inspect.getsource(challenger_operations.run_challenger_backtest)
    assert 'f"MaxMTF_Backtest_{backtest_id}.set"' in source
    assert 'f"MaxMTF_Backtest_{backtest_id}"' in source
    assert "MAX_M06_" not in source



def test_global_clean_preserves_protected_baseline_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "state" / "max.db"
    initialize_database(db)
    _patch_global_cleanup_roots(tmp_path, monkeypatch)
    ensure_baseline_registered(db)
    migrate_current(db)

    history_root = tmp_path / "artifacts" / "strategy_history"
    protected = history_root / "BASELINE-ACCEPTED"
    protected.mkdir(parents=True, exist_ok=True)
    (protected / "baseline.json").write_text(
        json.dumps(
            {
                "schema": "MAX_REBUILD_BASELINE_ARCHIVE_V1",
                "archive_id": "BASELINE-ACCEPTED",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (protected / "Max_MTF.mq5").write_text(
        "protected baseline history",
        encoding="utf-8",
    )

    disposable = history_root / "TENURE-DISPOSABLE"
    disposable.mkdir(parents=True, exist_ok=True)
    (disposable / "history.json").write_text("{}", encoding="utf-8")

    artifact_control.reconcile_artifacts(path=db)
    page = artifact_control.artifact_page(page=1, page_size=100, path=db)
    protected_rows = [
        item
        for item in page["items"]
        if item["owner_type"] == "STRATEGY_HISTORY_FILE"
        and "BASELINE-ACCEPTED" in item["canonical_path"]
    ]
    assert protected_rows
    assert all(
        item["retention_class"] == "ACTIVE_AUTHORITY"
        and item["in_use"] is True
        for item in protected_rows
    )

    result = artifact_control.clean_generated_data(confirmed=True, path=db)
    assert result["status"] == "CLEAN"
    assert result["baseline_unchanged"] is True
    assert protected.is_dir()
    assert (protected / "baseline.json").is_file()
    assert (protected / "Max_MTF.mq5").is_file()
    assert not disposable.exists()
    assert result["final"]["generated_semantic_root_objects"] == 0

    after = artifact_control.artifact_page(page=1, page_size=100, path=db)
    remaining = [
        item
        for item in after["items"]
        if item["owner_type"] == "STRATEGY_HISTORY_FILE"
    ]
    assert remaining
    assert all(item["retention_class"] == "ACTIVE_AUTHORITY" for item in remaining)


@pytest.mark.parametrize(
    "consumed_status",
    ["REGISTERING", "CHALLENGER", "PROMOTED", "RETIRED"],
)
def test_consumed_optimizer_source_never_returns_to_active_candidate_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    consumed_status: str,
) -> None:
    rows = [
        pass_row(1, mean_r=0.31, weighted_r=0.11),
        pass_row(2, mean_r=0.29, weighted_r=0.12),
        pass_row(3, mean_r=0.27, weighted_r=0.13),
    ]
    db, job, _request, _root = build_optimizer_fixture(
        tmp_path,
        monkeypatch,
        rows,
    )
    artifact_root = tmp_path / "artifacts" / "challengers"

    before = qualified_candidates_page(job["job_id"], path=db)
    assert before["historical_qualified_count"] == 3
    assert before["qualified_count"] == 3
    assert before["consumed_count"] == 0
    assert {item["pass"] for item in before["items"]} == {1, 2, 3}

    created = selection.create_selected_challengers(
        job["job_id"],
        [{"round": 1, "pass": 1}],
        path=db,
        artifact_root=artifact_root,
        project_root=tmp_path,
    )
    assert created["state"] == "COMMITTED"
    challenger_id = created["challengers"][0]["challenger_id"]

    with connect(db) as conn:
        conn.execute(
            """
            UPDATE strategy_challengers
            SET status=?,
                retired_utc=CASE WHEN ?='RETIRED'
                    THEN '2026-09-26T00:00:00+00:00'
                    ELSE NULL END
            WHERE challenger_id=?
            """,
            (consumed_status, consumed_status, challenger_id),
        )

    active = qualified_candidates_page(job["job_id"], path=db)
    assert active["historical_qualified_count"] == 3
    assert active["qualified_count"] == 2
    assert active["consumed_count"] == 1
    assert active["rejected_count"] == 0
    assert {item["pass"] for item in active["items"]} == {2, 3}

    historical = qualified_candidate(job["job_id"], 1, 1, path=db)
    assert historical["pass"] == 1
    before_pass_one = next(
        item for item in before["items"] if item["pass"] == 1
    )
    assert historical["params"] == before_pass_one["params"]

    with pytest.raises(
        RuntimeError,
        match="QUALIFIED_CANDIDATE_ALREADY_CONSUMED",
    ):
        revalidate_candidate_for_registration(
            job["job_id"],
            1,
            1,
            path=db,
        )


def test_committed_batch_keeps_source_consumed_after_challenger_row_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        pass_row(1, mean_r=0.31, weighted_r=0.11),
        pass_row(2, mean_r=0.29, weighted_r=0.12),
    ]
    db, job, _request, _root = build_optimizer_fixture(
        tmp_path,
        monkeypatch,
        rows,
    )
    artifact_root = tmp_path / "artifacts" / "challengers"

    created = selection.create_selected_challengers(
        job["job_id"],
        [{"round": 1, "pass": 1}],
        path=db,
        artifact_root=artifact_root,
        project_root=tmp_path,
    )
    challenger_id = created["challengers"][0]["challenger_id"]
    assert created["state"] == "COMMITTED"

    with connect(db) as conn:
        conn.execute(
            "DELETE FROM strategy_challengers WHERE challenger_id=?",
            (challenger_id,),
        )
    assert get_challenger(challenger_id, path=db) is None

    active = qualified_candidates_page(job["job_id"], path=db)
    assert active["historical_qualified_count"] == 2
    assert active["qualified_count"] == 1
    assert active["consumed_count"] == 1
    assert [item["pass"] for item in active["items"]] == [2]

    with pytest.raises(
        RuntimeError,
        match="QUALIFIED_CANDIDATE_ALREADY_CONSUMED",
    ):
        revalidate_candidate_for_registration(
            job["job_id"],
            1,
            1,
            path=db,
        )
