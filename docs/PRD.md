# MAX Rebuild â€” Phase 1 PRD

Status: CURRENT EPOCH FRESH; NO RESEARCH PROJECT; R00 NOT STARTED; R01 BLOCKED / NOT STARTED; R02 BLOCKED
Implementation history: PREVIOUS-EPOCH R01 DATASET + LABEL + LEAKAGE FOUNDATION IMPLEMENTATION/RUNTIME REPAIR TARGETED REAUDIT PASS; no real R01 scientific run occurred.
Current runtime epoch: FRESH; no Research project or gate; current-epoch R00 NOT STARTED; R01 BLOCKED / NOT STARTED; R02 BLOCKED. M00-M08 remain ACCEPTED + MERGED. Previous-epoch R00 acceptance and R01 targeted re-audit are historical evidence only.
Owner authority: this PRD is the self-contained Phase-1 product and contract authority for the rebuild.

## Product goal

Rebuild the MAX research/control harness around the accepted Strategy lifecycle. The current baseline is Max_MTF EA v2.11: M07 true-MTF Strategy semantics plus the post-M08 optimizer-only fitness V2 contract.
Do not redesign the Strategy lifecycle. Replace only the old application shell with a local React + FastAPI web application.

Canonical lifecycle:

EA v2.11 baseline -> Strategy Optimizer -> real MT5 native optimization -> deterministic hard-gate qualification.
If no qualified candidate exists and another frozen round remains: bounded LLM Scientist refinement -> deterministic validation -> next MT5 round.
If qualified candidates exist: optimization stops at QUALIFIED_POOL_READY -> Owner selects one or many qualified candidates -> one Strategy Challenger per selected candidate -> explicit Owner promotion -> Strategy Champion.

Phase 1 also includes compact Scientist Knowledge and read-only Scientist Chat.

## Authorities

- Historical MAX MTF / Research source reference: `maxqstudio/max_research_agent@3e969efcdeb4ca6a2ae63acbd80592e378d2a446`, read-only concept/source authority for R00 audit; it is not current build authority.
- MT5 Strategy Tester: simulation/execution truth.
- Deterministic Python domain engine: legality, KPI gates, ranking, selection.
- Scientist: advisory only.
- Owner: promotion authority.
- SQLite: single mutable application-state authority.
- Immutable files: raw/runtime/scientific evidence.
- Old MAX directories: read-only reference only.

## Technical boundary

- Windows local application.
- Frontend: React + TypeScript + Vite.
- Backend: FastAPI + Python.
- State: SQLite.
- No Streamlit.
- No Docker, Redis, message brokers, Kubernetes, cloud deployment, multi-user/auth, or plugin framework in Phase 1.

## Phase-1 scope

1. EA v2.11 MAX_TRUE_MTF_DYNAMIC_V1 baseline authority; v2.11 changes optimizer objective/evidence semantics only, not Strategy runtime science.
2. Strategy Optimizer.
3. LLM Scientist optimizer advisory.
4. Strategy Challenger.
5. Owner promotion.
6. Strategy Champion.
7. Minimum Scientist Knowledge.
8. Read-only Scientist Chat.
9. Web UI for those workflows.
10. Strategy Challenger Operations: real-MT5 Challenger Backtest and non-destructive Retirement / Archive.
11. M07 true-MTF Strategy semantics and explicit Strategy epoch reset before Research.
12. M08 qualified-result control, Owner multi-Challenger selection, real Backtest KPI results, global Artifact control plane, and operational hard-coding cleanup.

## Product invariants

- The seven Strategy family formulas are preserved; M07 changes their information source from single-timeframe snapshots to deterministic true-MTF role snapshots and quality-weighted family fusion.
- Strategy candidate is EA v2.0 plus a complete parameter configuration.
- Eligibility is evaluated before ranking.
- Failed hard gates cannot be compensated by ranking.
- Scientist cannot mutate authority.
- For current M08 optimizer jobs, existence of any fully qualified candidate means no Scientist call and no later optimization round; it does not create a governance winner.
- Challenger registration never changes Champion.
- Baseline is not automatically Champion.
- Promotion is explicit, atomic, and fail-closed.
- Phase-1 chat is read-only.
- No PASS claim without executed evidence.

## Explicitly deferred

Model training, GRU/LSTM/Transformer/PatchTST/TFT/MoE, LightGBM model research,
WFA/CPCV model lifecycle, Tournament, Monte Carlo, Fresh model validation,
Model Champion, ONNX, Shadow, Live trading, portfolio management, news/sentiment,
cloud deployment, multi-user/auth, and MTF-2+.

## M00 acceptance

