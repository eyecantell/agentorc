# Technical Debt

Known issues, compromises, and deferred work. Add an entry any time a problem is identified but not immediately fixed — no exceptions, however small (see [`cadence.md`](cadence.md) §2).

**This file holds open work only.** The summary table below lists exactly the entries that have a body in this file — one row each, no history. When an entry is fully resolved, move the whole body to [`technical_debt_archive.md`](technical_debt_archive.md), delete its summary row, and replace **Fix** with the resolution date plus a pointer to whichever doc/code now carries the lasting content. An entry that is only *partly* resolved stays here, with what shipped and what remains spelled out in its **Status**.

IDs are `TD-` plus a zero-padded three-digit number, assigned in order and never reused. Priority is a field, never part of the ID.

Two header lines follow **Added:** so a worker can filter the file instead of re-reading it (TD-118): **Owner** — who moves the entry next (`designer`, `grinder`, `paul`, `dev-cadence`; the anchor seat is retired, design §4.9b *No anchor seat*); **Kind** — what the next step is (`build`, `design-first`, `live-check`, `evaluation`, `decision`). Every open entry carries them; keep them true when the Status changes. A third, **Waits**, is written only on an entry that is pickable and still waits on something no `Blocked by:` item names (TD-715, design §4.4 *An entry says what it waits for*): `until <date>` (*`**Waits:** until 2026-10-16 — CI runs on or after 10-16`*) or `event` (*`**Waits:** event — the next cadence-sync PR`*). What waits on the person is `Blocked by: decision (paul) — <what they must do>`, or `Owner: paul`, never a `Waits:` line. **Pickable is derived, never written** (cadence §2.4, TD-228): an entry is pickable while its `**Blocked by:**` names nothing still open and no decision, so what waits on another entry or on a person's word says so there, with its pointer; what nothing blocks and a grinder still cannot build — a live look, an evaluation, the person's edit — is said by its **Kind** or its **Owner**, and the Status says what is next. A `live-check` is followed on its line by its build's last merged PR — `**Kind:** live-check #<n>`, written by the PR that makes the entry a live check — and once that PR is live (`ao promote status`) the check is in a `free-pick` lane whose owner it has (design §4.9b, TD-320); cadence's script reads the word alone. A grinder's pick list is therefore `ao repo` (its pickable entries, a live check whose build is live among them), and `python3 scripts/ledger.py --list --pickable yes --owner grinder --kind build` lists the builds alone; the designer's is `--owner designer --kind design-first`. The line below is the one list of the **Owner** and **Kind** words (cadence §2.12): `scripts/ledger.py` flags a value outside it, and `tests/test_ledger.py` reads its sets from it and checks the two lines' shape, so a new word is added there and nowhere else.

Fields: Owner = designer | grinder | paul | dev-cadence; Kind = build | design-first | live-check | evaluation | decision; Waits = until | event

---

## Summary

