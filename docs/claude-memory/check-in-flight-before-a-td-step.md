---
name: check-in-flight-before-a-td-step
description: "Before starting the next step of a multi-step TD, check open PRs and remote branches — another of Paul's sessions may already hold it"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 192b2355-2009-4e5e-ab33-69257956a53b
  modified: 2026-09-18T05:53:55.260Z
---

Before starting a step of a multi-step TD (TD-057 especially), run `gh pr list` and
`git branch -r | grep <td>` and read the TD's status line on `origin/main`: on 2026-09-17 another
of Paul's sessions (`agentorc-containers-98`, worktree `agentorc_containers`) built TD-057 steps
3c.1–3c.5 and 4a in three hours while the anchor was on TD-052 step 6, and 4a was the anchor's
planned next step.

**Why:** two sessions on one step cost a rebase at best and a duplicated design decision at worst;
Paul runs several sessions at once and does not always say which holds what.

**How to apply:** when a step is held elsewhere, do the conformance read of what lands instead of
building; coordinate over Claude Code's session messages for anchor-to-session talk, but use
`ao msg` for anything to an unattended lead — a peer message to a lead is held for approval and
stalls it ([[agentorc-td-grind-mechanics]], TD-064).
