"""Agent Desk: check-ins and owner replies are journal-only and cannot trade."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from max_trading_agent.desk import AGENTS, Desk
from max_trading_agent.runtime import EventJournal
from max_trading_agent.web import create_server


class DeskUnitTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.desk = Desk(EventJournal(Path(folder.name) / "e.sqlite3"))

    def test_seeded_questions_are_idempotent_and_cover_all_agents(self):
        self.desk.seed_questions()
        qs = [m for m in self.desk.messages() if m["kind"] == "QUESTION"]
        self.assertEqual({q["author"] for q in qs}, set(AGENTS))
        self.assertEqual(len(qs), len(AGENTS))

    def test_reply_marks_answered_and_agent_acks_without_changing_decision(self):
        q = next(m for m in self.desk.messages() if m["author"] == "executor")
        before = self.desk.open_questions()
        self.desk.reply("Ya, kirim", q["msg_id"])
        self.assertEqual(self.desk.open_questions(), before - 1)
        ack = [m for m in self.desk.messages() if m["kind"] == "ACK"][-1]
        self.assertIn("DISABLED", ack["body"])
        self.assertIn("NO_TRADE", ack["body"])

    def test_rejects_invalid_replies(self):
        for args in [("", None, "scanner"), ("x" * 501, None, "scanner"),
                     ("hi", 9999, None), ("hi", None, "hacker"), ("hi", None, None)]:
            with self.assertRaises(ValueError):
                self.desk.reply(*args)


class DeskHttpTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.server = create_server(port=0, journal_path=root / "e.sqlite3",
                                    knowledge_path=root / "kb.sqlite3", heartbeat_seconds=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def call(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        out = (r.status, r.read())
        c.close()
        return out

    def post_reply(self, payload):
        return self.call("POST", "/api/desk/reply", json.dumps(payload),
                         {"Content-Type": "application/json"})

    def test_desk_endpoint_reports_safety_and_questions(self):
        status, raw = self.call("GET", "/api/desk")
        data = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(data["safety"]["decision"], "NO_TRADE")
        self.assertEqual(data["safety"]["execution"], "DISABLED")
        self.assertFalse(data["safety"]["broker_execution"])
        self.assertGreaterEqual(data["open_questions"], len(AGENTS))

    def test_reply_flow_and_validation(self):
        _, raw = self.call("GET", "/api/desk")
        qid = next(m["msg_id"] for m in json.loads(raw)["messages"] if m["kind"] == "QUESTION")
        status, raw = self.post_reply({"body": "H1", "question_id": qid})
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(raw)["execution"], "DISABLED")
        for bad in [{"body": "x"}, {"body": "x", "to": "nope"}, {"body": 5, "to": "risk"},
                    {"body": "x", "question_id": True}, {"body": "x", "question_id": "1"}]:
            self.assertEqual(self.post_reply(bad)[0], 400, bad)
        status, _ = self.call("POST", "/api/desk/reply", "{}", {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        status, _ = self.call("POST", "/api/desk/reply", json.dumps({"body": "x", "to": "risk"}),
                              {"Content-Type": "application/json", "Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_mission_posts_checkins_and_no_order_surface(self):
        self.call("POST", "/api/run", json.dumps({"task": "analyze_symbol"}),
                  {"Content-Type": "application/json"})
        for _ in range(150):
            _, raw = self.call("GET", "/api/desk")
            checkins = [m for m in json.loads(raw)["messages"] if m["kind"] == "CHECKIN" and m["run_id"]]
            if len(checkins) >= len(AGENTS):
                break
            time.sleep(0.05)
        self.assertEqual({c["author"] for c in checkins}, set(AGENTS))
        self.assertIn("NO_TRADE", next(c for c in checkins if c["author"] == "brain")["body"])

    def test_ui_has_no_order_controls(self):
        for path in ("/", "/app.js"):
            _, raw = self.call("GET", path)
            text = raw.decode().lower()
            for forbidden in ("order_send", ">buy<", ">sell<", "place order", "/api/order"):
                self.assertNotIn(forbidden, text)
        _, raw = self.call("GET", "/")
        self.assertIn(b"NO_TRADE", raw)
        self.assertIn(b"DISABLED", raw)


if __name__ == "__main__":
    unittest.main()
