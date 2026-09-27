# M04 Acceptance — Strategy Champion / Explicit Owner Promotion

Status: ACCEPTED

## Authority

Original M04 base commit: `0b91cc3270cadaff9dbf085cf3b580dd07b95e1e`

Control Room repair base: `b75ceb9eb58bf71799887da1d9dfac0871d7768f`

Source Challenger:
- `STRAT-20260922-120735-R01-P11`
- optimizer job `20260922_120735_07989e09`
- round 1 / MT5 pass 11
- M03 manifest SHA-256 `12d1872641754376512b5e2ca014426423801f2dafc1fb6338fed78c28a3c996`

Frozen seed EA remains `ea/baseline/Max_MTF.mq5` with SHA-256
`9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345`.

## Real Owner promotion

The real Owner-visible web flow was exercised through the running local application:

`Challengers -> Promote -> confirmation -> CONFIRM PROMOTION`.

The first explicit confirmation attempt entered the durable promotion transaction and failed before authority commit because the compile freshness clock used an unavailable datetime API. The journal rolled back to prior authority, leaving Champion NONE, Challenger active, baseline unchanged, and deployment restored. The defect was repaired to use `time.time_ns()`.

The second explicit confirmation committed:

- promotion: `PROMOTE-20260922-141239-328869ab`
- previous Champion: `NONE`
- new/current Champion: `STRAT-20260922-120735-R01-P11`
- baseline archive: `BASELINE-MTF-V2_20260922_141239_UTC`
- selected Challenger state after commit: `PROMOTED`
- current Champion count: 1
- Scientist calls during promotion: 0
- Live authority: NONE

An immediate frontend refresh defect left the already-promoted Challenger visible until page refresh. That presentation defect was repaired; final browser checks show zero active Challengers and the current Champion correctly.

## Real compile and deployment

Promotion compile authority:
- MetaEditor summary: `Result: 0 errors, 0 warnings`
- fresh EX5: PASS
- Champion source SHA-256:
  `a1e7e35c370ed889221e4da2fde3ed1801106234a2ab455e5da418bb58ae6aaf`
- promotion EX5 SHA-256:
  `308e1e4b313b151ad2b42f2d87bf4d34e37e8efe65e850a4044c627e60412fb0`
- Champion / Tester SET SHA-256:
  `dd273cb4b82c1164dc1a1e166c6447bced8b49db7b635805731280ca575b02a6`

The final code was independently recompiled after the freshness-clock repair. It again produced `Result: 0 errors, 0 warnings` and a fresh EX5. That recompile is verification only; it does not change Champion authority or deployment evidence.

## Parity

PASS:
- Challenger params == Champion params
- Champion params == Champion EA defaults
- Champion params == project Champion SET
- Champion params == deployed Tester SET
- project Champion EA == deployed MT5 source
- all 16 optimizer flags in Champion Tester SET == N
- strategy logic has zero non-whitelisted changes
- only `InpMinConsensus` default differs from the frozen baseline

## Baseline/history

The first-promotion history archive status is
`ARCHIVED_PRE_FIRST_STRATEGY_CHAMPION`.

The archived seed EA and the current frozen seed EA both retain SHA-256
`9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345`.

The seed file remains `BASELINE_NOT_CHAMPION`; it was not overwritten.

## Recovery and later-promotion coverage

Controlled backend tests cover:
- explicit confirmation and stale confirmation rejection
- active optimizer promotion block
- compile/parity/SET/filesystem/DB commit rollback
- pre-commit and post-commit crash recovery
- restart persistence
- later promotion closes A Champion tenure as FORMER history while A operational Strategy returns PROMOTED -> CHALLENGER
- B operational Strategy changes CHALLENGER -> PROMOTED while B Champion tenure becomes CURRENT
- A -> B -> A re-promotion using the same Strategy IDs and original immutable Challenger bundles
- later-promotion and re-promotion rollback
- exactly one CURRENT Champion database invariant
- append-only promotion / Champion tenure history
- EA version remains 2.00 through Challenger -> Champion -> Challenger -> Champion role changes

## UI/runtime

Final `RUN_MAX.cmd` clean restart reports:
- Backend READY
- SQLite READY, schema 5
- seed EA `BASELINE_NOT_CHAMPION`
- current Strategy Champion `STRAT-20260922-120735-R01-P11`
- MT5 preflight READY
- browser opens only after readiness

Final browser check proves:
- Overview current Champion is correct
- active Challenger list is empty
- Champion page reports CURRENT STRATEGY CHAMPION
- promotion history and retained KPI are visible
- no Scientist page exists
- no Live controls exist

## Control Room lifecycle repair

The rejected schema-4 model keyed Champion history directly by strategy_id and left a replaced Champion's Challenger row at PROMOTED. The repair migrates Champion history to schema 5 tenure rows keyed by champion_tenure_id while preserving stable strategy_id.

Committed replacement semantics are now atomic:

`A CURRENT Champion + B CHALLENGER -> A tenure FORMER + A CHALLENGER + B PROMOTED + B tenure CURRENT`.

A can then be promoted again by the normal Challenger promotion flow:

`B CURRENT Champion + A CHALLENGER -> B tenure FORMER + B CHALLENGER + A PROMOTED + new A tenure CURRENT`.

The original Challenger bundle, manifest, optimizer lineage, Strategy ID and EA version remain unchanged across all role changes. Failed replacement or failed re-promotion leaves the pre-existing Champion and Challenger statuses intact because reactivation occurs only in the final SQLite authority transaction.

The existing real first promotion `PROMOTE-20260922-141239-328869ab` remains valid and was not re-run. Schema-4 -> schema-5 migration was replayed on a copy of the real database before migrating the actual database; the real CURRENT Champion, PROMOTED Challenger row, committed and rolled-back promotion journals, manifest, bundle path and EA version were preserved.

## Regression/evidence

Final backend suite, frontend tests/lint/build/audit, pip check, and M01/M02/M03 evidence verifiers pass. Historical real M04 evidence remains under `evidence/m04/`; Control Room repair evidence is retained separately under `evidence/m04/repair/`. Both are verified by `scripts/verify_m04_evidence.py`.

M04 was accepted after the final schema-compatibility repair. M05 is now the active authorized milestone; M06 remains blocked pending independent Control Room M05 acceptance.
