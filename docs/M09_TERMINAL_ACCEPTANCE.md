# M09 — owner-controlled MT5 feed acceptance kit (read-only)

**CI has NOT connected to a real broker or local MT5 terminal.** A GitHub Actions PASS on this milestone validates fake-terminal test contracts only. Owner runtime steps remain **PENDING** and must never run on public GitHub CI or involve posting credentials.

## Purpose

M01–M08 can test synthetic MT5 adapter behavior and shadow results. M09 adds a bounded multi-snapshot check of **locally configured terminal** feed consistency across successive read-only observations. This does not authenticate brokerage prices independently or establish financial profitability.

The future owner-controlled runtime requires a preconfigured authorized Windows MT5 terminal, official MetaTrader5 Python package, a visible broker-specific symbol, and a private local HMAC key. The utility never logs into an account, selects symbols, or changes orders.

Illustrative single-line PowerShell command, **not executed here**:

```powershell
python -m max_trading_agent mt5-acceptance --symbol EURUSD --timeframe M15 --bars 100 --samples 3 --sample-interval 2 --identity-key-file ".local/mt5_identity.key" --journal ".local/agent_events.sqlite3"
```

The command samples 2–10 times, with 1–30 seconds between samples. Each snapshot uses the existing read-only adapter. Reports contain **only safe summary fields and a Trading Room journal run**; no account number, account-binding HMAC, key, server, balance, position IDs or raw snapshot. Keep the private SQLite journal locally. Never attach a raw MT5 output or private key to public issues.

## Status and exit codes

- `OBSERVED_CONSISTENT` (exit 0): local HMAC, stable account, recent candle history/mandatory metadata and at least one **new quote timestamp**. **Owner acceptance is still PENDING**; no trading permission.
- `INCONCLUSIVE` (exit 3): valid signed reads with no newer quote, absent symbol specifications, aged last completed candle, gaps/revisions-free but incomplete requested history, historical gaps anywhere in the requested bars (not only the most recent 40), or skipped completed candles. Can be normal in quiet/closed markets; never count as feed-ready.
- `BLOCKED` (exit 2): account switch, bad HMAC, invalid/stale quote or capture (including a locally signed replay older than 30 seconds), backwards timestamp, revised historical candle even outside the LLM's 40-bar window, disconnection or malformed evidence. Stop and investigate locally.
- Missing local MT5 bridge/key or invalid CLI args also exits 2 with redacted error.

`NO_TRADE` and `broker_execution=false` apply to ALL results. Journal contains only sample hashes of scrubbed market evidence, quote age, modeled spread (price units), safe reason codes and independent deterministic veto. The CLI summary exposes minimum/maximum quote age and bid–ask spread in price units across verified samples. Stale capture/quote checks are evaluated against the independent operator clock in addition to adapter-provided timestamps. A local HMAC is **not broker attestation**.

## Unmet real-runtime acceptance

No real MT5 terminal, proprietary feed, credentials, or paid LLM was accessed during development. A changing quote alone is not proof of genuine exchange prices, account identity beyond the configured terminal, fill quality, or profitable trading. A weekend/closed session may be INCONCLUSIVE; gaps require human context. Owner-controlled smoke and long-run feed validation must be reviewed separately before any DEMO order capability.

Original canonical PRD #1 AUTO_DEMO/RL/RAG/Tool Factory/LIVE gates remain unaccepted. See [Issue #29](https://github.com/maxqstudio/max-trading-agent/issues/29).
