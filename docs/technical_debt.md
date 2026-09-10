# Technical Debt

Known issues, compromises, and deferred work. Add an entry any time a problem is identified but not immediately fixed — no exceptions, however small (see [`cadence.md`](cadence.md) §2).

**This file holds open work only.** The summary table below lists exactly the entries that have a body in this file — one row each, no history. When an entry is fully resolved, move the whole body to [`technical_debt_archive.md`](technical_debt_archive.md), delete its summary row, and replace **Fix** with the resolution date plus a pointer to whichever doc/code now carries the lasting content. An entry that is only *partly* resolved stays here, with what shipped and what remains spelled out in its **Status**.

IDs are `TD-` plus a zero-padded three-digit number, assigned in order and never reused. Priority is a field, never part of the ID.

---

## Summary

| ID | Title | Priority | Status |
|----|-------|----------|--------|
| TD-001 | `limited` state: wire adapter `usage()` into the agent tick | Medium | Open |
| TD-002 | Focus composer: Attach / drop / paste upload | Medium | Open |
| TD-003 | Phone layout: narrow Focus with a soft-key row | Medium | Open |
| TD-004 | Host identity: `hosts.yml` (local entry only so far), ssh transport and volatile hosts pending | Medium | Partly done |
| TD-005 | `pretrust()` can lose a concurrent Claude Code rewrite of `.claude.json` | Low | Open |
| TD-006 | `.claude.json` location under a custom `CLAUDE_CONFIG_DIR` is assumed, not verified | Low | Open |
| TD-007 | test_ui mutates `os.environ` for a module-scoped agent | Low | Open |
| TD-008 | Deny reason input and "allow for this session" (design §10 open questions) | Low | Open |
| TD-009 | `subscribe` resets the shared push cache: every new tab re-pushes everything to every tab | Low | Open |
| TD-010 | Adopt hand-started sessions: VS Code-terminal Claude sessions are invisible to the Herd | Medium | Open |
| TD-012 | Resuming a conversation that is still live elsewhere is not refused | Low | Open |
| TD-013 | External-session check reads the default profile's registry only | Low | Open |
| TD-014 | herdr spike: can it be the `sessionorc` substrate under phase 2? (design §10) | High | Done |
| TD-015 | Screen-rule manifests per tool with `ao explain`: the scraped second source gets a shape | Medium | Open |
| TD-016 | `send` should confirm the prompt took: a send-and-wait RPC for policies | Medium | Open |
| TD-017 | Seen-state: "finished while you were away" is not the same as idle | Medium | Open |
| TD-018 | `ao --json` on every subcommand | Low | Open |
| TD-019 | Ship a skill file for `ao` (`ao --skill`) | Low | Open |

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

## TD-001: `limited` state: wire adapter `usage()` into the agent tick

**Priority:** Medium
**Added:** 2026-09-06
**Status:** Open
**Location:** `src/sessionorc/agent.py` (tick), `src/agentorc/adapters/claude_code/__init__.py` (`usage()`)

**Why:** Design §4.2 promises a `limited` state (usage cap hit, reset time shown, Switch profile / Wait). `usage()` exists and parses the OAuth usage endpoint, but nothing calls it: the agent never produces `limited`, and the top bar has no usage figure. Left out of phase 1a–1c to keep each PR reviewable. `usage()` is synchronous network I/O and must run in `asyncio.to_thread`, per profile, on a slow cadence (tdgrind polled per tick; once a minute is plenty), with a fetch failure never gating anything (§6).

**Fix:** per-profile usage cache on the agent (`rpc_usage`), a `limited` transition for interactive sessions whose profile is at 100% of a window (pending text = reset time), `working` again after the reset; `/events` carries a `usage` event for the top bar. Done when a session on a capped profile shows `limited` with its reset time within a minute of the cap.

**Related:** design §4.2, §4.2a, §6 usage gate (phase 3).

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

## TD-007: test_ui mutates `os.environ` for a module-scoped agent

**Priority:** Low
**Added:** 2026-09-06
**Status:** Open
**Location:** `tests/test_ui.py` (`client` fixture over `subprocess_agent` in `tests/conftest.py`)

