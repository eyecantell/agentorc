## 6. Policies (the tdgrind supervisor, generalized)

Each runs on the host agent's tick, per repo, only for sessions whose record says
`unattended: true` (set at start by New session's Role pick, or flipped later by the mode toggle —
on the Focus header, and in a card's *more*; interactive sessions are exempt from gates). Mode is a
field on the session record, never re-derived from the brief or the name, and a flip takes effect
on the next tick without restarting the session. The brief file is required only when a *policy*
starts a worker; a session flipped to unattended keeps whatever it was doing. Policies key on
`unattended`, `supervised` (*Keeping a team running*, below) and the session's schedule, never on
its role preset or its grants (§4.8, §9 invariant 9): a manager session left running past the
window is wrapped up like any worker, and a plain session with a stop time is stopped like any
worker. A policy that starts a worker names the preset and lane it starts it with
(`workers: [{role: grinder, lane: free-pick}, …]`, §4.8's presets); the schedule stays on the
block. A policy is agent code and needs no grant; a session doing the same work does.

- **Stop time** (`run_until`): a session may carry the instant it must stop, set at start
  (`ao new --unattended --until 06:00 | +8h | <ISO>`) or after it (`ao until <session> <when>`,
  `--clear`; the Focus header's **stops** note, §4.5a), shown by `ao status -v` as *stops 06:00* in
  the reader's own local time. At the instant, the host agent sends the wrap-up prompt **once** and
  then kills the session when it settles or ten minutes later, whichever comes first — the same two
  steps, and the same words, as `ao team stop`. The wording travels on the record, since
  `sessionorc` must not know what a brief is. A session sitting on a permission or a question at
  its stop time is stopped without being asked: typing at it would answer the dialog rather than
  reach the composer (which is why `send` refuses too), and nobody is coming to answer it. This is
  the general form the rest of this section's schedules reduce to: the run window sets a stop time
  rather than being a second mechanism. Setting one on an interactive session is refused rather
  than stored: policies leave those alone (§4.2). The card and the Focus header show it, and the
  New session form takes one (§4.5a). Not built: window overrides with an expiry, and
  calendar-shaped schedules (the one start rule designed is *Schedule*, below).
- **Start time** (`start_at`): the stop time's twin. `ao new --unattended --at 20:00 | +2h | <ISO>`
  (and the New session form's **At** field, §4.5a) creates the **record now** — the name taken
  under §4.1's rule, the worktree made, the launch record written (`launch/<id>.json`, the same one
  a restart replays, *Keeping a team running*) — in the state **`scheduled`**: no pane, no run log
  yet, the directory's agent slot held from this moment (§9 invariant 2: a second session there is
  refused as if it were live), and `supervised` as any supervised create. At the instant the home's
  tick creates the session from the launch record (`restarts: [{why: start}]`, under
  `RESTART_CEILING` as any replay is, a failed create counted and shown), and the record goes on as
  any session's; a `scheduled` record on a node whose link is down waits and is looked at again on
  the next tick, as a restart does. Before the instant, `ao at <session> <when>` moves it,
  `ao at <session> now` starts it at once, and **Cancel** (§4.5a; `ao close` on a scheduled record)
  forgets the record — nothing ran, so there is nothing to keep — and frees the slot; **Kill** on a
  scheduled record is the same Cancel (so `ao team stop --now` stops a member not yet started), and
  **Switch to interactive** is refused until it runs, since the start is a policy's act. `--at`
  with `--until` gives a stop time after the start, parsed together and refused when the stop is
  not after the start. **Refused**: `--at` without `--unattended` (a start nobody is at the keyboard
  for is unattended by definition; §9 invariant 5 keeps a policy's act off an interactive session),
  an instant in the past (say `now`), and a start time on a live session (`ao at` is for
  `scheduled` records). It is the per-session form of *Schedule*, below: a person's press with a
  clock on it, replayed from a launch record, never a definition re-read. The card and the Focus
  header show it as the **starts** note (§4.5a), `ao status -v` prints *starts 20:00* by the same
  formatter as *stops*, and the New session form takes it. In the agent: the record carries
  `start_at` (home-owned, as `run_until` is) and ranks between idle and exited; `create` with
  `start_at` writes it, and the tick's replay passes `start_of`, naming the record it starts, so the
  create supersedes it in place — its mail moved to the session — rather than refusing it as the
  live holder of its name and slot. The usage gate and the stop time pass a scheduled record by: it
  has nothing running to pause or stop. Starting a scheduled record supersedes it and takes its
  mailbox, so `start_of` is an act on that record: a person's, or one of its controllers' — as
  `keep_mail` is (§4.9b, §9 invariant 11); the tick's own start carries no caller. At the restart
  ceiling the record is a person's: a person's new time (`ao at`) spends the ceiling and the failed
  starts' count, while a controller's is refused, naming the ceiling, and below it moves the time
  and leaves the count.
- **Keeping a team running**: rules that lived in the manager's brief, applied by a model every
  round, are policies of the host agent's tick; the manager preset is silent on them. **Scope: a
  session is *supervised* when its record says `unattended: true` and `supervised: true`.**
  `supervised` is a home-owned intent field (§4.4a), set by `ao team start` on **every session it
  creates — the manager, the seats and the members alike** — and by `ao new --supervised`, and by
  nothing else; a person's New session form does not offer it (not built). It says *someone chose
  to keep this session running*, which is the whole of what the rules act on. **It is cleared by
  nothing but Forget**: it is inert while the session is interactive (below) and live again when a
  person hands it back, and every Resume carries it as it carries `controllers` — the one-press
  Resume starts an attended session, where it is inert until a person flips the mode; *Resume with
  changes…* carries it with *Unattended*. So a manager that crashes is restarted as a member is
  (rule 1), and a manager that ends a wind-down with `ao close` on itself (§4.9a) has closed, not
  crashed. Nothing keys on a role, a team badge or a controller (§9 invariant 9): a
  `manager: person` team's members are supervised exactly as a manager's are. An interactive
  session is never supervised (§9 invariant 5: **Take over** on Focus takes a member out of these
  rules on the next tick, and **Hand back** returns it); a suspended record never is (§4.8a).
  **A restart is not a start.** The host agent still starts nothing *new* by itself, and what it
  starts again without a press at the time is a person's standing word — a schedule, or rule 8's
  `on_work: start`, which starts the team, or one member of a team that runs on; a schedule is a
  person's to turn on and is off by default. A restart re-creates a session the person or
  `ao team start` already chose to run — same name, directory, worktree, profile, brief, lane,
  role, badges and `controllers` — and supersedes its record in place (§4.1), so nothing appears on
  the Org that a person did not put there. It replays the session's **launch record**: at every
  create whose record carries `supervised` — attended or not, so a *Resume with changes…* that
  leaves *Unattended* off still writes one and a later Hand back replays the person's latest
  choices — the host agent writes `launch/<id>.json` under its home — the adapter, the profile, the
  prompt as handed (placeholders filled) and the files and texts it was made from (`prompt_from`,
  rule 7), the lane and the fields above — and a restart is that record handed to `create` again,
  its prompt filled afresh from `prompt_from`, never the definition re-read (the host agent does not
  read `org.yml`, §4.9). The launch record is deleted with the record on Forget and kept across a
  supersede. **A replay that fails** — the worktree reaped, the profile gone, the name taken by a
  live session — is not retried silently: it counts toward the ceiling as any restart does, keeping
  its `why` and carrying the error text as `error` on the entry, so an unrepairable record reaches
  the Inbox row within three ticks rather than never. **Each supervised session's pass is
  isolated**: one session's exception is logged and the tick goes on to the next (the stop-time
  policy guards only its send today). **The restarts run at the home** (§4.4a: policies that start
  run at the home), so a member on an unreachable host is left as it is until its link returns —
  refused, not queued, looked at again on the next tick; the nudge and the seat close run at the
  home and execute on the member's node as any act does. A policy needs no grant and passes no
  gate; it acts on the record's own fields and never on text a session wrote. The rules:
  1. **Crash restart.** A supervised member **other than a seat** (a seat's ending is its own,
     and rule 3 is the only rule that fills one) that is `exited` by a **natural exit** — `pane` true,
     the tool left on its own — with **no declaration** (`out_of_work` and `restart_wanted` both
     absent), **no wrap-up asked** (`wrapup_at` and `wrapup_sent_at` empty), **no stop time
     passed**, **not gated** (§6 *Usage gate*: a paused profile is not restarted into a pause)
     and **not suspended** is restarted on the next tick — but not within `RESTART_SETTLE` (60 s)
     of a restart of its own that succeeded, and an exit event carrying another tool session id
     than the record's is the end of the run a restart replaced, ignored whole. A kill (`pane`
     false) is a person's or a controller's act and is never undone; an exit after a wrap-up is an
     ending. **The ceiling**: `RESTART_CEILING` — three counted restarts of one session in two
     hours (a wanted restart with new work is not counted: §4.9a *Inside the ceiling*;
     `agent_common._counted`), `one_for_one` (only the session that exited, never its siblings).
     Each restart is appended to the record's `restarts: [{at, why}]` (home-owned, carried across
     the supersede so the count survives the restart it counts; each replay's entry also carries
     `done: [{ref, pr}]` and `left: [ref]`, what the run it replaced reported and what it claimed
     and did not close, from the old record's `progress`, which the new record does not keep);
     **the mail is kept**: every replay of the tick's — this rule's, rule 2's, rule 7's and
     rule 8's — is a `create` with `keep_mail` (§4.9b), so the closed run's inbox and outbox move
     to the new record and a reply to an `ask` the run before sent — a reader's to a held PR's,
     rule 11 — lands on a thread the new record holds; `why` is `crash`, `wanted` or `fill` (and
     `start`, `schedule`, rule 7's `brief` and rule 8's `work`, each named where it is written),
     and a replay that failed keeps its `why` and adds `error` (the text), so a failed entry still
     says what it was trying. At the ceiling the policy stops, writes `restart_ceiling: {at, count}`
     on the record, and the session is a person's: the card's slot says *restarts exhausted · 3 in
     2 h* as an ending (§4.5 row 5 (b)) and the Inbox lists it under *Needs you* (§4.5a **Inbox
     row: restart**). The host agent writes no board line (§4.4's write-back acts at a person's
     press and never on a session's behalf); the Inbox row is the person's channel, and a manager
     reads the field.
  2. **Wanted restart.** A supervised member carrying `restart_wanted` (§4.9a) that is `idle`, or
     `exited` by a natural exit (`pane` true) — a kill or a Close, a person's or the stop time's,
     is never undone, as in rule 1 — with **no stop time passed**, **not `early`**, not suspended,
     **not gated**, with **nothing uncommitted and nothing unpushed** on its git fields (known, not
     merely absent: an unknown git state is left alone) is closed if it is still there — the one
     close a policy makes outside a wrap-up — and restarted as rule 1 does, under the same ceiling
     (`why: wanted`); a close or replay that failed keeps its `wanted` entry with `error` and is
     tried again by the tick. The tick writes `closed_for: {why: wanted, closed_at}` (rule 7's
     `brief`) on the record after its own close, naming the `closed_at` that close wrote — a node's
     own stamp for a node's member, taken from the close's reply. Any close the home runs clears
     it, the local one and one routed to a node; a person's Close made at the node, which never
     crosses as an act, writes a new `closed_at` that no longer matches. So a `closed` record is
     the tick's to retry only when it carries the mark, the mark names its `closed_at`, and its
     last entry carries the `error`; any other is a Close by someone else. One failed close is not
     retried: a close routed to a node whose verdict never came back (the link dropped, or no
     answer in time) leaves no mark, so if the node did close it the record reads as someone
     else's Close and waits for a person — a stranded restart, never an undone Close.
     With work left it is **not** restarted: one send of fixed text naming what is left (the dirty
     files' count and the unpushed count, from the record, never a session's words), once
     (`restart_blocked_sent_at`) — typed only into an idle member's empty composer on the home's
     own host, as rule 4's line is; an exited one, or a node's, gets no line and its clock runs from
     the declaration — and if the git fields still show work after `IDLE_NUDGE` the record carries
     `restart_blocked: {at, dirty, unpushed}` and the same Inbox row lists it. The tick keeps
     looking: the moment the git fields read clean and pushed — a person or a sibling pushed — the
     restart runs and clears the mark itself, so `restart_blocked` is transient where
     `restart_ceiling` is not: the ceiling stands until a person's Resume or Forget, or until the
     window holds fewer than `RESTART_CEILING` counted restarts and the member declares a clean
     `restart_wanted`, which rule 2 then acts on — the ceiling guards against a crash loop, not a
     member that has worked for hours since — and rule 1 never restarts past it; a person's Resume
     clears `restarts` with the mark, so a resumed session gets three fresh restarts, where the
     tick's own supersede carries the list. An `early` one is the Inbox row at once, as §4.9a says:
     a controller does not act on it, and neither does the tick.
     **A person's restart.** What the tick would not do, a person can say in one press, and the
     home then does it the tick's way: the **`restart`** RPC — a person's alone, as `set_settings`
     is (§5; no controller gains it: an `early` or a `repeat` one is the person's by §4.9a, and a
     manager that could restart its members past the marks would be the loop the marks catch) —
     on a supervised record that has a launch record and is `idle`, `exited` or `closed`, **closes
     it if it is still there under the same test the tick's own close makes** — the git fields
     known, nothing uncommitted and nothing unpushed; else refused by name with what is left (*git
     state unknown*, *2 uncommitted*, *1 unpushed*), as a wanted restart is held — and **replays it
     from its launch record** as this rule replays one: the same name, directory, worktree,
     profile, role, team, lane, brief and start context (§4.3), `unattended` and `supervised` as the
     launch record says whatever the old record had become (a member a person took over with `ao
     mode` comes back unattended when its launch record says so: a restart is the team's run
     resumed, where Resume is the person's own), the prompt as rule 7's replay hands it — refilled
     from the files it was made from, as merged, on the home's own host; the stored prompt for a
     node's member or a launch record with no `prompt_from` — and the mail kept: the create moves
     the old record's mail to the new one, as a seat's fill does (`keep_mail`, §4.9b). What it
     asked the person follows the close, not the mail: an `idle` member is closed first, and a
     close ends the open `ask`s and `steer`s it put to the person (`asker_gone`, §4.10; one about a
     ledger id or a PR is orphaned instead, and the new record, holding the name, adopts it), while
     an `exited` or `closed` one is not closed again, so an exited member's open questions stand
     and their answers reach the new record. **The new record starts fresh** otherwise: no
     `out_of_work`, `progress`, `doing` or `lane_seen` of the old run. It **clears the marks the
     person is answering** — `restart_wanted`, `restart_ceiling`, `restart_blocked` — and, as a
     person's Resume does, **clears `restarts`**: the new list holds one entry, `{at, why:
     person}`, so a restarted member has three fresh restarts and the press never counts toward
     the ceiling it lifts (not `_replay`'s append: the same create, a list of one). Refused, each
     by name: a record with no launch record (*Resume with changes…* is the way back, since
     nothing says how it was started), a `working` or `needs-you` one (wait, or Wrap up), a
     suspended one (§4.8a), a `scheduled` one (`ao at`), a seat (rule 3 fills it), a record whose
     name is held by another live record, one whose launch record's stop time has passed (*its
     stop time has passed — Resume with changes…*), and one whose profile is over its usage line
     (the gate would pause what the press started). Every refusal is made before anything is
     touched. A create that fails after the close is told to the person as the press's answer:
     the record stays `closed` with its marks, so the row stands and the press can be made again.
     A member on a node is closed and created over the link as rule 1's replay is, and waits for
     the link. **Where it is pressed**: **Restart** on the Inbox row *restart* beside Resume
     (§4.5a, with the help saying which is which: *Restart puts it back in its team's run as its
     launch record started it, on a fresh prompt; Resume brings it back attended, under you*) —
     drawn only for a record that is not a seat, so the *fills exhausted* row keeps Open, Resume
     and Dismiss alone, and on a `restart_blocked` row it answers with what is left — on the
     card's **more ▾** for a supervised `idle`, `exited` or `closed` record that is not a seat, and
     `ao restart <session>` (§4.7). **A manager's board line names the control and never asks for
     words**: *the Inbox row's Restart, or `ao restart <id>`* is what it writes beside a member it
     puts on the board, and the press is the person's. Restart, not *Resume with changes…*, is a
     supervised member's way back into its team's run (§4.9a *One member back, today*).
  3. **Seats.** `ao team start` writes each seat's trigger on its record as **`seat: {trigger}`**
     (home-owned, set at create like `review`, §4.9b), and the tick computes **`seat_due: {at,
     by}`** from it — set only once the trigger is met, `by` naming what met it (`asks`, `prs`,
     `every`, `work`), and kept until the fill: for `asks`, when `asks_waiting` leaves zero (and
     cleared again if it returns to zero before a fill — the question was answered elsewhere);
     for `prs: n`, when **`seat_count: {prs, at}`** (home-owned) reaches `n` — the PRs merged to
     the seat's repo's default branch since the seat's record was created, read with `gh` on the
     reports' five-minute cadence, one read per repo, at the home in the seat's checkout (a
     node's is at the same absolute path, §4.4a; a path the home cannot see gives no reading), a
     read that failed leaving the last reading rather than zero; for `every: <d>`, when `d` has
     passed since it was created. A supervised seat that is `exited` or `closed` with `seat_due`
     set, not suspended, on a profile that is not gated, is filled: `create` with `keep_mail`
     (§4.9b), the launch record, and `seat_due` cleared; a seat that is `idle` with no `seat_due`,
     hook-confirmed for two minutes (`SEAT_IDLE_GRACE`, so a fill is not closed before its prompt
     lands), with nothing dirty or unpushed, is closed (a seat that left work is the board's) —
     **unless it waits on a PR of its own**: an open PR on the branch it has checked out, as the
     repo reading has it; a claim on its record carrying a PR not read merged or closed (`pr`, or
     `review_pr`); or a claim derived from that branch whose PR no reading has yet — a close would
     kill a fact-check or CI it left running and leave the PR with nobody. A PR it handed to a
     reader — an `ask` carrying it, open in any record's inbox — is the reader's, and no longer
     holds it; and the wait is bounded: idle `SEAT_PR_WAIT` (two hours, `review.bound`'s default),
     it is closed as before, and the PR stands among the repo's open ones. **The fill ceiling**:
     `FILL_CEILING` — six fills an hour over all seats sharing a controller (the graph, not the
     team badge), then `restart_ceiling` (with `why: fill`; the card says *fills exhausted · 6 in
     1 h*) on the seat whose fill tripped it and the Inbox row as for a crash, its siblings merely
     refused fills until the hour rolls; fills are not crash restarts and do not count toward
     `RESTART_CEILING`. The card draws the count toward a `prs:` trigger from `seat_count` (*on
     call — runs after 10 PRs · 4 of 10*).
     **The anchor seat is a seat of this rule, with the trigger `work`** (§4.9b *The anchor
     seat*): `seat_due` is set with `by: work` and the `ids` when the seat's lane reading — the
     lane word `anchor` (rule 6), kept on the seat's record as `lane_seen` as a finished member's
     is, and pruned as rule 6 prunes it (an id the lane no longer matches leaves it, so a live
     check that goes live after the seat saw it as a build is due again) — holds an id it did not
     when the seat last declared or was created, and a cause fills once per stretch as the
     manager's do: the same ids raise no second `seat_due` after a `none`. **Its fill is gated by
     the checkout**: the `create` is in the home repo's main checkout, refused by occupancy (§9
     invariant 2) while any session holds it and refused while the checkout is dirty or off its
     default branch, each leaving `seat_due` standing with the reason on the record — **`seat_held:
     {by, why}`** (home-owned) — for the card's slot (*on call — the checkout is yours · branch
     td-x, 2 files uncommitted*); the next tick tries again. **`ao team start` is gated by the
     same reading**: a Start whose checkout is not free writes the seat's record held — `closed`,
     no pane, `seat_held` with the reason, which stands while its lane holds work due and is
     cleared as any `seat_held` is when nothing is — in place of a pane there, and this rule fills
     it once the checkout is free; the gate is one reading, never two that can disagree (a node's
     checkout: occupancy alone, from its records, on both roads). **A `none` that consumes no
     stretch**: a `work` seat's `none` declared while its checkout is not its own — dirty or off
     its default branch at the declaration, the holder being itself — writes `lane_seen` with no
     ids (`ids: []`, as a held Start's record is written; never `None`, which the next tick fills
     from the reading), so the ids it could not work stand due and fill it again once the checkout
     is clean; the fill ceiling bounds a checkout that keeps turning dirty between the gate and
     the first prompt. Its idle close and its fill ceiling are this rule's as for every seat.
     **A manager on call is a seat of this rule.** `ao team start` writes `seat: {trigger: team}`
     on a manager whose definition does not say `on_call: false` — the `manager` role's default
     alone, another role's manager being a seat only where its definition says `on_call: true`
     (§4.9) — and the tick sets its `seat_due` with `by` one of four words, each a reading of the
     records and never of a screen: **`asks`**, as the techlead's — an open `ask` or `steer`
     addressed to it (`asks_waiting` leaves zero); **`pending`** — a member of its team (a
     supervised record listing it in `controllers`) hook-confirmed `needs-you` with a `permission`
     pending (a question or a menu fills nothing: it is a person's, and the Inbox's state row has
     it); **`stalled`** — a member `stalled?`; **`open`** — a member *idle · open work*:
     `nudged_at` set and the member still hook-confirmed idle `IDLE_NUDGE` after it with the same
     work open, which the tick writes as **`idle_open: {at, ref}`** on the member's record
     (home-owned; a member's alone, never a seat's; `ref` the first open reference when it was
     written, and none where the open work is an outcome owed alone, rule 4's owed clause;
     cleared when its state changes, the work closes or the member declares), the one reading the
     card's slot, this trigger and the person-led team's Inbox row (§4.5a *Inbox row: idle · open
     work*) draw; the page reads it from the record and derives nothing from `nudged_at`.
     `seat_due` carries the cause — `{at, by, member}` — and **a cause fills once per stretch**:
     the home keeps **`seat_filled: [{member, by, at}]`** on the seat's record, an entry dropped
     when its cause has gone (the state changed, the question closed), and a cause still standing
     after a fill raises no second `seat_due` until then, so a manager that chose to leave a
     member as it was is not refilled into the same reading; the fill ceiling guards the rest. **A
     question is a cause by its id**: for `asks` the due and the entry carry `ask`, the question's
     id, in `member`'s place, so a question the manager left standing does not fill it again and
     a second question does — an entry the person handed it excepted, which is never remembered
     and fills the seat while it owes its outcome, as it does any seat (§4.10). A member's cause
     is its word and the member, nothing finer: a second permission raised before a tick saw the
     first one go is the same cause, and waits for a person as a question does; and a question
     waiting on an idle, filled seat holds its due, as the techlead's does, so a member's reading
     waits behind it for rule 4's nudge or the answer. The causes are read in that order —
     questions, then each member's permission, `stalled?`, *idle · open work* — and the first one
     no fill was made for is the due; one whose cause goes before the fill is cleared, as `asks`
     is. **A reading that comes while the seat is filled is the next fill's**: a fill starts cold
     on the one reading its `seat_due` names, so a manager on call that is idle past
     `SEAT_IDLE_GRACE` with a member's reading due — never a question, which an idle seat reads as
     the techlead does — is closed as one with nothing due is, and filled for it on the next
     tick. The fill and the close are this rule's as for any seat — `create` with `keep_mail`, the
     launch record, `restarts: [{why: fill}]`; closed once idle with no `seat_due` for
     `SEAT_IDLE_GRACE` with nothing dirty or unpushed. **A Start writes the seat and never fills
     it**: `ao team start`, and a Start's or an Apply's create of a manager the run lacks (§4.9c),
     writes a manager on call's record as the anchor's is written when its checkout is not free
     (§4.9b: `create` with `held`) — `closed`, no pane, the launch record written, `seat:
     {trigger: team}`, no `seat_held`, since nothing holds it: it is simply not due — and the
     Start's line for it reads *on call — comes when a member needs a reading*, the card's words.
     The team's other records list its id in `controllers` as before: the id is the name's (§4.1)
     and the fill keeps it, so the members' briefs name it before it has ever run, as they name
     the techlead's. The first tick reads its causes as it reads any seat's, so **a Start with
     something already due fills at once** — a question in the mail a Start keeps (`keep_mail`;
     the held create moves the mail as a fill does), a member `needs-you` from the first minute —
     by this rule and no Start-time case: the Start itself is never a cause.
     **A fill says why it came**: the prompt a fill hands `create` is the brief, filled again as
     rule 7 has it, with **one closing line** in fixed words — *[agentorc] you are filled for: <by>
     — <member or the question's id>* — the cause as `seat_due` carries it, so the seat's first
     act is the reading and not `ao status`; `seat_due` on the record stays the truth it reads
     back, and a replay for any other `why` adds no such line. Its `control` grant and its place
     in its members' `controllers` are the record's and survive the close, since a fill supersedes
     the record in place at the same id (§4.1) — which is why a person's session joins the team
     under the seat's id as its controller, live or not (§4.5a New session **Controllers**): a
     member's mail to its controller lands in the closed seat's inbox and waits for the fill, as a
     question to the techlead does — and the mail sweep (§4.10) **spares an `ask` to a closed
     record that carries `seat`**, for every seat: a fill refused by its ceiling, a gate or a down
     link would otherwise turn a question to an empty seat into a lost one on the next sweep.
     **The ceiling's groups**: `FILL_CEILING` is over the seats sharing a controller, so a team's
     techlead and auditors (`controllers: [manager]`) are one group whether the manager is
     standing or on call, the manager seat itself is a group of one (its controller is a
     director's, where there is one), and in a person-led team, whose seats have empty
     `controllers`, every seat is its own group with six fills an hour of its own.
  4. **The idle nudge.** A supervised member that has been hook-confirmed `idle` for `IDLE_NUDGE`
     (twenty minutes) with **open work on its record** — a `lane` reference with no `done` or
     `dropped` entry, a declared `claimed` entry with no `done` or `dropped`, or, for a seat,
     `seat_due` set (a question waiting on an idle techlead) — and no declaration, no pending, not
     gated, its composer empty (§4.2 `send`'s rules; the doorbell's *one typist per pane* holds), is
     sent **one fixed line** through `send`'s path: *[agentorc] you have been idle 20 minutes with
     `<ref>` open — end the run with one of `ao progress done <ref> --pr N`, `ao progress drop
     <ref> --why`, `ao progress none --why` or `ao progress restart --why`*, naming the first open
     reference — for a seat, the number waiting: *you have N questions waiting — run `ao inbox`*,
     and an entry the person handed it (§4.10 *An entry handed to a seat*) named apart, since one
     already read wants its outcome and not another read: *1 entry the person handed you owes its
     outcome — `ao msg person --outcome done|blocked|dropped "…" --for <id>`*. **An outcome the
     member owes** (§4.10 *Outcomes*: the record's `owed()` reading, the `owed:` line `ao status -v`
     prints from `mail.owed`) counts as open work for this rule, seat or not, named apart the same
     way — *you owe 1 outcome on <id> — `ao msg person --outcome done|blocked|dropped "…" --for
     <id>`*, after the open reference where there is one and alone (*you have been idle 20 minutes:
     you owe …*) where there is none; several are counted and named three at most, *and n more*,
     and a seat's clause counts its own answered questions, a handed entry being the part before
     it; the slot's *idle · open work* follows this nudge as it follows any other, a debt being
     work the member left; a finished member is never sent to, and its debt is the Inbox's
     *Waiting on them* and nothing more. The line holds nothing a session wrote; it is recorded on
     `sends` as the home's own (`system`). A node's member is not nudged yet: the composer is read
     on the member's host, and no node act does that. Once per idle stretch (`nudged_at`; a
     stretch ends when the state changes), never a second before the first is answered — the same
     rule as the doorbell's *rung only for new mail*. It spends no wake budget (§4.10: it is the
     host agent's own clock, like a lapse). After the nudge the policy is done: what the member
     does next is its own, and a member still idle another `IDLE_NUDGE` later reads *idle · open
     work* in the slot for a person or its manager to judge.
  **What is left is judgement, and a seat holds it.** Four of the manager's round's jobs read
  fields and scripts and no screen — the cadence check (`check_cadence.py --json` gives a verdict
  per row), the held-path check (a merged PR's files against `held:`, the reader's reply in a
  mailbox the home holds), the relay of `docs/cadence-changes.md` (a file as merged, a list of
  headings) and the chase of an owed outcome (the record's `owed()`) — and they are rules 10, 11
  and 12 below and rule 4's owed clause: each fixed text and a mark, an Inbox row where a second
  failure earns one, none typed by a session. What is left reads a screen or weighs words: an
  `ask` addressed to the manager, a permission on an unattended member (rare, since an unattended
  launch skips them — the Claude Code adapter's `--dangerously-skip-permissions`, §4.8; a profile's
  `unattended_args` may not), a `stalled?` member, one *idle · open work* after the nudge, and the
  prose of an escalation. So **the manager is a seat on call** unless its definition says
  otherwise (`on_call`, §4.9, whose default is the `manager` role's alone), filled by rule 3's
  `team` trigger on exactly those readings and closed when it has acted, as the techlead is; a
  standing manager stays what a definition may ask for (`on_call: false`); and `manager: {role:
  person}` means the readings are the person's — the Inbox's state rows for a permission and a
  `stalled?` member, and the *idle · open work* row (§4.5a), nothing started. **No permission
  allow-list joins the definition**: the tool's own settings are the allow-list, an unattended
  launch passes them all, and what still prompts is by construction what nobody pre-allowed — the
  judgement the seat is for; a second list in `org.yml` would be the same rule in two places, and
  the host agent matching a prompt's text is a policy acting on text (this section's first rule).
  The second reading of the ledger goes with the round: rule 6 tells a dropped lease and an entry
  that became pickable, rule 8 a team that wound down, and the member's own `none --why` names
  what it saw and left. **On call is the default**: a definition with no `on_call` means the seat
  for a manager of the `manager` role (§4.9: the template with a seat's shape is that role's, so
  another role's manager stands unless its definition says `on_call: true`), and every team takes
  the shape at its next Start, since the seat field is written at the create.
  **A fill's first reads are its brief and what its cause names**: the on-call template
  (`manager_on_call.md`, 1,400 words) reads `ao --skill` and its own log, and each of the four
  causes says what to read for it (a tail, a transcript, the member's record), never a section of
  the design whole; a repo's supplement to it names reads for a reading, never for a round, and
  agentorc's own (`docs/briefs/manager-ao-1.md`) keeps its *first reads* — §4.2, §9's invariants,
  the cadence skill — under the readings that need them. The standing manager's brief
  (`manager.md`, 4,200 words, reading §4.8 and §6 whole and cadence §1–§4) is `on_call: false`'s
  alone. The wind-down is rule 9's whichever shape the manager has: a manager on call that is
  closed is not live, so the reading holds without it, the home closes the members and writes the
  announcement, and the seat is not filled for a finished team — every act the manager's last
  round made (the declaration, the note, the board lines) is a row or a note the home already
  writes: an orphaned question, a member left open, a refused start. With the four rules on the
  tick a standing manager's round is the fallback timer alone — `ao wait` on an hour, not ten
  minutes. The briefs carry none of the four rules — a brief is read at team start, so a team
  started before a change keeps the old words until its next start — and the restart ceiling, the
  fill ceiling and the twenty minutes are these constants, never numbers in `manager.md`. The
  built-in manager preset reads the marks and performs no restart, fill or nudge — except the
  nudge to a member on a node, which rule 4 does not reach yet — and its round ends in `ao wait
  --timeout 3540`, run in the background because a tool call is capped at ten minutes.
  5. **Context bound.** A supervised member whose **context reading** (§4.3 `context`, on the
     record as `context: {tokens, at, window}` — the window kept beside the tokens, since the
     model may change mid-run) is over its role's **bound** (§4.8 `context: {bound}`; 300k for
     every built-in worker preset — grinder, hunter, auditor — and for every role that sets none,
     a seat excepted, and a repo's own number under its `.agentorc.yml`, §4.8 *The bound has two
     layers*) is told so **once it is hook-confirmed `idle` and holds no claim in progress** —
     between entries, never mid-turn — by **one fixed line** through `send`'s path, as rule 4's
     is: *[agentorc] context 231k, over your 200k bound — take nothing new: push, ledger, then
     `ao progress restart --why "context bound"`*; `context_sent_at` marks it, and it is sent
     again after twenty minutes if the member is still idle and over. A member that is `working`
     past the bound is not interrupted: every `ao` reply it makes ends with *(context 231k over
     the 200k bound)* beside the unread line (§4.10 *Busy for hours*), and its brief says what that
     means — finish the entry in hand, then declare; for a manager, end the round with the
     declaration in place of `ao wait` (§4.8 *The manager's restart at the bound*). The
     declaration is the member's (§4.9a *A run that ends with work left*, §9 invariant 14) and
     rule 2 restarts it. Ordered as the doorbell is: a wrap-up under way or a gate pause beats
     it, and a member that has declared already (out of work, a restart wanted) or is a seat is
     not sent it; a member on a node is not told yet, as rule 4's is not. The reply clause is read
     at the home: a read a node serves alone carries none. **Not compaction**: Claude Code
     documents no settable auto-compact threshold, no way for another process to send `/compact`,
     and no hook before it, its summary is lossy and keeps merged work, and it is one tool's — so
     the bound restarts, which every adapter can do, and compaction stays what a person types into
     their own session. The reading is drawn whether or not a bound is set (§4.5 *The card's
     anatomy* row 4, `ao status -v`).
  6. **New work in a lane.** A member that declared `out_of_work` is never sent to (§4.9a), and
     filing a ledger entry sends no mail, so the tick tells a finished member that its lane gained
     work. For a supervised member, not a seat, carrying `out_of_work`, the tick keeps
     **`lane_seen: {at, ids}`** (home-owned) — written on the first tick after the declaration as
     the ids of the entries in its repo's ledger reading (§4.4 *Repo facts*) that **match its
     lane**, and cleared with `out_of_work`. **The first write is the ledger as it stood at the
     declaration**: the ledger file as the last commit of `origin/<default>` before
     `out_of_work.at` held it, read from the checkout's history, so an entry merged between the
     declaration and the first tick that looks is new and is told; an entry the checkout's reading
     holds and that ref's tip does not (a branch checked out at the home) is written into it
     untold, so neither that tick nor a later one tells as new an entry the checkout held at the
     first write and `origin/<default>` does not. That is the first write's alone: an entry that
     appears in the checkout's reading only after it (the anchor checking out a ledger branch) is
     told once, as any new id is, though its note's *read the ledger on `origin/main`* will not
     find it there — the ledger read is the checkout's file; where the history cannot be read, the
     reading at that tick. **A lane word matches by the entry's header, never its prose**: with
     pickable derived (§4.4 *Repo facts*), `design-first` is a pickable entry with `Kind:
     design-first` — and, pickable or not, an entry of any kind whose `Blocked by:` names
     `decision (designer)`: the designer claims the entry as any reference, writes the decision
     into the design, and the same PR drops the item from the entry's `Blocked by:`, so the next
     reading finds the entry pickable and tells the grinder lane as it tells any lane news; for
     that match the decision's holder stands as the entry's owner, so `[design-first,
     owner:designer]` takes an entry whose `Owner:` is `grinder`, and a decision named for anyone
     else — `decision (paul)`, `decision (anchor)` — matches no lane: the person's is *for you*
     (§4.4 *Repo facts*), and the anchor's is the lane word `anchor`, below —, and `free-pick` a
     pickable entry whose kind is `build` or unwritten — or `live-check`, **once its build is
     live** (§4.9b *A live check is a grinder's once its build is live*) — and **every open
     decided line of the repo's board**, a work order `board:<key>` (§4.4 *Board write-back*),
     which `owner:<word>` never narrows out, since a line has no owner — and **`anchor`** every
     pickable entry whose `Owner:` is `anchor`, of any kind, and every work order (§4.9b *The
     anchor seat*: the seat's lane, and a person's own anchor session reads the same) — so an
     evaluation, a decision and a live check whose build is not live match no lane, and a ledger
     with no header lines at all gives a `free-pick` lane everything it has unblocked; a lane of
     references gains nothing, and any other lane word matches nothing until a role gives it a
     meaning here. **An `owner:<word>` in a lane narrows it**: with one or more, an entry matches
     when it matches one of the lane's other words and its `Owner:` is one of the owners named or
     is absent, so `[free-pick, owner:grinder]` leaves an entry that is the anchor's or Paul's
     out. The word is the lane's, written where the lane is (`org.yml`, a role's preset), and is
     compared with the ledger's line alone: a session's role badge is not read (§9 invariant 9). A
     lane with no owner word matches as it did. An owner word is **not a reference and not a lane
     of its own**: it is set aside before a lane is checked as `free-pick` or a list of references
     (so `[free-pick, owner:grinder]` is accepted where `free-pick` beside a reference is refused),
     and it is never counted, nudged about or offered to `ao progress` as something held; the
     brief's `{lane}` slot leaves it out (and the count and the nudge leave `design-first` out as
     they leave `free-pick`), and the lane as written (the card, `ao team list`) shows it. When a
     later reading holds a matching id that `lane_seen` does not — an entry filed since, or one
     that has become pickable, its blocker archived or its decision made — and the member is
     **live** (not `exited`, not `closed`), with no wrap-up asked, no stop time passed, not gated
     and not suspended, the home delivers **one `note` from `system`** into its inbox: *your lane
     gained n entries since you declared out of work: TD-180, TD-181, TD-183 — read the ledger on
     `origin/main`, then claim one or declare again* — the ids from the reading, five at most and
     *and n more*, nothing a session wrote — and adds them to `lane_seen`, so an entry is told
     once. **`lane_seen` is the lane's memory, never the ledger's**: on every reading, an id in it
     that the reading holds and the lane no longer matches is removed, its drop mark under
     `dropped` removed with it, for a live member and a gone one alike, so an entry that leaves
     the lane and comes back is news once more — a build that became a live check and then went
     live, a `Blocked by` that reopened and cleared, an `Owner:` that moved away and back; an id
     the reading does not hold is kept, since an entry absent from the checkout's file is archived
     or the checkout's moment, not the lane's. The reading is one function (`sessionorc.work`),
     the memory pruned and the news read in one pass, so rule 6, rule 8 and the anchor seat's
     `work` trigger (rule 3) never disagree on what is new; it keys on the id alone, since
     *matches the lane now and did not when last seen* is what being news means, and no kind or
     workability is written beside the id. The promote tells nobody itself: it is one of the ways
     an entry comes back, and the reading sees them all. It is mail, so the doorbell is what
     wakes the member, under every rule the doorbell has (§4.10: hook-confirmed idle, an empty
     composer, a pending stop beats it, **one unit of the wake budget**), and a node's member is
     reached as any mail reaches it. What the member does is its own: a claim clears
     `out_of_work` as it always has (§4.9a), and a second `none` is a declaration like the first.
     **Not covered, on purpose**: an entry that was in the ledger when the member declared — one a
     sibling held and dropped, one that came off the board — is not new by this rule; the one
     case that is a fact on the records is this rule's: an id in `lane_seen` that still matches
     the lane and whose lease a sibling released — a `dropped` entry in the `progress` of any
     other record of the same repo, its `at` later than the member's `out_of_work.at` — is told
     once, in the note a new id is told in and counted with them (*TD-108 (dropped by
     grinder-ao-2)*), the release's instant kept beside the id in `lane_seen` (`dropped: {id:
     at}`) so a second look at the same drop tells nothing and a later drop of the same id is told
     again; an id a live record has claimed since, its lease unexpired, is not told while that
     lease holds — the entry is its holder's; an entry that came off the board is *has become
     pickable*, above, already. **An exited or closed member is not written to**, and its
     `lane_seen` is kept all the same: what its lane gains while it is gone is rule 8's once its
     team has wound down. A finished member closed while the team runs on — by rule 9's pass, by
     its manager, or by itself — is rule 8's *A member that finished while its team runs on*: told
     nothing where it sits, it is started again alone for what its lane gained. The ledger read is
     the checkout's file at the home, so an entry counts from the moment that checkout holds it.
  7. **Brief changed.** A member reads its brief once, at its start; a restart replays the brief's
     files, not the prompt its first start was handed. Two halves. **A replay reads the brief's
     files again.** The client that composes a brief (§4.8) hands the create, beside the filled
     `prompt`, what it was made from — **`prompt_from: {base, slots}`**: `base` the template's path
     as installed (a role with no template: the repo's brief), and each slot, in the order it is
     filled, either `{file: <path>}` (the repo's supplement, its text stripped and `none` when
     empty) or `{text: …}` (the lane, the techlead's and the manager's ids, the seat's primer's
     path, `none`), with **`prefix`** the §4.9 Project block put in front as it stands — and the
     launch record keeps it; a prompt a person typed whole (`ao new --prompt`, the form's Opening
     prompt) sends none. A replay — rules 1, 2 and 3, and this one — fills `base`'s slots from those
     files and texts, plain replacement of the slot's name and nothing else, and hands that to
     `create`; where `base` is the package's template (its `slots` name `{repo}`), any placeholder
     of `base` (lower-case letters alone between `{` and `}`) the record's `slots` do not name is
     filled from the file beside `base` named `<base's stem>.<slot>.md` where there is one, else
     `none`, so a template that gains a slot never replays it literally (§4.9c item 5). A record
     carrying **`relaunch: {at, lane, review}`** (§4.9c *Switching*) is restarted under this rule's
     conditions as one carrying `brief_changed` is, save that a declaration of `out_of_work` does
     not hold it back. The host agent knows a file and a slot and no role, template or team, and
     never reads a definition. A file inside a checkout is read **as merged** —
     `origin/<default>:<path>` as last fetched — so a branch checked out there is never a running
     team's brief; the template is the installed package's, so it is what was promoted. *Origin's
     default* is `origin/HEAD`, else `origin/main`, else `origin/master`; a file in a checkout with
     no origin is read from disk. A file that cannot be read, a launch record with no `prompt_from`
     (`ao new --prompt`, a record written before this), or a member on a node (its files are that
     host's, and the home reads its own disk) replays the stored prompt — the one the last create
     was handed, a refill included — and the `restarts` entry says so (`prompt: stored`). **The tick
     sees the change.** The record carries **`brief: {at, sources: [{path, sha}]}`** (home-owned),
     written at each create from the files as they were read; on the reports' cadence the home reads
     each source the same way and, when one differs for **ten minutes** (`BRIEF_SETTLE`: a run of
     merges is one change), writes **`brief_changed: {at, paths}`** (`at` when that difference was
     first read), cleared by the next create under the name or when the files read as recorded
     again; a source that cannot be read now is no change. A replay of the stored prompt writes no
     `brief`. The card and `ao status -v` say it (§4.5a **brief changed** chip). Then, as rule 5
     does and in the same two ways: a member that is **`working`** is not interrupted — every `ao`
     reply it makes ends with *(your brief changed — finish what you hold, then `ao progress restart
     --why "brief changed"`)*, and rule 2 restarts it on its word — a word said while the record
     carries the mark is never *early*, and a member that has declared, or a seat, is not given the
     clause; a member that is **hook-confirmed `idle`, holds no claim in progress, has declared
     nothing, and has nothing uncommitted or unpushed** (known, as in rule 2) **is restarted by the
     tick itself**, closed first and replayed, `restarts: [{why: brief}]`, under the ceiling as
     every replay is; a close or replay that failed keeps its `brief` entry with `error`, counts
     toward the ceiling, and is tried again by the tick, while a Close by anyone else is never
     undone. The same tick restart, under the same precondition, serves §4.10 *A lapsed cache is
     started again, not rung*: the doorbell hands it a member idle past the cache lifetime with a
     long context, and the entry reads `why: cache`. Never a seat, never — for `brief_changed` — a
     member that declared `out_of_work`, never an interactive session, never past a stop time, into
     a wrap-up, a gate pause or a suspension, and not on a node yet, as rule 4's nudge is not. **The
     manager stays outside rule 5's context bound** (§4.8): this rule and a crash are what restart
     it, and its reading is drawn for a person to judge. **Not covered, on purpose**: a team's first
     start still reads a `brief:` from the checkout's working tree (§4.9); when that differs from
     what is merged, the record says *brief changed* ten minutes later and the rule above brings the
     merged text. `org.yml` is not a brief's file: a changed lane, profile or member is a person's
     Start or **Members…**.

  8. **Work for a team that wound down.** A team that winds down closes its members, so rule 6 has
     nobody to tell. A member the current flow sits out (`closed_for: {why: sit_out}`, §4.9c) is
     passed over: no lane of it is watched, and `wound_down` skips it as it skips a `finished` one.
     The tick keeps `lane_seen` for a member that is gone as it does for a live one, and reads a
     team as **wound down** as its card does (§4.5a *wound down* note), over the team's
     **unattended** sessions as the card's reading is (§4.9 *A person in the team*): none of them
     live, and every one that is not a seat — the record's `seat` field, since the home reads no
     definition — having declared `out_of_work`; a member killed or closed by a person makes it
     *stopped*, which this rule leaves alone, and a person's own session in the team changes
     nothing. The reading is written once (`sessionorc.work`), and the card's is the same function
     given the definition's seat names. When a wound-down team's member's lane holds a matching id
     its `lane_seen` does not — the memory pruned as rule 6 prunes it for a member that is gone, so
     an entry that left the lane before the wind-down and came back after it is this rule's news —
     and `WORK_SETTLE` (ten minutes) has passed since the home first read the newest of them — a
     time the home keeps in memory, so a restart of the home starts the settle again — the home
     writes **`work_waiting: {at, repo, members: {<name>: [ids]}}`**, `repo` being the ledger's, on
     its own `host` record under the team's name — for the first registry root, in sort order, where
     the team's news is in two, the other's waiting for the next wind-down. It is removed when every
     member it names is live again (a wound-down team's: any crew session live again), when no id is
     new, and under `off`; a member's ledger that cannot be read removes nothing, since *could not
     look* is not *no id new*: what stands stands, its `at` and the settle's memory kept; while more
     ids settle, what stands stands, its `at` kept, its ids possibly stale until the settle ends.
     What follows is the team's setting, **`teams.<team>.on_work`** (§5 `settings.yml`), a person's
     alone:
     - **`ask`**: the **Inbox row: team start** (§4.5a) under *Needs you* — *ao-grind · wound down
       00:56 · its lanes gained 3 entries: TD-213, TD-214, TD-223* — whose **Start** is the team
       card's. Nothing starts until the press.
     - **`start`**, and what a team with no key has (not built: the code still reads `ask` when the
       key is absent; TD-466 builds it): a person's standing press, as a schedule is. The home
       replays the team: the launch record of every record carrying the badge that ended by the
       team's own ending, as §6 *Schedule* replays at the reset and under its rules (seats included,
       no launch record no start, a suspended record or one at its ceiling left out — a seat's
       fills, which rule 3 never counts toward `RESTART_CEILING`, not counted here either), `why:
       work`, each entry carrying the `ids` it was started for, the start's instant as `start` and
       `of`, how many records the start set out to replay (so a start whose replays partly fail is
       still one start, removes the mark, and is counted *n of m* from the records), the records
       other ones name as a controller first so the lead is up before its members, and every
       record's mail kept, as a fill's and a Restart's is (§4.10 *The name coming back adopts it*).
       It starts **the whole team**, a member whose lane gained nothing included: the manager decides
       who runs, and a member with nothing to pick declares `none` again at the cost of one short
       run. Five bounds, each
       read before the first replay and each leaving the row of `ask` in its place, saying which: a
       profile of any member **over its usage line** (§6 *Usage gate*); the team's **stop time**
       passed and not cleared; **`WORK_STARTS_DAY`** (three) starts of that team by this rule in the
       last twenty-four hours, a start being one instant however many records it replayed (the home
       keeps `work_started: [at]` beside `work_waiting`); a start by this rule inside the last
       **`WORK_EARLY`** (thirty minutes); and its repo **over the team's balance line** (§6
       *Balance*), read here by the rule itself, since the mark goes with the team's last live
       member: the team's `balance` against every registry root its records name, either crossing
       counting as for a live team, and `review` against the queue its ended seats keep. A reading
       that cannot be told writes no new hold and lifts none, and a standing hold whose repo, lines
       and limits are unchanged is kept as it stands, so its numbers are the crossing's. A standing
       balance hold that an earlier bound displaces is kept beside it, as `held.balance`, and read
       as the standing hold once that bound lifts, so a reading that fails at that moment does not
       start a team that was over its line. Under `on_work: ask` the rule starts nothing, so it
       holds nothing back: the mark drops every hold, the balance one with it, and a later `start`
       reads the line afresh, a failed reading writing no new hold. A person's turn of `on_work` is
       the person's own start or stop, and is read so. A bound that holds writes
       **`held: {why}`** on `work_waiting` — `usage` with the `profile` and, when the reading has
       one, the window's `resets`, `until` with the instant, `day` with the `count`, `early` with
       the last start, `balance` with the `repo` and the `crossed` lines in the mark's shape —
       re-read on every tick, so the start follows on the tick the bound lifts; a team with no
       record left to replay is held as `nothing`, and one with a member on a node whose link is
       down as `link` with every down host as `hosts` (and `host`, the first, for a page before it),
       the whole start waiting for a tick the links are up. The row is drawn from `held`.
     - **`off`**: nothing is written and nothing drawn; the team waits for Start or its schedule.

     Dismiss — `clear_work {team}`, a person's own RPC and the home's alone (`modes.HOME_EDITS`: a
     node forwards it and refuses it offline) — adds the ids to each member's `lane_seen`; a start
     needs no such write, since the records it makes begin with no `lane_seen` and rule 6 writes
     theirs at their next declaration. A person's Start, a schedule's, or Dismiss clears
     `work_waiting`. The note on the team card (§4.5a *work waiting* note) says which of the two
     happened: *3 entries waiting since 14:02* or *started 14:12 for TD-213 and 2 more*. **A start
     is still never the host agent's own idea**: with no key it asks, and `start` is written only by
     `set_settings`, which a session cannot call. Not on a node yet, as rule 4 is not; a team with
     no lane that matches by header (a lane of references) never has work waiting by this rule.
     **A member that finished while its team runs on.** A team is not wound down while a seat or a
     member is live, and a closed member is written to by no rule, so an entry merged into a
     finished member's lane would wait for a wind-down a long seat never gives. So this rule reads
     **member by member** as well. A crew member — not a seat, not the manager — that is `closed` or
     `exited` after declaring `out_of_work` (closed by rule 9's pass, by its manager, or by itself;
     one a person closed — its `closer` names the person, §4.7 — or anyone killed — a kill writes no
     closer and destroys the pane, `pane: false` — is *stopped* and left alone, as a stopped team
     is), while a seat or a member of its team is live (nobody live and not wound down is
     *stopped*), whose lane holds a matching id its `lane_seen` lacks while its team is not wound
     down, is this rule's news as a wound-down team's member is: after `WORK_SETTLE` the home writes
     the same `work_waiting` mark, `members` naming it, and the team's `on_work` follows. **What a
     start replays is read at the start**: a wound-down team is replayed whole, as above; a team
     that runs on has only the members the mark names replayed, each alone — its launch record,
     `why: work` with its `ids`, `start` and `of` counting the members replayed, its mail kept —
     under the same five bounds, the usage line read for its profile, one such start counting as one
     of the team's `WORK_STARTS_DAY`; a named member at its ceiling, suspended, sat out, or without
     a launch record is left, and a mark naming only such members holds as `nothing`. Under `ask`
     the row reads *ao-grind · grinder-ao-1 finished 00:56 · its lane gained TD-428* (§4.5a **Inbox
     row: team start**; the row's words and the help text not built — TD-466 builds them), and its
     **Start** replays the named members and not the team, which is running — `work_start {team}`, a
     person's own RPC and the home's alone (`modes.HOME_EDITS`), the same replay under the same
     bounds, refused in the row's words when one holds; `off` writes nothing. The member started
     again reads the ledger at its start and finds the entry, as any start does: nothing is typed at
     it, and rule 6 tells it nothing it has not read. **A seat still holds a wind-down**: a team
     whose anchor is at work is running; what changed is that a member's lane no longer waits for
     that reading. **Rule 9's race** — a member closed as finished on the tick before the checkout's
     reading held the entry merged into its lane — needs no hold on the finished pass: the closed
     member is this reading's on the next tick, and the miss costs one restart rather than a day.
     **A question's end is work.** A member closed with a question out — a `steer` or an `ask` to
     the person about a reference, orphaned at the close (§4.10 *A question about a reference
     outlives its asker*) — meant to act on the answer, and the lapse of its `steer` is an answer
     too (§4.10 *An orphaned `steer` lapses to its default*). So when the asker's team is **wound
     down** by this rule's reading, the lapse, or the person's Reply, suggested answer or *Go with
     it* on the orphaned entry, writes `work_waiting` for the team as a lane's new id does —
     `members[<orphaned.name>]` gaining `orphaned.ref`, `repo` being `orphaned.repo`, and
     **`questions: [{id, ref, name, how, kind}]`** beside `members`, `how` one of `lapsed`,
     `answered`, `kind` the entry's (`steer`, `ask`) — with no settle; a mark already standing gains
     the question and keeps its `at`. What follows is the team's `on_work` as above, under the same
     five bounds: `ask` draws the Inbox row *team start* saying what ended — *designer-ao-1's steer
     about TD-222 lapsed to its default*, *designer-ao-1's ask about TD-222 was answered* — and
     `start` replays the team, where the create that puts a record under the asker's name keeps the
     closed record's mail (§4.10 *The name coming back adopts it*), so the successor reads the lapse
     note or the `handed` answer at its first `ao inbox` and owes the outcome. Dismiss clears the
     mark; a question ends once, so nothing is added to `lane_seen` for it. A team that is *stopped*
     rather than wound down — a stop time, a person's Stop — is left alone as this rule leaves it:
     the note waits in the mailbox for the person's Start, which keeps it the same way. A member
     whose team is live when its question ends is not this rule's: it is waiting (§4.9a *Waiting is
     read, never declared*), and the answer or the lapse rings it where it sits. A `steer` whose
     asker merely `exited` is not orphaned (§4.10), and one whose bound passes inside the
     wind-down's settle lapses as any does, its note waiting in the mailbox for the next Start: the
     window is `FINISHED_SETTLE` wide and is left as it is.
  9. **Finished is the home's reading** (`sessionorc.work.finished`, which the page's *concluded*
     returns; the tick's `_finished_pass`). Whether a team had finished was the manager's judgement
     (§4.9a *The manager runs the stop itself*), and a manager judges from what it remembers; every
     fact it needs is on the records, so the home reads it: a team is **finished** when every
     unattended live session carrying its badge that is not a seat and not the manager is finished
     as §4.9a defines it — `out_of_work` on the record and `idle`, `exited` or `closed` — none of
     them carries `restart_wanted` (that one is rule 2's, and a team with a restart wanted is not
     finished), its seats are `idle` or gone, and its manager, when live, is `idle`, declared or
     not. A team whose only unattended live sessions are its seats and its manager is finished too:
     nobody is working and nobody has anything to take. An interactive session in the team is a
     person's and counts for nothing, as everywhere (§4.9 *A person in the team*). **The reading is
     made from the records and nothing else**, since the home reads no definition (§4.9): a seat is
     a record with `seat`, the manager is the record the team's other records list in
     `controllers` and that holds `control` — as `team_groups` finds it — a person's is
     `unattended: false`, and a team with no such manager (a person leads it) is read with nobody
     to tell. It is **derived on every tick from the records and never stored as a fact**, and it
     is the one the page's *concluded* draws (§4.5a *team groups*): one function, `finished`, in
     `sessionorc`, answering `{at, restart, names, why}` — it holds when `why` is empty, and `at` is
     the latest declaration counted, none where nobody declared — or nothing for a team with no
     unattended session live, the clients calling it with the same records, so the card and the
     tick never disagree. Where a lead under the manager also holds `control` and is listed
     (manager → lead → worker), the manager is the one no other such record controls, and the
     lead is a member. **Which members count.** A live member with `out_of_work` is finished; a
     `closed` or `exited` one with `out_of_work` is finished too; a member `exited` with `pane`
     true and no declaration (rule 1's crash), or `exited` or `closed` with `restart_wanted` (rule
     2's), **blocks the reading** until that rule has acted, since a team about to have a member
     back is not finished; a live member that is **waiting** on the person (§4.9a *Waiting is read,
     never declared*) — the person inbox holds an open `ask` or `steer` from it about a reference —
     **blocks it too**, declared or not, its clause naming the reference and the bound, so
     `finished` reads the person inbox's open entries beside the records; any other dead record is
     passed over, as *concluded* passes it over; and a record the current flow sat out
     (`closed_for: {why: sit_out}`, §4.9c) is passed over whatever it declared — rules 1, 2 and 7
     never recreate it, so a `restart_wanted` on it would otherwise block the reading for good.
     `restart` is true when any counted member carries `restart_wanted`: the page reads
     *concluded · restart wanted* then, mixed with declarations of `out_of_work` or not, and **the
     tick winds down only a team whose `restart` is false** — one that wants another run is rule
     2's. **What the tick does with it.** When the reading has held for **`FINISHED_SETTLE`** (ten
     minutes, a clock the home keeps in memory per team from the tick the reading first held,
     dropped the tick it stops holding, so a restart of the home starts it again as rule 8's
     `WORK_SETTLE` does: a finished member may claim again on rule 6's news, and a manager whose
     round has the wind-down in hand makes it) the home **winds the team down itself** — and it
     **holds no wind-down for an entry a declared member has not been told of**: rule 6 tells on
     the tick an entry matches, inside this rule's settle, and the cases it does not tell in —
     gated, wrapping up, past its stop, suspended, over its balance line — are each a reason the
     member is not to be started into it, and leave the id out of `lane_seen`, so the wound-down
     team finds it by rule 8; a hold would make this rule a second reader of the lane —: the
     members' half is `ao team stop --close`'s (§4.9a) — each finished member is closed under the
     wrap-up's own safety check, one with uncommitted or unpushed work left open and the Inbox row
     it is after any wrap-up — and the manager's half is this rule's own. The manager, if live and
     on the home, gets **one** send of fixed text, *[agentorc] your team is finished: every member
     has declared. Make your last acts — `ao progress none`, the note to the person — then `ao
     close` yourself*, typed only into an idle composer as rule 4's line is, and
     `finished_sent_at` on its record says it landed (§4.3's list of the home's fields). From that
     send on the reading is no longer asked of the manager, whose last acts are work:
     `WRAPUP_GRACE` after `finished_sent_at` it is closed once `idle` and clean, with
     `closed_for: {why: finished, closed_at}` on its record as rule 2 marks its close; one that is
     never idle, or is `needs-you` or `limited`, is left as it is and is the Inbox row *manager did
     not close*, under *Needs you* — drawn from `finished_sent_at` past its grace on a live record,
     nothing more stored. A manager that closed itself, as the line asks (`closed`; one that
     `exited` is rule 1's or a person's kill, and is left) gets the same mark on the tick that
     finds it gone, so the team reads *wound down* without its declaration. **A member live and
     not finished after the send takes the wind-down back** — one working, one that never
     declared, or one that now wants a restart: `finished_sent_at` is removed and the reading is
     asked afresh; a member left open with work is still finished, and is closed the tick its work
     reads pushed, its manager live or already closed, with nothing said again: a team whose
     manager carries the mark was announced. A manager on a node gets no line and the close alone,
     routed as rule 2 routes one, and waits for the link. **The announcement the manager did not
     make** the home makes: one `system` note to the person with the two lines §4.9a asks of the
     manager, and the lines §4.9a *The home's note says more* adds — the pull requests in the
     members' `progress` entries reported `done` with a `pr` since the team's start, the start
     being the earliest `created` among the team's records that are neither superseded nor
     forgotten (§4.9a's own list is the manager's, from `gh`), and each member's `out_of_work.why`
     — written on the tick the manager is closed or found closed (for a team with no live manager,
     the tick that closes its last member; where one was left open with work, the tick that finds
     the last one gone, whoever closed it — that the note is owed is kept in memory per team from
     the settle, as the settle's clock is, and dropped the tick a member is at work again), and
     only when no `note` from the manager reached the person inbox after the reading first held, so
     a team that dissolves is never quiet and never told twice, whoever ended it. **Then the card
     reads *wound down <t> ago · by the tick*** — `wound_down` and rule 8's reading skip the
     manager as they skip a seat when its `closed_for` says `finished`, since the premise of this
     rule is a manager that never declared — and rule 8 watches its lanes as after any wind-down.
     Seats are not this rule's to close: an idle seat closes under rule 3's grace, and a Start
     closes it. An interactive manager, or member, is a person's: never typed at, never closed,
     and not counted (§9 invariant 5). Not while a member is gated: a paused member has declared
     nothing, so the team is not finished, and nothing here reads the gate. A node's member is
     closed as rule 2 closes one, routed over the link, and waits for the link. **What this leaves
     the manager**: its round still ends the team when it sees the same reading first — the
     sequence is one code path — and its brief says the reading is the records' each round
     (`ao team status --json`, §4.9) and never an earlier round's; what it may not do is keep a
     finished team live by not saying so.

  10. **The cadence check** (`sessionorc.cadence`; the Inbox row from `ui/inbox.py`
     `cadence_marks`). The script's verdict needs no reader, so the home runs it, not the
     manager. For a supervised member, not a seat, each `progress` entry `done` carrying `pr` —
     declared or derived, so a PR merged from the member's branch is one too (§4.4 *Repo facts*,
     `reports.derive`) — that the record's **`checks: [{pr, at, sha, verdict, failed, told, row,
     merged, read_by}]`** (home-owned) does not hold at that head is checked **at the home** on
     the reports' five-minute cadence, detached as `seat_count`'s read is: the script run `--json`
     in the registry root the record's `repo` names, at the home (a node's member's checkout is at
     the same absolute path, §4.4a; a path the home cannot see, or a root without the script — a
     repo not on dev-cadence — gives no reading), one PR per run; its exit of 2 (no such PR) ends
     the reading, and an `unknown` verdict is kept and read again on the next cadence, nothing
     told. The entry keeps the head it was read at, so a PR whose head moved is read again: the
     head and the PR's state are read from the repo's PR reading (§4.4 *Repo facts*: its open PRs
     and those it saw close, each with its head), so a PR is asked no more often than that reading
     reads it, and only a PR the reading lacks is one `gh pr view` before the script; a head
     neither can give is no reading. A merged PR's head no longer moves, and nor does one closed
     unmerged, so its entry carries `merged` or `closed` (a PR found closed at the head it was
     read at is marked so with no second run) and a settled read of it stands with nothing asked,
     until the member's own new `done` names the PR — so a PR reopened after it was marked is read
     again only at that `done` (a derived entry is written anew at every derivation, so its date
     is no report). Of the PRs waiting, the one longest unread is looked at first. A pass takes
     `told` away with `row`, so a fail after it is a first fail again. **`pass`** writes the entry
     and nothing else. **`fail`** the first time for that PR — `told` empty — is one fixed line to
     the member, rule 5's two ways: typed into the idle composer of an unattended member on the
     home's own host (never into an attended session, invariant 5: it is told by the clause),
     *[agentorc] PR #842 failed the cadence check: review, ledger — fix it, then report `ao
     progress done TD-257 --pr 842` again*, the row names from the script's `rule` field and never
     its `detail`, and on a working member the clause at the end of every `ao` reply, *(PR #842
     fails the cadence check: review, ledger)*, read at the home as rule 5's is; `told` marks it.
     **The second** fail of the same PR — read again at a new head, or after a new `done` names
     it, and still failing — or **a fail read on a PR already merged**, which no re-report cures,
     is the **Inbox row: cadence check failed** (§4.5a), under *Needs you*, counted, `#842` a
     link, the failed rows named, and `row` on the entry; a later pass removes the row and leaves
     the entry as the record of what was read. **The `review` row is still self-attested**: the
     script proves the comment was posted before the merge, not that it was honest, and the home
     adds beside the verdict what it alone knows — *read by techlead-ao-1* when a mailbox it holds
     has an `ask` carrying `pr: 842` with a reply from the seat on its thread (§4.9b *The
     reader*), *recorded* otherwise — and never says *verified*; the reader is kept on the entry
     as `read_by` the first time the reply is seen, since mail is pruned and the read is not. `ao
     status -v` prints the record's last check per PR; a member on a node is checked as any (the
     script runs at the home) and told by the clause alone, as rule 5 tells it. **Not this
     rule's**: a PR from a session with no `progress` entry naming it (a person's anchor — the
     cadence is theirs to run), and a repo whose checkout holds no `scripts/check_cadence.py`.
  11. **Merged without its read** (`sessionorc.held`, the tick's `_held_pass` and `_held_line`;
     the Inbox row from `ui/inbox.py` `held_mark`). `ao pr held <n>` is the author's own read of
     its record's `review` against the PR's files (§4.9b *The reader*); whether a held PR merged
     without the reader's reply, the home checks. For a supervised member whose record carries
     `review`, each `done` with a PR — the member's own word or a derived one, since a declared
     `done` stands in a derived one's place (§9 invariant 10) — is read at the home once the PR
     has **merged**: the PR's changed files and its merge time (`gh pr view --json
     files,changedFiles,mergedAt`, five PRs a pass on the reports' cadence, the longest unread
     first; a PR not merged yet is read again, a settled one no more — kept in memory, so a
     restarted home reads each once more, and a PR merged longer ago than half the mail's
     retention is settled unjudged, since the reply that would clear it may be pruned — in the
     record's registry root at the home; the matching lives in `sessionorc.held`, where `ao pr
     held` reads it, since the package rule runs one way) against the record's `held:` globs; a
     PR touching none is not held, and the reading ends. A held one looks for its read **in the
     mail the home holds**, link by link (`review_links`; the one-reader form is a chain of one,
     §4.9c *The record carries the chain*): the PR is **read** when **every link whose paths it
     touches** has, on the thread of an `ask` of the record's carrying `pr: <n>` addressed to that
     link's reader — the seat, by its name, the match `ao pr held` makes (`addressed`) — a reply
     from that reader, whatever it says, since a reader answers `pass`, `merged` or `findings` and
     never stays silent (§4.9b); **or** one such thread holds a reply with the verdict `merged`,
     since only the last reader holding the PR merges it (§4.9c); **or** one holds the person's
     reply, where an ask went up past its bound and the PR became the person's, the rest of its
     chain with it (§4.9c *The bound and the person*); for `reader: person`, the same `ask` to the
     person, replied by the person alone. So a chain's PR merged after its first reader's `pass`
     and before its last reader's reply is a crossing, as a one-reader PR merged before its reply
     is. `held.read_by(s, pr, files)` walks `review_links` against the PR's files, `addressed` (in
     `sessionorc.held`, re-exported by `agentorc.review`) matching each reply's sender and each
     ask's addressee to a link's reader; the older `{reader, held}` reads as before. The mail read
     is the record's own — its `ask` in its outbox, the reply in its inbox — and, since mail is
     pruned, the `read_by` rule 10 kept on the PR's `checks` entry — one sender, so for a chain
     the read of the one link it matches, and once mail is pruned a chain keeps that link's read
     and no more. Neither found, **fifteen minutes after the merge** (`held.GRACE`: the reader
     merges first and replies after, and the home may read in between), is a crossing, written to
     the record as **`held_missed: [{pr, at, paths, told, dismissed}]`** (home-owned): the member
     gets one fixed line, rule 5's two ways — typed into the idle composer of an unattended member
     on the home's host, else the clause on its next `ao` reply, said once, `told` marking either
     — *[agentorc] PR #845 touched held paths (`src/sessionorc/agent_tick.py`) and merged without
     the techlead's read — a held PR waits for `ao msg --kind ask --pr <n> <seat> "…"` and the
     reply before the merge* — and the person gets one `system` note, FYI and uncounted, naming
     the PR, the paths and the member, since a gate was passed and the person should know each
     time; the **second** crossing by the same member — two entries on `held_missed` not
     `dismissed` — is the **Inbox row: merged without its read** (§4.5a), under *Needs you*,
     counted, each `#<n>` a link, Open the member, Dismiss marking the entries `dismissed`
     (`clear_mark`; they stay, so no PR is a crossing twice). The home undoes nothing: a revert is
     a person's word, or the reader's finding on a later thread. **Not a crossing**: a PR the
     reader merged itself, which replied; a PR whose record carries no `review` (the anchor's); a
     PR merged before the record carried `review`; a files read that failed — no reading, read
     again next cadence, never a crossing by default. A node's member is read as rule 10 reads
     one.
  12. **Conventions relayed** (`sessionorc.conventions`, the tick's `_conventions_pass`).
     dev-cadence's SessionStart hook prints the new entries of `docs/cadence-changes.md` to a
     session that starts, so only a session that outlives a change needs telling, and the tick
     tells it as rule 6 tells lane news. For each registry root at the home whose checkout holds
     `scripts/cadence_changes.py`, the home runs it `--json` on the reports' cadence — it reads
     the file as merged, `origin/<default>`, as rule 7 reads a brief's file — taking each entry's
     `heading` and `landed`; a root without the script, or whose origin has no default branch
     (exit 2), gives no reading. On each supervised member record of that root, not a seat (every
     fill starts cold, and the hook tells it) and not finished (`out_of_work`: never sent to, and
     its next start is told at its start) and not `scheduled`, `exited` or `closed` (it runs again
     as a new record, which the hook tells), the home keeps **`conventions_seen: {at, headings}`**
     (home-owned), written at the first reading after the create as the headings whose `landed`
     is at or before the record's `created` — an entry whose `landed` the script could not read
     (`None`: the `git log` call failed or timed out) is left for the next reading — and when
     that reading or a later one holds a heading `conventions_seen` does not, landed after
     `created`, the member gets **one `note` from `system`**: *docs/cadence-changes.md gained 1
     entry since you started: "2026-09-29 — a review comment's first line carries the verdict" —
     read it on `origin/main`, then go on* — each as the script's `date` and `title` fields give
     it, three at most and *and n more*, nothing a session wrote — and the headings join
     `conventions_seen`, so each is told once. It is mail: the doorbell wakes the member under
     every rule the doorbell has, one unit of the wake budget, and a node's member is reached as
     any mail reaches it. A member restarted by any rule starts with no `conventions_seen`, is
     written afresh at its create, and the hook has told it. **The manager's `relayed.json` is
     gone**: nothing reads it, and neither the preset nor this repo's supplement names the step.
- **Promote**: a repo's live copy — the host agent and every session's `ao` for this repo, a cluster
  for samscrape — is made from `main` by **a person's press or this policy, never by a session**
  (CLAUDE.md: a worker never promotes; the `promote` RPC is refused to a session as `set_settings`
  is, §4.7). It runs **at the home** (§4.4a), for each checkout in the home's registry (`hosts.yml`
  `repos_registry`) whose `.agentorc.yml` carries `promote:` (§5); a repo whose checkout is on a
  node only has no promote. On the reports' cadence (five minutes, one read per repo, each detached
  from the tick as the `gh` reads are) the home takes **three readings** and keeps them on `host`
  under `promotes`: **live**, `check`'s commit, a failed read keeping the last good one with its
  reason; **main**, `origin/main` of the checkout after the home's own `git fetch origin main`;
  **checks**, the CI verdict on main's head read with `gh` — `green` when every check run has
  concluded and none failed, `pending`, `failed`, or `unknown` with why (no `gh`, no remote, a rate
  limit, no check runs at all — a commit CI has not looked at is not a green one). *When main moved*
  is its head's committer time (a squash merge stamps it), so the settle and the row's age survive a
  restart of the home; the readings are held in memory and re-read at start, and what must survive
  one — a run in flight, a failure — is in the intent files below. **Three preconditions** stand
  between the readings and a promote: **(1) the checkout is on main's head with a clean tree** —
  `HEAD == origin/main` and `git status --porcelain` empty — because `run` installs from the tree
  (the tree is a person's: the policy reads it and never makes it — no checkout, no reset, nothing
  beyond the fetch); **(2) checks green**; **(3) nothing in flight and no failure standing** for
  that repo. With **`auto: true`** (§5 `settings.yml` `repos.<repo>.promote.auto`, the Settings
  page's switch — the one part of the block a person flips, so it is not in the checked-in file) the
  home promotes when live ≠ main, the three hold, and main has stood still for `PROMOTE_SETTLE` (ten
  minutes) — a burst of merges is one promote, since for this repo every promote restarts the host
  agent. **`auto` acts only on a live it has read**: while `check` is not answering (live *unknown*,
  with its reason, a last good reading kept beside it), the home waits for a reading rather than
  take unknown for behind, and the row still offers the person's press. With **`auto: false`** the
  Inbox row (§4.5a *Inbox row: promote*) and `ao promote` (§4.7) are the press: refused, naming it,
  on (1) or (3); on (2) a person may press through `pending`, `failed` or `unknown` — a rollback
  (`--sha`) or a merged hotfix past a flaky check is the person's word — with the row saying what
  the checks said. **The run is detached** from the agent's own process group, its output to
  `~/.agentorc/promotes/<repo>/<sha>.log` (pruned with the run logs, `runs_keep_days`), and an
  intent file `~/.agentorc/promotes/<repo>/inflight.json` — `{sha, at, pid, log, by}`, `by` being
  `auto` or the person, and for a rollback `from`, `kind` and `tree` too (below) — is written
  **before** the start, because for this repo the run restarts the host agent that started it: **the
  outcome is read from `check` on later ticks, never from the run's exit code** (`check` read every
  fifteen seconds, `PROMOTE_WATCH`, while it is in flight), and the agent that judges it need not be
  the one that started it — a restart mid-run finds the file and carries on. Live reads the wanted
  commit → done: the file cleared and a `system` note to the person inbox, *promoted `<repo>`
  `<sha>` — n commits* (FYI, uncounted, §4.10); the wheel and the nodes follow §4.4a. The process
  gone with live still elsewhere, or `PROMOTE_BOUND` (twenty minutes) passed — the process killed at
  the bound as the stop time kills, and only while it is still the run: the intent keeps the
  process's start time beside its pid, so after a restart of the home a pid taken again by another
  process reads gone and is never killed — → **failed**: `failed.json` `{sha, at, log, exit, why}`
  beside it, the Inbox row under *Needs you*, and **nothing further is promoted for that repo, auto
  or press, until the person clears it** — the row's Dismiss, or a rollback that concludes (below),
  the one press a failure does not refuse. Sessions are never told: no send, no state change; they
  live in tmux and survive a restart of the home, an attached Focus reconnects under §4.6's
  contract, and a blocked `wait` ends with the socket as §4.7 says. This repo's block is CLAUDE.md's
  pair: `run` rebuilds the live venv from the checkout (its path from where it runs, never written
  in) and `ao service install` replaces client and host agent together; `check` prints the live
  install's build commit (§4.4 *What is running says which commit it is*), or exits 1 with *build
  unknown*. The home reads the block from the checkout, so it stands as soon as the checkout's
  `main` holds it; with `auto` off, the row or `ao promote` is the person's press, and CLAUDE.md's
  pair by hand stays the fallback while the agent is down.
  **A rollback** is a person's press with a commit: `ao promote --sha <commit>` or `--back` (§4.7),
  never the policy's and never the page's, since the page may be what broke.
  - **Which commit.** The home fetches, then resolves the commit in the checkout. It is refused when
    it is not hex (a branch, a tag or `HEAD` would be read in the person's checkout, not on main),
    when it names no commit or more than one, when it is **not an ancestor of `origin/main`** (a
    branch is never promoted, CLAUDE.md; a hotfix is a merge and then a press), and when it is live
    already. Main's head is the plain press, by the path above. `--back` is `from` in
    `~/.agentorc/promotes/<repo>/last.json` — `{sha, from, at, by}`, written when a promote
    concludes, `from` being what `check` read before the run — and is refused when there is no such
    record or its `from` was never read, and **while a hold stands**: the last promote was then the
    rollback, and its `from` is the commit the person went back from.
  - **How it reaches `run`.** The checkout stays where the person left it. The home adds a detached
    worktree of the checkout at the commit, `~/.agentorc/promotes/<repo>/tree` (`git worktree add
    --detach`; one left by an earlier run is removed first), and starts `run` **there**, which is
    why §5 says `run` makes live *the tree it is started in*. It is the one write the promote makes
    to a repo: an entry in the repo's worktree list, touching no branch, no index and no file of the
    person's tree (git runs the repo's `post-checkout` hook, if it has one, in the new tree). The
    block is read from the checkout as ever, so `run` names a path, and what runs is that path as
    the commit had it; a commit older than the script fails to start, and reads as any failure does.
    **The tree stays** until the next promote or rollback of that repo starts, which removes it:
    what was installed from it may name it as its source, as this repo's build record does to say
    how far main is ahead.
  - **What stands in its way.** Precondition (1) is not asked: the checkout's tree is not what is
    installed, so a rollback goes through with a branch checked out there or a change left in it.
    (2) is the person's word, as on any press, and the reply says what the checks read on *that*
    commit. Of (3), a run in flight refuses it and **a failure standing does not**: a rollback that
    concludes clears the failure.
  - **What the repo may refuse.** `run` is started with `AGENTORC_PROMOTE_SHA` (the commit),
    `AGENTORC_PROMOTE_FROM` (what is live, empty when unread) and `AGENTORC_PROMOTE_ROOT` (the
    checkout, for what a run needs that no commit holds), on every promote. The home knows no repo's
    schema, so a commit that cannot be gone back to (one older than a migration) is the repo's `run`
    to refuse, by exiting before it changes anything: live reads as it did, and the row is a failure
    with the script's own words in the log's last lines.
  - **The outcome** is `check`'s, as ever: live reads the commit → done, `last.json` written, and
    the note reads *rolled back `<repo>` to `<sha7>` from `<sha7>` — main is n commits ahead*. The
    wheel and the nodes follow as after any promote: the run writes the wheel of what it installed,
    and that is the newest.
  - **The hold.** A rollback that concludes writes `~/.agentorc/promotes/<repo>/held.json` — `{sha,
    from, main, at}` — and the reading carries it as `held`. **While it stands the policy starts
    nothing for that repo**, whatever `auto` says and however far main moves: the commit the person
    went back from is still on main, and a later merge that is not its cure would put it live again.
    The hold ends by the person's word alone: a plain press that concludes (main's head, by then
    holding the cure or the revert), or Dismiss — `ao promote clear` — after which `auto` goes on as
    if nothing had been held. **With a failure standing too** (a plain press under the hold that
    failed) Dismiss clears the failure and the hold stands, a second Dismiss ending it. **A second
    rollback under a hold** rewrites `sha` and `at` and keeps `from`: what the person first went
    back from is what the row goes on naming. The row (§4.5a) is drawn while it stands and says
    *rolled back*, never *behind*: live is older than main because a person put it there.
  - **What it does not reach.** The press is an RPC, so it needs a host agent that answers and an
    `ao` that runs. A live copy that does not start is below it: the person makes the worktree and
    starts the repo's `run` in it by hand, and this repo's CLAUDE.md says so beside the promote's
    own pair.
