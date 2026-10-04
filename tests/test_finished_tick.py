"""TD-241 slice 3, design §6 rule 9 *What the tick does with it*: a team whose reading of *finished*
has held `FINISHED_SETTLE` is wound down by the home — its finished members closed under the
wrap-up's safety check, its manager told once and closed `WRAPUP_GRACE` later with `closed_for:
{why: finished}`, and the person told what the manager did not say."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import park_ticks

from sessionorc import work
from sessionorc.agent_common import FINISHED_SETTLE, WRAPUP_GRACE
from sessionorc.client import LocalClient
from sessionorc.models import PERSON, SYSTEM, MailEntry, ProgressEntry, Session, now_iso

CLEAN = {"branch": "w", "dirty": 0, "unpushed": 0}
MANAGER = "ao-t-manager"
START = "2026-09-28T06:00:00Z"


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _rec(name: str, **kw) -> Session:
    base = dict(
        id=f"ao-t-{name}", name=name, kind="interactive", adapter="shell", dir="/tmp/x", state="idle",
        team="g", unattended=True, supervised=True, git=dict(CLEAN), controllers=[MANAGER], created=START,
        out_of_work={"at": "2026-09-28T06:56:00Z", "why": f"{name} found nothing pickable"},
    )  # fmt: skip
    return Session(**{**base, **kw})


def _manager(**kw) -> Session:
    return _rec("manager", capabilities=["control"], controllers=[], out_of_work=None, **kw)


class _Home:
    """The agent's close and its policy send stood in for: what rule 9 closed and typed, in order."""

    def __init__(self, agent, monkeypatch, *recs: Session) -> None:
        self.agent, self.closed, self.sent, self.takes, self.closers = agent, [], [], True, []
        for r in recs:
            r.host = agent.host
            agent.sessions[r.id] = r
        monkeypatch.setattr(agent, "rpc_close", self._close)
        monkeypatch.setattr(agent, "_policy_send", self._send)

    async def _close(self, id: str, closer: dict | None = None) -> dict:
        s = self.agent.sessions[id]
        self.closers.append(closer)
        s.set_state("closed", confidence="scraped")
        s.closed_at, s.closed_for = now_iso(), None
        self.closed.append(s.name)
        return {}

    async def _send(self, s: Session, text: str) -> bool:
        if self.takes:
            self.sent.append((s.name, text))
        return self.takes

    def notes(self) -> list[str]:
        return [e.text for e in self.agent.person_inbox if e.from_ == SYSTEM]


async def test_a_finished_team_is_wound_down_after_the_settle_and_the_person_told(agent, monkeypatch):
    await park_ticks(agent)
    g1 = _rec("g1", progress=[ProgressEntry(ref="TD-1", status="done", pr=812, at="2026-09-28T06:30:00Z")])
    old = ProgressEntry(ref="TD-0", status="done", pr=700, at="2026-09-27T01:00:00Z")
    g2 = _rec("g2", progress=[old, ProgressEntry(ref="TD-2", status="done", pr=813, at="2026-09-28T06:40:00Z")])
    manager = _manager()
    paul = _rec("paul", unattended=False, out_of_work=None, state="working")
    seat = _rec("techlead", seat={"trigger": "asks"}, out_of_work=None)
    home = _Home(agent, monkeypatch, g1, g2, manager, paul, seat)
    now = datetime.now(UTC)

    await agent._finished_pass(now)
    await agent._finished_pass(now + FINISHED_SETTLE - timedelta(seconds=1))
    assert home.closed == [] and home.sent == [], "not before the settle"

    told = now + FINISHED_SETTLE
    await agent._finished_pass(told)
    assert home.closed == ["g1", "g2"], "the members; never the seat, the manager or a person's session"
    assert [n for n, _ in home.sent] == ["manager"] and "your team is finished" in home.sent[0][1]
    assert manager.finished_sent_at and manager.state == "idle"

    manager.finished_sent_at = _iso(told)
    await agent._finished_pass(told + WRAPUP_GRACE - timedelta(seconds=5))
    assert len(home.sent) == 1 and "manager" not in home.closed, "one line, and its grace"
    assert home.notes() == []

    await agent._finished_pass(told + WRAPUP_GRACE)
    assert home.closed == ["g1", "g2", "manager"]
    assert manager.closed_for == {"why": "finished", "closed_at": manager.closed_at}
    (note,) = home.notes()
    assert note.startswith("g finished and the host agent wound it down") and "#812, #813" in note
    assert [e.team for e in agent.person_inbox if e.from_ == SYSTEM] == ["g"], "filed under its team"
    assert "#700" not in note, "a pull request from before the team's start"
    assert "g1: g1 found nothing pickable" in note and "g2: g2 found nothing pickable" in note
    assert work.team_wound_down([g1, g2, manager, seat, paul]) is None, "its seat is live: rule 3's to close"

    seat.set_state("closed", confidence="scraped")
    assert work.team_wound_down([g1, g2, manager, seat, paul]) == g1.out_of_work["at"], "wound down, by the tick"
    await agent._finished_pass(told + WRAPUP_GRACE + timedelta(minutes=1))
    assert len(home.notes()) == 1 and len(home.closed) == 3, "told once"


