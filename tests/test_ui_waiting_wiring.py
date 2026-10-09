"""TD-481 (PR #1354, §4.2 *Waiting*, TD-428 slice 4): the *waiting* pill is composed by `view`, and
every surface that builds its own views must hand `view` the repo reading and the person-inbox waits —
the events stream's group heads and rollup (`heads`), the Inbox's state rows (`person_states`) and the
Repo page — or that surface reads *idle* while the Org card reads *waiting*."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

NOW = datetime.now(UTC).isoformat().replace("+00:00", "Z")


def reading(root: str) -> dict:
    return {
        "name": "samscrape",
        "root": root,
        "ledger": {"path": "docs/technical_debt.md", "entries": [], "by_priority": {}, "by_kind": {}},
        "prs": {"open": [{"number": 811, "title": "TD-301", "branch": "td301", "created": NOW, "author": "eye"}]},
        "at": NOW,
    }


def member(sid: str, root: str, **kw) -> dict:
    return {
        "id": sid,
        "name": sid,
        "state": "idle",
        "confidence": "hook",
        "since": NOW,
        "kind": "agent",
        "adapter": "shell",
        "team": "grind",
        "repo": root,
        "dir": root,
        "unattended": True,
        **kw,
    }


def client(monkeypatch, tmp_path):
    """A host agent stub: an idle member holding TD-301, whose PR #811 the repo reading holds open; one
    idle with an open ask to the person; a working one; `subscribe` yields the first one's delta once."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    root = str(tmp_path / "samscrape")
    waiting = member(
        "g1",
        root,
        progress=[{"ref": "TD-301", "status": "claimed", "at": NOW, "pr": 811}],
        supervised=True,
        idle_open={"at": NOW, "ref": "TD-301"},
    )
    fleet = [waiting, member("g2", root, state="working"), member("g3", root)]
    # g3 waits on the person: an open ask whose `about` names a reference (TD-274)
    ask = {"id": "m-a", "from": "g3", "to": ["person"], "kind": "ask", "about": "TD-222", "text": "q?", "at": NOW}
    replies = {
        "repos": {root: reading(root)},
        "doing_log": {"grind": [{"id": "g1", "text": "asked the techlead", "at": NOW}]},
        "list": fleet,
        "usage": {},
        "host": {"name": "kmaster"},
    }

    class Fake:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call(self, method, **kw):
            if method == "inbox":
                return {"entries": [] if kw.get("id") else [ask]}
            return replies.get(method, {})

        async def subscribe(self):
            yield {"event": "session", "session": waiting}

    from agentorc.ui import app as ui

    monkeypatch.setattr(ui, "LocalClient", Fake)
    monkeypatch.setattr(ui, "read_boards", lambda *a, **k: ([], ""))
    return TestClient(ui.create_app())


def test_the_events_streams_heads_and_rollup_read_waiting(tmp_path, monkeypatch):
    """`heads`: the group head's counts by state and the rollup's Agents pill read the delta's own view."""
    c = client(monkeypatch, tmp_path)
    with c.websocket_connect("/events") as ws:
        ev = json.loads(ws.receive_text())
    assert ev["event"] == "session" and ev["session"]["pill_word"] == "waiting"  # the card's own view
    (head,) = [g for g in ev["groups"] if g["team"] == "grind"]
    # both waits — the claim's open PR (`repos`) and the ask to the person (`waits`)
    assert '<span class="meta counts foldonly">· 1 working · 2 waiting</span>' in head["html"]
    assert 'data-state-filter="waiting"' in ev["rollup"] and "waiting (2)</button>" in ev["rollup"]
    assert "idle (1)" not in ev["rollup"]


def test_the_inbox_state_rows_read_waiting(tmp_path, monkeypatch):
    """`person_states`: the member's *idle · open work* row carries the Org card's pill, *waiting*."""
    html = client(monkeypatch, tmp_path).get("/inbox").text
    row = html[html.index('data-msg="g1:idle_open"') :]
    row = row[: row.index("</div>")]
    assert '<span class="pill s-waiting"' in row and "s-idle" not in row


def test_the_repo_page_builds_its_views_with_the_reading_and_the_waits(tmp_path, monkeypatch):
    """The Repo page draws no member pill today, so what it would show is not visible in its HTML; what
    is pinned is that its views are built as the Org's are, with the repo reading and the waits."""
    from agentorc.ui import app as ui

    c = client(monkeypatch, tmp_path)
    seen: list[dict] = []
    real = ui.view

    def spy(s, *a, **kw):
        seen.append(kw)
        return real(s, *a, **kw)

    monkeypatch.setattr(ui, "view", spy)
    assert c.get("/repo/samscrape").status_code == 200
    assert seen and all(kw.get("repos") and kw.get("waits") for kw in seen)
