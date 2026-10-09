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
  — **decided** 2026-09-19 (Paul), confirmed 2026-09-20 (TD-076, design §4.8 *The names*). Since
  2026-09-30 (TD-247, designed) a manager is **on call** (`on_call` in its definition, `true` by default for the `manager` role and `false` for another role named as manager):
  a seat of design §6 rule 3, filled when a member needs a reading no policy makes — a question
  to it, a permission, a `stalled?` member, one *idle · open work* — and closed when it has acted;
  the lifecycle jobs are the host agent's tick (§6 rules 1–12). A **standing** manager is the
  other shape, rounding on `ao wait`, asked for with `on_call: false`; on call is the default from the
  build (Paul, 2026-10-01).
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
- **designer** — a team's design member: an `unattended` session whose lane is the ledger's `design-first` entries, and any entry blocked by `decision (designer)` (TD-367). It writes the design and the entries the grinders pick, merges nothing on a held path, and shows the person only what needs them — an obvious design is merged and noted, a defensible default is a bounded `steer`, taste is an `ask` with answers (design §4.8 *Role names*, TD-120; decided 2026-09-24, an interactive seat from 2026-09-23 until then).
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
  prose and UI is **decided** 2026-09-16 (Paul) — code variable names keep it. Since TD-210 (2026-09-28, designed) the org is an **aggregate**: the teams each registered repo defines in its own `.agentorc.yml`, with the org file holding only what spans repos or belongs to one install (design §4.9 *The org is an aggregate*).
- **team** — a manager (or the person) plus its workers, defined once and started many times.
  A team may contain teams. — *proposed* (ADR 2026-09-13).
- **project** — a named set of one or more repos that belong together. — *proposed* (ADR
  2026-09-13).
- **flow** — the path a team's work takes through its roles, and the words that describe it, defined
  once and reused by every team that lists it: an ordered list of **stages**, each a role, the lane
  it gives and its **stage brief** (the built-in `td`: design → build → review, then the person, who
  is no stage). A team lists the flows it may run; which one it runs now is a setting the person
  turns, and **Apply** moves the running team onto a switch. Per team, never per entry; nothing keys on it at runtime.
  *Not:* workflow, pipeline. — *proposed* 2026-10-04 (Paul; design §4.9c, TD-307).
- **stage** — one step of a flow: a role, the lane it gives that role's members, and the stage brief
  that tells them the path from that step's side; a team that lists a flow staffs every stage of it.
  A stage is not a phase (*design · grind · review*, derived from claims) and not a state. —
  *proposed* 2026-10-04 (design §4.9c).
- **path set** — a named list of a repo's held paths (`.agentorc.yml` `held: {<name>: […]}`), which a review stage names as the paths it reads (design §4.9c *A review stage any seat may hold*, TD-314). — *proposed* 2026-10-04.
- **verdict** — the word on a reader's answer to a held PR's `ask`: `pass`, `merged` or `findings`, a field of the reply and never read from its text (design §4.9c, TD-314). — *proposed* 2026-10-04.
- **sits out** — what a member is while its team's current flow has no stage for its role but another
  of the team's flows does: not started, or wound down at a switch, its card saying so until a flow
  that uses it is current again. — *proposed* 2026-10-04 (design §4.9c).
- **role** — a skillset preset an agent is started from: brief template, lane shape, grants,
  profile, and a **kind** (`worker`, `seat`, `manager`, `plain`) — the one fact a flow asks of it
  (design §4.9c, TD-307). Nothing keys on it at runtime (invariant 9). Built-in: `grinder`, `hunter`, `designer`, `techlead`, `auditor`, `manager`,
  `plain` (`manager` was `lead` until 2026-09-20, TD-076, and `orchestrator` until 2026-09-17, TD-055 step 2; neither old name resolves, TD-107). A role may carry a display `label:` — what the badge shows; nothing keys on it. *Not:* type. — *proposed*; the `orchestrator` → `lead` rename is **decided**.
- **look** — a message to the person that names screenshots: a builder's question about a
  merged change to a page, for the one thing its checks and the techlead could not settle. A
  `steer` with the default *Works* where the techlead leaned (under *Steering*; it lapses to
  *Works*), an `ask` with *Works* / *Not right* where nobody did (under *Needs you*). On a
  board, a line of kind `look`, only where there is no agentorc mail. Not a `watch`, which is a
  check a session makes again before acting. — *decided* 2026-10-03, Paul (design §4.9b *A UI
  change is verified by its builder*, §4.10 *A look*, TD-290).
