---
name: read-inbox-before-merging
description: A worker reads `ao inbox` right before `gh pr merge` — the anchor reserves src/sessionorc merges and says so by mail mid-run
metadata:
  type: feedback
---

Run `ao inbox --unread` immediately before merging, not only before claiming. Merge rights change mid-run by mail. As of 2026-09-20 the anchor (Paul, acting director) keeps **every PR touching `src/sessionorc`** for itself to merge (also the repo's own briefs and `org.yml`). A worker's sessionorc PR stops at green CI + independent review, and the worker says so to the person.

**Why:** 2026-09-20 grinder-ao-1 merged PR #343 (the doorbell, sessionorc). The reservation had arrived by mail during the build, and it went unread because the `[agentorc] you have N unread` line only prints on `ao` replies, and none ran between claim and merge.

**How to apply:** the last two steps before `gh pr merge` are `ao inbox --unread` and `python3 scripts/check_cadence.py --pr N` (every row PASS/NA — its `review` row reads only a comment whose first line is `cadence-review: SHIP|FIXED|BLOCK · <model> · <code|docs> · <n> findings`, and a comment posted after the merge fails forever: #343 did); if the diff touches `src/sessionorc`, leave the merge to the anchor unless mail says otherwise. See [[unattended-workers-run-inside-agentorc]], [[agentorc-td-grind-mechanics]].
