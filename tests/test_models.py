"""The session record every layer agrees on: on-disk tolerance, ordering, `since` semantics."""

import json
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
    # the role badge and the ledger path the client read at create (design §4.8, §5) ride on the record
    s = Session(id="b", name="b", kind="interactive", adapter="claude-code", dir="/", role="grinder", ledger="d/l.md")
    assert Session.from_dict(s.to_dict()) == s
    assert Session.from_dict({"id": "c", "name": "c", "kind": "interactive", "adapter": "x", "dir": "/"}).role == ""


def test_state_rank_covers_every_state():
    """The Org sorts by STATE_RANK; a state without a rank falls to the bottom silently."""
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


def test_retire_branch_claims_takes_only_the_derived_prless_claims_it_is_given():
    """TD-045: the one delete in the report channels, and it is narrow — a `done` entry is a fact
    about the past, an entry with a PR is still being re-checked by number, and a declaration is the
    session's own word (§9 invariant 10)."""
    s = Session(id="a", name="a", kind="interactive", adapter="shell", dir="/")
    s.report_progress(ProgressEntry(ref="TD-001", source="derived", branch="td001-a"))  # retireable
    s.report_progress(ProgressEntry(ref="TD-002", source="derived", pr=2, branch="td002-b"))  # has a PR
    s.report_progress(ProgressEntry(ref="TD-003", status="done", source="derived", branch="td003-c"))  # done
    s.report_progress(ProgressEntry(ref="TD-004"))  # declared
    assert s.retire_branch_claims([]) is False
    assert s.retire_branch_claims(["TD-005"]) is False  # a reference it does not hold
    assert s.retire_branch_claims(["TD-002", "TD-003", "TD-004"]) is False  # none of them qualify
    assert [p.ref for p in s.progress] == ["TD-001", "TD-002", "TD-003", "TD-004"]
    assert s.retire_branch_claims(["TD-001", "TD-004"]) is True
    assert [p.ref for p in s.progress] == ["TD-002", "TD-003", "TD-004"]  # order kept, declaration kept


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


def test_every_record_field_has_exactly_one_owner():
    """Design §4.4a, §9 invariant 15: merges go by owner, never by last write, so every field of
    the record is node-owned, home-owned or identity — one set, never two, never none."""
    from sessionorc.models import HOME_OWNED, IDENTITY, NODE_OWNED

    fields = set(Session.__dataclass_fields__) - {"rev"}
    assert fields == NODE_OWNED | HOME_OWNED | IDENTITY
    assert not (NODE_OWNED & HOME_OWNED) and not (NODE_OWNED & IDENTITY) and not (HOME_OWNED & IDENTITY)


def test_mail_survives_the_store_roundtrip_and_stays_out_of_the_view():
    """Design §4.10: the inbox, the sender's copy, the tallies and `sends` are persisted with the
    record; `view()` — what `list`, `get` and the push carry — drops the bodies and carries counts."""
    from sessionorc.models import MailEntry, SendEntry, Tally

    s = Session(id="ao-x", name="x", kind="interactive", adapter="shell", dir="/tmp", host="h1")
    ask = MailEntry(
        id="m-1",
        from_="ao-lead",
        to=["ao-x"],
        at="2026-09-16T10:00:00Z",
        kind="ask",
        text="?",
        bound="2026-09-17T10:00:00Z",
    )
    s.inbox.append(ask)
    s.outbox.append(
        MailEntry(id="m-2", from_="ao-x", to=["ao-lead"], at="2026-09-16T10:01:00Z", kind="note", text="fyi")
    )
    s.threads["m-1"] = Tally(count=1)
    s.sends.append(SendEntry(id="s-1", from_="person", at="2026-09-16T09:00:00Z", text="echo hi"))
    d = s.to_dict()
    assert d["inbox"][0]["from"] == "ao-lead" and "from_" not in d["inbox"][0] and d["sends"][0]["from"] == "person"
    back = Session.from_dict(json.loads(json.dumps(d)))
    assert back == s and back.inbox[0].root == "m-1" and back.inbox[0].open
    v = s.view()
    assert "inbox" not in v and "outbox" not in v
    assert (
        v["unread"] == 1 and v["host"] == "h1" and v["threads"] == {"m-1": {"count": 1, "bound_hit": False, "at": []}}
    )
    assert v["mail"]["open_asks"] == ["m-1"] and v["sends"][0]["id"] == "s-1"
    ask.closed_by = "m-3"
    assert not ask.open and s.mail_marks()["open_asks"] == []
