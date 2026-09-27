# Existing Concept Audit — MAX Rebuild Phase 1

Authority source: D:\MAX_MTF\MAX_MTF_v2_0_1
Candidate build: V201_657604676E59_1A29B4C5
Source tree signature: 657604676e590d5da59596a43f94225155644f0a02c7655555c2c41dab1c6835

## Audit rule

Current executable source and current lifecycle tests override superseded historical prose.
The rebuild ports domain contracts only. Streamlit glue, closure machinery, and old handoff runners are not domain authority.

## EA v2.0

Existing source location:
- EA_v2_00/baseline/Max_MTF.mq5
- ModelLab/core/project_paths.py
- ModelLab/runtime/strategy_authority.json
- ModelLab/runtime/strategy_challenger_registry.json

Existing behavior:
- Max_MTF.mq5 is the canonical EA v2.00 baseline.
- Seven strategy families remain active: Trend, Range, Breakout, Pullback, Session, Shock, Relative Value.
- Baseline SHA-256: 9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345.
- Bootstrap state is BASELINE_NOT_CHAMPION and current Strategy Champion is null.

Invariant to preserve:
- Strategy candidate = exact EA v2.0 identity + complete optimizer-owned parameter set.
- Trading logic is frozen in Phase 1.
- Baseline is active seed authority but is not automatically Strategy Champion.

Legacy detail not carried:
- Streamlit-specific baseline rendering/state.
- MTF-1 closure and Owner-handoff machinery.

Supporting evidence:
- ModelLab/docs/mtf/MAX_MTF_V2_FOUNDATION.md
- ModelLab/tests/v200_first_strategy_promotion_selftest.py

## Strategy Optimizer

Existing source location:
- ModelLab/strategy/strategy_optimizer.py
- ModelLab/strategy/strategy_optimizer_worker.py
- ModelLab/docs/research/STRATEGY_OPTIMIZER_V2.md
- ModelLab/tests/stage12_strategy_optimizer_v2_selftest.py

Existing behavior:
- MT5 Strategy Tester owns simulation/execution and native optimization.
- Python owns request freeze, search-space validation, MT5 orchestration, report parsing, hard gates, ranking, and evidence.
- Optimizer-owned universe is exactly 16 inputs:
  InpWeightTrend, InpWeightRange, InpWeightBreakout, InpWeightPullback,
  InpWeightSession, InpWeightShock, InpWeightRelative,
  InpEntryThreshold, InpExitReverseThreshold, InpMinConsensus,
  InpSL_ATR, InpTP_ATR, InpMaxHoldBars, InpShockHaltATR,
  InpRelativeLookback, InpMinRelativeCorr.
- Default max rounds = 3.
- Eligibility and ranking are separate.
- Default hard gates: PF >= 1.00, RF >= 0.00, Mean R >= 0.00, Weighted R >= 0.00, AUTO minimum closed trades.
- Ranking: Weighted R DESC, Mean R DESC, PF DESC, RF DESC, MT5 pass ASC.
- Missing/non-finite required metrics fail closed.
- A pass clearing native gates but missing required Weighted-R evidence blocks deterministic winner selection.
- Current v2.0.1 lifecycle registers an eligible winner as Strategy Challenger, not Champion.

Invariant to preserve:
- No Python substitute backtester for production optimizer decisions.
- Hard-gate failure can never be rescued by ranking.
- Frozen job settings are immutable during the run.
- Stop further optimization once an eligible deterministic winner is registered as Challenger.

Legacy detail not carried:
- Streamlit widget shadow-state implementation.
- Historical direct-Champion apply behavior superseded by current Challenger lifecycle.
- Legacy compatibility naming is not new-job authority.

Supporting evidence:
- validate_search_space(), select_champion(), deterministic_refine().
- strategy_optimizer_worker.py current round lifecycle.

## Scientist Optimizer Advisory

Existing source location:
- ModelLab/strategy/strategy_optimizer.py scientist_propose_ranges()
- ModelLab/strategy/strategy_optimizer_worker.py no-winner refinement path
- ModelLab/scientist/core/scientist.py

