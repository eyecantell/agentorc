"""The agentorc web UI (design §4.5): server-rendered pages, one `/events` websocket per tab
pushing rendered cards, one `/term/<id>` websocket per open Focus terminal. Phase 1: the local
host only; `hosts.yml` and ssh transport arrive in phase 2.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agentorc import profiles as profiles_mod
from sessionorc import paths
from sessionorc.client import AgentError, AgentUnavailable, LocalClient
from sessionorc.models import STATE_RANK

from .pty_bridge import PtySession, attach_argv, pump

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

WRAPUP_PROMPT = (
    "agentorc: this session is being wrapped up. Stop starting new work now. Commit and push whatever "
    "is in flight, make sure the ledger and user_attention.md reflect any undone steps (ledger before "
    "idle), then stop."
)


def host_name() -> str:
    return os.environ.get("AGENTORC_HOST_NAME") or socket.gethostname().split(".")[0]


def vscode_url(directory: str) -> str:
    """`vscode://vscode-remote/ssh-remote+<host><path>` unless this host is marked local."""
    if os.environ.get("AGENTORC_LOCAL_HOST") == "1":
        return f"vscode://file{directory}"
    return f"vscode://vscode-remote/ssh-remote+{os.environ.get('AGENTORC_VSCODE_HOST') or host_name()}{directory}"


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


def view(s: dict[str, Any]) -> dict[str, Any]:
    """Everything a card or the Focus header needs, computed once."""
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
    if s.get("adapter") == "shell":
        d["profile_line"] = "shell"
    else:
        try:
            d["profile_line"] = profiles_mod.get(prof or None).label
        except (KeyError, ValueError):
            d["profile_line"] = f"{s.get('adapter')} · {prof or 'default'}"
    pend = s.get("pending") or {}
    d["deadline"] = pend.get("deadline") or ""
    d["ready"] = ready_to_close(s)
    return d


def ready_to_close(s: dict[str, Any]) -> list[tuple[str, bool]]:
    """Phase 1 subset of the checklist (design §4.2): tree clean, branch pushed, no subagents."""
    git = s.get("git") or {}
    checks = []
    if s.get("dir") and git:
        checks.append(("tree clean", git.get("dirty", 0) == 0))
        checks.append(("branch pushed", git.get("ahead", 0) == 0 and bool(git.get("upstream"))))
    checks.append(("no subagents running", (s.get("subagents") or 0) == 0))
    return checks


# -- app -------------------------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(title="agentorc")
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    async def call(method: str, **params: Any) -> Any:
        try:
            async with LocalClient() as c:
                return await c.call(method, **params)
        except AgentUnavailable as e:
            raise HTTPException(503, f"host agent unreachable: {e}") from e
        except AgentError as e:
            raise HTTPException(400, str(e)) from e

    def render_card(request: Request, s: dict[str, Any]) -> str:
        return templates.get_template("card.html").render(request=request, s=view(s))

    @app.get("/", response_class=HTMLResponse)
    async def herd(request: Request):
        sessions = await call("list")
        vs = sorted((view(s) for s in sessions), key=lambda v: (v["rank"], v["name"]))
        counts = {k: sum(1 for v in vs if v["state"] == k) for k in ("needs-you", "limited", "stalled?")}
        return templates.TemplateResponse(
            request, "herd.html", {"sessions": vs, "counts": counts, "host": host_name(), "active": "Herd"}
        )

    @app.get("/focus/{sid}", response_class=HTMLResponse)
    async def focus(request: Request, sid: str):
        s = await call("get", id=sid)
        return templates.TemplateResponse(request, "focus.html", {"s": view(s), "host": host_name(), "active": "Herd"})

    @app.get("/new", response_class=HTMLResponse)
    async def new_form(request: Request, dir: str = "", adapter: str = "claude-code", resume: str = ""):
        profs, default = profiles_mod.load()
        recent = await call("recent_dirs")
        adapters = await call("adapters")
        return templates.TemplateResponse(
            request,
            "new.html",
            {
                "host": host_name(),
                "active": "Herd",
                "profiles": profs,
                "default_profile": default,
                "recent": recent,
                "adapters": adapters,
                "prefill": {"dir": dir, "adapter": adapter, "resume": resume},
            },
        )

    @app.post("/new")
    async def new_submit(
        name: str = Form(...),
        dir: str = Form(...),
        adapter: str = Form("claude-code"),
        profile: str = Form(""),
        prompt: str = Form(""),
        resume: str = Form(""),
        unattended: str = Form(""),
    ):
        s = await call(
            "create",
            name=name.strip() or "session",
            dir=dir.strip(),
            adapter=adapter,
            profile=profile,
            prompt=prompt.strip() or None,
            resume=resume.strip() or None,
            unattended=unattended == "on",
        )
        return RedirectResponse(f"/focus/{s['id']}", status_code=303)

    @app.post("/shell")
    async def shell(dir: str = Form(...), name: str = Form("shell")):
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
            return JSONResponse({"ok": True, "id": new["id"]})
        elif action == "remove":
            await call("remove", id=sid)
        else:
            raise HTTPException(404, f"no action {action}")
        return JSONResponse({"ok": True})

    @app.get("/api/sessions")
    async def api_sessions():
        return [view(s) for s in await call("list")]

    # -- live state ------------------------------------------------------------------------------

    @app.websocket("/events")
    async def events(ws: WebSocket):
        await ws.accept()
        try:
            async with LocalClient() as c:
                async for ev in c.subscribe():
                    if ev.get("event") == "session":
                        s = ev["session"]
                        await ws.send_text(
                            json.dumps(
                                {
                                    "event": "session",
                                    "id": s["id"],
                                    "state": s["state"],
                                    "rank": STATE_RANK.get(s["state"], 9),
                                    "html": render_card(ws, s),
                                    "session": view(s),
                                }  # noqa: E501
                            )
                        )
                    elif ev.get("event") == "gone":
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
    async def term(ws: WebSocket, sid: str, cols: int = 120, rows: int = 32):
        await ws.accept()
        try:
            await call("get", id=sid)
        except HTTPException as e:
            await ws.send_bytes(f"\r\n[agentorc] {e.detail}\r\n".encode())
            await ws.close()
            return
        pty = PtySession(attach_argv(sid, socket_name=os.environ.get("AGENTORC_TMUX_SOCKET")), cols=cols, rows=rows)

        async def send(data: bytes) -> None:
            await ws.send_bytes(data)

        async def recv() -> Any:
            try:
                msg = await ws.receive()
            except WebSocketDisconnect:
                return None
            if msg.get("type") == "websocket.disconnect":
                return None
            if msg.get("bytes") is not None:
                return msg["bytes"]
            text = msg.get("text") or ""
            if text.startswith("{"):
                with contextlib.suppress(ValueError):
                    return json.loads(text)
            return text

        try:
            await pump(pty, send, recv)
        finally:
            with contextlib.suppress(Exception):
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
    uvicorn.run("agentorc.ui.app:app", host=args.bind, port=args.port, log_level="info", ws_ping_interval=20)
    return 0


async def _wait_agent() -> bool:
    for _ in range(20):
        if paths.socket_path().exists():
            return True
        await asyncio.sleep(0.25)
    return False
