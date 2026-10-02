from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .optimizer_resources import resource_pressure_state

MONITOR_INTERVAL_SECONDS = 2.0
PERSIST_INTERVAL_SECONDS = 10.0


class ResourceGuardTriggered(RuntimeError):
    def __init__(self, reason: str, summary: dict[str, Any]) -> None:
        super().__init__(f"OPTIMIZER_RESOURCE_GUARD:{reason}")
        self.reason = reason
        self.summary = summary


def _system_available_ram_bytes() -> int:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise RuntimeError("OPTIMIZER_RESOURCE_MEMORY_QUERY_FAILED")
    return int(status.ullAvailPhys)


def _process_rows(terminal_pid: int, terminal: Path) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("OPTIMIZER_RESOURCE_MONITOR_REQUIRES_WINDOWS")
    expected_terminal = str(terminal.resolve()).replace("'", "''")
    expected_tester = str(terminal.with_name("metatester64.exe").resolve()).replace("'", "''")
    command = (
        f"$tp={int(terminal_pid)};"
        f"$te='{expected_terminal}';$me='{expected_tester}';"
        '$t=Get-CimInstance Win32_Process -Filter "ProcessId=$tp" -ErrorAction SilentlyContinue | '
        "Where-Object {$_.ExecutablePath -ieq $te};"
        "$a=Get-CimInstance Win32_Process -Filter \"Name='metatester64.exe'\" -ErrorAction SilentlyContinue | "
        "Where-Object {$_.ParentProcessId -eq $tp -and $_.ExecutablePath -ieq $me};"
        "$rows=@($t)+@($a) | Where-Object {$_} | "
        "Select-Object Name,ProcessId,ParentProcessId,ExecutablePath,WorkingSetSize,CreationDate;"
        "if($rows){$rows|ConvertTo-Json -Compress}"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=8,
    )
    if result.returncode != 0:
        raise RuntimeError("OPTIMIZER_RESOURCE_PROCESS_QUERY_FAILED")
    raw = (result.stdout or "").strip()
    if not raw:
        return {"terminal": None, "agents": []}
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OPTIMIZER_RESOURCE_PROCESS_QUERY_INVALID") from exc
    if isinstance(rows, dict):
        rows = [rows]
    terminal_row = None
    agents: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if int(row.get("ProcessId") or 0) == int(terminal_pid):
            terminal_row = row
        elif int(row.get("ParentProcessId") or 0) == int(terminal_pid):
            agents.append(row)
    return {"terminal": terminal_row, "agents": agents}

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
    try:
        if not kernel32.AssignProcessToJobObject(job_handle, process_info.hProcess):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
        if kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
            raise OSError(ctypes.get_last_error(), "ResumeThread")
    except Exception:
        kernel32.TerminateJobObject(job_handle, 1)
        kernel32.CloseHandle(process_info.hThread)
        kernel32.CloseHandle(process_info.hProcess)
        kernel32.CloseHandle(job_handle)
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
    transitions: list[str] = ["SAFE"]
    summary: dict[str, Any] = {
        "resource_state": "SAFE",
        "resolved_max_local_agents": cap,
        "actual_max_active_agents": 0,
        "min_available_ram_bytes": int(policy["detected"]["available_ram_bytes"]),
        "peak_terminal_working_set_bytes": 0,
        "peak_agent_working_set_bytes": 0,
        "peak_mt5_working_set_bytes": 0,
        "transitions": transitions,
        "stop_reason": None,
    }
    if on_process is not None:
        on_process({
            "pid": pid,
            "executable_path": str(terminal.resolve()),
            "command_line": command_line,
            "ini_path": str(ini_path.resolve()),
            "started_utc_epoch": time.time(),
        })
    started = time.monotonic()
    next_persist = started
    pressure_hits = 0
    guard_reason: str | None = None
    returncode: int | None = None
    try:
        while True:
            wait = kernel32.WaitForSingleObject(process_handle, 0)
            if wait == WAIT_OBJECT_0:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(process_handle, ctypes.byref(exit_code)):
                    raise OSError(ctypes.get_last_error(), "GetExitCodeProcess")
                returncode = int(exit_code.value)
                break
            if time.monotonic() - started >= int(timeout_sec):
                kernel32.TerminateJobObject(job_handle, 1)
                raise RuntimeError("MT5_EXECUTION_TIMEOUT_UNCERTAIN")

            available = _system_available_ram_bytes()
            rows = _process_rows(pid, terminal)
            agents = rows["agents"]
            terminal_ws = int((rows["terminal"] or {}).get("WorkingSetSize") or 0)
            agent_ws = sum(int(row.get("WorkingSetSize") or 0) for row in agents)
            actual_agents = len(agents)
            summary["actual_max_active_agents"] = max(
                int(summary["actual_max_active_agents"]), actual_agents
            )
            summary["min_available_ram_bytes"] = min(
                int(summary["min_available_ram_bytes"]), available
            )
            summary["peak_terminal_working_set_bytes"] = max(
                int(summary["peak_terminal_working_set_bytes"]), terminal_ws
            )
            summary["peak_agent_working_set_bytes"] = max(
                int(summary["peak_agent_working_set_bytes"]), agent_ws
            )
            summary["peak_mt5_working_set_bytes"] = max(
                int(summary["peak_mt5_working_set_bytes"]), terminal_ws + agent_ws
            )
            state = resource_pressure_state(
                available,
                pressure_free_ram_bytes=int(policy["pressure_free_ram_bytes"]),
                critical_free_ram_bytes=int(policy["critical_free_ram_bytes"]),
            )
            if actual_agents > cap:
                guard_reason = "LOCAL_AGENT_CAP_BREACH"
                state = "CRITICAL"
            if state == "PRESSURE":
                pressure_hits += 1
            else:
                pressure_hits = 0
            if state != summary["resource_state"]:
                summary["resource_state"] = state
                transitions.append(state)
                if on_resource is not None:
                    on_resource(dict(summary))
            now = time.monotonic()
            if on_resource is not None and now >= next_persist:
                on_resource(dict(summary))
                next_persist = now + PERSIST_INTERVAL_SECONDS
            if state == "CRITICAL" or pressure_hits >= 2:
                guard_reason = guard_reason or (
                    "CRITICAL_FREE_RAM" if state == "CRITICAL" else "SUSTAINED_RAM_PRESSURE"
                )
                summary["resource_state"] = "RESOURCE_STOP_REQUESTED"
                transitions.append("RESOURCE_STOP_REQUESTED")
                summary["stop_reason"] = guard_reason
                if on_resource is not None:
                    on_resource(dict(summary))
                kernel32.TerminateJobObject(job_handle, 1)
                kernel32.WaitForSingleObject(process_handle, 10000)
                summary["resource_state"] = "RESOURCE_STOPPED"
                transitions.append("RESOURCE_STOPPED")
                if on_resource is not None:
                    on_resource(dict(summary))
                raise ResourceGuardTriggered(guard_reason, dict(summary))
            time.sleep(MONITOR_INTERVAL_SECONDS)
        summary["resource_state"] = "SAFE"
        summary["returncode"] = int(returncode or 0)
        summary["duration_seconds"] = round(time.monotonic() - started, 3)
        if on_resource is not None:
            on_resource(dict(summary))
        if returncode not in (0, None):
            raise RuntimeError(f"MT5_EXECUTION_FAILURE: exit={returncode}")
        return int(returncode or 0)
    finally:
        kernel32.CloseHandle(process_handle)
        kernel32.CloseHandle(job_handle)
