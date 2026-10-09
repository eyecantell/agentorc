## 9. Invariants

1. Only the host agent creates, kills, or sends keys to an `ao-*` tmux session.
2. A directory has at most one agent session (`kind: interactive`, adapter other than `shell`;
   main checkout, worktree, or plain directory). Shells and command sessions are exempt. The
   anchor seat (§4.9b *The anchor seat*) is that one agent in a main checkout only while
   no person's session holds it: its fill is refused by occupancy as any `create` is.
3. Every session has a run log from its first byte.
4. A state shown as `hook` came from a hook; `scraped` is visible in the UI.
5. Interactive sessions (`kind: interactive`, `unattended: false`) are never paused, killed, or
   sent to by a policy, and never acted on by another session: an acting RPC from a session onto
   one — `set_controllers` included — is refused whatever the caller's grant and membership
   (§4.8). Only a person acts on an interactive session, and only a person hands one to
   unattended mode or to a controller. Flipping a session to interactive takes it out of every
   controller's reach on their next call; its `controllers` entries stay, inert. A **message** is
   not an act of control and is not refused by this invariant: it lands in the target's inbox
   and changes no state (§4.10), where the graph reaches a person's session at all; a session
   that wants the *person* writes to the org's person inbox instead. Mail to an interactive
   target **lands and never wakes**, whatever wake budget the general rule would allow: the
   mediator there is a person, and nothing starts a turn in their session but them.
6. The core never types a menu choice into a pane; permissions are answered through the hook,
   everything else in the terminal.
7. The host agent's edits to a repo's board file are always committed, never left in the tree.
8. A session's process is launched as the adapter's argv, never through the person's
   interactive shell (§4.1); tmux, not the host agent, holds the process.
9. Nothing keys on a session's role, team or project: policies key on `unattended`, `supervised` (§6), the
    schedule and a team's own settings (§5 `teams.<team>`: the stop time, the reserve, `on_work` — the badge says which sessions a person's setting is about, and grants nothing), acting RPCs key on grants and `controllers`, displays key on the report channels
   (§4.8). A preset sets defaults at start and is a badge afterwards; `team` and `project` are
   badges from the start (§4.9), and the Org page's grouping is derived from `controllers` and
   the badge on each tick, never stored. **One named exception:** the message gate's sideways
   edge admits a session carrying the same `team` badge (§4.10) — for a `manager: person` team
   there is no other edge between members. It gates mail only; nothing that acts keys on `team`.
   **A flow is not on the record** (§4.9c): the host agent never reads a flow, the record gains
   no flow or stage field, and no rule keys on a flow or on a stage's `name` — a flow compiles,
   at a client, into fields a start already writes (`lane`, `review`, `prompt_from`), and the
   tick reads those. `relaunch: {at, lane, review}`, `sit_out: {at}` and `closed_for: {why: sit_out}` are
   marks a person's act writes (the `relaunch` RPC, a person's own), and the tick reads them as
   marks — a restart's second trigger, a record to close and then pass over — never as a flow.
10. A report entry the session declared is never overwritten by one the host agent derived; a
    derived entry is shown as such, like a scraped state.
11. A session **acts on** another session only through the host agent, only with the `control`
    grant on its record, and only when the caller is in the target's `controllers` list (an
    empty list means nobody may act on it; §4.8) — and never when the target is
    interactive, whatever the list says (invariant 5). Grant and membership are both read from
    the records on every call, so a revoke or a membership edit takes effect on the session's
    next call and neither is cached. Reads are never gated, and a person at a terminal or the
    UI is not a session. Acting is what changes a session — the host agent's `ACTING_RPCS`:
    `send`, `keys`, `kill`, `close`, `set_mode`, `create`, `remove`, `decide`, `set_grants`,
    `set_controllers` and `set_stop` (`ao until`, §6). **Messaging is not acting** and does not
    pass through this gate: its own, weaker rule is §4.10's graph (my controllers, my members,
    my team, a shared target), it needs no grant, and it is refused by naming that rule rather
    than this one.
12. Within a scope (repo, or directory), a name identifies at most one session record: a live
    holder refuses a second, an exited or closed holder is superseded by it (§4.1). Suffixes
    exist only for tmux-level accidents and are then shown, never hidden.
13. A message is delivered to the recipient's inbox and may start a turn there, bounded by the
    recipient's wake budget: the mail-caused wakes — a doorbell, or `wait` returning on mail,
    each decided by the host agent when the recipient is next reachable — it may take in a
    rolling window, restored by time and by a person, never by anything a session does (§4.10).
    A spent budget makes a message land without waking — never dropped, never refused — and the
    difference is visible on the record and to the sender. That is the *recipient's* budget and
    *incoming* mail; a controller's `send` is never charged to it. A sender that needs the
    recipient's next turn to *be* its text is asking for an act of control and is bound by
    invariant 11. Whatever tells a session it has mail — a doorbell in its pane, a line on an
    `ao` reply — is fixed text carrying a count, never anything a sender wrote; no doorbell is
    typed into a person's session or into a pane whose composer cannot be read. Reading marks an
    entry read and never deletes it. Messages are coordination and die with the record;
    anything that must outlive the session belongs to the ledger, the board or a PR.
14. No session is stopped, and no team wound down, for lack of work by anything but the
    sessions' own declarations: the core never infers that work has run out (§4.9a).
    `out_of_work` is written only by the session it is about, through the ungated `progress`
    channel, and is never derived — alone among what the channels carry, it has no derived form,
    because every clause of the test is a judgement over prose the core cannot read. A worker
    that exits without declaring it is a crash and is restarted. `restart_wanted`
    (§4.9a) is the same kind of word under the same rule: the session's own, never derived, and acted on by its controller or, for a supervised member,
    by the host agent's tick under §6 *Keeping a team running* rule 2, never inferred by the core.
15. The org's **graph, intent and mail have one writer, the home host agent**; a session's
    **observed state has one writer, its node** (§4.4a). `controllers`, grants, team,
    `unattended`, `supervised` and the supervision marks (§6), stop time, reports, inboxes, `sends`,
    tallies, wake budgets and `suspended` (§4.8a) change only at the home, and every gate reads them there; `state`, pane, exit code,
    usage and `wrapup_sent_at` change only on the node that owns the session's tmux
    (invariant 1). Merges go by owner, never by last write. A request's identity is the channel
    it arrived on, never a field it carries — between hosts the link and its key (§4.4a), and
    on one host the connecting process's pane (§4.8a): *no caller* is a person only from outside
    every pane, and only on a host that carries one. Inside one OS account that is
    tamper-evidence, and the design says so; the wall is a node that carries no person. While a
    node's link is down its sessions neither send mail, act on another session nor create one —
    refused, visibly — its stopping policies keep running, and a person at that host may still
    act through it.

