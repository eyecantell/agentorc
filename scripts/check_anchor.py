#!/usr/bin/env python3
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/check_anchor.py
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
"""SessionStart anchor guard — detect a second session in the main checkout.

Cadence rule (docs/cadence.md §1): ONE anchor session per checkout; every other
concurrent session isolates in a worktree. This hook makes the rule mechanical:
at session start it reads the live-session registry (~/.claude/sessions/*.json,
one file per running Claude Code process) and, if another live session already
occupies this checkout, prints a loud warning — which SessionStart hooks inject
into the new session's context — telling it to isolate before touching
anything. One session per checkout applies to worktrees too: starting in an
already-occupied worktree warns the same way. Silent when this session is the
first (anchor) in its checkout. Always exits 0 (non-blocking).

Self-exclusion is by session id AND by process ancestry: after /resume the new
session id differs from the one in the registry entry for this very process
(the registry updates only after SessionStart hooks run), so id equality alone
made a resumed session flag ITSELF as a conflicting occupant. Any registry
entry whose pid is this hook's own ancestor is self, whatever id it carries.

Liveness is CORROBORATED, not assumed from `/proc/<pid>` existing: the registry
is a directory of pid-named files, and a pid is only meaningful within the pid
namespace that issued it. Where `~/.claude` is shared across namespaces — the
durable-storage bind cadence.md §1 + appendix recommend for containers, which necessarily
shares `sessions/` too — a dead entry's pid is read against a *different*
namespace's `/proc`, where low pids are readily in use by unrelated processes.
Existence alone then reports a long-dead session as live and manufactures anchor
conflicts out of nothing (the same failure the cadence.md appendix warns about
for restored mirrors, made permanent by the bind). An entry survives only when nothing *positively*
establishes that its pid is no longer its session. The proof is exact wherever
the registry allows it: entries record `procStart` in the same clock ticks as
/proc/<pid>/stat field 22, so equality identifies the same PROCESS, needing
neither a name allowlist nor any wall-clock conversion. Entries predating that
field fall back to start-time ordering (a session writes its own entry, so its
process always predates it), and last to the process command. Anything
undeterminable keeps the entry, so the guard degrades toward a redundant
warning, never toward silence.

Usage:
    check_anchor.py            # standalone: prints anchor status for cwd
    check_anchor.py --hook     # SessionStart hook: reads {session_id, cwd} JSON
                               # from stdin; silent unless there's a conflict
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# A registry pid must belong to a process that plausibly IS its Claude Code
# session. Claude Code's own comm varies by install (native binary vs. a node
# launcher), so this is an allowlist of plausible names, not an identity test —
# its job is only to reject the obvious impostors a recycled pid produces (bash,
# python, vite, sleep). Widen it rather than narrowing it: a name missing here
# resurrects the false-conflict bug this filter exists to kill.
SESSION_COMMS = {"claude", "node"}

# Clock skew allowance between the entry's startedAt (wall clock, written by the
# session process) and the process start derived from /proc. Both come from the
# same kernel, so the real spread is sub-second; this is slack, not a threshold.
START_SKEW_SEC = 120


def _proc_start_ticks(pid: int) -> int | None:
    """Field 22 of /proc/<pid>/stat — process start in clock ticks since boot.

    The comm field is parenthesized and may itself contain spaces or parens, so
    split after the LAST ')'. Raw ticks, deliberately unconverted: this is the
    unit the registry records, and comparing raw values avoids every rounding
    and clock-correction question that converting to wall-clock introduces.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
        return int(stat[stat.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _proc_start_epoch(pid: int) -> float | None:
    """Wall-clock start time of PID, or None if it can't be determined.

    btime in /proc/stat is the boot epoch; both it and the tick count are
    host-kernel values readable from inside a container, which is precisely the
    case this exists to serve.
    """
    ticks = _proc_start_ticks(pid)
    if ticks is None:
        return None
    try:
        hz = os.sysconf("SC_CLK_TCK")
        btime = None
        for line in Path("/proc/stat").read_text(encoding="ascii", errors="replace").splitlines():
            if line.startswith("btime "):
                btime = int(line.split()[1])
                break
        if btime is None or not hz:
            return None
        return btime + ticks / hz
    except (OSError, ValueError, AttributeError):
        return None


def _entry_proc_start(entry: dict) -> int | None:
    """The entry's recorded procStart in clock ticks (Claude Code writes it as a string)."""
    try:
        return int(entry["procStart"])
    except (KeyError, TypeError, ValueError):
        return None


def _pid_is_dead(entry: dict) -> bool:
    """True only when the entry's pid is POSITIVELY not this session's process.

    Undeterminable cases return False (keep the entry) — a guard that stays
    quiet is worse than one that occasionally warns twice.
    """
    pid = _entry_pid(entry)
    if pid is None:
        return False  # no pid recorded — nothing to disprove
    if not Path(f"/proc/{pid}").exists():
        return True  # stale registry entry (same-namespace case)

    # PRIMARY, and exact: the registry records procStart in the same units and
    # epoch as /proc/<pid>/stat field 22, so equality identifies the SAME
    # PROCESS rather than merely a plausible one. Both values come from the one
    # kernel, so this is namespace-independent, and it needs neither the comm
    # allowlist nor btime — no wall-clock conversion means no exposure to a
    # post-boot clock correction shifting derived start times. Present on every
    # entry from Claude Code versions in circulation; the heuristics below exist
    # only for entries that predate the field.
    recorded = _entry_proc_start(entry)
    actual = _proc_start_ticks(pid)
    if recorded is not None and actual is not None:
        return recorded != actual

    # FALLBACK, in order of strength. Start time is checked first and decides on
    # its own: the session writes its own registry entry, so its process always
    # predates the entry, and a process that started after it took the pid later.
    started = entry.get("startedAt")
    proc_start = _proc_start_epoch(pid)
    if isinstance(started, (int, float)) and proc_start is not None:
        return proc_start > started / 1000 + START_SKEW_SEC

    # Weakest, and only when nothing better is available: an unrecognized comm.
    # Deliberately last — comm is an allowlist, and /proc/<pid>/comm truncates at
    # 15 chars, so a future Claude Code launched under a different name would be
    # judged dead here and the guard would stop reporting real conflicts. That
    # silent-permissive direction is why this never runs while procStart or a
    # usable start time is available.
    try:
        comm = Path(f"/proc/{pid}/comm").read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        comm = ""
    return bool(comm) and comm not in SESSION_COMMS


def live_sessions() -> list[dict]:
    reg = Path.home() / ".claude" / "sessions"
    if not reg.is_dir():
        return []
    out = []
    for f in reg.glob("*.json"):
        try:
            obj = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict) or "sessionId" not in obj:
            continue
        if _pid_is_dead(obj):
            continue
        out.append(obj)
    return out


def _ancestor_pids() -> set[int]:
    """Pids identifying THIS session: us up to our own claude process, via /proc.

    The hook runs as a descendant of the claude process, so the session that
    spawned it is always an ancestor. The walk stops at (and includes) the
    first ancestor whose comm is "claude" — that one is our session; anything
    ABOVE it is foreign, possibly a different claude session that spawned the
    shell this one was started from, and excluding it would silently suppress
    a genuine conflict. If no claude ancestor is identifiable (no /proc, or a
    wrapper changes comm), the walk runs to the top — over-inclusive fallback,
    trading the rare nested-session false negative for never regressing the
    /resume fix. On systems without /proc entirely the walk stops at the first
    miss and returns whatever it collected (worst case just our own pid — the
    pre-fix behavior, never worse).
    """
    pids: set[int] = set()
    pid = os.getpid()
    while pid > 1 and pid not in pids and len(pids) < 64:
        pids.add(pid)
        try:
            comm = Path(f"/proc/{pid}/comm").read_text(encoding="ascii", errors="replace").strip()
        except OSError:
            comm = ""
        if comm == "claude" and pid != os.getpid():
            break  # our own session process — its ancestors are not us
        try:
            status = Path(f"/proc/{pid}/status").read_text(encoding="ascii", errors="replace")
        except OSError:
            break
        m = re.search(r"^PPid:\s*(\d+)", status, re.MULTILINE)
        if not m:
            break
        pid = int(m.group(1))
    return pids


def _entry_pid(s: dict) -> int | None:
    try:
        return int(s.get("pid"))
    except (TypeError, ValueError):
        return None


# Occupancy resolution runs per registry entry, so its git calls need tighter
# bounds than the single call main() makes for its own cwd. A hung `git` — stale
# NFS, a dead bind mount, exactly the container hazards the cadence.md appendix
# is about — would otherwise stall SessionStart for the full timeout PER ENTRY,
# serially.
OCCUPANCY_GIT_TIMEOUT = 2     # seconds per call
OCCUPANCY_GIT_BUDGET = 4.0    # seconds across all occupancy calls in one run
_repo_root_cache: dict[str, "Path | None"] = {}
_occupancy_git_spent = 0.0


def repo_root(cwd: Path, timeout: int = 10) -> Path | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True, timeout=timeout,
        )
        return Path(r.stdout.strip()).resolve()
    except (subprocess.SubprocessError, OSError):
        return None


