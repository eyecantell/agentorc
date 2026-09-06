"""Session records — the one shape every layer (agent, CLI, UI, adapters) agrees on.

A session is a tmux session, with or without a repo, with or without an agent (design §2.14).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

State = Literal["working", "needs-you", "limited", "stalled?", "idle", "exited", "closed", "unreachable"]
Kind = Literal["interactive", "command"]
Confidence = Literal["hook", "scraped"]

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
    subagents: int = 0  # live subagents (SubagentStart − SubagentStop); Ready to close needs zero
    last_output: str | None = None  # ISO time the run log last grew (liveness cross-check)
    run_log: str | None = None
    closed_at: str | None = None
    git: dict[str, Any] | None = None  # branch, dirty, ahead, behind, files (sessionorc.gitinfo)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pending"] = self.pending.to_dict() if self.pending else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Session:
        d = dict(d)
        pending = d.pop("pending", None)
        known = {f for f in cls.__dataclass_fields__}
        obj = cls(**{k: v for k, v in d.items() if k in known})
        obj.pending = Pending.from_dict(pending) if pending else None
        return obj

    def set_state(self, state: State, *, confidence: Confidence, pending: Pending | None = None) -> None:
        if state != self.state:
            self.since = now_iso()
        self.state = state
        self.confidence = confidence
        self.pending = pending
