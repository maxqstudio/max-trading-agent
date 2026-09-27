# MAX Rebuild — Artifact Control Plane

Status: M08 ACCEPTED authority with R00 Research artifact extension in source candidate.

## Purpose

The Artifact Control Plane makes generated MAX data discoverable and controllable without turning file inventory into a second scientific authority.

Core rules:

- If MAX creates persistent data, MAX must be able to identify its producer, owner/source and lifecycle.
- Routine inspection, tracing, cleanup and deletion must not require Windows Explorer.
- Artifact metadata describes and controls files; Strategy/Optimizer/Champion scientific authority remains in the existing canonical DB/evidence contracts.
- Accepted historical evidence is protected and never rewritten merely to fit a new operational namespace.

## Current semantic namespaces

New operational output uses semantic roots:

- `artifacts/optimizer/<JOB-ID>`
- `artifacts/challengers/<CHALLENGER-ID>`
- `artifacts/backtests/<BT-ID>`
- `artifacts/challenger_operations/<operation>`
- `artifacts/research/<RESEARCH-ID>`

Milestone-specific paths are acceptance/history only. New normal runtime code must not create `MAX_M08_*` or `evidence/m08/backtests/*` namespaces.

Legacy accepted roots remain readable/protected for compatibility. They are not normal M08 write targets.

## Registry metadata

`artifact_registry` is inventory/control metadata. It does not replace Strategy scientific authority.

Each row exposes:

- artifact identity/type and producer;
- owner type/id;
- source type/id;
- created/updated UTC;
- canonical project/runtime path;
- registered runtime paths;
- byte size and SHA-256 when relevant;
- status/in-use state;
- retention class;
- delete/clean eligibility;
- dependency information.

Artifact identity is deterministic from artifact type, owner identity and canonical path.

## Retention

Four operational classes are used:

- `ACTIVE_AUTHORITY`: required current or accepted authority; not deletable through normal cleanup.
- `USER_GENERATED`: Owner-generated operational result; physically deletable when dependencies permit.
- `TEMPORARY_RUNTIME`: MAX-owned runtime residue; cleanable after the owning operation is inactive.
- `REGENERABLE`: generated data that can safely be recreated and may be cleaned/deleted.

Retention is not a reason to preserve garbage permanently. Dependency/in-use state is evaluated independently.

## Strategy lineage

The control plane exposes navigable relationships:

`Optimizer Job -> Round -> Qualified Candidate -> Challenger -> Backtest -> MT5 Report / Runtime`.

Historical pre-M08 lineage may contain `OPTIMIZER_WINNER` instead of Owner-selected qualified-candidate provenance. Historical records are not rewritten.

R00 extends the same registry rather than creating a second artifact authority. R00 registers immutable active-authority objects for:

- `RESEARCH_PARENT_MANIFEST`;
- `RESEARCH_HARDWARE_SNAPSHOT`;
- R00 input/output `RESEARCH_AUTHORITY_MANIFEST` evidence;
- persisted `RESEARCH_AUTHORITY_STATE`.

The R00 chain is Parent Strategy -> frozen parent manifest -> hardware/authority input -> deterministic R00 output -> persisted waiting state. Every R00 artifact is non-deletable active authority while the Research identity exists. Future Research artifact classes are contractual only until their owning gates are implemented.

## Qualified candidate ownership

Current optimizer jobs terminate at `QUALIFIED_POOL_READY` when one or more rows pass every frozen hard gate.

The normal Owner candidate API:

- reparses canonical MT5 XML and Weighted-R CSV;
- verifies report and sidecar SHA-256 and report identity;
- reapplies frozen finite-value, minimum-trades, PF, RF, Mean-R and Weighted-R gates;
- returns qualified rows only;
- reports raw/qualified/rejected counts;
- sorts, filters and paginates server-side.

Raw/rejected rows remain forensic round evidence. Rank is presentation only.

Owner-selected Challenger batches are prevalidated completely before mutation. New Challengers use `OWNER_SELECTED_QUALIFIED_CANDIDATE` provenance. Batch lifecycle is `PREPARED -> VALIDATED -> STAGED -> COMMITTED`; `FAILED` and `RECOVERY_REQUIRED` preserve deterministic retry semantics.

## Backtest result authority

Backtest performance metrics come only from the retained MT5 Strategy Tester HTML report bound by its registered SHA-256.

Normalized fields include, when present:

- total net/gross profit and gross loss;
- Profit Factor, Expected Payoff, Recovery Factor, Sharpe Ratio;
- total trades;
- profit/loss trade count and percentage;
- balance drawdown absolute/maximal/relative amount and percentage;
- equity drawdown maximal/relative amount and percentage.