- Existing concept audit is coherent.
- Isolated rebuild root exists.
- Owner can start the system with RUN_MAX.cmd.
- Backend and frontend start on 127.0.0.1 only.
- SQLite initializes and reports a readable schema/baseline authority state.
- Frozen EA identity and zero-Champion state are displayed.
- MT5 terminal, MetaEditor, and data-root preflight works.
- Explicit invalid MAX_MT5_TERMINAL fails closed without auto-detection fallback.
- Browser opens only after the Vite-proxied overview passes readiness.
- Optimizer is not implemented yet.

## Canonical Phase-1 lifecycle authority

This PRD is self-contained. External Builder prompts, chat history, and legacy prose may explain lineage, but they are not required to determine the Phase-1 product contract.

Canonical lifecycle:

EA v2.11 baseline -> Strategy Optimizer -> real MT5 native optimization -> deterministic evidence parsing -> deterministic hard-gate qualification -> Owner-facing qualified candidate pool.

If no qualified candidate exists and another frozen round remains:
- Scientist OFF: accepted deterministic refinement drives the next real MT5 round.
- Scientist ON: one bounded advisory proposal is validated deterministically; legal ranges or deterministic fallback drive the next real MT5 round.

If one or more qualified candidates exist for a current M08 job:
- optimization stops immediately with QUALIFIED_POOL_READY;
- no automatic Challenger is created;
- the normal Owner table contains qualified candidates only; raw/rejected passes remain forensic evidence;
- Owner may explicitly select one or many qualified candidates;
- backend revalidates every selected candidate from canonical MT5 XML, Weighted-R sidecar, hashes, parameters, Strategy geometry and all frozen hard gates before any mutation;
- one independent Strategy Challenger is created per selected candidate using OWNER_SELECTED_QUALIFIED_CANDIDATE provenance;
- once a source pass is durably consumed by a committed Challenger batch or Challenger registry row, it is excluded from the active Qualified Candidates pool; CHALLENGER, PROMOTED, RETIRED, and later physical Challenger deletion do not make that source pass selectable again while its optimizer/batch authority remains;
- only the existing explicit Owner Challenger -> Champion promotion flow may create a Strategy Champion.

Historical pre-M08 optimizer jobs retain their accepted eligible-winner evidence and remain readable without rewriting historical artifacts.

Authority boundaries:
- MT5 Strategy Tester = simulation and optimization truth.
- Deterministic Python = legality, evidence, KPI qualification, provenance and revalidation authority. Candidate table rank/sort is presentation only and never creates a governance winner.
- LLM Scientist = advisory only.
- SQLite = mutable operational and registry authority.
- Immutable files = scientific, execution and bundle evidence.
- Owner = sole Strategy Champion promotion authority.

## Current EA / Strategy epoch authority

Current baseline: ea/baseline/Max_MTF.mq5, EA semantic version 2.11.

Current SHA-256:
827c4caddedbe37081353e08bba35eac5f01e96314dd8650d7ea17ad109ae725

Strategy contract: MAX_TRUE_MTF_DYNAMIC_V1.
Feature contract: CP32_TRUE_MTF_V1.
Baseline status: BASELINE_NOT_CHAMPION.

The predecessor v2.00 / SHA-256 9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345 is historical single-timeframe foundation authority only and cannot be a current Champion in the new Strategy epoch.

The seven families remain Trend, Range, Breakout, Pullback, Session, Shock and Relative Value. Their formulas remain inherited; M07 evaluates them across causal Context / Structure / Main / Timing role snapshots and fuses them before the existing RuleMetaScore. Session participates only on Main and Timing because its broker-session-hour semantics are intraday.

## Strategy Optimizer contract

For new V6 optimizer jobs, the optimizer-owned parameter universe is exactly 17 inputs. Historical V1-V5 jobs retain their accepted 16-parameter universe and are never rewritten:

- InpWeightTrend: 0.20..2.00, base step 0.10
- InpWeightRange: 0.20..2.00, base step 0.10
- InpWeightBreakout: 0.20..2.00, base step 0.10
- InpWeightPullback: 0.20..2.00, base step 0.10
- InpWeightSession: 0.10..1.50, base step 0.10
- InpWeightShock: 0.10..1.50, base step 0.10
- InpWeightRelative: 0.10..1.50, base step 0.10
- InpEntryThreshold: 0.18..0.60, base step 0.02
- InpExitReverseThreshold: 0.20..0.75, base step 0.05
- InpMinConsensus: 0.10..0.70, base step 0.05
- InpSL_ATR: 1.00..3.20, base step 0.20
- InpTP_ATR: 1.20..5.00, base step 0.20
- InpMaxHoldBars: 6..72, base step 6, integer
- InpShockHaltATR: 2.50..7.00, base step 0.50
- InpRelativeLookback: 8..60, base step 4, integer
- InpMinRelativeCorr: 0.10..0.80, base step 0.05
- InpRiskPct: 0.50..5.00, exact base/minimum step 0.50

