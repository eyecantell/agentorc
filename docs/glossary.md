# Glossary

The words agentorc uses, one meaning each. [`design.md`](design.md) is the source of truth for
**behaviour**; this file is the source of truth for **words**. When the two disagree on a word,
this file wins and the design is corrected to match.

Every entry is marked **decided** (with the date and who decided it) or **proposed** (a
default, awaiting a decision). Dated history (design §10, `docs/decisions/`, the archive) keeps
the words of its day and is never rewritten; this glossary names the retired words so older
text stays readable.

Format: **term** — meaning. *Not:* retired or confusable words. Status.

## The rule: two layers, one genre each (Paul, 2026-09-16)

Decided after a Fable review of the whole vocabulary, so names stay intuitive and metaphors do
not mix:

- **Organisation words are workplace words** — what a person has to learn: who works (person,
  agent, worker, lead, director), how work is arranged (org, team, project, role, brief, lane,
  home), and what is handed around (report, message, inbox, board, ledger). A familiar org chart
  teaches them in one read.
- **Mechanism words are plain and tool-native** — session, turn, hook, pane, worktree, grant,
  `controllers`, the state names, tick, wake, run log. They match tmux, git and Claude Code,
  because the person sees those tools' own words in the same terminal.
- **No third genre.** No music, nautical, animal, household or driving words, with one named
  exception: **doorbell**, kept as the proper name of one fixed line. *orc* survives only in
  product, package and command names (`agentorc`, `sessionorc`, `cmdorc`, `ao`), never for a
  session or a position.
- **Record fields, state names and `ao` verbs do not change** for a word's sake; the one
  exception is the `orchestrate` grant, renamed `control` with an alias (TD-055).

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
  which case no lead session exists. *Not:* orchestrator, orc, supervisor. — **decided**
  2026-09-16 (Paul), with the role preset renamed from `orchestrator` to `lead`.
- **director** — the session that coordinates leads: its members are leads, not workers.
  Agentorc does not treat it specially (design §4.8); the word names a position in the graph, not
  a kind of session. Director > lead > worker. *Not:* conductor (decided and replaced the same
  day), orc-of-orcs, orchestrator of orchestrators, top orc. — **decided** 2026-09-16 (Paul).
- **member** — the graph relation, not a position: B is A's member when B's `controllers` name
  A. A worker is its lead's member; a lead is its director's member. — **decided** 2026-09-16
  (Paul), as the relation behind *worker*.
- **controller** — the inverse: A is B's controller when B's `controllers` list names A. A
  controller may act on its members if it also holds the `control` grant (invariant 11).
  — *proposed* (design §4.8).

## How work is organised

- **org** — everything one person runs through agentorc, across hosts; also the name of the home
  page. One per install. *Not:* fleet, herd. — *proposed* (ADR 2026-09-13); retiring *fleet* in
  prose and UI is **decided** 2026-09-16 (Paul) — code variable names keep it.
- **team** — a lead (or the person) plus its workers, defined once and started many times.
  A team may contain teams. — *proposed* (ADR 2026-09-13).
- **project** — a named set of one or more repos that belong together. — *proposed* (ADR
  2026-09-13).
- **role** — a skillset preset an agent is started from: brief template, lane shape, grants,
  profile. Nothing keys on it at runtime (invariant 9). Built-in: `grinder`, `hunter`, `lead`,
  `plain` (`lead` is `orchestrator` in the code until TD-055 renames it). *Not:* type, kind. — *proposed*; the `orchestrator` → `lead` rename is **decided**.
- **grinder** — a role: resolves each lane item to a merged PR. — *proposed*.
- **hunter** — a role: finds problems and files them with evidence, never fixes them. —
  *proposed*.
- **brief** — the job description a session is started with, written from its role's template.
  Describes the job, not the run (TD-042). — *proposed*.
- **lane** — the list of references a worker was handed, or `free-pick`. — *proposed*.
- **profile** — `(adapter, account, model)` a session runs under (design §4.2a). — *proposed*.
- **grant** — a capability on a session record (the field is `capabilities`; prose says
  *grant*). **`control`** is the one that lets a session act on its members, so one word names the
  whole authority mechanism: the `control` grant, the `controllers` list, `ao control`. *Not:*
  `orchestrate`, accepted as an alias for one release. — **decided** 2026-09-16 (Paul); rename is
  TD-055.
- **home** — the one checkout an agent lives in; **reach** — other repos a project lets it read or
  change without moving it. *reach* means only that: a lead's members are its *members*, not its
  reach. — *proposed*.

## Machinery

- **host** — a machine sessions run on. — *proposed*.
- **host agent** — the per-host daemon (`agentorc-agent`, `sessionorc/agent.py`): the only
  process that creates, kills or types into `ao-*` tmux sessions (invariant 1). Always written in
  full; never shortened to *agent*. Command and module names are unchanged. — **decided**
  2026-09-16 (Paul).
- **UI host** — the machine running `agentorc[ui]`. — *proposed*.
- **home** (host agent) — the one host agent that holds the org's session graph and mail; kmaster
  on Paul's machines. *Not:* hub, master, server. — **decided** 2026-09-16 (Paul, design §4.4a).
  (The *home* of an agent — its checkout — is a different, older sense; say *home host agent* when
  the context could mean either.)
- **node** — any other host agent: keeps its host's tmux, pty and hooks, and dials the home. —
  **decided** 2026-09-16 (Paul, design §4.4a).
