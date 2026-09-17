"""Session records — the one shape every layer (agent, CLI, UI, adapters) agrees on.

A session is a tmux session, with or without a repo, with or without an agent (design §2.14).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

State = Literal["working", "needs-you", "limited", "stalled?", "idle", "exited", "closed", "unreachable"]
Kind = Literal["interactive", "command"]
Confidence = Literal["hook", "scraped"]

# Where a report entry came from (design §4.8, on the same rule as state): the session said so,
# the tick worked it out, or a screen rule read it off the pane. §9 invariant 10 gives `declared`
# precedence over the other two.
Source = Literal["declared", "derived", "scraped"]
SOURCES = ("declared", "derived", "scraped")
ProgressStatus = Literal["claimed", "done", "dropped"]
PROGRESS_STATUSES = ("claimed", "done", "dropped")

# Grants a session record can hold in `capabilities` (design §4.8). `control`: the session may
# act on other sessions through the host agent (§9 invariant 11).
GRANTS = ("control",)
# Renamed grants, old → new (TD-055, docs/glossary.md). For one release the old name is read
# wherever a grant is named — a stored record (normalised on load, so its next save writes the new
# name), a request, a role's `grants:` — and never written.
GRANT_ALIASES: dict[str, str] = {"orchestrate": "control"}


def canonical_grants(names: list[str]) -> list[str]:
    """`names` with renamed grants under their current names, each once, in the order given."""
    return list(dict.fromkeys(GRANT_ALIASES.get(n, n) for n in names))


def has_control(capabilities: list[str] | None) -> bool:
    """Whether a record's grants include `control`, under either name — a client can be newer than
    the host agent whose records it reads until that agent restarts (TD-062)."""
    return "control" in canonical_grants(list(capabilities or []))


# Message kinds (design §4.10): a small closed set, so a message's purpose is read off its envelope.
MailKind = Literal["note", "ask", "reply", "conflict"]
MAIL_KINDS = ("note", "ask", "reply", "conflict")
# The two kinds that pose a question and so carry a bound, may be closed by a reply, are left
# pending by an addressee's exit, and are never pruned while open. A `conflict` is an `ask` for
# every rule in §4.10; only its delivery shape differs.
ASK_KINDS = ("ask", "conflict")
PERSON = "person"  # `from` when a person sent the entry; never a session id

# Who owns which field of a record (design §4.4a, §9 invariant 15): the node observes and enforces
# on its host, the home holds the graph and intent, and identity is set once at create. Merges go
# by owner, never by last write, so a field belongs to exactly one set — `test_models` pins that
# every field of `Session` is in one and only one of the three.
NODE_OWNED = frozenset(
    {
        "state",
        "since",
        "pending",
        "confidence",
        "pane",
        "tail",
        "last_output",
        "exit_code",
        "git",
        "model",
        "subagents",
        "wrapup_sent_at",
        "run_log",
        "previous_run",
        "closed_at",
    }
)
HOME_OWNED = frozenset(
    {
        "controllers",
        "capabilities",
        "team",
        "project",
        "role",
        "lane",
        "unattended",
        "run_until",
        "wrapup_prompt",
        "progress",
        "findings",
        "out_of_work",
        "ledger",
        "seen_at",
        "inbox",
        "outbox",
        "threads",
        "sends",
        "superseded_by",
        "mail_decided",
        "wakes",
        "wake_refilled_at",
    }
)
IDENTITY = frozenset(
    {"id", "name", "kind", "adapter", "dir", "profile", "repo", "worktree", "adapter_id", "created", "host"}
)

# Urgent-first order (design §4.5). Lower sorts first. `unreachable` is placed by the UI
# depending on whether the host is volatile, so it gets two slots.
STATE_RANK: dict[str, int] = {
    "needs-you": 0,
    "limited": 1,
    "stalled?": 2,
    "unreachable": 3,
    "working": 4,
    "idle": 5,
    "exited": 7,
    "closed": 8,
}


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Pending:
    """What a `needs-you` or `limited` session is waiting on."""

    kind: str  # permission | question | prompt | limit | note (a screen rule's explanation, TD-032)
    text: str
    deadline: str | None = None  # ISO time the hook falls through to the terminal (permission only)
    tool_use_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Pending:
        return cls(kind=d["kind"], text=d.get("text", ""), deadline=d.get("deadline"), tool_use_id=d.get("tool_use_id"))


@dataclass
class ProgressEntry:
    """One reference the session set out to resolve (design §4.8 `progress`)."""

    ref: str
    status: ProgressStatus = "claimed"
    pr: int | None = None
    why: str | None = None  # `ao progress drop <ref> --why "…"`
    at: str = field(default_factory=now_iso)
    source: Source = "declared"
    # The branch a `derived` claim came from, so a claim the session has since left can be re-checked
    # by name for the PR it may have grown, and retired when it never grew one (TD-045). Never set on
    # a declared entry: what the session says about itself stands on its own.
    branch: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProgressEntry:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class FindingEntry:
    """One reference the session filed on the side (design §4.8 `findings`)."""

    ref: str
    priority: str | None = None
    at: str = field(default_factory=now_iso)
    source: Source = "declared"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FindingEntry:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class MailEntry:
    """One message as it sits in an inbox — or, the same entry, in its sender's `outbox`
    (design §4.10 "A message is delivered to a mailbox"). Every copy of one message carries the
    same `id`, which is what makes a thread one thread; the fields the home writes later
    (`read_at`, `closed_by`, `expired_at`, `pending`, `copies_failed`) are written on every record
    holding the id, so both cards show the same fact."""

    id: str  # minted by the home: `m-<hex>`
    from_: str  # the sender's session id, or PERSON; serialised as `from`
    to: list[str]  # the addressees the *sender* named (the recipient cap counts these)
    at: str  # stamped by the home on its own clock (§4.4a)
    kind: str  # one of MAIL_KINDS
    text: str
    about: str | None = None  # a session id, a `TD-NNN`, a PR — free text the sender chose
    read_at: str | None = None  # set only when `inbox` returned the entry to its caller (lifecycle stage 2)
    reply_to: str | None = None  # a `reply`: the entry it answers, one in the replier's own inbox
    root: str = ""  # the thread: the id of the first `ask`, `conflict` or `note` a chain replies to
    copies: list[str] = field(default_factory=list)  # the other controllers this landed with, expanded at send
    copies_failed: list[str] = field(default_factory=list)  # copies that could not land, dropped (§4.10)
    bound: str | None = None  # an `ask`'s expiry, wall-clock on the home's clock, running from `at` read or not
    closed_by: str | None = None  # the first `reply` that answered an `ask`; closes it uncounted
    closed_at: str | None = None  # when it did: retention for a closed `ask` runs from here
    expired_at: str | None = None  # the bound ran out, or an addressee was closed or forgotten
    pending: list[str] = field(default_factory=list)  # addressees that exited with the `ask` open (may resume)
    cites: list[str] = field(default_factory=list)  # a `conflict`: the `sends` ids it cannot reconcile

    def __post_init__(self) -> None:
        self.root = self.root or self.id  # a message replying to nothing is its own thread's root

    @property
    def open(self) -> bool:
        """An `ask` or `conflict` nobody has answered and whose bound has not run out — never
        pruned, and the one thing a first `reply` closes for free."""
        return self.kind in ASK_KINDS and not self.closed_by and not self.expired_at

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["from"] = d.pop("from_")
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MailEntry:
        d = dict(d)
        d["from_"] = d.pop("from", d.pop("from_", ""))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Tally:
    """The exchange count of one thread — or of one pair, for messages that reply to nothing —
    kept as its own field on every record holding an entry of it, never recounted from the entries
    that survive pruning (design §4.10 "How the count works, exactly"). A pair's count is the
    entries inside a rolling window, so `at` keeps their times; a thread's is a plain count."""

    count: int = 0
    bound_hit: bool = False
    at: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Tally:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class SendEntry:
    """One `send` or `keys` that reached this session's pane (design §4.10 "A `send` is recorded
    on the record it lands on"): who typed what, minted and stamped at the home like a message, so
    a `conflict` can cite two of them by id instead of guessing at the keystrokes."""

    id: str  # `s-<hex>`
    from_: str  # the caller's session id, or PERSON
    at: str
    text: str
    verdict: str = "submitted"  # or the submit error (`prompt-stuck`, …): the paste still reached the pane

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["from"] = d.pop("from_")
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SendEntry:
        d = dict(d)
        d["from_"] = d.pop("from", d.pop("from_", ""))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def normalize_ref(ref: str) -> str:
    """A reference is a ledger id, a PR number, or an attention-board line (design §4.8). Only the
    two machine-readable shapes are canonicalised, so `td-27` and `TD-027` are one entry, not two."""
    r = " ".join(str(ref).split())
    if not r:
        raise ValueError("a report entry needs a reference (a TD id, a PR number, a board line)")
    if m := re.fullmatch(r"(?i)([a-z]{2,6})-(\d{1,4})", r):
        return f"{m[1].upper()}-{int(m[2]):03d}"
    if m := re.fullmatch(r"#?(\d{1,6})", r):
        return f"#{m[1]}"
    return r


