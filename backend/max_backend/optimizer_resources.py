from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import subprocess
import threading
import time
from datetime import datetime
from copy import deepcopy
from pathlib import Path
from typing import Any

GIB = 1024 ** 3
MIB = 1024 ** 2
RESOURCE_POLICY_SCHEMA = "MAX_OPTIMIZER_RESOURCE_POLICY_V2"
RESOURCE_MODES = {"AUTO_SAFE", "CUSTOM"}
RESOURCE_CACHE_SECONDS = 5.0
MAX_RESOURCE_BYTES = (1 << 63) - 1

FALLBACK_TERMINAL_COMMIT_BYTES = 14 * GIB
FALLBACK_AGENT_COMMIT_BYTES = 12 * GIB
FALLBACK_TERMINAL_WORKING_SET_BYTES = 6 * GIB
FALLBACK_AGENT_WORKING_SET_BYTES = 4 * GIB
HARD_PHYSICAL_RESERVE_RATIO = 0.20
HARD_PHYSICAL_RESERVE_MIN_BYTES = 4 * GIB
SYSTEM_COMMIT_RESERVE_RATIO = 0.10
SYSTEM_COMMIT_RESERVE_MIN_BYTES = 4 * GIB
MAX_COMMIT_RESERVE_MIN_BYTES = 2 * GIB
COMMIT_PRESSURE_RATIO = 0.05
COMMIT_PRESSURE_MIN_BYTES = 1 * GIB
CALIBRATION_MARGIN_NUMERATOR = 5
CALIBRATION_MARGIN_DENOMINATOR = 4

_DEFAULT_RESOURCE_SETTINGS = {
    "mode": "AUTO_SAFE",
    "custom_max_local_agents": 1,
    "custom_min_free_ram_gb": 4.0,
    "custom_cpu_reserve_logical": 2,
}

_RESOURCE_CACHE_LOCK = threading.Lock()
_RESOURCE_CACHE: dict[str, Any] = {"key": None, "time": 0.0, "value": None}

def default_resource_settings() -> dict[str, Any]:
    return deepcopy(_DEFAULT_RESOURCE_SETTINGS)


def validate_resource_settings(value: Any) -> dict[str, Any]:
    if value is None:
        value = default_resource_settings()
    if not isinstance(value, dict) or set(value) != set(_DEFAULT_RESOURCE_SETTINGS):
        raise ValueError("OPTIMIZER_RESOURCE_SETTINGS_SHAPE_INVALID")
    mode = str(value.get("mode") or "").upper()
    if mode not in RESOURCE_MODES:
        raise ValueError("OPTIMIZER_RESOURCE_MODE_INVALID")
    max_agents = value.get("custom_max_local_agents")
    cpu_reserve = value.get("custom_cpu_reserve_logical")
    min_free = value.get("custom_min_free_ram_gb")
    if isinstance(max_agents, bool) or not isinstance(max_agents, int) or max_agents < 1 or max_agents > 256:
        raise ValueError("OPTIMIZER_RESOURCE_MAX_AGENTS_INVALID")
    if isinstance(cpu_reserve, bool) or not isinstance(cpu_reserve, int) or cpu_reserve < 0 or cpu_reserve > 256:
        raise ValueError("OPTIMIZER_RESOURCE_CPU_RESERVE_INVALID")
    if isinstance(min_free, bool) or not isinstance(min_free, (int, float)):
        raise ValueError("OPTIMIZER_RESOURCE_MIN_FREE_RAM_INVALID")
    min_free_value = float(min_free)
    if not math.isfinite(min_free_value) or min_free_value <= 0 or min_free_value > 1024:
        raise ValueError("OPTIMIZER_RESOURCE_MIN_FREE_RAM_INVALID")
    return {
        "mode": mode,
        "custom_max_local_agents": int(max_agents),
        "custom_min_free_ram_gb": min_free_value,
        "custom_cpu_reserve_logical": int(cpu_reserve),
    }

def _current_process_working_set_bytes() -> int:
    if os.name != "nt":
        return 0
    from ctypes import wintypes
    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise RuntimeError("OPTIMIZER_RESOURCE_BACKEND_MEMORY_QUERY_FAILED")
    return int(counters.WorkingSetSize)


