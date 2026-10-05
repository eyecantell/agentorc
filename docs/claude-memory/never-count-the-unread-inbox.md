---
name: never-count-the-unread-inbox
description: "`ao inbox --unread` marks what it returns as read, so piping it through `jq length` or `tail` swallows mail unseen"
metadata:
  node_type: memory
  type: feedback
  originSessionId: d48c204e-c325-42c7-9dbb-1a69ffe5ce92
  modified: 2026-10-04T03:48:55.489Z
---

Every `ao inbox --unread --json` call marks the entries it returns as read. A call whose output is reduced — `| jq '.entries|length'`, `| tail`, `| head` — consumes mail nobody saw: the next `--unread` says *nothing unread*.

**Why:** 2026-10-03, techlead-ao-1: two "is anything new?" counts after a reply swallowed a designer's `steer` (one-hour bound) and a held-PR `ask` (#980). They were found only because the count said 1 and the next read said 0, by listing `ao inbox --json` (all entries) and filtering for open questions.

**How to apply:** read unread mail once, in full, into a file (`ao inbox --unread --json > f.json`), then filter the file. Before stopping, check for open questions from the full inbox, not the unread one: `ao inbox --json | jq '.entries[] | select((.kind=="ask" or .kind=="steer") and .closed_at==null and .expired_at==null)'`. Related: [[ao-msg-options-before-recipients]] (never pipe a send through `tail`).
