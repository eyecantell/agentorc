"""What every page of the web UI shares (design §4.5): the templates and their globals, the usage chip,
the host and org notes, the team and project views the forms read, the time helpers and the role
icons. Moved out of `agentorc.ui.app` unchanged (TD-196) and re-exported from it, so a route, a
template or a test reads each name from the app as before.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import math
import os
import threading
import time
from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from agentorc import org as orgmod
from agentorc import repoconfig, teamrun, teams
from agentorc.cli import stop_time as clistop
from sessionorc import build, hosts, identity, mail, paths
from sessionorc.client import AgentError
from sessionorc.client import call_sync as _call_sync
from sessionorc.models import (
    tokens_short,
)

from . import help as helpmod
from . import render as rendermod
from . import uiconf
from .icons import role_svg

HERE = Path(__file__).parent
log = logging.getLogger("uvicorn.error")  # the logger uvicorn already shows on the console
templates = Jinja2Templates(directory=str(HERE / "templates"))
# The role badge's picture (design §4.8 *Role presets*, TD-074): the markup lives in one place and
# the template asks for it by name, so no config file ever carries an SVG.
templates.env.globals["role_svg"] = role_svg
# The size of a Focus attachment's piece (§4.4 *Attachment drop*, TD-478), served on the Attach button
# so the page slices by the host agent's own constant.
templates.env.globals["attach_piece"] = paths.ATTACH_PIECE_BYTES


@functools.cache
def static_url(name: str) -> str:
    """The page's link to its own `app.css` / `app.js`, carrying a hash of the file: the server sends
    no `Cache-Control`, so a browser keeps a copy it guesses is fresh for hours, and after a promote
    it drew new markup with the old stylesheet (2026-09-26). The hash moves when the file does; the
    UI restarts on every promote, so it is read once per process."""
    digest = hashlib.sha256((HERE / "static" / name).read_bytes()).hexdigest()[:12]
    return f"/static/{name}?v={digest}"


templates.env.globals["static_url"] = static_url


def suggested_answers(e: Any) -> list[str]:
    """The suggested answers a mail row may draw buttons from (design §4.5a **Inbox row: suggested
    answers**, §4.10 *Suggested answers*; TD-070) — **the one place a row's answers are shaped**,
    so no template ever iterates whatever the record happens to hold.

    They are the only thing on this page that a control is built from, and only because they are a
    **structured field of the envelope** and not parsed out of what a session wrote (TD-071 item
    8). What the agent stores is always a short list of clean strings, so this is about a record
    that is somehow otherwise — a hand-edited store, a host agent older or newer than this UI: a
    non-list, or an item that is not a string or is blank, is dropped and the row simply shows no
    buttons. A row never breaks the page over its own envelope.

    Registered as a template global rather than folded into `person_inbox`, so that every path
    that renders a row — the page, the poll, a test rendering the template directly — shapes it
    the same way, and so that an entry's answers reach the markup nowhere else."""
    raw = e.get("answers") if hasattr(e, "get") else None
    if not isinstance(raw, list):
        return []
    return [a for a in raw if isinstance(a, str) and a.strip()][: mail.ANSWERS_MAX]


templates.env.globals["suggested_answers"] = suggested_answers


def shaped(text: Any, origin: Any = None) -> dict[str, Markup]:
    """A mail row's text as the row draws it (design §4.5a **Inbox row: details**, §4.10 *How a
    message to a person is written*; TD-138): `lead`, the first paragraph, and `rest`, what goes
    under *details* — empty when there is nothing to fold — each rendered from the closed markdown
    subset by `agentorc.ui.render`, whose output is escaped text and its own few tags and nothing
    else. `origin` is the page's own, so a link back to it is drawn as characters.

    A template global like `suggested_answers`, so the page, the poll and a test rendering the
    template directly shape a row the same way; the Focus Inbox panel gets the same two halves on
    each entry of its fetch (`api_inbox`)."""
    lead, rest = rendermod.fold(str(text or ""))
    o = str(origin) if origin else None
    return {"lead": Markup(rendermod.render(lead, o)), "rest": Markup(rendermod.render(rest, o) if rest else "")}


templates.env.globals["shaped"] = shaped


def md(text: Any, origin: Any = None, *, inline: bool = False, links: bool = True) -> Markup:
    """Text from the closed markdown subset as `shaped` renders it, unsplit — a board row's halves
    and its replies (§4.5a *Inbox board row: text*, TD-279), already cut where they are drawn.
    `links=False` draws every link as its characters: Focus's **Told at start** (TD-283)."""
    t, o = str(text or ""), (str(origin) if origin else None)
    return Markup(rendermod.inline(t, o, links) if inline else rendermod.render(t, o, links))


templates.env.globals["md"] = md
# design §4.5a *The help text* (TD-167): a control's `title` is its paragraph's first sentence, and a
# mark's panel the group's paragraphs — every one of them from `help.py`, the one table
templates.env.globals["help_title"] = helpmod.first_sentence
templates.env.globals["help_group"] = lambda g: [helpmod.BY_KEY[k] for k in helpmod.GROUPS[g]]
templates.env.globals["help_names"] = lambda g: ", ".join(helpmod.BY_KEY[k].name for k in helpmod.GROUPS[g])
templates.env.globals["help_screen"] = lambda g: helpmod.GROUP_SCREEN[g]
templates.env.globals["help_where"] = lambda where: Markup(rendermod.inline(where))  # the design's *emphasis*, as text


def page_origin(request: Request) -> str:
    """`scheme://host:port` of the page being served: the one origin a rendered link may not name."""
    return str(request.base_url).rstrip("/")


# Why a profile's last usage poll gave no reading (design §4.2, §4.5a **usage** chip; TD-087): the
# adapter's `reason` word, put into words for the chip's hover. The page keys on the word and
# never on text; a word this table does not know is printed as itself rather than dropped.
USAGE_WHY = {
    "rate_limited": "rate-limited by the usage endpoint",
    "no_credentials": "no credentials for this profile",
    "no_profile": "no such profile",
    "error": "the usage endpoint could not be read",
}
NEAR_CAP = 80  # "at or near a cap" (§4.5a): never collapsed into +n; `app.js` keeps the same number

