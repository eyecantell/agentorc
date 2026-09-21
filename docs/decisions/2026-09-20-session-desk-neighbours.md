# ADR 2026-09-20: The neighbours the rename search tripped over — read, and what to take

Status: survey (2026-09-20, worker `tdgrind-ao-1`, TD-059). **Nothing here changes the design by
itself.** It names every project read, the commit read, and what each does that agentorc does not;
the two capabilities worth wanting are filed as TD-091 and TD-092, both for Paul to approve or strike,
and two lessons land as lines on existing entries (TD-072, TD-060). One finding was a live bug of
ours, confirmed by a neighbour that had hit it too, and is fixed by TD-090 (PR #325).

## Context

The rename search (TD-060, [ADR 2026-09-16](2026-09-16-rename.md)) kept landing on projects in this
niche: `agentorc`, `agentdirector` and `agentboss` were all taken. TD-059 asked for each to be read
for what it *does*, from its code rather than its README, and for the search to be widened past the
names found by accident. Design §3 already covers ttyd, ccmanager, claude-squad, Vibe Kanban,
agent-dashboard, Remote Control, herdr and OpenAI's Agents API; the messaging survey
([ADR 2026-09-16](2026-09-16-agent-messaging-prior-art.md)) covers Claude Code's messaging and
agent teams, mcp_agent_mail, muster, agent-mux and muxcode. None of those is redone here.

## Who holds the names

| Name | Holder | In the niche? |
|---|---|---|
| `agentorc` (npm) | versions 0.1.0–0.2.1 unpublished 2026-07-16; by its staging-version scheme and `@agentorc/sdk`'s README (`npm install agentorc`), a hosted *multi-agent workflow automation* service (agentorc.com, GitHub org `agentorc` with no public repos). Inference, strongly supported | no |
| `agentorc` (GitHub repos) | `jesintharnold/agentorc` (`e646b40a8a`: a Postgres + RabbitMQ task engine, no LLM); `anahika/AgentORC` (`78c8401776`) and `varunkumarnr/AgentORC` (`699fba45f8`, the same Flask tree): a no-code builder of LangChain ReAct pipelines | no |
| `agentorc` (PyPI, crates.io) | free | — |
| `agentdirector` | unhyphenated: free on npm, PyPI, crates.io and GitHub; the .com is parked. The real holder is **`agent-director`** (`gabemahoney/agent-director`, npm `agent-director` 0.8.0) | **yes** — read below |
| `agentboss` (npm) | `agentboss` 0.1.4 (tarball sha1 `4003be84774c4155964d369fec24f79033f3fd7c`; its GitHub repo is gone): after-the-fact analytics over Claude Code transcripts and OpenCode's database — active minutes, token cost at built-in prices, a "collab bill", five LLM-judged scores | no — it reads finished sessions and controls none |
| `agentboss` (GitHub) | `tallu-wonder/agentboss` | **yes** — read below |

## What was read

Search terms, run 2026-09-20 with `gh search repos "<term>" --sort stars --updated ">2026-01-01"`:
*tmux claude code sessions*, *claude code dashboard*, *claude code orchestrator*, *multi-agent coding
tmux*, *coding agent sessions web dashboard*, *claude code hooks dashboard*, *agent orchestration tmux
worktree*, *claude code session manager*, *multi-agent tmux*, *agents tmux worktree*, *claude code
hooks*, *coding agents web dashboard tmux*, *claude code tmux web*, and *claude code tmux* sorted by
update. Terms of four words or more mostly returned nothing. The five below are the in-niche
projects the search found that no earlier survey covers; two more were looked at and set aside
(`primeline-ai/claude-tmux-orchestration`, ~1.1k lines of bash whose README now recommends
subagents first; `disler/claude-code-hooks-multi-agent-observability`, an event viewer that
controls nothing).

| Project (commit read) | Shape | Has that agentorc lacks | Lacks that agentorc has |
|---|---|---|---|
| **tallu-wonder/agentboss** (MIT, Go, `c9acd757fa`) | Bubble Tea TUI, one tmux session per agent session, local only | **Codex** as a second agent (below); per-session **context** from the transcript, reset on a `compact_boundary` record; **cost** at built-in prices; **desktop notifications** that jump to the session; fork a conversation (`--resume --fork-session`); coloured groups and an Archived shelf | anything above one person at one terminal: a file lock allows one manager per home, every tmux call goes to the default server, no network code at all, no coordination, no unattended mode |
| **gabemahoney/agent-director** (MIT, Go, `595daec9f2`, v0.8.0) | headless supervisor for Claude Code "Spawns": CLI, stdio MCP server, Go library over one SQLite file; no daemon | a supervising **LLM over MCP** (spawn, status, send_keys, read_pane, kill, pause, resume, decide); liveness by **pid + process start time** with a three-way verdict (dead / alive / unknown); an append-only audit log that never records tool input | any authority model (any caller may decide, kill or type into any Spawn; the parent is a list filter); screen fallback, so no trust or limit detection; multi-host, teams, mail, reports |
| **yohey-w/multi-agent-shogun** (MIT, bash, `aff8dc8495`, ~1.4k stars) | a fixed three-level team in one tmux session: the person's pane, a lead, up to seven workers; Claude Code, Codex, Copilot, Kimi, OpenCode | a **Stop hook that refuses to stop while mail is unread**, feeding it back, and posting a completion report to the lead; an escalation ladder for unread mail (nudge, then Escape/Ctrl-C, then `/clear`); ntfy phone notifications | a UI (the dashboard is a markdown file), multi-host, configurable teams, grants, revival |
| **trillion-labs/claude-code-orchestrator** (MIT, TS, `ad6b257a4e`) | Next.js dashboard; runs `claude` in **stream-json** mode locally or over ssh, no tmux, no terminal | state and **cost** from the structured stream; permissions through an MCP permission tool; a manager session with a blocking `ask_worker` | a terminal a person can take over; sessions that outlive the server; hooks; grants |
| **physic-gun/tmux_claude_codex_dashboard** (MIT, JS, `4c41df481d`) | multi-user browser console over tmux, xterm.js over a websocket, systemd units beside a `tmux -N` server | hook state in a **tmux pane option**, updated compare-and-swap on the turn's id so an old event cannot overwrite a newer turn; Claude failure kinds (`rate_limit`, `billing_error`, `overloaded`) as states; image paste | multi-host, anything between sessions, unattended mode |
| **drewdrewthis/orchardist** (MIT, Rust/Go, `409d92d99f`) | command centre over worktrees, PRs, tmux and Claude sessions across machines: TUI, GraphQL daemon, desktop app | daemons **federated over ssh tunnels**; PR/CI state joined to each worktree; chat whose delivery is **verified in the recipient's transcript** | a web UI, unattended workers, supervision, grants, teams |

`kevin101681/claude-code-dashboard` (`5f255cb471`) was read too and is left out of the table: it has
**no licence file**, so nothing of it may be copied. Two ideas from it are recorded below as ideas
only — a context gauge, and tying a pane to its current conversation id through the status line.

**Nobody combines the pieces.** Hooks feeding state is the norm (every project above but
trillion-labs); two keep a screen scrape as fallback; two read the transcript or stream. Multi-host
exists twice (ssh exec, daemon federation), coordination three times (a file inbox with a Stop
hook, a manager MCP, transcript-verified chat). **Nothing anywhere resembles grants and
controllers** — the rule of which session may act on which — and nothing supervises unattended
workers under a lead with the person's answers followed to an outcome. That is the claim.

## (a) What they do that agentorc does not — and whether it is wanted

1. **A context gauge per session** (agentboss, and the unlicensed dashboard): the context in use,
   read from the transcript's last non-sidechain usage record and reset by a `compact_boundary`
   record's `postTokens`. agentorc shows a model and a profile's usage windows but nothing that says
   a session is about to compact or has just lost its memory — which is what a lead reading a worker
   that went quiet wants to know. **Wanted, filed as TD-091** (design first: it is a card and Focus
   field, §4.5a).
2. **A notification when something needs the person and the page is not open** (agentboss desktop,
   shogun and the unlicensed dashboard via ntfy/Pushover). agentorc's Inbox is where a person works
   from, but it reaches nobody who is not looking at it. **Wanted, filed as TD-092**, for Paul to
   choose the channel; web push from the UI needs no second service.
3. **Refuse to stop with unread mail** (shogun's Stop hook). This is TD-072's part 2 by a different
   road: a refusal at the moment the turn ends rather than at `ao progress none`. It is
   tool-specific (Claude Code's Stop hook can block), which §4.3 allows only inside an adapter.
   **A line on TD-072**, as a mechanism to weigh there, not a new entry.
4. **Cost in dollars** (agentboss, trillion-labs). **Not wanted**: on a subscription, dollars at list
   price describe nothing a person pays; the usage windows (TD-087) are the budget that binds.
5. **Fork a conversation** (agentboss). Not wanted now: nothing in the lead/worker model asks for a
   second session from the same point, and it is one `--fork-session` flag if it ever does.
6. **A supervising LLM over MCP** (agent-director, trillion-labs). Not wanted: `ao` is already the
   supervisor's interface and needs no MCP client, and agentorc stays tool-neutral (the messaging
   ADR's reasoning).
7. **Mail delivery verified in the recipient's transcript** (orchardist). Not needed: agentorc's
   mail is pulled — `ao inbox` marks it read — so *read* is already a fact the home records, not an
   inference from scrollback.

## (b) The same problem, solved differently

- **Compaction.** agent-director maps `SessionStart` to *waiting*, not working, and treats a
  `SessionEnd` of any reason but logout/exit as a refresh — it had hit the bug agentorc had (the
  attention board item of 2026-09-14). **Fixed here as TD-090**: a `SessionStart` with
  `source: compact` changes no state.
- **Liveness.** agentorc reads the tmux pane (§4.1); agent-director checks the recorded pid and its
  start time, so a reused pid is not mistaken for the session. agentorc has no pid to reuse — a
  pane is the unit — so nothing to take today; worth remembering if a session ever runs outside
  tmux.
- **Out-of-order hook events.** The tmux dashboard applies an event only if its turn id is not older
  than the stored one. agentorc applies hook events in arrival order, draining its offline queue in
  order, and stamps them at apply time; no sighting of a stale event winning exists here, so this is
  recorded, not filed.
- **Hook attribution.** agentboss accepts a hook report only if its conversation id matches, or its
  cwd is under the row's folder, because (its code comment says) Claude Code can hand a new terminal
  a pre-started process carrying another session's environment. agentorc trusts `AGENTORC_SESSION`
  alone. **Not verified here**, and agentorc starts every session's process itself in its own pane;
  if a hook is ever attributed to the wrong card, this is the first place to look.
- **Codex, for the second adapter (§7 phase 5).** agentboss derives Codex state from its rollout
  transcripts (`task_started` / `task_complete` / `turn_aborted`), which carry **no approval state**
  — so without Codex's `notify` hook a permission prompt never reads as needing the person — and
  `notify` is a **single slot** it must share by chaining the program it displaced. The
  conversation id appears only at a turn's end, so it adopts the newest rollout whose `cwd` equals
  the folder and whose start is within 30 s; two sessions started close together in one folder
  adopt the same one. An adapter author should start from those three failure modes.
- **Permission relay.** agent-director blocks in `PermissionRequest` as agentorc does, but defaults
  to **deny** on its timeout and sets no hook `timeout` in the settings it writes (so, by its code,
  Claude Code's default likely kills the hook first). agentorc's default — fall through to the
  terminal's own dialog, with a per-profile wait — is the better one; nothing to take.

## (c) The sentence for the README's first screen

> *agentorc runs a team of coding agents the way you would run people: sessions in tmux on any of
> your machines, one browser page to watch and answer them, leads that supervise unattended
> workers, and a rule for who may act on whom — so the agents can talk to each other and none of
> them can act on a session it was not given.*

The rename (TD-060) is where it lands; TD-060 carries a pointer here.

## What this ADR does not do

It approves nothing: TD-091 and TD-092 are filed open for Paul, and §3's table gains one row
pointing here. It re-reads nothing §3 or the messaging ADR already covered. The npm `agentorc`
attribution is an inference from a sibling package and is labelled as one.
