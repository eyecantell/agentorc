"""TD-249 slice 3, design §4.9a *What counts toward the ceiling*: one count for rules 1, 2, 7 and 8 —
the `restarts` entries inside `RESTART_WINDOW`, leaving out a `wanted` restart that carried new work.
A crash, a fill, a failed replay, an early and a repeat restart count as they did."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sessionorc.agent_common import RESTART_CEILING, RESTART_WINDOW, _counted
from sessionorc.agent_tick import TickMixin
from sessionorc.models import Session

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _entry(minutes_ago: int, why: str = "wanted", done=None, **more) -> dict:
    entry = {"at": _iso(NOW - timedelta(minutes=minutes_ago)), "why": why, **more}
    if done is not None:
        entry.update(done=[{"ref": ref, "pr": pr} for ref, pr in done], left=[])
    return entry


def _rec(restarts: list) -> Session:
    return Session(
        id="ao-t-w", name="w", kind="interactive", adapter="shell", dir="/tmp/x", state="idle",
        created=_iso(NOW), restarts=restarts,
    )  # fmt: skip


@pytest.mark.unit
def test_four_clean_restarts_with_new_work_do_not_reach_the_ceiling_and_three_crashes_do():
    worked = [_entry(100 - 25 * i, done=[(f"TD-{i}", 800 + i)]) for i in range(4)]
    assert _counted(worked, NOW) == [] and not TickMixin._window_full(_rec(worked), NOW)
    crashes = [_entry(30 - i, "crash", done=[]) for i in range(RESTART_CEILING)]
    assert _counted(crashes, NOW) == crashes and TickMixin._window_full(_rec(crashes), NOW)
    # a crash counts whatever the run had reported: the ceiling guards a crash loop
    busy = [_entry(30 - i, "crash", done=[(f"TD-{i}", 900 + i)]) for i in range(RESTART_CEILING)]
    assert len(_counted(busy, NOW)) == RESTART_CEILING
    # and the clean restarts beside the crashes do not push it over
    assert len(_counted([*worked, *crashes[:2]], NOW)) == 2


@pytest.mark.unit
def test_a_wanted_restart_with_nothing_new_counts():
    nothing = _entry(50, done=[])  # an early one a person let run
    first = _entry(40, done=[("TD-1", 812)])
    again = _entry(30, done=[("TD-1", 812)])  # a repeat: the same pair an earlier entry holds
    by_ref = _entry(20, done=[("TD-1", None)])  # no pull request: the reference alone decides
    sliced = _entry(10, done=[("TD-1", 813)])  # a new pull request on the same entry is new work
    old = _entry(5)  # written before the fields: nothing says it carried work
    assert _counted([nothing, first, again, by_ref, sliced, old], NOW) == [nothing, again, by_ref, old]
    # a replay that failed counts, whatever the run had done
    failed = _entry(5, done=[("TD-7", 850)], error="create: boom")
    assert _counted([failed], NOW) == [failed]
    # what an entry is compared with is the window before its own time, as the declaration read it
    stale = _entry(40 + int(RESTART_WINDOW.total_seconds() // 60) + 1, done=[("TD-1", 812)])
    assert _counted([stale, first], NOW) == []


@pytest.mark.unit
def test_outside_the_window_nothing_counts_and_a_fill_counts_unless_left_out():
    gone = _entry(int(RESTART_WINDOW.total_seconds() // 60) + 1, "crash")
    fill = _entry(10, "fill")
    brief = _entry(5, "brief", done=[("TD-2", 820)])  # only a `wanted` restart is excused by its work
    assert _counted([gone, fill, brief, "junk"], NOW) == [fill, brief]
    assert _counted([gone, fill, brief], NOW, fills=False) == [brief]
