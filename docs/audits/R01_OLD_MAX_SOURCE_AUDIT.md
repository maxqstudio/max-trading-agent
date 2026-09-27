# R01 Old MAX Source Audit

Historical source authority:

`maxqstudio/max_research_agent@3e969efcdeb4ca6a2ae63acbd80592e378d2a446`

This audit is source-based. MAX Rebuild remains a rebuild of MAX MTF Old concepts while current R00 parent Strategy authority, `MAX_TRUE_MTF_DYNAMIC_V1`, `CP32_TRUE_MTF_V1`, and `TRUE_MTF_LOG_RATIO_V1` determine current geometry and execution details.

## Classification authority

Only these classifications are used:

- `INHERIT`
- `INHERIT + DEFECT REPAIR`

The historical concept remains design authority. Where the historical implementation contains stale constants, obsolete geometry, incomplete evidence, or a scientific defect, R01 inherits the concept and repairs that defect without replacing the concept.

## Source-concept map

| Historical source concept | Classification | R01 treatment |
| --- | --- | --- |
| Immutable original source-row identity | INHERIT | R01 assigns and preserves contiguous `source_row_id` chronology and never compresses supervised indices into fake adjacency. |
| Context rows remain physically present when targets are unusable | INHERIT | Target-invalid, ambiguous, incomplete-horizon, and protected-boundary-excluded rows remain physical context rows. |
| First-barrier long/short target construction | INHERIT + DEFECT REPAIR | Barrier direction, bid/ask and time-exit intent are inherited; current SL/TP/MaxHold authority comes from the frozen R00 parent and arbitrary historical edge/margin thresholds are not part of the R01 label contract. |
| SELL / SKIP / BUY directional target intent | INHERIT + DEFECT REPAIR | R01 freezes `0=SELL, 1=SKIP, 2=BUY` under the current parent execution geometry while preserving the old bounded-future target concept. |
| Future information may exist only in supervised target construction | INHERIT | CP32 features remain causal; target construction alone may inspect the bounded future horizon. |
| Temporal target purge before validation/evaluation | INHERIT + DEFECT REPAIR | R01 now applies the historical strict boundary rule to Discovery→Locked and Locked→Fresh using current dependency authority and original source-row chronology. |
| Original-row chronology for discontinuous supervised sets | INHERIT | Boundary eligibility uses original `source_row_id`, never compressed supervised position. |
| Purge/embargo derived from dependency authority | INHERIT + DEFECT REPAIR | Historical temporal separation is inherited; current R01 derives horizon/purge/embargo from the frozen parent, CP32/MTF/relative dependencies and candidate-extension rule instead of a legacy fixed constant. |
| Timezone-aware chronology, duplicate detection, monotonicity, finite/OHLC validation | INHERIT + DEFECT REPAIR | R01 fails closed on chronology/source defects and requires explicit timezone/source authority. |
| Latest fully closed role-bar alignment | INHERIT | Current role geometry uses the same causal as-of principle. |
| Exact main/relative timestamp pairing | INHERIT | Relative Value inputs remain timestamp paired across the complete dependency window. |
| Historical fixed M5/M15/H1/H4 topology | INHERIT + DEFECT REPAIR | The multi-timeframe concept is inherited while role timeframes are resolved from the frozen current parent rather than fixed historical values. |
| Native feature parity and broker/feed source identity | INHERIT + DEFECT REPAIR | Evidence intent is inherited and generalized to exact current role timeframes, current parent identity and managed MT5 source authority. |
| Future-perturbation leakage attacks | INHERIT + DEFECT REPAIR | R01 expands the inherited attack principle across all CP32 families, higher timeframes, relative symbol, target contamination and protected-boundary target dependency. |
| Deliberate leakage negative control | INHERIT | A known-leaky synthetic feature must still be detected. |
| Immutable source hashes and deterministic provenance | INHERIT | R01 seals source, dataset, partition, dependency and leakage evidence. |

## Mandatory temporal inheritance

### Historical implementation

Exact source:

`ModelLab/core/temporal_index.py`

at:

`maxqstudio/max_research_agent@3e969efcdeb4ca6a2ae63acbd80592e378d2a446`

The inherited temporal authority is explicit in:

- `purged_internal_earlystop_split`
- `purge_train_before_validation`
- `expanding_folds`

`purged_internal_earlystop_split` preserves original row ids and uses:

```text
effective_purge = max(label_horizon_bars, purge_bars)
keep upstream row t only when:
t < first_validation_row - effective_purge
```

It then proves:

```text
latest_target_end = last_legal_upstream_row + label_horizon_bars
latest_target_end < first_validation_row
```

The strict inequality is authoritative. A target that touches the evaluation boundary is not legal upstream supervision.

