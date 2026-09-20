---
name: ao-team
description: How to stand up an agentorc team — the project, the definition, the roles, and `ao team start` — for a person at a terminal and for an agent told to do it.
---

# Stand up a team

One recipe, for a person at a terminal and for a Claude session told *stand up a grind team for
repo X*. It is the same text either way: `ao team --skill` prints this file, so a session serves
itself from the tool rather than from a memory of the design.

Every command below is one you can run. Nothing here explains why — design §4.9 (teams), §4.8
(roles), §4.4a (nodes) and §5 (a repo's own file) do that, and this points at them where it helps.

**What a team is.** A named definition of sessions to start together: a **lead** and its
**members**, each under a **role**, all of them over one or more **projects** (a project is repos,
and where each repo is checked out on each host). `ao team start` reads the definition, checks
everything first, then creates the lead and each member with the lead as its controller. Nothing
about a team is stored on a session's record but two strings, `team` and `project`, and nothing
keys on them (§9 invariant 9) — the definition is the whole of it.

---

## 0. Before anything: is there a host agent?

```
ao status
```

`no sessions` (or a list) means the agent is up. **Exit 3 means it is not** — a person starts one
with `agentorc-agent serve`, or installs the units with `ao service install`. A session that gets
exit 3 stops there and says so: it never starts one (`ao --skill`).

## 1. Decide where the team runs

A team runs on the machine you start it from, unless its definition carries `host:` — then every
session lands on that **node** (§4.4a). Skip to step 2 if it runs here.

For a container node, in `~/.agentorc/hosts.yml`:

```yaml
nodes:
  contractmatch: {container: {devcontainer: ~/contractmatch}}
```

then bring it up and check it:

```
ao host up contractmatch
ao host status contractmatch
```

`ao host` takes `up`, `rebuild`, `forget` and `status`, and the name must be a `nodes:` entry with
a `container:` block. **The one-time human steps are not `ao`'s**: the node's `env` file and a
`claude` login inside it for each profile the team uses. A node whose link is down is not a team
you can start — `ao team start` refuses before it creates anything.

## 2. Say what the project is

In `~/.agentorc/org.yml` — a project is repos, and a repo is where it is checked out per host:

```yaml
projects:
  contractmatch:
    repos:
      contractmatch: {kmaster: ~/contractmatch, contractmatch: /home/kmaster/contractmatch}
```

The key under a repo is a **host name**, and the value that host's checkout. A team's sessions are
started in the checkouts of the repos of its projects, so a missing entry for the host the team
runs on is what `ao team start` refuses on.

## 3. Say what the team is

Beside `projects:` in the same file:

```yaml
teams:
  cm-grind:
    projects: [contractmatch]
    host: contractmatch          # omit to run on the machine you start it from
    lead: {role: lead, name: orchestrator-cm}
    members:
      - {role: grinder, count: 2, name: tdgrind-cm, lane: free-pick}
```

- **`lead:`** — always a mapping. `role` (default `lead`), `name` (default `<team>-lead`), and
  optionally `home`, `profile`, `lane`, `brief`, `grants`, `unattended`. **`lead: {role: person}`**
  means *the person leads*: no lead session is started. (`lead: person`, the bare string, is
  refused — *teams.<name>.lead must be a mapping, not str*.)
- **`members:`** — each is a role and a `count`; `name` is the **prefix**, and above one member the
  sessions are `<name>-1`, `<name>-2`, …  A member may instead be `{team: other-team}`, which
  nests that team's members under this one.
- Both take `lane:` — the references that member works, in order — and `unattended:` (default
  true).

A team whose only project is one repo can live in **that repo's** `.agentorc.yml` under `teams:`
instead, and travels with the checkout. The org file wins a name collision.

## 4. Say what the roles are

A role is a preset: a brief, a lane, grants, a profile, an icon. Three are built in —

| role | brief | grants |
|---|---|---|
| `lead` | the package's `lead.md` | `control` |
| `grinder` | the package's `grinder.md` | none |
| `hunter` | the package's `hunter.md` | none |
| `plain` | none | none |

— and a repo overrides any key in its `.agentorc.yml`:

```yaml
roles:
  grinder: {brief: docs/briefs/grinder.md, lane: free-pick, profile: grind, icon: wrench}
  lead: {brief: docs/briefs/lead.md, grants: [control]}
controllers: [orchestrator-cm]
ledger: docs/technical_debt.md
```

Resolution is lowest first: the package's built-ins, then an org-wide `roles:` overlay in
`org.yml`, then the repo's own — each overriding **per key**, so a repo that sets only `brief`
keeps the built-in's grants. A `brief:` path is relative to the repo root; a built-in name is the
package's own file. `{lane}` in a brief is replaced with the member's lane.

Check what resolves, from inside the repo:

```
ao roles
```

## 5. Start it, and read it

```
ao team list                 # every definition, its source file, and whether it is live
ao team start cm-grind       # every check first, then the lead, then each member
ao team status cm-grind      # each member with its state, lane and report line
```

`ao team start` is **all or nothing**: every check runs before any session is created, so a bad
definition costs you nothing. A name a **live** session already holds refuses the whole start; a
name an exited or closed session holds is **superseded**, which is what makes `start` the restart
too. `-p/--profile` overrides the profile for every session in the team.

Then, per session:

```
ao status -v                 # grants, team, project, under (its controllers), members, stop time,
                             # the tool's title, the model, its report line, findings, out of work,
                             # its `doing` line and unread mail — whichever of those it has
```

For what a **role** resolved to, read `ao roles` in the repo; `ao status -v` shows what a session
*is*, not the preset it came from.

## 6. Stop it

```
ao team stop cm-grind                 # wrap up the members, then the lead — each finishes and exits
ao team stop cm-grind --now           # kill instead of asking
ao team stop cm-grind --close         # also close each member that settled clean and pushed
```

`--close` is what lets `ao team start` run again under the same names without superseding anything.
`--timeout` bounds the wait for the members (default 300 s).

---

## For a Claude session told to do this

You are reading this because someone ran `ao team --skill`. The rules of `ao --skill` still apply —
they are about behaving inside a session; these are about setting one up.

1. **Read before you write.** `ao team list` and `ao roles` say what already exists. A team that is
   already defined is edited, not re-added under a second name.
2. **Write the definition, then check it, then start it.** `ao team list` fails loudly on a
   malformed file and names the key; fix it there rather than at `start`.
3. **Never invent a host, a repo path or a profile.** A checkout that is not in `projects:` for the
   host the team runs on is a refusal, not a guess — ask the person for the path.
4. **The one-time human steps are not yours**: a profile's `claude` login, a node's `env` file,
   anything that needs a credential. Say which are outstanding and stop.
5. **Report what you did with the tool's own words**: paste `ao team status <name>`. Do not
   summarise a state you did not read.
6. **`ao --json` on every call** when you are parsing, never prose (`ao --skill`).

## Where this leaves the older documents

`docs/briefs/README.md` covers the one case this does not: a **single** worker launched by hand
with `ao new`, and the membership pitfall that comes with it. Design §4.9 is the spec this recipe
is a path through; read it when a key here is not enough.
