<!-- SYNCED FILE — canonical copy: eyecantell/dev-cadence files/docs/cadence.md
     Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one). -->

# Working Cadence

How this repo runs day-to-day with one human (the repo owner — “the user” below) and multiple concurrent Claude Code sessions. The **canonical copy lives in [eyecantell/dev-cadence](https://github.com/eyecantell/dev-cadence)** — edit it there, then re-run its `sync.sh` in each consuming repo (`docs/cadence-sync.lock` records the synced version). New repo: follow the [Adoption checklist](#adoption-checklist-for-a-new-repo). Repo-specific commands are marked *(adapt per repo)*.

## 1. Sessions and worktrees

- **One anchor session** keeps the main checkout (so the user can watch its changes in the editor). **Every other concurrent session** starts with `claude --worktree <name>` — a shared checkout means shared HEAD/index/working files, and two sessions on one checkout have caused real commit collisions.
- **Worktree lifecycle:** a worktree lives exactly as long as its unmerged work — remove it once its PRs merge. Starting *additional* work in an existing worktree is fine (fresh branch off updated `origin/main`; the worktree is the isolation unit, not the branch). Squash merges mean branches never register as merged: `git branch -D` is expected, and before discarding a worktree verify its content is on main (`git diff origin/main HEAD -- <your files>` is empty).
- **Merging from a worktree — don't use `gh pr merge --delete-branch`.** It half-fails, and the way it fails is the problem. `gh` merges server-side first, then tries to check out the base branch locally to clean up; the main checkout already holds `main`, so git refuses with `fatal: 'main' is already used by worktree at <path>` and `gh` aborts — *after* the merge has landed. Nothing in that message mentions the merge, so a merged PR reads as a failed one and its branch quietly survives on the remote looking unmerged. This is not an edge case: with one anchor plus worktrees for everything else, it is every PR. Merge in two steps instead, and treat any `gh pr merge` error as "check before retrying" — the error text describes the local cleanup failure, never the merge, so only `gh pr view` tells you what actually happened:
  ```bash
  gh pr merge <n> --squash                       # no --delete-branch from a worktree
  gh pr view <n> --json state,mergeCommit        # confirm MERGED before concluding anything
  git push origin --delete <branch>              # only if the repo does NOT auto-delete merged heads (§4)
  ```
  With the repo's **Automatically delete head branches** setting on (§4, "Give the rule teeth"), the third step is already done server-side and the manual `push --delete` just errors with "remote ref does not exist" — harmless, but the reason to keep the two-step habit is the first two lines, not the third.
- **Tearing down a worktree you *entered* rather than created is manual.** The tooling refuses to remove a worktree this session didn't create — the right call (it stops one session from deleting another's work), but it costs the blessed "reuse an existing worktree" path its one-step teardown. The refusal lists two possible causes *without saying which one applies* — merely entered, not created (routine) vs. another live session's liveness lock (stop and check) — so let git disambiguate: the Claude Code session that *created* a worktree holds a literal `git worktree lock` whose reason names it (`claude session <name> (pid N start T)`) — merely entering one takes no lock — and `git worktree remove` refuses and prints that reason while the lock is present, but succeeds (exit 0, verified) on a worktree you only entered. (Observed on Claude Code 2.1.231; message wording may shift across versions, but the lock is plain git and checkable with `git worktree list --porcelain`.) Expect a refusal even for a worktree this session *did* create: squash merges mean the branch's commits are never ancestors of main, so finished work still reads as unmerged. That removal needs `discard_changes: true` — safe exactly when the verify step above (`git diff origin/main HEAD -- <your files>` empty) says so. For a worktree you only entered, return to the main checkout first, then use git directly:
  ```bash
  ExitWorktree({action: "keep"})   # "remove" will refuse; keep just returns you
  scripts/hydrate_worktree.sh --dehydrate <path>   # constellation: FIRST, see below
  git worktree remove <path>
  git branch -D <branch>           # squash merges never register as merged
  ```

  **In a constellation the dehydrate line is not optional and its position matters.** A nested
  sibling worktree is gitignored in this repo, so `git worktree remove` neither sees it nor
  refuses because of it — measured, it deletes the sibling's uncommitted work along with the
  parent and leaves a `prunable` registration behind. `--dehydrate` removes them through their
  own repos and refuses if any holds unsaved work, so a non-zero exit means *stop*, not *retry
  with --force*. `reap_worktrees.sh` already does this in the right order; the sequence above is
  the hand-removal path, which is the one with nothing to catch the mistake.
