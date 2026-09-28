# MAX Trading Agent — Phase 1 Roadmap

Current development governance: GitHub `main` is source authority; ordinary phase validation runs on GitHub Actions `windows-latest`; each green phase merges to `main`; real Owner-PC/MT5 runtime acceptance is deferred until GitHub development closure. Older milestone acceptance sequences below are retained as historical implementation evidence, not current development procedure.

## M00 â€” Existing Contract Extraction + New Foundation

Status: ACCEPTED

Deliver:
- Existing concept audit.
- Isolated project root.
- React/Vite frontend.
- FastAPI backend.
- SQLite foundation schema.
- Health endpoint.
- EA v2.0 frozen registration.
- MT5 executable/data-root preflight.
- PRD and roadmap.

Acceptance:
- RUN_MAX.cmd starts the Owner-visible local web app.
- Backend and frontend readiness are verified before browser launch.
- DB initializes and exposes real READY/FAIL schema state.
- EA identity is displayed.
- MT5 normal preflight works and explicit invalid executable fails closed.
- No optimizer implementation.

## M01 â€” Strategy Optimizer Deterministic Core

Status: ACCEPTED

Deliver:
- Optimizer job/request freeze.
- Exact parameter search contract.
- MT5 native orchestration.
- Atomic round state/recovery.
- XML + Weighted-R sidecar parsing.
- KPI eligibility and deterministic ranking.
- Provenance and Optimizer web UI.

Acceptance requires real MT5 optimization evidence.

## M02 â€” LLM Scientist Optimizer Advisory

Status: FUNCTIONAL PASS; RECOVERY-ACCOUNTING DEBT CLOSED_IN_M03

Deliver:
- Small Scientist provider boundary.
- Bounded no-winner payload.
- Structured range proposal.
- Deterministic schema/hard-bound validation.
- Deterministic fallback.
- Scientist provenance UI.

Critical regression:
eligible winner -> Scientist call count = 0.

## M03 â€” Strategy Challenger

Status: ACCEPTED

Deliver:
- SQLite Challenger registry.
- Immutable EA/parameter/evidence snapshot.
- Reproducible artifacts.
- Web comparison.

Critical regression:
eligible winner -> exactly one Challenger -> Champion unchanged.

## M04 â€” Strategy Champion / Promotion

Status: ACCEPTED

Deliver:
- Explicit Owner promotion.
- Confirmation UI.
- Atomic fail-closed mutation.
- First-Champion baseline archive semantics.
- Champion tenure history separated from operational Challenger eligibility.
- Replacement promotion keeps the previous Champion source Challenger in historical PROMOTED lineage rather than returning it to the active Challenger pool.
- Stable Strategy identity and immutable Challenger evidence remain preserved across Champion tenure history.
- Restart-safe lineage.

## M05 â€” Scientist Knowledge + Read-Only Chat

Status: ACCEPTED

Deliver:
- Compact Phase-1 Knowledge Authority.
- Context injection.
- Read-only chat over committed evidence.
- Optimizer/Challenger/Champion explanations.
- No mutation or execution tools.

## M06 â€” Strategy Challenger Operations

Status: ACCEPTED

Deliver:
- Challenger Backtest using each active Challenger's own retained Strategy artifacts.
- Non-destructive Challenger Retirement / Archive.
- Registry/UI scaling for many Challengers: Active and Retired/Archive views with search, filter, sort, and pagination or lazy listing.
- Preserve MQ5, SET, EX5 when compiled, manifest, params, KPI, optimizer evidence, promotion history, backtest history, hashes, lineage, and timestamps.
- M06 originally preserves Challengers non-destructively; M08 adds dependency-safe physical deletion for generated obsolete Challengers.
- No manual Champion demotion or special previous-Champion restore action.

## M07 â€” True MTF Strategy + Strategy Epoch Reset

Status: ACCEPTED + MERGED

Deliver:
- EA v2.10 / MAX_TRUE_MTF_DYNAMIC_V1.
- Dynamic Context / Structure / Main / Timing resolver with Main >= M15.
- Latest fully closed role-bar alignment at Main decision timestamps.
- Quality-weighted seven-family MTF fusion with Main-owned execution/risk.
- Relative Value evaluated on main and relative symbols at the same role timeframe.
- CP32_TRUE_MTF_V1 feature semantics while retaining the 32-feature runtime boundary.
- Frozen Strategy geometry provenance through Optimizer -> Challenger -> M06 retained backtest lineage.
- Explicit Strategy epoch reset with backup-before-mutation, no current Champion, no active Challenger, no optimizer state, and BASELINE_NOT_CHAMPION.
- Old M00-M06 evidence remains immutable historical evidence.

