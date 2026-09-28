"""What the host agent's modules share (TD-108 step 1): its constants, the helpers that are not
methods, and the small classes around the RPC — moved out of `sessionorc.agent` so the mixins the
agent is being split into can import them without importing the agent. `sessionorc.agent` re-exports
every name here, so `from sessionorc.agent import X` still works; a test that patches one of these
constants patches it here (`sessionorc.agent_common.X`), since here is where it is read.
"""

from __future__ import annotations

import asyncio
import heapq
import inspect
import json
import logging
import os
import re
import socket
import stat
import struct
import tarfile
import time
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sessionorc import link, mail, paths
from sessionorc.models import (
    GRANTS,
    HOME_OWNED,
    SOURCES,
    MailEntry,
    Session,
    normalize_context,
    normalize_ref,
    normalize_review,
    report_line,
)

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
# A `restart` declared inside this of the record's own start is marked `early` (design §4.9a
# *A run that ends with work left*, TD-083). The word still stands — it is the session's — but a
# controller does not act on an early one and puts it on the board instead: a run that is over
# before it began did not run out of context. The home applies the bound because it holds the
# start time, so a controller reads a field and never a clock.
RESTART_EARLY = timedelta(minutes=30)
GIT_EVERY = timedelta(seconds=10)  # git status per live session, cheap and cached
# Derived report entries per session (design §4.8, TD-028 step 3): a `gh` call and a little git, so
# a slow cadence. Nothing waits on it and a failure derives nothing (`sessionorc.reports`).
DERIVE_EVERY = timedelta(minutes=5)
# The model in use per live agent session (TD-031): a local file's tail, so cheap, but not per tick.
MODEL_EVERY = timedelta(seconds=30)
CONTEXT_EVERY = timedelta(minutes=1)  # the context reading, unattended records only (§6 rule 5, TD-190)
LANE_NEWS_NAMED = 5  # rule 6's note names this many new entries, then *and n more* (§6, TD-195)
CREATE_GRACE = timedelta(seconds=10)  # a pane snapshot older than a session cannot judge it
SEND_STALL_SECONDS = 5.0  # `send(wait=True)`: no sign of the prompt being taken within this → prompt-stalled
PASTE_SHOW_SECONDS = 1.0  # `send`: how long the pasted text gets to appear in the composer before Enter (TD-027)
SUBMIT_SECONDS = 1.5  # `send`: how long the composer gets to empty after Enter, per try (TD-027)
COMPOSER_LINES = 12  # raw rows an adapter's `composer` reads (the composer sits above a status line or two)
DOORBELL_TRIES = 2  # a doorbell that fails to submit is tried once more, then recorded (design §4.10)
TITLE_CAP = 80  # characters of the tool's own title kept (design §4.5a **title**, TD-074): a name, not a line
SETTLED = ("idle", "needs-you", "exited", "closed", "limited", "stalled?")  # where a `send(wait=True)` ends
# `ACTING_RPCS` lives in `sessionorc.mail` beside the gates, and is re-exported here for the
# callers that always read it from the agent.
REMOVED_GUARD_SECONDS = 60.0  # how long a removed session's name is checked against re-adoption
PRUNE_EVERY = timedelta(hours=1)  # run-log retention sweep (design §4.6, `runs_keep_days`)
ID_RECHECK = 30.0  # seconds between re-reads of the tmux server's pid (design §4.8a, TD-077)
TRAIL_KEEP = 100  # attention-trail entries kept, newest first (design §4.10, TD-079)
TRAIL_FLOOR = timedelta(seconds=5)  # a row this short leaves no trail unless a person ended it
# What **Reply** on a board row says it did until dev-cadence's reader carries each item's `session` and
# `refs` (TD-142 slice 2): the file half alone, and nobody mailed.
BOARD_REPLY_NOTE = "written on the board — no session standing is known: the board reader carries no session fields yet"
# A session past its `run_until` is asked to wrap up and then killed (design §6, TD-026): this is how
# long it is given to finish after the ask. It is a grace, not a deadline the session can see — a
# session that settles sooner is killed sooner, and one that is still working when it runs out is
# killed anyway, because the whole point is that nobody is watching.
WRAPUP_GRACE = timedelta(minutes=10)
# The usage gate (design §6, TD-100) resumes a paused session no sooner than this after the pause:
# a reading that flickers across the line must not type pause and resume at a session every minute.
RESUME_MIN = timedelta(minutes=10)
# The crash restart's ceiling (design §6 *Keeping a team running* rule 1, §4.8 — OTP's, systemd's
# and Circus's numbers): three restarts of one session inside two hours, `one_for_one`. The fourth
# exit inside the window is the person's.
RESTART_CEILING = 3
RESTART_WINDOW = timedelta(hours=2)
# A record a tick restart has just created is not judged crashed for this long (TD-186): the run it
# replaced may still be ending, and what it reports then is not the new run's.
RESTART_SETTLE = timedelta(seconds=60)
# §6 rule 3 (TD-103 slice 3): six fills an hour over all seats sharing a controller, and how long a
# seat must sit hook-confirmed idle with nothing due before the tick closes it — so a seat just
# filled, idle for a moment before its prompt lands, is not closed on the tick that filled it.
FILL_CEILING = 6
FILL_WINDOW = timedelta(hours=1)
SEAT_IDLE_GRACE = timedelta(minutes=2)
# §6 rules 2 and 4 (TD-103 slice 4): how long a member sits hook-confirmed idle with open work before
# the one nudge, and how long a wanted restart held by work left waits before it is the Inbox's.
IDLE_NUDGE = timedelta(minutes=20)
# §6 rule 5 (TD-190): the context-bound line is typed again after this, while the member is still
# idle and over its bound.
CONTEXT_AGAIN = timedelta(minutes=20)
# A round-log line (design §4.8 *A session's round log*, TD-191): one line, a manager's round says
# who did what, so it is allowed more than `doing`'s 200 characters.
ROUND_LINE_CAP = 500
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
# `suspend` is deliberately **not** here (§4.8a, TD-077 a2): this set is what the home *forwards*
# to a node and then mirrors on its own copy, and a suspend forwarded that way would kill twice.
# The home does it the other way round — it marks its own record, which owns the field, and routes
# only the `kill`. `modes.HOME_EDITS` is the other table, and `suspend` **is** in that one: a node
# asked to suspend forwards the whole act here, or its mark would be wiped by the home's next copy.
HOME_EDITS = frozenset({"set_mode", "set_stop", "set_grants", "set_controllers"})
# What the home reads from the node whose name is the record's `host` (§4.4a, step 4b.1): a pane's
# screen, which only that node's tmux holds. Reads are never gated (§9 invariant 11), so these are
# their own set and cross as their own link method, `read`, whose allowlist is this set alone — a
# read can never reach an acting method through it, and `act`'s allowlist never grows by a read.
NODE_READS = frozenset({"tail", "explain", "log_tail"})


