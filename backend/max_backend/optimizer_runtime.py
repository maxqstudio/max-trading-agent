from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
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
from .path_safety import remove_owned_path

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


def committed_round_dir(job_id: str, round_no: int) -> Path:
    return round_evidence_dir(job_id, round_no) / "committed"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Immutable evidence already exists: {path}")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise FileExistsError(f"Immutable evidence already exists: {path}")
        os.rename(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


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
    if result.returncode != 0:
        raise RuntimeError("MT5_PROCESS_OWNERSHIP_QUERY_FAILED")
    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        text = line.strip()
        if text.isdigit():
            pids.append(int(text))
    return pids


def optimizer_terminal_process_state(
    request: dict[str, Any],
    *,
    ini_path: str | Path,
) -> dict[str, Any]:
    """Match MT5 ownership by executable and this round's unique INI path."""
    if os.name != "nt":
        return {"status": "UNKNOWN", "processes": []}
    command = (
        "$rows=Get-CimInstance Win32_Process -Filter \"Name='terminal64.exe'\" "
        "-ErrorAction SilentlyContinue | Select-Object ProcessId,ExecutablePath,CommandLine,CreationDate;"
        "if($rows){$rows | ConvertTo-Json -Compress}"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=8,
    )
    if result.returncode != 0:
        raise RuntimeError("MT5_PROCESS_OWNERSHIP_QUERY_FAILED")
    try:
        rows = json.loads((result.stdout or "").strip()) if (result.stdout or "").strip() else []
    except json.JSONDecodeError as exc:
        raise RuntimeError("MT5_PROCESS_OWNERSHIP_QUERY_INVALID") from exc
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise RuntimeError("MT5_PROCESS_OWNERSHIP_QUERY_INVALID")
    expected_exe = str(Path(request["mt5"]["terminal"]).resolve()).casefold()
    expected_ini = str(Path(ini_path).resolve()).replace("/", "\\").casefold()
    owned: list[dict[str, Any]] = []
    unrelated: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        executable = str(row.get("ExecutablePath") or "")
        line = str(row.get("CommandLine") or "")
        item = {
            "pid": int(row.get("ProcessId") or 0),
            "executable_path": executable,
            "command_line": line,
            "creation_time": str(row.get("CreationDate") or ""),
        }
        if executable.casefold() != expected_exe:
            continue
        normalized_line = line.replace("/", "\\").casefold()
        if expected_ini in normalized_line:
            owned.append(item)
        else:
            unrelated.append(item)
    if len(owned) == 1:
        return {"status": "OPTIMIZER_OWNED_RUNNING", "processes": owned}
    if len(owned) > 1:
        return {"status": "OWNERSHIP_AMBIGUOUS", "processes": owned}
    if unrelated:
        return {"status": "UNRELATED_MT5_RUNNING", "processes": unrelated}
    return {"status": "NOT_RUNNING", "processes": []}


def launch_mt5(
    request: dict[str, Any],
    *,
    ini_path: str | Path,
    timeout_sec: int,
    on_process: Any | None = None,
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
    command = [str(terminal), f"/config:{Path(ini_path)}"]
    process = subprocess.Popen(command, cwd=str(terminal.parent))
    if on_process is not None:
        on_process({
            "pid": int(process.pid),
            "executable_path": str(terminal.resolve()),
            "command_line": subprocess.list2cmdline(command),
            "ini_path": str(Path(ini_path).resolve()),
            "started_utc_epoch": time.time(),
        })
    try:
        returncode = process.wait(timeout=int(timeout_sec))
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("MT5_EXECUTION_TIMEOUT_UNCERTAIN") from exc
    if returncode not in (0, None):
        raise RuntimeError(f"MT5_EXECUTION_FAILURE: exit={returncode}")
    return int(returncode or 0)


def stage_raw_round_evidence(
    *,
    job_id: str,
    round_no: int,
    report: Path,
    metrics_path: Path,
    expected_report_fingerprint: dict[str, Any] | None = None,
) -> dict[str, str]:
    evidence = round_evidence_dir(job_id, round_no)
    staging = evidence / ".staging" / "raw-freeze"
    manifest_path = staging / "raw-manifest.json"
    names = {
        "raw_report": "raw_Max_MTF.xml",
        "raw_sidecar": "raw_Max_MTF_metrics.csv",
    }

    def validated_result(manifest: dict[str, Any]) -> dict[str, str]:
        if (
            manifest.get("schema") != "MAX_OPTIMIZER_RAW_FREEZE_V1"
            or manifest.get("job_id") != job_id
            or manifest.get("round") != int(round_no)
            or not isinstance(manifest.get("files"), dict)
        ):
            raise RuntimeError("RAW_ROUND_FREEZE_MANIFEST_INVALID")
        if (
            expected_report_fingerprint is not None
            and manifest.get("source_report_fingerprint") != expected_report_fingerprint
        ):
            raise RuntimeError("RAW_ROUND_FREEZE_SOURCE_FINGERPRINT_MISMATCH")
        result: dict[str, str] = {"raw_manifest_path": str(manifest_path)}
        for key, name in names.items():
            expected = str(manifest["files"].get(name) or "")
            target = staging / name
            if not expected or not target.is_file() or sha256_file(target) != expected:
                raise RuntimeError(f"RAW_ROUND_FREEZE_FILE_INVALID:{name}")
            result[f"{key}_path"] = str(target)
            result[f"{key}_sha256"] = expected
        return result

    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("RAW_ROUND_FREEZE_MANIFEST_INVALID") from exc
        return validated_result(manifest)

    if staging.exists():
        current = report_fingerprint(report)
        if expected_report_fingerprint != current:
            raise RuntimeError("RAW_ROUND_FREEZE_SOURCE_CHANGED")
        remove_owned_path(staging, roots=[OPTIMIZER_EVIDENCE_ROOT])

    if not report.is_file() or not metrics_path.is_file():
        raise FileNotFoundError("RAW_ROUND_FREEZE_SOURCE_MISSING")
    if expected_report_fingerprint is not None and report_fingerprint(report) != expected_report_fingerprint:
        raise RuntimeError("RAW_ROUND_FREEZE_SOURCE_CHANGED")
    staging.mkdir(parents=True, exist_ok=False)
    files: dict[str, str] = {}
    for source, name in (
        (report, names["raw_report"]),
        (metrics_path, names["raw_sidecar"]),
    ):
        temporary = staging / f".{name}.{uuid.uuid4().hex}.tmp"
        target = staging / name
        shutil.copy2(source, temporary)
        source_sha = sha256_file(source)
        if sha256_file(temporary) != source_sha:
            raise RuntimeError(f"RAW_ROUND_FREEZE_COPY_MISMATCH:{name}")
        os.rename(temporary, target)
        files[name] = source_sha
    manifest = {
        "schema": "MAX_OPTIMIZER_RAW_FREEZE_V1",
        "job_id": job_id,
        "round": int(round_no),
        "source_report_fingerprint": report_fingerprint(report),
        "files": files,
    }
    write_json(manifest_path, manifest)
    return validated_result(manifest)


def discard_raw_round_staging(job_id: str, round_no: int) -> int:
    staging = round_evidence_dir(job_id, round_no) / ".staging" / "raw-freeze"
    return remove_owned_path(staging, roots=[OPTIMIZER_EVIDENCE_ROOT])


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
    final = evidence / "committed"
    if not report.is_file() or not metrics_path.is_file():
        raise FileNotFoundError("ROUND_SOURCE_EVIDENCE_MISSING")
    report_sha = sha256_file(report)
    metrics_sha = sha256_file(metrics_path)
    if final.exists():
        manifest = verify_committed_round_bundle(final, job_id=job_id, round_no=round_no)
        if (
            manifest["files"].get(OPTIMIZER_REPORT_XML) != report_sha
            or manifest["files"].get(OPTIMIZER_METRICS_CSV) != metrics_sha
        ):
            raise RuntimeError("COMMITTED_ROUND_BUNDLE_MISMATCH")
        try:
            provenance = json.loads(
                (final / "report_provenance.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("COMMITTED_ROUND_PROVENANCE_INVALID") from exc
        report_identity = provenance.get("report_identity") if isinstance(provenance, dict) else None
        if (
            not isinstance(provenance, dict)
            or provenance.get("schema") != "MAX_REBUILD_OPTIMIZER_REPORT_PROVENANCE_V1"
            or provenance.get("selection_mode") != report_selection_mode
            or provenance.get("report_sha256") != report_sha
            or provenance.get("sidecar_sha256") != metrics_sha
            or not isinstance(report_identity, dict)
            or report_identity.get("path") != str((final / OPTIMIZER_REPORT_XML).resolve())
        ):
            raise RuntimeError("COMMITTED_ROUND_PROVENANCE_AUTHORITY_MISMATCH")
    else:
        staging_parent = evidence / ".staging"
        staging_parent.mkdir(parents=True, exist_ok=True)
        staging = staging_parent / uuid.uuid4().hex
        staging.mkdir()
        try:
            staged_report = staging / OPTIMIZER_REPORT_XML
            staged_metrics = staging / OPTIMIZER_METRICS_CSV
            shutil.copy2(report, staged_report)
            shutil.copy2(metrics_path, staged_metrics)
            shutil.copy2(report, staging / "raw_Max_MTF.xml")
            shutil.copy2(metrics_path, staging / "raw_Max_MTF_metrics.csv")

            report_identity = optimization_report_identity(staged_report)
            report_identity["path"] = str((final / OPTIMIZER_REPORT_XML).resolve())
            report_fingerprint = report_fingerprint_for_publication(
                staged_report,
                final / OPTIMIZER_REPORT_XML,
            )
            _write_synced_json(staging / "eligibility_audit.json", audit)
            _write_synced_json(
                staging / "passes.json",
                {
                    "schema": "MAX_REBUILD_OPTIMIZER_PASSES_V1",
                    "round": round_no,
                    "passes": passes_payload,
                },
            )
            _write_synced_json(
                staging / "report_provenance.json",
                {
                    "schema": "MAX_REBUILD_OPTIMIZER_REPORT_PROVENANCE_V1",
                    "selection_mode": report_selection_mode,
                    "report_identity": report_identity,
                    "report_fingerprint": report_fingerprint,
                    "report_sha256": report_sha,
                    "sidecar_sha256": metrics_sha,
                },
            )
            files = {
                item.name: sha256_file(item)
                for item in staging.iterdir()
                if item.is_file()
            }
            manifest = {
                "schema": "MAX_OPTIMIZER_ROUND_BUNDLE_V1",
                "job_id": job_id,
                "round": int(round_no),
                "files": files,
            }
            _write_synced_json(staging / "manifest.json", manifest)
            verify_committed_round_bundle(staging, job_id=job_id, round_no=round_no)
            try:
                os.rename(staging, final)
            except OSError:
                # Another publisher may have won the same immutable bundle
                # race. Accept only its exact verified bytes.
                if not final.is_dir():
                    raise
                concurrent = verify_committed_round_bundle(
                    final,
                    job_id=job_id,
                    round_no=round_no,
                )
                if (
                    concurrent["files"].get(OPTIMIZER_REPORT_XML) != report_sha
                    or concurrent["files"].get(OPTIMIZER_METRICS_CSV) != metrics_sha
                ):
                    raise RuntimeError("COMMITTED_ROUND_BUNDLE_MISMATCH")
                provenance = json.loads(
                    (final / "report_provenance.json").read_text(encoding="utf-8")
                )
                concurrent_identity = provenance.get("report_identity") if isinstance(provenance, dict) else None
                if (
                    not isinstance(concurrent_identity, dict)
                    or provenance.get("selection_mode") != report_selection_mode
                    or provenance.get("report_sha256") != report_sha
                    or provenance.get("sidecar_sha256") != metrics_sha
                    or concurrent_identity.get("path") != str((final / OPTIMIZER_REPORT_XML).resolve())
                ):
                    raise RuntimeError("COMMITTED_ROUND_PROVENANCE_AUTHORITY_MISMATCH")
                report_identity = concurrent_identity
        except Exception:
            # A remaining .staging/<attempt> is intentionally isolated and is
            # never interpreted as committed round evidence.
            raise

    local_report = final / OPTIMIZER_REPORT_XML
    local_metrics = final / OPTIMIZER_METRICS_CSV
    return {
        "report_path": str(local_report),
        "report_sha256": report_sha,
        "sidecar_path": str(local_metrics),
        "sidecar_sha256": metrics_sha,
        "report_identity": report_identity,
        "report_selection_mode": report_selection_mode,
        "bundle_path": str(final),
        "manifest_sha256": sha256_file(final / "manifest.json"),
    }


def report_fingerprint_for_publication(source: Path, published_path: Path) -> dict[str, Any]:
    stat = source.stat()
    return {
        "path": str(published_path.resolve()),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _write_synced_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def verify_committed_round_bundle(
    directory: Path,
    *,
    job_id: str,
    round_no: int,
) -> dict[str, Any]:
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("COMMITTED_ROUND_MANIFEST_MISSING")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("COMMITTED_ROUND_MANIFEST_INVALID") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "MAX_OPTIMIZER_ROUND_BUNDLE_V1"
        or manifest.get("job_id") != job_id
        or manifest.get("round") != int(round_no)
        or not isinstance(manifest.get("files"), dict)
    ):
        raise RuntimeError("COMMITTED_ROUND_MANIFEST_AUTHORITY_MISMATCH")
    files = manifest["files"]
    for name, expected in files.items():
        if not isinstance(name, str) or Path(name).name != name or name == "manifest.json":
            raise RuntimeError("COMMITTED_ROUND_MANIFEST_PATH_INVALID")
        path = directory / name
        if not path.is_file() or sha256_file(path) != str(expected):
            raise RuntimeError(f"COMMITTED_ROUND_BUNDLE_FILE_INVALID:{name}")
    required = {
        OPTIMIZER_REPORT_XML,
        OPTIMIZER_METRICS_CSV,
        "raw_Max_MTF.xml",
        "raw_Max_MTF_metrics.csv",
        "eligibility_audit.json",
        "passes.json",
        "report_provenance.json",
    }
    if not required.issubset(files):
        raise RuntimeError("COMMITTED_ROUND_BUNDLE_INCOMPLETE")
    return manifest
