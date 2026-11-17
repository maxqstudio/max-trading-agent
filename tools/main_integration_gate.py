"""G32 authenticated GitHub stack completeness gate; never trading authority.

The verifier reads GitHub's PR API using a read-only gh client. It refuses
model-authored status snippets and refuses to declare main integration from a
feature branch, synthetic pull_request merge, or a single cumulative CI run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from tools.stack_audit import StackAuditError, fetch_authenticated_pr_stack

_REPO = "maxqstudio/max-trading-agent"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REF = re.compile(r"feat/[a-z0-9][a-z0-9/_-]{0,100}\Z")
_FIELDS = frozenset({"schema_version", "repository", "default_branch",
                     "status", "predecessors", "self_pr", "self_branch",
                     "owner_acceptance", "decision", "execution"})


class IntegrationGateError(ValueError):
    """Safe non-sensitive error code."""


def _sha(value: Any) -> bool:
    return type(value) is str and _SHA.fullmatch(value) is not None


def verify_baseline(data: Any) -> list[dict]:
    if (type(data) is not dict or set(data) != _FIELDS
            or data["schema_version"] != 1 or data["repository"] != _REPO
            or data["default_branch"] != "main"
            or data["status"] != "PINNED_PREMERGE_SOURCE_IDENTITIES"
            or data["self_pr"] != 82
            or data["self_branch"] != "feat/g35-full-stack-release-gates"
            or data["owner_acceptance"] != "PENDING"
            or data["decision"] != "NO_TRADE"
            or data["execution"] != "DISABLED"
            or type(data["predecessors"]) is not list
            or len(data["predecessors"]) != 34):
        raise IntegrationGateError("G32_INVALID_PINNED_BASELINE")
    names = set()
    rows = []
    for index, entry in enumerate(data["predecessors"]):
        if (type(entry) is not dict or set(entry) != {"pr", "branch", "head_sha"}
                or type(entry["pr"]) is not int or entry["pr"] != 14 + 2 * index
                or type(entry["branch"]) is not str
                or _REF.fullmatch(entry["branch"]) is None
                or entry["branch"] in names
                or not _sha(entry["head_sha"])):
            raise IntegrationGateError("G32_INVALID_PINNED_PREDECESSOR")
        names.add(entry["branch"])
        rows.append(entry)
    if data["self_branch"] in names:
        raise IntegrationGateError("G32_SELF_BRANCH_COLLISION")
    return rows


def _safe_pr(record: dict, number: int, branch: str, sha: str | None) -> dict:
    if type(record) is not dict or record.get("number") != number:
        raise IntegrationGateError("G32_PR_IDENTITY_CHANGED")
    head, base = record.get("head"), record.get("base")
    if type(head) is not dict or type(base) is not dict:
        raise IntegrationGateError("G32_PR_IDENTITY_CHANGED")
    base_repo, head_repo = base.get("repo"), head.get("repo")
    merged = record.get("merged_at") is not None
    if (type(base_repo) is not dict or base_repo.get("full_name") != _REPO
            or (head_repo is not None and
                (type(head_repo) is not dict or head_repo.get("full_name") != _REPO))
            or (head_repo is None and not merged)
            or head.get("ref") != branch or not _sha(head.get("sha"))
            or (sha is not None and head["sha"] != sha)
            or type(base.get("ref")) is not str
            or type(record.get("draft")) is not bool
            or record.get("state") not in ("open", "closed")
            or record.get("html_url") !=
               f"https://github.com/{_REPO}/pull/{number}"
            or (merged and record.get("state") != "closed")
            or (record.get("state") == "closed" and not merged)):
        raise IntegrationGateError("G32_PR_IDENTITY_CHANGED")
    return {
        "pr": number,
        "head_sha": head["sha"],
        "head_branch": branch,
        "base_branch": base["ref"],
        "draft": record["draft"],
        "github_merged": merged,
        "state": record["state"],
    }


def assess_integration(data: Any, prs: Any, *, environment: dict) -> dict:
    pinned = verify_baseline(data)
    if (type(prs) is not list or len(prs) > 400
            or any(type(item) is not dict or type(item.get("number")) is not int
                   for item in prs)):
        raise IntegrationGateError("G32_INVALID_GITHUB_PR_RESPONSE")
    if (environment.get("GITHUB_REPOSITORY") != _REPO
            or not _sha(environment.get("GITHUB_SHA"))
            or type(environment.get("GITHUB_REF")) is not str
            or environment.get("GITHUB_EVENT_NAME") not in
               ("push", "pull_request", "workflow_dispatch")
            or environment.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise IntegrationGateError("G32_UNTRUSTED_RUN_CONTEXT")

    index = {}
    for item in prs:
        number = item["number"]
        if number in index:
            raise IntegrationGateError("G32_DUPLICATE_PR")
        index[number] = item
    expected = pinned + [{"pr": 82, "branch": data["self_branch"], "head_sha": None}]
    if any(x["pr"] not in index for x in expected):
        raise IntegrationGateError("G32_REQUIRED_PR_MISSING")
    rows = [_safe_pr(index[x["pr"]], x["pr"], x["branch"], x["head_sha"])
            for x in expected]
    main_run = (environment["GITHUB_EVENT_NAME"] == "push"
                and environment["GITHUB_REF"] == "refs/heads/main")
    # A future #84+ cannot silently disappear from this pinned release gate.
    # A new milestone requires review and re-pinning before main can pass.
    unexpected_new = sorted(number for number in index
                            if number >= 84 and number % 2 == 0)
    if main_run and unexpected_new:
        raise IntegrationGateError("G32_NEW_PR_NOT_IN_BASELINE")
    if environment["GITHUB_REF"] != "refs/heads/main" and (
        environment["GITHUB_REF"] !=
        "refs/heads/feat/g35-full-stack-release-gates"
        and not environment["GITHUB_REF"].startswith("refs/pull/")):
        raise IntegrationGateError("G32_WRONG_BRANCH")
    if (environment["GITHUB_REF"] == "refs/heads/main"
            and environment["GITHUB_EVENT_NAME"] != "push"):
        raise IntegrationGateError("G32_NOT_A_MAIN_PUSH")

    # On main, all 35 audited PRs must have been merged to main with
    # current base metadata. GitHub merge records are NOT proof of executable
    # correctness, acceptance, or complete data provenance.
    if main_run:
        incomplete = [r["pr"] for r in rows if (
            not r["github_merged"] or r["state"] != "closed"
            or r["base_branch"] != "main"
        )]
        if incomplete:
            raise IntegrationGateError("G32_INCOMPLETE_MAIN_MERGE_CHAIN")
        status = "G32_GITHUB_MAIN_MERGE_METADATA_VERIFIED"
    else:
        if not all(r["state"] == "open" and r["draft"] and not r["github_merged"]
                   for r in rows):
            # Feature CI may run after some real merges; remain a HOLD rather
            # than falsely reporting that main is incomplete or ready.
            status = "G32_FEATURE_CI_NOT_MAIN_HOLD"
        else:
            for i, row in enumerate(rows):
                previous = "main" if i == 0 else rows[i - 1]["head_branch"]
                if row["base_branch"] != previous:
                    raise IntegrationGateError("G32_BROKEN_PREMERGE_STACK")
            status = "G32_EXPECTED_UNMERGED_STACK_HOLD"
    return {
        "schema_version": 1,
        "status": status,
        "repository": _REPO,
        "scope": "MAIN_INTEGRATION_METADATA_ONLY" if main_run else
                 "BRANCH_REHEARSAL_NOT_MAIN",
        "github_sha": environment["GITHUB_SHA"],
        "github_ref": environment["GITHUB_REF"],
        "checked_prs": len(rows),
        "merged_to_main_count": sum(
            bool(r["github_merged"] and r["base_branch"] == "main") for r in rows),
        "prs": rows,
        "main_integration_metadata_verified": main_run,
        "postmerge_python_js_docker_ci_verified": False,
        "real_mt5_terminal_tested": False,
        "independent_human_review_completed": False,
        "owner_acceptance": "PENDING",
        "generated_tool_admitted": False,
        "execution": "DISABLED",
        "broker_execution": False,
        "eligible_for_demo_or_live": False,
        "decision": "NO_TRADE",
        "limitations": (
            "Authenticated GitHub PR metadata proves recorded target and "
            "merge status only, not source integrity after squash/rebase, "
            "final main test success, reviewer quality or trading readiness."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="g32-integration-gate")
    parser.add_argument("--baseline", default="knowledge/g32_pinned_merge_identities.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        source, out = Path(args.baseline), Path(args.output)
        if (source.is_symlink() or not source.is_file()
                or not 100 <= source.stat().st_size <= 15_000
                or out.is_symlink() or out.exists() or not out.parent.is_dir()):
            raise IntegrationGateError("G32_UNSAFE_FILE")
        baseline = json.loads(source.read_text(encoding="utf-8"))
        env = dict(os.environ)
        if (env.get("GITHUB_REPOSITORY") != _REPO or not _sha(env.get("GITHUB_SHA"))
                or env.get("RUNNER_ENVIRONMENT") != "github-hosted"):
            raise IntegrationGateError("G32_UNTRUSTED_RUN_CONTEXT")
        if subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, timeout=10, check=False).stdout.strip() != env["GITHUB_SHA"]:
            raise IntegrationGateError("G32_CHECKOUT_SHA_MISMATCH")
        report = assess_integration(baseline, fetch_authenticated_pr_stack(),
                                    environment=env)
        with out.open("x", encoding="utf-8") as file:
            json.dump(report, file, sort_keys=True, indent=2)
            file.write("\n")
    except (IntegrationGateError, StackAuditError, OSError, ValueError,
            UnicodeError, TypeError, KeyError, subprocess.TimeoutExpired):
        raise SystemExit("G32_INTEGRATION_GATE_BLOCKED") from None
    print(json.dumps({
        "status": report["status"], "scope": report["scope"],
        "checked_prs": report["checked_prs"],
        "main_integration_metadata_verified":
            report["main_integration_metadata_verified"],
        "decision": report["decision"],
    }, sort_keys=True))
    if report["scope"] != "MAIN_INTEGRATION_METADATA_ONLY":
        raise SystemExit(3)  # HOLD; not release authorization


if __name__ == "__main__":
    main()
