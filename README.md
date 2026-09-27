# MAX Trading Agent

MAX REBUILD is a local Windows control plane for deterministic Strategy optimization/Challenger/Champion governance and a separately gated scientific Research pipeline around MetaTrader 5 execution evidence.

## Current scientific boundary

- R00: ACCEPTED.
- R01 source/runtime repair: TARGETED REAUDIT PASS.
- Real R01 scientific run: NOT STARTED.
- R01 scientific result: NOT_PROVEN.
- R02: BLOCKED / NOT STARTED.
- Model training: 0.
- ONNX: 0.
- Research Challenger: 0.
- Governance work must not mutate Strategy Champion authority.

R01 implementation/runtime readiness is not R01 scientific PASS.

## Governance reading order

PROJECT_PROFILE.yaml
→ docs/SYSTEM_OVERVIEW.md
→ docs/CURRENT_STATE.md
→ docs/PROJECT_MANIFEST.md
→ remaining profile-required generated governance documents under docs/
→ exact relevant source/tests.

## Current governance authority

MAX uses Skill Workflow exact authority:

9e22feddb8f94e8c0f1af6a33e14b64de5068f8f

Governance model:

- .workflow/*.json = machine-readable semantic/governance authority.
- .workflow/workflows/*.json = current workflow/state-machine authority.
- .workflow/tools/ = project-local governance tooling vendored byte-identically from the pinned Skill Workflow authority.
- docs/*.md = generated human-readable Project Truth Compiler projection.
- docs/sequence/sessions/ = current DURING sequence session contracts.
- docs/sequence/generated/ = generated static actual sequence evidence.
- docs/sequence/runtime/ = runtime sequence evidence location when policy requires it; current policy does not.

Do not manually patch generated canonical documents. Repair upstream source/.workflow authority and regenerate.

Historical acceptance/audit documents under docs/ remain historical evidence and are not rewritten merely to match current terminology.

## Source and runtime authority

GitHub exact commit is source authority.

Owner runtime/E2E authority: D:\MAX_REBUILD

Canonical startup: RUN_MAX.cmd

Automation/non-pausing form: RUN_MAX.cmd --no-pause

Backend: http://127.0.0.1:8000

Frontend: http://127.0.0.1:5173

GitHub Actions on Windows are the hosted build/test authority during development. MT5 runtime/E2E remains final Owner-PC phase acceptance.

## Development

Backend: scripts\dev_backend.cmd

Frontend: scripts\dev_frontend.cmd

See docs/RUNBOOK.md for current deterministic governance/test/runtime commands.


## Windows installation

MAX Trading Agent is Windows-only because its production execution boundary integrates with MetaTrader 5.

    git clone https://github.com/maxqstudio/max-trading-agent.git
    Set-Location max-trading-agent
    powershell -ExecutionPolicy Bypass -File .\INSTALL.ps1

## Hosted development workflow

- GitHub is the source and hosted build/test authority.
- GitHub Actions runs on windows-latest only.
- Skill Workflow governance remains vendored under .workflow/.
- MetaTrader 5 runtime/E2E is deferred to final Owner-PC acceptance at the end of a phase.

## Support

- Saweria: https://saweria.co/maxq
- PayPal: https://paypal.me/JacksonJackson1501

Support is optional and does not affect access to the public repository or its features.
