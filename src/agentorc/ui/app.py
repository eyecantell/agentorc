"""The agentorc web UI (design §4.5): server-rendered pages, one `/events` websocket per tab
pushing rendered cards, one `/term/<id>` websocket per open Focus terminal. Phase 1: the local
host only, from `hosts.yml`'s `local` entry; ssh transport arrives in phase 2.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import subprocess
import sys
import time
from collections.abc import Collection, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from agentorc import org as orgmod
from agentorc import profiles as profiles_mod
from agentorc import repoconfig, teamrun, teams
from agentorc import review as reviewmod
from agentorc.cli import stop_time as clistop
from sessionorc import hosts, identity, mail, naming, paths
from sessionorc.adapters import short_model
from sessionorc.client import AgentError, AgentUnavailable, LocalClient
from sessionorc.client import call_sync as _call_sync
from sessionorc.containers import attach_argv_in
from sessionorc.models import GRANTS, STATE_RANK, has_control, report_head, report_line, stop_note

from . import render as rendermod
from . import uiconf
from .icons import role_svg
from .pty_bridge import PtySession, attach_argv, pump, scroll_argv

HERE = Path(__file__).parent
log = logging.getLogger("uvicorn.error")  # the logger uvicorn already shows on the console
templates = Jinja2Templates(directory=str(HERE / "templates"))
# The role badge's picture (design §4.8 *Role presets*, TD-074): the markup lives in one place and
# the template asks for it by name, so no config file ever carries an SVG.
templates.env.globals["role_svg"] = role_svg


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


def _usage_line(w: dict[str, Any], lines: Any) -> dict[str, Any] | None:
    """This window's row of the gate's reading (§6 *Usage gate*, TD-100): `{line, reserve, next}`
    where the profile has a reserve for the window and it makes a line, else None."""
    for row in lines if isinstance(lines, list) else []:
        if isinstance(row, dict) and row.get("label") == w.get("label"):
            ln = row.get("line")
            return row if isinstance(ln, int | float) and not isinstance(ln, bool) else None
    return None


def _usage_hover(w: dict[str, Any], row: dict[str, Any] | None) -> str:
    """One window on the chip's hover: its number, and where it has a line the line, the reserve,
    the days left a per-day reserve counts and when the line next moves (§4.5a **usage**)."""
    if row is None:
        return f"{w.get('label')} {w['pct']}% (resets {w.get('resets') or '?'})"
    return (
        f"{w.get('label')} {w['pct']}% / line {row['line']:g}% ({_reserve_why(row)}; resets {w.get('resets') or '?'})"
    )


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


def _usage_profiles(u: dict[str, Any]) -> str:
    """The profiles sharing an account, for its chip's hover (§4.5a **usage**, TD-122): each with
    the lines its reserves make — each with its reserve, the days left and when it next moves — and
    the live sessions running under it."""
    parts = []
    for p in u.get("profiles") or []:
        if not isinstance(p, dict):
            continue
        lines = [
            f"{r.get('label')} line {r['line']:g}% ({_reserve_why(r)})"
            for r in p.get("lines") or []
            if isinstance(r, dict) and isinstance(r.get("line"), int | float) and not isinstance(r.get("line"), bool)
        ]
        names = [str(x) for x in p.get("sessions") or []]
        part = str(p.get("name"))
        if lines:
            part += f" [{', '.join(lines)}]"
        if names:
            part += f": {', '.join(names)}"
        parts.append(part)
    return f"profiles on this account: {'; '.join(parts)}" if parts else ""


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
        lines = [r for r in u.get("lines") or [] if isinstance(r, dict)]
        acc["profiles"].append({"name": prof, "lines": lines, "sessions": live.get(prof, [])})
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


def usage_chip(prof: str, u: Any) -> dict[str, Any] | None:
    """One account's top-bar chip (design §4.5a **usage**, TD-073, TD-087, TD-122), or None for no
    chip. `prof` is the chip's name — `<tool> · <account>` from `usage_accounts`, a bare profile
    for an agent that names no account.

    The worst window is printed, every window on hover, red at a cap. **Worst** is the window with
    the smallest gap to its line — the line the profile's reserve makes (§6, TD-100: `u["lines"]`,
    the `gate` reading the page attaches), the tool's 100% where it has none — and a window with a
    line prints it after the number, *grind · week 61% / 70%*. *Near* (never collapsed into +n) is
    within ten points of a line, 80% without one. **A held reading goes stale,
    not out** (TD-087): when the last poll was refused, the host agent keeps the last good windows
    with the `reason` beside them, and the chip draws them dimmed with *· stale* and says on hover
    when they were read and why the poll since failed — *the chip went out* and *the allowance is
    spent* are different things to a person. A refusal with no reading ever held is `<profile>: no
    reading yet`, the same way: a chip that silently went out is what this entry was. An `ok` answer
    with no windows is an adapter that reports no quota, which has no chip.

    `app.js`'s `AO.usageChip` is the same rule for a pushed `usage` event; the tests hold the two
    to the same cases."""
    if not isinstance(u, dict):
        return None
    windows = [
        w
        for w in (u.get("windows") or [])
        if isinstance(w, dict) and isinstance(w.get("pct"), int | float) and not isinstance(w.get("pct"), bool)
    ]
    reason = str(u.get("reason") or "ok")
    stale = reason != "ok"
    if not windows and not stale:
        return None
    why = ""
    if stale:
        why = "the last poll was refused: " + USAGE_WHY.get(reason, reason)
        if isinstance(u.get("retry_after"), int | float):
            why += f", which asked to be left {max(1, math.ceil(u['retry_after'] / 60))} min"
    sharing = _usage_profiles(u)
    if not windows:
        title = f"no usage reading for {prof} yet — {why}"
        if sharing:
            title += f". {sharing}"
        return {"text": f"{prof}: no reading yet", "title": title, "pct": 0, "cls": "stale", "near": False}
    rows = [(w, _usage_line(w, u.get("lines"))) for w in windows]
    ws = sorted(rows, key=lambda x: ((x[1]["line"] if x[1] else 100) - x[0]["pct"], -x[0]["pct"]))
    worst, row = ws[0]
    title = " · ".join(_usage_hover(w, r) for w, r in ws)
    near = worst["pct"] >= 100 or (worst["pct"] >= row["line"] - 10 if row else worst["pct"] >= NEAR_CAP)
    cls = "cap" if worst["pct"] >= 100 else "near" if near else ""
    text = f"{prof} · {worst.get('label')} {worst['pct']}%"  # *Claude · paul · week 24%* (TD-122)
    if row:
        text += f" / {row['line']:g}%"  # *grind · week 61% / 70%* (§4.5a, TD-100)
    if stale:
        title = f"held reading from {u.get('fetched') or 'an unknown time'} — {why}. {title}"
        text += " · stale"
        cls = f"{cls} stale".strip()
    if sharing:
        title += f". {sharing}"
    return {"text": text, "title": title, "pct": worst["pct"], "cls": cls, "near": near}


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


def org_here() -> tuple[orgmod.Org, list[str]]:
    """The definitions the Org page acts on (design §4.9): `~/.agentorc/org.yml`, plus the `teams:`
    of every repo in this host's registry — the page is not *in* a directory the way `ao team` is,
    so "a repo's own teams" means every repo the host knows about. The org file wins a name
    collision. Read on every use and cached nowhere; a malformed file is a note beside the strip,
    never a 500 — the rest of the page is still the fleet. On a node the org is not here (design
    §4.4a: `org.yml` lives on the home), which is a note too."""
    if hosts.is_node():
        return orgmod.Org(path=orgmod.org_file()), [node_org_note()]
    try:
        org = orgmod.load()
    except ValueError as e:
        return orgmod.Org(path=orgmod.org_file()), [str(e)]
    return teamrun.org_with_repo_teams(org, list(hosts.local_host().repos()))


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


def teams_view(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """The **Teams** strip's contents (design §4.5a): every definition with its source, projects,
    member count and live count — `teamrun.rows`, the very rows `ao team list` prints."""
    org, notes = org_here()
    rows = _aged(teamrun.rows(org, sessions))
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


def stop_fields(until: str, unattended: bool) -> dict[str, str]:
    """The New session **Until** field (design §6, §4.5a, TD-026). Same rule as `ao new --until`:
    the friendly shapes are parsed here, in the caller's clock, and the agent is handed an instant;
    a stop time on an interactive session is refused rather than stored, because policies leave
    those alone (§4.2) and a stop time nothing acts on is TD-026's own failure inverted."""
    text = (until or "").strip()
    if not text:
        return {}
    if not unattended:
        raise HTTPException(400, "a stop time applies to unattended sessions: tick Unattended, or clear Until")
    try:
        return {"run_until": clistop(text), "wrapup_prompt": teams.WRAPUP_PROMPT}
    except AgentError as e:
        raise HTTPException(400, str(e)) from None


ICON_TTL = 5.0  # seconds a resolved role icon and label are kept, the `DEFS_TTL` idiom (design §4.5a)
# (repo, role) → (read at, (icon name, label)). Module-level, so every open page and every delta
# shares one read: resolving a role is a `.agentorc.yml` per repo, which must never ride the render path.
_icon_cache: dict[tuple[str, str], tuple[float, tuple[str, str]]] = {}


def _look_for(repo: str, role: str, org_roles: Any) -> tuple[str, str]:
    """The role's icon and display label in that repo (design §4.8): the repo's own `roles:` over
    the org's over the built-in, resolved by `repoconfig` — the core never keys on a role, and the
    UI is free to (§9 invariant 9). A repo with no file, an unreadable one, a role nothing defines,
    a repo on another host: no icon, and the **default label** — the role's name, raised — never
    nothing (§4.8 *The names*)."""
    try:
        cfg = repoconfig.load(repo) if repo else repoconfig.RepoConfig()
        r = repoconfig.resolve_role(cfg, role, org_roles)
        return r.icon or "", r.display
    except (KeyError, ValueError, OSError):
        return "", repoconfig.default_label(role)


async def role_icons(sessions: Collection[dict[str, Any]]) -> dict[tuple[str, str], tuple[str, str]]:
    """The (icon, label) per (repo, role) the fleet carries, off the loop and cached for `ICON_TTL`
    seconds — a role redefined by hand shows on the next load, or within that, exactly as a team
    definition does. Passed into `view`, so the record itself never carries either."""
    now = time.monotonic()
    want = {(str(s.get("repo") or ""), str(s.get("role") or "")) for s in sessions if s.get("role")}
    if stale := [k for k in want if now - _icon_cache.get(k, (0.0, ("", "")))[0] > ICON_TTL]:

        def resolve() -> dict[tuple[str, str], tuple[str, str]]:
            try:
                org_roles = org_here()[0].roles  # read once per batch, not once per pair (review of PR #240)
            except (ValueError, OSError):
                org_roles = None
            return {k: _look_for(*k, org_roles) for k in stale}

        got = await asyncio.to_thread(resolve)
        _icon_cache.update({k: (now, v) for k, v in got.items()})
    return {k: _icon_cache[k][1] for k in want if k in _icon_cache}


# -- identity alarms (design §4.8a, TD-077 step 2) -------------------------------------------------


def _count(raw: Any) -> int:
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def alarm_words(a: dict[str, Any]) -> str:
    """One alarm in words (design §4.8a: `{channel, claimed, rpc, count, at, last}`). The `(others)`
    entry stands for every distinct alarm past the list's room and names no rpc, so it is said as
    what it is rather than printed as a row with empty fields."""
    n = _count(a.get("count"))
    claimed = str(a.get("claimed") or "")
    if claimed == identity.OTHERS:
        return f"and {n} more distinct claim{'' if n == 1 else 's'}"
    channel = str(a.get("channel") or "an unknown channel")
    rpc = str(a.get("rpc") or "a request")
    who = f"claimed to be {claimed}" if claimed else "sent no caller"
    return f"{channel} {who} on {rpc}{'' if n == 1 else f' ×{n}'}"


def alarm_view(raw: Any) -> list[dict[str, Any]]:
    """A record's (or the host's) `identity_alarms` as the page shows them: the words, and the
    first and last time for the browser to put in the person's own clock. **Tolerant by design** —
    a record written by another build, or repaired by hand, must cost its card a mark and not the
    grid (the `_age` rule, review of PR #203), so anything that is not a dict is dropped and every
    field is read as text."""
    if not isinstance(raw, list):
        return []
    out = []
    now = datetime.now(UTC)  # one instant for the whole render, as every other call site takes one
    for a in raw:
        if not isinstance(a, dict):
            continue
        at = a.get("at") if isinstance(a.get("at"), str) else ""
        last = a.get("last") if isinstance(a.get("last"), str) else ""
        # §4.5 screen 6 *Layout* (TD-082): every time on the page is in words from here. These two
        # are instants, so the row upgrades them to the browser's own clock once its script runs —
        # but a page that never runs it, or a screenshot of one, still reads *2h 5m ago*, not `…`.
        out.append({
            "words": alarm_words(a),
            "at": at,
            "last": last or at,
            "at_words": f"{_age(at, now)} ago" if _age(at, now) else "",
            "last_words": f"{_age(last or at, now)} ago" if _age(last or at, now) else "",
            "count": _count(a.get("count")),
        })  # fmt: skip
    return out


def alarm_to_view(raw: Any) -> dict[str, str] | None:
    """Who **Log TD** would hand a record's alarms to (design §4.8a *An alarm's answers*, TD-077 b),
    as the home's `alarm_to` gives it — `{"id", "name"}`, its first live controller — or None for
    *nobody*. A shape another build wrote (a bare string, a dict with no id) is *nobody* too: the
    row then says so in words, and costs nothing but the button."""
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"].strip():
        return None
    name = raw.get("name")
    return {"id": raw["id"], "name": name if isinstance(name, str) and name.strip() else raw["id"]}


def suspended_note(raw: Any) -> str:
    """The **suspended** mark's words, or "" (design §4.8a *An alarm's answers*, TD-077 a2).

    A suspension **ends no row and so writes no trail**, which makes this mark its only record on
    a page: if it is not drawn, nothing says it happened. So it is drawn wherever the record is —
    and it is a *mark*, never a control, because the two things that lift it are a person's Resume
    and Forget, both of which already exist and neither of which belongs on a badge.

    Tolerant, like every other derived chip here: a record written by another build, or repaired by
    hand, costs its card a mark and never the grid (the `_age` rule)."""
    if not isinstance(raw, dict):
        return ""
    at, by, why = (str(raw.get(k) or "") for k in ("at", "by", "why"))
    if not (at or by or why):
        return "suspended by a person — no detail recorded"
    bits = [f"suspended{f' at {at}' if at else ''}{f' by {by}' if by else ''}"]
    bits.append(why or "no reason recorded")
    return f"{bits[0]}: {bits[1]} — only a person lifts it, by resuming it or forgetting it (design §4.8a)"


def alarm_note(alarms: list[dict[str, Any]]) -> str:
    """The card mark's hover: the newest alarm in words, and how many there are in all. Empty when
    there are none, which is what draws no mark."""
    if not alarms:
        return ""
    newest = max(alarms, key=lambda a: (a["last"], a["at"]))
    rest = f" · {len(alarms)} alarms in all" if len(alarms) > 1 else ""
    return f"identity alarm: {newest['words']}{rest}"


