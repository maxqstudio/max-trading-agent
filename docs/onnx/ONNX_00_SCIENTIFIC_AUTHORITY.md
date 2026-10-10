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

- Current decision D-043 supersedes D-040's historical 14-family roster and any earlier Transformer-to-iTransformer universe amendment. D-040 remains intact as historical evidence with status SUPERSEDED.
- Standalone (exactly 9): lightgbm, xgboost, gru, tcn, itransformer, patchtst, tft, transformer_moe, lstm.
- Hybrid (exactly 14): each of gru, tcn, itransformer, patchtst, tft, transformer_moe, and lstm paired separately with lightgbm and xgboost.
- Total: exactly 23 first-class identities; no aliases, fallback models, or implicit hybrid compositions.
- Excluded: conventional standalone transformer, random_forest, all other unapproved model families, and all other unapproved hybrid compositions. The `transformer` exclusion does not ban attention/Transformer components within an explicitly approved architecture.
- D-044 records the new families' hyperparameter envelopes as proposed only and defines parameter-capacity accounting; it does not grant runtime/training authority.

Future-runtime backend preference carried from the Owner decision: LightGBM uses OpenCL GPU and XGBoost uses CUDA GPU. Synthetic CI may use CPU to test deterministic source behavior, but CPU-only CI is not GPU/runtime evidence. Temporal-device policy must be explicitly frozen before runtime implementation; it must not be silently assumed to be CPU-only.

## 2. Selective ModelLab adoption

All 19 authorities below were inspected at ModelLab main SHA 883ebeb1ca2e70f6255e0889358ba7c822ac52f5. The reference informs this plan; it is not copied wholesale and is not runtime authority for MAX.

