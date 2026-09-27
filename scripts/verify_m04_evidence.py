from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BASE = "0b91cc3270cadaff9dbf085cf3b580dd07b95e1e"
EXPECTED_REPAIR_BASE = "b75ceb9eb58bf71799887da1d9dfac0871d7768f"
EXPECTED_CHALLENGER = "STRAT-20260922-120735-R01-P11"
EXPECTED_JOB = "20260922_120735_07989e09"
EXPECTED_PROMOTION = "PROMOTE-20260922-141239-328869ab"
EXPECTED_BASELINE_ARCHIVE = "BASELINE-MTF-V2_20260922_141239_UTC"
EXPECTED_BASELINE_SHA = "9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345"
EXPECTED_CHAMPION_EA_SHA = "a1e7e35c370ed889221e4da2fde3ed1801106234a2ab455e5da418bb58ae6aaf"
EXPECTED_CHAMPION_SET_SHA = "dd273cb4b82c1164dc1a1e166c6447bced8b49db7b635805731280ca575b02a6"
EXPECTED_DEPLOYED_EX5_SHA = "308e1e4b313b151ad2b42f2d87bf4d34e37e8efe65e850a4044c627e60412fb0"
EXPECTED_XML_SHA = "c269a31fdabc2d4f4adac010894a96ea344927ba015b55861ae0bd8cde5fe30f"
EXPECTED_SIDECAR_SHA = "3f0fcc5e2307f1fef7e7d9f99b3e63507cf924fa4978f0be0626286af48e2629"


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
    return Path(text).as_posix()


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
        worktree_sha = sha(path)
        git_object_sha = hashlib.sha256(blob.stdout).hexdigest()
        require(
            git_object_sha == worktree_sha,
            f"Worktree/Git object mismatch: {name}",
        )
        if expected_sha:
            require(
                git_object_sha == expected_sha.lower(),
                f"Git object SHA mismatch: {name}",
            )
    return path


