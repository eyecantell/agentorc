"""TD-233 slice 2, the host agent's half (design §4.4 *Usage, reported first and asked for last*):
a session's report of its account's limits, merged by what a window can do, with an age of its
own and a short history; the endpoint then asked only for what reports cannot give."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

from conftest import wait_for

from sessionorc import usage
from sessionorc.client import LocalClient


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# two resets five hours apart, a day ahead of whenever the suite runs
R1 = iso(datetime.now(UTC) + timedelta(days=1))
R2 = iso(datetime.now(UTC) + timedelta(days=1, hours=5))


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


def test_a_number_that_is_no_reading_is_dropped_and_a_label_twice_reads_the_higher():
    """Review of #770: an `inf` from JSON's `1e999` would reach `_cap`'s `int()` and stop every pass."""
    assert usage.clean_windows([w("5h", float("inf")), w("a", float("nan")), w("b", 10**400), w("c", -1)]) == []
    assert usage.clean_windows([w("5h", 40.0), w("5h", 61.5), w("5h", 50.0)]) == [w("5h", 61.5)]


def test_an_answer_asked_before_a_fresher_report_does_not_undo_it():
    a = usage.merge(None, [w("5h", 70.0)], at="2026-10-01T01:05:00Z", source="reported", fresh=True, by="g1")
    b = usage.merge(a, [w("5h", 60)], at="2026-10-01T01:00:00Z", source="asked", fresh=True)
    assert pcts(b) == {"5h": 70.0} and b["windows"][0]["source"] == "reported"


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

        # a report at a cap marks the account's sessions `limited` at once, not on the next poll
        async with LocalClient(caller=sa["id"]) as me:
            await me.call("usage_report", windows=[w("5h", 100.0, R1)], fresh=True)
        assert agent.sessions[sa["id"]].state == "limited" and agent.sessions[sb["id"]].state == "limited"

        async with LocalClient() as nobody:
            assert await nobody.call("usage_report", windows=[w("5h", 99.0)], fresh=True) == {"taken": False}
        async with LocalClient(caller=sa["id"]) as me:
            assert await me.call("usage_report", windows=[], fresh=True) == {"taken": False}
        hookstub.usage_value = None
        for s in (sa, sb):
            await c.call("kill", id=s["id"])


# -- a node's sessions report to their node, which sends each on to the home (TD-233 slice 2) ---------


def node_record(rid="ao-x-w", host="laptop", **kw):
    from sessionorc.models import Session

    base = dict(id=rid, name="nw", kind="interactive", adapter="hookstub", dir="/tmp/x", host=host, state="working")
    return Session(**{**base, "profile": "pn", **kw}).to_dict()


async def test_a_node_sends_each_report_on_to_the_home_and_drops_it_with_no_link(
    agent, hookstub, tmp_path, monkeypatch
):
    """A node's session's report is merged on the node, for its own gate, and sent on to the home
    as `usage_report {id, account, windows, fresh}`, the account the node keys the profile by;
    with no link, or before its snapshot, it is dropped rather than queued."""
    monkeypatch.setattr(hookstub, "accounts", {"pn": "paul"})
    sent: list[dict] = []

    class Mux:
        async def notify(self, method, **params):
            if method == "usage_report":  # the records' own reports go the same road
                sent.append({"method": method, **params})

        async def request(self, method, timeout=None, **params):
            return None

    monkeypatch.setattr(agent, "mode", "node")
    monkeypatch.setattr(agent, "_home_mux", Mux())
    monkeypatch.setattr(agent, "_snapshot_sent", True)
    async with LocalClient() as c:
        (tmp_path / "n").mkdir()
        s = await c.call("create", name="nw", dir=str(tmp_path / "n"), adapter="hookstub", profile="pn")
        async with LocalClient(caller=s["id"]) as me:
            assert await me.call("usage_report", windows=[w("5h", 42.5)], fresh=True) == {"taken": True}
        assert await wait_for(lambda: bool(sent), timeout=5.0)
        assert sent == [
            {
                "method": "usage_report",
                "id": s["id"],
                "account": "hookstub:paul",
                "windows": [w("5h", 42.5)],
                "fresh": True,
            }
        ]
        assert pcts(agent._usage["pn"]) == {"5h": 42.5}  # the node's own reading, for its own gate

        sent.clear()
        monkeypatch.setattr(agent, "_snapshot_sent", False)  # a link not yet past its snapshot
        async with LocalClient(caller=s["id"]) as me:
            assert await me.call("usage_report", windows=[w("5h", 43.0)], fresh=True) == {"taken": True}
        monkeypatch.setattr(agent, "_home_mux", None)  # and a link that is down
        async with LocalClient(caller=s["id"]) as me:
            assert await me.call("usage_report", windows=[w("5h", 44.0)], fresh=True) == {"taken": True}
        for _ in range(5):
            await asyncio.sleep(0)
        assert sent == [] and pcts(agent._usage["pn"]) == {"5h": 44.0}
        await c.call("kill", id=s["id"])


