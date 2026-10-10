# ONNX-01 Workspace and Read API

## Purpose and boundary

ONNX-01 adds an Owner-facing workspace shell and a versioned, read-only backend
snapshot. It does not create an operational research cycle or store, inspect or
snapshot datasets, instantiate models, train or score candidates, export or run
ONNX, or mutate a Strategy/ONNX Champion. The frozen 23-family ONNX-00
scientific authority is displayed as planning authority only; no scientific
result is implied.

## Navigation contract

The application workspaces are exactly **Strategy**, **ONNX**, **Artifacts**,
and **Settings**. ONNX contains exactly **Overview**, **Data Intake**,
**Discovery**, **CPCV**, **Tournament**, **Monte Carlo**, **Challenger**, and
**Champion**. Qualified Pool is presented within Discovery. Forward is
presented within Challenger. Neither has a separate navigation page. Strategy
Challenger/Champion remain separate from their future ONNX counterparts.

## Endpoint and ownership

```text
GET /api/v1/onnx/workspace
```

The backend owns the response (`backend/max_backend/onnx_api.py`). The response
is a static capability snapshot labeled `BACKEND_ONNX_01_SKELETON`, not a
database read or persisted state-machine instance. The frontend validates the
version and status vocabulary before rendering and has no fallback that
manufactures cycle, dataset, candidate, progress, checkpoint, or Champion data.

Contract version `1.0` uses these status values:

| Status | Meaning in this contract |
|---|---|
| `NOT_IMPLEMENTED` | The capability or operation is outside ONNX-01. |
| `NOT_STARTED` | No operational execution has started or been recorded. |
| `NOT_PROVEN` | Scientific/hardware/runtime truth has no evidence in this phase. |
| `UNAVAILABLE` | No authoritative value can be read from an ONNX operational store. |
| `RECOVERY_REQUIRED` | The existing application recovery gate blocks ordinary reads. |

The response groups include:

- `operational_state`: `persisted=false`, `cycle_id=null`, and a reason that
  the planning state machine is not operational persisted state.
- `dataset`, `research_windows`, and `hardware_capacity`: IDs, values, and
  snapshots are null; no source scanning or hardware probing is performed.
- `scientific_authority`, `discovery`, Qualified Pool, and seven `stage_pages`:
  backend-owned status/reason/prerequisite fields. Stage page IDs are
  `data_intake`, `discovery`, `cpcv`, `tournament`, `monte_carlo`, `challenger`,
  and `champion` in that order.
- `challenger.forward`: a nested Forward status under Challenger, not a
  separately addressable stage page. Challenger items and Champion identity
  are null.
- `current_stage`, `checkpoint`, `first_blocker`, and `recovery`: explicit
  unavailable/not-implemented capability states; no checkpoint or recovery
  cursor is fabricated.

## Recovery and mutation semantics

The existing application-wide Recovery Required middleware remains in front
of this endpoint. In recovery mode the endpoint returns the established HTTP
503 body:

```json
{"detail":"RECOVERY_REQUIRED: application operations are disabled"}
```

The ONNX router has only the GET endpoint. There are no start, train, resume,
export, register, promote, transition, dataset-intake, or other scientific
mutation routes. The page shell omits those controls rather than presenting
unimplemented actions as usable.

## Verification boundary

Automated tests verify the response shape, empty-state truthfulness, absence of
mutation routes, frontend rejection of unsupported status values, and the
application recovery gate. These are source/synthetic checks only. They do not
prove data readiness, hardware capacity, scientific execution, ONNX
compatibility, GPU behavior, MT5 behavior, or Owner-PC runtime acceptance.

Project Truth linkage: `TRUTH-ONNX-PLANNING-001` and
`TRUTH-ONNX-WORKSPACE-001` in `.workflow/claims.json` describe the planning
boundary and backend-owned read-state contract.

The one-click Windows source acceptance runner is:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\accept_onnx01.ps1
```

It requires a clean candidate checkout and writes machine-readable gate results
and `first_failed_gate` under ignored `evidence/onnx01/acceptance.json`. It does
not modify operational state or perform runtime/scientific execution.

ONNX-02 adds data-intake state through the separately versioned
`/api/v2/onnx/data` API. It does not change this frozen v1 response or its
null-only capability snapshot. Snapshot, DQ, and window identities are
backend-owned in the v2 contract; real `DATA_READY` remains `NOT_PROVEN` without
broker reconciliation evidence. See [ONNX-02 Data Intake](ONNX_02_DATA_INTAKE.md).
