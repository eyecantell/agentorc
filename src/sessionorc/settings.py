"""The host agent's settings a person moves (design §5 `settings.yml`, §6 *Usage gate*, TD-100).

`settings.yml` sits beside `hosts.yml` in the agentorc home of each session host. The host agent
reads it on every tick and writes it only through its `set_settings` RPC — a person's own — so a
change takes effect on the next tick with no restart; a hand edit is read the same way. Its first
key is the usage gate's reserves, per profile, per window label as the adapter names them (§4.3):

    usage_gate:
      grind: {"5h": 30, wk: {per_day: 10}}

A **reserve** is what a person keeps back for their own interactive work, and the gate's **line**
is computed from it, never typed: a flat percent `30` makes the line `100 − 30`; a percent per day
`{per_day: 10}` makes it `100 − 10 × days_left`, where `days_left` is the whole days until the
window's `resets`, today counted whole (`ceil`, never below 1), clamped to 0–100. A per-day reserve
on a window that carries no `resets` makes no line, as no reserve does. Tool-agnostic: the labels
are whatever the adapter reports, and nothing here knows one by name.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from sessionorc import paths
from sessionorc.store import _atomic_write

DAY = timedelta(days=1)


def settings_file() -> Path:
    return paths.home() / "settings.yml"


def load(path: Path | None = None) -> dict[str, Any]:
    """The whole file, `{}` when it is missing or unreadable: a broken file gates nothing, which is
    the side a person would pick — a gate that pauses on a typo stops a team for no reason."""
    try:
        doc = yaml.safe_load((path or settings_file()).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return doc if isinstance(doc, dict) else {}


def save(doc: dict[str, Any], path: Path | None = None) -> None:
    p = path or settings_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, yaml.safe_dump(doc, sort_keys=True, default_flow_style=False))


def reserves(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`usage_gate` as `{profile: {label: reserve}}`, every entry that is not a valid reserve
    dropped (a hand edit can put anything there, and a malformed one must gate nothing)."""
    gate = doc.get("usage_gate")
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(gate, dict):
        return out
    for prof, by_label in gate.items():
        if not isinstance(by_label, dict):
            continue
        good = {}
        for label, r in by_label.items():
            try:
                good[str(label)] = parse_reserve(r)
            except ValueError:
                continue
        if good:
            out[str(prof)] = good
    return out


def parse_reserve(value: Any) -> int | dict[str, int]:
    """A reserve as stored: an int 0–100 (flat) or `{per_day: int 0–100}`. Anything else raises."""
    if isinstance(value, dict):
        if set(value) != {"per_day"}:
            raise ValueError(f"a per-day reserve is {{per_day: N}}, not {value!r}")
        return {"per_day": _pct(value["per_day"])}
    return _pct(value)


def _pct(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError(f"a reserve is a whole percent from 0 to 100, not {value!r}")
    return value


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def days_left(resets: datetime, now: datetime) -> int:
    return max(1, math.ceil((resets - now) / DAY))


def line(reserve: int | dict[str, int], resets: Any, now: datetime) -> int | None:
    """The window's line under this reserve, or None when it makes none (a per-day reserve on a
    window with no `resets`)."""
    if isinstance(reserve, dict):
        when = _when(resets)
        if when is None:
            return None
        return max(0, min(100, 100 - reserve["per_day"] * days_left(when, now)))
    return max(0, min(100, 100 - reserve))


def moves(reserve: int | dict[str, int], resets: Any, now: datetime) -> datetime | None:
    """When this window's line next changes: the next day boundary counted back from `resets` for
    a per-day reserve, or the reset itself — whichever is sooner, as design §6's `next` is."""
    when = _when(resets)
    if when is None:
        return None
    if isinstance(reserve, dict):
        step = when - (days_left(when, now) - 1) * DAY
        return step if step > now else when
    return when


def lines(by_label: dict[str, Any], windows: list[dict[str, Any]] | None, now: datetime) -> list[dict[str, Any]]:
    """Every reported window that has a reserve, with its line: `{label, pct, line, resets, next,
    reserve}` (`line` None where the reserve makes none). Windows without a reserve are left out."""
    out = []
    for w in windows or []:
        label = str(w.get("label"))
        if label not in by_label:
            continue
        r = by_label[label]
        nxt = moves(r, w.get("resets"), now)
        out.append(
            {
                "label": label,
                "pct": w.get("pct"),
                "line": line(r, w.get("resets"), now),
                "resets": w.get("resets"),
                "next": nxt.isoformat().replace("+00:00", "Z") if nxt else None,
                "reserve": r,
            }
        )
    return out


def crossed(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first window at or over its line, or None when every window is under its line."""
    for row in rows:
        if row["line"] is not None and isinstance(row["pct"], int | float) and row["pct"] >= row["line"]:
            return row
    return None
