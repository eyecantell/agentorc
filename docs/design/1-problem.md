## 1. Problem

One person runs many interactive AI coding-agent sessions (today: Claude Code) across several
repos and hosts. Some are unattended workers (samscrape's `tdgrind` supervisor: three Claude
Code workers in tmux, in their own git worktrees, on a night/weekend window, gated by
subscription usage). Some are the person's own conversations, opened in VS Code windows.

Knowing which session is working, which is blocked on a question, and which has quietly died
means cycling through VS Code windows and tmux panes by hand. Lessons from `tdgrind`
(samscrape `scripts/tdgrind.sh`, TD-274) and a stranded-work audit:

- A reboot silently killed the tmux server; a cron tick noticed 10 minutes later.
- OAuth lapses stalled workers twice until a human looked.
- Worker state was detected by grepping the pane (`Yes, I accept`, `[tdgrind] claude exited`,
  a 401 regex). Every one breaks when the CLI changes its wording.
- A worker's end-of-run report existed only in tmux scrollback; snapshots every tick lose up to
  a tick.
- Work that lived only in a session's chat was lost when the session closed.

