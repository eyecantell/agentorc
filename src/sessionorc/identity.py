"""Who is calling: identity on one host (design §4.8a, TD-077).

A request's identity is the channel it arrived on, never a field it carries (§9 invariant 15).
Between hosts that is the link and its key; on one host it is **the pane the connecting process
belongs to**, read from the peer's credentials at accept. This module is the pure half: given a
peer pid, the panes this host knows and a reader over `/proc`, it says which pane — if any — the
peer belongs to, and given that and the envelope's claim it says what the request runs as. It
holds no socket, no record and no tmux; the host agent supplies those.

Inside one OS account this is tamper-evidence, not a wall (§4.8a *The threat model*): whatever can
reach the socket can also reach the tmux server. What it buys is that the accidental and the casual
are refused, and that a forgery is shown.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

MODES = ("off", "observe", "enforce")
# The release that introduces §4.8a observes: a wrong rule must not lock a host out of `ao`. The
# suite patches this to `off` (its fixtures call the socket from pytest, under no pane, as sessions).
DEFAULT_MODE = "observe"
WALK_LIMIT = 64  # hops of the `ppid` chain
# How long a record's pane is still matched, for a `hook` naming that record, after it left the
# tick's list (TD-115): a tool's last hooks can connect a few seconds after its pane is gone.
PANE_GONE_GRACE = 10.0
ALARMS_KEEP = 20

# Never-gated reads (§4.4a's first table row, less `wait`, which answers *for* a caller): served on
# every channel, no alarm — they tell a caller nothing the socket's mode did not already grant.
# **A read here must not decide anything on its `caller`**: the envelope's claim reaches it
# unjudged from outside a pane. `host_files` was listed until the red-team of PR #248 — it
# authorises on `caller` (the person, or a `control` holder), so a session that left its `caller`
# out read a checkout as the person. `tests/test_identity.py` pins the rule for every name here.
READS = frozenset(
    {
        "list",
        "get",
        "tail",
        "log_tail",
        "explain",
        "transcript",
        "occupancy",
        "name_check",
        "recent_dirs",
        "usage",
        "repos",
        "doing_log",
        "gate",
        "adapters",
        "ping",
        "whoami",
        "identity",
        "doctor",
    }
)


@dataclass(frozen=True)
class Proc:
    """The fields of `/proc/<pid>/stat` the classification reads."""

    pid: int
    ppid: int
    sid: int  # the POSIX session id
    tty_nr: int  # the controlling terminal, 0 for none
    start: int  # field 22: start time in clock ticks since boot — with `pid`, a process's name for life


class ProcReader(Protocol):
    def stat(self, pid: int) -> Proc | None: ...

    def cgroup(self, pid: int) -> str | None: ...

    def comm(self, pid: int) -> str | None: ...


class LinuxProc:
    """`/proc` as it is. A process that is gone, or one we may not read, is `None` — never an error."""

    def stat(self, pid: int) -> Proc | None:
        try:
            with open(f"/proc/{int(pid)}/stat", "rb") as f:
                raw = f.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            return None
        # `pid (comm) state ppid pgrp session tty_nr …`: comm may hold spaces and parentheses, so
        # the fields are counted from the *last* `)`.
        close = raw.rfind(")")
        rest = raw[close + 2 :].split()
        try:
            return Proc(pid=int(pid), ppid=int(rest[1]), sid=int(rest[3]), tty_nr=int(rest[4]), start=int(rest[19]))
        except (IndexError, ValueError):
            return None

    def comm(self, pid: int) -> str | None:
        try:
            with open(f"/proc/{int(pid)}/comm", encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except (OSError, ValueError):
            return None

    def cgroup(self, pid: int) -> str | None:
        try:
            with open(f"/proc/{int(pid)}/cgroup", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except (OSError, ValueError):
            return None
        return cgroup_path(lines)


def cgroup_path(lines: Iterable[str]) -> str | None:
    """The path systemd put the process in: cgroup v2's single `0::<path>` line, else v1's
    `name=systemd` line. None when neither is there."""
    v1 = None
    for line in lines:
        hier, _, rest = line.partition(":")
        ctrl, _, path = rest.partition(":")
        if hier == "0" and ctrl == "":
            return path
        if ctrl == "name=systemd":
            v1 = path
    return v1


def tty_nr_of(path: str) -> int:
    """`#{pane_tty}` (`/dev/pts/7`) as the kernel writes it in `stat`'s `tty_nr`: the minor's low
    byte, the major above it, the minor's high bits above that. 0 when the device cannot be read."""
    try:
        rdev = os.stat(path).st_rdev
    except (OSError, ValueError):
        return 0
    major, minor = os.major(rdev), os.minor(rdev)
    return (minor & 0xFF) | (major << 8) | ((minor & ~0xFF) << 12)


