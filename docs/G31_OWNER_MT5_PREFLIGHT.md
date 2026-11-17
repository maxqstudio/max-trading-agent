# G31 — Owner Windows MT5 read-only preflight and post-merge GitHub CI

**This is a preparation workflow, not live MT5 acceptance.** G30 already replayed 30 temporary merges on GitHub, but the owner still controls each actual PR merge. G31 adds a separate read-only workflow to test the final `main` after those PRs are merged, and a zero-contact readiness check for the owner's Windows PC.

## Before using the owner's PC

First merge and retarget the Draft PRs **in dependency order**: #14, #16, …, #72 and G31 #74. Follow [G28 Owner Handoff](G28_OWNER_HANDOFF.md). After each predecessor merges, retarget the next PR's base to `main` and inspect the diff. Do not run a real MT5 acceptance from an unmerged feature branch. Re-run existing full CI and the G31 `Postmerge main CI and local MT5 prerequisites` workflow on the exact final `main` SHA.

The new workflow is designed to run on both `main` and the G31 feature branch. A successful run on the feature branch is explicitly labeled **`BRANCH_REHEARSAL_ONLY`**, even when all Linux/Windows tests pass. Only an actual `push` to `main` with a matching SHA can emit `MAIN_POSTMERGE_CI`. Both cases retain `real_mt5_terminal_tested=false`, `owner_acceptance=PENDING`, `decision=NO_TRADE`, `execution=DISABLED`. They use no MT5 installation/account or broker credentials.

## Windows owner — safe offline preflight

Once all approved PRs are merged to `main` and the owner has an authorized local MT5 terminal:

1. Open a Windows terminal in your freshly updated, clean local `main` checkout. Ensure Python 3.10+ and official MetaTrader5 Python distribution are installed **in the same environment**. The repository's existing Python suite must already pass. Keep the MT5 account configured *in the desktop terminal*; do not place account credentials in this project.
2. With GitHub credentials configured by you, run `git fetch origin main` and `git status --short --branch` **yourself**, separately from this script. `git fetch` updates only remote references, but is a network operation the preflight deliberately never performs.
3. Run the safe probe from the repository root:

   ```powershell
   python -m tools.owner_mt5_preflight --repo .
   ```

   This runs **no** `MetaTrader5.initialize()`, `login()`, `order_send()`, `order_check()` or market query. It does not import the MetaTrader5 module, create the HMAC key, contact GitHub, start MT5, read terminal/account details, or write broker secrets. It only checks Windows, Python, installed distribution **metadata**, clean `main` checkout, equality of the **locally cached** `origin/main` SHA and HEAD, and the required G31/M09 source files. Results list safe booleans, a SHA and missing prerequisite codes; no file paths or account/server identifiers.

   `LOCAL_PREREQUISITES_PRESENT_LIVE_TEST_PENDING` (exit **0**) means the local prerequisites are present, **not** that the terminal is reachable. `LOCAL_PREFLIGHT_NOT_READY` (exit **3**) means fix the listed blockers. An unexpected invalid input/runtime failure is blocked. A cached local `origin/main` may still be stale without the owner's separate `git fetch`.

4. If the preflight succeeds and the desktop MT5 terminal is already open/configured, the owner may deliberately invoke the **existing M09 read-only CLI** (the preflight will **never** trigger this command automatically):

   ```powershell
   python -m max_trading_agent mt5-acceptance --symbol EURUSD --timeframe M15 --bars 100 --samples 3 --sample-interval 2 --identity-key-file ".local/mt5_identity.key" --journal ".local/agent_events.sqlite3"
   ```

   Use a broker-visible symbol and a market session with recent ticks. This step creates/uses a **private local HMAC key and event journal**, opens and shuts down a local read-only MT5 API bridge, and does **not** place an order or log in to MT5. Keep those files **only on the local PC**. Never upload any raw terminal snapshot, account information, key, journal or credentials to public GitHub. The journal remains private. The generated HMAC is **not** broker/exchange attestation.

5. Interpret the result strictly: **`OBSERVED_CONSISTENT` (exit 0)** = several local feed samples passed checks but still `owner_acceptance=PENDING`; **`INCONCLUSIVE` (exit 3)** = inadequate live movement/data, common outside market hours; **`BLOCKED` (exit 2)** = invalid evidence/connection. Do not promote a strategy, broker mode or generated Python tool based only on this result. Review the [M09 acceptance contract](M09_TERMINAL_ACCEPTANCE.md) and record owner approval separately after independent source/security review and risk testing.

## What G31 proves—and does not prove

The GitHub-hosted read-only postmerge workflow reruns complete Python unit/security tests on Linux and Windows, JS/Trading Room tests on Linux, trusted R0 reference checks, and **actual isolated Docker** G21 independent numerical review with a rejected incorrect tool. Reports are uploaded with source SHA, branch/main scope, operating system and the **explicit statement that no real MT5 feed was tested**.

A green G31 feature-branch workflow does **not** mean final `main` was tested. A green future `main` workflow still does **not** establish mathematical strategy performance, order fill behavior, independent reviewer acceptance, live broker readiness, profitability or permission to trade. No generated code gets T1 admission; no DEMO/LIVE authorization is introduced.

Source [G31 Issue #73](https://github.com/maxqstudio/max-trading-agent/issues/73). Owner handles PR merge, local MT5 account, actual feed acceptance and any explicit future permission decisions. Until then `NO_TRADE` and `execution=DISABLED` remain mandatory.
