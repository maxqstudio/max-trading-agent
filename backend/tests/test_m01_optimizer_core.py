from __future__ import annotations

import csv
import math
from copy import deepcopy
from pathlib import Path

import pytest

import max_backend.optimizer_core as core
from max_backend.optimizer_core import (
    ABSOLUTE_BOUNDS,
    CURRENT_DAILY_LOSS_LIMIT_PCT,
    CURRENT_OPTIMIZER_SCHEMA,
    DEFAULT_KPI,
    LEGACY_ABSOLUTE_BOUNDS,
    DEFAULT_OPTIMIZER_TRADE_EXPONENT_ALPHA,
    DEFAULT_SPACE,
    FAMILY_WEIGHT_PARAMS,
    OPTIMIZER_FITNESS_SCHEMA,
    OPTIMIZER_METRICS_SCHEMA_V2,
    OptimizationPass,
    apply_frozen_gates,
    build_set_text,
    build_tester_ini,
    deterministic_refine,
    eligibility_audit,
    freeze_request,
    optimizer_fitness_contract,
    optimizer_fitness_for_request,
    optimizer_fitness_value,
    parameter_signature,
    parse_optimization_xml,
    parse_optimizer_metrics_csv,
    parse_set_optimizer_entries,
    search_space_cardinality,
    select_winner,
    trade_sample,
    unresolved_weighted_contenders,
    validate_optimizer_trade_exponent,
    validate_search_space,
)


EXPECTED_PARAMS = [
    "InpWeightTrend",
    "InpWeightRange",
    "InpWeightBreakout",
    "InpWeightPullback",
    "InpWeightSession",
    "InpWeightShock",
    "InpWeightRelative",
    "InpEntryThreshold",
    "InpExitReverseThreshold",
    "InpMinConsensus",
    "InpSL_ATR",
    "InpTP_ATR",
    "InpMaxHoldBars",
    "InpShockHaltATR",
    "InpRelativeLookback",
    "InpMinRelativeCorr",
    "InpRiskPct",
]


def fake_mt5() -> dict:
    return {
        "status": "READY_EXECUTABLE_AND_DATA_ROOT",
        "reason": "READY",
        "terminal": r"C:\Fake\MetaTrader 5\terminal64.exe",
        "metaeditor": r"C:\Fake\MetaTrader 5\metaeditor64.exe",
        "data_root": r"C:\Fake\TerminalData",
    }


def raw_request(**overrides):
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
        "max_rounds": 3,
        "optimize_params": ["InpEntryThreshold"],
        "search_space": deepcopy(DEFAULT_SPACE),
        "kpi": {
            "min_profit_factor": 1.0,
            "min_recovery_factor": 0.0,
            "min_expectancy_r": 0.0,
            "min_weighted_r": 0.0,
            "base_h1_trades_per_month": 20,
        },
        "scientist_assist": False,
    }
    payload.update(overrides)
    return payload


def freeze(monkeypatch: pytest.MonkeyPatch, **overrides):
    monkeypatch.setattr(core, "detect_mt5", fake_mt5)
    return freeze_request(raw_request(**overrides))


def param_vector(**overrides):
    values = core.read_ea_optimizer_defaults()
    values.update(overrides)
    return values


def write_xml(path: Path, rows: list[dict], *, include_rf: bool = True) -> None:
    headers = ["Pass", "Profit Factor"]
    if include_rf:
        headers.append("Recovery Factor")
    headers += ["Custom", "Profit", "Trades"] + list(ABSOLUTE_BOUNDS)

    def row_xml(values):
        cells = "".join(
            f'<Cell><Data ss:Type="String">{value}</Data></Cell>'
            for value in values
        )
        return f"<Row>{cells}</Row>"

    body_rows = [row_xml(headers)]
    for item in rows:
        values = [item["pass"], item["pf"]]
        if include_rf:
            values.append(item["rf"])
        values += [item.get("custom_fitness", item["mean_r"]), item["profit"], item["trades"]]
        values += [item["params"][name] for name in ABSOLUTE_BOUNDS]
        body_rows.append(row_xml(values))
    xml = (
        '<?xml version="1.0"?>'
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
        'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
        "<Worksheet><Table>"
        + "".join(body_rows)
        + "</Table></Worksheet></Workbook>"
    )
    path.write_text(xml, encoding="utf-8")


