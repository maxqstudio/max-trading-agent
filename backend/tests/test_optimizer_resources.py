from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from max_backend import optimizer_jobs, optimizer_runtime
from max_backend.optimizer_core import build_tester_ini
from max_backend.optimizer_store import load_resource_calibration


def test_launch_uses_native_terminal_popen_and_ignores_legacy_cap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    terminal = tmp_path / "terminal64.exe"
    terminal.write_bytes(b"MZ")
    ini = tmp_path / "round.ini"
    ini.write_text("[Tester]\n", encoding="utf-8")
    request = {
        "mt5": {"terminal": str(terminal)},
        "resource_policy": {"status": "BLOCKED", "resolved_max_local_agents": 1},
    }
    calls: list[tuple[tuple, dict]] = []
    waits: list[int] = []
    ownership: list[dict] = []

    monkeypatch.setattr(optimizer_runtime, "_matching_terminal_pids", lambda _terminal: [])

    def fake_popen(command, **kwargs):
        calls.append((tuple(command), kwargs))
        return SimpleNamespace(pid=4321, wait=lambda timeout: waits.append(timeout) or 0)

    monkeypatch.setattr(optimizer_runtime.subprocess, "Popen", fake_popen)
    result = optimizer_runtime.launch_mt5(
        request,
        ini_path=ini,
        timeout_sec=73,
        on_process=ownership.append,
    )

    assert result == 0
    assert calls == [((str(terminal), f"/config:{ini}"), {"cwd": str(tmp_path)})]
    assert waits == [73]
    assert ownership[0]["pid"] == 4321
    assert ownership[0]["executable_path"] == str(terminal.resolve())
    assert ownership[0]["ini_path"] == str(ini.resolve())


def test_tester_ini_does_not_override_owner_agent_configuration() -> None:
    ini = build_tester_ini(
        {
            "optimization": 2,
            "symbol": "EURUSD",
            "period": "H1",
            "deposit": 10000,
            "leverage": 100,
            "model": 1,
            "from_date": "2020.01.01",
            "to_date": "2020.12.31",
        },
        expert_name="Max_MTF",
    )

    assert "UseCloud=0" in ini
    assert "UseLocal=" not in ini
    assert "UseRemote=" not in ini


def test_start_does_not_gate_on_legacy_resource_preflight(monkeypatch, tmp_path: Path) -> None:
    raw_request = {"resource_policy": {"status": "BLOCKED"}}
    frozen_request = {"mt5": {}, "legacy_resource_policy": raw_request["resource_policy"]}
    job = {"job_id": "job-native-launch"}
    observed = {}

    monkeypatch.setattr(optimizer_jobs, "migrate_m03", lambda: None)

    def freeze(raw):
        observed["raw"] = raw
        return frozen_request

    monkeypatch.setattr(optimizer_jobs, "freeze_request", freeze)
    monkeypatch.setattr(optimizer_jobs, "create_job", lambda *_a, **_k: job)
    monkeypatch.setattr(optimizer_jobs, "job_evidence_dir", lambda _job_id: tmp_path)
    monkeypatch.setattr(optimizer_jobs, "write_json", lambda *_a, **_k: None)
    monkeypatch.setattr(optimizer_jobs, "_spawn_worker", lambda *_a, **_k: {"status": "STARTING"})

    result = optimizer_jobs.start_optimizer(raw_request)

    assert observed["raw"] is raw_request
    assert result == {"status": "STARTING"}


def test_historical_resource_calibration_is_ignored_without_database_access(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "owner.sqlite3"

    assert load_resource_calibration("a" * 64, path=database_path) is None
    assert not database_path.exists()


def test_production_optimizer_has_no_external_active_process_cap() -> None:
    source_root = Path(__file__).resolve().parents[1] / "max_backend"
    production_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in source_root.rglob("*.py")
    )

    assert "ActiveProcess" + "Limit" not in production_source
    assert "JOB_OBJECT_LIMIT_" + "ACTIVE_PROCESS" not in production_source
    assert "launch_bounded_mt5" not in production_source
    assert "frozen_resource_admission" not in production_source
