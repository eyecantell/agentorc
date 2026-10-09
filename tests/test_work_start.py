"""TD-227 slice 4, design §6 rule 8 under `on_work: start`: a wound-down team whose lanes gained work
is started again by replaying its records' launch records, `why: work` with the ids, the lead first;
five bounds read first (the fifth the team's balance line, TD-239) hold the start back and leave the
mark with `held`, which draws the row."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks

from sessionorc import paths, work
from sessionorc import settings as settings_mod
from sessionorc.agent_common import WORK_EARLY, WORK_SETTLE
from sessionorc.client import LocalClient
from sessionorc.models import Session

DECLARED = {"at": "2026-09-28T06:56:00Z", "why": "nothing pickable"}


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _e(id: str) -> dict:
    return {"id": id, "title": id, "priority": "medium", "owner": "grinder", "kind": "build", "pickable": "yes"}


def _rec(name: str, **kw) -> Session:
    base = dict(
        id=f"ao-t-{name}", name=name, kind="interactive", adapter="shell", dir="/tmp/x", state="closed",
        team="g", unattended=True, supervised=True, out_of_work=DECLARED, lane=["free-pick"],
        lane_seen={"at": "x", "ids": ["TD-001"]}, profile="grind",
    )  # fmt: skip
    return Session(**{**base, **kw})


def _launch(sid: str) -> None:
    paths.launch_dir().mkdir(parents=True, exist_ok=True)
    (paths.launch_dir() / f"{sid}.json").write_text(json.dumps({"name": sid}), encoding="utf-8")


class _Replays:
    """`_replay` stood in for: what the rule would start, in order, with its arguments."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, dict]] = []

    async def __call__(self, s, why, *, mark=None, **extra):
        self.calls.append((s.name, why, mark or {}, extra))


async def _settled(agent, tmp_path, *recs: Session, launch: bool = True):
    """A wound-down team `g` whose grinder's lane gained TD-002, past the settle: returns the instant."""
    repo = str(tmp_path)
    agent._repos[repo] = {"name": "r", "root": repo, "ledger": {"entries": [_e("TD-001"), _e("TD-002")]}}
    for r in recs:
        r.repo, r.host = repo, agent.host
        agent.sessions[r.id] = r
        if launch:
            _launch(r.id)
    now = datetime.now(UTC)
    await agent._work_marks(now)
    return now + WORK_SETTLE


def _team_rec(agent) -> dict:
    return (agent._host_rec.get("teams") or {}).get("g") or {}


async def test_start_replays_the_team_the_lead_first_and_counts_one_start(agent, tmp_path, monkeypatch):
    await park_ticks(agent)
    settings_mod.save({"teams": {"g": {"on_work": "start"}}})
    replays = _Replays()
    monkeypatch.setattr(agent, "_replay", replays)
    later = await _settled(
        agent, tmp_path,
        _rec("grinder-ao-1", controllers=["ao-t-manager-ao"]),
        _rec("manager-ao", lane=["TD-900"]),
        _rec("techlead-ao", seat={"trigger": "asks"}, out_of_work=None, controllers=["ao-t-manager-ao"]),
        _rec("paul-ao", unattended=False, out_of_work=None, state="working"),
    )  # fmt: skip
    assert replays.calls == [], "not before the settle"
    await agent._work_marks(later)
    assert [c[0] for c in replays.calls] == ["manager-ao", "grinder-ao-1", "techlead-ao"], "the lead first; no person"
    rec = _team_rec(agent)
    # every entry of the one start carries its instant and how many records it set out to replay,
    # so a client counts *n of m* when some replays fail (the techlead's read of #792)
    about = {"ids": ["TD-002"], "start": rec["work_started"][-1], "of": 3}
    assert {c[1] for c in replays.calls} == {"work"} and all(c[2] == about for c in replays.calls)
    assert all(c[3] == {"keep_mail": True} for c in replays.calls), "every record keeps its mail (TD-274)"
    rec = _team_rec(agent)
    assert "work_waiting" not in rec and len(rec["work_started"]) == 1
    assert agent.host_store.load()["teams"]["g"]["work_started"] == rec["work_started"], "saved"


