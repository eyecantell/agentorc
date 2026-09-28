"""A metered account's ledger and reading at the home (design §4.4 *Usage*, §4.2a *How a profile is
billed*, §6 *Usage gate*; TD-151 slice 3): turns counted once across reads, rewrites and a response
that straddles two reads; the three windows in the home's clock; `pct` from the profile's amount;
the eight-tenths note; the gate's pause at the amount; `limited` never from it."""

from datetime import UTC, date, datetime, timedelta

import pytest
from conftest import park_ticks

from sessionorc import settings
from sessionorc import spend as spend_mod
from sessionorc.client import LocalClient

TODAY = date(2026, 9, 27)  # a Sunday


def _at(minute: int, ms: int = 0, day: date = TODAY) -> str:
    return f"{day.isoformat()}T10:{minute:02d}:00.{ms:03d}Z"


def _t(uid, at, offset, source="a", response=None, inp=0, out=0, cr=0, cw=0, cost=None):
    return {
        "at": at, "id": uid, "source": source, "offset": offset, "response": response or f"r-{uid}",
        "model": "m", "input": inp, "output": out, "cache_read": cr, "cache_write": cw, "cost": cost,
    }  # fmt: skip


def _ingest(acct, turns, cursors, profile="p", prices=None):
    result = {"turns": turns, "cursors": cursors, "reason": "ok"}
    return spend_mod.ingest(acct, "h", profile, result, prices, TODAY, UTC)


def _day(acct, d: date = TODAY):
    return acct["days"][d.isoformat()]


def test_the_first_read_takes_the_ends_and_a_turn_is_counted_once():
    acct: dict = {}
    assert _ingest(acct, [_t("u1", _at(1), 0, inp=100)], {"a": 10}) == 0, "history is not billed to the first day"
    assert "days" not in acct and acct["hosts"]["h"]["cursors"]["a"]["offset"] == 10
    assert _ingest(acct, [_t("u2", _at(2), 10, inp=50)], {"a": 20}) == 1
    assert _day(acct)["input"] == 50 and _day(acct)["turns"] == 1
    # a transcript that appears later is read from 0 and counts whole
    assert _ingest(acct, [_t("v1", _at(3), 0, source="b", out=7)], {"a": 20, "b": 5}) == 1
    # a second profile on the account, sharing the directory: its first read still counts what a
    # known transcript gained, and takes only the ends of the ones nobody had read
    got = _ingest(acct, [_t("u3", _at(4), 20, inp=1), _t("w1", _at(4), 0, source="c", inp=999)], {"a": 30, "c": 9}, "q")
    assert got == 1 and _day(acct)["input"] == 51 and acct["hosts"]["h"]["seeded"] == ["p", "q"]
    # a late turn from another transcript, stamped before what the others ledgered, still counts,
    # on its own day's row: the test is per transcript, never a time
    y = TODAY - timedelta(days=1)
    assert _ingest(acct, [_t("v2", _at(59, day=y), 0, source="d", out=3)], {"a": 30, "b": 5, "c": 9, "d": 4}) == 1
    assert _day(acct, y)["output"] == 3


def test_a_response_straddling_two_reads_counts_what_it_adds():
    acct: dict = {}
    _ingest(acct, [], {"a": 0})
    assert _ingest(acct, [_t("u1", _at(1), 0, response="m1", inp=10, out=5)], {"a": 10}) == 1
    later = [_t("u2", _at(1, 5), 10, response="m1", inp=10, out=40), _t("u3", _at(2), 20, response="m2", out=1)]
    assert _ingest(acct, later, {"a": 30}) == 2
    assert (_day(acct)["input"], _day(acct)["output"]) == (10, 41)


def test_a_rewrite_read_from_the_top_bills_nothing_twice():
    """§4.4: every entry before the last ledgered `at` is dropped, and at that `at` those up to and
    including the one with that `id` — so a new turn in the same millisecond still counts."""
    acct: dict = {}
    _ingest(acct, [], {"a": 0})
    _ingest(acct, [_t("u1", _at(1), 0, inp=1), _t("u2", _at(2), 5, inp=1), _t("u3", _at(2), 8, inp=1)], {"a": 30})
    assert _day(acct)["input"] == 3
    again = [_t("u1", _at(1), 0, inp=1), _t("u2", _at(2), 5, inp=1), _t("u3", _at(2), 8, inp=1)]
    again += [_t("n1", _at(2), 12, inp=10), _t("n2", _at(3), 15, inp=100)]
    assert _ingest(acct, again, {"a": 20}) == 2 and _day(acct)["input"] == 113
    # a compaction that removed the last ledgered entry: nothing at its `at` is billed again
    acct2: dict = {}
    _ingest(acct2, [], {"a": 0})
    _ingest(acct2, [_t("u1", _at(1), 0, inp=1), _t("u2", _at(2), 5, inp=1)], {"a": 30})
    gone = [_t("x", _at(2), 0, inp=10), _t("y", _at(2), 3, inp=10), _t("z", _at(4), 6, inp=100)]
    assert _ingest(acct2, gone, {"a": 9}) == 1 and _day(acct2)["input"] == 102


