"""Bounded LLM research hypothesis → prospective SHADOW → later quote outcome.

No brokerage mutations; even LONG/SHORT are hypothetical research intentions.
All input market observations must pass M06's local HMAC verification.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from typing import Any

from .identity import IdentityError, verify_snapshot
from .runtime import EventJournal
from .shadow import ShadowError, ShadowLedger
from .shadow_mission import ShadowMission
from .signal_research import market_context


def _proposal(raw: str) -> dict[str, str]:
    if not isinstance(raw, str) or not 1 <= len(raw) <= 1500:
        raise ShadowError("HYPOTHESIS_SCHEMA_INVALID")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise ShadowError("HYPOTHESIS_SCHEMA_INVALID") from None
    if not isinstance(value, dict) or set(value) != {"action", "thesis", "falsifier"}:
        raise ShadowError("HYPOTHESIS_SCHEMA_INVALID")
    if value["action"] not in ("LONG", "SHORT", "ABSTAIN"):
        raise ShadowError("HYPOTHESIS_SCHEMA_INVALID")
    for field in ("thesis", "falsifier"):
        if not isinstance(value[field], str) or not 1 <= len(value[field].strip()) <= 350:
            raise ShadowError("HYPOTHESIS_SCHEMA_INVALID")
    return {k: v.strip() for k, v in value.items()}


class ResearchShadowPipeline:
    """Only instantiated by the owner's explicitly opted-in local M05 watcher."""

    def __init__(self, journal: EventJournal, client: Any,
                 identity_key: bytes, *, slippage: float, fee_per_side: float):
        self.journal = journal
        self.client = client
        self.identity_key = identity_key
        self.ledger = ShadowLedger(journal.path, identity_key=identity_key)
        self.mission = ShadowMission(journal, self.ledger)
        # Validate once; the ledger performs independent checks again per entry.
        from .shadow import _nonnegative
        self.slippage = _nonnegative(slippage, "SLIPPAGE")
        self.fee_per_side = _nonnegative(fee_per_side, "FEE")

    def _identity(self, snapshot: dict) -> str:
        try:
            return verify_snapshot(snapshot, self.identity_key)
        except IdentityError:
            raise ShadowError("SHADOW_IDENTITY_UNVERIFIED") from None

    def settle_due(self, snapshot: dict) -> dict | None:
        """Invoked only for a freshly completed *later* candle after M06 checks."""
        binding = self._identity(snapshot)
        context = market_context(snapshot)
        episodes = self.ledger.pending_for(context["symbol"], binding)
        if len(episodes) > 1:
            raise ShadowError("MULTIPLE_PENDING_CANDIDATES")
        if not episodes:
            return None
        episode = episodes[0]
        # A signed later quote is not enough for the one-candle horizon. A
        # skipped bar cannot be silently filled on a subsequent trigger.
        with closing(sqlite3.connect(self.journal.path)) as db:
            origin = db.execute(
                "SELECT symbol,timeframe,bar_utc,status FROM watch_jobs "
                "WHERE job_id=?", (episode["watch_job_id"],)
            ).fetchone()
        if (origin is None or origin[0] != context["symbol"]
                or origin[1] != context["timeframe"] or origin[3] != "COMPLETED"):
            raise ShadowError("SHADOW_ORIGIN_UNVERIFIED")
        if context["completed_candles"][-2]["time_utc"] != origin[2]:
            raise ShadowError("SHADOW_HORIZON_EXCEEDED")
        # The ledger separately enforces later quote time and same account.
        return self.mission.settle(episode["candidate_id"], snapshot)

    def propose(self, snapshot: dict, *, job_id: str, research_run_id: str,
                watcher_run_id: str) -> dict:
        """Invoke one extra LLM role, parse strict schema, never an MT5 order."""
        binding = self._identity(snapshot)
        context = market_context(snapshot)
        if not re.fullmatch(r"[0-9a-f]{64}", job_id):
            raise ShadowError("INVALID_WATCH_JOB")
        if not re.fullmatch(r"[0-9a-f]{32}", research_run_id):
            raise ShadowError("INVALID_RESEARCH_RUN")
        if not re.fullmatch(r"[0-9a-f]{32}", watcher_run_id):
            raise ShadowError("INVALID_WATCHER_RUN")
        events = self.journal.events(research_run_id, limit=200)
        initial = next((event for event in events
                        if event["event_type"] == "SNAPSHOT_VALIDATED"), None)
        final = next((event for event in events
                      if event["event_type"] == "REPORT_READY"), None)
        veto = next((event for event in events
                     if event["event_type"] == "DECISION"
                     and event["role"] == "Independent Policy Guard"), None)
        if (not initial or initial["details"].get("snapshot_hash") != context["snapshot_hash"]
                or not final or not veto
                or veto["details"].get("action") != "NO_TRADE"):
            raise ShadowError("RESEARCH_LINEAGE_MISMATCH")
        report = final["details"].get("report")
        if not isinstance(report, str):
            raise ShadowError("INVALID_RESEARCH_REPORT")
        previous = self.ledger.pending_for(context["symbol"], binding)
        if previous:
            raise ShadowError("PREVIOUS_SHADOW_NOT_SETTLED")

        self.journal.append(
            watcher_run_id, "Hypothesis Strategist", "HYPOTHESIS_ROLE_STARTED", "RUNNING",
            "Asking a model for a non-executable, one-candle shadow hypothesis.",
            {"watch_job_id": job_id, "research_run_id": research_run_id,
             "order_authority": False,
             "evidence_grade": "UNVERIFIED_LLM_OPINION"},
        )
        prompt = json.dumps({
            "task": "Return only raw JSON, exactly keys action,thesis,falsifier. "
                    "action must be LONG, SHORT or ABSTAIN. No order instructions, "
                    "profit projections, fake statistics or executable tool calls. "
                    "Prefer ABSTAIN when evidence is weak. One completed-candle horizon.",
            "market": {**context,
                       "completed_candles": context["completed_candles"][-12:]},
            "research_manager_report": report[:3000],
            "validated_strategy_results": [],
            "execution": "DISABLED",
        }, separators=(",", ":"), allow_nan=False)
        try:
            proposal = _proposal(self.client.complete("Hypothesis Strategist", prompt))
        except Exception:
            # A model/provider failure cannot silently become a shadow entry.
            self.journal.append(
                watcher_run_id, "Hypothesis Strategist", "HYPOTHESIS_REJECTED", "BLOCKED",
                "Hypothesis schema or provider failed; no shadow candidate admitted.",
                {"reason": "INVALID_HYPOTHESIS",
                 "research_run_id": research_run_id, "watch_job_id": job_id,
                 "broker_execution": False},
            )
            raise ShadowError("HYPOTHESIS_REJECTED") from None

        self.journal.append(
            watcher_run_id, "Hypothesis Strategist", (
                "HYPOTHESIS_ABSTAINED" if proposal["action"] == "ABSTAIN"
                else "HYPOTHESIS_PROPOSED"
            ), "INFO",
            "Model hypothesis recorded for research only; not a trade approval.",
            {"action": proposal["action"], "thesis": proposal["thesis"],
             "falsifier": proposal["falsifier"], "watch_job_id": job_id,
             "research_run_id": research_run_id,
             "broker_execution": False, "evidence_grade": "UNVERIFIED_LLM_OPINION"},
        )
        if proposal["action"] == "ABSTAIN":
            return {"status": "ABSTAIN", "research_run_id": research_run_id,
                    "execution": "DISABLED"}

        candidate_id = "watch_" + job_id
        # M04 independently checks freshness. If LLM took too long, reject.
        entry = self.mission.open(
            candidate_id, snapshot, side=proposal["action"],
            slippage=self.slippage, fee_per_side=self.fee_per_side,
            origin="LLM_RESEARCH", research_run_id=research_run_id,
            watch_job_id=job_id,
        )
        return {"status": "SHADOW_OPENED", "candidate_id": candidate_id,
                "research_run_id": research_run_id,
                "shadow_run_id": entry["run_id"], "execution": "DISABLED"}
