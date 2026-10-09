# ADR 2026-10-08: dev-cadence is what a repo needs with no agentorc on the machine; agentorc is what needs a host agent, a record or a second session

**Status:** decided 2026-10-08 by the anchor (owed to it by TD-159, Paul's question of 2026-09-25). Paul may overrule it with a superseding ADR.
**Date:** 2026-10-08
**Related:** TD-159, TD-425, TD-035, TD-055, TD-070, TD-118, TD-125, TD-126, TD-142; [ADR 2026-09-06](2026-09-06-adopt-dev-cadence.md); design §4.3, §8

## Context

Paul, 2026-09-25: *are there things that live here that should actually live in dev-cadence or
vice-versa?*

The two repos were split by origin, not by rule. dev-cadence holds what tdgrind and the per-repo
conventions had before agentorc existed. agentorc took the session substrate and the org. When
TD-159 was filed, six open entries each said "that part is dev-cadence's" and stopped. Nothing
said what makes a thing belong on one side or the other.

On 2026-10-08 a Sonnet reader took an inventory of both repos' `origin/main`, read only. The
anchor checked its load-bearing claims against the files.

**agentorc's synced copies.**
- There are 28 files carrying the SYNCED FILE header: 17 scripts, 3 git hooks, 5 docs
  (`cadence.md`, `cadence-changes.md`, `cadence-incidents.md`, `cadence-rationale.md`,
  `adr-template.md`) and 3 skills.
- `.claude/settings.json` wires one SessionStart line, the runner `scripts/cadence_hooks.sh`.
- Every one of the 28 is still needed with agentorc uninstalled. They are the board, the ledger,
  the merge gate, the git hooks and the session-start nudge.
- agentorc reads some of them as a client: `nudge_user_attention.py --report --json` (the
  Inbox's board rows, `ui/inbox.py`), `check_cadence.py --json` (§6 rule 10),
  `cadence_changes.py --json` (rule 12) and `hydrate_worktree.sh` (`gitinfo.py`).

**agentorc's two second readers.**
- `src/sessionorc/ledger.py` re-implements `scripts/ledger.py`. `tests/test_ledger_derived.py`
  holds the two equal.
- `src/sessionorc/board.py` re-implements `board_edit.py`'s edit.
- The scripts stay the definition. The in-process readers exist because the tick cannot spawn a
  script per call.

**dev-cadence's mentions of agentorc.**
- Most are records or documentation of a parity pair: `cadence-changes.md`,
  `cadence-rationale.md`, and the parity table in `cadence.md` §7 naming `CADENCE_HOOK_LINE` and
  the fields agentorc reads.
- Some are dev-cadence's own team supplements: `docs/briefs/`, `.agentorc.yml` and dc-grind's
  memory. These are a repo's configuration of agentorc, which every consumer repo carries.
- One is behaviour that only agentorc needs. `cadence_hooks.sh`'s `attention_scope` asks
  `ao status --json` whether `AGENTORC_SESSION` is an unattended record, and if so scopes the
  session-start nudge to the session's own board (dev-cadence's TD-077, agentorc's TD-118).
- The script already reads `CADENCE_ATTENTION_SCOPE=own|machine` first, so it has a neutral
  seam. agentorc never sets it.

**The ledger's "dev-cadence's" clauses.** Of the seven entries TD-159 named, five are archived:
TD-070, TD-118, TD-125, TD-126 and TD-142. Each was closed with dev-cadence's matching TD:
TD-036, TD-077, TD-075, and none needed for TD-125. Two remain open:
- TD-035's clause (2), an argument form for the runner's repo root, is settled in design §4.3.
  The runner reads `CLAUDE_PROJECT_DIR`, else `git rev-parse --show-toplevel`, else `pwd`, and
  "an argument form would be dev-cadence's, if a tool ever needs one". No move is owed.
- TD-055 names no dev-cadence change.

## Options

1. **Keep the split as it is and write no rule.** Each new thing is placed by whoever builds it.
   That is how the six dangling clauses arose.
2. **The candidate rule, applied.** Something belongs to dev-cadence when a repo needs it with no
   agentorc on the machine. It belongs to agentorc when it needs a host agent, a session record,
   or a second session.
   - A file needed both ways stays dev-cadence's. agentorc reads it as a client, through a
     parity pair named in cadence §7.
   - A behaviour inside a dev-cadence file that only an agentorc session needs is given a
     neutral seam: an environment variable or an argument. agentorc sets the seam, so the file
     never calls `ao`.
3. **Move the shared formats into agentorc.** The board and ledger formats would become
   agentorc's, with dev-cadence a thin copy. This breaks every repo that runs the cadence
   without agentorc, which is the point of dev-cadence. Rejected.

## Decision

Option 2. Tested against it, the split is already close. There is one misfit:

- **`attention_scope`'s `ao status` branch.** It moves to agentorc's side of the seam. agentorc's
  launch sets `CADENCE_ATTENTION_SCOPE=own` for an unattended session and `machine` for an
  interactive one (TD-425, a build here). Once that build is live on every machine running
  dev-cadence's hook, a dev-cadence session can drop the branch. That is dev-cadence's change,
  which cadence §3 makes its own session's: until then the branch is harmless, because the
  variable is read first.

What stays, and why:
- **The two second readers** (`sessionorc/ledger.py`, `sessionorc/board.py`) stay as parity
  pairs. The script is the definition and a test holds them equal.
- **dev-cadence's supplements and records** stay where they are. A repo's own `.agentorc.yml`
  and briefs are that repo's configuration, and the dated records are history.
- **TD-035 and TD-055** owe dev-cadence nothing.

The rule goes into design §8 as one paragraph, so that the next new thing lands on the right side
without a review.

## Consequences

- A dev-cadence file never calls `ao`. Where a synced script must behave differently in an
  agentorc session, agentorc passes the difference in, and the script reads it.
- A new field that only agentorc renders still goes in dev-cadence's format, if the person reads
  it on the board without agentorc. Otherwise it goes in agentorc's records.
- A second reader of a dev-cadence format inside agentorc is allowed only with a parity test, and
  only with its row in cadence §7's table.

## Revisit triggers

- A second consumer of dev-cadence that is not agentorc needs the seam to mean something else.
- agentorc needs a synced script to behave differently in a way an environment variable cannot
  carry.
- A repo runs agentorc without dev-cadence.
