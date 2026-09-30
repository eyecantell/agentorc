"""The ledger's entry headers, read (design §4.4 *Repo facts*, TD-176 slice 1).

A repo's ledger (`.agentorc.yml`'s `ledger:`, default `docs/technical_debt.md`) is a file of
entries, each `## TD-NNN: title` followed by header fields — `**Priority:**`, `**Owner:**`,
`**Kind:**`, `**Type:**`, `**Blocked by:**`. This module is the one reader of those fields: the
Org's Repo facet and the Repo page count what it returns, and `tests/test_ledger.py` holds the
repo's own ledger to its rules through the same regexes. **Pickable is derived** from `Blocked by:`
by cadence §2.4's rule (TD-228), the rule dev-cadence's `scripts/ledger.py` applies, and
`tests/test_ledger_derived.py` holds the two readers equal; a written `**Pickable:** no` still
blocks until the ledger's migration removes the line.

Two reads. `entries` parses one version of the file: an entry counts while its section is in it.
`history` reads the file's git history in the checkout, so *opened* and *closed* in a window mean
the first commit whose file holds an entry's section and the first whose file no longer does.
Both are pure reads of files and git; neither writes anything.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from sessionorc.models import owner_word

# origin's default branch as last fetched; the two usual names when `origin/HEAD` was never set
DEFAULT_REFS = ("origin/HEAD", "origin/main", "origin/master")
DEFAULT = "docs/technical_debt.md"  # the `ledger:` default, as `agentorc.repoconfig` has it
REPO_FILE = ".agentorc.yml"

HEADING = re.compile(r"^## (TD-\d+):[ \t]*(.*)$", re.M)
SECTION = re.compile(r"^## ", re.M)  # a body ends at the next `## ` heading of any kind, as the script reads it
# Parity (cadence §7): §2.12's header-block rule, as dev-cadence's `scripts/ledger.py` reads it.
FIELD = re.compile(r"^\*\*([A-Za-z][^*:\n]*?):\*\*[ \t]*(.*?)[ \t]*$")  # one line, one field
# the fields read further down the body when the header block lacks them: the script's three by their
# exact spelling, as it reads them, and ours by any spelling
BELOW_EXACT = ("Priority", "Type", "Blocked by")
BELOW = ("owner", "kind", "pickable")
COMMENT = re.compile(r"<!--.*?-->", re.S)
ID_ITEM = re.compile(r"^TD-(\d+)$")
XREPO_ITEM = re.compile(r"^([\w.-]+(?:/[\w.-]+)?)#(TD-\d+)$")  # cadence §2.4's `<repo>#TD-NNN`
DECISION = re.compile(r"\bdecision\s*\(([^)]+)\)", re.I)
WORD = re.compile(r"[^\s,;]+")

# The page's four kinds, in the order an entry is tested for them (§4.4 *Repo facts*).
KINDS = ("for-you", "design-first", "pickable", "other")
PRIORITIES = ("high", "medium", "low")
TYPES = ("debt", "feature")  # cadence §2.11: pick order within a Priority; the first is the default
# The windows every count is kept for: the last day, week and month, rolling.
WINDOWS = {"day": timedelta(days=1), "week": timedelta(days=7), "month": timedelta(days=30)}


def strip_comments(text: str) -> str:
    """The file without its HTML comments: the entry template lives in one and is headed TD-001,
    which is a real id."""
    return COMMENT.sub("", text)


def _word(value: str) -> str:
    """A field's first word, lower-cased: `**Pickable:** no — …` is `no`, `**Owner:** paul (…)` is
    `paul`. The prose after the word is the entry's, and no count reads it."""
    m = WORD.search(value or "")
    return m.group(0).strip("*_`").rstrip(".:").lower() if m else ""


def _key(name: str) -> str:
    """`Blocked by` / `blocked-by` / `BLOCKED_BY` all read as `blocked by`."""
    return re.sub(r"[-_\s]+", " ", name.strip()).lower()