def write_metrics(path: Path, rows: list[dict], *, nonce: int) -> None:
    fields = [
        "frame_pass_id",
        "frame_inputs",
        "mean_expectancy_r",
        "weighted_r",
        "mt5_trades",
        "r_accounted_trades",
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
            risk = float(item.get("sum_initial_risk", profit / weighted if weighted else 100.0))
            writer.writerow(
                {
                    "frame_pass_id": item.get("frame_pass_id", 10_000_000_000 + index),
                    "frame_inputs": "|".join(
                        f"{name}={item['params'][name]}" for name in ABSOLUTE_BOUNDS
                    ),
                    "mean_expectancy_r": item["mean_r"],
                    "weighted_r": weighted,
                    "mt5_trades": item["trades"],
                    "r_accounted_trades": item.get("r_accounted_trades", item["trades"]),
                    "sum_net": profit,
                    "sum_initial_risk": risk,
                    "accounting_errors": item.get("accounting_errors", 0),
                    "run_nonce": item.get("run_nonce", nonce),
                }
            )


def write_metrics_v2(
    path: Path,
    rows: list[dict],
    *,
    nonce: int,
    alpha: float,
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
            mean_r = float(item["mean_r"])
            trades = int(item.get("r_accounted_trades", item["trades"]))
            custom_fitness = (
                float(item["custom_fitness"])
                if "custom_fitness" in item
                else optimizer_fitness_value(mean_r, trades, alpha)
            )
            weighted = float(item["weighted_r"])
            profit = float(item["profit"])
            risk = float(item.get("sum_initial_risk", profit / weighted if weighted else 100.0))
            writer.writerow(
                {
                    "evidence_schema": OPTIMIZER_METRICS_SCHEMA_V2,
                    "fitness_schema": OPTIMIZER_FITNESS_SCHEMA,
                    "frame_pass_id": item.get("frame_pass_id", 20_000_000_000 + index),
                    "frame_inputs": "|".join(
                        f"{name}={item['params'][name]}" for name in ABSOLUTE_BOUNDS
                    ),
                    "custom_fitness": custom_fitness,
                    "trade_exponent_alpha": item.get("trade_exponent_alpha", alpha),
                    "mean_expectancy_r": mean_r,
                    "weighted_r": weighted,
                    "mt5_trades": item["trades"],
                    "r_accounted_trades": trades,
                    "sum_r": item.get("sum_r", mean_r * trades),
                    "sum_net": profit,
                    "sum_initial_risk": risk,
                    "accounting_errors": item.get("accounting_errors", 0),
                    "run_nonce": item.get("run_nonce", nonce),
                }
            )


def pass_row(pass_no: int = 1, **overrides):
    params = overrides.pop("params", param_vector())
    row = {
        "pass": pass_no,
        "pf": 1.2,
        "rf": 0.5,
        "mean_r": 0.2,
        "weighted_r": 0.1,
        "profit": 10.0,
        "trades": 25,
        "params": params,
    }
    row.update(overrides)
    return row


def test_exact_17_parameter_universe_and_family_weights() -> None:
    assert list(ABSOLUTE_BOUNDS) == EXPECTED_PARAMS
    assert len(ABSOLUTE_BOUNDS) == 17
    assert len(LEGACY_ABSOLUTE_BOUNDS) == 16
    assert set(LEGACY_ABSOLUTE_BOUNDS).issubset(ABSOLUTE_BOUNDS)
    assert "InpRiskPct" not in LEGACY_ABSOLUTE_BOUNDS
    assert ABSOLUTE_BOUNDS["InpRiskPct"] == (0.50, 5.00, 0.50, "float")
    assert len(FAMILY_WEIGHT_PARAMS) == 7


@pytest.mark.parametrize(
    "mutator",
    [
        lambda space: space.update({"HACK": {"start": 1, "step": 1, "stop": 2}}),
        lambda space: space.pop("InpWeightTrend"),
        lambda space: space["InpEntryThreshold"].update({"start": float("nan")}),
        lambda space: space["InpEntryThreshold"].update({"stop": float("inf")}),
        lambda space: space["InpEntryThreshold"].update({"start": 0.1}),
        lambda space: space["InpEntryThreshold"].update({"stop": 0.9}),
        lambda space: space["InpEntryThreshold"].update({"stop": 0.2, "start": 0.4}),
        lambda space: space["InpEntryThreshold"].update({"step": 0}),
        lambda space: space["InpEntryThreshold"].update({"step": 0.001}),
        lambda space: space["InpMaxHoldBars"].update({"start": 7.5}),
        lambda space: space["InpWeightTrend"].update({"start": 0.0}),
    ],
)
def test_search_space_fail_closed(mutator) -> None:
    space = deepcopy(DEFAULT_SPACE)
    mutator(space)
    with pytest.raises(ValueError):
        validate_search_space(space)


def test_selected_and_unselected_set_flags_are_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    request = freeze(monkeypatch)
    text = build_set_text(request, request["search_space"], optimizer_run_nonce=123)
    entries = parse_set_optimizer_entries(text)

    assert entries["InpEntryThreshold"]["optimize"] == "Y"
    assert entries["InpWeightTrend"]["optimize"] == "N"
    assert entries["InpWeightTrend"]["value"] == request["fixed_param_values"]["InpWeightTrend"]
    assert "InpUseOnnxChampion=false" in text
    assert "InpUseOnnxChallenger=false" in text
    assert "InpWriteTrainingData=false" in text
    assert "InpWriteTelemetry=false" in text
    assert "InpAllowLiveTrading=true" in text
    assert "InpConfirmSymbol=EURUSD.m" in text
    assert "InpOptimizerRunNonce=123" in text
    assert "InpOptimizerTradeExponent=0.5||0.5||0||0.5||N" in text
    assert "InpOptimizerTradeExponent" not in entries


def test_risk_pct_default_range_and_daily_loss_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = freeze(
        monkeypatch,
        optimize_params=["InpRiskPct"],
    )
    assert request["search_space"]["InpRiskPct"] == {
        "start": 0.5,
        "step": 0.5,
        "stop": 5.0,
    }
    assert request["fixed_param_values"]["InpRiskPct"] == 0.5
    assert request["fixed_execution_authority"]["InpMaxDailyLossPct"] == 5.0
    assert "InpMaxDailyLossPct" not in ABSOLUTE_BOUNDS

    text = build_set_text(
        request,
        request["search_space"],
        optimizer_run_nonce=321,
    )
    assert "InpRiskPct=0.5||0.5||0.5||5||Y" in text
    assert "InpMaxDailyLossPct=5" in text
    entries = parse_set_optimizer_entries(text)
    assert entries["InpRiskPct"]["start"] == "0.5"
    assert entries["InpRiskPct"]["step"] == "0.5"
    assert entries["InpRiskPct"]["stop"] == "5"
    assert entries["InpRiskPct"]["optimize"] == "Y"


def test_current_v6_freeze_rejects_legacy_16d_search_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_space = {
        name: deepcopy(DEFAULT_SPACE[name])
        for name in LEGACY_ABSOLUTE_BOUNDS
    }
    with pytest.raises(
        ValueError,
        match="current optimizer search space must contain exactly 17 parameters",
    ):
        freeze(
            monkeypatch,
            search_space=legacy_space,
            optimize_params=list(LEGACY_ABSOLUTE_BOUNDS),
        )


def test_risk_pct_discrete_values_are_exact() -> None:
    spec = DEFAULT_SPACE["InpRiskPct"]
    values = [
        round(float(spec["start"]) + index * float(spec["step"]), 10)
        for index in range(
            int(round((float(spec["stop"]) - float(spec["start"])) / float(spec["step"]))) + 1
        )
    ]
    assert values == [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]


@pytest.mark.parametrize(
    "range_value",
    [
        {"start": 0.0, "step": 0.5, "stop": 5.0},
        {"start": 0.5, "step": 0.5, "stop": 5.5},
        {"start": 0.5, "step": 0.0, "stop": 5.0},
        {"start": 0.5, "step": 0.1, "stop": 5.0},
        {"start": 0.5, "step": 1.0, "stop": 4.5},
        {"start": 0.75, "step": 0.5, "stop": 4.75},
        {"start": 1.0, "step": 0.5, "stop": 4.25},
        {"start": 4.0, "step": 0.5, "stop": 3.5},
    ],
)
def test_risk_pct_illegal_ranges_fail_closed(range_value: dict[str, float]) -> None:
    space = deepcopy(DEFAULT_SPACE)
    space["InpRiskPct"] = range_value
    with pytest.raises(ValueError):
        validate_search_space(space)


@pytest.mark.parametrize("center", [1.0, 2.5, 3.5, 5.0])
def test_risk_pct_refinement_stays_on_exact_owner_grid(center: float) -> None:
    rows = [
        OptimizationPass(
            round_no=1,
            pass_no=1,
            profit_factor=1.2,
            recovery_factor=1.0,
            expectancy_r=0.2,
            weighted_r=0.2,
            profit=10.0,
            trades=30,
            params=param_vector(InpRiskPct=center),
            raw={},
        )
    ]
    refined = deterministic_refine(DEFAULT_SPACE, rows, ["InpRiskPct"])
    risk = refined["InpRiskPct"]
    assert risk["step"] == pytest.approx(0.5)
    assert 0.5 <= float(risk["start"]) <= float(risk["stop"]) <= 5.0
    approved = {0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0}
    count = int(round((float(risk["stop"]) - float(risk["start"])) / 0.5)) + 1
    enumerated = {
        round(float(risk["start"]) + index * 0.5, 10)
        for index in range(count)
    }
    assert enumerated
    assert enumerated.issubset(approved)
    assert 0.75 not in enumerated
    assert 1.25 not in enumerated
    assert 1.375 not in enumerated


def test_parameter_vector_identity_distinguishes_risk_pct() -> None:
    lower = param_vector(InpRiskPct=0.5)
    higher = param_vector(InpRiskPct=1.0)
    assert parameter_signature(lower) != parameter_signature(higher)
    assert dict(parameter_signature(lower))["InpRiskPct"] == pytest.approx(0.5)
    assert dict(parameter_signature(higher))["InpRiskPct"] == pytest.approx(1.0)


def test_optimizer_fitness_v2_is_independent_of_risk_pct() -> None:
    left = optimizer_fitness_value(0.25, 100, 0.5)
    right = optimizer_fitness_value(0.25, 100, 0.5)
    assert left == right
    assert CURRENT_DAILY_LOSS_LIMIT_PCT == 5.0


def test_raw_cardinality_is_context_not_genetic_tasks() -> None:
    space = deepcopy(DEFAULT_SPACE)
    space["InpEntryThreshold"] = {"start": 0.18, "step": 0.02, "stop": 0.22}
    card = search_space_cardinality(space, ["InpEntryThreshold"])
    assert card["raw_complete_grid_combinations"] == 3
    assert card["authority"] == "RAW_CARTESIAN_GRID_ONLY_NOT_MT5_GENETIC_TASK_COUNT"


def test_request_freeze_and_ea_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    request = freeze(monkeypatch)
    assert request["scientist_assist"] is False
    assert request["optimize_params"] == ["InpEntryThreshold"]
    assert set(request["fixed_param_values"]) == set(ABSOLUTE_BOUNDS)
    assert request["schema"] == CURRENT_OPTIMIZER_SCHEMA
    assert request["schema"] == "MAX_REBUILD_OPTIMIZER_REQUEST_V6"
    assert request["fixed_execution_authority"]["InpMaxDailyLossPct"] == 5.0
    assert request["fixed_execution_authority"]["risk_pct_upper_bound"] == 5.0
    assert request["ea"]["sha256"] == "827c4caddedbe37081353e08bba35eac5f01e96314dd8650d7ea17ad109ae725"
    assert request["optimizer_fitness"] == optimizer_fitness_contract(0.5)
    assert request["mt5_optimization_criterion"]["code"] == 6
    assert request["mt5_optimization_criterion"]["fitness"] == OPTIMIZER_FITNESS_SCHEMA


@pytest.mark.parametrize(
    "override",
    [
        {"relative_symbol": "XAUUSD.m"},
        {"symbol": ""},
        {"period": "H5"},
        {"from_date": "2026-01-01"},
        {"from_date": "2026.02.01", "to_date": "2026.01.01"},
        {"from_date": "2026.02.30"},
        {"model": 3},
        {"optimization": 3},
        {"max_rounds": 0},
        {"max_rounds": 6},
    ],
)
def test_request_validation_rejects_illegal_contract(
    monkeypatch: pytest.MonkeyPatch,
    override,
) -> None:
    monkeypatch.setattr(core, "detect_mt5", fake_mt5)
    with pytest.raises((ValueError, RuntimeError)):
        freeze_request(raw_request(**override))


def test_optimizer_trade_exponent_default_and_legal_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = freeze(monkeypatch)
    low = freeze(monkeypatch, optimizer_trade_exponent_alpha=0.0)
    high = freeze(monkeypatch, optimizer_trade_exponent_alpha=1.0)
    assert DEFAULT_OPTIMIZER_TRADE_EXPONENT_ALPHA == pytest.approx(0.5)
    assert default["optimizer_fitness"]["trade_exponent_alpha"] == pytest.approx(0.5)
    assert low["optimizer_fitness"]["trade_exponent_alpha"] == pytest.approx(0.0)
    assert high["optimizer_fitness"]["trade_exponent_alpha"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    "value",
    [-0.01, 1.01, float("nan"), float("inf"), float("-inf"), "bad", True],
)
def test_optimizer_trade_exponent_rejects_invalid(
    monkeypatch: pytest.MonkeyPatch,
    value,
) -> None:
    monkeypatch.setattr(core, "detect_mt5", fake_mt5)
    with pytest.raises(ValueError):
        freeze_request(raw_request(optimizer_trade_exponent_alpha=value))


@pytest.mark.parametrize(
    "schema",
    [
        "MAX_REBUILD_OPTIMIZER_REQUEST_V1",
        "MAX_REBUILD_OPTIMIZER_REQUEST_V2",
        "MAX_REBUILD_OPTIMIZER_REQUEST_V3",
        "MAX_REBUILD_OPTIMIZER_REQUEST_V4",
    ],
)
def test_legacy_request_without_fitness_retains_mean_r_semantics(schema: str) -> None:
    assert optimizer_fitness_for_request({"schema": schema}) is None


@pytest.mark.parametrize(
    "schema",
    [
        "MAX_REBUILD_OPTIMIZER_REQUEST_V1",
        "MAX_REBUILD_OPTIMIZER_REQUEST_V2",
        "MAX_REBUILD_OPTIMIZER_REQUEST_V3",
        "MAX_REBUILD_OPTIMIZER_REQUEST_V4",
    ],
)
def test_legacy_request_with_v2_fitness_is_rejected(schema: str) -> None:
    with pytest.raises(ValueError, match="legacy optimizer request"):
        optimizer_fitness_for_request(
            {
                "schema": schema,
                "optimizer_fitness": optimizer_fitness_contract(0.5),
            }
        )


def test_v5_valid_fitness_contract_is_accepted() -> None:
    payload = optimizer_fitness_contract(0.5)
    assert optimizer_fitness_for_request(
        {
            "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V5",
            "optimizer_result_workflow": "QUALIFIED_POOL_OWNER_SELECTION",
            "optimizer_fitness": payload,
        }
    ) == payload


def test_v5_missing_fitness_contract_fails_closed() -> None:
    with pytest.raises(ValueError, match="fitness contract missing"):
        optimizer_fitness_for_request(
            {
                "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V5",
                "optimizer_result_workflow": "QUALIFIED_POOL_OWNER_SELECTION",
            }
        )


def test_v5_malformed_fitness_contract_fails_closed() -> None:
    malformed = optimizer_fitness_contract(0.5)
    malformed["formula"] = "NOT_THE_V2_FORMULA"
    with pytest.raises(ValueError, match="fitness contract mismatch"):
        optimizer_fitness_for_request(
            {
                "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V5",
                "optimizer_result_workflow": "QUALIFIED_POOL_OWNER_SELECTION",
                "optimizer_fitness": malformed,
            }
        )


def test_v5_wrong_authority_fails_closed() -> None:
    malformed = optimizer_fitness_contract(0.5)
    malformed["mean_r_authority"] = "WRONG_MEAN_R_AUTHORITY"
    with pytest.raises(ValueError, match="fitness contract mismatch"):
        optimizer_fitness_for_request(
            {
                "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V5",
                "optimizer_result_workflow": "QUALIFIED_POOL_OWNER_SELECTION",
                "optimizer_fitness": malformed,
            }
        )


@pytest.mark.parametrize("alpha", [-0.01, 1.01, float("nan"), float("inf")])
def test_v5_invalid_alpha_fails_closed(alpha: float) -> None:
    with pytest.raises(ValueError):
        optimizer_fitness_for_request(
            {
                "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V5",
                "optimizer_result_workflow": "QUALIFIED_POOL_OWNER_SELECTION",
                "optimizer_fitness": {
                    "schema": OPTIMIZER_FITNESS_SCHEMA,
                    "formula": "MEAN_R_X_TRADES_POW_ALPHA",
                    "trade_exponent_alpha": alpha,
                    "mean_r_authority": "R_ACCOUNTED_ARITHMETIC_MEAN",
                    "trade_count_authority": "R_ACCOUNTED_CLOSED_TRADES",
                },
            }
        )


def test_v6_valid_fitness_contract_is_accepted() -> None:
    payload = optimizer_fitness_contract(0.5)
    assert optimizer_fitness_for_request(
        {
            "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V6",
            "optimizer_result_workflow": "QUALIFIED_POOL_OWNER_SELECTION",
            "optimizer_fitness": payload,
        }
    ) == payload


def test_v6_missing_fitness_contract_fails_closed() -> None:
    with pytest.raises(ValueError, match="fitness contract missing"):
        optimizer_fitness_for_request(
            {
                "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V6",
                "optimizer_result_workflow": "QUALIFIED_POOL_OWNER_SELECTION",
            }
        )


def test_future_schema_without_fitness_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported optimizer request schema"):
        optimizer_fitness_for_request(
            {"schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V7"}
        )


def test_future_schema_with_valid_looking_fitness_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported optimizer request schema"):
        optimizer_fitness_for_request(
            {
                "schema": "MAX_REBUILD_OPTIMIZER_REQUEST_V7",
                "optimizer_result_workflow": "QUALIFIED_POOL_OWNER_SELECTION",
                "optimizer_fitness": optimizer_fitness_contract(0.5),
            }
        )


def test_optimizer_fitness_formula_edges_and_negative_mean() -> None:
    assert optimizer_fitness_value(0.2, 25, 0.0) == pytest.approx(0.2)
    assert optimizer_fitness_value(0.2, 25, 0.5) == pytest.approx(1.0)
    assert optimizer_fitness_value(0.2, 25, 1.0) == pytest.approx(5.0)
    assert optimizer_fitness_value(-0.2, 25, 0.5) == pytest.approx(-1.0)
    assert validate_optimizer_trade_exponent(0.5) == pytest.approx(0.5)


def test_v2_custom_fitness_is_distinct_from_mean_r_and_cross_verified(
    tmp_path: Path,
) -> None:
    alpha = 0.5
    row = pass_row(mean_r=0.2, trades=25, custom_fitness=1.0)
    xml = tmp_path / "Max_MTF.xml"
    metrics = tmp_path / "Max_MTF_metrics.csv"
    write_xml(xml, [row])
    write_metrics_v2(metrics, [row], nonce=77, alpha=alpha)

    parsed = parse_optimization_xml(
        xml,
        round_no=1,
        metrics_path=metrics,
        expected_nonce=77,
        optimizer_fitness=optimizer_fitness_contract(alpha),
    )

    assert parsed[0].custom_fitness == pytest.approx(1.0)
    assert parsed[0].expectancy_r == pytest.approx(0.2)
    assert parsed[0].r_sum_r == pytest.approx(5.0)
    assert parsed[0].fitness_schema == OPTIMIZER_FITNESS_SCHEMA
    payload = parsed[0].payload()
    assert payload["custom_fitness"] == pytest.approx(1.0)
    assert payload["expectancy_r"] == pytest.approx(0.2)


def test_v2_mismatched_fitness_evidence_fails_closed(tmp_path: Path) -> None:
    alpha = 0.5
    row = pass_row(mean_r=0.2, trades=25, custom_fitness=1.1)
    xml = tmp_path / "Max_MTF.xml"
    metrics = tmp_path / "Max_MTF_metrics.csv"
    write_xml(xml, [row])
    write_metrics_v2(metrics, [row], nonce=78, alpha=alpha)
    with pytest.raises(ValueError, match="fitness arithmetic mismatch"):
        parse_optimization_xml(
            xml,
            round_no=1,
            metrics_path=metrics,
            expected_nonce=78,
            optimizer_fitness=optimizer_fitness_contract(alpha),
        )


def test_v2_mean_r_arithmetic_mismatch_fails_closed(tmp_path: Path) -> None:
    alpha = 0.5
    row = pass_row(mean_r=0.2, trades=25, sum_r=6.0)
    xml = tmp_path / "Max_MTF.xml"
    metrics = tmp_path / "Max_MTF_metrics.csv"
    row["custom_fitness"] = optimizer_fitness_value(row["mean_r"], row["trades"], alpha)
    write_xml(xml, [row])
    write_metrics_v2(metrics, [row], nonce=79, alpha=alpha)
    with pytest.raises(ValueError, match="Mean-R arithmetic mismatch"):
        parse_optimizer_metrics_csv(
            metrics,
            expected_nonce=79,
            optimizer_fitness=optimizer_fitness_contract(alpha),
        )


def test_custom_fitness_never_changes_kpi_eligibility() -> None:
    common = dict(
        round_no=1,
        pass_no=1,
        profit_factor=1.2,
        recovery_factor=0.3,
        expectancy_r=0.1,
        weighted_r=0.1,
        profit=10.0,
        trades=25,
        params=param_vector(),
        raw={},
        minimum_trades_required=20,
        min_profit_factor_required=1.0,
        min_recovery_factor_required=0.0,
        min_expectancy_r_required=0.0,
        min_weighted_r_required=0.0,
        fitness_schema=OPTIMIZER_FITNESS_SCHEMA,
        trade_exponent_alpha=0.5,
    )
    assert OptimizationPass(custom_fitness=-999.0, **common).eligible
    assert OptimizationPass(custom_fitness=999.0, **common).eligible


def test_legacy_custom_result_remains_mean_r_semantics(tmp_path: Path) -> None:
    row = pass_row(mean_r=0.25, trades=20)
    xml = tmp_path / "legacy.xml"
    metrics = tmp_path / "legacy.csv"
    write_xml(xml, [row])
    write_metrics(metrics, [row], nonce=80)
    parsed = parse_optimization_xml(
        xml,
        round_no=1,
        metrics_path=metrics,
        expected_nonce=80,
    )
    assert parsed[0].expectancy_r == pytest.approx(0.25)
    assert "custom_fitness" not in parsed[0].payload()


def test_v2_zero_trade_fail_closed_sentinel_is_retained_as_ineligible(
    tmp_path: Path,
) -> None:
    alpha = 0.5
    sentinel = core.OPTIMIZER_FAIL_CLOSED_SENTINEL
    row = pass_row(
        pf="",
        rf=0.0,
        mean_r=sentinel,
        weighted_r=sentinel,
        profit=0.0,
        trades=0,
        r_accounted_trades=0,
        sum_r=0.0,
        sum_initial_risk=0.0,
        custom_fitness=sentinel,
    )
    xml = tmp_path / "zero_trade.xml"
    metrics = tmp_path / "zero_trade.csv"
    write_xml(xml, [row])
    write_metrics_v2(metrics, [row], nonce=810, alpha=alpha)

    parsed = parse_optimization_xml(
        xml,
        round_no=1,
        metrics_path=metrics,
        expected_nonce=810,
        optimizer_fitness=optimizer_fitness_contract(alpha),
    )
    apply_frozen_gates(
        parsed,
        {
            "trade_sample": {"minimum_trades": 1},
            "kpi": {
                "min_profit_factor": 1.0,
                "min_recovery_factor": 0.0,
                "min_expectancy_r": 0.0,
                "min_weighted_r": 0.0,
            },
        },
    )

    assert len(parsed) == 1
    assert parsed[0].trades == 0
    assert parsed[0].profit_factor == pytest.approx(0.0)
    assert parsed[0].expectancy_r == pytest.approx(sentinel)
    assert parsed[0].weighted_r == pytest.approx(sentinel)
    assert parsed[0].custom_fitness == pytest.approx(sentinel)
    assert parsed[0].eligible is False


def test_v2_malformed_zero_trade_sentinel_still_fails_closed(tmp_path: Path) -> None:
    alpha = 0.5
    sentinel = core.OPTIMIZER_FAIL_CLOSED_SENTINEL
    row = pass_row(
        mean_r=sentinel,
        weighted_r=sentinel,
        profit=0.0,
        trades=0,
        r_accounted_trades=0,
        sum_r=0.0,
        sum_initial_risk=0.0,
        custom_fitness=-123.0,
    )
    metrics = tmp_path / "malformed_zero_trade.csv"
    write_metrics_v2(metrics, [row], nonce=811, alpha=alpha)
    with pytest.raises(ValueError, match="Invalid R-accounted trade count"):
        parse_optimizer_metrics_csv(
            metrics,
            expected_nonce=811,
            optimizer_fitness=optimizer_fitness_contract(alpha),
        )


def test_v2_positive_trade_zero_risk_still_fails_closed(tmp_path: Path) -> None:
    alpha = 0.5
    row = pass_row(sum_initial_risk=0.0)
    metrics = tmp_path / "positive_trade_zero_risk.csv"
    write_metrics_v2(metrics, [row], nonce=812, alpha=alpha)
    with pytest.raises(ValueError, match="Invalid summed initial risk"):
        parse_optimizer_metrics_csv(
            metrics,
            expected_nonce=812,
            optimizer_fitness=optimizer_fitness_contract(alpha),
        )


def test_v2_invalid_accounting_evidence_fails_closed(tmp_path: Path) -> None:
    alpha = 0.5
    row = pass_row(accounting_errors=1)
    row["custom_fitness"] = optimizer_fitness_value(
        row["mean_r"],
        row["trades"],
        alpha,
    )
    metrics = tmp_path / "v2_bad_accounting.csv"
    write_metrics_v2(metrics, [row], nonce=81, alpha=alpha)
    with pytest.raises(ValueError, match="R-accounting errors"):
        parse_optimizer_metrics_csv(
            metrics,
            expected_nonce=81,
            optimizer_fitness=optimizer_fitness_contract(alpha),
        )


def test_ea_source_implements_v2_fitness_without_strategy_dimension_mutation() -> None:
    source = core.EA_BASELINE.read_text(encoding="utf-8")
    assert '#property version   "2.11"' in source
    assert "input double InpOptimizerTradeExponent = 0.50;" in source
    assert (
        "custom_fitness=mean_r*MathPow((double)g_optimizerClosedTrades,"
        "InpOptimizerTradeExponent);"
    ) in source
    assert 'FrameAdd("MAX_R_METRICS_V2",InpOptimizerRunNonce,custom_fitness,metrics)' in source
    assert "return valid ? custom_fitness : -1.0e9;" in source
    assert "mt5_trades==(long)g_optimizerClosedTrades" in source
    assert "g_optimizerAccountingErrors==0" in source
    assert "g_optimizerSumRisk>0.0" in source
    assert "InpOptimizerTradeExponent" not in ABSOLUTE_BOUNDS


def test_auto_trade_sample_matches_h1_sqrt_authority() -> None:
    h1 = trade_sample("H1", "2026.01.01", "2026.02.01", DEFAULT_KPI)
    h4 = trade_sample("H4", "2026.01.01", "2026.02.01", DEFAULT_KPI)
    m5 = trade_sample("M5", "2026.01.01", "2026.02.01", DEFAULT_KPI)
    assert h1["scaled_trades_per_month"] == 20
    assert h4["scaled_trades_per_month"] == 10
    assert m5["scaled_trades_per_month"] == 70
    assert h1["minimum_trades"] >= 20


def test_tester_ini_modes_and_fixed_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    request = freeze(monkeypatch)
    genetic = build_tester_ini(request, expert_name=r"MaxMTF\Max_MTF")
    assert "Optimization=2" in genetic
    assert "OptimizationCriterion=6" in genetic
    assert "ForwardMode=0" in genetic
    assert "UseCloud=0" in genetic
    assert "Visual=0" in genetic
    assert "Model=1" in genetic

    request["optimization"] = 1
    complete = build_tester_ini(request, expert_name=r"MaxMTF\Max_MTF")
    assert "Optimization=1" in complete


def test_historical_v5_parameter_vector_remains_readable(tmp_path: Path) -> None:
    legacy = core.read_ea_optimizer_defaults(bounds=LEGACY_ABSOLUTE_BOUNDS)
    assert len(legacy) == 16
    assert "InpRiskPct" not in legacy
    assert len(parameter_signature(legacy)) == 16

    metrics = tmp_path / "legacy_v5_metrics.csv"
    alpha = 0.5
    mean_r = 0.2
    trades = 25
    weighted_r = 0.1
    profit = 10.0
    risk = profit / weighted_r
    with metrics.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "evidence_schema","fitness_schema","frame_pass_id","frame_inputs",
                "custom_fitness","trade_exponent_alpha","mean_expectancy_r",
                "weighted_r","mt5_trades","r_accounted_trades","sum_r",
                "sum_net","sum_initial_risk","accounting_errors","run_nonce",
            ],
        )
        writer.writeheader()
        writer.writerow({
            "evidence_schema": OPTIMIZER_METRICS_SCHEMA_V2,
            "fitness_schema": OPTIMIZER_FITNESS_SCHEMA,
            "frame_pass_id": 7,
            "frame_inputs": "|".join(
                f"{name}={legacy[name]}" for name in LEGACY_ABSOLUTE_BOUNDS
            ),
            "custom_fitness": optimizer_fitness_value(mean_r, trades, alpha),
            "trade_exponent_alpha": alpha,
            "mean_expectancy_r": mean_r,
            "weighted_r": weighted_r,
            "mt5_trades": trades,
            "r_accounted_trades": trades,
            "sum_r": mean_r * trades,
            "sum_net": profit,
            "sum_initial_risk": risk,
            "accounting_errors": 0,
            "run_nonce": 123,
        })

    parsed = parse_optimizer_metrics_csv(
        metrics,
        expected_nonce=123,
        optimizer_fitness=optimizer_fitness_contract(alpha),
        parameter_bounds=LEGACY_ABSOLUTE_BOUNDS,
    )
    assert len(parsed) == 1
    record = next(iter(parsed.values()))
    assert record["frame_params"] == legacy


