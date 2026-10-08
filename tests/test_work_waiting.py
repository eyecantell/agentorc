"""TD-227 slice 2, design §6 rule 8 (*Work for a team that wound down*): the home reads a team as
wound down from its records, as the team card does, and writes `work_waiting` on its `host` record
once a member's lane holds ids its `lane_seen` does not and `WORK_SETTLE` has passed; `on_work`
says whether it is written at all; `clear_work` is Dismiss's half."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks

from agentorc import teamrun
from sessionorc import modes, work
from sessionorc import settings as settings_mod
from sessionorc.agent_common import WORK_SETTLE
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import Session

DECLARED = {"at": "2026-09-28T06:56:00Z", "why": "nothing pickable"}


def _e(id: str) -> dict:
    return {"id": id, "title": id, "priority": "medium", "owner": "grinder", "kind": "build", "pickable": "yes"}


def _rec(name: str, team: str = "g", **kw) -> Session:
    base = dict(
        id=f"ao-t-{name}", name=name, kind="interactive", adapter="shell", dir="/tmp/x", state="closed",
        team=team, unattended=True, supervised=True, out_of_work=DECLARED,
    )  # fmt: skip
    return Session(**{**base, **kw})


def _team(agent, repo: str, *recs: Session) -> list[Session]:
    for r in recs:
        r.repo = repo
        agent.sessions[r.id] = r
    return list(recs)


def test_the_home_reads_wound_down_as_the_card_does():
    """One reading for both (TD-227 slice 2): a seat by the record's `seat` field at the home and by
    the definition's name on the card, a person's session left out by both."""
    seat = _rec("techlead-ao", seat={"role": "techlead"}, out_of_work=None, state="idle")
    person = _rec("paul-ao", unattended=False, out_of_work=None, state="working")
    cases = [
        [_rec("manager-ao"), _rec("grinder-ao-1"), _rec("techlead-ao", seat={"role": "techlead"}, out_of_work=None)],
        [_rec("manager-ao"), _rec("grinder-ao-1", out_of_work=None), seat],
        [_rec("manager-ao"), _rec("grinder-ao-1", state="idle"), person],
        [_rec("manager-ao"), _rec("grinder-ao-1"), person],
    ]
    for recs in cases:
        dicts = [r.to_dict() for r in recs]
        crew = [d for d in dicts if not teamrun.persons(d)]
        live = [d for d in crew if d["state"] not in teamrun.DEAD]
        card = None if live else teamrun.wound_down(crew, {"techlead-ao"})
        assert work.team_wound_down(recs) == card
    assert work.team_wound_down(cases[0]) == DECLARED["at"] and work.team_wound_down(cases[3]) == DECLARED["at"]
    assert teamrun.wound_down is work.wound_down


async def test_a_wound_down_teams_lane_news_waits_the_settle_then_is_written(agent, tmp_path, monkeypatch):
    await park_ticks(agent)
    replays: list[tuple] = []

    async def replay(*a, **k):
        replays.append(a)

    monkeypatch.setattr(agent, "_replay", replay)  # `on_work` unset is `ask`: the row, never a start
    now = datetime.now(UTC)
    repo = str(tmp_path)
    agent._repos[repo] = {"name": "r", "root": repo, "ledger": {"entries": [_e("TD-001"), _e("TD-002"), _e("TD-003")]}}
    seen = {"at": "x", "ids": ["TD-001"]}
    recs = _team(
        agent, repo,
        _rec("manager-ao", lane=["TD-900"], lane_seen={"at": "x", "ids": []}),
        _rec("grinder-ao-1", lane=["free-pick"], lane_seen=dict(seen)),
        _rec("techlead-ao", lane=["free-pick"], lane_seen=None, seat={"role": "techlead"}, out_of_work=None),
        _rec("paul-ao", lane=["free-pick"], lane_seen={"at": "x", "ids": []}, unattended=False, state="working"),
    )  # fmt: skip
    await agent._work_marks(now)
    assert "work_waiting" not in (agent._host_rec.get("teams") or {}).get("g", {}), "not before the settle"
    await agent._work_marks(now + WORK_SETTLE - timedelta(seconds=1))
    assert "work_waiting" not in (agent._host_rec.get("teams") or {}).get("g", {})
    await agent._work_marks(now + WORK_SETTLE)
    mark = agent._host_rec["teams"]["g"]["work_waiting"]
    assert mark["repo"] == repo and mark["members"] == {"grinder-ao-1": ["TD-002", "TD-003"]}
    at = mark["at"]
    assert agent.host_store.load()["teams"]["g"]["work_waiting"] == mark, "saved on the host record"
    assert replays == [], "`ask` starts nothing"

    # a later entry settles again, and the mark keeps its age meanwhile
    agent._repos[repo]["ledger"]["entries"].append(_e("TD-004"))
    await agent._work_marks(now + WORK_SETTLE + timedelta(minutes=1))
    assert agent._host_rec["teams"]["g"]["work_waiting"]["members"] == {"grinder-ao-1": ["TD-002", "TD-003"]}
    await agent._work_marks(now + 2 * WORK_SETTLE + timedelta(minutes=1))
    mark = agent._host_rec["teams"]["g"]["work_waiting"]
    assert mark["members"] == {"grinder-ao-1": ["TD-002", "TD-003", "TD-004"]} and mark["at"] == at

    # a crew session live again takes it away; a person's own does not (it was live throughout)
    recs[1].state = "idle"
    await agent._work_marks(now + 3 * WORK_SETTLE)
    assert "g" not in (agent._host_rec.get("teams") or {})


async def test_a_build_seen_then_made_a_live_check_is_work_once_live(agent, tmp_path, monkeypatch):
    """TD-411 (§6 rule 8, TD-407): both members saw TD-005 as a build and declared; merged, it reads as
    a live check not yet live, which leaves their `lane_seen` (rule 6 prunes a gone member's memory);
    at the promote it is in their lanes again, so the wound-down team has work waiting after the
    settle — what ao-grind missed for three hours over TD-400."""
    await park_ticks(agent)
    monkeypatch.setattr(agent, "_replay", lambda *a, **k: None)
    now = datetime.now(UTC)
    repo = str(tmp_path)

    def check(live: str) -> dict:
        return {**_e("TD-005"), "kind": "live-check", "built": [7], "live": live}

    agent._repos[repo] = {"name": "r", "root": repo, "ledger": {"entries": [_e("TD-001"), _e("TD-005")]}}
    recs = _team(
        agent, repo,
        _rec("grinder-ao-1", lane=["free-pick"], lane_seen={"at": "x", "ids": ["TD-001", "TD-005"]}),
        _rec("grinder-ao-2", lane=["free-pick"], lane_seen={"at": "x", "ids": ["TD-005", "TD-001"]}),
    )  # fmt: skip

    async def tick(at: datetime) -> None:
        for r in recs:
            await agent._lane_news(r, at)  # the keep-running pass, which runs before rule 8's
        await agent._work_marks(at)

    def waiting() -> dict | None:
        return (agent._host_rec.get("teams") or {}).get("g", {}).get("work_waiting")

    await tick(now + WORK_SETTLE)
    assert waiting() is None, "nothing new"
    agent._repos[repo]["ledger"]["entries"] = [_e("TD-001"), check("no")]
    await tick(now + 2 * WORK_SETTLE)
    assert [r.lane_seen["ids"] for r in recs] == [["TD-001"], ["TD-001"]] and waiting() is None
    agent._repos[repo]["ledger"]["entries"] = [_e("TD-001"), check("yes")]
    await tick(now + 3 * WORK_SETTLE)
    assert waiting() is None, "not before the settle"
    await tick(now + 4 * WORK_SETTLE)
    assert waiting()["members"] == {"grinder-ao-1": ["TD-005"], "grinder-ao-2": ["TD-005"]}


async def test_a_stopped_team_and_on_work_off_write_nothing(agent, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    repo = str(tmp_path)
    agent._repos[repo] = {"name": "r", "root": repo, "ledger": {"entries": [_e("TD-001"), _e("TD-002")]}}
    member = _rec("grinder-ao-1", lane=["free-pick"], lane_seen={"at": "x", "ids": ["TD-001"]})
    _team(agent, repo, _rec("manager-ao", lane=["TD-900"], lane_seen={"at": "x", "ids": []}), member)
    settings_mod.save({"teams": {"g": {"on_work": "off"}}})
    await agent._work_marks(now)
    await agent._work_marks(now + WORK_SETTLE)
    assert "g" not in (agent._host_rec.get("teams") or {}), "off: nothing written"
    settings_mod.save({})
    member.out_of_work = None  # killed by a person before it declared: stopped, not wound down
    await agent._work_marks(now + 2 * WORK_SETTLE)
    assert "g" not in (agent._host_rec.get("teams") or {})
    member.out_of_work = DECLARED
    await agent._work_marks(now + 3 * WORK_SETTLE)
    assert "g" not in (agent._host_rec.get("teams") or {}), "the settle starts again for an id no longer new"
    await agent._work_marks(now + 4 * WORK_SETTLE)
    assert agent._host_rec["teams"]["g"]["work_waiting"]["members"] == {"grinder-ao-1": ["TD-002"]}


async def test_a_ledger_that_cannot_be_read_keeps_the_mark(agent, tmp_path):
    """The techlead's read of #788: *could not look* is none of rule 8's three removals. A tick
    whose ledger reading carries `error` keeps the mark and its `at`, and the ids read back need no
    second settle."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    repo = str(tmp_path)
    good = {"entries": [_e("TD-001"), _e("TD-002")]}
    agent._repos[repo] = {"name": "r", "root": repo, "ledger": good}
    member = _rec("grinder-ao-1", lane=["free-pick"], lane_seen={"at": "x", "ids": ["TD-001"]})
    _team(agent, repo, _rec("manager-ao", lane=["TD-900"], lane_seen={"at": "x", "ids": []}), member)
    await agent._work_marks(now)
    await agent._work_marks(now + WORK_SETTLE)
    mark = agent._host_rec["teams"]["g"]["work_waiting"]
    first = dict(agent._work_first)
    agent._repos[repo]["ledger"] = {"error": "docs/technical_debt.md: unreadable"}
    await agent._work_marks(now + WORK_SETTLE + timedelta(minutes=1))
    assert agent._host_rec["teams"]["g"]["work_waiting"] == mark
    assert agent._work_first == first, "the settle's memory kept"
    agent._repos[repo]["ledger"] = good
    await agent._work_marks(now + WORK_SETTLE + timedelta(minutes=2))
    assert agent._host_rec["teams"]["g"]["work_waiting"] == mark, "no second settle, the same `at`"


def test_on_work_is_ask_start_or_off():
    assert settings_mod.parse_team({"on_work": "start"}) == {"on_work": "start"}
    with pytest.raises(ValueError, match="ask, start or off"):
        settings_mod.parse_team({"on_work": "later"})
    assert settings_mod.teams({"teams": {"g": {"on_work": "later", "reserve": 5}}}) == {"g": {"reserve": 5}}


async def test_dismiss_adds_the_ids_to_lane_seen_and_a_later_entry_asks_again(agent, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    repo = str(tmp_path)
    agent._repos[repo] = {"name": "r", "root": repo, "ledger": {"entries": [_e("TD-001"), _e("TD-002")]}}
    member = _rec("grinder-ao-1", lane=["free-pick"], lane_seen={"at": "x", "ids": ["TD-001"]})
    _team(agent, repo, member)
    await agent._work_marks(now)
    await agent._work_marks(now + WORK_SETTLE)
    assert agent._host_rec["teams"]["g"]["work_waiting"]
    async with LocalClient(caller="ao-t-grinder-ao-1") as worker:
        with pytest.raises(AgentError, match="a person's own"):
            await worker.call("clear_work", team="g")
    async with LocalClient() as person:
        # the reading the clients draw the row and the card's note from (TD-227 slice 3), and the
        # row's Snooze, keyed `work:<team>` in the attention store
        assert (await person.call("host"))["work"] == {"g": agent._host_rec["teams"]["g"]["work_waiting"]}
        snoozed = await person.call("attention_snooze", id="work:g", kind="work", until="2099-01-01T00:00:00Z")
        assert snoozed["row"] == "work:g|work" and snoozed["snoozed_until"] == "2099-01-01T00:00:00Z"
        assert (await person.call("attention_snooze", id="work:g", kind="work"))["snoozed_until"] is None
        got = await person.call("clear_work", team="g")
        assert got == {"team": "g", "cleared": True, "ids": ["TD-002"]}
        assert member.lane_seen["ids"] == ["TD-001", "TD-002"] and "g" not in agent._host_rec.get("teams", {})
        assert (await person.call("clear_work", team="g"))["cleared"] is False
        assert (await person.call("host"))["work"] == {}
    await agent._work_marks(now + 2 * WORK_SETTLE)
    assert "g" not in agent._host_rec.get("teams", {}), "a dismissed entry does not ask again"
    agent._repos[repo]["ledger"]["entries"].append(_e("TD-003"))
    await agent._work_marks(now + 3 * WORK_SETTLE)
    await agent._work_marks(now + 4 * WORK_SETTLE)
    assert agent._host_rec["teams"]["g"]["work_waiting"]["members"] == {"grinder-ao-1": ["TD-003"]}
    assert "clear_work" in modes.HOME_EDITS


def _orphan(id: str, kind: str, name: str, ref: str, repo: str, team: str = "g", **kw):
    from sessionorc.models import MailEntry

    o = {
        "at": "2026-10-02T09:00:00Z",
        "how": "closed",
        "ref": ref,
        "name": name,
        "repo": repo,
        "host": "h",
        "team": team,
    }
    return MailEntry(
        id=id, from_=f"ao-t-{name}", to=["person"], at="2026-10-02T08:00:00Z", kind=kind, text="which?", about=ref,
        orphaned=o, **kw,
    )  # fmt: skip


async def test_a_questions_end_is_work_for_a_wound_down_team(agent, tmp_path, monkeypatch):
    """TD-274 slice 4, design §6 rule 8 *A question's end is work*: an orphaned steer's lapse writes
    `work_waiting` for its wound-down team with no settle — the reference under the asker's name and
    `questions` — which the tick keeps with no lane news; an answer on another adds to it keeping
    `at`; Dismiss clears it and writes no question's reference to `lane_seen`."""
    await park_ticks(agent)
    monkeypatch.setattr(agent, "_replay", lambda *a, **k: None)
    repo = str(tmp_path)
    agent._repos[repo] = {"name": "r", "root": repo, "ledger": {"entries": []}}
    _team(agent, repo, _rec("manager-ao"), _rec("designer", lane=["free-pick"], lane_seen={"at": "x", "ids": []}))
    lapse = _orphan("m-s", "steer", "designer", "TD-222", repo, bound="2026-10-02T09:30:00Z", default="go")
    agent.person_inbox.append(lapse)
    now = datetime.now(UTC)
    await agent._sweep_mail(now)
    mark = agent._host_rec["teams"]["g"]["work_waiting"]
    assert mark["members"] == {"designer": ["TD-222"]} and mark["repo"] == repo
    assert mark["questions"] == [{"id": "m-s", "ref": "TD-222", "name": "designer", "how": "lapsed", "kind": "steer"}]
    await agent._work_marks(now)  # nothing new in the lanes: the question's mark stands as written
    assert agent._host_rec["teams"]["g"]["work_waiting"] == mark
    asked = _orphan("m-a", "ask", "designer", "TD-223", repo)
    agent._question_end(asked, "answered")
    again = agent._host_rec["teams"]["g"]["work_waiting"]
    assert again["at"] == mark["at"] and again["members"] == {"designer": ["TD-222", "TD-223"]}
    assert [q["how"] for q in again["questions"]] == ["lapsed", "answered"]
    agent._question_end(asked, "answered")  # a question ends once
    assert len(agent._host_rec["teams"]["g"]["work_waiting"]["questions"]) == 2
    async with LocalClient() as person:
        await person.call("clear_work", team="g")
    assert "g" not in (agent._host_rec.get("teams") or {})
    assert agent.sessions["ao-t-designer"].lane_seen["ids"] == []  # a question ends once


async def test_a_lane_id_no_longer_new_leaves_the_questions_refs_alone(agent, tmp_path, monkeypatch):
    """§6 rule 8: a mark whose members gained a lane's id beside a question's reference, once that id
    is no longer new (the entry resolved on main), keeps the question's reference alone — the id is
    not counted or started for (the techlead's read of #932)."""
    await park_ticks(agent)
    monkeypatch.setattr(agent, "_replay", lambda *a, **k: None)
    repo = str(tmp_path)
    agent._repos[repo] = {"name": "r", "root": repo, "ledger": {"entries": []}}
    _team(agent, repo, _rec("manager-ao"), _rec("designer", lane=["free-pick"], lane_seen={"at": "x", "ids": []}))
    agent._question_end(_orphan("m-s", "steer", "designer", "TD-222", repo, default="go"), "lapsed")
    agent._host_rec["teams"]["g"]["work_waiting"]["members"]["designer"].append("TD-300")
    await agent._work_marks(datetime.now(UTC))
    mark = agent._host_rec["teams"]["g"]["work_waiting"]
    assert mark["members"] == {"designer": ["TD-222"]} and len(mark["questions"]) == 1


async def test_a_questions_end_on_a_stopped_or_live_team_or_under_off_writes_nothing(agent, tmp_path):
    """TD-274 slice 4: a team stopped rather than wound down (a member that never declared), one
    live again, and `on_work: off` are left alone; the note waits in the mailbox for a Start."""
    await park_ticks(agent)
    repo = str(tmp_path)
    q = _orphan("m-x", "steer", "designer", "TD-9", repo)
    _team(agent, repo, _rec("manager-ao"), _rec("designer", out_of_work=None))
    agent._question_end(q, "lapsed")
    assert "g" not in (agent._host_rec.get("teams") or {}), "stopped: nothing"
    agent.sessions["ao-t-designer"].out_of_work = DECLARED
    agent.sessions["ao-t-manager-ao"].state = "idle"
    agent._question_end(q, "lapsed")
    assert "g" not in (agent._host_rec.get("teams") or {}), "live: the member is waiting, not this rule's"
    agent.sessions["ao-t-manager-ao"].state = "closed"
    settings_mod.save({"teams": {"g": {"on_work": "off"}}})
    agent._question_end(q, "lapsed")
    assert "g" not in (agent._host_rec.get("teams") or {}), "off: nothing written"


async def test_a_decided_board_line_is_work_for_a_wound_down_team(agent, tmp_path, monkeypatch):
    """TD-384 (§6 rule 8): the repo's work orders count as entries do — a decided line in a wound-down
    grinder's `free-pick` lane, owner words or not, is written as the team's work, by `board:<key>`."""
    from sessionorc import workorders

    await park_ticks(agent)

    async def replay(*a, **k):
        pass

    monkeypatch.setattr(agent, "_replay", replay)
    now = datetime.now(UTC)
    repo = str(tmp_path)
    order = workorders.entry({"id": "board:0123abcd", "title": "Keep the backup?"})
    agent._repos[repo] = {"name": "r", "root": repo, "ledger": {"entries": [_e("TD-001")]}}
    _team(
        agent, repo,
        _rec("manager-ao", lane=["TD-900"], lane_seen={"at": "x", "ids": []}),
        _rec("grinder-ao-1", lane=["free-pick", "owner:grinder"], lane_seen={"at": "x", "ids": ["TD-001"]}),
    )  # fmt: skip
    await agent._work_marks(now + WORK_SETTLE)
    assert "work_waiting" not in (agent._host_rec.get("teams") or {}).get("g", {}), "nothing new without it"
    agent._repos[repo]["work_orders"] = {"orders": [order]}
    await agent._work_marks(now + WORK_SETTLE)
    await agent._work_marks(now + 2 * WORK_SETTLE)
    assert agent._host_rec["teams"]["g"]["work_waiting"]["members"] == {"grinder-ao-1": ["board:0123abcd"]}
