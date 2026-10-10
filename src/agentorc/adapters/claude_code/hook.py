"""`agentorc-hook`: the one command every Claude Code hook event runs (design §4.2).

Reads the hook payload on stdin, finds its agentorc session in `AGENTORC_SESSION`, and tells the
host agent what happened. A `PermissionRequest` blocks until the person answers from the UI or the
wait elapses, then prints the decision (or nothing, letting Claude Code draw its own dialog).
Every other event is fire-and-forget: socket first, `events/<session>.jsonl` if the agent is down.
An error in the reply is the agent answering — a refusal (design §4.8a) or a bug — and is never
queued: the tick applies the queue unjudged, so a queued refusal would be applied anyway (TD-115).
A `PreToolUse` of `Bash` whose command the kill guard refuses (`guard.py`, design §4.3) prints a `deny`
with its reason first, the hook's own decision, and is reported as any `PreToolUse` is.

With `--statusline` it is the session's status line instead (design §4.4 *A report*, TD-233): it
reports the account's limits the tool hands it as `usage_report`, keeps the context window and
tokens it reports for the adapter's `context` (TD-295), then runs the status line it
displaced and prints what that prints.

Always exits 0. A hook that fails would break the session it is watching.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentorc.adapters.claude_code import (
    AT_COMPOSER_ENV,
    STATUSLINE_FLAG,
    STATUSLINE_REFRESH,
    context_file,
    context_report,
    displaced_status_line,
    usage_report,
)
from agentorc.adapters.claude_code.guard import refuse
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
# SessionStart sources that are not a start: a compaction ends by firing SessionStart again, and a
# manual `/compact` fires nothing after it, so reading it as `working` left an idle session reading
# `stalled?` at STALL_AFTER (TD-090). An auto-compaction mid-turn is already `working`.
SESSION_START_NOT_A_START = {"compact"}
# SessionStart sources that land at the composer with no turn to follow: `claude --resume` prints the
# old conversation and waits, so no Stop ever reports `idle` and `working` would read `stalled?` at
# STALL_AFTER, never rung (TD-155). A prompt given with the resume reports `working` through its own
# UserPromptSubmit, which fires after this.
SESSION_START_AT_THE_COMPOSER = {"resume"}
# A launch with no prompt lands at the composer too: the launch says so in `AT_COMPOSER_ENV` (TD-283).

# Tool events that report `working` without being a turn's start. Each carries its name in `event`
# (`PreToolUse:Bash`), which the host agent logs when one wakes a hook-confirmed `idle` session: an
# idle grinder was turned `working` four seconds after its Stop, with no turn behind it, and read
# `stalled?` for 13 hours unrung (TD-201). The line names the event the next time it happens.
TOOL_EVENTS = {"PreToolUse", "PostToolUse"}
# The tools whose `PostToolUse` names the file they edited, and the key it is under (design §4.2,
# TD-527): the record keeps it as `files`, the Focus Session card's recent files. A read is no edit.
EDIT_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}

# idle_prompt (Claude idle for a minute) is deliberately absent: an idle session waiting for you is
# `idle`, the normal state, not an alert (design §4.2, first-use finding 2026-09-06).
NOTIFICATION_KINDS = {
    "permission_prompt": "permission",  # the terminal dialog is up (our hook fell through, or was absent)
    "elicitation_dialog": "question",
    "elicitation_url_dialog": "question",
    "agent_needs_input": "question",
}


def translate(payload: dict[str, Any], *, at_composer: bool = False) -> dict[str, Any] | None:
    """Hook payload → agent `hook` params (without the session). None = nothing to report.
    `at_composer`: the launch gave no prompt (`AT_COMPOSER_ENV`), so its `startup` is `idle`."""
    ev = payload.get("hook_event_name", "")
    out: dict[str, Any] = {}
    if sid := payload.get("session_id"):
        out["adapter_id"] = sid
    # Only SessionStart carries `model`, and not always (TD-031); PostModelSwitch is how a `/model`
    # mid-session reports itself. Read both generically: an event without one says nothing about it.
    if model := payload.get("model"):
        out["model"] = model
    if ev == "PostModelSwitch":
        return {**out, "model": payload["to_model"]} if payload.get("to_model") else None
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
    if ev in TOOL_EVENTS and payload.get("agent_id"):
        # A subagent's tool (the tool sets `agent_id` only inside one) says nothing about the main
        # composer: a background agent runs on after its caller's Stop, and read as `working` it held
        # an idle session unrung; mid-turn the session is `working` anyway, and a `PostToolUse` of
        # its own no longer clears the main thread's `needs-you` (TD-201).
        return out or None
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
        # the reason rides on the event as the record's `ended.reason` (§4.2, TD-490)
        return {**out, "state": "exited", "pending": None, **({"reason": reason} if reason else {})}
    if ev == "SessionStart" and payload.get("source") in SESSION_START_NOT_A_START:
        return out or None  # the session id and model still count; the state is what it was
    if ev == "SessionStart" and (
        payload.get("source") in SESSION_START_AT_THE_COMPOSER or (at_composer and payload.get("source") == "startup")
    ):
        return {**out, "state": "idle", "pending": None}
    if ev == "SubagentStart":
        return {**out, "subagent_delta": 1}
    if ev == "SubagentStop":
        return {**out, "subagent_delta": -1}
    if ev in TOOL_EVENTS:
        tool = payload.get("tool_name")
        if ev == "PostToolUse" and (key := EDIT_TOOLS.get(tool or "")):
            path = (payload.get("tool_input") or {}).get(key)
            if isinstance(path, str) and path:
                out["file"] = path
        return {**out, "state": STATE_EVENTS[ev], "pending": None, "event": f"{ev}:{tool}" if tool else ev}
    if ev == "UserPromptSubmit":
        # `prompt`: a prompt went in, which is what the host agent's typed brief waits on (§4.1 *No
        # prose in the argv*, TD-339) — a word, not the prompt's text
        return {**out, "state": "working", "pending": None, "prompt": True}
    if ev in STATE_EVENTS:
        return {**out, "state": STATE_EVENTS[ev], "pending": None}
    return None


def describe_tool_input(tool: str, ti: dict[str, Any]) -> str:
    for key in ("command", "file_path", "path", "url", "pattern", "description", "prompt"):
        if v := ti.get(key):
            return str(v)[:200]
    return json.dumps(ti)[:200] if ti else ""


def refused_command(payload: dict[str, Any]) -> str | None:
    """The kill guard's reason for a `PreToolUse` of `Bash` (design §4.3), None for anything else."""
    if payload.get("hook_event_name") != "PreToolUse" or payload.get("tool_name") != "Bash":
        return None
    command = (payload.get("tool_input") or {}).get("command")
    return refuse(command) if isinstance(command, str) else None


