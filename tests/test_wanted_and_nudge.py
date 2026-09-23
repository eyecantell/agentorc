"""TD-103 slice (4), design §6 *Keeping a team running* rules 2 and 4. The wanted restart: a member
that declared `restart_wanted` is closed and restarted when its work is pushed, sent one line naming
what is left when it is not, and the Inbox's after `IDLE_NUDGE`. The idle nudge: a member idle for
`IDLE_NUDGE` with open work is sent one fixed line naming it, once per idle stretch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks, wait_for

from sessionorc.agent import IDLE_NUDGE
from sessionorc.client import LocalClient
from sessionorc.models import ProgressEntry

pytestmark = pytest.mark.integration

CLEAN = {"branch": "w", "dirty": 0, "unpushed": 0, "upstream": "origin/w"}


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


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
    return sid


async def _painted(agent, sid: str) -> bool:
    return any(t.startswith(">>") for t in await agent.rpc_tail(sid, 3))


async def _shows(agent, sid: str, text: str) -> bool:
    return any(text in t.replace(" ", "") for t in await agent.rpc_tail(sid, 3))


async def _submitted(agent, sid: str) -> list[str]:
    return [t.rstrip() for t in await agent.rpc_tail(sid, 30) if t.startswith("SUBMITTED ")]


async def _idle_for(agent, sid: str, now: datetime, long: timedelta) -> None:
    """A hook-confirmed idle that began `long` before `now`."""
    await agent.rpc_hook(sid, state="idle")
    agent.sessions[sid].since = _iso(now - long)


async def test_an_idle_member_with_open_work_is_nudged_once_per_stretch(agent, composerstubs, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path, lane=["TD-1", "TD-2"])
        rec = agent.sessions[sid]
        await _idle_for(agent, sid, now, IDLE_NUDGE - timedelta(minutes=1))
        await agent._keep_running(now)
        assert await _submitted(agent, sid) == [] and rec.nudged_at is None, "not yet twenty minutes"
        await _idle_for(agent, sid, now, IDLE_NUDGE + timedelta(minutes=1))
        await agent._keep_running(now)
        lines = await _submitted(agent, sid)
        assert len(lines) == 1 and "`TD-001` open" in lines[0] and "ao progress restart --why" in lines[0]
        assert rec.nudged_at and rec.sends[-1].from_ == "system"
        await agent._keep_running(now + timedelta(minutes=30))
        assert len(await _submitted(agent, sid)) == 1, "never a second in the same stretch"
        # it answered by working and went idle again: a new stretch, and the next open reference
        await agent.rpc_hook(sid, state="working")
        rec.progress = [ProgressEntry(ref="TD-001", status="done", pr=5)]
        await _idle_for(agent, sid, now + timedelta(hours=1), IDLE_NUDGE + timedelta(minutes=1))
        await agent._keep_running(now + timedelta(hours=1))
        lines = await _submitted(agent, sid)
        assert len(lines) == 2 and "`TD-002` open" in lines[1]
        await person.call("kill", id=sid)


async def test_what_the_nudge_leaves_alone(agent, composerstubs, tmp_path):
    """Nothing open, a declaration, an unsupervised or attended member, and a pane with words in
    its composer are not nudged."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        cases = {
            "done": {"progress": [ProgressEntry(ref="TD-001", status="done", pr=1)]},
            "declared": {"out_of_work": {"at": _iso(now), "why": "nothing open"}},
            "unsupervised": {"supervised": False},
            "attended": {"unattended": False},
        }
        ids = {}
        for n, fields in cases.items():
            sid = await _member(agent, person, tmp_path, name=n, lane=["TD-1"])
            await _idle_for(agent, sid, now, IDLE_NUDGE + timedelta(minutes=1))
            for k, v in fields.items():
                setattr(agent.sessions[sid], k, v)
            ids[n] = sid
        typed = await _member(agent, person, tmp_path, name="typed", lane=["TD-1"])
        await agent.rpc_keys(typed, ["h", "i"])  # someone's half-typed words in the composer
        assert await wait_for(lambda: _shows(agent, typed, ">>hi"), timeout=5)
        await _idle_for(agent, typed, now, IDLE_NUDGE + timedelta(minutes=1))
        await agent._keep_running(now)
        for n, sid in ids.items():
            assert await _submitted(agent, sid) == [] and agent.sessions[sid].nudged_at is None, n
            await person.call("kill", id=sid)
        assert await _submitted(agent, typed) == [] and agent.sessions[typed].nudged_at is None, "words typed"
        await person.call("kill", id=typed)


async def test_a_wanted_restart_with_its_work_pushed_is_closed_and_restarted(agent, composerstubs, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path, lane=["TD-1"])
        first = agent.sessions[sid]
        await _idle_for(agent, sid, now, timedelta(minutes=1))
        first.restart_wanted = {"at": _iso(now), "why": "context is long"}
        first.git = None
        await agent._keep_running(now)
        assert agent.sessions[sid] is first, "an unknown git state is left alone"
        first.restart_wanted = {**first.restart_wanted, "early": True}
        first.git = dict(CLEAN)
        await agent._keep_running(now)
        assert agent.sessions[sid] is first, "an early one is the person's (§4.9a)"
        first.restart_wanted = {"at": _iso(now), "why": "context is long"}
        await agent._keep_running(now)
        new = agent.sessions[sid]
        assert new is not first and first.state == "closed"
        assert [r["why"] for r in new.restarts] == ["wanted"] and new.restart_wanted is None
        assert new.lane == ["TD-001"] and new.supervised
        await person.call("kill", id=sid)


async def test_a_wanted_restart_with_work_left_is_held_then_runs_once_pushed(agent, composerstubs, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path)
        rec = agent.sessions[sid]
        await _idle_for(agent, sid, now, timedelta(minutes=1))
        rec.restart_wanted = {"at": _iso(now), "why": "context is long"}
        rec.git = {**CLEAN, "dirty": 2, "unpushed": 1}
        await agent._keep_running(now)
        lines = await _submitted(agent, sid)
        assert len(lines) == 1 and "2 uncommitted and 1 unpushed" in lines[0]
        assert agent.sessions[sid] is rec and rec.restart_blocked_sent_at and rec.restart_blocked is None
        await agent._keep_running(now + timedelta(minutes=5))
        assert len(await _submitted(agent, sid)) == 1, "one send, once"
        await agent._keep_running(now + IDLE_NUDGE + timedelta(minutes=1))
        assert rec.restart_blocked == {"at": rec.restart_blocked["at"], "dirty": 2, "unpushed": 1}
        rec.git = dict(CLEAN)  # someone pushed
        await agent._keep_running(now + IDLE_NUDGE + timedelta(minutes=2))
        new = agent.sessions[sid]
        assert new is not rec and new.restart_blocked is None and [r["why"] for r in new.restarts] == ["wanted"]
        await person.call("kill", id=sid)
