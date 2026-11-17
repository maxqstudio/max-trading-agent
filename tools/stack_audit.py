"""G28: read-only audit of the canonical stacked Draft PR chain.

This produces a GitHub-only owner merge handoff. It NEVER merges, retargets,
approves or creates trading permissions. Current source is the authenticated
GitHub PR REST endpoint, not user/LLM-provided merge status.
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
_REQUIRED = tuple(range(14, 68, 2))  # PRs M01 through G27, 27 stacked PRs
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_BRANCH = re.compile(r"feat/[a-z0-9][a-z0-9/_-]{0,100}\Z")


class StackAuditError(ValueError):
    """Safe bounded error code; no GitHub token, PR body or secrets."""


def _github_pr_page(page: int) -> list[dict]:
    if type(page) is not int or not 1 <= page <= 4:
        raise StackAuditError("G28_INVALID_PAGE")
    path = f"repos/{_REPO}/pulls?state=all&per_page=100&page={page}"
    try:
        result = subprocess.run(["gh", "api", path], capture_output=True,
                                text=True, timeout=40, check=False)
        if result.returncode != 0 or len(result.stdout) > 4_000_000:
            raise ValueError("github unavailable")
        data = json.loads(result.stdout)
        if type(data) is not list or any(type(item) is not dict for item in data):
            raise ValueError("unexpected schema")
        return data
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise StackAuditError("G28_GITHUB_API_UNAVAILABLE") from None


def fetch_authenticated_pr_stack() -> list[dict]:
    """GitHub REST only. Fetch all pages with a finite bound."""
    collected: list[dict] = []
    for page in range(1, 5):
        rows = _github_pr_page(page)
        collected.extend(rows)
        if len(rows) < 100:
            return collected
    raise StackAuditError("G28_GITHUB_API_PAGINATION_LIMIT")


def _validate_pr(pr: Any, number: int) -> dict:
    if type(pr) is not dict or type(pr.get("number")) is not int or pr["number"] != number:
        raise StackAuditError("G28_INVALID_PR_METADATA")
    head, base = pr.get("head"), pr.get("base")
    if type(head) is not dict or type(base) is not dict:
        raise StackAuditError("G28_INVALID_PR_METADATA")
    head_repo, base_repo = head.get("repo"), base.get("repo")
    sha, head_ref, base_ref = head.get("sha"), head.get("ref"), base.get("ref")
    if (type(head_repo) is not dict or head_repo.get("full_name") != _REPO
            or type(base_repo) is not dict or base_repo.get("full_name") != _REPO
            or type(sha) is not str or not _SHA.fullmatch(sha)
            or type(head_ref) is not str or not _BRANCH.fullmatch(head_ref)
            or type(base_ref) is not str
            or pr.get("state") not in ("open", "closed")
            or type(pr.get("draft")) is not bool
            or pr.get("html_url") != f"https://github.com/{_REPO}/pull/{number}"):
        raise StackAuditError("G28_INVALID_PR_METADATA")
    merged = pr.get("merged_at") is not None
    if (merged and (pr.get("state") != "closed"
                    or type(pr["merged_at"]) is not str)
            or pr["state"] == "closed" and not merged):
        raise StackAuditError("G28_CLOSED_UNMERGED_OR_INVALID")
    return {
        "number": number,
        "url": pr["html_url"],
        "head_branch": head_ref,
        "head_sha": sha,
        "current_base": base_ref,
        "draft": pr["draft"],
        "merged": merged,
    }


def audit_stack(prs: Any) -> dict:
    """Only metadata consistency; never reads/changes runtime or GitHub state."""
    if type(prs) is not list or len(prs) > 400:
        raise StackAuditError("G28_INVALID_PR_LIST")
    index: dict[int, dict] = {}
    for item in prs:
        if type(item) is not dict or type(item.get("number")) is not int:
            raise StackAuditError("G28_INVALID_PR_LIST")
        number = item["number"]
        if number in index:
            raise StackAuditError("G28_DUPLICATE_PR")
        index[number] = item
    missing = [number for number in _REQUIRED if number not in index]
    if missing:
        raise StackAuditError("G28_REQUIRED_PR_MISSING")
    numbers = list(_REQUIRED)
    if 68 in index:
        numbers.append(68)
    records = [_validate_pr(index[number], number) for number in numbers]
    if records[0]["current_base"] != "main":
        raise StackAuditError("G28_FIRST_BASE_NOT_MAIN")
    if len({record["head_branch"] for record in records}) != len(records):
        raise StackAuditError("G28_DUPLICATE_HEAD_BRANCH")

    found_unmerged = False
    for index_number, row in enumerate(records):
        if found_unmerged and row["merged"]:
            raise StackAuditError("G28_OUT_OF_ORDER_MERGE")
        found_unmerged |= not row["merged"]
        if index_number == 0:
            row["base_relationship"] = "MAIN_ROOT"
            row["retarget_to_main_before_merging"] = False
            continue
        previous = records[index_number - 1]
        original_base = previous["head_branch"]
        current_base = row["current_base"]
        if current_base not in (original_base, "main"):
            raise StackAuditError("G28_BROKEN_STACK_BASE")
        if current_base == original_base and previous["merged"]:
            row["base_relationship"] = "REQUIRES_RETARGET_TO_MAIN"
        elif current_base == "main" and not previous["merged"]:
            row["base_relationship"] = "RETARGETED_BEFORE_PREDECESSOR_MERGE"
        elif current_base == "main":
            row["base_relationship"] = "MAIN_AFTER_PREDECESSOR_MERGE"
        else:
            row["base_relationship"] = "STACKED_ON_PREVIOUS_FEATURE"
        row["retarget_to_main_before_merging"] = (
            not row["merged"] and current_base != "main"
        )

    first_open = next((row for row in records if not row["merged"]), None)
    if first_open is None:
        next_action = "ALL_PR_MERGED_RECHECK_RUNTIME_AND_OWNER_GATES"
    elif first_open["current_base"] != "main":
        next_action = "RETARGET_NEXT_PR_BASE_TO_MAIN_AND_RERUN_CI"
    elif first_open["draft"]:
        next_action = "OWNER_REVIEW_NEXT_PR_AND_MARK_READY_WHEN_APPROPRIATE"
    else:
        next_action = "REVIEW_DIFF_INDEPENDENT_APPROVAL_AND_RERUN_CI_BEFORE_MERGE"

    report = {
        "schema_version": 1,
        "status": "OWNER_MERGE_HANDOFF_ACTION_REQUIRED",
        "source": "GITHUB_PR_API_READ_ONLY",
        "repository": _REPO,
        "default_branch": "main",
        "audited_count": len(records),
        "merged_count": sum(x["merged"] for x in records),
        "unmerged_count": sum(not x["merged"] for x in records),
        "ordered_pull_requests": records,
        "next_merge_pr": first_open["number"] if first_open else None,
        "next_merge_pr_url": first_open["url"] if first_open else None,
        "next_action": next_action,
        "merge_target_required": "main",
        "owner_merge_steps": [
            "Merge only in ascending dependency order, starting with PR #14.",
            "Before each later merge, retarget that PR base to main once its predecessor has merged.",
            "Inspect the changed diff and obtain fresh GitHub CI plus required human reviews after retargeting.",
            "Never merge an intermediate PR into the previous feature branch if the intention is main.",
            "Do not interpret synthetic CI as real MT5 terminal, risk or broker acceptance.",
        ],
        "branch_protection_verified": False,
        "github_current_ci_conclusion_verified_by_this_audit": False,
        "independent_source_review_completed": False,
        "local_mt5_terminal_acceptance_completed": False,
        "owner_acceptance": "PENDING",
        "demo_or_live_authorized": False,
        "execution": "DISABLED",
        "broker_execution": False,
        "decision": "NO_TRADE",
        "limitations": (
            "PR API facts identify stacked branches, not correctness, signed "
            "CI provenance, human code quality, broker readiness or profitability. "
            "GitHub branch protection was not verifiable with this integration."
        ),
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(prog="g28-owner-merge-handoff")
    parser.add_argument("--output", help="Optional write-once local JSON report")
    args = parser.parse_args()
    try:
        report = audit_stack(fetch_authenticated_pr_stack())
        if args.output:
            target = Path(args.output)
            if target.is_symlink() or target.exists():
                raise StackAuditError("G28_OUTPUT_ALREADY_EXISTS")
            with target.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2, sort_keys=True)
                stream.write("\n")
    except (StackAuditError, ValueError, TypeError, OSError, UnicodeError):
        raise SystemExit("G28_AUDIT_BLOCKED") from None
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
