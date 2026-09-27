from __future__ import annotations

import csv
import importlib
import json
import math
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .challenger_bundle import verify_challenger_bundle
from .config import (
    DATABASE_PATH,
    RESEARCH_DATA_ROOT,
    ROOT,
)
from .db import connect
from .mt5 import detect_mt5
from .optimizer_runtime import compile_summary, launch_mt5
from .optimizer_store import utc_now
from .research_contract import FEATURE_CONTRACT, stable_hash
from .research_dataset import (
    EA_COLUMNS,
    ROLE_ORDER,
    SOURCE_BUNDLE_SCHEMA,
    SOURCE_PRODUCER_SCHEMA,
    SOURCE_PRODUCER_VERSION,
    dependency_report,
    load_source_bundle,
    sha256_file,
)
from .workflow_store import migrate_current

SOURCE_PREPARE_CONFIRMATION = "OWNER_EXPLICIT_R01_SOURCE_PREPARE"


def _source_root(research_id: str) -> Path:
    return RESEARCH_DATA_ROOT / str(research_id)


def _managed_path(path: Path) -> Path:
    resolved = path.resolve()
    authority = RESEARCH_DATA_ROOT.resolve()
    if not resolved.is_relative_to(authority):
        raise RuntimeError("R01_SOURCE_PATH_OUTSIDE_MANAGED_ROOT")
    return resolved


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise RuntimeError(f"R01_SOURCE_IMMUTABLE_MUTATION:{path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _parse_source_date(value: Any, label: str) -> datetime:
    token = str(value or "").strip()
    try:
        parsed = datetime.strptime(token, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"R01_SOURCE_DATE_INVALID:{label}") from exc
    return parsed


def _timeframe_constant(mt5: Any, name: str) -> int:
    attr = "TIMEFRAME_" + str(name).upper()
    value = getattr(mt5, attr, None)
    if value is None:
        raise RuntimeError(f"R01_MT5_TIMEFRAME_UNSUPPORTED:{name}")
    return int(value)


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    method = getattr(value, "_asdict", None)
    if callable(method):
        return dict(method())
    if isinstance(value, dict):
        return dict(value)
    return {
        name: getattr(value, name)
        for name in dir(value)
        if not name.startswith("_") and not callable(getattr(value, name))
    }


def _import_mt5() -> Any:
    try:
        return importlib.import_module("MetaTrader5")
    except Exception as exc:
        raise RuntimeError("R01_MT5_PYTHON_PROVIDER_UNAVAILABLE") from exc


def _format_set_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)


def _parent_training_set(
    frozen_set_text: str,
    *,
    training_filename: str,
) -> str:
    overrides: dict[str, Any] = {
        "InpAllowLiveTrading": False,
        "InpOneDecisionPerBar": True,
        "InpWriteTrainingData": True,
        "InpTrainingFile": training_filename,
        "InpWriteTelemetry": False,
        "InpWriteChampionTrades": False,
        "InpWriteShadowTrades": False,
        "InpUseOnnxChampion": False,
        "InpUseOnnxChallenger": False,
        "InpUseChampionPolicy": False,
        "InpUseChallengerPolicy": False,
    }
    seen: set[str] = set()
    output = [
        "; MAX R01 managed parent feature capture",
        "; accepted frozen parent SET preserved; runtime-output controls only",
    ]
    for raw in frozen_set_text.splitlines():
        line = raw.rstrip("\r\n")
        stripped = line.strip()
        if not stripped or stripped.startswith(";") or "=" not in line:
            output.append(line)
            continue
        name = line.split("=", 1)[0].strip()
        if name in overrides:
            output.append(f"{name}={_format_set_value(overrides[name])}")
            seen.add(name)
        else:
            output.append(line)
    for name in sorted(set(overrides) - seen):
        output.append(f"{name}={_format_set_value(overrides[name])}")
    output.append("")
    return "\n".join(output)


