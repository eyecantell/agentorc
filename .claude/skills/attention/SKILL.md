---
name: attention
description: Machine-wide "what needs my attention?" — every registered repo's user-attention board in one due-date-ordered report. Pass "fetch" to also compare each board against its origin (pushed cross-machine state). Pass "remote" for the everything view: fetch plus GitHub discovery of boards in repos not present on this machine.
---

<!-- SYNCED FILE — canonical copy: eyecantell/dev-cadence files/.claude/skills/attention/SKILL.md
     Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one). -->

# Attention — machine-wide board report

Answer "what needs the user's attention across all repos on this machine?" in one shot.
Thin wrapper around the report mode of the synced nudge script — never reimplement its
parsing, ordering, or roster reading (spec: cadence.md §Machine scope).

## Run

```bash
python3 scripts/nudge_user_attention.py --report
```

Run the current repo's synced copy (any consumer's copy is the same file — that's what
SYNC means). With `/attention fetch`, add `--fetch`: serial, hang-proofed origin fetches
that compare each local board with its `origin/<default>` copy three ways (local, origin,
their merge-base — TD-030). A clone that is merely **behind** (local board untouched,
origin's moved — a service-account append, or another machine's push) has its board read
from origin and its header tagged `— showing origin/<default>`; local-only edits and
two-sided divergence keep reading the local file and say which. Plus a per-repo
cadence-staleness line when a repo's synced copy lags dev-cadence upstream — so a behind
repo names itself in the fetch view.

With `/attention remote`, add `--remote` instead (it implies `--fetch`): after the local
sections, a labeled remote tier lists boards discovered on GitHub (gh CLI) in repos no
local row covers — repos living only on other machines. Pushed state only; archived
repos are skipped; discovery scans the authenticated user + orgs unless
`${XDG_CONFIG_HOME:-~/.config}/dev-cadence/remote_repos.txt` narrows it (one `owner` or
`owner/repo` per line; empty/missing = scan all). Every failure mode (gh missing,
unauthenticated, per-repo 403/5xx, truncated listing, budget exhaustion) degrades to a
⚠ note in the tier — surface those notes; absence of a note is the coverage claim.

## Present

Lead with the report's own header line (N boards, M open items, K due/overdue) as the
TL;DR, then the per-repo sections. The report is already ordered (overdue-most-first, then
dated soonest-first, then undated) — do not re-sort or re-derive tags. Surface any ⚠ lines
(unreadable/missing boards, fetch skips) rather than smoothing
them over.

## No registry on this machine

The report degrades to the current repo's board by itself; say that's what happened.
Offer — never do unasked — to create `${XDG_CONFIG_HOME:-~/.config}/dev-cadence/repos.txt`.
The registry is append-only and nothing auto-cleans it, so the seed algorithm is pinned
and conservative:

- Seed with exactly ONE line: the **current repo's** canonicalized main-checkout root
  (`git rev-parse --git-common-dir` → parent dir → `realpath` — same resolution as
  sync.sh; spec: cadence.md §Machine scope).
- You may *mention* other candidate repos you happen to know of, but each one is added
  only on the user's explicit per-path confirmation AND after verifying on disk that the
  path exists, is a git repo, and resolves to a main checkout — never from session
  memory alone.
- The normal registration path is running dev-cadence's sync.sh against a repo on this
  machine; hand-seeding is the exception for repos adopted by hand.

## Cross-machine questions

Offer `--report --fetch`, with the machine-scope caveat (same as /stranded-work): a
behind clone's row already shows origin's board, but **un-pushed board edits on another
machine are invisible** to every view. A "board DIFFERS from origin" line is the cue to
pull (or push) before trusting that row; "showing origin/<default>" means the row is
ahead of the local file, and a pull will make the file match what was reported.

## Bounds

Read-only by default — the report path makes no writes. The only writes this skill may
ever make are registry creation/append under the explicit opt-in above. Checking items
off, snoozing (editing `Due:` dates), and any other board edits are user-directed
follow-ups, not part of the report.
