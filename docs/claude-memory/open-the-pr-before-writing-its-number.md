---
name: open-the-pr-before-writing-its-number
description: "Write the ledger's PR reference after `gh pr create` returns, never before — a guessed number is wrong and costs a second commit"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c44f5b9c-065b-498a-b66a-6f037b83f500
  modified: 2026-09-20T21:04:05.634Z
---

Write the `PR #N` reference in docs/technical_debt.md (and in any PR body or review comment)
**after** `gh pr create` prints the URL — never from the last number seen. Numbers are shared
across every session and every bot on the repo, so "the next one" is wrong whenever a sibling
opens one first. On 2026-09-20 two entries in one run named #281 and #283 for PRs that came out
as #282 and #286, each costing an extra commit and push.

**Why:** the ledger is what a later session trusts about where a fix landed; a number that points
at someone else's PR is worse than none.

**How to apply:** land the code commit first, run `gh pr create`, then edit the ledger with the
number it returned and push that as its own small commit — or, better, hold the ledger line back
and write it once with the real number. Same rule for `ao progress done <ref> --pr N`.

Related: [[agentorc-td-grind-mechanics]].
