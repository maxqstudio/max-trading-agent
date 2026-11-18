"""Loopback-only Agent Trading Room backed by actual stored reference events."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .desk import Desk, SAFETY, AGENTS
from .runtime import ALLOWED_TASKS, DEFAULT_JOURNAL, DEFAULT_KNOWLEDGE, EventJournal, ReferenceMission

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


def create_server(host: str = "127.0.0.1", port: int = 8765,
                  journal_path: Path = DEFAULT_JOURNAL,
                  knowledge_path: Path = DEFAULT_KNOWLEDGE,
                  heartbeat_seconds: float = 300.0) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("UI must bind to local loopback only in M00")
    journal = EventJournal(journal_path)
    mission = ReferenceMission(journal, knowledge_path)
    desk = Desk(journal)
    desk.startup_checkin()
    stop = threading.Event()

    def run_mission(run_id: str, task: str) -> None:
        try:
            mission.execute(run_id, task)
        finally:
            try:
                desk.checkin_for_run(run_id)
            except Exception:
                pass

    def heartbeat() -> None:
        tick = 0
        while not stop.wait(heartbeat_seconds):
            try:
                desk.heartbeat(tick)
            except Exception:
                pass
            tick += 1

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, code: int, payload: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; img-src 'self'; base-uri 'none'; "
                             "form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, code: int, data: object) -> None:
            self._respond(code, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

        def _valid_host(self) -> bool:
            hostname = self.headers.get("Host", "").split(":", 1)[0].lower()
            return hostname in {"127.0.0.1", "localhost"}

        def do_GET(self) -> None:
            if not self._valid_host():
                self._json(403, {"error": "invalid_host"})
                return
            url = urlsplit(self.path)
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if url.path in assets:
                filename, mime = assets[url.path]
                self._respond(200, (UI_DIR / filename).read_bytes(), mime)
                return
            if url.path == "/api/runs":
                self._json(200, {"runs": journal.runs()})
                return
            if url.path == "/api/events":
                q = parse_qs(url.query)
                run_id = q.get("run_id", [""])[0]
                try:
                    after = int(q.get("after", ["0"])[0])
                    rows = journal.events(run_id, after)
                except (ValueError, OverflowError):
                    self._json(400, {"error": "invalid_cursor"})
                    return
                self._json(200, {"events": rows})
                return
            if url.path == "/api/desk":
                q = parse_qs(url.query)
                try:
                    after = int(q.get("after", ["0"])[0])
                    rows = desk.messages(after)
                except (ValueError, OverflowError):
                    self._json(400, {"error": "invalid_cursor"})
                    return
                self._json(200, {"messages": rows, "open_questions": desk.open_questions(),
                                 "started_at": desk.started_at, "safety": SAFETY,
                                 "agents": list(AGENTS)})
                return
            self._json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if not self._valid_host():
                self._json(403, {"error": "invalid_host"})
                return
            route = urlsplit(self.path).path
            if route not in ("/api/run", "/api/desk/reply"):
                self._json(404, {"error": "not_found"})
                return
            # JSON requests are not cross-origin simple requests; reject an explicit foreign Origin.
            origin = self.headers.get("Origin")
            allowed_origins = {f"http://{self.headers.get('Host', '')}"}
            if origin and origin not in allowed_origins:
                self._json(403, {"error": "cross_origin_forbidden"})
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self._json(415, {"error": "json_required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 4096:
                    raise ValueError("Invalid content length")
                data = json.loads(self.rfile.read(length))
                if route == "/api/desk/reply":
                    qid = data.get("question_id")
                    if qid is not None and (not isinstance(qid, int) or isinstance(qid, bool)):
                        raise ValueError("Bad question")
                    to = data.get("to")
                    if to is not None and not isinstance(to, str):
                        raise ValueError("Bad agent")
                    result = desk.reply(data.get("body"), qid, to)
                    self._json(201, {**result, "execution": "DISABLED"})
                    return
                task = data["task"]
                if not isinstance(task, str) or task not in ALLOWED_TASKS:
                    raise ValueError("Unknown task")
            except (ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError):
                self._json(400, {"error": "invalid_request"})
                return
            run_id = journal.start(task)
            threading.Thread(target=run_mission, args=(run_id, task),
                             daemon=True, name=f"reference-mission-{run_id[:8]}").start()
            self._json(202, {"run_id": run_id, "mode": "REFERENCE_ONLY", "execution": "DISABLED"})

        def log_message(self, format: str, *args: object) -> None:
            # Do not write raw client inputs or query strings to disk.
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    original_close = server.server_close

    def close() -> None:
        stop.set()
        original_close()

    server.server_close = close  # type: ignore[method-assign]
    if heartbeat_seconds and heartbeat_seconds > 0:
        threading.Thread(target=heartbeat, daemon=True, name="desk-heartbeat").start()
    return server