async def test_each_bound_holds_the_start_back_and_says_which(agent, tmp_path, monkeypatch):
    await park_ticks(agent)
    replays = _Replays()
    monkeypatch.setattr(agent, "_replay", replays)
    later = await _settled(agent, tmp_path, _rec("manager-ao", lane=["TD-900"]), _rec("grinder-ao-1"))
    teams = agent._host_rec.setdefault("teams", {})

    async def held(conf: dict, started: list[str] | None = None) -> dict | None:
        settings_mod.save({"teams": {"g": {"on_work": "start", **conf}}})
        teams.setdefault("g", {})["work_started"] = list(started or [])
        await agent._work_marks(later)
        return (_team_rec(agent).get("work_waiting") or {}).get("held")

    past = _iso(later - timedelta(minutes=1))
    assert await held({"until": past}) == {"why": "until", "until": past}
    day = [_iso(later - timedelta(hours=h)) for h in (20, 10, 5)]
    assert await held({}, day) == {"why": "day", "count": 3}
    assert await held({}, [_iso(later - timedelta(hours=30)), *day]) == {"why": "day", "count": 3}
    assert _team_rec(agent)["work_started"] == day, "a start older than the day is dropped"
    early = _iso(later - WORK_EARLY + timedelta(minutes=1))
    assert await held({}, [early]) == {"why": "early", "started": early}
    monkeypatch.setattr(agent, "_profile_over", lambda profile, now, team="": {"label": "week", "resets": None})
    assert await held({}) == {"why": "usage", "profile": "grind"}, "a reading with no reset names none"
    resets = _iso(later + timedelta(hours=3))
    monkeypatch.setattr(agent, "_profile_over", lambda profile, now, team="": {"label": "week", "resets": resets})
    assert await held({}) == {"why": "usage", "profile": "grind", "resets": resets}, "when the hold lifts"
    assert replays.calls == [], "nothing started while a bound held"
    assert _team_rec(agent)["work_waiting"]["members"] == {"grinder-ao-1": ["TD-002"]}, "the row of ask, in its place"

    # the person turns it back to `ask`: the row stands, with no start to hold back
    settings_mod.save({"teams": {"g": {"on_work": "ask"}}})
    await agent._work_marks(later)
    assert "held" not in _team_rec(agent)["work_waiting"] and replays.calls == []

    # the bound lifts: the next tick starts the team
    monkeypatch.setattr(agent, "_profile_over", lambda profile, now, team="": None)
    settings_mod.save({"teams": {"g": {"on_work": "start"}}})
    teams["g"]["work_started"] = [_iso(later - WORK_EARLY - timedelta(minutes=1))]
    await agent._work_marks(later)
    assert [c[0] for c in replays.calls] == ["grinder-ao-1", "manager-ao"]
    assert "work_waiting" not in _team_rec(agent) and len(_team_rec(agent)["work_started"]) == 2


async def test_no_launch_record_no_start(agent, tmp_path, monkeypatch):
    await park_ticks(agent)
    settings_mod.save({"teams": {"g": {"on_work": "start"}}})
    replays = _Replays()
    monkeypatch.setattr(agent, "_replay", replays)
    later = await _settled(agent, tmp_path, _rec("grinder-ao-1"), launch=False)
    await agent._work_marks(later)
    assert replays.calls == [] and _team_rec(agent)["work_waiting"]["held"] == {"why": "nothing"}
    assert "work_started" not in _team_rec(agent)


