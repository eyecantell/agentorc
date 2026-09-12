"""Session records — the one shape every layer (agent, CLI, UI, adapters) agrees on.

A session is a tmux session, with or without a repo, with or without an agent (design §2.14).
"""

from __future__ import annotations

import re
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

    kind: str  # permission | question | prompt | limit
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
    # The model actually in use, when the adapter can tell (TD-031): observed, never the profile's
    # declared model — that is an intent (§4.2a), and a display says so when it falls back to it.
    model: str | None = None
    # Report channels (design §4.8). `lane` is the ordered list of references the session was handed
    # (or `["free-pick"]`), so a display can say *1 of 2* without parsing the brief; the other two
    # are what the session says it did.
    lane: list[str] = field(default_factory=list)
    progress: list[ProgressEntry] = field(default_factory=list)
    findings: list[FindingEntry] = field(default_factory=list)

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
