"""G31 owner offline MT5 prerequisite probe: no network, import or key writes."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.owner_mt5_preflight import (
    _REQUIRED, _git, _mt5_package_present, inspect_owner_environment,
)

SHA = "a" * 40


def local_git(repo, *args):
    if args == ("symbolic-ref", "--quiet", "--short", "HEAD"):
        return "main"
    if args == ("rev-parse", "--verify", "HEAD"):
        return SHA
    if args == ("rev-parse", "--verify", "refs/remotes/origin/main"):
        return SHA
    if args == ("status", "--porcelain", "--untracked-files=normal"):
        return ""
    raise AssertionError("UNEXPECTED_GIT_OPERATION")


class OwnerOfflinePreflightTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        for name in _REQUIRED:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test marker", encoding="utf-8")
        self.key = self.repo / ".local" / "mt5_identity.key"

    def check(self, git_fn=local_git, **changes):
        with patch("tools.owner_mt5_preflight._git", side_effect=git_fn):
            return inspect_owner_environment(
                self.repo, os_name=changes.pop("os_name", "Windows"),
                package_check=changes.pop("package_check", lambda: True),
                **changes,
            )

    def test_all_prerequisites_present_still_not_live_acceptance(self):
        report = self.check()
        self.assertEqual(report["status"],
                         "LOCAL_PREREQUISITES_PRESENT_LIVE_TEST_PENDING")
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["git_head_sha"], SHA)
        self.assertEqual(report["blockers"], [])
        self.assertTrue(report["origin_main_is_cached_local_ref"])
        self.assertFalse(report["actual_remote_main_verified"])
        self.assertFalse(report["mt5_python_module_imported"])
        self.assertFalse(report["mt5_terminal_initialized"])
        self.assertFalse(report["terminal_feed_tested"])
        self.assertFalse(report["broker_connected"])
        self.assertFalse(report["identity_key_created"])
        self.assertFalse(report["github_postmerge_ci_verified"])
        self.assertFalse(report["eligible_for_demo_or_live"])
        self.assertFalse(report["broker_execution"])
        self.assertEqual(report["decision"], "NO_TRADE")
        self.assertEqual(report["execution"], "DISABLED")
        self.assertEqual(report["owner_acceptance"], "PENDING")
        self.assertEqual(report["next_action"],
                         "OWNER_MAY_RUN_EXPLICIT_M09_READONLY_ACCEPTANCE")
        self.assertFalse(self.key.exists())

    def test_linux_host_always_rejected_even_with_package(self):
        report = self.check(os_name="Linux")
        self.assertEqual(report["status"], "LOCAL_PREFLIGHT_NOT_READY")
        self.assertIn("WINDOWS_HOST", report["blockers"])
        self.assertFalse(report["terminal_feed_tested"])

    def test_absent_mt5_distribution_never_claims_prepared(self):
        report = self.check(package_check=lambda: False)
        self.assertFalse(report["checks"]["metatrader5_distribution_installed"])
        self.assertIn("METATRADER5_DISTRIBUTION_INSTALLED", report["blockers"])
        self.assertEqual(report["next_action"], "FIX_LOCAL_BLOCKERS_BEFORE_M09")

    def test_git_wrong_branch_is_blocked(self):
        def wrong(repo, *args):
            return "feat/g31-postmerge-main-preflight" if args[0] == "symbolic-ref" else local_git(repo, *args)
        report = self.check(git_fn=wrong)
        self.assertIn("BRANCH_IS_MAIN", report["blockers"])

    def test_locally_stale_origin_main_is_blocked(self):
        def stale(repo, *args):
            return "b" * 40 if args[-1] == "refs/remotes/origin/main" else local_git(repo, *args)
        report = self.check(git_fn=stale)
        self.assertIn("LOCAL_ORIGIN_MAIN_MATCHES_HEAD", report["blockers"])
        self.assertFalse(report["actual_remote_main_verified"])

    def test_missing_cached_origin_main_is_blocked(self):
        def missing(repo, *args):
            return None if args[-1] == "refs/remotes/origin/main" else local_git(repo, *args)
        report = self.check(git_fn=missing)
        self.assertIn("LOCAL_ORIGIN_MAIN_MATCHES_HEAD", report["blockers"])

    def test_dirty_worktree_never_claims_clean(self):
        def dirty(repo, *args):
            return " M max_trading_agent/identity.py" if args[0] == "status" else local_git(repo, *args)
        report = self.check(git_fn=dirty)
        self.assertIn("WORKING_TREE_CLEAN", report["blockers"])
        self.assertNotIn("identity.py", json.dumps(report))

    def test_untracked_file_marks_tree_dirty(self):
        def untracked(repo, *args):
            return "?? attacker.py" if args[0] == "status" else local_git(repo, *args)
        report = self.check(git_fn=untracked)
        self.assertFalse(report["checks"]["working_tree_clean"])
        self.assertNotIn("attacker.py", json.dumps(report))

    def test_missing_actual_g31_workflow_or_m09_file_blocks(self):
        for file in (_REQUIRED[0], _REQUIRED[-1]):
            path = self.repo / file
            path.unlink()
            with self.subTest(file=file):
                report = self.check()
                self.assertIn("MERGED_ACCEPTANCE_FILES_PRESENT", report["blockers"])
            path.write_text("test marker", encoding="utf-8")

    @unittest.skipIf(os.name == "nt", "Windows symlink privileges vary")
    def test_symlink_repo_or_marker_fails_closed(self):
        link = self.root / "repo-link"
        link.symlink_to(self.repo, target_is_directory=True)
        with patch("tools.owner_mt5_preflight._git") as git:
            result = inspect_owner_environment(
                link, os_name="Windows", package_check=lambda: True)
            git.assert_not_called()
        self.assertFalse(result["checks"]["valid_non_symlink_repository"])
        self.assertEqual(result["status"], "LOCAL_PREFLIGHT_NOT_READY")
        marker = self.repo / _REQUIRED[0]
        marker.unlink()
        marker.symlink_to(self.repo / _REQUIRED[1])
        self.assertIn("MERGED_ACCEPTANCE_FILES_PRESENT", self.check()["blockers"])

    def test_invalid_or_unknown_git_head_does_not_disclose_raw_error(self):
        def bad(repo, *args):
            return "bad\nSECRET_ACCOUNT_PASSWORD" if args == ("rev-parse", "--verify", "HEAD") else local_git(repo, *args)
        report = self.check(git_fn=bad)
        self.assertIsNone(report["git_head_sha"])
        self.assertIn("VALID_NON_SYMLINK_REPOSITORY", report["blockers"])
        self.assertNotIn("SECRET_ACCOUNT_PASSWORD", json.dumps(report))

    def test_package_check_exception_is_not_disclosed(self):
        def malicious():
            raise RuntimeError("SECRET_BROKER_LOGIN")
        report = self.check(package_check=malicious)
        self.assertFalse(report["checks"]["metatrader5_distribution_installed"])
        self.assertNotIn("SECRET_BROKER_LOGIN", json.dumps(report))

    def test_git_helper_only_uses_local_read_subprocess_no_shell(self):
        fake = type("Done", (), {"returncode":0, "stdout":"main\n"})()
        with patch("tools.owner_mt5_preflight.subprocess.run", return_value=fake) as run:
            self.assertEqual(_git(self.repo, "symbolic-ref", "--quiet",
                                  "--short", "HEAD"), "main")
        args = run.call_args.args[0]
        self.assertEqual(args[:3], ["git", "-C", str(self.repo)])
        self.assertNotIn("fetch", args)
        self.assertNotIn("push", args)
        self.assertNotIn("login", args)
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_mt5_dist_metadata_does_not_import_terminal_module(self):
        with patch("tools.owner_mt5_preflight.importlib.metadata.version",
                   return_value="5.0.0") as version, patch(
                "importlib.import_module") as imported:
            self.assertTrue(_mt5_package_present())
        version.assert_called_once_with("MetaTrader5")
        imported.assert_not_called()

    def test_empty_or_missing_package_metadata_fails_closed(self):
        import importlib.metadata
        with patch("tools.owner_mt5_preflight.importlib.metadata.version",
                   side_effect=importlib.metadata.PackageNotFoundError):
            self.assertFalse(_mt5_package_present())

    def test_cli_on_hosted_linux_never_creates_local_identity_key(self):
        result = subprocess.run([
            sys.executable, "-m", "tools.owner_mt5_preflight",
            "--repo", str(self.repo),
        ], capture_output=True, text=True, timeout=12)
        self.assertIn(result.returncode, (3, 0))
        data = json.loads(result.stdout)
        if sys.platform != "win32":
            self.assertEqual(result.returncode, 3)
            self.assertFalse(data["checks"]["windows_host"])
        self.assertEqual(data["decision"], "NO_TRADE")
        self.assertFalse(data["terminal_feed_tested"])
        self.assertFalse(data["identity_key_created"])
        self.assertFalse(self.key.exists())

    def test_g31_postmerge_rechecks_real_docker_timeout_cleanup(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" /
                    "workflows" / "g31-postmerge-main.yml").read_text(
                        encoding="utf-8")
        self.assertIn("G31 postmerge Docker timeout cleanup", workflow)
        self.assertIn("sandbox_evaluate(candidate,cases,timeout_seconds=3)", workflow)
        self.assertIn('"status"]=="SANDBOX_TIMEOUT"', workflow)
        self.assertIn('["docker","ps","-a","--filter","name=maxq-t0-"', workflow)
        self.assertIn("TIMEOUT_CONTAINER_NOT_REMOVED", workflow)
        self.assertIn("if: runner.os == 'Linux'", workflow)

    def test_g31_hosted_ci_never_claims_owner_local_preflight(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" /
                    "workflows" / "g31-postmerge-main.yml").read_text(encoding="utf-8")
        self.assertIn('"local_owner_preflight_performed":False', workflow)
        self.assertIn('assert report["local_owner_preflight_performed"] is False', workflow)
        self.assertNotIn('report["local_owner_preflight_performed"]=True', workflow)
        self.assertIn('"real_mt5_terminal_tested":False', workflow)
        self.assertIn('"decision":"NO_TRADE"', workflow)

    def test_no_working_tree_or_broker_credentials_serialized(self):
        data = self.check()
        text = json.dumps(data)
        self.assertNotIn(str(self.repo), text)
        for field in ("account_number", "server_name", "api_key",
                      "secret", "broker_login", "mt5_identity.key"):
            self.assertNotIn(field, text)


if __name__ == "__main__":
    unittest.main()