async def test_a_seats_fills_do_not_count_toward_the_ceiling(agent, tmp_path, monkeypatch):
    """The techlead's read of #792: rule 3 never counts a fill toward `RESTART_CEILING`, so a seat
    filled three times in the window is still started; three restarts of another kind leave it out."""
    await park_ticks(agent)
    settings_mod.save({"teams": {"g": {"on_work": "start"}}})
    replays = _Replays()
    monkeypatch.setattr(agent, "_replay", replays)
    now = _iso(datetime.now(UTC))
    fills = [{"at": now, "why": "fill"} for _ in range(3)]
    later = await _settled(
        agent, tmp_path,
        _rec("grinder-ao-1"),
        _rec("techlead-ao", seat={"trigger": "asks"}, out_of_work=None, restarts=fills),
        _rec("auditor-ao", seat={"trigger": "asks"}, out_of_work=None, restarts=[{"at": now, "why": "work"}] * 3),
    )  # fmt: skip
    await agent._work_marks(later)
    assert sorted(c[0] for c in replays.calls) == ["grinder-ao-1", "techlead-ao"]


async def test_the_fifth_bound_reads_every_repo_the_team_names(agent, tmp_path, monkeypatch):
    """The techlead's read of #799: as for a live team, either repo's crossing counts — a start into a
    team whose manager's repo is over the line would meet the mark it then raises."""
    await park_ticks(agent)
    replays = _Replays()
    monkeypatch.setattr(agent, "_replay", replays)
    lead = _rec("manager-ao", lane=["TD-900"])
    later = await _settled(agent, tmp_path, lead, _rec("grinder-ao-1"))
    other = str(tmp_path / "b")
    born = _iso(later - timedelta(hours=1))
    agent._repos[str(tmp_path)]["prs"] = {"open": [], "at": born}
    agent._repos[other] = {
        "name": "b",
        "root": other,
        "prs": {"open": [{"number": i, "created": born} for i in range(3)]},
    }
    lead.repo = other
    settings_mod.save({"teams": {"g": {"on_work": "start", "balance": {"prs": 2}}}})
    await agent._work_marks(later)
    held = _team_rec(agent)["work_waiting"]["held"]
    assert held == {"why": "balance", "repo": other, "crossed": [{"line": "prs", "value": 3, "limit": 2}]}
    assert replays.calls == []


async def test_a_member_behind_a_down_link_holds_the_start_as_link(agent, tmp_path, monkeypatch):
    """The techlead's read of #792: the start waits for the link, and says so, so the row is drawn."""
    await park_ticks(agent)
    settings_mod.save({"teams": {"g": {"on_work": "start"}}})
    replays = _Replays()
    monkeypatch.setattr(agent, "_replay", replays)
    far = _rec("grinder-ao-2")
    later = await _settled(agent, tmp_path, _rec("grinder-ao-1"), far)
    far.host = "far-node"
    _launch(agent._address(far))  # a node's record is launched under its address at the home
    await agent._work_marks(later)
    held = {"why": "link", "host": "far-node", "hosts": ["far-node"]}
    assert replays.calls == [] and _team_rec(agent)["work_waiting"]["held"] == held
    assert "work_started" not in _team_rec(agent)
    near = next(r for r in agent.sessions.values() if r.name == "grinder-ao-1")
    near.host = "other-node"  # two links down: both named, in replay order (the techlead's read of #797)
    _launch(agent._address(near))
    await agent._work_marks(later)
    held = {"why": "link", "host": "other-node", "hosts": ["other-node", "far-node"]}
    assert replays.calls == [] and _team_rec(agent)["work_waiting"]["held"] == held
    near.host = agent.host
    far.host = agent.host  # the link is back: the next tick starts the team
    await agent._work_marks(later)
    assert sorted(c[0] for c in replays.calls) == ["grinder-ao-1", "grinder-ao-2"]


