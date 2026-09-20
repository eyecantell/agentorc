"""Session ids: `ao-<repo-or-dir>-<name>`, slugified, unique within the prefix (design §4.1)."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

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


def base_id(directory: str | Path, repo: str | None, name: str) -> str:
    """The id a name claims in its scope — `ao-<scope>-<name>`, no suffix (design §4.1). A name
    identifies one session per scope (§9 invariant 12), so this is the identity the agent checks
    before it starts anything; `session_id` only adds a suffix for a tmux id nobody has a record of."""
    scope, nm = scope_slug(directory, repo), slug(name)
    return f"{PREFIX}{scope}" if nm == scope else f"{PREFIX}{scope}-{nm}"  # ao-ao-test-ao-test → ao-ao-test


def session_id(directory: str | Path, repo: str | None, name: str, existing: Iterable[str]) -> str:
    """`ao-<scope>-<name>`, with `-2`, `-3`… appended on collision with any id in `existing`."""
    base = base_id(directory, repo, name)
    taken = set(existing)
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def is_ours(tmux_session_name: str) -> bool:
    return tmux_session_name.startswith(PREFIX)


# -- addresses (design §4.4a, TD-057 step 1) --------------------------------------------------------
#
# The org-wide address of a record is `<id>@<host>`. A bare id means *the record's own host*, so a
# single-host store never contains `@`, and a `controllers`, `to` or `from` entry is stored
# qualified only when it names a session on another host. `qualify` is the one normaliser every
# id passes through on the way in; nothing else in the tree parses an address.


def split_address(address: str) -> tuple[str, str | None]:
    """`ao-x@laptop` → `("ao-x", "laptop")`; a bare id → `(id, None)`. Whitespace stripped."""
    text = str(address).strip()
    sid, sep, host = text.rpartition("@")
    if not sep:
        return text, None
    return sid.strip(), (host.strip() or None)


def qualify(address: str, *, local: str) -> str:
    """The stored form of an address seen from host `local`: bare for a session on this host,
    `id@host` for one elsewhere. `ao-x@kmaster` on kmaster is `ao-x`; `ao-x@laptop` stays."""
    sid, host = split_address(address)
    if host is None or host == local:
        return sid
    return f"{sid}@{host}"


# The keys under which a reply or a request carries session addresses (design §4.4a "Every address
# crosses in the reader's form", TD-057 step 5): what is rewritten when a call crosses the link.
# `text` and message ids are never touched; `add`/`remove` hold addresses only on `set_controllers`.
ADDRESS_KEYS = frozenset(
    {"id", "from", "to", "about", "controllers", "copies", "copies_failed", "delivered", "wake_budget_spent", "holder"}
)
ADDRESS_MAPS = frozenset({"forwarded"})  # id → id


def is_address(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def readdress(obj: Any, fn: Callable[[str], str], *, keys: frozenset[str] = ADDRESS_KEYS) -> Any:
    """`obj` with every session address under an address key rewritten by `fn` — recursively
    through dicts and lists, so a reply's entries and a wait's records are covered — and nothing
    else touched. A string that is not an id (`person`, a name) is left alone."""
    if isinstance(obj, list):
        return [readdress(x, fn, keys=keys) for x in obj]
    if not isinstance(obj, dict):
        return obj
    out: dict[str, Any] = {}
    for k, v in obj.items():
        if k in keys:
            if is_address(v):
                out[k] = fn(v)
            elif isinstance(v, list):
                out[k] = [fn(x) if is_address(x) else x for x in v]
            else:
                out[k] = readdress(v, fn, keys=keys)
        elif k in ADDRESS_MAPS and isinstance(v, dict):
            out[k] = {(fn(a) if is_address(a) else a): (fn(b) if is_address(b) else b) for a, b in v.items()}
        else:
            out[k] = readdress(v, fn, keys=keys)
    return out
