# Max Trading Agent

An autonomous MetaTrader 5 research/trading agent, beginning with an **MT5 Knowledge & Tool Intelligence Engine**. This repository is public; never commit broker credentials or proprietary trading information.

## Status

**M00 foundation only.** No broker connection, no live prices, and **no order sending** implemented. See [master PRD](https://github.com/maxqstudio/max-trading-agent/issues/1) and [M00 issue](https://github.com/maxqstudio/max-trading-agent/issues/2).

## Principles

- The agent must know which MT5 functions and data to inspect *without custom training*.
- Source-grounded indexed knowledge explains **how MT5 works**; real quotes, account state, positions and broker rules must be read from a running MT5 terminal.
- Knowledge search is not a trading signal, and embeddings alone cannot make trading profitable.
- STOP is the default. Manual SIGNAL, AUTO_DEMO, RL, policies, skills, personas and controlled self-development are roadmap stages; AUTO_LIVE needs separate explicit approval.

## Next: M00 Knowledge Foundation

A Python standard-library SQLite FTS5 index of curated, attributed MT5 API descriptions, task-specific tool plans, validation tests and GitHub Actions CI. Embeddings / hybrid search will be added later without requiring retraining the LLM.

## Docs

- [Official Python integration](https://www.mql5.com/en/docs/python_metatrader5)
- [Official error codes](https://www.mql5.com/en/docs/python_metatrader5/mt5lasterror_py)
- [Official order handling](https://www.mql5.com/en/docs/python_metatrader5/mt5ordersend_py)

**Trading carries substantial loss risk. Nothing in this repository is evidence of profitability.**

## M00 working branch quickstart

Python 3.12+ and the Python standard library suffice for this *reference-only* foundation.

```bash
python -m max_trading_agent validate
python -m max_trading_agent index
python -m max_trading_agent search "bid ask"
python -m max_trading_agent plan prepare_entry
python -m unittest discover -s tests -v
```

All plans have `execution: DISABLED`. The KB does **not** connect to MT5, fetch live data, or submit orders. The broad strategy RAG, LLM tool factory, shadow trading and RL are specified in [Issues #1–#10](https://github.com/maxqstudio/max-trading-agent/issues).

## A00 Agent Trading Room — read-only runtime prototype

The browser workspace draws **Team Progress / Messages & Tool Calls / Current Report & Evidence** from persisted event records, inspired by [TradingAgents](https://github.com/TauricResearch/TradingAgents) but not copied from its code.

```bash
python -m max_trading_agent run analyze_symbol
python -m max_trading_agent serve
```

Open http://127.0.0.1:8765 and run a reference mission. The web server binds to loopback only. It indexes and searches curated MT5 API knowledge, plans required MT5 reads (but **does not call them**), records exact missing evidence and emits NO_TRADE. This is a genuine local tool-index and journal integration, **not** a connected LLM, MT5 broker, live quote stream, backtest, actual role debate, or trading engine. No executable order path exists.

Only **GitHub Actions CI** is used for acceptance; it tests offline Python, HTTP and JS syntax. Real MT5/LLM integrations require separate stages and evidence.

## M01 — Opt-in real MT5 read-only snapshot (candidate)

The connector uses an already configured **local MetaTrader 5 terminal** and never sends an order or changes terminal account credentials. The actual terminal workflow is **not** tested on GitHub runners.

```sh
python -m max_trading_agent mt5-probe --symbol EURUSD --timeframe M15 --bars 100
```

The command requires the MetaTrader5 Python package and working terminal on an authorized computer, and reads snapshot data only. Output includes **private balances and open position details; do not post raw output publicly**. Missing data/stale quote means a failed probe, not a trade. See [M01_READONLY.md](docs/M01_READONLY.md) and [Issue #13](https://github.com/maxqstudio/max-trading-agent/issues/13).

The `mt5-probe` command also records a **redacted READ_ONLY mission** into the local event journal, which the Trading Room can display after the command. Account balances, broker login/server and private position details are excluded from the journal. The full CLI response remains private and should not be uploaded.
