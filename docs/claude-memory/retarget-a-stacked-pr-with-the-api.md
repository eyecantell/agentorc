---
name: retarget-a-stacked-pr-with-the-api
description: gh pr edit --base fails with a projectCards GraphQL error; retarget a stacked PR with gh api PATCH pulls/N -f base=main, and never delete the base branch first
metadata:
  type: feedback
---

`gh pr edit <n> --base main` fails on this repo with `GraphQL: Projects (classic) is being deprecated … (repository.pullRequest.projectCards)` and leaves the base unchanged (2026-09-22, PR #453). `gh api -X PATCH repos/eyecantell/agentorc/pulls/<n> -f base=main` does it. Deleting the old base branch before the retarget auto-closes the stacked PR, and a closed PR's base cannot be changed.

**How to apply:** merge the base PR without `--delete-branch`, PATCH the stacked PR's base through the API, then rebase its own commits with `git rebase --onto origin/main <old base tip> <branch>` (the squash-merged base's commits will not replay cleanly), and delete the base branch last. Related: [[no-checks-means-conflicting]].