Forbidden in M07:
- Champion search or promotion.
- Research/model training.
- KPI weakening.
- Live trading authority.

## M08 — Strategy Result Control + Multi-Challenger + Backtest Results + Artifact Control

Status: ACCEPTED + MERGED

Deliver:
- Qualified-only Owner candidate pool for new optimizer jobs; raw/rejected passes retained as forensic evidence.
- No automatic Challenger. New terminal semantic is QUALIFIED_POOL_READY.
- Server-side qualified sorting/search/filter/pagination and stable multi-selection.
- Explicit Owner `Promote Selected to Challengers` with full canonical source revalidation.
- Deterministic PREPARED -> VALIDATED -> STAGED -> COMMITTED batch journal and retry-safe recovery.
- One independent Challenger per selected candidate with `OWNER_SELECTED_QUALIFIED_CANDIDATE` provenance.
- A source pass consumed by a COMMITTED Challenger batch remains historical and is excluded from the active Qualified Candidates pool even after later Challenger retirement/promotion or dependency-safe physical Challenger deletion.
- Backtest KPI authority parsed from retained MT5 Strategy Tester reports.
- Backtest View Details / Open Report / Clean Runtime / physical Delete.
- Interactive Challenger registry with precise `Promote to Champion` and dependency-safe physical Delete.
- Global Artifacts workspace with lineage, storage totals, retention, runtime inventory, orphan reconciliation, bulk preflight and `Clean Generated Data`.
- Registered-identity-only deletion with root containment and reparse/symlink/traversal rejection.
- Semantic operational artifact paths without milestone names.
- Canonical runtime schema / EA / Strategy identity and reduced duplicated hard-coding.
- Deterministic temporary DB/filesystem test isolation.
- Fresh-clone Owner acceptance from the exact frozen GitHub candidate.

Forbidden in M08:
- EA/True-MTF/KPI/trading-science changes.
- automatic Challenger or Champion selection.
- arbitrary filesystem path read/delete endpoints.
- destructive modification of accepted legacy authority/evidence.
- Research/model training.
- Builder merge of main.

## Phase-1 cumulative Strategy E2E

Status: M08 ACCEPTED; OWNER STRATEGY/RESEARCH AUTHORIZATION REMAINS SEPARATE

M08 is accepted and merged at `3ccbee7e79662fa3f2f57caa8b4ee1764cdd66bd`. The failed historical optimizer job `20260924_072758_d9f506cc` is not execution authority and must not be resumed by post-M08 work.

Research follows [RESEARCH_MODEL_ROADMAP.md](RESEARCH_MODEL_ROADMAP.md). In the previous runtime epoch, R00 was accepted after source audit PASS and Owner-PC runtime acceptance PASS. Its source/runtime lineage is `5a1c303b0b901e7b370e2606aabc8cceed228a32`, Research ID `RSRCH-653cc6cff84e14f835ef764f`, parent Champion `STRAT-20260924-115344-R01-P8912`, and immutable execution snapshot with Research H1 minimum 4 trades/month. These identity and acceptance facts are historical only. Current fresh epoch: no Research project or gate, R00 NOT STARTED, R01 BLOCKED / NOT STARTED, and R02 BLOCKED. The current Research sample requirement is separately editable; future executions snapshot the configured value without rewriting historical R00 evidence. The merged R00 baseline is `3e224c8b6a493fadd1dabeba6b76bd9eb1f6bb7a`. The previous-epoch R01 implementation/runtime repair received Control Room targeted re-audit PASS; no current-epoch R01 scientific run or result is proven.

Required real path:
Web UI -> real MT5 zero-qualified round -> Scientist legal refinement -> real MT5 qualified pool
-> Owner selects qualified candidate(s) -> Challenger(s) -> Champion unchanged -> explicit Owner promotion -> Champion changes
-> complete history/provenance -> Scientist Chat explains read-only.

Required direct-qualified branch:
Round 1 has qualified candidate(s) -> Scientist never called -> no automatic Challenger -> Owner selection creates Challenger(s).

