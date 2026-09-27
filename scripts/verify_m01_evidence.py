from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_PATH = ROOT / "evidence" / "m01" / "acceptance.json"

EXPECTED_JOB = "20260922_081129_e3902b3d"
EXPECTED_EA_SHA = "9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345"
EXPECTED_XML_SHA = "77a2dc21a2dd871c2ac50a65b2681b6cb95a0bdaa483fec8669c6edd4ab2ef1f"
EXPECTED_SIDECAR_SHA = "5bc79b33a9f4172f307f7a9761e3c23fa0ced00c1b970255699339016146700b"


class EvidenceIntegrityError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise EvidenceIntegrityError(f"Invalid JSON evidence: {path}") from exc


def git(*args: str, capture_bytes: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not capture_bytes,
        check=False,
    )


def normalize_repo_path(value: str) -> str:
    path = str(value).replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    if not path or path.startswith("/") or ":" in path.split("/")[0]:
        raise EvidenceIntegrityError(f"Not a repo-relative evidence path: {value}")
    return Path(path).as_posix()


def assert_repo_tree(rel: str, *, git_ref: str | None = None) -> None:
    rel = normalize_repo_path(rel).rstrip("/")
    path = ROOT / Path(rel)
    if not path.is_dir():
        raise EvidenceIntegrityError(f"Referenced evidence directory missing: {rel}")

    tracked = git("ls-files", "--", f"{rel}/")
    if tracked.returncode != 0 or not tracked.stdout.strip():
        raise EvidenceIntegrityError(f"Evidence directory has no tracked files: {rel}")

    if git_ref:
        exists = git("cat-file", "-e", f"{git_ref}:{rel}")
        if exists.returncode != 0:
            raise EvidenceIntegrityError(
                f"Candidate Git tree missing: {git_ref}:{rel}"
            )


def assert_file(
    rel: str,
    *,
    expected_sha: str | None = None,
    git_ref: str | None = None,
) -> None:
    rel = normalize_repo_path(rel)
    path = ROOT / Path(rel)
    if not path.is_file():
        raise EvidenceIntegrityError(f"Referenced evidence missing: {rel}")

    ignored = git("check-ignore", "--no-index", "-q", "--", rel)
    if ignored.returncode == 0:
        raise EvidenceIntegrityError(f"Retained evidence is still ignored: {rel}")
    if ignored.returncode not in (1,):
        raise EvidenceIntegrityError(
            f"git check-ignore failed for {rel}: {ignored.stderr.strip()}"
        )

    tracked = git("ls-files", "--error-unmatch", "--", rel)
    if tracked.returncode != 0:
        raise EvidenceIntegrityError(f"Retained evidence is untracked: {rel}")

    local_sha = sha256_file(path)
    if expected_sha and local_sha != expected_sha.lower():
        raise EvidenceIntegrityError(
            f"SHA-256 mismatch for {rel}: expected={expected_sha} actual={local_sha}"
        )

    if git_ref:
        exists = git("cat-file", "-e", f"{git_ref}:{rel}")
        if exists.returncode != 0:
            raise EvidenceIntegrityError(
                f"Candidate Git object missing: {git_ref}:{rel}"
            )
        if expected_sha:
            blob = git("show", f"{git_ref}:{rel}", capture_bytes=True)
            if blob.returncode != 0:
                raise EvidenceIntegrityError(
                    f"Cannot read candidate Git object: {git_ref}:{rel}"
                )
            object_sha = sha256_bytes(blob.stdout)
            if object_sha != expected_sha.lower():
                raise EvidenceIntegrityError(
                    f"Candidate-object SHA mismatch for {rel}: "
                    f"expected={expected_sha} actual={object_sha}"
                )


