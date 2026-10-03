---
name: gate-everything-after-a-merge-on-its-state
description: "never chain branch deletion or \"merged\" reports after `gh pr merge`; check state is MERGED first — a deleted branch closes the PR for good"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 866f2602-78a4-4c74-a483-2bbcacf81063
  modified: 2026-10-02T23:50:00.000Z
---

After `gh pr merge`, read `gh pr view N --json state -q .state` and go on only when it says `MERGED`. Never put the branch's deletion, `ao progress done` or the *merged* notes in the same command line as the merge.

**Why:** on 2026-09-28 PR #727 failed to merge on a conflict (main had moved after CI passed), and the `;`-chained commands after it deleted the remote branch and told the manager and Paul it was merged. GitHub closes a PR whose branch is deleted and refuses to reopen it once the branch is recreated or force-pushed, so the work needed a new PR (#730), a second review-evidence comment and two corrections by mail.

It happened again on 2026-10-02 (#895): the `ao progress done` and the notes were gated on `MERGED`, but a *loop* deleting two remote branches sat in the same command after the gate, ungated, and the merge had failed on a fresh conflict. This time `git push origin <branch>` of the same sha followed by `gh pr reopen` did bring the PR back with its review comments — so recreate the branch at the same sha and reopen before anything else.

**How to apply:** merge in one call guarded by the cadence check's exit code, green CI and `ao pr held`; then a separate call that checks `MERGED` before `ao progress done`, the notes and the delete — the delete named one branch at a time, never a loop over a list, and never in a call that also merges. A branch made with `git checkout -b x origin/main` tracks `origin/main`: push it with `git push -u origin x`, or the ledger's follow-up commit stays local and the check fails on *pushed*. See [[gate-a-merge-on-the-checks-exit-code]], [[no-checks-means-conflicting]], [[open-the-pr-before-writing-its-number]].