@dataclass(frozen=True)
class Pane:
    """A live pane of a record on this host: the record's id, the pane's first process, its pty, and
    the cgroup the pane runs in when it is the pane's own (`own_scope`), else empty."""

    session: str
    pid: int
    tty_nr: int = 0
    cgroup: str = ""


@dataclass(frozen=True)
class Channel:
    """What a connection is (§4.8a *The channel*), and the signal that decided it."""

    kind: str  # "session" | "outside" | "unknown"
    session: str | None = None
    signal: str | None = None  # ancestry | sid | tty · cgroup | unreadable · None for outside

    def label(self) -> str:
        return f"session {self.session}" if self.kind == "session" else self.kind


OUTSIDE = Channel("outside")


TMUX_UNIT = "agentorc-tmux.service"  # the installed home's tmux server, a system unit (design §4.1, TD-488)


def server_placement(cgroup: str | None) -> str | None:
    """Where the tmux server runs (design §4.1, TD-495): `system` in its own system unit
    (`…/agentorc-tmux.service`), `user` anywhere under the user manager (`user@<uid>.service`, where
    one stop of `systemd --user` ends every session — a warning), `none` elsewhere (a dev run, a
    container, a scratch home's server started from a shell); None with no server or no reading."""
    if not cgroup:
        return None
    parts = cgroup.rstrip("/").split("/")
    if parts[-1] == TMUX_UNIT:
        return "system"
    if any(p.startswith("user@") and p.endswith(".service") for p in parts):
        return "user"
    return "none"


def pane_cgroup(root: str | os.PathLike[str], server: str, name: str, pid: int, live: Collection[str]) -> str | None:
    """Give a pane a cgroup of its own under the server's delegated one (design §4.8a, the fourth
    signal; TD-495): `<root><server>/pane-<name>`, the pane's pid written to its `cgroup.procs`, so
    what the pane starts inherits it as a `tmux-spawn-*.scope` would give it. First the empty
    `pane-*` directories `live` (the tmux sessions now listed) no longer names are removed. Any
    failure leaves nothing made — the pane keeps the server's cgroup — and returns None."""
    base = os.path.join(os.fspath(root), server.strip("/"))
    try:
        for old in os.listdir(base):
            if old.startswith("pane-") and old[5:] not in live and old[5:] != name:
                with contextlib.suppress(OSError):  # an empty one only: a live process refuses it
                    os.rmdir(os.path.join(base, old))
    except OSError:
        return None
    path = os.path.join(base, f"pane-{name}")
    made = False
    try:
        if not os.path.isdir(path):
            os.mkdir(path)
            made = True
        with open(os.path.join(path, "cgroup.procs"), "w", encoding="ascii") as f:
            f.write(f"{int(pid)}\n")
    except (OSError, ValueError):
        if made:
            with contextlib.suppress(OSError):
                os.rmdir(path)
        return None
    return "/" + os.path.relpath(path, os.fspath(root))


def detached_check(reader: ProcReader, *, agent_pid: int, tmux_pid: int | None) -> str | None:
    """The cgroup a detached process would still be in — or None when the check is **off**.

    On only when the tmux server's cgroup is a systemd `.service` whose own process is either **the
    server itself** — the system unit, `agentorc-tmux.service`, the server's parent systemd (§4.1,
    TD-495) — or **the agent**: the server's cgroup is the agent's own and the agent was started by
    systemd itself (the user units: `KillMode=process` keeps the tmux server, and every pane, inside
    `agentorc-agent.service`). The unit's name is asked as well as the parent, because a server that
    daemonised inside a test runner's `.service` is reparented to init too.
    The agent's parent condition is what CI taught on the first push of this module: a test runner is often
    inside *some* `.service` together with the person standing in it, and so is any agent a worker
    starts from inside an `ao` pane — there the cgroup tells nobody apart, and with the check on
    the person read as *unknown*. A tmux server that predates the unit, a dev run from a shell, a
    container with one cgroup for everything: off, and such a peer is plainly *outside* — never a
    silent refusal (§4.8a)."""
    if not tmux_pid:
        return None
    mine, theirs = reader.cgroup(agent_pid), reader.cgroup(tmux_pid)
    if server_placement(theirs) == "system" and (srv := reader.stat(tmux_pid)) and reader.comm(srv.ppid) == "systemd":
        return theirs
    if not mine or mine != theirs or not mine.rstrip("/").endswith(".service"):
        return None
    me = reader.stat(agent_pid)
    if me is None or reader.comm(me.ppid) != "systemd":
        return None
    return mine


