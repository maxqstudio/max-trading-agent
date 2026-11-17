"""G30 temporary PR-by-PR merge rehearsal: pinned refs, no GitHub writes.

Use only in ephemeral GitHub Actions checkout of main with read-only token.
Local merge commits are disposable. Never executes a generated candidate tool
or grants broker, trading, T1, demo or live permissions.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

_REPO = "maxqstudio/max-trading-agent"
_SELF = "feat/g30-sequential-main-rehearsal"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_BRANCH = re.compile(r"feat/[a-z0-9][a-z0-9/_-]{0,100}\Z")
_FAIL = "G30_REHEARSAL_BLOCKED"


class RehearsalError(ValueError):
    """Explicit failure with no credentials or command stdout."""


def validate_manifest(data: Any, *, feature_sha: str) -> list[dict]:
    if (type(data) is not dict or set(data) != {
            "schema_version", "repository", "main_sha", "status",
            "expected_merge_method", "previous_prs", "self_branch",
            "decision", "execution"}
            or data["schema_version"] != 1 or data["repository"] != _REPO
            or type(data["main_sha"]) is not str
            or not _SHA.fullmatch(data["main_sha"])
            or data["status"] != "REVIEWED_PINNED_PREMERGE_INPUTS"
            or data["expected_merge_method"] != "LOCAL_MERGE_COMMITS_ONLY_NO_PUSH"
            or data["self_branch"] != _SELF
            or data["decision"] != "NO_TRADE"
            or data["execution"] != "DISABLED"
            or type(feature_sha) is not str or not _SHA.fullmatch(feature_sha)):
        raise RehearsalError("G30_INVALID_MANIFEST")
    raw = data["previous_prs"]
    if type(raw) is not list or len(raw) != 29:
        raise RehearsalError("G30_INVALID_STACK_LENGTH")
    seen = set()
    planned = []
    for i, item in enumerate(raw):
        if type(item) is not dict or set(item) != {"pr", "branch", "sha", "base"}:
            raise RehearsalError("G30_INVALID_PR_ENTRY")
        expected_number = 14 + 2 * i
        previous_branch = "main" if i == 0 else raw[i - 1]["branch"]
        sha, name, base = item["sha"], item["branch"], item["base"]
        if (type(item["pr"]) is not int or item["pr"] != expected_number
                or type(sha) is not str or not _SHA.fullmatch(sha)
                or type(name) is not str or not _BRANCH.fullmatch(name)
                or name in seen or name == _SELF
                or type(base) is not str or base != previous_branch):
            raise RehearsalError("G30_BROKEN_STACK_MANIFEST")
        seen.add(name)
        planned.append({"pr": expected_number, "branch": name, "sha": sha})
    if planned[-1]["pr"] != 70:
        raise RehearsalError("G30_INCOMPLETE_STACK")
    planned.append({"pr": 72, "branch": _SELF, "sha": feature_sha})
    return planned


def _git(cwd: Path, *args: str, check: bool = True,
         timeout: int = 120) -> subprocess.CompletedProcess[str]:
    try:
        command = subprocess.run(["git", *args], cwd=cwd, text=True,
                                 capture_output=True, timeout=timeout,
                                 check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RehearsalError("G30_GIT_UNAVAILABLE") from None
    if check and command.returncode != 0:
        raise RehearsalError("G30_GIT_OPERATION_FAILED")
    return command


def _ref(cwd: Path, ref: str) -> str:
    result = _git(cwd, "rev-parse", "--verify", ref).stdout.strip()
    if not _SHA.fullmatch(result):
        raise RehearsalError("G30_INVALID_GIT_REF")
    return result


def _temp_commit(cwd: Path, label: str) -> str:
    _git(cwd, "-c", "user.name=G30-Temporary-GitHub-CI",
         "-c", "user.email=g30-ci@users.noreply.github.com",
         "-c", "commit.gpgsign=false",
         "commit", "--no-verify", "--no-gpg-sign", "-m", label)
    return _ref(cwd, "HEAD")


def _guard_environment(env: dict, expected_main: str,
                       expected_branch_sha: str) -> None:
    if (env.get("GITHUB_REPOSITORY") != _REPO
            or env.get("GITHUB_EVENT_NAME") != "push"
            or env.get("GITHUB_REF") != f"refs/heads/{_SELF}"
            or env.get("GITHUB_SHA") != expected_branch_sha
            or env.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or env.get("G30_EXPECTED_MAIN_SHA") != expected_main):
        raise RehearsalError("G30_UNTRUSTED_CI_CONTEXT")


def run_rehearsal(repo: Path, manifest: dict, feature_sha: str,
                  output: Path, aggregate_dir: Path,
                  *, env: dict | None = None) -> dict:
    """Rehearse 30 real merge commits and compare to one aggregate merge."""
    if env is None:
        env = os.environ
    plan = validate_manifest(manifest, feature_sha=feature_sha)
    _guard_environment(env, manifest["main_sha"], feature_sha)
    repo = Path(repo).resolve()
    output = Path(output)
    aggregate_dir = Path(aggregate_dir)
    if (not repo.is_dir() or not (repo / ".git").exists()
            or output.exists() or output.is_symlink()
            or aggregate_dir.exists() or aggregate_dir.is_symlink()):
        raise RehearsalError("G30_UNSAFE_WORKING_PATHS")
    if _git(repo, "status", "--porcelain").stdout.strip():
        raise RehearsalError("G30_DIRTY_MAIN_CHECKOUT")
    initial_main = _ref(repo, "HEAD")
    if initial_main != manifest["main_sha"]:
        raise RehearsalError("G30_MAIN_SHA_MOVED")

    report = {
        "schema_version": 1,
        "status": "G30_SEQUENTIAL_REHEARSAL_STARTED",
        "repository": _REPO,
        "main_sha": initial_main,
        "feature_sha": feature_sha,
        "expected_steps": 30,
        "completed_steps": [],
        "aggregate_tree_sha": None,
        "sequential_tree_sha": None,
        "trees_identical": False,
        "conflict_paths": [],
        "test_suite_passed": False,
        "docker_review_passed": False,
        "runner_os": env.get("RUNNER_OS", "UNVERIFIED"),
        "remote_repository_modified": False,
        "broker_execution": False,
        "generated_tool_admitted": False,
        "owner_acceptance": "PENDING",
        "decision": "NO_TRADE",
        "execution": "DISABLED",
    }

    def save() -> None:
        # The report is local runner metadata; an already supplied target is
        # refused rather than overwritten. Subsequent internal writes use
        # this exact file that we created and control.
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")

    # Create the artifact before network/merge operations so failures leave
    # useful evidence; no sensitive data is copied into the artifact.
    if not output.parent.is_dir():
        raise RehearsalError("G30_OUTPUT_PARENT_MISSING")
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    try:
        _git(repo, "fetch", "--no-tags", "origin",
             "refs/heads/main:refs/remotes/origin/main", timeout=180)
        if _ref(repo, "refs/remotes/origin/main") != initial_main:
            raise RehearsalError("G30_MAIN_CHANGED_DURING_REHEARSAL")
        _git(repo, "fetch", "--no-tags", "origin",
             "+refs/heads/feat/*:refs/remotes/origin/feat/*",
             timeout=240)
        last_sha = None
        for item in plan:
            actual = _ref(repo, "refs/remotes/origin/" + item["branch"])
            if actual != item["sha"]:
                raise RehearsalError("G30_PR_BRANCH_SHA_CHANGED")
            if last_sha is not None:
                ancestor = _git(repo, "merge-base", "--is-ancestor",
                                last_sha, actual, check=False)
                if ancestor.returncode != 0:
                    raise RehearsalError("G30_BROKEN_GIT_ANCESTRY")
            last_sha = actual

        for item in plan:
            before = _ref(repo, "HEAD")
            merge = _git(
                repo,
                "-c", "user.name=G30-Temporary-GitHub-CI",
                "-c", "user.email=g30-ci@users.noreply.github.com",
                "-c", "commit.gpgsign=false",
                "merge", "--no-ff", "--no-commit", "--no-edit",
                item["sha"], check=False,
            )
            conflicts = _git(repo, "diff", "--name-only",
                             "--diff-filter=U").stdout.splitlines()
            if merge.returncode != 0 or conflicts:
                report["blocked_pr"] = item["pr"]
                report["conflict_paths"] = conflicts[:100]
                raise RehearsalError(
                    "G30_SEQUENTIAL_MERGE_CONFLICT" if conflicts
                    else "G30_SEQUENTIAL_MERGE_PROCESS_FAILED"
                )
            git_tree = _git(repo, "write-tree").stdout.strip()
            if not _SHA.fullmatch(git_tree):
                raise RehearsalError("G30_INVALID_MERGE_TREE")
            commit = _temp_commit(repo, f"G30 temporary merge PR #{item['pr']}")
            parents = _git(repo, "show", "-s", "--format=%P", "HEAD").stdout.split()
            if parents != [before, item["sha"]]:
                raise RehearsalError("G30_UNEXPECTED_MERGE_PARENTS")
            if _ref(repo, "HEAD^{tree}") != git_tree:
                raise RehearsalError("G30_MERGE_TREE_CHANGED")
            report["completed_steps"].append({
                "pr": item["pr"], "head_branch": item["branch"],
                "pinned_head_sha": item["sha"],
                "temporary_merge_sha": commit,
                "merged_tree_sha": git_tree,
            })
            save()
        sequential = _ref(repo, "HEAD^{tree}")
        report["sequential_tree_sha"] = sequential
        # Entirely separate disposable Git worktree starting from initial
        # main, not the 30 temporary merge commits above.
        _git(repo, "worktree", "add", "--detach",
             str(aggregate_dir), initial_main)
        merge = _git(
            aggregate_dir,
            "-c", "user.name=G30-Temporary-GitHub-CI",
            "-c", "user.email=g30-ci@users.noreply.github.com",
            "-c", "commit.gpgsign=false",
            "merge", "--no-ff", "--no-commit", "--no-edit",
            feature_sha, check=False,
        )
        if merge.returncode != 0 or _git(
                aggregate_dir, "diff", "--name-only", "--diff-filter=U"
        ).stdout.strip():
            raise RehearsalError("G30_AGGREGATE_MERGE_CONFLICT")
        aggregate = _git(aggregate_dir, "write-tree").stdout.strip()
        if not _SHA.fullmatch(aggregate):
            raise RehearsalError("G30_INVALID_AGGREGATE_TREE")
        report["aggregate_tree_sha"] = aggregate
        report["trees_identical"] = sequential == aggregate
        if not report["trees_identical"]:
            raise RehearsalError("G30_SEQUENTIAL_AGGREGATE_TREE_MISMATCH")
        if len(report["completed_steps"]) != 30:
            raise RehearsalError("G30_INCOMPLETE_MERGE_REHEARSAL")
        report["status"] = "G30_SEQUENTIAL_AND_AGGREGATE_TREES_MATCH_TESTS_PENDING"
        save()
        return report
    except RehearsalError as problem:
        report["status"] = "G30_REHEARSAL_BLOCKED"
        report["blocker"] = str(problem)
        save()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(prog="g30-sequential-merge-rehearsal")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--aggregate-dir", required=True)
    args = parser.parse_args()
    try:
        source = Path(args.manifest)
        if (source.is_symlink() or not source.is_file()
                or not 100 <= source.stat().st_size <= 15_000):
            raise RehearsalError("G30_INVALID_MANIFEST_FILE")
        manifest = json.loads(source.read_text(encoding="utf-8"))
        report = run_rehearsal(
            Path(args.repo), manifest, os.environ["GITHUB_SHA"],
            Path(args.output), Path(args.aggregate_dir),
        )
    except RehearsalError as problem:
        # Only internal fixed error codes are exposed, never command stdout,
        # credentials, response text or candidate Python.
        raise SystemExit(f"{_FAIL}: {problem}") from None
    except (KeyError, OSError, ValueError, TypeError, UnicodeError):
        raise SystemExit(_FAIL) from None
    print(json.dumps({
        "status": report["status"],
        "completed_steps": len(report["completed_steps"]),
        "main_sha": report["main_sha"],
        "feature_sha": report["feature_sha"],
        "final_git_tree": report["sequential_tree_sha"],
        "trees_identical": report["trees_identical"],
        "decision": report["decision"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
