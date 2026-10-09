# ONNX-00 — Scientific Authority, Model Universe, and Roadmap

Status: planning-only proposal for Control Room audit
MAX starting main: 203f6ab3dc618e3edd247841aa25e1fa0951a19a
ModelLab main inspected: 883ebeb1ca2e70f6255e0889358ba7c822ac52f5
Machine-readable authority: [onnx_v1_authority.json](../../.workflow/onnx_v1_authority.json)

## 1. Scope and evidence boundary

ONNX-00 establishes the scientific and product authority for a future ONNX Research workspace. It does not implement an API, UI, model runtime, model training, ONNX export, or MT5 integration. No Owner PC was accessed and no MT5 process was run.

Governed by claim `TRUTH-ONNX-PLANNING-001` in `.workflow/claims.json`.

The active legacy Research implementation is RETIRED. The ONNX roadmap replaces the old R00-R11 roadmap; it does not run beside the retired Research authority. Strategy remains the source of trading Strategy geometry and CP32 data. ONNX will own model Challenger/Champion authority. Artifacts retain immutable evidence and runtime artifacts. Scientist remains advisory across workspaces.

Exact Owner decisions frozen for V1:

- Standalone: lightgbm, xgboost, gru, tcn, transformer, patchtst.
- Hybrid: gru/tcn/transformer/patchtst paired separately with lightgbm and xgboost.
- Total: 14 active families.
- Excluded: random_forest, lstm, itransformer, tft, transformer_moe, and every other hybrid composition.

Future-runtime backend preference carried from the Owner decision: LightGBM uses OpenCL GPU and XGBoost uses CUDA GPU. Synthetic CI may use CPU to test deterministic source behavior, but CPU-only CI is not GPU/runtime evidence. Temporal-device policy must be explicitly frozen before runtime implementation; it must not be silently assumed to be CPU-only.

## 2. Selective ModelLab adoption

All 19 authorities below were inspected at ModelLab main SHA 883ebeb1ca2e70f6255e0889358ba7c822ac52f5. The reference informs this plan; it is not copied wholesale and is not runtime authority for MAX.