- **scratch home** — a host agent and a UI run from a worktree on an `AGENTORC_HOME`, a tmux
  server and a port of their own, where a builder looks at its own UI change and a press is
  safe; never the live home. — *proposed* 2026-10-03 (TD-290; `scripts/look_home.py`, TD-291).
- **seat** — a place in a team definition that one session fills at a time, named by what it
  does rather than by who is in it: the team's `techlead:` seat. A seat is *empty* or *filled*
  — the session in it may end and another be started into it (`ao new --keep-mail`, so the
  questions that were waiting are still there) — and it is never *finished*, since it declares
  nothing and is not counted in a wind-down (design §4.9b). Workers are members, not seats. On
  a card a seat with nobody in it reads **on call** — *empty* and *filled* are what the home's
  tick does to it, *on call* is what a person sees (TD-097). Every seat has a **trigger**: the
  techlead's is a question; an **auditor**'s is a count of merged PRs or a period (TD-098). —
  **decided** 2026-09-21 (asked for by Paul: the word was in use and not defined; *on call* his
  choice the same evening).
- **on call** — what a seat's card says while nobody is in it: grey, nothing for the person to
  do, and the slot says what would make it come. Not a state: the record is `exited` or `closed`.
  — **decided** 2026-09-21 (Paul; *empty* and *available* read as something to do). A manager on
  call reads *on call — comes when a member needs a reading* (TD-247, designed 2026-09-30).
- **auditor** — a role for a seat with a trigger: checks one area (docs, tests) every n merged
  PRs or every so often, files what it finds, and ends. Hunter-shaped unless its brief says
  otherwise. — *proposed* 2026-09-21 (design §4.9b *Seats with a trigger*, TD-098).
- **grinder** — a role: resolves each lane item to a merged PR. — *proposed*.
- **hunter** — a role: finds problems and files them with evidence, never fixes them. —
  *proposed*.
- **brief** — the job description a session is started with, written from its role's template. The template holds agentorc's mechanics and ships with the package; a repo's brief is a **supplement** filled into the template's `{repo}` slot (design §4.8, TD-114), never a replacement.
  Describes the job, not the run (TD-042). — *proposed*.
