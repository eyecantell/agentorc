---
name: dates-and-pr-numbers-are-local-and-real
description: In agentorc's ledger and design, dates are the machine's LOCAL date and a PR number is only written after the PR exists — both got me twice in one run.
metadata:
  type: feedback
---

Two things a ledger or design edit gets wrong silently, and a reviewer has to catch:

**The date is the local date.** kmaster is MDT (UTC−6), so a session working past 18:00 local sees UTC timestamps a day ahead — `git log --date=iso` and `date` are the truth, the `Z` stamps in records and mail are not. I wrote `2026-09-21` into TD-029 and TD-083 on the evening of 2026-09-20 because the mail and commit *times* said so.

**A PR number is written after the PR is opened, never before.** I guessed `#307` in a ledger entry (it was a sibling's open PR, not mine) and `#319` in TD-029 (someone else's board PR). The habit that works: commit the change, open the PR, then add the number in a second commit.

**Why:** both send a future reader to the wrong place, and neither fails a test — the fact-check is the only thing between them and main. [[agentorc-td-grind-mechanics]] already says *never guess a PR number*; this adds that knowing the rule is not enough, and that the date has the same shape of failure.

**How to apply:** before any ledger or design edit that carries a date, run `date`. Before any that carries `#N`, have the PR URL in hand. Related: [[pdm-run-fmt-sweeps-other-sessions-files]].
