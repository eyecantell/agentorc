"""Screen rules: the labelled scraped fallback of design §4.2 (TD-015).

A tool ships one versioned TOML manifest of rules evaluated over the bottom of its pane. A rule
names the state it means, the patterns that must (`all`) / may (`any`) / must not (`not`) appear
in the last `region` lines, an optional pending (`$line` = the first matched line), and a
priority. The highest-priority match wins; `explain()` says which rule and on what evidence, so
`ao explain` can show its work. Tool-agnostic: `sessionorc` never knows whose rules these are.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from sessionorc.models import Pending, State

_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_ESC = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[ -/]*[0-~]")


def painted_text(row: str) -> str:
    """A raw pane row (`capture-pane -e`) reduced to the text painted at normal intensity: runs
    under the faint attribute (SGR 2, what tools use for placeholder and suggestion text) are
    dropped, every other escape is stripped. Faint ends at SGR 0 or SGR 22. TD-027: Claude Code
    paints a suggested next prompt into its composer in faint text, and a session's own last
    prompt is a common suggestion — so "the text is still in the composer" must mean painted text."""
    out: list[str] = []
    faint = False
    pos = 0
    for m in _SGR.finditer(row):
        if not faint:
            out.append(row[pos : m.start()])
        params = m.group(1).split(";") if m.group(1) else ["0"]
        for p_ in params:
            if p_ in ("", "0", "22"):
                faint = False
            elif p_ == "2":
                faint = True
        pos = m.end()
    if not faint:
        out.append(row[pos:])
    return _ESC.sub("", "".join(out))


@dataclass
class Rule:
    id: str
    state: State
    any: list[str] = field(default_factory=list)
    all: list[str] = field(default_factory=list)
    not_: list[str] = field(default_factory=list)
    region: int = 12  # lines from the bottom of the pane that the rule looks at
    priority: int = 0
    pending_kind: str | None = None
    pending_text: str | None = None  # "$line" → the first line that matched

    def __post_init__(self) -> None:
        flags = re.IGNORECASE
        self._any = [re.compile(p, flags) for p in self.any]
        self._all = [re.compile(p, flags) for p in self.all]
        self._not = [re.compile(p, flags) for p in self.not_]

    def match(self, tail: list[str]) -> list[str] | None:
        """The evidence (matched lines, in pane order) when the rule fires, else None."""
        window = tail[-self.region :] if self.region > 0 else tail
        lines = [ln for ln in window if ln.strip()]
        if any(p.search(ln) for p in self._not for ln in lines):
            return None
        hits: list[str] = []
        for p in self._all:
            hit = next((ln for ln in lines if p.search(ln)), None)
            if hit is None:
                return None
            hits.append(hit)
        if self._any:
            any_hits = [ln for ln in lines if any(p.search(ln) for p in self._any)]
            if not any_hits:
                return None
            hits += any_hits
        if not self._all and not self._any:
            return None  # a rule with no positive pattern never fires
        seen: set[str] = set()
        return [h for h in hits if not (h in seen or seen.add(h))]


@dataclass
class Match:
    rule: str
    state: State
    evidence: list[str]
    pending: Pending | None = None

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "state": self.state,
            "evidence": self.evidence,
            "pending": self.pending.to_dict() if self.pending else None,
        }


@dataclass
class Manifest:
    tool: str
    version: int
    rules: list[Rule]

    @classmethod
    def load(cls, path: Path | str) -> Manifest:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        rules = []
        for raw in data.get("rules") or []:
            pend = raw.get("pending") or {}
            rules.append(
                Rule(
                    id=str(raw["id"]),
                    state=raw["state"],
                    any=list(raw.get("any") or []),
                    all=list(raw.get("all") or []),
                    not_=list(raw.get("not") or []),
                    region=int(raw.get("region", 12)),
                    priority=int(raw.get("priority", 0)),
                    pending_kind=pend.get("kind"),
                    pending_text=pend.get("text"),
                )
            )
        rules.sort(key=lambda r: -r.priority)
        return cls(tool=str(data.get("tool", "")), version=int(data.get("version", 1)), rules=rules)

    def explain(self, tail: list[str]) -> Match | None:
        for rule in self.rules:
            hits = rule.match(tail)
            if hits is None:
                continue
            pending = None
            if rule.pending_kind:
                text = rule.pending_text or ""
                if text == "$line":
                    text = hits[0].strip()
                pending = Pending(kind=rule.pending_kind, text=text[:200])
            return Match(rule=rule.id, state=rule.state, evidence=hits, pending=pending)
        return None
