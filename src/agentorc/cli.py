"""`agentorc` / `ao`: a thin client of the host agent (design §4.7). Never touches tmux itself."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime

from sessionorc.client import AgentError, AgentUnavailable, call_sync
from sessionorc.models import STATE_RANK


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


def cmd_new(args: argparse.Namespace) -> int:
    s = call_sync(
        "create",
        name=args.name,
        dir=args.dir or os.getcwd(),
        adapter=args.adapter,
        profile=args.profile or "",
        repo=args.repo,
        unattended=args.unattended,
        resume=args.resume,
        prompt=args.prompt,
    )
    print(f"{s['id']}  ({s['adapter']}, {s['dir']})")
    print(f"attach: tmux attach -t {s['id']}")
    return 0


def cmd_shell(args: argparse.Namespace) -> int:
    args.adapter, args.profile, args.repo, args.unattended, args.resume, args.prompt = (
        "shell",
        "",
        None,
        False,
        None,
        None,
    )
    args.name = args.name or "shell"
    return cmd_new(args)


def cmd_kill(args: argparse.Namespace) -> int:
    call_sync("kill", id=args.id)
    print(f"killed {args.id}")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    call_sync("close", id=args.id)
    print(f"closed {args.id}")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    text = " ".join(args.text) if args.text else sys.stdin.read()
    call_sync("send", id=args.id, text=text)
    return 0


def cmd_keys(args: argparse.Namespace) -> int:
    """Raw tmux key names into the pane (Down, Enter, Escape, C-c, 1 …) — for dialogs the
    terminal owns when no browser is open. Not a menu-answering API: design §9 invariant 6."""
    call_sync("keys", id=args.id, keys=args.keys)
    return 0


def cmd_tail(args: argparse.Namespace) -> int:
    for line in call_sync("tail", id=args.id, lines=args.lines):
        print(line)
    return 0


def cmd_mode(args: argparse.Namespace) -> int:
    s = call_sync("set_mode", id=args.id, unattended=args.mode == "unattended")
    print(f"{s['id']}: {'unattended' if s['unattended'] else 'interactive'}")
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    s = call_sync("get", id=args.id)
    pend = s.get("pending") or {}
    if pend.get("kind") != "permission" or not pend.get("tool_use_id"):
        print(f"{args.id} has no pending permission", file=sys.stderr)
        return 1
    call_sync("decide", id=args.id, tool_use_id=pend["tool_use_id"], behavior=args.behavior, reason=args.reason)
    print(f"{args.behavior}: {pend['text']}")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from agentorc.ui.app import main as ui_main

    return ui_main(["--bind", args.bind, "--port", str(args.port)])


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ao", description="agentorc — sessions in tmux, one view")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="list sessions on this host")
    p.add_argument("-v", "--verbose", action="store_true", help="show the last output lines")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("new", help="start a session")
    p.add_argument("name")
    p.add_argument("-d", "--dir", help="directory (default: cwd)")
    p.add_argument("-a", "--adapter", default="claude-code")
    p.add_argument("-p", "--profile", help="tool · account · model profile name")
    p.add_argument("--repo", help="repo root when dir is a worktree")
    p.add_argument("--unattended", action="store_true")
    p.add_argument("--resume", help="the tool's session id to resume")
    p.add_argument("--prompt", help="opening prompt")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("shell", help="start a plain shell session here")
    p.add_argument("name", nargs="?")
    p.add_argument("-d", "--dir")
    p.set_defaults(fn=cmd_shell)

    for name, fn, help_ in (
        ("kill", cmd_kill, "kill a session (worktree kept)"),
        ("close", cmd_close, "close a session"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("id")
        p.set_defaults(fn=fn)

    p = sub.add_parser("send", help="send a prompt (args or stdin)")
    p.add_argument("id")
    p.add_argument("text", nargs="*")
    p.set_defaults(fn=cmd_send)

    p = sub.add_parser("keys", help="send raw tmux key names (Down Enter Escape C-c …) to a session")
    p.add_argument("id")
    p.add_argument("keys", nargs="+")
    p.set_defaults(fn=cmd_keys)

    p = sub.add_parser("tail", help="last lines of a session's pane")
    p.add_argument("id")
    p.add_argument("-n", "--lines", type=int, default=40)
    p.set_defaults(fn=cmd_tail)

    p = sub.add_parser("mode", help="flip a session between unattended and interactive")
    p.add_argument("id")
    p.add_argument("mode", choices=["unattended", "interactive"])
    p.set_defaults(fn=cmd_mode)

    p = sub.add_parser("ui", help="serve the web UI (localhost by default; design §4.5 security)")
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(fn=cmd_ui)

    for behavior in ("allow", "deny"):
        p = sub.add_parser(behavior, help=f"{behavior} the pending permission")
        p.add_argument("id")
        p.add_argument("reason", nargs="?")
        p.set_defaults(fn=cmd_decide, behavior=behavior)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except AgentUnavailable as e:
        print(f"error: {e}\nstart it with: agentorc-agent serve", file=sys.stderr)
        return 3
    except AgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
