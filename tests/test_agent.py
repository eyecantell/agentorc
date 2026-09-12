"""End-to-end: an in-process host agent on a private tmux server and a temp AGENTORC_HOME."""

import asyncio
import json
import subprocess

import pytest
from conftest import FAST_TICK, wait_state

from sessionorc import adapters, naming, paths, reports
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import FindingEntry, ProgressEntry

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
        # same name, the holder exited → superseded, id reused, one card (design §4.1, TD-030)
        await c.call("kill", id=a["id"])
        await wait_state(c, a["id"], "exited")
        b = await c.call("create", name="one", dir=str(tmp_path), adapter="command", argv=["sleep", "30"])
        assert b["id"] == a["id"] and b["previous_run"] == a["run_log"]


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


async def test_registry_only_sessions_get_read_only_cards(agent, hookstub, tmp_path):
    """TD-010 (a): a live session the adapter sees outside agentorc (no tmux) is a read-only card:
    state scraped from the registry status, no pane, no controls; it leaves with its process.
    One of our own sessions, or a session in a directory one of ours holds, is not doubled."""
    from sessionorc.adapters import ExternalSession

    ext_dir = tmp_path / "vscode"
    ext_dir.mkdir()
    hookstub.external = [
        ExternalSession(adapter="hookstub", cwd=str(ext_dir), name="editor", tool_id="u-1", status="busy")
    ]
    async with LocalClient() as c:
        for _ in range(30):  # the next tick builds the card
            if any(x["id"] == "ext-u-1" for x in await c.call("list")):
                break
            await asyncio.sleep(0.1)
        card = await wait_state(c, "ext-u-1", "working")
        assert card["external"] is True and card["pane"] is False and card["confidence"] == "scraped"
        assert card["adapter_id"] == "u-1" and card["dir"] == str(ext_dir) and card["name"] == "editor"
        assert "ext-u-1" in [x["id"] for x in await c.call("list")]
        hookstub.external[0] = ExternalSession(
            adapter="hookstub", cwd=str(ext_dir), name="editor", tool_id="u-1", status="idle"
        )
        await wait_state(c, "ext-u-1", "idle")
        assert (await c.call("seen", id="ext-u-1"))["seen_at"]  # a person may look at it
        for act in ("kill", "close", "remove", "set_mode"):
            with pytest.raises(AgentError, match="outside agentorc"):
                await c.call(act, id="ext-u-1", **({"unattended": True} if act == "set_mode" else {}))
        with pytest.raises(AgentError, match="outside agentorc"):
            await c.call("send", id="ext-u-1", text="hi")
        # our own session in another directory, seen through the registry under its tool id: one card
        own = await c.call("create", name="own", dir=str(tmp_path), adapter="hookstub")
        await c.call("hook", session=own["id"], state="idle", adapter_id="u-own")
        hookstub.external.append(
            ExternalSession(adapter="hookstub", cwd=str(tmp_path), name="own", tool_id="u-own", status="busy")
        )
        # and a registry entry with an unknown id in a directory one of ours holds: our pane before its first hook
        hookstub.external.append(
            ExternalSession(adapter="hookstub", cwd=str(tmp_path), name="early", tool_id="u-early", status="busy")
        )
        await asyncio.sleep(FAST_TICK * 3)
        ids = [x["id"] for x in await c.call("list")]
        assert "ext-u-1" in ids and "ext-u-own" not in ids and "ext-u-early" not in ids
        # the process ends: the card goes
        hookstub.external.clear()
        for _ in range(30):
            if "ext-u-1" not in [x["id"] for x in await c.call("list")]:
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("registry-only card outlived its entry")
        with pytest.raises(AgentError, match="no session"):
            await c.call("get", id="ext-u-1")
        await c.call("kill", id=own["id"])


