from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backtest_report import parse_mt5_backtest_report
from .challenger_bundle import verify_challenger_bundle, verify_geometry_set
from .challenger_operations_store import (
    create_backtest_record,
    get_backtest,
    list_backtests,
    list_backtests_page,
    list_registry_page,
    list_retirements,
    retire_registry_row,
    update_backtest,
)
from .challenger_store import get_challenger
from .config import (
    BACKTEST_ARTIFACT_ROOT,
    CHALLENGER_ARTIFACT_ROOT,
    CHALLENGER_OPERATION_ARTIFACT_ROOT,
    DATABASE_PATH,
    LEGACY_CHALLENGER_ARTIFACT_ROOT,
    ROOT,
)
from .mt5 import detect_mt5
from .optimizer_core import (
    ABSOLUTE_BOUNDS,
    FIXED_INPUTS,
    TICK_MODELS,
    optimizer_fixed_execution_authority,
    optimizer_parameter_bounds_for_schema,
    parse_set_optimizer_entries,
    sha256_file,
)
from .mtf_geometry import (
    ALLOWED_MAIN_TIMEFRAMES,
    STRATEGY_CONTRACT,
    assert_geometry_matches_main,
)
from .optimizer_runtime import compile_summary, launch_mt5
from .optimizer_store import active_job, utc_now

BACKTEST_EVIDENCE_ROOT = BACKTEST_ARTIFACT_ROOT
RETIREMENT_EVIDENCE_ROOT = CHALLENGER_OPERATION_ARTIFACT_ROOT / "retirements"
SYMBOL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._#&+\\-]{0,63}")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Immutable operation artifact already exists: {path}")
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _read_text_flexible(path: Path) -> str:
    if not path.is_file():
        return ""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeError:
            pass
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _bundle_paths(row: dict[str, Any]) -> tuple[Path, Path, Path]:
    bundle = (ROOT / str(row["bundle_path"])).resolve()
    roots = (
        CHALLENGER_ARTIFACT_ROOT.resolve(),
        LEGACY_CHALLENGER_ARTIFACT_ROOT.resolve(),
    )
    if not any(bundle.is_relative_to(root) for root in roots):
        raise RuntimeError("CHALLENGER_BUNDLE_PATH_OUTSIDE_AUTHORITY")
    challenger_id = str(row["challenger_id"])
    ea = bundle / f"Max_Challenger_{challenger_id}.mq5"
    set_path = bundle / f"Max_Challenger_{challenger_id}.set"
    if not bundle.is_dir() or not ea.is_file() or not set_path.is_file():
        raise RuntimeError("CHALLENGER_RETAINED_ARTIFACTS_MISSING")
    return bundle, ea, set_path