async def test_the_home_shows_a_nodes_report_under_the_nodes_account_and_never_asks_for_it(
    agent, hookstub, tmp_path, monkeypatch
):
    """At the home, a node's report lands on the chip under the account the node named, for a record
    of that link's host only and an account in its adapter's namespace; the poll keeps it while the
    session lives and never asks the endpoint for it; a profile a session here runs under keeps its
    own account's reading; the chip goes when the node's session does."""
    monkeypatch.setattr(hookstub, "accounts", {"pn": "paul", "ph": "heather"})
    monkeypatch.setattr(hookstub, "usage_asked", [])
    hookstub.usage_value = None
    agent._take_records("laptop", [node_record(), node_record("ao-x-v", name="nv", profile="ph")], whole=True)
    report = {"id": "ao-x-w", "account": "hookstub:laptop-paul", "windows": [w("5h", 71.0)], "fresh": True}
    await agent._take_usage_report("laptop", report)
    got = agent._usage["pn"]
    assert got["account"] == "laptop-paul" and got["by"] == "nw" and pcts(got) == {"5h": 71.0}

    # another host's name for the record, an unknown record, an account of another adapter: dropped
    await agent._take_usage_report("desk", report | {"windows": [w("5h", 99.0)]})
    await agent._take_usage_report("laptop", report | {"id": "ao-x-nope", "windows": [w("5h", 99.0)]})
    await agent._take_usage_report("laptop", report | {"account": "claude-code:paul", "windows": [w("5h", 99.0)]})
    await agent._take_usage_report("laptop", report | {"account": "hookstub:", "windows": [w("5h", 99.0)]})
    assert pcts(agent._usage["pn"]) == {"5h": 71.0}

    async with LocalClient() as c:
        (tmp_path / "h").mkdir()
        mine = await c.call("create", name="hh", dir=str(tmp_path / "h"), adapter="hookstub", profile="ph")
        async with LocalClient(caller=mine["id"]) as me:
            await me.call("usage_report", windows=[w("5h", 12.0)], fresh=True)
        await agent._take_usage_report(
            "laptop", {"id": "ao-x-v", "account": "hookstub:laptop-h", "windows": [w("5h", 88.0)], "fresh": True}
        )
        agent._usage_checked["hookstub:laptop-paul"] = time.monotonic() - 3600
        hookstub.usage_asked.clear()
        await agent._refresh_usage_inner()
        assert hookstub.usage_asked == []  # the node's account is the node's to ask; this one is young
        assert agent._usage["pn"]["account"] == "laptop-paul" and pcts(agent._usage["pn"]) == {"5h": 71.0}
        # the profile a session here runs under keeps this host's account, whatever the node reported
        assert agent._usage["ph"]["account"] == "heather" and pcts(agent._usage["ph"]) == {"5h": 12.0}
        await c.call("kill", id=mine["id"])

    agent._take_records("laptop", [node_record(state="exited")], whole=False)
    await agent._refresh_usage_inner()
    assert "pn" not in agent._usage and "hookstub:laptop-paul" not in agent._usage_acct


