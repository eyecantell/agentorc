---
name: gate-a-merge-on-the-checks-exit-code
description: "`check_cadence.py | head -1 && gh pr merge` gates on head's exit code, not the check's — #398 merged on a FAIL"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 192b2355-2009-4e5e-ab33-69257956a53b
  modified: 2026-09-22T01:33:51.888Z
---

Never pipe `scripts/check_cadence.py` into anything on the line that decides the merge. On
2026-09-21 the anchor ran `python3 scripts/check_cadence.py --pr 398 | head -1 && gh pr merge 398`
and merged a PR whose `ledger` row had **failed**: the `&&` saw `head`'s exit 0. Capture first —
`python3 scripts/check_cadence.py --pr N > out; ec=$?` — and merge only on `ec -eq 0`.

**Why:** the cadence check is a detector, not a gate (cadence §7); the only gate is the session's
own conditional, so the shell construct is the whole rule.

**How to apply:** read the `[PASS]`/`[FAIL]` line *and* branch on the script's exit code. When a
title names a `TD-NNN`, the PR must touch `docs/technical_debt.md` (a board-only edit fails
`ledger`). Related: [[agentorc-td-grind-mechanics]], [[read-inbox-before-merging]].