| ModelLab authority | Decision | ONNX treatment |
|---|---|---|
| ModelLab/docs/research/CHAMPION_FACTORY_V3.md | ADAPT | Preserve hard stage boundaries, WFA-only pool admission, append-only failures, candidate identity, stage seals, and runtime-blocked repair isolation. Do not inherit exact-12 filling, legacy Top-1 promotion, automatic failure-cycle research, or unapproved legacy KPI profiles. |
| ModelLab/docs/research/POLICY_DISCOVERY_V1.md | RETIRE | No separate adaptive policy-discovery stage in ONNX V1. Do not reopen candidate thresholds or add a second model-selection authority. |
| ModelLab/docs/research/FEATURE_LABEL_AUDIT.md | ADAPT | Permit bounded upstream/pre-freeze diagnostics only. No downstream holdout feedback, post-freeze label changes, or new search loop without a governed new cycle. |
| ModelLab/docs/research/RESEARCH_ATOMIC_RESUME_V1.md | KEEP / ADAPT | Keep last-valid-committed-checkpoint semantics, immutable source binding, sequence/hash envelope, same-cycle recovery, and disposable uncommitted work. Bind the envelope to ONNX cycle, stages, candidate pool, budget, and survivors. |
| ModelLab/docs/research/HARDWARE_ADAPTIVE_MODEL_RESEARCH_V1.md | ADAPT | Keep deterministic hardware/resource measurement and the distinction between broad legal support and active recommendations. Replace dynamic family expansion with D-043's exact 23-family allowlist. Hardware may narrow effective search capacity, not change science or gates. |
| ModelLab/governance/MODEL_TRAINING_METHOD_CONTRACT_V1.json | ADAPT | Keep strict rejection of out-of-bounds concrete proposals, Strategy-derived label horizon, purged chronological early stopping, and temporal-to-tree purged OOF. Use the ONNX V1 family set and per-family contracts; ModelLab implementation details are not adopted automatically. |
| ModelLab/config/models/model_registry.json | RETIRE | Do not use its broader legacy registry as ONNX V1 family authority. The ONNX allowlist in the machine-readable authority is the sole V1 family list. |
| ModelLab/models/model_registry.py | RETIRE / ADAPT | Retire dynamic hybrid enumeration and legacy aliases. Adapt capability checks behind an explicit, frozen allowlist in a later implementation phase. |
| ModelLab/models/models.py | ADAPT | Reuse CandidateSpec/fingerprint and strict-admission concepts. ONNX identity must bind exact canonical params, cycle, dataset, Strategy geometry, feature/label contracts, windows, and evaluation contracts. Any AUTO canonicalization is recorded before identity freeze. |
| ModelLab/data/dataset_integrity.py | KEEP / ADAPT | Keep writer-safe snapshots, source hashes, and duplicate-identity detection. Exact-identical duplicates require deterministic backup/removal/revalidation; conflicting same-identity rows fail closed. Verify MAX CP32 identity fields before use. |
| ModelLab/data/data_quality.py | ADAPT | Keep finite-feature, data-sanity, discontinuity, and broker-proof distinctions. A timestamp gap alone is not missing-broker-data proof. Broker reconciliation is future runtime work; it was not run in ONNX-00. |
| ModelLab/data/labels.py | KEEP / ADAPT | Preserve causal labeling, exact Strategy geometry, SELL/SKIP/BUY order, effective label horizon, ambiguous-outcome policy, and context-only versus supervised-row distinction. No downstream holdout label tuning. |
| ModelLab/research/gru_research.py | ADAPT | Use only the approved GRU envelope and the ONNX temporal training contract: AdamW, purged chronological validation, best accepted checkpoint restoration, and frozen patience. |
| ModelLab/research/temporal_research.py | ADAPT | Preserve chronological sequence identity, training-only preprocessing, purged internal validation, best-checkpoint restoration, and fail-closed numeric safety. ModelLab architecture implementations are reference material, not MAX implementation authority. |
| ModelLab/research/hybrid_research.py | KEEP / ADAPT | Keep temporal-to-tree purged OOF stacking. A hybrid is one CandidateSpec; policy training cannot consume in-sample temporal predictions. Use only D-043's fourteen exact compositions. |
| ModelLab/host/preflight.py | ADAPT | Keep canonical training CSV auto-discovery, Owner path override, hash, and dataset summary concepts. Data Intake must create and validate an immutable snapshot before any experiment. |
| ModelLab/host/mt5_gap_repair.py | ADAPT | Keep only source-backed, authority-preserving MT5/EA gap-repair semantics for a future authorized Data Intake phase. No terminal discovery, launch, repair, or MT5 access occurs in ONNX-00. |
| ModelLab/factory/champion_factory.py | ADAPT | Adopt stage identity, hard-gate, lineage, and seal concepts. Do not port its monolithic execution engine, dynamic feedback cycles, legacy family roster, or unapproved gate/profile behavior. |
| ModelLab/ui/app.py | RETIRE | Do not port the Streamlit workspace. Use MAX React/FastAPI ownership and the exact eight-page ONNX information architecture below. |

NOT_USED for V1: ModelLab legacy family aliases, dynamic all-combinations hybrid roster, ModelLab-specific MoE implementation internals unless separately specified and accepted, Policy Discovery runtime, Streamlit UI, and any old numerical KPI profile not explicitly adopted here. Transformer MoE itself is an authorized family under D-043, with its own genuine architecture contract and open implementation/export gaps.

## 3. Model universe and parameter authority

The exact machine allowlist is authoritative; the family universe is not a registry expansion point.

| Temporal family | LightGBM composition | XGBoost composition |
|---|---|---|
| GRU | hybrid::gru::lightgbm | hybrid::gru::xgboost |
| TCN | hybrid::tcn::lightgbm | hybrid::tcn::xgboost |
| iTransformer | hybrid::itransformer::lightgbm | hybrid::itransformer::xgboost |
| PatchTST | hybrid::patchtst::lightgbm | hybrid::patchtst::xgboost |
| TFT | hybrid::tft::lightgbm | hybrid::tft::xgboost |
| Transformer MoE | hybrid::transformer_moe::lightgbm | hybrid::transformer_moe::xgboost |
| LSTM | hybrid::lstm::lightgbm | hybrid::lstm::xgboost |

Standalone identities are exactly: lightgbm, xgboost, gru, tcn, itransformer, patchtst, tft, transformer_moe, lstm. Conventional `transformer`, `random_forest`, aliases, fallbacks, and all other compositions are excluded.

