# ADR 2026-09-25: Settings — what is settable, where it lives, and the shape of a settings page

Status: audit and proposal (2026-09-25, a cloud session with Paul). **Nothing here changes the
design by itself**; the design round that follows writes §4.5 screen 7, §4.5a's controls and §5's
one file change, and this note is the reasoning behind them. Sonnet review rounds are recorded at
the end.

## Context

Paul, 2026-09-25: *we have many settings in this project and no way to currently set them other
than editing files directly … 1. do an audit to see what things are settable, and if we should
offer more. 2. see if we should collapse the location of the various settings. After those we will
start a design session to lay out a new settings screen.* And: *I definitely want the subscription
percentage (and budget) limits settable — I will probably change those once or twice weekly based
on the amount of manual work I need to do.*

Two agents read the code and the design independently; the anchor of this note is what both found.

## 1. What is settable today

Design §5 *Configuration* already has a rule — **a setting lives with whoever owns it and whoever
acts on it** — and six scopes. The code matches it, with the gaps listed in §3 below.

| Scope | File | Holds | Written by | Re-read | On a page today |
|---|---|---|---|---|---|
| Host topology | `~/.agentorc/hosts.yml` (`sessionorc.hosts`) | `local.{name, vscode_host, local, volatile, repos_registry, runs_keep_days, identity}`, `home:`, `nodes:`, `link:` | by hand; `ao host forget` removes a `nodes:` line | UI and CLI per request (mtime); the **agent snapshots `name`, `home`, `identity` and `nodes:` at start** | shown in the top bar; not editable |
| Profiles | `~/.agentorc/profiles.yml` (`agentorc.profiles`) | `default`, per profile `adapter, account, model, config_dir, permission_wait, extra_args, unattended_args` | by hand; unknown keys dropped silently | per call | the New form picks one |
| Org | `~/.agentorc/org.yml` (`agentorc.org`) | `projects`, `teams` (manager, techlead, seats, members), an org-wide `roles:` overlay | by hand | per call, clients only, never the agent | the Teams line reads it |
| Host-agent settings | `~/.agentorc/settings.yml` (`sessionorc.settings`) | `usage_gate.<profile>.<window>` = reserve or `{per_day}`; `schedules:` designed (TD-133), not built | the `set_settings` RPC only, person-only, via `ao gate`; a hand edit is read next tick | every tick | the usage chip shows the resulting line |
| The person's own | `~/.agentorc/ui.yml` (`agentorc.ui.uiconf`) | `open_in:` — `vscode` \| `none` \| `{label, url}`; nothing else | by hand; no RPC | mtime | a bad value is named on the Org |
| Repo | `.agentorc.yml`, in git (`agentorc.repoconfig`) | `roles`, `controllers`, `ledger`, `teams` do work; `adapter`, `worktrees`, `anchor`, `unattended`, `ready_when`, `commands` are parsed and read by nothing; `promote:` (designed, TD-132) is refused as an unknown key | PRs | per call, clients only | none |
| Per row | `attention.json`, `person_inbox.json` | state-row snoozes; entry snoozes and paused steers | Inbox RPCs, person-only | live | the Inbox |
| Per session | the record | mode, stop time, grants, controllers | UI and CLI | live | card and Focus |
| This browser | `localStorage` (`ao.*`) | theme, *mine*, the Inbox filter and folds, the Shell's last directory | the page | live | implicit |
| Environment | `AGENTORC_HOME`; `AGENTORC_TICK` | the home; the tick period (an override the design does not name — §5's *no env-var overrides* is TD-004's rule for `hosts.yml`) | the shell or the unit | start | no |
| Service | the systemd units | bind, port, PATH, the home if set at install | `ao service install` | install | no |
| Code | about ninety module constants | every bound and cadence (`LEASE_TTL`, `MAIL_RETENTION`, `RESTART_CEILING`, …); some twenty-five are named in the design | a PR | never | no |

**There is no settings page, route or control.** The design decided one exists in principle on
2026-09-22 (TD-100; Paul: *eventually we will want a settings page where we can change this
manually in the shiftlead ui*), named its first three settings (the 5-hour reserve, the weekly
per-day reserve, *start the team at the weekly reset*, off), and §5, §6 and §4.5a defer to it
*once §4.5 lists one* — which §4.5 never did, so by §4.5a's rule the page does not exist. The
terminal's font is two literals in `app.js` (`AO.TERM_OPTS`: JetBrains Mono, 13 px); goal 12 says
the panes stay dark in both themes and nothing more, so it may be a preference.

## 2. Should more be offered