# What counts as something worth waking a lead for (design §4.8 "Waking a lead", TD-049). The
# vocabulary is deliberately short, and the exclusions are the point: `last_output`, `tail`,
# `since`, `seen_at`, `git`, `subagents` and `model` move on almost every tick of a healthy
# session, so a digest over the whole record would wake a lead continuously and be worth less
# than the poll it replaces. What is left is what a lead acts on: the state it may have to answer
# or restart, the pending thing it would answer, what the session has claimed or finished, what it
# has filed, and whether it is still this lead's to act on.
WAKE_FIELDS = ("state", "exit_code")


def wake_digest(session: dict[str, Any]) -> str:
    """A stable string for the parts of a record a lead is waiting on.

    Two records with the same digest are the same *to a lead*, however much else has moved. A
    session appearing or disappearing is a change in its own right and is handled by the caller,
    which compares the set of ids as well as the digests.
    """
    p = session.get("pending") or {}
    parts: list[str] = [f"{f}={session.get(f)!r}" for f in WAKE_FIELDS]
    # A permission's `deadline` counts down every tick, so the countdown is not the event — the
    # question being asked is.
    parts.append(f"pending={(p.get('kind'), p.get('text'))!r}")
    prog = [(x.get("ref"), x.get("status"), x.get("pr")) for x in session.get("progress") or []]
    parts.append("progress=" + repr(prog))
    parts.append("findings=" + repr([(x.get("ref"), x.get("priority")) for x in session.get("findings") or []]))
    parts.append("controllers=" + repr(sorted(session.get("controllers") or [])))
    ow = session.get("out_of_work") or {}
    parts.append(f"out_of_work={(ow.get('at'), ow.get('why'))!r}")  # an ending, not a crash (§4.9a)
    return "\n".join(parts)


