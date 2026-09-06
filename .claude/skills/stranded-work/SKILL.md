---
name: stranded-work
description: Audit for work stranded by closed Claude Code sessions — approved plans never started, "awaiting the user" steps that evaporated, deploy-pending fixes, stale watch-items. Quick mode runs inline; pass "deep" to fan out agents including a session-transcript scan.
---

<!-- SYNCED FILE — canonical copy: eyecantell/dev-cadence files/.claude/skills/stranded-work/SKILL.md
     Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one). -->

# Stranded-Work Audit

Find work that was identified, approved, or partially completed in a past session but dropped when the session closed. Born from a 2026-08-07 audit in samscrape that found the root pattern: **work strands when it exists only in a session's conversational state; everything written to the TD ledger survives.**

## Modes

- **Quick (default):** run the checks below inline. ~2 minutes, no agents.
- **Deep (`/stranded-work deep`):** additionally fan out Sonnet agents (general-purpose, `model: sonnet`, launched in one parallel batch) for the transcript scan and TD-ledger cross-verification described in §Deep.

**Machine scope:** this sweep sees only the current machine — its clones, worktrees, and `~/.claude` transcripts. Unpushed work on another machine is invisible; the only cross-machine traces are the pushed remote and board entries citing other hosts (`session <id> on <host>`). Flag any board entry whose host isn't this machine as "verify on <host>" rather than assuming it's stale.

## Quick checks (run all; read-only)