def _frozen_parent_ea_path(
    parent: dict[str, Any],
    *,
    path: Path,
) -> Path:
    challenger_id = str(parent.get("source_challenger_id") or "").strip()
    if not challenger_id:
        raise RuntimeError("R01_FROZEN_PARENT_CHALLENGER_ID_MISSING")
    verified = verify_challenger_bundle(
        challenger_id,
        allow_promoted=True,
        allow_retired=True,
        path=path,
    )
    expected = str(parent.get("ea_sha256") or "")
    if (
        verified.get("status") != "VERIFIED"
        or str(verified.get("ea_sha256") or "") != expected
    ):
        raise RuntimeError("R01_FROZEN_PARENT_CHALLENGER_EA_MISMATCH")
    bundle = (ROOT / str(verified["bundle_path"])).resolve()
    source = bundle / f"Max_Challenger_{challenger_id}.mq5"
    if not source.is_file() or sha256_file(source) != expected:
        raise RuntimeError("R01_FROZEN_PARENT_EA_ARTIFACT_MISMATCH")
    return source


def _frozen_parent_set_path(
    parent: dict[str, Any],
    *,
    path: Path,
) -> Path:
    challenger_id = str(parent.get("source_challenger_id") or "").strip()
    if not challenger_id:
        raise RuntimeError("R01_FROZEN_PARENT_CHALLENGER_ID_MISSING")
    verified = verify_challenger_bundle(
        challenger_id,
        allow_promoted=True,
        allow_retired=True,
        path=path,
    )
    bundle = (ROOT / str(verified["bundle_path"])).resolve()
    source = bundle / f"Max_Challenger_{challenger_id}.set"
    expected = str(verified.get("set_sha256") or "")
    if not expected or not source.is_file() or sha256_file(source) != expected:
        raise RuntimeError("R01_FROZEN_PARENT_SET_ARTIFACT_MISMATCH")
    return source


def _compile_parent_ea(
    parent: dict[str, Any],
    *,
    mt5_runtime: dict[str, Any],
    staging: Path,
) -> dict[str, Any]:
    source_token = str(parent.get("_frozen_ea_source_path") or "").strip()
    if not source_token:
        raise RuntimeError("R01_FROZEN_PARENT_EA_SOURCE_NOT_BOUND")
    source = Path(source_token).resolve()
    if not source.is_file():
        raise FileNotFoundError("R01_FROZEN_PARENT_EA_SOURCE_MISSING")
    actual = sha256_file(source)
    expected = str(parent.get("ea_sha256") or "")
    if not expected or actual != expected:
        raise RuntimeError("R01_FROZEN_PARENT_EA_HASH_MISMATCH")

    data_root = Path(str(mt5_runtime["data_root"]))
    metaeditor = Path(str(mt5_runtime["metaeditor"]))
    expert_dir = data_root / "MQL5" / "Experts" / "MAX_REBUILD"
    expert_dir.mkdir(parents=True, exist_ok=True)
    deployed = expert_dir / "Max_R01_Parent_Source.mq5"
    shutil.copy2(source, deployed)
    if sha256_file(deployed) != expected:
        raise RuntimeError("R01_PARENT_CHAMPION_EA_DEPLOY_HASH_MISMATCH")
    ex5 = deployed.with_suffix(".ex5")
    if ex5.exists():
        ex5.unlink()
    compile_log = deployed.with_suffix(".log")
    if compile_log.exists():
        compile_log.unlink()

    command = [str(metaeditor), f"/compile:{deployed}", "/log"]
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log_text = ""
    if compile_log.is_file():
        raw = compile_log.read_bytes()
        for encoding in ("utf-16", "utf-8-sig", "utf-8", "cp1252"):
            try:
                log_text = raw.decode(encoding)
                break
            except UnicodeError:
                continue
    if not log_text:
        log_text = (process.stdout or "") + (process.stderr or "")
    summary = compile_summary(log_text)
    if not summary["found"]:
        raise RuntimeError("R01_PARENT_EA_COMPILE_SUMMARY_MISSING")
    if int(summary["errors"]) != 0:
        raise RuntimeError("R01_PARENT_EA_COMPILE_FAILED")
    if not ex5.is_file():
        raise RuntimeError("R01_PARENT_EA_EX5_MISSING")

    evidence = staging / "mt5_parent_compile"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "compile.log").write_text(log_text, encoding="utf-8")
    shutil.copy2(source, evidence / "accepted_parent.mq5")
    shutil.copy2(ex5, evidence / "accepted_parent.ex5")
    return {
        "expert_name": "MAX_REBUILD\\Max_R01_Parent_Source",
        "source_sha256": expected,
        "ex5_sha256": sha256_file(ex5),
        "compile_log_sha256": sha256_file(evidence / "compile.log"),
    }


