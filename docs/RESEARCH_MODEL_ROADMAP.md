# MAX Research Model Roadmap

Status: CANONICAL RESEARCH ROADMAP
Implementation history: PREVIOUS-EPOCH R00 ACCEPTED; PREVIOUS-EPOCH R01 IMPLEMENTATION/RUNTIME REPAIR TARGETED REAUDIT PASS
Current source-governed Research status: FRESH EPOCH; NO RESEARCH PROJECT OR GATE; R00 NOT STARTED; R01 BLOCKED / NOT STARTED; R02 BLOCKED; final Owner-PC runtime state NOT_PROVEN

This document is the canonical milestone roadmap for MAX model research. The previous runtime epoch's R00 was accepted with source audit PASS and Owner-PC runtime acceptance PASS. Its source/runtime lineage is `5a1c303b0b901e7b370e2606aabc8cceed228a32`, Research ID `RSRCH-653cc6cff84e14f835ef764f`, parent Champion `STRAT-20260924-115344-R01-P8912`, and immutable execution snapshot with Research H1 minimum 4 trades/month. These identities and acceptance results are historical only and are not current source/runtime authority for the migrated repository. The fresh epoch has no Research project or gate; current-epoch R00 has not started, R01 is blocked/not started, and R02 is blocked. The current Research sample requirement is a separate persistent Owner configuration and may be edited without rewriting historical R00 or any running execution snapshot. The merged R00 baseline is `3e224c8b6a493fadd1dabeba6b76bd9eb1f6bb7a`. The previous-epoch R01 implementation/runtime repair received Control Room targeted re-audit PASS; no current-epoch R01 scientific run or result is proven. Model training, ONNX production, Research Challenger creation and Champion mutation remain not started.

## Non-negotiable governance

**AUTOMATION INSIDE THE GATE. HUMAN AUTHORIZATION BETWEEN GATES.**

**SYSTEM DETERMINES SCIENTIFIC QUALIFICATION. SYSTEM MAY RECOMMEND. OWNER DETERMINES WHICH QUALIFIED CANDIDATES ADVANCE.**

There is no autonomous Research -> model winner -> Challenger chain. Every gate is bounded, persists its result, stops, and waits for an explicit Owner action before the next gate can open.

At every gate with multiple candidates:

```text
ALL RESULTS
    |
HARD PASS / FAIL
    |
QUALIFIED POOL
    |
SYSTEM RECOMMENDATION
    |
STOP / WAITING OWNER
    |
OWNER SELECTION
```

The Owner may select one, several, or all qualified candidates, subject to explicitly frozen compute limits for the next gate. A scientific FAIL remains FAIL and cannot be converted to PASS by Owner selection or Scientist recommendation.

## Previous-epoch R00 accepted entry prerequisites

The previous-epoch R00 started only after all of the following were satisfied for its accepted Research identity:

1. M08 is accepted and merged.
2. The required Owner cumulative Strategy E2E is canonically proven PASS or explicitly satisfied by Owner/Control Room authority.
3. An exact frozen parent Strategy is available.
4. The Owner explicitly authorizes R00.

The accepted R00 runtime persisted an explicit Owner assertion of cumulative Strategy E2E satisfaction together with the exact verified parent identity. Source tests alone did not substitute for that persisted authorization. These prerequisites are now historical R00 acceptance evidence and do not authorize R01.

## Research parent authority

Research starts only from an Owner-selected Strategy Champion or other explicitly Owner-authorized parent Strategy. A research parent is immutable for one research identity and must bind, where applicable:

- Strategy ID and complete parameter manifest.
- EA SHA-256 and EA semantic version.
- Strategy contract `MAX_TRUE_MTF_DYNAMIC_V1`.
- Feature contract `CP32_TRUE_MTF_V1`.
- MTF resolver version.
- Context / Structure / Main / Timing geometry.
- Main timeframe.
- Main symbol and relative symbol.
- SL, TP, MaxHoldBars and deterministic risk semantics.
- source/broker/feed/data identity.
- exact parent-manifest hash and creation/authorization timestamps.

