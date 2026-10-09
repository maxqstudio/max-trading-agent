from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from max_backend.optimizer_core import (
    ABSOLUTE_BOUNDS,
    CURRENT_OPTIMIZER_SCHEMA,
    DEFAULT_SPACE,
    OPTIMIZER_FITNESS_SCHEMA,
    OPTIMIZER_METRICS_SCHEMA_V2,
    optimizer_fitness_contract,
    parse_optimizer_report_preview,
    sha256_file,
)
from max_backend.optimizer_jobs import _verified_raw_report_preview


METRIC_FIELDS = [
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


def make_request() -> dict:
    params = {
        name: (int(bounds[0]) if bounds[3] == "int" else bounds[0])
        for name, bounds in ABSOLUTE_BOUNDS.items()
    }
    return {
        "schema": CURRENT_OPTIMIZER_SCHEMA,
        "optimizer_fitness": optimizer_fitness_contract(0.5),
        "symbol": "EURUSD.m",
        "relative_symbol": "GBPUSD.m",
        "period": "H1",
        "from_date": "2021.01.01",
        "to_date": "2024.12.31",
        "optimize_params": list(ABSOLUTE_BOUNDS),
        "fixed_param_values": params,
        "search_space": DEFAULT_SPACE,
    }


def params_for(request: dict, *, weight_trend: float | None = None) -> dict:
    params = dict(request["fixed_param_values"])
    if weight_trend is not None:
        params["InpWeightTrend"] = weight_trend
    return params


def write_metric_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=METRIC_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def metric_row(params: dict, *, nonce: int, frame: int, custom: float = 0.5) -> dict:
    alpha = 0.5
    trades = 4
    mean_r = custom / (trades**alpha)
    return {
        "evidence_schema": OPTIMIZER_METRICS_SCHEMA_V2,
        "fitness_schema": OPTIMIZER_FITNESS_SCHEMA,
        "frame_pass_id": frame,
        "frame_inputs": "|".join(f"{name}={params[name]}" for name in ABSOLUTE_BOUNDS),
        "custom_fitness": custom,
        "trade_exponent_alpha": alpha,
        "mean_expectancy_r": mean_r,
        "weighted_r": 0.1,
        "mt5_trades": trades,
        "r_accounted_trades": trades,
        "sum_r": mean_r * trades,
        "sum_net": 10.0,
        "sum_initial_risk": 100.0,
        "accounting_errors": 0,
        "run_nonce": nonce,
    }


def write_report(path: Path, rows: list[dict], *, request: dict) -> None:
    headers = [
        "Pass", "Result", "Profit", "Expected Payoff", "Profit Factor",
        "Recovery Factor", "Sharpe Ratio", "Custom", "Equity DD %", "Trades",
        *ABSOLUTE_BOUNDS,
    ]
    workbook = ET.Element("Workbook")
    ET.SubElement(workbook, "Title").text = (
        f"Max_MTF {request['symbol']},{request['period']} "
        f"{request['from_date']}-{request['to_date']}"
    )
    worksheet = ET.SubElement(workbook, "Worksheet")
    table = ET.SubElement(worksheet, "Table")
    for values in [headers, *rows]:
        row = ET.SubElement(table, "Row")
        for value in values:
            cell = ET.SubElement(row, "Cell")
            ET.SubElement(cell, "Data").text = str(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(workbook).write(path, encoding="utf-8", xml_declaration=True)


def report_row(pass_no: int, params: dict, *, custom: float = 0.5) -> list:
    return [
        pass_no, 0.5, 10.0, 2.5, 1.4, 0.8, 1.2, custom, 5.0, 4,
        *(params[name] for name in ABSOLUTE_BOUNDS),
    ]


def test_raw_report_preview_keeps_partial_rows_separate_from_qualification(tmp_path: Path) -> None:
    request = make_request()
    matched = params_for(request)
    unrecorded = params_for(request, weight_trend=0.3)
    report = tmp_path / "raw_Max_MTF.xml"
    sidecar = tmp_path / "raw_Max_MTF_metrics.csv"
    write_report(
        report,
        [report_row(10, matched), report_row(11, unrecorded, custom=1.0)],
        request=request,
    )
    write_metric_csv(sidecar, [metric_row(matched, nonce=92, frame=45)])

    preview = parse_optimizer_report_preview(
        report,
        round_no=1,
        request=request,
        metrics_path=sidecar,
        expected_nonce=92,
        limit=10,
    )

    assert preview["status"] == "RAW_REPORT_ONLY_UNVERIFIED"
    assert preview["eligibility_authority"] is False
    assert preview["qualified_pool_authority"] is False
    assert preview["total_report_passes"] == 2
    assert preview["verified_r_evidence_passes"] == 1
    assert preview["missing_r_evidence_passes"] == 1
    assert preview["sidecar_state"] == "PARTIAL"
    assert preview["rows"][0]["pass_no"] == 11
    assert preview["rows"][0]["r_metrics"] is None
    assert preview["rows"][0]["implied_mean_r"] == 0.5
    assert preview["rows"][1]["r_metrics"]["weighted_r"] == 0.1


def test_report_preview_does_not_trust_sidecar_mismatch(tmp_path: Path) -> None:
    request = make_request()
    params = params_for(request)
    report = tmp_path / "raw_Max_MTF.xml"
    sidecar = tmp_path / "raw_Max_MTF_metrics.csv"
    write_report(report, [report_row(10, params, custom=0.75)], request=request)
    write_metric_csv(sidecar, [metric_row(params, nonce=92, frame=45, custom=0.5)])

    preview = parse_optimizer_report_preview(
        report,
        round_no=1,
        request=request,
        metrics_path=sidecar,
        expected_nonce=92,
    )

    assert preview["verified_r_evidence_passes"] == 0
    assert preview["mismatched_r_evidence_passes"] == 1
    assert preview["sidecar_state"] == "INVALID"
    assert preview["rows"][0]["r_metrics"] is None


def test_report_preview_shows_bad_sidecar_as_invalid_not_trusted(tmp_path: Path) -> None:
    request = make_request()
    params = params_for(request)
    report = tmp_path / "raw_Max_MTF.xml"
    sidecar = tmp_path / "raw_Max_MTF_metrics.csv"
    write_report(report, [report_row(1, params)], request=request)
    sidecar.write_text("not,a,valid,sidecar\n", encoding="utf-8")

    preview = parse_optimizer_report_preview(
        report,
        round_no=1,
        request=request,
        metrics_path=sidecar,
        expected_nonce=93,
    )

    assert preview["sidecar_state"] == "INVALID"
    assert preview["verified_r_evidence_passes"] == 0
    assert preview["rows"][0]["r_metrics"] is None


def test_failed_job_preview_requires_exact_frozen_manifest_and_hashes(tmp_path: Path) -> None:
    request = make_request()
    params = params_for(request)
    root = tmp_path / "evidence"
    raw_dir = root / "JOB-RAW" / "round_01" / ".staging" / "raw-freeze"
    raw_dir.mkdir(parents=True)
    report = raw_dir / "raw_Max_MTF.xml"
    sidecar = raw_dir / "raw_Max_MTF_metrics.csv"
    write_report(report, [report_row(5, params)], request=request)
    write_metric_csv(sidecar, [metric_row(params, nonce=456, frame=5)])
    fingerprint = {"path": "synthetic-report.xml", "size": report.stat().st_size, "mtime_ns": 123}
    manifest = {
        "schema": "MAX_OPTIMIZER_RAW_FREEZE_V1",
        "job_id": "JOB-RAW",
        "round": 1,
        "source_report_fingerprint": fingerprint,
        "files": {
            report.name: sha256_file(report),
            sidecar.name: sha256_file(sidecar),
        },
    }
    (raw_dir / "raw-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    state = {
        "report_fingerprint": fingerprint,
        "raw_report_sha256": manifest["files"][report.name],
        "raw_sidecar_sha256": manifest["files"][sidecar.name],
        "optimizer_run_nonce": 456,
    }
    job = {
        "job_id": "JOB-RAW",
        "status": "FAILED",
        "first_blocker": "OPTIMIZER_RUNTIME_FAILURE",
        "request": request,
    }

    preview = _verified_raw_report_preview(
        job, {"round_no": 1, "state": state}, evidence_root=root
    )

    assert preview is not None
    assert preview["status"] == "RAW_REPORT_ONLY_UNVERIFIED"
    assert preview["total_report_passes"] == 1


def test_failed_job_preview_refuses_manifest_hash_mismatch(tmp_path: Path) -> None:
    request = make_request()
    params = params_for(request)
    root = tmp_path / "evidence"
    raw_dir = root / "JOB-RAW" / "round_01" / ".staging" / "raw-freeze"
    raw_dir.mkdir(parents=True)
    report = raw_dir / "raw_Max_MTF.xml"
    sidecar = raw_dir / "raw_Max_MTF_metrics.csv"
    write_report(report, [report_row(5, params)], request=request)
    write_metric_csv(sidecar, [metric_row(params, nonce=456, frame=5)])
    fingerprint = {"path": "synthetic-report.xml", "size": report.stat().st_size, "mtime_ns": 123}
    manifest = {
        "schema": "MAX_OPTIMIZER_RAW_FREEZE_V1",
        "job_id": "JOB-RAW",
        "round": 1,
        "source_report_fingerprint": fingerprint,
        "files": {report.name: "0" * 64, sidecar.name: sha256_file(sidecar)},
    }
    (raw_dir / "raw-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    state = {
        "report_fingerprint": fingerprint,
        "raw_report_sha256": manifest["files"][report.name],
        "raw_sidecar_sha256": manifest["files"][sidecar.name],
        "optimizer_run_nonce": 456,
    }

    preview = _verified_raw_report_preview(
        {
            "job_id": "JOB-RAW", "status": "FAILED",
            "first_blocker": "OPTIMIZER_RUNTIME_FAILURE", "request": request,
        },
        {"round_no": 1, "state": state},
        evidence_root=root,
    )

    assert preview == {
        "status": "UNAVAILABLE",
        "message": "Raw optimizer evidence could not be verified; no pass data is shown.",
    }


def test_failed_job_preview_is_not_exposed_for_other_failures(tmp_path: Path) -> None:
    preview = _verified_raw_report_preview(
        {
            "job_id": "JOB-RAW", "status": "FAILED",
            "first_blocker": "EA_COMPILE_FAILED", "request": make_request(),
        },
        {"round_no": 1, "state": {}},
        evidence_root=tmp_path,
    )
    assert preview is None
