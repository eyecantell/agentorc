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
from sessionorc.models import Session

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
        assert "balance" not in agent._host_rec["teams"]["grind"]  # what was told is kept for the flap window
        assert "balance" not in json.loads(paths.host_file().read_text())["teams"]["grind"]
        assert "balance" not in (await person.call("repos"))[root]
        await agent._balance_marks(datetime.now(UTC) + balance.FLAP)
        assert json.loads(paths.host_file().read_text())["teams"] == {}


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

        # the mark with no repo is in no checkout's reading: the `host` read carries every mark, and
        # the client's reading takes it from there (TD-330 (3))
        home = await person.call("host")
        assert home["balance"] == {"grind": saved["grind"]["balance"], "other": saved["other"]["balance"]}
        from agentorc import teamrun

        repos = await person.call("repos")
        assert set(teamrun.balance_marks(repos)) == {"other"}
        assert teamrun.balance_marks(repos, home) == home["balance"]


# -- the refusal (slice 2) ----------------------------------------------------------------------------


def _mark(agent, team="grind", **over):
    mark = {
        "since": datetime.now(UTC).isoformat(),
        "repo": "/r",
        "crossed": [{"line": "prs", "value": 9, "limit": 8}],
        **over,
    }
    agent._host_rec.setdefault("teams", {})[team] = {"balance": mark}
    return mark


def test_the_refusal_says_the_marks_numbers_and_the_durations_read_alike():
    assert [balance.duration(s) for s in (0, 40 * 60, 5 * 3600 + 600, 2 * 86400, 3 * 86400 + 4 * 3600 + 59)] == [
        "0m",
        "40m",
        "5h 10m",
        "2d",
        "3d 4h",
    ]
    mark = {
        "since": "2026-09-29T14:02:00+00:00",
        "crossed": [
            {"line": "prs", "value": 9, "limit": 8},
            {"line": "oldest", "value": 3 * 86400, "limit": 2 * 86400},
            {"line": "review", "value": 5 * 3600, "limit": 7200},
        ],
    }
    words = balance.refusal("ao-grind", mark)
    assert words.startswith("ao-grind is over its line: 9 open PRs, the line is 8; the oldest PR open 3d, the line")
    assert "the reader's queue waiting 5h, the bound is 2h (since " in words
    assert "Take nothing new: finish, rebase or answer what is open of yours" in words
    assert "you are told when the line clears" in words


async def test_a_member_of_a_team_over_its_line_is_refused_a_new_claim(agent, tmp_path):
    """Design §6 *Balance*: a declared claim by an unattended member that is no seat is refused while
    its team's mark stands, `--force` or not, in the mark's numbers; a renewal, a pull request as the
    reference, `done` and `dropped` pass, and so does every claim once the mark goes."""
    await park_ticks(agent)
    async with LocalClient() as person:
        g = await _member(person, tmp_path, "g1", unattended=True)
        await person.call("progress", id=g, ref="TD-800", status="claimed")  # held before the crossing
        _mark(agent)
        with pytest.raises(AgentError, match="over its line"):
            await person.call("progress", id=g, ref="TD-900", status="claimed")
        assert agent.sessions[g].balance_refused is None, "a person's write is refused, and nobody tried to be rung"
        for force in (False, True):
            with pytest.raises(AgentError, match="grind is over its line: 9 open PRs, the line is 8") as no:
                async with LocalClient(caller=g) as me:
                    await me.call("progress", id=g, ref="TD-900", status="claimed", force=force)
            assert no.value.data["balance"]["crossed"][0]["value"] == 9
        rec = agent.sessions[g]
        assert rec.balance_refused["ref"] == "TD-900" and not any(e.ref == "TD-900" for e in rec.progress)
        assert json.loads((paths.sessions_dir() / f"{g}.json").read_text())["balance_refused"]["ref"] == "TD-900"

        await person.call("progress", id=g, ref="TD-800", status="claimed")  # a renewal
        await person.call("progress", id=g, ref="TD-801", status="claimed", source="derived")  # its branch's
        await person.call("progress", id=g, ref="TD-801", status="claimed")  # declaring work in hand passes
        await person.call("progress", id=g, ref="#712", status="claimed")  # reading a PR brings the count down
        await person.call("progress", id=g, ref="TD-800", status="done", pr=712)
        await person.call("progress", id=g, ref="TD-700", status="dropped", why="not mine")
        with pytest.raises(AgentError, match="over its line"):
            await person.call("progress", id=g, ref="TD-800", status="claimed")  # done: no longer held

        agent._host_rec["teams"].pop("grind")
        await person.call("progress", id=g, ref="TD-900", status="claimed")
        assert rec.balance_refused is None, "the line cleared and it claimed: nothing left to ring"


