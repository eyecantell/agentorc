"""Session records — the one shape every layer (agent, CLI, UI, adapters) agrees on.

A session is a tmux session, with or without a repo, with or without an agent (design §2.14).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sessionorc import naming

# `scheduled` (design §6 *Start time*, TD-152): a record with a name, a directory, a launch record and
# the directory's slot, but no pane yet — the home's tick creates the session at its `start_at`
State = Literal["working", "needs-you", "limited", "stalled?", "idle", "exited", "closed", "unreachable", "scheduled"]
Kind = Literal["interactive", "command"]
# `tick`: the host agent observed it — a pane dead or gone, a kill, a close, `stalled?` (design §4.2, TD-490)
Confidence = Literal["hook", "scraped", "tick"]

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


def has_control(capabilities: list[str] | None) -> bool:
    """Whether a record's grants include `control`."""
    return "control" in (capabilities or [])


# Message kinds (design §4.10): a small closed set, so a message's purpose is read off its envelope.
MailKind = Literal["note", "ask", "steer", "reply"]
MAIL_KINDS = ("note", "ask", "steer", "reply")
# The kinds that pose a question and so carry a bound, may be closed by a reply, are left
# pending by an addressee's exit, and are never pruned while open. A `steer` is an `ask` for
# every rule in §4.10 (2026-09-19, TD-069): a `steer` differs only in that its
# bound ends in *lapsed* rather than *expired*, and runs whatever becomes of the addressee.
ASK_KINDS = ("ask", "steer")
# the kind folded into `ask` (§4.10 *Two controllers disagree*, TD-462): refused on a send, and an
# entry of it still retained is read as an `ask`
FOLDED_KINDS = {"conflict": "ask"}
PERSON = "person"  # `from` when a person sent the entry; never a session id
SYSTEM = "system"  # the third sender (design §4.10): the home saying what became of a session's own message
# How an entry closed (design §4.10 "One way of being closed"). `expired` is a session-to-session
# `ask` whose bound ran out or whose addressee was closed or forgotten; `lapsed` is a `steer`
# reaching its bound, where nothing failed.
CLOSED_REASONS = ("replied", "declined", "asker_gone", "lapsed", "go_with_it", "expired", "asked_person")

# Who owns which field of a record (design §4.4a, §9 invariant 15): the node observes and enforces
# on its host, the home holds the graph and intent, and identity is set once at create. Merges go
# by owner, never by last write, so a field belongs to exactly one set — `test_models` pins that
# every field of `Session` is in one and only one of the three.
NODE_OWNED = frozenset(
    {
        "identity_alarms",
        "state",
        "since",
        "pending",
        "confidence",
        "pane",
        "tail",
        "title",
        "last_output",
        "exit_code",
        "ended",
        "git",
        "model",
        "context",
        "subagents",
        "wrapup_sent_at",
        "gated",
        "wrapup_at",
        "doorbell_failed",
        "first_prompt",
        "first_prompt_sent_at",
        "first_prompt_tries",
        "first_prompt_error",
        "run_log",
        "previous_run",
        "supersedes",
        "closed_at",
    }
)
HOME_OWNED = frozenset(
    {
        "closer",
        "controllers",
        "capabilities",
        "team",
        "project",
        "role",
        "lane",
        "unattended",
        "run_until",
        "start_at",
        "supervised",
        "restarts",
        "restart_ceiling",
        "closed_for",
        "finished_sent_at",
        "restart_blocked",
        "restart_blocked_sent_at",
        "nudged_at",
        "context_sent_at",
        "brief",
        "brief_changed",
        "relaunch",
        "sit_out",
        "seat",
        "seat_due",
        "seat_count",
        "seat_filled",
        "seat_held",
        "idle_open",
        "checks",
        "conventions_seen",
        "held_missed",
        "review",
        "context_bound",
        "wrapup_prompt",
        "pause_prompt",
        "resume_prompt",
        "progress",
        "findings",
        "out_of_work",
        "lane_seen",
        "balance_refused",
        "restart_wanted",
        "suspended",
        "doing",
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
        "doorbell_held",
    }
)
IDENTITY = frozenset({
    "id", "name", "kind", "adapter", "dir", "profile", "repo", "worktree", "adapter_id", "created", "host",
    "start_context",
})  # fmt: skip

