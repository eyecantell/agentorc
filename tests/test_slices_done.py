"""TD-325 slice 1, design §4.8 (the `progress` channel) and §4.9a *A slice is work done*: a merged
PR on an entry still claimed is held in the claim's `slices` — declared with `--slice` or derived by
the tick — and a run's `done` holds them, so a run that lands a slice each time is never early,
never a repeat and never counted toward the ceiling, while three runs that merge nothing still are."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks

from sessionorc.agent_common import RESTART_EARLY, _counted, _reported, _restart_reading
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import PR_CLOSED, ProgressEntry, Session

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
SHORT, LONG = timedelta(minutes=20), RESTART_EARLY + timedelta(minutes=1)


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _rec(age: timedelta, restarts=(), slices=(), status="claimed") -> Session:
    """A run `age` old holding a declared claim on TD-1 with `slices` — `(pr, source)` pairs written
    a minute ago — and the `restarts` entries the runs before it left."""
    claim = ProgressEntry(ref="TD-1", status=status, at=_iso(NOW - age))
    claim.slices = [{"pr": pr, "at": _iso(NOW - timedelta(minutes=1)), "source": src} for pr, src in slices]
    return Session(
        id="ao-t-w", name="w", kind="interactive", adapter="shell", dir="/tmp/x", state="idle",
        created=_iso(NOW - age), progress=[claim], restarts=list(restarts),
    )  # fmt: skip


def _replay(s: Session, ago: timedelta) -> dict:
    """The `restarts` entry the tick writes when it replaces run `s` (`_replay`), dated `ago`."""
    return {"at": _iso(NOW - ago), "why": "wanted", **_reported(s, NOW - ago)}


@pytest.mark.unit
def test_a_derived_merge_on_a_declared_claim_is_held_once_as_a_slice():
    claim = ProgressEntry(ref="TD-1")
    s = Session(id="ao-t-w", name="w", kind="interactive", adapter="shell", dir="/tmp/x", progress=[claim])
    merged = ProgressEntry(ref="TD-1", status="done", pr=1025, source="derived")
    assert s.report_progress(merged) is False  # refused whole, as ever (§9 invariant 10)…
    assert s.note_review(merged) is True  # …but its PR is held beside the claim
    assert claim.status == "claimed" and claim.pr is None and [x["pr"] for x in claim.slices] == [1025]
    assert claim.slices[0]["source"] == "derived"
    assert s.note_review(merged) is False  # every tick derives it again: held once
    # a PR closed unmerged writes nothing; an open one is `review_pr`, as before
    assert s.note_review(ProgressEntry(ref="TD-1", status="claimed", pr=1026, source="derived", why=PR_CLOSED)) is False
    assert s.note_review(ProgressEntry(ref="TD-1", status="claimed", pr=1027, source="derived")) is True
    assert claim.review_pr == 1027 and len(claim.slices) == 1
    # a claim declared with its own `--pr` that merges holds it once as a derived slice: the session
    # that forgets `--slice` is still counted (§4.9a *A slice is work done*; techlead on #1055)
    own = Session(
        id="ao-t-v",
        name="v",
        kind="interactive",
        adapter="shell",
        dir="/tmp/x",
        progress=[ProgressEntry(ref="TD-2", pr=900)],
    )
    assert own.note_review(ProgressEntry(ref="TD-2", status="done", pr=900, source="derived")) is True
    assert [(x["pr"], x["source"]) for x in own.progress[0].slices] == [(900, "derived")]
    assert own.note_review(ProgressEntry(ref="TD-2", status="done", pr=900, source="derived")) is False
    assert own.progress[0].status == "claimed" and own.progress[0].pr == 900
    # declared after it was derived: held once, now the session's word
    assert claim.add_slice(1025, "declared") is True and claim.slices == [{**claim.slices[0], "source": "declared"}]
    assert claim.add_slice(1025, "derived") is False and claim.slices[0]["source"] == "declared"
    # a re-claim and the last slice's plain `done` keep the slices
    s.report_progress(ProgressEntry(ref="TD-1"))
    s.report_progress(ProgressEntry(ref="TD-1", status="done", pr=1030))
    assert [x["pr"] for x in s.progress[0].slices] == [1025] and s.progress[0].status == "done"
    # and they are kept in the record's file
    assert ProgressEntry.from_dict(s.progress[0].to_dict()).slices == s.progress[0].slices


@pytest.mark.unit
def test_a_runs_done_holds_its_slices_and_a_reference_with_a_new_one_is_not_left():
    s = _rec(SHORT, slices=[(1025, "declared"), (1027, "derived")])
    got = _reported(s, NOW)
    assert got["done"] == [{"ref": "TD-1", "pr": 1025, "slice": True}, {"ref": "TD-1", "pr": 1027, "slice": True}]
    assert got["left"] == []
    # a slice written before this record began is an earlier run's
    s.progress[0].slices[0]["at"] = s.progress[0].slices[1]["at"] = _iso(NOW - timedelta(hours=3))
    assert _reported(s, NOW) == {"done": [], "left": ["TD-1"]}
    # a slice re-derived after a replay is a pair an earlier entry holds: not new, the reference still left
    earlier = [{"at": _iso(NOW - timedelta(minutes=30)), "why": "wanted", "done": [{"ref": "TD-1", "pr": 1025}]}]
    again = _rec(SHORT, earlier, slices=[(1025, "derived")])
    assert _reported(again, NOW)["left"] == ["TD-1"]
    assert _restart_reading(again, NOW) == {
        "early": True,
        "repeat": None,
        "words": "early: nothing reported done this run",
    }


@pytest.mark.unit
def test_three_runs_each_merging_a_slice_are_never_early_a_repeat_or_counted():
    """TD-321's *Fix* 4, the case it was seen on: TD-309 across three runs, a slice merged in each."""
    restarts: list[dict] = []
    for n, pr in enumerate((1025, 1027, 1029)):
        run = _rec(SHORT, restarts, slices=[(pr, "declared" if n % 2 else "derived")])
        got = _restart_reading(run, NOW)
        assert got["early"] is False and got["repeat"] is None, (n, got)
        assert got["words"] == f"not early: TD-1 #{pr} reported done this run"
        restarts.append(_replay(run, timedelta(minutes=3 * (3 - n))))
    assert _counted(restarts, NOW) == []
    assert all(r["left"] == [] for r in restarts)