# How old a reading may be before the chip says so (design §4.5a **usage** chip, *The age*; TD-230,
# TD-233 slice 1), in seconds; `app.js` keeps the same three numbers. Past `USAGE_AGED` the age is
# printed after the number, past `USAGE_FRESH` the chip is dimmed, and past `USAGE_UNKNOWN` the
# number is no longer offered as the account's: *week unknown since 22:21 (was 88%)*.
USAGE_AGED = 5 * 60
USAGE_FRESH = 15 * 60
USAGE_UNKNOWN = 3 * 3600
# Where a reading came from (§4.4 *Usage*), for the hover: a reading with no `source` was asked.
USAGE_SOURCE = {"asked": "asked of the endpoint", "reported": "reported by a session"}


def usage_age(secs: float) -> str:
    """A reading's age in the chip's short shape — *7m*, *6h*, *2d* — as `ao gate` prints it."""
    secs = max(0, int(secs))
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h" if secs < 86400 else f"{secs // 86400}d"


def usage_clock(dt: datetime, now: datetime) -> str:
    """When a reading was taken, in local time: *22:21*, with the day in front once it is a day old."""
    try:
        local = dt.astimezone()
    except (OverflowError, ValueError, OSError):  # year 1 or 9999 off a hand-repaired file: UTC, never a raise
        local = dt
    return local.strftime("%a %H:%M") if (now - dt).total_seconds() >= 86400 else local.strftime("%H:%M")


def usage_read(u: Any, now: datetime) -> dict[str, Any] | None:
    """A reading's age as the chip, its hover and the Settings card say it (§4.5a *The age*, TD-233
    slice 1): `{secs, age, clock, source}`, or None when `fetched` is not a time — an agent before
    TD-087, or a test's placeholder — which is drawn as before, with no age."""
    at = _instant(u.get("fetched")) if isinstance(u, dict) else None
    if at is None:
        return None
    secs = max(0.0, (now - at).total_seconds())
    source = str(u.get("source") or "asked")
    return {"secs": secs, "age": usage_age(secs), "clock": usage_clock(at, now),
            "source": USAGE_SOURCE.get(source, source)}  # fmt: skip


def _usage_line(w: dict[str, Any], lines: Any) -> dict[str, Any] | None:
    """This window's row of the gate's reading (§6 *Usage gate*, TD-100): `{line, reserve, next}`
    where the profile has a reserve for the window and it makes a line, else None."""
    for row in lines if isinstance(lines, list) else []:
        if isinstance(row, dict) and row.get("label") == w.get("label"):
            ln = row.get("line")
            return row if isinstance(ln, int | float) and not isinstance(ln, bool) else None
    return None


def _projected(row: dict[str, Any] | None) -> float | None:
    """The projection the gate reads for this window (§6 *A reading the gate can no longer trust*,
    TD-233): the row's `pct` when `usage.project` marked it `projected`, else None."""
    if row is None or not isinstance(row.get("projected"), dict):
        return None
    pct = row.get("pct")
    return pct if isinstance(pct, int | float) and not isinstance(pct, bool) else None


def _usage_hover(w: dict[str, Any], row: dict[str, Any] | None) -> str:
    """One window on the chip's hover: its number, and where it has a line the line, the reserve,
    the days left a per-day reserve counts and when the line next moves (§4.5a **usage**); the
    gate's projection after the number when it is projecting."""
    if row is None:
        return f"{w.get('label')} {w['pct']:g}% (resets {w.get('resets') or '?'})"
    pr = _projected(row)
    now = f"{w['pct']:g}%" + (f", projected {pr:g}%" if pr is not None else "")
    return f"{w.get('label')} {now} / line {row['line']:g}% ({_reserve_why(row)}; resets {w.get('resets') or '?'})"


def _reserve_why(row: dict[str, Any]) -> str:
    """A line's reserve, the days left a per-day reserve counts, and when the line next moves —
    the account's lowest line and each profile's own alike (§4.5a **usage**, TD-100, TD-122)."""
    r, ln = row.get("reserve"), row["line"]
    if isinstance(r, dict) and isinstance(r.get("per_day"), int) and r["per_day"] > 0:
        why = f"reserve {r['per_day']}% a day"
        if ln > 0:
            why += f", {int((100 - ln) // r['per_day'])} days left"
    else:
        why = "reserve ?" if r is None else f"reserve {r}%"
    return f"{why}; line moves {row.get('next') or '?'}"


def _amount_says(a: Any) -> str:
    """A metered profile's amount for a window as the chip writes it — *$5*, *2M tok* — or "" for
    what is not one."""
    v = a.get("value") if isinstance(a, dict) else None
    if not isinstance(v, int | float) or isinstance(v, bool) or not math.isfinite(v):
        return ""
    return _money(a["value"]) if a.get("unit") == "$" else f"{tokens_short(int(a['value']))} tok"


def _usage_profiles(u: dict[str, Any]) -> str:
    """The profiles sharing an account, for its chip's hover (§4.5a **usage**, TD-122): each with
    the lines its reserves make — each with its reserve, the days left and when it next moves — a
    metered one with its own amount per window (TD-151), and the live sessions running under it."""
    parts = []
    for p in u.get("profiles") or []:
        if not isinstance(p, dict):
            continue
        lines = [
            f"{r.get('label')} line {r['line']:g}% ({_reserve_why(r)})"
            for r in p.get("lines") or []
            if isinstance(r, dict) and isinstance(r.get("line"), int | float) and not isinstance(r.get("line"), bool)
        ]
        # a metered profile's own amounts (TD-151): the chip prints the smallest, the hover each
        lines += [
            f"{a.get('label')} amount {said}"
            for a in p.get("amounts") or []
            if isinstance(a, dict) and (said := _amount_says(a.get("amount")))
        ]
        names = [str(x) for x in p.get("sessions") or []]
        part = str(p.get("name"))
        if lines:
            part += f" [{', '.join(lines)}]"
        if names:
            part += f": {', '.join(names)}"
        parts.append(part)
    # one profile per line under its heading: a hover that lists is a list (TD-270)
    return "profiles on this account:\n" + "\n".join(parts) if parts else ""


def usage_accounts(usage: Any, sessions: Any = None) -> dict[str, Any]:
    """The host agent's per-profile readings as **one per account** (design §4.2a, §4.5a **usage**,
    TD-122), keyed by the chip's name — `<tool> · <account>`, *Claude · paul* — which is what the
    person knows the quota by; never a profile's name, which said nothing (TD-071 item 8). Every
    profile of an account carries the same reading, so the first one's stands for it; the chip's
    `lines` are the lowest line per window among its profiles, and `profiles` lists each with its
    own lines and the live sessions running under it, for the hover. A reading from an agent
    before TD-122 names no account and stays its profile's own chip."""
    live: dict[str, list[str]] = {}
    for s in sessions or []:
        if isinstance(s, dict) and s.get("state") not in ("exited", "closed"):
            live.setdefault(str(s.get("profile") or ""), []).append(str(s.get("name") or s.get("id")))
    out: dict[str, Any] = {}
    for prof, u in (usage or {}).items():
        if not isinstance(u, dict):
            continue
        name = f"{u['tool']} · {u['account']}" if u.get("tool") and u.get("account") else prof
        acc = out.get(name)
        if acc is None:
            acc = out[name] = {**{k: v for k, v in u.items() if k != "lines"}, "lines": [], "profiles": []}
        else:
            # a metered account's spend is one sum and each profile's amount its own: the chip reads
            # each window over the smallest amount, which is the highest `pct` (§4.5a **usage**)
            theirs = {w.get("label"): w for w in u.get("windows") or [] if isinstance(w, dict) and w.get("spent")}
            acc["windows"] = [
                theirs[w.get("label")]
                if isinstance(w, dict)
                and isinstance((theirs.get(w.get("label")) or {}).get("pct"), int)
                and (not isinstance(w.get("pct"), int) or theirs[w.get("label")]["pct"] > w["pct"])
                else w
                for w in acc.get("windows") or []
            ]
        lines = [r for r in u.get("lines") or [] if isinstance(r, dict)]
        # a metered profile's amounts are its own, as a reserve is (§4.2a): kept for the hover
        amounts = [
            {"label": w.get("label"), "amount": w["amount"]}
            for w in u.get("windows") or []
            if isinstance(w, dict) and isinstance(w.get("spent"), dict) and isinstance(w.get("amount"), dict)
        ]
        acc["profiles"].append(
            {"name": prof, "lines": lines, "sessions": live.get(prof, [])} | ({"amounts": amounts} if amounts else {})
        )
        for row in lines:
            ln = row.get("line")
            if not isinstance(ln, int | float) or isinstance(ln, bool):
                continue
            have = next((i for i, r in enumerate(acc["lines"]) if r.get("label") == row.get("label")), None)
            if have is None:
                acc["lines"].append(row)
            elif ln < acc["lines"][have]["line"]:
                acc["lines"][have] = row
    return out


def _money(v: Any) -> str:
    return f"${float(v):,.2f}".removesuffix(".00")


def _pace_says(w: dict[str, Any], now: datetime) -> str:
    """A metered window's turns and pace on the hover (§4.5a **usage**, TD-151): *· 412 turns ·
    $0.40/h · at this pace $5 by 12:30* — the clock time only where the pace reaches the amount before
    the reset, its weekday in front when it is not today. Nothing for what is not one."""
    out = ""
    turns = w.get("turns")
    if isinstance(turns, int) and not isinstance(turns, bool):
        out += f" · {turns:,} turn{'' if turns == 1 else 's'}"
    p = w.get("pace")
    v = p.get("per_hour") if isinstance(p, dict) else None
    if (
        not isinstance(v, int | float)
        or isinstance(v, bool)
        or not math.isfinite(v)
        or p.get("unit") not in ("$", "tok")
    ):
        return out
    out += f" · {_money(v) if p['unit'] == '$' else f'{tokens_short(int(v))} tok'}/h"
    at, amount = _instant(p.get("at")), _amount_says(w.get("amount"))
    if at is not None and amount:
        local, today = at.astimezone(), now.astimezone().date()
        out += f" · at this pace {amount} by {local.strftime('%H:%M' if local.date() == today else '%a %H:%M')}"
    return out


def _metered_chip(
    prof: str, u: dict[str, Any], windows: list[dict[str, Any]], now: datetime | None = None
) -> dict[str, Any]:
    """A **metered** account's chip (§4.5a **usage**, §4.2a; TD-151 slice 5): *Claude · key · day
    $3.20 / $5* — the account's spend over the window's amount — or *day 1.2M tok* with no amount;
    its worst window the one nearest its amount, red at it, amber from eight tenths; tokens by kind,
    the window's turns and its pace on hover; *spend unknown* beside it when the adapter could not
    read. Never *stale*: the reading is a sum. `AO.usageChip` is the same rule."""
    now = now or datetime.now(UTC)

    def spend(w: dict[str, Any]) -> str:
        s, a = w["spent"], w.get("amount") if isinstance(w.get("amount"), dict) else {}
        if a.get("unit") == "tok" or not isinstance(s.get("cost"), int | float):
            return f"{tokens_short(int(s.get('total') or 0))} tok"
        return _money(s["cost"])

    def amount(w: dict[str, Any]) -> str:
        return _amount_says(w.get("amount"))

    def pct(w: dict[str, Any]) -> int | None:
        p = w.get("pct")
        return p if isinstance(p, int) and not isinstance(p, bool) else None

    worst = windows[0]
    for w in windows:
        if pct(w) is not None and (pct(worst) is None or pct(w) > pct(worst)):
            worst = w
    n = pct(worst) or 0
    text = f"{prof} · {worst.get('label')} {spend(worst)}"
    if amount(worst):
        text += f" / {amount(worst)}"
    parts = []
    for w in windows:
        t = w["spent"].get("tokens") if isinstance(w["spent"].get("tokens"), dict) else {}
        kinds = ", ".join(f"{tokens_short(int(t.get(k) or 0))} {word}" for k, word in METERED_KINDS)
        part = f"{w.get('label')} {spend(w)}"
        if amount(w):
            part += f" / {amount(w)} ({pct(w) if pct(w) is not None else '?'}%)"
        part += _pace_says(w, now)
        parts.append(f"{part} — {kinds} (resets {w.get('resets') or '?'})")
    title = "\n".join(parts)  # one window per line (TD-270)
    reason = str(u.get("reason") or "ok")
    if reason != "ok":
        text += " · spend unknown"
        title = f"spend unknown: {USAGE_WHY.get(reason, reason)}\n{title}"
    if sharing := _usage_profiles(u):
        title += f"\n\n{sharing}"
    near = n >= NEAR_CAP
    return {"text": text, "title": title, "pct": n, "cls": "cap" if n >= 100 else "near" if near else "", "near": near}


METERED_KINDS = (("input", "in"), ("output", "out"), ("cache_read", "cache read"), ("cache_write", "cache write"))


def usage_chip(prof: str, u: Any, now: datetime | None = None) -> dict[str, Any] | None:
    """One account's top-bar chip (design §4.5a **usage**, TD-073, TD-087, TD-122), or None for no
    chip. `prof` is the chip's name — `<tool> · <account>` from `usage_accounts`, a bare profile
    for an agent that names no account.

    The worst window is printed, every window on hover, red at a cap. **Worst** is the window with
    the smallest gap to its line — the line the profile's reserve makes (§6, TD-100: `u["lines"]`,
    the `gate` reading the page attaches), the tool's 100% where it has none — and a window with a
    line prints it after the number, *grind · week 61% / 70%*. *Near* (never collapsed into +n) is
    within ten points of a line, 80% without one.

    **The age** (§4.5a, TD-230, TD-233 slice 1): past five minutes the reading's age follows the
    number, *Claude · paul · week 88% · 6h*, and past `USAGE_FRESH` the chip is dimmed (`old`; still
    red at a cap); past `USAGE_UNKNOWN`, or once the worst window's reset has passed, the number is
    no longer offered as the account's — *week unknown since 22:21 (was 88%)* — and a window past
    its reset is never the worst while one is not. The age replaced the word *stale* (TD-087), which
    said a reading was old and not how old; the hover still says when it was taken, from where, and
    why the poll since failed. A refusal with no reading ever held is `<profile>: no reading yet`: a
    chip that silently went out is what TD-087 was. An `ok` answer with no windows is an adapter that
    reports no quota, which has no chip. `now` is for the tests.

    `app.js`'s `AO.usageChip` is the same rule for a pushed `usage` event; the tests hold the two
    to the same cases."""
    if not isinstance(u, dict):
        return None
    spent = [w for w in (u.get("windows") or []) if isinstance(w, dict) and isinstance(w.get("spent"), dict)]
    if spent:
        return _metered_chip(prof, u, spent, now)
    windows = [
        w
        for w in (u.get("windows") or [])
        if isinstance(w, dict) and isinstance(w.get("pct"), int | float) and not isinstance(w.get("pct"), bool)
    ]
    reason = str(u.get("reason") or "ok")
    refused = reason != "ok"
    if not windows and not refused:
        return None
    why = ""
    if refused:
        why = "the last poll was refused: " + USAGE_WHY.get(reason, reason)
        if isinstance(u.get("retry_after"), int | float):
            why += f", which asked to be left {max(1, math.ceil(u['retry_after'] / 60))} min"
    sharing = _usage_profiles(u)
    if not windows:
        title = f"no usage reading for {prof} yet — {why}"
        if sharing:
            title += f"\n\n{sharing}"
        return {"text": f"{prof}: no reading yet", "title": title, "pct": 0, "cls": "unknown", "near": False}
    now = now or datetime.now(UTC)
    read = usage_read(u, now)
    # A window past its reset is unknown until it is read again — never zero, never a cap (§4.4)
    gone = {id(w) for w in windows if (r := _instant(w.get("resets"))) is not None and r <= now}
    rows = [(w, _usage_line(w, u.get("lines"))) for w in windows]

    def shown(w: dict[str, Any], r: dict[str, Any] | None) -> float:
        """The number the gate reads: the projection while it projects (§6, TD-233), else the reading."""
        pr = _projected(r)
        return w["pct"] if pr is None else pr

    ws = sorted(rows, key=lambda x: (id(x[0]) in gone, (x[1]["line"] if x[1] else 100) - shown(*x), -shown(*x)))
    worst, row = ws[0]
    projected = None if id(worst) in gone else _projected(row)
    parts = []
    for w, r in ws:
        if id(w) in gone:
            parts.append(f"{w.get('label')} unknown since its reset at {usage_clock(_instant(w['resets']), now)} "
                         f"(was {w['pct']:g}%)")  # fmt: skip
        else:
            parts.append(_usage_hover(w, r))
    head = [f"read at {read['clock']}, {read['age']} ago, {read['source']}"] if read else []
    # the reading, why it is held, then each window on its own line; a blank line; the profiles (TD-270)
    title = "\n".join([*head, *([why] if why else []), *parts])
    if sharing:
        title += f"\n\n{sharing}"
    # a projection is what the chip shows past USAGE_UNKNOWN too (§4.5a *The age*): the gate reads it
    unknown = id(worst) in gone or (projected is None and read is not None and read["secs"] > USAGE_UNKNOWN)
    if unknown:
        # the number is no longer offered as the account's (§4.5a *The age*); it stays on the hover
        since = _instant(worst["resets"]) if id(worst) in gone else _instant(u.get("fetched"))
        text = f"{prof} · {worst.get('label')} unknown since {usage_clock(since, now)} (was {worst['pct']:g}%)"
        return {"text": text, "title": title, "pct": 0, "cls": "unknown", "near": False}
    n = shown(worst, row)
    near = n >= 100 or (n >= row["line"] - 10 if row else n >= NEAR_CAP)
    cls = "cap" if n >= 100 else "near" if near else ""
    text = f"{prof} · {worst.get('label')} {worst['pct']:g}%"  # *Claude · paul · week 24%* (TD-122)
    if row and projected is None:
        text += f" / {row['line']:g}%"  # *grind · week 61% / 70%* (§4.5a, TD-100)
    if read and read["secs"] > USAGE_AGED:
        text += f" · {read['age']}"  # *Claude · paul · week 88% · 6h* (TD-230)
    if row and projected is not None:
        text += f" · projected {projected:g}% / {row['line']:g}%"  # *week 88% · 2h · projected 96% / 95%* (TD-233)
    if read and read["secs"] > USAGE_FRESH:
        cls = f"{cls} old".strip()  # still red at a cap: it is the best evidence there is
    return {"text": text, "title": title, "pct": n, "cls": cls, "near": near}


def with_lines(usage: Any, gate: Any) -> dict[str, Any]:
    """The host agent's `usage` with each profile's rows of its `gate` reading attached as `lines`
    (§6, TD-100), which is what the chip reads its line from — on the page's render and on each
    pushed `usage` event alike. A gate that could not be read (an older agent, no `settings.yml`)
    leaves every chip as it was: a number with no line."""
    profiles = gate.get("profiles") if isinstance(gate, dict) else None
    out: dict[str, Any] = {}
    for prof, u in (usage or {}).items():
        g = profiles.get(prof) if isinstance(profiles, dict) else None
        out[prof] = {**u, "lines": g.get("windows") or []} if isinstance(u, dict) and isinstance(g, dict) else u
    return out


templates.env.globals["usage_chip"] = usage_chip
templates.env.globals["usage_accounts"] = usage_accounts

# The New session form's `controller` field when nothing is ticked: an empty list means nobody may
# act on the session, which is design §4.8's explicit default. Module-level so the signature keeps
# no mutable default and no call in its arguments.
NO_CONTROLLERS: list[str] = []
NO_GRANTS: list[str] = []

# design §4.5a New session **Grants** checkboxes: "shown with a one-line warning of what the grant
# allows". Keyed by the grant, so a grant added to `GRANTS` without a line here is visible as a
# missing note rather than silently shipping an unexplained checkbox (a test pins it).
UNDESCRIBED_GRANT = "⚠ this grant has no description — see design §4.8"

GRANT_NOTES: dict[str, str] = {
    "control": (
        "lets this session act on other sessions — send to them, wrap them up, kill them — "
        "for the sessions that name it a controller (§4.8)"
    ),
}


def _as_session_id(ident: str, fleet: list[dict[str, Any]], *, must_exist: bool = True) -> str:
    """A session id from what a person typed into the controllers chip: a full id is itself, a name
    is resolved against the fleet (a live holder wins over an exited one, §4.1). Removing takes
    whatever it is given — an entry may name a session that is gone, and that is exactly the entry
    a person most needs to remove."""
    if any(o.get("id") == ident for o in fleet) or not must_exist:
        return ident
    named = [o for o in fleet if o.get("name") == ident]
    live = [o for o in named if o.get("state") not in ("exited", "closed")] or named
    if len(live) == 1:
        return str(live[0]["id"])
    if not live:
        raise HTTPException(400, f"no session {ident!r} — use the id or name from ao status")
    raise HTTPException(400, f"{ident!r} is ambiguous — {', '.join(sorted(str(o['id']) for o in live))}")


def _str_list(body: dict[str, Any], key: str) -> list[str]:
    """A JSON body's list-of-strings field, or a 400. Without the type check `{"add": "ao-x"}`
    would reach `list()` and explode a string into one-character ids, and `[null]` would store the
    literal "None" — both accepted downstream, since the agent only refuses empty strings."""
    v = body.get(key) or []
    if not isinstance(v, list) or any(not isinstance(x, str) or not x.strip() for x in v):
        raise HTTPException(400, f"{key} must be a list of session ids")
    return [x.strip() for x in v]


WRAPUP_PROMPT = teams.WRAPUP_PROMPT  # the card's Wrap up and `ao team stop` send the one text (§4.9)


def host_name() -> str:
    return hosts.local_host().name


def rpc(method: str, **params: Any) -> Any:
    """The **blocking** RPC, for `agentorc.teamrun` only. A team start or stop is a sequence of
    calls with waits in it, so the runner is written blocking and shared with `ao team`; the routes
    run it on a worker thread (`asyncio.to_thread`), where this opens and closes its own connection
    and never touches the event loop. Every other route uses the async `call` inside `create_app`."""
    return _call_sync(method, **params)


def node_org_note() -> str:
    """What a node's page and toasts say about the org (design §4.4a): it lives on the home, and a
    node does not read it from there — decided, not pending (TD-057 step 4b.3)."""
    return (
        f"the org lives on {hosts.home_name()} (home): start and stop teams there — "
        f"{hosts.local_host().name} is a node, and its page shows this host's sessions only"
    )


def node_banner(info: dict[str, Any] | None) -> str:
    """The Org page's line on a node (design §4.4a "When a host cannot reach home"): whether its
    link to the home is up, from the `host` RPC, and when it is not, what that means here."""
    if not info or info.get("mode") != "node":
        return ""
    home, link = info.get("home") or "the home", info.get("link") or {}
    if info.get("home_reachable"):
        return f"node of {home}: linked — the org's teams, mail and other hosts are on {home}"
    return (
        f"node of {home}: unreachable since {link.get('since') or '?'} — {link.get('why') or 'no link'} · "
        f"offline: this host's sessions only; mail, reports and home-owned edits wait for the link"
    )


def build_chip(info: dict[str, Any] | None) -> dict[str, str] | None:
    """The Org top bar's **build** chip (design §4.5a, TD-132 slice 5): `build.chip` over the
    running host agent's `built_from` from `host`. How far main is ahead is the home's own
    `promotes` reading when it holds one, with a main, for the checkout the build came from and read
    the same live commit — shown exactly when the Inbox's Promote row would be, with its count —
    and otherwise measured here, on the UI's side, as `ao status -v` measures it (§4.4). None
    with nothing to say: the build is main's head, or the agent did not answer. Runs git: call it
    in a thread."""
    if not info:
        return None
    b = info.get("built_from") if isinstance(info.get("built_from"), dict) else {}
    a = None
    source, commit = b.get("source"), b.get("commit")
    if isinstance(source, str) and commit:
        for r in (info.get("promotes") or {}).values():
            if not isinstance(r, dict) or not r.get("root") or r.get("live") != commit:
                continue
            if Path(str(r["root"])).resolve() != Path(source).resolve() or not r.get("main"):
                continue
            # the row's own test (`promote_rows`: main set and live ≠ main), so the two are drawn
            # together; a live main does not descend from has no count, and says so
            if r["main"] == commit:
                a = {"ref": build.REF, "ahead": 0}
            elif isinstance(r.get("ahead"), int) and r["ahead"] > 0:
                a = {"ref": build.REF, "ahead": r["ahead"]}
            else:
                a = {"ref": build.REF, "why": f"main is at {str(r['main'])[:12]}, which it cannot count back to"}
            break
    return build.chip(b, str(info.get("started_at") or ""), a)


def restart_note(info: dict[str, Any] | None, id_info: dict[str, Any] | None) -> str:
    """The Org's line when `hosts.yml` moved under a running host agent (design §5, TD-149 (5)):
    `local.name`, `home:` and `local.identity` are read by the agent once, at its start, and by
    this page on every request, so a hand edit leaves the two disagreeing until a restart. This
    says which of the three the file has moved on — the agent's value from its `host` and
    `identity` answers against the file's — and that the restart applies them. A value the agent
    did not answer is not compared."""
    info, id_info = info or {}, id_info or {}
    local = hosts.local_host()
    pairs = (
        ("name", info.get("host"), local.name),
        ("home", info.get("home"), hosts.home_name()),
        ("identity", id_info.get("mode"), identity.mode_of(local.identity)),
    )
    moved = [f"{k} {ran} → {filed}" for k, ran, filed in pairs if ran and str(ran) != filed]
    if not moved:
        return ""
    return f"hosts.yml changed: {', '.join(moved)} — restart pending: the host agent applies it when it restarts"


def identity_note(info: dict[str, Any] | None) -> str:
    """The Org's teams line on who is calling (design §4.8a: *`ao status -v` and the Org's teams
    line say which mode a host is in, since `observe` is a host that is not yet protected*). It
    says **identity: observe** as loudly as it says **identity: off**, and says nothing at all
    under `enforce`, which is the host that is protected — a note that was always there would stop
    being read."""
    mode = str((info or {}).get("mode") or "")
    if mode not in ("off", "observe"):
        return ""
    if mode == "off":
        return "identity: off — a caller is whatever it says it is here; nothing is classified (design §4.8a)"
    return (
        "identity: observe — a forged caller is recorded and shown, and still served: "
        "this host is not enforcing it yet (design §4.8a)"
    )


PLACE_TTL = 5.0  # seconds a host's registry, or the reason it could not be read, is kept (the `DEFS_TTL` idiom)
PLACE_WAIT = 2.0  # seconds a page waits for a host's registry before reading it as unknown
_place_cache: dict[str, tuple[float, list[str] | OSError]] = {}
_place_asking: set[str] = set()  # hosts being asked right now: never two calls to one host at once


def repos_of(host: str) -> list[str]:
    """Another host's registered checkouts, for a team `place:` puts there (design §4.9 *Where a
    repo's team lands*): the home's `host_repos`, kept for `PLACE_TTL` — `org_here` is read on every
    use and the answer is a node's registry file. Asked on a thread of its own, since `org_here` is
    called on the event loop as well as off it and the blocking client (`_call_sync`, what `rpc`
    is) opens its own loop; the caller waits `PLACE_WAIT` for it and no longer, so a node that hangs
    holds a page two seconds and not the call's whole timeout — the registry is then *unknown* until
    the call ends, and its answer is kept when it does. A refusal is kept as long as an answer."""
    now = time.monotonic()
    at, kept = _place_cache.get(host, (0.0, None))
    if kept is None or now - at > PLACE_TTL:
        if host not in _place_asking:
            _place_asking.add(host)

            def ask() -> None:
                try:
                    got: list[str] | OSError = teamrun.repos_via(_call_sync)(host)
                except OSError as e:
                    got = e
                _place_cache[host] = (time.monotonic(), got)
                _place_asking.discard(host)

            t = threading.Thread(target=ask, daemon=True)
            t.start()
            t.join(PLACE_WAIT)
        if host in _place_asking:  # still out: unknown for now, and not asked again while it is
            raise OSError(f"{host} has not answered for its registry yet")
        kept = _place_cache[host][1]
    if isinstance(kept, OSError):
        raise kept
    return list(kept)


def org_here() -> tuple[orgmod.Org, list[str]]:
    """The definitions the Org page acts on (design §4.9 *The org is an aggregate*, TD-229):
    `~/.agentorc/org.yml`, plus the `teams:` of every repo in this host's registry, by the one
    function `ao team` reads too. The org file wins a name collision, and a name two repos define
    is refused in both, a note; so is a team whose landing cannot be told. Read on every use and cached
    nowhere (but a placed team's host registry, `repos_of`); a malformed file is a note
    beside the strip, never a 500 — the rest of the page is still the fleet. On a node the org is
    not here (design §4.4a: `org.yml` lives on the home), which is a note too."""
    if hosts.is_node():
        return orgmod.Org(path=orgmod.org_file()), [node_org_note()]
    try:
        org = orgmod.load()
    except ValueError as e:
        return orgmod.Org(path=orgmod.org_file()), [str(e)]
    org, notes = orgmod.with_repos(org, hosts.local_host().repos(), repos_of=repos_of)
    return orgmod.with_home_settings(org), notes  # the flow each team runs now (§4.9c, `teams.<team>.flow`)


def projects_view() -> list[dict[str, Any]]:
    """The projects defined for this host, for New session's **Project** picker (design §4.5a,
    §4.9): each with its repos and their checkouts *here*. A repo whose entry names another host
    is listed with an empty path and the hosts that do have it — phase 2's transport reaches it,
    and until then the picker says so rather than offering a path that is not there."""
    try:
        org = orgmod.load()
    except ValueError:
        return []  # a malformed org.yml leaves the form exactly as it was before projects existed
    host = host_name()
    return [
        {
            "name": name,
            "repos": [{"repo": r, "path": str(by.get(host) or ""), "hosts": sorted(by)} for r, by in p.repos.items()],
        }
        for name, p in org.projects.items()
    ]


def teams_for_form(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The teams for New session's **Team** picker (design §4.5a, §4.9 *A person in the team*,
    TD-173): every definition `org_here` reads — `ao team list`'s set — with the host it runs on,
    its projects' checkouts there (the Directory list it narrows to), its member roles (Role is
    filtered to them plus `plain`) and its manager's id when that session is live or a seat on call
    (the Controllers tick, TD-269). A definition that cannot be read leaves the picker at *none*, as
    the strip notes it."""
    org, _notes = org_here()
    here = host_name()
    out = []
    for t in org.teams.values():
        host = t.host or here
        dirs = [
            str(by[host])
            for p in t.projects
            if p in org.projects
            for by in org.projects[p].repos.values()
            if by.get(host)
        ]
        mid = teams.manager_id(org, t, host, here)
        out.append(
            {
                "name": t.name,
                "host": host,
                "dirs": dirs,
                "roles": sorted({m.role for m in t.members if m.team is None and m.role}),
                "manager": mid if mid and teamrun.can_control(sessions, mid) else "",
            }
        )
    return out


def team_brief_ids(
    team: str,
    role: str | None = None,
    cfg: repoconfig.RepoConfig | None = None,
    read: repoconfig.Reader | None = None,
) -> dict[str, Any]:
    """What New session's **Team** pick gives a role's brief (design §4.9 *A person in the team*,
    TD-253): `teams.brief_ids` over the org the picker lists — the `{techlead}`, `{manager}` and
    `{context}` slots, and the team's current flow for `role` in `cfg`'s repo (§4.9c, TD-309);
    nothing for a team no definition names."""
    org, _notes = org_here()
    return teams.brief_ids(org, team, host_name(), role, cfg, read=read)


def team_reader(
    team: str, directory: str, cfg: repoconfig.RepoConfig | None = None, role: str | None = None
) -> dict[str, Any]:
    """The reader a person's session in `team` gets when its role has none (design §4.9 *A person in
    the team*): `{review, line}` — `teams.team_review` over the member roles resolved in
    `directory`'s repo (`cfg`, when it was read on another host: §4.4a *The New session form on
    another host*), and the one line under the picker saying what that is."""
    org, _notes = org_here()
    t = org.teams.get(team)
    if t is None:
        return {"review": None, "line": f"no team {team} is defined"}
    try:
        cfg = cfg or repoconfig.discover(directory or os.getcwd())
    except ValueError as e:
        return {"review": None, "line": f"⚠ {e}"}
    # §4.9c items 2 and 3 (TD-309): under the team's current flow the flow says who reads, and
    # `flow` tells the caller a role's own `review:` is set aside
    flowed, review = teams.flow_review(org, t, cfg, role)
    if flowed:
        if review is None:
            return {"review": None, "line": f"no reader: the flow {teams.current_flow(t)} holds nothing", "flow": True}
        if "chain" in review:  # more than the techlead on every held path (§4.9c, TD-315)
            return {"review": review, "line": f"held PRs read by {teams.chain_line(review)}", "flow": True}
        here = host_name()
        seat = teams.seat_id(org, t, t.host or here, here) or t.techlead.name
        return {"review": review, "line": f"held PRs read by {seat} on {', '.join(review['held'])}", "flow": True}
    review = teams.team_review(t, teams.team_roles(t, cfg, org.roles))
    if review is None:
        seatless = t.techlead is None
        why = "this team has no techlead seat" if seatless else "its members' roles hold no path"
        return {"review": None, "line": f"no reader: {why}"}
    here = host_name()
    seat = teams.seat_id(org, t, t.host or here, here) or t.techlead.name
    return {"review": review, "line": f"held PRs read by {seat} on {', '.join(review['held'])}"}


def _aged(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """design §4.5a **wound down** note (§4.9a, TD-053 step 6): a team's card says *wound down <t>*
    rather than a bare *stopped* when every session that carried the badge declared it was out of
    work. The instant comes from the records; the age is rendered here, like every other."""
    now = datetime.now(UTC)
    for r in rows:
        r["wound_down_age"] = _age(r.get("wound_down"), now)
        c = r.get("concluded")
        r["concluded_age"] = _age(c.get("at"), now) if isinstance(c, dict) else ""  # TD-099, the same way
    return rows


def teams_view(sessions: list[dict[str, Any]], waiting: teamrun.Waiting | None = None) -> dict[str, Any]:
    """The **Teams** strip's contents (design §4.5a): every definition with its source, projects,
    member count and live count — `teamrun.rows`, the very rows `ao team list` prints. `waiting` is
    the `host` reading's (TD-274), without which no member reads as waiting on the person."""
    org, notes = org_here()
    rows = _aged(teamrun.rows(org, sessions, waiting))
    # on a node the one note is where the org is, not a definition that failed to read
    elsewhere = notes[0] if hosts.is_node() and notes else ""
    return {"teams": rows, "source": str(org.path or ""), "notes": [] if elsewhere else notes, "elsewhere": elsewhere}