Existing behavior:
- Scientist is called only after a completed round has no eligible winner and another round is allowed.
- Payload is bounded to frozen KPI, selected inputs, current ranges, hard bounds, round, and top near-best MT5 passes.
- Scientist may propose only start/step/stop for existing selected parameters.
- Proposal is untrusted and passes deterministic validation.
- Invalid/out-of-bound proposal falls back to deterministic refinement.
- Disabled/unavailable/failed Scientist falls back deterministically with provenance.
- If an eligible winner already exists, Scientist is not called and no next round launches.

Invariant to preserve:
- Scientist is adviser only.
- Deterministic engine owns legality and winner selection.
- Scientist cannot alter KPI, EA logic, parameter universe, symbol, dates, validation policy, promotion, or Champion state.

Legacy detail not carried:
- Vendor-specific routing inside domain logic.
- Generic model-research agent orchestration unrelated to optimizer refinement.

Supporting evidence:
- scientist_propose_ranges()
- validate_search_space()
- worker modes DETERMINISTIC_ONLY, SCIENTIST_PROPOSAL, DETERMINISTIC_FALLBACK.

## Strategy Challenger

Existing source location:
- ModelLab/strategy/strategy_challenger_registry.py
- EA_v2_00/challengers/
- ModelLab/runtime/strategy_challenger_registry.json
- ModelLab/tests/v011_strategy_challenger_lifecycle_selftest.py

Existing behavior:
- Eligible optimizer winner creates one immutable Strategy Challenger bundle.
- Human identity format: STRAT-<UTC>-R<round>-P<pass>.
- Bundle contains Max_Challenger_<ID>.mq5, matching .set, metadata, exact params, KPI, gates, source request, job/round/pass, hashes, and round report provenance.
- Challenger registration copies the canonical EA and changes only whitelisted optimizer defaults in the Challenger copy.
- Registration does not mutate current Champion/baseline.

Invariant to preserve:
- No orphan Challenger.
- Complete frozen parameters and EA/config parity are required.
- Challenger registration must not change Champion.
- Duplicate creation must be guarded at the application-state boundary.

Legacy detail not carried:
- JSON registry as mutable authority; rebuild moves operational state to SQLite.
- Artifact layout may be simplified only if immutable reproducibility is still proven.

Supporting evidence:
- register_optimizer_challenger()
- _write_challenger_bundle()
- v011_strategy_challenger_lifecycle_selftest.py

## Strategy Champion

Existing source location:
- ModelLab/strategy/strategy_challenger_registry.py
- ModelLab/runtime/strategy_challenger_registry.json
- ModelLab/runtime/strategy_authority.json
- ModelLab/tests/v200_first_strategy_promotion_selftest.py

Existing behavior:
- Exactly one current Strategy Champion or none.
- Initial Max MTF v2 baseline is not Champion.
- First promotion archives baseline as ARCHIVED_PRE_FIRST_STRATEGY_CHAMPION.
- Later promotion closes the previous Champion tenure as FORMER while preserving its source Challenger as historical PROMOTED lineage; it is not returned to active Challenger eligibility.

Invariant to preserve:
- Champion changes only through explicit Owner promotion.
- Exact Challenger parameters and provenance survive promotion.
- History remains auditable and reversible.

Legacy detail not carried:
- Duplicate mutable Champion truth spread across JSON files.

Supporting evidence:
- _baseline_record()
- _archive_baseline_before_first_promotion()
- _demote_current()
- v200_first_strategy_promotion_selftest.py

## Promotion

Existing source location:
- ModelLab/strategy/strategy_challenger_registry.py promote_strategy_challenger()
- ModelLab/ui/app.py explicit PROMOTE TO STRATEGY CHAMPION action

Existing behavior:
- Promotion requires an existing active Challenger.
- Artifact hashes and parameter completeness are checked first.
- Promotion commits canonical EA defaults, canonical Tester set parity, MetaEditor compile, Python strategy authority, then registry state.
- Existing EA/set/runtime-authority/registry bytes are retained for rollback.
- Any exception restores prior authority.

Invariant to preserve:
- Owner is sole promotion authority.
- Promotion is atomic and fail-closed.
- Unknown, incomplete, or tampered Challenger cannot be promoted.
- Scientist and Optimizer cannot trigger promotion.