- **Pull: the main checkout follows origin**: the registry's main checkouts move only when someone
  pulls, and the promote's precondition (1) fails on any checkout no one has pulled since the merge,
  so the home's tick pulls them. It runs **at the home** (§4.4a), in the promote's pass and on its
  cadence (five minutes, the full readings, detached from the tick as the `gh` reads are), over
  **every checkout in the home's registry**, `promote:` block or not — one `git fetch origin
  <default>` per repo per pass (the promote's own fetch where the repo has one, never a second),
  then `git merge --ff-only origin/<default>` when two things hold. **(1) Git allows**: the checkout
  is on its default branch (`origin/HEAD`'s); no merge, rebase, cherry-pick or index lock is under
  way (the list `board.busy` keeps, `BUSY`); the repo has no promote run in flight (`run` installs
  from this tree, §6 *Promote*); and the fast-forward itself goes through — git refuses one that
  would overwrite a locally changed tracked file, so a dirty file elsewhere (an unsaved memory note
  under `docs/claude-memory/`) stops nothing and nothing of the person's is overwritten, and a
  checkout with a commit of its own is left alone, `--ff-only` refusing it. The policy makes no
  checkout, no reset, no rebase and no stash: nothing beyond the fetch and the fast-forward, and it
  never pushes — a checkout ahead of origin is the person's to push (the write-back's commit lands
  on origin's head, §4.4 *Board write-back*). **(2) The anchor session is idle**: no session in the
  checkout's root is mid-turn. The home reads it from what the anchor rule reads (`occupants`, §9
  invariant 2), with shells counted too: every record of ours whose `dir` is that root, and a
  container node's record over the same path while its link is up, is at rest — `idle`, `exited` or
  `closed`; `working`, `needs-you`, `stalled?`, `limited` and `unreachable` are not rest, since a
  turn may be under way or about to resume — and every live session the adapters see there outside
  agentorc (a plain `claude` in a terminal, `external_sessions`, §4.3) reports `idle` from the
  tool's own registry (`status`: busy | idle | shell). One the home cannot read the state of is
  taken as mid-turn, and the pass waits (no pull, and the note stays). No session at all is idle. A `shell` record running a
  foreground command is `working` and counts: it may be `git` itself. So tracked files move under
  the anchor only while it sits at the composer, which is where a person's own `git pull --ff-only`
  (cadence §1.10) finds it. **It says nothing**: a pull is routine, so no mail, no state change and
  no trail line; the outcome is a reading on `host` under `pulls`, one per registered checkout, kept
  beside `promotes` — `{at, outcome, why, from, to, commits, occupant}`, `outcome` one of *current*
  (already at origin's head), *pulled* (with `from`, `to` and `commits`), *waiting* (with the
  `occupant` it waits on, or *unreadable*), *refused* (with git's reason, or *on <branch>*, *a
  promote in flight*, *a git operation under way*) and *off* — drawn by the Inbox's origin note
  (§4.5 screen 6 *Boards are read against origin*: the *behind* note's tail) and by the Settings
  page's Repos card (§4.5a), never pushed to anyone. **The switch** is `repos.<repo>.pull` in
  `settings.yml` (§5; the Repos card's **pull**), `true` when absent, and `false` leaves the
  checkout to the person, the reading *off*. What it is not: it does not move the live copy (the
  promote stays its own press or policy; that a pull makes precondition (1) hold more often is all),
  it never touches a worktree (a member's home is its own, §4.9 *Home and reach*), and it never
  moves a checkout to a branch or a commit (that is the person's, and a rollback's tree is the host
  agent's own, §6 *A rollback*). *The tree is a person's* (§6 *Promote*) stands: the pull is the one
  move the host agent makes in a person's checkout, and only ever a fast-forward.
- **Schedule: a team start at the reset** (not built; TD-133 builds it, unscheduled until the person
  says): the one start the host agent makes that no person pressed at the time, and the general form
  the run window (below) reduces to. **A schedule is a person's standing press, kept in
  `settings.yml`** under the team's own key (§5 `teams.<team>.schedule`; read on every tick, written
  only by `set_settings`, so a session cannot set one):

```yaml
teams:
  ao-grind:
    schedule: {start: reset, profile: grind, window: week}   # start ao-grind when grind's weekly window resets