def view(
    s: dict[str, Any],
    fleet: list[dict[str, Any]] | None = None,
    *,
    fleet_known: bool = True,
    icons: dict[tuple[str, str], tuple[str, str]] | None = None,
    seats: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Everything a card or the Focus header needs, computed once. `fleet` is the other records,
    needed only for the membership directions (design §4.8): who controls this session, and — for
    a lead — which sessions it controls. Without it both come back empty, which is what a
    caller that has only one record should show. `seats` is the ids a team definition names as a
    seat, each with what would make it come (`teamrun.seat_ids`): without it, a seat with nobody in
    it is drawn as the `exited` it is."""
    now = datetime.now(UTC)
    d = dict(s)
    state = s["state"]
    # `closed` is its own class since TD-095: it was `done`, drawn green, and on a card green now
    # means working and nothing else (design §4.5 *The card's anatomy*) — closed is over, and grey.
    d["state_class"] = {"needs-you": "needs", "stalled?": "stalled"}.get(state, state)
    d["state_label"] = {"needs-you": "needs you", "closed": "closed"}.get(state, state)
    d["rank"] = STATE_RANK.get(state, 9)
    # Finished while nobody was looking (design §4.2, TD-017): not a state, a rendering of `idle`
    # that sorts just above the idle it will become once someone opens Focus. Both stamps are whole
    # seconds, so a finish in the same second as the last look reads as seen. **Interactive only**
    # (Paul, 2026-09-21, TD-095 f): an unattended session's result is its manager's to read, and
    # its slot already says how it ended — a finished worker is plain `idle`.
    d["unseen"] = (
        state == "idle" and not s.get("unattended") and (not s.get("seen_at") or (s.get("since") or "") > s["seen_at"])
    )
    if d["unseen"]:
        d["state_label"] = "idle · unseen"  # not *finished*: that word is a declaration's (§4.9a, TD-095 e)
        d["rank"] = STATE_RANK["idle"] - 0.5
    # A seat with nobody in it reads *on call* (design §4.5 *The card's anatomy*, TD-097): composed
    # as *idle · unseen* is, from `exited` / `closed` and the definition — the state stays what it
    # is in every payload. A seat ends between questions by design, and drawn as *exited* the one
    # card behaving exactly as designed looked like the one that had failed.
    seats = seats or {}
    d["seat"] = state in DEAD and s.get("id") in seats
    d["seat_when"] = seats.get(s.get("id") or "", "") if d["seat"] else ""
    trig, counted = s.get("seat") or {}, (s.get("seat_count") or {}).get("prs")
    if d["seat_when"] and trig.get("trigger") == "prs" and isinstance(counted, int) and not s.get("seat_due"):
        # the tick's count toward it (§6 rule 3, TD-103): *on call — runs after 10 PRs · 4 of 10*
        d["seat_when"] += f" · {counted} of {trig.get('after')}"
    if d["seat"]:
        d["state_class"], d["state_label"] = "oncall", "on call"
    d["age"] = _age(s.get("since"), now)
    d["scraped"] = s.get("confidence") != "hook"
    # Another host's record, as the home shows it (design §4.4a): its own host on the card, and a
    # VS Code link only when a container node's reach names one — the ssh URL below is built from
    # *this* host's alias, which would open the wrong machine.
    d["host"] = s.get("host") or host_name()
    here = d["host"] == host_name()
    # An unreachable host's reason, and what the home is doing about a container node (§4.4a
    # "The home supervises it"): the overlay's line, on the state pill's title and as the flag.
    hl = s.get("host_link") or {}
    sup = hl.get("supervisor") or {}
    d["host_note"] = sup.get("doing") or (hl.get("why", "") if state == "unreachable" else "")
    # The editor button (§4.5a, §5 *The person's own*, TD-095): the person's `open_in:`. A container
    # node's record reaches VS Code by attaching to that container (§4.4a "Reach"), from what the
    # home derived when the node dialed in; any other host's record has no link.
    reach = "" if here else str((hl.get("reach") or {}).get("vscode") or "")
    d["editor"] = editor_link(str(s.get("dir") or ""), reach) if here or reach else None
    d["place"] = f"{d['host']} / {Path(s['repo']).name}" if s.get("repo") else f"{d['host']} / {s.get('dir', '')}"
    git = s.get("git") or {}
    where = s.get("dir", "")
    if s.get("repo") and s.get("dir") and s["dir"] != s["repo"]:
        where = f"wt/{Path(s['dir']).name}"
    if git.get("branch"):
        where += f" → {git['branch']}"
    d["where"] = where
    # design §4.5 *The card's anatomy*, row 3 (TD-095): **where**, alone on its row — the branch
    # by name, shortened in the middle so both ends read, whole on hover; a detached HEAD by its
    # short sha; the directory for a session with no repo. `wt/<name> ·` leads only when the
    # worktree is not the session's own name, which on a team is every member's.
    wt = Path(s["dir"]).name if s.get("repo") and s.get("dir") and s["dir"] != s["repo"] else ""
    d["wt_prefix"] = f"wt/{wt} · " if wt and wt != s.get("name") else ""
    branch = str(git.get("branch") or "")
    if branch == "(detached)":
        oid = str(git.get("oid") or "")
        d["branch_full"] = f"detached at {oid[:7]}" if oid else "detached HEAD"
    elif branch and branch != "?":
        d["branch_full"] = f"branch {branch}"
    else:
        d["branch_full"] = "" if s.get("repo") else str(s.get("dir") or "")
    d["branch_line"] = _middle(d["branch_full"], BRANCH_SHOWN)
    # what leads row 3 outside a team's own group, where the header does not say it: `host / repo ·`,
    # or `host /` before the directory of a session with no repo
    d["place_prefix"] = f"{d['place']} · " if s.get("repo") else f"{d['host']} / "
    flags = []
    if git.get("dirty"):
        flags.append("dirty")
    if git.get("unpushed"):
        # the one measure (design §4.2, TD-080): *exists only on this machine*, never *unmerged*
        flags.append(f"{git['unpushed']} unpushed")
    d["flag"] = " · ".join(flags) if state in ("idle", "exited", "stalled?", "needs-you") and flags else ""
    prof = s.get("profile") or ""
    if s.get("adapter") == "shell":
        d["profile_line"] = "shell"
    else:
        # tool · account · model (design §4.2a). The third part is the model actually in use when
        # the adapter can tell; the profile's declared model is an intent, so it says so (TD-031).
        declared = None
        try:
            p = profiles_mod.get(prof or None)
            line = " · ".join([p.adapter, p.account or p.name])
            declared = p.model
        except (KeyError, ValueError):
            line = f"{s.get('adapter')} · {prof or 'default'}"
        if observed := short_model(str(s.get("adapter") or ""), s.get("model")):
            line += f" · {observed}"
        elif declared:
            line += f" · {declared} (profile)"
        d["profile_line"] = line
    # A record whose `pending` is not a dict — another build, a hand repair — costs its card its
    # pending line and nothing more, the rule `doing` and `out_of_work` already follow: every
    # reader below (the card, the Focus header, `state_kind`) gets one shape (review of PR #251).
    pend = s.get("pending")
    pend = pend if isinstance(pend, dict) else {}
    d["pending"] = pend
    d["deadline"] = pend.get("deadline") or ""
    # The report channels (design §4.8, §4.5a card **report line**, TD-028 step 4). One line, shown
    # only when a channel is non-empty: `report_line` is the same text `ao status -v` prints — one
    # formatter, so the card and the CLI cannot drift — and the findings count rides beside it. The
    # line is dashed when the entry it leads with was derived rather than declared, exactly as a
    # scraped state is; the `~` in the text says *which* entry, the dash says "not from the session".
    findings = s.get("findings") or []
    head = report_head(s)
    d["report"] = report_line(s)
    d["report_derived"] = bool(head and head.get("source", "declared") != "declared")
    d["findings_line"] = f"{len(findings)} filed" if findings else ""
    # design §4.5a card / Focus header **out of work** chip (§4.9a, TD-053 step 6). Not a state —
    # the session still reads `idle` or `exited` — and shown for any session that declared it, since
    # a hand-started worker may run out too. The words are fixed and the `why` is the hover, because
    # the reason is a paragraph naming every entry the session looked at: a card cannot hold it, and
    # a card that tried would push the report line off. Dropped the moment the session claims again,
    # which is the record's own rule (§4.9a: a session that claims has work again).
    oow = s.get("out_of_work")
    oow = oow if isinstance(oow, dict) else {}  # one malformed record must not empty the grid
    d["out_of_work"] = (
        {"why": str(oow.get("why") or "").strip(), "age": _age(oow.get("at"), now)} if oow.get("at") else None
    )
    # design §4.5a **restart wanted** chip (§4.9a *A run that ends with work left*, TD-083): the
    # third ending — *my run is over and my lane is not*. Shaped exactly like `out_of_work` above,
    # and for the same reasons: fixed words, the `why` on hover because it is a sentence a card
    # cannot hold, and one malformed record costs that card its chip and not the grid.
    #
    # **`early` is carried, and it is the one thing the design did not have to say.** The home
    # marks a restart asked for inside `RESTART_EARLY` of the record's own start, and a controller
    # **does not act on it** — a run that was over before it began did not run out of context. So
    # an early one must not read as an ordinary one: a person seeing the same chip would expect the
    # same thing to happen next, and nothing will.
    rw = s.get("restart_wanted")
    rw = rw if isinstance(rw, dict) else {}
    d["restart_wanted"] = (
        {
            "why": str(rw.get("why") or "").strip(),
            "age": _age(rw.get("at"), now),
            "early": bool(rw.get("early")),
            "at": str(rw.get("at")),  # the Inbox's restart row is keyed on it (§4.5a, TD-103)
        }
        if rw.get("at")
        else None
    )
    # design §4.8a (TD-077 step 2): the identity alarms kept on this record — requests that named
    # this session from somewhere it does not live. A **mark**, never a control: it says *a person
    # should look*, and what to do about it is a row in the Inbox. Shaped like the chips above, so
    # one malformed entry costs that card its mark and not the grid.
    d["alarms"] = alarm_view(s.get("identity_alarms"))
    d["alarm_note"] = alarm_note(d["alarms"])
    d["suspended_note"] = suspended_note(s.get("suspended"))
    # design §4.5a card **doing** line (§4.8, TD-074): what the session says it is doing, always with
    # its age — *says · 11m ago* — so a stale line reads as stale. Text a model wrote: shown, never
    # acted on, and escaped like everything else. `None` for a session that has said nothing, which
    # is what makes the slot fall back to the tail; shaped like the chip above, so one malformed
    # record costs that card its line and not the grid.
    doing = s.get("doing")
    doing = doing if isinstance(doing, dict) else {}
    text = str(doing.get("text") or "").strip() if isinstance(doing.get("text"), str) else ""
    d["doing"] = {"text": text, "age": _age(doing.get("at"), now)} if text else None
    # design §4.5a card / Focus header **title** (§4.3 `title()`, TD-074): the session's name as its
    # tool holds it, observed from the pane and cleaned there. Display only and always shown when
    # there is one — it is a name, not a status, so it is not a fallback for the `doing` line. Empty
    # for every adapter that gives none, which is what draws nothing.
    tool_title = s.get("title")
    d["title"] = tool_title.strip() if isinstance(tool_title, str) else ""
    # on a card only when it says something the name does not (TD-095): a team's members are
    # titled by their names, and the same word twice is noise. Focus shows it as before.
    d["title_shown"] = d["title"] if d["title"] != s.get("name") else ""
    # The role's icon (design §4.8 *Role presets*): resolved here from the role's *name* — nothing in
    # the core keys on a role (§9 invariant 9) and no icon is stored on the record. Without a map
    # (a caller that did not resolve one) the badge draws its word alone, as it always has.
    # the role badge (design §4.8 *The names*, TD-076): its picture and its **label** — what it
    # shows in place of the bare key. Resolved with the icon, off the render path; a view built
    # without `icons` still says the role, by its default label, never nothing.
    look = (icons or {}).get((str(s.get("repo") or ""), str(s.get("role") or "")))
    d["role_icon"], d["role_label"] = look or ("", repoconfig.default_label(str(s.get("role") or "")))
    # design §6 / §4.5a: when this session stops, from the same formatter `ao status -v` uses, in
    # the host's local clock. Empty for every session nothing will stop, which is most of them.
    d["stop_note"] = stop_note(s)
    d["gated"] = gated_view(s.get("gated"))  # the usage gate's pause (§6, TD-100): a mark, never a state
    d["grants_all"] = list(GRANTS)
    # The Focus header's mode toggle, under the name of what it does (design §4.5a, TD-096): Take
    # over an unattended session; hand an interactive one back where there is someone to hand it
    # to — its `controllers` as written, or a team — and otherwise just switch it.
    if s.get("unattended"):
        d["mode_act"] = "Take over"
        d["mode_title"] = (
            "you are watching: the terminal is read-only. Take over switches this session to interactive "
            "and gives you the keyboard — its controllers and the policies leave it alone until you hand it back"
        )
    else:
        d["mode_act"] = "Hand back" if s.get("controllers") or s.get("team") else "Switch to unattended"
        d["mode_title"] = (
            "switch this session to unattended: the terminal goes read-only, and its manager and the policies "
            "pick it up again. A stop time that passed while you held it is cleared; one still ahead stays"
        )
    # Membership, both directions (design §4.8, §4.5a, TD-036 step 3). `controllers` is on the
    # record; `members` is derived across the records on every render and never stored — the same
    # rule `ao status -v` follows, so the page and the CLI cannot disagree. A controller whose
    # session is gone keeps its entry and shows as its bare id: §4.8 surfaces it rather than
    # silently releasing the worker.
    by_id = {o.get("id"): o for o in (fleet or [])}
    # `controllers` stays exactly as the record has it — a list of ids, the same shape
    # `ao status --json` prints. The display form goes under its own name, so nothing downstream
    # has to know which of two shapes it was handed (review 2026-09-13).
    d["under"] = [
        {"id": c, "name": (by_id.get(c) or {}).get("name") or c, "gone": c not in by_id}
        for c in (s.get("controllers") or [])
    ]
    d["members"] = [
        {
            "id": o["id"],
            "name": o.get("name") or o["id"],
            "state": o.get("state"),
            "lane": ", ".join(o.get("lane") or []),
            "report": report_line(o),
        }
        for o in (fleet or [])
        if s.get("id") in (o.get("controllers") or [])
    ]
    # *under `<manager>`* is not drawn inside a team's own group when that manager is the only
    # controller (§4.5a, TD-095): the group says it. The card cannot know which group it is drawn
    # in, so it marks the chip and the stylesheet hides it there — a filtered grid still shows it.
    only = by_id.get(d["under"][0]["id"]) if len(d["under"]) == 1 else None
    d["under_is_manager"] = bool(
        only and s.get("team") and only.get("team") == s.get("team") and has_control(only.get("capabilities"))
    )
    d["holds_control"] = has_control(s.get("capabilities"))
    # `fleet_known=False`: the caller asked for the fleet and did not get it. An empty members list
    # then means *unknown*, and Ready to close must not read it as *none* (review of PR #195).
    d["ready"] = ready_to_close(s, d["members"] if fleet_known else None)
    d["ready_ok"] = bool(d["ready"]) and all(ok for _, ok in d["ready"])
    d["not_ready"] = [name for name, ok in d["ready"] if not ok]  # what *more ▾ → Close* says it waits on
    # §6 rule 4 (TD-103): nudged once in this idle stretch and still idle another twenty minutes
    # later — the host agent is done, and it is for a person or its manager to judge
    nudged, since = _iso(s.get("nudged_at")), _iso(s.get("since"))
    d["open_work"] = bool(
        state == "idle" and nudged and since and nudged >= since and now - nudged >= timedelta(minutes=20)
    )
    d["slot"] = card_slot(d)
    d["next_act"] = next_act(d)
    return d


def _clock(iso: Any) -> str:
    """An instant as the host's local clock, *14:00*, with the day once it is not today (*Thu
    14:00*) — `stop_note`'s form, so a time on a card reads the same wherever it is. "" for anything
    unreadable, which costs a line its time and never the page."""
    at = _instant(iso)
    if at is None:
        return ""
    at = at.astimezone()
    return ("" if at.date() == datetime.now().astimezone().date() else at.strftime("%a ")) + f"{at:%H:%M}"


def gated_view(raw: Any) -> dict[str, str] | None:
    """design §4.5a **paused · usage** mark (§6 *Usage gate*, TD-100 slice 3): the record's `gated`
    as the words a card's slot and the Focus header show — *paused · usage — grind week 75% ≥ 70%,
    line moves 14:00 · pause sent* — composed from the mark's own fields, never from anything the
    session said. None when there is no mark, or none this can read: one malformed record costs its
    card the mark and not the grid. A mark, not pressable; it goes when the resume send clears it."""
    if not isinstance(raw, dict):
        return None
    pct, line = raw.get("pct"), raw.get("line")
    if any(isinstance(n, bool) or not isinstance(n, int | float) for n in (pct, line)):
        return None  # a bool is an int to isinstance, and `True%` is no reading
    prof = str(raw.get("profile") or "default")
    text = f"paused · usage — {prof} {raw.get('label') or '?'} {pct:g}% ≥ {line:g}%"
    if nxt := _clock(raw.get("next")):
        # a flat reserve's line moves only at the reset, where the honest word is *resets* (§4.5a)
        same = _instant(raw.get("next")) is not None and _instant(raw.get("next")) == _instant(raw.get("resets"))
        text += f", {'resets' if same else 'line moves'} {nxt}"
    if raw.get("sent_at"):
        text += " · pause sent"
    since = _clock(raw.get("since"))
    full = (
        f"{text}. Paused by the usage gate{f' since {since}' if since else ''}: the profile crossed the line "
        "its reserve makes (design §6), so the session was asked to pause"
        + ("" if raw.get("sent_at") else " — the ask is not typed yet, it waits for a clear composer")
        + ". It resumes by itself when every window is back under its line; to go on now, Take over, "
        "or lower the reserve with `ao gate`."
    )
    return {"text": text, "full": full}


BRANCH_SHOWN = 34  # characters of row 3's branch a card shows before it shortens it in the middle


def _middle(text: str, width: int) -> str:
    """`text` shortened in the middle to `width` characters, so both ends read — a branch is told
    apart by its prefix (`td095-`) and its end (`-rows`) alike. Whole when it fits."""
    if len(text) <= width:
        return text
    keep = width - 1
    return f"{text[: keep - keep // 2]}…{text[len(text) - keep // 2 :]}"


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ""


def card_slot(d: dict[str, Any]) -> dict[str, Any]:
    """The card's slot (design §4.5 *The card's anatomy*, row 5; §4.5a **doing**, TD-095): **one
    text, the first that applies**, and a caption. (a) what needs a person or explains a stop, (b)
    an ending — exited, closed, or a declaration — (c) what the session says it is doing, (d) its
    last output. The caption: the time a pending answer has left, else *ready to close ✓* whenever
    the checklist passes, else *says · age* under a `doing` line. `text` is a session's or a tool's
    words: escaped by the template, shown, never a control.

    `kind` picks the rule's colour (`needs`, `lim`, `bad`, `ok`, `doing`, `tail`, or "") and `full`
    is the hover; a working pane's two lines keep their line break (`tail`)."""
    state, pend = d["state"], d["pending"]
    ptext = str(pend.get("text") or "")
    kind, text, full = "", "", ""
    if state == "needs-you" and pend:
        # a hook permission says its tool and command; a question says that it is one
        kind = "needs"
        text = ptext if pend.get("kind") == "permission" else f"{pend.get('kind')}: {ptext}"
    elif state == "unreachable" and pend and pend.get("host_unreachable"):
        # design §4.4a "Permission prompts follow the same line": the waiter is on the node
        kind, text = "needs", f"{pend.get('kind')}: {ptext} — answer it at {d['host']}"
    elif state == "limited" and pend:
        kind, text = "lim", ptext
    elif state == "stalled?" and pend:
        # design §4.2: "a `stalled?` that can say why" — a screen rule's note (TD-032)
        kind, text = "needs", ptext
    elif state == "unreachable" and d["host_note"]:
        text = d["host_note"]
    elif d.get("gated"):
        # the usage gate's pause explains a stop (§4.5a **paused · usage**, TD-100); it waits behind
        # a permission, a question, a limit or a stall above, which are a person's to answer
        kind, text, full = "lim", d["gated"]["text"], d["gated"]["full"]
    elif d.get("seat") and isinstance(d.get("restart_ceiling"), dict):
        # §6 rule 3's fill ceiling (TD-103): the seats sharing its controller were filled six times
        # in the hour, and this one's fill tripped it — an ending, as the crash ceiling's is
        n = d["restart_ceiling"].get("count")
        kind, text = "bad", f"fills exhausted · {n if isinstance(n, int) else '?'} in 1 h"
        full = (
            f"{text}: the host agent filled this seat and the ones beside it as often as it will "
            "(design §6) — it is yours now: Resume it, or Forget it"
        )
    elif d.get("seat"):
        # what would make it come (§4.5): the techlead's trigger is a question landing (§4.9b)
        # (§4.9b) — or a seat's own trigger: after n PRs, every so often (TD-098)
        text = f"on call — {d.get('seat_when') or 'comes on the next question'}"
        full = (
            f"{text}: a question to it fills the seat, and it ends again once it has answered (design §4.9b)"
            if not d.get("seat_when") or d["seat_when"] == "comes on the next question"
            else f"{text}: the host agent fills the seat when that comes due, and it ends once it has run (§6)"
        )
    elif state == "exited" and isinstance(d.get("restart_ceiling"), dict):
        # an ending (§4.5 row 5 (b), §6 *Keeping a team running* rule 1, TD-103): the tick restarted
        # it as often as it will, and the session is a person's now
        n = d["restart_ceiling"].get("count")
        kind, text = "bad", f"restarts exhausted · {n if isinstance(n, int) else '?'} in 2 h"
        full = (
            f"{text}: it exited on its own each time and the host agent restarted it, up to its ceiling "
            "(design §6) — it is yours now: Resume it, or Forget it"
        )
    elif state == "exited":
        code = d.get("exit_code")
        kind, text = ("bad" if code else ""), "exited" + (f" · code {code}" if code is not None else "")
    elif state == "closed":
        kind, text = "ok", "closed by you"
        full = f"closed by you at {d['closed_at']}" if d.get("closed_at") else ""
    elif d["out_of_work"] or d["restart_wanted"]:
        # a declaration (§4.9a): the fixed words, then the first line of its reason
        # (§4.9a); the whole reason and when it was said are the hover — a card holds one clock
        said = d["out_of_work"] or d["restart_wanted"]
        words = "out of work" if d["out_of_work"] else "restart wanted"
        if not d["out_of_work"] and said["early"]:
            words += " · early — for a person"
        why = said["why"]
        text = words + (f" — {_first_line(why)}" if why else "")
        when = f" {said['age']} ago" if said["age"] else ""
        full = f"{words}{when} — {why or 'no reason recorded'}"
        if not d["out_of_work"] and said["early"]:
            full += " — asked inside its own first half hour, so a controller does not act on it (design §4.9a)"
    elif d.get("open_work"):
        kind, text = "lim", "idle · open work"
        full = (
            "idle with its work open: the host agent nudged it once, twenty minutes into this stretch, and it "
            "is still idle — yours or its manager's to judge (design §6)"
        )
    elif d["doing"]:
        kind, text = "doing", d["doing"]["text"]
    elif state in ("working", "stalled?"):
        # the pane's last two lines, as they stand — for a shell or a command run that is the work
        tail = [str(line) for line in (d.get("tail") or [])[-2:] if str(line).strip()]
        kind, text = "tail", "\n".join(tail) or "at prompt"
    else:
        tail_last = (d.get("tail") or [""])[-1]
        text = f"last: {tail_last}" if tail_last else "at prompt"
    caption, ccls = "", ""
    if state == "needs-you" and pend.get("kind") == "permission" and pend.get("tool_use_id"):
        caption, ccls = "via hook", "countdown"  # the page's clock fills in the time left
    elif d.get("seat"):
        # never *ready to close ✓*: a seat is not closed while the definition names it (§4.5)
        # *last ran* for a seat with a trigger: it runs its brief rather than answering (§4.5, TD-098)
        came = "last came" if d.get("seat_when") in ("", "comes on the next question") else "last ran"
        caption = came + (f" · {d['age']} ago" if d.get("age") else "")
    elif d["ready_ok"] and state in ("idle", "exited"):
        caption, ccls = "ready to close ✓", "ready"
    elif kind == "doing":
        caption = "says" + (f" · {d['doing']['age']} ago" if d["doing"]["age"] else "")
    return {"kind": kind, "text": text, "full": full or text, "caption": caption, "ccls": ccls}


