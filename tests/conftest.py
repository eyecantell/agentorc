"""Shared fixtures. Read tests/README.md before adding a fixture that touches tmux or a socket.

Two agents are on offer:

- `agent`: an in-process `HostAgent` on this test's event loop. For `async def` tests that talk
  to it through `LocalClient`. Cheap, one per test.
- `subprocess_agent`: a separate process (`tests/_agent_child.py`), module-scoped. For plain
  `def` tests that go through `call_sync` / `cli.main` / the sync FastAPI `TestClient`: those
  call `asyncio.run`, which hangs against the in-process fixture because a sync test body never
  pumps that fixture's loop.

Both use a private tmux server (`-L ao-test-<uuid>`) and a temp `AGENTORC_HOME`, never the
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
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import pytest
from _stubs import HookFedStub

from sessionorc import paths
from sessionorc.agent import HostAgent
from sessionorc.tmux import Tmux

HERE = Path(__file__).parent
CHILD = HERE / "_agent_child.py"
FAST_TICK = 0.3


def private_socket_name() -> str:
    return f"ao-test-{uuid.uuid4().hex[:8]}"


# -- stale servers from a killed run ------------------------------------------------------------


def kill_private_server(tmux: Tmux) -> None:
    """`tmux kill-server` leaves the socket file behind; unlink it so the sweep below stays cheap."""
    tmux.kill_server()
    if tmux.socket_name:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(f"/tmp/tmux-{os.getuid()}/{tmux.socket_name}")


@pytest.fixture(scope="session", autouse=True)
def _sweep_stale_test_servers():
    """A killed pytest run leaves `ao-test-*` tmux servers behind (never the user's). Best effort:
    a live socket accepts a connection and gets kill-server; a dead socket file is unlinked."""
    for sock in glob.glob(f"/tmp/tmux-{os.getuid()}/ao-test-*"):
        with socket.socket(socket.AF_UNIX) as s:
            try:
                s.connect(sock)
            except OSError:
                with contextlib.suppress(OSError):
                    os.unlink(sock)
                continue
        subprocess.run(["tmux", "-S", sock, "kill-server"], capture_output=True, check=False)
        with contextlib.suppress(OSError):
            os.unlink(sock)
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


async def wait_state(client, sid: str, state: str, timeout: float = 6.0) -> dict:
    s = await client.call("get", id=sid)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if s["state"] == state:
            return s
        await asyncio.sleep(0.1)
        s = await client.call("get", id=sid)
    raise AssertionError(f"{sid} never reached {state}: {s['state']} {s.get('tail')}")


# -- the in-process agent -----------------------------------------------------------------------


@pytest.fixture
async def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("sessionorc.agent.TICK_SECONDS", FAST_TICK)
    tmux = Tmux(socket_name=private_socket_name())
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
        mp.setenv("AGENTORC_TMUX_SOCKET", sock_name)
        mp.setenv("AGENTORC_TICK", str(FAST_TICK))
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
