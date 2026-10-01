from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import max_backend.optimizer_jobs as optimizer_jobs
import max_backend.optimizer_worker as optimizer_worker
from max_backend.db import ensure_baseline_registered, initialize_database
from max_backend.optimizer_jobs import (
    _worker_identity_matches,
    reconcile_optimizer_startup,
)
from max_backend.optimizer_store import (
    claim_initial_worker_launch,
    claim_resume_worker_launch,
    confirm_worker_launch,
    create_job,
    get_job,
    get_round,
    update_job,
    upsert_round,
)
from max_backend.workflow_store import migrate_current


def make_db(tmp_path: Path) -> Path:
    path = tmp_path / "state" / "max.db"
    initialize_database(path)
    ensure_baseline_registered(path)
    migrate_current(path)
    return path


def test_startup_reconciles_worker_loss_before_mt5_to_resumable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    monkeypatch.setattr("max_backend.optimizer_jobs._worker_is_alive", lambda *_a, **_k: False)
    monkeypatch.setattr("max_backend.optimizer_jobs._find_worker_by_token", lambda *_a, **_k: None)
    result = reconcile_optimizer_startup(path=path)

    current = get_job(job["job_id"], path=path)
    assert result["action"] == "STALE_ACTIVE_JOB_RECONCILED"
    assert current is not None
    assert current["status"] == "INTERRUPTED_SAFE_TO_RESUME"
    assert current["active"] is False
    assert current["first_blocker"] == "WORKER_INTERRUPTED_SAFE_TO_RESUME"


def test_startup_never_converts_mt5_launch_intent_to_blind_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    update_job(job["job_id"], status="MT5_RUNNING", path=path)
    upsert_round(
        job["job_id"],
        1,
        phase="LAUNCH_INTENT",
        state={"phase": "LAUNCH_INTENT", "ini_path": str(tmp_path / "unique.ini")},
        path=path,
    )
    monkeypatch.setattr("max_backend.optimizer_jobs._worker_is_alive", lambda *_a, **_k: False)
    monkeypatch.setattr("max_backend.optimizer_jobs._find_worker_by_token", lambda *_a, **_k: None)
    result = reconcile_optimizer_startup(path=path)

    current = get_job(job["job_id"], path=path)
    assert result["status"] == "EXECUTION_UNCERTAIN"
    assert current is not None
    assert current["status"] == "EXECUTION_UNCERTAIN"
    assert current["active"] is False
    with pytest.raises(RuntimeError, match="requires resume or explicit stop"):
        create_job(
            {"max_rounds": 1},
            evidence_root=tmp_path / "artifacts" / "optimizer",
            path=path,
        )


def test_resume_launch_claim_is_single_flight(tmp_path: Path) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    update_job(
        job["job_id"],
        status="INTERRUPTED_SAFE_TO_RESUME",
        active=False,
        path=path,
    )
    claim_resume_worker_launch(
        job["job_id"],
        "first-token",
        allowed_statuses={"INTERRUPTED_SAFE_TO_RESUME"},
        path=path,
    )
    with pytest.raises(RuntimeError, match="OPTIMIZER_WORKER_LAUNCH_ALREADY_ACTIVE"):
        claim_resume_worker_launch(
            job["job_id"],
            "second-token",
            allowed_statuses={"INTERRUPTED_SAFE_TO_RESUME"},
            path=path,
        )
    current = get_job(job["job_id"], path=path)
    assert current is not None
    assert current["active"] is True
    assert current["launch_token"] == "first-token"


def test_worker_launch_confirmation_cannot_reactivate_a_stopped_claim(tmp_path: Path) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    claim_initial_worker_launch(job["job_id"], "launch-token", path=path)
    update_job(
        job["job_id"],
        status="STOPPED",
        active=False,
        path=path,
    )

    with pytest.raises(RuntimeError, match="OPTIMIZER_WORKER_LAUNCH_CLAIM_LOST"):
        confirm_worker_launch(
            job["job_id"],
            "launch-token",
            worker_pid=4321,
            worker_identity={"pid": 4321},
            path=path,
        )

    current = get_job(job["job_id"], path=path)
    assert current is not None
    assert current["status"] == "STOPPED"
    assert current["active"] is False


