"""A metered account's ledger and reading at the home (design §4.4 *Usage*, §4.2a *How a profile is
billed*, §6 *Usage gate*; TD-151 slice 3): turns counted once across reads, rewrites and a response
that straddles two reads; the three windows in the home's clock; `pct` from the profile's amount;
the eight-tenths note; the gate's pause at the amount; `limited` never from it."""

import json
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
        # a restart of the home: the gate runs before the detached spend pass, and a pause made at an
        # amount stands, with nothing typed (the techlead's read of #681). A moment later, never an
        # hour: the day is the home's local one, and within an hour of midnight an hour on is the
        # next day, whose reset lifts the pause (TD-242)
        agent._metered, agent._billing_seen = set(), {}
        typed: list[str] = []

        async def counting(sid_, adapter, text):
            typed.append(text)

        monkeypatch.setattr(agent, "_submit", counting)
        await agent._enforce_usage_gate(now + timedelta(seconds=1))
        assert agent.sessions[w["id"]].gated and typed == []
        # one definition of live: a record the quota poll does not count is not read for spend either
        agent.sessions[i["id"]].kind = "command"
        await agent._refresh_spend_inner()
        await agent._refresh_usage_inner()
        assert "api2" not in agent._usage and "api" in agent._usage
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


# -- a node's road (§4.4 *Usage*, §4.4a; TD-151 slice 4) --------------------------------------------


def test_a_long_batch_goes_in_pieces_cut_at_a_line_and_counts_as_one_read():
    """A piece ends at a turn's offset, so a transcript cut across two pieces resumes at that line;
    ingesting the pieces in order counts exactly what one read would, and a piece sent again after
    its reply was lost counts nothing (the build fact (ii))."""
    turns = [_t(f"a{i}", _at(1, i), 10 * i, source="a", inp=100) for i in range(5)]
    turns += [_t(f"b{i}", _at(2, i), 10 * i, source="b", inp=10) for i in range(3)]
    result = {"turns": turns, "cursors": {"a": 50, "b": 30, "quiet": 7}}
    size = max(len(json.dumps(t)) for t in turns)
    got = spend_mod.pieces(result, budget=2 * size)
    assert [len(p["turns"]) for p in got] == [2, 2, 2, 2]
    assert got[0]["cursors"] == {"quiet": 7, "a": 20}, "cut inside a: resume at the next line"
    assert got[2]["cursors"] == {"a": 50, "b": 10}, "a's end rides with its last turn, b is cut after its first"
    assert got[-1]["cursors"] == {"b": 30}
    assert spend_mod.pieces(result) == [
        {"turns": sorted(turns, key=lambda t: (t["source"], t["offset"])), "cursors": {"quiet": 7, "a": 50, "b": 30}}
    ]

    whole: dict = {}
    _ingest(whole, [], {"a": 0, "b": 0, "quiet": 0})  # seeded, with every transcript at its start
    _ingest(whole, turns, result["cursors"])
    cut: dict = {}
    _ingest(cut, [], {"a": 0, "b": 0, "quiet": 0})
    for p in got:
        _ingest(cut, p["turns"], p["cursors"])
    assert _day(cut) == _day(whole) and _day(cut)["input"] == 530
    assert _ingest(cut, got[1]["turns"], got[1]["cursors"]) == 0, "a resent piece is dropped turn by turn"
    assert _day(cut)["input"] == 530


def test_the_offline_figure_is_the_held_sums_plus_the_nodes_own_turns():
    """Offline, a node's figure adds its unacknowledged turns to the sums the home last sent; a window
    whose reset has passed since counts from nothing (§4.4 *Usage*)."""
    now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    held = spend_mod.sums({"days": {"2026-09-27": {"input": 1000, "cost": 2.0}}}, now)
    t = _t("u1", now.isoformat(), 0, inp=500, cost=0.5)
    fig = spend_mod.figure(held, [t], None, now)
    assert (fig["day"]["tokens"]["input"], fig["day"]["cost"]) == (1500, 2.5)
    tomorrow = now + timedelta(days=1)
    fig = spend_mod.figure(held, [t | {"at": tomorrow.isoformat()}], None, tomorrow)
    assert (fig["day"]["total"], fig["week"]["total"]) == (500, 500), "the day and the week (Sunday→Monday) rolled"
    assert fig["month"]["total"] == 1500