Research must never operate against an anonymous parent or a mutable “current” Strategy reference.

## Model purpose and runtime authority

The research model is decision intelligence layered on the deterministic Strategy. It may output only:

```text
0 = SELL
1 = SKIP
2 = BUY
```

The model never owns account risk, lot sizing, SL, TP, MaxHoldBars, daily drawdown limits, or broker execution constraints. Those remain deterministic EA authority.

## Dataset and feature authority

`CP32_TRUE_MTF_V1` remains the feature contract unless a future explicit feature-contract milestone replaces it.

The EA / canonical Strategy implementation is feature-semantic authority. Python may reproduce those features only by an exact, tested contract; it must not create “approximately equivalent” features silently.

Every sealed dataset must bind at minimum:

- research ID and parent Strategy identity;
- EA SHA and version;
- Strategy contract and feature contract;
- MTF resolver and exact timeframe geometry;
- symbol and relative symbol;
- date range;
- broker/feed/source identity;
- source-data hashes;
- feature-data hash;
- row count;
- label contract;
- label horizon and full dependency horizon;
- dataset-builder version and deterministic provenance.

Dataset mutation after sealing creates a new dataset identity.

## Label and leakage governance

A label contract must be frozen before training. Future information may be used only to construct targets. Future information must never enter model features.

The mandatory adversarial leakage suite must test at least:

- centered or future-derived feature labels;
- future MTF bars;
- future relative-symbol bars;
- look-ahead normalization;
- future-filled market data;
- scalers/preprocessors fitted on validation or OOS;
- direct or indirect target contamination;
- fold overlap through label dependency;
- temporal sequences bridging purged CPCV gaps;
- hybrid policy training from in-sample temporal predictions;
- Locked OOS feedback entering Discovery;
- Fresh/Forward feedback entering tuning of the same research snapshot.

Purge and embargo are derived from the actual dependency horizon, including sequence/history and label dependencies. Arbitrary constants are not scientific authority.

## Model universe and staged expansion

Model families are staged rather than enabled all at once.

Baseline discovery starts with:

- LightGBM;
- XGBoost.

Optional control:

- Random Forest.

Temporal competitors may be opened only after dataset, sequence, causality, resource and runtime-kernel proof:

- GRU;
- PatchTST;
- causal Transformer Encoder.

Later evidence-justified expansion may include:

- LSTM;
- TCN;
- iTransformer;
- TFT;
- Transformer MoE.

No architecture is scientifically preferred by name. Every family must earn advancement from deterministic evidence.

## Dynamic model capacity

Model capacity uses:

```text
Executable Capacity =
min(
    LEGAL,
    RESOURCE,
    SCIENTIFIC
)
```

**LEGAL** covers implemented architecture/runtime/ONNX constraints.

**RESOURCE** covers CPU, RAM, GPU, VRAM, storage, training-time and inference-time budgets.

**SCIENTIFIC** covers effective training information, minimum fold rows, sequence length, label/dependency horizon, purge loss, OOF information and total dataset capacity.

There is no arbitrary global parameter-count hard ceiling. Larger models are legal only when all three capacity authorities support them. Current MAX EA runtime sequence-length constraints must be treated as deployment constraints and measured from the current runtime contract; old registry ceilings must not be copied blindly.

## Tabular, temporal and hybrid contracts

Standalone tabular:

```text
[1, 32]
    ->
SELL / SKIP / BUY
```

Standalone temporal:

```text
[1, T, 32]
    ->
SELL / SKIP / BUY
```

Generic hybrid:

```text
temporal input:
[1, T, 32]
    ->
P(DOWN), P(UP), directional score, confidence

policy input:
CP32 current row (32)
+ P(DOWN)
+ P(UP)
+ directional score
+ confidence
= 36 features

[1, 36]
    ->
SELL / SKIP / BUY
```

