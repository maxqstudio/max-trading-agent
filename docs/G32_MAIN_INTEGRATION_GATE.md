> **Current scope (G35):** The earlier 32-PR boundary below is historical. G35 pins predecessor SHAs through #80 and requires **all 35 PRs through #82** genuinely merged to `main`. A future canonical #84+ blocks the main gate until the reviewed baseline is updated. See [G35 full-stack gate](G35_FULL_STACK_GATE.md).

# G32 — Authenticated main integration completeness; NOT trading approval

G30 demonstrated a successful **temporary 30-step sequential merge** and G31 introduced post-merge test jobs. Neither proves that each real GitHub PR was ultimately merged into the intended `main` branch. G32 adds a narrowly scoped **read-only audit of GitHub's actual PR metadata**.

## Inputs and checks

`knowledge/g32_pinned_merge_identities.json` records the SHA, head branch and PR number of each of the **31 predecessor Draft PRs #14 through #74**. G32 adds itself as **PR #76**. The GitHub-hosted workflow `.github/workflows/g32-main-completeness.yml` queries GitHub's authenticated PR API using a token scoped to `contents:read` and `pull-requests:read`. The verifier refuses missing/duplicate PRs, a changed pinned SHA, changed head branch, foreign head/base repositories, closed-but-not-merged PRs, and invalid or unexpected branch/event contexts.

On a G32 **feature branch or PR** it must report `G32_EXPECTED_UNMERGED_STACK_HOLD` (or `G32_FEATURE_CI_NOT_MAIN_HOLD` after the owner has merged a prefix). **The feature job exits 3 deliberately**, while GitHub workflow checks that HOLD is the correct, safe behavior. A green feature workflow is therefore **not** a claim that the repository is integrated.

Only a genuine **`push` to `main`** after all **32 PRs #14–#76** have been merged and each GitHub base branch reports `main` can return `G32_GITHUB_MAIN_MERGE_METADATA_VERIFIED`. That result establishes only GitHub merge-target metadata: it does not prove that squash/rebase produced the same code as an earlier merge rehearsal, nor that a full main test suite passed. Those are separate responsibilities of G31's Python Linux/Windows, JavaScript/UI and real isolated Docker jobs on main.

## Owner merge procedure

1. Merge the stack **in order** beginning at #14. After each predecessor lands, retarget the successor base from its prior feature branch to `main`, inspect the changed diff and rerun required GitHub CI before accepting the next PR. Never treat a merge into a feature branch as equivalent to a merge into main.
2. When #76 is merged, inspect the **G31 Postmerge main CI** and **G32 Authenticated main integration completeness** runs triggered by the new main SHA. Both must be SUCCESS at that exact SHA before labeling the GitHub integration verified. A G32 main metadata success alone is insufficient.
3. Review the G32 SHA-bound evidence artifact `g32-main-integration-readonly`. It includes 32 PR numbers/head SHAs/base branches/statuses and only non-executable safe metadata. Artifacts are configured for 30-day retention. No GH API writes, merge, release, owner approval or trading order is issued.
4. Only after CI acceptance may the owner run G31's separate offline Windows `python -m tools.owner_mt5_preflight --repo .`, followed **deliberately and manually** by the read-only M09 `python -m max_trading_agent mt5-acceptance ...` command on a configured local terminal. No MT5 account or keys are used in GitHub CI.

## Limits

The PR API is authenticated by GitHub but **does not certify semantic code correctness, package supply chain, trading profitability, real broker behavior or independent reviewer approval**. All G32 artifacts explicitly retain `postmerge_python_js_docker_ci_verified=false` (G31 verifies that separately), `real_mt5_terminal_tested=false`, `owner_acceptance=PENDING`, `generated_tool_admitted=false`, `execution=DISABLED`, `decision=NO_TRADE`. No T1 model-generated code admission, DEMO/LIVE trade functionality or broker credentials.

Sources: [G32 Issue #75](https://github.com/maxqstudio/max-trading-agent/issues/75) and [G31 owner guide](G31_OWNER_MT5_PREFLIGHT.md). G32 itself remains Draft/unmerged until owner decides to integrate it.
