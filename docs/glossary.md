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
  agent, worker, manager, techlead, director), how work is arranged (org, team, project, role, brief, lane,
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
  exception is the `orchestrate` grant, renamed `control` (TD-055; no alias since TD-107).

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
- **worker** — an agent on a team that coordinates nobody: its record names a manager in its
  `controllers`, and no session names it. Started from a role such as `grinder` or `hunter`.
  — **decided** 2026-09-16 (Paul).
- **manager** — the session that runs one team's lifecycle: it starts its members, nudges
  them, checks their merges against the cadence, wraps them up. Its members are the sessions
  whose `controllers` name it. Started from the `manager` role; shown as *Manager*. A person may
  manage a team instead (`manager: person`), in which case no manager session exists. Called
  **lead** from 2026-09-16 to 2026-09-20 and `orchestrator` before that; neither old role name
  resolves any more (TD-107). *Not:* lead, orchestrator, orc, supervisor.
  — **decided** 2026-09-19 (Paul), confirmed 2026-09-20 (TD-076, design §4.8 *The names*).
- **techlead** — the technical go-between of TD-075: a session that answers technical
  questions for a team's workers before they reach a person. Shown as *Tech Lead*. A team's
  optional `techlead:` seat; started per batch of questions, holds no grant (design §4.9b). —
  **decided** as a name 2026-09-19 (Paul); the seat, the preset and the briefs *built* 2026-09-20
  (TD-075 step 1), its mail verbs not yet.
- **lead** — **retired** 2026-09-20 (TD-076). It named the manager; it is never given a new
  meaning, so that a record badged `lead` and a definition's `lead:` key cannot come to mean a
  different session than the one they were written for. In text dated before the rename it
  means the manager.
- **ShiftLead** — the project's name (shiftlead.dev): the thing that runs a shift of agents and
  tells the person what needs them. Spelled as one word with two capitals in prose; the command
  is `ao`, and `agentorc` remains the name of the packages, the state directory, the units and
  the config file until TD-060's machine-side step. — **decided** 2026-09-21 (Paul).
- **designer** — a team's design member: an `unattended` session whose lane is the ledger's `design-first` entries. It writes the design and the entries the grinders pick, merges nothing on a held path, and shows the person only what needs them — an obvious design is merged and noted, a defensible default is a bounded `steer`, taste is an `ask` with answers (design §4.8 *Role names*, TD-120; decided 2026-09-24, an interactive seat from 2026-09-23 until then).
- **director** — the session that coordinates managers: its members are managers, not workers.
  Agentorc does not treat it specially (design §4.8); the word names a position in the graph, not
  a kind of session. Director > manager > worker. *Not:* conductor (decided and replaced the same
  day), orc-of-orcs, orchestrator of orchestrators, top orc. — **decided** 2026-09-16 (Paul).
- **member** — the graph relation, not a position: B is A's member when B's `controllers` name
  A. A worker is its manager's member; a manager is its director's member. — **decided** 2026-09-16
  (Paul), as the relation behind *worker*.
- **controller** — the inverse: A is B's controller when B's `controllers` list names A. A
  controller may act on its members if it also holds the `control` grant (invariant 11).
  — *proposed* (design §4.8).

## How work is organised

- **org** — everything one person runs through agentorc, across hosts; also the name of the home
  page. One per install. *Not:* fleet, herd. — *proposed* (ADR 2026-09-13); retiring *fleet* in
  prose and UI is **decided** 2026-09-16 (Paul) — code variable names keep it.
- **team** — a manager (or the person) plus its workers, defined once and started many times.
  A team may contain teams. — *proposed* (ADR 2026-09-13).
- **project** — a named set of one or more repos that belong together. — *proposed* (ADR
  2026-09-13).
- **role** — a skillset preset an agent is started from: brief template, lane shape, grants,
  profile. Nothing keys on it at runtime (invariant 9). Built-in: `grinder`, `hunter`, `manager`,
  `plain` (`manager` was `lead` until 2026-09-20, TD-076, and `orchestrator` until 2026-09-17, TD-055 step 2; neither old name resolves, TD-107). A role may carry a display `label:` — what the badge shows; nothing keys on it. *Not:* type, kind. — *proposed*; the `orchestrator` → `lead` rename is **decided**.
- **seat** — a place in a team definition that one session fills at a time, named by what it
  does rather than by who is in it: the team's `techlead:` seat. A seat is *empty* or *filled*
  — the session in it may end and another be started into it (`ao new --keep-mail`, so the
  questions that were waiting are still there) — and it is never *finished*, since it declares
  nothing and is not counted in a wind-down (design §4.9b). Workers are members, not seats. On
  a card a seat with nobody in it reads **on call** — *empty* and *filled* are what the manager
  does to it, *on call* is what a person sees (TD-097). Every seat has a **trigger**: the
  techlead's is a question; an **auditor**'s is a count of merged PRs or a period (TD-098). —
  **decided** 2026-09-21 (asked for by Paul: the word was in use and not defined; *on call* his
  choice the same evening).
- **on call** — what a seat's card says while nobody is in it: grey, nothing for the person to
  do, and the slot says what would make it come. Not a state: the record is `exited` or `closed`.
  — **decided** 2026-09-21 (Paul; *empty* and *available* read as something to do).
- **auditor** — a role for a seat with a trigger: checks one area (docs, tests) every n merged
  PRs or every so often, files what it finds, and ends. Hunter-shaped unless its brief says
  otherwise. — *proposed* 2026-09-21 (design §4.9b *Seats with a trigger*, TD-098).
- **grinder** — a role: resolves each lane item to a merged PR. — *proposed*.
- **hunter** — a role: finds problems and files them with evidence, never fixes them. —
  *proposed*.
- **brief** — the job description a session is started with, written from its role's template. The template holds agentorc's mechanics and ships with the package; a repo's brief is a **supplement** filled into the template's `{repo}` slot (design §4.8, TD-114), never a replacement.
  Describes the job, not the run (TD-042). — *proposed*.
- **lane** — the list of references a worker was handed, or `free-pick`. — *proposed*.
- **profile** — `(adapter, account, model)` a session runs under (design §4.2a). — *proposed*.
- **grant** — a capability on a session record (the field is `capabilities`; prose says
  *grant*). **`control`** is the one that lets a session act on its members, so one word names the
  whole authority mechanism: the `control` grant, the `controllers` list, `ao control`. *Not:*
  `orchestrate`, now an unknown grant (TD-107). — **decided** 2026-09-16 (Paul); renamed 2026-09-17
  (TD-055 step 3).
- **home** — the one checkout an agent lives in; **reach** — other repos a project lets it read or
  change without moving it. *reach* means only that: a manager's members are its *members*, not its
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
  next check. *Not:* a manager's loop (that is a *round*), pass. — **decided** 2026-09-16 (Paul),
  matching the code.
- **policies** — the host agent's rules that act on unattended sessions (run window, usage gate,
  stall; design §6). *Not:* supervisor. — *proposed*.

