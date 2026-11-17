# M05 — Autonomous Read-Only Market Watcher

**Explicit limitation:** This watcher does not make executable trade decisions. It can autonomously monitor a local MT5 terminal and optionally ask the research team for **unverified LLM analysis** after a fresh completed candle appears. It **never** opens/shuts/edits MT5 orders, creates LIVE/DEMO fills or claims successful investment performance.

## Usage on an authorized terminal (NOT GitHub Actions)

1. Open and configure MT5 on your own Windows computer; enable the exact desired symbol in Market Watch.
2. Install the Python MetaTrader5 package according to the vendor's instructions.
3. Start a local watch process explicitly:

```powershell
python -m max_trading_agent watch --symbol EURUSD --timeframe M15 --bars 100 --interval-seconds 15
```

The watcher continues polling until Ctrl+C. It is **not** a background service hosted by ChatGPT or GitHub Actions and must be running on a machine with MT5 access. First successful poll sets a **baseline** and does not backdate a trading opportunity. Each next correctly spaced completed bar creates exactly one research job; a revised same-time candle, clock regression, feed gap or broker account-mode change blocks the trigger. Weekend gaps are reported and skipped instead of manufacturing interpolated bars.

By default **LLM is completely disabled**. To explicitly enable up to **four provider calls per new completed candle** for the M02 Market/Bull/Bear/Manager sequence, configure `MAX_TRADING_LLM_ENDPOINT`, `MAX_TRADING_LLM_MODEL`, and when required `MAX_TRADING_LLM_API_KEY` locally, then pass `--enable-llm-research`. This may incur real provider costs. Every role remains **SIGNAL research only**; order execution is absent and final decision remains `NO_TRADE`.

For bounded smoke in an authorized terminal, add `--max-polls 3`. In CI, fake MT5 and fake LLM models are used; no local CI owner machine is touched.

## Event contract and durability

The existing SQLite EventJournal tracks `WATCH_STARTED`, `WATCH_BASELINED`, `WATCH_NEW_COMPLETED_CANDLE`, `WATCH_CANDLE_GAP`, `WATCH_HISTORY_REVISED`, `WATCH_ACCOUNT_MODE_CHANGED`, optional `WATCH_UNCHANGED` heartbeat and `RESEARCH_COMPLETE`/`RESEARCH_BLOCKED`, ending with `WATCH_STOPPED`.

A private `watch_state` table stores the latest completed bar, checksum and account mode. `watch_jobs` stores a deterministic job ID and bounded state machine. A **single-watcher lease** prevents two local processes from researching the same symbol/timeframe at once. A crash with a `RUNNING` billable job puts it into `REVIEW_REQUIRED` on a later lease reclaim: it is **never automatically paid/retried twice**.

Risk caveats:
- The account's DEMO/REAL trade mode is monitored, **not** a cryptographically authenticated account identity. Switching between two accounts of the same mode cannot yet be reliably detected; that must be addressed before any future trade-capable system.
- Persistent observation records contain **market-only hashes/timestamps**, not raw account balances, login/server, positions, or API keys. Some quote/symbol values are still financially relevant and must remain in the private local database, not a public repo.
- The watcher does not validate strategy profitability. Its LLM research opinion is not a signal to execute.
- There is no automatic resumption on boot; no guarantee that remote/cloud MT5 instances work without a licensed, installed terminal.
- Fatal lease loss and unexpected exception stop monitoring; they do not imply a successful action. GitHub Actions validates mocks, not live MT5 uptime.

## Vendor reference
[MetaQuotes Python copy_rates_from_pos](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrompos_py): bar index 0 is the unfinished current bar; index 1 is the most recently completed bar. The MT5 adapter reads from index 1 and the watcher detects a changed latest completed bar timestamp.
