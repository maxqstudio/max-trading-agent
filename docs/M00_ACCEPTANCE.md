# M00 Repair Acceptance Record

Status: BUILDER PASS — CONTROL ROOM ACCEPTANCE PENDING
Evidence: evidence/m00/acceptance.json

## Final verification

1. Git diff review: PASS; no unrelated domain implementation.
2. EA source/snapshot SHA-256 parity: PASS.
3. Backend pytest: 11 passed.
4. pip check: PASS.
5. Frontend test: 1 passed.
6. Frontend lint: 0 warnings, 0 errors.
7. Frontend production build: PASS.
8. npm audit --omit=dev --audit-level=high: 0 vulnerabilities.
9. Real installed MT5 preflight: READY_EXECUTABLE_AND_DATA_ROOT.
10. Explicit invalid MAX_MT5_TERMINAL: UNAVAILABLE / EXPLICIT_TERMINAL_NOT_FOUND; no fallback.
11. Fresh SQLite initialization: READY, schema 1, BASELINE_NOT_CHAMPION.
12. Separate-process SQLite restart/readback: PASS.
13. Owner runtime ports confirmed clean before launch.
14. RUN_MAX.cmd --no-pause: exit 0 from clean runtime.
15. Backend health: READY.
16. SQLite health: READY, schema 1.
17. Vite /api/overview proxy: PASS.
18. Frontend root: HTTP 200.
19. Overview authority: Backend READY; SQLite READY; EA v2.00 BASELINE_NOT_CHAMPION; Strategy Champion NONE; exact EA SHA-256; MT5 READY.
20. Acceptance-created MAX backend/frontend process trees removed after verification.

## Runner behavior

RUN_MAX.cmd is the Owner entrypoint.
It reuses an already-running service only when the expected MAX overview is valid.
An unrelated/broken listener on 8000 or 5173 fails clearly and is not killed.
The browser open action occurs only after direct backend state, Vite proxy state, and frontend HTTP readiness succeed.

Failure-path proof:
- MAX_MT5_TERMINAL=C:\THIS_PATH_MUST_NOT_EXIST\terminal64.exe
- RUN_MAX.cmd returned 26.
- Reason: EXPLICIT_TERMINAL_NOT_FOUND.
- Runtime ports were clean after runner cleanup.
- Browser readiness/open success was not printed.

## Browser evidence boundary

No browser DOM PASS is claimed.
The M00 Owner UI evidence is:
- real one-click runner browser open action after readiness,
- real frontend HTTP 200,
- real Vite-proxied overview,
- React rendering test for the required foundation values.

## M00 boundary

MT5 Strategy Tester optimization: NOT USED.
LLM Scientist: NOT USED.
Strategy Optimizer: NOT IMPLEMENTED.
Strategy Challenger actions: NOT IMPLEMENTED.
Strategy Champion promotion: NOT IMPLEMENTED.
Scientist Chat: NOT IMPLEMENTED.

M01 remains blocked until Control Room accepts M00.
