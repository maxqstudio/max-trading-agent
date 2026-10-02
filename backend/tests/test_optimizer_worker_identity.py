from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

import max_backend.optimizer_jobs as optimizer_jobs
from max_backend.optimizer_worker_identity import (
    WORKER_IDENTITY_MODULE,
    WORKER_IDENTITY_PROTOCOL,
    WorkerIdentityServer,
    _canonical_json,
    get_process_identity,
    terminate_process_verified,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows named-pipe worker ownership contract")


def _identity(*, pid: int = 100, job_id: str = "job-1", token: str = "token-1") -> dict[str, Any]:
    executable = r"C:\Python313\python.exe"
    return {
        "pid": pid,
        "job_id": job_id,
        "launch_token": token,
        "worker_module": WORKER_IDENTITY_MODULE,
        "worker_executable_path": executable,
        "identity_protocol": WORKER_IDENTITY_PROTOCOL,
        "startup_identity": "a" * 64,
        "executable_path": executable,
        "creation_time": "01dd5271f359352f",
    }


def test_worker_identity_requires_exact_job_token_pid_path_and_creation_identity() -> None:
    saved = _identity()
    current = {
        "pid": saved["pid"],
        "executable_path": saved["executable_path"],
        "creation_time": saved["creation_time"],
    }
    assert optimizer_jobs._worker_identity_matches(
        saved,
        job_id="job-1",
        launch_token="token-1",
        expected=None,
    )
    assert optimizer_jobs._worker_identity_matches(
        current,
        job_id="job-1",
        launch_token="token-1",
        expected=saved,
    )

    for field, value in (
        ("job_id", "job-10"),
        ("launch_token", "other-token"),
        ("pid", 101),
        ("executable_path", r"C:\Other\python.exe"),
        ("creation_time", "01dd5271f3593530"),
        ("worker_module", "unrelated.module"),
        ("identity_protocol", "unknown"),
        ("startup_identity", ""),
    ):
        tampered = {**saved, field: value}
        assert not optimizer_jobs._worker_identity_matches(
            current,
            job_id="job-1",
            launch_token="token-1",
            expected=tampered,
        ), field


def test_worker_identity_rejects_pid_reuse_and_unbound_native_process() -> None:
    saved = _identity()
    changed_creation = {
        "pid": saved["pid"],
        "executable_path": saved["executable_path"],
        "creation_time": "01dd5271f3593530",
    }
    assert not optimizer_jobs._worker_identity_matches(
        changed_creation,
        job_id="job-1",
        launch_token="token-1",
        expected=saved,
    )
    assert not optimizer_jobs._worker_identity_matches(
        {"pid": 100, "executable_path": saved["executable_path"], "creation_time": saved["creation_time"]},
        job_id="job-1",
        launch_token="token-1",
        expected=None,
    )


def _handshake_script(*, tamper: str = "") -> str:
    return f"""
import hashlib, hmac, json, os, sys, time
from multiprocessing.connection import Client
from max_backend.optimizer_worker_identity import _canonical_json, WORKER_IDENTITY_MODULE
address, job_id, token = sys.argv[1:4]
conn = Client(address)
challenge = conn.recv_bytes(64)
identity = {{
    'identity_version': 1,
    'job_id': job_id,
    'launch_token': token,
    'pid': os.getpid(),
    'python_executable_path': os.path.abspath(sys.executable),
    'worker_module': WORKER_IDENTITY_MODULE,
    'startup_identity': challenge.hex(),
}}
if {tamper!r} == 'pid':
    identity['pid'] += 1000
if {tamper!r} == 'job':
    identity['job_id'] += '-substituted'
proof = hmac.new(token.encode(), challenge + _canonical_json(identity), hashlib.sha256).hexdigest()
if {tamper!r} == 'proof':
    proof = '0' * 64
conn.send_bytes(_canonical_json({{'identity': identity, 'proof': proof}}))
time.sleep(0.5)
conn.close()
"""


def _spawn_handshake_client(
    server: WorkerIdentityServer,
    *,
    job_id: str = "job-1",
    token: str = "token-1",
    tamper: str = "",
) -> subprocess.Popen[Any]:
    code = _handshake_script(tamper=tamper)
    return subprocess.Popen(
        [sys.executable, "-c", code, server.address, job_id, token],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


@WINDOWS_ONLY
@pytest.mark.parametrize(
    ("job_id", "token", "tamper", "expected_error"),
    [
        ("job-1", "wrong-token", "", "OPTIMIZER_WORKER_IDENTITY_BINDING_MISMATCH"),
        ("wrong-job", "token-1", "", "OPTIMIZER_WORKER_IDENTITY_BINDING_MISMATCH"),
        ("job-1", "token-1", "job", "OPTIMIZER_WORKER_IDENTITY_BINDING_MISMATCH"),
        ("job-1", "token-1", "pid", "OPTIMIZER_WORKER_IDENTITY_PID_MISMATCH"),
        ("job-1", "token-1", "proof", "OPTIMIZER_WORKER_IDENTITY_PROOF_MISMATCH"),
    ],
)
def test_windows_handshake_rejects_wrong_job_token_pid_and_proof(
    job_id: str,
    token: str,
    tamper: str,
    expected_error: str,
) -> None:
    server = WorkerIdentityServer()
    process = _spawn_handshake_client(server, job_id=job_id, token=token, tamper=tamper)
    try:
        with pytest.raises(RuntimeError, match=expected_error):
            server.receive(
                job_id="job-1",
                launch_token="token-1",
                expected_executable=sys.executable,
                timeout_seconds=5,
            )
    finally:
        server.close()
        process.wait(timeout=5)


@WINDOWS_ONLY
def test_windows_handshake_rejects_unexpected_worker_executable() -> None:
    server = WorkerIdentityServer()
    process = _spawn_handshake_client(server)
    try:
        with pytest.raises(RuntimeError, match="OPTIMIZER_WORKER_IDENTITY_EXECUTABLE_MISMATCH"):
            server.receive(
                job_id="job-1",
                launch_token="token-1",
                expected_executable=str(Path(sys.executable).with_name("different-python.exe")),
                timeout_seconds=5,
            )
    finally:
        server.close()
        process.wait(timeout=5)


@WINDOWS_ONLY
def test_windows_handshake_missing_or_early_exit_worker_fails_closed() -> None:
    server = WorkerIdentityServer()
    try:
        with pytest.raises(TimeoutError, match="OPTIMIZER_WORKER_IDENTITY_CONNECT_TIMEOUT"):
            server.receive(
                job_id="missing-job",
                launch_token="missing-token",
                expected_executable=sys.executable,
                timeout_seconds=0.05,
            )
    finally:
        server.close()

    server = WorkerIdentityServer()
    process = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        assert process.wait(timeout=5) is not None
        with pytest.raises(TimeoutError, match="OPTIMIZER_WORKER_IDENTITY_CONNECT_TIMEOUT"):
            server.receive(
                job_id="exited-job",
                launch_token="exited-token",
                expected_executable=sys.executable,
                timeout_seconds=0.05,
            )
    finally:
        server.close()


@WINDOWS_ONLY
def test_windows_venv_worker_identity_is_repeatedly_bound_to_real_pipe_peer(tmp_path: Path) -> None:
    venv = tmp_path / "worker-identity-venv"
    created = subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert created.returncode == 0, created.stderr
    launcher = venv / "Scripts" / "python.exe"
    assert launcher.is_file()
    successful_iterations = 0
    child_code = (
        "import sys,time; from max_backend.optimizer_worker_identity import send_worker_identity; "
        "send_worker_identity(sys.argv[1], job_id=sys.argv[2], launch_token=sys.argv[3]); time.sleep(30)"
    )

    for iteration in range(5):
        server = WorkerIdentityServer()
        job_id = f"venv-worker-{iteration}"
        token = f"token-{os.urandom(12).hex()}"
        ambiguous_candidates = [
            subprocess.Popen(
                [
                    str(launcher),
                    "-c",
                    "import time; time.sleep(1)",
                    WORKER_IDENTITY_MODULE,
                    "--job-id",
                    job_id,
                    "--launch-token",
                    token,
                ],
                cwd=REPO_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for _ in range(2)
        ]
        child = subprocess.Popen(
            [str(launcher), "-c", child_code, server.address, job_id, token],
            cwd=REPO_ROOT,
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    item
                    for item in (
                        str(REPO_ROOT / "backend"),
                        os.environ.get("PYTHONPATH", ""),
                    )
                    if item
                ),
            },
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        identity: dict[str, Any] | None = None
        try:
            identity = server.receive(
                job_id=job_id,
                launch_token=token,
                expected_executable=str(launcher),
                timeout_seconds=10,
            )
            assert identity["pid"] != child.pid
            assert all(identity["pid"] != candidate.pid for candidate in ambiguous_candidates)
            assert identity["job_id"] == job_id
            assert identity["launch_token"] == token
            assert identity["worker_module"] == WORKER_IDENTITY_MODULE
            assert identity["identity_protocol"] == WORKER_IDENTITY_PROTOCOL
            current = get_process_identity(int(identity["pid"]))
            assert current is not None
            assert optimizer_jobs._worker_identity_matches(
                current,
                job_id=job_id,
                launch_token=token,
                expected=identity,
            )

            reused_pid = {**identity, "creation_time": "0000000000000000"}
            with pytest.raises(RuntimeError, match="OPTIMIZER_WORKER_PROCESS_IDENTITY_CHANGED"):
                terminate_process_verified(int(identity["pid"]), expected=reused_pid, timeout_seconds=3)
            assert get_process_identity(int(identity["pid"])) is not None

            assert terminate_process_verified(
                int(identity["pid"]),
                expected=identity,
                timeout_seconds=5,
            )
            child.wait(timeout=5)
            successful_iterations += 1
        finally:
            server.close()
            if child.poll() is None and identity is not None:
                with suppress(RuntimeError, OSError, ProcessLookupError):
                    terminate_process_verified(int(identity["pid"]), expected=identity, timeout_seconds=3)
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            for candidate in ambiguous_candidates:
                if candidate.poll() is None:
                    candidate.terminate()
                    candidate.wait(timeout=5)

    assert successful_iterations == 5


@WINDOWS_ONLY
def test_windows_worker_identity_channel_does_not_enumerate_other_processes() -> None:
    server = WorkerIdentityServer()
    source = Path(optimizer_jobs.__file__).read_text(encoding="utf-8").casefold()
    identity_source = Path(__file__).parents[1] / "max_backend" / "optimizer_worker_identity.py"
    native_source = identity_source.read_text(encoding="utf-8").casefold()
    forbidden = ("powershell.exe", "get-ciminstance", "win32_process", "wmi", "cim")
    try:
        assert not any(item in native_source for item in forbidden)
        assert "_find_worker_by_token" not in source
        assert "_select_worker_candidate" not in source
        assert "powershell.exe" not in source
        assert "get-ciminstance" not in source
        assert "win32_process" not in source
    finally:
        server.close()
