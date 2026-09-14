---
name: live-system-restart-authorized
description: On 2026-09-13 Paul authorized stopping and removing all running ao agents and the whole agentorc system, to be restarted once teams (TD-040) are set up
metadata:
  type: project
---

On 2026-09-13 Paul said all current agents can be stopped/removed and restarted once teams are
set up, and that the whole system (agentorc-agent, UI, `ao-*` sessions) can be stopped and
restarted when ready. This supersedes, for this rebuild only, the standing rule that sessions
never touch live agentorc.

**Why:** the TD-036 migration and the team definitions (TD-040) both need the installed venv
upgraded and the agent restarted; doing it once, with teams, beats migrating five old workers.

**How to apply:** before closing any worker, sweep its worktree for unpushed or uncommitted
work (never strand). Rebuild order: design → loader/teams code → reinstall venv → restart agent
→ start teams. See [[unattended-workers-run-inside-agentorc]].

**Done 2026-09-13:** the seven hand-started workers were killed and forgotten, the installed venv
at `~/.local/share/agentorc-venv` was reinstalled from the checkout, `agentorc-agent` and
`agentorc-ui` were restarted, `~/.agentorc/org.yml` was written, and `ao team start ao-grind`
brought up orchestrator-ao-1 over tdgrind-ao-1 with `controllers` set at create. samscrape's team
is defined and not started (see [[unattended-workers-run-inside-agentorc]] and the board item).
