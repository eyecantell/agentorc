"""The exited pill's cause, the host agent's half (design §4.2, §4.5 row 5 (b), TD-490/TD-498): the
record's `ended` for each way a run ends, `confidence: tick` on what the host agent observed, and
`last_tick` in `host.json` for the next run's *down since*."""

import json
from datetime import UTC, datetime, timedelta

from conftest import wait_for, wait_state

from sessionorc import paths
from sessionorc.agent import HostAgent
from sessionorc.client import LocalClient
from sessionorc.models import Session
from sessionorc.store import HostStore
from sessionorc.tmux import PaneInfo

SHELL = ["bash", "--norc", "--noprofile"]


async def test_the_tools_own_end_keeps_its_reason(agent, hookstub, tmp_path):
    async with LocalClient() as c, LocalClient() as feeder:
        s = await c.call("create", name="w", dir=str(tmp_path), adapter="hookstub")
        await feeder.call("hook", session=s["id"], state="idle")
        await wait_state(c, s["id"], "idle")
        await feeder.call("hook", session=s["id"], state="exited", reason="logout")
        got = await c.call("get", id=s["id"])
        assert got["state"] == "exited" and got["confidence"] == "hook"
        assert got["ended"]["how"] == "tool" and got["ended"]["reason"] == "logout" and got["ended"]["at"]
        assert "code" not in got["ended"]
        # a run that comes back (a SessionStart after it) has not ended
        await feeder.call("hook", session=s["id"], state="working")
        assert (await c.call("get", id=s["id"]))["ended"] is None
        await c.call("kill", id=s["id"])
        await c.call("remove", id=s["id"])


async def test_a_dead_pane_ends_with_its_status_and_is_observed(agent, tmp_path):
    async with LocalClient() as c:
        s = await c.call("create", name="nat", dir=str(tmp_path), adapter="shell", argv=SHELL)
        await wait_state(c, s["id"], "idle")
        await c.call("send", id=s["id"], text="exit 3")
        got = await wait_state(c, s["id"], "exited")
        assert got["confidence"] == "tick" and got["ended"]["how"] == "pane"
        # the code is tmux's to report and lags the dead flag (on some runners past any wait), so it
        # is pinned as the dead pane's status, whatever tmux has said by now
        await wait_for(lambda: agent.sessions[s["id"]].exit_code == 3, timeout=3.0)
        await agent.tick()
        rec = agent.sessions[s["id"]]
        assert rec.ended["code"] == rec.exit_code
        await c.call("remove", id=s["id"])


async def test_a_kill_says_who_pressed_it(agent, tmp_path, monkeypatch):
    async with LocalClient() as c:
        a = await c.call("create", name="a", dir=str(tmp_path), adapter="shell", argv=SHELL)
        killed = await c.call("kill", id=a["id"])
        assert killed["confidence"] == "tick"
        assert killed["ended"]["how"] == "kill" and killed["ended"]["by"] == "person"
        b = await c.call("create", name="b", dir=str(tmp_path), adapter="shell", argv=SHELL)
        rec = await agent.rpc_kill(b["id"], caller="ao-repo-manager-1")
        assert rec["ended"]["by"] == "ao-repo-manager-1" and "why" not in rec["ended"]
        # a session cannot write the tick's word: `killer` is taken only from a call with no caller
        c2 = await c.call("create", name="c", dir=str(tmp_path), adapter="shell", argv=SHELL)
        rec = await agent.rpc_kill(c2["id"], caller="ao-repo-manager-1", killer={"by": "tick", "why": "x"})
        assert rec["ended"]["by"] == "ao-repo-manager-1"
        # a second kill of an exited record keeps the first one's words: the person's later press
        # never rewrites a manager's kill, nor its time (TD-508)
        first = dict(rec["ended"])
        monkeypatch.setattr("sessionorc.agent.now_iso", lambda: "2099-01-01T00:00:00Z")
        again = await c.call("kill", id=c2["id"])
        assert again["ended"] == first and again["ended"]["by"] == "ao-repo-manager-1"
        for x in (a, b, c2):
            await c.call("remove", id=x["id"])


def test_a_dead_pane_keeps_the_tools_end_and_refreshes_its_own_code(agent, tmp_path):
    """`_observe` over a dead pane (TD-509): the tool's own end, its hook landed first, keeps its
    words; a pane's own end takes tmux's status once it arrives, as `exit_code` does."""
    now = datetime.now(UTC)
    tool = Session(id="ao-x-t", name="t", kind="agent", dir=str(tmp_path), adapter="shell", state="exited")
    tool.ended = {"how": "tool", "at": "2026-10-09T12:00:00Z", "reason": "logout"}
    pane = Session(id="ao-x-p", name="p", kind="agent", dir=str(tmp_path), adapter="shell", state="working")
    for status in (None, 3):
        for s in (tool, pane):
            agent._observe(s, PaneInfo(s.id, 0, "bash", 1, dead=True, dead_status=status), [], now)
    assert tool.ended == {"how": "tool", "at": "2026-10-09T12:00:00Z", "reason": "logout"}
    assert tool.exit_code == 3
    assert pane.state == "exited" and pane.confidence == "tick"
    assert pane.ended["how"] == "pane" and pane.ended["code"] == 3


