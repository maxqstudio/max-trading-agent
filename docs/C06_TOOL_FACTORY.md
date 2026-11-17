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

## G22 — model-authored oracle hypotheses enter quarantine, never independent evidence

G22 adds an **opt-in** LLM hypothesis drafting step and a completely **offline** deterministic audit step. For an existing private T0 candidate JSON:

```sh
# One optional billable provider call; do not use in CI
python -m max_trading_agent tool-oracle-propose --file .local/tool-factory/my_tool.json --oracle-dir .local/tool-factory/oracle-hypotheses

# Inspect the generated hash-named JSON, then run strictly offline
python -m max_trading_agent tool-oracle-audit --file .local/tool-factory/my_tool.json --oracle-file .local/tool-factory/oracle-hypotheses/<sha256>.json --audit-dir .local/tool-factory/oracle-audits
```

Only the candidate's **tool ID, declared purpose, interface and SHA-256** go into the oracle-writing model prompt; its Python code and self-generated examples are deliberately withheld. This mitigates simple copying but does **not** make a second prompt or second LLM independent. The model returns *untrusted hypotheses*, 3–12 bounded numeric input/expected proposals, and optional named property claims. Its JSON cannot contain executable code, file paths, arbitrary property functions, source URLs, broker authority, registry changes or policy edits. Duplicate JSON keys, repeated inputs, nonfinite numbers, extra keys and mismatched candidate hashes are rejected. Proposals are stored privately, content-addressed, write-once, without import or execution.

`tool-oracle-audit` checks whether at least **three proposed inputs differ from the candidate's own examples**; copied examples cannot qualify as coverage. For **already reviewer-owned G21 contracts**, the audit independently recomputes each proposed expected value from the trusted reference and checks that named claimed properties match the known operation. Claims which disagree become `QUARANTINED_REFERENCE_MISMATCH`. Claims that agree become only `QUARANTINED_REFERENCE_CONSISTENT`: **even a consistent case proposal is not a tool test or approval**. For any novel tool with no independently reviewed contract, status is `QUARANTINED_UNVERIFIED`; inadequate coverage is `QUARANTINED_INSUFFICIENT_COVERAGE`. No path accepts model-authored values as official G21 golden fixtures. All audit outcomes exit **3**, not a success exit code. Malformed specs/IO failures exit 2.

The independent audit writes only a private immutable, hash-bound **quarantine receipt**: proposal+candidate fingerprints, coverage counts, reference-check status, `trusted_oracle_adopted=false`, `candidate_verified=false`, `eligible_for_demo_or_live=false`, `decision=NO_TRADE`, `execution=DISABLED`, `owner_acceptance=PENDING`. The LLM-authored text is **not** copied into an executable registry. An independently authored reference, reviewed goldens, adversarial tests and owner review are still required before use of the G21 sandbox for newly invented mathematics.

