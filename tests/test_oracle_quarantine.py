"""G22 oracle proposals are untrusted data; no model/provider/broker in CI."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from max_trading_agent.oracle_quarantine import (
    _digest, audit_hypothesis, propose_oracle_hypothesis, quarantine_hypothesis,
)
from max_trading_agent.tool_factory import FactoryError, validate_candidate

FIXTURE = Path(__file__).parent / "fixtures" / "tool_factory_range_span.json"


class FakeOracleClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete(self, role, prompt):
        self.calls.append((role, prompt))
        return self.response


class OracleQuarantineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.candidate = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.pack = {
            "tool_id": self.candidate["tool_id"],
            "candidate_sha256": validate_candidate(self.candidate)["sha256"],
            "hypothesis": "Compute maximum minus minimum of all input observations.",
            "cases": [
                {"input": [1, 3], "expected": 2},
                {"input": [-4, 5], "expected": 9},
                {"input": [4, 9, 2], "expected": 7},
                {"input": [11], "expected": 0},
            ],
            "properties": [
                "PERMUTATION_INVARIANT",
                "TRANSLATION_INVARIANT",
                "POSITIVE_SCALE_EQUIVARIANT",
            ],
        }
        self.proposals = self.root / "proposals"
        self.reviews = self.root / "reviews"

    def test_known_reference_consistency_is_still_quarantined(self):
        saved = quarantine_hypothesis(self.candidate, self.pack, self.proposals)
        receipt = audit_hypothesis(self.candidate, self.pack,
                                   audit_dir=self.reviews)
        self.assertEqual(saved["status"], "QUARANTINED_UNVERIFIED")
        self.assertEqual(receipt["status"], "QUARANTINED_REFERENCE_CONSISTENT")
        self.assertTrue(receipt["trusted_reference_checked"])
        self.assertFalse(receipt["trusted_oracle_adopted"])
        self.assertFalse(receipt["candidate_verified"])
        self.assertFalse(receipt["eligible_for_demo_or_live"])
        self.assertEqual(receipt["decision"], "NO_TRADE")
        self.assertEqual(receipt["execution"], "DISABLED")
        self.assertEqual(receipt["reference"], "NUMERIC_RANGE")
        self.assertEqual(receipt["novel_cases_vs_candidate_examples"], 4)
        self.assertEqual(saved["oracle_hypothesis_sha256"], _digest(self.pack))
        self.assertEqual(len(list(self.proposals.glob("*.json"))), 1)
        self.assertEqual(len(list(self.reviews.glob("*.json"))), 1)
        self.assertEqual(json.loads(
            (self.reviews / (receipt["review_id"] + ".json")).read_text()), receipt)
        self.assertNotIn(self.candidate["code"], json.dumps(receipt))

    def test_novel_tool_with_no_trusted_reference_never_passes(self):
        novel = copy.deepcopy(self.candidate)
        novel["tool_id"] = "invented_median_feature_v1"
        pack = copy.deepcopy(self.pack)
        pack["tool_id"] = novel["tool_id"]
        pack["candidate_sha256"] = validate_candidate(novel)["sha256"]
        pack["cases"][0]["expected"] = 999  # no reference exists
        receipt = audit_hypothesis(novel, pack, audit_dir=self.reviews)
        self.assertEqual(receipt["status"], "QUARANTINED_UNVERIFIED")
        self.assertIs(receipt["trusted_reference_checked"], False)
        self.assertIs(receipt["candidate_verified"], False)
        self.assertIs(receipt["trusted_oracle_adopted"], False)
        self.assertEqual(len(list(self.reviews.glob("*.json"))), 1)

    def test_false_expected_and_wrong_property_flagged_against_trusted_reference(self):
        wrong = copy.deepcopy(self.pack)
        wrong["cases"][0]["expected"] = 222
        wrong["properties"].append("TRANSLATION_EQUIVARIANT")
        result = audit_hypothesis(self.candidate, wrong, audit_dir=self.reviews)
        self.assertEqual(result["status"], "QUARANTINED_REFERENCE_MISMATCH")
        self.assertTrue(result["trusted_reference_checked"])
        self.assertFalse(result["trusted_oracle_adopted"])

    def test_copied_self_examples_are_not_coverage(self):
        copied = copy.deepcopy(self.pack)
        same = self.candidate["examples"][0]
        copied["cases"] = [
            copy.deepcopy(same),
            {"input": [1, 3], "expected": 2},
            {"input": [-4, 5], "expected": 9},
        ]
        result = audit_hypothesis(self.candidate, copied)
        self.assertEqual(result["status"], "QUARANTINED_INSUFFICIENT_COVERAGE")
        self.assertEqual(result["novel_cases_vs_candidate_examples"], 2)

    def test_cannot_rebind_candidate_hash_or_tool_id(self):
        for key, value in (
            ("candidate_sha256", "0" * 64),
            ("tool_id", "forged_tool"),
            ("candidate_sha256", "Z" * 64),
        ):
            pack = copy.deepcopy(self.pack)
            pack[key] = value
            with self.subTest(key=key, value=value[:12]):
                with self.assertRaisesRegex(FactoryError,
                                            "ORACLE_CANDIDATE_MISMATCH"):
                    quarantine_hypothesis(self.candidate, pack, self.proposals)
                self.assertFalse(self.proposals.exists())

    def test_duplicate_inputs_and_nonfinite_numbers_block_write(self):
        duplicated = copy.deepcopy(self.pack)
        duplicated["cases"][1]["input"] = [1.0, 3.0]
        with self.assertRaisesRegex(FactoryError, "DUPLICATE_ORACLE_INPUT"):
            quarantine_hypothesis(self.candidate, duplicated, self.proposals)
        for value in (True, float("nan"), float("inf"), 1e12):
            bad = copy.deepcopy(self.pack)
            bad["cases"][1]["expected"] = value
            with self.subTest(value=str(value)):
                with self.assertRaises(FactoryError):
                    quarantine_hypothesis(self.candidate, bad, self.proposals)
        self.assertFalse(self.proposals.exists())

    def test_injection_extra_keys_and_bad_properties_rejected(self):
        for key, value in (
            ("code", "import os; os.system('whoami')"),
            ("registry", "T3_LIVE"),
            ("execute", True),
        ):
            bad = copy.deepcopy(self.pack)
            bad[key] = value
            with self.assertRaisesRegex(FactoryError, "ORACLE_CANDIDATE_MISMATCH"):
                audit_hypothesis(self.candidate, bad)
        bad = copy.deepcopy(self.pack)
        bad["properties"].append("TRADE_MUTATION")
        with self.assertRaisesRegex(FactoryError, "INVALID_ORACLE_PROPERTIES"):
            audit_hypothesis(self.candidate, bad)
        self.assertFalse(self.reviews.exists())

    def test_model_receives_only_purpose_not_code_or_examples(self):
        client = FakeOracleClient(json.dumps(self.pack))
        output = propose_oracle_hypothesis(self.candidate, client)
        self.assertEqual(output, self.pack)
        self.assertEqual(len(client.calls), 1)
        role, prompt = client.calls[0]
        self.assertIn("UNTRUSTED", role)
        data = json.loads(prompt)
        self.assertEqual(data["tool_id"], self.candidate["tool_id"])
        self.assertEqual(data["purpose"], self.candidate["purpose"])
        self.assertNotIn(self.candidate["code"], prompt)
        self.assertNotIn('"examples"', prompt)
        self.assertNotIn("secret", json.dumps(output).lower())

    def test_corrupt_or_duplicate_llm_json_never_stored(self):
        messages = (
            "not-json",
            "{\"tool_id\":1,\"tool_id\":2}",
            '{"tool_id":"foo"}',
            "x" * 4501,
            None,
        )
        for raw in messages:
            with self.subTest(raw=str(raw)[:20]):
                with self.assertRaises(FactoryError):
                    propose_oracle_hypothesis(self.candidate, FakeOracleClient(raw))
        self.assertFalse(self.proposals.exists())

    def test_write_once_idempotent_and_conflict_fails_closed(self):
        first = quarantine_hypothesis(self.candidate, self.pack, self.proposals)
        self.assertEqual(first, quarantine_hypothesis(
            self.candidate, self.pack, self.proposals))
        file = self.proposals / first["proposal_filename"]
        file.write_text('{"tamper":"true"}', encoding="utf-8")
        with self.assertRaisesRegex(FactoryError, "ORACLE_PROPOSAL_CONFLICT"):
            quarantine_hypothesis(self.candidate, self.pack, self.proposals)
        self.assertEqual(file.read_text(), '{"tamper":"true"}')

    @unittest.skipIf(os.name == "nt", "Windows symlink privilege restricted")
    def test_symlink_quarantine_directory_denied(self):
        destination = self.root / "target"
        destination.mkdir()
        self.proposals.symlink_to(destination, target_is_directory=True)
        with self.assertRaisesRegex(FactoryError, "UNSAFE_ORACLE_DIRECTORY"):
            quarantine_hypothesis(self.candidate, self.pack, self.proposals)
        self.assertEqual(list(destination.iterdir()), [])

    def test_cli_offline_audit_quarantine_exit_3_with_no_provider(self):
        file = self.root / "candidate.json"
        pack = self.root / "oracle.json"
        file.write_text(json.dumps(self.candidate), encoding="utf-8")
        pack.write_text(json.dumps(self.pack), encoding="utf-8")
        run = subprocess.run([
            sys.executable, "-m", "max_trading_agent",
            "tool-oracle-audit", "--file", str(file),
            "--oracle-file", str(pack),
            "--audit-dir", str(self.reviews),
        ], capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 3, msg=run.stderr[:300])
        response = json.loads(run.stdout)
        self.assertEqual(response["status"],
                         "QUARANTINED_REFERENCE_CONSISTENT")
        self.assertEqual(response["decision"], "NO_TRADE")
        self.assertFalse(response["trusted_oracle_adopted"])
        self.assertTrue(self.reviews.exists())


if __name__ == "__main__":
    unittest.main()