async def test_two_nodes_keying_one_profile_to_two_accounts_show_one_and_never_flip(agent, hookstub, monkeypatch):
    """The review of the node's report: a profile two nodes key to two accounts goes to the first by
    host, and stays there pass after pass; a report for a record that is no live tool session sets no
    key; a node's key is forgotten once no live session of that node runs under the profile."""
    monkeypatch.setattr(hookstub, "usage_asked", [])
    hookstub.usage_value = None
    agent._take_records("desk", [node_record("ao-d-w", host="desk")], whole=True)
    agent._take_records("laptop", [node_record(), node_record("ao-x-z", name="nz", state="exited")], whole=True)
    await agent._take_usage_report("laptop", {"id": "ao-x-w", "account": "hookstub:bob", "windows": [w("5h", 20.0)]})
    await agent._take_usage_report("desk", {"id": "ao-d-w", "account": "hookstub:alice", "windows": [w("5h", 50.0)]})
    # an exited record's report names no key, whatever it says
    await agent._take_usage_report("laptop", {"id": "ao-x-z", "account": "hookstub:carol", "windows": [w("5h", 9.0)]})
    assert ("laptop", "pn") in agent._usage_remote_keys and agent._usage_remote_keys[("laptop", "pn")] == "hookstub:bob"
    for _ in range(3):
        await agent._refresh_usage_inner()
        assert agent._usage["pn"]["account"] == "alice" and pcts(agent._usage["pn"]) == {"5h": 50.0}
    assert hookstub.usage_asked == []

    agent._take_records("desk", [node_record("ao-d-w", host="desk", state="exited")], whole=False)
    await agent._refresh_usage_inner()
    assert ("desk", "pn") not in agent._usage_remote_keys
    await agent._take_usage_report("laptop", {"id": "ao-x-w", "account": "hookstub:bob", "windows": [w("5h", 21.0)]})
    assert agent._usage["pn"]["account"] == "bob" and pcts(agent._usage["pn"]) == {"5h": 21.0}


# -- the home sends the account's reading back to its nodes (TD-233 slice 2) ---------------------------


def test_a_node_adopts_each_window_the_home_confirmed_later_and_keeps_its_own_word():
    """`usage.adopt`: a window the home confirmed later, or saw roll, is the home's, history and all;
    one the node confirmed no earlier, or saw roll first, stays the node's; a window only one of them
    has is kept or added; `fetched` follows the newer confirmation, and the node's `reason` and
    cool-off stay its own."""
    held = usage.merge(None, [w("5h", 40.0), w("week", 20.0)], at="2026-10-01T01:00:00Z", source="asked", fresh=True)
    held |= {"reason": "rate_limited", "cool_until": "2026-10-01T02:00:00Z"}
    theirs = {
        "windows": [
            {
                **w("5h", 55.0),
                "at": "2026-10-01T01:30:00Z",
                "source": "reported",
                "history": [{"at": "2026-10-01T01:10:00Z", "pct": 50.0}, {"at": "2026-10-01T01:30:00Z", "pct": 55.0}],
            },
            {**w("week", 18.0), "at": "2026-10-01T00:30:00Z", "source": "reported", "history": []},
            {**w("week · Fable", 10), "at": "2026-10-01T00:40:00Z", "source": "asked", "history": []},
            {"label": "", "pct": 99},
        ],
        "fetched": "2026-10-01T01:30:00Z",
        "source": "reported",
        "by": "grinder-ao-1",
    }
    got = usage.adopt(held, theirs)
    assert pcts(got) == {"5h": 55.0, "week": 20.0, "week · Fable": 10}
    five = next(x for x in got["windows"] if x["label"] == "5h")
    assert five["at"] == "2026-10-01T01:30:00Z" and [h["pct"] for h in five["history"]] == [50.0, 55.0]
    assert got["fetched"] == "2026-10-01T01:30:00Z" and got["source"] == "reported" and got["by"] == "grinder-ao-1"
    assert got["reason"] == "rate_limited" and got["cool_until"] == "2026-10-01T02:00:00Z"

    # the node saw the window roll: the home's report of the window before it is over
    rolled = usage.merge(None, [w("5h", 2.0, R2)], at="2026-10-01T01:00:00Z", source="reported", fresh=True)
    assert pcts(usage.adopt(rolled, theirs))["5h"] == 2.0
    # the home saw it roll first: its window replaces the node's, whatever its age
    old = {"windows": [{**w("5h", 3.0, R2), "at": "2026-10-01T00:00:00Z", "source": "reported"}]}
    assert pcts(usage.adopt(held, old))["5h"] == 3.0
    # an older confirmation of the whole reading leaves the node's age as it was
    assert usage.adopt(held, old)["fetched"] == "2026-10-01T01:00:00Z"
    assert usage.adopt(held, None) == held and usage.adopt(None, {})["windows"] == []

    # a reset one side does not know is no roll: the newer confirmation stands (the review)
    unknown = usage.merge(None, [w("5h", 45.0, None)], at="2026-10-01T02:00:00Z", source="reported", fresh=True)
    assert pcts(usage.adopt(unknown, theirs))["5h"] == 45.0
    # the home's history is kept in order, whatever order it came in
    shuffled = {"windows": [{**theirs["windows"][0], "history": theirs["windows"][0]["history"][::-1]}]}
    assert [h["pct"] for h in usage.adopt(None, shuffled)["windows"][0]["history"]] == [50.0, 55.0]


