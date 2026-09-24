# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

## What this repo is

agentorc is a self-hosted web dashboard that orchestrates interactive AI coding-agent sessions
(Claude Code first) and plain shells running in tmux across hosts. **Status: past phase 1**
(§7 of the design says what each phase has and lacks). [`docs/design.md`](docs/design.md) is the source of truth: requirements,
architecture, every control (§4.5a — a control that is not in that table does not exist), the
phase plan (§7), invariants (§9), and the dated question log (§10). It is written in the present tense and says what
is true now; the dated record of how each rule came to be is [`docs/design-history.md`](docs/design-history.md). A
change in behaviour is a change to the design first, and the dated fact goes to the history, never the other way round. Mockups regenerate from `docs/mockups/gen.py`.

## Stack and conventions

- Python **3.12+** (kmaster runs 3.13), managed with **pdm**; `requires-python >= 3.12`.
- **ruff** at line length 120 for lint and format.
- One distribution, `agentorc`, with an `[ui]` extra; two import packages: `sessionorc`
  (tmux, hosts, ssh, pty bridge, run logs, the `shell` adapter) and `agentorc` (hook adapters,
  profiles, policies, Ready to close, the board). `sessionorc` never imports `agentorc`.
- Adapters live under `agentorc/adapters/<tool>/`; core never imports tool-specific names
  outside the adapter (design §4.3).
- `pdm run test` (private tmux sockets, temp `AGENTORC_HOME`; never the user's server),
  `pdm run lint`, `pdm run fmt`. Run the thing: `pdm run agentorc-agent serve` + `pdm run ao ui`
  (README "Run it").
- If `pdm run test` dies with an ImportError inside `_pytest`, the venv is fine but pdm's
  symlink install cache (`~/.cache/pdm/packages`) lost files: `pdm sync -G ui -G dev --reinstall`
  (seen 2026-09-06).

## Building against live sessions — read before phase 1

Phase 1 installs Claude Code hooks and creates `ao-*` tmux sessions on **this machine, in your
own sessions' config**. Develop and test against a scratch directory and a throwaway profile
config dir first; point it at real repos (samscrape) only when the success test in design §7
passes there. The one-agent-per-directory anchor rule (design §9 invariant 2) is the same rule
this repo's cadence enforces on its own checkout (below), and it applies to the sessions
agentorc creates.

## The live copy is promoted, not edited (TD-062, 2026-09-17)

The systemd units and every session's `ao` run a **non-editable** install in
`~/.local/share/agentorc-venv`, so a merge, or a branch checked out here, changes nothing that is
running. Until 2026-09-17 the install was editable: one merged RPC change broke `ao progress` for
every worker for 31 minutes, and any branch checked out in this checkout was live at once.
**The anchor session promotes**: after a merge that should be live, with `main` clean and current —

```bash
V=~/.local/share/agentorc-venv/bin
$V/pip install --upgrade '/home/kmaster/agentorc[ui]' && $V/ao service install
```

— pip rebuilds a local directory even at an unchanged version and picks up a new dependency; the
pair replaces client and host agent together and restarts both units (sessions live in tmux and
survive it), and writes the wheel of what it installed to `~/.agentorc/wheels/`, which is what a
container node is provisioned from (`ao host up`, design §4.4a). Never promote from a feature branch; a lead or a worker never promotes.
The promote is designed as the repo's `promote:` block and a policy of the home's tick (design §5, §6 *Promote*,
TD-120 step 2; TD-132 builds it); until that lands, the pair above is the press. One thing
is still read from the checkout: a team's `brief:` files under `docs/briefs/`, at team start, so
a team still follows the branch checked out here at that moment.

## Session Hygiene — Never Strand Work

Work that exists only in a session's conversation is lost when the session closes. Two rules,
full conventions in [docs/cadence.md](docs/cadence.md):

1. **Ledger before idle.** The moment Paul approves a multi-item plan, or a session pauses with
   steps undone, write the pending steps into [docs/technical_debt.md](docs/technical_debt.md)
   (new `TD-NNN` entry or a line in the relevant one). Items that need Paul to act or decide go
   on [docs/user_attention.md](docs/user_attention.md) in the board's `Format:` line, each with
   a `Due:` date matched to its urgency; snoozing is editing the date. Due items are printed
   into the context of every session started in a repo that wires the SessionStart hook (this
   repo does, via `.claude/settings.json`). If parked work has a branch, push it and name it.
2. **Periodic sweep.** Run `/stranded-work` weekly or whenever in doubt; `/attention` is the
   machine-wide board view.

Every change goes branch → PR → squash merge; the pre-push hook blocks main
(`ALLOW_MAIN_PUSH=1` is the deliberate override; cadence §4 lists the carve-outs). Every other
concurrent session in this repo works in a worktree (`claude --worktree <name>` or
`scripts/open_worktree.sh <topic>`); the first session in the checkout is the anchor.
Self-authored PRs get an independent cheaper-model review before merge; doc-only PRs get a
fact-check against the repo.

Files under `scripts/`, `docs/cadence.md`, and `.claude/skills/` that open with a SYNCED FILE
header belong to dev-cadence: edit them there, never here.

## Documentation map

| Need | Where |
|---|---|
| What agentorc is and how it works | [docs/design.md](docs/design.md) |
| How the design got here: dated decisions, reversals and review rounds, by section | [docs/design-history.md](docs/design-history.md) |
| What a word means (lead, director, worker, host agent, …) | [docs/glossary.md](docs/glossary.md) |
| Why dev-cadence was adopted, what it changed in the design | [docs/decisions/2026-09-06-adopt-dev-cadence.md](docs/decisions/2026-09-06-adopt-dev-cadence.md) |
| Why herdr is prior art and not the session substrate | [docs/decisions/2026-09-10-herdr-spike.md](docs/decisions/2026-09-10-herdr-spike.md) |
| Why OpenAI's Agents API is not the substrate, and the four lessons taken from it | [docs/decisions/2026-09-13-openai-agents-api.md](docs/decisions/2026-09-13-openai-agents-api.md) |
| What Org, Team, Project and Role mean, and why the name stays agentorc for now | [docs/decisions/2026-09-13-org-teams-projects.md](docs/decisions/2026-09-13-org-teams-projects.md) |
| Why agentorc builds its own agent messaging instead of Claude Code's or mcp_agent_mail | [docs/decisions/2026-09-16-agent-messaging-prior-art.md](docs/decisions/2026-09-16-agent-messaging-prior-art.md) |
| Who else builds session desks and supervisors, and what was taken from them | [docs/decisions/2026-09-20-session-desk-neighbours.md](docs/decisions/2026-09-20-session-desk-neighbours.md) |
| How Paperclip (the "AI company" control plane) compares, and what to learn from it | [docs/decisions/2026-09-24-paperclip.md](docs/decisions/2026-09-24-paperclip.md) |
| Whether a claude.ai/code cloud session can join the org, and how its PR reaches the reader | [docs/decisions/2026-09-24-cloud-sessions.md](docs/decisions/2026-09-24-cloud-sessions.md) |
| Mockup sources and regeneration | [docs/mockups/README.md](docs/mockups/README.md) |
| Working cadence (sessions, reviews, TD flow) | [docs/cadence.md](docs/cadence.md) |
| Known issues and deferred work | [docs/technical_debt.md](docs/technical_debt.md) |
| What is waiting on Paul / parked work | [docs/user_attention.md](docs/user_attention.md) |
| Durable cross-session memory | [docs/claude-memory/MEMORY.md](docs/claude-memory/MEMORY.md) |
| Resume a past session | `scripts/list_sessions.py` |
