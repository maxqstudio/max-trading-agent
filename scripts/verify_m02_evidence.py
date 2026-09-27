from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = ROOT / "evidence" / "m02" / "acceptance.json"

EXPECTED_JOB = "20260922_101450_dc549869"
EXPECTED_EA_SHA = "9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345"
EXPECTED_R1_XML = "0a97cefe673e257db7b7a3325ae74fda18fc16a3a4b65b981c3459ad5512882c"
EXPECTED_R1_SIDECAR = "0b7076a7a0127090ae2c7951d37c6e06abedee5fbd5bc2f4acf5af9af35a0641"
EXPECTED_R2_XML = "93b2030eb36c764905963c3e901b3632c030dbf54e95acb1ec9a20bba13bd6d3"
EXPECTED_R2_SIDECAR = "c59f7038599bde47a9e5e72962266334db1c73904e04e9d55b2472aed03ea289"
EXPECTED_REQUEST_SHA = "abefd0ebec2945d192edc91dc3a01fa09451a59834f5a8c308e547015129a05e"
EXPECTED_RAW_SHA = "f8f4cb0f068900ebcd4703bc2d0c4f56de5f1d0acf8944082d980e0f3da519d1"
EXPECTED_NORMALIZED_SHA = "233169fff869eea1e90bf843addf3c0a27b2dc54cf049f517984ccaf5b2ba23b"


class EvidenceError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise EvidenceError(f"Invalid JSON evidence: {path}") from exc


def git(*args: str, binary: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not binary,
        check=False,
    )


def repo_rel(value: str) -> str:
    rel = str(value).replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    if not rel or rel.startswith("/") or ":" in rel.split("/")[0]:
        raise EvidenceError(f"Not a repo-relative path: {value}")
    return Path(rel).as_posix()


def assert_tree(value: str, *, git_ref: str | None = None) -> None:
    rel = repo_rel(value).rstrip("/")
    path = ROOT / rel
    if not path.is_dir():
        raise EvidenceError(f"Missing evidence directory: {rel}")
    tracked = git("ls-files", "--", f"{rel}/")
    if tracked.returncode != 0 or not tracked.stdout.strip():
        raise EvidenceError(f"Evidence directory has no tracked files: {rel}")
    if git_ref:
        exists = git("cat-file", "-e", f"{git_ref}:{rel}")
        if exists.returncode != 0:
            raise EvidenceError(f"Git tree missing: {git_ref}:{rel}")


def assert_file(
    value: str,
    *,
    sha256: str | None = None,
    git_ref: str | None = None,
) -> None:
    rel = repo_rel(value)
    path = ROOT / rel
    if not path.is_file():
        raise EvidenceError(f"Missing evidence file: {rel}")
    ignored = git("check-ignore", "--no-index", "-q", "--", rel)
    if ignored.returncode == 0:
        raise EvidenceError(f"Retained evidence is ignored: {rel}")
    if ignored.returncode != 1:
        raise EvidenceError(f"git check-ignore failed: {rel}")
    tracked = git("ls-files", "--error-unmatch", "--", rel)
    if tracked.returncode != 0:
        raise EvidenceError(f"Retained evidence is untracked: {rel}")
    actual = sha256_file(path)
    if sha256 and actual != sha256.lower():
        raise EvidenceError(
            f"SHA mismatch {rel}: expected={sha256.lower()} actual={actual}"
        )
    if git_ref:
        exists = git("cat-file", "-e", f"{git_ref}:{rel}")
        if exists.returncode != 0:
            raise EvidenceError(f"Git object missing: {git_ref}:{rel}")
        if sha256:
            blob = git("show", f"{git_ref}:{rel}", binary=True)
            if blob.returncode != 0:
                raise EvidenceError(f"Cannot read Git object: {git_ref}:{rel}")
            object_sha = sha256_bytes(blob.stdout)
            if object_sha != sha256.lower():
                raise EvidenceError(
                    f"Git-object SHA mismatch {rel}: "
                    f"expected={sha256.lower()} actual={object_sha}"
                )


