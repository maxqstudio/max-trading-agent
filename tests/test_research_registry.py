"""G27 static R0 reference registry and non-executable T1 inbox regression."""
import copy
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from max_trading_agent.research_registry import (
    _built_in_spec, evaluate_reference, list_references,
    stage_untrusted_candidate,
)
from max_trading_agent.tool_factory import FactoryError, validate_candidate

FIXTURE = Path(__file__).resolve().parent / "fixtures/tool_factory_range_span.json"


class ReferenceRegistryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.stage_dir = self.root / "pending"
        self.candidate = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_static_catalog_is_explicit_and_has_no_generated_executable_entry(self):
        catalog = list_references()
        self.assertEqual(catalog["status"], "R0_BUILTIN_REFERENCES_AVAILABLE")
        self.assertEqual([x["tool_id"] for x in catalog["entries"]],
                         ["range_span_v1", "rolling_mean_v1"])
        self.assertEqual(catalog["generated_t1_tool_registration"], "DISABLED")
        self.assertEqual(catalog["decision"], "NO_TRADE")
        self.assertFalse(catalog["broker_execution"])
        for entry in catalog["entries"]:
            self.assertFalse(entry["candidate_code_loaded"])
            self.assertEqual(entry["source"], "REPOSITORY_OWNED_STATIC_REFERENCE")
            self.assertTrue(entry["trusted_golden_sha256"])
            self.assertTrue(entry["trusted_contract_sha256"])

    def test_known_mean_and_range_reference_outputs(self):
        mean = evaluate_reference("rolling_mean_v1", [2, 4, 6])
        spread = evaluate_reference("range_span_v1", [-4, 0, 7])
        self.assertEqual(mean["result"], 4.0)
        self.assertEqual(spread["result"], 11.0)
        self.assertEqual(mean["reference"], "ARITHMETIC_MEAN")
        self.assertEqual(spread["reference"], "NUMERIC_RANGE")
        self.assertEqual(mean["status"], "R0_TRUSTED_REFERENCE_EVALUATED")
        self.assertTrue(spread["deterministic_built_in_calculation"])
        self.assertFalse(spread["generated_candidate_executed"])
        self.assertEqual(spread["execution"], "BUILTIN_REFERENCE_ONLY")
        self.assertFalse(spread["eligible_for_demo_or_live"])
        self.assertFalse(spread["broker_execution"])
        self.assertEqual(spread["decision"], "NO_TRADE")

    def test_reference_outputs_replay_deterministically(self):
        for name in ("rolling_mean_v1", "range_span_v1"):
            a = evaluate_reference(name, [0.25, -3.25, 6.25])
            b = evaluate_reference(name, [0.25, -3.25, 6.25])
            self.assertEqual(a, b)
            self.assertEqual(a["input_count"], 3)
            self.assertEqual(len(a["input_sha256"]), 64)

    def test_unknown_generated_tool_cannot_auto_register_or_run(self):
        for tool_id in ("invented_indicator", "min_max_indicator", "__import__",
                        "range_span_v2", "", None):
            with self.subTest(tool_id=tool_id):
                with self.assertRaisesRegex(FactoryError, "R0_REFERENCE_NOT_REGISTERED"):
                    evaluate_reference(tool_id, [1, 2, 3])
        self.assertEqual(len(list_references()["entries"]), 2)

    def test_bool_nonfinite_text_huge_and_empty_input_rejected(self):
        values = (
            True, None, "2", float("nan"), float("inf"),
            float("-inf"), 1e15,
        )
        for value in values:
            with self.subTest(value=str(value)):
                with self.assertRaises(FactoryError):
                    evaluate_reference("range_span_v1", [1, value])
        for sequence in ([], [1] * 129, 3, {"values": [1]}, "1,2"):
            with self.subTest(type=str(type(sequence))):
                with self.assertRaisesRegex(FactoryError, "R0_INVALID_VALUES"):
                    evaluate_reference("rolling_mean_v1", sequence)

    def test_oversized_result_is_blocked_even_if_each_input_is_valid(self):
        with self.assertRaisesRegex(FactoryError, "R0_RESULT_OUT_OF_BOUNDS"):
            evaluate_reference("range_span_v1", [-1e9, 1e9])

    def test_changed_reference_kind_or_missing_goldens_is_fail_closed(self):
        with patch("max_trading_agent.research_registry._contract",
                   return_value={"schema_version": 1, "reference": "ARBITRARY_CALL"}):
            with self.assertRaisesRegex(FactoryError, "R0_TRUSTED_REFERENCE_CHANGED"):
                evaluate_reference("range_span_v1", [1, 2])
        with patch("max_trading_agent.research_registry.independent_cases",
                   return_value=None):
            with self.assertRaisesRegex(FactoryError, "R0_GOLDENS_MISSING"):
                list_references()

    def test_poisoned_golden_expectation_never_used_as_reference(self):
        golden = [{"input": [1, 2, 3], "expected": 1_000},
                  {"input": [3, 5], "expected": 2},
                  {"input": [-1, 4], "expected": 5}]
        with patch("max_trading_agent.research_registry.independent_cases",
                   return_value=golden):
            with self.assertRaisesRegex(FactoryError, "TRUSTED_ORACLE_MISMATCH"):
                evaluate_reference("range_span_v1", [1, 2])

    def test_staging_unreviewed_generated_candidate_never_registers(self):
        bad = copy.deepcopy(self.candidate)
        bad["code"] = "def analyze(values):\n    return 999\n"
        record = stage_untrusted_candidate(bad, directory=self.stage_dir)
        self.assertEqual(record["status"], "T1_CANDIDATE_NOT_REGISTERED")
        self.assertEqual(record["candidate_sha256"], validate_candidate(bad)["sha256"])
        self.assertFalse(record["candidate_source_stored"])
        self.assertFalse(record["candidate_source_executed"])
        self.assertFalse(record["tool_admitted"])
        self.assertFalse(record["candidate_verified"])
        self.assertEqual(record["generated_t1_tool_registration"], "DISABLED")
        self.assertEqual(record["decision"], "NO_TRADE")
        self.assertEqual(record["execution"], "DISABLED")
        path = self.stage_dir / (record["review_id"] + ".json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), record)
        self.assertNotIn(bad["code"], path.read_text(encoding="utf-8"))
        self.assertEqual(evaluate_reference("range_span_v1", [2, 3])["result"], 1.0)

    def test_stage_idempotence_and_hash_conflict_fail_closed(self):
        a = stage_untrusted_candidate(self.candidate, directory=self.stage_dir)
        b = stage_untrusted_candidate(self.candidate, directory=self.stage_dir)
        self.assertEqual(a, b)
        file = self.stage_dir / (a["review_id"] + ".json")
        file.write_text('{"tampered":true}', encoding="utf-8")
        with self.assertRaisesRegex(FactoryError, "REVIEW_RECEIPT_CONFLICT"):
            stage_untrusted_candidate(self.candidate, directory=self.stage_dir)
        self.assertEqual(len(list(self.stage_dir.glob("*.json"))), 1)

    @unittest.skipIf(os.name == "nt", "Windows symlink permission unavailable")
    def test_symlink_stage_dir_denied_without_writing_target(self):
        target = self.root / "target"
        target.mkdir()
        self.stage_dir.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(FactoryError, "UNSAFE_REVIEW_DIRECTORY"):
            stage_untrusted_candidate(self.candidate, directory=self.stage_dir)
        self.assertEqual(list(target.iterdir()), [])

    def test_candidate_import_source_rejected_at_staging_boundary(self):
        bad = copy.deepcopy(self.candidate)
        bad["code"] = "import os\ndef analyze(values):\n    return 0\n"
        with self.assertRaises(FactoryError):
            stage_untrusted_candidate(bad, directory=self.stage_dir)
        self.assertFalse(self.stage_dir.exists())

    def test_cli_list_and_reference_evaluate_are_offline_no_trade(self):
        def call(*args):
            return subprocess.run([sys.executable, "-m",
                                   "max_trading_agent.research_registry",
                                   *args], capture_output=True, text=True, timeout=20)
        ls = call("list")
        self.assertEqual(ls.returncode, 0, ls.stderr[:200])
        listing = json.loads(ls.stdout)
        self.assertEqual(len(listing["entries"]), 2)
        result = call("evaluate", "--tool-id", "rolling_mean_v1",
                      "--values", "[2, 4, 6]")
        self.assertEqual(result.returncode, 0, result.stderr[:200])
        record = json.loads(result.stdout)
        self.assertEqual(record["result"], 4.0)
        self.assertEqual(record["decision"], "NO_TRADE")
        self.assertFalse(record["broker_execution"])

    def test_cli_stage_exits_three_and_is_non_executable(self):
        candidate = self.root / "candidate.json"
        candidate.write_text(json.dumps(self.candidate), encoding="utf-8")
        run = subprocess.run([
            sys.executable, "-m", "max_trading_agent.research_registry",
            "stage", "--file", str(candidate),
            "--stage-dir", str(self.stage_dir),
        ], capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 3, run.stderr[:200])
        result = json.loads(run.stdout)
        self.assertEqual(result["status"], "T1_CANDIDATE_NOT_REGISTERED")
        self.assertFalse(result["tool_admitted"])
        self.assertTrue(self.stage_dir.exists())

    def test_cli_unknown_reference_and_invalid_number_blocks(self):
        commands = (
            ["evaluate", "--tool-id", "llm_new_feature", "--values", "[2, 4]"],
            ["evaluate", "--tool-id", "range_span_v1", "--values", "[NaN, 4]"],
            ["evaluate", "--tool-id", "range_span_v1", "--values", '"bad"'],
            ["stage", "--file", "does-not-exist"],
        )
        for args in commands:
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, "-m",
                                         "max_trading_agent.research_registry",
                                         *args], capture_output=True,
                                        text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("R0_REGISTRY_BLOCKED", result.stderr)


if __name__ == "__main__":
    unittest.main()
