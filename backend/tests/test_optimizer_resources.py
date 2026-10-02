from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from max_backend import optimizer_jobs, optimizer_resource_runtime, optimizer_worker
from max_backend.db import ensure_baseline_registered, initialize_database
from max_backend.optimizer_store import (
    create_job,
    load_resource_calibration,
    migrate_m01,
    update_job,
    upsert_round,
)
from max_backend.optimizer_resources import (
    default_resource_settings,
    frozen_resource_admission,
    resolve_resource_preflight,
    classify_resource_pressure,
    resource_pressure_state,
    validate_resource_settings,
    workload_compatibility_key,
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
    total = int(total_gib * GIB)
    commit_limit = int(total_gib * 1.5 * GIB)
    commit_charge = int(total_gib * 0.375 * GIB)
    return {
        "total_ram_bytes": total,
        "available_ram_bytes": int(available_gib * GIB),
        "total_virtual_bytes": int(44 * GIB),
        "available_virtual_bytes": int(32 * GIB),
        "commit_charge_bytes": commit_charge,
        "commit_limit_bytes": commit_limit,
        "commit_headroom_bytes": commit_limit - commit_charge,
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
        "from_date": "2019.01.01",
        "to_date": "2026.09.30",
        "tick_model": 1,
        "tick_model_name": "1 minute OHLC",
        "symbol": "EURUSD.m",
        "period": "H1",
        "ea_sha256": "a" * 64,
        "history_span_bucket": "3Y+",
    }


def test_auto_safe_resolves_bounded_cpu_and_ram_cap() -> None:
    result = resolve_resource_preflight(
        default_resource_settings(), capacity(), workload=workload()
    )
    assert result["status"] == "SAFE"
    assert result["mode"] == "AUTO_SAFE"
    assert result["resolved_max_local_agents"] == 1
    assert result["calibration_status"] == "NONE"
    assert result["estimation_source"] == "CONSERVATIVE_FALLBACK"
    assert result["resolved_max_local_agents"] < result["detected"]["logical_processors"]
    assert result["max_job_processes"] == result["resolved_max_local_agents"] + 1
    assert result["terminal_commit_budget_bytes"] >= 14 * GIB
    assert result["per_agent_commit_budget_bytes"] >= 8 * GIB


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
    assert result["resolved_max_local_agents"] == 1
    assert result["requested_max_local_agents"] == 3
    assert result["minimum_free_ram_bytes"] >= 6 * GIB
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


def test_frozen_policy_blocks_when_native_telemetry_raises_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = resolve_resource_preflight(
        default_resource_settings(), capacity(), workload=workload()
    )

    def fail_native_telemetry(*_args: object, **_kwargs: object) -> dict:
        raise OSError("native telemetry unavailable")

    monkeypatch.setattr(
        "max_backend.optimizer_resources.detect_resource_capacity",
        fail_native_telemetry,
    )
    result = frozen_resource_admission(
        policy,
        mt5={"terminal": "unused", "data_root": "unused"},
    )
    assert result == {
        "status": "BLOCKED",
        "reason": "Current resource telemetry or frozen policy is invalid; execution is blocked.",
    }


def test_low_commit_headroom_blocks_even_when_physical_ram_is_high() -> None:
    detected = capacity(available_gib=28)
    detected["commit_charge_bytes"] = 45 * GIB
    detected["commit_limit_bytes"] = 48 * GIB
    detected["commit_headroom_bytes"] = 3 * GIB
    result = resolve_resource_preflight(default_resource_settings(), detected, workload=workload())
    assert result["status"] == "BLOCKED"
    assert result["resolved_max_local_agents"] == 0
    assert result["commit_safe_agent_slots"] == 0
    assert result["detected"]["commit_headroom_bytes"] == 3 * GIB


def test_small_ram_with_large_pagefile_still_blocks() -> None:
    detected = capacity(total_gib=8, available_gib=7)
    detected["commit_limit_bytes"] = 128 * GIB
    detected["commit_charge_bytes"] = 16 * GIB
    detected["commit_headroom_bytes"] = 112 * GIB
    result = resolve_resource_preflight(default_resource_settings(), detected, workload=workload())
    assert result["status"] == "BLOCKED"
    assert result["physical_ram_safe_agent_slots"] == 0


def test_custom_reserve_below_hard_floor_cannot_reduce_safety() -> None:
    settings = default_resource_settings()
    settings.update({"mode": "CUSTOM", "custom_min_free_ram_gb": 1.0, "custom_max_local_agents": 32})
    result = resolve_resource_preflight(settings, capacity(available_gib=18), workload=workload())
    assert result["minimum_free_ram_bytes"] >= result["hard_physical_reserve_bytes"]
    assert result["resolved_max_local_agents"] <= result["safe_agent_cap"]
    assert result["requested_max_local_agents"] == 32


def test_compatible_calibration_raises_fallback_estimates_conservatively() -> None:
    current_workload = workload()
    key = workload_compatibility_key(current_workload)
    calibration = {
        "workload_key": key,
        "peak_terminal_private_bytes": 16 * GIB,
        "peak_terminal_working_set_bytes": 8 * GIB,
        "peak_tester_private_bytes": 20 * GIB,
        "peak_single_tester_private_bytes": 10 * GIB,
        "peak_tester_working_set_bytes": 8 * GIB,
        "peak_single_tester_working_set_bytes": 4 * GIB,
        "actual_max_active_agents": 2,
    }
    result = resolve_resource_preflight(
        default_resource_settings(), capacity(), workload=current_workload, calibration=calibration
    )
    assert result["calibration_status"] == "MEASURED"
    assert result["estimation_source"] == "MEASURED"
    assert result["terminal_commit_budget_bytes"] >= 20 * GIB
    assert result["per_agent_commit_budget_bytes"] >= 12 * GIB


def test_incompatible_calibration_is_ignored() -> None:
    current_workload = workload()
    result = resolve_resource_preflight(
        default_resource_settings(),
        capacity(),
        workload=current_workload,
        calibration={
            "workload_key": "different-workload",
            "peak_terminal_private_bytes": 50 * GIB,
            "peak_single_tester_private_bytes": 40 * GIB,
        },
    )
    assert result["calibration_status"] == "NONE"
    assert result["terminal_commit_budget_bytes"] == 14 * GIB
    assert result["per_agent_commit_budget_bytes"] == 12 * GIB


def test_zero_or_inconsistent_commit_metrics_fail_closed() -> None:
    detected = capacity()
    detected["commit_limit_bytes"] = 0
    with pytest.raises(RuntimeError, match="CAPACITY_INVALID"):
        resolve_resource_preflight(default_resource_settings(), detected, workload=workload())
    detected = capacity()
    detected["commit_charge_bytes"] = detected["commit_limit_bytes"] + 1
    with pytest.raises(RuntimeError, match="CAPACITY_INVALID"):
        resolve_resource_preflight(default_resource_settings(), detected, workload=workload())


def test_commit_pressure_states_are_explicit_and_precede_oom() -> None:
    policy = resolve_resource_preflight(default_resource_settings(), capacity(), workload=workload())
    assert classify_resource_pressure(
        available_ram_bytes=capacity()["available_ram_bytes"],
        commit_headroom_bytes=policy["pressure_commit_headroom_bytes"] + 1,
        policy=policy,
    ) == "SAFE"
    assert classify_resource_pressure(
        available_ram_bytes=capacity()["available_ram_bytes"],
        commit_headroom_bytes=policy["pressure_commit_headroom_bytes"],
        policy=policy,
    ) == "COMMIT_PRESSURE"
    assert classify_resource_pressure(
        available_ram_bytes=capacity()["available_ram_bytes"],
        commit_headroom_bytes=policy["critical_commit_headroom_bytes"],
        policy=policy,
    ) == "CRITICAL_COMMIT"


def test_start_revalidation_uses_changed_commit_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = resolve_resource_preflight(default_resource_settings(), capacity(), workload=workload())
    lower_headroom = capacity(available_gib=28)
    lower_headroom["commit_charge_bytes"] = 46 * GIB
    lower_headroom["commit_limit_bytes"] = 48 * GIB
    lower_headroom["commit_headroom_bytes"] = 2 * GIB
    monkeypatch.setattr(
        "max_backend.optimizer_resources.detect_resource_capacity",
        lambda *_args, **_kwargs: lower_headroom,
    )
    result = frozen_resource_admission(policy, mt5={"terminal": "unused", "data_root": "unused"})
    assert result["status"] == "BLOCKED"
    assert result["commit_headroom_bytes"] == 2 * GIB


def test_worker_rechecks_frozen_commit_before_mt5_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    request = {
        "ea": {"sha256": "frozen"},
        "mt5": {"terminal": "terminal", "data_root": "data"},
        "resource_policy": {"schema": "MAX_OPTIMIZER_RESOURCE_POLICY_V2"},
    }
    monkeypatch.setattr(optimizer_worker, "get_round", lambda *_a, **_k: None)
    monkeypatch.setattr(optimizer_worker, "prepare_round", lambda *_a, **_k: {"phase": "PREPARED", "optimizer_run_nonce": 1})
    monkeypatch.setattr(optimizer_worker, "_save_phase", lambda _job, _round, state, phase, **_kwargs: {**state, "phase": phase})
    monkeypatch.setattr(optimizer_worker, "sha256_file", lambda _path: "frozen")
    monkeypatch.setattr(optimizer_worker, "frozen_resource_admission", lambda *_a, **_k: {"status": "BLOCKED", "reason": "commit headroom changed"})
    monkeypatch.setattr(optimizer_worker, "snapshot_compatible_reports", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("prelaunch must block first")))
    monkeypatch.setattr(optimizer_worker, "launch_mt5", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("MT5 must not launch")))
    with pytest.raises(RuntimeError, match="RESOURCE_PREFLIGHT_BLOCKED.*commit headroom changed"):
        optimizer_worker.execute_round(request, job_id="J", round_no=1, search_space={}, resume=False)


