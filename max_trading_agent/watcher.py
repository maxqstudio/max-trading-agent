"""Autonomous *read-only* candle watcher and durable trigger state.

No MT5 order, account-mutation tool, or generated executable is reachable here.
A user starts/stops the process explicitly. An agent role is only invoked when
opted-in and when a new completed candle is observed prospectively.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .identity import IdentityError, verify_snapshot
from .llm_client import LLMError
from .mt5_readonly import MT5ReadError, ReadOnlyMT5
from .runtime import DEFAULT_KNOWLEDGE, EventJournal
from .shadow import ShadowError
from .shadow_pipeline import ResearchShadowPipeline
from .signal_research import SignalResearch, market_context

INTERVALS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800,
             "H1": 3600, "H4": 14400, "D1": 86400}
LEASE_SECONDS = 180


class WatchError(ValueError):
    """Safe watcher error; code intentionally excludes private broker data."""


def _fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


class WatchState:
    """Private SQLite monitor, checkpoint and research admission gate."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS watch_state (
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                bar_utc TEXT NOT NULL, bar_hash TEXT NOT NULL,
                account_mode TEXT NOT NULL, seen_utc TEXT NOT NULL,
                account_binding_id TEXT,
                PRIMARY KEY(symbol, timeframe))""")
            # Existing M05 DBs contain unbound baselines. A NULL binding
            # explicitly blocks research; never auto-approve a legacy state.
            columns = {row[1] for row in db.execute("PRAGMA table_info(watch_state)")}
            if "account_binding_id" not in columns:
                db.execute("ALTER TABLE watch_state ADD COLUMN account_binding_id TEXT")
            db.execute("""CREATE TABLE IF NOT EXISTS watch_jobs (
                job_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                bar_utc TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
                status TEXT NOT NULL, research_run_id TEXT,
                created_utc TEXT NOT NULL, completed_utc TEXT,
                shadow_requested INTEGER NOT NULL DEFAULT 0,
                UNIQUE(symbol,timeframe,bar_utc))""")
            # Existing M05/M06 rows are not retrospectively billable cycles.
            job_columns = {row[1] for row in db.execute("PRAGMA table_info(watch_jobs)")}
            if "shadow_requested" not in job_columns:
                db.execute("ALTER TABLE watch_jobs ADD COLUMN shadow_requested "
                           "INTEGER NOT NULL DEFAULT 0")
            db.execute("""CREATE TABLE IF NOT EXISTS watch_leases (
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                owner TEXT NOT NULL, expires_epoch REAL NOT NULL,
                PRIMARY KEY(symbol,timeframe))""")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def acquire(self, symbol: str, timeframe: str, owner: str, *, now: float) -> None:
        with self._db() as db:
            old = db.execute(
                "SELECT owner, expires_epoch FROM watch_leases "
                "WHERE symbol=? AND timeframe=?", (symbol, timeframe)
            ).fetchone()
            if old and old[0] != owner and old[1] > now:
                raise WatchError("MONITOR_ALREADY_ACTIVE")
            db.execute("""INSERT INTO watch_leases (symbol,timeframe,owner,expires_epoch)
                VALUES (?,?,?,?) ON CONFLICT(symbol,timeframe) DO UPDATE
                SET owner=excluded.owner,expires_epoch=excluded.expires_epoch""",
                (symbol, timeframe, owner, now + LEASE_SECONDS))
            # An abandoned LLM call is never automatically billed a second time.
            db.execute("""UPDATE watch_jobs SET status='REVIEW_REQUIRED',
                completed_utc=? WHERE symbol=? AND timeframe=?
                AND status='RUNNING'""",
                (datetime.now(timezone.utc).isoformat(), symbol, timeframe))

    def renew(self, symbol: str, timeframe: str, owner: str, *, now: float) -> None:
        with self._db() as db:
            cursor = db.execute(
                "UPDATE watch_leases SET expires_epoch=? WHERE symbol=? "
                "AND timeframe=? AND owner=? AND expires_epoch>=?",
                (now + LEASE_SECONDS, symbol, timeframe, owner, now),
            )
            if cursor.rowcount != 1:
                raise WatchError("WATCH_LEASE_LOST")

    def release(self, symbol: str, timeframe: str, owner: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM watch_leases WHERE symbol=? AND timeframe=? AND owner=?",
                       (symbol, timeframe, owner))

    def observe(self, public: dict, account_mode: str, *,
                observed_at: str, account_binding_id: str) -> dict:
        """Atomic baseline/new-candle detection and one-time job creation."""
        if not isinstance(account_binding_id, str) or len(account_binding_id) != 64 or (
            any(c not in "0123456789abcdef" for c in account_binding_id)
        ):
            raise WatchError("ACCOUNT_BINDING_MISSING")
        symbol, timeframe = public["symbol"], public["timeframe"]
        bar = public["completed_candles"][-1]
        bar_utc = bar["time_utc"]
        bar_hash = _fingerprint(bar)
        snapshot_hash = public["snapshot_hash"]
        step = INTERVALS[timeframe]
        with self._db() as db:
            row = db.execute(
                "SELECT bar_utc,bar_hash,account_mode,account_binding_id FROM watch_state "
                "WHERE symbol=? AND timeframe=?", (symbol, timeframe)
            ).fetchone()
            if row is None:
                db.execute("""INSERT INTO watch_state (
                    symbol,timeframe,bar_utc,bar_hash,account_mode,seen_utc,
                    account_binding_id
                ) VALUES (?,?,?,?,?,?,?)""",
                           (symbol, timeframe, bar_utc, bar_hash,
                            account_mode, observed_at, account_binding_id))
                return {"status": "BASELINED", "bar_utc": bar_utc}
            prev_utc, prev_hash, prev_mode, prev_identity = row
            if prev_identity is None:
                return {"status": "ACCOUNT_BINDING_REQUIRED", "bar_utc": bar_utc}
            if prev_identity != account_binding_id:
                return {"status": "ACCOUNT_ID_CHANGED", "bar_utc": bar_utc}
            if prev_mode != account_mode:
                return {"status": "ACCOUNT_MODE_CHANGED", "bar_utc": bar_utc}
            previous = datetime.fromisoformat(prev_utc)
            current = datetime.fromisoformat(bar_utc)
            if current < previous:
                return {"status": "BAR_TIME_REGRESSION", "bar_utc": bar_utc}
            if current == previous:
                if prev_hash == bar_hash:
                    db.execute("UPDATE watch_state SET seen_utc=? WHERE symbol=? "
                               "AND timeframe=?", (observed_at, symbol, timeframe))
                    return {"status": "UNCHANGED", "bar_utc": bar_utc}
                db.execute(
                    "UPDATE watch_state SET bar_hash=?,seen_utc=? "
                    "WHERE symbol=? AND timeframe=?",
                    (bar_hash, observed_at, symbol, timeframe)
                )
                return {"status": "HISTORY_REVISED", "bar_utc": bar_utc}
            delta = (current - previous).total_seconds()
            db.execute(
                "UPDATE watch_state SET bar_utc=?,bar_hash=?,seen_utc=? "
                "WHERE symbol=? AND timeframe=?",
                (bar_utc, bar_hash, observed_at, symbol, timeframe)
            )
            if delta != step:
                return {"status": "CANDLE_GAP", "bar_utc": bar_utc,
                        "delta_seconds": delta}
            job_id = _fingerprint({"symbol": symbol, "timeframe": timeframe,
                                   "closed_bar_utc": bar_utc})
            db.execute("""INSERT OR IGNORE INTO watch_jobs (
                job_id,symbol,timeframe,bar_utc,snapshot_hash,status,created_utc
            ) VALUES (?,?,?,?,?,'PENDING',?)""",
                       (job_id, symbol, timeframe, bar_utc,
                        snapshot_hash, observed_at))
            return {"status": "NEW_COMPLETED_CANDLE", "bar_utc": bar_utc,
                    "job_id": job_id}

    def shadow_cycles(self, symbol: str, timeframe: str) -> int:
        """Persisted billable-cycle counter; never resets on watcher restart."""
        with self._db() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM watch_jobs WHERE symbol=? AND timeframe=? "
                "AND shadow_requested=1", (symbol, timeframe),
            ).fetchone()[0])

    def block_job(self, job_id: str) -> None:
        with self._db() as db:
            db.execute("UPDATE watch_jobs SET status='BLOCKED',completed_utc=? "
                       "WHERE job_id=? AND status='PENDING'",
                       (datetime.now(timezone.utc).isoformat(), job_id))

    def claim(self, job_id: str, expected_hash: str, *,
              shadow_requested: bool = False) -> bool:
        """Atomically ensure only one worker can make paid model calls."""
        with self._db() as db:
            cursor = db.execute(
                "UPDATE watch_jobs SET status='RUNNING',shadow_requested=? "
                "WHERE job_id=? AND snapshot_hash=? AND status='PENDING'",
                (int(shadow_requested), job_id, expected_hash),
            )
            return cursor.rowcount == 1

    def finish_job(self, job_id: str, state: str, run_id: str | None) -> None:
        if state not in ("COMPLETED", "FAILED", "REVIEW_REQUIRED", "BLOCKED"):
            raise WatchError("INVALID_JOB_STATE")
        with self._db() as db:
            cursor = db.execute(
                "UPDATE watch_jobs SET status=?,research_run_id=?,completed_utc=? "
                "WHERE job_id=? AND status='RUNNING'",
                (state, run_id, datetime.now(timezone.utc).isoformat(), job_id)
            )
            if cursor.rowcount != 1:
                raise WatchError("JOB_STATE_CONFLICT")

    def jobs(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT job_id,symbol,timeframe,bar_utc,status,research_run_id "
                "FROM watch_jobs ORDER BY created_utc,job_id"
            ).fetchall()
        return [dict(zip(("job_id", "symbol", "timeframe", "bar_utc",
                          "status", "research_run_id"), row)) for row in rows]


class MarketWatcher:
    """Single local watch process. It cannot produce executable trade orders."""

    def __init__(self, journal: EventJournal, adapter: ReadOnlyMT5,
                 symbol: str, timeframe: str, *, bars: int = 100,
                 client: Any = None, clock: Any = time.time,
                 knowledge_db: Path = DEFAULT_KNOWLEDGE,
                 identity_key: bytes | None = None,
                 enable_shadow_research: bool = False,
                 shadow_slippage: float | None = None,
                 shadow_fee_per_side: float | None = None,
                 shadow_max_cycles: int = 5):
        if timeframe not in INTERVALS or not 2 <= bars <= 2000:
            raise WatchError("INVALID_WATCH_CONFIG")
        self.journal = journal
        self.adapter = adapter
        self.symbol = symbol
        self.timeframe = timeframe
        self.bars = bars
        self.client = client
        if not isinstance(identity_key, bytes) or len(identity_key) != 32:
            raise WatchError("IDENTITY_KEY_REQUIRED")
        self.identity_key = identity_key
        self.clock = clock
        self.knowledge_db = Path(knowledge_db)
        if enable_shadow_research and client is None:
            raise WatchError("SHADOW_REQUIRES_LLM_OPT_IN")
        if enable_shadow_research and (
            not isinstance(shadow_max_cycles, int) or isinstance(shadow_max_cycles, bool)
            or not 1 <= shadow_max_cycles <= 50
        ):
            raise WatchError("INVALID_SHADOW_CYCLE_BUDGET")
        self.pipeline = (
            ResearchShadowPipeline(
                journal, client, identity_key, slippage=shadow_slippage,
                fee_per_side=shadow_fee_per_side,
            ) if enable_shadow_research else None
        )
        self.shadow_max_cycles = shadow_max_cycles
        self.store = WatchState(journal.path)
        self.owner = uuid.uuid4().hex
        self.run_id: str | None = None
        self.poll_count = 0

    def start(self) -> str:
        if self.run_id is not None:
            raise WatchError("WATCH_ALREADY_STARTED")
        now = float(self.clock())
        if not math.isfinite(now):
            raise WatchError("INVALID_WATCH_CLOCK")
        self.store.acquire(self.symbol, self.timeframe, self.owner, now=now)
        try:
            self.run_id = self.journal.start("monitor_market", mode="READ_ONLY")
            self.journal.append(
                self.run_id, "Supervisor", "WATCH_STARTED", "RUNNING",
                "Started opt-in local MT5 observation; no order execution is available.",
                {"symbol": self.symbol, "timeframe": self.timeframe, "bars": self.bars,
                 "llm_enabled": self.client is not None,
                 "shadow_enabled": self.pipeline is not None,
                 "shadow_cycle_budget": self.shadow_max_cycles if self.pipeline else 0,
                 "mode": "READ_ONLY", "execution": "DISABLED"},
            )
            return self.run_id
        except Exception:
            self.store.release(self.symbol, self.timeframe, self.owner)
            raise

    def poll_once(self) -> dict:
        if self.run_id is None:
            raise WatchError("WATCH_NOT_STARTED")
        now = float(self.clock())
        if not math.isfinite(now):
            raise WatchError("INVALID_WATCH_CLOCK")
        self.store.renew(self.symbol, self.timeframe, self.owner, now=now)
        self.poll_count += 1
        try:
            snapshot = self.adapter.capture(
                self.symbol, timeframe=self.timeframe, bars=self.bars
            )
            # Authenticate the local read-only adapter evidence first.
            # HMAC is not remote broker attestation or a trading permission.
            account_binding_id = verify_snapshot(snapshot, self.identity_key)
            public = market_context(snapshot)
            if public["symbol"] != self.symbol or public["timeframe"] != self.timeframe:
                raise WatchError("BROKER_SNAPSHOT_MISMATCH")
            account = snapshot.get("account")
            if not isinstance(account, dict):
                raise WatchError("BROKER_ACCOUNT_MISSING")
            account_mode = account.get("trade_mode", "UNKNOWN")
            if account_mode not in ("DEMO", "REAL", "CONTEST"):
                raise WatchError("INVALID_ACCOUNT_MODE")
            observation = self.store.observe(
                public, account_mode,
                observed_at=datetime.now(timezone.utc).isoformat(),
                account_binding_id=account_binding_id,
            )
        except (MT5ReadError, IdentityError, LLMError, WatchError) as exc:
            code = getattr(exc, "code", str(exc) if isinstance(exc, WatchError)
                           else "INVALID_MARKET_DATA")
            self.journal.append(
                self.run_id, "Market Watcher", "WATCH_DATA_BLOCKED", "BLOCKED",
                "Market observation unavailable or invalid. No trade was attempted.",
                {"code": code, "symbol": self.symbol, "timeframe": self.timeframe,
                 "execution": "DISABLED"},
            )
            return {"status": "BLOCKED", "reason": code}
        except Exception as exc:
            # Unanticipated bridge/data errors must not expose broker paths.
            self.journal.append(
                self.run_id, "Market Watcher", "WATCH_FATAL", "FAILED",
                "Unrecoverable watcher error. Read-only execution is halted.",
                {"error_class": type(exc).__name__, "execution": "DISABLED"},
            )
            raise WatchError("WATCH_INTERNAL_ERROR") from None

        status = observation["status"]
        if status != "UNCHANGED" or self.poll_count % 5 == 0:
            self.journal.append(
                self.run_id, "Market Watcher", "WATCH_" + status, (
                    "PASS" if status in ("BASELINED", "UNCHANGED",
                                        "NEW_COMPLETED_CANDLE") else "BLOCKED"
                ),
                "Observed completed MT5 candles; state and trigger checked.",
                {"symbol": self.symbol, "timeframe": self.timeframe, **observation,
                 "source": "READ_ONLY_SNAPSHOT", "local_hmac_verified": True,
                 "broker_execution": False},
            )

        if status == "NEW_COMPLETED_CANDLE":
            self.journal.append(
                self.run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
                "NO_TRADE: a read-only candle trigger cannot authorize execution.",
                {"action": "NO_TRADE", "vetoes": ["WATCH_READ_ONLY"],
                 "order_send_called": False},
            )
        if status == "NEW_COMPLETED_CANDLE" and self.pipeline is not None:
            # An existing prospective candidate is settled only from a
            # later, freshly HMAC-verified completed-candle observation.
            try:
                settled = self.pipeline.settle_due(snapshot)
                if settled is not None:
                    self.journal.append(
                        self.run_id, "Supervisor", "SHADOW_OUTCOME_LINKED", "INFO",
                        "Future observation linked to a previous hypothetical research episode.",
                        {"candidate_id": settled["episode"]["candidate_id"],
                         "shadow_run_id": settled["run_id"],
                         "research_run_id": settled["episode"]["research_run_id"],
                         "fill_type": "HYPOTHETICAL", "broker_execution": False},
                    )
            except (ShadowError, LLMError) as exc:
                self.store.block_job(observation["job_id"])
                code = str(exc) if isinstance(exc, ShadowError) else exc.code
                self.journal.append(
                    self.run_id, "Supervisor", "SHADOW_SETTLEMENT_BLOCKED", "BLOCKED",
                    "Prior prospective shadow candidate could not be settled safely.",
                    {"job_id": observation["job_id"], "code": code,
                     "decision": "NO_TRADE", "broker_execution": False},
                )
                return {**observation, "status": "SHADOW_SETTLEMENT_BLOCKED"}
            if self.store.shadow_cycles(self.symbol, self.timeframe) >= self.shadow_max_cycles:
                self.store.block_job(observation["job_id"])
                self.journal.append(
                    self.run_id, "Supervisor", "SHADOW_BUDGET_EXHAUSTED", "BLOCKED",
                    "Persistent opt-in shadow LLM cycle budget exhausted. No further model calls.",
                    {"max_cycles": self.shadow_max_cycles, "decision": "NO_TRADE",
                     "broker_execution": False},
                )
                return {**observation, "status": "SHADOW_BUDGET_EXHAUSTED"}

        if status == "NEW_COMPLETED_CANDLE" and self.client is not None:
            if not self.store.claim(observation["job_id"], public["snapshot_hash"],
                                    shadow_requested=self.pipeline is not None):
                return {**observation, "status": "RESEARCH_NOT_CLAIMED"}
            try:
                research_id = SignalResearch(
                    self.journal, self.client, knowledge_db=self.knowledge_db
                ).execute(snapshot)
                shadow_result = (
                    self.pipeline.propose(
                        snapshot, job_id=observation["job_id"],
                        research_run_id=research_id,
                        watcher_run_id=self.run_id,
                    ) if self.pipeline is not None else None
                )
                self.store.finish_job(observation["job_id"], "COMPLETED", research_id)
                self.journal.append(
                    self.run_id, "Supervisor", "RESEARCH_COMPLETE", "PASS",
                    "Research roles finished; independent trade veto remains active.",
                    {"job_id": observation["job_id"], "research_run_id": research_id,
                     "shadow_status": shadow_result["status"] if shadow_result else "DISABLED",
                     "candidate_id": shadow_result.get("candidate_id") if shadow_result else None,
                     "decision": "NO_TRADE", "execution": "DISABLED"},
                )
            except Exception as exc:
                code = exc.code if isinstance(exc, LLMError) else (
                    str(exc) if isinstance(exc, ShadowError) else "RESEARCH_FAILURE"
                )
                self.store.finish_job(observation["job_id"], "FAILED", None)
                self.journal.append(
                    self.run_id, "Supervisor", "RESEARCH_BLOCKED", "BLOCKED",
                    "Research failed; automatic retry disabled to avoid duplicate paid calls.",
                    {"job_id": observation["job_id"], "code": code,
                     "decision": "NO_TRADE", "execution": "DISABLED"},
                )
                return {**observation, "status": "RESEARCH_FAILED", "reason": code}
        return observation

    def stop(self, *, failed: bool = False) -> None:
        if self.run_id is None:
            return
        try:
            self.journal.append(
                self.run_id, "Supervisor",
                "WATCH_ABORTED" if failed else "WATCH_STOPPED",
                "FAILED" if failed else "INFO",
                "Read-only observer terminated; no live broker mutation occurred.",
                {"symbol": self.symbol, "timeframe": self.timeframe,
                 "execution": "DISABLED"},
            )
            self.journal.finish(self.run_id, "FAILED" if failed else "COMPLETED")
        finally:
            self.store.release(self.symbol, self.timeframe, self.owner)
            self.run_id = None

    def run(self, interval_seconds: int = 15, max_polls: int = 0,
            sleep: Any = time.sleep) -> dict:
        if not 5 <= interval_seconds <= 60 or not 0 <= max_polls <= 100000:
            raise WatchError("INVALID_POLL_BUDGET")
        count = 0
        self.start()
        try:
            while max_polls == 0 or count < max_polls:
                outcome = self.poll_once()
                count += 1
                if max_polls == 0 or count < max_polls:
                    sleep(interval_seconds)
        except KeyboardInterrupt:
            # An explicit operator stop is not a fatal watcher failure.
            self.stop()
            raise
        except BaseException:
            # Any unexpected fatal exit releases the single-watcher lease.
            self.stop(failed=True)
            raise
        else:
            self.stop()
            return {"polls": count, "last_status": outcome["status"],
                    "mode": "READ_ONLY", "execution": "DISABLED"}