async def test_closing_an_exited_record_keeps_its_end_and_is_the_ticks(agent, tmp_path):
    """A close is among the `tick` readings (§4.2), and an exited record closed keeps how it ended
    (TD-509): `set_state` clears `ended` only for a run that came back."""
    async with LocalClient() as c:
        s = await c.call("create", name="k", dir=str(tmp_path), adapter="shell", argv=SHELL)
        killed = await c.call("kill", id=s["id"])
        closed = await c.call("close", id=s["id"])
        assert closed["state"] == "closed" and closed["confidence"] == "tick"
        assert closed["ended"] == killed["ended"]
        await c.call("remove", id=s["id"])


async def test_a_forgotten_hosts_records_close_as_the_ticks_and_keep_their_end(agent):
    from test_link import record

    agent.sessions.clear()
    ended = {"how": "pane", "at": "2026-10-09T12:00:00Z", "code": 0}
    agent._take_records("laptop", [record("ao-x-v", state="exited", ended=ended)], whole=True)
    async with LocalClient() as c:
        await c.call("forget_host", host="laptop")
    held = agent.remote["laptop"]["ao-x-v"]
    assert held.state == "closed" and held.confidence == "tick" and held.ended == ended


async def test_a_failed_last_tick_write_is_never_a_failed_tick(agent, monkeypatch):
    def refuse(_rec):
        raise OSError("read-only")

    monkeypatch.setattr(agent.host_store, "save", refuse)
    await agent.tick()  # completes: the write's failure is logged, not raised
    assert agent._ticked and agent._host_rec["last_tick"]


async def test_the_stop_time_kill_is_the_ticks(agent, tmp_path):
    async with LocalClient() as c:
        s = await c.call(
            "create", name="w", dir=str(tmp_path), adapter="shell", argv=SHELL,
            unattended=True, run_until="2026-09-13T06:00:00Z",
        )  # fmt: skip
        await agent.tick()  # no wrap-up prompt: stopped at once
        got = await c.call("get", id=s["id"])
        assert got["state"] == "exited" and got["ended"]["by"] == "tick" and got["ended"]["why"] == "stop time"
        await c.call("remove", id=s["id"])


async def test_a_gone_pane_says_when_it_was_found(agent, tmp_path, monkeypatch):
    monkeypatch.setattr("sessionorc.agent_common.CREATE_GRACE", timedelta(0))
    async with LocalClient() as c:
        s = await c.call("create", name="g", dir=str(tmp_path), adapter="shell", argv=SHELL)
        await wait_state(c, s["id"], "idle")
        agent.tmux.kill_session(s["id"])  # the tmux session goes behind the agent's back
        got = await wait_state(c, s["id"], "exited")
        assert got["confidence"] == "tick" and got["pane"] is False
        assert got["ended"]["how"] == "gone" and got["ended"]["found"] == got["ended"]["at"]
        assert "down_since" not in got["ended"]  # the agent has ticked since its start
        await c.call("remove", id=s["id"])


def test_down_since_is_the_previous_runs_last_tick_on_the_first_tick_alone(agent):
    now = datetime(2026, 10, 9, 16, 50, tzinfo=UTC)
    agent._ticked, agent._prev_last_tick = False, "2026-10-09T12:30:00Z"
    assert agent._gone_ending(now) == {
        "how": "gone",
        "at": "2026-10-09T16:50:00Z",
        "found": "2026-10-09T16:50:00Z",
        "down_since": "2026-10-09T12:30:00Z",
    }
    # a last tick inside the create grace is a restart nobody would call down
    agent._prev_last_tick = "2026-10-09T16:49:55Z"
    assert "down_since" not in agent._gone_ending(now)
    agent._prev_last_tick = "not a time"
    assert "down_since" not in agent._gone_ending(now)
    agent._ticked, agent._prev_last_tick = True, "2026-10-09T12:30:00Z"
    assert "down_since" not in agent._gone_ending(now)


async def test_last_tick_is_kept_in_host_json_and_read_at_start(agent):
    await agent.tick()
    rec = json.loads(paths.host_file().read_text())
    assert rec["last_tick"] and "teams" in rec
    assert HostStore().load()["last_tick"] == rec["last_tick"]
    # the next run reads it before its first tick writes over it
    assert HostAgent(tmux=agent.tmux)._prev_last_tick == rec["last_tick"]


def test_host_json_without_a_last_tick_loads_as_before(tmp_path):
    p = tmp_path / "host.json"
    p.write_text(json.dumps({"teams": {"t": {"balance": {}}}, "last_tick": 5}))
    assert HostStore(p).load() == {"teams": {"t": {"balance": {}}}}
    p.write_text("[]")
    assert HostStore(p).load() == {"teams": {}}


def test_session_end_carries_its_reason():
    from agentorc.adapters.claude_code.hook import translate

    assert translate({"hook_event_name": "SessionEnd", "reason": "prompt_input_exit"})["reason"] == "prompt_input_exit"
    assert "reason" not in translate({"hook_event_name": "SessionEnd"})


def test_only_a_scraped_state_is_marked_a_guess():
    from agentorc import cli
    from agentorc.ui import cards

    base = Session(id="ao-x", name="x", kind="interactive", dir="/tmp", adapter="shell", state="exited").view()
    for conf, guessed in (("scraped", True), ("tick", False), ("hook", False)):
        rec = {**base, "confidence": conf}
        assert ("~" in cli.status_line(rec)) is guessed
        assert cards.view(rec)["scraped"] is guessed