### Existing active V1 search ranges — preserved

These existing ranges remain unchanged and are active search envelopes, not permanent legal maxima. `training_memory_months` is dynamic and frozen per cycle.

| Family | Existing parameters |
|---|---|
| LightGBM | n_estimators 150–1500; learning_rate 0.005–0.12 log; num_leaves 8–128; max_depth 3–12; min_child_samples 10–500; subsample 0.60–1.00; colsample_bytree 0.60–1.00; reg_alpha 0–20; reg_lambda 0.01–100 log; training_memory_months dynamic. |
| XGBoost | n_estimators 150–1500; max_depth 2–10; learning_rate 0.005–0.12 log; min_child_weight 0.5–100 log; subsample 0.60–1.00; colsample_bytree 0.60–1.00; reg_alpha 0–20; reg_lambda 0.01–100 log; training_memory_months dynamic. |
| GRU | sequence_length 24–192; hidden_size 32–256; num_layers 1–3; dropout 0–0.35; learning_rate 0.0001–0.003 log; batch_size 32–512; max_epochs 20–120; weight_decay 0.000001–0.02 log; training_memory_months dynamic. |
| TCN | sequence_length 32–256; tcn_channels 32–192; tcn_blocks 2–6; kernel_size 2–5; dropout 0–0.35; learning_rate 0.0001–0.003 log; batch_size 32–512; max_epochs 20–120; weight_decay 0.000001–0.02 log; training_memory_months dynamic. |
| PatchTST | sequence_length 64–256; d_model 32–192; num_layers 1–4; attention_heads 1–8; ffn_mult 2–4; patch_len 8–48; patch_stride 4–24; dropout 0.05–0.35; learning_rate 0.00005–0.002 log; batch_size 16–256; max_epochs 20–100; weight_decay 0.000001–0.02 log; training_memory_months dynamic; patch_stride <= patch_len <= sequence_length; d_model divisible by attention_heads. |

### D-044 proposed active envelopes — not yet executable authority

Every range below is `PROPOSED_ACTIVE_SEARCH_ENVELOPE`: not a permanent legal maximum, benchmark, guarantee, or executable gate until accepted and versioned through the applicable governance process. No unlisted value may be filled by an implicit default.

| Family | Proposed ranges and fixed/proposed semantics |
|---|---|
| iTransformer | sequence_length 32–512; d_model 32–384; num_layers 1–6; attention_heads 1–12; ffn_mult 2–8; dropout 0.05–0.35; learning_rate 5e-5–2e-3; batch_size 16–256; max_epochs 20–100; weight_decay 1e-6–0.02; proposed patience 8. `embedding_mode=variates_as_tokens`; input_variates derive from verified feature contract; d_model divisible by heads; projection exactly matches lookback; output head binds label contract. |
| TFT | sequence_length 32–384; hidden_size 32–256; LSTM temporal layers 1–3; attention_heads 1–8; GRN hidden_size 32–512; `ffn_mult` remains architecture-specific and requires an exact versioned contract (no numeric default); dropout 0.05–0.35; learning_rate 5e-5–2e-3; batch_size 16–128; max_epochs 20–100; weight_decay 1e-6–0.02; proposed patience 8. Variable-selection configuration/sharing must be explicit; horizon is frozen by labels. Static, observed-history, or known-future inputs are permitted only when source-backed and actually available; never synthesize unsupported inputs. |
| Transformer MoE | sequence_length 32–384; d_model 64–384; num_layers 1–6; attention_heads 1–12; ffn_mult 2–4; num_experts 2–16; top_k 1–4; expert_ffn_mult 2–4; capacity_factor 1.0–2.0 and router_aux_loss_weight 1e-4–0.1 where applicable; dropout 0.05–0.35; learning_rate 1e-5–1e-3; batch_size 8–128; max_epochs 20–100; weight_decay 1e-6–0.02; proposed patience 10. Require 1 <= top_k <= num_experts and d_model divisible by heads. Routing, overflow, sharing, auxiliary loss, and deterministic behavior must be versioned. |
| LSTM | sequence_length 24–192; hidden_size 32–256; num_layers 1–3; dropout 0–0.35; learning_rate 1e-4–3e-3; batch_size 32–512; max_epochs 20–120; weight_decay 1e-6–0.02; proposed patience 6. Proposed causal configuration is unidirectional with projection_size 0; state initialization/reset must be deterministic and reset at sequence/fold/window boundaries. Effective recurrent dropout is zero for a single layer where the framework only applies it between layers. |