def test_parameter_signature_is_exact_vector_identity() -> None:
    params = param_vector()
    signature = parameter_signature(params)
    assert len(signature) == 17
    assert ("InpRiskPct", 0.5) in signature
    assert signature[0][0] == "InpWeightTrend"


def test_xml_and_sidecar_join_by_parameter_vector_not_pass_number(tmp_path: Path) -> None:
    nonce = 777
    row = pass_row(pass_no=7, frame_pass_id=18_446_744_073_709_551_000)
    xml = tmp_path / "Max_MTF.xml"
    metrics = tmp_path / "Max_MTF_metrics.csv"
    write_xml(xml, [row])
    write_metrics(metrics, [row], nonce=nonce)

    parsed = parse_optimization_xml(
        xml,
        round_no=1,
        metrics_path=metrics,
        expected_nonce=nonce,
    )
    assert parsed[0].pass_no == 7
    assert parsed[0].frame_pass_id == 18_446_744_073_709_551_000
    assert parsed[0].weighted_r == pytest.approx(0.1)


def test_subset_xml_reconstructs_full_vector_from_frozen_defaults(tmp_path: Path) -> None:
    nonce = 888
    params = param_vector(InpEntryThreshold=0.20)
    xml = tmp_path / "Max_MTF.xml"
    metrics = tmp_path / "Max_MTF_metrics.csv"
    xml.write_text(
        '<?xml version="1.0"?>'
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
        'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
        '<Worksheet><Table>'
        '<Row>'
        '<Cell><Data ss:Type="String">Pass</Data></Cell>'
        '<Cell><Data ss:Type="String">Result</Data></Cell>'
        '<Cell><Data ss:Type="String">Profit</Data></Cell>'
        '<Cell><Data ss:Type="String">Profit Factor</Data></Cell>'
        '<Cell><Data ss:Type="String">Recovery Factor</Data></Cell>'
        '<Cell><Data ss:Type="String">Custom</Data></Cell>'
        '<Cell><Data ss:Type="String">Trades</Data></Cell>'
        '<Cell><Data ss:Type="String">InpEntryThreshold</Data></Cell>'
        '</Row>'
        '<Row>'
        '<Cell><Data ss:Type="Number">1</Data></Cell>'
        '<Cell><Data ss:Type="Number">0.2</Data></Cell>'
        '<Cell><Data ss:Type="Number">10.0</Data></Cell>'
        '<Cell><Data ss:Type="Number">1.2</Data></Cell>'
        '<Cell><Data ss:Type="Number">0.5</Data></Cell>'
        '<Cell><Data ss:Type="Number">0.2</Data></Cell>'
        '<Cell><Data ss:Type="Number">25</Data></Cell>'
        '<Cell><Data ss:Type="Number">0.20</Data></Cell>'
        '</Row>'
        '</Table></Worksheet></Workbook>',
        encoding="utf-8",
    )
    row = pass_row(
        pass_no=1,
        params=params,
        mean_r=0.2,
        profit=10.0,
        trades=25,
        weighted_r=0.1,
    )
    write_metrics(metrics, [row], nonce=nonce)
    fixed = param_vector()
    parsed = parse_optimization_xml(
        xml,
        round_no=1,
        metrics_path=metrics,
        expected_nonce=nonce,
        fixed_param_values=fixed,
        optimize_params=["InpEntryThreshold"],
    )
    assert len(parsed) == 1
    assert parsed[0].params == params
    assert parsed[0].weighted_r == pytest.approx(0.1)


