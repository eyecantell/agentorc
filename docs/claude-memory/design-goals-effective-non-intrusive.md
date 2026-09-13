---
name: design-goals-effective-non-intrusive
description: "Paul judges harness and cadence designs by two goals, effective and non-intrusive, and asks whether cross-repo work should be done by that repo's own agents"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: b4f95a9e-2c82-4f50-b80a-1a073bd51c7d
  modified: 2026-09-12T13:23:23.785Z
---

Paul reviews agentorc / dev-cadence designs against "effective and non-intrusive" and asks
(2026-09-12) whether changes to other repos should be done by that repo's agents rather than
directly from an agentorc session, because those agents know the code and the repo may be
worked by a non-Claude tool.

**Why:** per-repo agents have the repo's context; direct cross-repo edits from one session
bypass that and assume Claude Code everywhere.

**How to apply:** mechanical synced-file changes may go direct (sync PR); anything needing the
repo's judgement is left to the repo's next session via a `docs/cadence-changes.md` entry and
the cadence check, never edited from outside. Keep tool-specific wiring in agentorc adapters
and tool-neutral rules/scripts in the repo. See [[unattended-workers-run-inside-agentorc]].
