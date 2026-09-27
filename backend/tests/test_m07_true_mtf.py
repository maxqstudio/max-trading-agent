from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

import max_backend.optimizer_core as core
from max_backend.workflow_contract import OPTIMIZER_REQUEST_SCHEMA_CURRENT
from max_backend.challenger_bundle import verify_geometry_set, write_challenger_set
from max_backend.mtf_geometry import (
    ALLOWED_MAIN_TIMEFRAMES,
    STRATEGY_CONTRACT,
    SUPPORTED_MT5_ROLE_TIMEFRAMES,
    frozen_geometry_inputs,
    fuse_role_observations,
    latest_fully_closed_open_time,
    resolve_strategy_geometry,
)


def fake_mt5() -> dict:
    return {
        "status": "READY_EXECUTABLE_AND_DATA_ROOT",
        "reason": "READY",
        "terminal": r"C:\Fake\MetaTrader 5\terminal64.exe",
        "metaeditor": r"C:\Fake\MetaTrader 5\metaeditor64.exe",
        "data_root": r"C:\Fake\TerminalData",
    }


def request_payload(period: str) -> dict:
    return {
        "symbol": "XAUUSD.m",
        "relative_symbol": "EURUSD.m",
        "period": period,
        "from_date": "2026.01.01",
        "to_date": "2026.02.01",
        "deposit": 10000,
        "leverage": 100,
        "model": 1,
        "optimization": 1,
        "max_rounds": 1,
        "optimize_params": ["InpMinConsensus"],
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


@pytest.mark.parametrize(
    ("main_tf", "context", "structure", "timing"),
    [
        ("M15", "H4", "H1", "M5"),
        ("M30", "H8", "H2", "M10"),
        ("H1", "H12", "H4", "M20"),
    ],
)
def test_dynamic_formation_exact_contract(
    main_tf: str,
    context: str,
    structure: str,
    timing: str,
) -> None:
    geometry = resolve_strategy_geometry(main_tf)
    assert geometry["contract"] == STRATEGY_CONTRACT
    assert geometry["context_tf"] == context
    assert geometry["structure_tf"] == structure
    assert geometry["main_tf"] == main_tf
    assert geometry["timing_tf"] == timing
    assert geometry["closed_bar_only"] is True
    assert geometry["decision_cadence"] == "MAIN_TF"


@pytest.mark.parametrize("main_tf", ["M1", "M5", "M10", "M12"])
def test_main_below_m15_rejected(main_tf: str) -> None:
    with pytest.raises(ValueError, match="BELOW_M15"):
        resolve_strategy_geometry(main_tf)


def test_m15_is_minimum_accepted_main() -> None:
    assert "M15" in ALLOWED_MAIN_TIMEFRAMES
    assert resolve_strategy_geometry("M15")["timing_tf"] == "M5"


def test_every_allowed_main_has_four_distinct_ordered_roles() -> None:
    for main_tf in ALLOWED_MAIN_TIMEFRAMES:
        g = resolve_strategy_geometry(main_tf)
        values = [
            g["timing_minutes"],
            g["main_minutes"],
            g["structure_minutes"],
            g["context_minutes"],
        ]
        assert len(set(values)) == 4
        assert 5 <= values[0] < values[1] < values[2] < values[3]


def test_latest_fully_closed_exact_boundary_is_eligible() -> None:
    # H1 bar opened at 10:00 closes exactly at 11:00.
    opens = [9 * 3600, 10 * 3600, 11 * 3600]
    assert latest_fully_closed_open_time(
        opens,
        timeframe_minutes=60,
        decision_time=11 * 3600,
    ) == 10 * 3600


def test_incomplete_higher_timeframe_bar_is_never_selected() -> None:
    # H4 08:00 bar is still open at 10:15. Latest legal H4 is 04:00.
    opens = [0, 4 * 3600, 8 * 3600]
    selected = latest_fully_closed_open_time(
        opens,
        timeframe_minutes=240,
        decision_time=10 * 3600 + 15 * 60,
    )
    assert selected == 4 * 3600
    assert selected + 240 * 60 <= 10 * 3600 + 15 * 60


def test_no_role_can_select_future_close() -> None:
    decision = 12 * 3600
    for tf in (5, 15, 60, 240):
        opens = list(range(0, decision + tf * 60, tf * 60))
        selected = latest_fully_closed_open_time(
            opens,
            timeframe_minutes=tf,
            decision_time=decision,
        )
        assert selected + tf * 60 <= decision


def test_higher_tf_can_change_fused_strategy_family_with_main_constant() -> None:
    main = (0.20, 0.80)
    baseline = fuse_role_observations(
        [(0.10, 0.5), (0.10, 0.5), main, (0.10, 0.5)]
    )
    changed_context = fuse_role_observations(
        [(-1.0, 1.0), (0.10, 0.5), main, (0.10, 0.5)]
    )
    assert changed_context["signal"] != pytest.approx(baseline["signal"])


def test_timing_tf_can_change_fused_strategy_family_with_main_constant() -> None:
    main = (0.20, 0.80)
    baseline = fuse_role_observations(
        [(0.10, 0.5), (0.10, 0.5), main, (0.10, 0.5)]
    )
    changed_timing = fuse_role_observations(
        [(0.10, 0.5), (0.10, 0.5), main, (-1.0, 1.0)]
    )
    assert changed_timing["signal"] != pytest.approx(baseline["signal"])


def test_optimizer_freezes_exact_geometry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "detect_mt5", fake_mt5)
    request = core.freeze_request(request_payload("M30"))
    assert request["schema"] == OPTIMIZER_REQUEST_SCHEMA_CURRENT
    assert request["strategy_contract"] == STRATEGY_CONTRACT
    assert request["strategy_geometry"] == resolve_strategy_geometry("M30")
    text = core.build_set_text(
        request,
        request["search_space"],
        optimizer_run_nonce=123,
    )
    for name, value in frozen_geometry_inputs(request).items():
        assert f"{name}={value}" in text