async def test_a_member_with_work_left_stays_open_until_it_is_pushed(agent, monkeypatch):
    await park_ticks(agent)
    g1, g2 = _rec("g1"), _rec("g2", git={"branch": "w", "dirty": 0, "unpushed": 2})
    unknown = _rec("g3", git=None)
    manager = _manager()
    home = _Home(agent, monkeypatch, g1, g2, unknown, manager)
    now = datetime.now(UTC)
    await agent._finished_pass(now)
    await agent._finished_pass(now + FINISHED_SETTLE)
    assert home.closed == ["g1"] and g2.state == "idle" and unknown.state == "idle"
    assert [n for n, _ in home.sent] == ["manager"], "the manager is told all the same"
    manager.finished_sent_at = _iso(now + FINISHED_SETTLE)
    g2.git = dict(CLEAN)
    await agent._finished_pass(now + FINISHED_SETTLE + timedelta(minutes=1))
    assert home.closed == ["g1", "g2"], "closed once its work reads pushed; an unknown git state never"


async def test_what_keeps_a_team_from_being_wound_down(agent, monkeypatch):
    await park_ticks(agent)
    g1, manager = _rec("g1"), _manager()
    seat = _rec("techlead", seat={"trigger": "asks"}, out_of_work=None)
    home = _Home(agent, monkeypatch, g1, manager, seat)
    now = datetime.now(UTC)
    late = now + FINISHED_SETTLE

    g1.restart_wanted = {"at": "2026-09-28T07:00:00Z", "why": "context bound"}
    await agent._finished_pass(now)
    await agent._finished_pass(late)
    assert home.closed == [] and home.sent == [], "a team that wants another run is rule 2's"

    g1.restart_wanted = None
    seat.set_state("working", confidence="hook")
    await agent._finished_pass(late)
    await agent._finished_pass(late + FINISHED_SETTLE)
    assert home.closed == [] and home.sent == [], "a working seat"

    seat.set_state("idle", confidence="hook")
    await agent._finished_pass(late)  # the reading holds from here
    g1.set_state("working", confidence="hook")  # it claimed again on rule 6's news
    await agent._finished_pass(late + timedelta(minutes=5))
    g1.set_state("idle", confidence="hook")
    await agent._finished_pass(late + FINISHED_SETTLE)
    assert home.closed == [] and home.sent == [], "the settle starts again from the tick it holds again"
    await agent._finished_pass(late + 2 * FINISHED_SETTLE)
    assert home.closed == ["g1"] and len(home.sent) == 1

    agent.mode = "node"
    home.closed.clear()
    manager.finished_sent_at = _iso(late)
    await agent._finished_pass(late + timedelta(days=1))
    assert home.closed == [], "the home's rule: a node's tick winds nothing down"


async def test_the_managers_own_note_is_not_said_twice_and_a_busy_manager_is_left(agent, monkeypatch):
    await park_ticks(agent)
    g1, manager = _rec("g1"), _manager()
    home = _Home(agent, monkeypatch, g1, manager)
    now = datetime.now(UTC)
    home.takes = False  # words in its composer: the line is not typed, and the next tick looks again
    await agent._finished_pass(now)
    await agent._finished_pass(now + FINISHED_SETTLE)
    assert home.closed == ["g1"] and manager.finished_sent_at is None
    home.takes = True
    told = now + FINISHED_SETTLE + timedelta(minutes=1)
    await agent._finished_pass(told)
    assert len(home.sent) == 1 and manager.finished_sent_at
    manager.finished_sent_at = _iso(told)

    manager.set_state("working", confidence="hook")  # its last acts are work
    agent.person_inbox.append(
        MailEntry(id="m-1", from_=MANAGER, to=[PERSON], at=_iso(told + timedelta(minutes=2)), kind="note", text="done")
    )
    await agent._finished_pass(told + WRAPUP_GRACE)
    assert "manager" not in home.closed and manager.finished_sent_at, "never idle: left as it is, the Inbox's row"
    manager.set_state("idle", confidence="hook")
    await agent._finished_pass(told + WRAPUP_GRACE + timedelta(minutes=1))
    assert home.closed == ["g1", "manager"] and work.closed_finished(manager)
    assert home.notes() == [], "the manager announced it: never told twice"