After cumulative E2E: STOP. Do not begin Model Research automatically.

## Post-M08 — Optimizer Fitness V2 + Research Planning

Status: BUILDER CANDIDATE / CONTROL ROOM PENDING

Optimizer objective authority for new jobs:
- current new-job request schema `MAX_REBUILD_OPTIMIZER_REQUEST_V6`; historical V5 remains accepted/readable;
- explicit `optimizer_result_workflow=QUALIFIED_POOL_OWNER_SELECTION`;
- `MAX_OPTIMIZER_FITNESS_V2`;
- `fitness = MeanR * Trades^alpha`;
- default `alpha=0.50`, legal range `0..1`;
- V6 adds `InpRiskPct` as the 17th optimizer dimension at 0.5%..5.0% with exact 0.5% step;
- V6 fixes `InpMaxDailyLossPct` at 5.0% and does not optimize it;
- alpha is fixed per job and is not one of the 17 V6 genetically optimized Strategy dimensions;
- historical V1-V5 jobs remain readable as their accepted 16-parameter epoch;
- Custom/Result is search fitness only and remains independent of the risk dimension;
- Mean R and Weighted R remain separate R-accounting KPI evidence;
- hard KPI qualification and M08 Owner selection remain unchanged.

The post-M08 milestone established Research planning only. The canonical gated implementation plan remains [RESEARCH_MODEL_ROADMAP.md](RESEARCH_MODEL_ROADMAP.md).

**AUTOMATION INSIDE THE GATE. HUMAN AUTHORIZATION BETWEEN GATES.**

## R00 — Research Authority Foundation

Status: ACCEPTED

R00 implements only:
- persisted Owner gate authorization;
- immutable verified Strategy Champion parent manifest;
- deterministic Research identity bound to one frozen parent;
- persisted R00 gate state and fail-closed restart semantics;
- `CP32_TRUE_MTF_V1` feature authority;
- unresolved/versioned label-design authority for R01;
- future candidate identity contract without creating candidates;
- M08 Research artifact lineage registration;
- deterministic hardware/resource snapshot;
- Research-specific KPI/sample authority: Owner must enter and freeze the Research H1 minimum trades/month during R00; no Strategy Optimizer default is inherited; KPI hard-gate dimensions are frozen while numeric thresholds remain gate-scoped Owner authority before first scientific qualification;
- append-only Research Memory foundation with protected-evaluation isolation;
- advisory-only Scientist Research contract;
- current-design Research web page.

The previous-epoch R00 terminal semantics were `PASS_WAITING_OWNER`, `FAIL_WAITING_OWNER`, or `ERROR_WAITING_OWNER`. Its accepted runtime persisted `PASS_WAITING_OWNER` as designed; canonical governance closeout for that historical identity was R00 = ACCEPTED.

## R01 — Dataset + Label + Leakage Foundation

Status: IMPLEMENTATION/RUNTIME REPAIR TARGETED REAUDIT PASS; REAL SCIENTIFIC RUN NOT STARTED

