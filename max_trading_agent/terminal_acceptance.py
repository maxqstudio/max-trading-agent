"""Owner-initiated MT5 read-only acceptance evidence, NOT broker attestation.

The check reads a bounded number of signed snapshots from a configured local
MT5 terminal. It never calls any order, login, symbol-select, LLM, or market
mutation API. A successful consistency check is still NOT live acceptance.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .identity import IdentityError, verify_snapshot
from .llm_client import LLMError
from .mt5_readonly import MT5ReadError
from .runtime import EventJournal
from .signal_research import market_context

_INTERVALS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800,
              "H1": 3600, "H4": 14400, "D1": 86400}
_SPEC_FIELDS = ("point", "trade_tick_size", "trade_contract_size", "volume_step")


class AcceptanceError(ValueError):
    """Sanitized acceptance-contract error with no private broker contents."""


def _time(raw: str) -> datetime:
    try:
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("timezone missing")
        return result.astimezone(timezone.utc)
    except (AttributeError, ValueError, OverflowError):
        raise AcceptanceError("INVALID_OBSERVATION_TIME") from None


def _positive(value: Any) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(float(value)) and value > 0)


class TerminalAcceptance:
    """Read-only, broker-account-bound feed diagnostics for future owner review."""

    def __init__(self, journal: EventJournal, adapter: Any, identity_key: bytes,
                 *, clock: Callable[[], datetime] | None = None):
        if not isinstance(identity_key, bytes) or len(identity_key) != 32:
            raise AcceptanceError("IDENTITY_KEY_REQUIRED")
        self.journal = journal
        self.adapter = adapter
        self.key = identity_key
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def execute(self, symbol: str, *, timeframe: str = "M15", bars: int = 100,
                samples: int = 3, sample_interval: int = 2,
                sleep: Callable[[float], None] = time.sleep) -> dict:
        if (not isinstance(samples, int) or isinstance(samples, bool)
                or not 2 <= samples <= 10
                or not isinstance(sample_interval, int)
                or isinstance(sample_interval, bool)
                or not 1 <= sample_interval <= 30
                or not isinstance(bars, int) or isinstance(bars, bool)
                or not 2 <= bars <= 2000 or timeframe not in _INTERVALS):
            raise AcceptanceError("INVALID_ACCEPTANCE_BUDGET")
        run_id = self.journal.start("accept_mt5_terminal", mode="READ_ONLY")
        report = {
            "run_id": run_id, "mode": "READ_ONLY",
            "status": "INCONCLUSIVE", "owner_acceptance": "PENDING",
            "broker_execution": False, "decision": "NO_TRADE",
            "symbol": symbol, "timeframe": timeframe,
            "samples_requested": samples, "samples_verified": 0,
            "new_quote_observed": False,
            "warnings": [], "block_reason": None,
            "min_quote_age_seconds": None,
            "max_quote_age_seconds": None,
            "min_spread_price_units": None,
            "max_spread_price_units": None,
        }
        self.journal.append(
            run_id, "Supervisor", "ACCEPTANCE_STARTED", "RUNNING",
            "Owner-initiated, read-only terminal feed consistency review.",
            {"symbol": symbol, "timeframe": timeframe, "samples": samples,
             "interval_seconds": sample_interval, "execution": "DISABLED"},
        )
        binding = previous_quote = previous_bar = previous_capture = None
        previous_mode = previous_candle_history = None
        warnings: set[str] = set()
        try:
            for index in range(samples):
                if index:
                    sleep(sample_interval)
                snapshot = self.adapter.capture(
                    symbol, timeframe=timeframe, bars=bars,
                )
                # Verify the local MAC BEFORE persisting ANY snapshot-derived
                # evidence; never write the HMAC account ID or raw snapshot.
                identity = verify_snapshot(snapshot, self.key)
                public = market_context(snapshot)
                if public["symbol"] != symbol or public["timeframe"] != timeframe:
                    raise AcceptanceError("MARKET_IDENTITY_CHANGED")
                if binding is not None and identity != binding:
                    raise AcceptanceError("ACCOUNT_ID_CHANGED")
                binding = identity
                account = snapshot.get("account")
                terminal = snapshot.get("terminal")
                if not isinstance(account, dict) or account.get("trade_mode") not in (
                    "DEMO", "REAL", "CONTEST"
                ):
                    raise AcceptanceError("UNKNOWN_ACCOUNT_MODE")
                if previous_mode is not None and account["trade_mode"] != previous_mode:
                    raise AcceptanceError("ACCOUNT_MODE_CHANGED")
                if not isinstance(terminal, dict) or terminal.get("connected") is not True:
                    raise AcceptanceError("TERMINAL_DISCONNECTED")
                spec = snapshot.get("symbol")
                if not isinstance(spec, dict):
                    raise AcceptanceError("BROKER_SYMBOL_SPEC_MISSING")
                missing = [field for field in _SPEC_FIELDS if not _positive(spec.get(field))]
                if missing:
                    warnings.add("INCOMPLETE_BROKER_SYMBOL_SPEC")

                captured = _time(public["snapshot_time_utc"])
                quoted = _time(public["quote"]["time_utc"])
                latest = public["completed_candles"][-1]
                completed = _time(latest["time_utc"])
                interval = _INTERVALS[timeframe]
                quote_age = (captured - quoted).total_seconds()

                # A signed but replayed market snapshot is not a fresh feed.
                # Validate age against an independent operator clock, not
                # solely the capture/quote timestamps supplied by the adapter.
                now = self.clock()
                if not isinstance(now, datetime) or now.tzinfo is None:
                    raise AcceptanceError("INVALID_ACCEPTANCE_CLOCK")
                now = now.astimezone(timezone.utc)
                if not -2 <= (now - captured).total_seconds() <= 30:
                    raise AcceptanceError("CAPTURE_NOT_FRESH")
                if (not -2 <= quote_age <= 30
                        or not -2 <= (now - quoted).total_seconds() <= 30):
                    raise AcceptanceError("QUOTE_NOT_FRESH")
                if (captured - completed).total_seconds() > 3 * interval:
                    warnings.add("RECENT_COMPLETED_BAR_MISSING")

                # market_context deliberately shares at most 40 latest bars
                # with LLM research. M09 must independently validate all
                # requested, locally MAC-verified historical candles.
                candles = snapshot["candles"]
                if len(candles) != bars:
                    warnings.add("INCOMPLETE_CANDLE_HISTORY")
                full_history = []
                for candle in candles:
                    if not isinstance(candle, dict):
                        raise AcceptanceError("INVALID_HISTORICAL_CANDLE")
                    stamp = _time(candle.get("time_utc"))
                    values = tuple(candle.get(field) for field in
                                   ("open", "high", "low", "close"))
                    if (not all(_positive(value) for value in values)
                            or values[2] > min(values[0], values[3])
                            or values[1] < max(values[0], values[3])):
                        raise AcceptanceError("INVALID_HISTORICAL_CANDLE")
                    full_history.append((stamp, values))
                if any(right[0] <= left[0] for left, right in
                       zip(full_history, full_history[1:])):
                    raise AcceptanceError("INVALID_HISTORICAL_CANDLE")
                current_history = {stamp.isoformat(): values
                                   for stamp, values in full_history}
                if any((right[0] - left[0]).total_seconds() != interval
                       for left, right in zip(full_history, full_history[1:])):
                    warnings.add("HISTORICAL_CANDLE_GAP")

                if previous_capture is not None and captured < previous_capture:
                    raise AcceptanceError("CAPTURE_TIME_REGRESSION")
                if previous_quote is not None:
                    if quoted < previous_quote:
                        raise AcceptanceError("QUOTE_TIME_REGRESSION")
                    if quoted > previous_quote:
                        report["new_quote_observed"] = True
                    elif (public["quote"]["bid"] != previous_price[0]
                          or public["quote"]["ask"] != previous_price[1]):
                        raise AcceptanceError("QUOTE_CHANGED_WITHOUT_NEW_TIMESTAMP")
                if previous_bar is not None and completed < previous_bar:
                    raise AcceptanceError("COMPLETED_BAR_REGRESSION")
                if previous_bar is not None and completed == previous_bar and (
                    latest != previous_last_bar
                ):
                    raise AcceptanceError("COMPLETED_BAR_REVISED")
                if previous_candle_history is not None and any(
                    prior != current_history[stamp]
                    for stamp, prior in previous_candle_history.items()
                    if stamp in current_history
                ):
                    raise AcceptanceError("HISTORICAL_CANDLE_REVISED")
                if previous_bar is not None and completed > previous_bar and (
                    completed - previous_bar
                ).total_seconds() != interval:
                    warnings.add("MISSED_COMPLETED_BAR")

                previous_capture = captured
                previous_mode = account["trade_mode"]
                previous_candle_history = current_history
                previous_quote = quoted
                previous_bar = completed
                previous_last_bar = latest
                previous_price = (public["quote"]["bid"], public["quote"]["ask"])
                spread = public["quote"]["ask"] - public["quote"]["bid"]
                for metric, value, places in (
                    ("quote_age_seconds", quote_age, 3),
                    ("spread_price_units", spread, 12),
                ):
                    low, high = "min_" + metric, "max_" + metric
                    report[low] = round(min(
                        value, report[low] if report[low] is not None else value
                    ), places)
                    report[high] = round(max(
                        value, report[high] if report[high] is not None else value
                    ), places)
                report["samples_verified"] += 1
                self.journal.append(
                    run_id, "MT5 Specialist", "ACCEPTANCE_SAMPLE", "PASS",
                    "Locally authenticated read-only market sample checked.",
                    {"sample": index + 1, "symbol": symbol, "timeframe": timeframe,
                     "market_snapshot_hash": public["snapshot_hash"],
                     "quote_age_seconds": round(quote_age, 3),
                     "spread_price_units": round(spread, 12),
                     "broker_execution": False},
                )

            if not report["new_quote_observed"]:
                warnings.add("NO_NEW_TICK_BETWEEN_SAMPLES")
            report["warnings"] = sorted(warnings)
            report["status"] = ("OBSERVED_CONSISTENT" if not warnings
                                else "INCONCLUSIVE")
            self.journal.append(
                run_id, "Market Data Auditor", "ACCEPTANCE_REPORT", (
                    "PASS" if not warnings else "BLOCKED"
                ),
                "Bounded read-only feed review completed; real broker owner approval pending.",
                {"status": report["status"],
                 "warnings": report["warnings"],
                 "samples_verified": report["samples_verified"],
                 "new_quote_observed": report["new_quote_observed"],
                 "owner_acceptance": "PENDING",
                 "order_authority": False},
            )
            self.journal.append(
                run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
                "NO_TRADE: feed consistency is not a trading authorization.",
                {"action": "NO_TRADE", "vetoes": ["READ_ONLY_ACCEPTANCE_ONLY"],
                 "order_send_called": False},
            )
            self.journal.finish(run_id, "COMPLETED")
        except (Exception, KeyboardInterrupt) as exc:
            if isinstance(exc, KeyboardInterrupt):
                reason = "OPERATOR_INTERRUPTED"
            elif isinstance(exc, (AcceptanceError, IdentityError, MT5ReadError, LLMError)):
                reason = getattr(exc, "code", str(exc))
            else:
                reason = "ACCEPTANCE_UNEXPECTED_ERROR"
            # Known codes only; never persist exception paths, account
            # identifiers, broker server names or raw terminal output.
            if not isinstance(reason, str) or len(reason) > 80 or not reason.replace(
                "_", ""
            ).isalnum() or reason.upper() != reason:
                reason = "ACCEPTANCE_UNEXPECTED_ERROR"
            report["status"] = "BLOCKED"
            report["block_reason"] = reason
            report["warnings"] = sorted(warnings)
            self.journal.append(
                run_id, "Market Data Auditor", "ACCEPTANCE_BLOCKED", "FAILED",
                "Read-only terminal acceptance evidence failed verification.",
                {"reason": reason, "samples_verified": report["samples_verified"],
                 "broker_execution": False},
            )
            self.journal.append(
                run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
                "NO_TRADE: terminal evidence is incomplete or invalid.",
                {"action": "NO_TRADE", "vetoes": [reason],
                 "order_send_called": False},
            )
            self.journal.finish(run_id, "FAILED")
        return report