Legacy detail not carried:
- Streamlit confirmation-widget implementation.
- File registry as transaction coordinator; rebuild will transact mutable state in SQLite.

## Scientist Knowledge

Existing source location:
- ModelLab/scientist/knowledge/scientist_knowledge.py
- ModelLab/scientist/knowledge/SCIENTIST_KNOWLEDGE_BASE.json
- ModelLab/docs/contracts/SCIENTIST_KNOWLEDGE_BASE.md

Existing behavior:
- Knowledge records implemented capabilities, workflow contracts, authority boundaries, and recommendation classifications.
- Classification set includes EXISTING, EXTENSION, EXPERIMENT, NEW, CONFLICT, OUTSIDE_CURRENT_CONTRACT.
- Handoff language also used OUTSIDE_CURRENT_SCOPE. Current behavioral authority remains OUTSIDE_CURRENT_CONTRACT because that is the implemented Scientist Knowledge source value.

Invariant to preserve:
- Scientist knows existing MAX capabilities before recommending additions.
- Static knowledge cannot override live deterministic state/evidence.

Legacy detail not carried:
- Full model-research knowledge inventory outside Phase 1.
- Future capability graph not needed for Strategy lifecycle.

Supporting evidence:
- scientist_knowledge.py
- SCIENTIST_KNOWLEDGE_BASE.json
- scientist_knowledge_sync_selftest.py

## Scientist Chat

Existing source location:
- ModelLab/scientist/chat/
- ModelLab/docs/ui/SCIENTIST_CHAT_READ_ONLY_V1.md
- ModelLab/tests/scientist_chat_readonly_selftest.py

Existing behavior:
- Manual Owner discussion room, separate from autonomous Scientist routing.
- Reads bounded committed evidence.
- May explain, critique, compare, diagnose, and recommend.
- Has no execution tools and cannot mutate state/config, call MT5, or promote.
- Reply provenance records requested/actual model, fallback, usage when available, and evidence IDs.

Invariant to preserve:
- Phase-1 Scientist Chat is read-only.
- Chat cannot start/stop optimizer, edit params/gates, register/promote, or modify DB.

Legacy detail not carried:
- Streamlit panel layout/state.
- Model-research context scopes outside Phase 1.

## Exact optimizer hard bounds extracted from source

| Input | Min | Max | Base step | Type |
| --- | ---: | ---: | ---: | --- |
| InpWeightTrend | 0.20 | 2.00 | 0.10 | float |
| InpWeightRange | 0.20 | 2.00 | 0.10 | float |
| InpWeightBreakout | 0.20 | 2.00 | 0.10 | float |
| InpWeightPullback | 0.20 | 2.00 | 0.10 | float |
| InpWeightSession | 0.10 | 1.50 | 0.10 | float |
| InpWeightShock | 0.10 | 1.50 | 0.10 | float |
| InpWeightRelative | 0.10 | 1.50 | 0.10 | float |
| InpEntryThreshold | 0.18 | 0.60 | 0.02 | float |
| InpExitReverseThreshold | 0.20 | 0.75 | 0.05 | float |
| InpMinConsensus | 0.10 | 0.70 | 0.05 | float |
| InpSL_ATR | 1.00 | 3.20 | 0.20 | float |
| InpTP_ATR | 1.20 | 5.00 | 0.20 | float |
| InpMaxHoldBars | 6 | 72 | 6 | int |
| InpShockHaltATR | 2.50 | 7.00 | 0.50 | float |
| InpRelativeLookback | 8 | 60 | 4 | int |
| InpMinRelativeCorr | 0.10 | 0.80 | 0.05 | float |

Scientist cannot exceed these bounds. Family-weight minimums remain strictly positive.
The validator also rejects non-finite ranges, reversed ranges, non-positive steps, missing/extra keys, and steps finer than the deterministic floor.

## Source conflict resolved

STRATEGY_OPTIMIZER_V2.md still contains historical text saying an eligible optimizer winner is applied directly to the canonical EA.
Current v2.0.1 source, Strategy Knowledge, runtime registry, and v0.11+/v2.00 lifecycle tests supersede that behavior.

Current authority:

eligible optimizer winner -> Strategy Challenger -> explicit Owner promotion -> Strategy Champion.

The rebuild preserves this newer lifecycle.
