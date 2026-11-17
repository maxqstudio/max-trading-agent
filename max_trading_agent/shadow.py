"""Forward-only SHADOW episode ledger; absolutely no MT5 order capabilities.

Consumes *declared* read-only snapshots. Hypothetical bid/ask fills are never
broker-confirmed transactions and must not be reported as DEMO/LIVE P&L.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from .llm_client import LLMError
from .market_store import DEFAULT_MARKET_DB
from .signal_research import market_context

_CANDIDATE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{3,95}\Z")


class ShadowError(ValueError):
    """Sanitized shadow-research error (no raw broker API response)."""


def _utc(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)


def _nonnegative(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ShadowError("INVALID_" + name)
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ShadowError("INVALID_" + name)
    return value


class ShadowLedger:
    """Private SQLite ledger. The only state transition is PENDING -> SETTLED."""

    def __init__(self, path: Path = DEFAULT_MARKET_DB,
                 *, clock: Callable[[], datetime] | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS shadow_episodes (
                candidate_id TEXT PRIMARY KEY,
                origin TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                status TEXT NOT NULL,
                entry_snapshot_hash TEXT NOT NULL,
                entry_quote_utc TEXT NOT NULL,
                entry_bid REAL NOT NULL,
                entry_ask REAL NOT NULL,
                entry_price REAL NOT NULL,
                slippage REAL NOT NULL,
                fee_per_side REAL NOT NULL,
                created_utc TEXT NOT NULL,
                exit_snapshot_hash TEXT,
                exit_quote_utc TEXT,
                exit_bid REAL,
                exit_ask REAL,
                exit_price REAL,
                net_pnl_price_units REAL,
                recorded_utc TEXT
            )""")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA busy_timeout=10000")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _market(self, snapshot: dict) -> dict:
        # Share M02's privacy scrubber and completed-candle validation.
        try:
            context = market_context(snapshot)
        except LLMError as exc:
            raise ShadowError("UNTRUSTED_OR_INVALID_MARKET_SNAPSHOT") from None
        now = self.clock()
        if now.tzinfo is None:
            raise ShadowError("INVALID_CLOCK")
        now = now.astimezone(timezone.utc)
        captured = _utc(context["snapshot_time_utc"])
        observed = _utc(context["quote"]["time_utc"])
        if not -2 <= (now - captured).total_seconds() <= 30:
            raise ShadowError("SNAPSHOT_NOT_FRESH")
        if not -2 <= (now - observed).total_seconds() <= 30:
            raise ShadowError("QUOTE_NOT_FRESH")
        return context

    def record(self, candidate_id: str, snapshot: dict, *, side: str,
               slippage: float, fee_per_side: float,
               origin: str = "USER_RESEARCH") -> dict:
        if not isinstance(candidate_id, str) or not _CANDIDATE.fullmatch(candidate_id):
            raise ShadowError("INVALID_CANDIDATE_ID")
        if side not in ("LONG", "SHORT") or origin not in ("USER_RESEARCH", "LLM_RESEARCH"):
            raise ShadowError("INVALID_RESEARCH_PROPOSAL")
        slippage = _nonnegative(slippage, "SLIPPAGE")
        fee_per_side = _nonnegative(fee_per_side, "FEE")
        context = self._market(snapshot)
        bid, ask = context["quote"]["bid"], context["quote"]["ask"]
        entry = ask + slippage if side == "LONG" else bid - slippage
        if entry <= 0:
            raise ShadowError("INVALID_EXECUTION_ASSUMPTION")
        data = (candidate_id, origin, context["symbol"], side, "PENDING",
                context["snapshot_hash"], context["quote"]["time_utc"],
                bid, ask, entry, slippage, fee_per_side,
                datetime.now(timezone.utc).isoformat())
        with self._db() as db:
            db.execute("""INSERT OR IGNORE INTO shadow_episodes (
                candidate_id,origin,symbol,side,status,entry_snapshot_hash,
                entry_quote_utc,entry_bid,entry_ask,entry_price,slippage,
                fee_per_side,created_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", data)
        episode = self.get(candidate_id)
        if (episode["origin"] != origin or episode["symbol"] != context["symbol"]
                or episode["side"] != side or episode["entry_snapshot_hash"] != context["snapshot_hash"]
                or episode["slippage"] != slippage or episode["fee_per_side"] != fee_per_side):
            raise ShadowError("CANDIDATE_REUSE_CONFLICT")
        return episode

    def settle(self, candidate_id: str, snapshot: dict) -> dict:
        episode = self.get(candidate_id)
        if episode["status"] not in ("PENDING", "SETTLED"):
            raise ShadowError("INVALID_EPISODE_STATUS")
        context = self._market(snapshot)
        if context["symbol"] != episode["symbol"]:
            raise ShadowError("SYMBOL_MISMATCH")
        if _utc(context["quote"]["time_utc"]) <= _utc(episode["entry_quote_utc"]):
            raise ShadowError("NOT_A_FORWARD_OBSERVATION")
        if episode["status"] == "SETTLED":
            if episode["exit_snapshot_hash"] != context["snapshot_hash"]:
                raise ShadowError("CONFLICTING_SETTLEMENT")
            return episode

        bid, ask = context["quote"]["bid"], context["quote"]["ask"]
        slip = episode["slippage"]
        exit_price = bid - slip if episode["side"] == "LONG" else ask + slip
        if exit_price <= 0:
            raise ShadowError("INVALID_EXECUTION_ASSUMPTION")
        net = ((exit_price - episode["entry_price"])
               if episode["side"] == "LONG"
               else (episode["entry_price"] - exit_price)) - 2 * episode["fee_per_side"]
        with self._db() as db:
            cursor = db.execute("""UPDATE shadow_episodes
                SET status='SETTLED',exit_snapshot_hash=?,exit_quote_utc=?,
                    exit_bid=?,exit_ask=?,exit_price=?,net_pnl_price_units=?,
                    recorded_utc=?
                WHERE candidate_id=? AND status='PENDING'""", (
                context["snapshot_hash"], context["quote"]["time_utc"],
                bid, ask, exit_price, net,
                datetime.now(timezone.utc).isoformat(), candidate_id,
            ))
            if cursor.rowcount != 1:
                raise ShadowError("CONCURRENT_SETTLEMENT_CONFLICT")
        return self.get(candidate_id)

    def get(self, candidate_id: str) -> dict:
        fields = ("candidate_id", "origin", "symbol", "side", "status",
                  "entry_snapshot_hash", "entry_quote_utc", "entry_bid",
                  "entry_ask", "entry_price", "slippage", "fee_per_side",
                  "created_utc", "exit_snapshot_hash", "exit_quote_utc",
                  "exit_bid", "exit_ask", "exit_price",
                  "net_pnl_price_units", "recorded_utc")
        with self._db() as db:
            row = db.execute("SELECT " + ",".join(fields)
                             + " FROM shadow_episodes WHERE candidate_id=?",
                             (candidate_id,)).fetchone()
        if row is None:
            raise ShadowError("CANDIDATE_NOT_FOUND")
        episode = dict(zip(fields, row))
        episode.update({"mode": "SHADOW", "broker_execution": False,
                        "fill_type": "HYPOTHETICAL", "unit": "PRICE_PER_ASSET_UNIT",
                        "baseline_no_trade_pnl": 0.0})
        return episode