def vscode_url(directory: str) -> str:
    """The VS Code link for a directory on this host — the default editor button's (design §4.5)."""
    h = hosts.local_host()
    return uiconf.vscode_link(directory, local=h.local, remote=h.vscode_host)


def editor_link(directory: str, reach: str = "") -> dict[str, str] | None:
    """The editor button for a directory on this host, from the person's `open_in:` (design §5 *The
    person's own*, TD-095): `{label, url}`, or None for no button."""
    h = hosts.local_host()
    return uiconf.editor_link(directory, local=h.local, remote=h.vscode_host, reach=reach)


templates.env.globals["editor_link"] = editor_link  # the Settings page's **Open file** (§4.5a, TD-148)
templates.env.globals["person_terminal"] = uiconf.terminal  # every page hands it to its terminals (goal 12)


# -- view model ------------------------------------------------------------------------------------


def _instant(iso: Any) -> datetime | None:
    """An instant off a record, or None for anything this cannot read — the shape check `_age` and
    `_left` share (review of PR #281).

    A well-formed ISO string **with no offset** parses fine and comes back *naive*, and subtracting
    a naive instant from an aware one raises `TypeError`, which no caller was catching: one record
    written by another build, or repaired by hand, would have taken down the page rather than cost
    its row a line. Today's writers always stamp `Z` (`sessionorc.models.now_iso`), so this is the
    `_age` rule kept rather than a bug anyone has seen — and a naive stamp is read as UTC, which is
    what every stamp in the store means."""
    if not isinstance(iso, str) or not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _short_age(iso: Any, now: datetime) -> str:
    """An instant as a short age of one unit (§4.5a *team card: Answer needed / Doing*, **Ages and
    columns**, TD-232): *0m* under a minute and for an instant ahead of the clock (TD-418; *just now*
    until then), then *5m*,
    *1h*, *2d* — the largest whole unit and nothing after it — or "" for anything this cannot read.
    `app.js`'s `fmtShortAge` spells it the same way, so the minute's tick changes nothing it lands on."""
    dt = _instant(iso)
    if dt is None:
        return ""
    secs = int((now - dt).total_seconds())
    if secs < 60:
        return "0m"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def _age(iso: str | None, now: datetime) -> str:
    """An instant off a record as *2h 5m*, or "" for anything this cannot read.

    Anything: `view` runs for every session on the grid, so a raise here takes down the page rather
    than the one card — the failure PR #131's review caught for a `run_until` of *half six*. A
    record's timestamps are written by the agent and are well-formed, but a state file that a
    different build, a bug or a hand repair left holding a number or a dict must cost its card a
    line and nothing more, so the shape is checked rather than trusted (review of PR #203)."""
    dt = _instant(iso)
    if dt is None:
        return ""
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    return f"{secs // 86400}d"


