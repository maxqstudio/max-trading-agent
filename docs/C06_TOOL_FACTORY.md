# Canonical M06 — Tool Factory T0 research proposals

LLM can propose NEW, arbitrary PURE numerical research tools and examples.
The model cannot approve permissions, alter risk limits, execute orders, or register tools.
Generated Python source is never imported or executed by the main agent.

## Owner-invoked optional workflow

Run locally only with an explicitly configured OpenAI-compatible endpoint:

    python -m max_trading_agent tool-propose "Design a bounded rolling numerical indicator"

The proposal is stored as a write-once JSON file in .local/tool-factory.
The one explicit optional provider call may incur costs. There are no paid calls in CI.

To execute an independently testable PURE candidate inside an isolated Docker container:

    python -m max_trading_agent tool-sandbox --file .local/tool-factory/rolling_mean_v1.json

Only fixed independent oracle fixtures from knowledge/tool_factory_oracles.json may
certify a T0 sandbox test. Agent-written examples DO NOT count as correctness evidence.
Unknown novel tools are PENDING_INDEPENDENT_ORACLE (exit 3).
Known passing oracle yields SANDBOX_PASSED_REVIEW_REQUIRED (exit 0), not production admission.
Failed, timed-out and missing-oracle candidates exit 3; malformed/unsafe proposals exit 2.

## Security boundary and limitations

AST allowlist blocks imports, attributes, dangerous builtins, filesystem, network and
process API calls. Sandbox execution uses Docker --network none, --read-only,
non-root UID, cap-drop ALL, no-new-privileges, memory, CPU, pids and wall-time limits.
Only ephemeral candidate source, immutable fixed runner and independent oracle fixtures
are mounted read-only. No broker data, secrets or local directories are exposed.
Do not use privileged Docker or mount the host Docker socket.

Threat model: LLM text, retrieved RAG pages, candidate code and candidate-generated
examples are UNTRUSTED. They cannot change the immutable validator or oracle fixture.
Even successful Docker isolation is defense in depth, not perfect confinement.
Image python:3.12-alpine is not digest pinned; pin it and review the runtime supply
chain before any real release. Automated independent PR publishing, full external
dependency tool support, self-administered T1/T2/T3 promotion and live trading remain
separate, unaccepted roadmap requirements.

Issue: https://github.com/maxqstudio/max-trading-agent/issues/37

## G20 — independent two-stage T0 review and immutable receipt (research only)

For an already-proposed PURE numerical tool, explicitly invoke:

```sh
python -m max_trading_agent tool-review --file .local/tool-factory/rolling_mean_v1.json --review-dir .local/tool-factory/reviews
```

The evaluator always runs the **existing Docker sandbox** with both (1) trusted versioned `knowledge/tool_factory_oracles.json` and (2) a reviewer-owned separate deterministic challenge generated for the **known `rolling_mean_v1`** contract. Neither uses LLM-authored examples for correctness. An agent that memorizes the five published fixed-oracle examples will pass the old standalone `tool-sandbox` but fail `tool-review` on the second batch. This check is included as an actual Docker GitHub Actions job, as well as mocked cross-platform unit tests.

When both batches pass, an immutable **non-executable evidence receipt** is created once, using an ID derived from its complete fixed report, in the private review directory. The receipt binds the SHA-256 candidate, both oracle set hashes, case counts, the reviewer version, `evidence_only=true`, `decision=NO_TRADE`, `execution=DISABLED` and `owner_acceptance=PENDING`. Repeating identical evidence is idempotent; conflicts or symlinks are rejected. No candidate source code is stored in receipts and **nothing imports, registers, or promotes the generated tool**. The registry here is only an immutable T0 review evidence catalog, not a callable runtime tool catalog.

`PENDING_INDEPENDENT_ORACLE` (exit 3) remains mandatory for all other newly invented tool IDs, even if they have polished LLM-written examples. A wrong result in either independent Docker batch produces `T0_REVIEW_REJECTED` (exit 3) and no review receipt. Missing Docker/oracle or malformed inputs are blocked (exit 2). These codes are evaluation outcomes, never permissions.

**Threat-model limits:** the challenge cases and deterministic candidate-hash seed are public, **not secret holdouts**; adaptive code can still overfit. The added reviewer currently knows one approved *numerical contract*, not all future tools. Validation cannot establish full mathematical correctness or absence of every sandbox escape. Docker image `python:3.12-alpine` is tag-pinned, not digest-pinned, so supply-chain acceptance remains pending. GitHub CI never receives live accounts, tokens or broker permissions. T1 read-only agent registry, T2 DEMO, T3 LIVE, and risk policy changes remain owner-gated and unimplemented. [G20 issue #51](https://github.com/maxqstudio/max-trading-agent/issues/51).

## G21 — reviewer-owned extensible contracts and randomized properties

`tool-property-review` is a **separate optional T0 evaluation** from G20's stable `tool-review`:

```sh
python -m max_trading_agent tool-property-review --file tests/fixtures/tool_factory_range_span.json --review-dir .local/tool-factory/reviews
```

Versioned **repository-reviewed** `knowledge/tool_review_contracts.json` maps candidate IDs to trusted numerical reference operations. Initially it supports `ARITHMETIC_MEAN` for `rolling_mean_v1` and `NUMERIC_RANGE` (max minus min) for the new `range_span_v1`. Supporting another independently specified mean/range candidate requires an explicitly reviewed catalog entry plus external golden test cases. A different mathematical contract requires new independently reviewed reference code; there is **no general-purpose proof engine** for arbitrary LLM-created algorithms. Neither candidates, LLM-proposed examples, nor CLI arguments can specify trusted oracle code or add permissions.

The review first independently recomputes all repository golden expected results. Missing contracts return `PENDING_INDEPENDENT_ORACLE` (exit 3); malformed contract files or mismatched trusted golden outcomes fail **before Docker** (exit 2). Next, existing isolated Docker runs (1) the fixed independent golden cases and (2) **32 newly generated property/reference cases** (base, permutation, additive translation, positive scaling across eight input lengths). An OS-random 128-bit seed is chosen on each review; all expected outputs come from the trusted reference implementation, never the candidate. A candidate memorizing the published five `range_span_v1` golden cases passes the older fixed oracle but fails G21's extra property batch in real Docker GitHub CI.

A successful evaluation creates a **private, write-once, non-executable** `T0_PROPERTY_REVIEWED` receipt with SHA-256 candidate/contract/oracle/challenge hashes, input-case counts and the seed hex for reproducible challenge reconstruction; the seed is disclosed **after** that run. An incorrect candidate returns `T0_PROPERTY_REJECTED` (exit 3) and creates **no** receipt. The reviewer checks stage identity, no-trade flags and exact observed oracle case counts. Repeating the review with fresh randomness produces a new immutable evidence receipt; identical recorded seed and candidate reproduce the same review ID.

**Known limitations:** these are randomized bounded tests, not an exhaustive correctness proof, security proof, secret permanent holdout, or statistically defensible trading evaluation. Trusted catalog entries require separate independent code review, and any candidate able to change reviewer code/oracle files is outside this trust model. Generated Python runs only inside existing Docker defense-in-depth; the image remains version-tag-based rather than digest-pinned. Every review states `NO_TRADE`, `execution=DISABLED`, `owner_acceptance=PENDING` and `eligible_for_demo_or_live=false`. There is **no callable tool registry, automatic promotion, broker connection, or release permission**. [G21 Issue #53](https://github.com/maxqstudio/max-trading-agent/issues/53).
