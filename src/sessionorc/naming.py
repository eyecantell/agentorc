"""Session ids: `ao-<repo-or-dir>-<name>`, slugified, unique within the prefix (design §4.1)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

PREFIX = "ao-"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(text: str, *, max_len: int = 32) -> str:
    """Lowercase `[a-z0-9-]`; tmux treats `:`, `.` and whitespace specially in targets."""
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    s = s[:max_len].rstrip("-")
    return s or "x"


def scope_slug(directory: str | Path, repo: str | None) -> str:
    """The middle part: the repo's name, or the directory's basename when there is no repo."""
    if repo:
        return slug(Path(repo).name)
    return slug(Path(directory).name)


def session_id(directory: str | Path, repo: str | None, name: str, existing: Iterable[str]) -> str:
    """`ao-<scope>-<name>`, with `-2`, `-3`… appended on collision with any id in `existing`."""
    base = f"{PREFIX}{scope_slug(directory, repo)}-{slug(name)}"
    taken = set(existing)
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def is_ours(tmux_session_name: str) -> bool:
    return tmux_session_name.startswith(PREFIX)
