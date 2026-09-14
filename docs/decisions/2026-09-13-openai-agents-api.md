# ADR 2026-09-13: OpenAI's Agents API — evaluated, not adopted

Status: decision (2026-09-13). **agentorc does not build on the Agents API and does not ship an
adapter for it in any current phase.** Four lessons are taken into the design as vocabulary and
confirmation (§5 below); the prior-art table (§3) gains a row and §4.5c a dated amendment.
Nothing here changes what phase 1 or phase 2 builds.

## Context

Paul asked on 2026-09-13 whether OpenAI's newly released "agents API" is something this project
should leverage — use, or take lessons from. This is the second such question in four days
(herdr, [ADR 2026-09-10](2026-09-10-herdr-spike.md)), and the two have the same shape: a
well-funded thing that runs coding agents has appeared, and the design must say in writing why
it is or is not the substrate.

## What it is (surveyed 2026-09-13, from OpenAI's own documentation)

Public beta since **2026-09-10**. It exposes OpenAI's managed **Codex harness** as a service.
Four primitives:

| primitive | what it is |
|---|---|
| **Agent** | model, instructions, tools, MCP servers |
| **Environment** | an optional sandbox where the agent reads files and runs commands |
| **Session** | a durable instance carrying config, conversation and saved work across turns |
| **Events and items** | inputs sent to the agent and outputs it produced |

Mechanics that matter to this design:

- A **turn** is one cycle of work. A message to an *idle* session starts a new turn; a message
  during an *active* turn **steers** it. Turns run asynchronously.
- Progress arrives by **streaming** (`stream: true`) or **webhooks**. Terminal events are
  `agent.session.turn.completed` / `.failed` / `.cancelled`, plus `agent.session.failed`,
  `agent.session.environment.failed`, and `error`.
- A completed turn may carry **`required_actions`** — structured work the caller must handle
  before the agent can continue.
- **Streams do not replay.** After a disconnect you retrieve the session and its items to
  recover; there is no missed-event replay.
- Managed **context-window compaction by summarisation**, **multi-agent delegation to
  subagents**, **session resumption**, artifact production, MCP.
- Sandboxes: OpenAI-hosted, your own, or partners (Cloudflare, Vercel, Oracle, E2B, Modal,
  Daytona, DigitalOcean, Blaxel, Runloop). Token billing only, no infrastructure fee.
- Constraints: **US-only data residency, and no Zero Data Retention — not even on a
  self-hosted sandbox.**
- Context: the **Assistants API**, OpenAI's previous stateful agent primitive, **sunset
  2026-08-26**, two weeks before this opened.

Sources: <https://developers.openai.com/api/docs/guides/agents-api/overview>,
<https://developers.openai.com/api/docs/guides/agents-api/sessions>,
<https://developers.openai.com/api/docs/guides/agents-api/observability>,
<https://www.infoworld.com/article/4221163/openai-launches-managed-agents-api-to-simplify-enterprise-ai-agent-development.html>,
<https://the-decoder.com/openais-new-agents-api-gives-developers-the-infrastructure-behind-codex-and-chatgpt/>.

## Why it is not the substrate

**It solves the other half of the problem.** Goals 2, 4, 6, 7 and 14 and §4.1 are about
*interactive sessions on machines you own*: a tmux pane you attach to, a keyboard that passes
through so menus and questions are answered in the terminal exactly as in VS Code, a VS Code
link into the same directory, a git worktree, a continuous run log. An Agents API session has no
pty, no pane, no local checkout, and runs only OpenAI models. It cannot host the Claude Code
session that phase 1 exists to supervise. Adopting it would not replace tmux; it would add a
second, weaker kind of session beside it.

**The adapter contract does not fit, and bending it is a project.** §4.3's `Adapter` protocol is
built around a process in a pane — `launch_cmd` returns argv, `classify_pane(tail)` and
`composer(tail_raw)` read the screen, and prompt injection is core's `tmux load-buffer` +
`paste-buffer -p`. An Agents API session supplies none of those. It would be the first
paneless, API-backed adapter: no Focus terminal, no `tmux attach`, no **Open shell here**, no
scrollback (§4.6, TD-022), no `Copy tmux command`. That is a fork of the contract in exchange
for supervising cloud agents the person is not running on their own hosts.

**Its constraints are disqualifying for the hosted direction.** US-only residency and no ZDR
*even on your own sandbox* means an agentorc that depended on it could not be offered to anyone
with a data-residency or retention requirement — while §4.5b's whole premise is that the relay
sees only what the UI sees and the person's work stays on the person's host.

**The Assistants sunset is the sharpest datum.** A supplier's stateful agent primitive had a
roughly two-year life and was retired a fortnight before its replacement reached general
availability. Goal 9 ("framework, not a Claude tool") and §8's "state from the tool, not from
the screen" both assume the *tool* is the durable thing and agentorc is the neutral layer; a
vendor-hosted session object is the least durable place this design could put its state.

## Lessons taken

Four, all cheap, and three of them confirmations of decisions already made:

1. **`session` / `turn`, and *steer* as the verb.** "A message to an idle session starts a turn;
   a message during an active turn steers it" is a better name for the rule §4.3 already
   implements — Send stays enabled while the agent works, because a hook-fed tool like Claude Code queues input (scraped adapters also disable it while a foreground process runs).
   **Take the vocabulary**: the Focus composer and the card should distinguish steering a
   running turn from starting a new one. Ledgered as a design-wording item, not a behaviour
   change.
2. **`required_actions` as a structured payload, not a state flag.** This is precisely the gap
   §3 records against herdr — its `pane.agent_status_changed` carries only the state, so a
   permission, a question and the trust dialog look alike from outside. agentorc already carries
   the pending question's *text* with the state. **Confirmation**, plus a usable name.
3. **Splitting `session.failed` from `session.environment.failed`.** The same cut agentorc makes
   between `exited` (the tool stopped) and `unreachable` (the machine or the agent stopped),
   including the volatile-host rule of goal 13. **Confirmation** that keeping them apart is
   right.
4. **"Streams do not replay; retrieve the session and its items."** Identical to the reconnect
   contract in §4.6: the pty lives in the UI process, the browser reconnects with backoff, a
   fresh `tmux attach` redraws the current screen, and **nothing is replayed from the run log**
   (TD-029). Two independent designs reaching the same rule is good evidence for it.
   Their item-level stream events (message generated, tool ran — not tokens) are also the right
   granularity model for the Org card tail versus the raw pty in Focus.

**Explicitly not taken:** managed context compaction. Context is the coding tool's business, not
the supervisor's; owning it would break the neutrality §4.5c calls the moat.

## Consequence for positioning

Agents API, Anthropic's Managed Agents, AWS Bedrock AgentCore and Microsoft Foundry Agent
Service now all sell a managed cloud agent runtime on token billing with no infrastructure fee.
§4.5c already says a hosted "run Claude Code for you" is what every model supplier sells with
its own login and price, and that neutrality is the moat; as of 2026-09-13 that is no longer a
prediction. The amendment recorded in §4.5c says so and names the four.

## What this ADR does not do

It does not close or open a §10 question, and it does not ledger implementation work beyond the
§4.3/§4.5 wording item. If a Codex-CLI adapter is built (§4.3 lists it as *scraped until
verified*), it adapts the **CLI in a pane**, not this API — the two share a harness and nothing
else that matters here.
