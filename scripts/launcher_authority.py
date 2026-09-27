from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from max_backend.schema import CURRENT_SCHEMA_VERSION
from max_backend.config import EA_VERSION, PROJECT_NAME
from max_backend.ea import verify_baseline_snapshot

EXPECTED_BASELINE_STATUS = "BASELINE_NOT_CHAMPION"
EXPECTED_MT5_STATUS = "READY_EXECUTABLE_AND_DATA_ROOT"


def expected_authority() -> dict[str, Any]:
    manifest = verify_baseline_snapshot()
    baseline_status = str(manifest.get("baseline_status") or "")
    if baseline_status != EXPECTED_BASELINE_STATUS:
        raise RuntimeError("LAUNCHER_BASELINE_STATUS_AUTHORITY_INVALID")
    return {
        "project": PROJECT_NAME,
        "schema_version": int(CURRENT_SCHEMA_VERSION),
        "ea_version": EA_VERSION,
        "ea_sha256": str(manifest["snapshot_sha256"]),
        "baseline_status": baseline_status,
        "strategy_contract": str(manifest["strategy_contract"]),
    }


def overview_problem(
    overview: Any,
    *,
    authority: dict[str, Any] | None = None,
) -> str | None:
    expected = authority or expected_authority()
    if not isinstance(overview, dict):
        return "overview endpoint unavailable"
    if overview.get("project") != expected["project"]:
        return "unexpected project identity"
    backend = overview.get("backend")
    if not isinstance(backend, dict) or backend.get("status") != "READY":
        return "backend status is not READY"

    database = overview.get("database")
    if not isinstance(database, dict) or database.get("status") != "READY":
        return "SQLite status is not READY"
    try:
        schema_version = int(database.get("schema_version"))
    except (TypeError, ValueError):
        return "SQLite schema version is invalid"
    if schema_version != int(expected["schema_version"]):
        return f"SQLite schema version is not {expected['schema_version']}"

    baseline = overview.get("ea_baseline")
    if not isinstance(baseline, dict):
        return "EA baseline is unavailable"
    if baseline.get("ea_version") != expected["ea_version"]:
        return "EA baseline version mismatch"
    if baseline.get("sha256") != expected["ea_sha256"]:
        return "EA baseline SHA-256 mismatch"
    if baseline.get("status") != expected["baseline_status"]:
        return "EA baseline status is invalid"

    champion = overview.get("current_strategy_champion")
    if champion is not None:
        if not isinstance(champion, dict):
            return "Strategy Champion payload is invalid"
        if not str(champion.get("strategy_id") or "").strip():
            return "Strategy Champion identity is invalid"
        if champion.get("status") != "CURRENT":
            return "Strategy Champion status is not CURRENT"

    mt5 = overview.get("mt5")
    if not isinstance(mt5, dict) or mt5.get("status") != EXPECTED_MT5_STATUS:
        reason = mt5.get("reason") if isinstance(mt5, dict) else "UNKNOWN"
        return f"MT5 preflight is not READY: {reason}"
    return None


def scientist_problem(status: Any) -> str | None:
    if not isinstance(status, dict):
        return "Scientist status endpoint unavailable"
    if status.get("status") != "READY":
        return "Scientist status is not READY"
    if status.get("knowledge_status") != "READY":
        return f"Scientist knowledge is not READY: {status.get('knowledge_status')}"
    if status.get("read_only") is not True:
        return "Scientist read-only contract is not active"
    return None


def _identity_projection(overview: dict[str, Any]) -> dict[str, Any]:
    database = overview.get("database") or {}
    baseline = overview.get("ea_baseline") or {}
    champion = overview.get("current_strategy_champion")
    mt5 = overview.get("mt5") or {}
    return {
        "project": overview.get("project"),
        "backend_status": (overview.get("backend") or {}).get("status"),
        "database_status": database.get("status"),
        "schema_version": database.get("schema_version"),
        "ea_version": baseline.get("ea_version"),
        "ea_sha256": baseline.get("sha256"),
        "baseline_status": baseline.get("status"),
        "champion": champion,
        "mt5_status": mt5.get("status"),
    }


def proxy_match_problem(backend: Any, proxy: Any) -> str | None:
    if not isinstance(backend, dict) or not isinstance(proxy, dict):
        return "backend/proxy overview unavailable"
    if _identity_projection(backend) != _identity_projection(proxy):
        return "frontend proxy overview does not match backend authority"
    return None


def _read_stdin_json() -> Any:
    raw = sys.stdin.read()
    if not raw.strip():
        return None
    return json.loads(raw)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 1 or args[0] not in {"expected", "overview", "scientist", "match"}:
        print("usage: launcher_authority.py expected|overview|scientist|match")
        return 2
    mode = args[0]
    try:
        if mode == "expected":
            print(json.dumps(expected_authority(), sort_keys=True))
            return 0
        payload = _read_stdin_json()
        if mode == "overview":
            problem = overview_problem(payload)
        elif mode == "scientist":
            problem = scientist_problem(payload)
        else:
            if not isinstance(payload, dict):
                problem = "backend/proxy overview unavailable"
            else:
                problem = proxy_match_problem(payload.get("backend"), payload.get("proxy"))
    except Exception as exc:
        print(f"launcher authority unavailable: {exc}")
        return 3
    if problem is not None:
        print(problem)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
