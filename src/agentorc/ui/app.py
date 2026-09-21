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
import time
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agentorc import org as orgmod
from agentorc import profiles as profiles_mod
from agentorc import repoconfig, teamrun, teams
from agentorc.cli import stop_time as clistop
from sessionorc import hosts, identity, mail, naming, paths
from sessionorc.adapters import short_model
from sessionorc.client import AgentError, AgentUnavailable, LocalClient
from sessionorc.client import call_sync as _call_sync
from sessionorc.containers import attach_argv_in
from sessionorc.models import GRANTS, STATE_RANK, canonical_grants, has_control, report_head, report_line, stop_note

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


def usage_chip(prof: str, u: Any) -> dict[str, Any] | None:
    """One profile's top-bar chip (design §4.5a **usage**, TD-073, TD-087), or None for no chip.

    The worst window is printed, every window on hover, red at a cap. **A held reading goes stale,
    not out** (TD-087): when the last poll was refused, the host agent keeps the last good windows
    with the `reason` beside them, and the chip draws them dimmed with *· stale* and says on hover
    when they were read and why the poll since failed — *the chip went out* and *the allowance is
    spent* are different things to a person. A refusal with no reading ever held is `<profile> no
    reading`, the same way: a chip that silently went out is what this entry was. An `ok` answer
    with no windows is an adapter that reports no quota, which has no chip.

    `app.js`'s `AO.usageChip` is the same rule for a pushed `usage` event; the tests hold the two
    to the same cases."""
    if not isinstance(u, dict):
        return None
    windows = [w for w in (u.get("windows") or []) if isinstance(w, dict) and isinstance(w.get("pct"), int | float)]
    reason = str(u.get("reason") or "ok")
    stale = reason != "ok"
    if not windows and not stale:
        return None
    why = ""
    if stale:
        why = "the last poll was refused: " + USAGE_WHY.get(reason, reason)
        if isinstance(u.get("retry_after"), int | float):
            why += f", which asked to be left {max(1, math.ceil(u['retry_after'] / 60))} min"
    if not windows:
        title = f"no usage reading for {prof} yet — {why}"
        return {"text": f"{prof} no reading", "title": title, "pct": 0, "cls": "stale"}
    ws = sorted(windows, key=lambda w: w["pct"], reverse=True)
    worst = ws[0]
    title = " · ".join(f"{w.get('label')} {w['pct']}% (resets {w.get('resets') or '?'})" for w in ws)
    cls = "cap" if worst["pct"] >= 100 else "near" if worst["pct"] >= NEAR_CAP else ""
    text = f"{prof} {worst.get('label')} {worst['pct']}%"
    if stale:
        title = f"held reading from {u.get('fetched') or 'an unknown time'} — {why}. {title}"
        text += " · stale"
        cls = f"{cls} stale".strip()
    return {"text": text, "title": title, "pct": worst["pct"], "cls": cls}


