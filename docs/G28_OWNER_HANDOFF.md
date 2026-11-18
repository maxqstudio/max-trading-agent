> **Current scope (G35):** The original 27/28-PR snapshot below is historical. There are now 35 canonical stacked PRs **#14 through #82**. The updated auditor includes future canonical even-numbered PRs and fails on gaps. Use the current [G35 full-stack gate](G35_FULL_STACK_GATE.md) and live GitHub report before merging.

# G28 — GitHub-only owner merge handoff and project boundary

**Snapshot scope (17 November 2026).** The canonical agent code is implemented as a **single stack of 27 open Draft PRs #14, #16, … #66**, all still **unmerged** when this handoff was authored. The G28 work is itself a Draft PR #68 on top of #66. These PRs are not 27 independent patches to merge in arbitrary order: each targets the preceding feature branch. **Main does not yet contain their cumulative work.**

## Automated read-only verification

`.github/workflows/g28-stack-handoff.yml` checks current public GitHub PR metadata with a token restricted to `contents:read` and `pull-requests:read`. It runs `python tools/stack_audit.py` and its offline adversarial tests. It emits a downloadable **`g28-owner-pr-merge-handoff`** artifact containing a JSON list with exact PR numbers, HEAD SHAs, current bases, merged/Draft state, dependency chain, next merge candidate, required retarget status, and remaining owner/terminal acceptance blockers. The job never calls a GitHub mutation endpoint and does not obtain a token with write permission.

Use the live check again **before** each merge; existing archived CI PASS only proves the tested SHA, and retargeting/merging can change what must be validated.

When a successfully merged PR's source branch is deleted, GitHub may report `head.repo=null`. G28 tolerates that **only** for a PR with a valid GitHub merged/closed record, while still verifying its original head ref/SHA and the target repository. An unmerged PR with missing source repository remains a hard failure; G28 cannot accept missing branch identity as a merge shortcut.

## Owner-only merge procedure

1. First review **PR #14** targeting `main`; when satisfied and accepted, mark Ready for review as appropriate, require current CI and merge **into `main`**.
2. Before reviewing **PR #16**, edit its **base branch** from `feat/m01-mt5-readonly-adapter` to `main` *after* PR #14 is merged. Inspect the new file diff carefully and rerun fresh CI; then merge PR #16 **into `main`**. Do **not** merge #16 into `feat/m01-mt5-readonly-adapter` after #14 has landed: that would not integrate M02 into main.
3. Repeat this pattern for PRs **#18, #20, #22, #24, #26, #28, #30, #32, #34, #36, #38, #40, #42, #44, #46, #48, #50, #52, #54, #56, #58, #60, #62, #64, #66, then G28 #68**. Each time: previous PR must be merged first, retarget current PR base to `main`, inspect the resulting diff, obtain appropriate independent review/owner sign-off and current CI, and merge into `main`. If GitHub reports conflicts, stop instead of force-merging or silently rewriting unrelated code.
4. After the final merge, run the entire Python/JS suite and real Docker Tool Factory gate against **`main`'s exact final SHA**. Compare PR SHA tests to merged-main tests; do not mark the repository production-ready merely because each PR passed independently.
5. Perform separate **owner local terminal acceptance** for MT5 and any real account/broker integration. GitHub-hosted fake-MT5, shadow, paper, backtest, RAG, tool registry and signed evidence are **not** real market/terminal/broker acceptance.

**The assistant has not merged or retargeted any PR.** The owner stated they will handle merging later.

## GitHub-only work completed and what remains blocked

| Area | GitHub-delivered capability | Still blocked |
|---|---|---|
| M01–M09 read-only/SHADOW | MT5 read-only adapter, agent research watcher, HMAC identity, forward shadow evidence, terminal acceptance harness | Owner's actual MT5 terminal/data/account |
| Canonical M03/M04/M05 | Paper-only reconciliation, offline after-cost RL/backtest robustness, RAG index/link audits | Real performance/strategy validity and broker readiness |
| G20–G23 Tool Factory | Isolated PURE candidate Docker checks, independent numerical references, model hypotheses quarantine, immutable evidence packets | Arbitrary new math without reviewed independent oracle/source |
| G24–G25 Sigstore | Signed curated T0 Docker evidence and separate read-only consumer | Third-party source-security assurance; proof of arbitrary LLM-tool correctness |
| G26 GitHub governance | Authenticated PR reviewer/owner gate preview | Real independent reviewer, owner signed comment, merge |
| G27 read-only reference catalog | Two built-in offline math references, SHA-only staging of candidate | Any real generated-code T1 loader; T2/T3/DEMO/LIVE |
| G28 | Live GitHub merge-dependency audit & owner handoff artifact | Owner merge and acceptance |

**Audit limits:** The G28 report verifies GitHub PR API structure and dependency order only. It does **not** independently verify every historical CI result, GitHub branch protection (integration returned 403 earlier), source-code security, model correctness, real market conditions, profitability, or owner permission. All automated tool claims remain `NO_TRADE`; generated candidate code is not admitted into runtime. Branch/PR merge is a separate owner action, not a deterministic test result.

See [G28 issue #67](https://github.com/maxqstudio/max-trading-agent/issues/67). The latest verified CI run SHA and G28 report artifact are linked from its Draft PR's acceptance comment.
