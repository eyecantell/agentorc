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
| TD-004 | Host identity: `hosts.yml` `local` entry complete; ssh transport entries pending (phase 2) | Medium | Partly done |
| TD-005 | `pretrust()` can lose a concurrent Claude Code rewrite of `.claude.json` | Low | Open |
| TD-006 | `.claude.json` location under a custom `CLAUDE_CONFIG_DIR` is assumed, not verified | Low | Open |
| TD-008 | Deny reason input and "allow for this session" (design §10 open questions) | Low | Open |
| TD-025 | `tests/test_cli.py` flakes: a shell session's `idle` can take longer than the 6 s wait | Low | Open |
| TD-026 | Scheduling: start/stop times and window overrides for unattended sessions, editable from the UI | Medium | Open |
| TD-028 | Capabilities and report channels: the `orchestrate` grant with a caller check, `progress`/`findings` with `ao progress`/`ao finding`, the card's report line, role presets | Medium | Open |
| TD-029 | Close from Focus leaves the terminal reconnecting twice a second, printing tmux's "can't find session" until Forget | Medium | Open |
| TD-030 | One name, one session: refuse a live holder, supersede an exited one, drop hidden `-2` suffixes | Medium | Open |
| TD-031 | Show the model in use on the card and in `ao status` (when the adapter can tell) | Low | Open |

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
**Status:** Partly done — `~/.agentorc/hosts.yml` with a `local` entry (name, vscode_host, local) landed 2026-09-06 after the first real session hit the unresolvable hostname. 2026-09-10 (PR #55): the parser moved to `sessionorc.hosts` so the agent can read it too; `volatile` (Herd banner wording), `repos_registry` (registered repos head the New session directory list) and `runs_keep_days` (run-log retention on the tick, design §4.6) landed; the three `AGENTORC_*_HOST` env overrides are gone. Remaining: the ssh transport entries (phase 2) — and with them the `unreachable` card state and the volatile sort slot, which phase 1's single local host cannot produce.
**Location:** `src/sessionorc/hosts.py`, `src/sessionorc/agent.py` (`_prune_runs`), `src/agentorc/ui/app.py` (`host_name()`, `vscode_url()`, `new_form`)

**Note (2026-09-10, session tdgrind-ao-1):** the run-1 question "where do per-host agent settings live" was answered by moving the parser into `sessionorc`: the agent reads its own machine's `hosts.yml` `local` entry, which on a phase 2 session host is that host's own file (design §5).

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

## TD-025: `tests/test_cli.py` flakes: a shell session's `idle` can take longer than the 6 s wait

**Priority:** Low
**Added:** 2026-09-10
**Status:** Open — diagnostics landed (this entry's PR): a timed-out `wait_state` now prints tmux's own pane line (`pane_current_command`, dead flag) next to the record, so the next occurrence says which of the two hypotheses below is true. No code fix yet.
**Location:** `tests/test_cli.py` (`wait_state`, `test_keys_reach_the_pane`, `test_shell_send_tail_status_kill_close`), `tests/conftest.py` (`wait_state`, `pane_line`), `tests/_agent_child.py`

**Why:** Two distinct flakes have been seen in this module. (1) PR #34's CI run asserted `│` in `ao status -v` before the tick had refreshed the record's tail — fixed in PR #42 by waiting for the tail first. (2) 2026-09-10, one failure in 65 local runs of the module (a Sonnet review was running the suite concurrently, three samscrape workers were on the box): `test_keys_reach_the_pane` — `ao-…-keys never reached idle: working []` after 6 s. A 150-iteration probe of the same create → idle path against a child agent on an idle box measured no start over 1 s, so it is load-sensitive, not deterministic. The shell adapter says `idle` only when `pane_current_command` is a shell name; `ao shell` starts the person's login shell (tmux `default-shell`), whose `~/.bashrc` on kmaster runs `lesspipe`, `dircolors`, bash-completion and a sourced secrets file — under load those foreground commands can plausibly hold `working` past the wait, and the empty tail fits (bashrc prints nothing). The other reading, the pane never appearing in the tick's snapshot, would show as `pane=none` in the new diagnostic. Related but separate: PR #52's CI (3.13 job only) saw tmux report a dead pane without its exit status for over 6 s; that assertion was dropped rather than waited on.

**Fix:** once the diagnostic has named the cause — if it is shell start-up: have `tests/_agent_child.py` set `default-command` to `bash --norc` on its private server after `serve()` has started it (the server options `ensure_server` sets in `src/sessionorc/tmux.py` are production code shared with the real server, so not there) or give `wait_state` a longer, load-tolerant timeout for the first `idle` after create; if the pane is missing from the snapshot: that is an agent bug in `_reconcile` / `list_panes`, fix there. Done when 100 consecutive local runs of the module pass with the suite running beside them.

**Related:** PR #34, PR #42 (fix 1), PR #52 (exit-status lag), `tests/README.md` real-environment notes.

## TD-026: Scheduling: start/stop times and window overrides for unattended sessions, editable from the UI

**Priority:** Medium
**Added:** 2026-09-10
**Status:** Open (idea — no code yet)
**Location:** design §6 (policies), §4.5a (New session, card / Focus header), §5 `.agentorc.yml` `unattended:` block

**Why:** Design §6 gives unattended sessions one schedule: the repo's weekly `window` (phase 3, the tdgrind port). That covers the nightly grind and nothing else. Two gaps showed on 2026-09-10, the first day the three samscrape tdgrind workers ran under agentorc instead of the cron supervisor: (1) a worker started by hand with `ao new --unattended` outside the window has **no stopper at all** — nothing wraps it up at 06:00, at a usage cap, or when the token lapses, so "start it now so I can watch it" means "remember to close it yourself"; (2) every deviation from the weekly window is a hand edit to a config file — tdgrind's `~/.tdgrind/config` carries dated shell one-liners (`[ "$(date -u +%s)" -lt <epoch> ] && WINDOW_START=0 …`) for "Paul is out today, run all day", which is a schedule expressed as code, invisible to the Herd and forgotten once it lapses. A scheduler is the general form of both: a session or repo carries *when it may run*, the policy tick enforces it, and the UI shows and edits it.

**Fix:** treat the weekly window as one kind of schedule and add the others on the same policy tick, all visible on the card:

- **Per-session `run_until` / `start_at`** on the New session form and the `ao new` flags (`--until 06:00`, `--until +8h`, `--at 20:00`): a stop time gets wrap-up-then-kill exactly like leaving the window; a start time queues the session (a new `scheduled` state on the Herd, with the time) and the tick launches it. A session started by hand with `--unattended` and no `--until` inherits the repo window's next close, so gap (1) cannot recur.
- **Window overrides with an expiry**, per repo, kept in agent state rather than the checked-in `.agentorc.yml`: "run all day until 2026-09-02 20:00", "pause until Monday" (the PAUSE flag with a date), caps to 99% until the weekly reset. Shown on the repo's row with the expiry; expired overrides fall away by themselves. This replaces the dated one-liners in tdgrind's config.
- **Calendar-shaped schedules** in the `unattended:` block beyond weekday/weekend: a list of `{days, hours}` rules, and a `tz` (the window is in the host's local time today, which is right on one host and ambiguous on two — phase 2 adds the second host).
- **One-off runs**: schedule a *command* session (§4.5 Commands) or a brief at a time — "run the deploy at 02:00", "start a review worker Friday evening" — which is the Commands tab plus a time. This is the dev-cadence `/schedule`-style routine, local and tmux-backed.
- Edits from the UI go through agent RPCs (`set_schedule`, `set_override`), never by writing the repo file; the checked-in block is the default, overrides layer on top. `ao status` shows the next transition ("stops 06:00", "starts Fri 20:00") per session and per repo.

Done when: a hand-started unattended session shows when it will stop and stops then; "run all day today" is one click or one `ao` line with an expiry, not a config edit; the Herd shows a scheduled-but-not-started session; and tdgrind's `config` overrides file has nothing left to express.

**Related:** design §6 run window / usage gate / PAUSE, §7 phase 3 (tdgrind migration — this is the step after it, or the shape the port should take), TD-010 (adopting hand-started sessions faces the same "who stops it" question), samscrape TD-274 (the tdgrind supervisor), samscrape `~/.tdgrind/config` dated overrides of 2026-09-01.

## TD-028: Capabilities and report channels: the `orchestrate` grant with a caller check, `progress`/`findings` with `ao progress`/`ao finding`, the card's report line, role presets

**Priority:** Medium
**Added:** 2026-09-10
**Status:** Open — design landed (§4.8), no code yet
**Location:** `src/sessionorc/agent.py` (session record, acting RPCs, tick), `src/sessionorc/client.py` (caller id on every RPC), `src/agentorc/cli.py` (`progress`, `finding`, `new --role/--lane/--grant`, `roles`, the skill text), `src/agentorc/ui/` (report line, Focus Reports panel, grants chip), `.agentorc.yml` loader (`roles:`, `ledger:`)

**Why:** On 2026-09-10 the Herd showed four unattended workers and could not say which TDs any of them held, had finished, or had filed on the side — the answer lived in scrollback and PR titles — and nothing stopped one worker from `ao kill`-ing another. Design §4.8 (decided the same day, §10) makes the verbs first-class: two ungated report channels (`progress`, `findings`, each entry with a declared / derived / scraped source; declared never overwritten, §9 invariant 10) and one gated grant (`orchestrate`, checked by the agent against the caller's session id on every acting RPC, §9 invariant 11). Roles are presets over them and nothing keys on the preset (§9 invariant 9). Schedule is deliberately not part of any of it (TD-026 carries scheduling).

**Fix:** in order, each its own PR: (1) the CLI sends `AGENTORC_SESSION` as the caller on every RPC and the agent refuses `send`/`keys`/`kill`/`close`/`mode`/`remove`/`create` from a session onto a different session unless its record holds `orchestrate`; `capabilities` on the record, settable at create and by a `set_grants` RPC; (2) `lane`, `progress`, `findings` on the record with `rpc_progress` (claim / done / drop) and `rpc_finding`, `ao progress`, `ao finding`, `--json` included, and the skill text (TD-019's `ao --skill`) gains "declare before the first edit, declare the result before moving on"; (3) the derived source on the tick: worktree branch `tdNNN-*` → claimed, a merged PR from that branch → done, a ledger row that appeared on main from that branch → finding, all marked `derived`; (4) the card report line, the Focus Reports panel with Drop, the grants chip (§4.5a); (5) presets: `ao new --role/--lane/--grant`, `ao roles`, the `roles:` / `ledger:` keys, built-in brief templates for the three presets (the run-1..3 brief in `docs/briefs/tdgrind-ao-1.md` becomes the grinder template with `{lane}` filled in), and the first orchestrator brief. Done when a grinder started with `--lane TD-027,TD-019` shows `TD-027 → #60 · 1/2 done` on its card without anyone reading its pane, and a worker without the grant gets "needs the orchestrate grant" from `ao kill <other>`.

**Related:** design §4.8, §4.5a, §4.7, §5, §6, §9 invariants 9–11, §10 (2026-09-10); TD-019 (done), TD-026, TD-027 (done).

## TD-029: Close from Focus leaves the terminal reconnecting twice a second, printing tmux's "can't find session" until Forget

**Priority:** Medium
**Added:** 2026-09-10
**Status:** Open
**Location:** `src/agentorc/ui/app.py` (`/term/{sid}` websocket), `src/agentorc/ui/static/app.js` (`openTerm` reconnect), `src/sessionorc/agent.py` (`rpc_close`)

**Why:** Paul pressed **Close** on the Focus page of `ao-agentorc-tests-aotest` at 20:32:06 on 2026-09-10. The close succeeded (`POST /api/sessions/…/close` 200; `rpc_close` kills the tmux session, sets `closed`, `pane: false`, `closed_at`). The terminal pane then filled with tmux's `can't find session: ao-agentorc-tests-aotest` repeated, and the UI journal shows **14 accepted `/term/…` websocket attempts in the 16 s** before Paul pressed Forget at 20:32:22 (`remove`, which ended it because `get` then fails and the server closes with 4404). What the code says about that loop: (1) the client resets its backoff to 500 ms in `onopen`, so a connection the server accepts and then closes retries twice a second forever; (2) when the pty path runs and `tmux attach` exits at once, the server ends the handler normally (code 1000), which the client treats as retryable; (3) the guard that should have ended it — "state closed or `pane` false → send *pane is gone* and close 4404" — evidently did not fire on those 14 attempts, or fired and the client did not see 4404. Which of (3)'s halves happened is **not established**: the record was removed before it could be inspected, and the agent log carries nothing for the session. Related design: §4.5 "There is no silent failure path", the exited banner (TD-023), and the events push the Focus page already receives, which knew the session was `closed` from the first delta.

**Fix:** three parts, each independently worth having: (a) the Focus page reacts to a pushed `closed` / `exited pane:false` delta by closing its own terminal websocket and writing the banner line — the push is authoritative and arrives before any reconnect; (b) the server closes 4404 whenever the attach process exits without ever producing pane output, not only when the record already says the pane is gone, so a dead attach is final; (c) the client resets the backoff only after the first byte of pane output, never on open. Then reproduce Paul's sequence (Focus open → Close) in a browser and confirm one "pane is gone" line and no further attempts in the journal. If the reproduction shows the closed check *did* fire, record why the client kept retrying (the 4404 not reaching it) in this entry before archiving.

**Related:** TD-023 (`pane` flag), design §4.5 error rule and §4.5a Focus **Kill** / **Close**, the browser mechanics bullet on reconnect with backoff.

## TD-030: One name, one session: refuse a live holder, supersede an exited one, drop hidden `-2` suffixes

**Priority:** Medium
**Added:** 2026-09-10
**Status:** Open — design landed (§4.1, §9 invariant 12), no code yet
**Location:** `src/sessionorc/naming.py` (`session_id`), `src/sessionorc/agent.py` (`rpc_create`, `_supersede`, `occupants`), `src/agentorc/cli.py` (`new`, `shell`), `src/agentorc/ui/` (New session form, `/api/occupancy`)

**Why:** `naming.session_id` appends `-2`, `-3` on any id collision — live or dead — and the record keeps the person's original name, so on 2026-09-10 the Herd showed `aotest` beside `aotest-2` and two cards named `tdgrind-ao-1` (one exited, one working). Paul: "it is a little confusing to have multiple sessions of the same name". The name is the handle `ao focus`, `ao send` and the filter take, so ambiguity there is a defect. Design §4.1 now says a name identifies one session per scope: a live holder refuses, an exited or closed holder is superseded (as `_supersede` already does for a resumed conversation), and a suffix is only for a tmux id with no record and is then shown.

**Fix:** (1) `rpc_create` looks up the name in scope before choosing an id: live holder → `RpcError("<name> is running — switch to it, or pick another name")` carrying the holder's id; exited/closed holder → kill its dead pane, forget the record, remember its run log on the new record (`previous_run`), reuse the id; (2) `session_id` keeps the suffix path only for ids present in tmux with no record, and the record's `name` then carries the suffix; (3) `shell` gets agent-generated names (`shell`, `shell-2`) shown as the name; (4) `/api/occupancy` (or a sibling) answers the name check for the New session form, which disables Start with **Switch to** for a live holder and shows "replaces the exited `<name>` — run log kept" for a dead one; `ao new` prints the same texts. (5) Every CLI subcommand that takes an id also accepts a bare name: resolved to the one live session of that name in the current repo or directory (cwd), else "ambiguous — <ids>" or "no session named <name> here"; full ids keep working. Until (5) lands, `ao focus` / `ao send` take the full `ao-…` id only. Tests: live refuse, dead supersede with log link, tmux-only collision suffixed and shown, shell auto-name, bare-name resolution (unique, ambiguous, absent). Done when starting `aotest` twice yields one card, and `ao focus aotest` is unambiguous on every host.

**Related:** design §4.1, §4.5a (New session name field), §4.7, §9 invariant 12, §10 (2026-09-10); PR #17 (resume supersedes), TD-023 (`pane` flag), TD-029 (the Close that prompted the report).

## TD-031: Show the model in use on the card and in `ao status` (when the adapter can tell)

**Priority:** Low
**Added:** 2026-09-11
**Status:** Open
**Location:** `src/agentorc/adapters/claude_code/` (hook payload, transcript locator), `src/sessionorc/agent.py` (session record, tick), `src/agentorc/ui/templates/card.html` (`profile_line`), `src/agentorc/cli.py` (`status`)

**Why:** Paul asked for it on 2026-09-11: with grinders on one model and his own sessions on another, the card's profile line (`claude-code`, or `claude-code · <profile>`) does not say which model a session is actually running, and a `/model` switch mid-session changes it without any card noticing. The profile's declared `model` (§4.2a) is an intent, not an observation. What the adapter can observe for Claude Code: every assistant entry in the transcript carries `model` (`claude-fable-5-1`, `sonnet` for a subagent, `<synthetic>` for system entries), so the last top-level assistant entry is the model in use as of the last turn; whether the hook payload carries a model field is unverified (the hook reads none today).

**Fix:** an optional `model` on the session record, filled by the adapter: from the hook payload if Claude Code provides one (check the SessionStart / UserPromptSubmit input schema first), else from the transcript's last top-level assistant entry on the tick (the locator already knows the path; read the tail, not the file). Shown as the third part of the profile line — `claude-code · paul · fable-5-1` — shortened by dropping the `claude-` prefix, and as a column in `ao status -v` and a field in `--json`. Absent for `shell` and for adapters that cannot tell; never guessed from the profile without saying so (`fable-5-1 (profile)` if the declared model is shown before the first observation). Done when a `/model sonnet` in a live session changes the card within a tick.

**Related:** design §4.2a (profiles: tool · account · model), §4.5 card profile line, TD-001 (usage per profile).

