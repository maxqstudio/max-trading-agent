"""G23 review packet integrity: no generated candidate code executes here."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from max_trading_agent.oracle_quarantine import audit_hypothesis
from max_trading_agent.review_package import assemble_review_package
from max_trading_agent.tool_factory import FactoryError, validate_candidate
from max_trading_agent.tool_properties import review_t0_properties
from max_trading_agent.tool_review import _digest

FIXTURE = Path(__file__).parent / "fixtures" / "tool_factory_range_span.json"


def pass_sandbox(candidate, cases):
    return {
        **validate_candidate(candidate),
        "status": "SANDBOX_PASSED_REVIEW_REQUIRED",
        "oracle_cases": len(cases),
    }


class ReviewPackageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dir = self.root / "packets"
        self.candidate = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.pack = {
            "tool_id": self.candidate["tool_id"],
            "candidate_sha256": validate_candidate(self.candidate)["sha256"],
            "hypothesis": "Calculate maximum minus minimum of bounded observations.",
            "cases": [
                {"input": [1, 4], "expected": 3},
                {"input": [-5, 8], "expected": 13},
                {"input": [6, 12, 7], "expected": 6},
            ],
            "properties": ["PERMUTATION_INVARIANT"],
        }
        self.audit = audit_hypothesis(self.candidate, self.pack)

    def property_receipt(self):
        with patch("max_trading_agent.tool_properties.sandbox_evaluate",
                   side_effect=pass_sandbox):
            return review_t0_properties(self.candidate, seed=101)

    def test_known_contract_package_without_property_receipt_lists_evidence_gaps(self):
        result = assemble_review_package(
            self.candidate, self.pack, self.audit, package_dir=self.dir)
        self.assertEqual(result["status"], "REVIEW_PACKAGE_INCOMPLETE")
        self.assertEqual(result["oracle_audit_status"],
                         "QUARANTINED_REFERENCE_CONSISTENT")
        self.assertTrue(result["trusted_reference_available"])
        self.assertEqual(result["property_receipt_integrity"], "ABSENT")
        self.assertIn("T0_PROPERTY_REVIEW_RECEIPT", result["missing_evidence"])
        self.assertIn("SIGNED_SANDBOX_EXECUTION_ATTESTATION",
                      result["missing_evidence"])
        self.assertEqual(result["decision"], "NO_TRADE")
        self.assertEqual(result["execution"], "DISABLED")
        self.assertFalse(result["candidate_verified"])
        self.assertFalse(result["trusted_oracle_adopted"])
        self.assertFalse(result["eligible_for_demo_or_live"])
        path = self.dir / (result["review_id"] + ".json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), result)
        self.assertNotIn(self.candidate["code"], path.read_text(encoding="utf-8"))
        self.assertNotIn(self.pack["hypothesis"],
                         path.read_text(encoding="utf-8"))

    def test_hash_consistent_property_receipt_is_explicitly_unattested(self):
        receipt = self.property_receipt()
        packet = assemble_review_package(
            self.candidate, self.pack, self.audit,
            property_receipt=receipt, package_dir=self.dir)
        self.assertEqual(packet["property_receipt_integrity"],
                         "STRUCTURAL_ONLY_UNATTESTED")
        self.assertEqual(packet["property_receipt_id"], receipt["review_id"])
        self.assertEqual(packet["property_receipt_sha256"], _digest(receipt))
        self.assertEqual(packet["status"], "REVIEW_PACKAGE_INCOMPLETE")
        self.assertNotIn("T0_PROPERTY_REVIEW_RECEIPT", packet["missing_evidence"])
        self.assertIn("SIGNED_SANDBOX_EXECUTION_ATTESTATION",
                      packet["missing_evidence"])
        self.assertIn("INDEPENDENT_HUMAN_REVIEW", packet["missing_evidence"])
        self.assertIn("OWNER_ACCEPTANCE", packet["missing_evidence"])
        self.assertFalse(packet["package_is_execution_attestation"])

    def test_unknown_contract_needs_independent_reference_and_goldens(self):
        candidate = copy.deepcopy(self.candidate)
        candidate["tool_id"] = "entirely_new_formula_v1"
        pack = copy.deepcopy(self.pack)
        pack["tool_id"] = candidate["tool_id"]
        pack["candidate_sha256"] = validate_candidate(candidate)["sha256"]
        pack["cases"][0]["expected"] = 333
        audit = audit_hypothesis(candidate, pack)
        packet = assemble_review_package(candidate, pack, audit,
                                         package_dir=self.dir)
        self.assertEqual(packet["oracle_audit_status"],
                         "QUARANTINED_UNVERIFIED")
        self.assertFalse(packet["trusted_reference_available"])
        self.assertIn("INDEPENDENT_REFERENCE_CONTRACT",
                      packet["missing_evidence"])
        self.assertIn("INDEPENDENT_GOLDEN_ORACLES", packet["missing_evidence"])
        self.assertFalse(packet["candidate_verified"])

    def test_forged_audit_metadata_or_hash_rejected_before_write(self):
        for field, replacement in (
            ("status", "QUARANTINED_REFERENCE_CONSISTENT_APPROVED"),
            ("candidate_sha256", "0" * 64),
            ("review_id", "0" * 64),
            ("trusted_oracle_adopted", True),
            ("decision", "BUY"),
        ):
            bad = copy.deepcopy(self.audit)
            bad[field] = replacement
            with self.subTest(field=field):
                with self.assertRaisesRegex(FactoryError,
                                            "ORACLE_AUDIT_MISMATCH"):
                    assemble_review_package(
                        self.candidate, self.pack, bad, package_dir=self.dir)
                self.assertFalse(self.dir.exists())

    def test_mutated_candidate_and_different_pack_cannot_reuse_audit(self):
        changed = copy.deepcopy(self.candidate)
        changed["code"] = "def analyze(values):\n    return 5\n"
        with self.assertRaises(FactoryError):
            assemble_review_package(changed, self.pack, self.audit,
                                    package_dir=self.dir)
        altered = copy.deepcopy(self.pack)
        altered["cases"][0]["expected"] = 77
        with self.assertRaisesRegex(FactoryError, "ORACLE_AUDIT_MISMATCH"):
            assemble_review_package(self.candidate, altered, self.audit,
                                    package_dir=self.dir)
        self.assertFalse(self.dir.exists())

    def test_unsigned_property_receipt_cannot_forge_hashes_or_authority(self):
        receipt = self.property_receipt()
        for field, replacement in (
            ("property_sha256", "0" * 64),
            ("challenge_seed_hex", "g" * 32),
            ("candidate_sha256", "0" * 64),
            ("status", "T3_LIVE_APPROVED"),
            ("execution", "ENABLED"),
            ("eligible_for_demo_or_live", True),
            ("oracle_cases", 100),  # not a valid G21 receipt field
            ("review_id", "0" * 64),
        ):
            bad = copy.deepcopy(receipt)
            bad[field] = replacement
            with self.subTest(field=field):
                with self.assertRaises(FactoryError):
                    assemble_review_package(self.candidate, self.pack,
                                            self.audit, property_receipt=bad,
                                            package_dir=self.dir)
                self.assertFalse(self.dir.exists())

    def test_referencing_unreviewed_math_with_claimed_receipt_fails(self):
        receipt = self.property_receipt()
        candidate = copy.deepcopy(self.candidate)
        candidate["tool_id"] = "new_trend_computation_v1"
        pack = copy.deepcopy(self.pack)
        pack["tool_id"] = candidate["tool_id"]
        pack["candidate_sha256"] = validate_candidate(candidate)["sha256"]
        audit = audit_hypothesis(candidate, pack)
        receipt["tool_id"] = candidate["tool_id"]
        receipt["candidate_sha256"] = validate_candidate(candidate)["sha256"]
        with self.assertRaisesRegex(FactoryError, "PROPERTY_REFERENCE_MISSING"):
            assemble_review_package(candidate, pack, audit,
                                    property_receipt=receipt,
                                    package_dir=self.dir)
        self.assertFalse(self.dir.exists())

    def test_replay_idempotent_and_write_once_tamper_denied(self):
        one = assemble_review_package(
            self.candidate, self.pack, self.audit, package_dir=self.dir)
        two = assemble_review_package(
            self.candidate, self.pack, self.audit, package_dir=self.dir)
        self.assertEqual(one, two)
        self.assertEqual(len(list(self.dir.glob("*.json"))), 1)
        path = self.dir / (one["review_id"] + ".json")
        path.write_text('{"fake":true}', encoding="utf-8")
        with self.assertRaisesRegex(FactoryError, "REVIEW_RECEIPT_CONFLICT"):
            assemble_review_package(self.candidate, self.pack, self.audit,
                                    package_dir=self.dir)
        self.assertEqual(path.read_text(encoding="utf-8"), '{"fake":true}')

    @unittest.skipIf(os.name == "nt", "Windows symlink privilege restricted")
    def test_symlink_package_directory_rejected(self):
        target = self.root / "target"
        target.mkdir()
        self.dir.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(FactoryError, "UNSAFE_REVIEW_DIRECTORY"):
            assemble_review_package(
                self.candidate, self.pack, self.audit, package_dir=self.dir)
        self.assertEqual(list(target.iterdir()), [])

    def test_offline_cli_emits_incomplete_and_exits_nonzero(self):
        candidate_path = self.root / "candidate.json"
        pack_path = self.root / "oracle.json"
        audit_path = self.root / "audit.json"
        for path, data in (
            (candidate_path, self.candidate), (pack_path, self.pack),
            (audit_path, self.audit),
        ):
            path.write_text(json.dumps(data), encoding="utf-8")
        command = [
            sys.executable, "-m", "max_trading_agent", "tool-review-package",
            "--file", str(candidate_path), "--oracle-file", str(pack_path),
            "--audit-file", str(audit_path), "--package-dir", str(self.dir),
        ]
        run = subprocess.run(command, capture_output=True, text=True,
                             timeout=20)
        self.assertEqual(run.returncode, 3, msg=run.stderr[:300])
        report = json.loads(run.stdout)
        self.assertEqual(report["status"], "REVIEW_PACKAGE_INCOMPLETE")
        self.assertEqual(report["decision"], "NO_TRADE")
        self.assertTrue((self.dir / (report["review_id"] + ".json")).is_file())
        self.assertNotIn(self.candidate["code"], run.stdout)

    def test_cli_duplicate_json_keys_and_tampering_rejected_without_packet(self):
        candidate_path = self.root / "candidate.json"
        pack_path = self.root / "oracle.json"
        audit_path = self.root / "audit.json"
        candidate_path.write_text(json.dumps(self.candidate), encoding="utf-8")
        pack_path.write_text('{"tool_id":"x","tool_id":"y"}', encoding="utf-8")
        audit_path.write_text(json.dumps(self.audit), encoding="utf-8")
        cmd = [
            sys.executable, "-m", "max_trading_agent", "tool-review-package",
            "--file", str(candidate_path), "--oracle-file", str(pack_path),
            "--audit-file", str(audit_path), "--package-dir", str(self.dir),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertIn("TOOL_FACTORY_BLOCKED", result.stderr)
        self.assertFalse(self.dir.exists())

    def test_missing_files_fail_without_reading_any_candidate_code(self):
        with self.assertRaisesRegex(FactoryError, "ORACLE_AUDIT_MISMATCH"):
            assemble_review_package(self.candidate, self.pack, {},
                                    package_dir=self.dir)
        self.assertFalse(self.dir.exists())


if __name__ == "__main__":
    unittest.main()
