"""Tool proposal red-team, with NO candidate execution in the test host."""
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from max_trading_agent.tool_factory import (
    FactoryError, check_source, independent_cases, propose_from_llm,
    sandbox_evaluate, save_proposal, validate_candidate,
)
ORACLES = Path(__file__).resolve().parent.parent / "knowledge" / "tool_factory_oracles.json"
CANDIDATE = {
    "tool_id": "rolling_mean_v1", "version": "0.1.0", "effect": "PURE",
    "purpose": "Calculate the arithmetic mean of bounded numeric observations.",
    "code": "def analyze(values):\n    return sum(values) / len(values) if values else 0.0\n",
    "examples": [{"input": [2, 4, 6], "expected": 4}],
}


class FakeClient:
    def __init__(self, data=None):
        self.data = data if data is not None else json.dumps(CANDIDATE)
        self.calls = []

    def complete(self, role, prompt):
        self.calls.append((role, prompt))
        return self.data


class FactoryTests(unittest.TestCase):
    def test_model_produces_research_only_no_trade_spec(self):
        client = FakeClient()
        candidate, result = propose_from_llm(
            "Create a bounded research-only rolling mean diagnostic", client)
        self.assertEqual(candidate, CANDIDATE)
        self.assertEqual(result["tier"], "T0_PROPOSED")
        self.assertEqual(result["decision"], "NO_TRADE")
        self.assertFalse(result["broker_execution"])
        self.assertEqual(len(result["sha256"]), 64)
        self.assertEqual(len(client.calls), 1)

    def test_privileged_effects_and_path_traversal_blocked(self):
        for effect in ("TRADE_MUTATION", "POLICY_MUTATION", "READ_ONLY",
                       "RESEARCH_WRITE", "LIVE"):
            c = copy.deepcopy(CANDIDATE)
            c["effect"] = effect
            with self.assertRaises(FactoryError):
                validate_candidate(c)
        for name in ("../../secrets", "tool.py", "A_CAPS", "a"):
            c = copy.deepcopy(CANDIDATE)
            c["tool_id"] = name
            with self.assertRaises(FactoryError):
                validate_candidate(c)

    def test_dangerous_ast_never_reaches_execution(self):
        hostile = [
            "import os\ndef analyze(values):\n    return 1\n",
            "from pathlib import Path\ndef analyze(values):\n    return 1\n",
            "def analyze(values):\n    return open('/etc/passwd')\n",
            "def analyze(values):\n    return values.__class__\n",
            "def analyze(values):\n    return eval('1+1')\n",
            "def analyze(values):\n    return __import__('os')\n",
            "def analyze(values):\n    return globals()\n",
            "def analyze(values):\n    exec('print(1)')\n",
        ]
        for code in hostile:
            with self.subTest(code=code[:40]):
                with self.assertRaises(FactoryError):
                    check_source(code)

    def test_agent_examples_never_replace_external_oracle(self):
        good = independent_cases(CANDIDATE["tool_id"], ORACLES)
        self.assertGreaterEqual(len(good), 3)
        bad = copy.deepcopy(CANDIDATE)
        bad["examples"][0]["expected"] = 999
        self.assertEqual(validate_candidate(bad)["tier"], "T0_PROPOSED")
        self.assertEqual(independent_cases(bad["tool_id"], ORACLES), good)

    def test_unknown_novel_tool_has_no_self_certification(self):
        item = copy.deepcopy(CANDIDATE)
        item["tool_id"] = "novel_regime_indicator_v1"
        self.assertIsNone(independent_cases(item["tool_id"], ORACLES))
        r = sandbox_evaluate(item, None)
        self.assertEqual(r["status"], "PENDING_INDEPENDENT_ORACLE")
        self.assertEqual(r["decision"], "NO_TRADE")

    def test_docker_arguments_cannot_gain_network_or_root(self):
        cases = independent_cases(CANDIDATE["tool_id"], ORACLES)
        with patch("max_trading_agent.tool_factory.subprocess.run",
                   return_value=SimpleNamespace(returncode=0)) as runner:
            result = sandbox_evaluate(CANDIDATE, cases)
        self.assertEqual(result["status"], "SANDBOX_PASSED_REVIEW_REQUIRED")
        self.assertEqual(result["decision"], "NO_TRADE")
        cmd = runner.call_args.args[0]
        self.assertEqual(cmd[:2], ["docker", "run"])
        self.assertEqual(cmd[cmd.index("--network")+1], "none")
        self.assertIn("--read-only", cmd)
        self.assertEqual(cmd[cmd.index("--user")+1], "65534:65534")
        self.assertEqual(cmd[cmd.index("--cap-drop")+1], "ALL")
        self.assertIn("no-new-privileges", cmd)

    def test_failed_or_timed_out_container_is_never_accepted(self):
        cases = independent_cases(CANDIDATE["tool_id"], ORACLES)
        with patch("max_trading_agent.tool_factory.subprocess.run",
                   return_value=SimpleNamespace(returncode=1)):
            self.assertEqual(sandbox_evaluate(CANDIDATE, cases)["status"],
                             "SANDBOX_FAILED")
        with patch("max_trading_agent.tool_factory.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(["docker"], 2)):
            self.assertEqual(sandbox_evaluate(CANDIDATE, cases)["status"],
                             "SANDBOX_TIMEOUT")

    def test_private_proposal_is_write_once(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "proposals"
            first = save_proposal(CANDIDATE, path)
            self.assertEqual(first, save_proposal(CANDIDATE, path))
            changed = copy.deepcopy(CANDIDATE)
            changed["code"] = "def analyze(values):\n    return 999\n"
            with self.assertRaisesRegex(FactoryError, "TOOL_ID_CONFLICT"):
                save_proposal(changed, path)
            self.assertEqual(json.loads((path/"rolling_mean_v1.json").read_text()),
                             CANDIDATE)

    def test_invalid_llm_response_does_not_authorize(self):
        for result in ("not json", '{"effect":"TRADE_MUTATION"}'):
            with self.assertRaises(FactoryError):
                propose_from_llm("Build an independent pure numerical research tool",
                                 FakeClient(result))


if __name__ == "__main__":
    unittest.main()
