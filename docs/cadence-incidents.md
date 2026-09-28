<!-- SYNCED FILE — canonical copy: eyecantell/dev-cadence files/docs/cadence-incidents.md
     Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one). -->

# Working Cadence — incidents

The measured failures that produced the rules in [`cadence.md`](cadence.md), keyed by the same §N.M numbers (TD-049). Archive-style: an entry is added when a rule is born or sharpened by an incident, and never rewritten. The reasoning each one led to is in [`cadence-rationale.md`](cadence-rationale.md).

## 1. Sessions and worktrees

### §1.3 Every PR's base is main

Measured in a consumer repo: once one PR took the worktree's branch as its base, the next three did the same, and all four reported themselves merged while `main` had none of them — caught only because the user asked whether the branch was merging as it went.

### §1.7 A live session is not an attended one

Observed end to end: promoting a folder-opened VS Code window to a multi-root workspace forced a reload, the pre-reload `claude` survived on its old pty, and `claude --continue` then started a **second live process on the same session id**, both registered, both pointing at one transcript.

## 8. Subagent discipline

### §8.2 A subagent's summary is evidence, not a source

*(Reported by a consuming repo: a UI screen rebuilt from agent summaries came out as a two-column layout when the exported source was a single-column flow, and the export had explicitly marked which variant was canonical. The user spotted it on sight; the summaries had read as complete.)*
