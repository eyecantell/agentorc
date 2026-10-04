#!/usr/bin/env python3
"""A scratch home to look at a UI change in (design §4.9b *A UI change is verified by its builder*, TD-291).

Stands up a host agent and the web UI from the checkout this file sits in, on an `AGENTORC_HOME` of its
own under a short path (a unix socket's path is capped at about 108 bytes), a tmux server of its own and
a free port, with a fixture repo — a git checkout with a board and a ledger, on the home's repo roster —
and fixture sessions under the `shell` adapter. It prints the URL, and on Ctrl+C, SIGTERM or SIGHUP stops
both processes, kills its tmux server and removes the home it made.

It is never the live system: it refuses a home that is, or is under, `~/.agentorc`, and never runs
anything on the default tmux server (`agentorc-agent serve` builds a bare `Tmux()` on it, so the agent
is a child this script starts with a `Tmux` on a private socket, as `tests/conftest.py`'s fixture does).
A SIGKILL of the script cannot tear down: its two children, its tmux server (`tmux -L ao-look-…`) and
its `/tmp/aolook-*` home are then left for you to remove.

    pdm run python scripts/look_home.py            # prints `look home: http://127.0.0.1:<port>/`
    pdm run python scripts/look_home.py --port 8799 --sessions 0

Run it with the worktree's own interpreter: the children import `src/` of this checkout first.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
LIVE_HOME = Path("~/.agentorc").expanduser()
# what this process takes on itself once the agent is up, to start the fixture sessions through it
SCRATCH_ENV = ("AGENTORC_HOME", "AGENTORC_TMUX_SOCKET", "CLAUDE_CONFIG_DIR", "DEV_CADENCE_REG_DIR")
FIXTURE_SESSIONS = ("look-shell-1", "look-shell-2", "look-shell-3")
_LINE = "- [ ] {kind} {{today}} (session `look` on look) — **{head}** {rest}Context: TD-001. Due: {due}."
BOARD = "\n".join(
    [
        "# User Attention Board",
        "",
        "Format: `- [ ] YYYY-MM-DD (session <first-8-of-session-uuid> on <host>, or n/a) — what's needed."
        " Context: TD-NNN / PR #N / branch. Due: YYYY-MM-DD.`",
        "",
        "## Needs the user",
        "",
        _LINE.format(kind="act", head="A fixture act line.", rest="Something a person must do. ", due="{today}"),
        _LINE.format(kind="watch", head="A fixture live look.", rest="Say whether the Org reads right. ", due="{today}")
        + " Answers: Works | Not right: <what>.",
        _LINE.format(kind="look", head="A fixture look.", rest="Say whether the Inbox reads right. ", due="{today}")
        + " Answers: Works | Not right: <what>.",
        _LINE.format(kind="watch", head="A fixture line not due yet.", rest="", due="2099-01-01"),
        _LINE.format(kind="look", head="A fixture look the techlead leaned on.", rest="", due="{ahead}")
        + " Answers: Works (default) | Not right: <what>.",
        _LINE.format(kind="look", head="A fixture look past its bound.", rest="", due="{before}")
        + " Answers: Works (default) | Not right: <what>.",
        "",
    ]
)
LEDGER = """# Technical debt

| ID | Summary | Priority | Status |
|---|---|---|---|
| TD-001 | A fixture entry | Medium | Open |

## TD-001: A fixture entry

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-01
**Owner:** grinder
**Kind:** build
**Status:** Open
**Location:** nowhere

**Why:** the scratch home's ledger has one entry, so the Repo page has something to draw.

