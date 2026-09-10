# Technical Debt

Known issues, compromises, and deferred work. Add an entry any time a problem is identified but not immediately fixed — no exceptions, however small (see [`cadence.md`](cadence.md) §2).

**This file holds open work only.** The summary table below lists exactly the entries that have a body in this file — one row each, no history. When an entry is fully resolved, move the whole body to [`technical_debt_archive.md`](technical_debt_archive.md), delete its summary row, and replace **Fix** with the resolution date plus a pointer to whichever doc/code now carries the lasting content. An entry that is only *partly* resolved stays here, with what shipped and what remains spelled out in its **Status**.

IDs are `TD-` plus a zero-padded three-digit number, assigned in order and never reused. Priority is a field, never part of the ID.

---

## Summary

| ID | Title | Priority | Status |
|----|-------|----------|--------|
| TD-002 | Focus composer: Attach / drop / paste upload | Medium | Open |
| TD-003 | Phone layout: narrow Focus with a soft-key row | Medium | Open |
| TD-004 | Host identity: `hosts.yml` (local entry only so far), ssh transport and volatile hosts pending | Medium | Partly done |
| TD-005 | `pretrust()` can lose a concurrent Claude Code rewrite of `.claude.json` | Low | Open |
| TD-006 | `.claude.json` location under a custom `CLAUDE_CONFIG_DIR` is assumed, not verified | Low | Open |
| TD-008 | Deny reason input and "allow for this session" (design §10 open questions) | Low | Open |
| TD-010 | Adopt hand-started sessions: VS Code-terminal Claude sessions are invisible to the Herd | Medium | Partly done |
| TD-019 | Ship a skill file for `ao` (`ao --skill`) | Low | Open |
| TD-023 | `exited` is overloaded: a killed session and a natural exit look the same, but only one still has a pane | Low | Open |
| TD-024 | `agentorc-agent serve` logs a pending-task traceback on every SIGTERM stop | Low | Open |

---

<!-- Entry template:

## TD-001: Short title of the problem

**Priority:** High | Medium | Low
**Added:** YYYY-MM-DD
**Status:** Open
**Location:** `path/to/file.py` (function/section)

**Why:** what's wrong, how it was found, and the reasoning — future sessions need the why, not just the symptom.

**Fix:** concrete direction(s), and what would count as done.

**Related:** other TDs, PRs, decision docs.
-->

## TD-002: Focus composer: Attach / drop / paste upload

**Priority:** Medium
**Added:** 2026-09-06
**Status:** Open
**Location:** `src/agentorc/ui/templates/focus.html`, `src/agentorc/ui/app.py`

**Why:** Goal §2.2 says attaching files must be effortless; the mockup has Attach, drop, and paste. The design (§7 phase 2) parks it with the ssh copy path since the plumbing is the same. The composer ships without it.

**Fix:** `POST /api/sessions/<id>/attach` (multipart) → agent `attach` RPC writes to `~/.agentorc/attachments/<session>/`, returns the path, composer inserts it; drop and clipboard paste on desktop; share sheet on the phone. Done when a pasted screenshot lands as a path Claude Code can read.

**Related:** design §4.4 attachment drop, §4.5a Focus composer rows.

## TD-003: Phone layout: narrow Focus with a soft-key row

**Priority:** Medium
**Added:** 2026-09-06
**Status:** Open
**Location:** `src/agentorc/ui/static/app.css` (`@media (max-width: 720px)`), `focus.html`

**Why:** Design §4.5 (phone, phase 2): Herd collapses to cards with Allow / Deny, Focus gets a narrow mode with the terminal full-width and a soft-key row (↑ ↓ ← → Enter Esc Tab 1–9) so questions are still answered through the terminal. The CSS has a bare media query; there is no soft-key row and nothing has been tried on a phone.

**Fix:** soft-key row that sends keys through the terminal websocket (not `send-keys`, invariant 6), collapsed side panel, 44 px tap targets on Allow / Deny; test on a phone over WireGuard or the Cloudflare tunnel (design §4.5) once phase 2 lands it.

**Related:** design §10 "Phone answers for questions" (open).

## TD-004: Host identity: `hosts.yml`, host name and VS Code alias are env vars for now

**Priority:** Medium
**Added:** 2026-09-06
**Status:** Partly done — `~/.agentorc/hosts.yml` with a `local` entry (name, vscode_host, local) landed 2026-09-06 in `agentorc.hosts` after the first real session hit the unresolvable hostname; env vars remain as overrides. Remaining: the ssh transport entries, `volatile`, `repos_registry`, `runs_keep_days`, and dropping the env vars.
**Location:** `src/agentorc/hosts.py`, `src/agentorc/ui/app.py` (`host_name()`, `vscode_url()`)

