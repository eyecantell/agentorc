"""Shared fixtures. Read tests/README.md before adding a fixture that touches tmux or a socket.

Two agents are on offer:

- `agent`: an in-process `HostAgent` on this test's event loop. For `async def` tests that talk
  to it through `LocalClient`. Cheap, one per test.
- `subprocess_agent`: a separate process (`tests/_agent_child.py`), module-scoped. For plain
  `def` tests that go through `call_sync` / `cli.main` / the sync FastAPI `TestClient`: those
  call `asyncio.run`, which hangs against the in-process fixture because a sync test body never
  pumps that fixture's loop.

Both use a private tmux server (`-L ao-test-<uuid>`), a temp `AGENTORC_HOME` and a temp
`CLAUDE_CONFIG_DIR` (the default profile's registry; registry-only cards would otherwise show this
machine's live Claude sessions, TD-010 a), never the
user's. `subprocess_agent` sets the env in the test process too: the UI under `TestClient` runs
here and reads `paths.socket_path()` and `AGENTORC_TMUX_SOCKET` from `os.environ` at call time.
"""

from __future__ import annotations

import asyncio
import contextlib
import glob
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import pytest
from _stubs import ComposerStub, HookFedStub

from sessionorc import paths
from sessionorc.agent import HostAgent
from sessionorc.tmux import Tmux

HERE = Path(__file__).parent
CHILD = HERE / "_agent_child.py"
FAST_TICK = 0.3


# Which pytest process owns which private tmux server. The sweep below kills leaked servers, and a
# live server whose owner is still running belongs to a *concurrent* run — a reviewer's suite beside
# a worker's, two `pytest` processes in one loop — which must never be swept (TD-025).
OWNERS = Path(tempfile.gettempdir()) / f"ao-test-owners-{os.getuid()}"


def private_socket_name() -> str:
    """A socket name for this process's own tmux server, recorded as owned by it."""
    name = f"ao-test-{uuid.uuid4().hex[:8]}"
    with contextlib.suppress(OSError):
        OWNERS.mkdir(exist_ok=True)
        (OWNERS / name).write_text(str(os.getpid()))
    return name


def _owner_alive(name: str) -> bool:
    """Is the pytest process that created this server still running? An unowned socket (a leak from
    a run that predates this bookkeeping) answers False, so it is still swept. A recycled pid can
    make a leaked server look owned; that costs one skipped sweep, and the next run whose glob finds
    it takes it — the cost of being wrong the other way is killing a live run's server."""
    try:
        pid = int((OWNERS / name).read_text().strip())
    except (OSError, ValueError):
        return False
    return pid != os.getpid() and Path(f"/proc/{pid}").exists()


def _forget_owner(name: str) -> None:
    with contextlib.suppress(OSError):
        (OWNERS / name).unlink()


# -- stale servers from a killed run ------------------------------------------------------------


def kill_private_server(tmux: Tmux) -> None:
    """`tmux kill-server` leaves the socket file behind; unlink it so the sweep below stays cheap."""
    tmux.kill_server()
    if tmux.socket_name:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(f"/tmp/tmux-{os.getuid()}/{tmux.socket_name}")
        _forget_owner(tmux.socket_name)


def sweep_stale_test_servers() -> None:
    """A killed pytest run leaves `ao-test-*` tmux servers behind (never the user's). Best effort:
    a live socket accepts a connection and gets kill-server; a dead socket file is unlinked.

    **A server another pytest process is still using is left alone** (TD-025). This sweep used to
    kill every live `ao-test-*` server it found, so a second suite starting six seconds into the
    first one destroyed the first one's tmux server mid-test: its panes vanished from `list-panes`,
    its records froze at `working` with an empty tail, and its next `send-keys` failed with
    `error connecting to /tmp/tmux-…`. That is the whole family of "seen once while a review ran
    the suite beside me" flakes, and it is why the ownership file exists."""
    for sock in glob.glob(f"/tmp/tmux-{os.getuid()}/ao-test-*"):
        name = os.path.basename(sock)
        with socket.socket(socket.AF_UNIX) as s:
            try:
                s.connect(sock)
            except OSError:
                with contextlib.suppress(OSError):
                    os.unlink(sock)
                _forget_owner(name)
                continue
        if _owner_alive(name):
            continue  # a concurrent run is using it
        subprocess.run(["tmux", "-S", sock, "kill-server"], capture_output=True, check=False)
        with contextlib.suppress(OSError):
            os.unlink(sock)
        _forget_owner(name)


@pytest.fixture(scope="session", autouse=True)
def _sweep_stale_test_servers():
    sweep_stale_test_servers()
    yield


# -- waiting --------------------------------------------------------------------------------------


async def wait_for(pred: Callable[[], object], timeout: float = 6.0, step: float = 0.1) -> bool:
    """Poll an async or sync predicate until it is truthy. Bounded: returns False on timeout."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        res = pred()
        if asyncio.iscoroutine(res):
            res = await res
        if res:
            return True
        await asyncio.sleep(step)
    return False


def wait_for_sync(pred: Callable[[], object], timeout: float = 5.0, step: float = 0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return False


def pane_line(sid: str) -> str:
    """What tmux itself says about a session's pane, for a wait that timed out (TD-025): the
    record's state and tail alone cannot tell a slow shell start from a pane that never appeared."""
    sock = os.environ.get("AGENTORC_TMUX_SOCKET")
    if not sock:
        return "(no AGENTORC_TMUX_SOCKET: pane not inspected)"
    try:
        pane = Tmux(socket_name=sock).main_panes().get(sid)
    except Exception as e:  # noqa: BLE001 — a diagnostic must never mask the assertion
        return f"(pane lookup failed: {e})"
    return f"pane={pane}" if pane else "pane=none (not in list-panes)"


