from __future__ import annotations

import ctypes
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .optimizer_resources import _windows_system_resource_snapshot, classify_resource_pressure

MONITOR_INTERVAL_SECONDS = 2.0
PERSIST_INTERVAL_SECONDS = 10.0


class ResourceGuardTriggered(RuntimeError):
    def __init__(self, reason: str, summary: dict[str, Any]) -> None:
        super().__init__(f"OPTIMIZER_RESOURCE_GUARD:{reason}")
        self.reason = reason
        self.summary = summary


def _system_resource_snapshot() -> dict[str, int]:
    return _windows_system_resource_snapshot()


def _job_process_ids(job_handle: Any, max_processes: int) -> list[int]:
    """Return only PIDs assigned to the owned Job Object; never infer by parent PID."""
    if os.name != "nt":
        raise RuntimeError("OPTIMIZER_RESOURCE_MONITOR_REQUIRES_WINDOWS")
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    capacity = max(1, int(max_processes))
    buffer = ctypes.create_string_buffer(8 + ctypes.sizeof(ctypes.c_size_t) * capacity)
    returned = wintypes.DWORD()
    if not kernel32.QueryInformationJobObject(
        job_handle, 3, buffer, ctypes.sizeof(buffer), ctypes.byref(returned)
    ):
        raise RuntimeError("OPTIMIZER_RESOURCE_JOB_QUERY_FAILED")
    assigned = int.from_bytes(buffer.raw[0:4], "little")
    count = int.from_bytes(buffer.raw[4:8], "little")
    if count > capacity or count != assigned:
        raise RuntimeError("OPTIMIZER_RESOURCE_JOB_QUERY_INCOMPLETE")
    width = ctypes.sizeof(ctypes.c_size_t)
    return [
        int.from_bytes(buffer.raw[8 + index * width:8 + (index + 1) * width], "little")
        for index in range(count)
    ]


def _process_row(pid: int) -> dict[str, Any]:
    from ctypes import wintypes

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    PROCESS_QUERY_INFORMATION = 0x0400
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, int(pid))
    if not handle:
        raise RuntimeError("OPTIMIZER_RESOURCE_PROCESS_OPEN_FAILED")
    try:
        image = ctypes.create_unicode_buffer(32768)
        image_size = wintypes.DWORD(len(image))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(image_size)):
            raise RuntimeError("OPTIMIZER_RESOURCE_PROCESS_IDENTITY_FAILED")
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise RuntimeError("OPTIMIZER_RESOURCE_PROCESS_MEMORY_FAILED")
        return {
            "pid": int(pid),
            "executable_path": image.value,
            "working_set_bytes": int(counters.WorkingSetSize),
            "private_bytes": int(counters.PrivateUsage),
            "pagefile_bytes": int(counters.PagefileUsage),
        }
    finally:
        kernel32.CloseHandle(handle)


def _job_process_rows(job_handle: Any, terminal: Path, max_processes: int) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("OPTIMIZER_RESOURCE_MONITOR_REQUIRES_WINDOWS")
    expected_terminal = os.path.normcase(os.path.realpath(terminal))
    expected_tester = os.path.normcase(os.path.realpath(terminal.with_name("metatester64.exe")))
    terminal_row: dict[str, Any] | None = None
    agents: list[dict[str, Any]] = []
    for pid in _job_process_ids(job_handle, max_processes):
        row = _process_row(pid)
        executable = os.path.normcase(os.path.realpath(str(row["executable_path"])))
        row["executable_path"] = executable
        if executable == expected_terminal:
            if terminal_row is not None:
                raise RuntimeError("OPTIMIZER_RESOURCE_JOB_TERMINAL_IDENTITY_AMBIGUOUS")
            terminal_row = row
        elif executable == expected_tester:
            agents.append(row)
        else:
            raise RuntimeError("OPTIMIZER_RESOURCE_JOB_CONTAINS_UNKNOWN_PROCESS")
    if terminal_row is None:
        raise RuntimeError("OPTIMIZER_RESOURCE_JOB_TERMINAL_MISSING")
    agents.sort(key=lambda row: int(row["pid"]))
    return {"terminal": terminal_row, "agents": agents}


def _terminate_and_verify_job(job_handle: Any, process_handle: Any, *, timeout_ms: int = 10000) -> bool:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    if not kernel32.TerminateJobObject(job_handle, 1):
        return False
    if kernel32.WaitForSingleObject(process_handle, int(timeout_ms)) != 0:
        return False
    deadline = time.monotonic() + max(0, int(timeout_ms)) / 1000
    while True:
        try:
            if not _job_process_ids(job_handle, 257):
                return True
        except Exception:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)