For new V6 jobs, `InpRiskPct` is a Strategy Optimizer dimension and the fixed deterministic daily-loss limit is 5.0%. `InpMaxDailyLossPct` is not an optimizer dimension. V6 enforces `0 < InpRiskPct <= InpMaxDailyLossPct`; current V6 authority therefore permits the exact discrete risk set 0.5%, 1.0%, 1.5%, 2.0%, 2.5%, 3.0%, 3.5%, 4.0%, 4.5%, 5.0%. Historical V1-V5 requests, Challengers, Champions and evidence preserve their accepted parameter vector and historical daily-loss authority without migration or reinterpretation.

The seven family weights must remain strictly positive. Missing keys, extra keys, non-finite values, illegal integer values, reversed ranges, non-positive steps, steps below the deterministic floor, or hard-bound violations are rejected. Illegal input is never silently clipped.

Owner-selected parameters use MT5 optimization flag Y and frozen start/step/stop. Non-selected parameters use frozen EA defaults and flag N. An active request is immutable.

The request freezes main symbol, Relative Value reference symbol, timeframe, dates, tick model, deposit, leverage, native optimizer mode, max rounds, selected parameter set, complete search space, KPI authority, EA identity and Scientist ON/OFF route identity.

Supported MT5 role timeframes remain M1, M2, M3, M4, M5, M6, M10, M12, M15, M20, M30, H1, H2, H3, H4, H6, H8, H12, D1, W1 and MN1. Main/Decision timeframe is a separate capability and must be >= M15 and able to resolve four distinct roles.

Main and relative symbols must be non-empty and different. Dates use YYYY.MM.DD and require from_date earlier than to_date.

Native modes are MT5 Fast Genetic and Slow Complete. OptimizationCriterion=6 is custom MAX.

For new V6 jobs the frozen objective remains `MAX_OPTIMIZER_FITNESS_V2`:

```text
fitness = MeanR * pow(R-accounted closed trades, alpha)
0.00 <= alpha <= 1.00
default alpha = 0.50
```

Alpha is one fixed Owner-selected optimizer setting per job. It is not a Strategy parameter and is not part of the exact 17 V6 genetically optimized dimensions. `alpha=0` reduces to Mean R, `alpha=0.5` applies square-root trade weighting, and `alpha=1` equals cumulative Sum R.

For V6, MT5 Custom/Result is custom fitness and is search guidance only. `InpRiskPct` does not alter the Fitness V2 formula; fitness remains Mean R times the frozen trade-count exponent term. Mean R comes independently from verified R-accounting frame/sidecar evidence and remains a KPI gate. Recovery Factor comes only from the dedicated MT5 Recovery Factor column. Weighted R comes only from verified optimizer frame/sidecar evidence. V1-V4 artifacts retain their historical Custom==Mean-R interpretation. V5 retains its accepted 16-parameter Fitness V2 semantics. No historical artifact is silently reinterpreted as V6.

## KPI, sample and ranking authority

Default inclusive Strategy Optimizer hard gates are:
- Profit Factor >= 1.00
- Recovery Factor >= 0.00
- Mean R >= 0.00
- Weighted R >= 0.00
- Closed trades >= deterministic AUTO minimum

AUTO minimum trades uses an H1 baseline of 20 trades/month, SQRT timeframe scaling, minimum factor 0.20, maximum factor 4.00, CEIL monthly-rate rounding and CEIL final exact-date-range requirement. The computed sample authority is frozen with the request.

Eligibility occurs before any presentation ordering. Failed hard gates cannot be rescued by ordering. Custom fitness is not a KPI gate and cannot convert FAIL to PASS. Every row that passes all frozen KPI gates enters the qualified pool; no row becomes Challenger merely because it ranks first or has high custom fitness. The Owner-facing table keeps Mean R and Custom Result / Fitness separate and supports server-side sorting by supported numeric columns. Display rank is a UI convenience only. Historical pre-M08 eligible-winner records retain their accepted deterministic ranking semantics.

## Scientific evidence authority

Mean R is reconstructed by the frozen EA from complete realized trade history using actual initial-stop risk. Invalid or missing risk, non-finite accounting, accounting errors, non-positive summed initial risk, or R-accounted trades differing from MT5 STAT_TRADES invalidate the pass.

For V6, frame/sidecar evidence independently records `custom_fitness`, `trade_exponent_alpha`, Mean R, Weighted R, MT5 trades, R-accounted trades, Sum R, net P/L, summed initial risk, accounting errors, run nonce and exact parameter-vector identity. Python verifies XML Custom ~= frame custom fitness ~= recomputed `MeanR * Trades^alpha`. Any mismatch fails closed.

