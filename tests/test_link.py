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
async def node_agent(tmp_path, monkeypatch, command=None, name="laptop", socket=None):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    how = f"socket: {socket}" if socket is not None else f"command: {command!r}"
    (d / "hosts.yml").write_text(f"home: kmaster\nlocal:\n  name: {name}\nlink:\n  {how}\n")
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
    monkeypatch.setenv("AGENTORC_BUILD", "abc123def456")  # what a provisioned agent is started with
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as node:
        assert await wait_for(node.home_reachable, timeout=10.0, step=0.05), node.home_link
        assert node.home_link["why"] == "linked to kmaster as laptop"
        seen = await home.host_rpc()
        assert seen["mode"] == "home" and seen["links"]["laptop"]["up"] is True
        assert seen["links"]["laptop"]["build"] == "abc123def456"  # said in the hello, kept on the link state
        async with LocalClient() as c:
            mine = await c.call("host")
            assert mine["home_reachable"] is True and mine["link"]["up"] is True
            # a frame well past asyncio's 64 KiB default crosses the whole path — subprocess pipe,
            # bridge, the home's socket — and the link is still there afterwards (step 3b's snapshot)
            with pytest.raises(link.LinkError, match="unknown link method"):
                await node._home_mux.request("not-a-method", timeout=10, pad="x" * 200_000)
            assert await node._home_mux.request("ping", timeout=10) == "pong"
            # up: the mailbox call is forwarded to the home (step 5), which reads the bare id as
            # this node's session — and has no such record
            with pytest.raises(AgentError, match="no session ao-x-w@laptop"):
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


async def test_a_container_node_dials_the_homes_socket_with_no_ssh_and_survives_its_restart(
    home, tmp_path, monkeypatch
):
    """TD-057 step 3c.1: the home binds `links/<name>/link.sock` for each `nodes:` entry and the
    node opens it directly — no ssh, no bridge — and what the node holds is the *directory*, so a
    home that unlinks and re-binds its socket on restart is found again."""
    sock = home.dir / "links" / "laptop" / "link.sock"
    assert await wait_for(sock.exists, timeout=5.0, step=0.05)  # bound just after agent.sock, which start() waited on
    assert (sock.parent.stat().st_mode & 0o777) == 0o700 and (sock.stat().st_mode & 0o777) == 0o600
    async with node_agent(tmp_path, monkeypatch, socket=sock) as node:
        assert await wait_for(node.home_reachable, timeout=10.0, step=0.05), node.home_link
        assert node.home_link["why"] == "linked to kmaster as laptop"
        seen = await home.host_rpc()
        assert seen["links"]["laptop"]["up"] is True
        assert await node._home_mux.request("ping", timeout=10) == "pong"
        home.stop()
        assert await wait_for(lambda: not node.home_reachable(), timeout=10.0, step=0.05)
        assert await wait_for(lambda: node.home_link["why"].startswith("cannot connect: "), timeout=10.0, step=0.05), (
            node.home_link
        )
        assert not sock.exists()  # the home took its socket with it; the directory stayed
        await home.start()
        assert await wait_for(node.home_reachable, timeout=15.0, step=0.05), node.home_link
        assert (await home.host_rpc())["links"]["laptop"]["up"] is True
        # the socket is that node's: a second connection on it is *laptop* too, and replaces the first
        r, w = await asyncio.open_unix_connection(str(sock), limit=link.FRAME_LIMIT)
        other = link.Mux(r, w, nothing)
        run = asyncio.ensure_future(other.run())
        try:
            hello = await other.request("hello", timeout=10, protocol=link.PROTOCOL, host="laptop")
            assert hello["host"] == "laptop"
            with pytest.raises(link.LinkError, match="bound to laptop, and the node calls itself desk"):
                await other.request("hello", timeout=10, protocol=link.PROTOCOL, host="desk")
        finally:
            other.close()
            run.cancel()
    # a node that is not in `nodes:` gets no socket at all
    assert not (home.dir / "links" / "desk").exists()
    # an authorised node on an older build is refused for protocol — and the refusal is recorded on
    # the home's link state, which is what the container supervisor acts on (3c.3)
    r, w = await asyncio.open_unix_connection(str(sock), limit=link.FRAME_LIMIT)
    old_build = link.Mux(r, w, nothing)
    run = asyncio.ensure_future(old_build.run())
    try:
        with pytest.raises(link.LinkError, match="link protocol 0 here is 1"):
            await old_build.request("hello", timeout=10, protocol=0, host="laptop")
    finally:
        old_build.close()
        run.cancel()
    seen = (await home.host_rpc())["links"]["laptop"]
    assert seen["up"] is False and seen["why"].startswith("refused: link protocol 0 here is 1")


async def test_a_node_takes_no_links_and_a_home_not_its_own_name(tmp_path, monkeypatch):
    async with node_agent(tmp_path, monkeypatch, ["false"]) as node:
        assert "not a home" in node._link_refusal("desk", {"protocol": link.PROTOCOL})


# -- a node's records at the home (step 3b) -------------------------------------------------------------


def record(rid="ao-x-w", host="laptop", **kw):
    from sessionorc.models import Session

    base = dict(id=rid, name="w", kind="agent", adapter="claude-code", dir="/tmp/x", host=host, state="working")
    return Session(**{**base, **kw}).to_dict()


async def test_the_home_adopts_applies_by_owner_and_a_snapshot_is_the_truth(agent):
    """`_take_records`, without a link: adoption whole, `apply_node` on a known record, another
    host's record dropped, and a snapshot forgetting what it does not list."""
    assert agent._take_records("laptop", [record(team="grind", controllers=["ao-x-lead"])], whole=True) == 1
    held = agent.remote["laptop"]["ao-x-w"]
    assert held.team == "grind" and held.controllers == ["ao-x-lead"]  # adopted whole, home-owned fields too
    held.team = "renamed-at-home"
    agent._take_records("laptop", [record(state="idle", team="the-nodes-stale-copy")], whole=False)
    assert held.state == "idle" and held.team == "renamed-at-home"  # a report moves only what the node owns
    # a laptop cannot report on kmaster's sessions, or on anyone's but its own
    assert (
        agent._take_records(
            "laptop", [record("ao-evil", host=agent.host), record("ao-evil2", host="desk")], whole=False
        )
        == 0
    )
    assert set(agent.remote["laptop"]) == {"ao-x-w"} and "ao-evil" not in agent.sessions
    agent._take_records("laptop", [record("ao-x-other")], whole=True)  # the snapshot lacks ao-x-w
    assert set(agent.remote["laptop"]) == {"ao-x-other"}
    assert "ao-x-w@laptop" in agent._gone and not paths.remote_dir("laptop").joinpath("ao-x-w.json").exists()


async def test_another_hosts_record_is_addressed_unreachable_until_its_node_dials_and_never_acted_on(agent):
    agent._take_records("laptop", [record()], whole=True)
    async with LocalClient() as c:
        (v,) = await c.call("list")
        assert v["id"] == "ao-x-w@laptop" and v["host"] == "laptop"
        assert v["state"] == "unreachable" and v["last_state"] == "working" and v["host_link"]["up"] is False
        assert agent.remote["laptop"]["ao-x-w"].state == "working"  # an overlay on the view, never the record
        agent.links["laptop"] = {"up": True, "since": "t", "why": "linked"}
        assert (await c.call("get", id="ao-x-w@laptop"))["state"] == "working"
        assert (await c.call("seen", id="ao-x-w@laptop"))["seen_at"]  # a look is the home's to record
        agent.links["laptop"] = {"up": False, "since": "t", "why": "the lid closed"}
        with pytest.raises(AgentError, match="runs on laptop: unreachable since t — the lid closed; refused, not"):
            await c.call("kill", id="ao-x-w@laptop")
        with pytest.raises(AgentError, match="runs on laptop: unreachable since t — the lid closed; refused, not"):
            await c.call("explain", id="ao-x-w@laptop")  # a read crosses the link since 4b.1, and needs it up
    # a restarted home still has them, from `remote/laptop/`
    again = HostAgent(tmux=agent.tmux)
    assert (
        again.remote["laptop"]["ao-x-w"].seen_at
        and again._view(again.remote["laptop"]["ao-x-w"])["state"] == "unreachable"
    )