def test_missing_recovery_factor_fails_closed(tmp_path: Path) -> None:
    row = pass_row()
    xml = tmp_path / "bad.xml"
    metrics = tmp_path / "m.csv"
    write_xml(xml, [row], include_rf=False)
    write_metrics(metrics, [row], nonce=1)
    with pytest.raises(ValueError, match="Recovery Factor"):
        parse_optimization_xml(xml, round_no=1, metrics_path=metrics, expected_nonce=1)


def test_wrong_nonce_fails_closed(tmp_path: Path) -> None:
    row = pass_row(run_nonce=999)
    metrics = tmp_path / "m.csv"
    write_metrics(metrics, [row], nonce=1)
    with pytest.raises(ValueError, match="nonce mismatch"):
        parse_optimizer_metrics_csv(metrics, expected_nonce=1)


def test_duplicate_parameter_vector_fails_closed(tmp_path: Path) -> None:
    a = pass_row(frame_pass_id=1)
    b = pass_row(frame_pass_id=2)
    metrics = tmp_path / "m.csv"
    write_metrics(metrics, [a, b], nonce=1)
    with pytest.raises(ValueError, match="Duplicate optimizer parameter-vector"):
        parse_optimizer_metrics_csv(metrics, expected_nonce=1)


def test_accounting_error_fails_closed(tmp_path: Path) -> None:
    row = pass_row(accounting_errors=1)
    metrics = tmp_path / "m.csv"
    write_metrics(metrics, [row], nonce=1)
    with pytest.raises(ValueError, match="R-accounting errors"):
        parse_optimizer_metrics_csv(metrics, expected_nonce=1)