def check_manifest(root_rel: str, *, git_ref: str | None = None) -> dict[str, str]:
    root_name = rel(root_rel)
    root = ROOT / root_name
    manifest_path = assert_file(f"{root_name}/manifest.json", git_ref=git_ref)
    manifest = load(manifest_path)
    entries: dict[str, str] = {}
    for item in manifest.get("files", []):
        item_rel = f"{root_name}/{rel(item['path'])}"
        digest = str(item["sha256"]).lower()
        assert_file(item_rel, expected_sha=digest, git_ref=git_ref)
        entries[item_rel] = digest
    require(
        len(entries) == int(manifest.get("file_count", -1)),
        f"Manifest count mismatch: {root_name}",
    )
    actual = {
        p.relative_to(ROOT).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    require(actual == set(entries), f"Manifest file-set mismatch: {root_name}")
    return entries


def require_text(path: Path, needle: str) -> None:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    require(needle in text, f"Missing expected text in {path.name}: {needle}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--git-ref")
    args = parser.parse_args()
    git_ref = args.git_ref

    acceptance_path = assert_file("evidence/m04/acceptance.json", git_ref=git_ref)
    acceptance = load(acceptance_path)
    require(acceptance.get("schema") == "MAX_REBUILD_M04_ACCEPTANCE_V1", "Wrong acceptance schema")
    require(acceptance.get("status") == "PASS", "M04 acceptance not PASS")
    require(acceptance.get("base_commit") == EXPECTED_BASE, "Wrong M04 base commit")
    require(acceptance.get("promotion_id") == EXPECTED_PROMOTION, "Wrong promotion ID")

    evidence = acceptance["evidence"]
    for required in evidence["required_files"]:
        assert_file(required, git_ref=git_ref)

    promotion_root = rel(evidence["promotion_root"])
    check_manifest(promotion_root, git_ref=git_ref)
    promotion = load(ROOT / promotion_root / "promotion.json")
    request = load(ROOT / promotion_root / "request.json")
    compile_result = load(ROOT / promotion_root / "compile_result.json")
    parity = load(ROOT / promotion_root / "parity.json")
    deployment = load(ROOT / promotion_root / "deployment.json")
    preflight = load(ROOT / promotion_root / "preflight.json")

    require(promotion["promotion_id"] == EXPECTED_PROMOTION, "Promotion identity mismatch")
    require(promotion["challenger_id"] == EXPECTED_CHALLENGER, "Promotion Challenger mismatch")
    require(promotion["previous_champion_id"] is None, "First promotion previous Champion not NONE")
    require(promotion["new_champion_id"] == EXPECTED_CHALLENGER, "New Champion mismatch")
    require(promotion["state"] == "COMMITTED", "Promotion not COMMITTED")
    require(promotion["baseline_archive_id"] == EXPECTED_BASELINE_ARCHIVE, "Baseline archive mismatch")
    require(int(promotion["scientist_calls"]) == 0, "Scientist called during promotion")
    require(promotion["live_authority"] == "NONE", "Live authority introduced")

    require(request["confirmation"] == "OWNER_EXPLICIT_CONFIRM_PROMOTION", "Explicit confirmation missing")
    require(request["expected_current_champion_id"] is None, "First-promotion stale guard mismatch")
    require(
        request["expected_challenger_manifest_sha256"]
        == "12d1872641754376512b5e2ca014426423801f2dafc1fb6338fed78c28a3c996",
        "Challenger manifest expectation mismatch",
    )
    require(preflight["challenger_integrity"] == "VERIFIED", "Preflight Challenger not verified")
    require(preflight["current_champion_id"] is None, "Preflight current Champion was not NONE")

    require(compile_result["status"] == "PASS", "Promotion compile not PASS")
    require(compile_result["fresh_ex5"] is True, "Promotion EX5 not fresh")
    require(compile_result["compile_summary"]["found"] is True, "Compile summary missing")
    require(int(compile_result["compile_summary"]["errors"]) == 0, "Compile errors present")
    require(int(compile_result["compile_summary"]["warnings"]) == 0, "Compile warnings present")
    require(
        compile_result["compile_summary"]["line"] == "Result: 0 errors, 0 warnings",
        "Compile summary line mismatch",
    )
    require(compile_result["source_sha256"] == EXPECTED_CHAMPION_EA_SHA, "Compile source SHA mismatch")
    require(compile_result["ex5_sha256"] == EXPECTED_DEPLOYED_EX5_SHA, "Promotion EX5 SHA mismatch")
    compile_txt = assert_file(f"{promotion_root}/compile/metaeditor_compile.txt", git_ref=git_ref)
    require_text(compile_txt, "Result: 0 errors, 0 warnings")

    require(parity["status"] == "VERIFIED", "Champion parity not VERIFIED")
    require(int(parity["parameter_count"]) == 16, "Champion parameter count mismatch")
    require(parity["all_optimizer_flags"] == "N", "Champion SET has optimization flags enabled")
    require(parity["challenger_to_champion_params"] is True, "Challenger/Champion params mismatch")
    require(parity["champion_params_to_ea"] is True, "Champion EA param mismatch")
    require(parity["champion_params_to_project_set"] is True, "Champion SET param mismatch")
    require(parity["ea_to_project_set"] is True, "EA/SET parity mismatch")
    require(parity["strategy_logic"]["non_whitelisted_changes"] == 0, "Non-whitelisted EA mutation")
    require(
        parity["strategy_logic"]["changed_optimizer_defaults"] == ["InpMinConsensus"],
        "Unexpected Champion EA default changes",
    )

    champion_ea = assert_file(
        "ea/champion/current/Max_MTF.mq5",
        expected_sha=EXPECTED_CHAMPION_EA_SHA,
        git_ref=git_ref,
    )
    champion_set = assert_file(
        "ea/champion/current/Max_MTF.set",
        expected_sha=EXPECTED_CHAMPION_SET_SHA,
        git_ref=git_ref,
    )
    baseline = assert_file(
        "ea/baseline/Max_MTF.mq5",
        expected_sha=EXPECTED_BASELINE_SHA,
        git_ref=git_ref,
    )
    require(sha(champion_ea) == deployment["project_ea_sha256"], "Project Champion EA deployment record mismatch")
    require(sha(champion_set) == deployment["project_set_sha256"], "Project Champion SET deployment record mismatch")
    require(sha(baseline) == deployment["baseline_sha256"], "Baseline changed after promotion")

    for key, expected in (
        ("mt5_source_sha256", EXPECTED_CHAMPION_EA_SHA),
        ("mt5_ex5_sha256", EXPECTED_DEPLOYED_EX5_SHA),
        ("tester_set_sha256", EXPECTED_CHAMPION_SET_SHA),
    ):
        require(deployment[key] == expected, f"Deployment record mismatch: {key}")
    for key, sha_key in (
        ("mt5_source", "mt5_source_sha256"),
        ("mt5_ex5", "mt5_ex5_sha256"),
        ("tester_set", "tester_set_sha256"),
    ):
        external = Path(deployment[key])
        require(external.is_file(), f"External deployment missing: {key}")
        require(sha(external) == deployment[sha_key], f"External deployment SHA mismatch: {key}")

    archive_root = rel(evidence["baseline_archive_root"])
    check_manifest(archive_root, git_ref=git_ref)
    archive_meta = load(ROOT / archive_root / "baseline.json")
    require(
        archive_meta["status"] == "ARCHIVED_PRE_FIRST_STRATEGY_CHAMPION",
        "Baseline archive status mismatch",
    )
    require(archive_meta["ea_sha256"] == EXPECTED_BASELINE_SHA, "Baseline archive SHA mismatch")
    require(
        sha(ROOT / archive_root / "Max_MTF.mq5") == EXPECTED_BASELINE_SHA,
        "Archived baseline bytes changed",
    )

    challenger_manifest = assert_file(
        f"artifacts/strategy_challengers/{EXPECTED_CHALLENGER}/manifest.json",
        expected_sha="12d1872641754376512b5e2ca014426423801f2dafc1fb6338fed78c28a3c996",
        git_ref=git_ref,
    )
    require(challenger_manifest.is_file(), "Source Challenger manifest missing")

    authority = load(ROOT / rel(evidence["authority_snapshot"]))
    require(authority["database"]["schema_version"] == 4, "Authority snapshot schema not 4")
    require(authority["current_champion_count"] == 1, "Current Champion count is not 1")
    require(authority["active_challenger_count"] == 0, "Promoted Challenger still active")
    require(authority["source_challenger"]["status"] == "PROMOTED", "Challenger not PROMOTED")
    require(authority["current_champion"]["strategy_id"] == EXPECTED_CHALLENGER, "Current Champion identity mismatch")
    require(authority["current_champion"]["status"] == "CURRENT", "Champion status mismatch")
    require(authority["current_champion"]["source_job_id"] == EXPECTED_JOB, "Champion source job mismatch")
    require(authority["current_champion"]["source_round"] == 1, "Champion source round mismatch")
    require(authority["current_champion"]["source_pass"] == 11, "Champion source pass mismatch")
    require(authority["source_winner"]["xml_sha256"] == EXPECTED_XML_SHA, "Winner XML lineage mismatch")
    require(authority["source_winner"]["sidecar_sha256"] == EXPECTED_SIDECAR_SHA, "Winner sidecar lineage mismatch")
    require(authority["integrity"]["status"] == "VERIFIED", "Current Champion integrity not VERIFIED")
    require(authority["integrity"]["live_authority"] == "NONE", "Champion integrity claims Live authority")
    require(all(authority["historical_jobs"].values()), "Historical M01/M02/M03 jobs not preserved")

    ui = load(ROOT / rel(evidence["ui_promotion_execution"]))
    require(ui["pre_promotion_champion"] == "NONE", "UI pre-promotion Champion not NONE")
    require(ui["first_attempt"]["explicit_confirm_clicked"] is True, "First explicit Confirm not recorded")
    require(ui["first_attempt"]["journal_state"] == "ROLLED_BACK", "Failed first attempt did not roll back")
    require(ui["second_attempt"]["explicit_confirm_clicked"] is True, "Committed explicit Confirm not recorded")
    require(ui["second_attempt"]["journal_state"] == "COMMITTED", "Real UI promotion not COMMITTED")
    require(ui["second_attempt"]["new_champion"] == EXPECTED_CHALLENGER, "UI promoted wrong Champion")
    require(ui["ui_followup"]["stale_list_defect"] == "REPAIRED", "UI stale-list defect not repaired")
    require(ui["scientist_calls_during_promotion"] == 0, "UI promotion reports Scientist call")
    require(ui["live_authority"] == "NONE", "UI promotion reports Live authority")

    ui_final = load(ROOT / rel(evidence["ui_final_runtime"]))
    require(ui_final["status"] == "PASS", "Final UI postcheck failed")
    require(ui_final["overview_champion"] == EXPECTED_CHALLENGER, "Overview Champion mismatch")
    require(ui_final["active_challengers"] == 0, "Final UI still shows active Challenger")
    require(ui_final["champion_page_status"] == "CURRENT STRATEGY CHAMPION", "Champion page status mismatch")
    require(ui_final["scientist_page_present"] is False, "Scientist page exposed in M04")
    require(ui_final["live_controls_present"] is False, "Live controls exposed in M04")

    run_max = assert_file(rel(evidence["run_max_final"]), git_ref=git_ref)
    require_text(run_max, "MAX_READY")
    require_text(run_max, "[READY] SQLite = READY (schema 4)")
    require_text(run_max, f"[READY] Strategy Champion = {EXPECTED_CHALLENGER}")
    require_text(run_max, "[READY] Browser opened after readiness.")

    recompile = load(ROOT / rel(evidence["recompile_check"]))
    require(recompile["status"] == "PASS", "Final-code recompile failed")
    require(recompile["fresh_ex5"] is True, "Final-code recompile EX5 not fresh")
    require(recompile["compile_summary"]["line"] == "Result: 0 errors, 0 warnings", "Final-code compile summary mismatch")
    require(recompile["source_sha256"] == EXPECTED_CHAMPION_EA_SHA, "Final-code compile source mismatch")

    rollback = load(ROOT / rel(evidence["rollback_attempt"]))
    require(rollback["status"] == "ROLLED_BACK_AS_DESIGNED", "Real rollback diagnostic missing")
    require(rollback["champion_after_failed_attempt_was_none"] is True, "Failed promotion created Champion")
    require(rollback["source_challenger_remained_active_after_failed_attempt"] is True, "Failed promotion consumed Challenger")
    require(rollback["rollback_status"] == "RESTORED_PREVIOUS_AUTHORITY", "Rollback authority not restored")

    secret = load(ROOT / rel(evidence["secret_scan"]))
    require(secret["status"] == "PASS", "Secret scan failed")
    require(int(secret["matches"]) == 0, "Secret scan found matches")
    staged_secret = load(ROOT / "evidence/m04/verification/final_git_secret_scan.json")
    require(staged_secret["status"] == "PASS", "Final staged Git secret scan failed")
    require(int(staged_secret["matches"]) == 0, "Final staged Git secret scan found matches")

    for filename, needle in (
        ("backend_pytest.txt", "[100%]"),
        ("pip_check.txt", "No broken requirements found."),
        ("frontend_tests.txt", "12 passed"),
        ("frontend_lint.txt", "Found 0 warnings and 0 errors."),
        ("frontend_build.txt", "built in"),
        ("npm_audit.txt", "found 0 vulnerabilities"),
        ("verify_m01.txt", "M01_EVIDENCE_INTEGRITY=PASS"),
        ("verify_m02.txt", "M02_EVIDENCE_INTEGRITY=PASS"),
        ("verify_m03.txt", "M03_EVIDENCE_INTEGRITY=PASS"),
    ):
        p = assert_file(f"evidence/m04/verification/{filename}", git_ref=git_ref)
        require_text(p, needle)

    repair_path = assert_file(
        "evidence/m04/repair/acceptance.json",
        git_ref=git_ref,
    )
    repair = load(repair_path)
    require(
        repair.get("schema") == "MAX_REBUILD_M04_CONTROL_ROOM_REPAIR_V1",
        "Wrong M04 repair acceptance schema",
    )
    require(repair.get("status") == "PASS", "M04 repair acceptance not PASS")
    require(
        repair.get("repair_base") == EXPECTED_REPAIR_BASE,
        "Wrong M04 repair base",
    )
    for required in repair["evidence"]["required_files"]:
        expected = repair["evidence"]["sha256"].get(required)
        require(bool(expected), f"Repair evidence SHA missing: {required}")
        assert_file(required, expected_sha=expected, git_ref=git_ref)

    migration = load(
        ROOT / rel(repair["evidence"]["schema4_to_5_migration"])
    )
    require(migration["status"] == "PASS", "Schema 4 -> 5 migration not PASS")
    require(migration["before"]["schema"] == "4", "Migration source was not schema 4")
    require(
        migration["after"]["database"]["schema_version"] == 5,
        "Migration target is not schema 5",
    )
    require(
        migration["after"]["current_champion"]["strategy_id"] == EXPECTED_CHALLENGER,
        "Real Champion changed during schema migration",
    )
    require(
        migration["after"]["current_champion"]["promotion_id"] == EXPECTED_PROMOTION,
        "Real Champion promotion lineage changed during migration",
    )
    require(
        migration["after"]["challenger"]["status"] == "PROMOTED",
        "Real current Champion Challenger state changed during migration",
    )
    require(
        migration["after"]["challenger"]["manifest_sha256"]
        == "12d1872641754376512b5e2ca014426423801f2dafc1fb6338fed78c28a3c996",
        "Real Challenger manifest changed during migration",
    )
    require(
        migration["after"]["challenger"]["ea_version"] == "2.00",
        "Real Strategy EA version changed during migration",
    )

    runtime = load(ROOT / rel(repair["evidence"]["runtime_schema5"]))
    require(runtime["status"] == "PASS", "Schema-5 runtime smoke failed")
    require(runtime["database_schema"] == 5, "Runtime database is not schema 5")
    require(
        runtime["current_champion"] == EXPECTED_CHALLENGER,
        "Runtime current Champion changed",
    )
    require(
        runtime["active_challenger_count"] == 0,
        "Current real Champion incorrectly exposed as active Challenger",
    )
    require(
        runtime["baseline_sha256"] == EXPECTED_BASELINE_SHA,
        "Runtime baseline SHA changed",
    )

    compatibility = load(
        ROOT / rel(repair["evidence"]["final_schema_compatibility"])
    )
    require(
        compatibility["schema"] == "MAX_REBUILD_M04_FINAL_SCHEMA_COMPATIBILITY_V1",
        "Wrong final schema compatibility evidence schema",
    )
    require(compatibility["status"] == "PASS", "Final schema compatibility not PASS")
    require(compatibility["database_schema"] == 5, "Final database schema is not 5")
    require(
        compatibility["schema_migration_required"] is False,
        "Final compatibility repair unexpectedly requires a schema migration",
    )
    for layer in ("foundation", "optimizer", "challenger", "champion"):
        require(
            compatibility[layer]["status"] == "READY",
            f"{layer} database status is not READY",
        )
        require(
            int(compatibility[layer]["schema_version"]) == 5,
            f"{layer} database status does not report actual schema 5",
        )
    require(
        compatibility["real_champion"]["strategy_id"] == EXPECTED_CHALLENGER,
        "Final compatibility repair changed real Champion",
    )
    require(
        compatibility["real_champion"]["current_count"] == 1,
        "Final compatibility repair changed current Champion cardinality",
    )
    require(
        compatibility["real_champion"]["source_challenger_status"] == "PROMOTED",
        "Final compatibility repair changed source Challenger status",
    )
    require(
        compatibility["real_champion"]["ea_version"] == "2.00",
        "Final compatibility repair changed Strategy EA version",
    )
    require(
        compatibility["real_champion"]["promotion_id"] == EXPECTED_PROMOTION,
        "Final compatibility repair changed real promotion authority",
    )

    db_source = assert_file("backend/max_backend/db.py", git_ref=git_ref)
    require_text(db_source, "MIN_SUPPORTED_SCHEMA = 1")
    require_text(db_source, "actual_schema < MIN_SUPPORTED_SCHEMA")
    challenger_source = assert_file(
        "backend/max_backend/challenger_store.py",
        git_ref=git_ref,
    )
    require_text(challenger_source, "MIN_SUPPORTED_SCHEMA = 3")
    require_text(challenger_source, "actual_schema < MIN_SUPPORTED_SCHEMA")

    final_verification = repair["evidence"]["final_schema_compatibility_verification"]
    for filename, needle in (
        ("backend_pytest.txt", "[100%]"),
        ("pip_check.txt", "No broken requirements found."),
        ("frontend_tests.txt", "12 passed"),
        ("frontend_lint.txt", "Found 0 warnings and 0 errors."),
        ("frontend_build.txt", "built in"),
        ("npm_audit.txt", "found 0 vulnerabilities"),
        ("run_max.txt", "[READY] SQLite = READY (schema 5)"),
    ):
        artifact = assert_file(
            f"{rel(final_verification)}/{filename}",
            git_ref=git_ref,
        )
        require_text(artifact, needle)
    final_run = ROOT / rel(final_verification) / "run_max.txt"
    require_text(final_run, "MAX_READY")
    require_text(
        final_run,
        f"[READY] Strategy Champion = {EXPECTED_CHALLENGER}",
    )

    repair_secret = load(
        ROOT / rel(repair["evidence"]["secret_scan"])
    )
    require(repair_secret["status"] == "PASS", "M04 repair secret scan failed")
    require(int(repair_secret["matches"]) == 0, "M04 repair secret scan found matches")

    repair_run = assert_file(
        rel(repair["evidence"]["run_max_schema5"]),
        git_ref=git_ref,
    )
    require_text(repair_run, "MAX_READY")
    require_text(repair_run, "[READY] SQLite = READY (schema 5)")
    require_text(
        repair_run,
        f"[READY] Strategy Champion = {EXPECTED_CHALLENGER}",
    )

    replacement_tests = assert_file(
        rel(repair["evidence"]["replacement_tests"]),
        git_ref=git_ref,
    )
    require_text(replacement_tests, "[100%]")

    for filename, needle in (
        ("backend_pytest.txt", "[100%]"),
        ("pip_check.txt", "No broken requirements found."),
        ("frontend_tests.txt", "12 passed"),
        ("frontend_lint.txt", "Found 0 warnings and 0 errors."),
        ("frontend_build.txt", "built in"),
        ("npm_audit.txt", "found 0 vulnerabilities"),
    ):
        artifact = assert_file(
            f"evidence/m04/repair/verification/{filename}",
            git_ref=git_ref,
        )
        require_text(artifact, needle)

    store_source = assert_file(
        "backend/max_backend/champion_store.py",
        git_ref=git_ref,
    )
    require_text(store_source, "SCHEMA_VERSION = 5")
    require_text(store_source, "champion_tenure_id TEXT PRIMARY KEY")
    require_text(store_source, "SET status='CHALLENGER', updated_utc=?")
    require_text(
        store_source,
        "PREVIOUS_CHAMPION_CHALLENGER_REACTIVATION_FAILED",
    )

    store_tests = assert_file(
        "backend/tests/test_m04_store.py",
        git_ref=git_ref,
    )
    require_text(
        store_tests,
        "test_replacement_and_repromotion_preserve_strategy_identity_and_bundle",
    )
    require_text(
        store_tests,
        'assert a_after["ea_version"] == "2.00"',
    )
    require_text(
        store_tests,
        '"bundle_path": a_after["bundle_path"]',
    )

    promotion_tests = assert_file(
        "backend/tests/test_m04_promotion.py",
        git_ref=git_ref,
    )
    require_text(
        promotion_tests,
        "test_service_a_to_b_to_a_repromotion_reuses_strategy_identity_and_bundle",
    )
    require_text(
        promotion_tests,
        "test_failed_repromotion_of_a_keeps_b_current_and_a_challenger",
    )

    roadmap = assert_file("docs/ROADMAP.md", git_ref=git_ref)
    require_text(roadmap, "M06")
    require_text(roadmap, "Strategy Challenger Operations")
    require_text(roadmap, "PLANNED / BLOCKED_BY_M05")
    require_text(roadmap, "Non-destructive Challenger Retirement / Archive")
    require_text(roadmap, "No destructive Challenger/Strategy delete")

    require(
        repair["blockers"]["old_champion_to_active_challenger"] == "PASS",
        "Control Room blocker #1 not closed",
    )
    require(
        repair["blockers"]["same_strategy_repromotion"] == "PASS",
        "Control Room blocker #2 not closed",
    )
    require(
        repair["real_m04_authority"]["promotion"] == EXPECTED_PROMOTION,
        "Repair changed real M04 promotion authority",
    )
    require(
        repair["real_m04_authority"]["current_champion"] == EXPECTED_CHALLENGER,
        "Repair changed real M04 Champion authority",
    )
    require(
        repair["real_m04_authority"]["baseline_sha256"] == EXPECTED_BASELINE_SHA,
        "Repair changed baseline authority",
    )

    print("M04_EVIDENCE_INTEGRITY=PASS")
    print(f"PROMOTION_ID={EXPECTED_PROMOTION}")
    print(f"CHAMPION_ID={EXPECTED_CHALLENGER}")
    print(f"BASELINE_SHA={EXPECTED_BASELINE_SHA}")
    print(f"CHAMPION_EA_SHA={EXPECTED_CHAMPION_EA_SHA}")
    print(f"CHAMPION_SET_SHA={EXPECTED_CHAMPION_SET_SHA}")
    print(f"DEPLOYED_EX5_SHA={EXPECTED_DEPLOYED_EX5_SHA}")
    print(f"GIT_OBJECT_REF={git_ref or 'INDEX/WORKTREE'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EvidenceError as exc:
        print(f"M04_EVIDENCE_INTEGRITY=FAIL: {exc}")
        raise SystemExit(1)