## What a session does

- **turn** — one cycle of work, from input to settling (design §4.3). A session outlives many
  turns. — *proposed* (TD-047).
- **send** — the act of pasting text into a session and pressing Enter. To an idle session it
  **starts a turn**; to a working one it **steers** the turn in flight. — *proposed* (TD-047).
- **round** — one iteration of a manager's or director's loop: read status, act, end in `ao wait`.
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
- **supervised** — a session someone chose to keep running: `ao team start` marks every member and seat, `ao new --supervised` marks one by hand, and §6's *Keeping a team running* restarts, fills and nudges only sessions so marked (and `unattended`). A person's session is never supervised. — *design 2026-09-22, TD-103*.
- **out of work** — a session's own declaration that its role's test finds nothing left
  (`out_of_work`, design §4.9a). **Wind down** — a manager stopping its team after every member
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
  **Wake budget** — how many wakes a session may take in a rolling window. — *proposed*; the `wait`
  half and the budget (unlimited until step 6) *built* 2026-09-16 (TD-052 step 3); the doorbell *built* 2026-09-20 (step 7).
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
- **promote** — make a repo's `main` the live copy: for this repo, install the checkout into the live venv and restart the units (CLAUDE.md); for any repo, its `promote.run` (design §5 `promote:`, §6 *Promote*, TD-120). A person's press (`ao promote`, the Inbox row) or the home's policy under `auto: true`; never a session's. *Not:* deploy (a repo's own word for its `run`), release, upgrade. — *proposed* 2026-09-24.
- **reserve** — the usage gate's setting (design §6, TD-100, 2026-09-22): what the person keeps back of a quota window for their own interactive work — a flat percent (`30` of a session window) or a percent per day (`{per_day: 10}` of a weekly one). Per profile, per window label, in the host's `settings.yml`; the gate computes the **line** from it. — *proposed 2026-09-22*
- **line** — the percentage at which the usage gate pauses a profile's unattended sessions: `100 − reserve` for a flat reserve, `100 − per_day × days left` (today counted whole) for a per-day one, so a weekly line rises as the week goes. Shown beside the number on the top bar's chip; a session paused at it carries the *paused · usage* mark, not a state. — *proposed 2026-09-22*
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
2. **tick** — the host agent's loop; a manager's or director's loop is a **round**.
3. **`orchestrate` grant** — renamed **`control`**; `orchestrate` was an alias until TD-107 removed it.
4. **nudge** — retired; it is a **send**. *supervisor* (→ manager, or policies) and *fleet* (→ org)
   retired with it, in prose and UI only.
5. **fleet** — retired, as above.
6. **orc** — only in product, package and command names; never a session or position. Also
   decided: **conductor → director**, and **doorbell** kept.