**Note (2026-09-10, session tdgrind-ao-1):** `runs_keep_days` needs a home the *host agent* reads: `hosts.yml` lives on the UI host and is parsed by `agentorc.hosts`, which `sessionorc` cannot import, so run-log pruning was left unbuilt rather than adding another env var. Decide where per-host agent settings live (a `~/.agentorc/agent.yml` on the session host, say) before building it.

**Why:** Phase 1 is one host, so the UI names it from `gethostname()` (on kmaster that is `kmaster-Standard-PC-i440FX-PIIX-1996`) with `AGENTORC_HOST_NAME` / `AGENTORC_VSCODE_HOST` / `AGENTORC_LOCAL_HOST` env overrides. Design §5 wants `~/.agentorc/hosts.yml` (name, transport, ssh target, volatile, `vscode_host`) on the UI host; that is the phase 2 shape and the env vars should disappear into it.

**Fix:** `hosts.yml` loader; the local host is an entry like any other; drop the env vars. Done when the top bar shows `kmaster` from the file and the VS Code link uses the ssh alias from it.

**Related:** design §4.5 browser mechanics (VS Code links), §5, phase 2.

## TD-005: `pretrust()` can lose a concurrent Claude Code rewrite of `.claude.json`

**Priority:** Low
**Added:** 2026-09-06
**Status:** Open
**Location:** `src/agentorc/adapters/claude_code/__init__.py` (`pretrust`)

**Why:** The first-run trust quirk does a read-modify-write of Claude Code's own `.claude.json` under an flock that only agentorc takes. A running Claude Code session that rewrites the file inside that window loses its write (or we lose our flag, which merely re-shows the dialog). Raised in the PR #3 review; the window is one read plus one write at launch time.

**Fix:** either watch for the dialog in the pane as the fallback and answer it through the terminal channel, or find a supported way to pre-trust a directory (a `--trust`-style flag or a per-project settings key) and drop the file edit. Done when no agentorc code writes `.claude.json`.

**Related:** TD-006.

## TD-006: `.claude.json` location under a custom `CLAUDE_CONFIG_DIR` is assumed, not verified

**Priority:** Low
**Added:** 2026-09-06
**Status:** Open
**Location:** `src/agentorc/adapters/claude_code/__init__.py` (`global_config_file`)

**Why:** For a profile with `config_dir` set, the adapter reads/writes `<config_dir>/.claude.json`. The docs say every `~/.claude` path moves under `CLAUDE_CONFIG_DIR`, but `~/.claude.json` is not under `~/.claude`, and no second-account profile exists yet to test it. If wrong, pretrust silently writes a file Claude Code never reads and the trust dialog appears for that profile.

**Fix:** create a throwaway `CLAUDE_CONFIG_DIR`, run `claude` once, see where `.claude.json` lands, pin it with a test. Done when the second profile (grind) launches without the dialog.

**Related:** TD-005, design §4.2a.

## TD-008: Deny reason input and "allow for this session" (design §10 open questions)

**Priority:** Low
**Added:** 2026-09-06
**Status:** Open
**Location:** `src/agentorc/ui/templates/card.html`, `focus.html`; design §10

**Why:** The hook decision already carries a `reason` (the API and CLI accept one), but the UI's Deny button sends none. "Allow for this session" is not built. Both are open questions in design §10 for Paul to decide (board item).

**Fix:** after the decision: an optional reason field next to Deny (card, Focus, phone); if approved, a third smaller button that updates the session's permission rules through the hook output, never the default. Done when §10 marks both decided and the controls table lists what exists.

## TD-010: Adopt hand-started sessions: VS Code-terminal Claude sessions are invisible to the Herd

**Priority:** Medium
**Added:** 2026-09-06
**Status:** Partly done — half (b) landed 2026-09-10 (PR #46) as `ao new --attach` / `ao shell --attach` and `ao focus <id>` (design §4.7): typed in any terminal instead of `claude`, the session is created by the agent and the terminal attached to it, so it is a first-class card. Half (a), read-only cards for registry-only sessions with no tmux, remains.
**Location:** `src/sessionorc/agent.py` (`_reconcile` adoption of `ao-*` panes), `src/agentorc/adapters/claude_code/__init__.py` (`registry_entries`)

**Why:** The phase 1 success test reads "every session Paul has open on kmaster shows the right state". Today the Herd shows sessions agentorc launched plus any hand-started tmux session named `ao-*`. Paul's day-to-day sessions run in VS Code terminals with no tmux at all, so they never appear. Design §4.1 says hand-started sessions enter the Herd by being **adopted** (the Resumable tab's Adopt control; not built yet, and not assigned to a phase in §7), which assumes a tmux session to attach to; a VS Code-terminal session has none.