def test_weighted_r_arithmetic_mismatch_fails_closed(tmp_path: Path) -> None:
    row = pass_row(weighted_r=0.2, sum_initial_risk=100.0)
    metrics = tmp_path / "m.csv"
    write_metrics(metrics, [row], nonce=1)
    with pytest.raises(ValueError, match="Weighted-R arithmetic mismatch"):
        parse_optimizer_metrics_csv(metrics, expected_nonce=1)


def test_trade_count_xml_sidecar_mismatch_fails_closed(tmp_path: Path) -> None:
    xml_row = pass_row(trades=25)
    side_row = pass_row(trades=24)
    xml = tmp_path / "Max_MTF.xml"
    metrics = tmp_path / "m.csv"
    write_xml(xml, [xml_row])
    write_metrics(metrics, [side_row], nonce=1)
    with pytest.raises(ValueError, match="Trade-count XML/frame mismatch"):
        parse_optimization_xml(xml, round_no=1, metrics_path=metrics, expected_nonce=1)


def test_sidecar_extra_vector_fails_closed(tmp_path: Path) -> None:
    a = pass_row(params=param_vector(InpEntryThreshold=0.18))
    b = pass_row(
        pass_no=2,
        params=param_vector(InpEntryThreshold=0.20),
        profit=20.0,
        weighted_r=0.2,
    )
    xml = tmp_path / "Max_MTF.xml"
    metrics = tmp_path / "m.csv"
    write_xml(xml, [a])
    write_metrics(metrics, [a, b], nonce=1)
    with pytest.raises(ValueError, match="not present in XML"):
        parse_optimization_xml(xml, round_no=1, metrics_path=metrics, expected_nonce=1)


