<!-- SYNCED FILE — canonical copy: eyecantell/dev-cadence files/docs/adr-template.md
     Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one). -->

# ADR <id>: <the decision, as one sentence>

<!-- Copy this file to <the repo's ADR directory>/<id>-<slug>.md and drop this comment and the
     SYNCED FILE header above. <id> is the repo's own scheme (NNN, or YYYY-MM-DD). The rules —
     when a decision earns an ADR, what each field means, why a decided ADR is never edited —
     are cadence.md §7 (Architecture decisions). -->

**Status:** proposed | decided YYYY-MM-DD by <who> | superseded by ADR <id> (its path)
**Date:** YYYY-MM-DD
**Related:** TD-NNN, PR #N, ADR <id>

## Context

What forced the decision: the problem, the constraint, what was measured. Enough that a reader
who was not there can tell whether it still holds.

## Options

Each option considered, one short paragraph, with what it costs — including "do nothing".

## Decision

What was chosen, and by whom. While Status is `proposed`, the question put to the decider and
the recommended answer.

## Consequences

What changes because of it, what gets harder, and what is now out of bounds.

## Revisit triggers

The observable events that would reopen it ("a second consumer needs X", "the table passes
1M rows"). Reopening means a new ADR that supersedes this one — never an edit to this one.
