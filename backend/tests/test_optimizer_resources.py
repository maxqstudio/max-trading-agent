from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from max_backend import optimizer_jobs, optimizer_resource_runtime
from max_backend.optimizer_resources import (
    default_resource_settings,
    frozen_resource_admission,
    resolve_resource_preflight,
    resource_pressure_state,
    validate_resource_settings,
)

GIB = 1024 ** 3


def capacity(
    *,
    total_gib: float = 32,
    available_gib: float = 22,
    physical: int = 6,
    logical: int = 12,
    local_agents: int = 12,
) -> dict:
    return {
        "total_ram_bytes": int(total_gib * GIB),
        "available_ram_bytes": int(available_gib * GIB),
        "total_virtual_bytes": int(44 * GIB),
        "available_virtual_bytes": int(32 * GIB),
        "physical_cores": physical,
        "logical_processors": logical,
        "configured_local_agent_capacity": local_agents,
        "max_backend_rss_bytes": 300 * 1024 ** 2,
        "windows_version": "10.0",
        "windows_build": "test",
        "mt5_build": "test",
    }


def workload() -> dict:
    return {
        "optimization": 2,
        "optimization_name": "Fast Genetic",
        "optimized_parameter_count": 17,
        "raw_complete_grid_combinations": 10**18,
        "from_date": "2026.01.01",
        "to_date": "2026.02.01",
        "tick_model": 1,
        "tick_model_name": "Every tick based on real ticks",
    }


def test_auto_safe_resolves_bounded_cpu_and_ram_cap() -> None:
    result = resolve_resource_preflight(
        default_resource_settings(), capacity(), workload=workload()
    )
    assert result["status"] == "SAFE"
    assert result["mode"] == "AUTO_SAFE"
    assert 1 <= result["resolved_max_local_agents"] <= 5
    assert result["resolved_max_local_agents"] < result["detected"]["logical_processors"]
    assert result["max_job_processes"] == result["resolved_max_local_agents"] + 1


def test_auto_safe_blocks_when_even_one_agent_has_no_headroom() -> None:
    result = resolve_resource_preflight(
        default_resource_settings(),
        capacity(total_gib=16, available_gib=2),
        workload=workload(),
    )
    assert result["status"] == "BLOCKED"
    assert result["resolved_max_local_agents"] == 0
    assert "Insufficient" in result["reason"]


def test_single_logical_processor_fails_closed() -> None:
    result = resolve_resource_preflight(
        default_resource_settings(),
        capacity(physical=1, logical=1, local_agents=1),
        workload=workload(),
    )
    assert result["status"] == "SAFE"
    assert result["resolved_max_local_agents"] == 1
    assert result["cpu_reserve_logical"] == 0


def test_many_logical_processors_still_reserves_cpu() -> None:
    result = resolve_resource_preflight(
        default_resource_settings(),
        capacity(total_gib=128, available_gib=100, physical=16, logical=32, local_agents=32),
        workload=workload(),
    )
    assert result["status"] == "SAFE"
    assert result["cpu_reserve_logical"] >= 8
    assert result["resolved_max_local_agents"] <= 15


def test_custom_cap_is_respected_without_expanding_hardware_capacity() -> None:
    settings = default_resource_settings()
    settings.update({
        "mode": "CUSTOM",
        "custom_max_local_agents": 3,
        "custom_min_free_ram_gb": 6.0,
        "custom_cpu_reserve_logical": 3,
    })
    result = resolve_resource_preflight(settings, capacity(), workload=workload())
    assert result["status"] == "SAFE"
    assert result["resolved_max_local_agents"] == 3
    assert result["minimum_free_ram_bytes"] == 6 * GIB
    assert result["cpu_reserve_logical"] == 3


def test_custom_settings_reject_invalid_values() -> None:
    settings = default_resource_settings()
    settings["custom_max_local_agents"] = 0
    with pytest.raises(ValueError, match="MAX_AGENTS"):
        validate_resource_settings(settings)


def test_resource_pressure_states_are_explicit() -> None:
    assert resource_pressure_state(10 * GIB, pressure_free_ram_bytes=8 * GIB, critical_free_ram_bytes=6 * GIB) == "SAFE"
    assert resource_pressure_state(7 * GIB, pressure_free_ram_bytes=8 * GIB, critical_free_ram_bytes=6 * GIB) == "PRESSURE"
    assert resource_pressure_state(6 * GIB, pressure_free_ram_bytes=8 * GIB, critical_free_ram_bytes=6 * GIB) == "CRITICAL"


