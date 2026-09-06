"""End-to-end: an in-process host agent on a private tmux server and a temp AGENTORC_HOME."""

import asyncio
import contextlib
import uuid

import pytest

from sessionorc import paths
from sessionorc.agent import HostAgent
from sessionorc.client import AgentError, LocalClient
from sessionorc.tmux import Tmux


@pytest.fixture
async def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("sessionorc.agent.TICK_SECONDS", 0.3)
    tmux = Tmux(socket_name=f"ao-test-{uuid.uuid4().hex[:8]}")
    a = HostAgent(tmux=tmux)
    task = asyncio.create_task(a.serve(paths.socket_path()))
    for _ in range(50):
        if paths.socket_path().exists():
            break
        await asyncio.sleep(0.05)
    yield a
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    tmux.kill_server()


async def wait_state(client, sid, state, timeout=6.0):
    for _ in range(int(timeout / 0.1)):
        s = await client.call("get", id=sid)
        if s["state"] == state:
            return s
        await asyncio.sleep(0.1)
    raise AssertionError(f"{sid} never reached {state}: {s['state']} {s.get('tail')}")


async def test_shell_lifecycle(agent, tmp_path):
    async with LocalClient() as c:
        assert await c.call("ping") == "pong"
        s = await c.call("create", name="my shell", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        sid = s["id"]
        assert sid.startswith("ao-") and s["adapter"] == "shell" and s["confidence"] == "scraped"
        # the session's environment names this agent's home, so hooks inside it reach this socket
        env = await c.call("tail", id=sid, lines=1)  # warm-up; the real check is below
        assert env is not None
        await c.call("send", id=sid, text="echo HOME=$AGENTORC_HOME SESSION=$AGENTORC_SESSION")
        for _ in range(30):
            tail = await c.call("tail", id=sid, lines=6)
            if any(f"HOME={paths.home()} SESSION={sid}" in line for line in tail):
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError(f"env not set in session: {tail}")
        await wait_state(c, sid, "idle")
        await c.call("send", id=sid, text="sleep 1.5")
        await wait_state(c, sid, "working")
        await wait_state(c, sid, "idle")
        tail = await c.call("tail", id=sid, lines=5)
        assert any("sleep 1.5" in line for line in tail)
        assert (paths.runs_dir()).exists() and list(paths.runs_dir().glob(f"{sid}-*.log"))
        await c.call("kill", id=sid)
        await wait_state(c, sid, "exited")
        await c.call("remove", id=sid)
        assert all(x["id"] != sid for x in await c.call("list"))


async def test_anchor_rule_and_shell_exemption(agent, tmp_path):
    async with LocalClient() as c:
        a = await c.call("create", name="one", dir=str(tmp_path), adapter="command", argv=["sleep", "30"])
        with pytest.raises(AgentError, match="anchor rule"):
            await c.call("create", name="two", dir=str(tmp_path), adapter="command", argv=["sleep", "30"])
        # shells are exempt (design §9 invariant 2)
        sh = await c.call("create", name="sh", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        assert sh["id"] != a["id"]
        # same name → suffix, not a collision
        await c.call("kill", id=a["id"])
        await wait_state(c, a["id"], "exited")
        b = await c.call("create", name="one", dir=str(tmp_path), adapter="command", argv=["sleep", "30"])
        assert b["id"] == a["id"] + "-2"


async def test_permission_roundtrip(agent, tmp_path):
    async with LocalClient() as c:
        s = await c.call("create", name="w", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        sid = s["id"]

        async def hook():
            async with LocalClient() as h:
                return await h.call(
                    "hook", session=sid, kind="permission", text="Bash: git push", tool_use_id="tu1", wait_seconds=5
                )

        task = asyncio.create_task(hook())
        st = await wait_state(c, sid, "needs-you")
        assert st["pending"]["kind"] == "permission" and st["pending"]["deadline"]
        with pytest.raises(AgentError, match="pending permission"):
            await c.call("send", id=sid, text="hi")
        # the terminal's own dialog notification must not displace the hook-channel buttons
        async with LocalClient() as h:
            await h.call(
                "hook",
                session=sid,
                state="needs-you",
                pending={"kind": "question", "text": "Claude needs your permission"},
            )
        st = await c.call("get", id=sid)
        assert st["pending"]["kind"] == "permission" and st["pending"]["tool_use_id"] == "tu1"
        await c.call("decide", id=sid, tool_use_id="tu1", behavior="allow", reason="ok")
        assert await task == {"behavior": "allow", "reason": "ok"}
        assert (await c.call("get", id=sid))["state"] == "working"


async def test_permission_timeout_falls_to_terminal(agent, tmp_path):
    async with LocalClient() as c:
        s = await c.call("create", name="t", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        sid = s["id"]
        res = await c.call("hook", session=sid, kind="permission", text="Edit: x", tool_use_id="tu2", wait_seconds=0.2)
        assert res is None
        st = await c.call("get", id=sid)
        assert st["state"] == "needs-you" and st["pending"]["kind"] == "question"


async def test_subscribe_streams_changes(agent, tmp_path):
    async with LocalClient() as c, LocalClient() as sub:
        events = sub.subscribe()
        s = await c.call("create", name="s", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        seen = []
        async for ev in events:
            seen.append(ev)
            if ev.get("event") == "session" and ev["session"]["id"] == s["id"] and ev["session"]["state"] == "idle":
                break
        assert seen
