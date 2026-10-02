from __future__ import annotations

import ctypes
import json
import math
import os
import subprocess
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

GIB = 1024 ** 3
MIB = 1024 ** 2
RESOURCE_POLICY_SCHEMA = "MAX_OPTIMIZER_RESOURCE_POLICY_V1"
RESOURCE_MODES = {"AUTO_SAFE", "CUSTOM"}
RESOURCE_CACHE_SECONDS = 5.0

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

def resolve_resource_preflight(
    settings: dict[str, Any],
    capacity: dict[str, Any],
    *,
    workload: dict[str, Any],
) -> dict[str, Any]:
    configured = validate_resource_settings(settings)
    total = int(capacity.get("total_ram_bytes") or 0)
    available = int(capacity.get("available_ram_bytes") or 0)
    physical = int(capacity.get("physical_cores") or 0)
    logical = int(capacity.get("logical_processors") or 0)
    local_capacity = int(capacity.get("configured_local_agent_capacity") or logical or 0)
    backend_rss = int(capacity.get("max_backend_rss_bytes") or 0)
    if min(total, available, physical, logical, local_capacity) <= 0:
        raise RuntimeError("OPTIMIZER_RESOURCE_CAPACITY_INVALID")

    per_agent_budget = int(max(1 * GIB, min(4 * GIB, total / max(8, logical))))
    terminal_budget = int(max(512 * MIB, total * 0.02))

    if configured["mode"] == "AUTO_SAFE":
        protected_system = int(max(4 * GIB, total * 0.20))
        max_headroom = int(max(1 * GIB, total * 0.05, backend_rss * 2))
        minimum_free = protected_system + max_headroom
        cpu_reserve = 0 if logical <= 1 else max(2, math.ceil(logical * 0.25))
        requested_cap = local_capacity
    else:
        minimum_free = int(configured["custom_min_free_ram_gb"] * GIB)
        protected_system = minimum_free
        max_headroom = 0
        cpu_reserve = configured["custom_cpu_reserve_logical"]
        requested_cap = configured["custom_max_local_agents"]

    cpu_slots = max(0, logical - cpu_reserve)
    if physical > 1:
        cpu_slots = min(cpu_slots, physical - 1)
    safe_mt5_ram_budget = max(0, available - minimum_free)
    ram_slots = max(0, int((safe_mt5_ram_budget - terminal_budget) // per_agent_budget))
    resolved = min(requested_cap, local_capacity, cpu_slots, ram_slots)
    required_for_one = minimum_free + terminal_budget + per_agent_budget
    status = "SAFE" if resolved >= 1 and available >= required_for_one else "BLOCKED"
    if resolved < 1:
        reason = "Insufficient currently available RAM/CPU capacity for one bounded local MT5 agent."
    elif available < required_for_one:
        reason = "Insufficient currently available RAM for safe MT5 optimization."
    else:
        reason = None

    critical_free = minimum_free
    pressure_margin = int(max(512 * MIB, total * 0.03))
    pressure_free = critical_free + pressure_margin
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
            "total_virtual_bytes": int(capacity.get("total_virtual_bytes") or 0),
            "available_virtual_bytes": int(capacity.get("available_virtual_bytes") or 0),
            "windows_version": str(capacity.get("windows_version") or ""),
            "windows_build": str(capacity.get("windows_build") or ""),
            "mt5_build": str(capacity.get("mt5_build") or ""),
            "configured_local_agent_capacity": local_capacity,
            "max_backend_rss_bytes": backend_rss,
        },
        "protected_system_headroom_bytes": protected_system,
        "max_headroom_bytes": max_headroom,
        "minimum_free_ram_bytes": minimum_free,
        "pressure_free_ram_bytes": pressure_free,
        "critical_free_ram_bytes": critical_free,
        "safe_mt5_ram_budget_bytes": safe_mt5_ram_budget,
        "terminal_memory_budget_bytes": terminal_budget,
        "per_agent_memory_budget_bytes": per_agent_budget,
        "cpu_reserve_logical": cpu_reserve,
        "ram_safe_agent_slots": ram_slots,
        "cpu_safe_agent_slots": cpu_slots,
        "requested_max_local_agents": requested_cap,
        "resolved_max_local_agents": resolved,
        "max_job_processes": resolved + 1 if resolved > 0 else 0,
        "enforcement": "WINDOWS_JOB_OBJECT_ACTIVE_PROCESS_LIMIT",
        "workload": deepcopy(workload),
    }


def build_resource_preflight(
    settings: Any,
    *,
    mt5: dict[str, Any],
    workload: dict[str, Any],
    capacity: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    validated = validate_resource_settings(settings)
    detected = capacity if capacity is not None else detect_resource_capacity(mt5, force=force)
    return resolve_resource_preflight(validated, detected, workload=workload)


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

def frozen_resource_admission(
    policy: dict[str, Any],
    *,
    mt5: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(policy, dict) or policy.get("schema") != RESOURCE_POLICY_SCHEMA:
        return {"status": "BLOCKED", "reason": "Frozen resource policy is missing or invalid."}
    capacity = detect_resource_capacity(mt5, force=True)
    cap = int(policy.get("resolved_max_local_agents") or 0)
    reserve = int(policy.get("minimum_free_ram_bytes") or 0)
    terminal_budget = int(policy.get("terminal_memory_budget_bytes") or 0)
    per_agent = int(policy.get("per_agent_memory_budget_bytes") or 0)
    cpu_reserve = int(policy.get("cpu_reserve_logical") or 0)
    required_available = reserve + terminal_budget + cap * per_agent
    cpu_slots = max(0, int(capacity.get("logical_processors") or 0) - cpu_reserve)
    physical = int(capacity.get("physical_cores") or 0)
    if physical > 1:
        cpu_slots = min(cpu_slots, physical - 1)
    safe = (
        cap >= 1
        and int(capacity.get("available_ram_bytes") or 0) >= required_available
        and int(capacity.get("configured_local_agent_capacity") or 0) >= cap
        and cpu_slots >= cap
    )
    return {
        "status": "SAFE" if safe else "BLOCKED",
        "reason": None if safe else "Current resources cannot safely honor the frozen local-agent cap.",
        "available_ram_bytes": int(capacity.get("available_ram_bytes") or 0),
        "required_available_ram_bytes": required_available,
        "resolved_max_local_agents": cap,
    }