def _tester_ini(
    *,
    expert_name: str,
    set_name: str,
    symbol: str,
    period: str,
    from_date: datetime,
    to_date: datetime,
    report_name: str,
) -> str:
    return "\n".join([
        "[Tester]",
        f"Expert={expert_name}",
        f"ExpertParameters={set_name}",
        f"Symbol={symbol}",
        f"Period={period}",
        "Deposit=10000.00",
        "Leverage=1:100",
        "Model=1",
        "ExecutionMode=0",
        "Optimization=0",
        "FromDate=" + from_date.strftime("%Y.%m.%d"),
        "ToDate=" + to_date.strftime("%Y.%m.%d"),
        "ForwardMode=0",
        f"Report={report_name}",
        "ReplaceReport=1",
        "ShutdownTerminal=1",
        "UseCloud=0",
        "Visual=0",
        "",
    ])


def _run_parent_feature_capture(
    parent: dict[str, Any],
    *,
    mt5_runtime: dict[str, Any],
    staging: Path,
    from_date: datetime,
    to_date: datetime,
    token: str,
    common_files_root: Path,
    frozen_set_path: Path,
) -> dict[str, Any]:
    compiled = _compile_parent_ea(parent, mt5_runtime=mt5_runtime, staging=staging)
    data_root = Path(str(mt5_runtime["data_root"]))
    preset_dir = data_root / "MQL5" / "Profiles" / "Tester"
    preset_dir.mkdir(parents=True, exist_ok=True)
    training_filename = f"MAX_R01_{token}_CP32.csv"
    common_files_root.mkdir(parents=True, exist_ok=True)
    common_training = common_files_root / training_filename
    if common_training.exists():
        common_training.unlink()

    set_name = f"MAX_R01_{token}.set"
    set_path = preset_dir / set_name
    if not frozen_set_path.is_file():
        raise RuntimeError("R01_FROZEN_PARENT_SET_ARTIFACT_MISSING")
    frozen_set_sha = sha256_file(frozen_set_path)
    set_text = _parent_training_set(
        frozen_set_path.read_text(encoding="utf-8"),
        training_filename=training_filename,
    )
    set_path.write_text(set_text, encoding="utf-8", newline="\n")
    ini = staging / "parent_feature_capture.ini"
    ini.write_text(
        _tester_ini(
            expert_name=compiled["expert_name"],
            set_name=set_name,
            symbol=str(parent["main_symbol"]),
            period=str(parent["strategy_geometry"]["main_tf"]),
            from_date=from_date,
            to_date=to_date,
            report_name=f"MAX_R01_{token}_parent.xml",
        ),
        encoding="utf-8",
        newline="\n",
    )
    runtime_request = {
        "mt5": mt5_runtime,
    }
    launch_mt5(runtime_request, ini_path=ini, timeout_sec=1800)
    if not common_training.is_file() or common_training.stat().st_size < 100:
        raise RuntimeError("R01_EA_CP32_CAPTURE_MISSING")
    raw_target = staging / "ea_cp32_raw.csv"
    shutil.copy2(common_training, raw_target)
    common_training.unlink(missing_ok=True)
    return {
        **compiled,
        "raw_training_path": raw_target,
        "raw_training_sha256": sha256_file(raw_target),
        "accepted_parent_set_sha256": frozen_set_sha,
        "set_sha256": sha256_file(set_path),
        "ini_sha256": sha256_file(ini),
    }


