"""G30: manifest topology and fail-closed pre-merge safety tests (offline)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.sequential_merge_rehearsal import (
    RehearsalError, _guard_environment, run_rehearsal,
    validate_manifest,
)

MANIFEST_FILE = Path(__file__).resolve().parents[1] / "knowledge" / "g30_pinned_pr_stack.json"
FEATURE = "a" * 40
MAIN = "5f27d132bc627fa73fde2e1b56b625cbdb09a465"


def env():
    return {
        "GITHUB_REPOSITORY": "maxqstudio/max-trading-agent",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/feat/g30-sequential-main-rehearsal",
        "GITHUB_SHA": FEATURE,
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux",
        "G30_EXPECTED_MAIN_SHA": MAIN,
    }


class G30PinnedSequenceTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))

    def plan(self, data=None, sha=FEATURE):
        return validate_manifest(self.manifest if data is None else data,
                                 feature_sha=sha)

    def test_real_manifest_pins_29_prior_prs_and_adds_own_g30_head(self):
        p = self.plan()
        self.assertEqual(len(p), 30)
        self.assertEqual([x["pr"] for x in p], list(range(14, 74, 2)))
        self.assertEqual(p[0]["branch"], "feat/m01-mt5-readonly-adapter")
        self.assertEqual(p[-2]["branch"], "feat/g29-main-integration-rehearsal")
        self.assertEqual(p[-1]["branch"], "feat/g30-sequential-main-rehearsal")
        self.assertEqual(p[-1]["sha"], FEATURE)
        self.assertEqual(self.manifest["main_sha"], MAIN)
        self.assertEqual(self.manifest["decision"], "NO_TRADE")
        self.assertEqual(self.manifest["execution"], "DISABLED")

    def test_previous_base_branch_must_match_exact_predecessor(self):
        for i in (1, 7, 19, 28):
            data = copy.deepcopy(self.manifest)
            data["previous_prs"][i]["base"] = "main"
            with self.subTest(index=i), self.assertRaisesRegex(
                RehearsalError, "G30_BROKEN_STACK_MANIFEST"):
                self.plan(data)

    def test_duplicate_head_branch_cannot_hide_changed_pr(self):
        data = copy.deepcopy(self.manifest)
        data["previous_prs"][10]["branch"] = data["previous_prs"][9]["branch"]
        data["previous_prs"][11]["base"] = data["previous_prs"][10]["branch"]
        with self.assertRaisesRegex(RehearsalError, "G30_BROKEN_STACK_MANIFEST"):
            self.plan(data)

    def test_wrong_pr_number_out_of_order_and_missing_entries_fail(self):
        for kind in ("missing", "extra", "order", "number"):
            data = copy.deepcopy(self.manifest)
            if kind == "missing":
                data["previous_prs"].pop(9)
            elif kind == "extra":
                data["previous_prs"].append(copy.deepcopy(data["previous_prs"][-1]))
            elif kind == "order":
                data["previous_prs"][4], data["previous_prs"][5] = (
                    data["previous_prs"][5], data["previous_prs"][4])
            else:
                data["previous_prs"][5]["pr"] = 99
            with self.subTest(kind=kind), self.assertRaises(RehearsalError):
                self.plan(data)

    def test_new_unapproved_manifest_field_is_rejected(self):
        data = copy.deepcopy(self.manifest)
        data["allow_trade"] = True
        with self.assertRaisesRegex(RehearsalError, "G30_INVALID_MANIFEST"):
            self.plan(data)

    def test_manifest_never_allows_a_trading_permission(self):
        for field, value in (("decision", "BUY"), ("execution", "ENABLED"),
                             ("expected_merge_method", "PUSH_TO_MAIN"),
                             ("repository", "attacker/fork")):
            data = copy.deepcopy(self.manifest)
            data[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                RehearsalError, "G30_INVALID_MANIFEST"):
                self.plan(data)

    def test_broken_sha_and_ref_string_are_rejected(self):
        for key, value in (
            ("sha", "z" * 40),
            ("sha", "1" * 39),
            ("branch", "refs/heads/main"),
            ("branch", "feat/bad\n--upload-pack"),
            ("branch", "feat/g30-sequential-main-rehearsal"),
        ):
            data = copy.deepcopy(self.manifest)
            data["previous_prs"][3][key] = value
            with self.subTest(key=key, value=value[:20]), self.assertRaises(
                RehearsalError):
                self.plan(data)

    def test_invalid_current_head_sha_is_rejected(self):
        for sha in ("0" * 39, "Z" * 40, "", None, 40):
            with self.subTest(sha=sha), self.assertRaisesRegex(
                RehearsalError, "G30_INVALID_MANIFEST"):
                self.plan(sha=sha)

    def test_missing_expected_github_context_is_rejected(self):
        for key, wrong in (
            ("GITHUB_REPOSITORY", "fork/other"),
            ("GITHUB_EVENT_NAME", "pull_request"),
            ("GITHUB_REF", "refs/heads/main"),
            ("GITHUB_REF", "refs/heads/feat/g29-main-integration-rehearsal"),
            ("GITHUB_SHA", "1" * 40),
            ("RUNNER_ENVIRONMENT", "self-hosted"),
            ("G30_EXPECTED_MAIN_SHA", "0" * 40),
        ):
            case = env()
            case[key] = wrong
            with self.subTest(key=key), self.assertRaisesRegex(
                RehearsalError, "G30_UNTRUSTED_CI_CONTEXT"):
                _guard_environment(case, MAIN, FEATURE)

    def test_github_hosted_fixed_context_is_accepted(self):
        _guard_environment(env(), MAIN, FEATURE)

    def test_missing_checkout_does_not_create_or_write_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "evidence.json"
            with self.assertRaisesRegex(RehearsalError, "G30_UNSAFE_WORKING_PATHS"):
                run_rehearsal(root / "missing", self.manifest, FEATURE,
                              output, root / "aggregate", env=env())
            self.assertFalse(output.exists())

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work" / ".git").mkdir(parents=True)
            output = root / "evidence.json"
            output.write_text('{"status":"do not overwrite"}')
            with self.assertRaisesRegex(RehearsalError, "G30_UNSAFE_WORKING_PATHS"):
                run_rehearsal(root / "work", self.manifest, FEATURE, output,
                              root / "aggregate", env=env())
            self.assertEqual(output.read_text(), '{"status":"do not overwrite"}')

    def test_existing_aggregate_directory_fails_before_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work" / ".git").mkdir(parents=True)
            aggregate = root / "aggregate"
            aggregate.mkdir()
            with self.assertRaisesRegex(RehearsalError, "G30_UNSAFE_WORKING_PATHS"):
                run_rehearsal(root / "work", self.manifest, FEATURE,
                              root / "evidence.json", aggregate, env=env())

    def test_caller_cannot_execute_sequential_merges_on_untrusted_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work" / ".git").mkdir(parents=True)
            data = env()
            data["RUNNER_ENVIRONMENT"] = "self-hosted"
            with patch("tools.sequential_merge_rehearsal._git") as called:
                with self.assertRaisesRegex(RehearsalError,
                                            "G30_UNTRUSTED_CI_CONTEXT"):
                    run_rehearsal(root / "work", self.manifest, FEATURE,
                                  root / "evidence.json", root / "aggregate",
                                  env=data)
                called.assert_not_called()

    def test_required_manifest_is_no_source_and_no_secrets(self):
        text = MANIFEST_FILE.read_text(encoding="utf-8")
        for forbidden in ("private_key", "api_key", "broker_password",
                          "candidate_code", "access_token"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(len(self.manifest["previous_prs"]), 29)
        self.assertTrue(all(set(row) == {"pr","branch","sha","base"}
                            for row in self.manifest["previous_prs"]))


if __name__ == "__main__":
    unittest.main()
