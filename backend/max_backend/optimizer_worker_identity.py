from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import secrets
import signal
import sys
import time
import uuid
from contextlib import suppress
from multiprocessing import connection as multiprocessing_connection
from multiprocessing.connection import Listener
from pathlib import Path
from typing import Any

WORKER_IDENTITY_PROTOCOL = "named-pipe-hmac-v1"
WORKER_IDENTITY_MODULE = "max_backend.optimizer_worker"
WORKER_IDENTITY_TIMEOUT_SECONDS = 12.0
MAX_IDENTITY_HANDSHAKE_BYTES = 4096


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def send_worker_identity(address: str, *, job_id: str, launch_token: str) -> None:
    """Prove this worker's launch claim over its parent's one-use channel."""
    connection = multiprocessing_connection.Client(address)
    try:
        if not connection.poll(WORKER_IDENTITY_TIMEOUT_SECONDS):
            raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_CHALLENGE_TIMEOUT")
        challenge = connection.recv_bytes(64)
        if len(challenge) != 32:
            raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_CHALLENGE_INVALID")
        identity = {
            "identity_version": 1,
            "job_id": str(job_id),
            "launch_token": str(launch_token),
            "pid": int(os.getpid()),
            "python_executable_path": os.path.abspath(sys.executable),
            "worker_module": WORKER_IDENTITY_MODULE,
            "startup_identity": challenge.hex(),
        }
        proof = hmac.new(
            str(launch_token).encode("utf-8"),
            challenge + _canonical_json(identity),
            hashlib.sha256,
        ).hexdigest()
        connection.send_bytes(_canonical_json({"identity": identity, "proof": proof}))
    finally:
        connection.close()


def _windows_api() -> Any:
    if os.name != "nt":
        raise RuntimeError("OPTIMIZER_WINDOWS_PROCESS_API_UNAVAILABLE")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.GetNamedPipeClientProcessId.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel32.GetNamedPipeClientProcessId.restype = ctypes.c_int
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.TerminateProcess.restype = ctypes.c_int
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]