def _oldest_first(found: dict[str, MailEntry], chains: list[list[str]]) -> list[MailEntry]:
    """A thread's entries oldest first (TD-136). `at` is whole seconds, so a reply and the next
    question can share one; each mailbox, though, holds its entries in the order they landed, and a
    reply comes after what it answers, and whatever settled a question (its outcome, or asking
    again on the thread) after the reply that answered it. So the order is a merge all of those
    agree with — taking the earliest `at` (then id) among the entries free to go next."""
    succ: dict[str, set[str]] = {i: set() for i in found}
    indeg = dict.fromkeys(found, 0)
    answers = [[e.reply_to, e.id] for e in found.values() if e.reply_to in found]
    settled = [
        [e.closed_by, e.outcome.get("by")]
        for e in found.values()
        if e.outcome and e.closed_by in found and e.outcome.get("by") in found
    ]
    for chain in [*chains, *answers, *settled]:
        for a, b in zip(chain, chain[1:], strict=False):
            if b not in succ[a]:
                succ[a].add(b)
                indeg[b] += 1
    ready = [(found[i].at, i) for i, n in indeg.items() if n == 0]
    heapq.heapify(ready)
    out: list[MailEntry] = []
    while ready:
        _, i = heapq.heappop(ready)
        out.append(found[i])
        for b in succ[i]:
            indeg[b] -= 1
            if indeg[b] == 0:
                heapq.heappush(ready, (found[b].at, b))
    placed = {e.id for e in out}
    left = sorted((e for e in found.values() if e.id not in placed), key=lambda e: (e.at, e.id))
    return out + left  # boxes that disagree (never expected) still lose nothing