@pytest.mark.integration
async def test_a_start_by_the_rule_replays_the_records_under_their_names(agent, tmp_path):
    await park_ticks(agent)
    settings_mod.save({"teams": {"g": {"on_work": "start"}}})
    async with LocalClient() as person:
        made = {}
        for name in ("manager-ao", "grinder-ao-1"):
            made[name] = (
                await person.call(
                    "create",
                    name=name,
                    dir=str(tmp_path),
                    adapter="shell",
                    argv=["bash", "--norc", "--noprofile"],
                    unattended=True,
                    supervised=True,
                    team="g",
                    lane=["free-pick"],
                    controllers=[made["manager-ao"]] if made else [],
                )  # fmt: skip
            )["id"]
        repo = str(tmp_path)
        agent._repos[repo] = {"name": "r", "root": repo, "ledger": {"entries": [_e("TD-001"), _e("TD-002")]}}
        for sid in made.values():
            rec = agent.sessions[sid]
            rec.state, rec.pane, rec.out_of_work, rec.repo = "exited", True, DECLARED, repo
            rec.lane_seen = {"at": "x", "ids": ["TD-001"]}
            agent.store.save(rec)
        old = {sid: agent.sessions[sid] for sid in made.values()}
        now = datetime.now(UTC)
        await agent._work_marks(now)
        await agent._work_marks(now + WORK_SETTLE)
        for sid in made.values():
            new = agent.sessions[sid]
            assert new is not old[sid] and new.state not in ("exited", "closed"), "superseded in place (§4.1)"
            assert [(r["why"], r["ids"]) for r in new.restarts] == [("work", ["TD-002"])]
            assert new.out_of_work is None and new.lane_seen is None, "the records it makes begin afresh"
        assert "work_waiting" not in _team_rec(agent) and len(_team_rec(agent)["work_started"]) == 1
        for sid in made.values():
            await person.call("kill", id=sid)


async def test_a_repo_over_the_teams_balance_line_holds_the_start_as_the_fifth_bound(agent, tmp_path, monkeypatch):
    """§6 *Balance*: the mark goes with the team's last live member, so rule 8 reads the lines itself —
    against every registry root the team's records name, and the `review` line against the queue its
    ended seats keep."""
    await park_ticks(agent)
    replays = _Replays()
    monkeypatch.setattr(agent, "_replay", replays)
    seat = _rec("techlead-ao", seat={"trigger": "asks"}, out_of_work=None)
    later = await _settled(agent, tmp_path, _rec("manager-ao", lane=["TD-900"]), _rec("grinder-ao-1"), seat)
    repo = str(tmp_path)
    born = _iso(later - timedelta(hours=1))
    agent._repos[repo]["prs"] = {"open": [{"number": 700 + i, "created": born} for i in range(3)], "at": born}

    async def held(balance: dict) -> dict | None:
        settings_mod.save({"teams": {"g": {"on_work": "start", "balance": balance}}})
        await agent._work_marks(later)
        return (_team_rec(agent).get("work_waiting") or {}).get("held")

    got = await held({"prs": 2})
    assert got == {"why": "balance", "repo": repo, "crossed": [{"line": "prs", "value": 3, "limit": 2}]}
    prs = agent._repos[repo]["prs"]
    agent._repos[repo]["prs"] = {"error": "gh: offline"}
    assert await held({"prs": 2}) == got and replays.calls == [], "a reading that cannot be told lifts no hold"
    agent._repos[repo]["prs"] = {**prs, "open": [*prs["open"], {"number": 799, "created": born}]}
    saved = agent._host_rec["teams"]["g"]["work_waiting"]
    assert await held({"prs": 2}) == got, "the same line crossed: the hold stands with the crossing's numbers"
    assert agent._host_rec["teams"]["g"]["work_waiting"] is saved, "and the tick writes nothing"
    agent._repos[repo]["prs"] = {"error": "gh: offline"}
    for k in list(_team_rec(agent)):
        _team_rec(agent).pop(k)
    await agent._work_marks(later - WORK_SETTLE)
    assert await held({"prs": 2}) is None, "a reading that cannot be told writes no new hold"
    assert [c[0] for c in replays.calls] == ["grinder-ao-1", "manager-ao", "techlead-ao"], "the team started"
    replays.calls.clear()

    # the review line: the ended seat's queue, three hours old against the two-hour bound
    for k in list(_team_rec(agent)):
        _team_rec(agent).pop(k)
    await agent._work_marks(later - WORK_SETTLE)
    waited = _iso(later - timedelta(hours=3))
    monkeypatch.setattr(seat, "prs_waiting", lambda home=None: {"n": 1, "oldest": waited})
    got = await held({"review": True})
    assert got["why"] == "balance" and got["crossed"][0]["line"] == "review" and replays.calls == []


