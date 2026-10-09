---
name: never-kill-a-parent-pid-unseen
description: A cleanup loop that kills each `sleep 90`'s parent killed `systemd --user` (pid 1920) and took every session, the host agent and the UI down for 4h20m on 2026-10-09; kill only pids you started and recorded, never a parent found by walking up
metadata:
  type: feedback
---

On 2026-10-09 at 12:30 MDT a designer run (designer-ao-1) cleaned up its stray `sleep 90` helpers with
`for p in $(ps -eo pid,args | grep '[s]leep 90' | awk '{print $1}'); do pp=$(ps -o ppid= -p $p); kill $pp $p; done`.
One `sleep` was not the run's own: its parent was pid 1920, `/usr/lib/systemd/systemd --user`. The SIGTERM
ran `exit.target`, which SIGKILLed the tmux server, so every `ao-*` session, the host agent and the UI were
down until an ssh login at 16:50 started a new user manager (ledger TD-488, TD-489).

**Why:** a pattern over `ps` matches processes other sessions and the system own, and a parent found by
walking up from a match can be anything — a shell, a service, the user manager itself. Printing the parent's
name in the same command that kills it is a report of the damage, not a check.

**How to apply:** kill only a pid you started and recorded (`cmd & echo $! > file`), or one you looked at in
an earlier call and named by number in the next, as [[stop-a-scratch-home-by-its-pid]] says. Never
`kill $(ps … | awk)` or kill a `ppid`: a loop over a pattern is never a cleanup. A helper that must stop
itself gets a bound at birth (`timeout 600 …`), so nothing is left to hunt. See also
[[delete-only-your-own-branches-by-name]] — the same shape of incident on branches.
