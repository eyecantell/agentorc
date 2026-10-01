---
name: primer-is-a-held-path
description: docs/briefs/techlead-context.md sits under the held path docs/briefs/**, so a design PR that updates the primer waits for the techlead's read — put the primer row in the build entry instead
metadata:
  type: feedback
---

The techlead's primer (`docs/briefs/techlead-context.md`) lives under `docs/briefs/**`, one of the designer's and grinders' `held` paths, so touching it in a design PR turns an unheld docs PR into one that waits for the techlead seat (seen 2026-09-30 on TD-247's PR #845, reverted before the push).

**Why:** the design says the primer updates "in the PR that changes the architecture", but the designer's brief promises its PRs touch no held path; the two collide on every architecture-level design.

**How to apply:** when a design changes who does what, write the primer's new row as a slice of the build entry (the build's briefs slice is held anyway) and keep the design PR to `docs/design.md`, the history, the glossary and the ledger. Related: [[design-help-text-is-bound-to-help-py]].
