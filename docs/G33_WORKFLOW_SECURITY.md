# G33 — GitHub Actions source-pinning and least-privilege review

## Security gap actually found

Several of this repository's older workflows used movable GitHub Actions version tags (`actions/checkout@v4`, `actions/setup-python@v5`, `actions/setup-node@v4` or `actions/upload-artifact@v4`). This leaves the exact action code that a future CI run executes subject to future tag updates. Tag usage does **not** prove a past compromise. The existing G24–G32 jobs already pinned most actions; G33 applies the same approach to every currently committed workflow.

G33 replaces those references with the full **40-character commit SHAs already exercised and verified by the preceding repository CI**. It also explicitly sets `persist-credentials: false` on every `actions/checkout` step; this prevents the checkout action from leaving the GitHub token in repository Git configuration for subsequent code steps. Neither measure is a claim that all CI runner risks have been eliminated.

| Reviewed action | Pin used |
|---|---|
| `actions/checkout` | `11d5960a326750d5838078e36cf38b85af677262` |
| `actions/setup-python` | `a26af69be951a213d495a4c3e4e4022e16d87065` |
| `actions/setup-node` | `49933ea5288caeca8642d1e84afbd3f7d6820020` |
| `actions/upload-artifact` | `ea165f8d65b6e75b540449e92b4886f43607fa02` |
| `actions/download-artifact` | `d3f86a106a0bac45b974a628896c90dbdf5c8093` |
| `actions/attest` | `1e69f48acb82d1966a394da916b4c1698aa569d6` |

## Enforced security invariants

`python -m tools.workflow_security_audit --root .` inspects **every** `.github/workflows/*.yml` or `.yaml` file, without contacting providers or executing the workflow itself. It fails closed when a `uses` action is not on the reviewed allowlist with its exact SHA, or if checkout fails to explicitly disable credential persistence. It rejects untrusted `pull_request_target` and `workflow_run` triggers, missing global read-only permissions, unknown write grants and an attempt to duplicate the signer credentials into another job.

The **only** action that may request `id-token: write` and `attestations: write` is `.github/workflows/g24-signed-t0.yml`, job `attest-isolated-tool-review`; that job is deliberately responsible for G24 Sigstore evidence. G25's independent consumer, the ordinary CI jobs, the PR stack audits and G33 itself remain read-only. This is enforced as a repository policy—changing the allowlist or scope must be a separately reviewed code change, never a self-declared LLM approval.

Run locally or in CI:

```sh
python -m tools.workflow_security_audit --root .
python -m unittest discover -s tests -p test_workflow_supply_chain.py -v
```

The G33 read-only GitHub-hosted workflow `.github/workflows/g33-supply-chain.yml` runs those checks for the feature branch and changed PR files. The normal full Linux/Windows CI and the isolated Docker Tool Factory regressions are also rerun. A GitHub Actions artifact named `g33-workflow-security-static-report` records the audited workflow count, action count and signer privilege without uploading raw workflow secrets.

## Limits and owner handoff

A static policy checker is **not** a YAML interpreter, a third-party audit of action binary content, a runtime sandbox proof, or a guarantee against malicious scripts inside allowed workflows. The GitHub Actions service and each allowed third-party action commit remain trust dependencies. Docker's existing public `python:3.12-alpine` tag is not pinned to a digest by this change; it is a separate supply-chain issue. This milestone **does not** merge any of the existing Draft PRs, retarget their bases, establish MT5 terminal acceptance, permit autonomous brokerage or register generated tools. All trading remains `NO_TRADE` and `execution=DISABLED`.

Keep G33 a stacked Draft PR above [G32 #76](https://github.com/maxqstudio/max-trading-agent/pull/76). The owner still handles the 33-PR merge sequence, exact final `main` CI and local Windows M09 acceptance. See [G33 issue #77](https://github.com/maxqstudio/max-trading-agent/issues/77).
