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

**What a team is.** A named definition of sessions to start together: a **manager** and its
**members**, each under a **role**, all of them over one or more **projects** (a project is repos,
and where each repo is checked out on each host). `ao team start` reads the definition, checks
everything first, then creates the manager and each member with the manager as its controller. Nothing
about a team is stored on a session's record but two strings, `team` and `project`, and nothing
keys on them (§9 invariant 9) — the definition is the whole of it. **One exception, and it lands
exactly on the `manager: {role: person}` case below:** the mail gate's *sideways* edge keys on the
`team` badge, so on a team with no manager session the badge is the only edge between members there
is (§4.10). Everywhere else the badge is a label.

---

## 0. Before anything: is there a host agent?

```
ao status
```

`no sessions` (or a list) means the agent is up. **Exit 3 means the command could not reach one**,
and the line says which of two things happened: *the host agent restarted under this command — run
it again* (it is up; run it again and nothing else), or *host agent not reachable*, which is the
one a person answers with `agentorc-agent serve`, or `ao service install` for the units. A session
that gets exit 3 stops there and says so: **it never starts one** (`ao --skill`).

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
    manager: {role: manager, name: manager-cm}
    members:
      - {role: grinder, count: 2, name: grinder-cm, lane: free-pick}
```

- **`manager:`** — always a mapping. `role` (default `manager`), `name` (default `<team>-lead`), and
  optionally `home`, `profile`, `lane`, `brief`, `grants`, `unattended`. **`manager: {role: person}`**
  means *the person manages*: no manager session is started. (`manager: person`, the bare string, is
  refused — *teams.<name>.manager must be a mapping, not str*.) `lead:`, its name until
  2026-09-20 (TD-076), is refused as an unknown key.
- **`techlead:`** — optional, one per team: the go-between that answers teammates' questions
  before they reach you (design §4.9b). `name` (default `<team>-techlead`), and optionally `home`,
  `profile`, `brief`, `context`; no `role` and no `grants`. It is started after the manager, under
  it, and every brief in the team names it as `{techlead}`. Without one, questions come to you as
  now. **`context:`** is the seat's **primer** — a path in its home checkout that its brief reads
  first (`{context}`); `ao team start` says so, and starts anyway, when it is missing. See
  *A techlead's primer* below before the first start with a seat.
- **`members:`** — each is a role and a `count`; `name` is the **prefix**, and above one member the
  sessions are `<name>-1`, `<name>-2`, …
  **`{team: other-team}` — a nested team — parses but is refused at `start`, because it is not
  built** (design §4.9: the flat case ships first). Write the teams flat and start each on its own
  until it is. The trap is that `ao team list` accepts it and only `ao team start` refuses it,
  which is the one place in this recipe where a file that reads fine is not one.
- Both take `lane:` and `unattended:` (default true). **`lane:` is one word or a list.**
  `free-pick` means *choose from the ledger by priority*; anything else is a named lane, the
  references that member works — `lane: [TD-041, TD-054]`, or `lane: TD-041,TD-054` — which its
  brief reads as `TD-041, TD-054` (`{lane}`) and works **in that order, stopping at its end**
  rather than free-picking past it. A member with a named lane is out of work when every item
  on it is `done` or `dropped`.

A team whose only project is one repo can live in **that repo's** `.agentorc.yml` under `teams:`
instead, and travels with the checkout. The org file wins a name collision. **Which file:** the
repo's, when the team works that one repo from its checkout on the machine you start it from —
its `projects:` defaults to the repo, and the project is made from the checkout it was read in;
`org.yml`, when the team spans repos, runs on another host (`host:`) or in a container node, or
its repo's checkout is not where you start it, because only `org.yml` says where a checkout is
on a host.

`.agentorc.yml` takes these keys and refuses any other: `adapter`, `worktrees`, `anchor`,
`ledger`, `unattended`, `roles`, `controllers`, `ready_when`, `commands` and `teams` — **never
`projects`**, which is the org's (design §5).

**Ignore `.claude/worktrees/`** in the repo's `.gitignore` before the first start: every team
session works in `<repo>/.claude/worktrees/<name>`, and a repo that does not ignore it leaves
nested checkouts that a `git add -A` in the main checkout stages as gitlinks.

## 4. Say what the roles are

A role is a preset: a brief, a lane, grants, a profile, an icon. Six are built in —

| role | brief | grants |
|---|---|---|
| `manager` | the package's `manager.md` | `control` |
| `grinder` | the package's `grinder.md` | none |
| `hunter` | the package's `hunter.md` | none |
| `techlead` | the package's `techlead.md` | none |
| `auditor` | the package's `auditor.md` | none |
| `plain` | none | none |

(`lead` and `orchestrator`, the manager's old names, are unknown roles.)

— and a repo overrides any key in its `.agentorc.yml`:

```yaml
roles:
  grinder: {brief: docs/briefs/grinder.md, lane: free-pick, profile: grind, icon: wrench}
  manager: {brief: docs/briefs/manager.md, grants: [control]}