@pytest.mark.unit
def test_three_runs_merging_nothing_are_still_a_repeat_on_the_third():
    restarts: list[dict] = []
    for n in range(2):
        run = _rec(LONG, restarts)
        assert _restart_reading(run, NOW)["repeat"] is None
        restarts.append(_replay(run, timedelta(minutes=10 - n)))
    third = _restart_reading(_rec(LONG, restarts), NOW)
    assert third["repeat"] == {"ref": "TD-1"} and third["words"] == "repeats TD-1: claimed and left three runs running"
    assert len(_counted(restarts, NOW)) == 2
    # a run that landed a slice, then two that land none: the repeat is one run later (§4.9a)
    restarts = [_replay(_rec(SHORT, slices=[(1025, "declared")]), timedelta(minutes=9))]
    restarts.append(_replay(_rec(LONG, restarts), timedelta(minutes=8)))
    assert _restart_reading(_rec(LONG, restarts), NOW)["repeat"] is None
    restarts.append(_replay(_rec(LONG, restarts), timedelta(minutes=7)))
    assert _restart_reading(_rec(LONG, restarts), NOW)["repeat"] == {"ref": "TD-1"}


@pytest.mark.unit
def test_a_declared_slice_the_last_run_reported_too_is_a_repeat():
    """The *reported done by an earlier run too* test reads a declared slice as it reads a declared
    `done`, and never a derived one."""
    earlier = [{"at": _iso(NOW - timedelta(minutes=30)), "why": "wanted", "done": [{"ref": "TD-1", "pr": 1025}]}]
    got = _restart_reading(_rec(LONG, earlier, slices=[(1025, "declared")]), NOW)
    assert got["repeat"] == {"ref": "TD-1"} and got["words"] == "repeats TD-1: reported done by an earlier run too"
    assert _restart_reading(_rec(LONG, earlier, slices=[(1025, "derived")]), NOW)["repeat"] is None


@pytest.mark.integration
async def test_progress_done_with_slice_holds_the_pr_and_the_claim_stays(agent, tmp_path):
    await park_ticks(agent)
    made = {"dir": str(tmp_path), "adapter": "shell", "argv": ["bash", "--norc", "--noprofile"], "unattended": True}
    async with LocalClient() as person:
        sid = (await person.call("create", name="w", **made))["id"]
        rec = agent.sessions[sid]
        async with LocalClient(caller=sid) as w:
            with pytest.raises(AgentError, match="holds no claim of yours"):
                await w.call("progress", id=sid, ref="TD-1", status="done", pr=1025, slice=True)
            await w.call("progress", id=sid, ref="TD-1")
            with pytest.raises(AgentError, match="a slice is a merged PR"):
                await w.call("progress", id=sid, ref="TD-1", status="done", slice=True)
            with pytest.raises(AgentError, match="--slice goes with"):
                await w.call("progress", id=sid, ref="TD-1", status="dropped", pr=1025, slice=True)
            got = await w.call("progress", id=sid, ref="TD-1", status="done", pr=1025, slice=True)
            assert "stays claimed" in got["slice"] and "refused" not in got
            (claim,) = rec.progress
            assert claim.status == "claimed" and claim.pr is None
            assert [(x["pr"], x["source"]) for x in claim.slices] == [(1025, "declared")]
            again = await w.call("progress", id=sid, ref="TD-1", status="done", pr=1025, slice=True)
            assert "refused" not in again and len(claim.slices) == 1
            # the view carries them, for the panel and `ao status --json`
            assert again["progress"][0]["slices"][0]["pr"] == 1025
        await person.call("kill", id=sid)
