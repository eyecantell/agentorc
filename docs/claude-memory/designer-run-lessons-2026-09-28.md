---
name: designer-run-lessons-2026-09-28
description: Designer run lessons — ao msg's id is .entry.id and a printed warning is not a refusal; run tests/test_ledger.py before a ledger push; re-check a reviewer's "number taken" live; never design the host agent moving the person's checkout
metadata:
  type: feedback
---

Four mistakes from the designer's run of 2026-09-28, each of which cost Paul a message or CI a run.

1. **`ao --json msg` returns the message under `.entry.id`, and a line such as *the person reads the first paragraph: 65 words* is advice, not a refusal.** Reading `.id` gave `null`, the send was repeated, and Paul got the same steer twice.
   **Why:** there is no withdraw; a duplicate can only be explained by another note.
   **How to apply:** write the reply to a file, read `.entry.id` and the exit code, and check `ao inbox --sent` before sending anything again. Keep a message's first paragraph under about 50 words.
2. **Run `tests/test_ledger.py` before pushing a ledger edit**, beside the doc-bound tests. `**Pickable:**` is `yes` or `no — …` and nothing else; a condition goes in Status. An entry that waits on Paul also needs `**Blocked by:** decision (Paul) — ask <id>`, since `ledger.py --pickable` reads only that line.
3. **A reviewer's "TD number taken" is a reading of one moment.** Re-read the other branch before renumbering: PR #716 had already moved its own entries, and renumbering put mine on the ids it then held. See [[open-the-pr-before-writing-its-number]].
4. **Never design the host agent moving the person's main checkout** (a fast-forward, a pull): design §6 *Promote* says the tree is a person's. It goes to Paul as a decision, not into a steer's default. **Paul decided it on 2026-10-01 (TD-222): the host agent fast-forwards when git allows and the anchor session is idle** — so the pull is now to be designed; the lesson that stands is that moving a person's tree is his decision, never a default. Check a default against the design's stated rules before sending the steer, since a withdrawn default is another message.

Also: a design PR stacked on another of the designer's gets no CI until it is retargeted to `main` ([[retarget-a-stacked-pr-with-the-api]]), and a reviewer agent must read through `origin/<branch>` refs because the worktree's branch changes under it ([[designer-run-lessons-2026-09-25]]).
