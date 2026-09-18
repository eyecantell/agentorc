---
name: scratchpad-too-long-for-unix-sockets
description: The session scratchpad path exceeds the AF_UNIX 108-byte limit; a scratch AGENTORC_HOME (or any unix socket) needs a short path such as ~/.cache/<name>
metadata: 
  node_type: memory
  type: project
  originSessionId: d736f01f-1c5a-4673-b729-0c19c1bc45db
  modified: 2026-09-18T02:56:58.688Z
---

A scratch `AGENTORC_HOME` under the session scratchpad (`/tmp/claude-1000/-home-kmaster-agentorc--claude-worktrees-<name>/<uuid>/scratchpad/...`) fails with `OSError: AF_UNIX path too long` when the host agent binds `agent.sock` (seen 2026-09-17 while live-checking `ao host up` from a scratch home).

**Why:** unix socket paths are capped at 108 bytes; the scratchpad path alone is ~130.

**How to apply:** for a scratch home or any unix socket, use a short user-owned path such as `~/.cache/ao<topic>` and remove it afterwards; the scratchpad is fine for everything else. The test suite is unaffected (pytest's `tmp_path` is short). Related: [[unattended-workers-run-inside-agentorc]].
