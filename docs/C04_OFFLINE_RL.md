# Canonical M04 — Offline tabular Q-learning research candidate

This implementation trains a **small, deterministic tabular Q-learner** exclusively on imported **user-provided MID OHLC** from MarketStore. It is **not** deep RL, offline policy evaluation on behavior logs, real broker trading or proven alpha. All candidate actions (FLAT/LONG/SHORT) are evaluated using synthetic price paths; this is a counterfactual simulator with strict limits.

```sh
python -m max_trading_agent offline-rl --market-db .local/market_evidence.sqlite3 --dataset THE_DATASET_SHA256 --spread 0.0002 --slippage 0.00003 --fee-per-side 0.00001 --epochs 16 --alpha 0.2 --gamma 0.6
```

The command has no access to MT5, order_send, broker credentials or online LLM. The data must have at least 180 immutable, hash-verified completed candles; the full set is split into the **first 70% development** and **last 30% holdout** with an embargo. The training state for decision i uses only closes through i; simulated entry and exit occur at opens i+1 and i+2. The volatility reference threshold derives from the development partition only. Q is frozen before holdout scoring and scored against fixed momentum and zero P/L NO_TRADE baselines. Every hypothetical round trip subtracts explicit modeled spread/slippage/fee costs. Windows/Linux CI tests cover holdout modification having no effect on trained model hash.

Outputs are **synthetic price-unit differences per one asset unit**, not account currency, lot-adjusted returns, real fees or realizable broker fills. The class can be profitable on the supplied data and still be overfit or fail on live market. No trading mode promotion is authorized; all results carry `decision=NO_TRADE` and `execution=DISABLED`.

Further acceptance: multiple provenance-compliant datasets/regimes, true broker-cost calibration, robust confidence intervals, walk-forward repetitions and owner runtime acceptance. See [Issue #35](https://github.com/maxqstudio/max-trading-agent/issues/35).

## G16 — Walk-forward robustness drill (offline only)

```sh
python -m max_trading_agent rl-walk-forward --market-db .local/market_evidence.sqlite3 --dataset THE_DATASET_SHA256 --spread 0.0002 --slippage 0.00003 --fee-per-side 0.00001 --folds 3 --test-bars 32 --epochs 16
```

A separately invoked, **predeclared expanding-window** walk-forward experiment on one hash-verified, contiguous MID OHLC dataset. Supports 2–6 chronological folds with 24–120 holdout bars each and at least 128 initial training bars. Each fold recomputes its feature threshold and Q policy **from bars before that fold only**; the last training outcome ends before the first test state. Earlier test windows may become training data for later folds, as expected for expanding walk-forward retraining.

Test entries/exits use open(i+1)/open(i+2) after the completed state at close(i), and each holdout is scored with a two-bar decision stride to avoid **overlapping hypothetical positions**. Baselines: fixed momentum and NO_TRADE, using identical holdout decision points. A doubled-cost test re-scores the **same frozen policy**, never retrains or optimizes on the holdout. Every fold is reported, including losses; all outputs are price units per one asset unit, not account-currency returns. Store records use deterministic SHA-256 keys and immutable dataset IDs.

**Known limitations:** This cannot prove robustness across independent instruments, timeframes, market regimes, brokers, discontinuous/weekend gap markets or real execution. It does not produce reliable confidence intervals, account leverage/margin, brokerage fees/fills or alpha. Do not use the first-fold results to retune parameters and then call the same windows independent. An impressive synthetic walk-forward result is *still* `NO_TRADE` and never qualifies for DEMO/LIVE release. See [Issue #43](https://github.com/maxqstudio/max-trading-agent/issues/43).
