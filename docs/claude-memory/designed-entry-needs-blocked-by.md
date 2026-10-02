---
name: designed-entry-needs-blocked-by
description: Since 2026-10-01 the cadence check runs ledger.py --check — a design-first entry marked "Pickable: no — designed" fails it unless a "**Blocked by:** TD-NNN" line names its build entries
metadata:
  type: feedback
---

A designed entry's `**Pickable:** no — designed; the build is TD-NNN` is only a field: the check
derives pickable from `**Blocked by:**`, and with no such line it derives *yes* and fails the PR
(`TD-222: its Pickable line says no, the derived pickable is yes`, 2026-10-01). Add
`**Blocked by:** TD-263, TD-264` (the build entries) right after the Pickable line. Run
`python3 scripts/ledger.py --check --since origin/main` before every push of a ledger edit; the
86 standing flags on untouched entries are not yours.

**Why:** TD-248 step 2 (PR #885) turned the check on in `check_cadence.py`'s `ledger` row the same
day; older designed entries predate it and are flagged but unchanged.

**How to apply:** every design PR's TD entry gets the Blocked by line naming its build entries; a
build entry blocked on a board decision writes `**Blocked by:** decision (Paul) — docs/user_attention.md, <date> item`. See [[agentorc-td-grind-mechanics]].
