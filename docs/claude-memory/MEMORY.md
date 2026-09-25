# Project Memory

<!-- Index of docs/claude-memory/ — one line per memory, pointers only, never content.
     Format:  - [Title](file.md) — one-line hook
     Group under ## headers by topic as the list grows. -->
- [Unattended workers run inside agentorc](unattended-workers-run-inside-agentorc.md) — launch TD-grind workers with `ao new --unattended`, briefs in docs/briefs/
- [TD-grind mechanics in agentorc](agentorc-td-grind-mechanics.md) — ledger template is headed TD-001; never guess a PR number; cadence verdict goes on the comment's first line; archive-tail conflicts; a TD number can be taken on main while your PR is open; an archived entry needs `**Resolved:**`
- [Claude Code composer ghost text](claude-code-composer-ghost-text.md) — faint `❯ text` is a suggestion, not an unsubmitted prompt; check `capture-pane -e`
- [Design goals: effective and non-intrusive](design-goals-effective-non-intrusive.md) — mechanical sync direct; repo-judgement work by that repo's sessions; tool wiring in adapters
- [No VS Code windows for agent worktrees](no-vscode-windows-for-agent-worktrees.md) — open_worktree.sh opens `code -n`; use git worktree add for session-only worktrees
- [Live system restart authorized 2026-09-13](live-system-restart-authorized.md) — stop all agents and the system; restart once teams (TD-040) exist; sweep worktrees first
- [Design review loop with Sonnet](design-review-loop-with-sonnet.md) — Fable findings → adopt → Sonnet rounds until READY → PR with fact-check
- [Scratchpad too long for unix sockets](scratchpad-too-long-for-unix-sockets.md) — a scratch AGENTORC_HOME needs a short path like ~/.cache/ao<topic>
- [How a design round runs from a cloud session](cloud-design-round.md) — park on the board first, propose in chat, Sonnet rounds to READY, fact-check on the PR, one branch
- [Check open PRs before a design round](check-open-prs-before-a-design-round.md) — the designer's stacked PRs may already hold the design; compare and reconcile, never design twice
- [A park is a merged PR, not an open one](a-park-is-a-merged-pr.md) — the designer reads the board on `main`; wait for the park PR to merge, and re-search the id in every state before the round
- [Check in-flight work before a TD step](check-in-flight-before-a-td-step.md) — another session may hold the next step; conformance-read instead; `ao msg` for leads, never a peer message
- [Headless screenshots on kmaster](headless-screenshots-on-kmaster.md) — snap Firefox `--headless --screenshot`; profile and output under a plain home dir, not /tmp or hidden
- [Open the PR before writing its number](open-the-pr-before-writing-its-number.md) — a guessed `PR #N` in the ledger points at someone else's PR
- [pdm run fmt sweeps other sessions' files](pdm-run-fmt-sweeps-other-sessions-files.md) — it formats the whole repo; `git add -A` after it steals a sibling's merged files into your PR
- [gh comment bodies go in a file](gh-comment-bodies-go-in-a-file.md) — `--body "…"` lets bash run its backticks; use `--body-file`
- [Dates are local, PR numbers are real](dates-and-pr-numbers-are-local-and-real.md) — `date` before a dated ledger edit; open the PR before writing its number
- [Read inbox before merging](read-inbox-before-merging.md) — merge rights change by mail; since 2026-09-23 a held-path PR waits for the techlead seat (`ao pr held N`), not the anchor
- [No checks means conflicting](no-checks-means-conflicting.md) — `gh pr checks` empty after a push: the PR conflicts with main, so no pull_request run; rebase
- [Gate a merge on the check's exit code](gate-a-merge-on-the-checks-exit-code.md) — `check_cadence.py | head` hides a FAIL; capture the exit code, then merge
- [Reviewer prompts keep out of the anchor](reviewer-prompts-keep-out-of-the-anchor.md) — a reviewer given `git -C /home/kmaster/agentorc` ran a checkout there; worktree paths only
- [Scratch-worktree tests import the main checkout](scratch-worktree-tests-import-main-checkout.md) — pull main before running a branch's tests from a scratch worktree
- [Retarget a stacked PR with the API](retarget-a-stacked-pr-with-the-api.md) — `gh pr edit --base` fails on projectCards; PATCH pulls/N, rebase --onto, delete the base last
- [Summary-table conflicts resolve row-wise](summary-table-conflicts-resolve-row-wise.md) — one row per id after a rebase, never both sides; `ao --json` goes before the subcommand
- [Designer run lessons 2026-09-25](designer-run-lessons-2026-09-25.md) — a steer is refused when the person inbox is full (board line instead); reviewer agents need explicit refs in a shared worktree; re-read main for the next TD number before a design PR