templates.env.globals["usage_chip"] = usage_chip

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
    """`vscode://vscode-remote/ssh-remote+<alias><path>` — the alias must be in the person's own
    ~/.ssh/config (design §4.5) — or `vscode://file/…` when the UI runs where the person sits."""
    h = hosts.local_host()
    # Percent-encode the path: a space or `?` in a directory name would otherwise produce a URI the
    # browser silently drops (TD-011). `/` stays, so the path reads as a path.
    path = quote(directory, safe="/")
    if h.local:
        return f"vscode://file{path}?windowId=_blank"
    # windowId=_blank: a new VS Code window. Without it the handler reuses the current window and
    # replaces whatever it was showing (first-use finding 2026-09-06).
    return f"vscode://vscode-remote/ssh-remote+{h.vscode_host}{path}?windowId=_blank"


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
    a repo on another host: no icon, and the **default label** — the role's name, raised, an old
    name read through the renamed-roles table — never nothing (§4.8 *The names*)."""
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
) -> dict[str, Any]:
    """Everything a card or the Focus header needs, computed once. `fleet` is the other records,
    needed only for the membership directions (design §4.8): who controls this session, and — for
    a lead — which sessions it controls. Without it both come back empty, which is what a
    caller that has only one record should show."""
    now = datetime.now(UTC)
    d = dict(s)
    state = s["state"]
    d["state_class"] = {
        "needs-you": "needs",
        "stalled?": "stalled",
        "closed": "done",
    }.get(state, state)
    d["state_label"] = {"needs-you": "needs you", "closed": "closed"}.get(state, state)
    d["rank"] = STATE_RANK.get(state, 9)
    # Finished while nobody was looking (design §4.2, TD-017): not a state, a rendering of `idle`
    # that sorts just above the idle it will become once someone opens Focus. Both stamps are whole
    # seconds, so a finish in the same second as the last look reads as seen.
    d["unseen"] = state == "idle" and (not s.get("seen_at") or (s.get("since") or "") > s["seen_at"])
    if d["unseen"]:
        d["state_label"] = "finished · unseen"
        d["rank"] = STATE_RANK["idle"] - 0.5
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
    # A container node's record reaches VS Code by attaching to that container (§4.4a "Reach"),
    # from what the home derived when the node dialed in; any other host's record has no link.
    d["vscode"] = vscode_url(s["dir"]) if s.get("dir") and here else (hl.get("reach") or {}).get("vscode", "")
    d["place"] = f"{d['host']} / {Path(s['repo']).name}" if s.get("repo") else f"{d['host']} / {s.get('dir', '')}"
    git = s.get("git") or {}
    where = s.get("dir", "")
    if s.get("repo") and s.get("dir") and s["dir"] != s["repo"]:
        where = f"wt/{Path(s['dir']).name}"
    if git.get("branch"):
        where += f" → {git['branch']}"
    d["where"] = where
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
    d["grants_all"] = list(GRANTS)
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
    d["holds_control"] = has_control(s.get("capabilities"))
    # `fleet_known=False`: the caller asked for the fleet and did not get it. An empty members list
    # then means *unknown*, and Ready to close must not read it as *none* (review of PR #195).
    d["ready"] = ready_to_close(s, d["members"] if fleet_known else None)
    return d


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
        members = sorted(by_team[team], key=lambda v: (v["rank"], v["name"]))
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
        groups.append(
            {
                "team": team,
                "label": team or "No team",
                # `doing` rides with the manager (design §4.5a **team groups**, TD-074): the team
                # card's header shows its manager's line, which is the manager reporting on the team
                # without being asked to narrate each member. `role_label` is what the header calls
                # it (§4.8 *The names*, TD-076): *Manager*, or whatever label its role carries.
                "manager": {
                    k: manager.get(k)
                    for k in ("id", "name", "state", "state_class", "state_label", "scraped", "doing", "role_label")
                }
                if manager
                else None,
                "manager_elsewhere": manager_elsewhere,  # its card sits under its own badge, not here
                "members": members,
                "ids": [m["id"] for m in members],
                "projects": projects or list(row.get("projects") or []),
                "needs": sum(1 for m in members if m.get("state") == "needs-you"),
                "live": sum(1 for m in members if m.get("state") not in DEAD),
                # a definition exists, so the group's card carries Start, or Stop / Stop now (§4.5a)
                "defined": team in defs,
                "source": row.get("source"),
                "def_manager": row.get("manager"),  # the definition's word, for a card with no sessions yet
                "def_members": row.get("members"),
                # *nothing running* and *nothing left to run* are different facts (§4.9a)
                "wound_down": row.get("wound_down"),
                "wound_down_age": row.get("wound_down_age"),
            }
        )
    # what is running is read first; *No team* is never "stopped" — nothing there starts as one
    # …and among the live teams, one with a session that needs a person comes first (2026-09-18)
    groups.sort(
        key=lambda g: (2 if g["team"] and not g["live"] else 1 if not g["team"] else 0, not g["needs"], g["team"])
    )
    return groups


# -- the Inbox page (design §4.5 screen 6, §4.5a **Inbox page**, §4.10; TD-069 step 1) -------------

PERSON_ASK_KINDS = ("ask", "conflict")  # what reads as a question to the person; `steer` has its own rules
# design §4.5 screen 6 / §4.10 *Outcomes* (TD-079 step 2): **Waiting on them** is the fourth
# section, under *Steering* and in neither number — it waits on a session, not on the person.
INBOX_SECTIONS = ("needs", "steering", "waiting", "fyi", "snoozed")


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
) -> dict[str, Any]:
    """Design §4.5 screen 6: the person inbox split into the page's three sections, plus what is
    snoozed — and, from TD-069 step 2, the **session states** (`states`, from `state_rows`) joined
    into **Needs you** here rather than anywhere else. Board items are step 3 and join the same way.

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
    does not simply vanish."""
    at = now or datetime.now(UTC)
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in INBOX_SECTIONS}
    snoozed_rows = attention_snoozed or {}
    for r in states:
        raw = snoozed_rows.get(f"{r.get('sid') or ''}|{r.get('row') or ''}")
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
        elif e["id"] in settles:
            continue  # the reporting note: drawn under the question it closes, not beside it
        else:
            out["fyi"].append(e)
    out["fyi"].extend(_trail_rows(trail or (), at))
    out["needs"].sort(key=_needs_key)
    out["steering"].sort(key=lambda e: (not e.get("bound"), str(e.get("bound") or "")))
    out["waiting"].sort(key=lambda e: str(e.get("closed_at") or e.get("at") or ""))
    out["fyi"].sort(key=lambda e: str(e.get("at") or ""), reverse=True)
    out["snoozed"].sort(key=lambda e: str(e.get("snoozed_until") or ""))
    return {**out, "count": len(out["needs"]), "fyi_n": len(out["fyi"])}


# -- app -------------------------------------------------------------------------------------------


# design §4.5a **Focus (exited / closed)** → **Resume** (TD-081 step 2; the plumbing is step 1,
# PR #282). What a one-press resume carries, and what it deliberately does not.
RESUME_CARRIES = ("name", "dir", "adapter", "profile", "role", "team", "project", "lane", "controllers")
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

    async def team_rows(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The definitions' rows for a delta's headers. The page reads the files on every load;
        the events stream re-renders every header on every delta, so it reads them at most once in
        `DEFS_TTL` seconds — a definition edited by hand shows on the next load, or within that."""
        now = time.monotonic()
        if defs_cache["org"] is None or now - defs_cache["at"] > DEFS_TTL:
            # off the loop: every open page shares it, and the read is a file per registered repo
            defs_cache.update(at=now, org=(await asyncio.to_thread(org_here))[0])
        return _aged(teamrun.rows(defs_cache["org"], sessions))

    async def group_heads(known: dict[str, dict[str, Any]]) -> list[dict[str, Any]] | None:
        """The team groups as the events stream ships them (design §4.5a **team groups**): per group
        its key, the member ids in order, and the header rendered by the same template the page
        uses — so the client moves cards between groups and swaps headers without composing any
        markup of its own. `None` means "flat grid", exactly as the page renders it."""
        fleet = list(known.values())
        groups = team_groups([view(s, fleet) for s in fleet], await team_rows(fleet))
        if groups is None:
            return None
        head = templates.get_template("group_head.html")
        return [
            {
                "team": g["team"],
                "manager": (g["manager"] or {}).get("id", ""),
                "live": g["live"],
                "ids": g["ids"],
                "html": head.render(g=g),
            }
            for g in groups
        ]

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
        vs = sorted((view(s, sessions, icons=icons) for s in sessions), key=lambda v: (v["rank"], v["name"]))
        # the needs-you badge is the same predicate the Inbox rows are (review of PR #251): a
        # record the Org counts and the Inbox did not list was the two pages disagreeing in public
        counts = {"needs-you": sum(1 for v in vs if state_kind(v) in NEEDS_YOU_ROWS)}
        counts.update({k: sum(1 for v in vs if v["state"] == k) for k in ("limited", "stalled?")})
        strip = teams_view(sessions)
        id_info = {} if agent_down else await identity_info()
        if entries or vs:
            secs = inbox_sections(
                entries,
                states=state_rows(
                    vs,
                    host_alarms=alarm_view(id_info.get("alarms")),
                    host=str(id_info.get("host") or host_name()),
                    identity_mode=str(id_info.get("mode") or ""),
                ),
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
            },
        )

    @app.get("/focus/{sid}", response_class=HTMLResponse)
    async def focus(request: Request, sid: str):
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
                "s": view(s, fleet, fleet_known=known, icons=await role_icons([s])),
                "host": host_name(),
                "active": "Org",
            },
        )

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
            worktree=wt,
            repo=dir.strip() if wt else None,
            # The Grants checkboxes were ticked from the preset when the page loaded and as the
            # Role changed (design §4.5a), so what is ticked is what was meant — including an
            # untick, which is a person deciding this session does not get the grant.
            capabilities=[g for g in canonical_grants(grant) if g in GRANTS],
            lane=refs or (list(preset.lane) if preset else []),
            role=preset.name if preset else "",
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
            await call("send", id=sid, text=WRAPUP_PROMPT)
        elif action == "mode":
            await call("set_mode", id=sid, unattended=bool(body.get("unattended")))
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
            warnings = " · ".join(e.plan.warnings)
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

    @app.get("/api/sessions/{sid}/inbox")
    async def api_inbox(sid: str):
        """design §4.5a Focus side panel **Inbox** (§4.10 "A bounded body"): bodies never ride the
        pushed record, so the panel fetches them here. No caller — a person's read, which sets no
        `read_at`, because a person is not the session. Each sender gets its name beside its id."""
        got = await call("inbox", id=sid)
        fleet = await call("list")
        names = {o.get("id"): o.get("name") or o.get("id") for o in fleet}
        for e in got["entries"]:
            e["from_name"] = "person" if e["from"] == "person" else names.get(e["from"], e["from"])
        return got

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

    async def person_states(fleet: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The session-state rows of the Inbox (design §4.5 screen 6, TD-069 step 2), from the
        fleet the request already read. Every host the home knows, exactly as the Org shows them —
        the same records and the same `view()` — plus this host's own identity alarms (§4.8a).

        Rendered server-side on the page's load *and* on its poll: a state that changed between
        polls is corrected by the next one, and nothing here has to ride the pushed stream to be
        no more than a few seconds behind the Org."""
        icons = await role_icons(fleet)
        views = [view(s, fleet, icons=icons) for s in fleet]
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
        at = datetime.now(UTC)
        for e in got["entries"]:
            e["from_name"] = "person" if e["from"] == "person" else names.get(e["from"], e["from"])
            e["from_open"] = e["from"] if e["from"] in names else ""
            _owing(e, records.get(e["from"]), at)
            e["age"] = _age(e.get("at"), at)  # the client keeps it ticking; this is what it opens on
            # §4.5 screen 6 *Layout* (TD-082): a duration is words from here, never a `…` the
            # client fills in — `left` for a `steer`'s bound, `until_words` for a snoozed entry,
            # whose row upgrades to the browser's own clock once the script runs.
            e["left"] = _left(e.get("bound"), at)
            e["until_words"] = _left(e.get("snoozed_until"), at)
        return got

    def inbox_html(sections: dict[str, Any]) -> dict[str, str]:
        """Each section's rows, rendered by the one template the page itself renders them with, so
        a poll replaces a section without the client composing any markup — the shape the events
        stream already uses for team headers. Jinja escapes every field, which is what keeps what a
        session wrote text and nothing else (TD-071 item 8)."""
        rows = templates.get_template("inbox_rows.html")
        return {k: rows.render(rows=sections[k], section=k) for k in INBOX_SECTIONS}

    @app.get("/inbox", response_class=HTMLResponse)
    async def inbox_page(request: Request):
        """design §4.5 screen 6 / §4.5a **Inbox page** (TD-069 steps 1 and 2): full width, the
        person inbox and the sessions' states in three sections, and the count that means *what is
        waiting on a person*. Board items are step 3 and join the same sections here."""
        agent_down = False
        try:
            got, states = await person_view()
        except HTTPException as e:
            if e.status_code != 503:
                raise
            got, states, agent_down = {"entries": []}, [], True  # the banner + Retry, never a bare 503
        sections = inbox_sections(
            got["entries"], states=states, trail=got.get("trail") or (), attention_snoozed=got.get("attention_snoozed")
        )
        return templates.TemplateResponse(
            request,
            "inbox.html",
            {
                "sections": sections,
                "person_needs": sections["count"],
                "person_fyi": sections["fyi_n"],
                "host": host_name(),
                "active": "Inbox",
                "agent_down": agent_down,
                "volatile": hosts.local_host().volatile,
                "usage": {},
            },
        )

    @app.get("/api/person/inbox")
    async def api_person_inbox():
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
                "html": {},
                "unread": None,
                "agent_down": True,
                "why": str(e.detail),
            }
        sections = inbox_sections(
            got["entries"], states=states, trail=got.get("trail") or (), attention_snoozed=got.get("attention_snoozed")
        )
        got["agent_down"] = False
        got["sections"] = {k: [e["id"] for e in sections[k]] for k in INBOX_SECTIONS}
        got["needs"] = sections["count"]
        # §4.10 *The Inbox is a queue*: FYI's own quiet number, **never added to the first** — the
        # first is *what needs you*. The page opens the section by itself when this is higher than
        # the browser last saw, which is what stops a folded FYI hiding mail nobody counted.
        got["fyi_n"] = sections["fyi_n"]
        got["snoozed_n"] = len(sections["snoozed"])
        got["html"] = inbox_html(sections)
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

    @app.get("/api/sessions")
    async def api_sessions():
        sessions = await call("list")
        icons = await role_icons(sessions)
        return [view(s, sessions, icons=icons) for s in sessions]

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
            async for ev in c.subscribe():
                if ev.get("event") == "session":
                    s = ev["session"]
                    known[s["id"]] = s
                    v = view(s, list(known.values()), icons=await role_icons([s]))
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
                                ov = view(other, list(known.values()), icons=await role_icons([other]))
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
                    await ws.send_text(json.dumps(ev))

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
            await pump(pty, send, recv, scroll)
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

    return app


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