# Urgent-first order (design §4.5). Lower sorts first. `unreachable` is placed by the UI
# depending on whether the host is volatile, so it gets two slots.
STATE_RANK: dict[str, int] = {
    "needs-you": 0,
    "limited": 1,
    "stalled?": 2,
    "unreachable": 3,
    "working": 4,
    "idle": 5,
    "scheduled": 6,  # not started yet (§6 *Start time*): after what runs, before what has ended
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
    # Beside a *declared* claim, the one thing the agent adds (design §4.5a *Reports*, TD-150): the
    # open PR of the branch named for its reference, as the tick derived it — so the panel reads
    # *in review* for a claim whose session opened its PR without `--pr`. Derived, and never one
    # of the session's own fields: its status, `pr` and `why` stay as it declared them (§9
    # invariant 10). Cleared when that PR merges, since a merged PR holds no review.
    review_pr: int | None = None
    # Beside a *declared* claim too (§4.8, §4.9a *A slice is work done*, TD-325): each merged PR on
    # its reference that closed no entry, `{pr, at, source}`, one item per `pr` — declared by
    # `ao progress done <ref> --pr <n> --slice`, or derived by the tick from a merged PR the session
    # did not declare. Neither touches the claim's status, `pr` or `why` (§9 invariant 10).
    slices: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def add_slice(self, pr: int, source: Source) -> bool:
        """Hold merged PR `pr` as a slice of this claim, once per `pr` (TD-325). A slice the tick
        derived and the session then declares becomes declared, since the restart reading reads the
        session's word (§4.9a); a declared one is never made derived. True when that changed."""
        for item in self.slices:
            if item.get("pr") == pr:
                if source == "declared" and item.get("source") != "declared":
                    item["source"] = "declared"
                    return True
                return False
        self.slices.append({"pr": pr, "at": now_iso(), "source": source})
        return True

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


ATTENTION_KINDS = ("permission", "question", "needs", "stalled", "limited", "unpushed")


def attention_kind(s: Session) -> str:
    """Which **state row** this record is, or `""` for one that needs nobody (design §4.5a *Inbox
    row: state*, §4.10 *The Inbox is a queue*). The home needs this to know when a row **ends**,
    so that what became of it leaves a trail; the page has its own form of the same predicate over
    the view it renders (`agentorc.ui.app.state_kind`) — **a parity pair**: a kind added to one is
    added to the other, and `tests/test_attention.py` holds them to the same answers.

    `unpushed` is the one the two read differently and deliberately: the page adds *what Ready to
    close says*, which is the page's own checklist, while the home reads the record's own git
    facts. Both mean *exited with work that is not pushed*; the page's words are richer — and both
    read the **one measure**, `git.unpushed`, never the porcelain's `ahead` (design §4.2, TD-080):
    a launch branch tracking `origin/main` and pushed to its own ref is *ahead* for ever and is
    not stranded work (review of PR #269)."""
    pend = s.pending.to_dict() if s.pending else {}
    if s.state == "needs-you":
        if pend.get("kind") == "permission" and pend.get("tool_use_id"):
            return "permission"
        return "question" if pend.get("text") or pend.get("kind") else "needs"
    if s.state == "stalled?":
        return "stalled"
    if s.state == "limited":
        return "limited"
    git = s.git or {}
    if s.state == "exited" and (git.get("dirty") or git.get("unpushed")):
        return "unpushed"
    return ""


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
    root: str = ""  # the thread: the id of the first `ask` or `note` a chain replies to
    copies: list[str] = field(default_factory=list)  # the other controllers this landed with, expanded at send
    copies_failed: list[str] = field(default_factory=list)  # copies that could not land, dropped (§4.10)
    bound: str | None = None  # an `ask`'s expiry, wall-clock on the home's clock, running from `at` read or not
    closed_by: str | None = None  # the first `reply` that answered an `ask`; closes it uncounted
    closed_at: str | None = None  # when it did: retention for a closed `ask` runs from here
    expired_at: str | None = None  # the bound ran out, or an addressee was closed or forgotten
    closed_reason: str | None = None  # one of CLOSED_REASONS, written on every close path (§4.10, TD-069)
    pending: list[str] = field(default_factory=list)  # addressees that exited with the `ask` open (may resume)
    cites: list[str] = field(default_factory=list)  # an `ask` to two or more: the `sends` ids it cannot reconcile
    default: str | None = None  # a `steer`: the one line saying what the sender will do, required on it
    team: str | None = None  # the sender's `team` at send, stamped by the home (§4.10, 2026-09-19)
    snoozed_until: str | None = None  # a person-inbox entry the person set aside; the Inbox page only
    # A look handed to a reviewer (design §4.10 *A look*, §4.5a **Send to reviewer**, TD-292 slice 4):
    # `snoozed_for` is the id of the `handed` `ask` that carries it to its sender's team's techlead
    # seat, in place of a time — the look is set aside until that debt closes. `looked_by` is
    # `{seat, text}`, the seat's outcome line brought back to the look, drawn above its answers.
    # `look` is on the handed `ask` itself: the id of the look it carries.
    snoozed_for: str | None = None
    looked_by: dict[str, str] | None = None
    look: str | None = None
    paused_at: str | None = None  # a person-inbox `steer` whose clock the person stopped (§4.10 *Pause*)
    # Suggested answers (design §4.10 *Suggested answers*, 2026-09-20, TD-070). `answers` is the
    # sender's own likely answers on an `ask` or a `steer` — **data the sender
    # proposed, never instructions and never parsed from its text**, which is the only reason a
    # page may draw a control from them (TD-071 item 8). `answer` is the zero-based index of the
    # one a reply picked, so a sender branches on the number rather than comparing strings; a
    # typed reply carries None. Entries written before this date have neither and load with both
    # at their defaults, because `from_dict` keeps only the fields the class declares.
    answers: list[str] = field(default_factory=list)
    answer: int | None = None
    # A pull request the entry is about (design §4.9b *The reader*, TD-093): the PR number an
    # author's `ask` puts in front of its reader, an integer so a row can link it and a header can
    # count it — the text stays the author's summary. Only an `ask` carries one.
    pr: int | None = None
    # A reader's verdict (design §4.9c *The reader's answer carries a verdict*, TD-315 slice 1): on a
    # reply to an `ask` that carries `pr`, one of `VERDICTS` — a word on the envelope, never read out
    # of the text. Required of a session's such reply, refused on any other.
    verdict: str | None = None
    # A look's screenshots (design §4.10 *A look*, TD-292): repo-relative paths of `.png` files under
    # `docs/mockups/reviews/` of the sender's repo, in the order sent, at most `mail.SHOTS_MAX`. Only a
    # `steer` or an `ask` to the person carries them, and one that does is a look; drawn only as file
    # names and images, never parsed from the text.
    shots: list[str] = field(default_factory=list)
    # A `system` note whose wake is **uncharged** (§4.10: a lapse is the home's clock, not another
    # session's message). It is on the entry rather than in a set beside the records so that it
    # survives what the entry survives: a resume moves it with the note, and a host-agent restart
    # reloads it — the wake is decided when the session is next reachable, which may be after both.
    uncharged: bool = False
    # What became of the answer (design §4.10 *Outcomes*, 2026-09-20, TD-079): `{state, text, at,
    # by}` on a question the person answered, where `state` is `done`, `blocked` or `dropped` as
    # the asker reported it, `asked_again` when the asker put a follow-up on the thread, or
    # `asker_gone` when its record was closed or forgotten. `by` is the id of the reporting `note`
    # — an ordinary entry in its own right — and is empty on the two the home writes itself.
    # Written on every copy, as `read_at` and `closed_reason` are, so the asker's card and the
    # person's Inbox say the same thing.
    outcome: dict[str, Any] | None = None
    # Design §4.8a *An alarm's answers* (TD-077 b): a piece of work the **person** handed to this
    # session, which owes an outcome back exactly as the session's own answered question does.
    # TD-079's debt exists only on a session's *own* question to the person — it is read from the
    # asker's outbox — so work that travels the other way had no debt at all. `identity_log` is
    # its first and only writer; nothing else sets it until a design says so.
    handed: bool = False
    # An entry handed to a seat (design §4.10 *An entry handed to a seat*, TD-180, TD-218): the
    # Add entry form's `{repo, type}` — the repo's name in the home's registry and `debt` or
    # `feature` — on a `handed` `ask` from the person. From the form's fields, never read out of
    # the text.
    entry: dict[str, str] | None = None
    # Passed up (design §4.9b *Passing up keeps the thread and the asker*, TD-075 step 3): when the
    # addressee of an open `ask` or `steer` handed it to the person — once — with its own
    # recommendation. `passed_up` is the time, written on every copy; `recommend` is `{by, text}`,
    # the passer's one line, drawn as text and labelled as the passer's, never the asker's. The
    # person-inbox copy carries the passer's suggested answers as its `answers`, the recommendation
    # first; the asker's and the passer's copies keep their own.
    passed_up: str | None = None
    recommend: dict[str, str] | None = None
    # Answered from the record (design §4.9b, TD-075 step 2). `source` is on a **reply**: one line
    # saying where its answer is written down (a file and section, a dated decision), at most
    # `mail.SOURCE_CAP` characters, only ever drawn as text. `answered` is on the **FYI** the home
    # files to the person for such a reply — `{question, asker, answerer, source}` — and a reply to
    # an entry carrying it goes to the asker with a copy to the answerer (the Overrule path). Both
    # key on these fields, never on the role of whoever answered (§9 invariant 9).
    source: str | None = None
    answered: dict[str, str] | None = None
    # Orphaned (design §4.10 *A question about a reference outlives its asker*, TD-213/TD-215): an
    # open `ask` or `steer` to the person whose `about` names a reference, left standing when its
    # asker's record was closed, forgotten or cancelled. `{at, how, ref, name, repo, host, team}` —
    # `how` one of `closed`, `forgotten`, `cancelled`, `ref` the canonical reference, the rest
    # copied from the record then, since a forgotten record can be asked nothing afterwards. A
    # create under the id `from` names clears it (`adopted_at` then keeps that it ever was).
    orphaned: dict[str, str] | None = None
    adopted_at: str | None = None

    def __post_init__(self) -> None:
        self.root = self.root or self.id  # a message replying to nothing is its own thread's root

    @property
    def owes(self) -> bool:
        """Design §4.10 *Outcomes*: a question **to the person** that the person **answered** owes
        an outcome back, until one is reported or the home writes one. Read from the entry alone,
        so the asker's own copy in its outbox answers it — which is what the card, the `ao` reply
        line, `ao progress none` and Ready to close all read. A `declined` or `lapsed` close owes
        nothing: nobody answered.

        **And the other direction** (§4.8a *An alarm's answers*, TD-077 b): an entry **from** the
        person marked `handed` is a debt on its **addressee**, settled the same way and by the
        same command. Until it there was no such thing — a debt existed only on a session's own
        question — so a piece of work the person handed a session could be dropped in silence.
        That branch reads **only** `from_`, `handed` and `outcome`: today `identity_log` is the
        one writer and it always sends a `note`, which can never open or close, so `kind` and
        `closed_reason` have nothing to say. A future writer that hands an `ask` would have to
        settle what an unanswered one owes before setting the mark (review of PR #318)."""
        if self.handed:
            return self.from_ == PERSON and not self.outcome
        return (
            (PERSON in self.to or bool(self.passed_up))  # a question passed up is the person's to answer (§4.9b)
            and self.kind in ASK_KINDS
            and self.closed_reason in ("replied", "go_with_it")
            and not self.outcome
        )

    def owes_for(self, *, session_inbox: bool) -> bool:
        """`owes`, asked of one copy where it is held. A copy in a **session's inbox** owes only
        when it is `handed` — work the person handed that session. Every other debt belongs to the
        asker, on its outbox copy, and to the person inbox's copy it is listed under. Without this,
        a question passed up (§4.9b) would owe on the passer's copy too, and on any copy recipient's,
        and those could then neither be deleted nor pruned (review of PR #347). `Session.owed()`,
        `inbox_delete` and the retention sweep all ask it this way."""
        return self.owes and (self.handed or not session_inbox)

    @property
    def handed_entry(self) -> bool:
        """A piece of work the person handed as an `ask` (§4.10 *An entry handed to a seat*): it
        fills the seat, carries no bound, and closes by its outcome, never by a reply."""
        return self.handed and self.kind == "ask" and self.from_ == PERSON

    @property
    def open(self) -> bool:
        """Design §4.10 "One way of being closed": an entry is open exactly when it is an `ask`
        or a `steer` with no `closed_reason` — which is what *never pruned while open*,
        the person inbox's depths and the FYI list all read. Entries written before 2026-09-19
        carry no `closed_reason` and read as closed when `closed_by` or `expired_at` is set, which
        is the rule until then."""
        if self.kind not in ASK_KINDS:
            return False
        if self.closed_reason:
            return False
        return not (self.closed_by or self.expired_at)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["from"] = d.pop("from_")
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MailEntry:
        d = dict(d)
        d["from_"] = d.pop("from", d.pop("from_", ""))
        d["kind"] = FOLDED_KINDS.get(d.get("kind"), d.get("kind"))
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
    an `ask` between controllers can cite two of them by id instead of guessing at the keystrokes."""

    id: str  # `s-<hex>`
    from_: str  # the caller's session id, or PERSON
    at: str
    text: str
    verdict: str = "submitted"  # or the submit error (`prompt-stuck`, …): the paste still reached the pane
    scraped: bool = False  # the brief sent on a scraped `idle`, no hook having reported (§4.1, TD-348)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["from"] = d.pop("from_")
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SendEntry:
        d = dict(d)
        d["from_"] = d.pop("from", d.pop("from_", ""))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


REVIEW_READERS = ("techlead", "person")
# What a reader's reply to a PR's `ask` came to (design §4.9c, TD-315): nothing against it on my paths
# and not merged; I am the last reader and merged it; the text says what, and the author asks again.
VERDICTS = ("pass", "merged", "findings")
REVIEW_BOUND = "2h"  # §4.9b *The reader*: a read of a diff, unless the preset says otherwise
# The one wrap-up text (design §4.5a **Wrap up**, §4.9 `ao team stop`, §4.9c a sit-out): the card, the CLI
# (`agentorc.teams` re-exports it) and the home's sit-out send the same words.
WRAPUP_PROMPT = (
    "agentorc: this session is being wrapped up. Stop starting new work now. Commit and push whatever "
    "is in flight, make sure the ledger and user_attention.md reflect any undone steps (ledger before "
    "idle), then stop."
)
_REVIEW_DURATION = re.compile(r"[1-9]\d*[mhd]")


def normalize_review(review: Any, *, chain: bool = True) -> dict[str, Any] | None:
    """A role preset's `review:` (design §4.9b *The reader*, TD-093) as the record keeps it:
    `{reader, held, bound}` — `reader` is `techlead` or `person`, `held` a list of path globs
    defaulting to every PR (`**`), `bound` a duration written as a seat's `every:` is (`90m`,
    `2h`), default two hours. A `ValueError` rather than a guess: a malformed setting would hold
    nothing and say nothing. The one check, read by the config loader and the host agent alike.

    Or a flow's chain (§4.9c *A review stage any seat may hold*, TD-315 slice 1): `{chain: [{stage,
    reader, held}, …], bound}`, one link per review stage in the flow's order, `reader` the seat's name
    as the definition gives it (that it names a seat of the team is the client's check, at the
    compile), `held` that stage's globs. The older shape means a chain of one (`review_links`). A chain
    is the compile's to write, never a role's `review:` in a file: `chain=False` is the config loader's."""
    if review is None:
        return None
    if not isinstance(review, dict):
        raise ValueError(f"review: a mapping of reader, held and bound, not {review!r}")
    if "chain" in review:
        if not chain:
            raise ValueError(
                "review: a role's review takes reader, held and bound; a chain is written by a flow's compile"
            )
        return _normalize_chain(review)
    unknown = sorted(str(k) for k in set(review) - {"reader", "held", "bound"})
    if unknown:
        raise ValueError(f"review: unknown key(s) {', '.join(unknown)}; it takes reader, held and bound")
    reader = str(review.get("reader") or "")
    if reader not in REVIEW_READERS:
        raise ValueError(f"review: reader is one of {', '.join(REVIEW_READERS)}, not {reader or 'nothing'!r}")
    return {
        "reader": reader,
        "held": _review_globs(review.get("held", ["**"]), "review"),
        "bound": _review_bound(review),
    }


def _review_globs(held: Any, where: str) -> list[str]:
    if isinstance(held, str):
        held = [held]
    if not isinstance(held, list) or not held or not all(isinstance(g, str) and g.strip() for g in held):
        raise ValueError(f"{where}: held is a list of path globs, not {held!r}")
    return [g.strip() for g in held]


def _review_bound(review: dict[str, Any]) -> str:
    bound = str(review.get("bound") or REVIEW_BOUND).strip()
    if not _REVIEW_DURATION.fullmatch(bound):
        raise ValueError(f"review: bound is a duration such as 90m or 2h, not {bound!r}")
    return bound


def _normalize_chain(review: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(str(k) for k in set(review) - {"chain", "bound"})
    if unknown:
        raise ValueError(f"review: unknown key(s) {', '.join(unknown)}; a chain takes chain and bound")
    chain = review.get("chain")
    if not isinstance(chain, list) or not chain:
        raise ValueError(f"review: chain is a list of links {{stage, reader, held}}, not {chain!r}")
    links: list[dict[str, Any]] = []
    for link in chain:
        if not isinstance(link, dict):
            raise ValueError(f"review: a chain's link is a mapping of stage, reader and held, not {link!r}")
        stray = sorted(str(k) for k in set(link) - {"stage", "reader", "held"})
        if stray:
            raise ValueError(f"review: unknown key(s) {', '.join(stray)} on a link; it takes stage, reader and held")
        stage, reader = str(link.get("stage") or "").strip(), str(link.get("reader") or "").strip()
        if not stage or not reader:
            raise ValueError(f"review: a chain's link names its stage and its reader, not {link!r}")
        if any(x["stage"] == stage for x in links):
            raise ValueError(f"review: the chain names stage {stage!r} twice")
        links.append({"stage": stage, "reader": reader, "held": _review_globs(link.get("held", ["**"]), "review")})
    return {"chain": links, "bound": _review_bound(review)}


def review_links(review: Any) -> list[dict[str, Any]]:
    """A record's `review` as its links, `[{stage, reader, held}]` in the order they read (§4.9c): a
    chain as it stands, the older `{reader, held}` as a chain of one (its stage `review`), none for none."""
    if not isinstance(review, dict):
        return []
    if isinstance(review.get("chain"), list):
        return [x for x in review["chain"] if isinstance(x, dict)]
    if not review.get("reader"):
        return []
    return [{"stage": "review", "reader": str(review["reader"]), "held": list(review.get("held") or ["**"])}]


_TOKENS = re.compile(r"(\d+(?:\.\d+)?)\s*([kKmM]?)")


def normalize_context(context: Any) -> int | None:
    """A role preset's `context:` (design §4.8 *A role has a context bound*, TD-190) as the record
    keeps it: the bound in tokens, `context_bound`. `{bound: 200k}` — a count written as the reading
    is (`200k`, `1M`, `1.5M`) or a plain integer; `none` (or null) is no bound, which is how a layer
    takes a built-in's away. A `ValueError` rather than a guess, as `normalize_review`'s is."""
    if context is None or context == "none":
        return None
    if not isinstance(context, dict):
        raise ValueError(f"context: a mapping {{bound: 200k}}, or none, not {context!r}")
    unknown = sorted(str(k) for k in set(context) - {"bound"})
    if unknown:
        raise ValueError(f"context: unknown key(s) {', '.join(unknown)}; it takes bound")
    bound = context.get("bound")
    if bound is None or bound == "none":
        return None
    if isinstance(bound, bool):
        bound = str(bound)
    if isinstance(bound, int):
        n = bound
    else:
        m = _TOKENS.fullmatch(str(bound).strip())
        if not m:
            raise ValueError(f"context: bound is a token count such as 200k or 1M, not {bound!r}")
        n = int(float(m.group(1)) * {"": 1, "k": 1_000, "m": 1_000_000}[m.group(2).lower()])
    if n <= 0:
        raise ValueError(f"context: bound is a token count above zero, not {bound!r}")
    return n


def reference_of(about: str | None) -> str | None:
    """The canonical reference `about` names, or None when it has neither machine-readable shape
    (design §4.10 *A question about a reference outlives its asker*): `normalize_ref` passes any
    other text through, so the test is the shape, not the call. A session's id or prose is None."""
    r = " ".join(str(about or "").split())
    if re.fullmatch(r"(?i)[a-z]{2,6}-\d{1,4}", r) or re.fullmatch(r"#?\d{1,6}", r):
        return normalize_ref(r)
    return None


# A lane's words that are no reference (design §4.8, §6 rule 6): `free-pick` and `design-first` name
# what a member may pick, and `owner:<word>` narrows it (TD-214, TD-227). None is counted, nudged
# about or offered to `ao progress` as something held. `anchor` is the anchor seat's (§4.9b, TD-381).
LANE_WORDS = ("free-pick", "design-first", "anchor")
OWNER_WORD = "owner:"


def owner_word(word: Any) -> str | None:
    """The owner an `owner:<word>` lane word names, lower-cased, or None for any other word."""
    w = str(word).strip()
    if w[: len(OWNER_WORD)].lower() != OWNER_WORD:
        return None
    return w[len(OWNER_WORD) :].strip().lower() or None


def lane_refs(lane: Iterable[Any]) -> list[str]:
    """The references of a lane, in its order: its lane words and owner words left out."""
    return [str(r) for r in lane if str(r) not in LANE_WORDS and owner_word(r) is None]


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


# What counts as something worth waking a manager for (design §4.8 "Waking a manager", TD-049). The
# vocabulary is deliberately short, and the exclusions are the point: `last_output`, `tail`,
# `since`, `seen_at`, `git`, `subagents`, `model` and `context` move on almost every tick of a healthy
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
    # the third ending (§4.9a *A run that ends with work left*, TD-083): a lead acts on it — it
    # closes the member and starts it again — so it is exactly the kind of change a wait is for
    rw = session.get("restart_wanted") or {}
    parts.append(f"restart_wanted={(rw.get('at'), rw.get('why'), rw.get('early'))!r}")
    # a question landing on an empty techlead seat (§4.9b, TD-075 step 4): the manager fills it, so
    # a manager blocked in `ao wait` returns on it — whether any wait, never how many, so it wakes
    # on the count leaving zero and not on 1→2 (and never the text)
    parts.append(f"asks_waiting={bool(session.get('asks_waiting'))!r}")
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


def start_note(session: Mapping[str, Any]) -> str:
    """ "starts 20:00" (or *starts Mon 20:00*) for a `scheduled` record's card, Focus header and
    `ao status -v` (design §6 *Start time*, §4.5a **starts** note, TD-152) — the *stops* formatter's
    clock, so the two notes read alike; empty on any record that is not scheduled."""
    when = session.get("start_at")
    if session.get("state") != "scheduled" or not when:
        return ""
    try:
        at = datetime.fromisoformat(str(when).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return ""  # one card's note, never the grid (see `stop_note`)
    day = "" if at.date() == datetime.now().astimezone().date() else at.strftime("%a ")
    return f"starts {day}{at:%H:%M}"


PR_MARKS = ("merged", "closed")  # the words a PR that is no longer open carries (§4.5a **the PR's mark**)


def pr_marks(session: Mapping[str, Any], repos: Mapping[str, Any] | None) -> dict[int, str]:
    """The PRs of the record's repo that are no longer open, by number, as *merged* or *closed* — what
    **the PR's mark** reads (§4.5a card **report line**, TD-193). `repos` is the home's repo facts
    (§4.4 *Repo facts*, the `repos` read: a registered root to its reading, whose `prs` hold the open
    list and the month's `recent`); the record's `repo` — a worktree's main checkout — picks one. A
    PR the readings do not hold is absent, so it is never guessed; no readings, no marks."""
    if not repos or not session.get("repo"):
        return {}
    want = str(Path(str(session["repo"])).resolve())
    r = next(
        (r for root, r in repos.items() if isinstance(r, dict) and str(Path(str(root)).resolve()) == want),
        None,
    )
    prs = (r or {}).get("prs") or {}
    return {
        p["number"]: str(p.get("state"))
        for p in [*(prs.get("recent") or []), *(prs.get("open") or [])]
        if isinstance(p, dict) and isinstance(p.get("number"), int) and p.get("state") in PR_MARKS
    }


def report_ref(session: dict[str, Any], prs: Mapping[int, str] | None = None) -> str:
    """The reference half of the report line — `TD-066 → #158`, `#359`, `TD-027~` — with **the PR's
    mark** (design §4.5a card **report line**, TD-182, built by TD-193): one word after the PR's
    number, *merged* or *closed*, when `prs` (the readings' PRs of the record's repo, by number, as
    their state) holds it so. An open PR, one the readings do not hold, and `prs` None leave it
    unmarked — never guessed. Display only: the record is not touched. Empty with no entry."""
    head = report_head(session)
    if not head:
        return ""
    pr = head.get("pr")
    mark = f" {prs[pr]}" if pr and prs and prs.get(pr) in PR_MARKS else ""
    ref = head["ref"] + ("~" if head.get("source", "declared") != "declared" else "")
    if pr and head["ref"] == f"#{pr}":
        return ref + mark
    return ref + (f" → #{pr}{mark}" if pr else "")


def tokens_short(n: int) -> str:
    """A token count as a reading says it: `231k`, `1M`, `1.2M`; under a thousand, as it is."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".removesuffix(".0") + "M"
    if n >= 1_000:
        return f"{round(n / 1_000)}k"
    return str(n)