def next_act(d: dict[str, Any]) -> str:
    """The foot's first button, by state (design §4.5 *The card's anatomy*, row 6, TD-095): what a
    person would press next. `allow` (with Deny beside it) for a hook permission; `forget` for an
    exited session, ready to close or not — there is no process left to close; `close` for an idle
    session the checklist passes; `details` when the pane is gone; else `focus`. A `limited`
    session's *Switch profile…* / *Wait* have no route yet, so it falls to Focus. A seat on call
    (TD-097) → `message`: asking it is how it comes, and it is never Forget or Close session."""
    state, pend = d["state"], d["pending"]
    if state == "needs-you" and pend.get("kind") == "permission" and pend.get("tool_use_id"):
        return "allow"
    if d.get("seat"):
        return "message"
    if state == "exited":
        return "forget"
    if state == "idle" and d["ready_ok"]:
        return "close"
    if state == "closed" or d.get("pane") is False:
        return "details"
    return "focus"


def ready_to_close(s: dict[str, Any], members: list[dict[str, Any]] | None = ()) -> list[tuple[str, bool]]:
    """Phase 1 subset of the checklist (design §4.2): tree clean, branch pushed, no subagents — and,
    for a session other sessions list as a controller, no live member. That last one comes from the
    control graph, not from a role: it covers a lead, a director over leads, and a session attached
    by hand with `ao control`, and a session that controls nothing never sees it. A lead idle
    between rounds with its log pushed used to read *ready to close ✓* over three working members,
    one click from orphaning them (seen 2026-09-17)."""
    git = s.get("git") or {}
    checks = []
    if s.get("dir") and git:
        checks.append(("tree clean", git.get("dirty", 0) == 0))
        # one measure, computed by the host agent and read here (design §4.2, TD-080): a branch
        # with no upstream is no longer *not pushed* by definition — rule 3 looks for the commit
        # on the remote-tracking branches, which is what a merged worker on a detached HEAD needs
        pushed = git.get("unpushed", 0) == 0
        label = (
            "branch pushed"
            if pushed or not git.get("pushed_against")
            else f"branch pushed (vs {git['pushed_against']})"
        )  # noqa: E501
        checks.append((label, pushed))
    checks.append(("no subagents running", (s.get("subagents") or 0) == 0))
    # design §4.2 / §4.10 *Outcomes* (TD-079): the person answered this session's question and has
    # not been told what came of it. `ao progress none` is refused on the same fact; this row is
    # for a session that exits some other way and never declares anything.
    owed = (s.get("mail") or {}).get("owed") or []
    checks.append((f"outcomes reported ({len(owed)} owed)" if owed else "outcomes reported", not owed))
    # Design §4.2, §4.9a (TD-072, TD-141): mail nobody read is mail nobody triaged. `ao progress
    # none` refuses on the same fact; this row is for a session that exits some other way.
    unread = s.get("unread") or 0
    checks.append((f"mail read ({unread} unread — `ao inbox`)" if unread else "mail read", not unread))
    if members is None:
        checks.append(("members unknown — the host agent did not list the sessions; reload", False))
    elif members:
        up = [m["name"] for m in members if m.get("state") not in DEAD]
        label = f"no live members ({', '.join(up)} — stop the team first)" if up else "no live members"
        checks.append((label, not up))
    return checks


DEFS_TTL = 5.0  # seconds the events stream keeps the team definitions it read (design §4.5a)
NO_TEAM = ""  # the group key for sessions carrying no `team` badge; rendered as *No team*, last
DEAD = ("exited", "closed")


def card_order(v: dict[str, Any]) -> tuple[float, bool, str]:
    """The grid's one order (design §4.5 *One order, no control*): urgency first, then — within one
    urgency — an `interactive` session ahead of an unattended one (TD-095, second pass: the person's
    own are what a person looks for), then the name. The manager's card is placed first before any
    of this, by `team_groups` and by the page's layout."""
    return (v["rank"], bool(v.get("unattended")), str(v.get("name") or ""))


# The header's counts, in the order the grid sorts by (§4.5 *One order*). *needs you* is not among
# them: the header carries it as its ringed mark, which is what a person scans a page of headers for.
COUNT_ORDER = (
    ("limited", "limited"),
    ("stalled?", "stalled?"),
    ("unreachable", "unreachable"),
    ("working", "working"),
    ("unseen", "unseen"),
    ("idle", "idle"),
    ("oncall", "on call"),
    ("exited", "exited"),
    ("closed", "closed"),
)


def state_counts(members: list[dict[str, Any]]) -> list[str]:
    """A team header's counts by state — `["1 limited", "2 working", "1 unseen"]`, in urgency order,
    zeros left out. An idle session nobody has looked at counts as *unseen*, and a seat with nobody
    in it as *on call* (TD-097), as their pills say."""
    tally: dict[str, int] = {}
    for m in members:
        key = "unseen" if m.get("unseen") else "oncall" if m.get("seat") else str(m.get("state") or "")
        tally[key] = tally.get(key, 0) + 1
    return [f"{tally[k]} {label}" for k, label in COUNT_ORDER if tally.get(k)]


def group_place(members: list[dict[str, Any]]) -> str:
    """Where a team's sessions are, said once in its header so no card has to (TD-095): the
    `host / repo` they share, or *mixed* when they do not. Empty for a group with no sessions."""
    places = {str(m.get("place") or "") for m in members}
    return "" if not places else places.pop() if len(places) == 1 else "mixed"


def prs_waiting(members: Collection[dict[str, Any]], now: datetime | None = None) -> dict[str, Any] | None:
    """Design §4.5a **team header** → *PRs waiting* (§4.9b *The reader*, TD-093): the held PRs
    put in front of the team's reader and not yet answered, from each record's `prs_waiting` — the
    seat's, in practice — as `{n, age}` of the oldest. A count and a time, never the entries. None
    when nothing waits, or when no record carries the field (a host agent older than it)."""
    got = [m["prs_waiting"] for m in members if isinstance(m.get("prs_waiting"), dict)]
    n = sum(int(w.get("n") or 0) for w in got if isinstance(w.get("n"), int))
    if n <= 0:
        return None
    oldest = min((str(w["oldest"]) for w in got if w.get("oldest")), default="")
    return {"n": n, "age": _age(oldest, now or datetime.now(UTC))}


def team_groups(views: list[dict[str, Any]], rows: Collection[dict[str, Any]] = ()) -> list[dict[str, Any]] | None:
    """Design §4.5a Org **team groups** (§4.9, §9 invariant 9): the grid grouped by the `team` badge,
    derived from the views on every render and every delta, never stored. `rows` is the definitions
    (`teamrun.rows`). `None` when no session carries a badge and nothing is defined — the page then
    renders the flat grid, with no header anywhere. A team with nothing live keeps its group
    (2026-09-18): dead cards under a team's name are still that team's, and a definition no session
    carries is a group with no members, because its card is where Start lives.

    The badge decides the group; `controllers` decides the manager: the one member holding
    `control` that other members of the same group list as a controller. A group without one
    has no manager card and its header says so. Within a group the manager comes first, then the rest in
    urgent-first order (the same `rank`, `name` key the flat grid sorts by); the client re-sorts
    per group in Pinned mode. Down the page: the teams with something live, the sessions with no
    badge as *No team*, then the teams with nothing live.

    A manager carrying a different badge from its members — which `ao team start` never produces, but a
    hand-typed `ao new --team` can — is still found, by looking across the whole fleet rather than
    only inside the group (review of PR #117). Its card stays where its own badge puts it; the
    header names it and says so, because moving the card would contradict the badge."""
    defs = {str(r["name"]): r for r in rows}
    by_team: dict[str, list[dict[str, Any]]] = {name: [] for name in defs}
    for v in views:
        by_team.setdefault(str(v.get("team") or NO_TEAM), []).append(v)
    if not any(t != NO_TEAM for t in by_team):
        return None
    groups: list[dict[str, Any]] = []
    for team in sorted(by_team):
        members = sorted(by_team[team], key=card_order)
        manager, manager_elsewhere = None, False
        if team != NO_TEAM:
            named = {c for m in members for c in (m.get("controllers") or [])}
            # The fleet, not just this group: a manager whose own badge differs is still this group's
            # manager, and saying "managed by you" over a group that plainly has one would be a lie.
            managers = sorted(
                (v for v in views if has_control(v.get("capabilities")) and v["id"] in named),
                key=lambda v: (str(v.get("team") or "") != team, v["rank"], v["name"]),  # our own badge first
            )
            if managers:
                manager = managers[0]
                if manager in members:
                    members.remove(manager)
                    members.insert(0, manager)
                else:
                    manager_elsewhere = True
        projects = sorted({str(m.get("project")) for m in members if m.get("project")})
        row = defs.get(team) or {}
        live = sum(1 for m in members if m.get("state") not in DEAD)
        c = row.get("concluded")
        # the definition's rows read the raw records; a view that disagrees about what is live (a
        # delta between the two reads) is not drawn concluded — Wind down is the safe offer then
        concluded = c if live and isinstance(c, dict) and len(c.get("names") or ()) == live else None
        dead = [m for m in members if not m.get("seat")] if team != NO_TEAM and not live else []
        groups.append(
            {
                "team": team,
                "label": team or "No team",
                # the header names its manager only when that card is in another group (TD-095: its
                # name, state and line are on its own card, the first here); `role_label` is what it
                # is called there (§4.8 *The names*, TD-076): *Manager*, or its role's own label.
                "manager": {k: manager.get(k) for k in ("id", "name", "state", "role_label")} if manager else None,
                "manager_elsewhere": manager_elsewhere,  # its card sits under its own badge, not here
                "members": members,
                "ids": [m["id"] for m in members],
                "projects": projects or list(row.get("projects") or []),
                "needs": sum(1 for m in members if m.get("state") == "needs-you"),
                "prs_waiting": prs_waiting(members) if team != NO_TEAM else None,
                "live": live,
                # the header's own facts (design §4.5 *The card's anatomy*, TD-095): where the
                # team's sessions are, once, and how many are in each state — never its manager's
                # name, state or line, which are on the manager's card, the first in the group
                "place": group_place(members),
                "counts": state_counts(members),
                # a definition exists, so the group's card carries Start, or Stop / Stop now (§4.5a)
                "defined": team in defs,
                "source": row.get("source"),
                "def_manager": row.get("manager"),  # the definition's word, for a card with no sessions yet
                "def_members": row.get("members"),
                "def_techlead": row.get("techlead"),  # the seat's name (§4.9b), when the definition has one
                # *nothing running* and *nothing left to run* are different facts (§4.9a)
                "wound_down": row.get("wound_down"),
                "wound_down_age": row.get("wound_down_age"),
                # live, and every live session idle and declared (§4.5a, TD-099): drawn like a
                # stopped team — folded, sorted with them, Start alone — since a wind-down would
                # only wake the manager to find nothing to wind down
                "concluded": concluded,
                "concluded_age": row.get("concluded_age") if concluded else "",
                "stopped": not live or concluded is not None,
                # design §4.5a team card **Forget all** (TD-071 item 1): on a team with nothing live,
                # the Forget each card carries, on every card but those with the dirty / unpushed
                # flag — Forget drops the record that points at the worktree, and unpushed work would
                # lose its only pointer, so those are named apart and forgotten one at a time. A seat
                # with nobody in it is neither: its card never offers Forget while the definition
                # names it (`next_act`), and Forget all does not go round that
                "forget": [m for m in dead if not m.get("flag")],
                "forget_kept": [m for m in dead if m.get("flag")],
                # design §4.5a team header **✉ n** (TD-071 item 2): what the fold hides of the cards'
                # unread chips — display only, the mail stays where it is (§4.10)
                "unread": sum(int(m.get("unread") or 0) for m in members),
            }
        )
    # what is running is read first; *No team* is never "stopped" — nothing there starts as one
    # …and among the live teams, one with a session that needs a person comes first (2026-09-18)
    groups.sort(
        key=lambda g: (2 if g["team"] and g["stopped"] else 1 if not g["team"] else 0, not g["needs"], g["team"])
    )
    return groups


# -- the Inbox page (design §4.5 screen 6, §4.5a **Inbox page**, §4.10; TD-069 step 1) -------------

PERSON_ASK_KINDS = ("ask", "conflict")  # what reads as a question to the person; `steer` has its own rules
# design §4.5 screen 6 / §4.10 *Outcomes* (TD-079 step 2): **Waiting on them** is the fourth
# section, under *Steering* and in neither number — it waits on a session, not on the person.
# §4.9b (TD-075 step 2): **Answered for you** follows it, uncounted too — what a teammate answered
# for the person from the record, each with **Overrule**.
INBOX_SECTIONS = ("needs", "steering", "waiting", "answered", "fyi", "snoozed")


def _entry_open(e: dict[str, Any]) -> bool:
    """Design §4.10 *One way of being closed*, mirrored for the dicts the RPC hands the page: an
    entry is open exactly when it is an `ask`, `steer` or `conflict` with no `closed_reason` —
    and, for entries written before 2026-09-19, one with no `closed_by` and no `expired_at`."""
    if e.get("kind") not in (*PERSON_ASK_KINDS, "steer"):
        return False
    if e.get("closed_reason"):
        return False
    return not (e.get("closed_by") or e.get("expired_at"))


def _iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _find_text(*parts: Any) -> str:
    """What the page's free-text filter matches on, lowercased once here rather than in the
    browser: the same `data-find` the mail rows carry."""
    return " ".join(str(p).strip() for p in parts if str(p or "").strip()).lower()


# design §4.5a **Inbox row: state**: the row kinds that are a *needs you* session, and so exactly
# what the Org's needs-you count counts. Kept beside `state_kind` because the two are one rule.
NEEDS_YOU_ROWS = ("permission", "question", "needs")


def state_kind(v: dict[str, Any]) -> str:
    """Which state row this session is, or "" for one that needs nobody (design §4.5a **Inbox row:
    state**). **The one predicate the Inbox and the Org's needs-you count both read**, so every
    session the Org counts as `needs-you` has exactly one row on the Inbox and the page's hover
    text — *the session states the Org counts too* — is true (review of PR #251).

    A `needs-you` record whose `pending` is empty, is not a dict, or names a kind this build does
    not know is still a session stopped for a person: it becomes a plain **needs** row with
    **Open** and no Allow / Deny, because nothing structured came with it and a control built from
    what is not there is the thing §4.2 and TD-071 item 8 forbid."""
    state = v.get("state")
    pend = v.get("pending")
    pend = pend if isinstance(pend, dict) else {}
    if state == "needs-you":
        if pend.get("kind") == "permission" and pend.get("tool_use_id"):
            return "permission"
        return "question" if pend.get("text") or pend.get("kind") else "needs"
    if state == "stalled?":
        return "stalled"
    if state == "limited":
        return "limited"
    if state == "exited" and v.get("flag") and [name for name, ok in (v.get("ready") or []) if not ok]:
        # "exited with unpushed work" (§4.5a, TD-069): what Ready to close says, in its own words —
        # the row goes when the work is pushed or the session is forgotten.
        return "unpushed"
    return ""


def restart_mark(v: dict[str, Any]) -> tuple[str, str] | None:
    """Design §4.5a **Inbox row: restart** (§6 *Keeping a team running*, TD-103 slice 5): `(the
    mark's own time, the row's words)` for a record the tick could not restart — at a ceiling,
    held by work left, or an `early` `restart_wanted` that neither a controller nor the tick acts
    on (§4.9a) — else None. The words are fixed, from the record's fields; the `why` of an early
    one is the session's own sentence and is shown as such, never acted on."""
    if v.get("superseded_by"):
        return None
    ceiling, held, wanted = v.get("restart_ceiling"), v.get("restart_blocked"), v.get("restart_wanted")
    if isinstance(ceiling, dict):
        n = ceiling.get("count")
        n = n if isinstance(n, int) else "?"
        if ceiling.get("why") == "fill":
            return str(ceiling.get("at") or ""), (
                f"fills exhausted · {n} in 1 h — the seats beside it were filled as often as the host agent will"
            )
        return str(ceiling.get("at") or ""), (
            f"restarts exhausted · {n} in 2 h — it exited on its own each time and the host agent restarted it "
            "up to its ceiling"
        )
    if isinstance(held, dict):
        return str(held.get("at") or ""), (
            f"restart held: {held.get('dirty')} uncommitted and {held.get('unpushed')} unpushed in its checkout "
            "— it is restarted by itself the moment they are pushed"
        )
    if isinstance(wanted, dict) and wanted.get("early") and v.get("state") in ("idle", "exited"):
        why = _first_line(wanted.get("why") or "") or "no reason given"
        return str(wanted.get("at") or ""), f"restart wanted · early — asked inside its first half hour: {why}"
    return None


