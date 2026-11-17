"""G20 trusted T0 review evidence tests. No generated code runs on CI host."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from max_trading_agent.tool_factory import FactoryError, validate_candidate
from max_trading_agent.tool_review import _known_challenge, review_t0
from test_tool_factory import CANDIDATE


def passing(candidate, cases):
    identity = validate_candidate(candidate)
    return {**identity, "status": "SANDBOX_PASSED_REVIEW_REQUIRED"}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / "t0-reviews"

    def test_review_has_two_independent_batches_and_no_authority(self):
        with patch("max_trading_agent.tool_review.sandbox_evaluate",
                   side_effect=passing) as run:
            first = review_t0(CANDIDATE, review_dir=self.out)
            second = review_t0(CANDIDATE, review_dir=self.out)
        self.assertEqual(first, second)
        self.assertEqual(run.call_count, 4)
        self.assertEqual(first["status"], "T0_SANDBOX_REVIEWED")
        self.assertEqual(first["tier"], "T0_RESEARCH_ONLY")
        self.assertEqual(first["decision"], "NO_TRADE")
        self.assertEqual(first["execution"], "DISABLED")
        self.assertFalse(first["broker_execution"])
        self.assertFalse(first["eligible_for_demo_or_live"])
        self.assertTrue(first["evidence_only"])
        self.assertEqual(first["owner_acceptance"], "PENDING")
        self.assertEqual(len(first["review_id"]), 64)
        self.assertEqual(first["oracle_cases"], 5)
        self.assertEqual(first["challenge_cases"], 8)
        self.assertEqual(first["candidate_sha256"],
                         validate_candidate(CANDIDATE)["sha256"])
        original_inputs = [x["input"] for x in run.call_args_list[0].args[1]]
        challenge_inputs = [x["input"] for x in run.call_args_list[1].args[1]]
        self.assertTrue(all(x not in original_inputs for x in challenge_inputs))
        files = list(self.out.glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text()), first)
        self.assertNotIn(CANDIDATE["code"], files[0].read_text())

    def test_challenges_are_deterministic_and_independent_of_examples(self):
        sha = validate_candidate(CANDIDATE)["sha256"]
        a = _known_challenge(CANDIDATE["tool_id"], sha)
        self.assertEqual(a, _known_challenge(CANDIDATE["tool_id"], sha))
        changed = copy.deepcopy(CANDIDATE)
        changed["examples"][0]["expected"] = 999
        b = _known_challenge(changed["tool_id"],
                             validate_candidate(changed)["sha256"])
        self.assertNotEqual(a, b)
        self.assertTrue(all(len(case["input"]) in (1, 2, 3, 5, 8, 13, 21, 31)
                            for case in a))
        self.assertIsNone(_known_challenge("genuinely_novel_tool", sha))

    def test_unknown_novel_tool_never_self_approves_or_writes(self):
        unknown = copy.deepcopy(CANDIDATE)
        unknown["tool_id"] = "newly_created_regime_feature"
        with patch("max_trading_agent.tool_review.sandbox_evaluate") as run:
            result = review_t0(unknown, review_dir=self.out)
        self.assertEqual(result["status"], "PENDING_INDEPENDENT_ORACLE")
        self.assertEqual(result["decision"], "NO_TRADE")
        self.assertFalse(result["eligible_for_demo_or_live"])
        self.assertFalse(self.out.exists())
        run.assert_not_called()

    def test_failing_second_batch_cannot_create_receipt(self):
        def fake(candidate, cases):
            result = passing(candidate, cases)
            if len(cases) == 8:
                result["status"] = "SANDBOX_FAILED"
            return result
        with patch("max_trading_agent.tool_review.sandbox_evaluate",
                   side_effect=fake) as run:
            report = review_t0(CANDIDATE, review_dir=self.out)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(report["status"], "T0_REVIEW_REJECTED")
        self.assertEqual(report["failed_stage"], "CHALLENGE")
        self.assertFalse(self.out.exists())

    def test_failing_first_batch_does_not_run_challenge(self):
        def fake(candidate, cases):
            return {**passing(candidate, cases), "status": "SANDBOX_FAILED"}
        with patch("max_trading_agent.tool_review.sandbox_evaluate",
                   side_effect=fake) as run:
            report = review_t0(CANDIDATE, review_dir=self.out)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(report["failed_stage"], "BASELINE")
        self.assertFalse(self.out.exists())

    def test_forged_success_missing_identity_is_not_accepted(self):
        sha = validate_candidate(CANDIDATE)["sha256"]
        for key, wrong in (("sha256", "0"*64), ("tier", "T3_LIVE"),
                           ("decision", "BUY"), ("execution", "ENABLED"),
                           ("broker_execution", True), ("status", "SUCCESS")):
            with self.subTest(key=key):
                def fake(candidate, cases):
                    return {**passing(candidate, cases), key: wrong}
                with patch("max_trading_agent.tool_review.sandbox_evaluate",
                           side_effect=fake):
                    result = review_t0(CANDIDATE, review_dir=self.out)
                self.assertEqual(result["status"], "T0_REVIEW_REJECTED")
                self.assertFalse(self.out.exists())
        self.assertEqual(len(sha), 64)

    def test_invalid_effect_or_code_rejected_before_docker(self):
        item = copy.deepcopy(CANDIDATE)
        item["effect"] = "TRADE_MUTATION"
        with patch("max_trading_agent.tool_review.sandbox_evaluate") as run:
            with self.assertRaises(FactoryError):
                review_t0(item, review_dir=self.out)
        run.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_tampered_existing_receipt_cannot_be_overwritten(self):
        with patch("max_trading_agent.tool_review.sandbox_evaluate",
                   side_effect=passing):
            first = review_t0(CANDIDATE, review_dir=self.out)
            path = self.out / (first["review_id"] + ".json")
            path.write_text('{"fraudulent":"success"}', encoding="utf-8")
            with self.assertRaisesRegex(FactoryError, "REVIEW_RECEIPT_CONFLICT"):
                review_t0(CANDIDATE, review_dir=self.out)
        self.assertEqual(path.read_text(), '{"fraudulent":"success"}')

    @unittest.skipIf(os.name == "nt", "Windows symlink privilege is restricted")
    def test_symlink_review_directory_is_denied(self):
        destination = self.root / "destination"
        destination.mkdir()
        self.out.symlink_to(destination, target_is_directory=True)
        with patch("max_trading_agent.tool_review.sandbox_evaluate",
                   side_effect=passing):
            with self.assertRaisesRegex(FactoryError, "UNSAFE_REVIEW_DIRECTORY"):
                review_t0(CANDIDATE, review_dir=self.out)
        self.assertEqual(list(destination.iterdir()), [])

    def test_unknown_cli_exits_3_without_docker_or_receipt(self):
        item = copy.deepcopy(CANDIDATE)
        item["tool_id"] = "unknown_research_function"
        file = self.root / "unknown.json"
        file.write_text(json.dumps(item), encoding="utf-8")
        run = subprocess.run([
            sys.executable, "-m", "max_trading_agent",
            "tool-review", "--file", str(file), "--review-dir", str(self.out),
        ], capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 3, msg=run.stderr[:300])
        data = json.loads(run.stdout)
        self.assertEqual(data["status"], "PENDING_INDEPENDENT_ORACLE")
        self.assertEqual(data["decision"], "NO_TRADE")
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
