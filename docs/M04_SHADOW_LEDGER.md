# M04 — Forward-only SHADOW ledger (NO real trading)

**What it does:** Record an explicitly selected hypothetical LONG/SHORT candidate **before** an outcome is known, then settle it when a **later** read-only market snapshot arrives. Persist the episode and stage-by-stage journal events, visible in the Trading Room.

**What it does not do:** Send MT5 orders, alter a brokerage account, create a real order/deal/position, prove LLM strategy accuracy, or execute shadow trading on a running scheduler. The default remains STOP/NO_TRADE outside explicitly invoked research operations.

## Example: two independently observed private snapshots

Use the M01 `mt5-probe` command on an authorized computer to capture entry and (later) exit snapshots privately. These files contain sensitive account information; **never commit or upload them publicly**. A supplied JSON file is not cryptographically authenticated as an MT5 response.

```sh
python -m max_trading_agent shadow-open \
  --market-db .local/market_evidence.sqlite3 \
  --journal .local/agent_events.sqlite3 \
  --candidate research_0001 --snapshot /private/entry.json \
  --side LONG --slippage 0.00002 --fee-per-side 0.00001

# After a NEWER observed quote for the same symbol:
python -m max_trading_agent shadow-settle \
  --market-db .local/market_evidence.sqlite3 \
  --journal .local/agent_events.sqlite3 \
  --candidate research_0001 --snapshot /private/later.json

python -m max_trading_agent shadow-show \
  --market-db .local/market_evidence.sqlite3 \
  --candidate research_0001
```

Quotes must be fresh (maximum 30 seconds between snapshot/quote and current clock), UTC-aware and strictly forward in time. Entry uses **ask + adverse slippage** for LONG or **bid − adverse slippage** for SHORT. Exit uses **bid − slippage** for LONG or **ask + slippage** for SHORT. Two per-side fees are subtracted from hypothetical P&L. Results use *price difference per ONE asset unit*, **not** broker account currency, real profit or guaranteed executable quotes.

A candidate ID is an idempotency key. Reusing an ID with a different snapshot or direction is rejected; duplicate identical settlement is accepted without double-counting. A contradictory second outcome is rejected.

The database labels every episode `SHADOW`, `HYPOTHETICAL`, `broker_execution=false`. No order path is present. The UI may show `SHADOW_OPENED` and `SHADOW_SETTLED` with evidence, but this is not a broker's execution confirmation.

## Evidence-quality limitations

A local JSON file can claim to originate from MT5. It may be fabricated or modified; only direct authenticated terminal capture can establish stronger provenance. CI uses fake snapshots. To evaluate a strategy forward, log all eligible hypotheses including NO_TRADE and failed cases *before* outcomes, sample over many regimes, and measure costs and calibration. This M04 slice is the ledger, **not** an autonomous signal-selection or profitability-validation system.

## Acceptance

- Source identity and UTC freshness checked, wrong symbol or reversed event timestamps rejected.
- Idempotent write / single settlement and restart recovery tested with SQLite.
- Price/fee/slippage monotonicity; no P&L in account currency.
- Independent NO_TRADE policy event always written. GitHub Actions runs only synthetic tests; no real MT5 calls or local CI.

## Forward-observation chronology hardening (G35 stacked tip)

SHADOW settlement now rejects a quote timestamp unless it is **strictly later than both** the recorded entry quote **and the persisted candidate `created_utc`**. An outcome quote newer than the original quote but already observed before the candidate was recorded cannot manufacture a forward-looking result. The creation timestamp uses the ledger's validated observation clock, allowing reproducible synthetic tests. The CLI end-to-end test generates a fresh outcome **after** recording the candidate; watcher integration fixtures similarly generate a new observed snapshot only when it is consumed. Negative tests prove a retrospective outcome leaves the candidate PENDING. All settlement remains hypothetical and `NO_TRADE`; local clocks and user-provided snapshot JSON are not cryptographic proof of authentic broker observation. Original PR #20 is unchanged; this safety repair lives in final stacked PR #82.
