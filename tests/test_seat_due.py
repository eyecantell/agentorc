"""A seat's trigger is the host agent's to time and count (design §4.9b *Seats with a trigger*, TD-104):
the record carries `trigger`, the reports' cadence writes `seat_due`, and the manager reads a field."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import wait_state

from sessionorc import reports
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import seat_trigger

SINCE = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def test_a_trigger_is_prs_or_every_and_nothing_else():
    assert seat_trigger(None) is None and seat_trigger({}) is None
    assert seat_trigger({"prs": 10}) == {"prs": 10}
    assert seat_trigger({"every": "6h"}) == {"every": "6h"}
    for bad in ({"prs": 0}, {"prs": "10"}, {"prs": True}, {"every": "6"}, {"every": "0h"}, {"asks": 1}, [1], "prs"):
        with pytest.raises(ValueError, match="a trigger is"):
            seat_trigger(bad)
    with pytest.raises(ValueError):
        seat_trigger({"prs": 1, "every": "1h"})


def test_every_comes_due_on_the_clock_from_when_the_seat_last_came():
    due = reports.seat_due({"every": "6h"}, SINCE, SINCE + timedelta(hours=5), None)
    assert due == {
        "trigger": {"every": "6h"},
        "since": "2026-09-22T12:00:00Z",
        "due_at": "2026-09-22T18:00:00Z",
        "met_at": None,
    }
    met = reports.seat_due({"every": "6h"}, SINCE, SINCE + timedelta(hours=6), None)
    assert met["met_at"] == "2026-09-22T18:00:00Z"


def test_prs_counts_merges_since_the_seat_came_and_meets_on_the_nth():
    prs = [
        {"number": 1, "mergedAt": "2026-09-22T11:00:00Z"},  # before it came: not counted
        {"number": 2, "mergedAt": "2026-09-22T13:00:00Z"},
        {"number": 3, "mergedAt": None, "state": "OPEN"},  # not merged
        {"number": 4, "mergedAt": "2026-09-22T12:30:00Z"},
        {"number": 5, "mergedAt": "2026-09-22T14:00:00Z"},
    ]
    now = SINCE + timedelta(hours=3)
    two = reports.seat_due({"prs": 2}, SINCE, now, prs)
    assert two["count"] == 3 and two["met_at"] == "2026-09-22T13:00:00Z"  # the second merge, in time order
    ten = reports.seat_due({"prs": 10}, SINCE, now, prs)
    assert ten["count"] == 3 and ten["met_at"] is None and ten["since"] == "2026-09-22T12:00:00Z"
    # `gh` could not be asked: no reading, never "zero merges"
    assert reports.seat_due({"prs": 2}, SINCE, now, None) is None


async def test_a_seat_record_keeps_its_trigger_and_the_tick_writes_seat_due(agent, tmp_path, monkeypatch):
    """`create(trigger=…)` keeps it on the record; a fill under the same name that gives none keeps
    the seat's; a bad one is refused before anything starts; and the reports' pass writes
    `seat_due` from one `gh` read per repo — and leaves it alone when `gh` could not be asked."""
    seat = {"name": "audit", "dir": str(tmp_path), "adapter": "shell", "argv": ["bash", "--norc"]}
    reads: list[str] = []
    merged = [{"number": 9, "mergedAt": "2999-01-01T00:00:00Z"}]  # after any `created`

    def fake_prs(directory, *a, **kw):
        reads.append(str(directory))
        return merged

    monkeypatch.setattr(reports, "_prs_or_none", fake_prs)
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="a trigger is"):
            await person.call("create", trigger={"prs": 0}, **seat)
        sid = (await person.call("create", trigger={"prs": 2}, **seat))["id"]
        assert agent.sessions[sid].trigger == {"prs": 2}
        # the fixture's own tick runs the pass too; step past its cadence and count from here
        now = agent._seat_due_at + timedelta(minutes=6)
        reads.clear()
        await agent._refresh_seat_due(now)
        due = agent.sessions[sid].seat_due
        assert due["count"] == 1 and due["met_at"] is None and reads == [str(tmp_path)]
        await agent._refresh_seat_due(now + timedelta(minutes=1))  # inside the cadence: not read again
        assert reads == [str(tmp_path)]
        merged.append({"number": 10, "mergedAt": "2999-01-02T00:00:00Z"})
        await agent._refresh_seat_due(now + timedelta(minutes=6))
        assert agent.sessions[sid].seat_due["met_at"] == "2999-01-02T00:00:00Z"
        assert (await person.call("get", id=sid))["seat_due"]["count"] == 2  # on the view the manager reads
        monkeypatch.setattr(reports, "_prs_or_none", lambda *a, **kw: None)
        await agent._refresh_seat_due(now + timedelta(minutes=12))
        assert agent.sessions[sid].seat_due["count"] == 2  # an outage is no reading, not zero
        # the manager's fill: the same name, no trigger given — the seat's is kept, and `since` restarts
        await person.call("kill", id=sid)
        await wait_state(person, sid, "exited")
        filled = (await person.call("create", **seat))["id"]
        assert agent.sessions[filled].trigger == {"prs": 2}
        await person.call("kill", id=filled)
        plain = (await person.call("create", **{**seat, "name": "other"}))["id"]
        assert agent.sessions[plain].trigger is None and agent.sessions[plain].seat_due is None
        await person.call("kill", id=plain)
