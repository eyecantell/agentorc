## 10. Open questions

A dated log. Each entry: the question, the decision, and where the reasoning lives.

- [x] Reachability (2026-09-06): **never a bare public port** — a private network (WireGuard,
      Tailscale) or an authenticated tunnel (Cloudflare Tunnel + Access); no VPS needed. For
      non-technical users and a hosted service, the agent-initiated **relay** transport (§4.5b),
      kept compatible now and scheduled later.
- [x] Where the UI process runs (2026-09-05): **wherever `agentorc[ui]` is installed**; nothing
      in the design assumes a particular host. `hosts.yml` lives on the UI host, which may or
      may not also be a session host; for Paul it is kmaster. A hosted service is the `relay`
      transport (§4.5b), after phase 5.
- [x] Password vs Tailscale-only for the UI (2026-09-04): Tailscale only; superseded 2026-09-06
      by the reachability decision above.
- [x] Team view: table vs card grid (2026-09-04): card grid, with Urgent first / pinned sort
      modes (§4.5a).
- [x] Pinned layout (2026-09-05): **per browser, `localStorage`**, keyed by session id; the same
      store as the sort mode and the dark-mode toggle. Moves to the UI host only if a second
      person or browser makes it hurt.
- [x] "Done when" (2026-09-04): renamed **Ready to close** — Focus side panel with a Close
      button; a card shows "ready to close ✓" or the failing items; `closed` is reached only by
      the person's Close (§4.5a).
- [x] Name (2026-09-04): `sessionherd` → **`agentorc`**, beside cmdorc; CLI alias `ao`.
- [x] ttyd vs a Python pty bridge (2026-09-05): **pty bridge in the UI process** — a pty around
      `ssh -tt host tmux attach -t <name>`, bridged to xterm.js with `asyncio` + `os.openpty`;
      resize is `TIOCSWINSZ` on the local pty. The host agent stays the only per-host process.
      ttyd remains the fallback if the bridge proves flaky on slow links (§4.5b).
- [x] `.agentorc.yml` vs. a section in dev-cadence's per-repo config (2026-09-04): its own
      file; dev-cadence stays the cadence system, agentorc reads its registry and, via the host
      agent only, edits and commits board items (§4.7).
- [x] Repo layout (2026-09-04): **one repo, two packages**, `sessionorc` below the adapter
      contract and `agentorc` above it; `sessionorc` imports nothing from `agentorc`; split into
      two repos only when a second consumer (cmdorc) appears. Packaging (2026-09-05): **one
      distribution, `agentorc`, with a `[ui]` extra**; base deps `pyyaml`, `typer`; one version
      crosses the RPC boundary; `pipx install agentorc` on a new host; `requires-python >= 3.12`
      (§4.3, §5).
- [x] Attention sort vs Attention tab (2026-09-04): sort renamed Urgent first; overdue/due-today
      board items on a Due strip with Snooze/Done; the tab keeps the full board and loses its
      sessions column (§4.5a).
- [x] Board write-back (2026-09-04): both Snooze and Done, by the host agent, committed with a
      fixed message naming the session (§4.7, invariant 7).
- [x] Repo-less sessions (2026-09-04): allowed; anchor on the directory, one agent session per
      directory, shells and command runs exempt (invariant 2).
- [x] Shell vs agent (2026-09-04): a shell is an adapter; ad-hoc shells are Team cards (Shell
      button, Open shell here); predefined command runs are `kind: command` on the Commands tab,
      hidden from the Team by default (§4.5a).
- [x] `unreachable` (2026-09-04): host-level chip + banner, greyed cards keep last state; sorts
      with idle on a volatile host, after stalled? otherwise (§4.5a).
- [x] Answer buttons under the Focus terminal (2026-09-04): removed — the terminal owns menus
      and questions; Allow/Deny go through the `PermissionRequest` hook decision; questions get
      Focus only. Composer + Attach stay (§4.5a, invariant 6).
- [x] Session identity (2026-09-04): name + adapter id from birth; hand-started sessions show
      the id until adopted (§4.1).
- [x] Existing-worktree picker (2026-09-04; superseded 2026-09-06): no picker — the **Where**
      control names the worktree and reuses one of that name; the only list shown is the
      directory-occupancy answer as the person types. §4.5a is the authority; a control not in
      that table does not exist.
