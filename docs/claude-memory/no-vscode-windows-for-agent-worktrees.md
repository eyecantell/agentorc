---
name: no-vscode-windows-for-agent-worktrees
description: "scripts/open_worktree.sh opens a VS Code window on Paul's desktop; agent-driven worktrees should use plain git worktree add instead"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: b4f95a9e-2c82-4f50-b80a-1a073bd51c7d
  modified: 2026-09-12T13:57:10.999Z
---

`scripts/open_worktree.sh <topic>` opens a new VS Code window (`code -n`) on the worktree, and
there is no way to close it from a script. On 2026-09-12 Paul found stray windows named
`td035` and `briefs-run6` from worktrees this session had already removed.

**Why:** the script is for a person who will work in that window; a session doing a short
branch → PR → merge loop leaves a dead window behind.

**How to apply:** for a worktree only the session will use, run
`git worktree add -b <topic> .claude/worktrees/<topic> main` then
`scripts/hydrate_worktree.sh .claude/worktrees/<topic>` (for settings.local.json), and remove
it with `git worktree remove` when merged. Use open_worktree.sh only when Paul will open it.
