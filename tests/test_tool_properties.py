"""G21 trusted property review tests: synthetic + mocked sandbox, never host exec."""
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
from max_trading_agent.tool_properties import (
    _contract, _property_cases, review_t0_properties,
)
from test_tool_factory import CANDIDATE

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tool_factory_range_span.json"


def passed(candidate, cases):
    return {**validate_candidate(candidate),
            "status": "SANDBOX_PASSED_REVIEW_REQUIRED",
            "oracle_cases": len(cases)}


class PropertyReviewTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        self.output = self.root / "t0-review"
        self.range_candidate = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_new_reviewer_owned_range_contract_has_separate_independent_evidence(self):
        with patch("max_trading_agent.tool_properties.sandbox_evaluate",
                   side_effect=passed) as runner:
            result = review_t0_properties(
                self.range_candidate, review_dir=self.output, seed=731)
        self.assertEqual(result["status"], "T0_PROPERTY_REVIEWED")
        self.assertEqual(result["reference"], "NUMERIC_RANGE")
        self.assertEqual(result["reviewer"], "TRUSTED_PROPERTY_CONTRACT_V1")
        self.assertEqual(result["golden_cases"], 5)
        self.assertEqual(result["property_cases"], 32)
        self.assertEqual(result["challenge_seed_hex"], f"{731:032x}")
        self.assertEqual(result["tier"], "T0_RESEARCH_ONLY")
        self.assertEqual(result["decision"], "NO_TRADE")
        self.assertEqual(result["execution"], "DISABLED")
        self.assertIs(result["broker_execution"], False)
        self.assertFalse(result["eligible_for_demo_or_live"])
        self.assertTrue(result["evidence_only"])
        self.assertEqual(result["owner_acceptance"], "PENDING")
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(len(runner.call_args_list[0].args[1]), 5)
        self.assertEqual(len(runner.call_args_list[1].args[1]), 32)
        self.assertEqual(json.loads(
            (self.output / (result["review_id"] + ".json")).read_text()), result)
        self.assertNotIn(self.range_candidate["code"], json.dumps(result))

    def test_mean_contract_keeps_g20_review_behavior_unchanged(self):
        with patch("max_trading_agent.tool_properties.sandbox_evaluate",
                   side_effect=passed) as runner:
            report = review_t0_properties(CANDIDATE, seed=42)
        self.assertEqual(report["reference"], "ARITHMETIC_MEAN")
        self.assertEqual(report["status"], "T0_PROPERTY_REVIEWED")
        self.assertEqual(runner.call_count, 2)

    def test_properties_are_replayable_and_check_known_reference(self):
        for kind in ("ARITHMETIC_MEAN", "NUMERIC_RANGE"):
            first = _property_cases(kind, 91)
            self.assertEqual(first, _property_cases(kind, 91))
            self.assertNotEqual(first, _property_cases(kind, 92))
            self.assertEqual(len(first), 32)
            for pos in range(0, 32, 4):
                base, permuted, shifted, scaled = first[pos:pos+4]
                self.assertEqual(sorted(base["input"]),
                                 sorted(permuted["input"]))
                self.assertAlmostEqual(base["expected"], permuted["expected"])
                if kind == "ARITHMETIC_MEAN":
                    offset = shifted["input"][0] - base["input"][0]
                    self.assertAlmostEqual(shifted["expected"],
                                           base["expected"] + offset)
                else:
                    self.assertAlmostEqual(shifted["expected"],
                                           base["expected"])
                factor = scaled["input"][0] / base["input"][0] if base["input"][0] else None
                if factor is not None:
                    self.assertAlmostEqual(scaled["expected"],
                                           base["expected"] * factor)
        with self.assertRaisesRegex(FactoryError, "INVALID_CHALLENGE_SEED"):
            _property_cases("ARITHMETIC_MEAN", -1)
        with self.assertRaisesRegex(FactoryError, "INVALID_CHALLENGE_SEED"):
            _property_cases("NUMERIC_RANGE", True)

    def test_unlisted_arbitrary_tool_remains_pending(self):
        candidate = copy.deepcopy(self.range_candidate)
        candidate["tool_id"] = "invented_unreviewed_signal"
        with patch("max_trading_agent.tool_properties.sandbox_evaluate") as run:
            result = review_t0_properties(candidate, review_dir=self.output)
        self.assertEqual(result["status"], "PENDING_INDEPENDENT_ORACLE")
        self.assertEqual(result["decision"], "NO_TRADE")
        self.assertFalse(result["eligible_for_demo_or_live"])
        self.assertFalse(self.output.exists())
        run.assert_not_called()

    def test_poisoned_golden_expectation_fails_before_sandbox(self):
        def poisoned(name, path):
            return [
                {"input": [1, 2, 3], "expected": 999},
                {"input": [1, 2], "expected": 1.5},
                {"input": [2, 4], "expected": 3},
            ]
        with patch("max_trading_agent.tool_properties.independent_cases",
                   side_effect=poisoned), patch(
                   "max_trading_agent.tool_properties.sandbox_evaluate") as run:
            with self.assertRaisesRegex(FactoryError, "TRUSTED_ORACLE_MISMATCH"):
                review_t0_properties(CANDIDATE, review_dir=self.output)
        run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_corrupt_contract_file_rejected_before_sandbox(self):
        path = self.root / "contracts.json"
        cases = (
            '{"rolling_mean_v1":{"schema_version":1,"reference":"UNKNOWN"}}',
            '{"rolling_mean_v1":{"schema_version":1,"reference":"ARITHMETIC_MEAN"},'
            '"rolling_mean_v1":{"schema_version":1,"reference":"ARITHMETIC_MEAN"}}',
            '[]',
            '{"rolling_mean_v1":{"schema_version":true,"reference":"ARITHMETIC_MEAN"}}',
        )
        for raw in cases:
            path.write_text(raw, encoding="utf-8")
            with self.subTest(raw=raw[:45]), patch(
                "max_trading_agent.tool_properties._CONTRACTS", path), patch(
                "max_trading_agent.tool_properties.sandbox_evaluate") as run:
                with self.assertRaisesRegex(FactoryError, "TRUSTED_CONTRACT_INVALID"):
                    review_t0_properties(CANDIDATE, review_dir=self.output)
                run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_forged_pass_wrong_identity_status_or_count_rejected(self):
        for key, fake_value in (
            ("sha256", "0" * 64), ("tier", "T3_LIVE"),
            ("decision", "BUY"), ("execution", "ENABLED"),
            ("broker_execution", True), ("oracle_cases", 0),
            ("status", "SANDBOX_FAILED"),
        ):
            with self.subTest(field=key):
                def corrupted(candidate, cases):
                    return {**passed(candidate, cases), key: fake_value}
                with patch("max_trading_agent.tool_properties.sandbox_evaluate",
                           side_effect=corrupted) as runner:
                    result = review_t0_properties(
                        self.range_candidate, review_dir=self.output, seed=3)
                self.assertEqual(runner.call_count, 1)
                self.assertEqual(result["status"], "T0_PROPERTY_REJECTED")
                self.assertEqual(result["failed_stage"], "BASELINE")
                self.assertFalse(self.output.exists())

    def test_property_failure_keeps_review_and_runtime_registry_empty(self):
        def cheating(candidate, cases):
            return {**passed(candidate, cases),
                    "status": ("SANDBOX_FAILED" if len(cases) == 32
                               else "SANDBOX_PASSED_REVIEW_REQUIRED")}
        with patch("max_trading_agent.tool_properties.sandbox_evaluate",
                   side_effect=cheating) as runner:
            result = review_t0_properties(
                self.range_candidate, review_dir=self.output, seed=3)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(result["status"], "T0_PROPERTY_REJECTED")
        self.assertEqual(result["failed_stage"], "PROPERTIES")
        self.assertFalse(self.output.exists())

    def test_fresh_seed_yields_distinct_immutable_receipts(self):
        with patch("max_trading_agent.tool_properties.sandbox_evaluate",
                   side_effect=passed), patch(
                "max_trading_agent.tool_properties.secrets.randbits",
                side_effect=(101, 202)):
            a = review_t0_properties(self.range_candidate,
                                     review_dir=self.output)
            b = review_t0_properties(self.range_candidate,
                                     review_dir=self.output)
        self.assertNotEqual(a["review_id"], b["review_id"])
        self.assertNotEqual(a["property_sha256"], b["property_sha256"])
        self.assertEqual(len(list(self.output.glob("*.json"))), 2)
        self.assertEqual(a["candidate_sha256"], b["candidate_sha256"])

    def test_existing_receipt_conflict_rejected_without_overwrite(self):
        with patch("max_trading_agent.tool_properties.sandbox_evaluate",
                   side_effect=passed):
            first = review_t0_properties(self.range_candidate,
                                         review_dir=self.output, seed=9)
            file = self.output / (first["review_id"] + ".json")
            file.write_text('{"forged":true}', encoding="utf-8")
            with self.assertRaisesRegex(FactoryError, "REVIEW_RECEIPT_CONFLICT"):
                review_t0_properties(self.range_candidate,
                                     review_dir=self.output, seed=9)
        self.assertEqual(file.read_text(encoding="utf-8"), '{"forged":true}')

    def test_unsafe_effect_fails_before_oracle_or_sandbox(self):
        candidate = copy.deepcopy(self.range_candidate)
        candidate["effect"] = "TRADE_MUTATION"
        with patch("max_trading_agent.tool_properties.sandbox_evaluate") as run:
            with self.assertRaises(FactoryError):
                review_t0_properties(candidate, review_dir=self.output)
        run.assert_not_called()

    def test_cli_novel_without_contract_is_pending_and_exits_nonzero(self):
        candidate = copy.deepcopy(self.range_candidate)
        candidate["tool_id"] = "totally_unrecognized_study"
        file = self.root / "candidate.json"
        file.write_text(json.dumps(candidate), encoding="utf-8")
        run = subprocess.run([
            sys.executable, "-m", "max_trading_agent",
            "tool-property-review", "--file", str(file),
            "--review-dir", str(self.output),
        ], capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 3, msg=run.stderr[-300:])
        report = json.loads(run.stdout)
        self.assertEqual(report["status"], "PENDING_INDEPENDENT_ORACLE")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