def test_missing_weighted_row_blocks_winner_selection(tmp_path: Path) -> None:
    a = pass_row(
        pass_no=1,
        params=param_vector(InpEntryThreshold=0.18),
        weighted_r=0.1,
    )
    b = pass_row(
        pass_no=2,
        params=param_vector(InpEntryThreshold=0.20),
        weighted_r=0.5,
        profit=20.0,
    )
    xml = tmp_path / "Max_MTF.xml"
    metrics = tmp_path / "m.csv"
    write_xml(xml, [a, b])
    write_metrics(metrics, [a], nonce=1)
    rows = parse_optimization_xml(xml, round_no=1, metrics_path=metrics, expected_nonce=1)
    for row in rows:
        row.minimum_trades_required = 20
    assert len(unresolved_weighted_contenders(rows)) == 1
    assert select_winner(rows) is None


def test_inclusive_eligibility_boundaries() -> None:
    row = OptimizationPass(
        round_no=1,
        pass_no=1,
        profit_factor=1.0,
        recovery_factor=0.0,
        expectancy_r=0.0,
        weighted_r=0.0,
        profit=0.0,
        trades=20,
        params=param_vector(),
        raw={},
        minimum_trades_required=20,
        min_profit_factor_required=1.0,
        min_recovery_factor_required=0.0,
        min_expectancy_r_required=0.0,
        min_weighted_r_required=0.0,
    )
    assert row.eligible