def challenger_registry_page(
    *,
    view: str,
    query: str = "",
    sort: str = "created",
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    result = list_registry_page(
        view=view,
        query=query,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
        path=path,
    )
    for item in result["items"]:
        item["integrity"] = "NOT_CHECKED"
    return result


def retire_challenger(
    challenger_id: str,
    *,
    expected_manifest_sha256: str,
    confirmed: bool,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if not confirmed:
        raise RuntimeError("EXPLICIT_RETIREMENT_CONFIRMATION_REQUIRED")
    row = get_challenger(challenger_id, path=path)
    if row is None:
        raise FileNotFoundError(challenger_id)
    if row["status"] not in {"CHALLENGER", "RETIRED"}:
        raise RuntimeError("CHALLENGER_NOT_ACTIVE")

    integrity = verify_challenger_bundle(
        challenger_id,
        allow_retired=row["status"] == "RETIRED",
        path=path,
    )
    if integrity["status"] != "VERIFIED":
        raise RuntimeError("CHALLENGER_INTEGRITY_NOT_VERIFIED")
    if str(integrity["manifest_sha256"]) != str(expected_manifest_sha256):
        raise RuntimeError("CHALLENGER_RETIREMENT_STALE")

    if row["status"] == "RETIRED":
        journals = list_retirements(challenger_id, path=path)
        committed = next(
            (item for item in journals if item["state"] == "COMMITTED"),
            None,
        )
        if committed is None:
            raise RuntimeError("CHALLENGER_RETIRED_WITHOUT_RETIREMENT_JOURNAL")
        return {
            **row,
            "retirement_id": committed["retirement_id"],
            "retirement_state": committed["state"],
            "retirement_evidence_path": committed["evidence_path"],
            "artifact_integrity": "VERIFIED",
            "retirement": "NON_DESTRUCTIVE",
            "bundle_preserved": True,
            "backtest_history_preserved": len(
                list_backtests(challenger_id, path=path)
            ),
        }

    retirement_id = (
        "RETIRE-"
        + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    evidence = RETIREMENT_EVIDENCE_ROOT / retirement_id
    evidence.mkdir(parents=True, exist_ok=False)
    relative_evidence = evidence.resolve().relative_to(ROOT.resolve()).as_posix()
    _write_json(
        evidence / "request.json",
        {
            "schema": "MAX_REBUILD_CHALLENGER_RETIREMENT_REQUEST_V1",
            "retirement_id": retirement_id,
            "challenger_id": challenger_id,
            "expected_manifest_sha256": expected_manifest_sha256,
            "confirmation": "OWNER_EXPLICIT_RETIREMENT_CONFIRMATION",
            "requested_utc": utc_now(),
        },
    )
    _write_json(
        evidence / "preflight.json",
        {
            "schema": "MAX_REBUILD_CHALLENGER_RETIREMENT_PREFLIGHT_V1",
            "challenger_id": challenger_id,
            "status": row["status"],
            "artifact_integrity": integrity["status"],
            "manifest_sha256": integrity["manifest_sha256"],
            "bundle_path": integrity["bundle_path"],
            "retirement": "NON_DESTRUCTIVE",
        },
    )

    try:
        committed = retire_registry_row(
            challenger_id,
            retirement_id=retirement_id,
            expected_manifest_sha256=expected_manifest_sha256,
            evidence_path=relative_evidence,
            path=path,
        )
        retired = committed["challenger"]
        journal = committed["retirement"]
        result = {
            **retired,
            "retirement_id": journal["retirement_id"],
            "retirement_state": journal["state"],
            "retirement_evidence_path": journal["evidence_path"],
            "retirement_before_status": journal["before_status"],
            "retirement_after_status": journal["after_status"],
            "artifact_integrity": "VERIFIED",
            "retirement": "NON_DESTRUCTIVE",
            "bundle_preserved": True,
            "backtest_history_preserved": len(
                list_backtests(challenger_id, path=path)
            ),
        }
        _write_json(
            evidence / "retirement.json",
            {
                "schema": "MAX_REBUILD_CHALLENGER_RETIREMENT_V1",
                **result,
                "journal": journal,
            },
        )
        return result
    except Exception as exc:
        try:
            _write_json(
                evidence / "diagnostic.json",
                {
                    "schema": "MAX_REBUILD_CHALLENGER_RETIREMENT_DIAGNOSTIC_V1",
                    "retirement_id": retirement_id,
                    "challenger_id": challenger_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
        except Exception:
            pass
        raise


def _parse_date(value: Any) -> str:
    text = str(value or "")
    if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", text):
        raise ValueError("dates must use YYYY.MM.DD")
    datetime.strptime(text, "%Y.%m.%d")
    return text


def freeze_backtest_request(
    row: dict[str, Any],
    raw: dict[str, Any] | None,
) -> dict[str, Any]:
    supplied = dict(raw or {})
    allowed = {
        "symbol",
        "relative_symbol",
        "period",
        "from_date",
        "to_date",
        "model",
        "deposit",
        "leverage",
    }
    extra = sorted(set(supplied) - allowed)
    if extra:
        raise ValueError(f"unsupported backtest fields: {extra}")

    source = dict(row["source_request"])
    retained = {
        "symbol": str(source.get("symbol", "")).strip(),
        "relative_symbol": str(source.get("relative_symbol", "")).strip(),
        "period": str(source.get("period", "")).upper(),
        "from_date": _parse_date(source.get("from_date", "")),
        "to_date": _parse_date(source.get("to_date", "")),
        "model": int(source.get("model", 1)),
        "deposit": float(source.get("deposit", 10000.0)),
        "leverage": int(source.get("leverage", 100)),
        "strategy_contract": str(source.get("strategy_contract") or ""),
        "strategy_geometry": dict(source.get("strategy_geometry") or {}),
    }

    if not retained["symbol"] or not retained["relative_symbol"]:
        raise ValueError("retained source symbols are required")
    if not SYMBOL_RE.fullmatch(retained["symbol"]):
        raise ValueError("retained Main Symbol contains unsupported characters")
    if not SYMBOL_RE.fullmatch(retained["relative_symbol"]):
        raise ValueError(
            "retained Relative reference symbol contains unsupported characters"
        )
    if retained["symbol"].casefold() == retained["relative_symbol"].casefold():
        raise ValueError("retained Relative reference symbol must differ from Main Symbol")
    if retained["period"] not in ALLOWED_MAIN_TIMEFRAMES:
        raise ValueError("retained source Main timeframe unsupported")
    if retained["strategy_contract"] != STRATEGY_CONTRACT:
        raise ValueError("retained source Strategy contract mismatch")
    expected_geometry = assert_geometry_matches_main(
        retained["strategy_geometry"],
        retained["period"],
    )
    retained["strategy_geometry"] = expected_geometry
    if retained["model"] not in TICK_MODELS:
        raise ValueError("retained source MT5 tick model unsupported")
    if retained["deposit"] <= 0:
        raise ValueError("retained source deposit must be positive")
    if retained["leverage"] <= 0:
        raise ValueError("retained source leverage must be positive")
    if datetime.strptime(retained["from_date"], "%Y.%m.%d") >= datetime.strptime(
        retained["to_date"], "%Y.%m.%d"
    ):
        raise ValueError("retained source from_date must be earlier than to_date")

    normalizers = {
        "symbol": lambda value: str(value).strip(),
        "relative_symbol": lambda value: str(value).strip(),
        "period": lambda value: str(value).upper(),
        "from_date": lambda value: _parse_date(value),
        "to_date": lambda value: _parse_date(value),
        "model": lambda value: int(value),
        "deposit": lambda value: float(value),
        "leverage": lambda value: int(value),
    }
    for key, value in supplied.items():
        if normalizers[key](value) != retained[key]:
            raise ValueError(
                f"BACKTEST_RETAINED_CONTRACT_OVERRIDE_FORBIDDEN:{key}"
            )

    mt5 = detect_mt5()
    if mt5.get("status") != "READY_EXECUTABLE_AND_DATA_ROOT":
        raise RuntimeError(f"MT5 unavailable: {mt5.get('reason')}")

    return {
        "schema": "MAX_REBUILD_CHALLENGER_BACKTEST_REQUEST_V1",
        "challenger_id": row["challenger_id"],
        "strategy_source": "RETAINED_CHALLENGER_BUNDLE",
        "contract_authority": "RETAINED_SOURCE_REQUEST",
        "source_manifest_sha256": row["manifest_sha256"],
        **retained,
        "tick_model_name": TICK_MODELS[retained["model"]],
        "mt5": {
            "terminal": mt5["terminal"],
            "metaeditor": mt5["metaeditor"],
            "data_root": mt5["data_root"],
        },
        "optimization": 0,
        "parameter_mutation": "NONE",
        "scientist_calls": 0,
        "live_authority": "NONE",
    }


def _format_set_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def _runtime_set_text(
    immutable_set: Path,
    request: dict[str, Any],
    source_request: dict[str, Any],
) -> str:
    text = immutable_set.read_text(encoding="utf-8")
    verify_geometry_set(immutable_set, source_request)

    try:
        bounds = optimizer_parameter_bounds_for_schema(
            str(source_request.get("schema") or "")
        )
    except ValueError as exc:
        raise RuntimeError(
            "CHALLENGER_SET_PARAMETER_UNIVERSE_MISMATCH"
        ) from exc

    optimizer_names: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or "=" not in line:
            continue
        name, rhs = line.split("=", 1)
        if "||" not in rhs:
            continue
        optimizer_names.add(name.strip())
        if len(rhs.split("||")) != 5:
            raise RuntimeError("CHALLENGER_SET_PARAMETER_UNIVERSE_MISMATCH")

    if optimizer_names != set(bounds):
        raise RuntimeError("CHALLENGER_SET_PARAMETER_UNIVERSE_MISMATCH")

    entries = parse_set_optimizer_entries(text, bounds=bounds)
    if set(entries) != set(bounds):
        raise RuntimeError("CHALLENGER_SET_PARAMETER_UNIVERSE_MISMATCH")
    if any(entries[name]["optimize"] != "N" for name in bounds):
        raise RuntimeError("CHALLENGER_SET_CONTAINS_ACTIVE_OPTIMIZATION")

    lines = [
        text.rstrip("\r\n"),
        "",
        "; runtime-only fixed Tester inputs; immutable Challenger SET is unchanged",
    ]
    fixed = dict(FIXED_INPUTS)
    retained_execution = optimizer_fixed_execution_authority(
        str(source_request.get("schema") or "")
    )
    fixed["InpMaxDailyLossPct"] = retained_execution["InpMaxDailyLossPct"]
    fixed["InpConfirmSymbol"] = request["relative_symbol"]
    for name, value in fixed.items():
        lines.append(f"{name}={_format_set_value(value)}")
    return "\n".join(lines) + "\n"


def _build_backtest_ini(
    request: dict[str, Any],
    *,
    expert_name: str,
    set_name: str,
    report_name: str,
) -> str:
    return "\n".join(
        [
            "[Tester]",
            f"Expert={expert_name}",
            f"ExpertParameters={set_name}",
            f"Symbol={request['symbol']}",
            f"Period={request['period']}",
            f"Deposit={float(request['deposit']):.2f}",
            f"Leverage=1:{int(request['leverage'])}",
            f"Model={int(request['model'])}",
            "ExecutionMode=0",
            "Optimization=0",
            f"FromDate={request['from_date']}",
            f"ToDate={request['to_date']}",
            f"Report={report_name}",
            "ReplaceReport=1",
            "ShutdownTerminal=1",
            "UseCloud=0",
            "Visual=0",
            "",
        ]
    )


def _compile_retained_challenger(
    *,
    row: dict[str, Any],
    request: dict[str, Any],
    source_ea: Path,
    evidence: Path,
    backtest_id: str,
) -> dict[str, Any]:
    data_root = Path(request["mt5"]["data_root"])
    metaeditor = Path(request["mt5"]["metaeditor"])
    if not metaeditor.is_file():
        raise FileNotFoundError("METAEDITOR_UNAVAILABLE")

    expert_dir = (
        data_root
        / "MQL5"
        / "Experts"
        / "MaxMTF"
        / "ChallengerBacktests"
        / backtest_id
    )
    expert_dir.mkdir(parents=True, exist_ok=True)
    deployed = expert_dir / source_ea.name
    shutil.copy2(source_ea, deployed)
    if sha256_file(deployed) != sha256_file(source_ea):
        raise RuntimeError("CHALLENGER_BACKTEST_EA_DEPLOYMENT_HASH_MISMATCH")

    ex5 = deployed.with_suffix(".ex5")
    log_path = deployed.with_suffix(".log")
    for stale in (ex5, log_path):
        if stale.exists():
            stale.unlink()
    command = [str(metaeditor), f"/compile:{deployed}", "/log"]
    (evidence / "compile_command.txt").write_text(
        subprocess.list2cmdline(command) + "\n",
        encoding="utf-8",
    )
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log_text = _read_text_flexible(log_path)
    if not log_text:
        log_text = (process.stdout or "") + (process.stderr or "")
    evidence_log = evidence / "metaeditor_compile.log"
    evidence_log.write_text(log_text, encoding="utf-8")
    summary = compile_summary(log_text)
    if not summary["found"]:
        raise RuntimeError("METAEDITOR_COMPILE_SUMMARY_MISSING")
    if int(summary["errors"]) != 0:
        raise RuntimeError(f"METAEDITOR_COMPILE_ERRORS:{summary['errors']}")
    if int(summary["warnings"]) != 0:
        raise RuntimeError(f"METAEDITOR_COMPILE_WARNINGS:{summary['warnings']}")
    if not ex5.is_file():
        raise RuntimeError("METAEDITOR_EX5_MISSING")

    compiled_copy = evidence / "compiled_challenger.ex5"
    shutil.copy2(ex5, compiled_copy)
    return {
        "status": "PASS",
        "compile_summary": summary,
        "process_returncode": process.returncode,
        "returncode_authority": "DIAGNOSTIC_ONLY",
        "metaeditor": str(metaeditor),
        "metaeditor_sha256": sha256_file(metaeditor),
        "compile_command": subprocess.list2cmdline(command),
        "compile_log_sha256": sha256_file(evidence_log),
        "source_mq5_sha256": sha256_file(source_ea),
        "deployed_mq5": str(deployed),
        "deployed_mq5_sha256": sha256_file(deployed),
        "compiled_ex5": str(ex5),
        "ex5_sha256": sha256_file(ex5),
        "expert_name": (
            "MaxMTF\\ChallengerBacktests\\"
            + backtest_id
            + "\\"
            + source_ea.stem
        ),
    }


def _wait_for_report(path: Path, timeout_sec: int = 60) -> Path:
    candidates = [path]
    if path.suffix.lower() == ".html":
        candidates.append(path.with_suffix(".htm"))
    deadline = time.time() + max(1, int(timeout_sec))
    last: tuple[str, int, int] | None = None
    stable = 0
    while time.time() <= deadline:
        existing = [candidate for candidate in candidates if candidate.is_file()]
        if existing:
            selected = max(existing, key=lambda item: item.stat().st_mtime_ns)
            stat = selected.stat()
            signature = (str(selected), int(stat.st_size), int(stat.st_mtime_ns))
            if stat.st_size > 0 and signature == last:
                stable += 1
            else:
                last = signature
                stable = 0
            if stable >= 1:
                return selected
        time.sleep(0.5)
    raise RuntimeError("CHALLENGER_BACKTEST_REPORT_MISSING")


def run_challenger_backtest(
    challenger_id: str,
    *,
    request_payload: dict[str, Any] | None = None,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    row = get_challenger(challenger_id, path=path)
    if row is None:
        raise FileNotFoundError(challenger_id)
    if row["status"] != "CHALLENGER":
        raise RuntimeError("CHALLENGER_NOT_ACTIVE")
    if active_job(path=path) is not None:
        raise RuntimeError("CHALLENGER_BACKTEST_BLOCKED_OPTIMIZER_ACTIVE")

    integrity = verify_challenger_bundle(challenger_id, path=path)
    if integrity["status"] != "VERIFIED":
        raise RuntimeError("CHALLENGER_INTEGRITY_NOT_VERIFIED")
    request = freeze_backtest_request(row, request_payload)
    _bundle, source_ea, source_set = _bundle_paths(row)

    backtest_id = (
        "BT-"
        + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    evidence = BACKTEST_EVIDENCE_ROOT / backtest_id
    evidence.mkdir(parents=True, exist_ok=False)
    relative_evidence = evidence.resolve().relative_to(ROOT.resolve()).as_posix()

    _write_json(evidence / "request.json", request)
    shutil.copy2(source_ea, evidence / "retained_challenger.mq5")
    shutil.copy2(source_set, evidence / "retained_challenger.set")
    record = create_backtest_record(
        backtest_id=backtest_id,
        challenger_id=challenger_id,
        source_manifest_sha256=str(integrity["manifest_sha256"]),
        request=request,
        ea_sha256=str(integrity["ea_sha256"]),
        set_sha256=str(integrity["set_sha256"]),
        evidence_path=relative_evidence,
        path=path,
    )

    mt5_returned = False

    try:
        compile_result = _compile_retained_challenger(
            row=row,
            request=request,
            source_ea=source_ea,
            evidence=evidence,
            backtest_id=backtest_id,
        )
        _write_json(evidence / "compile.json", compile_result)

        data_root = Path(request["mt5"]["data_root"])
        tester_dir = data_root / "MQL5" / "Profiles" / "Tester"
        tester_dir.mkdir(parents=True, exist_ok=True)
        set_name = f"MaxMTF_Backtest_{backtest_id}.set"
        runtime_set = tester_dir / set_name
        runtime_text = _runtime_set_text(
            source_set,
            request,
            dict(row["source_request"]),
        )
        runtime_set.write_text(runtime_text, encoding="utf-8")
        (evidence / "runtime_backtest.set").write_text(
            runtime_text,
            encoding="utf-8",
        )

        report_stem = f"MaxMTF_Backtest_{backtest_id}"
        report_relative = "reports\\" + report_stem
        mt5_report = data_root / "reports" / f"{report_stem}.htm"
        mt5_report.parent.mkdir(parents=True, exist_ok=True)
        for stale_report in (mt5_report, mt5_report.with_suffix(".html")):
            if stale_report.exists():
                stale_report.unlink()

        ini = evidence / "backtest.ini"
        ini.write_text(
            _build_backtest_ini(
                request,
                expert_name=compile_result["expert_name"],
                set_name=set_name,
                report_name=report_relative,
            ),
            encoding="utf-8",
        )
        update_backtest(
            backtest_id,
            state="RUNNING",
            ex5_sha256=compile_result["ex5_sha256"],
            path=path,
        )
        terminal = Path(request["mt5"]["terminal"])
        if not terminal.is_file():
            raise FileNotFoundError("MT5_TERMINAL_UNAVAILABLE")
        terminal_sha256 = sha256_file(terminal)
        returncode = launch_mt5(
            request,
            ini_path=ini,
            timeout_sec=21600,
        )
        mt5_returned = True
        source_report = _wait_for_report(mt5_report)
        staged_report = evidence / "backtest_report.htm"
        shutil.copy2(source_report, staged_report)
        if sha256_file(staged_report) != sha256_file(source_report):
            raise RuntimeError("CHALLENGER_BACKTEST_REPORT_STAGE_HASH_MISMATCH")
        report_sha = sha256_file(staged_report)
        parsed_report = parse_mt5_backtest_report(
            staged_report,
            expected_sha256=report_sha,
        )
        result = {
            "schema": "MAX_REBUILD_CHALLENGER_BACKTEST_RESULT_V2",
            "status": "COMPLETED",
            "execution_truth": "MT5_STRATEGY_TESTER",
            "challenger_id": challenger_id,
            "backtest_id": backtest_id,
            "source_manifest_sha256": integrity["manifest_sha256"],
            "strategy_contract": request["strategy_contract"],
            "strategy_geometry": request["strategy_geometry"],
            "retained_ea_sha256": integrity["ea_sha256"],
            "retained_set_sha256": integrity["set_sha256"],
            "compiled_ex5_sha256": compile_result["ex5_sha256"],
            "runtime_set_sha256": sha256_file(evidence / "runtime_backtest.set"),
            "tester_ini_sha256": sha256_file(ini),
            "metaeditor_sha256": compile_result["metaeditor_sha256"],
            "compile_log_sha256": compile_result["compile_log_sha256"],
            "terminal_sha256": terminal_sha256,
            "mt5_returncode": returncode,
            "report_file": staged_report.name,
            "report_source": str(source_report),
            "report_sha256": report_sha,
            "report_size": staged_report.stat().st_size,
            "metrics_schema": parsed_report["schema"],
            "metrics": parsed_report["metrics"],
            "available_metrics": parsed_report["available_metrics"],
            "runtime": {
                "expert_dir": str(Path(compile_result["deployed_mq5"]).parent),
                "tester_set": str(runtime_set),
                "source_report": str(source_report),
            },
            "parameter_mutation": "NONE",
            "scientist_calls": 0,
            "live_authority": "NONE",
        }
        _write_json(evidence / "result.json", result)
        completed = update_backtest(
            backtest_id,
            state="COMPLETED",
            ex5_sha256=compile_result["ex5_sha256"],
            report_path=staged_report.resolve().relative_to(ROOT.resolve()).as_posix(),
            report_sha256=report_sha,
            result=result,
            path=path,
        )
        return completed
    except Exception as exc:
        diagnostic = {
            "schema": "MAX_REBUILD_CHALLENGER_BACKTEST_DIAGNOSTIC_V1",
            "backtest_id": backtest_id,
            "challenger_id": challenger_id,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        try:
            _write_json(evidence / "diagnostic.json", diagnostic)
        except Exception:
            pass
        try:
            update_backtest(
                backtest_id,
                state="UNCONFIRMED" if mt5_returned else "FAILED",
                error=str(exc),
                path=path,
            )
        except Exception:
            pass
        raise


def _with_retained_report_metrics(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(record.get("result") or {})
    if (
        record.get("state") == "COMPLETED"
        and "metrics" not in result
        and record.get("report_path")
        and record.get("report_sha256")
    ):
        report = (ROOT / str(record["report_path"])).resolve()
        parsed = parse_mt5_backtest_report(
            report,
            expected_sha256=str(record["report_sha256"]),
        )
        result["metrics_schema"] = parsed["schema"]
        result["metrics"] = parsed["metrics"]
        result["available_metrics"] = parsed["available_metrics"]
    return {**record, "result": result or record.get("result")}


def backtest_registry_page(
    *,
    challenger_id: str | None = None,
    query: str = "",
    state: str = "",
    sort: str = "created",
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    result = list_backtests_page(
        challenger_id=challenger_id,
        query=query,
        state=state,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
        path=path,
    )
    result["items"] = [
        _with_retained_report_metrics(item)
        for item in result["items"]
    ]
    return result


def backtest_history(
    challenger_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> list[dict[str, Any]]:
    if get_challenger(challenger_id, path=path) is None:
        raise FileNotFoundError(challenger_id)
    return [
        _with_retained_report_metrics(item)
        for item in list_backtests(challenger_id, path=path)
    ]


def backtest_detail(
    backtest_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    record = get_backtest(backtest_id, path=path)
    if record is None:
        raise FileNotFoundError(backtest_id)
    enriched = _with_retained_report_metrics(record)
    challenger = get_challenger(str(record["challenger_id"]), path=path)
    if challenger is not None:
        enriched["source_challenger"] = {
            "challenger_id": challenger["challenger_id"],
            "role_origin": challenger["role_origin"],
            "source_job_id": challenger["source_job_id"],
            "source_round": challenger["source_round"],
            "source_pass": challenger["source_pass"],
            "manifest_sha256": challenger["manifest_sha256"],
            "params": challenger["params"],
            "optimizer_source_kpi": challenger["kpi"],
        }
    return enriched
