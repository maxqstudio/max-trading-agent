from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = ROOT / "evidence" / "m06" / "acceptance.json"
EXPECTED_BASE = "0e7b41c2be9ead1e25d3b0b23138a32acfdb24d1"
OLD_ACCEPTED_M03_JOB = "20260922_120735_07989e09"


class EvidenceError(RuntimeError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise EvidenceError(message)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise EvidenceError(f"Invalid JSON: {path}") from exc
    require(isinstance(value, dict), f"Expected JSON object: {path}")
    return value


def git(*args: str, binary: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not binary,
        check=False,
    )


def rel(value: str | Path) -> str:
    text = str(value).replace("\\", "/").strip()
    require(bool(text), "Empty repo-relative path")
    require(not text.startswith("/"), f"Absolute path not allowed: {text}")
    require(":" not in text.split("/")[0], f"Drive path not allowed: {text}")
    path = Path(text)
    require(".." not in path.parts, f"Parent traversal not allowed: {text}")
    return path.as_posix()


def assert_file(
    value: str | Path,
    *,
    expected_sha: str | None = None,
    git_ref: str | None = None,
) -> Path:
    name = rel(value)
    path = ROOT / name
    require(path.is_file(), f"Missing file: {name}")
    ignored = git("check-ignore", "--no-index", "-q", "--", name)
    require(ignored.returncode == 1, f"Retained file ignored: {name}")
    tracked = git("ls-files", "--error-unmatch", "--", name)
    require(tracked.returncode == 0, f"Retained file untracked: {name}")
    if expected_sha:
        require(sha(path) == expected_sha.lower(), f"SHA mismatch: {name}")
    if git_ref:
        require(
            git("cat-file", "-e", f"{git_ref}:{name}").returncode == 0,
            f"Missing Git object: {git_ref}:{name}",
        )
        blob = git("show", f"{git_ref}:{name}", binary=True)
        require(blob.returncode == 0, f"Cannot read Git object: {name}")
        require(
            hashlib.sha256(blob.stdout).hexdigest() == sha(path),
            f"Worktree/Git object mismatch: {name}",
        )
    return path


def assert_tree(value: str | Path, *, git_ref: str | None = None) -> Path:
    name = rel(value).rstrip("/")
    root = ROOT / name
    require(root.is_dir(), f"Missing tree: {name}")
    require(
        bool(git("ls-files", "--", f"{name}/").stdout.strip()),
        f"Tree has no tracked files: {name}",
    )
    if git_ref:
        require(
            git("cat-file", "-e", f"{git_ref}:{name}").returncode == 0,
            f"Missing Git tree: {git_ref}:{name}",
        )
    return root


def verify_challenger_manifest(
    bundle_root: str,
    *,
    challenger_id: str,
    git_ref: str | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    root = assert_tree(bundle_root, git_ref=git_ref)
    manifest_path = assert_file(
        f"{rel(bundle_root)}/manifest.json",
        git_ref=git_ref,
    )
    manifest = load(manifest_path)
    require(
        manifest.get("schema") == "MAX_REBUILD_STRATEGY_CHALLENGER_MANIFEST_V1",
        "Wrong Challenger manifest schema",
    )
    require(
        manifest.get("challenger_id") == challenger_id,
        "Challenger manifest identity mismatch",
    )

    entries: dict[str, str] = {}
    for item in manifest.get("files", []):
        require(isinstance(item, dict), "Invalid Challenger manifest entry")
        item_rel = rel(str(item.get("path") or ""))
        require(item_rel != "manifest.json", "Manifest cannot hash itself")
        full_rel = f"{rel(bundle_root)}/{item_rel}"
        digest = str(item.get("sha256") or "").lower()
        path = assert_file(full_rel, expected_sha=digest, git_ref=git_ref)
        require(
            path.stat().st_size == int(item.get("size", -1)),
            f"Challenger manifest size mismatch: {item_rel}",
        )
        entries[item_rel] = digest

    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    require(actual == set(entries), "Challenger manifest file-set mismatch")
    return manifest, entries


def require_same_contract(backtest: dict[str, Any], source: dict[str, Any]) -> None:
    request = backtest["request"]
    require(
        request.get("contract_authority") == "RETAINED_SOURCE_REQUEST",
        "Backtest contract is not retained-source authoritative",
    )
    for key in (
        "symbol",
        "relative_symbol",
        "period",
        "from_date",
        "to_date",
        "model",
        "deposit",
        "leverage",
    ):
        left = request.get(key)
        right = source.get(key)
        if key in {"model", "leverage"}:
            require(int(left) == int(right), f"Backtest override detected: {key}")
        elif key == "deposit":
            require(float(left) == float(right), f"Backtest override detected: {key}")
        else:
            require(str(left) == str(right), f"Backtest override detected: {key}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--git-ref")
    args = parser.parse_args()
    git_ref = args.git_ref

    acceptance_path = assert_file("evidence/m06/acceptance.json", git_ref=git_ref)
    acceptance = load(acceptance_path)
    require(
        acceptance.get("schema") == "MAX_REBUILD_M06_ACCEPTANCE_V2",
        "Wrong M06 acceptance schema",
    )
    require(acceptance.get("status") == "PASS", "M06 acceptance is not PASS")
    require(
        acceptance.get("base_commit") == EXPECTED_BASE,
        "M06 base commit mismatch",
    )

    lifecycle = acceptance.get("lifecycle")
    evidence = acceptance.get("evidence")
    require(isinstance(lifecycle, dict), "M06 lifecycle block missing")
    require(isinstance(evidence, dict), "M06 evidence block missing")

    optimizer_job_id = str(lifecycle.get("optimizer_job_id") or "")
    challenger_id = str(lifecycle.get("challenger_id") or "")
    backtest_id = str(lifecycle.get("backtest_id") or "")
    retirement_id = str(lifecycle.get("retirement_id") or "")
    require(optimizer_job_id, "Optimizer job identity missing")
    require(
        optimizer_job_id != OLD_ACCEPTED_M03_JOB,
        "M06 reused the old accepted M03 optimizer job",
    )
    require(challenger_id.startswith("STRAT-"), "Challenger identity missing")
    require(backtest_id.startswith("BT-"), "Backtest identity missing")
    require(retirement_id.startswith("RETIRE-"), "Retirement identity missing")

    optimizer_request_path = assert_file(
        evidence["optimizer_request"],
        git_ref=git_ref,
    )
    optimizer_winner_path = assert_file(
        evidence["optimizer_winner"],
        git_ref=git_ref,
    )
    optimizer_snapshot_path = assert_file(
        evidence["optimizer_snapshot"],
        git_ref=git_ref,
    )
    db_snapshot_path = assert_file(
        evidence["database_snapshot"],
        git_ref=git_ref,
    )
    recovery_probe_path = assert_file(
        evidence["recovery_probe"],
        git_ref=git_ref,
    )
    race_probe_path = assert_file(
        evidence["race_probe"],
        git_ref=git_ref,
    )

    optimizer_request = load(optimizer_request_path)
    optimizer_winner = load(optimizer_winner_path)
    optimizer_snapshot = load(optimizer_snapshot_path)
    db_snapshot = load(db_snapshot_path)
    recovery_probe = load(recovery_probe_path)
    race_probe = load(race_probe_path)

    require(
        optimizer_request.get("schema") == "MAX_REBUILD_OPTIMIZER_REQUEST_V2",
        "Unexpected optimizer request schema",
    )
    require(
        int(optimizer_request.get("optimization", 1)) == 1,
        "M06 lifecycle proof did not use optimization",
    )
    require(
        optimizer_snapshot.get("job_id") == optimizer_job_id,
        "Optimizer snapshot job mismatch",
    )
    require(
        optimizer_snapshot.get("terminal_result") == "STRATEGY_CHALLENGER_FOUND",
        "Optimizer did not terminate with a real Strategy Challenger",
    )
    require(
        optimizer_snapshot.get("status") in {
            "ELIGIBLE_WINNER",
            "STRATEGY_CHALLENGER_FOUND",
        },
        "Optimizer job did not retain eligible-winner authority",
    )
    require(
        optimizer_winner.get("job_id") == optimizer_job_id,
        "Eligible winner job mismatch",
    )
    require(
        int(optimizer_winner.get("round", 0)) >= 1,
        "Eligible winner round missing",
    )
    require(
        int(optimizer_winner.get("mt5_pass", 0)) >= 0,
        "Eligible winner MT5 pass missing",
    )

    bundle_root = rel(evidence["challenger_bundle_root"])
    _manifest, _entries = verify_challenger_manifest(
        bundle_root,
        challenger_id=challenger_id,
        git_ref=git_ref,
    )
    challenger_meta = load(assert_file(
        f"{bundle_root}/challenger.json",
        git_ref=git_ref,
    ))
    require(
        challenger_meta.get("challenger_id") == challenger_id,
        "Challenger metadata identity mismatch",
    )
    require(
        challenger_meta.get("status") == "CHALLENGER",
        "Immutable Challenger metadata status changed",
    )
    source = challenger_meta.get("source")
    require(isinstance(source, dict), "Challenger source metadata missing")
    require(
        source.get("optimizer_job") == optimizer_job_id,
        "Challenger did not originate from the new optimizer job",
    )
    require(
        int(source.get("round", 0)) == int(optimizer_winner.get("round", -1)),
        "Challenger source round mismatch",
    )
    require(
        int(source.get("mt5_pass", -1))
        == int(optimizer_winner.get("mt5_pass", -2)),
        "Challenger source pass mismatch",
    )

    source_request_path = assert_file(
        f"{bundle_root}/source/request.json",
        git_ref=git_ref,
    )
    source_request = load(source_request_path)
    require(
        source_request.get("schema") == "MAX_REBUILD_OPTIMIZER_REQUEST_V2",
        "Retained Challenger source request schema mismatch",
    )

    backtest_root = rel(evidence["backtest_root"])
    assert_tree(backtest_root, git_ref=git_ref)
    backtest_request = load(assert_file(
        f"{backtest_root}/request.json",
        git_ref=git_ref,
    ))
    backtest_result = load(assert_file(
        f"{backtest_root}/result.json",
        git_ref=git_ref,
    ))
    compile_result = load(assert_file(
        f"{backtest_root}/compile.json",
        git_ref=git_ref,
    ))
    retained_ea = assert_file(
        f"{backtest_root}/retained_challenger.mq5",
        expected_sha=str(backtest_result["retained_ea_sha256"]),
        git_ref=git_ref,
    )
    retained_set = assert_file(
        f"{backtest_root}/retained_challenger.set",
        expected_sha=str(backtest_result["retained_set_sha256"]),
        git_ref=git_ref,
    )
    compiled_ex5 = assert_file(
        f"{backtest_root}/compiled_challenger.ex5",
        expected_sha=str(backtest_result["compiled_ex5_sha256"]),
        git_ref=git_ref,
    )
    require(retained_ea.stat().st_size > 0, "Retained Challenger MQ5 is empty")
    require(retained_set.stat().st_size > 0, "Retained Challenger SET is empty")
    require(compiled_ex5.stat().st_size > 0, "Compiled Challenger EX5 is empty")
    require(compile_result.get("status") == "PASS", "MetaEditor compile not PASS")
    summary = compile_result.get("compile_summary") or {}
    require(summary.get("found") is True, "MetaEditor compile summary missing")
    require(int(summary.get("errors", -1)) == 0, "MetaEditor compile errors present")
    require(int(summary.get("warnings", -1)) == 0, "MetaEditor compile warnings present")
    require(
        compile_result.get("ex5_sha256") == backtest_result.get("compiled_ex5_sha256"),
        "Compile/result EX5 SHA mismatch",
    )
    backtest_report = assert_file(
        f"{backtest_root}/{backtest_result['report_file']}",
        expected_sha=str(backtest_result["report_sha256"]),
        git_ref=git_ref,
    )
    require(backtest_result.get("backtest_id") == backtest_id, "Backtest ID mismatch")
    require(
        backtest_result.get("challenger_id") == challenger_id,
        "Backtest Challenger mismatch",
    )
    require(
        backtest_result.get("status") == "COMPLETED",
        "Real M06 backtest not COMPLETED",
    )
    require(
        backtest_result.get("execution_truth") == "MT5_STRATEGY_TESTER",
        "Backtest execution truth is not MT5 Strategy Tester",
    )
    require(
        backtest_result.get("parameter_mutation") == "NONE",
        "Backtest mutated Strategy parameters",
    )
    require(
        int(backtest_result.get("scientist_calls", -1)) == 0,
        "Backtest invoked Scientist",
    )
    require(
        backtest_result.get("live_authority") == "NONE",
        "Backtest introduced Live authority",
    )
    require(
        int(backtest_result.get("report_size", -1)) == backtest_report.stat().st_size,
        "Backtest report size mismatch",
    )
    require_same_contract({"request": backtest_request}, source_request)

    retirement_root = rel(evidence["retirement_root"])
    assert_tree(retirement_root, git_ref=git_ref)
    retirement_request = load(assert_file(
        f"{retirement_root}/request.json",
        git_ref=git_ref,
    ))
    retirement_result = load(assert_file(
        f"{retirement_root}/retirement.json",
        git_ref=git_ref,
    ))
    require(
        retirement_request.get("confirmation")
        == "OWNER_EXPLICIT_RETIREMENT_CONFIRMATION",
        "Retirement explicit confirmation missing",
    )
    require(
        retirement_request.get("retirement_id") == retirement_id,
        "Retirement request ID mismatch",
    )
    require(
        retirement_result.get("retirement_id") == retirement_id,
        "Retirement result ID mismatch",
    )
    require(
        retirement_result.get("challenger_id") == challenger_id,
        "Retirement Challenger mismatch",
    )
    require(
        retirement_result.get("status") == "RETIRED",
        "Retirement did not end RETIRED",
    )
    require(
        retirement_result.get("retirement_state") == "COMMITTED",
        "Retirement journal not COMMITTED",
    )
    require(
        retirement_result.get("retirement_before_status") == "CHALLENGER",
        "Retirement before_status mismatch",
    )
    require(
        retirement_result.get("retirement_after_status") == "RETIRED",
        "Retirement after_status mismatch",
    )
    require(
        retirement_result.get("bundle_preserved") is True,
        "Retirement did not preserve bundle",
    )

    require(
        db_snapshot.get("schema") == "MAX_REBUILD_M06_DATABASE_SNAPSHOT_V1",
        "Database snapshot schema mismatch",
    )
    require(int(db_snapshot.get("schema_version", 0)) == 7, "M06 schema is not 7")
    champion_before = db_snapshot.get("champion_before")
    champion_after = db_snapshot.get("champion_after")
    require(
        champion_before == champion_after,
        "Current Strategy Champion changed during M06 lifecycle acceptance",
    )
    require(
        db_snapshot.get("challenger", {}).get("challenger_id") == challenger_id,
        "Database Challenger identity mismatch",
    )
    require(
        db_snapshot.get("challenger", {}).get("status") == "RETIRED",
        "Database Challenger not RETIRED",
    )
    require(
        db_snapshot.get("backtest", {}).get("backtest_id") == backtest_id,
        "Database backtest identity mismatch",
    )
    require(
        db_snapshot.get("backtest", {}).get("state") == "COMPLETED",
        "Database backtest not COMPLETED",
    )
    journal = db_snapshot.get("retirement")
    require(isinstance(journal, dict), "Retirement journal snapshot missing")
    require(journal.get("retirement_id") == retirement_id, "Journal ID mismatch")
    require(journal.get("state") == "COMMITTED", "Journal state mismatch")
    require(journal.get("before_status") == "CHALLENGER", "Journal before_status mismatch")
    require(journal.get("after_status") == "RETIRED", "Journal after_status mismatch")
    require(
        journal.get("expected_manifest_sha256")
        == backtest_result.get("source_manifest_sha256"),
        "Journal/backtest manifest authority mismatch",
    )

    require(
        recovery_probe.get("schema") == "MAX_REBUILD_M06_RECOVERY_PROBE_V1",
        "Recovery probe schema mismatch",
    )
    require(
        recovery_probe.get("running_before_restart") == "RUNNING",
        "Recovery probe did not start RUNNING",
    )
    require(
        recovery_probe.get("running_after_restart") == "UNCONFIRMED",
        "RUNNING restart did not become UNCONFIRMED",
    )
    require(
        int(recovery_probe.get("automatic_relaunch_count", -1)) == 0,
        "Restart recovery automatically relaunched MT5",
    )

    require(
        race_probe.get("schema") == "MAX_REBUILD_M06_RACE_PROBE_V1",
        "Race probe schema mismatch",
    )
    for key in (
        "retirement_blocked_active_backtest",
        "retirement_blocked_active_promotion",
        "promotion_blocked_after_retirement",
        "backtest_blocked_after_retirement",
    ):
        require(race_probe.get(key) is True, f"Race-safety proof failed: {key}")

    print("M06 EVIDENCE VERIFIER: PASS")
    print(f"optimizer_job={optimizer_job_id}")
    print(f"challenger={challenger_id}")
    print(f"backtest={backtest_id}")
    print(f"retirement={retirement_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EvidenceError as exc:
        print(f"M06 EVIDENCE VERIFIER: FAIL: {exc}")
        raise SystemExit(1)