- **start context** — text a session holds from its start that is not a prompt: no turn runs for it, nothing is typed, and the composer does not hold it (design §4.3 `start_context`, TD-283 part 2, built 2026-10-04). Carried by the adapter, kept on the record and the launch record, read on Focus under **Told at start**. *Not:* a brief, which is sent as the opening prompt of an unattended run; nor the person's words, which stay in the composer.
- **lane** — the list of references a worker was handed, or `free-pick`. — *proposed*. A lane word matches a ledger entry by its header (design §6 rule 6); an `owner:<word>` in the lane narrows it to entries whose `Owner:` is that word or absent (TD-214). A `design-first` lane also takes an entry of any kind blocked by `decision (designer)`, the decision's holder standing as its owner (TD-367).
- **pickable** — of a ledger entry: not blocked, which is derived and never written (design §4.4 *Repo facts*, cadence §2.4, TD-223): no `Blocked by:`, or every entry it names archived and no decision named. Whether a given worker may take it is its lane's kind and owner words. *Not:* the `**Pickable:**` header line, retired. — *proposed* 2026-09-28.
- **for you** — of a ledger entry, the page's kind for what waits on the person: `Owner: paul`, or blocked by `decision (paul)` (design §4.4 *Repo facts*, TD-367). *Not:* a decision a session owes — the designer's alone makes the entry *design*, and the anchor's alone makes an entry of no other kind an *evaluation* (beside an open id, either is *blocked*) (TD-418; *design-first* and *other* until then). — *proposed* 2026-10-07.
- **blocked** — of a ledger entry, the page's kind for one whose `Blocked by:` holds anything but a `decision (…)` — an open id, a cross-repo or unreadable one — whatever its `Kind:` (design §4.4 *Repo facts*, TD-418). It is tested right after *for you*, so a designed entry waiting on its build is blocked, not the designer's. *Not:* not pickable, which a decision alone also makes. — *proposed* 2026-10-08.
- **live check** — as a page kind: an entry with `Kind: live-check`, its build live or not (design §4.4 *Repo facts*, TD-418); a live one is still taken by a `free-pick` lane and listed among `ao repo`'s pickable rows (§4.9b). — *proposed* 2026-10-08.
- **waiting** — the pill of an `idle` session that waits on someone: it holds a claim whose PR is open, or has an open `ask` or `steer` to the person whose `about` names a reference (design §4.2 *Waiting*, §4.5, TD-418). Read on the view; the state stays `idle`. *Not:* `ao wait`, which holds a turn open, so that session reads `working`. — *proposed* 2026-10-08.
- **own-lane count** — on a member's card, inside its state pill: what `lane_matches` takes for its own `lane` that no live record holds, `n/k` where `k` live records of the team carry the same set of lane words (design §4.4 *In a team's lanes*, §4.5a *card: compact*, TD-418). Rule 6's count, so it may differ from the kind bar. — *proposed* 2026-10-08.
- **snooze** — of an Inbox row or a board item: the date it comes back on, and nothing more — the row keeps every control its kind has in the Inbox's *Snoozed* fold, and a press there answers (design §4.10 *Snooze*, TD-371). *Not:* a hiding place; **Unsnooze**, retired 2026-10-07. — *decided* 2026-10-07 (Paul).
- **in a team's lanes** — of an open entry: taken by §6 rule 6's `lane_matches` for the `lane` of some record of the team (design §4.4 *In a team's lanes*, TD-357). The Repo page's lanes line, the team card's lanes *i* (TD-418) and `ao repo`'s first line count it beside the repo's *pickable*, whose rest is counted by owner; *pickable* stays the repo's word, this the team's. — *proposed* 2026-10-06.
- **over its line** — of a team: one of its balance lines is crossed (design §6 *Balance*, TD-177) — more open pull requests than `n`, the oldest open longer than `d`, or the reader's queue past its bound — so its unattended members take no new claim until it clears. *Not:* the usage gate's line, which is a profile's and pauses. — *proposed* 2026-09-29.
- **work waiting** — a wound-down team whose lanes gained entries since its members declared (design §6 rule 8, TD-214): the home's `work_waiting`, the home's own start when `teams.<team>.on_work` is `start` or absent, an Inbox row under `ask`; a team that runs on has work waiting for its finished members alone (TD-466). — *proposed* 2026-09-28.
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
- **transcript** — the tool's own record of one conversation (Claude Code: one JSON object per
  line under `~/.claude/projects/`), located and rendered by the adapter (design §4.3
  `transcript_path`, `read_transcript`), read on the Transcript page and by `ao transcript` (§4.5
  screen 9) and never copied off the host that holds it. Not the run log, which is the pane's output.
- **anchor session** — the first agent in a checkout; every other concurrent agent works in a
  worktree (invariant 2). — *proposed*.
