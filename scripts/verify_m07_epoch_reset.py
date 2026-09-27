from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from max_backend.config import DATABASE_PATH
from max_backend.ea import verify_baseline_snapshot
from max_backend.mtf_geometry import STRATEGY_CONTRACT

sys.path.insert(0, str(ROOT / "scripts"))
from reset_strategy_epoch import (  # noqa: E402
    ACCEPTED_PREVIOUS_EPOCH_SHA,
    inspect_state,
    settings_fingerprint,
    sha256_file,
)


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise VerificationError(f"INVALID_JSON:{path}") from exc
    require(isinstance(value, dict), f"JSON_OBJECT_REQUIRED:{path}")
    return value


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def verify_historical_git_evidence() -> dict[str, Any]:
    protected = [
        "evidence/m00",
        "evidence/m01",
        "evidence/m02",
        "evidence/m03",
        "evidence/m04",
        "evidence/m05",
        "evidence/m06",
        "artifacts/strategy_challengers",
        "artifacts/strategy_history",
    ]
    diff = git(
        "diff",
        "--name-only",
        ACCEPTED_PREVIOUS_EPOCH_SHA,
        "--",
        *protected,
    )
    require(diff.returncode == 0, "HISTORICAL_EVIDENCE_DIFF_FAILED")
    changed = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
    require(not changed, f"HISTORICAL_EVIDENCE_CHANGED:{changed}")
    return {
        "accepted_source_sha": ACCEPTED_PREVIOUS_EPOCH_SHA,
        "protected_paths": protected,
        "changed_paths": changed,
        "status": "UNCHANGED",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset-manifest",
        default="evidence/m07/epoch_reset/reset_manifest.json",
    )
    args = parser.parse_args()

    baseline = verify_baseline_snapshot()
    require(
        baseline.get("strategy_contract") == STRATEGY_CONTRACT,
        "BASELINE_STRATEGY_CONTRACT_INVALID",
    )
    require(baseline.get("ea_version") == "2.10", "BASELINE_VERSION_NOT_2_10")

    reset_manifest_path = ROOT / args.reset_manifest
    require(reset_manifest_path.is_file(), "RESET_MANIFEST_MISSING")
    reset = load(reset_manifest_path)
    require(
        reset.get("previous_accepted_sha") == ACCEPTED_PREVIOUS_EPOCH_SHA,
        "RESET_SOURCE_ACCEPTED_SHA_MISMATCH",
    )
    backup = reset.get("backup")
    require(isinstance(backup, dict), "RESET_BACKUP_MISSING")
    backup_path = ROOT / str(backup.get("path"))
    require(backup_path.is_file(), "RESET_DATABASE_BACKUP_MISSING")
    require(
        sha256_file(backup_path) == str(backup.get("sha256")),
        "RESET_DATABASE_BACKUP_SHA_MISMATCH",
    )

    state = inspect_state(DATABASE_PATH)
    counts = state["counts"]
    for key in (
        "optimizer_jobs",
        "challengers",
        "backtests",
        "retirements",
        "promotions",
        "champions",
        "scientist_threads",
    ):
        require(int(counts[key]) == 0, f"POST_RESET_NONZERO:{key}:{counts[key]}")
    require(state["current_champion"] is None, "POST_RESET_CURRENT_CHAMPION_EXISTS")
    baseline_row = state["baseline"]
    require(isinstance(baseline_row, dict), "POST_RESET_BASELINE_ROW_MISSING")
    require(
        baseline_row["sha256"] == baseline["snapshot_sha256"],
        "POST_RESET_BASELINE_SHA_MISMATCH",
    )
    require(
        baseline_row["status"] == "BASELINE_NOT_CHAMPION",
        "POST_RESET_BASELINE_STATUS_INVALID",
    )
    require(baseline_row["ea_version"] == "2.10", "POST_RESET_BASELINE_VERSION_INVALID")

    expected_settings = reset.get("provider_settings_fingerprint")
    require(
        isinstance(expected_settings, dict),
        "RESET_PROVIDER_SETTINGS_FINGERPRINT_MISSING",
    )
    require(
        settings_fingerprint() == expected_settings,
        "PROVIDER_SETTINGS_CHANGED_AFTER_RESET",
    )

    runtime = reset.get("runtime_authority_archive")
    require(isinstance(runtime, list), "RUNTIME_AUTHORITY_ARCHIVE_MISSING")
    for item in runtime:
        require(isinstance(item, dict), "RUNTIME_ARCHIVE_ENTRY_INVALID")
        if item.get("exists"):
            archived = (
                reset_manifest_path.parent
                / "previous_champion_runtime"
                / str(item.get("archive_file"))
            )
            require(archived.is_file(), f"RUNTIME_ARCHIVE_FILE_MISSING:{item.get('key')}")
            require(
                sha256_file(archived) == str(item.get("sha256")),
                f"RUNTIME_ARCHIVE_HASH_MISMATCH:{item.get('key')}",
            )
            original = Path(str(item.get("original_path")))
            require(
                not original.is_file(),
                f"OLD_RUNTIME_AUTHORITY_STILL_PRESENT:{item.get('key')}",
            )

    historical = verify_historical_git_evidence()
    result = {
        "status": "PASS",
        "strategy_contract": STRATEGY_CONTRACT,
        "baseline_sha256": baseline["snapshot_sha256"],
        "baseline_version": baseline["ea_version"],
        "database": {
            "current_champion_count": counts["champions"],
            "challenger_count": counts["challengers"],
            "optimizer_job_count": counts["optimizer_jobs"],
            "backtest_count": counts["backtests"],
            "retirement_count": counts["retirements"],
            "promotion_count": counts["promotions"],
        },
        "backup": backup,
        "historical_evidence": historical,
        "provider_settings": "UNCHANGED",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"M07 EPOCH RESET VERIFIER: FAIL: {exc}")
        raise SystemExit(1)
