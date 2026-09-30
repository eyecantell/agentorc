"""TD-233 slice 2, the host agent's half (design §4.4 *Usage, reported first and asked for last*):
a session's report of its account's limits, merged by what a window can do, with an age of its
own and a short history; the endpoint then asked only for what reports cannot give."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from conftest import wait_for

from sessionorc import usage
from sessionorc.client import LocalClient

R1 = "2026-10-01T05:00:00Z"
R2 = "2026-10-01T10:00:00Z"


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def w(label: str, pct: float, resets: str | None = R1) -> dict:
    return {"label": label, "pct": pct, "resets": resets}


def pcts(reading: dict) -> dict:
    return {x["label"]: x["pct"] for x in reading["windows"]}


def test_two_sessions_inside_one_reset_read_the_higher():
    """Inside one `resets` a window's use never falls, so the highest stands, whoever spoke last."""
    a = usage.merge(
        None, usage.clean_windows([w("5h", 61.5)]), at="2026-10-01T01:00:00Z", source="reported", fresh=True
    )
    b = usage.merge(a, usage.clean_windows([w("5h", 60.0)]), at="2026-10-01T01:01:00Z", source="reported", fresh=True)
    assert pcts(b) == {"5h": 61.5}
    assert b["fetched"] == "2026-10-01T01:01:00Z" and b["source"] == "reported"  # confirmed again, so it is younger


def test_a_later_reset_replaces_and_an_earlier_one_is_over():
    a = usage.merge(None, [w("5h", 90.0)], at="2026-10-01T01:00:00Z", source="reported", fresh=True)
    b = usage.merge(a, [w("5h", 3.0, R2)], at="2026-10-01T05:01:00Z", source="reported", fresh=True)
    assert pcts(b) == {"5h": 3.0} and b["windows"][0]["resets"] == R2
    assert [h["pct"] for h in b["windows"][0]["history"]] == [3.0]  # a new window starts its own history
    c = usage.merge(b, [w("5h", 95.0, R1)], at="2026-10-01T05:02:00Z", source="reported", fresh=True)
    assert pcts(c) == {"5h": 3.0}  # a late report of the window before the reset changes nothing


def test_a_report_that_is_not_fresh_moves_no_age():
    a = usage.merge(None, [w("week", 40.0)], at="2026-10-01T01:00:00Z", source="asked", fresh=True)
    b = usage.merge(a, [w("week", 41.0)], at="2026-10-01T02:00:00Z", source="reported", fresh=False, by="g1")
    assert pcts(b) == {"week": 41.0}  # the number may rise
    assert b["fetched"] == "2026-10-01T01:00:00Z" and b["source"] == "asked" and "by" not in b
    assert b["windows"][0]["at"] == "2026-10-01T01:00:00Z"


def test_a_window_only_the_endpoint_gives_is_kept_beside_a_report():
    a = usage.merge(None, [w("week", 40), w("week · Fable", 17)], at="2026-10-01T01:00:00Z", source="asked", fresh=True)
    b = usage.merge(a, [w("week", 41.0)], at="2026-10-01T01:30:00Z", source="reported", fresh=True, by="g1")
    only = next(x for x in b["windows"] if x["label"] == "week · Fable")
    assert only["pct"] == 17 and only["source"] == "asked" and only["at"] == "2026-10-01T01:00:00Z"
    assert b["by"] == "g1" and b["fetched"] == "2026-10-01T01:30:00Z"


def test_the_history_keeps_one_point_per_ten_minutes_for_three_hours():
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    r = None
    for m in range(0, 241):  # a report a minute for four hours, one point a minute
        r = usage.merge(
            r, [w("5h", float(m) / 10, R2)], at=iso(t0 + timedelta(minutes=m)), source="reported", fresh=True
        )
    hist = r["windows"][0]["history"]
    ats = [datetime.fromisoformat(h["at"].replace("Z", "+00:00")) for h in hist]
    assert ats[-1] == t0 + timedelta(minutes=240)  # the newest always
    assert ats[-1] - ats[0] <= timedelta(hours=3)
    assert all(b - a >= timedelta(minutes=10) for a, b in zip(ats[:-2], ats[1:-1], strict=True))
    assert 17 <= len(hist) <= 20