- **anchor seat** — a team's seat on call in its home repo's main checkout, present by default, filled when the lane `anchor` gains work and never while a person's session holds the checkout (design §4.9b *The anchor seat*, TD-381). — *proposed* 2026-10-07 (the designer).
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
- **early** — of a wanted restart: declared inside `RESTART_EARLY` of the run's start with nothing new reported `done` (design §4.9a *Inside the ceiling*, TD-245; before TD-249 lands, the thirty minutes alone), or repeating what its last run reported or claimed and left (`repeat`): nobody acts on it, the person does. A short run with new merged work is not early. — *proposed* 2026-09-30.
- **restart** (a person's) — the press that puts a supervised member back into its team's run: closed if still there, replayed from its launch record unattended, its restart marks and count cleared (design §6 rule 2 *A person's restart*, TD-246). *Not:* Resume, which brings a session back attended under the person. — *proposed* 2026-09-30.
- **finished** — of a member: it declared *out of work*, is idle, exited or closed, and is not *waiting* (TD-271) (design §4.9a *Finished means declared, not gone*). Of a team: the home's reading that every unattended live member that is not a seat and not the manager is finished, no restart is wanted, the seats are idle or gone and the manager is idle (design §6 rule 9, TD-240) — read from the records each tick, never a manager's word. — *proposed* 2026-09-29.
- **concluded** — the team card's word for a live team the home reads as finished: its sessions are still there, idle, and its one control is Start (design §4.5a *team groups*, §4.9a *A person's Start on a concluded team*). *Not:* wound down, which is a team with nothing live. — *proposed* 2026-09-29.
- **waiting** — of a live member: the person inbox holds an open `ask` or `steer` from it that names a reference; it is not finished, whatever it declared, until the answer or the bound (design §4.9a *Waiting is read, never declared*, TD-271). Read from the person inbox each tick, never declared and never a state. *Not:* out of work, the member's own word about its lane, which may stand beside it. — *proposed* 2026-10-02.
- **out of work** — a session's own declaration that its role's test finds nothing left
  (`out_of_work`, design §4.9a). **Wind down** — a manager stopping its team after every member
  declared it. — *proposed*.

## Reporting and mail

- **report** — what a session declares about its work on its own record: **progress** (per
  reference) and **findings**. Addressed to nobody. — *proposed* (design §4.8).
- **message** (**mail**) — an attributed entry delivered to a recipient's **inbox**, never typed
  into a pane (design §4.10). Kinds: `note`, `ask`, `steer`, `reply` (an `ask` to two or more controllers with `--cites` is what `conflict` was until TD-462). The sender keeps its own
  copy in its **outbox**, which is where the marks it must see live. — *proposed*; built 2026-09-17
  (TD-052 step 1).
- **orphaned question** — an open `ask` or `steer` to the person whose `about` names a reference
  and whose asker's record was closed, forgotten or cancelled: it stays in the Inbox, a `steer`
  among them waits on the person from its bound instead of lapsing, and the person's answer is
  written on the repo's board and mailed to whoever holds the reference. The asker's name coming
  back adopts it. A question with no reference closes `asker_gone`. — *designed* 2026-09-28
  (design §4.10 *A question about a reference outlives its asker*, TD-213; the build is TD-215, TD-216).
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
- **work order** — a board line the person has decided (`Decided:` on it), listed by the repo reading
  as `board:<key>` in the `free-pick` lanes of the repo's teams until a session closes it (design §4.4
  *Board write-back*, TD-380). — *proposed* 2026-10-07 (the designer).
- **ledger** — `docs/technical_debt.md`: known issues and deferred work as `TD-NNN` entries. —
  *proposed*.
- **Add entry** — the form by which the person puts an entry in the ledger from a page: their
  words and a Type, then **Hand to the techlead** (mail that fills the seat, which drafts the
  entry and lands it by PR) or **Open a session** (an interactive session with the words in its
  composer, not sent). The page writes no file (design §4.9 *Add an entry to the ledger*).
  *Not:* drafter, as a role or a seat: the techlead seat drafts. — *proposed 2026-09-28*.