def _header(body: str) -> dict[str, str]:
    """Every `**Name:** value` line of the header block: after the heading's blank lines, up to the
    next blank line; a repeated field, the first. A field the block lacks is read from further down
    the body (the safe direction, as the script reads it)."""
    fields: dict[str, str] = {}
    started = False
    for line in body.split("\n"):
        if not line.strip():
            if started:
                break
            continue
        started = True
        if m := FIELD.match(line):
            fields.setdefault(_key(m.group(1)), m.group(2))
    for line in body.split("\n"):
        if not (m := FIELD.match(line)) or (k := _key(m.group(1))) in fields:
            continue
        if m.group(1) in BELOW_EXACT or k in BELOW:
            fields[k] = m.group(2)
    return fields


def _norm(tid: str) -> str:
    """An id by its number, as the script compares them: `TD-42` and `TD-042` are one entry."""
    return f"TD-{int(tid[3:]):03d}"


def ids_in(text: str | None) -> set[str]:
    """The ids with a `## TD-NNN:` heading in a ledger or an archive, comments dropped."""
    return {_norm(m.group(1)) for m in HEADING.finditer(strip_comments(text or ""))}


def parse_blocked(raw: str) -> tuple[list[str], list[str], list[str]]:
    """One `**Blocked by:**` value as (ids — `TD-NNN` or `<repo>#TD-NNN` — decision holders, items it
    cannot read), cadence §2.4's rule: the decision last, what follows it with no comma its pointer
    and never read, an item after its comma unreadable."""
    ids: list[str] = []
    who: list[str] = []
    bad: list[str] = []
    if d := DECISION.search(raw):
        who.append(d.group(1).strip())
        rest = raw[d.end() :].strip()
        if rest.startswith(","):
            bad.append(f"after the decision: {rest[1:].strip()}")
        raw = raw[: d.start()]
    for item in (x.strip() for x in raw.split(",")):
        if not item:
            continue
        (ids if ID_ITEM.match(item) or XREPO_ITEM.match(item) else bad).append(item)
    return ids, who, bad


Resolve = Callable[[str], str]  # a `<repo>#TD-NNN` item → `archived`, `open` or `unresolved`


def kind_of(entry: dict[str, Any]) -> str:
    """The page's kind for an entry (§4.4 *Repo facts*), tested in this order so each has one:
    *for you* (`Owner: paul`, `Kind: decision`, or blocked by a decision), *design-first* (`Kind:
    design-first`), *pickable* (pickable, `Kind: build` or none), else *other*."""
    if (
        entry.get("owner") == "paul"
        or entry.get("kind") == "decision"
        or any(b.startswith("decision (") for b in entry.get("blocked_by") or [])
    ):
        return "for-you"
    if entry.get("kind") == "design-first":
        return "design-first"
    if entry.get("pickable") == "yes" and entry.get("kind") in ("", "build"):
        return "pickable"
    return "other"


def lane_matches(lane: list[str], entry: dict[str, Any]) -> bool:
    """Whether an entry belongs to a lane (design §6 rule 6, TD-195), by its header and never its
    prose. A word: `design-first` is a pickable entry with `Kind: design-first`; `free-pick` a
    pickable one whose kind is `build` or unwritten (TD-228), so a live check, an evaluation and a
    decision match no lane; a reference, or any other word, matches nothing until a role gives it a
    meaning here. **An `owner:<word>` narrows the rest** (TD-214, TD-227): with one or more, the
    entry's `Owner:` must be one named or absent, so `[free-pick, owner:grinder]` leaves the
    anchor's entries out; it matches nothing by itself."""
    owners = {o for w in lane if (o := owner_word(w))}
    mine = str(entry.get("owner") or "").lower()
    if owners and mine and mine not in owners:
        return False
    return any(_word_matches(w, entry) for w in lane if owner_word(w) is None)


def _word_matches(word: str, entry: dict[str, Any]) -> bool:
    if entry.get("pickable") != "yes":
        return False
    if word == "design-first":
        return entry.get("kind") == "design-first"
    if word == "free-pick":
        return entry.get("kind") in ("", "build")
    return False