class _Mux:
    def __init__(self):
        self.sent: list[dict] = []

    async def notify(self, method, **params):
        if method == "usage_reading":
            self.sent.append(params)


async def test_the_home_sends_a_nodes_account_back_when_it_moves_and_again_on_a_new_link(agent, hookstub):
    """The home sends the reading of each account a node keyed a live session to, as `usage_reading
    {account, reading}`, when it moved since that link was told — not before the link's snapshot,
    not twice for no change, and again to a new link."""
    hookstub.usage_value = None
    agent._take_records("laptop", [node_record()], whole=True)
    mux = _Mux()
    agent._link_muxes["laptop"] = mux
    report = {"id": "ao-x-w", "account": "hookstub:laptop-paul", "windows": [w("5h", 71.0)], "fresh": True}
    try:
        await agent._take_usage_report("laptop", report)
        assert mux.sent == []  # the link's snapshot is not taken yet
        agent._intent_sent["laptop"] = {}
        await agent._refresh_usage_inner()
        assert [(m["account"], pcts(m["reading"])) for m in mux.sent] == [("hookstub:laptop-paul", {"5h": 71.0})]
        assert set(mux.sent[0]["reading"]) == {"windows", "fetched", "source", "by"}

        await agent._refresh_usage_inner()
        await agent._take_usage_report("laptop", report | {"windows": [w("5h", 70.0)], "fresh": False})
        assert len(mux.sent) == 1  # nothing moved: nothing sent
        await agent._take_usage_report("laptop", report | {"windows": [w("5h", 73.0)]})
        assert [pcts(m["reading"]) for m in mux.sent] == [{"5h": 71.0}, {"5h": 73.0}]

        again = _Mux()
        agent._link_muxes["laptop"] = again  # a new dial: told once more, though nothing moved
        await agent._refresh_usage_inner()
        assert [pcts(m["reading"]) for m in again.sent] == [{"5h": 73.0}]
    finally:
        agent._link_muxes.pop("laptop", None)
        agent._intent_sent.pop("laptop", None)


