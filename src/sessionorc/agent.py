"""The host agent: one process per host, the only writer to `ao-*` tmux sessions (design §4.4, §9).

JSON-lines RPC over a Unix socket: `{"id": n, "method": "...", "params": {...}}` →
`{"id": n, "result": ...}` or `{"id": n, "error": "..."}`. `subscribe` turns the connection into
a stream of `{"event": "session", "session": {...}}` / `{"event": "gone", "id": ...}` lines; a
`wait` holds its connection until it returns and is dropped when that connection closes. A response
to a session with unread mail carries `"mail": {"unread": N, "wake_budget_spent": bool}` beside
its result or error (design §4.10 "a line on every `ao` reply").
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import inspect
import json
import logging
import os
import re
import secrets
import signal
import socket
import stat
import struct
import sys
import tarfile
import time
import unicodedata
from collections import OrderedDict, defaultdict
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sessionorc import adapters, containers, hosts, identity, link, mail, modes, naming, paths, reports, waits
from sessionorc.gitinfo import WorktreeError, ensure_worktree, git_info, worktree_path
from sessionorc.mail import ACTING_RPCS  # noqa: F401 — re-exported: callers read it from the agent
from sessionorc.models import (
    ASK_KINDS,
    GRANTS,
    HOME_OWNED,
    MAIL_KINDS,
    PERSON,
    PROGRESS_STATUSES,
    SOURCES,
    SYSTEM,
    FindingEntry,
    MailEntry,
    NotTheSameSession,
    Pending,
    ProgressEntry,
    SendEntry,
    Session,
    State,
    Tally,
    apply_home,
    apply_node,
    canonical_grants,
    has_control,
    normalize_ref,
    now_iso,
)
from sessionorc.store import EventQueue, IdentityAlarmStore, PersonInboxStore, SessionStore
from sessionorc.tmux import ARG_LIMIT, DuplicateSession, PaneInfo, Tmux

log = logging.getLogger("agentorc.agent")

TICK_SECONDS = float(os.environ.get("AGENTORC_TICK", "2"))
PUSH_OPEN = b'{"event": "session", "session": '  # a pushed view's envelope, measured not guessed (TD-066)
TAIL_LINES = 15  # cards show the last 3; the screen rules (TD-015) need the dialog above the options
CLOSED_KEEP = timedelta(days=1)
STALL_AFTER = timedelta(minutes=20)
# How long a declared claim holds its reference against another live session's claim (design §4.8
# "A claim is a lease", TD-056). Renewed by claiming again; released sooner by done/dropped or the
# holder's record ending. Long enough for one medium TD without a renewal, short enough that a
# stood-down worker does not hold a reference into the next day.
LEASE_TTL = timedelta(hours=12)
GIT_EVERY = timedelta(seconds=10)  # git status per live session, cheap and cached
# Derived report entries per session (design §4.8, TD-028 step 3): a `gh` call and a little git, so
# a slow cadence. Nothing waits on it and a failure derives nothing (`sessionorc.reports`).
DERIVE_EVERY = timedelta(minutes=5)
# The model in use per live agent session (TD-031): a local file's tail, so cheap, but not per tick.
MODEL_EVERY = timedelta(seconds=30)
CREATE_GRACE = timedelta(seconds=10)  # a pane snapshot older than a session cannot judge it
SEND_STALL_SECONDS = 5.0  # `send(wait=True)`: no sign of the prompt being taken within this → prompt-stalled
PASTE_SHOW_SECONDS = 1.0  # `send`: how long the pasted text gets to appear in the composer before Enter (TD-027)
SUBMIT_SECONDS = 1.5  # `send`: how long the composer gets to empty after Enter, per try (TD-027)
COMPOSER_LINES = 12  # raw rows an adapter's `composer` reads (the composer sits above a status line or two)
TITLE_CAP = 80  # characters of the tool's own title kept (design §4.5a **title**, TD-074): a name, not a line
SETTLED = ("idle", "needs-you", "exited", "closed", "limited", "stalled?")  # where a `send(wait=True)` ends
# `ACTING_RPCS` lives in `sessionorc.mail` beside the gates, and is re-exported here for the
# callers that always read it from the agent.
REMOVED_GUARD_SECONDS = 60.0  # how long a removed session's name is checked against re-adoption
PRUNE_EVERY = timedelta(hours=1)  # run-log retention sweep (design §4.6, `runs_keep_days`)
ID_RECHECK = 30.0  # seconds between re-reads of the tmux server's pid (design §4.8a, TD-077)
# A session past its `run_until` is asked to wrap up and then killed (design §6, TD-026): this is how
# long it is given to finish after the ask. It is a grace, not a deadline the session can see — a
# session that settles sooner is killed sooner, and one that is still working when it runs out is
# killed anyway, because the whole point is that nobody is watching.
WRAPUP_GRACE = timedelta(minutes=10)
REPORT_WRITE = 5.0  # seconds a node's report may take to write before the link is given up
# An act routed to a node (§4.4a, step 4a) is answered within this, on top of any wait the act
# itself carries (`send --wait --timeout N`): a `create` runs a worktree add and a tmux start.
ACT_TIMEOUT = 120.0
# What the home routes to the node whose name is the record's `host` (design §4.4a "A node reports
# and executes; the home decides"): the acts that touch a pane or the node's waiters, executed
# there with no gate of their own. `name_check` is a read, routed with a `host` for a team start.
# `identity_ack` is here for the same reason (§4.8a, review of PR #251): a record's identity alarms
# are **node-owned**, observed where the socket is, so the node clears its own list and the home
# learns it from the reply's record — a home that cleared its replica would have it back on the
# next report. Without an `id` the RPC is the home's own list and never leaves this host.
NODE_ACTS = frozenset({"send", "keys", "kill", "close", "remove", "decide", "create", "name_check", "identity_ack"})
# What the home owns and edits on its own copy (§4.4a "Each field has one owner"), and pushes to
# the node's replica in the same call so its stopping policies read the same intent. 4b generalises
# the push to every home-owned field on reconnect.
HOME_EDITS = frozenset({"set_mode", "set_stop", "set_grants", "set_controllers"})
# What the home reads from the node whose name is the record's `host` (§4.4a, step 4b.1): a pane's
# screen, which only that node's tmux holds. Reads are never gated (§9 invariant 11), so these are
# their own set and cross as their own link method, `read`, whose allowlist is this set alone — a
# read can never reach an acting method through it, and `act`'s allowlist never grows by a read.
NODE_READS = frozenset({"tail", "explain"})
# What the home pushes a node about each of its records (§4.4a "The home pushes each node its
# records' policy fields as they change", step 4b.2): the home-owned fields, less the mailbox — the
# inbox, outbox, threads, wakes and `mail_decided` stay the home's, and no message body ever reaches
# a node — less `sends`, which every pane's own node writes first (a send runs there) and the merge
# unions, and less `superseded_by`, which the node writes itself when a resume there supersedes a
# record and the home does not hear (a node's report carries node-owned fields only).
INTENT_FIELDS = HOME_OWNED - frozenset(
    {"inbox", "outbox", "threads", "wakes", "mail_decided", "sends", "superseded_by"}
)
# A checkout's files read across the link for a team start (§4.4a "Teams across hosts", step
# 4b.3): its repo config and the briefs its roles name. A read across a trust boundary, so bounded:
# at most this many files per call, each at most this many bytes, and only inside the checkout.
FILES_MAX = 16
FILE_CAP = 256 * 1024
# The home's nightly tarball of its store (§4.4a "When the home is lost", step 4b.3): what goes in,
# relative to AGENTORC_HOME — the org's records and the files that say what the org is — and how
# many days are kept. Nothing else: never a node's `env`, a token, a run log or a socket.
BACKUP_KEEP = 7
BACKUP_MEMBERS = ("sessions", "remote", "person_inbox.json", "org.yml", "hosts.yml", "profiles.yml")
REPORT_EVERY = 5.0  # seconds between a node's reports of one record whose state did not move (§4.4a)
USAGE_EVERY = 60.0  # seconds between usage polls per profile (TD-001): a slow cadence, never per tick
REMOVED_GUARD_SECONDS = 60.0  # how long a removed session's name is checked against re-adoption


class RpcError(Exception):
    """A refusal the caller is meant to act on. `data` rides along in the error envelope — the id
    of the session that already holds a name, say, so `ao new --json` can print it (design §4.1)."""

    def __init__(self, message: str, **data: Any):
        super().__init__(message)
        self.data = data


def _is_branch_claim(e: Any) -> bool:
    """A derived `claimed` entry with no PR: only the branch it came from ever supported it, so it
    is the one kind of report entry the tick may retire (TD-045)."""
    return e.source != "declared" and e.status == "claimed" and not e.pr


class _Wait:
    """One session (or person) blocked in `wait` (design §4.10: *blocked in `wait`* is what makes a
    session reachable). Registered while the RPC is blocked, removed the moment it returns or its
    connection closes, so the tick's decision never finds a ghost."""

    def __init__(self, caller: str | None) -> None:
        self.caller = caller
        self.poke = asyncio.Event()


def _peer_pid(writer: asyncio.StreamWriter) -> int | None:
    """The pid at the other end of a unix socket (`SO_PEERCRED`: pid, uid, gid), or None when the
    transport cannot say — an in-memory stream, a platform without it."""
    sock = writer.get_extra_info("socket")
    if sock is None or not hasattr(socket, "SO_PEERCRED"):
        return None
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except OSError:
        return None
    return struct.unpack("3i", raw)[0] or None


def _reply_line(req: dict[str, Any], reply: dict[str, Any]) -> bytes:
    """One reply is one line. A line longer than the limit every client opens its stream with is a
    line no client can read: the reader raises `Separator is found, but chunk is longer than
    limit`, the connection is lost, and the agent's log says nothing — on 2026-09-17 a `list` that
    had grown past asyncio's 64 KiB default took every `ao` on the machine down at once, and a
    person had to work out why (TD-066). So the agent refuses its own oversize reply rather than
    writing it: the request is answered, in words, against its own id, and the method that produced
    it is logged."""
    line = (json.dumps(reply) + "\n").encode()
    if len(line) <= link.FRAME_LIMIT:
        return line
    method = req.get("method")
    log.error("reply to %s is %d bytes, past the %d-byte line limit: refused", method, len(line), link.FRAME_LIMIT)
    refusal = {
        "id": reply.get("id", req.get("id")),
        "error": (
            f"the reply to {method!r} is {len(line)} bytes, past the {link.FRAME_LIMIT}-byte line "
            "limit — ask for less of it, or raise the limit at both ends"
        ),
    }
    return (json.dumps(refusal) + "\n").encode()


