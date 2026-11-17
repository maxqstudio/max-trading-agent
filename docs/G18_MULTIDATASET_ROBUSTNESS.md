# G18 — Disjoint-dataset walk-forward research (no trade authority)

**Purpose:** check whether one fixed, predeclared tabular-Q research setup gives similarly descriptive results across multiple disjoint historical periods. This is an extension of [G16](C04_OFFLINE_RL.md); it does not train on real transaction experience, verify true statistical independence, or authorize broker execution.

## Private invocation

Import **2–5 user-provided** contiguous completed MID OHLC datasets into the same local MarketStore, for the **same symbol and timeframe**, with strictly increasing non-overlapping timestamp ranges. Capture their SHA-256 dataset IDs from `market-import`.

```sh
python -m max_trading_agent rl-robustness --market-db .local/market_evidence.sqlite3 --datasets EARLIER_SHA256,LATER_SHA256 --spread 0.0002 --slippage 0.00003 --fee-per-side 0.00001 --folds 2 --test-bars 24 --epochs 16 --alpha 0.2 --gamma 0.6
```

**All numeric costs are user-supplied price units per one asset unit** and must represent the same instrument and fee convention; the example numbers are illustrative, never broker calibrated. Each dataset has its own independent *run* of G16 expanding-window fitting and holdout scoring. Frozen fold parameters are not shared across datasets; earlier dataset results never train a later dataset. Holdout windows inside each dataset remain chronological and disjoint.

The command first validates **every** dataset ID, manifest, checksum, quote basis, continuity, training length, price/cost bound, same symbol/timeframe and non-overlapping order. Invalid batches are rejected before any per-dataset experiment record is created. The per-dataset records can be re-read using `experiment-show --experiment ID`. The combined report has deterministic SHA-256, per-dataset provenance hashes, every fold's Q/momentum/NO_TRADE/doubled-cost net price-unit score and separate descriptive pass counts. **Raw scores are never summed across periods or instruments.** No external data are fetched; neither MT5 nor an LLM is invoked.

### Interpretation and hard limits

- `historical_windows_disjoint=true` means timestamps do not overlap, **not** that price regimes, providers, markets, or samples are statistically independent.
- `datasets_all_folds_positive` and `datasets_surviving_doubled_cost` are descriptive counts, **not** p-values, confidence intervals, risk-adjusted portfolio performance or evidence of profitable live execution. A zero-trade model can pass some relative baselines; inspect trade counts and all fold scores.
- The result **always** says `dataset_independence_verified=false`, `eligible_for_trading=false`, `decision=NO_TRADE` and `execution=DISABLED`. Sample/fixture data in GitHub CI are synthetic and have no generalization guarantee.
- One global cost model intentionally rejects mixed instruments/timeframes. Real tick BID/ASK feeds, spread dynamics, latency, swaps, broker acceptance, independent sourced history/rights and multiple-testing corrections remain unverified.

**GitHub-only acceptance:** Linux and Windows offline tests must pass at the exact Draft PR commit. No merge, demo, live trading, automatic self-promotion or owner release from a green CI alone. Tracking [Issue #47](https://github.com/maxqstudio/max-trading-agent/issues/47).
