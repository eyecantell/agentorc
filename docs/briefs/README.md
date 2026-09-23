# Briefs for unattended agentorc sessions

> **Standing a team up is not here — run `ao team --skill`** (TD-067). That recipe covers the
> project and team definition, the roles, and `ao team list / start / status / stop`, which is how
> a set of sessions is started now. **This file is the one case it does not cover: a *single*
> worker launched by hand with `ao new` — and the membership pitfall that comes with it**, which
> is real for that path and only that path, because `ao team start` attaches every member itself.

A brief is the opening prompt of an unattended worker. Launch one from the main checkout (the
session gets its own worktree and branch, per design §4.5a, New session "Where: new worktree"):

```
pdm run ao new -d ~/agentorc -w grinder-ao-1 --unattended --prompt "$(cat docs/briefs/grinder-ao-1.md)" grinder-ao-1
```

A brief names no run, no date and no stop time (TD-042): the same file starts the session every
time, and `ao team start` warns when one does. What used to be written into each brief is the host
agent's now — the usage gate pauses and resumes unattended sessions against the reserves in
`settings.yml` (design §6, `ao gate`), and a hand-launched worker gets a stop time with
`ao new --until 06:00|+8h` (TD-026). samscrape runs its own team on its own briefs, in that repo.

`manager-ao-1.md` is the team's manager (design §4.8): a session holding the
`control` grant that keeps the unattended workers going and runs the cadence check
(`scripts/check_cadence.py`, cadence §4) on what they call done. **By hand** — which is what this
file is about — launch it **after** the workers and attach it to them in the same step; see
Membership below, which is the part that is easy to forget and looks like a broken lead when it
is. (`ao team start` does both for you, in the right order, and is the ordinary way in.)

```
pdm run ao new -d ~/agentorc -w manager-ao-1 --unattended -p grind --grant control --prompt "$(cat docs/briefs/manager-ao-1.md)" manager-ao-1
```

**Membership (design §4.8, TD-036).** The grant lets a lead act on other sessions; it
does not say which. Each worker's record carries the `controllers` that may act on *it*, and an
empty list means nobody may. A person's `ao new` sets no controller — there is no caller — so
**every worker launched by hand needs attaching**, in the same step as the grant:

```
ao control manager-ao-1 add grinder-ao-1 grinder-ao-2
ao status -v            # each worker's `under:`, and the lead's `members:`
```

Skip it and the lead holds the grant with no members, which looks exactly like a broken
lead: every send it makes is refused, and its own log is the only place that says why.
The one case that needs nothing is a worker the **lead itself** started — a session lists
its creator from birth (§4.8), which is what the brief's restart rule relies on.

Two briefs are written but **not launchable yet**, so that their rules are decided before the day
they are needed: `guardians-orchestrator.md` (blocked — the repos are not on this host, and the
devcontainer question in design §10 is open) and `director.md` (needs two leads before
it is worth running). Both carry the restart ceiling and `one_for_one` scope from TD-036.

`techlead-context.md` is the techlead's **primer** (design §4.9b): its first read on every fill —
the team definition's `techlead: {…, context: docs/briefs/techlead-context.md}` names it and the
preset brief reads it as `{context}` — the project in a few thousand words, an index and never a
source. The PR that changes the architecture,
a standing decision or the merge rules updates it; `tests/test_primer.py` holds its pointers to ones
that exist.

`archive/` holds the briefs of one-off step workers that are finished: the `td052-step*.md` set,
whose steps merged, and `td036-migration.md`, a migration never run because `ao team start`
(TD-040) superseded it. Kept for the record, never started again. A new one-off brief moves there
when its step merges.