async def test_a_manager_that_closed_itself_is_marked_and_the_person_told(agent, monkeypatch):
    await park_ticks(agent)
    g1, manager = _rec("g1", state="closed"), _manager()
    home = _Home(agent, monkeypatch, g1, manager)
    now = datetime.now(UTC)
    manager.finished_sent_at = _iso(now)
    manager.set_state("closed", confidence="scraped")
    manager.closed_at = _iso(now + timedelta(minutes=3))
    await agent._finished_pass(now + timedelta(minutes=4))
    assert manager.closed_for == {"why": "finished", "closed_at": manager.closed_at} and len(home.notes()) == 1
    await agent._finished_pass(now + timedelta(minutes=5))
    assert len(home.notes()) == 1 and home.closed == []


async def test_a_member_at_work_again_takes_the_wind_down_back(agent, monkeypatch):
    await park_ticks(agent)
    g1, manager = _rec("g1", git={"branch": "w", "dirty": 1, "unpushed": 0}), _manager()
    home = _Home(agent, monkeypatch, g1, manager)
    now = datetime.now(UTC)
    manager.finished_sent_at = _iso(now)
    g1.set_state("working", confidence="hook")
    await agent._finished_pass(now + WRAPUP_GRACE)
    assert manager.finished_sent_at is None and home.closed == [] and home.notes() == []

    g1.set_state("idle", confidence="hook")
    g1.git = dict(CLEAN)
    g1.restart_wanted = {"at": _iso(now), "why": "context bound"}  # rule 2's, and a close would lose it
    manager.finished_sent_at = _iso(now)
    await agent._finished_pass(now + WRAPUP_GRACE)
    assert manager.finished_sent_at is None and home.closed == [] and home.notes() == []


async def test_a_killed_manager_is_not_marked_and_one_teams_surprise_is_not_anothers(agent, monkeypatch):
    await park_ticks(agent)
    g1, manager = _rec("g1", state="closed"), _manager()
    h1, other = _rec("h1", team="h", controllers=[]), _rec("z1", team="z", controllers=[])
    home = _Home(agent, monkeypatch, g1, manager, h1, other)
    now = datetime.now(UTC)
    manager.finished_sent_at = _iso(now)
    manager.set_state("exited", confidence="scraped")
    await agent._finished_pass(now)
    assert manager.closed_for is None and home.notes() == [], "a kill or a crash is not the line's close"

    real = agent._finished_team

    async def surprised(team, records, at):
        if team == "h":
            raise RuntimeError("boom")
        return await real(team, records, at)

    monkeypatch.setattr(agent, "_finished_team", surprised)
    agent._finished_first["h"] = now
    await agent._finished_pass(now + FINISHED_SETTLE)
    assert agent._finished_first["h"] == now, "its settle is kept"
    assert home.closed == ["z1"] and h1.state == "idle", "z was read, and wound down a settle after its first tick"


async def test_a_team_a_person_leads_is_closed_and_announced_with_nobody_to_tell(agent, monkeypatch):
    await park_ticks(agent)
    g1, g2 = _rec("g1", controllers=[]), _rec("g2", controllers=[])
    home = _Home(agent, monkeypatch, g1, g2)
    now = datetime.now(UTC)
    await agent._finished_pass(now)
    await agent._finished_pass(now + FINISHED_SETTLE)
    assert home.closed == ["g1", "g2"] and home.sent == []
    (note,) = home.notes()
    assert "its manager did not announce it" not in note and "g1: g1 found nothing pickable" in note
    await agent._finished_pass(now + FINISHED_SETTLE + timedelta(minutes=1))
    assert len(home.notes()) == 1


