"""G28: GitHub PR stack topology, safe retarget and owner handoff tests."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.stack_audit import (
    StackAuditError, _github_pr_page, audit_stack,
    fetch_authenticated_pr_stack,
)

REPO = "maxqstudio/max-trading-agent"


def fake_pr(number: int, base: str, *, merged=False, draft=True) -> dict:
    return {
        "number": number,
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "head": {
            "ref": f"feat/stage-{number}",
            "sha": f"{number:040x}",
            "repo": {"full_name": REPO},
        },
        "base": {"ref": base, "repo": {"full_name": REPO}},
        "state": "closed" if merged else "open",
        "draft": draft,
        "merged_at": "2026-11-17T00:00:00Z" if merged else None,
    }


def fake_stack(*, include_future=False) -> list[dict]:
    numbers = list(range(14, 84, 2))
    if include_future:
        numbers.append(84)
    items = []
    for index, number in enumerate(numbers):
        base = "main" if index == 0 else f"feat/stage-{numbers[index - 1]}"
        items.append(fake_pr(number, base))
    return items


class StackedMergeHandoffTests(unittest.TestCase):
    def setUp(self):
        self.prs = fake_stack()

    def test_open_stacked_draft_chain_includes_all_35_prs(self):
        result = audit_stack(self.prs)
        self.assertEqual(result["audited_count"], 35)
        self.assertEqual(result["merged_count"], 0)
        self.assertEqual(result["unmerged_count"], 35)
        self.assertEqual(result["next_merge_pr"], 14)
        self.assertEqual(result["merge_target_required"], "main")
        self.assertEqual(result["next_action"],
                         "OWNER_REVIEW_NEXT_PR_AND_MARK_READY_WHEN_APPROPRIATE")
        self.assertEqual(result["ordered_pull_requests"][0]["current_base"], "main")
        self.assertEqual(result["ordered_pull_requests"][1]["base_relationship"],
                         "STACKED_ON_PREVIOUS_FEATURE")
        self.assertTrue(result["ordered_pull_requests"][1]["retarget_to_main_before_merging"])
        self.assertFalse(result["branch_protection_verified"])
        self.assertFalse(result["local_mt5_terminal_acceptance_completed"])
        self.assertFalse(result["demo_or_live_authorized"])
        self.assertEqual(result["decision"], "NO_TRADE")
        self.assertEqual(result["execution"], "DISABLED")

    def test_future_pr_appended_without_silent_truncation(self):
        future = audit_stack(fake_stack(include_future=True))
        self.assertEqual(future["audited_count"], 36)
        self.assertEqual(future["ordered_pull_requests"][-1]["number"], 84)
        self.assertEqual(future["ordered_pull_requests"][-1]["current_base"],
                         "feat/stage-82")

    def test_deleted_head_repo_only_allowed_for_github_merged_pr(self):
        self.prs[0] = fake_pr(14, "main", merged=True, draft=False)
        self.prs[0]["head"]["repo"] = None
        self.prs[1]["base"]["ref"] = "main"
        result = audit_stack(self.prs)
        self.assertEqual(result["merged_count"], 1)
        self.assertEqual(result["next_merge_pr"], 16)
        self.assertEqual(result["next_action"],
                         "OWNER_REVIEW_NEXT_PR_AND_MARK_READY_WHEN_APPROPRIATE")
        self.assertEqual(result["decision"], "NO_TRADE")

    def test_deleted_head_repo_without_merge_record_is_rejected(self):
        self.prs[0]["head"]["repo"] = None
        with self.assertRaisesRegex(StackAuditError, "G28_INVALID_PR_METADATA"):
            audit_stack(self.prs)

    def test_deleted_head_repo_with_fake_or_incomplete_merge_is_rejected(self):
        self.prs[0] = fake_pr(14, "main", merged=True, draft=False)
        self.prs[0]["head"]["repo"] = None
        self.prs[0]["merged_at"] = 123
        with self.assertRaisesRegex(StackAuditError, "G28_CLOSED_UNMERGED_OR_INVALID"):
            audit_stack(self.prs)
        self.prs[0]["merged_at"] = None
        with self.assertRaisesRegex(StackAuditError, "G28_INVALID_PR_METADATA"):
            audit_stack(self.prs)

    def test_next_pr_must_be_retargeted_to_main_after_predecessor_merged(self):
        self.prs[0] = fake_pr(14, "main", merged=True, draft=False)
        result = audit_stack(self.prs)
        self.assertEqual(result["merged_count"], 1)
        self.assertEqual(result["next_merge_pr"], 16)
        self.assertEqual(result["next_action"],
                         "RETARGET_NEXT_PR_BASE_TO_MAIN_AND_RERUN_CI")
        self.assertEqual(result["ordered_pull_requests"][1]["base_relationship"],
                         "REQUIRES_RETARGET_TO_MAIN")

    def test_retarget_after_merging_previous_unlocks_next_review_not_merge(self):
        self.prs[0] = fake_pr(14, "main", merged=True, draft=False)
        self.prs[1]["base"]["ref"] = "main"
        result = audit_stack(self.prs)
        self.assertEqual(result["next_merge_pr"], 16)
        self.assertEqual(result["next_action"],
                         "OWNER_REVIEW_NEXT_PR_AND_MARK_READY_WHEN_APPROPRIATE")
        self.assertEqual(result["ordered_pull_requests"][1]["base_relationship"],
                         "MAIN_AFTER_PREDECESSOR_MERGE")
        self.assertFalse(result["ordered_pull_requests"][1]["retarget_to_main_before_merging"])

    def test_early_retarget_to_main_is_visible_without_auto_merging(self):
        self.prs[1]["base"]["ref"] = "main"
        result = audit_stack(self.prs)
        self.assertEqual(result["ordered_pull_requests"][1]["base_relationship"],
                         "RETARGETED_BEFORE_PREDECESSOR_MERGE")
        self.assertEqual(result["next_merge_pr"], 14)

    def test_out_of_order_merge_is_a_hard_error(self):
        self.prs[1] = fake_pr(16, "feat/stage-14", merged=True)
        with self.assertRaisesRegex(StackAuditError, "G28_OUT_OF_ORDER_MERGE"):
            audit_stack(self.prs)

    def test_closed_unmerged_pr_blocks_handoff(self):
        self.prs[5]["state"] = "closed"
        with self.assertRaisesRegex(StackAuditError,
                                    "G28_CLOSED_UNMERGED_OR_INVALID"):
            audit_stack(self.prs)

    def test_missing_required_pr_blocks_handoff(self):
        self.prs.pop()
        with self.assertRaisesRegex(StackAuditError, "G28_REQUIRED_PR_MISSING"):
            audit_stack(self.prs)

    def test_missing_latest_g34_or_g35_pr_blocks_handoff(self):
        for number in (80, 82):
            remaining = [x for x in fake_stack() if x["number"] != number]
            with self.subTest(number=number), self.assertRaisesRegex(
                StackAuditError, "G28_REQUIRED_PR_MISSING"):
                audit_stack(remaining)

    def test_gap_before_future_canonical_pr_is_denied(self):
        items = fake_stack(include_future=True)
        items = [x for x in items if x["number"] != 82]
        with self.assertRaisesRegex(StackAuditError, "G28_REQUIRED_PR_MISSING"):
            audit_stack(items)

    def test_unexpected_odd_future_pr_is_not_silently_omitted(self):
        items = fake_stack()
        items.append(fake_pr(83, "feat/stage-82"))
        with self.assertRaisesRegex(StackAuditError, "G28_UNEXPECTED_NEW_PR"):
            audit_stack(items)

    def test_duplicate_pr_number_blocks_handoff(self):
        self.prs.append(dict(self.prs[-1]))
        with self.assertRaisesRegex(StackAuditError, "G28_DUPLICATE_PR"):
            audit_stack(self.prs)

    def test_broken_base_chain_blocks_handoff(self):
        self.prs[2]["base"]["ref"] = "feat/unrelated-branch"
        with self.assertRaisesRegex(StackAuditError, "G28_BROKEN_STACK_BASE"):
            audit_stack(self.prs)

    def test_wrong_first_base_never_targets_feature_instead_of_main(self):
        self.prs[0]["base"]["ref"] = "feat/sneaky"
        with self.assertRaisesRegex(StackAuditError, "G28_FIRST_BASE_NOT_MAIN"):
            audit_stack(self.prs)

    def test_foreign_repo_or_fork_metadata_denied(self):
        for record_index, attribute in ((1, "head"), (3, "base")):
            items = fake_stack()
            items[record_index][attribute]["repo"]["full_name"] = "evil/fork"
            with self.subTest(attribute=attribute), self.assertRaisesRegex(
                StackAuditError, "G28_INVALID_PR_METADATA"):
                audit_stack(items)

    def test_invalid_head_sha_or_branch_or_url_denied(self):
        for field, value in (
            ("sha", "z" * 40),
            ("sha", "1" * 39),
            ("ref", "main"),
            ("ref", "feat/Untrusted Uppercase"),
        ):
            items = fake_stack()
            items[6]["head"][field] = value
            with self.subTest(field=field, value=value[:12]), self.assertRaisesRegex(
                StackAuditError, "G28_INVALID_PR_METADATA"):
                audit_stack(items)
        items = fake_stack()
        items[1]["html_url"] = "https://example.org/16"
        with self.assertRaisesRegex(StackAuditError, "G28_INVALID_PR_METADATA"):
            audit_stack(items)

    def test_github_api_unavailable_fails_closed(self):
        with patch("tools.stack_audit.subprocess.run",
                   return_value=SimpleNamespace(returncode=401, stdout="")):
            with self.assertRaisesRegex(StackAuditError,
                                        "G28_GITHUB_API_UNAVAILABLE"):
                fetch_authenticated_pr_stack()
        with patch("tools.stack_audit.subprocess.run",
                   side_effect=OSError("gh not installed")):
            with self.assertRaisesRegex(StackAuditError,
                                        "G28_GITHUB_API_UNAVAILABLE"):
                fetch_authenticated_pr_stack()

    def test_bounded_github_api_path_and_paginated_traversal(self):
        seen = []
        def mock(cmd, **kwargs):
            seen.append(cmd)
            return SimpleNamespace(returncode=0, stdout=json.dumps(fake_stack()))
        with patch("tools.stack_audit.subprocess.run", side_effect=mock):
            data = fetch_authenticated_pr_stack()
        self.assertEqual(len(data), 35)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][:2], ["gh", "api"])
        self.assertEqual(seen[0][2],
                         "repos/maxqstudio/max-trading-agent/pulls?state=all&per_page=100&page=1")

    def test_page_schema_corruption_is_rejected(self):
        for raw in ("{}", "not-json", '[{"ok":1},4]'):
            with self.subTest(raw=raw), patch("tools.stack_audit.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout=raw)):
                with self.assertRaisesRegex(StackAuditError,
                                            "G28_GITHUB_API_UNAVAILABLE"):
                    _github_pr_page(1)

    def test_handoff_text_never_claims_ci_or_branch_protection_verified(self):
        data = audit_stack(self.prs)
        self.assertFalse(data["github_current_ci_conclusion_verified_by_this_audit"])
        self.assertFalse(data["independent_source_review_completed"])
        self.assertEqual(data["owner_acceptance"], "PENDING")
        self.assertIn("retarget", " ".join(data["owner_merge_steps"]).lower())
        self.assertIn("rerun CI", " ".join(data["owner_merge_steps"]))

    def test_cli_help_is_non_mutating(self):
        run = subprocess.run([sys.executable, "tools/stack_audit.py", "--help"],
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr[-200:])
        self.assertIn("g28-owner-merge-handoff", run.stdout)


if __name__ == "__main__":
    unittest.main()