### Architecture identity and remaining authority gaps

- **iTransformer:** genuine inverted multivariate representation: variates are tokens and attention is variate-oriented. It is not a renamed conventional time-token Transformer. The exact implementation/module ledger, legal envelope, hardware validation, and ONNX/runtime support remain to be proven.
- **TFT:** genuine variable selection, temporal processing, gating/GRN, and attention. Static context and known-future inputs are conditional on verified MAX schema support; observed historical inputs must be source-backed. If a required covariate is absent, reject the candidate. Exact implementation/module ledger, sharing policy, and compatible ONNX operations remain gaps.
- **Transformer MoE:** genuine attention plus routed expert networks; no dense Transformer fallback. Exact expert/router variant, deterministic tie behavior, capacity/overflow, auxiliary-loss applicability, memory estimator validation, and ONNX/runtime support remain gaps.
- **LSTM:** genuine unidirectional causal recurrence; no bidirectional context or hidden-state carryover between independent samples/folds/windows/cycles. Exact state initialization contract and runtime/module validation remain gaps.
- **GRU, TCN, and PatchTST:** retain their existing architecture and active-envelope contracts; expansion does not rewrite those identities.

Parameter resolution remains:

LEGAL ENVELOPE → hardware feasibility → dataset/sample adequacy → budget feasibility → ACTIVE SEARCH ENVELOPE → exact immutable CandidateSpec.

Available VRAM/RAM, compute capability, dataset samples/features/effective samples, sequence geometry, architecture, training budget, experiment and checkpoint storage all constrain the active envelope. More hardware alone does not authorize a larger model. Reject an over-capacity or out-of-envelope proposal with a concrete reason; never silently clamp. No permanent trainable-parameter ceiling is authorized.

Hybrid candidates combine one exact temporal identity and one LightGBM/XGBoost policy identity. Temporal parameters are prefixed `temporal_`, policy parameters `policy_`, and one `training_memory_months` is shared. Each exact composition is one immutable CandidateSpec and hash identity; artifacts cannot cross family/composition boundaries.

### Parameter and capacity accounting contract

Every exact CandidateSpec must report architecture/version, feature/output dimensions, sequence length, exact parameters, trainable/non-trainable/total parameters, and applicable active/expert/shared parameter counts. It also reports weight, gradient, optimizer-state, activation-peak, total peak-training, and inference bytes plus estimation method, assumptions, and confidence. Non-applicable fields say `NOT_APPLICABLE`; unknown memory or architecture terms remain `OPEN_AUTHORITY_GAP`, never fabricated zeros.

The estimator separates `RAW_DECLARED_LEDGER_COUNT` from `CANDIDATE_SPEC_STATICALLY_VALIDATED`. A tensor ledger is only the arithmetic sum of declared shapes; role labels alone do not prove architecture topology. GRU/LSTM may be labeled statically validated only for the exact supported unidirectional, bias-enabled, unprojected variant with a linear output head, when each named per-layer recurrent tensor and output-head tensor has the exact CandidateSpec-bound shape and the ledger sum equals the approved formula. The counterexample GRU(32 inputs, hidden 8, one layer, 3 outputs) cannot accept an arbitrary 120-count ledger; its supported formula is 1,035. TCN, PatchTST, iTransformer, TFT, and Transformer MoE remain raw-ledger estimates with `OPEN_AUTHORITY_GAP` until their exact topology, tensor identities/shapes, and sharing are validated. No ONNX-00 estimate is `MEASURED_INSTANTIATED_MODEL`; actual trainable count remains `sum(p.numel() for p in model.parameters() if p.requires_grad)` when a future authorized model exists.