class HostAgent:
    def __init__(
        self,
        *,
        tmux: Tmux | None = None,
        store: SessionStore | None = None,
        events: EventQueue | None = None,
        identity_mode: str | None = None,
        proc: identity.ProcReader | None = None,
    ):
        paths.ensure_layout()
        # Who is calling (design §4.8a, TD-077): `off | observe | enforce` from `local: {identity: …}`,
        # the panes the last list saw, the home's own alarms (about no record), and a tally of
        # connections by class and deciding signal since start — what `ao identity` prints, and what
        # turning a host from `observe` to `enforce` is decided on.
        self.identity_mode = identity.mode_of(identity_mode or hosts.local_host().identity)
        self.proc: identity.ProcReader = proc or identity.LinuxProc()
        self._id_panes: list[identity.Pane] = []
        self._id_conns: dict[Any, identity.Channel] = {}  # a connection's classification, for its life
        self._id_dirty: set[str] = set()  # records whose alarm counts moved since their last write
        self._id_listed_at = 0.0
        self._id_list_lock = asyncio.Lock()
        self._id_detached: str | None = None
        self._id_tmux: tuple[int, int] | None = None  # (pid, start time): the server the answer is about
        self._id_rechecked = 0.0  # monotonic; the check is re-read on a cadence, not per connection
        # The home's own alarms **persist** (TD-077 step 2): a forgery aimed at no record — a claim
        # from outside every pane — is evidence, and evidence that dies with the process is a page
        # that says *no alarms* about the night the agent was restarted. The tally does not: it
        # says *since the agent started*, and §4.8a means that literally.
        self.identity_store = IdentityAlarmStore()
        self.identity_alarms: list[dict[str, Any]] = self.identity_store.load()
        self._id_host_dirty = False  # counts moved since the last write; the tick writes them
        self.identity_tally: dict[str, int] = defaultdict(int)
        self.tmux = tmux or Tmux()
        self.store = store or SessionStore()
        self.events = events or EventQueue()
        # This host's name (design §4.4a): what `host` on every record holds, and what an address
        # is qualified against — `ao-x@<this host>` is stored bare (`naming.qualify`).
        self.host = hosts.local_host().name
        # Home or node (design §4.4a, TD-057 step 2). With no `home:` in hosts.yml, or one naming
        # this host, this agent is the home and nothing below differs from phase 1. A node keeps
        # everything that touches its machine and its own host's records on disk — the replica.
        self.home = hosts.home_name()
        self.mode = "node" if self.home != self.host else "home"
        # The link (§4.4a "The link's protocol", TD-057 step 3a). At the home: one entry per node
        # that has ever connected — `{up, since, why}` — and the live `Mux` while it is up. At a
        # node: the same shape for its one link to the home.
        self.links: dict[str, dict[str, Any]] = {}
        self._link_muxes: dict[str, link.Mux] = {}
        # The container supervisor (§4.4a "The home supervises it", step 3c.3): per container node,
        # what the tick is doing about a down link — `{doing, since, attempts, next}` — and the one
        # action in flight. `container_runner` is the seam the suite replaces.
        self.supervision: dict[str, dict[str, Any]] = {}
        self._supervising: dict[str, tuple[asyncio.Task[None], containers.Runner]] = {}
        self.container_runner: Callable[[], containers.Runner] = lambda: containers.Runner(log=log.info)
        self.home_link: dict[str, Any] = {"up": False, "since": now_iso(), "why": "not dialed yet"}
        self._home_mux: link.Mux | None = None
        # A node's calls in flight here, by the node's token (step 5): a `wait` the node cancels
        self._forwarded_calls: dict[str, asyncio.Task[Any]] = {}
        # Other hosts' records, at the home (§4.4a "A node's records at the home", step 3b): held
        # apart from `self.sessions` — host → id → record — so nothing that reads this host's panes
        # ever meets one. Loaded from `remote/<host>/`, so a restarted home still shows them,
        # unreachable, until their node dials in.
        self.remote: dict[str, dict[str, Session]] = {}
        self._remote_stores: dict[str, SessionStore] = {}
        if self.mode == "home" and paths.home().joinpath("remote").is_dir():
            for d in sorted(paths.home().joinpath("remote").iterdir()):
                if d.is_dir():
                    self.remote[d.name] = self._remote_store(d.name).load_all()
        # What the home last pushed each linked node about each of its records (`intent`, step
        # 4b.2): host → id → payload. Present from that link's snapshot until the link goes, so a
        # new link is pushed everything once and then what changes.
        self._intent_sent: dict[str, dict[str, str]] = {}
        # A node's side of the same: what it last told the home about each record, and when.
        self._reported: dict[str, tuple[str, str, float]] = {}
        # A node's hint of each record's mail, as the home last pushed it: `(unread, budget spent)`.
        # Never an inbox — the mailbox is the home's — only what a reply's mail line needs.
        self._mail_hints: dict[str, tuple[int, bool, list[str]]] = {}
        self._snapshot_sent = False
        self._bg: set[asyncio.Task[None]] = set()  # fire-and-forget tasks, held so they are not collected
        if self.mode == "node":
            log.warning(
                "node of %s: this host's sessions only while the link is down — mail, reports, home-owned edits "
                "and sessions' acts on others are then refused, and forwarded to the home while it is up. "
                "This host calls itself %s: if this machine IS %s, set `local: {name: %s}` in hosts.yml — without "
                "it the name is the machine's hostname, and a home that does not recognise its own name is a node.",
                self.home,
                self.host,
                self.home,
                self.home,
            )
        self.sessions: dict[str, Session] = self.store.load_all()
        # The org's person inbox (design §4.10): held here, on no session record, in its own file.
        self.person_store = PersonInboxStore()
        self.person_inbox: list[MailEntry] = self.person_store.load()
        for s in self.sessions.values():
            # `prompt` pendings (Claude's idle_prompt) stopped being an alert on 2026-09-06; a record
            # written before that would otherwise show needs-you until the next hook event.
            if s.pending and s.pending.kind == "prompt":
                s.set_state("idle", confidence="hook")
                self.store.save(s)
            if not s.host:  # a record written before TD-057 step 1: it ran here, so it is this host's
                s.host = self.host
                self.store.save(s)
            if renamed := getattr(s, "renamed_grants", None):  # TD-055: read for one release, written new
                log.warning("%s: grant %s is now `control` (TD-055); the record is rewritten", s.id, ", ".join(renamed))
                self.store.save(s)
        # (sender, client nonce) → the verdict its first send got (design §4.4a "Delivery and
        # time"): a retry after a reconnect never lands twice and is not an identical repeat —
        # it returns the original verdict. Bounded, oldest out, in memory only.
        self._nonces: OrderedDict[tuple[str, str], tuple[dict[str, Any] | None, RpcError | None]] = OrderedDict()
        self._dir_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # subscriber → what it was last sent, per session (TD-009: a new tab gets its own snapshot
        # without every other tab being re-sent everything)
        self._subscribers: dict[asyncio.StreamWriter, dict[str, str]] = {}
        self._gone: list[str] = []  # forgotten ids not yet announced (`_forget` → `_push_changes`)
        self._waits: set[_Wait] = set()  # every `wait` blocked right now, each on its own connection
        # every open client connection: a stop closes them, or `Server.wait_closed()` waits on the
        # UI's subscription and each blocked `wait` forever (TD-058)
        self._conns: set[asyncio.StreamWriter] = set()
        # (session id, tool_use_id) → the hook's pending decision
        self._waiters: dict[tuple[str, str], asyncio.Future[dict[str, Any]]] = {}
        self._git_checked: dict[str, datetime] = {}
        self._derived_at: dict[str, datetime] = {}
        self._model_checked: dict[str, datetime] = {}
        # When a `kill` or a `close` destroyed a pane, so a tick holding a pane list taken before
        # it does not observe a session that is already gone (TD-063). Dropped as soon as a
        # snapshot newer than the kill arrives, so it holds at most one tick's worth of ids.
        self._killed_at: dict[str, datetime] = {}
        self._derive_task: asyncio.Task[None] | None = None
        # when a hook last reported on a session: a screen-rule verdict never outranks a hook
        # state fresher than STALL_AFTER (design §4.2); a session no hook has reported on yet — the
        # trust dialog case — takes the classifier's verdict at once (TD-015)
        # Seeded on load: a hook-confirmed record was fed by a live hook stream until the agent
        # stopped, and `since` is a transition time, not a hook time — so count it fresh as of now.
        # A restart must never let the screen outrank a state a hook just reported.
        self._last_hook: dict[str, datetime] = {
            sid: datetime.now(UTC) for sid, s in self.sessions.items() if s.confidence == "hook"
        }
        # profile → last usage dict from its adapter (`usage_for`), and when it was last asked
        self._usage: dict[str, dict[str, Any]] = {}
        self._usage_checked: dict[str, float] = {}
        self._usage_task: asyncio.Task[None] | None = None
        self._pre_limited: dict[str, State] = {}  # what a `limited` session was before the cap
        # Sessions removed recently: name → (the removed pane's tmux creation time, a monotonic
        # stamp for expiry). A pane snapshot taken before the remove must not re-adopt the pane it
        # still lists (the tick snapshots in a thread; remove runs between). Keyed by the pane's
        # own creation time, not by clocks compared across a thread hop, so a clock step cannot
        # re-adopt a dead pane and a hand-made session that reuses the name is adopted at once
        # (TD-020, TD-021). `None` for the creation time: the pane was already gone at remove.
        self._removed: dict[str, tuple[int | None, float]] = {}
        self._pruned_at = datetime.min.replace(tzinfo=UTC)  # first tick sweeps
        self._oversize: set[str] = set()  # ids whose view is past the line limit, logged once each
        self._backed_up = ""  # the local date of the last nightly tarball tried (a home only)
        self._backup_task: asyncio.Task[None] | None = None

    # -- lifecycle ------------------------------------------------------------------------------

    async def serve(self, sock: Path | None = None) -> None:
        sock = sock or paths.socket_path()
        sock.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            sock.unlink()
        self.tmux.ensure_server()
        # `limit`: a link's frame is one line, and a node's snapshot outgrows asyncio's 64 KiB default
        server = await asyncio.start_unix_server(self._handle_conn, path=str(sock), limit=link.FRAME_LIMIT)
        os.chmod(sock, 0o600)
        log.info("listening on %s", sock)
        link_servers = await self._bind_links() if self.mode == "home" else []
        ticker = asyncio.create_task(self._tick_loop())
        dialer = asyncio.create_task(self._dial_home()) if self.mode == "node" else None
        try:
            async with server:
                # `start_unix_server` is already serving. Not `serve_forever()`: cancelled, it awaits
                # `wait_closed()` itself, which since Python 3.12 waits for every client connection
                # to end — and a subscriber or a blocked `wait` never does on its own, so a stop hung
                # until systemd's SIGKILL. Close them first, then let `async with` wait: a `wait`
                # sees the close, is cancelled and writes its cursor on the way out (TD-058).
                try:
                    await asyncio.get_running_loop().create_future()
                finally:
                    server.close()
                    for _, srv in link_servers:
                        srv.close()
                    for w in list(self._conns):
                        w.close()
        finally:
            ticker.cancel()
            if dialer is not None:
                dialer.cancel()
            for m in list(self._link_muxes.values()):
                m.close("the home is stopping")
            for name in list(self._supervising):
                self._stop_supervising(name)  # a build in flight is killed, not left to finish alone
            with contextlib.suppress(FileNotFoundError):
                sock.unlink()
            for lsock, _ in link_servers:
                with contextlib.suppress(FileNotFoundError):
                    lsock.unlink()

    async def _bind_links(self) -> list[tuple[Path, asyncio.AbstractServer]]:
        """The home's per-node link sockets (design §4.4a "A container node", TD-057 step 3c): one
        listener per `nodes:` entry at `links/<name>/link.sock`, speaking only the link protocol. A
        connection on it *is* that node — the name is the home's configuration, never the node's
        argv — so it enters `_serve_link` as sshd's forced command would, with no bridge between.
        Read once, here: a new node is a restart. The directory is `0700` and the socket `0600`,
        and a container node is handed the directory, which outlives the socket file (re-bound on
        every start of the home)."""
        out: list[tuple[Path, asyncio.AbstractServer]] = []
        for name in hosts.nodes():
            lsock = paths.link_socket(name)
            lsock.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(lsock.parent, 0o700)
            with contextlib.suppress(FileNotFoundError):
                lsock.unlink()

            async def take(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, name: str = name) -> None:
                self._conns.add(writer)
                try:
                    await self._serve_link({"host": name}, reader, writer)
                except (ConnectionError, asyncio.IncompleteReadError):
                    pass
                finally:
                    self._conns.discard(writer)
                    writer.close()

            srv = await asyncio.start_unix_server(take, path=str(lsock), limit=link.FRAME_LIMIT)
            os.chmod(lsock, 0o600)
            log.info("link socket for %s at %s", name, lsock)
            out.append((lsock, srv))
        return out

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self.tick()
                await self._push_changes()
            except Exception:  # noqa: BLE001
                log.exception("tick failed")
            await asyncio.sleep(TICK_SECONDS)

    # -- reconcile -------------------------------------------------------------------------------
    #
    # Concurrency model: every mutation of `self.sessions` and of Session objects happens on the
    # event loop, never in a thread. Only the tmux subprocess calls run in threads, and they
    # return plain data. So a tick's reconcile step and an RPC handler can never interleave
    # inside a check-then-act sequence — the loop runs them one at a time.

    async def tick(self) -> None:
        snapshot_at = datetime.now(UTC)
        panes = await asyncio.to_thread(self.tmux.main_panes, naming.PREFIX)
        self._id_note_panes(panes)
        self._id_flush()
        await self._id_recheck_detached()
        tails = await asyncio.to_thread(lambda: {sid: self.tmux.capture_tail(sid, TAIL_LINES) for sid in panes})
        self._reconcile(panes, tails, snapshot_at)
        await self._refresh_git(snapshot_at)
        await self._refresh_model(snapshot_at)
        if self._derive_task is None or self._derive_task.done():
            # detached for the same reason the usage refresh is: `gh` talks to the network, and the
            # tick and its push must not wait on it (review 2026-09-11)
            self._derive_task = asyncio.create_task(self._derive_reports(snapshot_at))
        if snapshot_at - self._pruned_at > PRUNE_EVERY:
            self._pruned_at = snapshot_at
            # the live set is read here, on the loop (the class's one-writer rule); only the file
            # work goes to the thread
            live = {s.run_log for s in self.sessions.values() if s.run_log and s.state not in ("exited", "closed")}
            await asyncio.to_thread(self._prune_runs, snapshot_at, live)
        if self.mode == "home":
            self._supervise_containers()
            day = datetime.now().astimezone().date().isoformat()
            if day != self._backed_up and (self._backup_task is None or self._backup_task.done()):
                self._backed_up = day  # tried once a day: a failure is a log line and tomorrow's retry
                self._backup_task = asyncio.create_task(self._backup(day))
        if self._usage_task is None or self._usage_task.done():
            # detached: a slow usage endpoint (10 s timeout) must not hold up the tick or its push
            self._usage_task = asyncio.create_task(self._refresh_usage())
        await self._enforce_stop_times(snapshot_at)
        await self._sweep_mail(snapshot_at)
        self._poke_waits()  # the wake decision is re-taken every tick for a session blocked in `wait`

    async def _enforce_stop_times(self, now: datetime) -> None:
        """Stop the unattended sessions whose time is up (design §6, TD-026 gap 1).

        A session started by hand with `--unattended` used to have no stopper at all: nothing wrapped
        it up at 06:00, at a usage cap or when its token lapsed, so "start it now so I can watch it"
        meant "remember to close it yourself". A `run_until` is the general form, and the weekly run
        window (phase 3) becomes one way of setting it rather than a second mechanism.

        Two steps, in the order a person would use: at the time, the session is asked to wrap up —
        once, with the words the client gave at create, because this package must not know what a
        brief is. Then it is killed when it settles (its work is done, or it is waiting on a person
        who is not there) or when `WRAPUP_GRACE` runs out, whichever comes first. Interactive
        sessions are never touched: a stop time is a policy, and policies apply to unattended
        sessions alone (§4.2).
        """
        for s in list(self.sessions.values()):
            if not (s.unattended and s.run_until) or s.state in ("exited", "closed"):
                continue
            if now < _parse(s.run_until):
                continue
            if not s.wrapup_sent_at:
                if s.pending and s.pending.kind in ("permission", "question"):
                    # A session stopped on a dialog cannot wrap up, and typing at it would answer
                    # the dialog rather than reach the composer (`send` refuses for this reason,
                    # §4.2). Nobody is coming to answer it either — that is what unattended means —
                    # so it is stopped now rather than asked something it cannot hear.
                    log.info("%s reached its run_until on a pending %s; stopping it", s.id, s.pending.kind)
                    await self.rpc_kill(s.id)
                    continue
                if not s.wrapup_prompt:
                    # Nothing to say, so say nothing and stop it: a stop time with no wrap-up text is
                    # still a stop time, and silently running past it is the failure this fixes.
                    log.info("%s reached its run_until with no wrap-up prompt; stopping it", s.id)
                    await self.rpc_kill(s.id)
                    continue
                log.info("%s reached its run_until (%s); asking it to wrap up", s.id, s.run_until)
                try:
                    await self._submit(s.id, adapters.get(s.adapter), s.wrapup_prompt)
                except Exception:  # noqa: BLE001 — a pane that will not take a prompt is killed below
                    log.warning("%s would not take the wrap-up prompt; it will be stopped anyway", s.id)
                s.wrapup_sent_at = now_iso()
                self.store.save(s)
                await self._push_changes()
                continue
            settled = s.state in SETTLED
            if settled or now - _parse(s.wrapup_sent_at) >= WRAPUP_GRACE:
                log.info("%s stopped after its wrap-up (%s)", s.id, "settled" if settled else "grace ran out")
                await self.rpc_kill(s.id)

    async def _backup(self, day: str) -> None:
        """The nightly tarball (§4.4a "When the home is lost"): off the loop, and never an error
        anyone but the log hears of."""
        try:
            made = await asyncio.to_thread(backup_store, day)
            if made:
                log.info("backed up the store to %s", made)
        except Exception:  # noqa: BLE001 — a detached task: log, and try again tomorrow
            log.exception("the nightly backup of the store failed")

    def _prune_runs(self, now: datetime, live: set[str]) -> None:
        """Run-log retention (design §4.6): a log older than `runs_keep_days` goes unless it is in
        `live`, the logs of sessions still running (never truncate a live log: invariant 3). Logs
        of forgotten sessions are the common case — Forget keeps the file until this sweep. `0`
        keeps everything. Runs in a thread: touches files, never `self.sessions`."""
        keep = hosts.local_host().runs_keep_days
        if keep <= 0:
            return
        cutoff = (now - timedelta(days=keep)).timestamp()
        for f in paths.runs_dir().glob("*.log"):
            try:
                if str(f) not in live and f.stat().st_mtime < cutoff:
                    f.unlink()
                    log.info("pruned run log %s (older than %d days)", f.name, keep)
            except OSError:
                continue

    async def _refresh_git(self, now: datetime) -> None:
        due = [
            s
            for s in self.sessions.values()
            if s.dir
            and s.state not in ("closed",)
            and now - self._git_checked.get(s.id, datetime.min.replace(tzinfo=UTC)) > GIT_EVERY
        ]
        if not due:
            return
        results = await asyncio.gather(*(asyncio.to_thread(git_info, s.dir) for s in due), return_exceptions=True)
        infos = {s.id: (r if not isinstance(r, BaseException) else None) for s, r in zip(due, results, strict=True)}
        for s in due:
            self._git_checked[s.id] = now
            live = self.sessions.get(s.id)
            if live is None:
                continue
            info = infos.get(s.id)
            new = info.to_dict() if info else None
            if new != live.git:
                live.git = new
                self.store.save(live)

    async def _derive_reports(self, now: datetime) -> None:
        """A detached task: log a failure, never let it vanish silently, and announce what changed
        (the tick's own push has been and gone by the time this finishes)."""
        try:
            await self._derive_reports_inner(now)
        except Exception:  # noqa: BLE001
            log.exception("deriving reports failed")
        finally:
            await self._push_changes()

    async def _derive_reports_inner(self, now: datetime) -> None:
        """Fill in the report channels the session did not declare (design §4.8, §9 invariant 10):
        its branch, the PRs from it, and the ledger rows those PRs put on main. Every entry is
        marked `derived`, so a declaration stands whatever this finds — the record refuses the
        write, which is why a refusal is not an error. Runs off the branch `_refresh_git` already
        read, on its own slow cadence, and never blocks the tick on a failure.

        What is checked out in a directory is derived only for the record that *holds* that
        directory now (TD-034, `reports.holds_directory`) — a worktree is reused run after run, and
        an exited predecessor sharing the `dir` would otherwise be credited with its successor's
        branch and PRs. Every record keeps the `pending`-by-PR re-check, which is attributed by a PR
        the record itself claimed, so a merge still lands on a worker that has since exited
        (TD-032).

        A branch-only claim the session has moved off is looked up one last time by the branch it
        came from and then *retired* if that branch never grew a PR (TD-045): nothing else could
        ever remove one — the re-check above works by PR number, the upsert has no delete branch,
        and invariant 10 only replaces a derived entry when the session declares the same reference
        — and a permanent false `claimed` is exactly what the idle-with-open-work send fires on."""
        if self.mode == "node" and not self.home_reachable():
            # `progress` and `findings` are the home's (§4.4a, step 4b.2): a claim written to the
            # replica is overwritten on reconnect and was never checked against the siblings' leases.
            return
        holders = reports.holds_directory(self.sessions.values())
        due: list[tuple[Session, str | None, list[tuple[str, int]], list[tuple[str, str | None]]]] = []
        for s in self.sessions.values():
            if not s.dir or s.state == "closed":
                continue
            if now - self._derived_at.get(s.id, datetime.min.replace(tzinfo=UTC)) <= DERIVE_EVERY:
                continue
            branch = (s.git or {}).get("branch") if s.id in holders else None
            pending = [e for e in s.progress if e.source != "declared" and e.status == "claimed" and e.pr]
            # Branch-only claims from a branch this record is no longer on (TD-045). Only for a
            # record that still holds its directory: what is checked out elsewhere says nothing
            # about this one, and an exited worker's claims are its history, not a live question.
            left = (
                [(e.ref, e.branch) for e in s.progress if _is_branch_claim(e) and e.branch != branch]
                if s.id in holders
                else []
            )
            if not branch and not pending and not left:
                continue
            due.append((s, branch, [(e.ref, e.pr) for e in pending if e.pr], left))
        if not due:
            return
        results = await asyncio.gather(
            *(
                # the ledger path the client read from the repo's config at create (design §5
                # `ledger:`), else the default — this package never reads `.agentorc.yml` itself
                asyncio.to_thread(reports.derive, s.dir, branch, pend, s.ledger or reports.LEDGER_DEFAULT, left)
                for s, branch, pend, left in due
            ),
            return_exceptions=True,
        )
        for (s, _branch, _pending, _left), result in zip(due, results, strict=True):
            self._derived_at[s.id] = now
            live = self.sessions.get(s.id)
            if live is None:
                continue
            if isinstance(result, BaseException):
                log.warning("deriving reports for %s failed: %s", s.id, result)
                continue
            progress, findings, retire = result
            if self.mode == "node":
                # sent to the home as the derived reports they are; the home's push brings them back
                await self._send_derived(live, progress, findings, retire)
                continue
            # Lists, not generators: these upserts are the write, and `any()` over a generator
            # would stop at the first change and silently drop every later entry (review 2026-09-11).
            applied = [live.report_progress(e) for e in progress] + [live.report_finding(e) for e in findings]
            changed = any(applied) | live.retire_branch_claims(retire)
            if changed:
                self.store.save(live)

    async def _refresh_model(self, now: datetime) -> None:
        """The model each live agent session is running (TD-031, design §4.2a). The hook reports it
        at SessionStart and on a `/model` switch; this is the cross-check and the fallback for a
        session that started before the hook carried one — a read of the transcript's tail, in a
        thread, on its own cadence. An adapter that cannot tell leaves the field alone."""
        due = []
        for s in self.sessions.values():
            if not (s.adapter_id and s.dir) or s.state == "closed":
                continue
            if now - self._model_checked.get(s.id, datetime.min.replace(tzinfo=UTC)) <= MODEL_EVERY:
                continue
            try:
                fn = getattr(adapters.get(s.adapter), "model_in_use", None)
            except KeyError:
                fn = None
            if fn:
                due.append((s, fn))
        if not due:
            return
        results = await asyncio.gather(
            *(asyncio.to_thread(fn, str(s.adapter_id), Path(s.dir), s.profile) for s, fn in due),
            return_exceptions=True,
        )
        for (s, _), result in zip(due, results, strict=True):
            self._model_checked[s.id] = now
            live = self.sessions.get(s.id)
            if live is None or isinstance(result, BaseException) or not result:
                continue
            if live.model != result:
                live.model = str(result)
                self.store.save(live)

    async def _refresh_usage(self) -> None:
        """Ask each live agent session's adapter for its profile's usage once a minute, in a
        thread; a fetch failure keeps the last answer and never gates anything (design §6).
        Then the `limited` rule: an interactive session on a profile at 100% of a window shows
        `limited` with the reset time, and goes back to what it was once the window resets."""
        try:
            await self._refresh_usage_inner()
        except Exception:  # noqa: BLE001 — a detached task: log, never let it vanish silently
            log.exception("usage refresh failed")
        finally:
            await self._push_changes()

    async def _refresh_usage_inner(self) -> None:
        live = [
            s
            for s in self.sessions.values()
            if s.kind == "interactive" and s.adapter != "shell" and s.state not in ("exited", "closed")
        ]
        mono = time.monotonic()
        due: dict[str, Any] = {}
        for s in live:
            fn = getattr(adapters.get(s.adapter), "usage_for", None)
            if fn and s.profile not in due and mono - self._usage_checked.get(s.profile, -USAGE_EVERY) >= USAGE_EVERY:
                due[s.profile] = fn
        if due:
            results = await asyncio.gather(
                *(asyncio.to_thread(fn, prof) for prof, fn in due.items()), return_exceptions=True
            )
            for prof, r in zip(due, results, strict=True):
                self._usage_checked[prof] = mono
                if isinstance(r, dict) and r != self._usage.get(prof):
                    self._usage[prof] = r
                    await self._broadcast({"event": "usage", "profile": prof, "usage": r})
        for s in live:
            cap = _cap(self._usage.get(s.profile))
            if cap and s.state not in ("limited", "needs-you"):
                # the tool's own endpoint, not the screen: reported, so `hook` (design §9 invariant 4)
                self._pre_limited[s.id] = s.state
                s.set_state("limited", confidence="hook", pending=Pending(kind="limit", text=cap))
                self.store.save(s)
            elif not cap and s.state == "limited" and s.pending and s.pending.kind == "limit":
                # back to what it was (idle stays idle: no hook will come to correct a wrong `working`)
                s.set_state(self._pre_limited.pop(s.id, "working"), confidence="hook")
                self.store.save(s)

    def _reconcile(self, panes: dict[str, PaneInfo], tails: dict[str, list[str]], snapshot_at: datetime) -> None:
        for sid, event in self.events.drain():
            self._apply_event(sid, event)
        now = datetime.now(UTC)
        mono = time.monotonic()
        self._removed = {k: v for k, v in self._removed.items() if mono - v[1] < REMOVED_GUARD_SECONDS}
        for sid, s in list(self.sessions.items()):
            if s.state == "closed":
                if s.closed_at and _parse(s.closed_at) + CLOSED_KEEP < now:
                    self._forget(sid)
                continue
            killed = self._killed_at.get(sid)
            if killed is not None and killed < snapshot_at:
                del self._killed_at[sid]  # this snapshot is newer than the kill: the guard is spent
                killed = None
            pane = panes.get(sid)
            if pane is not None and killed is not None:
                # The pane list was taken before the kill that ended this record, so it still lists
                # a tmux session that is gone. Observing it would set `pane` back to True and read a
                # state off the last screen — an `exited` record flipped back to `idle`, which then
                # refuses its own `remove` ("kill it first"). The window is the two `to_thread` hops
                # between the snapshot and here, and an RPC runs on the loop inside them (TD-063,
                # seen on a 3.12 CI runner 2026-09-17).
                continue
            if pane is None:
                # A session created after the pane snapshot was taken is not judged by it.
                if _parse(s.created) + CREATE_GRACE < snapshot_at and (s.state != "exited" or s.pane):
                    s.set_state("exited", confidence="scraped")
                    s.pane = False  # gone for good: killed, or the tmux server restarted (TD-023)
                    self.store.save(s)
                continue
            self._observe(s, pane, tails.get(sid, []), now)
        # tmux sessions with our prefix that we have no record of (created by hand, or the
        # store was lost): adopt them minimally as shells so they appear in the Org.
        for name, pane in panes.items():
            if name not in self.sessions and not self._is_removed_pane(name, pane):
                s = Session(
                    id=name,
                    name=name[len(naming.PREFIX) :],
                    kind="interactive",
                    adapter="shell",
                    dir="",
                    host=self.host,
                )
                s.created = datetime.fromtimestamp(pane.created, UTC).isoformat().replace("+00:00", "Z")
                self.sessions[name] = s
                self._observe(s, pane, tails.get(name, []), now)

    def _is_removed_pane(self, name: str, pane: PaneInfo) -> bool:
        """Is this the pane a recent `remove` killed (as a stale snapshot would still list it)? A
        pane born after the removed one is a new session and adopts normally (TD-021)."""
        rem = self._removed.get(name)
        if rem is None:
            return False
        created, _ = rem
        # `created` is tmux's `session_created`, whole seconds. Equal means "could be the same
        # pane", so it is skipped: a pane that was created, exited, observed, removed *and* had
        # its name reused inside one second is the only case this delays (until the guard
        # expires), and `None` (the pane was already gone at remove) is treated the same way.
        return created is None or pane.created <= created

    def _observe(self, s: Session, pane: PaneInfo, tail: list[str], now: datetime) -> None:
        adapter = adapters.get(s.adapter)
        s.tail = [_clean(t) for t in tail]
        s.title = _pane_title(adapter, pane.title)
        s.pane = True
        if s.run_log:
            with contextlib.suppress(OSError):
                mtime = datetime.fromtimestamp(Path(s.run_log).stat().st_mtime, UTC)
                s.last_output = mtime.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if pane.dead:
            s.exit_code = pane.dead_status
            if s.state != "exited":
                s.set_state("exited", confidence="scraped")
        elif adapter.state_source == "scraped":
            st = adapter.classify(pane, tail)
            if st and st != s.state:
                s.set_state(st, confidence="scraped")
        elif (m := self._screen_verdict(s, adapter, now)) is not None:
            # Hook-fed adapter, screen rule fired, no fresher hook state: the labelled fallback
            if m.state != s.state or (m.pending and m.pending != s.pending):
                s.set_state(m.state, confidence="scraped", pending=m.pending)
        elif s.state == "working" and s.last_output and now - _parse(s.last_output) > STALL_AFTER:
            # Hook-fed adapters: the liveness cross-check applies to `working` alone — a
            # `needs-you` or `idle` session is silent by design.
            s.set_state("stalled?", confidence="scraped")
        self.store.save(s)

    def _hook_fresh(self, sid: str, now: datetime) -> bool:
        last = self._last_hook.get(sid)
        return last is not None and now - last <= STALL_AFTER

    def _screen_verdict(self, s: Session, adapter: Any, now: datetime) -> Any:
        """The adapter's screen-rule match for the session's tail, or None when there is no rule
        set, nothing matched, or a hook state is fresher (design §4.2: scraped never outranks it)."""
        explain = getattr(adapter, "explain", None)
        if explain is None or self._hook_fresh(s.id, now):
            return None
        return explain(s.tail)

    def _apply_event(self, sid: str, event: dict[str, Any]) -> None:
        """A hook-fed state transition (design §4.2 table). Adapters map hook names to these."""
        s = self.sessions.get(sid)
        if s is None:
            return
        self._last_hook[sid] = datetime.now(UTC)  # apply time, also for events drained from the offline queue
        if aid := event.get("adapter_id"):
            s.adapter_id = aid
        if model := event.get("model"):
            s.model = str(model)  # SessionStart's `model`, or a `/model` switch (TD-031)
        if delta := event.get("subagent_delta"):
            s.subagents = max(0, s.subagents + int(delta))
        state = event.get("state")
        if state:
            pending = Pending.from_dict(event["pending"]) if event.get("pending") else None
            if state == "needs-you" and self._permission_waiting(sid):
                # Claude Code draws its own dialog a few seconds into a PermissionRequest hook
                # and fires a permission_prompt Notification for it; the hook is still blocking
                # and its answer still wins (verified 2026-09-06). Keep Allow / Deny up.
                pass
            else:
                s.set_state(state, confidence="hook", pending=pending)
        self.store.save(s)

    def _permission_waiting(self, sid: str) -> bool:
        return any(k[0] == sid and not f.done() for k, f in self._waiters.items())

    def _scrub(self, sid: str) -> None:
        """No cadence or hook key outlives the session it was about — whether the record is
        forgotten or replaced in place by a new session of the same name (§4.1)."""
        for side in (
            self._git_checked,
            self._derived_at,
            self._model_checked,
            self._pre_limited,
            self._last_hook,
            self._killed_at,
            self._mail_hints,
        ):
            side.pop(sid, None)

    def _forget(self, sid: str) -> None:
        gone = self.sessions.get(sid)
        if gone is None:
            return  # already forgotten (two removes of one id in flight): nothing more to announce
        # Its open `ask`s expire with it (design §4.10 lifecycle): the record and its inbox go, and
        # every other holder of those asks — the askers — is told so. Done while it is still in the
        # map so `_mark` reaches it, harmlessly, along with the rest. A `steer` is the exception:
        # its bound runs whatever becomes of the addressee, so the sender's copy lapses on time.
        for e in [e for e in gone.inbox if e.open and e.kind != "steer"]:
            self._close_entry(e.id, "expired", now_iso())
        self._asker_gone(gone, self._address(gone))
        self.sessions.pop(sid, None)
        self.store.delete(sid)
        # Scrub the id from every subscriber's map and queue the one `gone`: whichever
        # `_push_changes` runs next (the caller's or a tick's) announces it exactly once.
        for last in self._subscribers.values():
            last.pop(sid, None)
        self._scrub(sid)
        self._gone.append(sid)

    def _asker_gone(self, s: Session, *ids: str) -> None:
        """Design §4.10 *What a person is asked*: an `ask` to the person cannot expire, so the
        other half of its lifecycle is the **asker's** — closing or forgetting a record closes the
        open `ask`s and `steer`s it put to the person, `closed_reason: asker_gone`, or a forgotten
        worker's questions would stand forever. An asker that merely **exited** leaves them open: a
        resume may still want the answer. Nothing is told — there is no one left to tell.

        **A record a resume superseded is not a gone asker** (review of PR #245): the conversation
        continues under the new id, `_move_mail` moved its questions' `from` there with it, and this
        record is closed only as the bookkeeping of that move — forgetting it a day later must not
        close a question the resumed session is still waiting on. The id rewrite is what makes this
        so; this is the second line, for a superseded record whose person-inbox copy was pruned and
        written again, or a rewrite a future path misses."""
        if s.superseded_by:
            return
        at = now_iso()
        for e in [e for e in self.person_inbox if e.from_ in (s.id, *ids) and e.open]:
            self._close_entry(e.id, "asker_gone", at)
        # And the debt goes with the asker (design §4.10 *Outcomes*): a question the person
        # answered whose asker was **closed or forgotten** is settled `asker_gone` — nobody is left
        # to report it, and the row must not wait in the Inbox for a session that cannot come back.
        # An asker that merely **exited** still owes: that row waits until the person opens the
        # session or dismisses it, which is why this runs from close and forget and not from exit.
        for e in [e for e in self.person_inbox if e.from_ in (s.id, *ids) and e.owes]:
            self._mark(e.id, outcome={"state": "asker_gone", "text": "", "at": at, "by": ""})

    # -- RPC methods -----------------------------------------------------------------------------

    async def rpc_list(self) -> list[dict[str, Any]]:
        return self._views()

    async def rpc_get(self, id: str) -> dict[str, Any]:
        return self._view(self._find(id), bookkeeping=True)  # one record: its tallies and wakes ride along

    async def rpc_create(
        self,
        *,
        name: str,
        dir: str,
        adapter: str = "shell",
        profile: str = "",
        kind: str = "interactive",
        repo: str | None = None,
        worktree: str | None = None,
        argv: list[str] | None = None,
        unattended: bool = False,
        resume: str | None = None,
        prompt: str | None = None,
        capabilities: list[str] | None = None,
        lane: list[str] | None = None,
        controllers: list[str] | None = None,
        role: str = "",
        ledger: str | None = None,
        team: str = "",
        project: str = "",
        run_until: str | None = None,
        wrapup_prompt: str | None = None,
        host: str | None = None,
        caller: str | None = None,
    ) -> dict[str, Any]:
        if host and host != self.host:
            # Routed before the method runs (`_act_host`) when this is the home; a node asked for
            # another host's create got here through the link, and the link is one host's.
            raise RpcError(f"{host} is not this host ({self.host}): a create lands on the host it names")
        directory = Path(dir).expanduser().resolve()
        if not directory.is_dir():
            raise RpcError(f"not a directory: {directory}")
        grants, references = _grants(capabilities or []), _lane(lane or [])  # validate before anything starts
        # Create adds the creator (design §4.8): a session that starts another may act on what it
        # started, without a second call and without a person in the loop. `caller` is the request
        # envelope's, injected by the dispatcher — a person's create has none, and then the new
        # session begins with only whatever `--controller` asked for (often nothing, which means
        # nobody may act on it: the explicit default).
        members = [self._addr(c) for c in _controllers(controllers or [])]
        if caller and caller not in members:
            members.insert(0, str(caller))
        try:
            ad = adapters.get(adapter)
        except KeyError as e:
            raise RpcError(str(e).strip('"')) from None
        # A prompt no path can deliver is refused **here**, before anything is created (TD-068): an
        # adapter hands it to the tool as one argument, and the kernel refuses one past `ARG_LIMIT`.
        # `_fit` says the same thing at the tmux layer, but by then `create` has made a worktree
        # that no record points at — which is exactly what happened on 2026-09-18.
        if prompt and len(str(prompt).encode("utf-8", "surrogateescape")) > ARG_LIMIT:
            raise RpcError(
                f"the prompt is {len(str(prompt).encode('utf-8', 'surrogateescape')):,} bytes — past what a "
                f"process can be started with ({ARG_LIMIT:,}): put a brief that long in a file and tell the "
                "session to read it (design §4.9, TD-068)"
            )
        if worktree:
            # Design §4.5 New session "new worktree": the checkout is the repo, the session runs in
            # `<repo>/.claude/worktrees/<name>` on branch <name>, created here if missing.
            repo_root = Path(repo).expanduser().resolve() if repo else directory
            async with self._dir_locks[f"worktree:{repo_root}:{worktree}"]:  # two creates of one name: second reuses
                try:
                    directory = await asyncio.to_thread(ensure_worktree, repo_root, worktree)
                except WorktreeError as e:
                    raise RpcError(str(e)) from None
            repo = str(directory.parent.parent.parent)  # <repo>/.claude/worktrees/<name> → the main checkout
        async with contextlib.AsyncExitStack() as locks:
            await locks.enter_async_context(self._dir_locks[str(directory)])
            # A name identifies one session per *scope* (§4.1), and a scope spans a repo's
            # worktrees — so the name check needs a lock on the scope, not just on this directory,
            # or two concurrent creates of one name from two worktrees would both pass it (review).
            await locks.enter_async_context(self._dir_locks[f"scope:{naming.scope_slug(directory, repo)}"])
            if resume:
                # one create per conversation at a time, whatever the directory: two concurrent
                # resumes of one id would otherwise both pass the holder check below (TD-012)
                await locks.enter_async_context(self._dir_locks[f"conversation:{resume}"])
            if kind == "interactive" and adapter != "shell":
                for who in await asyncio.to_thread(self.occupants, directory):
                    raise RpcError(f"{directory} already has agent session {who}; anchor rule (use a worktree)")
            # Design §4.1 / §9 invariant 12: a name identifies one session per scope. An unnamed
            # session is named here, not by its caller, so two of them never collide (TD-030).
            if not name.strip():
                name = await asyncio.to_thread(self._auto_name, directory, repo, adapter)
            # Refuse here; *take* the name below, once the launch has succeeded. Taking it kills a
            # pane, and a launch that then failed would have killed it for nothing (review).
            holder = await self._name_holder(directory, repo, name)
            if resume:
                for who in await asyncio.to_thread(self.conversation_holders, resume):
                    raise RpcError(f"conversation {resume} is still live in {who}; kill it first, or Switch to it")
            try:
                spec = ad.launch(
                    profile=profile, resume=resume, prompt=prompt, unattended=unattended, cwd=directory, name=name
                )
            except (KeyError, ValueError) as e:
                raise RpcError(str(e).strip('"')) from None
            if argv:
                spec.argv = argv
            previous_run, freed = await self._take_name(holder)
            # read after the supersede, so the id it freed is not `taken` and gets reused
            live = await asyncio.to_thread(lambda: [p.session for p in self.tmux.list_panes()])
            # Records can no longer hold the base id: `_claim_name` refused or superseded the one
            # session of this name in scope. What is left is a tmux session nobody has a record of
            # — and then the suffix is part of the name the Org shows (TD-030 step 2).
            taken = (set(self.sessions) | set(live)) - ({freed} if freed else set())
            base = naming.base_id(directory, repo, name)
            for _attempt in range(5):
                sid = naming.session_id(directory, repo, name, taken)
                # Ours win, whatever an adapter sets. AGENTORC_HOME is explicit because the tmux
                # server may predate this agent and carry a different environment; a hook script
                # inside the session must find *this* agent's socket (found the hard way, 2026-09-06).
                env = {**spec.env, "AGENTORC_SESSION": sid, "AGENTORC_HOME": str(paths.home())}
                run_log = paths.runs_dir() / f"{sid}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.log"
                try:
                    await asyncio.to_thread(self._start, sid, directory, spec.argv, env, run_log)
                    break
                except DuplicateSession:
                    taken.add(sid)  # design §4.1: handle tmux's own verdict, not just our check
            else:
                raise RpcError(f"could not find a free session name for {name!r} in {directory}")
            s = Session(
                id=sid,
                name=name if sid == base else name + sid[len(base) :],  # a shown suffix, never a hidden one
                kind=kind,  # type: ignore[arg-type]
                adapter=adapter,
                dir=str(directory),
                profile=profile,
                repo=repo,
                worktree=worktree,
                unattended=unattended,
                confidence=ad.state_source,
                run_log=str(run_log),
                adapter_id=spec.adapter_id,
                capabilities=grants,
                controllers=members,
                lane=references,
                role=str(role or ""),
                ledger=(str(ledger).strip() or None) if ledger else None,
                team=str(team or ""),  # badges (§4.9): stored as given, never validated here
                project=str(project or ""),
                run_until=_stop_time(run_until),
                wrapup_prompt=(str(wrapup_prompt).strip() or None) if wrapup_prompt else None,
                previous_run=previous_run,
                host=self.host,
            )
            self.sessions[sid] = s
            self.store.save(s)
            self._remember_dir(directory)
            if resume:
                await self._supersede(resume, sid)
        return s.view()

    async def rpc_name_check(
        self, dir: str, name: str, repo: str | None = None, host: str | None = None
    ) -> dict[str, Any]:
        """What §4.1's name rule would do to this name, without doing it: the New session form's
        check as you type, and the note `ao new` prints (design §4.5a, TD-030 step 4)."""
        if host and host != self.host:
            raise RpcError(f"{host} is not this host ({self.host}): a name is checked on the host it would run on")
        verdict, _ = await self._name_verdict(Path(dir).expanduser(), repo, name)
        return verdict

    async def _name_verdict(
        self, directory: Path, repo: str | None, name: str
    ) -> tuple[dict[str, Any], Session | str | None]:
        """The one place §4.1's rule is decided, so the form, the CLI and `create` never drift:
        `(what it would do, what it would have to supersede)`. `verdict` is `free`, `live` (a
        holder that is running) or `supersede`, with the text both surfaces show."""
        base = naming.base_id(directory, repo, name)
        out: dict[str, Any] = {"id": base, "name": name, "verdict": "free", "holder": None, "message": ""}
        if not name.strip():
            return out, None
        holder = self.sessions.get(base)
        if holder is None:
            # No record, but tmux may still hold the id (a hand-made session the tick has not
            # adopted yet, or a pane whose record was lost). Decide on tmux's own answer rather
            # than on when the next tick runs, or the same `ao new` would refuse or suffix
            # depending on the second it landed in (design §4.1).
            pane = (await asyncio.to_thread(self.tmux.main_panes, naming.PREFIX)).get(base)
            if pane is None:
                return out, None
            if not pane.dead:
                out |= {
                    "verdict": "live",
                    "holder": base,
                    "holder_state": "unrecorded",
                    "message": f"{name} is running outside agentorc — its card appears within a tick",
                    "hint": f"switch to it with: ao focus {base}",
                }
                return out, None
            out |= {"verdict": "supersede", "holder": base, "holder_state": "unrecorded"}
            out["message"] = f"replaces a leftover tmux pane called {name} — it has no record and no log"
            return out, base  # a dead pane nobody has a record of: nothing to keep from it
        if holder.state not in ("exited", "closed"):
            out |= {
                "verdict": "live",
                "holder": holder.id,
                "holder_state": holder.state,
                "message": f"{name} is running — switch to it, or pick another name",
                "hint": f"switch to it with: ao focus {holder.id}",
            }
            return out, None
        out |= {
            "verdict": "supersede",
            "holder": holder.id,
            "holder_state": holder.state,
            "message": f"replaces the {holder.state} {name} — run log kept",
        }
        return out, holder

    async def _name_holder(self, directory: Path, repo: str | None, name: str) -> Session | str | None:
        """Raises for a **live** holder; otherwise returns what a new session would supersede — the
        exited or closed record, or the id of a dead pane nobody has a record of — or None when the
        name is free. Nothing is destroyed here: `_take_name` does that once the launch has
        succeeded (TD-030)."""
        verdict, holder = await self._name_verdict(directory, repo, name)
        if verdict["verdict"] == "live":
            raise RpcError(
                verdict["message"],
                holder=verdict["holder"],
                holder_state=verdict["holder_state"],
                hint=verdict["hint"],
            )
        return holder

    async def _take_name(self, holder: Session | str | None) -> tuple[str | None, str | None]:
        """Supersede what `_name_holder` found, returning `(previous_run, the id it freed)`. The
        dead pane is killed and the record's run log handed on; the record itself is **replaced in
        place** by the new one under the same id — not forgotten — so the Org's card becomes the
        new session rather than going and coming back, and a launch that fails after this point
        leaves the old record standing instead of losing it (review 2026-09-11)."""
        if holder is None:
            return None, None
        if isinstance(holder, str):
            await asyncio.to_thread(self.tmux.kill_session, holder)
            return None, holder
        await asyncio.to_thread(self.tmux.kill_session, holder.id)  # a dead pane, if it still has one
        self._scrub(holder.id)  # the side tables are about the old session, not the new one
        log.info("%s superseded the %s session of the same name", holder.name, holder.state)
        return holder.run_log, holder.id

    def _auto_name(self, directory: Path, repo: str | None, adapter: str) -> str:
        """`shell`, `shell-2`, … for a session started without a name (TD-030 step 3): the agent
        picks it, so the name it is shown under is the name it holds. Runs in a thread: tmux."""
        stem = "shell" if adapter == "shell" else adapter
        live = {p.session for p in self.tmux.list_panes()}
        for n in range(1, 100):
            candidate = stem if n == 1 else f"{stem}-{n}"
            if naming.base_id(directory, repo, candidate) not in (set(self.sessions) | live):
                return candidate
        return f"{stem}-{now_iso()}"

    async def _supersede(self, adapter_id: str, new_sid: str) -> None:
        """A resumed conversation continues in the new session: the exited record it came from is
        closed (kept a day, sorted last) and its dead pane dropped, so the Org shows one card.

        **Resume carries mail forward** (design §4.10 lifecycle): every entry still inside the
        retention window, read or unread, the exchange tallies and `sends` move to the new record,
        because the conversation they were addressed to is the one continuing — a worker that read
        an `ask`, crashed and was resumed must not lose the thread it was answering. Ids follow the
        move: the old id is rewritten to the new one in the moved entries' `to`, and in every other
        record's pair tallies and pending-`ask` addressees; an `ask` left pending by the exit is
        open again. The old record remembers its successor, so a message addressed to it is
        forwarded there (`rpc_msg`) while an act on it is refused, as on any closed record."""
        new = self.sessions.get(new_sid)
        for other in list(self.sessions.values()):
            if other.id != new_sid and other.adapter_id == adapter_id and other.state == "exited":
                await asyncio.to_thread(self.tmux.kill_session, other.id)
                other.set_state("closed", confidence=other.confidence)
                other.closed_at = now_iso()
                other.superseded_by = new_sid
                if new is not None:
                    self._move_mail(other, new)
                self.store.save(other)
        if new is not None:
            self.store.save(new)

    def _move_mail(self, old: Session, new: Session) -> None:
        now = datetime.now(UTC)
        new.inbox = self._rename([self._copy(e) for e in old.inbox if self._keep(e, now, inbox=True)], old.id, new.id)
        new.outbox = self._rename(
            [self._copy(e) for e in old.outbox if self._keep(e, now, inbox=False)], old.id, new.id
        )
        new.threads = {k.replace(f"pair:{old.id}", f"pair:{new.id}"): t for k, t in old.threads.items()}
        new.sends = list(old.sends)
        # the wake decisions follow the conversation too, so moved mail a wake already covered is
        # not decided (and charged) a second time, and a spent budget is not reset by a resume
        new.mail_decided, new.wakes, new.wake_refilled_at = old.mail_decided, list(old.wakes), old.wake_refilled_at
        old.inbox, old.outbox, old.threads, old.sends = [], [], {}, []
        # **The person inbox's copies follow the move too** (§4.10 "Ids follow the move"; review of
        # PR #245). An `ask` to the person does not expire, so a question the old id put there can
        # outlive the record that sent it — and its `from` is load-bearing in four places: the
        # per-sender depth, the advice line, where a `system` note about it is delivered, and where
        # the person's Reply is addressed. Left naming the old id, the question would be closed
        # `asker_gone` when the superseded record is forgotten a day later, although the
        # conversation it belongs to is still running.
        moved = [e for e in self.person_inbox if e.from_ == old.id]
        for e in moved:
            e.from_ = new.id
        if moved:
            self.person_store.save(self.person_inbox)
        for r in self.sessions.values():
            if r.id in (old.id, new.id):
                continue
            touched = False
            if f"pair:{old.id}" in r.threads:
                r.threads[f"pair:{new.id}"] = r.threads.pop(f"pair:{old.id}")
                touched = True
            pending = [e for e in (*r.inbox, *r.outbox) if old.id in e.pending]
            if pending:  # the addressee that exited is back: its ask is open again, addressed to it
                self._rename(pending, old.id, new.id)
                touched = True
            if touched:
                self.store.save(r)

    @staticmethod
    def _rename(entries: list[MailEntry], old: str, new: str) -> list[MailEntry]:
        """The old id rewritten to the new one in entries the resume carries. `from_` too, on the
        copies this record owns — its **outbox**, where `from_` is this conversation and the home
        reads it to deliver a `system` note about the entry (review of PR #245). A delivered copy
        in someone else's inbox is never passed here and keeps `from` as it was (§4.10)."""
        for e in entries:
            e.to = [new if x == old else x for x in e.to]
            e.copies = [new if x == old else x for x in e.copies]
            e.pending = [x for x in e.pending if x != old]
            if e.from_ == old:
                e.from_ = new
        return entries

    def occupants(self, directory: Path) -> list[str]:
        """Who holds the agent slot for `directory` (design §9 invariant 2): agentorc's own live
        agent sessions, plus live sessions the adapters can see that agentorc did not start
        (a VS Code terminal running `claude` in the checkout, say). Shells never count."""
        directory = Path(directory).resolve()
        # Runs in a thread while the loop goes on writing these maps in place: iterate copies, taken
        # in one step, never the live dicts (review of PR #215).
        mine = list(self.sessions.values())
        ours = {s.adapter_id for s in mine if s.adapter_id}

        def holds(s: Session) -> bool:
            return (
                s.kind == "interactive"
                and s.adapter != "shell"
                and s.state not in ("exited", "closed")
                and Path(s.dir).resolve() == directory
            )

        out = [f"{s.id} ({s.state})" for s in mine if holds(s)]
        # A container node on this machine has the checkout mounted at the same path (design §4.4a
        # "A container node", 3c.4): a record of its over this directory holds the slot too — read
        # from what the node reports, derived from the `container:` entry, never configured — while
        # its link is up. Down, the container is a blip away from dialing back (its records are
        # then repaired by the snapshot) or stopped, and a stopped container's sessions are dead:
        # neither may hold this checkout against a create here. A machine node's `/home/x/repo`
        # is another directory, and is not read.
        if self.mode == "home" and self.remote:
            for host in containers.container_nodes():
                if not (self.links.get(host) or {}).get("up"):
                    continue
                out += [f"{s.id}@{host} ({s.state})" for s in list(self.remote.get(host, {}).values()) if holds(s)]
        for ext in adapters.external_sessions():
            if ext.tool_id and ext.tool_id in ours:
                continue  # that is one of ours, seen through the tool's registry
            if Path(ext.cwd).resolve() == directory:
                out.append(f"{ext.name} ({ext.adapter}, outside agentorc{', ' + ext.status if ext.status else ''})")
        return out

    def conversation_holders(self, adapter_id: str) -> list[str]:
        """Who is driving the tool conversation `adapter_id` right now (TD-012): a live record of
        ours, or a live session outside agentorc whose tool id matches. Two panes on one
        conversation is the failure `resume` must not create; an exited record is superseded
        instead (`_supersede`)."""
        out = [
            f"{s.id} ({s.state})"
            for s in self.sessions.values()
            if s.adapter_id == adapter_id and s.state not in ("exited", "closed")
        ]
        ours = {s.adapter_id for s in self.sessions.values() if s.adapter_id}
        for ext in adapters.external_sessions():
            if ext.tool_id == adapter_id and ext.tool_id not in ours:
                out.append(f"{ext.name} ({ext.adapter}, outside agentorc{', ' + ext.status if ext.status else ''})")
        return out

    async def rpc_occupancy(self, dir: str) -> dict[str, Any]:
        directory = Path(dir).expanduser()
        if not directory.is_dir():
            return {"dir": str(directory), "occupants": [], "git": False}
        occ = await asyncio.to_thread(self.occupants, directory)
        is_git = (directory / ".git").exists() or await asyncio.to_thread(lambda: git_info(directory) is not None)
        return {"dir": str(directory.resolve()), "occupants": occ, "git": bool(is_git)}

    def _start(self, sid: str, cwd: Path, argv: list[str] | None, env: dict[str, str], run_log: Path) -> None:
        self.tmux.ensure_server()
        self.tmux.new_session(sid, cwd, argv, env, logfile=run_log)  # log from the first byte (invariant 3)

    async def rpc_kill(self, id: str) -> dict[str, Any]:
        s = self._get(id)
        await asyncio.to_thread(self.tmux.kill_session, id)
        s.set_state("exited", confidence="scraped")
        s.pane = False  # unlike a natural exit, a kill destroys the pane (TD-023)
        self._killed_at[id] = datetime.now(UTC)  # a tick's older pane list must not revive it (TD-063)
        self.store.save(s)
        await self._push_changes()  # the Focus terminal ends on this delta, not on a retry (TD-029)
        return s.view()

    async def rpc_close(self, id: str) -> dict[str, Any]:
        s = self._get(id)
        await asyncio.to_thread(self.tmux.kill_session, id)
        s.set_state("closed", confidence="scraped")
        s.pane = False
        s.closed_at = now_iso()
        # A `kill` then a `close` before an intervening tick would otherwise strand a `_killed_at`
        # stamp for `CLOSED_KEEP`: the reconcile skips a closed record before it reaches the guard,
        # so nothing else would ever clear it. Harmless — a closed record is never observed either
        # way — but it would make the guard's one-tick bound untrue (review of PR #199).
        self._killed_at.pop(id, None)
        self.store.save(s)
        self._asker_gone(s, self._address(s))  # its open questions to the person close with it (§4.10)
        await self._push_changes()  # the Focus terminal ends on this delta, not on a retry (TD-029)
        return s.view()

    async def rpc_remove(self, id: str) -> None:
        s = self._get(id)
        if s.state not in ("exited", "closed"):
            raise RpcError(f"{id} is {s.state}; kill it first")
        # The dead pane is kept until now (exit code, last screen); without this it would be
        # re-adopted as a nameless shell on the next tick (first-use finding 2026-09-06).
        # one extra `list-panes -a` per remove, a person-driven action: accepted
        pane = (await asyncio.to_thread(self.tmux.main_panes, naming.PREFIX)).get(id)
        await asyncio.to_thread(self.tmux.kill_session, id)
        self._forget(id)
        self._removed[id] = (pane.created if pane else None, time.monotonic())
        await self._push_changes()

    async def rpc_send(
        self, id: str, text: str, wait: bool = False, timeout: float | None = None, caller: Any = None
    ) -> dict | None:
        """Type a prompt. With `wait` (TD-016, design §4.2): return the record once the session has
        started on *this* prompt and settled again (`SETTLED`). A session that is busy queues the
        prompt behind its current turn, so the wait first lets that turn end, then looks for the
        next one to start. Errors: `prompt-stalled` when nothing starts within `SEND_STALL_SECONDS`
        of the moment it could, `timeout` after `timeout` seconds in total, `removed` if the record
        goes away. Nothing is ever re-sent on a guess (design §4.2). `caller` is the envelope's,
        injected by the dispatcher: every send that reaches the pane is recorded on the record as
        `sends`, with who typed it (design §4.10)."""
        s = self._get(id)
        self._refuse_closed(s)
        if s.pending and s.pending.kind in ("permission", "question"):
            raise RpcError(f"{id} has a pending {s.pending.kind}; answer it in the terminal")
        entry = self._record_send(s, caller, text)
        if mail.is_person(caller):
            self._refill(s)
        try:
            await self._submit(id, adapters.get(s.adapter), text)
        except RpcError as e:
            entry.verdict = str(e)
            self.store.save(s)
            raise
        if not wait:
            return None
        end = None if timeout is None else time.monotonic() + timeout

        def left() -> float | None:
            return None if end is None else max(0.0, end - time.monotonic())

        s = self._get(id)
        if s.state != "idle":
            # Busy: the tool queues the text. Wait for the current turn to end; a stop on anything
            # but idle (a question, an exit) is returned as is — the prompt is still queued behind it.
            rev_sent = s.rev
            if not await self._wait_state(id, lambda x: x.state in SETTLED, left()):
                self._raise_not_settled(id, timeout)
            s = self._get(id)
            if s.state != "idle":
                return s.view()
            if s.rev - rev_sent >= 3:
                # working → idle → working → idle inside one poll: the queued turn already ran.
                # Accepted residual: a permission answered *and* the rest of that same turn finishing
                # inside one 0.1 s poll would look the same; a turn does not end that fast.
                return s.view()
        rev_before = s.rev
        # Started: any transition off this idle (a hook's UserPromptSubmit → working, a scraped
        # working, even an exit) within the stall window.
        stall = SEND_STALL_SECONDS if left() is None else min(SEND_STALL_SECONDS, left())
        if not await self._wait_state(id, lambda x: x.rev != rev_before, stall):
            if id not in self.sessions:
                raise RpcError(f"removed: {id} went away while waiting")
            raise RpcError(f"prompt-stalled: {id} showed no activity within {stall:g} s")
        if not await self._wait_state(id, lambda x: x.state in SETTLED and x.rev != rev_before, left()):
            self._raise_not_settled(id, timeout)
        return self._get(id).view()

    @staticmethod
    def _refuse_closed(s: Session) -> None:
        """An act on a closed record is refused (design §4.10 lifecycle) — a resumed conversation's
        old id in particular: only mail is forwarded to the successor, never keystrokes."""
        if s.state == "closed":
            where = f"; it was resumed as {s.superseded_by}" if s.superseded_by else ""
            raise RpcError(f"{s.id} is closed{where}: nothing is typed into a closed session")

    def _record_send(self, s: Session, caller: Any, text: str) -> SendEntry:
        """`sends` (design §4.10): what was typed into this pane and by whom, minted and stamped
        here like a message, bounded to the last `SENDS_KEEP`. Written at the gate — before the
        paste, since a paste that then sticks still reached the pane — and its verdict amended if
        the submit fails."""
        who = PERSON if mail.is_person(caller) else str(caller)
        entry = SendEntry(id="s-" + secrets.token_hex(6), from_=who, at=now_iso(), text=text)
        s.sends = (s.sends + [entry])[-mail.SENDS_KEEP :]
        self.store.save(s)
        return entry

    async def _submit(self, sid: str, adapter: Any, text: str) -> None:
        """Paste, Enter, and confirm the prompt left the composer (TD-027, design §4.2). Only an
        adapter that can read its tool's composer (`composer(tail_raw)`, design §4.3) gets the
        confirmation; the rest get the blind paste + Enter. The paste is given a moment to paint
        (Enter sent while the tool is still taking the paste is swallowed — measured 2026-09-10:
        a paste landing within ~0.1 s of the previous submit lost its Enter every time), then the
        composer must empty within `SUBMIT_SECONDS`; one retry with `C-m`, then `prompt-stuck`.
        Only the Enter is ever re-sent, and only with the text visibly still in the composer —
        never the text (design §4.2). A composer that cannot be read (no composer row, or a failed
        capture — `capture_tail` returns [] then) counts as emptied: no evidence is not evidence of a
        stuck prompt."""
        reader = getattr(adapter, "composer", None)
        await asyncio.to_thread(self.tmux.paste, sid, text)
        if reader is None:
            await asyncio.to_thread(self.tmux.send_enter, sid)
            return

        async def composer() -> str | None:
            return reader(await asyncio.to_thread(self.tmux.capture_tail, sid, COMPOSER_LINES, raw=True))

        if not await self._poll(lambda: composer(), PASTE_SHOW_SECONDS):
            log.info(
                "send %s: the paste did not show in the composer within %gs; pressing Enter anyway",
                sid,
                PASTE_SHOW_SECONDS,
            )
        for key in ("Enter", "C-m"):
            await asyncio.to_thread(self.tmux.send_key, sid, key)
            if await self._poll(lambda: _falsy(composer()), SUBMIT_SECONDS):
                return
            log.warning("send %s: the composer still shows the prompt %gs after %s", sid, SUBMIT_SECONDS, key)
        raise RpcError(f"prompt-stuck: {sid} still shows the prompt in its composer after Enter and C-m")

    async def _poll(self, pred: Any, timeout: float) -> bool:
        """Until `await pred()` is truthy; False on timeout."""
        end = time.monotonic() + timeout
        while True:
            if await pred():
                return True
            if time.monotonic() >= end:
                return False
            await asyncio.sleep(0.1)

    def _raise_not_settled(self, sid: str, timeout: float | None) -> None:
        live = self.sessions.get(sid)
        if live is None:
            raise RpcError(f"removed: {sid} went away while waiting")
        raise RpcError(f"timeout: {sid} is still {live.state} after {timeout:g} s")

    async def _wait_state(self, sid: str, pred: Any, timeout: float | None) -> bool:
        """Poll the record on the loop until `pred(session)` holds; False on timeout (None: no limit)
        or when the record is gone."""
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            s = self.sessions.get(sid)
            if s is None:
                return False
            if pred(s):
                return True
            if end is not None and time.monotonic() >= end:
                return False
            await asyncio.sleep(0.1)

    async def rpc_keys(self, id: str, keys: list[str], caller: Any = None) -> None:
        s = self._get(id)
        self._refuse_closed(s)
        self._record_send(s, caller, " ".join(str(k) for k in keys))
        if mail.is_person(caller):
            self._refill(s)  # keys are how a person answers a menu or a question in the pane
        await asyncio.to_thread(lambda: self.tmux.run("send-keys", "-t", f"={id}:", *keys))

    async def rpc_explain(self, id: str, lines: int = 40) -> dict[str, Any]:
        """Why the session shows the state it does (design §4.2, TD-015): the current screen, the
        record's state and confidence, the screen rule that would fire on it with its evidence, and
        whether that verdict applies (no fresher hook state) or is outranked."""
        s = self._get(id)
        adapter = adapters.get(s.adapter)
        tail = [_clean(t) for t in await asyncio.to_thread(self.tmux.capture_tail, id, lines)]
        explain = getattr(adapter, "explain", None)
        now = datetime.now(UTC)
        out: dict[str, Any] = {
            "id": id,
            "adapter": s.adapter,
            "state": s.state,
            "confidence": s.confidence,
            "pending": s.pending.to_dict() if s.pending else None,
            "since": s.since,
            "last_hook": self._last_hook[id].replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if id in self._last_hook
            else None,
            "tail": tail,
            "match": None,
            "reason": "",
        }
        if explain is None:
            src = "foreground process" if adapter.state_source == "scraped" else "hooks only"
            out["reason"] = f"{s.adapter} has no screen rules; its state comes from {src}"
            return out
        m = explain(tail)
        out["match"] = m.to_dict() if m else None
        if m is None:
            out["reason"] = "no screen rule matched"
        elif self._hook_fresh(id, now):
            out["reason"] = f"rule {m.rule} matched, but a hook reported within {STALL_AFTER} — the hook state wins"
        else:
            out["reason"] = f"rule {m.rule} matched and no fresher hook state exists — applied as scraped"
        return out

    async def rpc_tail(self, id: str, lines: int = 40) -> list[str]:
        self._get(id)
        return await asyncio.to_thread(self.tmux.capture_tail, id, lines)

    async def rpc_seen(self, id: str) -> dict[str, Any]:
        """A person looked at this session (Focus opened, a card control used). The UI reads
        `since > seen_at` on an `idle` record as "finished while you were away" (design §4.2)."""
        s = self._find(id)  # a person may look at another host's session: `seen_at` is the home's
        s.seen_at = now_iso()
        self._save(s)
        await self._push_changes()
        return s.view()

    async def rpc_set_mode(self, id: str, unattended: bool) -> dict[str, Any]:
        s = self._find(id)  # the home's own copy of another host's record too (4a)
        s.unattended = bool(unattended)
        self._save(s)
        return s.view()

    async def rpc_set_stop(
        self, id: str, run_until: str | None = None, wrapup_prompt: str | None = None
    ) -> dict[str, Any]:
        """`ao until <session> <when>` (design §6, TD-026): set or clear a session's stop time.

        Acting, and gated as one: a stop time ends another session's run, which is the same act as
        `kill` with a delay on it. Passing no time clears it — an unattended session with no stop
        time is where this started, so clearing one is a decision worth being able to make out loud
        rather than by restarting the session.
        """
        s = self._find(id)  # the home's own copy of another host's record too (4a)
        when = _stop_time(run_until)  # a malformed time is an error before anything else is judged
        if when and not s.unattended:
            raise RpcError(f"{id} is interactive: a stop time is a policy, and policies leave it alone (§4.2)")
        if when != s.run_until:
            # A *new* time is a new run, so whatever was asked before is spent. Re-confirming the
            # same one is not: it used to reset this unconditionally, so a session already asked to
            # wrap up and sitting inside its grace would be asked again on the next tick — and,
            # because the ask happens before the grace check, touching the stop time repeatedly
            # deferred the forced kill indefinitely. That is the failure TD-026 gap 1 exists to
            # close, and the Focus control (TD-026, PR #147) put a button in front of it whose
            # pre-fill is exactly this value. Found in that PR's review.
            s.wrapup_sent_at = None
        s.run_until = when
        if wrapup_prompt is not None:
            s.wrapup_prompt = str(wrapup_prompt).strip() or None
        self._save(s)
        await self._push_changes()
        return s.view()

    async def rpc_set_grants(
        self, id: str, add: list[str] | None = None, remove: list[str] | None = None
    ) -> dict[str, Any]:
        """`ao grant` / `ao revoke`, the Focus grants chip (design §4.8): edit `capabilities`. Takes
        effect on the target's next call — the gate reads the record, not a cached copy."""
        s = self._find(id)  # the home's own copy of another host's record too (4a)
        adding, removing = _grants(add or []), _grants(remove or [])
        s.capabilities = [g for g in GRANTS if (g in s.capabilities or g in adding) and g not in removing]
        self._save(s)
        await self._push_changes()
        return s.view()

    async def rpc_set_controllers(
        self, id: str, add: list[str] | None = None, remove: list[str] | None = None
    ) -> dict[str, Any]:
        """`ao control <controller> add|remove <session>`, the Focus controllers chip (design §4.8,
        TD-036): edit which sessions may act on this one. Gated on the *target* like `set_grants`,
        so a person always may and a session only if it already controls it — control is handed
        on, never seized; and never at all from a session onto an interactive target (§9
        invariant 5, TD-041 — `_gate` refuses it before this method runs), while a person may hand
        their own session to a controller deliberately. Takes effect on the next call the
        controller makes: the gate reads the record, not a cached copy."""
        s = self._find(id)  # the home's own copy of another host's record too (4a)
        # Stored as the record's own host addresses them (§4.4a, step 4a): bare for that host's
        # sessions, `id@host` for the rest — another host's record keeps its node's form here too.
        adding = [self._to_host(c, s.host) for c in _controllers(add or [])]
        removing = [self._to_host(c, s.host) for c in _controllers(remove or [])]
        if s.id in adding:
            raise RpcError(f"{s.id} cannot be its own controller: it could then drop the ones watching it")
        # One call naming an id in both `add` and `remove` drops it: remove wins, as it already
        # does for grants (`rpc_set_grants`'s `and g not in removing`). Two authority-editing RPCs
        # that disagree on the ambiguous call is how one of them eventually surprises someone, and
        # of the two answers the safe one is the one that takes authority away (review 2026-09-13).
        s.controllers = _controllers([c for c in s.controllers + adding if c not in removing])
        self._save(s)
        await self._push_changes()
        return s.view()

    async def rpc_progress(
        self,
        id: str,
        ref: str = "",
        status: str = "claimed",
        pr: int | None = None,
        why: str | None = None,
        source: str = "declared",
        force: bool = False,
        caller: Any = None,
    ) -> dict[str, Any]:
        """`ao progress claim|done|drop <ref>` (design §4.8): what this session set out to resolve
        and how it went. A report channel is **ungated** — any session may write any record's, the
        Org renders whichever are non-empty — and one reference is one entry, upserted in place.

        `status="none"` is `ao progress none --why` (design §4.9a): no reference and no entry, but
        `out_of_work: {at, why}` on the record. It is the one write on this channel that is not
        open to everyone — only the session itself may make it, declared, with a reason (§9
        invariant 14).

        A declared claim is a **lease** (§4.8, TD-056): refused while another live record holds an
        unexpired declared claim on the same reference, naming the holder; `force` claims anyway and
        the reply carries `lease_overridden`."""
        s = self._find(id)  # a node's session reports here (step 5): the field is the home's
        if status == "none":
            if ref or pr is not None:
                raise RpcError('progress none takes no reference and no PR, only why="<the search that came up empty>"')
            return await self._out_of_work(s, why, source, caller)
        if status not in PROGRESS_STATUSES:
            raise RpcError(f"unknown progress status {status!r}; statuses are: {', '.join(PROGRESS_STATUSES)}, none")
        entry = ProgressEntry(ref=_ref(ref), status=status, pr=_pr(pr), why=why, source=_source(source))
        holder = self._lease_holder(s, entry) if status == "claimed" and entry.source == "declared" else None
        if holder is not None and not force:
            raise RpcError(
                f"{entry.ref} is claimed by {holder['session']} since {holder['at']} (a lease, design §4.8): pick "
                "another reference, or claim it anyway with --force",
                holder=holder,
            )
        applied = s.report_progress(entry)
        if applied and status == "claimed" and entry.source == "declared":
            s.out_of_work = None  # a session that claims something has work again
        out = await self._report(s, applied, entry)
        if holder is not None and applied:
            out["lease_overridden"] = holder
        return out

    def _lease_holder(self, s: Session, entry: ProgressEntry) -> dict[str, str] | None:
        """The other live record holding an unexpired declared claim on `entry.ref`, if any. Read and
        acted on in one loop step, so two claims a moment apart get one grant and one refusal."""
        now = datetime.now(UTC)
        for o in self._graph().values():
            if o.id == s.id or o.state in ("exited", "closed"):
                continue
            for e in o.progress:
                if e.ref == entry.ref and e.status == "claimed" and e.source == "declared":
                    with contextlib.suppress(ValueError):
                        if now - _parse(e.at) < LEASE_TTL:
                            return {"session": o.id, "at": e.at}
        return None

    async def _out_of_work(self, s: Session, why: str | None, source: str, caller: Any) -> dict[str, Any]:
        if _source(source) != "declared":
            raise RpcError("out of work is declared, never derived (design §9 invariant 14)")
        if mail.is_person(caller) or str(caller) != s.id:
            raise RpcError(
                f"only {s.id} may declare itself out of work: it is the session's own word that it searched "
                "(design §9 invariant 14)"
            )
        if not (why or "").strip():
            raise RpcError(
                'ao progress none needs --why "<the search that came up empty>": a declaration without its '
                "reason is refused (design §4.9a)"
            )
        if owed := s.owed():
            # Design §4.10 *Outcomes*: the person answered; what became of it is owed before this
            # session stops. `dropped` is an honest way out, and one line settles each.
            raise RpcError(
                f"{s.id} owes {len(owed)} outcome{'s' if len(owed) != 1 else ''} to the person: report each with "
                f'ao msg person --outcome done|blocked|dropped "<one line>" --for <id> before declaring yourself '
                f"out of work — {', '.join(owed)} (design §4.9a, §4.10 *Outcomes*)",
                owed=owed,
            )
        s.out_of_work = {"at": now_iso(), "why": why.strip()}
        return await self._report(s, True, None)

    async def rpc_doing(self, id: str, text: str = "", clear: bool = False, caller: Any = None) -> dict[str, Any]:
        """`ao doing "<line>"` (design §4.8, the third report channel, TD-074): one line, the
        session's own word for what it is doing now. `doing: {text, at}` on the record — a value
        beside the entry lists, as `out_of_work` is, so the last line replaces the one before and
        `--clear` empties it.

        Ungated like the other channels, and, like `out_of_work`, only the session itself may write
        it (§9 invariant 14): it is *the session says*, and a lead describing a member would be
        second-hand. One line (a newline ends it), control bytes stripped as a tail's are, capped at
        200 characters; a line that cleans to nothing is refused. Nothing derives it, nothing keys
        on it and no wake fires on it — it is shown, never acted on, and an exit leaves it in
        place."""
        s = self._find(id)  # a node's session reports here (step 5): the field is the home's
        if mail.is_person(caller) or str(caller) != s.id:
            raise RpcError(
                f"only {s.id} may say what it is doing: it is the session's own word (design §9 invariant 14)"
            )
        if clear:
            s.doing = None
        else:
            line = _clean(str(text or "").split("\n", 1)[0]).strip()
            if not line:
                raise RpcError('ao doing needs a line: `ao doing "<what you are doing now>"`, or --clear (design §4.8)')
            s.doing = {"text": line, "at": now_iso()}
        self._save(s)
        await self._push_changes()
        return s.view()

    async def rpc_finding(
        self, id: str, ref: str, priority: str | None = None, source: str = "declared"
    ) -> dict[str, Any]:
        """`ao finding <ref> [--priority …]` (design §4.8): a reference this session filed on the
        side. Ungated like `progress`, and upserted by reference the same way."""
        s = self._find(id)  # a node's session reports here (step 5): the field is the home's
        entry = FindingEntry(ref=_ref(ref), priority=priority, source=_source(source))
        return await self._report(s, s.report_finding(entry), entry)

    async def _report(self, s: Session, applied: bool, entry: Any) -> dict[str, Any]:
        """Save and announce a report entry the record accepted. A refused one (§9 invariant 10: a
        `derived` or `scraped` entry over a `declared` one) is not an error — the caller gets the
        record as it stands, with `refused` naming the entry that did not land."""
        if applied:
            self._save(s)
            await self._push_changes()
        out = s.view()
        if not applied:
            out["refused"] = entry.to_dict()
        return out

    # -- mail (design §4.10, TD-052 step 1) ------------------------------------------------------

    async def rpc_msg(
        self,
        text: str,
        to: list[str] | str | None = None,
        kind: str = "note",
        about: str | None = None,
        reply_to: str | None = None,
        bound: float | None = None,
        cites: list[str] | None = None,
        default: str | None = None,
        answers: list[str] | str | None = None,
        answer: Any = None,
        outcome: str | None = None,
        for_: str | None = None,
        thread: str | None = None,
        nonce: str | None = None,
        caller: Any = None,
    ) -> dict[str, Any]:
        """`ao msg <to>… "…" [--kind] [--about] [--reply-to]` (design §4.10): put an attributed
        entry in each addressee's inbox. Nothing is typed anywhere. Gated by §4.10's graph, never
        by invariant 11 — messaging is not acting — and bounded as that section lists: a recipient
        cap on the addressees the sender named, all or nothing across them, a copy to the other
        controllers of the session it is `about` (exempt from both), the exchange bound per thread
        and per pair, the mailbox depth, the body cap, and an `ask`'s wall-clock bound. `bound` is
        the `ask`'s, in seconds, else `mail.ASK_BOUND` — and an `ask` to the person carries none at
        all (§4.10 *What a person is asked*, 2026-09-19): `default` is a `steer`'s, required on it.
        `answers` are the sender's likely answers on a question, and `answer` the zero-based index
        of the one a reply picked (§4.10 *Suggested answers*, 2026-09-20, TD-070). A retry carrying
        the same `nonce` returns the first send's verdict. The reply names what landed, what was
        copied, and what was forwarded to a resumed successor."""
        sender = PERSON if mail.is_person(caller) else str(caller)
        key = (sender, str(nonce)) if nonce else None
        if key and key in self._nonces:
            result, error = self._nonces[key]
            if error is not None:
                raise error
            return dict(result or {})
        try:
            result = await self._msg(
                sender, text, to, kind, about, reply_to, bound, cites, default, answers, answer, outcome, for_, thread
            )
        except RpcError as e:
            if key:
                self._remember_nonce(key, (None, e))
            raise
        if key:
            self._remember_nonce(key, (result, None))
        return result

    def _owing_question(self, sender: str, mid: str) -> MailEntry:
        """The person's copy of `mid`, checked to be the caller's own question that owes an outcome
        (design §4.10 *Outcomes*). Every refusal says which of the five it is, because the remedy
        differs: wait, nothing, ask again, nothing, and it is not yours."""
        held = [e for e in self.person_inbox if e.id == mid]
        if not held or held[0].from_ != sender:
            raise RpcError(
                f"the person inbox holds no question {mid} from {sender}: an outcome names your own question to "
                "the person, by the id `ao msg` printed when it landed (design §4.10 *Outcomes*)"
            )
        e = held[0]
        if e.kind not in ASK_KINDS:
            raise RpcError(f"{mid} is a {e.kind}: only a question to the person is answered, and only one owes back")
        if e.open:
            raise RpcError(f"{mid} has not been answered yet: there is nothing to report on it (design §4.10)")
        if e.outcome:
            raise RpcError(
                f"{mid} is settled — its outcome is recorded as {e.outcome.get('state')}. If more is needed, "
                "ask again on the thread: --thread <id> (design §4.10 *Outcomes*)"
            )
        if e.closed_reason not in ("replied", "go_with_it"):
            raise RpcError(
                f"{mid} closed as {e.closed_reason}: nothing is owed on a question nobody answered (design §4.10)"
            )
        return e

    def _remember_nonce(self, key: tuple[str, str], verdict: Any) -> None:
        self._nonces[key] = verdict
        while len(self._nonces) > mail.NONCES_KEEP:
            self._nonces.popitem(last=False)

    async def _msg(
        self,
        sender: str,
        text: str,
        to: list[str] | str | None,
        kind: str,
        about: str | None,
        reply_to: str | None,
        bound: float | None,
        cites: list[str] | None,
        default: str | None = None,
        answers: list[str] | str | None = None,
        answer: Any = None,
        outcome: str | None = None,
        for_: str | None = None,
        thread: str | None = None,
    ) -> dict[str, Any]:
        """One message, every rule of §4.10 in the order it applies. Long on purpose: the order is
        the design (validate, resolve the thread, forward, gate all-or-nothing, cap, count, land).
        The records are the org's one graph (§4.4a, step 5): an addressee on another host is its
        record here, its inbox the home's copy — landed whether or not its link is up, and said so."""
        records = self._graph()
        if kind not in MAIL_KINDS:
            raise RpcError(f"unknown message kind {kind!r}; kinds are: {', '.join(MAIL_KINDS)}")
        text = str(text or "").strip()
        if not text:
            raise RpcError("a message needs a body")
        if len(text.encode()) > mail.TEXT_CAP:
            raise RpcError(
                f"message body over {mail.TEXT_CAP} bytes: cite a `sends` id or a reference instead (design §4.10)"
            )
        if sender == SYSTEM:
            raise RpcError(
                f"{SYSTEM!r} is the home's own name on a note about your message: no session sends as it (design §4.10)"
            )
        me = records.get(sender) if sender != PERSON else None
        if sender != PERSON and me is None:
            raise RpcError(f"{sender} cannot send mail: this host agent has no record of it (design §4.10)")
        named = [self._addr(x) for x in ([to] if isinstance(to, str) else list(to or [])) if str(x).strip()]
        named = list(dict.fromkeys(named))
        if PERSON in named and sender == PERSON:
            raise RpcError("the person inbox is how a session reaches a person; a person's own note is a board line")
        if SYSTEM in named:
            raise RpcError(
                f"{SYSTEM!r} is the home's own name on a note about your message: it addresses nobody (design §4.10)"
            )
        # -- a `steer` carries the one line it will go with, cleaned and capped as a `doing` line --
        line = _clean(str(default or "").split("\n", 1)[0]).strip()[: mail.DEFAULT_CAP]
        if kind == "steer" and not line:
            raise RpcError(
                'a steer says what it will do unless told otherwise: --default "<the line you will go with>" '
                "(design §4.10)"
            )
        if kind != "steer" and line:
            raise RpcError(f"only a steer carries a default: {kind} says what it says (design §4.10)")
        # -- the sender's likely answers: a field of its own, not counted toward `TEXT_CAP` --------
        # Design §4.10 *Suggested answers* (TD-070). Each is cleaned more strictly than displayed
        # text is (`_clean_answer`); one that cleans to nothing, or that repeats an earlier one
        # exactly (compared after cleaning, case-sensitively), is dropped; a fifth is refused.
        # RPC input is raw JSON from any local process, so the shape is checked and the work bounded
        # before any of it is cleaned: a list of strings, and no more of them than could ever matter
        # (`ANSWERS_MAX` kept plus as many again dropped as blanks or repeats).
        if answers is not None and not isinstance(answers, (str, list)):
            raise RpcError("answers must be a list of lines (design §4.10)")
        offered = [answers] if isinstance(answers, str) else list(answers or [])
        if any(not isinstance(a, str) for a in offered):
            raise RpcError("answers must be a list of lines (design §4.10)")
        if len(offered) > mail.ANSWERS_MAX * 2:
            raise RpcError(f"an ask carries at most four answers: {len(offered)} given (design §4.10)")
        picks: list[str] = []
        for raw in offered:
            one = _clean_answer(raw)
            if one and one not in picks:
                picks.append(one)
        if offered and kind not in ASK_KINDS:
            raise RpcError(f"only a question carries answers: a {kind} says what it says (design §4.10)")
        if len(picks) > mail.ANSWERS_MAX:  # the word below is `mail.ANSWERS_MAX`, which is 4
            raise RpcError(f"an ask carries at most four answers: {len(picks)} given (design §4.10)")
        # -- a reply belongs to its root's thread, and answers an entry the replier holds ----------
        replied: MailEntry | None = None
        copies: list[str] = []
        if kind == "reply" and not reply_to:
            raise RpcError("a reply names the entry it answers: --reply-to <id> (design §4.10)")
        if reply_to:
            held = [e for e in me.inbox if e.id == reply_to] if me is not None else self._person_holds(reply_to)
            if not held:
                raise RpcError(
                    f"{sender} holds no entry {reply_to} in its inbox: a reply names one it was addressed or "
                    f"copied (design §4.10)"
                )
            replied = held[0]
            if replied.from_ == SYSTEM:
                raise RpcError(
                    "a system note reports what happened to your own message; there is nobody to reply to "
                    "(design §4.10)"
                )
            if not named:
                if replied.from_ == PERSON and sender == PERSON:
                    raise RpcError(f"{reply_to} is a person's own message: name the addressee")
                named = [replied.from_]  # a session answering a person answers into the person inbox
            # replies in a copied thread are copied to the same set (design §4.10): the thread's
            # copies — a copy that failed to land included, so the set is the one meant — and its
            # other addressees, so the other lead of a `conflict` sees how it was settled
            same_set = dict.fromkeys([*replied.copies, *replied.copies_failed, *replied.to, replied.from_])
            copies = [x for x in same_set if x not in (sender, PERSON, *named)]
        # -- a reply may *pick* one of the answers the entry it answers carries (§4.10, TD-070) ----
        # **The home checks it**: `answer` must index the `answers` of the entry `reply_to` names
        # and `text` must equal that answer exactly, else the reply is refused — a session can call
        # this RPC directly, and a receiver must not be asked to trust an index the text does not
        # bear out. Everything else about the reply is unchanged: same gate, same tallies, same
        # close (`replied`), same wake.
        picked: int | None = None
        if answer is not None:
            no = "that is not one of the suggested answers"
            if replied is None:
                raise RpcError(f"{no}: an answer picks one on an entry — name it with --reply-to <id> (design §4.10)")
            if isinstance(answer, bool) or not isinstance(answer, int):
                raise RpcError(f"{no}: the index is a whole number, counted from 0 (design §4.10)")
            if not replied.answers:
                raise RpcError(f"{no}: {replied.id} carries no answers at all (design §4.10)")
            if not 0 <= answer < len(replied.answers):
                raise RpcError(f"{no}: {replied.id} carries {len(replied.answers)} of them (design §4.10)")
            if text != replied.answers[answer]:
                raise RpcError(f"{no}: the text of a picked answer is that answer, word for word (design §4.10)")
            picked = answer
        if not named:
            raise RpcError("a message names its addressees: there is no broadcast (design §4.10)")
        if len(named) > mail.RECIPIENT_CAP:
            raise RpcError(
                f"{len(named)} addressees is more than the cap of {mail.RECIPIENT_CAP} (design §4.10: no broadcast)"
            )
        # -- what a person is asked (design §4.10, 2026-09-19): alone, unbounded, never a conflict --
        if PERSON in named:
            if kind == "conflict":
                raise RpcError(
                    "a conflict never names the person: it is put to your controllers, and if they cannot "
                    "settle it, ask the person about it with --kind ask (design §4.10)"
                )
            if kind in ("ask", "steer") and len(named) > 1:
                raise RpcError(
                    f"the person is asked alone: a {kind} naming the person names nobody else — send it to the "
                    f"person on its own, and a note to the others (design §4.10)"
                )
            if kind == "ask" and bound is not None:
                raise RpcError(
                    "an ask to the person carries no bound and never expires: send a steer with --default "
                    "<the line you will go with> --bound <seconds> if you can go on without an answer "
                    "(design §4.10)"
                )
        # -- outcomes: an answer is followed to what became of it (§4.10 *Outcomes*, TD-079) -------
        # `--outcome … --for <id>` settles a question the person answered; `--thread <id>` asks
        # again on the same thread and settles the first as `asked_again`. Both name an entry the
        # **home** verifies — unlike `--about`, which is free text nobody checks.
        settle: MailEntry | None = None
        state = str(outcome or "").strip()
        if state and not for_:
            raise RpcError(
                'an outcome names the question it settles: --for <ask id> (design §4.10 *Outcomes*)'
            )
        if for_ and not state:
            raise RpcError(
                "--for names the question an outcome settles: give it one, --outcome done|blocked|dropped "
                "(design §4.10 *Outcomes*)"
            )
        if state:
            if state not in mail.OUTCOME_STATES:
                raise RpcError(
                    f"unknown outcome {state!r}; an asker reports one of: {', '.join(mail.OUTCOME_STATES)} "
                    "(design §4.10 *Outcomes*)"
                )
            if kind != "note":
                raise RpcError(f"an outcome is a note about how the work went, not a {kind} (design §4.10)")
            if named != [PERSON]:
                raise RpcError(
                    "an outcome is reported to the person who answered: ao msg person --outcome … --for <id>"
                )
            settle = self._owing_question(sender, str(for_))
        if thread:
            if kind not in ("ask", "steer"):
                raise RpcError(
                    f"--thread asks again on a question's own thread: a {kind} settles nothing "
                    "(design §4.10 *Outcomes*)"
                )
            if named != [PERSON]:
                raise RpcError("--thread follows up a question put to the person: name `person` as the addressee")
            settle = self._owing_question(sender, str(thread))
        # -- forwarding: a closed record a live one superseded hands its mail on -------------------
        forwarded: dict[str, str] = {}
        resolved: list[str] = []
        for asked in named:
            sid, seen = asked, {asked}
            while (r := records.get(sid)) is not None and r.state == "closed" and r.superseded_by:
                sid = r.superseded_by
                if sid in seen:
                    break
                seen.add(sid)
            if sid != asked:
                forwarded[asked] = sid  # the id the sender wrote → the record continuing it, however many hops
            if sid not in resolved:
                resolved.append(sid)
        named = resolved
        # -- the gate, per addressee, all or nothing ------------------------------------------------
        for sid in named:
            if sid not in records and sid != PERSON:
                raise RpcError(f"no session {sid}")
            if (reason := mail.message_gate(records, sender, sid, controllers=self._ctl)) is not None:
                raise RpcError(reason)
        cited: list[str] = []
        if kind == "conflict":
            if len(named) < 2:
                raise RpcError("a conflict is an ask to two or more controllers at once (design §4.10)")
            cited = [str(c) for c in (cites or [])]
            known = {e.id for e in me.sends} if me is not None else set()
            if not cited or any(c not in known for c in cited):
                raise RpcError(
                    "a conflict cites the `sends` it cannot reconcile by id — the ids `ao status` prints for this "
                    "session (design §4.10)"
                )
        # -- copies: a controller's mail `about` its member reaches the member's other controllers --
        subject = records.get(self._addr(about)) if about and not reply_to and me is not None else None
        if subject is not None and sender in self._ctl(subject):
            copies = [c for c in self._ctl(subject) if c not in (sender, *named)]
        copies = [c for c in copies if c in records]
        # -- what this message counts as ------------------------------------------------------------
        closes = replied is not None and kind == "reply" and replied.open
        counts = sender != PERSON and not closes  # a person's message is never counted; a first reply is free
        # A reporting note and a follow-up both belong to the **question's own thread** (design
        # §4.10 *Outcomes*): the person reads the answer and what came of it in one place, and the
        # thread's exchange bound counts them where they belong (review of PR #267).
        root = settle.root if settle is not None else (replied.root if replied is not None else "")
        now = datetime.now(UTC)
        if counts and (mail.THREAD_BOUND is not None or mail.PAIR_BOUND is not None):
            self._check_bounds(sender, named, root, now)
        advice = None
        if PERSON in named:
            if kind in ("ask", "steer") and me is not None and mail.OUTCOMES_OWED_MAX is not None:
                # The debt's own bound (design §4.10 *Outcomes*): it never holds the sender's slot
                # in the person-inbox depths — a long night of answered questions must not cost a
                # worker the ability to ask — but an asker that has stopped reporting is stopped.
                owed = [x for x in me.owed() if x != (settle.id if settle is not None else None)]
                if len(owed) >= mail.OUTCOMES_OWED_MAX:
                    raise RpcError(
                        f"you owe {len(owed)} outcomes to the person: report them first "
                        f"(ao msg person --outcome done|blocked|dropped \"<one line>\" --for <id>): "
                        f"{', '.join(owed[: mail.OUTCOMES_OWED_MAX])} (design §4.10 *Outcomes*)",
                        owed=owed,
                    )
            self._check_person_depth(sender)
            if kind == "ask":
                # One line of advice from the home, not a gate — the per-sender depth is the gate
                # (design §4.10 "Which to send is the brief's to teach"): counted before this send.
                held = sum(1 for e in self.person_inbox if e.from_ == sender and e.kind == "ask" and e.open)
                if held >= mail.OPEN_ASK_ADVICE:
                    advice = f"you have {held} open asks to the person: is this one needed, or a steer?"
        if mail.MAILBOX_DEPTH is not None:
            for sid in named:
                if sid != PERSON and records[sid].unread() >= mail.MAILBOX_DEPTH:
                    raise RpcError(
                        f"{sid}'s inbox holds {mail.MAILBOX_DEPTH} unread entries: the send is refused, not dropped "
                        f"(design §4.10)"
                    )
        # -- land it ----------------------------------------------------------------------------------
        mid = "m-" + secrets.token_hex(6)
        root = root or mid
        at = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        entry = MailEntry(
            id=mid, from_=sender, to=list(named), at=at, kind=kind, text=text, about=about, reply_to=reply_to, root=root
        )
        entry.cites = cited
        entry.default = line or None
        entry.answers = list(picks)  # data the sender proposed, on the envelope (§4.10, TD-070)
        entry.answer = picked
        entry.team = (me.team or None) if me is not None else None  # the envelope carries its sender's team (§4.10)
        if kind in ASK_KINDS and not (kind == "ask" and PERSON in named):
            # `bound` is None exactly when the addressee is the person and the kind is `ask`: that
            # one never expires, and the person inbox's depths are what bound it instead (§4.10).
            span = timedelta(seconds=float(bound)) if bound is not None else mail.ASK_BOUND
            entry.bound = (now + span).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        landed: list[str] = []
        failed: list[str] = []
        for sid in copies:
            if mail.MAILBOX_DEPTH is not None and records[sid].unread() >= mail.MAILBOX_DEPTH:
                failed.append(sid)  # a copy never sinks a send: dropped and recorded (design §4.10)
            else:
                landed.append(sid)
        entry.copies, entry.copies_failed = landed, failed
        touched: list[Session] = []
        for sid in (*named, *landed):
            if sid == PERSON:
                self.person_inbox.append(self._copy(entry))
                self.person_store.save(self.person_inbox)
                continue
            r = records[sid]
            r.inbox.append(self._copy(entry))
            touched.append(r)
        if me is not None:
            me.outbox.append(self._copy(entry))
            touched.append(me)
        if counts:
            for r in touched:
                r.threads.setdefault(root, Tally()).count += 1
            if replied is None and me is not None:  # replying to nothing: counted under the pair too
                for sid in named:
                    if sid == PERSON:
                        continue  # the person inbox keeps no tally: its depths bound it instead
                    for t in (self._pair(me, sid, now), self._pair(records[sid], sender, now)):
                        t.at.append(at)
                        t.count = len(t.at)  # the window's count, kept as a field so pruning cannot reset it
        if closes and replied is not None:
            self._mark(replied.id, closed_by=mid, closed_at=at, closed_reason="replied")
        if settle is not None:
            # Written on every copy, as a close is: the person's Inbox lists it under the question,
            # and the asker's own card stops saying it owes one. `by` is this entry — an ordinary
            # note of the person inbox, listed under its question and pruned as any FYI entry is.
            self._mark(
                settle.id,
                outcome={
                    "state": state or "asked_again",
                    "text": _clean(text)[: mail.DEFAULT_CAP],
                    "at": at,
                    "by": mid,
                },
            )
        if sender == PERSON and reply_to:
            # A person's message into a thread resets it (design §4.10): tally and `bound_hit`
            # cleared on every record holding it, so the sessions may reply to the ruling.
            for r in records.values():
                if root in r.threads:
                    r.threads[root] = Tally()
                    if r not in touched:
                        touched.append(r)
        if sender == PERSON:
            for sid in named:  # a person's message, reply or not, refills each addressee's budget
                if sid != PERSON:
                    self._refill(records[sid])
        for r in touched:
            self._save(r)
        await self._push_changes()
        now = datetime.now(UTC)
        return {
            # exhaustion is visible to the sender (design §4.10): the mail landed, and it wakes nobody
            "wake_budget_spent": [
                sid for sid in (*named, *landed) if sid != PERSON and mail.wake_budget_spent(records[sid], now)
            ],
            # landed at the home while its host's link is down (§4.4a "When the recipient's host is
            # unreachable"): nothing waits anywhere but the mailbox, and the sender is told
            "unreachable": [
                sid
                for sid in (*named, *landed)
                if sid != PERSON
                and records[sid].host != self.host
                and not (self.links.get(records[sid].host) or {}).get("up")
            ],
            "entry": entry.to_dict(),
            "delivered": list(named),
            "copies": landed,
            "copies_failed": failed,
            "forwarded": forwarded,
            "closed": replied.id if closes and replied is not None else None,
            # advice, not a refusal: the id comes back either way (design §4.10)
            "advice": advice,
        }

    @staticmethod
    def _copy(entry: MailEntry) -> MailEntry:
        """A record's own copy of an entry: same values, no shared lists."""
        return replace(
            entry,
            to=list(entry.to),
            copies=list(entry.copies),
            copies_failed=list(entry.copies_failed),
            pending=list(entry.pending),
            cites=list(entry.cites),
            # every addressee's copy carries the answers — a `conflict` is read and picked between
            # sessions, so each controller's copy must hold them (§4.10 *Suggested answers*)
            answers=list(entry.answers),
        )

    def _pair(self, r: Session, other: str, now: datetime) -> Tally:
        """The pair tally `r` keeps for `other`, its window rolled forward: entries older than
        `PAIR_WINDOW` fall out, and `count` is what is left."""
        t = r.threads.setdefault(f"pair:{other}", Tally())
        cutoff = now - mail.PAIR_WINDOW
        t.at = [x for x in t.at if _parse(x) >= cutoff]
        t.count = len(t.at)
        return t

    def _check_bounds(self, sender: str, named: list[str], root: str, now: datetime) -> None:
        """A send is refused when the sender's tally, or any named addressee's, is at the bound —
        never a copy recipient's — and `bound_hit` is written on every record holding the thread
        so the other side learns the exchange stopped (design §4.10 "A bounded exchange")."""
        records = self._graph()
        me = records[sender]
        if root:
            limit = mail.THREAD_BOUND
            if limit is None:
                return
            at_bound = [
                sid
                for sid in (sender, *named)
                if sid != PERSON and records[sid].threads.get(root, Tally()).count >= limit
            ]
            if at_bound:
                for r in records.values():
                    if root in r.threads:
                        r.threads[root].bound_hit = True
                        self._save(r)
                raise RpcError(
                    f"thread {root} is at its bound of {limit} entries ({', '.join(at_bound)}): the send is "
                    f"refused — write the user_attention.md line yourself, with the thread attached (design §4.10)"
                )
            return
        limit = mail.PAIR_BOUND
        if limit is None:
            return
        for sid in named:
            if sid == PERSON:
                continue
            mine, theirs = self._pair(me, sid, now), self._pair(records[sid], sender, now)
            if mine.count >= limit or theirs.count >= limit:
                mine.bound_hit = theirs.bound_hit = True
                self._save(me)
                self._save(records[sid])
                raise RpcError(
                    f"{sender} and {sid} have exchanged {limit} messages replying to nothing inside "
                    f"{mail.PAIR_WINDOW}: the send is refused — write the user_attention.md line yourself "
                    f"(design §4.10)"
                )

    def _person_holds(self, msg_id: str) -> list[MailEntry]:
        """Where a person's `--reply-to` looks: the person inbox first (a session's message to the
        person), then every session's copies (a person answering from a session's Inbox panel)."""
        return [e for e in self.person_inbox if e.id == msg_id] + [
            e for r in self._graph().values() for e in r.holds(msg_id)
        ]

    def _check_person_depth(self, sender: str) -> None:
        """The person inbox's depth and per-sender depth (design §4.10): it fills exactly when the
        person has been away, so the refusal is a redirect to the channel with a `Due:` date.

        From 2026-09-19 (TD-069) they count **every entry that is unread or is an open `ask` or
        `steer`** — one set, each entry once — so reading the page frees no slot an unanswered
        question still holds, and one worker cannot fill the Inbox with asks that never lapse."""
        counted = [e for e in self.person_inbox if not e.read_at or e.open]
        full = None
        if mail.PERSON_INBOX_DEPTH is not None and len(counted) >= mail.PERSON_INBOX_DEPTH:
            full = f"the person inbox holds {mail.PERSON_INBOX_DEPTH} entries unread or unanswered"
        elif mail.PERSON_SENDER_DEPTH is not None and (
            sum(1 for e in counted if e.from_ == sender) >= mail.PERSON_SENDER_DEPTH
        ):
            full = f"the person inbox holds {mail.PERSON_SENDER_DEPTH} entries unread or unanswered from {sender}"
        if full:
            raise RpcError(
                f"{full}: the person is away — write the line on user_attention.md with a Due: date, "
                f"the channel that reaches an absent person (design §4.10)"
            )

    def _mark(self, msg_id: str, **fields: Any) -> None:
        """Write the same fact on every copy of one message, so both cards show it: the home is
        the one writer (design §4.4a). `pending=<id>` appends to the list; anything else is set."""
        for r in self._graph().values():
            copies = r.holds(msg_id)
            if not copies:
                continue
            for e in copies:
                for k, v in fields.items():
                    if k == "pending":
                        if v not in e.pending:
                            e.pending.append(v)
                    else:
                        setattr(e, k, v)
            self._save(r)
        mine = [e for e in self.person_inbox if e.id == msg_id]
        for e in mine:
            for k, v in fields.items():
                if k == "pending":
                    if v not in e.pending:
                        e.pending.append(v)
                else:
                    setattr(e, k, v)
        if mine:
            self.person_store.save(self.person_inbox)

    def _close_entry(self, msg_id: str, reason: str, at: str) -> None:
        """Design §4.10 "One way of being closed": `closed_reason` is set whenever an entry closes,
        by whatever path, and the fields that existed before it are kept and still written —
        `expired` sets `expired_at` as today, and `lapsed`, `declined`, `go_with_it` and
        `asker_gone` set `closed_at` alone. (`replied` is the send path's, which writes `closed_by`
        and `closed_at` with the reply that answered.)"""
        if reason == "expired":
            self._mark(msg_id, expired_at=at, closed_reason=reason)
        else:
            self._mark(msg_id, closed_at=at, closed_reason=reason)

    def _system_note(self, to: str, text: str, *, wake: str = "note") -> None:
        """A `note` from `system` written **straight into the sender's mailbox** (design §4.10 "How
        the sender hears that one closed without a reply"): it does not pass through the send path,
        so no gate, no tally and no depth sees it, and no session can send as `system`. It reports
        what happened to the reader's own message and is never an instruction; `--reply-to` naming
        one is refused.

        `wake` is which of the section's three rules applies:

        - `person` — a decline, a *Go with it* or a **pause**, the three by which a person releases
          a sender that may be blocked in `ao wait`: they wake as a person's `reply` does and
          **refill** the budget;
        - `note` — a **resume**, ordinary: it wakes within the budget like any `note`;
        - `uncharged` — a **lapse**: outside the budget, neither spending nor refilling it, so a
          spent budget cannot hold a sender past the bound it set itself. It is carried on the note
          (`MailEntry.uncharged`) rather than beside the records, so it survives the two things
          that happen between a lapse and the wake it earns: a **resume**, which moves the note to
          the new record, and a host-agent **restart**, which reloads it (review of PR #245).

        A sender that has since been resumed is followed to its successor, as mail addressed to a
        superseded record is (§4.10 lifecycle): the note is about the conversation, not the id."""
        entry = MailEntry(id="m-" + secrets.token_hex(6), from_=SYSTEM, to=[to], at=now_iso(), kind="note", text=text)
        entry.uncharged = wake == "uncharged"
        if to == PERSON:
            self.person_inbox.append(entry)
            self.person_store.save(self.person_inbox)
            return
        records = self._graph()
        sid, seen = to, {to}
        while (r := records.get(sid)) is not None and r.state == "closed" and r.superseded_by:
            sid = r.superseded_by
            if sid in seen:
                break
            seen.add(sid)
        r = records.get(sid)
        if r is None:
            return  # the sender's record is gone: there is nobody left to tell
        entry.to = [sid]
        r.inbox.append(entry)
        if wake == "person":
            self._refill(r)  # saves the record and pokes the waits
        else:
            self._save(r)
            self._poke_waits()

    async def rpc_inbox(self, id: str | None = None, unread: bool = False, caller: Any = None) -> dict[str, Any]:
        """`ao inbox [--unread]` (design §4.10): a session reads its own inbox and nobody else's;
        that read — and nothing else — sets `read_at` (lifecycle stage 2: delivered into a turn).
        A person (no caller) reads any session's inbox, as the Inbox panel does, and sets nothing:
        a person is not the session. A person naming no session reads the org's person inbox, and
        that read sets nothing either. Each entry says whether its sender is one of the reader's
        controllers, a person, or neither — the rule stated where the mail is read."""
        if mail.is_person(caller):
            if not id or id == PERSON:
                held = [e for e in self.person_inbox if not (unread and e.read_at)]
                return {
                    "id": PERSON,
                    "entries": [
                        {
                            **e.to_dict(),
                            "from_role": mail.from_role(self._graph(), PERSON, e.from_, controllers=self._ctl),
                        }
                        for e in held
                    ],
                    "threads": {},
                    "sends": [],
                    "unread": sum(1 for e in self.person_inbox if not e.read_at),
                }
            s = self._find(self._addr(id))  # another host's too: the mailbox is the home's (step 5)
            mark = False
        else:
            me = self._addr(caller)
            if id and self._addr(id) != me:
                raise RpcError(f"{me} cannot read {id}'s inbox: nobody reads another session's inbox (design §4.10)")
            s = self._find(me)
            mark = True
        entries = [e for e in s.inbox if not (unread and e.read_at)]
        if mark and any(not e.read_at for e in entries):
            at = now_iso()
            for e in entries:
                e.read_at = e.read_at or at
            self._save(s)
            await self._push_changes()
        who = self._address(s)
        return {
            "id": who,
            "entries": [
                {**e.to_dict(), "from_role": mail.from_role(self._graph(), who, e.from_, controllers=self._ctl)}
                for e in entries
            ],
            "threads": {k: t.to_dict() for k, t in s.threads.items()},
            "sends": [e.to_dict() for e in s.sends[-3:]],
            "unread": s.unread(),
        }

    async def rpc_inbox_delete(self, msg: str, id: str | None = None, caller: Any = None) -> dict[str, Any]:
        """The Inbox panel's delete (design §4.10 lifecycle, §4.5a): a person removes one entry
        from one session's inbox — that record's copy only, so the sender's and any other
        addressee's copies stay, and a thread stays one thread on their side. Refused to every
        session, itself included: a session's inbox is read-only to it through the RPCs, and an
        entry leaves outside its lifecycle only with its record or by a person's hand. Naming no
        session (or `person`) deletes from the org's person inbox — the top bar's delete — and the
        sender's copy stays there too.

        **Deleting is declining, and nothing vanishes at once** (design §4.10, 2026-09-19): in the
        person inbox, deleting an *open* `ask` or `steer` closes it `declined` — a deletion is an
        answer, and silence is not — and the entry stays for the retention window like any closed
        one; the asker is told by a `system` note that wakes it as a person's reply does. A `note`,
        or anything already closed, is removed outright, as it always was."""
        if not mail.is_person(caller):
            raise RpcError(
                f"{caller} cannot delete mail: an entry is deleted only by a person, in the Inbox panel (design §4.10)"
            )
        if not id or id == PERSON:
            held = [e for e in self.person_inbox if e.id == msg]
            if not held:
                raise RpcError(f"the person inbox holds no entry {msg}")
            if held[0].open:
                e = held[0]
                self._close_entry(msg, "declined", now_iso())
                self._system_note(e.from_, f"{e.kind} {msg} declined by the person", wake="person")
                await self._push_changes()
                return {
                    "id": PERSON,
                    "deleted": msg,
                    "declined": True,
                    "unread": sum(1 for x in self.person_inbox if not x.read_at),
                }
            kept = [e for e in self.person_inbox if e.id != msg]
            self.person_inbox = kept
            self.person_store.save(kept)
            return {"id": PERSON, "deleted": msg, "declined": False, "unread": sum(1 for e in kept if not e.read_at)}
        s = self._find(self._addr(id))
        kept = [e for e in s.inbox if e.id != msg]
        if len(kept) == len(s.inbox):
            raise RpcError(f"{s.id}'s inbox holds no entry {msg}")
        s.inbox = kept
        _prune_tallies(s)  # a delete is the other way an entry leaves (review of PR #214)
        self._save(s)
        await self._push_changes()
        return {"id": self._address(s), "deleted": msg, "unread": s.unread()}

    # -- the person's own bookkeeping on their inbox (design §4.10, TD-069 step 0) ----------------
    # Each is refused to every session exactly as `inbox_delete` is, and each acts on the org's
    # person inbox: a snooze, a pause and a *Go with it* are the person's, and no session has them.

    def _person_entry(self, msg: str, caller: Any, what: str) -> MailEntry:
        if not mail.is_person(caller):
            raise RpcError(
                f"{caller} cannot {what} mail: it is the person's own bookkeeping on their inbox (design §4.10)"
            )
        held = [e for e in self.person_inbox if e.id == msg]
        if not held:
            raise RpcError(f"the person inbox holds no entry {msg}")
        return held[0]

    async def rpc_inbox_snooze(self, msg: str, until: str | None = None, caller: Any = None) -> dict[str, Any]:
        """**Snooze** (design §4.10, TD-069): `snoozed_until` on a person-inbox entry, set by this
        RPC and cleared by it with no `until`. A snooze is the person's own bookkeeping, as editing
        a `Due:` date is, and the sender is not told. It persists with the person inbox and affects
        **the Inbox page only** — the entry leaves its section and the page's count until that time
        — and nothing else: it is still unread if it was, it still occupies the depths, and a
        snoozed `ask` stays open. A `steer` has **Pause** instead, so it is refused one: snooze
        hides a row while its clock runs, pause stops the clock, and both on one row invite the
        wrong press."""
        e = self._person_entry(msg, caller, "snooze")
        if e.kind == "steer":
            raise RpcError(f"{msg} is a steer: it has Pause, which stops its clock, and no Snooze (design §4.10)")
        self._mark(msg, snoozed_until=str(until) if until else None)
        return {"id": PERSON, "msg": msg, "snoozed_until": e.snoozed_until}

    async def rpc_inbox_pause(self, msg: str, caller: Any = None) -> dict[str, Any]:
        """**Pause** (design §4.10, TD-069): on a `steer` in the person inbox — *I want to answer
        this; do not go on without me*. `paused_at` stops the bound running (the sweep skips the
        entry outright, whatever `bound` reads), the sender is told by a `system` note that wakes it
        as a person's reply does, so it turns to other work instead of waiting out a clock that has
        stopped, and the entry is counted while paused: a preference has become something a session
        is held on. Only a `steer` can be paused — an `ask` to the person has no clock — and a
        `steer` addressed to a session cannot be: the pause is the person's."""
        e = self._person_entry(msg, caller, "pause")
        if e.kind != "steer":
            raise RpcError(f"only a steer can be paused: {msg} is a {e.kind}, and has no clock to stop (design §4.10)")
        if not e.open:
            raise RpcError(f"{msg} is closed ({e.closed_reason}): there is no clock left to stop (design §4.10)")
        if e.paused_at:
            raise RpcError(f"{msg} is already paused")
        self._mark(msg, paused_at=now_iso())
        self._system_note(e.from_, f"steer {msg} paused by the person: do not take your default yet", wake="person")
        await self._push_changes()
        return {"id": PERSON, "msg": msg, "paused_at": e.paused_at, "bound": e.bound}

    async def rpc_inbox_resume(self, msg: str, caller: Any = None) -> dict[str, Any]:
        """**Resume** (design §4.10, TD-069): moves `bound` later by the time it was held and then
        clears `paused_at`, **in one step**, so the sweep never sees a resumed entry with its old
        bound — what was left is what is left — and the sender is told again. That note is an
        ordinary one: it only says the clock runs again and what is left, so it wakes within the
        wake budget like any `note`."""
        e = self._person_entry(msg, caller, "resume")
        if not e.paused_at:
            raise RpcError(f"{msg} is not paused")
        held = datetime.now(UTC) - _parse(e.paused_at)
        bound = (_parse(e.bound) + held).replace(microsecond=0).isoformat().replace("+00:00", "Z") if e.bound else None
        self._mark(msg, bound=bound, paused_at=None)
        self._system_note(e.from_, f"steer {msg} resumed by the person: the clock runs again, until {bound}")
        await self._push_changes()
        return {"id": PERSON, "msg": msg, "paused_at": None, "bound": bound}

    async def rpc_inbox_go_with_it(self, msg: str, caller: Any = None) -> dict[str, Any]:
        """**Go with it** (design §4.5a **Inbox**, §4.10): closes a `steer` now —
        `closed_reason: go_with_it`, a fixed outcome and not text for the sender to weigh — so the
        sender need not wait out the bound; doing nothing would let it lapse to the same end. The
        sender is told by a `system` note that wakes it as a person's reply does. It closes a
        paused `steer` as it closes a running one."""
        e = self._person_entry(msg, caller, "answer")
        if e.kind != "steer":
            raise RpcError(f"Go with it answers a steer, which carries the default: {msg} is a {e.kind} (§4.10)")
        if not e.open:
            raise RpcError(f"{msg} is already closed ({e.closed_reason})")
        self._close_entry(msg, "go_with_it", now_iso())
        self._system_note(e.from_, f"steer {msg} — the person says: go with your default", wake="person")
        await self._push_changes()
        return {"id": PERSON, "msg": msg, "closed_reason": "go_with_it"}

    async def _sweep_mail(self, now: datetime) -> None:
        """Once a tick: an `ask` past its bound expires on every copy and a `steer` past its bound
        **lapses**; an addressee that exited leaves the `ask`s addressed to it pending, a closed one
        expires them (design §4.10 lifecycle); read entries past retention are pruned, open asks
        exempt. Two things a `steer` does differently (§4.10 *What a person is asked*): its bound
        runs whatever becomes of the addressee — an exit leaves it no `pending` and a close expires
        nothing, it lapses on time — and while the person has **paused** it the sweep skips it
        outright, whatever `bound` reads. An `ask` to the person carries no bound at all, so nothing
        here ever reaches it."""
        stamp = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        for r in list(self._graph().values()):
            for e in list(r.inbox):
                if not e.open or e.paused_at:
                    continue
                if e.bound and _parse(e.bound) <= now:
                    self._lapse_or_expire(e, stamp)
                elif e.kind == "steer":
                    continue  # the sender goes on: nothing the addressee does closes it early
                elif r.state == "closed":
                    self._close_entry(e.id, "expired", stamp)
                elif r.state == "exited" and r.id not in e.pending:
                    self._mark(e.id, pending=r.id)
            for e in list(r.outbox):
                if e.open and not e.paused_at and e.bound and _parse(e.bound) <= now:
                    self._lapse_or_expire(e, stamp)
        for e in list(self.person_inbox):  # a `steer` to the person lapses on its bound; an `ask` has none
            if e.open and not e.paused_at and e.bound and _parse(e.bound) <= now:
                self._lapse_or_expire(e, stamp)
        if mail.MAIL_RETENTION is None:
            return
        kept = [e for e in self.person_inbox if self._keep(e, now, inbox=True)]
        if len(kept) != len(self.person_inbox):
            self.person_inbox = kept
            self.person_store.save(kept)
        for r in self._graph().values():
            inbox = [e for e in r.inbox if self._keep(e, now, inbox=True)]
            outbox = [e for e in r.outbox if self._keep(e, now, inbox=False)]
            if len(inbox) != len(r.inbox) or len(outbox) != len(r.outbox):
                r.inbox, r.outbox = inbox, outbox
                _prune_tallies(r)
                self._save(r)

    def _lapse_or_expire(self, e: MailEntry, stamp: str) -> None:
        """A bound that ran out (design §4.10): a `steer` **lapses** — `closed_reason: lapsed`,
        never `expired_at`, because nothing failed — and the sender is told by a `system` note that
        wakes it **uncharged**, so a spent budget cannot hold it past the bound it set itself. An
        `ask` or a `conflict` expires, as it always has."""
        if e.kind == "steer":
            self._close_entry(e.id, "lapsed", stamp)
            self._system_note(e.from_, f"steer {e.id} lapsed: go with your default", wake="uncharged")
        else:
            self._close_entry(e.id, "expired", stamp)

    @staticmethod
    def _keep(e: MailEntry, now: datetime, *, inbox: bool) -> bool:
        """Lifecycle stage 3 (design §4.10): a read entry is kept for the retention window from
        `read_at` — or, for an `ask`, from when it closed or expired — and an open `ask` is never
        pruned. An unread inbox entry never ages out. The sender's copy runs from `at`."""
        if e.open or e.owes or mail.MAIL_RETENTION is None:
            # `owes`: a question that was answered and not reported back is kept until it is
            # (design §4.10 *Outcomes*) — the follow-up `--thread` names it, and the person's
            # Inbox lists it under *Waiting on them*, so pruning it would strand both.
            return True
        since = e.expired_at or e.closed_at or (e.read_at if inbox else e.at)
        if since is None:
            return True
        return _parse(since) + mail.MAIL_RETENTION > now

    # -- waking (design §4.8 "Waking a lead", §4.10 "The host agent decides each wake") ----------

    async def rpc_wait(
        self, timeout: float = 600.0, scope: str = "controlled", only_host: str | None = None, caller: Any = None
    ) -> dict[str, Any]:
        """`ao wait [--timeout N] [--scope controlled|all]` (TD-049, moved here by TD-052 step 3):
        block until something in the caller's scope changes, new mail wakes it, or the timeout
        passes — and **that timeout is the fallback poll**.

        A read, never gated (§9 invariant 11). The snapshot is this agent's own records, complete
        by construction, compared against the per-caller cursor under `waits/` exactly as the CLI
        did: an unreadable cursor wakes on everything in scope, a first wait records and wakes on
        nothing. Mail's half of the cursor is the caller's `mail_decided` watermark, on its record:
        while it is blocked here the caller is *reachable*, so every pass — each change, and each
        tick — takes the wake decision (`_decide_wake`), which may return the wait for mail. A
        member's change returns at once, carrying any undecided mail with it for free.

        Returns `{"changed": [records], "mail": [headers], "wake": decision | None}`. The mail is
        headers only — id, from, kind, about, at, the sender's role — because `read_at` means
        *`ao inbox` printed it* and nothing else (§4.10 lifecycle)."""
        if scope not in waits.SCOPES:
            raise RpcError(f"unknown scope {scope!r}; scopes are: {', '.join(waits.SCOPES)}")
        who = None if mail.is_person(caller) else self._addr(caller)
        before = waits.read_cursor(who)
        cursor: dict[str, str] | None = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(float(timeout), 0.0)
        me = _Wait(who)
        self._waits.add(me)
        try:
            while True:
                me.poke.clear()
                views = self._views()
                if only_host:  # a person over a link watches that node's records only (§4.4a)
                    views = [v for v in views if v.get("host") == only_host]
                changed, cursor = waits.wake_changes(before, waits.wait_scope(views, who, scope))
                s = self._graph().get(who) if who is not None else None  # a node's session waits here too (step 5)
                wake = self._decide_wake(s, member_change=bool(changed)) if s is not None else None
                if changed or (wake is not None and wake["cause"] == "mail"):
                    covered = wake["entries"] if wake is not None else []
                    return {
                        "changed": changed,
                        "mail": [self._header(s, e) for e in covered] if s is not None else [],
                        "wake": {k: v for k, v in wake.items() if k != "entries"} if wake is not None else None,
                    }
                left = deadline - loop.time()
                if left <= 0:
                    return {"changed": [], "mail": [], "wake": None}
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(me.poke.wait(), left)
        finally:
            # Discarded before anything else can run: a cancelled wait (its connection closed) is
            # gone by the time the next tick decides. The cursor is written on every way out —
            # it holds only what this wait compared and found unchanged, or what it returned.
            self._waits.discard(me)
            if cursor is not None:
                waits.write_cursor(who, cursor)

    def _header(self, s: Session, e: MailEntry) -> dict[str, Any]:
        return {
            "id": e.id,
            "from": e.from_,
            "from_role": mail.from_role(self._graph(), self._address(s), e.from_, controllers=self._ctl),
            "kind": e.kind,
            "about": e.about,
            "at": e.at,
        }

    def _poke_waits(self) -> None:
        for w in self._waits:
            w.poke.set()

    def blocked_in_wait(self, sid: str) -> bool:
        """Reachable in step 3's sense: some connection holds a `wait` for this session now."""
        return any(w.caller == sid for w in self._waits)

    def _decide_wake(self, s: Session, *, member_change: bool) -> dict[str, Any] | None:
        """The one wake decision (design §4.10), taken when `s` is reachable — blocked in `wait`
        here; step 7's doorbell calls it at a hook-confirmed `idle`. Looks at the unread mail past
        the `mail_decided` watermark:

        - none, or `s` is a person's session (never woken by mail) → None, nothing recorded;
        - a member's change is waking it anyway → a **free** wake: recorded `charged: False`,
          watermark advanced over all of it;
        - one of the undecided entries is a note the home marked `uncharged` — a `steer` of its own
          **lapsed** → a free wake, free in the same sense and for the same reason it is not a
          refill: it is the home's clock, not another session's message (design §4.10), so a spent
          budget cannot hold a sender past its own bound. The mark is on the note, so it survives a
          resume and a restart between the lapse and the moment the session is next reachable;
        - the budget holds → one unit spent however many entries it covers, `charged: True`,
          watermark advanced;
        - the budget is spent → None: the mail has landed, nothing wakes, **the watermark stays**,
          so the same mail is still undecided when the window refills.

        Returns the decision as recorded, plus the `entries` it covered."""
        if not mail.mail_wakes(s):
            return None
        fresh = mail.undecided_mail(s)
        if not fresh:
            return None
        now = datetime.now(UTC)
        free = member_change or any(e.uncharged for e in fresh)
        if not free and mail.wake_budget_spent(s, now):
            return None
        decision = {
            "at": now.isoformat(timespec="microseconds"),
            "cause": "member" if member_change else "mail",
            "charged": not free,
            "covered": len(fresh),
        }
        s.wakes = (s.wakes + [decision])[-mail.WAKES_KEEP :]
        s.mail_decided = {"id": fresh[-1].id, "at": fresh[-1].at}
        self._save(s)
        return {**decision, "entries": fresh}

    def _refill(self, s: Session | None) -> None:
        """A person's act toward `s` restores its wake budget in full (design §4.10 "Time and a
        person restore it"): a send or keys with no caller, a person's message, a decided
        permission. Never a session's traffic, never a person looking (`seen`, a panel read)."""
        if s is None:
            return
        s.wake_refilled_at = datetime.now(UTC).isoformat(timespec="microseconds")
        self._save(s)
        self._poke_waits()

    async def rpc_hook(self, session: str, **event: Any) -> dict[str, Any] | None:
        """Called by an adapter's hook script. A `permission` event blocks until answered or timed out."""
        if event.get("kind") == "permission":
            return await self._await_permission(session, event)
        self._apply_event(session, event)
        await self._push_changes()
        return None

    async def _await_permission(self, session: str, event: dict[str, Any]) -> dict[str, Any] | None:
        s = self.sessions.get(session)
        if s is None:
            return None
        self._last_hook[session] = datetime.now(UTC)
        wait = float(event.get("wait_seconds") or 600)
        deadline = (datetime.now(UTC) + timedelta(seconds=wait)).replace(microsecond=0)
        tool_use_id = event.get("tool_use_id") or f"{session}:{now_iso()}"
        pending = Pending(
            kind="permission",
            text=event.get("text", ""),
            deadline=deadline.isoformat().replace("+00:00", "Z"),
            tool_use_id=tool_use_id,
        )
        s.set_state("needs-you", confidence="hook", pending=pending)
        self.store.save(s)
        # Register the waiter BEFORE the first await: a client reacting to the push must find it.
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        key = (session, tool_use_id)
        self._waiters[key] = fut
        await self._push_changes()
        try:
            return await asyncio.wait_for(fut, timeout=wait)
        except TimeoutError:
            # Fell through to the terminal dialog: the decision now lives there (design §4.2).
            s.set_state("needs-you", confidence="hook", pending=Pending(kind="question", text=pending.text))
            self.store.save(s)
            await self._push_changes()
            return None
        finally:
            self._waiters.pop(key, None)

    async def rpc_decide(self, id: str, tool_use_id: str, behavior: str, reason: str | None = None) -> None:
        s = self._get(id)
        fut = self._waiters.get((id, tool_use_id))
        if fut is None or fut.done():
            raise RpcError("no pending permission with that id (answered, timed out, or in the terminal)")
        if behavior not in ("allow", "deny"):
            raise RpcError("behavior must be allow or deny")
        fut.set_result({"behavior": behavior, "reason": reason})
        s.set_state("working", confidence="hook")
        self._refill(s)  # an answer to its permission is a person's act toward it (design §4.10)
        self.store.save(s)
        await self._push_changes()

    async def rpc_recent_dirs(self) -> list[str]:
        p = paths.recent_dirs_file()
        return p.read_text().splitlines() if p.is_file() else []

    async def rpc_usage(self) -> dict[str, dict[str, Any]]:
        """Last known usage per profile (TD-001): what the top bar shows."""
        return dict(self._usage)

    async def rpc_adapters(self) -> list[str]:
        return adapters.names()

    async def rpc_ping(self) -> str:
        return "pong"

    async def rpc_host(self) -> dict[str, Any]:
        """Who this host agent is in the org (design §4.4a): its host, its home, its mode, and
        whether the home can be reached — which a client on a node needs before it labels what it
        shows *offline*."""
        out = {"host": self.host, "home": self.home, "mode": self.mode, "home_reachable": self.home_reachable()}
        if self.mode == "home":
            out["links"] = {h: dict(v) for h, v in sorted(self.links.items())}
        else:
            out["link"] = dict(self.home_link)
        return out

    # -- the container supervisor (design §4.4a "The home supervises it", TD-057 step 3c.3) -----

    def _supervise_containers(self) -> None:
        """For each container node whose link is down and whose turn has come: observe, decide,
        act — in a task, so a build never holds the tick — and say on the overlay what is being
        done. A node whose link is up is left alone and its record cleared."""
        now = time.monotonic()
        for name, n in containers.container_nodes().items():
            state = self.links.get(name)
            if state and state["up"] and "build" in state:
                # Asked again on every tick, not only at `hello`: a link that outlives a new wheel
                # at the home (today a promote restarts the home and so drops it — nothing promises
                # that) must not leave the node behind for good (review of PR #225).
                self._note_build(name, str(state["build"]))
            if state and state["up"] and not state.get("stale"):
                if self.supervision.pop(name, None) is not None:
                    self._note_link(name)
                continue
            if name not in self.supervision:
                # A linked node that is merely behind waits one grace before anything is done about
                # it: a promote writes the new wheel about a second before it restarts this agent,
                # and the process about to be stopped must not start an install it cannot finish
                # (seen live at the promote of PR #227: a `docker exec pip` left running by the stop,
                # no outcome logged, and the new process installing again 30 s later). A node whose
                # link is down is looked at at once, as before.
                linked = bool(state and state["up"])
                first = now + containers.SUPERVISE_GRACE if linked else 0.0
                self.supervision[name] = {"doing": "", "since": now_iso(), "attempts": 0, "next": first, "error": ""}
            sup = self.supervision[name]
            if not (state and state["up"]) and sup["attempts"] == 0 and not sup["doing"]:
                # the link dropped while that grace was still running — the container may really be
                # gone: looked at at once, as a down link always is. A record that has already
                # acted keeps its own backoff (review of PR #228).
                sup["next"] = 0.0
            if name in self._supervising and not self._supervising[name][0].done():
                continue
            if now < sup["next"]:
                continue
            r = self.container_runner()
            task = asyncio.create_task(self._supervise_one(n, sup, r))
            self._supervising[name] = (task, r)
            task.add_done_callback(lambda t, name=name: self._supervising.pop(name, None))

    def _stop_supervising(self, name: str) -> None:
        """Cancel the node's action in flight, process and all, and forget its record."""
        entry = self._supervising.pop(name, None)
        if entry is not None:
            task, r = entry
            r.cancel()
            task.cancel()
        self.supervision.pop(name, None)

    async def _supervise_one(self, n: containers.ContainerNode, sup: dict[str, Any], r: containers.Runner) -> None:
        try:
            cstate, alive, user = await asyncio.to_thread(containers.observe, n, r)
        except Exception as e:  # noqa: BLE001 — docker itself failing is a reason on the card, not a crash
            self._supervised(n.name, sup, f"cannot observe the container: {e}", failed=True)
            return
        state = self.links.get(n.name) or {}
        why = state.get("why", "")
        stale = bool(state.get("up") and state.get("stale"))  # linked, and behind this home's build
        action, doing = containers.decide(cstate, alive, str(why), volatile=n.volatile, stale=stale)
        if action == "wait":
            # nothing to mend — the agent is dialing, or the person stopped a volatile container:
            # give it the grace, then look again
            sup["next"] = time.monotonic() + containers.SUPERVISE_GRACE
            if sup["doing"] != doing:
                sup["doing"], sup["since"], sup["error"], sup["attempts"] = doing, now_iso(), "", 0
                self._note_link(n.name)
            return
        sup["doing"], sup["since"], sup["error"] = doing, now_iso(), ""
        self._note_link(n.name)
        log.info("supervisor %s: %s", n.name, doing)
        try:
            await asyncio.to_thread(containers.act, n, r, action, user)
        except Exception as e:  # noqa: BLE001
            self._supervised(n.name, sup, f"{doing}: failed — {e}", failed=True)
            return
        self._supervised(n.name, sup, f"{doing}: done, waiting for it to dial in", failed=False)

    def _supervised(self, name: str, sup: dict[str, Any], doing: str, *, failed: bool) -> None:
        # the first failure waits SUPERVISE_FIRST, then doubling to SUPERVISE_MAX; a success resets
        delay = min(containers.SUPERVISE_FIRST * (2 ** sup["attempts"]), containers.SUPERVISE_MAX)
        sup["attempts"] = sup["attempts"] + 1 if failed else 0
        sup["next"] = time.monotonic() + (delay if failed else containers.SUPERVISE_GRACE)
        sup["doing"], sup["since"], sup["error"] = doing, now_iso(), doing if failed else ""
        (log.warning if failed else log.info)("supervisor %s: %s", name, doing)
        self._note_link(name)

    def _note_link(self, name: str) -> None:
        """The overlay changed for that host's cards: push it."""
        task = asyncio.ensure_future(self._push_changes())
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def rpc_forget_host(self, host: str, caller: Any = None) -> dict[str, Any]:
        """`ao host forget` (design §4.4a "A container node"): the host's records at the home are
        closed as a closed session is kept — never deleted, their run logs are the node's volume —
        and its link, if up, is dropped. A person's act: a session may not forget a host."""
        if not mail.is_person(caller):
            raise RpcError(f"{caller} cannot forget host {host}: a person's act, from a terminal or the UI")
        if self.mode != "home":
            raise RpcError(f"{self.host} is a node of {self.home}: hosts are forgotten at the home")
        if host == self.host:
            raise RpcError(f"{host} is this home: it cannot forget itself")
        closed = 0
        for s in self.remote.get(host, {}).values():
            if s.state != "closed":
                s.set_state("closed", confidence="scraped")
                self._save(s)
                closed += 1
        mux = self._link_muxes.pop(host, None)
        if mux is not None:
            mux.close("the host was forgotten")
        self.links.pop(host, None)
        self._stop_supervising(host)  # `ao host forget` removed the `nodes:` entry: nothing to mend
        await self._push_changes()
        return {"host": host, "closed": closed, "kept": len(self.remote.get(host, {}))}

    def home_reachable(self) -> bool:
        """The home is always in reach of itself; a node reaches it while its link is up (§4.4a)."""
        return self.mode == "home" or bool(self.home_link["up"])

    # -- the link (design §4.4a "The link's protocol", TD-057 step 3a) ---------------------------

    async def _dial_home(self) -> None:
        """A node's dialer: keeps one link to the home up, forever, with backoff."""

        def on_state(up: bool, why: str, mux: link.Mux | None) -> None:
            if up != self.home_link["up"] or why != self.home_link["why"]:
                (log.info if up else log.warning)("link to %s: %s", self.home, why)
            self.home_link = {"up": up, "since": now_iso(), "why": why}
            self._home_mux = mux
            self._snapshot_sent = False
            if up:
                task = asyncio.ensure_future(self._send_snapshot(mux))
                self._bg.add(task)
                task.add_done_callback(self._bg.discard)

        await link.dial(
            hosts.link_socket() or hosts.link_command(),
            host=self.host,
            handler=self._from_home,
            on_state=on_state,
            first=link.BACKOFF_FIRST,
            top=link.BACKOFF_MAX,
        )

    async def _send_snapshot(self, mux: link.Mux | None) -> None:
        """First thing on a new link (§4.4a): every record of this host, whole. Reports start only
        once it is acknowledged, so the home never applies a change to a record it has not got."""
        if mux is None:
            return
        # What is marked as told is exactly what was sent: a record created while the request is out
        # is not in it, and must go in the first report rather than be taken as known.
        sending = {s.id: (_urgent(s), s.to_dict()) for s in self.sessions.values()}
        try:
            await mux.request("snapshot", timeout=link.LINK_SILENCE, records=[d for _, d in sending.values()])
        except (link.LinkClosed, link.LinkError, TimeoutError) as e:
            log.warning("snapshot to %s failed: %s", self.home, e)
            mux.close(f"snapshot failed: {e}")  # start over: a link whose snapshot did not land reports into a void
            return
        now = time.monotonic()
        self._reported = {sid: (urgent, json.dumps(d, sort_keys=True), now) for sid, (urgent, d) in sending.items()}
        self._snapshot_sent = True

    async def _report_home(self) -> None:
        """What changed since the home was last told (§4.4a "Snapshot, then reports"): at once when
        `state`, `pending`, `exit_code` or `pane` moved, else at most every `REPORT_EVERY` per
        record; and the ids this node has forgotten."""
        mux = self._home_mux
        if self.mode != "node" or mux is None or not self._snapshot_sent:
            return
        now, changed, marks = time.monotonic(), [], []
        for s in self.sessions.values():
            urgent, payload = _urgent(s), json.dumps(s.to_dict(), sort_keys=True)
            was = self._reported.get(s.id)
            if was is None or was[0] != urgent or (was[1] != payload and now - was[2] >= REPORT_EVERY):
                marks.append((s.id, (urgent, payload, now)))
                changed.append(s.to_dict())
        forgotten = [sid for sid in self._reported if sid not in self.sessions]
        # Bounded: this runs inside the tick and inside every RPC that pushes, and a write to a peer
        # that has gone blocks once the pipe is full — for `LINK_SILENCE`, were nothing to stop it.
        try:
            async with asyncio.timeout(REPORT_WRITE):
                if changed:
                    await mux.notify("report", records=changed)
                if forgotten:
                    await mux.notify("gone", ids=forgotten)
        except link.LinkClosed:
            return
        except link.LinkError as e:
            # a frame this end refused to write, because the home could not have read it (TD-066).
            # The link is started over, exactly as a failed snapshot above is: nothing is marked
            # told over a write that did not happen, and the reconnect's snapshot repairs the gap.
            log.error("a report to %s was not written: %s", self.home, e)
            mux.close(f"a report could not be written: {e}")
            return
        except TimeoutError:
            mux.close(f"a report could not be written within {REPORT_WRITE:g} s")  # the reconnect's snapshot repairs it
            return
        for sid, mark in marks:  # marked as told only once it went, as the intent push below is
            self._reported[sid] = mark
        for sid in forgotten:
            del self._reported[sid]

    async def _from_home(self, method: str, params: dict[str, Any]) -> Any:
        """What the home may ask of this node: a ping; an `act` (step 4a) — an RPC the home has
        already gated, run here through the same handler a local caller reaches, with no gate of
        its own; and `stat`, whether a directory exists here (a team start's checkout check)."""
        if method == "ping":
            return "pong"
        if method == "act":
            return await self._act(params)
        if method == "read":
            return await self._read(params)
        if method == "intent":
            await self._take_intent(params.get("records") or [])
            return None
        if method == "files":
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(read_checkout, params.get("dir"), params.get("paths")), ACT_TIMEOUT
                )
            except ValueError as e:
                raise link.LinkError(str(e)) from None
        if method == "stat":
            d = Path(str(params.get("dir") or "")).expanduser()
            return {"dir": str(d), "exists": await asyncio.to_thread(d.is_dir)}
        raise link.LinkError(f"unknown link method {method!r}")

    async def _act(self, params: dict[str, Any]) -> dict[str, Any]:
        """An act the home routed here (design §4.4a "A node reports and executes; the home
        decides"): `{rpc, params, caller}`, every address in it already written from this host's
        point of view. The home is the gate and this node the executor, so neither `_gate` nor
        the offline table runs — the request came over the link, which is the home. Refused by
        name when the record is another host's (*not my host*). The reply carries the RPC's
        result and the record as it now stands, so the home applies the outcome before it answers
        the caller rather than a report later."""
        rpc = str(params.get("rpc") or "")
        if rpc not in NODE_ACTS and rpc not in HOME_EDITS:
            raise link.LinkError(f"{rpc!r} is not an act a node executes")
        p = dict(params.get("params") or {})
        caller = params.get("caller")
        rid = str(p["id"]) if p.get("id") is not None else None
        if rid is not None:
            bare, where = naming.split_address(rid)
            if where and where != self.host:
                raise link.LinkError(f"{rid} is not on {self.host}: not my host")
            p["id"] = rid = bare
        if rpc in ("create", "name_check"):
            asked = p.pop("host", None)
            if asked and asked != self.host:
                raise link.LinkError(f"a {rpc} for {asked} is not {self.host}'s: not my host")
        method = getattr(self, f"rpc_{rpc}")
        if ignored := _drop_unknown(method, p):
            log.warning("act %s from the home: ignored unknown params %s", rpc, ", ".join(ignored))
        if "caller" in inspect.signature(method).parameters:
            p["caller"] = caller
        try:
            result = await method(**p)
        except RpcError as e:
            raise link.LinkError(str(e)) from None
        except TypeError as e:
            raise link.LinkError(f"bad params: {e}") from None
        sid = rid if rid is not None else (result.get("id") if isinstance(result, dict) else None)
        record = self.sessions.get(str(sid)) if sid else None
        return {
            "result": result,
            "record": record.to_dict() if record is not None else None,
            "gone": bool(sid) and record is None and rpc == "remove",
        }

    async def _take_intent(self, records: list[Any]) -> None:
        """The home's intent for this node's records (§4.4a, step 4b.2): `apply_home` over exactly
        `INTENT_FIELDS` — whatever else a push carries is not taken — so the stopping policies read
        what the home holds; the unread count kept apart as a hint for the mail line, never an
        inbox. A record of another host, or one this node does not hold, is not this node's."""
        changed = False
        for raw in records:
            if not isinstance(raw, dict) or raw.get("host") != self.host:
                log.warning("intent from %s: dropped a record that is not %s's", self.home, self.host)
                continue
            s = self.sessions.get(str(raw.get("id") or ""))
            if s is None:
                continue
            with contextlib.suppress(TypeError, ValueError):
                self._mail_hints[s.id] = (
                    int(raw.get("unread") or 0),
                    bool(raw.get("wake_budget_spent")),
                    [str(x) for x in (raw.get("owed") or [])],
                )
            fields = {k: raw[k] for k in INTENT_FIELDS if k in raw}
            before, stop_before = s.to_dict(), s.run_until
            try:
                apply_home(s, {**fields, "id": s.id, "host": s.host})
            except (NotTheSameSession, TypeError, ValueError, KeyError) as e:
                log.warning("intent from %s: could not take %s: %s", self.home, s.id, e)
                continue
            if s.run_until != stop_before:
                s.wrapup_sent_at = None  # a new stop time is a new run, as `set_stop` has it
            if _renamed_grants(s, f"the intent from {self.home}") or s.to_dict() != before:
                self.store.save(s)
                changed = True
        if changed:
            await self._push_changes()

    async def _send_derived(self, s: Session, progress: list[Any], findings: list[Any], retire: list[str]) -> None:
        """A node's tick derived these for `s` (§4.4a, step 4b.2): sent to the home — whose fields
        they are — as `derived`, and applied there exactly as a home's own tick applies them. Not
        queued: a link that is gone, or a home that refuses, is a log line, and the next derive
        (`DERIVE_EVERY`) says it again."""
        mux = self._home_mux
        if mux is None or not (progress or findings or retire):
            return
        try:
            await mux.request(
                "derived",
                timeout=REPORT_WRITE,
                id=s.id,
                progress=[e.to_dict() for e in progress],
                findings=[e.to_dict() for e in findings],
                retire=list(retire),
            )
        except (link.LinkError, link.LinkClosed, TimeoutError) as e:
            log.warning("derived reports for %s did not reach %s: %s", s.id, self.home, e)

    async def _read(self, params: dict[str, Any]) -> Any:
        """A read the home routed here (§4.4a, step 4b.1): `{rpc, params}`, `rpc` one of
        `NODE_READS` and nothing else — ungated, as on one host, so no caller rides along. Refused
        by name when the record is another host's (*not my host*). The reply is the RPC's own."""
        rpc = str(params.get("rpc") or "")
        if rpc not in NODE_READS:
            raise link.LinkError(f"{rpc!r} is not a read a node serves the home")
        p = dict(params.get("params") or {})
        bare, where = naming.split_address(str(p.get("id") or ""))
        if where and where != self.host:
            raise link.LinkError(f"{p.get('id')} is not on {self.host}: not my host")
        p["id"] = bare
        method = getattr(self, f"rpc_{rpc}")
        if ignored := _drop_unknown(method, p):
            log.warning("read %s from the home: ignored unknown params %s", rpc, ", ".join(ignored))
        try:
            return await method(**p)
        except RpcError as e:
            raise link.LinkError(str(e)) from None
        except TypeError as e:
            raise link.LinkError(f"bad params: {e}") from None

    # -- a node's calls the home answers (design §4.4a "Mail across hosts", TD-057 step 5) --------

    def _from_home_form(self, address: str) -> str:
        """An address in the home's form, read at this node: the home's own sessions bare there are
        `id@home` here, and `id@<this node>` is bare."""
        sid, h = naming.split_address(address)
        return naming.qualify(f"{sid}@{h or self.home}", local=self.host)

    async def _forward(self, rid: Any, name: str, params: dict[str, Any], caller: Any) -> dict[str, Any]:
        """A node hands a call it cannot serve to the home over the link — `forward {rpc, params,
        caller, token}` — and answers with the home's verdict, every address in it rewritten into
        this host's form. A cancelled call (a `wait` whose client went away) is cancelled at the
        home too, by its token, so no ghost wait is charged a wake there. Never queued: a link
        that drops while the call is out is that call's error."""
        mux = self._home_mux
        if mux is None:
            raise RpcError(f"{self.home} (home) is unreachable from {self.host}; refused, not queued (design §4.4a)")
        token = secrets.token_hex(8)
        # A `wait` is bounded by its own timeout at the home; everything else within ACT_TIMEOUT —
        # pings keep a link up past a dispatch that hangs (review of PR #217)
        timeout = None if name == "wait" else ACT_TIMEOUT
        try:
            reply = await mux.request("forward", timeout=timeout, rpc=name, params=params, caller=caller, token=token)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await mux.notify("cancel", token=token)
            raise
        except link.LinkError as e:
            return {"id": rid, "error": f"{self.home}: {e}"}
        except link.LinkClosed:
            return {"id": rid, "error": f"the link to {self.home} dropped while {name} was out: its verdict is unknown"}
        except TimeoutError:
            with contextlib.suppress(Exception):
                await mux.notify("cancel", token=token)
            return {
                "id": rid,
                "error": f"{self.home} did not answer {name} within {timeout:g} s: its verdict is unknown",
            }
        reply = reply if isinstance(reply, dict) else {}
        out = naming.readdress({k: v for k, v in reply.items() if k != "id"}, self._from_home_form)
        return {"id": rid, **out}

    async def _forwarded(self, host: str, params: dict[str, Any]) -> dict[str, Any]:
        """The home's end: a node's call run here as that node's session — the caller is
        `id@host`, every address in the params read from that host's point of view, a `create`
        landing on that host unless it says otherwise — through the same dispatch a local caller
        gets, gate and routing included. The reply is the dispatch's, addresses in the home's
        form; the node rewrites them. Registered by token until it returns so the node can
        cancel it."""
        rpc = str(params.get("rpc") or "")
        if rpc in modes.HOME_ONLY:
            # a checkout's files are read by a caller at the home, for a team start there (4b.3);
            # a node — a person or a session on it — never reads another host's files through here
            return {"id": 0, "error": f"host_files is not served to a call from {host}: ask at the home (design §4.4a)"}
        p = naming.readdress(dict(params.get("params") or {}), lambda a: self._from_host(a, host))
        if rpc == "set_controllers":
            for key in ("add", "remove"):
                if p.get(key):
                    p[key] = [self._from_host(x, host) for x in p[key]]
        if rpc == "create":
            p.setdefault("host", host)
        if params.get("caller") is None:
            # A person at a node acts only on that node's records (§4.4a): `id@<this home>` collapsed
            # to a home record above, and the person gate would then have passed it (security read of
            # PR #217). The target's host, read after the rewrite, has to be the link's own. A call
            # that names no session — the person inbox, a `wait` — names nothing to bound; the wait
            # is scoped to the node's host instead. A `create` for another host is refused at the
            # node before it gets here; the check is kept as a second line.
            if rpc == "wait":
                p["only_host"] = host
            where: str | None = None
            if rpc == "create":
                where = str(p.get("host") or host)
            elif rpc in modes.PERSON_NODE_BOUND and p.get("id") and p["id"] != PERSON:
                where = naming.split_address(str(p["id"]))[1] or self.host
            if where is not None and where != host:
                return {
                    "id": 0,
                    "error": (
                        f"a person at {host} may {rpc} only {host}'s sessions: {p.get('id') or p.get('host')} "
                        f"is on {where} (design §4.4a)"
                    ),
                }
        req = {"id": 0, "method": rpc, "params": p, "caller": params.get("caller")}
        task = asyncio.ensure_future(self._dispatch(req, link_host=host))
        token = str(params.get("token") or "")
        if token:
            self._forwarded_calls[token] = task
        try:
            return await task
        finally:
            if token:
                self._forwarded_calls.pop(token, None)
            if not task.done():
                task.cancel()

    def _cancel_forwarded(self, token: str) -> None:
        task = self._forwarded_calls.pop(str(token), None)
        if task is not None and not task.done():
            task.cancel()

    # -- acts across the link, at the home (design §4.4a, TD-057 step 4a) -----------------------

    def _act_host(self, method: str, params: dict[str, Any]) -> str | None:
        """The host an act is for when it is not this one: the address in `id`, or `create`'s
        (and `name_check`'s) `host`. None means *here*, and the method runs as it always has."""
        if method not in NODE_ACTS and method not in HOME_EDITS and method not in NODE_READS:
            return None
        if method in ("create", "name_check"):
            h = str(params.get("host") or "")
        else:
            _rid, h = naming.split_address(str(params.get("id") or ""))
        return h if h and h != self.host else None

    def _from_host(self, address: Any, host: str) -> str:
        """An address as `host`'s node stores it, read from here: its bare ids are `id@host`, and
        an `id@<this home>` is bare."""
        sid, h = naming.split_address(str(address))
        return naming.qualify(f"{sid}@{h or host}", local=self.host)

    def _to_host(self, address: Any, host: str) -> str:
        """The inverse: an address as this home stores it, written for `host`'s node."""
        sid, h = naming.split_address(str(address))
        return naming.qualify(f"{sid}@{h or self.host}", local=host)

    def _graph(self) -> dict[str, Session]:
        """One graph for the gates and the mailbox (§4.4a "The gate reads one graph", steps 4a and
        5): this host's records under their ids and every other host's under `id@host` — the
        records themselves, so what the mailbox writes lands on the record `_save` knows the host
        of. A remote record's `controllers` are stored as its node writes them; `_ctl` reads them
        from here, and every gate takes it. `self.sessions` itself is never widened — the tick,
        the anchor rule and the pane reads stay this host's."""
        g: dict[str, Session] = dict(self.sessions)
        for host, recs in self.remote.items():
            for rid, r in recs.items():
                g[f"{rid}@{host}"] = r
        return g

    def _ctl(self, s: Session) -> list[str]:
        """A record's `controllers` as this host addresses them (§4.4a "Every address crosses in
        the reader's form"): its own as stored, another host's re-addressed."""
        return s.controllers if s.host == self.host else [self._from_host(x, s.host) for x in s.controllers]

    def _address(self, s: Session) -> str:
        return s.id if s.host == self.host else f"{s.id}@{s.host}"

    def _node_mux(self, host: str) -> link.Mux:
        """The live link to `host`, or the refusal in words: *unreachable since <when> — <why>*,
        never queued (§4.4a "When the recipient's host is unreachable")."""
        if self.mode != "home":
            raise RpcError(f"{self.host} is a node of {self.home}: it acts on its own sessions only, ask the home")
        mux = self._link_muxes.get(host)
        if mux is not None:
            return mux
        state = self.links.get(host)
        if state is None and host not in hosts.nodes() and host not in self.remote:
            raise RpcError(f"unknown host {host}: not under `nodes:` in {self.host}'s hosts.yml")
        since = (state or {}).get("since") or "the home started"
        why = (state or {}).get("why") or "not connected since the home started"
        raise RpcError(f"runs on {host}: unreachable since {since} — {why}; refused, not queued (design §4.4a)")

    async def rpc_host_dir(self, host: str, dir: str) -> dict[str, Any]:
        """Whether `dir` exists on `host` (design §4.4a "Teams across hosts"): `ao team start`'s
        *every checkout exists on the record's host*, asked of that host's node. A read."""
        if host == self.host:
            return {"host": host, "dir": dir, "exists": await asyncio.to_thread(Path(dir).expanduser().is_dir)}
        mux = self._node_mux(host)
        try:
            seen = await mux.request("stat", timeout=ACT_TIMEOUT, dir=dir)
        except link.LinkError as e:
            raise RpcError(f"{host}: {e}") from None
        except (link.LinkClosed, TimeoutError) as e:
            raise RpcError(f"{host} did not answer: {e or 'the link dropped'}") from None
        return {"host": host, **(seen if isinstance(seen, dict) else {"dir": dir, "exists": False})}

    async def rpc_host_files(
        self, host: str, dir: str, paths: list[str] | None = None, caller: Any = None
    ) -> dict[str, Any]:
        """The text of files in a checkout on `host` (design §4.4a "Teams across hosts", step
        4b.3): a team start's repo config and briefs, read where the checkout is — `paths` relative
        to `dir`, confined to it, bounded (`read_checkout`). A read, like `host_dir`, and yet more
        than an existence check: a person's, or a session's holding `control` — one that could
        start that team anyway. Never for a call forwarded from a node (`_forwarded` refuses it):
        a laptop does not read another host's files through the home."""
        if not mail.is_person(caller):
            me = self._graph().get(self._addr(caller))
            if me is None or not has_control(me.capabilities):
                raise RpcError(f"{caller} cannot read files on {host}: needs the control grant (design §4.4a)")
        if host == self.host:
            try:
                got = await asyncio.wait_for(asyncio.to_thread(read_checkout, dir, paths), ACT_TIMEOUT)
            except ValueError as e:
                raise RpcError(str(e)) from None
            except TimeoutError:
                raise RpcError(f"reading {dir} did not finish within {ACT_TIMEOUT:g} s") from None
            return {"host": host, **got}
        mux = self._node_mux(host)
        try:
            got = await mux.request("files", timeout=ACT_TIMEOUT, dir=dir, paths=list(paths or []))
        except link.LinkError as e:
            raise RpcError(f"{host}: {e}") from None
        except (link.LinkClosed, TimeoutError) as e:
            raise RpcError(f"{host} did not answer: {e or 'the link dropped'}") from None
        return {"host": host, **(got if isinstance(got, dict) else {"dir": dir, "files": {}})}

    async def _check_occupancy_for(self, host: str, params: dict[str, Any]) -> None:
        """The anchor rule (§9 invariant 2) for a create routed to a container node on this
        machine (3c.4): the checkout is one directory here and there, and the node cannot see this
        host's sessions, so the home checks its own records — and the other container nodes' —
        before the create crosses. The node then checks its own as it always has. A machine node
        is not checked: its path is another directory."""
        if host not in containers.container_nodes():
            return
        if params.get("kind", "interactive") != "interactive" or params.get("adapter", "shell") == "shell":
            return
        directory = Path(str(params.get("dir") or "")).expanduser().resolve()
        if params.get("worktree"):
            # the same place `create` puts it — one resolution, the main checkout's, shared with it
            repo = Path(str(params.get("repo") or directory)).expanduser().resolve()
            try:
                directory = await asyncio.to_thread(worktree_path, repo, str(params["worktree"]))
            except WorktreeError:
                return  # the node's own create refuses it in its own words
        if not directory.is_dir():
            return  # the node's own create says whether it exists there
        for who in await asyncio.to_thread(self.occupants, directory):
            raise RpcError(f"{directory} already has agent session {who}; anchor rule (use a worktree)")

    async def _route_read(self, method: str, params: dict[str, Any], host: str) -> Any:
        """A read of another host's pane (§4.4a, step 4b.1): served by that host's node through the
        `read` link method, ungated, and refused as unreachable — never queued — while the link is
        down. The reply is the node's, untouched but for its addresses."""
        rid, _h = naming.split_address(str(params.get("id") or ""))
        if rid not in self.remote.get(host, {}):
            raise RpcError(f"no session {rid}@{host}")
        mux = self._node_mux(host)
        try:
            reply = await mux.request("read", timeout=ACT_TIMEOUT, rpc=method, params={**params, "id": rid})
        except link.LinkError as e:
            raise RpcError(f"{host}: {e}") from None
        except (link.LinkClosed, TimeoutError) as e:
            raise RpcError(f"{host} did not answer {method}: {e or 'the link dropped'}") from None
        return naming.readdress(reply, lambda a: self._from_host(a, host))

    async def _route_act(self, method: str, params: dict[str, Any], caller: Any, host: str) -> Any:
        """An act on another host's record, gated here already: executed by that host's node and
        its verdict returned (§4.4a). Refused in words — never queued — while the link is down.
        A home-owned edit is applied to this home's copy after the node took it, so the two agree
        and the caller's reply is the home's view."""
        rid, _h = naming.split_address(str(params.get("id") or ""))
        if method not in ("create", "name_check") and rid not in self.remote.get(host, {}):
            raise RpcError(f"no session {rid}@{host}")
        if method == "create":
            await self._check_occupancy_for(host, params)
        mux = self._node_mux(host)
        sent = dict(params)
        if rid:
            sent["id"] = rid
        for key in ("controllers", "add", "remove") if method in ("create", "set_controllers") else ():
            if sent.get(key):
                sent[key] = [self._to_host(x, host) for x in sent[key]]
        who = None if mail.is_person(caller) else self._to_host(caller, host)
        timeout: float | None = ACT_TIMEOUT
        if method == "send" and sent.get("wait"):
            timeout = None if sent.get("timeout") is None else float(sent["timeout"]) + ACT_TIMEOUT
        try:
            reply = await mux.request("act", timeout=timeout, rpc=method, params=sent, caller=who)
        except link.LinkError as e:
            raise RpcError(f"{host}: {e}") from None
        except link.LinkClosed:
            raise RpcError(
                f"the link to {host} dropped while {method} was out: its verdict is unknown — read the record"
            ) from None
        except TimeoutError:
            raise RpcError(f"{host} did not answer {method} within {timeout:g} s: its verdict is unknown") from None
        reply = reply if isinstance(reply, dict) else {}
        if reply.get("record"):
            self._take_records(host, [reply["record"]], whole=False)
        elif reply.get("gone") and rid:
            self._forget_remote(host, rid)
        result = reply.get("result")
        if method in HOME_EDITS:
            # the home's own copy carries the edit, and its view — addressed — is what the caller gets
            await getattr(self, f"rpc_{method}")(**params)
            result = self._view(self.remote[host][rid])
        elif method == "name_check" and isinstance(result, dict):
            for key in ("id", "holder"):  # the node's bare ids, addressed for the caller (review of PR #213)
                if isinstance(result.get(key), str) and result[key].startswith(naming.PREFIX):
                    result[key] = f"{result[key]}@{host}"
        elif isinstance(result, dict) and result.get("host") == host and result.get("id"):
            # the node answered with its view of the record: the caller gets this home's, addressed
            held = self.remote.get(host, {}).get(str(result["id"]))
            result = self._view(held) if held is not None else {**result, "id": f"{result['id']}@{host}"}
        await self._push_changes()
        return result

    async def _serve_link(self, info: Any, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One node's link, for as long as it lasts. The host name is the one sshd's forced command
        announced — it came from `authorized_keys`, never from the node — and everything the link
        may do is decided against that name."""
        host = str((info or {}).get("host") or "").strip() if isinstance(info, dict) else ""
        said_hello = False

        async def from_node(method: str, params: dict[str, Any]) -> Any:
            nonlocal said_hello
            if method == "hello":
                refusal = self._link_refusal(host, params)
                if refusal:
                    asyncio.get_running_loop().call_later(0.2, mux.close, refusal)  # after the reply is written
                    if host in hosts.nodes() and "protocol" in refusal:
                        # an authorised node on an older build: the supervisor re-provisions a container
                        self.links[host] = {"up": False, "since": now_iso(), "why": f"refused: {refusal}"}
                    raise link.LinkError(refusal)
                old = self._link_muxes.get(host)
                if old is not None and old is not mux:
                    old.close("replaced by a newer link from the same host")
                self._link_muxes[host] = mux
                self._intent_sent.pop(host, None)  # nothing is pushed to a link before its snapshot
                self.links[host] = {"up": True, "since": now_iso(), "why": "linked"}
                self._note_build(host, str(params.get("build") or ""))
                said_hello = True
                log.info("link from %s: up", host)
                if host in containers.container_nodes():
                    task = asyncio.ensure_future(self._note_reach(host))  # one docker look, off the link
                    self._bg.add(task)
                    task.add_done_callback(self._bg.discard)
                await self._push_changes()  # the overlay lifts on its cards
                return {"protocol": link.PROTOCOL, "home": self.host, "host": host}
            if not said_hello:
                raise link.LinkError("say hello first")
            if method == "ping":
                return "pong"
            if method in ("snapshot", "report"):
                taken = self._take_records(host, params.get("records") or [], whole=method == "snapshot")
                if method == "snapshot":
                    self._intent_sent[host] = {}  # the intent goes out whole once, then as it changes
                await self._push_changes()
                return {"taken": taken}
            if method == "gone":
                for rid in params.get("ids") or []:
                    self._forget_remote(host, str(rid))
                await self._push_changes()
                return None
            if method == "derived":
                return await self._take_derived(host, params)
            if method == "forward":
                return await self._forwarded(host, params)
            if method == "cancel":
                self._cancel_forwarded(str(params.get("token") or ""))
                return None
            raise link.LinkError(f"unknown link method {method!r}")

        mux = link.Mux(reader, writer, from_node)
        why = "the link's reader failed"
        try:
            why = await mux.run()
        finally:  # however it ended: a link the home still calls up after it has gone is the worst answer
            if self._link_muxes.get(host) is mux:
                del self._link_muxes[host]
                self._intent_sent.pop(host, None)
                self.links[host] = {"up": False, "since": now_iso(), "why": why}
                log.warning("link from %s: down — %s", host, why)
                with contextlib.suppress(Exception):
                    await self._push_changes()  # its cards go `unreachable` now, not at the next tick

    async def _take_derived(self, host: str, params: dict[str, Any]) -> dict[str, Any]:
        """A node's tick derived reports for one of its records (§4.4a, step 4b.2): applied as this
        home's own tick applies its own — upserted under §9 invariant 10, the branch claims named
        retired — and only for a record of the link's host. A derived report is never `declared`:
        one that says it is is refused whole."""
        rid = str(params.get("id") or "")
        s = self.remote.get(host, {}).get(rid)
        if s is None:
            raise link.LinkError(f"{rid}@{host}: no such record here")
        try:
            progress = [ProgressEntry.from_dict(e) for e in params.get("progress") or []]
            findings = [FindingEntry.from_dict(e) for e in params.get("findings") or []]
        except (TypeError, ValueError, AttributeError) as e:
            raise link.LinkError(f"bad derived report: {e}") from None
        if any(e.source == "declared" for e in (*progress, *findings)):
            raise link.LinkError("a derived report is never declared: a session declares through its own `ao`")
        applied = [s.report_progress(e) for e in progress] + [s.report_finding(e) for e in findings]
        changed = any(applied) | s.retire_branch_claims(str(r) for r in params.get("retire") or [])
        if changed:
            self._save(s)
            await self._push_changes()
        return {"changed": changed}

    async def _push_intent(self) -> None:
        """What changed in the home-owned fields, or the unread count, of a linked node's records
        since that node was last told (§4.4a, step 4b.2) — everything, once, after its snapshot.
        `controllers` go as stored: another host's record keeps them in its node's form here
        (step 4a). A notification, bounded like a report: a push that cannot be written gives the
        link up, and the next link's snapshot pushes everything again. Never queued."""
        for host, sent in list(self._intent_sent.items()):
            mux = self._link_muxes.get(host)
            if mux is None:
                continue
            recs = self.remote.get(host, {})
            out = []
            for rid, r in recs.items():
                d = r.to_dict()
                item = {
                    "id": rid,
                    "host": host,
                    **{k: d[k] for k in sorted(INTENT_FIELDS) if k in d},
                    "unread": r.unread(),
                    "wake_budget_spent": r.wake_budget_spent(),
                    "owed": r.owed(),  # the outcome debt rides with the unread hint (§4.10 *Outcomes*)
                }
                payload = json.dumps(item, sort_keys=True)
                if sent.get(rid) != payload:
                    out.append((rid, payload, item))
            for rid in [x for x in sent if x not in recs]:
                del sent[rid]
            if not out:
                continue
            try:
                async with asyncio.timeout(REPORT_WRITE):
                    await mux.notify("intent", records=[item for _, _, item in out])
            except link.LinkClosed:
                continue
            except link.LinkError as e:  # refused here, unreadable there: start the link over (TD-066)
                log.error("an intent push to %s was not written: %s", host, e)
                mux.close(f"an intent push could not be written: {e}")
                continue
            except TimeoutError:
                mux.close(f"an intent push could not be written within {REPORT_WRITE:g} s")
                continue
            for rid, payload, _ in out:  # marked as told only once it went
                sent[rid] = payload

    def _note_build(self, host: str, build: str) -> None:
        """What the node says it runs, kept on its link state — and, for a container node, whether
        it is behind what this home would provision now (§4.4a "The home supervises it": *at its
        own version*). A promote changes no protocol number, so a node can stay linked many builds
        behind, answering *unknown link method* to everything newer; the supervisor re-provisions
        one marked `stale`. A machine node's build is recorded and nothing more: the home does
        not install there."""
        state = self.links.get(host)
        if state is None:
            return
        state["build"] = build
        if host in containers.container_nodes():
            ours = containers.home_build()
            if ours and build != ours:
                if "stale" not in state:
                    log.warning(
                        "link from %s: the node runs build %s, this home's is %s", host, build or "unknown", ours
                    )
                state["stale"] = f"the node runs build {build or 'unknown'}, this home's is {ours}"
            else:
                state.pop("stale", None)

    async def _note_reach(self, host: str) -> None:
        """How a container node's sessions are reached (§4.4a "Reach", 3c.5): looked up (three
        docker calls) once when it dials in and kept on its link state, so every card of its carries `host_link.reach` —
        the Focus terminal and `ao focus` run `docker exec … tmux attach` from it, and the card's
        VS Code link attaches to that container. A failed look is a log line and no reach."""
        n = containers.container_nodes().get(host)
        state = self.links.get(host)
        if n is None or state is None or not state.get("up"):
            return
        try:
            seen = await asyncio.to_thread(containers.observe_reach, n, self.container_runner())
        except Exception as e:  # noqa: BLE001 — docker failing is a log line, never a dead link
            log.warning("link from %s: could not derive its reach: %s", host, e)
            return
        if seen and self.links.get(host) is state:
            state["reach"] = seen
            await self._push_changes()

    def _take_records(self, host: str, records: list[Any], *, whole: bool) -> int:
        """A node's snapshot or report (§4.4a): only records whose `host` is the name this link's
        key is bound to; a known one takes the node-owned fields, an unknown one is adopted whole;
        and a snapshot is the truth about which sessions that host has."""
        mine = self.remote.setdefault(host, {})
        seen: set[str] = set()
        for raw in records:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            if raw.get("host") != host:
                log.warning("link from %s: dropped a report about %s@%s", host, raw.get("id"), raw.get("host"))
                continue
            rid = str(raw["id"])
            seen.add(rid)  # listed, whether or not it could be taken: a snapshot forgets only what it omits
            try:
                if rid in mine:
                    try:
                        apply_node(mine[rid], raw)
                    except NotTheSameSession:
                        if mine[rid].state != "closed":
                            raise  # a live record that disagrees on identity is another session: refused
                        # §4.4a, §9 invariant 12: a closed record of this id is superseded by the
                        # node's new one — replaced in place, as a new session of a name replaces a
                        # finished one on one host (`_take_name`); its run log stays on its node.
                        log.info("link from %s: %s supersedes the closed record of the same id", host, rid)
                        mine[rid] = Session.from_dict(raw)
                else:
                    mine[rid] = Session.from_dict(raw)  # adopted, the replica's home-owned fields and all
            except (NotTheSameSession, TypeError, ValueError, KeyError) as e:
                log.warning("link from %s: could not take %s: %s", host, rid, e)
                continue
            _renamed_grants(mine[rid], f"the link from {host}")  # saved just below
            self._remote_store(host).save(mine[rid])
        if whole:
            for rid in [r for r in mine if r not in seen]:
                self._forget_remote(host, rid)
        return len(seen)

    def _forget_remote(self, host: str, rid: str) -> None:
        if self.remote.get(host, {}).pop(rid, None) is None:
            return
        self._remote_store(host).delete(rid)
        address = f"{rid}@{host}"
        for last in self._subscribers.values():
            last.pop(address, None)
        self._gone.append(address)

    def _link_refusal(self, host: str, hello: dict[str, Any]) -> str | None:
        """Why this home does not take a link from `host`, or None (§4.4a "Who may connect")."""
        if self.mode != "home":
            return f"{self.host} is a node of {self.home}, not a home: there is one home, and a node takes no links"
        if not host:
            return "the link named no host (`agentorc-agent link --host <name>` in authorized_keys)"
        if host == self.host:
            return f"{host} is this home's own name: a node's key must be bound to the node's name"
        if host not in hosts.nodes():
            return f"{host} is not an authorised node: add it under `nodes:` in {self.host}'s hosts.yml"
        claimed = str(hello.get("host") or "")
        if claimed and claimed != host:
            return (
                f"this link is bound to {host}, and the node calls itself {claimed}: the name the home binds "
                "(the `--host` in authorized_keys, or the `nodes:` entry whose socket this is) and the node's "
                "`local: {name: …}` must agree"
            )
        if hello.get("protocol") != link.PROTOCOL:
            return f"link protocol {hello.get('protocol')!r} here is {link.PROTOCOL}: promote both ends to one build"
        return None

    # -- who is calling (design §4.8a, TD-077) -----------------------------------------------------

    def _id_note_panes(self, panes: dict[str, PaneInfo]) -> None:
        """The live panes of this host's records, as the classification reads them. On the loop,
        from a pane list a thread already took."""
        self._id_panes = [
            identity.Pane(sid, p.pane_pid, identity.tty_nr_of(p.tty) if p.tty else 0)
            for sid, p in panes.items()
            if not p.dead and sid in self.sessions
        ]
        self._id_listed_at = time.monotonic()

    async def _id_read_detached(self, tmux_pid: int | None) -> None:
        """Compute the detached-process check against the tmux server now running. `None` while
        there is no server, which is *not yet known* rather than *off*: the next connection asks
        again."""
        self._id_tmux, self._id_rechecked = self._id_server(tmux_pid), time.monotonic()
        if not tmux_pid:
            self._id_detached = None
            return
        was = self._id_detached
        self._id_detached = identity.detached_check(self.proc, agent_pid=os.getpid(), tmux_pid=tmux_pid) or ""
        if (was or "") != self._id_detached:
            log.info(
                "detached-process check %s (tmux server pid %s)", "on" if self._id_detached else "off", tmux_pid
            )

    async def _id_recheck_detached(self) -> None:
        """A tmux server can be **replaced** while the agent runs — kmaster's was, on 2026-09-20 —
        and the check is a fact about *that* server's cgroup. Computed once and kept, it went on
        describing a server that no longer existed until the agent was restarted, which is a host
        reading `enforce` off a dead process's cgroup (TD-077). So the tick re-reads the server's
        pid on a cadence of its own, and only a pid that moved costs the check itself. One
        `display-message` every `ID_RECHECK` seconds, in a thread, beside the pane list the tick
        already takes."""
        if time.monotonic() - self._id_rechecked < ID_RECHECK:
            return
        pid = await asyncio.to_thread(self.tmux.server_pid)
        if self._id_server(pid) == self._id_tmux and self._id_detached is not None:
            self._id_rechecked = time.monotonic()
            return
        await self._id_read_detached(pid)

    def _id_server(self, pid: int | None) -> tuple[int, int] | None:
        """A server's name for life: its pid **and its start time**. A pid alone would read a
        replacement that landed on the same pid inside one `ID_RECHECK` window as the same server
        — the identity.py walk pairs the two for exactly this reason (review of PR #265)."""
        if not pid:
            return None
        st = self.proc.stat(pid)
        return (pid, st.start if st else 0)

    async def _id_channel(self, peer: int) -> identity.Channel:
        """Classify one connection's peer. A peer that matches no pane we know may belong to one the
        tick has not listed yet — a session's first hook can beat the first tick after `create` —
        so it waits for a fresh list (one at a time, at most one a second) before it is judged
        *outside* or *unknown*; never against the old one."""
        if self._id_detached is None:
            await self._id_read_detached(await asyncio.to_thread(self.tmux.server_pid))
        detached = self._id_detached or None
        ch = identity.classify(peer, self._id_panes, self.proc, detached=detached)
        listed = {p.session for p in self._id_panes}
        unlisted = any(
            sid not in listed and r.pane and (r.host or self.host) == self.host and r.state not in ("exited", "closed")
            for sid, r in self.sessions.items()
        )
        if ch.kind == "session" or not unlisted:
            # Every live record's pane is known, so a peer that matched none is under none: the
            # person's terminal and the UI — nearly every such connection — never wait for a list.
            return ch
        asked = time.monotonic()
        async with self._id_list_lock:
            if self._id_listed_at <= asked:  # nobody listed while this one waited for the lock
                wait = 1.0 - (time.monotonic() - self._id_listed_at)
                if self._id_listed_at and wait > 0:
                    await asyncio.sleep(wait)
                self._id_note_panes(await asyncio.to_thread(self.tmux.main_panes, naming.PREFIX))
        return identity.classify(peer, self._id_panes, self.proc, detached=detached)

    async def _identify(self, req: dict[str, Any], peer: int, conn: Any = None) -> dict[str, Any] | None:
        """The one step at the head of dispatch (§4.8a *Where it lives*): judge the envelope's
        `caller` against the channel, record an alarm when they disagree, and — under `enforce` —
        replace the claim with the verdict or refuse. Under `observe` nothing a caller sees
        changes. Returns the refusal to send, or None to go on."""
        rpc = str(req.get("method") or "")
        # The connection is classified once, at its first request, and keeps that for its life: the
        # peer pid is the one that connected, and asking `/proc` about it again later could be asking
        # about whoever holds that pid *now* — a process that connects, hands the socket to a child
        # and exits must not become whatever reuses its pid.
        ch = self._id_conns.get(conn) if conn is not None else None
        if ch is None:
            ch = await self._id_channel(peer)
            self.identity_tally[f"{ch.kind}:{ch.signal}" if ch.signal else ch.kind] += 1
            if conn is not None:
                self._id_conns[conn] = ch
        if rpc == "whoami":
            return {"id": req.get("id"), "result": {"channel": ch.kind, "session": ch.session, "signal": ch.signal}}
        claimed = req.get("caller")
        named = None if mail.is_person(claimed) else self._addr(claimed)
        params = req.get("params")
        hooked = params.get("session") if rpc == "hook" and isinstance(params, dict) else None
        verdict = identity.judge(ch, named, rpc, hook_session=self._addr(hooked) if hooked else None)
        if verdict.alarm is not None:
            self._id_alarm(verdict.alarm, verdict.about)
        if self.identity_mode != "enforce":
            return None
        if verdict.refusal is not None:
            return {"id": req.get("id"), "error": verdict.refusal}
        if rpc not in identity.READS:
            if verdict.caller is None:
                req.pop("caller", None)
            else:
                req["caller"] = verdict.caller
        elif ch.kind == "session":
            # A read is served whatever it claims, but from under a pane it runs as that pane's
            # session all the same — no refusal and no alarm, only no borrowed name (the unread-mail
            # line on a reply is the caller's, for one).
            req["caller"] = ch.session
        return None

    def _id_alarm(self, entry: dict[str, Any], about: str | None) -> None:
        at = now_iso()
        log.warning("identity alarm (%s): %s claimed %r on %s", self.identity_mode, *entry.values())
        s = self.sessions.get(about) if about else None
        if s is None:
            # The host's own list follows the record's rule (§4.8a): a *new* alarm is written at
            # once, a repeat moves a count in memory and the next tick writes it, so a loop of
            # forgeries is not a disk write each.
            known = len(self.identity_alarms)
            self.identity_alarms = identity.coalesce(self.identity_alarms, entry, at)
            if len(self.identity_alarms) != known:
                self.identity_store.save(self.identity_alarms)
                self._id_host_dirty = False
            else:
                self._id_host_dirty = True
            return
        known = len(s.identity_alarms)
        s.identity_alarms = identity.coalesce(s.identity_alarms, entry, at)
        # A new alarm is written at once; a repeat only bumps a count, and a loop of forged requests
        # must not become a disk write each — the tick writes what is left (`_id_flush`).
        if len(s.identity_alarms) != known:
            self._save(s)
        else:
            self._id_dirty.add(s.id)

    def _id_flush(self) -> None:
        for sid in list(self._id_dirty):
            self._id_dirty.discard(sid)
            if (s := self.sessions.get(sid)) is not None:
                self._save(s)
        if self._id_host_dirty:
            self._id_host_dirty = False
            self.identity_store.save(self.identity_alarms)

    async def rpc_identity_ack(self, id: str | None = None, caller: Any = None) -> dict[str, Any]:
        """**Acknowledge** an identity alarm list (design §4.8a, §4.5a **Inbox row: identity
        alarm**): clears one record's `identity_alarms`, or — with no `id` — the host's own list,
        so the row leaves the person's Inbox. *A person has seen this and decided what it was.*

        **A person's only**, refused to every session exactly as `inbox_delete` is, and deliberately
        **not** in `identity.READS`: a session that could clear the list could erase the evidence of
        its own forgery, which is the one thing the alarm exists to prevent. Nothing is lost either
        way — the host agent's log keeps every alarm, one line each (§4.8a).

        **A node's record is acknowledged at that node** (§4.8a): alarms are node-owned, so an `id`
        naming another host is routed there like any other act (`NODE_ACTS`, §4.4a step 4a), the
        node clears its own list and the home takes the cleared record from the reply — a home that
        cleared its replica would have the alarms back on the node's next report. A person at a
        node may clear only that node's records (`PERSON_NODE_BOUND`); the host's own list is
        whichever host was asked, and never travels."""
        if not mail.is_person(caller):
            raise RpcError(
                f"{caller} cannot acknowledge an identity alarm: the list is cleared only by a person, "
                "in the Inbox (design §4.8a)"
            )
        if not id or id == PERSON:
            self.identity_alarms = []
            self._id_host_dirty = False
            self.identity_store.save(self.identity_alarms)
            return {"id": PERSON, "cleared": True, "alarms": []}
        s = self._get(self._addr(id))
        s.identity_alarms = []
        self._id_dirty.discard(s.id)
        self._save(s)
        await self._push_changes()
        return {"id": s.id, "cleared": True, "alarms": []}

    async def rpc_identity(self) -> dict[str, Any]:
        """`ao identity` (design §4.8a): this host's mode, whether the detached-process check is on,
        the tally of connections by class and deciding signal since the agent started, and the
        alarms — the host's own and each record's. A never-gated read: it tells a session nothing
        it could not learn by trying."""
        return {
            "host": self.host,
            "mode": self.identity_mode,
            "detached_check": bool(self._id_detached),
            "tally": dict(sorted(self.identity_tally.items())),
            "alarms": list(self.identity_alarms),
            "sessions": {sid: list(s.identity_alarms) for sid, s in self.sessions.items() if s.identity_alarms},
        }

    async def rpc_whoami(self) -> dict[str, Any]:
        """Answered at the head of dispatch, where the channel is known; reached here only when
        identity is `off` or the call was made in-process."""
        return {"channel": None, "session": None, "signal": None}

    # -- helpers ---------------------------------------------------------------------------------

    def _gate(self, caller: Any, method: str, params: dict[str, Any]) -> None:
        """`mail.act_gate` over this agent's records (design §4.8, §9 invariants 5 and 11): a
        function over a record map, so a node can forward and the home can answer (§4.4a)."""
        if method == "create" and params.get("capabilities"):
            _grants(params["capabilities"])  # an unknown grant name is refused before the gate reads it
        reason = mail.act_gate(self._graph(), caller, method, params, controllers=self._ctl)
        if reason:
            raise RpcError(reason)

    def _addr(self, address: Any) -> str:
        """One normaliser for every id on the way in (design §4.4a, TD-057 step 1): a session on
        this host is stored bare, another host's as `id@host`."""
        return naming.qualify(str(address), local=self.host)

    def _get(self, sid: str) -> Session:
        try:
            return self.sessions[sid]
        except KeyError:
            rid, host = naming.split_address(sid)
            if host and rid in self.remote.get(host, {}):
                raise RpcError(f"{sid} runs on {host}: this call does not cross the link") from None
            raise RpcError(f"no session {sid}") from None

    def _find(self, sid: str) -> Session:
        """A record by id or by address — this host's, or another host's as the home holds it. For
        reads and for what the home owns; `_get` is for everything that touches a pane."""
        rid, host = naming.split_address(sid)
        if host and host != self.host:
            try:
                return self.remote[host][rid]
            except KeyError:
                raise RpcError(f"no session {sid}") from None
        return self._get(rid)

    def _save(self, s: Session) -> None:
        (self.store if s.host == self.host else self._remote_store(s.host)).save(s)

    def _remote_store(self, host: str) -> SessionStore:
        if host not in self._remote_stores:
            self._remote_stores[host] = SessionStore(paths.remote_dir(host))
        return self._remote_stores[host]

    # -- one org, to a client (design §4.4a "A node's records at the home") -----------------------

    def _view(self, s: Session, *, bookkeeping: bool = False) -> dict[str, Any]:
        """The view a client gets. This host's record is `s.view()`. Another host's carries its
        address as `id`, and while that host's link is down reads `unreachable` — an overlay on the
        view, never a state on the record."""
        v = s.view(bookkeeping=bookkeeping)
        if s.host == self.host:
            return v
        v["id"] = f"{s.id}@{s.host}"
        v["controllers"] = self._ctl(s)  # as this home addresses them
        state = self.links.get(s.host) or {"up": False, "since": None, "why": "not connected since the home started"}
        v["host_link"] = dict(state)
        if (sup := self.supervision.get(s.host)) and sup.get("doing"):
            v["host_link"]["supervisor"] = {k: sup[k] for k in ("doing", "since", "attempts")}
        if not state["up"]:
            v["last_state"], v["state"] = v["state"], "unreachable"
            if v.get("pending"):
                # §4.4a "Permission prompts follow the same line": the hook still blocks on its node
                # and nothing answered here can reach it, so the card says so and sends the person
                # to the tool's own dialog at that host. An overlay too — the record keeps its pending.
                v["pending"] = {**v["pending"], "host_unreachable": True}
        return v

    def _views(self) -> list[dict[str, Any]]:
        return [
            self._view(s) for s in (*self.sessions.values(), *(r for h in self.remote.values() for r in h.values()))
        ]

    def _remember_dir(self, directory: Path) -> None:
        p = paths.recent_dirs_file()
        lines = p.read_text().splitlines() if p.is_file() else []
        lines = [str(directory)] + [ln for ln in lines if ln != str(directory)]
        p.write_text("\n".join(lines[:20]) + "\n")

    # -- streaming -------------------------------------------------------------------------------

    async def _push_changes(self) -> None:
        """Send each subscriber what changed since *it* was last told. One payload per session is
        serialised once; the per-subscriber comparison is a string compare."""
        self._poke_waits()
        await self._report_home()  # a node tells its home, whether or not a browser is watching
        if self._intent_sent:
            await self._push_intent()  # and the home tells each node what it holds of its records
        gone, self._gone = self._gone, []
        if not self._subscribers:
            return
        # sort_keys: the payload is the comparison key too (the UI reads fields by name, never order)
        payloads = {v["id"]: json.dumps(v, sort_keys=True) for v in self._views()}
        # A push is a line like a reply, and one past the limit ends every open subscription at
        # once — every tab, not just the card that grew (TD-066). One card is dropped from the
        # stream instead, and named in the log; `last` is left untouched, so the card returns to
        # the stream the moment it fits again. Logged once per card, not once a tick.
        room = link.FRAME_LIMIT - len(PUSH_OPEN) - len(b"}\n")  # the envelope `_send` puts around it
        big = {sid for sid, payload in payloads.items() if len(payload.encode()) > room}
        for sid in big - self._oversize:
            log.error("the view of %s is past the %d-byte line limit: not pushed", sid, link.FRAME_LIMIT)
        for sid in self._oversize & (payloads.keys() - big):
            log.info("the view of %s fits again: pushing", sid)
        self._oversize = big  # so a record that has gone leaves the set with its view
        payloads = {sid: payload for sid, payload in payloads.items() if sid not in big}
        for w, last in list(self._subscribers.items()):
            for sid, payload in payloads.items():
                if last.get(sid) != payload:
                    last[sid] = payload
                    await self._send(w, PUSH_OPEN.decode() + payload + "}")
            for sid in gone:
                await self._send(w, json.dumps({"event": "gone", "id": sid}))

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg)
        for w in list(self._subscribers):
            await self._send(w, line)

    async def _send(self, w: asyncio.StreamWriter, line: str) -> None:
        try:
            w.write((line + "\n").encode())
            await w.drain()
        except (ConnectionError, OSError):
            self._subscribers.pop(w, None)

    # -- connection handling ---------------------------------------------------------------------

    async def _handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._conns.add(writer)
        try:
            while line := await reader.readline():
                try:
                    req = json.loads(line)
                except ValueError:
                    writer.write(b'{"error": "bad json"}\n')
                    continue
                if "link" in req and "method" not in req:
                    # sshd's forced command, announcing the host its key is bound to (§4.4a): from
                    # here on this connection is a link, not a client.
                    await self._serve_link(req["link"], reader, writer)
                    return
                if req.get("method") == "wait":
                    if writer in self._subscribers:
                        writer.write(b'{"error": "a wait runs on its own connection, never a subscribed one"}\n')
                        continue
                    await self._serve_wait(req, reader, writer)
                    continue
                if req.get("method") == "subscribe":
                    self._subscribers[writer] = {}  # empty: the first push is this tab's full snapshot
                    writer.write((json.dumps({"id": req.get("id"), "result": "subscribed"}) + "\n").encode())
                    await writer.drain()
                    for prof, u in self._usage.items():  # the top bar's figure, before the cards
                        await self._send(writer, json.dumps({"event": "usage", "profile": prof, "usage": u}))
                    await self._push_changes()
                    continue
                writer.write(_reply_line(req, await self._dispatch(req, peer=_peer_pid(writer), conn=writer)))
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._conns.discard(writer)
            self._id_conns.pop(writer, None)
            self._subscribers.pop(writer, None)
            writer.close()

    async def _serve_wait(
        self, req: dict[str, Any], reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """A `wait` holds its connection (design §4.10): the RPC runs while this reads the socket
        for the close. The moment the client goes away — a Ctrl-C, a cancelled turn — the wait is
        cancelled, so no ghost wait is left to be charged a wake and hand the mail to nobody.
        Requests are serial per connection; one sent mid-wait is answered with an error."""
        task = asyncio.ensure_future(self._dispatch(req, peer=_peer_pid(writer), conn=writer))
        try:
            while True:
                line = asyncio.ensure_future(reader.readline())
                done, _ = await asyncio.wait({task, line}, return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    # readline leaves unconsumed bytes in the buffer when cancelled, so a request
                    # already on its way is read by the connection loop after the reply
                    line.cancel()
                    with contextlib.suppress(asyncio.CancelledError, ConnectionError):
                        await line
                    writer.write(_reply_line(req, task.result()))
                    await writer.drain()
                    return
                try:
                    got = line.result()
                except (ConnectionError, asyncio.IncompleteReadError, ValueError):
                    got = b""
                if not got:
                    raise ConnectionResetError("the waiting client closed its connection")
                with contextlib.suppress(ValueError, AttributeError):
                    rid = json.loads(got).get("id")
                    writer.write(
                        (json.dumps({"id": rid, "error": "this connection is blocked in wait"}) + "\n").encode()
                    )
                    await writer.drain()
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def _dispatch(
        self, req: dict[str, Any], *, link_host: str | None = None, peer: int | None = None, conn: Any = None
    ) -> dict[str, Any]:
        """`link_host`: the request came over that node's link (step 5), so its caller is that
        host's session — `id@host` here — and never this socket's. `peer`: the pid at the other end
        of this host's own socket, from its credentials — what design §4.8a judges the envelope's
        `caller` against; None for a link (identified by its key, never classified) and for a call
        made in-process. `conn`: the connection, which is what is classified — once, for its life."""
        if peer is not None and link_host is None and self.identity_mode != "off":
            try:
                refused = await self._identify(req, peer, conn)
            except Exception:  # noqa: BLE001 — a bug in the check must never take the socket down with it
                # `observe` promises that nothing a caller sees changes, so the request is served as
                # before. Under `enforce` a check that can be made to fail would be a way round it, so
                # everything but a read is refused — and a person recovers with `identity: observe`
                # in hosts.yml, which needs no RPC.
                log.exception("identity check failed on %s (%s)", req.get("method"), self.identity_mode)
                refused = None
                if self.identity_mode == "enforce" and str(req.get("method") or "") not in identity.READS:
                    refused = {"id": req.get("id"), "error": identity.CHECK_FAILED}
            if refused is not None:
                return refused
        resp = await self._dispatch_inner(req, link_host=link_host)
        via_home = resp.pop("_via_home", False)
        caller = req.get("caller")
        if not mail.is_person(caller) and self.mode == "node" and not via_home and "mail" not in resp:
            # A read this node served alone (§4.4a, step 4b.2): the inbox is at the home, and the
            # count it last pushed is what the line says — a hint, as fresh as the link.
            unread, spent, owed = self._mail_hints.get(naming.split_address(str(caller))[0], (0, False, []))
            if unread or owed:
                resp["mail"] = {"unread": unread, "wake_budget_spent": spent}
                if owed:
                    resp["mail"]["owed"] = owed
        if not mail.is_person(caller) and self.mode == "home":
            # The line on every `ao` reply (design §4.10): the response to a session with unread
            # mail says so — result or refusal alike — read after the method ran, so an `ao inbox`
            # that just read everything carries no line. It types nothing and starts nothing.
            s = self._graph().get(self._caller_address(caller, link_host))
            owed = s.owed() if s is not None else []
            if s is not None and ((n := s.unread()) or owed):
                # The same line carries the debt (design §4.10 *Outcomes*): *briefs are skimmed, a
                # refusal is not*, and this is the cheapest thing that is neither.
                resp["mail"] = {"unread": n, "wake_budget_spent": s.wake_budget_spent()}
                if owed:
                    resp["mail"]["owed"] = owed
        return resp

    def _caller_address(self, caller: Any, link_host: str | None) -> str:
        """A request's identity comes from the channel it arrived on, never from a field it
        carries (design §4.4a): this socket is this host's, so any `@host` a client wrote is
        dropped and the caller is this host's bare id; a node's link is that node's, so the
        caller is `id@node` here."""
        bare = naming.split_address(str(caller))[0]
        return naming.qualify(f"{bare}@{link_host}", local=self.host) if link_host else bare

    async def _dispatch_inner(self, req: dict[str, Any], *, link_host: str | None = None) -> dict[str, Any]:
        rid = req.get("id")
        name = req.get("method")
        method = getattr(self, f"rpc_{name}", None)
        if method is None:
            return {"id": rid, "error": f"unknown method {name!r}"}
        if not isinstance(req.get("params") or {}, dict):
            # raw JSON from any local process: `"params": 5` used to raise outside the handler's
            # `try` and drop the connection without a reply (red-team of PR #248)
            return {"id": rid, "error": "params must be an object"}
        params = dict(req.get("params") or {})
        if ignored := _drop_unknown(method, params):
            # logged with the method, because the reply cannot tell a client newer than this agent
            # from a caller bug of the same age, and the second is worth finding in a log
            log.warning("rpc %s: ignored unknown params %s", name, ", ".join(ignored))
        caller = req.get("caller")
        if not mail.is_person(caller):
            caller = self._caller_address(caller, link_host)
        try:
            if self.mode == "node":
                # A node (design §4.4a, the call-by-call table): decided before the gate, because
                # the gate's graph is at the home. What the table refuses — the mailbox, reports,
                # home-owned edits, a session's acts on others — is forwarded to the home while
                # the link is up (step 5) and refused as unreachable while it is down; never
                # served from the replica. A `wait` goes to the home too: that is where the mail
                # and the other hosts' members are.
                refusal = modes.offline_refusal(str(name), caller, params, host=self.host, home=self.home)
                if refusal or name == "wait":
                    if not self.home_reachable():
                        raise RpcError(
                            refusal
                            or f"a wait sees the org at the home: {self.home} (home) is unreachable from {self.host}"
                        )
                    return {**await self._forward(rid, str(name), params, caller), "_via_home": True}
            self._gate(caller, str(name), params)
            if (target := self._act_host(str(name), params)) is not None:
                # Another host's record (design §4.4a, step 4a): gated above over the one graph,
                # executed by that host's node, its verdict returned — or refused as unreachable.
                if name in NODE_READS:
                    return {"id": rid, "result": await self._route_read(str(name), params, target)}
                return {"id": rid, "result": await self._route_act(str(name), params, caller, target)}
            if "caller" in inspect.signature(method).parameters:
                # The methods that need to know who called (`create` seeds the new record's
                # controllers with its creator; `send` and `keys` record who typed; `msg` and
                # `inbox` are the caller's own) get the envelope's caller, set unconditionally and
                # after the gate: a client that put its own `caller` in `params` does not get to
                # choose who it is.
                params["caller"] = caller
            resp: dict[str, Any] = {"id": rid, "result": await method(**params)}
        except RpcError as e:
            resp = {"id": rid, "error": str(e), **({"error_data": e.data} if e.data else {})}
        except TypeError as e:
            resp = {"id": rid, "error": f"bad params: {e}"}
        except Exception as e:  # noqa: BLE001
            log.exception("rpc %s failed", req.get("method"))
            resp = {"id": rid, "error": f"{type(e).__name__}: {e}"}
        if ignored:
            resp["ignored"] = ignored
        return resp


def backup_store(day: str) -> Path | None:
    """`backups/store-<day>.tar.gz` of `BACKUP_MEMBERS` under AGENTORC_HOME (design §4.4a "When
    the home is lost", TD-057 step 4b.3), mode `0600`, written under a temporary name and renamed
    so a half-written tarball never counts as one; the newest `BACKUP_KEEP` kept. Regular files
    only — a socket, a symlink or anything else found among them is not followed. None when
    today's already exists. Blocking: run it in a thread."""
    out_dir = paths.backups_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    target = out_dir / f"store-{day}.tar.gz"
    if target.exists():
        return None
    home = paths.home()
    tmp = out_dir / f".store-{day}.tar.gz.part"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh, tarfile.open(fileobj=fh, mode="w:gz") as tar:
            for member in BACKUP_MEMBERS:
                top = home / member
                for f in sorted([top] if top.is_file() else top.rglob("*") if top.is_dir() else []):
                    if f.is_file() and not f.is_symlink():
                        tar.add(f, arcname=str(f.relative_to(home)), recursive=False)
        os.chmod(tmp, 0o600)
        tmp.rename(target)
    finally:
        tmp.unlink(missing_ok=True)
    for old in sorted(out_dir.glob("store-*.tar.gz"))[:-BACKUP_KEEP]:
        old.unlink(missing_ok=True)
    return target


def read_checkout(directory: Any, rel_paths: Any) -> dict[str, Any]:
    """Files inside one checkout, by their paths relative to it (design §4.4a "Teams across
    hosts", step 4b.3): `{dir, files: {path: text, or None when there is no such file}}`. A file
    read across a trust boundary, so everything is refused rather than guessed: a directory that
    is not one, an absolute path or one that climbs out, a path — symlinks followed — that
    resolves outside the checkout, anything but a regular file, a file over `FILE_CAP` bytes, and
    more than `FILES_MAX` paths. Blocking: run it in a thread."""
    if not directory or not str(directory).strip():
        raise ValueError("no checkout named")
    try:
        root = Path(str(directory)).expanduser().resolve(strict=True)
    except OSError:
        raise ValueError(f"{directory} does not exist here") from None
    if not root.is_dir():
        raise ValueError(f"{directory} is not a directory")
    if not (root / ".git").exists():
        # a team's checkout is a repo (a worktree's `.git` is a file): a directory that is not one
        # — a home directory, `~/.ssh` — is not read, whoever asks (review of PR #224)
        raise ValueError(f"{directory} is not a git checkout")
    wanted = [str(p) for p in (rel_paths or [])]
    if len(wanted) > FILES_MAX:
        raise ValueError(f"{len(wanted)} files asked for: at most {FILES_MAX} in one call")
    out: dict[str, str | None] = {}
    for rel in wanted:
        if not rel.strip() or Path(rel).is_absolute():
            raise ValueError(f"{rel!r}: a path relative to the checkout, please")
        full = (root / rel).resolve()  # symlinks followed, then checked: a link out is refused like `..`
        if not full.is_relative_to(root):
            raise ValueError(f"{rel}: outside the checkout {root}")
        if not full.exists():
            out[rel] = None
            continue
        out[rel] = _read_capped(full, rel)
    return {"dir": str(root), "files": out}


def _read_capped(full: Path, rel: str) -> str:
    """One file, judged and read through one descriptor (review of PR #224): opened without
    blocking and without following a final symlink swapped in since the check, then `fstat` says
    whether it is a regular file, and no more than `FILE_CAP` + 1 bytes are ever read — so a file
    replaced by a FIFO, or grown, between the check and the read is refused rather than trusted."""
    try:
        fd = os.open(full, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except OSError as e:
        raise ValueError(f"{rel}: cannot be read ({e.strerror or e})") from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError(f"{rel}: not a regular file")
    with os.fdopen(fd, "rb") as fh:
        data = fh.read(FILE_CAP + 1)
    if len(data) > FILE_CAP:
        raise ValueError(f"{rel}: over {FILE_CAP} bytes, which is not a repo config or a brief")
    return data.decode("utf-8", errors="replace")


def _drop_unknown(method: Any, params: dict[str, Any]) -> list[str]:
    """Design §4.4, TD-062 fix (b): remove the parameters `method` does not take and name them,
    so a client newer than this host agent degrades instead of failing.

    The other half of the skew rule — a client never sends a parameter it has not set (§4.4,
    `LocalClient.call`) — means a dropped parameter is always one the caller *did* set, i.e. a
    feature this agent predates. The call still runs, without it, and the reply says which ones
    went: `ao` prints that line, so the skew is visible rather than silent. A method that takes
    `**kwargs` (`rpc_hook`) accepts everything and nothing is dropped.
    """
    sig = inspect.signature(method).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.values()):
        return []
    dropped = sorted(k for k in params if k not in sig)
    for k in dropped:
        del params[k]
    return dropped


_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")  # title sets etc.
_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ESC_OTHER = re.compile(r"\x1b[ -/]*[0-~]")  # remaining ESC sequences (charset, keypad, …)


def _prune_tallies(r: Session) -> None:
    """A thread's tally lives as long as the record holds an entry of it (§4.10): pruned with its
    last entry — by retention or by a person's delete — or it would grow by one key per message
    forever (TD-066). A reply must name an entry the replier holds, so a pruned thread cannot come
    back. Pair tallies are windowed by `_pair` and stay."""
    held = {e.root for e in (*r.inbox, *r.outbox)}
    for key in [k for k in r.threads if not k.startswith("pair:") and k not in held]:
        del r.threads[key]


def _renamed_grants(s: Session, where: str) -> bool:
    """A copy that arrived with a renamed grant's old name (TD-055) was normalised as it was read;
    say so, as the loader does, and tell the caller to save it. True when there was one."""
    renamed = getattr(s, "renamed_grants", None)
    if not renamed:
        return False
    log.warning(
        "%s: grant %s is now `control` (TD-055), from %s; the record is rewritten", s.id, ", ".join(renamed), where
    )
    del s.renamed_grants
    return True


def _urgent(s: Session) -> str:
    """What a node reports at once when it moves (§4.4a): the rest waits for `REPORT_EVERY`."""
    return json.dumps([s.state, s.exit_code, s.pane, s.pending.to_dict() if s.pending else None])


def _controllers(ids: list[Any]) -> list[str]:
    """Session ids for a `controllers` list (design §4.8): stripped, deduped, order kept. Ids are
    not checked against live records on purpose — a controller that has exited keeps its entry
    (§4.8: an exited lead's workers are surfaced, not silently released), and a list may
    be set before the session it names is created."""
    out: list[str] = []
    for raw in ids:
        cid = str(raw).strip()
        if not cid:
            raise RpcError("a controller is a session id, not an empty string")
        if cid not in out:
            out.append(cid)
    return out


def _lane(refs: list[str]) -> list[str]:
    """The references a session was handed (design §4.8), in the order given and each canonical, so
    a lane item and the session's own claim are the same string. `free-pick` is a lane of its own."""
    if [r for r in refs if str(r).strip() == "free-pick"]:
        if len(refs) > 1:
            raise RpcError("a lane is either `free-pick` or a list of references, not both")
        return ["free-pick"]
    # deduped after canonicalisation: `--lane TD-027,td-27` is one item, or the lane count the card
    # shows (*1 of 2*) would be a lie about how much work there is (review 2026-09-11)
    return list(dict.fromkeys(_ref(r) for r in refs))


def _ref(ref: str) -> str:
    try:
        return normalize_ref(ref)
    except ValueError as e:
        raise RpcError(str(e)) from None


def _pr(pr: Any) -> int | None:
    """A PR number, however it was typed (`59`, `"#59"`, a URL's tail) — or an error, never a lie."""
    if pr is None or pr == "":
        return None
    try:
        return int(str(pr).lstrip("#"))
    except ValueError:
        raise RpcError(f"not a PR number: {pr!r}") from None


def _source(source: str) -> str:
    if source not in SOURCES:
        raise RpcError(f"unknown report source {source!r}; sources are: {', '.join(SOURCES)}")
    return source


def _grants(names: list[str]) -> list[str]:
    """Validate a list of grant names against `GRANTS`, in canonical order. A renamed grant's old
    name is accepted as the new one for a release (`GRANT_ALIASES`, TD-055)."""
    names = canonical_grants(list(names))
    bad = [n for n in names if n not in GRANTS]
    if bad:
        raise RpcError(f"unknown grant {', '.join(map(str, bad))}; grants are: {', '.join(GRANTS)}")
    return [g for g in GRANTS if g in names]


async def _falsy(coro: Any) -> bool:
    """`not await coro`: a composer that reads empty ("") or unreadable (None) counts as emptied."""
    return not await coro


def _pane_title(adapter: Any, pane_title: str) -> str | None:
    """The session's name as its tool holds it (design §4.5a **title**, TD-074): the pane's terminal
    title handed to the session's adapter, which alone knows what of it is a name (§4.3 `title()`).
    An adapter without the method — `shell`, a command run — gives none, and so does a broken one:
    a title is a display, and no tick is lost over one. Cleaned as a tail is, and shorter."""
    reader = getattr(adapter, "title", None)
    if reader is None:
        return None
    try:
        name = reader(pane_title or "")
    except Exception:  # noqa: BLE001 — one adapter's title never costs the tick
        log.exception("adapter %s: title() failed", getattr(adapter, "name", adapter))
        return None
    return (_clean(str(name)).strip()[:TITLE_CAP] or None) if name else None


def _clean(text: str) -> str:
    """Strip ANSI/control bytes and cap width: pane output is untrusted everywhere but xterm.js."""
    text = _OSC.sub("", text)
    text = _CSI.sub("", text)
    text = _ESC_OTHER.sub("", text)
    text = "".join(ch for ch in text if ch == "\t" or ch >= " ")
    return text[:200]


_LINE_BREAKS = re.compile("[\n\r\x0b\x0c\x85\u2028\u2029]")


def _clean_answer(text: Any) -> str:
    """One suggested answer, cleaned **more strictly than displayed text is** (design §4.10
    *Suggested answers*, TD-070): the tail's cleaning (`_clean` — ANSI, bytes under U+0020) and
    also **every Unicode format character** (category `Cf`: the bidi overrides and isolates, the
    zero-width marks), because an answer becomes the label of something a person presses and a
    label that can reorder or hide its own letters can look like what it is not. One line — the
    first, taken before `_clean`, which would otherwise drop the newline and run two lines
    together — stripped, and capped at `ANSWER_CAP`.

    Two things this costs and one it does not cover, so nobody reads it as more: a zero-width
    joiner is `Cf` too, so a multi-part emoji falls apart in a label, and a soft hyphen goes —
    both accepted; and look-alike letters from another script are not addressed — the quoted,
    separately grouped drawing of §4.5a is what answers those, not the cleaning.

    `_clean` itself is untouched: displayed text stays displayed text, and this is the label rule."""
    # Every line break a renderer honours ends the label, not `\n` alone: `\r`, NEL (U+0085, a
    # control `_clean` keeps because it is above U+0020), and the line and paragraph separators
    # (U+2028, U+2029: categories Zl and Zp, so the `Cf` strip below does not see them). And the
    # work is bounded by the cap, not by what was sent: a 10 MB item costs what 320 characters do.
    raw = str(text or "")[: mail.ANSWER_CAP * 4]
    line = _clean(_LINE_BREAKS.split(raw, 1)[0])
    line = "".join(ch for ch in line if unicodedata.category(ch) != "Cf")
    return line.strip()[: mail.ANSWER_CAP]


def _cap(usage: dict[str, Any] | None) -> str | None:
    """The pending text for a capped profile, or None. A window whose `resets_at` has passed is
    not a cap any more even before the next poll says so."""
    if not usage:
        return None
    now = datetime.now(UTC)
    for key, label in (("five_hour", "5-hour"), ("weekly", "weekly")):
        try:
            pct = int(usage.get(f"{key}_pct") or 0)
        except (TypeError, ValueError):
            continue
        resets = usage.get(f"{key}_resets")
        if pct < 100:
            continue
        try:
            at = _parse(str(resets)) if resets else None
        except (TypeError, ValueError):
            at = None  # unparseable: still a cap, reset time unknown
        if at is not None and at <= now:
            continue
        return f"{label} cap · resets {at.strftime('%H:%MZ') if at else 'unknown'}"
    return None


def _stop_time(value: str | None) -> str | None:
    """An ISO stop time, normalised to UTC, or None. A malformed one is an error at create rather
    than a session nothing ever stops (design §6, TD-026) — the clients do the friendly parsing of
    `06:00` and `+8h`, because "next 06:00" is a question about the *caller's* clock and this
    package only ever deals in absolute instants."""
    if not value:
        return None
    try:
        when = _parse(str(value).strip())
    except ValueError as exc:
        raise RpcError(f"run_until: not a time: {value}") from exc
    if when.tzinfo is None:
        raise RpcError(f"run_until: needs a timezone (got {value})")
    return when.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


async def serve_until_signal(agent: HostAgent, sock: Path | None = None) -> None:
    """Run `agent.serve()` until SIGINT or SIGTERM.

    The signal cancels the serve task rather than stopping the loop, so `serve()`'s `finally`
    (cancel the ticker, unlink the socket file) runs while the loop is still alive and the
    process ends without asyncio's "Task was destroyed but it is pending!" (TD-024).
    """
    # Run this under `asyncio.run`: its shutdown awaits every task still on the loop (the
    # cancelled ticker, a live usage refresh), which is what keeps them from being destroyed pending.
    task = asyncio.ensure_future(agent.serve(sock))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    try:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agentorc-agent", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the host agent (foreground)")
    sub.add_parser("rpc", help="stdin/stdout JSON-lines bridge to the local socket (used over ssh)")
    lk = sub.add_parser(
        "link",
        help="a node's link into this home: the forced command of the node's key in authorized_keys (design §4.4a)",
    )
    lk.add_argument("--host", default="", help="the node's host name — set in authorized_keys, never by the node")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "serve":
        asyncio.run(serve_until_signal(HostAgent()))
        return 0
    if args.cmd == "rpc":
        from sessionorc.client import bridge_stdio

        return asyncio.run(bridge_stdio())
    if args.cmd == "link":
        return asyncio.run(link.bridge(args.host))
    return 2


if __name__ == "__main__":
    sys.exit(main())
