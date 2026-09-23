"""Shared fixtures. Read tests/README.md before adding a fixture that touches tmux or a socket.

Two agents are on offer:

- `agent`: an in-process `HostAgent` on this test's event loop. For `async def` tests that talk
  to it through `LocalClient`. Cheap, one per test.
- `subprocess_agent`: a separate process (`tests/_agent_child.py`), module-scoped. For plain
  `def` tests that go through `call_sync` / `cli.main` / the sync FastAPI `TestClient`: those
  call `asyncio.run`, which hangs against the in-process fixture because a sync test body never
  pumps that fixture's loop.

Both use a private tmux server (`-L ao-test-<uuid>`), a temp `AGENTORC_HOME` and a temp
`CLAUDE_CONFIG_DIR` (the default profile's registry; the occupancy check would otherwise see this
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


@pytest.fixture(autouse=True)
def _identity_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Design §4.8a *Tests*: the suite drives the socket from the pytest process — under no pane —
    and passes `caller=<id>` to stand for a session, which is exactly what the rule table calls a
    forgery. So an agent built here with no mode of its own runs `off`; the classification is
    tested directly (tests/test_identity.py), and `enforce` against real panes there too."""
    from sessionorc import identity

    monkeypatch.setattr(identity, "DEFAULT_MODE", "off")


@pytest.fixture(autouse=True)
def _no_real_repos_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A host with no `repos_registry:` of its own reads dev-cadence's machine roster in the
    person's home — and from TD-069 step 3 the Inbox runs the board reader over every repo on it.
    A test never reads the person's roster: it gets an empty one unless it writes its own."""
    from sessionorc import hosts

    monkeypatch.setattr(hosts, "DEFAULT_REPOS_REGISTRY", str(tmp_path / "no-roster" / "repos.txt"))


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


@pytest.fixture(scope="session", autouse=True)
def _never_docker():
    """Docker is not in `pdm run test` (TD-057 3c.2): a host agent's container supervisor that
    reaches `containers.Runner` here gets a refusal, never the machine's docker. A test that wants
    the seam builds its own fake on the original class."""
    from sessionorc import containers

    class NoDocker(containers.Runner):
        def run(self, cmd, *, check=True, stream=False):
            raise containers.ContainerError("docker is not in pdm run test: give the agent a fake container_runner")

        def devcontainer(self):
            raise containers.ContainerError("docker is not in pdm run test: give the agent a fake container_runner")

    original = containers.Runner
    containers.Runner = NoDocker
    yield
    containers.Runner = original


@pytest.fixture(scope="session", autouse=True)
def _never_this_machines_home(tmp_path_factory):
    """No test reads this machine's `~/.agentorc` or `~/.claude` (TD-044).

    The agent fixtures set their own `AGENTORC_HOME`, but a *unit* test that only calls into
    `agentorc.cli` sets nothing — and since `org.yml` landed (TD-040), `ao roles` reads it from
    `paths.home()`, so on a machine with a real fleet definition those tests saw Paul's roles and
    failed. Session scope on purpose: it is set up before the module- and function-scoped agent
    fixtures, which then override it with their own temp home for the tests that need one."""
    mp = pytest.MonkeyPatch()
    mp.setenv("AGENTORC_HOME", str(tmp_path_factory.mktemp("unit-home")))
    mp.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("unit-claude")))
    mp.delenv("AGENTORC_SESSION", raising=False)  # a test run inside an ao session is not a caller
    # …and nor is its ancestry: the exit-3 sentence reads the pane's environment above `ao` (TD-089)
    from agentorc import cli

    mp.setattr(cli, "PROC", tmp_path_factory.mktemp("no-proc"))
    yield
    mp.undo()


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


def wait_screen(sid: str, produce, ok, timeout: float = 8.0, step: float = 0.1, what: str = "what it asserts on"):
    """Read a live pane until it shows the thing the assertion is about, then return that reading
    (TD-033, TD-025): `produce()` is re-run until `ok(value)`. Several tests read a pane once and
    asserted on it, which passes on an idle box and races a loaded one — a pane that has not
    painted yet is not a wrong pane, it is an early read. This is `wait_state` for a screen: bounded,
    on the real signal, and a timeout says what never appeared plus tmux's own pane line."""
    end = time.monotonic() + timeout
    while True:
        value = produce()
        if ok(value):
            return value
        if time.monotonic() >= end:
            raise AssertionError(f"{sid}: {what} never appeared in {timeout}s: {value!r} {pane_line(sid)}")
        time.sleep(step)


async def wait_state(client, sid: str, state: str, timeout: float = 6.0) -> dict:
    s = await client.call("get", id=sid)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if s["state"] == state:
            return s
        await asyncio.sleep(0.1)
        s = await client.call("get", id=sid)
    raise AssertionError(f"{sid} never reached {state}: {s['state']} {s.get('tail')} {pane_line(sid)}")


async def derived(agent, check, timeout: float = 10.0):
    """Drive derivations until `check()` holds, and return what it returned (TD-063).

    `tick()` starts a derivation only when `self._derive_task` is None or done, and the `agent`
    fixture runs a live tick loop at `FAST_TICK`. So a background tick can already own an in-flight
    derive whose snapshot predates whatever the test just changed — a `git checkout`, a `gh` stub —
    and the test's own `tick()` then starts nothing at all: `await agent._derive_task` awaits that
    stale derive, which legitimately still reports the old answer. Seen on CI 2026-09-17 as
    `test_the_tick_retires_a_branch_claim_the_session_abandoned` asserting `progress == []` one
    derive too early. Waiting on the condition instead of on one task is what makes it deterministic:
    a stale derive costs a retry rather than a failure, and nothing here waits on a duration.

    `check` is called after each round and may be a coroutine function.
    """
    end = time.monotonic() + timeout
    while True:
        if (t := agent._derive_task) is not None and not t.done():
            # A derive this helper did not start: whatever it raises belongs to the round that
            # started it, not to this one, and the round below is the one under test.
            with contextlib.suppress(Exception):
                await t  # let a tick's in-flight derive finish before asking for a fresh one
        agent._git_checked.clear()  # the branch read has its own cadence; the derive follows it
        agent._derived_at.clear()
        await agent.tick()
        if (t := agent._derive_task) is not None:
            await t  # not suppressed: this is the derive the round asked for, and its traceback is
            # worth more than the generic timeout below (review of PR #199)
        got = check()
        if asyncio.iscoroutine(got):
            got = await got
        if got:
            return got
        if time.monotonic() > end:
            raise AssertionError(f"the derive never satisfied {getattr(check, '__doc__', None) or check}")
        await asyncio.sleep(0.05)


# -- the in-process agent -----------------------------------------------------------------------


async def park_ticks(agent) -> None:
    """Stop the fixture's tick loop for the rest of the test, so a test that must own the clock
    **owns** it: every tick after this is one the test takes by hand with `await agent.tick()`.

    Narrowing the window is not the same as closing it — TD-078 and TD-088 are both a wait bounded
    on something other than the thing waited for, and a sleep raced against the loop is exactly
    that. Cancelling the task cannot race: it is awaited here, so an in-flight tick is finished or
    cancelled before this returns. Nothing resumes it; `serve`'s own `finally` cancels a cancelled
    task harmlessly at teardown.
    """
    t = getattr(agent, "_ticker", None)
    if t is None:
        return
    t.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await t


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
