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

There are no policies for these yet (design §6 usage gate and the run-window policy are
phase 3), so each brief carries its own stop time and usage rule. Edit the date and stop time
in the brief before relaunching. The samscrape workers still run under samscrape's
`scripts/tdgrind.sh` supervisor; moving them into agentorc is the phase 3 work.

`manager-ao-1.md` is the first lead (design §4.8): a session holding the
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
ao control manager-ao-1 add grinder-ao-1 tdgrind-1 tdgrind-2 tdgrind-3
ao status -v            # each worker's `under:`, and the lead's `members:`
```

Skip it and the lead holds the grant with no members, which looks exactly like a broken
lead: every send it makes is refused, and its own log is the only place that says why.
The one case that needs nothing is a worker the **lead itself** started — a session lists
its creator from birth (§4.8), which is what the brief's restart rule relies on.

The samscrape workers now launch from agentorc too (`ao new -d ~/samscrape …` from their
`~/.tdgrind/tdgrind-N-prompt.md` briefs, since 2026-09-10); `scripts/tdgrind.sh` is paused.

Two briefs are written but **not launchable yet**, so that their rules are decided before the day
they are needed: `guardians-orchestrator.md` (blocked — the repos are not on this host, and the
devcontainer question in design §10 is open) and `director.md` (needs two leads before
it is worth running). Both carry the restart ceiling and `one_for_one` scope from TD-036.

`techlead-context.md` is the techlead's **primer** (design §4.9b): meant as its first read on every fill, once a techlead
brief names it (none does yet — TD-075 step 5) — the
project in a few thousand words, an index and never a source. The PR that changes the architecture,
a standing decision or the merge rules updates it; `tests/test_primer.py` holds its pointers to ones
that exist.
