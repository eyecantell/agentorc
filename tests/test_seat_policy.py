"""TD-103 slice (3), design §6 *Keeping a team running* rule 3: the seats. The tick computes
`seat_due` from a seat's trigger — a question waiting, n PRs merged since it came, or a time since
it came — fills an ended seat that is due with its mail kept, caps the fills at six an hour over the
seats sharing a controller, and closes a seat that has run and sits idle with nothing due."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks

from sessionorc import reports
from sessionorc.agent import FILL_CEILING, SEAT_IDLE_GRACE
from sessionorc.client import LocalClient

pytestmark = pytest.mark.integration


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


@pytest.fixture(autouse=True)
def _no_gh(monkeypatch):
    """No test here asks GitHub: a `prs:` count is what the test says it is."""
    monkeypatch.setattr(reports, "merged_prs", lambda *a, **kw: None)


async def _seat(person, tmp_path, name="audit", trigger="every", after="6h", **kw):
    params = {
        "name": name,
        "dir": str(tmp_path),
        "adapter": "shell",
        "argv": ["bash", "--norc", "--noprofile"],
        "unattended": True,
        "supervised": True,
        "prompt": "the seat's brief",
        "seat": {"trigger": trigger, **({"after": after} if after else {})},
    }
    return (await person.call("create", **{**params, **kw}))["id"]


def _end(agent, sid: str) -> None:
    rec = agent.sessions[sid]
    rec.state, rec.pane, rec.exit_code = "exited", True, 0
    agent.store.save(rec)


def _idle(agent, sid: str, now: datetime, **git) -> None:
    rec = agent.sessions[sid]
    rec.state, rec.confidence, rec.since = "idle", "hook", _iso(now - SEAT_IDLE_GRACE - timedelta(seconds=1))
    rec.git = {"branch": "main", "dirty": 0, "unpushed": 0, **git}


async def test_an_every_seat_comes_due_on_the_clock_and_is_filled_with_its_mail(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = await _seat(person, tmp_path)
        first = agent.sessions[sid]
        _end(agent, sid)
        now = datetime.now(UTC)
        await agent._keep_running(now + timedelta(hours=5))
        assert agent.sessions[sid] is first and first.seat_due is None, "not yet: six hours since it came"
        await agent._keep_running(now + timedelta(hours=6, minutes=1))
        new = agent.sessions[sid]
        assert new is not first, "filled under its name (§4.1)"
        assert first.seat_due == {"at": first.seat_due["at"], "by": "every"}
        assert new.seat == {"trigger": "every", "after": "6h"} and new.seat_due is None
        assert [r["why"] for r in new.restarts] == ["fill"]
        assert new.supersedes and new.supersedes[0]["mail"] is True, "a fill keeps the seat's mail (§4.9b)"
        await person.call("kill", id=sid)


async def test_an_asks_seat_is_due_while_a_question_waits(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = await _seat(person, tmp_path, name="tl", trigger="asks", after="")
        rec = agent.sessions[sid]
        _end(agent, sid)
        now = datetime.now(UTC)
        await agent._keep_running(now)
        assert agent.sessions[sid] is rec and rec.seat_due is None, "nothing waiting, nothing due"
        # a question lands and is answered by someone else before the tick: due, then not
        rec.asks_waiting = lambda **kw: 1
        agent._profile_gated = lambda *a: True  # paused: due, but not filled into a pause
        await agent._keep_running(now)
        assert rec.seat_due and rec.seat_due["by"] == "asks" and agent.sessions[sid] is rec
        rec.asks_waiting = lambda **kw: 0
        await agent._keep_running(now)
        assert rec.seat_due is None
        del agent._profile_gated
        rec.asks_waiting = lambda **kw: 2
        await agent._keep_running(now)
        assert agent.sessions[sid] is not rec
        await person.call("kill", id=sid)


async def test_a_prs_seat_counts_merges_since_it_came_and_an_outage_is_not_zero(agent, tmp_path, monkeypatch):
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = await _seat(person, tmp_path, trigger="prs", after="3")
        rec = agent.sessions[sid]
        came = datetime.fromisoformat(rec.created.replace("Z", "+00:00"))
        merged = [came - timedelta(hours=1), came + timedelta(minutes=1), came + timedelta(minutes=2)]
        asked: list[str] = []

        def fake(directory, *a, **kw):
            asked.append(str(directory))
            return list(merged)

        monkeypatch.setattr(reports, "merged_prs", fake)
        await agent._count_seats([rec])
        assert rec.seat_count["prs"] == 2 and asked == [str(tmp_path)], "one read, merges before it came left out"
        monkeypatch.setattr(reports, "merged_prs", lambda *a, **kw: None)
        await agent._count_seats([rec])
        assert rec.seat_count["prs"] == 2, "gh could not be asked: the last reading stands"
        _end(agent, sid)
        now = datetime.now(UTC)
        await agent._keep_running(now)
        assert rec.seat_due is None and agent.sessions[sid] is rec
        merged.append(came + timedelta(minutes=3))
        monkeypatch.setattr(reports, "merged_prs", fake)
        await agent._count_seats([rec])
        await agent._keep_running(now)
        assert rec.seat_due and rec.seat_due["by"] == "prs"
        assert agent.sessions[sid] is not rec and agent.sessions[sid].seat_count is None, "the count starts again"
        await person.call("kill", id=sid)


async def test_six_fills_an_hour_over_seats_sharing_a_controller(agent, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        a = await _seat(person, tmp_path, name="a", controllers=["ao-x-mgr"])
        b = await _seat(person, tmp_path, name="b", controllers=["ao-x-mgr"])
        alone = await _seat(person, tmp_path, name="c", controllers=["ao-y-mgr"])
        for sid in (a, b, alone):
            _end(agent, sid)
            agent.sessions[sid].seat_due = {"at": _iso(now), "by": "every"}
        agent.sessions[a].restarts = [{"at": _iso(now - ago), "why": "fill"} for ago in _mins(FILL_CEILING)]
        ra, rb, rc = agent.sessions[a], agent.sessions[b], agent.sessions[alone]
        await agent._keep_running(now)
        tripped = [r for r in (ra, rb) if r.restart_ceiling]
        assert len(tripped) == 1 and tripped[0].restart_ceiling["why"] == "fill", "one seat carries the mark"
        assert agent.sessions[a] is ra and agent.sessions[b] is rb, "its fellow is refused, unmarked"
        assert agent.sessions[alone] is not rc, "a seat under another controller is not held by the ceiling"
        for sid in (a, b, alone):
            await person.call("kill", id=sid)


def _mins(n: int) -> list[timedelta]:
    return [timedelta(minutes=5 * (i + 1)) for i in range(n)]


async def test_an_idle_seat_with_nothing_due_and_nothing_left_is_closed(agent, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        done = await _seat(person, tmp_path, name="done")
        dirty = await _seat(person, tmp_path, name="dirty")
        due = await _seat(person, tmp_path, name="due")
        fresh = await _seat(person, tmp_path, name="fresh")
        _idle(agent, done, now)
        _idle(agent, dirty, now, dirty=2)
        _idle(agent, due, now)
        agent.sessions[due].seat_due = {"at": _iso(now), "by": "every"}
        _idle(agent, fresh, now)
        agent.sessions[fresh].since = _iso(now)
        await agent._keep_running(now)
        assert agent.sessions[done].state == "closed"
        for sid in (dirty, due, fresh):
            assert agent.sessions[sid].state == "idle", sid
            await person.call("kill", id=sid)


async def test_rule_three_leaves_an_unsupervised_or_attended_seat_alone(agent, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        plain = await _seat(person, tmp_path, name="plain", supervised=False)
        attended = await _seat(person, tmp_path, name="att")
        agent.sessions[attended].unattended = False
        for sid in (plain, attended):
            _end(agent, sid)
        recs = {sid: agent.sessions[sid] for sid in (plain, attended)}
        await agent._keep_running(now + timedelta(days=1))
        for sid, rec in recs.items():
            assert agent.sessions[sid] is rec and rec.seat_due is None, sid
            await person.call("kill", id=sid)