async def test_laptop_closed_its_cards_go_unreachable_and_come_back(home, hookstub, tmp_path, monkeypatch):
    """TD-057 step 3's test. The lid closes: the link drops while the node's sessions live on. The
    home's cards for that host read `unreachable` with the reason, keep what was last known, and
    lift by themselves when the node dials back in — its snapshot first."""
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as node:
        async with LocalClient() as c:
            s = await c.call("create", name="w", dir=str(tmp_path), adapter=hookstub.name)
        address = f"{s['id']}@laptop"

        async def at_home():
            async with LocalClient(sock=home.dir / "agent.sock") as h:
                return {v["id"]: v for v in await h.call("list")}.get(address)

        async def until(pred, timeout=15.0):
            for _ in range(int(timeout / 0.1)):
                v = await at_home()
                if pred(v):
                    return v
                await asyncio.sleep(0.1)
            raise AssertionError(f"never: {await at_home()}")

        v = await until(lambda v: v is not None and v["state"] != "unreachable")
        assert v["host"] == "laptop" and v["host_link"]["up"] is True and v["name"] == "w"
        monkeypatch.setattr(link, "BACKOFF_FIRST", 2.0)  # the lid stays shut long enough to be seen
        monkeypatch.setattr(link, "BACKOFF_MAX", 2.0)
        node._home_mux.close("the lid closed")
        v = await until(lambda v: v is not None and v["state"] == "unreachable")
        assert v["last_state"] in ("working", "idle") and v["host_link"]["why"]
        v = await until(lambda v: v is not None and v["state"] != "unreachable")  # nobody did anything
        assert v["host_link"]["up"] is True
        async with LocalClient() as c:
            await c.call("kill", id=s["id"])  # and a change on the node reaches the home
        await until(lambda v: v is not None and v["state"] == "exited")


async def test_a_report_after_the_first_hook_still_lands_and_a_record_that_will_not_parse_is_not_forgotten(agent):
    """Review of PR #202. `adapter_id` is set by the first hook *after* create: an identity check over
    it froze the home's copy for good. And a snapshot forgets only what it omits — a record it lists
    and the home cannot take is kept, mail and all."""
    agent._take_records("laptop", [record(adapter_id=None)], whole=True)
    agent._take_records("laptop", [record(adapter_id="tool-uuid", state="idle", name="renamed")], whole=False)
    held = agent.remote["laptop"]["ao-x-w"]
    assert (held.adapter_id, held.state, held.name) == ("tool-uuid", "idle", "renamed")
    agent._take_records("laptop", [record(dir="/tmp/another-session-entirely")], whole=True)  # refused by `apply_node`
    assert "ao-x-w" in agent.remote["laptop"] and held.dir == "/tmp/x"  # listed, so not forgotten


class FakeMux:
    closed_why = None

    def __init__(self, during=None, stall=False):
        self.during, self.stall, self.sent = during, stall, []

    async def request(self, method, timeout=None, **params):
        self.sent.append((method, params))
        if self.during:
            self.during()
        return {"taken": len(params.get("records") or [])}

    async def notify(self, method, **params):
        if self.stall:
            await asyncio.sleep(30)
        self.sent.append((method, params))

    def close(self, why="closed"):
        self.closed_why = why


async def test_a_record_created_while_the_snapshot_is_out_goes_in_the_first_report(agent, monkeypatch):
    from sessionorc.models import Session

    agent.mode, agent.home = "node", "kmaster"
    mk = lambda rid: Session(id=rid, name=rid, kind="agent", adapter="claude-code", dir="/tmp/x", host=agent.host)  # noqa: E731
    agent.sessions["ao-x-a"] = mk("ao-x-a")
    mux = FakeMux(during=lambda: agent.sessions.__setitem__("ao-x-b", mk("ao-x-b")))
    agent._home_mux = mux
    await agent._send_snapshot(mux)
    assert [r["id"] for r in mux.sent[0][1]["records"]] == ["ao-x-a"]
    await agent._report_home()
    assert mux.sent[1][0] == "report" and [r["id"] for r in mux.sent[1][1]["records"]] == ["ao-x-b"]
    agent.sessions.pop("ao-x-a"), agent.sessions.pop("ao-x-b")


async def test_a_report_that_cannot_be_written_gives_the_link_up_instead_of_stalling_the_node(agent, monkeypatch):
    from sessionorc.models import Session

    monkeypatch.setattr("sessionorc.agent.REPORT_WRITE", 0.2)
    agent.mode, agent.home = "node", "kmaster"
    agent._home_mux, agent._snapshot_sent = FakeMux(stall=True), True
    agent.sessions["ao-x-a"] = Session(
        id="ao-x-a", name="a", kind="agent", adapter="claude-code", dir="/tmp/x", host=agent.host
    )
    await asyncio.wait_for(agent._report_home(), timeout=3)  # returns: the tick is not held for `LINK_SILENCE`
    assert "could not be written within 0.2 s" in agent._home_mux.closed_why
    agent.sessions.pop("ao-x-a")


# -- acts across the link (step 4a) ---------------------------------------------------------------------


async def test_the_gate_reads_one_graph_with_every_address_from_the_homes_point_of_view(agent, tmp_path):
    """Step 4a: a remote record's `controllers` are stored as its node writes them — the home's
    lead bare-qualified `@kmaster`, the node's own sessions bare — and the gate reads them
    re-addressed, so a lead here holding `control` over a member there passes the same two-part
    check it passes today, and is then refused only for what is true: the host is unreachable."""
    async with LocalClient() as person:
        lead = await person.call("create", name="lead", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        await person.call("set_grants", id=lead["id"], add=["control"])
    agent._take_records(
        "laptop", [record(controllers=[f"{lead['id']}@{agent.host}", "ao-x-sib"], team="g")], whole=True
    )
    g = agent._graph()
    assert g["ao-x-w@laptop"] is agent.remote["laptop"]["ao-x-w"]  # the record itself, never a copy (step 5)
    assert agent._ctl(g["ao-x-w@laptop"]) == [lead["id"], "ao-x-sib@laptop"]  # read from here
    assert agent.remote["laptop"]["ao-x-w"].controllers == [f"{lead['id']}@{agent.host}", "ao-x-sib"]  # untouched
    assert (await agent.rpc_get("ao-x-w@laptop"))["controllers"] == [lead["id"], "ao-x-sib@laptop"]
    assert "ao-x-w@laptop" not in agent.sessions  # never widened
    async with LocalClient(caller=lead["id"]) as c, LocalClient(caller="ao-stranger") as stranger:
        with pytest.raises(AgentError, match="runs on laptop: unreachable since .*refused, not queued"):
            await c.call("kill", id="ao-x-w@laptop")  # gated: passed; routed: no link
        with pytest.raises(AgentError, match="needs the control grant"):
            await stranger.call("kill", id="ao-x-w@laptop")
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="unknown host desk: not under `nodes:`"):
            await person.call("create", name="x", dir=str(tmp_path), adapter="shell", host="desk")
        with pytest.raises(AgentError, match="runs on laptop: unreachable"):
            await person.call("set_stop", id="ao-x-w@laptop", run_until="+1h")  # a home edit waits for the link too
        assert agent.remote["laptop"]["ao-x-w"].run_until is None
        await person.call("kill", id=lead["id"])


class Acts:
    """A spy on the node's `act` handler: what the home routed to it."""

    def __init__(self, node, monkeypatch):
        self.taken = []
        orig = node._act

        async def spy(params):
            self.taken.append(params)
            return await orig(params)

        monkeypatch.setattr(node, "_act", spy)


async def at_home(home, address):
    async with LocalClient(sock=home.dir / "agent.sock") as h:
        return {v["id"]: v for v in await h.call("list")}.get(address)


async def until(home, address, pred, timeout=15.0):
    for _ in range(int(timeout / 0.1)):
        v = await at_home(home, address)
        if pred(v):
            return v
        await asyncio.sleep(0.1)
    raise AssertionError(f"never: {await at_home(home, address)}")


