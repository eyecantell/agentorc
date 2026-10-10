"""The host agent's settings a person moves (design §5 `settings.yml`, §6 *Usage gate*, TD-100).

`settings.yml` sits beside `hosts.yml` in the agentorc home of each session host. The host agent
reads it on every tick and writes it only through its `set_settings` RPC — a person's own — so a
change takes effect on the next tick with no restart; a hand edit is read the same way. Its first
key is the usage gate's reserves, per profile, per window label as the adapter names them (§4.3):

    usage_gate:
      grind: {"5h": 30, wk: {per_day: 10}}

Six keys (§5 *The settings a person moves*, TD-146, TD-233, TD-319): `usage_gate`, `usage` (`max_age`),
`teams`, `repos`, `person` and `notify` (`telegram`). Each has a reader here that drops whatever is not
valid — a hand edit can put anything in the file, and a malformed entry must act on nothing — and a
`parse_*` that raises, which is what `set_settings` validates a write with. `person:` is the person's
own and reaches no policy: the gate and the tick read the file by key and never that one.

A **reserve** is what a person keeps back for their own interactive work, and the gate's **line**
is computed from it, never typed: a flat percent `30` makes the line `100 − 30`; a percent per day
`{per_day: 10}` makes it `100 − 10 × days_left`, where `days_left` is the whole days until the
window's `resets`, today counted whole (`ceil`, never below 1), clamped to 0–100. A per-day reserve
on a window that carries no `resets` makes no line, as no reserve does. Tool-agnostic: the labels
are whatever the adapter reports, and nothing here knows one by name.
"""

from __future__ import annotations

import math
import re
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
    window that has one — it lowers a line, never makes one — and carried on the row when it is set.
    A window whose `resets` has passed carries `unknown: "reset"` (§6 *Usage gate*: *a window that is
    unknown pauses nothing*): its number is the last window's, not this one's, so `crossed` skips it
    while the row still shows what was last read. A window the one reader marked (`usage.project`)
    carries its mark: `unknown: "rate"`, or `projected`, whose `pct` is then the projection."""
    out = []
    for w in windows or []:
        label = str(w.get("label"))
        if label not in by_label:
            continue
        r = by_label[label]
        nxt = moves(r, w.get("resets"), now)
        ended = (t := _when(w.get("resets"))) is not None and t <= now
        unknown = "reset" if ended else w.get("unknown")  # `rate`: past max_age with none to project by
        out.append(
            {
                "label": label,
                "pct": w.get("pct"),
                "line": _lowered(line(r, w.get("resets"), now), extra),
                "resets": w.get("resets"),
                "next": nxt.isoformat().replace("+00:00", "Z") if nxt else None,
                "reserve": r,
                **({"extra": extra} if extra else {}),
                **({"unknown": unknown} if unknown else {}),
                **({"age": w["age"]} if unknown == "rate" and "age" in w else {}),
                **({"projected": w["projected"]} if w.get("projected") and not ended else {}),
            }
        )
    return out


def _lowered(line_: int | None, extra: int) -> int | None:
    return None if line_ is None else max(0, line_ - max(0, extra))


def crossed(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first window at or over its line, or None when every window is under its line. A window
    past its reset (`unknown`) is never over: the gate has no number for it (§6 *Usage gate*)."""
    for row in rows:
        if row.get("unknown"):
            continue
        if row["line"] is not None and isinstance(row["pct"], int | float) and row["pct"] >= row["line"]:
            return row
    return None


# -- usage: max_age (§5 `usage.max_age`, §6 *A reading the gate can no longer trust*, TD-233) -------

USAGE_KEYS = ("max_age",)
MAX_AGE_DEFAULT = "1h"
MAX_AGE_BOUNDS = (5 * 60, 7 * 86400)  # five minutes to a week: shorter projects a reading still fresh
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_max_age(value: Any) -> str:
    """`off`, or an age as `90m`, `1h`, `2d` or whole seconds — kept as written, normalised to one
    unit. Raises on anything else, as every `parse_*` does."""
    if isinstance(value, str) and value.strip().lower() == "off":
        return "off"
    if isinstance(value, bool):
        raise ValueError(f"max_age is an age like 1h or 90m, or off — not {value!r}")
    m = re.fullmatch(r"\s*(\d+)\s*([smhd]?)\s*", str(value)) if isinstance(value, int | str) else None
    if m is None:
        raise ValueError(f"max_age is an age like 1h or 90m, or off — not {value!r}")
    secs = int(m.group(1)) * _UNITS[m.group(2) or "s"]
    lo, hi = MAX_AGE_BOUNDS
    if not lo <= secs <= hi:
        raise ValueError(f"max_age is from 5m to 7d, not {value!r}")
    return f"{m.group(1)}{m.group(2) or 's'}"