@pytest.mark.parametrize("period", ["M1", "M5", "M10", "M12"])
def test_optimizer_rejects_invalid_main_timeframe(
    monkeypatch: pytest.MonkeyPatch,
    period: str,
) -> None:
    monkeypatch.setattr(core, "detect_mt5", fake_mt5)
    with pytest.raises(ValueError, match="main timeframe"):
        core.freeze_request(request_payload(period))


def test_challenger_set_retains_exact_geometry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(core, "detect_mt5", fake_mt5)
    request = core.freeze_request(request_payload("H1"))
    params = core.read_ea_optimizer_defaults()
    path = tmp_path / "challenger.set"
    audit = write_challenger_set(path, params, request)
    assert audit["strategy_contract"] == STRATEGY_CONTRACT
    assert audit["strategy_geometry"] == resolve_strategy_geometry("H1")
    verified = verify_geometry_set(path, request)
    assert verified["strategy_geometry"] == request["strategy_geometry"]


def test_ea_source_is_role_aware_and_preserves_32_feature_boundary() -> None:
    source = (Path(__file__).resolve().parents[2] / "ea" / "baseline" / "Max_MTF.mq5").read_text(
        encoding="utf-8"
    )
    assert '#property version   "2.11"' in source
    assert '#define MAX_MTF_STRATEGY_CONTRACT "MAX_TRUE_MTF_DYNAMIC_V1"' in source
    assert "#define ONNX_FEATURE_COUNT 32" in source
    assert '#define MAX_MTF_FEATURE_CONTRACT "CP32_TRUE_MTF_V1"' in source
    assert "bool BuildSnapshot(" not in source
    assert "BuildRoleSnapshot(MTF_ROLE_CONTEXT" in source
    assert "BuildRoleSnapshot(MTF_ROLE_STRUCTURE" in source
    assert "BuildRoleSnapshot(MTF_ROLE_MAIN" in source
    assert "BuildRoleSnapshot(MTF_ROLE_TIMING" in source
    assert "LatestFullyClosedBarShift" in source
    assert "datetime BarCloseTime(" in source
    assert "close_time>0 && close_time<=decision_time" in source
    assert "PeriodSeconds(MN1)" in source
    assert "FuseFamilies(context_f,structure_f,main_f,timing_f,families)" in source
    assert "RuleMetaScore(families,consensus)" in source
    assert "BuildFeatures(main_s,families,rule_score,features)" in source


def test_ea_relative_family_uses_role_timeframe_for_both_symbols() -> None:
    source = (Path(__file__).resolve().parents[2] / "ea" / "baseline" / "Max_MTF.mq5").read_text(
        encoding="utf-8"
    )
    start = source.index("FamilySignal SignalRelative(")
    end = source.index("bool FamilyParticipatesOnRole", start)
    block = source[start:end]
    assert "const ENUM_TIMEFRAMES tf" in block
    assert "LatestFullyClosedBarShift(_Symbol,tf,decision_time)" in block
    assert "LatestFullyClosedBarShift(InpConfirmSymbol,tf,decision_time)" in block
    assert "CopyRates(_Symbol,tf" in block
    assert "CopyRates(InpConfirmSymbol,tf" in block
    assert "for(int i=0;i<=n;i++)" in block
    assert "_Period" not in block


def test_backend_and_ea_share_supported_timeframe_table_contract() -> None:
    source = (Path(__file__).resolve().parents[2] / "ea" / "baseline" / "Max_MTF.mq5").read_text(
        encoding="utf-8"
    )
    ordered_minutes = list(SUPPORTED_MT5_ROLE_TIMEFRAMES.values())
    initializer = "{" + ",".join(str(v) for v in ordered_minutes) + "}"
    assert f"int supported[{len(ordered_minutes)}]={initializer};" in source
