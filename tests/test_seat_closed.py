"""TD-259 slice 3, design §6 rule 3 *A manager on call is a seat of this rule*: a seat is closed
whenever it is empty, so a question to a closed seat waits for its fill — the mail sweep spares it,
for every seat — and a team whose manager on call is closed is read, wound down and left alone by
rules 9 and 1 as the records already say."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import park_ticks
from test_finished_tick import _Home, _manager, _rec

from sessionorc import work
from sessionorc.agent_common import FINISHED_SETTLE, WRAPUP_GRACE
from sessionorc.client import LocalClient

ON_CALL = {"trigger": "team"}


def _mk(person, tmp_path):
    async def mk(n: str, **kw):
        params = dict(name=n, dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"], unattended=True)
        return (await person.call("create", **{**params, **kw}))["id"]

    return mk


async def test_a_question_to_a_closed_seat_survives_a_refused_fill_and_is_there_at_the_fill(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        seat = await mk("tl", supervised=True, prompt="the seat's brief", seat={"trigger": "asks"})
        plain, asker = await mk("plain"), await mk("asker")
        for sid in (seat, plain):
            await person.call("set_controllers", id=asker, add=[sid])
        async with LocalClient(caller=asker) as a:
            q = (await a.call("msg", to=seat, text="which shape?", kind="ask"))["entry"]
            lost = (await a.call("msg", to=plain, text="status?", kind="ask"))["entry"]
            short = (await a.call("msg", to=seat, text="quick?", kind="ask", bound=1))["entry"]
        for sid in (seat, plain):
            await person.call("close", id=sid)
        was = agent.sessions[seat]
        # the sweeps read the clock the test gives them, from the short ask's own send: `bound=1` is a
        # second of wall clock, which two closes on a slow runner outlast (TD-289)
        now = datetime.fromisoformat(short["at"])
        agent._profile_gated = lambda *a: True  # the fill is refused: a pause, as a ceiling or a down link would
        await agent._keep_running(now)
        await agent._sweep_mail(now)
        assert agent.sessions[seat] is was and was.seat_due["by"] == "asks", "due, and not filled into a pause"
        mail = (await person.call("get", id=asker))["mail"]
        assert mail["expired"] == [lost["id"]], "a closed record that is no seat expires its ask, as ever"
        assert mail["addressee_exited"] == [q["id"], short["id"]], "the seat's wait, as an exited record's do"
        # a bound is still a bound: the asker set it, and an empty seat does not stretch it
        await agent._sweep_mail(now + timedelta(seconds=2))
        assert sorted((await person.call("get", id=asker))["mail"]["expired"]) == sorted([lost["id"], short["id"]])
        assert was.asks_waiting(home=agent.host) == 1
        del agent._profile_gated
        await agent._keep_running(now)
        new = agent.sessions[seat]
        assert new is not was and [r["why"] for r in new.restarts] == ["fill"]
        assert [e.id for e in new.inbox if e.open] == [q["id"]], "the question is there at the fill"
        for sid in (seat, asker):
            await person.call("kill", id=sid)


def test_a_closed_manager_on_call_is_still_the_manager_and_its_team_reads_without_it():
    """`work.manager_of` reads `control` and `controllers`, whatever `seat` says, and `work.finished`
    passes over a dead manager: the team's reading holds with the seat empty."""
    gone = _manager(state="closed", seat=ON_CALL)
    g1, g2 = _rec("g1"), _rec("g2")
    assert work.manager_of([g1, gone, g2]) is gone
    got = work.finished([gone, g1, g2])
    assert got["why"] == [] and got["names"] == ["g1", "g2"] and got["restart"] is False
    assert work.finished([gone, g1, _rec("g2", out_of_work=None)])["why"] == ["g2 idle, not declared"]
    assert work.finished([gone, g1, _rec("g2", state="working")])["why"] == ["g2 working"]
    # filled, it is read as a manager and as a seat alike: idle or not, never asked to declare
    assert work.finished([_manager(seat=ON_CALL), g1, g2])["why"] == []
    assert work.finished([_manager(state="working", seat=ON_CALL), g1, g2])["why"] == ["manager working"]


async def test_rule_nine_winds_down_a_team_whose_manager_on_call_is_closed(agent, monkeypatch):
    """The members are closed and the person told; the closed seat gets no line, is not filled for
    it, and is never the *manager did not close* row, which reads `finished_sent_at`."""
    await park_ticks(agent)
    manager = _manager(state="closed", seat=ON_CALL)
    g1, g2 = _rec("g1"), _rec("g2")
    home = _Home(agent, monkeypatch, g1, g2, manager)
    now = datetime.now(UTC)
    await agent._finished_pass(now)
    await agent._finished_pass(now + FINISHED_SETTLE)
    assert home.closed == ["g1", "g2"] and home.sent == []
    assert len(home.notes()) == 1 and "g finished and the host agent wound it down" in home.notes()[0]
    later = now + FINISHED_SETTLE + WRAPUP_GRACE + timedelta(minutes=1)
    await agent._keep_running(later)
    await agent._finished_pass(later)
    assert agent.sessions[manager.id] is manager and manager.state == "closed" and manager.restarts == []
    assert manager.finished_sent_at is None and manager.seat_due is None and manager.closed_for is None
    assert len(home.notes()) == 1, "told once"
    assert work.team_wound_down([manager, g1, g2]) == "2026-09-28T06:56:00Z", "a seat never declares"


async def test_rule_one_leaves_an_exited_manager_on_call_to_rule_three(agent, monkeypatch):
    """A manager on call that exits — its `/exit` after acting, or a crash — is an empty seat: no
    crash restart, and no fill while nothing is due."""
    await park_ticks(agent)
    manager = _manager(state="exited", seat=ON_CALL, pane=True, exit_code=1)
    _Home(agent, monkeypatch, manager, _rec("g1", out_of_work=None, state="working"))
    await agent._keep_running(datetime.now(UTC))
    assert agent.sessions[manager.id] is manager and manager.restarts == [] and manager.restart_ceiling is None