def test_workload_calibration_key_changes_for_material_identity() -> None:
    base = workload()
    key = workload_compatibility_key(base)
    assert workload_compatibility_key({**base, "period": "M15"}) != key
    assert workload_compatibility_key({**base, "tick_model_name": "Every tick"}) != key
    assert workload_compatibility_key({**base, "ea_sha256": "b" * 64}) != key


def test_compatible_round_high_water_is_reused_without_schema_change(tmp_path: Path) -> None:
    absent_database = tmp_path / "absent" / "optimizer.db"
    assert load_resource_calibration("0" * 64, path=absent_database) is None
    assert not absent_database.parent.exists()

    database = tmp_path / "optimizer.db"
    initialize_database(database)
    ensure_baseline_registered(database)
    migrate_m01(database)
    key = workload_compatibility_key(workload())
    summary = {
        "workload_compatibility_key": key,
        "sample_count": 8,
        "actual_max_active_agents": 2,
        "min_available_ram_bytes": 5 * GIB,
        "min_commit_headroom_bytes": 7 * GIB,
        "peak_terminal_private_bytes": 15 * GIB,
        "peak_terminal_working_set_bytes": 7 * GIB,
        "peak_tester_private_bytes": 20 * GIB,
        "peak_single_tester_private_bytes": 10 * GIB,
        "peak_tester_working_set_bytes": 8 * GIB,
        "peak_single_tester_working_set_bytes": 4 * GIB,
    }
    job = create_job(
        {
            "max_rounds": 1,
            "resource_policy": {"workload": {"compatibility_key": key}},
        },
        evidence_root=tmp_path / "evidence",
        path=database,
    )
    upsert_round(
        job["job_id"],
        1,
        phase="RESOURCE_STOPPED",
        state={"resource_runtime": summary},
        path=database,
    )
    update_job(job["job_id"], active=False, status="RESOURCE_STOPPED", path=database)
    loaded = load_resource_calibration(key, path=database)
    assert loaded is not None
    assert loaded["peak_terminal_private_bytes"] == 15 * GIB
    assert loaded["peak_single_tester_private_bytes"] == 10 * GIB
    assert loaded["min_commit_headroom_bytes"] == 7 * GIB
    assert loaded["compatible_run_count"] == 1
    assert load_resource_calibration("0" * 64, path=database) is None


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
    monkeypatch.setattr(optimizer_resource_runtime, "_system_resource_snapshot", lambda: {
        "available_ram_bytes": int(policy["critical_free_ram_bytes"]) - 1,
        "commit_charge_bytes": capacity()["commit_charge_bytes"],
        "commit_limit_bytes": capacity()["commit_limit_bytes"],
        "commit_headroom_bytes": capacity()["commit_headroom_bytes"],
    })
    monkeypatch.setattr(optimizer_resource_runtime, "_job_process_rows", lambda *_a, **_k: {
        "terminal": {"working_set_bytes": 100, "private_bytes": 100}, "agents": []
    })
    monkeypatch.setattr(optimizer_resource_runtime, "_terminate_and_verify_job", lambda *_a, **_k: calls.append(("terminate", ())) or True)
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
    assert caught.value.reason == "CRITICAL_PHYSICAL"
    assert states[-2:] == ["RESOURCE_STOP_REQUESTED", "RESOURCE_STOPPED"]
    assert any(item[0] == "terminate" for item in calls)


