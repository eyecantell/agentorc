---
name: steer-replies-land-on-the-board
description: Paul's reply to a steer can reach the board (via the anchor's PR), not the designer's inbox — read main's board for each open steer id before acting on its bound
metadata:
  type: feedback
---

On 2026-10-01 Paul answered two of the designer's steers (`m-23539eb2dcc8`, `m-4799b48b919b`) through the Inbox's board Reply; the replies were written on `docs/user_attention.md` and pushed by the anchor in PR #902. The designer's next run found `ao inbox --unread` empty: the mail half of Reply reaches only a live session holding a lease on the reference, and a new run holds none.

**Why:** a steer's bound would otherwise merge the default over a reply that was already given.

**How to apply:** before merging anything at a bound, `git show origin/main:docs/user_attention.md | grep <steer id>` for every open steer; a reply tail ` — Paul, <date>: …` on the line is the answer. Close that line (`- [x]`) in the PR that carries the answer out, and send `ao msg person --outcome done --for <id>` after the merge. See [[designer-run-lessons-2026-09-28]].