G22 does not execute candidate source during oracle auditing. No provider call runs in GitHub Actions; CI uses fake model output and offline CLI fixtures, rechecks that trusted catalog bytes are unchanged and still reruns the existing real Docker G20/G21 acceptance suite. The Docker base image remains tag-pinned rather than digest-pinned. Quarantined artifacts must be treated as untrusted when reviewed manually; they are **not** cryptographic or mathematical publisher attestation, trading readiness or proof of profitability. [G22 issue #55](https://github.com/maxqstudio/max-trading-agent/issues/55).

## G23 — offline evidence packet for an independent reviewer

G23 provides **a review manifest, not tool approval**. For an already saved PURE candidate, quarantined G22 oracle hypothesis and the independently recomputed G22 audit JSON, run:

```sh
python -m max_trading_agent tool-review-package \
  --file .local/tool-factory/example.json \
  --oracle-file .local/tool-factory/oracle-hypotheses/<sha256>.json \
  --audit-file .local/tool-factory/oracle-audits/<review-id>.json \
  --package-dir .local/tool-factory/review-packages
```

An optional `--property-receipt-file .local/tool-factory/reviews/<review-id>.json` supplies a G21 reviewer receipt, **not** an execution attestation. The CLI emits JSON and **exits 3 regardless of packet completeness**, since it cannot grant acceptance. Invalid/unrelated/tampered inputs exit 2.

The assembler validates the candidate's strict PURE contract, checks its SHA-256 against the LLM oracle pack, independently recomputes the entire G22 audit from the supplied candidate and oracle, and requires **field-for-field equality** with the supplied audit. For an optional G21 receipt, the assembler checks the candidate ID/version/SHA, status, NO_TRADE fields, trusted reference and golden hashes, expected property-case count, and regenerated challenge digest from the receipt's seed. It also validates the receipt's self-hash. A passing result is explicitly labeled `STRUCTURAL_ONLY_UNATTESTED`, because an attacker could fabricate a valid unsigned receipt without actually running Docker.

The write-once, content-addressed JSON package records only safe IDs, hashes, source-independent reference status, missing evidence and guardrails. It contains **no candidate code, LLM hypothesis text, test cases, secrets or executable registry entry**. All packages are `REVIEW_PACKAGE_INCOMPLETE`, `evidence_only=true`, `candidate_verified=false`, `trusted_oracle_adopted=false`, `decision=NO_TRADE`, `execution=DISABLED`, `eligible_for_demo_or_live=false`, `owner_acceptance=PENDING`. A new tool without an independently reviewed contract lists missing reference and golden evidence; a known tool without a G21 receipt lists that missing evidence; even a structurally valid G21 receipt still requires a **signed sandbox execution attestation**, independent human review and owner acceptance. G23 never creates or accepts these missing approvals.

A packet is a reproducible **checklist for a reviewer**, not a cryptographic publisher attestation, mathematical proof, live broker test, model-security guarantee, or deployment permit. All validation is offline, does not execute candidate code or contact a model or broker, and cannot modify trusted reference files. GitHub Actions reruns G20/G21's actual Docker suite to prevent regressions but that separate CI run does not magically sign or approve a user-supplied receipt. See [G23 Issue #57](https://github.com/maxqstudio/max-trading-agent/issues/57).

## G24 — GitHub-Sigstore signed Docker T0 test evidence (not admission)

A **dedicated, push-only GitHub-hosted Actions workflow**, `.github/workflows/g24-signed-t0.yml`, runs the existing G21 property reviewer for the **curated** `range_span_v1` fixture. The candidate executes only inside its original no-network, read-only, non-root Docker sandbox, with five independent fixed oracle cases and 32 newly randomized property/reference cases. The evidence producer refuses any other tool ID, incorrect GitHub repository, commit, branch, signer workflow, event, self-hosted runner, missing expected review results or malformed G21 receipt.

After a successful real Docker review, the workflow creates one JSON evidence file including the candidate SHA-256, trusted contract and golden hashes, property-challenge SHA and seed, G21 receipt fingerprint, exact GitHub repository/ref/commit, and workflow run/attempt. The dedicated job alone receives `id-token:write` (GitHub OIDC) and `attestations:write`; it has `contents:read` and no broker or provider secret. The `actions/attest` action is pinned to a full commit SHA and signs **that artifact's digest and GitHub workflow identity** with Sigstore. Existing PR CI and Tool Factory jobs retain read-only repository permission and no OIDC signing grant.

A separate step uses `gh attestation verify` against the emitted Sigstore bundle with strict `--repo`, `--signer-workflow`, `--source-digest`, `--source-ref` and `--deny-self-hosted-runners`. The Python verifier also validates evidence schema, source SHA, signed subject filename+digest, SLSA predicate and certificate verification result. A modified copy of the same evidence filename **must fail verification**. No unsigned fallback is accepted when attestation signing or verification fails.

The signed statement establishes **artifact provenance**: GitHub's claimed workflow and commit produced a file asserting successful tests. It does NOT establish an independent mathematical proof of the generated code, sandbox invulnerability, absence of a compromised workflow, strategy profitability, or approval to load a runtime tool. In particular, G23's arbitrary copied user-local property receipt is still `STRUCTURAL_ONLY_UNATTESTED`; it is **not silently upgraded** because some unrelated workflow succeeded. A future separate independent verification protocol would need to bind each third-party candidate to the attestation subject before changing that status.

Inspect the GitHub Actions run under **G24 Sigstore signed T0 Docker evidence** or retrieve its published attestation via the GitHub API; for independently obtained matching subject bytes, `gh attestation verify FILE -R maxqstudio/max-trading-agent --signer-workflow maxqstudio/max-trading-agent/.github/workflows/g24-signed-t0.yml` validates the signer. The workflow also requires the exact source SHA/ref. **Owner acceptance remains `PENDING`; every proof and packet retains `NO_TRADE`, `execution=DISABLED`, no T1/DEMO/LIVE permission and no tool admission.** [G24 Issue #59](https://github.com/maxqstudio/max-trading-agent/issues/59).

## G25 — independent downloaded Sigstore evidence linked to G23 review (T0 only)

G25 corrects a concrete G24 evidence gap: G24 signed a JSON claim naming a G21 property-review receipt but uploaded only the claim and Sigstore bundle. An independent consumer now needs to inspect **the exact G21 receipt bytes**, verify its independent goldens and 32 property cases, and match its digest to the signed G24 subject. The G24 signer now exports `g24-property-receipt.json` with the subject and Sigstore bundle. Only the subject itself is signed; its SHA-256 field cryptographically binds the separate receipt after verification.

The dedicated **`independent-download-and-bind` job** in `.github/workflows/g24-signed-t0.yml` runs on a different GitHub-hosted runner after signing finishes. It has **`contents:read`, `actions:read` only**: no `id-token:write`, no `attestations:write`, no model/provider/broker secrets. It downloads the exact *same workflow run's* named artifact using the SHA-pinned `actions/download-artifact@v4` action (not a cross-run lookup). The initial `gh run download` approach was rejected by GitHub's API with HTTP 401 in CI, so the consumer uses the standard same-run artifact retrieval instead. It independently constructs the known fixture's G22 oracle audit and G23 immutable review package, then checks against the downloaded G21 receipt and signed G24 claim.

Independent consumers can run the same offline G23/G21 reconstruction and signature verification for previously downloaded files:

```sh
python -m max_trading_agent.signed_review_link \
  --candidate tests/fixtures/tool_factory_range_span.json \
  --oracle /private/review/oracle.json \
  --audit /private/review/audit.json \
  --package /private/review/package.json \
  --receipt /private/signing-artifact/g24-property-receipt.json \
  --evidence /private/signing-artifact/g24-t0-proof.json \
  --bundle /private/signing-artifact/g24-sigstore-bundle.json \
  --expected-commit FULL_40_CHARACTER_GIT_SHA \
  --expected-ref refs/heads/feat/g25-independent-signed-link \
  --expected-run-id GITHUB_SIGNER_RUN_ID \
  --expected-attempt 1
```

The caller must obtain the expected signer run, commit, ref and artifact **independently** from GitHub, not copy them blindly from untrusted artifact JSON. The tool internally invokes `gh attestation verify` with the required repository, signer workflow, source commit/ref, bundle and hosted-runner constraints; if GitHub CLI, Sigstore verification, subject bytes or any expected value are unavailable/mismatched, the run **fails closed**. All JSON inputs reject duplicate keys, wrong schemas, tampered reviews and candidate/receipt mismatch. A structurally valid G23 review package must exactly recompute from the supplied candidate/oracle/G21 receipt; the downloaded G21 receipt's candidate SHA, seed, trusted contract/goldens and challenge hashes must exactly match the signed subject and the G23 packet.

If successful, it creates a **separate content-addressed, private, write-once** link record `SIGNED_WORKFLOW_EVIDENCE_LINKED_REVIEW_PENDING`. It never modifies the original G23 `REVIEW_PACKAGE_INCOMPLETE` packet. The link explicitly records `GITHUB_WORKFLOW_SIGNED_CLAIM_ONLY`, signed subject digest, bundle digest, original packet/receipt IDs, remaining independent source/human review and owner-acceptance gates. The CLI emits a JSON record and **exits 3 even on a successful link**, so orchestration does not mistake evidence for authorized tool execution. No model-authored source or raw hypothesis text is included in the record.

The second runner constitutes an **independent verification process**, not an independent mathematical audit: both signer and consumer jobs are defined by the same source repo. A compromised signing workflow could still produce signed false claims about Docker tests. Sigstore proves **who signed which workflow-produced bytes**; it does not certify computation correctness, strategy profitability, sandbox security, permission changes or safe broker integration. The new G25 link **never admits a tool**, `candidate_verified=false`, `NO_TRADE`, `execution=DISABLED`, `eligible_for_demo_or_live=false`, `owner_acceptance=PENDING`. Tests cover forged subject/receipt/package, mismatched SHA/ref/run and missing cryptographic verification. See [G25 Issue #61](https://github.com/maxqstudio/max-trading-agent/issues/61).
