---
name: agentorc-td-grind-mechanics
description: "Gotchas for scripted ledger edits, PR comments and merging in agentorc (template headed TD-001, never guess a PR number, the cadence verdict goes on the comment's first line, archive-tail conflicts, the **Resolved:** label)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 31a6d882-e7d4-40aa-a6a0-3e7bb44d4af9
  modified: 2026-09-14T18:40:00Z
---

Learned running the 2026-09-09/10 unattended TD grind in agentorc (session tdgrind-ao-1):

- `docs/technical_debt.md` carries an `<!-- Entry template … -->` block that is itself headed
  `## TD-001: Short title of the problem`. Any regex that finds an entry by `## TD-NNN:` must
  start searching *after* the template's `-->`, or it moves the template and leaves the comment
  unclosed (this happened once, caught in review of PR #44).
- `gh pr merge --squash` is silently refused while CI is still pending (`mergeStateStatus`
  UNSTABLE); `gh pr view N --json state` then still says OPEN. Poll
  `gh pr view N --json statusCheckRollup` for `SUCCESS` first. `gh pr checks` output has a
  space in the check name, so `awk '{print $2}'` is wrong.
- Every TD PR appends to `docs/technical_debt_archive.md`, so consecutive merges conflict on the
  archive tail (and on the ledger when two PRs delete neighbouring entries). Rebase each branch
  right before its merge; keep-both is always the right archive resolution.
- An archived entry needs the literal `**Resolved:** <date> …` label; `tests/test_ledger.py`
  fails CI on anything else (`**Resolved 2026-09-23**` failed PR #475). Run that test before
  pushing even a doc-only ledger PR. The cadence comment's first line is
  `cadence-review: SHIP | FIXED | BLOCK · <model> · <code|docs> · <n> findings`, and a comment
  is never edited: a malformed one is superseded by a new one.
- `pkill -f <pattern>` kills the wrapping shell when the pattern appears in the command line
  itself; kill by pid. (Run 2 hit it again: exit 144 on the whole Bash tool call.)
- A probe script that starts a child agent must put `AGENTORC_HOME` under a *short* path: the
  session scratchpad path is too long for `agent.sock` (`OSError: AF_UNIX path too long`) and
  the child waits forever for a socket that never appears.
- Sonnet reviewers should be told to read the PR via `gh pr diff N` and
  `git show origin/<branch>:<path>` (own throwaway worktree for running tests), never the
  working copy — then the checkout is free to move to the next TD while the review runs.
- CI runners lag tmux's exit status: a pane can read `dead=1` with no `pane_dead_status` for
  more than 6 s (seen on 3.13 only, PR #52). Do not assert `exit_code` right after `exited`.

Added 2026-09-14 (same session, a later run, ten PRs merged):

- **Never write a PR number into the ledger before the PR exists.** Guessing "the next free
  number" is wrong whenever another session merges first, and several do. I did it twice in one
  run — wrote #140 and #146, both of which went to other sessions' PRs — and each needed its own
  correcting PR. Open the PR, read the number it returns, then write the ledger line. No test can
  catch this: a PR number is not a ledger id and nothing checks a citation against GitHub.
- **`scripts/check_cadence.py` reads the `cadence-review:` verdict only on a comment's FIRST
  line** (`REVIEW_RE` against `body.splitlines()[:1]`), while every reviewer is briefed to *end*
  its report with that line. A comment written the natural way reads as "no review at all", and
  once the PR is merged the row can never go green — editing the comment afterwards fails on
  `updated_at > merged_at`. Put the verdict line first. Ledgered as TD-050.
- **Post PR comments from a file, and read them back.** `gh pr comment --body "$(...)"` with a
  command substitution that fails silently posts the *error output* as the comment. I destroyed
  two real review-evidence comments that way (an `gh api .../issues/$pr/comments/$cid` call that
  404'd became the comment body). Write the body to a file, POST with `--input`, then
  `gh api .../issues/comments/$ID -q .body | head -1` to confirm.
- **`gh pr list --author @me` is not "my PRs".** Every session on this machine pushes as the same
  GitHub account, so that lists *every* session's open PRs. At wrap-up I took one for my own
  in-flight work, commissioned a review of it, and only found the owner when `git checkout` said
  the branch was already used by another worktree. Identify ownership first: `git worktree list`
  (a branch checked out elsewhere is someone else's), and the branch name against your own
  `ao progress` claims.
- **Never `git checkout <file>` to undo a test fixture edit** while you have uncommitted work in
  that file — it silently discards the real change too. Copy the file aside and copy it back.

Added 2026-09-22 (grinder-ao-1):

- **The verdict word is `SHIP`, `FIXED` or `BLOCK`, never `PASS`.** The first line must be
  `cadence-review: FIXED · <model> · <code|docs> · <n> findings`. A reviewer briefed to answer
  `cadence-review: PASS` produces a comment the check reads as no review at all. Ask the
  reviewer for its findings, then write the verdict line yourself. Post a corrected comment
  rather than editing the old one.
- A TD number is taken on main between your write and your merge (2026-09-24: TD-126 filed by the
  anchor while a PR carrying its own TD-126 was open). Before pushing a new entry, and again at the
  rebase, read `origin/main`'s highest `## TD-` and renumber; keep main's rows and yours row-wise.

**Why:** these cost real time and one wrong PR state on the first run.
**How to apply:** when scripting ledger moves or chaining merges in this repo, reuse these rules;
a helper that skips the template block lives only in a session scratchpad, so rewrite it.
Related: [[unattended-workers-run-inside-agentorc]].
