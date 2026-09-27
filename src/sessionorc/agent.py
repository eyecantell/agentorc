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
import secrets
import signal
import sys
import time
from collections import OrderedDict, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sessionorc import (
    adapters,
    agent_common,
    build,
    containers,
    hosts,
    identity,
    link,
    mail,
    modes,
    naming,
    paths,
    reports,
)
from sessionorc import ledger as ledger_mod
from sessionorc import settings as settings_mod
from sessionorc.agent_attention import AttentionMixin
from sessionorc.agent_common import (  # re-exported: callers and tests read these from the agent
    _CSI,  # noqa: F401
    _ESC_OTHER,  # noqa: F401
    _LINE_BREAKS,  # noqa: F401
    _OSC,  # noqa: F401
    ACT_TIMEOUT,  # noqa: F401
    BACKUP_KEEP,  # noqa: F401
    BACKUP_MEMBERS,  # noqa: F401
    BOARD_REPLY_NOTE,  # noqa: F401
    CLOSED_KEEP,  # noqa: F401
    COMPOSER_LINES,  # noqa: F401
    CREATE_GRACE,  # noqa: F401
    DERIVE_EVERY,  # noqa: F401
    DOORBELL_TRIES,  # noqa: F401
    FILE_CAP,  # noqa: F401
    FILES_MAX,  # noqa: F401
    FILL_CEILING,  # noqa: F401
    FILL_WINDOW,  # noqa: F401
    GIT_EVERY,  # noqa: F401
    HOME_EDITS,  # noqa: F401
    ID_RECHECK,  # noqa: F401
    IDLE_NUDGE,  # noqa: F401
    INTENT_FIELDS,  # noqa: F401
    LAUNCH_KEYS,  # noqa: F401
    LEASE_TTL,  # noqa: F401
    MODEL_EVERY,  # noqa: F401
    NODE_ACTS,  # noqa: F401
    NODE_READS,  # noqa: F401
    PASTE_SHOW_SECONDS,  # noqa: F401
    PRUNE_EVERY,  # noqa: F401
    PUSH_OPEN,  # noqa: F401
    REMOVED_GUARD_SECONDS,  # noqa: F401
    REPORT_EVERY,  # noqa: F401
    REPORT_WRITE,  # noqa: F401
    REPOS_EVERY,  # noqa: F401
    RESTART_CEILING,  # noqa: F401
    RESTART_EARLY,  # noqa: F401
    RESTART_SETTLE,  # noqa: F401
    RESTART_WINDOW,  # noqa: F401
    RESUME_MIN,  # noqa: F401
    SEAT_IDLE_GRACE,  # noqa: F401
    SEND_STALL_SECONDS,  # noqa: F401
    SETTLED,  # noqa: F401
    STALL_AFTER,  # noqa: F401
    SUBMIT_SECONDS,  # noqa: F401
    TAIL_LINES,  # noqa: F401
    TICK_SECONDS,  # noqa: F401
    TITLE_CAP,  # noqa: F401
    TRAIL_FLOOR,  # noqa: F401
    TRAIL_KEEP,  # noqa: F401
    USAGE_BACKOFF_MAX,  # noqa: F401
    USAGE_EVERY,  # noqa: F401
    WRAPUP_GRACE,  # noqa: F401
    RpcError,  # noqa: F401
    _alarm_report,  # noqa: F401
    _alarm_since,  # noqa: F401
    _alarm_words,  # noqa: F401
    _cap,  # noqa: F401
    _clean,  # noqa: F401
    _clean_answer,  # noqa: F401
    _controllers,  # noqa: F401
    _drop_unknown,  # noqa: F401
    _duration,  # noqa: F401
    _ended_by,  # noqa: F401
    _falsy,  # noqa: F401
    _grants,  # noqa: F401
    _is_branch_claim,  # noqa: F401
    _lane,  # noqa: F401
    _older,  # noqa: F401
    _oldest_first,  # noqa: F401
    _pane_title,  # noqa: F401
    _parse,  # noqa: F401
    _peer_pid,  # noqa: F401
    _pr,  # noqa: F401
    _prune_tallies,  # noqa: F401
    _read_capped,  # noqa: F401
    _recent,  # noqa: F401
    _ref,  # noqa: F401
    _reply_line,  # noqa: F401
    _review,  # noqa: F401
    _source,  # noqa: F401
    _stop_time,  # noqa: F401
    _urgent,  # noqa: F401
    _usage_checked_at,  # noqa: F401
    _usage_key,  # noqa: F401
    _Wait,  # noqa: F401
    backup_store,  # noqa: F401
    launch_params,  # noqa: F401
    log,  # noqa: F401
    read_checkout,  # noqa: F401
)
from sessionorc.agent_identity import IdentityMixin
from sessionorc.agent_inbox import InboxMixin
from sessionorc.agent_link import LinkMixin
from sessionorc.agent_mail import MailMixin
from sessionorc.agent_remote import RemoteMixin
from sessionorc.agent_wake import WakeMixin
from sessionorc.gitinfo import WorktreeError, ensure_worktree, git_info
from sessionorc.mail import ACTING_RPCS  # noqa: F401 — re-exported: callers read it from the agent
from sessionorc.models import (
    GRANTS,
    PERSON,
    PROGRESS_STATUSES,
    SYSTEM,
    FindingEntry,
    MailEntry,
    Pending,
    ProgressEntry,
    SendEntry,
    Session,
    State,
    now_iso,
)
from sessionorc.store import (
    AttentionStore,
    DoingLogStore,
    EventQueue,
    IdentityAlarmStore,
    PersonInboxStore,
    RepoStore,
    SessionStore,
    UsageStore,
)
from sessionorc.tmux import ARG_LIMIT, DuplicateSession, PaneInfo, Tmux


