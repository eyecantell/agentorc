"""The agentorc web UI (design §4.5): server-rendered pages, one `/events` websocket per tab
pushing rendered cards, one `/term/<id>` websocket per open Focus terminal. Phase 1: the local
host only, from `hosts.yml`'s `local` entry; ssh transport arrives in phase 2.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agentorc import profiles as profiles_mod
from agentorc import repoconfig, teams
from sessionorc import hosts, naming, paths
from sessionorc.adapters import short_model
from sessionorc.client import AgentError, AgentUnavailable, LocalClient
from sessionorc.models import GRANTS, STATE_RANK, report_head, report_line

from .pty_bridge import PtySession, attach_argv, pump, scroll_argv

HERE = Path(__file__).parent
log = logging.getLogger("uvicorn.error")  # the logger uvicorn already shows on the console
templates = Jinja2Templates(directory=str(HERE / "templates"))

# The New session form's `controller` field when nothing is ticked: an empty list means nobody may
# act on the session, which is design §4.8's explicit default. Module-level so the signature keeps
# no mutable default and no call in its arguments.
NO_CONTROLLERS: list[str] = []


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


def _age(iso: str | None, now: datetime) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    return f"{secs // 86400}d"


def view(s: dict[str, Any], fleet: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Everything a card or the Focus header needs, computed once. `fleet` is the other records,
    needed only for the membership directions (design §4.8): who controls this session, and — for
    an orchestrator — which sessions it controls. Without it both come back empty, which is what a
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
    d["host"] = host_name()
    d["vscode"] = vscode_url(s["dir"]) if s.get("dir") else ""
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
    if git.get("ahead"):
        flags.append(f"{git['ahead']} unpushed")
    d["flag"] = " · ".join(flags) if state in ("idle", "exited", "stalled?", "needs-you") and flags else ""
    prof = s.get("profile") or ""
    if s.get("external"):
        d["profile_line"] = f"{s.get('adapter')} · started outside agentorc (read-only)"
    elif s.get("adapter") == "shell":
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
    pend = s.get("pending") or {}
    d["deadline"] = pend.get("deadline") or ""
    d["ready"] = ready_to_close(s)
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
    d["is_orchestrator"] = "orchestrate" in (s.get("capabilities") or [])
    return d


def ready_to_close(s: dict[str, Any]) -> list[tuple[str, bool]]:
    """Phase 1 subset of the checklist (design §4.2): tree clean, branch pushed, no subagents.
    A registry-only card has nothing to close: no checks, so no Close button (TD-010 a)."""
    if s.get("external"):
        return []
    git = s.get("git") or {}
    checks = []
    if s.get("dir") and git:
        checks.append(("tree clean", git.get("dirty", 0) == 0))
        checks.append(("branch pushed", git.get("ahead", 0) == 0 and bool(git.get("upstream"))))
    checks.append(("no subagents running", (s.get("subagents") or 0) == 0))
    return checks


# -- app -------------------------------------------------------------------------------------------


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

    @app.get("/", response_class=HTMLResponse)
    async def team(request: Request):
        # An unreachable agent still gets a page: the banner + Retry are the recovery path
        # (design §4.5 unreachable hosts), never a bare 503.
        agent_down = False
        usage: dict[str, Any] = {}
        try:
            sessions = await call("list")
            usage = await call("usage")
        except HTTPException as e:
            if e.status_code != 503:
                raise
            sessions, agent_down = [], True
        vs = sorted((view(s, sessions) for s in sessions), key=lambda v: (v["rank"], v["name"]))
        counts = {k: sum(1 for v in vs if v["state"] == k) for k in ("needs-you", "limited", "stalled?")}
        return templates.TemplateResponse(
            request,
            "team.html",
            {
                "sessions": vs,
                "counts": counts,
                "host": host_name(),
                "active": "Team",
                "agent_down": agent_down,
                "volatile": hosts.local_host().volatile,
                "usage": usage,
            },
        )

    @app.get("/focus/{sid}", response_class=HTMLResponse)
    async def focus(request: Request, sid: str):
        try:
            s = await call("seen", id=sid)  # opening Focus is the "seen" (TD-017); returns the record
        except HTTPException as e:
            if e.status_code == 503:
                return RedirectResponse("/", status_code=303)  # the Team shows the down banner
            raise
        try:
            fleet = await call("list")
        except HTTPException:  # the record we already have still renders; membership just empties
            fleet = [s]
        return templates.TemplateResponse(
            request, "focus.html", {"s": view(s, fleet), "host": host_name(), "active": "Team"}
        )

    @app.get("/new", response_class=HTMLResponse)
    async def new_form(request: Request, dir: str = "", adapter: str = "claude-code", resume: str = ""):
        profs, default = profiles_mod.load()
        # registered repos (design §5: the dev-cadence registry, `repos_registry` in hosts.yml) first,
        # then recent directories; phase 1 reads the local host's file directly
        repos = hosts.local_host().repos()
        recent = repos + [d for d in await call("recent_dirs") if d not in repos]
        adapters = await call("adapters")
        # design §4.5a New session **Controllers** picker (§4.8): the candidates are the sessions
        # holding `orchestrate` — nothing else could act on the new session anyway.
        orchestrators = [
            {"id": o["id"], "name": o.get("name") or o["id"]}
            for o in await call("list")
            if "orchestrate" in (o.get("capabilities") or []) and o.get("state") not in ("closed", "exited")
        ]
        # design §4.5a New session **Role** preset: the built-ins, plus what the prefilled directory's
        # repo redefines; `/api/roles` refreshes the list as the directory is typed (TD-040 step a).
        roles = _roles_for(dir)
        return templates.TemplateResponse(
            request,
            "new.html",
            {
                "host": host_name(),
                "active": "Team",
                "profiles": profs,
                "default_profile": default,
                "recent": recent,
                "adapters": adapters,
                "orchestrators": orchestrators,
                "roles": roles["roles"],
                "default_controllers": roles.get("controllers") or [],
                "prefill": {"dir": dir, "adapter": adapter, "resume": resume},
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
        controller: Annotated[list[str], Form()] = NO_CONTROLLERS,
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
        s = await call(
            "create",
            name=name.strip() or "session",
            dir=dir.strip(),
            adapter=adapter,
            profile=profile or (preset.profile if preset else None) or "",
            prompt=prompt.strip() or brief,
            resume=resume.strip() or None,
            unattended=unattended == "on",
            worktree=wt,
            repo=dir.strip() if wt else None,
            capabilities=list(preset.grants) if preset else [],
            lane=refs or (list(preset.lane) if preset else []),
            role=preset.name if preset else "",
            ledger=ledger,
            controllers=[c for c in controller if c.strip()],
        )
        return RedirectResponse(f"/focus/{s['id']}", status_code=303)

    @app.post("/shell")
    async def shell(dir: str = Form(...), name: str = Form("")):  # unnamed: the agent names it (TD-030)
        s = await call("create", name=name, dir=dir, adapter="shell")
        return RedirectResponse(f"/focus/{s['id']}", status_code=303)

    # -- actions (every control in design §4.5a that exists in phase 1) --------------------------

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

    @app.get("/api/sessions")
    async def api_sessions():
        sessions = await call("list")
        return [view(s, sessions) for s in sessions]

    # -- live state ------------------------------------------------------------------------------

    @app.websocket("/events")
    async def events(ws: WebSocket):
        await ws.accept()
        try:
            async with LocalClient() as c:
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
                        v = view(s, list(known.values()))
                        await ws.send_text(
                            json.dumps(
                                {
                                    "event": "session",
                                    "id": s["id"],
                                    "state": s["state"],
                                    "rank": v["rank"],  # the view's: unseen idle sorts above idle
                                    "html": render_card(v),
                                    "session": v,
                                }
                            )
                        )
                    elif ev.get("event") in ("gone", "usage"):
                        if ev.get("event") == "gone":
                            # only a `gone` names a session; a `usage` event carries a profile, and
                            # popping on it would one day evict a live session by coincidence
                            went = str(ev.get("id") or "")
                            known.pop(went, None)
                            await ws.send_text(json.dumps(ev))
                            # A card's *under* chip names another record, so the session that went
                            # is not the only card now out of date: every card listing it as a
                            # controller has to be redrawn, or it keeps naming and linking to a
                            # session that is gone until the page is reloaded (review 2026-09-13).
                            for other in list(known.values()):
                                if went in (other.get("controllers") or []):
                                    ov = view(other, list(known.values()))
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
        if s.get("state") == "closed" or not s.get("pane", True):  # no pane to attach (TD-023)
            await ws.send_bytes(b"\r\n[agentorc] this session's pane is gone (see the banner).\r\n")
            await ws.close(code=4404)
            return
        sock = os.environ.get("AGENTORC_TMUX_SOCKET")
        try:
            pty = PtySession(attach_argv(sid, socket_name=sock), cols=cols, rows=rows)
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
                argv = scroll_argv(sid, direction, socket_name=sock)
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
