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
import sys
import time
from collections import OrderedDict, defaultdict
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sessionorc import adapters, containers, hosts, link, mail, modes, naming, paths, reports, waits
from sessionorc.gitinfo import WorktreeError, ensure_worktree, git_info
from sessionorc.mail import ACTING_RPCS  # noqa: F401 — re-exported: callers read it from the agent
from sessionorc.models import (
    ASK_KINDS,
    GRANTS,
    MAIL_KINDS,
    PERSON,
    PROGRESS_STATUSES,
    SOURCES,
    FindingEntry,
    MailEntry,
    NotTheSameSession,
    Pending,
    ProgressEntry,
    SendEntry,
    Session,
    State,
    Tally,
    apply_node,
    canonical_grants,
    normalize_ref,
    now_iso,
)
from sessionorc.store import EventQueue, PersonInboxStore, SessionStore
from sessionorc.tmux import DuplicateSession, PaneInfo, Tmux

log = logging.getLogger("agentorc.agent")

TICK_SECONDS = float(os.environ.get("AGENTORC_TICK", "2"))
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
SETTLED = ("idle", "needs-you", "exited", "closed", "limited", "stalled?")  # where a `send(wait=True)` ends
# `ACTING_RPCS` lives in `sessionorc.mail` beside the gates, and is re-exported here for the
# callers that always read it from the agent.
REMOVED_GUARD_SECONDS = 60.0  # how long a removed session's name is checked against re-adoption
PRUNE_EVERY = timedelta(hours=1)  # run-log retention sweep (design §4.6, `runs_keep_days`)
# A session past its `run_until` is asked to wrap up and then killed (design §6, TD-026): this is how
# long it is given to finish after the ask. It is a grace, not a deadline the session can see — a
# session that settles sooner is killed sooner, and one that is still working when it runs out is
# killed anyway, because the whole point is that nobody is watching.
WRAPUP_GRACE = timedelta(minutes=10)
REPORT_WRITE = 5.0  # seconds a node's report may take to write before the link is given up
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


