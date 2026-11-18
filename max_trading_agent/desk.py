"""Agent Desk: check-ins, questions and replies between the agents and the owner.

Observation/journal only. Nothing here can place an order, contact a broker or change
the NO_TRADE decision. Owner replies are stored as research notes/preferences; they are
never executed or interpreted as commands.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from .runtime import EventJournal

AGENTS = ("scanner", "analyst", "brain", "executor", "check", "risk")
AUTHORS = frozenset(AGENTS) | {"jackson"}
KINDS = frozenset({"CHECKIN", "QUESTION", "REPLY", "ACK"})
MAX_BODY = 500
SAFETY = {"decision": "NO_TRADE", "execution": "DISABLED",
          "broker_execution": False, "owner_acceptance": "PENDING"}

STANDING_QUESTIONS = (
    ("scanner", "Watchlist riset mana yang kamu mau? Ini hanya perencanaan offline; belum ada data live.",
     ["XAUUSD", "EURUSD", "BTCUSD"]),
    ("analyst", "Timeframe utama mana untuk analisa nanti?", ["M15", "H1", "H4"]),
    ("brain", "Mau briefing rutin dari tim setiap pagi?", ["Ya, tiap pagi", "Nanti dulu"]),
    ("risk", "Catatan kebijakan: batas risiko per trade yang kamu inginkan? (hanya dicatat, tidak dieksekusi)",
     ["0.5%", "1%", "2%"]),
    ("check", "Evidence apa yang paling ingin kamu lihat lebih dulu?",
     ["Jurnal harian", "Shadow trade", "Backtest"]),
    ("executor", "Saya terkunci (DISABLED). Mau saya kirim checklist syarat sebelum eksekusi boleh dibahas?",
     ["Ya, kirim", "Tidak perlu"]),
)

ACK_TEXT = {
    "scanner": "Dicatat. Saya akan rencanakan pembacaan MT5 untuk itu saat terminal terhubung.",
    "analyst": "Dicatat. Saya tunggu snapshot pasar nyata sebelum menganalisa apa pun.",
    "brain": "Dicatat. Saya masukkan ke jurnal tim.",
    "executor": "Dicatat. Saya tetap DISABLED; tidak ada jalur order di sini.",
    "check": "Dicatat. Saya prioritaskan evidence itu di pemeriksaan berikutnya.",
    "risk": "Dicatat sebagai preferensi saja. Keputusan tetap NO_TRADE.",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Desk:
    def __init__(self, journal: EventJournal):
        self.journal = journal
        self.started_at = _utc_now()
        self._lock = threading.Lock()
        with journal._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS desk_messages ("
                "msg_id INTEGER PRIMARY KEY AUTOINCREMENT, author TEXT NOT NULL, "
                "kind TEXT NOT NULL, body TEXT NOT NULL, options TEXT NOT NULL DEFAULT '[]', "
                "parent_id INTEGER, run_id TEXT, answered INTEGER NOT NULL DEFAULT 0, "
                "created_at TEXT NOT NULL)")
        self.seed_questions()

    def post(self, author: str, kind: str, body: str, options: list[str] | None = None,
             parent_id: int | None = None, run_id: str | None = None) -> int:
        if author not in AUTHORS or kind not in KINDS or not body or len(body) > MAX_BODY:
            raise ValueError("Invalid desk message")
        with self.journal._db() as db:
            cur = db.execute(
                "INSERT INTO desk_messages (author, kind, body, options, parent_id, run_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (author, kind, body, json.dumps(options or []), parent_id, run_id, _utc_now()))
            return int(cur.lastrowid)

    def seed_questions(self) -> None:
        with self._lock:
            for agent, body, options in STANDING_QUESTIONS:
                with self.journal._db() as db:
                    exists = db.execute(
                        "SELECT 1 FROM desk_messages WHERE kind='QUESTION' AND author=? AND body=?",
                        (agent, body)).fetchone()
                if not exists:
                    self.post(agent, "QUESTION", body, options)

    def messages(self, after: int = 0, limit: int = 200) -> list[dict]:
        if not isinstance(after, int) or after < 0 or not 1 <= limit <= 500:
            raise ValueError("Invalid desk cursor")
        with self.journal._db() as db:
            rows = db.execute(
                "SELECT msg_id, author, kind, body, options, parent_id, run_id, answered, created_at "
                "FROM desk_messages WHERE msg_id > ? ORDER BY msg_id ASC LIMIT ?", (after, limit)).fetchall()
        keys = ("msg_id", "author", "kind", "body", "options", "parent_id", "run_id", "answered", "created_at")
        out = []
        for row in rows:
            item = dict(zip(keys, row))
            item["options"] = json.loads(item["options"])
            item["answered"] = bool(item["answered"])
            out.append(item)
        return out

    def open_questions(self) -> int:
        with self.journal._db() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM desk_messages WHERE kind='QUESTION' AND answered=0").fetchone()[0])

    def reply(self, body: str, question_id: int | None = None, to: str | None = None) -> dict:
        """Store the owner's reply/note and an acknowledgement. Never executes anything."""
        body = body.strip() if isinstance(body, str) else ""
        if not body or len(body) > MAX_BODY:
            raise ValueError("Invalid reply")
        agent = to
        if question_id is not None:
            with self.journal._db() as db:
                row = db.execute("SELECT author FROM desk_messages WHERE msg_id=? AND kind='QUESTION'",
                                 (question_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown question")
            agent = row[0]
        if agent not in AGENTS:
            raise ValueError("Unknown agent")
        reply_id = self.post("jackson", "REPLY", body, parent_id=question_id)
        if question_id is not None:
            with self.journal._db() as db:
                db.execute("UPDATE desk_messages SET answered=1 WHERE msg_id=?", (question_id,))
        ack_id = self.post(agent, "ACK", ACK_TEXT[agent] + " (Catatan riset saja; tidak mengubah NO_TRADE.)",
                           parent_id=reply_id)
        return {"reply_id": reply_id, "ack_id": ack_id}

    def checkin_for_run(self, run_id: str) -> None:
        """One short check-in per agent, derived only from recorded events of the run."""
        events = self.journal.events(run_id, 0, 500)
        by_role: dict[str, list[dict]] = {}
        for e in events:
            by_role.setdefault(e["role"], []).append(e)
        n_tools = len(by_role.get("MT5 Specialist", []))
        docs = next((e["details"].get("documents_indexed") for e in events
                     if e["event_type"] == "TOOL_RESULT"), None)
        vetoes = next((e["details"].get("vetoes", []) for e in events if e["event_type"] == "DECISION"), [])
        blocked = sum(1 for e in events if e["status"] == "BLOCKED")
        lines = {
            "scanner": f"Misi selesai: {n_tools} langkah rencana MT5 tercatat; belum ada pool/quote yang dibaca.",
            "analyst": "Tidak ada snapshot pasar nyata, jadi saya tidak membuat setup. 0 setup.",
            "brain": f"Ringkasan: {len(events)} event, {blocked} diblokir. Keputusan NO_TRADE.",
            "executor": "Tidak ada order. Eksekusi DISABLED; jalur order tidak ada.",
            "check": f"Indeks pengetahuan diperiksa: {docs if docs is not None else '?'} dokumen terindeks.",
            "risk": "Veto aktif: " + (", ".join(vetoes) if vetoes else "tidak ada") + ".",
        }
        for agent in AGENTS:
            self.post(agent, "CHECKIN", lines[agent], run_id=run_id)

    def startup_checkin(self) -> None:
        self.post("brain", "CHECKIN",
                  "Tim online. Mode REFERENCE_ONLY: NO_TRADE, eksekusi DISABLED, MT5 belum terhubung.")

    def heartbeat(self, tick: int) -> None:
        agent = AGENTS[tick % len(AGENTS)]
        runs = self.journal.runs(100)
        text = {
            "scanner": "Standby. Menunggu terminal MT5 untuk membaca pasar; belum ada data live.",
            "analyst": "Standby. Tanpa data pasar nyata saya tidak membuat analisa.",
            "brain": f"Rutin: {len(runs)} misi tercatat. {self.open_questions()} pertanyaan menunggu jawabanmu.",
            "executor": "Terkunci. Tidak ada yang bisa saya kirim; eksekusi DISABLED.",
            "check": "Cek rutin: pagar keselamatan tetap NO_TRADE / DISABLED / broker off.",
            "risk": "Risiko nol: tidak ada posisi terbuka, tidak ada order.",
        }[agent]
        self.post(agent, "CHECKIN", text)