def _left(iso: str | None, now: datetime) -> str:
    """A deadline off an entry as *3m 05s* or *1h 5m*, "" for a deadline already past or for
    anything this cannot read — `_age`'s rule, pointed the other way (design §4.5 screen 6
    *Layout*, TD-082).

    **Times never draw as a placeholder.** A time left is a duration and needs no time zone, so it
    is rendered here, in exactly the words `fmtLeft` in `app.js` uses, and the script only keeps it
    moving: a screenshot, a slow phone and a script error all show the number rather than `…`, and
    nothing on the row jumps when the first tick lands. Same defensiveness as `_age`: a malformed
    instant costs its row a line, never the page."""
    dt = _instant(iso)
    if dt is None:
        return ""
    secs = int((dt - now).total_seconds())
    if secs <= 0:
        return ""
    if secs < 3600:
        return f"{secs // 60}m {secs % 60:02d}s"
    if secs < 86400:
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    return f"{secs // 86400}d"


def _countdown(iso: str | None, now: datetime) -> str:
    """The permission row's countdown, in `app.js`'s own words (§4.5a **Inbox row: state**)."""
    if not iso:
        return ""
    left = _left(iso, now)
    return f"via hook · {left} left" if left else "via hook · falling through to the terminal"


def start_fields(at: str, unattended: bool) -> dict[str, str]:
    """The New session **At** field (design §6 *Start time*, §4.5a, TD-152): parsed as Until is, in the
    caller's clock, and refused on a session that is not Unattended, as Until is — the agent refuses
    it too, and a past time or an Until not after it, in its own words."""
    text = (at or "").strip()
    if not text:
        return {}
    if not unattended:
        raise HTTPException(
            400, "a start time applies to unattended sessions: pick a role that runs unattended, or clear At"
        )
    try:
        return {"start_at": clistop(text, "At")}
    except AgentError as e:
        raise HTTPException(400, str(e)) from None