def test_a_rewrite_that_left_the_file_past_its_cursor_is_caught_by_at():
    """The techlead's read of #674: a rewrite that grew the file is read from the old cursor, and
    only the entry's `at` shows that what follows is old."""
    acct: dict = {}
    _ingest(acct, [], {"a": 0})
    _ingest(acct, [_t("u1", _at(5), 0, inp=1)], {"a": 30})
    assert _ingest(acct, [_t("old", _at(1), 35, inp=50), _t("new", _at(6), 40, inp=2)], {"a": 50}) == 1
    assert _day(acct)["input"] == 3


def test_cost_at_the_profiles_prices_cache_reads_at_a_tenth():
    prices = {"input": 3, "output": 15, "cache_read": 0.3}
    reads = spend_mod.cost_of({"input": 0, "output": 0, "cache_read": 1000, "cache_write": 0}, None, prices)
    inputs = spend_mod.cost_of({"input": 1000, "output": 0, "cache_read": 0, "cache_write": 0}, None, prices)
    assert reads == pytest.approx(inputs / 10)
    # a kind without a price is charged at input, the safe side
    assert spend_mod.cost_of({"input": 0, "output": 0, "cache_read": 0, "cache_write": 1000}, None, prices) == inputs
    assert spend_mod.cost_of({"input": 5, "output": 0, "cache_read": 0, "cache_write": 0}, None, None) is None
    assert spend_mod.cost_of({"input": 5, "output": 0, "cache_read": 0, "cache_write": 0}, 0.25, prices) == 0.25
    acct: dict = {}
    _ingest(acct, [], {"a": 0}, prices=prices)
    _ingest(acct, [_t("u1", _at(1), 0, inp=1_000_000)], {"a": 9}, prices=prices)
    assert _day(acct)["cost"] == 3.0


def test_the_windows_roll_in_the_homes_clock_and_pct_is_spend_over_the_amount():
    rows = {
        TODAY.isoformat(): {"input": 10, "cost": 4.1},
        (TODAY - timedelta(days=3)).isoformat(): {"input": 5, "cost": 2.0},
        (TODAY - timedelta(days=40)).isoformat(): {"input": 1, "cost": 9.0},
    }
    now = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)
    sums = spend_mod.sums({"days": rows}, now)
    assert (sums["day"]["cost"], sums["week"]["cost"], sums["month"]["cost"]) == (4.1, 6.1, 6.1)
    assert sums["day"]["resets"] == "2026-09-28T00:00:00+00:00"
    assert sums["week"]["resets"] == "2026-09-28T00:00:00+00:00"  # Monday
    assert sums["month"]["resets"] == "2026-10-01T00:00:00+00:00"
    amounts = {"day": settings.parse_amount("$5"), "week": settings.parse_amount("20 tok")}
    r = spend_mod.reading(sums, amounts, "t")
    by = {w["label"]: w for w in r["windows"]}
    assert by["day"]["pct"] == 82 and by["week"]["pct"] == 75 and by["month"]["pct"] is None
    assert by["day"]["spent"]["cost"] == 4.1 and "amount" not in by["month"] and r["reason"] == "ok"
    # a money amount on a priceless account makes no line
    priceless = spend_mod.sums({"days": {TODAY.isoformat(): {"input": 10}}}, now)
    assert spend_mod.reading(priceless, {"day": amounts["day"]}, "t")["windows"][0]["pct"] is None


def test_an_amount_is_money_or_tokens_and_the_percents_do_not_read_it(tmp_path):
    assert settings.parse_amount("$5") == {"value": 5.0, "unit": "$"}
    assert settings.parse_amount("2M tok") == {"value": 2_000_000.0, "unit": "tok"}
    assert settings.parse_amount("1500 tok") == {"value": 1500.0, "unit": "tok"}
    for bad in ("$0", "5", 30, "2M", "$x", None):
        with pytest.raises(ValueError):
            settings.parse_amount(bad)
    doc = {"usage_gate": {"api": {"day": "$5", "week": "nope"}, "grind": {"5h": 30}}}
    assert settings.amounts(doc) == {"api": {"day": {"value": 5.0, "unit": "$"}}}
    assert settings.reserves(doc) == {"grind": {"5h": 30}}


def test_prune_keeps_thirteen_months():
    acct = {"days": {"2025-08-01": {}, "2026-09-01": {}}, "hosts": {"h": {"cursors": {"a": {"seen": "2025-08-01"}}}}}
    spend_mod.prune(acct, TODAY)
    assert list(acct["days"]) == ["2026-09-01"] and acct["hosts"]["h"]["cursors"] == {}


