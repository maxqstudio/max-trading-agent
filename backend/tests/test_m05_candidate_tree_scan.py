from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "scan_m05_candidate_tree.py"

spec = importlib.util.spec_from_file_location("scan_m05_candidate_tree", SCRIPT)
assert spec is not None and spec.loader is not None
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def init_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.email", "m05-test@example.invalid")
    git(root, "config", "user.name", "M05 Test")
    return root


def test_exact_candidate_tree_scan_binds_to_requested_commit(tmp_path: Path) -> None:
    root = init_repo(tmp_path)
    (root / "safe.txt").write_text("safe candidate\n", encoding="utf-8")
    git(root, "add", "safe.txt")
    git(root, "commit", "-m", "safe")
    candidate = git(root, "rev-parse", "HEAD")

    result = scanner.scan_candidate_tree(candidate, root=root)

    assert result["scope"] == "EXACT_GIT_CANDIDATE_TREE"
    assert result["candidate_sha"] == candidate
    assert result["status"] == "PASS"
    assert result["matches"] == 0
    assert result["high_confidence_matches"] == []
    assert result["dpapi_or_settings_file_tracked"] is False
    assert result["forbidden_secret_storage_tracked"] == []
    assert "history" not in result["claim"].casefold()


def test_exact_candidate_tree_scan_redacts_detected_secret(tmp_path: Path) -> None:
    root = init_repo(tmp_path)
    secret = "sk-" + ("A" * 30)
    (root / "candidate.txt").write_text(
        "credential=" + secret + "\n",
        encoding="utf-8",
    )
    git(root, "add", "candidate.txt")
    git(root, "commit", "-m", "candidate")
    candidate = git(root, "rev-parse", "HEAD")

    result = scanner.scan_candidate_tree(candidate, root=root)
    serialized = json.dumps(result, sort_keys=True)

    assert result["status"] == "FAIL"
    assert result["scope"] == "EXACT_GIT_CANDIDATE_TREE"
    assert result["candidate_sha"] == candidate
    assert result["matches"] == 1
    assert result["high_confidence_matches"][0]["file"] == "candidate.txt"
    assert result["high_confidence_matches"][0]["rule"] == "openai_key"
    assert secret not in serialized
