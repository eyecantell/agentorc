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
- [Check open PRs before a design round](check-open-prs-before-a-design-round.md) — the designer's stacked PRs may already hold the design (a restarted run's own too: `gh pr list` before the first claim); compare and reconcile, never design twice
- [A park is a merged PR, not an open one](a-park-is-a-merged-pr.md) — the designer reads the board on `main`; wait for the park PR to merge, and re-search the id in every state before the round
- [Check in-flight work before a TD step](check-in-flight-before-a-td-step.md) — another session may hold the next step; conformance-read instead; `ao msg` for leads, never a peer message
- [Headless screenshots on kmaster](headless-screenshots-on-kmaster.md) — snap Firefox `--headless --screenshot` at load; Playwright (`~/ao-shots/pwlib`, Chromium) for anything after scripts run; read-only against the live UI
- [Open the PR before writing its number](open-the-pr-before-writing-its-number.md) — a guessed `PR #N` in the ledger points at someone else's PR
- [pdm run fmt sweeps other sessions' files](pdm-run-fmt-sweeps-other-sessions-files.md) — it formats the whole repo; `git add -A` after it steals a sibling's merged files into your PR
- [gh comment bodies go in a file](gh-comment-bodies-go-in-a-file.md) — `--body "…"` lets bash run its backticks; use `--body-file`
- [Dates are local, PR numbers are real](dates-and-pr-numbers-are-local-and-real.md) — `date` before a dated ledger edit; open the PR before writing its number
- [Read inbox before merging](read-inbox-before-merging.md) — merge rights change by mail; since 2026-09-23 a held-path PR waits for the techlead seat (`ao pr held N`), not the anchor
- [No checks means conflicting](no-checks-means-conflicting.md) — `gh pr checks` empty after a push: the PR conflicts with main, so no pull_request run; rebase
- [Gate a merge on the check's exit code](gate-a-merge-on-the-checks-exit-code.md) — `check_cadence.py | head` hides a FAIL; capture the exit code, then merge
- [Reviewer prompts keep out of the anchor](reviewer-prompts-keep-out-of-the-anchor.md) — a reviewer given `git -C /home/kmaster/agentorc` ran a checkout there; worktree paths only
- [Scratch-worktree tests import the main checkout](scratch-worktree-tests-import-main-checkout.md) — run a branch's tests with `PYTHONPATH=$PWD/src` and the main venv
- [Retarget a stacked PR with the API](retarget-a-stacked-pr-with-the-api.md) — `gh pr edit --base` fails on projectCards; PATCH pulls/N, rebase --onto, delete the base last
- [Summary-table conflicts resolve row-wise](summary-table-conflicts-resolve-row-wise.md) — one row per id after a rebase, never both sides; `ao --json` goes before the subcommand
- [Designer run lessons 2026-09-25](designer-run-lessons-2026-09-25.md) — a steer, and a techlead reply with `--source`, is refused when the person inbox is full (board line / `Source:` first line instead); reviewer agents need explicit refs in a shared worktree; re-read main for the next TD number before a design PR
- [An interactive UI/UX review makes the change visible](ui-review-screenshots.md) — significant UI changes reviewed with Paul always come with something to look at: screenshots, renderings or a design page (one per compared shape), kept under docs/mockups/reviews/ and sent into the chat
- [Design help text is bound to help.py](design-help-text-is-bound-to-help-py.md) — a design PR never rewords §4.5a's help list; the wording goes in the build entry; run the doc-bound tests before pushing
- [Delete only your own branches, by name](delete-only-your-own-branches-by-name.md) — `git branch` is every session's; never loop a delete over it (2026-09-27 incident, refs/recovered)
- [Designer run lessons 2026-09-28](designer-run-lessons-2026-09-28.md) — `ao msg`'s id is `.entry.id` and a warning is not a refusal; run test_ledger.py; re-check a "number taken" live; the host agent never moves the person's checkout
- [Gate everything after a merge on its state](gate-everything-after-a-merge-on-its-state.md) — check `MERGED` before deleting the branch or reporting; a deleted branch closes the PR for good (2026-09-28, #727 → #730)
- [The primer is a held path](primer-is-a-held-path.md) — docs/briefs/techlead-context.md is under docs/briefs/**; a design PR leaves it to the build entry's briefs slice
- [ao msg: options before recipients](ao-msg-options-before-recipients.md) — `ao msg person --kind note "…"` exits 2; put options first, and never pipe a send through `tail`
- [A designed entry needs a Blocked by line](designed-entry-needs-blocked-by.md) — pickable is derived from Blocked by and no Pickable line is written; name the build entries (2026-10-01)
- [Steer replies land on the board](steer-replies-land-on-the-board.md) — Paul's Reply to a steer reaches main's board via the anchor, not a new run's inbox; grep the board for each open steer id before merging at a bound
- [Edit scripts assert before writing](edit-script-asserts-before-writing.md) — check every replacement count first, then write; never chain `git commit` after an edit script
- [Never count the unread inbox](never-count-the-unread-inbox.md) — `ao inbox --unread` marks entries read; save it to a file, never pipe it through `jq length`; check open questions from the full inbox before stopping
- [Stop a scratch home by its pid](stop-a-scratch-home-by-its-pid.md) — `pkill -f`/`pgrep -f` on look_home args kills the tool's own shell; `ps | grep '[l]ook…'`, then kill the number
- [Designer run lessons 2026-10-04](designer-run-lessons-2026-10-04.md) — a history line goes above a section's newest lines, never at its tail; re-check the build number at the merge; no checkout while a reviewer reads the worktree
- [Memory edits block auto promote](memory-edits-block-auto-promote.md) — a memory write lands uncommitted in the main checkout and auto promote refuses a dirty checkout; land it by PR
- [Read the claim's reply](read-the-claims-reply.md) — `ao progress claim` can be refused; never send it to /dev/null, check your record before the first step (TD-152 pressed twice, 2026-10-05)
- [Designer run lessons 2026-10-05](designer-run-lessons-2026-10-05.md) — a steer's first paragraph is warned past ~100 words (read the reply past the warning line); the manager seat may hold no session; a fact-check by commit ref frees the worktree
- [Unused weekly tokens are lost velocity](unused-weekly-tokens-are-lost-velocity.md) — the weekly window does not carry over; near the reset keep every session busy, never defer to "save" it
- [Designer run lessons 2026-10-06](designer-run-lessons-2026-10-06.md) — a steer needs `--default`; a build number taken mid-PR: squash, rebase once, renumber in the one resolution
- [ao msg's warning precedes its JSON](ao-msg-warning-precedes-json.md) — a jq parse error on the reply is not a failed send; strip the warning line, check `entry.id` before resending
- [Never kill a parent pid unseen](never-kill-a-parent-pid-unseen.md) — a `kill $ppid` loop over `sleep 90` matches killed `systemd --user` and the whole org for 4h20m (2026-10-09); kill only pids you started and recorded
