"""Session records — the one shape every layer (agent, CLI, UI, adapters) agrees on.

A session is a tmux session, with or without a repo, with or without an agent (design §2.14).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
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

# Grants a session record can hold in `capabilities` (design §4.8). `orchestrate`: the session may
# act on other sessions through the agent (§9 invariant 11).
GRANTS = ("orchestrate",)

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
    at = datetime.fromisoformat(str(when).replace("Z", "+00:00")).astimezone()
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
    # Started outside agentorc and known only from the tool's live registry (TD-010 a): a read-only
    # card — no tmux pane, no controls, never stored; it leaves when the process does.
    external: bool = False
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
    # lives on the target, not on the orchestrator, so the gate is one lookup and nothing has to be
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

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("rev")
        d["pending"] = self.pending.to_dict() if self.pending else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Session:
        d = dict(d)
        pending = d.pop("pending", None)
        progress = d.pop("progress", None) or []
        findings = d.pop("findings", None) or []
        known = {f for f in cls.__dataclass_fields__}
        obj = cls(**{k: v for k, v in d.items() if k in known})
        obj.pending = Pending.from_dict(pending) if pending else None
        obj.progress = [ProgressEntry.from_dict(p) for p in progress]
        obj.findings = [FindingEntry.from_dict(f) for f in findings]
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
        that TD — left a `claimed` entry forever, and the idle-with-open-work nudge (§4.8, §6) fires
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
