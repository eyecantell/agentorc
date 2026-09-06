from sessionorc.models import Pending, Session
from sessionorc.store import EventQueue, SessionStore


def test_session_roundtrip(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    s = Session(id="ao-r-n", name="n", kind="interactive", adapter="claude-code", dir="/r", profile="p")
    s.set_state("needs-you", confidence="hook", pending=Pending(kind="permission", text="Bash: git push"))
    store.save(s)
    back = store.load("ao-r-n")
    assert back is not None and back.pending is not None
    assert back.state == "needs-you" and back.pending.text == "Bash: git push" and back.confidence == "hook"
    assert list(store.load_all()) == ["ao-r-n"]
    store.delete("ao-r-n")
    assert store.load("ao-r-n") is None


def test_load_all_skips_garbage(tmp_path):
    root = tmp_path / "s"
    root.mkdir()
    (root / "bad.json").write_text("{not json")
    (root / "empty.json").write_text("{}")
    assert SessionStore(root).load_all() == {}


def test_set_state_keeps_since_when_unchanged():
    s = Session(id="a", name="a", kind="interactive", adapter="shell", dir="/", state="idle")
    since = s.since
    s.set_state("idle", confidence="scraped")
    assert s.since == since


def test_event_queue_drain_order(tmp_path):
    q = EventQueue(tmp_path / "events")
    q.append("s1", {"event": "Stop"})
    q.append("s1", {"event": "UserPromptSubmit"})
    q.append("s0", {"event": "SessionEnd"})
    drained = q.drain()
    assert [sid for sid, _ in drained] == ["s0", "s1", "s1"]
    assert [e["event"] for _, e in drained] == ["SessionEnd", "Stop", "UserPromptSubmit"]
    assert q.drain() == []
