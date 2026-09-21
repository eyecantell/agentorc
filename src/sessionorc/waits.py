"""The `wait` RPC's pure half (design §4.8 "Waking a manager", TD-049; moved into the host agent by
TD-052 step 3): which sessions a waiter watches, its per-caller cursor under `waits/`, and the
snapshot comparison. The agent owns the blocking and the wake decision; this module owns what a
wait compares, so it can be tested without a socket."""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

from sessionorc import naming, paths
from sessionorc.models import wake_digest

SCOPES = ("controlled", "all")


def wait_scope(sessions: list[dict[str, Any]], caller: str | None, scope: str) -> list[dict[str, Any]]:
    """Which sessions a waiter is watching. The default reuses the authority rule (§4.8): a lead
    waits on exactly the sessions it may act on — the ones whose `controllers` name it — so the
    wake and the authority cannot drift apart. A person at a terminal has no caller and watches
    everything, which is what `ao status` already shows them."""
    if scope == "all" or caller is None:
        return list(sessions)
    return [s for s in sessions if caller in (s.get("controllers") or [])]


def cursor_file(caller: str | None) -> pathlib.Path:
    # `slug` truncates, so two ids differing only past its limit would share one cursor and
    # cross-pollinate each other's history (review of PR #145). The digest makes the name unique;
    # the slug keeps it readable for whoever opens the directory. The name is the one the CLI
    # used before the move, so a lead's cursor survives the upgrade.
    who = caller or "person"
    return paths.waits_dir() / f"{naming.slug(who)}-{hashlib.sha256(who.encode()).hexdigest()[:8]}.json"


def read_cursor(caller: str | None) -> dict[str, str] | None:
    """What this caller last saw, `{}` for a caller that has never waited, and **None** for a
    cursor that exists but cannot be read.

    The three are not the same thing (review of PR #145). An empty cursor wakes on nothing, which
    is right for a first wait and exactly wrong for a corrupt one: it would swallow everything
    that changed since the last good write, silently, which is the one failure a wait must not
    have. `None` means *unknown*, and unknown wakes on everything in scope — a redundant wake,
    never a missed one, which is the trade this whole mechanism is built on."""
    f = cursor_file(caller)
    if not f.exists():
        return {}
    try:
        got = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def write_cursor(caller: str | None, cursor: dict[str, str]) -> None:
    f = cursor_file(caller)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(cursor))
        # Atomic, so a cursor is never half-written. Two waits for one caller at once (a lead's
        # tick overlapping a person's `ao wait` in another pane) still race on *which* complete
        # cursor lands, and the loser costs one redundant wake next time — self-healing, and the
        # reason this is a replace rather than a lock (review of PR #145).
        tmp.replace(f)
    except OSError:
        pass  # a cursor that cannot be saved costs one redundant wake, never a missed one


def wake_changes(
    before: dict[str, str] | None, now: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """The sessions whose wake digest differs from what this waiter last saw, and the new cursor.

    `now` must be the **complete** set in scope: a session in `before` and not in `now` is
    reported gone, so a partial view would report a healthy fleet as vanished and then drop it
    from the cursor. The agent passes its own records, which are complete by construction.

    Three cursors, three different answers (review of PR #145):

    - `{}` — never waited. Report nothing and record where we are, or the first call of every
      lead's life returns its whole fleet and it learns to ignore the result.
    - `None` — a cursor exists and could not be read. We do not know what was seen, so everything
      in scope is reported: a redundant wake, never a missed one.
    - anything else — the digests to compare against.
    """
    cursor = {s["id"]: wake_digest(s) for s in now}
    if before is None:
        return list(now), cursor
    if not before:
        return [], cursor
    changed = [s for s in now if before.get(s["id"]) != cursor[s["id"]]]
    changed += [{"id": sid, "gone": True} for sid in before if sid not in cursor]
    return changed, cursor