def test_monitor_fail_closes_repeated_telemetry_loss(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    policy = resolve_resource_preflight(default_resource_settings(), capacity(), workload=workload())
    terminal = tmp_path / "terminal64.exe"
    terminal.write_bytes(b"MZ")
    ini = tmp_path / "round.ini"
    ini.write_text("[Tester]", encoding="utf-8")
    monkeypatch.setattr(optimizer_resource_runtime, "_launch_in_job", lambda *_a, **_k: (101, 202, 303, "synthetic"))
    monkeypatch.setattr(optimizer_resource_runtime, "_system_resource_snapshot", lambda: (_ for _ in ()).throw(RuntimeError("native telemetry failed")))
    monkeypatch.setattr(optimizer_resource_runtime, "_terminate_and_verify_job", lambda *_a, **_k: True)
    monkeypatch.setattr(optimizer_resource_runtime.time, "sleep", lambda *_a, **_k: None)
    class Kernel:
        def WaitForSingleObject(self, *_a): return 258
        def CloseHandle(self, _handle): return 1
    monkeypatch.setattr(optimizer_resource_runtime.ctypes, "WinDLL", lambda *_a, **_k: Kernel())
    states = []
    with pytest.raises(optimizer_resource_runtime.ResourceGuardTriggered) as caught:
        optimizer_resource_runtime.launch_bounded_mt5(terminal, ini, timeout_sec=30, policy=policy, on_resource=lambda value: states.append(value["resource_state"]))
    assert caught.value.reason == "RESOURCE_TELEMETRY_UNAVAILABLE"
    assert states[-3:] == ["TELEMETRY_UNAVAILABLE", "RESOURCE_STOP_REQUESTED", "RESOURCE_STOPPED"]
    assert caught.value.summary["telemetry_failure_count"] >= 2


def test_monitor_commit_pressure_stops_owned_job_without_oomevent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    policy = resolve_resource_preflight(default_resource_settings(), capacity(), workload=workload())
    terminal = tmp_path / "terminal64.exe"
    terminal.write_bytes(b"MZ")
    ini = tmp_path / "round.ini"
    ini.write_text("[Tester]", encoding="utf-8")
    snapshots = iter([
        {"available_ram_bytes": 22 * GIB, "commit_charge_bytes": 10 * GIB, "commit_limit_bytes": 48 * GIB, "commit_headroom_bytes": 38 * GIB},
        {"available_ram_bytes": 22 * GIB, "commit_charge_bytes": 40 * GIB, "commit_limit_bytes": 48 * GIB, "commit_headroom_bytes": 8 * GIB},
        {"available_ram_bytes": 22 * GIB, "commit_charge_bytes": 44 * GIB, "commit_limit_bytes": 48 * GIB, "commit_headroom_bytes": 4 * GIB},
    ])
    monkeypatch.setattr(optimizer_resource_runtime, "_launch_in_job", lambda *_a, **_k: (101, 202, 303, "synthetic"))
    monkeypatch.setattr(optimizer_resource_runtime, "_system_resource_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(optimizer_resource_runtime, "_job_process_rows", lambda *_a, **_k: {
        "terminal": {"working_set_bytes": 100, "private_bytes": 100}, "agents": []
    })
    monkeypatch.setattr(optimizer_resource_runtime, "_terminate_and_verify_job", lambda *_a, **_k: True)
    monkeypatch.setattr(optimizer_resource_runtime.time, "sleep", lambda *_a, **_k: None)
    class Kernel:
        def WaitForSingleObject(self, *_a): return 258
        def CloseHandle(self, _handle): return 1
    monkeypatch.setattr(optimizer_resource_runtime.ctypes, "WinDLL", lambda *_a, **_k: Kernel())
    states = []
    with pytest.raises(optimizer_resource_runtime.ResourceGuardTriggered) as caught:
        optimizer_resource_runtime.launch_bounded_mt5(terminal, ini, timeout_sec=30, policy=policy, on_resource=lambda value: states.append(value["resource_state"]))
    assert caught.value.reason == "CRITICAL_COMMIT"
    assert "COMMIT_PRESSURE" in states
    assert states[-2:] == ["RESOURCE_STOP_REQUESTED", "RESOURCE_STOPPED"]
    assert caught.value.summary["min_commit_headroom_bytes"] == 4 * GIB