def entries(
    text: str, archive: str | None = None, resolve: Resolve | None = None, *, fallback: bool = True
) -> list[dict[str, Any]]:
    """Every entry of one version of the file, in file order: `id`, `title`, the header fields
    `priority`, `owner`, `kind` as their first word ('' when absent), `type` (`debt` unwritten or
    unknown), `blocked_by` — what still blocks, as the script lists it: open ids, `decision (<who>)`,
    an unread item quoted — and `pickable`, `yes` or `no`, derived by cadence §2.4's rule (§4.4
    *Repo facts*, TD-228): blocked while `Blocked by:` names an id not in `archive` (open, or in
    neither file), a decision, or an item it cannot read. A `<repo>#TD-NNN` item asks `resolve`, and
    anything but `archived` keeps the block; with no `resolve` it is unresolved. `fallback`: a
    written `Pickable: no` still reads as blocked (the migration's, TD-228 slice 3; the equality
    test turns it off). `for_page` is the page's kind. A field is read only from the entry's own
    section."""
    t = strip_comments(text)
    heads = list(HEADING.finditer(t))
    live = {_norm(m.group(1)) for m in heads}
    archived = ids_in(archive)
    out: list[dict[str, Any]] = []
    for m in heads:
        nxt = SECTION.search(t, m.end())
        fields = _header(t[m.end() : nxt.start() if nxt else len(t)])
        etype = (fields.get("type") or "").split()[0].lower() if (fields.get("type") or "").split() else ""
        blocked_by: list[str] = []
        raw = fields.get("blocked by") or ""  # an empty line blocks nothing
        if raw:
            ids, who, bad = parse_blocked(raw)
            for b in ids:
                if "#" in b:
                    if (resolve(b) if resolve else "unresolved") != "archived":
                        blocked_by.append(b)
                elif _norm(b) in live or _norm(b) not in archived:
                    blocked_by.append(b)
            blocked_by += [f"decision ({w})" for w in who] + [repr(b) for b in bad]
        written = _word(fields.get("pickable", ""))
        e = {
            "id": m.group(1),
            "title": m.group(2).strip(),
            "priority": _word(fields.get("priority", "")),
            "owner": _word(fields.get("owner", "")),
            "kind": _word(fields.get("kind", "")),
            "type": etype if etype in TYPES else TYPES[0],
            "blocked_by": blocked_by,
            "pickable": "no" if blocked_by or (fallback and written == "no") else "yes",
        }
        e["for_page"] = kind_of(e)
        out.append(e)
    return out


def archive_path(rel: str) -> str:
    """The archive beside a ledger: its name with `_archive` before its suffix (§4.4 *Repo facts*),
    which for the default is cadence's `docs/technical_debt_archive.md`."""
    p = Path(rel)
    return str(p.with_name(f"{p.stem}_archive{p.suffix}"))


def _git(root: Path | str, *args: str, timeout: float = 10.0) -> str | None:
    try:
        cp = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, errors="replace", timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return cp.stdout if cp.returncode == 0 else None


