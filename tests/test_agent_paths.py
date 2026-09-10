"""Design-promised paths the end-to-end tests do not reach: run log from the first byte, closed
sessions forgotten after their day, adoption of hand-started `ao-*` sessions, the offline hook
event queue, tail hygiene."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import wait_for, wait_state

from sessionorc import paths
from sessionorc.agent import _clean
from sessionorc.client import AgentError, LocalClient

pytestmark = pytest.mark.integration


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


async def test_forget_leaves_no_side_table_entry(agent, hookstub, tmp_path):
    async with LocalClient() as c:
        s = await c.call("create", name="side", dir=str(tmp_path), adapter="hookstub")
        await c.call("hook", session=s["id"], state="idle")
        await asyncio.sleep(0.5)  # a tick: git checked
        assert s["id"] in agent._last_hook and s["id"] in agent._git_checked
        await c.call("kill", id=s["id"])
        await wait_state(c, s["id"], "exited")
        await c.call("remove", id=s["id"])
        assert all(s["id"] not in d for d in (agent._last_hook, agent._git_checked, agent._pre_limited))


async def test_closed_sessions_are_forgotten_after_keep(agent, tmp_path, monkeypatch):
    monkeypatch.setattr("sessionorc.agent.CLOSED_KEEP", timedelta(seconds=0))
    async with LocalClient() as c, LocalClient() as sub:
        s = await c.call("create", name="c", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        await sub.call("subscribe")
        await c.call("close", id=s["id"])
        assert (await c.call("get", id=s["id"]))["state"] == "closed"

        async def gone():
            return all(x["id"] != s["id"] for x in await c.call("list"))

        assert await wait_for(gone)
        assert not (paths.sessions_dir() / f"{s['id']}.json").exists()
        # the tick's forget is announced to subscribers too (TD-009 routes every forget one way)
        while (ev := json.loads(await asyncio.wait_for(sub._reader.readline(), 5))).get("event") != "gone":
            pass
        assert ev["id"] == s["id"]


async def test_hand_started_session_is_adopted(agent, tmp_path):
    agent.tmux.new_session("ao-byhand-x", str(tmp_path), ["bash", "--norc"], {})
    async with LocalClient() as c:

        async def adopted():
            return any(x["id"] == "ao-byhand-x" and x["state"] == "idle" for x in await c.call("list"))

        assert await wait_for(adopted)
        s = await c.call("get", id="ao-byhand-x")
        assert s["adapter"] == "shell" and s["name"] == "byhand-x" and s["confidence"] == "scraped"


async def test_offline_hook_events_are_drained(agent, hookstub, tmp_path):
    """A hook that could not reach the socket appends to events/; the next tick applies it."""
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


async def test_forget_kills_leftover_pane_and_dead_panes_are_not_adopted(agent, tmp_path):
    async with LocalClient() as c:
        s = await c.call("create", name="f", dir=str(tmp_path), adapter="command", kind="command", argv=["true"])

        async def exited():
            return (await c.call("get", id=s["id"]))["state"] == "exited"

        assert await wait_for(exited)
        await c.call("remove", id=s["id"])
        await asyncio.sleep(1.0)  # several ticks
        assert all(x["id"] != s["id"] for x in await c.call("list"))  # not re-adopted
        assert not agent.tmux.has_session(s["id"])


async def test_resume_supersedes_the_exited_record(agent, hookstub, tmp_path):
    async with LocalClient() as c:
        old = await c.call("create", name="conv", dir=str(tmp_path), adapter="hookstub")
        await c.call("hook", session=old["id"], adapter_id="cc-123")
        await c.call("kill", id=old["id"])
        new = await c.call("create", name="conv", dir=str(tmp_path), adapter="hookstub", resume="cc-123")
        assert new["id"] != old["id"]
        states = {x["id"]: x["state"] for x in await c.call("list")}
        assert states[old["id"]] == "closed" and states[new["id"]] != "closed"


async def test_resume_of_a_live_conversation_is_refused(agent, hookstub, tmp_path, monkeypatch):
    """TD-012: two panes must never drive one conversation. A live record of ours, or a live session
    outside agentorc with that tool id, refuses the resume; the anchor rule (directories) is separate."""
    from sessionorc import adapters
    from sessionorc.adapters import ExternalSession

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    async with LocalClient() as c:
        live = await c.call("create", name="conv", dir=str(tmp_path / "a"), adapter="hookstub")
        await c.call("hook", session=live["id"], adapter_id="cc-live")
        with pytest.raises(AgentError, match="cc-live is still live in .*kill it first"):
            await c.call("create", name="again", dir=str(tmp_path / "b"), adapter="hookstub", resume="cc-live")
        assert [x["id"] for x in await c.call("list")] == [live["id"]]  # nothing was started
        monkeypatch.setattr(
            adapters,
            "external_sessions",
            lambda: [ExternalSession("ext", str(tmp_path / "c"), "vscode-conv", "cc-ext", "idle")],
        )
        with pytest.raises(AgentError, match="vscode-conv .*outside agentorc"):
            await c.call("create", name="again", dir=str(tmp_path / "b"), adapter="hookstub", resume="cc-ext")
        await c.call("kill", id=live["id"])
        new = await c.call("create", name="again", dir=str(tmp_path / "b"), adapter="hookstub", resume="cc-live")
        assert new["id"] != live["id"] and (await c.call("get", id=live["id"]))["state"] == "closed"  # superseded


async def test_concurrent_resumes_of_one_conversation_serialise(agent, tmp_path):
    """Two creates with the same resume id into different directories: the second sees the first's
    record (the conversation lock spans check and insert), so at most one is started."""
    from sessionorc.adapters import LaunchSpec

    class IdStub:
        name = "idstub"
        state_source = "hook"

        def launch(self, *, profile, resume, prompt, unattended, cwd, name=""):
            return LaunchSpec(argv=["bash", "--norc"], adapter_id=resume)

        def classify(self, pane, tail):
            return None

    from sessionorc import adapters

    adapters.load_all()
    adapters._REGISTRY["idstub"] = IdStub()
    try:
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        async with LocalClient() as c1, LocalClient() as c2:
            results = await asyncio.gather(
                c1.call("create", name="r", dir=str(tmp_path / "a"), adapter="idstub", resume="cc-race"),
                c2.call("create", name="r", dir=str(tmp_path / "b"), adapter="idstub", resume="cc-race"),
                return_exceptions=True,
            )
        started = [r for r in results if isinstance(r, dict)]
        refused = [r for r in results if isinstance(r, AgentError)]
        assert len(started) == 1 and len(refused) == 1 and "still live" in str(refused[0])
    finally:
        adapters._REGISTRY.pop("idstub", None)


async def test_pane_snapshot_older_than_a_remove_does_not_readopt(agent, tmp_path):
    """The tick snapshots panes in a thread; a remove that lands before reconcile must not have its
    dead pane adopted back from the stale snapshot (flaked under CPU load on 2026-09-07). The guard
    is keyed by the removed pane's own creation time (TD-020, TD-021): a pane of that name born
    later is a new hand-made session and is adopted on the first tick that sees it."""
    from sessionorc.tmux import PaneInfo

    async with LocalClient() as c:
        s = await c.call("create", name="gone", dir=str(tmp_path), adapter="command", kind="command", argv=["true"])

        async def exited():
            return (await c.call("get", id=s["id"]))["state"] == "exited"

        assert await wait_for(exited)
        born = agent.tmux.main_panes()[s["id"]].created
        stale = PaneInfo(session=s["id"], created=born, current_command="true", pane_pid=0, dead=True, dead_status=0)
        await c.call("remove", id=s["id"])
        assert agent._removed[s["id"]][0] == born
        agent._reconcile({s["id"]: stale}, {}, datetime.now(UTC))
        assert s["id"] not in agent.sessions
        # a wall-clock step between the remove and the tick changes nothing: the stamp is not a clock
        agent._reconcile({s["id"]: stale}, {}, datetime.now(UTC) + timedelta(hours=1))
        assert s["id"] not in agent.sessions
        # the same second of creation could be the same pane: still skipped (whole-second resolution)
        same = PaneInfo(session=s["id"], created=born, current_command="bash", pane_pid=0, dead=False, dead_status=None)
        agent._reconcile({s["id"]: same}, {}, datetime.now(UTC))
        assert s["id"] not in agent.sessions
        # the same name, a pane created after the removal: a new session, adopted at once
        fresh = PaneInfo(
            session=s["id"], created=born + 1, current_command="bash", pane_pid=0, dead=False, dead_status=None
        )
        agent._reconcile({s["id"]: fresh}, {}, datetime.now(UTC))
        assert s["id"] in agent.sessions
        agent._forget(s["id"])
        # the guard forgets the name after REMOVED_GUARD_SECONDS
        agent._removed[s["id"]] = (born, time.monotonic() - 61)
        agent._reconcile({s["id"]: stale}, {}, datetime.now(UTC))
        assert s["id"] in agent.sessions


async def test_screen_rule_is_a_labelled_fallback_that_a_fresh_hook_outranks(agent, hookstub, tmp_path, monkeypatch):
    """TD-015: with no hook yet, the screen rule's verdict is applied as scraped (the trust dialog
    case); once a hook reports, the screen no longer outranks it; `explain` says which."""
    from agentorc.adapters.claude_code import RULES_FILE
    from sessionorc.screen import Manifest

    monkeypatch.setattr(type(hookstub), "rules", Manifest.load(RULES_FILE))
    async with LocalClient() as c:
        s = await c.call("create", name="scr", dir=str(tmp_path), adapter="hookstub")
        assert s["state"] == "working" and s["confidence"] == "hook"  # the launch assumption, no hook yet
        fixture = Path(__file__).parent / "fixtures" / "screens" / "trust-dialog.txt"
        agent.tmux.send_prompt(s["id"], f"cat {fixture}")  # the real boxed dialog, through the tick's tail

        async def scraped_needs_you():
            x = await c.call("get", id=s["id"])
            return x["state"] == "needs-you" and x["confidence"] == "scraped"

        assert await wait_for(scraped_needs_you)
        x = await c.call("get", id=s["id"])
        assert x["pending"]["kind"] == "question" and "trust" in x["pending"]["text"]
        ex = await c.call("explain", id=s["id"])
        assert ex["match"]["rule"] == "trust-dialog" and "applied as scraped" in ex["reason"]
        assert any("trust the files" in ln for ln in ex["match"]["evidence"])
        # a hook reports: fresher than the screen, the rule no longer applies although still on screen
        await c.call("hook", session=s["id"], state="working")
        await asyncio.sleep(0.8)  # several ticks
        x = await c.call("get", id=s["id"])
        assert x["state"] == "working" and x["confidence"] == "hook"
        ex = await c.call("explain", id=s["id"])
        assert ex["match"]["rule"] == "trust-dialog" and "hook state wins" in ex["reason"] and ex["last_hook"]
        # a shell has no rules: explain says so
        sh = await c.call("create", name="sh", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        ex = await c.call("explain", id=sh["id"])
        assert ex["match"] is None and "no screen rules" in ex["reason"]
        await c.call("kill", id=s["id"])
        await c.call("kill", id=sh["id"])
