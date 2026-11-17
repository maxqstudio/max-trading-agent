# G35 — Full-stack GitHub merge completeness repair

## Verified issue

Previous milestones implemented a **stale release handoff**: G28's `stack_audit.py` only inspected 27–28 PRs through #68, while G32's `main_integration_gate.py` required 32 PRs through #76. After G33 and G34, the real repo already had **34 open Draft PRs through #80**. If left unchanged, those controls might omit required PRs or claim that an incomplete newer stack has landed on `main`.

G35 is intentionally **not** a new trading feature. It repairs only these existing GitHub integration controls, plus their narrow tests and workflow triggers.

## What changed

**Live owner handoff (G28).** `tools/stack_audit.py` now has an audited minimum of all **35 PRs #14 through #82**, including G35 itself. It also discovers any *later canonical even-numbered PRs* returned by the authenticated GitHub API. The auditor fails closed for missing PRs, gaps in numbering, unexpected bases, duplicate IDs, out-of-order merges, closed-unmerged PRs and changed repository identity. A future #84 or #86 can no longer be silently hidden behind the old #68 cutoff. A live handoff still reports the next owner-required PR and clearly marks when a successor must be retargeted to `main`.

**Strict main-only completion (G32).** `knowledge/g32_pinned_merge_identities.json` now pins **34 exact predecessor head SHAs #14–#80**; PR #82 (G35) must also exist and be GitHub-merged **into main** before G32 returns `G32_GITHUB_MAIN_MERGE_METADATA_VERIFIED`. A genuine GitHub-hosted `push main`, not a PR synthetic merge or feature-branch run, is mandatory. G32 refuses missing G33 #78, G34 #80 or G35 #82, changed pinned SHAs, foreign repo metadata or a next canonical #84+ that has not been incorporated into an explicitly updated baseline.

**Expected feature result:** The G35 PR/push workflows **must exit HOLD from their CLI**: `G32_EXPECTED_UNMERGED_STACK_HOLD` or `G32_FEATURE_CI_NOT_MAIN_HOLD`. GitHub Actions checks that exit 3 is correct and treats the *workflow* as successful only if the run truthfully reports HOLD and no trading authority. It must never call feature CI a verified main deployment.

**Regression and acceptance:** G28 owner handoff and G32 main-only workflows have G35 feature triggers, plus updated exact coverage counts and unit tests. Existing full Linux/Windows/JS and isolated Docker Tool Factory regression pipelines run against the complete G35 branch. GitHub Actions metadata reports show source ref and commit, ordered PRs and missing owner gates; no change to any PR base, merged status or `main`.

## Critical limitation

G28's owner report dynamically includes later canonical PRs; G32's release gate **deliberately does not auto-trust future milestones**. If G36 creates #84, G28 includes it, while G32 blocks a future main-complete claim until the pinned review baseline is deliberately extended. This is a desirable fail-closed mismatch, not a bug. It is still possible for real PRs to be squash/rebase merged and produce contents different from prior simulated merge trees; G28/G32 authenticate PR metadata only. They do **not** verify final `main` regression, code security, branch protection, MT5 terminal behaviour or owner acceptance.

Owner still controls all 35 Draft PR merges, retargets each successor to `main` *after* predecessor merge, reviews the diff and reruns CI. After the final merge, wait for genuine `main` CI (G31), check G32 current main metadata, then perform the separately opt-in local MT5 acceptance. No generated T1 execution, trading order, DEMO/LIVE or real broker use is authorized.

**Status: `NO_TRADE`, `execution=DISABLED`, `owner_acceptance=PENDING`.** [Issue #81](https://github.com/maxqstudio/max-trading-agent/issues/81) · [G28 original owner procedure](G28_OWNER_HANDOFF.md) · [G31 local MT5](G31_OWNER_MT5_PREFLIGHT.md).