def context_reading(session: dict[str, Any], *, of_window: bool = True) -> str:
    """The record's context reading (design §4.5 row 4, §4.7 `ao status -v`, TD-190): *231k of
    1M*, or *231k* where the window is unknown or not asked for; "" where there is none, which is
    an adapter that cannot tell and never an error. One text for the card, Focus and the CLI."""
    ctx = session.get("context")
    if not isinstance(ctx, dict):
        return ""
    try:
        tokens = int(ctx.get("tokens") or 0)
        window = int(ctx.get("window") or 0)
    except (TypeError, ValueError):
        return ""
    if tokens <= 0:
        return ""
    return tokens_short(tokens) + (f" of {tokens_short(window)}" if of_window and window > 0 else "")


def context_over_text(session: dict[str, Any]) -> str:
    """*context 231k over the 200k bound* while the record's reading is past its role's bound (§6
    rule 5, TD-190): the text rule 5's line and the clause on every `ao` reply both carry; "" when
    it is not over."""
    if not context_over(session):
        return ""
    tokens = int(session["context"].get("tokens") or 0)
    return f"context {tokens_short(tokens)} over the {tokens_short(session['context_bound'])} bound"


def context_over(session: dict[str, Any]) -> bool:
    """Whether the record's context reading is past its role's bound (design §4.8, §6 rule 5,
    TD-190): what draws the card's reading red. False without a reading or without a bound."""
    ctx, bound = session.get("context"), session.get("context_bound")
    if not isinstance(ctx, dict) or not isinstance(bound, int) or isinstance(bound, bool) or bound <= 0:
        return False
    try:
        return int(ctx.get("tokens") or 0) > bound
    except (TypeError, ValueError):
        return False


