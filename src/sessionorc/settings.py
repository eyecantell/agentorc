"""The host agent's settings a person moves (design §5 `settings.yml`, §6 *Usage gate*, TD-100).

`settings.yml` sits beside `hosts.yml` in the agentorc home of each session host. The host agent
reads it on every tick and writes it only through its `set_settings` RPC — a person's own — so a
change takes effect on the next tick with no restart; a hand edit is read the same way. Its first
key is the usage gate's reserves, per profile, per window label as the adapter names them (§4.3):

    usage_gate:
      grind: {"5h": 30, wk: {per_day: 10}}

Four keys since 2026-09-25 (§5 *The settings a person moves*, TD-146): `usage_gate`, `teams`, `repos`
and `person`. Each has a reader here that drops whatever is not valid — a hand edit can put anything
in the file, and a malformed entry must act on nothing — and a `parse_*` that raises, which is what
`set_settings` validates a write with. `person:` is the person's own and reaches no policy: the gate
and the tick read the file by key and never that one.

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


def read(path: Path | None = None) -> dict[str, Any] | None:
    """The whole file as it was read, or None when it is **absent or broken** — missing, unreadable,
    not YAML, not a mapping — where `load` answers `{}` for all of them. An empty file is `{}`.
    What the home sends its nodes (§4.4a *Settings, replicated*, TD-147): a broken file is not
    settings, and a node keeps the replica it has rather than take nothing over it."""
    try:
        doc = yaml.safe_load((path or settings_file()).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if doc is None:
        return {}
    return doc if isinstance(doc, dict) else None


def save(doc: dict[str, Any], path: Path | None = None) -> None:
    p = path or settings_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, yaml.safe_dump(doc, sort_keys=True, default_flow_style=False))


def ui_yml() -> Path:
    """The retired `ui.yml` (§5: its one key, `open_in:`, lives under `person:` now). One still on disk
    is named as *migrate* and never read."""
    return paths.home() / "ui.yml"


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


def amounts(doc: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """A metered profile's reserves under `usage_gate` (§6 *Usage gate*, TD-151): `{profile: {label:
    {value, unit}}}` from every entry written as an amount — `"$5"`, `"2M tok"` — and nothing else;
    `reserves` reads the percents and drops these, so each reader sees only its own billing's kind."""
    gate = doc.get("usage_gate")
    out: dict[str, dict[str, dict[str, Any]]] = {}
    if not isinstance(gate, dict):
        return out
    for prof, by_label in gate.items():
        if not isinstance(by_label, dict):
            continue
        good = {}
        for label, r in by_label.items():
            try:
                good[str(label)] = parse_amount(r)
            except ValueError:
                continue
        if good:
            out[str(prof)] = good
    return out


def parse_amount(value: Any) -> dict[str, Any]:
    """An amount per window (§6 *Usage gate*): `$n` in the account's currency, or `n tok` / `nM tok`
    in tokens. `{value, unit}` with `unit` `$` or `tok`; anything else raises."""
    text = str(value).strip() if isinstance(value, str) else ""
    try:
        if text.startswith("$"):
            n = float(text[1:].replace(",", ""))
            unit = "$"
        elif text.endswith("tok"):
            num = text[:-3].strip()
            scale = 1_000_000 if num[-1:] in ("M", "m") else 1
            n = float(num[:-1] if scale > 1 else num) * scale
            unit = "tok"
        else:
            raise ValueError
    except (ValueError, IndexError):
        raise ValueError(f"an amount is $n or n tok (nM tok), not {value!r}") from None
    if not math.isfinite(n) or n <= 0:
        raise ValueError(f"an amount is more than nothing, not {value!r}")
    return {"value": n, "unit": unit}


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
    if when is None or when <= now:
        return None  # a reset already past: a reading kept through failed polls (TD-087) — not known
    if isinstance(reserve, dict):
        step = when - (days_left(when, now) - 1) * DAY
        return step if step > now else when
    return when


def lines(
    by_label: dict[str, Any], windows: list[dict[str, Any]] | None, now: datetime, extra: int = 0
) -> list[dict[str, Any]]:
    """Every reported window that has a reserve, with its line: `{label, pct, line, resets, next,
    reserve}` (`line` None where the reserve makes none). Windows without a reserve are left out.
    `extra` is a team's reserve priority (§6 *Usage gate*, TD-146), added to the reserve on every
    window that has one — it lowers a line, never makes one — and carried on the row when it is set."""
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
                "line": _lowered(line(r, w.get("resets"), now), extra),
                "resets": w.get("resets"),
                "next": nxt.isoformat().replace("+00:00", "Z") if nxt else None,
                "reserve": r,
                **({"extra": extra} if extra else {}),
            }
        )
    return out


def _lowered(line_: int | None, extra: int) -> int | None:
    return None if line_ is None else max(0, line_ - max(0, extra))


def crossed(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first window at or over its line, or None when every window is under its line."""
    for row in rows:
        if row["line"] is not None and isinstance(row["pct"], int | float) and row["pct"] >= row["line"]:
            return row
    return None


# -- teams, repos, person (§5, TD-146) -------------------------------------------------------------

TEAM_KEYS = ("schedule", "until", "reserve")
TERMINAL_KEYS = ("size", "face", "copy_on_select")
TERMINAL_SIZE = (8, 32)  # a readable monospace size in px, either way of the Focus pane's default 13


def _keyed(doc: dict[str, Any], key: str, parse: Any) -> dict[str, Any]:
    """`doc[key]` as `{name: parse(value)}`, every name whose value does not parse dropped."""
    raw = doc.get(key)
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for name, value in raw.items():
        try:
            got = parse(value, drop=True)
        except ValueError:
            continue
        if got:
            out[str(name)] = got
    return out


def teams(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`teams:` as `{team: {schedule?, until?, reserve?}}` — each field that is not valid dropped."""
    return _keyed(doc, "teams", parse_team)


def repos(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`repos:` as `{repo: {promote: {auto: bool}}}` — the one switch a person flips per repo."""
    return _keyed(doc, "repos", parse_repo)


def person(doc: dict[str, Any]) -> dict[str, Any]:
    """`person:` with every field that is not valid dropped. Its shape is checked here; what an
    `open_in` template may point at (the scheme) is the UI's check, which draws the refusal."""
    try:
        return parse_person(doc.get("person") or {}, drop=True)
    except ValueError:
        return {}


def _fields(value: Any, keys: tuple[str, ...], what: str, drop: bool = False) -> dict[str, Any]:
    """`value` as a mapping of `keys`. An unknown key is refused — or, with `drop` (the reader's side),
    left out, so one stray hand-edited key costs that key and not the entry."""
    if not isinstance(value, dict):
        raise ValueError(f"{what} is a mapping of {', '.join(keys)}, not {value!r}")
    if unknown := sorted(set(map(str, value)) - set(keys)):
        if drop:
            return {k: v for k, v in value.items() if k in keys}
        raise ValueError(f"{what}: unknown key {', '.join(unknown)} (known: {', '.join(keys)})")
    return value


def _each(value: dict[str, Any], checks: dict[str, Any], drop: bool) -> dict[str, Any]:
    """Each present field through its check; with `drop`, a field that fails is left out rather than
    raising — the reader's side of the one set of rules."""
    out: dict[str, Any] = {}
    for key, check in checks.items():
        if key not in value:
            continue
        try:
            out[key] = check(value[key])
        except ValueError:
            if not drop:
                raise
    return out


def instant(value: Any) -> str:
    """An ISO instant with its offset, normalised to UTC `…Z` — the clients parse `06:00` and `+8h`
    in the caller's clock and hand an instant over, as `ao until` does. A YAML timestamp a hand edit
    left unquoted arrives as a `datetime` and is taken the same way."""
    if isinstance(value, datetime):
        t = value
    else:
        try:
            t = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"not an instant: {value!r}") from None
    if t.tzinfo is None:
        raise ValueError(f"an instant needs its timezone: {value!r}")
    return t.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_team(value: Any, drop: bool = False) -> dict[str, Any]:
    """One team's settings: `schedule` (TD-133's rule, a mapping kept as written until that build
    reads it), `until` (an instant, §6 *Team stop time*) and `reserve` (a flat percent added to the
    profile's reserve for the team's sessions, §6 *Usage gate*)."""

    def schedule(v: Any) -> dict[str, Any]:
        if not isinstance(v, dict) or not v:
            raise ValueError(f"schedule is a mapping (§6 *Schedule*), not {v!r}")
        return dict(v)

    value = _fields(value, TEAM_KEYS, "a team's settings", drop)
    return _each(value, {"schedule": schedule, "until": instant, "reserve": _pct}, drop)


def parse_repo(value: Any, drop: bool = False) -> dict[str, Any]:
    """One repo's settings: `promote: {auto: bool}` (§6 *Promote*; `run` and `check` stay in the
    repo's `.agentorc.yml`)."""

    def promote(v: Any) -> dict[str, bool]:
        _fields(v, ("auto",), "promote")
        if not isinstance(v.get("auto"), bool):
            raise ValueError(f"promote.auto is true or false, not {v.get('auto')!r}")
        return {"auto": v["auto"]}

    value = _fields(value, ("promote",), "a repo's settings", drop)
    return _each(value, {"promote": promote}, drop)


def parse_open_in(v: Any) -> str | dict[str, str]:
    """`open_in:`'s shape (§5): `vscode`, `none`, or `{label, url}` — any other word is kept too, so
    the UI can name it (`cursor` is refused there with the reason, not silently dropped here)."""
    if isinstance(v, str) and v.strip():
        return v.strip()
    if isinstance(v, dict) and set(v) == {"label", "url"} and all(isinstance(x, str) and x.strip() for x in v.values()):
        return {"label": v["label"].strip(), "url": v["url"].strip()}
    raise ValueError(f"open_in is vscode, none or {{label, url}}, not {v!r}")


def parse_person(value: Any, drop: bool = False) -> dict[str, Any]:
    """`person:` — `open_in` and `terminal: {size, face, copy_on_select}` (§5, goal 12, TD-164)."""

    def terminal(v: Any) -> dict[str, Any]:
        v = _fields(v, TERMINAL_KEYS, "terminal", drop)

        def size(n: Any) -> int:
            lo, hi = TERMINAL_SIZE
            if isinstance(n, bool) or not isinstance(n, int) or not lo <= n <= hi:
                raise ValueError(f"terminal.size is a whole number of px from {lo} to {hi}, not {n!r}")
            return n

        def face(f: Any) -> str:
            if not isinstance(f, str) or not f.strip() or len(f) > 80:
                raise ValueError(f"terminal.face is a font family's name, not {f!r}")
            return f.strip()

        def flag(b: Any) -> bool:
            if not isinstance(b, bool):
                raise ValueError(f"terminal.copy_on_select is true or false, not {b!r}")
            return b

        return _each(v, {"size": size, "face": face, "copy_on_select": flag}, drop)

    value = _fields(value, ("open_in", "terminal"), "person", drop)
    return _each(value, {"open_in": parse_open_in, "terminal": terminal}, drop)


def team_extra(doc: dict[str, Any], team: str) -> int:
    """The reserve priority of `team` (§6 *Usage gate*): 0 for no team, or a team with none."""
    return int((teams(doc).get(team) or {}).get("reserve") or 0) if team else 0


def merge(current: Any, change: dict[str, Any]) -> dict[str, Any]:
    """`change` laid over `current` one level down: a name set to None is removed, a field set to
    None is cleared, anything else replaces that field — so `{ao-grind: {until: None}}` clears the
    stop time and keeps the reserve. A name left with no fields goes."""
    out = (
        {str(k): dict(v) for k, v in (current or {}).items() if isinstance(v, dict)}
        if isinstance(current, dict)
        else {}
    )
    for name, fields in change.items():
        if fields is None:
            out.pop(str(name), None)
            continue
        mine = out.get(str(name), {})
        for key, value in fields.items():
            if value is None:
                mine.pop(key, None)
            else:
                mine[key] = value
        if mine:
            out[str(name)] = mine
        else:
            out.pop(str(name), None)
    return out