Weighted R is sum(net P/L) divided by sum(actual initial risk). Its authority remains Max_MTF_metrics.csv. SpreadsheetML pass IDs and frame pass IDs are different namespaces; XML and sidecar evidence are joined by the canonical exact request-epoch parameter vector—16 dimensions for historical V1-V5 and 17 for V6—never by pass ID.

The sidecar must match the round run nonce and pass arithmetic/accounting parity checks. Missing Weighted-R evidence prevents eligibility. If a contender passes all non-Weighted-R gates but lacks Weighted-R evidence, winner selection is unresolved and fails closed.

A native round requires fresh compatible report identity for EA, symbol, timeframe, from-date and to-date. SpreadsheetML Created metadata is not freshness authority. Stale, malformed or identity-mismatched evidence fails closed.

Durable round phases are PREPARED, MT5_RUNNING, MT5_COMPLETE, REPORT_READY and PARSED, with WAITING_FOR_REPORT as a recoverable state. Restart never blindly relaunches the same uncertain or completed MT5 round.

## Bounded Scientist authority

Scientist may run only after a completed, successfully parsed round with zero qualified candidates, when another frozen round remains and scientist_assist is true. Qualified-pool existence and max-round exhaustion short-circuit before any Scientist call.

Scientist receives only bounded evidence: frozen KPI gates, selected parameter names/current ranges/hard bounds/types/base steps, compact search context, non-selected parameter names and at most 12 near-best completed MT5 passes. It receives no secret value, filesystem authority, raw XML/CSV, Champion mutation capability or unrelated model-research context.

Scientist may return only start, step and stop for exactly the frozen selected parameters. It cannot change KPI, minimum trades, selected parameter universe, EA, market contract, dates, tick model, optimizer mode, max rounds, qualification, Owner selection, Challenger, Champion or promotion state.

Proposal output is untrusted JSON. Invalid, malformed, out-of-bounds or authority-changing output is rejected without clipping or semantic repair and falls back to accepted deterministic refinement.

One source-round to target-round transition has at most one semantic proposal attempt. Provider credentials are resolved at call time from the configured environment-variable identity and are never persisted.

Provider accounting distinguishes NOT_STARTED, UNCONFIRMED and CONFIRMED. Only confirmed provider execution increments confirmed/actual provider-call counts. A crash in an unconfirmed call window never invents a confirmed call and never retries the uncertain provider request; deterministic fallback continues the optimizer.

## Strategy Challenger authority

For current M08 jobs, a Strategy Challenger may be created only from a durable qualified candidate explicitly selected by the Owner. A Scientist proposal, near miss, manual parameter entry, rejected pass, stopped job, failed job, waiting job or zero-qualified terminal result cannot create a Challenger. Historical pre-M08 Challengers may retain OPTIMIZER_WINNER provenance.

Before M08 registration, backend independently reparses and cross-checks each Owner-selected candidate against retained optimizer request, canonical MT5 XML, Weighted-R sidecar, passes evidence, eligibility audit, run nonce, the exact request-epoch parameter vector, EA SHA, Strategy contract/geometry, report identity/hashes, and every frozen hard gate.

Unique source identity remains source_job_id + source_round + source_pass. Human identity derives from source optimizer time when available using STRAT-<UTC>-R<round>-P<pass>. M08 batch requests use deterministic batch identity; retries cannot create duplicate Challengers. A prevalidation failure creates none.

SQLite schema version 3 adds strategy_challengers as the sole mutable Challenger registry. JSON is bundle evidence only and never a competing mutable registry.

The immutable bundle contains the Challenger EA copy, fixed Challenger .set, challenger.json, manifest.json, and copied authoritative optimizer request, qualified-candidate evidence for M08 (or historical eligible-winner evidence), passes, eligibility audit, report provenance, XML and Weighted-R sidecar.

The Challenger EA starts from the frozen baseline and may change only the request-epoch optimizer-owned defaults plus the request-bound fixed daily-loss default where V6 authority requires it. A V6 Challenger contains the exact 17 winner parameters with optimization flags N and a fixed 5.0% daily-loss limit. Historical V1-V5 Challenger bundles remain readable with their accepted 16-parameter universe and historical fixed-risk authority.

Required parity:
- selected qualified-candidate full params == Challenger EA defaults
- selected qualified-candidate full params == Challenger SET values
- Challenger EA defaults == Challenger SET values

Registration uses a durable REGISTERING -> CHALLENGER sequence. Incomplete staging is not visible. A complete final bundle may be verified and finalized after restart. Committed tampering fails closed and is never silently repaired.

