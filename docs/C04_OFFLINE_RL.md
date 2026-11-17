# Canonical M04 — Offline tabular Q-learning research candidate

This implementation trains a **small, deterministic tabular Q-learner** exclusively on imported **user-provided MID OHLC** from MarketStore. It is **not** deep RL, offline policy evaluation on behavior logs, real broker trading or proven alpha. All candidate actions (FLAT/LONG/SHORT) are evaluated using synthetic price paths; this is a counterfactual simulator with strict limits.

```sh
python -m max_trading_agent offline-rl --market-db .local/market_evidence.sqlite3 --dataset THE_DATASET_SHA256 --spread 0.0002 --slippage 0.00003 --fee-per-side 0.00001 --epochs 16 --alpha 0.2 --gamma 0.6
```

The command has no access to MT5, order_send, broker credentials or online LLM. The data must have at least 180 immutable, hash-verified completed candles; the full set is split into the **first 70% development** and **last 30% holdout** with an embargo. The training state for decision i uses only closes through i; simulated entry and exit occur at opens i+1 and i+2. The volatility reference threshold derives from the development partition only. Q is frozen before holdout scoring and scored against fixed momentum and zero P/L NO_TRADE baselines. Every hypothetical round trip subtracts explicit modeled spread/slippage/fee costs. Windows/Linux CI tests cover holdout modification having no effect on trained model hash.

Outputs are **synthetic price-unit differences per one asset unit**, not account currency, lot-adjusted returns, real fees or realizable broker fills. The class can be profitable on the supplied data and still be overfit or fail on live market. No trading mode promotion is authorized; all results carry `decision=NO_TRADE` and `execution=DISABLED`.

Further acceptance: multiple provenance-compliant datasets/regimes, true broker-cost calibration, robust confidence intervals, walk-forward repetitions and owner runtime acceptance. See [Issue #35](https://github.com/maxqstudio/max-trading-agent/issues/35).