| ModelLab authority | Decision | ONNX treatment |
|---|---|---|
| ModelLab/docs/research/CHAMPION_FACTORY_V3.md | ADAPT | Preserve hard stage boundaries, WFA-only pool admission, append-only failures, candidate identity, stage seals, and runtime-blocked repair isolation. Do not inherit exact-12 filling, legacy Top-1 promotion, automatic failure-cycle research, or unapproved legacy KPI profiles. |
| ModelLab/docs/research/POLICY_DISCOVERY_V1.md | RETIRE | No separate adaptive policy-discovery stage in ONNX V1. Do not reopen candidate thresholds or add a second model-selection authority. |
| ModelLab/docs/research/FEATURE_LABEL_AUDIT.md | ADAPT | Permit bounded upstream/pre-freeze diagnostics only. No downstream holdout feedback, post-freeze label changes, or new search loop without a governed new cycle. |
| ModelLab/docs/research/RESEARCH_ATOMIC_RESUME_V1.md | KEEP / ADAPT | Keep last-valid-committed-checkpoint semantics, immutable source binding, sequence/hash envelope, same-cycle recovery, and disposable uncommitted work. Bind the envelope to ONNX cycle, stages, candidate pool, budget, and survivors. |
| ModelLab/docs/research/HARDWARE_ADAPTIVE_MODEL_RESEARCH_V1.md | ADAPT | Keep deterministic hardware/resource measurement and the distinction between broad legal support and active recommendations. Replace dynamic family expansion with the Owner-frozen 14-family allowlist. Hardware may narrow effective search capacity, not change science or gates. |
| ModelLab/governance/MODEL_TRAINING_METHOD_CONTRACT_V1.json | ADAPT | Keep strict rejection of out-of-bounds concrete proposals, Strategy-derived label horizon, purged chronological early stopping, and temporal-to-tree purged OOF. Use the ONNX V1 family set and training contract; MoE rules are not used. |
| ModelLab/config/models/model_registry.json | RETIRE | Do not use its broader legacy registry as ONNX V1 family authority. The ONNX allowlist in the machine-readable authority is the sole V1 family list. |
| ModelLab/models/model_registry.py | RETIRE / ADAPT | Retire dynamic hybrid enumeration and legacy aliases. Adapt capability checks behind an explicit, frozen allowlist in a later implementation phase. |
| ModelLab/models/models.py | ADAPT | Reuse CandidateSpec/fingerprint and strict-admission concepts. ONNX identity must bind exact canonical params, cycle, dataset, Strategy geometry, feature/label contracts, windows, and evaluation contracts. Any AUTO canonicalization is recorded before identity freeze. |
| ModelLab/data/dataset_integrity.py | KEEP / ADAPT | Keep writer-safe snapshots, source hashes, and duplicate-identity detection. Exact-identical duplicates require deterministic backup/removal/revalidation; conflicting same-identity rows fail closed. Verify MAX CP32 identity fields before use. |
| ModelLab/data/data_quality.py | ADAPT | Keep finite-feature, data-sanity, discontinuity, and broker-proof distinctions. A timestamp gap alone is not missing-broker-data proof. Broker reconciliation is future runtime work; it was not run in ONNX-00. |
| ModelLab/data/labels.py | KEEP / ADAPT | Preserve causal labeling, exact Strategy geometry, SELL/SKIP/BUY order, effective label horizon, ambiguous-outcome policy, and context-only versus supervised-row distinction. No downstream holdout label tuning. |
| ModelLab/research/gru_research.py | ADAPT | Use only the approved GRU envelope and the ONNX temporal training contract: AdamW, purged chronological validation, best accepted checkpoint restoration, and frozen patience. |
| ModelLab/research/temporal_research.py | ADAPT | Preserve chronological sequence identity, training-only preprocessing, purged internal validation, best-checkpoint restoration, and fail-closed numeric safety for TCN, Transformer, and PatchTST. Excluded temporal families remain excluded. |
| ModelLab/research/hybrid_research.py | KEEP / ADAPT | Keep temporal-to-tree purged OOF stacking. A hybrid is one CandidateSpec; policy training cannot consume in-sample temporal predictions. Use only the eight approved compositions. |
| ModelLab/host/preflight.py | ADAPT | Keep canonical training CSV auto-discovery, Owner path override, hash, and dataset summary concepts. Data Intake must create and validate an immutable snapshot before any experiment. |
| ModelLab/host/mt5_gap_repair.py | ADAPT | Keep only source-backed, authority-preserving MT5/EA gap-repair semantics for a future authorized Data Intake phase. No terminal discovery, launch, repair, or MT5 access occurs in ONNX-00. |
| ModelLab/factory/champion_factory.py | ADAPT | Adopt stage identity, hard-gate, lineage, and seal concepts. Do not port its monolithic execution engine, dynamic feedback cycles, legacy family roster, or unapproved gate/profile behavior. |
| ModelLab/ui/app.py | RETIRE | Do not port the Streamlit workspace. Use MAX React/FastAPI ownership and the exact eight-page ONNX information architecture below. |

NOT_USED for V1: ModelLab legacy family aliases, dynamic all-combinations hybrid roster, Transformer-MoE internals, Policy Discovery runtime, Streamlit UI, and any old numerical KPI profile not explicitly adopted here.

## 3. Model universe and parameter authority

The authoritative exact list and active ranges are in onnx_v1_authority.json. The family universe is an allowlist, not a capability-registry expansion point.

| Standalone family | Hybrid temporal pairings |
|---|---|
| lightgbm | hybrid::gru::lightgbm, hybrid::tcn::lightgbm, hybrid::transformer::lightgbm, hybrid::patchtst::lightgbm |
| xgboost | hybrid::gru::xgboost, hybrid::tcn::xgboost, hybrid::transformer::xgboost, hybrid::patchtst::xgboost |
| gru | — |
| tcn | — |
| transformer | — |
| patchtst | — |

### Active V1 search ranges

These are active search envelopes for V1, not permanent legal ceilings. Log distribution is specified where noted. training_memory_months is dynamic and must be resolved and frozen per cycle.

