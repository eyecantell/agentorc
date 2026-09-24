---
name: summary-table-conflicts-resolve-row-wise
description: A rebase conflict in the ledger's summary table is two edits of different rows, not an append tail — keep-both-sides duplicates rows; also `ao --json` goes before the subcommand
metadata:
  type: feedback
---

A rebase conflict inside `docs/technical_debt.md`'s summary table (two branches each changed one
row in the same hunk) must be resolved row by row — one row per TD id, the current status of
each — never by keeping both sides as the archive-tail and history-tail conflicts are.

**Why:** on 2026-09-23 the keep-both-sides regex left TD-124 and TD-125 with a stale row and a
current row each; Sonnet's fact-check caught it, and a merged copy would have said two things.

**How to apply:** after `git rebase`, `grep -c '^| TD-NNN '` for each id in the hunk; pick the
row whose status matches the entry body. Related: [[agentorc-td-grind-mechanics]].

Also: `ao --json msg person "…"` — the global `--json` sits before the subcommand; after the text
it is read as part of the message and the call errors with *unrecognized arguments*.
