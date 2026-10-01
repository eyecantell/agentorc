"""Design §6 *Keeping a team running*, rule 12 (TD-247, TD-258): conventions relayed by the tick.
dev-cadence's SessionStart hook prints the new entries of `docs/cadence-changes.md` to a session
that starts, so only a member that outlives a change needs telling: the home runs
`scripts/cadence_changes.py --json` in each registry root that carries it, keeps on each member's
record the headings it has seen, and sends one `note` from `system` for those that landed after
the record was created. Nothing here is typed by a session, and the words are the script's `date`
and `title` fields.

The read (`read`) shells out and is called in a thread; the rest is pure."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT = "scripts/cadence_changes.py"
NAMED = 3  # the entries one note names; the rest are *and n more*


def has_script(root: Path | str) -> bool:
    """Whether `root` is a checkout the home can see that carries the script."""
    return (Path(root) / SCRIPT).is_file()


def read(root: Path | str, timeout: float = 60.0) -> dict[str, Any] | None:
    """One run of the script in `root`: `{ref, entries}`, or None for **no reading** — no script,
    an origin with no default branch (exit 2), a run that could not be made, or output that is not
    the script's."""
    if not has_script(root):
        return None
    try:
        cp = subprocess.run(
            ["python3", SCRIPT, "--json"], capture_output=True, text=True, timeout=timeout, cwd=str(root)
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if cp.returncode != 0:
        return None
    return parse(cp.stdout)


def parse(text: str) -> dict[str, Any] | None:
    """`{ref, entries: [{heading, date, title, landed}]}` from the script's `--json`, or None when
    it is not that. An entry with no heading is dropped; `landed` is kept as the script gave it,
    None included."""
    try:
        out = json.loads(text or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(out, dict) or not isinstance(out.get("entries"), list):
        return None
    entries = [
        {"heading": e["heading"], "date": e.get("date"), "title": e.get("title"), "landed": e.get("landed")}
        for e in out["entries"]
        if isinstance(e, dict) and isinstance(e.get("heading"), str) and e["heading"]
    ]
    return {"ref": str(out.get("ref") or "origin/main"), "entries": entries}


def _when(iso: Any) -> datetime | None:
    """An instant from the script's `landed` or a record's `created`; a naive one is read as UTC."""
    if not isinstance(iso, str) or not iso:
        return None
    try:
        at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def sort(
    entries: list[dict[str, Any]], seen: list[str] | None, created: str
) -> tuple[list[str], list[dict[str, Any]]] | None:
    """What one reading makes of one record: `(seen, told)` — the headings `conventions_seen` now
    holds, and the entries to tell, in the script's order. With no `seen` yet (the first reading
    after the create) the headings landed at or before `created` are written and nothing is told
    of them; an entry landed after `created` that `seen` does not hold is told and joins it; one
    whose `landed` the script could not read is left for the next reading. None when `created`
    cannot be read: nothing is written."""
    born = _when(created)
    if born is None:
        return None
    held = list(seen or [])
    told: list[dict[str, Any]] = []
    for e in entries:
        landed = _when(e.get("landed"))
        if e["heading"] in held or landed is None:
            continue
        held.append(e["heading"])
        if landed > born:
            told.append(e)
    return held, told


def _name(e: dict[str, Any]) -> str:
    date, title = e.get("date"), e.get("title")
    if date and title:
        return f'"{date} — {title}"'
    return f'"{str(e["heading"]).lstrip("# ").strip()}"'


def note(told: list[dict[str, Any]], ref: str) -> str:
    """The `system` note's fixed words, three entries named at most."""
    count = f"{len(told)} entr{'y' if len(told) == 1 else 'ies'}"
    named = ", ".join(_name(e) for e in told[:NAMED])
    more = f" and {len(told) - NAMED} more" if len(told) > NAMED else ""
    it = "it" if len(told) == 1 else "them"
    return f"docs/cadence-changes.md gained {count} since you started: {named}{more} — read {it} on `{ref}`, then go on"
