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
import base64
import binascii
import contextlib
import json
import logging
import os
import secrets
import signal
import socket
import sys
import time
from collections import OrderedDict, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sessionorc import (
    adapters,
    agent_common,
    brief,
    build,
    containers,
    hosts,
    identity,
    link,
    mail,
    naming,
    paths,
    work,
)
from sessionorc import balance as balance_mod
from sessionorc import spend as spend_mod
from sessionorc import work as work_mod
from sessionorc.agent_attention import AttentionMixin
from sessionorc.agent_common import (  # re-exported: callers and tests read these from the agent
    _CSI,  # noqa: F401
    _ESC_OTHER,  # noqa: F401
    _LINE_BREAKS,  # noqa: F401
    _OSC,  # noqa: F401
    ACT_TIMEOUT,  # noqa: F401
    BACKUP_KEEP,  # noqa: F401
    BACKUP_MEMBERS,  # noqa: F401
    BRIEF_CLAUSE,  # noqa: F401
    BRIEF_SETTLE,  # noqa: F401
    CACHE_FLOOR,  # noqa: F401
    CACHE_LIFETIME,  # noqa: F401
    CLOSED_KEEP,  # noqa: F401
    COMPOSER_LINES,  # noqa: F401
    CONTEXT_AGAIN,  # noqa: F401
    CONTEXT_EVERY,  # noqa: F401
    CREATE_GRACE,  # noqa: F401
    DERIVE_EVERY,  # noqa: F401
    DOORBELL_TRIES,  # noqa: F401
    ENTRY_TYPES,  # noqa: F401
    FILE_CAP,  # noqa: F401
    FILES_MAX,  # noqa: F401
    FILL_CEILING,  # noqa: F401
    FILL_WINDOW,  # noqa: F401
    FINISHED_SETTLE,  # noqa: F401
    FIRST_PROMPT_BOUND,  # noqa: F401
    FIRST_PROMPT_HOOK_WAIT,  # noqa: F401
    FIRST_PROMPT_NO_HOOK,  # noqa: F401
    FIRST_PROMPT_TRIES,  # noqa: F401
    FLOW_CLAUSE,  # noqa: F401
    GIT_EVERY,  # noqa: F401
    HOME_EDITS,  # noqa: F401
    ID_RECHECK,  # noqa: F401
    IDLE_NUDGE,  # noqa: F401
    INTENT_FIELDS,  # noqa: F401
    LANE_NEWS_NAMED,  # noqa: F401
    LAUNCH_KEYS,  # noqa: F401
    LEASE_TTL,  # noqa: F401
    MODEL_EVERY,  # noqa: F401
    NODE_ACTS,  # noqa: F401
    NODE_READS,  # noqa: F401
    OWED_NAMED,  # noqa: F401
    PASTE_SHOW_SECONDS,  # noqa: F401
    PRUNE_EVERY,  # noqa: F401
    PUSH_OPEN,  # noqa: F401
    RELAUNCH_KEYS,
    REMOVED_GUARD_SECONDS,  # noqa: F401
    REPORT_EVERY,  # noqa: F401
    REPORT_WRITE,  # noqa: F401
    REPOS_EVERY,  # noqa: F401
    RESTART_CEILING,  # noqa: F401
    RESTART_EARLY,  # noqa: F401
    RESTART_SETTLE,  # noqa: F401
    RESTART_WINDOW,  # noqa: F401
    RESUME_MIN,  # noqa: F401
    ROUND_LINE_CAP,  # noqa: F401
    SEAT_IDLE_GRACE,  # noqa: F401
    SEAT_PR_WAIT,  # noqa: F401
    SEND_STALL_SECONDS,  # noqa: F401
    SETTLED,  # noqa: F401
    STALL_AFTER,  # noqa: F401
    SUBMIT_SECONDS,  # noqa: F401
    TAIL_LINES,  # noqa: F401
    TICK_SECONDS,  # noqa: F401
    TITLE_CAP,  # noqa: F401
    TRAIL_FLOOR,  # noqa: F401
    TRAIL_KEEP,  # noqa: F401
    TRANSCRIPT_TURNS_MAX,
    USAGE_COOL,  # noqa: F401
    USAGE_FRESH,  # noqa: F401
    USAGE_ONLY_EVERY,  # noqa: F401
    WORK_DAY,  # noqa: F401
    WORK_EARLY,  # noqa: F401
    WORK_SETTLE,  # noqa: F401
    WORK_STARTS_DAY,  # noqa: F401
    WRAPUP_GRACE,  # noqa: F401
    LeadTyped,
    RpcError,  # noqa: F401
    _alarm_report,  # noqa: F401
    _alarm_since,  # noqa: F401
    _alarm_words,  # noqa: F401
    _cap,  # noqa: F401
    _clean,  # noqa: F401
    _clean_answer,  # noqa: F401
    _context_bound,  # noqa: F401
    _controllers,  # noqa: F401
    _cool_left,  # noqa: F401
    _counted,  # noqa: F401
    _drop_unknown,  # noqa: F401
    _duration,  # noqa: F401
    _ended_by,  # noqa: F401
    _falsy,  # noqa: F401
    _grants,  # noqa: F401
    _is_branch_claim,  # noqa: F401
    _lane,  # noqa: F401
    _new_done,  # noqa: F401
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
    _reported,  # noqa: F401
    _restart_reading,  # noqa: F401
    _review,  # noqa: F401
    _slices,  # noqa: F401
    _source,  # noqa: F401
    _start_time,  # noqa: F401
    _stop_time,  # noqa: F401
    _urgent,  # noqa: F401
    _usage_checked_at,  # noqa: F401
    _usage_key,  # noqa: F401
    _Wait,  # noqa: F401
    backup_store,  # noqa: F401
    cache_lapsed,  # noqa: F401
    cache_restarts,  # noqa: F401
    closer_of,
    launch_params,  # noqa: F401
    log,  # noqa: F401
    person_only,  # noqa: F401
    read_checkout,  # noqa: F401
    stat_dir,  # noqa: F401
    tick_ready,  # noqa: F401
)
from sessionorc.agent_hook import HookMixin
from sessionorc.agent_identity import IdentityMixin
from sessionorc.agent_inbox import InboxMixin
from sessionorc.agent_link import LinkMixin
from sessionorc.agent_mail import MailMixin
from sessionorc.agent_notify import NotifyMixin
from sessionorc.agent_promote import PromoteMixin
from sessionorc.agent_remote import RemoteMixin
from sessionorc.agent_serve import ServeMixin
from sessionorc.agent_settings import SettingsMixin
from sessionorc.agent_spend import SpendMixin
from sessionorc.agent_tick import TickMixin
from sessionorc.agent_wake import WakeMixin
from sessionorc.gitinfo import WorktreeError, ensure_worktree, git_info, work_left
from sessionorc.mail import ACTING_RPCS  # noqa: F401 — re-exported: callers read it from the agent
from sessionorc.models import (
    GRANTS,
    PERSON,
    PROGRESS_STATUSES,
    WRAPUP_PROMPT,
    FindingEntry,
    MailEntry,
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
    HostStore,
    IdentityAlarmStore,
    PersonInboxStore,
    RepoStore,
    SessionStore,
    UsageStore,
)
from sessionorc.tmux import ARG_LIMIT, DuplicateSession, Tmux


def _write_attachment(where: Path, name: str, raw: bytes) -> Path:
    """`raw` as a new file in `where` (made `0700`), named `paths.attachment_name(name)`, or with
    `-2`, `-3`… before its extension where that is taken: never over an earlier attachment, whose
    path a sent prompt may still name. The file is `0600`."""
    where.mkdir(mode=0o700, parents=True, exist_ok=True)
    base = paths.attachment_name(name)
    stem, dot, ext = base.rpartition(".")
    if not stem:
        stem, dot, ext = base, "", ""
    for n in range(1, 1000):
        path = where / (base if n == 1 else f"{stem}-{n}{dot}{ext}")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        return path
    raise RpcError(f"attach: {base} is taken a thousand times over in {where}")


