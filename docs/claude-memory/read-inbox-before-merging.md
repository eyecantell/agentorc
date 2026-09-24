---
name: read-inbox-before-merging
description: A worker reads `ao inbox` right before `gh pr merge` — the anchor reserves src/sessionorc merges and says so by mail mid-run
metadata:
  type: feedback
---

Run `ao inbox --unread` immediately before merging, not only before claiming. Merge rights change mid-run by mail. From 2026-09-20 to 2026-09-23 the anchor kept **every PR touching `src/sessionorc`** (and `docs/briefs/`, `org.yml`) for itself to merge. Since 2026-09-23 (TD-093 slice 4, PR #512) that hand is the **techlead seat's**: the grinder role in `org.yml` carries `review: {reader: techlead, held: [src/sessionorc/**, docs/briefs/**]}`, `ao pr held N` says whether a PR waits, and a held PR goes to the seat as `ao msg --kind ask --pr N`. The anchor still merges its own held-path PRs (it is interactive: the seat's reply is a recommendation).

**Why:** 2026-09-20 grinder-ao-1 merged PR #343 (the doorbell, sessionorc). The reservation had arrived by mail during the build, and it went unread because the `[agentorc] you have N unread` line only prints on `ao` replies, and none ran between claim and merge.

**How to apply:** the last two steps before `gh pr merge` are `ao inbox --unread` and `python3 scripts/check_cadence.py --pr N` (every row PASS/NA — its `review` row reads only a comment whose first line is `cadence-review: SHIP|FIXED|BLOCK · <model> · <code|docs> · <n> findings`, and a comment posted after the merge fails forever: #343 did); if `ao pr held N` says held, the merge is the seat's (an unattended author) or the person's after the seat's recommendation (an interactive one). See [[unattended-workers-run-inside-agentorc]], [[agentorc-td-grind-mechanics]].
