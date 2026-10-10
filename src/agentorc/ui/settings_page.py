"""The Settings page's view (design §4.5 screen 8, §4.5a *Settings page* rows; TD-148): the one page
that **writes a setting** — a value a person moves without redefining anything, in the home's
`settings.yml`, written only through `set_settings` — and shows every other configured value with
where it lives.

Everything here is pure: the route in `app.py` hands over what the host agent answered (the
`settings` read, `usage`, `host`) and what the clients read from their own files (`profiles.yml`,
`hosts.yml`, the org's definitions, each registered repo's `.agentorc.yml`), and gets back the
sections the template draws. Nothing here reads `settings.yml`: on a node the agent's read serves the
replica and the writes forward to the home (§4.4a *Settings, replicated*), which only the agent knows.

A value a file does not set is drawn with its default and marked *default*; every file card carries
the *i* mark's words — the file's path, when it is re-read, who edits it — from `FILES`, which is
`ao settings --where`'s table in the page's words.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from agentorc import profiles as profiles_mod
from agentorc import repoconfig, teamrun
from sessionorc import hosts
from sessionorc import settings as settings_mod
from sessionorc import spend as spend_mod
from sessionorc.models import tokens_short

from .common import USAGE_UNKNOWN, _age, usage_clock, usage_read

# The *i* mark of each file card (§4.5a *Settings page: read-only values and the i mark*): when the
# file is re-read and who edits it. A host's name, its `home:` and its identity mode are read once,
# at the agent's start, so the hosts card says *restart the host agent to apply* for those three.
FILES = {
    "settings": ("every tick", "written only through set_settings — this page, ao gate, ao team — never by hand"),
    "hosts": ("name, home: and identity at the host agent's start — restart the host agent to apply; the rest on use",
              "by hand"),
    "profiles": ("on every use", "by hand"),
    "org": ("by the clients on every use; never the host agent", "by hand"),
    "repo": ("by the clients on every use; promote: alone also by the host agent at the home, every five minutes",
             "by PR"),
}  # fmt: skip

# The browser's own keys (§4.5a *Settings page: You*, *this browser*): drawn with what they hold,
# each beside the control that already sets it; **Reset this browser** clears every `ao.*` key.
BROWSER_KEYS = (
    ("theme", "theme", "the ◐ toggle in the top bar"),
    ("inboxfyi", "Inbox: FYI open", "the section's own fold"),
    ("inboxanswered", "Inbox: Answered open", "the section's own fold"),
)


# a price's kind as the metered card's badge says it (§4.5a: *$3 in / $15 out per M*)
PRICE_WORDS = {"input": "in", "output": "out", "cache_read": "cache read", "cache_write": "cache write"}


def _is_num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def reserve_text(r: Any) -> str:
    """A reserve as the field takes it back: `30`, `10/day`, or "" for none (`ao gate`'s forms)."""
    if isinstance(r, dict) and isinstance(r.get("per_day"), int):
        return f"{r['per_day']}/day"
    return "" if r is None else str(r)


def parse_reserve_text(text: Any) -> int | dict[str, int] | str | None:
    """What a reserve field sends: `30`, `10/day`, empty to clear — `ao gate`'s forms, the same
    refusal — or a metered card's amount, `$5` or `20M tok`, which goes as written for the agent to
    check against the profile's billing. Raises ValueError with the words the field shows in place."""
    t = str(text if text is not None else "").strip()
    if not t:
        return None
    if t.startswith("$") or t.endswith("tok"):
        return t
    per_day = t.endswith("/day")
    n = t.removesuffix("/day").strip()
    if not n.isdigit():
        raise ValueError(f"a reserve is a whole percent (30) or a percent per day (10/day), not {t!r}")
    return {"per_day": int(n)} if per_day else int(n)


def _when(iso: Any) -> datetime | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def spend_text(w: Mapping[str, Any] | None, unit: str = "") -> str:
    """A metered window's spend as the chip draws it (§4.5a): *spent $3.20 · 64% · resets 00:00*, in
    tokens where the amount is tokens or nothing was priced; *no reading yet* before one."""
    spent = (w or {}).get("spent")
    if not isinstance(spent, dict):
        return "no reading yet"
    cost = spent.get("cost")
    got = f"{tokens_short(int(spent.get('total') or 0))} tok" if unit == "tok" or cost is None else f"${cost:,.2f}"
    out = f"spent {got}"
    if isinstance(w.get("pct"), int):
        out += f" · {w['pct']}%"
    if resets := _when(w.get("resets")):
        out += f" · resets {resets.astimezone():%H:%M}"
    return out


def line_text(row: Mapping[str, Any] | None, now: datetime | None = None) -> str:
    """The line a reserve makes today, as `ao gate` prints it and the chip will draw it: *→ line
    70%*, *→ line 60% · 4 days left · moves Thu 07:00*, or why there is none; past `usage.max_age`,
    the projection the gate reads, *· projected 96%*, or *· no rate to project by*."""
    if not row:
        return "no line"
    if row.get("unknown") == "reset":
        return "unknown since its reset — pauses nothing until a new reading"
    if row.get("line") is None:
        return "no line — the window reports no reset"
    out = f"→ line {row['line']:g}%"
    if isinstance(row.get("projected"), dict) and _is_num(row.get("pct")):
        # past `usage.max_age` (§6 *A reading the gate can no longer trust*, TD-233): what the gate reads
        out += f" · projected {row['pct']:g}%"
    elif row.get("unknown") == "rate":
        out += " · no rate to project by"
    r = row.get("reserve")
    resets = _when(row.get("resets"))
    if isinstance(r, dict) and resets:
        now = now or datetime.now(UTC)
        left = max(1, math.ceil((resets - now) / timedelta(days=1)))
        out += f" · {left} day{'s' if left != 1 else ''} left"
    nxt = _when(row.get("next"))
    if nxt:
        out += f" · moves {nxt.astimezone():%a %H:%M}"
    return out


def _account(p: profiles_mod.Profile, reading: Mapping[str, Any] | None) -> tuple[str, str]:
    """The account a profile's card is grouped under, as the chip names it (TD-122): the reading's
    `<tool> · <account>` when there is one, else the profile's own `account:` and adapter."""
    tool = str((reading or {}).get("tool") or p.adapter)
    acct = str((reading or {}).get("account") or p.account or p.name)
    return f"{tool} · {acct}", acct


def reading_age(w: Mapping[str, Any], read: Mapping[str, Any] | None, now: datetime) -> str:
    """A window's reading as the chip's hover gives it (§4.5a *Settings page: Usage*, *The reading's
    age*; TD-233 slice 1): *week 88% · read 6h ago, asked of the endpoint*; past its reset or past
    `USAGE_UNKNOWN`, *unknown since 22:21 (was 88%)*. Empty for a reading with no time on it."""
    pct = f"{w['pct']:g}%" if _is_num(w.get("pct")) else "?"
    resets = _when(w.get("resets"))
    if resets is not None and resets <= now:
        return f"unknown since its reset at {usage_clock(resets, now)} (was {pct})"
    if read is None:
        return ""
    if read["secs"] > USAGE_UNKNOWN:
        return f"unknown since {read['clock']} (was {pct}), {read['source']}"
    return f"{pct} · read {read['age']} ago, {read['source']}"


def usage_cards(
    profiles: Mapping[str, profiles_mod.Profile],
    gate: Mapping[str, Any] | None,
    usage: Mapping[str, Any] | None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """**Usage** (§4.5 screen 8): a card per profile, grouped under its account as the chip is, a
    row per window label the adapter reports — each with its reserve as the field takes it, the line
    it makes today, and what the page's preview needs to draw a new line before the press lands
    (`resets`). A profile with no reading yet draws the labels it has reserves for, and says the
    labels are not checked. A **metered** profile's card (§4.2a) carries the home's three windows,
    each taking an amount (`$5`, `20M tok`), with the account's spend beside it."""
    now = now or datetime.now(UTC)
    gate, usage = gate or {}, usage or {}
    groups: dict[str, dict[str, Any]] = {}
    for name, p in profiles.items():
        reading = usage.get(name) if isinstance(usage.get(name), dict) else None
        g = gate.get(name) if isinstance(gate.get(name), dict) else {}
        key, acct = _account(p, reading)
        windows = [w for w in (reading or {}).get("windows") or [] if isinstance(w, dict) and w.get("label")]
        reserves = g.get("reserves") or {}
        rows_by = {str(r.get("label")): r for r in g.get("windows") or [] if isinstance(r, dict)}
        read = usage_read(reading, now)
        rows = []
        for w in windows:
            label = str(w["label"])
            rows.append(
                {
                    "label": label,
                    "value": reserve_text(reserves.get(label)),
                    "line": line_text(rows_by.get(label), now) if label in reserves else "no line",
                    "resets": str(w.get("resets") or ""),
                    "pct": w.get("pct") if _is_num(w.get("pct")) else None,
                    "age": reading_age(w, read, now),
                }
            )
        for label, r in reserves.items():  # a reserve on a label no reading has shown yet
            if not any(x["label"] == label for x in rows):
                rows.append(
                    {"label": label, "value": reserve_text(r), "line": "no reading yet", "resets": "", "pct": None}
                )
        metered = p.billing == "metered"
        badge = f"account {acct} · {p.adapter}"
        if metered:
            prices = " / ".join(f"${v:g} {PRICE_WORDS.get(k, k)}" for k, v in p.prices.items())
            badge += f" · metered · {prices + ' per M' if prices else 'tokens'}"
            # the home's three windows, each an amount (§4.5a *Settings page: Usage*): the account's
            # spend beside it, as the chip draws it
            by_label = {str(w["label"]): w for w in windows}
            rows = []
            for label in spend_mod.LABELS:
                value = str(reserves.get(label) or "")
                rows.append(
                    {
                        "label": label,
                        "value": value,
                        "line": spend_text(by_label.get(label), "tok" if value.endswith("tok") else ""),
                        "resets": str((by_label.get(label) or {}).get("resets") or ""),
                        "pct": None,
                    }
                )
        groups.setdefault(key, {"account": key, "cards": []})["cards"].append(
            {
                "profile": name,
                "badge": badge,
                "metered": metered,
                "rows": rows,
                "unchecked": not windows and not metered,
            }
        )
    return list(groups.values())


def balance_card(
    name: str,
    bal: Any,
    sessions: list[dict[str, Any]] | None,
    repos: Mapping[str, Any] | None,
    host: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """**balance** on a team's card (§4.5a *Settings page: Teams*, §6 *Balance*, TD-239): the switch,
    the three fields as `settings.yml` holds them — a line not drawn is an empty field — and under
    them the repo's numbers as they read now, in `ao team balance`'s lines, so a line is set against
    what it would have done today. `was` is the value as the form would send it, which is how the
    page tells a Save that moved it from one that did not. `mark` is the card's **over its line**
    note while the home's mark stands — from the `repos` reading and the home's `host` read, whose
    `balance` carries a mark with no `repo` too (TD-330)."""
    bal = {k: bal[k] for k in settings_mod.BALANCE_KEYS if k in bal} if isinstance(bal, Mapping) else {}
    if not bal.get("review"):
        bal.pop("review", None)  # off is no key, as the file reads
    readings = {str(k): v for k, v in (repos or {}).items() if isinstance(v, dict)}
    now = teamrun.balance_now(name, list(sessions or []), readings)
    mark = teamrun.balance_marks(readings, dict(host or {})).get(name) if bal else None
    return {
        "on": bool(bal),
        "prs": str(bal.get("prs") or ""),
        "oldest": str(bal.get("oldest") or ""),
        "review": bool(bal.get("review")),
        "was": json.dumps(bal or None, separators=(",", ":")),
        "live": bool(now["members"]),
        "now": [line.strip() for line in teamrun.balance_rows(bal, now)],
        "mark": teamrun.balance_note(mark) if mark else "",
    }


def team_cards(
    defs: Mapping[str, Any],
    teams: Mapping[str, Any] | None,
    now: datetime | None = None,
    sessions: list[dict[str, Any]] | None = None,
    repos: Mapping[str, Any] | None = None,
    flows: Mapping[str, list[dict[str, Any]]] | None = None,
    host: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """**Teams** (§4.5 screen 8): a card per team the org defines, with the settings a person
    moves — the schedule (drawn disabled until TD-133), the stop time, the reserve priority,
    **when work appears** (§6 rule 8) and **balance** (`balance_card`, read against `sessions` and
    the `repos` reading) — as `settings.yml` holds them. The stop time is drawn in the
    reader's clock, as `ao team until` takes it; one already past says so. `on_work` is the picker's
    value, `start` while the key is absent (`ON_WORK_DEFAULT`, TD-457), which `on_work_set` tells apart
    (*start the team* is then marked *default*). `flows` is each team's `teams.flow_rows`, for the
    **flow** pick (§4.9c): the current one is `flow`, its strip `flow_strip`."""
    now = now or datetime.now(UTC)
    out = []
    for name, d in defs.items():
        t = (teams or {}).get(name) or {}
        until = _when(t.get("until"))
        out.append(
            {
                "name": name,
                "source": str(getattr(d, "source", "") or ""),
                "until": until.astimezone().strftime("%a %d %b %H:%M") if until else "",
                "passed": bool(until and until <= now),
                "reserve": t.get("reserve") if isinstance(t.get("reserve"), int) else 0,
                "schedule": t.get("schedule") or None,
                "on_work": t.get("on_work")
                if t.get("on_work") in settings_mod.ON_WORK
                else settings_mod.ON_WORK_DEFAULT,
                "on_work_set": t.get("on_work") in settings_mod.ON_WORK,
                "balance": balance_card(name, t.get("balance"), sessions, repos, host),
                "flows": (rows := list((flows or {}).get(name) or [])),
                "flow": next((r["name"] for r in rows if r.get("current")), ""),
                "flow_strip": next((r.get("strip") or "" for r in rows if r.get("current")), ""),
            }
        )
    return out


def _row(key: str, value: Any, default: Any = None, *, is_default: bool | None = None) -> dict[str, Any]:
    """One read-only value: its key, its text, and whether the file left it to its default."""
    if is_default is None:
        is_default = value == default
    if isinstance(value, list | tuple):
        text = ", ".join(str(v) for v in value) or "none"
    elif isinstance(value, dict):
        text = ", ".join(f"{k} {v}" for k, v in value.items()) or "none"
    elif value is None or value == "":
        text = "none"
    else:
        text = str(value)
    return {"key": key, "value": text, "default": bool(is_default)}


def pull_reading(reading: Mapping[str, Any] | None, now: datetime | None = None) -> str:
    """The last pull pass's reading beside the Repos card's **pull** switch (§4.5a, §6 *Pull*,
    TD-263): *current*, *last pulled <age> ago · n commits*, *waiting: <name> is mid-turn*,
    *refused: <why>*, *off* — "" before any pass has reached the checkout."""
    r = reading or {}
    outcome = r.get("outcome")
    if outcome == "pulled":
        n = r.get("commits")
        age = _age(r.get("at"), now or datetime.now(UTC))
        count = f" · {n} commit{'' if n == 1 else 's'}" if isinstance(n, int) else ""
        return f"last pulled {age + ' ago' if age else 'just now'}{count}"
    if outcome == "waiting":
        who = r.get("occupant")
        return (
            f"waiting: {who} is mid-turn"
            if who
            else "waiting: a session here cannot be read, so it is taken as mid-turn"
        )
    if outcome == "refused":
        return f"refused: {r.get('why') or 'no reason given'}"
    return str(outcome) if outcome in ("current", "off") else ""


def repo_cards(
    checkouts: list[str], settings_repos: Mapping[str, Any] | None, pulls: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """**Repos** (§4.5 screen 8): a card per registered checkout with its `.agentorc.yml` values
    read-only — the keys the clients read — and, where the file carries `promote:`, the one setting,
    **auto**, as `settings.yml` holds it (`false` when absent). A repo with no block says why it has
    no switch; a file that does not parse says so in place of its values. Every card, block or not,
    carries **pull** (§6 *Pull*: on when absent) and the home's last reading of it from `pulls`."""
    out = []
    for path in checkouts:
        root = Path(path)
        name = root.name
        card: dict[str, Any] = {"name": name, "path": path, "file": str(root / repoconfig.FILE), "rows": []}
        card["pull"] = ((settings_repos or {}).get(name) or {}).get("pull") is not False
        card["pull_reading"] = pull_reading((pulls or {}).get(name))
        try:
            cfg = repoconfig.load(root)
        except (ValueError, OSError) as e:
            card["error"] = str(e)
            out.append(card)
            continue
        card["exists"] = cfg.path is not None
        card["rows"] = [
            _row("ledger", cfg.ledger, repoconfig.DEFAULT_LEDGER),
            _row("roles", sorted(cfg.roles), is_default=not cfg.roles),
            _row("controllers", cfg.controllers, is_default=not cfg.controllers),
            _row("teams", sorted(cfg.teams), is_default=not cfg.teams),
        ]
        if cfg.promote:
            card["rows"] += [
                _row("promote.run", cfg.promote.get("run"), None),
                _row("promote.check", cfg.promote.get("check"), None),
            ]
        auto = (((settings_repos or {}).get(name) or {}).get("promote") or {}).get("auto")
        card["promote"] = bool(cfg.promote)
        card["auto"] = auto is True
        out.append(card)
    return out


def local_entry(path: Path) -> dict[str, Any]:
    """`hosts.yml`'s `local:` mapping as written — only to tell a value the file sets from one it
    leaves to its default; the values themselves are `sessionorc.hosts`'s reading."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    local = doc.get("local") if isinstance(doc, dict) else None
    return local if isinstance(local, dict) else {}


def host_card(raw: Mapping[str, Any], local: hosts.Host, home: str, nodes: Mapping[str, Any]) -> dict[str, Any]:
    """**Hosts** (§4.5 screen 8): this host's `local:` entry as `sessionorc.hosts` reads it, each
    value marked *default* where the file leaves it out; the home; the nodes it admits."""
    rows = [
        _row("name", local.name, is_default="name" not in raw),
        _row("vscode_host", local.vscode_host, is_default="vscode_host" not in raw),
        _row("local", local.local, is_default="local" not in raw),
        _row("volatile", local.volatile, is_default="volatile" not in raw),
        _row("repos_registry", str(local.repos_registry), is_default="repos_registry" not in raw),
        _row("runs_keep_days", local.runs_keep_days, is_default="runs_keep_days" not in raw),
        _row("identity", local.identity or "enforce", is_default="identity" not in raw),
        _row("person", local.person, is_default="person" not in raw),
        _row("home", home, is_default=home == local.name),
    ]
    if nodes:
        rows.append(
            _row("nodes", [f"{n} ({', '.join(k for k, v in f.items() if v) or 'plain'})" for n, f in nodes.items()])
        )
    return {"name": local.name, "file": str(hosts.hosts_file()), "rows": rows}


_PROFILE_DEFAULTS = {f.name: f.default for f in fields(profiles_mod.Profile) if f.name != "name"}


def profile_cards(profiles: Mapping[str, profiles_mod.Profile], default: str) -> list[dict[str, Any]]:
    """**Profiles** (§4.5 screen 8): every profile as `profiles.yml` declares it, read-only."""
    out = []
    for name, p in profiles.items():
        rows = []
        for key in ("adapter", "account", "model", "config_dir", "permission_wait", "billing", "prices"):
            v = getattr(p, key)
            d = _PROFILE_DEFAULTS.get(key)
            rows.append(_row(key, str(v) if isinstance(v, Path) else v, is_default=(not v and not d) or v == d))
        for key in ("extra_args", "unattended_args"):
            if getattr(p, key):
                rows.append(_row(key, " ".join(getattr(p, key))))
        out.append({"name": name, "default": name == default, "rows": rows})
    return out


def org_cards(defs: Mapping[str, Any]) -> list[dict[str, Any]]:
    """**Org** (§4.5 screen 8): each team the org defines, read-only — its projects, its manager,
    its techlead seat, its members by role and count, the host it lands on."""
    out = []
    for name, d in defs.items():
        members: dict[str, int] = {}
        for m in d.members:
            key = f"team {m.team}" if m.team else m.role
            members[key] = members.get(key, 0) + (m.count or 1)
        rows = [
            _row("projects", d.projects),
            _row("manager", f"{d.manager.name} · {d.manager.role}"),
            _row("techlead", d.techlead.name if d.techlead else None, is_default=d.techlead is None),
            _row("members", " · ".join(f"{k} ×{n}" for k, n in members.items()) or "none"),
            _row("host", d.host or "where the start runs", is_default=not d.host),
        ]
        if d.seats:
            rows.append(_row("seats", [f"{s.name} ({s.trigger})" for s in d.seats]))
        out.append({"name": name, "source": str(d.source or ""), "rows": rows})
    return out


def you(person: Mapping[str, Any] | None) -> dict[str, Any]:
    """**You**, *yours everywhere* (§4.5 screen 8): `open_in` as the field takes it back and the
    terminal's size and face, the attachment bound, the composer's pick — what `settings.yml` holds, the
    defaults where it holds nothing."""
    person = person or {}
    o = person.get("open_in")
    if isinstance(o, dict):
        mode, label, url = "template", str(o.get("label") or ""), str(o.get("url") or "")
    else:
        mode, label, url = (str(o) if o in ("vscode", "none") else "vscode"), "", ""
    term = person.get("terminal") if isinstance(person.get("terminal"), dict) else {}
    lo, hi = settings_mod.TERMINAL_SIZE
    attach = person.get("attach") if isinstance(person.get("attach"), dict) else {}
    # **board items shown** (§4.5a, TD-220 slice 4): the pick of four and its two numbers, 10 and 7 until typed
    inbox = person.get("inbox") if isinstance(person.get("inbox"), dict) else {}
    try:
        show = settings_mod.parse_board_show(inbox.get("board_show"))
    except ValueError:
        show = settings_mod.BOARD_SHOW_DEFAULT
    board_next, board_days = 10, 7
    if show.startswith("next:"):
        board_mode, board_next = "next", int(show.split(":", 1)[1])
    elif show.endswith("d"):
        board_mode, board_days = "days", int(show[:-1])
    else:
        board_mode = show
    return {
        "board_mode": board_mode,
        "board_next": board_next,
        "board_days": board_days,
        "board_next_bounds": settings_mod.BOARD_SHOW_NEXT,
        "board_days_bounds": settings_mod.BOARD_SHOW_DAYS,
        "open_in": mode,
        "label": label,
        "url": url,
        "size": term.get("size") if isinstance(term.get("size"), int) else None,
        "face": str(term.get("face") or ""),
        "copy_on_select": term.get("copy_on_select") is not False,  # on by default (§4.5a, TD-164)
        "size_bounds": (lo, hi),
        # **attachment bound** (§4.5 screen 8, §5 `person.attach.max`, TD-478): as written, the default beside it
        "attach_max": str(attach.get("max") or ""),
        "attach_default": settings_mod.ATTACH_MAX_DEFAULT,
        # **composer** (§4.5a *Focus composer* **the bar**, §5 `person.composer`, TD-500): folded by default
        "composer": "open" if person.get("composer") == "open" else "folded",
    }


# **Telegram**'s two display lines under its fields (§4.5a **You**: **Telegram**, §4.10 *Told on
# Telegram when nobody is looking*): what is told, and when — the design's list, not a setting
TELEGRAM_TOLD = (
    "told: a session waiting on you · a question · a blocked outcome · an identity alarm · "
    "a member not restarted · a team with work"
)
TELEGRAM_WHEN = "after a minute, once, and never while a page is visible · no text a session wrote is sent"


def telegram(
    notify: Mapping[str, Any] | None, last: Mapping[str, Any] | None, now: datetime | None = None
) -> dict[str, Any]:
    """**You**: **Telegram** (§4.5a, §4.10; TD-319 slice 3): `notify.telegram` as the `settings` read
    keeps it — the switch, the Doppler `project/config` (a name, never a value) and the link — and the
    last send from the home's `host` read (`notify: {last_ok, last_error}`), the later of the two:
    *last sent 14:02* or *last send failed 14:02: <reason>*; nothing yet, or a node's read, draws none."""
    tg = (notify or {}).get("telegram") if isinstance(notify, Mapping) else None
    tg = tg if isinstance(tg, Mapping) else {}
    now = now or datetime.now(UTC)
    last = last if isinstance(last, Mapping) else {}
    ok = _when(last.get("last_ok"))
    err = last.get("last_error") if isinstance(last.get("last_error"), Mapping) else {}
    bad = _when(err.get("at"))
    said, failed = "", False
    if bad is not None and (ok is None or bad > ok):
        said, failed = f"last send failed {usage_clock(bad, now)}: {err.get('reason') or 'no reason given'}", True
    elif ok is not None:
        said = f"last sent {usage_clock(ok, now)}"
    return {
        "on": tg.get("on") is True,
        "secrets": str(tg.get("secrets") or ""),
        "link": str(tg.get("link") or ""),
        "last": said,
        "last_failed": failed,
        "told": TELEGRAM_TOLD,
        "when": TELEGRAM_WHEN,
    }