def _capture_rates(
    mt5: Any,
    *,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"R01_MT5_SYMBOL_SELECT_FAIL:{symbol}")
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"R01_MT5_SYMBOL_INFO_MISSING:{symbol}")
    raw = mt5.copy_rates_range(
        symbol,
        _timeframe_constant(mt5, timeframe),
        start,
        end,
    )
    if raw is None or len(raw) == 0:
        raise RuntimeError(f"R01_MT5_RATES_EMPTY:{symbol}:{timeframe}")
    rows: list[dict[str, Any]] = []
    previous: int | None = None
    for item in raw:
        epoch = int(item["time"])
        if previous is not None and epoch <= previous:
            raise RuntimeError(f"R01_MT5_RATES_NON_MONOTONIC:{symbol}:{timeframe}")
        previous = epoch
        rows.append({
            "open_time": datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
            "open": float(item["open"]),
            "high": float(item["high"]),
            "low": float(item["low"]),
            "close": float(item["close"]),
            "tick_volume": float(item["tick_volume"]),
            "spread_points": float(item["spread"]),
        })
    return rows


def _write_rate_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "open_time","open","high","low","close","tick_volume","spread_points"
            ),
        )
        writer.writeheader()
        writer.writerows(rows)


def _ohlc_key(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return tuple(round(float(row[name]), 10) for name in ("open","high","low","close"))


def _normalize_ea_training(
    raw_training: Path,
    *,
    main_bars: list[dict[str, Any]],
    output: Path,
) -> dict[str, Any]:
    with raw_training.open("r", encoding="utf-8-sig", errors="strict", newline="") as handle:
        first = handle.readline()
        handle.seek(0)
        delimiter = ";" if first.count(";") >= first.count(",") else ","
        reader = csv.DictReader(handle, delimiter=delimiter)
        if tuple(reader.fieldnames or ()) != EA_COLUMNS:
            raise RuntimeError("R01_EA_CP32_COLUMN_ORDER_MISMATCH")
        raw_rows = list(reader)
    if not raw_rows:
        raise RuntimeError("R01_EA_CP32_EMPTY")

    index: dict[tuple[float, float, float, float], list[int]] = {}
    for position, bar in enumerate(main_bars):
        index.setdefault(_ohlc_key(bar), []).append(position)

    normalized: list[dict[str, Any]] = []
    prior: int | None = None
    for raw in raw_rows:
        key = tuple(round(float(raw[name]), 10) for name in ("open","high","low","close"))
        if prior is None:
            candidates = [
                position for position in index.get(key, [])
                if position + 1 < len(main_bars)
            ]
            if len(candidates) != 1:
                raise RuntimeError("R01_EA_CP32_UTC_MAPPING_AMBIGUOUS")
            position = candidates[0]
        else:
            position = prior + 1
            if (
                position + 1 >= len(main_bars)
                or _ohlc_key(main_bars[position]) != key
            ):
                raise RuntimeError("R01_EA_CP32_UTC_SEQUENCE_MISMATCH")
        prior = position
        row = dict(raw)
        row["signal_time"] = str(main_bars[position]["open_time"])
        row["decision_bar_time"] = str(main_bars[position + 1]["open_time"])
        normalized.append(row)

    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EA_COLUMNS)
        writer.writeheader()
        writer.writerows(normalized)
    return {
        "rows": len(normalized),
        "start_utc": normalized[0]["signal_time"],
        "end_utc": normalized[-1]["signal_time"],
        "mapping_authority": "EA_OHLC_TO_MT5_COPY_RATES_UTC_EPOCH",
    }