The same module explicitly states that feature/context rows are not physically removed; only supervised target eligibility is purged. `purge_train_before_validation` also operates on original row identities and preserves discontinuous chronology rather than compressing it into false adjacency.

### Historical outer split

Exact source:

`ModelLab/models/model_lab.py`

The historical `split_locked_test` and `research_region` functions preserve the concept that a later evaluation region is distinct from upstream research and that temporal separation is applied around the boundary.

### Historical labels

Exact source:

`ModelLab/data/labels.py`

The historical label path preserves `source_row_id`, keeps context-only rows physically present, and computes bounded-future directional outcomes. R01 retains that physical-row/context principle while using the current frozen parent label geometry.

## Batch-3 classification

Old concept:

`temporal target purge at validation/protected boundary`

Classification:

`INHERIT + DEFECT REPAIR`

Confirmed pre-repair R01 defect:

R01 physical partition ownership was correct, but Batch-2 Discovery public eligibility was primarily selected by:

```text
signal_time ∈ Discovery
```

A Discovery-owned row near `discovery_to` could therefore retain an internally valid label whose MaxHold target dependency reached Locked OOS. The same defect existed at Locked OOS→Fresh for future R07 evaluation.

## Exact R01 repair

Production authority is implemented in:

`backend/max_backend/research_leakage.py`

Functions:

- `bind_protected_target_dependency_authority`
- `boundary_safe_partition_rows`
- `partition_supervised_eligible`

The sealed contract is:

`MAX_RESEARCH_PROTECTED_TARGET_DEPENDENCY_R01_V1`

It binds:

- historical source reference;
- original `source_row_id` authority;
- label horizon;
- requested minimum legal purge;
- applied boundary purge;
- minimum legal embargo;
- Discovery→Locked boundary evidence;
- Locked→Fresh boundary evidence;
- physical partition/context preservation;
- strict overlap checks.

Current rule:

```text
applied_boundary_purge_main_bars =
max(label_dependency_main_bars, minimum_legal_purge_main_bars)

boundary-safe upstream row iff:
source_row_id <
first_downstream_source_row_id - applied_boundary_purge_main_bars
```

This directly inherits the old strict temporal rule. No supervised index compression is used.

For every sealed boundary R01 proves:

```text
latest_boundary_safe_target_end_source_row_id
<
first_downstream_source_row_id
```

### Discovery public/adaptive evidence

`backend/max_backend/research_r01_service.py::_discovery_label_summary`

now uses only:

```text
Discovery-owned
AND target-valid
AND internally supervised-eligible
AND boundary-safe against Locked OOS
```

A cross-boundary Discovery row:

- remains physically owned by Discovery;
- remains in the sealed full dataset;
- keeps its deterministic internal label/outcome evidence;
- is context-only for Discovery supervision/public statistics;
- cannot affect Discovery SELL/SKIP/BUY counts, ratios or target-derived summary fields.

The summary records structural boundary evidence including boundary-excluded rows, effective purge, last eligible Discovery row, latest legal target-end row, first Locked row and overlap status.

### Locked OOS→Fresh authority

R01 does not execute R07 or R08. It seals the same inherited boundary contract for Locked OOS→Fresh so future Locked evaluation cannot use a label whose target dependency reaches Fresh/Forward.

No Locked/Fresh class balance or protected target outcome is exposed by this repair.

## Adversarial evidence

The production leakage suite exercises the production boundary functions.

Attack 22:

`DISCOVERY_TO_LOCKED_TARGET_DEPENDENCY`

A synthetic Discovery decision has a target horizon that touches the first Locked bar. Only that downstream target bar is changed. The internal target changes, but production boundary eligibility excludes the row and the public/adaptive eligible projection remains invariant. A row ending strictly before the boundary remains eligible.

Attack 23:

`LOCKED_TO_FRESH_TARGET_DEPENDENCY`

The same production-path attack is applied to Locked OOS→Fresh. A Locked decision whose target touches Fresh is not legal future Locked-evaluation supervision.

The deliberate leak negative control remains separate and must still be detected.

## Resulting R01 flow

```text
explicit managed source authority
-> chronology validation
-> current parent MTF geometry / closed-bar alignment
-> exact CP32 reproduction + parity
-> parent-bound internal SELL/SKIP/BUY labels
-> physical protected partition ownership
-> inherited target-boundary purge authority
-> Discovery boundary-safe public/adaptive supervision
-> sealed Locked→Fresh future evaluation authority
-> adversarial leakage suite
-> immutable dataset + manifests + evidence
-> atomic R01 terminal publication
-> STOP / waiting Owner
```

No R01 runtime, model training, ONNX, R02, Research Challenger, Champion mutation, or automatic next gate is authorized by this source repair.