Hybrid policy training must use chronological out-of-fold temporal predictions only. A policy must never train on temporal predictions produced by a temporal model that fitted those same target rows.

## Persisted research state

Research authority is backend-persisted, not frontend-only. Future implementation must bind concepts equivalent to:

- `research_id`;
- `current_gate`;
- `gate_state`;
- `gate_input_manifest_sha`;
- `gate_output_manifest_sha`;
- candidate IDs;
- qualification states;
- system recommendation;
- Owner-selected IDs;
- Owner authorization ID;
- authorized UTC.

Gate terminal states must include the semantics:

- `PASS_WAITING_OWNER`;
- `FAIL_WAITING_OWNER`;
- `ERROR_WAITING_OWNER`.

A MAX restart cannot automatically continue. Example: `CPCV / PASS_WAITING_OWNER` remains exactly that after restart until explicit Owner authorization.

## R00 — Research Authority Foundation

Previous-epoch record: R00 was ACCEPTED after all four prerequisites above were satisfied. **No model training occurred in that epoch.** This acceptance does not initialize current-epoch Research or authorize a new gate.

Freeze:

- parent Strategy authority;
- feature-contract authority;
- label-design authority;
- research state machine;
- candidate identity scheme;
- artifact lineage;
- hardware profiler contract;
- Owner authorization contract;
- the exact Research KPI/sample authority consumed by the R00 execution.

Research H1 minimum trade/sample policy is a persistent Owner configuration with no hidden default and no Strategy Optimizer inheritance. R00 does not permanently freeze the mutable setting itself; when R00 starts, it snapshots the exact current positive H1 trades/month value into immutable Owner authorization and Research policy evidence. Later configuration edits apply only to future Research executions and cannot rewrite accepted or running snapshots. Research KPI hard-gate dimensions are frozen in R00, while numeric gate thresholds remain gate-scoped Owner authority that must be frozen before the first scientific qualification gate consumes them.

Previous-epoch terminal behavior: evidence persisted, then **STOP — WAITING OWNER**. That accepted runtime remained `PASS_WAITING_OWNER` as designed while Control Room/Owner governance closeout established R00 = ACCEPTED. The previous-epoch R01 implementation/runtime repair received targeted re-audit PASS; current-epoch R01 is blocked/not started and still requires a new explicit Owner authorization after a new current-epoch Research parent is established.

## R01 — Dataset + Label + Leakage Foundation

Status: **IMPLEMENTATION/RUNTIME REPAIR TARGETED REAUDIT PASS; REAL SCIENTIFIC RUN NOT STARTED**.

The previous-epoch targeted R01 implementation/runtime repair implemented the bounded R01 gate but did not execute a real scientific run. Any future runtime entry requires a separate explicit Owner R01 START authorization bound to that epoch's newly accepted R00 Research identity and frozen parent Strategy; the previous-epoch identity cannot authorize it.

Build and seal:

- MAX-owned verified Research source preparation under the canonical managed Research data root; production R01 does not accept an Owner-authored bundle path, broker/feed identity or guessed timezone;
- source identity bound to actual MT5 terminal/account authority, accepted-parent EA hash/version, Strategy/feature/resolver contracts, exact role geometry, symbols, point size, exact file SHA-256 values, coverage and creation provenance;
- causal dataset with immutable source-row identity;
- feature manifest for exact `CP32_TRUE_MTF_V1` ordering and provenance;
- independent Python CP32 reproduction with deterministic EA/Python parity evidence;
- chronology and data-quality evidence;
- exact current true-MTF role geometry with fully closed role bars;
- exact relative-symbol timestamp pairing over the full dependency window;
- parent-bound SELL / SKIP / BUY first-barrier label manifest;
- SL/TP/MaxHold, bid/ask, spread and time-exit semantics bound to the frozen parent;
- context-only handling for ambiguous same-bar TP/SL and incomplete future-horizon rows;
- full feature/MTF/relative/label dependency horizon;
- purge and embargo derived from the actual dependency horizon;
- explicit Owner Discovery / Locked OOS / Fresh Forward partition boundaries with no hidden defaults, no overlaps, no ungoverned gaps and no implicit timezone assignment; direct API timezone-naive input is rejected;
- deterministic row ownership: Discovery [from,to), Locked OOS [from,to), Fresh/Forward [from,to], with persisted row counts, first/last owned rows, overlap_count=0 and unassigned_count=0;
- full labels may be deterministically materialized and sealed internally for immutable dataset lineage, but pre-R07/R08 Owner/Scientist outcome-derived summaries are restricted to a versioned Discovery-only label-summary artifact; Locked OOS and Fresh/Forward class balance, target-resolution, ambiguity and long/short outcome aggregates remain inaccessible until their separately authorized stages;
- R01 Research Memory may retain artifact identities, validation state, structural quality and the Discovery-only summary identity, but no protected target aggregate or full-dataset target-distribution feedback;
- inherited MAX MTF Old temporal protection from `maxqstudio/max_research_agent@3e969efcdeb4ca6a2ae63acbd80592e378d2a446`: physical Discovery/Locked/Fresh ownership and context chronology are preserved, but supervised/evaluation eligibility is purged on original source-row identity so the latest legal upstream target end is strictly before the downstream protected boundary; applied boundary purge derives from the existing label horizon and minimum legal dependency purge, and R01 seals both Discovery→Locked and Locked→Fresh authority;
- active adversarial leakage suite whose named gates execute attacks against production functions/contracts: representative future perturbation across all CP32 features/families, future higher-timeframe and relative-symbol injections, centered/future information-contract violations, fitted-preprocessing/fill attacks, direct and indirect target contamination, dependency/sequence overlap, physical source-row fake adjacency, MTF as-of defects, relative timestamp mismatch, incomplete target tail, same-bar TP/SL ambiguity, and actual protected-memory insertion/use attempts;
- a deliberately leaky synthetic feature that must be detected as a negative control;
- immutable dataset, feature, label, chronology, data-quality, dependency, protected-data, parity and leakage artifacts;
- atomic terminal authority publication across R01 run state, artifact registry, gate events and Research Memory;
- fail-closed restart recovery that seals `ERROR_WAITING_OWNER` without silently rerunning uncertain dataset work.

The R01 label contract has **no arbitrary historical edge/margin threshold**. Future information is legal only in target construction. Features use information at or before the decision boundary. Label horizon is runtime-derived from the frozen R00 parent `deterministic_risk.max_hold_bars`; no numeric horizon is accepted merely from source code or documentation.

R01 Research Memory publication is non-adaptive for protected evaluation evidence. Locked OOS and Fresh Forward remain unavailable as hidden tuning feedback.

No model training, model fitting, ONNX generation, Research Challenger creation, Champion mutation or R02 execution is authorized by the accepted targeted R01 implementation/runtime repair.

Terminal behavior remains `PASS_WAITING_OWNER`, `FAIL_WAITING_OWNER`, or `ERROR_WAITING_OWNER`, followed by **STOP — WAITING OWNER**. The next gate cannot open automatically.

## R02 — Discovery / Cheap Screen

Inside one explicitly Owner-started bounded Discovery block:

- deterministic planner and/or Scientist may propose candidate specs;
- candidate count and compute budget are frozen;
- cheap screening may allocate compute;
- Cheap Screen PASS has no scientific qualification authority;
- all candidate specs and outcomes are persisted, including failures.

The block ends once. There is no autonomous second Discovery block.

Terminal behavior: **STOP — WAITING OWNER**.

## R03 — Full WFA + Qualified Pool

Only candidates authorized from Discovery are rerun under Full Walk-Forward Analysis using their original immutable candidate specs.

Only hard-gate PASS candidates enter the Qualified Pool. There is no automatic “Pool 12” requirement unless a future Owner policy explicitly freezes one.