def _capture_with_mt5(
    parent: dict[str, Any],
    *,
    staging: Path,
    from_date: datetime,
    to_date: datetime,
    token: str,
) -> dict[str, Any]:
    runtime = detect_mt5()
    if runtime.get("status") != "READY_EXECUTABLE_AND_DATA_ROOT":
        raise RuntimeError("R01_MT5_RUNTIME_NOT_READY:" + str(runtime.get("reason") or "UNKNOWN"))
    mt5 = _import_mt5()
    terminal = str(runtime["terminal"])
    if not mt5.initialize(path=terminal):
        raise RuntimeError("R01_MT5_PYTHON_INITIALIZE_FAIL")
    try:
        terminal_info = _as_dict(mt5.terminal_info())
        account_info = _as_dict(mt5.account_info())
        if not terminal_info or not account_info:
            raise RuntimeError("R01_MT5_IDENTITY_UNAVAILABLE")
        if terminal_info.get("connected") is False:
            raise RuntimeError("R01_MT5_NOT_CONNECTED")
        server = str(account_info.get("server") or "").strip()
        company = str(account_info.get("company") or terminal_info.get("company") or "").strip()
        terminal_company = str(terminal_info.get("company") or "").strip()
        terminal_name = str(terminal_info.get("name") or "MetaTrader 5").strip()
        commondata_raw = str(terminal_info.get("commondata_path") or "").strip()
        if not server or not company or not terminal_company:
            raise RuntimeError("R01_MT5_BROKER_FEED_IDENTITY_UNPROVEN")
        if not commondata_raw:
            raise RuntimeError("R01_MT5_COMMON_DATA_AUTHORITY_UNAVAILABLE")
        commondata_root = Path(commondata_raw)
        if not commondata_root.is_dir():
            raise RuntimeError("R01_MT5_COMMON_DATA_AUTHORITY_INVALID")
        common_files_root = commondata_root / "Files"

        main_symbol = str(parent["main_symbol"])
        relative_symbol = str(parent["relative_symbol"])
        for symbol in (main_symbol, relative_symbol):
            if not mt5.symbol_select(symbol, True):
                raise RuntimeError(f"R01_MT5_SYMBOL_SELECT_FAIL:{symbol}")
        main_info = _as_dict(mt5.symbol_info(main_symbol))
        relative_info = _as_dict(mt5.symbol_info(relative_symbol))
        if not main_info or not relative_info:
            raise RuntimeError("R01_MT5_SYMBOL_IDENTITY_UNAVAILABLE")
        point = float(main_info.get("point") or 0.0)
        if not math.isfinite(point) or point <= 0.0:
            raise RuntimeError("R01_POINT_SIZE_INVALID")

        main_minutes = int(parent["strategy_geometry"]["main_minutes"])
        dependency = dependency_report(parent)
        base_dependency = int(dependency["full_base_dependency_main_bars"])
        history_start = from_date - timedelta(minutes=base_dependency * main_minutes)
        history_end = to_date + timedelta(
            minutes=(int(parent["deterministic_risk"]["max_hold_bars"]) + 2) * main_minutes
        )

        role_rows: dict[str, dict[str, list[dict[str, Any]]]] = {"main": {}, "relative": {}}
        role_files: dict[str, dict[str, dict[str, Any]]] = {"main": {}, "relative": {}}
        coverage: dict[str, Any] = {}
        for side, symbol in (("main", main_symbol), ("relative", relative_symbol)):
            for role in ROLE_ORDER:
                timeframe = str(parent["strategy_geometry"][f"{role}_tf"])
                rows = _capture_rates(
                    mt5,
                    symbol=symbol,
                    timeframe=timeframe,
                    start=history_start,
                    end=history_end,
                )
                role_rows[side][role] = rows
                file_path = staging / f"{side}_{role}.csv"
                _write_rate_csv(file_path, rows)
                role_files[side][role] = {
                    "path": file_path.name,
                    "sha256": sha256_file(file_path),
                    "symbol": symbol,
                    "timeframe": timeframe,
                }
                coverage[f"{side}:{role}"] = {
                    "rows": len(rows),
                    "start_utc": rows[0]["open_time"],
                    "end_utc": rows[-1]["open_time"],
                }

        capture = _run_parent_feature_capture(
            parent,
            mt5_runtime=runtime,
            staging=staging,
            from_date=from_date,
            to_date=to_date,
            token=token,
            common_files_root=common_files_root,
            frozen_set_path=Path(str(parent["_frozen_set_source_path"])).resolve(),
        )
        normalized_path = staging / "ea_cp32.csv"
        ea_coverage = _normalize_ea_training(
            Path(capture["raw_training_path"]),
            main_bars=role_rows["main"]["main"],
            output=normalized_path,
        )
        coverage["ea_cp32"] = ea_coverage

        identity = {
            "schema": "MAX_RESEARCH_MT5_SOURCE_IDENTITY_R01_V1",
            "provider": "MetaTrader5 Python API + accepted parent Strategy Tester",
            "terminal_authority": runtime["terminal_authority"],
            "terminal_name": terminal_name,
            "terminal_company": terminal_company,
            "terminal_build": terminal_info.get("build"),
            "commondata_authority": str(commondata_root.resolve()),
            "account_server": server,
            "account_company": company,
            "main_symbol": {
                "name": main_symbol,
                "path": main_info.get("path"),
                "digits": main_info.get("digits"),
                "point": point,
            },
            "relative_symbol": {
                "name": relative_symbol,
                "path": relative_info.get("path"),
                "digits": relative_info.get("digits"),
                "point": relative_info.get("point"),
            },
            "source_timezone": "UTC",
            "source_timezone_authority": "METATRADER5_COPY_RATES_UTC_EPOCH",
        }
        identity["source_identity_sha256"] = stable_hash(identity)
        return {
            "runtime": runtime,
            "broker": company,
            "feed": server,
            "source_timezone": "UTC",
            "point_size": point,
            "identity": identity,
            "role_files": role_files,
            "ea_cp32": {
                "path": normalized_path.name,
                "sha256": sha256_file(normalized_path),
            },
            "coverage": coverage,
            "capture_provenance": {
                "accepted_parent_ea_sha256": capture["source_sha256"],
                "accepted_parent_set_sha256": capture["accepted_parent_set_sha256"],
                "compiled_parent_ex5_sha256": capture["ex5_sha256"],
                "parent_compile_log_sha256": capture["compile_log_sha256"],
                "parent_test_set_sha256": capture["set_sha256"],
                "parent_test_ini_sha256": capture["ini_sha256"],
                "raw_ea_training_sha256": capture["raw_training_sha256"],
            },
        }
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