**Why:** The UI tests need one agent shared across a sync `TestClient`. The env/tick leak was fixed in PR #4 review (module-scoped `MonkeyPatch`, undone at teardown). The agent thread went away in the test-suite consolidation (2026-09-07): the module's agent is now a separate process (`tests/_agent_child.py` on the private tmux socket; `agentorc-agent serve` cannot take one). What remains: starlette's `TestClient` keeps an anyio portal thread alive for the whole `with` block, so `ptyprocess` still calls `forkpty()` in a multi-threaded process and Python still warns it may deadlock the child — the rare-flake exposure is smaller, not gone.

**Fix:** either drive `/term` through a pty helper subprocess in the tests, or accept the warning and filter it in `pyproject.toml` with a comment pointing here.

## TD-008: Deny reason input and "allow for this session" (design §10 open questions)

**Priority:** Low
**Added:** 2026-09-06
**Status:** Open
**Location:** `src/agentorc/ui/templates/card.html`, `focus.html`; design §10

**Why:** The hook decision already carries a `reason` (the API and CLI accept one), but the UI's Deny button sends none. "Allow for this session" is not built. Both are open questions in design §10 for Paul to decide (board item).

**Fix:** after the decision: an optional reason field next to Deny (card, Focus, phone); if approved, a third smaller button that updates the session's permission rules through the hook output, never the default. Done when §10 marks both decided and the controls table lists what exists.

## TD-009: `subscribe` resets the shared push cache: every new tab re-pushes everything to every tab

**Priority:** Low
**Added:** 2026-09-06
**Status:** Open
**Location:** `src/sessionorc/agent.py` (`_handle_conn`, `_last_pushed`)

**Why:** `_last_pushed` is one dict for all subscribers; a new `subscribe` clears it so the newcomer gets a full snapshot, which also re-sends every session to every other connected tab. Harmless at a handful of tabs, wasteful at many; found in the PR #4 review.

**Fix:** per-subscriber last-pushed maps (or send the newcomer a snapshot directly and leave the shared cache alone). Done when opening a second tab produces no traffic on the first.

## TD-010: Adopt hand-started sessions: VS Code-terminal Claude sessions are invisible to the Herd

**Priority:** Medium
**Added:** 2026-09-06
**Status:** Open
**Location:** `src/sessionorc/agent.py` (`_reconcile` adoption of `ao-*` panes), `src/agentorc/adapters/claude_code/__init__.py` (`registry_entries`)

**Why:** The phase 1 success test reads "every session Paul has open on kmaster shows the right state". Today the Herd shows sessions agentorc launched plus any hand-started tmux session named `ao-*`. Paul's day-to-day sessions run in VS Code terminals with no tmux at all, so they never appear. Design §4.1 says hand-started sessions enter the Herd by being **adopted** (the Resumable tab's Adopt control; not built yet, and not assigned to a phase in §7), which assumes a tmux session to attach to; a VS Code-terminal session has none.

**Fix:** two halves. (a) Claude Code's own registry (`~/.claude/sessions/<pid>.json`: `status` busy/idle/shell, `name`, `cwd`, `sessionId`) can populate read-only cards for non-tmux sessions — state guessed (`scraped`), no Focus terminal, Allow/Deny only if the person launches them with agentorc's hooks layer (`claude --settings ~/.agentorc/claude-hooks/<profile>.json`, which works outside tmux too since the hook only needs `AGENTORC_SESSION` and `AGENTORC_HOME`). (b) A `ao wrap` / shell alias that starts Claude inside an `ao-*` tmux session from any terminal, so the VS Code habit produces first-class cards. Done when a session started from a VS Code terminal shows the right state within 5 s.

**Related:** design §4.1 adoption, §4.3 registry cross-check, phase 1 success test.

## TD-012: Resuming a conversation that is still live elsewhere is not refused

**Priority:** Low
**Added:** 2026-09-06
**Status:** Open
**Location:** `src/sessionorc/agent.py` (`rpc_create`, `_supersede`)

**Why:** `create(resume=<id>)` supersedes an *exited* record with that adapter id, but nothing stops a resume of a conversation whose session is still running (in another directory, or hand-started): two tmux sessions would then drive one Claude Code conversation. The anchor rule compares directories, not conversations. Raised in the PR #17 review.

**Fix:** refuse a resume whose adapter id belongs to a live record (state not exited/closed), with the same "already has … ; anchor rule" style error, or offer Switch to instead (Resumable tab).

## TD-013: External-session check reads the default profile's registry only

**Priority:** Low
**Added:** 2026-09-06
**Status:** Open
**Location:** `src/agentorc/adapters/claude_code/__init__.py` (`registry_entries`, `external_sessions`)

