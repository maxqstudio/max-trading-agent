"""Persist a real MT5 read-only probe as auditable, redacted agent events.

Only an explicitly invoked local command may start this mission. The UI
queries the same journal but has no endpoint to initiate a real broker probe.
"""
from __future__ import annotations

from typing import Any

from .mt5_readonly import MT5ReadError, ReadOnlyMT5
from .runtime import EventJournal


class ProbeMission:
    def __init__(self, journal: EventJournal, adapter: ReadOnlyMT5):
        self.journal = journal
        self.adapter = adapter

    def execute(self, symbol: str, *, timeframe: str = "M15",
                bars: int = 100) -> tuple[str, dict[str, Any]]:
        run_id = self.journal.start("observe_mt5", mode="READ_ONLY")
        self.journal.append(
            run_id, "Supervisor", "PROBE_STARTED", "RUNNING",
            "Opt-in, local MT5 read-only snapshot requested.",
            {"symbol": symbol, "timeframe": timeframe, "bars_requested": bars,
             "execution": "DISABLED"},
        )
        try:
            result = self.adapter.capture(symbol, timeframe=timeframe, bars=bars)
            # Account numbers, broker IDs, server, paths, orders and deals are
            # NEVER written into this event journal; full result stays in CLI.
            self.journal.append(
                run_id, "MT5 Specialist", "BROKER_SNAPSHOT", "PASS",
                "Read-only MT5 broker and completed-bar snapshot captured.",
                {
                    "symbol": result["symbol"]["name"],
                    "timeframe": result["timeframe"],
                    "captured_at_utc": result["captured_at_utc"],
                    "quote_at_utc": result["quote"]["time_utc"],
                    "quote_age_seconds": result["quote"]["age_seconds"],
                    "account_mode": result["account"]["trade_mode"],
                    "candle_count": len(result["candles"]),
                    "position_count": len(result["positions"]),
                    "order_count": len(result["orders"]),
                    "source": "MT5_LOCAL_TERMINAL",
                    "execution": "DISABLED",
                },
            )
            self.journal.append(
                run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
                "NO_TRADE: market observed, but this read-only probe is not a trading strategy.",
                {"action": "NO_TRADE", "vetoes": ["READ_ONLY_MODE",
                                                    "STRATEGY_NOT_EVALUATED"],
                 "order_send_called": False},
            )
            self.journal.finish(run_id, "COMPLETED")
            return run_id, result
        except MT5ReadError as exc:
            self.journal.append(
                run_id, "MT5 Specialist", "PROBE_FAILED", "FAILED",
                "MT5 probe failed safely without submitting an order.",
                {"error": exc.as_dict()},
            )
            self.journal.append(
                run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
                "NO_TRADE: required MT5 market observation failed.",
                {"action": "NO_TRADE", "vetoes": [exc.code],
                 "order_send_called": False},
            )
            self.journal.finish(run_id, "FAILED")
            raise
        except Exception as exc:
            self.journal.append(
                run_id, "MT5 Specialist", "PROBE_FAILED", "FAILED",
                "Unexpected read-only adapter failure; no order was sent.",
                {"error_class": type(exc).__name__},
            )
            self.journal.append(
                run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
                "NO_TRADE: unhandled MT5 observation error.",
                {"action": "NO_TRADE", "vetoes": ["UNKNOWN_READ_ERROR"],
                 "order_send_called": False},
            )
            self.journal.finish(run_id, "FAILED")
            raise MT5ReadError("PROBE_UNEXPECTED_ERROR") from None