*(Numbering note: these are the sweep's own steps 1–5, local to this skill. Prose elsewhere saying "check #N" always means `check_claude_memory.sh`'s guard checks #0–#6 — two independent schemes.)*

1. **Attention board** — read `docs/user_attention.md`. Flag unchecked items past their `Due:` date (the SessionStart due-items line should already be surfacing these — if one is long overdue, ask whether to renegotiate the date or escalate) and items with no due date older than ~2 weeks. Verify each item's current state before assuming it's still needed, and remove entries that were handled but never checked off. (The `Nudge:` delivery-claim audit that used to live here retired with TD-7 — boards no longer carry claims; a leftover `Nudge:` header on an older board is dead text, safe to delete.)
2. **Ledger commitments** — grep `docs/technical_debt.md` (case-insensitive) for: `deploy pending`, `not yet deployed`, `deploy+watch`, `manual step`, `remaining:`, `watch `, `pending fires`, `next:`. For each hit, read enough of the entry to classify: tracked-deferral (fine) vs. in-flight-and-dropped vs. stale-status (work done, line never updated — per `feedback_verify_td_code_state`, verify against code/deployment before trusting the status line).
3. **Git/GitHub state:**
   - `gh pr list --state open` — any PR open >2 days is a flag.
   - `git worktree list` — for each non-main worktree: `git -C <path> status --porcelain` (uncommitted changes = strong flag; run it with `--untracked-files=all`, since scratch left in a worktree is exactly what must not be reaped) and whether its **content has landed** (see the landed test below). Clean+landed worktrees are just housekeeping.
   - `git branch -a --sort=-committerdate`, flagging any branch whose content has **not** landed; `git stash list`; `git log --branches --not --remotes --oneline` (unpushed commits on ANY branch — these are invisible to every other machine, the worst stranding class in a multi-machine setup); `git status` (untracked session leftovers).

   **The landed test — and both obvious forms of it are wrong.** Wherever §4 squash-merges, ancestry and landed-ness stop being the same question: squashing severs the first while satisfying the second.

   ```bash
   base=$(git merge-base origin/main "$branch")
   mapfile -d '' -t files < <(git diff --name-only -z "$base" "$branch")   # what the branch touched
   ((${#files[@]})) && git diff --quiet origin/main "$branch" -- "${files[@]}"   # quiet ⇒ landed
   ```

   **The `-z`/array form is not fussiness — the obvious version silently reports unlanded work as landed.** With a plain `files=$(...)` and an unquoted `-- $files`, bash word-splits any path containing a space; the fragments arrive as pathspecs matching nothing, and `git diff --quiet` with a pathspec that matches nothing exits **0**, with no error on stdout or stderr. A branch whose only work lives in `dir with space/file.txt` therefore reports landed while never having been compared. One repo running this had a dozen such paths tracked.

   Why not the simpler forms:

   - `git log origin/main..<branch>` asks about **ancestry**, which squashing destroys — so it reports every squash-merged branch and worktree as unmerged, permanently. It fails in the direction that manufactures work: the sweep re-reports finished topics every run, which is how a sweep gets ignored.
   - `git diff origin/main <branch>` unscoped asks a **symmetric** question, so it also reports main's *other* advances as differences. A genuinely landed branch reads as unlanded the moment anything else merges — which, on a repo running parallel sessions, is immediately.

   Scoping to the branch's own files is what §1's `git diff origin/main HEAD -- <your files>` has always meant. Ten-second reproduction of both failures:

   ```bash
   git checkout -b topic && echo x >> f && git commit -qam "topic work"
   git checkout main && git merge --squash topic && git commit -qm "topic work (#1)"
   echo unrelated > g && git add g && git commit -qm "other work on main"
   git log --oneline main..topic     # prints "topic work"  → ancestry says UNMERGED
   git diff --stat main topic        # shows g              → unscoped says UNLANDED
   git diff --stat main topic -- f   # empty                → scoped: topic DID land
   ```

   **An empty `files` means "nothing committed yet", not "landed".** A worktree opened moments ago has no commits, so its diff from the merge-base is empty; treating that as landed marks every freshly-created topic reapable before its session writes a line. (Found exactly that way, against a live worktree.) Report it as its own state and keep it.

   Compare against `origin/main`, not `main`: in the worktree layout §1 prescribes, local `main` is whatever the anchor last pulled and is routinely behind the branch that just merged.
   - Deploy gap: compare what's actually running against the latest main commits touching deployed code *(adapt per repo — k8s example: `kubectl get pods --sort-by=.status.startTime` vs `git log`)* — merged-but-never-rolled-out fixes are a recurring stranding class.
4. **Design docs / plans** — `grep -rn '\- \[ \]' docs/*.md docs/decisions/*.md`. **The grep is the file list; `git log` only ranks it.** Use `git log --since="60 days ago" --name-only -- docs/` to decide what to read *first*, never to decide what to look at — this is a detector, so it walks the disk on purpose (cadence.md §7, "gates judge the index; detectors judge the disk"). An untracked plan doc has no git history at all, so narrowing the list by `git log` drops exactly the files most likely to hold stranded work, and the sweep still reports clean. Read the untracked hits even though they sort last. An approved plan with unchecked phases and no matching TD entry is the highest-severity pattern.
5. **Memories** — scan the memory directory for past-due dates (expiries, "watch until", "check ~YYYY-MM-DD") and "pending"/"next"/"still open" claims; spot-verify the top hits against git log / the TD archive to separate STRANDED from STALE MEMORY. **Resolve the directory from `autoMemoryDirectory`** (`.claude/settings.local.json`, then `.claude/settings.json`, then `~/.claude/settings.json`), falling back to `docs/claude-memory` only when unset — that setting is what decides where Claude Code actually writes memory, so a hardcoded path silently scans an empty or nonexistent directory and reports the memories clean without having read one. **Parity (§7):** `check_claude_memory.sh` resolves the same setting the same way; change them together.

## Deep additions

- **Transcript scan agent:** scan session tails under `~/.claude/projects/` — the main project dir (the munged project path, e.g. `-home-user-myrepo`) **and** worktree-path variants (`-*claude-worktrees-*`), since concurrent sessions run in worktrees. For the ~20 most recent sessions per dir: extract the last few assistant messages + any todo state from each JSONL tail (`tail -c 200000`, helper script in scratchpad — files are huge, never read whole). Flag sessions ending with pending todos, "held for your review", "next steps", or an unanswered blocking question to the user. Cross-check each flagged item against git log / the TD ledger before calling it stranded.
- **Verification agents:** for each quick-check candidate, an agent verifies actual code/cluster state so the report never claims stranded work that already shipped.

## Report format

Group as: **STRANDED** (high confidence, with evidence: file:line, PR/commit, session id if known) / **AWAITING THE USER** (the `docs/user_attention.md` items with age and session id) / **STALE STATUS** (work done, ledger/memory line needs updating — offer to fix these inline) / **CLEARED** (checked, fine — brief). End with a one-line recommendation per STRANDED item: resume, or explicitly close as deprioritized (a decision, either way — never leave it dangling again).

Fix STALE STATUS items directly when trivial (status lines, memory text). Never start the stranded work itself without the user's go-ahead — surfacing it IS the deliverable.

## Stamp the sweep (final step, TD-12/TD-13)

After delivering the report, update the board's committed sweep stamp lines in `docs/user_attention.md` (each at column 0 — an indented stamp reads as absent), near the top:

- **Every run:** `Swept: YYYY-MM-DD (<host>, quick|deep)` — replace the existing line; add one if absent. The mode marker is load-bearing and the reader *validates* it — a stamp claiming neither `quick` nor `deep` warns, so always write the mode you actually ran.
- **Deep runs only:** also write `Swept-deep: YYYY-MM-DD` — replace in place; **quick runs never touch this line.** It preserves the last transcript-scan date after later quick sweeps overwrite `Swept:` (TD-13) — with the two-line stamp, `quick` structurally *cannot* claim `deep`'s coverage.

Stamp ONLY the current repo's board, never other repos' boards (a machine-wide stamp would claim sweeps that didn't happen). **Landing it (TD-16, cadence.md §4's one carve-out):** commit the stamp change alone — the board file, the stamp line(s), nothing else — and push it directly to main with `ALLOW_MAIN_PUSH=1 git push`; anything else in the commit voids the exception and goes through the normal PR flow. On a cross-machine conflict take the most recent date (single scalar — nothing to merge). The `/attention` report (`nudge_user_attention.py --report`) warns when `Swept:` is absent or older than `SWEEP_STALE_DAYS`, and separately when no deep sweep is on record or the last is older than `SWEEP_DEEP_STALE_DAYS` — those warnings are the pull-side reminder these stamps exist to feed.