- **Anchor detection is mechanical, not memory:** a SessionStart hook (`scripts/check_anchor.py`) reads the live-session registry (`~/.claude/sessions/` — per-session state, distinct from §9's machine *roster* of repos) and, when a new session starts in a checkout that already hosts a live session, injects a loud warning telling it to isolate into a worktree before touching anything. First session in = anchor. Run the script standalone anytime to see who holds the anchor. The anchor unit is the *git checkout*, not the folder tree: a nested repo (a **submodule**, a vendored or gitignored subproject, a sibling clone under an umbrella repo) shares no HEAD, index, or working files with its parent, so a session there is not an occupant of the outer checkout — it holds the *nested* repo's anchor, not the outer one's, which is worth knowing before assuming the outer checkout is unattended.
- **A live session is not necessarily an attended one — expect an anchor warning naming a session you cannot find.** The guard proves a registry entry's process is *alive*; nothing proves anyone is driving it. Editors persist terminals across a window reload or close: the terminal keeps running server-side, its `claude` with it, and no window reattaches. That process still corroborates perfectly — same pid, same `procStart` — so the warning is a true positive that is practically false, and it will keep firing at every new session in that checkout. Observed end to end: promoting a folder-opened VS Code window to a multi-root workspace forced a reload, the pre-reload `claude` survived on its old pty, and `claude --continue` then started a **second live process on the same session id**, both registered, both pointing at one transcript. Diagnose by pid, not by trust — walk the named pid's parents to see whether it still leads to a terminal you have open, and check it against the pid your own session reports. If it is abandoned, kill it; the transcript is on disk, so nothing is lost, and the registry entry becomes an inert fossil the guard already drops. Note it may ignore `SIGTERM` and need `SIGKILL`.
- **Seeing what parallel sessions are doing, before PR time.** Isolation costs visibility: an editor opened on the checkout shows only the main working tree, so the other sessions' edits are invisible until their PRs — too late to steer them. Two shapes, and they compose:
  - `scripts/open_worktree.sh <topic>` — creates the worktree (new branch off freshly-fetched `origin/main`, reusing an existing branch of that name) and opens it in **its own editor window**. One window per topic makes the *window* the context: one keystroke switches file tree, terminal and source control together, and the title says which topic you are in. Measured: the new window attaches to the same remote/container as the one launching it, no rebuild, ~660MB each. **In a constellation** the window opens on a per-topic multi-root workspace instead of the bare folder, because one topic spans several repos there (see §9 and the `--topic` note below) — still one window per topic, and the workspace is named for the topic so the title is unchanged.
  - `scripts/hydrate_worktree.sh` — completes a worktree with the things git does not track, and empties it again before removal. `open_worktree.sh` runs it for you, **and a SessionStart hook (`--hook`) warns in any worktree that is missing something** — which is what covers `claude --worktree` and hand-rolled `git worktree add`, since neither goes anywhere near this repo's scripts and they are the paths §1 names first. The hook only reports; run the script to act, `--all` to sweep every existing worktree at once (the migration path for worktrees that predate this), `--detect` in the main checkout to see the nested repos and write the config. Two halves. **Everywhere:** `.claude/settings.local.json` is gitignored and per-clone, so a fresh worktree has no `autoMemoryDirectory` — its session writes memory to the default location instead of the repo's, and re-prompts for every permission the main checkout already trusts. It is symlinked, not copied, so the two cannot drift. **In a constellation:** the sibling repos, per `docs/nested-repos.txt` — see §9. Worktrees created before this existed are repaired by running it; it is idempotent.
  - `scripts/generate_workspace.sh` — rebuilds a gitignored multi-root workspace file listing the checkout plus every worktree (and, in a constellation, each tree's sibling repos), for a single window showing all topics' source control at once. Useful as an occasional overview rather than the primary surface. Re-run it after adding or removing a worktree; editors watch the file and apply folder changes live, **in both directions, without reloading**. (`code --add` is not needed and is a trap: against a window opened as a plain *folder* it must promote the window to a workspace, and that promotion reloads — dropping every terminal, including the sessions in them.) `--topic <worktree>` writes a **different** file: that one topic's repos, which is what `open_worktree.sh` opens. The two answer different questions — "show me everything" versus "show me this topic" — and both are needed because the overview cannot be the window you work in without giving up one-window-per-topic. In a repo with no `docs/nested-repos.txt` the topic mode writes nothing at all, so nothing changes for single-repo consumers. `reap_worktrees.sh` deletes a topic's file when it removes the tree.
- **Reaping worktrees whose work has landed:** `scripts/reap_worktrees.sh` reports them, `--reap` removes them. Triggered by `scripts/git-hooks/post-merge`, which fires on `git pull --ff-only` — i.e. when the anchor pulls after merging a PR, exactly when a worktree becomes reapable. **No cron and no marker file:** every worktree is temporary by the rule above, so a "temporary" flag would be true for all of them and carry no information, while landed-ness is computed and therefore cannot go stale the way a marker left by a crashed session would. Reaping requires *all* of — landed (the branch's own files identical to `origin/main`), clean **including untracked** (scratch left in a worktree is exactly what must not be destroyed), idle (no live session's cwd inside it), unlocked, and — in a constellation — nested-clean: nothing inside it belonging to *another* repo holds unsaved work. That last check is not redundant with `clean`, and the gap it fills is a measured one: a nested sibling worktree is gitignored in the home repo, so `git status` cannot see it, and `git worktree remove` does not refuse either — it succeeds, deletes the sibling's uncommitted work along with the parent, and leaves a `prunable` registration behind. Unattended, from the post-merge hook. So the reaper dehydrates first, through the siblings' own repos, and removes the parent only if that succeeds. A branch that has committed nothing reports *empty*, never *landed* — otherwise a worktree opened moments ago is reapable before its session writes a line.
- **Session start ritual:** `git pull --ff-only` (or create the worktree fresh), skim `docs/user_attention.md`, and `git worktree list` if starting parallel work.
- **Finding a past session to resume:** run `scripts/list_sessions.py` — a generated index over the local transcripts (start/last-activity times, how each session started and ended, the directory each session ran from, branch tags, and a PARKED-ITEMS flag for sessions with entries on the attention board). Richer than `/resume`'s blurbs, always current, and never needs maintaining because it's derived on demand. `--repo` is repeatable: when several repos run Claude sessions on one machine, pass each one for a single combined index — the Where directory column is what tells the repos (and their worktrees) apart, since every repo has its own "main". *(adapt per repo)* Optionally cron it every ~10 min to a gitignored file (e.g. `docs/session_index.md`) so the index is always sitting there to glance at — write atomically (`> tmp && mv`) so a reader never sees a half-written file; the cron wrapper (`generate_session_index.sh REPO OUT EXTRA_REPO...`) takes the extra repos as trailing args.
- If a non-anchor session finds itself sharing the main checkout with another active session (unexplained branch switches, "file modified on disk" warnings): stop committing, snapshot work to a uniquely-named branch, move to a worktree, and tell the user. Never `git reset` shared state.
- **Everything under `~/.claude/` is machine-local** — the live-session registry, the transcripts `list_sessions.py` indexes, and any materialized session index cover only the machine they run on. The anchor guard still works everywhere (a checkout lives on one machine), but a session running on another machine is invisible here: the git remote, the board, and the ledger are the only cross-machine channels, and only once **pushed** (see §3).
- **`~/.claude/` may also be *ephemeral*, and that silently removes §3's last safety net.** If the checkout runs in a devcontainer or any disposable container, a rebuild can orphan the registry, the transcripts, and the session tails `/stranded-work deep` scans — the only mechanism that recovers work from a closed session's conversation, so the failure is invisible and total (it has happened twice in one consuming repo). **Rule: give `~/.claude` durable storage before relying on §3's safety nets — but never restore `~/.claude/sessions/` from a backup.** The mechanics — what to mirror, and what a whole-directory bind does to the anchor guard across pid namespaces — are in the [appendix](#appendix-containers-and-claude-durability); if your checkouts don't run in containers, you can skip all of it.

## 2. Work tracking — the technical-debt ledger

- `docs/technical_debt.md` is the single ledger of known issues, compromises, and deferred work. **Add a TD-NNN entry any time a problem is identified but not immediately fixed** — no exceptions, however small.
- **IDs are `TD-` plus a zero-padded three-digit number** (`TD-007`, `TD-142`), assigned in order and never reused. **Never encode priority in the ID** (`M24`, `L21`): priority is a field and changes over an entry's life, the ID must not — the day an item is re-prioritized, every reference to it is either wrong or a lie about its urgency.
- Entry shape: `## TD-NNN: title` + **Priority / Added / Status / Location / Why / Fix** fields. The Why is the valuable part — future sessions need the reasoning, not just the symptom.
- **The summary table at the top lists exactly the entries that have a body in the file** — one row each, `| ID | Title | Priority | Status |`. It is a table of contents, not a history: an entry enters it when it is filed and leaves with it. Any second copy of the same state drifts, and the table is the copy nobody remembers to update.
- **The live ledger holds open work only.** When an entry is fully resolved, move the whole body to `docs/technical_debt_archive.md` *and delete its summary row*. No `Resolved (archived)` rows left behind, no hand-maintained list of retired IDs under the table — the archive is the record, and `grep -rn TD-042 docs/` answers "where did it go" in one step. Archived entries are appended in resolution order, so the archive doubles as a chronological account of what the project has actually paid down.
- **Partially resolved stays live.** Archive only when nothing is left to do. Until then the entry keeps its row, and its Status says what shipped and what remains — `Guard shipped 2026-05-16 (never-demote in upsert SQL); root-cause diagnosis + Phase 3 cutover deferred`. Half-finished work that reads as finished is the main way a ledger lies.
- **An archived entry keeps its Why** — that is what the archive is for — and replaces **Fix** with `**Resolved:** YYYY-MM-DD (PR #n)` plus a pointer to whichever doc, test, or code now carries the lasting content. If nothing lasting needs promoting, archive the entry as-is.
- **Verify before picking a TD:** status lines lag reality. Grep the code first — entries have been found already-fixed, and "deploy pending" lines have been found already-deployed.
- **Numbering under concurrency:** the next ID is one past the highest in the live ledger, the archive, *and* other sessions' open PRs — pull `origin/main` and check open PRs before assigning. If two sessions collide anyway, the later merge renumbers before merging.

## 3. Never strand work

Work that exists only in a session's conversation is lost when the session closes. Two files catch it:

- **Ledger before idle:** the moment the user approves a multi-item plan, or a session pauses with steps undone, the pending steps go into the TD ledger (new entry or a line in the relevant one) — not just chat.
- **`docs/user_attention.md`** is the small, high-churn board of items that need the user to act or decide, plus in-flight work a session had to park. Entry format:
  `- [ ] YYYY-MM-DD (session <first-8-of-session-uuid> on <host>, or n/a) — what's needed. Context: TD-NNN / PR #N / branch. Due: YYYY-MM-DD.`
  The session id (the UUID directory in the session's scratchpad path) tells the user *which* session holds the context; `<host>` tells them *which machine* can resume it — transcripts don't travel, so a session id without a host is a dead pointer from any other machine. Use a name the user recognizes (`hostname -s`; in a devcontainer, name the host machine, not the container hash). Sessions add entries the moment they arise and remove them when handled. Keep the file tiny — durable debt belongs in the ledger; this board is only "a human must act".
- **Push before you pause:** commits that exist on only one machine are stranded from every other machine — a `/stranded-work` sweep or `list_sessions.py` run elsewhere cannot see this machine's unpushed branches or transcripts. When a session parks work (board entry, ledger line, or just going idle with commits made), push the branch — WIP state is fine — and name the branch in the board entry so any machine can pick it up. The board and ledger are only cross-machine once committed *and pushed*.
- **Due dates drive the wake-up (TD-7, decided 2026-08-12):** give each board item a `Due:` date matched to its real urgency — **snoozing is just editing the date**, so a busy week costs one keystroke, not a lost commitment. The push channel is the SessionStart hook in the settings template: `nudge_user_attention.py --report --due-only --fetch` prints "N attention item(s) due across this machine's repos" into session context when anything on the roster is due, and stays completely silent otherwise — no external service, and it fires wherever sessions actually start, which is the only place action can be taken anyway. The `--fetch` is what lets a line pushed by a service account (§4's second carve-out) or by another machine surface *before* anyone pulls (TD-030): each roster repo is fetched under a hard aggregate budget (`ATTENTION_DUE_FETCH_BUDGET`, 8 s), and a clone that is merely behind — local board untouched since the merge-base, origin's moved — has its board read from `origin/<default>` and the row tagged `[origin/main — local clone behind, pull]`; local-only or two-sided differences keep reading the local file. Offline, every fetch degrades to a skip and the line is exactly what it was before: this machine's clones. The settings file is SEED, so a consumer seeded before 2026-08-26 adds the `--fetch` (and the 20 s timeout) to its own hook line by hand. The full pull view is `/attention` (`--report`), where undated items also surface. Those two — the SessionStart line and `/attention` — are the channels that are on by default, and they need no setup. A true external push channel also survives in the script for anyone who wants one: point a cron at `scripts/nudge_user_attention.py --board <path>` with `NUDGE_COMMAND` (any mailer/notifier/webhook via stdin) or `TELEGRAM_BOT_TOKEN`+`TELEGRAM_CHAT_ID`, and the cadence-sync staleness note rides along on delivered messages. It is **optional, unclaimed, and unverified** — the old `Nudge:` claim machinery that used to track delivery (board header lines, guard check #5's crontab verification, the report's claim note) is gone, deliberately and completely, so no dead machinery lingers. The nudge's Monday "run /stranded-work" reminder is retired with it; the `Swept:`/`Swept-deep:` staleness warnings are its designated successor.
- **Sweeps stamp the board (TD-12, TD-13):** `/stranded-work`'s final step writes a committed `Swept: YYYY-MM-DD (<host>, quick|deep)` header line on the board, and a **deep** run also writes `Swept-deep: YYYY-MM-DD` — quick runs never touch that second line, so the last transcript-scan date survives later quick sweeps: one slot per claim, and `quick` structurally cannot claim `deep`'s coverage (TD-13). The machine-wide report (`nudge_user_attention.py --report`, the `/attention` skill) warns when `Swept:` is absent or past 10 days, and separately when no deep sweep is on record or the last is past 30 — a `Swept:` stamp whose own mode is `deep` counts as deep recency, so pre-TD-13 stamps never false-warn. Cadence is weekly-ish quick, monthly-ish deep. **Parity (§7):** format, thresholds, and mode marker are enforced in two places — the stranded-work skill (writer) and `nudge_user_attention.py`'s `SWEEP_RE`/`SWEEP_DEEP_RE`/`SWEEP_STALE_DAYS`/`SWEEP_DEEP_STALE_DAYS`/`sweep_mode()` (reader); change them together. The mode marker is *validated*, not just echoed: a stamp claiming neither `quick` nor `deep` warns, because a bare date is a timestamp and not a statement about coverage. **The staleness warning fires even on a board with no open items — deliberately (TD-14):** the sweep covers PRs, worktrees, unpushed commits, and memories, so an empty board proves nothing about the rest; a freshly adopted repo's first warning is its prompt to run a first sweep. (TD-14 originally documented this as an intended asymmetry with the `Nudge:` claim check; that check retired with TD-7, and the unconditional warning stands on its own.) Stamp commits are one of §4's two carve-outs (TD-16; the other is service-account board appends).
- **Periodic sweep:** run `/stranded-work` (weekly, or whenever in doubt) — it checks the attention board for aged items, greps the ledger for `deploy pending`/watch-items, lists open PRs and worktrees by age, finds unchecked boxes in recent design docs and past-due dates in memories. Deep mode scans closed-session transcript tails.

## 4. Review and merge loop

- **All changes go through branch → PR → squash merge.** Never commit directly to main, even for docs.
- **Bounded carve-out — the sweep stamp (TD-16):** a commit touching ONLY the board's `Swept:`/`Swept-deep:` stamp line(s) may be pushed directly to main with `ALLOW_MAIN_PUSH=1` — one file, one line class, no review value, and a PR per stamp trains either skipping the stamp or making the override routine, which are the two worst outcomes. Anything else in the commit voids the exception. This is the only sanctioned *routine* use of the `ALLOW_MAIN_PUSH=1` override by a human or session (§4's other carve-out is written by a service account through the API and never touches the hook) — and it is a convention, not machinery. The pre-push hook checks only the env var; it cannot see whether the commit really touches just stamp lines. Honoring that scope is on the humans and sessions doing it, audited in review.
- **Bounded carve-out — service-account board appends (added 2026-08-26):** an unattended service account (a pipeline that sees an event no session is present for) may push a commit directly to main that ONLY appends board entries — one or more whole `- [ ]` lines inserted under `## Needs the user` in the board, in the documented `Format:` shape, with a `Due:` date. It must never edit or remove an existing line, never touch the `Swept:`/`Swept-deep:` stamps, and never touch another file; anything else in the commit voids the exception. The justification is TD-16's, sharpened: a PR here adds no review value *and* cannot complete — it needs a human to merge it, and the whole reason to write the line is that no human is watching. Conditions on the writer: **idempotent on a stable event id kept in its own store** (a board line is meant to be handled and removed, so scanning the file is belt-and-braces, never the dedup authority); bounded retry on `sha` conflict, then fall back to its existing notification channel; and **alert on that fallback** rather than fail silently. Sessions may edit or close these lines like any other item — the writer's append-only discipline binds the writer, not the humans. As with TD-16 this is a convention, not machinery: neither the pre-push hook nor the GitHub Contents API can see the commit's scope, so it is audited in review. **The reader half (TD-030, landed 2026-08-26):** `nudge_user_attention.py --fetch` reads a merely-behind clone's board from `origin/<default>` (§3), so a bot-appended line surfaces at the next SessionStart on any machine whose hook line carries `--fetch` — no pull needed. `/attention remote` remains the on-demand view for repos not cloned here.
- **Give the rule teeth:** on paid-plan/public repos, enable a branch ruleset requiring a PR with **0 required approvals** (the fast path stays fast — self-merge immediately — but every change is a visible, revertable unit and accidental direct pushes are impossible, admins included). Private repos on GitHub's free plan can't use server-side protection; there, commit a pre-push hook (`scripts/git-hooks/pre-push`) and enable it per-clone with `git config core.hooksPath scripts/git-hooks` — config is shared across worktrees, so one setting covers all concurrent sessions on the machine. Deliberate override: `ALLOW_MAIN_PUSH=1 git push`. Also switch on the repo's **Automatically delete head branches** (`gh api -X PATCH repos/<owner>/<repo> -f delete_branch_on_merge=true`, any plan): with squash merges nothing on the client side ever recognises a PR branch as merged (`git branch -d` refuses, `--merged` lists nothing), so unless the server deletes the head every merged PR leaves one behind — a consumer measured **83** on its remote before switching it on (2026-08-27). One caveat: the branch a *stacked* PR is based on gets deleted at its own merge too, and GitHub retargets the stacked PR onto the default branch — under squash merges its diff then re-includes the already-landed work. Stack on the default branch, or expect a rebase.
- **Self-review before merge:** every self-authored PR gets an independent review by a cheaper-model agent (Sonnet) before merging — code PRs get a correctness review; **doc-only PRs get a fact-check against the repo** (doc errors have historically been worse than code errors). Fix findings, re-verify, then merge.
- **Auto-merge threshold:** cleanup/doc/self-contained-fix PRs with review = SHIP and tests green merge without re-asking the user. Behavior changes, product decisions, and anything irreversible wait for them.
- Tests + lint run before the PR (`pdm run test`, `pdm run lint` — *adapt per repo*).
- Loop for backlog work: pick a TD → verify its code state → fix + test → review → merge → next.

## 5. Deploys are part of "done"

Merged ≠ deployed ≠ verified. Any merge that changes deployed behavior either (a) deploys and verifies in the same session, or (b) records a `deploy pending` line — in the TD entry *and* on the attention board if a human must trigger it. The `/stranded-work` sweep compares pod/service start times against recent main commits to catch merged-but-never-rolled-out fixes *(adapt per repo — e.g. a k8s repo compares `kubectl get pods --sort-by=.status.startTime` vs `git log`)*.

## 6. Watch items get dates

Every "watch this and confirm later" commitment gets a **check-by date and where to measure** written into its TD entry (e.g. "re-check fires→0 in logs by YYYY-MM-DD"). Open-ended watches are how monitoring commitments silently die; the sweep flags past-due ones.

## 7. Memory and docs

- Durable cross-session knowledge lives in `docs/claude-memory/` (one fact per file, frontmatter with a `name:` slug) indexed by `MEMORY.md` (one line per memory — pointers only, never content). Update or delete stale memories on contact; wrong memories are worse than none.
- **Memories are git-tracked and committed** like any other doc — that's what makes them durable across machines and visible in review. Claude Code must be pointed at the repo dir via the `autoMemoryDirectory` setting, and a SessionStart hook (`scripts/check_claude_memory.sh` here) guards against the silent failure mode where memory falls back to `~/.claude/projects/.../memory/` outside the repo and is never committed.
- **Memory is never a decision's only home.** Memory is agent-facing — read by the next session, not by a human asking "why did we decide X" — and it sits outside the reviewed path of a PR. A memory may *mirror* a decision; the version-controlled doc (an ADR in `docs/decisions/`, a design doc, a contract doc) owns it. A repo that finds load-bearing decisions living only in memory adds a routing line to its CLAUDE.md — decision type → owning doc — and treats memory as the working note that points there. This is the churn-file rule one level up: the content survives while becoming unfindable by the people who need it.
- Architecture decisions get ADRs in `docs/decisions/`. CLAUDE.md stays a **map, not a manual** — quick-reference commands, a documentation table, and the few inline sections used constantly; everything else links out.
- Approved multi-phase plans get a TD entry per not-yet-started phase (a plan doc's unchecked boxes are invisible to future sessions; the ledger is not).
- **Load-bearing content never lives in churn files.** Anything durable that lands in a handoff stub, per-batch note, or scratch doc that gets rewritten — or only in a commit message — gets extracted to a durable home (contract doc, ADR, memory) before the churn file turns over. Corrections follow the same rule: when a recorded rule turns out wrong, write the correction into the durable doc future sessions will actually read — old commit messages and stale docs keep resurfacing the wrong version forever.
- **Parity rules get written down once.** When one rule or data shape is enforced in several places (the same filter applied in app + digest + API; N renderers of one payload), a single doc names the rule and lists *every* enforcement site; changes update the spec first, then all sites together — never one alone.
- **A gotcha a test can pin gets a test, not just a memory** — especially silent-drop layers (allowlists, mappers, serializers) where the next missed field vanishes without an error. When the failure class is *behavioral* — every static gate passes and only running the thing reveals it — the pin is a **runtime canary**, and the memory's job is to name the *trigger conditions* that should invoke it (the kinds of edits, the helpers involved), not to describe the bug: the green type-check and lint are positive evidence of health that will overrule a be-careful memory, and a canary nobody knows when to run is a canary nobody runs.
- **A test that asserts against the current date derives its fixtures from the current date.** A hardcoded calendar fixture passes on the day it is written and starts failing on a schedule, each new failure reading as a regression in whatever happens to be in flight — and a suite that is expected to fail stops being read (dev-cadence TD-24).
- **Gates judge the index; detectors judge the disk.** Before a check enumerates files, decide which kind it is, because the right source is opposite for each. A **gate** blocks something (a commit, a push, a merge), so it must consider only what is entering the repo — `git ls-files`, not a filesystem walk. A gate that walks the disk can be made permanently red by untracked scratch that will never be committable, and a permanently-red gate does not get fixed, it gets routinely bypassed (`--no-verify`), which quietly disables every other check sharing that hook. A **detector** hunts for work that is lost or stranded, so untracked files are its highest-value target, not noise — it must walk the disk, and making it "git-aware" deletes its reason to exist. The memory guard's fallback check is a detector by definition: it looks for memory written *outside* the repo, which git cannot see. Mixing the two in one check is the trap — a disk walk narrowed by a `git log` window silently drops every untracked file while still reporting a clean result.

## 8. Subagent discipline

Subagent prompts state their scope and bounds explicitly — read-only vs. write, and whether production systems/secrets are in bounds. Agents doing doc/audit work must not read secrets or touch production state; if an agent exceeds scope, the parent session reports it to the user rather than silently using the output.

- **A subagent's summary is evidence, not a source.** Delegating the *reading* of an authoritative artifact and then building from the prose that comes back is a lossy step that looks lossless: summaries preserve content and intent while dropping structure, order, and proportion, and nothing in a fluent summary signals what it dropped. So the output is plausible, confident, and wrong in exactly the dimension nobody re-checks. When work has to *match* an authoritative artifact — recreating a design from its export, porting a spec, reimplementing a documented contract — the session doing the building reads that artifact itself, however long it is. A subagent may find it, rank it, or report what changed in it; its prose never stands in for it. **Corollary for ties:** an explicit authority marker inside the source (a `canonical` annotation, a "this supersedes" note) beats any secondary index, digest, or reconciliation doc that disagrees — summaries and index docs are the layers where such markers get flattened away first. *(Reported by a consuming repo: a UI screen rebuilt from agent summaries came out as a two-column layout when the exported source was a single-column flow, and the export had explicitly marked which variant was canonical. The user spotted it on sight; the summaries had read as complete.)*

## 9. Machine scope

> **Repos own their boards; the machine owns the roster of repos.**

Committed state stays per-repo (boards, ledgers, locks — it survives sessions and travels
between machines via git). The roster of cadence repos on a given machine is inherently
machine-local, so it gets exactly one machine-local file, and every machine-scope feature
reads it instead of keeping a private list:

- **Registry:** `${XDG_CONFIG_HOME:-~/.config}/dev-cadence/repos.txt` — one absolute
  main-checkout repo root per line; `#` comments and blank lines allowed. Scripts append,
  only humans delete lines (a machine stops hosting a repo → edit the file).
- **Written by:** sync.sh (registers each repo it installs cadence into, as its LAST
  mutating step), sync-all.sh (self-registers the dev-cadence clone), or hand-editing.
  All writes are idempotent, flock'd check-then-append.
- **Read by:** the SessionStart guard's check #6 (warns when this repo's board has open
  items but the roster doesn't cover it) and `nudge_user_attention.py --report` (the
  machine-wide view behind the `/attention` skill).
- **Remote-tier filter (optional):** `${XDG_CONFIG_HOME:-~/.config}/dev-cadence/remote_repos.txt`
  — restricts `/attention remote`'s GitHub discovery. One `owner` or `owner/repo` per
  line (`#` comments allowed); missing or empty file = full discovery (authenticated
  user + all orgs). The remote tier shows **pushed board state only** and skips archived
  repos — so sweep a repo's board *before* archiving it, or its open items silently
  leave the remote view (TD-6, plan 2026-08-13).

**Path-resolution spec (SINGLE source of truth — all enforcement sites follow it, and a
change here updates all of them together, per §7's parity rule):** an entry is the
repo's **canonicalized main-checkout root** — resolve worktrees via `git rev-parse
--git-common-dir` (parent of the common dir), then canonicalize with `realpath`; writers
canonicalize before the append-if-missing check, and readers canonicalize AND dedupe
again at read time (defense-in-depth — a duplicate or symlink-spelled line must never
yield a duplicate report row). Where `realpath`/`flock` are missing, degrade the same
way: skip the canonicalization/lock and rely on read-time dedupe. Enforcement sites:
**sync.sh** (target registration), **sync-all.sh** (self-registration),
**check_claude_memory.sh check #6** (coverage), **nudge_user_attention.py**
(`read_registry()` / report mode).

**Machine-locality assumption:** `~/.config` (or `$XDG_CONFIG_HOME`) is private to one
machine — not NFS-shared across hosts and not bind-mounted into devcontainers. A
shared-`$HOME` topology would make the registry silently cross-machine; a devcontainer
needs a *persisted* `~/.config` volume for its registry to survive rebuilds (an ephemeral
one loses roster coverage silently — by design the degradation is silence, never a false
warning; check #6's origin-URL fallback also keeps a bind-mounted or deploy-mirror
checkout of an already-registered repo from false-warning).

Reader semantics: a missing registry file means "no roster on this machine" (features
fall back to current-repo scope), not an error; roster lines whose path is gone or isn't
a git repo degrade to a per-row note, never a failure. Cross-machine visibility is out of
scope here: boards are committed files, so remotes already carry them — see the
dev-cadence ledger's cross-machine entry for the planned remote tier.

### Constellations: one project, several repos

> **A constellation installs cadence ONCE, in a repo it names the home. It is a convention, not a feature.**

*Single-repo project? Skip this whole subsection — nothing in it applies to you.*

Some projects are one product spread over several repos with separate remotes — an umbrella
plus per-surface siblings, a docs repo, a native repo per platform. Installing per-repo
multiplies every singleton the system has: N boards, N `Swept:` stamps to
keep from going stale, N rows in `/attention` for what is one project. That is the wrong shape,
because the work being tracked belongs to the *project*: an item parked in one sibling needs the
user to act on the product, not on a repo.

So: **run `sync.sh` against exactly one repo — the home — and install nothing in the siblings.**
Each sibling carries only a committed `.claude/settings.json` whose SessionStart hooks point at
the home's `scripts/` — and that is **not** the stock template `sync.sh` installs: the stock
hooks say `"$CLAUDE_PROJECT_DIR/scripts/check_claude_memory.sh"`, which resolves to the
*session's own repo*, i.e. to a `scripts/` directory the sibling deliberately does not have.
Write the hop to the home explicitly, e.g.
`"$CLAUDE_PROJECT_DIR/../<home-repo>/scripts/check_claude_memory.sh" --hook`, and set the
sibling's `autoMemoryDirectory` in the same file by hand (step 2 of the checklist is a
`sync.sh` side-effect the sibling never gets). The relative hop is brittle — it assumes every
sibling is checked out beside the home, and a checkout laid out differently gets hooks that
point at nothing. The board, the ledger, `cadence.md`, and the skills live in the home and
nowhere else. §9's registry lists the home only, so the machine-wide report shows one row per
project rather than one per repo.

**Choose the home by where sessions actually run, not by where the code lives.** Every guard and
skill resolves paths from the session's repo, not the repo being edited, so a board in a repo
nobody opens a session in is a board nobody reads. Count it before deciding — in the
constellation this guidance came from, 19 of 25 recorded sessions ran in the umbrella while the
commits landed almost entirely in two siblings, which is the opposite of where the home was first
assumed to belong.

**Know the edge, which cuts both ways:** `/stranded-work` and `/attention` are meaningful only
from the home. Run from a sibling they resolve to a board and ledger that do not exist and report
nothing — quietly, because those checks are gated on the files existing. Say so in the project's
CLAUDE.md.

Run from the *home*, the sweep has the opposite problem: its checks are scoped to the repo it
runs in, and in a constellation the cadence lives in the home while the **content** lives in the
siblings. §7 says detectors judge the disk — in a constellation that disk is the whole family,
not one repo. So when sweeping a constellation, widen the repo-scoped checks by hand:

- **Git state** across every sibling, not just the home. Unpushed commits, stashes, and dirty
  trees in a sibling are invisible from the home, and the skill itself calls unpushed work the
  worst stranding class.
- **Unchecked boxes / plan docs** across every sibling. In the constellation this guidance came
  from, the home repo returned 2 hits (both template examples) while a sibling held 57.
- **Memories** wherever `autoMemoryDirectory` points, which in a constellation is routinely a
  different repo than the one being swept.

A sweep that quietly covers one repo of four is worse than no sweep, because the clean result is
the evidence people act on. *(adapt per repo)*

**Worktrees need the siblings brought in, or §1 and this section contradict each other.** §1 says
every non-anchor session works in a worktree. A worktree contains what the home repo *tracks* —
and in a constellation the siblings are separate gitignored clones, so it contains the cadence and
none of the content. Both instructions are right and, taken literally together, unfollowable: the
isolation is paid for in full and buys nothing, because the files the session came to edit are not
there. Symptoms read as three unrelated bugs — the sibling folders are missing, the memory guard
warns that `autoMemoryDirectory` points nowhere, and the generated workspace shows one root where
the project has four.

`scripts/hydrate_worktree.sh` closes it, driven by **`docs/nested-repos.txt`** in the home: one
line per nested repo, path then mode.

| mode | what a worktree gets | costs |
|---|---|---|
| `worktree` | its own worktree of that sibling, on a branch named for the topic | a branch in that sibling per topic even when untouched; no build artifacts, so an install step must be re-run |
| `link` | a symlink to the home checkout's clone | **no isolation** — every topic shares that clone's HEAD and index, the collision §1 exists to prevent |
| `skip` | nothing | — |

The mode is per repo because the right answer differs per repo inside one project: the docs sibling
every topic edits wants `worktree`; the app sibling with a large install and a running dev server
wants `link`, and accepts the shared HEAD to get it. Repos are **detected**, not declared — a
sibling with no config line is named at every hydrate, with the line to add, so a newly-cloned one
announces itself instead of being quietly absent from every worktree. Detection deliberately does
not choose the mode; nothing on disk distinguishes a sibling worth branching from a vendored
dependency.

Two edges worth knowing before they bite:

- **Teardown order is load-bearing.** Dehydrate before `git worktree remove`, always. A nested
  sibling worktree is invisible to the home's `git status` and does not make `remove` refuse;
  measured, `remove` deletes it and its uncommitted work without a word. `reap_worktrees.sh` does
  this correctly on its own — the rule is for removing a worktree by hand.
- **Write the sibling's ignore rule without a trailing slash.** `/guardians-docs/` is
  directory-only and does not match a `link` mode symlink, which is a file, so the link shows as
  untracked forever and the worktree can never satisfy reap's `clean` check. Hydration warns and
  prints the fix rather than editing `.gitignore` for you.

**Memory does not have to live with the install.** `autoMemoryDirectory` decides where memory
goes and the guard follows it, so a constellation can keep one shared memory directory in
whichever repo owns durable project knowledge while the cadence install lives in the home.

This is deliberately convention rather than machinery. A "cadence family" with pointer files and
resolution logic would touch every hardcoded board/ledger/memory path across the SYNC set, all
of which would then need §7 parity — a framework for a shape only one known consumer has, and
against this project's no-framework non-goal. If a second constellation adopts and the convention
chafes in the same place twice, that is the signal to build it, and it will be better specified
for having watched this run.

## Adoption checklist for a new repo

1. Clone [eyecantell/dev-cadence](https://github.com/eyecantell/dev-cadence) and run `./sync.sh /path/to/new-repo` — installs this file, the board/ledger/memory skeletons, the `stranded-work` and `attention` skills, the memory-guard hook script, the nudge script, and the pre-push main guard, writes `docs/cadence-sync.lock`, and registers the repo in this machine's roster (§9).
2. Guards: sync.sh configures both per-clone settings itself — `core.hooksPath scripts/git-hooks` and `autoMemoryDirectory` (the clone root's **absolute** `docs/claude-memory` path, merged into `.claude/settings.local.json`; values must be absolute or `~/`-prefixed — relative paths are invalid). It never clobbers a divergent existing value (it warns instead — heed WARN lines in its output). **Both are per-clone/per-machine — re-run sync.sh against each additional machine's clone** (the SessionStart guard warns when either is missing, so a new machine self-reports rather than silently regressing). Exception: if every checkout lives at one stable absolute path (e.g. a devcontainer's `/workspaces/<repo>`), `autoMemoryDirectory` can instead go in the committed `.claude/settings.json` and covers all machines at once (honored after the workspace-trust prompt) — the value must be absolute (no relative paths or variables), so this only works when the path really is identical everywhere. The seeded `.claude/settings.json` carries the SessionStart hooks — if the repo already had settings (sync.sh never overwrites an existing one), merge in the **entire `hooks` block** from dev-cadence's `files/.claude/settings.json` by diffing it, not by copying individually named hooks: the set grows over time, and a hand-picked subset silently drops whichever hook your list predates.
3. Add a "Session Hygiene" section + Documentation Map rows to the repo's CLAUDE.md pointing at this file, the ledger, and the attention board.
4. Set the repo to squash-merge; agree on the auto-merge threshold (§4) for that repo's risk profile; add a server-side 0-approval PR ruleset if the plan allows (the pre-push hook covers free-plan private repos).
5. The SessionStart due-items line ships in the settings template — nothing to wire (TD-7). Optional true push: point a daily cron/scheduler at `scripts/nudge_user_attention.py --board <path-to-board>` with `NUDGE_COMMAND` or `TELEGRAM_BOT_TOKEN`+`TELEGRAM_CHAT_ID` (§3) — optional, unclaimed, unverified.
6. If this repo's checkout runs in a container (devcontainer or similar), confirm `~/.claude` has durable storage before relying on `/stranded-work deep` or `list_sessions.py` — see the [appendix](#appendix-containers-and-claude-durability). Watch the transition case specifically: a container created *before* its `~/.claude` volume was declared keeps that directory in its writable layer, so the first rebuild after adding the volume mounts an empty one and the old transcripts survive only inside the old container — recoverable, but only until it is pruned. Copy them out during that rebuild, not after.
7. **Multi-repo project?** Pick the home repo first and install only there — see
   [Constellations](#constellations-one-project-several-repos) above. Choosing it by where
   sessions run rather than where the code lives is the part that is easy to get backwards.
   Then write `docs/nested-repos.txt` before opening the first worktree: run
   `scripts/hydrate_worktree.sh --detect` in the home checkout and it lists every nested repo
   it can see with the line to add. Skipping this does not fail loudly — it produces worktrees
   that are simply missing the siblings, which reads as a tooling bug rather than a missing
   config, so it is worth the one command up front.
8. Pulling cadence updates later: `git -C <dev-cadence-clone> pull && ./sync.sh <repo>`, review the diff in the target repo, PR it like any other change. Suspect a synced copy was edited locally? `./sync.sh --verify <repo>` (read-only, TD-8) tells local drift apart from an upstream upgrade using the lock's per-file hashes — run it *before* a re-sync silently overwrites the evidence.
9. **Before editing anything under `scripts/`, `docs/cadence.md`, or `.claude/skills/`, read the file's first lines.** Synced files open with a header naming their canonical path in dev-cadence; an edit made here is destroyed by the next sync, without warning, and the fix has to be ported upstream anyway. The header is the answer to "is this file ours?" — `docs/cadence-sync.lock` is the exhaustive list, and `--verify` above is the after-the-fact detector.

## Appendix: containers and `~/.claude` durability

Only relevant when a checkout runs inside a container (devcontainer or similar). §1 states the rule — durable storage for `~/.claude`, never restore `sessions/` from backup — and this appendix is the reasoning and the sharp edges.

**Why durability, in full.** Machine-local says *who* can see `~/.claude`; it does not say *how long it lives*. A containerized checkout usually keeps `~/.claude` in a named volume or the container's writable layer, so a rebuild can orphan all of it: the live-session registry (`check_anchor.py`), the transcripts `list_sessions.py` indexes, and the session tails `/stranded-work deep` scans. The deep transcript scan is the only mechanism that recovers work left in a *closed session's conversation* — precisely the loss this whole system exists to prevent — so when it quietly has nothing to scan, the failure is invisible and total. This is not hypothetical: one consuming repo lost transcripts to it twice, on two different container hosts (2026-07-23 and 2026-07-24), each time recovering only because someone remembered to copy `~/.claude` out of the old container by hand. The fix is a host bind, or a hook-driven mirror of `~/.claude/projects/` onto one, restoring when the volume comes up empty. Mirror the *transcripts*; leave `~/.claude/sessions/` alone, because a restored registry entry whose recorded pid now belongs to some unrelated live process reads to the anchor guard's `/proc` liveness filter as a live session, and would manufacture anchor conflicts out of nothing. Such a mirror runs on session hooks, so it must never break or slow a session: bound its total runtime under the `timeout` declared on the hook, exit 0 on every failure path, rename copies into place rather than writing them in place, never delete from the backup, and no-op entirely when the container bind isn't present. *(adapt per repo)*

**Worktrees record container-absolute paths, so nothing on the host may prune them.** A worktree created inside the container writes the *container's* view of its path into `.git/worktrees/<name>/gitdir` — e.g. `/workspaces/<repo>/.claude/worktrees/<topic>/.git` — even though the repo itself lives on the host and is only bind-mounted there. Read that same repo from the host, where the path is `~/dev/<repo>/...`, and every container-created worktree looks like its directory is **gone**. That is precisely the condition `git worktree prune` exists to clean up, so a host-side scheduler running it — or any tool that calls it — silently de-registers live worktrees, leaving their directories on disk as orphans that `git worktree list` no longer knows about. The rule is blunt because the failure is quiet: **run worktree maintenance from the same namespace that created the worktrees.** In practice that means in-container, which also means a host cron is the wrong home for it; §1's teardown belongs to a session, and a session runs where the worktrees are real. (Nothing here is container-specific beyond the path split — any setup where one repo is reachable at two absolute paths has it.)

**A whole-`~/.claude` bind shares the session registry across pid namespaces — the durability fix and the anchor guard want opposite things.** The bind above is the simplest way to make transcripts durable, but it cannot be scoped to `projects/`: it carries `sessions/` too, so the host and every container generation read and write ONE registry whose files are named by pid. A pid only means something inside the namespace that issued it, so entries left by the host, or by a previous container, get resolved against a *different* `/proc` — where low pids are readily in use by unrelated processes. That is the same false-liveness the mirror rule above avoids by leaving `sessions/` alone, except a bind makes it permanent rather than one-shot. `check_anchor.py` therefore **corroborates** each entry instead of trusting `/proc/<pid>` existence, and an entry is dropped only when something positively disproves it. The registry records `procStart` — the process start in the same clock ticks as `/proc/<pid>/stat` field 22 — so comparing them proves *same process* rather than merely plausible one; that is the primary test, and it needs neither a process-name allowlist nor any wall-clock conversion. Entries predating that field fall back to start-time ordering (a session writes its own entry, so its process always predates it), and last of all to the process command. Anything undeterminable keeps the entry, so the guard degrades toward a redundant warning rather than toward silence. If you bind `~/.claude` wholesale, expect stale entries to accumulate there indefinitely — they are inert, but `check_anchor.py` standalone is what tells you so. Binding only `~/.claude/projects/` (leaving `sessions/` container-local) avoids the sharing entirely and is the cleaner shape where the container runtime allows it.
