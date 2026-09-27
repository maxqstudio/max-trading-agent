from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .champion_store import migrate_m04
from .config import DATABASE_PATH
from .db import connect
from .optimizer_store import utc_now

SCHEMA_VERSION = 6
MIN_SUPPORTED_SCHEMA = 6
REQUEST_STATES = {"PREPARED", "CALL_IN_FLIGHT", "COMPLETED", "UNCONFIRMED", "FAILED"}
CHAT_TABLES = {"scientist_threads", "scientist_messages", "scientist_chat_requests"}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _decode_json(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


def migrate_m05(path: Path = DATABASE_PATH) -> None:
    migrate_m04(path)
    with connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scientist_threads (
                thread_id TEXT PRIMARY KEY,
                created_utc TEXT NOT NULL,
                updated_utc TEXT NOT NULL,
                title TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scientist_chat_requests (
                request_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN ('PREPARED','CALL_IN_FLIGHT','COMPLETED','UNCONFIRMED','FAILED')
                ),
                created_utc TEXT NOT NULL,
                updated_utc TEXT NOT NULL,
                completed_utc TEXT,
                confirmed_provider_calls INTEGER NOT NULL DEFAULT 0 CHECK (confirmed_provider_calls IN (0,1)),
                unconfirmed_provider_attempts INTEGER NOT NULL DEFAULT 0 CHECK (unconfirmed_provider_attempts IN (0,1)),
                knowledge_sha256 TEXT,
                context_sha256 TEXT,
                provider_provenance_json TEXT,
                response_sha256 TEXT,
                assistant_message_id TEXT,
                error_code TEXT,
                FOREIGN KEY(thread_id) REFERENCES scientist_threads(thread_id)
            );

            CREATE TABLE IF NOT EXISTS scientist_messages (
                message_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user','assistant')),
                content TEXT NOT NULL,
                created_utc TEXT NOT NULL,
                classification TEXT,
                evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                knowledge_sha256 TEXT,
                context_sha256 TEXT,
                provider_provenance_json TEXT,
                request_id TEXT,
                UNIQUE(thread_id, sequence),
                FOREIGN KEY(thread_id) REFERENCES scientist_threads(thread_id),
                FOREIGN KEY(request_id) REFERENCES scientist_chat_requests(request_id)
            );

            CREATE INDEX IF NOT EXISTS ix_scientist_threads_updated
            ON scientist_threads(updated_utc DESC, thread_id DESC);
            CREATE INDEX IF NOT EXISTS ix_scientist_messages_thread
            ON scientist_messages(thread_id, sequence);
            CREATE INDEX IF NOT EXISTS ix_scientist_requests_thread
            ON scientist_chat_requests(thread_id, created_utc);
            """
        )
        current = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        version = int(current["value"]) if current is not None else 0
        if version < SCHEMA_VERSION:
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )


def scientist_database_status(path: Path = DATABASE_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "FAIL", "reason": "DATABASE_FILE_MISSING"}
    try:
        migrate_m05(path)
        with connect(path) as conn:
            schema = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
            baseline = conn.execute("SELECT status FROM ea_baseline WHERE id=1").fetchone()
            tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        actual = int(schema["value"]) if schema is not None else None
        if actual is None or actual < MIN_SUPPORTED_SCHEMA:
            return {"status": "FAIL", "reason": "SCHEMA_VERSION_MISMATCH", "schema_version": actual}
        if not CHAT_TABLES.issubset(tables):
            return {"status": "FAIL", "reason": "M05_TABLES_MISSING"}
        if baseline is None or baseline["status"] != "BASELINE_NOT_CHAMPION":
            return {"status": "FAIL", "reason": "EA_BASELINE_AUTHORITY_INVALID"}
        return {"status": "READY", "schema_version": actual, "baseline_status": baseline["status"]}
    except Exception as exc:
        return {"status": "FAIL", "reason": "DATABASE_UNAVAILABLE", "detail": str(exc)}


def _decode_thread(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _decode_message(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["evidence_refs"] = _decode_json(result.pop("evidence_refs_json"), [])
    result["provider_provenance"] = _decode_json(result.pop("provider_provenance_json"), {})
    return result


def _decode_request(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["provider_provenance"] = _decode_json(result.pop("provider_provenance_json"), {})
    return result


def create_thread(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    migrate_m05(path)
    thread_id = "SCI-" + uuid.uuid4().hex
    now = utc_now()
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO scientist_threads(thread_id,created_utc,updated_utc,title) VALUES(?,?,?,?)",
            (thread_id, now, now, "New Chat"),
        )
    thread = get_thread(thread_id, path=path)
    if thread is None:
        raise RuntimeError("SCIENTIST_THREAD_CREATE_FAILED")
    return thread


def get_thread(thread_id: str, *, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    migrate_m05(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM scientist_threads WHERE thread_id=?",
            (str(thread_id),),
        ).fetchone()
    return _decode_thread(row)


def list_threads(*, limit: int = 100, path: Path = DATABASE_PATH) -> list[dict[str, Any]]:
    migrate_m05(path)
    bounded = max(1, min(int(limit), 200))
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM scientist_threads ORDER BY updated_utc DESC, thread_id DESC LIMIT ?",
            (bounded,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_messages(
    thread_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> list[dict[str, Any]]:
    migrate_m05(path)
    with connect(path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM scientist_threads WHERE thread_id=?",
            (str(thread_id),),
        ).fetchone()
        if exists is None:
            raise FileNotFoundError(thread_id)
        rows = conn.execute(
            "SELECT * FROM scientist_messages WHERE thread_id=? ORDER BY sequence",
            (str(thread_id),),
        ).fetchall()
    return [item for row in rows if (item := _decode_message(row)) is not None]


def _title_from_message(content: str) -> str:
    compact = " ".join(str(content).strip().split())
    if not compact:
        return "New Chat"
    return compact if len(compact) <= 72 else compact[:69].rstrip() + "..."


def get_request(request_id: str, *, path: Path = DATABASE_PATH) -> dict[str, Any] | None:
    migrate_m05(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM scientist_chat_requests WHERE request_id=?",
            (str(request_id),),
        ).fetchone()
    return _decode_request(row)


def prepare_request(
    thread_id: str,
    request_id: str,
    content: str,
    *,
    path: Path = DATABASE_PATH,
) -> tuple[dict[str, Any], bool]:
    migrate_m05(path)
    body = str(content).strip()
    if not body:
        raise ValueError("SCIENTIST_MESSAGE_EMPTY")
    if len(body) > 8000:
        raise ValueError("SCIENTIST_MESSAGE_TOO_LONG")
    now = utc_now()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM scientist_chat_requests WHERE request_id=?",
            (str(request_id),),
        ).fetchone()
        if existing is not None:
            if str(existing["thread_id"]) != str(thread_id):
                raise RuntimeError("SCIENTIST_REQUEST_ID_THREAD_CONFLICT")
            return _decode_request(existing) or {}, False
        thread = conn.execute(
            "SELECT * FROM scientist_threads WHERE thread_id=?",
            (str(thread_id),),
        ).fetchone()
        if thread is None:
            raise FileNotFoundError(thread_id)
        seq = int(conn.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM scientist_messages WHERE thread_id=?",
            (str(thread_id),),
        ).fetchone()["n"])
        conn.execute(
            """
            INSERT INTO scientist_chat_requests(
                request_id,thread_id,state,created_utc,updated_utc,
                confirmed_provider_calls,unconfirmed_provider_attempts
            ) VALUES(?,?,'PREPARED',?,?,0,0)
            """,
            (str(request_id), str(thread_id), now, now),
        )
        message_id = "MSG-" + uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO scientist_messages(
                message_id,thread_id,sequence,role,content,created_utc,
                evidence_refs_json,request_id
            ) VALUES(?,?,?,'user',?,?, '[]',?)
            """,
            (message_id, str(thread_id), seq, body, now, str(request_id)),
        )
        title = str(thread["title"])
        if seq == 1 and title == "New Chat":
            title = _title_from_message(body)
        conn.execute(
            "UPDATE scientist_threads SET updated_utc=?, title=? WHERE thread_id=?",
            (now, title, str(thread_id)),
        )
        row = conn.execute(
            "SELECT * FROM scientist_chat_requests WHERE request_id=?",
            (str(request_id),),
        ).fetchone()
    return _decode_request(row) or {}, True


