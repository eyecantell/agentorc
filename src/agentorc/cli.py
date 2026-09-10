"""`agentorc` / `ao`: a thin client of the host agent (design §4.7). Never touches tmux itself."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sessionorc.client import AgentError, AgentUnavailable, call_sync
from sessionorc.models import STATE_RANK
from sessionorc.tmux import attach_argv


def _age(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "?"
    secs = int((datetime.now(UTC) - dt).total_seconds())
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def emit(args: argparse.Namespace, result: Any, prose: Callable[[], None]) -> int:
    """`--json` (TD-018, design §4.7): print the RPC result — the ids the next call needs — and
    nothing else on stdout; otherwise the human line(s)."""
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        prose()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    sessions = call_sync("list")
    if args.json:
        print(json.dumps(sessions, indent=1))
        return 0
    if not sessions:
        print("no sessions")
        return 0
    sessions.sort(key=lambda s: (STATE_RANK.get(s["state"], 9), s["name"]))
    w = max(len(s["id"]) for s in sessions)
    for s in sessions:
        conf = "" if s["confidence"] == "hook" else " ~"
        pend = f"  ← {s['pending']['kind']}: {s['pending']['text']}" if s.get("pending") else ""
        mode = " [unattended]" if s.get("unattended") else ""
        print(f"{s['id']:<{w}}  {s['state']:<10}{conf:<3} {_age(s['since']):>4}  {s['adapter']}{mode}{pend}")
        if args.verbose and s.get("tail"):
            for line in s["tail"][-3:]:
                print(f"{'':<{w}}      │ {line}")
    return 0


def _attach(args: argparse.Namespace, sid: str, result: Any | None = None) -> int:
    """`ao focus` / `--attach`: exec `tmux attach` on the session (the terminal equivalent of the
    Focus screen; TD-010 b). Under `--json` nothing is exec'd: the argv is printed for the caller."""
    argv = attach_argv(sid, socket_name=os.environ.get("AGENTORC_TMUX_SOCKET"))
    if args.json:
        print(json.dumps({**(result or {"id": sid}), "attach": argv}, indent=1))
        return 0
    os.execvp(argv[0], argv)
    return 0  # not reached


def cmd_focus(args: argparse.Namespace) -> int:
    s = call_sync("get", id=args.id)  # a clear error for an unknown id, not tmux's
    if s["state"] in ("closed",):
        return fail(args, f"{args.id} is closed; its pane is gone", 1)
    return _attach(args, args.id, s)


def cmd_new(args: argparse.Namespace) -> int:
    s = call_sync(
        "create",
        name=args.name,
        dir=args.dir or os.getcwd(),
        adapter=args.adapter,
        profile=args.profile or "",
        repo=args.repo or (args.dir or os.getcwd() if args.worktree else None),
        worktree=args.worktree,
        unattended=args.unattended,
        resume=args.resume,
        prompt=args.prompt,
    )
    if getattr(args, "attach", False):
        if not args.json:
            print(f"{s['id']}  ({s['adapter']}, {s['dir']})")
        return _attach(args, s["id"], s)
    return emit(args, s, lambda: print(f"{s['id']}  ({s['adapter']}, {s['dir']})\nattach: tmux attach -t {s['id']}"))


def cmd_shell(args: argparse.Namespace) -> int:
    args.adapter, args.profile, args.repo, args.unattended, args.resume, args.prompt = (
        "shell",
        "",
        None,
        False,
        None,
        None,
    )
    args.worktree = None
    args.name = args.name or "shell"
    return cmd_new(args)


def cmd_kill(args: argparse.Namespace) -> int:
    s = call_sync("kill", id=args.id)
    return emit(args, s, lambda: print(f"killed {args.id}"))


def cmd_close(args: argparse.Namespace) -> int:
    s = call_sync("close", id=args.id)
    return emit(args, s, lambda: print(f"closed {args.id}"))


def cmd_send(args: argparse.Namespace) -> int:
    text = " ".join(args.text) if args.text else sys.stdin.read()
    s = call_sync("send", id=args.id, text=text, wait=args.wait, timeout=args.timeout)
    if s:  # --wait: the settled record
        pend = f"  ← {s['pending']['kind']}: {s['pending']['text']}" if s.get("pending") else ""
        return emit(args, s, lambda: print(f"{s['id']}: {s['state']}{pend}"))
    return emit(args, {"ok": True, "id": args.id}, lambda: None)  # without --wait the RPC returns nothing


def cmd_keys(args: argparse.Namespace) -> int:
    """Raw tmux key names into the pane (Down, Enter, Escape, C-c, 1 …) — for dialogs the
    terminal owns when no browser is open. Not a menu-answering API: design §9 invariant 6."""
    call_sync("keys", id=args.id, keys=args.keys)
    return emit(args, {"ok": True, "id": args.id}, lambda: None)


def cmd_tail(args: argparse.Namespace) -> int:
    lines = call_sync("tail", id=args.id, lines=args.lines)
    return emit(args, lines, lambda: print("\n".join(lines)) if lines else None)


def cmd_mode(args: argparse.Namespace) -> int:
    s = call_sync("set_mode", id=args.id, unattended=args.mode == "unattended")
    return emit(args, s, lambda: print(f"{s['id']}: {'unattended' if s['unattended'] else 'interactive'}"))


def cmd_decide(args: argparse.Namespace) -> int:
    s = call_sync("get", id=args.id)
    pend = s.get("pending") or {}
    if pend.get("kind") != "permission" or not pend.get("tool_use_id"):
        msg = f"{args.id} has no pending permission"
        return fail(args, msg, 1, prose=msg)  # no "error:" prefix: the line main printed before --json
    call_sync("decide", id=args.id, tool_use_id=pend["tool_use_id"], behavior=args.behavior, reason=args.reason)
    result = {"ok": True, "id": args.id, "behavior": args.behavior, "tool_use_id": pend["tool_use_id"]}
    return emit(args, result, lambda: print(f"{args.behavior}: {pend['text']}"))