def _launch_in_job(
    terminal: Path,
    ini_path: Path,
    max_processes: int,
) -> tuple[Any, Any, int, str]:
    from ctypes import wintypes
    if os.name != "nt":
        raise RuntimeError("OPTIMIZER_RESOURCE_JOB_OBJECT_REQUIRES_WINDOWS")
    CREATE_SUSPENDED = 0x00000004
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    class STARTUPINFO(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]
    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
        ]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD

    job_handle = kernel32.CreateJobObjectW(None, None)
    if not job_handle:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
    limits = EXTENDED_LIMIT()
    limits.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    limits.BasicLimitInformation.ActiveProcessLimit = int(max_processes)
    if not kernel32.SetInformationJobObject(
        job_handle,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        kernel32.CloseHandle(job_handle)
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
    startup = STARTUPINFO()
    startup.cb = ctypes.sizeof(startup)
    process_info = PROCESS_INFORMATION()
    command_line = subprocess.list2cmdline([str(terminal), f"/config:{ini_path}"])
    command_buffer = ctypes.create_unicode_buffer(command_line)
    created = kernel32.CreateProcessW(
        str(terminal), command_buffer, None, None, False, CREATE_SUSPENDED,
        None, str(terminal.parent), ctypes.byref(startup), ctypes.byref(process_info),
    )
    if not created:
        kernel32.CloseHandle(job_handle)
        raise OSError(ctypes.get_last_error(), "CreateProcessW")
    assigned_to_job = False
    try:
        if not kernel32.AssignProcessToJobObject(job_handle, process_info.hProcess):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
        assigned_to_job = True
        if kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
            raise OSError(ctypes.get_last_error(), "ResumeThread")
    except Exception as launch_error:
        terminated = bool(
            assigned_to_job and kernel32.TerminateJobObject(job_handle, 1)
        )
        if not terminated:
            terminated = bool(kernel32.TerminateProcess(process_info.hProcess, 1))
        stopped = kernel32.WaitForSingleObject(process_info.hProcess, 10000) == 0
        kernel32.CloseHandle(process_info.hThread)
        kernel32.CloseHandle(process_info.hProcess)
        kernel32.CloseHandle(job_handle)
        if not stopped:
            raise RuntimeError("OPTIMIZER_RESOURCE_LAUNCH_STOP_UNVERIFIED") from launch_error
        raise
    kernel32.CloseHandle(process_info.hThread)
    return job_handle, process_info.hProcess, int(process_info.dwProcessId), command_line

def launch_bounded_mt5(
    terminal: Path,
    ini_path: Path,
    *,
    timeout_sec: int,
    policy: dict[str, Any],
    on_process: Callable[[dict[str, Any]], None] | None = None,
    on_resource: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    from ctypes import wintypes
    cap = int(policy.get("resolved_max_local_agents") or 0)
    max_processes = int(policy.get("max_job_processes") or 0)
    if cap < 1 or max_processes != cap + 1:
        raise RuntimeError("OPTIMIZER_RESOURCE_POLICY_INVALID")
    job_handle, process_handle, pid, command_line = _launch_in_job(
        terminal, ini_path, max_processes
    )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258
    transitions: list[str] = ["SAFE"]
    detected = policy.get("detected") or {}
    summary: dict[str, Any] = {
        "resource_state": "SAFE",
        "resolved_max_local_agents": cap,
        "actual_max_active_agents": 0,
        "workload_compatibility_key": str((policy.get("workload") or {}).get("compatibility_key") or ""),
        "min_available_ram_bytes": int(detected.get("available_ram_bytes") or 0),
        "min_commit_headroom_bytes": int(detected.get("commit_headroom_bytes") or 0),
        "peak_commit_charge_bytes": int(detected.get("commit_charge_bytes") or 0),
        "commit_limit_bytes": int(detected.get("commit_limit_bytes") or 0),
        "peak_terminal_private_bytes": 0,
        "peak_terminal_working_set_bytes": 0,
        "peak_tester_private_bytes": 0,
        "peak_single_tester_private_bytes": 0,
        "peak_tester_working_set_bytes": 0,
        "peak_single_tester_working_set_bytes": 0,
        "peak_agent_working_set_bytes": 0,
        "peak_mt5_working_set_bytes": 0,
        "sample_count": 0,
        "telemetry_failure_count": 0,
        "consecutive_telemetry_failures": 0,
        "transitions": transitions,
        "stop_reason": None,
    }
    started = time.monotonic()
    next_persist = started
    pressure_hits = 0
    returncode: int | None = None

    def transition(state: str) -> bool:
        if state != summary["resource_state"]:
            summary["resource_state"] = state
            if len(transitions) >= 16:
                del transitions[1]
            transitions.append(state)
            return True
        return False

    def emit_resource() -> bool:
        if on_resource is None:
            return True
        try:
            on_resource(dict(summary))
            return True
        except Exception:
            summary["persistence_failure_count"] = int(summary.get("persistence_failure_count") or 0) + 1
            return False

    def stop_owned_job(reason: str) -> None:
        transition("RESOURCE_STOP_REQUESTED")
        summary["stop_reason"] = reason
        emit_resource()
        verified = _terminate_and_verify_job(job_handle, process_handle)
        transition("RESOURCE_STOPPED" if verified else "RESOURCE_RECONCILIATION_REQUIRED")
        if not verified:
            summary["stop_reason"] = "RESOURCE_RECONCILIATION_REQUIRED"
        emit_resource()
        raise ResourceGuardTriggered(
            reason if verified else "RESOURCE_RECONCILIATION_REQUIRED", dict(summary)
        )

    try:
        if on_process is not None:
            try:
                on_process({
                    "pid": pid,
                    "executable_path": str(terminal.resolve()),
                    "command_line": command_line,
                    "ini_path": str(ini_path.resolve()),
                    "started_utc_epoch": time.time(),
                })
            except Exception:
                stop_owned_job("RESOURCE_STATE_PERSISTENCE_FAILED")
        while True:
            wait = kernel32.WaitForSingleObject(process_handle, 0)
            if wait == WAIT_OBJECT_0:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(process_handle, ctypes.byref(exit_code)):
                    raise OSError(ctypes.get_last_error(), "GetExitCodeProcess")
                returncode = int(exit_code.value)
                break
            if wait != WAIT_TIMEOUT:
                stop_owned_job("PROCESS_WAIT_TELEMETRY_UNAVAILABLE")
            if time.monotonic() - started >= int(timeout_sec):
                stop_owned_job("MT5_EXECUTION_TIMEOUT")

            try:
                system = _system_resource_snapshot()
                rows = _job_process_rows(job_handle, terminal, max_processes)
                available = int(system["available_ram_bytes"])
                commit_charge = int(system["commit_charge_bytes"])
                commit_limit = int(system["commit_limit_bytes"])
                commit_headroom = int(system["commit_headroom_bytes"])
                if min(available, commit_charge, commit_limit, commit_headroom) < 0 or commit_limit <= 0 or commit_charge > commit_limit or commit_headroom != commit_limit - commit_charge:
                    raise RuntimeError("OPTIMIZER_RESOURCE_SYSTEM_TELEMETRY_INVALID")
                terminal_row = rows["terminal"]
                if not isinstance(terminal_row, dict):
                    raise RuntimeError("OPTIMIZER_RESOURCE_JOB_TERMINAL_MISSING")
                agents = rows["agents"]
                terminal_ws = int(terminal_row["working_set_bytes"])
                terminal_private = int(terminal_row["private_bytes"])
                agent_ws_values = [int(row["working_set_bytes"]) for row in agents]
                agent_private_values = [int(row["private_bytes"]) for row in agents]
                if min([terminal_ws, terminal_private, *agent_ws_values, *agent_private_values], default=0) < 0:
                    raise RuntimeError("OPTIMIZER_RESOURCE_PROCESS_MEMORY_INVALID")
            except Exception:
                summary["telemetry_failure_count"] = int(summary["telemetry_failure_count"]) + 1
                summary["consecutive_telemetry_failures"] = int(summary["consecutive_telemetry_failures"]) + 1
                transition("TELEMETRY_UNAVAILABLE")
                if not emit_resource():
                    stop_owned_job("RESOURCE_STATE_PERSISTENCE_FAILED")
                if summary["consecutive_telemetry_failures"] >= 2:
                    stop_owned_job("RESOURCE_TELEMETRY_UNAVAILABLE")
                time.sleep(MONITOR_INTERVAL_SECONDS)
                continue

            summary["consecutive_telemetry_failures"] = 0
            actual_agents = len(agents)
            agent_ws = sum(agent_ws_values)
            agent_private = sum(agent_private_values)
            single_agent_ws = max(agent_ws_values, default=0)
            single_agent_private = max(agent_private_values, default=0)
            summary["sample_count"] = int(summary["sample_count"]) + 1
            summary["actual_max_active_agents"] = max(int(summary["actual_max_active_agents"]), actual_agents)
            summary["min_available_ram_bytes"] = min(int(summary["min_available_ram_bytes"]), available)
            summary["min_commit_headroom_bytes"] = min(int(summary["min_commit_headroom_bytes"]), commit_headroom)
            summary["peak_commit_charge_bytes"] = max(int(summary["peak_commit_charge_bytes"]), commit_charge)
            summary["commit_limit_bytes"] = min(int(summary["commit_limit_bytes"]), commit_limit)
            summary["peak_terminal_private_bytes"] = max(int(summary["peak_terminal_private_bytes"]), terminal_private)
            summary["peak_terminal_working_set_bytes"] = max(int(summary["peak_terminal_working_set_bytes"]), terminal_ws)
            summary["peak_tester_private_bytes"] = max(int(summary["peak_tester_private_bytes"]), agent_private)
            summary["peak_single_tester_private_bytes"] = max(int(summary["peak_single_tester_private_bytes"]), single_agent_private)
            summary["peak_tester_working_set_bytes"] = max(int(summary["peak_tester_working_set_bytes"]), agent_ws)
            summary["peak_single_tester_working_set_bytes"] = max(int(summary["peak_single_tester_working_set_bytes"]), single_agent_ws)
            summary["peak_agent_working_set_bytes"] = max(int(summary["peak_agent_working_set_bytes"]), agent_ws)
            summary["peak_mt5_working_set_bytes"] = max(int(summary["peak_mt5_working_set_bytes"]), terminal_ws + agent_ws)

            state = classify_resource_pressure(
                available_ram_bytes=available,
                commit_headroom_bytes=commit_headroom,
                policy=policy,
            )
            guard_reason: str | None = None
            if actual_agents > cap:
                guard_reason = "LOCAL_AGENT_CAP_BREACH"
            elif terminal_private > int(policy["terminal_commit_budget_bytes"]):
                guard_reason = "TERMINAL_PRIVATE_BUDGET_EXCEEDED"
            elif single_agent_private > int(policy["per_agent_commit_budget_bytes"]):
                guard_reason = "AGENT_PRIVATE_BUDGET_EXCEEDED"
            elif terminal_private + agent_private > int(policy["safe_job_commit_budget_bytes"]):
                guard_reason = "JOB_PRIVATE_BUDGET_EXCEEDED"
            if state in {"PHYSICAL_PRESSURE", "COMMIT_PRESSURE"}:
                pressure_hits += 1
            else:
                pressure_hits = 0
            state_changed = transition(state)
            if state_changed and not emit_resource():
                stop_owned_job("RESOURCE_STATE_PERSISTENCE_FAILED")
            now = time.monotonic()
            if on_resource is not None and now >= next_persist:
                if not emit_resource():
                    stop_owned_job("RESOURCE_STATE_PERSISTENCE_FAILED")
                next_persist = now + PERSIST_INTERVAL_SECONDS
            if state in {"CRITICAL_PHYSICAL", "CRITICAL_COMMIT"}:
                stop_owned_job(guard_reason or state)
            if guard_reason is not None:
                stop_owned_job(guard_reason)
            if pressure_hits >= 2:
                stop_owned_job("SUSTAINED_RESOURCE_PRESSURE")
            time.sleep(MONITOR_INTERVAL_SECONDS)
        transition("SAFE")
        summary["returncode"] = int(returncode or 0)
        summary["duration_seconds"] = round(time.monotonic() - started, 3)
        if not emit_resource():
            summary["resource_state"] = "RESOURCE_RECONCILIATION_REQUIRED"
            summary["stop_reason"] = "RESOURCE_STATE_PERSISTENCE_FAILED"
            raise ResourceGuardTriggered("RESOURCE_RECONCILIATION_REQUIRED", dict(summary))
        if returncode not in (0, None):
            raise RuntimeError(f"MT5_EXECUTION_FAILURE: exit={returncode}")
        return int(returncode or 0)
    finally:
        kernel32.CloseHandle(process_handle)
        kernel32.CloseHandle(job_handle)
