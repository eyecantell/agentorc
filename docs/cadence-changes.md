<!-- SYNCED FILE — canonical copy: eyecantell/dev-cadence files/docs/cadence-changes.md
     Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one). -->

# Cadence changes — what a session must now do differently

One entry per convention change, newest first, **append-only**: an amended rule gets a new
dated entry, an old entry is never edited (the SessionStart hook diffs headings, so an edit
would be invisible). Each entry is at most three lines — the hook prints it into a session's
context once per worktree — and says what to *do*, then where the rule lives.

Read by `scripts/cadence_changes.py --hook` (SessionStart) and by orchestrators relaying to
sessions already running (agentorc design §4.8). Written by the dev-cadence PR that changes
the convention (cadence.md §7).

## 2026-09-11 — briefs, CLAUDE.md and skills cite a convention, never restate it
Do: write "merge per `/cadence` (docs/cadence.md §4)", not the loop in prose — a restated
rule is a copy that goes stale the day the rule changes.
See: cadence.md §7.

## 2026-09-11 — the independent review leaves evidence on the PR
Do: after the Sonnet review and before merging, `gh pr comment <n>` whose first line is
`cadence-review: SHIP|FIXED|BLOCK · <model> · <code|docs> · <n> findings`; never edit it.
See: cadence.md §4; `/cadence` runs the check (`scripts/check_cadence.py`).