async def test_a_balance_hold_another_bound_displaces_is_kept_beside_it(agent, tmp_path, monkeypatch):
    """§6 rule 8 (TD-330 (1), #799's read): a usage hold that takes `held` from a standing balance hold
    keeps it beside itself, so the reading failing when the usage hold lifts holds the start as before,
    rather than starting a team that was over its line; a reading under the line then lifts it."""
    await park_ticks(agent)
    replays = _Replays()
    monkeypatch.setattr(agent, "_replay", replays)
    later = await _settled(agent, tmp_path, _rec("manager-ao", lane=["TD-900"]), _rec("grinder-ao-1"))
    repo = str(tmp_path)
    born = _iso(later - timedelta(hours=1))
    prs = {"open": [{"number": 700 + i, "created": born} for i in range(3)], "at": born}
    agent._repos[repo]["prs"] = prs
    settings_mod.save({"teams": {"g": {"on_work": "start", "balance": {"prs": 2}}}})

    async def held() -> dict | None:
        await agent._work_marks(later)
        return (_team_rec(agent).get("work_waiting") or {}).get("held")

    over = await held()
    assert over["why"] == "balance"
    monkeypatch.setattr(agent, "_profile_over", lambda profile, now, team="": {"resets": "2026-10-06T00:00:00Z"})
    got = await held()
    assert got["why"] == "usage" and got["balance"] == over, "the balance hold is kept beside the usage one"
    assert await held() == got, "and stands unchanged while the usage hold does"
    monkeypatch.setattr(agent, "_profile_over", lambda profile, now, team="": None)
    agent._repos[repo]["prs"] = {"error": "gh: offline"}
    assert await held() == over and replays.calls == [], "a reading that cannot be told lifts no hold"
    agent._repos[repo]["prs"] = {**prs, "open": prs["open"][:1]}
    assert await held() is None
    assert [c[0] for c in replays.calls] == ["grinder-ao-1", "manager-ao"], "under the line: the team started"


async def test_a_member_its_flow_sat_out_is_not_replayed_by_a_start(agent, tmp_path, monkeypatch):
    """§4.9c *Switching*, the techlead's read of #1101: a sat-out member did not end by the team's own
    ending, so a start by rule 8 passes over it — not replayed and not counted in `of` — though the
    team reads wound down with it."""
    await park_ticks(agent)
    settings_mod.save({"teams": {"g": {"on_work": "start"}}})
    replays = _Replays()
    monkeypatch.setattr(agent, "_replay", replays)
    sat = {"why": "sit_out", "closed_at": "2026-10-05T07:00:00Z"}
    later = await _settled(
        agent, tmp_path,
        _rec("grinder-ao-1"),
        _rec("designer-ao", out_of_work=None, closed_for=sat, sit_out={"at": "2026-10-05T06:00:00Z"}),
    )  # fmt: skip
    await agent._work_marks(later)
    assert [c[0] for c in replays.calls] == ["grinder-ao-1"] and replays.calls[0][2]["of"] == 1


# -- a member that finished while its team runs on (§6 rule 8, TD-457, TD-466) ---------------------


def _running(agent, tmp_path, **grinder):
    """Team `g` running on its anchor seat; its grinder closed after declaring, its lane gaining TD-002."""
    return (
        _rec("grinder-ao-1", controllers=["ao-t-manager-ao"], **grinder),
        _rec("grinder-ao-2", controllers=["ao-t-manager-ao"]),  # finished too, its lane gained nothing new
        _rec("manager-ao", lane=["TD-900"], state="closed"),
        _rec("anchor-ao", seat={"trigger": "work"}, out_of_work=None, state="working", lane=["TD-900"]),
    )


