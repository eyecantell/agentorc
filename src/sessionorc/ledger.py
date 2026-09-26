"""The ledger's entry headers, read (design §4.4 *Repo facts*, TD-176 slice 1).

A repo's ledger (`.agentorc.yml`'s `ledger:`, default `docs/technical_debt.md`) is a file of
entries, each `## TD-NNN: title` followed by header fields — `**Priority:**`, `**Owner:**`,
`**Kind:**`, `**Pickable:**`. This module is the one reader of those fields: the Org's Repo facet
and the Repo page count what it returns, and `tests/test_ledger.py` holds the repo's own ledger to
its rules through the same regexes. Nothing in dev-cadence reads these fields (its readers are the
board's and the sweep's), which is why the reader is here; TD-159 weighs whether it belongs there.

Two reads. `entries` parses one version of the file: an entry counts while its section is in it.
`history` reads the file's git history in the checkout, so *opened* and *closed* in a window mean
the first commit whose file holds an entry's section and the first whose file no longer does.
Both are pure reads of files and git; neither writes anything.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

DEFAULT = "docs/technical_debt.md"  # the `ledger:` default, as `agentorc.repoconfig` has it
REPO_FILE = ".agentorc.yml"

HEADING = re.compile(r"^## (TD-\d{3}):[ \t]*(.*)$", re.M)
FIELD = re.compile(r"^\*\*(Priority|Owner|Kind|Pickable):\*\*[ \t]*(.*)$", re.M)
COMMENT = re.compile(r"<!--.*?-->", re.S)

# The page's four kinds, in the order an entry is tested for them (§4.4 *Repo facts*).
KINDS = ("pickable", "design-first", "for-you", "other")
PRIORITIES = ("high", "medium", "low")
# The windows every count is kept for: the last day, week and month, rolling.
WINDOWS = {"day": timedelta(days=1), "week": timedelta(days=7), "month": timedelta(days=30)}


def strip_comments(text: str) -> str:
    """The file without its HTML comments: the entry template lives in one and is headed TD-001,
    which is a real id."""
    return COMMENT.sub("", text)


def _word(value: str) -> str:
    """A field's first word, lower-cased: `**Pickable:** no — …` is `no`, `**Owner:** paul (…)` is
    `paul`. The prose after the word is the entry's, and no count reads it."""
    m = re.match(r"[\w-]+", value.strip())
    return m.group(0).lower() if m else ""


def kind_of(entry: dict[str, Any]) -> str:
    """The page's kind for an entry: *pickable* (`Pickable: yes`), *design-first* (`Kind:
    design-first`), *for you* (`Owner: paul` or `Kind: decision`), else *other*."""
    if entry.get("pickable") == "yes":
        return "pickable"
    if entry.get("kind") == "design-first":
        return "design-first"
    if entry.get("owner") == "paul" or entry.get("kind") == "decision":
        return "for-you"
    return "other"


def entries(text: str) -> list[dict[str, Any]]:
    """Every entry of one version of the file, in file order: `id`, `title`, and the header fields
    `priority`, `owner`, `kind`, `pickable` as their first word ('' when absent), with `for_page`
    the page's kind. A field is read only from the entry's own section."""
    t = strip_comments(text)
    heads = list(HEADING.finditer(t))
    out: list[dict[str, Any]] = []
    for i, m in enumerate(heads):
        body = t[m.end() : heads[i + 1].start() if i + 1 < len(heads) else len(t)]
        fields = {k.lower(): _word(v) for k, v in FIELD.findall(body)}
        e = {
            "id": m.group(1),
            "title": m.group(2).strip(),
            "priority": fields.get("priority", ""),
            "owner": fields.get("owner", ""),
            "kind": fields.get("kind", ""),
            "pickable": fields.get("pickable", ""),
        }
        e["for_page"] = kind_of(e)
        out.append(e)
    return out


