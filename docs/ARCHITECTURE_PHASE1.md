# Phase-1 Architecture Contract

## Isolation

Rebuild root: D:\MAX_REBUILD
Reference source: D:\MAX_MTF\MAX_MTF_v2_0_1 (read-only)

The imported EA snapshot is byte-identical to the reference baseline and verified by SHA-256.
Runtime rebuild code uses the rebuild snapshot, not the old project path.

## Layers

Frontend:
- React/TypeScript/Vite.
- Displays state/evidence and sends explicit Owner actions.
- No domain authority.
- Milestone/build identifiers (M00, M01, M02, ...) are internal authority metadata and are not rendered in the Owner UI.

Backend:
- FastAPI endpoints and application orchestration.
- Domain contracts remain independent from frontend state.

State:
- SQLite is the only mutable operational authority.
- The accepted foundation stores schema metadata and baseline registration.
- Strategy Optimizer owns optimizer_jobs and optimizer_rounds; frozen request JSON is stored inside the job row and round checkpoints are restart-safe.
- M02 stores Scientist transition identity, confirmed/unconfirmed provider-call accounting, provenance, and effective next search space inside optimizer_rounds.state_json; no separate Scientist conversation/runtime table is introduced.
- M03 raises the schema to version 3 and adds strategy_challengers as the mutable Challenger registry. The unique source identity is (source_job_id, source_round, source_pass).
- M04 repair raises the schema to version 5. strategy_promotions remains the durable promotion journal. strategy_champions stores one row per Champion tenure using champion_tenure_id while strategy_id remains stable across repeated tenures.
- M05 raises the cumulative schema to version 6 and adds only scientist_threads, scientist_messages and scientist_chat_requests. These tables own chat persistence and request accounting; they are not scientific, optimizer, Challenger, Champion, KPI or promotion authority.
- M06 raises the cumulative schema to version 7, extends strategy_challengers with RETIRED plus retired_utc, adds strategy_challenger_backtests including UNCONFIRMED, and adds strategy_challenger_retirements as the durable retirement journal. Retirement changes registry status only; retained Strategy bundles and histories remain immutable/preserved.
- All lower milestone status helpers accept later cumulative schema versions while enforcing their own minimum schema and invariants.
- Champion history and operational Challenger eligibility are separate domains. A replacement closes the old Champion tenure as FORMER, preserves its original strategy_challengers row as historical PROMOTED authority, and changes the newly selected active Strategy from CHALLENGER -> PROMOTED. Former Champion sources do not re-enter the active Challenger pool automatically.
- A partial unique index enforces at most one CURRENT Strategy Champion. Tenure rows preserve historical Champion authority without making a consumed Strategy source promotion-eligible again under the current lifecycle contract.

Evidence:
- Immutable files hold MT5 reports, Weighted-R sidecars, bounded Scientist request/proposal/provenance evidence, and Strategy Challenger bundles.
- A committed Challenger bundle contains the Challenger EA copy, fixed .set, challenger.json, manifest.json, and cryptographically retained optimizer source evidence.
- Evidence does not independently own mutable Champion state.

External execution:
- MT5 Strategy Tester owns simulation and optimization.
- MetaEditor compile evidence is required where the existing contract requires compilation.

## Current API

Foundation:
- GET /api/health
- GET /api/overview
- GET /api/ea/baseline
- GET /api/mt5/preflight

Strategy Optimizer:
- GET /api/optimizer/contract
- GET /api/optimizer/scientist/status
- POST /api/optimizer/preview
- GET /api/optimizer/current
- GET /api/optimizer/jobs/{job_id}
- POST /api/optimizer/start
- POST /api/optimizer/jobs/{job_id}/stop
- POST /api/optimizer/jobs/{job_id}/resume

Strategy Challenger:
- GET /api/challengers
- GET /api/challengers/{challenger_id}
- GET /api/challengers/registry
- GET /api/challengers/{challenger_id}/backtests
- GET /api/challengers/backtests/{backtest_id}
- POST /api/challengers/{challenger_id}/backtest
- POST /api/challengers/{challenger_id}/retire
- POST /api/challengers/{challenger_id}/promote

Strategy Champion / promotion:
- GET /api/champion
- GET /api/promotions
- GET /api/promotions/{promotion_id}

Scientist Chat:
- GET /api/scientist/status
- GET /api/scientist/knowledge
- GET /api/scientist/chat
- POST /api/scientist/chat/clear
- GET /api/scientist/threads/{thread_id}/messages
- POST /api/scientist/threads/{thread_id}/messages
- GET/POST /api/scientist/threads remain compatibility aliases for the one active Owner chat and never create a second room.

Promotion requires an explicit stale-protected confirmation payload. There is no delete, rollback-history action, Live-control endpoint, or Scientist promotion authority.
All services bind localhost only during development.