def manifest_files(
    manifest_rel: str,
    *,
    git_ref: str | None,
) -> dict[str, str]:
    assert_file(manifest_rel, git_ref=git_ref)
    manifest = load_json(ROOT / manifest_rel)
    root_rel = repo_rel(str(manifest.get("root") or manifest.get("retained_root") or ""))
    assert_tree(root_rel, git_ref=git_ref)
    for key in ("source_job_evidence", "retained_copy"):
        value = manifest.get(key)
        if isinstance(value, str) and value.strip():
            assert_tree(value, git_ref=git_ref)
    entries: dict[str, str] = {}
    for item in manifest.get("files", []):
        rel = repo_rel(f"{root_rel}/{item['path']}")
        expected = str(item["sha256"]).lower()
        if rel in entries:
            raise EvidenceError(f"Duplicate manifest path: {rel}")
        assert_file(rel, sha256=expected, git_ref=git_ref)
        entries[rel] = expected
    if not entries:
        raise EvidenceError(f"Manifest has no files: {manifest_rel}")
    return entries


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--git-ref", default=None)
    args = parser.parse_args()

    acceptance_rel = "evidence/m02/acceptance.json"
    assert_file(acceptance_rel, git_ref=args.git_ref)
    acceptance = load_json(ACCEPTANCE)
    require(
        acceptance.get("schema") == "MAX_REBUILD_M02_ACCEPTANCE_V1",
        "Unexpected M02 acceptance schema",
    )
    require(acceptance.get("status") == "PASS", "M02 acceptance status is not PASS")

    real = acceptance["real_optimizer_job"]
    require(real["job_id"] == EXPECTED_JOB, "Real M02 job identity changed")
    require(
        real["terminal_result"] == "NO_ELIGIBLE_WINNER_MAX_ROUNDS",
        "Unexpected real M02 terminal result",
    )
    require(real.get("winner") is None, "Real M02 winner must remain NONE")
    require(real["round_1"]["parsed_passes"] == 2, "Round 1 parsed count changed")
    require(real["round_1"]["eligible_passes"] == 0, "Round 1 eligibility changed")
    require(real["round_2"]["parsed_passes"] == 22, "Round 2 parsed count changed")
    require(real["round_2"]["eligible_passes"] == 0, "Round 2 eligibility changed")
    require(real["round_1"]["report_sha256"] == EXPECTED_R1_XML, "Round 1 XML SHA changed")
    require(
        real["round_1"]["sidecar_sha256"] == EXPECTED_R1_SIDECAR,
        "Round 1 sidecar SHA changed",
    )
    require(real["round_2"]["report_sha256"] == EXPECTED_R2_XML, "Round 2 XML SHA changed")
    require(
        real["round_2"]["sidecar_sha256"] == EXPECTED_R2_SIDECAR,
        "Round 2 sidecar SHA changed",
    )

    scientist = acceptance["scientist"]
    require(scientist["advisory_only"] is True, "Scientist advisory-only flag changed")
    require(scientist["source_round"] == 1 and scientist["target_round"] == 2, "Scientist transition changed")
    require(scientist["mode"] == "SCIENTIST_PROPOSAL", "Scientist mode changed")
    require(scientist["actual_llm_call"] is True, "Real provider call is not retained")
    require(scientist["accepted"] is True, "Real Scientist proposal is not accepted")
    require(scientist["validation_status"] == "ACCEPTED", "Scientist validation changed")
    require(scientist["provider"] == "gemini", "Provider identity changed")
    require(scientist["model"] == "gemini-3.5-flash", "Model identity changed")
    require(scientist["temperature"] == 0.1, "Scientist temperature changed")
    require(scientist["request_payload_sha256"] == EXPECTED_REQUEST_SHA, "Scientist request SHA changed")
    require(scientist["raw_response_sha256"] == EXPECTED_RAW_SHA, "Scientist raw response SHA changed")
    require(
        scientist["normalized_response_sha256"] == EXPECTED_NORMALIZED_SHA,
        "Scientist normalized response SHA changed",
    )
    counts = scientist["call_counts"]
    require(counts == {
        "proposal_transitions": 1,
        "actual_provider_calls": 1,
        "accepted_proposals": 1,
        "rejected_proposals": 0,
        "fallbacks": 0,
    }, "Scientist call counters changed")
    require(
        scientist["proposed_ranges"] == {
            "InpEntryThreshold": {"start": 0.18, "step": 0.02, "stop": 0.6}
        },
        "Scientist proposed range changed",
    )
    require(
        scientist["effective_selected_ranges"] == scientist["proposed_ranges"],
        "Effective selected range no longer equals accepted proposal",
    )

    require(acceptance["ea"]["snapshot_sha256"] == EXPECTED_EA_SHA, "EA SHA changed")
    require(acceptance["ea"]["parity"] is True, "EA parity is false")
    assert_file(
        "ea/baseline/Max_MTF.mq5",
        sha256=EXPECTED_EA_SHA,
        git_ref=args.git_ref,
    )

    require(acceptance["challenger"]["created"] == 0, "Challenger was created")
    require(
        acceptance["strategy_champion"]["mutation"] == "NONE",
        "Strategy Champion was mutated",
    )
    require(
        acceptance["restart"]["actual_provider_calls_after_restart"] == 1,
        "Restart duplicated Scientist provider call",
    )
    require(
        acceptance["restart"]["proposal_transitions_after_restart"] == 1,
        "Restart duplicated Scientist transition",
    )

    evidence = acceptance["evidence"]
    real_entries = manifest_files(
        evidence["real_mt5_manifest"],
        git_ref=args.git_ref,
    )
    verification_entries = manifest_files(
        evidence["verification_manifest"],
        git_ref=args.git_ref,
    )
    for rel in evidence["required_real_files"]:
        require(repo_rel(rel) in real_entries, f"Required real evidence missing from manifest: {rel}")

    compile_rel = repo_rel(real["compile"]["retained_compile_output"])
    require(compile_rel in real_entries, "Retained MetaEditor output missing from real manifest")
    assert_file(
        compile_rel,
        sha256=real["compile"]["retained_compile_output_sha256"],
        git_ref=args.git_ref,
    )
    compile_bytes = (ROOT / compile_rel).read_bytes()
    require(
        b"Result: 0 errors, 0 warnings" in compile_bytes,
        "Retained MetaEditor output lacks zero-error compile summary",
    )
    if args.git_ref:
        compile_blob = git("show", f"{args.git_ref}:{compile_rel}", binary=True)
        require(
            compile_blob.returncode == 0
            and b"Result: 0 errors, 0 warnings" in compile_blob.stdout,
            "Candidate-object MetaEditor output lacks zero-error compile summary",
        )

    scientist_request_rel = (
        f"evidence/m02/real_scientist_mt5/{EXPECTED_JOB}/round_01/"
        "scientist_request.json"
    )
    scientist_raw_rel = (
        f"evidence/m02/real_scientist_mt5/{EXPECTED_JOB}/round_01/"
        "scientist_raw_response.txt"
    )
    scientist_normalized_rel = (
        f"evidence/m02/real_scientist_mt5/{EXPECTED_JOB}/round_01/"
        "scientist_normalized_response.json"
    )
    request_evidence = load_json(ROOT / scientist_request_rel)
    require(
        sha256_json(
            {
                "system_contract": request_evidence["system_contract"],
                "payload": request_evidence["payload"],
            }
        )
        == EXPECTED_REQUEST_SHA,
        "Scientist semantic request payload SHA changed",
    )
    require(
        sha256_file(ROOT / scientist_raw_rel) == EXPECTED_RAW_SHA,
        "Scientist raw response file SHA changed",
    )
    normalized = load_json(ROOT / scientist_normalized_rel)
    require(
        sha256_json(normalized) == EXPECTED_NORMALIZED_SHA,
        "Scientist normalized semantic response SHA changed",
    )

    verification_refs = [
        evidence["secret_payload_audit"],
        evidence["legacy_source_recheck"],
        evidence["db_migration_audit"],
        evidence["ea_parity_audit"],
        evidence["real_retained_replay"],
        evidence["runtime_overview"],
        evidence["mt5_preflight"],
        evidence["run_max"],
        evidence["scope_guard"],
        evidence["final_git_secret_scan"],
    ]
    for rel in verification_refs:
        normalized_rel = repo_rel(rel)
        require(
            normalized_rel in verification_entries,
            f"Acceptance verification reference missing from manifest: {normalized_rel}",
        )
        assert_file(
            normalized_rel,
            sha256=verification_entries[normalized_rel],
            git_ref=args.git_ref,
        )

    secret_audit = load_json(ROOT / evidence["secret_payload_audit"])
    require(secret_audit["secret_matches"] == 0, "Secret scan found persisted credential")
    require(
        secret_audit.get("git_diff_secret_match") is False,
        "Working diff secret scan found credential material",
    )
    final_git_secret = load_json(ROOT / evidence["final_git_secret_scan"])
    require(
        final_git_secret["matches"] == 0,
        "Final staged Git secret scan found credential material",
    )
    require(
        secret_audit["forbidden_payload_fields_present"] == [],
        "Forbidden fields reached Scientist payload",
    )
    require(
        secret_audit["route_has_api_key_value"] is False,
        "Scientist request retained an api_key value",
    )
    git_secret = load_json(ROOT / evidence["final_git_secret_scan"])
    require(
        int(git_secret["matches"]) == 0,
        "Final staged candidate secret scan found credential material",
    )

    legacy = load_json(ROOT / evidence["legacy_source_recheck"])
    require(legacy["all_match"] is True, "Legacy source authority changed")
    db = load_json(ROOT / evidence["db_migration_audit"])
    require(int(db["schema_version"]) == 2, "DB schema changed unexpectedly")
    require(db["accepted_m01_job_preserved"] is True, "Accepted M01 job changed")
    require(db["accepted_m01_rounds_preserved"] is True, "Accepted M01 rounds changed")
    ea = load_json(ROOT / evidence["ea_parity_audit"])
    require(ea["parity"] is True, "EA source/snapshot parity audit failed")

    for name, test in acceptance["tests"].items():
        if not isinstance(test, dict) or "log" not in test:
            continue
        rel = repo_rel(test["log"])
        require(rel.endswith(".txt") or rel.endswith(".json"), f"Ignored log referenced by {name}")
        require(rel in verification_entries, f"Test evidence missing from manifest: {rel}")

    print("M02_EVIDENCE_INTEGRITY=PASS")
    print(f"REAL_JOB={EXPECTED_JOB}")
    print(f"RETAINED_REAL_FILES={len(real_entries)}")
    print(f"RETAINED_VERIFICATION_FILES={len(verification_entries)}")
    print(f"EA_SHA={EXPECTED_EA_SHA}")
    print(f"ROUND1_XML_SHA={EXPECTED_R1_XML}")
    print(f"ROUND1_SIDECAR_SHA={EXPECTED_R1_SIDECAR}")
    print(f"ROUND2_XML_SHA={EXPECTED_R2_XML}")
    print(f"ROUND2_SIDECAR_SHA={EXPECTED_R2_SIDECAR}")
    print("ACTUAL_PROVIDER_CALLS=1")
    if args.git_ref:
        print(f"GIT_OBJECT_REF={args.git_ref}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EvidenceError as exc:
        print(f"M02_EVIDENCE_INTEGRITY=FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
