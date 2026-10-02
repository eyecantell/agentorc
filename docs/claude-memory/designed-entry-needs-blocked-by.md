---
name: designed-entry-needs-blocked-by
description: Since 2026-10-01 the cadence check runs ledger.py --check — a designed entry needs a "**Blocked by:** TD-NNN" line naming its build entries, or it derives pickable and (where a written Pickable line disagrees) fails the PR
metadata:
  type: feedback
---

Pickable is derived, never written (TD-228 slice 3, PR #898: the ledger carries no
`**Pickable:**` line any more). A designed entry is kept out of the lanes by
`**Blocked by:** TD-263, TD-264` — its build entries — and by nothing else: with no such line the
check derives *yes*. On an old entry that still carried a written `Pickable: no — designed`, that
disagreement failed the PR (`TD-222: its Pickable line says no, the derived pickable is yes`,
2026-10-01). Run `python3 scripts/ledger.py --check --since origin/main` before every push of a
ledger edit; standing flags on untouched entries are not yours.

**Why:** the dev-cadence sync of 2026-10-01 (PR #889, TD-248 step 1) turned the check on in
`check_cadence.py`'s `ledger` row; #885 (step 2) had declared the Owner and Kind words.

**How to apply:** every design PR's TD entry gets the Blocked by line naming its build entries, and
no Pickable line; a build entry blocked on a board decision writes
`**Blocked by:** decision (Paul) — docs/user_attention.md, <date> item`. See [[agentorc-td-grind-mechanics]].
