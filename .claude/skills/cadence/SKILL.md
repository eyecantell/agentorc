---
name: cadence
description: Run the cadence check on this branch's PR (review evidence, squash, CI, ledger, worktree, pushed, deploy) — do the independent review and post its evidence comment if missing, then merge only when every row passes. Pass a PR number to check another PR, or "week" for every PR merged in the last 7 days.
---

<!-- SYNCED FILE — canonical copy: eyecantell/dev-cadence files/.claude/skills/cadence/SKILL.md
     Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one). -->

# Cadence — check a PR against cadence.md §1–§5 before merging

Thin wrapper around `scripts/check_cadence.py` — never reimplement its rows here. The script
is a **detector, not a gate** (cadence §7): nothing stops a `gh pr merge` that skipped it, and
that is deliberate — a gate that gets bypassed disables itself. The backstop is
`/stranded-work`, which runs the same script over the week's merges (`--since 7d`), so a
skipped check is found, not prevented.

## Steps

1. Find the PR: the current branch's (`gh pr view --json number`), or the number given as
   the argument. `week` means `--since 7d` (report only, steps 3–5 do not apply).
2. Run `python3 scripts/check_cadence.py --pr <n>` and read every row. Exit 0 = every row
   pass or n/a; 1 = a fail; 2 = nothing could be checked (no `gh`, no PR, or every row `unknown`). A row marked
   `unknown` is evidence gh could not give (a deleted head's check runs, a rate limit) — say
   so, never call it a pass.
3. **`review` fails and no review has been done:** do it now — a cheaper-model agent
   (Sonnet) reviews the diff for correctness (code) or fact-checks it against the repo
   (docs). Fix the findings, re-verify, and then post the evidence comment (§4):

   ```
   gh pr comment <n> --body "$(cat <<'MSG'
   cadence-review: FIXED · sonnet · code · 3 findings
   - <finding 1 and what changed>
   - ...
   MSG
   )"
   ```

   First line exactly in that shape — `SHIP` (nothing to change), `FIXED` (findings fixed
   before merge), or `BLOCK` (do not merge) — then `<reviewer model>`, `<code|docs>`,
   `<n> findings`, separated by ` · `. Findings in prose below. **Never edit the comment
   afterwards** (an edit after the merge fails the row: GitHub stamps `updated_at`). Post a
   new comment if there is more to say. The script's `REVIEW_RE` reads this line — the two
   are a parity pair (cadence §7): change the shape in both or in neither.
4. **Any other row fails:** fix the cause, not the row — a `ledger` fail means the TD named
   in the title needs its status line or archive move in this PR (§2); `pushed` means
   commits exist only on this machine (§3); `worktree` after a merge means scratch or
   unlanded commits are sitting in the worktree (§1).
5. Merge only on exit 0 (`gh pr merge <n> --squash` — no `--delete-branch` from a worktree,
   cadence §1), then re-run with `--pr <n>` once merged if you want the record: the `pr`,
   `review` (created and last edited before the merge) and `worktree` (gone or reapable)
   rows change meaning after the merge.

## What the rows prove, and what they don't

`review` is self-attested: the session that ran the review posted the comment. Green means
the ritual was recorded. An orchestrator that nudges or escalates on this check, and a sweep
that reports a clean week, should say "recorded", not "verified". `pr`'s squash is inferred
from the merge commit having one parent — GitHub records no merge method. `deploy` runs the
consumer's own `scripts/cadence_deploy_check.sh <pr> <sha>` if one exists (exit 0 pass,
1 fail, 2 n/a; cadence §5, *adapt per repo*) and is n/a otherwise.