def deny(reason: str) -> dict[str, Any]:
    """The `PreToolUse` decision that refuses the call, with the reason the session reads."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


class Refused(Exception):
    """The host agent answered with an error: it is up, and the event is not to be queued."""


def call_agent(params: dict[str, Any], timeout: float | None, method: str = "hook", caller: str | None = None) -> Any:
    """One request over the socket; raises `Refused` on an error reply, and anything else on a
    transport problem. `caller` goes on the envelope, where a method that answers for its caller
    reads it (`usage_report`); a `hook` names its session in `params`."""
    req: dict[str, Any] = {"id": 1, "method": method, "params": params}
    if caller:
        req["caller"] = caller
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout if method == "usage_report" else 5.0)  # a report waits no longer to connect
        s.connect(str(paths.socket_path()))
        s.settimeout(timeout)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    resp = json.loads(buf or b"{}")
    if "error" in resp:
        raise Refused(resp["error"])
    return resp.get("result")


# The status line's report (design §4.4 *A report*): sent with this timeout and never queued, since
# the tool cancels a status line still running when the next is due and a late report reads as new.
REPORT_TIMEOUT = 1.0
# The status line it displaced gets this long; the tool cancels ours past its own bound anyway.
CHAINED_TIMEOUT = 5.0


def last_report_file(session: str) -> Path:
    """What this session's status line last sent: one small file per session under the home."""
    return paths.home() / "statusline" / f"{session}.json"


def report_due(rep: dict[str, Any], last: dict[str, Any] | None, now: float) -> dict[str, Any] | None:
    """The `usage_report` params to send for `rep`, or None when nothing is owed: a report goes when a
    number or a reset changed, or `STATUSLINE_REFRESH` seconds passed, never on every redraw.
    `fresh` says the session had a response since the last report — its running total of API time
    grew — so a redraw that repeats what the tool last said moves no reading's age."""
    if not isinstance(last, dict):
        last = {}
    same = last.get("windows") == rep["windows"]
    sent = last.get("sent")
    if same and isinstance(sent, int | float) and 0 <= now - sent < STATUSLINE_REFRESH:
        return None
    work, was = rep.get("work"), last.get("work")
    if not isinstance(was, int | float) or isinstance(was, bool) or last.get("sid") != rep.get("sid"):
        was = 0  # a new process under the same name counts its API time from nothing again
    elif work is not None and work < was:
        was = 0  # the same tool session resumed in a new process: its running total started over
    # with no running total to read, only a changed number says anything happened
    fresh = not same if work is None else work > was
    return {"windows": rep["windows"], "fresh": fresh}


