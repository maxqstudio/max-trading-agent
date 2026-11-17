"""G31: offline owner Windows MT5 preflight; never opens a terminal or trades.

This checks only LOCAL prerequisites for manually running the existing M09
read-only feed acceptance. GitHub CI must never present this as a live-terminal
acceptance result. No key, account, broker, trading API or network operations.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REQUIRED = (
    "max_trading_agent/terminal_acceptance.py",
    "max_trading_agent/mt5_readonly.py",
    "max_trading_agent/identity.py",
    "docs/G31_OWNER_MT5_PREFLIGHT.md",
    ".github/workflows/g31-postmerge-main.yml",
)
_GIT_TIMEOUT = 8


def _git(repo: Path, *args: str) -> str | None:
    """Read local Git metadata only; no fetch/pull/login or remote API calls."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, check=False,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or len(result.stdout) > 100_000:
        return None
    return result.stdout.strip()


def _mt5_package_present() -> bool:
    """Read installed distribution metadata; DO NOT import MetaTrader5."""
    try:
        return bool(importlib.metadata.version("MetaTrader5"))
    except (importlib.metadata.PackageNotFoundError, OSError, ValueError):
        return False


def inspect_owner_environment(repo: Path, *,
                              os_name: str | None = None,
                              package_check: Any = None) -> dict:
    """Inspect environment without altering any file or contacting MT5."""
    repo = Path(repo)
    system = platform.system() if os_name is None else os_name
    if package_check is None:
        package_check = _mt5_package_present
    # No output of local file path, broker, environment, account or secrets.
    is_repo = repo.is_dir() and not repo.is_symlink()
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD") if is_repo else None
    head = _git(repo, "rev-parse", "--verify", "HEAD") if is_repo else None
    origin = (_git(repo, "rev-parse", "--verify", "refs/remotes/origin/main")
              if is_repo else None)
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=normal") if is_repo else None
    # Cached remote refs might be stale: this does not prove GitHub current main!
    markers = bool(is_repo and all((repo / name).is_file() and not
                                   (repo / name).is_symlink()
                                   for name in _REQUIRED))
    try:
        present = package_check() is True
    except Exception:
        present = False

    checks = {
        "windows_host": system == "Windows",
        "python_supported": sys.version_info >= (3, 10),
        "valid_non_symlink_repository": bool(is_repo and
            head is not None and _SHA.fullmatch(head)),
        "branch_is_main": branch == "main",
        "working_tree_clean": dirty == "",
        "local_origin_main_matches_head": (
            type(head) is str and type(origin) is str
            and _SHA.fullmatch(head) is not None
            and _SHA.fullmatch(origin) is not None and head == origin
        ),
        "merged_acceptance_files_present": markers,
        "metatrader5_distribution_installed": present,
    }
    all_present = all(checks.values())
    return {
        "schema_version": 1,
        "status": ("LOCAL_PREREQUISITES_PRESENT_LIVE_TEST_PENDING"
                   if all_present else "LOCAL_PREFLIGHT_NOT_READY"),
        "scope": "OWNER_WINDOWS_OFFLINE_CHECK_ONLY",
        "git_head_sha": head if type(head) is str and _SHA.fullmatch(head) else None,
        "checks": checks,
        "blockers": [name.upper() for name, ok in checks.items() if not ok],
        "origin_main_is_cached_local_ref": True,
        "actual_remote_main_verified": False,
        "mt5_python_module_imported": False,
        "mt5_terminal_initialized": False,
        "terminal_feed_tested": False,
        "broker_connected": False,
        "identity_key_created": False,
        "github_postmerge_ci_verified": False,
        "owner_acceptance": "PENDING",
        "broker_execution": False,
        "eligible_for_demo_or_live": False,
        "execution": "DISABLED",
        "decision": "NO_TRADE",
        "next_action": (
            "OWNER_MAY_RUN_EXPLICIT_M09_READONLY_ACCEPTANCE"
            if all_present else "FIX_LOCAL_BLOCKERS_BEFORE_M09"
        ),
        "limitations": (
            "Installed Python metadata and a locally cached origin/main SHA "
            "do not prove a connected terminal, verified live broker feed, "
            "current remote main, signed release or trading permission."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="owner-mt5-readonly-preflight")
    parser.add_argument("--repo", default=None,
                        help="Repository path; defaults to this script's repository")
    args = parser.parse_args()
    try:
        path = Path(args.repo) if args.repo else Path(__file__).resolve().parents[1]
        report = inspect_owner_environment(path)
    except (OSError, ValueError, TypeError):
        raise SystemExit("OWNER_MT5_PREFLIGHT_BLOCKED") from None
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "LOCAL_PREREQUISITES_PRESENT_LIVE_TEST_PENDING":
        raise SystemExit(3)


if __name__ == "__main__":
    main()
