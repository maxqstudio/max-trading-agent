"""G33: hostile GitHub Actions workflow supply-chain policy regression."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.workflow_security_audit import (
    WorkflowSecurityError, audit_workflows, inspect_workflow,
)

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = "11d5960a326750d5838078e36cf38b85af677262"
SETUP_PY = "a26af69be951a213d495a4c3e4e4022e16d87065"

BASE = f"""name: Example
on:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  safe-job:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@{CHECKOUT} # v4 pinned
        with:
          persist-credentials: false
      - uses: actions/setup-python@{SETUP_PY} # v5 pinned
        with:
          python-version: "3.12"
"""
SIGNER = BASE.replace(
    "  safe-job:\n    runs-on:",
    "  attest-isolated-tool-review:\n    permissions:\n"
    "      contents: read\n      id-token: write\n"
    "      attestations: write\n    runs-on:",
)


class WorkflowSupplyChainTests(unittest.TestCase):
    def verify(self, source=BASE, name="ci.yml"):
        return inspect_workflow(name, source)

    def test_actual_repository_has_only_sha_pinned_allowed_actions(self):
        result = audit_workflows(ROOT)
        self.assertEqual(result["status"], "G33_WORKFLOW_SECURITY_STATIC_AUDIT_PASS")
        self.assertGreaterEqual(result["workflow_count"], 9)
        self.assertGreaterEqual(result["pinned_action_references"], 20)
        self.assertEqual(result["privileged_job_count"], 1)
        self.assertEqual(result["signer_workflow"], "g24-signed-t0.yml")
        self.assertEqual(result["signer_job"], "attest-isolated-tool-review")
        self.assertEqual(result["decision"], "NO_TRADE")
        self.assertEqual(result["execution"], "DISABLED")
        self.assertFalse(result["third_party_action_code_audited"])
        self.assertFalse(result["dynamic_runner_behavior_proven_safe"])
        self.assertFalse(result["all_supply_chain_risks_eliminated"])
        self.assertEqual(
            len({x["workflow"] for x in result["workflows"]}),
            result["workflow_count"],
        )

    def test_base_valid_with_exact_allowlisted_commits(self):
        data = self.verify()
        self.assertEqual(data["workflow"], "ci.yml")
        self.assertEqual(data["checkout_credentials_not_persisted"], 1)
        self.assertEqual(data["pinned_actions"], 2)
        self.assertEqual(data["signature_jobs"], [])

    def test_tag_based_checkout_and_setup_python_blocked(self):
        for action, version in ((CHECKOUT, "v4"), (SETUP_PY, "v5")):
            with self.subTest(version=version), self.assertRaisesRegex(
                WorkflowSecurityError, "G33_UNREVIEWED_ACTION_REFERENCE"):
                self.verify(BASE.replace("@" + action, "@" + version))

    def test_short_sha_and_invalid_sha_blocked(self):
        for replacement in ("0" * 39, "Z" * 40, "0" * 41, "main"):
            with self.subTest(replacement=replacement[:8]), self.assertRaisesRegex(
                WorkflowSecurityError, "G33_UNREVIEWED_ACTION_REFERENCE"):
                self.verify(BASE.replace("@" + CHECKOUT, "@" + replacement))

    def test_unreviewed_valid_sha_cannot_replace_known_action(self):
        with self.assertRaisesRegex(WorkflowSecurityError,
                                    "G33_UNREVIEWED_ACTION_REFERENCE"):
            self.verify(BASE.replace("@" + CHECKOUT, "@" + "0" * 40))

    def test_lookalike_action_name_cannot_use_pinned_sha(self):
        with self.assertRaisesRegex(WorkflowSecurityError,
                                    "G33_UNREVIEWED_ACTION_REFERENCE"):
            self.verify(BASE.replace("actions/checkout@", "actions/checkout-helper@"))

    def test_third_party_and_local_reusable_uses_are_not_auto_allowed(self):
        for target in (
            "untrusted/actions@deadbeef",
            "third-party/audit@" + CHECKOUT,
            "./.github/actions/audit",
            "docker://alpine:latest",
        ):
            with self.subTest(target=target), self.assertRaises(WorkflowSecurityError):
                self.verify(BASE.replace("actions/setup-python@" + SETUP_PY,
                                         target))

    def test_expressions_cannot_generate_uses_reference(self):
        with self.assertRaises(WorkflowSecurityError):
            self.verify(BASE.replace("actions/setup-python@" + SETUP_PY,
                                     "${{ github.event.inputs.action }}"))

    def test_malformed_uses_or_empty_value_blocks(self):
        for line in ("      - uses:", "      - uses:  ", "      - uses: !!!"):
            source = BASE.replace(
                "      - uses: actions/setup-python@" + SETUP_PY + " # v5 pinned", line
            )
            with self.subTest(line=line), self.assertRaises(WorkflowSecurityError):
                self.verify(source)

    def test_missing_global_permissions_blocked(self):
        with self.assertRaisesRegex(WorkflowSecurityError,
                                    "G33_GLOBAL_PERMISSIONS_REQUIRED"):
            self.verify(BASE.replace("permissions:\n  contents: read", ""))

    def test_broad_global_write_permission_blocked(self):
        with self.assertRaisesRegex(WorkflowSecurityError,
                                    "G33_GLOBAL_PERMISSIONS_TOO_BROAD"):
            self.verify(BASE.replace("permissions:\n  contents: read",
                                     "permissions:\n  contents: write"))

    def test_global_id_token_write_blocked_even_in_signer(self):
        with self.assertRaisesRegex(WorkflowSecurityError,
                                    "G33_GLOBAL_PERMISSIONS_TOO_BROAD"):
            self.verify(SIGNER.replace("permissions:\n  contents: read",
                                       "permissions:\n  contents: read\n  id-token: write"),
                        name="g24-signed-t0.yml")

    def test_forged_pull_request_target_is_rejected(self):
        source = BASE.replace("  push:", "  pull_request_target:")
        with self.assertRaisesRegex(WorkflowSecurityError,
                                    "G33_UNTRUSTED_PRIVILEGED_TRIGGER"):
            self.verify(source)

    def test_workflow_run_privilege_escalation_is_rejected(self):
        with self.assertRaisesRegex(WorkflowSecurityError,
                                    "G33_UNTRUSTED_PRIVILEGED_TRIGGER"):
            self.verify(BASE.replace("  push:", "  workflow_run:"))

    def test_checkout_without_explicit_token_disable_is_rejected(self):
        source = BASE.replace("        with:\n          persist-credentials: false\n", "")
        with self.assertRaisesRegex(WorkflowSecurityError,
                                    "G33_CHECKOUT_TOKEN_PERSISTENCE"):
            self.verify(source)

    def test_checkout_persist_true_or_duplicate_keys_is_rejected(self):
        for source in (
            BASE.replace("persist-credentials: false", "persist-credentials: true"),
            BASE.replace("persist-credentials: false",
                         "persist-credentials: false\n          persist-credentials: true"),
        ):
            with self.subTest(source=source[-120:]), self.assertRaisesRegex(
                WorkflowSecurityError, "G33_CHECKOUT_TOKEN_PERSISTENCE"):
                self.verify(source)

    def test_checkout_does_not_accept_ambiguous_string_value(self):
        with self.assertRaisesRegex(WorkflowSecurityError,
                                    "G33_CHECKOUT_TOKEN_PERSISTENCE"):
            self.verify(BASE.replace("persist-credentials: false",
                                     'persist-credentials: "false"'))

    def test_signer_only_approved_issuer_has_exact_job_permissions(self):
        result = self.verify(SIGNER, name="g24-signed-t0.yml")
        self.assertEqual(result["signature_jobs"], ["attest-isolated-tool-review"])
        self.assertEqual(result["pinned_actions"], 2)

    def test_signer_missing_one_grant_fails_closed(self):
        for removed in ("      attestations: write\n", "      id-token: write\n"):
            source = SIGNER.replace(removed, "")
            with self.subTest(removed=removed), self.assertRaisesRegex(
                WorkflowSecurityError, "G33_INCOMPLETE_SIGNER_PERMISSION"):
                self.verify(source, name="g24-signed-t0.yml")

    def test_foreign_job_cannot_mint_oidc_attestations(self):
        with self.assertRaisesRegex(WorkflowSecurityError,
                                    "G33_UNEXPECTED_WRITE_PERMISSION"):
            self.verify(SIGNER.replace("attest-isolated-tool-review",
                                       "other-self-signing-job"),
                        name="g24-signed-t0.yml")

    def test_other_workflow_cannot_copy_signer_privilege(self):
        with self.assertRaisesRegex(WorkflowSecurityError,
                                    "G33_UNEXPECTED_WRITE_PERMISSION"):
            self.verify(SIGNER, name="ci.yml")

    def test_foreign_job_contents_write_cannot_override_global(self):
        source = BASE.replace("  safe-job:\n    runs-on:",
                              "  safe-job:\n    permissions:\n"
                              "      contents: write\n    runs-on:")
        with self.assertRaisesRegex(WorkflowSecurityError,
                                    "G33_JOB_CONTENTS_NOT_READ_ONLY"):
            self.verify(source)

    def test_missing_jobs_or_trigger_fails_closed(self):
        for source in (BASE.replace("jobs:", "not-jobs:"),
                       BASE.replace("on:", "not-on:")):
            with self.assertRaises(WorkflowSecurityError):
                self.verify(source)

    def test_untrusted_alias_of_permissions_rejected(self):
        for source in (
            BASE.replace("permissions:\n  contents: read",
                         "permissions: write-all"),
            BASE.replace("permissions:\n  contents: read",
                         "permissions:\n  contents: read\n  secrets: write"),
        ):
            with self.assertRaises(WorkflowSecurityError):
                self.verify(source)

    @unittest.skipIf(os.name == "nt", "Windows symlink privileges vary")
    def test_workflow_directory_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            target.mkdir()
            (root / ".github").mkdir()
            (root / ".github" / "workflows").symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(WorkflowSecurityError,
                                        "G33_UNSAFE_WORKFLOW_DIRECTORY"):
                audit_workflows(root)

    def test_cli_reports_only_no_trade_safe_fields(self):
        proc = subprocess.run(
            [sys.executable, "-m", "tools.workflow_security_audit", "--root", str(ROOT)],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["decision"], "NO_TRADE")
        self.assertEqual(report["owner_acceptance"], "PENDING")
        self.assertFalse(report["broker_execution"])
        self.assertTrue(report["checkout_credentials_disabled"])
        self.assertNotIn("API_KEY", proc.stdout)
        self.assertNotIn("GITHUB_TOKEN=", proc.stdout)


if __name__ == "__main__":
    unittest.main()