def report_head(session: dict[str, Any]) -> dict[str, Any] | None:
    """The one `progress` entry a one-line report leads with: what the session is on now, else the
    last thing it finished, else whatever it has. One rule, so a card, `ao status -v` and the Focus
    panel all lead with the same entry."""
    progress = session.get("progress") or []
    done = [p for p in progress if p.get("status") == "done"]
    claimed = [p for p in progress if p.get("status") == "claimed"]
    return (claimed or done or progress or [None])[-1]


def stop_note(session: dict[str, Any]) -> str:
    """ "stops 06:00" for a card or `ao status -v` (design §6, TD-026), empty when nothing will stop
    it. One formatter, so the page and the CLI cannot drift — the rule `report_line` above follows.

    The record keeps UTC; this reads in the *host's* local clock, which in phase 1 is the one the
    person is looking at. A day prefix appears once the stop is not today, so *stops Mon 06:00* can
    never be read as this evening.
    """
    when = session.get("run_until")
    if not when:
        return ""
    try:
        at = datetime.fromisoformat(str(when).replace("Z", "+00:00")).astimezone()
    except ValueError:
        # A record is written through `_stop_time`'s validator, so this needs a hand-edited or
        # corrupted one — and then it is one card's note, not the whole grid: `view()` runs this for
        # every session on the page. The `since` field beside it is read the same way (`_age`).
        return ""
    day = "" if at.date() == datetime.now().astimezone().date() else at.strftime("%a ")
    return f"stops {day}{at:%H:%M}" + (" · wrap-up sent" if session.get("wrapup_sent_at") else "")


