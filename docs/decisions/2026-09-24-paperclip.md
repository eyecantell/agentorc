# ADR 2026-09-24: Paperclip — the "AI company" neighbour, read from its code

Status: survey (2026-09-24, session `research_paperclip`, at Paul's request). **Nothing here changes
the design by itself.** It says what Paperclip is, where agentorc differs, and seven things worth
weighing; one is filed (TD-128, at Paul's word), the rest wait on Paul.

## Context

Paul heard of [Paperclip](https://paperclip.ing/) and asked how it stacks up. It is the product the
Org/Team ADR ([2026-09-13](2026-09-13-org-teams-projects.md)) had in mind when it declined the
"autonomous company" framing: an org chart of agents, with a CEO at the top. Three Sonnet agents
read the site and docs, the code, and its public reception. The anchor session re-checked every
claim below against the code or an API, because two of the agents' claims were wrong (see *What was
checked*).

## What it is

| | |
|---|---|
| Source | `paperclipai/paperclip`, MIT, commit `0f8750627f` (2026-09-24); repo created 2026-03-02; releases dated `v2026.MMDD.N`, roughly weekly, with breaking changes as late as 2026-09-16 (the 2026-09-21 release is a non-breaking patch) |
| Size | ~1.19M lines of TypeScript excluding tests (2.19M with them), ~66k lines of Rust in an experimental runner, 138 Drizzle schema files; `server/src/services/heartbeat.ts` alone is 29,619 lines |
| Shape | one Node/Express server, Postgres (embedded PGlite by default), React UI, CLI `paperclipai`; deploy modes `local_trusted` (loopback, no login), `authenticated`+`private` (LAN/Tailscale), `authenticated`+`public` |
| Traction | GitHub API: 82,018 stars, 15,015 forks, 5,625 open issues. HN (Algolia): five paperclip.ing submissions, 1–5 points, **no comments** on any. Secondary coverage is mostly templated SEO articles that disagree about basic facts. **The stars are not evidence of use**; one maintainer (`cryppadotta`, 2,834 commits) dominates |
| Pitch, by date | 2026-03 *"open-source orchestration for zero-human companies"* → 2026-05 *"the human control plane for AI labor"* → 2026-09-24 *"meta-harness for your agents"* (HN titles). It is moving toward agentorc's ground |

**Vocabulary.** A *company* (tenant, with a monthly budget) has *agents* in an org chart
(`agents.reportsTo`), *goals* in a tree (mission → project goal → agent goal), and *issues*
(tickets) in trees, with comments, `blocks` relations, approvals and question/answer interactions.
Work is done in *heartbeat runs*: an agent wakes, acts on its issue and exits.

**How it drives an agent.** A wake request (a comment, an answer, an assignment, a scheduler tick)
becomes a `heartbeat_runs` row. The adapter spawns the vendor CLI, by default over the Agent Client
Protocol (ACP) with a CLI fallback, and passes `--resume <session id>` to continue the conversation.
It reads structured events rather than a screen, and exits. *"Paperclip does not keep an agent
process alive between turns. A heartbeat run is finite"* (`doc/architecture/durable-continuation-scheduler.md`).
ACP's `warmHandleIdleMs` defaults to 0. There are twelve adapters (Claude Code, Codex, Cursor
local/cloud, Gemini, Grok, Kimi, OpenCode, Pi, Hermes, OpenClaw) plus generic `process` and `http`
adapters. The Claude adapter **defaults `dangerouslySkipPermissions: true`**
(`packages/adapters/claude-local/src/index.ts:67`). Instead of per-action consent, safety rests on
budgets, approvals and completion checks.

**Governance.** It has a flat capability ACL (`principal_permission_grants`: principal, permission,
scope). A manager may take over an issue checked out by anyone below it in the org chart
(`authorization.ts:2277`, `allow_manager_chain`). Budgets are generic policies (`scope, metric,
windowKind, amount, warnPercent 80, hardStopEnabled`), with the metric defaulting to `billed_cents`.
An `activity_log` table and per-run event logs record what happened.

**Where work runs.** Each issue or run gets an `execution_workspaces` row: a git worktree or a
sandbox (Daytona, Modal, SSH/Railway targets), leased, with lineage and cleanup. The control plane
is one server.

## How it stacks up

The two share a surface — a board of agents, an inbox, budgets, worktrees, many runtimes — but not
the same unit of work. **Paperclip's unit is a ticket and an agent is a batch job that works it;
agentorc's unit is a live session and a ticket is whatever the repo's ledger says.**

| | Paperclip | agentorc |
|---|---|---|
| An agent is | a finite run per wake, resumed by session id | a long-lived interactive session in tmux (§4.1) |
| The person intervenes by | commenting on the issue, answering an interaction, approving, pausing a subtree; then the next wake | typing into the live terminal in Focus, answering the real dialog, mail (§4.5, §4.10) |
| Per-action consent | off by default (`--dangerously-skip-permissions`) | the hook relays permission prompts to the Inbox; policy per profile (§4.2) |
| State comes from | the run's structured output and exit | Claude Code hooks, with a labelled screen fallback (§4.2) |
| Work lives in | Postgres: issues, goals, comments | the repo: the ledger, the attention board, PRs, commits (dev-cadence) |
| Coordination | issue comments and child issues; no mailbox | mail between sessions (§4.10) |
| Who may act on whom | a capability ACL, plus the manager chain for checkouts | `controllers` and grants on every act of control (§4.8, §9) |
| Budget | dollars per scope and window, hard stop | subscription usage windows and reserves per profile (§6 *Usage gate*, TD-100) |
| Hosts | one control plane; remote *execution* in sandboxes | a home and nodes; sessions live on the person's machines and outlive the UI (§4.4a) |
| Audience | anyone "running a company": marketing, content, and dev as one role | a developer running coding agents on their own repos |
| Weight | ~1.2M lines, 138 tables | one Python distribution |

## How agentorc differentiates

1. **A session the person can take over at any moment.** The strongest differentiator is having
   nothing between the person and the agent. Paperclip has no terminal to attach to, because
   between wakes there is no process. When an agent is going wrong mid-turn, agentorc lets the
   person see it and type; Paperclip's person gets a comment box and the next heartbeat.
2. **Consent stays on by default.** Paperclip's default skips every permission prompt and
   governs afterwards. agentorc's hooks put the prompt in front of the person or a lead.
3. **Work lands in git, not a database.** Paperclip's ledger is its Postgres; delete the install and
   the history of what was decided goes with it. agentorc's cadence makes every session's work a
   commit, a branch, a ledger line and a board item (§4.5c's "cadence-as-a-product").
4. **The subscription is the budget.** Paperclip counts cents. Its Claude adapter does detect *5-hour*
   and *weekly limit reached* wording (`parse.ts:24`), but only as a run failure, not as a window to
   plan work around.
5. **Sessions that outlive the server.** Both run on machines the person owns; that is not a
   difference. A Paperclip restart ends its runs and reconstructs them from the database. An
   agentorc restart leaves every tmux session running, mid-thought.

**What §4.5c claims, read against Paperclip.** §4.5c says what no model vendor sells is *"one
neutral view across tools, on machines you own, with policies and a working cadence that never
strands work"*, and that *"neutrality is the moat"* — in plain words, that working with any vendor's
tool is the thing nobody else offers. Paperclip is not a model vendor, and it offers the first three
of those four: twelve runtimes, self-hosted, budgets and approvals. Its cadence is the fourth, and
differs in kind: work that never strands because a database reconciler re-wakes it, against work that
never strands because it is a commit, a branch and a ledger line. So against Paperclip, being
tool-neutral is necessary and not distinctive; what it cannot offer by construction is the five
points above — a **live** session, **consent** before an act, and work that lands **in git**. §4.5c
was written against the vendors' runtimes and is right about them; whether it should also name this
is Paul's call, and this ADR changes no design text.

## What to learn — seven things worth weighing

1. **Continuation reconstructed from records, with a written postmortem.** Paperclip keeps no
   intent in memory. Every tick, a reconciler finds *assigned + in progress + no live run + no wait*
   and wakes the agent again. Its postmortem (DOT-2, same doc) is the cautionary half: a run kept
   reporting `done`, a classifier kept rejecting the evidence, and the reconciler re-woke it every
   30 s with a prompt that lacked the wake comment, so it repeated itself. **For agentorc:** the
   TD-103 tick policies (crash restart, the idle nudge) are the same kind of reconciler. Our ceilings
   (`RESTART_CEILING`, the one-nudge rule) are the damping Paperclip lacked, so DOT-2 is worth
   citing in §6 as the failure those ceilings prevent. *A line on §6, not a TD.*
2. **Wake admission as a pure function.** `server/src/modules/wake-queue/domain/policy.ts` decides,
   for a trigger that arrives while a run is going, whether to *proceed*, *coalesce* it into the
   running turn, or *defer* it, and tests that decision without I/O. agentorc's equivalent (mail or
   a steer to a busy session, §4.10) is spread across delivery code. *Worth a look when §4.10's
   delivery is next touched.*
3. **Budget policy as one generic row** (scope, metric, window, warn at 80 %, hard stop, notify).
   agentorc's reserves (§6 *Usage gate*, TD-100) are per-profile fields. The generic shape, with a
   *utilisation of a usage window* metric instead of cents, would let a team or a project carry a
   reserve without new fields. *Filed with metered billing as TD-128.*
4. **Decisions: propose instead of act.** Shipped as Decisions v1 in `v2026.817.0`
   (`server/src/services/decisions.ts`, `decision-queues.ts`; `DecisionQueuePage.tsx`). An agent
   that would take an action with consequences instead files a *decision*: a title, a body, typed
   *options*, each carrying the *effects* the server will run if it is chosen (`comment_on_issue`,
   `assign_issue`, `create_issue`, `cancel_issue_tree`), optional typed *inputs*, and an `expiresAt`.
   Three details matter. **The spec is signed at creation** (`decision-signing.ts`), so what the
   person approves is exactly what runs. **Each effect runs only if both the proposing agent and the
   deciding person may perform it** (`deny_decision_intersection`, `decisions.ts:458`), so approving
   never widens anyone's authority. **The targets are snapshotted when proposed**; a `strict` effect
   whose target changed since then is skipped as `target_changed` (`decisions.ts:465`) rather than
   run against a world the person never saw. *Decision queues* group them and fill themselves from
   three seed signals (a PR on the issue, a plan waiting for confirmation, an agent's questions).
   Triage gives each a `decide_by` date, a snooze and a responsible person, and the attention feed
   ranks *decide now* first. *Not what it first looked like:* `decision_training_examples` is a
   snapshot library for reviewing how agents decided. It auto-answers nothing, was experimental, and
   its UI was removed in `v2026.824.0`. **For agentorc — a narrower lesson than it first read.**
   An `ask` or a `steer` already carries up to four **suggested answers** beside free-text Reply
   (§4.10 *Suggested answers*, TD-070, the Inbox half built 2026-09-20; the board half waits on
   dev-cadence), so "one press on a phone" is not the gap. The difference is **what a press does**:
   here it sends *that text* as a `reply` to the sender, which reads it and acts — the same RPC, gate
   and wake as a typed reply, so a button can say nothing Reply could not (§4.5a: nothing on the page
   is built from a session's text except as text); in Paperclip a press runs server-side *effects*
   with no agent in between. Ours is the more robust choice and stays: the sender keeps the context,
   free text is always open, and no act rides a label. Of Paperclip's three details, the intersection
   rule has no work here (a reply is not an act of control; the acts are gated by grants already),
   `expiresAt` is declined by design (an `ask` to the person does not expire, §4.10), and only the
   **staleness check** carries over: an `ask` whose `about` names a PR or a branch that moved after
   it was sent could say so on the row (*#512 has new commits since this was asked*), so an answer
   is never given to a question the world has overtaken. *A line for TD-070's step 4 or the Inbox
   rows, for Paul to file or strike; not a new mechanism.*
7. **Two layers in a message to the person.** Paperclip's `v2026.817.0` rewrote its system notices
   as *compact rows with evidence on demand* and moved review, recovery and blocked notices to
   plain language; its attention feed ranks *decide now* and *new today* first and shelves what has
   aged. That is TD-127's ask (Paul, 2026-09-24: the basic context and the decision, the gritty
   details behind a *details* control) arrived at by another road. *A pointer on TD-127, nothing
   more; the shape rule there is already the right one.*
5. **Credential ownership stated per adapter.** Each Paperclip adapter doc says
   whether the host owns the login or a snapshot carries it into the sandbox. agentorc meets the
   same question for container nodes (§4.4a) and profiles (§4.2a). *One table in §4.2a, when the
   second adapter lands (TD-112).*
6. **Named deploy modes.** `local_trusted` / `authenticated+private` / `authenticated+public` puts
   what §4.5b describes in prose into three names a user can pick. *Adopt the naming when §4.5b is
   next edited.*

**Not wanted:** the org chart as the organising noun (the Org/Team ADR's reasoning stands), dollar
budgets (the neighbours ADR's reasoning), per-issue database tickets replacing the repo ledger, and
skip-permissions as a default. The size is a warning in itself: a 29,600-line dispatch service and a
long run of `*-recovery`, `*-watchdog` and `*-liveness` services are what a system accumulates when
every agent is a batch job and every continuation has to be reconstructed.

## What was checked

The anchor re-read the code for every claim above. Two agent claims were wrong and are corrected
here: the size (reported as ~64k lines of TypeScript; it is ~1.2M excluding tests) and *"the org
chart is not enforced"* (`allow_manager_chain` enforces it for checkouts). A third was overstated:
*"a warm process persists across wakes by default"*, when `warmHandleIdleMs` defaults to 0. Two more
were the anchor's own, corrected the same day: the first draft of item 4 said the decision queues
learn from the person's past decisions, and they do not; the second draft said an `ask` here is
answered only in prose, and Paul pointed at TD-070's suggested answers — item 4 is now the narrower
lesson. Paperclip's one terminal (`environment-custom-image-terminal-ws.ts`) is a shell into a sandbox
image while it is being set up, not onto a running agent, so *no terminal to attach to* stands. The
founder's identity, the star history and the praise and criticism in secondary coverage are not
verified and are left out.

## What this ADR does not do

It approves nothing. The §3 table gains one row pointing here. Item 3 is part of TD-128 (Paul,
2026-09-24: budgets for pay-per-token profiles); item 4's staleness line is for Paul to file on
TD-070 or strike; item 7 is a pointer for TD-127's designer; items 1, 2, 5 and 6 are notes for
whoever next edits those sections. The §4.5c reading above is an argument, not an edit.
