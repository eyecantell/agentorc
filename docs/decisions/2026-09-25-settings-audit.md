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
| Environment | `AGENTORC_HOME`; `AGENTORC_TICK` | the home; the tick period (an override §5 says does not exist) | the shell or the unit | start | no |
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
**person's own**: `open_in`, and the **terminal's font family and size** (new: a person who reads
terminals all day wants it; one xterm option and a refit). (e) The **default profile**.

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
(today `settings.yml` is the only file the agent writes and the only config *not* in
`BACKUP_MEMBERS`), and the agent ignores `person:`, which keeps §5's rule that nothing of the
person's reaches a host agent. §5's reason for two files — a second person, or a UI off the home —
is the trigger §5 already names for moving the scope when it comes. The UI runs on the home and
reads the file as it reads `hosts.yml`. The theme stays per browser: a phone and a desk differ.

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
three places; `hosts.yml` `transport`/`ssh`/`person` in the design and not the code;
`AGENTORC_PROFILE` exported and read by nothing.

## 4. The page, in outline (for the design round)

Screen 7, **Settings**, `/settings`, a top-bar tab once built (TD-123's rule). One centred column
as the Inbox (TD-082), sections in this order, each with its *i* mark: **Usage** (the reserves
matrix by account → profile → window, each cell a reserve, flat or per day, with the line it
produces; budgets per metered profile when TD-128 lands), **Schedules** (per team; TD-133),
**You** (`open_in`, terminal font and size, and this browser's theme, *mine* and folds with a
*reset this browser*), **Hosts**, **Profiles**, **Org**, **Repos** (read-only, the file, the
re-read rule, the editor button). Writes go through `set_settings`; nothing else on the page
writes a file. A node's settings are the home's (§5) until the replica carries settings. Edits
apply on the next tick and the page says so; a reserve's new line is shown as the chip will show
it before the press lands.

## Open decisions

1. The `ui.yml` fold into `settings.yml` (§3) — recommended yes.
2. The page edits the home's settings only — recommended yes, as §5 says.
3. A top-bar tab — recommended, once built.

## Review rounds

_(filled by the Sonnet rounds below)_