def _occupancy_repo_root(p: Path) -> "Path | None":
    """repo_root() for occupancy checks: memoized, time-bounded, fail-safe.

    Stale registries commonly hold many entries sharing one cwd, so the cache
    turns a per-entry spawn into a per-distinct-path one. Past the budget the
    remaining entries resolve as undeterminable, which occupies() keeps — the
    same degrade-toward-warning direction as every other unknown here.
    """
    global _occupancy_git_spent
    key = str(p)
    if key in _repo_root_cache:
        return _repo_root_cache[key]
    if _occupancy_git_spent >= OCCUPANCY_GIT_BUDGET:
        return None
    t0 = time.monotonic()
    result = repo_root(p, timeout=OCCUPANCY_GIT_TIMEOUT)
    _occupancy_git_spent += time.monotonic() - t0
    _repo_root_cache[key] = result
    return result


def occupies(session_cwd: str, root: Path) -> bool:
    """True if a session started at session_cwd is working in ROOT's main checkout."""
    try:
        p = Path(session_cwd).resolve()
    except OSError:
        return False
    if p == root:
        return True
    if root not in p.parents:
        return False
    # sessions inside <root>/.claude/worktrees/<name> are isolated, not occupants
    rel = p.relative_to(root).parts
    if len(rel) >= 2 and rel[0] == ".claude" and rel[1] == "worktrees":
        return False
    # Being UNDER root's directory is not the same as being IN root's checkout.
    # An independent repo nested in the working dir — a sibling checkout kept
    # under an umbrella repo, a submodule, a vendored clone, a gitignored
    # subproject — shares no HEAD, index, or working files with root, so a
    # session there collides with nothing here. Path nesting alone reported it as
    # an occupant and made the umbrella session warn about a checkout it does not
    # share. Only nested paths pay this git call; the p == root fast path above
    # covers the common case.
    nested_root = _occupancy_repo_root(p)
    if nested_root is None:
        # UNDETERMINABLE, not disproved — git failed, or the cwd has since been
        # deleted. Keep it an occupant: this check exists to REMOVE false
        # conflicts, and letting it invent false *clearances* would trade a
        # visible annoyance for a silent one, against the same degrade-toward-
        # warning rule the liveness filter follows.
        return True
    return nested_root == root


