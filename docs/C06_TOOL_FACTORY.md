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
