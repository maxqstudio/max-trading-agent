from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import max_backend.optimizer_core as core
import max_backend.optimizer_runtime as runtime
from max_backend.optimizer_core import (
    ABSOLUTE_BOUNDS,
    DEFAULT_SPACE,
    freeze_request,
    parse_optimization_xml,
    parse_optimizer_metrics_csv,
)
from max_backend.optimizer_runtime import clear_stale_sidecar, compile_ea, launch_mt5


def request_for_compile(source: Path, tmp_path: Path) -> dict:
    install = tmp_path / "install"
    data = tmp_path / "data"
    install.mkdir()
    data.mkdir()
    meta = install / "metaeditor64.exe"
    terminal = install / "terminal64.exe"
    meta.write_bytes(b"MZ")
    terminal.write_bytes(b"MZ")
    return {
        "ea": {
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        "mt5": {
            "terminal": str(terminal),
            "metaeditor": str(meta),
            "data_root": str(data),
        },
    }


def patch_compile_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    log_text: str,
    create_ex5: bool,
    returncode: int = 0,
):
    source = tmp_path / "Max_MTF.mq5"
    source.write_text("// frozen ea", encoding="utf-8")
    request = request_for_compile(source, tmp_path)
    monkeypatch.setattr(runtime, "EA_BASELINE", source)
    monkeypatch.setattr(runtime, "OPTIMIZER_EVIDENCE_ROOT", tmp_path / "evidence")

    def fake_run(command, **kwargs):
        deployed = Path(request["mt5"]["data_root"]) / "MQL5" / "Experts" / "MaxMTF" / "Max_MTF.mq5"
        deployed.with_suffix(".log").write_text(log_text, encoding="utf-8")
        if create_ex5:
            deployed.with_suffix(".ex5").write_bytes(b"compiled")
        return SimpleNamespace(returncode=returncode, stdout="", stderr="")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    return source, request


def test_ea_tamper_after_request_freeze_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, request = patch_compile_environment(
        monkeypatch,
        tmp_path,
        log_text="Result: 0 errors, 0 warnings",
        create_ex5=True,
    )
    source.write_text("// tampered", encoding="utf-8")
    with pytest.raises(RuntimeError, match="EA_CHANGED_AFTER_REQUEST_FREEZE"):
        compile_ea(request, "JOB")


def test_metaeditor_unavailable_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "Max_MTF.mq5"
    source.write_text("// frozen", encoding="utf-8")
    request = request_for_compile(source, tmp_path)
    Path(request["mt5"]["metaeditor"]).unlink()
    monkeypatch.setattr(runtime, "EA_BASELINE", source)
    monkeypatch.setattr(runtime, "OPTIMIZER_EVIDENCE_ROOT", tmp_path / "evidence")

    with pytest.raises(FileNotFoundError, match="METAEDITOR_UNAVAILABLE"):
        compile_ea(request, "JOB")


def test_compile_summary_missing_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _source, request = patch_compile_environment(
        monkeypatch,
        tmp_path,
        log_text="MetaEditor started and stopped",
        create_ex5=True,
    )
    with pytest.raises(RuntimeError, match="METAEDITOR_COMPILE_SUMMARY_MISSING"):
        compile_ea(request, "JOB")


def test_compile_errors_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _source, request = patch_compile_environment(
        monkeypatch,
        tmp_path,
        log_text="Result: 2 errors, 1 warnings",
        create_ex5=True,
    )
    with pytest.raises(RuntimeError, match="METAEDITOR_COMPILE_ERRORS"):
        compile_ea(request, "JOB")


def test_missing_ex5_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _source, request = patch_compile_environment(
        monkeypatch,
        tmp_path,
        log_text="Result: 0 errors, 0 warnings",
        create_ex5=False,
    )
    with pytest.raises(RuntimeError, match="METAEDITOR_EX5_MISSING"):
        compile_ea(request, "JOB")


def test_metaeditor_return_code_is_diagnostic_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _source, request = patch_compile_environment(
        monkeypatch,
        tmp_path,
        log_text="Result: 0 errors, 0 warnings",
        create_ex5=True,
        returncode=9,
    )
    result = compile_ea(request, "JOB")
    assert result["status"] == "PASS"
    assert result["process_returncode"] == 9
    assert result["compile_summary"]["errors"] == 0


def test_preexisting_mt5_terminal_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = tmp_path / "terminal64.exe"
    terminal.write_bytes(b"MZ")
    ini = tmp_path / "round.ini"
    ini.write_text("[Tester]", encoding="utf-8")
    request = {"mt5": {"terminal": str(terminal)}}
    monkeypatch.setattr(runtime, "_matching_terminal_pids", lambda _terminal: [4321])
    called = {"run": False}

    def forbidden_run(*args, **kwargs):
        called["run"] = True
        raise AssertionError("MT5 subprocess must not launch while configured terminal is already running")

    monkeypatch.setattr(runtime.subprocess, "run", forbidden_run)
    with pytest.raises(RuntimeError, match="MT5_TERMINAL_ALREADY_RUNNING"):
        launch_mt5(request, ini_path=ini, timeout_sec=10)
    assert called["run"] is False