Unavailable report metrics remain unavailable. Optimizer Mean R and Weighted R are not relabeled as Backtest metrics. They may appear in details only as `Optimizer Source Mean R` / `Optimizer Source Weighted R` context.

`Open Report` resolves the retained report from a registered Backtest identity and verifies its hash. There is no arbitrary-path report endpoint.

## Runtime inventory and cleanup

MT5 data-root authority is obtained from canonical MT5 detection/configuration; Windows user names and terminal hashes are not hard-coded.

Known MAX-owned Backtest runtime categories include:

- `MQL5/Experts/MaxMTF/ChallengerBacktests/<BT-ID>`;
- exact MAX Backtest Tester SET naming;
- exact MAX Backtest report naming.

`Clean Runtime` removes registered temporary runtime while preserving:

- Backtest DB identity;
- retained canonical report/result;
- performance metrics;
- Strategy lineage.

After successful cleanup the Backtest runtime status is `CLEANED`.

Known MAX-owned runtime with no DB owner is shown as `ORPHAN_RUNTIME`. Discovery never auto-deletes it.

## Physical deletion

M08 deletion is real deletion for generated objects when safe.

Backtest deletion removes the generated Backtest DB record, current semantic Backtest artifact directory, registered runtime residue and matching artifact metadata, then verifies absence. Accepted legacy Backtest evidence is protected.

Challenger deletion removes the generated Challenger DB row and bundle only when dependency preflight is clear. Blocking dependencies include current Champion authority, Backtest history/active Backtest, active promotion/operation, protected promotion history and protected legacy artifacts.

Optimizer job deletion is allowed only for inactive current-semantic jobs with no Challenger dependency.

## Path safety

All filesystem mutation is identity-based. The backend never accepts an arbitrary deletion path from the browser.

Before mutation it:

1. resolves the registered object/artifact;
2. resolves its allowed canonical root;
3. rejects non-absolute or traversal paths;
4. verifies resolved containment;
5. rejects symlink/reparse-point roots and escape paths;
6. verifies exact object naming/ownership where runtime identity is relevant;
7. performs deletion;
8. verifies physical absence.

Foreign paths fail closed.

## Bulk actions

Bulk clean/delete always begins with preflight showing:

- selected object count;
- deletable/cleanable count;
- blocked count and reasons;
- project bytes;
- runtime bytes.

Any blocked selection prevents the confirmed bulk action from silently partially mutating the set. Execution order follows dependencies: Backtests, Challengers, Optimizer jobs, then orphan runtime.

## Clean Generated Data

Global cleanup requires explicit confirmation and is blocked while active Optimizer, Backtest, promotion, Scientist provider call or current Champion authority exists.

Eligible current generated data is removed in dependency order. The action must never remove:

- source code;
- baseline EA;
- current required Strategy authority;
- accepted historical authority/evidence;
- provider/API settings.

Scientist operational chat data may be cleared only when safe; provider/settings configuration is not part of generated-data cleanup.

## Test and acceptance isolation

Unit/integration tests use deterministic temporary DB/filesystem fixtures and may inject canonical roots. They do not copy or depend on mutable Owner `state/max.db`.

Owner runtime acceptance is performed only after the exact GitHub candidate is frozen, the prior `D:\MAX_REBUILD` is deleted in full, and a clean clone is checked out at that exact SHA.

## Canonical Authority / Anti-Hardcoding Rules

The Artifact control plane consumes canonical runtime authority; it does not define a second Strategy truth. Project milestone and schema identity come from backend canonical authority, while EA version, EA SHA-256, Strategy contract and baseline status are bound to the verified baseline manifest. New producers must register semantic owner/source identities and write under semantic generated namespaces, never `MAX_M08_*` or `evidence/m08/*` operational paths.

Legacy `MAX_M06_*` and accepted milestone paths may be recognized only to preserve/read/clean known historical objects safely. Compatibility recognition must never cause new output to be written with historical naming.

## Fresh-Clone Acceptance

Before Owner-PC M08 acceptance, the GitHub candidate SHA must already be frozen. The old `D:\MAX_REBUILD` tree is then deleted entirely and recreated by a fresh clone at that exact SHA. No old SQLite state, evidence, generated artifacts, dependencies or build output is copied back. M08 E2E uses disposable data created after the fresh clone, and that data is cleaned using registered artifact/backtest/Challenger/Optimizer controls before Builder handoff.

This workflow proves that inventory, ownership, cleanup and deletion do not depend on hidden residue from earlier milestones or manual Explorer intervention.
