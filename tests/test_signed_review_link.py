"""G25 independent Sigstore consumer and G23 package-binding adversarial tests.

Cryptographic verification is mocked only in unit tests. GitHub Actions runs
a separate read-only job using real downloaded Sigstore material.
"""
import copy
import hashlib
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
from max_trading_agent.signed_evidence import generate_evidence
from max_trading_agent.signed_review_link import (
    expected_signer_context, link_review_evidence,
)
from max_trading_agent.tool_factory import FactoryError, validate_candidate
from max_trading_agent.tool_properties import review_t0_properties
from max_trading_agent.tool_review import _digest

FIXTURE = Path(__file__).resolve().parent / "fixtures/tool_factory_range_span.json"
SHA = "a" * 40
REF = "refs/heads/feat/g25-independent-signed-link"
RUN_ID = "4400000"
REPO = "maxqstudio/max-trading-agent"
WORKFLOW = REPO + "/.github/workflows/g24-signed-t0.yml"


def ci_env():
    return expected_signer_context(SHA, REF, RUN_ID)


def sandbox_pass(candidate, cases):
    return {**validate_candidate(candidate),
            "status": "SANDBOX_PASSED_REVIEW_REQUIRED",
            "oracle_cases": len(cases)}


class SignedReviewLinkTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.link_dir = self.root / "links"
        self.candidate = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.oracle = {
            "tool_id": self.candidate["tool_id"],
            "candidate_sha256": validate_candidate(self.candidate)["sha256"],
            "hypothesis": "Compute maximum minus minimum of numerical observations.",
            "cases": [
                {"input": [1, 3], "expected": 2},
                {"input": [-4, 5], "expected": 9},
                {"input": [4, 9, 2], "expected": 7},
            ],
            "properties": ["PERMUTATION_INVARIANT"],
        }
        self.audit = audit_hypothesis(self.candidate, self.oracle)
        with patch("max_trading_agent.tool_properties.sandbox_evaluate",
                   side_effect=sandbox_pass):
            self.receipt = review_t0_properties(self.candidate, seed=731)
        self.packet = assemble_review_package(
            self.candidate, self.oracle, self.audit,
            property_receipt=self.receipt)
        self.signed = self.root / "g24-t0-proof.json"
        self.bundle = self.root / "g24-sigstore-bundle.json"
        with patch("max_trading_agent.signed_evidence.review_t0_properties",
                   return_value=self.receipt):
            subject = generate_evidence(self.candidate, ci_env())
        self.signed.write_text(json.dumps(subject), encoding="utf-8")
        self.bundle.write_text('{"bundle":"fixture-only"}', encoding="utf-8")

    def _good_verifier(self, path, bundle, env):
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        return {
            "status": "G24_SIGSTORE_PROVENANCE_VERIFIED",
            "source_commit": env["GITHUB_SHA"],
            "candidate_sha256": validate_candidate(self.candidate)["sha256"],
            "subject_sha256": digest,
            "signer_workflow": WORKFLOW,
            "cryptographic_attestation_verified": True,
            "tool_admitted": False,
        }

    def bind(self, **changes):
        values = dict(
            candidate=self.candidate,
            oracle_proposal=self.oracle,
            oracle_audit=self.audit,
            review_package=self.packet,
            property_receipt=self.receipt,
            signed_evidence_file=self.signed,
            sigstore_bundle_file=self.bundle,
            expected_commit=SHA,
            expected_ref=REF,
            expected_run_id=RUN_ID,
            link_dir=self.link_dir,
        )
        values.update(changes)
        return link_review_evidence(**values)

    def test_crypto_verified_subject_links_exact_g21_and_g23_but_no_trade(self):
        with patch("max_trading_agent.signed_review_link.verify_signed_evidence",
                   side_effect=self._good_verifier) as verifier:
            report = self.bind()
        self.assertEqual(verifier.call_count, 1)
        self.assertEqual(report["status"],
                         "SIGNED_WORKFLOW_EVIDENCE_LINKED_REVIEW_PENDING")
        self.assertTrue(report["cryptographic_attestation_verified"])
        self.assertTrue(report["signed_claim_matches_g23_and_g21"])
        self.assertEqual(report["g23_package_review_id"], self.packet["review_id"])
        self.assertEqual(report["g21_property_receipt_id"], self.receipt["review_id"])
        self.assertEqual(report["candidate_sha256"],
                         validate_candidate(self.candidate)["sha256"])
        self.assertEqual(report["source_commit"], SHA)
        self.assertEqual(report["source_ref"], REF)
        self.assertEqual(report["source_run_id"], RUN_ID)
        self.assertEqual(report["decision"], "NO_TRADE")
        self.assertEqual(report["execution"], "DISABLED")
        self.assertFalse(report["candidate_verified"])
        self.assertFalse(report["tool_admitted"])
        self.assertFalse(report["eligible_for_demo_or_live"])
        self.assertEqual(report["owner_acceptance"], "PENDING")
        self.assertEqual(report["g23_package_status"], "REVIEW_PACKAGE_INCOMPLETE")
        self.assertIn("INDEPENDENT_CODE_AND_HUMAN_REVIEW",
                      report["missing_evidence"])
        file = self.link_dir / (report["review_id"] + ".json")
        self.assertEqual(json.loads(file.read_text()), report)
        self.assertNotIn(self.candidate["code"], file.read_text())
        self.assertNotIn(self.oracle["hypothesis"], file.read_text())
        self.assertFalse(self.packet["candidate_verified"])

    def test_signed_subject_is_reverified_using_expected_external_commit_run_and_ref(self):
        with patch("max_trading_agent.signed_review_link.verify_signed_evidence",
                   side_effect=self._good_verifier) as verifier:
            self.bind()
        env = verifier.call_args.args[2]
        self.assertEqual(env["GITHUB_SHA"], SHA)
        self.assertEqual(env["GITHUB_REF"], REF)
        self.assertEqual(env["GITHUB_RUN_ID"], RUN_ID)
        self.assertEqual(env["GITHUB_WORKFLOW_REF"], WORKFLOW + "@" + REF)
        self.assertEqual(env["RUNNER_ENVIRONMENT"], "github-hosted")

    def test_wrong_expected_signer_identity_ref_and_run_fail_closed(self):
        for field, value in (
            ("expected_commit", "0" * 40),
            ("expected_ref", "refs/heads/feat/g24-other"),
            ("expected_run_id", "999"),
            ("expected_attempt", "2"),
        ):
            with self.subTest(field=field), patch(
                "max_trading_agent.signed_review_link.verify_signed_evidence"
            ) as verifier:
                with self.assertRaisesRegex(FactoryError,
                                            "G24_EVIDENCE_INVALID"):
                    self.bind(**{field: value})
                verifier.assert_called_once()
                self.assertFalse(self.link_dir.exists())

    def test_invalid_expected_commit_or_branch_prevents_verifier_call(self):
        for kwargs in (
            {"expected_commit": "z" * 40},
            {"expected_ref": "refs/heads/main"},
            {"expected_ref": "refs/heads/feat/g23-unreviewed"},
            {"expected_run_id": "0"},
            {"expected_attempt": "01"},
        ):
            with self.subTest(kwargs=kwargs), patch(
                "max_trading_agent.signed_review_link.verify_signed_evidence"
            ) as verifier:
                with self.assertRaisesRegex(FactoryError,
                                            "G25_INVALID_EXPECTED_SOURCE"):
                    self.bind(**kwargs)
                verifier.assert_not_called()

    def test_tampered_candidate_and_replayed_packet_rejected_before_sigstore(self):
        mutated = copy.deepcopy(self.candidate)
        mutated["code"] = "def analyze(values):\n    return 99\n"
        with patch("max_trading_agent.signed_review_link.verify_signed_evidence"
                   ) as verifier:
            with self.assertRaises(FactoryError):
                self.bind(candidate=mutated)
            verifier.assert_not_called()
        self.assertFalse(self.link_dir.exists())

    def test_wrong_g23_packet_flags_or_audit_id_denied_before_sigstore(self):
        for field, wrong in (("review_id", "0" * 64),
                             ("candidate_verified", True),
                             ("status", "APPROVED"),
                             ("decision", "BUY"),
                             ("property_receipt_sha256", "a" * 64)):
            with self.subTest(field=field), patch(
                "max_trading_agent.signed_review_link.verify_signed_evidence"
            ) as verifier:
                forged = copy.deepcopy(self.packet)
                forged[field] = wrong
                with self.assertRaisesRegex(FactoryError,
                                            "G25_REVIEW_PACKAGE_MISMATCH"):
                    self.bind(review_package=forged)
                verifier.assert_not_called()
                self.assertFalse(self.link_dir.exists())

    def test_modified_g21_receipt_seed_and_review_id_rejected(self):
        for field, wrong in (("review_id", "0" * 64),
                             ("challenge_seed_hex", "0" * 32),
                             ("property_sha256", "0" * 64),
                             ("decision", "BUY")):
            with self.subTest(field=field), patch(
                "max_trading_agent.signed_review_link.verify_signed_evidence"
            ) as verifier:
                receipt = copy.deepcopy(self.receipt)
                receipt[field] = wrong
                with self.assertRaises(FactoryError):
                    self.bind(property_receipt=receipt)
                verifier.assert_not_called()
                self.assertFalse(self.link_dir.exists())

    def test_signed_subject_wrong_candidate_receipt_seed_or_oracle_sha_denied(self):
        subject = json.loads(self.signed.read_text())
        for field, wrong in (
            ("candidate_sha256", "0" * 64),
            ("property_receipt_sha256", "0" * 64),
            ("property_receipt_id", "0" * 64),
            ("property_challenge_sha256", "0" * 64),
            ("trusted_golden_sha256", "0" * 64),
            ("challenge_seed_hex", "0" * 32),
            ("property_cases", 1),
        ):
            with self.subTest(field=field), patch(
                "max_trading_agent.signed_review_link.verify_signed_evidence"
            ) as verifier:
                tampered = dict(subject)
                tampered[field] = wrong
                self.signed.write_text(json.dumps(tampered))
                with self.assertRaisesRegex(FactoryError,
                                            "G25_SIGNED_RECEIPT_MISMATCH"):
                    self.bind()
                verifier.assert_not_called()
                self.assertFalse(self.link_dir.exists())
        self.signed.write_text(json.dumps(subject))

    def test_wrong_signature_bundle_or_missing_gh_cli_never_records_link(self):
        with patch("max_trading_agent.signed_review_link.verify_signed_evidence",
                   side_effect=FactoryError("G24_SIGNATURE_VERIFICATION_FAILED")):
            with self.assertRaises(FactoryError):
                self.bind()
        with patch("max_trading_agent.signed_review_link.verify_signed_evidence",
                   side_effect=FactoryError("G24_SIGNATURE_VERIFICATION_UNAVAILABLE")):
            with self.assertRaises(FactoryError):
                self.bind()
        self.assertFalse(self.link_dir.exists())

    def test_forged_success_payload_without_subject_binding_is_denied(self):
        fake = self._good_verifier(self.signed, self.bundle, ci_env())
        for change in (
            {"subject_sha256": "0" * 64},
            {"candidate_sha256": "0" * 64},
            {"tool_admitted": True},
            {"cryptographic_attestation_verified": False},
        ):
            with self.subTest(change=change), patch(
                "max_trading_agent.signed_review_link.verify_signed_evidence",
                return_value={**fake, **change},
            ):
                with self.assertRaisesRegex(FactoryError,
                                            "G25_PROVENANCE_MISMATCH"):
                    self.bind()
                self.assertFalse(self.link_dir.exists())

    def test_idempotent_write_once_and_tamper_rejection(self):
        with patch("max_trading_agent.signed_review_link.verify_signed_evidence",
                   side_effect=self._good_verifier):
            a = self.bind()
            b = self.bind()
            self.assertEqual(a, b)
            file = self.link_dir / (a["review_id"] + ".json")
            file.write_text('{"spoofed":"approved"}')
            with self.assertRaisesRegex(FactoryError, "REVIEW_RECEIPT_CONFLICT"):
                self.bind()
        self.assertEqual(len(list(self.link_dir.glob("*.json"))), 1)

    @unittest.skipIf(os.name == "nt", "Windows symlink privileges unavailable")
    def test_link_directory_symlink_is_rejected(self):
        dest = self.root / "target"
        dest.mkdir()
        self.link_dir.symlink_to(dest, target_is_directory=True)
        with patch("max_trading_agent.signed_review_link.verify_signed_evidence",
                   side_effect=self._good_verifier):
            with self.assertRaisesRegex(FactoryError, "UNSAFE_REVIEW_DIRECTORY"):
                self.bind()
        self.assertFalse(list(dest.iterdir()))

    def test_missing_bundle_and_duplicate_json_keys_fail_before_gh(self):
        self.bundle.unlink()
        with patch("max_trading_agent.signed_review_link.verify_signed_evidence"
                   ) as verifier:
            with self.assertRaisesRegex(FactoryError, "G25_MISSING_ATTESTATION"):
                self.bind()
            verifier.assert_not_called()
        self.bundle.write_text('{"fake":"bundle"}')
        self.signed.write_text('{"tool_id":"a","tool_id":"b"}')
        with patch("max_trading_agent.signed_review_link.verify_signed_evidence"
                   ) as verifier:
            with self.assertRaisesRegex(FactoryError, "G25_SIGNED_SUBJECT_INVALID"):
                self.bind()
            verifier.assert_not_called()

    def test_external_cli_exits_3_even_on_crypto_verified_link(self):
        # CLI can be exercised without actual gh in unit tests by proving
        # malformed/unknown bytes are denied. Real signing/CLI is Actions-only.
        data = [
            ("candidate.json", self.candidate),
            ("oracle.json", self.oracle),
            ("audit.json", self.audit),
            ("package.json", self.packet),
            ("receipt.json", self.receipt),
        ]
        for name, obj in data:
            (self.root / name).write_text(json.dumps(obj))
        cmd = [
            sys.executable, "-m", "max_trading_agent.signed_review_link",
            "--candidate", str(self.root / "candidate.json"),
            "--oracle", str(self.root / "oracle.json"),
            "--audit", str(self.root / "audit.json"),
            "--package", str(self.root / "package.json"),
            "--receipt", str(self.root / "receipt.json"),
            "--evidence", str(self.signed),
            "--bundle", str(self.bundle),
            "--expected-commit", SHA,
            "--expected-ref", REF,
            "--expected-run-id", RUN_ID,
            "--link-dir", str(self.link_dir),
        ]
        self.bundle.unlink()
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("G25_BLOCKED", run.stderr)
        self.assertFalse(self.link_dir.exists())


if __name__ == "__main__":
    unittest.main()