MoE accounting binds `expert_ffn_mult` to expert width and uses `ffn_mult` only when the selected architecture explicitly contains a shared dense FFN; otherwise its contribution is `NOT_APPLICABLE`. `num_experts` controls all stored expert weights and `top_k` controls active expert weights per token. Router, shared attention, optional shared FFN, and experts are separate ledger groups; role/group conflicts, missing/extra expert indices, duplicate tensor IDs, unsupported sharing, and invalid `top_k` fail closed. Unequal expert sizes produce an active-count range (or `OPEN_AUTHORITY_GAP` if routing restrictions are not specified), never an assumed representative expert. Memory weight bytes use all stored weights, not active MoE weights. Static structural estimates do not establish peak VRAM or ONNX compatibility. LightGBM/XGBoost report tree structure separately from neural parameter counts. Hybrid accounting adds temporal, tree, OOF intermediate storage, and applicable overhead without assuming shared encoders.

FP32 AdamW planning baseline is approximately 16 bytes per trainable parameter for parameters, gradients, and two moment buffers, excluding activations, temporary/framework buffers, and mixed-precision master weights. Parameter-memory fit is not total training feasibility. The estimator contract and Owner-supplied illustrative counts are in `.workflow/onnx_v1_authority.json`; no model was instantiated in ONNX-00, so those counts are not measured evidence.

The supplied illustration uses 32 inputs, 3 outputs, lookback 128, recurrent hidden size 128, dense Transformer width 128 × 3 layers with FFN ×4, TCN 4 blocks × 2 convolutions × kernel 3, PatchTST patch 16/stride 8, and MoE 8 experts/top-2 with expert FFN ×4 and no shared-FFN term in that illustrative formula. Approximate scales are GRU 0.26M, LSTM 0.35M, TCN 0.36M, iTransformer 0.61M, PatchTST 0.60M, TFT roughly 3–6M (implementation-dependent), and Transformer MoE 3.37M total / 0.99M active per token. These are illustrative only; TFT's range is not an exact regression invariant, and the MoE example does not close the architecture gap.

Additional illustrative scaling: GRU 256 hidden × 3 layers ~1.01M; LSTM same ~1.35M; TCN 192 channels × 6 blocks × 2 convolutions × kernel 5 ~2.06M; iTransformer d_model 384 × 6 layers × FFN ×4 × lookback 512 ~10.85M; MoE d_model 384 × 6 layers × 16 experts/top-2/FFN ×4 ~117M total / 17.7M active per token. Seven reference standalone temporal models total ~8.5–11.5M; independently instantiating those encoders for fourteen hybrids implies ~25.5–34.5M plus 16 tree models portfolio-wide, not a single simultaneous network. None of these examples is a VRAM-fit guarantee or permanent parameter ceiling.

## 4. Training, identity, and evidence

All seven temporal families use AdamW, purged chronological internal validation, immutable CandidateSpec, deterministic experiment fingerprints, source-backed dataset snapshots, versioned preprocessing/feature contracts, finite-value fail-closed checks, and checkpoint integrity. There is no random train/validation split. Existing patience is GRU 6, TCN 8, PatchTST 8. D-044 proposes iTransformer 8, TFT 8, Transformer MoE 10, and LSTM 6; these four values are not executable until accepted and versioned. No family silently inherits another family's patience. Required CPCV seed confirmation remains ordered 42, 11, 77 for every temporal and hybrid candidate. If GRU num_layers is 1, effective recurrent dropout is zero. max_epochs is an upper budget, not a promise to train every epoch.

Preprocessing is fit only on the eligible training partition. Early stopping uses purged chronological internal validation, never randomized splitting. Restore the minimum accepted validation-loss checkpoint before downstream evidence. Preserve original row identity across sequences and folds.

Hybrid policy training requires TEMPORAL_TO_TREE_PURGED_OOF_STACKING: split the permitted training region chronologically; fit the temporal component on each inner-train partition; purge by at least the effective label horizon; generate temporal meta-features only on the corresponding inner-validation rows; then combine these out-of-fold values with permitted CP32 policy features. In-sample temporal predictions for policy training are forbidden. Final permitted fit is separate from qualification evidence.

