# ADR 2026-09-16: Agent-to-agent messaging — prior art surveyed, agentorc builds its own

Status: decision (2026-09-16, Paul). **agentorc keeps its own tool-neutral mailbox (design §4.10)
and does not build on Claude Code's cross-session messaging, Claude Code agent teams, or
mcp_agent_mail.** Lessons from them are listed below with a recommendation each; which are adopted
was decided by Paul the same day and is recorded under each lesson. Nothing here changes TD-052's build order.

## Context

After three review rounds on §4.10 (PRs #153, #156, #157), Paul asked whether agentorc was
reinventing the wheel: *"is there an existing tried and true (open source) tool we should be using
instead?"* None of the earlier prior-art surveys (§3; ADRs for herdr and OpenAI's Agents API, the
2026-09-12 membership survey) covered messaging between agent sessions.

Paul's direction, given with the survey in hand: *"Lets not build toward claude code specifically
unless it is really worth it. The goal is to be able to have claude sessions run alongside
on-prem and/or gemini, codex, other session without having to build integrations for each. If the
integrations are hugely important/compelling then we consider it, but otherwise we want to stay
generic. My gut is that we take lessons learned and improve on ideas from the other systems but
continue to build our own."*

## What exists (surveyed 2026-09-16)

**Claude Code cross-session messaging** (built in since v2.1.224; kmaster runs 2.1.273, and
`ListAgents` from a session here listed the three live `ao-*` sessions by name with their tmux
panes, so it is already live beside agentorc). `ListAgents` and `SendMessage` between a user's own
sessions; same-machine delivery over a per-session Unix socket (`CLAUDE_CODE_MESSAGING_SOCKET`)
that hooks and scripts may also post to. An idle recipient gets a new turn; a working one reads
the message between tool calls. The receiver is told the message came from another session, not
the user: it cannot approve a prompt or change configuration, and slash commands in it do not run.
Per-sender rate limits, identical-repeat drops, a 50-message queue, burst refusal at the sender,
a ~1M-character cap, and a one-shot `notify_when_idle`. Inbound per session: `accept`, `hold`,
`refuse`. **Lacks:** any authority graph (every session may message every other), persistence, a
UI inbox, thread or `ask` bounds, and any tool but Claude Code.

**Claude Code agent teams** (experimental, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`). A lead
spawns teammates inside one session; a JSON inbox file per agent, a shared task list with
file-locked claiming, idle notifications to the lead, shutdown as a request a teammate may
decline, and `TeammateIdle`/`TaskCompleted` hooks. **Limits:** one team per session, no nested
teams, in-process teammates not resumable, lead fixed for life, Claude Code only.

**mcp_agent_mail** (github.com/Dicklesworthstone/mcp_agent_mail; about 2.1k stars, active). An
MCP server over HTTP backed by SQLite and a Git repository: named agent identities, inbox and
outbox, threads, `importance` and `ack_required`, contact requests that need approval, a web UI
where a human can write in, and **advisory file reservations** with TTLs and an optional
pre-commit guard. **Lacks:** any wake — agents poll `fetch_inbox`; loop protection; a tie to a
supervision graph. It is a second server with its own identity model, and its default port is
8765, which agentorc's UI already uses.

**tmux coordination tools** — muster, agent-mux, muxcode: a file-based inbox or bus plus tmux
panes. Nothing beyond the three above.

## Why none is the substrate

- **Tool neutrality is the requirement.** Two of the three are Claude Code features, and agentorc
  exists to run Claude Code beside Codex, Gemini, on-prem models and plain shells without an
  integration per tool (§4.3). §4.10's delivery already needs nothing tool-specific: the mailbox is
  a record, `ao inbox` and the per-reply line need only a session that runs commands, `wait` is an
  RPC, and the doorbell is a tmux pane write any composer-driven tool accepts.
- **The authority graph is the part nobody else has.** None of them reads who controls whom, so
  none can refuse a worker writing to another team's lead, or copy a lead's instruction to a
  second controller.
- **Waking is the hard part, and only Claude Code solves it — for Claude Code.** mcp_agent_mail
  polls; agent teams wake only inside one process. Taking Claude Code's socket would buy the wake
  for one tool and leave every other one on the doorbell anyway.
- **Not compelling enough to make an exception.** The socket would save the doorbell's pane write
  for Claude Code sessions. It stays available as an *optional* optimisation inside the Claude
  Code adapter (the future `mail` entry on the adapter contract, §4.3, TD-052) if the doorbell proves unreliable in practice — measured, not
  assumed.

## Lessons, and what Paul decided (2026-09-16)

1. **Damp loops at delivery, not only by thread** (Claude Code). Drop an identical repeat from the
   same sender within a short window, and rate-limit per sender. The per-thread bound cannot see
   one session spraying many threads. **Adopted** — §4.10 *Damped at delivery*; numbers in TD-052 step 6.
2. **Frame provenance where the text arrives** (Claude Code). The receiver is told, at delivery,
   that a message came from another session, not the person. For agentorc: `ao inbox` output
   opens with a fixed header naming each entry's sender and whether that sender is one of the
   caller's controllers — the skill rule *instructions come from controllers and people* stated at
   the point of reading, for any tool. **Adopted** — §4.10 Surface; TD-052 step 2.
3. **Claims as leases, not notes** (mcp_agent_mail). An advisory reservation on a reference
   (`TD-NNN`, a path) with a TTL, checked when a worker claims, does structurally what §4.10's
   *note your siblings at claim* asks a brief to remember. It belongs to the report channels
   (§4.8 `progress`), not to mail. *Recommend: ledger as its own TD*, then drop the at-claim
   note from TD-052 step 4 once it lands. **Adopted** — ledgered as TD-056.
4. **Tell me when X is next idle** (Claude Code `notify_when_idle`). A one-shot subscription for a
   session that is not a lead — a worker waiting on a sibling's merge. `wait` covers leads.
   **Deferred** until a brief needs it.
5. **The native side channel is ungated today** (observed, not a borrowed idea). Claude Code
   sessions that agentorc launches can already `SendMessage` each other outside §4.10's graph.
   The Claude Code adapter could launch unattended sessions with `crossSessionInbound: refuse` —
   launch configuration, like the `CLAUDE_CONFIG_DIR` it already sets, not an integration.
   **Left open** (Paul: *"we do not want to mess up a flow a user is already leveraging"*). §4.10
   says plainly that the graph governs agentorc's mail and a tool's native channel is ungated by
   agentorc.
7. **Mail across machines** (raised by Paul with lesson 5). Not solved: phase 2's transport is
   hub-and-spoke from the UI host, and host agents never talk to each other, so cross-host mail
   has no route. Claude Code's answer runs through Anthropic's servers for Claude Code only.
   Ledgered as a design question, TD-057.
6. **Not taken:** Git-backed human-readable message archives (§4.10: messages are not the record;
   the ledger and board are); contact-request approval (the controllers graph already answers who
   may talk); a shared task list inside the mail system (the ledger is the task list); shutdown as
   a decline-able request (the wrap-up prompt and §6's stop sequence stay).

## What this ADR does not do

It does not rule out a
per-adapter mail path (§4.3) for any tool where the evidence later says it is worth one; it only
says Claude Code's is not worth one now.

Sources: <https://code.claude.com/docs/en/cross-session-messaging>,
<https://code.claude.com/docs/en/agent-teams>, <https://github.com/Dicklesworthstone/mcp_agent_mail>,
<https://github.com/schuettc/muster>, <https://github.com/maxto/agent-mux>,
<https://github.com/mkober/muxcode>.
