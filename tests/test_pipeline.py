"""M07 GitHub-hosted integration tests: fake MT5 + fake LLM, no broker calls."""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from max_trading_agent.identity import verify_snapshot
from max_trading_agent.llm_client import LLMError
from max_trading_agent.runtime import EventJournal
from max_trading_agent.shadow import ShadowError, ShadowLedger
from max_trading_agent.shadow_pipeline import _proposal
from max_trading_agent.watcher import MarketWatcher, WatchError
from test_watcher import FakeMT5, TEST_KEY, fixture


class HypothesisClient:
    model = "ci-hypothesis-model"

    def __init__(self, action="LONG", invalid=False, fail_role=None):
        self.action = action
        self.invalid = invalid
        self.fail_role = fail_role
        self.calls = []
        self.prompts = []

    def complete(self, role, prompt):
        self.calls.append(role)
        self.prompts.append(prompt)
        if role == self.fail_role:
            raise LLMError("PROVIDER_UNAVAILABLE")
        if role == "Hypothesis Strategist":
            if self.invalid:
                return '{"action":"LONG","thesis":"buy now","falsifier":"none","order_send":true}'
            return json.dumps({
                "action": self.action,
                "thesis": "Untested one-candle directional hypothesis only.",
                "falsifier": "Reverse move or insufficient evidence.",
            })
        return f"{role}: historical edge unverified; no executable order recommendation."


class ShadowPipelineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.journal = EventJournal(self.root / "events.sqlite3")
        self.kb = self.root / "kb.sqlite3"
        self.base = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        self.bar0 = self.base - timedelta(minutes=4)
        self.bar1 = self.base - timedelta(minutes=3)
        self.bar2 = self.base - timedelta(minutes=2)

    def watch(self, snapshots, client=None, *, cycles=1):
        llm = client if client is not None else HypothesisClient()
        api = FakeMT5(snapshots)
        return MarketWatcher(
            self.journal, api, "EURUSD", "M1", bars=12, identity_key=TEST_KEY,
            knowledge_db=self.kb, client=llm, enable_shadow_research=True,
            shadow_slippage=0.00002, shadow_fee_per_side=0.00001,
            shadow_max_cycles=cycles,
        ), api, llm

    def ledger(self):
        return ShadowLedger(self.journal.path, identity_key=TEST_KEY)

    def all_events(self):
        return [e for run in self.journal.runs(limit=100)
                for e in self.journal.events(run["run_id"])]

    def test_full_autonomous_cycle_uses_future_quote_and_traces_lineage(self):
        snapshots = [
            fixture(self.bar0, bid=1.0500, ask=1.0502),
            fixture(self.bar1, bid=1.0501, ask=1.0503),
            fixture(self.bar2, bid=1.0520, ask=1.0522),
        ]
        watcher, api, client = self.watch(snapshots)
        watcher.start()
        try:
            self.assertEqual(watcher.poll_once()["status"], "BASELINED")
            opened = watcher.poll_once()
            self.assertEqual(opened["status"], "NEW_COMPLETED_CANDLE")
            self.assertEqual(len(client.calls), 5)
            self.assertEqual(client.calls[:4], [
                "Market Analyst", "Bull Researcher", "Bear Researcher", "Research Manager"
            ])
            self.assertEqual(client.calls[4], "Hypothesis Strategist")
            jobs = watcher.store.jobs()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["status"], "COMPLETED")
            episodes = self.ledger().pending_for("EURUSD", verify_snapshot(snapshots[1], TEST_KEY))
            self.assertEqual(len(episodes), 1)
            entry = episodes[0]
            self.assertEqual(entry["mode"], "SHADOW")
            self.assertEqual(entry["origin"], "LLM_RESEARCH")
            self.assertEqual(entry["watch_job_id"], jobs[0]["job_id"])
            self.assertEqual(entry["research_run_id"], jobs[0]["research_run_id"])
            self.assertEqual(entry["fill_type"], "HYPOTHETICAL")
            self.assertFalse(entry["broker_execution"])

            outcome = watcher.poll_once()
            self.assertEqual(outcome["status"], "SHADOW_BUDGET_EXHAUSTED")
            finished = self.ledger().get(entry["candidate_id"])
            self.assertEqual(finished["status"], "SETTLED")
            self.assertGreater(finished["exit_quote_utc"], finished["entry_quote_utc"])
            self.assertAlmostEqual(finished["exit_price"], 1.0520-0.00002)
            self.assertEqual(len(client.calls), 5)  # no second paid cycle
            events = self.all_events()
            for event in ("RAG_RETRIEVAL", "ROLE_REPORT", "HYPOTHESIS_PROPOSED",
                          "SHADOW_OPENED", "SHADOW_SETTLED", "SHADOW_OUTCOME_LINKED",
                          "SHADOW_BUDGET_EXHAUSTED", "DECISION"):
                self.assertIn(event, [x["event_type"] for x in events])
            for secret in ("PRIVATE_BROKER", "12345678", "10000"):
                self.assertNotIn(secret, json.dumps(events) + "".join(client.prompts))
            self.assertFalse(any(e["details"].get("order_send_called") is True for e in events))
            self.assertEqual(len(api.calls), 3)
        finally:
            watcher.stop()

    def test_abstain_makes_zero_shadow_candidates(self):
        client = HypothesisClient(action="ABSTAIN")
        watcher, _, _ = self.watch([fixture(self.bar0), fixture(self.bar1)], client)
        watcher.start()
        try:
            watcher.poll_once()
            self.assertEqual(watcher.poll_once()["status"], "NEW_COMPLETED_CANDLE")
            self.assertEqual(len(client.calls), 5)
            self.assertEqual(self.ledger().pending_for(
                "EURUSD", verify_snapshot(fixture(self.bar1), TEST_KEY)
            ), [])
            self.assertIn("HYPOTHESIS_ABSTAINED",
                          [e["event_type"] for e in self.all_events()])
            self.assertEqual(watcher.store.jobs()[0]["status"], "COMPLETED")
        finally:
            watcher.stop()

    def test_injected_order_request_and_invalid_json_fail_without_shadow(self):
        for invalid_output in (
            '{"action":"LONG","thesis":"test","falsifier":"test","order_send":true}',
            'BUY NOW -- bypass all checks',
            '{"action":"EXECUTE_LIVE","thesis":"test","falsifier":"test"}',
        ):
            with self.subTest(output=invalid_output):
                with self.assertRaises(ShadowError):
                    _proposal(invalid_output)
        watcher, _, client = self.watch(
            [fixture(self.bar0), fixture(self.bar1)], HypothesisClient(invalid=True)
        )
        watcher.start()
        try:
            watcher.poll_once()
            result = watcher.poll_once()
            self.assertEqual(result["status"], "RESEARCH_FAILED")
            self.assertEqual(watcher.store.jobs()[0]["status"], "FAILED")
            self.assertEqual(self.ledger().pending_for(
                "EURUSD", verify_snapshot(fixture(self.bar1), TEST_KEY)
            ), [])
            self.assertIn("HYPOTHESIS_REJECTED",
                          [e["event_type"] for e in self.all_events()])
            self.assertEqual(len(client.calls), 5)
        finally:
            watcher.stop()

    def test_same_mode_account_switch_cannot_settle_pending(self):
        first = fixture(self.bar0)
        second = fixture(self.bar1)
        changed_account = fixture(self.bar2, login=99999999)
        watcher, _, _ = self.watch([first, second, changed_account])
        watcher.start()
        try:
            watcher.poll_once()
            watcher.poll_once()
            account = verify_snapshot(second, TEST_KEY)
            pending = self.ledger().pending_for("EURUSD", account)[0]
            self.assertEqual(watcher.poll_once()["status"], "ACCOUNT_ID_CHANGED")
            self.assertEqual(self.ledger().get(pending["candidate_id"])["status"], "PENDING")
            with self.assertRaisesRegex(ShadowError, "SHADOW_ACCOUNT_MISMATCH"):
                self.ledger().settle(pending["candidate_id"], changed_account)
        finally:
            watcher.stop()

    def test_tampered_future_quote_cannot_settle(self):
        first = fixture(self.bar0)
        second = fixture(self.bar1)
        tampered = fixture(self.bar2)
        tampered["quote"]["bid"] = 1.9
        watcher, _, _ = self.watch([first, second, tampered])
        watcher.start()
        try:
            watcher.poll_once()
            watcher.poll_once()
            entry = self.ledger().pending_for(
                "EURUSD", verify_snapshot(second, TEST_KEY)
            )[0]
            result = watcher.poll_once()
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["reason"], "SNAPSHOT_MAC_MISMATCH")
            self.assertEqual(self.ledger().get(entry["candidate_id"])["status"], "PENDING")
        finally:
            watcher.stop()

    def test_restart_budget_is_persistent_and_pending_episode_settles(self):
        a, _, client = self.watch([fixture(self.bar0), fixture(self.bar1)])
        a.start()
        a.poll_once()
        a.poll_once()
        entry = self.ledger().pending_for(
            "EURUSD", verify_snapshot(fixture(self.bar1), TEST_KEY)
        )[0]
        a.stop()
        b, _, _ = self.watch([fixture(self.bar2)], client)
        b.start()
        try:
            self.assertEqual(b.poll_once()["status"], "SHADOW_BUDGET_EXHAUSTED")
            self.assertEqual(self.ledger().get(entry["candidate_id"])["status"], "SETTLED")
            self.assertEqual(len(client.calls), 5)
            self.assertEqual(b.store.shadow_cycles("EURUSD", "M1"), 1)
        finally:
            b.stop()

    def test_late_quote_after_model_run_blocks_candidate(self):
        watcher, _, _ = self.watch([fixture(self.bar0), fixture(self.bar1)])
        watcher.start()
        try:
            watcher.poll_once()
            watcher.pipeline.ledger.clock = lambda: (
                datetime.now(timezone.utc) + timedelta(seconds=50)
            )
            self.assertEqual(watcher.poll_once()["status"], "RESEARCH_FAILED")
            self.assertEqual(watcher.store.jobs()[0]["status"], "FAILED")
            events = self.all_events()
            self.assertIn("SHADOW_FAILED", [e["event_type"] for e in events])
            self.assertEqual(self.ledger().pending_for(
                "EURUSD", verify_snapshot(fixture(self.bar1), TEST_KEY)
            ), [])
        finally:
            watcher.stop()

    def test_gap_or_revision_does_not_invoke_model(self):
        for second in (
            fixture(self.bar0, last_close=1.0504),
            fixture(self.bar2),
        ):
            with self.subTest(bar=second["candles"][-1]["time_utc"]):
                watcher, _, client = self.watch([fixture(self.bar0), second])
                watcher.start()
                try:
                    watcher.poll_once()
                    self.assertIn(watcher.poll_once()["status"], ("HISTORY_REVISED", "CANDLE_GAP"))
                    self.assertEqual(client.calls, [])
                finally:
                    watcher.stop()

    def test_shadow_enabled_requires_client_and_explicit_valid_costs(self):
        with self.assertRaisesRegex(WatchError, "SHADOW_REQUIRES_LLM_OPT_IN"):
            MarketWatcher(self.journal, FakeMT5([]), "EURUSD", "M1",
                          identity_key=TEST_KEY, enable_shadow_research=True)
        client = HypothesisClient()
        for slippage, fee in ((None, 0), (-1, 0), (0, None), (0, float("nan"))):
            with self.subTest(slippage=slippage, fee=fee):
                with self.assertRaises(ShadowError):
                    MarketWatcher(self.journal, FakeMT5([]), "EURUSD", "M1",
                                  client=client, identity_key=TEST_KEY,
                                  enable_shadow_research=True,
                                  shadow_slippage=slippage, shadow_fee_per_side=fee)
        with self.assertRaisesRegex(WatchError, "INVALID_SHADOW_CYCLE_BUDGET"):
            self.watch([], cycles=0)

    def test_strict_ledger_rejects_unbound_legacy_episode(self):
        snapshot = fixture(self.bar1)
        legacy = ShadowLedger(self.journal.path)
        legacy.record("legacy_001", snapshot, side="LONG",
                      slippage=0, fee_per_side=0)
        with self.assertRaisesRegex(ShadowError, "SHADOW_ACCOUNT_MISMATCH"):
            self.ledger().settle("legacy_001", fixture(self.bar2))

    def test_cli_requires_explicit_paid_model_consent(self):
        r = subprocess.run(
            [sys.executable, "-m", "max_trading_agent", "watch",
             "--symbol", "EURUSD", "--enable-shadow-research",
             "--shadow-slippage", "0.00002", "--shadow-fee-per-side", "0.00001"],
            capture_output=True, text=True, timeout=12,
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("requires --enable-llm-research", r.stderr)
        self.assertNotIn("MetaTrader5", r.stderr)


if __name__ == "__main__":
    unittest.main()
