"""Local-only MT5 account binding and snapshot tamper evidence.

An HMAC prevents accidental or unauthorized modifications *without the local
secret*. It is not remote exchange/broker attestation: whoever controls this
machine or key can forge data. No raw broker login/server is persisted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any

KEY_BYTES = 32
SCHEME = "LOCAL_MT5_HMAC_V2"
DEFAULT_KEY_PATH = Path(".local/mt5_identity.key")


class IdentityError(ValueError):
    """Always a bounded error code; never includes private data."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _key_bytes(key: bytes) -> bytes:
    if not isinstance(key, bytes) or len(key) != KEY_BYTES:
        raise IdentityError("INVALID_IDENTITY_KEY")
    return key


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError):
        raise IdentityError("INVALID_SNAPSHOT_CONTENT") from None


def load_or_create_key(path: Path = DEFAULT_KEY_PATH) -> bytes:
    """Atomic local key creation; reject symlinks, weak UNIX ACL and corruption.

    Windows file ownership/ACL privacy depends on the selected directory;
    this does not claim OS-level credential-vault protection.
    """
    path = Path(path)
    if path.is_symlink():
        raise IdentityError("IDENTITY_KEY_SYMLINK")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            fd = None
            try:
                fd = os.open(path, flags, 0o600)
                with os.fdopen(fd, "wb", closefd=True) as stream:
                    fd = None
                    stream.write(secrets.token_bytes(KEY_BYTES))
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                # Another process created the key. Read and validate it.
                pass
            finally:
                if fd is not None:
                    os.close(fd)
        if path.is_symlink():
            raise IdentityError("IDENTITY_KEY_SYMLINK")
        flags = os.O_RDONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise IdentityError("IDENTITY_KEY_NOT_REGULAR")
            if os.name == "posix" and (info.st_mode & 0o077):
                raise IdentityError("IDENTITY_KEY_PERMISSIONS")
            key = stream.read(KEY_BYTES + 1)
        return _key_bytes(key)
    except IdentityError:
        raise
    except OSError:
        raise IdentityError("IDENTITY_KEY_UNAVAILABLE") from None


def _account_fingerprint(key: bytes, login: Any, server: Any) -> str:
    if isinstance(login, bool) or not isinstance(login, int) or login <= 0:
        raise IdentityError("ACCOUNT_ID_UNAVAILABLE")
    if not isinstance(server, str) or not 1 <= len(server) <= 256 or not server.strip():
        raise IdentityError("ACCOUNT_ID_UNAVAILABLE")
    return hmac.new(key, b"mt5-account-v1:" + _canonical(
        {"login": login, "server": server}
    ), hashlib.sha256).hexdigest()


def seal_snapshot(snapshot: dict, key: bytes, *, login: int,
                  server: str) -> dict:
    """Called ONLY after an MT5 local adapter read; never by the LLM."""
    key = _key_bytes(key)
    if not isinstance(snapshot, dict) or "provenance" in snapshot:
        raise IdentityError("UNSUPPORTED_SNAPSHOT")
    account_id = _account_fingerprint(key, login, server)
    if snapshot.get("source") != "MT5_LOCAL_TERMINAL" or (
        snapshot.get("mode") != "READ_ONLY" or
        snapshot.get("execution") != "DISABLED"
    ):
        raise IdentityError("UNSUPPORTED_SNAPSHOT")
    # Bind the opaque account ID to the snapshot MAC. In V1 the account
    # ID sat outside the MAC and could be substituted without the secret.
    digest = hmac.new(key, b"mt5-snapshot-v2:" + _canonical({
        "account_binding_id": account_id, "snapshot": snapshot,
    }), hashlib.sha256).hexdigest()
    return {**snapshot, "provenance": {
        "scheme": SCHEME, "account_binding_id": account_id,
        "snapshot_mac": digest,
    }}


def verify_snapshot(snapshot: Any, key: bytes) -> str:
    """Return opaque identity iff both local MAC and schema are valid."""
    key = _key_bytes(key)
    if not isinstance(snapshot, dict):
        raise IdentityError("UNSUPPORTED_SNAPSHOT")
    provenance = snapshot.get("provenance")
    if not isinstance(provenance, dict) or set(provenance) != {
        "scheme", "account_binding_id", "snapshot_mac"
    } or provenance.get("scheme") != SCHEME:
        raise IdentityError("SNAPSHOT_PROVENANCE_MISSING")
    account_id = provenance.get("account_binding_id")
    mac = provenance.get("snapshot_mac")
    if not isinstance(account_id, str) or not isinstance(mac, str) or (
        len(account_id) != 64 or len(mac) != 64 or
        any(c not in "0123456789abcdef" for c in account_id + mac)
    ):
        raise IdentityError("INVALID_SNAPSHOT_MAC")
    if snapshot.get("source") != "MT5_LOCAL_TERMINAL" or (
        snapshot.get("mode") != "READ_ONLY" or
        snapshot.get("execution") != "DISABLED"
    ):
        raise IdentityError("UNSUPPORTED_SNAPSHOT")
    content = {k: v for k, v in snapshot.items() if k != "provenance"}
    expected = hmac.new(key, b"mt5-snapshot-v2:" + _canonical({
        "account_binding_id": account_id, "snapshot": content,
    }), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        raise IdentityError("SNAPSHOT_MAC_MISMATCH")
    return account_id
