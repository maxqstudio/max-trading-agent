"""Logged, bounded TradingAgents-style LLM research; trading remains disabled."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .core import build_index, search
from .llm_client import LLMError, ROLE_INSTRUCTIONS
from .runtime import DEFAULT_KNOWLEDGE, EventJournal

ROLE_SEQUENCE = ("Market Analyst", "Bull Researcher", "Bear Researcher", "Research Manager")


def _positive_market_price(value: Any, error: str) -> float:
    # JSON booleans are int subclasses; huge integers can overflow float().
    if type(value) not in (int, float):
        raise LLMError(error)
    try:
        price = float(value)
    except (OverflowError, TypeError, ValueError):
        raise LLMError(error) from None
    if not math.isfinite(price) or price <= 0:
        raise LLMError(error)
    return price


def _parse_utc(raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Naive time")
        return dt.astimezone(timezone.utc)
    except (ValueError, AttributeError, TypeError):
        raise LLMError("INVALID_SNAPSHOT_TIME") from None


def market_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Select only market fields. Never send private broker account/positions to an LLM."""
    if not isinstance(snapshot, dict) or (
        snapshot.get("source") != "MT5_LOCAL_TERMINAL"
        or snapshot.get("mode") != "READ_ONLY"
        or snapshot.get("execution") != "DISABLED"
    ):
        raise LLMError("UNTRUSTED_OR_UNSUPPORTED_SNAPSHOT")

    symbol = snapshot.get("symbol")
    quote = snapshot.get("quote")
    candles = snapshot.get("candles")
    if not isinstance(symbol, dict) or not isinstance(quote, dict) or (
        not isinstance(candles, list) or not 2 <= len(candles) <= 2000
    ):
        raise LLMError("INCOMPLETE_MARKET_SNAPSHOT")
    name = symbol.get("name")
    timeframe = snapshot.get("timeframe")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.#-]{0,39}", name) or (
        timeframe not in {"M1", "M5", "M15", "M30", "H1", "H4", "D1"}
    ):
        raise LLMError("INVALID_MARKET_IDENTITY")

    captured = _parse_utc(snapshot.get("captured_at_utc"))
    quoted = _parse_utc(quote.get("time_utc"))
    now = datetime.now(timezone.utc)
    if (captured - now).total_seconds() > 120 or (now - captured).total_seconds() > 900:
        raise LLMError("SNAPSHOT_TOO_OLD")
    if not -2 <= (captured - quoted).total_seconds() <= 90:
        raise LLMError("STALE_SNAPSHOT_QUOTE")

    bid = _positive_market_price(quote.get("bid"), "INVALID_QUOTE")
    ask = _positive_market_price(quote.get("ask"), "INVALID_QUOTE")
    if bid >= ask:
        raise LLMError("INVALID_QUOTE")

    latest = candles[-min(40, len(candles)):]
    seconds = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800,
               "H1": 3600, "H4": 14400, "D1": 86400}[timeframe]
    sanitized = []
    previous_time = None
    for candle in latest:
        if not isinstance(candle, dict):
            raise LLMError("INVALID_CANDLE")
        timestamp = _parse_utc(candle.get("time_utc"))
        if (timestamp + timedelta(seconds=seconds) > captured + timedelta(seconds=2)
                or (previous_time is not None and timestamp <= previous_time)):
            raise LLMError("UNFINISHED_OR_UNORDERED_CANDLE")
        previous_time = timestamp
        values = {}
        for key in ("open", "high", "low", "close"):
            values[key] = _positive_market_price(candle.get(key), "INVALID_CANDLE")
        if values["low"] > min(values["open"], values["close"]) or (
            values["high"] < max(values["open"], values["close"])
        ):
            raise LLMError("INVALID_CANDLE")
        sanitized.append({"time_utc": timestamp.isoformat(), **values})

    public = {
        "snapshot_time_utc": captured.isoformat(),
        "symbol": name,
        "timeframe": timeframe,
        "quote": {"bid": bid, "ask": ask,
                  "time_utc": quoted.isoformat()},
        "completed_candles": sanitized,
    }
    public["snapshot_hash"] = hashlib.sha256(
        json.dumps(public, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return public


class SignalResearch:
    """Multi-role LLM opinions, never order submissions or validated signals."""

    def __init__(self, journal: EventJournal, client: Any,
                 knowledge_db: Path = DEFAULT_KNOWLEDGE):
        self.journal = journal
        self.client = client
        self.knowledge_db = Path(knowledge_db)

    def execute(self, snapshot: dict[str, Any]) -> str:
        run_id = self.journal.start("research_signal", mode="SIGNAL")
        try:
            public = market_context(snapshot)
            self.journal.append(
                run_id, "Supervisor", "SNAPSHOT_VALIDATED", "PASS",
                "Validated read-only market input and removed private account information.",
                {"snapshot_hash": public["snapshot_hash"], "symbol": public["symbol"],
                 "timeframe": public["timeframe"], "candle_count": len(public["completed_candles"]),
                 "source": "DECLARED_MT5_LOCAL_TERMINAL", "execution": "DISABLED"},
            )
            # The grounded knowledge here concerns MT5 API operations only.
            # No strategy cards or performance evidence are silently invented.
            count = build_index(self.knowledge_db)
            refs = search(self.knowledge_db, "symbol_info_tick copy_rates_from_pos terminal_info",
                          limit=6)
            if not refs:
                raise LLMError("KNOWLEDGE_UNAVAILABLE")
            self.journal.append(
                run_id, "RAG Curator", "RAG_RETRIEVAL", "PASS",
                "Retrieved versioned MT5 tool references; strategy edge remains UNKNOWN.",
                {"tools_executed": ["build_index", "search"],
                 "documents_indexed": count,
                 "references": refs,
                 "strategy_backtest_evidence": 0},
            )

            reports = {}
            for role in ROLE_SEQUENCE:
                self.journal.append(
                    run_id, role, "ROLE_STARTED", "RUNNING",
                    "Invoking configured LLM for a research-only role.",
                    {"model": getattr(self.client, "model", "UNSPECIFIED"),
                     "order_authority": False},
                )
                context = {
                    "instructions": ROLE_INSTRUCTIONS[role],
                    "market": public,
                    "mt5_tool_references": refs,
                    "previous_reports": reports,
                    "validated_strategy_experiments": [],
                    "execution": "DISABLED",
                }
                prompt = json.dumps(context, separators=(",", ":"), allow_nan=False)
                response = self.client.complete(role, prompt)
                if not isinstance(response, str) or not response.strip():
                    raise LLMError("EMPTY_ROLE_REPORT")
                reports[role] = response[:4500]
                self.journal.append(
                    run_id, role, "ROLE_REPORT", "PASS",
                    "LLM research report recorded; claims are not independent evidence.",
                    {"report": reports[role], "evidence_grade": "UNVERIFIED_LLM_OPINION"},
                )

            self.journal.append(
                run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
                "NO_TRADE: research reports are not verified trade evidence.",
                {"action": "NO_TRADE", "vetoes": ["NO_VALIDATED_STRATEGY_EXPERIMENT",
                                                   "SIGNAL_RESEARCH_ONLY"],
                 "order_send_called": False},
            )
            self.journal.append(
                run_id, "Supervisor", "REPORT_READY", "PASS",
                "Research roles completed; report available for review.",
                {"report": reports["Research Manager"],
                 "decision": "NO_TRADE", "is_trading_signal": False},
            )
            self.journal.finish(run_id, "COMPLETED")
        except Exception as exc:
            # The failure log never contains raw API errors or tokens.
            code = exc.code if isinstance(exc, LLMError) else "RESEARCH_INTERNAL_ERROR"
            self.journal.append(
                run_id, "Supervisor", "RESEARCH_FAILED", "FAILED",
                "Research halted safely. No trading decision was executed.",
                {"error_code": code},
            )
            self.journal.append(
                run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
                "NO_TRADE: research run failed.",
                {"action": "NO_TRADE", "vetoes": [code], "order_send_called": False},
            )
            self.journal.finish(run_id, "FAILED")
            if isinstance(exc, LLMError):
                raise
            raise LLMError("RESEARCH_INTERNAL_ERROR") from None
        return run_id
