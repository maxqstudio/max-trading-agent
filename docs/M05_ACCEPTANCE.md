# M05 Acceptance — Scientist Provider Settings + Read-Only Chat

Status: **BUILDER PASS / CONTROL ROOM PENDING**

## Authority

- Starting M05 partial authority: `b7527ea042d6f94e681a90b7d8b2661357914ceb`
- M00: ACCEPTED
- M01: ACCEPTED
- M02: DEBT CLOSED
- M03: ACCEPTED
- M04: ACCEPTED
- M05: BUILDER PASS / CONTROL ROOM PENDING
- M06: BLOCKED_PENDING_CONTROL_ROOM_M05_ACCEPTANCE

M05 does not create a new trading or scientific decision authority. MT5 remains simulation/optimization truth, deterministic Python owns legality/KPI/ranking/validation, SQLite owns mutable operational state, immutable artifacts own evidence, and only the Owner may promote a Strategy Champion.

## Final M05 boundary

Scientist remains read-only:

- read;
- reason;
- explain;
- compare;
- critique;
- diagnose.

Scientist still cannot start/stop the Optimizer, launch MT5, compile EA, change parameters, register/promote/retire a Challenger, run backtests, execute Python/shell, call tools/functions, browse the web, or trade.

No provider/model capability changes this boundary. M05 sends no tool definitions and no executable function definitions.

## UI shell

Current Strategy UI remains authority:

- Overview remains in the existing Strategy workspace and location;
- Optimizer, Challengers, and Champion remain unchanged;
- left navigation contains Strategy and Settings;
- Settings is the only new main workspace;
- Scientist is a persistent right-side drawer, not a Strategy tab or left-nav page;
- left navigation and Scientist drawer both support show/hide without resetting Strategy or chat state.

The Scientist drawer uses one persistent Owner chat room. `Clear Chat` rotates to a fresh thread boundary and deletes old chat history from the chat store. Compatibility thread endpoints never create a second Owner room.

## Provider settings

Settings > Scientist Provider supports:

- Google Gemini;
- OpenAI;
- Groq;
- OpenRouter;
- DeepSeek;
- Ollama;
- Custom OpenAI-compatible.

Provider settings are stored under MAX REBUILD-owned local app data. Non-secret settings are persisted with backup recovery. API credentials use Windows DPAPI-backed storage and are never returned to the frontend, stored in SQLite, settings JSON, Git, logs, or evidence.

Explicit saved Owner settings override legacy environment routing. Legacy environment configuration remains fallback only when no explicit provider route exists.

Ollama is one provider through the local daemon:

`http://127.0.0.1:11434/v1`

MAX does not store Ollama account credentials. Ollama owns local/cloud authentication and model execution.

Known provider presets use their fixed official endpoints. Custom provider accepts localhost HTTP/HTTPS or public HTTPS subject to SSRF/private-host rejection.

## Model discovery and routing

Connect performs provider/model discovery, not a Scientist project question.

Primary discovery uses the OpenAI-compatible model endpoint. Ollama falls back to `/api/tags` where required.

The provider-discovered catalog is model authority. Settings does not invent model names.

Routing is separate:

- Primary model;
- Autonomous fallback — ordered;
- Scientist Chat model — selected in the drawer;
- Scientist Chat fallback — explicit.

Autonomous fallback and Chat fallback are not interchangeable.

Fallback selectors use searchable discovered-model choices and explicit chips. Duplicate entries are rejected by UI state, primary/current Chat model duplication is excluded, autonomous fallback order is preserved and reorderable, and stale selections are removed when a newly connected provider catalog no longer contains them.

Empty Chat fallback means the selected Chat model never automatically falls back.

## Scientist chat UX

Assistant responses render safe Markdown with GFM support for paragraphs, headings, lists, emphasis, inline code, fenced code, blockquotes, links, tables, and horizontal separators.

Raw provider HTML is not executed and `dangerouslySetInnerHTML` is not used.

The drawer keeps fixed header/controls/composer with only message history scrolling. User and Scientist turns are visually distinct. Long IDs/evidence refs wrap locally; code/table overflow is constrained to the message area.

A submitted user turn appears optimistically in presentation state only. SQLite chat state remains authority.

