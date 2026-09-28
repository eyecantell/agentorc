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
