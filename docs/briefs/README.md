# Briefs for unattended agentorc sessions

A brief is the opening prompt of an unattended worker. Launch one from the main checkout (the
session gets its own worktree and branch, per design §4.5a, New session "Where: new worktree"):

```
pdm run ao new -d ~/agentorc -w tdgrind-ao-1 --unattended --prompt "$(cat docs/briefs/tdgrind-ao-1.md)" tdgrind-ao-1
```

There is no supervisor for these yet (design §6 usage gate and the run-window policy are
phase 3), so each brief carries its own stop time and usage rule. Edit the date and stop time
in the brief before relaunching. The samscrape workers still run under samscrape's
`scripts/tdgrind.sh` supervisor; moving them into agentorc is the phase 3 work.

`orchestrator-ao-1.md` is the first orchestrator (design §4.8): a session holding the
`orchestrate` grant that keeps the unattended workers going and runs the cadence check
(`scripts/check_cadence.py`, cadence §4) on what they call done. Launch it **after** the workers,
then attach it to them in the same step — see Membership below, which is the part that is easy to
forget and looks like a broken orchestrator when it is:

```
pdm run ao new -d ~/agentorc -w orchestrator-ao-1 --unattended -p grind --grant orchestrate --prompt "$(cat docs/briefs/orchestrator-ao-1.md)" orchestrator-ao-1
```

**Membership (design §4.8, TD-036).** The grant lets an orchestrator act on other sessions; it
does not say which. Each worker's record carries the `controllers` that may act on *it*, and an
empty list means nobody may. A person's `ao new` sets no controller — there is no caller — so
**every worker launched by hand needs attaching**, in the same step as the grant:

```
ao control orchestrator-ao-1 add tdgrind-ao-1 tdgrind-1 tdgrind-2 tdgrind-3
ao status -v            # each worker's `under:`, and the orchestrator's `members:`
```

Skip it and the orchestrator holds the grant with no reach, which looks exactly like a broken
orchestrator: every nudge it makes is refused, and its own log is the only place that says why.
The one case that needs nothing is a worker the **orchestrator itself** started — a session lists
its creator from birth (§4.8), which is what the brief's restart rule relies on.

The samscrape workers now launch from agentorc too (`ao new -d ~/samscrape …` from their
`~/.tdgrind/tdgrind-N-prompt.md` briefs, since 2026-09-10); `scripts/tdgrind.sh` is paused.

Two briefs are written but **not launchable yet**, so that their rules are decided before the day
they are needed: `guardians-orchestrator.md` (blocked — the repos are not on this host, and the
devcontainer question in design §10 is open) and `orc-of-orcs.md` (needs two orchestrators before
it is worth running). Both carry the restart ceiling and `one_for_one` scope from TD-036.
