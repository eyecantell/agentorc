"""TD-241 slice 1, design §6 rule 9 (*Finished is the home's reading*): `work.finished` reads a live
team as finished from the records carrying its badge and nothing else — who is a seat, who the
manager and who a person's all come off the records — and the page's *concluded* is that reading."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from agentorc import teamrun
from sessionorc import work
from sessionorc.models import Session

pytestmark = pytest.mark.unit

OUT = {"at": "2026-09-29T06:00:00Z", "why": "nothing pickable"}
LATER = {"at": "2026-09-29T07:00:00Z", "why": "nothing pickable"}
WANTS = {"at": "2026-09-29T08:00:00Z", "why": "context bound"}
LEAD = "ao-t-manager"


def _rec(name: str, state: str = "idle", **kw) -> Session:
    base = dict(
        id=f"ao-t-{name}", name=name, kind="interactive", adapter="shell", dir="/tmp/x", state=state,
        team="g", unattended=True, controllers=[LEAD],
    )  # fmt: skip
    return Session(**{**base, **kw})


def _manager(state: str = "idle", **kw) -> Session:
    return _rec("manager", state, controllers=[], capabilities=["control"], **kw)


def _seat(state: str = "idle") -> Session:
    return _rec("techlead", state, seat={"role": "techlead"})


def test_two_members_declared_and_a_manager_idle_without_declaring_is_finished():
    team = [_manager(), _rec("g1", out_of_work=OUT), _rec("g2", out_of_work=LATER), _seat()]
    got = work.finished(team)
    assert got == {"at": LATER["at"], "restart": False, "names": ["g1", "g2", "manager", "techlead"], "why": []}
    # the page's concluded is the same reading, from the views a client holds, and names what Start closes
    views = [asdict(r) for r in team]
    assert work.finished(views) == got
    assert teamrun.concluded(views) == {k: got[k] for k in ("at", "restart", "names")}
    # the manager's own word changes nothing: it is not asked for
    assert work.finished([_manager(out_of_work=OUT), *team[1:]])["why"] == []


def test_the_why_clauses_name_the_sessions_that_keep_it_from_holding():
    team = [_manager("working"), _rec("g1", "working", out_of_work=OUT), _rec("g2"), _seat("needs-you")]
    got = work.finished(team)
    assert got["why"] == ["manager working", "g1 working", "g2 idle, not declared", "techlead needs-you"]
    assert got["at"] is None and teamrun.concluded([asdict(r) for r in team]) is None


def test_a_working_seat_blocks_it_and_an_idle_or_gone_one_does_not():
    members = [_manager(), _rec("g1", out_of_work=OUT)]
    assert work.finished([*members, _seat("working")])["why"] == ["techlead working"]
    assert work.finished([*members, _seat("closed")])["why"] == []
    # a seat by the definition's name, for a record with no `seat` field (the card's `seats`)
    assert work.finished([*members, _rec("techlead", "working")], {"techlead"})["why"] == ["techlead working"]
    assert work.finished([*members, _rec("techlead")], {"techlead"})["why"] == []


def test_an_interactive_member_counts_for_nothing():
    paul = _rec("paul", "working", unattended=False)
    got = work.finished([_manager(), _rec("g1", out_of_work=OUT), paul])
    assert got["why"] == [] and "paul" not in got["names"]
    # and a team whose only live session is a person's has nothing live to read
    assert work.finished([paul, _rec("g1", "closed", out_of_work=OUT)]) is None


def test_a_member_that_wants_a_restart_reads_restart_and_a_dead_one_blocks():
    got = work.finished([_manager(), _rec("g1", restart_wanted=WANTS), _rec("g2", out_of_work=OUT)])
    assert got["why"] == [] and got["restart"] is True and got["at"] == WANTS["at"]
    # exited or closed with the word: rule 2 has it, and a team about to have a member back is not finished
    for state in ("exited", "closed"):
        got = work.finished([_manager(), _rec("g1", state, restart_wanted=WANTS), _rec("g2", out_of_work=OUT)])
        assert got["why"] == [f"g1 {state}, restart wanted"]
    # once rule 2 has acted the old record is superseded, and passed over
    old = _rec("g1", "closed", restart_wanted=WANTS, superseded_by="ao-t-g1b")
    assert work.finished([_manager(), old, _rec("g1b", out_of_work=OUT)])["why"] == []


def test_the_dead_are_counted_blocking_or_passed_over():
    lead, g2 = _manager(), _rec("g2", out_of_work=OUT)
    # dead and declared: counted, its instant with the rest
    assert work.finished([lead, _rec("g1", "closed", out_of_work=LATER, pane=False), g2])["at"] == LATER["at"]
    # exited with its pane and no word: rule 1's crash blocks until that rule has acted
    assert work.finished([lead, _rec("g1", "exited", pane=True), g2])["why"] == ["g1 crashed"]
    # killed or closed with no word: passed over, as concluded passed it over
    for state, pane in (("exited", False), ("closed", False)):
        assert work.finished([lead, _rec("g1", state, pane=pane), g2])["why"] == []
    # nothing live at all: `wound_down`'s to describe
    assert work.finished([_rec("g1", "closed", out_of_work=OUT), _manager("exited")]) is None


def test_only_seats_and_the_manager_left_is_finished_with_no_instant():
    got = work.finished([_manager(), _seat(), _rec("g1", "closed", pane=False)])
    assert got == {"at": None, "restart": False, "names": ["manager", "techlead"], "why": []}
    assert work.finished([_manager("working"), _seat()])["why"] == ["manager working"]


def test_the_manager_is_read_from_the_records():
    lead, g1 = _manager(), _rec("g1", out_of_work=OUT)
    assert work.manager_of([lead, g1]) is lead
    # a person leads the team: nobody holds control, so every record is a member and must declare
    assert work.manager_of([g1, _rec("g2")]) is None
    assert work.finished([_rec("g1", out_of_work=OUT, controllers=[]), _rec("g2", controllers=[])])["why"] == [
        "g2 idle, not declared"
    ]
    # holding `control` with nobody listing it, or listed without the grant, is a member
    assert work.manager_of([_rec("x", capabilities=["control"], controllers=[]), _rec("y", controllers=[])]) is None
    assert work.manager_of([_rec("manager", controllers=[]), g1]) is None
    # manager → lead → worker: the lead holds control and is listed, and is a member under the manager
    sub = _rec("lead", capabilities=["control"])
    w = _rec("w", controllers=[sub.id], out_of_work=OUT)
    assert work.manager_of([sub, w, lead]) is lead
    assert work.finished([lead, sub, w])["why"] == ["lead idle, not declared"]


def test_a_dead_manager_is_passed_over_and_its_members_still_read():
    """A manager that exited is still the manager — never a member that crashed or must declare."""
    gone = _manager("exited", pane=True)
    assert work.manager_of([gone, _rec("g1")]) is gone
    assert work.finished([gone, _rec("g1", out_of_work=OUT)])["why"] == []
    assert work.finished([gone, _rec("g1")])["why"] == ["g1 idle, not declared"]


def test_a_start_replaces_a_dead_record_in_place_so_only_a_stray_one_blocks():
    """A member started again takes its old record's id (`_take_name`), so a dead `restart_wanted`
    blocks only while rule 2 has not acted — or on a record nothing will start again, which is a
    person's to Forget, and the clause names it."""
    stray = _rec("old-name", "closed", restart_wanted=WANTS, pane=False)
    got = work.finished([_manager(), _rec("g1", out_of_work=OUT), stray])
    assert got["why"] == ["old-name closed, restart wanted"]


def test_a_declaration_in_any_other_shape_is_none_and_never_a_raise():
    for junk in ("x", ["y"], 7, {"why": "no at"}):
        for key in ("out_of_work", "restart_wanted"):
            view = {"id": "ao-t-g1", "name": "g1", "team": "g", "state": "idle", key: junk}
            assert work.finished([view])["why"] == ["g1 idle, not declared"]