The system may rank and recommend qualified candidates but hard qualification is separate from ranking.

Terminal behavior: **STOP — WAITING OWNER**.

## R04 — Owner Selection -> CPCV

The Research UI exposes qualified candidates and deterministic evidence. The system may preselect/recommend a preferred set, but the Owner can change it.

Only Owner-selected qualified candidates enter CPCV. Failed candidates cannot be selected as scientifically qualified.

CPCV executes automatically inside this authorized gate.

Terminal behavior after CPCV: **STOP — WAITING OWNER**.

## R05 — Tournament

Only Owner-authorized CPCV survivors enter.

Run deterministic comparison and evidence generation. Hard PASS/FAIL remains independent from ranking. The system may recommend survivors; the Owner selects which qualified survivors advance.

Terminal behavior: **STOP — WAITING OWNER**.

## R06 — Monte Carlo

Run stress simulation only on selected, qualified candidates. Persist configuration, seeds, distributions, failure modes and outputs.

The system may recommend. The Owner selects.

Terminal behavior: **STOP — WAITING OWNER**.

## R07 — Locked OOS

Locked OOS remains sealed until the Owner explicitly opens R07.

Evaluate exactly the authorized candidates. No tuning, candidate mutation, feature changes or threshold changes may use Locked OOS outcomes.

A Locked OOS failure never automatically starts another Discovery cycle using that feedback.

Terminal behavior: persist PASS/FAIL evidence, then **STOP — WAITING OWNER**.

## R08 — Fresh / Forward

Fresh/Forward is opened only by explicit Owner authorization.

Evaluate the frozen candidates and persist immutable evidence. The same research snapshot cannot tune itself from these results.

Terminal behavior: **STOP — WAITING OWNER**. No automatic final fit.

## R09 — Final Fit + ONNX Production

The Owner explicitly selects one or more scientifically qualified candidates for final fit/export.

Each immutable model package must bind:

- architecture and exact parameter count;
- parent Strategy and dataset lineage;
- feature ordering and preprocessing;
- seeds;
- training configuration;
- validation/CPCV/Tournament/Monte Carlo/OOS/Forward evidence;
- model and policy hashes;
- ONNX opset/runtime contract;
- sequence length where applicable.

Required runtime parity:

```text
native Python
≈ ONNX Runtime
≈ MT5 ONNX
```

Tolerance must be frozen by model/output semantics, not chosen after observing results.

Terminal behavior: **STOP — WAITING OWNER**. No automatic Strategy Challenger creation.

## R10 — Strategy Challenger Creation

The Owner explicitly selects which successful Research Model Packages become Strategy Challengers.

Future flow:

```text
Research Model Package
+
Parent Strategy
    |
Strategy Challenger
    |
existing MAX Backtest / Artifact governance
    |
STOP
    |
Owner review
    |
Owner explicit Champion decision
```

Research does not own Champion promotion. There is no autonomous Challenger or Champion.

## System recommendation contract

At each candidate-selection gate the system recommendation must be deterministic and evidence-based. Its inputs, ordering, tie-breaks and compute limits must be persisted.

The LLM Scientist may explain the recommendation or propose hypotheses. It cannot declare hard PASS, override deterministic validators, open the next gate, or select on behalf of the Owner.

## Scientist role

Scientist may:

- inspect authorized dataset evidence;
- inspect drift and class balance;
- inspect feature importance;
- analyze failure topology;
- compare candidates;
- analyze capacity;
- propose hypotheses;
- produce bounded analytical Python evidence.

Scientist may not:

- declare scientific hard PASS;
- override deterministic validators;
- open the next gate;
- select candidates on behalf of the Owner;
- access Locked OOS before authorization;
- promote Challenger or Champion;
- change risk;
- silently change datasets;
- run arbitrary shell/network/filesystem actions.

## Scientist analytical Python sandbox

A future research sandbox may expose only bounded analytical capability over authorized evidence IDs.