def _bound(path: Path) -> socket.socket:
    """A unix socket bound at `path` and **born `0600`** (TD-260): made and bound by hand under a
    umask of `0177`, with no `await` inside, and handed to `start_unix_server(sock=…)`. Binding
    through `path=` and then `chmod` left the file at the umask's mode for a moment — and a umask
    held across an awaited bind would leak into whatever else the loop creates meanwhile."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        s.bind(str(path))
    except BaseException:
        s.close()
        raise
    finally:
        os.umask(old)
    return s


class HostAgent(
    ServeMixin,
    TickMixin,
    PromoteMixin,
    SpendMixin,
    AttentionMixin,
    NotifyMixin,
    WakeMixin,
    HookMixin,
    SettingsMixin,
    InboxMixin,
    MailMixin,
    IdentityMixin,
    RemoteMixin,
    LinkMixin,
):
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
        self._id_scopes: dict[int, str] = {}  # pane pid → its own cgroup, or "" (TD-360)
        self._id_own_cg: str | None = None  # the host agent's own cgroup, never a pane's scope
        self._id_other_scopes: set[str] = set()  # own scopes of the server's panes no record holds (TD-362)
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
        # Told on Telegram (§4.10, TD-319): when each recent message went, for the burst's window, and
        # the last send's result for the `host` read — in memory: a restart forgets both, and `notified`
        # (on the attention store) is what keeps a standing row from being told twice
        self._notify_sent: list[datetime] = []
        self._notify_last: dict[str, Any] = {}
        self._notify_quiet_until: datetime | None = None  # after the burst's *and more*, nothing until then
        self._notify_watched_at: datetime | None = None  # the last person's read from a visible page
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
        # A ring's answer (§4.10 *A ring is answered by a read, or the bell stops*, TD-347): per session,
        # the unread count the last ring rang with and whether its turn has started (`{count, turned}`),
        # judged at that turn's `Stop`; and the run of rings so answered with nothing read.
        self._rang: dict[str, dict[str, Any]] = {}
        self._unread_rings: dict[str, int] = {}
        # a brief whose line went in and whose paste did not (TD-347): its next try pastes the brief alone
        self._lead_typed: set[str] = set()
        self._prompting: dict[str, asyncio.Task[None]] = {}  # a brief being typed (§4.1, TD-339), one per session
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
        self._context_checked: dict[str, datetime] = {}  # the context reading's cadence (TD-190)
        # When a `kill` or a `close` destroyed a pane, so a tick holding a pane list taken before
        # it does not observe a session that is already gone (TD-063). Dropped as soon as a
        # snapshot newer than the kill arrives, so it holds at most one tick's worth of ids.
        self._killed_at: dict[str, datetime] = {}
        self._derive_task: asyncio.Task[None] | None = None
        self._seat_count_task: asyncio.Task[None] | None = None  # §6 rule 3's `gh` read, detached
        self._seat_counted_at = datetime.min.replace(tzinfo=UTC)
        # rule 7's mark (TD-217 slice 3): the detached read of every brief's sources, when it last
        # ran, and per record the blob ids that first read otherwise and since when
        self._brief_task: asyncio.Task[None] | None = None
        # rule 10 (TD-258): the detached run of the cadence check, one PR a run, and when it last ran
        self._cadence_task: asyncio.Task[None] | None = None
        self._cadence_read_at = datetime.min.replace(tzinfo=UTC)
        # rule 12 (TD-258): the detached read of each root's docs/cadence-changes.md, and when it last ran
        self._conventions_task: asyncio.Task[None] | None = None
        self._conventions_read_at = datetime.min.replace(tzinfo=UTC)
        # rule 11 (TD-258): the detached read of merged PRs against `held:`, when it last ran, what
        # was settled (address, the record's `created`, PR) and when each PR was last tried
        self._held_task: asyncio.Task[None] | None = None
        self._held_read_at = datetime.min.replace(tzinfo=UTC)
        self._held_settled: set[tuple[str, str, int]] = set()
        self._held_tried: dict[tuple[str, int], float] = {}
        # §4.9 *What is left at the home has a history*: one committer, so one git at a time
        self._defs_lock = asyncio.Lock()
        self._defs_task: asyncio.Task[None] | None = None
        self._defs_read_at = datetime.min.replace(tzinfo=UTC)
        self._brief_read_at = datetime.min.replace(tzinfo=UTC)
        self._brief_differs: dict[str, tuple[tuple[str, ...], datetime]] = {}
        # §6 *A reading the gate can no longer trust* (TD-233 slice 4): the accounts told of a pause
        # on a projection while it stands, and the day each was last told its usage is unknown
        self._projection_noted: set[str] = set()
        self._unknown_noted: dict[str, str] = {}
        # when a hook last reported on a session: a screen-rule verdict never outranks a hook
        # state fresher than STALL_AFTER (design §4.2); a session no hook has reported on yet — the
        # trust dialog case — takes the classifier's verdict at once (TD-015)
        # Seeded on load: a hook-confirmed record was fed by a live hook stream until the agent
        # stopped, and `since` is a transition time, not a hook time — so count it fresh as of now.
        # A restart must never let the screen outrank a state a hook just reported.
        self._last_hook: dict[str, datetime] = {
            sid: datetime.now(UTC) for sid, s in self.sessions.items() if s.confidence == "hook"
        }
        # sid → the state, pending and confidence a screen rule's verdict replaced (TD-306): what
        # the record goes back to when that screen is gone and no hook has spoken since; dropped
        # when a hook sets the state. Not kept across a restart: the record then goes to `idle`.
        self._hook_state: dict[str, tuple[str, Any, str]] = {}
        # When a hook event last reached a record **live**, in epoch seconds (TD-169): a queued
        # event stamped before it is older than the state it would set, so its state is skipped.
        self._live_hook_at: dict[str, float] = {}
        # entries whose *Put on the board* is under way (TD-140): one press per entry at a time
        self._board_adding: set[str] = set()
        # profile → last usage dict from its adapter (`usage_for`), and when it was last asked
        # The last good reading per profile, kept across a restart (TD-087) — with, beside the
        # windows, why the *last poll* failed, which the page draws as a stale chip rather than
        # as nothing at all. `_usage_wait` is the cool-off a 429 sets and a success clears.
        # **The reading is the account's** (§4.2a, TD-122): `_usage_acct` holds one reading per
        # `(adapter, account)` key and the poll, its clock and its backoff are keyed on that;
        # `_usage` is the same reading copied under every live profile sharing the account, with
        # the account and the tool's display name beside it, which is what the gate, `limited`,
        # the `usage` RPC and the chip read.
        self.usage_store = UsageStore()
        self._usage: dict[str, dict[str, Any]] = self.usage_store.load()
        # the profiles restored above: a node's keeps its reading until the node's first report
        self._usage_restored: set[str] = set(self._usage)
        self._usage_acct: dict[str, dict[str, Any]] = {}
        # (node, profile) → the account key that node keys the profile by (§4.4 *A node's sessions
        # report to their node*, TD-233 slice 2): its credentials decide, not a profile of that name here
        self._usage_remote_keys: dict[tuple[str, str], str] = {}
        # (node, account key) → (the link, what it was last sent): the home's reading goes to a node
        # when it moves, and again on a new link
        self._usage_told: dict[tuple[str, str], tuple[Any, str]] = {}
        # …and the **allowance** survives with it (anchor's read of PR #307): a held reading is as
        # good as a poll made at its `fetched`, so the first poll after a promote waits until
        # `fetched + USAGE_FRESH`, never sooner. A reading with no readable time is polled at once.
        # Seeded per account on the first refresh, from its profiles' held readings.
        self._usage_checked: dict[str, float] = {}
        self._usage_wait: dict[str, float] = {}
        self._usage_only_at: dict[str, float] = {}  # when an endpoint-only window was last asked for (TD-233)
        self._usage_task: asyncio.Task[None] | None = None
        # A metered account's ledger (§4.4 *Usage*, TD-151): `spend.json`, the rows and the cursors
        # that make its reading a sum, written only when it changed. `_metered` is the profiles the
        # last pass found billed `metered` — what the gate reads amounts for — and `_metered_shown`
        # those whose reading is in `_usage`, so one no live session runs under leaves the top bar.
        self.spend_store = spend_mod.SpendStore()
        self._spend: dict[str, dict[str, Any]] = self.spend_store.load()
        self._spend_saved = json.dumps(self._spend, sort_keys=True)
        self._spend_pruned = ""
        self._spend_reason: dict[str, str] = {}
        self._metered: set[str] = set()
        self._billing_seen: dict[str, tuple[float, bool]] = {}  # profile → (monotonic, metered), for the gate
        self._metered_shown: set[str] = set()
        self._spend_task: asyncio.Task[None] | None = None
        # A node's side of the road (§4.4a, TD-151 slice 4): per profile, the cursors and seeding the
        # home answered on this link (cleared on each dial) and the last cursors it acknowledged (kept
        # for the offline figure); per account, the sums the home last sent. The home's side: the
        # accounts each node named in a `spend`, and what each was last told.
        self._spend_link: dict[str, dict[str, Any]] = {}
        self._spend_acked: dict[str, dict[str, int]] = {}
        self._spend_held: dict[str, dict[str, Any]] = {}
        self._spend_named: dict[str, dict[str, set[str]]] = {}
        self._spend_node_prices: dict[str, dict[str, float]] = {}  # profile → prices, as a node's `spend` declared
        self._spend_told: dict[tuple[str, str], str] = {}
        # The repo facts per registered checkout (design §4.4 *Repo facts*, TD-176), kept across a
        # restart in `repos.json`; the home's alone — a node reads none of this (§4.4a).
        self.repos_store = RepoStore()
        self._repos: dict[str, dict[str, Any]] = self.repos_store.load()
        self._repos_read_at = float("-inf")  # monotonic: the first tick reads
        self._ledger_mtime: dict[str, float | None] = {}
        self._repos_task: asyncio.Task[None] | None = None
        # The home's own `host` record (§6 *Balance*, TD-239): a team's balance mark, kept in `host.json`.
        self.host_store = HostStore()
        self._host_rec: dict[str, Any] = self.host_store.load()
        # rule 8 (§6, TD-227): (team, id) → when the home first read that id as new in a wound-down
        # team's lane; in memory, so a restart of the home starts the settle again
        self._work_first: dict[tuple[str, str], datetime] = {}
        self._question_ended = False  # rule 8: a lapse wrote a question's mark the sweep must push (TD-274)
        # rule 9 (§6, TD-241): team → when the home's reading of *finished* first held; in memory, and
        # dropped the tick it stops holding, so a restart of the home starts the settle again
        self._finished_first: dict[str, datetime] = {}
        # team → that reading's first tick, where no manager is live to carry `finished_sent_at` and a
        # member was left open with work: the note to the person is owed until the last one is gone
        self._finished_owed: dict[str, datetime] = {}
        # The promote's readings per repo (design §6 *Promote*, TD-132): in memory, re-read at start —
        # what must survive a restart (a run in flight, a failure) is in its intent files.
        self._promotes: dict[str, dict[str, Any]] = {}
        self._promote_read_at = float("-inf")  # monotonic: the first tick reads
        self._promote_watch_at = float("-inf")
        self._promote_bad: dict[str, str] = {}
        self._promote_task: asyncio.Task[None] | None = None
        self._promote_lock = asyncio.Lock()
        self._pulls: dict[str, dict[str, Any]] = {}  # the pull's readings by repo (design §6 *Pull*)
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
        server = await asyncio.start_unix_server(self._handle_conn, sock=_bound(sock), limit=link.FRAME_LIMIT)
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

            srv = await asyncio.start_unix_server(take, sock=_bound(lsock), limit=link.FRAME_LIMIT)
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

    # -- RPC methods -----------------------------------------------------------------------------

    async def rpc_list(self) -> list[dict[str, Any]]:
        return self._views()

    async def rpc_recent_dirs(self) -> list[str]:
        p = paths.recent_dirs_file()
        return p.read_text().splitlines() if p.is_file() else []

    async def rpc_repos(self) -> dict[str, dict[str, Any]]:
        """The repo facts per registered checkout (design §4.4 *Repo facts*, TD-176), keyed by the
        checkout's path: what the team card's Repo facet, the rollup and the Repo page draw, with
        `balance` beside a reading whose repo a team is over its line in (§6 *Balance*). The home's;
        a node forwards it (§4.4a)."""
        return {root: v for root in self._repos if (v := self._repo_view(root)) is not None}

    async def rpc_doing_log(self, team: str | None = None) -> dict[str, list[dict[str, Any]]]:
        """The doing log (design §4.8 *the doing log*, TD-176 slice 2): per team, its last fifty
        `ao doing` calls, oldest first — `{team, id, text, at}` each; one team's with `team`. The
        home's; a node forwards it (§4.4a). A read, and not the `doing` RPC, which writes the line."""
        rings = self.doing_log.rings
        if team is not None:
            return {team: list(rings.get(team, []))}
        return {t: list(r) for t, r in rings.items()}

    async def rpc_adapters(self) -> list[str]:
        return adapters.names()

    async def rpc_ping(self) -> str:
        return "pong"

    async def rpc_host(self) -> dict[str, Any]:
        """Who this host agent is in the org (design §4.4a): its host, its home, its mode, and
        whether the home can be reached — which a client on a node needs before it labels what it
        shows *offline*; which build it runs and since when (§4.4, TD-062); and, at the home, the
        promote's readings per repo (`promotes`, §6 *Promote*), the pull's (`pulls`, §6 *Pull*) and
        each wound-down team's `work_waiting` as the `host` record holds it (`work: {<team>: mark}`,
        §6 rule 8), which is what draws the Inbox's team start row and the card's note; each team's
        balance mark (`balance: {<team>: mark}`, §6 *Balance*), every one, a mark with no `repo` included,
        which no checkout's `repos` reading carries (TD-330); and what
        each live session waits on (`waiting: {<sender>: [{id, ref, bound}]}`, `work.waiting_of`
        over the person inbox, §4.9a *Waiting is read, never declared*, TD-274) — references and
        bounds, never the questions' text — which a session's `ao team status` cannot read for
        itself, since nobody reads the person inbox but a person."""
        out = {"host": self.host, "home": self.home, "mode": self.mode, "home_reachable": self.home_reachable()}
        out["built_from"], out["started_at"] = dict(self.build), self.started_at
        if self.mode == "home":
            out["links"] = {h: dict(v) for h, v in sorted(self.links.items())}
            out["promotes"] = self._promotes_view()
            out["pulls"] = self._pulls_view()
            out["waiting"] = work.waiting_of(self.person_inbox)
            out["notify"] = self._notify_view()
            out["work"] = {
                team: dict(rec["work_waiting"])
                for team, rec in sorted((self._host_rec.get("teams") or {}).items())
                if isinstance(rec, dict) and isinstance(rec.get("work_waiting"), dict)
            }
            out["balance"] = {
                team: dict(rec["balance"])
                for team, rec in sorted((self._host_rec.get("teams") or {}).items())
                if isinstance(rec, dict) and isinstance(rec.get("balance"), dict)
            }
        else:
            out["link"] = dict(self.home_link)
        return out

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
        prompt_from: dict[str, Any] | None = None,  # kept in the launch record only (§6 rule 7, TD-217)
        start_context: str | None = None,
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
        context_bound: int | None = None,
        start_at: str | None = None,
        start_of: str | None = None,
        held: bool = False,
        held_reason: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """`held` (design §4.9b *The anchor seat*, TD-386): a seat's record written alone, `closed`
        with no pane, for a team's start whose seat's checkout is not free — held by another session,
        dirty, or off its default branch, as the fill reads it (`checkout_held`, TD-395) — so §6 rule 3
        fills it once the checkout is free. `held_reason` is that reading, `{by, why}`, written as the
        record's `seat_held` so its card says why before the first tick. Refused without `seat` and
        `unattended`, and beside `start_at` or a resume; `held_reason` without `held`. A manager on
        call is written so by every Start (§6 rule 3 *A Start writes the seat and never fills it*,
        TD-410), with no `held_reason`; with `keep_mail` the held record takes the mail as a fill does.

        `start_at` (design §6 *Start time*, TD-152): an instant ahead; the create makes the
        **record** now — the name taken, the worktree made, the launch record written, the slot held
        — in the state `scheduled`, with no pane, and the home's tick creates the session at the
        instant. Refused without `unattended`, in the past, and with a `run_until` not after it.
        `start_of` is that start: the tick's replay names the scheduled record it starts, which is
        then superseded in place, its mail kept, rather than refused as the live holder it is.

        `start_context` (design §4.3, TD-283): text the session holds from its start that is no
        prompt, handed to the adapter's `launch` — refused for an adapter that cannot carry one,
        never folded into the prompt — and kept on the record and the launch record. A resume that
        gives none carries the conversation's own, so every launch of it holds the same text.

        `review` (design §4.9b *The reader*, TD-093): the role preset's `{reader, held, bound}`,
        checked and kept on the record, read afterwards by the author's own `ao`. `context_bound`
        (design §4.8 *A role has a context bound*, TD-190): the preset's `context: {bound}` in tokens,
        kept on the record the same way.

        `supervised` (design §6 *Keeping a team running*, TD-103 slice 1): kept on the record,
        and a resume of a supervised record stays supervised whether or not it says so — the field
        is cleared by nothing but Forget. With it, the create writes the session's launch record
        (`_write_launch`), here unless this is a node, whose home writes it when it routes the create.

        `keep_mail` (design §4.9b, TD-075 step 4): a fresh start under a name moves the mail of
        the record it supersedes — inbox, outbox, tallies, wake decisions — as a resume does, and
        resumes nothing of its conversation. It is how a techlead seat is filled without forgetting
        the questions that caused the fill. Handing a record's mailbox to a successor is an act on
        that record, so it is open to a person and to the record's own controllers alone: a team's
        start under a closed member's name asks it (§4.10 *The name coming back adopts it*, TD-274)."""
        if host and host != self.host:
            # Routed before the method runs (`_act_host`) when this is the home; a node asked for
            # another host's create got here through the link, and the link is one host's.
            raise RpcError(f"{host} is not this host ({self.host}): a create lands on the host it names")
        directory = Path(dir).expanduser().resolve()
        if not directory.is_dir():
            raise RpcError(f"not a directory: {directory}")
        grants, references = _grants(capabilities or []), _lane(lane or [])  # validate before anything starts
        reading, bound = _review(review), _context_bound(context_bound)
        starts = _start_time(start_at, unattended, run_until)
        if held_reason is not None and (
            not held
            or not isinstance(held_reason, dict)
            or not all(isinstance(held_reason.get(k), str) and held_reason[k].strip() for k in ("by", "why"))
        ):
            raise RpcError("held_reason is a held seat's {by, why}, both words, and only beside held (TD-395)")
        if held and (not (seat and unattended) or starts or resume):
            raise RpcError(
                "held writes a seat's record alone, closed, for rule 3 to fill: it takes a seat and unattended, "
                "and no start_at or resume (design §4.9b The anchor seat)"
            )
        # the scheduled record this create starts (the tick's replay, §6 *Start time*), if it is one
        starting = self.sessions.get(str(start_of)) if start_of else None
        if start_of and (starting is None or starting.state != "scheduled"):
            raise RpcError(f"{start_of} is not a scheduled record: nothing to start (design §6 Start time)")
        if starting is not None and not mail.is_person(caller) and self._addr(str(caller)) not in self._ctl(starting):
            # starting a scheduled record supersedes it and takes its mailbox: an act on that record,
            # open to a person and to its own controllers, as keep_mail's is (§4.9b, §9 invariant 11);
            # the tick's own start calls with no caller (techlead's read of #669)
            raise RpcError(
                f"{caller} is not a controller of {starting.id}: starting a scheduled record early is an act on it, "
                "open to a person and to its own controllers (design §6 Start time, §9 invariant 11)"
            )
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
        start_context = (str(start_context).strip() or None) if start_context else None
        if start_context:
            if not getattr(ad, "start_context", False):
                raise RpcError(
                    f"the {adapter} adapter cannot carry a start context: give the text as a prompt, or start "
                    "a tool that holds one (design §4.3, TD-283)"
                )
            # one argument of its own, counted as the prompt is (TD-068)
            if (size := len(start_context.encode("utf-8", "surrogateescape")) + 1) > ARG_LIMIT:
                raise RpcError(
                    f"the start context is {size - 1:,} bytes — past what a process can be started with "
                    f"({ARG_LIMIT:,}): put text that long in a file and tell the session to read it "
                    "(design §4.3, TD-068)"
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
            if kind == "interactive" and adapter != "shell" and not held:
                for who in await asyncio.to_thread(self.occupants, directory):
                    if starting is not None and who.split(" ", 1)[0] == starting.id:
                        continue  # the scheduled record holds the slot for exactly this start
                    raise RpcError(f"{directory} already has agent session {who}; anchor rule (use a worktree)")
            # Design §4.1 / §9 invariant 12: a name identifies one session per scope. An unnamed
            # session is named here, not by its caller, so two of them never collide (TD-030).
            if not name.strip():
                name = await asyncio.to_thread(self._auto_name, directory, repo, adapter)
            # Refuse here; *take* the name below, once the launch has succeeded. Taking it kills a
            # pane, and a launch that then failed would have killed it for nothing (review).
            base_of = naming.base_id(directory, repo, name)
            if starting is not None and (starting.dir != str(directory) or not starting.id.startswith(base_of)):
                # the start is of that record, under its name and in its directory — never a way to
                # supersede some other scheduled record from elsewhere (§6 *Start time*)
                raise RpcError(f"{start_of} is scheduled as {starting.name} in {starting.dir}: start_of must name it")
            holder = starting if starting is not None else await self._name_holder(directory, repo, name)
            if isinstance(holder, Session):
                self._refuse_suspended(holder, caller, "a create under that name")
            if keep_mail:
                self._check_keep_mail(holder, caller, resume)
            if resume:
                for gone in [r for r in self.sessions.values() if r.adapter_id == resume and r.suspended]:
                    self._refuse_suspended(gone, caller, "a resume of that conversation")
                    # a person got past that line, which **is** the lift (§4.8a) — and it lifts
                    # here rather than only in `_take_name`, because a resume under another name
                    # leaves this record standing, and a mark nothing can clear would hold its
                    # old name for ever (review of PR #301)
                    gone.suspended = None
                    self._save(gone)
                for who in await asyncio.to_thread(self.conversation_holders, resume):
                    raise RpcError(f"conversation {resume} is still live in {who}; kill it first, or Switch to it")
                if start_context is None:
                    # the conversation's own, whoever presses Resume (§4.3): the tool keeps it in no file
                    start_context = self._start_context_of(resume, holder)
            if starts:
                return await self._schedule(locals(), holder, directory, repo, name, starts)
            if held:
                if isinstance(holder, Session) and holder.state not in ("exited", "closed"):
                    # the name's record is running (or scheduled): the seat is there already, and a
                    # record written over it would take its name and kill its pane (review of TD-386)
                    raise RpcError(f"{holder.id} is {holder.state}: the seat is already there, nothing to write")
                return await self._schedule(locals(), holder, directory, repo, name, None, state="closed")
            try:
                spec = ad.launch(
                    profile=profile,
                    resume=resume,
                    prompt=prompt,
                    unattended=unattended,
                    cwd=directory,
                    name=name,
                    # the home's preface heads it, so every launch of such a tool has the file (§4.1, TD-347)
                    **(
                        {"start_context": mail.start_context_file_text(start_context)}
                        if getattr(ad, "start_context", False)
                        else {}
                    ),
                )
            except (KeyError, ValueError, OSError) as e:  # OSError: the start context's file (TD-339)
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
                # AGENT_NAME is the name the Org shows, for whatever in the session signs its work —
                # dev-cadence's commit hook writes it as a co-author (TD-185). Tool-neutral: every
                # adapter's session gets it, and a blank name was given its automatic one above.
                # CADENCE_ATTENTION_SCOPE tells dev-cadence's start hook whose board to nudge from —
                # its own for an unattended session, the machine's for one a person drives — so the
                # synced script never asks `ao status` (design §4.3, §8, TD-425).
                shown = name if sid == base else name + sid[len(base) :]  # a shown suffix, never a hidden one
                env = {
                    **spec.env,
                    "AGENTORC_SESSION": sid,
                    "AGENTORC_HOME": str(paths.home()),
                    "AGENT_NAME": shown,
                    "CADENCE_ATTENTION_SCOPE": "own" if unattended else "machine",
                }
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
                name=shown,
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
                context_bound=bound,
                start_context=start_context,
                first_prompt=spec.first_prompt or None,
            )
            if isinstance(holder, Session):
                # the record of this name it replaced, for the home, which holds the mail (§4.4a)
                kept = bool(keep_mail or starting is not None or (resume and holder.adapter_id == resume))
                s.supersedes = [{"id": holder.id, "mail": kept, "at": s.created}]
            self.sessions[sid] = s
            self.store.save(s)
            if isinstance(holder, Session) and holder.adapter_id != s.adapter_id:
                self._drop_context(holder.adapter_id, holder)  # replaced in place, never forgotten
            self._remember_dir(directory)
            if resume:
                await self._supersede(resume, sid, replaced=holder if isinstance(holder, Session) else None)
            elif (keep_mail or starting is not None) and isinstance(holder, Session):
                # the seat's mail, to the seat's next holder (§4.9b); a scheduled record's, to the
                # session it becomes (§6 *Start time*): mail sent to it before the instant is its own
                self._move_mail(holder, s)
                self.store.save(s)
            self._adopt_orphans(self._address(s))  # its name's orphaned questions are its own again (§4.10)
            if self.mode != "node":
                if prompt_from:  # what the prompt was made from, as this create's client read it (§6 rule 7)
                    s.brief = await asyncio.to_thread(brief.record, prompt_from, False)
                    self.store.save(s)
                if s.supervised:
                    self._write_launch(s.id, s, launch_params(locals()))
                else:
                    self._drop_launch(s.id)  # a fresh start that took a supervised record's id is not it
        return s.view()

    async def _schedule(
        self,
        given: dict[str, Any],
        holder: Session | str | None,
        directory: Path,
        repo: str | None,
        name: str,
        starts: str | None,
        state: str = "scheduled",
    ) -> dict[str, Any]:
        """A create with `start_at` (design §6 *Start time*, TD-152): the record now, in the state
        `scheduled` — the name taken under §4.1's rule, the worktree already made, the launch record
        written (the one the tick replays at the instant), the directory's slot held by being a
        record that is neither exited nor closed — and no pane, no run log, no conversation yet.
        Called from `create` under its locks, with `given` its own arguments as they stand. With
        `state="closed"` it is a `held` seat's record (§4.9b *The anchor seat*, TD-386): the same
        record, holding no slot, whose `lane_seen` is empty so everything in its lane is the first
        fill's."""
        if given.get("resume"):
            raise RpcError("start_at is a fresh start: a resume runs now or not at all (design §6 Start time)")
        previous_run, freed = await self._take_name(holder)
        live = await asyncio.to_thread(lambda: [p.session for p in self.tmux.list_panes()])
        taken = (set(self.sessions) | set(live)) - ({freed} if freed else set())
        base = naming.base_id(directory, repo, name)
        sid = naming.session_id(directory, repo, name, taken)
        team = str(given.get("team") or "")
        s = Session(
            id=sid,
            name=name if sid == base else name + sid[len(base) :],
            kind=given["kind"],
            adapter=given["adapter"],
            dir=str(directory),
            profile=given["profile"],
            repo=repo,
            worktree=given["worktree"],
            unattended=True,
            capabilities=given["grants"],
            controllers=given["members"],
            lane=given["references"],
            role=str(given.get("role") or ""),
            ledger=(str(given["ledger"]).strip() or None) if given.get("ledger") else None,
            team=team,
            project=str(given.get("project") or ""),
            run_until=self._team_stamp(team, True, _stop_time(given.get("run_until"))),
            wrapup_prompt=(str(given["wrapup_prompt"]).strip() or None) if given.get("wrapup_prompt") else None,
            pause_prompt=(str(given["pause_prompt"]).strip() or None) if given.get("pause_prompt") else None,
            resume_prompt=(str(given["resume_prompt"]).strip() or None) if given.get("resume_prompt") else None,
            previous_run=previous_run,
            host=self.host,
            supervised=True,  # it is started by the tick's replay, as any supervised record is restarted
            seat=dict(given["seat"]) if given.get("seat") else None,
            review=given["reading"],
            context_bound=given["bound"],
            start_context=given.get("start_context"),
            start_at=starts,
        )
        s.set_state(state, confidence="hook")  # type: ignore[arg-type]
        if state == "closed":
            s.closed_at = s.created
            s.closer = {"by": "start", "why": "held"}
            s.lane_seen = {"at": s.created, "ids": []}
            if given.get("held_reason"):
                s.seat_held = {"by": str(given["held_reason"]["by"]), "why": str(given["held_reason"]["why"])}
        # a held seat's record keeps the mail as its fill would (§6 rule 3, TD-410): a question to the
        # last run's manager is in its inbox, so the first tick reads it as due and fills the seat
        kept = state == "closed" and bool(given.get("keep_mail")) and isinstance(holder, Session)
        if isinstance(holder, Session):
            s.supersedes = [{"id": holder.id, "mail": kept, "at": s.created}]
        self.sessions[sid] = s
        if kept:
            self._move_mail(holder, s)
        self.store.save(s)
        self._remember_dir(directory)
        if self.mode != "node":
            self._write_launch(s.id, s, launch_params(given))
        if starts:
            log.info("%s scheduled to start at %s", sid, starts)
        else:
            log.info("%s written %s with no pane: rule 3 fills it", sid, state)
        return s.view()

    def _start_context_of(self, resume: str, holder: Session | str | None) -> str | None:
        """The start context a resume of conversation `resume` carries when its create gave none
        (design §4.3, TD-283): the record of this name's, when it holds that conversation, else the
        newest record that does — a Resume under another name is still the same conversation."""
        if isinstance(holder, Session) and holder.adapter_id == resume and holder.start_context:
            return holder.start_context
        held = [r for r in self.sessions.values() if r.adapter_id == resume and r.start_context]
        return max(held, key=lambda r: r.created).start_context if held else None

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
        self._id_pane_replaced(holder.id)  # the new run's first hook waits for a fresh list (TD-341)
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

    def pull_occupant(self, directory: Path) -> str | None:
        """The first session in `directory` not at rest, for the pull (design §6 *Pull* (2)): None
        when every one there is at rest or there is none; its name when one is mid-turn; `""` when
        one's state cannot be read, which the pull takes as mid-turn. Unlike `occupants` it counts
        shells (a foreground command may be `git` itself) and reads each record's state: `idle`,
        `exited` and `closed` are at rest, everything else may be a turn under way or about to
        resume. A session outside agentorc is at rest only when its tool's registry says `idle`."""
        directory = Path(directory).resolve()
        rest = ("idle", "exited", "closed")
        mine = list(self.sessions.values())  # a copy taken in one step: this runs in a thread
        ours = {s.adapter_id for s in mine if s.adapter_id}
        records = [s for s in mine if Path(s.dir).resolve() == directory]
        if self.mode == "home" and self.remote:
            for host in containers.container_nodes():
                if (self.links.get(host) or {}).get("up"):
                    records += [
                        s for s in list(self.remote.get(host, {}).values()) if Path(s.dir).resolve() == directory
                    ]
        for s in records:
            if s.state not in rest:
                return s.name
        for ext in adapters.external_sessions():
            if ext.tool_id and ext.tool_id in ours:
                continue
            if Path(ext.cwd).resolve() == directory and ext.status != "idle":
                return ext.name if ext.status in ("busy", "shell") else ""
        return None

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
        if s.state == "scheduled":
            # nothing runs, so a kill is its Cancel (design §6 *Start time*, TD-328): a record left
            # `exited` would read as a run that never happened, and no policy would ever start it
            return await self._cancel_start(s)
        await asyncio.to_thread(self.tmux.kill_session, id)
        s.set_state("exited", confidence="scraped")
        s.pane = False  # unlike a natural exit, a kill destroys the pane (TD-023)
        self._killed_at[id] = datetime.now(UTC)  # a tick's older pane list must not revive it (TD-063)
        self.store.save(s)
        await self._push_changes()  # the Focus terminal ends on this delta, not on a retry (TD-029)
        return s.view()

    async def rpc_close(self, id: str, caller: Any = None, closer: dict[str, Any] | None = None) -> dict[str, Any]:
        """End a session and keep its record (§4.5a **Close**). Writes `closer: {by, why, at}`
        (§4.5 row 5 (b), TD-265): `by` is `person` when the envelope carries no caller, else the
        caller's id. `closer` is the tick's own word (`{"by": "tick", "why": …}`) or the home's,
        handed on to a node over the link; it is taken only from a call with no session caller, so
        a session can never write *closed by the tick* or another's name."""
        s = self._get(id)
        if s.state == "scheduled":
            # **Cancel** (design §6 *Start time*, §4.5a): nothing ran, so there is nothing to keep —
            # the record and its launch record are forgotten and the directory's slot is free
            return await self._cancel_start(s)
        await asyncio.to_thread(self.tmux.kill_session, id)
        s.set_state("closed", confidence="scraped")
        s.pane = False
        s.closed_at = now_iso()
        s.closer = closer_of(caller, closer, s.closed_at)
        s.closed_for = None  # a Close is nobody's restart; the tick writes its own after this returns (§6 rule 2)
        # A `kill` then a `close` before an intervening tick would otherwise strand a `_killed_at`
        # stamp for `CLOSED_KEEP`: the reconcile skips a closed record before it reaches the guard,
        # so nothing else would ever clear it. Harmless — a closed record is never observed either
        # way — but it would make the guard's one-tick bound untrue (review of PR #199).
        self._killed_at.pop(id, None)
        self.store.save(s)
        self._asker_gone(s, self._address(s))  # its open questions to the person close with it (§4.10)
        await self._push_changes()  # the Focus terminal ends on this delta, not on a retry (TD-029)
        return s.view()

    async def rpc_restart(self, id: str, caller: Any = None) -> dict[str, Any]:
        """A person's restart (design §6 rule 2 *A person's restart*, TD-250): what the tick would not
        do — an `early` or a `repeat` declaration, a member at its ceiling, one a person closed —
        said in one press and then done the tick's way. The record is closed if it is still there,
        under the tick's own test (`gitinfo.work_left`), and created again from its launch record,
        the prompt refilled as rule 7's replay refills it. The new record starts fresh: no marks,
        and `restarts` the one entry `{at, why: person}`, which never counts toward the ceiling.
        **A person's own**, refused to a session as `set_settings` is, and the home's alone
        (`modes.HOME_EDITS`); a node's member is closed and created over the link. Every refusal is
        made before anything is touched; the reply is the new record."""
        agent_common.person_only(caller, "restart a session", "§6 rule 2")
        if self.mode != "home":
            raise RpcError("restart runs at the home (design §6 rule 2): this host is a node")
        s = self._find(id)
        now = datetime.now(UTC)
        params = self._restart_check(s, now)
        entry: dict[str, Any] = {"at": now_iso(), "why": "person"}
        read = await self._refill_prompt(s, params, entry)  # before the close: a read that raises touches nothing
        if s.state == "idle":
            if s.host == self.host:
                await self.rpc_close(s.id)
            else:
                await self._route_act("close", {"id": s.id}, None, s.host)
        try:
            if s.host == self.host:
                view = await self.rpc_create(**params)
            else:
                view = await self._route_act("create", {**params, "host": s.host}, None, s.host)
        except Exception as e:  # noqa: BLE001 — said to the person who pressed, whatever it was
            log.warning("%s: the person's restart failed: %s", s.id, e)
            raise RpcError(
                f"the restart of {s.name} failed: {str(e) or type(e).__name__} — it is {s.state} and its "
                "marks stand; Restart again, or Resume with changes… (design §6 rule 2)"
            ) from None
        rid, _h = naming.split_address(str((view or {}).get("id") or ""))
        new = self.sessions.get(rid) if s.host == self.host else self.remote.get(s.host, {}).get(rid)
        if new is None or new is s:
            return view  # a node's record the home has not been told of yet: the create's own reply
        new.restarts = [entry]
        new.restart_wanted = new.restart_ceiling = new.restart_blocked = None
        new.brief = read  # the files as this restart read them (as `_replay` writes it)
        self._save(new)
        await self._push_changes()
        log.info("%s restarted by the person from its launch record", self._address(new))
        return self._view(new)

    async def rpc_relaunch(
        self, id: str, launch: dict[str, Any] | None = None, sit_out: bool = False, caller: Any = None
    ) -> dict[str, Any]:
        """A person's Apply, one member (design §4.9c *Switching*, TD-309 slice 5): the client composed
        the member under the team's current flow and hands its launch here — `prompt`, `prompt_from`,
        `lane`, `review` (`RELAUNCH_KEYS`), each replaced as handed and an absent one removed, the rest
        of the launch record standing. The home writes it as the record's launch record, re-records
        `brief` from the new `prompt_from` as a create does, clears `brief_changed`, and marks the record
        `relaunch: {at, lane, review}` (the two as handed, so a client reads what the member runs next),
        which rule 7 reads as its second trigger (`why: flow`): every replay reads the new record, and a
        create under the name clears the mark. **A person's own**, refused to a session as
        `set_settings` is, and the home's alone (`modes.HOME_EDITS`); never an interactive session, and
        never one with no launch record. Nothing is touched before every check passes;
        the reply is the record.

        **`sit_out: true`** is its other form (slice 5b): a member the flow no longer uses is wound
        down as **Members…**'s remove winds one down — the wrap-up sent now, a working member finishing
        what it holds — but by the home, which writes `sit_out: {at}` and, once the member is settled,
        closes it itself and marks it `closed_for: {why: sit_out}` (the tick's `_sit_out_close`). It
        takes no launch; never a seat (rule 3 fills one when it is due) or a scheduled record."""
        agent_common.person_only(caller, "relaunch a session", "§4.9c")
        if self.mode != "home":
            raise RpcError("relaunch runs at the home (design §4.9c): this host is a node")
        s = self._find(id)
        rule = "(design §4.9c *Switching*)"
        if sit_out:
            return await self._sit_out(s, launch, rule)
        if not isinstance(launch, dict):
            raise RpcError(f"relaunch needs the member's composed launch {rule}")
        stray = sorted(k for k in launch if k not in RELAUNCH_KEYS)
        if stray:
            raise RpcError(f"a relaunch replaces {', '.join(RELAUNCH_KEYS)} alone, not {', '.join(stray)} {rule}")
        kinds = {"prompt": str, "prompt_from": dict, "lane": list, "review": dict}
        for k, v in launch.items():
            if v is not None and not isinstance(v, kinds[k]):
                raise RpcError(f"a relaunch's {k} is a {kinds[k].__name__}, not {type(v).__name__} {rule}")
        if not s.unattended:
            raise RpcError(f"{s.name} is interactive: its lane and brief are the person's, never relaunched {rule}")
        if s.superseded_by:
            raise RpcError(f"{s.name} was resumed as {s.superseded_by}: that is the record to relaunch {rule}")
        if s.sit_out or work_mod.sat_out(s):
            raise RpcError(f"{s.name} is sat out by its team's flow: a create under the name starts it again {rule}")
        address = self._address(s)
        try:
            if not s.supervised:
                raise RpcError("not supervised")
            params = self._read_launch(address)
        except RpcError:
            raise RpcError(f"{s.name} has no launch record, so there is nothing to relaunch {rule}") from None
        for k in RELAUNCH_KEYS:
            params.pop(k, None)
        params.update({k: v for k, v in launch.items() if v is not None})
        made = params.get("prompt_from")
        # a node member's files are that host's, which rule 7 never reads here (as the replay)
        read = await asyncio.to_thread(brief.record, made, False) if made and s.host == self.host else None
        self._write_launch(address, s, params)
        s.brief, s.brief_changed = read, None
        self._brief_differs.pop(s.id, None)
        s.relaunch = {"at": now_iso(), "lane": launch.get("lane"), "review": launch.get("review")}
        self._save(s)
        await self._push_changes()
        log.info("%s relaunched by the person: its launch record replaced", address)
        return self._view(s)

    async def _sit_out(self, s: Session, launch: Any, rule: str) -> dict[str, Any]:
        """`relaunch`'s sit-out form: refused, touching nothing, on a launch handed with it, an interactive
        record, a superseded one, a seat or a scheduled one, and when the wrap-up cannot be sent (a
        pending prompt, a link down); said again of a record already sitting out, it does nothing more."""
        if launch is not None:
            raise RpcError(f"a sit-out takes no launch: the member stops, it is not composed again {rule}")
        if not s.unattended:
            raise RpcError(f"{s.name} is interactive: a team act never stops a person's session {rule}")
        if s.superseded_by:
            raise RpcError(f"{s.name} was resumed as {s.superseded_by}: that is the record to sit out {rule}")
        if s.seat is not None:
            raise RpcError(f"{s.name} is a seat: rule 3 fills it when it is due, and a flow never sits one out {rule}")
        if s.state == "scheduled":
            raise RpcError(f"{s.name} is scheduled and has not started: Cancel it instead {rule}")
        if s.sit_out or work_mod.sat_out(s):
            return self._view(s)
        if s.state not in ("exited", "closed"):
            try:
                if s.host == self.host:
                    await self.rpc_send(s.id, WRAPUP_PROMPT, wrapup=True)
                else:
                    await self._route_act("send", {"id": s.id, "text": WRAPUP_PROMPT, "wrapup": True}, None, s.host)
            except Exception as e:  # noqa: BLE001 — said to the person who pressed, nothing marked
                raise RpcError(
                    f"{s.name}: the wrap-up could not be sent ({str(e) or type(e).__name__}), "
                    f"so it is not sat out {rule}"
                ) from None
        s.sit_out = {"at": now_iso()}
        self._save(s)
        await self._push_changes()
        log.info("%s sat out by the person's Apply: the wrap-up sent", self._address(s))
        return self._view(s)

    def _restart_check(self, s: Session, now: datetime) -> dict[str, Any]:
        """`restart`'s refusals, each by name (design §6 rule 2 *A person's restart*), and the launch
        record as `create`'s arguments when none applies. Nothing is changed here."""
        rule = "(design §6 rule 2)"
        if s.seat is not None:
            raise RpcError(f"{s.name} is a seat: rule 3 fills it when it is due, and Resume is the way to it {rule}")
        if s.suspended:
            raise RpcError(
                f"{s.name} is suspended over an identity alarm: only a person's Resume or Forget lifts that, "
                f"and a restart is neither (design §4.8a)"
            )
        if s.superseded_by:
            raise RpcError(f"{s.name} was resumed as {s.superseded_by}: that is the record to restart {rule}")
        if s.state == "scheduled":
            raise RpcError(f"{s.name} is scheduled and has not started: `ao at {s.id} now` starts it {rule}")
        if s.state not in ("idle", "exited", "closed"):
            raise RpcError(
                f"{s.name} is {s.state}: a restart is of a session that is idle, exited or closed — wait, or "
                f"Wrap up {rule}"
            )
        try:
            if not s.supervised:
                raise RpcError("not supervised")
            launch = self._read_launch(self._address(s))
        except RpcError:
            raise RpcError(
                f"{s.name} has no launch record, so nothing says how it was started: Resume with changes… "
                f"is the way back {rule}"
            ) from None
        scope = s.repo or s.dir
        for r in self._graph().values():
            live = r.state not in ("exited", "closed") and not r.superseded_by
            if r is not s and live and r.host == s.host and r.name == s.name and (r.repo or r.dir) == scope:
                raise RpcError(f"{s.name} is the name of {self._address(r)}, which is {r.state}: one of a name {rule}")
        if s.host == self.host:
            # the create finds what it supersedes at the name's own id: a record under a suffixed id
            # (tmux held the base when it started) is not there, and the create would refuse after the close
            base = naming.base_id(s.dir, s.repo, str(launch.get("name") or ""))
            if self.sessions.get(base) is not s:
                raise RpcError(
                    f"{s.name} ({s.id}) does not hold its launch record's name, {launch.get('name')!r} ({base}): "
                    f"Resume with changes… is the way back {rule}"
                )
        until = launch.get("run_until")
        try:
            passed = bool(until) and now >= _parse(str(until))
        except ValueError:
            passed = False
        if passed:
            raise RpcError(f"{s.name}: its stop time has passed — Resume with changes… {rule}")
        profile, team = str(launch.get("profile") or ""), str(launch.get("team") or "")
        if self._profile_gated(profile, now, team):
            raise RpcError(
                f"{s.name}: its profile {profile or '(default)'} is over its usage line, and the gate would pause "
                f"what the press started — `ao gate` prints the lines {rule}"
            )
        if s.host != self.host and s.host not in self._link_muxes:
            raise RpcError(f"{s.name} runs on {s.host}, whose link is down: a restart waits for it (design §4.4a)")
        if left := work_left(s.git):
            raise RpcError(
                f"{s.name} has {left}: a restart closes a session only with its work committed and pushed {rule}"
            )
        # `keep_mail`: the new record is a new record, so its inbox and outbox move as a seat's fill moves
        # them. Its own open questions to the person end with the close of an idle one, as at any close.
        return {**launch, "supervised": True, "keep_mail": True}

    async def rpc_remove(self, id: str, caller: Any = None) -> None:
        s = self._get(id)
        if s.state == "scheduled":
            await self._cancel_start(s)  # §6 *Start time*: Forget of a scheduled record is its Cancel
            return
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
            state_sent, rev_sent = await self._submit(id, adapters.get(s.adapter), text)
        except RpcError as e:
            entry.verdict = str(e)
            self.store.save(s)
            raise
        if not wait:
            return None
        end = None if timeout is None else time.monotonic() + timeout

        def left() -> float | None:
            return None if end is None else max(0.0, end - time.monotonic())

        # The baseline is what the session was doing when the text was typed, read before the paste
        # (TD-204): the tool's UserPromptSubmit lands the moment Enter does, often while `_type` is
        # still confirming the composer emptied, and a baseline read after that took this prompt's
        # own turn for a busy session's and then waited for a further one that never came. Accepted
        # residual: an earlier typist's (the doorbell's, a wrap-up's) start that lands late, inside
        # this paste, reads as this prompt's; nothing ties a hook to the prompt that caused it.
        rev_before = rev_sent
        if state_sent != "idle":
            # Busy: the tool queues the text. Wait for the current turn to end; a stop on anything
            # but idle (a question, an exit) is returned as is — the prompt is still queued behind it.
            if not await self._wait_state(s, lambda x: x.state in SETTLED, left()):
                self._raise_not_settled(s, timeout)
            if s.state != "idle":
                return s.view()
            if s.rev - rev_sent >= 3:
                # working → idle → working → idle inside one poll: the queued turn already ran.
                # Accepted residual: a permission answered *and* the rest of that same turn finishing
                # inside one 0.1 s poll would look the same; a turn does not end that fast.
                return s.view()
            rev_before = s.rev
        # Started: any transition since the baseline (a hook's UserPromptSubmit → working, a scraped
        # working, even an exit) within the stall window.
        stall = agent_common.SEND_STALL_SECONDS if left() is None else min(agent_common.SEND_STALL_SECONDS, left())
        if not await self._wait_state(s, lambda x: x.rev != rev_before, stall):
            if self.sessions.get(id) is not s:
                raise RpcError(f"removed: {id} went away while waiting")
            raise RpcError(f"prompt-stalled: {id} showed no activity within {stall:g} s")
        if not await self._wait_state(s, lambda x: x.state in SETTLED and x.rev != rev_before, left()):
            self._raise_not_settled(s, timeout)
        return s.view()

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

    async def _submit(self, sid: str, adapter: Any, text: str) -> tuple[str, int]:
        """`_type`, holding the session's typing lock: one typist per pane (TD-094). Returns the
        record's state and `rev` as they were under the lock just before the paste — the baseline
        `send --wait` measures *started* from (TD-204)."""
        async with self._typing[sid]:
            s = self._get(sid)
            before = (s.state, s.rev)
            await self._type(sid, adapter, text)
        return before

    async def _type(self, sid: str, adapter: Any, text: str, lead: str | None = None, lead_in: bool = False) -> None:
        """Paste, Enter, and confirm the prompt left the composer (TD-027, design §4.2). Only an
        adapter that can read its tool's composer (`composer(tail_raw)`, design §4.3) gets the
        confirmation; the rest get the blind paste + Enter. The paste is given a moment to paint
        (Enter sent while the tool is still taking the paste is swallowed — measured 2026-09-10:
        a paste landing within ~0.1 s of the previous submit lost its Enter every time), then the
        composer must empty within `SUBMIT_SECONDS`; one retry with `C-m`, then `prompt-stuck`.
        Only the Enter is ever re-sent, and only with the text visibly still in the composer —
        never the text (design §4.2). A composer that cannot be read (no composer row, or a failed
        capture — `capture_tail` returns [] then) counts as emptied: no evidence is not evidence of a
        stuck prompt.

        `lead` is a line of the home's typed as literal keys before the paste, in the same prompt —
        the brief's line (§4.1 *The brief is the person's word*, TD-347). A paste refused while that
        line is in the composer — typed now, or by an earlier try (`lead_in`) — raises `LeadTyped`."""
        reader = getattr(adapter, "composer", None)
        if lead:
            await asyncio.to_thread(self.tmux.send_literal, sid, lead + " ")
        try:
            await asyncio.to_thread(self.tmux.paste, sid, text)
        except Exception as e:
            if lead or lead_in:
                raise LeadTyped(f"the paste was refused after the line went in: {e}") from e
            raise
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

    def _raise_not_settled(self, s: Session, timeout: float | None) -> None:
        if self.sessions.get(s.id) is not s:
            raise RpcError(f"removed: {s.id} went away while waiting")
        raise RpcError(f"timeout: {s.id} is still {s.state} after {timeout:g} s")

    async def _wait_state(self, s: Session, pred: Any, timeout: float | None) -> bool:
        """Poll the record on the loop until `pred(s)` holds; False on timeout (None: no limit) or
        when the record is gone. Gone is *this* record no longer being the one under its id, not
        the id being empty (TD-261): a record forgotten and its id taken again inside one poll — a
        live pane the tick adopts, a new session under the name — is another session, whose turns
        say nothing about the prompt typed into this one, and a wait with no timeout read it for
        ever."""
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            if self.sessions.get(s.id) is not s:
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

    async def rpc_attach(self, id: str, name: str = "", data: str = "", caller: Any = None) -> dict[str, Any]:
        """The Focus composer's **Attach** / drop / paste (design §4.4 *Attachment drop*, §4.5a,
        TD-002): the person's file, base64 in `data`, written under `attachments/<session>/` by its
        name made safe for a prompt (`paths.attachment_name`, `-2`, `-3`… where it is taken), and its
        path returned for the composer to insert — Claude Code reads a path in a prompt. A person's
        own act. A session on this host only: `_get` refuses a node's, whose copy over ssh is §7's
        phase 2. Refused past `paths.ATTACH_BYTES_MAX` or when `data` is not base64."""
        agent_common.person_only(caller, "attach a file", "§4.5a")
        s = self._get(id)
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise RpcError("attach: the file did not arrive as base64") from None
        if len(raw) > paths.ATTACH_BYTES_MAX:
            raise RpcError(
                f"attach: {len(raw)} bytes is past the {paths.ATTACH_BYTES_MAX // (1024 * 1024)} MiB a file may be"
            )
        path = await asyncio.to_thread(_write_attachment, paths.attachments_dir() / s.id, name, raw)
        return {"path": str(path), "bytes": len(raw)}

    async def rpc_tail(self, id: str, lines: int = 40) -> list[str]:
        self._get(id)
        return await asyncio.to_thread(self.tmux.capture_tail, id, lines)

    async def rpc_transcript(
        self,
        id: str = "",
        before: int | None = None,
        turns: int = 20,
        raw: bool = False,
        adapter: str = "",
        adapter_id: str = "",
        dir: str = "",
        profile: str = "",
    ) -> dict[str, Any]:
        """`ao transcript` and the Transcript page's read (design §4.5 screen 9, §4.7, TD-165): the
        record's tool transcript as the adapter's neutral entries — the last `turns` prompts before
        byte `before`, with the offset that asks for earlier ones — or, with `raw`, the file's last
        `turns` lines. Located from the record's own `adapter_id`, `dir`, `adapter` and `profile`, so
        an exited or superseded record reads the run it held; a Resumable row with no record passes
        those four instead of `id`. Served here, on the record's host — a node's record reaches its
        node through `read` (`NODE_READS`) — and never copied. A read: never gated (§9 invariant 11)."""
        if id:
            s = self._get(id)
            adapter, adapter_id, dir, profile = s.adapter, s.adapter_id or "", s.dir, s.profile
            if not adapter_id:
                why = "a shell has no transcript" if adapter in ("shell", "command") else "its hook never reported one"
                raise RpcError(f"{id} carries no tool session id: {why} (design §4.5 screen 9)")
        elif not (adapter and adapter_id and dir):
            raise RpcError("transcript needs id, or adapter, adapter_id and dir (design §4.7)")
        try:
            fn = getattr(adapters.get(adapter), "read_transcript", None)
        except KeyError:
            raise RpcError(f"no adapter {adapter}") from None
        if fn is None:
            raise RpcError(f"the {adapter} adapter reads no transcript (design §4.3)")
        if before is not None and (isinstance(before, bool) or not isinstance(before, int) or before < 0):
            raise RpcError(f"before is a byte offset, not {before!r}")
        turns = max(1, min(int(turns or 20), TRANSCRIPT_TURNS_MAX))
        t = await asyncio.to_thread(fn, adapter_id, Path(dir), profile, before=before, turns=turns, raw=bool(raw))
        if t is None:
            raise RpcError(f"no transcript for {id or adapter_id}: the tool's file is not on {self.host}")
        return t.to_dict()

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
        if s.state == "scheduled" and not unattended:
            # the tick would start it from its launch record, unattended: the switch silently undone
            # and a policy's act on a session the person made interactive (§6 *Start time*, TD-328)
            raise RpcError(
                f"{s.name} has not started: a scheduled start is unattended — `ao at` moves it, Cancel "
                f"forgets it; switch it to interactive once it runs (design §6 Start time, §9 invariant 5)"
            )
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

    async def _cancel_start(self, s: Session) -> dict[str, Any]:
        """A scheduled record cancelled (design §6 *Start time*): forgotten with its launch record, and
        its open questions closed with it, as a close does."""
        view = {**s.view(), "state": "closed", "cancelled": True}
        self._asker_gone(s, self._address(s), how="cancelled")
        self._forget(s.id)
        log.info("%s: its scheduled start cancelled", s.id)
        await self._push_changes()
        return view

    async def rpc_set_start(self, id: str, start_at: str = "", caller: Any = None) -> dict[str, Any]:
        """`ao at <session> <when> | now` (design §6 *Start time*, §4.7, TD-152): move a scheduled
        start, or — with `now` — hand it to the tick's next pass. Acting, and gated as `set_stop` is:
        it starts another session's run. Refused on a record that is not `scheduled`: a live session
        already started."""
        s = self._find(id)
        if s.state != "scheduled":
            raise RpcError(f"{id} is {s.state}, not scheduled: a start time is for a record that has not started")
        if mail.is_person(caller):
            # a person's new time is a new start: a ceiling the failed starts reached, and the count
            # that reached it, are spent — as a person's own Resume starts rule 1's count again (§6)
            s.restart_ceiling = None
            s.restarts = [r for r in s.restarts if not (isinstance(r, dict) and r.get("why") == "start")]
        elif s.restart_ceiling:
            # at the ceiling the record is a person's (§6): a controller moving the time would retry
            # without bound, so a session is refused here and the count is left as it is
            raise RpcError(
                f"{id} reached the restart ceiling ({s.restart_ceiling.get('count')} failed starts): it is a "
                "person's now — a person's ao at spends the ceiling (design §6 Start time)"
            )
        if str(start_at).strip().lower() == "now":
            s.start_at = now_iso()
        else:
            s.start_at = _start_time(start_at, True, s.run_until)
            if not s.start_at:
                raise RpcError("set_start needs a time, or now (design §6 Start time)")
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
        slice: bool = False,
        caller: Any = None,
    ) -> dict[str, Any]:
        """`ao progress claim|done|drop <ref>` (design §4.8): what this session set out to resolve
        and how it went. A report channel is **ungated** — any session may write any record's, the
        Org renders whichever are non-empty — and one reference is one entry, upserted in place.

        `status="none"` is `ao progress none --why` (design §4.9a): no reference and no entry, but
        `out_of_work: {at, why}` on the record. `status="restart"` is the third ending (§4.9a
        *A run that ends with work left*, TD-083): the same shape, setting
        `restart_wanted: {at, why, early?, repeat?, decided?}` — *my run is over and my lane is not*. They are the
        two writes on this channel that are not open to everyone — only the session itself may
        make either, declared, with a reason (§9 invariant 14) — and they refuse each other.

        A declared claim is a **lease** (§4.8, TD-056): refused while another live record holds an
        unexpired declared claim on the same reference, naming the holder; `force` claims anyway and
        the reply carries `lease_overridden`.

        `slice` with `status="done"` is `ao progress done <ref> --pr <n> --slice` (§4.8, §4.9a *A
        slice is work done*, TD-325): the PR is held in the declared claim's `slices` and the claim
        stays `claimed`, its status, `pr` and `why` as they were."""
        s = self._find(id)  # a node's session reports here (step 5): the field is the home's
        if slice:
            return await self._slice(s, ref, status, pr, source)
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
        new_claim = status == "claimed" and entry.source == "declared"
        if new_claim and (words := self._balance_refusal(s, entry.ref, caller)):
            raise RpcError(words, balance=self._balance_of(s))
        holder = self._lease_holder(s, entry) if new_claim else None
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
            s.lane_seen = None  # rule 6's memory goes with the declaration it was about (TD-195)
            if s.balance_refused and not self._balance_of(s):
                s.balance_refused = None  # the line cleared and the member claimed: nothing left to ring
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

    async def _slice(self, s: Session, ref: str, status: str, pr: int | None, source: str) -> dict[str, Any]:
        """A slice declared (TD-325): refused but for `done` with a PR, declared, on a reference the
        record holds a declared claim on — a slice is a PR of an entry still held."""
        ref, number = _ref(ref), _pr(pr)
        if status != "done" or _source(source) != "declared":
            raise RpcError("--slice goes with `ao progress done <ref> --pr <n>`, declared by the session itself")
        if number is None:
            raise RpcError(f"a slice is a merged PR: ao progress done {ref} --pr <n> --slice")
        claim = next((e for e in s.progress if e.ref == ref and e.source == "declared" and e.status == "claimed"), None)
        if claim is None:
            raise RpcError(
                f"{ref} holds no claim of yours: a slice is a PR of an entry you still hold — "
                f"ao progress claim {ref} first, or report the entry finished with a plain done"
            )
        out = await self._report(s, claim.add_slice(number, "declared"), claim)
        out.pop("refused", None)  # held already is no refusal: the slice is on the claim either way
        out["slice"] = f"#{number} held as a slice of {ref}; {ref} stays claimed — the last slice is a plain done"
        return out

    def _balance_of(self, s: Session) -> dict[str, Any] | None:
        """The balance mark of `s`'s team if `s` is one it refuses (design §6 *Balance*, TD-239): an
        unattended record that is no seat, on a team the home's `host` record marks — read there by
        team, never from a repo's reading, since a `review` crossing may name no repo. A node's member is
        checked here too: `progress` is a report, which its node forwards to the home (`modes.REPORTS`);
        only a node cut off from the home, which refuses reports outright, checks nothing (§6)."""
        if not s.team or not s.unattended or s.seat is not None:
            return None
        mark = ((self._host_rec.get("teams") or {}).get(s.team) or {}).get("balance")
        return mark if isinstance(mark, dict) and mark.get("crossed") else None

    def _balance_refusal(self, s: Session, ref: str | None, caller: Any = None) -> str | None:
        """The words refusing `s` a new claim on `ref` — or, with `ref` None, its `none` — while its
        team is over its line; None when it passes. A renewal (a claim `s` already holds on `ref`, its
        branch's derived one included: the work is in hand) and
        a pull request as the reference pass: finishing one is what brings the count down. A refusal
        of the session's own word is kept on the record (`balance_refused`) so the clearing can ring
        it; one of another's write on its record (a person's `--id`) is not, since the member never
        tried and has nothing to be rung about."""
        mark = self._balance_of(s)
        if mark is None:
            return None
        if ref is not None:
            if ref.startswith("#"):
                return None
            if any(e.ref == ref and e.status == "claimed" for e in s.progress):  # declared or from its branch
                return None
        if self._is_self(s, caller):
            s.balance_refused = {"at": now_iso(), "ref": ref}
            self._save(s)
        return balance_mod.refusal(s.team, mark)

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
        if not self._is_self(s, caller):
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
        if status == "none" and (words := self._balance_refusal(s, None, caller)):
            # a member refused a claim is not out of work (§6 *Balance*): its team idles, never winds down
            raise RpcError(words, balance=self._balance_of(s))
        if status == "none":
            s.out_of_work = {"at": now_iso(), "why": why.strip()}
            s.balance_refused = None  # taken, so the mark has gone: nothing left to ring
            # a second `none` is a declaration like the first: the tick looks afresh — but a `work` seat's
            # (§6 rule 3, TD-386) is the reading now, so an id that lands before the next tick fills it
            ids = self._seat_lane(s) if (s.seat or {}).get("trigger") == "work" else None
            if ids and s.host == self.host and await asyncio.to_thread(self._checkout_tree, Path(s.dir)):
                # …unless the checkout was not its own to work in (dirty, or off its default branch, TD-395):
                # what it could not reach is not its stretch, so the same ids fill it once the tree is clean
                ids = []
            s.lane_seen = {"at": now_iso(), "ids": ids} if ids is not None else None
        else:
            # The word stands whenever it is said — it is the session's — but one said inside
            # `RESTART_EARLY` of this record's own start is marked, and a controller does not act
            # on it: a run that is over before it began did not run out of context. The bound is
            # applied here because the home holds the start time; the controller reads a field.
            mark: dict[str, Any] = {"at": now_iso(), "why": why.strip()}
            # Early is read from the record, not from the clock alone (§4.9a, TD-249): a run that
            # reported new work is not one that never began, and one that reports only what an
            # earlier run did, or leaves a claim a third time, is the person's whenever it says so.
            # A restart its changed brief asked for (§6 rule 7) is neither: the run is not over on
            # its own account, and rule 2 is what brings the new brief; nor is one its team's changed flow
            # asked for (§4.9c *Switching*)
            reading = agent_common._restart_reading(s, datetime.now(UTC))
            if s.brief_changed or s.relaunch:
                reading = {"early": False, "repeat": None, "words": None}
            if reading["early"]:
                mark["early"] = True
                mark["decided"] = reading["words"]  # the row's, the card's and `ao status -v`'s words
            if reading["repeat"]:
                mark["repeat"] = reading["repeat"]
            s.restart_wanted = mark
            out = await self._report(s, True, None)
            if reading["words"]:
                out["decided"] = reading["words"]  # the reply names what decided it; the record keeps the fields
            return out
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
        if not self._is_self(s, caller):
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

    def _rounds_log(self, s: Session) -> Path:
        """The record's round log (§4.8, TD-191): keyed by its name in its repo, never by the run."""
        return paths.rounds_log(naming.base_id(s.dir, s.repo, s.name))

    async def rpc_log(self, id: str, text: str = "", caller: Any = None) -> dict[str, Any]:
        """`ao log "<line>"` (design §4.8 *A session's round log*, TD-191): one line, stamped to the
        minute, appended to the session's round log beside its run logs — the manager's memory
        across runs, never a report and never a commit. Only the session itself writes it (§9
        invariant 14, as `doing`): a person may not, since it is the session's own memory. Served
        where the session runs, as the run log is: a node writes its own. A line is text, never a
        control (TD-071): control bytes stripped, one line, capped at `ROUND_LINE_CAP`."""
        s = self._get(id)
        if not self._is_self(s, caller):
            raise RpcError(f"only {s.id} may write its round log: it is the session's own memory (design §4.8)")
        line = _clean(str(text or "").split("\n", 1)[0], ROUND_LINE_CAP).strip()
        if not line:
            raise RpcError('ao log needs a line: `ao log "<what this round did>"` (design §4.8)')
        entry = {"at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ"), "text": line}
        path = self._rounds_log(s)

        def append() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(f"{entry['at']} {line}\n")

        await asyncio.to_thread(append)
        return entry

    async def rpc_log_tail(self, id: str, n: int = 20) -> list[dict[str, str]]:
        """`ao log --tail n` (design §4.8, TD-191): the last `n` lines of the session's round log as
        `{at, text}`, oldest first; empty when it has none. A read, ungated (§9 invariant 11), and
        routed to the node a record runs on, whose disk holds the file."""
        s = self._get(id)
        path = self._rounds_log(s)
        n = max(0, min(int(n), 1000))

        def read() -> list[str]:
            try:
                return path.read_text(encoding="utf-8", errors="replace").splitlines()
            except FileNotFoundError:
                return []

        lines = [x for x in await asyncio.to_thread(read) if x.strip()]
        out = []
        for x in lines[-n:] if n else []:
            at, _, text = x.partition(" ")
            out.append({"at": at, "text": text})
        return out

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

    def _is_self(self, s: Session, caller: Any) -> bool:
        """Whether `caller` is the session `s` itself (design §9 invariant 14, TD-302): its address in
        this host's form — a node's session calls the home as `id@<node>` while its record's id is
        bare. The person never is."""
        return caller is not None and not mail.is_person(caller) and self._addr(caller) == self._address(s)

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
        # `AGENTORC_TMUX_SOCKET` names a private tmux server (TD-298), as the tests and `scripts/look_home.py`
        # use: a scratch home's agent never lists or drives the default server's sessions.
        tmux = Tmux(socket_name=os.environ.get("AGENTORC_TMUX_SOCKET") or None)
        asyncio.run(serve_until_signal(HostAgent(tmux=tmux)))
        return 0
    if args.cmd == "rpc":
        from sessionorc.client import bridge_stdio

        return asyncio.run(bridge_stdio())
    if args.cmd == "link":
        return asyncio.run(link.bridge(args.host))
    return 2


if __name__ == "__main__":
    sys.exit(main())
