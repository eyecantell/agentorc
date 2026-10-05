---
name: memory-edits-block-auto-promote
description: "A session's memory write lands uncommitted in the main checkout, and auto promote refuses a dirty checkout — land it by PR"
metadata:
  node_type: memory
  type: project
  originSessionId: 2e9d7f1e-b4aa-416a-ad77-d2a06c54cf7a
  modified: 2026-10-05T12:23:25.096Z
---

The memory folder (`docs/claude-memory/`) is inside the main checkout, so any session's memory write — a designer's, a grinder's — lands there uncommitted. Auto promote (on since 2026-10-04) refuses while the checkout has uncommitted changes (`ao promote status`: *not now: the checkout has N uncommitted changes*); on 2026-10-05 one designer lesson held live 21 commits behind overnight.

**Why:** the promote runs from the checkout, which must be clean and at main's head.

**How to apply:** when `ao promote status` says *uncommitted changes*, `git status` the checkout; memory edits go to a worktree branch → PR (doc-only, a short fact-check) → merge, then `git checkout --` the files in the checkout once the copies match. See [[a-park-is-a-merged-pr]].
