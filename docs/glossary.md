# Glossary

The words agentorc uses, one meaning each. [`design.md`](design.md) is the source of truth for
**behaviour**; this file is the source of truth for **words**. When the two disagree on a word,
this file wins and the design is corrected to match.

Every entry is marked **decided** (with the date and who decided it) or **proposed** (a
default, awaiting a decision). Dated history (design §10, `docs/decisions/`, the archive) keeps
the words of its day and is never rewritten; this glossary names the retired words so older
text stays readable.

Format: **term** — meaning. *Not:* retired or confusable words. Status.

---

## Who does the work

- **person** — the human at the top of the org: starts sessions and teams, reads the board,
  answers what agents escalate. *Not:* user, operator. Paul, in this repo's own docs. —
  *proposed* (already the design's word).
- **agent** — an AI coding session: an interactive session running a tool such as Claude Code.
  Bare *agent* never means the daemon. *Not:* bot, instance. — **decided** 2026-09-16 (Paul).
- **session** — the record agentorc keeps for one tmux session: an agent, a shell or a command
  run. Every agent is a session; not every session is an agent. The record's field names keep
  *session*. — *proposed* (design §4.1, ADR 2026-09-13).
- **worker** — an agent on a team that coordinates nobody: its record names a lead in its
  `controllers`, and no session names it. Started from a role such as `grinder` or `hunter`.
  — **decided** 2026-09-16 (Paul).
- **lead** — the session that coordinates one team's workers: the sessions whose `controllers`
  name it. Started from the `lead` role. A person may lead a team instead (`lead: person`), in
  which case no lead session exists. *Not:* orchestrator, orc (as a position). — **decided**
  2026-09-16 (Paul), with the role preset renamed from `orchestrator` to `lead`.
- **conductor** — the session that coordinates leads: its members are leads, not workers.
  Agentorc does not treat it specially (design §4.8); the word names a position in the graph, not
  a kind of session. *Not:* orc-of-orcs, orchestrator of orchestrators, top orc. — **decided**
  2026-09-16 (Paul).
- **member** — the graph relation, not a position: B is A's member when B's `controllers` name
  A. A worker is its lead's member; a lead is its conductor's member. — **decided** 2026-09-16
  (Paul), as the relation behind *worker*.
- **controller** — the inverse: A is B's controller when B's `controllers` list names A. A
  controller may act on its members if it also holds the `orchestrate` grant (invariant 11).
  — *proposed* (design §4.8).

## How work is organised

- **org** — everything one person runs through agentorc, across hosts; also the name of the home
  page. One per install. *Not:* fleet, herd. — *proposed* (ADR 2026-09-13).
- **team** — a lead (or the person) plus its workers, defined once and started many times.
  A team may contain teams. — *proposed* (ADR 2026-09-13).
- **project** — a named set of one or more repos that belong together. — *proposed* (ADR
  2026-09-13).
- **role** — a skillset preset an agent is started from: brief template, lane shape, grants,
  profile. Nothing keys on it at runtime (invariant 9). Built-in: `grinder`, `hunter`, `lead`,
  `plain`. *Not:* type, kind. — *proposed*; the `orchestrator` → `lead` rename is **decided**.
- **grinder** — a role: resolves each lane item to a merged PR. — *proposed*.
- **hunter** — a role: finds problems and files them with evidence, never fixes them. —
  *proposed*.
- **brief** — the job description a session is started with, written from its role's template.
  Describes the job, not the run (TD-042). — *proposed*.
- **lane** — the list of references a worker was handed, or `free-pick`. — *proposed*.
- **profile** — `(adapter, account, model)` a session runs under (design §4.2a). — *proposed*.
- **grant** — a capability on a session record; `orchestrate` is the one that lets a session act
  on its members. — *proposed*. (Open: whether `orchestrate` keeps its name now that
  *orchestrator* is retired — see below.)
- **home** — the one checkout an agent lives in; **reach** — other repos a project lets it read or
  change without moving it. — *proposed*.

## Machinery

- **host** — a machine sessions run on. — *proposed*.
- **host agent** — the per-host daemon (`agentorc-agent`, `sessionorc/agent.py`): the only
  process that creates, kills or types into `ao-*` tmux sessions (invariant 1). Always written in
  full; never shortened to *agent*. Command and module names are unchanged. — **decided**
  2026-09-16 (Paul).
- **UI host** — the machine running `agentorc[ui]`. — *proposed*.
- **adapter** — the per-tool code that knows how a tool reports state and takes input (design
  §4.3). — *proposed*.
- **hook** — a tool's own event callback (Claude Code's `Stop`, `PreToolUse`…) that reports
  state to the host agent. A state from a hook is `hook` confidence; one read off the pane is
  `scraped`. — *proposed*.
