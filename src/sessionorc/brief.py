"""A brief filled again from what it was made from (design §6 *Keeping a team running* rule 7,
TD-217 slice 2).

The client that composes a brief hands `create`, beside the text, `prompt_from = {base, slots,
prefix?}`: `base` a file, each slot in the order it is filled either `{file: <path>}` or `{text: …}`,
and `prefix` put in front. This module knows a file and a slot and nothing of a role, a template or
a team. A replay fills `base` again with the files **as merged** — a file inside a git checkout is
read at `origin/<default>:<path>` as last fetched, so a branch checked out there is never a running
team's brief; any other file (the installed template) from disk. Each file read is named with its
git blob sha, which is what the record's `brief.sources` keeps and what the tick compares later."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from sessionorc.models import now_iso

GIT_TIMEOUT = 10.0
# origin's default branch as last fetched; the two usual names when `origin/HEAD` was never set
DEFAULT_REFS = ("origin/HEAD", "origin/main", "origin/master")


class Unreadable(Exception):
    """A file the brief was made from that cannot be read now; the replay takes the stored prompt."""


def blob_sha(data: bytes) -> str:
    """Git's blob id of `data`, the same for a file read from disk and one read from a ref."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 — git's own id, not a secret


def _git(directory: Path, *args: str) -> bytes | None:
    try:
        cp = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return cp.stdout if cp.returncode == 0 else None


def read(path: str, merged: bool = True) -> tuple[str, str]:
    """`(text, sha)` of one file. With `merged`, a file inside a git checkout that has an origin is
    read at origin's default branch; a file that is not there (untracked, or only on a branch) is
    `Unreadable`, never the working tree's copy. A file outside any checkout, or in one with no
    origin, is read from disk."""
    p = Path(path)
    if merged and p.parent.is_dir():
        top = _git(p.parent, "rev-parse", "--show-toplevel")
        if top is not None:
            root = Path(top.decode().strip())
            for ref in DEFAULT_REFS:
                if _git(root, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}") is None:
                    continue
                try:
                    rel = p.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    break
                data = _git(root, "show", f"{ref}:{rel}")
                if data is None:
                    raise Unreadable(f"{rel} is not on {ref} in {root}")
                return data.decode("utf-8", errors="replace"), blob_sha(data)
    try:
        data = p.read_bytes()
    except OSError as e:
        raise Unreadable(f"{path}: {e.strerror or e}") from None
    return data.decode("utf-8", errors="replace"), blob_sha(data)


def fill(prompt_from: dict[str, Any], merged: bool = True) -> tuple[str, list[dict[str, str]]]:
    """The prompt `prompt_from` makes now, and `[{path, sha}]` for every file read: `base`, then
    each slot in order by plain replacement of its name — a file's text stripped, `none` when
    empty — then `prefix` in front. `Unreadable` when a file cannot be read or the shape is not
    one a client hands."""
    base = prompt_from.get("base") if isinstance(prompt_from, dict) else None
    slots = prompt_from.get("slots") if isinstance(prompt_from, dict) else None
    if not isinstance(base, str) or not base or not isinstance(slots, dict):
        raise Unreadable("prompt_from has no base or no slots")
    text, sha = read(base, merged)
    sources = [{"path": base, "sha": sha}]
    for slot, spec in slots.items():
        if isinstance(spec, dict) and isinstance(spec.get("file"), str):
            got, sha = read(spec["file"], merged)
            sources.append({"path": spec["file"], "sha": sha})
            value = got.strip() or "none"
        elif isinstance(spec, dict) and isinstance(spec.get("text"), str):
            value = spec["text"]
        else:
            raise Unreadable(f"slot {slot} is neither a file nor a text")
        text = text.replace(str(slot), value)
    prefix = prompt_from.get("prefix")
    return (prefix if isinstance(prefix, str) else "") + text, sources


def record(prompt_from: dict[str, Any], merged: bool = True) -> dict[str, Any] | None:
    """The record's `brief` (design §6 rule 7): `{at, sources}` from a `fill`, or None when a file
    cannot be read — nothing is compared then, and nothing is claimed about it."""
    try:
        return {"at": now_iso(), "sources": fill(prompt_from, merged)[1]}
    except Unreadable:
        return None