controllers: [manager-cm]
ledger: docs/technical_debt.md
```

Resolution is lowest first: the package's built-ins, then an org-wide `roles:` overlay in
`org.yml`, then the repo's own — each overriding **per key**, so a repo that sets only `brief`
keeps the built-in's grants. A `brief:` path is relative to the repo root. **A repo's brief is a
supplement, never a replacement** (design §4.8): the package's template is always the brief, and
the repo's file is filled into its *This repo's rules* section — see *A repo's brief*, below. At
the start a brief's placeholders are filled: `{lane}` with the member's lane,
`{techlead}` and `{manager}` with the ids those sessions take, `{context}` with the seat's primer —
each `none` where there is nothing to name (an empty lane reads `(none given)`).

**A `profile:` must already exist**, in `~/.agentorc/profiles.yml` — it is an account of a tool,
not a word you may invent, and `ao team start` refuses a name that is not declared:

```yaml
default: paul
profiles:
  paul:  {adapter: claude-code, account: paul,  model: opus,   config_dir: ~/.claude}
  grind: {adapter: claude-code, account: grind, model: sonnet, config_dir: ~/.claude-grind}
```

With no file at all there is one implicit profile, `default`, on the tool's own config directory —
so a team that names none works, and a team that names `grind` needs the block above **and** a
`claude` login inside that `config_dir`, which is a person's one-time step and nobody else's.

Check what resolves, from inside the repo:

```
ao roles
```

### A repo's brief

The built-in templates hold every agentorc mechanic — the lane loop, claims and leases, the
cadence review and merge, asking and outcomes, the declarations a run ends with, the usage gate's
pause, the never-list of `ao` verbs — and move with the package on every promote. They also assume
agentorc's own layout: `CLAUDE.md` and `docs/cadence.md` as first reads, `pdm run test` and
`pdm run lint`, `/cadence` and `scripts/check_cadence.py` as the gate, `docs/technical_debt.md`
and `docs/user_attention.md` as ledger and board. **A repo whose layout, gate or standing rules
differ says so in its own brief**, and only that. Wherever it is given — a member's or manager's
`brief:` in the team definition (which takes the slot in place of the role's), a repo's
`roles.<name>.brief`, or `ao new --role <r> --brief <path>` — it is filled under *This repo's
rules*, and **where it and the template disagree, the repo's wins**, except that it may add to the
never-list and never take from it. What the host agent enforces (the usage gate, the restart
ceiling, the permission gate) no brief moves. A role the package ships no template for takes its
brief whole.

A skeleton — one short section each, and nothing the template already says:

- **First reads** — the files to read before anything, if not `CLAUDE.md` and `docs/cadence.md`.
- **Where it works** — the branch naming, the default branch, anything about worktrees the repo
  does differently.
- **The gate** — the test and lint commands, the merge check, who reviews and who merges which
  PRs (e.g. *anything under `src/core/` waits for the anchor*).
- **Standing rules** — what is never done here: deploys, destructive data operations, files with
  their own line endings, what waits on the person, whole areas the team leaves alone.
- **The lane** — what a `free-pick` excludes in this repo, or the context a named list needs.
- **Asking** — only if it differs from the template's (a techlead, else the person).

`ao team start` and `ao new` say, and start anyway, when a repo's brief repeats one of the
template's headings — the sign of a whole brief not yet cut down to the repo's own rules — and
`ao team start` says the same of a clock time or a run number in it: a brief describes the job,
not the run (design §4.8), because the start is also the restart.

### A techlead's primer

A techlead starts cold, for one batch of questions, and sees only the asker's framing; a design
of any size cannot be read per fill. So the seat reads a primer first — this repo's is
`docs/briefs/techlead-context.md`, a model for another's (design §4.9b *Its standing context*).

- **It is an index, never a source.** An answer cites the document the primer pointed to, read
  there; the primer itself is never cited, so a stale one can misdirect a search and cannot
  become an authority.
- **What goes in:** what the project is, in a page; its parts and which depends on which; who may
  do what; how a change lands (review, merge rights, what is never touched); the questions already
  decided, each with **where it is written**; and what always goes up. Two to three thousand
  words — it is read on every fill.
- **What stays out:** anything that would be quoted as the answer itself; anything that changes
  weekly (the ledger's contents, who is working on what); secrets.
- **When:** before the first `ao team start` with a techlead seat; and again in the PR that
  changes the architecture, a standing decision or the merge rules — that PR updates the primer,
  and its fact-check reads the primer against the change.
- **Who:** a session of **that** repo with its design in front of it, or the person — never a
  session reaching across from another repo, which knows neither its decisions nor its never-list.
- **Held to its pointers by a test** where the repo can — agentorc's is `tests/test_primer.py`,
  four checks, each a few lines in any language: every design section the primer names is a
  heading in the design; every invariant it names is in the design's invariant list; every path
  it names exists in the repo; every ledger id it names is an entry in the ledger or its archive.

## 5. Start it, and read it

```
ao team list                 # every definition, its source file, and whether it is live
ao team start cm-grind       # every check first, then the manager, then each member
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
ao team stop cm-grind                 # wrap up the members, then the manager — each finishes and exits
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
4. **Check the profile before you name it.** A `profile:` in a role or a member must be a key in
   `~/.agentorc/profiles.yml`; `ao team start` refuses one that is not, and the file is the only
   place that says which exist.
5. **The one-time human steps are not yours**: a profile's `claude` login, a node's `env` file,
   anything that needs a credential. Say which are outstanding and stop.
6. **Report what you did with the tool's own words**: paste `ao team status <name>`. Do not
   summarise a state you did not read.
7. **`ao --json` on every call** when you are parsing, never prose (`ao --skill`).

## Where this leaves the older documents

`docs/briefs/README.md` covers the one case this does not: a **single** worker launched by hand
with `ao new`, and the membership pitfall that comes with it. Design §4.9 is the spec this recipe
is a path through; read it when a key here is not enough.
