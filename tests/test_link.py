"""The link between a node and its home (design §4.4a "The link's protocol", TD-057 step 3a)."""

import asyncio
import contextlib
import os
import signal
import subprocess
import sys

import pytest
from conftest import CHILD, FAST_TICK, kill_private_server, private_socket_name, wait_for

from sessionorc import link, paths
from sessionorc.agent import HostAgent
from sessionorc.client import AgentError, LocalClient
from sessionorc.tmux import Tmux

# -- the multiplexer, over a real socket pair ----------------------------------------------------------


@contextlib.asynccontextmanager
async def pair(tmp_path, a_handler, b_handler, **kw):
    """Two `Mux` ends joined by a unix socket, both running."""
    path, ready = str(tmp_path / "pair.sock"), asyncio.get_running_loop().create_future()

    async def accept(r, w):
        ready.set_result((r, w))

    server = await asyncio.start_unix_server(accept, path=path)
    r1, w1 = await asyncio.open_unix_connection(path)
    r2, w2 = await ready
    a, b = link.Mux(r1, w1, a_handler, **kw), link.Mux(r2, w2, b_handler, **kw)
    tasks = [asyncio.ensure_future(a.run()), asyncio.ensure_future(b.run())]
    try:
        yield a, b, tasks
    finally:
        a.close()
        b.close()
        server.close()
        await asyncio.gather(*tasks, return_exceptions=True)  # both end on the close; none left pending


async def nothing(method, params):
    raise link.LinkError(f"unknown link method {method!r}")


async def test_requests_are_multiplexed_both_ways_and_a_reply_may_overtake(tmp_path):
    async def slow_or_fast(method, params):
        if method == "slow":
            await asyncio.sleep(0.2)
        return {"did": method, **params}

    async with pair(tmp_path, slow_or_fast, slow_or_fast) as (a, b, _):
        slow = asyncio.ensure_future(a.request("slow", n=1))
        assert await a.request("fast", n=2) == {"did": "fast", "n": 2}  # answered while `slow` is still out
        assert not slow.done()
        assert await b.request("fast", n=3) == {"did": "fast", "n": 3}  # and the other end asks too, from id 1
        assert await slow == {"did": "slow", "n": 1}


async def test_a_handlers_error_is_that_requests_error_and_the_link_lives(tmp_path):
    async with pair(tmp_path, nothing, nothing) as (a, _b, _):
        with pytest.raises(link.LinkError, match="unknown link method 'nope'"):
            await a.request("nope")
        with pytest.raises(link.LinkError):
            await a.request("still-here")  # the link survived the first error


async def test_silence_ends_the_link_and_fails_what_was_outstanding(tmp_path):
    async def never(method, params):
        await asyncio.sleep(30)

    async with pair(tmp_path, nothing, never, silence=0.3) as (a, _b, tasks):
        with pytest.raises(link.LinkClosed):
            await a.request("anything")
        assert "no frame for 0.3 s" in await tasks[0]


async def test_a_frame_past_the_limit_ends_the_link_with_a_reason_never_an_exception(tmp_path, monkeypatch):
    """Review of PR #200: `readline` raises `ValueError` past the stream's limit. Uncaught, it ended
    the node's dialer for good and left the home calling a dead link up."""
    path, ready = str(tmp_path / "small.sock"), asyncio.get_running_loop().create_future()
    server = await asyncio.start_unix_server(lambda r, w: ready.set_result((r, w)), path=path, limit=1024)
    _r1, w1 = await asyncio.open_unix_connection(path)
    r2, w2 = await ready
    monkeypatch.setattr(link, "FRAME_LIMIT", 1024)
    mux = link.Mux(r2, w2, nothing)
    w1.write(b'{"method": "x", "params": {"pad": "' + b"a" * 4096 + b'"}}\n')
    await w1.drain()
    assert await asyncio.wait_for(mux.run(), timeout=5) == "a frame longer than 1024 bytes"
    assert mux.closed
    server.close()
    w1.close()


def test_backoff_doubles_to_a_ceiling_with_jitter():
    d = link.backoff_delays(1.0, 8.0)
    got = [next(d) for _ in range(6)]
    for delay, base in zip(got, [1, 2, 4, 8, 8, 8], strict=True):
        assert base <= delay <= base * 1.25


# -- a home and a node, end to end -----------------------------------------------------------------------


class Home:
    """A home agent in its own process and its own AGENTORC_HOME — the test process is the node."""

    def __init__(self, root, nodes):
        self.dir = root / "kmaster"
        self.dir.mkdir()
        self.write_hosts(nodes)
        self.sock_name = private_socket_name()
        self.proc = None

    def write_hosts(self, nodes):
        (self.dir / "hosts.yml").write_text(f"local:\n  name: kmaster\nnodes: [{', '.join(nodes)}]\n")

    @property
    def env(self):
        ours = {
            "AGENTORC_HOME": str(self.dir),
            "AGENTORC_TICK": str(FAST_TICK),
            "CLAUDE_CONFIG_DIR": str(self.dir / "c"),
        }
        return {**os.environ, **ours}

    async def start(self):
        env = self.env
        env.pop("AGENTORC_SESSION", None)
        self.proc = subprocess.Popen([sys.executable, str(CHILD), self.sock_name], env=env, stderr=subprocess.DEVNULL)
        assert await wait_for(lambda: (self.dir / "agent.sock").exists(), timeout=10.0, step=0.05), "home never came up"

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=5)
            if self.proc.poll() is None:
                self.proc.kill()

    def dial_command(self, host="laptop"):
        return ["env", f"AGENTORC_HOME={self.dir}", sys.executable, "-m", "sessionorc.agent", "link", "--host", host]

    async def host_rpc(self):
        async with LocalClient(sock=self.dir / "agent.sock") as c:
            return await c.call("host")


