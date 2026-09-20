---
name: pdm-run-fmt-sweeps-other-sessions-files
description: In an agentorc worktree, `pdm run fmt` reformats the whole repo — including files another session just merged — and `git add -A` then sweeps them into your commit.
metadata:
  type: feedback
---

`pdm run fmt` in a worktree runs ruff format over the **whole repo**, not over what you changed. Files a sibling merged minutes ago get reformatted, and a `git add -A` puts them in your commit under your PR's message and past your PR's review. Seen 2026-09-20 on PR #287: a `fmt` run mid-task reformatted `src/sessionorc/agent.py`, `gitinfo.py` and three test files from `tdgrind-ao-1`'s #269 and #282; it surfaced only on the rebase, from `git show --stat`.

**Why:** it is TD-061's failure from the other side — a change riding a PR whose author did not write it and whose review did not cover it — and in a two-worker team it also breaks the lane split the briefs depend on.

**How to apply:** don't run `pdm run fmt` bare; `pdm run lint` is what the cadence needs and it does not rewrite files. If you do run it, `git show --stat HEAD` before pushing and `git checkout origin/main -- <paths>` anything outside your lane, then `git commit --amend`. Never `git add -A` after a formatter. Related: [[check-in-flight-before-a-td-step]], [[agentorc-td-grind-mechanics]].
