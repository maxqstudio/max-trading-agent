# G29 — GitHub-hosted pre-merge integration rehearsal

## Why a pre-merge rehearsal is required

At the start of G29, GitHub's compare endpoint confirmed that `main` (`5f27d132bc627fa73fde2e1b56b625cbdb09a465`) and the G28 cumulative Draft branch (`da31ce902ac99ce747da6ea1ba2ad5c97d747c89`) had **diverged**: 162 commits ahead on G28, one commit behind `main`, sharing ancestor `8809d2fd461c745fa88e950df4abf9c25640f6dd`. Therefore G28's CI tested the feature tree, **not** a simulated merge of feature + current main.

## What the G29 workflow actually verifies

`.github/workflows/g29-ephemeral-main-merge.yml` runs on GitHub-hosted Ubuntu and Windows for push events to `feat/g29-main-integration-rehearsal`. It has **`contents:read`** only, and does not run on the owner's PC. For each OS:

1. Checkout exact `main` and require its SHA to equal the observed baseline; fetch the exact event's cumulative G29 feature commit and verify it matches `GITHUB_SHA`.
2. Run Git's normal three-way merge **without committing** (`git merge --no-ff --no-commit`) in the disposable runner. On conflict, fail and record the list of conflicted files without pushing, changing a PR, or force-merging.
3. Record the Git merge-base SHA and the **staged merged tree hash**. `HEAD` remains `main` throughout the test; tests run against the combined files in the worktree, not the old main files.
4. Run full Python offline tests on **both Linux and Windows**, plus R0 reference checks and proof that generated candidate source remains `T1_CANDIDATE_NOT_REGISTERED`. Run Trading Room JavaScript/UI tests on Linux.
5. On Linux, pull the public Python sandbox image and perform **actual isolated Docker independent golden/property review** (5 fixed goldens, 32 randomized property cases). Intentionally wrong Python must fail, with `NO_TRADE` maintained.
6. Write one small report artifact per OS, `g29-rehearsal-ubuntu-latest` / `g29-rehearsal-windows-latest`, with current main SHA, feature SHA, merge-base, staged merged tree SHA, OS, conflict paths, full-test PASS flags and `remote_repository_modified=false`. Artifacts retain for 30 days.

The rehearsal uses no GitHub mutation token, OIDC, broker secrets, real MT5 terminal, LLM API key or trade/order functionality. If the main SHA changes, the workflow **fails closed** until its baseline and assumptions are re-reviewed. It is a disposable two-tree integration test, **not** a merge into the repository.

## Acceptance and remaining work

A clean cumulative merge with passing tests shows that the tested snapshot of `main` and the cumulative feature branch can coexist under that particular three-way merge on GitHub-hosted Linux and Windows. It does **not** guarantee that sequentially retargeting and merging **28 individual PRs** in different merge modes (merge/squash/rebase) produces an identical tree. Owner must still review changes, retarget and merge in order (#14, #16, …, #68, then G29 #70), and rerun the entire CI against final `main` HEAD. Review/owner gates from G26 remain pending; simulated CI is not real terminal or trading acceptance.

As before, generated tool admission, broker execution, DEMO/LIVE permissions, strategy profitability and real MT5 acceptance remain **unproven and disabled**. See [G28 merge handoff](G28_OWNER_HANDOFF.md) and [G29 Issue #69](https://github.com/maxqstudio/max-trading-agent/issues/69).

When a G29 PR is opened, verify its *actual exact-head* GitHub-hosted rehearsal and full PR CI before accepting any of the above as a measured result.
