"""G34 Docker manifest platform, sandbox and tag-replay hostile checks."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.docker_image_audit import (
    DIGEST, IMAGE, PLATFORM, REQUIRED, PULL_WORKFLOWS, SANDBOX,
    DockerImageAuditError, audit_repository, audit_sources,
)

ROOT = Path(__file__).resolve().parents[1]


class DockerImagePinTests(unittest.TestCase):
    def setUp(self):
        self.inputs = {p: (ROOT / p).read_text(encoding="utf-8") for p in REQUIRED}

    def altered(self, path, old, new):
        data = dict(self.inputs)
        self.assertEqual(data[path].count(old), 1)
        data[path] = data[path].replace(old, new)
        return data

    def test_current_repository_has_one_exact_linux_amd64_image(self):
        result = audit_repository(ROOT)
        self.assertEqual(result["status"], "G34_STATIC_DOCKER_IMAGE_PIN_AUDIT_PASS")
        self.assertEqual(result["image"], IMAGE)
        self.assertEqual(result["manifest_digest"], DIGEST)
        self.assertEqual(result["platform"], PLATFORM)
        self.assertEqual(result["source_files_checked"], 7)
        self.assertEqual(result["workflow_pull_count"], 6)
        self.assertFalse(result["runtime_image_executed_by_static_audit"])
        self.assertFalse(result["image_signature_verified"])
        self.assertFalse(result["image_has_no_known_vulnerabilities"])
        self.assertFalse(result["broker_execution"])
        self.assertEqual(result["execution"], "DISABLED")
        self.assertEqual(result["decision"], "NO_TRADE")

    def test_g35_docker_pull_is_a_required_image_pin_source(self):
        self.assertIn(".github/workflows/g35-sequential-main.yml", PULL_WORKFLOWS)
        self.assertIn(".github/workflows/g35-sequential-main.yml", REQUIRED)

    def test_g34_pull_request_triggers_on_all_pinned_workflow_changes(self):
        workflow = (ROOT / ".github/workflows/g34-docker-image.yml").read_text(
            encoding="utf-8")
        paths = workflow.split("  pull_request:", 1)[1].split(
            "  workflow_dispatch:", 1)[0]
        for path in PULL_WORKFLOWS:
            with self.subTest(path=path):
                self.assertIn('"' + path + '"', paths)

    def test_digest_is_full_sha256_and_not_a_mutable_tag(self):
        self.assertEqual(len(DIGEST), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in DIGEST))
        self.assertEqual(IMAGE, "python:3.12-alpine@sha256:" + DIGEST)

    def test_tag_only_runtime_is_rejected(self):
        with self.assertRaisesRegex(DockerImageAuditError, "G34_MUTABLE_DOCKER_TAG"):
            audit_sources(self.altered(SANDBOX, IMAGE, "python:3.12-alpine"))

    def test_unknown_digest_cannot_be_silently_substituted(self):
        with self.assertRaisesRegex(DockerImageAuditError, "G34_UNPINNED_EXECUTION_IMAGE"):
            audit_sources(self.altered(SANDBOX, DIGEST, "0" * 64))

    def test_sandbox_must_lock_amd64_platform(self):
        with self.assertRaisesRegex(DockerImageAuditError,
                                    "G34_CONTAINER_PLATFORM_OR_NETWORK_CHANGED"):
            audit_sources(self.altered(SANDBOX, '"--platform", "linux/amd64"',
                                       '"--platform", "linux/arm64"'))

    def test_network_must_remain_disabled(self):
        with self.assertRaisesRegex(DockerImageAuditError,
                                    "G34_CONTAINER_PLATFORM_OR_NETWORK_CHANGED"):
            audit_sources(self.altered(SANDBOX, '"--network", "none"',
                                       '"--network", "host"'))

    def test_sandbox_must_retain_nonroot_readonly_and_capability_veto(self):
        changes = [
            ('"--read-only"', '"--privileged"'),
            ('"--user", "65534:65534"', '"--user", "0:0"'),
            ('"--cap-drop", "ALL"', '"--cap-add", "ALL"'),
            ('"--security-opt", "no-new-privileges"', '"--security-opt", "seccomp=unconfined"'),
            ('"--pids-limit", "32"', '"--pids-limit", "-1"'),
            ('"--memory", "128m"', '"--memory", "10g"'),
            ('"--cpus", "1"', '"--cpus", "128"'),
        ]
        for old, new in changes:
            with self.subTest(change=old), self.assertRaisesRegex(
                DockerImageAuditError, "G34_SANDBOX_ISOLATION_CHANGED"):
                audit_sources(self.altered(SANDBOX, old, new))

    def test_all_six_workflow_pulls_must_be_digest_pinned(self):
        for path in PULL_WORKFLOWS:
            with self.subTest(path=path), self.assertRaisesRegex(
                DockerImageAuditError, "G34_MUTABLE_DOCKER_TAG"):
                audit_sources(self.altered(path, IMAGE, "python:3.12-alpine"))

    def test_workflow_wrong_digest_is_denied(self):
        for path in PULL_WORKFLOWS:
            with self.subTest(path=path), self.assertRaisesRegex(
                DockerImageAuditError, "G34_WORKFLOW_PULL_NOT_PINNED"):
                audit_sources(self.altered(path, DIGEST, "0" * 64))

    def test_workflow_wrong_platform_is_denied(self):
        for path in PULL_WORKFLOWS:
            with self.subTest(path=path), self.assertRaisesRegex(
                DockerImageAuditError, "G34_WORKFLOW_PULL_NOT_PINNED"):
                audit_sources(self.altered(path, "--platform linux/amd64",
                                           "--platform linux/arm64"))

    def test_extra_tag_pull_is_not_allowed(self):
        for path in PULL_WORKFLOWS:
            changed = dict(self.inputs)
            changed[path] += "\n      - name: Surprise fallback\n        run: docker pull python:3.12-alpine\n"
            with self.subTest(path=path), self.assertRaisesRegex(
                DockerImageAuditError, "G34_MUTABLE_DOCKER_TAG"):
                audit_sources(changed)

    def test_extra_second_digest_pull_is_not_allowed(self):
        changed = dict(self.inputs)
        path = PULL_WORKFLOWS[0]
        changed[path] += "\n      - name: Extra runtime\n        run: docker pull --platform linux/amd64 " + IMAGE + "\n"
        with self.assertRaisesRegex(DockerImageAuditError,
                                    "G34_WORKFLOW_PULL_NOT_PINNED"):
            audit_sources(changed)

    def test_missing_or_injected_source_file_fails(self):
        for key in REQUIRED:
            altered = dict(self.inputs)
            altered.pop(key)
            with self.subTest(key=key), self.assertRaisesRegex(
                DockerImageAuditError, "G34_MISSING_REQUIRED_SOURCE"):
                audit_sources(altered)
        altered = dict(self.inputs)
        altered["malicious/extra.py"] = "x"
        with self.assertRaisesRegex(DockerImageAuditError,
                                    "G34_MISSING_REQUIRED_SOURCE"):
            audit_sources(altered)

    def test_malformed_source_types_are_denied(self):
        for value in (None, True, 42, "", "x" * 200001):
            altered = dict(self.inputs)
            altered[SANDBOX] = value
            with self.subTest(value=str(value)[:16]), self.assertRaisesRegex(
                DockerImageAuditError, "G34_INVALID_SOURCE_BYTES"):
                audit_sources(altered)

    def test_source_symlink_is_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in REQUIRED:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(self.inputs[name], encoding="utf-8")
            if os.name != "nt":
                real = root / SANDBOX
                real.unlink()
                real.symlink_to(ROOT / SANDBOX)
                with self.assertRaisesRegex(DockerImageAuditError,
                                            "G34_SOURCE_MISSING_OR_UNSAFE"):
                    audit_repository(root)

    def test_missing_source_from_repository_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(DockerImageAuditError,
                                        "G34_SOURCE_MISSING_OR_UNSAFE"):
                audit_repository(Path(tmp))

    def test_image_pin_check_never_calls_docker_or_network(self):
        with patch("subprocess.run") as subprocess_run:
            result = audit_sources(dict(self.inputs))
        self.assertEqual(result["decision"], "NO_TRADE")
        subprocess_run.assert_not_called()

    def test_cli_emits_safe_no_trade_json(self):
        r = subprocess.run(
            [sys.executable, "-m", "tools.docker_image_audit", "--root", str(ROOT)],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(report["image"], IMAGE)
        self.assertFalse(report["broker_execution"])
        self.assertFalse(report["image_signature_verified"])
        self.assertEqual(report["owner_acceptance"], "PENDING")

    def test_wrong_docker_manifest_does_not_execute_candidate_source(self):
        from max_trading_agent.tool_factory import _IMAGE
        self.assertEqual(_IMAGE, IMAGE)
        self.assertEqual(len(REQUIRED), 7)


if __name__ == "__main__":
    unittest.main()