def report_usage(session: str, payload: dict[str, Any], now: float) -> None:
    """Send the payload's limits as `usage_report` when one is owed, and remember what was sent.
    Nothing is remembered when the host agent was not reached, so the next redraw tries again."""
    rep = usage_report(payload)
    if rep is None:
        return
    f = last_report_file(session)
    try:
        last = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = None
    params = report_due(rep, last, now)
    if params is None:
        return
    call_agent(params, timeout=REPORT_TIMEOUT, method="usage_report", caller=session)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.stem}.{os.getpid()}.tmp")  # two status lines of one session may overlap
    tmp.write_text(json.dumps({**rep, "sent": now}), encoding="utf-8")
    tmp.replace(f)


def keep_context(payload: dict[str, Any], now: float) -> None:
    """Keep the window and tokens the tool reports for the adapter's `context` (TD-295), stamped with
    the redraw that first saw them; a redraw that repeats them writes nothing."""
    rep = context_report(payload)
    if rep is None:
        return
    f = context_file(rep["sid"])
    try:
        last = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = None
    if isinstance(last, dict) and (last.get("window"), last.get("tokens")) == (rep["window"], rep["tokens"]):
        return
    at = datetime.fromtimestamp(now, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.stem}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"window": rep["window"], "tokens": rep["tokens"], "at": at}), encoding="utf-8")
    tmp.replace(f)


def chained_output(raw: str, payload: dict[str, Any]) -> str:
    """Run the status line the launch's layer displaced with the same stdin, in the session's
    directory, and return what it printed ("" when there is none or it failed)."""
    ws = payload.get("workspace") if isinstance(payload.get("workspace"), dict) else {}
    cwd = Path(str(ws.get("project_dir") or payload.get("cwd") or ws.get("current_dir") or os.getcwd()))
    cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
    line = displaced_status_line(cwd, cfg)
    if line is None:
        return ""
    try:
        done = subprocess.run(  # noqa: S602 — the person's own status line, as the tool would run it
            line["command"],
            shell=True,
            input=raw,
            capture_output=True,
            text=True,
            cwd=cwd if cwd.is_dir() else None,
            timeout=CHAINED_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    return done.stdout


def statusline() -> int:
    """`agentorc-hook --statusline`: report, then print the person's own status line. It never
    fails the status line — any error in the report is swallowed and the chained command still runs."""
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    # The person's line first, so a host agent slow to answer never delays what the pane shows.
    with contextlib.suppress(Exception):
        if out := chained_output(raw, payload):
            sys.stdout.write(out)
            sys.stdout.flush()
    if session := os.environ.get("AGENTORC_SESSION"):
        with contextlib.suppress(Exception):
            report_usage(session, payload, time.time())
        with contextlib.suppress(Exception):
            keep_context(payload, time.time())
    return 0


def main() -> int:
    if STATUSLINE_FLAG in sys.argv[1:]:
        return statusline()
    session = os.environ.get("AGENTORC_SESSION")
    if not session:
        return 0  # not an agentorc session; the hook layer is only ever passed to ours, but be safe
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if isinstance(payload, dict) and (reason := refused_command(payload)):
        # the hook's own decision, before the host agent is asked anything: one it cannot reach still
        # refuses, and the event below still reports `working` (design §4.3 *A kill the guard refuses*)
        print(json.dumps(deny(reason)))
        sys.stdout.flush()
    params = translate(payload, at_composer=os.environ.get(AT_COMPOSER_ENV) == "1")
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
    except Refused as e:
        print(f"agentorc-hook: {session}: the host agent refused the event: {e}", file=sys.stderr)
    except Exception:  # noqa: BLE001
        with contextlib.suppress(OSError):
            # `at`: when it happened, so the drain can tell it from a newer event that got through
            # live while this one waited (TD-169)
            EventQueue().append(session, {**{k: v for k, v in params.items() if k != "session"}, "at": time.time()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