def _off(value: Any) -> Any:
    """A hand-written `max_age: off` as YAML reads it, `False`, is the word `off` (the RPC keeps
    refusing a boolean: only the file's own spelling is read this way)."""
    return "off" if value is False else value


def max_age(doc: dict[str, Any]) -> float | None:
    """The age in seconds past which the gate projects a reading, None under `off`. An unset or
    malformed value is the default hour: a hand edit's typo must not switch the projection off."""
    raw = doc.get("usage")
    raw = _off(raw.get("max_age")) if isinstance(raw, dict) else None
    try:
        kept = parse_max_age(MAX_AGE_DEFAULT if raw is None else raw)
    except ValueError:
        kept = MAX_AGE_DEFAULT
    if kept == "off":
        return None
    return float(int(kept[:-1]) * _UNITS[kept[-1]])


def usage(doc: dict[str, Any]) -> dict[str, Any]:
    """The `usage:` key as kept: `{max_age}`, the default when unset or malformed."""
    raw = doc.get("usage")
    try:
        return {"max_age": parse_max_age(_off(raw["max_age"]))} if isinstance(raw, dict) and "max_age" in raw else {}
    except ValueError:
        return {}


# -- teams, repos, person (§5, TD-146) -------------------------------------------------------------

TEAM_KEYS = ("schedule", "until", "reserve", "balance", "on_work", "flow")
ON_WORK = (
    "ask",
    "start",
    "off",
)  # §6 rule 8: what a team does when a lane of it gains work (wound down, or a finished member)
ON_WORK_DEFAULT = (
    "start"  # §6 rule 8, §5: what a team with no `on_work` key has (Paul, 2026-10-08, TD-457; `ask` until then)
)
BALANCE_KEYS = ("prs", "oldest", "review")
TERMINAL_KEYS = ("size", "face", "copy_on_select")
TERMINAL_SIZE = (8, 32)  # a readable monospace size in px, either way of the Focus pane's default 13
INBOX_KEYS = ("board_show",)
BOARD_SHOW_DEFAULT = "next:10"  # §4.5 screen 6 *The board's horizon*: what the page reads when nothing is set
BOARD_SHOW_NEXT = (1, 50)  # `next:<n>`, the soonest n per team
BOARD_SHOW_DAYS = (1, 365)  # `<n>d`, what falls due within n days
ATTACH_KEYS = ("max",)
ATTACH_MAX_DEFAULT = "256M"  # §4.4 *Attachment drop*: the most a Focus attachment may be when nothing is set
ATTACH_MAX = (1 << 20, 4 << 30)  # `1M` to `4G`: the bound is for the disk the sweep frees, not for the tool
COMPOSER = ("folded", "open")  # §4.5a *Focus composer* **the bar** (TD-491): the first is the default
PERSON_KEYS = ("open_in", "terminal", "inbox", "attach", "composer")


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
    """`teams:` as `{team: {schedule?, until?, reserve?, balance?, on_work?}}` — each field that is not
    valid dropped."""
    return _keyed(doc, "teams", parse_team)