def classify(
    peer_pid: int,
    panes: Collection[Pane],
    reader: ProcReader,
    *,
    detached: str | None = None,
    ours: Collection[str] = (),
) -> Channel:
    """Which pane the peer belongs to, by the first of four signals that answers (§4.8a).

    *Ancestry* breaks on an ordinary race — a background `ao wait` whose parent shell exited is
    reparented to init — so the POSIX session id and then the controlling terminal are asked: a
    reparented process keeps both, and neither can be borrowed (`setsid` only creates a session; a
    terminal that is one session's cannot be taken by another without privilege). Last, the pane's
    own cgroup scope (`Pane.cgroup`, empty where panes share one), which a same-user process could
    move into as it could reopen a pty (TD-362). `detached` is `detached_check`'s answer: a peer
    that matched no pane but sits in that cgroup shed every signal on purpose or by accident, and
    is *unknown*, never the person — and so is one in `ours`, the scopes of the server's panes that
    no live record's pane holds (a pane that is no record's, a gone one inside the grace)."""
    peer = reader.stat(peer_pid)
    if peer is None:
        return Channel("unknown", signal="unreadable")
    by_pid = {p.pid: p.session for p in panes if p.pid > 1}
    # -- ancestry: each hop read twice, so a pid reused under the walk cannot steer it ------------
    cur = peer
    for _ in range(WALK_LIMIT):
        if cur.pid in by_pid:
            return Channel("session", by_pid[cur.pid], "ancestry")
        if cur.ppid <= 1:
            break
        parent = reader.stat(cur.ppid)
        again, parent2 = reader.stat(cur.pid), reader.stat(cur.ppid)
        if (
            parent is None
            or again is None
            or parent2 is None
            or again.ppid != cur.ppid
            or again.start != cur.start
            or parent2.start != parent.start
        ):
            break  # ancestry did not answer — nothing more; the next signal is asked
        cur = parent
    # -- the POSIX session id: the pane's first process is a session leader ----------------------
    if peer.sid in by_pid:
        return Channel("session", by_pid[peer.sid], "sid")
    # -- the controlling terminal: the pane's pty ------------------------------------------------
    if peer.tty_nr:
        for p in panes:
            if p.tty_nr and p.tty_nr == peer.tty_nr:
                return Channel("session", p.session, "tty")
    # -- the pane's own cgroup scope (TD-362): a tool that starts its shells with `setsid` and no
    # terminal (Claude Code) leaves an orphaned background command none of the three; tmux under
    # systemd starts each pane in a scope of its own, which the orphan keeps -----------------------
    cg = reader.cgroup(peer_pid) if detached or any(p.cgroup for p in panes) else None
    if cg:
        for p in panes:
            if p.cgroup and p.cgroup == cg:
                return Channel("session", p.session, "scope")
    if detached and cg and (cg == detached or cg in ours):
        return Channel("unknown", signal="cgroup")
    return OUTSIDE


def classify_gone(peer_pid: int, pane: Pane, reader: ProcReader) -> Channel | None:
    """`pane`'s session when the peer still carries its POSIX session id, its terminal, or — where
    the pane ran in a cgroup of its own — that cgroup: the signals an orphan keeps once the pane's
    first process has exited; else None (§4.8a *A hook just after its pane ended*, TD-115, TD-360).
    No walk: the pane pid is gone, so no ancestor can be it."""
    peer = reader.stat(peer_pid)
    if peer is None:
        return None
    if pane.pid > 1 and peer.sid == pane.pid:
        return Channel("session", pane.session, "sid")
    if pane.tty_nr and peer.tty_nr == pane.tty_nr:
        return Channel("session", pane.session, "tty")
    # A tool that starts its hooks with `setsid` and no terminal (Claude Code) leaves an orphan
    # neither of those; tmux under systemd starts each pane in a scope of its own, which it keeps
    if pane.cgroup and reader.cgroup(peer_pid) == pane.cgroup:
        return Channel("session", pane.session, "scope")
    return None


