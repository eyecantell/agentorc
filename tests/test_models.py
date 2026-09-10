"""The session record every layer agrees on: on-disk tolerance, ordering, `since` semantics."""

from typing import get_args

import pytest

from sessionorc.models import STATE_RANK, Pending, Session, State

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
