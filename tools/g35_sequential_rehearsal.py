"""Read-only G35 GitHub-hosted 35-PR sequential merge rehearsal.

Only temporary local merge commits in a disposable runner. This is not
GitHub PR integration, MT5 acceptance, or authority to place trades.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from tools.main_integration_gate import verify_baseline
from tools.stack_audit import StackAuditError, audit_stack, fetch_authenticated_pr_stack
from tools.sequential_merge_rehearsal import RehearsalError, _git, _ref

_REPO = "maxqstudio/max-trading-agent"
_BRANCH = "feat/g35-full-stack-release-gates"
_MAIN_SHA = "5f27d132bc627fa73fde2e1b56b625cbdb09a465"
_SHA = re.compile(r"[0-9a-f]{40}\Z")


def validate_inputs(manifest: Any, sha: str, env: dict) -> list[dict]:
    """Make the current G32 pinned predecessors an exact merge plan."""
    try:
        predecessors = verify_baseline(manifest)
    except ValueError:
        raise RehearsalError("G35_INVALID_PINNED_STACK") from None
    if (type(sha) is not str or not _SHA.fullmatch(sha)
            or len(predecessors) != 34
            or env.get("GITHUB_REPOSITORY") != _REPO
            or env.get("GITHUB_EVENT_NAME") != "push"
            or env.get("GITHUB_REF") != "refs/heads/" + _BRANCH
            or env.get("GITHUB_SHA") != sha
            or env.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or env.get("G35_EXPECTED_MAIN_SHA") != _MAIN_SHA):
        raise RehearsalError("G35_UNTRUSTED_RUN_CONTEXT")
    return [dict(record) for record in predecessors] + [{
        "pr": 82, "branch": _BRANCH, "head_sha": sha,
    }]


def verify_live_pr_bindings(plan: list[dict], github_prs: Any) -> None:
    """Require actual GitHub Draft PR identities, not only named Git refs."""
    try:
        audited = audit_stack(github_prs)
    except StackAuditError:
        raise RehearsalError("G35_GITHUB_PR_CHAIN_UNVERIFIED") from None
    if (audited["audited_count"] != len(plan)
            or audited["merged_count"] != 0):
        raise RehearsalError("G35_GITHUB_PR_CHAIN_UNVERIFIED")
    for index, (pinned, current) in enumerate(
            zip(plan, audited["ordered_pull_requests"])):
        parent = "main" if index == 0 else plan[index - 1]["branch"]
        if (current["number"] != pinned["pr"]
                or current["head_branch"] != pinned["branch"]
                or current["head_sha"] != pinned["head_sha"]
                or current["current_base"] != parent
                or not current["draft"]):
            raise RehearsalError("G35_GITHUB_PR_CHAIN_UNVERIFIED")


def run_rehearsal(work: Path, aggregate: Path, output: Path,
                  manifest: dict, *, env: dict | None = None) -> dict:
    env = dict(os.environ if env is None else env)
    plan = validate_inputs(manifest, env.get("GITHUB_SHA"), env)
    work, aggregate, output = Path(work), Path(aggregate), Path(output)
    if (not work.is_dir() or work.is_symlink() or
            not (work / ".git").is_dir() or
            aggregate.exists() or aggregate.is_symlink() or
            output.exists() or output.is_symlink() or
            not output.parent.is_dir()):
        raise RehearsalError("G35_UNSAFE_REHEARSAL_PATHS")
    if _git(work, "status", "--porcelain").stdout.strip():
        raise RehearsalError("G35_DIRTY_MAIN_CHECKOUT")
    if _ref(work, "HEAD") != _MAIN_SHA:
        raise RehearsalError("G35_MAIN_SHA_MISMATCH")

    report = {
        "schema_version": 1,
        "status": "G35_REHEARSAL_PENDING",
        "scope": "EPHEMERAL_MERGE_COMMIT_SIMULATION_ONLY",
        "repository": _REPO,
        "main_sha": _MAIN_SHA,
        "feature_sha": env["GITHUB_SHA"],
        "os": env.get("RUNNER_OS"),
        "expected_prs": 35,
        "github_pr_metadata_verified": False,
        "completed_steps": [],
        "conflicts": [],
        "sequential_tree_sha": None,
        "aggregate_tree_sha": None,
        "trees_identical": False,
        "full_python_suite_passed": False,
        "ui_suite_passed": False,
        "docker_review_passed": False,
        "remote_repository_modified": False,
        "real_mt5_terminal_tested": False,
        "owner_acceptance": "PENDING",
        "broker_execution": False,
        "execution": "DISABLED",
        "decision": "NO_TRADE",
    }

    def save() -> None:
        output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n",
                          encoding="utf-8")

    with output.open("x", encoding="utf-8") as sink:
        sink.write(json.dumps(report, sort_keys=True, indent=2) + "\n")
    try:
        _git(work, "fetch", "--no-tags", "origin",
             "refs/heads/main:refs/remotes/origin/main", timeout=180)
        if _ref(work, "refs/remotes/origin/main") != _MAIN_SHA:
            raise RehearsalError("G35_REMOTE_MAIN_MOVED")
        try:
            verify_live_pr_bindings(plan, fetch_authenticated_pr_stack())
        except StackAuditError:
            raise RehearsalError("G35_GITHUB_PR_API_UNAVAILABLE") from None
        report["github_pr_metadata_verified"] = True
        save()
        _git(work, "fetch", "--no-tags", "origin",
             "+refs/heads/feat/*:refs/remotes/origin/feat/*", timeout=240)
        last = None
        for item in plan:
            actual = _ref(work, "refs/remotes/origin/" + item["branch"])
            if actual != item["head_sha"]:
                raise RehearsalError("G35_PINNED_PR_HEAD_CHANGED")
            if last is not None and _git(
                    work, "merge-base", "--is-ancestor", last, actual,
                    check=False).returncode != 0:
                raise RehearsalError("G35_BROKEN_PR_ANCESTRY")
            last = actual

        for item in plan:
            before = _ref(work, "HEAD")
            merge = _git(work,
                         "-c", "user.name=G35-Ephemeral-CI",
                         "-c", "user.email=g35-ci@users.noreply.github.com",
                         "-c", "commit.gpgsign=false",
                         "merge", "--no-ff", "--no-commit", "--no-edit",
                         item["head_sha"], check=False)
            conflicts = _git(work, "diff", "--name-only",
                             "--diff-filter=U").stdout.splitlines()
            if merge.returncode != 0 or conflicts:
                report["blocked_pr"] = item["pr"]
                report["conflicts"] = conflicts[:100]
                raise RehearsalError(
                    "G35_SEQUENTIAL_SOURCE_CONFLICT" if conflicts else
                    "G35_SEQUENTIAL_MERGE_PROCESS_FAILED")
            merged_tree = _git(work, "write-tree").stdout.strip()
            if not _SHA.fullmatch(merged_tree):
                raise RehearsalError("G35_INVALID_STAGED_TREE")
            _git(work,
                 "-c", "user.name=G35-Ephemeral-CI",
                 "-c", "user.email=g35-ci@users.noreply.github.com",
                 "-c", "commit.gpgsign=false",
                 "commit", "--no-verify", "--no-gpg-sign",
                 "-m", "G35 temporary PR #" + str(item["pr"]))
            parents = _git(work, "show", "-s", "--format=%P", "HEAD").stdout.split()
            if parents != [before, item["head_sha"]]:
                raise RehearsalError("G35_UNEXPECTED_MERGE_PARENTS")
            if _ref(work, "HEAD^{tree}") != merged_tree:
                raise RehearsalError("G35_STAGED_TREE_CHANGED")
            report["completed_steps"].append({
                "pr": item["pr"],
                "head_branch": item["branch"],
                "head_sha": item["head_sha"],
                "temporary_merge_sha": _ref(work, "HEAD"),
                "tree_sha": merged_tree,
            })
            save()

        report["sequential_tree_sha"] = _ref(work, "HEAD^{tree}")
        _git(work, "worktree", "add", "--detach", str(aggregate), _MAIN_SHA)
        aggregate_merge = _git(
            aggregate,
            "-c", "user.name=G35-Ephemeral-CI",
            "-c", "user.email=g35-ci@users.noreply.github.com",
            "-c", "commit.gpgsign=false",
            "merge", "--no-ff", "--no-commit", "--no-edit",
            env["GITHUB_SHA"], check=False)
        if (aggregate_merge.returncode != 0 or _git(
                aggregate, "diff", "--name-only", "--diff-filter=U"
                ).stdout.strip()):
            raise RehearsalError("G35_AGGREGATE_MERGE_FAILED")
        report["aggregate_tree_sha"] = _git(aggregate, "write-tree").stdout.strip()
        if (not _SHA.fullmatch(report["aggregate_tree_sha"])
                or report["aggregate_tree_sha"] != report["sequential_tree_sha"]
                or len(report["completed_steps"]) != 35
                or report["completed_steps"][0]["pr"] != 14
                or report["completed_steps"][-1]["pr"] != 82):
            raise RehearsalError("G35_SEQUENTIAL_AGGREGATE_TREE_MISMATCH")
        report["trees_identical"] = True
        report["status"] = "G35_TREES_MATCH_TESTS_PENDING"
        save()
        return report
    except RehearsalError as problem:
        report["status"] = "G35_REHEARSAL_BLOCKED"
        report["blocker"] = str(problem)
        save()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(prog="g35-ephemeral-35-pr-merge")
    parser.add_argument("--work", required=True)
    parser.add_argument("--aggregate", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    try:
        source = Path(args.manifest)
        if (source.is_symlink() or not source.is_file()
                or not 100 <= source.stat().st_size <= 15000):
            raise RehearsalError("G35_INVALID_MANIFEST_PATH")
        manifest = json.loads(source.read_text(encoding="utf-8"))
        report = run_rehearsal(
            Path(args.work), Path(args.aggregate), Path(args.report), manifest)
    except (RehearsalError, OSError, ValueError, KeyError, TypeError) as failure:
        code = str(failure) if isinstance(failure, RehearsalError) else "G35_INVALID_INPUT"
        raise SystemExit("G35_REHEARSAL_BLOCKED: " + code) from None
    print(json.dumps({
        "status": report["status"],
        "steps": len(report["completed_steps"]),
        "tree_sha": report["sequential_tree_sha"],
        "feature_sha": report["feature_sha"],
        "main_sha": report["main_sha"],
        "decision": report["decision"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