M03 never mutates the baseline, creates a Champion, archives the baseline, demotes a Champion or exposes promotion/delete actions.

## M04 promotion boundary

Only an explicit Owner action may promote a verified Strategy Challenger. The Owner flow is two-stage: Promote opens a confirmation surface and a distinct CONFIRM PROMOTION action submits the mutation with the Challenger manifest SHA and expected current Champion identity. Backend state and artifact integrity are re-read at final confirmation; stale confirmation fails closed.

M04 repair raises SQLite authority to schema version 5. strategy_promotions remains the durable promotion journal. strategy_champions stores Champion tenure rows keyed by a tenure identity, while strategy_id remains the stable Strategy identity. A database invariant allows zero or one CURRENT Champion. Challenger state uses CHALLENGER for active promotion eligibility and PROMOTED for any source that has entered Champion lineage; immutable M03 Challenger bundles remain unchanged.

Promotion revalidates the full Challenger and retained optimizer evidence using the exact optimizer request epoch, verifies the frozen baseline SHA, stages before-state backups, creates the first-promotion baseline archive or later former-Champion archive, copies the verified Challenger EA into ea/champion/current, writes a fixed Champion Tester SET with every request-epoch optimizer flag N and preserves the request-bound V6 5.0% daily-loss authority where applicable, performs real MetaEditor compilation, verifies fresh EX5 and source/SET/deployment parity, then commits Champion authority in one SQLite transaction. File mutations before the authority commit are rollback-protected by the durable journal and before-state snapshots.

The frozen seed file ea/baseline/Max_MTF.mq5 remains BASELINE_NOT_CHAMPION and byte-identical at SHA-256 9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345. First promotion creates an immutable ARCHIVED_PRE_FIRST_STRATEGY_CHAMPION history artifact without mutating the seed file.

Successful first promotion makes the promoted Challenger identity the CURRENT Strategy Champion, changes that Strategy's Challenger registry row to PROMOTED, preserves source optimizer/job/round/pass lineage, and leaves Live authority as NONE.

Later promotion separates Champion tenure history from active Challenger eligibility. If Strategy A is CURRENT Champion and active Challenger B is promoted, A's Champion tenure closes as FORMER history, A's original source Challenger remains PROMOTED as historical Champion lineage, B changes CHALLENGER -> PROMOTED, and B receives the new CURRENT Champion tenure. Strategy A keeps the same strategy_id, original immutable Challenger bundle, manifest, params, optimizer provenance, and EA version. A consumed Champion source is not automatically eligible for another promotion; no duplicate Strategy identity or rebuilt Challenger bundle is created.

Promotion failure or pre-commit crash never reruns the optimizer or Scientist and never invents a Champion. If SQLite authority has not committed, prior authority wins and files are restored. A valid COMMITTED promotion survives restart and is reverified rather than automatically repeated. Strategy Champion is governance/research authority only; it does not authorize live-account trading.

## M05 Scientist Knowledge and Chat boundary

M05 raises SQLite authority to schema version 6 only for Scientist chat persistence and adds a deterministic compact knowledge projection plus a separate read-only Owner discussion room. The projection is derived from an explicit canonical-source allowlist and fails closed as SCIENTIST_KNOWLEDGE_STALE when source provenance no longer matches. Knowledge classification semantics are exactly EXISTING, EXTENSION, EXPERIMENT, NEW, CONFLICT and OUTSIDE_CURRENT_CONTRACT; historical OUTSIDE_CURRENT_SCOPE wording is not runtime authority.

Each request receives fresh bounded context from committed baseline, optimizer, Challenger, Champion and promotion authority. Exact Strategy/job/promotion identities use deterministic parameterized lookups; user text never becomes a filesystem path or arbitrary SQL. Stable evidence references must resolve to supplied context before an answer can be returned.

Scientist Chat may read, reason, explain, compare, critique and diagnose committed evidence. It has no execution/tool definitions and cannot start or stop optimizer jobs, call MT5, execute Python/shell/browser/MCP/filesystem tools, register Challengers, promote Champions, alter KPI/settings, deploy, trade or mutate domain authority. Chat may mutate only scientist_threads, scientist_messages and scientist_chat_requests.

Provider requests are idempotent by request_id. CALL_IN_FLIGHT records an unconfirmed attempt before external execution; only a returned provider response can increment confirmed calls. A restart converts uncertain in-flight calls to UNCONFIRMED and never automatically retries them. Provider output is strict untrusted JSON; malformed schema, invalid classification, unsupported evidence references, missing project evidence, secret echo and prohibited profit-guarantee language fail closed without persisting leaked response text.

## M06 Strategy Challenger Operations boundary

