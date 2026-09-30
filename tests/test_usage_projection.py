"""The gate's projection (design §6 *A reading the gate can no longer trust*, §5 `usage.max_age`;
TD-233 slice 4): a reading older than `max_age` is read as the held number plus the rate it last
rose at times its age, the gate pauses on that as on a reading, and a window with no rate to
project by pauses nothing and is said once a day."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks
from test_usage_gate import _iso, _worker

from sessionorc import settings, usage
from sessionorc.agent import RESUME_MIN
from sessionorc.client import AgentError, LocalClient

NOW = datetime(2026, 9, 29, 22, 0, tzinfo=UTC)
H = timedelta(hours=1)


def window(pct: float, read: datetime, per_hour: float | None, span: timedelta = H, resets=NOW + 3 * 24 * H) -> dict:
    """A window read at `read`, its history rising `per_hour` over `span` before it (none with None)."""
    hist = [{"at": _iso(read), "pct": pct}]
    if per_hour is not None:
        hist.insert(0, {"at": _iso(read - span), "pct": pct - per_hour * span / H})
    return {"label": "wk", "pct": pct, "resets": _iso(resets), "at": _iso(read), "history": hist}


@pytest.mark.unit
def test_six_hours_old_at_one_point_an_hour_does_not_cross_and_at_two_does():
    old = NOW - 6 * H
    one = usage.project([window(88, old, 1.0)], NOW, 3600)[0]
    assert one["pct"] == 94.0 and one["projected"] == {"from": 88, "rate": 1.0, "age": 6 * 3600}
    rows = settings.lines({"wk": 5}, [one], NOW)
    assert rows[0]["projected"] and settings.crossed(rows) is None
    two = usage.project([window(88, old, 2.0)], NOW, 3600)
    assert two[0]["pct"] == 100.0  # 88 + 12, at most 100
    assert settings.crossed(settings.lines({"wk": 5}, two, NOW))["projected"]["rate"] == 2.0
    # a fall reads as no rise: never below zero
    assert usage.project([window(88, old, -3.0)], NOW, 3600)[0]["pct"] == 88.0


@pytest.mark.unit
def test_no_history_or_a_short_one_is_unknown_and_a_fresh_or_ended_window_is_the_reading():
    old = NOW - 2 * H
    for w in (window(90, old, None), window(90, old, 5.0, span=timedelta(minutes=10))):
        got = usage.project([w], NOW, 3600)[0]
        assert got["unknown"] == "rate" and got["age"] == 7200 and "projected" not in got
        assert settings.crossed(settings.lines({"wk": 5}, [got], NOW)) is None  # unknown pauses nothing
    fresh = window(90, NOW - timedelta(minutes=30), 5.0)
    assert usage.project([fresh], NOW, 3600) == [fresh]
    assert usage.project([window(90, old, 5.0)], NOW, None) == [window(90, old, 5.0)]  # `off`
    ended = window(90, old, 5.0, resets=NOW - H)
    assert usage.project([ended], NOW, 3600) == [ended]  # past its reset: `lines` marks it
    # only points inside two hours of the newest count: an old climb says nothing of the last two
    w = window(90, old, None)
    w["history"] = [{"at": _iso(old - 5 * H), "pct": 10}, {"at": _iso(old - H), "pct": 90}, w["history"][0]]
    assert usage.rate(w) == 0.0
    # no age at all (a window never confirmed, on a reading with no `fetched`): the reading itself
    bare = {"label": "wk", "pct": 90, "resets": None}
    assert usage.project([bare], NOW, 3600) == [bare]
    assert usage.project([bare], NOW, 3600, fetched=_iso(old))[0]["unknown"] == "rate"


@pytest.mark.unit
def test_max_age_takes_an_age_or_off_and_the_reader_defaults_to_an_hour():
    assert settings.max_age({}) == 3600.0
    assert settings.max_age({"usage": {"max_age": "90m"}}) == 5400.0
    assert settings.max_age({"usage": {"max_age": "off"}}) is None
    assert settings.max_age({"usage": {"max_age": "soon"}}) == 3600.0  # a typo never switches it off
    assert settings.parse_max_age("2h") == "2h" and settings.parse_max_age(600) == "600s"
    for bad in ("1m", "30d", "1.5h", True, None):
        with pytest.raises(ValueError):
            settings.parse_max_age(bad)


def _reading(w: dict) -> dict:
    return {"windows": [w], "reason": "ok", "fetched": w["at"], "tool": "Claude", "account": "paul"}


def _notes(agent, text: str) -> list[str]:
    return [e.text for e in agent.person_inbox if e.from_ == "system" and text in e.text]


@pytest.mark.integration
async def test_a_projection_pauses_says_so_once_and_a_fresh_reading_under_the_line_resumes(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = await _worker(person, tmp_path)
        now = datetime.now(UTC)
        agent._usage[""] = _reading(window(88, now - 6 * H, 2.0, resets=now + 3 * 24 * H))
        await person.call("set_settings", profile="", reserves={"wk": 5})
        rec = agent.sessions[sid]
        await agent._enforce_usage_gate(now)
        g = rec.gated
        assert g and g["pct"] == 100.0 and g["line"] == 95 and g["projected"]["from"] == 88
        assert agent._profile_gated("", now)  # the same reader: no restart into the pause
        gate = (await person.call("gate"))["profiles"][""]
        assert gate["windows"][0]["projected"] and (await person.call("gate"))["max_age"] == "1h"
        await agent._enforce_usage_gate(now + timedelta(minutes=1))
        assert len(_notes(agent, "pausing on a projection: no reading of Claude · paul for 6h")) == 1
        # a fresh reading under the line resumes, past RESUME_MIN
        later = now + RESUME_MIN + timedelta(seconds=5)
        agent._usage[""] = _reading(window(90, later, 2.0, resets=now + 3 * 24 * H))
        await agent._enforce_usage_gate(later)
        assert rec.gated is None
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)


@pytest.mark.integration
async def test_an_old_reading_with_no_rate_holds_a_pause_and_pauses_nothing_new(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = await _worker(person, tmp_path)
        now = datetime.now(UTC)
        agent._usage[""] = _reading(window(97, now, None, resets=now + 3 * 24 * H))
        await person.call("set_settings", profile="", reserves={"wk": 5})
        rec = agent.sessions[sid]
        await agent._enforce_usage_gate(now)
        assert rec.gated and "projected" not in rec.gated  # a pause on a reading
        # the paused session reports nothing; two hours on the reading has no rate: the pause stands
        later = now + 2 * H
        await agent._enforce_usage_gate(later)
        assert rec.gated is not None
        # `max_age: off`: the reading is read as it is, 97 ≥ 95, still paused
        await person.call("set_settings", usage={"max_age": "off"})
        assert settings.max_age(settings.load()) is None
        await agent._enforce_usage_gate(later)
        assert rec.gated is not None
        # an old reading with no rate is no reason to lift it either
        await person.call("set_settings", usage={"max_age": None})
        agent._usage[""] = _reading(window(80, now, None, resets=now + 3 * 24 * H))
        await agent._enforce_usage_gate(later + RESUME_MIN)
        assert rec.gated is not None
        # and it pauses nothing that was not paused
        rec.gated = None
        await agent._enforce_usage_gate(later + RESUME_MIN)
        assert rec.gated is None
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)


@pytest.mark.integration
async def test_usage_unknown_is_said_once_a_day_while_a_session_works(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = await _worker(person, tmp_path)
        rec = agent.sessions[sid]
        rec.state = "working"
        now = datetime.now(UTC)
        agent._usage[""] = _reading(window(80, now - 2 * H, None, resets=now + 3 * 24 * H))
        await person.call("set_settings", profile="", reserves={"wk": 5})
        await agent._enforce_usage_gate(now)
        await agent._enforce_usage_gate(now + timedelta(minutes=5))
        got = _notes(agent, "usage unknown for 2h: Claude · paul wk")
        assert len(got) == 1 and "1 unattended session working" in got[0]
        assert rec.gated is None
        # under `off` nothing is said
        agent._unknown_noted.clear()
        await person.call("set_settings", usage={"max_age": "off"})
        await agent._enforce_usage_gate(now + timedelta(minutes=10))
        assert len(_notes(agent, "usage unknown")) == 1
        with pytest.raises(AgentError, match="usage.max_age"):
            await person.call("set_settings", usage={"max_age": "soon"})
        with pytest.raises(AgentError, match="unknown key"):
            await person.call("set_settings", usage={"maxage": "1h"})
        assert (await person.call("settings"))["usage"] == {"max_age": "off"}
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)