def manifest_entries(
    manifest_path: str,
    *,
    git_ref: str | None,
) -> dict[str, str]:
    manifest_rel = normalize_repo_path(manifest_path)
    assert_file(manifest_rel, git_ref=git_ref)
    manifest = load_json(ROOT / manifest_rel)
    root_rel = normalize_repo_path(str(manifest.get("retained_root") or manifest.get("root") or ""))
    assert_repo_tree(root_rel, git_ref=git_ref)
    for key in ("source_job_evidence", "retained_copy"):
        value = manifest.get(key)
        if isinstance(value, str) and value.strip():
            assert_repo_tree(value, git_ref=git_ref)
    entries: dict[str, str] = {}
    for item in manifest.get("files", []):
        rel = normalize_repo_path(f"{root_rel}/{item['path']}")
        expected_sha = str(item["sha256"]).lower()
        if rel in entries:
            raise EvidenceIntegrityError(f"Duplicate manifest path: {rel}")
        assert_file(rel, expected_sha=expected_sha, git_ref=git_ref)
        entries[rel] = expected_sha
    if not entries:
        raise EvidenceIntegrityError(f"Manifest contains no retained files: {manifest_rel}")
    return entries


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--git-ref",
        default=None,
        help="Also verify every retained artifact exists in this Git object/ref.",
    )
    args = parser.parse_args()

    acceptance_rel = "evidence/m01/acceptance.json"
    assert_file(acceptance_rel, git_ref=args.git_ref)
    acceptance = load_json(ACCEPTANCE_PATH)

    if acceptance.get("schema") != "MAX_REBUILD_M01_ACCEPTANCE_V1":
        raise EvidenceIntegrityError("Unexpected M01 acceptance schema")
    if acceptance.get("status") != "PASS":
        raise EvidenceIntegrityError("M01 acceptance status is not PASS")

    real = acceptance["real_optimizer_job"]
    if real["job_id"] != EXPECTED_JOB:
        raise EvidenceIntegrityError("Real optimizer job identity changed")
    if real["terminal_result"] != "NO_ELIGIBLE_WINNER_MAX_ROUNDS":
        raise EvidenceIntegrityError("Real optimizer terminal result changed")
    if real.get("winner") is not None:
        raise EvidenceIntegrityError("Real optimizer winner must remain NONE")
    round1 = real["round_1"]
    if int(round1["parsed_passes"]) != 2 or int(round1["eligible_passes"]) != 0:
        raise EvidenceIntegrityError("Real optimizer replay counts changed")

    ea = acceptance["ea"]
    for key in ("source_sha256", "snapshot_sha256", "deployed_mt5_sha256"):
        if str(ea[key]).lower() != EXPECTED_EA_SHA:
            raise EvidenceIntegrityError(f"EA SHA authority changed: {key}")
    if not bool(ea["parity"]):
        raise EvidenceIntegrityError("EA parity flag is false")
    assert_file(
        "ea/baseline/Max_MTF.mq5",
        expected_sha=EXPECTED_EA_SHA,
        git_ref=args.git_ref,
    )

    if str(round1["report_sha256"]).lower() != EXPECTED_XML_SHA:
        raise EvidenceIntegrityError("XML SHA authority changed")
    if str(round1["sidecar_sha256"]).lower() != EXPECTED_SIDECAR_SHA:
        raise EvidenceIntegrityError("Weighted-R sidecar SHA authority changed")

    evidence = acceptance["evidence"]
    assert_repo_tree(evidence["real_mt5_root"], git_ref=args.git_ref)
    assert_repo_tree(evidence["verification_root"], git_ref=args.git_ref)
    real_entries = manifest_entries(
        evidence["real_mt5_manifest"],
        git_ref=args.git_ref,
    )
    verification_entries = manifest_entries(
        evidence["verification_manifest"],
        git_ref=args.git_ref,
    )

    legacy_anchor = normalize_repo_path(acceptance["legacy_source_hash_anchor"])
    assert_file(legacy_anchor, git_ref=args.git_ref)

    integrity_guard = normalize_repo_path(evidence["integrity_guard"])
    assert_file(integrity_guard, git_ref=args.git_ref)

    referenced_logs: set[str] = set()
    for test_name, test in acceptance["tests"].items():
        if not isinstance(test, dict) or "log" not in test:
            continue
        rel = normalize_repo_path(test["log"])
        referenced_logs.add(rel)
        if not rel.endswith(".txt"):
            raise EvidenceIntegrityError(
                f"Acceptance test log is not retained non-ignored text: {test_name} -> {rel}"
            )
        expected_sha = verification_entries.get(rel)
        if expected_sha is None:
            raise EvidenceIntegrityError(
                f"Acceptance log missing from verification manifest: {rel}"
            )
        assert_file(rel, expected_sha=expected_sha, git_ref=args.git_ref)

    manifest_log_paths = {
        rel for rel in verification_entries
        if rel.endswith(".txt")
    }
    if referenced_logs != manifest_log_paths:
        missing = sorted(referenced_logs - manifest_log_paths)
        extra = sorted(manifest_log_paths - referenced_logs)
        raise EvidenceIntegrityError(
            f"Verification manifest/reference mismatch; missing={missing} extra={extra}"
        )

    compile_rel = normalize_repo_path(real["compile"]["retained_output"])
    compile_sha = str(real["compile"]["retained_output_sha256"]).lower()
    if real_entries.get(compile_rel) != compile_sha:
        raise EvidenceIntegrityError("Retained compiler output missing from real-MT5 manifest")
    assert_file(compile_rel, expected_sha=compile_sha, git_ref=args.git_ref)

    compile_bytes = (ROOT / compile_rel).read_bytes()
    if b"Result: 0 errors, 0 warnings" not in compile_bytes:
        raise EvidenceIntegrityError(
            "Retained MetaEditor output lacks real zero-error compile summary"
        )
    if args.git_ref:
        blob = git("show", f"{args.git_ref}:{compile_rel}", capture_bytes=True)
        if blob.returncode != 0 or b"Result: 0 errors, 0 warnings" not in blob.stdout:
            raise EvidenceIntegrityError(
                "Candidate-object MetaEditor output lacks zero-error compile summary"
            )

    report_rel = normalize_repo_path(round1["report"])
    sidecar_rel = normalize_repo_path(round1["weighted_r_sidecar"])
    if real_entries.get(report_rel) != EXPECTED_XML_SHA:
        raise EvidenceIntegrityError("Retained XML is absent from real-MT5 manifest")
    if real_entries.get(sidecar_rel) != EXPECTED_SIDECAR_SHA:
        raise EvidenceIntegrityError("Retained sidecar is absent from real-MT5 manifest")
    assert_file(report_rel, expected_sha=EXPECTED_XML_SHA, git_ref=args.git_ref)
    assert_file(sidecar_rel, expected_sha=EXPECTED_SIDECAR_SHA, git_ref=args.git_ref)

    if int(acceptance["scientist"]["call_count"]) != 0:
        raise EvidenceIntegrityError("Scientist call count is not zero")
    if int(acceptance["challenger"]["created"]) != 0:
        raise EvidenceIntegrityError("Challenger count is not zero")
    if acceptance["strategy_champion"]["mutation"] != "NONE":
        raise EvidenceIntegrityError("Strategy Champion mutation is not NONE")

    print("M01_EVIDENCE_INTEGRITY=PASS")
    print(f"REAL_JOB={EXPECTED_JOB}")
    print(f"RETAINED_REAL_MT5_FILES={len(real_entries)}")
    print(f"RETAINED_VERIFICATION_FILES={len(verification_entries)}")
    print(f"EA_SHA={EXPECTED_EA_SHA}")
    print(f"XML_SHA={EXPECTED_XML_SHA}")
    print(f"SIDECAR_SHA={EXPECTED_SIDECAR_SHA}")
    if args.git_ref:
        print(f"GIT_OBJECT_REF={args.git_ref}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EvidenceIntegrityError as exc:
        print(f"M01_EVIDENCE_INTEGRITY=FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
