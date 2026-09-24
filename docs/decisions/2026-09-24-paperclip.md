# ADR 2026-09-24: Paperclip — the "AI company" neighbour, read from its code

Status: survey (2026-09-24, session `research_paperclip`, at Paul's request). **Nothing here changes
the design by itself.** It says what Paperclip is, where agentorc differs, and six things worth
weighing; none is filed as a TD until Paul picks.

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
5. **Machines the person owns, sessions that outlive the server.** A Paperclip restart ends its
   runs and reconstructs them from the database. An agentorc restart leaves every tmux session
   running.

**What is *not* a differentiator any more:** §4.5c names *"one neutral view across tools"* as the
moat. Paperclip already drives twelve runtimes. Against it, neutrality is table stakes; the
differentiator is the **live, consented, git-native** session. §4.5c should say so if Paul agrees.

## What to learn — six things worth weighing

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
   reserve without new fields. *Candidate TD, for Paul.*
4. **Decision queues with training examples.** Paperclip records the person's past decisions as
   `decision_training_examples` and has agent-raised option prompts with expiry and auto-answer rules
   (`decisions`, `decision_queues`, `DecisionQueuePage.tsx`). This is the §4.9b techlead's problem,
   which is what to answer without the person, solved with an explicit record of precedent. *Read
   only at the schema level here; worth a deeper read before the techlead's next change.*
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
*"a warm process persists across wakes by default"*, when `warmHandleIdleMs` defaults to 0. The
founder's identity, the star history and the praise and criticism in secondary coverage are not
verified and are left out.

## What this ADR does not do

It approves nothing. The §3 table gains one row pointing here. Items 3 and 4 are candidates Paul
may file or strike; items 1, 2, 5 and 6 are notes for whoever next edits those sections.
