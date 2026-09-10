# ADR 2026-09-10: herdr stays a reference, not the `sessionorc` substrate

Status: accepted (2026-09-10). Closes the §10 question "Build on, or beside, herdr?" as
option (a). Measured, not argued: TD-014's spike, run 2026-09-10 on kmaster against herdr
0.9.0 (release binary, headless `herdr server --session spike` under a scratch
`XDG_CONFIG_HOME`, two Claude Code 2.1.267 sessions and one shell in a scratch repo, beside the
running agentorc agent). Nothing in `~/.claude` or `~/.agentorc` was touched; the Claude Code
integration was installed into a scratch `CLAUDE_CONFIG_DIR` to read what it does.

## Context

Design §3 records herdr as the closest tool found: persistent panes, multi-host, worktrees, a
socket API, a funded company, a cloud transport coming. Phase 2 (the ssh transport) is the first
thing it would replace, so the question had to be settled by measurement before phase 2 is
built. The pass criterion in TD-014: a permission prompt, a question and a usage cap must be
distinguishable from herdr's events, with the pending text present.

## What was measured

1. **Claude Code state under herdr is scraped, not hook-fed.** herdr's own agents table lists
   Claude Code's state authority as "screen manifest" and its integration role as "session"
   only. Confirmed: `herdr integration install claude` writes one `SessionStart` hook
   (`hooks/herdr-agent-state.sh`, matcher `*`, 10 s timeout) into `settings.json`, and the
   script reports nothing but the session id (`pane.report_agent_session`); it exits 0 outside
   a herdr pane. Only Pi, OMP, Kimi, OpenCode, Kilo and MastraCode get lifecycle authority from
   hooks. This corrects the §3 row of 2026-09-10, which said the Claude Code integration pushes
   state.
2. **Permission, question and usage cap are not distinguishable from the event stream.** The
   `pane.agent_status_changed` payload is `{agent, agent_status, pane_id, workspace_id}`; the
   `message` of `pane.report_agent --state blocked --message …` is accepted and exposed nowhere
   (write-only in the schema). Live results: the Bash permission dialog → `blocked` (manifest
   rule `bash_permission_prompt`); `AskUserQuestion` → `blocked` (rule `live_blocked_form`);
   the first-run "trust this folder?" dialog → `blocked` (same rule). Three synthetic
   usage-limit screens (`Claude usage limit reached…`, `You've hit your usage limit…`, a 429
   retry banner) all classify as `idle` (`agent explain --file`): no `limited` exists. The
   pending text is reachable only by reading the screen (`agent.read --source detection`), and
   the rule id only through `agent.explain` — both are scraping, which design §4.2 already
   allows as a labelled fallback, not as the feed. **The criterion fails.**
3. **An outside source cannot own a Claude pane's state.** `pane.report_agent` from
   `custom:agentorc` was accepted (a plain shell pane became a `blocked` "agent" in
   `agent.list`), but on a live Claude pane the screen detector kept writing over it: a
   reported `working` became `blocked` when the permission dialog appeared, and `release-agent`
   went `unknown` → `idle` from the screen. Full lifecycle authority is reserved for official
   integrations. Display metadata (`pane.report_metadata --token pending=… --token
   kind=permission`, `--state-label blocked="needs you"`) is stored and exposed on
   `pane.get`/`agent.get`, so agentorc *could* publish its richer state into herdr's sidebar —
   as decoration, not as the state.
4. **A herdr server restart kills every pane process.** `herdr server stop` ended both
   `claude` processes (pids gone). Restore brought back the layout and new shells; the pane
   with a reported session reference re-launched `claude --resume <id>` only after a client
   attached. agentorc's tmux server holds the processes across a host-agent restart
   (`tests/test_agent_restart.py`), which is what "laptop closed for an hour; session still
   there" (§7 phase 2) and unattended runs need from an upgrade or a crash of the supervisor.
5. **Panes go through the person's interactive shell.** The pane shell ran `~/.bashrc`, whose
   `cd ~/samscrape` moved every new pane out of its requested cwd, and `agent start` launched
   the first Claude Code session in samscrape (idle, closed at once). Restored panes did the
   same. agentorc runs the adapter's argv directly in the tmux session with `-c <dir>`.
6. **No run log, no exit state, no plain-shell state.** `pane.read` returns the viewport and
   scrollback (10 MB in memory, nothing on disk); Claude Code draws on the alternate screen, so
   its output is not recoverable that way — herdr's own skill file says so. A shell that exits
   closes its pane (`pane_exited`, no code, no `remain-on-exit`); a busy shell has no state at
   all (`agent` absent, status `unknown`). agentorc's `pipe-pane` run logs, `exited` with
   `exit_code`, and the `command` kind have no counterpart.
7. **No cross-host API.** The socket is per server; `--remote` and saved machines are the TUI
   attaching over ssh. An agentorc over herdr would still need its own transport per host, so
   phase 2 is not replaced, only its remote end renamed.
8. **What works well.** `worktree create/list/remove` (branch kept, workspace grouped),
   `agent prompt --wait` with `agent_blocked` refusing input into a dialog, `agent start`
   waiting for readiness, and the screen detector catching the trust dialog — a prompt no
   Claude Code hook reports (§4.2 first-run quirk). Cohabitation is safe: the integration's hook
   is additive to `settings.json` and inert outside herdr, and agentorc's hooks are a per-launch
   `--settings` layer it never sees. herdr does write remote detection manifests under
   `~/.local/state/herdr` regardless of `XDG_CONFIG_HOME`.

## Decision

**(a) Independent.** `sessionorc` stays on tmux; herdr is prior art. Reasons, in order of
weight: the state feed would regress from hook-fed to scraped for the one tool phase 1 is
built on (1, 2); agentorc's hooks and herdr's screen detector would fight over the same pane
(3); a supervisor restart must not kill sessions (4); run logs, exit codes and the `command`
kind are needed by phases 3 and 4 (6); and phase 2 is not saved by it (7).

Option (c), agentorc as herdr plugins, is not taken: herdr's plugin surface is local
executables and TUI panes, which is not the web and phone UI of §4.5, and the state ceiling of
(2) applies to it too. A cheap later step, if herdr users ask, is a publish-only bridge that
mirrors agentorc's state into herdr metadata tokens (3); it needs nothing decided now.

## Consequences

- §3 row corrected; §10 item checked; §7 phase 2 unchanged; §4.5b/§4.5c unchanged — Herdr
  Cloud is the competitor for the `relay` transport, and the answer is the one already
  written there: the layer above the runtime (finer states, supervision, cadence, neutrality).
- Borrow, later: a screen-rule fallback for prompts no hook reports (the trust dialog) as
  `confidence: scraped` (§4.2); the worktree API shape for phase 4's commands.
- TD-014 done. TD-004 (ssh transport) proceeds as designed.

## Evidence

Event log, `session.json`, server log, API schema and the fetched docs from the run are in the
session's scratchpad (not committed); the measurements above quote them. Session 019chcZM.
