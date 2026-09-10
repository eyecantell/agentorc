---
name: agentorc-td-grind-mechanics
description: "Gotchas for scripted ledger edits and PR merging in agentorc (template headed TD-001, gh merge refused while CI pending, archive-tail conflicts)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 31a6d882-e7d4-40aa-a6a0-3e7bb44d4af9
  modified: 2026-09-10T05:44:00.095Z
---

Learned running the 2026-09-09/10 unattended TD grind in agentorc (session tdgrind-ao-1):

- `docs/technical_debt.md` carries an `<!-- Entry template … -->` block that is itself headed
  `## TD-001: Short title of the problem`. Any regex that finds an entry by `## TD-NNN:` must
  start searching *after* the template's `-->`, or it moves the template and leaves the comment
  unclosed (this happened once, caught in review of PR #44).
- `gh pr merge --squash` is silently refused while CI is still pending (`mergeStateStatus`
  UNSTABLE); `gh pr view N --json state` then still says OPEN. Poll
  `gh pr view N --json statusCheckRollup` for `SUCCESS` first. `gh pr checks` output has a
  space in the check name, so `awk '{print $2}'` is wrong.
- Every TD PR appends to `docs/technical_debt_archive.md`, so consecutive merges conflict on the
  archive tail (and on the ledger when two PRs delete neighbouring entries). Rebase each branch
  right before its merge; keep-both is always the right archive resolution.
- `pkill -f <pattern>` kills the wrapping shell when the pattern appears in the command line
  itself; kill by pid.

**Why:** these cost real time and one wrong PR state on the first run.
**How to apply:** when scripting ledger moves or chaining merges in this repo, reuse these rules;
a helper that skips the template block lives only in a session scratchpad, so rewrite it.
Related: [[unattended-workers-run-inside-agentorc]].
