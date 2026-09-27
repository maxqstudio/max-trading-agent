from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import EA_BASELINE, OPTIMIZER_ARTIFACT_ROOT
from .optimizer_core import (
    EA_STEM,
    EXPERT_SUBDIR,
    OPTIMIZER_METRICS_CSV,
    OPTIMIZER_REPORT_XML,
    TESTER_SET,
    build_set_text,
    build_tester_ini,
    optimization_report_identity,
    report_matches_request,
    sha256_file,
)

OPTIMIZER_EVIDENCE_ROOT = OPTIMIZER_ARTIFACT_ROOT


class ReportPending(RuntimeError):
    pass


def job_evidence_dir(job_id: str) -> Path:
    path = OPTIMIZER_EVIDENCE_ROOT / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def round_evidence_dir(job_id: str, round_no: int) -> Path:
    path = job_evidence_dir(job_id) / f"round_{round_no:02d}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Immutable evidence already exists: {path}")
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_state_snapshot(job_id: str, round_no: int, state: dict[str, Any]) -> Path:
    directory = round_evidence_dir(job_id, round_no)
    existing = sorted(directory.glob("state_*.json"))
    sequence = len(existing) + 1
    phase = re.sub(r"[^A-Z0-9_]+", "_", str(state.get("phase") or "UNKNOWN").upper())
    path = directory / f"state_{sequence:03d}_{phase}.json"
    write_json(path, state)
    return path


