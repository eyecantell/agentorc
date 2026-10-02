---
name: designer-run-lessons-2026-09-25
description: "A steer to the person is refused when the person inbox holds 20 unanswered entries (the board is the channel); a fact-check agent in the shared worktree must read explicit refs; a stacked design PR chain and the bound-merges are the next run's first job"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 111132eb-b111-40f2-842c-9895175c01c3
  modified: 2026-09-25T19:15:25.180Z
---

Three things the designer run (and one from a techlead seat) of 2026-09-25 learned (see [[agentorc-td-grind-mechanics]], [[reviewer-prompts-keep-out-of-the-anchor]]):

1. **`ao msg --kind steer person` is refused as full** once the person inbox holds 20 entries unread or unanswered from the caller: *the person is away — write the line on user_attention.md with a Due: date*. The board line is then the channel (one line listing every open steer, its default and the alternative, `Due:` the bound's day), merged as its own small PR so it is on `main` at once; the design PR's Status names that board line instead of a steer id.
2. **A fact-check agent started in this worktree reads the branch that is checked out at the moment it looks** — and the designer switches branches every few minutes. Give it explicit refs (`git diff origin/main...<branch>`, `git show <branch>:<path>`) and tell it to skip `gen.py` unless `rev-parse --abbrev-ref HEAD` is the PR's branch. Two checks went wrong before this was in the prompt.
4. **A techlead's `ao msg --reply-to <id> --source …` is refused the same way** (2026-10-01, at the per-sender cap of 100, `PERSON_SENDER_DEPTH`): the `--source` form copies the person as *answered for you*, so a full person inbox refuses the reply to the asker too. Send the reply without `--source`, opening with `Source: <where>`, and say in the summary that no answered-for-you copy reached the person.
3. **A build entry's TD number can be taken on `main` by a grinder while the design PR is open** (TD-169 was, PR #556's review): before opening a design PR, re-read `origin/main`'s summary table for the next free number, and at merge time rebase and resolve the table row-wise, renumbering the build entry across design.md, the history and the ledger.

**Why:** each cost a fix commit and a second review round; the first also left a steer unsent until the refusal was read.

**How to apply:** at every `ao msg person`, read the reply for `"error"`; prompt every reviewer with refs; `git show origin/main:docs/technical_debt.md | grep -oE '^\| TD-1[0-9]{2} ' | sort | tail -1` before numbering.
