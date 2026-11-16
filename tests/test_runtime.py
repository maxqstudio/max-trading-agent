"""Persistent event-backed agent workflow; never accesses MT5 or secrets."""
import tempfile
import unittest
from pathlib import Path
from max_trading_agent.runtime import EventJournal, ReferenceMission


class EventJournalTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.path = self.root / "events.sqlite3"

    def test_persists_monotonic_events_and_replay(self):
        journal = EventJournal(self.path)
        run_id = journal.start("analyze_symbol")
        ReferenceMission(journal, self.root / "kb.sqlite3").execute(run_id, "analyze_symbol")
        original = journal.events(run_id)
        self.assertGreaterEqual(len(original), 8)
        self.assertEqual([event["event_id"] for event in original],
                         sorted(set(event["event_id"] for event in original)))
        self.assertEqual(EventJournal(self.path).events(run_id), original)
        self.assertEqual(journal.events(run_id, original[3]["event_id"]), original[4:])
        self.assertEqual(journal.runs()[0]["status"], "COMPLETED")
        self.assertEqual(journal.runs()[0]["mode"], "REFERENCE_ONLY")

    def test_factual_tools_and_no_trade(self):
        journal = EventJournal(self.path)
        run_id = journal.start("prepare_entry")
        ReferenceMission(journal, self.root / "kb.sqlite3").execute(run_id, "prepare_entry")
        events = journal.events(run_id)
        actual = next(e for e in events if e["event_type"] == "TOOL_RESULT")
        self.assertTrue(actual["details"]["references"])
        self.assertIn("search", actual["details"]["tools_executed"])
        planned = next(e for e in events if e["event_type"] == "TOOL_PLAN")
        self.assertIn("order_check", planned["details"]["required_tools"])
        self.assertNotIn("order_send", planned["details"]["required_tools"])
        decision = next(e for e in events if e["event_type"] == "DECISION")
        self.assertEqual(decision["details"]["action"], "NO_TRADE")
        self.assertFalse(decision["details"]["order_send_called"])
        self.assertTrue(any(e["event_type"] == "DEBATE_SKIPPED" for e in events))

    def test_bad_task_and_invalid_mutation_rejected(self):
        journal = EventJournal(self.path)
        with self.assertRaises(ValueError):
            journal.start("send_order")
        self.assertEqual(journal.runs(), [])
        run_id = journal.start("inspect_positions")
        with self.assertRaises(ValueError):
            journal.append(run_id, "X", "Y", "TRADE", "bad")
        with self.assertRaises(ValueError):
            journal.finish(run_id, "RUNNING")
        journal.finish(run_id, "COMPLETED")
        with self.assertRaises(ValueError):
            journal.finish(run_id, "COMPLETED")

    def test_negative_cursor_rejected(self):
        journal = EventJournal(self.path)
        run_id = journal.start("analyze_symbol")
        with self.assertRaises(ValueError):
            journal.events(run_id, -1)
        with self.assertRaises(ValueError):
            journal.events(run_id, "not_int")


if __name__ == "__main__":
    unittest.main()