## M02 Scientist boundary

- Scientist may run only after a completed, successfully parsed no-winner round when another frozen round remains and Scientist advisory is enabled.
- Eligible winner and max-round exhaustion short-circuit before any Scientist call.
- The request payload is bounded to frozen KPI gates, selected parameter ranges/hard bounds/types, search-space context, non-selected parameter names, and at most 12 near-best completed MT5 passes.
- Provider transport is a small OpenAI-compatible HTTP boundary. Domain code consumes normalized response text/provenance only.
- Credential values are resolved at call time from the environment variable named by the frozen api_key_env identity and are never persisted or returned to the frontend.
- Response JSON may propose only start/step/stop for exactly the selected parameters. Unexpected authority fields, missing/extra parameters, illegal types, hard-bound violations, finer-than-base steps, non-finite values, and invalid integer/family-weight semantics are rejected without clipping or repair.
- Valid proposals feed the next real MT5 native round. Invalid/unavailable/failed Scientist paths use the accepted M01 deterministic_refine() fallback.
- One transition has at most one semantic proposal attempt; persisted transition identity prevents duplicate calls after restart. Unconfirmed calls recover fail-safe to deterministic fallback.
- No Scientist Python sandbox, tools, chat, knowledge, model research, Challenger, promotion, or Champion mutation exists in M02.

## Not ported

- Streamlit lifecycle/widget implementation.
- GitHub acceptance machinery.
- MTF closure/handoff scripts.
- Duplicate mutable JSON authorities.
- Future model-research modules.

## EA baseline identity contract

The rebuild stores:
- source path and source candidate identity as provenance only;
- source SHA-256;
- local snapshot path;
- local snapshot SHA-256.

Runtime startup verifies source/snapshot parity recorded in the manifest.
A different local snapshot hash is rejected; it is never silently accepted as a new baseline.


## M03 Strategy Challenger boundary

- Challenger creation occurs only after deterministic eligible_winner.json is durably committed.
- Registration independently replays the winning XML and Weighted-R sidecar through accepted M01 parsing, gates, and deterministic ranking before trusting the winner.
- The source identity (optimizer job, round, pass) is unique in SQLite. Repeated registration resolves to the same Challenger ID and bundle.
- Registration uses REGISTERING -> CHALLENGER. Only CHALLENGER is Owner-visible.
- A staging directory is built, hashed and parity-checked before atomic directory commit; SQLite is finalized only after the committed bundle verifies.
- Recovery from REGISTERING either verifies/finalizes a complete committed bundle or discards incomplete staging and rebuilds from the same immutable winner evidence.
- Historical M03 accepted evidence used the then-current exact 16D optimizer-owned universe. Current retained Challenger validation is request-epoch aware: legacy retained evidence remains exact 16D, while current V6 uses exact 17D including InpRiskPct.
- The Challenger .set contains the complete exact retained winner universe with all optimization flags N.
- Winner params, Challenger EA defaults and Challenger .set values must match for every input in that retained request epoch; mixed/missing/extra universes fail closed.
- Bundle integrity is checked at registration and detail read; tampered committed bundles fail closed and are never silently regenerated.
- The frozen baseline EA remains BASELINE_NOT_CHAMPION and byte-identical to the registered SHA.
- M03 acceptance ended with Strategy Champion NONE and no promotion implementation.

## M04 Strategy Champion boundary

- Promotion starts only from a verified active CHALLENGER and a distinct Owner CONFIRM PROMOTION action.
- The final request carries the expected Challenger manifest SHA and expected current Champion identity; backend revalidation, not frontend state, owns authority.
- The immutable seed EA remains under ea/baseline and is never overwritten. Active Champion derivatives live under ea/champion/current.
- The first successful promotion commits an immutable baseline transition archive with status ARCHIVED_PRE_FIRST_STRATEGY_CHAMPION while leaving the seed file unchanged.
- Before file mutation the service retains before-state snapshots for project Champion files, MT5 deployed source/EX5 and Tester SET. The journal records PREPARED -> ARTIFACTS_STAGED -> FILES_COMMITTED -> COMMITTED or terminal rollback/failure.
- Champion source is byte-identical to the verified Challenger EA. The Champion SET is a fixed point with every retained request-epoch optimizer flag N and preserves the accepted tester fixed-input contract; legacy 16D and current 17D are not silently reinterpreted.
- Real MetaEditor compile requires an explicit zero-error/zero-warning summary and a fresh EX5. Process return code alone is diagnostic.
- SQLite authority is committed only after deployment and parity verification. Prior Champion authority remains valid on any pre-commit failure.
- Restart rolls back incomplete pre-commit promotion states. A valid COMMITTED promotion is preserved and reverified; it is never silently repeated.
- Later promotion closes the prior Champion tenure as FORMER history, archives that tenure's immutable Champion artifacts/lineage, keeps the prior Strategy's source Challenger row PROMOTED as historical Champion lineage, marks the selected Challenger PROMOTED, creates the selected Strategy's CURRENT tenure, and keeps exactly one CURRENT row.
- The previous Strategy retains the same strategy_id, bundle path, manifest SHA, optimizer lineage, params, and EA version. No new Strategy ID or duplicate Challenger bundle is created.
- A Strategy that has entered Champion lineage is not automatically returned to active Challenger eligibility. Promotion history and retained source evidence remain append-only and auditable.
- Failed replacement leaves the prior Champion and both source Challenger authorities unchanged; successful replacement likewise does not reactivate the former Champion source as an active Challenger.
- Scientist calls during promotion are zero. Strategy Champion is governance/research authority only; Live trading authorization remains NONE.

