# M01 — MT5 read-only connection protocol

**Status:** implementation candidate; GitHub Actions fake API verification is not a real-terminal acceptance test.

## Why this exists

The agent must know which account, symbol, price and history calls are required before any trading reasoning. This adapter reads the already-authorized *local* MetaTrader 5 terminal. It does not log in, change Market Watch, calculate an order request, run an LLM or place trades.

## Opt-in local probe (future owner terminal acceptance)

This command is for an **authorized Windows computer with MetaTrader 5 installed and connected**. It is not run in GitHub Actions.

1. Install Python version compatible with the MT5 Python package and install the package into your chosen environment using the vendor's official documentation.
2. Open and log into the MT5 terminal yourself.
3. Run `python -m max_trading_agent mt5-probe --symbol EURUSD --timeframe M15 --bars 100`.
4. The CLI prints a strictly read-only, timestamped JSON snapshot. **It includes account balance, positions and other private financial information. Do not post raw output to GitHub or public issue comments.** Only a redacted test report may be shared.
5. Any unavailable/invisible symbol, disconnected terminal, invalid price, stale quote or insufficient history returns a safe error code; trading remains disabled.

## Provenance and limits

- MT5 Python function reference: https://www.mql5.com/en/docs/python_metatrader5
- Bar index zero is the current unfinished candle; the adapter requests from position one: https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrompos_py
- The terminal only exposes historical bars available in its configured chart history. The adapter must not treat those as a guaranteed multi-decade database.
- Tick times and bars use UTC timestamps. Real-world broker holidays and session gaps mean equal-spacing bars are **not** universally valid.
- Explicitly supported timeframes: M1, M5, M15, M30, H1, H4, D1. Others require tested additions rather than silent fallback.
- This is a *snapshot*, not an uninterrupted high-frequency feed; further work must detect runtime disconnect/reconnect, analyze fill models and broker data drift before demo trading.
- GitHub runners do not have MT5 or brokerage credentials. Fake provider tests prove only behavior against their fixtures.

## Non-goals

No signal, live position management, broker mutation, RAG embedding, model-generated strategy or autonomous LIVE trading in M01.

## Structured NumPy candle compatibility (M01 patch)

Official `copy_rates_from_pos` yields NumPy structured rows with fields `time`, `open`, `high`, `low`, `close`, `tick_volume`, `spread`, and `real_volume`. The adapter now reads `dtype.names` fields via `row[field]`, normalizes NumPy integer/float scalars to JSON-safe Python numbers, and rejects invalid or nonfinite values. GitHub Actions installs **NumPy 2.2.6 only for these fake-provider tests** on both Linux and Windows; runtime still loads MetaTrader5 lazily and no live terminal is accessed by CI.

**Remaining acceptance:** signed real terminal smoke on an authorized owner's Windows session, with private account/server details redacted. A fake structured-array PASS is not evidence of a genuine broker connection. No order execution is enabled.

## M01 acceptance gap repair (implemented in the G35 stacked tip)

The final G35 aggregate branch adds two narrow fixes to M01 without changing the pinned head of PR #14 or any intermediate stacked PR:

- Reject zero or negative `open`, `high`, `low`, or `close` before a snapshot can pass as valid market data. Fail closed with `INVALID_OHLC` and release the MT5 connection.
- Read the official [MT5 `version()` API](https://www.mql5.com/en/docs/python_metatrader5/mt5version_py) after confirming terminal connectivity. Capture only validated numeric terminal version, build and structured release date in `terminal.version`; missing or malformed metadata aborts with a bounded error code. No broker login, server or raw exception text is recorded as version metadata.

These checks are exercised by fake-provider regression on GitHub Linux/Windows, not by an actual owner's MT5 terminal. Because the fix lives in the final stacked branch, PR #14 alone remains the historical M01 candidate: its standalone tests still do **not** verify these new constraints, and it must not be described as individually accepted. Integrating the full reviewed stack and owner Windows terminal acceptance remain separately gated. `execution=DISABLED`, `NO_TRADE`.
