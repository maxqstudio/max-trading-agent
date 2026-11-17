# M02 — LLM research roles, not broker trading

**Scope:** Market Analyst → Bull Researcher → Bear Researcher → Research Manager. Multiple role invocations may share one model provider. Each real invocation is journaled and displayed in the Trading Room after the run.

## Local, opt-in setup

1. Produce an MT5 snapshot using the M01 \`mt5-probe\` command. Save the response to a **private** JSON file outside git. It may contain account balances and open positions.
2. Set \`MAX_TRADING_LLM_ENDPOINT\` to a provider \`https://.../chat/completions\` endpoint (or \`http://127.0.0.1:.../chat/completions\` for a local server).
3. Set \`MAX_TRADING_LLM_MODEL\` to an exact provider model ID.
4. Set \`MAX_TRADING_LLM_API_KEY\` in the local environment only when the provider requires it.
5. Execute \`python -m max_trading_agent signal-research --snapshot /private/path/snapshot.json\`.
6. Open the Trading Room (\`python -m max_trading_agent serve\`); the same local SQLite journal contains the named research role events and final \`NO_TRADE\`.

**Cost notice:** A run makes four real provider calls. No provider subscription or credentials are assumed in CI. Calls may incur charges. The agent never sends your account IDs/balances/positions in the LLM payload: only the symbol, timeframe, bid/ask, completed OHLC and timestamps are forwarded. In this stage the role sequence is fixed; future supervisors will make it adaptive.

**No factual/alpha claim:** Role debates are hypotheses. This milestone has no independently validated strategy experiments, execution-cost simulations or broker order capability. Even if the LLM proposes BUY or claims a high win rate, the independent guard records \`NO_TRADE\`.

**Input source warning:** Local snapshot JSON is user-supplied data that declares MT5 origin. The program does not cryptographically prove who generated a supplied file. Do not use \`signal-research\` results as evidence of real broker execution.

**CI:** Python fake-client tests exercise role ordering, prompt minimization, output journaling, failures, security boundaries and veto. GitHub Actions is the requested CI; owner terminal/API acceptance is separately required.