def mark_request_in_flight(
    request_id: str,
    *,
    knowledge_sha256: str,
    context_sha256: str,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    now = utc_now()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM scientist_chat_requests WHERE request_id=?",
            (str(request_id),),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(request_id)
        if str(row["state"]) != "PREPARED":
            return _decode_request(row) or {}
        conn.execute(
            """
            UPDATE scientist_chat_requests
            SET state='CALL_IN_FLIGHT', updated_utc=?,
                unconfirmed_provider_attempts=1,
                knowledge_sha256=?, context_sha256=?
            WHERE request_id=? AND state='PREPARED'
            """,
            (now, str(knowledge_sha256), str(context_sha256), str(request_id)),
        )
        row = conn.execute(
            "SELECT * FROM scientist_chat_requests WHERE request_id=?",
            (str(request_id),),
        ).fetchone()
    return _decode_request(row) or {}


def complete_request(
    request_id: str,
    *,
    answer: str,
    classification: str,
    evidence_refs: list[str],
    knowledge_sha256: str,
    context_sha256: str,
    provider_provenance: dict[str, Any],
    response_sha256: str,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    now = utc_now()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        request = conn.execute(
            "SELECT * FROM scientist_chat_requests WHERE request_id=?",
            (str(request_id),),
        ).fetchone()
        if request is None:
            raise FileNotFoundError(request_id)
        if str(request["state"]) == "COMPLETED":
            return _decode_request(request) or {}
        if str(request["state"]) != "CALL_IN_FLIGHT":
            raise RuntimeError("SCIENTIST_REQUEST_NOT_IN_FLIGHT")
        thread_id = str(request["thread_id"])
        seq = int(conn.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM scientist_messages WHERE thread_id=?",
            (thread_id,),
        ).fetchone()["n"])
        message_id = "MSG-" + uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO scientist_messages(
                message_id,thread_id,sequence,role,content,created_utc,
                classification,evidence_refs_json,knowledge_sha256,context_sha256,
                provider_provenance_json,request_id
            ) VALUES(?,?,?,'assistant',?,?,?,?,?,?,?,?)
            """,
            (
                message_id, thread_id, seq, str(answer), now, str(classification),
                _json(evidence_refs), str(knowledge_sha256), str(context_sha256),
                _json(provider_provenance), str(request_id),
            ),
        )
        conn.execute(
            """
            UPDATE scientist_chat_requests
            SET state='COMPLETED', updated_utc=?, completed_utc=?,
                confirmed_provider_calls=1,
                unconfirmed_provider_attempts=0,
                provider_provenance_json=?,
                response_sha256=?, assistant_message_id=?, error_code=NULL
            WHERE request_id=?
            """,
            (
                now, now, _json(provider_provenance), str(response_sha256),
                message_id, str(request_id),
            ),
        )
        conn.execute(
            "UPDATE scientist_threads SET updated_utc=? WHERE thread_id=?",
            (now, thread_id),
        )
        row = conn.execute(
            "SELECT * FROM scientist_chat_requests WHERE request_id=?",
            (str(request_id),),
        ).fetchone()
    return _decode_request(row) or {}


def fail_request(
    request_id: str,
    error_code: str,
    *,
    confirmed_provider_call: bool = False,
    provider_provenance: dict[str, Any] | None = None,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    now = utc_now()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM scientist_chat_requests WHERE request_id=?",
            (str(request_id),),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(request_id)
        if str(row["state"]) == "COMPLETED":
            return _decode_request(row) or {}
        conn.execute(
            """
            UPDATE scientist_chat_requests
            SET state='FAILED', updated_utc=?, error_code=?,
                confirmed_provider_calls=?,
                unconfirmed_provider_attempts=0,
                provider_provenance_json=?
            WHERE request_id=?
            """,
            (
                now, str(error_code), 1 if confirmed_provider_call else 0,
                _json(provider_provenance or {}), str(request_id),
            ),
        )
        row = conn.execute(
            "SELECT * FROM scientist_chat_requests WHERE request_id=?",
            (str(request_id),),
        ).fetchone()
    return _decode_request(row) or {}


def recover_unconfirmed_requests(*, path: Path = DATABASE_PATH) -> int:
    migrate_m05(path)
    now = utc_now()
    with connect(path) as conn:
        cursor = conn.execute(
            """
            UPDATE scientist_chat_requests
            SET state='UNCONFIRMED', updated_utc=?,
                confirmed_provider_calls=0,
                unconfirmed_provider_attempts=1,
                error_code='SCIENTIST_UNCONFIRMED_CALL_AFTER_RESTART'
            WHERE state='CALL_IN_FLIGHT'
            """,
            (now,),
        )
    return int(cursor.rowcount)


def get_completed_assistant_message(
    request_id: str,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    migrate_m05(path)
    with connect(path) as conn:
        row = conn.execute(
            """
            SELECT m.* FROM scientist_messages m
            JOIN scientist_chat_requests r ON r.assistant_message_id=m.message_id
            WHERE r.request_id=? AND r.state='COMPLETED'
            """,
            (str(request_id),),
        ).fetchone()
    return _decode_message(row)



def mark_request_unconfirmed(
    request_id: str,
    error_code: str,
    *,
    provider_provenance: dict[str, Any] | None = None,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    now = utc_now()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM scientist_chat_requests WHERE request_id=?",
            (str(request_id),),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(request_id)
        if str(row["state"]) == "COMPLETED":
            return _decode_request(row) or {}
        conn.execute(
            """
            UPDATE scientist_chat_requests
            SET state='UNCONFIRMED', updated_utc=?, error_code=?,
                confirmed_provider_calls=0, unconfirmed_provider_attempts=1,
                provider_provenance_json=?
            WHERE request_id=?
            """,
            (
                now, str(error_code), _json(provider_provenance or {}),
                str(request_id),
            ),
        )
        row = conn.execute(
            "SELECT * FROM scientist_chat_requests WHERE request_id=?",
            (str(request_id),),
        ).fetchone()
    return _decode_request(row) or {}


def get_active_thread(
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    """Return the one visible Scientist chat thread, creating it when absent."""
    migrate_m05(path)
    with connect(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM scientist_threads
            ORDER BY updated_utc DESC, thread_id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is not None:
        return dict(row)
    return create_thread(path=path)


def clear_chat(
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    """Rotate the single visible chat generation and delete prior chat history."""
    migrate_m05(path)
    now = utc_now()
    thread_id = "SCI-" + uuid.uuid4().hex
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        in_flight = conn.execute(
            """
            SELECT request_id FROM scientist_chat_requests
            WHERE state='CALL_IN_FLIGHT'
            LIMIT 1
            """
        ).fetchone()
        if in_flight is not None:
            raise RuntimeError("SCIENTIST_CLEAR_BLOCKED_CALL_IN_FLIGHT")

        conn.execute("DELETE FROM scientist_messages")
        conn.execute("DELETE FROM scientist_chat_requests")
        conn.execute("DELETE FROM scientist_threads")
        conn.execute(
            """
            INSERT INTO scientist_threads(
                thread_id, created_utc, updated_utc, title
            ) VALUES(?,?,?,?)
            """,
            (thread_id, now, now, "Scientist Chat"),
        )
        row = conn.execute(
            "SELECT * FROM scientist_threads WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
    decoded = _decode_thread(row)
    if decoded is None:
        raise RuntimeError("SCIENTIST_CHAT_CLEAR_FAILED")
    return decoded


def purge_chat(
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, int]:
    """Delete Scientist operational chat state without creating a replacement thread."""
    migrate_m05(path)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        in_flight = conn.execute(
            """
            SELECT request_id FROM scientist_chat_requests
            WHERE state='CALL_IN_FLIGHT'
            LIMIT 1
            """
        ).fetchone()
        if in_flight is not None:
            raise RuntimeError("SCIENTIST_PURGE_BLOCKED_CALL_IN_FLIGHT")
        conn.execute("DELETE FROM scientist_messages")
        conn.execute("DELETE FROM scientist_chat_requests")
        conn.execute("DELETE FROM scientist_threads")
        final = {
            "threads": int(conn.execute(
                "SELECT COUNT(*) AS n FROM scientist_threads"
            ).fetchone()["n"]),
            "messages": int(conn.execute(
                "SELECT COUNT(*) AS n FROM scientist_messages"
            ).fetchone()["n"]),
            "requests": int(conn.execute(
                "SELECT COUNT(*) AS n FROM scientist_chat_requests"
            ).fetchone()["n"]),
        }
        if any(final.values()):
            raise RuntimeError("SCIENTIST_PURGE_VERIFICATION_FAILED")
        return final
