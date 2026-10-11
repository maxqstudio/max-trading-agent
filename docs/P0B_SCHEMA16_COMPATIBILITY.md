# P0B Schema-16 Compatibility Decision

Status: design authority for the P0B source phase; implementation and synthetic acceptance are not yet proven.

## Scope and evidence

The source baseline is the accepted P0A main commit `8ba001bba31d7c74e2d1d1aa03d6c79fb0f9576a`. The schema history was inspected directly in these commits:

| Commit | Source evidence | Schema effect |
|---|---|---|
| `203f6ab3dc618e3edd247841aa25e1fa0951a19a` | `backend/max_backend/schema.py`, `backend/max_backend/workflow_store.py` | Schema 15 application baseline. |
| `c75f978ea4438183e6a759d398eb69b3eeca9ac4` | ONNX-00 governance-only tree diff | No database schema change. |
| `1a9c9a6990e1f2571e4d85d79436402e6d37ec23` | ONNX-01 workspace/API tree diff | No database schema change. |
| `98ac8477646bafd608650f3a30de8a3c38b2df68` | `backend/max_backend/schema.py`, `backend/max_backend/onnx_data_store.py`, `backend/max_backend/workflow_store.py::migrate_current` | Schema 15 → 16; adds three ONNX data tables, two named indexes and six immutability triggers. |
| `39f7a7ea9da943e4ce72b74a5264395c1edd6888` | Final pre-rollback source tree | No further schema change after ONNX-02. |

The historical archive tables are `onnx_data_snapshots`, `onnx_data_window_configs`, and `onnx_data_readiness_evidence`. Their declared foreign keys retain snapshot ancestry and bind window/readiness records to snapshot identities. Two indexes support creation-time and snapshot/revision reads. Six triggers reject UPDATE/DELETE on the three archive tables. Exact DDL, columns, indexes, foreign keys, constraints, and trigger bodies must be verified by the implementation's structural profile; names alone are insufficient.

Important historical side effect: schema-16 `backend/max_backend/workflow_store.py::migrate_current` called the ONNX table creator and also called `backend/max_backend/workflow_store.py::_purge_research_authority`, which deletes Research-tagged `artifact_registry` rows, Research-prefixed `schema_meta` keys, and Research-prefixed SQLite objects. P0B must never replay this purge. If an earlier real migration already removed records, this compatibility work cannot recreate them; actual Owner contents remain unverified.

No historical schema transition was found in ONNX-00 or ONNX-01. No direct foreign key from the ONNX archive tables to `artifact_registry` was found; stored lineage/path/hash values are data fields. Core Strategy, Optimizer, Scientist, Champion and artifact tables were not intentionally changed by the schema-16 ONNX DDL.

## Chosen compatibility strategy

Keep the MAX application schema version at 15. Do not write schema version 15 into a version-16 database, do not bump the application to 16 merely to bypass P0A, and do not run the historical schema-16 migration implementation.

Introduce a single read-only structural compatibility profile for the exact historical schema-16 archive extension. It accepts schema 16 only when:

1. The complete application-owned schema-15 core object inventory matches the frozen schema-15 structural fingerprint.
2. The ONNX archive extension matches the exact historical schema-16 DDL contract, including all expected indexes, triggers, columns, constraints and foreign keys.
3. SQLite integrity and foreign-key checks pass.
4. No unresolved SQLite WAL, shared-memory, or rollback-journal sidecar makes the snapshot ambiguous.
5. Any retained legacy Research objects are treated as opaque historical evidence, are preserved, and are excluded from active runtime authority; unknown non-Research/non-archive objects fail closed.

For this exact profile, initialization and all migration entrypoints are no-ops. They preserve `schema_version=16`. Existing schema-15 migration behavior remains unchanged. Versions above 16 and structurally incompatible schema 16 remain blocked before a read-write connection is opened.

Schema compatibility does not itself prove that persisted operational transactions are safe to recover. Schema-16 startup must not invoke automatic Champion/promotion or retired-file recovery that can rewrite files or authority. If a pending/uncertain Optimizer, promotion, backtest, Challenger batch/retirement, or Scientist request is detected, the application fails closed into a diagnostic reconciliation-required state without mutating those records. Clean, integrity-valid state may proceed only after the application invariants are checked.

## Cutover boundary

The implementation may stage only explicitly supplied synthetic test databases in CI. A future Owner-authorized cutover must:

- preserve an independently verified source image and its hash;
- create a candidate in a separate location and never modify the source while staging;
- account for SQLite WAL/journal state using a consistent SQLite snapshot or reject the source without deleting sidecars;
- validate schema profile, integrity, foreign keys, protected row fingerprints, artifact references, Champion identity and Optimizer state before candidate publication;
- publish an immutable machine-readable staging report and retain failed candidates for explicit disposition;
- never auto-activate a candidate; activation is a separate explicit Owner action, followed by post-activation readback and rollback/recovery evidence.

This P0B source phase does not access or copy the actual Owner database and does not activate any candidate. Synthetic acceptance cannot establish compatibility with the actual Owner database.

## Compatibility matrix

| Input | Expected P0B behavior |
|---|---|
| Missing database | Existing fresh-install initialization path. |
| Supported schema ≤15 | Existing guarded migration path, unchanged. |
| Exact schema-16 core + historical archive extension | Preserve version 16; no-op migration/startup checks; serve only when operational invariants are clean. |
| Schema 16 with altered/missing core or archive DDL | Block before writable open; no database, sidecar, file, or evidence mutation. |
| Schema 17+ | Block before writable open; no downgrade or automatic recovery/reset. |
| Missing/corrupt version metadata or ambiguous sidecars | Block without mutation. |
| Schema 16 with pending operational transaction | Explicit reconciliation-required block; preserve transaction and evidence. |

P0B implementation acceptance is synthetic/source-level only. `OWNER_DATABASE_COMPATIBILITY=NOT_PROVEN`, `OWNER_RUNTIME=NOT_PROVEN`, `MT5=NOT_RUN`, and no scientific execution is authorized.