class HostAgent:
    def __init__(
        self, *, tmux: Tmux | None = None, store: SessionStore | None = None, events: EventQueue | None = None
    ):
        paths.ensure_layout()
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
        # A node's side of the same: what it last told the home about each record, and when.
        self._reported: dict[str, tuple[str, str, float]] = {}
        self._snapshot_sent = False
        self._bg: set[asyncio.Task[None]] = set()  # fire-and-forget tasks, held so they are not collected
        if self.mode == "node":
            log.warning(
                "node of %s: this host's sessions only until the link is up, and until TD-057 steps 4–5 "
                "forward them — mail, reports, home-owned edits and sessions' acts on others are refused. "
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
        ):
            side.pop(sid, None)

    def _forget(self, sid: str) -> None:
        gone = self.sessions.get(sid)
        if gone is None:
            return  # already forgotten (two removes of one id in flight): nothing more to announce
        # Its open `ask`s expire with it (design §4.10 lifecycle): the record and its inbox go, and
        # every other holder of those asks — the askers — is told so. Done while it is still in the
        # map so `_mark` reaches it, harmlessly, along with the rest.
        for e in [e for e in gone.inbox if e.open]:
            self._mark(e.id, expired_at=now_iso())
        self.sessions.pop(sid, None)
        self.store.delete(sid)
        # Scrub the id from every subscriber's map and queue the one `gone`: whichever
        # `_push_changes` runs next (the caller's or a tick's) announces it exactly once.
        for last in self._subscribers.values():
            last.pop(sid, None)
        self._scrub(sid)
        self._gone.append(sid)

    # -- RPC methods -----------------------------------------------------------------------------

    async def rpc_list(self) -> list[dict[str, Any]]:
        return self._views()

    async def rpc_get(self, id: str) -> dict[str, Any]:
        return self._view(self._find(id))

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
        caller: str | None = None,
    ) -> dict[str, Any]:
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

    async def rpc_name_check(self, dir: str, name: str, repo: str | None = None) -> dict[str, Any]:
        """What §4.1's name rule would do to this name, without doing it: the New session form's
        check as you type, and the note `ao new` prints (design §4.5a, TD-030 step 4)."""
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
        for e in entries:
            e.to = [new if x == old else x for x in e.to]
            e.copies = [new if x == old else x for x in e.copies]
            e.pending = [x for x in e.pending if x != old]
        return entries

    def occupants(self, directory: Path) -> list[str]:
        """Who holds the agent slot for `directory` (design §9 invariant 2): agentorc's own live
        agent sessions, plus live sessions the adapters can see that agentorc did not start
        (a VS Code terminal running `claude` in the checkout, say). Shells never count."""
        directory = Path(directory).resolve()
        ours = {s.adapter_id for s in self.sessions.values() if s.adapter_id}
        out = [
            f"{s.id} ({s.state})"
            for s in self.sessions.values()
            if s.kind == "interactive"
            and s.adapter != "shell"
            and s.state not in ("exited", "closed")
            and Path(s.dir).resolve() == directory
        ]
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
        s = self._get(id)
        s.unattended = bool(unattended)
        self.store.save(s)
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
        s = self._get(id)
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
        self.store.save(s)
        await self._push_changes()
        return s.view()

    async def rpc_set_grants(
        self, id: str, add: list[str] | None = None, remove: list[str] | None = None
    ) -> dict[str, Any]:
        """`ao grant` / `ao revoke`, the Focus grants chip (design §4.8): edit `capabilities`. Takes
        effect on the target's next call — the gate reads the record, not a cached copy."""
        s = self._get(id)
        adding, removing = _grants(add or []), _grants(remove or [])
        s.capabilities = [g for g in GRANTS if (g in s.capabilities or g in adding) and g not in removing]
        self.store.save(s)
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
        s = self._get(id)
        adding = [self._addr(c) for c in _controllers(add or [])]
        removing = [self._addr(c) for c in _controllers(remove or [])]
        if s.id in adding:
            raise RpcError(f"{s.id} cannot be its own controller: it could then drop the ones watching it")
        # One call naming an id in both `add` and `remove` drops it: remove wins, as it already
        # does for grants (`rpc_set_grants`'s `and g not in removing`). Two authority-editing RPCs
        # that disagree on the ambiguous call is how one of them eventually surprises someone, and
        # of the two answers the safe one is the one that takes authority away (review 2026-09-13).
        s.controllers = _controllers([c for c in s.controllers + adding if c not in removing])
        self.store.save(s)
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
        s = self._get(id)
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
        for o in self.sessions.values():
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
        s.out_of_work = {"at": now_iso(), "why": why.strip()}
        return await self._report(s, True, None)

    async def rpc_finding(
        self, id: str, ref: str, priority: str | None = None, source: str = "declared"
    ) -> dict[str, Any]:
        """`ao finding <ref> [--priority …]` (design §4.8): a reference this session filed on the
        side. Ungated like `progress`, and upserted by reference the same way."""
        s = self._get(id)
        entry = FindingEntry(ref=_ref(ref), priority=priority, source=_source(source))
        return await self._report(s, s.report_finding(entry), entry)

    async def _report(self, s: Session, applied: bool, entry: Any) -> dict[str, Any]:
        """Save and announce a report entry the record accepted. A refused one (§9 invariant 10: a
        `derived` or `scraped` entry over a `declared` one) is not an error — the caller gets the
        record as it stands, with `refused` naming the entry that did not land."""
        if applied:
            self.store.save(s)
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
        nonce: str | None = None,
        caller: Any = None,
    ) -> dict[str, Any]:
        """`ao msg <to>… "…" [--kind] [--about] [--reply-to]` (design §4.10): put an attributed
        entry in each addressee's inbox. Nothing is typed anywhere. Gated by §4.10's graph, never
        by invariant 11 — messaging is not acting — and bounded as that section lists: a recipient
        cap on the addressees the sender named, all or nothing across them, a copy to the other
        controllers of the session it is `about` (exempt from both), the exchange bound per thread
        and per pair, the mailbox depth, the body cap, and an `ask`'s wall-clock bound. `bound` is
        the `ask`'s, in seconds, else `mail.ASK_BOUND`. A retry carrying the same `nonce` returns
        the first send's verdict. The reply names what landed, what was copied, and what was
        forwarded to a resumed successor."""
        sender = PERSON if mail.is_person(caller) else str(caller)
        key = (sender, str(nonce)) if nonce else None
        if key and key in self._nonces:
            result, error = self._nonces[key]
            if error is not None:
                raise error
            return dict(result or {})
        try:
            result = await self._msg(sender, text, to, kind, about, reply_to, bound, cites)
        except RpcError as e:
            if key:
                self._remember_nonce(key, (None, e))
            raise
        if key:
            self._remember_nonce(key, (result, None))
        return result

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
    ) -> dict[str, Any]:
        """One message, every rule of §4.10 in the order it applies. Long on purpose: the order is
        the design (validate, resolve the thread, forward, gate all-or-nothing, cap, count, land)."""
        records = self.sessions
        if kind not in MAIL_KINDS:
            raise RpcError(f"unknown message kind {kind!r}; kinds are: {', '.join(MAIL_KINDS)}")
        text = str(text or "").strip()
        if not text:
            raise RpcError("a message needs a body")
        if len(text.encode()) > mail.TEXT_CAP:
            raise RpcError(
                f"message body over {mail.TEXT_CAP} bytes: cite a `sends` id or a reference instead (design §4.10)"
            )
        me = records.get(sender) if sender != PERSON else None
        if sender != PERSON and me is None:
            raise RpcError(f"{sender} cannot send mail: this host agent has no record of it (design §4.10)")
        named = [self._addr(x) for x in ([to] if isinstance(to, str) else list(to or [])) if str(x).strip()]
        named = list(dict.fromkeys(named))
        if PERSON in named and sender == PERSON:
            raise RpcError("the person inbox is how a session reaches a person; a person's own note is a board line")
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
            if not named:
                if replied.from_ == PERSON and sender == PERSON:
                    raise RpcError(f"{reply_to} is a person's own message: name the addressee")
                named = [replied.from_]  # a session answering a person answers into the person inbox
            # replies in a copied thread are copied to the same set (design §4.10): the thread's
            # copies — a copy that failed to land included, so the set is the one meant — and its
            # other addressees, so the other lead of a `conflict` sees how it was settled
            same_set = dict.fromkeys([*replied.copies, *replied.copies_failed, *replied.to, replied.from_])
            copies = [x for x in same_set if x not in (sender, PERSON, *named)]
        if not named:
            raise RpcError("a message names its addressees: there is no broadcast (design §4.10)")
        if len(named) > mail.RECIPIENT_CAP:
            raise RpcError(
                f"{len(named)} addressees is more than the cap of {mail.RECIPIENT_CAP} (design §4.10: no broadcast)"
            )
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
            if (reason := mail.message_gate(records, sender, sid)) is not None:
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
        if subject is not None and sender in subject.controllers:
            copies = [c for c in subject.controllers if c not in (sender, *named)]
        copies = [c for c in copies if c in records]
        # -- what this message counts as ------------------------------------------------------------
        closes = replied is not None and kind == "reply" and replied.open
        counts = sender != PERSON and not closes  # a person's message is never counted; a first reply is free
        root = replied.root if replied is not None else ""
        now = datetime.now(UTC)
        if counts and mail.THREAD_BOUND is not None:
            self._check_bounds(sender, named, root, now)
        if PERSON in named:
            self._check_person_depth(sender)
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
        if kind in ASK_KINDS:
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
            self._mark(replied.id, closed_by=mid, closed_at=at)
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
            self.store.save(r)
        await self._push_changes()
        now = datetime.now(UTC)
        return {
            # exhaustion is visible to the sender (design §4.10): the mail landed, and it wakes nobody
            "wake_budget_spent": [
                sid for sid in (*named, *landed) if sid != PERSON and mail.wake_budget_spent(records[sid], now)
            ],
            "entry": entry.to_dict(),
            "delivered": list(named),
            "copies": landed,
            "copies_failed": failed,
            "forwarded": forwarded,
            "closed": replied.id if closes and replied is not None else None,
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
        limit = mail.THREAD_BOUND
        assert limit is not None
        records = self.sessions
        me = records[sender]
        if root:
            at_bound = [
                sid
                for sid in (sender, *named)
                if sid != PERSON and records[sid].threads.get(root, Tally()).count >= limit
            ]
            if at_bound:
                for r in records.values():
                    if root in r.threads:
                        r.threads[root].bound_hit = True
                        self.store.save(r)
                raise RpcError(
                    f"thread {root} is at its bound of {limit} entries ({', '.join(at_bound)}): the send is "
                    f"refused — write the user_attention.md line yourself, with the thread attached (design §4.10)"
                )
            return
        for sid in named:
            if sid == PERSON:
                continue
            mine, theirs = self._pair(me, sid, now), self._pair(records[sid], sender, now)
            if mine.count >= limit or theirs.count >= limit:
                mine.bound_hit = theirs.bound_hit = True
                self.store.save(me)
                self.store.save(records[sid])
                raise RpcError(
                    f"{sender} and {sid} have exchanged {limit} messages replying to nothing inside "
                    f"{mail.PAIR_WINDOW}: the send is refused — write the user_attention.md line yourself "
                    f"(design §4.10)"
                )

    def _person_holds(self, msg_id: str) -> list[MailEntry]:
        """Where a person's `--reply-to` looks: the person inbox first (a session's message to the
        person), then every session's copies (a person answering from a session's Inbox panel)."""
        return [e for e in self.person_inbox if e.id == msg_id] + [
            e for r in self.sessions.values() for e in r.holds(msg_id)
        ]

    def _check_person_depth(self, sender: str) -> None:
        """The person inbox's depth and per-sender depth (design §4.10): it fills exactly when the
        person has been away, so the refusal is a redirect to the channel with a `Due:` date."""
        unread = [e for e in self.person_inbox if not e.read_at]
        full = None
        if mail.PERSON_INBOX_DEPTH is not None and len(unread) >= mail.PERSON_INBOX_DEPTH:
            full = f"the person inbox holds {mail.PERSON_INBOX_DEPTH} unread entries"
        elif mail.PERSON_SENDER_DEPTH is not None and (
            sum(1 for e in unread if e.from_ == sender) >= mail.PERSON_SENDER_DEPTH
        ):
            full = f"the person inbox holds {mail.PERSON_SENDER_DEPTH} unread entries from {sender}"
        if full:
            raise RpcError(
                f"{full}: the person is away — write the line on user_attention.md with a Due: date, "
                f"the channel that reaches an absent person (design §4.10)"
            )

    def _mark(self, msg_id: str, **fields: Any) -> None:
        """Write the same fact on every copy of one message, so both cards show it: the home is
        the one writer (design §4.4a). `pending=<id>` appends to the list; anything else is set."""
        for r in self.sessions.values():
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
            self.store.save(r)
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
                        {**e.to_dict(), "from_role": mail.from_role(self.sessions, PERSON, e.from_)} for e in held
                    ],
                    "threads": {},
                    "sends": [],
                    "unread": sum(1 for e in self.person_inbox if not e.read_at),
                }
            s = self._get(self._addr(id))
            mark = False
        else:
            me = self._addr(caller)
            if id and self._addr(id) != me:
                raise RpcError(f"{me} cannot read {id}'s inbox: nobody reads another session's inbox (design §4.10)")
            s = self._get(me)
            mark = True
        entries = [e for e in s.inbox if not (unread and e.read_at)]
        if mark and any(not e.read_at for e in entries):
            at = now_iso()
            for e in entries:
                e.read_at = e.read_at or at
            self.store.save(s)
            await self._push_changes()
        return {
            "id": s.id,
            "entries": [{**e.to_dict(), "from_role": mail.from_role(self.sessions, s.id, e.from_)} for e in entries],
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
        sender's copy stays there too."""
        if not mail.is_person(caller):
            raise RpcError(
                f"{caller} cannot delete mail: an entry is deleted only by a person, in the Inbox panel (design §4.10)"
            )
        if not id or id == PERSON:
            kept = [e for e in self.person_inbox if e.id != msg]
            if len(kept) == len(self.person_inbox):
                raise RpcError(f"the person inbox holds no entry {msg}")
            self.person_inbox = kept
            self.person_store.save(kept)
            return {"id": PERSON, "deleted": msg, "unread": sum(1 for e in kept if not e.read_at)}
        s = self._get(self._addr(id))
        kept = [e for e in s.inbox if e.id != msg]
        if len(kept) == len(s.inbox):
            raise RpcError(f"{s.id}'s inbox holds no entry {msg}")
        s.inbox = kept
        self.store.save(s)
        await self._push_changes()
        return {"id": s.id, "deleted": msg, "unread": s.unread()}

    async def _sweep_mail(self, now: datetime) -> None:
        """Once a tick: an `ask` past its bound expires on every copy; an addressee that exited
        leaves the `ask`s addressed to it pending, a closed one expires them (design §4.10
        lifecycle); read entries past retention are pruned, open asks exempt."""
        stamp = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        for r in list(self.sessions.values()):
            for e in list(r.inbox):
                if not e.open:
                    continue
                if (e.bound and _parse(e.bound) <= now) or r.state == "closed":
                    self._mark(e.id, expired_at=stamp)
                elif r.state == "exited" and r.id not in e.pending:
                    self._mark(e.id, pending=r.id)
            for e in list(r.outbox):
                if e.open and e.bound and _parse(e.bound) <= now:
                    self._mark(e.id, expired_at=stamp)
        for e in list(self.person_inbox):  # an `ask` to the person expires on its bound like any other
            if e.open and e.bound and _parse(e.bound) <= now:
                self._mark(e.id, expired_at=stamp)
        if mail.MAIL_RETENTION is None:
            return
        kept = [e for e in self.person_inbox if self._keep(e, now, inbox=True)]
        if len(kept) != len(self.person_inbox):
            self.person_inbox = kept
            self.person_store.save(kept)
        for r in self.sessions.values():
            inbox = [e for e in r.inbox if self._keep(e, now, inbox=True)]
            outbox = [e for e in r.outbox if self._keep(e, now, inbox=False)]
            if len(inbox) != len(r.inbox) or len(outbox) != len(r.outbox):
                r.inbox, r.outbox = inbox, outbox
                self.store.save(r)

    @staticmethod
    def _keep(e: MailEntry, now: datetime, *, inbox: bool) -> bool:
        """Lifecycle stage 3 (design §4.10): a read entry is kept for the retention window from
        `read_at` — or, for an `ask`, from when it closed or expired — and an open `ask` is never
        pruned. An unread inbox entry never ages out. The sender's copy runs from `at`."""
        if e.open or mail.MAIL_RETENTION is None:
            return True
        since = e.expired_at or e.closed_at or (e.read_at if inbox else e.at)
        if since is None:
            return True
        return _parse(since) + mail.MAIL_RETENTION > now

    # -- waking (design §4.8 "Waking a lead", §4.10 "The host agent decides each wake") ----------

    async def rpc_wait(self, timeout: float = 600.0, scope: str = "controlled", caller: Any = None) -> dict[str, Any]:
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
                changed, cursor = waits.wake_changes(before, waits.wait_scope(views, who, scope))
                s = self.sessions.get(who) if who is not None else None
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
            "from_role": mail.from_role(self.sessions, s.id, e.from_),
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
        if not member_change and mail.wake_budget_spent(s, now):
            return None
        decision = {
            "at": now.isoformat(timespec="microseconds"),
            "cause": "member" if member_change else "mail",
            "charged": not member_change,
            "covered": len(fresh),
        }
        s.wakes = (s.wakes + [decision])[-mail.WAKES_KEEP :]
        s.mail_decided = {"id": fresh[-1].id, "at": fresh[-1].at}
        self.store.save(s)
        return {**decision, "entries": fresh}

    def _refill(self, s: Session | None) -> None:
        """A person's act toward `s` restores its wake budget in full (design §4.10 "Time and a
        person restore it"): a send or keys with no caller, a person's message, a decided
        permission. Never a session's traffic, never a person looking (`seen`, a panel read)."""
        if s is None:
            return
        s.wake_refilled_at = datetime.now(UTC).isoformat(timespec="microseconds")
        self.store.save(s)
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
            if state and state["up"]:
                if self.supervision.pop(name, None) is not None:
                    self._note_link(name)
                continue
            sup = self.supervision.setdefault(
                name, {"doing": "", "since": now_iso(), "attempts": 0, "next": 0.0, "error": ""}
            )
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
        why = (self.links.get(n.name) or {}).get("why", "")
        decision = containers.decide(cstate, alive, str(why))
        if decision is None:
            # running, the agent alive, the link simply not up yet: give it the grace, then look again
            sup["next"] = time.monotonic() + containers.SUPERVISE_GRACE
            waiting = "agent running inside, waiting for it to dial in"
            if sup["doing"] != waiting:
                sup["doing"], sup["since"], sup["error"], sup["attempts"] = waiting, now_iso(), "", 0
                self._note_link(n.name)
            return
        action, doing = decision
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
        now, changed = time.monotonic(), []
        for s in self.sessions.values():
            urgent, payload = _urgent(s), json.dumps(s.to_dict(), sort_keys=True)
            was = self._reported.get(s.id)
            if was is None or was[0] != urgent or (was[1] != payload and now - was[2] >= REPORT_EVERY):
                self._reported[s.id] = (urgent, payload, now)
                changed.append(s.to_dict())
        forgotten = [sid for sid in self._reported if sid not in self.sessions]
        for sid in forgotten:
            del self._reported[sid]
        # Bounded: this runs inside the tick and inside every RPC that pushes, and a write to a peer
        # that has gone blocks once the pipe is full — for `LINK_SILENCE`, were nothing to stop it.
        try:
            async with asyncio.timeout(REPORT_WRITE):
                if changed:
                    await mux.notify("report", records=changed)
                if forgotten:
                    await mux.notify("gone", ids=forgotten)
        except link.LinkClosed:
            pass
        except TimeoutError:
            mux.close(f"a report could not be written within {REPORT_WRITE:g} s")  # the reconnect's snapshot repairs it

    async def _from_home(self, method: str, params: dict[str, Any]) -> Any:
        """What the home may ask of this node. Step 3a: nothing but a ping; acts arrive with step 4."""
        if method == "ping":
            return "pong"
        raise link.LinkError(f"unknown link method {method!r}")

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
                self.links[host] = {"up": True, "since": now_iso(), "why": "linked"}
                said_hello = True
                log.info("link from %s: up", host)
                await self._push_changes()  # the overlay lifts on its cards
                return {"protocol": link.PROTOCOL, "home": self.host, "host": host}
            if not said_hello:
                raise link.LinkError("say hello first")
            if method == "ping":
                return "pong"
            if method in ("snapshot", "report"):
                taken = self._take_records(host, params.get("records") or [], whole=method == "snapshot")
                await self._push_changes()
                return {"taken": taken}
            if method == "gone":
                for rid in params.get("ids") or []:
                    self._forget_remote(host, str(rid))
                await self._push_changes()
                return None
            raise link.LinkError(f"unknown link method {method!r}")

        mux = link.Mux(reader, writer, from_node)
        why = "the link's reader failed"
        try:
            why = await mux.run()
        finally:  # however it ended: a link the home still calls up after it has gone is the worst answer
            if self._link_muxes.get(host) is mux:
                del self._link_muxes[host]
                self.links[host] = {"up": False, "since": now_iso(), "why": why}
                log.warning("link from %s: down — %s", host, why)
                with contextlib.suppress(Exception):
                    await self._push_changes()  # its cards go `unreachable` now, not at the next tick

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
                    apply_node(mine[rid], raw)
                else:
                    mine[rid] = Session.from_dict(raw)  # adopted, the replica's home-owned fields and all
            except (NotTheSameSession, TypeError, ValueError, KeyError) as e:
                log.warning("link from %s: could not take %s: %s", host, rid, e)
                continue
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

    # -- helpers ---------------------------------------------------------------------------------

    def _gate(self, caller: Any, method: str, params: dict[str, Any]) -> None:
        """`mail.act_gate` over this agent's records (design §4.8, §9 invariants 5 and 11): a
        function over a record map, so a node can forward and the home can answer (§4.4a)."""
        if method == "create" and params.get("capabilities"):
            _grants(params["capabilities"])  # an unknown grant name is refused before the gate reads it
        reason = mail.act_gate(self.sessions, caller, method, params)
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
                raise RpcError(f"{sid} runs on {host}: acts across the link are not built (TD-057 step 4)") from None
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

    def _view(self, s: Session) -> dict[str, Any]:
        """The view a client gets. This host's record is `s.view()`. Another host's carries its
        address as `id`, and while that host's link is down reads `unreachable` — an overlay on the
        view, never a state on the record."""
        v = s.view()
        if s.host == self.host:
            return v
        v["id"] = f"{s.id}@{s.host}"
        state = self.links.get(s.host) or {"up": False, "since": None, "why": "not connected since the home started"}
        v["host_link"] = dict(state)
        if (sup := self.supervision.get(s.host)) and sup.get("doing"):
            v["host_link"]["supervisor"] = {k: sup[k] for k in ("doing", "since", "attempts")}
        if not state["up"]:
            v["last_state"], v["state"] = v["state"], "unreachable"
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
        gone, self._gone = self._gone, []
        if not self._subscribers:
            return
        # sort_keys: the payload is the comparison key too (the UI reads fields by name, never order)
        payloads = {v["id"]: json.dumps(v, sort_keys=True) for v in self._views()}
        for w, last in list(self._subscribers.items()):
            for sid, payload in payloads.items():
                if last.get(sid) != payload:
                    last[sid] = payload
                    await self._send(w, '{"event": "session", "session": ' + payload + "}")
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
                writer.write((json.dumps(await self._dispatch(req)) + "\n").encode())
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._conns.discard(writer)
            self._subscribers.pop(writer, None)
            writer.close()

    async def _serve_wait(
        self, req: dict[str, Any], reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """A `wait` holds its connection (design §4.10): the RPC runs while this reads the socket
        for the close. The moment the client goes away — a Ctrl-C, a cancelled turn — the wait is
        cancelled, so no ghost wait is left to be charged a wake and hand the mail to nobody.
        Requests are serial per connection; one sent mid-wait is answered with an error."""
        task = asyncio.ensure_future(self._dispatch(req))
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
                    writer.write((json.dumps(task.result()) + "\n").encode())
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

    async def _dispatch(self, req: dict[str, Any]) -> dict[str, Any]:
        resp = await self._dispatch_inner(req)
        caller = req.get("caller")
        if not mail.is_person(caller):
            # The line on every `ao` reply (design §4.10): the response to a session with unread
            # mail says so — result or refusal alike — read after the method ran, so an `ao inbox`
            # that just read everything carries no line. It types nothing and starts nothing.
            s = self.sessions.get(self._addr(naming.split_address(str(caller))[0]))
            if s is not None and (n := s.unread()):
                resp["mail"] = {"unread": n, "wake_budget_spent": s.wake_budget_spent()}
        return resp

    async def _dispatch_inner(self, req: dict[str, Any]) -> dict[str, Any]:
        rid = req.get("id")
        name = req.get("method")
        method = getattr(self, f"rpc_{name}", None)
        if method is None:
            return {"id": rid, "error": f"unknown method {name!r}"}
        params = dict(req.get("params") or {})
        if ignored := _drop_unknown(method, params):
            # logged with the method, because the reply cannot tell a client newer than this agent
            # from a caller bug of the same age, and the second is worth finding in a log
            log.warning("rpc %s: ignored unknown params %s", name, ", ".join(ignored))
        caller = req.get("caller")
        if not mail.is_person(caller):
            # A request's identity comes from the channel it arrived on, never from a field it
            # carries (design §4.4a): this socket is this host's, so any `@host` a client wrote
            # is dropped and the caller is this host's bare id.
            caller = naming.split_address(str(caller))[0]
        try:
            if self.mode == "node":
                # A node (design §4.4a, the call-by-call table): decided before the gate, because
                # the gate's graph is at the home. With the link down these are refused as
                # unreachable; with it up, as not forwarded yet — never served from the replica.
                refusal = modes.offline_refusal(
                    str(name), caller, params, host=self.host, home=self.home, reachable=self.home_reachable()
                )
                if refusal:
                    raise RpcError(refusal)
            self._gate(caller, str(name), params)
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


def _clean(text: str) -> str:
    """Strip ANSI/control bytes and cap width: pane output is untrusted everywhere but xterm.js."""
    text = _OSC.sub("", text)
    text = _CSI.sub("", text)
    text = _ESC_OTHER.sub("", text)
    text = "".join(ch for ch in text if ch == "\t" or ch >= " ")
    return text[:200]


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