def test_mt5_nonzero_exit_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = tmp_path / "terminal64.exe"
    terminal.write_bytes(b"MZ")
    ini = tmp_path / "round.ini"
    ini.write_text("[Tester]", encoding="utf-8")
    request = {"mt5": {"terminal": str(terminal)}}
    monkeypatch.setattr(runtime, "_matching_terminal_pids", lambda _terminal: [])
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=7),
    )
    with pytest.raises(RuntimeError, match="MT5_EXECUTION_FAILURE"):
        launch_mt5(request, ini_path=ini, timeout_sec=10)


def test_stale_sidecar_clear_failure_is_not_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sidecar = tmp_path / "Max_MTF_metrics.csv"
    sidecar.write_text("stale", encoding="utf-8")
    original = Path.unlink

    def fail_unlink(self, *args, **kwargs):
        if self == sidecar:
            raise PermissionError("locked")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(RuntimeError, match="STALE_SIDECAR_CANNOT_BE_CLEARED"):
        clear_stale_sidecar({"optimizer_metrics_path": str(sidecar)})


def test_freeze_rejects_mt5_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        core,
        "detect_mt5",
        lambda: {"status": "UNAVAILABLE", "reason": "DATA_ROOT_MISSING"},
    )
    request = {
        "symbol": "XAUUSD.m",
        "relative_symbol": "EURUSD.m",
        "period": "H1",
        "from_date": "2026.01.01",
        "to_date": "2026.02.01",
        "optimize_params": ["InpEntryThreshold"],
        "search_space": DEFAULT_SPACE,
    }
    with pytest.raises(RuntimeError, match="DATA_ROOT_MISSING"):
        freeze_request(request)


def full_params() -> dict:
    return core.read_ea_optimizer_defaults()


def write_xml(path: Path, *, include_row: bool = True, pf: str = "1.2") -> None:
    headers = [
        "Pass",
        "Profit Factor",
        "Recovery Factor",
        "Custom",
        "Profit",
        "Trades",
        *ABSOLUTE_BOUNDS,
    ]
    rows = [headers]
    if include_row:
        params = full_params()
        rows.append([1, pf, 0.5, 0.2, 10.0, 25, *[params[name] for name in ABSOLUTE_BOUNDS]])

    def xml_row(values):
        return "<Row>" + "".join(
            f'<Cell><Data ss:Type="String">{value}</Data></Cell>' for value in values
        ) + "</Row>"

    path.write_text(
        '<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
        'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet><Table>'
        + "".join(xml_row(row) for row in rows)
        + "</Table></Worksheet></Workbook>",
        encoding="utf-8",
    )


def write_metric(path: Path, *, weighted: str = "0.1", nonce: int = 1) -> None:
    params = full_params()
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
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
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "frame_pass_id": 12345678901234567,
                "frame_inputs": "|".join(f"{name}={params[name]}" for name in ABSOLUTE_BOUNDS),
                "mean_expectancy_r": 0.2,
                "weighted_r": weighted,
                "mt5_trades": 25,
                "r_accounted_trades": 25,
                "sum_net": 10.0,
                "sum_initial_risk": 100.0,
                "accounting_errors": 0,
                "run_nonce": nonce,
            }
        )


def test_missing_sidecar_fails_closed(tmp_path: Path) -> None:
    xml = tmp_path / "Max_MTF.xml"
    write_xml(xml)
    with pytest.raises(FileNotFoundError):
        parse_optimization_xml(
            xml,
            round_no=1,
            metrics_path=tmp_path / "missing.csv",
            expected_nonce=1,
        )


def test_malformed_xml_fails_closed(tmp_path: Path) -> None:
    xml = tmp_path / "Max_MTF.xml"
    metrics = tmp_path / "m.csv"
    xml.write_text("<Workbook>" + ("x" * 80), encoding="utf-8")
    write_metric(metrics)
    with pytest.raises(ValueError, match="Malformed"):
        parse_optimization_xml(xml, round_no=1, metrics_path=metrics, expected_nonce=1)


def test_zero_passes_fails_closed(tmp_path: Path) -> None:
    xml = tmp_path / "Max_MTF.xml"
    metrics = tmp_path / "m.csv"
    write_xml(xml, include_row=False)
    write_metric(metrics)
    with pytest.raises(ValueError, match="zero parseable passes"):
        parse_optimization_xml(xml, round_no=1, metrics_path=metrics, expected_nonce=1)


def test_nonfinite_native_metric_fails_closed(tmp_path: Path) -> None:
    xml = tmp_path / "Max_MTF.xml"
    metrics = tmp_path / "m.csv"
    write_xml(xml, pf="nan")
    write_metric(metrics)
    with pytest.raises(ValueError, match="Non-finite XML metric"):
        parse_optimization_xml(xml, round_no=1, metrics_path=metrics, expected_nonce=1)


def test_nonfinite_weighted_metric_fails_closed(tmp_path: Path) -> None:
    metrics = tmp_path / "m.csv"
    write_metric(metrics, weighted="nan")
    with pytest.raises(ValueError, match="Non-finite optimizer R metrics"):
        parse_optimizer_metrics_csv(metrics, expected_nonce=1)
