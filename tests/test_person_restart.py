"""TD-250 slice 1, design §6 rule 2 *A person's restart*: the `restart` RPC. A person's alone; a
supervised member that is idle, exited or closed is closed under the tick's own clean-and-pushed
test and created again from its launch record, with `restarts` the one entry `{why: person}`, no
marks, its mail kept; every refusal is by name and changes nothing."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks, wait_for

from sessionorc import modes, paths
from sessionorc.agent import RESTART_CEILING
from sessionorc.agent_common import _counted
from sessionorc.client import AgentError, LocalClient
from sessionorc.gitinfo import UNKNOWN, work_left
from sessionorc.models import MailEntry, ProgressEntry

CLEAN = {"branch": "w", "dirty": 0, "unpushed": 0, "upstream": "origin/w"}


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


async def _painted(agent, sid: str) -> bool:
    return any(t.startswith(">>") for t in await agent.rpc_tail(sid, 3))


async def _member(agent, person, tmp_path, name="w", **kw) -> str:
    (tmp_path / name).mkdir()
    params = {
        "name": name,
        "dir": str(tmp_path / name),
        "adapter": "composer0",
        "unattended": True,
        "supervised": True,
        "prompt": "the brief",
    }
    sid = (await person.call("create", **{**params, **kw}))["id"]
    assert await wait_for(lambda: _painted(agent, sid), timeout=5), "the composer child never painted its prompt"
    agent.sessions[sid].git = dict(CLEAN)
    return sid


@pytest.mark.unit
def test_the_one_clean_and_pushed_test_reads_unknown_as_work_left():
    assert work_left(None) == UNKNOWN and work_left({}) == UNKNOWN
    assert work_left({"dirty": 0}) == UNKNOWN and work_left({"dirty": None, "unpushed": 0}) == UNKNOWN
    assert work_left({"dirty": 2, "unpushed": 1}) == "2 uncommitted"
    assert work_left({"dirty": 0, "unpushed": 1, "pushed_against": "origin/w"}) == "1 unpushed (vs origin/w)"
    assert work_left({"dirty": 0, "unpushed": 1}) == "1 unpushed (vs its remote)"
    assert work_left(dict(CLEAN)) is None


@pytest.mark.unit
def test_a_person_s_restart_never_counts_toward_the_ceiling_and_a_node_forwards_it():
    now = datetime.now(UTC)
    entries = [{"at": _iso(now), "why": "person"}, {"at": _iso(now), "why": "crash"}]
    assert [r["why"] for r in _counted(entries, now)] == ["crash"]
    assert "restart" in modes.HOME_EDITS
    assert "waits for the link" in modes.offline_refusal("restart", None, {"id": "x"}, host="n", home="h")


@pytest.mark.integration
async def test_an_early_wanted_member_is_back_in_its_teams_run(agent, composerstubs, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        lead = await _member(agent, person, tmp_path, name="lead")
        sid = await _member(agent, person, tmp_path, lane=["TD-1"], controllers=[lead], team="g", role="grinder")
        old = agent.sessions[sid]
        await agent.rpc_hook(sid, state="idle")
        old.restart_wanted = {"at": _iso(now), "why": "context bound", "early": True}
        old.restart_blocked = {"at": _iso(now), "dirty": 0, "unpushed": 0}
        old.out_of_work = {"at": _iso(now), "why": "x"}
        old.progress = [ProgressEntry(ref="TD-001", status="done", pr=5)]
        old.restarts = [{"at": _iso(now), "why": "wanted", "done": [], "left": []}]
        old.unattended = False  # a person took it over with `ao mode`: the launch record still says unattended
        old.inbox = [MailEntry(id="m-1", from_=lead, to=[sid], kind="note", text="rebase #7 first", at=_iso(now))]
        with pytest.raises(AgentError, match="a person's own act, refused to every session"):
            async with LocalClient(caller=lead) as manager:
                await manager.call("restart", id=sid)
        assert agent.sessions[sid] is old and old.state == "idle", "a session's call changes nothing"
        view = await person.call("restart", id=sid)
        new = agent.sessions[sid]
        assert new is not old and old.state == "closed" and view["id"] == sid
        assert new.state not in ("exited", "closed") and new.unattended and new.supervised
        assert new.controllers == [lead] and new.lane == ["TD-001"] and (new.team, new.role) == ("g", "grinder")
        assert [(r["why"], sorted(r)) for r in new.restarts] == [("person", ["at", "prompt", "why"])]
        assert new.restarts[0]["prompt"] == "stored", "a prompt typed whole is replayed as stored"
        assert new.restart_wanted is None and new.restart_blocked is None and new.restart_ceiling is None
        assert new.out_of_work is None and new.progress == []
        assert [e.id for e in new.inbox] == ["m-1"], "its mail is kept"
        assert await wait_for(lambda: _painted(agent, sid), timeout=5), "it runs on its launch record's prompt"
        await person.call("kill", id=sid)
        await person.call("kill", id=lead)


@pytest.mark.integration
async def test_an_exited_member_at_its_ceiling_is_restarted_and_the_ceiling_is_gone(agent, composerstubs, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path)
        old = agent.sessions[sid]
        await person.call("kill", id=sid)
        assert await wait_for(lambda: _state(agent, sid, "exited"), timeout=5)
        old.git = dict(CLEAN)
        old.restarts = [{"at": _iso(now), "why": "crash"} for _ in range(RESTART_CEILING)]
        old.restart_ceiling = {"at": _iso(now), "count": RESTART_CEILING}
        await person.call("restart", id=sid)
        new = agent.sessions[sid]
        assert new is not old and new.restart_ceiling is None
        assert [r["why"] for r in new.restarts] == ["person"] and _counted(new.restarts, now) == []
        # a closed one is restarted too: a person's own Close is theirs to take back
        await person.call("close", id=sid)
        new.git = dict(CLEAN)
        await person.call("restart", id=sid)
        assert agent.sessions[sid] is not new and agent.sessions[sid].state not in ("exited", "closed")
        await person.call("kill", id=sid)


async def _state(agent, sid: str, state: str) -> bool:
    await agent.tick()
    return agent.sessions[sid].state == state


@pytest.mark.integration
async def test_every_refusal_is_by_name_and_changes_nothing(agent, composerstubs, tmp_path, monkeypatch):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path)
        rec = agent.sessions[sid]
        launch = paths.launch_dir() / f"{sid}.json"

        async def refused(match: str) -> None:
            with pytest.raises(AgentError, match=match):
                await person.call("restart", id=sid)
            assert agent.sessions[sid] is rec and rec.state not in ("closed", "exited"), match
            assert rec.restarts == [], match

        await agent.rpc_hook(sid, state="working")
        await refused("w is working: a restart is of a session that is idle, exited or closed")
        await agent.rpc_hook(sid, state="idle")
        rec.set_state("scheduled", confidence="hook")
        await refused(f"w is scheduled and has not started: `ao at {sid} now` starts it")
        rec.set_state("idle", confidence="hook")
        rec.superseded_by = "w-2"
        await refused("w was resumed as w-2: that is the record to restart")
        rec.superseded_by = None
        rec.git = {**CLEAN, "dirty": 2}
        await refused("w has 2 uncommitted")
        rec.git = {**CLEAN, "unpushed": 1, "pushed_against": "origin/w"}
        await refused(r"w has 1 unpushed \(vs origin/w\)")
        rec.git = None
        await refused("w has git state unknown")
        rec.git = dict(CLEAN)
        del agent.sessions[sid]
        agent.sessions[sid + "-2"] = rec  # a record under a suffixed id: the create would not find it
        with pytest.raises(AgentError, match="does not hold its launch record's name, 'w'"):
            await person.call("restart", id=sid + "-2")
        del agent.sessions[sid + "-2"]
        agent.sessions[sid] = rec
        assert rec.state == "idle"
        twin = await _member(agent, person, tmp_path, name="twin")
        agent.sessions[twin].name, agent.sessions[twin].dir = "w", rec.dir
        await refused(f"w is the name of {twin}")
        await person.call("kill", id=twin)
        await person.call("remove", id=twin)
        rec.seat = {"trigger": "asks"}
        await refused("w is a seat: rule 3 fills it")
        rec.seat = None
        rec.suspended = {"at": _iso(now), "why": "alarm"}
        await refused("w is suspended")
        rec.suspended = None
        monkeypatch.setattr(agent, "_profile_gated", lambda *a, **k: True)
        await refused("over its usage line")
        monkeypatch.setattr(agent, "_profile_gated", lambda *a, **k: False)
        kept = launch.read_text(encoding="utf-8")
        launch.write_text(json.dumps({**json.loads(kept), "run_until": _iso(now - timedelta(minutes=1))}))
        await refused("its stop time has passed — Resume with changes…")
        launch.unlink()
        await refused("w has no launch record, so nothing says how it was started: Resume with changes…")
        launch.write_text(kept, encoding="utf-8")
        with pytest.raises(AgentError, match="no session nobody"):
            await person.call("restart", id="nobody")
        # a create that fails after the close is said to the person, and the marks stand for the next press
        rec.restart_wanted = {"at": _iso(now), "why": "x", "early": True}

        async def boom(**_kw):
            raise RuntimeError("no tmux")

        monkeypatch.setattr(agent, "rpc_create", boom)
        with pytest.raises(AgentError, match="the restart of w failed: no tmux — it is closed and its marks stand"):
            await person.call("restart", id=sid)
        assert agent.sessions[sid] is rec and rec.state == "closed" and rec.restart_wanted and rec.restarts == []


@pytest.mark.integration
async def test_a_launch_record_that_says_attended_comes_back_attended(agent, composerstubs, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path, unattended=False)
        await agent.rpc_hook(sid, state="idle")
        await person.call("restart", id=sid)
        new = agent.sessions[sid]
        assert not new.unattended and new.supervised and [r["why"] for r in new.restarts] == ["person"]
        await person.call("kill", id=sid)