# What the home pushes a node about each of its records (§4.4a "The home pushes each node its
# records' policy fields as they change", step 4b.2): the home-owned fields, less the mailbox — the
# inbox, outbox, threads, wakes and `mail_decided` stay the home's, and no message body ever reaches
# a node — less `sends`, which every pane's own node writes first (a send runs there) and the merge
# unions, and less `superseded_by`, which each end writes for itself: the node when a resume there
# supersedes a record, the home when the successor's report says so (`supersedes`, node-owned;
# `_take_supersession`, TD-057).
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
BACKUP_MEMBERS = ("sessions", "remote", "person_inbox.json", "org.yml", "hosts.yml", "profiles.yml", "settings.yml")
REPORT_EVERY = 5.0  # seconds between a node's reports of one record whose state did not move (§4.4a)
# Seconds between usage polls per account (TD-001, TD-122): a slow cadence, never per tick. **Five
# minutes, not one** (TD-087): the shortest window the endpoint reports is five hours, so a
# minute buys nothing and spends an allowance shared with the tool itself.
USAGE_EVERY = 300.0
# Seconds between reads of the repo facts (design §4.4 *Repo facts*, TD-176): each registered
# checkout's PRs through `gh` and its ledger's git history, in a thread. The ledger itself is
# re-read on any tick its file's mtime moved, since that read is a local file.
REPOS_EVERY = 300.0
USAGE_BACKOFF_MAX = 3600.0  # the ceiling a 429 doubles up to, when the endpoint sends no Retry-After
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


LAUNCH_KEYS = (
    "name", "dir", "adapter", "profile", "repo", "worktree", "argv", "unattended", "prompt", "capabilities",
    "lane", "role", "ledger", "team", "project", "run_until", "wrapup_prompt", "pause_prompt", "resume_prompt",
    "seat", "review", "context_bound",
)  # fmt: skip


def launch_params(given: dict[str, Any]) -> dict[str, Any]:
    """The create's own arguments a launch record keeps (design §6): what a replay hands to `create`
    again, taken from the call as it arrived — never the conversation (`resume`), the caller, or
    the mail hand-over, which are the start's own and not the session's."""
    return {k: given[k] for k in LAUNCH_KEYS if k in given and given[k] is not None}


def _alarm_since(s: Session) -> str:
    """When an alarm row began: the first alarm's own time, not the record's state transition."""
    first = (s.identity_alarms or [{}])[0]
    return str(first.get("at") or first.get("first") or "")


def _ended_by(s: Session, slot: str, was: str) -> str:
    """The word for a state row that ended **because its session did** (design §4.10 rule 2,
    TD-088): the record now reads `exited` or `closed`, so the home can tell, and *resolved* — the
    word for when it cannot — would tell a person whose question a worker died holding that it had
    sorted itself out. An alarm row does not end with the session, and an `unpushed` row is itself
    a row of an exited record, so the exit is not what ended it; a close is."""
    if slot != "state":
        return ""
    if s.state == "closed":
        return "the session was closed"
    if s.state == "exited" and was != "unpushed":
        return "the session exited"
    return ""


def _alarm_report(s: Session, mode: str | None) -> str:
    """The words **Log TD** hands on (design §4.8a *An alarm's answers*, TD-077 b). Composed by
    the home from the alarm's own fields and the record's, so nothing here is a model's prose
    being passed off as a report: the two lines a session wrote — its `doing` and its report
    line — are quoted as text and labelled, and everything else is a field.

    It says which identity mode the alarm was raised under, because *observe* records what
    *enforce* would have refused and a reader needs to know which they are looking at. That is
    the mode on the alarm itself — the raising host's — and `mode` (this host's) only for a record
    of this host whose alarm predates the field; for a node's record without it the clause is left
    out rather than said with the home's mode under the node's name (review of PR #318)."""
    a = (s.identity_alarms or [{}])[0]
    n = len(s.identity_alarms or [])
    mode = a.get("mode") or mode
    lines = [
        f"Identity alarm on {s.name} ({s.id}) — this session made a request under another session's name.",
        f"channel: {a.get('channel') or 'unknown'} · claimed: {a.get('claimed') or 'unknown'} "
        f"· rpc: {a.get('rpc') or 'unknown'} · seen {a.get('count') or 1}×",
        f"first {a.get('first') or a.get('at') or 'unknown'} · last {a.get('last') or a.get('at') or 'unknown'}"
        + (f" · raised on {s.host} in identity {mode}" if mode else ""),
    ]
    if n > 1:
        lines.append(f"and {n - 1} more distinct claim{'' if n == 2 else 's'} on the same record.")
    if doing := (s.doing or {}).get("text"):
        lines.append(f'it said it was doing: "{doing}"')
    if line := report_line(s.view()):
        lines.append(f"its report line: {line}")
    lines.append(
        "This is either a bug of ours or a session misbehaving. File it where work is picked up, "
        'then report the outcome: ao msg person --outcome done|blocked|dropped "<one line>" --for <this id>.'
    )
    return "\n".join(lines)