async def test_send_confirms_the_submit(agent, composerstubs, tmp_path, monkeypatch):
    """TD-027: every `send` waits for the paste to paint, presses Enter, and confirms the composer
    emptied — one `C-m` retry, then `prompt-stuck`. Faint text painted back into the composer (a
    tool's suggested next prompt) does not count as content."""
    monkeypatch.setattr("sessionorc.agent.SUBMIT_SECONDS", 0.6)

    async def submitted(sid: str) -> list[str]:
        return [t for t in await agent.rpc_tail(sid, 20) if t.startswith("SUBMITTED ")]

    async def create(name: str, adapter: str) -> str:
        (tmp_path / name).mkdir()
        s = await c.call("create", name=name, dir=str(tmp_path / name), adapter=adapter)
        assert await agent._poll(lambda: _painted(s["id"]), 5), "the composer child never painted its prompt"
        return s["id"]

    async def _painted(sid: str) -> bool:
        return any(t.startswith(">>") for t in await agent.rpc_tail(sid, 3))

    async with LocalClient() as c:
        # a well-behaved pane: one Enter, one submit, and the faint echo afterwards is not "stuck"
        s0 = await create("c0", "composer0")
        await c.call("send", id=s0, text="first prompt")
        assert await submitted(s0) == ["SUBMITTED first prompt"]
        await c.call("send", id=s0, text="second prompt")  # composer holds faint "first prompt" here
        assert await submitted(s0) == ["SUBMITTED first prompt", "SUBMITTED second prompt"]

        # the swallow: Enter lost, C-m lands, one submit, no duplicate text
        s1 = await create("c1", "composer1")
        await c.call("send", id=s1, text="retried prompt")
        assert await submitted(s1) == ["SUBMITTED retried prompt"]

        # both lost: honest failure, the text left in the composer, nothing submitted
        s2 = await create("c2", "composer2")
        with pytest.raises(AgentError, match="prompt-stuck"):
            await c.call("send", id=s2, text="stuck prompt")
        assert await submitted(s2) == []
        assert any(t.rstrip().endswith(">> stuck prompt") for t in await agent.rpc_tail(s2, 5))