def own_scope(pane_pid: int, reader: ProcReader, shared: Collection[str]) -> str:
    """The cgroup `pane_pid` runs in when it is the pane's own — not its parent's (the tmux server's)
    and none of `shared` (the host agent's) — else empty. tmux built with systemd support starts each
    pane in a `tmux-spawn-<uuid>.scope`; a host where every pane shares the server's cgroup has none,
    and a gone pane is then matched by session id and terminal alone (TD-360). The caller also
    empties one that two panes share."""
    cg = reader.cgroup(pane_pid)
    st = reader.stat(pane_pid)
    if not cg or st is None or st.ppid <= 1 or cg in shared:
        return ""
    return "" if reader.cgroup(st.ppid) in (None, cg) else cg


@dataclass(frozen=True)
class Verdict:
    """What the request runs as under `enforce`: `caller` when served, else `refusal`; `alarm` is
    raised in `observe` too — a caller that *would* be refused shows up there exactly as it would
    in `enforce`, which is what observing is for. `about` is the record an alarm is kept on, None
    for the host's own list (a session is never framed by someone else's claim)."""

    caller: str | None
    refusal: str | None = None
    alarm: dict[str, Any] | None = None
    about: str | None = None


MISMATCH = "identity mismatch: this request did not come from the session it names (design §4.8a)"
CHECK_FAILED = (
    "the identity check failed and this host enforces it, so the request is refused: see the host agent's log; "
    "`local: {identity: observe}` in hosts.yml serves requests while it is fixed (design §4.8a)"
)


def judge(channel: Channel, claimed: str | None, rpc: str, *, hook_session: str | None = None) -> Verdict:
    """The rule table (§4.8a): the channel decides, and a claim that disagrees is never innocent.

    `claimed` is the envelope's `caller`, already normalised by the agent (None when absent);
    `hook_session` is the `hook` RPC's `session` parameter, bound the same way — a hook runs under
    its tool, under its pane. Reads are served on every channel, as claimed, and raise no alarm."""
    if rpc in READS:
        return Verdict(claimed)
    named = hook_session if rpc == "hook" else claimed

    def alarm(about: str | None) -> Verdict:
        entry = {"channel": channel.label(), "claimed": named or "", "rpc": rpc}
        return Verdict(claimed, refusal=MISMATCH, alarm=entry, about=about)

    if channel.kind == "session":
        if rpc == "hook":
            return Verdict(claimed) if named == channel.session else alarm(channel.session)
        # An absent `caller` under a pane is a script that forgot the variable, not a person: the
        # person does not live under a pane.
        return Verdict(channel.session) if named in (None, channel.session) else alarm(channel.session)
    if channel.kind == "outside":
        if rpc == "hook":
            return alarm(None)  # a hook runs under a pane, always
        return Verdict(None) if named is None else alarm(None)
    return alarm(None)  # unknown: refused, reads aside


OTHERS = "(others)"  # the `claimed` of the one entry that stands for every distinct alarm past the list's room


def coalesce(alarms: list[dict[str, Any]], entry: dict[str, Any], at: str) -> list[dict[str, Any]]:
    """`alarms` with `entry` recorded at `at`. Identical `{channel, claimed, rpc}` alarms are one
    entry with a `count` and its first and last time, so a loop cannot push a different alarm out.
    The list keeps the **first** `ALARMS_KEEP - 1` distinct alarms and then counts the rest in one
    closing `(others)` entry — never the newest-N: a session that has raised one alarm could
    otherwise bury it under twenty made-up ones (red-team of PR #248). The host agent's log has
    every alarm, one line each; this list is what a page shows."""
    key = (entry.get("channel"), entry.get("claimed"), entry.get("rpc"))
    out = [dict(a) for a in alarms]
    for a in out:
        if (a.get("channel"), a.get("claimed"), a.get("rpc")) == key:
            a["count"], a["last"] = int(a.get("count") or 1) + 1, at
            return out
    if len(out) < ALARMS_KEEP - 1:
        return [*out, {**entry, "count": 1, "at": at, "last": at}]
    if out and out[-1].get("claimed") == OTHERS:
        out[-1]["count"], out[-1]["last"] = int(out[-1].get("count") or 1) + 1, at
        return out
    return [*out, {"channel": "", "claimed": OTHERS, "rpc": "", "count": 1, "at": at, "last": at}]


def mode_of(value: Any) -> str:
    """`local: {identity: …}` as a mode. Anything unreadable is the default — never silently `off`."""
    v = str(value or "").strip().lower()
    return v if v in MODES else DEFAULT_MODE
