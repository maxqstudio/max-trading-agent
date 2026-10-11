# ONNX-02 Data Intake, Data Quality, and Immutable Snapshots

## Scope and authority

ONNX-02 is limited to source discovery, read-only preflight, exact-byte snapshotting,
data-quality and duplicate evidence, and configuration of the three research windows.
It does not execute Discovery, WFA, CPCV, Tournament, Monte Carlo, or Forward; it
does not instantiate or train models, export ONNX, invoke MT5, repair broker data, or
mutate either Champion.

The MAX source authority is `ea/baseline/Max_MTF.mq5` and its adjacent manifest.
The verified contracts are `MAX_TRUE_MTF_DYNAMIC_V1` and `CP32_TRUE_MTF_V1`, with
the exact 49-column semicolon-delimited `Max_MTF_Training.csv` and 32 CP32 feature
columns. The CSV's `signal_time` is a naive broker/source wall-clock timestamp. No
UTC conversion or timezone offset is inferred; the source timezone provenance is
required and carried unchanged into window validation.

The source writer uses the MetaTrader Common Files area, a training lock, shared-read
CSV access, flush, and close before lock release. ONNX discovery checks only
`%APPDATA%/MetaQuotes/Terminal/Common/Files/Max_MTF_Training.csv`, or an explicit
absolute override contained under that same approved root. It does not recurse or
scan other private files. Reads acquire the existing lock without creating or
modifying it, verify a regular non-reparse path and stable file fingerprint, bound
the copy, and reject a partial final row or changing source.

## Snapshot and evidence lifecycle

The backend captures exact source bytes, audits that capture, computes SHA-256, and
publishes a content-addressed, read-only artifact through same-volume staging and
atomic publication. SQLite schema 16 adds snapshot, window, and readiness evidence
tables; it does not create research execution state. Snapshot identity binds dataset
and snapshot IDs, content hash, source fingerprint, source/EA contract hashes, byte
size, row count, timezone provenance, DQ evidence, creation time, and parent/correction
lineage. Workspace reads re-hash and re-audit persisted artifacts and reconstruct
the saved window evidence; tampering or contradictory persistence fails closed.

An interrupted publication can be retried. Identical content and identity are
idempotent; conflicting identities and hash mismatches fail closed. Concurrent
attempts cannot replace a different snapshot under the same identity. The live CSV
is never edited. Explicit correction of byte-value-identical duplicate rows uses the
parent snapshot as a verified backup and creates a new derived immutable snapshot;
the parent remains retained. Conflicting rows sharing the verified identity
`(contract, symbol, period, signal_time)` block correction. The identity comes from
the MAX writer's actual first-write check, not an assumed generic key.

## Data-quality and broker-gap semantics

DQ checks exact headers, count and order; contract and timeframe; row completeness;
finite numeric values; OHLC, ATR, quote and spread sanity; one symbol/timeframe;
timestamp format and chronological order; timezone provenance; and repeated identity
rows. Unsupported encoding, malformed CSV, invalid features or source instability
fail closed.

An observed timestamp discontinuity is only an observation. The API reports it
separately from broker reconciliation using distinct statuses such as
`OBSERVED_TIMESTAMP_DISCONTINUITY`, `BROKER_RECONCILIATION_PENDING`,
`BROKER_CONFIRMED_MISSING`, `BROKER_CONFIRMED_NOT_MISSING`, `REPAIR_REQUIRED`, and
`REPAIR_VERIFIED`. This phase has no broker-history evidence source or authorized
repair process. A continuous timestamp series therefore does not prove broker
completeness, and a discontinuity does not prove a missing bar. No fill,
interpolation, synthetic OHLC, or synthetic CP32 values are created.

When broker evidence or an explicitly Owner-authorized disposition is absent,
`BROKER_RECONCILIATION_PENDING` remains the first readiness blocker. Window
configuration may be validated against an otherwise DQ-valid snapshot, but real
`DATA_READY` is withheld. CI uses only synthetic fixtures labeled
`SYNTHETIC_TEST_EVIDENCE`; passing those fixtures is not real-data evidence.

## Three-window contract

Exactly three windows are accepted, in order: `DISCOVERY`, `TOURNAMENT`, `FORWARD`.
For source-naive timestamps, their ranges must satisfy:

```text
DiscoveryFrom <= DiscoveryTo
DiscoveryTo < TournamentFrom
TournamentFrom <= TournamentTo
TournamentTo < ForwardFrom
ForwardFrom <= ForwardTo
```

Each range must lie within the same snapshot's timestamp coverage and contain source
rows. Persisted windows bind the snapshot ID and SHA-256, timezone provenance, exact
boundaries, validation evidence, and immutable revision identity. Revalidation does
not mutate prior evidence. Forward does not grow during a later frozen cycle. This
phase does not evaluate any window.

## Versioned API and UI

The ONNX-01 `/api/v1/onnx/workspace` snapshot remains frozen and null-only. Data
intake uses the separate `/api/v2/onnx/data` contract:

| Operation | Purpose |
| --- | --- |
| `GET /workspace` | Reconstruct persisted snapshot/window state and readiness blocker. |
| `POST /preflight` | Read-only source identity, lock, schema and DQ audit. |
| `POST /snapshots` | Explicitly confirmed publication of the exact preflighted source bytes. |
| `GET /snapshots/{snapshot_id}` | Verify and read one immutable snapshot. |
| `POST /snapshots/{snapshot_id}/resolve-identical-duplicates` | Explicitly create a corrected derived snapshot for identical rows only. |
| `POST /windows/validate` | Validate and persist the exact three-window configuration. |

All request models reject unknown fields. The existing application-wide
`RECOVERY_REQUIRED` middleware remains in force. Backend evidence is authoritative;
the frontend parser rejects contradictory response contracts and never creates
readiness. No data scanning or snapshot identity is placed into the frozen v1 API.

## Acceptance boundary

Source schema and EA authority are source-verified; API, UI, persistence and crash/
retry behavior are validated with synthetic temporary files and databases. No Owner
PC, real data, broker history, MT5, real scientific run, training, model output, ONNX
export/runtime, or Champion mutation was accessed or performed. `REAL_DATA_READY` and
Owner runtime remain `NOT_PROVEN`.

## Claim traceability

- `TRUTH-ONNX-PLANNING-001`
- `TRUTH-ONNX-WORKSPACE-001`
- `TRUTH-ONNX-DATA-INTAKE-001`
