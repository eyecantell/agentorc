# ADR 2026-09-06: adopt dev-cadence, and what the evaluation changed

Status: accepted (2026-09-06). Evaluation run per dev-cadence's README, both directions, before
installing.

## Context

agentorc's design already depends on dev-cadence: it reads the machine roster
(`~/.config/dev-cadence/repos.txt`) to know which repos exist, shows each repo's
`docs/user_attention.md` board on the Herd, and edits board items (Snooze, Done) through the host
agent. It will also be *built* by concurrent Claude Code sessions, which is exactly the situation
dev-cadence exists for. The question was not whether to use it but what fits, what needs
adapting, and what agentorc has learned that dev-cadence should have.

## What fits as-is

- The ledger, the attention board, ledger-before-idle, the periodic sweep (cadence §2, §3).
- Branch → PR → squash merge with the pre-push main guard (§4). The repo is public, so a
  server-side 0-approval ruleset is available too (proposed, not applied — see below).
- In-repo memory under `docs/claude-memory/` with the memory guard (§7).
- One anchor per checkout with worktrees for every other session (§1). agentorc's own anchor
  rule (design §9 invariant 2) is this rule generalised to directories.
- The worktree scripts: `open_worktree.sh`, `hydrate_worktree.sh`, `reap_worktrees.sh`. The
  design's "worktree reap" policy (§6) will call `reap_worktrees.sh` rather than reimplement its
  five checks, all of which exist because something went wrong once.
- `check_anchor.py`'s `live_sessions()` (corroborated by `procStart`, not `/proc` existence)
  and `list_sessions.py`'s transcript scan are what the Claude Code adapter needs for the
  Resumable tab and the liveness cross-check. Import them; do not copy them.

## What needed adapting

- **Deploys are part of done (§5)** has no deploy target yet. It becomes "installed on the
  hosts in `hosts.yml`" once phase 2 lands; until then the section is inert.
- **Constellations (§9)** and the containers appendix do not apply: single repo, no devcontainer.
- **Nothing to run yet.** CLAUDE.md names `pdm run test` / `pdm run lint` as the commands that
  will exist; the cadence's "tests + lint before the PR" is honoured from the first code PR.

## What the evaluation changed in agentorc's design

1. **Hooks are a per-launch settings layer, not an edit to anyone's config.** The design had
   `install_hooks(repo)`. dev-cadence seeds `.claude/settings.json` with SessionStart hooks in
   every consuming repo, and agentorc also runs sessions in directories that are not repos. The
   first revision of this ADR moved the install to each profile's `settings.json`; the build
   session found something better the same day: Claude Code takes `--settings <file>` as a
   settings layer for one launched session, and hooks in it fire (verified). So the adapter
   writes `~/.agentorc/claude-hooks/<profile>.json` and passes it at launch. Nothing of the
   person's is edited, hand-started sessions are untouched, and repo-level hooks keep running.
   The hook script finds its session and its agent from `AGENTORC_SESSION` and
   `AGENTORC_HOME`, both set on the tmux session by the host agent. Written into design §4.2
   and §4.3.
2. **Board write-back needs a cadence carve-out.** Committing a Snooze or Done edit to main in
   the main checkout contradicts cadence §4 as written; leaving it uncommitted contradicts the
   one-writer rule and "push before you pause". Proposed upstream as a third bounded carve-out
   (dev-cadence PR #83). The design already matched its shape: one line, fixed message naming
   tool, action, and session; never pushes.
3. **The Due strip and Attention tab consume `nudge_user_attention.py --report --json`,** not
   the text report, and edit items by the board line number that output carries. Proposed
   upstream as dev-cadence PR #82; the design's Attention tab is that report rendered, which is
   why the tab has no second data path.

## Sent upstream

- [dev-cadence #82](https://github.com/eyecantell/dev-cadence/pull/82) — `--report --json`
  plus `BoardItem.line`; four tests. Code.
- [dev-cadence #83](https://github.com/eyecantell/dev-cadence/pull/83) — cadence §4 carve-out
  for tool-made Snooze/Done edits on the user's click. Doc, marked *proposed*.

Review and merge of both stay with the maintainer. Until #82 merges, the synced copy here lacks
`--json`; the next `sync.sh` run picks it up.

## GitHub settings proposed, not applied

Cadence §4 asks for squash-only merges, automatic deletion of merged head branches, and a
0-approval PR ruleset on public repos. As of 2026-09-06 the repo allows all three merge methods,
does not delete merged heads, and has no rulesets. The commands, for when Paul says yes:

```bash
gh api -X PATCH repos/eyecantell/agentorc -f allow_squash_merge=true -f allow_merge_commit=false \
  -f allow_rebase_merge=false -f delete_branch_on_merge=true
# 0-approval PR-required ruleset on main: create in Settings → Rules → Rulesets (bypass: none),
# or via `gh api repos/eyecantell/agentorc/rulesets` with a pull_request rule of required_approving_review_count=0.
```

The optional external nudge channel (cron plus `NUDGE_COMMAND` or Telegram) is not wired; the
SessionStart due-items line covers this repo, and agentorc's Due strip is the intended
replacement for a push channel anyway.