M06 is ACCEPTED. Its scope is limited to retained-contract Challenger Backtest and non-destructive Challenger Retirement / Archive.

The Challenger registry supports many Strategies over time; no fixed small-count assumption is allowed. M06 UI separates Active Challengers from Retired / Archive and supports scalable search, sort, and pagination.

Retirement is status-based preservation, not deletion. CHALLENGER -> RETIRED must retain the Strategy's original MQ5, SET, EX5 when compiled, immutable manifest, params, KPI, optimizer evidence, promotion history, backtest history, hashes, lineage, created UTC, and retired UTC. Current mutable status remains in strategy_challengers, while immutable/durable retirement history is recorded in strategy_challenger_retirements with retirement identity, before/after status, expected manifest authority, state, evidence path, and created/completed UTC. Retirement must fail closed while the same Challenger has an active PREPARED/RUNNING backtest or active PREPARED/ARTIFACTS_STAGED/FILES_COMMITTED promotion. No destructive DELETE CHALLENGER or DELETE STRATEGY API is authorized.

Any active Challenger may be backtested only from its own retained Strategy artifacts through real MT5 Strategy Tester execution. M06 backtest is observational and must use the retained source request contract for symbol, relative symbol, timeframe, date window, tick model, deposit, and leverage; request overrides are not authorized. A RUNNING backtest recovered after restart/crash becomes UNCONFIRMED with no automatic retry, because execution may already have occurred. M04 preserves original Challenger bundle identity across promotion and replacement. M06 does not introduce manual Champion demotion or a special previous-Champion restore action; a former Champion source remains historical PROMOTED authority unless a future explicit governance contract defines another transition.

## M07 True MTF Strategy + Strategy Epoch Reset boundary

M07 creates a new Strategy epoch before Research. Canonical roles are Context (TF+2), Structure (TF+1), Main/Decision (TF), and Timing (TF-1). Main is the decision cadence and must be at least M15; absolute role floor is M5.

Formation is deterministic from the supported MT5 timeframe table using scale-aware absolute log-ratio distance: Timing target Main/3, Structure target Main*4, Context target Main*16. Required anchors are M15 -> H4/H1/M15/M5, M30 -> H8/H2/M30/M10, and H1 -> H12/H4/H1/M20. Four roles must be distinct and ordered Timing < Main < Structure < Context.

At each new Main bar timestamp, every role on both main and relative symbols uses only the latest fully closed role bar whose close_time <= decision_time. Open/current higher-timeframe bars are forbidden.

For each family, participating role signals are fused as quality-weighted signal sum divided by quality sum; fused quality is quality sum divided by participating-role count. The existing seven family weights and RuleMetaScore remain authoritative. No Context/Structure/Main/Timing optimizer weights are introduced.

Main remains execution authority for spread gate, ATR SL/TP, position/risk management and trade accounting. MTF roles provide Strategy information only. Relative Value compares Main Symbol vs Relative Symbol at the same role timeframe and remains analysis-only; only Main Symbol is traded.

ONNX class contract stays SELL/SKIP/BUY and feature dimensionality stays 32. CP32_TRUE_MTF_V1 uses Main-role market features 0..23, MTF-fused family contributions 24..30 and final MTF RuleMetaScore at 31. Old single-TF CP32 evidence is not scientifically equivalent to the new feature semantics.

Every frozen Optimizer request binds exact strategy_geometry and strategy_contract provenance. That identity must persist through optimizer evidence, M08 qualified candidate (or historical eligible winner), Challenger bundle/manifest, retained Backtest and future Champion lineage. M06 retained backtests must never recompute geometry from mutable global state.

The Strategy epoch reset is explicit, backup-first and fail-closed. Post-reset operational state is: current Champion NONE, Challenger registry 0, retirement registry 0, promotion 0, Challenger backtest 0, optimizer jobs/results 0, optimizer Scientist refinement state clean, and the v2.10 baseline registered only as BASELINE_NOT_CHAMPION. Provider/API settings are preserved. Old M00-M06 tracked evidence remains historical and immutable.

M07 and M08 are ACCEPTED + MERGED. In the previous runtime epoch, R00 was accepted after source audit PASS and Owner-PC runtime acceptance PASS. Its source/runtime lineage is `5a1c303b0b901e7b370e2606aabc8cceed228a32`, Research ID `RSRCH-653cc6cff84e14f835ef764f`, parent Champion `STRAT-20260924-115344-R01-P8912`, and immutable R00 execution snapshot with Research H1 minimum 4 trades/month. These identity and acceptance facts are historical only and do not describe current runtime authority. The fresh epoch has no Research project or gate; current-epoch R00 is NOT STARTED, R01 is BLOCKED / NOT STARTED, and R02 remains BLOCKED. The historical 4-trade value is not a permanent UI/configuration constant: the current Research sample requirement is separately editable and each future Research execution snapshots the exact configured value it consumes. The merged R00 baseline is `3e224c8b6a493fadd1dabeba6b76bd9eb1f6bb7a`. The previous-epoch R01 implementation/runtime repair received Control Room targeted re-audit PASS; no current-epoch R01 scientific run or result is proven.