```

  The window is named by its label as the adapter reports it (§4.3), refused when no adapter of that
  profile reports it, as `ao gate` refuses; the instant is that window's `resets` in the account's
  reading (§4.2a), so nothing here knows a tool's week by name. **A scheduled start is a replay,
  never a definition re-read** (*Keeping a team running*: a restart replays the launch record, and
  the host agent does not read `org.yml`). At the reset, for every record carrying that team's badge
  that is `exited` or `closed` **by the team's own ending** — an `out_of_work` declaration, or the
  manager's own close after a wind-down (§4.9a) — not suspended and not at a restart ceiling, the
  tick replays its launch record as rule 2 does for a wanted restart, `why: schedule`, under
  `RESTART_CEILING` as every restart is, the seats included (a replayed seat is on call again, its
  trigger standing). A team with anything live at the reset is left alone. One with no launch
  records — never started by a person on this home, or its cards forgotten, which deletes them —
  starts nothing: **a schedule restarts a team a person once started and never invents one**, so the
  briefs a scheduled team runs are the ones its last person-pressed start read, and a brief change
  is a person's `ao team start`. A kill or a person's Close is never undone, as rule 1 says. With
  the ledger still empty each member runs once, declares `none`, and the team winds down again. The
  team card says it (§4.5a *starts* note); `ao schedule` (§4.7) and the Settings page's **schedule**
  field set and clear it (both not built; TD-133). A start time for one session is **Start time**,
  above. **Not designed**: window overrides with an expiry (TD-101: not wanted yet), calendar-shaped
  windows and one-off runs — TD-026 holds them for the person's word on scope.
- **Team stop time** (§5 `teams.<team>.until`; the Settings page and `ao team until`, §4.7): the
  team-wide form of a session's `run_until`. On every tick, each live session carrying the team's
  badge — members and seats — whose `run_until` is unset or later than the team's takes the team's
  instant, exactly as `set_stop` would give it (`wrapup_sent_at` reset, the wrap-up then the kill
  as the stop time's own rule says), and a start the team makes after the instant is set stamps it
  on what it creates — while the instant is still ahead: a session created after it has passed is
  not given it, since starting the team again after its stop time is the person's word. It runs at
  the home, whose file it is; a node's member takes it through `set_stop` over the link, and waits
  for the link. **Clear** removes the key and takes the team's instant back from the members that
  carry it, and a moved instant moves the unattended ones with it (a session a person took over
  keeps what it carries: a move is a policy's); a session's own `ao until` is kept as the earlier of
  the two, and a Clear leaves it alone. A stop time in the past is refused when set, as `ao until`'s
  is. **Run window** (Not built — phase 3, the tdgrind port): start missing workers inside the
  window; wrap-up-then-kill outside, by setting a stop time.
  **Usage gate** (per profile): pause every unattended session on a profile when **any** of its
  reported windows reaches that window's **line**, and resume them when every window is back under
  its line — the windows being the **account's** reading (reported or asked, §4.4 *Usage*), the one
  poll every profile on that account shares (§4.2a), read against this profile's own lines — a
  profile no live session runs under, which keeps no copy of the reading, reads its account's, so a
  start under a wound-down team's profile is gated by the reading a person's session keeps fresh,
  and `ao gate` prints it with its age and source; a fetch failure never pauses — the last good
  reading stands, as the chip's does (§4.5a).
  **A reading the gate can no longer trust** (`usage.project` is the one reader; `_gate_windows`
  asks it for the gate's pass; `ao gate --max-age` and the Settings page's **trust a reading for**
  set the age, and both print the projection, as the chip does). A window's reading older than
  **`max_age`** (§5 `usage.max_age`, one hour unless set; `off` for never) is **projected**, for a
  profile with unattended sessions live and **while the window's `resets` is still ahead**: the
  held percentage plus the window's **rate** times the reading's age, never above 100, the rate
  being the rise per hour between the oldest and the newest of the kept readings (§4.4) that lie
  inside the two hours before the newest, never below zero, and **no rate at all** unless they span
  twenty minutes. One reader gives the gate its number, a reading or a projection, and every policy
  that asks whether a profile is over its line asks that reader, so a profile paused on a
  projection is not restarted or filled the next tick. A window with no age at all (never
  confirmed, on a reading with none) and a metered profile's spend are read as they are. **When the
  projection reaches the line** — the profile's, less a team's reserve priority as ever — **the gate
  pauses as it pauses on a reading**, the mark saying which it was — *paused · usage (projected
  week 96% ≥ 95%, last read 6h ago)* — and one `system` note tells the person, FYI, *pausing on a
  projection: no reading of Claude · paul for 6h*, once per account while a pause on a projection
  stands. **A pause on a projection ends** when a reading shows every window under its line (paused
  sessions report nothing, so that reading is the endpoint's, asked for as §4.4 says), when the
  window's `resets` passes, or when the person turns `max_age` off or moves a reserve so that the
  projection no longer reaches the line; `RESUME_MIN` holds as for any resume. A pause, on a
  projection or a reading, never ends because a window went unknown for want of a rate: that is a
  reading growing older, not one under the line. **A window that is unknown pauses nothing**: past
  its reset, or with no rate to project by, the gate has no number (the gate's rows carry `unknown:
  "reset"` beside the number last read, which `ao gate` prints as *week 5 → unknown since its reset
  (was 99%)* and the Settings card as *unknown since its reset*, and a pause on that window lifts
  once the reset passes), and one note says *usage unknown for 1h, n unattended sessions working*,
  once per account per day and not at all with `max_age: off` — for a window past its reset, once
  it has been so for `max_age`, since a session at work reports the new window within a minute. A
  profile with nothing unattended live is never projected. The windows and their labels are the
  adapter's (§4.3); the gate knows none of them by name.
  **The line is computed from a reserve, never typed as a percentage.** The setting is a
  **reserve** per window label, of two shapes: a flat percent — `30` — whose line is `100 −
  reserve`; or a percent **per day** — `{per_day: 10}` — whose line is `100 − per_day × days_left`,
  where `days_left` is the whole days until the window's `resets`, today counted whole (`ceil`,
  never below 1), and the line is clamped to 0–100. A per-day reserve on a window whose reading
  carries no `resets` (§4.3 allows one) makes no line, as no reserve does. With 10 a day the weekly
  line is 30% on the reset day, 60% with four days left, 90% on the last day: **the line rises as
  the week goes**, so a team paused on a Wednesday at 60% resumes on the Thursday when the line
  moves to 70%, and one paused on the last day resumes at the reset, when the window empties. A
  window with no reserve has no line and pauses nothing; the tool's own 100% shows `limited`. **A
  team's reserve priority** (§5 `teams.<team>.reserve`, the Settings page, `ao team reserve`) is a
  flat percent added to the profile's reserve for the sessions carrying that team's badge on every
  window that has one (it lowers a line and never makes one), the mark carrying `team_extra: {team,
  n}`: with grind at 30 on the session window and ao-grind at 10, ao-grind's sessions pause at 60%
  and the profile's others at 70%, so two teams on one profile pause at different lines; the chip
  still shows the profile's line, and a session paused under its team's line says *paused · usage
  (ao-grind +10)*.
  **A pause is a send, not a kill** (the PAUSE flag of this section's last bullet): the host agent
  submits the record's `pause_prompt` once — *pause: finish the step in hand, commit and push what
  you have, then stop and wait for a resume* — which a working session takes as its next prompt
  (§4.3: a busy session queues the text), and marks the record `gated: {profile, label, pct, line,
  since, next, resets, sent_at}` — `next` being when the line next moves or the window resets,
  whichever is sooner, `resets` the window's reset, and `sent_at` when the pause prompt **landed**,
  as `wrapup_sent_at` is to `wrapup_at` (§4.4a): the mark is written the tick the line is crossed,
  the send is retried on every tick until it lands, and a card reads the two apart — *paused ·
  usage* once the mark exists, *· pause sent* once it landed. A session sitting on a permission or a
  question is **not typed at** — the same rule as `send`'s (§4.2) — and, unlike the stop time, the
  gate does not kill it: the mark stands and the send lands on the tick after the dialog clears.
  The wording travels on the record as `wrapup_prompt` does. While gated: the doorbell does not
  ring the session; a controller's `send` is refused at the home with the gate as the reason — read
  from the replica's `gated`, a node-owned field like `state` (§4.4a); a person's own send is not —
  `ao send` directly (a person is not a session, §9 invariant 11), or from Focus after **Take over**
  (the composer of an unattended session is closed), which makes the session interactive and out of
  the gate's reach altogether (§9 invariant 5). The card's slot reads ***paused · usage** — grind
  week 71% ≥ 70%, line moves Thu 07:00* as *what explains a stop* (§4.5 *The card's anatomy*, row 5
  (a)), and row 5's rule stands, **one text, the first that applies**: a pending permission or
  question, a `limited` reset or a `stalled?` note wins the slot, since each needs a person and the
  pause does not, and the mark is drawn once that clears; the Focus header shows the mark whatever
  the slot shows. A mark, not a state — the session still reads `idle`. **Resume** is the record's
  `resume_prompt` — *resume: carry on from where you paused* — sent once when every window of the
  profile is under its line and no sooner than `RESUME_MIN` (ten minutes) after the pause; the mark
  goes with it. A manager on the profile is paused like any worker (this section's opening
  paragraph), so a team pauses whole — a paused team is a live team, so its card keeps **Wind
  down** and **Stop now** (§4.5a), and a wind-down sent to a paused member is a person's act, lands
  as one and ends it; whether a paused manager should also *declare* is open (TD-099). The run
  window's wrap-up-then-kill is **not** used here: a pause that killed would need a start, and a
  start is a person's act, a schedule's (off by default) or rule 8's where the person set it.
  Interactive sessions are never paused and carry no line: at 100% they show `limited`. The
  reserves are the person's, per profile, in the host's `settings.yml` (§5), read on every tick and
  changed by `ao gate` (§4.7) or the Settings page; the top bar's chip shows the line beside the
  number (§4.5a). A one-day change to a reserve is by hand — the reserve down, and back after the
  reset; an override that expires on its own is rejected for now (TD-101). **A metered profile**
  (§4.2a) has no quota to keep back, so its reserve is an **amount per window**, in money where the
  profile has prices (`{day: "$5", week: "$20"}`) or in tokens where it has none (`{day: "2M
  tok"}`), read against the **account's** spend (§4.2a): the amount is the window's **100**, `pct`
  is spend over amount, and the line is `100 − <team priority>` — the amount itself for the
  profile's plain sessions, nine tenths of it for a team carrying a reserve priority of 10, so a
  team's priority means one thing on both billings. The pause is the same pause, sent when the
  account's spend reaches the line, and lifted when the window rolls (`next` is the boundary;
  nothing else moves a spent window back, and the resume keeps `RESUME_MIN` as any does — a pause
  that began minutes before the roll resumes ten minutes after it began). At eight tenths of an
  amount the home files one `system` note to the person inbox — *grind-api · day $4.10 of $5* — an
  FYI, uncounted, once per window; the fraction is fixed, not a setting. A metered profile with no
  reserve has no line and pauses nothing, as a subscription window without one does; the chip
  still shows its spend. Two profiles on one key with different amounts pause at different spends
  of the same bill, as two profiles on one login keep different lines against one number.
  **Credential lapse** (Not built — phase 3): adapter `credentials_ok()` false → don't start;
  running workers get a send when fresh credentials land (tdgrind's `.nudged` marker). **Stall**
  (Not built — phase 3): `working` with no output past `stall_after` → flag `stalled?`, send one
  prompt, then wrap up. **Exit reap** (Not built — phase 3): a worker whose tool exited sits on a
  sleep; reap it and keep the run log. **Worktree reap** (Not built — phase 3): run
  `reap_worktrees.sh` (or its generalized form) between lifecycles. **Stranded-work flag** (Not
  built — phase 3): any session going `idle`/`exited` with a dirty tree or unpushed commits is
  flagged in the team — the stranded-work audit, continuous. **PAUSE** flag and `on`/`off`/`off
  --now` semantics (Not built — phase 3): kept as agent RPCs.
- **Balance: a team over its line takes no new work**: the rule keys on exactly the numbers the
  team card and the Repo page show (§4.4 *Repo facts*), so what trips it is what a person has been
  watching. It is **off until a person sets it**: `teams.<team>.balance` (§5 `settings.yml`; the
  Settings page and `ao team balance`, §4.7), a setting and not a definition, since it is a number a
  person turns without redefining the team (ADR 2026-09-25 §5). Three lines, each optional, any one
  of them enough:
  - **`prs: n`** — the repo has more than `n` open pull requests, counted from the reading's
    `prs.open` as the card counts them: drafts included, whoever opened them;
  - **`oldest: d`** (`12h`, `2d`) — the oldest of those has been open longer than `d`;
  - **`review: true`** — the reader's queue is past its bound: the oldest pull request waiting
    at the team's techlead seat (`prs_waiting.oldest` on the seat's record, §4.9b *The reader*)
    has waited longer than the shortest `review.bound` a live member of the team carries, two
    hours where none carries one.

  **Who reads it** is the home's tick, never the manager's round: a session's `ao repo` cannot
  see the reader's queue (§4.7), and nothing mechanical waits on a round (*Keeping a team
  running*). After every repo reading and on every tick for the queue, the home reads the
  lines for each team that has the key, the team's repo being the registry root its live
  members' records name (`repo`; a team over two repos is read against each, either crossing
  counts, and every member is refused whichever repo it sits in). A team with no live member
  is not read, and a mark it carried goes: there is nobody to refuse. A team whose live members
  name no registry root has no repo to read, so `prs` and `oldest` cross nothing for it and only
  `review` can mark it (with `repo` empty) — never *cannot be told*, which would keep a mark
  standing for as long as the repo stays out of the registry. A crossing writes **`balance:
  {since, repo, crossed: [{line, value, limit}]}`** on the home's own `host` record under the
  team's name — the record rule 8 designs for `work_waiting`, which is `host.json` in the home,
  `{teams: {<team>: {balance}}}`, kept across a restart so a mark keeps its `since` — served with
  the `repos` reading, each checkout's reading carrying `balance: {<team>: mark}` for the marks
  whose `repo` it is, so a client and a session's `ao repo` read it there, and on the home's `host`
  read as `balance: {<team>: mark}`, every mark, one whose `repo` is empty included, which no
  checkout's reading holds: `ao team list`, `ao team balance`, the page's team card and the
  Settings page's **balance** field read both. The mark goes when no line is crossed. A crossed
  line's `value` and `limit` are open pull requests for `prs` and seconds for `oldest` and
  `review`, so a reader writes both sides alike (*oldest PR 3d, line 2d*); a mark still crossed
  keeps its `since` whatever its numbers do. **A reading that failed crosses nothing and clears
  nothing**: the mark stands as it was, as the chip's reading does.

  **What it does** is one thing: while the mark stands, **a new claim by an unattended member
  of that team is refused** — a record that is `unattended` and no seat; a seat's record is
  never refused, and a manager claims nothing — `ao progress claim TD-NNN` answers *ao-grind is
  over its line: 9 open PRs, the line is 8 (since 14:02). Take nothing new: finish, rebase or
  answer what is open of yours, then end your turn — you are told when the line clears.* The mark
  is read from the `host` record by the member's team, never from a repo's reading, since a
  `review` crossing may name no repo. It is refused in the step that would write it, as a lease
  is, and unlike a lease **`--force` does not pass it**: the line is the person's, and a session
  cannot move it. Not refused: a claim on a reference the claimer already holds (a renewal, its
  branch's derived claim included), a claim on a pull request (a PR number as the reference:
  reading or finishing one is what brings the count down), `done`, `dropped` and `restart`, and
  any claim by an interactive session, a person's own included (§9 invariant 5: policies leave
  those alone). **A refused member is not out of work**: `ao progress none` is refused to it while
  the mark stands, in the same words, so a team over its line idles and never winds down on it. A
  refusal, of a claim or of `none`, is kept on the record as **`balance_refused: {at, ref}`**
  (`ref` null for `none`) — only when the member itself asked; a person's `--id` claim on its
  record is refused alike and leaves nothing to ring — so the clearing can ring it; a claim or a
  `none` taken once the mark has gone removes it. While the mark stands the idle nudge and rule 6's
  lane news pass a record carrying it by — an entry left untold is told once the line clears — and
  rule 8 holds a start back with *its repo is over its line* as a fifth bound, reading the lines
  itself against the repos the team's records name, since the mark went with the team's last live
  member. Never a pause, never a wrap-up, never a kill: work in hand goes on, which is what clears
  the line.

  **Who is told**, once per crossing and once when it clears — a mark that comes and goes
  inside ten minutes tells once, not each time: a crossing within ten minutes of the last note
  of either kind is told only once it has stood until those ten minutes are up — by `system`
  note: the team's **manager** (the controller its members share), which logs the line in its
  round — *14:02 over the line: 9 open PRs, line 8; no new claims until it clears* — and does
  nothing else, since its members are neither crashed nor finished; and the **person**, FYI and
  uncounted. **The techlead is asked by what already asks it**: every pull request in the reader's
  queue is an `ask` on the seat, which the tick fills for a seat whose trigger is `asks` (rule 3),
  so a crossing of `review` needs no second message, and a crossing of `prs` or `oldest` with
  nothing in the queue is not the reader's to cure. What was last told is kept beside the mark,
  **`balance_told: {state, at, since}`** on the same team entry of the `host` record (`over` or
  `clear`, when the last note was sent, the mark's `since`), so a restart of the home tells nothing
  twice; it goes ten minutes after a clearing, and at once when the team has no live member — a
  team that wound down loses its mark with nobody to tell, and neither the manager nor the person
  is sent a clearing. The manager is the controller the team's unattended, non-seat members share,
  read from those that control no teammate, each address in the home's form (a node's member's is
  `id@host`); a team a person leads has none, and only the person is told. Nested teams are not
  built (§4.9), so this reading is one layer deep: a team in which a member leads members of its
  own tells the controller those bottom members share, which is that member and not the manager
  above it, and where two leads' members share none, only the person. When the mark goes, each
  member whose claim was refused is rung with *the line is clear again: pick as your lane says*,
  within its wake budget, and its `balance_refused` removed — an exited one's is removed with
  nothing sent.

  **What a person sees**: the team card's **over its line** note (§4.5a) and the same words
  in `ao team list` and `ao repo`; the Repo facet's numbers are the evidence, unchanged. **The
  designer's pull requests count**: one waiting on a steer's bound is open, so a team whose
  designer holds several for a night reaches `prs` sooner, and `n` is set with that in mind;
  the default the page offers when the rule is first turned on is `prs: 10`, `oldest: 2d`,
  `review: true`. **A node's member is checked too**: a claim is a report, which its node
  forwards to the home (§4.4a), so the home refuses it against its own mark, and on the clearing
  its note lands in the home's copy of its record, read at its next forwarded `inbox` or `wait`:
  an idle member on a node has no doorbell yet (§4.4a *The doorbell is the forwarded `wait`*), so
  it learns of the clearing when something else has it read its inbox — its manager, told of the
  clearing, is the one to look. A node cut off from the home refuses every report, so nothing is
  claimed unchecked. **The presets say it**: `grinder` says what a refusal *over its line* means —
  nothing new, finish what is held, end the turn, never `none`, and no branch cut for a new entry
  as a way round; `hunter`, which claims nothing, says the same of its `none`; `auditor` is a
  seat, never refused, and says nothing; and `manager` says such a team is neither crashed nor
  finished, that its part is one log line, and that an idle member on another host is told of the
  clearing with one send.
