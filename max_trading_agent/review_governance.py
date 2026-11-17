"""G26 — authenticated GitHub human-review gate, strictly no tool admission.

GitHub API records are fetched with read-only `gh api` by the public CLI;
caller-provided JSON and LLM text are NEVER treated as authenticated approvals.
Even complete reviews record evidence only, not T1 execution/trading rights.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .tool_factory import FactoryError, validate_candidate
from .tool_review import _digest, _write_receipt

_REPO = "maxqstudio/max-trading-agent"
_OWNER = "maxqstudio"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_ALLOW_ASSOCIATIONS = frozenset({"COLLABORATOR", "MEMBER", "OWNER"})
_QUALIFIED_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED", "COMMENTED", "PENDING"})


def _iso(value: Any) -> datetime | None:
    if type(value) is not str:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except ValueError:
        return None


def _github_json(path: str) -> Any:
    """Use GitHub's own authenticated API; never shell or accept input URLs."""
    try:
        process = subprocess.run(["gh", "api", path], capture_output=True,
                                 text=True, timeout=30, check=False)
        if process.returncode or len(process.stdout) > 500_000:
            raise ValueError("unavailable")
        return json.loads(process.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise FactoryError("G26_GITHUB_API_UNAVAILABLE") from None


def _pages(path: str) -> list[dict]:
    output: list[dict] = []
    for page in range(1, 21):
        batch = _github_json(f"{path}?per_page=100&page={page}")
        if type(batch) is not list or any(type(x) is not dict for x in batch):
            raise FactoryError("G26_GITHUB_API_INVALID")
        output.extend(batch)
        if len(batch) < 100:
            return output
    raise FactoryError("G26_GITHUB_API_LIMIT")


def fetch_authenticated_pr_facts(number: int) -> tuple[dict, list[dict], list[dict]]:
    """No caller-provided snapshots on the production CLI."""
    if type(number) is not int or not 1 <= number <= 1000000:
        raise FactoryError("G26_INVALID_PR")
    prefix = f"repos/{_REPO}"
    pr = _github_json(f"{prefix}/pulls/{number}")
    if type(pr) is not dict or pr.get("number") != number:
        raise FactoryError("G26_GITHUB_API_INVALID")
    reviews = _pages(f"{prefix}/pulls/{number}/reviews")
    comments = _pages(f"{prefix}/issues/{number}/comments")
    return pr, reviews, comments


def _user(record: Any) -> tuple[str, str] | None:
    if type(record) is not dict:
        return None
    login, kind = record.get("login"), record.get("type")
    if (type(login) is str and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37})", login)
            and kind == "User"):
        return login.lower(), kind
    return None