def test_deterministic_ranking_exact_tie_order() -> None:
    base = dict(
        round_no=1,
        profit=10.0,
        trades=30,
        params=param_vector(),
        raw={},
        minimum_trades_required=20,
        min_profit_factor_required=1.0,
        min_recovery_factor_required=0.0,
        min_expectancy_r_required=0.0,
        min_weighted_r_required=0.0,
    )
    rows = [
        OptimizationPass(pass_no=5, weighted_r=0.3, expectancy_r=0.2, profit_factor=1.3, recovery_factor=0.4, **base),
        OptimizationPass(pass_no=4, weighted_r=0.3, expectancy_r=0.2, profit_factor=1.3, recovery_factor=0.4, **base),
        OptimizationPass(pass_no=3, weighted_r=0.2, expectancy_r=9.0, profit_factor=9.0, recovery_factor=9.0, **base),
    ]
    assert select_winner(rows).pass_no == 4


def test_eligibility_audit_and_refinement_preserve_universe() -> None:
    row = OptimizationPass(
        round_no=1,
        pass_no=1,
        profit_factor=0.9,
        recovery_factor=0.2,
        expectancy_r=0.1,
        weighted_r=-0.1,
        profit=10.0,
        trades=25,
        params=param_vector(),
        raw={},
        minimum_trades_required=20,
        min_profit_factor_required=1.0,
        min_recovery_factor_required=0.0,
        min_expectancy_r_required=0.0,
        min_weighted_r_required=0.0,
    )
    audit = eligibility_audit([row])
    assert audit["eligible_passes"] == 0
    refined = deterministic_refine(DEFAULT_SPACE, [row], ["InpEntryThreshold"])
    assert set(refined) == set(ABSOLUTE_BOUNDS)
    assert refined["InpWeightTrend"] == DEFAULT_SPACE["InpWeightTrend"]


