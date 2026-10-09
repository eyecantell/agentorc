## 5. Configuration

- Hosts: `~/.agentorc/hosts.yml` on every host — the UI host's copy lists the hosts; each host
  agent's copy carries its own `local` entry and, on a node, `home:` (§4.4a). The `local` entry's
  fields: `name`, `vscode_host`, `local: true|false` (`vscode://file` links, for a UI on the machine
  you sit at), `volatile: true|false`, `repos_registry` path (unset: dev-cadence's `~/.config/dev-cadence/repos.txt` on the default home `~/.agentorc`, `repos.txt` under any other home — TD-298), `runs_keep_days`, `identity` (§4.8a)
  and `person` (§4.4a); the top-level keys are `home:`, `nodes:` and `link:` (§4.4a). There are
  no per-host `transport:` or `ssh` target entries: the hub-and-spoke ssh transport they were for
  was replaced by the home and node split (§4.4a, §7 phase 2), where a node dials the home with its
  `link:` and the home lists it under `nodes:` with its flags (`volatile` among them, which sorts
  that host's *unreachable* card with *idle*, §4.5; TD-004). The UI process may run on a
  laptop; only the session hosts need to stay awake. The parser is `sessionorc.hosts`, shared by the UI and the host agent (TD-004; there are
  no env-var overrides). A field the *agent* acts on (`runs_keep_days`) is read on the session host
  from its own file's `local` entry, so every session host carries its own copy. `local.name`, `home:` and `local.identity`
  are read by the host agent **once, at its start**, and by the UI on every request, so a hand edit
  of one leaves the two disagreeing until the agent restarts: the Org page then says so in one line
  under the node banner — *hosts.yml changed: name kmaster → box — restart pending* — naming each
  value that moved, the agent's (from its `host` and `identity` answers) against the file's (TD-149).
  A node's `link:`
  key is the node→home link of §4.4a (built for a container node; a machine node is not yet in use, TD-057). **`home:`** names the host whose agent holds the org's graph
  and mail; an agent whose file names no `home:`, or names itself, is the home. On Paul's machines
  it is `home: kmaster`.
- **The settings a person moves** (TD-100; one file since 2026-09-25 — the ADR [settings
  audit](../decisions/2026-09-25-settings-audit.md); the file's four keys, their readers and `set_settings` / `settings` are built — TD-146 slice 1;
  and the UI reads `person:` through that read with `ui.yml` retired — TD-146 slice 1;
  the team stop time — slice 2; the CLI is TD-146's rest; the replica built — TD-147; TD-148
  the page, built 2026-09-27): **`settings.yml`**, beside `hosts.yml` in the agentorc home **of the home** — home-
  owned, one file for the org — read by the home's agent on every tick (`sessionorc.settings`),
  written **only by its `set_settings` RPC**, a person's own, refused to a session as `inbox_pause`
  is; `ao gate`, `ao schedule`, `ao team until` (§4.7) and the Settings page (§4.5 screen 8) all
  write through it. Nobody edits it by hand while the agent runs, though a hand edit is read on the
  next tick; it carries no comments, and a write rewrites it whole. **The line it draws**: a
  *definition* — what a team, a repo, a host or a profile *is* — stays in its own file above and
  below this bullet; a *setting* is a value the person turns without redefining anything, and every
  such value lives here, under six keys (`usage:`, the fifth, since TD-233 slice 4; `notify:`, the sixth, designed with TD-092 and read since TD-319 slice 1):

```yaml
usage_gate:                                   # §6 *Usage gate* — per profile, per window label as the adapter names it
  grind: {"5h": 30, week: {per_day: 10}}      # line 70% on the session window; 100 − 10 × days left on the weekly
  grind-api: {day: "$5", week: "$20"}        # a metered profile's reserve is an amount (§4.2a): the window's 100; "2M tok" without prices
usage: {max_age: 1h}                          # §6 *A reading the gate can no longer trust* (TD-230; read by the gate, set by set_settings, ao gate --max-age and the Settings page's field — TD-233 slice 4): past it the gate projects; `off` never. A key of its own: every key under usage_gate is a profile's name
teams:                                        # per team, by the name org.yml or a repo's teams: defines
  ao-grind:
    schedule: {start: reset, profile: grind, window: week}   # §6 *Schedule* (TD-133)
    until: "2026-09-26T06:00:00-06:00"        # §6 *Team stop time*: every member and seat stops here
    reserve: 10                               # §6 *Usage gate*: added to grind's reserve for this team's sessions
    on_work: ask                              # §6 rule 8 (TD-214): ask | start | off, when a wound-down team's lanes gain work
    flow: build-review                        # §4.9c (TD-307; read since TD-309 slice 4): which of the team's flows: it runs now; absent, the first
    balance: {prs: 10, oldest: 2d, review: true}   # §6 *Balance* (TD-177; built — TD-239): over any of these the team's members take no new claim; absent, no rule
repos:                                        # per registered checkout, by its directory name
  agentorc: {promote: {auto: false}, pull: true}   # §6 *Promote*: the one switch a person flips (run and check stay in .agentorc.yml); §6 *Pull*: the checkout follows origin, on when absent (TD-222)
person:                                       # the person's own — nothing here reaches a policy
  open_in: vscode                             # the editor button, below
  terminal: {size: 13, face: "JetBrains Mono",   # goal 12: ligatures off regardless, monospace always the fallback
             copy_on_select: true}             # a selection in the Focus pane copies itself (§4.5a, TD-164; default on)
  inbox: {board_show: "next:10"}              # which board items the Inbox lists before they are due: next:<n> per team | due | <n>d | all (TD-207; built — TD-220: drawn by the Inbox and the Repo page, picked on the Settings page's You)
notify:                                       # §4.10 *Told on Telegram when nobody is looking* (TD-092; TD-319 slice 1): read by the home's tick
  telegram: {on: false,                       # the one switch; absent or false, nothing is sent
             secrets: "samscrape/prd",        # the Doppler project/config holding TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID — a name, never a value
             link: "http://kmaster:8765"}     # how the person's phone reaches the UI (§4.5b); the row's address is appended
```

  A metered profile's reserve under `usage_gate:` is an amount per window (§6 *Usage gate*; TD-128) — `grind-api: {day: "$5", week: "$20"}` or `{day: "2M tok"}` — read against the account's spend (§4.2a), where a subscription profile's is a percent; the unit says which, and one that does not fit the profile's billing is refused, naming it (the gate reads amounts since TD-151 slice 3; `set_settings`, `ao gate` and the Settings page take them since slice 5). A profile absent under `usage_gate:` has no line on any window; a team absent under `teams:` has
  no schedule, no stop time and no priority, and asks when work appears (`on_work: ask`); a repo absent under `repos:` promotes by hand and is pulled (§6 *Pull*: `pull: false` is the opt-out).
  **Nodes** (§4.4a *Settings, replicated*): the home sends the whole file to every node whose link
  is up after each write, and to a node on its `hello`; the node writes its replica and its gate
  reads that, offline included — *policies that stop run on the node, from its replica* — so the
  last settings a node was sent stay in force until its next dial; `set_settings` at a node is
  forwarded while the link is up and refused offline in the words the home-owned edits use.
  **`ui.yml` is retired**: its one key, `open_in:`, lives under `person:`; a `ui.yml` still on disk
  is not read: the agent's `settings` read names it (`migrate`), and the Org's teams line — the
  Settings page too, once built — says *migrate: ui.yml is no longer read* (TD-146). The UI reads
  `person:` through that read, at most every five seconds, before it serves a request, and keeps the
  last answer when a read fails. What `person.open_in` takes is what `ui.yml` took, the editor button of the card, the
  Focus header, *edit yml* and the Settings page's **Open file**:
  - **`vscode`** — the default, and what a missing file means:
    `vscode://vscode-remote/ssh-remote+{remote}{path}?windowId=_blank` and, where the UI runs on
    the machine the person sits at, `vscode://file{path}?windowId=_blank`.
  - **`cursor`** — **no preset**. A preset's form must be confirmed against the editor's own
    documentation before it ships; Cursor's (`cursor.com/docs/reference/deeplinks`) documents
    only its `cursor://anysphere.cursor-deeplink/…` prompt, command and rule links, not a form
    that opens a folder, and community write-ups are not the editor's documentation. `open_in:
    cursor` is refused and named like any bad value; a Cursor user writes the form as a template,
    `{label: Cursor, url: "cursor://vscode-remote/ssh-remote+{remote}{path}?windowId=_blank"}`,
    which is theirs to trust (TD-095).
  - **`{label: "…", url: "…"}`** — a template of the person's own, which is how any other editor
    is reached (Zed, a JetBrains Gateway link: their remote forms are not the same shape, so they
    are not presets).
  - **`none`** — removes the button everywhere.

  A template takes `{path}` (percent-encoded, TD-011) and `{remote}`, the host's `vscode_host`
  from `hosts.yml` — an ssh alias in the person's own `~/.ssh/config`, whatever editor reads it;
  with no `{remote}` in it, a template is used as it stands on every host. **A template must be
  `scheme://…`, and `javascript`, `data`, `vbscript` and `file` are refused as schemes** — the
  scheme being the part before `://`, parsed, never a substring (`vscode://file…` is scheme
  `vscode`). One that does not parse, or is refused, is named on the page when it is served and
  the default button is drawn: a pasted bad line must not become a link that runs. The label is
  text the person wrote, escaped. Nothing under `person:` reaches a policy: the gate and the tick
  read the file by key and never this one.
- Repos: the dev-cadence registry (`~/.config/dev-cadence/repos.txt`) on each host — not
  duplicated; a home other than `~/.agentorc` reads its own `repos.txt` unless `repos_registry` names one (TD-298). A repo without dev-cadence can still be listed there. Directories that are not
  repos are not registered anywhere: New session takes a path, and the host agent remembers recent
  ones per host in `~/.agentorc/recent_dirs`.
- Per repo: `.agentorc.yml` (checked in):

```yaml
unattended:                           # read by nothing yet (below)
  workers: 3
  brief: ~/.tdgrind/{name}-prompt.md
  window: {weekday: "20:00-06:00", weekend: all}
  # no usage gate here: its reserves are the person's, per profile, in settings.yml (TD-100)
  wrapup_minutes: 15
  creds_min_hours: 0.25
roles:                                # §4.8 presets; every key optional, built-ins apply otherwise
  grinder: {brief: docs/briefs/grinder.md, lane: free-pick, profile: grind,   # profile: §4.9
            review: {reader: techlead}}   # §4.9b *The reader*: its PRs wait for the techlead; `held:` defaults to every PR
  hunter: {brief: docs/briefs/hunter.md, icon: search}   # icon: §4.8, one name from the fixed set
  manager: {brief: docs/briefs/manager.md, grants: [control]}
  plain: {prompts: [{label: review PR, text: "Review the PR I name next as the cadence says, then report."},
                    {label: sweep, text: /stranded-work},
                    {label: waiting on me, text: "What is waiting on me across this repo's board and my inbox?"}]}   # prompts: §4.8, the chips beside Send (TD-161)
controllers: [manager-ao-1]           # §4.8: who may act on a session started here (a preset may
                                      # override it with its own `controllers:`); omitted = nobody
ledger: docs/technical_debt.md        # what a TD-NNN reference resolves to
held: ["src/sessionorc/**"]           # §4.9c (TD-307; read since TD-309 slice 1): the paths a review stage holds (the `build` flow holds nothing)
                                      # this repo's own flows and roles are directories in .agentorc/, not keys (§4.9c)
teams:                                # §4.9: the repo's own teams, aggregated into the org (TD-210); org.yml wins a name
  grind: {flows: [build], manager: {role: manager, name: manager}, members: [{role: grinder, count: 2, name: grinder}]}
ready_when: [tree_clean, branch_pushed, pr_merged, no_subagents, ledger_touched]   # read by nothing yet
commands:                             # read by nothing yet
  - name: test        ; run: pdm run test
  - name: cluster     ; run: ./scripts/cluster-status.sh
  - name: attention   ; run: python scripts/nudge_user_attention.py --report
promote:                              # §6 *Promote* (TD-120): how a merge to `main` becomes the live copy
  run: scripts/promote.sh             # makes the tree it is started in live; run at the home
  check: scripts/live_sha.sh          # prints the commit that is live now, or fails saying why
```

  **Read by nothing yet** (TD-149): `unattended:` (once the New session switch's precondition; the Role pick that replaced it does not check it, §4.5
  screen 3; the window and wrap-up are `settings.yml`'s and the tick's today), `ready_when:` (§4.2's
  checks, which are fixed today and not chosen per repo) and `commands:` (the Commands page, §4.5
  screen 5, phase 4) are accepted and checked, so a file written ahead of its feature does not break
  `ao new`, and nothing acts on them until that feature is built. **Not keys**: `adapter:`,
  `worktrees:` and `anchor:` are refused, the error naming where each is decided — the adapter per
  session and per profile, a session's worktree always `<repo>/.claude/worktrees/<name>` (the host
  agent never reads this file, so it could not move it), and one agent session per directory is §9
  invariant 2, not a per-repo choice.

  **`promote:`** (TD-120 step 2, designed 2026-09-24; read by the home since TD-132 slice 1, run
  under `auto` or at a person's press; this repo carries one since TD-132 slice 4; the block is accepted and
  checked already — `run` and `check`, both required, `auto` refused as `settings.yml`'s — so
  writing it does not break `ao new`, TD-149) is the repo's own
  answer to *how does `main` become what is running*, and nothing in agentorc names pip, a venv,
  systemd, skaffold or wrangler: this repo's `run` is the pip pair of CLAUDE.md and its `check`
  prints the installed build's commit (`sessionorc.build.info()`); samscrape's would be its
  skaffold run and the deployed image's tag; contractmatch's a wrangler deploy and the deployment's
  commit; dev-cadence's its sync. Both are commands run **at the home** (§6
  *Promote* says when), `check` in the checkout and `run` in the tree it is to make live: the
  checkout, on main's head, for a promote, and a detached worktree at the commit for a rollback
  (§6 *A rollback*, TD-212). So **`run` makes live the tree it is started in**, names no path to
  the checkout, and finds the checkout in `AGENTORC_PROMOTE_ROOT` when it needs what no commit
  holds. It is judged by what `check` says
  afterwards, never by its exit code; `check` prints one full commit on stdout and exits 0, or
  exits non-zero with the reason on stderr (*not deployed*, *no cluster*), which the row shows as
  *live: unknown — <reason>*. `auto` lives in `settings.yml` (`repos.<repo>.promote.auto`, the Settings page's switch; 2026-09-25) and is `false` when absent: the row offers a press. A repo with no
  block has no promote, no row and no `ao promote`. It is **the one key of this file the host agent
  reads** (`sessionorc.promote`, the key alone, from each checkout in the home's registry — the
  rest of the file is the clients' and the agent still resolves no role and no brief), because a
  policy that acts needs its declaration where the policy runs; `ledger:` travels on the record
  instead because it is a session's.

- Org (§4.9): the teams each registered repo defines in its own `.agentorc.yml`, aggregated with
  the org file, which holds what no one repo can: projects and teams that span repos, `place:`
  and the install's `roles:` overlay; `org.yml`, `profiles.yml` and `settings.yml` are tracked
  by a git work tree in `~/.agentorc/` at the home (§4.9 *The org is an aggregate*, *What is
  left at the home has a history*; TD-210, built — TD-229 slice 5).
  The org file: `~/.agentorc/org.yml` on the UI host — projects, teams, and an org-wide `roles:`
  roster that sits between the package's built-ins and a repo's own, each preset checked key by key
  exactly as a repo's `roles:` is (TD-149). Read by the clients on every use, never by the host agent:

```yaml
projects:
  agentorc:  {repos: {agentorc: {kmaster: ~/agentorc}}}
  guardians: {repos: {guardians: {devenv: /workspaces/guardians}, guardians-api: {devenv: /workspaces/guardians/api}}}
teams:
  ao-grind:
    projects: [agentorc]
    manager: {role: manager, name: manager-ao-1}
    members:
      - {role: grinder, count: 2, name: grinder-ao, lane: free-pick}
roles:
  grinder: {profile: grind}
```

A repo without the file gets defaults: `adapter: claude-code`, worktrees under
`.claude/worktrees`, `ready_when: [tree_clean, branch_pushed, no_subagents]`, no commands, no
unattended mode, the three built-in presets with the package's brief templates, and
`docs/technical_debt.md` as the ledger. The `unattended:` block is where every time-shaped
setting lives (window, stop times — TD-026 extends it); the usage gate's reserves are the
person's and live in `settings.yml` (TD-100). A `roles:` preset never carries a schedule, and a
grant never carries one either. A directory session (no repo) reduces to `ready_when:
[no_subagents]`.

- **Test knobs, not settings**: `AGENTORC_TICK` — the host agent's tick in seconds (default 2),
  read once at its start — exists for the test suite, which runs agents on a faster clock; nothing a
  person sets, and no page shows it (TD-149).