def _windows_system_resource_snapshot() -> dict[str, int]:
    """Read physical memory and system commit from the documented native API."""
    if os.name != "nt":
        raise RuntimeError("OPTIMIZER_RESOURCE_DETECTION_REQUIRES_WINDOWS")
    from ctypes import wintypes

    class PERFORMANCE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("CommitTotal", ctypes.c_size_t),
            ("CommitLimit", ctypes.c_size_t),
            ("CommitPeak", ctypes.c_size_t),
            ("PhysicalTotal", ctypes.c_size_t),
            ("PhysicalAvailable", ctypes.c_size_t),
            ("SystemCache", ctypes.c_size_t),
            ("KernelTotal", ctypes.c_size_t),
            ("KernelPaged", ctypes.c_size_t),
            ("KernelNonpaged", ctypes.c_size_t),
            ("PageSize", ctypes.c_size_t),
            ("HandleCount", wintypes.DWORD),
            ("ProcessCount", wintypes.DWORD),
            ("ThreadCount", wintypes.DWORD),
        ]

    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetPerformanceInfo.argtypes = [
        ctypes.POINTER(PERFORMANCE_INFORMATION),
        wintypes.DWORD,
    ]
    psapi.GetPerformanceInfo.restype = wintypes.BOOL
    value = PERFORMANCE_INFORMATION()
    value.cb = ctypes.sizeof(value)
    if not psapi.GetPerformanceInfo(ctypes.byref(value), value.cb):
        raise RuntimeError("OPTIMIZER_RESOURCE_SYSTEM_TELEMETRY_FAILED")
    page_size = int(value.PageSize)
    if page_size <= 0:
        raise RuntimeError("OPTIMIZER_RESOURCE_SYSTEM_TELEMETRY_INVALID")
    commit_charge = int(value.CommitTotal) * page_size
    commit_limit = int(value.CommitLimit) * page_size
    total_ram = int(value.PhysicalTotal) * page_size
    available_ram = int(value.PhysicalAvailable) * page_size
    if min(commit_limit, total_ram, available_ram) <= 0 or commit_charge > commit_limit:
        raise RuntimeError("OPTIMIZER_RESOURCE_SYSTEM_TELEMETRY_INVALID")
    return {
        "total_ram_bytes": total_ram,
        "available_ram_bytes": available_ram,
        "commit_charge_bytes": commit_charge,
        "commit_limit_bytes": commit_limit,
        "commit_headroom_bytes": commit_limit - commit_charge,
    }


def _configured_local_agent_count(data_root: str | Path) -> int:
    appdata = os.getenv("APPDATA")
    if not appdata:
        return 0
    terminal_id = Path(data_root).name
    tester_root = Path(appdata) / "MetaQuotes" / "Tester" / terminal_id
    if not tester_root.is_dir():
        return 0
    return sum(1 for path in tester_root.glob("Agent-127.0.0.1-*") if path.is_dir())

