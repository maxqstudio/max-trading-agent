"""Synthetic DEMO intent recovery: must never call a real broker."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from max_trading_agent.paper_demo import (
    InMemoryPaperBroker, PaperError, PaperLedger, PaperMission,
)
from max_trading_agent.identity import seal_snapshot
from max_trading_agent.runtime import EventJournal
from test_demo_preflight import KEY, LIMITS, NOW, PROPOSAL, snapshot


class PaperDemoTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.journal = EventJournal(self.root/"events.sqlite3")
        self.db = self.root/"intents.sqlite3"
        self.ledger = PaperLedger(self.db)
        self.broker = InMemoryPaperBroker()
        self.mission = PaperMission(self.journal, self.ledger, self.broker)

    def execute(self, *, snap=None, proposal=None, limits=None):
        return self.mission.execute(
            snap if snap is not None else snapshot(),
            proposal if proposal is not None else copy.deepcopy(PROPOSAL),
            limits if limits is not None else copy.deepcopy(LIMITS),
            KEY, now=NOW,
        )

    def test_one_synthetic_fill_with_journal_and_no_trade(self):
        out = self.execute()
        self.assertEqual(out["status"], "RECONCILED_SIMULATED")
        self.assertFalse(out["broker_execution"])
        self.assertEqual(out["decision"], "NO_TRADE")
        self.assertEqual(out["owner_acceptance"], "PENDING")
        self.assertEqual(len(self.broker.calls), 1)
        rows = self.journal.events(out["run_id"])
        self.assertEqual([r["event_type"] for r in rows],
                         ["PAPER_PREFLIGHT", "PAPER_INTENT",
                          "PAPER_RECONCILIATION", "DECISION"])
        self.assertEqual(rows[-1]["details"]["action"], "NO_TRADE")
        self.assertFalse(rows[-1]["details"]["order_send_called"])
        self.assertEqual(self.journal.runs()[0]["mode"], "PAPER")
        content = json.dumps(rows) + json.dumps(out)
        for secret in ("SECRET_BROKER_SERVER", "987654321", "snapshot_mac", "9800"):
            self.assertNotIn(secret, content)

    def test_duplicate_request_never_resends_even_after_restart(self):
        a = self.execute()
        self.assertFalse(a["reused_intent"])
        self.ledger = PaperLedger(self.db)
        self.mission = PaperMission(self.journal, self.ledger, self.broker)
        b = self.execute()
        self.assertTrue(b["reused_intent"])
        self.assertEqual(b["status"], "RECONCILED_SIMULATED")
        self.assertEqual(len(self.broker.calls), 1)

    def test_timeout_after_fake_fill_reconciles_without_retry(self):
        self.broker = InMemoryPaperBroker("TIMEOUT_AFTER_FILL")
        self.mission = PaperMission(self.journal, self.ledger, self.broker)
        out = self.execute()
        self.assertEqual(out["status"], "RECONCILED_SIMULATED")
        self.assertEqual(self.ledger.state(PROPOSAL["request_id"]), "RECONCILED_SIMULATED")
        self.assertEqual(len(self.broker.calls), 1)

    def test_timeout_before_receipt_is_uncertain_and_never_blind_retry(self):
        self.broker = InMemoryPaperBroker("TIMEOUT_BEFORE_RECEIPT")
        self.mission = PaperMission(self.journal, self.ledger, self.broker)
        out = self.execute()
        self.assertEqual(out["status"], "UNCERTAIN")
        again = self.execute()
        self.assertEqual(again["status"], "UNCERTAIN")
        self.assertEqual(len(self.broker.calls), 1)
        # If an independent hypothetical acknowledgement appears later,
        # reconcile only; never invoke another synthetic send.
        self.broker.receipts[PROPOSAL["request_id"]] = {
            "state": "FILLED_SIMULATED", "symbol": "EURUSD", "side": "LONG",
            "price": 1.0502,
        }
        self.ledger = PaperLedger(self.db)
        state = self.ledger.reconcile(PROPOSAL["request_id"], self.broker,
                                      snapshot(), KEY)
        self.assertEqual(state, "RECONCILED_SIMULATED")
        self.assertEqual(len(self.broker.calls), 1)

    def test_fake_reject_is_final(self):
        self.broker = InMemoryPaperBroker("REJECT")
        self.mission = PaperMission(self.journal, self.ledger, self.broker)
        out = self.execute()
        self.assertEqual(out["status"], "REJECTED_SIMULATED")
        self.assertEqual(self.execute()["status"], "REJECTED_SIMULATED")
        self.assertEqual(len(self.broker.calls), 1)

    def test_same_request_different_payload_or_policy_vetoes(self):
        self.execute()
        changed = copy.deepcopy(PROPOSAL)
        changed["take_profit"] = 1.054
        with self.assertRaisesRegex(PaperError, "IDEMPOTENCY_CONFLICT"):
            self.execute(proposal=changed)
        self.assertEqual(len(self.broker.calls), 1)
        changed_policy = copy.deepcopy(LIMITS)
        changed_policy["max_positions"] = 3
        with self.assertRaisesRegex(PaperError, "IDEMPOTENCY_CONFLICT"):
            self.execute(limits=changed_policy)
        self.assertEqual(len(self.broker.calls), 1)

    def test_account_binding_switch_is_blocked_on_reconcile(self):
        self.execute()
        changed = snapshot()
        base = {k:v for k,v in changed.items() if k != "provenance"}
        switched = seal_snapshot(base, KEY, login=11111111, server="OTHER_BROKER")
        with self.assertRaisesRegex(PaperError, "ACCOUNT_CHANGED"):
            self.ledger.reconcile(PROPOSAL["request_id"], self.broker,
                                  switched, KEY)
        self.assertEqual(len(self.broker.calls), 1)

    def test_kill_switch_and_live_account_never_simulate(self):
        policy = copy.deepcopy(LIMITS)
        policy["kill_switch"] = True
        out = self.execute(limits=policy)
        self.assertEqual(out["status"], "VETOED")
        self.assertIn("KILL_SWITCH", out["vetoes"])
        self.assertEqual(self.broker.calls, [])
        real = snapshot(mode="REAL")
        out2 = self.execute(snap=real)
        self.assertIn("NON_DEMO_ACCOUNT", out2["vetoes"])
        self.assertEqual(self.broker.calls, [])

    def test_real_broker_implementation_cannot_be_injected(self):
        class FakeAttack(InMemoryPaperBroker):
            pass
        with self.assertRaisesRegex(PaperError, "REAL_BROKER_INTERFACE_FORBIDDEN"):
            PaperMission(self.journal, self.ledger, FakeAttack())

    def test_signature_tamper_prevents_any_action(self):
        bad = snapshot()
        bad["account"]["equity"] = 99999
        with self.assertRaisesRegex(PaperError, "PAPER_POLICY_INVALID"):
            self.execute(snap=bad)
        self.assertEqual(self.broker.calls, [])

    def test_recovery_from_intent_with_no_fake_receipt_stays_uncertain(self):
        snap = snapshot()
        self.ledger.prepare(PROPOSAL, LIMITS, snap, KEY)
        self.assertEqual(self.ledger.state(PROPOSAL["request_id"]), "PREPARED")
        self.ledger = PaperLedger(self.db)
        self.assertEqual(self.ledger.reconcile(PROPOSAL["request_id"],
                                                self.broker, snap, KEY), "UNCERTAIN")
        self.assertEqual(self.broker.calls, [])


if __name__ == "__main__":
    unittest.main()
