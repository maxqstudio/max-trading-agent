from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ROOT
from .research_contract import HARDWARE_SCHEMA, capacity_authority_contract, stable_hash


def _run(args: list[str], timeout: int = 8) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return int(proc.returncode), str(proc.stdout or "").strip()
    except Exception as exc:
        return 999, f"{type(exc).__name__}:{exc}"


def _cpu_name() -> str:
    value = str(platform.processor() or "").strip()
    if value:
        return value
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                value = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
                if value:
                    return value
        except Exception:
            pass
    return str(platform.machine() or "UNKNOWN")


def _physical_cores() -> dict[str, Any]:
    logical = max(1, int(os.cpu_count() or 1))
    if os.name == "nt":
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if exe:
            rc, out = _run(
                [
                    exe,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "($n=(Get-CimInstance Win32_Processor | Measure-Object "
                    "-Property NumberOfCores -Sum).Sum); "
                    "if($null -ne $n){[Console]::Write([int]$n)}",
                ],
                timeout=8,
            )
            if rc == 0:
                try:
                    physical = int(out.strip())
                    if physical > 0:
                        return {
                            "physical_cores": physical,
                            "logical_threads": logical,
                            "planning_cores": physical,
                            "source": "WINDOWS_CIM",
                        }
                except Exception:
                    pass
    planning = max(1, logical // 2 if logical >= 4 else logical)
    return {
        "physical_cores": None,
        "logical_threads": logical,
        "planning_cores": planning,
        "source": "CONSERVATIVE_LOGICAL_ESTIMATE",
    }


def _memory() -> dict[str, Any]:
    if os.name == "nt":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
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

            state = MEMORYSTATUSEX()
            state.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
                return {
                    "total_bytes": int(state.ullTotalPhys),
                    "available_bytes": int(state.ullAvailPhys),
                    "source": "WINDOWS_GLOBALMEMORYSTATUSEX",
                }
        except Exception:
            pass
    try:
        page = int(os.sysconf("SC_PAGE_SIZE"))
        total_pages = int(os.sysconf("SC_PHYS_PAGES"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        total = page * total_pages
        available = page * available_pages
        if total > 0:
            return {
                "total_bytes": total,
                "available_bytes": available,
                "source": "POSIX_SYSCONF",
            }
    except Exception:
        pass
    return {
        "total_bytes": None,
        "available_bytes": None,
        "source": "UNAVAILABLE",
    }


def _nvidia() -> list[dict[str, Any]]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    rc, out = _run(
        [
            exe,
            "--query-gpu=name,memory.total,memory.free,driver_version",
            "--format=csv,noheader,nounits",
        ],
        timeout=8,
    )
    if rc != 0:
        return []
    devices: list[dict[str, Any]] = []
    for index, line in enumerate(out.splitlines()):
        parts = [part.strip() for part in line.split(",")]
        if not parts or not parts[0]:
            continue
        try:
            total_mb = int(float(parts[1]))
        except Exception:
            total_mb = None
        try:
            free_mb = int(float(parts[2]))
        except Exception:
            free_mb = None
        devices.append(
            {
                "index": index,
                "name": parts[0],
                "memory_total_mb": total_mb,
                "memory_free_mb": free_mb,
                "driver_version": parts[3] if len(parts) > 3 else None,
            }
        )
    return devices


def collect_hardware_snapshot(root: Path = ROOT) -> dict[str, Any]:
    topology = _physical_cores()
    memory = _memory()
    gpus = _nvidia()
    try:
        disk = shutil.disk_usage(root)
        disk_payload = {
            "total_bytes": int(disk.total),
            "free_bytes": int(disk.free),
            "source": "SHUTIL_DISK_USAGE",
        }
    except Exception:
        disk_payload = {
            "total_bytes": None,
            "free_bytes": None,
            "source": "UNAVAILABLE",
        }

    stable_identity = {
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "cpu": {
            "name": _cpu_name(),
            "physical_cores": topology["physical_cores"],
            "logical_threads": topology["logical_threads"],
            "planning_cores": topology["planning_cores"],
            "core_count_source": topology["source"],
        },
        "memory_total_bytes": memory["total_bytes"],
        "disk_total_bytes": disk_payload["total_bytes"],
        "gpus": [
            {
                "index": row["index"],
                "name": row["name"],
                "memory_total_mb": row["memory_total_mb"],
                "driver_version": row["driver_version"],
            }
            for row in gpus
        ],
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "architecture": platform.architecture()[0],
        },
    }
    profile_hash = stable_hash(stable_identity)
    return {
        "schema": HARDWARE_SCHEMA,
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "profile_hash": profile_hash,
        "stable_identity": stable_identity,
        "dynamic_resources": {
            "memory_available_bytes": memory["available_bytes"],
            "gpu_memory_free_mb": [
                {
                    "index": row["index"],
                    "memory_free_mb": row["memory_free_mb"],
                }
                for row in gpus
            ],
            "disk_free_bytes": disk_payload["free_bytes"],
        },
        "capacity_authority": capacity_authority_contract(),
        "training_started": False,
        "model_capacity_decision_performed": False,
    }


def hardware_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    stable = snapshot.get("stable_identity") or {}
    dynamic = snapshot.get("dynamic_resources") or {}
    cpu = stable.get("cpu") or {}
    gpus = stable.get("gpus") or []
    primary = gpus[0] if gpus else {}
    return {
        "profile_hash": snapshot.get("profile_hash"),
        "cpu": cpu.get("name"),
        "physical_cores": cpu.get("physical_cores"),
        "logical_threads": cpu.get("logical_threads"),
        "planning_cores": cpu.get("planning_cores"),
        "ram_total_bytes": stable.get("memory_total_bytes"),
        "ram_available_bytes": dynamic.get("memory_available_bytes"),
        "gpu": primary.get("name"),
        "vram_total_mb": primary.get("memory_total_mb"),
        "python_version": (stable.get("python") or {}).get("version"),
        "capacity_equation": (
            (snapshot.get("capacity_authority") or {}).get("equation")
        ),
    }