- [x] **Deny with a reason?** (decided 2026-09-23: **yes**, built by TD-117 — §4.5a's card, Focus
      and Inbox rows) The hook decision carries a message Claude reads; a one-line optional
      "why" next to Deny steers the next attempt better than a bare refusal. Cost: one input box.
- [x] **"Allow for this session"?** (decided 2026-09-23: **no, not now**) The hook can update the session's permission rules so the
      same tool does not ask again. If added, it must be a third, smaller button and never the
      default — it is how a permission prompt stops being an alert.
- [x] **Build on, or beside, herdr?** (2026-09-09; decided 2026-09-10): **independent** —
      `sessionorc` stays on tmux, herdr is prior art (§3) with a screen-rule fallback and the
      worktree API shape to borrow later ([ADR](../decisions/2026-09-10-herdr-spike.md), TD-014).
- [x] **Worker types: roles the agent keys on, or capabilities?** (2026-09-10): **capabilities,
      with roles as presets** (§4.8) — two ungated report channels, one gated grant, presets that
      only fill the New session form. Schedule is orthogonal: time-shaped settings stay on the
      `unattended` side (§6, TD-026), no preset or grant exempts a session (invariant 9).
- [x] **Should a name identify one session?** (2026-09-10): **yes, per scope** (§4.1,
      invariant 12) — a live holder refuses a second (offer Switch to), an exited or closed
      holder is superseded, a suffix survives only for a tmux id the agent has no record of and
      is then shown. Cost: two workers cannot share a name across worktrees.
- [ ] **One orchestrator or many; how is membership expressed?** (raised 2026-09-12; decided
      2026-09-12, go 2026-09-13, TD-036): `controllers: [session ids]` on each session record; an
      acting RPC passes only with the grant *and* membership in the target's list; empty means
      nobody may act; several controllers allowed; create adds the creator with the child's
      grants ⊆ the creator's; `set_controllers` is gated on the target; defaults from
      `.agentorc.yml` (§4.8, §4.5a, invariant 11). Rejected: per repo — the boundary is the one
      the person set, not one inferred from a path. From prior art
      ([ADR](../decisions/2026-09-12-orchestrator-membership-prior-art.md)): a restart ceiling,
      `one_for_one` restart scope, an exited orchestrator neither kills nor loses its workers
      (re-attached by an explicit edit), "supervisors only supervise" in the orchestrator brief,
      the create rule as capability attenuation. Deliberate departures: N controllers per unit
      as a flat list; an empty list means *nobody may act*, not *nobody wants it*. The "3–5
      agents" coordination knee is struck (ADR §6). **Still open:** what a second controller
      does while the first is mid-prompt; where an orc-of-orcs' fan-out ceiling sits; whether a
      clean orchestrator exit and a crash propagate differently.
- [x] **What happens when two controllers of one session disagree?** (raised 2026-09-13;
      answered 2026-09-14 as §4.10): the conflict report is a `conflict` message to both
      controllers, the exchange is `reply` traffic under one `about`, the escalation is §4.10's
      exchange bound, and a message about a session is copied to its other controllers. Built as
      TD-052; TD-039 is the conflict-specific half.
- [x] **Should agent-to-agent communication be first class?** (2026-09-14): **yes, as §4.10.**
      An act of control and a message are different things — different delivery (a mailbox on
      the recipient's record, never a pane) and different authority (a weaker gate read off the
      controllers graph, no grant); a message may reach a person's interactive session where an
      act may not (invariant 5). §4.10 states two properties that hold at every point, mediation
      and attribution; `mail` joins the §4.3 adapter contract; a message may start a turn,
      metered by a per-session wake budget. Absorbs TD-039, TD-047's vocabulary and TD-049's
      wake. **Still open, deliberately:** every number in §4.10's bounds — mailbox depth,
      exchange bound, an `ask`'s default, the retention window — is a rule with no value yet,
      to be set from a running fleet.
- [x] **What are the nouns above a session — and is the home page Org?** (2026-09-13):
      **Org, Team, Project, Role, Agent** ([ADR](../decisions/2026-09-13-org-teams-projects.md)).
      Org is the whole and the home page; a team is a lead plus the sessions whose `controllers`
      name it; a project is one or more repos with a checkout location per host; a role is the
      §4.8 preset plus a profile; agent is the UI's word for an interactive session. "Access to
      repos" is which checkouts a team starts in, not credential scoping. Design in §4.9
      (org.yml, `ao team start|stop|status|list`, team groups, a role's `profile`, home and
      reach); code under TD-040.