async def test_a_member_left_with_work_is_closed_after_its_manager_is_gone_and_nothing_is_said_again(
    agent, monkeypatch
):
    await park_ticks(agent)
    g1, g2 = _rec("g1"), _rec("g2", git={"branch": "w", "dirty": 0, "unpushed": 2})
    manager = _manager()
    home = _Home(agent, monkeypatch, g1, g2, manager)
    now = datetime.now(UTC)
    told = now + FINISHED_SETTLE
    await agent._finished_pass(now)
    await agent._finished_pass(told)
    manager.finished_sent_at = _iso(told)
    await agent._finished_pass(told + WRAPUP_GRACE)
    assert home.closed == ["g1", "manager"] and work.closed_finished(manager) and len(home.notes()) == 1

    await agent._finished_pass(told + WRAPUP_GRACE + timedelta(minutes=1))
    assert g2.state == "idle", "work left: still open"
    g2.git = dict(CLEAN)
    await agent._finished_pass(told + WRAPUP_GRACE + timedelta(minutes=2))
    assert home.closed == ["g1", "manager", "g2"], "closed the tick its work reads pushed, its manager already closed"
    assert len(home.notes()) == 1, "never told twice"


async def test_a_nodes_manager_closed_with_a_member_left_is_announced_once(agent, monkeypatch):
    await park_ticks(agent)
    g1, g2 = _rec("g1"), _rec("g2", git={"branch": "w", "dirty": 1, "unpushed": 0})
    manager = _manager()
    home = _Home(agent, monkeypatch, g1, g2, manager)
    manager.host = "node-1"  # no line typed: the close alone, routed over its link
    manager.id = MANAGER
    agent._link_muxes["node-1"] = object()

    async def route(method, params, caller, host):
        assert (method, host) == ("close", "node-1")
        return await home._close(params["id"], params.get("closer"))

    monkeypatch.setattr(agent, "_route_act", route)
    monkeypatch.setattr(agent, "_address", lambda r: r.id)
    now = datetime.now(UTC)
    late = now + FINISHED_SETTLE
    await agent._finished_pass(now)
    await agent._finished_pass(late)
    assert home.closed == ["g1", "manager"] and home.sent == [] and len(home.notes()) == 1
    # each close carries the tick's word, the node's over the link too (§4.5 row 5 (b), TD-265)
    assert home.closers == [{"by": "tick", "why": "finished"}] * 2

    g2.git = dict(CLEAN)
    await agent._finished_pass(late + timedelta(minutes=1))
    await agent._finished_pass(late + timedelta(minutes=1) + FINISHED_SETTLE)
    assert home.closed == ["g1", "manager", "g2"] and len(home.notes()) == 1, "the member's close says nothing again"


async def test_a_person_led_teams_last_member_closed_by_a_person_is_announced_once(agent, monkeypatch):
    """TD-256 (2): no manager carries `finished_sent_at`, so that the note is owed is the home's to
    keep — from the settle until the last member is gone, whoever closed it."""
    await park_ticks(agent)
    g1, g2 = _rec("g1", controllers=[]), _rec("g2", controllers=[], git={"branch": "w", "dirty": 0, "unpushed": 2})
    home = _Home(agent, monkeypatch, g1, g2)
    now = datetime.now(UTC)
    late = now + FINISHED_SETTLE
    await agent._finished_pass(now)
    await agent._finished_pass(late)
    assert home.closed == ["g1"] and home.notes() == [], "one left open with work: not dissolved yet"
    await agent._finished_pass(late + timedelta(minutes=1))
    assert home.notes() == []

    agent._finished_owed["forgotten"] = now  # a team whose records are gone keeps no mark
    await home._close(g2.id)  # the person's Close
    await agent._finished_pass(late + timedelta(minutes=2))
    (note,) = home.notes()
    assert "g2: g2 found nothing pickable" in note and "its manager did not announce it" not in note
    await agent._finished_pass(late + timedelta(minutes=3))
    assert len(home.notes()) == 1 and agent._finished_owed == {}, "never told twice"


async def test_a_member_at_work_again_owes_no_note_and_one_member_alone_is_announced(agent, monkeypatch):
    await park_ticks(agent)
    g1 = _rec("g1", controllers=[], git={"branch": "w", "dirty": 3, "unpushed": 0})
    home = _Home(agent, monkeypatch, g1)
    now = datetime.now(UTC)
    late = now + FINISHED_SETTLE
    await agent._finished_pass(now)
    await agent._finished_pass(late)
    assert home.closed == [] and agent._finished_owed == {"g": now}
    g1.set_state("working", confidence="hook")  # it took a turn: the reading no longer holds
    await agent._finished_pass(late + timedelta(minutes=1))
    assert agent._finished_owed == {}
    await home._close(g1.id)
    await agent._finished_pass(late + timedelta(minutes=2))
    assert home.notes() == [], "closed at work: nothing finished, nothing announced"

    # a team of one, finished with work left and closed by hand, is announced as any other
    g1.set_state("idle", confidence="hook")
    await agent._finished_pass(late + timedelta(minutes=3))
    await agent._finished_pass(late + timedelta(minutes=3) + FINISHED_SETTLE)
    await home._close(g1.id)
    await agent._finished_pass(late + timedelta(minutes=4) + FINISHED_SETTLE)
    assert len(home.notes()) == 1