def stop_fields(until: str, unattended: bool) -> dict[str, str]:
    """The New session **Until** field (design §6, §4.5a, TD-026). Same rule as `ao new --until`:
    the friendly shapes are parsed here, in the caller's clock, and the agent is handed an instant;
    a stop time on an interactive session is refused rather than stored, because policies leave
    those alone (§4.2) and a stop time nothing acts on is TD-026's own failure inverted."""
    text = (until or "").strip()
    if not text:
        return {}
    if not unattended:
        raise HTTPException(
            400, "a stop time applies to unattended sessions: pick a role that runs unattended, or clear Until"
        )
    try:
        return {"run_until": clistop(text), "wrapup_prompt": teams.WRAPUP_PROMPT}
    except AgentError as e:
        raise HTTPException(400, str(e)) from None


ICON_TTL = 5.0  # seconds a resolved role icon and label are kept, the `DEFS_TTL` idiom (design §4.5a)
# (repo, role) → (read at, (icon name, label)). Module-level, so every open page and every delta
# shares one read: resolving a role is a `.agentorc.yml` per repo, which must never ride the render path.
_icon_cache: dict[tuple[str, str], tuple[float, tuple[str, str, str]]] = {}


def _look_for(repo: str, role: str, org_roles: Any) -> tuple[str, str, str]:
    """The role's icon and display label in that repo (design §4.8): the repo's own `roles:` over
    the org's over the built-in, resolved by `repoconfig` — the core never keys on a role, and the
    UI is free to (§9 invariant 9). A repo with no file, an unreadable one, a role nothing defines,
    a repo on another host: no icon, and the **default label** — the role's name, raised — never
    nothing (§4.8 *The names*). Third, its `message:` line (§4.8 *A role says when to message it*,
    TD-171) — a built-in's default where the repo cannot be read, "" for a role with none."""
    try:
        cfg = repoconfig.load(repo) if repo else repoconfig.RepoConfig()
        r = repoconfig.resolve_role(cfg, role, org_roles)
        return r.icon or "", r.display, r.message or ""
    except (KeyError, ValueError, OSError):
        return "", repoconfig.default_label(role), str((repoconfig.PRESETS.get(role) or {}).get("message") or "")