- [ ] **Product name.** (raised 2026-09-13) agentorc.com is taken by a pre-launch product and
      `agentorg` collides with AgentOrgs (table in the
      [ADR](../decisions/2026-09-13-org-teams-projects.md)). Keep `agentorc` as repo and package
      until there is a product to name; decide before the relay transport ships (§4.5c).
- [x] **A session that is meant to run inside a devcontainer** (raised 2026-09-13; decided
      2026-09-13, confirmed 2026-09-17): **a second host** — a host is wherever an
      `agentorc-agent` runs beside a tmux server, a devcontainer that runs one is a host, and a
      project's repo entry names it per host. Rejected: (a) run on the host against the same
      checkout, leaving the container to the person; (c) a container-exec launch in the adapter,
      which breaks invariant 8 the moment `docker exec` picks up an rc file. The mechanism is
      §4.4a *A container node*: the checkout is mounted at the same absolute path inside; the
      home provisions the container, installs its own wheel onto the node's volume and
      supervises the agent from its tick; the link is a per-node socket at the home,
      bind-mounted in; occupancy across the home and its container is derived from the
      `container:` entry; a runtime host has `ao host forget`. Rejected: bind-mounting the home's
      whole `~/.agentorc` (its `agent.sock` makes an unqualified caller a person at the home).
      Build list: TD-057 step 3c. Brief: `docs/briefs/guardians-orchestrator.md`.
- [x] **What may a manager do with the repo's numbers?** (raised 2026-09-25, Paul: *if I want to
      make sure the team is balanced, I can make sure the PR count is not growing too much — the
      grinders outpacing the techlead; this may lead to automatic checks by the manager*). The
      numbers come first, visible on the team card and the rollup and readable by `ao repo` (§4.5 screens 1 and 11, §4.7),
      so a person watches them before any rule keys on them. The rule under consideration
      (TD-177, design-first): a per-team **balance** setting — open PRs above `n`, or the oldest
      past `d`, or the reader's queue past its `bound` (§4.9b) — on which the manager hands out
      no new claim and asks the techlead to read, saying so in its log line; never a kill, never a
      wind-down. Open: whether the line is a setting (Settings page, Teams card) or a definition
      (`org.yml`), which the settings audit's rule decides (ADR 2026-09-25 §5); and whether the
      manager reads `ao repo` each round or the tick pushes the breach as a `system` note.
      **Designed 2026-09-29** (§6 *Balance*; built — TD-239): a setting, `teams.<team>.balance`,
      off until set; read by the home's tick; and the rule acts where work is taken, at the
      claim, because a manager hands out nothing — its members choose for themselves (§4.8
      *Choosing in a free-pick lane*). The manager is told and logs it.
- [ ] Phone answers for *questions*: the narrow Focus with a soft-key row is the current
      answer; revisit after phase 2 if it is too fiddly one-handed.
- [x] **Rename the Herd page?** (2026-09-13): **yes, to Team** — "Herd" reads too close to
      herdr (§3, prior art, not renamed).
- [x] **Does a team wind down when it runs out of work?** (2026-09-14): it did not — every
      stopper in §6 is a clock or a cap. → **§4.9a**: the test for "no work" belongs to the role,
      quiet is not empty, exhaustion is **declared** on the record and never inferred by the core
      (invariant 14), one member's exhaustion is not the team's, and the lead's last act is a
      board line. Built under TD-053. The guards against a false stand-down were settled
      2026-10-08: the reason, and the home's own reading (§6 rules 6, 8 and 9); no early bound.
- [x] **How does an idle agent learn it has mail, and when is mail deleted?** (2026-09-16):
      §4.10's **doorbell** and **lifecycle**. The Claude Code adapter submits a fixed line
      carrying only an unread count into a hook-confirmed idle pane with an empty composer, and
      every `ao` reply carries it for a busy session; no sender-written text, no doorbell where
      the composer cannot be read. Lifecycle: unread → read (set only by `ao inbox` printing the
      entry) → pruned after a retention window; mail also goes with Forget, with a closed record
      after `CLOSED_KEEP`, or by a person's delete; resume carries mail forward. Review rounds:
      history.