async def test_a_seat_an_interactive_member_and_another_team_are_not_refused(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        seat = await _member(person, tmp_path, "tl", unattended=True)
        agent.sessions[seat].seat = {"trigger": "asks"}
        mine = await _member(person, tmp_path, "paul")  # a person's own session in the team
        other = await _member(person, tmp_path, "o1", team="other", unattended=True)
        _mark(agent)
        for sid in (seat, mine, other):
            await person.call("progress", id=sid, ref="TD-900", status="claimed", force=True)
            assert agent.sessions[sid].balance_refused is None


async def test_none_is_refused_while_the_mark_stands_and_taken_once_it_goes(agent, tmp_path):
    """A refused member is not out of work: its team idles over its line and never winds down on it."""
    await park_ticks(agent)
    async with LocalClient() as person:
        g = await _member(person, tmp_path, "g1", unattended=True)
        _mark(agent, crossed=[{"line": "oldest", "value": 3 * 86400, "limit": 2 * 86400}])
        async with LocalClient(caller=g) as me:
            with pytest.raises(AgentError, match="the oldest PR open 3d, the line is 2d") as no:
                await me.call("progress", id=g, status="none", why="nothing I may pick")
            assert "balance" in no.value.data and agent.sessions[g].out_of_work is None
            assert agent.sessions[g].balance_refused["ref"] is None
            got = await me.call("progress", id=g, status="restart", why="context bound")
            assert got["restart_wanted"]["why"] == "context bound"  # a restart is not a claim
            agent.sessions[g].restart_wanted = None
            agent._host_rec["teams"].pop("grind")
            got = await me.call("progress", id=g, status="none", why="nothing I may pick")
            assert got["out_of_work"]["why"] == "nothing I may pick" and agent.sessions[g].balance_refused is None


async def test_a_refused_member_is_neither_nudged_nor_told_of_its_lane_while_the_mark_stands(
    agent, tmp_path, monkeypatch
):
    await park_ticks(agent)
    now = datetime.now(UTC)
    root = str(tmp_path / "repo")
    sent, notes = [], []

    async def policy_send(s, text):
        sent.append((s.id, text))
        return True

    monkeypatch.setattr(agent, "_policy_send", policy_send)
    monkeypatch.setattr(agent, "_system_note", lambda to, text, **kw: notes.append((to, text)))
    async with LocalClient() as person:
        g = await _member(person, tmp_path, "g1", unattended=True, lane=["TD-1"])
        rec = agent.sessions[g]
        rec.supervised, rec.repo = True, root
        rec.state, rec.confidence, rec.since = "idle", "hook", (now - timedelta(hours=1)).isoformat()
        entry = {"id": "TD-002", "title": "x", "owner": "grinder", "kind": "build", "pickable": "yes"}
        agent._repos[root] = {"name": "repo", "root": root, "ledger": {"entries": [entry]}}
        rec.balance_refused = {"at": now.isoformat(), "ref": "TD-900"}
        _mark(agent)

        await agent._idle_nudge(rec, now)
        assert sent == [] and rec.nudged_at is None
        rec.lane, rec.out_of_work = ["free-pick"], {"at": now.isoformat(), "why": "x"}
        rec.lane_seen = {"at": now.isoformat(), "ids": []}
        await agent._lane_news(rec, now)
        assert notes == [] and rec.lane_seen["ids"] == [], "untold, so it is told once the line clears"

        agent._host_rec["teams"].pop("grind")  # the mark went: the field alone holds nothing back
        await agent._lane_news(rec, now)
        assert len(notes) == 1 and "TD-002" in notes[0][1]
        rec.lane, rec.out_of_work, rec.lane_seen = ["TD-001"], None, None
        await agent._idle_nudge(rec, now)
        assert len(sent) == 1 and "TD-001" in sent[0][1]


# -- the notes (slice 3) ----------------------------------------------------------------------------


def _system(entries) -> list[str]:
    return [e.text for e in entries if e.from_ == "system"]


async def test_a_crossing_and_its_clearing_tell_the_manager_and_the_person_once_and_ring_the_refused(agent, tmp_path):
    """Design §6 *Balance*, *Who is told*: a crossing sends one `system` note to the controller the
    members share and one to the person; a mark still standing sends nothing more; the clearing sends
    the pair again and rings each member refused while it stood, once, removing `balance_refused`."""
    await park_ticks(agent)
    root = str(tmp_path / "repo")
    now = datetime.now(UTC)
    async with LocalClient() as person:
        await person.call("set_settings", teams={"grind": {"balance": {"prs": 8}}})
        mgr = await _member(person, tmp_path, "mgr", unattended=True)
        g1 = await _member(person, tmp_path, "g1", unattended=True)
        g2 = await _member(person, tmp_path, "g2", unattended=True)
        for g in (g1, g2):
            agent.sessions[g].controllers = [mgr]
            agent.sessions[g].repo = root
        agent._repos[root] = {"name": "repo", **_prs(9)}

        await agent._balance_marks(now)
        (to_mgr,) = _system(agent.sessions[mgr].inbox)
        assert to_mgr.startswith("grind is over its line since ") and "9 open PRs, the line is 8" in to_mgr
        assert _system(agent.person_inbox) == [to_mgr]
        assert not _system(agent.sessions[g1].inbox), "a member is told by its refusal, not by a note"

        agent._repos[root] = {"name": "repo", **_prs(12)}
        await agent._balance_marks(now + timedelta(minutes=30))  # still over: nothing more
        assert len(_system(agent.sessions[mgr].inbox)) == len(_system(agent.person_inbox)) == 1

        with pytest.raises(AgentError, match="over its line"):
            async with LocalClient(caller=g1) as me:
                await me.call("progress", id=g1, ref="TD-900", status="claimed")
        assert agent.sessions[g1].balance_refused

        agent._repos[root] = {"name": "repo", **_prs(8)}
        await agent._balance_marks(now + timedelta(minutes=40))
        cleared = _system(agent.sessions[mgr].inbox)[-1]
        assert (
            cleared.startswith("grind is under its line again (over since ") and len(_system(agent.person_inbox)) == 2
        )
        assert _system(agent.sessions[g1].inbox) == [balance.CLEAR] and agent.sessions[g1].balance_refused is None
        assert not _system(agent.sessions[g2].inbox), "only a member that was refused is rung"
        saved = json.loads((paths.sessions_dir() / f"{g1}.json").read_text())
        assert saved.get("balance_refused") is None

        await agent._balance_marks(now + timedelta(minutes=41))  # nothing rings twice
        assert len(_system(agent.sessions[g1].inbox)) == 1 and len(_system(agent.person_inbox)) == 2


async def test_a_mark_that_flaps_inside_ten_minutes_tells_one_pair(agent, tmp_path):
    """A crossing inside `FLAP` of the last one told waits until it has stood that long: a mark that
    comes and goes tells once, and one that comes back to stay is told once the window is over."""
    await park_ticks(agent)
    root = str(tmp_path / "repo")
    now = datetime.now(UTC)
    async with LocalClient() as person:
        await person.call("set_settings", teams={"grind": {"balance": {"prs": 8}}})
        g = await _member(person, tmp_path, "g1", unattended=True)
        agent.sessions[g].repo = root

        def told() -> list[str]:
            return _system(agent.person_inbox)

        for minute, n in ((0, 9), (2, 8), (4, 9), (6, 8), (8, 9)):
            agent._repos[root] = {"name": "repo", **_prs(n)}
            await agent._balance_marks(now + timedelta(minutes=minute))
        assert [t.split(" since")[0] for t in told()] == [
            "grind is over its line",
            "grind is under its line again (over",
        ]
        assert "balance" in agent._host_rec["teams"]["grind"], "the mark itself is not held back"

        await agent._balance_marks(now + timedelta(minutes=10))  # eight minutes from the clearing's note: held
        assert len(told()) == 2
        await agent._balance_marks(now + timedelta(minutes=12))  # ten from the last note: told now
        assert len(told()) == 3 and told()[2].startswith("grind is over its line")

        # a mark that stood for hours and clears: a crossing a minute later is held from the clearing's note
        agent._repos[root] = {"name": "repo", **_prs(8)}
        await agent._balance_marks(now + timedelta(hours=3))
        agent._repos[root] = {"name": "repo", **_prs(9)}
        await agent._balance_marks(now + timedelta(hours=3, minutes=1))
        assert len(told()) == 4 and told()[3].startswith("grind is under its line again")


async def test_a_told_crossing_survives_a_restart_and_a_wound_down_team_tells_nothing(agent, tmp_path):
    await park_ticks(agent)
    root = str(tmp_path / "repo")
    now = datetime.now(UTC)
    async with LocalClient() as person:
        await person.call("set_settings", teams={"grind": {"balance": {"prs": 8}}})
        g = await _member(person, tmp_path, "g1", unattended=True)
        agent.sessions[g].repo = root
        agent._repos[root] = {"name": "repo", **_prs(9)}
        await agent._balance_marks(now)
        assert json.loads(paths.host_file().read_text())["teams"]["grind"]["balance_told"]["state"] == "over"
        assert len(_system(agent.person_inbox)) == 1

        await person.call("kill", id=g)
        agent.sessions[g].state = "exited"
        await agent._balance_marks(now + timedelta(minutes=1))
        assert agent._host_rec["teams"] == {} and len(_system(agent.person_inbox)) == 1


async def test_the_clearing_rings_a_nodes_refused_member_through_its_record_at_the_home(agent):
    """A node forwards `progress` to the home (a report), so the home refuses a node's member and keeps
    `balance_refused` on its replica; the clearing rings it there, under its address, as any mail."""
    r = Session(id="ao-x-w", name="w", kind="agent", adapter="shell", dir="/tmp/x", host="laptop", team="grind")
    r.state, r.unattended = "idle", True
    r.balance_refused = {"at": datetime.now(UTC).isoformat(), "ref": "TD-900"}
    r.controllers = ["mgr"]  # the node's own form: the home reads `mgr@laptop`
    agent.remote["laptop"] = {r.id: r}
    try:
        assert agent._balance_leads([r]) == ["mgr@laptop"]
        _mark(agent)
        r.balance_refused = None
        assert agent._balance_refusal(r, "TD-901", caller="ao-x-w@laptop")  # as `_forwarded` names the caller
        assert r.balance_refused["ref"] == "TD-901", "the member's own word, from its node, is kept"
        agent._host_rec["teams"].pop("grind")
        agent._balance_ring("grind")
        assert _system(r.inbox) == [balance.CLEAR] and r.balance_refused is None
        assert json.loads((paths.remote_dir("laptop") / "ao-x-w.json").read_text()).get("balance_refused") is None
    finally:
        agent.remote.pop("laptop", None)