def _alarm_words(s: Session) -> str:
    """What an identity-alarm row says, in one line: the first alarm's own words, as §4.8a has the
    list keep the first and count the rest."""
    first = (s.identity_alarms or [{}])[0]
    return str(first.get("words") or first.get("claimed") or "an identity alarm")


def _older(stamp: Any, now: datetime) -> bool:
    """Past the retention window, for a trail entry's own stamp. An unreadable stamp is kept: the
    trail is evidence, and losing it to a bad clock would be worse than one stale row."""
    if mail.MAIL_RETENTION is None or not stamp:
        return False
    try:
        return _parse(str(stamp)) + mail.MAIL_RETENTION <= now
    except (ValueError, TypeError):
        return False


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


def _review(review: Any) -> dict[str, Any] | None:
    """A role preset's `review:` as the record keeps it (design §4.9b *The reader*, TD-093)."""
    try:
        return normalize_review(review)
    except ValueError as e:
        raise RpcError(str(e)) from None


def _context_bound(bound: Any) -> int | None:
    """A role preset's context bound as the record keeps it (design §4.8, TD-190): tokens, or None.
    The create is handed the number the client resolved, and checks it as the preset key is."""
    if bound is None:
        return None
    try:
        return normalize_context({"bound": bound})
    except ValueError as e:
        raise RpcError(str(e)) from None


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
    """Validate a list of grant names against `GRANTS`, in canonical order."""
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


def _clean(text: str, cap: int = 200) -> str:
    """Strip ANSI/control bytes and cap width: pane output is untrusted everywhere but xterm.js."""
    text = _OSC.sub("", text)
    text = _CSI.sub("", text)
    text = _ESC_OTHER.sub("", text)
    text = "".join(ch for ch in text if ch == "\t" or ch >= " ")
    return text[:cap]


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
    """The pending text for a capped profile, or None: **any** window the adapter reports at or
    over 100% whose `resets` has not passed (TD-073). The windows and their labels are the
    adapter's — a tool with one daily window, three windows or none says so and this package names
    none of them (design §4.3). A window whose `resets` has passed is not a cap any more even
    before the next poll says so."""
    if not usage:
        return None
    now = datetime.now(UTC)
    for w in usage.get("windows") or ():
        if not isinstance(w, dict):
            continue
        try:
            pct = int(w.get("pct") or 0)
        except (TypeError, ValueError):
            continue
        if pct < 100:
            continue
        try:
            at = _parse(str(w.get("resets"))) if w.get("resets") else None
        except (TypeError, ValueError):
            at = None  # unparseable: still a cap, reset time unknown
        if at is not None and at <= now:
            continue
        label = _clean(str(w.get("label") or "usage"))[:24] or "usage"
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


def _duration(text: str) -> timedelta | None:
    """A seat's `every:` as written (§4.9b: `30m`, `6h`, `1d`), or None when it is not one."""
    m = re.fullmatch(r"([1-9]\d*)([mhd])", text.strip())
    if not m:
        return None
    unit = {"m": "minutes", "h": "hours", "d": "days"}[m.group(2)]
    return timedelta(**{unit: int(m.group(1))})


def _recent(at: Any, now: datetime, window: timedelta) -> bool:
    """Whether an instant off a record falls inside `window` before `now`. An unreadable one is not
    recent: a restart entry is the host agent's own, so one it cannot read was never written by it."""
    try:
        return now - _parse(str(at)) < window
    except (TypeError, ValueError):
        return False


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _usage_key(adapter: Any, name: str, profile: str) -> tuple[str, str]:
    """The key a profile's usage is polled, cached and backed off under, and the account's name
    for the chip (§4.2a, TD-122): `(adapter, account)`, the account from the adapter's optional
    `account_for` — this package cannot read a profile. An adapter without it, or one that cannot
    say, keys on the profile, which is the per-profile poll as it was."""
    try:
        account = (getattr(adapter, "account_for", None) or (lambda _p: None))(profile)
    except Exception:  # noqa: BLE001 — an adapter's lookup never stops the poll
        account = None
    account = str(account or profile or "default")
    return f"{name}:{account}", account


def _usage_checked_at(reading: dict[str, Any]) -> float | None:
    """The monotonic clock's reading when `reading` was fetched, for seeding `_usage_checked`, or
    None when its `fetched` is not a time. A `fetched` in the future counts as now: a clock that
    stepped back may delay one poll by a period, never bring it forward."""
    try:
        age = (datetime.now(UTC) - _parse(str(reading.get("fetched")))).total_seconds()
    except (ValueError, TypeError):
        return None
    return time.monotonic() - max(age, 0.0)