- [x] **Is §4.10 right as a whole?** (2026-09-16): **sound with changes**, then **ready** — after
      four Fable reviews and three Sonnet rounds, §4.10 is agreed implementable from its text and
      TD-052 step 1 may start. The rules those rounds produced are in §4.10 (the wake budget's
      refill, the person inbox, copies to other controllers, tallies per thread root, `wait` as a
      host-agent RPC, `sends`, `bound_hit`, `copies_failed`, `mail_decided`, the Message control)
      and the rounds themselves in the history; prior art in
      [ADR 2026-09-16](../decisions/2026-09-16-agent-messaging-prior-art.md).
- [x] **How do sessions on different hosts talk?** (2026-09-16, TD-057): not routing but a
      **session graph that spans hosts** → **§4.4a**: one always-on **home** host agent (kmaster)
      holds the org's graph and mail; other host agents are **nodes** that keep tmux, the pty and
      hooks and dial home over ssh (`agentorc-agent link`); §4.10 runs in one place. Addresses
      are `id@host`, a bare id meaning the record's own host; mail to an unreachable host lands
      at home, an act onto one is refused; a host that cannot reach home lets a person act but
      refuses its sessions' mail and acts; the link is written as the relay's protocol. Rejected:
      the UI host as router; a host-agent mesh; the relay now. Invariant 15 added. The
      ownership, replica, backup, offline-fallback and terminal rules a review of §4.4a added
      are in §4.4a and invariant 15; the review in the history.

- [x] **Is the design over-complicated after three weeks of fast building?** (Paul, 2026-09-22:
      a full design review with Fable.) → The core stands (tmux, hooks over scraping, one writer,
      home/node with field ownership, `controllers` on the target, mail as an inbox). The excess is
      above it: this document itself, a manager session doing mechanical policy, the mail protocol's
      surface, and identity on one host. Adopted the same day: this document rewritten in the
      present tense with its dated record moved to [`design-history.md`](../design-history.md), and
      §7 re-baselined; the other findings ledgered for evaluation — TD-103 (mechanical rules move
      from the manager's brief to §6 policies; needs Paul's decision), TD-104 (seat triggers as a
      field the tick sets), TD-105 (mail surface), TD-106 (identity finished as built), TD-107
      (compatibility tables), TD-108 (code shape), TD-109 (docs housekeeping), and three features
      — TD-110 (night report), TD-111 (`ao doctor`), TD-112 (second-adapter spike); TD-092, TD-091
      and TD-008 endorsed.

- [x] **Who applies the mechanical rules that keep a team running?** (2026-09-22, the design
      review; **decided by Paul the same day: option 1**, the host agent's tick.) The manager's
      brief carried the crash restart and its ceiling, the wanted restart, the seat fills and the
      idle nudge, applied by a model every ten minutes; §6 *Keeping a team running* makes them
      policies keyed on `unattended` and a new `supervised` field, under the rule **a restart is
      not a start** — the host agent still starts nothing new by itself (TD-026), it re-creates
      what a person or `ao team start` chose to run, from a launch record. Rejected: the tick
      computing the facts while the manager still acts (a round per event, and a session that must
      exist); sends and closes on the tick with creates left to the manager (leaves the restarts,
      the cases that matter). Build is TD-103; TD-104 folds into it.

- [x] Flows (2026-10-04, TD-307): Paul asked for the TD path — designer if needed, grinder with a UI
      check if needed, techlead, the person if needed — defined once and reused, and for more paths;
      *per team*. **Decided**: a named flow of stages — a role, its lane and its stage brief each —
      that owns the words describing the path; a team lists the flows it may run and the person picks
      one as a setting, and a switch or an edit applies itself (relaunch under rule 7's conditions);
      no engine, no record flow field, the UI check and the route to the person unchanged and shown
      as every flow's last word (§4.9c). Opt-in; a flow usable only when whole; a team that cannot
      staff a listed flow is told so and refused, never skipped (Paul, on the first and second
      drafts). Roles and flows orthogonal, meeting only at a role's `kind`; each flow and each
      defined role a directory of whole files, so a page can create them later (Paul, on the third).
      Reviewed in Fable rounds.