**Why:** The anchor rule's view of Claude Code sessions started outside agentorc comes from `~/.claude/sessions/`, i.e. the default profile's config dir. A second profile with its own `CLAUDE_CONFIG_DIR` keeps its registry elsewhere, so a hand-started session under that account is invisible to occupancy and to the create-time refusal. No second profile exists yet; noted in the PR #20 review.

**Fix:** iterate every declared profile's config dir in `external_sessions()`; test with two temp config dirs.

## TD-014: herdr spike: can it be the `sessionorc` substrate under phase 2? (design §10)

**Priority:** High
**Added:** 2026-09-10
**Status:** Done 2026-09-10 — spike run against herdr 0.9.0 on kmaster in a scratch config; the pass criterion failed (Claude Code state is screen-scraped, the status event carries no kind or text, no `limited`); decided (a) independent in [ADR 2026-09-10](decisions/2026-09-10-herdr-spike.md); §10 item checked, §3 row corrected.
**Location:** design §3 (herdr row), §10 "Build on, or beside, herdr?"; `src/sessionorc/` (the layer a substrate would sit under)

**Why:** herdr (https://herdr.dev, Apache-2.0, Herdr, Inc., $6M seed 2026-09-09) already ships the
substrate half of this design — persistent panes, multi-host over ssh, restart recovery, hook-fed
state for six of its 17 integrated CLIs (not Claude Code, which it screen-scrapes — the spike's finding), a worktree API, an event-subscription socket API — and
Herdr Cloud is about to ship the `relay` transport of §4.5b. It does not do the half §1 came
from: states finer than `blocked`, unattended supervision, the anchor rule, Ready to close,
per-repo commands, phone triage. Core contribution is closed (no unsolicited PRs); plugins and
the socket API are the open surface. Phase 2 (ssh transport) is the first thing herdr would
replace, so the decision has to come before phase 2 is built, and it should be decided by a
measurement, not by argument. Analysis in session 019chcZM (2026-09-10); facts in design §3.

**Fix:** a one-to-two-day spike, read-only against herdr, on kmaster in a scratch config:
1. install herdr, add the Claude Code integration, start two Claude Code sessions and a shell
   under it alongside the running agentorc agent (they must not fight over hooks — check what
   its `settings.json` edit does to agentorc's hook entries first; back up `~/.claude`).
2. `events.subscribe` from a small Python client; drive a permission prompt, a question, and
   (if reachable) a usage cap; record what `pane.agent_status_changed` and `blocked --message`
   carry for each. **Pass:** the three are distinguishable and the pending text is present.
3. Check `agent.prompt` / `agent.send_keys` / `worktree.*` against what `rpc_create`, `send`,
   `keys`, `kill` need; note anything `sessionorc` exposes that herdr cannot (pipe-pane run logs,
   `exit-empty off`, session records surviving herdr restarts).
4. Write the result as a decision doc under `docs/decisions/` and close the §10 question one of
   three ways: (a) independent — herdr stays a reference; (b) herdr as a `sessionorc` substrate
   behind a transport, tmux kept for hosts herdr does not fit — then phase 2 becomes that
   transport; (c) herdr plugins only. Update §7 phases and §4.5b/§4.5c to match.
Done when the decision doc is merged and the §10 item is checked.

**Related:** design §3, §4.5b, §4.5c, §7 phase 2, §10; PRs #23, #24; TD-004 (ssh transport, the work this decides).

## TD-015: Screen-rule manifests per tool with `ao explain`: the scraped second source gets a shape

**Priority:** Medium
**Added:** 2026-09-10
**Status:** Open
**Location:** `src/sessionorc/adapters.py` (`classify`), `src/agentorc/adapters/claude_code/__init__.py`, `src/sessionorc/agent.py` (tick)

**Why:** Design §4.2 allows a pane classifier as a labelled fallback, and today the only scraped
verdicts are the shell/command adapters' foreground-process check and the agent's liveness
cross-check; the Claude Code adapter's `classify` returns nothing. The herdr spike ([ADR 2026-09-10](decisions/2026-09-10-herdr-spike.md))
showed what the fallback is for: its screen detector caught the trust dialog, which no Claude Code
hook reports, and its `agent explain` printed the rule that fired, the region it matched and the
fallback reason when nothing did. Three things agentorc wants rest on the same mechanism: the
trust dialog and any future dialog no hook covers, `limited` from the tool's own limit message
before TD-001's usage polling exists, and a `stalled?` that can say *why* it is unsure.

**Fix:** one TOML manifest per tool under the adapter (`rules = [{id, state, region, any/all/not
patterns, priority}]`, versioned), evaluated over the bottom of the pane on the tick; the result
is written with `confidence: scraped` and never overrides a `hook` state that is fresher than the
`STALL_AFTER` window (today a module constant in `agent.py`, per adapter once TD-015 lands; §4.2 "scraped never outranks a fresh hook state"). `ao explain <session>` prints the snapshot, the matched
rule and the evidence; `ao explain --file` classifies a saved fixture so rules can be tested
without a live pane. Done when the trust dialog shows as `needs-you` (scraped) on a Claude Code
session started with a fresh `.claude.json`, and the three usage-limit fixtures from the spike
classify as `limited`.

**Related:** design §4.2, §9 invariant 4; TD-001 (`limited` from the usage endpoint); ADR 2026-09-10.

## TD-016: `send` should confirm the prompt took: a send-and-wait RPC for policies

**Priority:** Medium
**Added:** 2026-09-10
**Status:** Open
**Location:** `src/sessionorc/agent.py` (`rpc_send`), `src/agentorc/cli.py` (`send`)

**Why:** `send` already refuses while a permission or question is pending (the right half of the
rule). It then writes the text and Enter and returns, so a policy that nudges an unattended
worker cannot tell whether the prompt was taken, swallowed by a dialog that appeared in between,
or typed into a pane whose agent had just exited. herdr's `agent prompt --wait` names the two
failure modes worth copying: nothing starts working within a few seconds (`agent_prompt_stalled`),
and the caller's timeout passes before a settled state. Phase 3's supervisor (§6: wrap-up prompt,
then kill) needs exactly this to know the wrap-up request landed.

**Fix:** `send(id, text, wait=False, timeout=None)`: with `wait`, return after the session has
left `idle` (hook `UserPromptSubmit` or a scraped `working`) *and* then reached `idle`,
`needs-you` or `exited`; error `prompt-stalled` if no activity is seen within 5 s; error
`timeout` after `timeout` seconds. `ao send --wait`. Never retry a send on its own (§9 invariant
6 applies to text too). Done when the wrap-up policy in phase 3 uses it and a test covers all
three outcomes with the `hookstub` adapter.

**Related:** design §4.4, §6, §9 invariant 6; ADR 2026-09-10.

## TD-017: Seen-state: "finished while you were away" is not the same as idle

**Priority:** Medium
**Added:** 2026-09-10
**Status:** Open
**Location:** `src/sessionorc/models.py` (`Session`), `src/sessionorc/agent.py`, `src/agentorc/ui/` (Herd card, Focus)

**Why:** A session that went `idle` while nobody was looking is the common phone-triage case, and
today it sorts and looks exactly like one that has been idle all day. herdr keeps `done` (idle,
not yet looked at) apart from `idle` by a server-side seen mark that explicit focus clears and
reads do not. agentorc's Focus view is the natural "seen".

**Fix:** `seen_at` on the record, set when Focus is opened for the session (the terminal bridge
attaches) or the person acts on the card; `since > seen_at` on an `idle` card renders as
"finished 12 m ago · unseen" and sorts above plain `idle`, below `needs-you`. Not a new state —
`idle` stays `idle` in every payload (§4.2 table unchanged). Done when an unattended worker that
finishes overnight is the first idle card in the morning and drops back after one Focus.

**Related:** design §4.2, §4.5 (Herd sort); ADR 2026-09-10.

## TD-018: `ao --json` on every subcommand

**Priority:** Low
**Added:** 2026-09-10
**Status:** Open
**Location:** `src/agentorc/cli.py`

**Why:** `status --json` exists; `new`, `shell`, `send`, `kill`, `close`, `allow`, `deny`, `tail`
print prose, so a script or an agent driving `ao` has to parse "ao-x-y  attach: tmux attach …".
herdr's CLI is JSON-first (most commands print the API response with the ids the next call
needs, and its skill file tells agents to parse ids rather than predict them), which is what made
the spike's automation a matter of `jq`; agentorc's own sessions are the obvious next driver of
`ao`.

**Fix:** a global `--json` that makes every subcommand print the RPC result (or `{"error": …}`
with the same exit codes). Done when `tests/test_cli.py` covers `--json` for each subcommand.

**Related:** design §4.7; TD-019; ADR 2026-09-10.

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
