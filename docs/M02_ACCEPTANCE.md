# M02 Acceptance — Bounded LLM Scientist Optimizer Advisory

## Authority

Base commit:
6b2644c1f997fb4da366fefb87e0924b0fb6c46d

Legacy source:
D:\MAX_MTF\MAX_MTF_v2_0_1
READ ONLY

EA baseline SHA-256:
9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345

M00 = ACCEPTED
M01 = ACCEPTED
M02 = BUILDER PASS / CONTROL ROOM ACCEPTANCE PENDING
M03 = BLOCKED_BY_CONTROL_ROOM_M02

## Implemented boundary

M02 adds only the no-winner advisory branch:

completed real MT5 round
-> deterministic parse / eligibility / ranking
-> no eligible winner and another round remains
-> optional bounded Scientist proposal
-> deterministic validation
-> accepted proposal OR accepted M01 deterministic_refine() fallback
-> next real MT5 native optimization round

Scientist remains advisory only.

No Challenger registry, promotion, Champion mutation, Scientist Chat, Knowledge, Python sandbox, tool calling, model research, or trading capability was added.

## Provider / secret contract

Frozen non-secret route identity:
- provider: gemini
- base URL: https://generativelanguage.googleapis.com/v1beta/openai/
- model: gemini-3.5-flash
- api_key_env: COMPLEXPOLICY_LLM_API_KEY
- timeout: 60 seconds
- proposal temperature: 0.10

The actual credential was resolved only in process memory for the real acceptance run.
It is not stored in SQLite, request JSON, evidence, frontend state, logs, or Git.

Actual secret scan across SQLite and evidence:
0 matches.

## Real acceptance job

Job:
20260922_101450_dc549869

Frozen market contract:
- Main symbol: XAUUSD.m
- Relative symbol: EURUSD.m
- Timeframe: H1
- From: 2026.08.01
- To: 2026.09.15
- Tick model: 1 minute OHLC
- Native optimization mode: Slow Complete
- Selected parameter: InpEntryThreshold
- Initial range: 0.18 / 0.02 / 0.20
- Max rounds: 2
- Scientist advisory: ON

### Round 1

Real MT5 native optimization:
PASS

Parsed passes:
2

Eligible passes:
0

Winner:
NONE

XML SHA-256:
0a97cefe673e257db7b7a3325ae74fda18fc16a3a4b65b981c3459ad5512882c

Weighted-R sidecar SHA-256:
0b7076a7a0127090ae2c7951d37c6e06abedee5fbd5bc2f4acf5af9af35a0641

Run nonce:
536196072

## Real Scientist transition

Source round:
1

Target round:
2

Mode:
SCIENTIST_PROPOSAL

Actual provider call:
YES

Validation:
ACCEPTED

Proposed selected range:
InpEntryThreshold = 0.18 / 0.02 / 0.60

Effective selected range:
InpEntryThreshold = 0.18 / 0.02 / 0.60

Request payload SHA-256:
abefd0ebec2945d192edc91dc3a01fa09451a59834f5a8c308e547015129a05e

Raw response SHA-256:
f8f4cb0f068900ebcd4703bc2d0c4f56de5f1d0acf8944082d980e0f3da519d1

Normalized response SHA-256:
233169fff869eea1e90bf843addf3c0a27b2dc54cf049f517984ccaf5b2ba23b

Provider-reported usage:
- prompt_tokens: 734
- completion_tokens: 132
- total_tokens: 1777

Provider-reported cost:
unavailable / null

Fallback route used:
NO

The retained request contains only bounded optimizer evidence.
It contains 2 top passes because Round 1 produced only 2 passes.
No full XML, sidecar, filesystem state, Champion authority, or secret was sent.

## Round 2

The accepted Scientist range was written into the real Round 2 .set file.
Retained replay verifies exact range parity and optimization flag Y.

Real MT5 native optimization:
PASS

Parsed passes:
22

Eligible passes:
0

Winner:
NONE

XML SHA-256:
93b2030eb36c764905963c3e901b3632c030dbf54e95acb1ec9a20bba13bd6d3

Weighted-R sidecar SHA-256:
c59f7038599bde47a9e5e72962266334db1c73904e04e9d55b2472aed03ea289

Run nonce:
1872494270

Terminal result:
NO_ELIGIBLE_WINNER_MAX_ROUNDS

## Critical regression gates

Eligible winner -> Scientist call count 0:
PASS

Max-round exhausted -> Scientist call count does not increase:
PASS

Scientist OFF -> accepted M01 deterministic refinement:
PASS

Illegal/out-of-bound proposal -> no clipping -> deterministic fallback:
PASS

Extra/missing selected parameter -> deterministic rejection/fallback:
PASS

Authority escape fields -> deterministic rejection:
PASS

Malformed JSON -> deterministic fallback:
PASS

Missing route / credential -> deterministic fallback:
PASS

Timeout / provider failure -> deterministic fallback:
PASS

Committed decision restart -> no duplicate call and same effective ranges reused:
PASS

Unconfirmed call restart -> fail-safe deterministic fallback without retry:
PASS

Real application restart after acceptance:
- status remained NO_ELIGIBLE_WINNER_MAX_ROUNDS
- proposal transitions remained 1
- actual provider calls remained 1
- Challenger remained 0
- Champion mutation remained NONE

The restart was intentionally performed without re-injecting the credential.
Route status therefore became MISSING_CREDENTIAL for future jobs, which is legal and does not alter the frozen completed job.

## Database

Schema remains:
2

No new Scientist table was required.

M02 transition checkpoint is stored in optimizer_rounds.state_json.

Accepted M01 job:
20260922_081129_e3902b3d

Accepted M01 job and round rows before/after migrate_m02():
unchanged.

Single active optimizer invariant:
PASS

## Web UI

RUN_MAX.cmd:
PASS

Overview regression:
PASS

Optimizer regression:
PASS

Scientist OFF/ON toggle:
PASS

Provider/model/credential status:
PASS

No raw credential input/value:
PASS

Accepted/rejected/fallback provenance:
PASS

Proposed vs effective range display:
PASS

No Challenger or promotion UI:
PASS

No milestone/build identifier rendered in Owner UI:
PASS

## Evidence

Retained real path:
evidence/m02/real_scientist_mt5/20260922_101450_dc549869

Real manifest:
evidence/m02/real_scientist_mt5/manifest.json

Verification:
evidence/m02/verification

Integrity verifier:
scripts/verify_m02_evidence.py

M01 verifier remains authoritative and must pass against the final candidate.

## Builder conclusion

M02 implementation and real evidence satisfy the bounded Scientist advisory contract.

M03 remains blocked pending independent Control Room M02 acceptance.
