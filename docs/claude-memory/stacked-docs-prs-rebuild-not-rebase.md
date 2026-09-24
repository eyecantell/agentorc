---
name: stacked-docs-prs-rebuild-not-rebase
description: A stack of design PRs whose base moved (main took a TD number) is repaired by rebuilding each branch from its original files, never by union-merging conflict hunks or chaining a renumber map.
metadata:
  type: feedback
---

When main moves under a stack of docs PRs (2026-09-24: the anchor landed TD-126/127 while the designer's six stacked PRs used TD-126..130), repair the stack by **rebuilding**, not rebasing: for each branch, bottom-up, check out its parent, take the branch's *original* files (`git checkout <old tip> -- <files>`), apply one exact id map in a single pass, insert main's new entries, test, commit once, `git branch -f`, push `--force-with-lease`.

**Why:** rebasing each branch and resolving the ledger's conflicts by union duplicated entries and rows and dropped lines; applying the renumber map at every level chained it (126→128→130…). Two hours went to that before the rebuild, which took one pass.

**How to apply:** before opening a design PR, re-check `origin/main`'s highest TD id and the archive's; number new entries from there. If the stack still collides, rebuild as above and post one renumber note per PR rather than editing review comments. Related: [[agentorc-td-grind-mechanics]].