@pytest.mark.parametrize("conf", [{"on_work": "start"}, {}], ids=["start", "the-default"])
async def test_a_finished_member_of_a_running_team_is_replayed_alone(agent, tmp_path, monkeypatch, conf):
    """A crew member closed after declaring, its team running on a seat, is rule 8's news: the mark
    names it after the settle, and `start` replays it alone, `of` counting the members replayed. A team
    with no `on_work` key has `start` (TD-457, TD-466)."""
    await park_ticks(agent)
    settings_mod.save({"teams": {"g": conf}} if conf else {})
    replays = _Replays()
    monkeypatch.setattr(agent, "_replay", replays)
    recs = _running(agent, tmp_path)
    recs[1].lane_seen = {"at": "x", "ids": ["TD-001", "TD-002"]}
    later = await _settled(agent, tmp_path, *recs)
    assert replays.calls == [] and "work_waiting" not in _team_rec(agent), "not before the settle"
    await agent._work_marks(later)
    rec = _team_rec(agent)
    assert [c[0] for c in replays.calls] == ["grinder-ao-1"], "the finished member alone, never the team"
    assert replays.calls[0][1] == "work" and replays.calls[0][2] == {
        "ids": ["TD-002"],
        "start": rec["work_started"][-1],
        "of": 1,
    }
    assert "work_waiting" not in rec and len(rec["work_started"]) == 1


async def test_a_running_teams_mark_names_its_member_and_goes_when_it_is_live(agent, tmp_path, monkeypatch):
    """Under `ask` the mark names the finished member; it is removed once that member is live again.
    One a person closed is *stopped* and left alone, as is a seat or a live member."""
    await park_ticks(agent)
    settings_mod.save({"teams": {"g": {"on_work": "ask"}}})
    recs = _running(agent, tmp_path)
    later = await _settled(agent, tmp_path, *recs)
    await agent._work_marks(later)
    mark = _team_rec(agent)["work_waiting"]
    assert mark["members"] == {"grinder-ao-1": ["TD-002"], "grinder-ao-2": ["TD-002"]} and "held" not in mark
    for r in recs[:2]:
        r.state = "idle"
    await agent._work_marks(later + timedelta(seconds=1))
    assert "work_waiting" not in _team_rec(agent), "every named member live again"
    recs[0].state, recs[0].closer = "closed", {"by": "person", "why": None, "at": "x"}
    await agent._work_marks(later + timedelta(seconds=2))
    await agent._work_marks(later + WORK_SETTLE + timedelta(seconds=3))
    assert "work_waiting" not in _team_rec(agent), "a member a person closed is stopped, not finished"


async def test_work_start_replays_the_named_members_and_a_bound_refuses_it(agent, tmp_path, monkeypatch):
    """`work_start {team}`, the row's Start for a team that runs on: a person's own, the home's alone;
    the members the mark names replayed under the five bounds, and a holding bound returned as `held`."""
    await park_ticks(agent)
    settings_mod.save({"teams": {"g": {"on_work": "ask"}}})
    replays = _Replays()
    monkeypatch.setattr(agent, "_replay", replays)
    recs = _running(agent, tmp_path)
    recs[1].lane_seen = {"at": "x", "ids": ["TD-001", "TD-002"]}
    later = await _settled(agent, tmp_path, *recs)
    await agent._work_marks(later)
    assert _team_rec(agent)["work_waiting"]["members"] == {"grinder-ao-1": ["TD-002"]}
    async with LocalClient(caller=recs[3].id) as seat:
        with pytest.raises(Exception, match="person"):
            await seat.call("work_start", team="g")
    async with LocalClient() as person:
        assert (await person.call("work_start", team="nobody"))["started"] is False
        got = await person.call("work_start", team="g")
        assert got["started"] is True and got["ids"] == ["TD-002"] and got["held"] is None
        assert [c[0] for c in replays.calls] == ["grinder-ao-1"] and "work_waiting" not in _team_rec(agent)
        # inside WORK_EARLY of that start, the next one is held back and says why
        await agent._work_marks(later + timedelta(seconds=1))
        replays.calls.clear()
        recs[1].lane_seen = {"at": "x", "ids": ["TD-001"]}
        await agent._work_marks(later + timedelta(seconds=2))
        await agent._work_marks(later + WORK_SETTLE + timedelta(seconds=3))
        assert _team_rec(agent)["work_waiting"]["members"] == {"grinder-ao-1": ["TD-002"], "grinder-ao-2": ["TD-002"]}
        got = await person.call("work_start", team="g")
        assert got["started"] is False and got["held"]["why"] == "early" and replays.calls == []
        assert _team_rec(agent)["work_waiting"]["held"]["why"] == "early"


