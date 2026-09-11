"""The session record every layer agrees on: on-disk tolerance, ordering, `since` semantics."""

from typing import get_args

import pytest

from sessionorc.models import (
    STATE_RANK,
    FindingEntry,
    Pending,
    ProgressEntry,
    Session,
    State,
    normalize_ref,
    report_line,
)

pytestmark = pytest.mark.unit


def test_from_dict_ignores_unknown_keys_and_missing_pending():
    """A record written by a newer agent (extra fields) must still load."""
    d = {
        "id": "ao-r-n",
        "name": "n",
        "kind": "interactive",
        "adapter": "shell",
        "dir": "/r",
        "future_field": {"anything": 1},
    }
    s = Session.from_dict(d)
    assert s.id == "ao-r-n" and s.pending is None and not hasattr(s, "future_field")
    assert Session.from_dict({**d, "pending": None}).pending is None


def test_pending_roundtrip_and_defaults():
    p = Pending(kind="permission", text="Bash: git push", deadline="2026-09-07T00:00:00Z", tool_use_id="tu")
    assert Pending.from_dict(p.to_dict()) == p
    assert Pending.from_dict({"kind": "question"}) == Pending(kind="question", text="")


def test_session_roundtrip_is_lossless():
    s = Session(id="a", name="a", kind="command", adapter="command", dir="/", exit_code=3, subagents=2)
    s.set_state("needs-you", confidence="hook", pending=Pending(kind="question", text="q"))
    back = Session.from_dict(s.to_dict())
    assert back == s


def test_state_rank_covers_every_state():
    """The Herd sorts by STATE_RANK; a state without a rank falls to the bottom silently."""
    assert set(get_args(State)) == set(STATE_RANK)
    assert STATE_RANK["needs-you"] < STATE_RANK["working"] < STATE_RANK["idle"] < STATE_RANK["exited"]


def test_set_state_since_only_moves_on_change():
    s = Session(
        id="a", name="a", kind="interactive", adapter="shell", dir="/", state="idle", since="2000-01-01T00:00:00Z"
    )
    s.set_state("idle", confidence="scraped")
    assert s.since == "2000-01-01T00:00:00Z"
    s.set_state("working", confidence="scraped")
    assert s.since != "2000-01-01T00:00:00Z" and s.confidence == "scraped" and s.pending is None


def test_normalize_ref_canonicalises_the_two_machine_shapes():
    """`td-27` and `TD-027` are one entry, not two (design §4.8); prose is left alone."""
    assert normalize_ref("td-27") == normalize_ref(" TD-027 ") == "TD-027"
    assert normalize_ref("#59") == normalize_ref("59") == "#59"
    assert normalize_ref("user_attention: approve the  reclaim") == "user_attention: approve the reclaim"
    with pytest.raises(ValueError, match="needs a reference"):
        normalize_ref("   ")


def test_report_entries_upsert_by_ref_and_declared_wins():
    """§9 invariant 10: a declared entry is never overwritten by a derived one; a declaration
    replaces a derived entry for the same reference the moment it arrives."""
    s = Session(id="a", name="a", kind="interactive", adapter="shell", dir="/")
    assert s.report_progress(ProgressEntry(ref="TD-027", source="derived"))
    assert s.report_progress(ProgressEntry(ref="TD-027", status="done", pr=60)) is True
    assert [(p.ref, p.status, p.source) for p in s.progress] == [("TD-027", "done", "declared")]
    assert s.report_progress(ProgressEntry(ref="TD-027", status="claimed", source="derived")) is False
    assert s.progress[0].status == "done"  # the declaration stands
    assert s.report_progress(ProgressEntry(ref="TD-019", source="derived"))
    assert [p.ref for p in s.progress] == ["TD-027", "TD-019"]  # order of arrival, upserted in place
    assert s.report_finding(FindingEntry(ref="TD-029", priority="low"))
    assert s.report_finding(FindingEntry(ref="TD-029", priority="high", source="scraped")) is False
    assert s.findings[0].priority == "low"


def test_report_entries_survive_the_store_roundtrip():
    s = Session(id="a", name="a", kind="interactive", adapter="shell", dir="/", lane=["TD-027", "TD-019"])
    s.report_progress(ProgressEntry(ref="TD-027", status="done", pr=60))
    s.report_finding(FindingEntry(ref="TD-029", priority="low", source="derived"))
    d = s.to_dict()
    assert d["progress"][0]["pr"] == 60 and d["findings"][0]["source"] == "derived"
    assert Session.from_dict(d) == s


def test_report_line_shows_the_reference_the_pr_and_the_lane_count():
    """Design §4.8's card line: `TD-027 → #60 · 1/2 done`, `~` for what the agent derived."""
    s = Session(id="a", name="a", kind="interactive", adapter="shell", dir="/")
    assert report_line(s.to_dict()) == ""
    s.lane = ["TD-027", "TD-019"]
    assert report_line(s.to_dict()) == "0/2 done"
    s.report_progress(ProgressEntry(ref="TD-027", status="done", pr=60))
    s.report_progress(ProgressEntry(ref="TD-019"))
    assert report_line(s.to_dict()) == "TD-019 · 1/2 done"  # what is in hand, not what is finished
    s.progress[1] = ProgressEntry(ref="TD-019", source="derived")
    assert report_line(s.to_dict()) == "TD-019~ · 1/2 done"
    s.progress[1] = ProgressEntry(ref="TD-019", status="done", pr=61)
    assert report_line(s.to_dict()) == "TD-019 → #61 · 2/2 done"
    # `free-pick` is not a countable lane: the count falls back to the entries themselves
    free = Session(id="b", name="b", kind="interactive", adapter="shell", dir="/", lane=["free-pick"])
    free.report_progress(ProgressEntry(ref="TD-025", status="done", pr=59))
    assert report_line(free.to_dict()) == "TD-025 → #59 · 1/1 done"
