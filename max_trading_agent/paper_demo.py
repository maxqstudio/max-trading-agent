"""Paper-only DEMO recovery drill; zero MetaTrader5 dependencies or order APIs.

No real broker adapter is accepted. Durable intent precedes fake send.
Ambiguous fake outcomes are reconciled by READBACK, never blind resend.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .demo_preflight import PreflightError, check_demo_preflight
from .identity import IdentityError, verify_snapshot
from .runtime import EventJournal


class PaperError(ValueError):
    """Sanitized code: no broker or account values ever in exceptions."""


class InMemoryPaperBroker:
    """Deliberately fake, never reaches or imports MT5."""

    def __init__(self, behavior: str = "FILL"):
        if behavior not in ("FILL", "REJECT", "TIMEOUT_AFTER_FILL",
                            "TIMEOUT_BEFORE_RECEIPT"):
            raise PaperError("INVALID_FAKE_BEHAVIOR")
        self.behavior = behavior
        self.receipts: dict[str, dict] = {}
        self.calls: list[str] = []

    def simulate(self, request_id: str, symbol: str, side: str,
                 assumed_quote: dict) -> None:
        self.calls.append(request_id)
        if request_id in self.receipts:
            raise PaperError("DUPLICATE_FAKE_BROKER_SEND")
        if self.behavior == "TIMEOUT_BEFORE_RECEIPT":
            raise TimeoutError("paper synthetic connection missing")
        if self.behavior == "REJECT":
            self.receipts[request_id] = {"state": "REJECTED_SIMULATED"}
        else:
            # Model-only reference price; never a real broker fill.
            price = assumed_quote["ask"] if side == "LONG" else assumed_quote["bid"]
            self.receipts[request_id] = {
                "state": "FILLED_SIMULATED", "price": price,
                "symbol": symbol, "side": side,
            }
        if self.behavior == "TIMEOUT_AFTER_FILL":
            raise TimeoutError("paper acknowledgement lost")

    def observe(self, request_id: str) -> dict | None:
        return self.receipts.get(request_id)


class PaperLedger:
    """Local SQLite synthetic intents with atomic idempotency record."""

    def __init__(self, db_file: Path):
        self.path = Path(db_file)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS paper_intents(
                request_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
                identity_binding TEXT NOT NULL, symbol TEXT NOT NULL,
                side TEXT NOT NULL, state TEXT NOT NULL,
                created_utc TEXT NOT NULL
            )""")

    from contextlib import contextmanager
    @contextmanager
    def _db(self):
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

    @staticmethod
    def _digest(proposal: dict, limits: dict) -> str:
        return hashlib.sha256(json.dumps(
            {"proposal": proposal, "limits": limits},
            sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")).hexdigest()

    @staticmethod
    def _verify_account(snapshot: dict, key: bytes) -> str:
        try:
            binding = verify_snapshot(snapshot, key)
        except IdentityError:
            raise PaperError("PAPER_ACCOUNT_UNVERIFIED") from None
        account = snapshot.get("account")
        if not isinstance(account, dict) or account.get("trade_mode") != "DEMO":
            raise PaperError("PAPER_DEMO_ACCOUNT_REQUIRED")
        return binding

    def prepare(self, proposal: dict, limits: dict, snapshot: dict,
                key: bytes) -> tuple[str, bool]:
        binding = self._verify_account(snapshot, key)
        request_id = proposal["request_id"]
        digest = self._digest(proposal, limits)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute(
                "SELECT request_digest,identity_binding,state FROM paper_intents "
                "WHERE request_id=?", (request_id,)
            ).fetchone()
            if found:
                if found[0] != digest or found[1] != binding:
                    raise PaperError("PAPER_IDEMPOTENCY_CONFLICT")
                return found[2], True
            db.execute(
                "INSERT INTO paper_intents VALUES(?,?,?,?,?,?,?)",
                (request_id, digest, binding, proposal["symbol"], proposal["side"],
                 "PREPARED", datetime.now(timezone.utc).isoformat())
            )
        return "PREPARED", False

    def state(self, request_id: str) -> str:
        with self._db() as db:
            row = db.execute(
                "SELECT state FROM paper_intents WHERE request_id=?", (request_id,)
            ).fetchone()
        if not row:
            raise PaperError("PAPER_INTENT_NOT_FOUND")
        return row[0]

    def reconcile(self, request_id: str, fake: InMemoryPaperBroker,
                  snapshot: dict, key: bytes) -> str:
        if type(fake) is not InMemoryPaperBroker:
            raise PaperError("REAL_BROKER_INTERFACE_FORBIDDEN")
        binding = self._verify_account(snapshot, key)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT identity_binding,state,symbol,side FROM paper_intents "
                "WHERE request_id=?", (request_id,)
            ).fetchone()
            if not row:
                raise PaperError("PAPER_INTENT_NOT_FOUND")
            if row[0] != binding:
                raise PaperError("PAPER_ACCOUNT_CHANGED")
            if row[1] in ("RECONCILED_SIMULATED", "REJECTED_SIMULATED"):
                return row[1]
            record = fake.observe(request_id)
            if record is None:
                new_state = "UNCERTAIN"
            elif record.get("state") == "REJECTED_SIMULATED":
                new_state = "REJECTED_SIMULATED"
            elif (record.get("state") == "FILLED_SIMULATED"
                  and record.get("symbol") == row[2]
                  and record.get("side") == row[3]):
                new_state = "RECONCILED_SIMULATED"
            else:
                new_state = "UNCERTAIN"
            db.execute(
                "UPDATE paper_intents SET state=? WHERE request_id=?",
                (new_state, request_id)
            )
        return new_state


class PaperMission:
    """Visible journal of fake broker recovery. NEVER submits broker orders."""

    def __init__(self, journal: EventJournal, ledger: PaperLedger,
                 broker: InMemoryPaperBroker):
        if type(broker) is not InMemoryPaperBroker:
            raise PaperError("REAL_BROKER_INTERFACE_FORBIDDEN")
        self.journal = journal
        self.ledger = ledger
        self.fake = broker

    def execute(self, snapshot: dict, proposal: dict, limits: dict, key: bytes,
                *, now: datetime | None = None) -> dict:
        # The independent policy is non-LLM and is checked before simulation.
        try:
            policy = check_demo_preflight(snapshot, proposal, limits, key, now=now)
        except PreflightError:
            raise PaperError("PAPER_POLICY_INVALID") from None
        run_id = self.journal.start("paper_demo", mode="PAPER")
        self.journal.append(
            run_id, "Independent Policy Guard", "PAPER_PREFLIGHT", (
                "PASS" if policy["checks"] == "PASS_CHECKS" else "BLOCKED"
            ), "Synthetic DEMO policy assessment; real broker remains disconnected.",
            {"checks": policy["checks"], "vetoes": policy["vetoes"],
             "execution": "DISABLED"},
        )
        if policy["vetoes"]:
            self.journal.append(run_id, "Independent Policy Guard", "DECISION",
                                "BLOCKED", "NO_TRADE: policy vetoed paper candidate.",
                                {"action": "NO_TRADE", "order_send_called": False})
            self.journal.finish(run_id, "COMPLETED")
            return {"run_id": run_id, "status": "VETOED", "vetoes": policy["vetoes"],
                    "decision": "NO_TRADE", "broker_execution": False}

        try:
            previous, reused = self.ledger.prepare(proposal, limits, snapshot, key)
            self.journal.append(
                run_id, "Paper Orchestrator", "PAPER_INTENT", "PASS",
                "Persistent synthetic intent exists; no broker order request.",
                {"status": previous, "reused": reused, "execution": "DISABLED"},
            )
            if not reused:
                try:
                    self.fake.simulate(proposal["request_id"], proposal["symbol"],
                                       proposal["side"], snapshot["quote"])
                except TimeoutError:
                    # Deliberately no retry. Read back fake state instead.
                    pass
            state = self.ledger.reconcile(proposal["request_id"], self.fake,
                                          snapshot, key)
            self.journal.append(
                run_id, "Paper Auditor", "PAPER_RECONCILIATION",
                "PASS" if state == "RECONCILED_SIMULATED" else "BLOCKED",
                "Hypothetical fake broker readback. Not a broker fill.",
                {"status": state, "reused": reused, "broker_execution": False},
            )
            self.journal.append(
                run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
                "NO_TRADE: synthetic outcome confers no DEMO/LIVE authority.",
                {"action": "NO_TRADE", "order_send_called": False},
            )
            self.journal.finish(run_id, "COMPLETED")
        except PaperError:
            self.journal.append(
                run_id, "Paper Auditor", "PAPER_RECONCILIATION", "FAILED",
                "Synthetic intent rejected; no broker order was sent.",
                {"status": "BLOCKED", "broker_execution": False},
            )
            self.journal.append(
                run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
                "NO_TRADE: paper trial failed a deterministic safety check.",
                {"action": "NO_TRADE", "order_send_called": False},
            )
            self.journal.finish(run_id, "FAILED")
            raise
        return {"run_id": run_id, "status": state,
                "decision": "NO_TRADE", "broker_execution": False,
                "owner_acceptance": "PENDING", "reused_intent": reused}
