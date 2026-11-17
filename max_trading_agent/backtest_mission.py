"""Write auditable research events for deterministic historical experiments."""
from __future__ import annotations

from .evidence import run_evidence
from .market_store import MarketDataError, MarketStore
from .runtime import EventJournal


class BacktestMission:
    def __init__(self, journal: EventJournal, store: MarketStore):
        self.journal = journal
        self.store = store

    def execute(self, dataset_id: str, *, spread: float, slippage: float,
                fee_per_side: float) -> dict:
        run_id = self.journal.start("evaluate_historical", mode="BACKTEST")
        try:
            manifest = self.store.manifest(dataset_id)
            self.journal.append(
                run_id, "Market Data Auditor", "DATASET_VERIFIED", "PASS",
                "Historical input declared; checking provenance, bars and gaps.",
                {"dataset_id": dataset_id, "symbol": manifest["symbol"],
                 "timeframe": manifest["timeframe"], "quote_basis": manifest["quote_basis"],
                 "bars": manifest["count"], "gap_count": manifest["gap_count"],
                 "source": manifest["source"]},
            )
            experiment = run_evidence(
                self.store, dataset_id, spread=spread,
                slippage=slippage, fee_per_side=fee_per_side,
            )
            result = experiment["result"]
            self.journal.append(
                run_id, "Evidence Lab", "HYPOTHESIS_TESTED", "PASS",
                "Executed fixed historical SMA hypothesis and no-trade baseline "
                "on separate chronological development/holdout windows.",
                {"experiment_id": experiment["experiment_id"],
                 "strategy": result["strategy"],
                 "split_index": experiment["config"]["split_index"],
                 "cost_assumptions": {
                     "spread": spread, "slippage": slippage,
                     "fee_per_side": fee_per_side,
                 },
                 "development": {
                     k: result["development"][k]
                     for k in ("trades", "net_pnl_price_units_per_unit",
                               "max_drawdown_price_units_per_unit", "evidence_grade")
                 },
                 "holdout": {
                     k: result["holdout"][k]
                     for k in ("trades", "net_pnl_price_units_per_unit",
                               "max_drawdown_price_units_per_unit", "evidence_grade")
                 },
                 "fill_model": "SYNTHETIC_MID_OHLC_NEXT_OPEN",
                 "no_real_broker_fills": True},
            )
            self.journal.append(
                run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
                "NO_TRADE: historical simulation is not sufficient to authorize a trade.",
                {"action": "NO_TRADE", "vetoes": ["BACKTEST_RESEARCH_ONLY",
                                                   "NO_REAL_BROKER_VALIDATION"],
                 "order_send_called": False},
            )
            self.journal.append(
                run_id, "Supervisor", "REPORT_READY", "PASS",
                "Backtest recorded with verifiable dataset and experiment IDs.",
                {"report": "Historical test complete. Strategy results are descriptive "
                           "per-unit price differences under declared synthetic costs. "
                           "Check holdout evidence and actual trade counts; NO_TRADE "
                           "remains the only authorized action.",
                 "experiment_id": experiment["experiment_id"]},
            )
            self.journal.finish(run_id, "COMPLETED")
            return {"run_id": run_id, "experiment_id": experiment["experiment_id"],
                    "dataset_id": dataset_id, "decision": "NO_TRADE",
                    "execution": "DISABLED",
                    "holdout": {
                        k: result["holdout"][k]
                        for k in ("trades", "net_pnl_price_units_per_unit",
                                  "max_drawdown_price_units_per_unit", "evidence_grade")
                    }}
        except Exception as exc:
            # Errors here must not be promoted into trading permissions.
            code = str(exc) if isinstance(exc, MarketDataError) else "EXPERIMENT_FAILED"
            self.journal.append(
                run_id, "Evidence Lab", "EXPERIMENT_FAILED", "FAILED",
                "Evidence experiment failed; previous records remain unchanged.",
                {"error_code": code},
            )
            self.journal.append(
                run_id, "Independent Policy Guard", "DECISION", "BLOCKED",
                "NO_TRADE: research evidence unavailable.",
                {"action": "NO_TRADE", "vetoes": ["NO_VALID_EXPERIMENT"],
                 "order_send_called": False},
            )
            self.journal.finish(run_id, "FAILED")
            if isinstance(exc, MarketDataError):
                raise
            raise MarketDataError("EXPERIMENT_FAILED") from None
