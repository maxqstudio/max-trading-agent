# M07 — Agent research → forward SHADOW research workflow

**Status:** Draft implementation verified only with fake MT5 and fake LLM in GitHub Actions. Owner-authorized MT5 runtime and actual paid model calls have NOT been verified. No market edge or real trading capability is claimed.

## Opt-in example — local owner-controlled Windows terminal

```powershell
$env:MAX_TRADING_LLM_ENDPOINT = "https://YOUR_PROVIDER/v1/chat/completions"
$env:MAX_TRADING_LLM_MODEL = "YOUR_MODEL_ID"
# MAX_TRADING_LLM_API_KEY, if required, must stay in the private environment
python -m max_trading_agent watch --symbol EURUSD --timeframe M1 --bars 100 --interval-seconds 15 --enable-llm-research --enable-shadow-research --shadow-slippage 0.00002 --shadow-fee-per-side 0.00001 --shadow-max-cycles 5
```

The costs above are **illustrative assumptions**, not broker recommendations or account-currency charges. The `--shadow-max-cycles` cap is persistent in the local watcher database for the same symbol/timeframe across restarts. A trigger can make **up to five model calls** (four M02 research roles and one optional hypothesis strategist). Set cap conservatively. No model API tokens are required or present in GitHub Actions.

Sequence: watch baseline (no retrospective signal) → first subsequent completed new bar → four Market/Bull/Bear/Research-Manager reports with MT5 knowledge references → fifth Hypothesis Strategist returns strict JSON action LONG/SHORT/ABSTAIN plus bounded thesis and falsifier → independent NO_TRADE policy remains enforced → if accepted, a **HYPOTHETICAL** prospective shadow candidate opens using **current** bid/ask, adverse slippage and explicit fee. On a **later** new completed candle, the previous episode is settled using a new local HMAC-verified quote from the **same account**. Every stage has durable run/job/episode IDs and is visible in the Trading Room.

**ABSTAIN** records a skipped candidate; invalid JSON or a model's request to call `order_send` is rejected. Neither text can trigger brokerage actions. The schema is exact; no confidence scores, backtest profits or invented fill records are accepted.

Safety: broker account HMAC from M06 is required for auto-created shadow episodes, and a different same-mode account cannot settle them. A stale quote after a long LLM round prevents retrospective opening. Pending candidates are not settled when market snapshots are missing/stale, market history is revised, a bar-gap occurs, the symbol/account changes, or the feed disconnects. The job is not blindly retried after failure or crash. A finite persisted research cycle budget blocks unbounded repeated model spending.

**Limitations:** The model's research is NOT independently validated alpha; the simulated entry/exit is NOT a broker fill. Quotes, account identity and local HMAC can still be forged by a compromised PC. Market hours, broker spread, latency, swap, account currency, lots/contract size and actual execution slippage require separate acceptance. This system does not optimize a strategy, train an RL agent or auto-author tools. M07 does not provide LIVE/DEMO order execution.

See [Issue #25](https://github.com/maxqstudio/max-trading-agent/issues/25) for acceptance gates. No user's local CI is used.

## G35 stacked-tip M07 one-candle horizon enforcement

A *later* signed quote by itself did not prove that the outcome was observed at the one-candle research horizon. If the watcher skipped a candle, the first gap observation correctly emitted `CANDLE_GAP`, but a later contiguous observation could settle the original older SHADOW episode retroactively. The stacked-tip `settle_due()` now checks the stored `watch_jobs` origin for matching symbol, timeframe, completed source job and precisely the **immediately preceding completed candle** in the outcome snapshot. Missing/incomplete source lineage yields `SHADOW_ORIGIN_UNVERIFIED`; an overrun horizon yields `SHADOW_HORIZON_EXCEEDED`. Both block settlement and further paid calls for that trigger while leaving the original episode `PENDING` for human review. No synthetic gap interpolation or broker order APIs were added. This corrective check exists only on stacked Draft PR #82; original Draft PR #26 is unchanged.