def test_subset_optimization_xml_reconstructs_frozen_full_vector(tmp_path: Path) -> None:
    nonce = 2108284765
    params = param_vector(InpEntryThreshold=0.20)
    row = pass_row(
        pass_no=1,
        params=params,
        mean_r=0.27695026605812195,
        weighted_r=0.2810658389881225,
        profit=277.10,
        trades=20,
        pf=1.764899,
        rf=1.351180,
    )
    xml = tmp_path / "Max_MTF.xml"
    metrics = tmp_path / "Max_MTF_metrics.csv"

    headers = [
        "Pass", "Result", "Profit", "Expected Payoff", "Profit Factor",
        "Recovery Factor", "Sharpe Ratio", "Custom", "Equity DD %",
        "Trades", "InpEntryThreshold",
    ]
    values = [
        1, 0.28, 277.10, 13.855, 1.764899, 1.351180, 4.267820,
        row["mean_r"], 2.0015, 20, 0.20,
    ]

    def xml_row(items):
        return "<Row>" + "".join(
            f'<Cell><Data ss:Type="String">{value}</Data></Cell>'
            for value in items
        ) + "</Row>"

    xml.write_text(
        '<?xml version="1.0"?><Workbook '
        'xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
        'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
        '<Worksheet><Table>'
        + xml_row(headers)
        + xml_row(values)
        + '</Table></Worksheet></Workbook>',
        encoding="utf-8",
    )
    write_metrics(metrics, [row], nonce=nonce)

    parsed = parse_optimization_xml(
        xml,
        round_no=1,
        metrics_path=metrics,
        expected_nonce=nonce,
        fixed_param_values=param_vector(),
        optimize_params=["InpEntryThreshold"],
    )

    assert len(parsed) == 1
    assert parsed[0].params == params
    assert parsed[0].weighted_r == pytest.approx(row["weighted_r"])