async def test_a_resumed_manager_carries_neither_of_rule_9s_marks(agent, hookstub, tmp_path):
    """TD-256 (1): a resume builds its record anew (`rpc_create`), under the name or another, so
    `finished_sent_at` and `closed_for` stay on the run rule 9 closed and never reach the next."""
    await park_ticks(agent)
    async with LocalClient() as person:
        made = {
            "dir": str(tmp_path),
            "adapter": "hookstub",
            "unattended": True,
            "team": "g",
            "capabilities": ["control"],
        }
        old = await person.call("create", name="manager", **made)
        await person.call("hook", session=old["id"], adapter_id="cc-41")
        rec = agent.sessions[old["id"]]
        rec.finished_sent_at = now_iso()
        await person.call("close", id=old["id"])
        agent._mark_closed(rec, "finished")
        assert work.closed_finished(rec) and rec.finished_sent_at

        same = await person.call("create", name="manager", resume="cc-41", **made)
        assert same["id"] == old["id"]
        again = agent.sessions[same["id"]]
        assert again.finished_sent_at is None and again.closed_for is None
        await person.call("kill", id=same["id"])
        (tmp_path / "b").mkdir()
        other = await person.call("create", name="manager-2", resume="cc-41", **{**made, "dir": str(tmp_path / "b")})
        new = agent.sessions[other["id"]]
        assert new.finished_sent_at is None and new.closed_for is None
        await person.call("kill", id=other["id"])


async def test_a_member_waiting_on_the_persons_answer_keeps_the_team_live(agent, monkeypatch):
    """TD-274 slice 1, design §4.9a *Waiting is read, never declared*: a member that declared
    `none` with a steer about a reference open in the person inbox is not finished, so rule 9 never
    settles; once the steer closes the reading holds and the wind-down runs as ever."""
    await park_ticks(agent)
    g1 = _rec("g1")
    designer = _rec("designer")
    home = _Home(agent, monkeypatch, g1, designer, _manager())
    steer = MailEntry(
        id="m-s", from_=designer.id, to=[PERSON], at=START, kind="steer", text="go with the default?",
        about="TD-222", bound="2026-10-02T09:57:00Z", default="the default",
    )  # fmt: skip
    agent.person_inbox.append(steer)
    now = datetime.now(UTC)
    await agent._finished_pass(now)
    await agent._finished_pass(now + FINISHED_SETTLE)
    assert home.closed == [] and home.sent == [], "a member waiting on the person keeps its team live"
    steer.closed_reason = "lapsed"
    later = now + FINISHED_SETTLE + timedelta(minutes=1)
    await agent._finished_pass(later)
    await agent._finished_pass(later + FINISHED_SETTLE)
    assert sorted(home.closed) == ["designer", "g1"]


async def test_a_member_waiting_after_the_line_takes_the_wind_down_back(agent, monkeypatch):
    """TD-274 slice 1: once the manager has been told the team is finished, a member that now waits
    on the person takes the wind-down back."""
    await park_ticks(agent)
    g1, manager = _rec("g1"), _manager()
    _Home(agent, monkeypatch, g1, manager)
    manager.finished_sent_at = _iso(datetime.now(UTC))
    agent.person_inbox.append(
        MailEntry(id="m-w", from_=g1.id, to=[PERSON], at=START, kind="ask", text="?", about="TD-9")
    )
    await agent._finished_pass(datetime.now(UTC))
    assert manager.finished_sent_at is None, "a member waiting on the person is a member at work again"


async def test_the_host_reading_carries_what_each_session_waits_on(agent):
    """TD-274's client half: nobody but a person reads the person inbox, so the home's `host`
    reading carries `waiting` — the reference and the bound, never the text — for the page's
    *concluded* and a session's `ao team status`."""
    agent.person_inbox.append(
        MailEntry(id="m-h", from_="ao-x-g1", to=[PERSON], at=START, kind="steer", text="secret?", about="td-7",
                  bound="2026-10-02T09:57:00Z", default="d")
    )  # fmt: skip
    got = (await agent.rpc_host())["waiting"]
    assert got == {"ao-x-g1": [{"id": "m-h", "ref": "TD-007", "bound": "2026-10-02T09:57:00Z"}]}
    assert "secret" not in str(got)
