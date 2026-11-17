# Canonical M03 — Offline AUTO_DEMO risk preflight (not demo execution)

This is a **deterministic rejection-only contract**. It does **not** connect to MT5, call `order_calc_margin`, `order_check`, `order_send`, `symbol_select`, or change any broker state. M01 real terminal acceptance is **PENDING**; no Owner DEMO or LIVE trading is authorized.

Future, only when explicitly run by the owner on a private computer with a separately captured/signed JSON snapshot:

```sh
python -m max_trading_agent demo-preflight --snapshot .local/snapshot.json --proposal .local/proposal.json --policy .local/risk_policy.json --identity-key-file .local/mt5_identity.key
```

Proposal JSON: `{"request_id":"candidate_00000001","symbol":"EURUSD","side":"LONG","volume":0.1,"stop_loss":1.049,"take_profit":1.053}`.

Risk policy JSON: `{"allowed_symbols":["EURUSD"],"max_risk_fraction":0.02,"max_spread_points":40,"max_positions":2,"max_orders":2,"kill_switch":false}`.

The validator requires a signed local snapshot, recent bid/ask and capture times, exact DEMO account mode, terminal/account permissions, symbol and volume specifications, stop distance, bounded spread, exposure count and an independent hard risk fraction. Invalid/tampered snapshots fail closed. It returns **`decision=NO_TRADE` even when every static check passes** and lists pending checks for real broker margin, actual session status, native order_check, reconciliation and owner acceptance. The tick-value risk estimate is indicative and does not include commission, dynamic conversion, market execution or all margin rules; it cannot certify financial risk. The entire result excludes raw login, account fingerprint, password, key, balance and broker server. Output is not a ready-to-submit broker order.

Owner must separately approve and prove actual read-only terminal evidence, broker-specific calculations and safe idempotent/reconciled DEMO execution before true AUTO_DEMO. Generated LLM prose or RAG citations never modify this hard gate. GitHub Actions runs synthetic signed fixtures on Linux/Windows only.

Acceptance and remaining gates: [Issue #31](https://github.com/maxqstudio/max-trading-agent/issues/31).
