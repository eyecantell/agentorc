"""TD-241 slice 3, design §6 rule 9 *What the tick does with it*: a team whose reading of *finished*
has held `FINISHED_SETTLE` is wound down by the home — its finished members closed under the
wrap-up's safety check, its manager told once and closed `WRAPUP_GRACE` later with `closed_for:
{why: finished}`, and the person told what the manager did not say."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import park_ticks

from sessionorc import work
from sessionorc.agent_common import FINISHED_SETTLE, WRAPUP_GRACE
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
        self.agent, self.closed, self.sent, self.takes = agent, [], [], True
        for r in recs:
            r.host = agent.host
            agent.sessions[r.id] = r
        monkeypatch.setattr(agent, "rpc_close", self._close)
        monkeypatch.setattr(agent, "_policy_send", self._send)

    async def _close(self, id: str) -> dict:
        s = self.agent.sessions[id]
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
