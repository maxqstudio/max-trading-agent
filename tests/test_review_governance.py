"""G26: GitHub-authenticated human review preview, never a permission grant."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from max_trading_agent.review_governance import (
    _github_json, _pages, assess_human_review,
    fetch_authenticated_pr_facts,
)
from max_trading_agent.tool_factory import FactoryError, validate_candidate

FIXTURE = Path(__file__).resolve().parent / "fixtures/tool_factory_range_span.json"
SHA = "a" * 40
REPO = "maxqstudio/max-trading-agent"


def human(login: str, kind: str = "User"):
    return {"login": login, "type": kind}


def pr_meta(author="maxqstudio", merged=False, draft=True, sha=SHA):
    return {
        "number": 62, "state": "closed" if merged else "open",
        "draft": draft, "merged": merged,
        "merged_at": "2026-11-17T14:00:00Z" if merged else None,
        "user": human(author),
        "head": {"sha": sha, "repo": {"full_name": REPO}},
        "base": {"repo": {"full_name": REPO}},
        "html_url": "https://github.com/maxqstudio/max-trading-agent/pull/62",
    }


def review(login="independent-reviewer", state="APPROVED", sha=SHA, rid=4,
           time="2026-11-17T12:00:00Z", association="COLLABORATOR", kind="User"):
    return {
        "id": rid, "user": human(login, kind),
        "commit_id": sha, "state": state, "submitted_at": time,
        "author_association": association,
    }


def owner_comment(candidate_sha, pr_sha=SHA, login="maxqstudio",
                  time="2026-11-17T13:00:00Z"):
    return {
        "user": human(login), "created_at": time,
        "author_association": "OWNER",
        "body": f"/accept-research-t1 {candidate_sha} {pr_sha}",
    }


def signed_link(candidate_sha):
    return {
        "status": "SIGNED_WORKFLOW_EVIDENCE_LINKED_REVIEW_PENDING",
        "candidate_sha256": candidate_sha,
        "cryptographic_attestation_verified": True,
        "signed_claim_matches_g23_and_g21": True,
        "tool_admitted": False, "decision": "NO_TRADE",
        "execution": "DISABLED",
    }


class GitHubReviewGateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.candidate = json.loads(FIXTURE.read_text())
        self.sha = validate_candidate(self.candidate)["sha256"]

    def evaluate(self, **kwargs):
        cfg = {
            "candidate": self.candidate,
            "expected_head_sha": SHA,
            "pr": pr_meta(),
            "reviews": [],
            "comments": [],
        }
        cfg.update(kwargs)
        return assess_human_review(**cfg)

    def test_current_unmerged_draft_no_reviewer_is_all_hold_and_no_trade(self):
        out = self.evaluate()
        self.assertEqual(out["status"], "G26_REVIEW_GATES_RECORDED_NO_ADMISSION")
        self.assertFalse(out["gates"]["signed_t0_provenance"])
        self.assertFalse(out["gates"]["independent_human_review"])
        self.assertFalse(out["gates"]["owner_sha_scoped_acceptance"])
        self.assertFalse(out["gates"]["merged_and_closed"])
        self.assertFalse(out["gates"]["not_draft"])
        self.assertEqual(out["candidate_sha256"], self.sha)
        self.assertEqual(out["tier"], "T0_REVIEW_GATE_ONLY")
        self.assertEqual(out["decision"], "NO_TRADE")
        self.assertEqual(out["execution"], "DISABLED")
        self.assertFalse(out["tool_admitted"])
        self.assertFalse(out["t1_registry_enabled"])
        self.assertFalse(out["eligible_for_demo_or_live"])
        self.assertFalse(out["candidate_verified"])
        self.assertEqual(out["owner_acceptance"], "PENDING")

    def test_even_full_authenticated_review_owner_merge_and_signed_link_no_t1(self):
        out = self.evaluate(
            pr=pr_meta(merged=True, draft=False),
            reviews=[review()],
            comments=[owner_comment(self.sha)],
            linked_provenance=signed_link(self.sha),
        )
        self.assertTrue(all(out["gates"].values()))
        self.assertEqual(out["blockers"], [])
        self.assertEqual(out["active_independent_reviewers"],
                         ["independent-reviewer"])
        self.assertEqual(out["owner_acceptance"], "EVIDENCED_NOT_DEPLOYED")
        self.assertFalse(out["tool_admitted"])
        self.assertFalse(out["t1_registry_enabled"])
        self.assertEqual(out["execution"], "DISABLED")
        self.assertEqual(out["decision"], "NO_TRADE")

    def test_review_from_pr_author_or_owner_cannot_be_independent(self):
        for actor in ("maxqstudio", "MAXQSTUDIO"):
            with self.subTest(actor=actor):
                out = self.evaluate(
                    reviews=[review(actor)],
                    comments=[owner_comment(self.sha)],
                )
                self.assertFalse(out["gates"]["independent_human_review"])
                self.assertFalse(out["gates"]["owner_sha_scoped_acceptance"])

    def test_bot_untrusted_contributor_or_fake_association_does_not_approve(self):
        for association, kind in (
            ("CONTRIBUTOR", "User"), ("FIRST_TIME_CONTRIBUTOR", "User"),
            ("COLLABORATOR", "Bot"), ("CONTRIBUTOR", "Bot"),
        ):
            with self.subTest(association=association, kind=kind):
                out = self.evaluate(reviews=[
                    review("external", association=association, kind=kind)])
                self.assertFalse(out["gates"]["independent_human_review"])

    def test_outdated_review_sha_cannot_cover_new_commit(self):
        out = self.evaluate(reviews=[review(sha="f" * 40)])
        self.assertFalse(out["gates"]["independent_human_review"])

    def test_latest_review_change_request_supersedes_prior_approval(self):
        out = self.evaluate(reviews=[
            review(rid=1),
            review(state="CHANGES_REQUESTED", rid=2,
                   time="2026-11-17T12:30:00Z"),
        ])
        self.assertFalse(out["gates"]["independent_human_review"])
        self.assertEqual(out["active_independent_reviewers"], [])

    def test_latest_review_dismissal_revokes_prior_approval(self):
        out = self.evaluate(reviews=[
            review(rid=1),
            review(state="DISMISSED", rid=2,
                   time="2026-11-17T12:30:00Z"),
        ])
        self.assertFalse(out["gates"]["independent_human_review"])

    def test_outdated_comment_before_latest_approval_cannot_accept(self):
        out = self.evaluate(
            reviews=[review(time="2026-11-17T13:30:00Z")],
            comments=[owner_comment(self.sha, time="2026-11-17T13:00:00Z")])
        self.assertTrue(out["gates"]["independent_human_review"])
        self.assertFalse(out["gates"]["owner_sha_scoped_acceptance"])

    def test_owner_comment_must_match_both_sha_exactly(self):
        for comment in (
            owner_comment("0" * 64),
            owner_comment(self.sha, "0" * 40),
            {**owner_comment(self.sha), "body": "/accept-research-t1 " + self.sha + " " + SHA + " extra"},
            {**owner_comment(self.sha), "body": "please /accept-research-t1 " + self.sha + " " + SHA},
            {**owner_comment(self.sha), "body": " /accept-research-t1 " + self.sha + " " + SHA},
        ):
            with self.subTest(comment=str(comment)[:50]):
                out = self.evaluate(reviews=[review()], comments=[comment])
                self.assertFalse(out["gates"]["owner_sha_scoped_acceptance"])

    def test_forged_owner_or_non_owner_author_association_rejected(self):
        for row in (
            owner_comment(self.sha, login="attacker"),
            {**owner_comment(self.sha), "author_association": "CONTRIBUTOR"},
            {**owner_comment(self.sha), "user": human("maxqstudio", "Bot")},
        ):
            with self.subTest(row=str(row)[:40]):
                out = self.evaluate(reviews=[review()], comments=[row])
                self.assertFalse(out["gates"]["owner_sha_scoped_acceptance"])

    def test_g25_link_must_be_bound_to_exact_candidate_and_safe_fields(self):
        for key, value in (
            ("candidate_sha256", "0" * 64),
            ("status", "APPROVED"),
            ("tool_admitted", True),
            ("cryptographic_attestation_verified", False),
            ("decision", "BUY"),
            ("execution", "ENABLED"),
        ):
            link = signed_link(self.sha)
            link[key] = value
            with self.subTest(field=key):
                out = self.evaluate(linked_provenance=link)
                self.assertFalse(out["gates"]["signed_t0_provenance"])

    def test_wrong_github_pr_repo_author_sha_or_url_fails_closed(self):
        changes = (
            ("head", {"sha": "0" * 40, "repo": {"full_name": REPO}}),
            ("head", {"sha": SHA, "repo": {"full_name": "attacker/repo"}}),
            ("base", {"repo": {"full_name": "attacker/repo"}}),
            ("user", human("evil", "Bot")),
            ("html_url", "https://example.com/62"),
            ("state", "draft"),
        )
        for key, value in changes:
            data = pr_meta()
            data[key] = value
            with self.subTest(field=key):
                with self.assertRaisesRegex(FactoryError, "G26_PR_IDENTITY_MISMATCH"):
                    self.evaluate(pr=data)

    def test_missing_or_untrusted_gh_api_denied_and_never_fallback(self):
        with patch("max_trading_agent.review_governance.subprocess.run",
                   return_value=type("Result", (), {"returncode":1, "stdout":""})()):
            with self.assertRaisesRegex(FactoryError, "G26_GITHUB_API_UNAVAILABLE"):
                fetch_authenticated_pr_facts(62)
        with patch("max_trading_agent.review_governance.subprocess.run",
                   side_effect=OSError("not installed")):
            with self.assertRaisesRegex(FactoryError, "G26_GITHUB_API_UNAVAILABLE"):
                fetch_authenticated_pr_facts(62)

    def test_only_allowlisted_github_api_paths_no_user_url_or_shell(self):
        paths = []
        def fn(cmd, **kwargs):
            paths.append(cmd)
            suffix = cmd[-1]
            data = (pr_meta() if suffix.endswith("/pulls/62")
                    else [] )
            return type("Result", (), {"returncode":0, "stdout":json.dumps(data)})()
        with patch("max_trading_agent.review_governance.subprocess.run",
                   side_effect=fn):
            pr, reviews, comments = fetch_authenticated_pr_facts(62)
        self.assertEqual(pr["number"], 62)
        self.assertEqual(reviews, [])
        self.assertEqual(comments, [])
        self.assertEqual(len(paths), 3)
        self.assertTrue(all(cmd[:2] == ["gh", "api"] for cmd in paths))
        self.assertTrue(all(cmd[2].startswith("repos/" + REPO + "/") for cmd in paths))

    def test_invalid_numbers_are_denied_before_gh(self):
        for number in (0, -1, 1000001, True, "62"):
            with self.subTest(number=number), patch(
                "max_trading_agent.review_governance.subprocess.run"
            ) as gh:
                with self.assertRaisesRegex(FactoryError, "G26_INVALID_PR"):
                    fetch_authenticated_pr_facts(number)
                gh.assert_not_called()

    def test_pagination_is_explicit_and_bounded(self):
        with patch("max_trading_agent.review_governance._github_json",
                   side_effect=([{"id": x} for x in range(100)],
                                [{"id": 101}])) as fetch:
            out = _pages("repos/" + REPO + "/issues/62/comments")
        self.assertEqual(len(out), 101)
        self.assertEqual(fetch.call_count, 2)

    def test_cli_does_not_accept_caller_supplied_reviews_or_signed_link(self):
        from max_trading_agent.review_governance import main
        with patch("sys.argv", ["g26-review-gate", "--help"]):
            with self.assertRaises(SystemExit) as help_exit:
                main()
        self.assertEqual(help_exit.exception.code, 0)

    def test_ci_preview_on_draft_pr_records_no_false_pass(self):
        data = pr_meta()
        out = self.evaluate(pr=data)
        self.assertIn("SIGNED_T0_PROVENANCE_PENDING", out["blockers"])
        self.assertIn("MERGED_AND_CLOSED_PENDING", out["blockers"])
        self.assertIn("INDEPENDENT_HUMAN_REVIEW_PENDING", out["blockers"])
        self.assertEqual(out["status"], "G26_REVIEW_GATES_RECORDED_NO_ADMISSION")


if __name__ == "__main__":
    unittest.main()