CandidateSpec is immutable after freeze and records exact case-sensitive family/composition ID, architecture contract/version, component identities/order, exact parameters and module ledger, training configuration/early-stopping contract, seed, shared training window, class/output contract, and any Owner-approved policy fields. CandidateSpec SHA-256 uses versioned deterministic serialization. The experiment fingerprint additionally binds cycle ID, immutable dataset snapshot SHA, Strategy geometry, feature/label contracts, all three windows, WFA/CPCV/Tournament/Monte Carlo/KPI contracts, parameter authority version, resolved active envelope, and CandidateSpec hash. Exact repeated fingerprints do not consume a second exposure; all failures remain in an append-only ledger with first_failed_gate.

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
3. DISCOVERY → proposals from the exact D-043 23-family allowlist; validate each selected family's accepted architecture/parameter authority and CandidateSpec; fingerprint and deduplicate. D-044 proposals alone do not make a family executable.
4. CHEAP SCREEN → compute allocation/obvious-breakage filter only; no qualification authority. Every selected candidate reruns its original full CandidateSpec.
5. FULL WFA → exactly three chronological folds with required purge; candidate identity cannot change across folds. Only WFA_PASS enters the pool; ranking applies only among passes.
6. QUALIFIED POOL → maximum 12. At least 12 passes: freeze best 12 under predeclared Discovery ranking. One through 11: freeze all actual passes. Zero: NO_QUALIFIED_CANDIDATE and terminate. Never lower KPI, secretly extend budget, add families, or elevate Cheap Screen results to fill the pool.
7. CPCV → every WFA-qualified candidate; six groups, two test groups, exactly 15 combinations; purge and embargo each at least the effective label horizon. No mutation or threshold tuning. Ranking cannot rescue failure. Temporal and hybrid candidates require ordered seeds 42, 11, 77; all required confirmations pass. A failure remains a hard failure.
8. TOURNAMENT → only the independent Tournament window and every CPCV survivor. No hyperparameter search, architecture change, threshold tuning, model substitution, or same-cycle retraining from Tournament failures. Pass advances; fail is terminal for that cycle.
9. MONTE CARLO → every Tournament survivor; default 10,000 bootstrap-with-replacement simulations per candidate. Exact config and seed are frozen before the cycle. It changes no training parameters and emits a hard gate.
10. FORWARD → mandatory untouched Forward window after all prior gates. No tuning or parameter changes. PASS, FAIL, or INSUFFICIENT_SAMPLE. PASS is necessary to start that candidate's post-Forward path, but it does not make the candidate scientifically qualified, runtime-ready, Challenger-ready, or promotion-eligible by itself.
11. SCIENTIFIC QUALIFICATION / FINAL FIT → a candidate becomes SCIENTIFICALLY_QUALIFIED only after WFA, CPCV, independent Tournament, mandatory Monte Carlo, and untouched Forward all PASS with verified seals. The exact final-fit boundary is frozen before Forward is opened. Final fit and preprocessing may use only permitted pre-Forward training rows; Forward outcomes/targets cannot train, refit preprocessing, tune, select, or alter the candidate.
12. ONNX / RUNTIME → the per-candidate order is FINAL_PERMITTED_FIT → ONNX_EXPORT → ONNX_PARITY → RUNTIME_MANIFEST_VALIDATION → CHALLENGER_REGISTRATION. Commit the artifact hash and evidence; validate parity under the pre-frozen tolerance; validate the versioned manifest against the exact candidate, artifact and upstream seals. CHALLENGER_READY is committed only after registry persistence and readback verify every binding.
13. CANDIDATE FAILURE / RECOVERY → a fit/package/export/parity/manifest/registration failure becomes candidate-scoped RUNTIME_BLOCKED. It does not invalidate or block other candidates. A bounded runtime-only repair resumes that same candidate from its last valid runtime checkpoint, reuses sealed science evidence, and never reruns Discovery, WFA, CPCV, Tournament, Monte Carlo, or Forward. If repair is unrecoverable, mark only that candidate RUNTIME_BLOCKED_UNRECOVERABLE and fail closed.
14. CHAMPION → explicit Owner action only, after selecting one exact CHALLENGER_READY candidate. Independently verify all upstream gates, lineage, stage seals, artifact hash/parity, runtime manifest and registry identity; commit atomically and archive prior Champion lineage. Rollback requires verified archived artifact/hash/parity.