def test_monitor_requires_verified_termination_after_telemetry_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    policy = resolve_resource_preflight(default_resource_settings(), capacity(), workload=workload())
    terminal = tmp_path / "terminal64.exe"
    terminal.write_bytes(b"MZ")
    ini = tmp_path / "round.ini"
    ini.write_text("[Tester]", encoding="utf-8")
    monkeypatch.setattr(optimizer_resource_runtime, "_launch_in_job", lambda *_a, **_k: (101, 202, 303, "synthetic"))
    monkeypatch.setattr(optimizer_resource_runtime, "_system_resource_snapshot", lambda: (_ for _ in ()).throw(RuntimeError("telemetry lost")))
    monkeypatch.setattr(optimizer_resource_runtime, "_terminate_and_verify_job", lambda *_a, **_k: False)
    monkeypatch.setattr(optimizer_resource_runtime.time, "sleep", lambda *_a, **_k: None)
    class Kernel:
        def WaitForSingleObject(self, *_a): return 258
        def CloseHandle(self, _handle): return 1
    monkeypatch.setattr(optimizer_resource_runtime.ctypes, "WinDLL", lambda *_a, **_k: Kernel())
    states = []
    with pytest.raises(optimizer_resource_runtime.ResourceGuardTriggered) as caught:
        optimizer_resource_runtime.launch_bounded_mt5(terminal, ini, timeout_sec=30, policy=policy, on_resource=lambda value: states.append(value["resource_state"]))
    assert caught.value.reason == "RESOURCE_RECONCILIATION_REQUIRED"
    assert states[-1] == "RESOURCE_RECONCILIATION_REQUIRED"