- **rollup** — the row of four facets under the Org's title: Agents, TDs in motion, PRs in motion, Needs you, summed over every live team (design §4.5 screen 1). — *proposed 2026-09-26*.
- **summary (team card)** — the three facets between a team's header and its members: Repo, TDs in motion, Answer needed / Doing (design §4.5 screen 1). *Not:* dashboard. — *proposed 2026-09-26*.
- **TDs in motion** — the ledger entries a team's members hold, each with its **phase**: *add* (a hunter filing it), *design* (a designer holds a design-first entry), *grind* (a claim, no PR), *review* (a claim with an open PR); derived from the records, never declared. — *proposed 2026-09-26*.
- **answer needed** — a member waiting on a permission or a question standing in its pane: what stops a session now; the team card's facet of that name. *Not:* needs you, which is the wider word (the Inbox's section, the rollup's facet: answers, board items, states stopped on a person); not asked you, a question in the mail. — *proposed 2026-09-26; prompts alone since 2026-10-06 (TD-353)*.
- **asked you** — a team's line for its members' open `ask`s to the person — *asked you · n · <age>* on the team card's Answer needed / Doing facet and on the rollup, linking the Inbox row; a question that waits in the mail, never counted as answer needed (design §4.5a *team card: Answer needed / Doing*). — *proposed 2026-10-06 (TD-353)*.
- **doing log** — the last fifty `ao doing` calls of a team, with time and caller, kept by the host agent for the Doing feed; the record itself keeps only the latest line (design §4.8). — *proposed 2026-09-26*.
- **Repo page** — `/repo/<name>`: the servicing team's three facets, then the lists behind the numbers, one repo at a time (design §4.5 screen 11). *Not:* dashboard — no history beyond the window. — *proposed 2026-09-25, reshaped 2026-09-26*.
- **run log** — a session's recorded pane output from its first byte (invariant 3). — *proposed*.
- **run window** — the hours unattended sessions may work (design §6). — *proposed*.
- **setting** — a value the person turns without redefining anything: a reserve, a team's stop time or priority, a schedule, the promote's `auto`, the person's own editor and terminal. Every setting lives in the home's `settings.yml` (design §5), written only through `set_settings`, and the Settings page (§4.5 screen 8) is where it is turned. *Not:* definition, option, config. — *proposed* 2026-09-25 (Paul's line).
- **definition** — what a team, a repo, a host or a profile *is*: `org.yml`, `.agentorc.yml`, `hosts.yml`, `profiles.yml`, edited by hand or by PR and shown read-only on the Settings page with where each comes from. *Not:* setting. — *proposed* 2026-09-25.
- **start time** — the instant a `scheduled` record becomes a session (design §6 *Start time*, TD-026): set at `ao new --at` or on the New session form, moved by `ao at`; the stop time's twin. *Not:* schedule (a team's standing rule), start_at (the field). — *proposed* 2026-09-25.
- **schedule** — a person's standing press to start a team, kept in `settings.yml` under `teams.<team>.schedule` (design §6 *Schedule*, TD-026): today one shape, *at the reset* of a profile's window, carried out as a replay of the team's last start. *Not:* cron, timer, run window (the hours a schedule keeps a team inside). — *proposed* 2026-09-24.
- **promote** — make a repo's `main` the live copy: for this repo, install the checkout into the live venv and restart the units (CLAUDE.md); for any repo, its `promote.run` (design §5 `promote:`, §6 *Promote*, TD-120). A person's press (`ao promote`, the Inbox row) or the home's policy under `auto: true`; never a session's. *Not:* deploy (a repo's own word for its `run`), release, upgrade. — *proposed* 2026-09-24.
- **rollback** — a person's promote of a commit of `main` older than what is live (`ao promote --sha`, `--back`; design §6 *A rollback*, TD-212). It leaves a **hold**: the automatic promote starts nothing for that repo until the person promotes again or dismisses it. *Not:* revert (a commit on `main` that undoes another; the cure a rollback buys time for), downgrade. — *proposed* 2026-09-28.
- **reserve** — the usage gate's setting (design §6, TD-100, 2026-09-22): what the person keeps back of a quota window for their own interactive work — a flat percent (`30` of a session window) or a percent per day (`{per_day: 10}` of a weekly one). Per profile, per window label, in the home's `settings.yml` (replicated to nodes, §4.4a); a team may add its own **reserve priority** on top (§6). The gate computes the **line** from it. On a **metered** profile the reserve is an **amount** per window — money (`$5`) with prices, tokens (`2M tok`) without — that is the window's 100, read against the account's spend (design §4.2a, §6; 2026-09-25). — *proposed 2026-09-22*
- **line** — the percentage at which the usage gate pauses a profile's unattended sessions: `100 − reserve` for a flat reserve, `100 − per_day × days left` (today counted whole) for a per-day one, so a weekly line rises as the week goes; on a metered window `100 − team priority`, the amount itself for a profile's plain sessions. Shown beside the number on the top bar's chip; a session paused at it carries the *paused · usage* mark, not a state. — *proposed 2026-09-22*
- **metered** — a profile billed per token: `billing: metered` in `profiles.yml` (design §4.2a, TD-128) — an API key, a hosted open-weights model, a second adapter billed the same way — with the profile's own prices per million tokens by kind, or none for a self-hosted model. Its reading is the account's spend, summed by the home over `day`, `week` and `month` (§4.4), never a poll; its bound is an amount reserve (§6). *Not:* API profile, pay-as-you-go, budget (the word for the wake budget, §4.10). — *proposed* 2026-09-25 (the designer, #547; reconciled with Paul).
- **command session** — a session running a predefined shell command. *Not:* command run (as a
  noun). — **decided** 2026-09-16 (Paul).
- *run N* — **retired** 2026-09-16 (Paul). One start of a team or brief gets no name and no
  number: say when it started ("started 09:00"). TD-042 already keeps numbers out of briefs; older
  board and ledger entries keep theirs.

---
- **rail** — the Inbox's left column of filters: the sections, the teams and the kinds as
  toggles, each with its count, and the find box (design §4.5 screen 6 *The rail*, TD-129). On a
  phone it is a chip row and a sheet. *Not:* sidebar, facets, tree. — *proposed* 2026-09-24
  (Paul's shape).

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