def report_line(session: dict[str, Any]) -> str:
    """The one-line report a card or `ao status -v` shows (design §4.8): the reference in hand, the
    PR it is on, and the lane count — `TD-027 → #60 · 1/2 done`. A reference whose entry the agent
    derived rather than the session declared carries `~`, as a scraped state does (§9 invariant 10).
    Empty when the session has neither a lane nor a single entry."""
    progress = session.get("progress") or []
    lane = [r for r in (session.get("lane") or []) if r != "free-pick"]
    done = [p for p in progress if p.get("status") == "done"]
    head = report_head(session)
    bits = []
    if head:
        bits.append(
            head["ref"]
            + ("~" if head.get("source", "declared") != "declared" else "")
            + (f" → #{head['pr']}" if head.get("pr") else "")
        )
    if total := (len(lane) or len(progress)):
        bits.append(f"{len(done)}/{total} done")
    return " · ".join(bits)


@dataclass
class Session:
    id: str  # agentorc's own id; also the tmux session name
    name: str  # what the person called it
    kind: Kind
    adapter: str  # claude-code | shell | ...
    dir: str
    profile: str = ""  # empty for shell
    repo: str | None = None
    worktree: str | None = None
    adapter_id: str | None = None  # the tool's own session id (Claude Code uuid) once known
    state: State = "working"
    since: str = field(default_factory=now_iso)
    pending: Pending | None = None
    confidence: Confidence = "scraped"
    unattended: bool = False
    created: str = field(default_factory=now_iso)
    tail: list[str] = field(default_factory=list)
    exit_code: int | None = None
    # A tmux pane (live or dead) still backs this record. False after `kill`/`close` (the session is
    # destroyed) or when the tick finds no pane; a natural exit keeps its dead pane (TD-023).
    pane: bool = True
    subagents: int = 0  # live subagents (SubagentStart − SubagentStop); Ready to close needs zero
    last_output: str | None = None  # ISO time the run log last grew (liveness cross-check)
    run_log: str | None = None
    # The run log of the exited session of the same name this one replaced (design §4.1, TD-030):
    # the name is reused, so the previous run stays reachable from the record that took it over.
    previous_run: str | None = None
    closed_at: str | None = None
    seen_at: str | None = None  # last time a person looked (Focus opened, card acted on); TD-017
    git: dict[str, Any] | None = None  # branch, dirty, ahead, behind, files (sessionorc.gitinfo)
    capabilities: list[str] = field(default_factory=list)  # grants, from GRANTS (design §4.8)
    # Membership (design §4.8, TD-036): the session ids that may act on *this* session. The list
    # lives on the target, not on the lead, so the gate is one lookup and nothing has to be
    # kept in step; empty — the default — means nobody may act on it. A grant says a session may
    # act on others at all; this says on which. Several controllers are allowed and none is
    # privileged.
    controllers: list[str] = field(default_factory=list)
    # Badges (design §4.9): the team and project a session was started under, two plain strings
    # the clients set from `org.yml` — the agent never reads that file, and nothing here keys on
    # either (§9 invariant 9); the Org page derives its groups from the badge and `controllers`.
    team: str = ""
    project: str = ""
    # The model actually in use, when the adapter can tell (TD-031): observed, never the profile's
    # declared model — that is an intent (§4.2a), and a display says so when it falls back to it.
    model: str | None = None
    # Report channels (design §4.8). `lane` is the ordered list of references the session was handed
    # (or `["free-pick"]`), so a display can say *1 of 2* without parsing the brief; the other two
    # are what the session says it did.
    lane: list[str] = field(default_factory=list)
    progress: list[ProgressEntry] = field(default_factory=list)
    findings: list[FindingEntry] = field(default_factory=list)
    # `{at, why}` once the session has declared it searched and found nothing it may pick
    # (`ao progress none --why`, design §4.9a): beside `progress`, never an entry in it. Only the
    # session itself writes it and nothing derives it (§9 invariant 14); a later declared claim
    # clears it, since the session has work again.
    out_of_work: dict[str, str] | None = None
    # The preset the session was started under (design §4.8): a badge, and nothing keys on it
    # (§9 invariant 9). Empty for a session started without one.
    role: str = ""
    # Where a `TD-NNN` reference resolves in this session's repo, relative to its checkout — the
    # client reads it from the repo's `.agentorc.yml` (`ledger:`, design §5) at create and hands it
    # over, so the derived-report tick (`sessionorc.reports`) reads the right file without this
    # package knowing the config format. None: the ledger's default path.
    ledger: str | None = None
    # When this session must stop, and how to ask it to (design §6, TD-026 gap 1). A session started
    # by hand with `--unattended` had no stopper at all: nothing wrapped it up at 06:00, at a usage
    # cap or when its token lapsed, so "start it now so I can watch it" meant "remember to close it
    # yourself". `run_until` is an absolute UTC time; at it the agent sends `wrapup_prompt` once and
    # kills the session when it settles or the grace runs out. The prompt's *wording* comes from the
    # client, like `ledger` does, because this package must not know what a brief or a role is.
    run_until: str | None = None
    wrapup_prompt: str | None = None
    wrapup_sent_at: str | None = None
    # The host this record's tmux session runs on (design §4.4a, TD-057 step 1). Ids naming a
    # session on this same host are stored bare; only another host's are stored `id@host`
    # (`naming.qualify`). The host agent fills it at create and backfills it on load.
    host: str = ""
    # Mail (design §4.10, TD-052 step 1). `inbox` lives on the *recipient*, on the same rule as
    # `controllers`: persisted with the record, dies when it is forgotten, moves with a resume.
    # `outbox` is the sender's own copy of what it sent, so "the sender's entry" — the one that
    # carries `copies_failed`, the expired mark, *addressee exited* — is a real entry and not a
    # scan of other records' inboxes. `threads` is the exchange tally per thread root (and per
    # pair, keyed `pair:<other>`), a field so pruning cannot reset the deadlock bound. `sends` is
    # what was typed into this pane and by whom. All home-owned (§4.4a).
    inbox: list[MailEntry] = field(default_factory=list)
    outbox: list[MailEntry] = field(default_factory=list)
    threads: dict[str, Tally] = field(default_factory=dict)
    sends: list[SendEntry] = field(default_factory=list)
    # Set on the closed record a resume left behind: the id continuing its conversation. Mail
    # addressed to this record is forwarded there; an act on it is refused as on any closed record.
    superseded_by: str | None = None
    # The wake decision (design §4.10, TD-052 step 3). `mail_decided` is the one watermark per
    # session — `{id, at}` of the newest inbox entry any wake has covered; a decision not to wake
    # leaves it where it was. `wakes` is every decision that woke the session, bounded
    # (`mail.WAKES_KEEP`): `{at, cause: "mail" | "member", charged, covered}` — the record step 5
    # measures, a charged mail wake apart from a free one that rode a member's change.
    # `wake_refilled_at` is the last person's act toward the session: charged wakes before it no
    # longer count against the budget.
    mail_decided: dict[str, str] | None = None
    wakes: list[dict[str, Any]] = field(default_factory=list)
    wake_refilled_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """The whole record, as the store writes it."""
        d = asdict(self)
        d.pop("rev")
        d["pending"] = self.pending.to_dict() if self.pending else None
        d["inbox"] = [e.to_dict() for e in self.inbox]
        d["outbox"] = [e.to_dict() for e in self.outbox]
        d["threads"] = {k: t.to_dict() for k, t in self.threads.items()}
        d["sends"] = [e.to_dict() for e in self.sends]
        return d

    def view(self) -> dict[str, Any]:
        """The record as `list`, `get` and the `subscribe` deltas hand it out: message bodies never
        ride the push that reaches the Org page on every change (design §4.10 "A bounded body") —
        those carry counts, and a body is fetched by `inbox`. `threads` (the `bound_hit` marks a
        card shows) and `sends` (what `ao status` prints) stay."""
        d = self.to_dict()
        d.pop("inbox")
        d.pop("outbox")
        d["unread"] = self.unread()
        d["mail"] = self.mail_marks()
        return d

    def unread(self) -> int:
        return sum(1 for e in self.inbox if not e.read_at)

    def mail_marks(self) -> dict[str, Any]:
        """What a card and an `ao` reply say about this session's mail without a body: open `ask`s
        it holds, its own `ask`s that expired or whose addressee exited, and copies that failed."""
        return {
            "open_asks": [e.id for e in self.inbox if e.open],
            "expired": [e.id for e in self.outbox if e.expired_at],
            "addressee_exited": [e.id for e in self.outbox if e.pending and e.open],
            "copies_failed": [e.id for e in self.outbox if e.copies_failed],
            "bound_hit": [k for k, t in self.threads.items() if t.bound_hit],
            "wake_budget_spent": self.wake_budget_spent(),
        }

    def wake_budget_spent(self) -> bool:
        """Exhaustion is visible (design §4.10): on the record, and so on the card and every `ao`
        reply. The rule and its placeholder numbers live in `mail`, beside the other bounds."""
        from sessionorc import mail  # mail imports this module; the bound is read at call time

        return mail.wake_budget_spent(self, datetime.now(UTC))

    def holds(self, msg_id: str) -> list[MailEntry]:
        """Every copy of a message this record holds: at most one in the inbox, one in the outbox."""
        return [e for e in (*self.inbox, *self.outbox) if e.id == msg_id]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Session:
        d = dict(d)
        pending = d.pop("pending", None)
        progress = d.pop("progress", None) or []
        findings = d.pop("findings", None) or []
        inbox = d.pop("inbox", None) or []
        outbox = d.pop("outbox", None) or []
        threads = d.pop("threads", None) or {}
        sends = d.pop("sends", None) or []
        known = {f for f in cls.__dataclass_fields__}
        obj = cls(**{k: v for k, v in d.items() if k in known})
        obj.pending = Pending.from_dict(pending) if pending else None
        if renamed := [g for g in obj.capabilities if g in GRANT_ALIASES]:
            obj.capabilities = canonical_grants(obj.capabilities)
            obj.renamed_grants = renamed  # not a field: tells the loader to save and say so (TD-055)
        obj.progress = [ProgressEntry.from_dict(p) for p in progress]
        obj.findings = [FindingEntry.from_dict(f) for f in findings]
        obj.inbox = [MailEntry.from_dict(e) for e in inbox]
        obj.outbox = [MailEntry.from_dict(e) for e in outbox]
        obj.threads = {k: Tally.from_dict(t) for k, t in threads.items()}
        obj.sends = [SendEntry.from_dict(e) for e in sends]
        return obj

    # Transition counter, in memory only (dropped by `to_dict`, so 0 on load): `since` is whole
    # seconds, so a turn that starts and ends inside one second would look unchanged to a waiter.
    rev: int = field(default=0, compare=False, repr=False)

    def set_state(self, state: State, *, confidence: Confidence, pending: Pending | None = None) -> None:
        if state != self.state:
            self.since = now_iso()
            self.rev += 1
        self.state = state
        self.confidence = confidence
        self.pending = pending

    def report_progress(self, entry: ProgressEntry) -> bool:
        """Upsert a `progress` entry by reference, in place so the lane order the session declared
        survives. Returns False when §9 invariant 10 refuses the write: what the session declared is
        never overwritten by what the agent derived or scraped."""
        return _upsert(self.progress, entry)

    def report_finding(self, entry: FindingEntry) -> bool:
        """Upsert a `findings` entry by reference; same invariant-10 rule as `report_progress`."""
        return _upsert(self.findings, entry)

    def retire_branch_claims(self, refs: Iterable[str]) -> bool:
        """Drop the derived, PR-less `claimed` entries named in `refs`. Returns True if any went.

        Nothing else in the system could ever remove one: the `pending` re-check works by PR number,
        the upsert has no delete branch, and invariant 10 means a derived entry is replaced only
        when the session *declares* the same reference. So a `tdNNN-*` branch created and abandoned
        before its PR existed — what a grinder does the moment it finds a neighbour already holds
        that TD — left a `claimed` entry forever, and the idle-with-open-work send (§4.8, §6) fires
        on exactly that (TD-045). Who is retireable is decided by `sessionorc.reports.derive`, which
        is the half that can see whether the branch ever grew a PR.

        Narrow on purpose: a `done` entry is a fact about the past and stays, an entry carrying a PR
        number stays because the by-number re-check is still watching it, and a *declared* entry is
        the session's own word and is never touched (§9 invariant 10)."""
        wanted = set(refs)
        if not wanted:
            return False
        before = len(self.progress)
        self.progress = [
            e
            for e in self.progress
            if not (e.source != "declared" and e.status == "claimed" and not e.pr and e.ref in wanted)
        ]
        return len(self.progress) != before