## Phase-1 completion boundary

The R00 entry prerequisites were satisfied only for the previous-epoch Research identity above: M08 accepted/merged, cumulative Strategy E2E explicitly satisfied by canonical Owner/Control Room authority, an exact verified parent Strategy frozen, and explicit Owner R00 authorization persisted. These historical conditions do not authorize current-epoch Research; the fresh runtime has no Strategy Champion or Research project. Model training, WFA/CPCV model lifecycle, Tournament, Monte Carlo, Fresh model validation, ONNX promotion, Shadow/Live trading, portfolio management and news/sentiment remain blocked until their separately authorized gates.

## Research implementation authority

Historical scope: the accepted R00 and targeted R01 repair described below belong to the previous runtime epoch. Current runtime has no Research project or gate; current-epoch R00 has not started, and R01 remains blocked/not started.

**Previous runtime epoch: R00 — Research Authority Foundation was ACCEPTED, and R01 implementation/runtime repair received TARGETED REAUDIT PASS. Current fresh epoch: no Research project or gate; R00 is NOT STARTED and R01 is BLOCKED / NOT STARTED.** The canonical gate-by-gate plan remains [RESEARCH_MODEL_ROADMAP.md](RESEARCH_MODEL_ROADMAP.md). No real R01 scientific run or result is proven, and R02 has not been authorized or started.

Accepted R00 persisted Owner authorization, froze the exact verified Strategy Champion as immutable Research parent, froze feature/hardware/lineage/state contracts, and terminated in the designed waiting-for-Owner state. Its immutable R00 execution snapshot records 4 trades/month with no Strategy Optimizer inheritance; current Research configuration may later change without rewriting that historical snapshot or any running execution snapshot. R00 froze Research KPI governance dimensions but did not guess numeric thresholds before the scientific gate that consumes them. R00 built no dataset, labeled no training rows, trained no model, exported no ONNX, created no Research Challenger, and performed no Champion mutation. The accepted targeted R01 implementation/runtime repair adds a MAX-owned managed Research-data producer: verified MT5 account/server and symbol authority plus the accepted parent EA are used to create a sealed source bundle under the canonical Research data root, with no Owner-entered filesystem path, broker or feed authority. R01 then applies causal CP32 reproduction/parity, parent-bound three-class labels, chronology/data-quality authority, dependency-derived purge/embargo, explicit protected-data row ownership, executable adversarial leakage attacks, immutable dataset evidence, atomic terminal publication and fail-closed restart recovery. Discovery and Locked OOS use [from,to) ownership and Fresh/Forward uses [from,to]; timezone-naive partition API input is rejected. Full labels may remain internally materialized and sealed for immutable dataset lineage, but all pre-authorization Owner/Scientist outcome-derived label evidence is Discovery-only through a versioned summary; Locked OOS/Fresh class balance, target resolution, ambiguity and long/short outcome aggregates are not exposed to Owner UI, Scientist context or Research Memory before their authorized stages. R01 inherits MAX MTF Old temporal target-purge semantics from `maxqstudio/max_research_agent@3e969efcdeb4ca6a2ae63acbd80592e378d2a446`: physical partition ownership/context rows remain intact, while supervised/evaluation eligibility uses original source-row chronology and requires the upstream legal target end to remain strictly before the downstream protected boundary. The applied boundary purge is derived from the existing label/dependency/minimum-purge authority, and the same sealed contract covers Discovery→Locked OOS and future Locked OOS→Fresh separation. The current Research lifecycle stage is derived from immutable R00 history plus the persisted R01 run, so accepted R00 evidence remains immutable while callers receive one canonical current-stage answer. Label horizon is always derived at runtime from the frozen parent max-hold authority, not from a static numeric assumption. Source implementation does not itself execute R01. R02 remains blocked pending a future separate Owner authorization after real R01 scientific execution and acceptance.

The governing rule is:

**AUTOMATION INSIDE THE GATE. HUMAN AUTHORIZATION BETWEEN GATES.**

The system determines scientific PASS/FAIL, may recommend among qualified candidates, and persists evidence. The Owner determines which qualified candidates advance and explicitly opens each next gate. No Research gate may automatically open the next gate, create a Strategy Challenger, or promote a Champion.

## Historical M08 Strategy Results + Artifact Control Plane acceptance boundary