def state_rows(
    views: Collection[dict[str, Any]],
    *,
    host_alarms: Collection[dict[str, Any]] = (),
    host: str = "",
    identity_mode: str = "",
) -> list[dict[str, Any]]:
    """Design §4.5 screen 6 / §4.5a **Inbox row: state** (TD-069 step 2): a session's state as a
    row of the Inbox, one per thing that needs a person. Built from the card views the Org is
    rendered from — the same `view()`, so a row's pill, `doing` line, `title`, team and role are
    the card's own and cannot drift from it — and never from anything parsed off a screen
    (TD-071 item 8).

    The kinds, in §4.5a's words: a pending **permission** (what is asked, the time left, Allow /
    Deny through the hook channel); a pending **question** and **stalled?** (the text or the host's
    note, **Open**); **limited** (the reset time); an **exited** session with unpushed work (what
    Ready to close says, **Open**). To them TD-077 step 2 adds the **identity alarm** rows: one per
    record that has alarms, and one for the host's own list — *it is either a bug of ours or a
    session misbehaving and a person should know which* (§4.8a).

    A row has no `snoozed_until` and no Snooze: a state lives on the record, and there is nowhere
    to keep a person's *not now* (the gap is written up in TD-069 step 2's entry). It leaves the
    list the moment the state does, which is the next poll."""
    rows: list[dict[str, Any]] = []
    now = datetime.now(UTC)  # one instant for the whole list, as `inbox_sections` takes one

    def base(v: dict[str, Any], row: str, text: str, *, extra: str = "") -> dict[str, Any]:
        doing = v.get("doing") or {}
        return {
            "row": row,
            "id": f"{v['id']}:{row}",  # the row's own key on the page; a record may raise two
            "sid": v["id"],
            "name": v.get("name") or v["id"],
            "team": v.get("team") or "",
            "role": v.get("role") or "",
            "role_icon": v.get("role_icon") or "",
            "role_label": v.get("role_label") or "",
            "title": v.get("title") or "",
            "doing": v.get("doing"),
            "state": v.get("state") or "",
            "state_class": v.get("state_class") or "",
            "state_label": v.get("state_label") or "",
            "scraped": bool(v.get("scraped")),
            "suspended_note": v.get("suspended_note") or "",  # §4.8a: the mark rides with the record
            "host": v.get("host") or "",
            "text": text,
            "deadline": v.get("deadline") or "" if row == "permission" else "",
            # §4.5 screen 6 *Layout* (TD-082): the countdown is rendered in words here, not left as
            # `…` for the client's first tick. The words are `fmtLeft`'s, so nothing jumps.
            "left": _countdown(v.get("deadline") if row == "permission" else None, now),
            "at": v.get("since") or "",
            "age": v.get("age") or "",
            "find": _find_text(v.get("name"), v.get("title"), doing.get("text"), text, extra),
        }

    for v in sorted(views, key=lambda v: (str(v.get("since") or ""), str(v.get("id") or ""))):
        pend = v.get("pending")
        pend = pend if isinstance(pend, dict) else {}
        text = str(pend.get("text") or "")
        kind = state_kind(v)
        if kind == "permission":
            rows.append(base(v, kind, text))
        elif kind == "question":
            rows.append(base(v, kind, f"{pend.get('kind')}: {text}" if pend.get("kind") else text))
        elif kind == "needs":
            rows.append(base(v, kind, "it is waiting on a person, and nothing came with it saying what for"))
        elif kind == "stalled":
            rows.append(base(v, kind, text or v.get("host_note") or "no output for a while, and no note"))
        elif kind == "limited":
            rows.append(base(v, kind, text or "the profile is at its cap"))
        elif kind == "unpushed":
            unmet = [name for name, ok in (v.get("ready") or []) if not ok]
            rows.append(base(v, kind, f"{v['flag']} — {', '.join(unmet)}", extra=v.get("where") or ""))
        if mark := restart_mark(v):
            # its own row beside any state row, as an alarm is: a crashed record at its ceiling can
            # also be exited with unpushed work, and the two are answered differently
            rows.append({**base(v, "restart", mark[1]), "at": mark[0] or v.get("since") or "", "age": ""})
        if alarms := v.get("alarms"):
            row = base(v, "alarm", alarm_note(alarms))
            # an alarm row is as old as its newest alarm, not as its session: what the order is
            # about is when the thing that needs a person happened
            rows.append(
                {
                    **row,
                    "at": max((a["last"] or a["at"]) for a in alarms) or row["at"],
                    "age": "",
                    "alarms": alarms,
                    # §4.8a *An alarm's answers* (TD-077 b): who **Log TD** would hand these to — the
                    # home's own answer (`alarm_to`, the record's first live controller from the
                    # control graph), never worked out here, so drawn-or-not and the RPC's
                    # refused-or-not cannot disagree.
                    "alarm_to": alarm_to_view(v.get("alarm_to")),
                    "mode": identity_mode,
                    "find": _find_text(row["find"], *(a["words"] for a in alarms)),
                }
            )
    if host_alarms:
        rows.append(
            {
                "row": "alarm_host",
                "id": "host:alarm",
                "sid": "",
                "name": host or host_name(),
                "team": "",
                "role": "",
                "role_icon": "",
                "role_label": "",
                "title": "",
                "doing": None,
                "state": "",
                "state_class": "",
                "state_label": "",
                "scraped": False,
                "host": host,
                "text": alarm_note(list(host_alarms)),
                "deadline": "",
                "at": max((a["last"] or a["at"]) for a in host_alarms),
                "age": "",
                "find": _find_text(host, "identity alarm", *(a["words"] for a in host_alarms)),
                "alarms": list(host_alarms),
                "mode": identity_mode,
            }
        )
    return rows


# design §4.5 screen 6 / §4.4 (TD-069 step 3): **board items** in the Inbox. The board is each
# repo's `docs/user_attention.md`, in dev-cadence's format, and its reader is dev-cadence's own
# `nudge_user_attention.py --report --json` — never a second parser here (§4.4: the items, with the
# board line each sits on, come from that report). It is run over the boards of the repos this host
# knows, with the script from the first of them that carries it (a SYNCED file: every copy is the
# same), at most once every `BOARD_TTL` seconds, since the page and the top bar poll every few.
BOARD_FILE = Path("docs") / "user_attention.md"
BOARD_SCRIPT = Path("scripts") / "nudge_user_attention.py"
BOARD_TTL = 60.0
BOARD_ACTS = ("snooze", "done")  # §4.5a's two answers to a board row, the `board_edit` RPC's actions
BOARD_TIMEOUT = 20.0


def board_argv(roots: Collection[str | Path]) -> tuple[list[str] | None, str]:
    """The command that reads the due items of these repos' boards, or None and a note saying why
    nothing is read. No board anywhere is not a fault and has no note; boards with no reader do,
    since the Inbox would otherwise look clear when it is not (§4.5 *no silent failure path*)."""
    rs = [Path(r).expanduser() for r in roots]
    boards = [str(r / BOARD_FILE) for r in rs if (r / BOARD_FILE).is_file()]
    if not boards:
        return None, ""
    script = next((r / BOARD_SCRIPT for r in rs if (r / BOARD_SCRIPT).is_file()), None)
    if script is None:
        return None, f"board items are not shown: no repo here carries {BOARD_SCRIPT}, dev-cadence's reader"
    argv = [sys.executable, str(script), "--report", "--due-only", "--json"]
    for b in boards:
        argv += ["--board", b]
    return argv, ""


def board_choices(roots: Collection[str | Path] | None = None) -> list[dict[str, str]]:
    """The boards *Put on the board* may write to (design §4.5a *Inbox row: FYI*, TD-140): each
    checkout this host's repos registry names that carries a board, as `{label, root, board}` with
    both paths resolved — the paths `board_edit` resolves against the same registry. On a node the
    org is the home's (§4.4a), so a node offers none."""
    if roots is None:
        if hosts.is_node():
            return []
        roots = hosts.local_host().repos()
    out: list[dict[str, str]] = []
    for r in roots:
        root = Path(r).expanduser().resolve()
        if (root / BOARD_FILE).is_file():
            out.append({"label": root.name, "root": str(root), "board": str(root / BOARD_FILE)})
    return out


def repo_teams(org: orgmod.Org, host: str) -> dict[str, str]:
    """Each checkout on `host` → the team whose projects hold it (§4.5 screen 6: *a board item
    carries its repo, which `org.yml`'s projects map to teams*). A repo two teams share is the
    first's, in definition order: a row carries one team badge, as a message does."""
    out: dict[str, str] = {}
    for tname, t in org.teams.items():
        for pname in t.projects:
            p = org.projects.get(pname)
            for by in p.repos.values() if p else ():
                path = by.get(host)
                if path:
                    out.setdefault(str(Path(path).expanduser().resolve()), tname)
    return out