class HostAgent(AttentionMixin, WakeMixin, InboxMixin, MailMixin, IdentityMixin, RemoteMixin, LinkMixin):
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
        # What `host` reports about this process (design §4.4 *Version skew is survivable*, TD-062):
        # when it started, and the commit its install was built from, read once — a promote replaces
        # the files under a running agent, and what is running is what was read at start.
        self.started_at = now_iso()
        self.build = build.info()
        # Who is calling (design §4.8a, TD-077): `off | observe | enforce` from `local: {identity: …}`,
        # the panes the last list saw, the home's own alarms (about no record), and a tally of
        # connections by class and deciding signal since start — what `ao identity` prints, and what
        # turning a host from `observe` to `enforce` is decided on.
        self.identity_mode = identity.mode_of(identity_mode or hosts.local_host().identity)
        self.proc: identity.ProcReader = proc or identity.LinuxProc()
        self._id_panes: list[identity.Pane] = []
        # A record's pane that left the list, and when (monotonic): a hook of its ending process may
        # still arrive (TD-115, `identity.PANE_GONE_GRACE`).
        self._id_gone: dict[str, tuple[identity.Pane, float]] = {}
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
        # The attention trail and the state rows' snoozes (design §4.10 *The Inbox is a queue*,
        # TD-079). A state row is a view of a record: when its need goes away by some road that is
        # not the person's — the session was resumed, the permission was answered in the terminal,
        # the work was pushed — the row used to vanish with no trace, which is what *it disappeared
        # when I read it* was. The home records the ending instead.
        self.attention_store = AttentionStore()
        self._ticker: asyncio.Task | None = None  # the tick loop, once `serve` is running
        self.trail, self.attention_snoozed = self.attention_store.load()
        # `<sid>|state` / `<sid>|alarm` → (the row kind it is showing, when that row began, what it
        # said). Empty on load **on purpose**: the first tick after a restart reads every live row
        # as one that has just begun, so a row that was already up leaves no trail when it ends —
        # one ending lost per restart, which is better than inventing one the agent never saw, and
        # the record itself is still there to be read.
        self._attention: dict[str, tuple[str, str, str]] = {}
        # sid → how its current row will have ended, when the home knows better than *resolved*:
        # written by the act that ended it (`decide`, `identity_ack`, a resume, a forget).
        self._attention_how: dict[str, str] = {}
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
        # …and the home's `asks_waiting` for each (§4.9b): a node holds no inbox to count, so its own
        # view of a record shows the number the home last pushed, as fresh as the link.
        self._asks_hints: dict[str, int] = {}
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
        # The doorbell (design §4.10, TD-052 step 7): per session, the idle stretch it last rang in —
        # `{rev, rung, failures}`, `rev` being the record's state counter, so a new stretch is a new
        # entry — and the rings in flight, one per session.
        self._bells: dict[str, dict[str, Any]] = {}
        self._ringing: dict[str, asyncio.Task[None]] = {}
        # One typist per pane (TD-094): `_submit` holds its session's lock from paste to confirmed
        # submit, and a ring holds it from reading the composer to its own submit — so a ring never
        # pastes into the middle of a `send`, where the two lines would be submitted as one.
        self._typing: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
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
        self._seat_count_task: asyncio.Task[None] | None = None  # §6 rule 3's `gh` read, detached
        self._seat_counted_at = datetime.min.replace(tzinfo=UTC)
        # when a hook last reported on a session: a screen-rule verdict never outranks a hook
        # state fresher than STALL_AFTER (design §4.2); a session no hook has reported on yet — the
        # trust dialog case — takes the classifier's verdict at once (TD-015)
        # Seeded on load: a hook-confirmed record was fed by a live hook stream until the agent
        # stopped, and `since` is a transition time, not a hook time — so count it fresh as of now.
        # A restart must never let the screen outrank a state a hook just reported.
        self._last_hook: dict[str, datetime] = {
            sid: datetime.now(UTC) for sid, s in self.sessions.items() if s.confidence == "hook"
        }
        # When a hook event last reached a record **live**, in epoch seconds (TD-169): a queued
        # event stamped before it is older than the state it would set, so its state is skipped.
        self._live_hook_at: dict[str, float] = {}
        # entries whose *Put on the board* is under way (TD-140): one press per entry at a time
        self._board_adding: set[str] = set()
        # profile → last usage dict from its adapter (`usage_for`), and when it was last asked
        # The last good reading per profile, kept across a restart (TD-087) — with, beside the
        # windows, why the *last poll* failed, which the page draws as a stale chip rather than
        # as nothing at all. `_usage_wait` is the backoff a 429 sets and a success clears.
        # **The reading is the account's** (§4.2a, TD-122): `_usage_acct` holds one reading per
        # `(adapter, account)` key and the poll, its clock and its backoff are keyed on that;
        # `_usage` is the same reading copied under every live profile sharing the account, with
        # the account and the tool's display name beside it, which is what the gate, `limited`,
        # the `usage` RPC and the chip read.
        self.usage_store = UsageStore()
        self._usage: dict[str, dict[str, Any]] = self.usage_store.load()
        self._usage_acct: dict[str, dict[str, Any]] = {}
        # …and the **allowance** survives with it (anchor's read of PR #307): a held reading is as
        # good as a poll made at its `fetched`, so the first poll after a promote waits until
        # `fetched + USAGE_EVERY`, never sooner. A reading with no readable time is polled at once.
        # Seeded per account on the first refresh, from its profiles' held readings.
        self._usage_checked: dict[str, float] = {}
        self._usage_wait: dict[str, float] = {}
        self._usage_task: asyncio.Task[None] | None = None
        # The repo facts per registered checkout (design §4.4 *Repo facts*, TD-176), kept across a
        # restart in `repos.json`; the home's alone — a node reads none of this (§4.4a).
        self.repos_store = RepoStore()
        self._repos: dict[str, dict[str, Any]] = self.repos_store.load()
        self._repos_read_at = float("-inf")  # monotonic: the first tick reads
        self._ledger_mtime: dict[str, float | None] = {}
        self._repos_task: asyncio.Task[None] | None = None
        # the last fifty `ao doing` calls per team (design §4.8 *the doing log*, TD-176 slice 2)
        self.doing_log = DoingLogStore()
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
        # Kept on the agent as well as locally: a test that must own the clock cancels it rather
        # than racing it with a sleep, which is the shape TD-078 and TD-088 keep catching. The
        # `finally` below cancels it either way — cancelling a cancelled task is a no-op.
        ticker = self._ticker = asyncio.create_task(self._tick_loop())
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
            await asyncio.sleep(agent_common.TICK_SECONDS)

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
        self._note_attention(snapshot_at)
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
        if self.mode == "home" and (self._repos_task is None or self._repos_task.done()):
            # detached as the usage refresh is: `gh` talks to the network (§4.4 *Repo facts*)
            self._repos_task = asyncio.create_task(self._refresh_repos())
        if self._usage_task is None or self._usage_task.done():
            # detached: a slow usage endpoint (10 s timeout) must not hold up the tick or its push
            self._usage_task = asyncio.create_task(self._refresh_usage())
        await self._team_stop_times(snapshot_at)
        await self._enforce_stop_times(snapshot_at)
        await self._enforce_usage_gate(snapshot_at)
        await self._keep_running(snapshot_at)
        await self._sweep_mail(snapshot_at)
        self._poke_waits()  # the wake decision is re-taken every tick for a session blocked in `wait`
        self._ring_doorbells()

    async def _team_stop_times(self, now: datetime) -> None:
        """Design §6 *Team stop time* (TD-146): each live, unattended session carrying a team's badge
        — members and seats, this host's and every node's — whose `run_until` is unset or later than
        its team's `teams.<team>.until` takes the team's instant, exactly as `set_stop` gives one. A
        session's own earlier stop time is kept. A record created after the instant had passed is not
        given it: a team started again after its stop time is the person's word, not a stop.
        **At the home**, whose file it is; a node's record takes it through `set_stop` over the link,
        and waits for the link when it is down."""
        if self.mode != "home":
            return
        untils = {t: v["until"] for t, v in settings_mod.teams(settings_mod.load()).items() if v.get("until")}
        if not untils:
            return
        changed = False
        for s in [*self.sessions.values(), *(r for recs in self.remote.values() for r in recs.values())]:
            inst = untils.get(s.team) if s.team else None
            if not inst or not s.unattended or s.state in ("exited", "closed") or s.superseded_by:
                continue
            if s.run_until and _parse(s.run_until) <= _parse(inst):
                continue  # its own earlier stop time, or the team's already
            with contextlib.suppress(TypeError, ValueError):
                if _parse(s.created) >= _parse(inst):
                    continue
            if s.host == self.host:
                self._take_stop(s, inst)
                changed = True
            elif s.host in self._link_muxes:
                try:
                    await self._route_act("set_stop", {"id": self._address(s), "run_until": inst}, None, s.host)
                except Exception as e:  # noqa: BLE001 — tried again on the next tick
                    log.warning("%s: the team's stop time did not reach %s: %s", s.id, s.host, e)
        if changed:
            await self._push_changes()

    def _team_stamp(self, team: str, unattended: bool, run_until: str | None) -> str | None:
        """What a create's `run_until` is under its team's stop time (§6 *Team stop time*): the
        earlier of the two while the team's is still ahead; its own, or none, otherwise — a start
        after the instant has passed is not stopped by it."""
        if not (team and unattended):
            return run_until
        inst = (settings_mod.teams(settings_mod.load()).get(team) or {}).get("until")
        if not inst or _parse(inst) <= datetime.now(UTC):
            return run_until
        return inst if not run_until or _parse(inst) < _parse(run_until) else run_until

    def _take_stop(self, s: Session, when: str | None) -> None:
        """A stop time given by a policy as `set_stop` gives one: a new time is a new run, so a wrap-up
        already asked is spent."""
        if when != s.run_until:
            s.wrapup_sent_at = None
        s.run_until = when
        self._save(s)

    async def _restamp_team(self, team: str, old: str | None, new: str | None) -> None:
        """A team's stop time moved or was cleared (TD-146): the live members that carry the old
        instant took it from the team, so they follow — to the new one, or to none on **Clear**. A
        member's own earlier stop time is another instant and is not touched."""
        if not old or old == new:
            return
        changed = False
        for s in [*self.sessions.values(), *(r for recs in self.remote.values() for r in recs.values())]:
            if s.team != team or s.run_until != old or s.state in ("exited", "closed") or s.superseded_by:
                continue
            if new and not s.unattended:
                # a person took it over: a move is a policy's, and policies leave it alone (§4.2); a Clear clears
                continue
            if s.host == self.host:
                self._take_stop(s, new)
                changed = True
            elif s.host in self._link_muxes:
                try:
                    await self._route_act("set_stop", {"id": self._address(s), "run_until": new}, None, s.host)
                except Exception as e:  # noqa: BLE001 — the tick's pass gives a new instant again; a clear waits
                    log.warning("%s: the team's changed stop time did not reach %s: %s", s.id, s.host, e)
        if changed:
            await self._push_changes()

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
            if settled or now - _parse(s.wrapup_sent_at) >= agent_common.WRAPUP_GRACE:
                log.info("%s stopped after its wrap-up (%s)", s.id, "settled" if settled else "grace ran out")
                await self.rpc_kill(s.id)

    async def _enforce_usage_gate(self, now: datetime) -> None:
        """Pause the unattended sessions of a profile whose usage crossed a line, and resume them
        when every window is back under (design §6 *Usage gate*, TD-100).

        The lines come from the person's reserves in `settings.yml`, read here on every tick, and
        the windows from the profile's last good usage reading — a failed poll keeps the last one,
        and a profile with no reading at all neither pauses nor resumes anything. A pause is a
        **send, not a kill**: the mark `gated` is written the tick the line is crossed, the record's
        `pause_prompt` is typed once and retried every tick until it lands (`sent_at`), and a session
        sitting on a permission or a question is not typed at — waiting on a dialog, it is consuming
        nothing, so the mark stands and the send lands after the dialog clears. The resume is the
        `resume_prompt`, typed when every window is under its line and no sooner than `RESUME_MIN`
        after the pause; the mark goes with it. Interactive sessions are never gated: a session a
        person took over loses its mark on the next tick, with nothing typed.

        A session carrying a team's badge whose team has a **reserve priority** (`teams.<team>.
        reserve`, TD-146) pauses at its profile's line lowered by that much, on every window with a
        reserve, and its mark carries `team_extra: {team, n}` so the card can say which line it was."""
        whole = settings_mod.load()
        doc = settings_mod.reserves(whole)
        changed = False
        for s in list(self.sessions.values()):
            if not s.unattended or s.state in ("exited", "closed"):
                if s.gated:
                    s.gated = None  # the gate's reach is unattended, live sessions alone (§9 invariant 5)
                    self.store.save(s)
                    changed = True
                continue
            by_label = doc.get(s.profile)
            reading = self._usage.get(s.profile) or {}
            windows = reading.get("windows")
            if windows is None and by_label:
                continue  # no reading: the last word stands, whatever it was (§6: a failure never gates)
            extra = settings_mod.team_extra(whole, s.team)
            rows = settings_mod.lines(by_label or {}, windows, now, extra)
            over = settings_mod.crossed(rows)
            if over is not None:
                mark = {
                    "profile": s.profile,
                    "label": over["label"],
                    "pct": over["pct"],
                    "line": over["line"],
                    "since": (s.gated or {}).get("since") or now_iso(),
                    "next": over["next"],
                    "resets": over["resets"],  # so a card can say *resets* when `next` is the reset
                    "sent_at": (s.gated or {}).get("sent_at"),
                    **({"team_extra": {"team": s.team, "n": extra}} if extra else {}),
                }
                if mark != s.gated:
                    if not s.gated:
                        log.info("%s paused by the usage gate: %s %s %s%% >= %s%%", s.id, s.profile,
                                 over["label"], over["pct"], over["line"])  # fmt: skip
                    s.gated = mark
                    self.store.save(s)
                    changed = True
                if not s.gated.get("sent_at") and s.pause_prompt and not self._on_a_dialog(s):
                    try:
                        await self._submit(s.id, adapters.get(s.adapter), s.pause_prompt)
                    except Exception:  # noqa: BLE001 — retried on the next tick (§6)
                        log.warning("%s would not take the pause prompt; retrying next tick", s.id)
                    else:
                        s.gated = {**s.gated, "sent_at": now_iso()}
                        self.store.save(s)
                        changed = True
                continue
            if not s.gated:
                continue
            if now - _parse(s.gated["since"]) < RESUME_MIN:
                continue
            if s.gated.get("sent_at") and s.resume_prompt:
                if self._on_a_dialog(s):
                    continue
                try:
                    await self._submit(s.id, adapters.get(s.adapter), s.resume_prompt)
                except Exception:  # noqa: BLE001 — the mark stands and the resume is tried again
                    log.warning("%s would not take the resume prompt; retrying next tick", s.id)
                    continue
            log.info("%s resumed by the usage gate", s.id)
            s.gated = None
            self.store.save(s)
            changed = True
        if changed:
            await self._push_changes()

    async def _keep_running(self, now: datetime) -> None:
        """Design §6 *Keeping a team running* (TD-103): rule 1, the crash restart; rule 2, the wanted
        restart; rule 3, the seats; rule 4, the idle nudge. **The restarts run at the home** (§4.4a: policies that start
        run at the home), over this host's records and every node's — a member on a host whose link
        is down is left as it is and looked at again on the next tick, refused rather than queued.
        **Each record's pass is isolated**: one's exception is logged and the tick goes on to the next."""
        if self.mode != "home":
            return
        records = [*self.sessions.values(), *(r for recs in self.remote.values() for r in recs.values())]
        for s in records:
            try:
                await self._crash_restart(s, now)
                await self._wanted_restart(s, now)
                await self._seat_pass(s, now, records)
                await self._idle_nudge(s, now)
            except Exception:  # noqa: BLE001 — one record's failure is never the tick's (§6)
                log.exception("%s: the keep-running pass failed", self._address(s))
        if (
            self._seat_count_task is None or self._seat_count_task.done()
        ) and now - self._seat_counted_at > DERIVE_EVERY:
            # detached, as the reports are: `gh` talks to the network, and the tick must not wait on it
            self._seat_counted_at = now
            self._seat_count_task = asyncio.create_task(self._count_seats(records))

    def _crashed(self, s: Session, now: datetime) -> bool:
        """Rule 1's test: a supervised, unattended member that is not a seat, `exited` by a
        **natural exit** — `pane` true, the tool left on its own; a kill takes the pane and is a
        person's or a controller's act, never undone — with no declaration, no wrap-up asked, no
        stop time passed, not gated, not suspended, and not already at its ceiling or superseded."""
        return bool(
            s.supervised
            and s.unattended
            and s.seat is None
            and s.state == "exited"
            and s.pane
            and not s.out_of_work
            and not s.restart_wanted
            and not s.wrapup_at
            and not s.wrapup_sent_at
            and not (s.run_until and now >= _parse(s.run_until))
            and not s.gated
            and not s.suspended
            and not s.restart_ceiling
            and not s.superseded_by
            and not self._profile_gated(s.profile, now, s.team)
            and not self._just_restarted(s, now)
        )

    @staticmethod
    def _just_restarted(s: Session, now: datetime) -> bool:
        """Whether the tick's last restart of `s` succeeded less than `RESTART_SETTLE` ago (TD-186):
        the record is the new run, whatever the ending run it replaced reports meanwhile."""
        last = s.restarts[-1] if s.restarts and isinstance(s.restarts[-1], dict) else None
        return bool(last and not last.get("error") and _recent(last.get("at"), now, agent_common.RESTART_SETTLE))

    def _profile_gated(self, profile: str, now: datetime, team: str = "") -> bool:
        """Whether `profile` is over a usage line now (§6 *Usage gate*): the gate's own reading, for a
        record the gate no longer marks — it clears `gated` on an exited one — so a policy does not
        restart or fill into a pause. No reading is no gate, as at the gate (a failure never gates).
        `team` is the record's: its reserve priority lowers the line exactly as it does at the gate
        (TD-146), or a teamed member would be restarted at 65% and paused on the next tick."""
        windows = (self._usage.get(profile) or {}).get("windows")
        if windows is None:
            return False
        whole = settings_mod.load()
        by_label = settings_mod.reserves(whole).get(profile) or {}
        extra = settings_mod.team_extra(whole, team)
        return settings_mod.crossed(settings_mod.lines(by_label, windows, now, extra)) is not None

    async def _crash_restart(self, s: Session, now: datetime) -> None:
        """Rule 1 (design §6 *Keeping a team running*): restart a member that crashed by replaying
        its launch record — `create` again with what the last supervised create was handed, never
        the definition re-read — which supersedes the record in place under its name (§4.1). Each
        attempt is appended to `restarts` and the list is carried onto the new record, so the count
        survives the restart it counts; a replay that fails keeps its entry with `error` and counts
        all the same, so an unrepairable record reaches the ceiling within three ticks. At
        `RESTART_CEILING` inside `RESTART_WINDOW` the tick writes `restart_ceiling` and stops: the
        session is a person's."""
        if not self._crashed(s, now):
            return
        if s.host != self.host and s.host not in self._link_muxes:
            return  # its link is down: left as it is, looked at again next tick (§4.4a)
        recent = [r for r in s.restarts if isinstance(r, dict) and _recent(r.get("at"), now, RESTART_WINDOW)]
        if len(recent) >= RESTART_CEILING:
            s.restart_ceiling = {"at": now_iso(), "count": len(recent)}
            log.warning("%s: %d restarts in %s — the ceiling; it is a person's now", s.id, len(recent), RESTART_WINDOW)
            self._save(s)
            await self._push_changes()
            return
        log.info("%s exited on its own with nothing declared: restarting it (%d in the window)", s.id, len(recent) + 1)
        await self._replay(s, "crash")

    async def _wanted_restart(self, s: Session, now: datetime) -> None:
        """Rule 2 (design §6, §4.9a): a supervised member that declared `restart_wanted` and is `idle`,
        or `exited` on its own — a kill or a Close is never undone — is closed if it is still there and
        restarted as rule 1 does, under the same ceiling, **only when its git fields are known and show
        nothing uncommitted and nothing unpushed**. With work left it is sent one fixed line naming the
        counts, and `IDLE_NUDGE` later carries `restart_blocked`; the moment the work reads pushed the
        restart runs. An `early` one is left to the person (§4.9a), and so is an unknown git state."""
        rw = s.restart_wanted
        if not (rw and s.supervised and s.unattended) or s.seat is not None or rw.get("early"):
            return
        if s.superseded_by or s.suspended or s.gated:
            return
        # A ceiling guards against a crash loop, not a member that has worked since (TD-186): a clean
        # declaration is acted on once the window holds fewer than the ceiling's restarts; the new
        # record carries no mark. Inside the window the mark stands, as it does for rule 1.
        if s.restart_ceiling and self._window_full(s, now):
            return
        # the tick's own close, then a replay that failed, leaves it `closed`: still the tick's to retry
        closed_by_tick = s.state == "closed" and bool(s.restarts) and s.restarts[-1].get("why") == "wanted"
        if not (s.state == "idle" or (s.state == "exited" and s.pane) or closed_by_tick):
            return
        if (s.run_until and now >= _parse(s.run_until)) or self._profile_gated(s.profile, now, s.team):
            return
        if s.host != self.host and s.host not in self._link_muxes:
            return  # its link is down: left as it is, looked at again next tick (§4.4a)
        git = s.git or {}
        if not isinstance(git.get("dirty"), int) or not isinstance(git.get("unpushed"), int):
            return  # an unknown git state is left alone (§6 rule 2)
        if git["dirty"] or git["unpushed"]:
            await self._restart_held(s, now, git["dirty"], git["unpushed"])
            return
        recent = [r for r in s.restarts if isinstance(r, dict) and _recent(r.get("at"), now, RESTART_WINDOW)]
        if len(recent) >= RESTART_CEILING:
            s.restart_ceiling = {"at": now_iso(), "count": len(recent)}
            log.warning("%s: %d restarts in %s — the ceiling; it is a person's now", s.id, len(recent), RESTART_WINDOW)
            self._save(s)
            await self._push_changes()
            return
        log.info("%s wants another run and its work is pushed: restarting it", s.id)
        if s.state == "idle":
            try:
                if s.host == self.host:
                    await self.rpc_close(s.id)
                else:
                    await self._route_act("close", {"id": s.id}, None, s.host)
            except Exception as e:  # noqa: BLE001 — a close that failed is a restart that failed, and counts
                # `rpc_close` marks the record closed before its own tail runs, so a failure there
                # leaves it `closed` with nothing replayed: the entry is what lets the next tick
                # retry it (`closed_by_tick`) rather than strand it (review of PR #461)
                s.restarts = [*s.restarts, {"at": now_iso(), "why": "wanted", "error": f"close: {e}"}]
                log.warning("%s: the close before a wanted restart failed: %s", s.id, e)
                self._save(s)
                await self._push_changes()
                return
        await self._replay(s, "wanted")

    @staticmethod
    def _window_full(s: Session, now: datetime) -> bool:
        recent = [r for r in s.restarts if isinstance(r, dict) and _recent(r.get("at"), now, RESTART_WINDOW)]
        return len(recent) >= RESTART_CEILING

    async def _restart_held(self, s: Session, now: datetime, dirty: int, unpushed: int) -> None:
        """Rule 2 with work left: one fixed send, once, naming the counts from the record and never a
        session's words; `IDLE_NUDGE` after it (after the declaration, where nothing could be typed)
        the record carries `restart_blocked` and the Inbox lists it."""
        if not s.restart_blocked_sent_at and s.state == "idle" and s.host == self.host:
            line = (
                f"[agentorc] your restart is held: {dirty} uncommitted and {unpushed} unpushed in your checkout "
                "— commit and push them, and the host agent restarts you"
            )
            if await self._policy_send(s, line):
                s.restart_blocked_sent_at = now_iso()
                self._save(s)
        start = s.restart_blocked_sent_at or (s.restart_wanted or {}).get("at")
        if not s.restart_blocked and start and now - _parse(str(start)) >= IDLE_NUDGE:
            s.restart_blocked = {"at": now_iso(), "dirty": dirty, "unpushed": unpushed}
            log.info("%s: its restart is held by work left — the person's now", s.id)
            self._save(s)
            await self._push_changes()

    async def _idle_nudge(self, s: Session, now: datetime) -> None:
        """Rule 4 (design §6): a supervised member hook-confirmed `idle` for `IDLE_NUDGE` with open work
        — a lane reference not done or dropped, a declared claim not done or dropped, or for a seat a
        question waiting — and nothing declared, is sent one fixed line naming it, once per idle
        stretch (`nudged_at`; a stretch ends when the state changes). It spends no wake budget. The
        send needs the pane here: a node's member is not nudged yet (TD-103)."""
        if not (s.supervised and s.unattended) or s.superseded_by or s.suspended or s.host != self.host:
            return
        if s.state != "idle" or s.confidence != "hook" or s.pending or s.out_of_work or s.restart_wanted:
            return
        if not s.since or now - _parse(s.since) < IDLE_NUDGE:
            return
        if s.nudged_at and _parse(s.nudged_at) >= _parse(s.since):
            return  # this stretch was nudged already: never a second before the first is answered
        if s.wrapup_at or s.wrapup_sent_at or (s.run_until and now >= _parse(s.run_until)):
            return
        if s.gated or self._profile_gated(s.profile, now, s.team):
            return
        line = self._nudge_line(s)
        if line and await self._policy_send(s, line):
            s.nudged_at = now_iso()
            log.info("%s: idle %s with work open — nudged", s.id, IDLE_NUDGE)
            self._save(s)
            await self._push_changes()

    def _nudge_line(self, s: Session) -> str | None:
        if s.seat is not None:
            n = s.asks_waiting(home=self.host) if s.seat_due else 0
            return f"[agentorc] you have {n} questions waiting — run `ao inbox`" if n else None
        ended = {e.ref for e in s.progress if e.status in ("done", "dropped")}
        claimed = [e.ref for e in s.progress if e.source == "declared" and e.status == "claimed"]
        ref = next((r for r in [*s.lane, *claimed] if r != "free-pick" and r not in ended), None)
        if ref is None:
            return None
        return (
            f"[agentorc] you have been idle {int(IDLE_NUDGE.total_seconds() // 60)} minutes with `{ref}` open "
            f"— end the run with one of `ao progress done {ref} --pr N`, `ao progress drop {ref} --why`, "
            "`ao progress none --why` or `ao progress restart --why`"
        )

    async def _policy_send(self, s: Session, text: str) -> bool:
        """A policy's fixed line, typed through `send`'s path under the doorbell's rules (§6, §4.10):
        one typist per pane, never at a dialog, only into an empty composer — someone's half-typed
        words would be submitted with it. Recorded on `sends` as the home's own (`system`). False
        when it was not typed: the tick looks again next time."""
        adapter = adapters.get(s.adapter)
        if getattr(adapter, "composer", None) is None or self._on_a_dialog(s):
            return False
        typing = self._typing[s.id]
        if typing.locked():
            return False
        async with typing:
            tail = await asyncio.to_thread(self.tmux.capture_tail, s.id, COMPOSER_LINES, raw=True)
            if adapter.composer(tail) != "":
                return False
            entry = self._record_send(s, SYSTEM, text)
            try:
                await self._type(s.id, adapter, text)
            except Exception as e:  # noqa: BLE001 — a failed send is not a sent one; next tick looks again
                entry.verdict = str(e) or type(e).__name__
                self.store.save(s)
                return False
        return True

    async def _replay(self, s: Session, why: str, **extra: Any) -> None:
        """One restart by the tick (§6): the record's launch record handed to `create` again — here,
        or at its node — the attempt appended to `restarts` and the list carried onto the new record,
        so the count survives the restart it counts. A replay that fails keeps its entry with `error`
        and counts all the same."""
        entry: dict[str, Any] = {"at": now_iso(), "why": why}
        history = [*s.restarts, entry]
        address = self._address(s)
        try:
            params = {**self._read_launch(address), **extra, "supervised": True}
            if s.host == self.host:
                view = await self.rpc_create(**params)
            else:
                view = await self._route_act("create", {**params, "host": s.host}, None, s.host)
        except Exception as e:  # noqa: BLE001 — a failed replay is a restart that failed, and counts (§6)
            entry["error"] = str(e) or type(e).__name__
            log.warning("%s: the %s restart failed: %s", s.id, why, entry["error"])
            s.restarts = history
            self._save(s)
            await self._push_changes()
            return
        rid, _h = naming.split_address(str((view or {}).get("id") or ""))
        new = self.sessions.get(rid) if s.host == self.host else self.remote.get(s.host, {}).get(rid)
        if new is not None:
            new.restarts = history
            self._save(new)
            await self._push_changes()

    def _read_launch(self, address: str) -> dict[str, Any]:
        """A launch record as `create`'s arguments (design §6): what `_write_launch` kept, less its own
        bookkeeping. Raises when there is none — a supervised record written before its launch record
        existed has nothing to replay, and that is a failed restart, said as one."""
        path = paths.launch_dir() / f"{address}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise RpcError(f"no launch record for {address}: nothing to replay (design §6)") from None
        if not isinstance(record, dict):
            raise RpcError(f"the launch record of {address} is not a record")
        params = {k: record[k] for k in LAUNCH_KEYS if k in record}
        if isinstance(record.get("controllers"), list):
            params["controllers"] = list(record["controllers"])
        return params

    async def _seat_pass(self, s: Session, now: datetime, records: list[Session]) -> None:
        """Rule 3 (design §6 *Keeping a team running*, §4.9b): a supervised seat's `seat_due` from its
        trigger, the fill of an ended seat that is due — `create` with `keep_mail`, so a question that
        was waiting is still there — under `FILL_CEILING`, and the close of a seat that has run and
        sits idle with nothing due and nothing left unpushed."""
        if not (s.seat and s.supervised and s.unattended) or s.superseded_by or s.suspended:
            return
        due = self._seat_due(s, now)
        if due != s.seat_due:
            s.seat_due = due
            self._save(s)
            await self._push_changes()
        if s.host != self.host and s.host not in self._link_muxes:
            return  # its link is down: left as it is, looked at again next tick (§4.4a)
        if s.state in ("exited", "closed") and s.seat_due:
            await self._fill(s, now, records)
        elif s.state == "idle" and not s.seat_due and self._seat_has_run(s, now):
            log.info("%s: a seat with nothing due, idle and pushed — closing it (§6 rule 3)", s.id)
            if s.host == self.host:
                await self.rpc_close(s.id)
            else:
                await self._route_act("close", {"id": s.id}, None, s.host)

    def _seat_due(self, s: Session, now: datetime) -> dict[str, Any] | None:
        """`seat_due: {at, by}` (§6 rule 3): set once the trigger is met and kept until the fill — but
        `asks` is a question waiting now, so it clears again if none is. `prs` reads `seat_count`,
        which `_count_seats` keeps; `every` is the time since this record was created."""
        seat = s.seat or {}
        trigger = seat.get("trigger")
        if trigger == "asks":
            return (s.seat_due or {"at": now_iso(), "by": "asks"}) if s.asks_waiting(home=self.host) else None
        if s.seat_due:
            return s.seat_due
        met = False
        if trigger == "prs":
            count = (s.seat_count or {}).get("prs")
            after = str(seat.get("after") or "")
            met = after.isdigit() and isinstance(count, int) and count >= int(after)
        elif trigger == "every":
            every = _duration(str(seat.get("after") or ""))
            met = every is not None and now - _parse(s.created) >= every
        return {"at": now_iso(), "by": trigger} if met else None

    async def _fill(self, s: Session, now: datetime, records: list[Session]) -> None:
        """A due seat, ended, is filled — unless its profile is paused, or it or its fellows are at
        the fill ceiling: six fills an hour over all seats sharing a controller (the graph, never the
        team badge). The seat whose fill tripped it gets `restart_ceiling` and the Inbox row; its
        fellows are merely refused until the hour rolls. Fills never count toward `RESTART_CEILING`."""
        if s.restart_ceiling or self._profile_gated(s.profile, now, s.team):
            return
        mine = set(self._ctl(s))
        fellows = [
            r for r in records if r.seat and not r.superseded_by and (r is s or (mine and mine & set(self._ctl(r))))
        ]
        fills = [
            e
            for r in fellows
            for e in r.restarts
            if isinstance(e, dict) and e.get("why") == "fill" and _recent(e.get("at"), now, FILL_WINDOW)
        ]
        if len(fills) >= FILL_CEILING:
            tripped = any(
                r is not s
                and isinstance(r.restart_ceiling, dict)
                and _recent(r.restart_ceiling.get("at"), now, FILL_WINDOW)
                for r in fellows
            )
            if not tripped:
                s.restart_ceiling = {"at": now_iso(), "count": len(fills), "why": "fill"}
                log.warning(
                    "%s: %d seat fills in %s — the ceiling; it is a person's now", s.id, len(fills), FILL_WINDOW
                )
                self._save(s)
                await self._push_changes()
            return
        log.info("%s: the seat is due (%s) — filling it", s.id, (s.seat_due or {}).get("by"))
        await self._replay(s, "fill", keep_mail=True)

    def _seat_has_run(self, s: Session, now: datetime) -> bool:
        """A seat to close (§6 rule 3): hook-confirmed idle for `SEAT_IDLE_GRACE`, with its git known
        and nothing uncommitted or unpushed — a seat that left work is the board's, as today."""
        git = s.git or {}
        return bool(
            s.state == "idle"
            and s.confidence == "hook"
            and s.since
            and now - _parse(s.since) >= SEAT_IDLE_GRACE
            and git
            and not git.get("dirty")
            and not git.get("unpushed")
            and not s.pending
        )

    async def _refresh_repos(self) -> None:
        """The repo facts (design §4.4 *Repo facts*, TD-176): for every checkout the repos registry
        lists, the PRs and the ledger with its history every `REPOS_EVERY`, and the ledger alone on
        any tick its file's mtime moved. The PRs are read once per remote, so two checkouts of one
        repo are one `gh` read. A read that failed keeps the last reading with `error` and
        `failed_at` beside it — an outage is never zero PRs. A changed reading is saved and pushed
        as a `repos` event; a checkout the registry no longer lists is dropped with `repo: null`."""
        try:
            due = time.monotonic() - self._repos_read_at >= REPOS_EVERY
            roots = hosts.local_host().repos()
            mtimes = await asyncio.to_thread(self._ledger_mtimes, roots)
            todo = [r for r in roots if due or mtimes.get(r) != self._ledger_mtime.get(r) or r not in self._repos]
            gone = [r for r in self._repos if r not in roots]
            if not (todo or gone):
                return
            prev = {r: self._repos.get(r) or {} for r in todo}
            # a checkout read for the first time gets the whole reading at once, not in five minutes
            full = {r for r in todo if due or r not in self._repos}
            got = await asyncio.to_thread(self._read_repos, todo, prev, full) if todo else {}
            if due:
                self._repos_read_at = time.monotonic()  # after the read: a read that raised is retried next tick
            for root in todo:
                self._ledger_mtime[root] = mtimes.get(root)
            changed = {r: v for r, v in got.items() if v != self._repos.get(r)}
            for r in gone:
                self._repos.pop(r, None)
                self._ledger_mtime.pop(r, None)
            self._repos.update(changed)
            if changed or gone:
                self.repos_store.save(self._repos)
            for r, v in changed.items():
                await self._broadcast({"event": "repos", "root": r, "repo": v})
            for r in gone:
                await self._broadcast({"event": "repos", "root": r, "repo": None})
        except Exception:  # noqa: BLE001 — a detached task: log it, and the next tick tries again
            log.exception("reading the repo facts failed")

    @staticmethod
    def _ledger_mtimes(roots: list[str]) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for root in roots:
            try:
                out[root] = (Path(root) / ledger_mod.ledger_path(root)).stat().st_mtime
            except OSError:
                out[root] = None
        return out

    @staticmethod
    def _read_repos(roots: list[str], prev: dict[str, dict[str, Any]], full: set[str]) -> dict[str, dict[str, Any]]:
        """Each checkout's reading, in a thread: `{name, root, remote, ledger, prs, at}`. A root in
        `full` has its PRs and the ledger's history read; the others their ledger's entries alone,
        the rest carried from `prev`. One checkout's read that raises keeps its last reading with
        the error beside it and never costs the others theirs."""
        now = datetime.now(UTC)
        stamp = now.isoformat()
        by_remote: dict[str, dict[str, Any]] = {}
        out: dict[str, dict[str, Any]] = {}
        for root in roots:
            old = prev.get(root) or {}
            try:
                out[root] = HostAgent._read_repo(root, old, root in full, now, by_remote)
            except Exception as e:  # noqa: BLE001 — one checkout's surprise is its reading's error, not the batch's
                log.exception("reading the repo facts of %s failed", root)
                why = f"the read failed: {type(e).__name__}"
                out[root] = {
                    "name": Path(root).name,
                    "root": root,
                    "remote": str(old.get("remote") or ""),
                    "ledger": {**(old.get("ledger") or {}), "error": why, "failed_at": stamp},
                    "prs": {**(old.get("prs") or {}), "error": why, "failed_at": stamp},
                    "at": str(old.get("at") or stamp),
                }
        return out

    @staticmethod
    def _read_repo(
        root: str, old: dict[str, Any], due: bool, now: datetime, by_remote: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        """One checkout's reading (`_read_repos`): `by_remote` holds the PR readings taken in this
        pass, so two checkouts of one remote are one `gh` read."""
        stamp = now.isoformat()
        remote = reports._git(root, "remote", "get-url", "origin") if due else None
        remote = (remote or "").strip() if due else str(old.get("remote") or "")
        led = ledger_mod.reading(root, now, with_history=due)
        old_led = old.get("ledger") or {}
        if "error" in led:
            led = {**old_led, "error": led["error"], "failed_at": stamp}
        elif not due:
            led.update({k: old_led[k] for k in ("windows", "recent", "history_error") if k in old_led})
        elif "history_error" in led:
            led.update({k: old_led[k] for k in ("windows", "recent") if k in old_led})
        prs = old.get("prs")
        if due:
            if remote and remote in by_remote:
                prs = by_remote[remote]
            else:
                fresh = reports.pr_reading(root, now)
                prs = {**(prs or {}), "error": fresh["error"], "failed_at": stamp} if "error" in fresh else fresh
                if remote:
                    by_remote[remote] = prs
        return {
            "name": Path(root).name,
            "root": root,
            "remote": remote,
            "ledger": led,
            "prs": prs,
            "at": stamp if due else str(old.get("at") or stamp),
        }

    async def _count_seats(self, records: list[Session]) -> None:
        """`seat_count` for every supervised `prs:` seat (§6 rule 3): the PRs merged to the seat's repo
        since its record was created, one `gh` read per repo, run at the home in the seat's checkout
        — a node's is at the same absolute path (§4.4a); a path that is not a directory here gives no
        reading. A read that failed leaves the last reading: an outage is never zero merges."""
        try:
            seats = [
                r
                for r in records
                if (r.seat or {}).get("trigger") == "prs" and r.supervised and r.unattended and not r.superseded_by
            ]
            where = {r.id: r.repo or r.dir for r in seats}
            dirs = sorted(set(where.values()))
            found = await asyncio.gather(*(asyncio.to_thread(reports.merged_prs, d) for d in dirs))
            merged = dict(zip(dirs, found, strict=True))
            for r in seats:
                got = merged.get(where[r.id])
                if got is None or r.superseded_by:
                    continue  # no reading; or a fill or restart replaced it while `gh` was out
                since = _parse(r.created)
                count = {"prs": sum(1 for at in got if at > since), "at": now_iso()}
                if (r.seat_count or {}).get("prs") != count["prs"]:
                    r.seat_count = count
                    self._save(r)
        except Exception:  # noqa: BLE001 — a detached task: log it, and the next pass tries again
            log.exception("counting the seats' PRs failed")
        finally:
            await self._push_changes()

    @staticmethod
    def _on_a_dialog(s: Session) -> bool:
        """Typing at a session on a permission or a question would answer the dialog rather than
        reach the composer (§4.2) — the rule `send` refuses by."""
        return bool(s.pending and s.pending.kind in ("permission", "question"))

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
            # …and a declared claim's `review_pr` (TD-150), re-checked by number, so a merge or a close
            # clears it after the session has moved to its next branch
            reviews = [
                (e.ref, e.review_pr)
                for e in s.progress
                if e.source == "declared" and e.status == "claimed" and e.review_pr and not e.pr
            ]
            # Branch-only claims from a branch this record is no longer on (TD-045). Only for a
            # record that still holds its directory: what is checked out elsewhere says nothing
            # about this one, and an exited worker's claims are its history, not a live question.
            left = (
                [(e.ref, e.branch) for e in s.progress if _is_branch_claim(e) and e.branch != branch]
                if s.id in holders
                else []
            )
            if not branch and not pending and not left and not reviews:
                continue
            due.append((s, branch, [(e.ref, e.pr) for e in pending if e.pr], left, reviews))
        if not due:
            return
        results = await asyncio.gather(
            *(
                # the ledger path the client read from the repo's config at create (design §5
                # `ledger:`), else the default — this package never reads `.agentorc.yml` itself
                asyncio.to_thread(
                    reports.derive, s.dir, branch, pend, s.ledger or reports.LEDGER_DEFAULT, left, reviews
                )
                for s, branch, pend, left, reviews in due
            ),
            return_exceptions=True,
        )
        for (s, _branch, _pending, _left, _reviews), result in zip(due, results, strict=True):
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
            # a refused derived entry still leaves its PR beside a declared claim (TD-150)
            applied += [live.note_review(e) for e in progress]
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
        """Ask each account a live agent session's profile names for its usage every
        `USAGE_EVERY`, once per account (§4.2a, TD-122), in a thread; a fetch failure keeps the
        last answer and never gates anything (design §6).
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
        # One poll per account, never per profile (§4.2a, TD-122): four profiles split by role on
        # one login asked four times, and the endpoint answered `rate_limited` to all of them.
        groups: dict[str, list[str]] = {}  # account key → the live profiles sharing it
        meta: dict[str, dict[str, str]] = {}  # account key → what the chip names it by
        ask: dict[str, tuple[Any, str]] = {}  # account key → (usage_for, the profile asked through)
        for s in live:
            ad = adapters.get(s.adapter)
            fn = getattr(ad, "usage_for", None)
            if not fn:
                continue
            key, account = _usage_key(ad, s.adapter, s.profile)
            profs = groups.setdefault(key, [])
            if s.profile not in profs:
                profs.append(s.profile)
            meta.setdefault(key, {"account": account, "tool": str(getattr(ad, "label", "") or s.adapter)})
            ask.setdefault(key, (fn, s.profile))
        for key, profs in groups.items():
            self._usage_seed(key, profs)
        due: dict[str, tuple[Any, str]] = {}
        for key in groups:
            wait = self._usage_wait.get(key, agent_common.USAGE_EVERY)
            if mono - self._usage_checked.get(key, -wait) >= wait:
                due[key] = ask[key]
        changed = False
        if due:
            results = await asyncio.gather(
                *(asyncio.to_thread(fn, prof) for fn, prof in due.values()), return_exceptions=True
            )
            for key, r in zip(due, results, strict=True):
                self._usage_checked[key] = mono
                if (merged := self._usage_reading(key, r)) is not None:
                    self._usage_acct[key] = merged
        # every profile sharing an account carries its reading — windows, `fetched`, `reason` alike
        for key, profs in groups.items():
            if (acct := self._usage_acct.get(key)) is None:
                continue
            reading = {**acct, **meta[key]}
            for prof in profs:
                if self._usage.get(prof) != reading:
                    self._usage[prof] = reading
                    changed = True
                    await self._broadcast({"event": "usage", "profile": prof, "usage": reading})
        if changed:
            self.usage_store.save(self._usage)  # once for the batch: the file is whole either way
        # Only profiles a live session is running under are shown (TD-073, Paul 2026-09-19): one
        # account in use is one chip, and last night's profile does not sit in the top bar all day.
        for key in [k for k in self._usage_acct if k not in groups]:
            self._usage_acct.pop(key, None)
            self._usage_checked.pop(key, None)
            self._usage_wait.pop(key, None)
        shown = {p for profs in groups.values() for p in profs}
        if dropped := [p for p in self._usage if p not in shown]:
            for prof in dropped:
                self._usage.pop(prof, None)
                await self._broadcast({"event": "usage", "profile": prof, "usage": None})
            self.usage_store.save(self._usage)
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

    def _usage_seed(self, key: str, profs: list[str]) -> None:
        """An account the poll has not met since the agent started takes its reading, and its
        poll's allowance, from the newest reading its profiles hold (TD-087, TD-122): a restart
        keeps the chip and does not ask sooner than `fetched + USAGE_EVERY`. A reading with no
        readable time is polled at once."""
        held = [self._usage[p] for p in profs if isinstance(self._usage.get(p), dict)]
        if key not in self._usage_acct and held:
            newest = max(held, key=lambda r: str(r.get("fetched") or ""))
            self._usage_acct[key] = {k: v for k, v in newest.items() if k not in ("account", "tool")}
        if key not in self._usage_checked and (seen := [m for r in held if (m := _usage_checked_at(r)) is not None]):
            self._usage_checked[key] = max(seen)

    def _usage_reading(self, key: str, r: Any) -> dict[str, Any] | None:
        """One poll's answer folded into what this account already had (TD-087, TD-122: `key` is
        the account's, so a backoff holds every profile on it), or None when nothing changed and
        nothing need be said.

        An adapter now answers with a **reason** rather than a silence: `ok` with the windows,
        or `rate_limited` / `no_credentials` / `no_profile` / `error` with none. A reading is
        replaced only by a newer reading — a failure **keeps the last one**, with the reason
        beside it, because *the chip went out* and *the allowance is spent* are different things
        to a person and a five-hour window does not change while we are refused. The backoff is
        set here too: a 429 waits the endpoint's own `Retry-After`, or doubles to a ceiling; any
        other answer, good or bad, goes back to the ordinary cadence, since only a 429 is the
        endpoint telling us to ask less often."""
        if isinstance(r, BaseException) or not isinstance(r, dict):
            r = {"reason": "error"}
        reason = str(r.get("reason") or ("ok" if r.get("windows") is not None else "error"))
        if reason == "rate_limited":
            after = r.get("retry_after")
            prev = self._usage_wait.get(key, agent_common.USAGE_EVERY)
            # the endpoint's own word is floored at the ordinary cadence and **not** capped: a
            # server saying *an hour and a half* is telling us something our ceiling is guessing
            # at. The ceiling is for our own doubling, which has no such word behind it.
            self._usage_wait[key] = (
                max(float(after), agent_common.USAGE_EVERY)
                if isinstance(after, int | float)
                else min(prev * 2, USAGE_BACKOFF_MAX)
            )
        else:
            self._usage_wait.pop(key, None)
        was = self._usage_acct.get(key) or {}
        if was.get("reason") != reason:
            # once per change of reason, never per poll: a 429 every five minutes is one line
            log.info("usage for account %s: %s (was %s)", key, reason, was.get("reason") or "no reading yet")
        if reason == "ok":
            out = {"windows": r.get("windows"), "fetched": r.get("fetched"), "reason": "ok"}
        else:
            out = {**{k: v for k, v in was.items() if k in ("windows", "fetched")}, "reason": reason}
            if isinstance(r.get("retry_after"), int | float):
                out["retry_after"] = r["retry_after"]
        return out if out != was else None

    def _reconcile(self, panes: dict[str, PaneInfo], tails: dict[str, list[str]], snapshot_at: datetime) -> None:
        for sid, event in self.events.drain():
            self._apply_event(sid, event, queued=True)
        now = datetime.now(UTC)
        mono = time.monotonic()
        self._removed = {k: v for k, v in self._removed.items() if mono - v[1] < REMOVED_GUARD_SECONDS}
        for sid, s in list(self.sessions.items()):
            if s.state == "closed":
                if s.closed_at and _parse(s.closed_at) + agent_common.CLOSED_KEEP < now:
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
                if _parse(s.created) + agent_common.CREATE_GRACE < snapshot_at and (s.state != "exited" or s.pane):
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

    def _apply_event(self, sid: str, event: dict[str, Any], queued: bool = False) -> None:
        """A hook-fed state transition (design §4.2 table). Adapters map hook names to these.

        A **queued** event (the hook's call failed and it wrote `events/<session>.jsonl`, drained
        at the tick) carries `at`, when it happened. One stamped before an event that reached the
        record live is older than the state that event set, so its state is skipped; its adapter
        id, model and subagent count still apply (TD-169)."""
        s = self.sessions.get(sid)
        if s is None:
            return
        at = event.get("at") if queued else None  # a queue written before the stamp is applied as it was
        stale = isinstance(at, int | float) and at < self._live_hook_at.get(sid, 0.0)
        aid = event.get("adapter_id")
        if aid and s.adapter_id and aid != s.adapter_id and event.get("state") in ("exited", "closed"):
            # The end of a run this record no longer holds (TD-186): a restart closed the old run and
            # created this one under the same id, and the old run's SessionEnd landed after it. It is
            # not this run's exit, and its tool id is not this run's — applied, it marked a live run
            # `exited` and made its own tool session read as *outside agentorc* to the anchor rule.
            log.info("%s: ignored the end of a previous run (%s; this run is %s)", sid, aid, s.adapter_id)
            return
        if not queued:
            self._live_hook_at[sid] = time.time()
        self._last_hook[sid] = datetime.now(UTC)  # apply time, also for events drained from the offline queue
        if aid:
            s.adapter_id = aid
        if model := event.get("model"):
            s.model = str(model)  # SessionStart's `model`, or a `/model` switch (TD-031)
        if delta := event.get("subagent_delta"):
            s.subagents = max(0, s.subagents + int(delta))
        state = None if stale else event.get("state")
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
            self._live_hook_at,
            self._killed_at,
            self._mail_hints,
            self._asks_hints,
            self._bells,
        ):
            side.pop(sid, None)
        if not (lock := self._typing.get(sid)) or not lock.locked():
            self._typing.pop(sid, None)  # a held one stays: its typist is mid-paste, the name reused or not
        for key in [k for k in self._attention_how if k.split("|", 1)[0] == sid]:
            del self._attention_how[key]
        for key in [k for k in self._attention if k.split("|", 1)[0] == sid]:
            del self._attention[key]  # belt and braces: `_attention_gone` wrote these out already

    def _write_launch(self, address: str, s: Session, params: dict[str, Any]) -> None:
        """The launch record of a supervised create (design §6 *Keeping a team running*): what the
        create was handed — adapter, profile, the prompt as handed, lane, name, directory, worktree,
        role, badges — with the record's own `controllers` (the creator added), written at the home
        as `launch/<address>.json`, which a restart replays rather than re-reading any definition.
        Written at every such create, so a resume with changes leaves the person's latest choices;
        owner-only, since it holds the brief. A failure to write is logged, never the create's."""
        record = {**params, "controllers": list(s.controllers), "supervised": True, "id": address, "at": now_iso()}
        path = paths.launch_dir() / f"{address}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(0o700)  # as the launch scripts beside it have it (tmux.py)
            tmp = path.with_suffix(".json.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner-only from the start
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=1)
            tmp.replace(path)
        except OSError as e:
            log.warning("%s: could not write its launch record: %s", address, e)

    @staticmethod
    def _drop_launch(address: str) -> None:
        with contextlib.suppress(OSError):
            (paths.launch_dir() / f"{address}.json").unlink(missing_ok=True)

    def _forget(self, sid: str) -> None:
        gone = self.sessions.get(sid)
        if gone is None:
            return  # already forgotten (two removes of one id in flight): nothing more to announce
        self._drop_launch(sid)  # the launch record goes with the record on Forget (§6)
        # Its open `ask`s expire with it (design §4.10 lifecycle): the record and its inbox go, and
        # every other holder of those asks — the askers — is told so. Done while it is still in the
        # map so `_mark` reaches it, harmlessly, along with the rest. A `steer` is the exception:
        # its bound runs whatever becomes of the addressee, so the sender's copy lapses on time.
        for e in [e for e in gone.inbox if e.open and e.kind != "steer"]:
            self._close_entry(e.id, "expired", now_iso())
        self._asker_gone(gone, self._address(gone))
        self._attention_gone(gone, "forgotten")
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
        pause_prompt: str | None = None,
        resume_prompt: str | None = None,
        host: str | None = None,
        caller: str | None = None,
        keep_mail: bool = False,
        supervised: bool = False,
        seat: dict[str, Any] | None = None,
        review: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """`review` (design §4.9b *The reader*, TD-093): the role preset's `{reader, held, bound}`,
        checked and kept on the record, read afterwards by the author's own `ao`.

        `supervised` (design §6 *Keeping a team running*, TD-103 slice 1): kept on the record,
        and a resume of a supervised record stays supervised whether or not it says so — the field
        is cleared by nothing but Forget. With it, the create writes the session's launch record
        (`_write_launch`), here unless this is a node, whose home writes it when it routes the create.

        `keep_mail` (design §4.9b, TD-075 step 4): a fresh start under a name moves the mail of
        the record it supersedes — inbox, outbox, tallies, wake decisions — as a resume does, and
        resumes nothing of its conversation. It is how a techlead seat is filled without forgetting
        the questions that caused the fill. Handing a record's mailbox to a successor is an act on
        that record, so it is open to a person and to the record's own controllers alone."""
        if host and host != self.host:
            # Routed before the method runs (`_act_host`) when this is the home; a node asked for
            # another host's create got here through the link, and the link is one host's.
            raise RpcError(f"{host} is not this host ({self.host}): a create lands on the host it names")
        directory = Path(dir).expanduser().resolve()
        if not directory.is_dir():
            raise RpcError(f"not a directory: {directory}")
        grants, references = _grants(capabilities or []), _lane(lane or [])  # validate before anything starts
        reading = _review(review)
        if seat is not None and not (
            isinstance(seat, dict) and isinstance(seat.get("trigger"), str) and seat["trigger"]
        ):
            raise RpcError("seat is {trigger, after?}: the trigger its team definition gives it (design §4.9b)")
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
        # `+ 1` for the argument's NUL, exactly as `_fit` counts it: a prompt of *exactly*
        # `ARG_LIMIT` bytes passes a bare `>` here and is still refused there — after the worktree
        # (review of PR #272).
        if prompt and (size := len(str(prompt).encode("utf-8", "surrogateescape")) + 1) > ARG_LIMIT:
            raise RpcError(
                f"the prompt is {size - 1:,} bytes — past what a process can be started with ({ARG_LIMIT:,}): "
                "put a brief that long in a file and tell the session to read it (design §4.9, TD-068)"
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
            if isinstance(holder, Session):
                self._refuse_suspended(holder, caller, "a create under that name")
            if keep_mail:
                self._check_keep_mail(holder, caller, resume)
            if resume:
                for held in [r for r in self.sessions.values() if r.adapter_id == resume and r.suspended]:
                    self._refuse_suspended(held, caller, "a resume of that conversation")
                    # a person got past that line, which **is** the lift (§4.8a) — and it lifts
                    # here rather than only in `_take_name`, because a resume under another name
                    # leaves this record standing, and a mark nothing can clear would hold its
                    # old name for ever (review of PR #301)
                    held.suspended = None
                    self._save(held)
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
            if isinstance(holder, Session) and resume and holder.adapter_id == resume:
                # its row ended because the person opened it, which is the word the trail wants —
                # `_take_name` would otherwise write the ending of a record merely replaced (§4.10)
                self._attention_ended(holder.id, "resumed", "*")
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
                run_until=self._team_stamp(str(team or ""), bool(unattended), _stop_time(run_until)),
                wrapup_prompt=(str(wrapup_prompt).strip() or None) if wrapup_prompt else None,
                pause_prompt=(str(pause_prompt).strip() or None) if pause_prompt else None,
                resume_prompt=(str(resume_prompt).strip() or None) if resume_prompt else None,
                previous_run=previous_run,
                host=self.host,
                # a resume carries it — of the name's record or the conversation's (§6: Forget alone clears it)
                supervised=bool(supervised)
                or bool(resume and isinstance(holder, Session) and holder.adapter_id == resume and holder.supervised)
                or any(r.supervised for r in self.sessions.values() if resume and r.adapter_id == resume),
                seat=dict(seat) if seat else None,
                review=reading,
            )
            if isinstance(holder, Session):
                # the record of this name it replaced, for the home, which holds the mail (§4.4a)
                kept = bool(keep_mail or (resume and holder.adapter_id == resume))
                s.supersedes = [{"id": holder.id, "mail": kept, "at": s.created}]
            self.sessions[sid] = s
            self.store.save(s)
            self._remember_dir(directory)
            if resume:
                await self._supersede(resume, sid, replaced=holder if isinstance(holder, Session) else None)
            elif keep_mail and isinstance(holder, Session):
                self._move_mail(holder, s)  # the seat's mail, to the seat's next holder (§4.9b)
                self.store.save(s)
            if self.mode != "node":
                if s.supervised:
                    self._write_launch(s.id, s, launch_params(locals()))
                else:
                    self._drop_launch(s.id)  # a fresh start that took a supervised record's id is not it
        return s.view()

    def _check_keep_mail(self, holder: Session | str | None, caller: Any, resume: str | None) -> None:
        """`create(keep_mail=true)` is refused, naming the rule, unless it has a record to keep the
        mail of and the caller may hand that record's mailbox on (design §4.9b)."""
        if resume:
            raise RpcError("a resume carries its mail already: --keep-mail is for a fresh start (design §4.9b)")
        if not isinstance(holder, Session):
            raise RpcError(
                "--keep-mail keeps the mail of the record this name held, and no record holds it (design §4.9b)"
            )
        if not mail.is_person(caller) and self._addr(str(caller)) not in self._ctl(holder):
            raise RpcError(
                f"{caller} is not a controller of {holder.id}: handing a record's mailbox to a successor is an act "
                "on that record, open to a person and to its own controllers (design §4.9b, §9 invariant 11)"
            )

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
        if holder.suspended:
            # §4.8a *An alarm's answers*, TD-077 a2: **the one exception to §4.1's rule that an
            # exited holder is superseded**. A verdict of its own rather than `supersede`, so the
            # form, `ao new` and `ao team start` all refuse without each knowing the rule — which
            # is what this function exists for. A person still may: `create` reads the caller, and
            # their own resume is one of the two things that lift it (the other is Forget).
            out |= {
                "verdict": "suspended",
                "holder": holder.id,
                "holder_state": holder.state,
                "message": (
                    f"{name} was suspended by a person at {holder.suspended.get('at')} over an identity alarm "
                    f"({holder.suspended.get('why') or 'no reason recorded'}) — no session may take that name; "
                    "a person lifts it by resuming it themselves or forgetting it"
                ),
                "hint": f"look at it first: ao tail {holder.id}",
            }
            # the holder is returned all the same: a **person** may take this name, and `create`
            # is where the caller is known — `_refuse_suspended` refuses a session there, and a
            # person's create supersedes the record in place, which is what lifts the mark
            return out, holder
        out |= {
            "verdict": "supersede",
            "holder": holder.id,
            "holder_state": holder.state,
            "message": f"replaces the {holder.state} {name} — run log kept",
        }
        return out, holder

    @staticmethod
    def _refuse_suspended(s: Session, caller: Any, road: str) -> None:
        """A suspended record is not brought back by a session (design §4.8a *An alarm's answers*,
        TD-077 a2). Both roads are closed — `create` under its name, and `create --resume` of its
        conversation — which is **the one exception to §4.1's rule that an exited holder is
        superseded**, and §4.1 says so. A **person** walks either road freely: their own resume is
        one of the two things that lift a suspension, the other being Forget."""
        if not s.suspended or mail.is_person(caller):
            return
        mark = s.suspended
        raise RpcError(
            f"{s.name} was suspended by a person at {mark.get('at')} over an identity alarm "
            f"({mark.get('why') or 'no reason recorded'}): {road} is refused to every session, and only a "
            f"person lifts it — by resuming it themselves or forgetting it (design §4.8a)"
        )

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
        # The replaced record's rows end here, with the record: it is gone from the graph, and the
        # new session under its id must not inherit what it was showing (review of PR #269).
        self._attention_gone(holder, "forgotten")
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

    async def _supersede(self, adapter_id: str, new_sid: str, replaced: Session | None = None) -> None:
        """A resumed conversation continues in the new session: the exited record it came from is
        closed (kept a day, sorted last) and its dead pane dropped, so the Org shows one card.

        **Resume carries mail forward** (design §4.10 lifecycle): every entry still inside the
        retention window, read or unread, the exchange tallies and `sends` move to the new record,
        because the conversation they were addressed to is the one continuing — a worker that read
        an `ask`, crashed and was resumed must not lose the thread it was answering. Ids follow the
        move: the old id is rewritten to the new one in the moved entries' `to`, and in every other
        record's pair tallies and pending-`ask` addressees; an `ask` left pending by the exit is
        open again. The old record remembers its successor, so a message addressed to it is
        forwarded there (`rpc_msg`) while an act on it is refused, as on any closed record.

        **A resume under the same name** has no such old record to find: §4.1's name rule replaced
        it **in place, at the same id** (`_take_name`), so the loop below — which looks for a
        *different*, `exited` record — finds nothing and the conversation's mail would be dropped
        by the very path that exists to carry it. `replaced` is what the name rule superseded, and
        when it is the record this resume continues (the same conversation, at the id the new
        session now holds) its mail moves from it (TD-081). Nothing is closed or forwarded in that
        case: there is one record and one id, and the successor *is* the record."""
        new = self.sessions.get(new_sid)
        for other in list(self.sessions.values()):
            if other.id != new_sid and other.adapter_id == adapter_id and other.state == "exited":
                await asyncio.to_thread(self.tmux.kill_session, other.id)
                other.set_state("closed", confidence=other.confidence)
                other.closed_at = now_iso()
                other.superseded_by = new_sid
                if new is not None:
                    self._move_mail(other, new)
                    new.supersedes.append({"id": other.id, "mail": True, "at": new.created})
                self.store.save(other)
        if replaced is not None and new is not None and replaced.id == new_sid and replaced.adapter_id == adapter_id:
            self._move_mail(replaced, new)
        if new is not None:
            self.store.save(new)

    def _move_mail(self, old: Session, new: Session) -> None:
        # The row the old record was showing ends here, and the trail says *resumed* — which is
        # what Paul saw vanish: opening a row's session resumed it (design §4.10, TD-079). **Only
        # when there are two ids**: under the same name the ending was marked in `rpc_create`,
        # before the name was taken, and marking it again here would leave the word on the id the
        # **live** record now holds — where `*` stands until the record goes, so its next, unrelated
        # ending would wear *resumed* with nothing resumed (review of PR #282).
        #
        # Ids are **addresses** here (`_address`): the same thing on this host's records, and
        # `id@host` on a node's records at the home, which moves their mail when a node reports
        # the supersession (`_take_supersession`, TD-057).
        a, b = self._address(old), self._address(new)
        if a != b:
            self._attention_ended(a, "resumed", "*")
        now = datetime.now(UTC)
        new.inbox = self._rename([self._copy(e) for e in old.inbox if self._keep(e, now, inbox=True)], a, b)
        new.outbox = self._rename([self._copy(e) for e in old.outbox if self._keep(e, now, inbox=False)], a, b)
        new.threads = {k.replace(f"pair:{a}", f"pair:{b}"): t for k, t in old.threads.items()}
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
        moved = [e for e in self.person_inbox if e.from_ == a]
        for e in moved:
            e.from_ = b
        if moved:
            self.person_store.save(self.person_inbox)
        for r in self._graph().values():
            if r is old or r is new:
                continue
            # the mailbox is addressed as this host reads it, on every record (`_msg` lands entries
            # by graph address) — only `controllers` are kept in a node's own form
            touched = False
            if f"pair:{a}" in r.threads:
                r.threads[f"pair:{b}"] = r.threads.pop(f"pair:{a}")
                touched = True
            pending = [e for e in (*r.inbox, *r.outbox) if a in e.pending]
            if pending:  # the addressee that exited is back: its ask is open again, addressed to it
                self._rename(pending, a, b)
                touched = True
            if touched:
                self._save(r)

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

    async def rpc_remove(self, id: str, caller: Any = None) -> None:
        s = self._get(id)
        if s.state not in ("exited", "closed"):
            raise RpcError(f"{id} is {s.state}; kill it first")
        # **Forget is the other road out of a suspension** (design §4.8a, TD-077 a2), so it is a
        # person's, as the resume is. The acting gate lets a controller remove an unattended
        # member's record — which for a suspended one would free the name and let the very
        # session under suspicion be started again, unmarked (review of PR #301).
        self._refuse_suspended(s, caller, "forgetting it")
        # The dead pane is kept until now (exit code, last screen); without this it would be
        # re-adopted as a nameless shell on the next tick (first-use finding 2026-09-06).
        # one extra `list-panes -a` per remove, a person-driven action: accepted
        pane = (await asyncio.to_thread(self.tmux.main_panes, naming.PREFIX)).get(id)
        await asyncio.to_thread(self.tmux.kill_session, id)
        self._forget(id)
        self._removed[id] = (pane.created if pane else None, time.monotonic())
        await self._push_changes()

    async def rpc_send(
        self,
        id: str,
        text: str,
        wait: bool = False,
        timeout: float | None = None,
        caller: Any = None,
        wrapup: bool = False,
    ) -> dict | None:
        """Type a prompt. With `wait` (TD-016, design §4.2): return the record once the session has
        started on *this* prompt and settled again (`SETTLED`). A session that is busy queues the
        prompt behind its current turn, so the wait first lets that turn end, then looks for the
        next one to start. Errors: `prompt-stalled` when nothing starts within `SEND_STALL_SECONDS`
        of the moment it could, `timeout` after `timeout` seconds in total, `removed` if the record
        goes away. Nothing is ever re-sent on a guess (design §4.2). `caller` is the envelope's,
        injected by the dispatcher: every send that reaches the pane is recorded on the record as
        `sends`, with who typed it (design §4.10). `wrapup` says this text is the wrap-up prompt
        (the card's Wrap up, `ao team stop`): it is stamped on the record as `wrapup_at`, which holds
        the doorbell off, and a send without it clears the stamp (§4.10 "A pending stop beats mail")."""
        s = self._get(id)
        self._refuse_gone(s)
        if s.pending and s.pending.kind in ("permission", "question"):
            raise RpcError(f"{id} has a pending {s.pending.kind}; answer it in the terminal")
        s.wrapup_at = now_iso() if wrapup else None
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
        stall = agent_common.SEND_STALL_SECONDS if left() is None else min(agent_common.SEND_STALL_SECONDS, left())
        if not await self._wait_state(id, lambda x: x.rev != rev_before, stall):
            if id not in self.sessions:
                raise RpcError(f"removed: {id} went away while waiting")
            raise RpcError(f"prompt-stalled: {id} showed no activity within {stall:g} s")
        if not await self._wait_state(id, lambda x: x.state in SETTLED and x.rev != rev_before, left()):
            self._raise_not_settled(id, timeout)
        return self._get(id).view()

    @staticmethod
    def _refuse_gone(s: Session) -> None:
        """A record with **no turn to type into** is refused in words (design §4.5a *Focus
        composer*, §4.10 lifecycle). Two of them: a **closed** record — a resumed conversation's
        old id in particular, where only mail is forwarded to the successor, never keystrokes —
        and an **exited** one, whose pane is gone or dead. Exited was not refused until 2026-09-20
        (TD-078): the page disabled its composer, the RPC did not, and a send that arrived anyway
        went to tmux, which answered `no current target` — a tmux error for a records question,
        with nothing in it to say which session had gone or when. That is how the flake was read."""
        if s.state == "closed":
            where = f"; it was resumed as {s.superseded_by}" if s.superseded_by else ""
            raise RpcError(f"{s.id} is closed{where}: nothing is typed into a closed session")
        if s.state == "exited":
            code = f" (code {s.exit_code})" if s.exit_code is not None else ""
            raise RpcError(f"{s.id} has exited{code}: there is no turn to type into — resume it, or forget it")

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
        """`_type`, holding the session's typing lock: one typist per pane (TD-094)."""
        async with self._typing[sid]:
            await self._type(sid, adapter, text)

    async def _type(self, sid: str, adapter: Any, text: str) -> None:
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
            if await self._poll(lambda: _falsy(composer()), agent_common.SUBMIT_SECONDS):
                return
            log.warning(
                "send %s: the composer still shows the prompt %gs after %s", sid, agent_common.SUBMIT_SECONDS, key
            )
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
        self._refuse_gone(s)
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
        return self._view(s)

    async def rpc_set_mode(
        self, id: str, unattended: bool, pause_prompt: str | None = None, resume_prompt: str | None = None
    ) -> dict[str, Any]:
        """The mode toggle and `ao mode`. The usage gate's two texts ride along as the wrap-up's
        ride `set_stop` (design §6, TD-100): a session made unattended after its create would
        otherwise be gated with nothing to type. Given only when set; an empty one clears it."""
        s = self._find(id)  # the home's own copy of another host's record too (4a)
        s.unattended = bool(unattended)
        if pause_prompt is not None:
            s.pause_prompt = str(pause_prompt).strip() or None
        if resume_prompt is not None:
            s.resume_prompt = str(resume_prompt).strip() or None
        self._save(s)
        # pushed now, as `set_stop` is: another tab's Focus re-attaches read-only or with the keyboard
        # at the press, not on the next tick (§4.6, TD-096)
        await self._push_changes()
        return self._view(s)

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
        return self._view(s)

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
        return self._view(s)

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
        return self._view(s)

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
        `out_of_work: {at, why}` on the record. `status="restart"` is the third ending (§4.9a
        *A run that ends with work left*, TD-083): the same shape, setting
        `restart_wanted: {at, why, early?}` — *my run is over and my lane is not*. They are the
        two writes on this channel that are not open to everyone — only the session itself may
        make either, declared, with a reason (§9 invariant 14) — and they refuse each other.

        A declared claim is a **lease** (§4.8, TD-056): refused while another live record holds an
        unexpired declared claim on the same reference, naming the holder; `force` claims anyway and
        the reply carries `lease_overridden`."""
        s = self._find(id)  # a node's session reports here (step 5): the field is the home's
        if status in ("none", "restart"):
            if ref or pr is not None:
                raise RpcError(
                    f"progress {status} takes no reference and no PR, only "
                    f'why="{"the search that came up empty" if status == "none" else "why this run is over"}"'
                )
            return await self._ending(s, status, why, source, caller)
        if status not in PROGRESS_STATUSES:
            raise RpcError(
                f"unknown progress status {status!r}; statuses are: {', '.join(PROGRESS_STATUSES)}, none, restart"
            )
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
            # a session that claims something went on after all — both endings are taken back
            # (§4.9a; `restart_wanted` since TD-083), and a controller must not act on a stale one
            s.out_of_work = s.restart_wanted = None
        if applied and status == "dropped" and entry.source == "declared" and mail.is_person(caller):
            # §4.5a *Reports* → Drop, §4.10 (TD-150 slice 3): the session is told its lease went, by a
            # `system` note that wakes it as a person's act does — else it works on, holding nothing
            self._system_note(
                self._address(s),
                f"your claim on {entry.ref} was dropped by the person — the lease is gone; claim again if you "
                "still hold the work",
                wake="person",
            )
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

    async def _ending(self, s: Session, status: str, why: str | None, source: str, caller: Any) -> dict[str, Any]:
        """The two ways a run ends by the session's own word (design §4.9a): **`none`** — I
        searched and there is nothing I may pick — and **`restart`** — my run is over and my lane
        is not (TD-083). Both are facts about the session, not states, both are written only by
        the session they are about (§9 invariant 14), and **they refuse each other**: a session is
        out of work or it wants another run at it, never both.

        A **third** ending was missing until 2026-09-20 and its absence parked a team for ninety
        minutes: a grinder ended a long run on purpose with work still on the ledger, and could
        say so only in prose — it was not out of work, and a Claude Code `/exit` does not leave,
        so it was never `exited` and no rule of its manager's fired."""
        word = "out of work" if status == "none" else "a restart"
        if _source(source) != "declared":
            raise RpcError(f"{word} is declared, never derived (design §9 invariant 14)")
        if mail.is_person(caller) or str(caller) != s.id:
            said = "declare itself out of work" if status == "none" else "declare a restart of itself"
            raise RpcError(
                f"only {s.id} may {said}: it is the session's own word about its own run (design §9 invariant 14)"
            )
        if not (why or "").strip():
            reason = "the search that came up empty" if status == "none" else "why this run is over"
            raise RpcError(
                f'ao progress {status} needs --why "<{reason}>": a declaration without its '
                "reason is refused (design §4.9a)"
            )
        if owed := s.owed():
            # Design §4.10 *Outcomes*: the person answered; what became of it is owed before this
            # session stops. `dropped` is an honest way out, and one line settles each. A restart
            # owes them for a reason of its own: the fresh run does not carry the conversation the
            # debt was made in, so nobody is left who can report it.
            raise RpcError(
                f"{s.id} owes {len(owed)} outcome{'s' if len(owed) != 1 else ''} to the person: report each with "
                f'ao msg person --outcome done|blocked|dropped "<one line>" --for <id> before declaring '
                f"{word} — {', '.join(owed)} (design §4.9a, §4.10 *Outcomes*)",
                owed=owed,
            )
        if unread := s.unread():
            # Design §4.9a (TD-072, TD-141): winding down is the last moment anyone reads this inbox,
            # and what is unread at it is a `note` nobody triaged or an `ask` whose sender waits on a
            # run about to end. Reading is the triage — `ao inbox` marks mail read (§4.10) — and a
            # restart is refused alike: the next run holds nothing of this inbox it did not read.
            # After the owed check, so a session with both is told the one it clears first.
            raise RpcError(
                f"{s.id} has {unread} unread message{'s' if unread != 1 else ''}: read them with `ao inbox` and "
                f"answer what needs answering before declaring {word} (design §4.9a)",
                unread=unread,
            )
        other = "restart_wanted" if status == "none" else "out_of_work"
        if getattr(s, other):
            said = "wants a restart" if other == "restart_wanted" else "is out of work"
            raise RpcError(
                f"{s.id} already {said} (since {getattr(s, other)['at']}): a session is out of work or it wants "
                "another run at it, never both — claim something to take that back (design §4.9a)"
            )
        if status == "none":
            s.out_of_work = {"at": now_iso(), "why": why.strip()}
        else:
            # The word stands whenever it is said — it is the session's — but one said inside
            # `RESTART_EARLY` of this record's own start is marked, and a controller does not act
            # on it: a run that is over before it began did not run out of context. The bound is
            # applied here because the home holds the start time; the controller reads a field.
            mark = {"at": now_iso(), "why": why.strip()}
            with contextlib.suppress(ValueError, TypeError):
                if datetime.now(UTC) - _parse(s.created) < RESTART_EARLY:
                    mark["early"] = True
            s.restart_wanted = mark
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
        if s.doing and s.team:
            # the doing log (§4.8, TD-176 slice 2): the call, beside the record's latest line — what a
            # team card's Doing facet and the Repo page draw; nothing else reads it
            entry = {"team": s.team, "id": s.id, "text": s.doing["text"], "at": s.doing["at"]}
            self.doing_log.append(entry)
            await self._broadcast({"event": "doing", "team": s.team, "entry": entry})
        await self._push_changes()
        return self._view(s)

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
        out = self._view(s)
        if not applied:
            out["refused"] = entry.to_dict()
        return out

    # -- helpers ---------------------------------------------------------------------------------

    def _gate(self, caller: Any, method: str, params: dict[str, Any]) -> None:
        """`mail.act_gate` over this agent's records (design §4.8, §9 invariants 5 and 11): a
        function over a record map, so a node can forward and the home can answer (§4.4a)."""
        if method == "create" and params.get("capabilities"):
            _grants(params["capabilities"])  # an unknown grant name is refused before the gate reads it
        graph = self._graph()
        reason = mail.act_gate(graph, caller, method, params, controllers=self._ctl)
        if reason:
            raise RpcError(reason)
        if method == "send" and not mail.is_person(caller) and params.get("id") != caller:
            # design §6 *Usage gate*: while gated, a controller's send is refused here, at the home,
            # read from the record's `gated` — a node-owned field, so a replica carries it. A person's
            # own send is not (§9 invariant 11): the way past the gate is a person's.
            target = graph.get(self._addr(params.get("id", "")))
            if target is not None and (g := target.gated):
                raise RpcError(
                    f"{params.get('id')} is paused by the usage gate ({g.get('profile')} {g.get('label')} "
                    f"{g.get('pct')}% >= {g.get('line')}%): a controller's send waits for the resume (design §6)"
                )

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

    def _view(
        self, s: Session, *, bookkeeping: bool = False, graph: dict[str, Session] | None = None
    ) -> dict[str, Any]:
        """The view a client gets. This host's record is `s.view()`. Another host's carries its
        address as `id`, and while that host's link is down reads `unreachable` — an overlay on the
        view, never a state on the record."""
        v = s.view(bookkeeping=bookkeeping)
        # design §4.8a *An alarm's answers* (TD-077 b): **who answers for this record** — its
        # first live controller, read from the control graph and never from a badge (§9 invariant
        # 9). The page draws **Log TD** only where this is non-null and says *no session answers
        # for this one* where it is, which it can only do if it knows before it draws; and the
        # RPC reads the same function, so drawn-or-not and refused-or-not cannot disagree.
        v["alarm_to"] = self._answers_for(s, graph)
        # §4.10 *When it is read* (TD-168): the composer's sentence for each kind, as a person reads
        # it — computed here so the dialog opens with it and asks nothing; a `reply` reads as a note
        now = datetime.now(UTC)
        down = s.host != self.host and not (self.links.get(s.host) or {}).get("up")
        rings = getattr(adapters.get(s.adapter), "composer", None) is not None
        v["read_when"] = {k: mail.read_when(s, k, now, unreachable=down, rings=rings) for k in ("ask", "note")}
        if s.host == self.host:
            if self.mode == "node":
                v["asks_waiting"] = self._asks_hints.get(s.id, 0)  # the mailbox is the home's (§4.4a)
                v["prs_waiting"] = None  # the home's to count: a node holds no inbox (§4.4a)
            return v
        v["asks_waiting"] = s.asks_waiting(home=self.host)  # a bare `to` here names this host's session
        v["prs_waiting"] = s.prs_waiting(home=self.host)
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
        # one graph for the batch, not one per record: `_views` runs on nearly every RPC through
        # `_push_changes`, and `_answers_for` reading its own would make that quadratic in the
        # org's size (review of PR #318)
        graph = self._graph()
        return [
            self._view(s, graph=graph)
            for s in (*self.sessions.values(), *(r for h in self.remote.values() for r in h.values()))
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
                    for root, r in self._repos.items():  # the repo facts, as a change would push them
                        await self._send(writer, json.dumps({"event": "repos", "root": root, "repo": r}))
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
