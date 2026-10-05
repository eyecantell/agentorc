---
name: stop-a-scratch-home-by-its-pid
description: pkill -f / pgrep -f with look_home.py's args matches the Bash tool's own shell and kills it (exit 144); find the pid with ps | grep '[l]ook_home…' and kill that number
metadata:
  type: feedback
---

Stopping `scripts/look_home.py` (or any background helper) with `pkill -f '<its args>'` or
`kill $(pgrep -f '<its args>')` kills the Bash tool's own shell, since the pattern is on that shell's
command line too: exit 144, and the helper may survive. It happened twice in one run on 2026-10-03.

**Why:** `-f` matches full command lines, and the command that runs the kill contains the pattern.

**How to apply:** first `ps -eo pid,args | grep '[l]ook_home.py --port NNNN'` (the bracket keeps grep
from matching itself) in one call, then `kill -TERM <pid>` of the python child in the next. look_home
tears its home down on SIGTERM; check `/tmp/aolook-*` is gone. See [[headless-screenshots-on-kmaster]].

Always match on *your own* home or port, never on `look_home.py` alone: every session on kmaster runs
scratch homes, and on 2026-10-03 a `grep '[l]ook_home.py'` loop stopped another session's home too.
Start it with `--port NNNN` (or read the home from its log) and grep for that, then kill one pid.