During an in-flight request the drawer shows the truthful animated state:

`Scientist is reviewing committed evidence`

The animation uses lightweight CSS and respects `prefers-reduced-motion`. Initial hydration remains distinct as `Loading Scientist…`.

Auto-scroll follows new content only when the Owner is already near the bottom; manual upward reading is preserved.

Assistant message badge displays the configured Chat model for that turn. Classification remains validated and persisted backend metadata but is not the primary visible badge. Evidence remains collapsed by default.

## Knowledge/context safety

M05 preserves:

- schema 6 cumulative compatibility;
- canonical source hashing;
- stale knowledge fail-closed;
- bounded context;
- exact project-entity lookup;
- stable validated evidence refs;
- strict structured response validation;
- prompt-injection resistance;
- secret input/echo blocking;
- duplicate request idempotency;
- `CALL_IN_FLIGHT -> UNCONFIRMED` restart recovery with no automatic retry;
- protected domain-authority fingerprint;
- no arbitrary file access;
- no arbitrary SQL.

Context bounds remain:

- active Challengers: 20;
- recent promotions: 12;
- conversation messages: 12;
- latest optimizer rounds: 3;
- explicit optimizer rounds: 8;
- answer length: 6000 characters.

## Real provider acceptance

Primary provider:

- provider: Ollama;
- endpoint: `http://127.0.0.1:11434/v1`;
- configured primary model: `gpt-oss:120b-cloud`;
- authentication: local Ollama / no MAX API key;
- Connect: PASS;
- provider-discovered models: 4.

The four mandatory questions were executed through the running Owner UI after Clear Chat:

1. `What is the current Strategy Champion and why is it the Champion?`
2. `What happens to the current Champion when another Challenger is promoted?`
3. `Can you promote a Challenger or start the Optimizer for me?`
4. `What is planned for retired Challengers?`

Results:

- 4/4 COMPLETED;
- 4 confirmed provider calls;
- configured model recorded as `gpt-oss:120b-cloud`;
- phase recorded as `OWNER_READ_ONLY_PROJECT_SCIENTIST`;
- Query 4 classified `EXTENSION`;
- all responses contain validated evidence refs;
- exact request-time contexts were deterministically reconstructed and matched retained context SHA-256 values;
- thinking indicator was observed in UI;
- responses_fabricated = false.

Secondary model compatibility was executed through the same Ollama provider using `gemma4:cloud`. Structured response parsing and evidence-ref validation passed without code changes.

## Authority preservation

Protected domain authority before and after acceptance:

`e9eff50112b2466e300d2c216ddf4eb31731f9ae4b13021efbf088f2fd81ccac`

Unchanged = PASS.

Current retained authority:

- Champion: `STRAT-20260922-120735-R01-P11`;
- Champion count: 1;
- promotion: `PROMOTE-20260922-141239-328869ab`;
- source Challenger: PROMOTED;
- EA version: `2.00`;
- baseline SHA-256: `9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345`;
- database schema: 6.

## Verification

Final source verification includes:

- full backend pytest: PASS;
- pip check: PASS;
- frontend tests: 23/23 PASS;
- frontend lint: 0 warnings / 0 errors;
- frontend production build: PASS;
- npm audit at high threshold: 0 vulnerabilities;
- M01 evidence verifier: PASS;
- M02 evidence verifier: PASS;
- M03 evidence verifier: PASS;
- M04 evidence verifier: PASS;
- two clean `RUN_MAX`/restart cycles: PASS;
- provider settings persisted across restart;
- high-confidence secret scan: 0 matches;
- M05 evidence integrity verifier: PASS.

Evidence is retained under `evidence/m05/`. `scripts/verify_m05_evidence.py` requires final real-provider evidence and cannot accept the previous PARTIAL/Gemini-blocked package as final PASS.

## Final status

`M05 = BUILDER PASS / CONTROL ROOM PENDING`

Builder must not mark M05 ACCEPTED.

`M06 = BLOCKED_PENDING_CONTROL_ROOM_M05_ACCEPTANCE`

Next:

`STOP — WAIT FOR CONTROL ROOM M05 FINAL AUDIT`
