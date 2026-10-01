---
name: delete-only-your-own-branches-by-name
description: "Branch cleanup in a shared repo deletes only branches you made, each by exact name — never a loop over `git branch`"
metadata:
  node_type: memory
  type: feedback
  originSessionId: c944f49a-c899-46b3-90cf-9685bfbd663c
  modified: 2026-09-27T20:19:24.184Z
---

Branches are shared by every worktree of the repo. On 2026-09-27 grinder-ao-1 ran a cleanup over the whole `git branch` list and deleted other sessions' local branches; their unpushed commits had to be pinned under `refs/recovered/*` and put on the board for Paul.

**Why:** `git branch` lists every session's branches, not yours; a squash-merged-looking branch may hold a sibling's unpushed work.

**How to apply:** after `gh pr view N` shows MERGED, `git branch -D <that PR's exact head name>` — one name at a time, only branches this session created. Never pipe `git branch` into a delete. See [[agentorc-td-grind-mechanics]].