def report_line(session: dict[str, Any], prs: Mapping[int, str] | None = None) -> str:
    """The one-line report a card or `ao status -v` shows (design §4.8): the reference in hand, the
    PR it is on, and the lane count — `TD-027 → #60 · 1/2 done`. A reference whose entry the agent
    derived rather than the session declared carries `~`, as a scraped state does (§9 invariant 10).
    A reference is shown once: where the entry's reference is the PR itself, `#359 · 1/2 done`,
    never `#359 → #359` (§4.5a card **report line**, TD-095). A PR that is no longer open carries
    its mark when `prs` says so, `TD-066 → #158 merged · 3/3 done` (`report_ref`, TD-193). Empty
    when the session has neither a lane nor a single entry."""
    progress = session.get("progress") or []
    lane = lane_refs(session.get("lane") or [])
    done = [p for p in progress if p.get("status") == "done"]
    bits = []
    if ref := report_ref(session, prs):
        bits.append(ref)
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
    # The session's name as its tool holds it (design §4.5a **title**, §4.3 `title()`, TD-074):
    # observed from the pane on the host where the pane lives, exactly as `tail` is — the tool
    # writes its terminal title, tmux holds it as `#{pane_title}`, and the session's adapter says
    # what of it is a name. Display only: agentorc has no rename of its own, so nothing here ever
    # writes it. None when the adapter has no opinion or there is no name to show; it changes
    # rarely and no wake fires on it (it is out of `wake_digest`).
    title: str | None = None
    # design §4.8a: requests on this host's socket whose claim disagreed with their channel —
    # `{channel, claimed, rpc, count, at, last}`, coalesced, the newest `identity.ALARMS_KEEP`. Observed
    # where the socket is, so the node's to write, like `tail`.
    identity_alarms: list[dict[str, Any]] = field(default_factory=list)
    exit_code: int | None = None
    # How the run ended (design §4.2 the `SessionEnd` row, §4.5 row 5 (b), TD-490): `{how, at, code?,
    # reason?, by?, why?, found?, down_since?}` — `how` is `tool` (the tool's own end, its `reason`),
    # `pane` (a dead pane, its status as `code`), `gone` (the tick found no tmux session: `found`,
    # and `down_since` when that was the agent's first tick after a start) or `kill` (`by` who
    # pressed it, as `closer` reads a caller). Observed where the pane is, so the node's; dropped
    # when the record leaves `exited` for a live state.
    ended: dict[str, Any] | None = None
    # A tmux pane (live or dead) still backs this record. False after `kill`/`close` (the session is
    # destroyed) or when the tick finds no pane; a natural exit keeps its dead pane (TD-023).
    pane: bool = True
    subagents: int = 0  # live subagents (SubagentStart − SubagentStop); Ready to close needs zero
    last_output: str | None = None  # ISO time the run log last grew (liveness cross-check)
    run_log: str | None = None
    # The run log of the exited session of the same name this one replaced (design §4.1, TD-030):
    # the name is reused, so the previous run stays reachable from the record that took it over.
    previous_run: str | None = None
    # What this session's create replaced or continued (design §4.4a, TD-057): `{id, mail, at}` per
    # record — the one of its name it replaced in place, the exited one whose conversation it
    # resumed — `mail` when that record's mailbox is this one's now (a resume, `--keep-mail`), `at`
    # this session's start. Written on the host the session runs on, which is where the name rule
    # ran; the home holds the mailbox, so it reads this once from a node's report and does the
    # same to its own copies (`HostAgent._take_supersession`).
    supersedes: list[dict[str, Any]] = field(default_factory=list)
    closed_at: str | None = None
    # Who closed it (design §4.5 row 5 (b), TD-262/TD-265): `{by, why, at}` — `by` is `person`, a
    # session's id (the caller the acting gate verified), or `tick` with `why` one of `finished`,
    # `wanted`, `brief`, `seat`. The home's (§4.4a): it writes its copy of a node's record as it
    # routes the close, and hands the word to the node in the close's params so the node's record
    # holds it too; absent on a record closed before this field existed.
    closer: dict[str, Any] | None = None
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
    # `{tokens, at, window}`: how much context the session's last turn sent, when the adapter can
    # tell (design §4.3 `context`, §6 rule 5, TD-190) — `at` the turn's own time, `window` the
    # model's, kept beside the tokens because the model may change mid-run. Observed like `model`:
    # read on the tick for unattended records, and nothing but a display reads it yet.
    context: dict[str, Any] | None = None
    # The reading past which §6 rule 5 tells a supervised member to end its run, in tokens (design
    # §4.8 *A role has a context bound*, TD-190): from its role preset's `context: {bound}`, written
    # at start as `review` is; None is no bound. The home's, like the rest of the role's intent.
    context_bound: int | None = None
    # Text the session holds from its start that is no prompt (design §4.3 `start_context`, TD-283):
    # no turn ran for it and nothing typed it. Set once at create and handed to every launch of the
    # conversation — a resume carries it whether or not it says so — so a page can show what the
    # session was told beside the person's own words (§4.5a **Told at start**). None: there is none.
    start_context: str | None = None
    # The brief, typed and never passed (design §4.1 *No prose in the argv*, TD-339): the first prompt
    # the adapter handed back (`LaunchSpec.first_prompt`) rather than put in the argv, kept until the
    # host agent has typed it at the record's first hook-reported `idle` (`first_prompt_sent_at`), or a
    # `UserPromptSubmit` says a prompt went in. `first_prompt_tries` counts the sends the composer did
    # not take; at `FIRST_PROMPT_TRIES` the last one's reason is `first_prompt_error`, the card's
    # *brief not sent* mark, which the next prompt clears. The pane's host writes all four.
    first_prompt: str | None = None
    first_prompt_sent_at: str | None = None
    first_prompt_tries: int = 0
    first_prompt_error: str | None = None
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
    # `{at, ids}` while `out_of_work` stands: the ledger entries matching the session's lane that it
    # has been told of — at first the ones its repo's reading held when the tick first saw the
    # declaration, then each new one as a `system` note names it (design §6 rule 6, TD-195), so an
    # entry is told once — and `dropped: {id: at}`, the instant of the last drop of a seen id by
    # another record of the repo that was told (TD-258), so a released lease is told once too. The
    # home's; cleared wherever `out_of_work` is set or cleared.
    lane_seen: dict[str, Any] | None = None
    # `{at, ref}` once a claim of this record's was refused because its team is over its line
    # (design §6 *Balance*, TD-239) — `ref` None for a refused `ao progress none` — so the mark's
    # clearing can ring it; while the mark stands, the idle nudge and rule 6's telling pass it by.
    # The home's; a later claim that is taken removes it.
    balance_refused: dict[str, Any] | None = None
    # `{at, why, early?, repeat?}` once the session has declared that **its run is over and its lane is
    # not** (`ao progress restart --why`, design §4.9a *A run that ends with work left*, TD-083):
    # start me again, under this name and this brief, with nothing of this conversation. A fact,
    # not a state — the record still reads `idle` or `exited` — written only by the session it is
    # about (§9 invariant 14) and cleared by a later declared claim, which means it went on after
    # all. `early` marks one declared inside `RESTART_EARLY` of the record's own start by a run that
    # reported nothing new, and `repeat: {ref}` beside it one whose `done` an earlier run reported
    # or that leaves a claim a third time (`agent_common._restart_reading`, TD-249): the word
    # stands, and a controller does not act on it. It and `out_of_work` refuse each other: a
    # session is out of work or it wants another run at it, never both.
    restart_wanted: dict[str, Any] | None = None
    # `{at, by, why}` once a **person** has suspended this session over an identity alarm
    # (design §4.8a *An alarm's answers*, TD-077 a2), `None` otherwise. **The home's field**
    # (§9 invariant 15 — intent, like `controllers`): a node's record is marked at the home and
    # only its `kill` is routed, because a `create` is gated at the home and that is where the
    # mark is read. It refuses every session's `create` under that name and `create --resume` of
    # that conversation — the one exception to §4.1's rule that an exited holder is superseded —
    # and it is lifted only by a person: their own resume of the conversation, or Forget.
    suspended: dict[str, str] | None = None
    # `{text, at}`: the session's own word for what it is doing now (`ao doing`, design §4.8, the
    # third report channel, 2026-09-19). A value, not a log — the last line replaces the one before,
    # as `out_of_work` does — and only the session itself writes it (§9 invariant 14). Never
    # derived, nothing keys on it, no wake fires on it (so it is out of `wake_digest`), and an exit
    # leaves it in place, as the last thing the session said.
    doing: dict[str, str] | None = None
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
    # When a `scheduled` record starts (design §6 *Start time*, TD-152): an absolute UTC instant, the
    # stop time's twin. Set at create with `start_at`, moved by `set_start`; the tick replays the
    # launch record at it (`restarts: [{why: start}]`). None on every record that is not scheduled.
    start_at: str | None = None
    # The usage gate (design §6, TD-100): how to ask this session to pause when its profile crosses
    # a line, and to carry on when every window is back under — wording from the client, as the
    # wrap-up's is. `gated` is the mark, `{profile, label, pct, line, since, next, resets, sent_at}`: written
    # the tick the line is crossed, `sent_at` once the pause prompt landed, gone with the resume.
    # The node's (§4.4a: an enforcement it made from usage it fetched); the prompts are the home's.
    pause_prompt: str | None = None
    resume_prompt: str | None = None
    gated: dict[str, Any] | None = None
    # *Someone chose to keep this session running* (design §6 *Keeping a team running*, TD-103):
    # set by `ao team start` on every session it creates and by `ao new --supervised`, carried by
    # every resume of the record, cleared by nothing but Forget. The home's intent (§4.4a). With
    # it, every create writes the session's launch record (`launch/<id>.json`), which is what a
    # restart replays; the policies that act on it key on it and on `unattended` together.
    supervised: bool = False
    # What the tick's restarts made of it (design §6 *Keeping a team running* rule 1, TD-103 slice 2):
    # `restarts` is every restart of this session, `[{at, why, done, left, error?}]` — `why` is `crash`,
    # `wanted` (rule 2) or `fill` (rule 3), `done: [{ref, pr}]` and `left: [ref]` what the run it
    # replaced reported `done` since its `created` and what it claimed and did not close (§4.9a,
    # TD-249: the new record keeps none of the old one's `progress`), and `error` the text of a
    # replay that failed, which counts all the same, and `said` a `wanted` one's own why (rule 9's
    # note, TD-468) — carried across the tick's own supersede so the count survives the restart it
    # counts, and empty on any other create (a
    # person's Resume starts it again; a person's Restart leaves the one entry `{at, why: person}`,
    # which never counts — §6 rule 2, TD-250). `restart_ceiling` is `{at, count}` once `RESTART_CEILING` is
    # reached: the tick stops and the session is a person's. Both the home's (§4.4a).
    restarts: list[dict[str, Any]] = field(default_factory=list)
    restart_ceiling: dict[str, Any] | None = None
    # The tick's mark after its own close for a restart, `{why, closed_at}`: the rule (`wanted`, rule 2's,
    # or `brief`, rule 7's) and the `closed_at` that close wrote. Any close the home runs clears it, and
    # a close it never hears of as an act (a person's at the node) writes a new `closed_at` that no
    # longer matches: what tells the tick's failed restart from a person's Close (§6 rule 2, TD-237,
    # TD-238). The home's, so an older node is sent nothing new.
    closed_for: dict[str, Any] | None = None
    # Rule 9's (§6, TD-241): when the home typed the fixed *your team is finished* line at this
    # record, the team's manager; from then its close waits `WRAPUP_GRACE`. The home's.
    finished_sent_at: str | None = None
    # Rule 2, the wanted restart (§6, TD-103 slice 4): a `restart_wanted` with work left is not
    # restarted. `restart_blocked_sent_at` is when the one fixed send naming what is left was typed,
    # and `restart_blocked` is `{at, dirty, unpushed}` once the git fields still show work
    # `IDLE_NUDGE` later — transient: the restart that runs once the work is pushed clears it.
    # `nudged_at` is rule 4's: when the idle nudge was typed, once per idle stretch. All the home's.
    restart_blocked: dict[str, Any] | None = None
    restart_blocked_sent_at: str | None = None
    nudged_at: str | None = None
    # Rule 5's (§6, TD-190): when the context-bound line was last typed; again after `CONTEXT_AGAIN`
    # while the member is still idle and over. The home's, as `nudged_at` is.
    context_sent_at: str | None = None
    # Rule 7's (§6, TD-217): `{at, sources: [{path, sha}]}`, the files the prompt was made from as
    # they were read at this create — the working tree's at a client's start, as merged at a replay —
    # each with its git blob id. None for a prompt typed whole. The home's.
    brief: dict[str, Any] | None = None
    # Rule 7's mark (§6, TD-217 slice 3): `{at, paths}` once a source of `brief` has read otherwise,
    # as merged, for `BRIEF_SETTLE`; gone at the next create, or when the files read as recorded
    # again. The home's.
    brief_changed: dict[str, Any] | None = None
    # Rule 7's second trigger (§4.9c *Switching*, TD-309 slice 5): `{at, lane, review}` once a person's
    # Apply replaced this record's launch record (`relaunch`), the two as handed (TD-355: what the member
    # runs next, which the record's own fields are not until then); gone at the next create, which
    # writes a launch record of its own. The home's.
    relaunch: dict[str, Any] | None = None
    # A sit-out under way (§4.9c *Switching*, TD-309 slice 5b): `{at}` once a person's Apply sent the
    # wrap-up to a member the team's flow no longer uses; the tick closes it once settled and writes
    # `closed_for: {why: sit_out}`. Gone at the next create. The home's.
    sit_out: dict[str, Any] | None = None
    # A seat of its team (§4.9b), `{trigger, after?}` as the definition gives it, written by `ao team
    # start` at create: a seat's ending is its own, so the crash restart never acts on one (§6 rule 1,
    # and rule 3 — the seat policy, TD-103 slice 3 — is what fills one). The home's.
    seat: dict[str, Any] | None = None
    # What the tick makes of a seat's trigger (§6 rule 3, TD-103 slice 3). `seat_due` is `{at, by}`,
    # set only once the trigger is met — `by` is what met it: `asks` (a question waiting, cleared
    # again if none is by the fill), `prs` (n merged to the seat's repo since this record was
    # created) or `every` (that long since it was created) — and it is what fills the seat; the
    # fill's new record starts without it. `seat_count` is the count toward a `prs:` trigger as last
    # read, `{prs, at}`, so a card can say how far along it is; a `gh` outage leaves the last
    # reading and never reads as zero. Both the home's, computed at the home.
    seat_due: dict[str, Any] | None = None
    seat_count: dict[str, Any] | None = None
    # A manager on call's memory of what it was filled for (§6 rule 3 *A manager on call is a seat
    # of this rule*, TD-259): `[{by, member, at}]`, `member` the address of the member the reading
    # was of and, for `asks`, `ask` the question's id in its place — one entry per cause a fill was
    # made for, carried across the fill and dropped when the cause has gone, so a cause still
    # standing fills once. The home's.
    seat_filled: list[dict[str, Any]] = field(default_factory=list)
    # Why a due anchor seat's fill waits (§6 rule 3 *The anchor seat*, TD-386): `{by, why}` — `by`
    # the session holding its checkout, or `checkout` for the tree itself — and `why` the words the
    # card's slot draws (*held by ao-x (working)*, *branch td-x, 2 files uncommitted*). Written when
    # the fill is refused, the next tick tries again, and cleared by the fill or once nothing is due.
    # The home's.
    seat_held: dict[str, Any] | None = None
    # `{at, ref}` on a supervised member, not a seat, still hook-confirmed idle `IDLE_NUDGE` after
    # rule 4's nudge with work open (§6 rule 3, TD-259): *idle · open work*, the one reading the
    # card's slot, a manager on call's `open` trigger and the person-led team's Inbox row draw.
    # `ref` is the first open reference when it was written. The home's; cleared when the stretch
    # ends, the work closes or the member declares.
    idle_open: dict[str, Any] | None = None
    # What the home read of this member's PRs with `scripts/check_cadence.py` (design §6 rule 10,
    # TD-258): one entry per PR, `{pr, at, sha, verdict, failed}` — the head it was read at, the
    # script's verdict and the `rule` of each failed row — with `merged` once the PR is, `told`
    # when the member was told of a fail, `row` while the Inbox row stands, and `read_by` once a
    # reader's reply on the PR's `ask` was seen. The home's: the tick writes it, and Dismiss
    # (`clear_mark`) takes `row` off.
    checks: list[dict[str, Any]] = field(default_factory=list)
    # The entries of `docs/cadence-changes.md` this member has been told of or started with (design
    # §6 rule 12, TD-258): `{at, headings}` — written at the first reading after the create as the
    # headings landed at or before `created`, a later one landed after it joining as its `system`
    # note is sent. A restart's record starts without it. The home's, written by the tick alone.
    conventions_seen: dict[str, Any] | None = None
    # The held PRs of this member that merged without their reader's reply (design §6 rule 11,
    # TD-258): one entry per crossing, `{pr, at, paths}` — the held paths it touched — with `told`
    # once the member was and `dismissed` once the person dismissed its row. Two entries not dismissed
    # are the Inbox row. The home's: the tick writes it, and Dismiss (`clear_mark`) marks it.
    held_missed: list[dict[str, Any]] = field(default_factory=list)
    # Who reads this session's PRs before they merge (design §4.9b *The reader*, TD-093):
    # `{reader, held, bound}` from its role preset's `review:`, written at start. The home stores
    # it and times nothing; the author's own `ao` reads it to decide whether a PR is held.
    review: dict[str, Any] | None = None
    # The other wrap-up (design §4.10 "A pending stop beats mail", TD-052 step 7): when a `send`
    # marked `wrapup` typed the wrap-up prompt — the card's Wrap up, `ao team stop` and a
    # manager's wind-down (§4.9a) — which this package cannot tell from any other send by its
    # text. The doorbell never rings while it is set; the next plain send clears it, since a new
    # instruction is a run carrying on.
    wrapup_at: str | None = None
    # A doorbell that failed to submit twice (design §4.10): `{at, error}`, so the sender and the
    # page can see that the ring did not land; cleared by the next ring that does.
    doorbell_failed: dict[str, str] | None = None
    # The bell stopped (design §4.10 *A ring is answered by a read, or the bell stops*, TD-347):
    # `{at, rings}` after `mail.DOORBELL_HELD` rings in a row each answered by a turn that read none of
    # its mail. The doorbell stays silent for it until it reads (`inbox`) or a person acts toward it.
    doorbell_held: dict[str, Any] | None = None
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
    # (`mail.WAKES_KEEP`): `{at, cause: "mail" | "member", charged, covered, via}` — the record step 5
    # measures, a charged mail wake apart from a free one that rode a member's change; `via` is
    # `wait` or `doorbell` (step 7), which is how the wake reached the session.
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

    def view(self, *, bookkeeping: bool = False) -> dict[str, Any]:
        """The record as `list`, `get` and the `subscribe` deltas hand it out: message bodies never
        ride the push that reaches the Org page on every change (design §4.10 "A bounded body") —
        those carry counts, and a body is fetched by `inbox`. `sends` (what `ao status` prints)
        stays. `threads` and `wakes` are the host agent's bookkeeping — their one visible part,
        `bound_hit`, is in `mail` — and ride only a `get` of one record (`bookkeeping=True`): they
        left `list`, the stream and `wait` when a `list` of six records outgrew what a client could
        read (TD-066)."""
        d = self.to_dict()
        d.pop("inbox")
        d.pop("outbox")
        d.pop("first_prompt")  # the brief's text, as a body is: the record's file has it until it is sent
        if not bookkeeping:
            d.pop("threads")
            d.pop("wakes")
        d["unread"] = self.unread()
        d["asks_waiting"] = self.asks_waiting()
        d["prs_waiting"] = self.prs_waiting()
        d["mail"] = self.mail_marks()
        return d

    def unread(self) -> int:
        return sum(1 for e in self.inbox if not e.read_at)

    def asks_waiting(self, *, home: str | None = None, handed_only: bool = False) -> int:
        """Design §4.9b (TD-075 step 4): the open `ask`s and `steer`s **addressed** to this record —
        a copy is not addressed to it — as a number and never their text, since nobody reads
        another session's inbox. What a manager reads to fill an empty techlead seat; computed
        here like `unread`, so every reader sees the same count. An entry the record passed up
        (§4.9b) is left out: it waits on the person, and a seat filled for it would have nothing
        to answer.

        An address is compared whole, host included (§4.4a): names are unique per host, not per
        org, so `tl@laptop` is not this record's address merely because its id is `tl`. A bare
        address names a session on the host whose store holds the entry — `home` when the reader
        says which, else the record's own host, which is the same thing for a record of this host.
        `handed_only` counts the handed entries among them alone: the nudge names those apart, since
        one already read wants its outcome, not another `ao inbox` (TD-218 slice 3)."""
        return len(self._asks(home, handed_only))

    def asks_ids(self, *, home: str | None = None) -> list[str]:
        """The ids `asks_waiting` counts, in the inbox's order: what a manager on call's `asks`
        trigger keys a fill on (§6 rule 3, TD-259), so a question it left standing fills it once."""
        return self._asks(home, False)

    def _asks(self, home: str | None, handed_only: bool) -> list[str]:
        storing = home or self.host
        mine = (self.id, self.host or storing)

        def where(address: str) -> tuple[str, str]:
            sid, host = naming.split_address(address)
            return sid, host or storing

        # a handed entry (§4.10 *An entry handed to a seat*, TD-218) counts while it owes its
        # outcome, read or not — so a seat that exited with it half done is filled again — and not
        # while a question of the record's own to the person on its thread is open: the seat waits
        # on the person then, and the person's answer closes that question and counts it again
        asking = {e.root for e in self.outbox if e.open and e.kind == "ask" and PERSON in e.to}
        return [
            e.id
            for e in self.inbox
            if (
                (e.owes and e.id not in asking)
                if e.handed_entry
                else (e.open and e.kind in ("ask", "steer") and not e.passed_up)
            )  # passed up: the person's to answer now (§4.9b), no seat need be filled for it
            and any(where(x) == mine for x in e.to)
            and (not handed_only or e.handed_entry)
        ]

    def prs_waiting(self, *, home: str | None = None) -> dict[str, Any] | None:
        """Design §4.9b *The reader* (TD-093): of `asks_waiting`, the `ask`s that carry a `pr` —
        `{n, oldest, asks}`, a number, the oldest one's time and `asks: [{from, pr}]`, each one's
        sender and PR in the order they came (TD-333), never their text. None when there are none,
        so a header draws nothing."""
        storing = home or self.host
        mine = (self.id, self.host or storing)

        def where(address: str) -> tuple[str, str]:
            sid, host = naming.split_address(address)
            return sid, host or storing

        held = [
            e
            for e in self.inbox
            if e.open
            and e.kind == "ask"
            and e.pr is not None
            and not e.passed_up
            and any(where(x) == mine for x in e.to)
        ]
        if not held:
            return None
        asks = [{"from": e.from_, "pr": e.pr} for e in sorted(held, key=lambda e: e.at)]
        return {"n": len(held), "oldest": min(e.at for e in held), "asks": asks}

    def mail_marks(self) -> dict[str, Any]:
        """What a card and an `ao` reply say about this session's mail without a body: open `ask`s
        it holds, its own `ask`s that expired or whose addressee exited, and copies that failed."""
        return {
            "open_asks": [e.id for e in self.inbox if e.open],
            "expired": [e.id for e in self.outbox if e.expired_at],
            "addressee_exited": [e.id for e in self.outbox if e.pending and e.open],
            "copies_failed": [e.id for e in self.outbox if e.copies_failed],
            "owed": self.owed(),
            "bound_hit": [k for k, t in self.threads.items() if t.bound_hit],
            "wake_budget_spent": self.wake_budget_spent(),
        }

    def owed(self) -> list[str]:
        """Everything this session owes the person an outcome on (design §4.10 *Outcomes*): the
        questions it asked and the person answered — its own copies in the **outbox**, in the
        order they were sent — and, since TD-077 b, the work the person **handed** it, which
        lives in its **inbox**. One number, because `ao progress none`, `ao progress restart`,
        `mail.owed` and *Ready to close* all read this and none of them cares which direction the
        work came from."""
        # the inbox half is handed work alone: a question passed up owes on the **asker's** outbox
        # copy, never on the copy the passer holds (§4.9b)
        return [e.id for e in self.outbox if e.owes_for(session_inbox=False)] + [
            e.id for e in self.inbox if e.owes_for(session_inbox=True)
        ]

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
        if state not in ("exited", "closed"):
            self.ended = None  # a run that came back has not ended

    def report_progress(self, entry: ProgressEntry) -> bool:
        """Upsert a `progress` entry by reference, in place so the lane order the session declared
        survives. Returns False when §9 invariant 10 refuses the write: what the session declared is
        never overwritten by what the agent derived or scraped."""
        return _upsert(self.progress, entry)

    def note_review(self, entry: ProgressEntry) -> bool:
        """The tick's derived `entry`, where it is refused over a declared claim on its reference,
        leaves its PR beside the claim as `review_pr` (§4.8, TD-150). True when that changed. Only
        the tick calls it: an RPC's refused entry is refused, and says so (`refused`)."""
        old = next((e for e in self.progress if e.ref == entry.ref), None)
        return old is not None and _note_review(old, entry)

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
        if isinstance(old, ProgressEntry) and old.source == "declared" and entry.status == "claimed" and not entry.pr:
            entry.review_pr = old.review_pr  # a re-claim keeps what the tick last saw; done or dropped holds none
        if isinstance(old, ProgressEntry) and old.source == entry.source == "declared" and not entry.slices:
            entry.slices = old.slices  # a re-claim, or the last slice's plain `done`, keeps them (TD-325)
        entries[i] = entry
        return True
    entries.append(entry)
    return True


