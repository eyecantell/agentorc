"""TD-249 slice 2, design §4.9a *Early is decided from the record*, *Repeated work is the person's at
once*: a `restart` inside `RESTART_EARLY` is early only when the run reported nothing new; one whose
`done` an earlier run in the ceiling's window already reported, or that leaves a claim the last two
runs left, carries `repeat: {ref}` beside `early`; and the reply names what decided it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks

from sessionorc.agent_common import RESTART_EARLY, RESTART_WINDOW, _restart_reading
from sessionorc.client import LocalClient
from sessionorc.models import ProgressEntry, Session

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _rec(age: timedelta, progress=(), restarts=()) -> Session:
    made = [ProgressEntry(at=_iso(NOW - timedelta(minutes=1)), **p) for p in progress]
    return Session(
        id="ao-t-w", name="w", kind="interactive", adapter="shell", dir="/tmp/x", state="idle",
        created=_iso(NOW - age), progress=made, restarts=list(restarts),
    )  # fmt: skip


def _entry(ago: timedelta, done=(), left=(), why="wanted") -> dict:
    return {"at": _iso(NOW - ago), "why": why, "done": [dict(d) for d in done], "left": list(left)}


SHORT, LONG = timedelta(minutes=20), RESTART_EARLY + timedelta(minutes=1)
DONE = {"ref": "TD-1", "status": "done", "pr": 812}


@pytest.mark.unit
def test_early_is_a_short_run_with_nothing_new_and_never_one_with_new_work():
    assert _restart_reading(_rec(SHORT), NOW) == {
        "early": True,
        "repeat": None,
        "words": "early: nothing reported done this run",
    }
    # a claim alone is not work done, and neither is a context reading: the `why` is never read
    assert _restart_reading(_rec(SHORT, [{"ref": "TD-1"}]), NOW)["early"] is True
    got = _restart_reading(_rec(SHORT, [DONE]), NOW)
    assert got == {"early": False, "repeat": None, "words": "not early: TD-1 #812 reported done this run"}
    # past the thirty minutes nothing is said and nothing marked, new work or none
    for progress in ([], [DONE]):
        assert _restart_reading(_rec(LONG, progress), NOW) == {"early": False, "repeat": None, "words": None}
    # a `done` from before this record began is an earlier run's
    old = _rec(SHORT, [DONE])
    old.progress[0].at = _iso(NOW - timedelta(hours=3))
    assert _restart_reading(old, NOW)["early"] is True


@pytest.mark.unit
def test_what_an_earlier_run_reported_is_a_repeat_whenever_it_is_declared():
    before = _entry(timedelta(minutes=40), done=[{"ref": "TD-1", "pr": 812}])
    for age in (SHORT, LONG):
        got = _restart_reading(_rec(age, [DONE], [before]), NOW)
        assert got["early"] is True and got["repeat"] == {"ref": "TD-1"}, age
        assert got["words"] == "repeats TD-1: reported done by an earlier run too"
    # a slice of the same entry under a new pull request is new work; with no `pr` the reference decides
    assert _restart_reading(_rec(SHORT, [{**DONE, "pr": 813}], [before]), NOW)["repeat"] is None
    assert _restart_reading(_rec(SHORT, [{**DONE, "pr": None}], [before]), NOW)["repeat"] == {"ref": "TD-1"}
    # one new `done` among repeats is new work
    both = _rec(SHORT, [DONE, {"ref": "TD-2", "status": "done", "pr": 820}], [before])
    assert _restart_reading(both, NOW) == {
        "early": False,
        "repeat": None,
        "words": "not early: TD-2 #820 reported done this run",
    }
    # outside the ceiling's window the earlier report is not held against it
    stale = _entry(RESTART_WINDOW + timedelta(minutes=1), done=[{"ref": "TD-1", "pr": 812}])
    assert _restart_reading(_rec(SHORT, [DONE], [stale]), NOW)["repeat"] is None


@pytest.mark.unit
def test_a_claim_left_by_the_last_two_runs_and_left_again_is_a_repeat():
    two = [_entry(timedelta(minutes=80), left=["TD-9"]), _entry(timedelta(minutes=40), left=["TD-9", "TD-3"])]
    got = _restart_reading(_rec(LONG, [{"ref": "TD-9"}], two), NOW)
    assert (got["early"], got["repeat"]) == (True, {"ref": "TD-9"})
    assert got["words"] == "repeats TD-9: claimed and left three runs running"
    # new work beside it does not excuse it; a claim closed this run is not left
    assert _restart_reading(_rec(LONG, [{"ref": "TD-9"}, {**DONE, "pr": 900}], two), NOW)["repeat"] == {"ref": "TD-9"}
    closed = [{"ref": "TD-9", "status": "done", "pr": 901}]
    assert _restart_reading(_rec(LONG, closed, two), NOW)["repeat"] is None
    # left by one run only, or by the one before last only: not yet
    assert _restart_reading(_rec(LONG, [{"ref": "TD-3"}], two), NOW)["repeat"] is None
    assert _restart_reading(_rec(LONG, [{"ref": "TD-9"}], two[:1]), NOW)["repeat"] is None
    before_last = [two[0], _entry(timedelta(minutes=40), left=["TD-3"])]
    assert _restart_reading(_rec(LONG, [{"ref": "TD-9"}], before_last), NOW)["repeat"] is None
    # an entry with no `left` (written before the field, or by a close that failed) says nothing
    bare = [two[0], {"at": two[1]["at"], "why": "wanted", "error": "close: x"}]
    assert _restart_reading(_rec(LONG, [{"ref": "TD-9"}], bare), NOW)["repeat"] is None
    # and one with no `done` holds no earlier report against a run's own
    assert _restart_reading(_rec(SHORT, [DONE], bare[1:]), NOW)["repeat"] is None


@pytest.mark.integration
async def test_the_declaration_writes_the_reading_and_the_reply_names_it(agent, tmp_path):
    await park_ticks(agent)
    made = {"dir": str(tmp_path), "adapter": "shell", "argv": ["bash", "--norc", "--noprofile"], "unattended": True}
    async with LocalClient() as person:
        sid = (await person.call("create", name="w", **made))["id"]
        rec = agent.sessions[sid]
        async with LocalClient(caller=sid) as w:
            got = await w.call("progress", id=sid, status="restart", why="context bound")
            assert got["restart_wanted"]["early"] is True and "repeat" not in got["restart_wanted"]
            assert got["decided"] == "early: nothing reported done this run"

            await w.call("progress", id=sid, ref="TD-1", status="done", pr=812)
            got = await w.call("progress", id=sid, status="restart", why="context bound")
            assert "early" not in got["restart_wanted"]
            assert got["decided"] == "not early: TD-001 #812 reported done this run"

            rec.restarts = [_entry(timedelta(0), done=[{"ref": "TD-001", "pr": 812}])]
            rec.restarts[0]["at"] = _iso(datetime.now(UTC))
            got = await w.call("progress", id=sid, status="restart", why="context bound")
            assert got["restart_wanted"]["early"] is True and got["restart_wanted"]["repeat"] == {"ref": "TD-001"}
            assert got["decided"].startswith("repeats TD-001")
            # a restart its changed brief asked for is neither (§6 rule 7)
            rec.brief_changed = {"at": "2026-09-30T00:00:00Z"}
            got = await w.call("progress", id=sid, status="restart", why="the brief changed")
            assert set(got["restart_wanted"]) == {"at", "why"} and "decided" not in got
        await person.call("kill", id=sid)
