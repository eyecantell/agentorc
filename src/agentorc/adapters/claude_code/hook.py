"""`agentorc-hook`: the one command every Claude Code hook event runs (design §4.2).

Reads the hook payload on stdin, finds its agentorc session in `AGENTORC_SESSION`, and tells the
host agent what happened. A `PermissionRequest` blocks until the person answers from the UI or the
wait elapses, then prints the decision (or nothing, letting Claude Code draw its own dialog).
Every other event is fire-and-forget: socket first, `events/<session>.jsonl` if the agent is down.

Always exits 0. A hook that fails would break the session it is watching.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
from typing import Any

from sessionorc import paths
from sessionorc.store import EventQueue

STATE_EVENTS = {
    "SessionStart": "working",
    "UserPromptSubmit": "working",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "Stop": "idle",
}
# SessionEnd reasons after which the process is still alive in the pane (a SessionStart follows).
SESSION_END_STILL_RUNNING = {"clear", "resume"}

NOTIFICATION_KINDS = {
    "permission_prompt": "permission",  # the terminal dialog is up (our hook fell through, or was absent)
    "idle_prompt": "prompt",
    "elicitation_dialog": "question",
    "elicitation_url_dialog": "question",
    "agent_needs_input": "question",
}


def translate(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Hook payload → agent `hook` params (without the session). None = nothing to report."""
    ev = payload.get("hook_event_name", "")
    out: dict[str, Any] = {}
    if sid := payload.get("session_id"):
        out["adapter_id"] = sid
    if ev == "PermissionRequest":
        tool = payload.get("tool_name", "?")
        return {
            **out,
            "kind": "permission",
            "text": f"{tool}: {describe_tool_input(tool, payload.get('tool_input') or {})}",
            "tool_use_id": payload.get("tool_use_id"),
        }
    if ev == "PreToolUse" and payload.get("tool_name") == "AskUserQuestion":
        qs = (payload.get("tool_input") or {}).get("questions") or []
        text = qs[0].get("question", "") if qs and isinstance(qs[0], dict) else "question"
        return {**out, "state": "needs-you", "pending": {"kind": "question", "text": text}}
    if ev == "Notification":
        kind = NOTIFICATION_KINDS.get(payload.get("notification_type", ""))
        if kind is None:
            return None
        if kind == "permission":
            # Our PermissionRequest hook already reported this one while it waited; the dialog
            # now lives in the terminal, so it is a question for the Focus screen (design §4.2).
            kind = "question"
        return {**out, "state": "needs-you", "pending": {"kind": kind, "text": payload.get("message", "")}}
    if ev == "SessionEnd":
        reason = payload.get("reason") or payload.get("how_ended") or ""
        if reason in SESSION_END_STILL_RUNNING:
            return None  # /clear or an in-session /resume: same process, new transcript coming
        return {**out, "state": "exited", "pending": None}
    if ev == "SubagentStart":
        return {**out, "subagent_delta": 1}
    if ev == "SubagentStop":
        return {**out, "subagent_delta": -1}
    if ev in STATE_EVENTS:
        return {**out, "state": STATE_EVENTS[ev], "pending": None}
    return None


def describe_tool_input(tool: str, ti: dict[str, Any]) -> str:
    for key in ("command", "file_path", "path", "url", "pattern", "description", "prompt"):
        if v := ti.get(key):
            return str(v)[:200]
    return json.dumps(ti)[:200] if ti else ""


def call_agent(params: dict[str, Any], timeout: float | None) -> Any:
    """One request over the socket; raises on any transport problem."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect(str(paths.socket_path()))
        s.settimeout(timeout)
        s.sendall((json.dumps({"id": 1, "method": "hook", "params": params}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    resp = json.loads(buf or b"{}")
    if "error" in resp:
        raise RuntimeError(resp["error"])
    return resp.get("result")


def main() -> int:
    session = os.environ.get("AGENTORC_SESSION")
    if not session:
        return 0  # not an agentorc session; the hook layer is only ever passed to ours, but be safe
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    params = translate(payload)
    if params is None:
        return 0
    params["session"] = session
    if params.get("kind") == "permission":
        wait = float(os.environ.get("AGENTORC_PERMISSION_WAIT", "600"))
        params["wait_seconds"] = wait
        try:
            decision = call_agent(params, timeout=wait + 10)
        except Exception:  # noqa: BLE001 — agent down: let Claude Code ask in the terminal
            return 0
        if decision and decision.get("behavior") in ("allow", "deny"):
            out = {"behavior": decision["behavior"]}
            if decision.get("reason"):
                out["reason"] = decision["reason"]
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": out}}))
        return 0
    try:
        call_agent(params, timeout=3)
    except Exception:  # noqa: BLE001
        with contextlib.suppress(OSError):
            EventQueue().append(session, {k: v for k, v in params.items() if k != "session"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
