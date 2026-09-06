"""Design-promised paths the end-to-end tests do not reach: run log from the first byte, closed
sessions forgotten after their day, adoption of hand-started `ao-*` sessions, the offline hook
event queue, tail hygiene."""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sessionorc import adapters, paths
from sessionorc.adapters import LaunchSpec
from sessionorc.agent import HostAgent, _clean
from sessionorc.client import LocalClient
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


async def wait_for(pred, timeout=6.0):
    for _ in range(int(timeout / 0.1)):
        if await pred():
            return True
        await asyncio.sleep(0.1)
    return False


async def test_fast_exit_has_log_and_exit_code(agent, tmp_path):
    """Invariant 3: a command that exits at once still gets its run log and a recorded exit code."""
    async with LocalClient() as c:
        for i in range(5):
            s = await c.call(
                "create",
                name=f"fast{i}",
                dir=str(tmp_path),
                adapter="command",
                kind="command",
                argv=["sh", "-c", "echo first-byte; exit 4"],
            )
            assert s["id"] in [x["id"] for x in await c.call("list")]  # recorded, not leaked

        async def all_exited():
            return all(x["state"] == "exited" and x["exit_code"] == 4 for x in await c.call("list"))

        assert await wait_for(all_exited)
        for x in await c.call("list"):
            assert "first-byte" in Path(x["run_log"]).read_text()


async def test_closed_sessions_are_forgotten_after_keep(agent, tmp_path, monkeypatch):
    monkeypatch.setattr("sessionorc.agent.CLOSED_KEEP", timedelta(seconds=0))
    async with LocalClient() as c:
        s = await c.call("create", name="c", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        await c.call("close", id=s["id"])
        assert (await c.call("get", id=s["id"]))["state"] == "closed"

        async def gone():
            return all(x["id"] != s["id"] for x in await c.call("list"))

        assert await wait_for(gone)
        assert not (paths.sessions_dir() / f"{s['id']}.json").exists()


async def test_hand_started_session_is_adopted(agent, tmp_path):
    agent.tmux.new_session("ao-byhand-x", str(tmp_path), ["bash", "--norc"], {})
    async with LocalClient() as c:

        async def adopted():
            return any(x["id"] == "ao-byhand-x" and x["state"] == "idle" for x in await c.call("list"))

        assert await wait_for(adopted)
        s = await c.call("get", id="ao-byhand-x")
        assert s["adapter"] == "shell" and s["name"] == "byhand-x" and s["confidence"] == "scraped"


class HookFedStub:
    """A hook-fed adapter: never scraped, so queued hook events are what set its state."""

    name = "hookstub"
    state_source = "hook"

    def launch(self, *, profile, resume, prompt, unattended, cwd, name=""):
        return LaunchSpec(argv=["bash", "--norc"])

    def classify(self, pane, tail):
        return None


async def test_offline_hook_events_are_drained(agent, tmp_path):
    """A hook that could not reach the socket appends to events/; the next tick applies it."""
    adapters.register(HookFedStub())
    async with LocalClient() as c:
        s = await c.call("create", name="q", dir=str(tmp_path), adapter="hookstub")
        agent.events.append(s["id"], {"state": "needs-you", "pending": {"kind": "question", "text": "pick 1-3"}})
        agent.events.append(s["id"], {"adapter_id": "uuid-1"})

        async def applied():
            x = await c.call("get", id=s["id"])
            return x["adapter_id"] == "uuid-1" and x["pending"] is not None and x["pending"]["text"] == "pick 1-3"

        assert await wait_for(applied)


async def test_fresh_session_not_judged_by_old_snapshot(agent, tmp_path):
    """A pane snapshot taken before a session existed must not flip it to exited."""
    from sessionorc.models import Session

    s = Session(id="ao-new-1", name="new", kind="interactive", adapter="shell", dir=str(tmp_path))
    agent.sessions[s.id] = s
    old_snapshot = datetime.now(UTC) - timedelta(seconds=1)
    agent._reconcile({}, {}, old_snapshot)
    assert agent.sessions[s.id].state != "exited"
    agent._reconcile({}, {}, datetime.now(UTC) + timedelta(seconds=30))
    assert agent.sessions[s.id].state == "exited"


def test_clean_strips_osc_csi_and_controls():
    assert _clean("\x1b]0;my-title\x07hello \x1b[32mworld\x1b[0m\r") == "hello world"
    assert _clean("\x1b(Bplain\x1b]2;t\x1b\\ tail") == "plain tail"
    assert len(_clean("x" * 500)) == 200
