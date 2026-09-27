from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = ROOT / "evidence" / "m03" / "acceptance.json"
EXPECTED_BASE = "1165effe013dd1cc561875bf38444ea59e3d04b7"
EXPECTED_JOB = "20260922_120735_07989e09"
EXPECTED_CHALLENGER = "STRAT-20260922-120735-R01-P11"
EXPECTED_EA = "9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345"
EXPECTED_XML = "c269a31fdabc2d4f4adac010894a96ea344927ba015b55861ae0bd8cde5fe30f"
EXPECTED_SIDECAR = "3f0fcc5e2307f1fef7e7d9f99b3e63507cf924fa4978f0be0626286af48e2629"
EXPECTED_WINNER_PASS = 11
EXPECTED_ELIGIBLE = 13
PARAM_COUNT = 16


class EvidenceError(RuntimeError):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise EvidenceError(f"Invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"Expected JSON object: {path}")
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


def rel(value: str) -> str:
    text = str(value).replace("\\", "/").strip()
    if not text or text.startswith("/") or ":" in text.split("/")[0]:
        raise EvidenceError(f"Not repo relative: {value}")
    return Path(text).as_posix()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise EvidenceError(message)


def assert_file(value: str, *, expected_sha: str | None = None, git_ref: str | None = None) -> Path:
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
        require(git("cat-file", "-e", f"{git_ref}:{name}").returncode == 0, f"Missing Git object: {git_ref}:{name}")
        if expected_sha:
            blob = git("show", f"{git_ref}:{name}", binary=True)
            require(blob.returncode == 0, f"Cannot read Git object: {name}")
            require(hashlib.sha256(blob.stdout).hexdigest() == expected_sha.lower(), f"Git object SHA mismatch: {name}")
    return path


def assert_tree(value: str, *, git_ref: str | None = None) -> None:
    name = rel(value).rstrip("/")
    require((ROOT / name).is_dir(), f"Missing tree: {name}")
    require(bool(git("ls-files", "--", f"{name}/").stdout.strip()), f"Tree has no tracked files: {name}")
    if git_ref:
        require(git("cat-file", "-e", f"{git_ref}:{name}").returncode == 0, f"Missing Git tree: {git_ref}:{name}")


def check_manifest(manifest_rel: str, *, git_ref: str | None) -> dict[str, str]:
    manifest_path = assert_file(manifest_rel, git_ref=git_ref)
    manifest = load(manifest_path)
    root_rel = rel(str(manifest["root"]))
    assert_tree(root_rel, git_ref=git_ref)
    entries: dict[str, str] = {}
    for item in manifest.get("files", []):
        item_rel = rel(f"{root_rel}/{item['path']}")
        digest = str(item["sha256"]).lower()
        assert_file(item_rel, expected_sha=digest, git_ref=git_ref)
        entries[item_rel] = digest
    require(len(entries) == int(manifest.get("file_count", -1)), f"Manifest count mismatch: {manifest_rel}")
    actual = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / root_rel).rglob("*")
        if path.is_file() and path.resolve() != manifest_path.resolve()
    }
    require(
        actual == set(entries),
        f"Manifest file-set mismatch: {manifest_rel}",
    )
    return entries