def cmd_service(args: argparse.Namespace) -> int:
    from agentorc import service

    if args.action == "install":
        written = service.install(bind=args.bind, port=args.port, start=not args.no_start)
        status = service.status()
        return emit(
            args,
            {"written": written, "status": status},
            lambda: print(
                "wrote " + ", ".join(written) + "\n" + status + "\n"
                "units run under your user; `loginctl enable-linger` keeps them (and tmux) alive after logout"
            ),
        )
    if args.action == "uninstall":
        service.uninstall()
        return emit(args, {"ok": True}, lambda: print("units disabled and removed (tmux sessions untouched)"))
    status = service.status()
    return emit(args, {"status": status}, lambda: print(status))


def fail(args: argparse.Namespace, message: str, code: int, prose: str | None = None, **extra: Any) -> int:
    """An error in the same shape as a success: `{"error": …, **extra}` on stdout under `--json`;
    otherwise `prose` (default `error: <message>` plus any `hint` line) on stderr. Same exit code."""
    if args.json:
        print(json.dumps({"error": message, **extra}))
    else:
        line = prose if prose is not None else f"error: {message}"
        print(line + (f"\n{extra['hint']}" if extra.get("hint") else ""), file=sys.stderr)
    return code


def cmd_ui(args: argparse.Namespace) -> int:
    from agentorc.ui.app import main as ui_main

    return ui_main(["--bind", args.bind, "--port", str(args.port)])


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ao", description="agentorc — sessions in tmux, one view")
    ap.add_argument("--json", action="store_true", help="print the RPC result as JSON (every subcommand; TD-018)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, **kw: Any) -> argparse.ArgumentParser:
        p = sub.add_parser(name, **kw)
        # SUPPRESS: a subparser default would otherwise overwrite the global flag's value
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="print the RPC result as JSON")
        return p

    p = add("status", help="list sessions on this host")
    p.add_argument("-v", "--verbose", action="store_true", help="show the last output lines")
    p.set_defaults(fn=cmd_status)

    p = add("new", help="start a session")
    p.add_argument("name")
    p.add_argument("-d", "--dir", help="directory (default: cwd)")
    p.add_argument("-a", "--adapter", default="claude-code")
    p.add_argument("-p", "--profile", help="tool · account · model profile name")
    p.add_argument("--repo", help="repo root when dir is a worktree")
    p.add_argument(
        "-w",
        "--worktree",
        help="run in <repo>/.claude/worktrees/NAME on branch NAME (created if missing); dir is the repo",
    )
    p.add_argument("--unattended", action="store_true")
    p.add_argument("--resume", help="the tool's session id to resume")
    p.add_argument("--prompt", help="opening prompt")
    p.add_argument("--attach", action="store_true", help="then attach this terminal to it (tmux attach)")
    p.set_defaults(fn=cmd_new)

    p = add("shell", help="start a plain shell session here")
    p.add_argument("name", nargs="?")
    p.add_argument("-d", "--dir")
    p.add_argument("--attach", action="store_true", help="then attach this terminal to it (tmux attach)")
    p.set_defaults(fn=cmd_shell)

    p = add("focus", help="attach this terminal to a session (the Focus screen, in tmux)")
    p.add_argument("id")
    p.set_defaults(fn=cmd_focus)

    for name, fn, help_ in (
        ("kill", cmd_kill, "kill a session (worktree kept)"),
        ("close", cmd_close, "close a session"),
    ):
        p = add(name, help=help_)
        p.add_argument("id")
        p.set_defaults(fn=fn)

    p = add("send", help="send a prompt (args or stdin)")
    p.add_argument("id")
    p.add_argument("text", nargs="*")
    p.add_argument(
        "--wait",
        action="store_true",
        help="return once the session has started on the prompt and settled again (idle, needs-you, exited); "
        "errors prompt-stalled / timeout",
    )
    p.add_argument("--timeout", type=float, help="seconds to wait for it to settle (default: no limit)")
    p.set_defaults(fn=cmd_send)

    p = add("keys", help="send raw tmux key names (Down Enter Escape C-c …) to a session")
    p.add_argument("id")
    p.add_argument("keys", nargs="+")
    p.set_defaults(fn=cmd_keys)

    p = add("tail", help="last lines of a session's pane")
    p.add_argument("id")
    p.add_argument("-n", "--lines", type=int, default=40)
    p.set_defaults(fn=cmd_tail)

    p = add("mode", help="flip a session between unattended and interactive")
    p.add_argument("id")
    p.add_argument("mode", choices=["unattended", "interactive"])
    p.set_defaults(fn=cmd_mode)

    p = add("ui", help="serve the web UI (localhost by default; design §4.5 security)")
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(fn=cmd_ui)

    p = add("service", help="systemd user units for the agent and the UI (install | uninstall | status)")
    p.add_argument("action", choices=["install", "uninstall", "status"])
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument(
        "--no-start",
        action="store_true",
        help="write and enable the units (start at next login/boot) without starting them now",
    )
    p.set_defaults(fn=cmd_service)

    for behavior in ("allow", "deny"):
        p = add(behavior, help=f"{behavior} the pending permission")
        p.add_argument("id")
        p.add_argument("reason", nargs="?")
        p.set_defaults(fn=cmd_decide, behavior=behavior)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except AgentUnavailable as e:
        return fail(args, str(e), 3, hint="start it with: agentorc-agent serve")
    except AgentError as e:
        return fail(args, str(e), 1)


if __name__ == "__main__":
    sys.exit(main())
