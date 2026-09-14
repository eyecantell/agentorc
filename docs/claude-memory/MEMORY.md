# Project Memory

<!-- Index of docs/claude-memory/ — one line per memory, pointers only, never content.
     Format:  - [Title](file.md) — one-line hook
     Group under ## headers by topic as the list grows. -->
- [Unattended workers run inside agentorc](unattended-workers-run-inside-agentorc.md) — launch TD-grind workers with `ao new --unattended`, briefs in docs/briefs/
- [TD-grind mechanics in agentorc](agentorc-td-grind-mechanics.md) — ledger template is headed TD-001; never guess a PR number; cadence verdict goes on the comment's first line; archive-tail conflicts
- [Claude Code composer ghost text](claude-code-composer-ghost-text.md) — faint `❯ text` is a suggestion, not an unsubmitted prompt; check `capture-pane -e`
- [Design goals: effective and non-intrusive](design-goals-effective-non-intrusive.md) — mechanical sync direct; repo-judgement work by that repo's sessions; tool wiring in adapters
- [No VS Code windows for agent worktrees](no-vscode-windows-for-agent-worktrees.md) — open_worktree.sh opens `code -n`; use git worktree add for session-only worktrees
- [Live system restart authorized 2026-09-13](live-system-restart-authorized.md) — stop all agents and the system; restart once teams (TD-040) exist; sweep worktrees first