def test_frozen_policy_resume_blocks_when_current_ram_cannot_honor_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = resolve_resource_preflight(
        default_resource_settings(), capacity(), workload=workload()
    )
    monkeypatch.setattr(
        "max_backend.optimizer_resources.detect_resource_capacity",
        lambda *_args, **_kwargs: capacity(available_gib=4),
    )
    result = frozen_resource_admission(
        policy,
        mt5={"terminal": "unused", "data_root": "unused"},
    )
    assert result["status"] == "BLOCKED"
    assert result["available_ram_bytes"] < result["required_available_ram_bytes"]


def test_start_blocks_before_job_creation_when_preflight_is_unsafe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(optimizer_jobs, "migrate_m03", lambda: None)
    monkeypatch.setattr(
        optimizer_jobs,
        "freeze_request",
        lambda *_args, **_kwargs: {
            "resource_policy": {
                "status": "BLOCKED",
                "reason": "Insufficient currently available RAM for safe MT5 optimization.",
            }
        },
    )
    monkeypatch.setattr(
        optimizer_jobs,
        "create_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("job must not be created")),
    )
    with pytest.raises(RuntimeError, match="RESOURCE_PREFLIGHT_BLOCKED"):
        optimizer_jobs.start_optimizer({})


def test_critical_ram_fault_injection_terminates_owned_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    policy = resolve_resource_preflight(default_resource_settings(), capacity(), workload=workload())
    terminal = tmp_path / "terminal64.exe"
    terminal.write_bytes(b"MZ")
    ini = tmp_path / "round.ini"
    ini.write_text("[Tester]", encoding="utf-8")
    monkeypatch.setattr(optimizer_resource_runtime, "_launch_in_job", lambda *_a, **_k: (101, 202, 303, "synthetic"))
    monkeypatch.setattr(optimizer_resource_runtime, "_system_available_ram_bytes", lambda: int(policy["critical_free_ram_bytes"]) - 1)
    monkeypatch.setattr(optimizer_resource_runtime, "_process_rows", lambda *_a, **_k: {"terminal": {"WorkingSetSize": 100}, "agents": []})
    monkeypatch.setattr(optimizer_resource_runtime.time, "sleep", lambda *_a, **_k: None)
    calls = []
    class Kernel:
        def WaitForSingleObject(self, *_a): return 258
        def TerminateJobObject(self, *args): calls.append(("terminate", args)); return 1
        def CloseHandle(self, handle): calls.append(("close", handle)); return 1
    monkeypatch.setattr(optimizer_resource_runtime.ctypes, "WinDLL", lambda *_a, **_k: Kernel())
    states = []
    with pytest.raises(optimizer_resource_runtime.ResourceGuardTriggered) as caught:
        optimizer_resource_runtime.launch_bounded_mt5(terminal, ini, timeout_sec=30, policy=policy, on_resource=lambda value: states.append(value["resource_state"]))
    assert caught.value.reason == "CRITICAL_FREE_RAM"
    assert states[-2:] == ["RESOURCE_STOP_REQUESTED", "RESOURCE_STOPPED"]
    assert any(item[0] == "terminate" for item in calls)


def test_resource_stopped_resume_rechecks_frozen_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    job = {"job_id": "J", "status": "RESOURCE_STOPPED", "active": False, "worker_pid": None, "request": {"resource_policy": {"schema": "MAX_OPTIMIZER_RESOURCE_POLICY_V1"}, "mt5": {}}}
    monkeypatch.setattr(optimizer_jobs, "get_job", lambda _job_id: job)
    monkeypatch.setattr(optimizer_jobs, "_ensure_request_snapshot", lambda _job: None)
    monkeypatch.setattr(optimizer_jobs, "frozen_resource_admission", lambda *_a, **_k: {"status": "BLOCKED", "reason": "synthetic low RAM"})
    monkeypatch.setattr(optimizer_jobs, "_spawn_worker", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("worker must not launch")))
    with pytest.raises(RuntimeError, match="RESOURCE_PREFLIGHT_BLOCKED"):
        optimizer_jobs.resume_optimizer("J")


@pytest.mark.skipif(optimizer_jobs.os.name != "nt", reason="Windows process memory API")
def test_backend_working_set_detector_returns_real_bytes() -> None:
    value = optimizer_resource_runtime.os.getpid()  # proves process context is live
    assert value > 0
    from max_backend.optimizer_resources import _current_process_working_set_bytes
    assert _current_process_working_set_bytes() > 0