async def test_the_home_takes_a_nodes_spend_and_pushes_its_sums_when_its_readings_move(agent, monkeypatch):
    """The home's side (§4.4a): a read answers the cursors and whether the profile is seeded; turns
    are ledgered under the node's host; a node is pushed `usage {account, sums}` only when a named
    profile's `pct` or a window's reset moved, never on every tick."""
    await park_ticks(agent)
    settings.save({"usage_gate": {"api": {"day": "$10"}}})
    sent: list[dict] = []

    class Mux:
        async def notify(self, method, **params):
            sent.append({"method": method, **params})

    monkeypatch.setitem(agent._link_muxes, "laptop", Mux())
    now = datetime.now().astimezone().replace(microsecond=0)  # the home's own clock, as the pass has it
    at = now.astimezone(UTC).isoformat().replace("+00:00", "Z")
    head = {"account": "hookstub:key", "profile": "api", "prices": {"input": 1.0}}
    got = await agent._take_spend("laptop", head | {"turns": [], "cursors": {}})
    assert (got["cursors"], got["seeded"]) == ({}, False) and set(got["sums"]) == {"day", "week", "month"}
    got = await agent._take_spend("laptop", head | {"turns": [_t("u0", at, 0, inp=9)], "cursors": {"t": 1}})
    assert (got["cursors"], got["seeded"], got["sums"]["day"]["total"]) == ({"t": 1}, True, 0)
    turn = _t("u1", at, 1, source="t", inp=1_000_000)
    got = await agent._take_spend("laptop", head | {"turns": [turn], "cursors": {"t": 2}})
    assert got["sums"]["day"]["cost"] == 1.0
    assert agent._spend["hookstub:key"]["hosts"]["laptop"]["cursors"]["t"]["offset"] == 2
    await agent._push_spend_usage(now)
    assert sent == [], "the reply told it: nothing moved since"
    acct = agent._spend["hookstub:key"]
    acct["days"][now.astimezone().date().isoformat()]["input"] += 50  # a turn too small to move a point
    await agent._push_spend_usage(now)
    assert sent == []
    acct["days"][now.astimezone().date().isoformat()]["cost"] += 1.0  # another host's turn: 20%
    await agent._push_spend_usage(now)
    await agent._push_spend_usage(now)
    assert [(m["method"], m["account"], m["sums"]["day"]["cost"]) for m in sent] == [("usage", "hookstub:key", 2.0)]
    # the eight-tenths note for a node's profile is the home's, filed once per window
    notes: list[str] = []
    monkeypatch.setattr(agent, "_system_note", lambda to, text, **kw: notes.append(text))
    acct["days"][now.astimezone().date().isoformat()]["cost"] += 6.5
    await agent._push_spend_usage(now)
    await agent._push_spend_usage(now)
    assert notes == ["api · day $8.50 of $10"]
    # a node's figure is kept only when it is one: a negative cost is not a cost
    bad = _t("u2", at, 2, source="t", inp=0, cost=-100.0)
    await agent._take_spend("laptop", head | {"turns": [bad], "cursors": {"t": 3}})
    assert agent._spend["hookstub:key"]["days"][now.astimezone().date().isoformat()]["cost"] == 8.5


async def test_set_settings_and_gate_take_a_metered_profiles_amounts(agent, hookstub):
    """§6 *Usage gate*, §4.7 `ao gate` (TD-151 slice 5): a metered profile's reserve is an amount per
    window of the home's three, set before its first session; a percent is refused by naming the
    billing, as are money on a profile with no prices and an amount on a subscription profile; `gate`
    answers the amounts as written with the spend beside them."""
    from agentorc.cli import _gate_line, _reserve
    from sessionorc.client import AgentError

    await park_ticks(agent)
    hookstub.billing = {
        "api": {"billing": "metered", "prices": {"input": 1.0}},
        "toks": {"billing": "metered", "prices": {}},
    }
    async with LocalClient() as person:
        got = await person.call("set_settings", profile="api", reserves={"day": "$5", "week": "20M tok"})
        assert got["metered"] and got["reserves"] == {"day": "$5", "week": "20M tok"}
        assert [r.get("unread") for r in got["windows"]] == [True, True]
        with pytest.raises(AgentError, match="is metered, so its reserve is an amount"):
            await person.call("set_settings", profile="api", reserves={"day": 30})
        with pytest.raises(AgentError, match="its windows are day, week, month, not 5h"):
            await person.call("set_settings", profile="api", reserves={"5h": "$5"})
        with pytest.raises(AgentError, match="declares no prices"):
            await person.call("set_settings", profile="toks", reserves={"day": "$5"})
        with pytest.raises(AgentError, match="billed by subscription, so its reserve is a percent"):
            await person.call("set_settings", profile="grind", reserves={"5h": "$5"})
        await person.call("set_settings", profile="api", reserves={"week": None})
        assert settings.load()["usage_gate"]["api"] == {"day": "$5"}
        agent._usage["api"] = spend_mod.reading(
            spend_mod.sums({"days": {datetime.now().astimezone().date().isoformat(): {"cost": 3.2}}},
                           datetime.now().astimezone()),
            settings.amounts(settings.load())["api"], "now",
        )  # fmt: skip
        gate = (await person.call("gate"))["profiles"]["api"]
    assert gate["metered"] and gate["reserves"] == {"day": "$5"}
    assert _gate_line("api", gate["windows"]) == "api · day $5 → spent $3.20 (64%)"
    assert _reserve(" $5 ") == "$5" and _reserve("2M tok") == "2M tok" and _reserve("30") == 30


async def test_a_metered_profile_only_a_node_defines_takes_an_amount_at_the_home(agent, hookstub):
    """The techlead's read of #689: the home's adapters do not know a profile only a node defines, so
    its billing is read from the node's `spend` calls — and its prices, for the money check."""
    from sessionorc.client import AgentError

    await park_ticks(agent)
    hookstub.billing = {}
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="billed by subscription"):
            await person.call("set_settings", profile="lap", reserves={"day": "$5"})
        head = {"account": "hookstub:k", "profile": "lap", "turns": [], "cursors": {}}
        await agent._take_spend("laptop", head | {"prices": {}})
        with pytest.raises(AgentError, match="declares no prices"):
            await person.call("set_settings", profile="lap", reserves={"day": "$5"})
        await agent._take_spend("laptop", head | {"prices": {"input": 1.0}})
        got = await person.call("set_settings", profile="lap", reserves={"day": "$5"})
    assert got["metered"] and got["reserves"] == {"day": "$5"}
    # a profile the home itself defines keeps the billing it gives it, whatever a node named
    hookstub.billing = {"lap": {"billing": "subscription"}}
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="billed by subscription"):
            await person.call("set_settings", profile="lap", reserves={"day": "$6"})
