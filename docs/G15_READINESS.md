# Trading Room release-readiness panel (G15)

The GET /api/readiness endpoint is a strictly read-only local observation
summary. It never obtains a release permit, authenticates broker ownership,
or executes a trading request. It only reports the number of local journal
runs that were recorded under known read-only, SIGNAL, SHADOW and PAPER modes.

Every canonical milestone M01 through M07 is independently blocked for
acceptance. M01 terminal owner smoke is still pending; M03 true DEMO order
checking/reconciliation is not implemented; M07 AUTO_LIVE is not implemented.

GitHub Actions success, synthetic paper fills, LLM hypotheses and locally
consistent MT5 ticks cannot change those statuses. Real terminal testing
and independently authorized broker execution require separate owner action.

Missing journal: NOT_OBSERVED, no database is created.
Malformed or corrupted journal: 409 INTEGRITY_BLOCKED, never zero-success.
UI renders from textContent only and intentionally shows INTEGRITY BLOCKED
on untrusted READY/live-authorized API responses.

Security: endpoint is local loopback-only with no-store/CSP/nosniff headers.
No raw account IDs, broker servers, local paths, passwords, HMAC keys or
symbol P/L are returned. Local run counts are unverified observations.

Issue #41: https://github.com/maxqstudio/max-trading-agent/issues/41