| ID | Title | Priority | Status |
|----|-------|----------|--------|
| TD-003 | Phone layout: narrow Focus with a soft-key row | Medium | Open |
| TD-005 | `pretrust()` can lose a concurrent Claude Code rewrite of `.claude.json` | Low | Open |
| TD-026 | Scheduling: start/stop times and window overrides for unattended sessions, editable from the UI | Medium | Partly done — the reset start (TD-133) and the start time (TD-152) designed; archives with them |
| TD-035 | Adapters without a session-start hook still run dev-cadence's SessionStart set | Low | Open — designed; builds with the second adapter (TD-112) |
| TD-052 | Messages between sessions: the mailbox, the graph that gates it, the bounds, and the surfaces — build design §4.10 | High | Partly done — steps 1–8 done (5 was a measurement); the second-adapter step and the live check remain |
| TD-060 | The rename to ShiftLead's loose ends: TD-552's text sweep and TD-553's fallback; archives when both are done | Medium | Step 2 live 2026-10-11 (#1505, cutover done); repo renamed and moved to the ShiftLead org; the live briefs swept; the dev venvs rebuilt; what is left is split out (TD-552, TD-553, and each repo's own entry) |
| TD-071 | Org page review 2026-09-18: the ideas not built — Forget all on a stopped team, unread mail on a folded team, a state roll-up on a live team's header, slimmer dead cards, a quieter mode badge | Low | Items 1 and 2 built 2026-09-23 (PR #499); 8 is Paul's canvas |
| TD-112 | The adapter contract has grown Claude-shaped and neutrality is untested: a second adapter (Gemini CLI) and a conformance suite every adapter passes | Medium | Designed — built by TD-784, TD-785, TD-786 |
| TD-131 | Mail, the board and the records are JSON and Markdown files read whole: fine at today's size, and no answer to search over history, counts per filter, or a retention longer than twelve hours | Low | Decision (Paul) — not yet; the trigger is written down |
| TD-133 | Build the team start at the reset — `schedules:` in `settings.yml`, the tick's replay, `ao schedule`, the card's *starts* note | Low | Open — designed; not scheduled until Paul says |
| TD-156 | UI review of the end of a session, and of the whole Focus screen: the two-line header, Close session as the next act, the side panel's folds, a concluded team never folds | Medium | Designed and built 2026-09-25 (cloud session with Paul) — live look pending; (g) not reproduced |
| TD-290 | A UI change goes to the person unchecked: the grinder that made it verifies it itself (Playwright, a scratch UI before the PR, the live copy read-only after the promote), a reviewer only for what it cannot settle, and what is left for the person is a look sent as mail — a steer or an ask, in Steering or Needs you — no longer a `watch` | Medium | Designed 2026-10-03 — build TD-292 (TD-291 built) |
| TD-292 | Build the mail half of a look: the briefs' three cases as a steer or an ask, `--shot` and the envelope's `shots`, screenshots and the pair on the row, Send to reviewer | Medium | Built (#975, #980, #982, #983, #987, #990, #1007); live check waiting on the first look a builder sends |
| TD-297 | The parts of the live looks of 2026-10-03 nobody could see: each waits on a press, a live event or a fixture, for a scratch home or the next time it happens | Low | Open |
| TD-299 | Run the guardians and contractmatch teams inside their devcontainers: provision the nodes for workers, define guardians' team, land both by `place:` | Medium | Paul's — contractmatch done; guardians' team, node and `place:` left |
| TD-300 | A look's screenshots from a sender on another host draw their names alone: the shot route reads this host's registry only | Low | Live check #993 — waits on a look from a node (TD-299) |
| TD-410 | Every team Start fills the on-call manager, which reads ~60k tokens of design to find nothing due: 14 fills since 2026-10-03, all *nothing to do* | Medium | Designed — TD-413 builds it |
| TD-413 | Build TD-410: a Start writes the on-call manager held — no pane until a reading is due — the fill's cause line, and the on-call briefs' first reads | Medium | Built (#1287, #1684); live check: a week's journal once #1684 is live |
| TD-460 | Read back the one-week trial of a 200k context bound on ao-grind's grinders | Medium | Open |
| TD-512 | A PR that truncates `docs/technical_debt_archive.md` passes every gate: the ledger tests and the cadence check's ledger row read neither its length nor what it lost | Low | Open |
| TD-553 | Drop the `.agentorc.yml` fallback once samscrape, contractmatch and dev-cadence have renamed theirs | Low | Blocked by the three repos' renames |
| TD-623 | No document says how a repo gets its first ShiftLead team: what to write, in which repo, what is the person's | Medium | Open — blocked: Paul says when guardians-devenv TD-028's gap list is enough |
| TD-636 | A director seat answers permission prompts for the person: routine ones allowed or denied by written rules, the rest escalated | Medium | Designed — TD-641, TD-642 build it |
| TD-637 | `ao progress done … --pr <n>` is accepted while the PR's required checks are red or missing | Medium | Designed — TD-639, TD-640 build it |
| TD-638 | A commit names the session that made it, not the model: as tools and models multiply, which model did what is lost | Low | Designed — TD-644, TD-645 build it |
| TD-640 | `check_cadence.py`'s `ci` row reads the base branch's required checks, not every run on the head (TD-637) | Low | Open |
| TD-641 | Build TD-636 (1): the person's `permissions:` rules per role, matched by the host at the hook, the always-the-person list, every answer an *Answered for you* FYI | Medium | Built (#1697, #1708, #1714); live check waits for the person's first `permissions:` rule and a profile set to `permission_prompts: ask` |
| TD-642 | Build TD-636 (2): the org's director seat — `director:` in `org.yml`, its trigger and `director.md`, the `permit` grant, the manager's triage standing aside | Medium | Open |
| TD-645 | `prepare-commit-msg` writes `Agent-Model: <adapter>/<model>` from `AGENT_MODEL_FILE`, else `AGENT_MODEL` (TD-638) | Low | Open |
| TD-656 | Build TD-646 (3): the Cloudflare route — the Access token check on the route port, `ao connect cloudflare`, its doctor line and guide | Low | Open — built when asked |
| TD-657 | The director seat knows the person only through its brief: it should also answer what techleads pass up, from the person's written preferences in layers, at a level the person sets | Medium | Designed — TD-661, TD-662, TD-663 build it |
| TD-661 | Build TD-657 (1): a pass goes to the director where the org has one — `passes` once per passer, the director's `asks` cause, its pass to the person, both recommendations on the row | Medium | Open |
| TD-662 | Build TD-657 (2): the preference layers and the level — the package's `preferences.md`, `{preferences}` in `director.md`, `director.level` per org and repo, `answered.level`, the Settings page's Director card, `ao prefs` | Medium | Open |
| TD-663 | Build TD-657 (3): the groom pass, the seed, Remember this, the interview — `ao prefs propose`, the *preferences proposed* row's Accept and Discard, the home's commit | Medium | Open |
| TD-673 | ShiftLead's Telegram notes go through samscrape's bot (`secrets: samscrape/prd`): give ShiftLead a bot and a Doppler config of its own | Low | Open |
| TD-681 | A green sync PR has no owner: in a repo with no session running, it waits for nobody | Medium | Designed — TD-684, TD-685 build it |
| TD-685 | Build TD-681 (2): §6 *Sync* — the home's sync check (the replay with `post_sync`), its review evidence, the gate and the merge; the Repos card's **sync** switch | Medium | Built — live check of #1775 |
| TD-703 | A member renamed in `.shiftlead.yml` keeps its old name on rule 8's restart, and a person's Start can leave the old record beside the new one | Medium | Designed — TD-706 builds it |
| TD-704 | The phase chips (*add*, *design*, *grind*, *review*) are fixed in the UI: define them, and what each matches, beside the roles in `org.yml`, overridable in `.shiftlead.yml` | Medium | Open |
| TD-706 | Build TD-703: a member renamed or removed in the definition is paired, applied and started without leaving two records | Medium | Built (#1802, #1807, #1818); live check |
| TD-717 | A seat whose first hook is lost sits with its brief unsent for hours: the scraped send never fires for Claude Code, and nothing acts on the *brief not sent* mark | High | Designed — TD-720 builds it |
| TD-720 | Build TD-717: a launch whose first hook is lost gets its brief from the composer, keeps *no hook* across a restart, and is replayed once | High | Built by #1834, #1837, #1838; live check read 2026-10-15, the replay waits for a lost first hook (anchor) |
| TD-723 | Backfill TD-722: samscrape and contractmatch under one ContractMatch project | Low | Open |
| TD-748 | The Queue: one card per effort — a TD and everything its `Blocked by:` joins, with its asks — ranked by the person, the rank the pick order; the Inbox keeps what names no TD | Medium | Designed — built by TD-769, TD-770, TD-771 (on dev-cadence#TD-119); trial TD-772 |
| TD-772 | Read back the Queue's two-week trial to Paul — passed-over flags a week, idle-while-work lines, the oldest unanswered TD ask | Medium | Open |
| TD-785 | Build TD-112 (2): the Gemini CLI adapter, launch and state — the profile's home, the layer, trust, `--session-id`, hooks to state, the title check, `permission_channel`, the kill guard, composer and title | Medium | Building — slice 1 #2014 |
| TD-786 | Build TD-112 (3): the Gemini CLI adapter's reads — transcript, context, links, model, start context, mail, background, usage and spend; the full conformance suite live | Medium | Open |
| TD-794 | The Focus terminal shows a reply's lines cut off mid-word, and a block of it twice: a resize makes Claude Code repaint over lines tmux has already kept | Low | Open |
| TD-804 | A promote is judged by `check` alone: nothing loads the pages or runs `ao` against the new live copy, so a build that passes CI on fixtures but breaks on the person's real state stays live until the person finds it and rolls back by hand | Medium | Designed — built by TD-811, TD-812 |
| TD-805 | Every page loads slowly from the person's browser (Org, Repo, Inbox, Queue): nothing measures a load end to end, and the Queue takes 2.4 s to its first byte and sends 1.9 MB of uncompressed markup | Medium | Open |
| TD-812 | Build TD-804 (2): this repo's smoke — `scripts/smoke.py`, every page loaded and `ao` read against the live copy, and `smoke:` in `.shiftlead.yml` | Medium | Open |
| TD-816 | The Queue page reads as one long list: its groups carry no count and do not fold, a card's attached mail is one run with no sections or Dismiss all, a filter press reloads the 2.4 s page, and the picks are lost on the Queue tab | Medium | Open |
| TD-818 | Nobody can see what each team member spends: the spend ledger reads metered profiles only | Medium | Open |
| TD-819 | Whether a team's shape is right cannot be read: nothing records how long members waited for work, or work for members | Medium | Open |
| TD-820 | The designer runs far past its context bound inside one design: 955 requests over 300k in a week | Medium | Open |
| TD-823 | `permission_prompts: ask` asks nothing Claude Code's `auto` mode allows: its classifier decides before any hook sees a permission, so the person's rules, the director seat and the person never see it | Medium | Open |
| TD-846 | Compliance before shiftlead is sold: SOC 2 and ISO 27001 wait for a hosted or team edition; the EU Cyber Resilience Act and the supply-chain basics a buyer asks for come first | Low | Open — deferred until the monetisation decision picks a paid option |

---

<!-- Entry template:
## TD-001: Short title of the problem

**Priority:** High | Medium | Low
**Added:** YYYY-MM-DD
**Owner:** designer | grinder | paul | dev-cadence
**Kind:** build | design-first | live-check | evaluation | decision
**Status:** Open
**Location:** `path/to/file.py` (function/section)

**Why:** what's wrong, how it was found, and the reasoning — future sessions need the why, not just the symptom.

**Fix:** concrete direction(s), and what would count as done.

**Related:** other TDs, PRs, decision docs.
-->

## TD-003: Phone layout: narrow Focus with a soft-key row

**Priority:** Medium
**Type:** feature
**Added:** 2026-09-06
**Owner:** paul
**Kind:** build
**Status:** **Paul, 2026-10-07:** reserved until the real connection path (how the phone reaches the home) is built. **Paul, 2026-10-13 (the lessons ADR, lesson 9):** the Inbox, improved, is to be the phone's text interface; a chat integration only if it cannot be. Open **Next:** Paul's (the grinder brief reserves it).
**Location:** `src/shiftlead/ui/static/app.css` (`@media (max-width: 720px)`), `focus.html`

**Why:** Design §4.5 (phone, phase 2): Team collapses to cards with Allow / Deny, Focus gets a narrow mode with the terminal full-width and a soft-key row (↑ ↓ ← → Enter Esc Tab 1–9) so questions are still answered through the terminal. The CSS has a bare media query; there is no soft-key row and nothing has been tried on a phone.

**Fix:** soft-key row that sends keys through the terminal websocket (not `send-keys`, invariant 6), collapsed side panel, 44 px tap targets on Allow / Deny; test on a phone over the route `ao connect` sets up (design §4.5b; TD-647, TD-648). On the phone the failure is silent — with the Tailscale app off or asleep, Safari shows its own error and none of the UI's pages — and iOS drops the `/events` and `/term` websockets whenever it suspends Safari: so reconnect on `visibilitychange` and `online`, the UI's disconnected banner opening with *is Tailscale on?*, the Telegram note saying *(Tailscale on)*, and the page fit for *Add to Home Screen* now that the route gives it a stable `https` origin (Fable review of TD-646, 2026-10-13).

**Related:** design §10 "Phone answers for questions" (open).

## TD-005: `pretrust()` can lose a concurrent Claude Code rewrite of `.claude.json`

**Priority:** Low
**Added:** 2026-09-06
**Owner:** paul
**Kind:** evaluation
**Status:** Open. **Read 2026-10-10 (ao-paul, with Paul): the file edit is the documented route, and its key is too narrow.** Claude Code has no flag, setting or environment variable that only skips the trust dialog (CLI reference, settings reference, env-vars, read 2026-10-10 on 2.1.296). `claude -p` skips it, but that is the headless mode, and bypass mode is not documented to skip it. The permissions page (*What runs before you trust a folder*) documents the manual route as `projects["<path>"].hasTrustDialogAccepted = true` in `~/.claude.json`, *where `<path>` is the repository root*. Its *Project allow rules and workspace trust* section adds that a worktree uses *the main checkout's root*. `pretrust()` keys the entry on `cwd`, each session's own worktree, so it rewrites the tool's file on nearly every new worktree where once per repo would do. That is where the race's exposure comes from. The worktrees page tells a person to run `claude` once in a new directory, which reads against the permissions page, so a test settles it. **Next:** an attended run, as the Fix says.
**Location:** `src/shiftlead/adapters/claude_code/__init__.py` (`pretrust`)

**Why:** The first-run trust quirk does a read-modify-write of Claude Code's own `.claude.json` under an flock that only agentorc takes. A running Claude Code session that rewrites the file inside that window loses its write (or we lose our flag, which merely re-shows the dialog). Raised in the PR #3 review; the window is one read plus one write at launch time.

**Fix:** (1) Test, in an attended run, whether a fresh worktree of a repo whose main checkout is trusted opens with no dialog (the permissions page says it does; the worktrees page suggests not). (2) If it does, key `pretrust()` on the main checkout's root (the first entry of `git worktree list --porcelain`, as the cadence line computes it) and on `cwd` outside a repo. The write then happens once per repo, and the race's window shrinks to that one launch. (3) As the fallback, answer the dialog through the terminal channel. The adapter's screen rules already recognise it (`explain`, which names the trust dialog), so what is missing is the key press, sent once, on a pane agentorc itself launched into a directory it chose. (4) Drop the file edit altogether only if (3) proves reliable on its own. Since there is no supported flag or settings key, the documented file edit stays the primary road. **Done when** the test's answer is written here, `pretrust()` writes at most once per repo, and a dialog that still appears is answered with no person's press.

**Related:** TD-006.

## TD-026: Scheduling: start/stop times and window overrides for unattended sessions, editable from the UI

**Priority:** Medium
**Type:** feature
**Added:** 2026-09-10
**Owner:** grinder
**Kind:** build
**Status:** Partly done — **round 2, 2026-09-25 (the designer, PR #548), on Paul's answer to the scope ask (*keep start_at and a scheduled state for one session as well*):** §6 *Start time* (`start_at`; the record created now with its launch record and the directory's slot, the `scheduled` state, the session created at the instant by the tick, `ao at`, Cancel), §4.2's state, §4.5a's *starts* note, **At** field and ◷ glyph, §4.7's verbs, the glossary's *start time*; the build is **TD-152**. Dropped on the same answer: window overrides with an expiry (TD-101), calendar-shaped windows, one-off runs. Was: **design round 2026-09-24 (the designer, PR #526):** the start at the reset is designed as §6 *Schedule* — a replay of the team's last person-pressed start from its launch records at the `resets` of a named profile's window, kept in `settings.yml` `schedules:` (§5), set by `ao schedule` (§4.7), shown as the team card's *starts* note (§4.5a); the build is **TD-133**, unscheduled until Paul says (2026-09-22: *not vital, not scheduled*). **A team-wide stop time** was designed 2026-09-25 as a setting (§6 *Team stop time*, `teams.<team>.until`, the Settings page and `ao team until`; TD-146 builds it), which answers the *editing a stop time from the page* shape for a team. The other shapes below — `start_at` and a `scheduled` state for one session, window overrides with an expiry (TD-101 already says *not wanted yet*), calendar-shaped windows, one-off runs — are a scope question put to Paul the same day (ask m-32d769985798; the recommendation: build the reset start, drop the rest, and let the phase-3 run window be its own design when it comes). Was: the per-session **stop time** landed 2026-09-13 (PR #129): `run_until` on the record, `ao new --unattended --until 06:00|+8h|<ISO>`, `ao until <session> <when>|--clear`, the wrap-up-then-kill on the tick, and *stops 06:00* on `ao status -v`. That closes gap (1) below — a hand-started worker now has a stopper. Open: `start_at` and the `scheduled` state, window overrides with an expiry, calendar-shaped schedules, one-off runs, and the UI *beyond* the stop time — the card and Focus header say *stops 06:00* and the New session form has an **Until** field (PR #131), **and the Focus header's badge became the control that edits it on 2026-09-14 (PR #147)** — click for a time, empty to clear, drawn only for an unattended session since the agent refuses one on an interactive session either way; a session with no stop time shows a dim *no stop time* rather than nothing, because "nothing will stop this" is the fact a person opening Focus most needs. The review of that PR found a live bug the control would have made easy to hit: `set_stop` cleared `wrapup_sent_at` on every call, so re-confirming an unchanged, already-elapsed stop time asked a session inside its wrap-up grace to wrap up a second time — and, since the ask happens before the grace check, repeatedly touching the control deferred the forced kill indefinitely, which is this entry's own gap (1) reintroduced through the edit path. Only a *different* time now counts as a new run. Latent and not reached today: every caller passes the one shared `WRAPUP_PROMPT`, so the control cannot yet clobber a custom wrap-up text, but it would if custom texts were ever exposed. The card shows no next transition for a repo window because there is no repo window. The repo window is still phase 3; when it lands it should *set* a stop time rather than be a second mechanism. **2026-09-22:** one more schedule shape, decided by Paul the same day: **a team start at the usage window's reset**, using the reset time the adapter already reports (§4.3, TD-073) — configurable, **off by default**, set from the settings page TD-100 describes; not vital, not scheduled. **Next:** designed; the builds are TD-133 (the reset start, unscheduled until Paul says) and TD-152 (the start time); this entry archives with them.
**Blocked by:** TD-133, TD-152
**Location:** design §6 (policies), §4.5a (New session, card / Focus header), §5 `.agentorc.yml` `unattended:` block

**Why:** Design §6 gives unattended sessions one schedule: the repo's weekly `window` (phase 3, the tdgrind port). That covers the nightly grind and nothing else. Two gaps showed on 2026-09-10, the first day the three samscrape tdgrind workers ran under agentorc instead of the cron supervisor: (1) a worker started by hand with `ao new --unattended` outside the window has **no stopper at all** — nothing wraps it up at 06:00, at a usage cap, or when the token lapses, so "start it now so I can watch it" means "remember to close it yourself"; (2) every deviation from the weekly window is a hand edit to a config file — tdgrind's `~/.tdgrind/config` carries dated shell one-liners (`[ "$(date -u +%s)" -lt <epoch> ] && WINDOW_START=0 …`) for "Paul is out today, run all day", which is a schedule expressed as code, invisible to the Team and forgotten once it lapses. A scheduler is the general form of both: a session or repo carries *when it may run*, the policy tick enforces it, and the UI shows and edits it.

**Fix:** treat the weekly window as one kind of schedule and add the others on the same policy tick, all visible on the card:

- **Per-session `run_until` / `start_at`** on the New session form and the `ao new` flags (`--until 06:00`, `--until +8h`, `--at 20:00`): a stop time gets wrap-up-then-kill exactly like leaving the window; a start time queues the session (a new `scheduled` state on the Org page, with the time) and the tick launches it. A session started by hand with `--unattended` and no `--until` inherits the repo window's next close, so gap (1) cannot recur.
  **`--until` landed 2026-09-13 (PR #129)**: the CLI parses `06:00` (the next one, in the caller's local clock), `+8h` and ISO and hands the agent an absolute instant — "the next 06:00" is a question about the caller's clock, so the record never has to answer it. At the instant the agent sends the wrap-up prompt once and kills the session when it settles or ten minutes later; the wording travels on the record (like `ledger`) because `sessionorc` must not know what a brief is, and it is the same text `ao team stop` sends. `ao until <session> <when>|--clear` sets or clears it afterwards, through a `set_stop` RPC gated as an acting call — a stop time is a `kill` with a delay on it. `--until` without `--unattended` is refused rather than stored: a stop time nothing will act on is this entry's own failure inverted. The UI followed in PR #131: the **stops** note on the card and the Focus header (§4.5a), from the same formatter `ao status -v` uses, and the New session **Until** field, which refuses a stop time on a session that is not unattended exactly as `ao new` does. Left here: `start_at`, the `scheduled` state, editing a stop time from the page (the CLI's `ao until` has no page equivalent yet), and the inheritance from the repo window, which needs the window (phase 3) first.
- **Window overrides with an expiry**, per repo, kept in agent state rather than the checked-in `.agentorc.yml`: "run all day until 2026-09-02 20:00", "pause until Monday" (the PAUSE flag with a date), caps to 99% until the weekly reset. Shown on the repo's row with the expiry; expired overrides fall away by themselves. This replaces the dated one-liners in tdgrind's config.
- **Calendar-shaped schedules** in the `unattended:` block beyond weekday/weekend: a list of `{days, hours}` rules, and a `tz` (the window is in the host's local time today, which is right on one host and ambiguous on two — phase 2 adds the second host).
- **One-off runs**: schedule a *command* session (§4.5 Commands) or a brief at a time — "run the deploy at 02:00", "start a review worker Friday evening" — which is the Commands tab plus a time. This is the dev-cadence `/schedule`-style routine, local and tmux-backed.
- Edits from the UI go through agent RPCs (`set_schedule`, `set_override`), never by writing the repo file; the checked-in block is the default, overrides layer on top. `ao status` shows the next transition ("stops 06:00", "starts Fri 20:00") per session and per repo.

Done when: a hand-started unattended session shows when it will stop and stops then; "run all day today" is one click or one `ao` line with an expiry, not a config edit; the Team shows a scheduled-but-not-started session; and tdgrind's `config` overrides file has nothing left to express.

**Related:** design §6 run window / usage gate / PAUSE, §7 phase 3 (tdgrind migration — this is the step after it, or the shape the port should take), TD-010 (adopting hand-started sessions faces the same "who stops it" question), samscrape TD-274 (the tdgrind supervisor), samscrape `~/.tdgrind/config` dated overrides of 2026-09-01.

## TD-035: Adapters without a session-start hook still run dev-cadence's SessionStart set

**Priority:** Low
**Added:** 2026-09-12
**Owner:** grinder
**Kind:** build
**Status:** Open — **designed 2026-09-24 (the designer, PR #530):** the 2026-09-12 decision is written into design §4.3 *A repo's start hooks are every adapter's to run* (the tool's start hook, else the runner at launch with its stdout prepended to the first prompt; a hand-started session untouched; the anchor check's source for another tool decided with that adapter). The build is the second adapter's, whichever TD-112's spike picks; this entry is its checklist and archives with it. Was: design question; no second adapter exists yet **Next:** nothing to build until a second adapter exists (TD-112's spike); the rule is written.
**Blocked by:** TD-112
**Location:** `src/shiftlead/adapters/<tool>/` (a future Gemini CLI / Codex CLI adapter), `src/shiftlead/adapters/claude_code/__init__.py` (`CADENCE_HOOK_LINE`, the pattern to generalise), design §4.2, §4.3

**Why:** dev-cadence's per-repo conventions are tool-neutral in their rules and scripts (cadence.md, the ledger and board, `check_cadence.py`, `cadence_hooks.sh`, the pre-push hook, the review-evidence PR comment) but their *wiring* is Claude Code's: the runner line lives in `.claude/settings.json` and in this adapter's launch layer, the runner resolves the repo from `CLAUDE_PROJECT_DIR`, and `check_anchor.py` reads the live-session registry under `~/.claude/sessions`. A repo worked by another tool gets the rules and none of the delivery: no attention board at session start, no cadence-changes entry, no anchor warning. Decided 2026-09-12 (with Paul): tool-specific wiring belongs in the harness adapter, not in the repo and not with a per-repo agent — the repo does not know which tool will work it; the harness does.

**Fix:** (1) every adapter's launch runs `scripts/cadence_hooks.sh --session-start` for the session directory when the script is executable there: through the tool's own start hook where it has one (Gemini CLI hooks, design §4.3 table), otherwise by running the runner at launch with the hook payload it would have received and prepending its stdout to the first prompt — non-intrusive, nothing needed from the repo; (2) the runner takes the repo root from an argument or `PWD` as well as `CLAUDE_PROJECT_DIR` (dev-cadence change; it already falls back to `git rev-parse --show-toplevel`) — settled by design §4.3 and ADR 2026-10-08 (TD-159): the runner falls back to `pwd`, and nothing is owed to dev-cadence; (3) the anchor check needs an equivalent of the live-session registry for the other tool, or agentorc's own session records as the source when the session was launched by it — decide when the second adapter lands. Done when a session launched through a second adapter in a dev-cadence consumer prints the due-items line and the unseen cadence-changes entries at its start, and a hand-started session of that tool is untouched.

**Related:** design §4.2 (the launch layer carries the line, 2026-09-11), §4.3 (adapter contract, the hooks table), §4.8 (relay); dev-cadence cadence §3 (one SessionStart line; who makes the change, 2026-09-12); TD-028 (orchestrator brief).

## TD-052: Messages between sessions — build design §4.10

**Priority:** High
**Added:** 2026-09-14 (raised by Paul)
**Owner:** grinder
**Kind:** live-check #343
**Status:** Open — **the design landed first, as §4.10 (2026-09-14)**, with three rows in §4.5a, a sentence in §4.7, invariants 5 and 11 amended and invariant 13 added, and the §10 question answered; **extended 2026-09-16** with how a Claude Code session is told it has mail (the doorbell, the `Stop` hook, the line on every `ao` reply) and the lifecycle of an entry; **revised twice 2026-09-16** after a Fable review of all of §4.10 (thirteen changes, §10's entry of that date — among them the wake budget's refill rule, the person inbox, copies to other controllers, the `Stop`-hook block cut, and the build order below — then sixteen more from a second Fable review: the host agent decides mail wakes when a recipient is reachable, `wait` moves into the host agent, exact thread counting, pending asks on exit; **a fourth Fable review the same day** adopted ten more — open asks never pruned, the pair tally windowed, a non-wake leaves the watermark, repeats refused not dropped, `sends` recorded on the target for `conflict`, the controller's `send` charge dropped, the skill file displaces, a person's Message control, wall-clock ask bounds, person-inbox refusals name the board — and Sonnet review rounds followed, §10). **Step 1 landed 2026-09-17 (PR #163, Fable)**: `sessionorc.mail` holds both gates as functions over a record map (`act_gate` is the old `_gate` moved; `message_gate` reads the four edges) and the bound constants, every one a placeholder until step 6 (`None` = unlimited; each refusal path is built and tested by monkeypatching it); `MailEntry`, `Tally` and `SendEntry` on the record (`inbox`, `outbox`, `threads`, `sends`, `superseded_by`, `host`), with `view()` — what `list`, `get` and the push carry — dropping bodies for `unread` and `mail` marks; the `msg` and `inbox` RPCs; `sends` written by `send`/`keys` at the gate with the submit's verdict, and `ao status -v` printing the last three plus the unread count and marks; the tick's `_sweep_mail` (expiry, pending on exit, expired on close, retention); `_supersede` moving mail and rewriting ids; a same-nonce retry returning the first verdict. Two things the step text did not say, decided in the code and written into §4.10: the sender keeps its own copy of every entry (`outbox`) so the sender-side marks are on its record, and `ACTING_RPCS` moved to `mail.py` beside the gates (re-exported from `agent.py`). Two things left to later steps deliberately: `ao msg` / `ao inbox` as CLI commands and the person inbox (step 2 — a `to` of `person` is refused naming the board until then); the identical-repeat and per-sender damping (step 6, with the numbers). `tests/test_mail.py` carries the tests, one per rule. **Step 8 landed 2026-09-16 (PR #165, Opus)** for the four session-side rows of §4.5a — the Focus Inbox panel, the card's unread chip, Reply, and Message — plus an `inbox_delete` RPC (a person's only, one record's copy); the Org top bar's person inbox followed once step 2 had merged: **step 8 completed 2026-09-16 (PR #168, Opus)** — the top bar's count (rendered on the Org page, then polled from the `inbox` RPC: nothing pushed carries it), its panel with Reply and delete, and `inbox_delete` naming no session deleting from the person inbox; no Reply on an entry the person sent, by design — a person does not answer themselves, and the session's answer lands in the person inbox. **Step 2 landed 2026-09-16 (PR #166, Opus)**: `ao msg`, `ao inbox` (the fixed header, each entry marked controller / person / other) and the person inbox in `person_inbox.json` beside `sessions/`, with the mail rules in `ao --skill`; the top bar reads it with the `inbox` RPC given no caller and no `id`, and replies with `msg` given `reply_to` and no `to`. Decisions the step text did not make: the person inbox keeps no exchange tally — only the sending session's record counts, and `PERSON_INBOX_DEPTH` / `PERSON_SENDER_DEPTH` bound the rest (a sentence in §4.10); a session's `reply` to a person's message with no addressee lands in the person inbox, and a person may not write to it; its depths count unread entries, which — a person's read setting nothing — is every entry until a person-inbox delete exists (`inbox_delete` covered session inboxes only until PR #168 added the person inbox); every session sender shows as `[other]` there, since no session is the person's controller; `ao msg --reply-to <id> "…"` may omit the addressee and defaults `--kind` to `reply`; `ao inbox` at a terminal reads only the person inbox; and to hold 120 lines the skill's membership and verify-every-send paragraphs were tightened as well as the `ao wait` one. **Step 4 landed 2026-09-16 (PR #170, Opus)**, text only: the mail rules in `src/shiftlead/briefs/orchestrator.md`, `grinder.md`, `hunter.md`, `docs/briefs/orchestrator-ao-1.md` and `docs/briefs/tdgrind-ao-1.md` — a lead's round reads `ao inbox --unread` first and answers every `ask`; a grinder reads its inbox before claiming, notes its same-team siblings at claim and mails its lead at `done`; `ao msg person` for what is not yet a board item; the controllers-and-people rule once per brief; and an *unknown method* fallback to `ao progress` / `ao finding` for a host agent older than the build. The live leads' ticks still run on `loop` rather than ending in `ao wait` (TD-049 steps 5–6), and `ao wait` returns on mail only once step 3 lands; the briefs take effect on the next `ao team start`. **Step 3 landed 2026-09-16 (PR #171, Opus)**: `wait` is the host agent's RPC (`ao wait` a thin call, which says in one line and exits 1 against an agent that predates it), held on its own connection and cancelled when that connection closes; the wake decision is `_decide_wake`, the one function step 7's doorbell calls; `mail_decided`, `wakes` and `wake_refilled_at` are home-owned fields on the record; `WAKE_BUDGET` (None), `WAKE_WINDOW` and `WAKES_KEEP` sit in `mail.py`; every response to a session with unread mail carries `mail` in its envelope, and the CLI prints the line after every command that reached the agent. Decisions the step text did not make: mail's half of the per-caller cursor *is* the `mail_decided` watermark, so no mail is kept in the `waits/` file; a first wait wakes on no member's change but does decide unread mail already waiting; a wait returns mail as headers only (id, from, role, kind, about, at), so `read_at` stays `ao inbox`'s; `ao --json wait` prints `{changed, mail, wake}` rather than a bare list; a person's session blocked in `wait` is never returned by mail (invariant 5); a person's `keys` refills like a `send`, and a person's message refills its named addressees, not its copies; the watermark, `wakes` and the refill move with a resume; the unread line goes to stderr under `--json`, and a command that never reached the agent (`ao --skill`) prints none; a watermark whose entry has left the inbox falls back to its `at`, compared `>=` (a redundant wake, never a missed one). Step 5 reads each record's `wakes`: `cause: mail, charged: true` are mail-caused wakes, `cause: member, charged: false` the free ones, `covered` the entries each took in. The briefs' *once step 3 lands* caveat stays true until the live agent is restarted onto this code. Steps 5, 6 and 7 below stay open. **The workers building them run from briefs in `docs/briefs/archive/td052-step*.md`** (steps 2 and 8: PR #164; steps 3, 4 and the top-bar row 8b: PR #167 — both merged on a failing `ledger` row, which this sentence repairs: a brief names the step it builds, and the ledger is where that is recorded), one worker per step under orchestrator-ao-1, each step its own PR. Code steps below, each its own PR, in order **Found watching two live teams, 2026-09-17:** delivery, three of the four gate edges in use (upward, downward, team; no *shared target* yet), the pair tallies and the person's notes all behave as §4.10 says, but **no record has a single `wakes` entry**: both leads pace their rounds with the `loop` timer and never call `ao wait`, because the lead brief says only that `ao wait` *returns on new mail* and never tells the lead to block in it between rounds. So mail to a lead waits for its next 10-minute round, and step 5 has no wake data to measure. Before step 5 can produce numbers the lead brief needs the sentence (block in `ao wait --timeout <round>` between rounds), which takes effect at the next team start. **Done 2026-09-17 (PR #194):** the lead template and `docs/briefs/orchestrator-ao-1.md` end every round in `ao wait --timeout 540`, with the `loop` timer kept only as the fallback when `ao wait` itself fails, and a quiet round no longer costs a commit. Not seen live yet. **Step 6 landed 2026-09-18 (PR #214):** every placeholder set from step 5's measurement (eight hours of samscrape-grind, 2026-09-17), with the evidence and the rule *twice the measured traffic stays clear* written into §4.10 *The numbers*: thread 40, pair 300 per 24 h (`PAIR_BOUND`, split from the thread bound — one number could not serve both), mailbox 100 unread, person inbox 200 / 20 per sender, retention 12 h, wake budget 30 an hour. A thread's tally is pruned with its last entry, and the bookkeeping fields ride only a `get`. Step 5 is what it was — the records of any night's team. **Step 7 landed 2026-09-20 (PR #343, Opus, grinder-ao-1):** the doorbell — each tick, a hook-confirmed `idle` session with unread mail no wake has covered and an empty composer is rung with `sessionorc.mail.unread_line`, the one text the per-command line prints, as its own task; the ring is `_decide_wake` (`via: doorbell` on the wake), so the budget and *rung only for new mail* are the watermark's; one ring per idle stretch; a person's session, a pane with no `composer()`, a session blocked in `wait` and a wrap-up under way are skipped. *A wrap-up under way* gained `wrapup_at`, stamped by a `send` marked `wrapup` (Wrap up and `ao team stop`, the manager's wind-down) and cleared by the next plain send, because the host agent cannot tell the wrap-up prompt from another send by its text. A failed ring is retried once without a second charge and only into an empty composer, then written as `doorbell_failed`, which `ao status -v` prints. As built in design §4.10 after the doorbell's rules. **What is left of this entry:** the second-adapter step below (with TD-035) and the *Done when*'s live half — seen on the running system after a promote (docs/user_attention.md). **Next:** the second-adapter step waits on TD-112, the live half on a promote.
**Blocked by:** TD-112

**Location:** `src/sessionorc/models.py` (the `inbox` on the record), `src/sessionorc/agent.py` (`rpc_msg`, `rpc_inbox`, a new `rpc_wait` — `ao wait` today runs wholly in `src/shiftlead/cli.py` (`cmd_wait`, `wait_scope`, `wake_changes`) and moves here — the message gate beside `_gate`, `_supersede`), `src/sessionorc/store.py` (persistence with the record), `src/shiftlead/cli.py` (`ao msg`, `ao inbox`, `ao wait`), `src/shiftlead/ui/` (the Inbox panel, the unread chip, Reply), `src/shiftlead/skill.md`, design §4.10 / §4.5a / §4.7 / §9 invariants 5, 11, 13

**Why:** four kinds of session-to-session traffic exist — lead → worker, worker → lead, lead ↔ lead, worker ↔ worker — and one mechanism served all of them: `ao send`, the host agent typing keystrokes into a pane. Only the first works. A worker reaches upward through `progress` and `findings`, which are the wrong shape for it: declarations about references, addressed to nobody and delivered to nobody. They are ungated — any session may write any record's — but that is not a channel: writing onto another session's record states a fact about it rather than telling it anything, and nothing carries it to the session that must read it; two leads are peers, so TD-036's gate refuses them each other; two workers coordinate by side effects — a ledger row, a branch name, a PR that already claims the reference. Paul, 2026-09-14: *"we need to make sure agent to agent comms are first class in our design."* The cause is a conflation, not four missing features: the `control` grant (`orchestrate` until TD-055) + `controllers` answers *may A act on B?*, and messaging was folded into it because keystrokes were the only delivery there was — typing into a pane genuinely is an act of control. §4.10 splits the two. This entry builds it.

**Fix, in order — each step is usable on its own.** Reordered 2026-09-16 after the Fable review of §4.10: everything that types nothing comes first, a team runs on it, and the wake budget is set from what that team spent *before* the doorbell — the only part that types into a pane — is built.

1. **The record and the RPCs.** Built with TD-057 step 1's address choices from the start — `host` on the record, ids qualified on the way in, message ids, `at` and nonces minted by the host agent, the gates as functions over a record map — so a second host needs no migration. `inbox` on the session record (persisted and reloaded with it, dies when it is forgotten); the `msg` and `inbox` RPCs; the message gate as its own function beside `_gate`, refusing by naming §4.10's graph rather than invariant 11, with a test per edge — upward, downward, same `team` badge (invariant 9's named exception), shared controlled target — and per refusal. The interactive split is its own test: an acting RPC onto a person's session still refused, a message to it landing and never waking (invariant 5). Also in this step, because each is a field or a rule on the entry: `read_at` set only when `inbox` returns the entry to its caller (never by `wait`, never by a person's UI read); `text` capped; **threads** — a `reply` belongs to its root's thread whatever its `about`, messages replying to nothing count per pair, and the tally is a field on **both** participants' records, never recounted from surviving entries; the **automatic copy** of a controller's mail `about` a session to that session's other controllers, expanded into `to` at send time and **exempt** from the recipient cap and from all-or-nothing (a copy that cannot land is dropped and recorded as `copies_failed` on the sender's entry), with replies in a copied thread copied to the same set; the recipient cap counting only sender-named addressees, and a multi-addressee send **all or nothing**, refused naming the failing addressee; the **thread tally per root on every record holding an entry of it** (the reply-less pair tally within a rolling window, never lifetime), counting `ask`/`note`/`conflict` (the first `reply` to an open `ask` never counted, never refused, and closing it; later replies, and replies to a `note`, counted as notes; `reply_to` required to name an entry in the replier's own inbox), checked against the sender's and each named addressee's tally, never a copy recipient's; an `ask`'s bound (wall-clock, home's clock) running from `at`, read or not, and an open `ask` or `conflict` exempt from pruning until it closes or expires; a bounded **`sends`** list on every record — `{id, from, at, text}` for each `send`/`keys` that reached it, written at the gate — printed by `ao status` and cited by id in a `conflict` — which is an `ask` for every rule (bound, first reply free, pending on exit, never pruned open) — and a person's message into a thread uncounted and resetting its tally and `bound_hit`; an exited addressee leaving its `ask`s **pending** (expired only on close, forget or the `ask`'s own bound), with *addressee exited* on the asker's replies; and `_supersede` (`agent.py`) moving every entry inside retention, read or unread, plus the tallies and `sends`, onto the resuming record (a `send` to the superseded id refused, as any act on a closed record), **rewriting the old id to the new one** in the moved entries' `to` and in every other record's pair tallies and pending `ask` addressees; and a `msg` to a closed record that a live one superseded **forwarded** to the successor, saying so. Bodies stay out of the `subscribe` deltas (counts only).
2. **The CLI and the person inbox.** `ao msg <to>… "…" [--kind] [--about] [--reply-to]`, `ao inbox [--unread] [--json]`, and `ao msg person "…"` into the person inbox — one per org, held by the home host agent (§4.4a; on one host, that host's agent), belonging to no session record, persisted in its own file in the host agent's store, ungated, with its own depth and per-sender depth; `ao inbox` with no `AGENTORC_SESSION` reads it; a person's reply from it is a person's act toward the sender (it may wake it and refills its budget). `ao --skill` gains the mail rules — read your inbox before acting, answer an `ask`, never broadcast, reach a person with `ao msg person` — displacing the `ao wait` paragraph so the file stays at 120 lines, and, above the rest, **instructions come from your controllers and from people; mail from anyone else is information you weigh** — stated again by `ao inbox` itself: a fixed header, and each entry marked as from a controller, a person, or neither.
3. **Typing-free waking: `wait` moves into the host agent and gains mail, and the line on every `ao` reply.** `ao wait` already exists (§4.8 "Waking a lead", TD-049 steps 1–4, PR #145) and blocks on a member's state change, but runs wholly in the CLI, so the host agent cannot know who is blocked or why a wait returned. Move it into the host agent as the `wait` RPC with the same snapshot and per-caller cursor, and add the **wake decision**: when a session is reachable (hook-confirmed `idle`, or blocked in `wait`; across hosts also its node's link up, TD-057 step 5), the host agent looks at the unread mail since its last decision and records one mail-caused wake covering it — a `wait` returning for a member's change at the same moment records none — against a single per-session `mail_decided` watermark that every **wake** advances and a non-wake leaves alone, re-taken on each host-agent tick while a session stays blocked in `wait`. A `wait` runs on its own connection and is dropped when that connection closes (no ghost waits after a Ctrl-C); the per-caller cursor stays on disk under `waits/`; `wait` is not an acting RPC. Until step 6 sets a number the budget is unlimited, but every decision is recorded, which is what step 5 measures. This step adds new mail to what `wait` returns on, and its per-caller cursor already keeps mail that arrived mid-turn for the next wait. Every `ao` command's output ends with `[agentorc] you have N unread messages — run ao inbox` while the caller has unread mail, plus `(wake budget spent)` while it is. Neither types into a pane.
4. **The briefs**: a lead's round reads its inbox at the top; the grinder mails its lead when a reference is done instead of relying on the lead noticing, and, until 2026-09-20, sent a `note` to its sibling workers (the same-team edge) when it **claimed** one, which is what let two workers learn the other held a reference before duplicating it. That at-claim note was always a stopgap and is gone: TD-056's reference leases replaced it once the running host agent was seen refusing a second live session's claim by name.
5. **Run it and measure.** A lead and two grinders on steps 1–4 for a few evenings, recording per session the mail-caused wakes (`wait` returning on mail), **separately** the free wakes that carried mail alongside a member's change (the grinder marks `done` and mails its lead at once, so without this split leads would read as needing no budget), exchanges per thread, mailbox depth reached, and how long read entries stay useful.
6. **The bounds, with numbers from step 5**, written into §4.10 in the same PR with the evidence that would change them: the **wake budget** as mail-caused wakes per rolling window, refilled by time and in full by a person's **act** toward the session (a send, a reply, an answer — never opening Focus or reading), never by a controller's send or a non-mail turn; a controller's `send` never charged (the round-two charge was dropped in the fourth review as redundant with the wake charge); exhaustion visible on the record and to the sender; the mailbox depth, refusing with a reason the sender sees; the per-thread exchange bound and the pair window, past which the host agent **refuses the send** naming the bound and the thread and writes a `bound_hit` mark every participant's card and `ao` replies show (the sender writes any board line itself — the host agent never commits to a board here); an `ask`'s default bound and the *expired* mark on both cards and the asker's `ao` replies, including the gone-addressee case; the `text` cap; the delivery damping (identical-repeat window and per-sender rate, both refusing with a reason); the retention window and pruning, open asks exempt; the person inbox's depth, whose refusal names the board.
7. **The doorbell** (§4.10, 2026-09-16): (a) the fixed line — an unread count and nothing a sender wrote, with a test that a crafted `about` never reaches the pane; (b) submitted through `_submit` only on a hook-confirmed `idle` **with an empty composer**, coalesced per idle stretch, spending wake budget, and skipped for a person's session, a session with no `composer()`, a spent budget, a wrap-up under way or `out_of_work`; rung only when the unread count has risen since the last ring; retried once, then recorded. A session that has only declared `out_of_work` may be rung — only a wrap-up or wind-down under way beats mail. No `Stop`-hook block — cut in review; `Stop` stays fire-and-forget.
8. **The UI**: the Inbox panel on Focus beside Reports, the unread chip on the card, Reply, a person's **Message** control, and the person inbox in the Org top bar (§4.5a's five rows).

**Deliberately not in scope:** any broadcast, channel or room; mail as a durable record (the ledger and the board keep that job — invariant 13); and messaging across hosts, which design §4.4a routes through the home host agent and TD-057 builds.

**A further step, only when a second adapter exists:** `mail` on the adapter contract (§4.3) — how a session of a given tool is handed entries and woken by them. Claude Code's answer is `ao inbox` and `ao wait`, which is why steps 1–8 can be built without it; the contract entry earns its keep the moment a tool with no shell, or one whose turns a harness composes, gets an adapter. Until then the honest statement is the one §4.10 makes: an adapter with no native path falls back to a pane write, which is a `send`, and the message/control line is that adapter's convention rather than the host agent's gate. Sequenced with TD-035, which is not itself the work of building a second adapter but the design question that second adapter forces — dev-cadence's SessionStart wiring is Claude Code's — and this step is the same question asked of mail: both are answered by the adapter, and neither can be answered before one exists.

**Done when** a worker tells its lead it finished without the lead polling, and an idle worker is rung for a message with nothing its sender wrote typed into its pane; a worker reaches its person through the person inbox; two leads over one worker settle a contradiction between themselves and land a board line when they cannot; a message to a person's session is delivered where a send is refused; every refusal names §4.10's graph; a session out of wake budget receives mail that lands without waking it, visibly to itself and to the sender; and a person's session is never woken by mail at all.

**Related:** design §4.10 (the whole section), §4.5a, §4.7, §9 invariants 5, 11 and 13, §10 (the 2026-09-14 entry, and the 2026-09-13 one it answers); TD-039 (the conflict half — a `conflict` message and its exchange, which step 6 bounds), TD-049 (the wake, absorbed by step 3), TD-036 (the graph the gate reads), TD-028 (the report channels the Inbox sits beside and deliberately is not).

## TD-060: The name `agentorc` is taken — rename the project

**Priority:** Medium
**Added:** 2026-09-16 (Paul; discussed in samscrape session 746e1973 on kmaster)
**Owner:** grinder
**Kind:** build
**Status:** **Read 2026-10-12 (the anchor):** TD-552 is archived (#1520); what is left is TD-553, now this entry's `Blocked by`, so it leaves the anchor's pickable list until TD-553 is done. **Split 2026-10-11 (the anchor, Paul's word):** what is left is no longer Paul's and no longer here — the rest of (b), the text sweep, is **TD-552** (a grinder's); (c)'s last step, the fallback dropped, is **TD-553**, blocked by each repo's rename: samscrape's `.agentorc.yml` → `.shiftlead.yml` by the anchor the same day (samscrape #1077), contractmatch's in its own ledger (TD-055, with its briefs, `CLAUDE.md` and memory), dev-cadence's in its own (TD-091, with (d), the synced files, and a sync; its TD-089 holds the `eyecantell/dev-cadence` headers). **The dev venvs were rebuilt the same day** (Paul's word): `~/shiftlead` and the 22 worktrees whose `.pdm-python` named `/home/kmaster/agentorc`, `rm -rf .venv .pdm-python && pdm sync -G ui -G dev` in each; `pdm run pytest` passes in the checkout and `grinder-ao-1`. This entry archives when TD-552 and TD-553 are done. **(b) the live briefs swept 2026-10-11 (the anchor, Paul's word, before ao-grind's restart):** nine of the ten under `docs/briefs/` outside `archive/` (`techlead-context.md` had none) name `~/shiftlead`, `src/shiftlead/`, `shiftlead-venv`, `~/.shiftlead`, `SHIFTLEAD_HOME`, the `shiftlead-*` units, `.shiftlead.yml` and `repos/ShiftLead/shiftlead` — the Playwright interpreter and grinder-ao-2's lane named paths that no longer exist; `docs/briefs/archive/` stays as written. **Found then, left for Paul's press:** the dev venvs did not move with the checkout — `~/shiftlead/.pdm-python`, the main `.venv`'s `.pth` and script shebangs, and each worktree's `.venv`/`.pdm-python` name `/home/kmaster/agentorc/…`, so `pdm run test` fails in the checkout and every worktree until each is rebuilt (`rm -rf .venv .pdm-python && pdm sync -G ui -G dev`). **(e) the repo renamed 2026-10-11 (the anchor, Paul's word; no session running):** GitHub `eyecantell/agentorc` → `eyecantell/shiftlead`, then transferred to the `ShiftLead` org the same day (Paul's word) — `ShiftLead/shiftlead`, both old URLs redirect; the checkout moved `~/agentorc` → `~/shiftlead`, with a symlink left at the old path for the session that moved it (`rm ~/agentorc` once that session is closed), `git worktree repair` over the 30 worktrees and `origin` set to `https://github.com/ShiftLead/shiftlead.git`; `org.yml`'s project and `settings.yml`'s `repos:` key renamed `shiftlead` (backups `*.bak-2026-10-11-repo-rename`), `promotes/agentorc` moved to `promotes/shiftlead`, dev-cadence's `repos.txt` and `consumers.txt`, each checkout's `autoMemoryDirectory`, and `~/.claude/projects/-home-kmaster-agentorc*` moved to `-home-kmaster-shiftlead*` with links back, so a past session resumes from the new path. Read after: `ao team list` — ao-grind on project `shiftlead`, its seats `ao-shiftlead-…`; `ao promote` names the repo `shiftlead`; `ao doctor hooks --probe default` — SessionStart in 1.9 s. A closed session's `ao-agentorc-…` records stay as they are. **(a) the cutover done 2026-10-11 (the anchor, Paul's word and root press; no session running):** `ao host forget contractmatch` (there is no `host down`; it drops the `nodes:` entry, put back by hand), the new venv, `ao migrate`, `sudo … --system`, `ao service install`, node up. Read after: `ao doctor` — tmux under `shiftlead-tmux`, the watch timer, every profile, node contractmatch linked at the home's build; `ao team list` reads all four teams (three through the `.agentorc.yml` fallback); `ao doctor hooks --probe default` — SessionStart in 1.7 s; the old venv removed. **What the runbook missed, for the next machine:** `~/.local/bin`'s `ao`/`agentorc*` links still pointed into the old venv (repointed by hand to `shiftlead-venv`); the home agent started before the `nodes:` entry was back, so it opened no link socket for the node until `ao service install` again; and its supervisor raced the hand `ao host up` into a second container (removed). **Step 2 merged 2026-10-11 (#1505; #1511 renamed two build-chip tests that merged beside it). Built 2026-10-10 (the anchor, branch `td060-rename`; Paul, that evening, with every session closed after the tmux move):** the decisions — `agentorc` → `shiftlead` for the distribution, the product package, the console scripts (`shiftlead`, `shiftlead-agent`, `-hook`, `-ui`, `-watch`; `ao` stays), the entry-point group, `~/.shiftlead`, the `SHIFTLEAD_*` variables, the four units and `~/.local/share/shiftlead-venv`; `sessionorc` **kept**; a **clean cut** with a one-time `ao migrate`; a repo's `.agentorc.yml` **read as a fallback** (`sessionorc.paths.repo_file`) until each repo renames its own by PR. The `ao-` prefix stays. **Left, in order:** (a) the cutover, with no session running and both units stopped — `ao host down contractmatch`; a new venv (`virtualenv -p python3.13 ~/.local/share/shiftlead-venv`, `pip install '/home/kmaster/agentorc[ui]'` at the merged main); the checkout on a clean, current `main`; `shiftlead-venv/bin/ao migrate`; `sudo …/shiftlead-venv/bin/ao service install --system` (Paul's root press, before the user units: it removes `agentorc-tmux`/`agentorc-watch` and starts the new tmux server); `shiftlead-venv/bin/ao service install`; `ao host up contractmatch`; the old venv removed; (b) the text sweep the PR left: `docs/briefs/**` (held paths), `docs/mockups/`, `docs/cadence*` notes, the memories' live pointers — dated records stay as written; (c) each repo's `.agentorc.yml` → `.shiftlead.yml` (agentorc, samscrape, contractmatch, dev-cadence), then the fallback dropped; (d) dev-cadence's synced files that name `agentorc` (`scripts/cadence_hooks.sh`, `docs/cadence.md`), and a sync; (e) **the repo renamed `shiftlead` too (Paul, 2026-10-10)** — GitHub redirects the old name; the checkout's directory, and with it every `ao-agentorc-…` session id, the `agentorc` project in `org.yml` and the Claude transcript paths, is its own quiet window. Before: **Paul, 2026-10-07:** snoozed to 2026-10-21. **Owner paul since 2026-10-06 (the anchor):** nothing in it is a member's to pick. Step 2 waits for the release boundary Paul picks. Open — **Paul, 2026-10-04: the anchor picks the quiet window for step 2 and runs it** (no team running, both units stopped). **decided by Paul 2026-09-22: go with the rename to `shiftlead`** (his words: *go with the rename to shiftlead, we will have to figure out when to rename the repo*). **Step 1 is done:** its remaining half, the visible name — the UI wordmark in `base.html` (*Shift**Lead***), every page's `<title>` (*Org · ShiftLead*) and the mockups' wordmark — landed 2026-09-22 (grinder-ao-2). **Step 2, the machine names** (`~/.agentorc`, the seven env vars, the units, the venv, `.agentorc.yml`, the `agentorc-hook` entry point, with the migration and its test), waits for a release boundary with no team running and both units stopped, as *Fix* says, and the anchor runs it. **When the GitHub repo is renamed is Paul's, and open** (board line, 2026-09-22): it can follow step 2 or go first, since GitHub redirects the old name. Was: awaiting Paul's decision on the name. `shiftlead` is the leading candidate (free on PyPI, npm, crates.io and as a GitHub org when checked 2026-09-16; `.dev`/`.org`/`.tech` open per Paul); Paul asked for time to think. Nothing is renamed. **Both names were claimed 2026-09-17 ahead of the decision:** PyPI `shiftlead 0.0.0` (empty placeholder) and the GitHub org `ShiftLead`; the domains are not registered. The candidates, the test they were held to, the measured surface and the six decisions are in [ADR 2026-09-16](decisions/2026-09-16-rename.md). Worktree `agentorc_rename` exists with no commits. **Next:** step 2 waits for the release boundary Paul picks.
**Blocked by:** TD-553

**Location:** the whole repo — 1,110 case-insensitive occurrences of `agentorc` in 93 tracked files and 260 of `sessionorc` in 56 (`origin/main` at `8bd21b9`) — plus what lives outside it: `~/.agentorc/`, seven `AGENTORC_*` environment variables (the six below plus `AGENTORC_BUILD`, added since this was measured), `agentorc-agent.service` / `agentorc-ui.service`, `~/.local/share/agentorc-venv`, `.agentorc.yml` in other repos, the `ao-` session-id prefix, and three synced files in dev-cadence (and so in samscrape and contractmatch).

**Why:** another project holds the name (TD-059 is to identify it). This is the second rename (`sessionherd` → `agentorc`, 2026-09-04, chosen to sit beside `cmdorc`), and it is cheaper now than it will ever be again: one user, nothing published to PyPI, no external adapter known. Every week adds briefs, ADRs and session records that carry the old name.

**Fix:** decide the six questions in the ADR (the name; what happens to `sessionorc`; whether the `ao-` id prefix stays as an opaque historical prefix — recommended; whether `ao` survives as an alias; a compatibility window or a clean cut with a `migrate` command; whether to claim the names before deciding). Then, in order: claim PyPI + the GitHub org; land TD-055 first, since it edits many of the same lines; one PR for packages, entry points, env vars, state dir, units and config file name, with the migration and a test for it; the text sweep, **leaving dated records — ADRs, the ledger archive, memories — as written**; dev-cadence's three synced files and a sync; the repo rename and a README tagline drawn from TD-059. **Land the machine-facing step with no team running and both units stopped:** running sessions hold `AGENTORC_SESSION` in their environment and resolve `agentorc-hook` from the unit's PATH, so a rename under a live team breaks every started session's hooks.

**See TD-076 before deciding (2026-09-20):** on 2026-09-19 Paul decided the coordinating role becomes `manager`, the go-between `techlead`, and bare `lead` is retired — which weakens the *shift lead is the job* argument for `shiftlead` and should be weighed in the same sitting.

**2026-09-21, Paul: the spelling is `ShiftLead`, `shiftlead.dev` is bought (Cloudflare), and the command stays `ao`** — short, in every brief and every worker's habit, and `sl` is a joke command on many Linux boxes; `shiftlead` may be a long-form alias. **The order confirmed by Paul 2026-09-21** (*that order works*) — **step 1 begun the same evening: the README's and the design's titles and the glossary say ShiftLead (this PR); the wordmark in `base.html` and the page `<title>` are a worker's, one line each; step 2 waits for a release boundary.** As suggested: **the visible name first** (the UI wordmark, the README, the design's title and this ledger's head — one PR, cheap, the page half a worker's) and **the machine names at a release boundary** (`~/.agentorc`, the seven env vars, the units, the venv, `.agentorc.yml`, with the migration and its test) with no team running, as *Fix* says. **Decided by Paul, 2026-09-20: the project keeps `shiftlead`** — with the roles `manager` and `techlead` (TD-076) — so the name stands for what the product is for, not for a role in it; the ADR's *the metaphor is the job* paragraph is to be reworded when the rename is carried out. What remains of this entry is the carrying out.

**Done when** the installed distribution, both units, the state directory and the README carry the new name; a session started before the rename is either migrated or documented as needing a restart; `git grep -i agentorc` returns only dated records and the "was `agentorc`" lines; and the three dev-cadence files are synced.

**Related:** TD-059 (the neighbours the name search found; its survey supplies the tagline), TD-055 (the glossary rename — `lead` is already the decided word, which is much of why `shiftlead` fits), design §4.1 (session ids), §4.3 (the two packages). **README positioning (TD-059, 2026-09-20):** the first-screen sentence drawn from the neighbours survey is in [ADR 2026-09-20](decisions/2026-09-20-session-desk-neighbours.md) (c); it goes in with the rename.

## TD-071: Org page review, 2026-09-18 — what was built, and the ideas that were not

**Priority:** Low
**Added:** 2026-09-18 (the anchor session; Paul's review of the Org page)
**Owner:** paul
**Kind:** build
**Status:** **Paul, 2026-10-07:** snoozed to 2026-10-14. Open — **items 1 and 2 built 2026-09-23 (PR #499, grinder-ao-1); item 8 is Paul's canvas.** Was: **items 1 and 2 designed 2026-09-23 (the anchor): §4.5a rows *team card: Forget all* and *team header ✉ n*; build them as the rows say, one page PR each or together (`src/shiftlead/ui/`: `group_head.html`, `org.html`, `app.js`); item 8 is Paul's canvas.** Forget all is n Forgets behind one confirm, skipping and naming every card with the dirty / unpushed flag (not refusing the team on one flag — the per-card Forget refuses nothing but a suspended record, `rpc_remove`); the unread sum on a folded header is display only, the mail staying under §4.10. Item 2's open question is answered by the code: a start under the same name moves the old record's mail to the new session (`_supersede`, TD-081), so an unread note outlives the run and lands in the next one's inbox — by design, and now visible on the fold. Was: **items 1 and 2 approved by Paul 2026-09-23: the anchor writes their two §4.5a rows in one design PR, then a grinder builds; item 8 stays Paul's canvas.** Was: a list to pick from, none approved; **items 3, 4 and 5 are settled by TD-095's card (2026-09-21)**, **6 and 7 by the mockups catching up (2026-09-22, grinder-ao-2)**, below. Left: 1 and 2 (each a §4.5a row first — the anchor's design), 8 (Paul's canvas). Built in the same PR as this entry: a team with nothing live keeps its card (Start on it, its dead cards folded, below the live teams and *No team*), the **Teams** strip retired, state glyphs on every pill, *Person inbox* → **Inbox**. **Next:** what is left is item 8, Paul's canvas.
**Location:** `src/shiftlead/ui/` (templates, `app.js`, `app.css`)

**Why:** on 2026-09-18 the page was seven cards, six of them dead, under a strip of three stopped teams: nothing on it said which dead card was whose, and what was running was one card among them. The review fixed the grouping; these are what it turned up beside it, kept here so they are not lost with the conversation.

**Ideas, cheapest first:**
1. ~~**Forget all** on a stopped team's card~~ — **built 2026-09-23 (PR #499)**, as design §4.5a *team card: Forget all* says: — seven exited cards is seven Forget clicks today. A team-level control: §4.5a row first. It should refuse while any of the team's sessions is not ready to close (unpushed work, as `orchestrator-ao-1`'s *305 unpushed* flag showed on 2026-09-18).
2. ~~**Unread mail on a folded team**~~ — **built 2026-09-23 (PR #499)**, as design §4.5a *team header ✉ n* says: — the header of a folded team says nothing about its sessions' unread counts (`tdgrind-1` held 19 on 2026-09-18). A `✉ n` beside the fold count. Unread mail on an exited session is by design as far as it goes: an unread entry never ages out (§4.10), and a *resumed* conversation takes the old record's mail with it (`agent.py`, `_supersede` → `_move_mail`). **Not verified:** what a fresh `ao team start` under the same name does with the dead record's inbox. Most of those 19 were claim notes, stale within the hour — worth deciding whether an unread `note` should outlive the run it was sent to (§4.10).
3. ~~**A roll-up on a live team's header**~~ — **built by TD-095 (2026-09-21, PR #389)**: the header reads `host / repo · 2 working · 1 unseen · 1 idle`, counts in the grid's order, *needs you* its ringed mark. In words, not glyphs — the counts sit beside pills that already carry the glyphs.
4. ~~**Exited cards carry less**~~ — **decided otherwise by TD-095** (design §4.5 *The card's anatomy*, Paul 2026-09-21): every card is the same six rows at one height, a dead one included, so a page of cards reads as a grid; what a dead card does carry less of is controls — its foot leads with Forget (PR #384), and a stopped team folds its dead cards away (2026-09-18).
5. ~~**The mode badge**~~ — **built by TD-095 (2026-09-21, PRs #384 and #391)**: *unattended* is a plain quiet word, *interactive* carries the `person` mark at full strength (reserved for it), the toggle moved into *more*, and the Org filter gained *mine*.
6. ~~**The legend**~~ — **built 2026-09-22 (mockups)**: the Org artboard's note gives the sort order as the state pills themselves, glyph and word, and every mockup pill that is a state carries the page's glyph (a due date or a report's status, marked `plain`, does not).

7. ~~**The mockups are behind the page**~~ — **done 2026-09-22**: the Teams strip is gone from `gen.py`; a stopped team is its own card below *No team* — `ao-grind` *wound down 6h ago* with its sessions folded, `cadence-sweep` a definition no session carries — each with **Start**; the pinned control was already gone (TD-095). As first written: `docs/mockups/gen.py` and the generated `Main.dc.html` / `MainDark.dc.html` still draw the Urgent first / Pinned control and the Teams strip, both retired 2026-09-18 (review of PR #229). Regenerate them with the look-and-feel pass rather than twice.

8. **The look-and-feel pass (Paul, 2026-09-18), in progress on a design canvas** — https://claude.ai/artifact/K1y8Nzy3PJnTCecBVANj8y (private to Paul): the Org page, the Inbox page (TD-069) and the state-icon vocabulary, round 1 dark. Constraints Paul has set so far, which bind whatever is built from it: **a state icon must not look like something to press** — the pause bars and the stop square read as buttons, and the glyphs shipped in PR #226 have the same fault (`‖` idle, `■` exited, and `▶` working is a play button by the same reading, all in `app.css`), so they change with this pass (canvas round 2: a prompt caret for idle, a dashed outline for exited, a pulse line for working); **nothing on a page is a control that parses what an agent printed** — Allow / Deny stay the `PermissionRequest` hook's answer channel (§4.2) and nothing else gets buttons from screen text; **the usage chip says what its numbers are** (`grind 19%·49%` is the profile's 5-hour and weekly windows, and reads as nothing); **a light theme is required, later** — so colours land as tokens (`app.css` already defines them on `:root`, with `html[data-theme="dark"]` overriding), never as literals in templates.

**Done when** each idea is either built (its own PR, §4.5a first) or struck here.

**Related:** design §4.5a (**team groups**, **state icon**), TD-069, TD-070, TD-065 (the undefined `chip` class — same page, same kind of finding).

## TD-112: The adapter contract has grown Claude-shaped and neutrality is untested

**Priority:** Medium
**Added:** 2026-09-22 (the anchor session; the design review)
**Owner:** designer
**Kind:** design-first
**Status:** Designed (2026-10-16, the designer) — §4.3 *The second adapter: Gemini CLI* and *The conformance suite*, read from Gemini CLI 0.64.0's installed package (no run): **hook-fed, not scraped** — `GEMINI_CLI_HOME` per profile, `--session-id` chosen at launch, the per-launch layer through `GEMINI_CLI_SYSTEM_SETTINGS_PATH` (its hooks concatenate), per-process trust (`GEMINI_CLI_TRUST_WORKSPACE=true`) since no hook runs in an untrusted folder; `BeforeAgent`/`AfterAgent`/`Notification` for the state, the terminal title (`CLI_TITLE`) covering a cancelled or failed turn; the contract's new `permission_channel` False — no hook answers a Gemini confirmation, so no Allow / Deny, the terminal is the answer (§4.5a); start context and mail through `SessionStart`/`BeforeAgent` `additionalContext`. The suite: offline fixtures in CI, a live walk by hand on a profile set aside for it. §4.2a, §4.5a, §7 phase 5. Built by TD-784 (the suite, first), TD-785, TD-786. Was: **Paul, 2026-10-16 (the board, line `board:fbc6ebd9`): file it design-first for the designer.** Of the three answers put to him — he runs the spike now with ao-paul, file it design-first for the designer, or leave it for evaluation — Paul chose the designer: the design decides the scraped Gemini adapter's shape (which §4.3 contract methods it implements and which are Claude Code's alone, `state_source: scraped`, what `send` and the doorbell do without a readable composer, whether the record assumes a tool session id) and the adapter conformance suite, and lands them as build entries naming this one in `**Blocked by:**`; the 2026-10-10 note that the spike is an interactive session's is superseded by this decision. Was: **Paul, 2026-10-10 (with ao-paul): agreed, a spike, and integration tests with it.** Run as an interactive session, not a member's lane: a second tool must be installed and signed in (neither `codex` nor `gemini` is on kmaster on 2026-10-10; the sign-in is Paul's account or key), the work is judgement (which of the contract's methods are neutral), and its result is an ADR, not a merge. **Paul, 2026-10-13 (the lessons ADR, `docs/decisions/2026-10-13-agent-product-lessons.md` lesson 10):** Gemini CLI is *"a definite fit"*, so it is the spike's tool; Hermes Agent, Muse Code and Grok Bot are not wanted as tools for now. The lasting product is an **adapter conformance suite**: one set of tests every adapter passes against its real tool (launch, the state it reports, `send`, `composer()`, the doorbell and mail, `title()`), so neutrality is checked on every change and not once by the spike. The spike says which of those a scraped adapter can pass and which belong to Claude Code alone. **2026-10-15 (grinder-ao-2, answering Paul's board question *does the recently added Gemini CLI fit the bill?*):** in part. What was added is dev-cadence's TD-109 (dev-cadence #259, 2026-10-14): Gemini CLI installed on kmaster (`~/.npm-global/bin/gemini`, 0.64.0) and signed in with Paul's Google account, and a Gemini backend for dev-cadence's session *introspection* (`agent_sessions.py`: lists Gemini sessions, their cwd and `gemini --resume <uuid>`). That removes the spike's blocker (a second tool installed and signed in) and gives facts read off a real install (`docs/plans/2026-10-12-session-introspection-adapter.md` in dev-cadence: chats under `~/.gemini/tmp/<slug>/chats/`, a `sessionId` in the first record, no live registry). It is not a ShiftLead adapter: `src/shiftlead/adapters/` still holds only `claude_code/`, and the spike, its ADR and the conformance suite are all unbuilt. Who runs the spike, and when, is put back to Paul on the board. Open — for evaluation. The design's stated moat is one neutral view across tools, and only one agent adapter exists. Since phase 1 the contract (§4.3) gained `composer()`, `title()`, `mail()`, a session id chosen at launch, the doorbell, the trust pre-seed and screen rules, each shaped by Claude Code. Phase 5 puts the second adapter last. Recommendation: a scraped spike now — Codex CLI or Gemini CLI, `state_source: scraped`, no hooks — to see which of the contract's methods are really neutral, what `send` and the doorbell do without a readable composer, and whether the record's fields assume a tool session id. The spike is a branch and an ADR, not a shipped adapter.
**Blocked by:** TD-784, TD-785, TD-786
**Location:** design §4.3, §7 phase 5, `src/shiftlead/adapters/`

**Why:** every Claude-shaped feature built before a second adapter exists is a feature that may have to be unbuilt, and the claim the product is sold on has not been tested.

**Related:** TD-015 (the classifier), TD-052 (mail's adapter step), design §4.3.

## TD-131: Mail, the board and the records are JSON and Markdown files read whole: fine at today's size, and no answer to search over history, counts per filter, or a retention longer than twelve hours

**Priority:** Low
**Added:** 2026-09-24 (Paul: *all this begs the question — do we need a db (with indexing) at some point?*; the anchor session)
**Owner:** paul
**Kind:** decision
**Status:** **Paul, 2026-10-07:** stays parked until its trigger. Open — **the anchor's answer 2026-09-24: not yet, and the trigger is written down.** What holds the data today: the person inbox is one JSON file (`~/.agentorc/person_inbox.json`, 39 entries and 46 KB on 2026-09-24), and a session's inbox is a field of its record, which is one JSON file per session under `sessions/` (`sessionorc/store.py`), both home-owned and read whole on every poll; closed entries are pruned after `MAIL_RETENTION` (12 h); the board is `docs/user_attention.md`, read by dev-cadence's script. At these sizes a read-and-filter in Python is under a millisecond and the page's filter runs in the browser over the rows it has; an index would buy nothing. **What would change the answer** — any one of: (1) mail kept past retention so a person can search history (*what did the techlead say about #517 last week*); (2) counts per filter across every team and host (TD-129's rail) on an inbox of thousands rather than dozens; (3) threads and outcomes joined across many sessions at once (mail stays home-owned across hosts — §4.4a: a node's replicas hold no mail — so this is a size question, not a placement one); (4) a second reader of the same data outside the host agent's process. **The step when one arrives is SQLite**, in the standard library, one file under `~/.agentorc/`, with FTS5 for the text search, written and read only by the host agent as the JSON is today — not a server, not a second process, and nothing a container node needs installed. The stores already sit behind one module each (`sessionorc/mail.py`, the record store in `sessionorc/agent.py`), so the change is local to them and the RPCs and the page do not move. What is not on the table: a database as the source of truth for the design's home-owned facts — those stay files a person can read and back up (`BACKUP_MEMBERS`). **Next:** a decision, and not yet: the trigger is below.

**Location:** `src/sessionorc/mail.py` (the store, `MAIL_RETENTION`), `src/sessionorc/store.py` (one record file per session, its `inbox` a field), `src/sessionorc/agent.py` (`BACKUP_MEMBERS`), `src/shiftlead/ui/app.py` (the filter's `data-find`), design §4.4 (the home), §4.4a (nodes), §4.10 (mail)

**Why:** the question is right and early; answering it now, with the trigger named, means the next person to feel the need knows what was decided and why.

**Related:** TD-129 (the rail's counts, the text search), TD-127 (what a row shows), TD-125 (retention and the FYI), TD-057 (nodes and where mail lives).

## TD-133: Build the team start at the reset — `schedules:` in `settings.yml`, the tick's replay, `ao schedule`, the card's *starts* note

**Priority:** Low
**Type:** feature
**Added:** 2026-09-24 (the designer; TD-026's design round, PR #526)
**Owner:** grinder
**Kind:** build
**Status:** **Paul, 2026-10-07:** snoozed to 2026-10-21. Open — designed, nothing built. Design: §6 *Schedule*, §5 `settings.yml` (`teams.<team>.schedule` since 2026-09-25 — the key moved under the team with the Settings page's design, TD-100 (4); the page's Teams card sets it once built), §4.7 `ao schedule`, §4.5a *team card: starts note*, glossary *schedule*. **Next:** Paul's word first: *not vital, not scheduled* (2026-09-22); ask m-32d769985798 offers to schedule it.
**Blocked by:** decision (Paul) — snoozed to 2026-10-21 (Paul, 2026-10-07); ask m-32d769985798

**Location:** `src/sessionorc/settings.py` (the second key and its validation), `src/sessionorc/agent.py` (the tick's pass beside `_keep_running`: the reset instant from the account's usage reading, the replay with `why: schedule`, `set_settings` taking the key), `src/shiftlead/cli.py` (`ao schedule`), `src/shiftlead/ui/app.py` and the team card template (the *starts* note), `tests/`.

**Why:** the ao-grind team winds down when its lane empties and is started again by a person's hand the next day, and the designer now refills the lane overnight; a start at the weekly reset closes that loop without the host agent reading `org.yml` or inventing a team. Paul decided the shape on 2026-09-22 and called it not vital; this entry is the build, waiting for his word.

**Fix:** three slices, the first two in `src/sessionorc/**` (the techlead reads them, §4.9b).
1. **The key and the pass**: `schedules: {<team>: {start: reset, profile, window}}` parsed with the reserves (a bad entry logged and skipped, never fatal); each tick, for each rule, the instant is the named window's `resets` in that profile's account reading (§4.2a; no reading, no fire); when the instant passes — once per reset, remembered on the home as `schedules_fired: {<team>: <resets>}` so a restart of the agent does not fire twice — every record badged with the team that is `exited`/`closed` by the team's own ending (`out_of_work` set, or a manager's own close after a wind-down), not suspended, not at a ceiling, is replayed from its launch record as rule 2 replays, `why: schedule`, under `RESTART_CEILING`, a seat written held as rule 8's start writes it (§6 rule 3, TD-654; TD-660 builds the held replay in `_replay`); a team with anything live is skipped; no launch records → nothing, logged once. `set_settings` accepts the key; a session's call is refused as today.
2. **`ao schedule`**: the listing with the next instant, `<team> reset --profile --window`, `<team> off`; the client refuses an undefined team (org.yml and the repos' `teams:`) and a label no adapter of the profile reports.
3. **The card**: the *starts* note on a team card with nothing live, from the rule and the reading, by the *stops* formatter.

**Done when** ao-grind, wound down on a Wednesday night with `ao schedule ao-grind reset --profile grind --window week` set, is running again within one tick of Thursday's reset with the manager, both grinders, the designer and the seats as they were last started, each card's `restarts` carrying `why: schedule`; the same team live at the reset is untouched; the team card read *wound down 6 h · starts at the reset · Thu 07:00* before and shows the sessions after; a session's `ao schedule … reset` is refused; TD-026 is closed or re-scoped by Paul's answer.

**Related:** TD-026 (the design and the open scope question), TD-103 (the launch record and the replay this reuses), TD-100 (`settings.yml`, the settings page that would also set this), TD-122 (the account's `resets`), TD-101 (the override shape that stays not wanted).

## TD-156: UI review of the end of a session: after a person's Wrap up, Focus offers Kill in the header and Close only in the side panel, and a concluded team folds the card that is ready to close

**Priority:** Medium
**Added:** 2026-09-25 (raised by Paul: he wrapped up the resumed designer and could not find a Close)
**Owner:** paul
**Kind:** design-first

**Why:** the end of a session is the one moment the person must act (only the person closes it, §2), and the screen they are on when the moment comes does not show them the act: Focus leads with Kill, Close is below the fold, and a concluded team hides the card. A session left open costs nothing in tokens but stays live, holds its worktree and its name, and is one more thing the team's next Start has to close first.

**Status:** **Paul, 2026-10-07:** snoozed to 2026-10-14. **Designed and built 2026-09-25**, in a cloud session with Paul, from his screenshot of the designer's Focus (the header over three lines, buttons on two of them, *unattended* where *Take over* should read) and his ask: *simplify the screen, hide the rarely used info under something expandable, and fix the button layout*. The design is §4.5 *The Focus screen's anatomy* and *What Wrap up promises*, and the §4.5a rows **Close session**, **more ▾**, Wrap up, Kill, Copy / Paste, the side panel, grants, controllers and **team groups**; the mockups are `Focus`, `FocusOrc` and the new `FocusReady` artboards, and the Org artboard's fold. What each question got: **Next:** Paul's interactive UI review, likely a cloud session (board line, due 2026-09-30); the design changes it yields go to the designer or a grinder afterwards.

- (a) **Focus after a wrap-up**: yes — the header is two lines, and the acts line's first button is the next act by the card's rule: **Close session**, outlined, the moment Ready to close passes on an `idle` or `exited` session, with ***ready to close ✓*** on the identity line in the card's words; Take over is the next act on an unattended session otherwise; Kill went into **more ▾**, last and red, with Pop out, Copy, Paste, Hand back and Copy tmux command. Message, Wrap up, Open shell here and the editor button stay plain on the acts line. The grants and controllers chips left the header for the Session card.
- (b) **a concluded team's fold**: a concluded team **never folds** — only a team with nothing live does (`live`, not `stopped`, is what the fold keys on) — and every team header counts *n ready to close* beside its states.
- (c) **what Wrap up promises**: the button's hover and the press's toast read *asks it to finish, push and report — the same prompt the policy sends; you close it when Ready to close passes*. The rule that a person's Wrap up is only the prompt stands.
- (d) **Kill's place**: behind more ▾ whatever the state, as on the card; Close is the act when the checklist passes, Kill one menu away for a worktree that must be kept.
- (e) the mockups: regenerated; `FocusReady.dc.html` is the after-wrap-up case.
- (f) the fold control: beside Start, *▸ n sessions* / *▾ n sessions*.
- (g) **two cards named designer-ao-1**: **not reproduced** — no live Org in a cloud session. The client's swap is by id (`old.replaceWith(fresh)`), so two elements need two inserts of one id; the events handler now drops any second element with a card's id after a delta as a guard. **Paul, 2026-09-25: the doubled card is handled under its own TD**, so it leaves this entry's live look.
- Fixed beside it: the header's mode button read *unattended* instead of *Take over* — a card-era line rewrote every mode button's text to the bare mode word after `mode_act` had been set.
- Every side card is a fold remembered per browser; Session starts folded, its summary the profile; the stops control sits on the identity line only while a stop time is set, and in the Session card as *none — set* otherwise.
- **Paul's second round, the same afternoon** (§4.5 *Whose session it is*): the identity line is the **name alone** (no host / repo, the tool's title only when it differs — the designer's read *designer-ao-1* twice); what the session says it is doing left the header for a **Working** card at the top of the side panel, beside Reports (*we already have a Reports card for finished work*); **Close session** and *ready to close ✓* are drawn only on the person's **own** session — interactive, or no team badge — since *I would expect the team members to run themselves and I would be responsible for interactive sessions I started*; a team member's Ready to close is **folded** by default and its note says its team closes it, its Close being the more ▾ entry; and **no team-level close** for the finished ones — the anchor's *Close finished* proposal was declined.

**Reviewed by Sonnet, four rounds in the cloud session (2026-09-25), READY at round 3 and confirmed at round 4.** Taken: the card's Close session gated on *own* like Focus's (round 1); the Forget all row's stale fold label, a member's checklist losing its small Close so more ▾ is the one place (round 2); the stops row as the Session card's control set or not, Forget all into the team header's right-hand cluster with Start and the fold, the first-paint outline following *own* (round 3); the stops row's old sentence on Focus's empty state (round 4). Overruled: "the branch deletes shipped sections" (a two-dot diff against a moved main; main was merged in instead) and "the header's *n ready to close* should count exited sessions" (an exited session's act is Forget, not Close — the row now says *idle*).

**Left for Paul's live look (board line):** the two lines at 1440 and 1280; Close session appearing on a wrapped-up own session without a reload; the fold beside Start; a concluded team's cards in view with *1 ready to close* in its header.

**What happened, as the seed for the review**, 2026-09-25 15:15Z on the designer resumed by Paul (TD-155's record):

1. Paul pressed **Wrap up** on Focus. The session declared *out of work* (`ao progress none`, the wrap-up's words), the record reads `idle` with every Ready-to-close check passing (*tree clean*, *branch pushed*, *no subagents running*, *outcomes reported*). Nothing closes it: a person's Wrap up is only the prompt (§4.5a *Focus: Wrap up*); the policy's `run_until` path is what kills after a wrap-up, and it applies to a stop time, never a press (`_enforce_stop_times`). *Closing is your act; the checklist only says when it is ready* (the side panel's own note). That is the design (§2, goal *only the person closes it*) and this entry does not question it.
2. On **Focus** the header reads *Pop out · Open shell here · VS Code · Copy · Paste · Message · Wrap up · ✕ Kill* — Kill in red as the only stop in sight. **Close** is a small button in the side panel's *Ready to close* card (`focus.html`, `#closebtn`, enabled by `app.js` when every check passes and the state is `idle` or `exited`), below the Reports and Inbox panels, where Paul did not find it. §4.5a says exactly this — *Focus side panel: diff / log / PRs, run-log link, Close* — so the build is faithful and the design is what the review has to change: the card's rule (§4.5 *The card's anatomy*: the next act is the one outlined button in the foot, **Close session** on *ready to close ✓*) has no counterpart on Focus, whose header shows the same controls whatever the session's state.
3. On **Org** the card was there but hidden: the `ao-grind` team had concluded (its manager exited), and a team with nothing live folds its cards behind *n sessions — show* (§4.5a *team groups*). The designer, `idle` and ready to close, counts as nothing live for the fold (`live: 0 if g["stopped"]`, `app.py`), so the header read *concluded 6m ago · out of work · 1 idle · 1 on call · 3 exited · 5 sessions* with a **Start** button, and Paul read it as *already closed*. The folded card carries the caption *ready to close ✓* and the **Close session** button, but the fold hides both, and the header's counts do not say that one of the five is a live session waiting for a press.

**What the review has to settle:** (a) **Focus after a wrap-up** — when Ready to close passes, does the header carry **Close session** as the outlined next act (the card's rule applied to Focus), with Kill behind *more* or kept as the danger it is; and does the *out of work* badge in the header say *ready to close ✓ · Close* the way the card's slot and caption do; (b) **a concluded team's fold** — a session still there (`idle`, `exited` with a pane) is *live* for the fold, or the fold header carries *1 ready to close* with the Close a press away, or the folded group unfolds itself while it holds anything a person must still close; (c) **what Wrap up promises** — the button's label or its confirm says what happens next (*asks it to finish and push; you close it when the checklist passes*), so a person is not left waiting for a close that is theirs to press; (d) **Kill's place** — whether a `ready to close ✓` session should offer Kill at all, since Close does the same kill after the checks; (e) the mockups (`docs/mockups/gen.py`) for Focus at *ready to close* and for a concluded team holding one; (f) **the fold control's place** (Paul, 2026-09-25): *n sessions — show* sits apart from the team card's other controls, and it took a minute to find — move it beside **Start** so a team card's buttons are together, and give it a show / hide icon (a chevron or an eye) with the count, since the words alone did not read as the way in; (g) **two cards named designer-ao-1** were on the page before Paul closed the resumed designer. The store holds one record and the host agent log reads *designer-ao-1 superseded the exited session of the same name* at the resume (08:06:16 MDT) — TD-145's rule, promoted 2026-09-24 20:16, before the resume — so the doubling was the page's: a card the browser kept after the supersede, or the same id drawn twice by the group and a fold. Reproduce it in the review (resume an exited team member from Focus, watch Org) and say which; the record side is §4.1's rule, one name per team, and held.

**Done when** Paul, having pressed Wrap up on a session, sees on the same screen the one press that closes it once it is ready, and a team card with a session waiting to be closed says so without unfolding — built; his live look closes it; (g) is another TD's.

**Related:** §4.5 *The card's anatomy* (the outlined next act), §4.5a *Focus: Wrap up*, *Focus: Kill*, *Focus side panel: Close*, *card: Close session*, *Org: team groups* (the fold), §4.9a (a declaration is not a state), TD-081 / TD-145 / TD-155 (the resumed session this was seen on), TD-154 (reading a transcript, the other reason the session was resumed at all), TD-095 (the card's foot and slot).

## TD-290: A UI change goes to the person unchecked — the grinder verifies it, a reviewer only if needed, and what is left is a `look`

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-02 (the anchor, from Paul's walk of the pages: *we should probably have these types of verification items sent to a reviewer (and then to me if needed)*; 2026-10-03: *I would expect a grinder who makes ui changes to verify them itself before sending to a reviewer, and only send to a reviewer if needed*; *a distinction between "watch" items (issues that have arisen once and need to be seen again before acting) and "look" items (items that are for me to take a look at) — have the look items go to either steering or "needs you" instead of creating a new category*)
**Owner:** designer
**Kind:** design-first
**Status:** **Designed 2026-10-03**, twice (the designer). In the morning, on Paul's three points (PR #947; the two choices steered as `m-71d3c709e116`, Paul: *Go with both defaults: the techlead seat is the reviewer … and a look the techlead leaned Works on closes as Works at its Due date*): what was left for the person was a board `look` line, drawn under Steering with a default and closed by its team at its `Due:` — **superseded the same evening**. In the evening, on Paul's note `m-d435ff0d831a` (PR #977): a look that reaches the person is mail. Design as it stands: §4.9b *A UI change is verified by its builder* (the builder's scratch home and the **UI check** in its PR; the live copy read by a `watch`, never pressed; the reviewer is the techlead seat, its three replies; what is left is nothing, a `steer` with the default *Works* where the techlead leaned, or an `ask` with *Works* / *Not right* where nobody could settle it; a board `look` only without agentorc's mail), §4.10 *A look* (`shots`), §4.7 `--shot`, §4.5 screen 6, §4.5a **Inbox row: a look** and its **Send to reviewer** (`inbox_hand`), the glossary's *look* and *scratch home*, mockup `Inbox.dc.html` (`docs/mockups/reviews/2026-10-03-td290-look-rows.png`). Nothing is asked of dev-cadence. Builds: TD-291 (the builder's half; built and archived), TD-292 (the mail's and the Inbox's half).
**Blocked by:** TD-292
**Location:** design §4.5a (a board row's answers, **Works** / **Not right…**; the Inbox's sections), §4.9 (roles), `docs/briefs/grinder-ao-*.md` (*never run `ao ui`*; *a fix that needs a live look is … a `watch` line*), cadence §3.5 (the live look, written as a `watch`); the anchor's Playwright set-up (`~/ao-shots/pwlib`, `~/ao-shots/scripts/`, headless Chromium)

**Why:** today no one checks a UI change before the person does. A grinder may not run `ao ui` (its brief), and the live copy changes only at the anchor's promote, so every merged UI change ends in a `watch` line asking the person to look, and most of what those lines ask is mechanical: a label reads so, a button is greyed, a toast says *x*, a size takes. The board held ten at once on 2026-10-02 and Paul walked them page by page over an evening. A headless browser can make most of those checks: Playwright with Chromium loads a page, waits for its scripts and the terminal, reads values and takes screenshots; it settled TD-288's fresh-load half. And the word is wrong: `watch` means a thing seen once that must be seen again before acting (a session's §6 check), while these lines ask the person to look.

**Fix:** design first. *(Point (3) below is the entry as it was raised; Paul's note of 2026-10-03 evening replaced it — what is left for the person is mail, a `steer` or an `ask`, and never a board line where agentorc's mail reaches: see Status.)* (1) **The grinder verifies its own UI change.** Before the PR, against a scratch UI: a temporary `AGENTORC_HOME` and a private port, the same kind of thing as `pdm run test`, never the live units, so the brief's rule changes from *never run `ao ui`* to *never run it against the live home*. It drives the change with Playwright and puts the screenshots and the values it read in the PR. After the promote, it makes a read-only look at the live copy. (2) **A reviewer only if needed:** for what the grinder cannot settle by itself, such as a judgement of layout or wording, or a state it cannot stage. Say who that reviewer is (the techlead seat, or a reviewer role with a browser) and what it sends on. (3) **What is left for the person is a `look` item, not a `watch`** (dev-cadence TD-082 adds the kind). It carries the screenshots, says what was checked and what is the person's to judge, and draws in **Needs you**, or in **Steering** when it carries a default and a bound. It does not get an Inbox section of its own. `watch` keeps its meaning. (4) A **Send to reviewer** answer on a `look` row, for when the person would rather have it checked first, and what that writes on the board. Keep it one rule: a look that a machine can settle never waits on the person.

**Done when:** the design says who verifies a UI change and how the rest reaches the person, the grinder briefs say it, the build entries are on the ledger with a `Blocked by:` line, and the next merged UI change goes through it.

**Related:** TD-255 (a live look's answers), TD-244 (the page walks), TD-288 (settled half by a headless look), cadence §3.5.

## TD-292: Build the mail half of a look

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-03 (the designer, TD-290's build; re-scoped the same evening on Paul's note: a look is mail)
**Owner:** grinder
**Kind:** live-check #1007
**Waits:** event — a builder's look after a page PR (a leaned-*Works* steer) and the person's **Send to reviewer** on one
**Status:** **Read 2026-10-13 (the anchor): a steer carrying shots lapsed with no press, but it is no builder's look.** The live Inbox (headless GET of `/inbox`, read only) draws one `/shot/` screenshot: designer-ao-1's TD-630 design `steer` (`m-10d827d892f8`, `shots: [2026-10-13-td630-add-entry-attach.png]`, the route answering it) closed with *you let it go with its default* and an `outcome · done`, now under *FYI*. That is the steer-with-shots half of the mechanism (the thumbnail drawn on a `steer` row, the lapse to its default with no press) seen live; the *Done when*'s three readings still wait on a builder's look after a page PR (a leaned-*Works* `steer`) and on Paul pressing **Send to reviewer** — no entry is `handed` as a look. **Read 2026-10-12 (the anchor):** no look since — the live Inbox (headless GET of `/inbox`) draws no `/shot/` screenshot, its one *Steering* row is no look, and no entry is `handed`. **Read 2026-10-09 (the anchor): the first look arrived, and its *ask* half holds.** grinder-ao-1's `m-500957934330` (05:13Z, `--kind ask`, re TD-478, two `shots`) is on the live Inbox (headless GET of `/inbox`, read only) under *Needs you* (`data-section="needs"`) with its two screenshots drawn from `/repo/agentorc/shot/…` (the route answers 200, `image/png`) and the pair drawn as **Works** / **Not right…**, beside **Send to reviewer**. Still to read: a leaned-Works look as a `steer` under *Steering* that lapses to *Works* with no press (none has been sent), and **Send to reviewer** pressed by Paul (no entry is `handed`). **Read 2026-10-08 (the anchor, TD-426):** still no look: none of the person inbox's 124 entries carries a shot. **Built; a live check waiting on an event (grinder-ao-2, 2026-10-05).** #1007, the last slice, is live (`ao promote status`: live f8090c2). Read that day with a headless GET of the live Inbox: no look mail is on it — no row draws a `/shot/` screenshot and none carries a seat's reading — so none of the *Done when*'s three readings can be made yet. They wait on the first look a builder sends after a page PR (design §4.9b), and Send to reviewer's on Paul pressing it on one. Nobody can cause that on purpose, so the entry is the anchor's until a look has arrived; whoever reads one then writes what it showed here. Before: the board row's pair on cadence's kind `look` is built: PR #975 (`LOOK_KINDS`), slice 1 of the entry as it was first scoped. Re-scoped 2026-10-03 evening (PR #977) to the mail rows. Slice 1 built — PR #980 (the grinder brief's three cases as mail; the techlead preset's two sentences). Slice 2 built — PR #983 (`ao msg --shot`; `shots` on the envelope, checked whole against `docs/mockups/reviews/<name>.png`). Slice 3, the row, built — PR #982 (`look_shots`, `look_pair`, `GET /repo/<repo>/shot/<name>.png`; this host's registry only, another host's is TD-300). Slice 4a, the host agent's half of Send to reviewer, built — PR #990 (`inbox_hand`, `snoozed_for`, `look`, `looked_by`; the techlead preset's *A look handed to you*). Slice 4b built — PR #1007 (the page's **Send to reviewer**, the seat read from `org.yml`; the snoozed row's *with <seat> since <time>*; the handed `ask` read as a look; the `looked_by` line above the answers). Merged, live look pending: the *Done when*'s Send to reviewer, with a real techlead seat.
**Location:** `docs/briefs/grinder-ao-1.md` (the paragraph *A change to a page is verified by you*, the one brief that carries it), `src/shiftlead/briefs/techlead.md` (*A screenshot ask*: *The builder then puts a board line before the person*; *what reaches the person is the builder's board line, one road*), `src/shiftlead/cli.py` (`ao msg --shot`), `src/sessionorc/mail.py` and `src/sessionorc/models.py` (`shots`, `snoozed_for`, `read_by` on an envelope), `src/sessionorc/agent_inbox.py` (`inbox_hand`), `src/shiftlead/ui/inbox.py` and `src/shiftlead/ui/app.py` (the row, the screenshot route); design §4.9b *A UI change is verified by its builder*, §4.10 *A look*, §4.7, §4.5a **Inbox row: a look** and **Send to reviewer**; mockup `docs/mockups/Inbox.dc.html`, `docs/mockups/reviews/2026-10-03-td290-look-rows.png`

**Why:** Paul, 2026-10-03 (his note `m-d435ff0d831a`): *rework TD-290 so a look that reaches the person is mail, not a board line*; his reason there: *a steer already means go with the default at the bound unless I say otherwise, which is what the leaned-Works look wants*. The briefs TD-291 landed (#972, #973) still tell a builder to write a board line.

**Fix:** as §4.9b's table and §4.5a's two rows say, in slices, each its own PR. (1) **The briefs** (touches `docs/briefs/**`: the reader's read; needs nothing built): this repo's grinder brief says the three cases as mail, and the techlead preset's two sentences name the builder's message in place of its board line — nothing when every check held; `ao msg person --kind steer --default "Works" --bound <no shorter than a night> --about TD-NNN "…"` naming the techlead's reply and the screenshots' paths where it leaned; `--kind ask --answer "Works" --answer "Not right: <what>"` where nobody could settle it — sent after the merge, the first paragraph the one thing that is Paul's to judge; a *Not right* reply is ledgered as an entry of its own and its outcome reported; no board line, but the `watch` for a read of the live copy stays. (2) **`--shot`** (touches `src/sessionorc/**`: the reader's read): up to four, a `.png` under `docs/mockups/reviews/` of the sender's repo, only on a `steer` or an `ask` to the person, refused otherwise in words; `shots` on the envelope; the briefs of slice 1 then name the screenshots with it. (3) **The row**: `GET /repo/<repo>/shot/<name>.png` serving that one directory at `origin/<default>` and nothing else; thumbnails under a look's text, on the `steer` and the `ask` row alike; on the `ask`, the pair *Works* / *Not right: <what>* drawn as **Works** and **Not right…**. (4) **Send to reviewer** (touches `src/sessionorc/**`): `inbox_hand {id}`, the person's alone — the `handed` `ask` to the sender's team's techlead seat with the look's text, `shots` and id; the look snoozed with `snoozed_for` until that debt closes and listed *with <seat> since*; the outcome's line back on the look as `looked_by`, drawn above its answers; the refusals in the host agent's words; the techlead preset says how a handed look ends: one outcome whose text is its reading, or `blocked`.

**Done when:** a builder's leaned-Works look reaches Paul as a `steer` under *Steering* with its screenshots drawn and lapses to *Works* with no press, one nobody settled is an `ask` under *Needs you* with **Works** / **Not right…**, and **Send to reviewer** fills the techlead seat and brings the look back with the seat's reading.

**Related:** TD-290 (the design), TD-291 (the builder's half), TD-255 (a live look's pair), TD-218 (a handed entry), TD-127 (a message's shape).

## TD-297: The parts of the live looks of 2026-10-03 nobody could see

**Priority:** Low
**Type:** debt
**Added:** 2026-10-03 (the anchor, with TD-296)
**Owner:** grinder
**Kind:** live-check
**Status:** Open — **Owner anchor from 2026-10-05 (grinder-ao-1): what is left is out of a scratch home's reach** — a check that waits on a logged-in session or on GitHub is made by whoever has them, and a live event when it happens. **A team stop time on members' cards does not hold** (pressed the same day with a shell member: the record and Focus take it, the Org draws it nowhere): TD-335. Every press still listed needs a logged-in claude-code session (a scratch home's `CLAUDE_CONFIG_DIR` is its own, so none logs in: a role chip's `prompts:`, Resume, a permission card's Deny and keys, a team Start or `ao new me --team`, `stalled?`, a second agent session, Add entry's session, a live team's reserve line) or GitHub (`auto`'s green checks, the Org's *over its line*), and the rest are live events, read when they happen. **seven presses seen working 2026-10-04 (grinder-ao-1)** on a scratch home (`scripts/look_home.py`, its repo given a local origin so Ready to close passes), in headless Chromium: Close session is in the Focus header once Ready to close passes, drawn with no reload, and Close takes the record to `closed` (TD-156); Close and Kill each leave one *pane is gone* line after one *retrying in 1s*, and no websocket opens in the six seconds after (TD-029); on a killed session `#compose` is disabled with *this session's process has ended*, and **→ Send** sends nothing and says so (TD-047); Message… from a card's *more ⋯*, ✉ Message in Focus and Reply on an Inbox `ask` row each open the mail dialog (TD-052); `j` rings an FYI note and `x` presses its Dismiss, which confirms and removes it (TD-124); Put on the board opens a four-row box where Enter adds a line and Ctrl+Enter puts it on, the lines joined into one line at the top of *Needs the user*, one commit (TD-281); `ao new --unattended --at +2h` draws a *scheduled · starts <time>* card whose *more ⋯* holds Start now and Cancel, Cancel confirms and forgets the record, and `ao at <id> now` starts it on the next tick (TD-152). Those are struck from the Fix's list. **Three more the same day, on the same home:** a board row's answers are drawn in the order written with the default marked *DEFAULT*, the body without the tail, and **Go with it** in the foot after Reply; a click on an answer, **Go with it** (its confirm names the default), `1` and `4` on a ringed row and `g` each write the decision as one commit (`agentorc: decide <head>: <answer> (session look)`) and toast *decided: <answer> — committed on the board, not pushed*; **Not right…** on a look line opens the Reply composer with *Not right:* begun and writes the words as the decision, toasting *no entry was handed on: no team services repo: there is no techlead seat …* on a home with no techlead (TD-255); *due this week* on Settings → You saves `board_show: 7d` and the Inbox's line reads *showing board items due this week · 1 not shown, the next due Jan 1 — show* (TD-220); **Drop** on a claim with no PR is under the Reports card's *more ▾* in Focus, its confirm names the lease and the one session that may claim it again, and the session's inbox gains *your claim on TD-001 was dropped by the person — the lease is gone* (TD-150, TD-028). Struck too. **Snooze on an `unpushed` row** (an exited session whose repo is ahead of origin) offers *in 1 hour*, *tomorrow 08:00* and *a date and time…*, and the row moves under *1 snoozed — show* (TD-079); struck. The trail row the same home drew for an `unpushed` row that ended by a push read *resolved: resolved ×2*, where §4.10 says *pushed*: TD-308. **Three more 2026-10-05 (grinder-ao-1)**, on a scratch home whose repo had a local bare origin and a `promote:` block (`check` reading a file the `run` writes), in headless Chromium: a `run` that exits 1, pressed with `ao promote repo`, draws a counted *Needs you* row reading *failed: promoting cdc6c41 — the run ended and live is 04cd6aa, not cdc6c41 (exit 1). Nothing is promoted for repo until this is dismissed or a press succeeds.* with the log's tail; a second merge on origin is read (*main 4359875, 2 ahead*) and promotes nothing while the failure stands; **Dismiss** toasts *the failure is cleared: promoting goes on* and the row is a plain promote row again (TD-132 (3)); **Promote** on the row, with a `run` that works, toasts *promoting repo to f556e05 — checks read unknown, pressed through* and live reaches main; with `auto` on, `ao promote repo --back` makes the earlier commit live, and `ao promote status` and the Inbox row (under *Needs you*) read *live 04cd6aa, rolled back from f556e05 … · auto on · held*, with FYI notes *rolled back repo to … — main is n commits ahead*; the row's **Dismiss** toasts *the hold is ended: live stays where it is, and promoting goes on* and the hold is gone from `ao promote status` (TD-226); **Start** on a stopped team whose only member's lane matches nothing opens the dialog *Start lk-grind? · Nothing to pick: every member's lane is empty, so the team will wind down as soon as it starts. · grinder: 0 · Cancel · Start anyway*, and Cancel closes it with the team still stopped (TD-265, TD-262; *Start anyway* not pressed, since it launches claude-code sessions a scratch home cannot log in). Struck. Not pressable there: `auto` promoting by itself (TD-132 (2)) needs green checks, which a repo with no GitHub remote never reads (*checks unknown*); **Snooze on a `stalled?` row** needs a hook-fed session, since only those stall (`agent_tick`: a shell is `working` or `idle`). **Two more the same day, on a second scratch home:** on an unattended shell session with a team, Focus draws **Take over**; once pressed, *more ▾* holds **Hand back** (a session with no team and no controllers reads *Switch to unattended* there, as `cards.py` words it), and Hand back turns the record unattended again, the composer closed (TD-096); on a team the home's `org.yml` defines, with the home made a work tree, **Members…** opens *Members of lk-org*, *stopped: an edit is the definition only — the next Start brings it*, the name prefilled *grinder-lk-2*, and **Add member** lists it, toasts *org.yml: added grinder-lk-2 (grinder) — the team is stopped: its next Start brings the member*, writes one line into `org.yml`, and the home commits it as *org: lk-org added grinder-lk-2 (grinder)* (TD-268). Struck.
**Waits:** event — each press left needs a logged-in session or GitHub, and each live event is read when it happens
**Location:** the closed look lines of 2026-10-03 in `docs/user_attention_archive.md` (each names its TD); design §4.5, §4.5a

**Why:** the looks were made read-only against the live copy, and presses only on isolated scratch homes, two of which stopped early (TD-298). What follows was not seen. None waits on Paul.

**Fix:** make each on a scratch home (TD-291's `scripts/look_home.py`) where a press settles it, or read-only the next time the live event happens; a failure is its own entry. **Presses:** a role chip sending `prompts:` (TD-170, needs a claude-code session); Resume on an exited card (TD-155); Deny's *why?* box (TD-117); `a`/`d` keys on a permission card; New session's Start and Wind down for a team (TD-173, TD-276) and `ao new me --team`; Snooze on a `stalled?` row (TD-079, needs a hook-fed session); Promote's `auto` (TD-132 (2), needs a repo whose checks read green); a profile reserve's chip line after Save, the Org's amber *over its line* (TD-244 Settings); Add entry → Hand to the techlead and → Open a session on the live repo page (TD-219, TD-283, with a logged-in session); a second agent session in a directory refused (TD-152; a shell is exempt, so it needs an agent session). **Live events:** an early or repeat restart mark reaching the person, with the row naming it (TD-249's last case); a member steering you with nothing else to pick (TD-274's wind-down part); *merged without its read* and either row's Dismiss (TD-258); the Inbox restart row, `ao restart`, an exited Focus banner (TD-250); a manager fill by cause (TD-259); a *reported* reading under a minute after a new session (TD-233 s2) and *· projected n%* (TD-233 s4); *closed by you / by manager-ao-1 / itself* and a wind-down within ten minutes (TD-265); an orphaned question (TD-216); the team-start row on the Inbox (TD-244 Settings); the origin row turning live with no reload after a pull, and agentorc's checkout catching up once the anchor is idle (TD-281, TD-263); *earlier turns* on a transcript of more than 20 turns (TD-166); four restarts in two hours and *early: nothing reported done* (TD-249); *brief: changed* in `ao status -v` (TD-217); a SendMessage to a worker refused (TD-064); a metered profile (TD-151, once one is declared).

**Carried from TD-255 at its archive (2026-10-03), a design gap, not a look:** a team whose manager is on call has no round, so a live look decided *Works* reaches no holder to close it, and the pair has no help entry. Whoever designs TD-290's rework (looks as mail) settles who closes a decided look; until then the anchor closes them.

**Done when:** each is seen working or ledgered as its own entry.

**Related:** TD-296, TD-291, TD-244.

## TD-299: Run the guardians and contractmatch teams inside their devcontainers

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-03 (the anchor; Paul lifted the guardians hold the same day: *i think guardians and contractmatch are good candidates for using teams within a container*)
**Owner:** paul
**Kind:** build
**Status:** **Read 2026-10-09 (the anchor): the owner is paul.** contractmatch's half is done: since 2026-10-06 its grinder gates a frontend PR in its container (`/home/kmaster/contractmatch/.agentorc.yml`). What is left is guardians, (5), and every step of it is the person's: `/home/kmaster/guardians-devenv/.agentorc.yml` does not exist, `hosts.yml` has no guardians node, and `org.yml` has no guardians `place:`. A board line asks for it. **Read 2026-10-08 (the anchor, TD-426's run):** nothing in it is a member's now — the node's env and logins, `org.yml`'s `place:`, `ao host up guardians` and contractmatch's brief are the person's, or the person's own session's. In progress — (1) and (2) done 2026-10-03: the image installed Claude Code as root, so `developer` had none; contractmatch #108 moved it under `USER developer`, and `ao host rebuild contractmatch` brought up container `9e66a47155fa` with `claude` 2.1.289 at `/home/developer/.local/bin`, the agent on the live wheel (`8b1da05d4214`) and the link up. The image's postCreate *copying credentials from stuff_for_containers_home* copies nothing on the node: that mount is a tmpfs there. **(3) and (4) done 2026-10-03:** the node's `env` (values unquoted: `docker --env-file` keeps quotes) and three profile logins; `place: {cm-grind: contractmatch}` and the project's node path in `org.yml`; the team started on the node, and grinder-cm-1 merged #110–#113 from inside (none touches `frontend/`: a frontend PR checked inside is still to come). Found on the way: TD-301 (hooks, built and live), TD-302 (a node session's own word is refused) and TD-316 (a node rebuild replaces its container and ends the sessions inside without a word: grinder-cm-1 and manager-cm-1 at 23:38). **Left:** contractmatch's brief still sends frontend work to the devcontainer (Paul's cm session is changing it); then guardians, (5). guardians works from the umbrella repo (Paul, 2026-10-03): its team is defined in `guardians-devenv` against that repo's ledger and board alone
**Location:** `~/.shiftlead/hosts.yml` (`nodes:`), `~/.shiftlead/nodes/<name>/` (`env`, `profiles/`), `~/.shiftlead/org.yml` (`place:`), `/home/kmaster/guardians-devenv/.shiftlead.yml` (new), guardians-devenv's `docs/briefs/team-setup.md` and TD-028, `/home/kmaster/contractmatch/.agentorc.yml`; design §4.4a *A container node*, §4.9 *Where a repo's team lands*, §10

**Why:** the hold of 2026-09-23 is lifted: guardians' five repos are checked out at `~/guardians-devenv`, §10 decided that a devcontainer running an `agentorc-agent` is a node, and the contractmatch node reads *running · link: up — linked* (`ao host status contractmatch`). A team in its container gets the project's own toolchain (contractmatch's `frontend/**` PRs are left open today because kmaster cannot run `flutter analyze`), and an unattended worker's reach is the container's mounts, with a repo-scoped token, never the person's credentials. Nothing has run a team on a node yet: TD-057's *Done when* is that run.

**Fix:** (1) **contractmatch first, on the node already up:** check that the image has what a worker runs (`claude`, the toolchain, Python 3.12+ on the agent's PATH); (2) `ao host rebuild contractmatch` at the live version if the node's build is behind; (3) **Paul's, once per node:** `~/.agentorc/nodes/<name>/env` (`0600`) with a fine-grained GitHub token scoped to the repo, the author name and email, and the repo's own needs (contractmatch: a Doppler service token), and one `claude` login inside per profile the node's roles name (`/agentorc/profiles/<profile>/`); (4) `place: {cm-grind: contractmatch}` in `org.yml` with the team wound down, then Start, and watch one entry go from pick to merged PR from inside, a frontend one included; (5) **guardians:** a `nodes: guardians: container: {devcontainer: ~/guardians-devenv}` entry and `ao host up guardians`, step (3) for it, then `guardians-devenv/.agentorc.yml` with a team in the manager / techlead / members shape the other repos use (by a PR in that repo), `place:` to its node, and the team's briefs written in guardians-devenv (2026-10-09: the old `docs/briefs/guardians-orchestrator.md` is retired to `archive/`; guardians-devenv's `docs/briefs/team-setup.md` and its TD-028 carry the steps, run by a session there). The five-repo constellation (cadence §9): the team works from the umbrella's ledger and board alone (Paul, 2026-10-03).

**Done when:** cm-grind and a guardians team each run on their node, each with a PR merged from inside, and TD-057's *Done when* reads met.

**Related:** TD-057 (the home and node split), TD-229 (repo-defined teams and `place:`), TD-296/TD-298 (2026-10-03's looks).

## TD-300: A look's screenshots from a sender on another host draw their names alone

**Priority:** Low
**Type:** debt
**Added:** 2026-10-03 (grinder-ao-2, TD-292 slice 3)
**Owner:** grinder
**Kind:** live-check #993
**Status:** **Read 2026-10-08 (the anchor, TD-426):** now `Blocked by: TD-299`, the entry that places the first member on a node, so it leaves the lane until then. **A live check waiting on an event (grinder-ao-1, 2026-10-05):** #993 is live, and its *Done when* needs a look sent by a member running on a node, which no grinder can cause — TD-299 places the first such member; whoever reads the home's Inbox after that look reads its thumbnails there. Built — PR #993 (`sessionorc.shots`, `host_shot` and the link's `shot`; the row's `?host=` and the route reading there). The *Done when* waits on the first look from a member on a node (TD-299 places one).
**Blocked by:** TD-299
**Location:** `src/sessionorc/shots.py` (the one reading), `src/sessionorc/agent_remote.py` (`host_shot`), `src/sessionorc/agent_link.py` (the link's `shot`), `src/shiftlead/ui/inbox.py` (`shot_root`, `shot_bytes`, `look_shots` with the sender's host), `src/shiftlead/ui/app.py` (`repo_shot`, `?host=`); design §4.4a *A look's screenshots on another host*, §4.5a **Inbox row: a look** (*through the host whose registry holds the repo*)

**Why:** the row (#982) serves a screenshot only from a checkout in this host's registry. A look sent by a member running on a node (TD-299, `place:`) names a repo that the node's registry holds and the home's does not, so the row draws the file names and no images.

**Fix:** a host-agent read of one `.png` under `docs/mockups/reviews/` at `origin/<default>` of a registered repo, refusing any other path; the route asks the host whose registry holds the sender's record's repo (`repos_of`), as the place check does. Tests: a node-placed sender's look draws its thumbnails.

**Done when:** a look from a member on a node draws its screenshots on the home's Inbox.

**Related:** TD-292 (the row), TD-299 (teams on nodes), TD-057 (the home and node split).

## TD-410: Every team Start fills the on-call manager, which reads ~60k tokens of design to find nothing due: 14 fills since 2026-10-03, all *nothing to do*

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-08 (the person's session, from Paul's *what does the manager still do — do you recommend moving it to Haiku?*; TD-118's step (4) dropped in its favour)
**Owner:** designer
**Kind:** design-first
**Status:** Designed 2026-10-08 (the designer, PR #1285): design §6 rule 3 *A Start writes the seat and never fills it* — `ao team start`, and an Apply's create of a manager the run lacks (§4.9c), writes a manager on call's record as a held anchor's is written (TD-386's `create` with `held`): `closed`, no pane, the launch record, `seat: {trigger: team}`, no `seat_held`; its id is the name's, so the members' `controllers` name it before it has run, as they name the techlead's, and the fill keeps the id; the Start's line reads *on call — comes when a member needs a reading*. **A Start with something already due fills at once** by the rule alone: the first tick reads the kept mail's question (the held create moves the mail as a fill does — it did not before, so the build adds it) or a member's `needs-you` as any seat's cause; the Start is never a cause. **A fill says why it came**: the fill's prompt is the brief with one closing line, *[agentorc] you are filled for: <by> — <member or ask id>*, so the first act is the reading. **First reads** (§6 *What is left is judgement*): checked against the code — the on-call template `manager_on_call.md` (1,400 words) already names no section of the design, reading `ao --skill` and its log first, then `seat_due` from `ao status --json` under its causes; the 60k reading in the Why is the standing `manager.md`'s (4,200 words; §4.8, §6 and the cadence), which is `on_call: false`'s alone; what remains is the repo's supplement `docs/briefs/manager-ao-1.md`, whose *first reads* (§4.2, §9's invariants, the cadence skill) move under the readings that need them — a held path, the build's briefs slice. The waste the journal shows is the fourteen fills with nothing due, and the held create ends them. §4.9 `on_call` and §4.9c's Apply say the same. TD-413 builds it. Before: Open.
**Blocked by:** TD-413
**Location:** `src/shiftlead/teamrun.py` (`start`: `plan.lead` is created live whatever `team.manager.on_call` says); `src/shiftlead/briefs/manager.md` (*First:* reads `ao --skill`, design §4.8 and §6, cadence §1–§4); design §4.9 `on_call`, §6 rule 3 (the `team` trigger), §6 *What is left is judgement, and a seat holds it*, §4.8

**Why:**
- Since TD-259 the manager is a seat on call, meant to be filled by §6 rule 3's `team` trigger only when a reading needs judgement: an `ask` to it, a permission on an unattended member, a `stalled?` member, *idle · open work* after the nudge, an escalation.
- `ao team start` still creates the manager as a live session, before the techlead and the members. So every Start fills it.
- Each fill reads the brief (about 4,200 words), then its first reads: design §4.8 (about 9,500 words), §6 (about 21,000) and cadence.md (about 8,500). That is some 60–70k tokens.
- The host agent's journal, 2026-10-01 to 2026-10-08, has 14 closes of a filled manager across the four teams, from 2026-10-03 on, each logged as *a seat with nothing due, idle and pushed*. They line up with Starts: on 2026-10-08, ao-grind's manager started at 15:12 and closed at 15:15 MDT, and samscrape's started at 15:54 and closed at 15:56. manager-ao-1's round log for each reads *filled with no seat_due … nothing to do*.

**Fix (to design):**
1. A Start writes an on-call manager's record without a pane, as a held seat is written (TD-386's `create` with `held`). Rule 3's `team` trigger fills it on its first due reading. Say what the controllers point at until then: the members' `controllers` name the manager's id whether or not it has a pane.
2. A filled manager's first reads shrink to its brief, plus the one §6 rule its filling reading names, read on demand. The brief's *First:* paragraph names no whole section.
3. A fill says why it came (the reading that made it due, on the line the tick types), so the round starts from it.
- Decide whether a Start with something already due (an open `ask` to the manager carried over) fills at once.

**Done when** a Start on a team with an on-call manager creates no manager pane, the manager is filled the first time one of its readings is due, a fill's first reads are its brief and the rule it was filled for, and a week's journal shows no *nothing due* close for a manager.

**Related:** TD-118 (archived; its step (4), the manager on Haiku, dropped for this), TD-247 (the manager's jobs moved to the tick), TD-259 (the seat on call), TD-386 (the held create), TD-381 (the anchor seat's record without a pane).

## TD-413: Build TD-410: a Start writes the on-call manager held — no pane until a reading is due — the fill's cause line, and the on-call briefs' first reads

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-08 (the designer, from TD-410's design)
**Owner:** grinder
**Kind:** live-check #1684
**Waits:** until 2026-10-21 — the journal's week since #1684 went live runs to 2026-10-20 23:07 MDT
**Status:** **Built 2026-10-13 (grinder-ao-1, PR #1684, TD-660):** rule 8's start writes every unattended seat held; once #1684 is live, read the journal half again — a week of it with no seat closed with nothing due straight after a rule 8 start. **Read 2026-10-14 (the anchor): the week runs to 2026-10-20 23:07 MDT.** #1684 went live with `bc43060` at 2026-10-13 23:07 MDT (the host agent's journal, read only: *promoted `shiftlead` `bc43060` — 11 commits*; `2a6b6dd`, the promote before, lacks it). Since then the journal holds no rule 8 start: the one that day, *rule 8: starting dc-grind again for ['TD-104']* at 23:00, came before the promote and found *no work waiting*. Every *a seat with nothing due* close since is a techlead or an anchor. Read the journal again after the week ends. **Read 2026-10-13 (the anchor): the journal half does not hold — blocked by TD-654.** `journalctl --user -u shiftlead-agent` (read only) since the promote: every *a seat with nothing due* close of a manager follows a fill for a cause (*the seat is due (stalled) — filling it*) or a held create, except two, and both follow a rule 8 start: *rule 8: starting dc-grind again for ['TD-092', 'TD-096'] (4 records)* at 2026-10-12 10:57:36 MDT, `ao-dev-cadence-manager-dc-1` *superseded the closed session* (no *written closed with no pane*) and closed at 10:59:51; and *starting cm-grind again for ['TD-056'] (4 records)* at 2026-10-13 10:06:20, `ao-contractmatch-manager-cm-1` replayed over the link and closed at 10:08:32. Each start's techlead seat went the same way, as did ao-grind's techlead at rule 8's start of 2026-10-11 04:35. `agent_tick._work_start` replays every ended crew record, seats included, through `_replay`, which creates a pane; §6 rule 3's *A Start writes the seat and never fills it* names `ao team start` and Apply alone. The 10-12 read below missed the first of these. The held create of a person's Start still holds. TD-654 says what is wrong; this check is read again once it is built and live. **Read 2026-10-12 (the anchor): the journal half holds so far.** `journalctl --user -u agentorc-agent -u shiftlead-agent` (read only) from 2026-10-08: the last *a seat with nothing due* close of a manager is `ao-samscrape-manager-sam-1` at 15:56 MDT on 2026-10-08, before the promote (19:33); since then such closes are techleads, audits and anchors only. The week ends 2026-10-15. **Read 2026-10-09 (the anchor): the held create holds.** ao-grind's start at 13:22Z left `ao-agentorc-manager-ao-1` `closed` with `closer: {by: start, why: held}`, and samscrape-grind's at 14:30Z did the same for `ao-samscrape-manager-sam-1` (`ao status --json`). The journal half waits on the calendar: the week from the promote runs to 2026-10-15. **Read 2026-10-08 (the anchor, TD-426):** #1287 went live 2026-10-08 19:33 MDT (aebeea3); no team has started since (ao-grind's manager-ao-1 was created at 15:12 that day, filled and closed by the tick as a seat), so the check waits on the next `ao team start`. Built 2026-10-08 (grinder-ao-1, PR #1287), all three slices: `teamrun.start` and `apply` write a manager on call `held` (`on_call_gate`), the line *on call — comes when a member needs a reading*; the held create moves kept mail; a fill's prompt ends with *[agentorc] you are filled for: <by> — <member or question>* (`agent_tick.filled_for`, `restarts` `for: seat_due`); `docs/briefs/manager-ao-1.md`'s first reads under the readings. Tests in `test_anchor_seat.py`, `test_manager_on_call.py`. **The live check:** once #1287 is live (`ao promote status`), read with no press of yours: the next `ao team start` of an on-call team (its output, or `ao status -v`) shows the manager `closed` with `closer: {by: start, why: held}` and no pane; and from the promote on, a week of `journalctl --user -u agentorc-agent` (read only) holds no *a seat with nothing due* close of a manager that was never filled for a cause. TD-410 is archived with this entry. **Read 2026-10-08 19:39 local (grinder-ao-2):** #1287 went live with `aebeea3` at 19:33 (the host agent's journal: *promoted `agentorc` `aebeea3` — 9 commits*); no `ao team start` has run since, so the held create is not yet seen — `ao status --json` shows `ao-agentorc-manager-ao-1` `closed` by `{by: tick, why: seat}` at 21:15Z, a close from before the promote. The journal since the promote holds no *a seat with nothing due* line. **Waits for** the next `ao team start` of an on-call team, and a week of journal from 2026-10-08 19:33 (to 2026-10-15): read both then, and archive or file what is wrong.
**Location:** `src/shiftlead/teamrun.py` (`start`: `plan.lead`'s create; `apply`: the manager the run lacks; the Start's row words), `src/sessionorc/agent.py` (`rpc_create`'s `held` for a `team` seat, `_schedule(state="closed")`), `src/sessionorc/agent_tick.py` (`_fill` → `_replay`: the closing line on the prompt), `src/shiftlead/briefs/manager_on_call.md` (the first paragraph reads the line), `docs/briefs/manager-ao-1.md` (first reads moved under the readings — a held path), `tests/test_teamrun*.py`, `tests/test_seats*.py` / `tests/test_manager_on_call*.py`

**Why:** TD-410: `start` creates the manager live before every other record, so every Start fills a manager on call into nothing — fourteen fills since 2026-10-03, each closed as *a seat with nothing due*. Design §6 rule 3 *A Start writes the seat and never fills it*, §6 *What is left is judgement* (first reads), §4.9 `on_call`, §4.9c (Apply).

**Fix:** as designed, three slices —
1. **The held create** (`teamrun.start`, `teamrun.apply`): when `team.manager.on_call`, `plan.lead` is created with `held=True` and no `held_reason` (the create takes `seat` and `unattended`, which the on-call launch carries; `rpc_create` accepts `held` without `held_reason`, and a `team` seat's held record takes no `seat_held`); **the held path honours `keep_mail`**: today `rpc_create` returns from `_schedule(state="closed")` before `_move_mail`, and `_schedule` writes `supersedes: [{mail: false}]`, so a held record begins with an empty inbox — make the held create move the superseded record's mail when `keep_mail` is given, as a fill does, so a question to the last run's manager fills the seat; `lead_id` is the reply's id as today, so every member's `controllers` and every brief's `{manager}` are unchanged; the Start's row for it reads *manager-ao-1: on call — comes when a member needs a reading* (`org.MANAGER_WHEN`); `apply` creates a manager on call the run lacks the same way. Pin: a Start on an on-call team makes no manager pane (`state == "closed"`, `closer: {by: start, why: held}`, a launch record written), the members' `controllers` name its id, the techlead's `controllers` too; the tick's first `_seat_pass` with a kept `ask` in its inbox (a Start with `keep_mail` over a concluded team's manager record holding an open `ask`, the mail moved) or a member hook-confirmed `needs-you` on a permission fills it on that tick; with nothing due, no fill and no close (it is closed already, and `_seat_pass` closes an `idle` seat only); the launch record replays into a pane (the fill), not into another held record. `on_call: false` starts the manager live as today.
2. **The cause line**: `_fill` passes the cause to `_replay`, which appends to the prompt it hands `create` — after `_refill_prompt`, on the stored prompt too — one line, `\n\n[agentorc] you are filled for: <by> — <member>` (`<ask id>` for `asks`), fixed words from `seat_due`; no other `why` adds a line; the `restarts` entry carries `for: seat_due` beside `why: fill`. `manager_on_call.md`'s *First* paragraph says the brief's last line names the reading it was filled for, and the line under the causes that reads `seat_due` from `ao status --json` says it is the same reading. Pin: the created prompt ends with the line for each of the four causes; a crash restart's prompt carries none.
3. **The briefs** (held path, `docs/briefs/**` — the techlead reads it): `docs/briefs/manager-ao-1.md` loses its *First reads, beyond the template's* sentence; what it named goes where a reading needs it — §9 invariants 5 and 9–11 beside the permission reading, §4.2 beside `stalled?`, the cadence skill beside *idle · open work* — in one short line each. `src/shiftlead/briefs/manager_on_call.md` names no section of the design whole (check it still does not after slice 2's edit).

**Done when** a Start on a team with an on-call manager creates no manager pane and its members' `controllers` name the manager's id; the manager is filled the first time one of its readings is due, a Start with a question kept in its mail filling on the first tick; a fill's prompt ends with the cause line; `docs/briefs/manager-ao-1.md` names no first reads of its own; the tests above pin each; `pdm run test` and `pdm run lint` pass. The live check, once this is live: a week's host-agent journal shows no *a seat with nothing due* close of a manager that was never filled for a cause, and `ao team start ao-grind`'s output names the manager *on call*. TD-410 is archived with this entry.

**Related:** TD-410 (the design), TD-259 (the seat on call), TD-386 (the held create), TD-395 (`held_reason`), TD-400 (Apply creates a seat the run lacks), TD-217 (the replay fills the brief again).

## TD-460: Read back the one-week trial of a 200k context bound on ao-grind's grinders

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-08 (the anchor, from Paul's decision on TD-378: *Pin 1h, restart lapsed, trial 200k*)
**Owner:** grinder
**Kind:** live-check #1317
**Waits:** until 2026-10-23 — the second trial week ends 10-23 04:26Z; read it from then (Paul, 2026-10-15)
**Status:** **Read 2026-10-15 (the anchor): flat. Paul, 2026-10-15 (board, m-e9930892429c): run another week — the line stays, and the check is read again from 2026-10-23 05:00Z over the second trial week (10-16 04:26Z to 10-23 04:26Z) against the same two weeks; that reading keeps the line or takes it out, a result still unclear going to Paul as a `steer`.** Steer `m-1aa735ccc0cc` to the techlead is overtaken by Paul's answer. Per merged PR the grinders' weighted cost was 1.54M the week before and 1.52M in the trial; mean context fell 191k → 153k, while requests per PR rose 102 → 117 and runs per PR 0.56 → 0.89. The table is in the ADR's Consequences. The second reading's answer is acted on in a PR of its own: the line out of `.shiftlead.yml` (and its comment), or kept and design §4.8 saying this repo's grinder number is measured. Was: Open — the bound is set in `.agentorc.yml` (`roles.grinder.context: {bound: 200k}`) by the PR that filed this entry. Each grinder takes it at its next start, and the trial week runs from the first such start to about 2026-10-15. Until then the check waits on the calendar, which nobody can hurry.
**Location:** `.shiftlead.yml` (`roles: grinder: context:`); design §4.8 *The bound has two layers*; the usage ledger and the run logs under `~/.shiftlead/` (read only)

**Why:**
- Requests over 200k tokens of context carried 35% of agentorc's weighted cost from 2026-10-01 to 10-08 ([ADR 2026-10-08](decisions/2026-10-08-prompt-cache.md)).
- A 200k bound cuts the mean context per request by about 29%. Against that, each grinder run is about two-fifths shorter, and every restart pays its first write and a re-orientation (the ledger, the files and the design read again).
- Nobody can net the two on paper. Paul decided to measure it on the weekly window for a week.

**Fix:** after a week on 200k (from about 2026-10-15), compare ao-grind's grinders' usage-window percentage per merged PR against the week before (2026-10-01–08). Count their restarts per merged PR alongside. Write the reading into the ADR's Consequences. Then keep the line in `.agentorc.yml` (and design §4.8 says this repo's grinder number is measured), or take it out (the 300k default returns), in a PR. A result that is unclear goes to Paul as a `steer` with the reading. **Done when** the line stays or goes with a written reading behind it.

**Related:** TD-378 (archived), [ADR 2026-10-08](decisions/2026-10-08-prompt-cache.md) option 4, TD-249 (the bound's last change), TD-459.

## TD-512: A PR that truncates `docs/technical_debt_archive.md` passes every gate: nothing reads what the archive lost

**Priority:** Low
**Type:** debt
**Added:** 2026-10-09 (grinder-ao-2, found on #1421)
**Owner:** dev-cadence
**Kind:** build
**Status:** Open
**Location:** `scripts/check_cadence.py` (the `ledger` row; a SYNCED FILE, dev-cadence's), `tests/test_ledger.py`

**Why:** On #1421 (TD-461) an archive edit written as `open(a, "w").write(open(a).read() + entry)` truncated the file before reading it: the archive went from 8,363 lines to 22, its header and every entry from TD-011 to TD-455 gone. `pdm run pytest tests/test_ledger.py` passed (11 passed), and the cadence check's `ledger` row reads only that the PR names its TD and touches a ledger file. Only the Sonnet fact-check caught it, by reading the diff's `@@ -1,8363 +1,22 @@`. The archive is append-only by its own header ("appended in resolution order", "Each keeps its original TD number forever"), so a PR that deletes archived entries is never right.

**Fix:** in the cadence check's `ledger` row, fail a PR whose diff removes a `## TD-NNN:` heading from `docs/technical_debt_archive.md`, naming the ids (a rebase's conflict resolution that drops one shows the same way). Being a SYNCED FILE, it is made in dev-cadence and synced here. **Done when** a PR deleting an archived entry's heading fails the `ledger` row.

**Related:** TD-461 (#1421).

## TD-553: Drop the `.agentorc.yml` fallback once samscrape, contractmatch and dev-cadence have renamed theirs

**Priority:** Low
**Type:** debt
**Added:** 2026-10-11 (the anchor, split from TD-060 (c) on Paul's word)
**Owner:** grinder
**Kind:** build
**Status:** Open — until each of the three repos' `main` carries `.shiftlead.yml` and no `.agentorc.yml` (samscrape's by the anchor's PR, samscrape #1077, 2026-10-11; the other two are the entries below); read each with `git -C ~/<repo> ls-tree origin/main --name-only` before the pick.
**Blocked by:** contractmatch#TD-055, dev-cadence#TD-091
**Location:** `src/sessionorc/paths.py` (`LEGACY_REPO_FILE`, `repo_file`), `src/shiftlead/org.py:1044`, `src/shiftlead/repoconfig.py` (536, 576), `src/shiftlead/migrate.py`'s docstring (21, 36), `tests/test_migrate.py:92`, and the design's sentences on the fallback (`docs/design/4.7-cli.md:373`, `5-configuration.md:124`).

**Why:** the rename read a repo's `.agentorc.yml` as a fallback (TD-060 step 2) so the other repos could rename theirs by their own PRs. Once they have, the fallback is code that reads a file nobody writes.

**Fix:** delete `LEGACY_REPO_FILE` and every use of it; `repo_file` returns `<root>/.shiftlead.yml`; a repo that still has only `.agentorc.yml` then has no repo file — `ao doctor` names the file to rename (one line, `org`'s check). The design's fallback sentences go in the same PR, the dated fact to `docs/design-history.md`. It is grinder-ao-1's (its lane is `src/sessionorc/**`), and its claim names the `repoconfig.py` and `org.py` lines it touches in grinder-ao-2's lane.

**Done when** `git grep -n LEGACY_REPO_FILE` returns nothing, `.agentorc.yml` is left only in `migrate.py` and `tests/test_migrate.py` (the migration's own data) and in §10's answered questions (dated), `ao team list` reads all four teams from `.shiftlead.yml`, and a repo with only `.agentorc.yml` is named by `ao doctor`.

**Related:** TD-060, TD-552.

---

## TD-623: No document says how a repo gets its first ShiftLead team

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-09 (Paul, while starting guardians' team)
**Owner:** designer
**Kind:** design-first
**Status:** Open. Standing up guardians' team (2026-10-09) needed facts spread across design §4.4a,
§4.9, §4.9b, §4.9c, `docs/briefs/README.md`, the package role templates, two working examples
(contractmatch's and dev-cadence's repo config and briefs) and TD-299's step list. A session
in the new repo had to be handed a brief written from all of them.
**Blocked by:** decision (paul) — when guardians-devenv TD-028's *doc gaps* list (a first run to learn from) is enough to write from
**Location:** a new how-to (where it lives is part of the fix: the README's *Run it*, a
`docs/` page, or `ao`'s skill text), `docs/briefs/README.md`, design §4.9.

**Why:** every repo that joins repeats the same search, and a brief written from memory goes stale
(the retired `guardians-orchestrator.md` did: it said the repos were not on kmaster and the
devcontainer question was open, both untrue by the time it would have run).

**Fix:** one page, in order: what the repo must have first (the cadence, §4, a ledger whose entries
carry `Blocked by:` so a worker can pick), `.shiftlead.yml` (teams, flows, `held:`), the briefs as
supplements and the techlead primer, `ao org check`, `ao team start` on the host, then the
container node (`hosts.yml`, the node's `env` and logins, `ao host up`, `place:`) — and which of
those are the person's. Fold in each gap guardians-devenv TD-028 records, and say what a
constellation (an umbrella ledger, PRs in sibling repos) does differently.

**Done when:** a session in a repo with no team stands one up from that page alone, without a
brief written for it.

**Related:** TD-299, guardians-devenv TD-028 and `docs/briefs/team-setup.md`, TD-055 (archived
with the retired brief).

---

## TD-636: A director seat answers permission prompts for the person: routine ones allowed or denied by written rules, the rest escalated

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-13 (ao-paul, with Paul — the lessons ADR, lesson 1)
**Owner:** designer
**Kind:** design-first
**Status:** Designed 2026-10-13 (the designer) — (1) **one director seat per org**, `org.yml`'s `director:` (absent, none: off by default), on call by the trigger *a prompt no rule answered pending on an unattended record*, distinct from the techlead (which answers asks) and replacing a manager's permission triage where defined; (2) **the person's `permissions:` rules per role** in `settings.yml`, the adapter's syntax, matched by the host first at no token cost, then the director's judgement, then the person; **always the person's**: questions and menus, every interactive session's prompt (§9 invariant 5), and any prompt naming `~/.shiftlead`, `~/.claude`, a systemd unit or a path outside the member's worktree and checkout unless denied — the host refuses a rule's or the director's *allow* there, a deny standing as a manager's does today; the director is **the first seat of no team**, written by any team start when the org defines it, trigger `prompt`; a profile's **`permission_prompts: ask`** is what lets an unattended launch keep its prompts (an empty `unattended_args` falls back to the skip today); a rule's or director's answer is an *Answered for you* FYI in the sourced reply's shape, with no Overrule; (4) **every answer told** as an FYI in *Answered for you*, the rule or reason with it; (5) **not *Allow for this session***: no session's settings change, one prompt at a time, rules written by the person; (6) the **`permit`** grant: `decide` on unattended records only. The value: an unattended profile can launch without `--dangerously-skip-permissions`. §4.9b *The director seat*, §4.8 `permit`, §4.2, §5, §10, glossary. Built by TD-641 and TD-642. Was: Open.
**Blocked by:** TD-641, TD-642
**Location:** design §4.2 (the permission relay, Allow/Deny), §4.8 (roles, grants), §4.9 / §4.9b (seats; the techlead seat answers asks for the person), §10 *"Allow for this session"* (decided no, 2026-09-23); `src/shiftlead/briefs/manager.md` (a manager's permission triage)

**Why:** Paul, 2026-10-13: *"I like the idea of filtering permission prompts — essentially allowing a director seat to handle them for the user (we talked about a director seat at one point but have not implemented)."* Every prompt a tool does not pre-allow reaches the person, or a team's manager, which triages its members' prompts by its brief. Muse's Sentinel, Grok Bot's Auto-review rules and Hermes's `smart` approvals all put a second line in front of the person (`docs/decisions/2026-10-13-agent-product-lessons.md`). The glossary's *director* is a position in the control graph (the session whose members are managers), not a seat; no seat answers prompts for the person.

**Fix:** design first. The round settles: (1) the seat — one per home or per team, its preset and brief, and how it differs from the techlead seat (which answers asks) and a manager's triage; (2) what it may answer — written allow/deny rules per role (deterministic, checked first), its own judgement for the rest, and what always reaches the person (a question or menu, anything touching `~/.shiftlead`, `~/.claude`, systemd, another worktree); (3) never an interactive session's prompt (§9 invariant 5); (4) every answer told to the person, as the techlead's are (§4.9b *Everything answered for the person is told to the person*), with the rule or reason; (5) how it squares with *Allow for this session* having been declined; (6) the grant it holds (§4.8). The design names the build entries.

**Done when** the design says who answers which prompts for the person and the build entries are filed with `Blocked by` lines.

**Related:** TD-637, TD-638 (the same ADR's other picks), the manager brief's triage, §4.9b.

---

## TD-637: `ao progress done … --pr <n>` is accepted while the PR's required checks are red or missing

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-13 (ao-paul, with Paul — the lessons ADR, lesson 4)
**Owner:** designer
**Kind:** design-first
**Status:** Designed 2026-10-13 (the designer) — (1) **refused while a required check failed** on the exact head, in words naming them; **accepted as *checks pending*** while one runs, rule 10 reading it again and a later failure its *fail*, the declaration standing (invariant 10); (2) **green reads the base branch's required checks** — protection and rulesets, read with the repo reading as `required` — and every run on the head where nothing is required; a non-required failure refuses nothing and is named; (3) a **merged** PR is accepted unread, a repo with **no CI** accepted, a **read the home could not make** accepted with *checks unknown*, a PR closed unmerged accepted and said; (4) **`check_cadence.py`'s `ci` row** reading the required set is dev-cadence's change, filed as TD-640. §4.8 *A `done` with a PR is told what its checks read*, §4.7, §6 rule 10. Built by TD-639 and TD-640. Was: Open.
**Blocked by:** TD-639, TD-640
**Location:** design §4.7 (`ao progress done <ref> --pr <n>`), §6 rule 10 (*The cadence check*, run at the home after the declaration); `scripts/check_cadence.py` `row_ci` (reads every check run on the head, not the protected branch's required set; the script is dev-cadence's)

**Why:** Paul, 2026-10-13: *"I agree an agent should not say its done if its PR is not green."* Today the declaration is accepted and checked afterwards on the reports' cadence: the member is told its failing rows, and a second fail becomes an Inbox row. Hermes Agent's PR completion contract refuses a task's completion until the required checks pass on the exact head (`docs/decisions/2026-10-13-hermes-agent.md`).

**Fix:** design first. The round settles: (1) `done … --pr <n>` refused, in words naming the failing or missing checks, while the PR's head is not green — or accepted as *done · checks pending* and turned into a refusal only when they fail; (2) what "green" reads — the protected branch's required checks (and rulesets) on the exact head, falling back to every run when the repo protects nothing; (3) a merged PR (already through the forge's gate) and a repo with no CI; (4) whether `check_cadence.py`'s `ci` row reads the required set too (a dev-cadence change). The design names the build entries.

**Done when** the design says what `done … --pr` does on a red or pending head and the build entries are filed.

**Related:** TD-636, TD-638, TD-618 (the PR `checks` mark), §6 rule 10.

---

## TD-638: A commit names the session that made it, not the model: as tools and models multiply, which model did what is lost

**Priority:** Low
**Type:** feature
**Added:** 2026-10-13 (ao-paul, with Paul — the lessons ADR, lesson 8)
**Owner:** designer
**Kind:** design-first
**Status:** Designed 2026-10-13 (the designer) — (1) **one trailer, `Agent-Model: <adapter>/<model>`**, not a second `Co-Authored-By` (a model is no git identity, and the forge draws a co-author as a person); (2) **the hook reads a file**, `AGENT_MODEL_FILE` (`<absolute git-dir>/shiftlead-agent-model`, one per worktree), which the host agent writes with the full id whenever the record's model in use is set or changes (both set-sites: the hook's SessionStart and `PostModelSwitch`, the tick's transcript read), with `AGENT_MODEL` (the profile's alias with its adapter, `claude-code/opus`, the full id being unknown at launch) as the fallback, the trailer replaced, not added, on an amend and nothing where neither is set — the synced hook never asks `ao` (§8); (3) a review's model stays the `cadence-review:` line's; a subagent's is not named, the commit being the session's; (4) the hook is dev-cadence's (TD-645), the launch's variables and the file ShiftLead's (TD-644). §4.2, §4.2a, §8. Built by TD-644 and TD-645. Was: Open.
**Blocked by:** TD-644, TD-645
**Location:** `scripts/git-hooks/prepare-commit-msg` (dev-cadence's, cadence §9.9: `Co-Authored-By: $AGENT_NAME <…>` from the `AGENT_NAME` the orchestrator exports); design §4.2a (the model in use, TD-031), §4.3 (a metered profile's `Turn` carries `model`)

**Why:** Paul, 2026-10-13: *"As we start using more models, recording which did what is a good idea. We get some of this by including the session name as a git contributor, but could expand it to list the actual model."* Today a squash commit carries the session's trailer (`ao-paul <ao-paul@shiftlead.placeholder>`) and, from Claude Code's own attribution, `Claude Opus 5.5`; a tool that adds no attribution of its own (Codex, Gemini CLI) leaves only the session, and a mid-run model switch or a subagent on another model is in neither.

**Fix:** design first. The round settles: (1) the trailer's shape (a second `Co-Authored-By` naming the model, or a key such as `Agent-Model:`); (2) where the hook reads the model — an `AGENT_MODEL` exported at launch is stale after a switch, so perhaps the session's record (the model in use, §4.2a) through `ao`; (3) a review's model, which the `cadence-review:` line already names; (4) the hook change goes to dev-cadence (its synced file), shiftlead's part is what it exports. The design names the build entries, one of them dev-cadence's.

**Done when** the design says how a commit names the model that made it and the build entries are filed.

**Related:** TD-636, TD-637, TD-031, cadence §9.9.

---

## TD-640: `check_cadence.py`'s `ci` row reads the base branch's required checks, not every run on the head (TD-637)

**Priority:** Low
**Type:** debt
**Added:** 2026-10-13 (the designer, TD-637's round)
**Owner:** dev-cadence
**Kind:** build
**Status:** Open
**Location:** `scripts/check_cadence.py` `row_ci` (a SYNCED FILE, dev-cadence's: the change is made there and synced)

**Why:** TD-637: ShiftLead's `done … --pr` now reads the base branch's required checks (§4.8), while the cadence check's `ci` row reads every check run on the head; a non-required check that fails fails the row and a required one the forge would block on reads the same as any. Two readers of *green* that disagree tell a member two things.

**Fix:** in dev-cadence, `row_ci` reads the base branch's required set (branch protection's required contexts and the rulesets' `required_status_checks`), passes when those passed on the head, names a failed non-required check as a note, and keeps *every run* where nothing is required; then the sync brings it here.

**Done when** the `ci` row passes a PR whose required checks passed and a non-required one failed, fails one whose required check failed, and dev-cadence's sync lands it.

**Related:** TD-637 (the design), TD-639 (ShiftLead's side).

---

## TD-641: Build TD-636 (1): the person's `permissions:` rules per role, matched by the host at the hook, the always-the-person list, every answer an *Answered for you* FYI

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-13 (the designer, TD-636's round)
**Owner:** grinder
**Kind:** live-check #1714
**Status:** **Read 2026-10-14 (grinder-ao-2, `b5096ad` live, which holds #1714): it waits for the person's first rule.** A headless GET of the live `/inbox` (read only) draws no row naming *your permissions: rules* — no prompt has been answered by a rule — and a session cannot read `settings.yml` (`ao settings` is the person's own act), so whether a rule is written is not readable from here. The row itself was looked at on a scratch home with #1714. What is left is the person's, and it is two settings, not one: a `permissions:` rule for a grinder role in `settings.yml`, **and** that role's profile set to `permission_prompts: ask` in `profiles.yml` — an unattended profile defaults to `skip`, so no member's prompt reaches the hook until then. The anchor reads the rest once a rules row shows on the Inbox. On a scratch home (grinder-ao-1, mail `m-0a16fbb045f9`, `b5096ad`): with grinder `allow ['Bash(pdm run test:*)', 'Read']` and `deny ['Bash(rm:*)']`, an unattended record's `pdm run test -q` was allowed and its `rm -rf build` denied, each with *rule: …* as the reason; a Read of `~/.claude/settings.json` (always the person), a prompt no rule covers, and an interactive record's covered prompt were each left waiting; the Inbox drew exactly two *your permissions: rules* rows, each offering Dismiss and the team chip only, with no Overrule. Slice 1 built (grinder-ao-1, PR #1697): the rules at the hook, deny before allow, the always-the-person check (`sessionorc/permits.py`), the FYI with answerer `rules` (a reply to it refused), `permissions` on the settings read with `malformed`, `permission_prompts`. Slice 2 built (grinder-ao-2, 2026-10-14, #1714): the *Answered for you* row whose `answered.answerer` is `rules` names *your permissions: rules*, draws the rule in place of a source and **Dismiss** alone; the Settings page's settings.yml card names each `permissions.malformed` value as ignored; a node's asker was already named — the home's fleet carries its record under the same `<id>@<host>` (a test holds it). Slice 3 built (grinder-ao-1, PR #1708): a node answers by rule only while its link is up and once the home has filed the FYI over it (`answered_by_rule`, a request; the home's `_take_answered_by_rule`, the asker by its address); a link down or a filing refused leaves the prompt for the person. **Built; a live check**: once #1714 is live (`ao promote status`), read the *Done when* on the live home — a `permissions:` rule in `settings.yml` for a grinder role, and the next prompt it covers on an unattended member answered at once with *rule: …* as its reason and filed under *Answered for you* (the Inbox read headless, its row drawing Dismiss alone); no prompt of an interactive session answered by it.
**Blocked by:** decision (paul) — a `permissions:` rule for a grinder role in `settings.yml`, and that role's profile set to `permission_prompts: ask` in `profiles.yml`
**Location:** design §4.9b *The director seat answers permission prompts for the person* (line 1 and *What always reaches the person*), §4.2 (the relay), §5 `settings.yml` `permissions:`; `src/sessionorc/settings.py` (the key, checked: a role name, `allow`/`deny` lists of strings), the adapter (`src/shiftlead/adapters/claude_code/`: a `rule_matches(rule, tool, input)` in Claude Code's `Tool(pattern)` syntax, the core naming no tool), the host agent's `PermissionRequest` path (`src/sessionorc/agent_hook.py`: match before the waiter; a deny over an allow; answered through the hook with the rule as reason), the always-the-person check (a path under `~/.shiftlead` or `~/.claude`, a systemd unit, outside the record's worktree and checkout — one function, used here and by TD-642's `permit`), the *Answered for you* FYI (`src/sessionorc/agent_mail.py` ~L671 writes one only for a reply with `source` today: a host-written one in the same `answered: {question, asker, answerer, source}` shape, `models.py` ~L390, the prompt as question, the member as asker, `rules` as answerer, the rule as source; the row draws no Overrule for it), the profile key (`src/shiftlead/profiles.py` ~L48 and the adapter ~L533: `permission_prompts: ask | skip`, the skip flag added only under `skip`, the default); `tests/test_hook*.py`, `tests/test_agent*.py`, `tests/test_mail.py`, `tests/test_profiles*.py`

**Why:** TD-636: Paul, 2026-10-13 — *filtering permission prompts*; the first line in front of the person is the person's own rules, at no token cost.

**Fix:**
1. `settings.yml` `permissions: {<role>: {allow: [...], deny: [...]}}`, read by the host agent; a malformed rule is named where the Settings page shows settings, and ignored. `profiles.yml`'s `permission_prompts: ask | skip` (default `skip`): the adapter adds `--dangerously-skip-permissions` to an unattended launch only under `skip`.
2. At the hook, for an **unattended** record only: the role's `deny`, then its `allow`; an *allow* of a prompt on the always-the-person list (a path under `~/.shiftlead` or `~/.claude`, a systemd unit, outside the record's worktree and checkout) is not made — the prompt goes on — while a deny of one stands; a match answers through the hook with *rule: <the rule>* as the reason.
3. Every answer by a rule: one FYI in the person inbox's *Answered for you*, the `answered` shape filled as the design says; no Overrule on its row, the row naming `settings.yml`'s `permissions:` instead.
4. An interactive record's prompt and a question or menu are never matched.
5. Tests: an allow rule answers and writes the FYI; a deny wins over an allow; an allow of a prompt naming `~/.shiftlead` is not made and a deny of one is; `permission_prompts: ask` launches without the skip flag and the default with it; an interactive record is never matched; a question is never matched; no rules leaves everything as today.

**Done when** a prompt on an unattended member that the person's rules cover is answered at once by the host with the rule as reason and told in *Answered for you*, the always-the-person prompts and every interactive session's are not, and the tests above pass.

**Related:** TD-636 (the design), TD-642 (the seat), TD-075 (*Answered for you*).

---

## TD-642: Build TD-636 (2): the org's director seat — `director:` in `org.yml`, its trigger and `director.md`, the `permit` grant, the manager's triage standing aside

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-13 (the designer, TD-636's round)
**Owner:** grinder
**Kind:** build
**Status:** Open
**Blocked by:** TD-641
**Location:** design §4.9b *The director seat* (line 2), §4.8 `permit`, §5 `org.yml` `director:`, §6 rule 3 (seats and their triggers); `src/shiftlead/teams.py` / `teamrun.py` (the org's `director:` read by the client; any `ao team start` or Apply writes the director seat when the org defines it and no record holds it — the first seat of no team, trigger `prompt`, added to the trigger set), `src/sessionorc/agent_tick.py` (rule 3: the trigger *a prompt no rule answered pending on an unattended record*, filling the seat, closing it with nothing due), `src/sessionorc/agent_common.py` / `agent.py` (the `permit` grant: `decide` on any unattended supervised record of the org, without controllers, refused on interactive, on itself and where TD-641's always-the-person check holds), `src/shiftlead/briefs/director.md` (new: what to allow, deny and pass up), `src/shiftlead/briefs/manager.md` (*with a director, permissions are its*), the *Answered for you* FYI with the director's reason; `tests/test_agent*.py`, `tests/test_seat*.py`, `tests/test_tick*.py`

**Why:** TD-636: Paul, 2026-10-13 — *allowing a director seat to handle them for the user*; the prompts no written rule covers need a judge before the person.

**Fix:**
1. `org.yml` `director: {profile, brief}` (absent: none); the home keeps the seat as a team's techlead seat is kept, with no team badge.
2. The trigger: an unattended record's prompt still pending after TD-641's rules, with the director defined; the seat filled, handed the prompt's record, tool, input, and the member's role, brief and claim.
3. `permit`: `ao allow|deny <id> <reason>` (the `decide` RPC) from the seat on any unattended record of the org; refused on interactive, on itself, and on an *allow* of an always-the-person prompt (a deny stands).
4. A pass: `ao msg person` naming the prompt and why, the prompt left as `needs-you`.
5. Every answer: an FYI in *Answered for you* with the reason.
6. The manager's brief: with a director defined, it leaves permissions to it.
6a. The seat is the org's, above every project heading (TD-722, amended 2026-10-15): no `team` or `project` badge; the **Org block** in place of a Project block — every registered checkout on its host, read from the repo registry at each fill (not from `projects:`), each named with its project heading where it has one; its record's `dir` its own plain directory outside every repo, `~/.local/share/shiftlead/director/` (§4.9b *The director seat*); and its card drawn in a row of its own at the top of the Org page beside the person's row, only where `director:` is defined, with no Start or Wind down (TD-725's grouping draws it there).
7. Tests: the seat fills on an unmatched prompt and not on a matched one; `permit` allows and denies an unattended record's prompt, is refused on an interactive one, on itself and on a `~/.shiftlead` prompt; no `director:` leaves the prompt to the person; the FYI is written.

**Done when** an unattended member's prompt no rule covers is answered by the director seat with a reason told to the person, or passed up in words, never touching an interactive session or the always-the-person prompts, and the tests above pass.

**Related:** TD-636 (the design), TD-641 (the rules), TD-151 (the techlead seat), §6 rule 3.

---

## TD-645: `prepare-commit-msg` writes `Agent-Model: <adapter>/<model>` from `AGENT_MODEL_FILE`, else `AGENT_MODEL` (TD-638)

**Priority:** Low
**Type:** feature
**Added:** 2026-10-13 (the designer, TD-638's round)
**Owner:** dev-cadence
**Kind:** build
**Status:** Open
**Location:** `scripts/git-hooks/prepare-commit-msg` (a SYNCED FILE, dev-cadence's: the change is made there and synced; cadence §9.9)

**Why:** TD-638: ShiftLead now names the model in use in `AGENT_MODEL_FILE` and the declared one in `AGENT_MODEL` (design §4.2); the hook that writes `AGENT_NAME` as a co-author writes no model.

**Fix:** in dev-cadence, beside the `Co-Authored-By` trailer: read the first line of `$AGENT_MODEL_FILE` when it is set and readable, else `$AGENT_MODEL`, and add `Agent-Model: <value>` with `git interpret-trailers --if-exists replace` for that key (an amend after a model switch names the new model, never two); nothing when neither is set; tool-neutral, never calling `ao`. Then the sync brings it here.

**Done when** a commit made in a session with either variable carries one `Agent-Model:` trailer naming the model then in use, one made without carries none, and dev-cadence's sync lands it.

**Related:** TD-638 (the design), TD-644 (ShiftLead's side).

---

## TD-656: Build TD-646 (3): the Cloudflare route — the Access token check on the route port, `ao connect cloudflare`, its doctor line and guide

**Priority:** Low
**Type:** feature
**Added:** 2026-10-13 (ao-connect, TD-646's Fable round)
**Owner:** grinder
**Kind:** build
**Status:** Open — designed, built when asked: nobody runs the route today, and the home's passkey (designed with the relay, §4.5b) may replace the token check, so the two are decided together before this is built.
**Blocked by:** decision (Paul) — built when asked (Fable review, adopted by Paul 2026-10-13)
**Location:** design §4.5b (the `cloudflare` route and check), §4.7 `ao connect cloudflare` and `ao doctor` **route**; `src/shiftlead/ui/` (the route port's layer), `src/shiftlead/cli.py`, `pyproject.toml` (`pyjwt[crypto]`), `README.md`

**Why:** TD-646: Cloudflare Tunnel + Access serves a device that cannot run Tailscale and a person who wants a hostname of their own; the Fable review moved it out of the path of Paul's phone.

**Fix:**
1. On the route port, `cloudflare` → `Cf-Access-Jwt-Assertion` verified with PyJWT (`pyjwt[crypto]`, a new dependency): algorithm pinned to RS256, the key its `kid` names from `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` (cached an hour; fetched again on an unknown `kid`, at most once a minute; none ever fetched refuses), `iss` that URL's origin, `aud` holding `reach.aud`, `nbf`/`exp` with 60 s leeway, `email` present and in `reach.allow`.
2. `ao connect cloudflare --hostname --team --aud`, as §4.7 orders it: the local tunnel config's rule targets the route port and carries `access: {required, teamName, audTag}` (a dashboard-managed tunnel said to be unreadable); the cookieless fetch refused on any answer marked by a listener; the certs fetched once; the email asked, or refused with no terminal and no `--allow`; `off` removing only `reach:`.
3. `ao doctor`'s **route** line for `cloudflare`: the cookieless fetch and the certs fetch again.
4. The README's Cloudflare part: the domain, the named tunnel pointed at `127.0.0.1:8766` with `access:`, the Access application, revoking a lost device's sessions.
5. Tests: wrong `alg`, `iss`, `aud`, expired, no `email`, with a test key pair standing in for Cloudflare's certs; `ao connect cloudflare`'s refusals with the fetches faked.

**Done when** a phone through a Cloudflare tunnel with Access reaches the UI, and `ao connect cloudflare` refuses a tunnel with no Access in front.

**Related:** TD-646 (the design), TD-647, TD-648, the relay's passkey (§4.5b).

---

## TD-657: The director seat knows the person only through its brief: it should also answer what techleads pass up, from the person's written preferences in layers, at a level the person sets

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-13 (ao-paul, with Paul — after TD-636's round)
**Owner:** designer
**Kind:** design-first
**Status:** Designed 2026-10-13 (the designer) — design §4.9b *The director answers what is passed up to it, from the person's preferences*, with §4.5a (the *preferences proposed* row, the Settings page's **Director** card, **Remember this**, the level on *answered for you*), §4.7 `ao prefs`, §4.9 (the home's work tree tracks `preferences.md`), §5 (`director: {level, groom}`, `repos.<repo>.director.level`), §6 rule 3 (the director's `asks` and `groom`), the glossary's *preferences*. The six points: (1) `--pass-up` goes to the director where the org defines one, once per passer (`passes`), the director's pass to the person; (2) the layers — `src/shiftlead/briefs/preferences.md` → **`~/.shiftlead/preferences.md`** (the person's, private, tracked by the home's work tree) → a repo's `.shiftlead/preferences.md` → the person's `## <repo>` section; the floor (*Always ask me*, the Never list, *What always reaches the person*) no layer lowers; the package and person layers filled into the brief as `{preferences}`, the repo's read in the asker's repo, a preference cited where it is written; (3) `director.level` (`conservative` default, `extrapolate`, `liberal`), overridden per repo, on `answered.level` and the row; (4) prompts judged against the preferences too, at the level; (5) the groom pass (`director.groom`, `7d`; **Groom now**), seeded on its first run from the boards' `Decided:` lines and the person's dated quotes, **Remember this** on a reply to a director's pass, the interview (an interactive session of the person's), every change a proposal (`ao prefs propose`) the person **Accept**s; (6) **Open file** on the Director card's layers and on an *answered for you* row citing a preferences file. TD-661, TD-662 and TD-663 build it. The defaults the person may care about — the person's layer in the home's work tree, every change by Accept, Remember this off until ticked, a weekly groom — were a steer to the techlead, m-b94fe34f403f, answered *go with your default* the same day (m-91cf2eed11f6, from the ledger's Why and §4.9).
**Blocked by:** TD-661, TD-662, TD-663
**Location:** design §4.9b *The director seat answers permission prompts for the person* (TD-636), *Passing up keeps the thread and the asker* (`pass_up` is *only to the person*), *Its standing context: a primer*, *What it may answer*; §5 (`settings.yml`, `org.yml`); §2 goal 6 (*Jump out*); briefs' layering (`src/shiftlead/briefs/`, `docs/briefs/`)

**Why:** TD-636's design (#1661) makes the director one per org, judging permission prompts by its brief (`director.md`). Paul, the same day, set its larger shape:
- *"The director would be one per ao instance — so across all teams. The original thought was that techleads would pass questions that they could not answer to it. Challenge is setting context — is this another techlead and it uses the context for the appropriate team … or does it have a higher level view based on user (my) prefs? Would we need an interview to set those up?"*
- Answered with ao-paul: a second techlead adds little (it reads the docs the first could not answer from); what a director adds is **what the person would say**. So it answers from the person's **written preferences** plus the asking team's primer, cites the line (`--source`), and passes the rest to the person. The techlead stays (Paul: *"agreed have both"*): worker → techlead → director → person.
- **A level** (Paul: *"Some users may want the director to be conservative (only what is clearly documented), what is reasonably extrapolated, or answer liberally (only ask if there is no related docs/indicators)"*): `conservative` (default) / `extrapolate` / `liberal`, per repo as well. At every level §4.9b's **Never** list and *What always reaches the person* still go to the person, and each answer names the level that produced it.
- **Preferences in layers** (Paul: *"Should we have preferences per repo (and it lives in each repo) with some set of defaults like we do for briefs?"*): package defaults → **the person's own, across repos** (private, in git; ao-paul's addition, so risk, spending and *always ask me* are not copied into every repo nor shown in a shared one) → a repo's own file, checked in. The most specific wins; nothing overrides the Never list.
- **Grown and groomed** (Paul: *"we would want the preferences routinely groomed so that it becomes general sentiments instead of specifics"*): seeded from what is already decided (boards' `Decided:` lines, *Paul, <date>:* quotes in the ledger and history), a short interview for the gaps, an answer to a passed-up question offered as an addition (*remember this?*), and a routine grooming pass landed as a change the person reviews.
- **An Open preferences button** (Paul: *"a button for the user to be able to open the pref file in an editor"*): on the Settings page and on each *Answered for you* row, opening the file the answer cited.

**Fix:** design first, extending TD-636's seat (its rules → director → person order and *What always reaches the person* stand). The round settles: (1) `pass_up` to the director where the org has one, and from it to the person (the thread, `passed_up` once per passer, the recommendation); (2) the preferences files — names, where the person's own lives, the layer order, and the director's first read (the primer's rule: an index, cited at the source); (3) the level — key, where it is set, how an answer shows it; (4) the director's prompt judgement read against the preferences as well as `director.md`; (5) seeding, the interview, *remember this?*, and grooming — who runs each and how each lands; (6) the button. The design names the build entries.

**Done when** the design says what the director answers beyond permission prompts, from which preferences, at which level, and how the preferences grow and are groomed, and the build entries are filed with `Blocked by` lines.

**Related:** TD-636 (the seat), TD-641 / TD-642 (its builds; TD-642's brief and trigger are where this lands), `docs/decisions/2026-10-13-agent-product-lessons.md` (lessons 1 and 7).

---

## TD-661: Build TD-657 (1): a pass goes to the director where the org has one — `passes` once per passer, the director's `asks` cause, its pass to the person, both recommendations on the row

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-13 (the designer, TD-657's round)
**Owner:** grinder
**Kind:** build
**Status:** Open
**Blocked by:** TD-642
**Location:** design §4.9b *Passing up keeps the thread and the asker*, *The director answers what is passed up to it*, §6 rule 3 (`asks` on the director seat), §4.5a *Inbox row: passed up*; `src/sessionorc/agent_mail.py` (`rpc_pass_up` ~L981, the `passed_up` refusal ~L1009 and ~L161), `src/sessionorc/agent_tick.py` (rule 3's `seat_due` for the director), `src/shiftlead/ui/` (the passed-up row), `src/shiftlead/briefs/techlead.md`, `director.md` (TD-642's)

**Why:** TD-657: Paul, 2026-10-13 — *techleads would pass questions that they could not answer to it*; today `pass_up` goes only to the person.

**Fix:**
1. `pass_up`: open to the addressee once **per passer** — `passes: [{by, at, recommend}]` appended, `passed_up` the latest time; a second pass by the same passer refused. Where the org's director seat record exists, is not suspended and is not at its fill ceiling, and the passer is not it, the asker's entry goes to the director's inbox, never refused as full; else, and from the director, to the person as today (the full-inbox refusal only there).
2. Rule 3: the director seat is due by `asks` while a question passed up to it is open in its inbox (as a techlead's `asks`); its *filled for* line names the question.
3. The person's copy draws each pass's recommendation, the latest first (`ao inbox` and the row).
4. `director.md` gains the question half: answer with `--source` or pass up with a recommendation; a `steer`'s bound is not extended. `techlead.md` says a pass reaches the director where there is one.
5. Tests: a techlead's pass with a director lands in the director's inbox and makes it due; the director's pass reaches the person with both recommendations; a second pass by the same passer is refused, the director's after the techlead's accepted; with no director, or the director suspended or at its fill ceiling, the pass goes to the person as before; the person's reply goes to the asker and closes every copy.

**Done when** a question a techlead passes up reaches the director where the org has one and the person otherwise, the director's pass reaches the person showing both recommendations, and the tests above pass.

**Related:** TD-657 (the design), TD-642 (the seat), TD-662, TD-663.

---

## TD-662: Build TD-657 (2): the preference layers and the level — the package's `preferences.md`, `{preferences}` in `director.md`, `director.level` per org and repo, `answered.level`, the Settings page's Director card, `ao prefs`

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-13 (the designer, TD-657's round)
**Owner:** grinder
**Kind:** build
**Status:** Open
**Blocked by:** TD-642
**Location:** design §4.9b *The director answers what is passed up to it* (*Preferences, in layers*, *Its first read*, *A level the person sets*), §4.5a (Settings page **Director** card, *Inbox row: answered for you*), §4.7 `ao prefs`, §5 `settings.yml` `director:`; `src/shiftlead/briefs/` (new `preferences.md`; `director.md`), `src/sessionorc/brief.py` (`fill`: `{preferences}`), `src/sessionorc/settings.py` (the keys), `src/sessionorc/agent_mail.py` (`answered.level`), `src/shiftlead/cli.py`, `src/shiftlead/ui/` (Settings, Inbox)

**Why:** TD-657: Paul, 2026-10-13 — *preferences per repo … with some set of defaults like we do for briefs*; *conservative … reasonably extrapolated, or answer liberally*; *a button for the user to be able to open the pref file in an editor*.

**Fix:**
1. `src/shiftlead/briefs/preferences.md`: the package's defaults, under the topic headings (*Spending*, *Scope*, *Risk*, *Style*, *Always ask me*, *Permissions*), short.
2. Two file slots in `director.md`, `{preferences}` (the package's) and `{my_preferences}` (`~/.shiftlead/preferences.md`, read by the home), filled by rule 7's fill so both are in `sources`; `brief.fill` (`src/sessionorc/brief.py` ~L84) lets this one slot be missing and fill *none written*, where a missing file raises `Unreadable` today. The director's *filled for* line names the absolute paths of the asking repo's `.shiftlead/preferences.md` and primer in the home's checkout of it. `director.md` states the order, the floor, the three levels, the citation form, and that it never reads `~/.shiftlead`.
2a. `director.md`'s prompt half reads a prompt against the preferences' *Permissions* as well as its own rules, at the level in force, under the floor; the level is handed on a prompt fill as on a question's. A start refuses a `director:` whose profile is `permission_prompts: ask`.
3. `settings.yml` `director: {level, groom}` and `repos.<repo>.director.level`, validated (`conservative|extrapolate|liberal`; a duration or `off`), written by `set_settings`; the level in force handed on the fill and recorded on a sourced reply as `answered.level`.
4. The *answered for you* row names the level and draws **Open file** on a source that is a preferences file.
5. The Settings page's **Director** card (level, per-repo picks, groom every, the layers with Open file and *create it*), drawn when `org.yml` defines `director:`; `ao prefs` (the listing).
6. Tests: the fill carries both layers in order, and fills with the person's file absent (*none written*); a change to the person's file is seen as a source change; a prompt fill carries the level; a director on an `ask` profile is refused at the start; a bad level is refused by `set_settings`; a repo's override wins for its questions; `answered.level` is written and drawn; the card's Save writes both keys.

**Done when** the director's fill carries the package's and the person's preferences and names the repo's, the level is set per org and per repo and shown on every answer, the Director card and `ao prefs` show the layers, and the tests above pass.

**Related:** TD-657 (the design), TD-642 (the seat and `director.md`), TD-661, TD-663.

---

## TD-663: Build TD-657 (3): the groom pass, the seed, Remember this, the interview — `ao prefs propose`, the *preferences proposed* row's Accept and Discard, the home's commit

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-13 (the designer, TD-657's round)
**Owner:** grinder
**Kind:** build
**Status:** Open
**Blocked by:** TD-661, TD-662
**Location:** design §4.9b *The director answers what is passed up to it* (*Grown and groomed*, *Remember this*, *The interview*), §4.5a (*Inbox row: preferences proposed*, *Inbox row: passed up*'s Remember this, the Director card's Groom now and Interview), §4.7 `ao prefs`, §4.9 *What is left at the home has a history*, §6 rule 3 (`groom`); `src/sessionorc/agent_settings.py` (`_commit_defs` ~L326 / `rpc_commit_defs` ~L312: `preferences.md` tracked; new `prefs_propose`, `prefs_accept`, `prefs_discard`), `src/sessionorc/agent_tick.py` (the `groom` cause), `src/sessionorc/agent_mail.py` (`remember` on a reply, kept until a pass concludes), `src/shiftlead/briefs/` (new `interview.md`; `director.md`'s groom half), `src/shiftlead/cli.py`, `src/shiftlead/ui/`

**Why:** TD-657: Paul, 2026-10-13 — *we would want the preferences routinely groomed so that it becomes general sentiments instead of specifics*; *would we need an interview to set those up?*

**Fix:**
1. The home's work tree tracks `preferences.md`; a hand edit is committed *edited by hand* as the other definitions are.
2. `prefs_propose {text, why}` from the director seat or an interactive session (refused from any other): writes `~/.shiftlead/preferences.proposed.md` (a newer replaces it) and the *preferences proposed* row; `prefs_accept` / `prefs_discard`, a person's own — accept moves it over and commits *preferences: <why>* — and either concludes the groom pass.
3. Rule 3's `groom` on the director: due `director.groom` after the last conclusion (none yet: at the first tick the seat exists), or on `ao prefs groom` / **Groom now**; never while a proposal stands, so a pass that proposed is not refilled until Accept or Discard.
4. `director.md`'s groom half: read the file, the remembered answers and, the first pass or with the file empty, the seed (every registered repo's board `Decided:` lines, the person's dated quotes in its ledger and design history); write general sentiments, a remembered line about one repo under its `## <repo>` heading; name the gaps in `--why`.
5. **Remember this** on the compose of the person's reply to a question whose `passes` name the director, and of an **Overrule** of a director's sourced answer: `remember: true` on the reply entry; its copy in the director's inbox kept until a groom pass concludes; the Director card counts them (*3 remembered*).
6. `ao prefs interview` / **Interview**: the person's interactive session with `interview.md` (the layers, the last gaps), ending in `ao prefs propose`.
7. Tests: a proposal from the director writes the row and a member's is refused; Accept commits and Discard deletes, each concluding the pass; `groom` is due at the seat's first tick, on the interval and on the press, and not while a proposal stands (no refill loop); a remembered reply survives the inbox's aging until the pass concludes; a hand edit is committed.

**Done when** the director grooms the person's file on its interval and on a press, seeding it the first time, a remembered answer reaches the next pass, the interview proposes, every change reaches the file only by the person's Accept or hand edit, and the tests above pass.

**Related:** TD-657 (the design), TD-661, TD-662, TD-210 (the home's work tree).

---

## TD-673: ShiftLead's Telegram notes go through samscrape's bot (`secrets: samscrape/prd`): give ShiftLead a bot and a Doppler config of its own

**Priority:** Low
**Type:** debt
**Added:** 2026-10-14 (ao-connect, Paul)
**Owner:** paul
**Kind:** build
**Status:** Open
**Location:** design §4.10 *Told on Telegram* (*Where the secrets live*); `~/.shiftlead/settings.yml` `notify.telegram.secrets`; Doppler

**Why:** the home's `notify.telegram` reads `secrets: samscrape/prd`, so ShiftLead's notes go out through samscrape's bot, with its token and chat (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). That ties the two products together: rotating samscrape's token, retiring its bot or moving its Doppler project silently stops ShiftLead's notes, and the notes arrive under samscrape's name. Paul, 2026-10-14: *set up our own ShiftLead bot instead of piggy-backing on samscrape's*.

**Fix:** done at a sitting with Paul, since each step is his:
1. In Telegram, ask @BotFather `/newbot` (a name such as *ShiftLead*), and keep the token.
2. Send the new bot any message from the chat that should receive the notes, then read that chat's id from `https://api.telegram.org/bot<token>/getUpdates` (`message.chat.id`).
3. Create a Doppler project and config for ShiftLead (say `shiftlead/prd`) holding `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
4. On the Settings page, set the Telegram card's secrets to `shiftlead/prd`; it is the person's own setting, refused to a session.
5. Check that a note arrives from the new bot, then decide whether samscrape's bot keeps its ShiftLead chat.

**Done when** a ShiftLead note arrives from ShiftLead's own bot, and `settings.yml` names no samscrape secrets.

**Related:** TD-092 (Told on Telegram), TD-648 (`reach.url`, the notes' default link).

---

## TD-681: A green sync PR has no owner: in a repo with no session running, it waits for nobody

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-14 (the session `ao-dc-decouple`, lessons from the ledgerboard split; Paul agreed the lesson the same day)
**Owner:** designer
**Kind:** design-first
**Status:** Designed 2026-10-14 (the designer; no mockup: the one drawn change is a switch and a line of text on the Settings page's Repos card) — §4.4 *A sync PR is the team's at once, unless the home merges it*: a `cadence-sync-*` PR whose diff touches the lock, and which the home does not merge, is a `pr:<n>` order (`sync: true`) from the first reading, with no `ORPHAN_AFTER`, green or red (only *pending*, and a green one not yet checked at its head, waits). Its lane is the red PR's, it is first in the pick order, and the anchor's and grinder's briefs say so. A repo no team services has the *Inbox row: red PR*, not a board line: §4.4 *Board write-back* says no policy adds one. §6 *Sync*: the home merges a mechanical sync PR. The replay at the merge base takes the lock's files from the head and runs the repo's `post_sync` (dev-cadence#TD-113); the tree must equal the head and each file match its lock hash. Before that, the PR must not be forked, its author must be the home's own login, and its repo must have a gate; a PR failing any of these three is *refused* before any comment. Its lock must also check against dev-cadence's `files/` at `files_commit` (every lock line that differs from the base's, and each changed file's bytes), it must delete no path the base lock never listed, and it must touch no held path. The replay runs `post_sync` with a scrubbed environment. Then a `cadence-review: SHIP · sync-check` comment, the gate in the registry checkout, and `gh pr merge --squash --match-head-commit` after re-reading the head and the holders, then a `system` note. A failed gate gets a BLOCK comment and goes to the team; a check with no reading is tried `SYNC_TRIES` times, then goes to the team. `repos.<repo>.sync.auto` (on when absent, §5) is the Repos card's **sync** switch (§4.5a). The manager brief needs no change: the order reaches a member's lane as any order does. Built by TD-684 and TD-685. Was: Open.
**Blocked by:** TD-684, TD-685
**Location:** design §4.4 (*A red PR nobody holds is a work order*), §6 (the home's policies), §4.8 (role presets); `src/shiftlead/briefs/` (grinder, manager)

**Why:** `sync-all.sh` from dev-cadence 517a2b5 opened six sync PRs. Only ledgerboard's merged that day, by the session that ran the sync. The PRs for pneuma-ops (#20) and guardians-devenv (#19) were green, but no session ran in either repo to merge them. samscrape's (#1097) and contractmatch's (#175) waited on their own sessions. ShiftLead's (#1728) failed its tests until a session added a step by hand (dev-cadence#TD-113). A sync PR changes only files dev-cadence owns, which may go in directly (Paul, 2026-09-12). Nothing needs the repo's judgement except its tests and its gate. Design §4.4 already gives a **red** sync PR an owner (*its owner is never nobody*), as a work order once it has been red for `ORPHAN_AFTER`. A **green** one is never an order, so nobody is assigned to merge it.

**Fix:** design first, in two parts (Paul, 2026-10-14: *each team should pick up those PRs and run them to ground, but if it's mechanical and post_sync would handle it that's even better*):
1. **A team runs its repo's sync PRs to ground.** The built-in grinder and manager briefs say that an open sync PR (`cadence-sync-*`) in the team's repo is the team's next pick, ahead of the lane. The mechanical review checks that the files are only SYNC files plus what the repo's `post_sync` wrote. Then the gate runs and the PR merges. A red one goes to the team like any red PR.
2. **With no team running, the home merges it by policy** when every file it touches is a SYNC file or `post_sync` output, its CI is green and the repo's gate passes. Otherwise the policy raises it as a board line for the repo. This is like the promote policy (§6): `auto` on the Settings page, per repo.

**Done when** a sync into a repo with no session running merges without a person, a red one in such a repo is a board line, and a team's member picks up its repo's sync PR before its lane.

**Related:** dev-cadence#TD-113 (`post_sync`), dev-cadence#TD-114 (a PR with nothing left to merge), TD-682.

---

## TD-685: Build TD-681 (2): §6 *Sync* — the home's sync check (the replay with `post_sync`), its review evidence, the gate and the merge; the Repos card's **sync** switch

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-14 (the designer, building TD-681's design)
**Owner:** grinder
**Kind:** live-check #1775
**Waits:** event — the next cadence-sync PR that nobody merges by hand before the home judges it
**Status:** **Read 2026-10-15 (the anchor): the sync it waited on was merged by hand before the home judged it, so it still waits.** dev-cadence `1145d97`'s `sync-all.sh` ran from the person's dev-cadence session (`ao-dev-cadence-dc-paul`) at 03:30Z, after the 03:29Z promote, and opened shiftlead #1788 and ledgerboard #12. That session merged all six sync PRs itself at 03:45Z, on Paul's *merge the PRs when green* (its transcript). Neither PR carries a `cadence-review: SHIP · sync-check` comment. The reading's `sync` is `{}` for both repos (`~/.shiftlead/repos.json`, read only), and the home's tree exists only for ledgerboard (`~/.shiftlead/sync/ledgerboard/tree`, 03:40Z), so its judge had begun there and no verdict was written before the merge. The mechanical half is unread: it waits for a sync PR that nobody merges by hand within its first reading or two. **Waits for the next sync PR (grinder-ao-2, 2026-10-14):** #1775 is live (`ao promote status`: live 179f7f1, #1775 itself, read 2026-10-15T02:10Z). No `cadence-sync-*` PR has been opened in any ShiftLead repo since it went live: the last are shiftlead #1728 (closed) and ledgerboard #11 (merged), both at 2026-10-14T15:26Z from dev-cadence `517a2b5`, before the build. dev-cadence's main has moved on since (`1145d97`), so the next `sync-all.sh` run makes one. Nobody in ao-grind's lanes can cause it, so the Owner is `anchor` until that PR exists; whoever reads it then reads it as below. Built — slices 1 (#1769), 2 (#1771) and 3 (#1775, the Repos card's **sync** switch with the last verdict beside it; `sync.prune` keeps the newest *merged* entry for it). The live check, once #1775 is live (`ao promote status`), is read only: the next dev-cadence sync PR on a registered repo (`cadence-sync-*`, pushed by the home's `gh` login). Read the repo reading's `sync` entry for it (`ao repo --json`, or the Settings page's Repos card). Mechanical and gated: verdict *merged*, the PR carrying the `cadence-review: SHIP · sync-check` comment and merged by the home, a `system` note in the person inbox, and the card saying *merged #<n> <age> ago*. Not mechanical: *refused* with its reason, and the team's order carrying it as Context. The scenarios themselves are covered by `tests/test_sync_check.py` and `tests/test_sync_pass.py` against a fake `gh`.
**Location:** design §6 *Sync: a mechanical sync PR is merged at the home*, §4.4 *A sync PR is the team's at once*, §5 `repos.<repo>.sync`, §4.5a *Settings page: Repos*; a new `src/sessionorc/sync.py`, the promote's pass in `src/sessionorc/agent_tick.py`, `set_settings`, `src/sessionorc/workorders.py` (the order narrowed to what the home does not merge), the Settings page's Repos card (`src/shiftlead/ui/`)

**Why:** TD-681's design. Paul (2026-10-14): *if it's mechanical and post_sync would handle it that's even better*. A sync PR changes only dev-cadence's files, so a mechanical one needs no session.

**Fix:** three slices, the first two in `src/sessionorc/**` (the techlead reads them, §4.9b).
1. **The check** (`sessionorc.sync`). `check(root, pr) -> {verdict, why}` runs §6 *Sync* steps 1 and 2.
   - **Step 1, the files.** The head lock's `source:` must equal the merge base's. The dev-cadence clone is the registry checkout whose origin is that `source:`. `files_commit` must not be `+dirty` and must be on the clone's `origin/<default>` (`git merge-base --is-ancestor`). For each lock-listed path the PR changes, the head's blob must match its lock line (sha256 and mode). Every lock line that differs from the base lock's must match the blob at `files/<path>` at `files_commit`. A deleted path the base lock never listed is refused.
   - **Step 2, the replay.** It runs in `~/.shiftlead/sync/<repo>/tree`, a detached worktree made as the board's is, set to the merge base of the PR's head and `origin/<default>`. The PR's paths come from `git diff --name-status`. The lock-listed paths, and the lock itself, are checked out from the head, and the PR's deletions that the lock dropped are removed.
   - **`post_sync`.** A string read from the merge base's `docs/cadence.toml`, run by the shell in the tree's root under `SYNC_BOUND` (5 min), its process killed at the bound. Its environment holds `PATH`, `HOME` and `LANG` alone.
   - **The comparison.** `git add -A` and `git diff --cached --quiet <head>`, then the held globs over the changed paths.
   - The check returns the first reason, worded as §6 words it.
   - **Tests**, with a fixture dev-cadence and a fixture repo: a clean sync; a non-lock file; an edited SYNC file; a lock the PR wrote for itself (its hashes its own files', not dev-cadence's); an honest lock line beside different bytes; a SYNC file edited with its line left alone; a changed `source:`; a `+dirty` `files_commit`; a `files_commit` off dev-cadence's default; a `post_sync` that reproduces the PR and one that does not; a `post_sync` that cannot see `GH_TOKEN`; a held path.
2. **The pass.** It sits in the promote's pass, on its cadence, detached, over every registry checkout with `repos.<repo>.sync.auto` not `false`. It takes up each unheld green sync PR that is not a draft, not conflicting, and has no `sync` entry with a verdict at its head. Three kinds get verdict *refused* with their reason and nothing else: a `fork` one; one not authored by the home's `gh` login (`gh api user`, read once); and one in a repo with no gate (`sessionorc.cadence`'s lookup, done first). Every other one goes on to the steps below.

   For each one:
   - **The check.** If it is mechanical, the SHIP comment (body as §6 step 3), posted once per head (`posted`).
   - **The gate**, run in the registry checkout as rule 10 runs it (`sessionorc.cadence`), never in the replay tree.
   - **Exit 0.** The head and holders are re-read. A moved head or a new holder drops the merge and writes no verdict. Otherwise `gh pr merge <n> --squash --match-head-commit <head>`, then the `system` note.
   - **A fail** (exit 1, or a `gate:` non-zero): the BLOCK comment with the gate's line, verdict *gate*.
   - **No reading** (exit 2 or 3, a timeout, the home's own error): nothing is posted and `tries` is counted. At `SYNC_TRIES` (3) at one head the verdict is *error*.
   - Every comment goes through `--body-file`. The outcome is written to the repo reading as `sync: {<n>: {head, verdict, why, tries, posted, at}}`, pruned to the open PRs, and pushed with `repos`. TD-684's order then excludes a PR whose verdict at its head is *merged*, and a green one with no verdict at its head while the switch is on; it takes its `Context` from `why`. `set_settings` accepts `repos.<repo>.sync.auto`, and a session's call is refused as today.
3. **The switch.** The Repos card's **sync** switch, on when absent, with the last verdict beside it as §4.5a words it.

**Done when** in a scratch home, a sync PR is merged by the home with the SHIP comment and the note when:
- its lock and files match a fixture dev-cadence's `files/` at `files_commit`;
- it is green and gated by `check_cadence.py`;
- it is pushed by the home's login.

It is not merged, and is the team's order with its reason, when:
- it has a non-lock file;
- its lock is self-made;
- it is forked;
- it touches a held path;
- it is authored by another login, or its repo has no gate (verdict *refused* at once, with no comment, so the order follows).

The other cases:
- A failed gate leaves the BLOCK comment and the order.
- A gate exit 3 posts nothing and is tried again.
- A head pushed between the check and the merge is not merged. `sync: {auto: false}` merges nothing. The switch shows the verdict.

**Related:** TD-681 (the design), TD-684 (the order), dev-cadence#TD-113 (`post_sync`), §6 *Promote* and *Pull* (the pass it shares).

---

## TD-703: A member renamed in `.shiftlead.yml` keeps its old name on rule 8's restart, and a person's Start can leave the old record beside the new one

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-14 (ao-paul, with Paul)
**Owner:** designer
**Kind:** design-first
**Status:** Designed 2026-10-14 (the designer; no mockup: the card's mark draws its lines as it does) — §4.9 *A member renamed or removed in the definition*: the client pairs the records the definition no longer names with the names no record carries, within a role, in order; a pair is a rename, applied by a relaunch under the new name whose create succeeds the old record (mail, open claims, `lane_seen` and every `controllers` carried), a leftover record a removal, marked sat out (`dropped` lets it mark a seat). Read on a stopped team too, and reached only by a person's Apply or Start; Members…'s Remove marks the record the same way. Built by TD-706. Was: Open.
**Blocked by:** TD-706
**Location:** design §4.9 *Where definitions live*, §4.9c *Apply*, §6 rule 8; `src/sessionorc/agent_tick.py` (`_work_start` ~L1670, `_work_replays` ~L1730), `src/sessionorc/work.py` (`crew`), `src/shiftlead/teamrun.py` (`start`, `apply`)

**Why:** Paul renamed dev-cadence's members on 2026-10-14 (dev-cadence `1145d97`: `manager-dc-1` → `dc-manager`, `techlead-dc-1` → `dc-techlead`, `grinder-dc-1` → `dc-grinder`) and saw nothing change. The definition is read — `ao team list` shows *manager: dc-manager (on call) techlead: dc-techlead* — but dc-grind is stopped, and its cards are the closed records of the last run under the old names. Rule 8's start replays a team's ended crew records (`_work_replays`) with their own names and launch records and never reads the definition (§6 *Keeping a team running*), so the team would come back as `grinder-dc-1`. Only a person's Start creates records under the new names, and `crew` is every unattended record of the team: the old ones stay closed, not superseded, and a later rule 8 start could replay them beside the new ones (read in the code, not seen live). Paul: *we should also make sure members are not duplicated on changes as well (e.g. dc-grinder alongside grinder-dc-1)*. The design says nothing of a rename.

**Fix:** decide what a renamed member is: the client finds the difference as it finds a lane's or a profile's (§4.9c, TD-619), shown as **definition changed — Apply**, and Apply (or the next Start) retires the old record — superseded by the new name's, its mail and claims carried — so neither rule 8 nor the card ever holds both. A member removed from the definition is the same case without a successor.

**Done when** the design says how a rename and a removal reach a running and a stopped team, and no path leaves two records for one member; a build entry carries it.

**Related:** TD-619 (a profile change reaches the team), TD-654 (rule 8 replays seats), TD-457.

---

## TD-704: The phase chips (*add*, *design*, *grind*, *review*) are fixed in the UI: define them, and what each matches, beside the roles in `org.yml`, overridable in `.shiftlead.yml`

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-14 (ao-paul, with Paul)
**Owner:** designer
**Kind:** design-first
**Status:** Open
**Blocked by:** decision (paul) — designed in an interactive session with Paul
**Location:** design §4.5a *team card: TDs in motion*, *Org: rollup*, §4.9 *Where definitions live*, §4.9c *Flows*; `src/shiftlead/ui/org.py` (`PHASES` L27, `motion_rows` ~L310)

**Why:** Paul, 2026-10-14: *where are the chip names defined (e.g. GRIND, DESIGN, REVIEW)? it seems like those should be defined beside the roles (in org.yml with repo-level overrides available in .shiftlead.yml)*, and: *having the chip names and entries it matches configurable is the right move for being able to create new flows on the fly. It may need to be designed in an interactive session to make sure we come up with a simple, effective design.* Today `PHASES = ("add", "design", "grind", "review")` is a constant, and `motion_rows` derives a claim's phase in one line: a `design-first` entry is *design*, a claim with a PR *review*, anything else *grind*. A flow with another stage (a fact-check, a live check, a hunt) has no chip of its own.

**Fix:** to be designed with Paul: where a phase is defined (the role, the flow's stage, or a list of its own), what it matches (an entry's `Kind`, a PR, the claimant's role), its order and colour, and how a repo overrides the org's.

**Done when** the design names the definition and its matching rule, and a build entry carries it.

**Related:** §4.9c flows, TD-418.

---

## TD-706: Build TD-703: a member renamed or removed in the definition is paired, applied and started without leaving two records

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-14 (the designer, building TD-703's design)
**Owner:** grinder
**Kind:** live-check #1818
**Waits:** event — Paul's next Apply or Start of dc-grind; read again 2026-10-15 (the anchor): dc-grind stopped, its card still lists the three old names as removed, every dc record closed
**Status:** Live check read 2026-10-15 (grinder-ao-2, #1818 live at a235a54): dc-grind was started under its new names at 04:10Z, before #1807 or #1818 was live, so `dc-grinder`, `dc-manager` and `dc-techlead` were created beside `grinder-dc-1`, `manager-dc-1` and `techlead-dc-1` with no `succeeds`, and the Done when's three renames can no longer be read: the new names carry records, so no newcomer is left to pair. What reads now holds for that state: `ao team list` and the page (headless GET) show dc-grind stopped, *definition changed*, *grinder-dc-1: removed from the definition*, *manager-dc-1: …*, *techlead-dc-1: …*, and Start's confirm says *It first applies the definition:* with the same three lines. Left for the event nobody here can cause (Paul's next Apply or Start of dc-grind), to read then: the three old records (now `sit_out: null`) carry `sit_out: {dropped: true}`, none is replayed, and `ao status -v` shows one record per new name; a rename read as a rename on a live card waits for a team renamed after #1818 went live. Owner anchor until then. Was: Slice 3 built 2026-10-15 (grinder-ao-2, #1818): Apply hands a rename as `relaunch` with the newcomer's `name` and a removal as `relaunch {sit_out, dropped}`, a stopped team's too; Start reads the same pairing after its checks and a concluded team's closes, marks each removal, creates each paired or held newcomer with `succeeds`, lists them in `moved` and its confirm, and refuses a session's start of a renamed team or one beside a live orphan; Members…'s Remove takes the sit-out form with `dropped`. **Live check:** once #1818 is live (`ao promote status`), read dc-grind's stopped card for the three renames of the Done when (a headless GET), and after Paul's Apply or Start, `ao status -v` shows one current record per new name and the old ones `superseded_by`. Not built: a rename pairing an orphan on one host with a newcomer on another is refused by the home's `succeeds` (the pairing is by role alone). Before: Slice 1 built 2026-10-14 (grinder-ao-2): `teamrun.renames` pairs the orphans with the newcomers; `differences` reads `rename` and `removed` (a stopped team's too, alone), first, and a paired or held newcomer is no `start`; the card's mark (beside Start on a stopped team), the member's `flow_mark`, `ao team list` and `ao team flow` print the lines. Slice 2 built 2026-10-14 (grinder-ao-1): `RELAUNCH_KEYS` gains `name` (kept when absent, as `profile`), a rename marked `relaunch.name`; `_replay` creates a renamed record under the launch record's new name with `succeeds: <old id>` (rule 7's restart, rule 8's start, rule 3's fill — a held seat's `_schedule` too); `create`'s `succeeds` is admitted as `keep_mail` is (`_check_succeeds`) and `_succeed` moves the mail, writes `superseded_by` and, at the home, `_carry` copies claims in progress and `lane_seen`, rewrites this host's records' and every launch record's `controllers`, and drops the old launch record (a node's own record waits for its next create, design §4.9, TD-712); `_sit_out` takes `dropped`, which lifts its seat refusal and is kept on the mark; `_seat_pass` never fills a sat-out seat. Until slice 3 Apply skips a rename or a removal and says so (*— not applied yet*), creating nothing. Slice 3 (Apply and Start, `src/shiftlead/`) open. Was: slice 1 built.
**Location:** design §4.9 *A member renamed or removed in the definition* and *Add or remove a member from the team card*, §4.9c *Switching*, §6 *Keeping a team running*, §4.5a *team card: flow changed — Apply*, *team groups* (Apply beside Start on a stopped team) and *Members dialog: Remove*; `src/shiftlead/teamrun.py` (`differences`, `flow_changed`, `flow_view`, `apply`, `start`, `remove_member`, `Difference`), `src/shiftlead/ui/cards.py` (`flow_mark`), `src/shiftlead/ui/org.py` and `src/shiftlead/ui/templates/group_head.html` (the mark and Apply, whose title and confirm name renames and removals, beside Start on a stopped team), `src/shiftlead/cli.py` (`ao team list` ~L1146, `ao team flow` ~L1972), `src/sessionorc/agent_common.py` (`RELAUNCH_KEYS`), `src/sessionorc/agent.py` (`rpc_relaunch`, `_sit_out`, `rpc_create`, `_move_mail`), `src/sessionorc/agent_tick.py` (`_replay`, `_seat_pass`)

**Why:** TD-703's design. Paul renamed dev-cadence's members on 2026-10-14 and nothing changed: rule 8 replays ended records under their own names, a person's Start would create the new names beside the old records, and Members…'s Remove sends a wrap-up that leaves a declared record rule 8 replays at the next wind-down.

**Fix:** this touches `src/sessionorc/**`, so the techlead reads it (§4.9b). Slices, each its own PR:
1. **The pairing** — `teamrun.renames(plan, sessions) -> (renames, removals)` as §4.9 says: orphans (crew, current, not sat out, name not in the definition) against newcomers (definition names no current record carries), paired within a role — a seat with the same seat — orphans by `created`, newcomers in definition order. `differences` gains `Difference` kinds `rename` (record, launch) and `removed` (record), read on a stopped or wound-down team too (renames and removals only there); an orphan whose `relaunch` mark already carries a newcomer's `name` is no difference, and the newcomer reads as held by it. Apply's and Start's confirm list each pair. `flow_mark`, `ao team list` and `ao team flow` print *grinder-dc-1 → dc-grinder: renamed* and *grinder-dc-2: removed from the definition*; the stopped team's card draws **definition changed — Apply** beside Start with the title §4.5a gives.
2. **The home's half** — `RELAUNCH_KEYS` gains `name`; a relaunch whose `name` differs is replayed (rule 7's restart, rule 8's start, rule 3's fill) as a `create` under the new name with **`succeeds: <old id>`**: the old record's mail moved (`_move_mail`), `superseded_by` written on it, its `claimed` progress entries and `lane_seen` copied to the new record, every record's `controllers` and every launch record's naming the old address rewritten to the new, the launch record moved to the new id. `succeeds` is admitted as `keep_mail` is (`_check_keep_mail`: the person, the home's replay, or a controller of the old record) and refused otherwise. `_sit_out` takes `dropped: true`, which lifts its seat refusal; `_seat_pass` never fills a sat-out seat. A node member's own record is rewritten by TD-712.
3. **Apply and Start** — `apply` hands each rename's launch with `name` and each removal as `relaunch {id, sit_out: true, dropped: true}`; `start` reads the same pairing after its checks and a concluded team's closes, creates each paired newcomer with `succeeds`, marks each removal, and lists both in its result and confirm. `remove_member` uses the sit-out form (with `dropped`) for a live or an ended member instead of `_stop_one`.
- **Tests:** a renamed member on a live team relaunches under the new name with its mail, claims and `lane_seen`, and the old record superseded; a renamed manager's members name the new id in their `controllers` and launch records; a stopped team's rename by Apply is replayed by rule 8 under the new name and the old never; a person's Start after a rename leaves one current record per name; a removed member (Apply or Members…'s Remove) is marked sat out and `_work_replays` skips it; a dropped seat is marked and never filled; two orphans and two newcomers of one role pair in order, and the confirm lists the pairs; after Apply on a stopped team the card offers the rename no more; a person's session is never an orphan; the doc-bound §4.5a tests pass.

**Done when** dev-cadence's rename (`grinder-dc-1` → `dc-grinder`, `manager-dc-1` → `dc-manager`, `techlead-dc-1` → `dc-techlead`) reads as three renames on dc-grind's stopped card, Apply or Start brings the team back under the new names with no old record replayed beside them, and every listed test passes.

**Related:** TD-703 (the design), TD-619 (a profile change reaches the team by Apply), TD-163 (Members…), TD-654.

---

## TD-717: A seat whose first hook is lost sits with its brief unsent for hours: the scraped send never fires for Claude Code, and nothing acts on the *brief not sent* mark

**Priority:** High
**Type:** debt
**Added:** 2026-10-15 (ao-paul, with Paul)
**Owner:** designer
**Kind:** design-first
**Status:** Designed 2026-10-15 (the designer; no mockup: one mark's words and one Inbox reason) — §4.1 *A launch whose hook never comes is recovered*. The scraped send keys on the composer reading empty, whatever the record's state. A record keeps `hook_seen_at`, and only that seeds the agent's fresh-hook table at load: the cause of the 47-minute mark was that a new Claude Code record is `confidence: hook` from its create, so a restart counted its missing hook as fresh. A supervised record marked *no hook since launch*, a seat included, is replayed once (`why: lost-brief`), and the senders of its open asks are told; a second loss is the person's. Neither a promote nor a fill waits for the other. `shiftlead-hook` keeps its traceback in `hook-errors.log`. Built by TD-720. Was: Open.
**Blocked by:** TD-720
**Location:** design §4.1 *A brief whose first hook is lost*, §4.5a *brief not sent* mark, §6 (seat fill and promote); `src/sessionorc/agent_wake.py` (`_first_prompt_road`, `_send_first_prompts`), `src/sessionorc/agent_tick.py` (`_observe`), `src/shiftlead/adapters/claude_code/screen_rules.toml`

**Why:** on 2026-10-15 at 00:42:07 MDT the home filled `ao-shiftlead-techlead-ao-1` for the held-PR ask on #1807; the ask on #1811 (opened 00:42:30) reached the same seat moments later. At 00:42:12 an auto promote restarted the host agent. The new pane's `SessionStart` hook failed with a traceback (run log `ao-shiftlead-techlead-ao-1-20261015T064207Z.log`), and no hook reached the agent. The seat sat at an empty composer, read `stalled?`, until Paul killed it at 08:37. Both asks passed their bound (`ao pr held` reads *bound 2h*) and lapsed to the person. Both PRs, each with a SHIP review, waited unread until Paul killed the seat at 08:37 and grinder-ao-1 asked again at 11:33, and grinder-ao-2's stacked #1818 waited with them. TD-348's fallback did not help, for three reasons:
- **The scraped send needs a scraped `idle` that Claude Code never produces.** `_first_prompt_road` sends once on a scraped `idle` after `FIRST_PROMPT_HOOK_WAIT`. No rule in `screen_rules.toml` matches Claude Code's empty composer, so a hook-fed record with no hook stays `working`, and `_observe` moves it to `stalled?`, never to `idle`. `ao explain --file` on the stuck screen, with and without the hook-error lines, reads *no screen rule matched*. So does a live idle session (`ao-shiftlead-entry-7`, *idle (hook)*).
- **The mark came late, and nothing acts on it.** The agent that started at 00:42:13 did not write *no hook since launch* in its 47 minutes. The agent that started at 01:29:46 wrote it at once (`the brief of ao-shiftlead-techlead-ao-1 was not sent: no hook since its launch`), 47 minutes after the launch, not the 5 minutes of `FIRST_PROMPT_BOUND`. Why the first agent never marked it is not yet known. Once written, the mark is only a card reason: the seat is neither relaunched nor closed, its asks are neither re-rung nor told, and the seat is not refilled because it is still held.
- **A launch just before a promote loses its first hook.** The fill and the restart were 5 s apart. The hook's traceback is not saved (the run log holds only its first line). That it ran against a half-replaced install is a guess, not confirmed.

**Fix** (for the designer to decide):
- A recovery for a Claude Code launch with no hook: a screen rule that reads the empty composer at launch as `idle` so the scraped send can fire, or a send that keys on the composer reading empty (the check `_first_prompt_typing` already makes) in place of a scraped `idle`.
- What the *brief not sent* mark does for an unattended seat: relaunch it once from its launch record (the seat stays due, as its asks are), then close it so the next fill starts clean, and tell the askers whose bound it holds.
- Why a restarted agent did not mark the record within `FIRST_PROMPT_BOUND`, and whether the bound counts from `created` across restarts.
- Whether a fill waits out a promote in progress, or a promote waits for a launch's first hook. And whether the `SessionStart` hook keeps its traceback somewhere readable.

**Done when** a seat launched with its first hook lost gets its brief within minutes, or is relaunched or closed with its askers told, and no seat sits with its brief unsent past `FIRST_PROMPT_BOUND`.

**Related:** TD-348 (the scraped send and the mark, built), TD-342 (its design), TD-341, TD-062 (the promote), TD-706 and TD-710 (the PRs that waited).

---

## TD-720: Build TD-717: a launch whose first hook is lost gets its brief from the composer, keeps *no hook* across a restart, and is replayed once

**Priority:** High
**Type:** debt
**Added:** 2026-10-15 (the designer, building TD-717's design)
**Owner:** grinder
**Kind:** live-check #1838
**Waits:** event — the next launch whose first hook is lost (a *brief not sent* mark); read again 2026-10-15 (the anchor): no record carries `first_prompt_error`, no `lost-brief` in any `restarts`
**Status:** Built — slice 1 (recovery of the send: `hook_seen_at`, the seeding at load, the scraped road on the composer, the mark) by #1834; slice 2 (the replay: `_lost_brief`, a seat's fill context, the senders told, *brief lost twice*) by #1837; slice 3 (the hook's traceback: `shiftlead.hook_entry`, `hook-errors.log`, `paths.hook_errors_file`) by #1838, with #1837's review leftovers: a test holds the fill ceiling's `tripped` to a `why: fill` ceiling, and a seat's ceiling with no `why` reads *restarts exhausted*, not *fills exhausted*. **Live check** once #1838 is live (`ao promote status`), read-only: `~/.local/share/shiftlead-venv/bin/shiftlead-hook` imports `shiftlead.hook_entry`, and no live record carries `first_prompt_error` *no hook since launch* without a `lost-brief` entry in its `restarts` or a `restart_ceiling` (`ao status -v --json`). The replay itself waits for the next lost first hook, an event nobody can cause: if none has come, write what was read here and make the **Owner** `anchor`. **Read 2026-10-15 (grinder-ao-1, live `96c68ac`):** the live `shiftlead-hook` imports `shiftlead.hook_entry` (`from shiftlead.hook_entry import main`, the module present in the venv); `ao status -v --json` lists 20 records, every one carrying the `hook_seen_at` field (set on the seven that have had a hook), none with `first_prompt_error`, no `lost-brief` in any `restarts`, and the only `restart_ceiling` is docs-audit-ao-1's `why: fill`. No lost first hook has come since #1838 went live, so the replay is unread: the anchor reads it at the next *brief not sent* mark — the record replayed once with `lost-brief`, its askers told — and archives the entry.
**Location:** design §4.1 *A launch whose hook never comes is recovered*, §6 rule 1 (*A lost brief*), §4.5a *brief not sent* mark and *Inbox row: restart*; `src/sessionorc/agent_wake.py` (`_first_prompt_road`, `_send_first_prompts`, `_first_prompt_typing`), `src/sessionorc/agent.py` (`_last_hook`'s seeding at load ~L629), `src/sessionorc/agent_hook.py` and `agent_tick.py` ~L3943 (where a hook is applied), `src/sessionorc/models.py` (`Session`), `src/sessionorc/agent_tick.py` (`_keep_running`, `_replay`), `src/shiftlead/adapters/claude_code/hook.py` (`main`), a new `src/shiftlead/hook_entry.py` and `pyproject.toml`'s `shiftlead-hook`, `src/sessionorc/paths.py` (`hook_errors_file`), `src/shiftlead/ui/inbox.py` (`restart_mark`), `src/shiftlead/ui/cards.py` (~L913, ~L951: a seat's `restart_ceiling` words)

**Why:** TD-717's design. On 2026-10-15 a techlead seat filled five seconds before an auto promote lost its first hook and sat at an empty composer for eight hours, while two held-PR asks lapsed to the person.

**Fix:** this touches `src/sessionorc/**`, so the techlead reads it (§4.9b). Slices, each its own PR:
1. **Recovery of the send** (`sessionorc`).
   - **`hook_seen_at`.** `Session` gains `hook_seen_at`, written by the pane's host at the first hook event applied to the record, live or drained from the queue. It is kept across saves and carried by nothing a replay copies.
   - **Seeding at load.** `_last_hook` is seeded for a `confidence == "hook"` record only when it carries `hook_seen_at` or its brief is not `_first_prompt_due`. Today every `confidence == "hook"` record is seeded. The second test keeps every record from before the field seeded as today.
   - **The scraped road.** `_first_prompt_road`'s `scraped` road no longer needs `state == "idle"`. It needs:
     - no `hook_seen_at`;
     - no entry in `_last_hook`;
     - no try made;
     - age at least `FIRST_PROMPT_HOOK_WAIT`;
     - a state that is not `needs-you`, `exited` or `closed`, and not `superseded_by`.

     `_first_prompt_typing` already insists on the composer reading empty for this road. An adapter with no `composer` reader keeps needing a scraped `idle`.
   - **The mark.** `_send_first_prompts`' *no hook since launch* mark reads `hook_seen_at`, not `_last_hook`, so it is written at `FIRST_PROMPT_BOUND` across restarts.
2. **The replay** (`sessionorc`).
   - **Who is replayed.** The tick replays a supervised record (a team member or a seat) whose `first_prompt_error` is *no hook since launch*, on the next tick. It first closes the live record, through `rpc_close` with closer `{by: tick, why: lost-brief}` and `_mark_closed`, as rules 2 and 7 do before `_replay` (agent_tick.py ~L785–L925); the run never started, so no `restart_blocked` applies. It then replays it as rule 1's replay does: `create` with `keep_mail`, `restarts` gaining `{at, why: "lost-brief"}`, counted in `RESTART_CEILING`.
   - **A second loss.** A record whose newest `restarts` entry is `lost-brief` and that is marked again is not replayed. It is written `restart_ceiling: {at, count, why: "lost-brief"}`, with `count` the record's `lost-brief` replays (so 1), and `ui/inbox.py`'s `restart_mark` and `cards.py`'s seat words (~L913, ~L951, which today read any seat ceiling as *fills exhausted*) read that `why` as *brief lost twice*, with no count. The fill ceiling's `tripped` test (~L2610) counts only a `why: "fill"` ceiling. The *brief not sent* mark stays.
   - **A seat's replay** passes the fill's `closing` (`filled_for(seat_due)`) again and sets `seat_filled`, as rule 3's fill does (agent_tick.py ~L2605–L2630). `lost-brief` is not counted by the fill ceiling (`why == "fill"`). A failed close is the replay entry's `error`.
   - **The senders.** After the replay, each sender of an `ask` or `steer` still open in the moved inbox gets a system note in the design's words. Bounds are unchanged.
   - **Who is left alone.** A person's own session, unsupervised, is never replayed.
3. **The hook's traceback** (`shiftlead`). `pyproject.toml`'s `shiftlead-hook` points at a new `shiftlead.hook_entry:main`, a module of the standard library alone (`src/shiftlead/__init__.py` is empty; `shiftlead.adapters.claude_code`'s `__init__` imports `sessionorc`, so the hook module cannot be the entry). It imports `shiftlead.adapters.claude_code.hook` inside a `try` and calls its `main`. An exception in the import is appended to `<home>/hook-errors.log`, the home read from `SHIFTLEAD_HOME` else `~/.shiftlead` without `sessionorc.paths` (cut to its last 256 KB when it passes it; `paths.hook_errors_file()` names the same file for readers), with the time, `SHIFTLEAD_SESSION` and the event name. The hook then exits 1, Claude Code's non-blocking error, so it still shows its one-line *hook error* as §4.1 says (the build found this line saying 0, which would hide it).
- **Tests:**
  - A Claude Code record created with no hook, reading `working` with an empty composer, gets its brief by the scraped send after `FIRST_PROMPT_HOOK_WAIT`.
  - The same record with a dialog on screen does not.
  - A live hook-fed record from before the field (no `hook_seen_at`, brief sent) is seeded at load.
  - An agent restarted after the launch does not seed it, sends it, and marks it at `FIRST_PROMPT_BOUND` when the composer never reads empty.
  - A record with `hook_seen_at` is seeded as before.
  - A marked seat is replayed once with `lost-brief`, its open ask moved and its sender told.
  - A second loss is not replayed and reads *brief lost twice*.
  - An unsupervised marked record is left alone.
  - The hook's import failure writes `hook-errors.log` and exits 1.

**Done when** a seat launched with its first hook lost gets its brief within `FIRST_PROMPT_HOOK_WAIT` of its composer reading empty, or is replayed once with its askers told, no record waits past `FIRST_PROMPT_BOUND` unmarked across an agent restart, and every listed test passes.

**Related:** TD-717 (the design), TD-348 (the scraped send and the mark), TD-342, TD-062 (the promote).

---

## TD-723: Backfill TD-722: samscrape and contractmatch under one ContractMatch project

**Priority:** Low
**Type:** feature
**Added:** 2026-10-15 (Paul, in an interactive session)
**Owner:** paul
**Kind:** build
**Status:** Open
**Blocked by:** TD-722
**Location:** `~/.shiftlead/org.yml` (`projects:` — today `samscrape` and `contractmatch`, one repo each); `~/samscrape/.shiftlead.yml`, `~/contractmatch/.shiftlead.yml` (their teams, defined there since 2026-10-03)

**Why:** Paul, 2026-10-15: *file a backfill to have samscrape and contractmatch both be on the ContractMatch project.* The two repos are one product; once a project is a heading on repos (TD-722), they share one, which draws them together on the Org page, lets their teams settle things by mail (§4.10 *across teams, by project*) and gives each session the other repo in its Project block.

**Fix:** once TD-722's build is live, one project, `contractmatch` (drawn *ContractMatch* if the design gives a heading a display name), listing both repos with their checkouts per host, in place of the two one-repo projects; the teams in each repo's `.shiftlead.yml` changed only as TD-722's migration says. A session live at the change keeps its old `project` badge (`samscrape`) until its next start, since the badge is written at create, so the two repos' teams mail each other only once each has restarted: make the change between runs, or accept the lag. Paul decided the change on 2026-10-15; Paul makes it once TD-722's build is live, with a backup of the org file first (`org.yml.bak-<date>-<what>`), as earlier org-file edits had.

**Done when** the Org page draws samscrape and contractmatch under one ContractMatch heading, and a samscrape session's brief names contractmatch's checkout in its Project block.

**Related:** TD-722.

---

## TD-748: The Queue: one card per effort — a TD and everything its `Blocked by:` joins, with its asks — ranked by the person, the rank the pick order; the Inbox keeps what names no TD

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-15 (Paul, in an interactive session)
**Owner:** designer
**Kind:** design-first
**Status:** Designed (2026-10-16, the designer): §4.4 *The Queue: cards, and the rank that orders them* (components with no exception, the top entry, rows and their state words, *stalled* at `QUEUE_STALL_AFTER`, the card's status, completed cards, lined and implicit keys, the rank file `<ledger stem>_rank.yml`, intents flushed as `queue: rank` PRs by the board's road, the rank the pick order); §4.5 screen 12 *Queue*, screen 6 *Where a row is drawn* (`home: inbox|queue`, the numbers unchanged) with its ledger and held rows on their cards, screen 11 item 2 the repo's cards; §4.5a nine Queue rows; §4.7 `ao rank`; §4.8 *Choosing in a free-pick lane* by rank; §4.10 `about` from the sender's claim and *The Inbox is a queue* renamed *Nothing leaves the Inbox without an answer*; glossary *Queue*, *card*, *rank*, *stalled*. Built by TD-769, TD-770, TD-771, which wait on dev-cadence#TD-119 (ledgerboard's components, keys and rank file; cadence's `queue-edit` class); the trial read back by TD-772. Was: Agreed 2026-10-15 — [ADR 2026-10-15](decisions/2026-10-15-queue.md), reviewed by Fable (four rounds converged, final AGREE) and Sonnet (three rounds, READY). A mockup drawn from the real ledger and inbox was reviewed with Paul over seven rounds the same evening, and the ADR amended from it (§9: one card rule with no exception, the header the top entry, no red, *waiting* beside *blocked*, `decision (paul)` drawn as a hold, no Snooze on the Queue, move buttons; round 6: the Queue's look from a frontend-design pass, titles wrapped in full, completed cards opening to each entry's `Resolved:`); Fable and Sonnet rounds on the amendment converged (round 2 READY) and round 6 was fact-checked by Sonnet; merged as #1887. **Paul lifted the hold 2026-10-15 23:44 MDT** (in an interactive session): the design round is next, the designer's. The builds will wait on a dev-cadence entry (the rank file's format and order in ledgerboard, the `queue-edit` audit class), filed when the design names it.
**Blocked by:** TD-769, TD-770, TD-771, TD-772
**Location:** `docs/decisions/2026-10-15-queue.md`; the design text it rewrites: §4.4 *Repo facts*, §4.5 screens 6 and 11 (4.5-ui.md:511–512, 1058), §4.5a a row per new control, §4.7 `ao rank` and `ao repo`, §4.8 *Choosing in a free-pick lane* (4.8-capabilities-roles.md:531–540), §4.10 (4.10-messages.md:719), the grinder and designer briefs

**Why:** Paul, 2026-10-15: *what I really care about is — where is TD/feature X that I added recently (already done, being worked, waiting to be worked, needs me for something)*, and *I want to know that my teams are working, and working the things I think are important first (dependencies considered)*. The Inbox is per message and orders by `Due:` dates he finds arbitrary; picks go by High/Medium/Low (36 of 56 open entries are Medium at filing) and nothing lets him say what comes next.

**Fix:** the ADR, whole: a `/queue` page of cards (connected `Blocked by:` components across repos, no exception), each headed by its top entry with its rows' states and its asks; groups Recently completed / In progress / Up next; the person drags or moves cards, the rank written to `docs/technical_debt_rank.yml` one line per member by debounced tool PRs, every undragged entry ranked implicitly by Priority, Type and id; the rank is the pick order by convention; the Inbox draws only what names no TD. Slices: (1) ledgerboard rank file + order + `ao rank` + briefs; (2) the Repo page's cards; (3) the Queue page. A two-week trial from slice 3, read by passed-over flags, idle-while-work lines and the oldest unanswered TD ask.

**Done when** the design says all of the ADR, its build entries are filed with the dev-cadence entry in their `Blocked by:`, and this entry's `Blocked by:` names them.

**Related:** TD-604, TD-693, TD-715, TD-716, TD-734, TD-749.

---

## TD-772: Read back the Queue's two-week trial to Paul — passed-over flags a week, idle-while-work lines, the oldest unanswered TD ask

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-16 (TD-748's design, the designer)
**Owner:** grinder
**Kind:** build
**Status:** Open — waits for the trial's two weeks since TD-771's last slice merged (#1967, 2026-10-16)
**Waits:** until 2026-10-30 — the Queue's two-week trial, from TD-771's last merge on 2026-10-16
**Location:** design §4.5 screen 12 (*A trial*); [ADR 2026-10-15](decisions/2026-10-15-queue.md) §7; the doing log (`doing.jsonl`), the `queue` reading's passed-over flags, the person inbox's open asks (read only)

**Why:** TD-748: Paul decides from the trial whether the Queue keeps its place beside the Inbox, and what changes in the lanes; no lane change is made before it reads back (ADR §7).

**Fix:** once its `Waits:` date has passed, read over the fourteen days: the passed-over flags per week (from the claims' first `ao doing` lines against the rank at the time, since the flags are not kept), the doing log's lines of a member idle or out of work while its lane held unclaimed pickable work, and the age of the oldest unanswered `ask` about a TD on each day; put them to Paul as one `ask` with the ADR's choices, his answer written into TD-748's ADR and, where it changes the design, a design-first entry.

**Done when** the three readings are written in this entry's Status and Paul's decision is recorded.

**Related:** TD-748, TD-771, TD-460 (the same shape of trial read).

---

## TD-785: Build TD-112 (2): the Gemini CLI adapter, launch and state — the profile's home, the layer, trust, `--session-id`, hooks to state, the title check, `permission_channel`, the kill guard, composer and title

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-16 (TD-112's design, the designer)
**Owner:** grinder
**Kind:** build
**Status:** Building — slice 1 #2014 (2026-10-17, grinder-ao-2): `confidence: title` read through `Session.told`, `permission_channel` on the view and every reader, `decide` and `ao allow`/`ao deny` refusing a tool without one, the kill guard at `shiftlead/kill_guard.py`. Slice 2 #2021 (2026-10-17): `shiftlead/adapters/gemini/` — the launch (binary, `--session-id`/`--resume`, `--model`, yolo under `skip`, the env and trust), the settings layer, `credentials_ok`, `doctor_profiles`, `title_state` (the glyph map), and the hook module (each event to its state, a confirmation as `needs-you` with no waiting call, the kill guard's exit 2); `shiftlead-hook --adapter gemini` dispatches to it. The entry point is not registered yet: the conformance suite tests every registered agent adapter against captured fixtures. Slice 3 #2024 (2026-10-17): the title verdict in the tick — `state_in_title` on the adapter, `explain(tail, title=…)` mapping the glyph, `_title_verdict` in `agent_tick._observe` (once `hook_seen_at`, past one tick since the last hook, `✋`'s text by the state it replaces, equal states no event, `stalled?` ended by `◇`/`✋` alone, nothing held), `ao explain` showing the title and `ao explain --file --title`. `ao doctor --probe` needs nothing more: its scratch launch is a `create` with the row's adapter, so it goes through that adapter's `launch` (read 2026-10-17). `SessionStart`'s and `BeforeAgent`'s `additionalContext` (`start_context`, mail, the repo's start hooks) are TD-786's Fix, left to it. Next, each needing fixtures captured from a real Gemini session (never written by hand, §4.3 *The conformance suite*): composer/title, the fixtures with the entry point, and the live walk — the Gemini profile for them asked of the techlead 2026-10-17 (m-322900fd26e2), passed up to Paul, who answered *Wait: Paul will set aside a separate profile home* — a signed-in `gemini-conformance` home (m-cad4683629b5); read 2026-10-17: no such home or profile yet (outcome `blocked`, m-8470dada41bc). Once it exists, the block below goes and the fixtures are captured from it. The techlead's note on merging #2024: a title `◇` over a hook-written `limited` would read `idle` by the letter of §4.3 — weigh it once Gemini's `limited` is built (TD-786's `usage`).
**Blocked by:** decision (paul)
**Location:** design §4.3 *The second adapter: Gemini CLI*, §4.2 *A title the tool writes its state into*, §4.2a, §4.5a **Allow / Deny**, §4.9b, §6 rule 3; new `src/shiftlead/adapters/gemini/` (`__init__.py`, `hook.py`); `src/sessionorc/agent_serve.py` (`_view`'s `permission_channel`); `src/sessionorc/agent_tick.py` (the `title` confidence, the screen-rule call with the title); `src/sessionorc/agent_hook.py` and the `decide` RPC; `src/shiftlead/hook_entry.py` (dispatch by an argv flag, `shiftlead-hook --adapter gemini`, as `--statusline` is read: a Gemini payload names no adapter and shares event names with Claude Code's; the adapter imported inside the `try`); `src/shiftlead/adapters/claude_code/guard.py` moved to `src/shiftlead/kill_guard.py`, both adapters importing it; `src/shiftlead/profiles.py` (`adapter: gemini`, its `config_dir`); the card's and Focus's Allow / Deny (`src/shiftlead/ui/templates/`, gated on `permission_channel`); `tests/conformance/fixtures/gemini/`

**Why:** TD-112: the second adapter, so the product's one-view claim is tested by a second tool.

**Fix:**
- **Profile**: `adapter: gemini`; its `config_dir` is the tool's home (`GEMINI_CLI_HOME`), default `~` (the tool's own `~/.gemini`); `credentials_ok` reads the home's `security.auth.selectedType` and `<home>/.gemini/gemini-credentials.json` (or `oauth_creds.json`), or `GEMINI_API_KEY` for a metered profile, whose launch also clears `GOOGLE_API_KEY` as a precaution (read only under Vertex auth or as a fallback). Signing a home in stays the person's.
- **`ao doctor`**: `doctor_profiles` reports the home, its credential, a metered key, a `GOOGLE_API_KEY` in the host agent's environment (a precaution's note), and the layer with whether `shiftlead-hook --adapter gemini` resolves; `--probe`'s scratch launch goes through the adapter's `launch` (`cli.py` ~L2646, `agent_doctor.py` ~L126–137).
- **Launch**: the binary by absolute path (`~/.npm-global/bin/gemini` found once and kept); `--session-id <uuid>` / `--resume <uuid>`; `--model`; `--approval-mode yolo` under `permission_prompts: skip`; env `GEMINI_CLI_HOME`, `GEMINI_FORCE_FILE_STORAGE=true`, `CLI_TITLE`, `SHIFTLEAD_SESSION`, `CADENCE_ATTENTION_SCOPE`, `GEMINI_CLI_SYSTEM_SETTINGS_PATH` → the layer file written per launch (hooks for BeforeAgent, AfterAgent, BeforeTool, AfterTool, Notification, SessionStart, SessionEnd calling `shiftlead-hook`; `hooksConfig.notifications: false`; `ui.hideTips`, `ui.hideBanner`; `general.enableAutoUpdate: false`; `ui.dynamicWindowTitle: true`, `ui.hideWindowTitle: false`, `ui.showStatusInTitle: false`). A resume launches from the record's directory.
- **Trust**: `GEMINI_CLI_TRUST_WORKSPACE=true` in every launch's environment; no trust file is written.
- **State**: the hook module (`shiftlead-hook --adapter gemini`) maps SessionStart → idle (the first hook the first prompt waits for), SessionEnd `exit` → exited (`clear` never), SessionStart `clear` adopting the payload's `session_id` as `adapter_id` and re-pointing the transcript, BeforeAgent → working with `prompt: true`, BeforeTool/AfterTool → working, AfterAgent → idle, Notification `ToolPermission` → needs-you, `kind` permission for `details.type` exec/edit/mcp/info and question otherwise; the record's transcript from the first non-empty `transcript_path`. The adapter's `explain` gets the pane's title (`PaneInfo`) and maps the leading glyph alone, with `confidence: title`: `◇` → idle, `✦`/`⏲` → working, `✋` → needs-you `{kind: question}` with no text; the tick writes the text by the state it replaces (*the tool's shell waits for input* over working or `stalled?`, *the tool waits for a confirmation* over idle). The tick replaces the record's state when the verdict's differs and the last hook is older than a tick, only once a hook has reached the record (`hook_seen_at`); a verdict whose state equals the record's never replaces its `pending`.
- **`confidence: title`** (§4.2): a fourth value of `Confidence` (`models.py` ~L22). Every check that reads `confidence == "hook"` reads `in ("hook", "title")` through one helper — `agent_tick.py` ~L1073, 1203, 1257, 1280, 1412, 1552, 2635 (`_team_causes`' `pending`), 2831, 4214 (a log line only); `agent_common.py` ~L1095, 1132; `agent_wake.py` ~L122, 254; `mail.py` ~L569, 571; `agent.py` ~L644; `doctor.py` ~L175 (grep `confidence` in `src/` for any added since). The screen-rule path (`agent_tick.py` ~L4109–4147): `_screen_verdict` asks the adapter's `explain` with the title inside `STALL_AFTER` for an adapter whose title carries its state (a hook older than one tick is the title's to overrule), and a title verdict whose state equals the record's never replaces its `pending` (a `✋` verdict over a `needs-you` record never replaces its `pending`: a Notification's permission stands); a verdict is compared with the record's hook-confirmed state only, never `exited`, `closed` or `stalled?` (a `stalled?` record ended by `◇` or `✋`, a `working` verdict over it no event), and a Match that changes nothing falls through to the stall check (the branch order at ~L4109–4127); a `title` verdict is **not held** — `_screen_held`/`_hook_state` never take it back to the state it replaced, which it drops as a hook report does. `ao explain --file` takes a title. The `scraped` badge (`cli.py` ~L244, `ui/cards.py` ~L233) stays scraped-only.
- **Kill guard**: BeforeTool of `run_shell_command` refused by `guard.refuse` exits 2 with the reason.
- **`permission_channel`**: on the adapter protocol (True where unset; Gemini False), carried on the record's view by `agent_serve._view` as `agent` is; `ao allow`/`ao deny` (`cli.py` ~L3441–3447) read it off the view and refuse in words (*answer it in the terminal*) before any call, rather than *has no pending permission* from a missing `tool_use_id`; the `decide` RPC refuses the same as the backstop; the person's rules (`agent_hook.py` ~L94–155) are never asked (no Gemini hook waits); rule 3's director `prompt` trigger and the manager's `pending` cause (`agent_tick.py` `_team_causes` ~L2633–2637) skip such a session; the readers that key on `tool_use_id` today — the state kind (`models.py` ~L290), `ui/inbox.py` ~L135, `ui/cards.py` ~L1052, 1084 — key on `pending.kind` and `permission_channel`; the card, Focus, the Inbox state row and the team card's **Answer needed** facet draw the text and *answer it in the terminal* with the Focus link, no Allow / Deny.
- **`composer`** (the `[>*!] ` row, the dim placeholder empty; a send to a working session is queued for the next turn) and **`title`** (`CLI_TITLE`'s bracket).
- **Tests**: fixtures captured from a scratch Gemini session (panes: idle, working, a confirmation; payloads of each event; titles); the offline suite passes for `gemini`; the trust env var (an untrusted fixture asserts the approval mode fell to `default`, not an exit); the layer's content; a needs-you cleared by a Working title; the guard's block; the Allow / Deny absence. Live: `pdm run conformance gemini --profile <p>` passes launch, state, send, permission and resume, output in the PR, on a profile Paul sets aside (ask him for it through the techlead before the live run).

**Done when** a Gemini CLI session started from ShiftLead reports working, idle and needs-you from its hooks, a cancelled turn reads idle from its title, `send` is confirmed, its permission shows no Allow / Deny, the offline suite passes for it, and the live walk's launch-to-resume steps pass with screenshots of its card in the PR (design §4.9b).

**Related:** TD-112, TD-784, TD-786.

---

## TD-786: Build TD-112 (3): the Gemini CLI adapter's reads — transcript, context, links, model, start context, mail, background, usage and spend; the full conformance suite live

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-16 (TD-112's design, the designer)
**Owner:** grinder
**Kind:** build
**Status:** Open
**Blocked by:** TD-785
**Location:** design §4.3 *The second adapter: Gemini CLI*; `src/shiftlead/adapters/gemini/` (a `transcript.py`); `tests/conformance/fixtures/gemini/`

**Why:** TD-112, slice 3: what the pages and the policies read from a session, so a Gemini session's card, transcript page and rule 5 (the context bound) works as a Claude Code one's does.

**Fix:**
- **`transcript_path(session_id, cwd, profile)`** (the home from the profile): the hook's reported path kept on the record, else `chats/session-*-<id[:8]>.jsonl` (top level only: `chats/<parent>/` holds subagents) whose first record's `sessionId` is the id.
- **`read_transcript`**: last line per record id, `$set`/`$patch`/`$rewindTo` applied, `user`/`gemini` mapped to neutral entries (a prompt, text, thoughts, tool calls with results); the tool's `<session_context>` preamble shown as context, not a prompt.
- **`context`**: the last `gemini` record's `tokens.input`, `window` 1,048,576 (256,000 for a Gemma model). **`links`**, **`model_in_use`** (the record's `model`), **`spend`** for a metered profile, the subagents' chats under `chats/<parent>/` included: `cache_read` = `cached`, `input` = `input` − `cached`, `output` = `output` + `thoughts`, `cache_write` absent, a record counted once at its last line (its `id` as the turn's `id` and `response`).
- **`start_context = True`**: the layer's SessionStart hook prints `additionalContext` from the record's `start_context`, on startup and resume; the host agent types the first prompt only after the SessionStart report.
- **`mail`**: BeforeAgent's `additionalContext` carries the inbox's top-of-turn delivery; the doorbell stays a pane write. **`links`** deduped by record id.
- **The repo's start hooks**: `shiftlead-hook --adapter gemini` on SessionStart runs `scripts/cadence_hooks.sh --session-start` itself where executable (the main checkout's runner, as §4.2's line does), its stdout in the same `additionalContext` (plain stdout would be an on-screen message), that hook's `timeout` at least 150000 ms (the runner's 150 s bound).
- **`background`**: the pane's `node`, its relaunched `node` child, that child's shell children.
- **`usage`**: a Google-login home's quota endpoint (`retrieveUserQuota`) into windows labelled by model; an API-key home None.
- **Tests**: fixtures of each record kind and patch; the offline suite in full for `gemini`. Live: the full walk, including the mail and the doorbell, output in the PR.

**Done when** a Gemini session's transcript page, context bound (rule 5), start context, mail and the cadence start hook work as a Claude Code session's do, and the full conformance suite passes for `gemini` offline and live.

**Related:** TD-112, TD-784, TD-785.

---

## TD-794: The Focus terminal shows a reply's lines cut off mid-word, and a block of it twice: a resize makes Claude Code repaint over lines tmux has already kept, and no refresh can mend them

**Priority:** Low
**Type:** debt
**Added:** 2026-10-16 (Paul, through Add entry; drafted by ao-shiftlead-techlead-ao-1)
**Owner:** grinder
**Kind:** evaluation
**Status:** Open
**Location:** design §4.6 *Attach behaviour with another client present* (`window-size latest`, and `window-size manual` plus a fixed `default-size` named as the fallback if the reflow upsets Claude Code's TUI), *Scrollback is tmux's, reached through tmux* (TD-022); §4.5 *The panel put away* (the terminal refits as a window resize does); `src/shiftlead/ui/static/app.js` (`new ResizeObserver(() => fit.fit())` and `term.onResize` → `{resize: [cols, rows]}`, ~L4536–L4537: one resize per observed frame, no debounce; `AO.setTermLook`, ~L70, refits on a face or size change); `src/shiftlead/ui/pty_bridge.py` (`resize`, ~L95, `setwinsize` on the attach's pty); `src/sessionorc/tmux.py` (the session options, `history-limit` ~L113)

**Why:** Paul, 2026-10-16, on `ao-queue-vs-inbox`'s Focus scrolled back in tmux's copy mode: the line *That was the last part of TD-769's live check. A builder will now find the check in its lane, read #1950 and cl* stops at *cl*, and the next line starts a different paragraph (*- The rank file: …*); a page refresh mended a different line but not this one. He asked whether resizing does it. The record says yes, and that the damage is in the pane itself, not the browser: `ao tail ao-shiftlead-ao-queue-vs-inbox -n 3000` (tmux's own capture, at the pane's current width) holds the same reply mangled differently — *a new* ends a line mid-sentence and the next line starts *- The rank file:*, and the bullets and the *The system recorded the move…* paragraph appear **twice**. Claude Code redraws its last frame when the pane's width changes. tmux had already put part of that frame into its history and reflowed it to the new width, so the redraw writes over lines that have moved and leaves the old copy's fragments behind. A browser refresh re-attaches and tmux repaints the visible screen, but history already written stays as it is: hence *helps, but not fully*. Any Focus resize can do it: a window drag, **» put away** and back, a terminal face or size from Settings, a second client under `window-size latest`. Today a drag sends one resize per observed frame. The harm: a reply the person reads back can silently lose words or repeat a block, and nothing on the page says the text is damaged.

**Fix:** an evaluation, then a build.
1. **Reproduce it on a scratch home** (`scripts/look_home.py`) with a Claude Code session printing a long reply. Resize the Focus by a drag, by **» put away**, and by a Settings size, then compare `tmux capture-pane -p -S -` before and after. Count the resizes the bridge receives per gesture.
2. **Measure the cheap cure first:** debounce the fit, so one gesture sends one `{resize}` with the final grid (~150 ms, after the last observed change), and send nothing when `cols`×`rows` is unchanged. Count the damaged lines with and without it.
3. If damage remains at one resize per gesture, weigh §4.6's named fallback, `window-size manual` with a fixed `default-size`. It ends reflow, but a narrower Focus then crops instead of wrapping. That changes behaviour the design describes, so it is a design PR first, with the person's look.
4. Whatever is left, which is Claude Code's own repaint, goes in the adapter's notes with the version it was seen on, so the next sighting is recognised and not re-filed.

**Done when** the evaluation's measurements are written here, the debounce has landed or been shown not to help, and any design change has a filed entry of its own.

**Related:** TD-022 (scrollback through tmux), TD-474 / TD-480 (*resized by another client*, the terminal mark), TD-288 (Settings' face and size refit the grid), TD-491 (the folded composer: *a resize would make the tool repaint*).

---

## TD-804: A promote is judged by `check` alone: nothing loads the pages or runs `ao` against the new live copy, so a build that passes CI on fixtures but breaks on the person's real state stays live until the person finds it and rolls back by hand

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-16 (Paul, in an interactive session)
**Owner:** designer
**Kind:** design-first
**Status:** Designed 2026-10-16 (design §6 *Promote* *The smoke*, §5 `promote:`, §4.5a *Inbox row: promote*, §4.7 `ao promote status`): an optional `smoke:` run at the home once `check` reads the commit; a failed smoke rolls back to `from` with the hold (steer m-bf390074fccb, the default), never after a rollback. A gate before the promote was set aside, and staging is left to the repo's `run`. Built by TD-811 (the home) and TD-812 (this repo's smoke).
**Blocked by:** TD-811, TD-812
**Location:** design §6 *Promote* (the three preconditions, the outcome read from `check`, *A rollback* and *The hold*), §5 `promote:` (`run`, `check`), §4.5a *Inbox row: promote*, §4.7 `ao promote`; `src/sessionorc/promote.py`; `.shiftlead.yml` `promote:`

**Why:** Paul, 2026-10-16, after the Queue tab stopped working for him in one browser during a day of merges: *maybe we require integration tests (if defined) to pass before a promote … Certainly for a production system working correctly would be vital.* Today a promote waits for (1) a clean checkout on main's head, (2) CI green on main's head and (3) nothing in flight, and is judged done when `check` reads the new commit. CI runs the test suite against fixtures; nothing loads a page or runs an `ao` read against the live copy with the person's real state (here 197 Queue cards and 886 completed in a month), so a break that only real data shows is found by the person. Promoting only at TD boundaries (an entry and everything its `Blocked by:` joins) was weighed with Paul and set aside: an effort can stay open for weeks (TD-748 until TD-772's read-back), and every merge to main is to be releasable, unfinished but never broken.

**Fix:** design it first:
1. A repo's own smoke command beside `run` and `check` in its `promote:` block (e.g. `smoke:`), optional, run by the home after `check` reads the new commit; what it is given (the commit, the live URL or port) and how long it may take.
2. What a failed smoke does: roll back to `from` at once as a rollback does (and the hold that follows), or only tell the person; whether that differs between `auto` and a press, and between a dev repo (this one) and a production one (samscrape's cluster).
3. This repo's smoke: each page (Org, Inbox, Queue, Repo, Settings, a Focus) loaded headless through the route the person uses, failing on an error status, a script error, a page that never commits or a load past a bound, plus `ao ls` and `ao inbox --json` reads; where it runs and what it needs (the Playwright setup the screenshots use).
4. Optionally the same smoke before the promote, on a scratch home built from main against a copy of the person's state (`scripts/look_home.py`), so a bad build is caught before it is live.
5. Staged promotes for a repo that has a staging target: smoke on staging, then production.
Then the build entries, named in `Blocked by:`.

**Done when** the design says what a smoke is, when it runs, what a failure does under `auto` and under a press, and this repo's own smoke, and the build entries are filed and named in `Blocked by:`.

**Related:** TD-120 and TD-132 (the promote policy), TD-226 (the rollback), TD-581 (the settle and the wait), TD-291 (`look_home.py`), TD-805 (page loads).

## TD-805: Every page loads slowly from the person's browser (Org, Repo, Inbox, Queue): nothing measures a load end to end, and the Queue takes 2.4 s to its first byte and sends 1.9 MB of uncompressed markup

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-16 (Paul, in an interactive session)
**Owner:** designer
**Kind:** evaluation
**Status:** Open
**Location:** `src/shiftlead/ui/app.py` (the page routes: `/`, `/inbox`, `/queue` `queue_page`, `/repo/<repo>`; `gzip_ok`, used for the counts' JSON and not for pages), `src/shiftlead/ui/queue.py`, `src/shiftlead/ui/static/app.js` (`connectEvents`, the per-page scripts), the templates; design §4.5 (the pages)

**Why:** Paul, 2026-10-16: *we need analysis done on our page loads — they all seem to take a long time (org, repo, inbox, queue).* Measured the same evening on kmaster's plain port (time to first byte, size): Org 0.43–0.55 s, 284 KB; Inbox 0.27–0.29 s, 847 KB; Repo (shiftlead) 0.57–0.73 s, 750 KB; Settings 0.15–0.22 s, 82 KB; Queue 2.37–2.66 s, 1.87 MB. A page is sent uncompressed with `Accept-Encoding: gzip` (the Queue's 1,873,503 bytes either way), and the person reaches the UI over the tailnet through `tailscale serve`, so the transfer, the parse of a large DOM and the page's scripts add to every number above, and none of that is measured.

**Fix:**
1. Measure each page end to end from a browser through the route the person uses: first byte, transfer, DOM ready, the page's scripts done, the stream up; and the server's time split by the RPCs each route makes (`list`, `queue`, `person_inbox`, `board_view`, …) and the render.
2. Say where each page's time goes and the size of each fix: compressing pages, markup that need not be sent at load (completed cards, archived rows, folded sections), RPCs read twice or that could be cached between loads, render work on the UI's one loop that holds every other request and Focus keystroke while it runs.
3. Set a budget per page, and file one build entry per page (or per shared cause) that misses it, each named in `Blocked by:`.

**Done when** each page's load is measured and broken down, a budget is set, and the build entries are filed and named in `Blocked by:`.

**Related:** TD-572 (the light stream for Focus and Inbox), TD-616 (the counts keeper), TD-771 (the Queue page), TD-804 (a promote's smoke, which can hold the budget).

## TD-812: Build TD-804 (2): this repo's smoke — `scripts/smoke.py`, every page loaded and `ao` read against the live copy, and `smoke:` in `.shiftlead.yml`

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-16 (TD-804's design, the designer)
**Owner:** grinder
**Kind:** build
**Status:** Open
**Blocked by:** TD-811
**Location:** design §6 *The smoke* (*This repo's smoke*); `scripts/smoke.py` (new; not a synced file); `.shiftlead.yml` `promote:`; `src/shiftlead/service.py` (`UNIT_DIR`, the `shiftlead-ui.service` unit's `--port`, `DEFAULT_PORT`); `tests/`

**Why:** TD-804, slice 2: the first repo's smoke, so a build that breaks a page or an `ao` read on Paul's real state is put back within minutes rather than found by him.

**Fix:**
- **`scripts/smoke.py`**, run as `"$HOME/.local/share/shiftlead-venv/bin/python" scripts/smoke.py` in the tree. It presses nothing and changes nothing: GETs and reads only, and never a Focus (`GET /focus/<id>` calls `seen`, and its attach resizes the pane).
  - It waits up to 90 s for the UI's plain port to answer. The address is read from the installed unit's `ExecStart` (`--bind` and `--port` in `service.UNIT_DIR / "shiftlead-ui.service"`): loopback when `--bind` is absent or `0.0.0.0`, and `service.DEFAULT_PORT` when the unit is absent or names no port.
  - It loads `/`, `/inbox`, `/queue`, `/repo/shiftlead` and `/settings`.
  - A page fails on any of: a status other than 200; a body that does not end its `</html>`; a load past `PAGE_BOUND` (the script's own 20 s, not a page budget: those are TD-805's); in the browser half, an uncaught script error (`pageerror`) or a `load` that never fires within the bound.
  - Where `settings.yml` has `reach.url`, it loads `/` and `/queue` once more through it. Those loads are told and fail nothing: a refusal is written in the log, and the last line says so on a pass.
  - It reads `ao status --json`, `ao repo --json` and `ao promote status --json`, each of which must exit 0 and parse. Never `ao inbox` (it marks entries read).
  - One log line per load: page, route, status, first byte, total, bytes.
  - It exits 0 when all pass, else 1 with the first failure as its last line.
- **The browser half.**
  - `scripts/smoke.py --setup` makes `~/.local/share/shiftlead-smoke/`: a venv made from the live venv's base interpreter, never inside the live venv, holding Playwright and its Chromium headless shell. It is run once, at a person's hand.
  - The script re-execs itself under that venv's python when it exists.
  - Without it, the HTTP half alone judges, and the log says *browser half skipped: run scripts/smoke.py --setup*.
- **`.shiftlead.yml`**: `smoke:` beside `run` and `check`, with its comment.
- **Tests:**
  - the page list, with no Focus;
  - the bind and port read from a unit file, and the defaults without one;
  - each failure (status, truncated body, bound, script error);
  - the `reach.url` loads, a refusal failing nothing;
  - the skipped browser half;
  - the exit code and last line, against a scratch UI or a stub server, never the live one.
- **README / CLAUDE.md:** one line beside the promote pair naming `--setup`.

**Done when** `scripts/smoke.py` passes against a healthy UI and fails, saying why, on each case above; `.shiftlead.yml` carries `smoke:`; and the next promote's log shows the smoke's lines.

**Related:** TD-804 (the design), TD-811 (the home's smoke), TD-805 (page loads: the log lines are its first measure, and its budgets replace `PAGE_BOUND`), TD-291 (`look_home.py`, a scratch UI for the tests).

## TD-816: The Queue page reads as one long list: its groups carry no count and do not fold, a card's attached mail is one run with no sections or Dismiss all, a filter press reloads the 2.4 s page, and the picks are lost on the Queue tab

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-16 (Paul, in an interactive session)
**Owner:** designer
**Kind:** design-first
**Status:** Open
**Location:** design §4.5 screen 12 (*Groups* ~L1165, *What is attached to a row* ~L1203, *Filters* ~L1238), §4.5a *Queue page* rows (*card*, *Recently completed: 4 h · day · week · month*, *filters, find*); `src/shiftlead/ui/templates/queue_part.html` (the three `rsec` groups), `queue_filters.html` (each pick an `<a href>` to `/queue?…`), `queue_row.html` (`attached`: one `qmail` run per row), `src/shiftlead/ui/queue.py` (`attach`, `queue_picks`, `apply_picks`, `WINDOW_LABEL`), `src/shiftlead/ui/static/app.js` (`AO.queue`, `store` key `qwin`; the Inbox's `dismissOnScreen` for *Dismiss all*), `base.html` (the Queue tab's bare `href="/queue"`)

**Why:** Paul, 2026-10-16, on the Queue page while it is still being built out: *each section should have a count e.g. "Recently completed (9)" and should be collapsible. Each card should have "Needs you (n)", "Steering (n)", "Answered for you (n)" and "FYI (n)" sections with counts that are hidden if the count is zero, and the "Answered for you" and "FYI" sections should have dismiss all options. The filters should be far more responsive (right now they are seconds per push) … The filter settings seem to get lost/reset - maybe on navigate? They should persist (and so should the duration setting of 4h, day, etc).* What the code does today:
- **Groups.** Each writes its number as a quiet word after the heading, not in it: *In progress* and *Up next* a bare number, *Recently completed* the window's (*9 in the last day*, and each window button its own, *Day (9)*). No group folds.
- **A card's mail.** `attach` draws a row's Inbox items as one `qmail` run in the Inbox's order (`later`, `needs`, `steering`, `coming`, `waiting`, `answered`, `fyi`), with no heading or count per section; *Dismiss all* exists only on the Inbox page's Answered for you and FYI groups (TD-599, §4.10).
- **Filters.** Every pick is a link that reloads `/queue`, which takes 2.4–2.7 s to its first byte and sends 1.9 MB (TD-805's measure), although the page already draws every card and marks what a pick hides `qoff`.
- **Persistence.** The picks live only in the URL, and the top bar's Queue tab is a bare `/queue`, so leaving the page and coming back by the tab drops them. The window is remembered per browser (`ao.qwin` in `localStorage`), so it carries neither to another browser (Paul uses Edge and Firefox) nor to the Repo page's own picker (`ao.win`); whether it is also lost within one browser is not yet reproduced.

**Fix:** design it first, then file the builds:
1. **Groups.** Each heading carries its count in the words, *Recently completed (9)* (the window's), *In progress (n)*, *Up next (n)*, and folds on a press, remembered in the browser as a card's fold is; a find, a `#TD-n` or a `?card=` address opens the group that holds its target.
2. **A card's sections, and no due dates on the Queue.** Under a row (the top entry's in the header), its attached items are grouped *Needs you (n)*, *Steering (n)*, *Waiting on them (n)*, *Answered for you (n)*, *FYI (n)* — the Inbox's names and order — each heading hidden at 0; *Answered for you* and *FYI* each carry **Dismiss all**, the Inbox's rule (the items on screen, by id, never one that arrived after the page was drawn). There is no *Later* or *Coming* on a card, because a `Due:` date orders nothing on the Queue. Paul, the same evening: *It does not make sense to me to have due dates affecting the queue. If an item is up next, then its up next. If it needs user input, it needs user input. Regardless of a due date.* So a board line about a card's entry is under *Needs you*, whatever its `Due:` (today `split_later` moves one not yet due to the card as `coming`, counted nowhere). A ledger row that is the person's (*yours to do*, *your decision*; today `later`, drawn first under its row) is *Needs you* too, drawn at its own row with its answers and Reply. Both make the card's status *needs you* and count in its *Needs you (n)* and the filter's. Say whether they also count in the top bar's number, which today leaves out what is not yet due. A `Waits:` date is not a `Due:` date: it says when an entry may start (TD-772's trial), and it stays the row's *waits until <date>*. Say whether the sections are a card's or each row's.
3. **Filters answer at once.** A pick re-marks `qoff` in the browser from what each card already carries (its status, kinds, repo and find text as `data-` attributes) and rewrites the URL with `history.replaceState`, with no request; the server keeps applying the URL's picks on a load, so a shared link still works. The find box filters as it is typed.
4. **Picks persist.** The last picks are remembered in the browser and restored on a bare `/queue` (the tab), and **All** clears them; say whether the window is one choice across the Queue and the Repo page, and whether either belongs in `settings.yml` (the person's, across browsers) rather than `localStorage`.

**Done when** the design says all four, and the build entries are filed and named in `Blocked by:`.

**Related:** TD-748 (the Queue), TD-771 (the Queue page), TD-772 (its trial, read back 2026-10-30), TD-599 (the Inbox's Dismiss all), TD-805 (page loads: the Queue's 2.4 s and 1.9 MB, which a client-side filter stops paying per press).

---

## TD-818: Nobody can see what each team member spends: the spend ledger reads metered profiles only, so on a subscription a seat's tokens, and its cost per finished entry, are nowhere

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-16 (Paul, in an interactive session: *is there a way to keep a token count per team member … see which ones use the most tokens, and make adjustments if there are surprises*)
**Owner:** designer
**Kind:** design-first
**Status:** Open
**Location:** design §4.3 *Spend per turn* (TD-128: `spend()` is asked for a `metered` profile only), §4.2a (a subscription profile is read by its quota windows), §4.4 (the home's spend ledger), §4.5 screen 1 (the team card), §4.7 (`ao status -v`); `src/shiftlead/adapters/claude_code/transcript.py` (the reader), `src/shiftlead/profiles.py`

**Why:**
- Every team member on Paul's subscription spends from one account-wide quota. The usage chip says how much of the window is gone, not who spent it, and the window keeps no history (ADR 2026-10-08: *the weekly window … is account-wide and keeps no history, so it cannot be split per grinder*).
- The data exists. Each Claude Code transcript carries the `usage` of every reply, and each seat has its own worktree. A one-off read of 2026-10-02 to 10-16 (Paul's session, read only) put shiftlead's grinders at 2.5–3.2B tokens each, the designer at 1.5B and the techlead at 0.55B. Cache reads were about 97% of the tokens, so a raw token total misleads: the read has to weigh the four kinds (TD-128 keeps them apart) and the model, since a Fable request costs 2.5× an Opus one at the same size on Anthropic's API list prices ($10 against $4 per million input tokens).
- The readings that have been taken (TD-378's ADR, TD-460's trial) were each a hand-written script over the transcripts. Every new question pays for that again.

**Fix:** design first.
- Read spend per turn for **every** profile the adapter can read it for, a subscription's included. A subscription's reading has a different meaning: weighted tokens at the profile's declared prices, or at the API's as an API-equivalent figure, labelled so, never a bill.
- Key each turn by the session record it came from (seat, role, team, repo). Subagent turns go to their parent's record, marked so.
- Show per seat, per role and per team, over a day and a week:
  - weighted spend, and its share of the team's;
  - **spend per finished entry**: the seat's spend between claim and `ao progress done`, the sum over entries it finished (a design and a build counted apart);
  - the share spent past each context size (200k, 300k), the reading TD-460 had to write by hand.
- Say where it is drawn (the team card, `ao status -v`, a Spend page or a fold on the Org) and what is kept, for how long (TD-131's limits).
- Which prices a subscription is weighed at is the person's: a `steer` with the API's list prices as the default.

**Done when** the design says what is read for a subscription profile, how a turn is attributed to a seat, which readings are drawn where, and how long they are kept, and the build entries are filed and named in `Blocked by:`.

**Related:** TD-128 and TD-151 (metered spend, archived), TD-378 and TD-460 (the hand readings), TD-819 (team shape reads this), TD-820 (a surprise this would have shown), TD-131 (retention).

---

## TD-819: Whether a team's shape is right — another designer, one grinder fewer — cannot be read: nothing records how long each member waited for work, or how long work waited for a member

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-16 (Paul, in an interactive session: *is there a way to tell if a different shape for a team would be more efficient (e.g. another designer, fewer grinders, etc)?*)
**Owner:** designer
**Kind:** design-first
**Status:** Open
**Location:** design §4.9c (flows: design → build → review), §4.2 (the state feed), §4.4 (records, `doing.jsonl`), §4.5 screen 1 (the team card, TDs in motion); `~/.shiftlead/sessions/` (current state only), `~/.shiftlead/events/` (empty), `doing.jsonl`

**Why:**
- A team is a pipeline: filed → design-first → designed → build → review → merge. A shape is right when no stage starves the next and no member idles with work waiting. Two readings answer it: a member's **starved time** (idle, out of work, while it is on the team) and each handoff's **queue depth and age** (design-first entries waiting for the designer, pickable builds waiting for a grinder, PRs waiting for the techlead or a merge).
- Neither can be read. A session record holds its current state and nothing before it, the events directory is empty, and `doing.jsonl` has a member's progress lines but not its idle stretches. TD-772 will read *idle while its lane held pickable work* from the doing log by hand for one trial; that reading is one stage's, and it is written once.
- Throughput alone does not decide it. shiftlead merged 1,071 PRs from 2026-10-02 to 10-16 (GitHub's search count), and the median open time over the 1,000 the list returns was about 14 minutes, so merging is not the bottleneck. Which stage is cannot be read from that number.

**Fix:** design first.
- Keep each session's state changes (working, idle, waiting on whom, out of work, limited) as a timeline per record, held as long as TD-818's readings are.
- Sample each handoff's queue on the tick: depth and the oldest item's age.
- From the two, draw per team and per role over a day and a week:
  - starved time (a member idle while nothing in its lane is pickable);
  - blocked time (waiting on a person, a review or CI);
  - each queue's depth and age;
  - beside TD-818's spend per finished entry.
- Say what the reading suggests, never acts on: *grinders starved 40% of the week while design-first waited 0: one grinder fewer, or a second designer*; *pickable builds aged 6 h with both grinders busy: one grinder more*. The team's shape stays the person's to change (`.shiftlead.yml`, Members…).
- Usage-window waits (`limited`) are counted apart: a shape cannot fix the account's quota.

**Done when** the design says what is recorded, what is drawn per role and team, and the wording of a shape suggestion, and the build entries are filed and named in `Blocked by:`.

**Related:** TD-818 (spend per finished entry), TD-772 (the Queue trial's idle-while-work reading), TD-749 (a lane split that idled a grinder, archived), TD-118 (where a team-run-day spends tokens, archived).

---

## TD-820: The designer runs far past its context bound inside one design: nearly a quarter of its requests read over 300k tokens and its median is 209k, against the grinders' 151k

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-16 (Paul's session, from a read of the transcripts asked for by Paul)
**Owner:** designer
**Kind:** design-first
**Status:** Open
**Location:** design §6 rule 5 (*Context bound*: restarts between entries, never mid-turn), §4.8 *The bound has two layers* (300k for every role that sets none); `src/shiftlead/briefs/designer.md`; `.shiftlead.yml` (`roles: designer:` sets no `context:`)

**Why:**
- designer-ao-1 is an unattended member, so it takes the 300k default bound. Rule 5 only asks a member to restart between entries, and a design is one long entry: the Fable proposal, its rounds with Sonnet and Fable reviewers, the fact-check and the PR.
- Read from its transcripts, 2026-10-09 to 10-16 (main conversation, each response once): 4,105 requests, median context 209k, p90 395k, **955 over 300k**, the largest 813k. Over the same days grinder-ao-1 read a median 151k, p90 223k, and 48 requests over 300k. The techlead read a median 88k, with none over 300k.
- Requests over 200k carried 35% of the weighted cost in TD-378's week. Fable's input is priced at 2.5× Opus 5.5's ($10 against $4 per million tokens on Anthropic's API list), and about 30% of the designer's requests that week were Fable.
- TD-460 measured the grinders' bound and found it flat: the saving in context went on extra restarts. That says nothing about the designer, whose context grows within one entry, where the bound never acts.

**Fix:** design first; measure before choosing.
- From the transcripts, find where a design's context grows: reading the design sections, the review rounds coming back, fact-checks.
- The candidates:
  - a restart point inside a design (after the proposal is written to its branch, before the rounds), carried by the branch and the entry;
  - review rounds run as fresh subagents that return a verdict, not whole transcripts;
  - a designer-specific bound with a mid-entry checkpoint;
  - nothing, if the cost per finished design is already lowest as it is.
- TD-818's spend per finished entry is the measure once it exists. Until then, the hand read is the same as TD-460's.

**Done when** the reading says where the designer's context goes, and the design either gives the designer a restart point or a bound with its build entries filed and named in `Blocked by:`, or says, with the numbers, why it stays as it is.

**Related:** TD-460 (the grinders' bound trial), TD-378 (the prompt cache, archived), TD-818 (spend per seat), rule 5.

---

## TD-823: `permission_prompts: ask` asks nothing Claude Code's `auto` mode allows: its classifier decides before any hook sees a permission, so the person's rules, the director seat and the person never see it

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-16 (grinder-ao-2, TD-784's live walk)
**Owner:** designer
**Kind:** design-first
**Status:** Open
**Location:** design §4.2a *`permission_prompts: ask | skip`*, §4.9b *The director seat*; `src/shiftlead/adapters/claude_code/__init__.py` (`launch`: under `ask` it only drops `--dangerously-skip-permissions`); `scripts/conformance.py` (`ASKING_ARGS`)

**Why:** TD-784's first live run (2026-10-16, Claude Code 2.1.302, a fresh config dir): the unattended launch under `permission_prompts: ask` ran in `permission_mode: auto` — the tool's own default now (its `.claude.json` carries `hasSeenAutoDefaultNotice`), and the mode Paul's own `~/.claude/settings.json` sets (`defaultMode: auto`). Its classifier allowed `touch conformance-permission.txt` with a `PreToolUse` and no `PermissionRequest`, so nothing reached the host agent. §4.2a says that under `ask` *the prompts meet the person's rules, the director seat and then the person*; under `auto` only the ones the classifier will not allow do. The walk now launches with `--permission-mode manual` and sees the permission.

**Fix:** design it first: under `ask`, does the adapter launch with the tool's asking mode (Claude Code's `--permission-mode manual`), so every prompt meets shiftlead's road, or leave the tool's mode to the person's settings (the classifier as one more of the person's rules, before the director seat), saying so in §4.2a — or a third word for it. The tool's mode is the person's setting (`defaultMode`), so whichever the design takes is said where the person reads `permission_prompts`.

**Done when** §4.2a says what `ask` does under a tool whose default mode allows on its own, and the build entry is filed and named in `Blocked by:`.

**Related:** TD-636 and TD-641 (`permission_prompts`), TD-784 (the walk).

---

## TD-846: Compliance before shiftlead is sold: SOC 2 and ISO 27001 wait for a hosted or team edition; the EU Cyber Resilience Act and the supply-chain basics a buyer asks for come first

**Priority:** Low
**Added:** 2026-10-17
**Owner:** paul
**Kind:** decision
**Blocked by:** decision (paul) — which paid option, if any, [the monetisation decision](decisions/2026-10-13-monetisation.md) takes; nothing here is started before then (Paul, 2026-10-17: *low priority, pushed off until we get closer to monetization*)
**Status:** Open — deferred.
**Location:** `docs/decisions/2026-10-13-monetisation.md`; the repo root (`SECURITY.md`, absent); `.github/workflows/` (release and scanning); the wheel `ao service install` writes to `~/.shiftlead/wheels/`

**Why:**
- Paul asked (2026-10-17, the `security` session) whether SOC 2, ISO or other compliance applies to shiftlead.
- SOC 2 and ISO 27001 certify an organisation that operates a service and holds customer data. shiftlead today is MIT, self-hosted, one person per home (design §1, §2), with nothing hosted, so there is nothing for an auditor to examine. They begin to matter on options 2, 3 and 5 of the monetisation doc:
  - **A hosted or relay part** (option 3's managed host, option 5's ciphertext relay; anything that carries sessions, transcripts or keys): SOC 2 Type II (US buyers) or ISO 27001 (EU buyers), and GDPR processor duties with a DPA.
  - **A paid team edition sold to companies:** their procurement sends security questionnaires even for self-hosted software. A SOC 2 report helps and is not required. The product features they need are the ones option 2 lists: identity-provider sign-in, roles, an audit log of who typed into which session.
- What applies to self-hosted software regardless, and is cheap before it is dear:
  - **The EU Cyber Resilience Act.** It covers software with digital elements placed on the EU market. Non-commercial open source is largely exempt; a paid edition is in scope. Vulnerability reporting duties apply from September 2026, the rest from December 2027: a vulnerability disclosure policy, an SBOM, security updates for the support period, secure-by-default settings.
  - **Supply-chain basics a questionnaire asks for:** a `SECURITY.md`, an SBOM of the wheel, signed release artifacts, dependency and secret scanning in CI.
  - **Sensitive data shiftlead keeps:** run logs, mail and pane scrollback under `~/.shiftlead` can hold secrets and personal data. Their retention, redaction and file modes matter more to a real user than a certificate (TD-131 holds retention for mail and records).
  - **Anthropic's terms for unattended use:** whether `ao new --unattended` on a subscription, rather than an API key under the Commercial Terms, fits Anthropic's usage terms. Not read yet; read it before shiftlead is offered to others.
  - **The EU AI Act:** orchestrating general-purpose models for coding is very unlikely to be high-risk; at most transparency duties, only if sold in the EU.
- The dates and scopes above are as known on 2026-10-17 and were not re-read against the regulations' text; check them when the entry is picked up.

**Fix:** when the monetisation decision names a paid option:
1. Read the CRA's obligations for that option (manufacturer of a commercial product, or open-source steward) and Anthropic's current terms for unattended use, and write the answer here.
2. Land the basics as build entries: `SECURITY.md` with a disclosure address, an SBOM and a signature on the release wheel, dependency and secret scanning in CI, and a retention and file-mode rule for `~/.shiftlead`'s logs.
3. Only for a hosted part or a team edition: choose SOC 2 Type II or ISO 27001 by the first buyers' region, and file the controls work (access control, audit log, change management; the cadence's branch, review and merge loop is already most of the last).

**Done when** the paid option's obligations are written here, and the basics are built or each is archived with its reason.

**Related:** [`docs/decisions/2026-10-13-monetisation.md`](decisions/2026-10-13-monetisation.md) (options 2, 3 and 5), TD-131 (retention), design §2 (multi-user access is a non-goal for now).