| Family | Parameters |
|---|---|
| LightGBM | n_estimators 150–1500; learning_rate 0.005–0.12 log; num_leaves 8–128; max_depth 3–12; min_child_samples 10–500; subsample 0.60–1.00; colsample_bytree 0.60–1.00; reg_alpha 0–20; reg_lambda 0.01–100 log; training_memory_months dynamic. |
| XGBoost | n_estimators 150–1500; max_depth 2–10; learning_rate 0.005–0.12 log; min_child_weight 0.5–100 log; subsample 0.60–1.00; colsample_bytree 0.60–1.00; reg_alpha 0–20; reg_lambda 0.01–100 log; training_memory_months dynamic. |
| GRU | sequence_length 24–192; hidden_size 32–256; num_layers 1–3; dropout 0–0.35; learning_rate 0.0001–0.003 log; batch_size 32–512; max_epochs 20–120; weight_decay 0.000001–0.02 log; training_memory_months dynamic. |
| TCN | sequence_length 32–256; tcn_channels 32–192; tcn_blocks 2–6; kernel_size 2–5; dropout 0–0.35; learning_rate 0.0001–0.003 log; batch_size 32–512; max_epochs 20–120; weight_decay 0.000001–0.02 log; training_memory_months dynamic. |
| Transformer | sequence_length 32–256; d_model 32–256; num_layers 1–4; attention_heads 1–8; ffn_mult 2–4; dropout 0.05–0.35; learning_rate 0.00005–0.002 log; batch_size 16–256; max_epochs 20–100; weight_decay 0.000001–0.02 log; training_memory_months dynamic; d_model divisible by attention_heads. |
| PatchTST | sequence_length 64–256; d_model 32–192; num_layers 1–4; attention_heads 1–8; ffn_mult 2–4; patch_len 8–48; patch_stride 4–24; dropout 0.05–0.35; learning_rate 0.00005–0.002 log; batch_size 16–256; max_epochs 20–100; weight_decay 0.000001–0.02 log; training_memory_months dynamic; patch_stride <= patch_len <= sequence_length; d_model divisible by attention_heads. |

Parameter resolution is:

LEGAL ENVELOPE ∩ hardware capacity ∩ dataset capacity ∩ resource capacity ∩ scientific capacity → ACTIVE SEARCH ENVELOPE → proposal → exact immutable CandidateSpec.

The legal envelope is a separately versioned technical contract. Active ranges may narrow as data, hardware, resource, and science capacity require; they must not be reinterpreted as permanent legal maxima. A concrete Scientist proposal outside the effective envelope is rejected, never clamped. Deterministic AUTO generation may canonicalize valid structural constraints before the CandidateSpec is frozen, with the requested and canonical values recorded.

Hybrid candidates combine one temporal component and one LightGBM/XGBoost policy component. Temporal parameters are prefixed temporal_, policy parameters policy_, and one unprefixed training_memory_months is shared. Component envelopes are inherited; no additional composition is admitted.

## 4. Training, identity, and evidence

Temporal families and temporal components use AdamW. Searchable values are architecture width/depth, sequence and architecture geometry, learning rate, weight decay, batch size, max_epochs, and training-memory window. Fixed authority includes optimizer, early-stop metric, checkpoint integrity, chronology, purge, embargo, NaN handling, leakage guards, and numerical fail-safes. GRU patience is 6; TCN, Transformer, and PatchTST patience is 8. If GRU num_layers is 1, effective recurrent dropout is zero. max_epochs is an upper budget, not a promise to train every epoch.

Preprocessing is fit only on the eligible training partition. Early stopping uses purged chronological internal validation, never randomized splitting. Restore the minimum accepted validation-loss checkpoint before downstream evidence. Preserve original row identity across sequences and folds.

Hybrid policy training requires TEMPORAL_TO_TREE_PURGED_OOF_STACKING: split the permitted training region chronologically; fit the temporal component on each inner-train partition; purge by at least the effective label horizon; generate temporal meta-features only on the corresponding inner-validation rows; then combine these out-of-fold values with permitted CP32 policy features. In-sample temporal predictions for policy training are forbidden. Final permitted fit is separate from qualification evidence.