async def test_a_killed_member_and_a_stopped_team_are_not_read_member_by_member(agent, tmp_path, monkeypatch):
    """A kill destroys the pane and writes no closer: a killed member is never read as finished. A team
    with nobody live that is not wound down (a member that never declared) is *stopped*, left alone."""
    await park_ticks(agent)
    settings_mod.save({"teams": {"g": {"on_work": "ask"}}})
    recs = _running(agent, tmp_path, state="exited", pane=False)
    recs[1].state = "idle"
    later = await _settled(agent, tmp_path, *recs)
    await agent._work_marks(later)
    assert "work_waiting" not in _team_rec(agent), "killed, not finished"
    recs[0].pane = True  # exited by itself after declaring: finished
    await agent._work_marks(later + timedelta(seconds=1))
    await agent._work_marks(later + WORK_SETTLE + timedelta(seconds=2))
    assert _team_rec(agent)["work_waiting"]["members"] == {"grinder-ao-1": ["TD-002"]}
    # nobody live, grinder-ao-2 never declared: stopped, so the mark goes
    recs[1].state, recs[1].out_of_work, recs[3].state = "exited", None, "closed"
    await agent._work_marks(later + WORK_SETTLE + timedelta(seconds=3))
    assert "work_waiting" not in _team_rec(agent)


@pytest.mark.parametrize(
    ("guard", "odd"),
    [
        ("a seat", {"seat": {"trigger": "work"}}),
        ("the manager", None),
        ("one its flow sat out", {"closed_for": {"why": "sit_out", "at": "x"}}),
        ("a record a successor took over", {"superseded_by": "ao-t-successor"}),
    ],
)
async def test_finished_alone_passes_over_each_record_that_is_not_a_finished_member(
    agent, tmp_path, monkeypatch, guard, odd
):
    """Each of `finished_alone`'s exclusions (TD-475): a record otherwise finished — closed after
    declaring, its lane gaining TD-002 — that is a seat, the manager, sat out or superseded is neither
    read as finished nor named by the mark, while the team's finished grinder beside it is."""
    await park_ticks(agent)
    settings_mod.save({"teams": {"g": {"on_work": "ask"}}})
    recs = list(_running(agent, tmp_path))
    recs[1].lane_seen = {"at": "x", "ids": ["TD-001", "TD-002"]}
    manager = recs[2]
    manager.capabilities = ["control"]
    if odd is None:
        manager.lane, odd_rec = ["free-pick"], manager  # its lane gains TD-002 as the grinder's does
    else:
        odd_rec = _rec("grinder-ao-3", controllers=["ao-t-manager-ao"], **odd)
        recs.append(odd_rec)
    later = await _settled(agent, tmp_path, *recs)
    assert work.manager_of(recs) is manager
    assert [r.name for r in work.finished_alone(recs)] == ["grinder-ao-1", "grinder-ao-2"], guard
    await agent._work_marks(later)
    assert _team_rec(agent)["work_waiting"]["members"] == {"grinder-ao-1": ["TD-002"]}, guard
