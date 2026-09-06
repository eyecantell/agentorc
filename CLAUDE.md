# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

## What this repo is

agentorc is a self-hosted web dashboard that orchestrates interactive AI coding-agent sessions
(Claude Code first) and plain shells running in tmux across hosts. **Status: design settled,
nothing built yet.** [`docs/design.md`](docs/design.md) is the source of truth: requirements,
architecture, every control (§4.5a — a control that is not in that table does not exist), the
phase plan (§7), invariants (§9), and the dated question log (§10, three items still open). A change in behaviour is a
change to that document first. Mockups regenerate from `docs/mockups/gen.py`.

## Stack and conventions

- Python **3.12+** (kmaster runs 3.13), managed with **pdm**; `requires-python >= 3.12`.
- **ruff** at line length 120 for lint and format.
- One distribution, `agentorc`, with an `[ui]` extra; two import packages: `sessionorc`
  (tmux, hosts, ssh, pty bridge, run logs, the `shell` adapter) and `agentorc` (hook adapters,
  profiles, policies, Ready to close, the board). `sessionorc` never imports `agentorc`.
- Adapters live under `agentorc/adapters/<tool>/`; core never imports tool-specific names
  outside the adapter (design §4.3).
- Once the package exists: `pdm run test`, `pdm run lint`. Until then there is nothing to run.

## Building against live sessions — read before phase 1

Phase 1 installs Claude Code hooks and creates `ao-*` tmux sessions on **this machine, in your
own sessions' config**. Develop and test against a scratch directory and a throwaway profile
config dir first; point it at real repos (samscrape) only when the success test in design §7
passes there. The one-agent-per-directory anchor rule (design §9 invariant 2) is the same rule
this repo's cadence enforces on its own checkout (below), and it applies to the sessions
agentorc creates.

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
| Why dev-cadence was adopted, what it changed in the design | [docs/decisions/2026-09-06-adopt-dev-cadence.md](docs/decisions/2026-09-06-adopt-dev-cadence.md) |
| Mockup sources and regeneration | [docs/mockups/README.md](docs/mockups/README.md) |
| Working cadence (sessions, reviews, TD flow) | [docs/cadence.md](docs/cadence.md) |
| Known issues and deferred work | [docs/technical_debt.md](docs/technical_debt.md) |
| What is waiting on Paul / parked work | [docs/user_attention.md](docs/user_attention.md) |
| Durable cross-session memory | [docs/claude-memory/MEMORY.md](docs/claude-memory/MEMORY.md) |
| Resume a past session | `scripts/list_sessions.py` |
