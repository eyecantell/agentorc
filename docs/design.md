# ShiftLead — design

*ShiftLead is the project's name (shiftlead.dev). This document says `agentorc` wherever it names the
packages, the state directory, the units, the config file or the command's home, because those are
still their names: the machine-side rename is TD-060's second step, at a release boundary. The command
is `ao`.*

Status: **past phase 1.** The host agent, the Claude Code adapter, the Org, Focus, Inbox and New
session pages, the CLI, teams, mail between sessions, a home/node split with one container node, and
identity on one host are built, each with the parts still open under its ledger entry. §7 lists what each phase still lacks;
[`technical_debt.md`](technical_debt.md) holds what is deferred;
[`design-history.md`](design-history.md) is the dated record of how the design got here. This
document is the requirements and architecture as they stand; each open question in §10 is a
decision that changes what gets built.

A change in behaviour is a change to the design first, and the dated fact goes to [`design-history.md`](design-history.md); a control that is not in §4.5a's table does not exist.

The design is the files under [`design/`](design/), one per section and one per subsection of §4;
grep the directory, read a section by its file.

- **§1** [Problem](design/1-problem.md) — the person running many agent sessions across hosts, and what that costs them today.
- **§2** [Goals](design/2-goals.md) — what agentorc must do, and what it does not try to.
- **§3** [Prior art](design/3-prior-art.md) — the tools surveyed, what was taken from each, and why none is the substrate.
- **§4** [Architecture](design/4-architecture.md) — the picture: browser, UI, host agents, tmux, and the home that joins them.
- **§4.1** [Session substrate: tmux, one session per conversation](design/4.1-session-substrate.md) — one tmux session per conversation; names, the anchor rule, worktrees, plain shells.
- **§4.2** [State feed: hooks first, scraping as a labelled fallback](design/4.2-state-feed.md) — the states, fed by the tool's hooks first and by screen rules, labelled, where none fires.
- **§4.2a** [Profiles: tool · account · model](design/4.2a-profiles.md) — a profile is a tool, an account and a model; how a session is launched on one.
- **§4.3** [Adapter contract](design/4.3-adapter-contract.md) — what a tool adapter supplies: argv, hooks, pane classification, the composer.
- **§4.4** [Host agent](design/4.4-host-agent.md) — the per-host process that owns tmux, the record, the tick and the RPC.
- **§4.4a** [Home and nodes: one session graph across hosts](design/4.4a-home-and-nodes.md) — one session graph across hosts: the home, its nodes, and how a container node is made.
- **§4.5** [UI](design/4.5-ui.md) — the pages — Org, Focus, Inbox, New session, Repo, Settings, Help — and what each shows.
- **§4.5a** [Controls](design/4.5a-controls.md) — every control, what it does and who executes it; a control not in this table does not exist.
- **§4.5b** [Reachability, and the shape of a hosted service](design/4.5b-reachability.md) — how someone who never opened a port reaches the page, and a hosted service's shape.
- **§4.5c** [Product direction](design/4.5c-product-direction.md) — the two products this architecture serves, and who owns the host in each.
- **§4.6** [Transport and terminal mechanics](design/4.6-transport.md) — the links between UI, host agents and panes; reconnects, backoff, `unreachable`.
- **§4.7** [CLI](design/4.7-cli.md) — the `ao` command: its verbs, its JSON, what each one mutates.
- **§4.8** [Capabilities, report channels, and role presets](design/4.8-capabilities-roles.md) — what a session may do to agentorc: grants, controllers, report channels, role presets, briefs.
- **§4.8a** [Who is calling: identity on one host (TD-077)](design/4.8a-identity.md) — how the host agent knows which session is calling, and what its gates are and are not.
- **§4.9** [Org, Team, Project: the definitions above a session](design/4.9-org-team-project.md) — the org, its teams and projects; how a team is defined, started and stopped.
- **§4.9a** [Winding down: a team that runs out of work](design/4.9a-winding-down.md) — how a team that runs out of work winds down, and a run's three words.
- **§4.9b** [The techlead: a go-between for what would reach the person (TD-075)](design/4.9b-techlead.md) — the techlead seat: what it reads, what it answers, and what still reaches the person.
- **§4.9c** [Flows: the path an entry takes through a team](design/4.9c-flows.md) — the path an entry takes through a team: flows, stages, `held:` and the review.
- **§4.10** [Messages between sessions](design/4.10-messages.md) — mail between sessions and to the person: kinds, bounds, outcomes, threads, looks.
- **§5** [Configuration](design/5-configuration.md) — the settings file, the repo's `.agentorc.yml`, and where each setting lives.
- **§6** [Policies (the tdgrind supervisor, generalized)](design/6-policies.md) — the tick's policies: usage gates, run windows, restarts, promote, balance.
- **§7** [Phases](design/7-phases.md) — the phase plan, re-baselined against what runs, and what each phase still lacks.
- **§8** [Lessons carried in (dev-cadence + tdgrind)](design/8-lessons.md) — lessons carried in from dev-cadence and tdgrind.
- **§9** [Invariants](design/9-invariants.md) — the rules that hold whatever else changes.
- **§10** [Open questions](design/10-open-questions.md) — the dated log of questions, each a decision that changes what gets built.
- **§11** [References](design/11-references.md) — files, repos and documents the design cites.
