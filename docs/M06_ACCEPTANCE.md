# M06 Acceptance — Strategy Challenger Operations

Status: **BUILDER PASS / CONTROL ROOM PENDING**

## Authority

- Accepted `main`: `0e7b41c2be9ead1e25d3b0b23138a32acfdb24d1`
- Work branch: `work/m06-challenger-operations`
- Rejected predecessor: `01a46a16d5e501ad62de2802055243d8edfb55a8`
- Runtime-tested repaired source head: `74f10564b559701640d877d589e759233732b90b`
- M00-M05: ACCEPTED
- M06: BUILDER PASS / CONTROL ROOM PENDING
- M06 merge: NO
- Research implementation: BLOCKED pending Control Room M06 acceptance

Builder does not accept or merge M06.

## Final repair closure

The six Control Room blocker areas are closed in source and retained evidence:

1. Backtest recovery supports `UNCONFIRMED`: `PREPARED -> FAILED`, `RUNNING -> UNCONFIRMED`, no automatic retry.
2. Durable `strategy_challenger_retirements` journal records retirement ID, before/after status, expected manifest SHA, state, evidence path, and timestamps.
3. Retirement is transactionally blocked by active same-Challenger backtest or promotion; retirement-first causes later promotion/backtest creation to fail closed.
4. M06 Backtest is observational and frozen to the retained Challenger source request. Different symbol, relative symbol, timeframe, date window, tick model, deposit, or leverage is rejected.
5. A new real Strategy Optimizer job ran through MT5 and normal M03 registration.
6. That legitimate Challenger was backtested and retired through M06 while the current Champion remained unchanged.

## Real cumulative M01 -> M03 -> M06 lifecycle

Fresh Optimizer job:

`20260923_111101_385d43be`

Result:

- real MT5 native optimization;
- terminal result: `STRATEGY_CHALLENGER_FOUND`;
- deterministic eligible winner: round 1 / MT5 pass 11;
- Scientist calls: 0;
- registered Challenger: `STRAT-20260923-111101-R01-P11`;
- Champion mutation: NONE.

The job is new and does not reuse accepted M03 job `20260922_120735_07989e09`.

## Real M06 retained-contract backtest

Backtest:

`BT-20260923-111228-1d6f32f2`

Contract:

- Challenger: `STRAT-20260923-111101-R01-P11`;
- symbol: `XAUUSD.m`;
- Relative Value symbol: `EURUSD.m`;
- timeframe: `M30`;
- window: `2026.08.01 -> 2026.08.15`;
- model: 1 minute OHLC;
- deposit: 10000;
- leverage: 100;
- contract authority: `RETAINED_SOURCE_REQUEST`;
- optimization: 0;
- parameter mutation: NONE;
- Scientist calls: 0;
- Live authority: NONE.

Result:

- state: `COMPLETED`;
- execution truth: `MT5_STRATEGY_TESTER`;
- MT5 return code: 0;
- report bytes: 82542;
- report SHA-256: `4cc424b1e2fdd2488fd20a3c589d57e979256ce93993d6d271dfd9b509540e93`;
- compiled EX5 SHA-256: `54f1aec8955e54a38af50caf5667fd5b20712a8c4e5ded1cdf1584c99ffbf8e8`;
- retained source manifest SHA-256: `c9017af91bbe32a4cf540cd22d295d1ce737f5845a7b86d08bf60f3fc35571c9`.

## Real M06 retirement

Retirement:

`RETIRE-20260923-111338-0db91bc5`

Result:

- journal state: `COMMITTED`;
- before: `CHALLENGER`;
- after: `RETIRED`;
- expected manifest SHA-256 matches the backtest/Challenger source authority;
- 11 retained bundle files remained hash-identical;
- completed backtest history remained retained;
- no destructive delete.

## Champion preservation

Before fresh Optimizer:

`STRAT-20260922-120735-R01-P11`

After fresh Optimizer + M03 registration + M06 Backtest + M06 Retirement:

`STRAT-20260922-120735-R01-P11`

Champion unchanged = PASS.

## Recovery and race proof

Retained recovery probe proves:

- PREPARED restart -> FAILED;
- RUNNING restart -> UNCONFIRMED;
- automatic relaunch count = 0.

Retained race probe proves:

- active backtest blocks retirement;
- active promotion blocks retirement;
- retirement-first blocks later promotion;
- retirement-first blocks later backtest.

## Executable verifier

`scripts/verify_m06_evidence.py`

Verified against Git evidence commit `3003e47`:

`M06 EVIDENCE VERIFIER: PASS`

The verifier fails closed on:

- wrong base/status/schema;
- reuse of the old accepted M03 optimizer job;
- missing/untracked/ignored evidence;
- Challenger manifest/file-set/hash mismatch;
- non-MT5 or mutated backtest evidence;
- retained-contract mismatch;
- missing/invalid retirement journal;
- Champion mutation;
- invalid restart recovery;
- missing race-safety proof.

## Cumulative regression

- M03 + M04 + M06 targeted regression: 53/53 PASS.
- full backend pytest: PASS.
- `pip check`: PASS / no broken requirements.
- frontend: 28/28 PASS.
- frontend lint: 0 warnings / 0 errors.
- frontend production build: PASS.
- `npm audit --audit-level=high`: 0 vulnerabilities.

Final raw/normalized evidence is retained under:

- `evidence/optimizer/20260923_111101_385d43be/`
- `artifacts/strategy_challengers/STRAT-20260923-111101-R01-P11/`
- `evidence/m06/challenger_backtests/BT-20260923-111228-1d6f32f2/`
- `evidence/m06/challenger_retirements/RETIRE-20260923-111338-0db91bc5/`
- `evidence/m06/lifecycle/`
- `evidence/m06/acceptance.json`

## Final builder status

`M06 = BUILDER PASS / CONTROL ROOM PENDING`

`main = UNCHANGED`

`M06 merge = NO`

`Phase-1 cumulative E2E = BLOCKED_PENDING_CONTROL_ROOM_M06_ACCEPTANCE`

`Research implementation = BLOCKED`

Next authority action:

`STOP — CONTROL ROOM MUST AUDIT/ACCEPT/MERGE M06`
