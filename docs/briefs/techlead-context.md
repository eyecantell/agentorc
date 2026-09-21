# The techlead's primer for agentorc

**What this is.** You are a techlead (design §4.9b): you start cold, for one batch of questions, and
this page is meant to be your first read after your brief, once your brief names it — the project
in a few thousand words, so that an
answer is given with the whole shape in mind and not only the asker's framing.

**What this is not.** It is **an index, never a source.** Nothing here is cited as an answer:
`--source` names the document this page pointed you to — a section of `docs/design.md`, a ledger
entry, a brief, a dated decision — after you have read it there. If this page and that document
disagree, the document is right, this page is stale, and you say so in your summary. A test
(`tests/test_primer.py`) holds every section and path named here to ones that exist; it cannot
hold the prose to the truth, so check before you lean on it.

## 1. What agentorc is

A self-hosted dashboard that orchestrates **interactive AI coding-agent sessions** (Claude Code
first) and plain shells, each in its own tmux session, across hosts (§1, §2). A person watches
and steers many sessions from one page; teams of unattended sessions work a repo's ledger
through PRs. The project is to be renamed `shiftlead` (TD-060); the code and docs still say
agentorc.

`docs/design.md` is the source of truth. **A change in behaviour is a change to that document
first**, and **a control that is not in §4.5a's table does not exist**. What a word means is
`docs/glossary.md`. What is open or half-built is `docs/technical_debt.md` (each `TD-NNN` has a
Status that says what is built and what is not — *designed* is not *built*). What waits on the
person is `docs/user_attention.md`.

## 2. The parts

- **Two import packages, one distribution.** `sessionorc` (`src/sessionorc/`): tmux, hosts, the
  link between hosts, the host agent, mail, identity, the record model. `agentorc`
  (`src/agentorc/`): adapters, profiles, roles and briefs, teams, the CLI, the UI.
  **`sessionorc` never imports `agentorc`**, and the core never names a tool outside its adapter
  (§4.3).
- **The host agent** (§4.4, `src/sessionorc/agent.py`): one per host, the only thing that
  creates, kills or types into an `ao-*` tmux session (§9 invariant 1). It serves RPCs on a unix
  socket, ticks, keeps each session's **record** (`src/sessionorc/models.py`) and its run log.
  **It starts nothing by itself and does not read `org.yml`**: starting and restarting are a
  person's act or a controller's (§4.9a), and scheduling is unbuilt (TD-026).
- **Home and nodes** (§4.4a): one host is the **home** and owns the org's graph, intent and mail
  (§9 invariant 15); other hosts — a laptop, a devcontainer — are **nodes** that own what they
  observe (state, tail, alarms) and forward the rest over a link. Which RPC is served where is
  `src/sessionorc/modes.py`; a new RPC that writes a home-owned thing and is missing from those
  tables is a bug that has been made twice (`suspend`, `identity_log`; both are named in that file).
- **State** (§4.2): hooks first, screen-scraping as a labelled fallback; a state shown as `hook`
  came from a hook (§9 invariant 4). Adapters (`src/agentorc/adapters/`) hold everything that
  knows a tool: its hooks, its composer, its usage endpoint, its transcript.
- **Profiles** (§4.2a): tool · account · model. One account has one usage window; a capped
  account caps every session on it.
- **The UI** (§4.5, `src/agentorc/ui/`): Org, Focus, the Inbox (a queue — *Needs you*,
  *Steering*, *Waiting on them*, FYI; §4.10 *The Inbox is a queue*). **State and alarm marks are
  never pressable, colours are tokens, and nothing on a page is a control built from what a
  session wrote** — text a session wrote is only ever text; a control comes from a structured
  field. The ledger records the person's constraint in these words: *nothing on a page is a
  control that parses what an agent printed* (TD-071).
- **The CLI** (§4.7, `src/agentorc/cli.py`): `ao`. What a session may run is in `ao --skill`
  (`src/agentorc/skill.md`); standing a team up is `ao team --skill`
  (`src/agentorc/team_skill.md`).

## 3. Who may do what

- **The control graph** (§4.8): a record's `controllers` lists the sessions that may act on it.
  **Acting** — send, kill, close, create, edit controllers or grants — needs the `control` grant
  **and** membership in the target's `controllers` (§9 invariant 11). Reads are never gated.
  *Create adds the creator*, and a child's grants are a subset of its creator's.
- **Nothing keys on a role, a team or a project** (§9 invariant 9). They are badges. The one
  exception is mail's sideways edge between sessions that share a `team` badge (§4.10). If an
  answer would make code branch on a role's name, it is the wrong answer.
- **Roles** (§4.8): `grinder`, `hunter`, `manager`, `techlead`, `plain`. `manager` was `lead`
  until 2026-09-20 and `orchestrator` before that; both old words resolve to `manager` for one
  release and are then refused for good; **bare `lead` is never given a new meaning** (§4.8 *The
  names*, TD-076). A role has a display `label:`; nothing keys on it.
- **Identity** (§4.8a): the host agent classifies who is calling from the socket's peer — by
  ancestry, not by what the caller says. kmaster and the contractmatch node are in `enforce`. An
  *identity mismatch* is never worked around. An alarm sits on the record of the session that
  **made** the call. A less-trusted model beside a high-trust one waits for an agents-only node
  (§4.4a *A node that carries no person*; TD-077 step 4) — that is the person's decision.