def test_resource_monitor_uses_native_queries_not_powershell_or_cim() -> None:
    source = Path(optimizer_resource_runtime.__file__).read_text(encoding="utf-8")
    assert "powershell.exe" not in source.casefold()
    assert "get-ciminstance" not in source.casefold()
    assert "subprocess.run(" not in source
    assert "_job_process_ids" in source
    assert "PrivateUsage" in source


def test_job_process_rows_only_accepts_owned_known_executables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    terminal = tmp_path / "terminal64.exe"
    tester = tmp_path / "metatester64.exe"
    terminal.write_bytes(b"MZ")
    tester.write_bytes(b"MZ")
    paths = {101: str(terminal), 102: str(tester)}
    monkeypatch.setattr(optimizer_resource_runtime, "_job_process_ids", lambda *_a: [101, 102])
    monkeypatch.setattr(optimizer_resource_runtime, "_process_row", lambda pid: {
        "pid": pid, "executable_path": paths[pid], "working_set_bytes": pid, "private_bytes": pid * 2,
    })
    result = optimizer_resource_runtime._job_process_rows(object(), terminal, 2)
    assert result["terminal"]["pid"] == 101
    assert [row["pid"] for row in result["agents"]] == [102]

    monkeypatch.setattr(optimizer_resource_runtime, "_job_process_ids", lambda *_a: [101, 999])
    paths[999] = str(tmp_path / "other.exe")
    with pytest.raises(RuntimeError, match="UNKNOWN_PROCESS"):
        optimizer_resource_runtime._job_process_rows(object(), terminal, 2)


def test_native_process_memory_query_closes_handle_on_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Function:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None
        def __call__(self, *args):
            return self.callback(*args)

    closed = []
    fail_memory = False
    kernel = type("Kernel", (), {})()
    psapi = type("Psapi", (), {})()
    kernel.OpenProcess = Function(lambda *_args: 99)
    kernel.QueryFullProcessImageNameW = Function(
        lambda _handle, _flags, output, _size: setattr(output, "value", r"C:\MT5\terminal64.exe") or 1
    )
    kernel.CloseHandle = Function(lambda handle: closed.append(handle) or 1)

    def get_memory_info(_handle, counters_ptr, _size):
        if fail_memory:
            return 0
        counters = counters_ptr._obj
        counters.WorkingSetSize = 300
        counters.PrivateUsage = 500
        counters.PagefileUsage = 600
        return 1

    psapi.GetProcessMemoryInfo = Function(get_memory_info)
    monkeypatch.setattr(
        optimizer_resource_runtime.ctypes,
        "WinDLL",
        lambda name, **_kwargs: kernel if name == "kernel32" else psapi,
    )
    row = optimizer_resource_runtime._process_row(42)
    assert row["working_set_bytes"] == 300
    assert row["private_bytes"] == 500
    assert row["pagefile_bytes"] == 600
    assert len(closed) == 1

    fail_memory = True
    with pytest.raises(RuntimeError, match="PROCESS_MEMORY_FAILED"):
        optimizer_resource_runtime._process_row(42)
    assert len(closed) == 2


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