CandidateSpec is immutable after freeze and records exact family/composition, exact parameters, seed, shared training window, class/output contract, training-method contract version, and any Owner-approved policy fields. CandidateSpec SHA-256 uses versioned deterministic serialization. The experiment fingerprint additionally binds cycle ID, immutable dataset snapshot SHA, Strategy geometry, feature/label contracts, all three windows, WFA/CPCV/Tournament/Monte Carlo/KPI contracts, parameter authority version, resolved active envelope, and CandidateSpec hash. Exact repeated fingerprints do not consume a second exposure; all failures remain in an append-only ledger with first_failed_gate.

## 5. Data authority, labels, and temporal windows

Data Intake auto-discovers the canonical Max_MTF_Training.csv and permits an explicit Owner path override. Before Discovery it creates a stable writer-safe byte snapshot and SHA-256; the snapshot, not the mutable source path, is the cycle data authority. A cycle binds one Strategy authority, one verified CP32 feature contract, one label contract, one symbol, and one timeframe.

Audit the approved schema, feature finiteness, duplicate identity, OHLC, ATR, quotes, and chronological discontinuities. An observed timestamp discontinuity is not proof of a missing broker bar. Reconcile against broker data before declaring a true gap. Only then may a future authorized MT5/EA repair produce source-backed bars, followed by a full re-audit and a new snapshot hash. Never mean-fill, forward-fill, back-fill, interpolate, or synthesize OHLC/CP32 rows. Exact-identical duplicate rows receive a deterministic backup, removal, full revalidation, and before/after hashes; conflicting rows sharing identity fail closed. The precise identity columns must be verified from the approved MAX CP32 schema in ONNX-02.

Expose exactly three windows: Discovery, Tournament, and Forward. Enforce:

- DiscoveryFrom <= DiscoveryTo
- DiscoveryTo < TournamentFrom <= TournamentTo
- TournamentTo < ForwardFrom <= ForwardTo

At cycle START freeze the data SHA, exact ranges, Strategy geometry, feature and label contracts, and scientific contract. Forward AUTO_NEWEST may be previewed, but START resolves it to an exact timestamp and the Forward range cannot grow within the cycle.

Labels are causal and preserve exact Strategy geometry. Class order is SELL, SKIP, BUY. Effective future label horizon is the purge/embargo authority. Ambiguous outcomes have an explicit versioned rule. Rows needed only as sequence context do not become supervised truth. No holdout outcomes tune labels.

## 6. Stage boundaries and gates

1. DATA INTAKE → validated immutable snapshot and DATA_READY evidence.
2. CAUSAL LABELING → frozen Strategy-derived label contract and effective horizon.
3. DISCOVERY → proposals from the exact 14-family allowlist; validate parameters and CandidateSpec; fingerprint and deduplicate.
4. CHEAP SCREEN → compute allocation/obvious-breakage filter only; no qualification authority. Every selected candidate reruns its original full CandidateSpec.
5. FULL WFA → exactly three chronological folds with required purge; candidate identity cannot change across folds. Only WFA_PASS enters the pool; ranking applies only among passes.
6. QUALIFIED POOL → maximum 12. At least 12 passes: freeze best 12 under predeclared Discovery ranking. One through 11: freeze all actual passes. Zero: NO_QUALIFIED_CANDIDATE and terminate. Never lower KPI, secretly extend budget, add families, or elevate Cheap Screen results to fill the pool.
7. CPCV → every WFA-qualified candidate; six groups, two test groups, exactly 15 combinations; purge and embargo each at least the effective label horizon. No mutation or threshold tuning. Ranking cannot rescue failure. Temporal and hybrid candidates require ordered seeds 42, 11, 77; all required confirmations pass. A failure remains a hard failure.
8. TOURNAMENT → only the independent Tournament window and every CPCV survivor. No hyperparameter search, architecture change, threshold tuning, model substitution, or same-cycle retraining from Tournament failures. Pass advances; fail is terminal for that cycle.
9. MONTE CARLO → every Tournament survivor; default 10,000 bootstrap-with-replacement simulations per candidate. Exact config and seed are frozen before the cycle. It changes no training parameters and emits a hard gate.
10. FORWARD → mandatory untouched Forward window after all prior gates. No tuning or parameter changes. PASS, FAIL, or INSUFFICIENT_SAMPLE; only PASS can proceed to runtime Challenger creation.
11. FINAL FIT / ONNX → only after selection and under the frozen permitted-history boundary; never train on Forward outcomes. Export, hash, parity, runtime manifest, and registry are sealed. Numeric parity tolerance and final-fit row boundary must be fixed before ONNX-07.
12. CHALLENGER → retain Forward evidence, exact candidate lineage/spec, all stage evidence/seals, final-fit evidence, ONNX hash/parity, runtime manifest, and registry identity. If science passes but runtime export/parity fails, mark RUNTIME_BLOCKED and repair runtime only; do not repeat Discovery.
13. CHAMPION → explicit Owner action only. Independently verify all upstream gates, lineage, stage seals, artifact hash/parity, and runtime manifest; commit atomically and archive prior Champion lineage. Rollback requires verified archived artifact/hash/parity.

