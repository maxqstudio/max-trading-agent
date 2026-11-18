# M03 — Historical Evidence Lab (offline, research-only)

This milestone **does not create a profitable trading strategy**. It gives the agent a reproducible history database and a testable baseline for researching hypotheses without contaminating future outcomes.

## Import private historical candles

Only use data you have permission to store. Required CSV headers (exact):

```csv
time_utc,open,high,low,close
2023-01-02T00:00:00+00:00,1.0500,1.0510,1.0490,1.0504
```

A full import requires at least **128** aligned, completed candles in ascending UTC order. No sample dataset is committed to this public repository. A sample line above is illustrative only, not a full import.

```sh
python -m max_trading_agent market-import \
  --market-db .local/market_evidence.sqlite3 \
  --file /private/history.csv --symbol EURUSD \
  --timeframe M15 --quote-basis MID \
  --source "owner-authorized-provider" --rights "USER_PROVIDED"
```

It outputs a SHA-256 dataset ID. The database stores the candles, provenance label, raw-file hash, start/end coverage, and gap counts; missing trading sessions are **reported**, not automatically interpolated. Source labels are operator declarations, not verified market licenses.

## Run an explicit-cost baseline

```sh
python -m max_trading_agent market-backtest \
  --market-db .local/market_evidence.sqlite3 \
  --dataset <DATASET_SHA256> \
  --spread 0.00020 --slippage 0.00005 --fee-per-side 0.00001
```

All three cost arguments must be explicitly supplied. They are **price units per ONE asset unit**, not MT5 broker account-currency P&L. This backtest uses **MID OHLC** and will reject BID/ASK-only datasets because the simulation's mid-price execution model would otherwise misstate spread costs.

The single **illustrative** SMA(3,8) long/flat method decides the position at candle *i open* using only close prices from candles strictly before *i*. The simulator:
- Opens long only at a later candle open, not the bar used to compute the signal.
- Models ask at entry and bid at exit using fixed spread and adverse slippage; explicit per-side fees.
- Exits on subsequent flat-state signals at next open. At each split end it forces a synthetic **END_MARK_NOT_FILL**, which is not an executable broker fill.
- Separates 70% development vs 30% holdout chronologically; holdout may use earlier price history as warmup, never future outcomes.
- Records development/holdout trade lists, per-unit P&L, win/loss, drawdown and baseline NO_TRADE = 0.
- Labels evidence `DESCRIPTIVE_ONLY` or `INSUFFICIENT_TRADES`, never `PROFITABLE` or `LIVE_APPROVED`.

All results, including poor/negative performance, persist by experiment hash in SQLite. CLI returns experiment ID, safe summary and `NO_TRADE`; inspect full local report:

```sh
python -m max_trading_agent experiment-show --market-db .local/market_evidence.sqlite3 --experiment <EXPERIMENT_SHA256>
python -m max_trading_agent serve
```

Research-only experiment events appear in the Trading Room under the **Market Data Auditor** and **Evidence Lab** roles when using the same `--journal` path.

## Explicit limitations

- **No licensed real-world 30-year market feed is bundled**. This does not pull market data from MT5; an owner-authorized historical export or provider is required.
- No bid/ask tick replay, order-book data, true slippage/liquidity, variable spread, swap, commissions in account currency, broker contract scaling, latency or partial fills. Results cannot be treated as real trade returns.
- No strategy optimization, multiple testing, walk-forward folds or RL yet. A single chronological holdout is a baseline, not proof of stable edge.
- Out-of-sample performance is not a future-performance guarantee. A profitable-looking historical result **does not authorize a trade**.
- GitHub Actions uses reproducible synthetic fixtures; it never accesses a real broker, private dataset, paid LLM or owner local CI. Real data quality must be evaluated separately.

## Gate

M03 can PASS software CI while remaining **RESEARCH_ONLY**. M04 should add shadow episodes and compare hypothetical decisions to observed forward market outcomes, with clear separation from DEMO and LIVE broker fills.

## M03 optional CSV tick-volume repair (G35 stacked tip)

CSV imports may include the exact additional column `tick_volume`, a **nonnegative integer count** (zero is permitted). This optional field is persisted with its historical candle, participates in the immutable dataset content hash, and is included in readback integrity checks; negative, missing, non-integral and out-of-range values are refused. OHLC-only CSV imports retain their historical content hashes. Existing offline SQLite databases are upgraded in place with a nullable volume field; no broker access or market feeds are introduced. Tests cover restart replay, tampering and legacy-schema upgrades. This correction lives in the final stacked G35 tip; original PR #18 is unchanged and must not be described as independently accepted. Research-only / `NO_TRADE` remains mandatory.