# A derived entry's `why` when its PR was closed without merging (`sessionorc.reports`): no review is
# held by a closed PR, so `review_pr` clears on it as it does on a merge (TD-150).
PR_CLOSED = "PR closed unmerged"


def _note_review(old: ProgressEntry, derived: ProgressEntry) -> bool:
    """A derived progress entry on a *declared* claim's reference is refused (§9 invariant 10) —
    all but its PR, which is kept beside the claim as `review_pr` while it is open and cleared once
    it merged (TD-150), and once merged held as one of the claim's `slices`, `source: derived`,
    unless the claim holds it already (§4.9a *A slice is work done*, TD-325); a PR closed unmerged
    writes no slice. True only when that changed, so the record is saved only then."""
    if old.source != "declared" or old.status != "claimed" or derived.source == "declared":
        return False
    # the claim's own `--pr` merged is a slice too, while the claim is held: the merge is work done,
    # and a plain `done --pr` after it keeps the slice, read as one pair (techlead, #1055)
    merged = derived.status == "done" and bool(derived.pr) and derived.why != PR_CLOSED
    sliced = merged and old.add_slice(int(derived.pr or 0), "derived")
    want = derived.pr if derived.status == "claimed" and derived.pr and derived.why != PR_CLOSED else None
    if want == old.review_pr:
        return sliced
    old.review_pr = want
    return True


