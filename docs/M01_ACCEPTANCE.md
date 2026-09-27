# M01 Acceptance Record

Status: BUILDER PASS — CONTROL ROOM ACCEPTANCE PENDING

Base authority:
e5b046c3dd55a44d3d7cb655ad8a484658734084

Real acceptance job:
20260922_081129_e3902b3d

Real market request:
- Main symbol: XAUUSD.m
- Relative reference symbol: EURUSD.m
- Timeframe: H1
- From: 2026.08.01
- To: 2026.09.15
- Tick model: 1 minute OHLC
- Native optimizer: Slow Complete
- Selected optimizer parameter: InpEntryThreshold
- Selected range: 0.18 / 0.02 / 0.20
- Raw complete grid combinations: 2
- Max rounds: 1
- KPI gates: PF >= 1.00, RF >= 0.00, Mean R >= 0.00, Weighted R >= 0.00, AUTO trades
- Required minimum closed trades: 30
- Scientist assist: false

Observed real runtime:
- MetaEditor compile summary: Result: 0 errors, 0 warnings.
- Process return code was diagnostic only.
- Fresh expected EX5 existed.
- EA source/deployed SHA-256 remained 9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345.
- MT5 native optimization produced 2 real passes.
- XML dedicated Recovery Factor was parsed.
- Weighted-R sidecar was joined by canonical 16-parameter vector, not pass number.
- Both passes had complete Weighted-R evidence.
- Both passes passed PF/RF/Mean-R/Weighted-R gates but had 20 trades versus required 30.
- Terminal result: NO_ELIGIBLE_WINNER_MAX_ROUNDS.
- Scientist calls: 0.
- Challenger created: 0.
- Strategy Champion mutation: NONE.

Scientific note:
MT5 subset optimization exports only actively optimized parameter columns in SpreadsheetML. The rebuild reconstructs the canonical 16-parameter identity from selected XML values plus the frozen non-selected EA defaults, then verifies that exact vector against the frame sidecar.

Owner UI:
- Overview preserved.
- Optimizer page added.
- START / STOP / RESUME are exposed.
- Round history and parsed result evidence are displayed.
- No Scientist, Challenger, Promote, or Champion mutation action is exposed.
- Milestone/build identifiers are not rendered in the Owner UI.

The complete M01 final verification order succeeded for the retained real-MT5 acceptance job above.


## Evidence Retention Repair

Repair base candidate:

```text
41ea9af72598b9016f1a98c9c495a24f22a438a6
```

Repair scope:

- Scientific optimizer code unchanged.
- Real MT5 was not rerun.
- Ignored `.log` acceptance outputs were copied byte-identically to retained `.txt` files.
- `acceptance.json` and retained manifests now reference only tracked evidence files.
- The real MetaEditor compile output is retained as `metaeditor_compile.txt` and contains `Result: 0 errors, 0 warnings`.
- Evidence integrity guard: `python scripts/verify_m01_evidence.py`.
- Final candidate-object verification: `python scripts/verify_m01_evidence.py --git-ref HEAD`.

Scientific authority remains unchanged:

```text
EA SHA      9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345
XML SHA     77a2dc21a2dd871c2ac50a65b2681b6cb95a0bdaa483fec8669c6edd4ab2ef1f
Sidecar SHA 5bc79b33a9f4172f307f7a9761e3c23fa0ced00c1b970255699339016146700b
```

Retained replay remains:

```text
parsed = 2
eligible = 0
winner = NONE
terminal = NO_ELIGIBLE_WINNER_MAX_ROUNDS
```