def default_ref(root: Path | str, timeout: float = 10.0) -> str | None:
    """`origin/<default>` as cadence §9's default-branch rule names it, the script's own: the first
    of `origin/HEAD`'s target, `init.defaultBranch`, `main` and `master` that exists on origin; None
    where none does (the working tree is read instead)."""
    names: list[str] = []
    if head := _git(root, "symbolic-ref", "-q", "refs/remotes/origin/HEAD", timeout=timeout):
        names.append(head.strip().removeprefix("refs/remotes/origin/"))
    if init := _git(root, "config", "--get", "init.defaultBranch", timeout=timeout):
        names.append(init.strip())
    for name in dict.fromkeys([*names, "main", "master"]):
        if name and _git(root, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{name}", timeout=timeout):
            return f"origin/{name}"
    return None


class Registry:
    """Resolves `<repo>#TD-NNN` through the home's registry (§4.4 *Repo facts*: the host's
    `repos()`), as the script's roster does: `<repo>` is a checkout's directory name and `owner/name`
    its origin; that checkout's `docs/technical_debt.md` and its archive are read at
    `origin/<default>`, or in the working tree where that ref is missing. Each repo is read once, and
    only when asked; a name two checkouts share, a checkout not listed, a ledger that cannot be read
    or an id in neither file is `unresolved`, which keeps the block."""

    LEDGER, ARCHIVE = DEFAULT, archive_path(DEFAULT)

    def __init__(self, roots: list[str]) -> None:
        self.roots = [Path(r) for r in roots]
        self._read: dict[Path, tuple[set[str], set[str]] | None] = {}
        self._origins: dict[Path, str] = {}

    def _origin(self, root: Path) -> str:
        if root not in self._origins:
            url = (_git(root, "remote", "get-url", "origin") or "").strip().lower()
            self._origins[root] = url.removesuffix("/").removesuffix(".git")
        return self._origins[root]

    def _find(self, repo: str) -> Path | None:
        name = repo.lower()
        if "/" in name:
            hits = [r for r in self.roots if self._origin(r).endswith(("/" + name, ":" + name))]
        else:
            hits = [r for r in self.roots if r.name.lower() == name]
        return hits[0] if len(hits) == 1 else None

    def _repo(self, root: Path) -> tuple[set[str], set[str]] | None:
        if root not in self._read:
            ref = default_ref(root)

            def read(rel: str) -> str | None:
                if ref is not None:
                    return _git(root, "show", f"{ref}:{rel}")
                try:
                    return (root / rel).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return None

            led = read(self.LEDGER)
            self._read[root] = None if led is None else (ids_in(led), ids_in(read(self.ARCHIVE)))
        return self._read[root]

    def __call__(self, item: str) -> str:
        m = XREPO_ITEM.match(item)
        root = self._find(m.group(1)) if m else None
        got = self._repo(root) if root is not None else None
        if got is None or m is None:
            return "unresolved"
        tid = _norm(m.group(2))
        return "archived" if tid in got[1] else "open" if tid in got[0] else "unresolved"


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


def entries_before(
    root: Path | str, rel: str, before: datetime | None, timeout: float = 10.0, resolve: Resolve | None = None
) -> tuple[list[dict[str, Any]] | None, str]:
    """The ledger's entries as the last commit of `origin/<default>` before `before` held them
    (design §6 rule 6, TD-227: `lane_seen`'s first write is the ledger at the declaration), and
    what was read — `origin/main at 1a2b3c4d` — or None and why not; the archive is read at the same
    commit, so `pickable` is derived as it was then. `--before` reads committer dates, which is the
    merge's time for a squash. `before` None reads the ref's tip. Read-only: nothing is fetched."""
    for ref in DEFAULT_REFS:
        when = [f"--before={before.isoformat()}"] if before is not None else []
        sha = _git(root, "rev-list", "-1", *when, ref, timeout=timeout)
        if sha is None:
            continue  # no such ref here: the next name
        if not (sha := sha.strip()):
            return None, f"no commit of {ref} before {before.isoformat() if before else 'now'}"
        text = _git(root, "show", f"{sha}:{rel}", timeout=timeout)
        if text is None:
            return None, f"no {rel} at {ref} {sha[:8]}"
        archive = _git(root, "show", f"{sha}:{archive_path(rel)}", timeout=timeout)
        return entries(text, archive, resolve), f"{ref} at {sha[:8]}"
    return None, "no origin/<default> here"


def _count(stamps: list[datetime], now: datetime) -> dict[str, int]:
    return {w: sum(1 for t in stamps if t > now - span) for w, span in WINDOWS.items()}


def reading(
    root: Path | str,
    now: datetime,
    *,
    with_history: bool = True,
    rel: str | None = None,
    resolve: Resolve | None = None,
) -> dict[str, Any]:
    """The ledger reading of one checkout (§4.4 *Repo facts*): `{path, entries, by_priority,
    by_kind, windows, recent, at}`, or `{path, error}` when the file cannot be read. The archive
    beside the ledger (`archive_path`; none there, nothing archived) and `resolve` — the home's
    `Registry` — derive each entry's `pickable`. `windows` is `{day|week|month: {opened, closed}}`
    from the history and `recent` the entries opened or closed in the longest window, newest first —
    both absent when the history was not read, and marked `history_error` when git could not be
    asked."""
    rel = rel or ledger_path(root)
    try:
        text = (Path(root) / rel).read_text(encoding="utf-8")
    except OSError as e:
        return {"path": rel, "error": f"{rel}: {e.strerror or e}", "at": now.isoformat()}
    try:
        archive: str | None = (Path(root) / archive_path(rel)).read_text(encoding="utf-8")
    except OSError:
        archive = None
    got = entries(text, archive, resolve)
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