Cheap Screen PASS is not scientific PASS. No downstream rank or recommendation overrides an upstream hard FAIL. No automatic R03 equivalent, Qualified Pool admission from screens, automatic winner selection, or Champion promotion exists.

### KPI boundary

The inspected ModelLab V3 authority explicitly supports Full-WFA/CV aggregate expectancy >= 0.00R and CPCV worst aggregate expectancy >= 0.00R; these two matching hard floors are recorded in the machine authority. No other legacy ModelLab KPI profile is imported implicitly. Exact definitions/thresholds for all remaining mandatory WFA, Tournament, Monte Carlo, and Forward KPIs are an OPEN AUTHORITY GAP and must be frozen before ONNX-03 execution. ONNX-00 invents no thresholds. Monte Carlo remains a hard PASS/FAIL gate, but its numeric acceptance criteria must be in the frozen KPI contract.

## 7. Cycle state, checkpoints, and artifacts

Cycle START freezes cycle ID; dataset and snapshot; Strategy geometry; feature/label contracts; three windows; model universe; parameter authority and active envelopes; WFA/CPCV/Monte Carlo configuration; KPI authority; experiment budget; Scientist configuration; and hardware/resource snapshot. Any scientific-contract change requires a new cycle.

The complete state list, guarded transition table, and cycle-terminal states are in `.workflow/onnx_v1_authority.json`. The normal path is `NO_CYCLE → DATA_PREFLIGHT → DATA_READY → CYCLE_FROZEN → DISCOVERY_RUNNING`; it then branches to `NO_QUALIFIED_CANDIDATE` or `QUALIFIED_POOL_FROZEN → CPCV_RUNNING → CPCV_NO_SURVIVOR` or `TOURNAMENT_RUNNING → TOURNAMENT_NO_SURVIVOR` or `MONTE_CARLO_RUNNING → MONTE_CARLO_NO_SURVIVOR` or `FORWARD_RUNNING → FORWARD_INSUFFICIENT_SAMPLE / FORWARD_NO_SURVIVOR / CHALLENGER_READY → PROMOTION_PENDING_OWNER → CHAMPION_ACTIVE`. Each branch is guarded by its stage’s complete terminal evidence and seal. Candidate-level failures are retained; zero survivors close the cycle without lowering gates. `INSUFFICIENT_SAMPLE` is not PASS. Runtime-only conversion/parity failure enters `RUNTIME_BLOCKED` and can return only to the same sealed candidate’s Challenger-ready path after runtime repair.

Any running scientific stage may pause only at a safe boundary with an atomically committed checkpoint; resume targets that same stage and must not repeat completed exposures. Authority, checkpoint, process-ownership, or durable-write uncertainty enters `RECOVERY_REQUIRED`; exit requires a verified checkpoint or explicit cycle abandonment with failure evidence preserved. Terminal outcomes return to `NO_CYCLE`; a new cycle requires a new explicit Owner start. No ranking result, UI action, or recovery path may bypass these transitions. Force Stop requires verified process ownership.

The only resume authority is LAST VALID COMMITTED CHECKPOINT. It binds cycle ID, dataset SHA, science-contract hash, stage/state, candidate pool and identities, tested fingerprints, budget consumption, survivor identities, monotonic sequence, and payload SHA-256. Commit is atomic. If the newest checkpoint is corrupt, validate and fall back to the previous valid checkpoint and resume the same cycle. Uncommitted work is disposable. Resume must not create a cycle or repeat completed exposures. Stage seals bind the cycle/science identity, exact candidate, previous terminal seal, configuration, and immutable evidence hashes.