**Fix:** two halves. (a) Claude Code's own registry (`~/.claude/sessions/<pid>.json`: `status` busy/idle/shell, `name`, `cwd`, `sessionId`) can populate read-only cards for non-tmux sessions — state guessed (`scraped`), no Focus terminal, Allow/Deny only if the person launches them with agentorc's hooks layer (`claude --settings ~/.agentorc/claude-hooks/<profile>.json`, which works outside tmux too since the hook only needs `AGENTORC_SESSION` and `AGENTORC_HOME`). (b) A `ao wrap` / shell alias that starts Claude inside an `ao-*` tmux session from any terminal, so the VS Code habit produces first-class cards — done as `ao new --attach` (PR #46). Done when a session started from a VS Code terminal shows the right state within 5 s.

**Related:** design §4.1 adoption, §4.3 registry cross-check, phase 1 success test.

## TD-019: Ship a skill file for `ao` (`ao --skill`)

**Priority:** Low
**Added:** 2026-09-10
**Status:** Open
**Location:** `src/agentorc/cli.py`, a new `src/agentorc/skill.md`

**Why:** `herdr --skill` prints the instructions a coding agent needs to drive it safely: check
you are inside a managed session, parse ids from JSON, which commands mutate, what not to do
(never answer another agent's dialog). An agent running inside an agentorc session has the same
needs — `AGENTORC_SESSION` is set, `ao` is on `PATH` — and the adapter-author guide in phase 5
is the moment to write it down once the CLI is stable.

**Fix:** `ao --skill` prints a Markdown skill (front matter + rules); the New session flow can
offer to install it into the repo's `.claude/skills/`. Depends on TD-018. Done in phase 5 with
the adapter-author guide.

**Related:** design §4.7, §7 phase 5; TD-018; ADR 2026-09-10.

## TD-023: `exited` is overloaded: a killed session and a natural exit look the same, but only one still has a pane

**Priority:** Low
**Added:** 2026-09-10
**Status:** Open
**Location:** `src/sessionorc/agent.py` (`rpc_kill`, `_observe`), `src/agentorc/cli.py` (`cmd_focus`)

**Why:** `rpc_kill` destroys the tmux session and sets `exited`; a process that ends on its own also reads `exited`, but `remain-on-exit` keeps its dead pane (exit code, last screen) until `remove`. Nothing on the record says which, so a client cannot tell whether Focus / `ao focus` will find a pane: the CLI learned to run `tmux attach` as a child and report a non-zero exit instead (PR #46 review), and the UI's `/term` bridge finds out the same way. Found in the PR #46 review.

**Fix:** either record the distinction (`killed_at`, or `pane: bool` refreshed by the tick from the pane snapshot) and have Focus / `ao focus` refuse cleanly when the pane is gone, or make `kill` keep the dead pane like a natural exit (kill the process, not the session) so `exited` always has a pane until `remove`. Done when `ao focus` on a killed session prints agentorc's own line without calling tmux.

**Related:** TD-010 (b), PR #46.

## TD-024: `agentorc-agent serve` logs a pending-task traceback on every SIGTERM stop

**Priority:** Low
**Added:** 2026-09-10
**Status:** Open
**Location:** `src/sessionorc/agent.py` (`main`, the `serve` branch)

**Why:** `main` installs `loop.stop` as the SIGINT/SIGTERM handler and runs `agent.serve()` with `run_until_complete`. `loop.stop` halts the loop with the `serve` coroutine still pending, so the process ends with `ERROR asyncio: Task was destroyed but it is pending!` and an `Exception ignored … RuntimeError: Event loop is closed` traceback in the journal. Seen 2026-09-10 in `journalctl --user -u agentorc-agent` when `ao service install` restarted the unit after the promote (pid 681793). Harmless — sessions are re-adopted on the next tick, nothing is lost — but every restart writes an ERROR line that looks like a crash, and any cleanup `serve` would do on the way out (closing the socket, flushing run logs) is skipped.

**Fix:** make the signal handler cancel the serve task instead of stopping the loop (`task = loop.create_task(agent.serve()); handler = task.cancel`), catch `CancelledError` in `main`, and let `serve` run its `finally` block; close the loop with `loop.run_until_complete(loop.shutdown_asyncgens())` before returning. Done when `systemctl --user restart agentorc-agent` leaves no ERROR line in the journal.

**Related:** TD-020 (agent restart), the restart tests from PR #30.

