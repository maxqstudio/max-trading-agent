"""G32 GitHub API main integration evidence gate adversarial tests."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.main_integration_gate import (
    IntegrationGateError, assess_integration, verify_baseline,
)

BASELINE = Path(__file__).resolve().parents[1] / "knowledge/g32_pinned_merge_identities.json"
REPO = "maxqstudio/max-trading-agent"
SHA = "a" * 40


def env(*, main=False, pull=False):
    return {
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_SHA": SHA,
        "GITHUB_REF": ("refs/heads/main" if main else
                       "refs/pull/76/merge" if pull else
                       "refs/heads/feat/g32-main-integration-completeness"),
        "GITHUB_EVENT_NAME": "push" if not pull else "pull_request",
        "RUNNER_ENVIRONMENT": "github-hosted",
    }


def pr(number, branch, sha, base, *, merged=False, draft=True, repo=REPO):
    return {
        "number": number,
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "head": {"ref": branch, "sha": sha,
                 "repo": {"full_name": repo}},
        "base": {"ref": base, "repo": {"full_name": REPO}},
        "draft": draft,
        "state": "closed" if merged else "open",
        "merged_at": "2026-11-17T14:00:00Z" if merged else None,
    }


def fake_prs(baseline, *, merged=False):
    previous = "main"
    result = []
    for entry in baseline["predecessors"] + [{
        "pr": 76,
        "branch": baseline["self_branch"],
        "head_sha": "b" * 40,
    }]:
        result.append(pr(entry["pr"], entry["branch"], entry["head_sha"],
                         "main" if merged else previous,
                         merged=merged, draft=not merged))
        previous = entry["branch"]
    return result


class G32IntegrationGateTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.prs = fake_prs(self.data)

    def review(self, *, prs=..., baseline=..., environment=...):
        return assess_integration(
            self.data if baseline is ... else baseline,
            self.prs if prs is ... else prs,
            environment=env() if environment is ... else environment,
        )

    def test_exact_snapshot_has_31_pinned_predecessors_and_self_76(self):
        pinned = verify_baseline(self.data)
        self.assertEqual(len(pinned), 31)
        self.assertEqual([row["pr"] for row in pinned],
                         list(range(14, 76, 2)))
        self.assertEqual(self.data["self_pr"], 76)
        self.assertEqual(self.data["decision"], "NO_TRADE")

    def test_feature_branch_all_draft_must_hold_even_with_all_tests(self):
        out = self.review()
        self.assertEqual(out["status"], "G32_EXPECTED_UNMERGED_STACK_HOLD")
        self.assertEqual(out["scope"], "BRANCH_REHEARSAL_NOT_MAIN")
        self.assertEqual(out["checked_prs"], 32)
        self.assertEqual(out["merged_to_main_count"], 0)
        self.assertFalse(out["main_integration_metadata_verified"])
        self.assertFalse(out["postmerge_python_js_docker_ci_verified"])
        self.assertFalse(out["real_mt5_terminal_tested"])
        self.assertFalse(out["generated_tool_admitted"])
        self.assertFalse(out["broker_execution"])
        self.assertEqual(out["owner_acceptance"], "PENDING")
        self.assertEqual(out["execution"], "DISABLED")
        self.assertEqual(out["decision"], "NO_TRADE")

    def test_pull_request_synthetic_commit_must_not_pass_main(self):
        out = self.review(environment=env(pull=True))
        self.assertFalse(out["main_integration_metadata_verified"])
        self.assertEqual(out["scope"], "BRANCH_REHEARSAL_NOT_MAIN")

    def test_only_real_push_main_all_target_main_metadata_passes(self):
        out = self.review(prs=fake_prs(self.data, merged=True),
                          environment=env(main=True))
        self.assertEqual(out["status"], "G32_GITHUB_MAIN_MERGE_METADATA_VERIFIED")
        self.assertEqual(out["merged_to_main_count"], 32)
        self.assertTrue(out["main_integration_metadata_verified"])
        self.assertFalse(out["postmerge_python_js_docker_ci_verified"])
        self.assertFalse(out["real_mt5_terminal_tested"])
        self.assertFalse(out["eligible_for_demo_or_live"])
        self.assertEqual(out["decision"], "NO_TRADE")

    def test_main_push_with_just_one_still_draft_or_unmerged_fails_closed(self):
        for n in (14, 46, 74, 76):
            fake = fake_prs(self.data, merged=True)
            item = next(x for x in fake if x["number"] == n)
            item.update(state="open", draft=True, merged_at=None)
            with self.subTest(pr=n), self.assertRaisesRegex(
                IntegrationGateError, "G32_INCOMPLETE_MAIN_MERGE_CHAIN"):
                self.review(prs=fake, environment=env(main=True))

    def test_foreign_base_main_metadata_cannot_pass(self):
        fake = fake_prs(self.data, merged=True)
        fake[-1]["base"]["ref"] = "feat/g31-postmerge-main-preflight"
        with self.assertRaisesRegex(
                IntegrationGateError, "G32_INCOMPLETE_MAIN_MERGE_CHAIN"):
            self.review(prs=fake, environment=env(main=True))

    def test_closed_unmerged_pr_fails_validation(self):
        fake = copy.deepcopy(self.prs)
        fake[10]["state"] = "closed"
        with self.assertRaisesRegex(IntegrationGateError, "G32_PR_IDENTITY_CHANGED"):
            self.review(prs=fake)

    def test_mismatched_head_sha_fails_even_after_merge(self):
        fake = fake_prs(self.data, merged=True)
        fake[13]["head"]["sha"] = "0" * 40
        with self.assertRaisesRegex(IntegrationGateError, "G32_PR_IDENTITY_CHANGED"):
            self.review(prs=fake, environment=env(main=True))

    def test_wrong_repo_head_or_base_denied(self):
        for key in ("head", "base"):
            fake = copy.deepcopy(self.prs)
            fake[4][key]["repo"]["full_name"] = "attacker/repo"
            with self.subTest(key=key), self.assertRaisesRegex(
                IntegrationGateError, "G32_PR_IDENTITY_CHANGED"):
                self.review(prs=fake)

    def test_missing_required_pr_or_duplicate_number_denied(self):
        with self.assertRaisesRegex(IntegrationGateError, "G32_REQUIRED_PR_MISSING"):
            self.review(prs=self.prs[:-1])
        with self.assertRaisesRegex(IntegrationGateError, "G32_DUPLICATE_PR"):
            self.review(prs=self.prs + [self.prs[-1]])

    def test_broken_premerge_base_chain_denied(self):
        fake = copy.deepcopy(self.prs)
        fake[5]["base"]["ref"] = "main"
        with self.assertRaisesRegex(IntegrationGateError, "G32_BROKEN_PREMERGE_STACK"):
            self.review(prs=fake)

    def test_after_some_real_merge_feature_run_is_still_hold(self):
        fake = copy.deepcopy(self.prs)
        fake[0].update(state="closed", merged_at="2026-11-17T14:00:00Z", draft=False)
        fake[1]["base"]["ref"] = "main"
        out = self.review(prs=fake)
        self.assertEqual(out["status"], "G32_FEATURE_CI_NOT_MAIN_HOLD")
        self.assertFalse(out["main_integration_metadata_verified"])

    def test_untrusted_runner_branch_or_event_denied(self):
        for key, value in (
            ("GITHUB_REPOSITORY", "fork/repo"),
            ("GITHUB_SHA", "0" * 39),
            ("RUNNER_ENVIRONMENT", "self-hosted"),
            ("GITHUB_REF", "refs/heads/evil"),
            ("GITHUB_EVENT_NAME", "repository_dispatch"),
        ):
            e = env()
            e[key] = value
            with self.subTest(field=key), self.assertRaises(IntegrationGateError):
                self.review(environment=e)
        e = env(main=True)
        e["GITHUB_EVENT_NAME"] = "workflow_dispatch"
        with self.assertRaisesRegex(IntegrationGateError, "G32_NOT_A_MAIN_PUSH"):
            self.review(environment=e)

    def test_predecessor_baseline_no_unreviewed_status_or_extra_fields(self):
        for mutation in (
            lambda x: x.update(execution="ENABLED"),
            lambda x: x.update(owner_acceptance="APPROVED"),
            lambda x: x.update(allow_trade=True),
            lambda x: x["predecessors"].pop(),
            lambda x: x["predecessors"][0].update(head_sha="0" * 39),
            lambda x: x["predecessors"][2].update(pr=15),
        ):
            base = copy.deepcopy(self.data)
            mutation(base)
            with self.assertRaises(IntegrationGateError):
                self.review(baseline=base)

    def test_merged_branch_can_be_deleted_only_with_real_merge_record(self):
        fake = fake_prs(self.data, merged=True)
        fake[8]["head"]["repo"] = None
        out = self.review(prs=fake, environment=env(main=True))
        self.assertTrue(out["main_integration_metadata_verified"])
        fake[8]["merged_at"] = None
        fake[8]["state"] = "open"
        with self.assertRaisesRegex(IntegrationGateError, "G32_PR_IDENTITY_CHANGED"):
            self.review(prs=fake, environment=env(main=True))

    def test_non_dict_bad_pr_records_fail_closed(self):
        for bad in (None, {}, "made up", [{"number": 76, "user": "evil"}]):
            with self.subTest(bad=str(bad)[:25]), self.assertRaises(
                    IntegrationGateError):
                self.review(prs=bad)

    def test_cli_help_never_changes_remote_git_state(self):
        run = subprocess.run([sys.executable, "-m",
                              "tools.main_integration_gate", "--help"],
                             capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0)
        self.assertIn("g32-integration-gate", run.stdout)


if __name__ == "__main__":
    unittest.main()
