"""TD-152 slices 1 and 2, design §6 *Start time*: a create with `start_at` makes the record now — the
name taken, the launch record written, the directory's slot held — in the state `scheduled`, with no
pane; `set_start` moves it or hands it to the next tick with `now`; close and remove cancel it; and the
home's tick creates the session from its launch record at the instant, `restarts: [{why: start}]`, the
record superseded in place with its mail, a failed start counted up to the ceiling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks

from sessionorc import paths
from sessionorc.agent import RESTART_CEILING
from sessionorc.client import AgentError, LocalClient

pytestmark = pytest.mark.integration


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _later(minutes: int = 30) -> str:
    return _iso(datetime.now(UTC) + timedelta(minutes=minutes))


async def _scheduled(person, tmp_path, name="w", adapter="shell", **kw):
    return await person.call(
        "create",
        name=name,
        dir=str(tmp_path),
        adapter=adapter,
        argv=["bash", "--norc", "--noprofile"],
        unattended=True,
        prompt="the brief",
        start_at=kw.pop("start_at", _later()),
        **kw,
    )


async def test_a_scheduled_create_is_a_record_with_no_pane_that_holds_its_name_and_slot(agent, tmp_path, hookstub):
    await park_ticks(agent)
    async with LocalClient() as person:
        v = await _scheduled(person, tmp_path, adapter="hookstub", lane=["TD-1"], team="t")
        sid = v["id"]
        rec = agent.sessions[sid]
        assert (v["state"], rec.supervised, rec.run_log, rec.lane) == ("scheduled", True, None, ["TD-001"])
        assert rec.start_at == v["start_at"] and rec.start_at.endswith("Z")
        assert (paths.launch_dir() / f"{sid}.json").exists()
        assert sid not in {p.session for p in agent.tmux.list_panes()}  # no pane yet
        # the slot is held: a second agent session there is refused as if it were live (§9 invariant 2)
        with pytest.raises(AgentError, match="anchor rule"):
            await person.call("create", name="x", dir=str(tmp_path), adapter="hookstub")
        # and the name: a create under it is refused as a live holder's is (§4.1)
        verdict = await person.call("name_check", dir=str(tmp_path), name="w")
        assert verdict["verdict"] == "live" and verdict["holder_state"] == "scheduled"
        # a tick leaves it alone: no pane is its nature, not an exit
        await agent.tick()
        assert agent.sessions[sid].state == "scheduled"


async def test_what_a_scheduled_create_refuses(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="applies to unattended sessions"):
            await person.call("create", name="w", dir=str(tmp_path), adapter="shell", start_at=_later())
        with pytest.raises(AgentError, match="is not ahead"):
            await _scheduled(person, tmp_path, start_at=_iso(datetime.now(UTC) - timedelta(minutes=1)))
        with pytest.raises(AgentError, match="is not after start_at"):
            await _scheduled(person, tmp_path, start_at=_later(60), run_until=_later(30))
        with pytest.raises(AgentError, match="needs a timezone"):
            await _scheduled(person, tmp_path, start_at="2030-01-01T20:00:00")
        assert not agent.sessions


async def test_set_start_moves_it_and_now_starts_it_on_the_next_tick_with_its_mail(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = (await _scheduled(person, tmp_path))["id"]
        later = _later(90)
        assert (await person.call("set_start", id=sid, start_at=later))["start_at"] == later
        await agent._keep_running(datetime.now(UTC))
        assert agent.sessions[sid].state == "scheduled"  # not yet
        await person.call("msg", to=sid, text="read this when you start")
        await person.call("set_start", id=sid, start_at="now")
        await agent._keep_running(datetime.now(UTC) + timedelta(seconds=1))
        new = agent.sessions[sid]
        assert new.state not in ("scheduled", "exited") and new.start_at is None and new.run_log
        assert [r["why"] for r in new.restarts] == ["start"] and "error" not in new.restarts[0]
        assert [e.text for e in new.inbox] == ["read this when you start"]  # its mail came with it
        assert sid in {p.session for p in agent.tmux.list_panes()}
        with pytest.raises(AgentError, match="not scheduled"):
            await person.call("set_start", id=sid, start_at=_later())
        await person.call("kill", id=sid)


async def test_close_and_remove_cancel_it_and_free_the_slot(agent, tmp_path, hookstub):
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = (await _scheduled(person, tmp_path, adapter="hookstub"))["id"]
        got = await person.call("close", id=sid)
        assert got["cancelled"] is True and sid not in agent.sessions
        assert not (paths.launch_dir() / f"{sid}.json").exists()
        sid = (await _scheduled(person, tmp_path, adapter="hookstub"))["id"]  # the slot was free again
        await person.call("remove", id=sid)
        assert sid not in agent.sessions


async def test_a_start_that_fails_counts_up_to_the_ceiling(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = (await _scheduled(person, tmp_path))["id"]
        (paths.launch_dir() / f"{sid}.json").unlink()  # nothing to replay: every start fails
        await person.call("set_start", id=sid, start_at="now")
        now = datetime.now(UTC) + timedelta(seconds=1)
        for _ in range(RESTART_CEILING):
            await agent._keep_running(now)
        rec = agent.sessions[sid]
        assert rec.state == "scheduled" and len(rec.restarts) == RESTART_CEILING
        assert all(r["why"] == "start" and "no launch record" in r["error"] for r in rec.restarts)
        await agent._keep_running(now)
        assert agent.sessions[sid].restart_ceiling and len(agent.sessions[sid].restarts) == RESTART_CEILING
        # a person's new time is a new start: the ceiling and its count are spent
        await person.call("set_start", id=sid, start_at="now")
        assert agent.sessions[sid].restart_ceiling is None and agent.sessions[sid].restarts == []


async def test_start_of_names_only_the_record_it_starts(agent, tmp_path):
    """`start_of` lets a create through the scheduled record's own name and slot, and no other: it is
    never a way to supersede a scheduled record from another name or directory."""
    await park_ticks(agent)
    other = tmp_path / "other"
    other.mkdir()
    async with LocalClient() as person:
        sid = (await _scheduled(person, tmp_path))["id"]
        with pytest.raises(AgentError, match="start_of must name it"):
            await person.call("create", name="x", dir=str(other), adapter="shell", unattended=True, start_of=sid)
        with pytest.raises(AgentError, match="not a scheduled record"):
            await person.call("create", name="x", dir=str(other), adapter="shell", start_of="ao-nope")
        assert agent.sessions[sid].state == "scheduled"


async def _manager(person, tmp_path):
    """A session holding `control`, as a team's manager does."""
    d = tmp_path / "mgr"
    d.mkdir(exist_ok=True)
    got = await person.call(
        "create",
        name="mgr",
        dir=str(d),
        adapter="shell",
        argv=["bash", "--norc", "--noprofile"],
        capabilities=["control"],
    )
    return got["id"]