def test_clean_windows_keeps_only_what_a_window_is():
    got = usage.clean_windows(
        [{"label": "5h", "pct": 12.34, "resets": "nope", "x": 1}, {"label": ""}, "junk", {"pct": 1}]
    )
    assert got == [{"label": "5h", "pct": 12.3, "resets": None}]
    assert usage.clean_windows({"not": "a list"}) == []


def test_a_window_only_the_endpoint_gives_is_asked_for_hourly_while_watched():
    now = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
    two_h = iso(now - timedelta(hours=2))
    a = usage.merge(None, [w("week", 40), w("week · Fable", 50)], at=two_h, source="asked", fresh=True)
    b = usage.merge(a, [w("week", 41.0)], at=iso(now), source="reported", fresh=True)
    assert not usage.asked_only_due(b, now, 3600, set())  # nobody watches it, and it is far from its cap
    assert usage.asked_only_due(b, now, 3600, {"week · Fable"})  # a reserve names it
    near = usage.merge(None, [w("week", 40), w("week · Fable", 92)], at=two_h, source="asked", fresh=True)
    near = usage.merge(near, [w("week", 41.0)], at=iso(now), source="reported", fresh=True)
    assert usage.asked_only_due(near, now, 3600, set())  # within ten points of its cap
    assert not usage.asked_only_due(a, now, 3600, {"week · Fable"})  # the endpoint's own reading: the age rule decides


async def test_a_report_reaches_the_accounts_reading_and_holds_the_endpoint_off(agent, hookstub, tmp_path, monkeypatch):
    """A live session's report lands on its account's reading, under every profile sharing it,
    with its age and its reporter; a young reading asks the endpoint nothing; the endpoint's own
    answer then merges by the same rule; a caller that is no session here is not taken."""
    monkeypatch.setattr(hookstub, "accounts", {"pa": "paul", "pb": "paul"})
    monkeypatch.setattr(hookstub, "usage_asked", [])
    hookstub.usage_value = None  # the endpoint has nothing to say until the end
    async with LocalClient() as c:
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        sa = await c.call("create", name="ra", dir=str(tmp_path / "a"), adapter="hookstub", profile="pa")
        sb = await c.call("create", name="rb", dir=str(tmp_path / "b"), adapter="hookstub", profile="pb")
        async with LocalClient(caller=sa["id"]) as me:
            got = await me.call("usage_report", windows=[w("5h", 61.5), w("week", 30.2)], fresh=True)
        assert got == {"taken": True}
        assert await wait_for(lambda: pcts(agent._usage.get("pb") or {"windows": []}).get("5h") == 61.5, timeout=5.0)
        ra = agent._usage["pa"]
        assert ra["source"] == "reported" and ra["by"] == "ra" and ra["account"] == "paul" and ra["windows"][0]["at"]
        assert agent._usage_young("hookstub:paul")
        agent._usage_checked["hookstub:paul"] = time.monotonic() - 3600  # the poll's clock alone says due
        hookstub.usage_asked.clear()
        await agent._refresh_usage_inner()
        assert hookstub.usage_asked == []  # a young reading: the endpoint is not asked

        async with LocalClient(caller=sb["id"]) as me:
            await me.call("usage_report", windows=[w("5h", 60.0)], fresh=True)  # lower, inside one reset
        assert pcts(agent._usage["pa"])["5h"] == 61.5 and agent._usage["pa"]["by"] == "rb"

        # the endpoint's answer is the account's own: it sets the windows it names outright, keeps the rest
        hookstub.usage_value = {"windows": [w("5h", 59), w("week · Fable", 17)], "fetched": iso(datetime.now(UTC))}
        merged = agent._usage_reading("hookstub:paul", hookstub.usage_value)
        assert merged is not None and merged["source"] == "asked" and "by" not in merged
        assert pcts(merged) == {"5h": 59, "week": 30.2, "week · Fable": 17}

        async with LocalClient() as nobody:
            assert await nobody.call("usage_report", windows=[w("5h", 99.0)], fresh=True) == {"taken": False}
        async with LocalClient(caller=sa["id"]) as me:
            assert await me.call("usage_report", windows=[], fresh=True) == {"taken": False}
        hookstub.usage_value = None
        for s in (sa, sb):
            await c.call("kill", id=s["id"])