At M08 acceptance, workflow/data-control/UX changed while EA v2.10, SHA-256 `b5555f6741c60af3b60e920105caec859f7acf106c10f2ee80a1bdada50aba31`, `MAX_TRUE_MTF_DYNAMIC_V1`, True-MTF resolver/families/fusion/Relative logic, trade/risk model, KPI formulas, minimum-trades formula, optimizer search-space science, Scientist authority and Champion promotion science remain frozen.

For new M08 optimizer jobs, all MT5 passes are retained as forensic evidence but only rows passing every frozen finite-value, minimum-trades, PF, RF, Mean-R and Weighted-R gate enter the normal qualified pool. The qualified API performs canonical report/sidecar hash and identity checks, reparses the MT5 XML and Weighted-R sidecar, recomputes gate evidence, and paginates server-side. No automatic Challenger or Champion is created.

Owner multi-selection is explicit. The backend prevalidates the entire batch before mutation, stages complete independent Challenger bundles, then commits registry authority transactionally. Batch states are PREPARED, VALIDATED, STAGED and COMMITTED; failures are FAILED or RECOVERY_REQUIRED. Retry uses deterministic batch/source identity and cannot duplicate a Challenger. A COMMITTED batch is durable source-consumption history: the source pass stays out of the active Qualified Candidates pool even if the generated Challenger row is later physically deleted.

Backtest performance authority is the actual retained MT5 Strategy Tester HTML report. M08 normalizes report metrics such as net/gross profit, PF, RF, expected payoff, Sharpe, trades, win/loss counts and percentages, and balance/equity drawdowns. Missing metrics remain unavailable; Optimizer Mean R / Weighted R may be shown only as explicitly labeled source Optimizer metrics. Opening a report resolves a registered verified Backtest identity; no generic filesystem read endpoint exists.

Generated operational paths are semantic: `artifacts/optimizer/<JOB-ID>`, `artifacts/challengers/<CHALLENGER-ID>`, `artifacts/backtests/<BT-ID>` and semantic operation namespaces. Accepted legacy evidence remains readable/protected and is not rewritten into M08 paths.

The Artifacts workspace is a global generated-data control plane, not a second Strategy scientific authority. It inventories owner/source/producer/path/hash/size/status/retention/dependencies and reconciles known MAX-owned MT5 runtime artifacts. Retention classes are ACTIVE_AUTHORITY, USER_GENERATED, TEMPORARY_RUNTIME and REGENERABLE. Runtime cleanup and deletion operate only by registered identity with canonical-root containment, ownership checks, traversal rejection, symlink/reparse escape rejection, dependency blocking and physical-absence verification. Orphan runtime is discovered and shown but never auto-deleted.

`Clean Generated Data` requires preflight and no active operations. It may delete currently deletable generated Optimizer jobs, obsolete dependency-free Challengers, Backtests, runtime residue/orphans and safe Scientist operational chat data. It must not delete source code, baseline EA, current required Strategy authority, accepted historical authority, or provider/settings configuration.

Normal tests construct deterministic temporary SQLite/filesystem fixtures; mutable Owner runtime DB is not a test fixture. Final M08 Owner acceptance is performed only after an exact GitHub candidate is frozen, the old `D:\MAX_REBUILD` is deleted, and a clean exact-SHA clone is created.

## M08 Operational Authority and Acceptance Contract

M08 normal runtime code is version-neutral. Current project milestone, schema version, EA version, EA SHA-256, Strategy contract and baseline status are resolved from canonical backend/config/manifest authority rather than duplicated across launcher, frontend, cleanup code or artifact management. Historical compatibility fixtures may retain old milestone/version literals, but new operational paths MUST NOT be milestone-named.

Generated operational namespaces are semantic:

- `artifacts/optimizer/<JOB-ID>`
- `artifacts/challengers/<CHALLENGER-ID>`
- `artifacts/backtests/<BT-ID>`

Accepted milestone evidence remains protected historical authority and is not reclassified as generated cleanup data.

M08 Builder acceptance uses a contamination-free Owner-PC deployment: source is completed and frozen in GitHub first, then the previous `D:\MAX_REBUILD` directory is deleted in full, the exact candidate SHA is freshly cloned, dependencies are installed from repository contracts, and runtime/E2E acceptance is executed only on that clean clone. Builder-created E2E Optimizer/Challenger/Backtest data must be removed through M08 controls before handoff. At that M08 acceptance boundary, Research remained blocked. R00 was later executed and accepted in the previous runtime epoch under the canonical R00 closeout authority above. The previous-epoch R01 implementation/runtime repair received targeted re-audit PASS; the current fresh epoch has no Research project, R00 has not started, R01 remains blocked/not started, and R02 remains blocked.
