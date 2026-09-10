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