async def test_orchestrate_grant_gates_acting_rpcs(agent, tmp_path):
    """Design §4.8, §9 invariant 11: a session acting on another session needs `orchestrate`;
    self, no caller, and every read pass; `set_grants` is a person's (or a granted session's)."""
    async with LocalClient() as person:
        a = (await person.call("create", name="a", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        b = (await person.call("create", name="b", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        assert (await person.call("get", id=a))["capabilities"] == []
        async with LocalClient(caller=a) as worker:
            # session → other: refused for every acting RPC, one message
            for method, params in (
                ("send", {"id": b, "text": "echo no"}),
                ("keys", {"id": b, "keys": ["Enter"]}),
                ("kill", {"id": b}),
                ("close", {"id": b}),
                ("set_mode", {"id": b, "unattended": True}),
                ("remove", {"id": b}),
                ("create", {"name": "c", "dir": str(tmp_path), "adapter": "shell"}),
                ("set_grants", {"id": a, "add": ["orchestrate"]}),  # no self-grant
                ("set_grants", {"id": b, "add": ["orchestrate"]}),
            ):
                with pytest.raises(AgentError, match="needs the orchestrate grant"):
                    await worker.call(method, **params)
            assert (await person.call("get", id=b))["state"] != "closed"
            # session → self: allowed
            await worker.call("send", id=a, text="echo SELF-OK")
            assert (await worker.call("set_mode", id=a, unattended=True))["unattended"] is True
            # reads are never gated
            assert b in [s["id"] for s in await worker.call("list")]
            assert (await worker.call("get", id=b))["id"] == b
            assert isinstance(await worker.call("tail", id=b, lines=3), list)
            assert (await worker.call("explain", id=b, lines=3))["id"] == b
            # unknown caller: a session this agent never started holds no grant
            async with LocalClient(caller="ao-stranger") as stranger:
                with pytest.raises(AgentError, match="needs the orchestrate grant"):
                    await stranger.call("kill", id=b)
            # a person grants; the next call the session makes sees it
            with pytest.raises(AgentError, match="unknown grant"):
                await person.call("set_grants", id=a, add=["root"])
            granted = await person.call("set_grants", id=a, add=["orchestrate"])
            assert granted["capabilities"] == ["orchestrate"]
            await worker.call("send", id=b, text="echo FROM-A")
            c = await worker.call("create", name="c", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
            assert c["capabilities"] == []
            killed = await worker.call("kill", id=b)
            assert killed["state"] == "exited"
            # revoked: refused again on the very next call
            assert (await person.call("set_grants", id=a, remove=["orchestrate"]))["capabilities"] == []
            with pytest.raises(AgentError, match="needs the orchestrate grant"):
                await worker.call("kill", id=c["id"])
        # `capabilities` at create, and it survives the store round trip
        d = await person.call(
            "create",
            name="d",
            dir=str(tmp_path),
            adapter="shell",
            argv=["bash", "--norc"],
            capabilities=["orchestrate"],
        )
        assert d["capabilities"] == ["orchestrate"]
        assert json.loads((paths.sessions_dir() / f"{d['id']}.json").read_text())["capabilities"] == ["orchestrate"]
        with pytest.raises(AgentError, match="unknown grant"):
            await person.call("create", name="e", dir=str(tmp_path), adapter="shell", capabilities=["sudo"])
        for sid in (a, c["id"], d["id"]):
            await person.call("kill", id=sid)


async def test_report_channels_are_ungated_and_declared_wins(agent, tmp_path):
    """TD-028 step 2, design §4.8: `progress` and `findings` on the record, written by `rpc_progress`
    / `rpc_finding`; ungated (no grant, any caller); one reference is one entry; §9 invariant 10
    refuses a derived write over a declared one and says which entry it refused."""
    async with LocalClient() as person:
        a = await person.call(
            "create",
            name="grinder",
            dir=str(tmp_path),
            adapter="shell",
            argv=["bash", "--norc"],
            lane=["td-27", "TD-19", "TD-027"],  # deduped after canonicalisation, or the count lies
        )
        sid = a["id"]
        assert a["lane"] == ["TD-027", "TD-019"] and a["progress"] == [] and a["findings"] == []
        with pytest.raises(AgentError, match="either `free-pick` or a list"):
            await person.call("create", name="bad", dir=str(tmp_path), adapter="shell", lane=["free-pick", "TD-1"])
        # the session declares its own, with no grant of any kind
        async with LocalClient(caller=sid) as worker:
            s = await worker.call("progress", id=sid, ref="td-27")
            assert s["progress"] == [
                {
                    "ref": "TD-027",
                    "status": "claimed",
                    "pr": None,
                    "why": None,
                    "at": s["progress"][0]["at"],
                    "source": "declared",
                }
            ]
            s = await worker.call("progress", id=sid, ref="TD-027", status="done", pr="#60")
            assert len(s["progress"]) == 1 and s["progress"][0]["status"] == "done" and s["progress"][0]["pr"] == 60
            s = await worker.call("finding", id=sid, ref="TD-029", priority="low")
            assert s["findings"] == [
                {"ref": "TD-029", "priority": "low", "at": s["findings"][0]["at"], "source": "declared"}
            ]
            with pytest.raises(AgentError, match="unknown progress status"):
                await worker.call("progress", id=sid, ref="TD-019", status="finished")
            with pytest.raises(AgentError, match="needs a reference"):
                await worker.call("progress", id=sid, ref="  ")
            with pytest.raises(AgentError, match="not a PR number"):
                await worker.call("progress", id=sid, ref="TD-019", status="done", pr="sixty")
            with pytest.raises(AgentError, match="unknown report source"):
                await worker.call("progress", id=sid, ref="TD-019", source="rumour")
        # §9 invariant 10: the tick's derived entry cannot overwrite the declaration
        refused = await person.call("progress", id=sid, ref="TD-027", status="claimed", source="derived")
        assert refused["progress"][0]["status"] == "done" and refused["refused"]["source"] == "derived"
        # ... but it fills in a reference the session never declared, marked derived
        filled = await person.call("progress", id=sid, ref="TD-019", source="derived")
        assert [(p["ref"], p["source"]) for p in filled["progress"]] == [("TD-027", "declared"), ("TD-019", "derived")]
        # a declaration replaces that derived entry the moment it arrives
        async with LocalClient(caller=sid) as worker:
            later = await worker.call("progress", id=sid, ref="TD-019", status="dropped", why="phase 5")
            assert later["progress"][1] == {
                "ref": "TD-019",
                "status": "dropped",
                "pr": None,
                "why": "phase 5",
                "at": later["progress"][1]["at"],
                "source": "declared",
            }
        # a foreign session may write a record's channels: reports are not an acting RPC (§4.8)
        async with LocalClient(caller="ao-stranger") as stranger:
            assert (await stranger.call("finding", id=sid, ref="59"))["findings"][-1]["ref"] == "#59"
        # and it all survives the store
        on_disk = json.loads((paths.sessions_dir() / f"{sid}.json").read_text())
        assert [p["ref"] for p in on_disk["progress"]] == ["TD-027", "TD-019"]
        assert [f["ref"] for f in on_disk["findings"]] == ["TD-029", "#59"]
        assert (await person.call("get", id=sid))["lane"] == ["TD-027", "TD-019"]
        await person.call("kill", id=sid)


async def test_the_tick_derives_report_entries_and_never_overwrites_a_declaration(agent, tmp_path, monkeypatch):
    """TD-028 step 3, design §4.8: the tick reads the session's branch and its PRs and fills in the
    channels, marked `derived` — and §9 invariant 10 keeps it off what the session declared."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@e.com"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    (repo / "f").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "one"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "td077-cap"], check=True)
    monkeypatch.setattr(
        reports, "_prs", lambda directory, **kw: [{"number": 77, "state": "OPEN", "headRefName": "td077-cap"}]
    )
    async with LocalClient() as person:
        sid = (await person.call("create", name="w", dir=str(repo), adapter="shell", argv=["bash", "--norc"]))["id"]
        await agent.tick()
        await agent._derive_task  # detached so a slow `gh` never holds up the tick
        s = await person.call("get", id=sid)
        assert [(p["ref"], p["status"], p["pr"], p["source"]) for p in s["progress"]] == [
            ("TD-077", "claimed", 77, "derived")
        ]
        # the session declares the same reference done: the declaration replaces the derived entry
        await person.call("progress", id=sid, ref="TD-077", status="done", pr=77)
        # ... and the next derivation cannot put it back to claimed (§9 invariant 10)
        agent._derived_at.clear()
        await agent.tick()
        await agent._derive_task
        s = await person.call("get", id=sid)
        assert [(p["ref"], p["status"], p["source"]) for p in s["progress"]] == [("TD-077", "done", "declared")]
        # every entry of a multi-entry derivation is applied and saved: these upserts are the write,
        # so an `any()` over a generator would have stopped at the first one (review 2026-09-11)
        monkeypatch.setattr(
            reports,
            "derive",
            lambda directory, branch, pending=None: (
                [ProgressEntry(ref="TD-080", source="derived"), ProgressEntry(ref="TD-081", source="derived")],
                [FindingEntry(ref="TD-082", source="derived"), FindingEntry(ref="TD-083", source="derived")],
            ),
        )
        agent._derived_at.clear()
        await agent.tick()
        await agent._derive_task
        on_disk = json.loads((paths.sessions_dir() / f"{sid}.json").read_text())
        assert [p["ref"] for p in on_disk["progress"]] == ["TD-077", "TD-080", "TD-081"]
        assert [f["ref"] for f in on_disk["findings"]] == ["TD-082", "TD-083"]
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)
        assert sid not in agent._derived_at  # no key outlives the record


async def test_the_model_in_use_comes_from_the_hook_and_from_the_tick(agent, tmp_path, monkeypatch):
    """TD-031: `model` on the record — reported by the hook (SessionStart, a `/model` switch) and
    cross-checked on the tick by whatever the adapter can read. An adapter with no opinion, or a
    reading it cannot make, leaves the field alone rather than guessing from the profile."""
    async with LocalClient() as person:
        sid = (await person.call("create", name="m", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        assert (await person.call("get", id=sid))["model"] is None  # `shell` cannot tell, and never will
        await person.call("hook", session=sid, adapter_id="conv-1", model="claude-opus-5", state="working")
        assert (await person.call("get", id=sid))["model"] == "claude-opus-5"
        await person.call("hook", session=sid, state="idle")  # an event with no model says nothing
        assert (await person.call("get", id=sid))["model"] == "claude-opus-5"
        await person.call("hook", session=sid, model="claude-sonnet-5")  # a `/model` switch mid-session
        assert (await person.call("get", id=sid))["model"] == "claude-sonnet-5"
        # the tick asks the adapter, which for `shell` has nothing to ask — so nothing changes
        agent._model_checked.clear()
        await agent.tick()
        assert (await person.call("get", id=sid))["model"] == "claude-sonnet-5"
        # an adapter that can read it: the tick picks up a switch the hook never reported
        monkeypatch.setattr(
            adapters.get("shell"), "model_in_use", lambda session_id, cwd, profile="": "claude-fable-5-1", raising=False
        )
        agent._model_checked.clear()
        await agent.tick()
        assert (await person.call("get", id=sid))["model"] == "claude-fable-5-1"
        assert json.loads((paths.sessions_dir() / f"{sid}.json").read_text())["model"] == "claude-fable-5-1"
        # a reading it cannot make leaves the last one alone
        monkeypatch.setattr(adapters.get("shell"), "model_in_use", lambda *a, **kw: None, raising=False)
        agent._model_checked.clear()
        await agent.tick()
        assert (await person.call("get", id=sid))["model"] == "claude-fable-5-1"
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)
        assert sid not in agent._model_checked  # no key outlives the record


async def test_one_name_one_session(agent, tmp_path, monkeypatch):
    """Design §4.1, §9 invariant 12, TD-030: within a scope a name identifies one session — a live
    holder refuses (and says which id to switch to), an exited or closed one is superseded and its
    id reused with its run log kept, and a suffix exists only for a tmux session nobody has a
    record of, and is then part of the name the Herd shows."""
    async with LocalClient() as c:
        a = await c.call("create", name="aotest", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        with pytest.raises(AgentError, match=f"aotest is running — `ao focus {a['id']}`") as e:
            await c.call("create", name="aotest", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        assert e.value.data == {"holder": a["id"], "holder_state": a["state"]}  # actionable, not just prose
        assert [x["id"] for x in await c.call("list")] == [a["id"]]  # nothing was started
        # a closed holder is superseded: same id, one card, the previous run still reachable
        await c.call("close", id=a["id"])
        b = await c.call("create", name="aotest", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        assert b["id"] == a["id"] and b["name"] == "aotest" and b["previous_run"] == a["run_log"]
        assert b["state"] != "closed" and [x["id"] for x in await c.call("list")] == [b["id"]]
        # the same name in another scope is another session, suffix-free
        other = tmp_path / "elsewhere"
        other.mkdir()
        far = await c.call("create", name="aotest", dir=str(other), adapter="shell", argv=["bash", "--norc"])
        assert far["id"] != b["id"] and far["name"] == "aotest" and not far["id"].endswith("-2")
        # a live tmux session holding the id with no record yet: refused on tmux's own answer, not
        # on whether the tick has adopted it — the same `ao new` must not depend on the second
        hand_id = naming.base_id(tmp_path, None, "byhand")
        agent.tmux.new_session(hand_id, str(tmp_path), ["bash", "--norc"], {})
        with pytest.raises(AgentError, match="byhand is running") as e:
            await c.call("create", name="byhand", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        assert e.value.data == {"holder": hand_id, "holder_state": "unrecorded"}
        agent.tmux.kill_session(hand_id)  # no record of ours: the agent never made one
        # A *dead* pane nobody has a record of holds nothing worth keeping: killed, id reused.
        # Asserted on the two helpers, because the tick adopts such a pane within a tick and the
        # window in which no record exists is not something a test can wait for.
        dead_id = naming.base_id(tmp_path, None, "leftover")
        agent.tmux.new_session(dead_id, str(tmp_path), ["sh", "-c", "exit 0"], {})
        for _ in range(40):
            pane = agent.tmux.main_panes(naming.PREFIX).get(dead_id)
            if pane and pane.dead:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("the leftover pane never died")
        agent.sessions.pop(dead_id, None)  # whatever the tick made of it: this is the no-record case
        assert await agent._name_holder(tmp_path, None, "leftover") == dead_id
        assert await agent._take_name(dead_id) == (None, dead_id)  # nothing to hand on, the id freed
        assert not agent.tmux.has_session(dead_id)
        for sid in (b["id"], far["id"]):
            await c.call("kill", id=sid)


async def test_a_failed_launch_leaves_the_superseded_record_standing(agent, tmp_path, monkeypatch):
    """The name is taken only once the launch has succeeded, and the record it supersedes is
    replaced in place rather than forgotten — so a launch that fails after the check loses
    neither the old record nor the run log it would have handed on (review 2026-09-11)."""
    async with LocalClient() as c:
        old = await c.call("create", name="keep", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        await c.call("kill", id=old["id"])
        await wait_state(c, old["id"], "exited")
        monkeypatch.setattr(
            adapters.get("shell"), "launch", lambda **kw: (_ for _ in ()).throw(ValueError("no launch today"))
        )
        with pytest.raises(AgentError, match="no launch today"):
            await c.call("create", name="keep", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        still = await c.call("get", id=old["id"])
        assert still["state"] == "exited" and still["run_log"] == old["run_log"]


async def test_an_unnamed_session_is_named_by_the_agent(agent, tmp_path):
    """TD-030 step 3: `ao shell` sends no name, so the agent picks `shell`, `shell-2`, … — a name
    the caller chose would collide with §4.1's rule the moment a second shell started here."""
    async with LocalClient() as c:
        one = await c.call("create", name="", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        two = await c.call("create", name="  ", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        assert (one["name"], two["name"]) == ("shell", "shell-2")
        assert one["id"].endswith("-shell") and two["id"].endswith("-shell-2")
        for sid in (one["id"], two["id"]):
            await c.call("kill", id=sid)