def role_prompts(s: Mapping[str, Any]) -> list[dict[str, str]]:
    """A record's role's `prompts:` (design §4.8 *A role has saved prompts*, TD-170), layered as the
    icon is — the repo's own `roles:` over the org's over the built-in, the list replaced whole per
    layer. A record with no role, a repo that cannot be read, a role nothing defines: no chips."""
    role, repo = str(s.get("role") or ""), str(s.get("repo") or "")
    if not role:
        return []
    try:
        try:
            org_roles = org_here()[0].roles
        except (ValueError, OSError):
            org_roles = None
        cfg = repoconfig.load(repo) if repo else repoconfig.RepoConfig()
        return repoconfig.resolve_role(cfg, role, org_roles).prompts
    except (KeyError, ValueError, OSError):
        return []


async def role_icons(sessions: Collection[dict[str, Any]]) -> dict[tuple[str, str], tuple[str, str, str]]:
    """The (icon, label, message) per (repo, role) the fleet carries, off the loop and cached for `ICON_TTL`
    seconds — a role redefined by hand shows on the next load, or within that, exactly as a team
    definition does. Passed into `view`, so the record itself never carries either."""
    now = time.monotonic()
    want = {(str(s.get("repo") or ""), str(s.get("role") or "")) for s in sessions if s.get("role")}
    if stale := [k for k in want if now - _icon_cache.get(k, (0.0, ("", "", "")))[0] > ICON_TTL]:

        def resolve() -> dict[tuple[str, str], tuple[str, str, str]]:
            try:
                org_roles = org_here()[0].roles  # read once per batch, not once per pair (review of PR #240)
            except (ValueError, OSError):
                org_roles = None
            return {k: _look_for(*k, org_roles) for k in stale}

        got = await asyncio.to_thread(resolve)
        _icon_cache.update({k: (now, v) for k, v in got.items()})
    return {k: _icon_cache[k][1] for k in want if k in _icon_cache}


def _iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
