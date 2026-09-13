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
| TD-026 | Scheduling: start/stop times and window overrides for unattended sessions, editable from the UI | Medium | Open |
| TD-028 | Capabilities and report channels: the `orchestrate` grant with a caller check, `progress`/`findings` with `ao progress`/`ao finding`, the card's report line, role presets | Medium | Open |
| TD-029 | Close from Focus leaves the terminal reconnecting twice a second, printing tmux's "can't find session" until Forget | Medium | Open |
| TD-032 | An unattended worker that stood down (Remote Control takeover) sat `idle` for 20 h with its PR unmerged and nothing noticed | Medium | Open |
| TD-035 | Adapters without a session-start hook still run dev-cadence's SessionStart set | Low | Open |
| TD-036 | Orchestrator membership: `controllers` on the target, the gate's second half, `set_controllers`, and the surface | Medium | Open |

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
**Status:** Open — design landed (§4.8); step (1) merged 2026-09-10 (caller on every RPC, the `orchestrate` gate, `capabilities` with `ao new --grant` / `ao grant` / `ao revoke`); steps (2)–(3) merged 2026-09-11 (the two channels, `ao progress` / `ao finding`, `ao new --lane`, the skill's declare rule; the derived source on the tick); step (4) merged 2026-09-12 (the card report line, the Focus Reports panel with Drop, the grants chip); step (5) pending
**Location:** `src/sessionorc/agent.py` (session record, acting RPCs, tick), `src/sessionorc/client.py` (caller id on every RPC), `src/agentorc/cli.py` (`progress`, `finding`, `new --role/--lane/--grant`, `roles`, the skill text), `src/agentorc/ui/` (report line, Focus Reports panel, grants chip), `.agentorc.yml` loader (`roles:`, `ledger:`)

**Why:** On 2026-09-10 the Herd showed four unattended workers and could not say which TDs any of them held, had finished, or had filed on the side — the answer lived in scrollback and PR titles — and nothing stopped one worker from `ao kill`-ing another. Design §4.8 (decided the same day, §10) makes the verbs first-class: two ungated report channels (`progress`, `findings`, each entry with a declared / derived / scraped source; declared never overwritten, §9 invariant 10) and one gated grant (`orchestrate`, checked by the agent against the caller's session id on every acting RPC, §9 invariant 11). Roles are presets over them and nothing keys on the preset (§9 invariant 9). Schedule is deliberately not part of any of it (TD-026 carries scheduling).

**Fix:** in order, each its own PR: (1) ✅ 2026-09-10 — the CLI sends `AGENTORC_SESSION` as the caller on every RPC and the agent refuses `send`/`keys`/`kill`/`close`/`mode`/`remove`/`create` (and `set_grants`) from a session onto a different session unless its record holds `orchestrate`; `capabilities` on the record, settable at create (`ao new --grant orchestrate`) and by a `set_grants` RPC (`ao grant` / `ao revoke`) — `decide` (allow/deny another session's permission) is not gated, as §4.8 does not list it; decide whether it should be when step (4) lands — **still open after step (4) (2026-09-12)**: the UI answers a permission as a person (no caller, so no gate either way), and gating session-to-session `decide` is a change to §4.8's list of acting RPCs, which is Paul's call rather than a step-4 side effect; it rides with step (5) or its own entry (board item, due 2026-09-19); (2) ✅ 2026-09-11 — `lane`, `progress`, `findings` on the record with `rpc_progress` (claim / done / drop) and `rpc_finding`, `ao progress`, `ao finding`, `--json` included, and the skill text (TD-019's `ao --skill`) gains "declare before the first edit, declare the result before moving on"; both RPCs are ungated per §4.8 and upsert by canonical reference, invariant 10 is enforced in `Session.report_progress` / `report_finding` (a refused write comes back as `refused`, not an error), and `ao new --lane` came along with the field rather than waiting for step (5), which keeps only `--role` / `ao roles` / the config keys; (3) ✅ 2026-09-11 — the derived source on the tick: worktree branch `tdNNN-*` → claimed, a merged PR from that branch → done, a ledger row that appeared on main from that branch → finding, all marked `derived` (`sessionorc.reports`, five-minute cadence off the branch `_refresh_git` already read; the merge state from `gh pr list`, the ledger rows from the squash-merge commit found by its `(#N)` subject, so a merged PR's findings survive the head branch's deletion). The ledger path is the `docs/technical_debt.md` default until §5's `ledger:` key lands with step (5); (4) ✅ 2026-09-12 — the card report line (`report_line`, the same text `ao status -v` prints, dashed when the entry it leads with was derived), the Focus **Reports** panel listing both channels with **Drop** on a claimed item (which lands as a *declaration*, so the tick cannot undo it), and the header **grants** chip (§4.5a; `/api/sessions/<id>/drop` and `/grants` over the `progress` and `set_grants` RPCs); (5) presets: `ao new --role/--lane/--grant`, `ao roles`, the `roles:` / `ledger:` keys, built-in brief templates for the three presets (the run-1..3 brief in `docs/briefs/tdgrind-ao-1.md` becomes the grinder template with `{lane}` filled in), and the first orchestrator brief (`docs/briefs/orchestrator-ao-1.md`; its per-PR verification is the dev-cadence check `scripts/check_cadence.py`, decided 2026-09-11 — §4.8 orchestrator row). Landed alongside, 2026-09-11: the launch layer carries dev-cadence's SessionStart runner line when the session directory's settings do not (§4.2), so the relay's skip rule is simply "started after `landed`". Done when a grinder started with `--lane TD-027,TD-019` shows `TD-027 → #60 · 1/2 done` on its card without anyone reading its pane, and a worker without the grant gets "needs the orchestrate grant" from `ao kill <other>`.

**Related:** design §4.8, §4.5a, §4.7, §5, §6, §9 invariants 9–11, §10 (2026-09-10); TD-019 (done), TD-026, TD-027 (done).

## TD-029: Close from Focus leaves the terminal reconnecting twice a second, printing tmux's "can't find session" until Forget

**Priority:** Medium
**Added:** 2026-09-10
**Status:** Open — all three hardenings merged 2026-09-11 (PR #71), and the loop is reproduced and explained in a test; what remains is one live Focus → Close in a browser, because the client half (a) and (c) is JavaScript with no test harness (docs/user_attention.md)
**Location:** `src/agentorc/ui/app.py` (`/term/{sid}` websocket), `src/agentorc/ui/static/app.js` (`openTerm` reconnect), `src/sessionorc/agent.py` (`rpc_close`)

**Why:** Paul pressed **Close** on the Focus page of `ao-agentorc-tests-aotest` at 20:32:06 on 2026-09-10. The close succeeded (`POST /api/sessions/…/close` 200; `rpc_close` kills the tmux session, sets `closed`, `pane: false`, `closed_at`). The terminal pane then filled with tmux's `can't find session: ao-agentorc-tests-aotest` repeated, and the UI journal shows **14 accepted `/term/…` websocket attempts in the 16 s** before Paul pressed Forget at 20:32:22 (`remove`, which ended it because `get` then fails and the server closes with 4404). What the code says about that loop: (1) the client resets its backoff to 500 ms in `onopen`, so a connection the server accepts and then closes retries twice a second forever; (2) when the pty path runs and `tmux attach` exits at once, the server ends the handler normally (code 1000), which the client treats as retryable; (3) the guard that should have ended it — "state closed or `pane` false → send *pane is gone* and close 4404" — evidently did not fire on those 14 attempts, or fired and the client did not see 4404. Which of (3)'s halves happened is **not established**: the record was removed before it could be inspected, and the agent log carries nothing for the session. Related design: §4.5 "There is no silent failure path", the exited banner (TD-023), and the events push the Focus page already receives, which knew the session was `closed` from the first delta.

**Fix:** three parts, each independently worth having: (a) ✅ the Focus page reacts to a pushed `closed` / `exited pane:false` delta by closing its own terminal websocket and writing the banner line — the push is authoritative and arrives before any reconnect (and `rpc_kill` / `rpc_close` now announce it as they return, instead of leaving it to the next tick); (b) ✅ the server closes 4404 whenever the attach process exits non-zero or without ever painting a screen, not only when the record already says the pane is gone, so a dead attach is final; (c) ✅ the client resets the backoff on the first byte of pane output, never on open. (Considered and dropped: "the attach is not closed when the websocket goes" — `pump`'s own `finally` has always closed the pty, so there was no leaked `tmux attach` to fix; the review caught the claim before it reached the archive.)

**Which candidate fired (2026-09-11).** Established by test, not by argument. Candidate (3) is **disproven**: when the record says `closed` or `pane: false` the guard does fire and closes 4404 — `test_closed_session_terminal_is_final_and_occupancy_endpoint` has asserted that since TD-023. What actually happened is **(2) driven by (1)**: a record that still claimed a pane whose tmux session was gone let `/term/` run `tmux attach`, which printed tmux's `can't find session` *into the pty* (so "produced no output" would not have caught it either) and exited, and the handler ended the websocket **normally — code 1000**, which the client treats as retryable, with its backoff reset to 500 ms on every open. `test_a_dead_attach_is_final` reproduces exactly that (it asserted `1000` before the fix) and now asserts 4404. Paul's record was `closed` by his Close, so the remaining question is only how his record still claimed a pane at those 14 attempts — the likeliest answer is that the loop was already running *before* the Close (the pane had gone with a tmux server restart) and Forget, not the Close, is what ended it. That part stays unproven: the record was removed before it could be inspected.

**Remaining:** one live Focus → Close in a browser — (a) and (c) are JavaScript, which this repo has no harness for — confirming one banner line and no further `/term/` attempts in the UI journal.

**Related:** TD-023 (`pane` flag), design §4.5 error rule and §4.5a Focus **Kill** / **Close**, the browser mechanics bullet on reconnect with backoff.

## TD-032: An unattended worker that stood down (Remote Control takeover) sat `idle` for 20 h with its PR unmerged and nothing noticed

**Priority:** Medium
**Added:** 2026-09-11
**Status:** Open
**Location:** design §6 (stall policy, phase 3), `src/sessionorc/agent.py` (tick), `src/agentorc/adapters/claude_code/` (screen rules, TD-015 manifest)

**Why:** Run 4 of `tdgrind-ao-1` (`ao-agentorc-tdgrind-ao-1-2`, unattended) opened PR #63 at 20:36 on 2026-09-10 and at 20:40 printed "Waiting for CI on the updated head of PR #63 before merging", then Claude Code printed "Remote Control disconnected — another connection took over this session … this device is standing down (code 4090)" and the pane's footer later showed `/rc failed`. From then until 16:47 on 2026-09-11 the session sat `idle` on the Herd — 20 hours, CI long green, PR unmerged, run unfinished — and nothing flagged it: `idle` is the normal state for a session waiting on a person (§4.2), so an unattended session that has *stopped driving itself* is indistinguishable from one resting between turns. A single `ao send --wait` (in short: CI is green, merge it, exit) woke it; it merged #63 and exited within 30 s, so the process was fine and only its loop had ended. Two gaps: (1) no policy for an unattended session idle past a threshold with its lane unfinished (§6 has a *stall* rule for `working` with no output, not for `idle` with open work — an orchestrator session, §4.8, would have caught it on its first tick); (2) the stand-down banner is a screen state the classifier does not know (TD-015 manifest): a Claude Code pane that says "standing down" is not `idle`, it is `detached`, and the card should say so.

**Fix:** (a) a TD-015 rule for the Remote Control stand-down / `/rc failed` screen → a labelled `scraped` state (`detached`, or `idle` with pending text "stood down: another device took over") so the card reads differently from a resting worker; (b) in §6, an **idle-with-open-work** rule for `unattended` sessions: idle past `idle_after` (default 15 min) with a `progress` entry still `claimed` or a PR open from the session's branch → flag `stalled?` and nudge once with a fixed prompt through `send --wait`, then wrap up — the same escalation as the working-stall rule; until §6 lands, this is the orchestrator brief's first rule; (c) record in the design (§4.2) that Remote Control takeovers happen to worker panes and what they look like. Done when a worker that stands down is flagged within `idle_after` and the nudge lands without a person.

**Related:** design §4.2 (idle is not an alert), §6 stall, §4.8 orchestrator, TD-015, TD-026 (no stopper for hand-started unattended sessions), TD-028 step (1) (PR #63, the run this happened to).

## TD-035: Adapters without a session-start hook still run dev-cadence's SessionStart set

**Priority:** Low
**Added:** 2026-09-12
**Status:** Open — design question; no second adapter exists yet
**Location:** `src/agentorc/adapters/<tool>/` (a future Gemini CLI / Codex CLI adapter), `src/agentorc/adapters/claude_code/__init__.py` (`CADENCE_HOOK_LINE`, the pattern to generalise), design §4.2, §4.3

**Why:** dev-cadence's per-repo conventions are tool-neutral in their rules and scripts (cadence.md, the ledger and board, `check_cadence.py`, `cadence_hooks.sh`, the pre-push hook, the review-evidence PR comment) but their *wiring* is Claude Code's: the runner line lives in `.claude/settings.json` and in this adapter's launch layer, the runner resolves the repo from `CLAUDE_PROJECT_DIR`, and `check_anchor.py` reads the live-session registry under `~/.claude/sessions`. A repo worked by another tool gets the rules and none of the delivery: no attention board at session start, no cadence-changes entry, no anchor warning. Decided 2026-09-12 (with Paul): tool-specific wiring belongs in the harness adapter, not in the repo and not with a per-repo agent — the repo does not know which tool will work it; the harness does.

**Fix:** (1) every adapter's launch runs `scripts/cadence_hooks.sh --session-start` for the session directory when the script is executable there: through the tool's own start hook where it has one (Gemini CLI hooks, design §4.3 table), otherwise by running the runner at launch with the hook payload it would have received and prepending its stdout to the first prompt — non-intrusive, nothing needed from the repo; (2) the runner takes the repo root from an argument or `PWD` as well as `CLAUDE_PROJECT_DIR` (dev-cadence change; it already falls back to `git rev-parse --show-toplevel`); (3) the anchor check needs an equivalent of the live-session registry for the other tool, or agentorc's own session records as the source when the session was launched by it — decide when the second adapter lands. Done when a session launched through a second adapter in a dev-cadence consumer prints the due-items line and the unseen cadence-changes entries at its start, and a hand-started session of that tool is untouched.

**Related:** design §4.2 (the launch layer carries the line, 2026-09-11), §4.3 (adapter contract, the hooks table), §4.8 (relay); dev-cadence cadence §3 (one SessionStart line; who makes the change, 2026-09-12); TD-028 (orchestrator brief).

## TD-036: Orchestrator membership: `controllers` on the target, the gate's second half, `set_controllers`, and the surface

**Priority:** Medium
**Added:** 2026-09-12
**Status:** Open — design written 2026-09-12 (§4.8, §4.5a, §9 invariant 11, §10, [ADR](decisions/2026-09-12-orchestrator-membership-prior-art.md)); **not approved to implement** — the §10 question is open and the go/no-go is Paul's
**Location:** `src/sessionorc/agent.py` (`_gate`, the session record, `create`, a new `set_controllers` RPC), `src/agentorc/cli.py` (`ao new --controller`, `ao control`, `ao status -v`, the skill text), `src/agentorc/ui/` (card chip, Focus controllers chip, orchestrator Members list, New session picker), the `.agentorc.yml` loader (`controllers:`)

**Why:** `orchestrate` is one bit on the *caller* and `_gate` never looks at the target, so any session holding the grant may act on every session on the host. That held while there was one orchestrator; several are in sight at once (`guardians`, a ui orc and a backend orc in one large repo, a read-only status orc, an orchestrator-of-orchestrators), and each would reach every session. Per-repo boundaries were rejected: the person says explicitly which sessions each orchestrator controls. The membership lives on the *target* so the gate is one lookup, nothing has to be kept in step, it survives a restart and dies with the record. The prior-art survey added the pieces the design lacked — a bounded restart ceiling, `one_for_one` scope, no propagation of an orchestrator's exit to its workers, explicit re-attachment rather than automatic reparenting.

**Fix:** in order, each its own PR: (1) `controllers: [session ids]` on the session record, persisted; `_gate` passes an acting RPC from a session only when the caller holds `orchestrate` **and** is in the target's list (empty = nobody), both read live per call, with tests for the refusal, the several-controllers case, the empty default, and that a person (no caller) is unaffected; `create` adds the creator and refuses grants the creator does not hold; a `set_controllers` RPC gated on the target like `set_grants`. (2) CLI: `ao new --controller <id>…`, `ao control <orc> add|remove <session>…`, both directions in `ao status -v`, and the `ao --skill` text gains the membership half of the gate. (3) UI: the card's *under `<orc>`* chip, the Focus controllers chip, the orchestrator's Members list, the New session controller picker (§4.5a). (4) `.agentorc.yml` `controllers:` per repo and per preset (§5), and the one line `ao new` prints when a session starts with no controller. (5) briefs: orchestrator-ao-1 run 3, the `guardians` orchestrator, and the orc-of-orcs — each stating its members, the restart ceiling (N per period, then escalate to the board), `one_for_one` scope, and "supervisors only supervise". (6) migration: the five running workers have no list, so promoting orchestrator-ao-1 must attach it to them (`ao control orchestrator-ao-1 add …`) **in the same step**, or the promotion silently takes its reach away. Done when two orchestrators run on one host, each acts only on its own members, `ao kill` across the boundary is refused by the gate rather than by a brief, and a worker card says who is over it.

**Related:** design §4.8, §4.5a, §5, §9 invariant 11, §10 (2026-09-12); [ADR 2026-09-12](decisions/2026-09-12-orchestrator-membership-prior-art.md); TD-028 (the grant this builds on), TD-026.