def repos(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`repos:` as `{repo: {promote: {auto: bool}, pull: bool}}` — the switches a person flips per repo."""
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
    profile's reserve for the team's sessions, §6 *Usage gate*), `balance` (§6 *Balance*),
    `on_work` (§6 rule 8: `ask`, `start` or `off`) and `flow` (§4.9c: the name of the flow the team
    runs now — the client checks it against the team's `flows:`; the agent reads no definition)."""

    def schedule(v: Any) -> dict[str, Any]:
        if not isinstance(v, dict) or not v:
            raise ValueError(f"schedule is a mapping (§6 *Schedule*), not {v!r}")
        return dict(v)

    def on_work(v: Any) -> str:
        if v not in ON_WORK:
            raise ValueError(f"on_work is ask, start or off (§6 rule 8), not {v!r}")
        return str(v)

    def flow(v: Any) -> str:
        word = v.strip() if isinstance(v, str) else ""
        if not word or any(c.isspace() for c in word):
            raise ValueError(f"flow is the name of a flow the team lists (§4.9c), not {v!r}")
        return word

    value = _fields(value, TEAM_KEYS, "a team's settings", drop)
    checks = {
        "schedule": schedule,
        "until": instant,
        "reserve": _pct,
        "balance": lambda v: parse_balance(v, drop),
        "on_work": on_work,
        "flow": flow,
    }
    return _each(value, checks, drop)


def parse_balance(value: Any, drop: bool = False) -> dict[str, Any]:
    """A team's balance lines (§6 *Balance*, TD-239): `{prs: <int ≥ 1>, oldest: <n>[mhd], review: <bool>}`,
    each optional and any one enough. An empty mapping is refused — it draws no line, and *off* is
    the key's absence — as is one whose every line failed on the reader's side."""
    if isinstance(value, dict) and not value:
        raise ValueError("balance draws at least one line: prs, oldest or review (off is no balance key)")

    def prs(n: Any) -> int:
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(f"balance.prs is a whole number of open pull requests, 1 or more, not {n!r}")
        return n

    def oldest(d: Any) -> str:
        word = d.strip() if isinstance(d, str) else ""
        if not re.fullmatch(r"[1-9][0-9]*[mhd]", word):
            raise ValueError(f"balance.oldest is a duration such as 12h or 2d, not {d!r}")
        return word

    def review(b: Any) -> bool:
        if not isinstance(b, bool):
            raise ValueError(f"balance.review is true or false, not {b!r}")
        return b

    got = _each(_fields(value, BALANCE_KEYS, "balance", drop), {"prs": prs, "oldest": oldest, "review": review}, drop)
    if not got:
        raise ValueError("balance draws at least one line: prs, oldest or review")
    return got


REPO_KEYS = ("promote", "pull")


def parse_repo(value: Any, drop: bool = False) -> dict[str, Any]:
    """One repo's settings: `promote: {auto: bool}` (§6 *Promote*; `run` and `check` stay in the
    repo's `.agentorc.yml`) and `pull: bool` (§6 *Pull*; absent is true)."""

    def promote(v: Any) -> dict[str, bool]:
        _fields(v, ("auto",), "promote")
        if not isinstance(v.get("auto"), bool):
            raise ValueError(f"promote.auto is true or false, not {v.get('auto')!r}")
        return {"auto": v["auto"]}

    def pull(v: Any) -> bool:
        if not isinstance(v, bool):
            raise ValueError(f"pull is true or false, not {v!r}")
        return v

    value = _fields(value, REPO_KEYS, "a repo's settings", drop)
    return _each(value, {"promote": promote, "pull": pull}, drop)


def parse_open_in(v: Any) -> str | dict[str, str]:
    """`open_in:`'s shape (§5): `vscode`, `none`, or `{label, url}` — any other word is kept too, so
    the UI can name it (`cursor` is refused there with the reason, not silently dropped here)."""
    if isinstance(v, str) and v.strip():
        return v.strip()
    if isinstance(v, dict) and set(v) == {"label", "url"} and all(isinstance(x, str) and x.strip() for x in v.values()):
        return {"label": v["label"].strip(), "url": v["url"].strip()}
    raise ValueError(f"open_in is vscode, none or {{label, url}}, not {v!r}")


def parse_person(value: Any, drop: bool = False) -> dict[str, Any]:
    """`person:` — `open_in`, `terminal: {size, face, copy_on_select}` (§5, goal 12, TD-164),
    `inbox: {board_show}` (TD-220), `attach: {max}` (TD-478) and `composer` (TD-500)."""

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

    def inbox(v: Any) -> dict[str, Any]:
        return _each(_fields(v, INBOX_KEYS, "inbox", drop), {"board_show": parse_board_show}, drop)

    def attach(v: Any) -> dict[str, Any]:
        return _each(_fields(v, ATTACH_KEYS, "attach", drop), {"max": parse_attach_max}, drop)

    value = _fields(value, PERSON_KEYS, "person", drop)
    return _each(
        value,
        {"open_in": parse_open_in, "terminal": terminal, "inbox": inbox, "attach": attach, "composer": parse_composer},
        drop,
    )


def parse_composer(v: Any) -> str:
    """`person.composer` (§5, §4.5a *Focus composer* **the bar**, TD-500): `folded`, Focus's composer one
    bar under the terminal, or `open`, drawn open under it as before the bar."""
    if v in COMPOSER:
        return v
    raise ValueError(f"composer is {' or '.join(COMPOSER)}, not {v!r}")


def parse_attach_max(v: Any) -> str:
    """`person.attach.max` (§5, §4.4 *Attachment drop*, TD-478): a count of MiB or GiB, `<n>M` or
    `<n>G`, from `1M` to `4G`; kept as written with its leading zeros dropped."""
    m = re.fullmatch(r"([0-9]+)([MG])", v.strip()) if isinstance(v, str) else None
    if m and ATTACH_MAX[0] <= attach_bytes(f"{int(m.group(1))}{m.group(2)}") <= ATTACH_MAX[1]:
        return f"{int(m.group(1))}{m.group(2)}"
    raise ValueError(f"attach.max is <n>M or <n>G from 1M to 4G, not {v!r}")


def attach_bytes(word: str) -> int:
    """A parsed `attach.max` (`256M`, `1G`) as bytes."""
    return int(word[:-1]) << (20 if word[-1] == "M" else 30)


def attach_max(doc: dict[str, Any]) -> int:
    """The bound on a Focus attachment in bytes: `person.attach.max`, or `ATTACH_MAX_DEFAULT`."""
    return attach_bytes(person(doc).get("attach", {}).get("max", ATTACH_MAX_DEFAULT))


def parse_board_show(v: Any) -> str:
    """`person.inbox.board_show` (§5, §4.5 screen 6 *The board's horizon*, TD-220): which board items the
    Inbox lists before they are due — `next:<n>` per team (n from 1 to 50), `due`, `<n>d` (n from 1 to
    365) or `all`. What is due is shown under every mode; this moves only what comes up ahead."""
    word = v.strip() if isinstance(v, str) else ""
    if word in ("due", "all"):
        return word
    for pattern, (lo, hi) in ((r"next:([0-9]+)", BOARD_SHOW_NEXT), (r"([0-9]+)d", BOARD_SHOW_DAYS)):
        if (m := re.fullmatch(pattern, word)) and lo <= int(m.group(1)) <= hi:
            return pattern.replace("([0-9]+)", str(int(m.group(1))))
    raise ValueError(
        f"inbox.board_show is next:<n> (n from {BOARD_SHOW_NEXT[0]} to {BOARD_SHOW_NEXT[1]}), due, "
        f"<n>d (n from {BOARD_SHOW_DAYS[0]} to {BOARD_SHOW_DAYS[1]}) or all, not {v!r}"
    )


# -- notify: telegram (§5 `notify:`, §4.10 *Told on Telegram when nobody is looking*, TD-319) ----------

NOTIFY_KEYS = ("telegram",)
TELEGRAM_KEYS = ("on", "secrets", "link")
_SECRETS = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_LINK = re.compile(r"https?://[^\s/]+(/\S*)?")


def parse_telegram(value: Any, drop: bool = False) -> dict[str, Any]:
    """`notify.telegram`: `on` (true or false), `secrets` — the Doppler `project/config` that holds
    `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, a name and never a value — and `link`, the
    `http(s)://` address the person's phone reaches the UI at, kept without a trailing slash."""

    def on(b: Any) -> bool:
        if not isinstance(b, bool):
            raise ValueError(f"telegram.on is true or false, not {b!r}")
        return b

    def secrets(s: Any) -> str:
        if not isinstance(s, str) or not _SECRETS.fullmatch(s.strip()):
            raise ValueError(f"telegram.secrets is a Doppler project/config like samscrape/prd, not {s!r}")
        return s.strip()

    def link(u: Any) -> str:
        if not isinstance(u, str) or not _LINK.fullmatch(u.strip()):
            raise ValueError(f"telegram.link is an http:// or https:// address, not {u!r}")
        return u.strip().rstrip("/")

    return _each(_fields(value, TELEGRAM_KEYS, "telegram", drop), {"on": on, "secrets": secrets, "link": link}, drop)


def notify(doc: dict[str, Any]) -> dict[str, Any]:
    """`notify:` as kept: `{telegram: {on?, secrets?, link?}}`, every field that is not valid dropped."""
    raw = doc.get("notify")
    tg = raw.get("telegram") if isinstance(raw, dict) else None
    try:
        got = parse_telegram(tg, drop=True) if tg is not None else {}
    except ValueError:
        got = {}
    return {"telegram": got} if got else {}


def telegram(doc: dict[str, Any]) -> dict[str, Any] | None:
    """The switch as the tick reads it: `{secrets, link}` when `on` is true and secrets are set, else
    None — absent, off, or a hand edit with no secrets sends nothing."""
    tg = notify(doc).get("telegram") or {}
    if tg.get("on") is not True or not tg.get("secrets"):
        return None
    return {"secrets": tg["secrets"], "link": tg.get("link") or ""}


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