def board_rows(report: Any, teams: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """design §4.5a **Due strip / Inbox board row** rows, as the Inbox draws them (TD-069 step 3): one
    per item the report says is due today or overdue — its repo, its due words, the whole text, and
    the board at that line in the editor. `at` is the due date, so *oldest first* in **Needs you**
    puts the longest overdue first among the states and the mail. The text is the board's, shown as
    text: nothing here is a control built from it (TD-071 item 8)."""
    rows: list[dict[str, Any]] = []
    boards = report.get("boards") if isinstance(report, dict) else None
    for b in boards if isinstance(boards, list) else ():
        if not isinstance(b, dict):
            continue
        root = str(b.get("root") or "")
        board = str(b.get("board") or "")
        label = str(b.get("label") or Path(root).name)
        team = (teams or {}).get(str(Path(root).resolve()), "") if root else ""
        for it in b.get("items") or ():
            if not isinstance(it, dict) or not it.get("text"):
                continue
            line, text, tag = it.get("line"), str(it["text"]), str(it.get("due_tag") or "")
            url = vscode_url(board) if board else ""
            if url and isinstance(line, int):
                head, _, query = url.partition("?")
                url = f"{head}:{line}" + (f"?{query}" if query else "")
            rows.append(
                {
                    "row": "board",
                    "id": f"board:{root}:{line}",
                    "repo": label,
                    "root": root,
                    "board": board,
                    "line": line,
                    "team": team,
                    "text": text,
                    "due": str(it.get("due") or ""),
                    "due_tag": tag,
                    "at": str(it.get("due") or ""),
                    "editor": url,
                    "find": _find_text(label, text, tag, "board"),
                }
            )
    return rows


def read_boards(run: Any = subprocess.run) -> tuple[list[dict[str, Any]], str]:
    """The due board items of the repos this host knows, as Inbox rows, and a note when they could
    not be read — a reader that failed is said in words, never shown as an empty board (§4.5 *no
    silent failure path*). On a node the org is the home's (§4.4a), so a node reads none."""
    if hosts.is_node():
        return [], ""
    argv, note = board_argv(hosts.local_host().repos())
    if argv is None:
        return [], note
    try:
        done = run(argv, capture_output=True, text=True, timeout=BOARD_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [], f"board items are not shown: the reader did not finish ({e})"
    if done.returncode != 0:
        why = (done.stderr or "").strip().splitlines()
        return [], f"board items are not shown: the reader exited {done.returncode}" + (f" — {why[-1]}" if why else "")
    try:
        report = json.loads(done.stdout)
    except ValueError:
        return [], "board items are not shown: the reader's output is not JSON"
    org, _ = org_here()
    return board_rows(report, repo_teams(org, host_name())), ""


def _needs_key(item: dict[str, Any]) -> tuple[int, str]:
    """The **Needs you** order (design §4.5 screen 6): *what is on the tool's clock first (a
    permission's countdown), then oldest first* — across states and mail together, which is why one
    key reads both."""
    if item.get("row") == "permission" and item.get("deadline"):
        return (0, str(item["deadline"]))
    return (1, str(item.get("at") or ""))


# design §4.10 *Outcomes*: the same predicate as `MailEntry.owes`, over the dict the RPC hands the
# page. It is duplicated rather than imported because the page reads entries, not models — and it
# is the *one* rule that decides which section a settled question is in, so it says so out loud.
OWING_CLOSES = ("replied", "go_with_it")
# …and **every** kind that can be one, which is not `PERSON_ASK_KINDS`: that pair is about which
# *open* entry is a question in *Needs you*, where a `steer` has its own branch. A debt is the
# model's `ASK_KINDS`, `steer` included — *a `go_with_it` close owes one too* (§4.10 *Outcomes*),
# and `go_with_it` is a `steer`'s own close reason. The agent enforces the debt on that set, so a
# narrower one here would leave a `steer`'s debt sitting in FYI, uncounted, while `ao progress
# none` was still refusing its sender (review of PR #287).
OWING_KINDS = ("ask", "steer", "conflict")


def _owing(e: dict[str, Any], record: dict[str, Any] | None, now: datetime) -> None:
    """Stamp a question the person answered with what *Waiting on them* and *Needs you* need
    (§4.10 *Outcomes*, §4.5a): whether it still owes an outcome, how long it has owed it, what the
    person answered as far as the inbox still holds it, and whether its asker has **exited without
    reporting** — which is what moves the row into the counted section, because then only a person
    or the asker's manager can find out what happened.

    An asker whose record is **gone** is not this row: the home settles that as `asker_gone` and
    the question stops owing. `exited` is the live record that will not report by itself."""
    outcome = _outcome_of(e)
    e["owes"] = bool(
        "person" in (e.get("to") or [])
        and e.get("kind") in OWING_KINDS
        and e.get("closed_reason") in OWING_CLOSES
        and not outcome
    )
    e["owed_age"] = _age(e.get("closed_at"), now) if e["owes"] else ""
    # what the person answered, as far as the person inbox still holds it: a pressed suggested
    # answer is in the entry itself (§4.10 *Suggested answers*), and a typed reply is not — the
    # reply went to the asker's inbox, not to this one — so the row says which of the two it was
    # rather than inventing words the person did not write
    answers = e.get("answers") if isinstance(e.get("answers"), list) else []
    idx = e.get("answer")
    e["answer_given"] = str(answers[idx]) if isinstance(idx, int) and 0 <= idx < len(answers) else ""
    e["answer_how"] = "you let it go with its default" if e.get("closed_reason") == "go_with_it" else "you answered"
    e["asker_state"] = str((record or {}).get("state") or "")
    # the record here is the raw one off `list`, not a `view()` — so its `doing` is `{text, at}`
    # and the row wants `{text, age}`, as the card and the Focus header draw it (§4.8, TD-074)
    doing = (record or {}).get("doing")
    doing = doing if isinstance(doing, dict) else {}
    e["asker_doing"] = (
        {"text": str(doing.get("text") or ""), "age": _age(doing.get("at"), now)} if doing.get("text") else None
    )
    e["asker_gone_quiet"] = bool(e["owes"] and e["asker_state"] in ("exited", "closed"))
    if outcome:
        e["outcome_age"] = _age(outcome.get("at"), now)


def _outcome_of(e: dict[str, Any]) -> dict[str, Any]:
    """An entry's `outcome` (§4.10 *Outcomes*) as a dict, or `{}` — read as a shape, never trusted:
    an entry from another build may carry anything there and it must cost a row a line, not the
    page (the `_age` rule)."""
    o = e.get("outcome")
    return o if isinstance(o, dict) else {}


def _answered_of(e: dict[str, Any]) -> dict[str, Any]:
    """An entry's `answered` (§4.9b, TD-075 step 2) as a dict, or `{}` — the structured field the
    home writes on an *answered for you* FYI, `{question, asker, answerer, source}`. The row and its
    **Overrule** key on it and on nothing else: never on who sent the entry, never on a role (§9
    invariant 9). Read as a shape, as `_outcome_of` is."""
    a = e.get("answered")
    return a if isinstance(a, dict) and a else {}


def _trail_rows(trail: Collection[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """The trail as FYI rows (§4.10 *The Inbox is a queue*, TD-079): what a state row's **ending**
    left behind, so a row that resolved by some other road does not simply vanish. The home writes
    these, and they are not mail — the page gives them `at` and `age` so they sort and read beside
    the entries, and a `row` of `trail` so one renderer draws them."""
    out = []
    for t in trail:
        if not isinstance(t, dict) or not t.get("id"):
            continue
        at = str(t.get("last") or t.get("resolved_at") or "")
        # *up for* is how long the row **stood**, which is its ending minus its start — not its
        # age now, which is a different number and the one a reader would misread it as
        ended = _instant(at) or now
        out.append({**t, "row": "trail", "at": at, "age": _age(at, now), "for_words": _age(t.get("since"), ended)})
    return out


def inbox_sections(
    entries: Collection[dict[str, Any]],
    *,
    now: datetime | None = None,
    states: Collection[dict[str, Any]] = (),
    trail: Collection[dict[str, Any]] = (),
    attention_snoozed: dict[str, Any] | None = None,
    boards: Collection[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Design §4.5 screen 6: the person inbox split into the page's three sections, plus what is
    snoozed — and, from TD-069 step 2, the **session states** (`states`, from `state_rows`) joined
    into **Needs you** here rather than anywhere else. From step 3 the due **board items**
    (`boards`, from `board_rows`) join it the same way, counted: a due board item is waiting on a
    person by definition. They carry no snooze of the Inbox's own: a board row's Snooze is §4.4's
    write-back, which moves its `Due:` date on the board itself.

    - **Needs you** — the state rows, open `ask`s to the person (an open `conflict` too: it cannot be addressed to
      the person, §4.10, but one written before that gate would still be a question nobody else
      can answer) and **paused** `steer`s; oldest first.
    - **Steering** — open `steer`s whose clock is running; soonest bound first, and a `steer`
      that somehow carries no bound last.
    - **FYI** — everything else: `note`s, `system` notes, replies, and every closed entry until
      retention prunes it (`MAIL_RETENTION`, 12 h); newest first, because the useful end of a list
      nobody must act on is the recent end.
    - **Snoozed** — an entry whose `snoozed_until` is still ahead is in none of the three and in
      no count (§4.10 *Snooze*); soonest first, so the page can say *n snoozed — show* and a
      snooze is never a way to lose mail.

    `count` is the **Needs you** section's length, which is the top bar's number (§4.5a): what is
    waiting on a person, never unread mail. One computation, used by the page and by the poll.
    An unreadable `snoozed_until` reads as *not snoozed*: mail is never hidden by a bad field.

    - **Waiting on them** (§4.10 *Outcomes*, TD-079 step 2) — answered questions that owe an
      outcome and whose asker is still live: it waits on a session, not on the person, so it is in
      neither number. A debt whose asker **exited without reporting** is in *Needs you* instead,
      counted — only a person, or the asker's manager, can find out what happened — and so is a
      **`blocked`** outcome, which is not a dead end but work stopped on something only a person
      can move. A `done` or `dropped` outcome is FYI, shown under the question it closes.

    **A state row's snooze** (§4.10 *The Inbox is a queue*, TD-079 step 1b) lives in the home's own
    attention store, per record and row kind, because a state has no mail entry to carry one:
    `attention_snoozed` is that store as the RPC hands it over, and a row whose time is still
    ahead is in no section and no count, exactly as a snoozed entry is.

    The **trail** (§4.10) is FYI too: what a state row's ending left behind, so a row that resolved
    by some other road — the session was resumed, the permission was answered in the terminal —
    does not simply vanish.

    - **Answered for you** (§4.9b, TD-075 step 2) — the FYI the home files when a teammate answers
      a question from the record (a reply carrying `source`): it carries `answered`, and is here
      instead of in FYI, uncounted, newest first, so what was decided in the person's name is
      never mixed in with the notes."""
    at = now or datetime.now(UTC)
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in INBOX_SECTIONS}
    snoozed_rows = attention_snoozed or {}
    for r in states:
        raw = snoozed_rows.get(f"{r.get('sid') or ''}|{r.get('row') or ''}")
        if isinstance(raw, str) and raw.startswith("dismissed:"):
            # the restart row's **Dismiss** (§4.5a, TD-103): that one mark's row is gone from every
            # section; a new mark carries a new `at` and raises a new row
            if raw == f"dismissed:{r.get('at') or ''}":
                continue
            raw = None
        until = _iso(raw)
        if until and until > at:
            out["snoozed"].append({**r, "snoozed_until": str(raw), "until_words": _left(str(raw), at)})
        else:
            out["needs"].append(r)
    # The outcome `note` that settles a question is an ordinary entry in its own right, and is
    # **listed under its question** rather than beside it (§4.10 *Outcomes*) — so it is taken out
    # of the FYI list here and hung on the question's own row by `id`.
    settles = {str(_outcome_of(e)["by"]): e["id"] for e in entries if _outcome_of(e).get("by")}
    for e in entries:
        snoozed = _iso(e.get("snoozed_until"))
        outcome = _outcome_of(e)
        if snoozed and snoozed > at:
            out["snoozed"].append(e)
        elif _entry_open(e) and (e.get("kind") in PERSON_ASK_KINDS or e.get("paused_at")):
            out["needs"].append(e)
        elif _entry_open(e) and e.get("kind") == "steer":
            out["steering"].append(e)
        elif outcome and outcome.get("state") == "blocked":
            out["needs"].append(e)  # counted: work stopped on something only a person can move
        elif e.get("owes"):
            # the asker exited without reporting: only a person or its manager can find out what
            # happened, so that one is counted; the rest wait on a live session and are not
            (out["needs"] if e.get("asker_gone_quiet") else out["waiting"]).append(e)
        elif _answered_of(e):
            out["answered"].append(e)  # §4.9b: uncounted, and apart from FYI's notes
        elif e["id"] in settles:
            continue  # the reporting note: drawn under the question it closes, not beside it
        else:
            out["fyi"].append(e)
    out["fyi"].extend(_trail_rows(trail or (), at))
    out["needs"].extend(boards)
    out["needs"].sort(key=_needs_key)
    out["steering"].sort(key=lambda e: (not e.get("bound"), str(e.get("bound") or "")))
    out["waiting"].sort(key=lambda e: str(e.get("closed_at") or e.get("at") or ""))
    out["answered"].sort(key=lambda e: str(e.get("at") or ""), reverse=True)
    out["fyi"].sort(key=lambda e: str(e.get("at") or ""), reverse=True)
    out["snoozed"].sort(key=lambda e: str(e.get("snoozed_until") or ""))
    return {**out, "count": len(out["needs"]), "fyi_n": len(out["fyi"])}


# -- the rail (design §4.5 screen 6 *The rail* and *Find*, §4.5a **Inbox page: the rail**; TD-129,
# built by TD-135) ----------------------------------------------------------------------------------

# *Urgency*: the page's own sections in the page's order. The snoozed list is not one of them: it
# is in no section and no count (§4.10 *Snooze*).
RAIL_SECTIONS = ("needs", "steering", "waiting", "answered", "fyi")
RAIL_SECTION_NAMES = {
    "needs": "Needs you",
    "steering": "Steering",
    "waiting": "Waiting on them",
    "answered": "Answered for you",
    "fyi": "FYI",
}
# *Kinds*: a row's coarse kind, one per line (§4.5 screen 6 *The rail*)
RAIL_KINDS = ("questions", "steering", "states", "board", "notes", "trail")
RAIL_KIND_NAMES = {
    "questions": "questions",
    "steering": "steering",
    "states": "session states",
    "board": "board items",
    "notes": "notes",
    "trail": "trail",
}
RAIL_NO_TEAM = "none"  # a row that carries no team, in the URL and on the rail's *no team* line
_FIND_EDGE = ",.;:!?()[]{}\"'“”‘’<>"


def rail_kind(e: Mapping[str, Any]) -> str:
    """A row's coarse kind (§4.5 screen 6 *The rail*): *questions* (an `ask`, a `conflict`, a
    passed-up question), *steering* (a `steer`), *session states* (every state row, alarms
    included), *board items*, *notes* (a `note`, a `system` note, a reply, answered-for-you, an
    outcome), *trail*."""
    row = e.get("row")
    if row == "board":
        return "board"
    if row == "trail":
        return "trail"
    if row:
        return "states"
    if e.get("kind") in PERSON_ASK_KINDS or e.get("passed_up"):
        return "questions"
    if e.get("kind") == "steer":
        return "steering"
    return "notes"


def row_find(e: Mapping[str, Any], section: str = "") -> str:
    """The row's whole visible text, lowercased once, which the find matches every word against
    (§4.5 screen 6 *Find*): a mail row's sender, team, kind, `about`, PR and text; the rows the
    home builds carry their own (`find` on a state, alarm or board row); a trail row its name,
    kind, `how` and text. `data-find` on every row kind is this."""
    row = e.get("row")
    if row == "trail":
        return _find_text(e.get("name"), e.get("team"), e.get("kind"), "trail", e.get("how"), e.get("text"))
    if row:  # the home's own text for the row, and its team and pill, which the row draws too
        return _find_text(e.get("find"), e.get("team"), e.get("state_label"))
    answered = e.get("answered") if section == "answered" and isinstance(e.get("answered"), dict) else {}
    pr = e.get("pr")
    return _find_text(
        e.get("from_name") or e.get("from"),
        e.get("team"),
        e.get("kind"),
        e.get("about"),
        f"#{pr}" if isinstance(pr, int) and not isinstance(pr, bool) else "",
        e.get("text"),
        e.get("asker_name") if answered else "",
        answered.get("question") if answered else "",
        answered.get("source") if answered else "",
    )


templates.env.globals["rail_kind"] = rail_kind
templates.env.globals["row_find"] = row_find
templates.env.globals["rail_sections"] = RAIL_SECTIONS
templates.env.globals["rail_section_names"] = RAIL_SECTION_NAMES
templates.env.globals["rail_kinds"] = RAIL_KINDS
templates.env.globals["rail_kind_names"] = RAIL_KIND_NAMES


def find_words(find: str) -> list[str]:
    """The find's words (§4.5 screen 6 *Find*): lowercased, and a word's edge punctuation dropped,
    so *517,* finds what *517* does; a `#` is kept, and a bare number finds *#517* as a substring
    of it anyway."""
    return [w for w in (x.strip(_FIND_EDGE) for x in str(find or "").lower().split()) if w]


def find_matches(text: str, words: Collection[str]) -> bool:
    """Every word must match, in any order, as a substring of the row's text."""
    return all(w in text for w in words)


def rail_picks(query: Mapping[str, Any]) -> dict[str, Any]:
    """The picks a page's URL carries (§4.5 screen 6 *The rail*): `team`, `sec` and `kind` as comma
    lists — `none` is *no team* — and `find`. An unknown section or kind is dropped, never an error:
    a link survives a renamed line by showing a little more."""

    def lst(key: str) -> list[str]:
        return [x.strip() for x in str(query.get(key) or "").split(",") if x.strip()]

    return {
        "team": lst("team"),
        "sec": [x for x in lst("sec") if x in RAIL_SECTIONS],
        "kind": [x for x in lst("kind") if x in RAIL_KINDS],
        "find": str(query.get("find") or "").strip(),
    }


def rail_rows(sections: Mapping[str, Any]) -> list[dict[str, str]]:
    """Every row on the page that the rail counts, as the four things a pick reads: its section,
    team (`none` for no team), coarse kind and find text. The snoozed list is in no count."""
    return [
        {
            "section": sec,
            "team": str(e.get("team") or "") or RAIL_NO_TEAM,
            "kind": rail_kind(e),
            "find": row_find(e, sec),
        }
        for sec in RAIL_SECTIONS
        for e in sections.get(sec) or ()
    ]


def rail_counts(rows: Collection[Mapping[str, str]], picks: Mapping[str, Any]) -> dict[str, Any]:
    """The rail's counts and the section headings' (§4.5 screen 6 *The rail*): **every count is a
    count of rows on the page now**. Within a group the picks are OR'd, across groups AND'd, and a
    group with nothing picked means all of it; the find is a fourth group. For a line: a picked line
    reads its share of the page; an unpicked line in a group with a pick reads 0; an unpicked line
    in a group without one reads its share under the other groups' picks. `all` is the line's count
    with no filter at all. A **team** line counts that team's *Needs you* rows — *which team needs
    me* (TD-135's steer to the techlead, 2026-09-25). `app.js`'s `AO.railCounts` is this function
    again, over the rows in the DOM, and a test holds the two to one answer."""
    words = find_words(picks.get("find") or "")
    pick = {g: set(picks.get(g) or ()) for g in ("team", "sec", "kind")}
    key = {"team": "team", "sec": "section", "kind": "kind"}

    def passes(r: Mapping[str, str], skip: str = "") -> bool:
        for g, want in pick.items():
            if g != skip and want and r[key[g]] not in want:
                return False
        return find_matches(r["find"], words)

    teams = sorted({r["team"] for r in rows if r["team"] != RAIL_NO_TEAM})
    if any(r["team"] == RAIL_NO_TEAM for r in rows):
        teams.append(RAIL_NO_TEAM)
    # a picked team no row carries still gets its line, so the pick can be undone
    teams += [t for t in picks.get("team") or () if t not in teams]

    def line(group: str, value: str, among: Collection[Mapping[str, str]]) -> dict[str, int]:
        mine = [r for r in among if r[key[group]] == value]
        shown = 0 if pick[group] and value not in pick[group] else sum(1 for r in mine if passes(r, group))
        return {"shown": shown, "all": len(mine)}

    needs = [r for r in rows if r["section"] == "needs"]
    return {
        "filtered": bool(pick["team"] or pick["sec"] or pick["kind"] or words),
        "sections": {s: line("sec", s, rows) for s in RAIL_SECTIONS},
        "teams": {t: line("team", t, needs) for t in teams},
        "team_order": teams,
        "kinds": {k: line("kind", k, rows) for k in RAIL_KINDS},
        "heads": {
            s: {
                "shown": sum(1 for r in rows if r["section"] == s and passes(r)),
                "all": sum(1 for r in rows if r["section"] == s),
            }
            for s in RAIL_SECTIONS
        },
    }


# the page computes its rail from its sections when a caller did not (a test rendering the template
# directly, as the rows are shaped by `shaped` and `suggested_answers` whoever renders them)
templates.env.globals["rail_picks"] = rail_picks
templates.env.globals["rail_rows"] = rail_rows
templates.env.globals["rail_counts"] = rail_counts


# -- app -------------------------------------------------------------------------------------------


# design §4.5a **Focus (exited / closed)** → **Resume** (TD-081 step 2; the plumbing is step 1,
# PR #282). What a one-press resume carries, and what it deliberately does not.
RESUME_CARRIES = ("name", "dir", "adapter", "profile", "repo", "role", "team", "project", "lane", "controllers")
# The first prompt **Reopen and push** writes (§4.5a *Inbox row: state*, TD-081). Fixed text, in
# the source: it is the page's sentence, never anything a session said (§4.2), and what comes of it
# returns as an outcome (§4.10 *Outcomes*).
REOPEN_AND_PUSH = (
    "Push your branch and open or update its PR, then report the outcome with "
    '`ao msg person --outcome done|blocked|dropped "<one line>" --for <the question this answers>` '
    "— or, if nothing is owed, say so on docs/user_attention.md."
)


def resume_create(rec: dict[str, Any], *, prompt: str = "") -> dict[str, Any]:
    """The `create` a one-press **Resume** makes from an exited record (design §4.5a).

    It carries the record's own `name` — which is the whole point: the name check answers
    `supersede`, the new session takes the bare name *and* the record's id, the old record is
    replaced in place and its mail stays with it (§4.10 *a resume under the same name*, built by
    TD-081 step 1). And it carries `dir`, `adapter`, `profile`, `role`, `team`, `project`, `lane`
    and `controllers`, so a live team finds its member where it was.

    **And `repo`, when the record has one** (TD-145). A name identifies one session per *scope*
    (§4.1), and the scope of a session in a worktree is its repo, not the worktree directory: the
    record's id is `ao-<repo>-<name>`. A resume that sent `dir` alone put the worktree's basename
    in the scope — and a worktree named after its session (every team member's is) made
    `ao-<name>-<name>`, collapsed to `ao-<name>`, an id nobody held — so the name check answered
    *free*, a second record appeared beside the one being resumed, and the team showed two of the
    seat (2026-09-24: `ao-techlead-ao-1` beside `ao-agentorc-techlead-ao-1`, and the seat's
    trigger dead with it, since a superseded record is never filled).

    **Never `unattended`.** The session a press starts is *attended*, whatever the record was: an
    unattended session answers its own permission prompts, and a press with no form is no place to
    grant that — least of all to a member whose manager has gone and whose `run_until` has passed.
    Also not carried: `run_until` and `wrapup_prompt` (a deadline that has passed is not one),
    the old `prompt`, and `capabilities` — the grants come from the record's **role**, through the
    same preset path the form takes, so nothing is copied and nothing is dropped."""
    lane = rec.get("lane")
    # *a `create` **on the record's host***: a record of a node's is addressed `id@host` and its
    # `host` is its own, so a resume without it would create the session on the **home** instead —
    # under the name that should have superseded the node's record (review of PR #290). Sent only
    # when it lands elsewhere, which is `Member.create_params`'s convention and §4.4's rule that a
    # client never sends a parameter it has not set.
    host = str(rec.get("host") or "")
    got: dict[str, Any] = {
        "name": str(rec.get("name") or ""),
        "dir": str(rec.get("dir") or ""),
        "adapter": str(rec.get("adapter") or "claude-code"),
        "profile": str(rec.get("profile") or ""),
        "role": str(rec.get("role") or ""),
        "team": str(rec.get("team") or ""),
        "project": str(rec.get("project") or ""),
        "lane": [str(x) for x in lane] if isinstance(lane, list) else [],
        "controllers": [str(c) for c in (rec.get("controllers") or [])],
        "resume": str(rec.get("adapter_id") or ""),
        "unattended": False,
        # the scope the name is checked in (§4.1): sent only when the record has one (§4.4 skew rule)
        **({"repo": str(rec["repo"])} if rec.get("repo") else {}),
        **({"host": host} if host and host != host_name() else {}),
    }
    if prompt:
        got["prompt"] = prompt
    # The **grants** and the **ledger** come from the directory's repo through the record's role,
    # exactly as they do when the form's Role picker is used — not copied off the old record, so a
    # grant a preset has since dropped is dropped here too, and one it has gained is gained. A
    # directory or a role that no longer resolves is not decided here: the create refuses it and
    # the press lands on the form with the agent's own words (`resume_blocked`'s docstring).
    with contextlib.suppress(KeyError, ValueError, OSError):
        cfg = repoconfig.discover(got["dir"] or os.getcwd())
        got["ledger"] = cfg.ledger
        if got["role"]:
            preset = repoconfig.resolve_role(cfg, got["role"])
            got["capabilities"] = [g for g in preset.grants if g in GRANTS]
            got["lane"] = got["lane"] or list(preset.lane)
            got["profile"] = got["profile"] or preset.profile or ""
    return got


def resume_blocked(rec: dict[str, Any]) -> str:
    """Why a one-press **Resume** cannot be silent on this record, or "" — *when it cannot be
    silent it is not a guess* (§4.5a): the press lands on the filled-in form with the reason on it.

    Only what is knowable from the record is decided here. Everything else — a directory that is
    gone, a profile or role that no longer exists, a host that is not connected, a name a live
    record has taken since — is the agent's own refusal, which the caller turns into the same
    fall-through in the agent's own words, so no rule of ours can drift from the one that runs."""
    if str(rec.get("state") or "") not in ("exited", "closed"):
        return "this session is still running — there is nothing to resume"
    if not str(rec.get("adapter_id") or ""):
        return "this record holds no tool session id, so there is no conversation to resume"
    if not str(rec.get("name") or "") or not str(rec.get("dir") or ""):
        return "this record has no name or no directory to take back"
    return ""


def resume_form_url(rec: dict[str, Any], why: str = "") -> str:
    """**Resume with changes…**: New session with every field of `resume_create` filled in, which
    is also where a press that cannot be silent lands, with its reason (§4.5a)."""
    got = resume_create(rec)
    # `capabilities` and `ledger` are the create's, not the form's — §4.5a says capabilities are
    # **not carried**, and the form derives both from the Role picker. `host` likewise: the form
    # has no host field, and phase 1 starts a session on the host the page is served from.
    q = {k: v for k, v in got.items() if k in RESUME_CARRIES and k not in ("lane", "controllers") and v}
    q["resume"] = got["resume"]
    # A worktree record lands on the form as the form says it: **Where** = new worktree, the
    # worktree's name, and the directory field holding the *repo* — which is what the form's own
    # Start sends (`repo=dir` with `worktree`), so the create checks the name in the record's scope
    # and takes its id back, exactly as the one-press Resume does (TD-145). Keyed on the record's
    # own `worktree` — the field the create sets when it made or reused `<repo>/.claude/worktrees/
    # <name>` — never on `dir != repo`: `ao new --repo` scopes a directory that is a worktree of the
    # repo's without going through that flow, and such a record resumes where it is (review of #543).
    repo = q.pop("repo", "")
    if repo and rec.get("worktree"):
        q["worktree"] = str(rec["worktree"])
        q["where"] = "worktree"
        q["dir"] = repo
    q["lane"] = ", ".join(got["lane"])
    q["controllers"] = ",".join(got["controllers"])
    # *Unattended* as the record had it — on the form, where it is next to its stop time
    q["unattended"] = "on" if rec.get("unattended") else ""
    # **The form is filled in from a record, not opened blank** — which is what lets an *empty*
    # controllers list mean *this record had none* rather than *nothing was said*. Without it the
    # repo's default would be re-ticked and a person who did not notice would Start a session with
    # controllers the record never had (review of PR #290).
    q["prefilled"] = "1"
    if why:
        q["why"] = why
    return "/new?" + urlencode({k: v for k, v in q.items() if v})


def _int_param(raw: str | None, default: int, lo: int, hi: int) -> int:
    try:
        v = int(float(raw)) if raw not in (None, "") else default
    except (TypeError, ValueError, OverflowError):  # "abc", "NaN", "inf" / "1e999"
        return default
    return max(lo, min(hi, v))


class _WsAttemptLog:
    """Log every websocket upgrade the instant it arrives, before any handler runs. uvicorn logs a
    websocket only once the app accepts or rejects it, so "no line at all" could not distinguish
    "never reached the server" from "hung before accept" (first-use finding 2026-09-06)."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") == "websocket":
            client = scope.get("client") or ("?", "?")
            log.info("websocket attempt from %s:%s for %s", client[0], client[1], scope.get("path"))
        await self.app(scope, receive, send)


def create_app() -> FastAPI:
    app = FastAPI(title="agentorc")
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.add_middleware(_WsAttemptLog)

    async def call(method: str, **params: Any) -> Any:
        try:
            async with LocalClient() as c:
                return await c.call(method, **params)
        except AgentUnavailable as e:
            raise HTTPException(503, f"host agent unreachable: {e}") from e
        except AgentError as e:
            raise HTTPException(400, str(e)) from e

    def render_card(v: dict[str, Any]) -> str:
        return templates.get_template("card.html").render(s=v)

    defs_cache: dict[str, Any] = {"at": 0.0, "org": None}

    async def defs() -> orgmod.Org:
        """The team definitions, read at most once in `DEFS_TTL` seconds: the events stream
        re-renders every header on every delta — a definition edited by hand shows within that."""
        now = time.monotonic()
        if defs_cache["org"] is None or now - defs_cache["at"] > DEFS_TTL:
            # off the loop: every open page shares it, and the read is a file per registered repo
            defs_cache.update(at=now, org=(await asyncio.to_thread(org_here))[0])
        return defs_cache["org"]

    async def team_rows(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The definitions' rows for a delta's headers."""
        return _aged(teamrun.rows(await defs(), sessions))

    async def seats_of(fleet: list[dict[str, Any]]) -> dict[str, str]:
        """The ids in `fleet` a team definition names as a seat — what `view()` draws *on call*
        while nobody is in one (TD-097). Every page that draws a pill asks, so they agree."""
        return teamrun.seat_ids(await defs(), fleet)

    async def group_heads(known: dict[str, dict[str, Any]]) -> list[dict[str, Any]] | None:
        """The team groups as the events stream ships them (design §4.5a **team groups**): per group
        its key, the member ids in order, and the header rendered by the same template the page
        uses — so the client moves cards between groups and swaps headers without composing any
        markup of its own. `None` means "flat grid", exactly as the page renders it."""
        fleet = list(known.values())
        seats = await seats_of(fleet)
        groups = team_groups([view(s, fleet, seats=seats) for s in fleet], await team_rows(fleet))
        if groups is None:
            return None
        head = templates.get_template("group_head.html")
        return [
            {
                "team": g["team"],
                "manager": (g["manager"] or {}).get("id", ""),
                "live": 0 if g["stopped"] else g["live"],  # what the fold keys on: a concluded team folds
                "ids": g["ids"],
                "html": head.render(g=g),
            }
            for g in groups
        ]

    identity_cache: dict[str, Any] = {"at": 0.0, "info": None}

    async def identity_info() -> dict[str, Any]:
        """This host's identity mode (design §4.8a), for the Org's one-line note. The `identity`
        RPC is a never-gated read, and it is read at most once every `DEFS_TTL` seconds — the same
        idiom as the team definitions, so a page under a delta storm never asks per render. An
        agent that is down or too old to answer leaves the note off rather than the page."""
        now = time.monotonic()
        if identity_cache["info"] is None or now - identity_cache["at"] > DEFS_TTL:
            try:
                identity_cache.update(at=now, info=await call("identity"))
            except HTTPException:
                identity_cache.update(at=now, info={})
        return identity_cache["info"] or {}

    board_cache: dict[str, Any] = {"at": None, "rows": [], "note": ""}

    async def board_items(fresh: bool = False) -> tuple[list[dict[str, Any]], str]:
        """The Inbox's board rows and the note beside them (TD-069 step 3), read at most once every
        `BOARD_TTL` seconds and off the event loop: the reader is a subprocess over every board on
        the host, and the page and the top bar both poll every few seconds. `fresh` reads now — after
        a Snooze or Done, so the row the person answered is gone from the next refresh."""
        now = time.monotonic()
        if fresh or board_cache["at"] is None or now - board_cache["at"] > BOARD_TTL:
            rows, note = await asyncio.to_thread(read_boards)
            board_cache.update(at=now, rows=rows, note=note)
        return board_cache["rows"], board_cache["note"]

    async def person_states(fleet: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The session-state rows of the Inbox (design §4.5 screen 6, TD-069 step 2), from the
        fleet the request already read. Every host the home knows, exactly as the Org shows them —
        the same records and the same `view()` — plus this host's own identity alarms (§4.8a).

        Rendered server-side on the page's load *and* on its poll: a state that changed between
        polls is corrected by the next one, and nothing here has to ride the pushed stream to be
        no more than a few seconds behind the Org."""
        icons = await role_icons(fleet)
        seats = await seats_of(fleet)
        views = [view(s, fleet, icons=icons, seats=seats) for s in fleet]
        info = await identity_info()
        return state_rows(
            views,
            host_alarms=alarm_view(info.get("alarms")),
            host=str(info.get("host") or host_name()),
            identity_mode=str(info.get("mode") or ""),
        )

    async def person_view() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """The mail and the state rows of one Inbox request — over **one** `list`. Both halves need
        the fleet (the mail for its senders' names, the states for the records themselves), and
        this runs on every page load and every poll: two fleet lists on that path would be two for
        no reason (review of PR #251)."""
        fleet = await call("list")
        return await person_inbox(fleet), await person_states(fleet)

    async def person_inbox(fleet: list[dict[str, Any]]) -> dict[str, Any]:
        """The person inbox as every surface here reads it (§4.10, §4.5a): the `inbox` RPC with no
        caller and no id — **a person's read, which sets no `read_at`**, because a person is not
        the session. That is what lets the Inbox page poll it every few seconds without marking
        anything read and without freeing a depth slot an unanswered question still holds; it is
        the rule the dialog this page replaces already relied on, so no `peek` was needed. Each
        sender gets the name it is known by, and `from_open` the id **Open** goes to while that
        record still exists (§4.5 screen 6: a row opens the session that needs the person)."""
        got = await call("inbox")
        names = {o.get("id"): o.get("name") or o.get("id") for o in fleet}
        records = {o.get("id"): o for o in fleet}
        # §4.5a *Inbox row: FYI* → **Put on the board** (TD-140): the form's board is the sender's
        # repo's, when this host knows that repo and it carries one; else the person picks
        boards_of = {c["root"]: c["board"] for c in board_choices()}

        def board_of(sid: Any) -> str:
            repo = str((records.get(sid) or {}).get("repo") or "")
            return boards_of.get(str(Path(repo).expanduser().resolve()), "") if repo else ""

        for t in got.get("trail") or ():
            if isinstance(t, dict):
                t["board_default"] = board_of(t.get("sid"))
        at = datetime.now(UTC)
        for e in got["entries"]:
            e["from_name"] = "person" if e["from"] == "person" else names.get(e["from"], e["from"])
            e["from_open"] = e["from"] if e["from"] in names else ""
            e["board_default"] = board_of(e["from"])
            if isinstance(e.get("pr"), int):  # §4.9b *The reader*: a held PR asked of the person (TD-093)
                sender = records.get(e["from"]) or {}
                e["pr_url"] = reviewmod.pr_url(sender.get("repo") or sender.get("dir"), e["pr"])
            _owing(e, records.get(e["from"]), at)
            e["age"] = _age(e.get("at"), at)  # the client keeps it ticking; this is what it opens on
            # §4.5 screen 6 *Layout* (TD-082): a duration is words from here, never a `…` the
            # client fills in — `left` for a `steer`'s bound, `until_words` for a snoozed entry,
            # whose row upgrades to the browser's own clock once the script runs.
            e["left"] = _left(e.get("bound"), at)
            e["until_words"] = _left(e.get("snoozed_until"), at)
            # §4.9b (TD-075 step 3): a question **passed up** is the asker's own entry, and the
            # passer — whose recommendation and suggested answers it carries — is named as a sender is
            rec = e.get("recommend")
            if e.get("passed_up") and isinstance(rec, dict) and rec.get("by"):
                e["passer_name"] = names.get(str(rec["by"]), str(rec["by"]))
            # §4.9b: an *answered for you* row names **who asked** — the session Overrule writes
            # to — by the name it is known by, and opens it while it is here
            asker = str(_answered_of(e).get("asker") or "")
            if asker:
                e["asker_name"] = names.get(asker, asker)
                e["asker_open"] = asker if asker in names else ""
        return got

    def inbox_html(sections: dict[str, Any], origin: str = "") -> dict[str, str]:
        """Each section's rows, rendered by the one template the page itself renders them with, so
        a poll replaces a section without the client composing any markup — the shape the events
        stream already uses for team headers. Jinja escapes every field, and a mail row's text goes
        through the closed-subset renderer (`shaped`), which is what keeps what a session wrote
        text and nothing else (TD-071 item 8, TD-138)."""
        rows = templates.get_template("inbox_rows.html")
        return {k: rows.render(rows=sections[k], section=k, origin=origin) for k in INBOX_SECTIONS}

    h = SimpleNamespace(
        call=call,
        render_card=render_card,
        defs=defs,
        team_rows=team_rows,
        seats_of=seats_of,
        group_heads=group_heads,
        identity_info=identity_info,
        person_states=person_states,
        person_view=person_view,
        person_inbox=person_inbox,
        board_items=board_items,
        inbox_html=inbox_html,
    )
    for register in (_pages_routes, _new_routes, _sessions_routes, _teams_routes, _inbox_routes, _stream_routes):
        register(app, h)
    return app


def _pages_routes(app: FastAPI, h: SimpleNamespace) -> None:
    """The Org page and Focus (design §4.5)."""
    call, seats_of, identity_info, board_items = h.call, h.seats_of, h.identity_info, h.board_items

    @app.get("/", response_class=HTMLResponse)
    async def org(request: Request):
        # An unreachable agent still gets a page: the banner + Retry are the recovery path
        # (design §4.5 unreachable hosts), never a bare 503.
        agent_down = False
        usage: dict[str, Any] = {}
        person_needs = person_fyi = 0
        info: dict[str, Any] | None = None
        entries: list[dict[str, Any]] = []
        try:
            sessions = await call("list")
            usage = await call("usage")
            with contextlib.suppress(Exception):  # an agent before TD-100 slice 1 has no `gate`: no lines
                usage = with_lines(usage, await call("gate"))
            usage = usage_accounts(usage, sessions)  # one chip per account (§4.5a, TD-122)
            info = await call("host")
            try:
                # the top bar's number: the Inbox page's **Needs you** section, and not unread mail
                # (§4.5a **Inbox page**, TD-069 steps 1–2) — mail *and* the session states, from the
                # same `inbox_sections` the page uses, so the two numbers cannot drift apart
                entries = (await call("inbox"))["entries"]
            except HTTPException as e:
                # a node whose link is down refuses the mailbox (§4.4a): the banner says why, and
                # the page is still this host's sessions
                if e.status_code == 503 or (info or {}).get("mode") != "node":
                    raise
        except HTTPException as e:
            if e.status_code != 503:
                raise
            sessions, agent_down = [], True
        icons = await role_icons(sessions)
        seats = await seats_of(sessions)
        vs = sorted((view(s, sessions, icons=icons, seats=seats) for s in sessions), key=card_order)
        # the needs-you badge is the same predicate the Inbox rows are (review of PR #251): a
        # record the Org counts and the Inbox did not list was the two pages disagreeing in public
        counts = {"needs-you": sum(1 for v in vs if state_kind(v) in NEEDS_YOU_ROWS)}
        counts.update({k: sum(1 for v in vs if v["state"] == k) for k in ("limited", "stalled?")})
        strip = teams_view(sessions)
        id_info = {} if agent_down else await identity_info()
        boards = [] if agent_down else (await board_items())[0]
        if entries or vs or boards:
            secs = inbox_sections(
                entries,
                states=state_rows(
                    vs,
                    host_alarms=alarm_view(id_info.get("alarms")),
                    host=str(id_info.get("host") or host_name()),
                    identity_mode=str(id_info.get("mode") or ""),
                ),
                boards=boards,
            )
            person_needs, person_fyi = secs["count"], secs["fyi_n"]
        return templates.TemplateResponse(
            request,
            "org.html",
            {
                "sessions": vs,
                "groups": team_groups(vs, strip["teams"]),
                "strip": strip,
                "counts": counts,
                "host": host_name(),
                "active": "Org",
                "agent_down": agent_down,
                "volatile": hosts.local_host().volatile,
                "usage": usage,
                "person_needs": person_needs,
                "person_fyi": person_fyi,
                "node_banner": node_banner(info),
                "identity_note": identity_note(id_info),
                "editor_note": uiconf.open_in().error,
            },
        )

    @app.get("/focus/{sid}", response_class=HTMLResponse)
    async def focus(request: Request, sid: str, window: str = ""):
        try:
            s = await call("seen", id=sid)  # opening Focus is the "seen" (TD-017); returns the record
        except HTTPException as e:
            if e.status_code == 503:
                return RedirectResponse("/", status_code=303)  # the Org shows the down banner
            raise
        known = True
        try:
            fleet = await call("list")
        except HTTPException:  # the record we already have still renders; membership just empties
            fleet, known = [s], False  # …and Ready to close says it does not know, rather than pass
        return templates.TemplateResponse(
            request,
            "focus.html",
            {
                "s": view(s, fleet, fleet_known=known, icons=await role_icons([s]), seats=await seats_of([s])),
                "host": host_name(),
                "active": "Org",
                # design §4.5a **Pop out** (TD-046): the same Focus, without the nav and the top bar
                "popped": window == "1",
            },
        )


def _new_routes(app: FastAPI, h: SimpleNamespace) -> None:
    """New session: the form, its checks, and a shell (design §4.5a *New session*)."""
    call = h.call

    @app.get("/new", response_class=HTMLResponse)
    async def new_form(
        request: Request,
        dir: str = "",
        adapter: str = "claude-code",
        resume: str = "",
        project: str = "",
        # design §4.5a **Resume with changes…** (TD-081 step 2): the same fields a one-press
        # Resume carries, filled into the form instead of used. `why` is set when a press that
        # should have been silent landed here, and the form says so rather than leaving a person
        # to wonder which of the two controls they pressed.
        name: str = "",
        profile: str = "",
        role: str = "",
        team: str = "",
        lane: str = "",
        controllers: str = "",
        unattended: str = "",
        where: str = "here",  # a worktree record's Resume with changes… (TD-145)
        worktree: str = "",
        prefilled: str = "",
        why: str = "",
    ):
        profs, default = profiles_mod.load()
        # registered repos (design §5: the dev-cadence registry, `repos_registry` in hosts.yml) first,
        # then recent directories; phase 1 reads the local host's file directly
        repos = hosts.local_host().repos()
        recent = repos + [d for d in await call("recent_dirs") if d not in repos]
        adapters = await call("adapters")
        # design §4.5a New session **Controllers** picker (§4.8): the candidates are the sessions
        # holding `control` — nothing else could act on the new session anyway.
        control_holders = [
            {"id": o["id"], "name": o.get("name") or o["id"]}
            for o in await call("list")
            if has_control(o.get("capabilities")) and o.get("state") not in ("closed", "exited")
        ]
        # design §4.5a New session **Role** preset: the built-ins, plus what the prefilled directory's
        # repo redefines; `/api/roles` refreshes the list as the directory is typed (TD-040 step a).
        roles = _roles_for(dir)
        return templates.TemplateResponse(
            request,
            "new.html",
            {
                "host": host_name(),
                "active": "Org",
                "profiles": profs,
                "default_profile": default,
                "recent": recent,
                "adapters": adapters,
                "control_holders": control_holders,
                # design §4.5a New session **Grants** checkboxes: the grants a record may hold
                # (`sessionorc.models.GRANTS`, the same list `ao new --grant` offers), each with
                # the one-line warning the row asks for.
                "grants_all": list(GRANTS),
                # Jinja renders a missing key as empty, so a grant added to GRANTS without a note
                # would ship a silent, unexplained checkbox. Say so on the page instead (review of
                # PR #141); the test still fails first for anyone who runs them.
                "grant_notes": {g: GRANT_NOTES.get(g) or UNDESCRIBED_GRANT for g in GRANTS},
                "roles": roles["roles"],
                "default_controllers": roles.get("controllers") or [],
                # design §4.5a New session **Project** picker (§4.9): the projects defined on this
                # host, each carrying its repos' checkouts so the form can narrow the directory
                # list without another round trip. "No project" is the default and is what every
                # session was before.
                "projects": projects_view(),
                "prefill": {
                    "dir": dir,
                    "adapter": adapter,
                    "resume": resume,
                    "project": project,
                    "name": name,
                    "profile": profile,
                    "role": role,
                    "team": team,
                    "lane": lane,
                    "controllers": [c for c in controllers.split(",") if c.strip()],
                    "unattended": unattended == "on",
                    "where": "worktree" if where == "worktree" else "here",
                    "worktree": worktree,
                    "prefilled": prefilled == "1",
                    "why": why,
                },
            },
        )

    def _roles_for(dir: str) -> dict[str, Any]:
        """The presets that resolve in `dir`'s repo, and the repo's `controllers:` default, for the
        form's pick-list and the Controllers picker's prefill (design §4.5a, §4.8 "Defaults fill
        membership at launch"). A malformed `.agentorc.yml` is reported, not raised: the form still
        renders with the built-ins, and Start says what is wrong."""
        try:
            cfg = repoconfig.discover(dir or os.getcwd())
            found = [r.to_dict() for r in repoconfig.roles(cfg)]
        except ValueError as e:
            return {"roles": [r.to_dict() for r in repoconfig.roles(repoconfig.RepoConfig())], "error": str(e)}
        return {"roles": found, "controllers": cfg.controllers, "file": str(cfg.path) if cfg.path else None}

    @app.get("/api/roles")
    async def api_roles(dir: str = ""):
        return _roles_for(dir)

    @app.post("/new")
    async def new_submit(
        name: str = Form(...),
        dir: str = Form(...),
        adapter: str = Form("claude-code"),
        profile: str = Form(""),
        prompt: str = Form(""),
        resume: str = Form(""),
        unattended: str = Form(""),
        where: str = Form("here"),
        worktree: str = Form(""),
        role: str = Form(""),
        lane: str = Form(""),
        project: str = Form(""),
        until: str = Form(""),
        controller: Annotated[list[str], Form()] = NO_CONTROLLERS,
        grant: Annotated[list[str], Form()] = NO_GRANTS,
    ):
        wt = None
        if where == "worktree":
            wt = worktree.strip() or naming.slug(name.strip() or "session")
        # The role preset (design §4.5a, §4.8) fills what the form left empty: the brief from its
        # template, the lane, its grants, and its profile unless one was picked. The Controllers
        # picker was prefilled from the repo's or preset's `controllers:` when the page loaded, so
        # what is ticked is what was meant — a person unticking the default is a decision.
        refs = [r.strip() for r in lane.split(",") if r.strip()]
        preset = brief = ledger = None
        if adapter != "shell":
            try:
                cfg = repoconfig.discover(dir.strip() or os.getcwd())
                ledger = cfg.ledger
                if role.strip():
                    preset = repoconfig.resolve_role(cfg, role.strip())
                    brief = preset.brief_text(refs or None)
            except (KeyError, ValueError) as e:
                raise HTTPException(400, str(e).strip('"')) from None
        # design §4.5a New session **Project** picker (§4.9 "Home and reach"): the same block
        # `ao new --project` puts in front of the brief, from the same function — each of the
        # project's repos on this host and which one is home. A one-repo project adds nothing, and
        # a name with no definition still badges the session: nothing keys on the badge.
        text = prompt.strip() or brief
        if project.strip():
            block, _note = teams.reach_block(orgmod.load(), project.strip(), dir.strip() or os.getcwd(), host_name())
            if block:
                text = block + text if text else block
        s = await call(
            "create",
            name=name.strip() or "session",
            dir=dir.strip(),
            adapter=adapter,
            profile=profile or (preset.profile if preset else None) or "",
            prompt=text,
            resume=resume.strip() or None,
            unattended=unattended == "on",
            **teams.gate_prompts(unattended == "on"),  # the usage gate's two texts (§6, TD-100)
            worktree=wt,
            repo=dir.strip() if wt else None,
            # The Grants checkboxes were ticked from the preset when the page loaded and as the
            # Role changed (design §4.5a), so what is ticked is what was meant — including an
            # untick, which is a person deciding this session does not get the grant.
            capabilities=[g for g in dict.fromkeys(grant) if g in GRANTS],
            lane=refs or (list(preset.lane) if preset else []),
            role=preset.name if preset else "",
            review=preset.review if preset else None,  # who reads its PRs (design §4.9b *The reader*)
            ledger=ledger,
            controllers=[c for c in controller if c.strip()],
            project=project.strip(),  # a badge, exactly as `ao new --project` sets it (§9 invariant 9)
            **stop_fields(until, unattended == "on"),
        )
        return RedirectResponse(f"/focus/{s['id']}", status_code=303)

    @app.post("/shell")
    async def shell(dir: str = Form(...), name: str = Form("")):  # unnamed: the agent names it (TD-030)
        s = await call("create", name=name, dir=dir, adapter="shell")
        return RedirectResponse(f"/focus/{s['id']}", status_code=303)

    @app.get("/api/occupancy")
    async def api_occupancy(dir: str = ""):
        if not dir.strip():
            return {"dir": "", "occupants": [], "git": False}
        return await call("occupancy", dir=dir.strip())

    @app.get("/api/name_check")
    async def api_name_check(dir: str = "", name: str = "", worktree: bool = False):
        """What §4.1's name rule would do (design §4.5a, TD-030): the New session form asks as you
        type, the way it already asks about directory occupancy. `worktree` puts the name in the
        repo's scope, which is where the session would actually land."""
        if not (dir.strip() and name.strip()):
            return {"id": "", "name": name, "verdict": "free", "holder": None, "message": ""}
        return await call("name_check", dir=dir.strip(), name=name.strip(), repo=dir.strip() if worktree else None)


def _sessions_routes(app: FastAPI, h: SimpleNamespace) -> None:
    """A session's own controls and reads: resume, the card's actions, its inbox, the list."""
    call, seats_of = h.call, h.seats_of

    # -- actions (every control in design §4.5a that exists in phase 1) --------------------------

    @app.post("/api/sessions/{sid}/resume")
    async def resume_session(sid: str, request: Request):
        """design §4.5a **Focus (exited / closed)** → **Resume**, and **Inbox row: state** →
        **Reopen and push** (TD-081 step 2): *a resume option that requires no input from me*.

        One press, no form: a `create` carrying the record's own name, so the name check answers
        `supersede` and the resumed session takes the bare name and the record's id in place, with
        its mail (§4.10, built by step 1). It is a person's act from the page — **caller-less**, no
        attenuation (§4.8 create rule) — and the session it starts is **attended**, whatever the
        record was.

        **When it cannot be silent it is not a guess.** What the record itself settles is answered
        here; everything else is the agent's own refusal, and either way the answer is the same:
        the filled-in form, with the reason on it, for the person to finish by hand. The client
        follows `form`; nothing is created behind their back and nothing is guessed at."""
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        rec = await call("get", id=sid)
        # **Resume with changes…** asks for the form outright and creates nothing (§4.5a).
        if body.get("form"):
            return JSONResponse({"ok": True, "form": resume_form_url(rec)})
        if why := resume_blocked(rec):
            return JSONResponse({"ok": False, "form": resume_form_url(rec, why), "why": why})
        # *Reopen and push* is the same press with a first prompt **the page wrote** — fixed text
        # in the source, never anything a session said (§4.2, TD-071 item 8).
        prompt = REOPEN_AND_PUSH if body.get("push") else ""
        try:
            new = await call("create", **resume_create(rec, prompt=prompt))
        except HTTPException as e:
            why = str(e.detail).strip('"') or "the host agent refused the resume"
            return JSONResponse({"ok": False, "form": resume_form_url(rec, why), "why": why})
        return JSONResponse({"ok": True, "id": new["id"]})

    @app.post("/api/sessions/{sid}/{action}")
    async def action(sid: str, action: str, request: Request):
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        if action in ("allow", "deny"):
            s = await call("get", id=sid)
            pend = s.get("pending") or {}
            if pend.get("kind") != "permission" or not pend.get("tool_use_id"):
                raise HTTPException(409, "no pending permission (answered, timed out, or in the terminal)")
            await call("decide", id=sid, tool_use_id=pend["tool_use_id"], behavior=action, reason=body.get("reason"))
        elif action == "kill":
            await call("kill", id=sid)
        elif action == "close":
            await call("close", id=sid)
        elif action == "send":
            await call("send", id=sid, text=body.get("text", ""))
        elif action == "wrapup":
            await call("send", id=sid, text=WRAPUP_PROMPT, wrapup=True)
        elif action == "mode":
            on = bool(body.get("unattended"))
            s = await call("set_mode", id=sid, unattended=on, **teams.gate_prompts(on))  # §6's two texts
            due = _instant(s.get("run_until"))
            if s.get("unattended") and due is not None and due <= datetime.now(UTC):
                # **Hand back** (design §4.5a, TD-096): a stop time that fell due while the person held
                # the session is not a deadline any more — the resume's rule — and nothing acted on it
                # while it was interactive, so handing back must not kill it on the next tick. One
                # still ahead stays.
                await call("set_stop", id=sid, run_until=None)
        elif action == "keys":
            await call("keys", id=sid, keys=list(body.get("keys") or []))
        elif action == "shell-here":
            s = await call("get", id=sid)
            new = await call("create", name=f"{s['name']}-shell", dir=s["dir"], adapter="shell")
            with contextlib.suppress(HTTPException):
                await call("seen", id=sid)  # acted on this card too (TD-017)
            return JSONResponse({"ok": True, "id": new["id"]})
        elif action == "drop":
            # design §4.5a Focus **Reports** → Drop: the person let a claim go, and the record says
            # so rather than losing it — a declaration, so the tick's derived entry cannot undo it.
            ref = str(body.get("ref") or "").strip()
            if not ref:
                raise HTTPException(400, "drop needs the reference to drop")
            await call("progress", id=sid, ref=ref, status="dropped", why=body.get("why") or "dropped from Focus")
        elif action == "stop":
            # design §4.5a Focus header **stops** badge → click to edit (§6, TD-026). The stop time
            # was settable at New session and from `ao until` and nowhere else, so a person who set
            # `+8h` and wanted another hour had to reach for the CLI. An empty time clears it, which
            # is the decision to let a session run on, made out loud rather than by restarting it.
            when = str(body.get("until") or "").strip()
            try:
                run_until = clistop(when) if when else None
            except AgentError as e:
                raise HTTPException(400, str(e)) from None
            s = await call("set_stop", id=sid, run_until=run_until, wrapup_prompt=WRAPUP_PROMPT if run_until else None)
            with contextlib.suppress(HTTPException):
                await call("seen", id=sid)
            return JSONResponse({"ok": True, "stop_note": stop_note(s), "run_until": s.get("run_until")})
        elif action == "grants":
            s = await call("set_grants", id=sid, add=_str_list(body, "add"), remove=_str_list(body, "remove"))
            with contextlib.suppress(HTTPException):
                await call("seen", id=sid)
            return JSONResponse({"ok": True, "capabilities": s.get("capabilities") or []})
        elif action == "controllers":
            # design §4.5a Focus **controllers** chip (§4.8, TD-036). The UI calls with no `caller`,
            # so it acts as the person it is: the agent's own rules still decide what is allowed.
            # A typed *name* is resolved to an id first — the agent stores whatever it is given and
            # a name would sit in the list forever as a controller that can never act (review).
            fleet = await call("list")
            s = await call(
                "set_controllers",
                id=sid,
                add=[_as_session_id(c, fleet) for c in _str_list(body, "add")],
                remove=[_as_session_id(c, fleet, must_exist=False) for c in _str_list(body, "remove")],
            )
            with contextlib.suppress(HTTPException):
                await call("seen", id=sid)
            return JSONResponse({"ok": True, "controllers": s.get("controllers") or []})
        elif action in ("message", "reply"):
            # design §4.5a **Message** (card `more ▾`, Focus header) and Focus Inbox **Reply**
            # (§4.10): mail, not a send — nothing is typed into any pane. The UI calls with no
            # `caller`, so `from` is the person and the message gate lets it through. A reply names
            # the entry it answers and no addressee: the agent addresses it to that entry's sender.
            text = str(body.get("text") or "")
            if action == "reply":
                ref = str(body.get("reply_to") or "").strip()
                if not ref:
                    raise HTTPException(400, "a reply names the entry it answers")
                got = await call("msg", text=text, kind="reply", reply_to=ref)
            else:
                kind = str(body.get("kind") or "note")
                if kind not in ("note", "ask"):
                    raise HTTPException(400, "a person's Message is a note or an ask")
                about = str(body.get("about") or "").strip() or None
                got = await call("msg", to=[sid], text=text, kind=kind, about=about)
            with contextlib.suppress(HTTPException):
                await call("seen", id=sid)
            return JSONResponse({"ok": True, "id": got["entry"]["id"], "delivered": got["delivered"]})
        elif action == "unmail":
            # design §4.5a Focus **Inbox** → delete (§4.10 lifecycle: a person's hand): this
            # session's copy only; the sender keeps its own.
            ref = str(body.get("msg") or "").strip()
            if not ref:
                raise HTTPException(400, "delete needs the entry's id")
            await call("inbox_delete", id=sid, msg=ref)
        elif action == "remove":
            await call("remove", id=sid)
        elif action == "seen":
            pass  # the mark below is the whole action (the Focus page sends it when its session goes idle)
        else:
            raise HTTPException(404, f"no action {action}")
        if action != "remove":
            with contextlib.suppress(HTTPException):
                await call("seen", id=sid)  # acting on a card counts as looking at it (TD-017)
        return JSONResponse({"ok": True})

    @app.get("/api/sessions/{sid}/inbox")
    async def api_inbox(sid: str, request: Request):
        """design §4.5a Focus side panel **Inbox** (§4.10 "A bounded body"): bodies never ride the
        pushed record, so the panel fetches them here. No caller — a person's read, which sets no
        `read_at`, because a person is not the session. Each sender gets its name beside its id,
        and its text the two halves the Inbox row draws (`shaped`, TD-138): the panel folds the
        same way, from the same renderer, and composes no markup of its own from a session's text."""
        got = await call("inbox", id=sid)
        fleet = await call("list")
        names = {o.get("id"): o.get("name") or o.get("id") for o in fleet}
        origin = page_origin(request)
        for e in got["entries"]:
            e["from_name"] = "person" if e["from"] == "person" else names.get(e["from"], e["from"])
            s = shaped(e.get("text"), origin)
            e["lead_html"], e["rest_html"] = str(s["lead"]), str(s["rest"])
        return got

    @app.get("/api/sessions")
    async def api_sessions():
        sessions = await call("list")
        icons = await role_icons(sessions)
        seats = await seats_of(sessions)
        return [view(s, sessions, icons=icons, seats=seats) for s in sessions]


def _teams_routes(app: FastAPI, h: SimpleNamespace) -> None:
    """Teams: the list, Start, and Stop / Wind down (design §4.9, §4.9a)."""
    call = h.call

    # -- the Teams strip (design §4.5a Org **Teams** strip, §4.9) --------------------------------
    # Start and Stop are `agentorc.teamrun`'s, the sequence `ao team start|stop` runs: every
    # pre-flight check before any create, and the wrap-up order on the way down. The runner blocks
    # (it waits on states), so it goes to a worker thread and the event loop stays free.

    background: set[asyncio.Task[Any]] = set()  # strong refs: a bare create_task may be collected
    stopping_leads: set[str] = set()  # teams whose lead-stop is already in flight: one per team
    stop_errors: dict[str, str] = {}  # a background lead-stop that failed, until the strip reports it

    def _lead_stopped(team: str, lead: str) -> Any:
        """What becomes of the background half of a stop. A task whose exception nobody retrieves is
        a silent failure path, and §4.5 "Errors" says there is none here — so the outcome is logged
        either way, and a failure is kept for the strip to report on its next refresh, which is the
        only channel a request that has already returned still has (review of PR #124)."""

        def done(task: asyncio.Task[Any]) -> None:
            background.discard(task)
            stopping_leads.discard(team)
            if task.cancelled():
                log.warning("team %s: stopping its lead %s was cancelled", team, lead)
                stop_errors[team] = f"stopping {lead} was cancelled"
                return
            err = task.exception()
            if err is None:
                log.info("team %s: lead %s stopped", team, lead)
                stop_errors.pop(team, None)
                return
            log.error("team %s: stopping its lead %s failed: %s", team, lead, err)
            stop_errors[team] = f"{lead} did not stop: {str(err).strip(chr(34))}"

        return done

    def _team_http(e: Exception) -> HTTPException:
        """One mapping for every way a start or a stop can fail, so the strip reports the agent's
        own message the way every other control does — a toast (design §4.5 "Errors")."""
        if isinstance(e, AgentUnavailable):
            return HTTPException(503, f"host agent unreachable: {e}")
        if isinstance(e, teamrun.PartialStart):
            # The created sessions are running on the brief, so a stale-brief warning belongs on
            # this path most of all (review of PR #139). `detail` is what the toast renders.
            warnings = " · ".join([*e.plan.warnings, *e.plan.notes])
            return HTTPException(409, f"{e}{' — ' + warnings if warnings else ''}")
        return HTTPException(400, str(e).strip('"'))

    @app.get("/api/teams")
    async def api_teams():
        v = teams_view(await call("list"))
        for row in v.get("teams", []):
            if row["name"] in stopping_leads:
                row["stopping"] = True
            failed = stop_errors.pop(row["name"], None)  # reported once, then forgotten
            if failed:
                row["error"] = failed
        return v

    @app.post("/api/teams/{name}/start")
    async def api_team_start(name: str):
        if hosts.is_node():
            raise HTTPException(409, node_org_note())  # the strip's note, as the toast (§4.4a)
        org, _notes = org_here()
        try:
            _plan, result = await asyncio.to_thread(teamrun.start, rpc, org, name, host_name())
        except (teams.TeamError, ValueError, AgentError, AgentUnavailable) as e:
            # A failed pre-flight check created nothing (§4.9): the toast is the whole outcome.
            raise _team_http(e) from None
        return JSONResponse({"ok": True, **result})

    @app.post("/api/teams/{name}/stop")
    async def api_team_stop(name: str, request: Request):
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        now = bool(body.get("now"))
        if hosts.is_node():
            raise HTTPException(409, node_org_note())
        org, _notes = org_here()
        try:
            st = await asyncio.to_thread(teamrun.stop_members, rpc, org, name, now=now)
        except (teams.TeamError, ValueError, AgentError, AgentUnavailable) as e:
            raise _team_http(e) from None
        pending = None
        if st.lead is not None and name in stopping_leads:
            # Two presses, or a slow first stop: the lead is already being stopped and a second task
            # would send it a second wrap-up or kill, whose failure the first would swallow.
            return JSONResponse(
                {
                    "ok": True,
                    "team": name,
                    "now": now,
                    "sessions": st.acted,
                    "manager": None,
                    "text": f"{name}: already stopping — its manager follows when the members settle",
                }
            )
        if st.lead is not None:
            # §4.9's order is members, then the lead once they settle — up to the wrap-up window,
            # which is minutes. The page must not hold a request open that long, so the second half
            # runs behind the response and the state deltas on /events show it happening.
            pending = st.lead.get("name") or st.lead["id"]
            stopping_leads.add(name)
            task = asyncio.create_task(asyncio.to_thread(teamrun.stop_lead, rpc, st))
            background.add(task)
            task.add_done_callback(_lead_stopped(name, pending))
        sent = len(st.acted)
        msg = f"{name}: {'killed' if now else 'wrap-up sent to'} {sent} session{'' if sent == 1 else 's'}"
        if pending:
            msg += f" — {pending} follows when they settle"
        return JSONResponse(
            {"ok": True, "team": name, "now": now, "sessions": st.acted, "manager": pending, "text": msg}
        )


def _inbox_routes(app: FastAPI, h: SimpleNamespace) -> None:
    """The person's Inbox: the page, its payload and its controls (design §4.10, §4.5a)."""
    call, person_view, inbox_html, board_items = h.call, h.person_view, h.inbox_html, h.board_items

    @app.get("/inbox", response_class=HTMLResponse)
    async def inbox_page(request: Request):
        """design §4.5 screen 6 / §4.5a **Inbox page** (TD-069 steps 1 and 2): full width, the
        person inbox and the sessions' states in three sections, and the count that means *what is
        waiting on a person*. From step 3 the due board items join **Needs you** too."""
        agent_down = False
        try:
            got, states = await person_view()
        except HTTPException as e:
            if e.status_code != 503:
                raise
            got, states, agent_down = {"entries": []}, [], True  # the banner + Retry, never a bare 503
        # with the host agent down nothing is claimed as waiting — the Org's top bar and the poll say
        # the same — so the board is left unread rather than counted on this page alone (review of #472)
        boards, board_note = ([], "") if agent_down else await board_items()
        sections = inbox_sections(
            got["entries"],
            states=states,
            trail=got.get("trail") or (),
            attention_snoozed=got.get("attention_snoozed"),
            boards=boards,
        )
        picks = rail_picks(request.query_params)
        return templates.TemplateResponse(
            request,
            "inbox.html",
            {
                "sections": sections,
                "picks": picks,
                "rail": rail_counts(rail_rows(sections), picks),
                "origin": page_origin(request),
                "board_note": board_note,
                "board_choices": board_choices(),
                "person_needs": sections["count"],
                "person_fyi": sections["fyi_n"],
                "host": host_name(),
                "active": "Inbox",
                "agent_down": agent_down,
                "volatile": hosts.local_host().volatile,
                "usage": {},
            },
        )

    @app.get("/inbox/{mid}", response_class=HTMLResponse)
    async def inbox_entry_page(request: Request, mid: str):
        """design §4.5 screen 6 *The message page*, §4.5a **Inbox message page** (TD-129, built by
        TD-136): one mail entry whole — its row, drawn by the row's own macro in the section the
        list has it in, so its controls, RPCs and confirms are the list's, with *details* open —
        then its thread from the person-only `thread` read, oldest first, none of it a control.
        **Back** returns to the list the page was opened from (`back`, the list's query) at this
        row. An entry the person inbox no longer holds reads *gone*; a refusal (another host's id,
        §4.4a) reads in the host agent's words. Reading marks nothing (§4.10)."""
        back = str(request.query_params.get("back") or "")
        back = back if back.startswith("?") else ""
        ctx: dict[str, Any] = {
            "host": host_name(),
            "active": "Inbox",
            "usage": {},
            "volatile": hosts.local_host().volatile,
            "mid": mid,
            "back_url": f"/inbox{back}#{mid}",
            "origin": page_origin(request),
            "fold_open": True,
            "board_choices": board_choices(),
            "entry": None,
            "section": "",
            "thread": [],
            "pruned": False,
            "gone": "",
        }
        try:
            got, states = await person_view()
        except HTTPException as e:
            if e.status_code != 503:
                raise
            ctx["gone"] = f"{host_name()}: host agent unreachable — no mail can be read until it is back"
            return templates.TemplateResponse(request, "inbox_entry.html", ctx)
        sections = inbox_sections(
            got["entries"],
            states=states,
            trail=got.get("trail") or (),
            attention_snoozed=got.get("attention_snoozed"),
        )
        for sec in INBOX_SECTIONS:
            e = next((x for x in sections[sec] if x.get("id") == mid and not x.get("row")), None)
            if e is not None:
                ctx["entry"], ctx["section"] = e, sec
                break
        if ctx["entry"] is None:
            ctx["gone"] = f"gone: {mid} is no longer in the person inbox — pruned by retention, or dismissed"
            if "@" in mid:  # another host's entry (§4.4a): the host agent says why in its own words
                try:
                    await call("thread", msg=mid)
                except HTTPException as err:
                    ctx["gone"] = str(err.detail)
            return templates.TemplateResponse(request, "inbox_entry.html", ctx)
        try:
            th = await call("thread", msg=mid)
        except HTTPException as err:
            # an older host agent has no `thread`: the entry still reads whole, the thread says why
            ctx["thread_error"] = str(err.detail)
            th = {"entries": [], "pruned": False}
        # a thread reaches senders the person inbox never heard from (a techlead's reply to its asker)
        names = {o.get("id"): o.get("name") or o.get("id") for o in await call("list")}
        ctx["thread"] = [
            {
                **t,
                "from_name": "person" if t.get("from") == "person" else names.get(t.get("from")) or t.get("from"),
                "age": _age(t.get("at"), datetime.now(UTC)),
            }
            for t in th.get("entries") or ()
            if t.get("id") != mid
        ]
        ctx["pruned"] = bool(th.get("pruned"))
        return templates.TemplateResponse(request, "inbox_entry.html", ctx)

    @app.get("/api/person/inbox")
    async def api_person_inbox(request: Request):
        """design §4.5a Org top bar **Inbox** and the **Inbox page** (§4.10): the entries, which
        section each is in, their rendered rows, and `needs` — the count, computed in the one place
        (`inbox_sections`) the page renders from, so the top bar's number and the page cannot
        disagree. Both poll this: the pushed stream carries session records only, and the person
        inbox belongs to none. The read marks nothing (`person_inbox`, above).

        From TD-069 step 2 the **state rows** ride this poll too, rendered from a fresh `list`
        (`person_states`): the Org's pushed stream is per-record and this page is per-person, so
        the simplest correct thing is one snapshot per poll — a row whose state changed in between
        is corrected by the next one, and a permission answered here leaves at once because the
        press refreshes."""
        try:
            got, states = await person_view()
        except HTTPException as e:
            if e.status_code != 503:
                raise
            # The page's own load already answers a host agent that is down with a banner; its
            # **poll** answered a bare 503, which the client could only drop on the floor — the
            # rows would sit there looking current (design §4.5 *there is no silent failure path*,
            # TD-069's leftover). So the poll says it in a shape the page can render: no entries,
            # no counts claimed, and the reason in words.
            return {
                "entries": [],
                "sections": {k: [] for k in INBOX_SECTIONS},
                "needs": None,  # not zero: nothing is *known* to be waiting, which is not *nothing is*
                "fyi_n": None,  # the same rule for the second number: not known is not zero
                "snoozed_n": 0,
                "answered_marks": None,  # not known either: the team headers keep what they showed
                "html": {},
                "unread": None,
                "agent_down": True,
                "why": str(e.detail),
            }
        boards, board_note = await board_items()
        sections = inbox_sections(
            got["entries"],
            states=states,
            trail=got.get("trail") or (),
            attention_snoozed=got.get("attention_snoozed"),
            boards=boards,
        )
        got["agent_down"] = False
        got["board_note"] = board_note
        got["sections"] = {k: [e["id"] for e in sections[k]] for k in INBOX_SECTIONS}
        got["needs"] = sections["count"]
        # §4.10 *The Inbox is a queue*: FYI's own quiet number, **never added to the first** — the
        # first is *what needs you*. The page opens the section by itself when this is higher than
        # the browser last saw, which is what stops a folded FYI hiding mail nobody counted.
        got["fyi_n"] = sections["fyi_n"]
        got["snoozed_n"] = len(sections["snoozed"])
        # §4.5a **team header** → *answered for you* count (§4.9b, TD-075): each row's team and
        # time, and nothing it says — the browser counts those newer than it last opened the group
        # (that memory is the browser's, as FYI's *new* mark is), so the home keeps no read state
        got["answered_marks"] = [{"team": e.get("team") or "", "at": e.get("at") or ""} for e in sections["answered"]]
        got["html"] = inbox_html(sections, page_origin(request))
        # the rail's *Teams* lines (§4.5 screen 6 *The rail*): a team appears or goes with its rows,
        # so the poll brings the group's markup as it brings the rows'; the script presses the lines
        # the URL picks and recounts every line from the rows on the page
        rail = rail_counts(rail_rows(sections), rail_picks({}))
        got["html"]["rail_teams"] = str(
            templates.get_template("inbox_rail.html").module.teams_group(rail, rail_picks({}))  # type: ignore[attr-defined]
        )
        return got

    # design §4.5a **Inbox row** controls (§4.10): each is a person's own act on their own inbox,
    # so each calls its RPC **caller-less** — a person is not a session, and every one of these is
    # refused to every session by the agent. The UI adds no rule of its own: a refusal comes back
    # as the toast every other RPC error on the page does.
    PERSON_ACTS = {"pause": "inbox_pause", "resume": "inbox_resume", "gowithit": "inbox_go_with_it"}

    @app.post("/api/person/{action}")
    async def api_person_action(action: str, request: Request):
        """design §4.5a Org top bar **person inbox** → Reply and delete (§4.10): a person's reply
        lands in the sender's inbox (the agent addresses it to the entry's sender and closes its
        `ask`); delete removes the entry from the person inbox only — the sender keeps its copy.
        From 2026-09-19 (TD-069 step 1) the Inbox page's own controls join it: **Snooze** and
        **Unsnooze** (`inbox_snooze`, with and without an `until`), **Pause** / **Resume**, and
        **Go with it**."""
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        if action in PERSON_ACTS or action == "snooze":
            ref = str(body.get("msg") or "").strip()
            if not ref:
                raise HTTPException(400, f"{action} needs the entry's id")
            if action == "snooze":
                # no `until` is the clear — *Unsnooze* on the page's snoozed list (§4.10 *Snooze*)
                until = str(body.get("until") or "").strip() or None
                got = await call("inbox_snooze", msg=ref, until=until)
            else:
                got = await call(PERSON_ACTS[action], msg=ref)
            return JSONResponse({"ok": True, **got})
        if action == "board":
            # design §4.5a **Due strip / Inbox board row** → **Snooze ▾** and **Done** on a board row
            # (§4.4 *Board write-back*, TD-069 step 3): the host agent edits the one line and commits
            # it in the repo's main checkout. The row hands back what the reader gave it — the board,
            # the line and its text — and the agent refuses the edit when that line has moved on.
            what = str(body.get("action") or "")
            if what == "add":
                # §4.5a *Inbox row: FYI* → **Put on the board** (TD-140): the form's board, text and
                # Due, and the entry it comes from; the agent writes the one line, commits it, and
                # only then dismisses the entry — its refusal is the form's, drawn in place
                ref, board, text = (str(body.get(k) or "").strip() for k in ("msg", "board", "text"))
                due = str(body.get("due") or "").strip()
                if not ref or not board or not text or not due:
                    raise HTTPException(400, "Put on the board names the entry, the board, the text and a Due date")
                got = await call("board_edit", board=board, action="add", text=text, due=due, entry=ref)
                await board_items(fresh=True)
                return JSONResponse({"ok": True, **(got if isinstance(got, dict) else {})})
            if what not in BOARD_ACTS:
                raise HTTPException(400, f"a board row's act is {' or '.join(BOARD_ACTS)}, not {what!r}")
            try:
                line = int(body.get("line"))
            except (TypeError, ValueError):
                raise HTTPException(400, "a board row's act names the item's line") from None
            board, text = str(body.get("board") or ""), str(body.get("text") or "")
            if not board or not text:
                raise HTTPException(400, "a board row's act names the board and the item's text")
            due = str(body.get("due") or "").strip() or None
            if what == "snooze" and not due:
                raise HTTPException(400, "a snooze names the new date, YYYY-MM-DD")
            got = await call("board_edit", board=board, line=line, text=text, action=what, due=due)
            await board_items(fresh=True)
            return JSONResponse({"ok": True, **(got if isinstance(got, dict) else {})})
        if action == "dismiss":
            # design §4.10 *The Inbox is a queue* (TD-079 step 2): **Dismiss** and **Dismiss all**.
            # A **list of ids**, because *Dismiss all* dismisses the entries **this browser has on
            # screen** — mail that arrived after the page was drawn is exactly what must not go
            # unseen — and the ids are the browser's, mail (`m-`) and trail (`t-`) alike. The agent
            # refuses an id that is still an open question, naming it; that comes back as the toast.
            raw = body.get("msg")
            ids = [str(x).strip() for x in (raw if isinstance(raw, list) else [raw]) if str(x or "").strip()]
            if not ids:
                raise HTTPException(400, "dismiss names the entries to dismiss, by id")
            got = await call("inbox_dismiss", msg=ids)
            return JSONResponse({"ok": True, **got})
        if action == "attention_snooze":
            # design §4.5a **Inbox row: state** / §4.10: a **state row's** snooze — `stalled?` and
            # unpushed work, the two that are not on the tool's clock. It is keyed on the record
            # **and the row kind**, because a session's permission and its stalled row are two rows
            # and snoozing one is not snoozing the other; no `until` is the clear (*Unsnooze*).
            sid, kind = str(body.get("id") or "").strip(), str(body.get("kind") or "").strip()
            if not sid or not kind:
                raise HTTPException(400, "a state row's snooze names the session and the row kind")
            got = await call("attention_snooze", id=sid, kind=kind, until=str(body.get("until") or "").strip() or None)
            return JSONResponse({"ok": True, **got})
        if action == "suspend":
            # design §4.8a *An alarm's answers* (TD-077 a2): **Suspend** — a person's own act, and
            # refused to every session by the agent for the reason `identity_ack` is, turned around:
            # a session that could suspend could stop its rival. Caller-less, like every control on
            # this page; `why` is left to the agent, which composes it from the alarm's own words.
            who = str(body.get("id") or "").strip()
            if not who:
                raise HTTPException(400, "suspend names the session to suspend")
            got = await call("suspend", id=who)
            return JSONResponse({"ok": True, **got})
        if action == "identity_log":
            # design §4.5a **Inbox row: identity alarm** (§4.8a *An alarm's answers*, TD-077 b):
            # **Log TD** hands the record's alarms to the session that answers for it, as mail from
            # the person that owes an outcome, and clears the list. The agent picks the controller
            # and composes the words; the page names nobody. A controller that went between the
            # draw and the press is refused by the agent in words, which is the toast.
            who = str(body.get("id") or "").strip()
            if not who:
                raise HTTPException(400, "Log TD names the session whose alarms it hands on")
            got = await call("identity_log", id=who)
            return JSONResponse({"ok": True, **got})
        if action == "identity_ack":
            # design §4.5a **Inbox row: identity alarm** (§4.8a, TD-077 step 2): a person has seen
            # the alarms and decided what they were, so the list is cleared and the row leaves.
            # Caller-less like every other control here — the agent refuses it to every session,
            # and the log keeps every alarm, so acknowledging loses nothing.
            got = await call("identity_ack", id=str(body.get("id") or "").strip() or None)
            return JSONResponse({"ok": True, **got})
        if action == "reply":
            ref = str(body.get("reply_to") or "").strip()
            if not ref:
                raise HTTPException(400, "a reply names the entry it answers")
            # design §4.5a **Inbox row: suggested answers** (§4.10, TD-070): a press sends **the
            # index**, never the label. The text is looked up here, from the entry the home holds,
            # so a tampered DOM cannot make the person "say" something else under a given index —
            # and the RPC re-checks that the two agree, because this route is not the only caller.
            answer = body.get("answer")
            text = str(body.get("text") or "")
            if answer is not None:
                if isinstance(answer, bool) or not isinstance(answer, int):
                    raise HTTPException(400, "a suggested answer is picked by its index")
                held = next((e for e in (await call("inbox"))["entries"] if e.get("id") == ref), None)
                offered = suggested_answers(held or {})
                if not 0 <= answer < len(offered):
                    raise HTTPException(400, "that is not one of the suggested answers")
                text = offered[answer]
            got = await call("msg", text=text, kind="reply", reply_to=ref, answer=answer)
            return JSONResponse({"ok": True, "id": got["entry"]["id"], "delivered": got["delivered"]})
        if action == "unmail":
            ref = str(body.get("msg") or "").strip()
            if not ref:
                raise HTTPException(400, "delete needs the entry's id")
            got = await call("inbox_delete", msg=ref)
            # design §4.10 *Deleting is declining*: on an open `ask` or `steer` the entry is not
            # stripped — it closes as `declined`, the asker is told, and it stays for retention.
            return JSONResponse({"ok": True, "unread": got["unread"], "declined": bool(got.get("declined"))})
        raise HTTPException(404, f"no action {action}")


def _stream_routes(app: FastAPI, h: SimpleNamespace) -> None:
    """The two websockets: the events stream and the terminal."""
    call, render_card, seats_of, group_heads = h.call, h.render_card, h.seats_of, h.group_heads

    # -- live state ------------------------------------------------------------------------------

    @app.websocket("/events")
    async def events(ws: WebSocket):
        await ws.accept()

        async def gone() -> None:
            # The page never sends on this socket, so the next message is its disconnect. Without
            # this watch the handler noticed a closed tab only when the next event's send failed —
            # and uvicorn's shutdown waits on the handler, which is the UI's 40 s stop (TD-058).
            while (await ws.receive()).get("type") != "websocket.disconnect":
                pass

        async def stream(c: LocalClient) -> None:
            # A delta carries one record, but membership is read across records (design §4.8):
            # a card's *under* chip names its controllers, which live on other records. So the
            # loop keeps the fleet it has already been told about — seeded once, then updated
            # by the very deltas it is rendering — rather than re-listing per event.
            known: dict[str, dict[str, Any]] = {}
            with contextlib.suppress(Exception):
                known = {o["id"]: o for o in await call("list")}
            acct_of: dict[str, str] = {}  # profile → the account chip it is drawn on (TD-122)
            with contextlib.suppress(Exception):
                for name, acc in usage_accounts(await call("usage")).items():
                    for p in acc["profiles"]:
                        acct_of[p["name"]] = name
            async for ev in c.subscribe():
                if ev.get("event") == "session":
                    s = ev["session"]
                    known[s["id"]] = s
                    v = view(s, list(known.values()), icons=await role_icons([s]), seats=await seats_of([s]))
                    # `groups` rides on every delta (design §4.5a **team groups**): a badge or a
                    # `controllers` change on one record can move a card, change a lead, or turn
                    # grouping on or off for the whole page, and only the server sees the fleet.
                    await ws.send_text(
                        json.dumps(
                            {
                                "event": "session",
                                "id": s["id"],
                                "state": s["state"],
                                "rank": v["rank"],  # the view's: unseen idle sorts above idle
                                "html": render_card(v),
                                "session": v,
                                "groups": await group_heads(known),
                            }
                        )
                    )
                elif ev.get("event") in ("gone", "usage"):
                    if ev.get("event") == "gone":
                        # only a `gone` names a session; a `usage` event carries a profile, and
                        # popping on it would one day evict a live session by coincidence
                        went = str(ev.get("id") or "")
                        known.pop(went, None)
                        await ws.send_text(json.dumps({**ev, "groups": await group_heads(known)}))
                        # A card's *under* chip names another record, so the session that went
                        # is not the only card now out of date: every card listing it as a
                        # controller has to be redrawn, or it keeps naming and linking to a
                        # session that is gone until the page is reloaded (review 2026-09-13).
                        for other in list(known.values()):
                            if went in (other.get("controllers") or []):
                                ov = view(
                                    other,
                                    list(known.values()),
                                    icons=await role_icons([other]),
                                    seats=await seats_of([other]),
                                )
                                await ws.send_text(
                                    json.dumps(
                                        {
                                            "event": "session",
                                            "id": other["id"],
                                            "state": other["state"],
                                            "rank": ov["rank"],
                                            "html": render_card(ov),
                                            "session": ov,
                                        }
                                    )
                                )
                        continue
                    # The agent pushes a profile's reading; the page draws **one chip per account**
                    # (§4.5a **usage**, TD-122), with the lowest line among the account's profiles and
                    # each profile's sessions on hover — so the account is regrouped here from the
                    # whole reading, the gate's lines and the fleet this loop already keeps. A profile
                    # that went (`usage: null`) redraws the account it was on, or takes its chip off.
                    prof = str(ev.get("profile"))
                    was = acct_of.pop(prof, prof)
                    readings: dict[str, Any] = {}
                    with contextlib.suppress(Exception):
                        readings = await call("usage")
                    with contextlib.suppress(Exception):
                        readings = with_lines(readings, await call("gate"))
                    grouped = usage_accounts(readings, list(known.values()))
                    for name, acc in grouped.items():
                        for p in acc["profiles"]:
                            acct_of[p["name"]] = name
                    now = acct_of.get(prof)
                    for name in {was, now} - {None}:
                        await ws.send_text(json.dumps({"event": "usage", "account": name, "usage": grouped.get(name)}))

        try:
            async with LocalClient() as c:
                tasks = {asyncio.ensure_future(stream(c)), asyncio.ensure_future(gone())}
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for t in tasks:
                        t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                for t in done:
                    t.result()  # the stream's own failure is handled below, as it always was
        except (WebSocketDisconnect, AgentUnavailable, ConnectionError):
            pass
        except Exception:  # noqa: BLE001
            with contextlib.suppress(Exception):
                await ws.send_text(json.dumps({"event": "error", "text": "events stream failed; reconnecting"}))
        finally:
            with contextlib.suppress(Exception):
                await ws.close()

    # -- terminal --------------------------------------------------------------------------------

    @app.websocket("/term/{sid}")
    async def term(ws: WebSocket, sid: str):
        # Size comes from the query, leniently: a bad or missing value falls back to a default
        # instead of a 403 before accept, which a browser can only report as an opaque 1006
        # (first-use finding 2026-09-06). The client re-sends its real size on open anyway.
        cols = _int_param(ws.query_params.get("cols"), 120, 10, 500)
        rows = _int_param(ws.query_params.get("rows"), 32, 2, 200)
        await ws.accept()
        try:
            s = await call("get", id=sid)
        except HTTPException as e:
            await ws.send_bytes(f"\r\n[agentorc] {e.detail}\r\n".encode())
            await ws.close(code=4404)  # final: the client must not retry
            return
        sid = sid.removesuffix(f"@{host_name()}")  # a self-addressed id is this host's tmux session
        sock = os.environ.get("AGENTORC_TMUX_SOCKET")
        inside: list[str] = []  # the `docker exec` prefix every tmux command takes for a container node's session
        argv = attach_argv(sid, socket_name=sock)
        if s.get("host") and s["host"] != host_name():
            # Another host's session (design §4.4a "Reach"): a container node on this machine is
            # reached by `docker exec` into it, from what the home derived when it dialed in; a
            # machine node's pane has nothing here that reaches it yet.
            reach = (s.get("host_link") or {}).get("reach") or {}
            if not reach.get("container"):
                await ws.send_bytes(
                    f"\r\n[agentorc] runs on {s['host']}: no terminal reaches it from here "
                    "(a container node's reach comes with its link; a machine node's waits for the terminal "
                    "over the link, TD-057 *Later*).\r\n".encode()
                )
                await ws.close(code=4404)
                return
            sid, sock = naming.split_address(sid)[0], None  # the bare id, on the user's default server inside
            argv = attach_argv_in(reach["container"], reach.get("user") or "root", sid)
            inside = argv[: argv.index("tmux")]
        if s.get("state") == "closed" or not s.get("pane", True):  # no pane to attach (TD-023)
            await ws.send_bytes(b"\r\n[agentorc] this session's pane is gone (see the banner).\r\n")
            await ws.close(code=4404)
            return
        try:
            pty = PtySession(argv, cols=cols, rows=rows)
        except Exception as e:  # noqa: BLE001 — no silent failure path (design §4.5)
            await ws.send_bytes(f"\r\n[agentorc] could not attach a terminal: {type(e).__name__}: {e}\r\n".encode())
            await ws.close()
            return

        # design §4.6 *A read-only attach* (TD-096): the mode is read once, here, and a read-only attach
        # tells the page so in its first frame — a text frame, which is the page's to read and not
        # pane output, so it neither counts as a painted screen nor resets the page's backoff. An
        # attach that sends none takes keys. A mode change re-attaches.
        read_only = bool(s.get("unattended"))
        if read_only:
            await ws.send_text(json.dumps({"read_only": True}))
        produced = False

        async def send(data: bytes) -> None:
            nonlocal produced
            produced = True
            await ws.send_bytes(data)

        async def recv() -> Any:
            msg = await ws.receive()  # a disconnect arrives as a message, never as an exception here
            if msg.get("type") == "websocket.disconnect":
                return None
            if msg.get("bytes") is not None:
                return msg["bytes"]
            text = msg.get("text") or ""
            if text.startswith("{"):
                with contextlib.suppress(ValueError):
                    return json.loads(text)
            return text

        reapers: set[asyncio.Future[int]] = set()

        async def scroll(direction: str) -> None:
            # A tmux command against the session, not keys into the pane: there is no escape
            # sequence that enters copy mode (TD-022). Bad directions are the client's bug; ignore.
            try:
                argv = [*[a for a in inside if a != "-it"], *scroll_argv(sid, direction, socket_name=sock)]
            except ValueError:
                return
            devnull = asyncio.subprocess.DEVNULL
            proc = await asyncio.create_subprocess_exec(*argv, stdout=devnull, stderr=devnull)
            # Reap in the background: waiting here would hold the key pump behind a slow tmux.
            reapers.add(asyncio.ensure_future(proc.wait()))

        try:
            await pump(pty, send, recv, scroll, read_only=read_only)
        finally:
            for f in reapers:
                f.cancel()
            # `pump` has already closed the pty, so the child is reaped and its status is final:
            # a `tmux attach` that exited on its own carries its code, and one the teardown
            # signalled carries None, which is the retryable case (TD-029).
            status = pty.exit_status()
            with contextlib.suppress(Exception):
                if status not in (0, None) or not produced:
                    # A dead attach is final (TD-029): `tmux attach` exits non-zero when its session
                    # is gone, and an attach that never painted a screen attached to nothing. Either
                    # way the record is behind — retrying twice a second would print tmux's "can't
                    # find session" forever, which is what Paul saw on 2026-09-10.
                    await ws.send_bytes(
                        b"\r\n[agentorc] this session's pane is gone (the attach ended immediately).\r\n"
                    )
                    await ws.close(code=4404)
                else:
                    await ws.close()


app = create_app()


def main(argv: list[str] | None = None) -> int:
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(prog="agentorc-ui")
    ap.add_argument("--bind", default="127.0.0.1", help="address to listen on (design §4.5: never the LAN)")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    # lifespan="off": the app has no startup/shutdown handlers, and with lifespan on, Ctrl+C makes
    # uvicorn log a CancelledError traceback from starlette's lifespan task (seen 2026-09-06).
    uvicorn.run(
        "agentorc.ui.app:app", host=args.bind, port=args.port, log_level="info", ws_ping_interval=20, lifespan="off"
    )
    return 0


async def _wait_agent() -> bool:
    for _ in range(20):
        if paths.socket_path().exists():
            return True
        await asyncio.sleep(0.25)
    return False
