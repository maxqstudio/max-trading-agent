"""G24 signed Docker evidence: security regression without local Docker or network."""
import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from max_trading_agent.signed_evidence import (
    _context, _validate_evidence, generate_evidence, verify_signed_evidence,
)
from max_trading_agent.tool_factory import FactoryError, validate_candidate
from max_trading_agent.tool_properties import review_t0_properties

FIXTURE = Path(__file__).parent / "fixtures" / "tool_factory_range_span.json"
SHA = "a" * 40
REF = "refs/heads/feat/g24-signed-t0-evidence"
REPO = "maxqstudio/max-trading-agent"
WORKFLOW = REPO + "/.github/workflows/g24-signed-t0.yml"


def ci_env():
    return {
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_SHA": SHA, "GITHUB_REF": REF,
        "GITHUB_WORKFLOW_REF": WORKFLOW + "@" + REF,
        "GITHUB_RUN_ID": "4400001", "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_EVENT_NAME": "push", "RUNNER_ENVIRONMENT": "github-hosted",
    }


def fake_runner(candidate, cases):
    return {
        **validate_candidate(candidate),
        "status": "SANDBOX_PASSED_REVIEW_REQUIRED",
        "oracle_cases": len(cases),
    }


class SignedEvidenceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.evidence = self.path / "g24-t0-proof.json"
        self.bundle = self.path / "sigstore-bundle.json"
        self.bundle.write_text('{"fixture":"not-a-real-signature"}')
        self.candidate = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def produce(self):
        with patch("max_trading_agent.tool_properties.sandbox_evaluate",
                   side_effect=fake_runner):
            return generate_evidence(self.candidate, ci_env())

    def save(self, data):
        self.evidence.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

    def verified_result(self):
        import hashlib
        digest = hashlib.sha256(self.evidence.read_bytes()).hexdigest()
        return [{
            "verificationResult": {
                "signature": {"certificate": {"issuer": "test-only"}},
                "statement": {
                    "predicateType": "https://slsa.dev/provenance/v1",
                    "subject": [{"name": self.evidence.name,
                                 "digest": {"sha256": digest}}],
                },
            },
        }]

    def test_create_binds_candidate_g21_receipt_ci_provenance_and_no_trading(self):
        item = self.produce()
        self.assertEqual(item["source_commit"], SHA)
        self.assertEqual(item["source_ref"], REF)
        self.assertEqual(item["source_repository"], REPO)
        self.assertEqual(item["signer_workflow"], WORKFLOW)
        self.assertEqual(item["candidate_sha256"],
                         validate_candidate(self.candidate)["sha256"])
        self.assertEqual(item["golden_cases"], 5)
        self.assertEqual(item["property_cases"], 32)
        self.assertEqual(item["reference"], "NUMERIC_RANGE")
        self.assertEqual(item["status"], "T0_DOCKER_TEST_EVIDENCE")
        self.assertEqual(item["decision"], "NO_TRADE")
        self.assertEqual(item["execution"], "DISABLED")
        self.assertIs(item["broker_execution"], False)
        self.assertFalse(item["candidate_verified"])
        self.assertFalse(item["eligible_for_demo_or_live"])
        self.assertEqual(item["owner_acceptance"], "PENDING")
        self.assertTrue(item["attestation_verification_required"])
        self.assertNotIn("code", item)
        _validate_evidence(item, ci_env())

    def test_github_context_must_be_exact_scoped_hosted_push(self):
        for key, value in (
            ("GITHUB_REPOSITORY", "attacker/fork"),
            ("GITHUB_SHA", "z" * 40),
            ("GITHUB_REF", "refs/heads/main"),
            ("GITHUB_REF", "refs/tags/v1"),
            ("GITHUB_WORKFLOW_REF", "attacker/repo/.github/workflows/mal.yml@main"),
            ("GITHUB_EVENT_NAME", "pull_request"),
            ("RUNNER_ENVIRONMENT", "self-hosted"),
            ("GITHUB_RUN_ID", "x"),
            ("GITHUB_RUN_ATTEMPT", "0"),
        ):
            env = ci_env()
            env[key] = value
            with self.subTest(key=key, value=value[:25]):
                with self.assertRaisesRegex(FactoryError, "G24_UNTRUSTED_CI_CONTEXT"):
                    _context(env)

    def test_arbitrary_novel_tool_cannot_trigger_signed_evidence(self):
        self.candidate["tool_id"] = "unreviewed_new_indicator"
        with patch("max_trading_agent.signed_evidence.review_t0_properties") as review:
            with self.assertRaisesRegex(FactoryError, "G24_CURATED_TOOL_ONLY"):
                generate_evidence(self.candidate, ci_env())
        review.assert_not_called()

    def test_failed_docker_status_cannot_create_subject(self):
        receipt = {
            "status": "T0_PROPERTY_REJECTED", "decision": "NO_TRADE",
            "property_cases": 0,
        }
        with patch("max_trading_agent.signed_evidence.review_t0_properties",
                   return_value=receipt):
            with self.assertRaisesRegex(FactoryError, "G24_DOCKER_REVIEW_FAILED"):
                generate_evidence(self.candidate, ci_env())

    def test_tampered_g21_receipt_flags_and_counts_rejected(self):
        with patch("max_trading_agent.tool_properties.sandbox_evaluate",
                   side_effect=fake_runner):
            base = review_t0_properties(self.candidate, seed=93)
        for field, wrong in (
            ("candidate_sha256", "0" * 64),
            ("challenge_seed_hex", "f" * 32),
            ("golden_cases", 0),
            ("property_cases", 31),
            ("execution", "ENABLED"),
            ("broker_execution", True),
            ("review_id", "0" * 64),
        ):
            receipt = copy.deepcopy(base)
            receipt[field] = wrong
            with self.subTest(field=field), patch(
                "max_trading_agent.signed_evidence.review_t0_properties",
                return_value=receipt):
                with self.assertRaisesRegex(FactoryError, "G24_DOCKER_REVIEW_FAILED"):
                    generate_evidence(self.candidate, ci_env())

    def test_verifier_calls_gh_cryptographic_check_with_pinned_identity(self):
        item = self.produce()
        self.save(item)
        good = SimpleNamespace(returncode=0,
                               stdout=json.dumps(self.verified_result()))
        with patch("max_trading_agent.signed_evidence.subprocess.run",
                   return_value=good) as process:
            proof = verify_signed_evidence(self.evidence, self.bundle, ci_env())
        self.assertEqual(proof["status"], "G24_SIGSTORE_PROVENANCE_VERIFIED")
        self.assertTrue(proof["cryptographic_attestation_verified"])
        self.assertFalse(proof["tool_admitted"])
        self.assertEqual(proof["decision"], "NO_TRADE")
        cmd = process.call_args.args[0]
        self.assertEqual(cmd[:3], ["gh", "attestation", "verify"])
        self.assertIn("--signer-workflow", cmd)
        self.assertEqual(cmd[cmd.index("--signer-workflow")+1], WORKFLOW)
        self.assertEqual(cmd[cmd.index("--source-digest")+1], SHA)
        self.assertEqual(cmd[cmd.index("--source-ref")+1], REF)
        self.assertIn("--deny-self-hosted-runners", cmd)
        self.assertIn("--bundle", cmd)

    def test_malicious_response_without_verified_subject_rejected(self):
        item = self.produce()
        self.save(item)
        empty_subject = self.verified_result()
        empty_subject[0]["verificationResult"]["statement"]["subject"][0]["digest"]["sha256"] = "0"*64
        for data in ([], [{}], empty_subject, [
            {"verificationResult": {
                "statement": {"predicateType": "https://fake.invalid",
                              "subject": []},
                "signature": {"certificate": "x"},
            }},
        ]):
            with self.subTest(data=str(data)[:25]), patch(
                "max_trading_agent.signed_evidence.subprocess.run",
                return_value=SimpleNamespace(returncode=0,
                                             stdout=json.dumps(data))):
                with self.assertRaises(FactoryError):
                    verify_signed_evidence(self.evidence, self.bundle, ci_env())

    def test_gh_nonzero_or_unavailable_is_a_hard_block(self):
        item = self.produce()
        self.save(item)
        with patch("max_trading_agent.signed_evidence.subprocess.run",
                   return_value=SimpleNamespace(returncode=1, stdout="")):
            with self.assertRaisesRegex(FactoryError,
                                        "G24_SIGNATURE_VERIFICATION_FAILED"):
                verify_signed_evidence(self.evidence, self.bundle, ci_env())
        with patch("max_trading_agent.signed_evidence.subprocess.run",
                   side_effect=OSError("missing binary")):
            with self.assertRaisesRegex(FactoryError,
                                        "G24_SIGNATURE_VERIFICATION_UNAVAILABLE"):
                verify_signed_evidence(self.evidence, self.bundle, ci_env())

    def test_changed_unsigned_evidence_cannot_claim_any_authority(self):
        item = self.produce()
        item["decision"] = "BUY"
        self.save(item)
        with patch("max_trading_agent.signed_evidence.subprocess.run") as gh:
            with self.assertRaisesRegex(FactoryError, "G24_EVIDENCE_INVALID"):
                verify_signed_evidence(self.evidence, self.bundle, ci_env())
        gh.assert_not_called()

    @unittest.skipIf(os.name == "nt", "Windows symlinks need elevated privileges")
    def test_bundle_or_evidence_symlink_rejected(self):
        item = self.produce()
        self.save(item)
        self.evidence.unlink()
        self.evidence.symlink_to(self.bundle)
        with self.assertRaisesRegex(FactoryError, "G24_EVIDENCE_UNAVAILABLE"):
            verify_signed_evidence(self.evidence, self.bundle, ci_env())

    def test_missing_evidence_or_bundle_fails_closed(self):
        with self.assertRaisesRegex(FactoryError, "G24_EVIDENCE_UNAVAILABLE"):
            verify_signed_evidence(self.evidence, self.bundle, ci_env())
        self.evidence.write_text('{"not":"g24"}')
        self.bundle.unlink()
        with self.assertRaisesRegex(FactoryError, "G24_EVIDENCE_UNAVAILABLE"):
            verify_signed_evidence(self.evidence, self.bundle, ci_env())


if __name__ == "__main__":
    unittest.main()
