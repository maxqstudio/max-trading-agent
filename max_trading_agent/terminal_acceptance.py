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

    def __init__(self, journal: EventJournal, adapter: Any, identity_key: bytes):
        if not isinstance(identity_key, bytes) or len(identity_key) != 32:
            raise AcceptanceError("IDENTITY_KEY_REQUIRED")
        self.journal = journal
        self.adapter = adapter
        self.key = identity_key

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
            "max_quote_age_seconds": None,
            "max_spread_price_units": None,
        }
        self.journal.append(
            run_id, "Supervisor", "ACCEPTANCE_STARTED", "RUNNING",
            "Owner-initiated, read-only terminal feed consistency review.",
            {"symbol": symbol, "timeframe": timeframe, "samples": samples,
             "interval_seconds": sample_interval, "execution": "DISABLED"},
        )
        binding = previous_quote = previous_bar = previous_capture = None
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
                if not -2 <= quote_age <= 30:
                    raise AcceptanceError("QUOTE_NOT_FRESH")
                if (captured - completed).total_seconds() > 3 * interval:
                    warnings.add("RECENT_COMPLETED_BAR_MISSING")
                candles = public["completed_candles"]
                if any(
                    (_time(right["time_utc"]) - _time(left["time_utc"])).total_seconds()
                    != interval for left, right in zip(candles, candles[1:])
                ):
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
                if previous_bar is not None and completed > previous_bar and (
                    completed - previous_bar
                ).total_seconds() != interval:
                    warnings.add("MISSED_COMPLETED_BAR")

                previous_capture = captured
                previous_quote = quoted
                previous_bar = completed
                previous_last_bar = latest
                previous_price = (public["quote"]["bid"], public["quote"]["ask"])
                spread = public["quote"]["ask"] - public["quote"]["bid"]
                report["max_quote_age_seconds"] = round(max(
                    quote_age, report["max_quote_age_seconds"]
                    if report["max_quote_age_seconds"] is not None else quote_age
                ), 3)
                report["max_spread_price_units"] = round(max(
                    spread, report["max_spread_price_units"]
                    if report["max_spread_price_units"] is not None else spread
                ), 12)
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
        except Exception as exc:
            if isinstance(exc, (AcceptanceError, IdentityError, MT5ReadError, LLMError)):
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