def assess_human_review(
    candidate: dict,
    expected_head_sha: str,
    pr: dict,
    reviews: list[dict],
    comments: list[dict],
    *,
    linked_provenance: dict | None = None,
) -> dict:
    """Evaluate API-authenticated facts, but NEVER treat it as permission.

    The optional signed link must only be constructed via the cryptographic
    G25 verifier in the same trusted caller. Public CLI does not read arbitrary
    'approved' snapshots or linked-provenance JSON.
    """
    identity = validate_candidate(candidate)
    if type(expected_head_sha) is not str or not _SHA.fullmatch(expected_head_sha):
        raise FactoryError("G26_INVALID_HEAD_SHA")
    if (type(pr) is not dict or type(reviews) is not list or type(comments) is not list
            or len(reviews) > 2000 or len(comments) > 2000
            or any(type(x) is not dict for x in reviews + comments)):
        raise FactoryError("G26_GITHUB_API_INVALID")
    head = pr.get("head")
    base = pr.get("base")
    repo = pr.get("base", {}).get("repo", {}) if type(base) is dict else {}
    author = _user(pr.get("user"))
    pr_sha = head.get("sha") if type(head) is dict else None
    if (type(repo) is not dict or repo.get("full_name") != _REPO
            or pr_sha != expected_head_sha or author is None
            or type(head) is not dict
            or type(head.get("repo")) is not dict
            or head["repo"].get("full_name") != _REPO
            or pr.get("state") not in ("open", "closed")
            or type(pr.get("draft")) is not bool
            or type(pr.get("merged")) is not bool
            or pr.get("html_url") !=
            f"https://github.com/{_REPO}/pull/{pr.get('number')}"):
        raise FactoryError("G26_PR_IDENTITY_MISMATCH")

    # Sort GitHub's actual review records; a later dismissal/change-request
    # supersedes any prior approval from the same human.
    last_by_actor: dict[str, dict] = {}
    for row in reviews:
        who = _user(row.get("user"))
        submitted = _iso(row.get("submitted_at"))
        state = row.get("state")
        review_sha = row.get("commit_id")
        if (who is None or submitted is None or state not in _QUALIFIED_STATES
                or type(review_sha) is not str or not _SHA.fullmatch(review_sha)
                or type(row.get("id")) is not int):
            continue
        login = who[0]
        if (login == author[0] or login == _OWNER
                or row.get("author_association") not in _ALLOW_ASSOCIATIONS):
            continue
        current = last_by_actor.get(login)
        if (current is None or (submitted, row["id"]) >
                (current["time"], current["id"])):
            last_by_actor[login] = {
                "time": submitted, "id": row["id"],
                "state": state, "sha": review_sha,
            }
    reviewers = sorted(login for login, value in last_by_actor.items()
                       if value["state"] == "APPROVED"
                       and value["sha"] == expected_head_sha)
    reviewer_times = [last_by_actor[x]["time"] for x in reviewers]
    latest_approval = max(reviewer_times) if reviewer_times else None

    # GitHub-authenticated PR comment is still a *request for human acceptance*,
    # not a cryptographic code review. Require an exact SHA-scoped owner command
    # and that it was made AFTER the newest active independent approval.
    owner_marker = f"/accept-research-t1 {identity['sha256']} {expected_head_sha}"
    owner_ack = False
    if latest_approval is not None:
        for row in comments:
            who = _user(row.get("user"))
            created = _iso(row.get("created_at"))
            if (who is not None and who[0] == _OWNER and created is not None
                    and created >= latest_approval
                    and row.get("author_association") == "OWNER"
                    and row.get("body") == owner_marker):
                owner_ack = True

    provenance_match = (
        type(linked_provenance) is dict
        and linked_provenance.get("status") == "SIGNED_WORKFLOW_EVIDENCE_LINKED_REVIEW_PENDING"
        and linked_provenance.get("candidate_sha256") == identity["sha256"]
        and linked_provenance.get("cryptographic_attestation_verified") is True
        and linked_provenance.get("signed_claim_matches_g23_and_g21") is True
        and linked_provenance.get("tool_admitted") is False
        and linked_provenance.get("decision") == "NO_TRADE"
        and linked_provenance.get("execution") == "DISABLED"
    )
    merged = pr["merged"] is True and _iso(pr.get("merged_at")) is not None
    gates = {
        "github_pr_head_match": True,
        "signed_t0_provenance": provenance_match,
        "independent_human_review": bool(reviewers),
        "owner_sha_scoped_acceptance": owner_ack,
        "merged_and_closed": merged and pr["state"] == "closed",
        "not_draft": not pr["draft"],
    }
    blockers = [key.upper() + "_PENDING" for key, okay in gates.items()
                if not okay]
    report = {
        "schema_version": 1,
        "status": "G26_REVIEW_GATES_RECORDED_NO_ADMISSION",
        "tool_id": identity["tool_id"],
        "tool_version": identity["version"],
        "candidate_sha256": identity["sha256"],
        "review_pr_number": pr["number"],
        "review_pr_head_sha": expected_head_sha,
        "review_pr_author": author[0],
        "active_independent_reviewers": reviewers,
        "owner_acceptance_command": owner_marker,
        "gates": gates,
        "blockers": blockers,
        "reviewer_approval_is_code_safety_proof": False,
        "t1_registry_enabled": False,
        "candidate_verified": False,
        "tool_admitted": False,
        "evidence_only": True,
        "tier": "T0_REVIEW_GATE_ONLY",
        "decision": "NO_TRADE",
        "execution": "DISABLED",
        "broker_execution": False,
        "eligible_for_demo_or_live": False,
        "owner_acceptance": "PENDING" if not owner_ack else "EVIDENCED_NOT_DEPLOYED",
        "limitations": (
            "GitHub review records and an owner comment provide governance "
            "facts, not mathematical correctness, independent code audit or "
            "permission to load candidate Python. No tool is registered."
        ),
    }
    report["review_id"] = _digest(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(prog="g26-review-gate")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--output-dir", default=".local/tool-factory/reviewer-gates")
    args = parser.parse_args()
    try:
        file = Path(args.candidate)
        if (file.is_symlink() or not file.is_file()
                or not 1 <= file.stat().st_size <= 10_000):
            raise FactoryError("G26_INVALID_CANDIDATE")
        candidate = json.loads(file.read_text(encoding="utf-8"))
        pr, reviews, comments = fetch_authenticated_pr_facts(args.pr)
        # The preview cannot accept caller-injected signed link JSON and
        # NEVER trusts an unsigned review/signer assertion.
        report = assess_human_review(
            candidate, args.expected_head, pr, reviews, comments,
            linked_provenance=None)
        _write_receipt(report, Path(args.output_dir))
    except (FactoryError, OSError, ValueError, UnicodeError):
        raise SystemExit("G26_BLOCKED") from None
    print(json.dumps(report, indent=2))
    raise SystemExit(3)  # evidence is not an executable permission


if __name__ == "__main__":
    main()
