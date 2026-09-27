# M03 Acceptance — Strategy Challenger

Status: BUILDER PASS / CONTROL ROOM ACCEPTANCE PENDING

## Authority

Base commit:
1165effe013dd1cc561875bf38444ea59e3d04b7

M00 = ACCEPTED
M01 = ACCEPTED
M02 carried recovery-accounting debt = CLOSED_IN_M03
M04 = BLOCKED_BY_CONTROL_ROOM_M03

## Carried M02 debt closure

The prior CALL_IN_FLIGHT checkpoint could overstate provider certainty by recording an actual provider call before execution was provable.

M03 repairs this with explicit provider-call state:

- NOT_STARTED
- UNCONFIRMED
- CONFIRMED

Only confirmed execution contributes to actual/confirmed provider call counters.

Recovery from an unconfirmed provider window:

- does not retry the provider;
- records confirmed_provider_calls = 0;
- records unconfirmed_provider_attempts explicitly;
- records actual_llm_call = false;
- uses deterministic fallback.

## Real M03 acceptance

Real optimizer job:
20260922_120735_07989e09

Frozen market:
- Main symbol: XAUUSD.m
- Relative reference symbol: EURUSD.m
- Timeframe: M30
- From: 2026.08.01
- To: 2026.08.15
- Native optimizer: Slow Complete
- Scientist: OFF
- Max rounds: 1
- Selected parameter: InpMinConsensus
- Raw complete grid: 13 values

Frozen gates were not relaxed:

- PF >= 1.00
- RF >= 0.00
- Mean R >= 0.00
- Weighted R >= 0.00
- AUTO minimum trades = 14

Real winning round:
- Round: 1
- Parsed passes: 13
- Eligible passes: 13
- Deterministic winner: MT5 pass 11
- PF: 3.10737
- RF: 2.142653
- Mean R: 0.3687204586937974
- Weighted R: 0.4026980117685243
- Trades: 18
- Required trades: 14
- XML SHA-256: c269a31fdabc2d4f4adac010894a96ea344927ba015b55861ae0bd8cde5fe30f
- Weighted-R sidecar SHA-256: 3f0fcc5e2307f1fef7e7d9f99b3e63507cf924fa4978f0be0626286af48e2629
- Run nonce: 847156689

Registered Challenger:
STRAT-20260922-120735-R01-P11

Required invariants verified:

- exactly one Challenger for source winner;
- repeated registration returns same Challenger ID;
- repeated registration preserves same manifest SHA;
- Challenger bundle integrity VERIFIED;
- winner params == Challenger EA defaults for all 16 params;
- winner params == Challenger SET for all 16 params;
- Challenger EA defaults == Challenger SET;
- all Challenger SET optimization flags are N;
- baseline EA SHA remains 9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345;
- baseline status remains BASELINE_NOT_CHAMPION;
- Strategy Champion remains NONE;
- Champion mutation = NONE;
- retained M01 no-winner job creates 0 Challenger;
- retained M02 no-winner job creates 0 Challenger;
- no promotion endpoint/action exists.

## Mutable authority

SQLite schema version 3 adds only:
strategy_challengers

Unique source identity:
(source_job_id, source_round, source_pass)

Visible Challenger status:
CHALLENGER

Registration staging status:
REGISTERING

JSON remains immutable evidence only and is not a competing mutable registry.

## Owner UI

RUN_MAX.cmd remains the single Owner entrypoint.

Owner navigation:
- Overview
- Optimizer
- Challengers

The Challenger page provides:
- dense registry table;
- source job/round/pass;
- real KPI;
- integrity status;
- Challenger detail;
- full 16-parameter baseline-vs-Challenger comparison;
- bundle hashes and provenance.

There is no Promote action and no Champion mutation endpoint in M03.

## Retained evidence

Primary real evidence:
evidence/m03/real_mt5_challenger/

Verification:
evidence/m03/verification/

Acceptance authority:
evidence/m03/acceptance.json

Candidate-object verifier:
scripts/verify_m03_evidence.py