async def test_a_node_takes_the_homes_reading_for_its_own_account_and_is_not_asked_while_young(
    agent, hookstub, tmp_path, monkeypatch
):
    """At the node, the home's reading of an account one of its live sessions is keyed to is adopted
    into its own, so its chip, gate and fallback read the whole account; one it keys none to is
    ignored; a young adopted reading holds off the endpoint."""
    monkeypatch.setattr(hookstub, "accounts", {"pn": "paul"})
    monkeypatch.setattr(hookstub, "usage_asked", [])
    monkeypatch.setattr(agent, "mode", "node")
    now = datetime.now(UTC)
    async with LocalClient() as c:
        (tmp_path / "n").mkdir()
        s = await c.call("create", name="nw", dir=str(tmp_path / "n"), adapter="hookstub", profile="pn")
        agent._usage_acct["hookstub:paul"] = usage.merge(
            None, [w("5h", 40.0)], at=iso(now - timedelta(hours=1)), source="asked", fresh=True
        )
        reading = {
            "windows": [{**w("5h", 55.0), "at": iso(now - timedelta(minutes=1)), "source": "reported"}],
            "fetched": iso(now - timedelta(minutes=1)),
            "source": "reported",
            "by": "grinder-ao-1",
        }
        await agent._from_home("usage_reading", {"account": "hookstub:paul", "reading": reading})
        assert pcts(agent._usage["pn"]) == {"5h": 55.0} and agent._usage["pn"]["by"] == "grinder-ao-1"

        await agent._from_home("usage_reading", {"account": "hookstub:heather", "reading": reading})
        assert "hookstub:heather" not in agent._usage_acct

        hookstub.usage_value = {"reason": "ok", "windows": [w("5h", 1.0)]}
        agent._usage_checked.clear()
        await agent._refresh_usage_inner()
        assert hookstub.usage_asked == [] and pcts(agent._usage["pn"]) == {"5h": 55.0}
        hookstub.usage_value = None
        await c.call("kill", id=s["id"])


async def test_after_a_home_restart_a_nodes_account_keeps_its_history(agent, hookstub):
    """The techlead's read of #783: the reading a node's profile held in `usage.json` survives the
    passes before the node's first report, and that report merges onto it — history and all — when
    it is of the node's account; one of another account starts clean."""
    hookstub.usage_value = None
    t0 = datetime.now(UTC) - timedelta(minutes=40)
    held = usage.merge(None, [w("5h", 60.0)], at=iso(t0), source="reported", fresh=True)
    held = usage.merge(held, [w("5h", 61.0)], at=iso(t0 + timedelta(minutes=20)), source="reported", fresh=True)
    agent._usage["pn"] = held | {"account": "laptop-paul", "tool": "hookstub"}
    agent._usage["pq"] = held | {"account": "laptop-paul", "tool": "hookstub"}
    agent._usage_restored |= {"pn"}  # as `usage.json` gave it at the start; `pq` came later
    agent._take_records("laptop", [node_record(), node_record("ao-x-q", name="nq", profile="pq")], whole=True)
    await agent._refresh_usage_inner()
    assert pcts(agent._usage["pn"]) == {"5h": 61.0}  # kept while no report has keyed it
    assert "pq" not in agent._usage  # a profile not restored at the start is shown by the rule

    report = {"id": "ao-x-w", "account": "hookstub:laptop-paul", "windows": [w("5h", 62.0)], "fresh": True}
    await agent._take_usage_report("laptop", report)
    assert [h["pct"] for h in agent._usage["pn"]["windows"][0]["history"]] == [60.0, 61.0, 62.0]

    for other in ({"account": "someone-else"}, {}):  # another account's, or one that names none
        agent._usage_acct.clear()
        agent._usage["pn"] = held | other | {"tool": "hookstub"}
        await agent._take_usage_report("laptop", report)
        assert [h["pct"] for h in agent._usage["pn"]["windows"][0]["history"]] == [62.0]


async def test_a_nodes_report_under_a_profile_metered_here_is_taken(agent, hookstub, monkeypatch):
    """The techlead's read of #783: `_metered` is this host's reading of its own profile files, and a
    node withholds its own metered profiles, so a node's report under a name metered here is taken."""
    hookstub.usage_value = None
    monkeypatch.setattr(agent, "_metered", {"pn"})
    agent._take_records("laptop", [node_record()], whole=True)
    report = {"id": "ao-x-w", "account": "hookstub:laptop-paul", "windows": [w("5h", 33.0)], "fresh": True}
    await agent._take_usage_report("laptop", report)
    assert pcts(agent._usage_acct["hookstub:laptop-paul"]) == {"5h": 33.0}