def _windows_process_identity(handle: int, pid: int, kernel32: Any) -> dict[str, Any] | None:
    wait_result = kernel32.WaitForSingleObject(handle, 0)
    if wait_result == 0:
        return None
    if wait_result != 258:
        raise OSError(ctypes.get_last_error(), "Unable to inspect Optimizer worker process state")

    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.c_ulong(len(buffer))
    if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        raise OSError(ctypes.get_last_error(), "Unable to query Optimizer worker executable")
    created = _FileTime()
    exited = _FileTime()
    kernel = _FileTime()
    user = _FileTime()
    if not kernel32.GetProcessTimes(
        handle,
        ctypes.byref(created),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        raise OSError(ctypes.get_last_error(), "Unable to query Optimizer worker creation time")
    creation_ticks = (int(created.high) << 32) | int(created.low)
    return {
        "pid": int(pid),
        "executable_path": buffer.value,
        "creation_time": f"{creation_ticks:016x}",
    }


def get_process_identity(pid: int) -> dict[str, Any] | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        kernel32 = _windows_api()
        process_query_limited_information = 0x1000
        synchronize = 0x00100000
        handle = kernel32.OpenProcess(
            process_query_limited_information | synchronize,
            False,
            int(pid),
        )
        if not handle:
            error = ctypes.get_last_error()
            if error in {87, 1168}:
                return None
            raise OSError(error, "Unable to open Optimizer worker process")
        try:
            return _windows_process_identity(handle, int(pid), kernel32)
        finally:
            kernel32.CloseHandle(handle)

    proc = Path(f"/proc/{int(pid)}")
    if not proc.is_dir():
        return None
    try:
        executable = str(proc.joinpath("exe").resolve())
        stat_fields = proc.joinpath("stat").read_text(encoding="ascii").split(")", 1)[1].split()
        creation_time = stat_fields[19]
    except (OSError, IndexError) as exc:
        if not proc.exists():
            return None
        raise RuntimeError("OPTIMIZER_PROCESS_IDENTITY_QUERY_FAILED") from exc
    return {
        "pid": int(pid),
        "executable_path": executable,
        "creation_time": creation_time,
    }


def _same_os_process(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    if int(actual.get("pid") or 0) != int(expected.get("pid") or 0):
        return False
    expected_path = str(expected.get("executable_path") or "")
    actual_path = str(actual.get("executable_path") or "")
    if not expected_path or not actual_path or os.path.normcase(os.path.abspath(expected_path)) != os.path.normcase(
        os.path.abspath(actual_path)
    ):
        return False
    expected_creation = str(expected.get("creation_time") or "")
    actual_creation = str(actual.get("creation_time") or "")
    return bool(expected_creation and actual_creation and expected_creation == actual_creation)


def terminate_process_verified(
    pid: int,
    *,
    expected: dict[str, Any],
    timeout_seconds: float = 15.0,
) -> bool:
    """Terminate only the exact process object represented by the saved identity."""
    if pid <= 0 or int(expected.get("pid") or 0) != int(pid):
        raise RuntimeError("OPTIMIZER_WORKER_PROCESS_IDENTITY_MISMATCH")
    if os.name == "nt":
        kernel32 = _windows_api()
        process_query_limited_information = 0x1000
        process_terminate = 0x0001
        synchronize = 0x00100000
        handle = kernel32.OpenProcess(
            process_query_limited_information | process_terminate | synchronize,
            False,
            int(pid),
        )
        if not handle:
            error = ctypes.get_last_error()
            if error in {87, 1168}:
                return False
            raise OSError(error, "Unable to open verified Optimizer worker for termination")
        try:
            actual = _windows_process_identity(handle, int(pid), kernel32)
            if actual is None:
                return False
            if not _same_os_process(actual, expected):
                raise RuntimeError("OPTIMIZER_WORKER_PROCESS_IDENTITY_CHANGED")
            if not kernel32.TerminateProcess(handle, 1):
                if kernel32.WaitForSingleObject(handle, 0) == 0:
                    return False
                raise OSError(ctypes.get_last_error(), "Unable to terminate verified Optimizer worker")
            wait_ms = max(1, min(int(timeout_seconds * 1000), 0xFFFFFFFE))
            if kernel32.WaitForSingleObject(handle, wait_ms) != 0:
                raise RuntimeError("OPTIMIZER_VERIFIED_WORKER_STOP_TIMEOUT")
            return True
        finally:
            kernel32.CloseHandle(handle)

    actual = get_process_identity(int(pid))
    if actual is None:
        return False
    if not _same_os_process(actual, expected):
        raise RuntimeError("OPTIMIZER_WORKER_PROCESS_IDENTITY_CHANGED")
    os.kill(int(pid), signal.SIGTERM)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = get_process_identity(int(pid))
        if current is None or not _same_os_process(current, expected):
            return True
        time.sleep(0.05)
    raise RuntimeError("OPTIMIZER_VERIFIED_WORKER_STOP_TIMEOUT")


class WorkerIdentityServer:
    """One-use authenticated launch channel bound to the actual pipe peer PID."""

    def __init__(self) -> None:
        self._winapi: Any = None
        self._pipe_handle: int | None = None
        self._listener: Any = None
        if os.name == "nt":
            import _winapi

            self._winapi = _winapi
            self.address = rf"\\.\pipe\max-optimizer-worker-{uuid.uuid4().hex}"
            open_mode = (
                _winapi.PIPE_ACCESS_DUPLEX
                | _winapi.FILE_FLAG_OVERLAPPED
                | _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
            )
            pipe_mode = _winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE | _winapi.PIPE_WAIT
            self._pipe_handle = _winapi.CreateNamedPipe(
                self.address,
                open_mode,
                pipe_mode,
                1,
                65536,
                65536,
                0,
                _winapi.NULL,
            )
        else:
            self._listener = Listener(address=None, family="AF_UNIX")
            self.address = str(self._listener.address)

    def _accept_windows(self, timeout_seconds: float) -> Any:
        if self._pipe_handle is None or self._winapi is None:
            raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_CHANNEL_CLOSED")
        handle = self._pipe_handle
        connected = False
        overlapped = None
        try:
            try:
                overlapped = self._winapi.ConnectNamedPipe(handle, overlapped=True)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 535:
                    connected = True
                else:
                    raise
            if not connected:
                timeout_ms = max(0, min(int(timeout_seconds * 1000), 0xFFFFFFFE))
                wait_result = self._winapi.WaitForMultipleObjects(
                    [overlapped.event],
                    False,
                    timeout_ms,
                )
                if wait_result == 258:
                    with suppress(OSError):
                        overlapped.cancel()
                    with suppress(OSError):
                        overlapped.GetOverlappedResult(True)
                    raise TimeoutError("OPTIMIZER_WORKER_IDENTITY_CONNECT_TIMEOUT")
                if wait_result != 0:
                    raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_CONNECT_FAILED")
                _, error = overlapped.GetOverlappedResult(True)
                if error:
                    raise OSError(error, "Unable to accept Optimizer worker identity pipe")
            self._pipe_handle = None
            return multiprocessing_connection.PipeConnection(handle)
        except BaseException:
            if self._pipe_handle is not None:
                with suppress(OSError):
                    self._winapi.CloseHandle(handle)
                self._pipe_handle = None
            raise

    def receive(
        self,
        *,
        job_id: str,
        launch_token: str,
        expected_executable: str,
        expected_pid: int | None = None,
        timeout_seconds: float = WORKER_IDENTITY_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        if os.name == "nt":
            connection = self._accept_windows(timeout_seconds)
        else:
            if self._listener is None:
                raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_CHANNEL_CLOSED")
            self._listener._listener._socket.settimeout(timeout_seconds)
            try:
                connection = self._listener.accept()
            except OSError as exc:
                raise TimeoutError("OPTIMIZER_WORKER_IDENTITY_CONNECT_TIMEOUT") from exc

        try:
            challenge = secrets.token_bytes(32)
            connection.send_bytes(challenge)
            remaining = max(0.0, deadline - time.monotonic())
            if not connection.poll(remaining):
                raise TimeoutError("OPTIMIZER_WORKER_IDENTITY_RESPONSE_TIMEOUT")
            try:
                envelope = json.loads(connection.recv_bytes(MAX_IDENTITY_HANDSHAKE_BYTES).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, OSError, ValueError) as exc:
                raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_RESPONSE_INVALID") from exc
            if not isinstance(envelope, dict) or set(envelope) != {"identity", "proof"}:
                raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_RESPONSE_INVALID")
            identity = envelope.get("identity")
            proof = envelope.get("proof")
            required = {
                "identity_version",
                "job_id",
                "launch_token",
                "pid",
                "python_executable_path",
                "worker_module",
                "startup_identity",
            }
            if not isinstance(identity, dict) or set(identity) != required:
                raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_RESPONSE_INVALID")
            if (
                type(identity.get("identity_version")) is not int
                or identity.get("identity_version") != 1
                or identity.get("job_id") != str(job_id)
                or identity.get("launch_token") != str(launch_token)
                or identity.get("worker_module") != WORKER_IDENTITY_MODULE
                or identity.get("startup_identity") != challenge.hex()
                or type(identity.get("pid")) is not int
                or identity["pid"] <= 0
                or not isinstance(identity.get("python_executable_path"), str)
                or not identity.get("python_executable_path")
            ):
                raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_BINDING_MISMATCH")
            try:
                expected_path = os.path.normcase(os.path.abspath(expected_executable))
                reported_path = os.path.normcase(os.path.abspath(identity["python_executable_path"]))
            except (TypeError, ValueError, OSError) as exc:
                raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_EXECUTABLE_MISMATCH") from exc
            if reported_path != expected_path:
                raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_EXECUTABLE_MISMATCH")
            proof_expected = hmac.new(
                str(launch_token).encode("utf-8"),
                challenge + _canonical_json(identity),
                hashlib.sha256,
            ).hexdigest()
            if not isinstance(proof, str) or not hmac.compare_digest(proof, proof_expected):
                raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_PROOF_MISMATCH")

            if os.name == "nt":
                kernel32 = _windows_api()
                peer_pid = ctypes.c_ulong()
                if not kernel32.GetNamedPipeClientProcessId(
                    connection.fileno(),
                    ctypes.byref(peer_pid),
                ):
                    raise OSError(ctypes.get_last_error(), "Unable to read worker pipe peer PID")
                actual_pid = int(peer_pid.value)
            else:
                actual_pid = int(expected_pid or 0)
            if actual_pid <= 0 or actual_pid != identity["pid"]:
                raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_PID_MISMATCH")

            process_identity = get_process_identity(actual_pid)
            if process_identity is None:
                raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_PROCESS_EXITED")
            executable_name = Path(str(process_identity.get("executable_path") or "")).name.casefold()
            if executable_name not in {"python.exe", "pythonw.exe", "python", "pythonw"}:
                raise RuntimeError("OPTIMIZER_WORKER_IDENTITY_PROCESS_IMAGE_MISMATCH")
            return {
                **process_identity,
                "identity_protocol": WORKER_IDENTITY_PROTOCOL,
                "job_id": str(job_id),
                "launch_token": str(launch_token),
                "worker_module": WORKER_IDENTITY_MODULE,
                "worker_executable_path": identity["python_executable_path"],
                "startup_identity": identity["startup_identity"],
            }
        finally:
            connection.close()

    def close(self) -> None:
        if self._pipe_handle is not None and self._winapi is not None:
            with suppress(OSError):
                self._winapi.CloseHandle(self._pipe_handle)
            self._pipe_handle = None
        if self._listener is not None:
            self._listener.close()
            self._listener = None
