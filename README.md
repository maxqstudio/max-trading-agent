# MAX Trading Agent

MAX Trading Agent is a Windows-only, GitHub-first control plane for deterministic Strategy optimization, Challenger/Champion governance, and a separately gated scientific Research pipeline around MetaTrader 5 execution evidence.

## Current authority

Development authority is:

```text
maxqstudio/max-trading-agent main exact SHA
→ work/* branch
→ GitHub Actions on windows-latest
→ repair until every required job is green
→ merge to main
→ revalidate main
```

Real MetaTrader 5, MetaEditor, broker/data-root, filesystem deployment, fresh-database bootstrap, and browser E2E are **not** proven by GitHub Actions. They are deferred to one final Owner-PC acceptance phase after the hosted development roadmap is complete.

Historical MAX REBUILD evidence remains provenance only. It is not current source or runtime authority.

## Current scientific boundary

- Previous-epoch R00 acceptance: historical only.
- Current-epoch R00: not executed during hosted development.
- Current-epoch R01: blocked/not started; no scientific result is proven.
- R02: BLOCKED / NOT STARTED.
- Model training: 0.
- ONNX: 0.
- Research Challenger: 0.
- H0 and ordinary hosted phases must not mutate Strategy Champion authority or start real Research execution.

R01 implementation/runtime readiness is not R01 scientific PASS.

## Governance

MAX uses Skill Workflow exact authority:

```text
9e22feddb8f94e8c0f1af6a33e14b64de5068f8f
```

Governance model:

- `.workflow/*.json` = machine-readable semantic/governance authority.
- `.workflow/workflows/*.json` = current workflow/state-machine authority.
- `.workflow/tools/` = project-local governance tooling vendored byte-identically from the pinned Skill Workflow authority.
- `docs/*.md` = generated Project Truth Compiler projections where marked generated.
- `docs/sequence/sessions/` = current DURING sequence contracts.
- `docs/sequence/generated/` = generated static sequence evidence.

Do not manually patch generated canonical Markdown. Repair source/`.workflow` authority and regenerate.

Historical audit/acceptance documents may retain previous repository names or paths when that is historically correct.

## Public source-only boundary

This repository intentionally excludes private/runtime state, including:

- real `.env` files, API keys, tokens, passwords, private keys, and certificates;
- SQLite runtime databases;
- `evidence/`, `artifacts/`, optimizer/Challenger/Champion/Research runtime outputs, backtests, and datasets;
- MT5 terminal/account/data-root files and broker credentials;
- `.venv/`, `node_modules/`, Vite caches, frontend build output, and local runtime logs.

Tests must use source-controlled synthetic fixtures or create disposable synthetic state during the test. Private historical runtime bundles are not test dependencies.

## Windows installation

MAX Trading Agent is Windows-only because its production execution boundary integrates with MetaTrader 5.

```powershell
git clone https://github.com/maxqstudio/max-trading-agent.git
Set-Location max-trading-agent
powershell -ExecutionPolicy Bypass -File .\INSTALL.ps1
```

`INSTALL.ps1` is the canonical installer. Existing legacy `.cmd` launchers remain compatibility entry points until intentionally migrated.

## Hosted validation

`.github/workflows/windows-ci.yml` validates on `windows-latest`:

- source-only/public repository policy;
- redacted exact-tree secret scan;
- Skill Workflow provenance and STRICT selftest;
- Project Truth/compiler and governance synchronization;
- full backend regression and `pip check`;
- frontend tests, lint, production build, and dependency tree.

A phase is not complete while any required job is red. After a green phase, merge it to `main` and revalidate `main`; do not request Owner-PC testing between ordinary hosted phases.

## Runtime entry point

Canonical legacy runtime wrapper:

```text
RUN_MAX.cmd --no-pause
```

It is reserved for the final Owner-PC runtime acceptance after the GitHub roadmap is complete, not ordinary hosted phase verification.

## Support

- Saweria: https://saweria.co/maxq
- PayPal: https://paypal.me/JacksonJackson1501

Support is optional and does not affect access to the public repository or its features.
