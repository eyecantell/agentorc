---
name: designer-run-lessons-2026-10-06
description: a steer needs --default; a build number taken while the PR is open means squash first, then rebase and renumber in the one resolution
metadata:
  type: feedback
---

Two lessons from the designer run of 2026-10-06 (TD-356, PR #1183).

- `ao msg --kind steer` is refused without `--default "<the line you will go with>"` (design §4.10); the refusal is a JSON `error`, so a pipeline that reads `.entry.id` fails. Put `--default` in the first send.
- A build number named in the ledger can be taken on main while the PR is open (TD-358 was, by #1180). Renumbering on a multi-commit branch makes every replayed commit conflict in turn: squash the branch to one commit (`git reset --soft $(git merge-base HEAD origin/main)`), rebase once, and resolve the ledger's two insertion hunks row-wise in that one step, renumbering yours there. The steer already sent names the old number: say so in the PR body, never edit the steer.

**Why:** a refused steer silently leaves Paul untold, and a three-way replay of a renumber cost three conflict rounds.
**How to apply:** `--default` on every steer; re-read the highest TD on `origin/main` at the merge, and squash before any rebase that renumbers. See [[designer-run-lessons-2026-10-05]], [[dates-and-pr-numbers-are-local-and-real]], [[summary-table-conflicts-resolve-row-wise]].
