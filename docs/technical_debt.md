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
| TD-010 | Adopt hand-started sessions: VS Code-terminal Claude sessions are invisible to the Herd | Medium | Open |
| TD-015 | Screen-rule manifests per tool with `ao explain`: the scraped second source gets a shape | Medium | Open |
| TD-016 | `send` should confirm the prompt took: a send-and-wait RPC for policies | Medium | Open |
| TD-019 | Ship a skill file for `ao` (`ao --skill`) | Low | Open |

---

<!-- Entry template:

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
**Status:** Open
**Location:** `src/sessionorc/agent.py` (`_reconcile` adoption of `ao-*` panes), `src/agentorc/adapters/claude_code/__init__.py` (`registry_entries`)

**Why:** The phase 1 success test reads "every session Paul has open on kmaster shows the right state". Today the Herd shows sessions agentorc launched plus any hand-started tmux session named `ao-*`. Paul's day-to-day sessions run in VS Code terminals with no tmux at all, so they never appear. Design §4.1 says hand-started sessions enter the Herd by being **adopted** (the Resumable tab's Adopt control; not built yet, and not assigned to a phase in §7), which assumes a tmux session to attach to; a VS Code-terminal session has none.

**Fix:** two halves. (a) Claude Code's own registry (`~/.claude/sessions/<pid>.json`: `status` busy/idle/shell, `name`, `cwd`, `sessionId`) can populate read-only cards for non-tmux sessions — state guessed (`scraped`), no Focus terminal, Allow/Deny only if the person launches them with agentorc's hooks layer (`claude --settings ~/.agentorc/claude-hooks/<profile>.json`, which works outside tmux too since the hook only needs `AGENTORC_SESSION` and `AGENTORC_HOME`). (b) A `ao wrap` / shell alias that starts Claude inside an `ao-*` tmux session from any terminal, so the VS Code habit produces first-class cards. Done when a session started from a VS Code terminal shows the right state within 5 s.

**Related:** design §4.1 adoption, §4.3 registry cross-check, phase 1 success test.

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