## 8. Product and UI ownership

Top-level navigation: Strategy, ONNX, Artifacts, Settings. ONNX has exactly eight pages: Overview, Data Intake, Discovery, CPCV, Tournament, Monte Carlo, Challenger, Champion. There is no separate Pool page; Pool is internal Discovery state. There is no separate Forward page; Forward is the first Challenger-admission section. The reusable Scientist drawer remains advisory.

Overview summarizes cycle and dataset IDs/SHA, symbol/timeframe, frozen windows, science and hardware status, Discovery budget/experiments, qualified pool, survivors at every stage, Challengers/recommendation, ONNX Champion, current stage, last committed checkpoint, first blocker, and resume availability. It does not duplicate stage controls.

React displays state, requests actions, confirms, presents progress/evidence/errors. Backend owns validation, transitions, family/parameter admission, CandidateSpec, KPI, stage PASS/FAIL, checkpoints, evidence, and promotion permission. ONNX-00 defines no API endpoints. ModelLab Streamlit UI is retired, not ported.

## 9. ONNX-00..ONNX-09 roadmap

| Phase | Scope | Exit boundary |
|---|---|---|
| ONNX-00 | Scientific authority, 14-family allowlist, hybrid/training/parameter/data/state/evidence/UI/API ownership, and roadmap. | Planning/governance only; no runtime. Control Room audit before merge. |
| ONNX-01 | Eight-page React shell and backend API/state skeleton. | No real science execution. |
| ONNX-02 | Data Intake, DQ, duplicate policy, broker reconciliation/gap-repair authority, three windows, immutable snapshot. | DATA_READY evidence. |
| ONNX-03 | Causal labels, 14-family Discovery, CandidateSpec/fingerprint, Cheap Screen, Full 3-fold WFA, max-12 pool, Discovery checkpoints. | Exact KPI/metric contract must be frozen before execution. |
| ONNX-04 | CPCV 6C2=15, purge/embargo, required seed confirmations, frozen candidates, stage seal. | Complete reproducible CPCV evidence. |
| ONNX-05 | Independent Tournament window, no tuning, hard KPI, stage seal. | All CPCV survivors receive a terminal Tournament result. |
| ONNX-06 | Monte Carlo, survivor gate, exact replayable config/results, stage seal. | Every Tournament survivor receives a terminal MC result. |
| ONNX-07 | Untouched Forward, Challenger admission, permitted final fit, ONNX conversion/parity, runtime manifest, registry. | Forward PASS and versioned runtime/parity contracts. |
| ONNX-08 | Explicit Owner Champion promotion, atomic publication, prior Champion archive, verified rollback. | No automatic promotion; complete lineage. |
| ONNX-09 | Cumulative browser/backend E2E, negative/crash/resume/checkpoint-corruption/lineage/resource/performance hardening, source-only/governance, Owner acceptance package. | Exact candidate evidence and Owner acceptance boundary. |

Every implementation phase after ONNX-00 includes a one-click acceptance runner, cumulative E2E/regression, negative-path tests, machine-readable evidence with first_failed_gate and exact candidate identity, crash/resume evidence where relevant, no unexplained skips, Windows hosted CI, Project Truth/ROADMAP_SYNC, source-grounded DURING sequence documentation, and Control Room audit before merge.

## 10. Open blockers before runtime

- Freeze complete KPI definitions/thresholds for WFA, Tournament, Monte Carlo, and Forward; do not rely on unspecified legacy profiles.
- Version the broad legal numeric envelope separately from this active V1 envelope.
- Verify actual MAX CP32 duplicate identity fields and Strategy geometry source in ONNX-02.
- Freeze temporal compute-device policy before runtime implementation.
- Freeze final-fit row boundary, ONNX parity tolerances, and runtime-manifest schema before ONNX-07.
- Migrate or explicitly reconcile MAX's vendored Skill Workflow pin with latest Skill Workflow main before treating the latest toolchain as byte-identical governance authority.

These are explicit future gates, not permission to execute research. ONNX-00 remains planning-only; legacy real R00-R11 execution remains RETIRED/NOT_STARTED; Strategy and Owner-PC/MT5 runtime remain NOT_PROVEN.