**Offer on the page, editable.** (a) The **usage reserves**, per profile and per window label as
the adapter reports them, flat or per day, grouped by account as the chip is (TD-122): the one
setting Paul moves weekly, RPC-backed today, effective next tick. (b) The **budget limits** of a
metered profile — an amount per window with a warning line and a hard stop — once TD-128 designs
them; the page reserves the row. (c) **Schedules** per team (TD-133; off by default). (d) The
**person's own**: `open_in`, and the **terminal's font size, and its face from the monospace faces
the browser has** — JetBrains Mono bundled, ligatures off regardless (goal 12: *a pane you type
into*), `monospace` always the fallback — a person who reads terminals all day wants it, and it is
one xterm option and a refit. Not the default profile: `profiles.yml` has no writer and sits beside
credentials; a `person:` override of it is a later question.

**Show on the page, read-only, each value with an *i* mark saying which file it comes from, when
it is re-read, and whether a change needs the agent restarted, and the editor button `open_in`
already gives** (Paul, 2026-09-25: *show the readonly values, with an info icon telling where they
come from*): hosts, profiles, org and the repo file. Each has a reason to stay a file: identity
mode deliberately has no RPC so a locked-out person recovers by hand (§4.8a); profiles sit beside
credentials; the org file is a team editor in waiting; the repo file belongs to PRs.