async def test_acts_are_gated_at_the_home_executed_on_the_node_and_the_verdict_returned(
    home, hookstub, tmp_path, monkeypatch
):
    """TD-057 step 4a. A `send` and a `kill` from the home on `id@laptop` run on the node and the
    reply is the record as it now stands; a caller without `control` is refused at the home before
    anything crosses (the node's handler never sees it); a home-owned edit lands on both copies."""
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as node:
        acts = Acts(node, monkeypatch)
        async with LocalClient() as c:
            w = await c.call("create", name="w", dir=str(tmp_path), adapter=hookstub.name, unattended=True)
        address = f"{w['id']}@laptop"
        await until(home, address, lambda v: v is not None and v["state"] != "unreachable")
        async with LocalClient(sock=home.dir / "agent.sock") as person:
            lead = await person.call("create", name="lead", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
            await person.call("set_grants", id=lead["id"], add=["control"])
            # a person's send: typed on the node and recorded there (a send without `wait` returns nothing,
            # here as everywhere), and the home's copy carries the `sends` entry before the reply
            assert await person.call("send", id=address, text="echo ACT-OK") is None
            assert node.sessions[w["id"]].sends[-1].text == "echo ACT-OK" and acts.taken[-1]["rpc"] == "send"
            assert (await at_home(home, address))["sends"][-1]["text"] == "echo ACT-OK"
            assert acts.taken[-1]["caller"] is None and acts.taken[-1]["params"]["id"] == w["id"]
            # the lead is nobody to it yet: refused at the home, and the node never hears of it
            n = len(acts.taken)
            async with LocalClient(sock=home.dir / "agent.sock", caller=lead["id"]) as as_lead:
                with pytest.raises(AgentError, match="not in its controllers"):
                    await as_lead.call("kill", id=address)
                assert len(acts.taken) == n and node.sessions[w["id"]].state != "exited"
                # a home-owned edit: the home's copy and the node's replica agree, each in its own address
                v = await person.call("set_controllers", id=address, add=[lead["id"]])
                assert v["controllers"] == [lead["id"]] and v["id"] == address
                assert node.sessions[w["id"]].controllers == [f"{lead['id']}@kmaster"]
                assert (await at_home(home, address))["controllers"] == [lead["id"]]
                # and now the lead's kill crosses, runs there, and the verdict comes back at once
                v = await as_lead.call("kill", id=address)
                assert v["state"] == "exited" and v["id"] == address
                assert node.sessions[w["id"]].state == "exited"
                assert acts.taken[-1]["rpc"] == "kill" and acts.taken[-1]["caller"] == f"{lead['id']}@kmaster"
                assert (await at_home(home, address))["state"] == "exited"  # applied from the reply, not a later report
                # a node's refusal comes back in words, and a remove is forgotten at both ends
                with pytest.raises(AgentError, match="laptop: .* is closed"):
                    await as_lead.call("close", id=address) and await as_lead.call("send", id=address, text="x")
                await as_lead.call("remove", id=address)
                assert w["id"] not in node.sessions and await at_home(home, address) is None
            await person.call("kill", id=lead["id"])


async def test_a_node_refuses_an_act_for_a_record_that_is_not_its_own(agent):
    """The node's side of *routes acts only to records whose host is that node's* (§4.4a): an `act`
    naming another host's record, or a create for another host, is refused in words and nothing
    runs — whatever the home (or anything on the link) sent."""
    agent.mode, agent.home = "node", "kmaster"
    with pytest.raises(link.LinkError, match="ao-x-w@desk is not on .*: not my host"):
        await agent._act({"rpc": "kill", "params": {"id": "ao-x-w@desk"}, "caller": None})
    with pytest.raises(link.LinkError, match="a create for desk is not .*: not my host"):
        await agent._act({"rpc": "create", "params": {"name": "w", "dir": "/tmp", "host": "desk"}, "caller": None})
    with pytest.raises(link.LinkError, match="'msg' is not an act a node executes"):
        await agent._act({"rpc": "msg", "params": {"to": ["x"], "text": "hi"}, "caller": None})
    with pytest.raises(link.LinkError, match="no session ao-nope"):
        await agent._act({"rpc": "kill", "params": {"id": "ao-nope"}, "caller": None})


async def test_an_act_on_an_unreachable_host_is_refused_and_never_runs_when_the_link_returns(
    home, hookstub, tmp_path, monkeypatch
):
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as node:
        async with LocalClient() as c:
            w = await c.call("create", name="w", dir=str(tmp_path), adapter=hookstub.name, unattended=True)
        address = f"{w['id']}@laptop"
        await until(home, address, lambda v: v is not None and v["state"] != "unreachable")
        monkeypatch.setattr(link, "BACKOFF_FIRST", 2.0)
        monkeypatch.setattr(link, "BACKOFF_MAX", 2.0)
        node._home_mux.close("the lid closed")
        await until(home, address, lambda v: v is not None and v["state"] == "unreachable")
        async with LocalClient(sock=home.dir / "agent.sock") as person:
            with pytest.raises(AgentError, match="runs on laptop: unreachable since .*; refused, not queued"):
                await person.call("kill", id=address)
            with pytest.raises(AgentError, match="runs on laptop: unreachable"):
                await person.call("host_dir", host="laptop", dir=str(tmp_path))
        await until(home, address, lambda v: v is not None and v["state"] != "unreachable")
        await asyncio.sleep(0.5)  # nothing was queued: the session is still there after the link came back
        assert node.sessions[w["id"]].state != "exited"
        async with LocalClient(sock=home.dir / "agent.sock") as person:
            assert (await person.call("host_dir", host="laptop", dir=str(tmp_path)))["exists"] is True
            assert (await person.call("host_dir", host="laptop", dir=str(tmp_path / "nope")))["exists"] is False


async def test_a_create_with_a_host_lands_on_the_node_and_is_adopted_at_the_home(home, hookstub, tmp_path, monkeypatch):
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as node:
        assert await wait_for(node.home_reachable, timeout=10.0, step=0.05)
        async with LocalClient(sock=home.dir / "agent.sock") as person:
            lead = await person.call("create", name="lead", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
            await person.call("set_grants", id=lead["id"], add=["control"])
            async with LocalClient(sock=home.dir / "agent.sock", caller=lead["id"]) as as_lead:
                v = await as_lead.call(
                    "create", name="w2", dir=str(tmp_path), adapter=hookstub.name, unattended=True, host="laptop"
                )
            assert v["id"].endswith("@laptop") and v["host"] == "laptop"
            rid = v["id"].removesuffix("@laptop")
            assert rid in node.sessions and all(s["id"] != rid for s in await person.call("list"))  # landed there
            assert node.sessions[rid].controllers == [f"{lead['id']}@kmaster"]  # the creator, as the node addresses it
            assert v["controllers"] == [lead["id"]]  # and as the home does
            assert (await person.call("get", id=v["id"]))["state"] != "unreachable"  # adopted before the reply
            check = await person.call("name_check", dir=str(tmp_path), name="w2", host="laptop")
            assert check["verdict"] == "live" and check["holder"] == f"{rid}@laptop"  # addressed, like every reply
            await person.call("kill", id=v["id"])
            await person.call("kill", id=lead["id"])


# -- reads of a pane across the link, and a permission answered over it (step 4b.1) ------------------------


async def test_a_read_of_another_hosts_pane_crosses_as_a_read_ungated_and_only_ever_a_read(agent):
    """`tail` and `explain` on `id@laptop` go to that node as the `read` link method — never `act` —
    for any caller, the gate being for acts (§9 invariant 11); the reply comes back with its
    addresses in the home's form and nothing else touched; a link that is down refuses it."""

    class Reads(FakeMux):
        async def request(self, method, timeout=None, **params):
            self.sent.append((method, params))
            if params["rpc"] == "tail":
                return ["line one", "ao-x-w is not an address here"]
            return {"id": "ao-x-w", "state": "working", "tail": ["ao-x-w"], "reason": "no screen rule matched"}

    agent._take_records("laptop", [record(controllers=["ao-x-lead"])], whole=True)
    agent._link_muxes["laptop"] = mux = Reads()
    agent.links["laptop"] = {"up": True, "since": "t", "why": "linked"}
    async with LocalClient(caller="ao-stranger") as stranger:
        assert await stranger.call("tail", id="ao-x-w@laptop", lines=5) == ["line one", "ao-x-w is not an address here"]
        x = await stranger.call("explain", id="ao-x-w@laptop")
        assert x["id"] == "ao-x-w@laptop" and x["tail"] == ["ao-x-w"]  # the id addressed; a screen line untouched
        assert mux.sent == [
            ("read", {"rpc": "tail", "params": {"id": "ao-x-w", "lines": 5}}),
            ("read", {"rpc": "explain", "params": {"id": "ao-x-w"}}),
        ]
        with pytest.raises(AgentError, match="needs the control grant"):
            await stranger.call("kill", id="ao-x-w@laptop")  # reading is not acting
        with pytest.raises(AgentError, match="no session ao-x-nope@laptop"):
            await stranger.call("tail", id="ao-x-nope@laptop")
        del agent._link_muxes["laptop"]
        agent.links["laptop"] = {"up": False, "since": "t", "why": "the lid closed"}
        with pytest.raises(
            AgentError, match="runs on laptop: unreachable since t — the lid closed; refused, not queued"
        ):
            await stranger.call("tail", id="ao-x-w@laptop")
    assert len(mux.sent) == 2  # nothing was queued for the link's return


async def test_a_node_serves_the_home_a_read_and_nothing_else_through_it(agent):
    """The node's end: `read` runs `NODE_READS` and only them — an acting method asked for through
    it is refused, whatever sent it — and a record of another host is not this node's to read."""
    agent.mode, agent.home = "node", "kmaster"
    for rpc in ("kill", "send", "set_grants", "decide", "msg"):
        with pytest.raises(link.LinkError, match=f"'{rpc}' is not a read a node serves the home"):
            await agent._from_home("read", {"rpc": rpc, "params": {"id": "ao-x-w"}})
    with pytest.raises(link.LinkError, match="ao-x-w@desk is not on .*: not my host"):
        await agent._from_home("read", {"rpc": "tail", "params": {"id": "ao-x-w@desk"}})
    with pytest.raises(link.LinkError, match="no session ao-nope"):
        await agent._from_home("read", {"rpc": "explain", "params": {"id": "ao-nope"}})
    with pytest.raises(link.LinkError, match="'tail' is not an act a node executes"):
        await agent._from_home("act", {"rpc": "tail", "params": {"id": "ao-x-w"}, "caller": None})


async def test_the_home_reads_a_nodes_pane_and_a_stranger_reads_it_too(home, tmp_path, monkeypatch):
    """TD-057 step 4b.1, end to end: `ao tail` and `ao explain` at the home on a session of the
    node return that node's pane; a session with no grant reads it as well and still cannot act."""
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as _node:
        async with LocalClient() as c:
            w = await c.call("create", name="w", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
            await c.call("send", id=w["id"], text="echo PANE-$((6*7))")
        address = f"{w['id']}@laptop"
        await until(home, address, lambda v: v is not None and v["state"] != "unreachable")
        sock = home.dir / "agent.sock"
        async with LocalClient(sock=sock) as person, LocalClient(sock=sock, caller="ao-stranger") as stranger:
            for _ in range(100):
                lines = await person.call("tail", id=address, lines=20)
                if any("PANE-42" in ln for ln in lines):
                    break
                await asyncio.sleep(0.1)
            assert any("PANE-42" in ln for ln in lines), lines
            x = await stranger.call("explain", id=address)
            assert x["id"] == address and x["adapter"] == "shell" and any("PANE-42" in ln for ln in x["tail"])
            with pytest.raises(AgentError, match="needs the control grant"):
                await stranger.call("kill", id=address)
            await person.call("kill", id=address)


async def test_a_permission_waiting_on_a_node_is_answered_at_the_home_and_says_so_when_the_link_drops(
    home, hookstub, tmp_path, monkeypatch
):
    """§4.4a "Permission prompts follow the same line". The hook blocks on its node; the home's card
    reads `needs-you` and the person's answer there reaches the node's waiter, which returns it to
    the hook. With the link down: the card's pending says *host unreachable*, `decide` is refused,
    never queued, and the hook keeps blocking until its own timeout."""
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as node:
        async with LocalClient() as c:
            w = await c.call("create", name="w", dir=str(tmp_path), adapter=hookstub.name, unattended=True)
        address = f"{w['id']}@laptop"
        await until(home, address, lambda v: v is not None and v["state"] != "unreachable")

        async def hook(tool_use_id, wait):
            async with LocalClient() as c:  # the hook script's call, on the node's own socket
                return await c.call(
                    "hook",
                    session=w["id"],
                    kind="permission",
                    text="Bash: git push",
                    tool_use_id=tool_use_id,
                    wait_seconds=wait,
                )

        blocked = asyncio.ensure_future(hook("tu-1", 20))
        v = await until(
            home, address, lambda v: v is not None and (v.get("pending") or {}).get("tool_use_id") == "tu-1"
        )
        assert v["state"] == "needs-you" and v["pending"]["kind"] == "permission"
        async with LocalClient(sock=home.dir / "agent.sock") as person:
            await person.call("decide", id=address, tool_use_id="tu-1", behavior="allow")
            assert await asyncio.wait_for(blocked, timeout=10) == {"behavior": "allow", "reason": None}
            await until(home, address, lambda v: v is not None and v["state"] == "working")
            # the lid closes mid-prompt
            blocked = asyncio.ensure_future(hook("tu-2", 4))
            await until(
                home, address, lambda v: v is not None and (v.get("pending") or {}).get("tool_use_id") == "tu-2"
            )
            home.write_hosts([])  # the link stays down: the dialer's backoff was fixed when it started
            node._home_mux.close("the lid closed")
            v = await until(home, address, lambda v: v is not None and v["state"] == "unreachable")
            assert v["last_state"] == "needs-you" and v["pending"]["host_unreachable"] is True
            with pytest.raises(AgentError, match="runs on laptop: unreachable since .*; refused, not queued"):
                await person.call("decide", id=address, tool_use_id="tu-2", behavior="allow")
            assert not blocked.done()  # still blocking on the node
            assert await asyncio.wait_for(blocked, timeout=10) is None  # its own timeout: the terminal dialog
        assert node.sessions[w["id"]].pending.kind == "question"


# -- occupancy across the home and its container nodes (step 3c.4) ----------------------------------------


async def test_a_container_nodes_session_holds_the_checkout_at_the_home_and_the_reverse(agent, tmp_path, monkeypatch):
    """Design §4.4a "A container node": the checkout is one directory here and there, so a record
    of the node's over it is an occupant at the home — `create` here is refused by the anchor
    rule, and a create routed to the node is refused here before it crosses. A machine node's
    record over the same path is another directory and is not read."""
    from sessionorc import containers

    checkout = tmp_path / "repo"
    (checkout / ".devcontainer").mkdir(parents=True)
    (checkout / ".devcontainer" / "devcontainer.json").write_text('{"image": "python:3.12"}')
    hosts_yml = paths.home() / "hosts.yml"
    hosts_yml.write_text(
        f"local:\n  name: {agent.host}\nnodes:\n  cm:\n    container: {{devcontainer: {checkout}}}\n  laptop: {{}}\n"
    )
    assert "cm" in containers.container_nodes()
    agent.links["cm"] = {"up": True, "since": "t", "why": "linked"}
    agent._take_records("cm", [record("ao-repo-w", host="cm", dir=str(checkout), kind="interactive")], whole=True)
    agent._take_records(
        "laptop", [record("ao-repo-l", host="laptop", dir=str(checkout), kind="interactive")], whole=True
    )
    async with LocalClient() as c:
        assert (await c.call("occupancy", dir=str(checkout)))["occupants"] == ["ao-repo-w@cm (working)"]
        with pytest.raises(AgentError, match="already has agent session ao-repo-w@cm .working.; anchor rule"):
            await c.call("create", name="x", dir=str(checkout), adapter="claude-code")
        # the link down: a blip, or a stopped container whose sessions are dead — it holds nothing here
        agent.links["cm"] = {"up": False, "since": "t", "why": "closed by the other end"}
        assert (await c.call("occupancy", dir=str(checkout)))["occupants"] == []
        agent.links["cm"] = {"up": True, "since": "t", "why": "linked"}
        s = await c.call(
            "create", name="sh", dir=str(checkout), adapter="shell", argv=["bash", "--norc"]
        )  # shells never count
        await c.call("kill", id=s["id"])
        # the reverse: a home session holds it, and a create for the container is refused here
        agent._take_records(
            "cm", [record("ao-repo-w", host="cm", dir=str(checkout), kind="interactive", state="exited")], whole=True
        )
        mine = await c.call("create", name="here", dir=str(checkout), adapter="shell", argv=["bash", "--norc"])
        agent.sessions[mine["id"]].adapter = "claude-code"  # an agent session, as far as the rule is concerned
        with pytest.raises(AgentError, match=f"already has agent session {mine['id']} .working.; anchor rule"):
            await c.call("create", name="y", dir=str(checkout), adapter="claude-code", host="cm")
        # a worktree of it is another directory: not held — and resolved as `create` resolves it, from the
        # main checkout, so a `dir` inside the repo finds the same place (review of PR #215)
        subprocess.run(["git", "-C", str(checkout), "init", "-q"], check=True)
        (checkout / "sub").mkdir()
        agent.links["cm"] = {"up": False, "since": "t", "why": "closed by the other end"}
        with pytest.raises(AgentError, match="unreachable"):
            await c.call("create", name="y", dir=str(checkout), adapter="claude-code", host="cm", worktree="y")
        wt = checkout / ".claude" / "worktrees" / "y"
        wt.mkdir(parents=True)
        held = await c.call("create", name="in-wt", dir=str(wt), adapter="shell", argv=["bash", "--norc"])
        agent.sessions[held["id"]].adapter = "claude-code"
        with pytest.raises(AgentError, match=f"already has agent session {held['id']}"):
            await c.call("create", name="y", dir=str(checkout / "sub"), adapter="claude-code", host="cm", worktree="y")
        agent.sessions[held["id"]].adapter = "shell"
        await c.call("kill", id=held["id"])
        with pytest.raises(AgentError, match="unreachable"):  # a machine node: its path is another directory
            await c.call("create", name="y", dir=str(checkout), adapter="claude-code", host="laptop")
        agent.sessions[mine["id"]].adapter = "shell"
        await c.call("kill", id=mine["id"])


# -- mail across hosts (step 5) ---------------------------------------------------------------------------


async def test_a_forwarded_wait_blocks_at_the_home_as_the_nodes_session_and_its_token_cancels_it(agent):
    """The home's end of `forward` (§4.4a "Mail across hosts"): the call runs under the caller's
    cross-host identity, a `wait` is registered like any other, and the node's `cancel` ends it —
    no ghost wait is charged a wake here."""
    from conftest import wait_for

    agent._take_records("laptop", [record(kind="interactive", unattended=True)], whole=True)
    call = {"rpc": "wait", "params": {"timeout": 30, "scope": "all"}, "caller": "ao-x-w", "token": "t1"}
    task = asyncio.ensure_future(agent._forwarded("laptop", call))
    assert await wait_for(lambda: agent.blocked_in_wait("ao-x-w@laptop"), timeout=5.0, step=0.05)
    assert "t1" in agent._forwarded_calls
    agent._cancel_forwarded("t1")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not agent._waits and not agent._forwarded_calls
    # a person's forwarded call is a person here, and a node session's is `id@node`
    resp = await agent._forwarded("laptop", {"rpc": "inbox", "params": {}, "caller": None, "token": ""})
    assert resp["result"]["id"] == "person"
    resp = await agent._forwarded("laptop", {"rpc": "inbox", "params": {}, "caller": "ao-x-w", "token": ""})
    assert resp["result"]["id"] == "ao-x-w@laptop"


async def test_a_node_cancels_its_forwarded_call_at_the_home_by_token(agent):
    class Held(FakeMux):
        async def request(self, method, timeout=None, **params):
            self.sent.append((method, params))
            await asyncio.sleep(30)

    agent.mode, agent.home = "node", "kmaster"
    agent._home_mux = mux = Held()
    task = asyncio.ensure_future(agent._forward(7, "wait", {"timeout": 30}, "ao-w"))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert mux.sent[0][0] == "forward" and mux.sent[1] == ("cancel", {"token": mux.sent[0][1]["token"]})
    agent._home_mux = None
    with pytest.raises(Exception, match="unreachable"):
        await agent._forward(8, "msg", {}, "ao-w")


async def test_mail_crosses_the_link_both_ways_and_a_wait_from_the_node_wakes_on_it(
    home, hookstub, tmp_path, monkeypatch
):
    """TD-057 step 5, on one machine. A grinder on the node mails its lead at the home and reads
    the reply; a `wait` from the node blocks at the home and returns on that mail; the node
    session's act on a home session is gated at the home over the one graph and runs there;
    mail to the node while its link is down lands at the home and says so."""
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as node:
        async with LocalClient() as c:
            w = await c.call("create", name="w", dir=str(tmp_path), adapter=hookstub.name, unattended=True)
        address = f"{w['id']}@laptop"
        await until(home, address, lambda v: v is not None and v["state"] != "unreachable")
        sock = home.dir / "agent.sock"
        async with LocalClient(sock=sock) as person:
            lead = await person.call(
                "create", name="lead", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"], unattended=True
            )
            await person.call("set_grants", id=lead["id"], add=["control"])
            await person.call("set_controllers", id=address, add=[lead["id"]])
            lead_there = f"{lead['id']}@kmaster"  # the lead, as the node addresses it
            async with LocalClient(caller=w["id"]) as as_w, LocalClient(sock=sock, caller=lead["id"]) as as_lead:
                # up the graph: the member mails its controller, forwarded, gated and landed at the home
                got = await as_w.call("msg", to=[lead_there], text="claimed TD-1", kind="note")
                assert got["delivered"] == [lead_there] and got["entry"]["from"] == w["id"]  # in the node's form
                held = await person.call("inbox", id=lead["id"])
                assert held["entries"][-1]["from"] == address and held["entries"][-1]["text"] == "claimed TD-1"
                # a wait from the node blocks at the home and returns when the lead's reply lands
                waiting = asyncio.ensure_future(as_w.call("wait", timeout=20, scope="all"))
                await asyncio.sleep(0.5)
                reply = await as_lead.call(
                    "msg", to=[address], text="go ahead", kind="reply", reply_to=held["entries"][-1]["id"]
                )
                assert reply["delivered"] == [address] and reply["unreachable"] == []
                woke = await asyncio.wait_for(waiting, timeout=15)
                assert woke["wake"] and woke["wake"]["cause"] == "mail"
                assert woke["mail"][0]["from"] == lead_there and woke["mail"][0]["from_role"] == "controller"
                # the unread line rides every reply the home answers for the node's session, until
                # it reads — and since 4b.2 a read the node serves alone too, from the count the
                # home pushes with the record's intent (the inbox itself never leaves the home)
                from sessionorc import client as clientmod

                await as_w.call("msg", to=[lead_there], text="one more", kind="note")
                assert clientmod.last_mail == {"unread": 1, "wake_budget_spent": False}
                await as_w.call("list")
                assert clientmod.last_mail == {"unread": 1, "wake_budget_spent": False}
                assert not node.sessions[w["id"]].inbox  # a hint, never the mailbox
                mine = await as_w.call("inbox")
                assert mine["id"] == w["id"] and mine["entries"][-1]["from"] == lead_there
                assert mine["entries"][-1]["read_at"] and (await person.call("get", id=address))["unread"] == 0
                assert await wait_for(lambda: node._mail_hints[w["id"]][0] == 0, timeout=5.0, step=0.05)
                await as_w.call("list")
                assert clientmod.last_mail is None  # read: the push said so
                # a node session acting on a home session: gated at the home over one graph, both halves
                with pytest.raises(AgentError, match="needs the control grant"):
                    await as_w.call("send", id=lead_there, text="echo NO")
                await person.call("set_grants", id=address, add=["control"])
                with pytest.raises(AgentError, match="not in its controllers"):
                    await as_w.call("send", id=lead_there, text="echo NO")
                await person.call("set_controllers", id=lead["id"], add=[address])
                await as_w.call("send", id=lead_there, text="echo FROM-NODE")
                assert (await person.call("get", id=lead["id"]))["sends"][-1]["from"] == address
                # a person at the node reaches the org's person inbox, and mails as a person
                async with LocalClient() as at_node:
                    assert (await at_node.call("inbox"))["id"] == "person"
                    got = await at_node.call("msg", to=[lead_there], text="from the node's terminal")
                    assert got["entry"]["from"] == "person"
                # the lid closes: mail to the node lands at the home and says so; read on return
                monkeypatch.setattr(link, "BACKOFF_FIRST", 2.0)
                monkeypatch.setattr(link, "BACKOFF_MAX", 2.0)
                node._home_mux.close("the lid closed")
                await until(home, address, lambda v: v is not None and v["state"] == "unreachable")
                got = await as_lead.call("msg", to=[address], text="while you were away")
                assert got["unreachable"] == [address] and got["delivered"] == [address]
                assert (await person.call("get", id=address))["unread"] == 1
                with pytest.raises(AgentError, match="unreachable"):
                    await as_w.call("inbox")  # refused at the node, not queued
                await until(home, address, lambda v: v is not None and v["state"] != "unreachable")
                assert await wait_for(node.home_reachable, timeout=15.0, step=0.05)
                mine = await as_w.call("inbox", unread=True)
                assert [e["text"] for e in mine["entries"]] == ["while you were away"]
            await person.call("kill", id=lead["id"])


async def test_a_persons_forwarded_act_reaches_only_the_nodes_own_records(home, hookstub, tmp_path, monkeypatch):
    """Security read of PR #217: a person at a node runs `ao` against `<home session>@kmaster`; the
    home rewrote that to its own bare id and the person gate passed it — controllers and grants of
    any session in the org, from any laptop. §4.4a: a person's request arriving over a link may act
    only on that node's records. Reads stay the org's, and a person may still message anyone."""
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as _node:
        async with LocalClient() as c:
            w = await c.call("create", name="w", dir=str(tmp_path), adapter=hookstub.name, unattended=True)
        await until(home, f"{w['id']}@laptop", lambda v: v is not None and v["state"] != "unreachable")
        async with LocalClient(sock=home.dir / "agent.sock") as at_home:
            lead = await at_home.call("create", name="lead", dir=str(tmp_path), adapter="shell", argv=["bash"])
        async with LocalClient() as person_at_laptop:
            for rpc, extra in (
                ("set_controllers", {"add": ["ao-anyone"]}),
                ("set_grants", {"add": ["control"]}),
                ("set_mode", {"unattended": True}),
                ("inbox_delete", {"msg": "m-none"}),
            ):
                with pytest.raises(AgentError, match=f"a person at laptop may {rpc} only laptop's sessions"):
                    await person_at_laptop.call(rpc, id=f"{lead['id']}@kmaster", **extra)
            # a person's acts on panes never leave the node: a foreign id is simply no session here —
            # and nor do its reads of a pane (4b.1 routes those from the home only)
            with pytest.raises(AgentError, match="no session"):
                await person_at_laptop.call("kill", id=f"{lead['id']}@kmaster")
            for read in ("tail", "explain"):
                with pytest.raises(AgentError, match="no session"):
                    await person_at_laptop.call(read, id=f"{lead['id']}@kmaster")
            # the two reads the same rule covers: a home lead's mail bodies, and a `wait` over the org
            with pytest.raises(AgentError, match="a person at laptop may inbox only laptop's sessions"):
                await person_at_laptop.call("inbox", id=f"{lead['id']}@kmaster")
            seen = await person_at_laptop.call("wait", timeout=0.5, scope="all")
            assert all(v.get("host") == "laptop" for v in seen.get("changed") or [])
            # the person inbox names no session, and stays the person's from anywhere (review of #219)
            async with LocalClient(caller=w["id"]) as as_w:
                sent = await as_w.call("msg", to=["person"], text="for you")
            theirs = await person_at_laptop.call("inbox")
            assert sent["entry"]["id"] in [e["id"] for e in theirs["entries"]]
            gone = await person_at_laptop.call("inbox_delete", msg=sent["entry"]["id"])
            assert gone["deleted"] == sent["entry"]["id"]
            with pytest.raises(AgentError, match="acts on its own sessions only"):  # refused at the node itself
                await person_at_laptop.call("create", name="x", dir=str(tmp_path), adapter="shell", host="kmaster")
            # nor may a person at the node read the home's files through it (4b.3: `host_files`)
            with pytest.raises(AgentError, match="is a node of kmaster"):
                await person_at_laptop.call("host_files", host="kmaster", dir=str(tmp_path), paths=["x"])
            # the node's own record: the same edit, forwarded and served
            got = await person_at_laptop.call("set_controllers", id=w["id"], add=[f"{lead['id']}@kmaster"])
            assert got["controllers"] == [f"{lead['id']}@kmaster"]
            # …and a person may still message anyone (a read of a home record is not served here at all:
            # a node's replica holds its own host's records only)
            assert (await person_at_laptop.call("msg", to=[f"{lead['id']}@kmaster"], text="hi"))["delivered"]
        async with LocalClient(sock=home.dir / "agent.sock") as at_home:
            assert (await at_home.call("get", id=lead["id"]))["controllers"] == []  # untouched
            await at_home.call("kill", id=lead["id"])


# -- the home's intent reaches the node's replica, and the node's own arrivals reach the home (4b.2) ------


async def test_the_home_pushes_each_record_its_intent_and_its_unread_count_and_never_its_mail(agent):
    """`intent` (§4.4a "The home pushes each node its records' policy fields", step 4b.2): once
    after the snapshot, then only what changed; the home-owned policy fields in the node's own
    address form, the unread count beside them — and never an inbox, outbox, thread, wake or
    message body."""
    from sessionorc.agent import INTENT_FIELDS
    from sessionorc.models import MailEntry

    agent._take_records(
        "laptop", [record(controllers=[f"ao-lead@{agent.host}", "ao-x-sib"], run_until=None)], whole=True
    )
    held = agent.remote["laptop"]["ao-x-w"]
    held.inbox = [
        MailEntry(id="m-1", from_="person", to=["ao-x-w@laptop"], at="2026-09-18T10:00:00Z", kind="note", text="SECRET")
    ]
    agent._link_muxes["laptop"] = mux = FakeMux()
    agent.links["laptop"] = {"up": True, "since": "t", "why": "linked"}
    await agent._push_changes()
    assert mux.sent == []  # nothing before the link's snapshot
    agent._intent_sent["laptop"] = {}  # what `snapshot` does
    await agent._push_changes()
    ((method, params),) = mux.sent
    (item,) = params["records"]
    assert method == "intent" and item["id"] == "ao-x-w" and item["host"] == "laptop" and item["unread"] == 1
    assert item["controllers"] == [f"ao-lead@{agent.host}", "ao-x-sib"]  # the node's form, as stored
    assert set(item) <= INTENT_FIELDS | {"id", "host", "unread", "wake_budget_spent"}
    for never in ("inbox", "outbox", "threads", "wakes", "mail_decided", "sends", "state", "tail"):
        assert never not in item
    assert "SECRET" not in str(mux.sent)
    await agent._push_changes()
    assert len(mux.sent) == 1  # unchanged: nothing more
    held.run_until, held.inbox = "2026-09-18T12:00:00Z", []
    await agent._push_changes()
    assert mux.sent[1][1]["records"][0]["run_until"] == "2026-09-18T12:00:00Z"
    assert mux.sent[1][1]["records"][0]["unread"] == 0
    del agent._link_muxes["laptop"]


async def test_a_node_takes_the_homes_intent_by_owner_and_keeps_the_count_as_a_hint(agent):
    from sessionorc.models import Session

    agent.mode, agent.home = "node", "kmaster"
    s = Session(id="ao-x-w", name="w", kind="interactive", adapter="claude-code", dir="/tmp/x", host=agent.host)
    s.unattended, s.run_until, s.wrapup_sent_at, s.state = True, "2026-09-18T10:00:00Z", "2026-09-18T10:00:01Z", "idle"
    agent.sessions[s.id] = s
    try:
        await agent._from_home(
            "intent",
            {
                "records": [
                    {
                        "id": s.id, "host": agent.host, "team": "grind", "run_until": "2026-09-18T11:00:00Z",
                        "controllers": ["ao-lead@kmaster"], "unread": 3, "wake_budget_spent": True,
                        "inbox": [{"id": "m-1", "from": "person", "text": "SECRET"}], "state": "exited",
                    },
                    {"id": s.id, "host": "desk", "team": "not-mine"},
                ]
            },
        )  # fmt: skip
        assert (s.team, s.controllers, s.run_until) == ("grind", ["ao-lead@kmaster"], "2026-09-18T11:00:00Z")
        assert s.wrapup_sent_at is None  # a new stop time is a new run, as `set_stop` has it
        assert s.state == "idle" and s.inbox == []  # nothing the node owns, and never the mailbox
        assert agent._mail_hints[s.id] == (3, True)
        async with LocalClient(caller=s.id) as c:
            from sessionorc import client as clientmod

            await c.call("list")  # a read this node serves alone carries the line
            assert clientmod.last_mail == {"unread": 3, "wake_budget_spent": True}
    finally:
        agent.sessions.pop(s.id)


async def test_a_nodes_derived_reports_go_to_the_home_while_linked_and_are_not_derived_while_it_is_not(
    agent, monkeypatch
):
    """`_derive_reports` on a node (step 4b.2): what it derives is sent as `derived` and the replica
    is not written — the push brings the home's copy back; with the link down nothing is derived."""
    from datetime import UTC, datetime

    from sessionorc import reports
    from sessionorc.models import ProgressEntry, Session

    agent.mode, agent.home = "node", "kmaster"
    s = Session(id="ao-x-w", name="w", kind="interactive", adapter="claude-code", dir="/tmp", host=agent.host)
    s.git = {"branch": "td9-x"}
    agent.sessions[s.id] = s
    monkeypatch.setattr(reports, "holds_directory", lambda _s: {s.id})
    monkeypatch.setattr(
        reports,
        "derive",
        lambda *a: ([ProgressEntry(ref="TD-009", source="derived", branch="td9-x")], [], ["TD-008"]),
    )
    try:
        agent._home_mux, agent.home_link = FakeMux(), {"up": False, "since": "t", "why": "the lid closed"}
        await agent._derive_reports_inner(datetime.now(UTC))
        assert agent._home_mux.sent == [] and s.id not in agent._derived_at  # nothing derived offline
        agent.home_link = {"up": True, "since": "t", "why": "linked"}
        await agent._derive_reports_inner(datetime.now(UTC))
        ((method, params),) = agent._home_mux.sent
        assert method == "derived" and params["id"] == s.id and params["retire"] == ["TD-008"]
        assert params["progress"][0]["ref"] == "TD-009" and params["progress"][0]["source"] == "derived"
        assert params["progress"][0]["branch"] == "td9-x" and s.progress == []  # the replica is not written
    finally:
        agent.sessions.pop(s.id)
        agent._home_mux = None


async def test_the_home_takes_a_nodes_derived_reports_for_that_nodes_records_only(agent):
    from sessionorc.models import ProgressEntry

    agent._take_records(
        "laptop",
        [record(progress=[ProgressEntry(ref="TD-008", source="derived", branch="td8-gone").to_dict()])],
        whole=True,
    )
    got = await agent._take_derived(
        "laptop",
        {
            "id": "ao-x-w",
            "progress": [{"ref": "TD-009", "status": "claimed", "source": "derived", "branch": "td9-x"}],
            "findings": [{"ref": "TD-010", "source": "derived"}],
            "retire": ["TD-008"],
        },
    )
    held = agent.remote["laptop"]["ao-x-w"]
    assert got == {"changed": True} and [e.ref for e in held.progress] == ["TD-009"]
    assert held.progress[0].branch == "td9-x" and [f.ref for f in held.findings] == ["TD-010"]
    with pytest.raises(link.LinkError, match="never declared"):
        await agent._take_derived("laptop", {"id": "ao-x-w", "progress": [{"ref": "TD-11", "source": "declared"}]})
    with pytest.raises(link.LinkError, match="no such record"):
        await agent._take_derived("desk", {"id": "ao-x-w", "progress": []})  # another link's host


async def test_a_closed_home_record_is_superseded_by_the_nodes_new_session_of_the_same_id(agent):
    """§4.4a, §9 invariant 12: a report of an id the home holds closed, and that disagrees on
    identity, is a new session — it replaces the closed record in place. A live record that
    disagrees stays refused, and logged."""
    agent._take_records("laptop", [record(state="closed", dir="/tmp/old", team="old")], whole=True)
    agent._take_records("laptop", [record(dir="/tmp/new", controllers=["ao-x-lead"], team="new")], whole=False)
    held = agent.remote["laptop"]["ao-x-w"]
    assert (held.dir, held.state, held.team, held.controllers) == ("/tmp/new", "working", "new", ["ao-x-lead"])
    agent._take_records("laptop", [record(dir="/tmp/third")], whole=False)  # the new one is live: refused
    assert agent.remote["laptop"]["ao-x-w"].dir == "/tmp/new"


async def test_the_replica_is_repaired_from_the_home_on_every_new_link(home, hookstub, tmp_path, monkeypatch):
    """The push after a snapshot (step 4b.2): a replica that drifted from the home — a home restored
    from an old store, an act whose second half failed — takes the home's copy when the link is
    next made, without anyone editing anything."""
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as node:
        async with LocalClient() as c:
            w = await c.call("create", name="w", dir=str(tmp_path), adapter=hookstub.name, unattended=True)
        address = f"{w['id']}@laptop"
        await until(home, address, lambda v: v is not None and v["state"] != "unreachable")
        rec = node.sessions[w["id"]]
        rec.team, rec.unattended = "drifted", False
        node.store.save(rec)
        node._home_mux.close("a blip")
        assert await wait_for(lambda: rec.team == "" and rec.unattended is True, timeout=15.0, step=0.05)
        assert (await at_home(home, address))["team"] == ""  # the home's copy never took the drift


async def test_stopping_policies_run_on_the_node_with_the_link_down_and_both_owners_stand_after(
    home, hookstub, tmp_path, monkeypatch
):
    """§4.4a "Policies that stop run on the node … offline included", and the example under *Each
    field has one owner*: with the link down the node wraps its worker up at `run_until` and kills
    it after the grace; on reconnect the node's `exited` and `wrapup_sent_at` stand at the home
    beside the home's `run_until`."""
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr("sessionorc.agent.WRAPUP_GRACE", timedelta(seconds=1))
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as node:
        at = (datetime.now(UTC) + timedelta(seconds=3)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        async with LocalClient() as c:
            w = await c.call(
                "create", name="w", dir=str(tmp_path), adapter=hookstub.name, unattended=True, run_until=at,
                wrapup_prompt="wrap up now",
            )  # fmt: skip
        address = f"{w['id']}@laptop"
        v = await until(home, address, lambda v: v is not None and v["state"] != "unreachable")
        assert v["run_until"] == at
        home.write_hosts([])  # the lid closes, and stays closed
        node._home_mux.close("the lid closed")
        await until(home, address, lambda v: v is not None and v["state"] == "unreachable")
        rec = node.sessions[w["id"]]
        assert await wait_for(lambda: rec.state == "exited" and rec.wrapup_sent_at, timeout=20.0, step=0.1)
        home.write_hosts(["laptop"])  # the lid opens
        v = await until(home, address, lambda v: v is not None and v["state"] == "exited", timeout=20.0)
        assert v["wrapup_sent_at"] == rec.wrapup_sent_at and v["run_until"] == at


async def test_a_persons_offline_create_is_adopted_with_its_controllers_when_the_link_returns(
    home, hookstub, tmp_path, monkeypatch
):
    """§4.4a "When a host cannot reach home": a person at that host creates a session with
    `controllers`; it is reported and adopted when the link returns, and the home's card carries
    them addressed as the home reads them."""
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as node:
        assert await wait_for(node.home_reachable, timeout=10.0, step=0.05)
        async with LocalClient(sock=home.dir / "agent.sock") as person:
            lead = await person.call("create", name="lead", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        home.write_hosts([])
        node._home_mux.close("the lid closed")
        assert await wait_for(lambda: not node.home_reachable(), timeout=10.0, step=0.05)
        async with LocalClient() as at_node:
            w = await at_node.call(
                "create", name="w", dir=str(tmp_path), adapter=hookstub.name, controllers=[f"{lead['id']}@kmaster"]
            )
        assert node.sessions[w["id"]].controllers == [f"{lead['id']}@kmaster"]
        home.write_hosts(["laptop"])
        v = await until(home, f"{w['id']}@laptop", lambda v: v is not None and v["state"] != "unreachable")
        assert v["controllers"] == [lead["id"]] and v["host"] == "laptop"
        async with LocalClient(sock=home.dir / "agent.sock") as person:
            await person.call("kill", id=lead["id"])


# -- a team on a machine node, the nightly tarball (step 4b.3) ---------------------------------------------


def test_a_checkouts_files_are_read_inside_it_and_nothing_else(tmp_path):
    """`read_checkout`: a file read across a trust boundary refuses rather than guesses — a path
    that climbs out, an absolute one, a symlink pointing out, a directory, an oversized file, too
    many paths — and a file that is simply not there is None."""
    from sessionorc.agent import FILE_CAP, FILES_MAX, read_checkout

    repo, outside = tmp_path / "repo", tmp_path / "secret"
    (repo / "docs").mkdir(parents=True)
    (repo / ".git").mkdir()
    outside.write_text("the key")
    (repo / ".agentorc.yml").write_text("roles: {}\n")
    (repo / "docs" / "brief.md").write_text("a brief")
    (repo / "docs" / "inside.md").symlink_to(repo / "docs" / "brief.md")
    (repo / "docs" / "out.md").symlink_to(outside)
    (repo / "big.md").write_text("x" * (FILE_CAP + 1))
    got = read_checkout(str(repo), [".agentorc.yml", "docs/brief.md", "docs/inside.md", "missing.md"])
    assert got["files"] == {
        ".agentorc.yml": "roles: {}\n", "docs/brief.md": "a brief", "docs/inside.md": "a brief", "missing.md": None,
    }  # fmt: skip
    for bad, why in (
        ("../secret", "outside the checkout"),
        ("docs/out.md", "outside the checkout"),
        (str(outside), "relative to the checkout"),
        ("docs", "not a regular file"),
        ("big.md", "over"),
        ("", "relative to the checkout"),
    ):
        with pytest.raises(ValueError, match=why):
            read_checkout(str(repo), [bad])
    with pytest.raises(ValueError, match=f"at most {FILES_MAX}"):
        read_checkout(str(repo), ["a"] * (FILES_MAX + 1))
    with pytest.raises(ValueError, match="does not exist here"):
        read_checkout(str(tmp_path / "nope"), [".agentorc.yml"])
    (tmp_path / ".ssh").mkdir()
    with pytest.raises(ValueError, match="is not a git checkout"):  # a directory that is not a repo is not read
        read_checkout(str(tmp_path / ".ssh"), ["id_ed25519"])
    os.mkfifo(repo / "pipe")
    with pytest.raises(ValueError, match="not a regular file"):  # and never blocks on one
        read_checkout(str(repo), ["pipe"])


async def test_host_files_is_a_persons_or_a_controllers_read_and_never_a_nodes(agent, tmp_path):
    """The home's `host_files` (step 4b.3): this host's checkout read here, another host's through its
    node's `files` link method, unreachable refused; a session needs `control`, as a team start
    does; and a call forwarded from a node — a person's or a session's — is refused whole."""

    class Files(FakeMux):
        async def request(self, method, timeout=None, **params):
            self.sent.append((method, params))
            return {"dir": params["dir"], "files": {p: f"on laptop: {p}" for p in params["paths"]}}

    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "repo" / ".agentorc.yml").write_text("x: 1\n")
    async with LocalClient() as person:
        here = await person.call("host_files", host=agent.host, dir=str(tmp_path / "repo"), paths=[".agentorc.yml"])
        assert here["files"] == {".agentorc.yml": "x: 1\n"}
        with pytest.raises(AgentError, match="outside the checkout"):
            await person.call("host_files", host=agent.host, dir=str(tmp_path / "repo"), paths=["../../etc/passwd"])
        agent._take_records("laptop", [record()], whole=True)  # a host the home has heard of
        with pytest.raises(AgentError, match="runs on laptop: unreachable"):
            await person.call("host_files", host="laptop", dir="/w", paths=["a"])
        agent._link_muxes["laptop"] = mux = Files()
        agent.links["laptop"] = {"up": True, "since": "t", "why": "linked"}
        got = await person.call("host_files", host="laptop", dir="/w", paths=[".agentorc.yml"])
        assert got["files"] == {".agentorc.yml": "on laptop: .agentorc.yml"} and mux.sent[0][0] == "files"
        lead = await person.call("create", name="lead", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
    async with LocalClient(caller=lead["id"]) as as_lead:
        with pytest.raises(AgentError, match="needs the control grant"):
            await as_lead.call("host_files", host="laptop", dir="/w", paths=["a"])
    async with LocalClient() as person:
        await person.call("set_grants", id=lead["id"], add=["control"])
    async with LocalClient(caller=lead["id"]) as as_lead:
        assert (await as_lead.call("host_files", host="laptop", dir="/w", paths=["a"]))["files"] == {
            "a": "on laptop: a"
        }
    for caller in (None, "ao-x-w"):
        call = {"rpc": "host_files", "params": {"host": agent.host, "dir": "/", "paths": ["etc/passwd"]}}
        resp = await agent._forwarded("laptop", {**call, "caller": caller, "token": ""})
        assert "host_files is not served to a call from laptop" in resp["error"]
    assert len(mux.sent) == 2
    del agent._link_muxes["laptop"]
    async with LocalClient() as person:
        await person.call("kill", id=lead["id"])


async def test_a_node_serves_files_from_its_own_checkout_across_the_link(home, tmp_path, monkeypatch):
    """End to end: the home's `host_files` for the node reads the checkout on the node."""
    (tmp_path / "cm" / ".git").mkdir(parents=True)
    (tmp_path / "cm" / ".agentorc.yml").write_text("roles: {grinder: {brief: b.md}}\n")
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as node:
        assert await wait_for(node.home_reachable, timeout=10.0, step=0.05)
        async with LocalClient(sock=home.dir / "agent.sock") as person:
            got = await person.call(
                "host_files", host="laptop", dir=str(tmp_path / "cm"), paths=[".agentorc.yml", "b.md"]
            )
            assert got["files"] == {".agentorc.yml": "roles: {grinder: {brief: b.md}}\n", "b.md": None}
            with pytest.raises(AgentError, match="laptop: .*outside the checkout"):
                await person.call("host_files", host="laptop", dir=str(tmp_path / "cm"), paths=["../kmaster/hosts.yml"])
        async with LocalClient() as at_node:  # a node reads no other host's files, and does not forward it
            with pytest.raises(AgentError, match="is a node of kmaster"):
                await at_node.call("host_files", host="kmaster", dir="/", paths=["etc/hostname"])


def test_the_nightly_tarball_holds_the_store_and_the_orgs_files_and_nothing_else(tmp_path, monkeypatch):
    import stat
    import tarfile

    from sessionorc.agent import BACKUP_KEEP, backup_store

    home = tmp_path / "h"
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    for rel, text in {
        "sessions/ao-a.json": "{}", "remote/laptop/ao-b.json": "{}", "person_inbox.json": "[]",
        "org.yml": "teams: {}", "hosts.yml": "nodes: [laptop]", "profiles.yml": "profiles: {}",
        "nodes/cm/env": "GH_TOKEN=secret", "runs/ao-a-1.log": "output", "waits/ao-a": "{}",
    }.items():  # fmt: skip
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(text)
    made = backup_store("2026-09-18")
    assert made == home / "backups" / "store-2026-09-18.tar.gz" and stat.S_IMODE(made.stat().st_mode) == 0o600
    with tarfile.open(made) as tar:
        names = set(tar.getnames())
    assert names == {
        "sessions/ao-a.json", "remote/laptop/ao-b.json", "person_inbox.json", "org.yml", "hosts.yml", "profiles.yml",
    }  # fmt: skip
    assert backup_store("2026-09-18") is None  # once a day
    for d in range(1, 10):
        backup_store(f"2026-08-{d:02d}")
    kept = sorted(p.name for p in (home / "backups").glob("store-*.tar.gz"))
    assert len(kept) == BACKUP_KEEP and kept[-1] == "store-2026-09-18.tar.gz"
    assert not list((home / "backups").glob(".*.part"))


async def test_the_homes_tick_takes_the_tarball_and_a_nodes_does_not(agent):
    from datetime import datetime

    day = datetime.now().astimezone().date().isoformat()
    assert await wait_for(lambda: (paths.backups_dir() / f"store-{day}.tar.gz").exists(), timeout=10.0, step=0.05)
    agent.mode, agent._backed_up = "node", ""
    await agent.tick()
    assert agent._backed_up == ""  # a node's store is a replica: the home's tarball is the backup
    agent.mode = "home"


async def test_a_copy_with_a_renamed_grant_is_rewritten_and_said_so_at_either_end(agent, caplog):
    """*Left for step 4* (TD-055 × step 2): a record crossing the link with `orchestrate` is
    normalised to `control` — and, as the loader does, saved and logged rather than silently."""
    import json as jsonmod
    import logging

    from sessionorc.models import Session

    caplog.set_level(logging.WARNING, logger="agentorc.agent")
    agent._take_records("laptop", [record(capabilities=["orchestrate"])], whole=True)
    assert agent.remote["laptop"]["ao-x-w"].capabilities == ["control"]
    stored = jsonmod.loads(paths.remote_dir("laptop").joinpath("ao-x-w.json").read_text())
    assert stored["capabilities"] == ["control"] and "grant orchestrate is now `control`" in caplog.text
    caplog.clear()
    agent.mode, agent.home = "node", "kmaster"
    s = Session(id="ao-x-n", name="n", kind="interactive", adapter="claude-code", dir="/tmp", host=agent.host)
    s.capabilities = ["control"]
    agent.sessions[s.id] = s
    try:
        await agent._take_intent([{"id": s.id, "host": agent.host, "capabilities": ["orchestrate"]}])
        assert s.capabilities == ["control"] and "grant orchestrate is now `control`" in caplog.text
        assert not hasattr(s, "renamed_grants")
    finally:
        agent.sessions.pop(s.id)
        agent.mode, agent.home = "home", agent.host


async def test_a_nodes_record_is_acknowledged_at_that_node(home, hookstub, tmp_path, monkeypatch):
    """§4.8a (TD-077 step 2, review of PR #251): identity alarms are **node-owned** — observed
    where the socket is — so **Acknowledge** on a node's record is routed to that node, which
    clears its own list; the home takes the cleared record from the reply, and no later report
    brings the alarms back. Without an `id` the RPC is the host's own list and never travels. A
    person at the node may clear only that node's records; a session is refused at both ends."""
    async with node_agent(tmp_path, monkeypatch, home.dial_command()) as node:
        acts = Acts(node, monkeypatch)
        async with LocalClient() as c:
            w = await c.call("create", name="w", dir=str(tmp_path), adapter=hookstub.name, unattended=True)
        address = f"{w['id']}@laptop"
        await until(home, address, lambda v: v is not None and v["state"] != "unreachable")
        # the node records a forgery aimed at its own session, exactly as its dispatch would
        node._id_alarm({"channel": f"session {w['id']}", "claimed": "ao-b", "rpc": "msg"}, w["id"])
        seen = await until(home, address, lambda v: bool((v or {}).get("identity_alarms")))
        assert [a["claimed"] for a in seen["identity_alarms"]] == ["ao-b"]

        async with LocalClient(sock=home.dir / "agent.sock", caller="ao-someone") as as_session:
            with pytest.raises(AgentError, match="only by a person"):
                await as_session.call("identity_ack", id=address)
        assert node.sessions[w["id"]].identity_alarms  # refused at the node, nothing cleared

        async with LocalClient(sock=home.dir / "agent.sock") as person:
            got = await person.call("identity_ack", id=address)
            assert got["cleared"] is True and acts.taken[-1]["rpc"] == "identity_ack"
            assert acts.taken[-1]["caller"] is None and acts.taken[-1]["params"]["id"] == w["id"]
        assert node.sessions[w["id"]].identity_alarms == []  # cleared where the list lives
        # the home's replica is clear from the reply, and stays clear through the node's next report
        assert (await at_home(home, address))["identity_alarms"] == []
        await asyncio.sleep(FAST_TICK * 3)
        assert (await at_home(home, address))["identity_alarms"] == []

        # a person at the node clears that node's records and no other host's: a home record is
        # simply no session here, exactly as a `kill` of one is (§4.4a) — and this never travels
        async with LocalClient() as at_node:
            with pytest.raises(AgentError, match="no session ao-x-lead@kmaster"):
                await at_node.call("identity_ack", id="ao-x-lead@kmaster")
            assert (await at_node.call("identity_ack"))["id"] == "person"  # its own host's list, here
        # the person's ack and the session's refused one: both crossed, because the node is the
        # authority on its own list — and nothing else went over the link on this account
        assert [a["rpc"] for a in acts.taken].count("identity_ack") == 2