## M05 read-only Scientist boundary

- Static knowledge is a compact deterministic projection with an explicit source-hash allowlist; stale provenance blocks chat before provider execution.
- Fresh context is built server-side from allowlisted stores only. Current Champion and latest optimizer state are always compact; exact Strategy/job/promotion identities are resolved deterministically; active Challenger, promotion and conversation lists are bounded.
- User text never becomes a path or SQL statement. The LLM receives no tools or executable function definitions and cannot query SQLite/files dynamically.
- M05 reuses only the generic OpenAI-compatible provider transport. The M02 optimizer-range prompt and propose_optimizer_ranges() path are not used.
- Provider response contract is exact JSON with answer, classification, evidence_refs and uncertainties. Classification and evidence references are validated against supplied context before persistence/display.
- Each Send owns a request_id. PREPARED -> CALL_IN_FLIGHT reserves one provider attempt; restart converts uncertain in-flight requests to UNCONFIRMED without retry. COMPLETED requires an actually returned provider response.
- A deterministic protected-domain fingerprint covers baseline, optimizer, Challenger, Challenger-backtest, Challenger-retirement, Champion, promotion rows and critical Strategy artifacts before/after chat. Chat-table mutation is permitted; domain-authority mutation is not.
- Owner chat is one persistent room. Clear Chat rotates to a fresh thread boundary, removes prior chat history from the chat store, and restart resolves only the new active thread; room accumulation is not an Owner UX.
- The assistant message badge shows the configured Chat model for that turn; classification remains validated/persisted backend metadata rather than the primary visible badge.
- Prior chat transcript is conversation context only and never outranks fresh committed MAX authority.

## M06 Challenger operations boundary

- Historical milestone status: M06 ACCEPTED. This section documents the retained Challenger-operations contract; later current parameter-universe repairs supersede any old fixed-16D assumption as stated below.
- Scope is only Challenger Backtest and non-destructive Challenger Retirement / Archive.
- Schema version 7 is cumulative; lower milestone readers accept later schema versions while still enforcing their own invariants.
- Active and Retired / Archive are status views over the same SQLite Strategy registry, with server-side search, deterministic sort, and bounded pagination.
- Retirement is stale-protected, explicitly confirmed CHALLENGER -> RETIRED. It preserves MQ5, SET, compiled EX5 evidence when available, manifests, params, KPI, optimizer/promotion/backtest evidence, hashes, lineage, created UTC, and retired UTC. No DELETE endpoint exists.
- Backtest accepts active CHALLENGER only, verifies the immutable bundle and exact manifest, requires no active optimizer job, freezes symbol/relative-symbol/timeframe/date window/tick model/deposit/leverage to the retained source request, compiles that retained MQ5 with MetaEditor, and executes Optimization=0 through the real MT5 Strategy Tester.
- The retained Challenger SET must contain exactly its request-epoch optimizer universe with all optimization flags N: historical legacy 16D remains 16D and current V6 is 17D including InpRiskPct. Runtime Tester-only inputs may be appended to a temporary SET; the immutable bundle is never rewritten.
- Backtest evidence records frozen retained request, source manifest/EA/SET identity, compile evidence, EX5 identity, MT5 report identity, terminal state, and failure diagnostics. Restart recovery is fail-closed: PREPARED becomes FAILED because MT5 launch was not confirmed; RUNNING becomes UNCONFIRMED because execution may already have occurred. Neither state is automatically relaunched.
- Only one M06 backtest may be RUNNING at a time; concurrent run races fail closed.
- Retirement and competing operations are serialized through SQLite BEGIN IMMEDIATE authority. Retirement is blocked by an active PREPARED/RUNNING backtest or active PREPARED/ARTIFACTS_STAGED/FILES_COMMITTED promotion on the same Challenger. Promotion creation revalidates active Challenger status/manifest and fails closed after retirement. Retired Strategies remain readable/auditable with retained backtest history but cannot be newly promoted or backtested.
- M06 does not add manual Champion demotion or a special prior-Champion restore flow.