R01 source scope implements only:
- for a future epoch, separate explicit Owner-authorized R01 START bound to that epoch's newly accepted R00 Research identity and frozen parent Strategy; the previous-epoch accepted identity does not authorize current R01;
- MAX-owned verified Research-data preparation under a canonical managed Research data root, using the retained immutable R00 parent Challenger EA + fixed SET as Strategy authority, actual MT5 account/server/symbol identity as feed authority, sealed source identity, and no production authority from an Owner-entered bundle path, broker string, feed string or guessed timezone;
- MT5 source identity bound to actual terminal/account server/company, exact symbols, point size and UTC epoch authority, plus exact accepted-parent EA hash/version, Strategy/feature/resolver contracts and timeframe geometry;
- causal dataset construction with immutable source identity, exact source-file hashes, coverage and creation provenance;
- independent Python reproduction of `CP32_TRUE_MTF_V1` with deterministic EA/Python parity evidence;
- exact current Context / Structure / Main / Timing closed-bar geometry and relative-symbol timestamp pairing;
- a frozen SELL / SKIP / BUY parent-bound first-barrier label contract whose horizon is runtime-derived from the frozen parent MaxHold value;
- label SL/TP/MaxHold and bid/ask/spread semantics bound to the accepted parent execution geometry;
- same-bar TP/SL ambiguity and incomplete target horizons preserved as context-only target-invalid rows;
- deterministic dependency horizon with purge/embargo derived from actual feature/MTF/relative/label dependencies;
- explicit Owner-supplied Discovery / Locked OOS / Fresh Forward boundaries with no hidden date defaults, timezone-naive API interpretation or ungoverned gaps; row ownership is Discovery [from,to), Locked OOS [from,to), Fresh/Forward [from,to];
- persisted protected-partition row counts, first/last owned rows, zero overlap and zero unassigned rows;
- full labels remain eligible for deterministic internal sealing and immutable lineage, while a versioned Discovery-only label summary is the sole pre-protected-stage outcome aggregate exposed to Owner/Scientist; Locked OOS/Fresh outcome-derived class balance, target-resolution, ambiguity and long/short summaries remain sealed, and Research Memory contains no protected target aggregate;
- inherited MAX MTF Old temporal target-purge authority: original physical/source-row chronology remains intact, while Discovery supervision excludes rows whose effective target/dependency purge reaches Locked OOS and sealed Locked-OOS evaluation authority analogously excludes rows reaching Fresh; legality uses the strict original-row rule `latest_legal_target_end < first_downstream_row`, with applied purge derived from existing label/dependency/minimum-purge authority rather than a new constant;
- executable adversarial leakage attacks against production validators/functions for future perturbation, direct/indirect target contamination, compressed-index adjacency and protected-memory feedback, plus the remaining dependency/alignment/label attacks and a deliberately leaky synthetic negative control;
- one canonical current Research-stage view derived from immutable R00 history plus the persisted R01 run;
- immutable dataset/feature/label/chronology/data-quality/dependency/protected/leakage evidence;
- atomic `PASS_WAITING_OWNER` / `FAIL_WAITING_OWNER` / `ERROR_WAITING_OWNER` publication and fail-closed restart recovery without automatic dataset rerun;
- append-only Research Memory event that is non-adaptive for protected evidence;
- human-readable Owner Research/Data Foundation presentation without exposing internal gate/hash/contract IDs or raw filesystem paths.

The accepted R01 implementation/runtime repair does **not** itself constitute a real R01 scientific execution, train a model, export ONNX, create a Research Challenger, mutate Champion authority, or authorize R02.

Optimizer addendum retained in the same accepted targeted-repair implementation:
- new optimizer request schema V6;
- `InpRiskPct` is the 17th dimension: start 0.5%, stop 5.0%, step 0.5%; every refined range preserves this Owner-grid origin and may only narrow to a subset of the exact 0.5% grid;
- off-grid `InpRiskPct` start/stop values or a non-0.5 step are rejected;
- new V6 jobs fix deterministic daily-loss limit at 5.0%;
- Fitness V2 formula and KPI qualification science remain unchanged;
- historical V1-V5 16-parameter jobs and accepted old risk authority remain readable and are not migrated;
- V6 Challenger/Champion lineage preserves both the selected risk value and fixed 5.0% daily-loss authority.

Real R01 scientific run = NOT STARTED. Model training = NOT STARTED. ONNX = NOT STARTED. Research Challenger = NONE. Research Champion mutation = NONE. R02 = BLOCKED.

## Historical M08 Builder Acceptance Sequence — superseded for current development

The following sequence records the historical M08 acceptance procedure. It is not the current GitHub-only phase workflow:

1. Build and verify M08 on `work/m08-strategy-results-artifacts`.
2. Freeze and push an exact candidate SHA.
3. Delete the entire previous `D:\MAX_REBUILD` directory; do not preserve DB, evidence, artifacts, node_modules, venv or Git worktree residue.
4. Fresh-clone the repository to `D:\MAX_REBUILD` and checkout the exact candidate SHA.
5. Install dependencies from repository contracts and verify a clean worktree.
6. Run cumulative tests, launcher twice, and disposable M08 E2E.
7. Use M08 cleanup/delete controls to remove Builder-created E2E data and return Owner operational state to zero Optimizer jobs, zero Challengers, no Champion and zero Backtests.
8. Return the candidate to Control Room. Builder does not merge.

Anti-hardcoding gate: new runtime output paths are semantic, runtime identity comes from canonical authority, and milestone-specific path/name literals are permitted only for accepted historical compatibility or acceptance evidence.