- **anchor session** — the first agent in a checkout; every other concurrent agent works in a
  worktree (invariant 2). — *proposed*.

## What a session does

- **turn** — one cycle of work, from input to settling (design §4.3). A session outlives many
  turns. — *proposed* (TD-047).
- **send** — the act of pasting text into a session and pressing Enter. To an idle session it
  **starts a turn**; to a working one it **steers** the turn in flight. — *proposed* (TD-047).
- **nudge** — a send from a controller to its member. — *proposed*. (Open: keep, or say *send*
  everywhere.)
- **wrap up** — ask a session to finish and stop, with the fixed wrap-up prompt. — *proposed*.
- **kill / close / forget / resume** — kill ends the tmux session; close is the person's "done"
  (kill + reap worktree); forget removes the record; resume continues a tool conversation in a
  new session. — *proposed* (design §4.5a).
- **states** — `working`, `idle`, `needs-you`, `limited`, `stalled?`, `exited`, `closed`,
  `unreachable` (design §2). — *proposed*.
- **unattended / interactive** — whether policies (run window, usage gate, stall) may act on a
  session. An interactive session is never acted on by another session (invariant 5). —
  *proposed*.
- **out of work** — a session's own declaration that its role's test finds nothing left
  (`out_of_work`, design §4.9a). **Wind down** — a lead stopping its team after every member
  declared it. — *proposed*.

## Reporting and mail

- **report** — what a session declares about its work on its own record: **progress** (per
  reference) and **findings**. Addressed to nobody. — *proposed* (design §4.8).
- **message** (**mail**) — an attributed entry delivered to a recipient's **inbox**, never typed
  into a pane (design §4.10). Kinds: `note`, `ask`, `reply`, `conflict`. — *proposed*.
- **person inbox** — the host's inbox for the person; sessions reach it with `ao msg person`. —
  *proposed* (design §4.10, 2026-09-16).
- **thread** — a root message and every `reply` chained to it. — *proposed*.
- **wake** — a turn that mail caused: a doorbell starting one, or `ao wait` returning on mail.
  **Wake budget** — how many wakes a session may take in a rolling window. — *proposed*.
- **doorbell** — the fixed line the host agent types into an idle agent's pane to say it has
  unread mail; carries a count, never a sender's text. — *proposed*.

## Records a person reads

- **board** — `docs/user_attention.md`: items waiting on the person, each with a `Due:` date.
  *Not:* attention list. — *proposed*.
- **ledger** — `docs/technical_debt.md`: known issues and deferred work as `TD-NNN` entries. —
  *proposed*.
- **run log** — a session's recorded pane output from its first byte (invariant 3). — *proposed*.

---

## Open questions — to work through together

1. **"run"** means four things: *run log*, *run window* (when unattended sessions may work),
   *command run* (a predefined shell command session), and *run 3* (the third start of a brief,
   which TD-042 says a brief should not name). Proposal: keep the three compounds, retire bare
   *run N*, and pick a word for "one start of a team or brief".
2. **"tick"** means a lead's loop iteration *and* the host agent's periodic pass (the
   derived-report tick). Proposal: *tick* for a lead or conductor, *pass* for the host agent.
3. **`orchestrate` grant** — keep the name (it describes acting on sessions, which is still
   true), or rename to match *lead* / *conductor* (e.g. `control`, which pairs with
   `controllers`).
4. **nudge vs send** — keep *nudge* as "a controller's send", or retire it.
5. **fleet** — retire in favour of *org*, or keep it for "every agent currently live".
6. **Product and page names** — *agentorc* stays for now (ADR 2026-09-13); does *orc* survive
   anywhere once *orchestrator* is retired as a position?