def main() -> int:
    hook = "--hook" in sys.argv
    data = {}
    if hook:
        try:
            data = json.load(sys.stdin)
        except (json.JSONDecodeError, OSError):
            data = {}
    self_id = data.get("session_id", "")
    cwd = Path(data.get("cwd") or os.getcwd()).resolve()

    root = repo_root(cwd)
    if root is None:
        return 0  # not a git repo — nothing to guard
    if root != cwd and not occupies(str(cwd), root):
        if not hook:
            print(f"OK: this session is isolated in a worktree ({cwd}).")
        return 0

    self_pids = _ancestor_pids()
    others = [
        s for s in live_sessions()
        if s.get("sessionId") != self_id
        and _entry_pid(s) not in self_pids  # self after /resume: old id, our own pid
        and occupies(s.get("cwd", ""), root)
    ]
    if not others:
        if not hook:
            print(f"OK: no other live session in {root} — this session is the anchor.")
        return 0

    lines = [f"⚠ ANCHOR CONFLICT: {root} already hosts {len(others)} other live Claude Code session(s):"]
    for s in others:
        started = s.get("startedAt")
        when = (
            datetime.fromtimestamp(started / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            if isinstance(started, (int, float)) else "?"
        )
        lines.append(
            f"  • session {s.get('sessionId', '?')[:8]} (pid {s.get('pid', '?')}, started {when}) "
            f"in {s.get('cwd', '?')}"
        )
    lines.append(
        "Per docs/cadence.md §1 there is ONE anchor session per checkout — the earlier session holds that role. "
        "Before making ANY changes here (no commits, branch switches, or file edits in this shared checkout), "
        "this session must isolate itself: move to a fresh worktree now (EnterWorktree), or tell the user to restart it "
        "with `claude --worktree <name>`. Shared-checkout collisions have caused real damage before."
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
