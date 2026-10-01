"""Held paths and the read a held PR waits for (design §4.9b *The reader*, TD-093; §6 *Keeping a
team running*, rule 11, TD-247, TD-258). A record's `review` names a reader and the path globs it
holds; `ao pr held <n>` is the author's own read of a PR's files against them, and rule 11 is the
home's read of the same thing once the PR has merged: a held PR with no reply from its reader in
the mail the home holds is a crossing, kept on the record's `held_missed`.

`held:` is a list of path globs: a pattern names paths from the repo root, `**` spans any number
of directories, `*` and `?` stay inside one, and a pattern ending in `/` names everything under
that directory. Nothing else is special — `[abc]` is those five characters, not a class.

The read of a PR (`pr_read`) shells out and is called in a thread by the tick; the rest is pure."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from sessionorc import naming
from sessionorc.models import PERSON, Session

GH_TIMEOUT = 30.0
# How long after the merge a held PR may go without its reader's reply before it is a crossing: the
# reader merges first and replies after, and the home may read in between.
GRACE = timedelta(minutes=15)
NAMED = 3  # the paths one line names; the rest are *and n more*


@lru_cache(maxsize=256)
def _pattern(glob: str) -> re.Pattern[str]:
    g = glob.strip().lstrip("/")
    if g.endswith("/"):
        g = g.rstrip("/") + "/**"  # `src/` and `src/**/` both mean everything under src
    out, i = [], 0
    while i < len(g):
        if g.startswith("**/", i):
            out.append("(?:.*/)?")  # any number of directories, none included
            i += 3
        elif g.startswith("**", i):
            out.append(".*")
            i += 2
        elif g[i] == "*":
            out.append("[^/]*")
            i += 1
        elif g[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(g[i]))
            i += 1
    return re.compile("".join(out))


def matches(path: str, glob: str) -> bool:
    """Whether a repo-relative path is named by one `held:` glob."""
    return _pattern(glob).fullmatch(path.lstrip("/")) is not None


def held_paths(files: Iterable[str], review: dict[str, Any] | None) -> list[str]:
    """The PR's changed files that its record's `review` holds — empty when there is no `review`,
    which is the case §4.9b says merges as the cadence does."""
    if not review:
        return []
    globs = list(review.get("held") or ["**"])
    return [f for f in files if any(matches(f, g) for g in globs)]


def pr_read(pr: int, cwd: str | None = None) -> tuple[list[str], datetime | None]:
    """The PR's changed files and when it merged (None while it has not), from `gh` in the checkout
    it belongs to. A `RuntimeError` saying why when `gh` cannot say: *not held* is never guessed
    from a failed read."""
    try:
        cp = subprocess.run(
            ["gh", "pr", "view", str(pr), "--json", "files,changedFiles,mergedAt"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=GH_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"gh could not read PR #{pr}: {e}") from None
    if cp.returncode != 0:
        why = (cp.stderr or cp.stdout).strip().splitlines()
        raise RuntimeError(f"gh could not read PR #{pr}: {why[-1] if why else cp.returncode}")
    try:
        got = json.loads(cp.stdout)
        files = [str(f["path"]) for f in got.get("files") or []]
        total = int(got.get("changedFiles") or 0)
        at = got.get("mergedAt")
        merged = datetime.fromisoformat(str(at).replace("Z", "+00:00")) if at else None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError(f"gh gave PR #{pr}'s files in a shape this does not read") from None
    if total > len(files):
        # `gh` returns one page of a large PR's files: a held path past it would read as *not held*
        raise RuntimeError(f"gh listed {len(files)} of PR #{pr}'s {total} files: too many to judge; ask the reader")
    if merged is not None and merged.tzinfo is None:
        merged = merged.replace(tzinfo=UTC)
    return files, merged


def pr_files(pr: int, cwd: str | None = None) -> list[str]:
    """The PR's changed files alone, as `ao pr held` reads them."""
    return pr_read(pr, cwd)[0]


# ── rule 11: merged without its read ────────────────────────────────────────────────────────────


def _bare(address: str) -> str:
    return naming.split_address(address)[0]


def read_by(s: Session, pr: int) -> str | None:
    """Who read the PR, from the record's own mail: the sender of a `reply` on the thread of an
    `ask` of this record's that carries `pr`, when that sender is one the thread's asks named. For
    `reader: person` only the person's reply is the read; for `reader: techlead` the seat's is, and
    so is the person's, since an ask the seat left past its bound is the person's on the same
    thread (§4.9b). The reply is the read whatever it says. Mail is pruned, so the reply rule 10
    kept on the PR's `checks` entry (`read_by`) counts as well."""
    reader = str((s.review or {}).get("reader") or "")
    named: dict[str, set[str]] = {}
    for e in s.outbox:
        if e.kind == "ask" and e.pr == pr:
            named.setdefault(e.root or e.id, set()).update(_bare(x) for x in e.to)
    for r in s.inbox:
        to = named.get(r.root or "") if r.kind == "reply" else None
        if to is None or _bare(r.from_) not in to:
            continue
        if reader != "person" or r.from_ == PERSON:
            return r.from_
    kept = next((c.get("read_by") for c in s.checks if c.get("pr") == pr), None)
    if kept and (reader != "person" or kept == PERSON):
        return str(kept)
    return None


def crossing(pr: int, paths: list[str], at: str) -> dict[str, Any]:
    """One entry of `held_missed`: `{pr, at, paths}`; `told` joins it once the member was."""
    return {"pr": pr, "at": at, "paths": list(paths)}


def _paths(c: dict[str, Any]) -> str:
    paths = [str(p) for p in c.get("paths") or []]
    more = f" and {len(paths) - NAMED} more" if len(paths) > NAMED else ""
    return ", ".join(f"`{p}`" for p in paths[:NAMED]) + more


def _whose(reader: str) -> str:
    return "the person's" if reader == "person" else "the techlead's"


def said(c: dict[str, Any], reader: str) -> str:
    return f"PR #{c['pr']} touched held paths ({_paths(c)}) and merged without {_whose(reader)} read"


def line(c: dict[str, Any], reader: str) -> str:
    """The fixed line typed into an idle member's composer."""
    to = "person" if reader == "person" else "<seat>"
    return (
        f"[agentorc] {said(c, reader)} — a held PR waits for "
        f'`ao msg --kind ask --pr <n> {to} "…"` and the reply before the merge'
    )


def untold(held_missed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in held_missed if not c.get("told")]


def clause(held_missed: list[dict[str, Any]], reader: str) -> str:
    """The clause an `ao` reply carries for each crossing the member has not been told of."""
    return "; ".join(said(c, reader) for c in untold(held_missed))


def note(c: dict[str, Any], reader: str, member: str) -> str:
    """The `system` note to the person, FYI: a gate was passed, and the person knows each time."""
    return f"{member}: {said(c, reader)}. The home undoes nothing: a revert is your word."