@pytest.fixture
async def home(tmp_path):
    h = Home(tmp_path, ["laptop"])
    try:  # around the start too: a home that never came up is still a process and a tmux server
        await h.start()
        yield h
    finally:
        h.stop()
        kill_private_server(Tmux(socket_name=h.sock_name))


@contextlib.asynccontextmanager
async def node_agent(tmp_path, monkeypatch, command, name="laptop"):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    (d / "hosts.yml").write_text(f"home: kmaster\nlocal:\n  name: {name}\nlink:\n  command: {command!r}\n")
    monkeypatch.setenv("AGENTORC_HOME", str(d))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(d / "claude"))
    monkeypatch.delenv("AGENTORC_SESSION", raising=False)
    monkeypatch.setattr("sessionorc.agent.TICK_SECONDS", FAST_TICK)
    monkeypatch.setattr(link, "BACKOFF_FIRST", 0.05)
    monkeypatch.setattr(link, "BACKOFF_MAX", 0.2)
    sock_name = private_socket_name()
    monkeypatch.setenv("AGENTORC_TMUX_SOCKET", sock_name)
    tmux = Tmux(socket_name=sock_name)
    a = HostAgent(tmux=tmux)
    task = asyncio.create_task(a.serve(paths.socket_path()))
    try:
        assert await wait_for(lambda: paths.socket_path().exists(), timeout=5.0, step=0.05)
        yield a
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        kill_private_server(tmux)


async def test_the_link_comes_up_drops_with_the_home_and_comes_back(home, tmp_path, monkeypatch):
    """The shape of *laptop closed for an hour*, from the link's side: up, down with a reason,
    and up again on its own, the backoff starting over."""
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as node:
        assert await wait_for(node.home_reachable, timeout=10.0, step=0.05), node.home_link
        assert node.home_link["why"] == "linked to kmaster as laptop"
        seen = await home.host_rpc()
        assert seen["mode"] == "home" and seen["links"]["laptop"]["up"] is True
        async with LocalClient() as c:
            mine = await c.call("host")
            assert mine["home_reachable"] is True and mine["link"]["up"] is True
            # a frame well past asyncio's 64 KiB default crosses the whole path — subprocess pipe,
            # bridge, the home's socket — and the link is still there afterwards (step 3b's snapshot)
            with pytest.raises(link.LinkError, match="unknown link method"):
                await node._home_mux.request("not-a-method", timeout=10, pad="x" * 200_000)
            assert await node._home_mux.request("ping", timeout=10) == "pong"
            # up, and still not served from the replica: nothing forwards yet (steps 4–5)
            with pytest.raises(AgentError, match="the link to kmaster .home. is up, but forwarding"):
                await c.call("msg", to=["ao-x-w"], text="hi")
        home.stop()
        assert await wait_for(lambda: not node.home_reachable(), timeout=10.0, step=0.05)
        assert await wait_for(lambda: "agent down on the home" in node.home_link["why"], timeout=10.0, step=0.05), (
            node.home_link
        )
        await home.start()
        assert await wait_for(node.home_reachable, timeout=15.0, step=0.05), node.home_link
        assert (await home.host_rpc())["links"]["laptop"]["up"] is True


async def test_the_home_notices_a_node_that_went_away(home, tmp_path, monkeypatch):
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as node:
        assert await wait_for(node.home_reachable, timeout=10.0, step=0.05)

    async def down():
        return (await home.host_rpc())["links"]["laptop"]["up"] is False

    for _ in range(100):
        if await down():
            break
        await asyncio.sleep(0.05)
    seen = (await home.host_rpc())["links"]["laptop"]
    assert seen["up"] is False and seen["why"]


async def test_a_host_the_home_has_not_authorised_is_refused_in_words(home, tmp_path, monkeypatch):
    home.write_hosts(["host1"])  # read on every use: no restart
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as node:
        assert await wait_for(lambda: node.home_link["why"].startswith("refused:"), timeout=10.0, step=0.05)
        assert "laptop is not an authorised node" in node.home_link["why"] and not node.home_reachable()
        assert "laptop" not in (await home.host_rpc())["links"]


async def test_the_name_comes_from_the_key_and_a_node_that_disagrees_is_told(home, tmp_path, monkeypatch):
    home.write_hosts(["laptop", "desk"])
    async with node_agent(tmp_path, monkeypatch, home.dial_command(host="desk")) as node:  # the key says `desk`
        assert await wait_for(lambda: node.home_link["why"].startswith("refused:"), timeout=10.0, step=0.05)
        assert "bound to desk, and the node calls itself laptop" in node.home_link["why"]


async def test_ssh_failed_and_agent_down_are_told_apart(tmp_path, monkeypatch):
    async with node_agent(tmp_path, monkeypatch, ["false"]) as node:
        assert await wait_for(lambda: node.home_link["why"].startswith("ssh failed"), timeout=10.0, step=0.05)
    empty = tmp_path / "nobody-home"
    empty.mkdir()
    cmd = ["env", f"AGENTORC_HOME={empty}", sys.executable, "-m", "sessionorc.agent", "link", "--host", "laptop"]
    async with node_agent(tmp_path, monkeypatch, cmd) as node:
        assert await wait_for(lambda: "agent down on the home" in node.home_link["why"], timeout=10.0, step=0.05)


async def test_a_node_takes_no_links_and_a_home_not_its_own_name(tmp_path, monkeypatch):
    async with node_agent(tmp_path, monkeypatch, ["false"]) as node:
        assert "not a home" in node._link_refusal("desk", {"protocol": link.PROTOCOL})