async def test_start_of_is_an_act_on_the_record_open_to_its_controllers_alone(agent, tmp_path):
    """The techlead's read of #669: starting a scheduled record supersedes it and takes its mailbox, so a
    session that is not one of its controllers is refused, as keep_mail's rule refuses (§9 invariant 11)."""
    await park_ticks(agent)
    async with LocalClient() as person:
        mgr = await _manager(person, tmp_path)
        (tmp_path / "a").mkdir()
        theirs = await _scheduled(person, tmp_path / "a")
        launch = {"name": "w", "dir": theirs["dir"], "adapter": "shell", "argv": ["bash", "--norc"], "unattended": True}
        async with LocalClient(caller=mgr) as session:
            with pytest.raises(AgentError, match="is not a controller of"):
                await session.call("create", start_of=theirs["id"], **launch)
        assert agent.sessions[theirs["id"]].state == "scheduled"
        (tmp_path / "b").mkdir()
        async with LocalClient(caller=mgr) as session:
            mine = await _scheduled(session, tmp_path / "b")  # the creator is its controller
            started = await session.call("create", start_of=mine["id"], **{**launch, "dir": mine["dir"]})
        assert started["id"] == mine["id"] and agent.sessions[mine["id"]].state != "scheduled"
        await person.call("kill", id=mine["id"])


async def test_a_sessions_set_start_moves_the_time_but_never_spends_the_ceiling(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        mgr = await _manager(person, tmp_path)
        (tmp_path / "c").mkdir()
        async with LocalClient(caller=mgr) as session:
            sid = (await _scheduled(session, tmp_path / "c"))["id"]
            (paths.launch_dir() / f"{sid}.json").unlink()  # every start fails
            await session.call("set_start", id=sid, start_at="now")
            now = datetime.now(UTC) + timedelta(seconds=1)
            await agent._keep_running(now)
            assert len(agent.sessions[sid].restarts) == 1
            later = _later(60)
            await session.call("set_start", id=sid, start_at=later)  # moves it, and the count stands
            assert agent.sessions[sid].start_at == later and len(agent.sessions[sid].restarts) == 1
            await session.call("set_start", id=sid, start_at="now")
            for _ in range(RESTART_CEILING):
                await agent._keep_running(now)
            assert agent.sessions[sid].restart_ceiling
            with pytest.raises(AgentError, match="reached the restart ceiling"):
                await session.call("set_start", id=sid, start_at="now")
        await person.call("set_start", id=sid, start_at="now")  # a person's spends it
        assert agent.sessions[sid].restart_ceiling is None and agent.sessions[sid].restarts == []
