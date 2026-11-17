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

## M02 — Real LLM research roles (SIGNAL-only candidate)

The project now includes a provider adapter for OpenAI-compatible Chat Completions and a role sequence: Market Analyst, Bull Researcher, Bear Researcher, Research Manager. Each model response is stored in the Trading Room journal as **unverified research opinion**. Research ends in `NO_TRADE`, irrespective of model confidence, because no validated strategy evidence or order engine exists yet.

```sh
# Export three variables in your own private environment:
# MAX_TRADING_LLM_ENDPOINT, MAX_TRADING_LLM_MODEL and (if required) MAX_TRADING_LLM_API_KEY
python -m max_trading_agent signal-research --snapshot /private/snapshot.json
```

This is not a public cloud service; the snapshot and keys remain on the user's authorized computer. Provider calls may cost money. See [M02_SIGNAL_RESEARCH.md](docs/M02_SIGNAL_RESEARCH.md) and [Issue #15](https://github.com/maxqstudio/max-trading-agent/issues/15). GitHub Actions uses a fake provider and no API credentials.

M02 research now also retrieves source-linked **MT5 operational knowledge** through the existing SQLite FTS5 RAG index and records the retrieval as a real `RAG_RETRIEVAL` event. These sources describe MT5 functions, **not** statistically validated strategies; the independent policy guard continues to veto trading.

## M03 — Offline history & evidence lab (candidate)

The engine can ingest operator-provided, provenance-labeled OHLC CSV to a private immutable-by-API SQLite dataset ledger and run a deterministic SMA long/flat **research-only** historical evaluation with a chronological holdout. It records actual computation artifacts and `NO_TRADE` in the existing Trading Room event journal.

```sh
python -m max_trading_agent market-import --file /private/history.csv --symbol EURUSD --timeframe M15 --source authorized-export --quote-basis MID
python -m max_trading_agent market-backtest --dataset <DATASET_SHA256> --spread 0.0002 --slippage 0.00005 --fee-per-side 0.00001
```

These are illustrative research inputs, **not recommended broker execution costs**. Review [M03 market evidence limitations](docs/M03_MARKET_EVIDENCE.md) and [Issue #17](https://github.com/maxqstudio/max-trading-agent/issues/17). No data vendor API, real account, LLM strategy approval, or MT5 order execution is involved. CI validates software behavior against synthetic candles only.

## M04 — Forward-only shadow episodes (candidate)

Persist research-only hypothetical LONG/SHORT decisions **before** outcomes exist, then settle using a strictly newer, fresh read-only MT5 snapshot. Each episode has its own SQLite record and Trading Room journal event; no `order_send`, broker fills, or live permissions exist. See [M04 Shadow Ledger](docs/M04_SHADOW_LEDGER.md) and [Issue #19](https://github.com/maxqstudio/max-trading-agent/issues/19).

```sh
python -m max_trading_agent shadow-open --candidate research_0001 --snapshot /private/entry.json --side LONG --slippage 0.00002 --fee-per-side 0.00001
python -m max_trading_agent shadow-settle --candidate research_0001 --snapshot /private/later.json
python -m max_trading_agent shadow-show --candidate research_0001
```

Real-time snapshot capture is owner-authorized and separate from GitHub Actions. The ledger is not an unattended autonomous agent or a validation of live trading.

## M05 — Autonomous read-only market watcher (candidate)

A locally started watcher can poll an authorized MT5 terminal, set a first-candle baseline, persist/deduplicate new completed candle events and optionally invoke research-only LLM roles. Watcher events appear in the existing Trading Room with real origins. This is **NOT** automatic broker trading and does not run on GitHub-hosted infrastructure.

```sh
python -m max_trading_agent watch --symbol EURUSD --timeframe M15 --interval-seconds 15
# Optional, explicitly billable research if provider env vars are configured:
python -m max_trading_agent watch --symbol EURUSD --timeframe M15 --enable-llm-research
```

See [M05 runtime and limits](docs/M05_AUTONOMOUS_WATCHER.md) and [Issue #21](https://github.com/maxqstudio/max-trading-agent/issues/21). GitHub Actions uses fake providers and tests policy boundaries; owner terminal runtime acceptance remains required.

## M06 — Local MT5 account binding (candidate)

Read-only MT5 snapshots are HMAC-signed using a locally generated secret at `.local/mt5_identity.key` (or `--identity-key-file`). The adapter binds the login and broker server without logging them; the watcher stops prospectively admitting research if **either changes, even between two DEMO accounts**. Existing legacy unbound M05 watcher states are intentionally not auto-rebound. Losing or changing the private key also blocks existing account bindings.

HMAC gives local tamper evidence only, **not independent proof of genuine broker quotes**. See [M06 security and limitations](docs/M06_IDENTITY.md) and [Issue #23](https://github.com/maxqstudio/max-trading-agent/issues/23). No trades are permitted.