Allowed capability includes bounded in-memory analytics and approved NumPy/Pandas/SciPy/scikit-learn analytical operations.

Forbidden capability includes:

- shell/process execution;
- network;
- arbitrary filesystem access;
- package installation;
- git;
- MT5 execution;
- Champion mutation;
- Research-state mutation.

Sandbox output is always `ANALYTICAL_EVIDENCE_ONLY`. It is never PASS authority.

## Research Memory

Research Memory is append-only legal scientific memory. It must retain:

- PASS;
- FAIL;
- near miss;
- leakage rejection;
- capacity rejection;
- instability;
- NaN/Inf;
- overfit signatures;
- failed hypotheses;
- successful hypotheses.

Memory may learn from legal Research/Discovery evidence. Locked OOS and Fresh/Forward evidence cannot become hidden tuning memory while they retain evaluation authority. Any later reuse must occur only under an explicitly new research authority that no longer claims those samples as untouched evaluation evidence.

## Artifact control plane integration

Research artifacts must integrate with M08 artifact governance. Planned artifact classes include:

- `RESEARCH_DATASET`;
- `FEATURE_MANIFEST`;
- `LABEL_MANIFEST`;
- `DATA_QUALITY_REPORT`;
- `CANDIDATE_SPEC`;
- `CHEAP_SCREEN_RUN`;
- `FULL_WFA_RUN`;
- `MODEL_CHECKPOINT`;
- `CPCV_REPORT`;
- `TOURNAMENT_REPORT`;
- `MONTE_CARLO_REPORT`;
- `LOCKED_OOS_REPORT`;
- `FORWARD_REPORT`;
- `SCIENTIST_ANALYSIS`;
- `ONNX_MODEL`;
- `ONNX_POLICY`;
- `RESEARCH_MODEL_PACKAGE`;
- `STRATEGY_CHALLENGER_PACKAGE`.

Required lineage:

```text
Parent Strategy
-> Dataset
-> Feature / Label Contract
-> Candidate
-> Fold / Seed / Training
-> Model
-> Validation
-> CPCV
-> Tournament
-> Monte Carlo
-> Locked OOS
-> Fresh / Forward
-> Final Model Package
-> Strategy Challenger
```

No orphan model or unbound research artifact is valid authority.

## Future Research UI

The future Research page must expose:

- Parent Strategy identity;
- one canonical current Research stage/state derived from immutable historical gate evidence plus the persisted current-stage run;
- hardware snapshot;
- model-family universe;
- dynamic-capacity evidence;
- candidate table;
- hard qualification state;
- system recommendation;
- Owner checkbox selection;
- evidence access;
- Scientist analysis;
- START the explicitly Owner-authorized current stage;
- STOP;
- retry where scientifically/legalistically permitted;
- Continue Selected to Next Gate.

There must be no generic **RUN FULL RESEARCH TO CHALLENGER** action.

## Build sequencing rule

Future Builders implement this roadmap milestone-by-milestone. A later milestone is not authorized merely because an earlier milestone passes tests.

The required control loop is:

```text
Owner authorizes current gate
-> automation executes only inside current gate
-> deterministic validation
-> artifacts + state persisted
-> gate reaches *_WAITING_OWNER
-> STOP
-> Control Room audits/accepts implementation as applicable
-> Owner selects qualified candidate(s)
-> Owner explicitly authorizes next gate
```

Previous epoch: R00 source audit and Owner-PC runtime acceptance passed, R00 was accepted, and R01 implementation/runtime repair received targeted re-audit PASS. Current source-governed fresh epoch: no Research project or gate; R00 NOT STARTED; R01 BLOCKED / NOT STARTED; training 0; ONNX 0; Research Challenger 0; Champion mutation NONE; R02 BLOCKED. A future R01 run requires a new current-epoch Strategy parent, Research initialization and separate Owner authorization; R02 requires later separate Owner authorization after real R01 scientific acceptance.
