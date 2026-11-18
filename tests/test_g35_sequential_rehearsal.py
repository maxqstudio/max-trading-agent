"""G35 pinned 35-PR ephemeral merge safety and fail-closed unit checks."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.g35_sequential_rehearsal import (
    RehearsalError, _MAIN_SHA, validate_inputs, run_rehearsal,
    verify_live_pr_bindings,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "knowledge" / "g32_pinned_merge_identities.json"
SHA = "a" * 40
BRANCH = "refs/heads/feat/g35-full-stack-release-gates"


def context(**changes):
    env = {
        "GITHUB_REPOSITORY": "maxqstudio/max-trading-agent",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": BRANCH,
        "GITHUB_SHA": SHA,
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux",
        "G35_EXPECTED_MAIN_SHA": _MAIN_SHA,
    }
    env.update(changes)
    return env


class G35RehearsalTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def live_prs(self):
        plan = validate_inputs(self.data, SHA, context())
        records = []
        for index, entry in enumerate(plan):
            records.append({
                "number": entry["pr"],
                "html_url": "https://github.com/maxqstudio/max-trading-agent/pull/" + str(entry["pr"]),
                "head": {
                    "sha": entry["head_sha"], "ref": entry["branch"],
                    "repo": {"full_name": "maxqstudio/max-trading-agent"},
                },
                "base": {
                    "ref": "main" if index == 0 else plan[index - 1]["branch"],
                    "repo": {"full_name": "maxqstudio/max-trading-agent"},
                },
                "state": "open", "draft": True, "merged_at": None,
            })
        return plan, records

    def test_g35_verified_live_github_pr_metadata_all_35_draft(self):
        plan, records = self.live_prs()
        self.assertIsNone(verify_live_pr_bindings(plan, records))

    def test_g35_live_github_pr_mutations_fail_closed(self):
        mutators = (
            lambda records: records[3]["head"].update(sha="b" * 40),
            lambda records: records[3]["head"].update(ref="feat/other-branch"),
            lambda records: records[3]["base"].update(ref="main"),
            lambda records: records[3]["head"]["repo"].update(full_name="evil/fork"),
            lambda records: records[3].update(draft=False),
            lambda records: records[3].update(state="closed"),
            lambda records: records[3].update(state="closed", merged_at="2026-11-18T00:00:00Z"),
            lambda records: records.pop(),
            lambda records: records.append(copy.deepcopy(records[-1])),
            lambda records: records.append({
                **copy.deepcopy(records[-1]), "number": 84,
                "html_url": "https://github.com/maxqstudio/max-trading-agent/pull/84",
                "head": {"ref": "feat/g36-unreviewed", "sha": "c" * 40,
                         "repo": {"full_name": "maxqstudio/max-trading-agent"}},
                "base": {"ref": "feat/g35-full-stack-release-gates",
                         "repo": {"full_name": "maxqstudio/max-trading-agent"}},
            }),
        )
        for index, mutate in enumerate(mutators):
            plan, records = self.live_prs()
            mutate(records)
            with self.subTest(case=index), self.assertRaisesRegex(
                    RehearsalError, "G35_GITHUB_PR_CHAIN_UNVERIFIED"):
                verify_live_pr_bindings(plan, records)

    def test_g35_live_pr_readonly_workflow_token_and_report_gate(self):
        workflow = (ROOT / ".github" / "workflows" /
                    "g35-sequential-main.yml").read_text(encoding="utf-8")
        self.assertIn("  pull-requests: read", workflow)
        self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
        self.assertIn('data["github_pr_metadata_verified"] is True', workflow)
        self.assertNotIn("pull-requests: write", workflow)

    def test_pinned_plan_is_exact_35_prs(self):
        plan = validate_inputs(self.data, SHA, context())
        self.assertEqual(len(plan), 35)
        self.assertEqual([x["pr"] for x in plan], list(range(14, 84, 2)))
        self.assertEqual(plan[0]["branch"], "feat/m01-mt5-readonly-adapter")
        self.assertEqual(plan[-2]["branch"], "feat/g34-docker-runtime-digest")
        self.assertEqual(plan[-1]["branch"], "feat/g35-full-stack-release-gates")
        self.assertEqual(plan[-1]["head_sha"], SHA)
        self.assertEqual(_MAIN_SHA, "5f27d132bc627fa73fde2e1b56b625cbdb09a465")

    def test_invalid_rehearsal_sha_rejected(self):
        for sha in ("", "0" * 39, "Z" * 40, None, 1):
            with self.subTest(sha=sha), self.assertRaises(RehearsalError):
                validate_inputs(self.data, sha, context())

    def test_untrusted_github_repository_ref_and_event_rejected(self):
        cases = {
            "GITHUB_REPOSITORY": "stranger/fork",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": "b" * 40,
            "RUNNER_ENVIRONMENT": "self-hosted",
            "G35_EXPECTED_MAIN_SHA": "b" * 40,
        }
        for key, invalid in cases.items():
            with self.subTest(key=key), self.assertRaisesRegex(
                    RehearsalError, "G35_UNTRUSTED_RUN_CONTEXT"):
                validate_inputs(self.data, SHA, context(**{key: invalid}))

    def test_future_nonapproved_branch_is_not_silently_added(self):
        altered = copy.deepcopy(self.data)
        altered["predecessors"].append({
            "pr": 82, "branch": "feat/g35-full-stack-release-gates",
            "head_sha": SHA,
        })
        with self.assertRaisesRegex(RehearsalError, "G35_INVALID_PINNED_STACK"):
            validate_inputs(altered, SHA, context())

    def test_missing_latest_g34_pr_denied(self):
        altered = copy.deepcopy(self.data)
        altered["predecessors"].pop()
        with self.assertRaisesRegex(RehearsalError, "G35_INVALID_PINNED_STACK"):
            validate_inputs(altered, SHA, context())

    def test_changed_predecessor_sha_denied(self):
        altered = copy.deepcopy(self.data)
        altered["predecessors"][-1]["head_sha"] = "not-a-sha"
        with self.assertRaisesRegex(RehearsalError, "G35_INVALID_PINNED_STACK"):
            validate_inputs(altered, SHA, context())

    def test_unsafe_baseline_rejects_authorized_trade(self):
        for field, value in (
            ("decision", "BUY"), ("execution", "ENABLED"),
            ("owner_acceptance", "APPROVED"),
            ("self_pr", 84),
        ):
            altered = copy.deepcopy(self.data)
            altered[field] = value
            with self.subTest(field=field), self.assertRaises(RehearsalError):
                validate_inputs(altered, SHA, context())

    def test_missing_worktree_rejected_before_git(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "report.json"
            with patch("tools.g35_sequential_rehearsal._git") as git:
                with self.assertRaisesRegex(RehearsalError,
                                            "G35_UNSAFE_REHEARSAL_PATHS"):
                    run_rehearsal(root / "absent", root / "aggregate", output,
                                  self.data, env=context())
                git.assert_not_called()
            self.assertFalse(output.exists())

    def test_existing_report_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            (work / ".git").mkdir(parents=True)
            output = root / "report.json"
            output.write_text("existing-report", encoding="utf-8")
            with self.assertRaisesRegex(RehearsalError,
                                        "G35_UNSAFE_REHEARSAL_PATHS"):
                run_rehearsal(work, root / "aggregate", output,
                              self.data, env=context())
            self.assertEqual(output.read_text(), "existing-report")

    def test_dirty_main_refuses_before_any_remote_fetch(self):
        class FakeResult:
            stdout = "?? local-file.py\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            (work / ".git").mkdir(parents=True)
            with patch("tools.g35_sequential_rehearsal._git",
                       return_value=FakeResult()) as git:
                with self.assertRaisesRegex(RehearsalError,
                                            "G35_DIRTY_MAIN_CHECKOUT"):
                    run_rehearsal(work, root / "aggregate", root / "report.json",
                                  self.data, env=context())
                self.assertEqual(git.call_count, 1)
                self.assertEqual(git.call_args.args[1:], ("status", "--porcelain"))

    def test_report_stays_uncreated_for_untrusted_run_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RehearsalError,
                                        "G35_UNTRUSTED_RUN_CONTEXT"):
                run_rehearsal(root, root / "aggregate", root / "report.json",
                              self.data, env=context(GITHUB_EVENT_NAME="schedule"))
            self.assertFalse((root / "report.json").exists())

    def test_metadata_does_not_include_secrets_or_broker_credentials(self):
        raw = MANIFEST.read_text(encoding="utf-8")
        for word in ("private_key", "api_key", "broker_password",
                     "access_token", "candidate_code", "order_send"):
            self.assertNotIn(word, raw)
        self.assertEqual(self.data["decision"], "NO_TRADE")
        self.assertEqual(self.data["execution"], "DISABLED")

    def test_g35_replay_checks_real_docker_timeout_cleanup(self):
        workflow = (ROOT / ".github" / "workflows" /
                    "g35-sequential-main.yml").read_text(encoding="utf-8")
        self.assertIn("G35 merged-tree Docker timeout cleanup", workflow)
        self.assertIn("sandbox_evaluate(candidate,cases,timeout_seconds=3)", workflow)
        self.assertIn('"status"]=="SANDBOX_TIMEOUT"', workflow)
        self.assertIn('["docker","ps","-a","--filter","name=maxq-t0-"', workflow)
        self.assertIn("TIMEOUT_CONTAINER_NOT_REMOVED", workflow)
        self.assertIn('working-directory: work', workflow)

    def test_replay_script_never_writes_to_remote(self):
        script = (ROOT / "tools" / "g35_sequential_rehearsal.py").read_text(
            encoding="utf-8")
        for forbidden in ('_git(work, "push"', '_git(aggregate, "push"',
                          '"order_send"', '"order_check"',
                          '"git", "commit", "--amend"'):
            self.assertNotIn(forbidden, script)
        self.assertIn('"--no-commit"', script)
        self.assertIn('"worktree", "add"', script)


if __name__ == "__main__":
    unittest.main()