def ranking_key(row: dict[str, Any]) -> tuple[float, float, float, float, int]:
    return (
        -float(row["weighted_r"]),
        -float(row["expectancy_r"]),
        -float(row["profit_factor"]),
        -float(row["recovery_factor"]),
        int(row["pass_no"]),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--git-ref")
    args = parser.parse_args()

    acceptance = load(ACCEPTANCE)
    if args.git_ref:
        assert_file(
            "evidence/m03/acceptance.json",
            git_ref=args.git_ref,
        )
    require(acceptance.get("schema") == "MAX_REBUILD_M03_ACCEPTANCE_V1", "Wrong acceptance schema")
    require(acceptance.get("status") == "PASS", "M03 acceptance is not PASS")
    require(acceptance.get("base_commit") == EXPECTED_BASE, "Wrong M03 base commit")

    evidence = acceptance["evidence"]
    real_files = check_manifest(evidence["real_manifest"], git_ref=args.git_ref)
    verification_files = check_manifest(evidence["verification_manifest"], git_ref=args.git_ref)

    job = acceptance["real_winner_job"]
    require(job["job_id"] == EXPECTED_JOB, "Unexpected real winner job")
    require(job["scientist"] == "OFF", "M03 real winner must retain Scientist OFF authority")
    require(job["terminal_result"] == "STRATEGY_CHALLENGER_FOUND", "Unexpected terminal result")

    retained = Path(evidence["real_root"])
    request_path = ROOT / retained / EXPECTED_JOB / "optimizer" / "request.json"
    compile_result_path = ROOT / retained / EXPECTED_JOB / "optimizer" / "compile_result.json"
    assert_file(request_path.relative_to(ROOT).as_posix(), git_ref=args.git_ref)
    assert_file(compile_result_path.relative_to(ROOT).as_posix(), git_ref=args.git_ref)
    request = load(request_path)
    require(request["scientist_assist"] is False, "Real M03 acceptance unexpectedly used Scientist")
    expected_gates = {
        "min_profit_factor": 1.0,
        "min_recovery_factor": 0.0,
        "min_expectancy_r": 0.0,
        "min_weighted_r": 0.0,
    }
    for key, expected in expected_gates.items():
        require(
            float(request["kpi"][key]) == expected,
            f"Real M03 acceptance gate changed: {key}",
        )
    compile_result = load(compile_result_path)
    require(compile_result["status"] == "PASS", "Real MetaEditor compile is not PASS")
    require(
        compile_result["compile_summary"]["line"] == "Result: 0 errors, 0 warnings",
        "Real MetaEditor compile summary changed",
    )
    require(
        compile_result["deployed_sha256"] == EXPECTED_EA,
        "Deployed MT5 EA SHA changed",
    )

    winning = acceptance["winning_round"]
    require(int(winning["round"]) == 1, "Unexpected winning round")
    require(int(winning["pass"]) == EXPECTED_WINNER_PASS, "Unexpected winning pass")
    require(winning["xml_sha256"] == EXPECTED_XML, "Unexpected winning XML SHA")
    require(winning["sidecar_sha256"] == EXPECTED_SIDECAR, "Unexpected winning sidecar SHA")
    require(int(winning["eligible_passes"]) == EXPECTED_ELIGIBLE, "Unexpected eligible count")

    passes_path = ROOT / retained / EXPECTED_JOB / "optimizer" / "round_01" / "passes.json"
    audit_path = ROOT / retained / EXPECTED_JOB / "optimizer" / "round_01" / "eligibility_audit.json"
    winner_path = ROOT / retained / EXPECTED_JOB / "optimizer" / "eligible_winner.json"
    xml_path = ROOT / retained / EXPECTED_JOB / "optimizer" / "round_01" / "Max_MTF.xml"
    sidecar_path = ROOT / retained / EXPECTED_JOB / "optimizer" / "round_01" / "Max_MTF_metrics.csv"
    for path in (passes_path, audit_path, winner_path, xml_path, sidecar_path):
        assert_file(path.relative_to(ROOT).as_posix(), git_ref=args.git_ref)

    require(sha(xml_path) == EXPECTED_XML, "Retained XML changed")
    require(sha(sidecar_path) == EXPECTED_SIDECAR, "Retained sidecar changed")
    passes = load(passes_path).get("passes")
    require(isinstance(passes, list) and len(passes) == 13, "Unexpected pass evidence")
    eligible = [row for row in passes if row.get("eligibility") == "ELIGIBLE"]
    require(len(eligible) == EXPECTED_ELIGIBLE, "Eligible pass replay count mismatch")
    selected = sorted(eligible, key=ranking_key)[0]
    require(int(selected["pass_no"]) == EXPECTED_WINNER_PASS, "Deterministic ranking replay mismatch")

    winner = load(winner_path)
    require(int(winner["mt5_pass"]) == EXPECTED_WINNER_PASS, "Winner evidence pass mismatch")
    require(winner["source_report_sha256"] == EXPECTED_XML, "Winner XML provenance mismatch")
    require(winner["sidecar_sha256"] == EXPECTED_SIDECAR, "Winner sidecar provenance mismatch")
    require(len(winner["params"]) == PARAM_COUNT, "Winner param count mismatch")
    audit = load(audit_path)
    require(int(audit["eligible_passes"]) == EXPECTED_ELIGIBLE, "Eligibility audit count mismatch")
    require(int(audit["winner_pass"]) == EXPECTED_WINNER_PASS, "Eligibility audit winner mismatch")

    challenger = acceptance["challenger"]
    require(challenger["challenger_id"] == EXPECTED_CHALLENGER, "Unexpected Challenger ID")
    require(challenger["status"] == "CHALLENGER", "Challenger not active")
    require(challenger["role_origin"] == "OPTIMIZER_WINNER", "Wrong Challenger origin")
    require(int(challenger["rows_for_source_winner"]) == 1, "Source winner has duplicate Challengers")

    bundle_root = rel(challenger["bundle_root"])
    assert_tree(bundle_root, git_ref=args.git_ref)
    bundle_manifest_rel = f"{bundle_root}/manifest.json"
    bundle_manifest_path = assert_file(
        bundle_manifest_rel,
        expected_sha=challenger["manifest_sha256"],
        git_ref=args.git_ref,
    )
    bundle_manifest = load(bundle_manifest_path)
    challenger_metadata = load(ROOT / bundle_root / "challenger.json")
    require(
        challenger_metadata["champion"]["before"] is None
        and challenger_metadata["champion"]["after"] is None
        and challenger_metadata["champion"]["mutation"] == "NONE",
        "Challenger bundle claims Champion mutation",
    )
    for item in bundle_manifest["files"]:
        assert_file(
            f"{bundle_root}/{item['path']}",
            expected_sha=str(item["sha256"]),
            git_ref=args.git_ref,
        )

    # Recheck winner/EA/SET parity from retained committed bundle.
    sys.path.insert(0, str(ROOT / "backend"))
    from max_backend.optimizer_core import parse_set_optimizer_entries, read_ea_optimizer_defaults

    ea_path = ROOT / bundle_root / f"Max_Challenger_{EXPECTED_CHALLENGER}.mq5"
    set_path = ROOT / bundle_root / f"Max_Challenger_{EXPECTED_CHALLENGER}.set"
    ea_params = read_ea_optimizer_defaults(ea_path)
    set_entries = parse_set_optimizer_entries(set_path.read_text(encoding="utf-8"))
    set_params = {name: set_entries[name]["value"] for name in winner["params"]}
    require(set(ea_params) == set(winner["params"]) == set(set_params), "Parameter universe parity mismatch")
    for name, expected in winner["params"].items():
        require(abs(float(ea_params[name]) - float(expected)) <= 1e-9, f"Winner/EA mismatch: {name}")
        require(abs(float(set_params[name]) - float(expected)) <= 1e-9, f"Winner/SET mismatch: {name}")
        require(set_entries[name]["optimize"] == "N", f"Challenger SET optimization flag not N: {name}")

    baseline = assert_file("ea/baseline/Max_MTF.mq5", expected_sha=EXPECTED_EA, git_ref=args.git_ref)
    require(sha(baseline) == EXPECTED_EA, "Baseline EA changed")

    overview_path = ROOT / retained / EXPECTED_JOB / "overview_after_registration.json"
    overview = load(overview_path)
    require(overview["current_strategy_champion"] is None, "Champion changed during M03")
    require(overview["ea_baseline"]["status"] == "BASELINE_NOT_CHAMPION", "Baseline status changed")
    require(int(overview["database"]["schema_version"]) == 3, "M03 DB schema is not 3")

    repair = load(ROOT / evidence["m02_carried_repair"])
    require(repair["status"] == "PASS", "M02 carried repair evidence is not PASS")
    require(repair["confirmed_call"]["confirmed_provider_calls"] == 1, "Confirmed-call accounting missing")
    require(repair["unconfirmed_restart"]["confirmed_provider_calls"] == 0, "Unconfirmed call falsely counted")
    require(repair["unconfirmed_restart"]["actual_llm_call"] is False, "Unconfirmed call falsely claims LLM call")
    require(repair["unconfirmed_restart"]["provider_retried"] is False, "Unconfirmed call retried")

    secret = load(ROOT / evidence["secret_scan"])
    require(int(secret["matches"]) == 0, "Secret scan found matches")
    final_git_secret = load(ROOT / evidence["final_git_secret_scan"])
    require(
        int(final_git_secret["matches"]) == 0,
        "Final staged Git secret scan found matches",
    )

    idempotency = load(ROOT / "evidence/m03/verification/real_idempotency.json")
    require(int(idempotency["rows_before"]) == 1, "Pre-idempotency Challenger count changed")
    require(int(idempotency["rows_after"]) == 1, "Duplicate Challenger created")
    require(
        idempotency["challenger_id_before"] == idempotency["challenger_id_after"]
        == EXPECTED_CHALLENGER,
        "Challenger identity changed during idempotent registration",
    )
    require(
        idempotency["manifest_sha_before"] == idempotency["manifest_sha_after"],
        "Challenger manifest changed during idempotent registration",
    )
    for job_id in ("20260922_081129_e3902b3d", "20260922_101450_dc549869"):
        require(
            int(idempotency["no_winner_challenger_counts"][job_id]) == 0,
            f"No-winner job created Challenger: {job_id}",
        )

    restart = load(ROOT / "evidence/m03/verification/restart_ui_audit.json")
    require(
        restart["before_challenger_count"] == restart["after_challenger_count"] == 1,
        "Restart created duplicate Challenger",
    )
    require(
        restart["before_manifest_sha"] == restart["after_manifest_sha"],
        "Restart changed Challenger manifest",
    )
    require(
        restart["before_scientist_calls"] == restart["after_scientist_calls"] == 0,
        "Restart triggered Scientist call",
    )
    require(
        restart["before_optimizer_evidence_files"]
        == restart["after_optimizer_evidence_files"],
        "Restart created new optimizer evidence",
    )

    runtime = load(ROOT / "evidence/m03/verification/runtime_smoke.json")
    require(runtime["promote_endpoint_status"] == 404, "Promotion endpoint exists in M03")
    require(runtime["challenger_integrity"] == "VERIFIED", "Runtime Challenger integrity failed")
    require(int(runtime["parameter_rows"]) == PARAM_COUNT, "Runtime parameter comparison is incomplete")

    for reference in evidence["required_files"]:
        normalized = rel(reference)
        require(normalized in real_files or normalized in verification_files or (ROOT / normalized).is_file(), f"Acceptance reference missing: {normalized}")
        assert_file(normalized, git_ref=args.git_ref)

    print("M03_EVIDENCE_INTEGRITY=PASS")
    print(f"REAL_JOB={EXPECTED_JOB}")
    print(f"CHALLENGER_ID={EXPECTED_CHALLENGER}")
    print(f"ELIGIBLE_PASSES={EXPECTED_ELIGIBLE}")
    print(f"WINNER_PASS={EXPECTED_WINNER_PASS}")
    print(f"XML_SHA={EXPECTED_XML}")
    print(f"SIDECAR_SHA={EXPECTED_SIDECAR}")
    print(f"GIT_OBJECT_REF={args.git_ref or 'WORKTREE'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EvidenceError as exc:
        print(f"M03_EVIDENCE_INTEGRITY=FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
