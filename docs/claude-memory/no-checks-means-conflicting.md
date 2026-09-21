---
name: no-checks-means-conflicting
description: "no checks reported" on an agentorc PR after a push usually means the PR conflicts with main — GitHub runs no pull_request CI then; rebase, don't re-push
metadata:
  type: reference
---

`gh pr checks N` answering *no checks reported* after a push is, in this repo, usually a PR that
has gone `CONFLICTING` (`gh pr view N --json mergeable,mergeStateStatus` → `DIRTY`): GitHub
cannot build the merge ref, so the `pull_request` workflow never starts. An empty commit does not
help (seen 2026-09-21, PR #360: a sibling's TD-075 ledger edit landed on the same one-line
**Status**). Rebase onto `origin/main` and force-push. The ledger's long single-line Status
fields make this the common cause — see [[agentorc-td-grind-mechanics]].