@pytest.mark.integration
async def test_a_metered_accounts_spend_is_summed_noted_and_gated(agent, hookstub, tmp_path, monkeypatch):
    """Two profiles on one key share one spend and keep their own amounts; the note at eight tenths
    is filed once; the unattended session pauses at its amount; the interactive one is never
    `limited`; the metered profiles are never polled; the ledger is on disk for a restart."""
    await park_ticks(agent)
    notes: list[str] = []
    monkeypatch.setattr(agent, "_system_note", lambda to, text, **kw: notes.append(text))
    hookstub.billing = {p: {"billing": "metered", "prices": {"input": 1.0}} for p in ("api", "api2")}
    hookstub.accounts = {"api": "key", "api2": "key"}
    hookstub.usage_asked = []
    settings.save({"usage_gate": {"api": {"day": "$5"}, "api2": {"day": "$10"}}})
    now = datetime.now(UTC).replace(microsecond=0)
    at = now.isoformat().replace("+00:00", "Z")
    hookstub.spend_turns = [_t("u0", at, 0, source="t1", inp=1_000_000)]
    async with LocalClient() as person:
        w = await person.call(
            "create", name="w", dir=str(tmp_path), adapter="hookstub", profile="api", unattended=True,
            pause_prompt="echo PAUSE-NOW", resume_prompt="echo RESUME-NOW",
        )  # fmt: skip
        (tmp_path / "i").mkdir()
        i = await person.call("create", name="i", dir=str(tmp_path / "i"), adapter="hookstub", profile="api2")
        await agent._refresh_spend_inner()  # the first read takes the ends
        assert agent._usage["api"]["windows"][0]["spent"]["cost"] is None
        hookstub.spend_turns.append(_t("u1", at, 1, source="t1", inp=4_100_000))
        await agent._refresh_spend_inner()
        await agent._refresh_spend_inner()
        api, api2 = agent._usage["api"]["windows"][0], agent._usage["api2"]["windows"][0]
        assert (api["label"], api["spent"]["cost"], api["pct"], api2["pct"]) == ("day", 4.1, 82, 41)
        assert agent._usage["api"]["account"] == "key" and notes == ["api · day $4.10 of $5"]
        hookstub.spend_turns.append(_t("u2", at, 2, source="t1", inp=900_000))
        await agent._refresh_spend_inner()
        assert agent._usage["api"]["windows"][0]["pct"] == 100
        await agent._enforce_usage_gate(now)
        g = agent.sessions[w["id"]].gated
        assert g and (g["label"], g["pct"], g["line"]) == ("day", 100, 100)
        await agent._refresh_usage_inner()
        assert agent.sessions[i["id"]].state != "limited" and hookstub.usage_asked == []
        assert agent._usage["api2"]["windows"][0]["pct"] == 50, "the poll leaves a summed reading alone"
        held = spend_mod.SpendStore().load()["hookstub:key"]
        assert sum(r["cost"] for r in held["days"].values()) == pytest.approx(5.0)
        for s in (w, i):
            await person.call("kill", id=s["id"])
            await person.call("remove", id=s["id"])


def test_status_v_prints_a_metered_profiles_spend():
    from agentorc.cli import spend_line

    now = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)
    rows = {TODAY.isoformat(): {"input": 1_200_000, "output": 0, "cache_read": 0, "cache_write": 0, "cost": 3.2}}
    r = spend_mod.reading(spend_mod.sums({"days": rows}, now), {"day": settings.parse_amount("$5")}, "t")
    assert spend_line(r) == "day $3.20 / $5.00 (64%) · week $3.20 · month $3.20"
    priceless = spend_mod.reading(spend_mod.sums({"days": {TODAY.isoformat(): {"input": 1_200_000}}}, now), {}, "t")
    unread = spend_line(priceless | {"reason": "no transcripts"})
    assert unread.endswith("month 1.2M tok · spend unknown (no transcripts)")
    assert spend_line({"windows": [{"label": "5h", "pct": 3}], "reason": "ok"}) == ""  # a polled reading


def test_a_boundary_past_a_dst_change_carries_its_own_offset(monkeypatch):
    """The review of #681: the home's `now` carries a fixed offset, and a reset on the far side of a
    DST change must still be that day's local midnight."""
    import time

    monkeypatch.setenv("TZ", "America/Denver")
    time.tzset()
    try:
        now = datetime(2026, 10, 31, 12, 0).astimezone()  # MDT, -06:00; DST ends Sunday Nov 1
        b = spend_mod.bounds(now)
        assert b["day"][1].isoformat() == "2026-11-01T00:00:00-06:00"
        assert b["week"][1].isoformat() == "2026-11-02T00:00:00-07:00"
    finally:
        monkeypatch.undo()
        time.tzset()