def _persist_source(record: dict[str, Any], *, path: Path) -> dict[str, Any]:
    migrate_current(path)
    with connect(path) as conn:
        existing = conn.execute(
            "SELECT * FROM research_r01_sources WHERE source_id=?",
            (record["source_id"],),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO research_r01_sources(
                    source_id,research_id,parent_strategy_id,parent_authority_sha256,
                    status,bundle_path,bundle_sha256,source_identity_sha256,
                    broker,feed,source_timezone,main_symbol,relative_symbol,
                    data_start_utc,data_end_utc,row_coverage_json,provenance_json,
                    created_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record["source_id"],record["research_id"],record["parent_strategy_id"],
                    record["parent_authority_sha256"],"READY",record["bundle_path"],
                    record["bundle_sha256"],record["source_identity_sha256"],
                    record["broker"],record["feed"],record["source_timezone"],
                    record["main_symbol"],record["relative_symbol"],
                    record["data_start_utc"],record["data_end_utc"],
                    json.dumps(record["row_coverage"],sort_keys=True),
                    json.dumps(record["provenance"],sort_keys=True),
                    record["created_utc"],
                ),
            )
        else:
            current = dict(existing)
            immutable = (
                "research_id","parent_strategy_id","parent_authority_sha256",
                "bundle_path","bundle_sha256","source_identity_sha256",
            )
            if any(str(current[key]) != str(record[key]) for key in immutable):
                raise RuntimeError("R01_SOURCE_IDENTITY_COLLISION")
    return get_prepared_source(record["source_id"], path=path) or {}