def test_worker_ownership_requires_exact_job_token_and_executable() -> None:
    identity = {
        "pid": 100,
        "executable_path": r"C:\Python313\python.exe",
        "command_line": (
            '"C:\\Python313\\python.exe" -m max_backend.optimizer_worker '
            "--job-id job-1 --launch-token token-1"
        ),
        "creation_time": "created",
    }
    assert _worker_identity_matches(
        identity,
        job_id="job-1",
        launch_token="token-1",
        expected={"executable_path": identity["executable_path"], "creation_time": "created"},
    )
    assert not _worker_identity_matches(
        identity,
        job_id="job-1",
        launch_token="token-2",
        expected=None,
    )
    assert not _worker_identity_matches(
        identity,
        job_id="job-10",
        launch_token="token-1",
        expected=None,
    )


def test_worker_process_resolution_selects_venv_python_child(tmp_path: Path) -> None:
    venv = tmp_path / ".venv"
    (venv / "Scripts").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = synthetic\n", encoding="utf-8")
    job_id = "job-venv-launch"
    token = "launch-venv-token"
    command_line = (
        '"C:\\Python313\\python.exe" -m max_backend.optimizer_worker '
        f"--job-id {job_id} --launch-token {token}"
    )
    matches = [
        {
            "pid": 100,
            "parent_pid": 1,
            "executable_path": str(venv / "Scripts" / "python.exe"),
            "command_line": command_line,
            "creation_time": "launcher-created",
        },
        {
            "pid": 200,
            "parent_pid": 100,
            "executable_path": r"C:\Python313\python.exe",
            "command_line": command_line,
            "creation_time": "worker-created",
        },
    ]

    selected = optimizer_jobs._select_worker_candidate(matches, launcher_pid=100)

    assert selected is not None
    assert selected["pid"] == 200
    assert selected["parent_pid"] == 100


def test_worker_process_resolution_fails_closed_for_multiple_children(tmp_path: Path) -> None:
    venv = tmp_path / ".venv"
    (venv / "Scripts").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = synthetic\n", encoding="utf-8")
    command_line = (
        '"C:\\Python313\\python.exe" -m max_backend.optimizer_worker '
        "--job-id job-1 --launch-token token-1"
    )
    matches = [
        {
            "pid": 100,
            "parent_pid": 1,
            "executable_path": str(venv / "Scripts" / "python.exe"),
            "command_line": command_line,
            "creation_time": "launcher-created",
        },
        {
            "pid": 200,
            "parent_pid": 100,
            "executable_path": r"C:\Python313\python.exe",
            "command_line": command_line,
            "creation_time": "worker-one",
        },
        {
            "pid": 201,
            "parent_pid": 100,
            "executable_path": r"C:\Python313\python.exe",
            "command_line": command_line,
            "creation_time": "worker-two",
        },
    ]

    with pytest.raises(RuntimeError, match="OPTIMIZER_WORKER_OWNERSHIP_AMBIGUOUS"):
        optimizer_jobs._select_worker_candidate(matches, launcher_pid=100)


def test_worker_process_resolution_does_not_accept_venv_launcher_alone(tmp_path: Path) -> None:
    venv = tmp_path / ".venv"
    (venv / "Scripts").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = synthetic\n", encoding="utf-8")
    matches = [
        {
            "pid": 100,
            "parent_pid": 1,
            "executable_path": str(venv / "Scripts" / "python.exe"),
            "command_line": (
                r"C:\max\.venv\Scripts\python.exe -m max_backend.optimizer_worker "
                "--job-id job-1 --launch-token token-1"
            ),
            "creation_time": "launcher-created",
        }
    ]

    assert optimizer_jobs._select_worker_candidate(matches, launcher_pid=100) is None