- **link** — a node's one long-lived connection to the home (`agentorc-agent link`, over ssh). —
  **decided** 2026-09-16 (Paul, design §4.4a).
- **address** — `<id>@<host>`, a session's org-wide name; a bare id means the record's own host. —
  **decided** 2026-09-16 (Paul, design §4.4a).
- **adapter** — the per-tool code that knows how a tool reports state and takes input (design
  §4.3). — *proposed*.
- **hook** — a tool's own event callback (Claude Code's `Stop`, `PreToolUse`…) that reports
  state to the host agent. A state from a hook is `hook` confidence; one read off the pane is
  `scraped`. — *proposed*.
- **anchor session** — the first agent in a checkout; every other concurrent agent works in a
  worktree (invariant 2). — *proposed*.
- **tick** — one pass of the host agent's own loop: derived reports, policies, the doorbell's
  next check. *Not:* a lead's loop (that is a *round*), pass. — **decided** 2026-09-16 (Paul),
  matching the code.
- **policies** — the host agent's rules that act on unattended sessions (run window, usage gate,
  stall; design §6). *Not:* supervisor. — *proposed*.

## What a session does

- **turn** — one cycle of work, from input to settling (design §4.3). A session outlives many
  turns. — *proposed* (TD-047).
- **send** — the act of pasting text into a session and pressing Enter. To an idle session it
  **starts a turn**; to a working one it **steers** the turn in flight. — *proposed* (TD-047).
- **round** — one iteration of a lead's or director's loop: read status, act, end in `ao wait`.
  "A round every 10 minutes." *Not:* tick. — **decided** 2026-09-16 (Paul).
- *nudge* — **retired** 2026-09-16 (Paul): a controller's send is a **send**. §4.10 splits send
  (control) from message (mail), and a third verb blurred it.
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
  into a pane (design §4.10). Kinds: `note`, `ask`, `reply`, `conflict`. The sender keeps its own
  copy in its **outbox**, which is where the marks it must see live. — *proposed*; built 2026-09-17
  (TD-052 step 1).
- **person inbox** — the host's inbox for the person; sessions reach it with `ao msg person`, a
  person reads it with `ao inbox` outside any session. — *built* 2026-09-16 (design §4.10, TD-052 step 2).
- **thread** — a root message and every `reply` chained to it. — *proposed*.
- **wake** — a turn that mail caused: a doorbell starting one, or `ao wait` returning on mail.
  **Wake budget** — how many wakes a session may take in a rolling window. — *proposed*.
- **bound_hit** — the mark the home writes on a thread when its exchange bound refuses a send;
  every participant's card and `ao` replies show it, and a person's message clears it (design
  §4.10). — *proposed* (2026-09-16).
- **reachable** — the moment the host agent may decide a wake for a session: hook-confirmed
  `idle`, or blocked in `wait`, and, across hosts, its node's link up (design §4.10, §4.4a). Distinct from the
  host state `unreachable`, which is the link being down. — *proposed* (2026-09-16).
- **doorbell** — the fixed line the host agent types into an idle agent's pane to say it has
  unread mail; carries a count, never a sender's text. The one word outside the two genres, kept
  as a proper name. — **decided** 2026-09-16 (Paul).

## Records a person reads

- **board** — `docs/user_attention.md`: items waiting on the person, each with a `Due:` date.
  *Not:* attention list. — *proposed*.
- **ledger** — `docs/technical_debt.md`: known issues and deferred work as `TD-NNN` entries. —
  *proposed*.
- **run log** — a session's recorded pane output from its first byte (invariant 3). — *proposed*.
- **run window** — the hours unattended sessions may work (design §6). — *proposed*.
- **command session** — a session running a predefined shell command. *Not:* command run (as a
  noun). — **decided** 2026-09-16 (Paul).
- *run N* — **retired** 2026-09-16 (Paul). One start of a team or brief gets no name and no
  number: say when it started ("started 09:00"). TD-042 already keeps numbers out of briefs; older
  board and ledger entries keep theirs.

---

## Collisions with the tools' own words

- **agent / subagent** — a *subagent* is always Claude Code's, inside one agentorc session; an
  agentorc session is never called a subagent. The daemon is always *host agent*.
- **team / teammate** — Claude Code has its own agent teams and *teammates*. Write "Claude Code
  team" if that feature is meant; never call an agentorc member a *teammate*.
- **session** — bare *session* is the agentorc record. Say *tmux session* and *the tool's session
  id* in full when those are meant.
- **window** — tmux's word. agentorc's *run window* is a time range and is always the compound;
  never say *window* bare for one.
- **turn, hook, worktree, branch** — deliberately aligned with Claude Code and git.

## Answered 2026-09-16 (Paul, after the Fable review)

1. **run** — keep *run log* and *run window*; *command session* for the noun; *run N* retired, a
   start is named by its time.
2. **tick** — the host agent's loop; a lead's or director's loop is a **round**.
3. **`orchestrate` grant** — renamed **`control`**, with `orchestrate` as a one-release alias.
4. **nudge** — retired; it is a **send**. *supervisor* (→ lead, or policies) and *fleet* (→ org)
   retired with it, in prose and UI only.
5. **fleet** — retired, as above.
6. **orc** — only in product, package and command names; never a session or position. Also
   decided: **conductor → director**, and **doorbell** kept.