def _read_text_flexible(path: Path) -> str:
    if not path.exists():
        return ""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeError:
            pass
    sample = raw[:512]
    if sample and sample.count(b"\x00") >= max(2, len(sample) // 8):
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


def compile_summary(text: str) -> dict[str, Any]:
    matches = list(
        re.finditer(
            r"Result:\s*(\d+)\s+errors?\s*,\s*(\d+)\s+warnings?",
            str(text or ""),
            re.IGNORECASE,
        )
    )
    if not matches:
        return {"found": False, "errors": None, "warnings": None, "line": None}
    match = matches[-1]
    return {
        "found": True,
        "errors": int(match.group(1)),
        "warnings": int(match.group(2)),
        "line": match.group(0),
    }


def compile_ea(request: dict[str, Any], job_id: str) -> dict[str, Any]:
    source = EA_BASELINE.resolve()
    frozen_sha = str(request["ea"]["sha256"])
    if not source.is_file():
        raise FileNotFoundError(f"Frozen EA source missing: {source}")
    current_sha = sha256_file(source)
    if current_sha != frozen_sha:
        raise RuntimeError("EA_CHANGED_AFTER_REQUEST_FREEZE")

    data_root = Path(request["mt5"]["data_root"])
    metaeditor = Path(request["mt5"]["metaeditor"])
    if not metaeditor.is_file():
        raise FileNotFoundError("METAEDITOR_UNAVAILABLE")

    expert_dir = data_root / "MQL5" / "Experts" / EXPERT_SUBDIR
    expert_dir.mkdir(parents=True, exist_ok=True)
    deployed = expert_dir / f"{EA_STEM}.mq5"
    shutil.copy2(source, deployed)
    os.utime(deployed, None)
    deployed_sha = sha256_file(deployed)
    if deployed_sha != frozen_sha:
        raise RuntimeError("EA_DEPLOYMENT_HASH_MISMATCH")

    ex5 = deployed.with_suffix(".ex5")
    if ex5.exists():
        try:
            ex5.unlink()
        except OSError as exc:
            raise RuntimeError(f"STALE_EX5_CANNOT_BE_CLEARED: {ex5}") from exc
    compile_log = deployed.with_suffix(".log")
    if compile_log.exists():
        try:
            compile_log.unlink()
        except OSError as exc:
            raise RuntimeError(f"STALE_COMPILE_LOG_CANNOT_BE_CLEARED: {compile_log}") from exc

    evidence = job_evidence_dir(job_id) / "compile"
    evidence.mkdir(parents=True, exist_ok=True)
    command = [str(metaeditor), f"/compile:{deployed}", "/log"]
    (evidence / "compile_command.txt").write_text(
        subprocess.list2cmdline(command) + "\n",
        encoding="utf-8",
    )
    shutil.copy2(source, evidence / "canonical_ea_source.mq5")

    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log_path = compile_log
    log_text = _read_text_flexible(log_path)
    if not log_text:
        log_text = (process.stdout or "") + (process.stderr or "")
    (evidence / "metaeditor_compile.log").write_text(log_text, encoding="utf-8")

    summary = compile_summary(log_text)
    context = {
        "metaeditor": str(metaeditor),
        "terminal_data_root": str(data_root),
        "source": str(source),
        "source_sha256": current_sha,
        "deployed_mq5": str(deployed),
        "deployed_sha256": deployed_sha,
        "expected_ex5": str(ex5),
        "process_returncode": process.returncode,
        "returncode_authority": "DIAGNOSTIC_ONLY",
        "compile_summary": summary,
    }
    write_json(evidence / "compile_context.json", context)

    if not summary["found"]:
        raise RuntimeError("METAEDITOR_COMPILE_SUMMARY_MISSING")
    if int(summary["errors"]) != 0:
        raise RuntimeError(f"METAEDITOR_COMPILE_ERRORS: {summary['errors']}")
    if not ex5.is_file():
        raise RuntimeError("METAEDITOR_EX5_MISSING")

    shutil.copy2(ex5, evidence / "compiled_ea.ex5")
    return {
        **context,
        "status": "PASS",
        "ex5_sha256": sha256_file(ex5),
        "expert_name": f"{EXPERT_SUBDIR}\\{EA_STEM}",
    }


def _report_roots(request: dict[str, Any]) -> list[Path]:
    data_root = Path(request["mt5"]["data_root"])
    terminal = Path(request["mt5"]["terminal"])
    return [
        data_root / "MQL5" / "Profiles" / "Tester",
        data_root,
        terminal.parent,
    ]


def compatible_reports(request: dict[str, Any]) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for root in _report_roots(request):
        if not root.is_dir():
            continue
        for path in root.glob("*.xml"):
            key = str(path.resolve()).casefold()
            if key in seen:
                continue
            seen.add(key)
            if report_matches_request(path, request):
                found.append(path)
    return sorted(found, key=lambda item: item.stat().st_mtime_ns, reverse=True)


def report_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def snapshot_compatible_reports(request: dict[str, Any]) -> list[dict[str, Any]]:
    return [report_fingerprint(path) for path in compatible_reports(request)]


def _snapshot_index(snapshot: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for item in snapshot:
        result[str(Path(item["path"]).resolve()).casefold()] = (
            int(item["size"]),
            int(item["mtime_ns"]),
        )
    return result


def is_new_or_changed(path: Path, snapshot: list[dict[str, Any]]) -> bool:
    current = report_fingerprint(path)
    prior = _snapshot_index(snapshot).get(
        str(Path(current["path"]).resolve()).casefold()
    )
    return prior is None or prior != (current["size"], current["mtime_ns"])


def wait_for_fresh_report(
    request: dict[str, Any],
    *,
    prelaunch_snapshot: list[dict[str, Any]],
    timeout_sec: int,
) -> tuple[Path | None, str]:
    deadline = time.time() + max(0, int(timeout_sec))
    last_signature: tuple[str, int, int] | None = None
    stable_count = 0
    while True:
        candidates = [
            path
            for path in compatible_reports(request)
            if is_new_or_changed(path, prelaunch_snapshot)
        ]
        if candidates:
            candidates.sort(key=lambda item: item.stat().st_mtime_ns, reverse=True)
            preferred = [
                path for path in candidates if path.name.casefold() == OPTIMIZER_REPORT_XML.casefold()
            ]
            selected = preferred[0] if preferred else candidates[0]
            mode = (
                "EXPECTED_CANONICAL_REPORT"
                if selected.name.casefold() == OPTIMIZER_REPORT_XML.casefold()
                else "FRESH_FINGERPRINT"
            )
            stat = selected.stat()
            signature = (str(selected.resolve()).casefold(), stat.st_size, stat.st_mtime_ns)
            if signature == last_signature:
                stable_count += 1
            else:
                last_signature = signature
                stable_count = 0
            if stable_count >= 2:
                return selected, mode
        if time.time() >= deadline:
            return None, ""
        time.sleep(1.0)


def optimizer_run_nonce(job_id: str, round_no: int) -> int:
    value = int(
        hashlib.sha256(f"{job_id}:{round_no}".encode("utf-8")).hexdigest()[:8],
        16,
    ) & 0x7FFFFFFF
    return value or 1


def prepare_round(
    request: dict[str, Any],
    *,
    job_id: str,
    round_no: int,
    search_space: dict[str, Any],
) -> dict[str, Any]:
    if sha256_file(EA_BASELINE) != request["ea"]["sha256"]:
        raise RuntimeError("EA_CHANGED_AFTER_REQUEST_FREEZE")

    data_root = Path(request["mt5"]["data_root"])
    preset_dir = data_root / "MQL5" / "Profiles" / "Tester"
    preset_dir.mkdir(parents=True, exist_ok=True)
    set_path = preset_dir / TESTER_SET

    nonce = optimizer_run_nonce(job_id, round_no)
    metrics_path = data_root / "MQL5" / "Files" / OPTIMIZER_METRICS_CSV
    metrics_path.parent.mkdir(parents=True, exist_ok=True)

    set_text = build_set_text(request, search_space, optimizer_run_nonce=nonce)
    set_path.write_text(set_text, encoding="utf-8")

    evidence = round_evidence_dir(job_id, round_no)
    evidence_set = evidence / TESTER_SET
    evidence_set.write_text(set_text, encoding="utf-8")

    ini_path = evidence / f"round_{round_no:02d}.ini"
    ini_path.write_text(
        build_tester_ini(
            request,
            expert_name=f"{EXPERT_SUBDIR}\\{EA_STEM}",
            report_name=OPTIMIZER_REPORT_XML,
        ),
        encoding="utf-8",
    )
    write_json(evidence / "search_space.json", search_space)

    return {
        "phase": "PREPARED",
        "search_space": search_space,
        "set_path": str(set_path),
        "ini_path": str(ini_path),
        "report_name": OPTIMIZER_REPORT_XML,
        "optimizer_metrics_path": str(metrics_path),
        "optimizer_run_nonce": nonce,
        "optimize_params": list(request["optimize_params"]),
    }


def clear_stale_sidecar(state: dict[str, Any]) -> None:
    metrics_path = Path(state["optimizer_metrics_path"])
    if metrics_path.exists():
        try:
            metrics_path.unlink()
        except OSError as exc:
            raise RuntimeError(f"STALE_SIDECAR_CANNOT_BE_CLEARED: {metrics_path}") from exc
    if metrics_path.exists():
        raise RuntimeError(f"STALE_SIDECAR_CANNOT_BE_CLEARED: {metrics_path}")


def _matching_terminal_pids(terminal: Path) -> list[int]:
    if os.name != "nt":
        return []
    target = str(terminal.resolve()).replace("'", "''")
    command = (
        "$target='" + target + "';"
        "$rows=Get-CimInstance Win32_Process -Filter \"Name='terminal64.exe'\" "
        "-ErrorAction SilentlyContinue | Where-Object { "
        "($_.ExecutablePath -and $_.ExecutablePath -ieq $target) -or "
        "($_.CommandLine -and $_.CommandLine -like ('*' + $target + '*')) };"
        "$rows | ForEach-Object { $_.ProcessId }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=8,
    )
    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        text = line.strip()
        if text.isdigit():
            pids.append(int(text))
    return pids


def launch_mt5(
    request: dict[str, Any],
    *,
    ini_path: str | Path,
    timeout_sec: int,
) -> int:
    terminal = Path(request["mt5"]["terminal"])
    if not terminal.is_file():
        raise FileNotFoundError("MT5_EXECUTABLE_UNAVAILABLE")
    existing = _matching_terminal_pids(terminal)
    if existing:
        raise RuntimeError(
            "MT5_TERMINAL_ALREADY_RUNNING: "
            + ",".join(str(pid) for pid in existing)
        )
    process = subprocess.run(
        [str(terminal), f"/config:{Path(ini_path)}"],
        cwd=str(terminal.parent),
        timeout=int(timeout_sec),
    )
    if process.returncode not in (0, None):
        raise RuntimeError(f"MT5_EXECUTION_FAILURE: exit={process.returncode}")
    return int(process.returncode or 0)


def stage_raw_round_evidence(
    *,
    job_id: str,
    round_no: int,
    report: Path,
    metrics_path: Path,
) -> dict[str, str]:
    evidence = round_evidence_dir(job_id, round_no)
    staged: dict[str, str] = {}
    for source, name, key in (
        (report, "raw_Max_MTF.xml", "raw_report"),
        (metrics_path, "raw_Max_MTF_metrics.csv", "raw_sidecar"),
    ):
        target = evidence / name
        source_sha = sha256_file(source)
        if target.exists():
            if sha256_file(target) != source_sha:
                raise RuntimeError(f"Immutable raw round evidence mismatch: {target}")
        else:
            shutil.copy2(source, target)
        staged[f"{key}_path"] = str(target)
        staged[f"{key}_sha256"] = source_sha
    return staged


def commit_round_evidence(
    *,
    job_id: str,
    round_no: int,
    report: Path,
    metrics_path: Path,
    audit: dict[str, Any],
    passes_payload: list[dict[str, Any]],
    report_selection_mode: str,
) -> dict[str, Any]:
    evidence = round_evidence_dir(job_id, round_no)
    local_report = evidence / OPTIMIZER_REPORT_XML
    local_metrics = evidence / OPTIMIZER_METRICS_CSV

    if local_report.exists() or local_metrics.exists():
        raise FileExistsError("Committed round evidence already exists")
    shutil.copy2(report, local_report)
    shutil.copy2(metrics_path, local_metrics)

    report_identity = optimization_report_identity(local_report)
    report_sha = sha256_file(local_report)
    metrics_sha = sha256_file(local_metrics)
    write_json(evidence / "eligibility_audit.json", audit)
    write_json(
        evidence / "passes.json",
        {
            "schema": "MAX_REBUILD_OPTIMIZER_PASSES_V1",
            "round": round_no,
            "passes": passes_payload,
        },
    )
    write_json(
        evidence / "report_provenance.json",
        {
            "schema": "MAX_REBUILD_OPTIMIZER_REPORT_PROVENANCE_V1",
            "selection_mode": report_selection_mode,
            "report_identity": report_identity,
            "report_fingerprint": report_fingerprint(local_report),
            "report_sha256": report_sha,
            "sidecar_sha256": metrics_sha,
        },
    )
    return {
        "report_path": str(local_report),
        "report_sha256": report_sha,
        "sidecar_path": str(local_metrics),
        "sidecar_sha256": metrics_sha,
        "report_identity": report_identity,
        "report_selection_mode": report_selection_mode,
    }
