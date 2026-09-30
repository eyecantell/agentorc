"""TD-239 slice 1, design §6 *Balance*: `teams.<team>.balance` and its three lines, and the home's
mark — written on its own `host` record from the repo facts and the reader's queue, kept while a
reading cannot be told, gone when no line is crossed or the team has no live member."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks

from sessionorc import balance, paths
from sessionorc import settings as settings_mod
from sessionorc.client import AgentError, LocalClient

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _prs(n: int, oldest: timedelta = timedelta(hours=1), **extra) -> dict:
    born = NOW - oldest
    open_ = [
        {"number": 700 + i, "created": (born + timedelta(minutes=i)).isoformat(), "draft": i == 0} for i in range(n)
    ]
    return {"prs": {"open": open_, "at": NOW.isoformat(), **extra}}


# -- the setting ------------------------------------------------------------------------------------


def test_parse_balance_takes_the_three_lines_each_optional():
    assert settings_mod.parse_balance({"prs": 8, "oldest": "2d", "review": True}) == {
        "prs": 8,
        "oldest": "2d",
        "review": True,
    }
    assert settings_mod.parse_balance({"oldest": " 12h "}) == {"oldest": "12h"}
    assert settings_mod.parse_team({"balance": {"prs": 3}}) == {"balance": {"prs": 3}}


@pytest.mark.parametrize(
    "value, words",
    [
        ({}, "at least one line"),
        ({"prs": 8, "pr": 9}, "unknown key pr"),
        ({"prs": 0}, "balance.prs"),
        ({"prs": True}, "balance.prs"),
        ({"oldest": "2 days"}, "balance.oldest"),
        ({"review": "yes"}, "balance.review"),
        ("10", "mapping"),
    ],
)
def test_parse_balance_refuses_what_draws_no_line(value, words):
    with pytest.raises(ValueError, match=words):
        settings_mod.parse_balance(value)


def test_the_reader_drops_a_bad_line_and_a_balance_left_with_none():
    doc = {
        "teams": {
            "a": {"balance": {"prs": 8, "oldest": "soon", "colour": "red"}},
            "b": {"balance": {"prs": -1}, "reserve": 10},
            "c": {"balance": {}},
        }
    }
    assert settings_mod.teams(doc) == {"a": {"balance": {"prs": 8}}, "b": {"reserve": 10}}


# -- the reading --------------------------------------------------------------------------------------


def _crossed(bal, repos, roots=("/r",), waiting=None, bound=balance.REVIEW_BOUND):
    return balance.crossed(bal, list(roots), repos, waiting, bound, NOW)


def test_nine_open_prs_cross_a_line_of_eight_and_eight_do_not():
    assert _crossed({"prs": 8}, {"/r": _prs(9)}) == ([{"line": "prs", "value": 9, "limit": 8}], "/r")
    assert _crossed({"prs": 8}, {"/r": _prs(8)}) == ([], "/r")  # a draft is counted: _prs(8) holds one


def test_the_oldest_open_pr_past_its_line_crosses_in_seconds():
    got = _crossed({"oldest": "2d"}, {"/r": _prs(2, oldest=timedelta(days=3))})
    assert got == ([{"line": "oldest", "value": 3 * 86400, "limit": 2 * 86400}], "/r")
    assert _crossed({"oldest": "2d"}, {"/r": _prs(2, oldest=timedelta(days=1))}) == ([], "/r")


def test_the_readers_queue_past_the_bound_crosses():
    waited = (NOW - timedelta(hours=3)).isoformat()
    got = _crossed({"review": True}, {}, waiting=waited)
    assert got == ([{"line": "review", "value": 3 * 3600, "limit": 2 * 3600}], "/r")
    assert _crossed({"review": True}, {}, waiting=waited, bound=timedelta(hours=4)) == ([], "/r")
    assert _crossed({"review": True}, {}, waiting=None) == ([], "/r")
    assert _crossed({"review": False, "prs": 50}, {"/r": _prs(1)}, waiting=waited) == ([], "/r")


def test_a_reading_that_failed_or_is_missing_cannot_be_told():
    assert _crossed({"prs": 8}, {"/r": _prs(20, error="gh: offline")}) is None
    assert _crossed({"prs": 8}, {}) is None
    assert _crossed({"oldest": "1h"}, {"/r": {"prs": None}}) is None


def test_a_team_over_two_repos_crosses_on_either():
    repos = {"/a": _prs(2), "/b": _prs(9)}
    assert _crossed({"prs": 8}, repos, roots=("/a", "/b")) == ([{"line": "prs", "value": 9, "limit": 8}], "/b")
    # one repo could not be read and the other is over: the crossing is known
    repos["/a"] = _prs(1, error="gh: offline")
    assert _crossed({"prs": 8}, repos, roots=("/a", "/b"))[0][0]["value"] == 9
    # one could not be read and the other is under: it cannot be told
    repos["/b"] = _prs(2)
    assert _crossed({"prs": 8}, repos, roots=("/a", "/b")) is None


# -- the mark ---------------------------------------------------------------------------------------


async def _member(person, tmp_path, name, team="grind", **kw):
    s = await person.call(
        "create", name=name, dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"], team=team, **kw
    )
    return s["id"]


async def test_the_home_marks_a_team_over_its_line_and_clears_it(agent, tmp_path):
    await park_ticks(agent)
    root = str(tmp_path / "repo")
    async with LocalClient() as person:
        got = await person.call("set_settings", teams={"grind": {"balance": {"prs": 8}}})
        assert got["teams"]["grind"]["balance"] == {"prs": 8}
        sid = await _member(person, tmp_path, "g1")
        agent.sessions[sid].repo = root
        agent._repos[root] = {"name": "repo", **_prs(9)}

        await agent._balance_marks(datetime.now(UTC))
        mark = agent._host_rec["teams"]["grind"]["balance"]
        assert mark["repo"] == root and mark["crossed"] == [{"line": "prs", "value": 9, "limit": 8}]
        since = mark["since"]
        assert json.loads(paths.host_file().read_text())["teams"]["grind"]["balance"] == mark
        served = (await person.call("repos"))[root]
        assert served["balance"] == {"grind": mark} and served["prs"]["open"]

        # still over, by more: the mark keeps its `since`
        agent._repos[root] = {"name": "repo", **_prs(11)}
        await agent._balance_marks(datetime.now(UTC))
        mark = agent._host_rec["teams"]["grind"]["balance"]
        assert mark["since"] == since and mark["crossed"][0]["value"] == 11

        # the reading failed: the mark stands as it was, and is not cleared
        agent._repos[root] = {"name": "repo", **_prs(2, error="gh: offline")}
        await agent._balance_marks(datetime.now(UTC))
        assert agent._host_rec["teams"]["grind"]["balance"] == mark

        # under the line: the mark goes, from the record, the file and the reading served
        agent._repos[root] = {"name": "repo", **_prs(8)}
        await agent._balance_marks(datetime.now(UTC))
        assert "grind" not in agent._host_rec["teams"]
        assert json.loads(paths.host_file().read_text()) == {"teams": {}}
        assert "balance" not in (await person.call("repos"))[root]


async def test_a_team_with_no_key_or_no_live_member_has_no_mark(agent, tmp_path):
    await park_ticks(agent)
    root = str(tmp_path / "repo")
    async with LocalClient() as person:
        other = await _member(person, tmp_path, "o1", team="other")
        agent.sessions[other].repo = root
        agent._repos[root] = {"name": "repo", **_prs(30)}
        await agent._balance_marks(datetime.now(UTC))
        assert agent._host_rec["teams"] == {}  # `other` has no balance key: never marked

        await person.call("set_settings", teams={"other": {"balance": {"prs": 8}}})
        await agent._balance_marks(datetime.now(UTC))
        assert agent._host_rec["teams"]["other"]["balance"]["crossed"][0]["value"] == 30

        await person.call("kill", id=other)
        agent.sessions[other].state = "exited"
        await agent._balance_marks(datetime.now(UTC))
        assert agent._host_rec["teams"] == {}  # nobody to refuse: the mark goes


async def test_the_review_line_reads_the_seats_queue_against_the_shortest_bound(agent, tmp_path, monkeypatch):
    await park_ticks(agent)
    root = str(tmp_path / "repo")
    async with LocalClient() as person:
        await person.call("set_settings", teams={"grind": {"balance": {"review": True}}})
        g = await _member(person, tmp_path, "g1")
        seat = await _member(person, tmp_path, "tl")
        agent.sessions[g].repo = agent.sessions[seat].repo = root
        agent.sessions[g].review = {"reader": "techlead", "held": ["**"], "bound": "90m"}
        agent.sessions[seat].seat = {"trigger": "asks"}
        waited = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        monkeypatch.setattr(agent.sessions[seat], "prs_waiting", lambda home=None: {"n": 1, "oldest": waited})

        await agent._balance_marks(datetime.now(UTC))
        assert "grind" not in agent._host_rec["teams"]  # an hour, under the 90 minutes

        waited = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
        await agent._balance_marks(datetime.now(UTC))
        (line,) = agent._host_rec["teams"]["grind"]["balance"]["crossed"]
        assert line["line"] == "review" and line["limit"] == 90 * 60 and line["value"] >= 3 * 3600 - 5


async def test_set_settings_refuses_a_balance_that_draws_no_line(agent):
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="at least one line"):
            await person.call("set_settings", teams={"grind": {"balance": {}}})
        with pytest.raises(AgentError, match="unknown key pr"):
            await person.call("set_settings", teams={"grind": {"balance": {"pr": 3}}})
        await person.call("set_settings", teams={"grind": {"balance": {"prs": 3}, "reserve": 5}})
        got = await person.call("set_settings", teams={"grind": {"balance": None}})
        assert got["teams"] == {"grind": {"reserve": 5}}


def test_a_host_record_that_cannot_be_read_is_empty(tmp_path):
    from sessionorc.store import HostStore

    p = tmp_path / "host.json"
    p.write_text("{not json")
    assert HostStore(p).load() == {"teams": {}}
    p.write_text(json.dumps({"teams": {"a": {"balance": {"since": "x"}}, "b": 3}}))
    assert HostStore(p).load() == {"teams": {"a": {"balance": {"since": "x"}}}}


async def test_a_mark_is_saved_with_no_repo_and_a_repo_the_home_does_not_read_is_not_the_teams(
    agent, tmp_path, monkeypatch
):
    """The review's findings on #772: a `review` crossing by members naming no registered repo is
    still saved, a member's unregistered path neither crosses nor makes the reading unknown, and one
    team's surprise costs the others nothing."""
    await park_ticks(agent)
    root = str(tmp_path / "repo")
    async with LocalClient() as person:
        await person.call(
            "set_settings",
            teams={
                "grind": {"balance": {"review": True}},
                "other": {"balance": {"prs": 1}},
                "bad": {"balance": {"prs": 1}},
            },
        )
        seat = await _member(person, tmp_path, "tl")
        agent.sessions[seat].seat = {"trigger": "asks"}
        waited = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
        monkeypatch.setattr(agent.sessions[seat], "prs_waiting", lambda home=None: {"n": 1, "oldest": waited})

        o1 = await _member(person, tmp_path, "o1", team="other")
        o2 = await _member(person, tmp_path, "o2", team="other")
        agent.sessions[o1].repo = root
        agent.sessions[o2].repo = "/elsewhere/not/registered"
        agent._repos[root] = {"name": "repo", **_prs(3)}

        b1 = await _member(person, tmp_path, "b1", team="bad")
        agent.sessions[b1].review = "not a mapping"  # a record a hand could have broken

        await agent._balance_marks(datetime.now(UTC))
        saved = json.loads(paths.host_file().read_text())["teams"]
        assert saved["grind"]["balance"]["repo"] == "" and saved["grind"]["balance"]["crossed"][0]["line"] == "review"
        assert saved["other"]["balance"]["repo"] == root  # the unregistered path is not the team's repo
        assert "bad" not in saved

        # a new subscriber's snapshot carries the marks, as `repos` does
        async with LocalClient() as sub:
            import asyncio

            got = []

            async def listen():
                async for ev in sub.subscribe():
                    if ev.get("event") == "repos":
                        got.append(ev)

            listener = asyncio.create_task(listen())
            await asyncio.sleep(0.3)
            listener.cancel()
        assert got and got[0]["repo"]["balance"] == {"other": saved["other"]["balance"]}