def _windows_capacity_snapshot(terminal: Path) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("OPTIMIZER_RESOURCE_DETECTION_REQUIRES_WINDOWS")
    target = str(terminal.resolve()).replace("'", "''")
    command = (
        "$cpu=Get-CimInstance Win32_Processor | Select-Object -First 1 NumberOfCores,NumberOfLogicalProcessors;"
        "$os=Get-CimInstance Win32_OperatingSystem;"
        "$ver=(Get-Item '" + target + "').VersionInfo;"
        "[pscustomobject]@{"
        "physical_cores=[int]$cpu.NumberOfCores;"
        "logical_processors=[int]$cpu.NumberOfLogicalProcessors;"
        "total_ram_bytes=[int64]$os.TotalVisibleMemorySize*1KB;"
        "available_ram_bytes=[int64]$os.FreePhysicalMemory*1KB;"
        "total_virtual_bytes=[int64]$os.TotalVirtualMemorySize*1KB;"
        "available_virtual_bytes=[int64]$os.FreeVirtualMemory*1KB;"
        "windows_version=[string]$os.Version;"
        "windows_build=[string]$os.BuildNumber;"
        "mt5_build=[string]$ver.FileVersion"
        "}|ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=8,
    )
    if result.returncode != 0:
        raise RuntimeError("OPTIMIZER_RESOURCE_DETECTION_FAILED")
    try:
        value = json.loads((result.stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("OPTIMIZER_RESOURCE_DETECTION_INVALID") from exc
    if not isinstance(value, dict):
        raise RuntimeError("OPTIMIZER_RESOURCE_DETECTION_INVALID")
    value.update(_windows_system_resource_snapshot())
    return value

def detect_resource_capacity(mt5: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    terminal = Path(str(mt5.get("terminal") or ""))
    data_root = Path(str(mt5.get("data_root") or ""))
    if not terminal.is_file():
        raise RuntimeError("OPTIMIZER_RESOURCE_MT5_TERMINAL_MISSING")
    key = f"{terminal.resolve()}|{data_root.resolve() if data_root.exists() else data_root}"
    now = time.monotonic()
    with _RESOURCE_CACHE_LOCK:
        cached = _RESOURCE_CACHE.get("value")
        if (
            not force
            and _RESOURCE_CACHE.get("key") == key
            and cached is not None
            and now - float(_RESOURCE_CACHE.get("time") or 0.0) < RESOURCE_CACHE_SECONDS
        ):
            value = deepcopy(cached)
            value["max_backend_rss_bytes"] = _current_process_working_set_bytes()
            return value
    value = _windows_capacity_snapshot(terminal)
    value.update({
        "configured_local_agent_capacity": _configured_local_agent_count(data_root),
        "max_backend_rss_bytes": _current_process_working_set_bytes(),
        "terminal": str(terminal.resolve()),
        "data_root": str(data_root.resolve()),
    })
    with _RESOURCE_CACHE_LOCK:
        _RESOURCE_CACHE.update({"key": key, "time": now, "value": deepcopy(value)})
    return value


def clear_resource_capacity_cache() -> None:
    with _RESOURCE_CACHE_LOCK:
        _RESOURCE_CACHE.update({"key": None, "time": 0.0, "value": None})

def _required_int(value: Any, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError("OPTIMIZER_RESOURCE_CAPACITY_INVALID")
    if value < (0 if allow_zero else 1) or value > MAX_RESOURCE_BYTES:
        raise RuntimeError("OPTIMIZER_RESOURCE_CAPACITY_INVALID")
    return value


def workload_compatibility_key(workload: dict[str, Any]) -> str:
    """Hash the workload dimensions that materially affect MT5 memory use."""
    grid = workload.get("raw_complete_grid_combinations")
    try:
        grid_count = max(0, int(grid or 0))
    except (TypeError, ValueError, OverflowError):
        grid_count = 0
    grid_bucket = "UNKNOWN" if grid_count <= 0 else f"10^{len(str(grid_count)) - 1}"
    identity = {
        "ea_sha256": str(workload.get("ea_sha256") or "").lower(),
        "mt5_build": str(workload.get("mt5_build") or ""),
        "symbol": str(workload.get("symbol") or "").casefold(),
        "period": str(workload.get("period") or "").upper(),
        "history_span_bucket": str(workload.get("history_span_bucket") or "UNKNOWN"),
        "tick_model": str(workload.get("tick_model_name") or workload.get("tick_model") or ""),
        "optimization": str(workload.get("optimization_name") or workload.get("optimization") or ""),
        "optimized_parameter_count": str(workload.get("optimized_parameter_count") or "0"),
        "search_space_bucket": grid_bucket,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def history_span_bucket(from_date: str, to_date: str) -> str:
    """Classify requested history duration for conservative memory estimates."""
    try:
        start = datetime.strptime(str(from_date), "%Y.%m.%d").date()
        end = datetime.strptime(str(to_date), "%Y.%m.%d").date()
    except (TypeError, ValueError):
        raise RuntimeError("OPTIMIZER_RESOURCE_WORKLOAD_DATE_INVALID")
    days = (end - start).days
    if days < 0:
        raise RuntimeError("OPTIMIZER_RESOURCE_WORKLOAD_DATE_INVALID")
    if days <= 30:
        return "0-30D"
    if days <= 180:
        return "31-180D"
    if days <= 365:
        return "181-365D"
    if days <= 365 * 3:
        return "1-3Y"
    return "3Y+"


def _workload_estimate_factor(workload: dict[str, Any]) -> float:
    history_factor = {
        "0-30D": 0.50,
        "31-180D": 0.70,
        "181-365D": 0.85,
        "1-3Y": 0.95,
        "3Y+": 1.0,
    }.get(str(workload.get("history_span_bucket") or ""), 1.0)
    try:
        dimensions = max(0, int(workload.get("optimized_parameter_count") or 0))
    except (TypeError, ValueError, OverflowError):
        dimensions = 0
    return max(history_factor, min(1.0, 0.50 + dimensions / 34))


def _calibration_estimates(
    workload: dict[str, Any], calibration: dict[str, Any] | None,
) -> tuple[str, int, int, int, int]:
    factor = _workload_estimate_factor(workload)
    fallback_terminal_commit = max(6 * GIB, math.ceil(FALLBACK_TERMINAL_COMMIT_BYTES * factor))
    fallback_agent_commit = max(6 * GIB, math.ceil(FALLBACK_AGENT_COMMIT_BYTES * factor))
    fallback_terminal_ws = max(3 * GIB, math.ceil(FALLBACK_TERMINAL_WORKING_SET_BYTES * factor))
    fallback_agent_ws = max(2 * GIB, math.ceil(FALLBACK_AGENT_WORKING_SET_BYTES * factor))
    if not isinstance(calibration, dict) or calibration.get("workload_key") != workload_compatibility_key(workload):
        return "NONE", fallback_terminal_commit, fallback_agent_commit, fallback_terminal_ws, fallback_agent_ws
    try:
        terminal_private = _required_int(calibration.get("peak_terminal_private_bytes"))
        terminal_ws = _required_int(calibration.get("peak_terminal_working_set_bytes"))
        aggregate_private = _required_int(calibration.get("peak_tester_private_bytes"))
        single_private = _required_int(calibration.get("peak_single_tester_private_bytes"))
        aggregate_ws = _required_int(calibration.get("peak_tester_working_set_bytes"))
        single_ws = _required_int(calibration.get("peak_single_tester_working_set_bytes"))
        agent_count = _required_int(calibration.get("actual_max_active_agents"))
    except RuntimeError:
        return "NONE", fallback_terminal_commit, fallback_agent_commit, fallback_terminal_ws, fallback_agent_ws
    margin = CALIBRATION_MARGIN_NUMERATOR
    divisor = CALIBRATION_MARGIN_DENOMINATOR
    return (
        "MEASURED",
        max(fallback_terminal_commit, math.ceil(terminal_private * margin / divisor)),
        max(fallback_agent_commit, math.ceil(max(single_private, aggregate_private // agent_count) * margin / divisor)),
        max(fallback_terminal_ws, math.ceil(terminal_ws * margin / divisor)),
        max(fallback_agent_ws, math.ceil(max(single_ws, aggregate_ws // agent_count) * margin / divisor)),
    )


def resolve_resource_preflight(
    settings: dict[str, Any],
    capacity: dict[str, Any],
    *,
    workload: dict[str, Any],
    calibration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    configured = validate_resource_settings(settings)
    if not isinstance(capacity, dict) or not isinstance(workload, dict):
        raise RuntimeError("OPTIMIZER_RESOURCE_CAPACITY_INVALID")
    total = _required_int(capacity.get("total_ram_bytes"))
    available = _required_int(capacity.get("available_ram_bytes"))
    physical = _required_int(capacity.get("physical_cores"))
    logical = _required_int(capacity.get("logical_processors"))
    detected_local_capacity = _required_int(
        capacity.get("configured_local_agent_capacity", 0), allow_zero=True
    )
    local_capacity = detected_local_capacity or logical
    backend_rss = _required_int(capacity.get("max_backend_rss_bytes", 0), allow_zero=True)
    commit_charge = _required_int(capacity.get("commit_charge_bytes"), allow_zero=True)
    commit_limit = _required_int(capacity.get("commit_limit_bytes"))
    if available > total or commit_charge > commit_limit:
        raise RuntimeError("OPTIMIZER_RESOURCE_CAPACITY_INVALID")
    commit_headroom = commit_limit - commit_charge

    calibration_status, terminal_commit, agent_commit, terminal_ws, agent_ws = _calibration_estimates(
        workload, calibration
    )
    estimate_factor = _workload_estimate_factor(workload)
    hard_physical_reserve = max(
        HARD_PHYSICAL_RESERVE_MIN_BYTES,
        math.ceil(total * HARD_PHYSICAL_RESERVE_RATIO),
        backend_rss * 2,
    )
    minimum_free = max(hard_physical_reserve, math.ceil(configured["custom_min_free_ram_gb"] * GIB))
    if configured["mode"] == "AUTO_SAFE":
        cpu_reserve = 0 if logical <= 1 else max(2, math.ceil(logical * 0.25))
        requested_cap = local_capacity
    else:
        cpu_reserve = configured["custom_cpu_reserve_logical"]
        requested_cap = configured["custom_max_local_agents"]

    cpu_slots = max(0, logical - cpu_reserve)
    if physical > 1:
        cpu_slots = min(cpu_slots, physical - 1)
    safe_physical_budget = max(0, available - minimum_free)
    physical_slots = max(0, int((safe_physical_budget - terminal_ws) // agent_ws))
    system_commit_reserve = max(
        SYSTEM_COMMIT_RESERVE_MIN_BYTES,
        math.ceil(commit_limit * SYSTEM_COMMIT_RESERVE_RATIO),
    )
    max_commit_reserve = max(MAX_COMMIT_RESERVE_MIN_BYTES, backend_rss * 2)
    minimum_commit_headroom = system_commit_reserve + max_commit_reserve
    safe_job_commit_budget = max(0, commit_headroom - minimum_commit_headroom)
    commit_slots = max(0, int((safe_job_commit_budget - terminal_commit) // agent_commit))
    safe_agent_cap = min(local_capacity, cpu_slots, physical_slots, commit_slots)
    resolved = min(requested_cap, safe_agent_cap)
    required_ram_for_one = minimum_free + terminal_ws + agent_ws
    required_commit_for_one = minimum_commit_headroom + terminal_commit + agent_commit
    status = "SAFE" if resolved >= 1 and available >= required_ram_for_one and commit_headroom >= required_commit_for_one else "BLOCKED"
    if commit_slots < 1 or commit_headroom < required_commit_for_one:
        reason = "Insufficient current Windows commit headroom for one bounded MT5 agent; reduce agents or increase proven commit capacity."
    elif physical_slots < 1 or available < required_ram_for_one:
        reason = "Insufficient currently available physical RAM for one bounded MT5 agent."
    elif cpu_slots < 1 or local_capacity < 1:
        reason = "Insufficient CPU or configured local-agent capacity for one bounded MT5 agent."
    elif resolved < 1:
        reason = "The Owner agent ceiling resolves to zero under mandatory local resource safety limits."
    else:
        reason = None

    physical_pressure_margin = max(512 * MIB, math.ceil(total * 0.03))
    commit_pressure_margin = max(COMMIT_PRESSURE_MIN_BYTES, math.ceil(commit_limit * COMMIT_PRESSURE_RATIO))
    pressure_free = minimum_free + physical_pressure_margin
    pressure_commit_headroom = minimum_commit_headroom + commit_pressure_margin
    frozen_workload = deepcopy(workload)
    frozen_workload["compatibility_key"] = workload_compatibility_key(workload)
    frozen_workload["estimate_factor"] = estimate_factor
    return {
        "schema": RESOURCE_POLICY_SCHEMA,
        "mode": configured["mode"],
        "status": status,
        "reason": reason,
        "detected": {
            "physical_cores": physical,
            "logical_processors": logical,
            "total_ram_bytes": total,
            "available_ram_bytes": available,
            "commit_charge_bytes": commit_charge,
            "commit_limit_bytes": commit_limit,
            "commit_headroom_bytes": commit_headroom,
            "total_virtual_bytes": int(capacity.get("total_virtual_bytes") or 0),
            "available_virtual_bytes": int(capacity.get("available_virtual_bytes") or 0),
            "windows_version": str(capacity.get("windows_version") or ""),
            "windows_build": str(capacity.get("windows_build") or ""),
            "mt5_build": str(capacity.get("mt5_build") or ""),
            "configured_local_agent_capacity": local_capacity,
            "local_agent_capacity_source": (
                "MT5_AGENT_DIRECTORIES" if detected_local_capacity else "LOGICAL_PROCESSOR_UPPER_BOUND"
            ),
            "max_backend_rss_bytes": backend_rss,
        },
        "hard_physical_reserve_bytes": hard_physical_reserve,
        "protected_system_headroom_bytes": minimum_free,
        "max_headroom_bytes": max(1 * GIB, backend_rss * 2),
        "minimum_free_ram_bytes": minimum_free,
        "pressure_free_ram_bytes": pressure_free,
        "critical_free_ram_bytes": minimum_free,
        "safe_mt5_ram_budget_bytes": safe_physical_budget,
        "terminal_memory_budget_bytes": terminal_ws,
        "per_agent_memory_budget_bytes": agent_ws,
        "terminal_commit_budget_bytes": terminal_commit,
        "per_agent_commit_budget_bytes": agent_commit,
        "protected_system_commit_reserve_bytes": system_commit_reserve,
        "max_commit_reserve_bytes": max_commit_reserve,
        "minimum_commit_headroom_bytes": minimum_commit_headroom,
        "pressure_commit_headroom_bytes": pressure_commit_headroom,
        "critical_commit_headroom_bytes": minimum_commit_headroom,
        "safe_job_commit_budget_bytes": safe_job_commit_budget,
        "required_ram_for_one_agent_bytes": required_ram_for_one,
        "required_commit_for_one_agent_bytes": required_commit_for_one,
        "cpu_reserve_logical": cpu_reserve,
        "physical_ram_safe_agent_slots": physical_slots,
        "ram_safe_agent_slots": physical_slots,
        "commit_safe_agent_slots": commit_slots,
        "cpu_safe_agent_slots": cpu_slots,
        "safe_agent_cap": safe_agent_cap,
        "requested_max_local_agents": requested_cap,
        "resolved_max_local_agents": resolved,
        "max_job_processes": resolved + 1 if resolved > 0 else 0,
        "calibration_status": calibration_status,
        "estimation_source": "MEASURED" if calibration_status == "MEASURED" else "CONSERVATIVE_FALLBACK",
        "enforcement": "WINDOWS_JOB_OBJECT_ACTIVE_PROCESS_LIMIT",
        "workload": frozen_workload,
    }


def build_resource_preflight(
    settings: Any,
    *,
    mt5: dict[str, Any],
    workload: dict[str, Any],
    capacity: dict[str, Any] | None = None,
    calibration: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    validated = validate_resource_settings(settings)
    detected = capacity if capacity is not None else detect_resource_capacity(mt5, force=force)
    enriched_workload = dict(workload)
    enriched_workload.setdefault("mt5_build", str(detected.get("mt5_build") or ""))
    if calibration is None and capacity is None:
        try:
            from .optimizer_store import load_resource_calibration

            calibration = load_resource_calibration(workload_compatibility_key(enriched_workload))
        except Exception:
            # Calibration is advisory. Failure to read it must not weaken fallback limits.
            calibration = None
    return resolve_resource_preflight(
        validated, detected, workload=enriched_workload, calibration=calibration
    )


def resource_pressure_state(
    available_ram_bytes: int,
    *,
    pressure_free_ram_bytes: int,
    critical_free_ram_bytes: int,
) -> str:
    available = int(available_ram_bytes)
    if available <= int(critical_free_ram_bytes):
        return "CRITICAL"
    if available <= int(pressure_free_ram_bytes):
        return "PRESSURE"
    return "SAFE"


def classify_resource_pressure(
    *,
    available_ram_bytes: int,
    commit_headroom_bytes: int,
    policy: dict[str, Any],
) -> str:
    """Classify current pressure using both physical RAM and Windows commit."""
    available = _required_int(available_ram_bytes, allow_zero=True)
    commit_headroom = _required_int(commit_headroom_bytes, allow_zero=True)
    if not isinstance(policy, dict):
        raise RuntimeError("OPTIMIZER_RESOURCE_POLICY_INVALID")
    critical_physical = _required_int(policy.get("critical_free_ram_bytes"), allow_zero=True)
    pressure_physical = _required_int(policy.get("pressure_free_ram_bytes"), allow_zero=True)
    critical_commit = _required_int(policy.get("critical_commit_headroom_bytes"), allow_zero=True)
    pressure_commit = _required_int(policy.get("pressure_commit_headroom_bytes"), allow_zero=True)
    if commit_headroom <= critical_commit:
        return "CRITICAL_COMMIT"
    if available <= critical_physical:
        return "CRITICAL_PHYSICAL"
    if commit_headroom <= pressure_commit:
        return "COMMIT_PRESSURE"
    if available <= pressure_physical:
        return "PHYSICAL_PRESSURE"
    return "SAFE"

def frozen_resource_admission(
    policy: dict[str, Any],
    *,
    mt5: dict[str, Any],
) -> dict[str, Any]:
    if (
        not isinstance(policy, dict)
        or policy.get("schema") != RESOURCE_POLICY_SCHEMA
        or not isinstance(policy.get("workload"), dict)
    ):
        return {"status": "BLOCKED", "reason": "Frozen resource policy is missing or invalid."}
    try:
        capacity = detect_resource_capacity(mt5, force=True)
        cap = _required_int(policy.get("resolved_max_local_agents"), allow_zero=True)
        reserve = _required_int(policy.get("minimum_free_ram_bytes"), allow_zero=True)
        terminal_budget = _required_int(policy.get("terminal_memory_budget_bytes"))
        per_agent = _required_int(policy.get("per_agent_memory_budget_bytes"))
        terminal_commit = _required_int(policy.get("terminal_commit_budget_bytes"))
        agent_commit = _required_int(policy.get("per_agent_commit_budget_bytes"))
        commit_reserve = _required_int(policy.get("minimum_commit_headroom_bytes"))
        cpu_reserve = _required_int(policy.get("cpu_reserve_logical"), allow_zero=True)
        current_available = _required_int(capacity.get("available_ram_bytes"), allow_zero=True)
        current_commit_charge = _required_int(capacity.get("commit_charge_bytes"), allow_zero=True)
        current_commit_limit = _required_int(capacity.get("commit_limit_bytes"))
        current_commit_headroom = current_commit_limit - current_commit_charge
        logical = _required_int(capacity.get("logical_processors"))
        physical = _required_int(capacity.get("physical_cores"))
        detected_capacity = _required_int(
            capacity.get("configured_local_agent_capacity", 0), allow_zero=True
        )
        current_capacity = detected_capacity or logical
        if current_commit_charge > current_commit_limit:
            raise RuntimeError("OPTIMIZER_RESOURCE_CAPACITY_INVALID")
        workload = policy["workload"]
        expected_key = workload_compatibility_key(workload)
        if workload.get("compatibility_key") != expected_key:
            raise RuntimeError("OPTIMIZER_RESOURCE_POLICY_INVALID")
        cpu_slots = max(0, logical - cpu_reserve)
        if physical > 1:
            cpu_slots = min(cpu_slots, physical - 1)
        required_available = reserve + terminal_budget + cap * per_agent
        required_commit = commit_reserve + terminal_commit + cap * agent_commit
        safe = (
            cap >= 1
            and current_available >= required_available
            and current_commit_headroom >= required_commit
            and current_capacity >= cap
            and cpu_slots >= cap
        )
        return {
            "status": "SAFE" if safe else "BLOCKED",
            "reason": None if safe else "Current physical RAM, Windows commit, CPU, or MT5 capacity cannot honor the frozen local-agent cap.",
            "available_ram_bytes": current_available,
            "required_available_ram_bytes": required_available,
            "commit_charge_bytes": current_commit_charge,
            "commit_limit_bytes": current_commit_limit,
            "commit_headroom_bytes": current_commit_headroom,
            "required_commit_headroom_bytes": required_commit,
            "configured_local_agent_capacity": current_capacity,
            "resolved_max_local_agents": cap,
        }
    except Exception:
        return {
            "status": "BLOCKED",
            "reason": "Current resource telemetry or frozen policy is invalid; execution is blocked.",
        }
