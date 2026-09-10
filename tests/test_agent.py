"""End-to-end: an in-process host agent on a private tmux server and a temp AGENTORC_HOME."""

import asyncio
import json

import pytest
from conftest import wait_state

from sessionorc import paths
from sessionorc.client import AgentError, LocalClient

pytestmark = pytest.mark.integration


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
        # a natural exit keeps its dead pane (exit code, last screen); a kill destroys it (TD-023)
        nat = await c.call("create", name="nat", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        await wait_state(c, nat["id"], "idle")
        await c.call("send", id=nat["id"], text="exit 3")
        ended = await wait_state(c, nat["id"], "exited")
        assert ended["pane"] is True  # the exit code is tmux's to report and lags the dead flag (not asserted)
        killed = await c.call("kill", id=sid)
        assert killed["state"] == "exited" and killed["pane"] is False
        await wait_state(c, sid, "exited")
        assert (await c.call("get", id=sid))["pane"] is False  # the tick does not bring it back
        await c.call("remove", id=nat["id"])
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


async def test_permission_roundtrip(agent, hookstub, tmp_path):
    async with LocalClient() as c:
        s = await c.call("create", name="w", dir=str(tmp_path), adapter=hookstub.name)
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


async def test_permission_timeout_falls_to_terminal(agent, hookstub, tmp_path):
    async with LocalClient() as c:
        s = await c.call("create", name="t", dir=str(tmp_path), adapter=hookstub.name)
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


async def test_second_subscriber_gets_a_snapshot_without_disturbing_the_first(agent, tmp_path):
    """TD-009: each subscriber has its own last-pushed map, so a new tab's full snapshot is not
    re-sent to every other tab, and a `gone` reaches every tab exactly once."""

    async def next_event(sub: LocalClient, timeout: float) -> dict:
        # the raw stream, not `subscribe()`'s generator: a timed-out `anext` leaves a generator broken
        return json.loads(await asyncio.wait_for(sub._reader.readline(), timeout))

    async with LocalClient() as c, LocalClient() as first, LocalClient() as second:
        s = await c.call("create", name="s", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        await wait_state(c, s["id"], "idle")
        await first.call("subscribe")
        got = await next_event(first, 5)
        assert got["event"] == "session" and got["session"]["id"] == s["id"]
        await second.call("subscribe")
        got = await next_event(second, 5)
        assert got["event"] == "session" and got["session"]["id"] == s["id"]  # the newcomer's snapshot
        with pytest.raises(TimeoutError):
            await next_event(first, 1.0)  # nothing changed for the first tab
        await c.call("kill", id=s["id"])
        await wait_state(c, s["id"], "exited")
        await c.call("remove", id=s["id"])
        for sub in (first, second):
            while (got := await next_event(sub, 5))["event"] != "gone":
                pass
            assert got["id"] == s["id"]
        with pytest.raises(TimeoutError):
            await next_event(first, 1.0)  # gone once, not once per push


async def test_create_in_new_worktree(agent, tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(cmd, cwd=repo, check=True)
    (repo / "README").write_text("x")
    subprocess.run(["git", "add", "README"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    async with LocalClient() as c:
        s = await c.call(
            "create", name="td 9", dir=str(repo), adapter="shell", argv=["bash", "--norc"], worktree="td-9"
        )
        assert s["dir"] == str(repo / ".claude" / "worktrees" / "td-9") and s["repo"] == str(repo)
        assert (repo / ".claude" / "worktrees" / "td-9" / "README").is_file()
        with pytest.raises(AgentError, match="letters, digits"):
            await c.call("create", name="bad", dir=str(repo), adapter="shell", worktree="a b")


async def test_occupancy_sees_own_and_external_sessions(agent, tmp_path, monkeypatch):
    from sessionorc import adapters
    from sessionorc.adapters import ExternalSession, LaunchSpec

    class Ext:
        name = "ext-tool"
        state_source = "hook"

        def launch(self, *, profile, resume, prompt, unattended, cwd, name=""):
            return LaunchSpec(argv=["bash", "--norc"])

        def classify(self, pane, tail):
            return None

        def external_sessions(self):
            return [
                ExternalSession(
                    adapter="ext-tool",
                    cwd=str(tmp_path / "vscode"),
                    name="editor-session",
                    tool_id="ext-1",
                    status="busy",
                )
            ]

    adapters.load_all()  # the registry must hold the built-ins before we add to it
    monkeypatch.setitem(adapters._REGISTRY, "ext-tool", Ext())
    (tmp_path / "vscode").mkdir()
    (tmp_path / "free").mkdir()
    async with LocalClient() as c:
        occ = await c.call("occupancy", dir=str(tmp_path / "vscode"))
        assert occ["occupants"] == ["editor-session (ext-tool, outside agentorc, busy)"]
        assert (await c.call("occupancy", dir=str(tmp_path / "free")))["occupants"] == []
        with pytest.raises(AgentError, match="outside agentorc"):
            await c.call("create", name="x", dir=str(tmp_path / "vscode"), adapter="ext-tool")
        # shells are exempt, and a shell in that directory does not occupy it either
        sh = await c.call("create", name="sh", dir=str(tmp_path / "vscode"), adapter="shell", argv=["bash", "--norc"])
        assert (await c.call("occupancy", dir=str(tmp_path / "vscode")))["occupants"] == [
            "editor-session (ext-tool, outside agentorc, busy)"
        ]
        # our own live agent session occupies its directory
        own = await c.call("create", name="own", dir=str(tmp_path / "free"), adapter="ext-tool")
        occ = await c.call("occupancy", dir=str(tmp_path / "free"))
        assert occ["occupants"] == [f"{own['id']} (working)"]
        await c.call("kill", id=sh["id"])
        await c.call("kill", id=own["id"])


async def test_limited_from_usage_cap(agent, hookstub, tmp_path, monkeypatch):
    """TD-001: a profile at 100% of a window makes its interactive sessions `limited` with the reset
    time; the cap lifting (or the reset time passing) brings them back to `working`; a session that
    needs you keeps that; subscribers get the usage figure."""
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr("sessionorc.agent.USAGE_EVERY", 0.0)
    soon = (datetime.now(UTC) + timedelta(hours=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    async with LocalClient() as c, LocalClient() as sub:
        s = await c.call("create", name="cap", dir=str(tmp_path), adapter="hookstub", profile="p1")
        await sub.call("subscribe")
        hookstub.usage_value = {"five_hour_pct": 100, "weekly_pct": 12, "five_hour_resets": soon, "weekly_resets": None}
        got = await wait_state(c, s["id"], "limited")
        assert got["pending"] == {
            "kind": "limit",
            "text": f"5-hour cap · resets {soon[11:16]}Z",
            "deadline": None,
            "tool_use_id": None,
        }
        assert got["confidence"] == "hook"
        assert (await c.call("usage"))["p1"]["five_hour_pct"] == 100
        while (ev := json.loads(await asyncio.wait_for(sub._reader.readline(), 5)))["event"] != "usage":
            pass
        assert ev["profile"] == "p1" and ev["usage"]["five_hour_pct"] == 100
        # the window resets: back to what it was (working)
        hookstub.usage_value = {"five_hour_pct": 3, "weekly_pct": 12, "five_hour_resets": soon, "weekly_resets": None}
        await wait_state(c, s["id"], "working")
        # an idle session comes back idle, not working — no hook would correct a wrong `working`
        await c.call("hook", session=s["id"], state="idle")
        hookstub.usage_value = {
            "five_hour_pct": 100,
            "weekly_pct": 12,
            "five_hour_resets": "garbage",
            "weekly_resets": None,
        }
        got = await wait_state(c, s["id"], "limited")
        assert got["pending"]["text"] == "5-hour cap · resets unknown"
        hookstub.usage_value = {"five_hour_pct": 3, "weekly_pct": 12, "five_hour_resets": soon, "weekly_resets": None}
        await wait_state(c, s["id"], "idle")
        # a cap whose reset time is already behind us is no cap
        past = (datetime.now(UTC) - timedelta(minutes=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        hookstub.usage_value = {"five_hour_pct": 100, "weekly_pct": 12, "five_hour_resets": past, "weekly_resets": None}
        await asyncio.sleep(0.8)
        assert (await c.call("get", id=s["id"]))["state"] == "idle"  # unchanged
        # needs-you is never overridden by a cap
        await c.call("hook", session=s["id"], state="needs-you", pending={"kind": "question", "text": "a or b?"})
        hookstub.usage_value = {"five_hour_pct": 5, "weekly_pct": 100, "five_hour_resets": None, "weekly_resets": soon}
        await asyncio.sleep(0.8)
        assert (await c.call("get", id=s["id"]))["state"] == "needs-you"
        hookstub.usage_value = None
        await c.call("kill", id=s["id"])


async def test_send_wait_three_outcomes(agent, hookstub, tmp_path, monkeypatch):
    """TD-016: `send(wait=True)` returns the settled record, or errors prompt-stalled / timeout.
    Hook events stand in for Claude Code's UserPromptSubmit → Stop."""
    monkeypatch.setattr("sessionorc.agent.SEND_STALL_SECONDS", 0.6)
    async with LocalClient() as c, LocalClient() as feeder:
        s = await c.call("create", name="w", dir=str(tmp_path), adapter="hookstub")
        await feeder.call("hook", session=s["id"], state="idle")
        await wait_state(c, s["id"], "idle")

        # settled: the prompt is taken (working) and the turn ends (idle)
        task = asyncio.create_task(c.call("send", id=s["id"], text="do it", wait=True, timeout=5))
        await asyncio.sleep(0.2)
        await feeder.call("hook", session=s["id"], state="working")
        await asyncio.sleep(0.2)
        assert not task.done()  # started, not settled
        await feeder.call("hook", session=s["id"], state="idle")
        got = await asyncio.wait_for(task, 5)
        assert got["id"] == s["id"] and got["state"] == "idle"

        # a permission counts as settled too: the caller can see what it is waiting on
        task = asyncio.create_task(c.call("send", id=s["id"], text="again", wait=True, timeout=5))
        await asyncio.sleep(0.2)
        await feeder.call("hook", session=s["id"], state="working")
        await feeder.call(
            "hook", session=s["id"], state="needs-you", pending={"kind": "question", "text": "which one?"}
        )
        got = await asyncio.wait_for(task, 5)
        assert got["state"] == "needs-you" and got["pending"]["text"] == "which one?"
        await feeder.call("hook", session=s["id"], state="idle")
        await wait_state(c, s["id"], "idle")

        # prompt-stalled: nothing reacts
        with pytest.raises(AgentError, match="prompt-stalled"):
            await c.call("send", id=s["id"], text="hello?", wait=True, timeout=5)

        # timeout: taken, never settles
        task = asyncio.create_task(c.call("send", id=s["id"], text="long", wait=True, timeout=0.8))
        await asyncio.sleep(0.2)
        await feeder.call("hook", session=s["id"], state="working")
        with pytest.raises(AgentError, match="timeout: .* still working"):
            await task
        assert (await c.call("get", id=s["id"]))["state"] == "working"  # nothing re-sent, nothing changed

        # busy at send: the prompt is queued behind the current turn. That turn ending is not the
        # confirmation; the *next* turn starting and settling is.
        task = asyncio.create_task(c.call("send", id=s["id"], text="queued", wait=True, timeout=5))
        await asyncio.sleep(0.2)
        await feeder.call("hook", session=s["id"], state="idle")  # the current turn ends
        await asyncio.sleep(0.3)
        assert not task.done()
        await feeder.call("hook", session=s["id"], state="working")  # the queued prompt is taken
        await asyncio.sleep(0.2)
        assert not task.done()
        await feeder.call("hook", session=s["id"], state="idle")
        assert (await asyncio.wait_for(task, 5))["state"] == "idle"
        # busy, and the whole queued turn runs between two polls (idle, working, idle back to back):
        # settled, never a false prompt-stalled
        await feeder.call("hook", session=s["id"], state="working")
        task = asyncio.create_task(c.call("send", id=s["id"], text="fast", wait=True, timeout=5))
        await asyncio.sleep(0.2)
        for st in ("idle", "working", "idle"):
            await feeder.call("hook", session=s["id"], state=st)
        assert (await asyncio.wait_for(task, 5))["state"] == "idle"
        # busy, and the current turn stops on a question: returned as is, the prompt still queued
        await feeder.call("hook", session=s["id"], state="working")
        task = asyncio.create_task(c.call("send", id=s["id"], text="queued2", wait=True, timeout=5))
        await asyncio.sleep(0.2)
        await feeder.call("hook", session=s["id"], state="needs-you", pending={"kind": "question", "text": "hm?"})
        assert (await asyncio.wait_for(task, 5))["state"] == "needs-you"
        # busy, the current turn ends, and nothing takes the queued prompt: prompt-stalled
        await feeder.call("hook", session=s["id"], state="working")
        task = asyncio.create_task(c.call("send", id=s["id"], text="queued3", wait=True, timeout=5))
        await asyncio.sleep(0.2)
        await feeder.call("hook", session=s["id"], state="idle")
        with pytest.raises(AgentError, match="prompt-stalled"):
            await task
        # removed while waiting, with no timeout: a clear error, not a format crash
        await feeder.call("hook", session=s["id"], state="idle")
        task = asyncio.create_task(c.call("send", id=s["id"], text="bye", wait=True))
        await asyncio.sleep(0.2)
        await feeder.call("kill", id=s["id"])  # `c` is busy with the send: one request per connection
        await wait_state(feeder, s["id"], "exited")
        got = await asyncio.wait_for(task, 5)  # an exit is a transition and a settled state
        assert got["state"] == "exited"
        await feeder.call("remove", id=s["id"])
        # a record that vanishes mid-wait (no timeout): a clear error, not a format crash
        s = await feeder.call("create", name="w2", dir=str(tmp_path), adapter="hookstub")
        await feeder.call("hook", session=s["id"], state="idle")
        await wait_state(feeder, s["id"], "idle")
        task = asyncio.create_task(c.call("send", id=s["id"], text="gone", wait=True))
        await asyncio.sleep(0.2)
        await feeder.call("hook", session=s["id"], state="working")
        await asyncio.sleep(0.2)
        agent._forget(s["id"])  # what a remove does to the record, without the exited precondition
        with pytest.raises(AgentError, match="removed: .* went away"):
            await task
        agent.tmux.kill_session(s["id"])