**Never on the page.** The constants (§4.10: the bounds are part of the design); the terminal's
colours (goal 12); density (TD-130 turned it down for the browser's zoom); live identity mode.

## 3. Should the locations collapse

**One move, not a merge.** The scopes are right; the fault is that *the person's own* is split
across two files with different write paths (`ui.yml` by hand, `settings.yml` by RPC), which is the
one thing a page cannot work around. **Retire `ui.yml` into `settings.yml` under a `person:` key**
(`open_in`, the terminal font and size, later anything else that is a person's alone): one file the
page writes, one person-only RPC that writes it (`set_settings` grows the key), one backup entry
(today neither `settings.yml` — the only file the agent writes — nor `ui.yml` is in
`BACKUP_MEMBERS`; the merged file goes in, which the round's build entry names), and the agent ignores `person:`, which keeps §5's rule that nothing of the
person's reaches a host agent. Why that is safe rather than merely asserted: the gate reads
`usage_gate:` by key and `schedules:` by key (`sessionorc.settings.reserves`), a stanza it does not
name is inert to it, and the page reaches the key only through the agent's own RPCs; the coupling is one file on
one disk, not one reader. §5's reason for two files — a second person, or a UI off the home —
is the trigger §5 already names for moving the scope when it comes. The page reads and writes through the agent's RPCs, never the file (§4 *Nodes and ownership*).
The theme stays per browser: a phone and a desk differ.

**YAML stays.** Python reads TOML from the standard library (3.11+) but has no writer in it; a
write needs a third-party package, so a move buys nothing on dependencies (PyYAML is one already)
and costs a migration of five files, the node-copy writer, README and tests. Revisit at the TD-060
rename boundary, when every path changes anyway. The one real cost of YAML — `safe_dump` flattens a
hand-written file's comments — falls away for `settings.yml`, which is machine-owned (§5: nobody
edits it by hand while the agent runs) and carries no comments.

**Housekeeping found by the audit** (a ledger entry, not design): the six dead `.agentorc.yml`
keys; `promote:` refused today; `settings.yml` not backed up; the org-level `roles:` overlay skips
the validation the repo layer gets; `AGENTORC_TICK` undocumented; a hand edit of `home:`, the host's
name or `identity` leaves the agent and the UI disagreeing until a restart; bind and port live in
three places; `hosts.yml` `transport` and `ssh` in the design and not the code, and `local.person` parsed but consumed by nothing;
`AGENTORC_PROFILE` exported and read by nothing.

## 4. The page, in outline (for the design round)

Screen 7, **Settings**, `/settings`, a top-bar tab once built (TD-123's rule). One centred column
as the Inbox (TD-082), sections in this order, each with its *i* mark: **Usage** (the reserves
matrix by account → profile → window, each cell a reserve, flat or per day, with the line it
produces; budgets per metered profile when TD-128 lands), **Schedules** (per team; drawn disabled with *not built — TD-133* until that lands, as the budget rows wait on TD-128),
**You**, in two halves the §4.5a rows keep apart by write path: *yours everywhere* — `open_in`,
the terminal's size and face (a typed or picked `font-family` name with the ordinary CSS fallback;
no font enumeration), written through `set_settings` — and *this browser* — theme, *mine*, the
folds, remembered in `localStorage`, with **Reset this browser**, a new control, **Hosts**, **Profiles**, **Org**, **Repos** (read-only, display only: the file's values, the
re-read rule and whether a change needs a restart on the *i* mark; **Repos** one card per
registered checkout, its `.agentorc.yml` when it has one; an **Open file** button per file, a new
use of `open_in`'s template with a file path rather than a session directory, which the round adds
to the editor button's §4.5a row). The disabled Schedules rows and the *set at <node>* words are
display only. Writes to the home's file go through `set_settings`, the browser half of **You** through
`localStorage`, and nothing on the page writes any other file. **Nodes and ownership (decided for the round; Paul confirms).** Today §5 says the page edits the
home's file and *a node's is edited at the node until the replica carries settings*, the gate runs
on each session host reading its own file, and §4.4a's offline table serves `set_settings` at a
node *link or no link* because the file is that host's own — so a reserve set on the home would not
reach a profile's sessions on a container node, which is where the page's first user would be
misled. The round makes **`settings.yml` home-owned**, one file for the org, and the four things
that follow are decided here, not left to the build: (1) **the replica is new work**, not
continuous with anything today — `write_node_config` copies `profiles.yml` into a container's
bind-mounted home only at `ao host up` and a promote, a local write that never crosses the link,
and the link (§4.4a *Frames*) carries records, not files; the round adds a **`settings` frame**
the home sends a node on `hello` (every dial) and after every `set_settings` write, from which the
node rewrites its own `settings.yml`; (2) **`set_settings` becomes a home-owned edit**: served at
the home, forwarded from a node while the link is up, refused at an offline node in the words
`set_controllers` uses — the offline table's row changes from *served* to *forwarded / refused* —
while **the node's gate keeps reading its replica offline**, so the last reserves it was sent stay
in force and a node still acts only on its own files (§9); a reserve moved while a node is offline
reaches it on the next dial, and the page says *nodes offline: <n> — takes effect when it dials*;
(3) **the page never reads the file from disk**: it reads through the agent (`gate` today; the
round adds a `settings` read RPC beside `set_settings`) and writes through `set_settings`, so a UI
started on a node — §4.4a allows one — shows the home's settings through the link and says *set at
<home>* when the link is down, exactly as `ao team` does on a node; the §3 sentence that the UI
reads the file *as it reads `hosts.yml`* is withdrawn; (4) **the person's own follows the home**:
folding `open_in` and the terminal font into the home-owned file retires §5's *read on the machine
the UI runs on* — one person, one org, one place, and a UI on a node shows them through the link.
This is a scope change named as such, and it is the reason Open decision 1 is a decision.
**Until the replica lands**, a profile with a live session on a node (a record lookup by host and
profile — every profile is copied to every node at provision, so nothing static says where a
profile runs) carries the node's name and *set at <node>* on its row, display only. Edits
apply on the next tick and the page says so; a reserve's new line is shown as the chip will show
it before the press lands.

## Open decisions

1. The `ui.yml` fold into `settings.yml`, and with it **the person's own following the home** rather
   than the machine the UI runs on (§4) — recommended yes.
2. `settings.yml` home-owned, replicated to nodes by a `settings` frame; `set_settings` forwarded from
   a node and refused offline, the node's gate reading its replica (§4) — recommended yes.
3. A top-bar tab — recommended, once built.

## Review rounds

**Round 1 (Sonnet, 2026-09-25): NOT READY — 1 BLOCK, 3 FIX, 4 NOTE, all adopted.** BLOCK: the outline said a node's settings are the home's; §5 says the opposite, and the gate reads each host's own file, so a reserve set on the home would not reach a node's sessions — the round now designs the replica and the page marks node-run profiles until it lands. FIX: the default profile was listed as editable with no writer (dropped); `local.person` is parsed, not absent from the code; `ui.yml` is also outside `BACKUP_MEMBERS`. NOTE: the fold's safety argued rather than asserted; Schedules drawn disabled until TD-133; the terminal face from monospace faces with ligatures off (goal 12); `AGENTORC_TICK`'s framing. Verified correct by the round: the six-scope table, `set_settings`'s person-only rule and next-tick effect, `promote:` refused today (executed), the org `roles:` overlay unvalidated, no `/settings` route, the localStorage keys, the TOML facts, the TD quotes, screen 7 free since TD-123.

**Round 2 (Sonnet, 2026-09-25): NOT READY — 3 BLOCK, 2 FIX, 2 NOTE, all adopted; the blocks forced decisions.** BLOCK: the replica was described as continuous with `profiles.yml`'s provision-time copy, which is a local bind-mount write at `ao host up`, never over the link — the replica is now named as new work, a `settings` frame on `hello` and after each write; once the file is home-owned, `set_settings` at a node can no longer be *served link or no link* — decided: forwarded while linked, refused offline, the node's gate reading its replica; the UI is not guaranteed to run on the home (§5 allows a laptop, §4.4a a node) and the chip already reads through the `gate` RPC, so the page reads and writes through RPCs, never the file. FIX: folding `person:` into a replicated file retires *read on the machine the UI runs on* — named as a scope change and made Open decision 1; the You section split by write path with **Reset this browser** a new control. NOTE: the font is a typed or picked family name with CSS fallback, no enumeration (`AO.termFont` already refits without a reload); display-only marks named; *runs on a node* is a record lookup by host and profile; Repos one card per checkout; **Open file** a new use of `open_in`. Verified correct: `set_settings`'s guard covers the whole RPC so no session can write `person:`; a `person:` stanza is inert to the gate; `save()` loses nothing on a machine-owned file; goal 12 permits a chosen face; TD-082's layout fits the outline.