**Done when:** never.
"""


def refuse_live(home: Path) -> str | None:
    """Why `home` may not be a scratch home, or None. The live home and anything under it are refused."""
    h, live = home.expanduser().resolve(), LIVE_HOME.resolve()
    if h == live or live in h.parents:
        return f"{home} is the live agentorc home (or under it): a scratch home never is"
    if len(str(h / "agent.sock")) > 100:
        return f"{home} is too long a path for a unix socket; use a short one (the default is under /tmp)"
    return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def child_env(home: Path, sock: str) -> dict[str, str]:
    env = dict(os.environ)
    for k in ("AGENTORC_SESSION", "TMUX", "TMUX_PANE"):  # nobody's session, inside nobody's pane
        env.pop(k, None)
    env.update(
        AGENTORC_HOME=str(home),
        AGENTORC_TMUX_SOCKET=sock,
        CLAUDE_CONFIG_DIR=str(home / "claude"),  # never this machine's live Claude registry
        DEV_CADENCE_REG_DIR=str(home),  # the board reader's roster is the home's `repos.txt`, never the person's
        PYTHONPATH=os.pathsep.join([str(SRC), *filter(None, [env.get("PYTHONPATH")])]),
    )
    return env


def write_fixtures(home: Path) -> Path:
    """The home's files and the fixture repo; returns the repo's path."""
    repo = home / "repo"
    (repo / "docs").mkdir(parents=True)
    today = time.strftime("%Y-%m-%d")
    day = 86400
    ahead, before = (time.strftime("%Y-%m-%d", time.localtime(time.time() + d)) for d in (2 * day, -day))
    (repo / "docs" / "user_attention.md").write_text(
        BOARD.format(today=today, ahead=ahead, before=before), encoding="utf-8"
    )
    (repo / "docs" / "technical_debt.md").write_text(LEDGER, encoding="utf-8")
    # the Inbox runs the board reader a registered repo carries: this checkout's copy
    (repo / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts" / "nudge_user_attention.py", repo / "scripts" / "nudge_user_attention.py")
    git = ["git", "-C", str(repo), "-c", "user.name=look", "-c", "user.email=look@localhost"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "fixture"], check=True)
    (home / "repos.txt").write_text(f"{repo}\n", encoding="utf-8")
    (home / "hosts.yml").write_text(
        f"local:\n  name: look\n  vscode_host: look\n  repos_registry: {home / 'repos.txt'}\n", encoding="utf-8"
    )
    (home / "claude").mkdir()
    return repo


async def _agent(sock: str) -> None:
    from sessionorc import adapters
    from sessionorc.agent import HostAgent, serve_until_signal
    from sessionorc.tmux import Tmux

    adapters.load_all()
    await serve_until_signal(HostAgent(tmux=Tmux(socket_name=sock)))


def run_agent(sock: str) -> int:
    """`--agent <socket>`: the child that is the host agent (the env names the home)."""
    asyncio.run(_agent(sock))
    return 0


def wait_for(cond, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="look_home.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=0, help="the UI's port (default: a free one)")
    ap.add_argument("--home", type=Path, help="the scratch AGENTORC_HOME, which must not exist (default: under /tmp)")
    ap.add_argument("--sessions", type=int, default=len(FIXTURE_SESSIONS), help="fixture shell sessions to start")
    ap.add_argument("--agent", metavar="SOCKET", help=argparse.SUPPRESS)  # the child's own mode
    args = ap.parse_args(argv)
    if args.agent:
        return run_agent(args.agent)

    if args.home is not None:
        if why := refuse_live(args.home):
            print(f"look_home: refused — {why}", file=sys.stderr)
            return 2
        if args.home.exists():
            print(f"look_home: refused — {args.home} exists; a scratch home is made fresh", file=sys.stderr)
            return 2
        home = args.home.expanduser().resolve()
        home.mkdir(parents=True)
    else:
        home = Path(tempfile.mkdtemp(prefix="aolook-", dir="/tmp"))
    sock = f"ao-look-{uuid.uuid4().hex[:8]}"
    port = args.port or free_port()
    env = child_env(home, sock)
    procs: list[subprocess.Popen] = []
    stopping = False

    def on_signal(signum, _frame):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, on_signal)
    try:
        repo = write_fixtures(home)
        agent = subprocess.Popen([sys.executable, __file__, "--agent", sock], env=env, cwd=ROOT)
        procs.append(agent)
        if not wait_for(lambda: stopping or (home / "agent.sock").exists() or agent.poll() is not None, 15):
            print("look_home: the host agent never opened its socket", file=sys.stderr)
            return 1
        if stopping:
            return 0
        if agent.poll() is not None:
            print(f"look_home: the host agent exited {agent.returncode}", file=sys.stderr)
            return 1
        os.environ.update({k: env[k] for k in SCRATCH_ENV})
        os.environ.pop("AGENTORC_SESSION", None)
        sys.path.insert(0, str(SRC))
        from sessionorc.client import call_sync

        for name in FIXTURE_SESSIONS[: max(0, args.sessions)]:
            if stopping:
                return 0
            call_sync("create", name=name, dir=str(repo), adapter="shell")
        ui = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys; from agentorc.ui.app import main; sys.exit(main(sys.argv[1:]))",
                "--bind",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=env,
            cwd=ROOT,
        )
        procs.append(ui)

        def up() -> bool:
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
            return stopping or ui.poll() is not None

        if stopping:
            return 0
        if not wait_for(up, 20) or ui.poll() is not None:
            print("look_home: the UI never answered", file=sys.stderr)
            return 1
        print(f"look home: http://127.0.0.1:{port}/  (AGENTORC_HOME={home}, tmux -L {sock})", flush=True)
        while not stopping and all(p.poll() is None for p in procs):
            time.sleep(0.2)
        if dead := [n for n, p in zip(("host agent", "UI"), procs, strict=True) if p.poll() is not None]:
            print(f"look_home: the {' and the '.join(dead)} exited; the scratch home is torn down", file=sys.stderr)
            return 1
        return 0
    finally:
        for p in reversed(procs):
            stop(p)
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True, timeout=5)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(f"/tmp/tmux-{os.getuid()}/{sock}")
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
