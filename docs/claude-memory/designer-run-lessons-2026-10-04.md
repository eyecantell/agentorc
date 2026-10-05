---
name: designer-run-lessons-2026-10-04
description: A design PR that waits on a steer — keep its appended lines off the contested tails, expect its build number to be taken, and hold the worktree still while a reviewer reads
metadata:
  type: feedback
---

Three things that cost CI cycles on 2026-10-04 (PRs #981, #1006, #1009, #1022), each a design PR that sat open for a steer while grinders merged:

- **A history line goes above the newest lines of its section, not at the tail.** Every grinder slice appends at the tail of the same `docs/design-history.md` section, so a line added there conflicts with each merge; #1022 took three rebases, five minutes of CI each, until its line was moved above the `TD-309 slice` lines. Same date, so the order is still true.
- **The build entry's number is checked again at the merge.** Main took TD-306 while #1009 waited twelve hours; the entry became TD-312. Before the last push: `git grep "TD-<n>" origin/main -- docs/technical_debt.md docs/technical_debt_archive.md` and the open PRs.
- **While a reviewer agent reads this worktree, the branch stays put.** It reads files from the working tree, so no checkout, rebase or edit until it reports; read for the next entry meanwhile. A second design that touches the same passages is stacked on the first's branch (see [[retarget-a-stacked-pr-with-the-api]]).

**Why:** each is a merge that failed after a green gate, or a number that pointed at someone else's entry.
**How to apply:** when writing the dated history line, the build entry, and when starting the fact-check. Related: [[agentorc-td-grind-mechanics]], [[summary-table-conflicts-resolve-row-wise]].