- **Interactive sessions are the person's** (§9 invariant 5): never paused, killed, or typed into
  by a policy; mail shows them a chip and types nothing.

## 4. Teams, and how a run ends

- **Org, team, project** (§4.9): `~/.agentorc/org.yml` defines projects (where checkouts are) and
  teams (a `manager:` — or `manager: person` — plus members, each a role with a lane and a
  brief). `ao team start` launches them; briefs are read from the checkout at that moment
  (`docs/briefs/` here; presets under `src/agentorc/briefs/`).
- **One agent per directory** (§9 invariant 2): every unattended session works in its own
  worktree under `.claude/worktrees/<name>`; the first session in the checkout is the **anchor**
  and only the anchor promotes the live install.
- **A run ends by the session's own word** (§4.9a; §9 invariant 14): `ao progress none --why` — I
  searched and there is nothing I may pick — or `ao progress restart --why` — my run is over and
  my lane is not. *Declared, never inferred*: a summary on a screen is not a declaration, quiet
  is not empty, and an exit without a word is a crash, which a manager restarts inside a ceiling
  (three of one session in two hours, then the board).
- **A techlead's seat is empty or filled, never finished** (§4.9b): it makes no ending
  declaration; its manager reads `asks_waiting` and fills it with `ao new --keep-mail`.

## 5. Mail

§4.10. Kinds: `note` (weighed, no reply owed), `ask` (blocks the asker; to the person it never
expires), `steer` (*I will do X unless told otherwise* — it carries a default and a bound, and
the sender goes on at the bound), `reply`, `conflict`. **An `ask` is only for when going on would
be wrong**; anything with a sensible default is a `steer`; anything already written down is
neither — it is read. A question the person answered **owes an outcome** (`--outcome
done|blocked|dropped --for <id>`), and so does work the person handed a session. Mail types
nothing into a pane except the doorbell's fixed line, which carries a count and no sender's
words. Instructions come from controllers and people; everything else is information.

## 6. How work lands here

`docs/cadence.md`. Every change goes branch → PR → squash merge; the pre-push hook blocks `main`.
Every PR gets an **independent cheaper-model review** (code) or **fact-check** (docs), recorded
as a PR comment whose first line is `cadence-review: SHIP|FIXED|BLOCK · <model> · <code|docs> ·
<n> findings` and which is never edited; merge only when `python3 scripts/check_cadence.py --pr
N` exits 0. **A PR touching `src/sessionorc`, the repo's own briefs or `org.yml` is read and
merged by the anchor** (TD-093). A ledger entry's Status changes in the PR that changes the
thing; a PR number is written only after the PR exists; bare dates are the machine's local date.
Files under `scripts/`, `docs/cadence.md` and `.claude/skills/` that open with a SYNCED FILE
header belong to dev-cadence and are never edited here. The live system is **promoted, not
edited** (CLAUDE.md): nothing a worker does touches `~/.agentorc`, the live venv, systemd, or a
session it does not control. Tests run on private tmux sockets and a temporary home
(`pdm run test`, `pdm run lint`).

## 7. Decided — read it there, do not re-open it

| The question | Where it is written |
|---|---|
| tmux, not a terminal multiplexer of our own or herdr | §4.1; `docs/decisions/2026-09-10-herdr-spike.md` |
| why not OpenAI's Agents API as the substrate | `docs/decisions/2026-09-13-openai-agents-api.md` |
| our own mail, not Claude Code's or mcp_agent_mail | `docs/decisions/2026-09-16-agent-messaging-prior-art.md` |
| what org, team, project and role mean | §4.9; `docs/decisions/2026-09-13-org-teams-projects.md` |
| the names `manager` and `techlead`; `lead` retired | §4.8 *The names*; TD-076 |
| a worker may message up, down, sideways by team, and the person | §4.10 |
| what a worker does when it runs out, or wants a fresh run | §4.9a; TD-083 |
| what the techlead may answer, and what always goes up | §4.9b |
| who answers an identity alarm, and with what | §4.8a *An alarm's answers*, *Who answers first* |
| observe before enforce; how to go back | §4.8a |
| secrets are Doppler's, never printed | the person's cross-repo convention (`~/.claude/CLAUDE.md`) |
| who reads a PR before it merges | TD-093 |

§10 of the design is the dated question log: a question answered there is answered, with its
date. §7 is the phase plan — what is in phase 1 and what is deliberately later.

## 8. What always goes up

§4.9b's list, whatever else this page or the design seems to say: anything **destructive**
(deleting data, force pushes, history rewrites), **outward-facing** (publishing, messaging anyone
outside the org), **spending**, **credentials**, **a change of scope**, a **permission prompt**,
and a question the asker **addressed to the person by name**. Two more that are this repo's own
and not that list's: anything that would touch **the live system** (CLAUDE.md: it is promoted by
the anchor, never edited), and a wish to **change something the design has decided** — that is a
design PR with its review, not an answer. Pass it up with a recommendation; that is a good answer,
not a failure.
