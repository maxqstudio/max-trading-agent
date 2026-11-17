# Canonical M03 B — SIMULATED paper order intents and recovery

Pure software model only: no MetaTrader5 module, no broker order_send,
no order_check, no broker execution, and no currency P&L. The fake broker
is an exact in-memory Python type, not an adapter to a real MT5 account.

Paper simulation requires already-local HMAC-signed snapshot,
explicit proposal JSON and risk policy JSON as documented in C03_DEMO_PREFLIGHT.md.

    python -m max_trading_agent paper-demo --snapshot .local/snapshot.json --proposal .local/proposal.json --policy .local/risk_policy.json --paper-db .local/paper_demo.sqlite3

Supported test behaviors: FILL, REJECT, TIMEOUT_AFTER_FILL, TIMEOUT_BEFORE_RECEIPT.
These are deliberately fabricated broker receipts to validate orchestration,
not quotes or actual broker acknowledgments.

An immutable request ID + payload+policy digest is written to private SQLite
BEFORE a fake send. Repeated calls never resend; a conflicting request ID is
blocked. If fake acknowledgement is missing, reconciliation reads only the
fake broker record; absent history remains UNCERTAIN. Restarting the CLI
creates a fresh empty in-memory fake broker, so an old pending receipt will
remain UNCERTAIN: the demo cannot assume any real fill.

Trading Room journal shows paper intent/reconciliation and an independent
NO_TRADE veto, without account IDs/HMAC fingerprints or balances.

Canonical AUTO_DEMO acceptance remains BLOCKED until owner-authorized
read-only terminal smoke, real margin/order checks, a carefully reviewed
MT5 DEMO executor, retcode/readback reconciliation and account-safety gates.
No LIVE feature is introduced. See Issue #39.