@pytest.mark.skipif(optimizer_jobs.os.name != "nt", reason="Windows Python venv launch behavior")
def test_windows_venv_worker_resolution_finds_actual_child_process(tmp_path: Path) -> None:
    venv_root = tmp_path / "worker-launch-venv"
    created = optimizer_jobs.subprocess.run(
        [optimizer_jobs.sys.executable, "-m", "venv", "--without-pip", str(venv_root)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert created.returncode == 0, created.stderr
    launcher = venv_root / "Scripts" / "python.exe"
    assert launcher.is_file()

    job_id = "pid-probe-" + optimizer_jobs.uuid.uuid4().hex
    token = optimizer_jobs.uuid.uuid4().hex
    process = optimizer_jobs.subprocess.Popen(
        [
            str(launcher),
            "-c",
            "import time; time.sleep(30)",
            "max_backend.optimizer_worker",
            "--job-id",
            job_id,
            "--launch-token",
            token,
        ],
        cwd=str(optimizer_jobs.ROOT),
        creationflags=getattr(optimizer_jobs.subprocess, "CREATE_NO_WINDOW", 0),
        stdout=optimizer_jobs.subprocess.DEVNULL,
        stderr=optimizer_jobs.subprocess.DEVNULL,
    )
    identity: dict[str, object] | None = None
    deadline = optimizer_jobs.time.monotonic() + 10.0
    try:
        while optimizer_jobs.time.monotonic() < deadline:
            identity = optimizer_jobs._find_worker_by_token(
                token,
                job_id,
                launcher_pid=int(process.pid),
            )
            if identity is not None:
                break
            optimizer_jobs.time.sleep(0.1)

        assert identity is not None
        assert identity["pid"] != process.pid
        assert identity["parent_pid"] == process.pid
        current = optimizer_jobs._process_identity(int(identity["pid"]))
        assert optimizer_jobs._worker_identity_matches(
            current,
            job_id=job_id,
            launch_token=token,
            expected=identity,
        )
    finally:
        try:
            if identity is not None:
                optimizer_jobs._terminate_verified_worker(
                    int(identity["pid"]),
                    job_id=job_id,
                    launch_token=token,
                    expected=identity,
                )
        finally:
            optimizer_jobs.subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            try:
                process.wait(timeout=5)
            except optimizer_jobs.subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_spawn_persists_verified_worker_pid_not_venv_launcher_pid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    identity = {
        "pid": 222,
        "parent_pid": 111,
        "executable_path": r"C:\Python313\python.exe",
        "command_line": (
            r"C:\Python313\python.exe -m max_backend.optimizer_worker --job-id "
            + job["job_id"]
            + " --launch-token launch-token"
        ),
        "creation_time": "worker-created",
    }
    evidence = tmp_path / "worker-evidence"
    evidence.mkdir()
    persisted_get_job = optimizer_jobs.get_job
    persisted_update_job = optimizer_jobs.update_job
    persisted_claim = optimizer_jobs.claim_initial_worker_launch
    persisted_confirm = optimizer_jobs.confirm_worker_launch

    monkeypatch.setattr(optimizer_jobs, "get_job", lambda job_id: persisted_get_job(job_id, path=path))
    monkeypatch.setattr(
        optimizer_jobs,
        "claim_initial_worker_launch",
        lambda job_id, token: persisted_claim(job_id, token, path=path),
    )

    def update_in_test_db(job_id: str, **changes: object) -> dict[str, object]:
        changes.setdefault("path", path)
        return persisted_update_job(job_id, **changes)

    def confirm_in_test_db(job_id: str, token: str, **changes: object) -> dict[str, object]:
        changes.setdefault("path", path)
        return persisted_confirm(job_id, token, **changes)

    class FakeProcess:
        pid = 111

        @staticmethod
        def poll() -> None:
            return None

    monkeypatch.setattr(optimizer_jobs, "update_job", update_in_test_db)
    monkeypatch.setattr(optimizer_jobs, "confirm_worker_launch", confirm_in_test_db)
    monkeypatch.setattr(optimizer_jobs, "job_evidence_dir", lambda _job_id: evidence)
    monkeypatch.setattr(optimizer_jobs.subprocess, "Popen", lambda *_a, **_k: FakeProcess())
    monkeypatch.setattr(optimizer_jobs, "_find_worker_by_token", lambda *_a, **_k: identity)
    monkeypatch.setattr(optimizer_jobs, "_process_identity", lambda _pid: identity)
    monkeypatch.setattr(optimizer_jobs.uuid, "uuid4", lambda: SimpleNamespace(hex="launch-token"))

    optimizer_jobs._spawn_worker(job["job_id"], resume=False)

    current = get_job(job["job_id"], path=path)
    assert current is not None
    assert current["worker_pid"] == 222
    assert current["worker_identity"]["pid"] == 222


def test_spawn_marks_reconciliation_required_when_worker_identity_is_unverified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    evidence = tmp_path / "worker-evidence-unverified"
    evidence.mkdir()
    persisted_get_job = optimizer_jobs.get_job
    persisted_update_job = optimizer_jobs.update_job
    persisted_claim = optimizer_jobs.claim_initial_worker_launch

    monkeypatch.setattr(optimizer_jobs, "get_job", lambda job_id: persisted_get_job(job_id, path=path))
    monkeypatch.setattr(
        optimizer_jobs,
        "claim_initial_worker_launch",
        lambda job_id, token: persisted_claim(job_id, token, path=path),
    )

    def update_in_test_db(job_id: str, **changes: object) -> dict[str, object]:
        changes.setdefault("path", path)
        return persisted_update_job(job_id, **changes)

    class FakeProcess:
        pid = 111

        @staticmethod
        def poll() -> None:
            return None

    monkeypatch.setattr(optimizer_jobs, "update_job", update_in_test_db)
    monkeypatch.setattr(optimizer_jobs, "job_evidence_dir", lambda _job_id: evidence)
    monkeypatch.setattr(optimizer_jobs.subprocess, "Popen", lambda *_a, **_k: FakeProcess())
    monkeypatch.setattr(optimizer_jobs, "_find_worker_by_token", lambda *_a, **_k: None)
    monkeypatch.setattr(optimizer_jobs, "WORKER_IDENTITY_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(optimizer_jobs.uuid, "uuid4", lambda: SimpleNamespace(hex="launch-token"))

    with pytest.raises(RuntimeError, match="OPTIMIZER_WORKER_LAUNCH_RECONCILIATION_REQUIRED"):
        optimizer_jobs._spawn_worker(job["job_id"], resume=False)

    current = get_job(job["job_id"], path=path)
    assert current is not None
    assert current["status"] == "RECONCILIATION_REQUIRED"
    assert current["active"] is True
    assert current["worker_pid"] is None
    assert current["first_blocker"] == "OPTIMIZER_WORKER_LAUNCH_IDENTITY_UNVERIFIED"


def test_stop_blocks_when_worker_is_gone_but_terminal_ownership_is_unproven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    upsert_round(
        job["job_id"],
        1,
        phase="LAUNCH_INTENT",
        state={"phase": "LAUNCH_INTENT", "ini_path": str(tmp_path / "owned.ini")},
        path=path,
    )
    update_job(
        job["job_id"],
        status="MT5_RUNNING",
        active=True,
        current_round=1,
        path=path,
    )

    persisted_get_job = optimizer_jobs.get_job
    persisted_get_round = optimizer_jobs.get_round
    persisted_update_job = optimizer_jobs.update_job
    monkeypatch.setattr(
        optimizer_jobs,
        "get_job",
        lambda job_id: persisted_get_job(job_id, path=path),
    )
    monkeypatch.setattr(
        optimizer_jobs,
        "get_round",
        lambda job_id, round_no: persisted_get_round(job_id, round_no, path=path),
    )

    def update_in_test_db(job_id: str, **changes: object) -> dict[str, object]:
        changes.setdefault("path", path)
        return persisted_update_job(job_id, **changes)

    monkeypatch.setattr(optimizer_jobs, "update_job", update_in_test_db)
    monkeypatch.setattr(optimizer_jobs, "_find_worker_by_token", lambda *_a, **_k: None)
    monkeypatch.setattr(
        optimizer_jobs,
        "optimizer_terminal_process_state",
        lambda *_args, **_kwargs: {"status": "UNRELATED_MT5_RUNNING", "processes": []},
    )

    with pytest.raises(RuntimeError, match="MT5 ownership is not proven"):
        optimizer_jobs.stop_optimizer(job["job_id"])

    current = get_job(job["job_id"], path=path)
    assert current is not None
    assert current["status"] == "MT5_RUNNING"
    assert current["active"] is True


def test_stop_terminates_only_the_uniquely_verified_optimizer_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    ini_path = str(tmp_path / "exact-run.ini")
    upsert_round(
        job["job_id"],
        1,
        phase="LAUNCH_INTENT",
        state={"phase": "LAUNCH_INTENT", "ini_path": ini_path},
        path=path,
    )
    update_job(
        job["job_id"],
        status="EXECUTION_UNCERTAIN",
        active=False,
        current_round=1,
        path=path,
    )
    persisted_get_job = optimizer_jobs.get_job
    persisted_get_round = optimizer_jobs.get_round
    persisted_update_job = optimizer_jobs.update_job
    monkeypatch.setattr(
        optimizer_jobs,
        "get_job",
        lambda job_id: persisted_get_job(job_id, path=path),
    )
    monkeypatch.setattr(
        optimizer_jobs,
        "get_round",
        lambda job_id, round_no: persisted_get_round(job_id, round_no, path=path),
    )

    def update_in_test_db(job_id: str, **changes: object) -> dict[str, object]:
        changes.setdefault("path", path)
        return persisted_update_job(job_id, **changes)

    monkeypatch.setattr(optimizer_jobs, "update_job", update_in_test_db)
    monkeypatch.setattr(optimizer_jobs, "_find_worker_by_token", lambda *_a, **_k: None)
    terminal_reads = iter([
        {"status": "OPTIMIZER_OWNED_RUNNING", "processes": [{"pid": 6123}]},
        {"status": "OPTIMIZER_OWNED_RUNNING", "processes": [{"pid": 6123}]},
        {"status": "NOT_RUNNING", "processes": []},
    ])
    monkeypatch.setattr(
        optimizer_jobs,
        "optimizer_terminal_process_state",
        lambda *_args, **_kwargs: next(terminal_reads),
    )
    stopped: list[list[str]] = []
    monkeypatch.setattr(
        optimizer_jobs.subprocess,
        "run",
        lambda command, **_kwargs: (stopped.append(command) or SimpleNamespace(returncode=0)),
    )

    result = optimizer_jobs.stop_optimizer(job["job_id"])

    current = get_job(job["job_id"], path=path)
    assert stopped == [["taskkill", "/PID", "6123", "/F"]]
    assert result["status"] == "STOPPED"
    assert current is not None
    assert current["active"] is False


def test_startup_process_query_failure_keeps_job_active_and_blocks_new_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    update_job(job["job_id"], worker_pid=4321, launch_token="launch-token", path=path)
    monkeypatch.setattr(
        optimizer_jobs,
        "_process_identity",
        lambda _pid: (_ for _ in ()).throw(RuntimeError("synthetic query failure")),
    )

    result = reconcile_optimizer_startup(path=path)

    current = get_job(job["job_id"], path=path)
    assert result == {
        "status": "BLOCKED",
        "job_id": job["job_id"],
        "action": "OPTIMIZER_PROCESS_OWNERSHIP_UNVERIFIED",
    }
    assert current is not None
    assert current["status"] == "RECONCILIATION_REQUIRED"
    assert current["active"] is True
    with pytest.raises(RuntimeError, match="already active"):
        create_job(
            {"max_rounds": 1},
            evidence_root=tmp_path / "artifacts" / "optimizer",
            path=path,
        )


def test_startup_does_not_treat_unverifiable_occupied_pid_as_worker_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    update_job(job["job_id"], worker_pid=4321, path=path)
    monkeypatch.setattr(
        optimizer_jobs,
        "_process_identity",
        lambda pid: {
            "pid": pid,
            "executable_path": r"C:\Python313\python.exe",
            "command_line": "python.exe process identity unavailable",
            "creation_time": "",
        },
    )

    result = reconcile_optimizer_startup(path=path)

    current = get_job(job["job_id"], path=path)
    assert result["status"] == "BLOCKED"
    assert result["action"] == "OPTIMIZER_PROCESS_IDENTITY_MISMATCH"
    assert current is not None
    assert current["status"] == "RECONCILIATION_REQUIRED"
    assert current["active"] is True


def test_startup_accepts_pid_reuse_only_when_persisted_creation_identity_differs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    executable = r"C:\Python313\python.exe"
    update_job(
        job["job_id"],
        status="STARTING",
        worker_pid=4321,
        launch_token="launch-token",
        worker_identity={
            "pid": 4321,
            "executable_path": executable,
            "command_line": "worker --job-id " + job["job_id"] + " --launch-token launch-token",
            "creation_time": "old-process",
        },
        path=path,
    )
    monkeypatch.setattr(
        optimizer_jobs,
        "_process_identity",
        lambda pid: {
            "pid": pid,
            "executable_path": executable,
            "command_line": "unrelated replacement process",
            "creation_time": "new-process",
        },
    )
    monkeypatch.setattr(optimizer_jobs, "_find_worker_by_token", lambda *_a, **_k: None)

    result = reconcile_optimizer_startup(path=path)

    current = get_job(job["job_id"], path=path)
    assert result["status"] == "INTERRUPTED_SAFE_TO_RESUME"
    assert current is not None
    assert current["active"] is False


def test_stopped_launch_token_cannot_enter_optimizer_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        optimizer_worker,
        "get_job",
        lambda _job_id: {
            "job_id": "job-1",
            "launch_token": "token-1",
            "active": False,
            "status": "STOPPED",
            "request": {},
        },
    )
    monkeypatch.setattr(
        optimizer_worker,
        "_load_committed_winner",
        lambda *_args: (_ for _ in ()).throw(AssertionError("worker continued after STOPPED")),
    )

    with pytest.raises(RuntimeError, match="OPTIMIZER_WORKER_LAUNCH_CLAIM_LOST"):
        optimizer_worker.run_job("job-1", launch_token="token-1")


def test_worker_waits_for_parent_to_persist_its_launch_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_pid = 9876
    job = {
        "job_id": "job-1",
        "launch_token": "token-1",
        "active": True,
        "status": "STARTING",
        "worker_pid": None,
        "request": {"max_rounds": 1},
    }
    reads = iter([dict(job), {**job, "worker_pid": worker_pid}])
    monkeypatch.setattr(optimizer_worker, "get_job", lambda _job_id: next(reads))
    monkeypatch.setattr(optimizer_worker.os, "getpid", lambda: worker_pid)

    result = optimizer_worker._await_worker_launch_confirmation(
        "job-1",
        "token-1",
        resume=False,
    )

    assert result["worker_pid"] == worker_pid


def test_worker_log_open_failure_does_not_leave_an_active_launch_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    blocked_evidence = tmp_path / "not-a-directory"
    blocked_evidence.write_text("synthetic", encoding="utf-8")
    persisted_get_job = optimizer_jobs.get_job
    persisted_claim = optimizer_jobs.claim_initial_worker_launch
    persisted_update = optimizer_jobs.update_job
    monkeypatch.setattr(
        optimizer_jobs,
        "get_job",
        lambda job_id: persisted_get_job(job_id, path=path),
    )
    monkeypatch.setattr(
        optimizer_jobs,
        "claim_initial_worker_launch",
        lambda job_id, token: persisted_claim(job_id, token, path=path),
    )

    def update_in_test_db(job_id: str, **changes: object) -> dict[str, object]:
        changes.setdefault("path", path)
        return persisted_update(job_id, **changes)

    monkeypatch.setattr(optimizer_jobs, "update_job", update_in_test_db)
    monkeypatch.setattr(
        optimizer_jobs,
        "job_evidence_dir",
        lambda _job_id: blocked_evidence,
    )

    with pytest.raises(RuntimeError, match="OPTIMIZER_WORKER_LOG_OPEN_FAILED"):
        optimizer_jobs._spawn_worker(job["job_id"], resume=False)

    current = get_job(job["job_id"], path=path)
    assert current is not None
    assert current["status"] == "FAILED"
    assert current["active"] is False
    assert current["first_blocker"] == "OPTIMIZER_WORKER_LOG_OPEN_FAILED"


def test_stop_finds_and_stops_worker_by_launch_token_when_pid_was_not_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_db(tmp_path)
    job = create_job(
        {"max_rounds": 1},
        evidence_root=tmp_path / "artifacts" / "optimizer",
        path=path,
    )
    identity = {
        "pid": 7654,
        "executable_path": r"C:\Python313\python.exe",
        "command_line": (
            r"C:\Python313\python.exe -m max_backend.optimizer_worker --job-id "
            + job["job_id"]
            + " --launch-token token-1"
        ),
        "creation_time": "created",
    }
    update_job(
        job["job_id"],
        status="STARTING",
        active=True,
        launch_token="token-1",
        path=path,
    )
    persisted_get_job = optimizer_jobs.get_job
    persisted_update_job = optimizer_jobs.update_job
    monkeypatch.setattr(
        optimizer_jobs,
        "get_job",
        lambda job_id: persisted_get_job(job_id, path=path),
    )

    def update_in_test_db(job_id: str, **changes: object) -> dict[str, object]:
        changes.setdefault("path", path)
        return persisted_update_job(job_id, **changes)

    monkeypatch.setattr(optimizer_jobs, "update_job", update_in_test_db)
    monkeypatch.setattr(optimizer_jobs, "_process_identity", lambda _pid: identity)
    monkeypatch.setattr(optimizer_jobs, "_find_worker_by_token", lambda *_a, **_k: identity)
    stopped: list[list[str]] = []
    identities = iter([identity, identity, None])
    monkeypatch.setattr(optimizer_jobs, "_process_identity", lambda _pid: next(identities))
    monkeypatch.setattr(
        optimizer_jobs.subprocess,
        "run",
        lambda command, **_kwargs: (stopped.append(command) or SimpleNamespace(returncode=0)),
    )

    result = optimizer_jobs.stop_optimizer(job["job_id"])

    current = get_job(job["job_id"], path=path)
    assert stopped == [["taskkill", "/PID", "7654", "/F"]]
    assert result["status"] == "STOPPED"
    assert current is not None
    assert current["active"] is False