def _decode_source(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["row_coverage"] = json.loads(result.pop("row_coverage_json"))
    result["provenance"] = json.loads(result.pop("provenance_json"))
    return result


def get_prepared_source(source_id: str, *, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    migrate_current(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM research_r01_sources WHERE source_id=?",
            (str(source_id),),
        ).fetchone()
    return _decode_source(row)


def list_prepared_sources(research_id: str, *, path: Path = DATABASE_PATH) -> list[dict[str, Any]]:
    migrate_current(path)
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM research_r01_sources
            WHERE research_id=?
            ORDER BY created_utc DESC,source_id DESC
            """,
            (str(research_id),),
        ).fetchall()
    return [_decode_source(row) or {} for row in rows]


def resolve_prepared_source(
    source_id: str,
    *,
    parent: dict[str, Any],
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    record = get_prepared_source(source_id, path=path)
    if record is None:
        raise FileNotFoundError("R01_VERIFIED_SOURCE_NOT_FOUND")
    if (
        record["status"] != "READY"
        or record["research_id"] != parent["research_id"]
        or record["parent_strategy_id"] != parent["parent_strategy_id"]
        or record["parent_authority_sha256"] != parent["parent_authority_sha256"]
    ):
        raise RuntimeError("R01_VERIFIED_SOURCE_PARENT_MISMATCH")
    bundle_path = _managed_path(Path(record["bundle_path"]))
    expected_root = _managed_path(_source_root(parent["research_id"]) / source_id)
    if not bundle_path.is_relative_to(expected_root):
        raise RuntimeError("R01_VERIFIED_SOURCE_PATH_MISMATCH")
    if not bundle_path.is_file() or sha256_file(bundle_path) != record["bundle_sha256"]:
        raise RuntimeError("R01_VERIFIED_SOURCE_BUNDLE_INTEGRITY_FAIL")
    loaded = load_source_bundle(bundle_path, parent=parent)
    if loaded["bundle_sha256"] != record["bundle_sha256"]:
        raise RuntimeError("R01_VERIFIED_SOURCE_BUNDLE_INTEGRITY_FAIL")
    if loaded["source_identity_sha256"] != record["source_identity_sha256"]:
        raise RuntimeError("R01_VERIFIED_SOURCE_IDENTITY_MISMATCH")
    manifest = dict(loaded["manifest"])
    manifest_source_id = str(manifest.pop("source_id", "") or "")
    manifest.pop("creation_provenance", None)
    expected_source_id = "RSRC-" + stable_hash(manifest)[:24]
    if manifest_source_id != str(source_id) or expected_source_id != str(source_id):
        raise RuntimeError("R01_VERIFIED_SOURCE_IDENTITY_BINDING_FAIL")
    managed_files = [Path(loaded["training_path"])]
    for side in ("main", "relative"):
        managed_files.extend(Path(value) for value in loaded["role_paths"][side].values())
    if any(not item.resolve().is_relative_to(expected_root) for item in managed_files):
        raise RuntimeError("R01_VERIFIED_SOURCE_FILE_OUTSIDE_MANAGED_ROOT")
    return {**record, "bundle_path": str(bundle_path), "loaded": loaded}


def prepare_r01_source(
    request: dict[str, Any],
    *,
    parent: dict[str, Any],
    path: Path = DATABASE_PATH,
    capture_fn: Any = None,
) -> dict[str, Any]:
    if request.get("confirmed") is not True:
        raise RuntimeError("R01_SOURCE_PREPARE_CONFIRMATION_REQUIRED")
    if str(request.get("owner_confirmation") or "") != SOURCE_PREPARE_CONFIRMATION:
        raise RuntimeError("R01_SOURCE_PREPARE_OWNER_AUTHORITY_INVALID")
    if str(request.get("research_id") or "") != parent["research_id"]:
        raise RuntimeError("R01_SOURCE_PREPARE_RESEARCH_STALE")
    if str(request.get("expected_parent_strategy_id") or "") != parent["parent_strategy_id"]:
        raise RuntimeError("R01_SOURCE_PREPARE_PARENT_STALE")

    from_date = _parse_source_date(request.get("from_date"), "from_date")
    to_date = _parse_source_date(request.get("to_date"), "to_date")
    if from_date >= to_date:
        raise ValueError("R01_SOURCE_DATE_RANGE_INVALID")
    frozen_parent_ea = _frozen_parent_ea_path(parent, path=path)
    frozen_parent_set = _frozen_parent_set_path(parent, path=path)
    capture_parent = dict(parent)
    capture_parent["_frozen_ea_source_path"] = str(frozen_parent_ea)
    capture_parent["_frozen_set_source_path"] = str(frozen_parent_set)

    request_identity = {
        "schema": SOURCE_PRODUCER_SCHEMA,
        "producer_version": SOURCE_PRODUCER_VERSION,
        "research_id": parent["research_id"],
        "research_parent_id": parent["research_parent_id"],
        "parent_strategy_id": parent["parent_strategy_id"],
        "parent_authority_sha256": parent["parent_authority_sha256"],
        "source_challenger_id": parent["source_challenger_id"],
        "from_date": from_date.date().isoformat(),
        "to_date": to_date.date().isoformat(),
    }
    token = stable_hash(request_identity)[:20]
    base = _source_root(parent["research_id"])
    base.mkdir(parents=True, exist_ok=True)
    staging = _managed_path(base / (".staging-" + token))
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=False)

    try:
        capture = (capture_fn or _capture_with_mt5)(
            capture_parent,
            staging=staging,
            from_date=from_date,
            to_date=to_date,
            token=token,
        )
        identity = capture["identity"]
        identity_sha = str(identity.get("source_identity_sha256") or "")
        if not identity_sha or stable_hash({k:v for k,v in identity.items() if k != "source_identity_sha256"}) != identity_sha:
            raise RuntimeError("R01_MT5_SOURCE_IDENTITY_HASH_INVALID")

        provenance_core = {
            **request_identity,
            **capture["capture_provenance"],
        }
        source_identity_core = {
            "schema": SOURCE_BUNDLE_SCHEMA,
            "producer_schema": SOURCE_PRODUCER_SCHEMA,
            "producer_version": SOURCE_PRODUCER_VERSION,
            "research_id": parent["research_id"],
            "research_parent_id": parent["research_parent_id"],
            "parent_strategy_id": parent["parent_strategy_id"],
            "parent_authority_sha256": parent["parent_authority_sha256"],
            "ea_sha256": parent["ea_sha256"],
            "ea_semantic_version": parent["ea_semantic_version"],
            "feature_contract": FEATURE_CONTRACT,
            "strategy_contract": parent["strategy_contract"],
            "resolver_version": parent["mtf_resolver_version"],
            "strategy_geometry": parent["strategy_geometry"],
            "main_symbol": parent["main_symbol"],
            "relative_symbol": parent["relative_symbol"],
            "broker": capture["broker"],
            "feed": capture["feed"],
            "source_timezone": capture["source_timezone"],
            "source_timezone_authority": identity["source_timezone_authority"],
            "point_size": capture["point_size"],
            "mt5_source_identity": identity,
            "source_identity_sha256": identity_sha,
            "source_time_coverage": capture["coverage"],
            "files": {
                "ea_cp32": capture["ea_cp32"],
                "role_bars": capture["role_files"],
            },
        }
        source_id = "RSRC-" + stable_hash(source_identity_core)[:24]
        created_utc = utc_now()
        bundle = {
            **source_identity_core,
            "source_id": source_id,
            "creation_provenance": {
                **provenance_core,
                "created_utc": created_utc,
            },
        }
        final_root = _managed_path(base / source_id)
        if final_root.exists():
            existing_bundle = final_root / "source_bundle.json"
            if not existing_bundle.is_file():
                raise RuntimeError("R01_SOURCE_IDENTITY_COLLISION")
            existing = json.loads(existing_bundle.read_text(encoding="utf-8"))
            existing_core = dict(existing)
            existing_core.pop("source_id", None)
            existing_provenance = dict(existing_core.pop("creation_provenance", {}) or {})
            existing_created = str(existing_provenance.get("created_utc") or "")
            if existing_core != source_identity_core or not existing_created:
                raise RuntimeError("R01_SOURCE_IDENTITY_COLLISION")
            bundle = existing
            created_utc = existing_created
            shutil.rmtree(staging)
        else:
            _write_json(staging / "source_bundle.json", bundle)
            staging.replace(final_root)

        bundle_path = final_root / "source_bundle.json"
        loaded = load_source_bundle(bundle_path, parent=parent)
        coverage = capture["coverage"]["ea_cp32"]
        record = {
            "source_id": source_id,
            "research_id": parent["research_id"],
            "parent_strategy_id": parent["parent_strategy_id"],
            "parent_authority_sha256": parent["parent_authority_sha256"],
            "bundle_path": str(bundle_path.resolve()),
            "bundle_sha256": loaded["bundle_sha256"],
            "source_identity_sha256": identity_sha,
            "broker": str(capture["broker"]),
            "feed": str(capture["feed"]),
            "source_timezone": str(capture["source_timezone"]),
            "main_symbol": parent["main_symbol"],
            "relative_symbol": parent["relative_symbol"],
            "data_start_utc": str(coverage["start_utc"]),
            "data_end_utc": str(coverage["end_utc"]),
            "row_coverage": capture["coverage"],
            "provenance": bundle["creation_provenance"],
            "created_utc": str(created_utc),
        }
        return _persist_source(record, path=path)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