Cheap Screen PASS is not scientific PASS. No downstream rank or recommendation overrides an upstream hard FAIL. No automatic R03 equivalent, Qualified Pool admission from screens, automatic winner selection, or Champion promotion exists.

### KPI boundary

The inspected ModelLab V3 values—Full-WFA/CV aggregate expectancy >= 0.00R and CPCV worst aggregate expectancy >= 0.00R—are preserved as scientific reference/proposed thresholds only. ModelLab provenance is not MAX Owner authorization: neither value is an approved executable hard gate. ONNX-00 invents or activates no KPI threshold. Before ONNX-03 execution, the Owner must authorize a complete versioned KPI contract defining Expectancy/Mean R, Profit Factor, Recovery Factor, Drawdown, sample adequacy/trade count, temporal stability, out-of-sample performance, and Monte Carlo tail risk, including definitions, aggregation, scope and thresholds. Freeze that contract before cycle start; it cannot change within a cycle. Monte Carlo remains a mandatory gate, but its numeric acceptance criteria are not authorized until that contract is approved.

## 7. Cycle state, checkpoints, and artifacts

Cycle START freezes cycle ID; dataset and snapshot; Strategy geometry; feature/label contracts; three windows; model universe; parameter authority and active envelopes; WFA/CPCV/Monte Carlo configuration; KPI authority; experiment budget; Scientist configuration; and hardware/resource snapshot. Any scientific-contract change requires a new cycle.

The cycle-level states, guarded transitions, candidate-level runtime pipeline, disposition contract, promotion recovery, and terminal states are in `.workflow/onnx_v1_authority.json`. After `FORWARD_RUNNING`, only Forward-PASS candidates enter independent per-candidate pipelines: `SCIENTIFICALLY_QUALIFIED → FINAL_PERMITTED_FIT → ONNX_EXPORT → ONNX_PARITY → RUNTIME_MANIFEST_VALIDATION → CHALLENGER_REGISTRATION → CHALLENGER_READY`. Therefore Forward PASS alone cannot create a ready Challenger. Each stage requires committed evidence and exact candidate-bound seals. Runtime blockage belongs to one candidate; another ready candidate remains independently eligible and a blocked candidate retains its own repair authority. If all Forward-PASS candidates become unrecoverably blocked and none is ready, the cycle ends `RUNTIME_NO_SURVIVOR`. `INSUFFICIENT_SAMPLE` is not PASS. Promotion remains a separate exact-candidate Owner action.

Promotion eligibility is independent of cycle-closure eligibility: the Owner may promote one exact `CHALLENGER_READY` candidate without waiting for unrelated candidates to finish. Each Forward-PASS candidate retains its immutable identity, independent runtime cursor, checkpoint, evidence, and disposition. Promoting A must not silently cancel, invalidate, or discard B. `CHAMPION_ACTIVE` is not a cycle-terminal state while any candidate cursor or disposition remains open; active candidate work continues only under the same open cycle and its existing identity/checkpoint. No post-cycle candidate continuation is authorized here. Safe cycle closure requires a committed, verified terminal disposition for every Forward-PASS candidate (`PROMOTED`, `OWNER_DECLINED`, `OWNER_CANCELLED`, or `RUNTIME_BLOCKED_UNRECOVERABLE`), no active candidate cursor or owned process, and verified cycle evidence. Cancellation requires an explicit Owner action, exact candidate identity, a verified safe stop, and durable disposition/lineage; an unverified stop enters `RECOVERY_REQUIRED`. A candidate failure cannot change another candidate's scientific PASS, evidence, or repair path.

Candidate disposition is atomic and idempotent. After a crash, read back the exact disposition transaction: reuse committed evidence without duplicate exposure, resume only the same uncommitted transaction, or remain fail-closed in `RECOVERY_REQUIRED` when evidence is uncertain. A blocked candidate resumes from its own last valid committed runtime checkpoint without replaying completed work.