async def wait_state(client, sid: str, state: str, timeout: float = 6.0) -> dict:
    s = await client.call("get", id=sid)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if s["state"] == state:
            return s
        await asyncio.sleep(0.1)
        s = await client.call("get", id=sid)
    raise AssertionError(f"{sid} never reached {state}: {s['state']} {s.get('tail')} {pane_line(sid)}")


# -- the in-process agent -----------------------------------------------------------------------


@pytest.fixture
async def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))  # never this machine's live registry
    monkeypatch.delenv("AGENTORC_SESSION", raising=False)  # tests run inside an ao session are not a caller
    monkeypatch.setattr("sessionorc.agent.TICK_SECONDS", FAST_TICK)
    sock_name = private_socket_name()
    monkeypatch.setenv("AGENTORC_TMUX_SOCKET", sock_name)  # so `pane_line` can look at this server
    tmux = Tmux(socket_name=sock_name)
    a = HostAgent(tmux=tmux)
    task = asyncio.create_task(a.serve(paths.socket_path()))
    assert await wait_for(lambda: paths.socket_path().exists(), timeout=5.0, step=0.05), "agent socket never appeared"
    yield a
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    kill_private_server(tmux)


# -- the subprocess agent -----------------------------------------------------------------------


class ChildAgent(NamedTuple):
    home: Path
    sock_name: str
    proc: subprocess.Popen


@pytest.fixture(scope="module")
def subprocess_agent(tmp_path_factory):
    """Module-scoped, so it cannot take the function-scoped `monkeypatch`; a manual MonkeyPatch,
    undone at teardown, sets the env in the test process AND the child."""
    mp = pytest.MonkeyPatch()
    home = tmp_path_factory.mktemp("home")
    sock_name = private_socket_name()
    proc: subprocess.Popen | None = None
    try:
        mp.setenv("AGENTORC_HOME", str(home))
        mp.setenv("CLAUDE_CONFIG_DIR", str(home / "claude"))  # never this machine's live registry
        mp.setenv("AGENTORC_TMUX_SOCKET", sock_name)
        mp.setenv("AGENTORC_TICK", str(FAST_TICK))
        # `ao` sends AGENTORC_SESSION as the caller (design §4.8); a test run from inside an ao
        # session would otherwise be gated as a session acting on another one
        mp.delenv("AGENTORC_SESSION", raising=False)
        proc = subprocess.Popen(
            [sys.executable, str(CHILD), sock_name],
            env=dict(os.environ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        sock = home / "agent.sock"
        if not wait_for_sync(lambda: sock.exists() or proc.poll() is not None, timeout=10.0):
            proc.kill()
            raise RuntimeError(f"child agent never opened {sock}:\n{proc.communicate(timeout=5)[1]}")
        if proc.poll() is not None:
            raise RuntimeError(f"child agent exited {proc.returncode}:\n{proc.communicate(timeout=5)[1]}")
        yield ChildAgent(home=home, sock_name=sock_name, proc=proc)
        proc.send_signal(signal.SIGTERM)
        try:
            _, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate(timeout=5)
            raise RuntimeError(f"child agent ignored SIGTERM:\n{err}") from None
    finally:
        # runs on setup failure too, so the env never leaks into later modules
        if proc is not None and proc.poll() is None:
            proc.kill()
        kill_private_server(Tmux(socket_name=sock_name))
        mp.undo()
    if proc.returncode not in (0, -signal.SIGTERM):
        raise RuntimeError(f"child agent exited {proc.returncode}:\n{err}")


# -- hook-fed stub adapter ----------------------------------------------------------------------


@pytest.fixture
def hookstub(monkeypatch):
    """Register `HookFedStub` for this test only (the registry keeps its built-ins)."""
    from sessionorc import adapters

    adapters.load_all()
    stub = HookFedStub()
    monkeypatch.setitem(adapters._REGISTRY, stub.name, stub)
    return stub


@pytest.fixture
def composerstubs(monkeypatch):
    """Register `composer0`, `composer1`, `composer2` (a pane that swallows that many Enters per
    paste) for this test only (TD-027)."""
    from sessionorc import adapters

    adapters.load_all()
    for n in (0, 1, 2):
        stub = ComposerStub(n)
        monkeypatch.setitem(adapters._REGISTRY, stub.name, stub)


# -- the real hook script -----------------------------------------------------------------------


def run_hook(session: str, payload: dict, wait: str = "5", env: dict[str, str] | None = None):
    """Run `agentorc-hook` as Claude Code would: payload on stdin, session in the env."""
    env = {**(env or os.environ), "AGENTORC_SESSION": session, "AGENTORC_PERMISSION_WAIT": wait}
    return subprocess.run(
        [sys.executable, "-m", "agentorc.adapters.claude_code.hook"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
