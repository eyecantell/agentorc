"""A host agent restart: sessions come back from the store, live panes reconcile, and a record
written before `prompt` pendings stopped being alerts (2026-09-06) comes back idle.

Fully in-process, on its own private tmux server: the second agent must not share a socket and
store with a still-running agent (design §9 invariant 1)."""

import asyncio
import contextlib
from datetime import timedelta

import pytest
from conftest import kill_private_server, private_socket_name, wait_for, wait_state

from sessionorc import paths
from sessionorc.agent import HostAgent
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import Pending, Session
from sessionorc.store import SessionStore
from sessionorc.tmux import Tmux

pytestmark = pytest.mark.integration


async def start(tmux: Tmux) -> tuple[HostAgent, asyncio.Task]:
    a = HostAgent(tmux=tmux)
    task = asyncio.create_task(a.serve(paths.socket_path()))
    assert await wait_for(lambda: paths.socket_path().exists(), timeout=5.0, step=0.05)
    return a, task


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    assert not paths.socket_path().exists()  # serve()'s finally unlinked it


async def test_restart_reloads_and_reconciles(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("sessionorc.agent.TICK_SECONDS", 0.3)
    monkeypatch.setattr("sessionorc.agent.CREATE_GRACE", timedelta(seconds=1))
    tmux = Tmux(socket_name=private_socket_name())
    try:
        first, task = await start(tmux)
        async with LocalClient() as c:
            sh = await c.call("create", name="sh", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
            cmd = await c.call(
                "create", name="cmd", dir=str(tmp_path), adapter="command", kind="command", argv=["sleep", "30"]
            )
            await wait_state(c, sh["id"], "idle")
            await wait_state(c, cmd["id"], "working")
            await c.call("set_mode", id=sh["id"], unattended=True)
        await stop(task)

        # a pre-2026-09-06 record: pending prompt shown as needs-you
        store = SessionStore(paths.sessions_dir())
        old = Session(id="ao-old-prompt", name="old", kind="interactive", adapter="shell", dir=str(tmp_path))
        old.set_state("needs-you", confidence="hook", pending=Pending(kind="prompt", text="waiting"))
        store.save(old)

        second, task = await start(tmux)
        assert second is not first
        assert set(second.sessions) == {sh["id"], cmd["id"], "ao-old-prompt"}
        assert second.sessions[sh["id"]].unattended is True  # persisted field survives
        migrated = second.sessions["ao-old-prompt"]
        assert migrated.state == "idle" and migrated.pending is None and migrated.confidence == "hook"
        async with LocalClient() as c:
            await wait_state(c, sh["id"], "idle")
            await wait_state(c, cmd["id"], "working")
            # no pane behind the old record: it is judged exited once the grace has passed
            await wait_state(c, "ao-old-prompt", "exited")
            with pytest.raises(AgentError, match="kill it first"):
                await c.call("remove", id=cmd["id"])
            await c.call("kill", id=cmd["id"])
            await wait_state(c, cmd["id"], "exited")
            await c.call("remove", id=cmd["id"])
            assert all(x["id"] != cmd["id"] for x in await c.call("list"))
            await c.call("kill", id=sh["id"])
        await stop(task)
    finally:
        kill_private_server(tmux)
