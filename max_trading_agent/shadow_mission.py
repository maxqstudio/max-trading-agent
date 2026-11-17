"""Journal-backed hypothetical shadow tasks, isolated from real broker orders."""
from __future__ import annotations

from .runtime import EventJournal
from .shadow import ShadowError, ShadowLedger


class ShadowMission:
    def __init__(self, journal: EventJournal, ledger: ShadowLedger):
        self.journal = journal
        self.ledger = ledger

    def open(self, candidate_id: str, snapshot: dict, *, side: str,
             slippage: float, fee_per_side: float,
             origin: str = "USER_RESEARCH",
             research_run_id: str | None = None,
             watch_job_id: str | None = None) -> dict:
        run_id = self.journal.start("shadow_open", mode="SHADOW")
        try:
            observation = self.ledger.record(
                candidate_id, snapshot, side=side, slippage=slippage,
                fee_per_side=fee_per_side, origin=origin,
                research_run_id=research_run_id, watch_job_id=watch_job_id,
            )
            self.journal.append(
                run_id, "Shadow Lab", "SHADOW_OPENED", "PASS",
                "Recorded a prospective hypothetical candidate before its outcome.",
                {"candidate_id": candidate_id, "side": observation["side"],
                 "symbol": observation["symbol"],
                 "entry_quote_utc": observation["entry_quote_utc"],
                 "entry_snapshot_hash": observation["entry_snapshot_hash"],
                 "research_run_id": observation["research_run_id"],
                 "watch_job_id": observation["watch_job_id"],
                 "fill_type": "HYPOTHETICAL", "broker_execution": False},
            )
            self.journal.append(
                run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
                "NO_TRADE: a shadow observation is not a broker order.",
                {"action": "NO_TRADE", "vetoes": ["SHADOW_ONLY"],
                 "order_send_called": False},
            )
            self.journal.finish(run_id, "COMPLETED")
            return {"run_id": run_id, "episode": observation}
        except Exception as exc:
            self._fail(run_id, exc)
            raise

    def settle(self, candidate_id: str, snapshot: dict) -> dict:
        run_id = self.journal.start("shadow_settle", mode="SHADOW")
        try:
            observation = self.ledger.settle(candidate_id, snapshot)
            self.journal.append(
                run_id, "Shadow Lab", "SHADOW_SETTLED", "PASS",
                "Future quote settled a hypothetical episode. Not a real fill.",
                {"candidate_id": candidate_id,
                 "entry_quote_utc": observation["entry_quote_utc"],
                 "exit_quote_utc": observation["exit_quote_utc"],
                 "net_pnl_price_units": observation["net_pnl_price_units"],
                 "research_run_id": observation["research_run_id"],
                 "watch_job_id": observation["watch_job_id"],
                 "fill_type": "HYPOTHETICAL", "broker_execution": False},
            )
            self.journal.append(
                run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
                "NO_TRADE: shadow settlement is not broker execution.",
                {"action": "NO_TRADE", "vetoes": ["SHADOW_ONLY"],
                 "order_send_called": False},
            )
            self.journal.finish(run_id, "COMPLETED")
            return {"run_id": run_id, "episode": observation}
        except Exception as exc:
            self._fail(run_id, exc)
            raise

    def _fail(self, run_id: str, exc: Exception) -> None:
        code = str(exc) if isinstance(exc, ShadowError) else "SHADOW_INTERNAL_ERROR"
        self.journal.append(
            run_id, "Shadow Lab", "SHADOW_FAILED", "FAILED",
            "Hypothetical episode failed verification; no broker action taken.",
            {"code": code},
        )
        self.journal.append(
            run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
            "NO_TRADE: invalid or unavailable shadow evidence.",
            {"action": "NO_TRADE", "vetoes": [code], "order_send_called": False},
        )
        self.journal.finish(run_id, "FAILED")
