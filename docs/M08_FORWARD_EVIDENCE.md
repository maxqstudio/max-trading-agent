# M08 — Forward shadow evidence, not trading profitability

This view draws **read-only** from the owner-started M07 research watcher and private SQLite event journal. All outcomes are **hypothetical**.

## What to inspect

Run `python -m max_trading_agent serve --journal .local/agent_events.sqlite3` locally, then open `http://127.0.0.1:8765`. The right-side **Forward Shadow Evidence** pane shows attempted opt-in LLM cycles, abstains, rejected hypotheses, failed/blocked/review-required jobs, pending simulated episodes and settled outcomes. It refreshes approximately every 6 seconds. The equivalent read-only endpoint is `GET /api/evidence`. It exposes **no broker account HMAC IDs, login, server, account currency balances, key paths or API credentials**.

Each symbol receives a separate descriptive grade:
- `INSUFFICIENT_FORWARD_SAMPLE`: fewer than 30 settled synthetic outcomes; never a claim of strategy alpha.
- `DESCRIPTIVE_ONLY`: 30+ settled outcomes, still **not** a proven edge or LIVE gate. A 30-sample threshold is merely a *display* convention.
- `ACCOUNT_MIXED`: more than one authenticated MT5 account contributed episodes for that symbol. To prevent false aggregation, P/L metrics are **withheld**.
- `NO_DATA`: no observed outcomes.

P/L is the **sum of hypothetical price differences per one asset unit** for sequential research candidates; it is not an actual MT5 deal, USD profit, contract-size-adjusted account return, portfolio result or prediction of a live fill. Fees and adverse slippage are modeled, not broker confirmed. Summary win rate ignores unresolved episodes but the panel also shows pending, failed and ABSTAIN counts so those outcomes cannot be hidden as if only the winners existed. No cross-symbol P/L total is offered. The reported drawdown is **realized model-P/L path drawdown** from settled hypothetical episodes, not floating equity drawdown.

A tampered/contradictory ledger returns HTTP **409** with `evidence_integrity_blocked` and does **not** return a partial misleading profit figure. Missing data yields `NO_DATA` and no invented values. Legacy manual/unbound shadow outcomes are excluded from autonomous evidence and are counted separately in the JSON totals. This pane never calls MT5, LLMs, backtesting jobs, or brokerage APIs.

## Actual acceptance still pending

All CI fixtures are synthetic; GitHub-hosted Windows/Linux runners have no broker, no real MT5 terminal and no paid model credentials. This project is **not production trading-ready**. Real forward evidence requires independent runtime collection over enough market regimes, authenticated local capture, sampling rules and proper execution-cost calibration. Original master PRD #1 AUTO_DEMO/RL/Tool Factory/LIVE gates remain independent and unaccepted; the chronological M08 label here does not redefine them.

See [Issue #27](https://github.com/maxqstudio/max-trading-agent/issues/27) for acceptance criteria.
