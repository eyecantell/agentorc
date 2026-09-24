# ADR 2026-09-24: Cloud sessions — can a claude.ai/code session join the org?

Status: research (2026-09-24, a cloud session on claude.ai/code, at Paul's request). **Nothing
here changes the design by itself.** It says what a cloud session is, measured from inside one,
which of the org's benefits it can have and at what cost, and recommends one shape; the decisions
at the end wait on Paul.

## Context

Paul runs some agentorc sessions on claude.ai/code ("Claude Code on the web", the harness
Anthropic calls Claude Code Remote). Such a session runs in an Anthropic-hosted container on a
fresh clone of the repo, on a `claude/*` branch, and is driven from the app. The question: can it
get what a session on kmaster gets — dev-cadence, the techlead's review before merge, mail with
the team and the person — and how?

The design already has a row for it (§3): *Anthropic Remote Control / cloud sessions — single-session
sync, Claude only — not an org view, not self-hosted*. This note is the measurement behind a
decision on that row.

## What a cloud session is, measured

Every line below was checked from inside the session that wrote this note (2026-09-24, Claude
Code 2.1.282, `entrypoint: remote`), not taken from documentation, except where a document is
cited.

| | On kmaster (design) | In a cloud session |
|---|---|---|
| Process | `claude` in an `ao-*` tmux pane the host agent created (§4.1, invariant 1) | `claude` is a direct child of the harness runner. No tmux server runs; tmux is installed but nothing is in it. Claude Code's own registry (`~/.claude/sessions/<pid>.json`) lists it, which is the one thing the adapter could read |
| State feed | hooks to the host agent's socket (§4.2) | hooks from the repo's `.claude/settings.json` run exactly as locally: the cadence SessionStart hook ran and printed the overdue board item. User-level `~/.claude/settings.json` does not apply ([hooks](https://code.claude.com/docs/en/hooks.md)). The harness wires its own Stop hook beside them (`~/.claude/launcher-settings.json`) |
| Acts on it (send, keys, kill, doorbell, decide) | on the pane | **nothing to act on**: no pane. The harness's own in-session tools can message a session and read its status bucket (below), and that is all |
| Route home | a node dials the home over ssh or a per-node socket, JSON lines over a byte pipe (§4.4a) | **none**: egress is an allowlist through an HTTPS proxy — an arbitrary host answers 403; the proxy's own README rules out WebSocket upgrades, raw TCP, ssh and non-443 ports. A Custom allowlist exists per environment ([network access](https://code.claude.com/docs/en/cloud-environments.md#network-access)), so a tunnel hostname could be allowed, but the link's byte-pipe transport cannot cross an HTTP request/response proxy as written |
| Lifetime | tmux owns the process; a host-agent restart loses nothing | the VM is reclaimed on inactivity; a daemon started by a setup script or SessionStart dies with it; background work is not restored on reopen ([environment expired](https://code.claude.com/docs/en/claude-code-on-the-web.md#environment-expired)) |
| Checkout | a worktree, or the anchor's checkout (invariant 2) | a fresh clone per session on its own branch: the anchor rule is met by construction. The cadence's hooksPath warning fires (no pre-push guard) and is noise here: the harness pushes only to its `claude/*` branch |
| Merge path | branch → PR → squash, `/cadence` over `gh` (cadence §4) | branch → PR works through the GitHub MCP tools; **`gh` is absent** in this session (the docs list it among installed tools; this container had the MCP instead), so `/cadence` and the review-evidence comment as scripted do not run here |
| Python | 3.12+ | 3.12 and 3.13 present, so `ao` is installable; it would have nothing to talk to |

**What the harness exposes, and to whom.** Inside a session, a *Claude Code Remote* MCP offers
`create_session`, `send_message` to another session, `get_session` (a status bucket:
working / blocked / review ready / completed / failed, the branch, context usage), scheduled
triggers, a one-shot `send_later`, and an inbound-webhook `watch_url`. A guide agent checked the
documentation the same day: **none of it is documented for callers outside a Claude Code session**,
the feature request for an external `create_session` was closed as not planned
([anthropics/claude-code#66126](https://github.com/anthropics/claude-code/issues/66126)), and
cross-session messaging is documented only session-to-session
([cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging.md)). What *is*
documented from outside: `claude --cloud "<prompt>"` starts a cloud session from a terminal and
prints its id and URL; `claude --teleport <id>` pulls one into a terminal as an independent copy
([web](https://code.claude.com/docs/en/claude-code-on-the-web.md)). Remote Control is a different
feature — a **local** pane steered from the app — and §4.2 already handles its takeover banner.
Anthropic's documented API for an externally driven agent is Managed Agents (`POST /v1/sessions`,
events, an SSE stream), which is the OpenAI Agents API shape §3 rejected as a substrate: a
vendor-hosted session with no pane, no tmux and no checkout of yours. It answers a different
question and is not weighed further here.

So a cloud session is nearest the design's **container node** (§4.4a) with the two things that make
a node a node removed: a pane and a link. It is a session the org can neither see nor act on, whose
one durable product is a pull request on the repo.

## The benefits, one by one

| Benefit | In a cloud session today | What it would take |
|---|---|---|
| SessionStart cadence hooks: board nudges, cadence-change relay, memory guard | works | nothing |
| Anchor rule, worktree isolation | met by construction | nothing |
| Ledger before idle, board items | the files are there to edit; **but a board line committed on the session's branch reaches nobody until that branch merges** (below) | — |
| Branch → PR → squash | works, through the GitHub MCP | a `gh`-less path for `/cadence`, or run `/cadence` from kmaster on the PR's number |
| The techlead's read before merge (§4.9b *The reader*) | **not automatic**: the reader's queue is its inbox, filled by an `ask` with `pr` that the author sends, and a cloud session cannot send one | a way for a PR nobody asked about to enter the seat's queue (shape 3) |
| `ao msg`, `ao inbox`, the doorbell, `ao wait` | impossible: no link, no pane | an HTTPS link transport and a pane-less session kind — new design, blocked by Anthropic's egress rules today (shape 1) |
| A manager's control: send, wrap-up, stop time, kill | impossible | the harness's undocumented in-session tools (shape 2) |

## Three shapes

1. **The cloud session as a node.** Install the host agent in the container from a setup script,
   dial home. Blocked twice: the environment's allowlist would need the tunnel hostname, and the
   link (§4.4a *Frames*) needs a request/response HTTPS transport that does not exist. Past both,
   a node with no pane can report and mail but never be steered or rung, and a VM reclaimed on
   idle is §4.4a's offline node for most of its life. Not recommended: the blockers are
   Anthropic's, and the design would carry a node kind that is offline by default.

2. **The cloud session as an adapter over Anthropic's harness.** §4.10 allows it in principle: *a
   tool whose turns are composed by a harness is woken by that harness*. State would be the
   harness's status bucket, mail delivery its `send_message`, no keys and no decide. The harness's
   tools are reachable only from inside another Claude Code session, and a manager on kmaster is
   one — so the spike is cheap: from a kmaster session, `claude --cloud`, then see whether the
   Remote MCP is offered there and can observe and message the child. Unverified from here whether
   a local session gets those tools at all. If it does, a `claude-cloud` adapter is a bounded
   piece of work on an undocumented surface; if not, the shape is dead until Anthropic documents it.

3. **The cloud session as an outside contributor.** Its durable output is a PR, and the review
   and evidence rules of the cadence are per PR, not per session. Let a PR nobody asked about
   enter the reader's queue, and the cloud session gets the same independent read the workers get,
   with the verdict where it already lives, on the PR. Messaging then rides GitHub: review
   comments in, the harness's own PR subscription out.

## Shape 3, worked: how a stray PR reaches the reader

Paul asked (2026-09-24) whether the cloud session should write a board line that nudges him, after
which the PR goes through the normal review, or whether a sweep should look for stranded PRs. The
design answers the first and shapes the second.

**Not a board line, for two reasons.** §4.9a: *a PR held for its reader is never on the board* —
the reader is not the person, and a counted item that asks for nothing teaches the person to clear
without reading. And mechanically it cannot work from a cloud session: the board is a committed
file, the session's edit lives on its unmerged `claude/*` branch, and the SessionStart hook reads
the checkout's own board — so the nudge would arrive after the merge it was meant to cause, or
never. The cloud session cannot mail the seat either (no link). It has exactly one signal it can
raise that the org can see: **the open PR itself**.

**A sweep, done by the home's tick, feeding the existing queue.** The reader's queue is the seat's
inbox: an unanswered `ask` with `pr` (§4.9b *The reader*, TD-093). Today the author sends it. The
sweep makes the org send it for an author who cannot: on its tick, for each repo a running team
holds (or, without one, each repo in `org.yml` with a `techlead:` or `review` block), list the
repo's open PRs, and for each one that no `ask` with that `pr` names, **file one into the seat's
inbox from the person's name** (a person may message anyone, §4.10) with `about` set to the PR and
a text saying *unannounced PR: <branch> by <author>*. Everything after that is built: the seat is
`exited` with `asks_waiting` > 0, so the tick fills it (§6 *Keeping a team running*, rule 3); the
reader reads the diff and, the author being no `unattended` session, replies with a
**recommendation** and posts the evidence comment (`cadence-review: …`) on the PR; the person
merges or overrules, as for any `interactive` author. `prs_waiting` counts it; the team header
shows it. Past `review.bound` the escalation is the person's, as today. One rule keeps it honest:
**the sweep files for a PR once** — the ask's `pr` is the key, and a re-push to the same PR is a
new commit on a thread that already exists, which the reader sees as the author's re-ask does.

What this costs: one policy in the tick with a `gh pr list` per repo per tick (cheap; the manager
already reads the merge queue with `gh`), a `pr` filter on the seat's inbox, and a line in the
techlead brief saying what an unannounced PR is and that its author cannot be replied to by mail
— findings go **on the PR**, as review comments, which is where a cloud session (and any human)
reads them. A cloud session that subscribed to its own PR's activity is woken by those comments;
one that did not reads them next time it is opened.

**Where the stranded-work sweep fits.** `/stranded-work` is a person's periodic audit of closed
sessions (cadence §3); it should list open `claude/*` PRs with no evidence comment as a class of
stranding, since a cloud session's PR is the one thing that outlives it. That is a line in the
skill, and it is the fallback for a repo with no seat, not the mechanism.

## Recommendation

- **Now: shape 3**, as worked above — the tick's sweep into the reader's queue, the techlead brief
  line, and the stranded-work line. Design changes: §6 gains the sweep as a policy that starts
  nothing and mails once; §4.9b *The reader* gains the unannounced PR as a second way an `ask`
  with `pr` arrives; §3's cloud-sessions row gains *what was taken*: the PR as the one signal.
- **Then: the shape-2 spike**, from the anchor session on kmaster, one afternoon: `claude --cloud`
  and whether the Remote MCP is offered to a local session and can observe and message the child.
  Its answer decides whether a `claude-cloud` adapter is ever worth designing.
- **Not shape 1**, until Anthropic's egress rules admit a long-lived outbound connection, which is
  the same thing the `relay` transport (§4.5b) would need from the other side.

## Decisions for Paul

1. Adopt shape 3 as the mechanism for a PR whose author is not in the org — cloud sessions, and
   equally a PR Paul opens from a laptop with no session? (The sweep does not care who the author
   is; the reader's mode rule already treats a non-`unattended` author as *recommend, never merge*.)
2. Which repos the sweep covers when no team is running: none (a team's repos only), or every repo
   in `org.yml` with a seat defined?
3. Run the shape-2 spike, and file it as a ledger entry with the sweep, or park it?

## What was checked, and what was not

Checked from inside this session: the process tree and the absence of a tmux server; Claude Code's
registry entry; the egress proxy's status and README (allowlist, no WebSocket or raw TCP, 403 for
an arbitrary host); hooks firing from the repo's settings; Python versions; the absence of `gh`;
the Remote MCP's tool set and `get_session`'s fields. Read from documentation by a guide agent the
same day, with the pages cited above: the hook sources, the network access levels, environment
expiry, `--cloud` and `--teleport`, the closed feature request. **Not checked:** whether a local
Claude Code session on kmaster is offered the Remote MCP (the shape-2 spike); whether a Custom
allowlist admits a Cloudflare Access hostname with a service token; what a cloud session's
`--teleport` copy does to §4.1's name rule if it is started inside an `ao-*` pane.