# -- the replica's merge, in both directions (design §4.4a, TD-057 step 2) --------------------------


class NotTheSameSession(ValueError):
    """A copy that disagrees with the record on an identity field describes another session."""


# Home-owned, and yet written on a node while it is offline: a person's `send` or `keys` there is
# recorded in `sends` and refills the wake budget, and their look sets `seen_at` (review of PR
# #198). An overlay would erase them on reconnect and say nothing. Each only ever grows — an
# append-only list keyed by id, and two timestamps that only move forward — so both directions
# **merge** these three instead: nothing a merge of monotonic fields can resurrect or lose.
GROWS = frozenset({"sends", "seen_at", "wake_refilled_at"})
SENDS_KEPT = 20  # the bound `sessionorc.mail.SENDS_KEEP` holds the list to; models cannot import mail


# What makes two copies the same session. The rest of `IDENTITY` is set or changed on the session's
# own host after it exists — `adapter_id` by the first hook, `name` by a rename, `profile`, `repo`,
# `worktree`, `created` — so it travels with the node's report and is never grounds for refusing one
# (review of PR #202: a record reported before its first hook would have frozen at the home).
SAME_SESSION = frozenset({"id", "host", "kind", "adapter", "dir"})


def _overlay(record: Session, copy: Mapping[str, Any], owned: frozenset[str]) -> Session:
    for f in sorted(SAME_SESSION):
        if f in copy and copy[f] != getattr(record, f):
            raise NotTheSameSession(f"{record.id}: `{f}` is {getattr(record, f)!r} here and {copy[f]!r} in the copy")
    taken = (owned | GROWS) & copy.keys()
    parsed = Session.from_dict({**record.to_dict(), **{k: copy[k] for k in taken}})
    for f in taken - GROWS:
        setattr(record, f, getattr(parsed, f))
    if "sends" in taken:
        by_id = {e.id: e for e in (*parsed.sends, *record.sends)}  # on one id the record's own entry stands
        record.sends = sorted(by_id.values(), key=lambda e: (e.at, e.id))[-SENDS_KEPT:]
    for f in ("seen_at", "wake_refilled_at"):
        if f in taken:
            setattr(record, f, max(filter(None, (getattr(record, f), getattr(parsed, f))), default=None))
    return record


def apply_home(record: Session, home_copy: Mapping[str, Any]) -> Session:
    """A node's replica takes the home's copy: exactly the home-owned fields are overlaid — the
    graph and intent — and nothing the node observes is touched. Merges go by owner, never by last
    write (§4.4a): the link was down, the node wrapped a worker up and it exited, and meanwhile a
    person at the home extended its `run_until`; the node's `exited` stands, and so does the new
    `run_until`. `home_copy` is a record as `to_dict()` writes it; fields it omits are left alone.
    The three fields in `GROWS` are merged, in this direction and the other."""
    return _overlay(record, home_copy, HOME_OWNED)


def apply_node(record: Session, report: Mapping[str, Any]) -> Session:
    """The home's record takes a node's report: exactly the node-owned fields — what the node
    observes and enforces on its host, and the identity fields that are set there after the session
    exists. The mirror of `apply_home`."""
    return _overlay(record, report, NODE_OWNED | (IDENTITY - SAME_SESSION))
