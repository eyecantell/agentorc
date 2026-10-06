---
name: designer-run-lessons-2026-10-05
description: A steer's first paragraph is read cold and warned past ~100 words; the manager seat may hold no session, so the brief's done-mail to it can fail; a fact-check by commit ref frees the worktree
metadata:
  type: feedback
---

Three things from the designer run of 2026-10-05 (PRs #1141, #1142):

- **`ao msg` warns when the first paragraph is long** — *the person reads the first paragraph: 110 words — say what it is about, what you decided or ask, what they must do*. A warning, the mail still goes (its `entry.id` follows the line), but the line breaks `--json` parsing: read the reply with `tail -n +2 | jq`, and keep the first paragraph to three plain sentences, the reading after the blank line.
- **The manager may be a seat with no session.** `ao msg ao-agentorc-manager-ao-1 "done: …"` answered *no session ao-agentorc-manager-ao-1* with the manager on call (design §6 rules 10–12, TD-247): the brief's done-mail to the manager is skipped, not retried, and `ao progress done` is what the seat reads.
- **A fact-check given a commit ref frees the worktree.** The reviewer read `git diff <base>...<sha>` and `git show <sha>:<path>` instead of the working tree, so the branch could be switched to the next entry's while it read (the 2026-10-04 lesson's *hold the worktree still* applies only when the reviewer reads files from the tree).

**Why:** each cost a retry or a parse error, or would have held the run idle.
**How to apply:** when sending a steer or ask, when the brief's done-mail is refused, and when starting a reviewer. Related: [[designer-run-lessons-2026-10-04]], [[ao-msg-options-before-recipients]], [[reviewer-prompts-keep-out-of-the-anchor]].