Promotion is also a candidate-bound transaction. Recovery preserves `original_state`, cycle/candidate identity, transaction ID, Owner authorization ID, and prior Champion identity. Before commit, it distinguishes absent from durable Owner authorization, verifies exact candidate preconditions, never assumes success, and resumes only the same transaction after proving commit did not occur; if authorization is absent, it returns to the pending Owner decision without creating one. After commit, readback must verify the exact Champion, artifact publication, and prior-Champion archive before reconciling projections. An already committed promotion is never replayed. Missing, corrupt, or conflicting evidence remains `RECOVERY_REQUIRED`; promotion recovery cannot resume scientific training, auto-promote, auto-rollback, or start a new cycle. Rollback is a separate explicitly Owner-authorized operation. A promotion-origin recovery cannot route through the generic paused scientific pipeline or close the cycle while uncertain.

Any running scientific stage may pause only at a safe boundary with an atomically committed checkpoint; resume targets that same stage and must not repeat completed exposures. Authority, checkpoint, process-ownership, candidate cursor, promotion transaction, or durable-write uncertainty enters `RECOVERY_REQUIRED`; exit requires the appropriate verified same-stage checkpoint or promotion transaction reconciliation, or an allowed explicit cycle abandonment with all candidate dispositions committed and failure evidence preserved. Terminal outcomes return to `NO_CYCLE` only after their closure guards pass; a new cycle requires a new explicit Owner start. No ranking result, UI action, or recovery path may bypass these transitions. Force Stop requires verified process ownership.

The only resume authority is LAST VALID COMMITTED CHECKPOINT. It binds cycle ID, dataset SHA, science-contract hash, stage/state, candidate pool and identities, tested fingerprints, budget consumption, survivor identities, monotonic sequence, and payload SHA-256. Commit is atomic. If the newest checkpoint is corrupt, validate and fall back to the previous valid checkpoint and resume the same cycle. Uncommitted work is disposable. Resume must not create a cycle or repeat completed exposures. Stage seals bind the cycle/science identity, exact candidate, previous terminal seal, configuration, and immutable evidence hashes.

## 8. Product and UI ownership

Top-level navigation: Strategy, ONNX, Artifacts, Settings. ONNX has exactly eight pages: Overview, Data Intake, Discovery, CPCV, Tournament, Monte Carlo, Challenger, Champion. There is no separate Pool page; Pool is internal Discovery state. There is no separate Forward page; Forward is the first Challenger-admission section. The reusable Scientist drawer remains advisory.

Overview summarizes cycle and dataset IDs/SHA, symbol/timeframe, frozen windows, science and hardware status, Discovery budget/experiments, qualified pool, survivors at every stage, Challengers/recommendation, ONNX Champion, current stage, last committed checkpoint, first blocker, and resume availability. It does not duplicate stage controls.

React displays state, requests actions, confirms, presents progress/evidence/errors. Backend owns validation, transitions, family/parameter admission, CandidateSpec, KPI, stage PASS/FAIL, checkpoints, evidence, and promotion permission. ONNX-00 defines no API endpoints. ModelLab Streamlit UI is retired, not ported.

## 9. ONNX-00..ONNX-09 roadmap

| Phase | Scope | Exit boundary |
|---|---|---|
| ONNX-00 | Scientific authority, exact D-043 23-family allowlist, D-044 proposed hyperparameters/capacity accounting, hybrid/training/parameter/data/state/evidence/UI/API ownership, and roadmap. | Planning/governance only; proposed envelopes and unresolved gaps do not authorize runtime. Control Room audit before merge. |
| ONNX-01 | Eight-page React shell and backend API/state skeleton. | No real science execution. |
| ONNX-02 | Data Intake, DQ, duplicate policy, broker reconciliation/gap-repair authority, three windows, immutable snapshot. | DATA_READY evidence. |
| ONNX-03 | Causal labels, 23-family Discovery, CandidateSpec/fingerprint, Cheap Screen, Full 3-fold WFA, max-12 pool, Discovery checkpoints. | Complete Owner-authorized KPI contract and selected-family architecture/input/parameter/patience/hardware/capacity/compatibility authority must be frozen before execution; unresolved gaps fail closed. |
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
