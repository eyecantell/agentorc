# ADR 2026-09-13: Org, Team, Project, Role — how a person organises agents

Status: decided 2026-09-13 (Paul, with the anchor session), vocabulary and relationships only.
The design changes it implies are ledgered as TD-040 and sequenced after TD-036; nothing here
changes behaviour until they are written into design §4.5, §4.8 and §5.

## Context

agentorc grew up as a fleet view: a grid of live sessions, each started by hand from a
directory and a role preset. That is the right substrate and the wrong vocabulary for what
the person is doing. On 2026-09-12 Paul asked how *a person* works — teams, roles, leads — and
on 2026-09-13, after the Herd page was renamed Team (PR #105), asked for the next step: define
agents by role, offer them as a pick-list, add agents by skillset to a team, give a team access
to repos, and ask whether the top-level noun should be **Org**, holding several teams. The
same day he asked whether a *project* is one repo or several, wanting the latter, because
guardians is five repos under one umbrella.

Two facts shaped the answers. First, the membership design (TD-036, go given 2026-09-13) already
gives every session a `controllers` list, so "which sessions belong to which lead" is about to
exist as data, and a team can be defined on top of it rather than beside it. Second, the
product direction (§4.5c) is a neutral view over tools on machines the person owns; the
vocabulary should be the workplace's, but the product does not claim to *be* an autonomous
company — that framing is exactly where the crowded field sits (see the name check below).

## Decision

Five nouns, one relationship each. Names are the words the UI and the docs use; the record on
disk keeps `session`, `controllers`, `role` and `profile` as they are.

- **Org** is the whole: everything this person runs through agentorc, across hosts. There is
  one org per install and the person is at the top of it. The org holds the projects, the
  teams, the roster of roles, and the attention board. *Org* is the name of the home page and
  the top menu item, whatever is inside it — one agent on one repo is an org, and so are ten
  teams. The noun does not change with the count.
- **Team** is a lead plus members. The lead is an orchestrator session, or the person for a
  team that has no orchestrator. A session is on a team when its `controllers` name the team's
  lead (TD-036); that edge *is* the membership, there is no second list. A team may contain
  teams: a lead whose members are themselves leads. A team is assigned one or more projects.
- **Project** is a named set of one or more repos that belong together, with each repo's
  checkout location per host. samscrape is a one-repo project; guardians is a five-repo
  project (the umbrella and four siblings cloned under it); agentorc and dev-cadence may be
  one project or two, the person decides. A project may have more than one team on it (a ui
  team and a backend team), and a team may span projects. Repos stay where they are registered
  today (the dev-cadence registry, `.agentorc.yml` per repo); a project is a grouping over
  them, not a new place to store them.
- **Role** is a skillset preset: the brief template, lane shape and grants §4.8 already
  defines, plus the **profile** (tool, account, model) the role runs under and any skill files
  it installs. Roles are the pick-list when adding a member. The package ships `grinder`,
  `hunter`, `orchestrator` and `plain`; a project or the org may add or redefine roles. Nothing
  at runtime keys on a role (§9 invariant 9) — it is how a member is *started*, not what it is
  allowed to do.
- **Agent** is what the UI calls an interactive session started from a role. An agent lives in
  exactly one checkout, its **home**, and a project may give it reach into other repos without
  moving it (Paul, 2026-09-13; TD-040 item 7). The record's word
  stays `session`, because shells and command runs are sessions and not agents.

A team is defined once and started many times: the definition names the lead's role, the
members as (role, count) pairs, and the projects; starting it launches the lead first and each
member with `controllers: [lead]`, in the repo checkouts the projects name. The live grid the
Team page shows today becomes the Org page: flat when nothing is grouped, grouped by team when
teams exist, each team card carrying its lead, its members' states and its needs-you count.

**"Access to repos" means, in phase 1, which checkouts a team's members are started in and
what the lead's brief covers.** It is not credential scoping: every session runs as the person
on the host and can reach whatever the person can. Scoping credentials per team is a later
phase and is named here so the word *access* in the UI does not promise it.

## Consequences

- The Herd → Team rename of 2026-09-13 becomes Team → Org at the home page once teams are
  definable; until then the page keeps its name. The membership session is mid-way through
  TD-036's steps and is not disturbed by this decision.
- TD-040 holds the design change: where team and project definitions live (an org-level file
  on the host, `.agentorc.yml`, or both), the start-a-team command, the role's `profile` key,
  the Org page's grouped view, and the New session picker's project-aware repo list.
- TD-039's conflict design gains a natural home: two leads on one member are two teams sharing
  a session, and escalation goes to the person at the top of the org.
- Guardians is the first multi-repo project. Its sessions run inside a VS Code devcontainer
  today, while agentorc launches tmux on the host; the project's per-host checkout location is
  where that gets answered, and it stays an open question until it is.

## The name

Paul noted a product at https://agentorc.com on 2026-09-13. Checked that day: it is a
pre-launch waitlist for a business multi-agent workflow product ("turn simple instructions
into complete multi-agent workflows"), not a shipped tool, and not in this space. `agentorg`
was checked as the alternative:

| Name | PyPI | GitHub repos matching the name | Domains |
|---|---|---|---|
| agentorc | free | 126 | agentorc.com live (the waitlist above) |
| agentorg | free | 19, including AgentOrgs (AgentX-lab, a Kubernetes control plane for AI agents) and several "autonomous company" frameworks | agentorg.ai live; agentorg.com registered, serves nothing |

Neither name is clean, and the *org* crowd is the "AI company" framing §4.5c declines. Decided:
keep `agentorc` as the repo and package name for now — it has one host and no users, so a
rename stays cheap — and choose the product name when there is a product to name, before the
relay transport ships (§4.5c). Logged in §10.
