# G30 — Merge-commit replay of the full stacked PR chain (GitHub only)

## Purpose and fixed inputs

G29 tested one aggregate three-way merge of `main` plus the cumulative Draft stack. G30 closes the narrower **sequence gap** by replaying every declared PR as an **individual, disposable Git merge commit** on GitHub-hosted Linux and Windows, then comparing the final Git tree to a separate one-step aggregate merge.

The audited baseline is `main` SHA `5f27d132bc627fa73fde2e1b56b625cbdb09a465`. The reviewed manifest `knowledge/g30_pinned_pr_stack.json` binds exactly 29 predecessor heads: PR #14 through PR #70 in order. The current G30 branch head is the 30th merge, intended as Draft PR #72. Changing *any* pinned predecessor SHA, branch/base relation, repository identity, expected main SHA or the current G30 event's branch/commit **fails closed** rather than silently retesting a different stack.

## What GitHub Actions does

`.github/workflows/g30-sequential-main.yml` has repository permission **`contents:read` only** and runs on pushes to `feat/g30-sequential-main-rehearsal`. Each hosted runner checks out two independent directories: `controller` at G30's exact workflow SHA and `work` at the pinned actual `main` SHA, both without persisted credentials.

`tools/sequential_merge_rehearsal.py` checks the manifest, GitHub-hosted push event, checked-out SHA, remote HEADs and the Git ancestry between consecutive feature heads. In the disposable `work` checkout it executes **30 local merges**, one for each PR in ascending order, with temporary local merge commits; there is **no `git push`**. Every merge step records its PR number, exact pinned candidate head SHA, temporary merge SHA and resulting Git tree hash. On an actual merge conflict it emits the conflicted paths, stops and uploads a blocked report. An unrelated Git merge process failure is recorded separately, not falsely labeled a source conflict.

After step 30, a second local Git worktree starts from the *same* pinned main commit and performs a single aggregate no-commit merge of G30's head. G30 requires equality between the **sequential merged tree SHA** and the **aggregate merged tree SHA**. Mismatches fail the job rather than claiming integration readiness. On the successful sequential tree, both Linux and Windows run the complete Python suite. Linux also runs the Trading Room JavaScript suite and real non-root isolated Docker independent numerical reviewer: five trusted goldens and 32 seeded property cases must pass, an intentionally incorrect candidate must fail. Both platforms run the deterministic research-only `NO_TRADE` smoke.

Per-OS reports `g30-sequential-rehearsal-ubuntu-latest` and `g30-sequential-rehearsal-windows-latest` are GitHub Actions artifacts (30-day retention). Reports record 30 per-step SHAs, exact main/feature SHA, final merged tree, merge conflicts when applicable, full-test status and explicit `remote_repository_modified=false`, `generated_tool_admitted=false`, `decision=NO_TRADE`.

## Important limitations

- A successful rehearsal proves **only** that the pinned stack, in this exact order, can be merged into the pinned main using **merge commits** on temporary runners and that the resulting contents pass offline CI. It does **not** guarantee that squash/rebase merges, changed PR heads, changed `main`, independent reviewer sign-off or fresh PR retargeting will produce the same tree.
- G30's control-plane Git commands create **temporary local merge commits only**. They do not merge or retarget any real PR. Owner still must integrate actual PRs into `main` in order, check each newly targeted diff and rerun fresh CI on final `main`.
- No real MetaTrader 5 terminal/broker is used. Fake MT5, backtest, paper-only, SHADOW, Sigstore provenance and non-executable T1 candidate staging are **not** real-trading acceptance. Independent code review, owner acceptance, actual terminal tests and all DEMO/LIVE promotion gates remain outstanding.
- The job uses a pinned public `python:3.12-alpine` sandbox image **by tag**; it does not claim image digest pinning or an invulnerable sandbox.
- A write-only artifact from a CI runner is not a substitute for authoritative GitHub branch protection; this connected GitHub integration could not inspect branch protection in prior milestones.

For the latest exact-head run and evidence links, use [G30 Draft PR #72](https://github.com/maxqstudio/max-trading-agent/pull/72) and [Issue #71](https://github.com/maxqstudio/max-trading-agent/issues/71). For owner merge instructions, see [G28 Owner Handoff](G28_OWNER_HANDOFF.md).

**Status: NO_TRADE, execution=DISABLED, owner_acceptance=PENDING.**