def _upsert(entries: list[Any], entry: Any) -> bool:
    for i, old in enumerate(entries):
        if old.ref != entry.ref:
            continue
        if old.source == "declared" and entry.source != "declared":
            return False
        entries[i] = entry
        return True
    entries.append(entry)
    return True


# -- the replica's merge, in both directions (design §4.4a, TD-057 step 2) --------------------------


class NotTheSameSession(ValueError):
    """A copy that disagrees with the record on an identity field describes another session."""


def _overlay(record: Session, copy: Mapping[str, Any], owned: frozenset[str]) -> Session:
    for f in sorted(IDENTITY):
        if f in copy and copy[f] != getattr(record, f):
            raise NotTheSameSession(f"{record.id}: `{f}` is {getattr(record, f)!r} here and {copy[f]!r} in the copy")
    parsed = Session.from_dict({**record.to_dict(), **{k: v for k, v in copy.items() if k in owned}})
    for f in owned:
        if f in copy:
            setattr(record, f, getattr(parsed, f))
    return record


def apply_home(record: Session, home_copy: Mapping[str, Any]) -> Session:
    """A node's replica takes the home's copy: exactly the home-owned fields are overlaid — the
    graph and intent — and nothing the node observes is touched. Merges go by owner, never by last
    write (§4.4a): the link was down, the node wrapped a worker up and it exited, and meanwhile a
    person at the home extended its `run_until`; the node's `exited` stands, and so does the new
    `run_until`. `home_copy` is a record as `to_dict()` writes it; fields it omits are left alone."""
    return _overlay(record, home_copy, HOME_OWNED)


def apply_node(record: Session, report: Mapping[str, Any]) -> Session:
    """The home's record takes a node's report: exactly the node-owned fields — what the node
    observes and enforces on its host. The mirror of `apply_home`."""
    return _overlay(record, report, NODE_OWNED)
