"""Loopback web UI smoke: actual persisted reference activity, not trading."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from max_trading_agent.web import create_server


class WebTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.server = create_server(port=0, journal_path=root / "events.sqlite3",
                                    knowledge_path=root / "kb.sqlite3")
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup_server)

    def cleanup_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        status, content, response_headers = response.status, response.read(), dict(response.getheaders())
        conn.close()
        return status, content, response_headers

    def test_static_assets_have_security_headers(self):
        for path, marker in [("/", b"TEAM PROGRESS"), ("/app.js", b"/api/events"),
                             ("/style.css", b".workspace")]:
            status, data, headers = self.call("GET", path)
            self.assertEqual(status, 200)
            self.assertIn(marker, data)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_reference_mission_is_accessible_by_event_cursor(self):
        status, data, _ = self.call(
            "POST", "/api/run", json.dumps({"task": "analyze_symbol"}),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 202)
        run_id = json.loads(data)["run_id"]
        for _ in range(100):
            _, data, _ = self.call("GET", "/api/runs")
            runs = json.loads(data)["runs"]
            if runs and runs[0]["status"] == "COMPLETED":
                break
            time.sleep(0.02)
        else:
            self.fail("Reference mission did not complete")
        _, raw, _ = self.call("GET", "/api/events?run_id=" + run_id)
        events = json.loads(raw)["events"]
        self.assertGreaterEqual(len(events), 8)
        self.assertEqual(next(e for e in events if e["event_type"] == "DECISION")["details"]["action"], "NO_TRADE")
        _, raw, _ = self.call("GET", "/api/events?run_id=" + run_id +
                              "&after=" + str(events[-1]["event_id"]))
        self.assertEqual(json.loads(raw)["events"], [])

    def test_bad_requests_and_foreign_origin(self):
        cases = [
            ("application/json", "trade_live", None, 400),
            ("text/plain", "analyze_symbol", None, 415),
            ("application/json", "analyze_symbol", "https://attacker.example", 403),
        ]
        for content_type, task, origin, want in cases:
            headers = {"Content-Type": content_type}
            if origin:
                headers["Origin"] = origin
            status, _, _ = self.call("POST", "/api/run", json.dumps({"task": task}), headers)
            self.assertEqual(status, want)
        _, raw, _ = self.call("GET", "/api/runs")
        self.assertEqual(json.loads(raw)["runs"], [])

    def test_loopback_only(self):
        with self.assertRaises(ValueError):
            create_server(host="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()