def ledger_path(root: Path | str) -> str:
    """The checkout's ledger file, relative to it: `.agentorc.yml`'s `ledger:` when it names one,
    else the default. An unreadable file is the default, as it is for `agentorc.repoconfig`."""
    try:
        doc = yaml.safe_load((Path(root) / REPO_FILE).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return DEFAULT
    got = doc.get("ledger") if isinstance(doc, dict) else None
    return got.strip() if isinstance(got, str) and got.strip() else DEFAULT


@dataclass
class Change:
    """One entry's life in the file's history: when its section first appeared, and when it left
    (None while it is still there)."""

    id: str
    title: str
    opened: datetime
    closed: datetime | None = None


def _parse_log(out: str, skip: set[str]) -> dict[str, Change]:
    """The changes from `git log --reverse -p --unified=0`: a commit is a `\\x00C <sha> <iso>` line,
    then its diff; a heading on a `+` line is in the file after that commit, on a `-` line it left.
    A heading both removed and added in one commit (an entry moved in the file, its title edited)
    stays. An entry closed and later re-added is open again from its first opening."""
    life: dict[str, Change] = {}
    present: set[str] = set()

    def apply(at: datetime | None, added: dict[str, str], removed: set[str]) -> None:
        if at is None:
            return
        for tid in removed - set(added):
            if tid in present:
                present.discard(tid)
                life[tid].closed = at
        for tid, title in added.items():
            if tid not in life:
                life[tid] = Change(tid, title, at)
            else:
                life[tid].title = title or life[tid].title
                if tid not in present:
                    life[tid].closed = None
            present.add(tid)

    at: datetime | None = None
    added: dict[str, str] = {}
    removed: set[str] = set()
    for line in out.splitlines():
        if line.startswith("\x00C "):
            apply(at, added, removed)
            added, removed = {}, set()
            try:
                at = datetime.fromisoformat(line.split(" ", 2)[2].strip())
            except (IndexError, ValueError):
                at = None
            continue
        if line.startswith(("+## TD-", "-## TD-")):
            m = HEADING.match(line[1:])
            if not m or m.group(1) in skip:
                continue
            if line[0] == "+":
                added[m.group(1)] = m.group(2).strip()
            else:
                removed.add(m.group(1))
    apply(at, added, removed)
    return life


def history(root: Path | str, rel: str, text: str = "", timeout: float = 30.0) -> dict[str, Change] | None:
    """Every entry's opened / closed instants from the ledger file's git history in `root`, or None
    when git could not be asked (not a repo, git missing, a timeout) — *could not look*, never
    *nothing changed*. `text` is the file as it stands, so the ids of headings that sit only in a
    comment (the entry template's) are left out. A file rewritten without a history reads as
    opened at its first commit, which is all its history says."""
    raw = set(HEADING.findall(text))
    skip = {tid for tid, _ in raw} - {tid for tid, _ in HEADING.findall(strip_comments(text))}
    try:
        cp = subprocess.run(
            [
                "git", "log", "--first-parent", "--diff-merges=first-parent", "--reverse", "--no-renames",
                "--format=%x00C %H %cI", "-p", "--unified=0", "--", rel,
            ],
            capture_output=True, text=True, errors="replace", timeout=timeout, cwd=str(root),
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    if cp.returncode != 0:
        return None
    return _parse_log(cp.stdout, skip)


def _count(stamps: list[datetime], now: datetime) -> dict[str, int]:
    return {w: sum(1 for t in stamps if t > now - span) for w, span in WINDOWS.items()}


def reading(root: Path | str, now: datetime, *, with_history: bool = True, rel: str | None = None) -> dict[str, Any]:
    """The ledger reading of one checkout (§4.4 *Repo facts*): `{path, entries, by_priority,
    by_kind, windows, recent, at}`, or `{path, error}` when the file cannot be read. `windows` is
    `{day|week|month: {opened, closed}}` from the history and `recent` the entries opened or closed
    in the longest window, newest first — both absent when the history was not read, and marked
    `history_error` when git could not be asked."""
    rel = rel or ledger_path(root)
    try:
        text = (Path(root) / rel).read_text(encoding="utf-8")
    except OSError as e:
        return {"path": rel, "error": f"{rel}: {e.strerror or e}", "at": now.isoformat()}
    got = entries(text)
    out: dict[str, Any] = {
        "path": rel,
        "entries": got,
        "by_priority": {p: sum(1 for e in got if e["priority"] == p) for p in PRIORITIES},
        "by_kind": {k: sum(1 for e in got if e["for_page"] == k) for k in KINDS},
        "at": now.isoformat(),
    }
    if not with_history:
        return out
    life = history(root, rel, text)
    if life is None:
        out["history_error"] = "git could not read the ledger's history"
        return out
    opened = [c.opened for c in life.values()]
    closed = [c.closed for c in life.values() if c.closed]
    out["windows"] = {w: {"opened": o, "closed": _count(closed, now)[w]} for w, o in _count(opened, now).items()}
    since = now - WINDOWS["month"]
    recent = [c for c in life.values() if c.opened > since or (c.closed and c.closed > since)]
    recent.sort(key=lambda c: max(c.opened, c.closed or c.opened), reverse=True)
    out["recent"] = [
        {"id": c.id, "title": c.title, "opened": c.opened.isoformat(), "closed": c.closed and c.closed.isoformat()}
        for c in recent
    ]
    return out
