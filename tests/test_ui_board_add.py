"""*Put on the board* on the Inbox page (design §4.5a *Inbox row: FYI*, §4.4 the write-back's one
add; TD-140 slice 2): the button on FYI rows only, the form it opens, and the route to `board_edit`
with `action: add`. The RPC itself is `tests/test_board.py`'s."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_ui_board import host, repo
from test_ui_inbox import entry, rec, rows


@pytest.mark.unit
def test_the_button_is_on_fyi_rows_and_trail_rows_and_never_on_an_open_question():
    """§4.5a: drawn on FYI rows only — a `note`, a closed question, a trail row — and never on an
    open `ask` or `steer`, whose *later* is Snooze; nor on *answered for you* or *Waiting on them*."""
    b = "/r/docs/user_attention.md"
    note = rows("fyi", [entry("m-1", "note", text="check <this> Friday", board_default=b)])
    assert note.count('data-act="board_add"') == 1
    assert 'data-msg="m-1"' in note and f'data-board="{b}"' in note
    assert 'data-text="check &lt;this&gt; Friday"' in note  # the text rides escaped, as an attribute
    closed = rows("fyi", [entry("m-2", "ask", closed_reason="replied", closed_by="person")])
    assert closed.count('data-act="board_add"') == 1 and 'data-board=""' in closed
    trail = rows("fyi", [{"row": "trail", "id": "t-1", "kind": "stalled", "text": "went quiet", "sid": "ao-w1"}])
    assert trail.count('data-act="board_add"') == 1 and 'data-msg="t-1"' in trail
    assert 'data-act="board_add"' not in rows("needs", [entry("m-3", "ask")])
    assert 'data-act="board_add"' not in rows("steering", [entry("m-4", "steer", default="go on")])
    answered = entry("m-5", "note", answered={"question": "q", "asker": "ao-w2", "source": "x"})
    assert 'data-act="board_add"' not in rows("answered", [answered])
    assert 'data-act="board_add"' not in rows("waiting", [entry("m-6", "ask", closed_reason="replied", owes=True)])
    assert 'data-act="board_add"' not in rows("snoozed", [entry("m-7", "note", snoozed_until="2026-09-30T08:00:00Z")])


@pytest.mark.unit
def test_the_choices_are_the_registered_repos_that_carry_a_board(tmp_path, monkeypatch):
    """The form's pick (§4.5a): the host's registered repos with a `docs/user_attention.md`, resolved
    as `board_edit` resolves them; a repo with no board is not offered."""
    a, b = repo(tmp_path, "alpha", "# Board\n"), repo(tmp_path, "beta")
    host(tmp_path, monkeypatch, repos=[a, b])
    from agentorc.ui.app import board_choices

    got = board_choices()
    assert got == [{"label": "alpha", "root": str(a.resolve()), "board": str(a.resolve() / "docs/user_attention.md")}]


class Fake:
    """The host agent as the UI's routes see it: `list`, `inbox` and `board_edit`."""

    calls: list = []
    fleet: list = []
    inbox: dict = {"entries": [], "trail": []}

    def __init__(self, *a, **k):
        self.kw = k

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def call(self, method, **kw):
        if method == "board_edit":
            Fake.calls.append((self.kw.get("caller"), kw))
            return {"board": kw["board"], "action": "add", "commit": "abc123", "line": 5, "dismissed": [kw["entry"]]}
        return {
            "list": Fake.fleet,
            "inbox": Fake.inbox,
            "usage": {},
            "gate": {},
            "host": {"name": "kmaster", "mode": "home"},
            "identity": {},
        }.get(method, {})


@pytest.mark.unit
def test_the_form_defaults_to_the_senders_repos_board_and_the_page_carries_it(tmp_path, monkeypatch):
    """The board is the sender's repo's when this host knows it (its record's `repo`), and nothing —
    the person picks — for a `system` note or a repo with no board. The page draws the form with
    every choice; the poll's rows carry the default the button opens it with."""
    a = repo(tmp_path, "alpha", "# Board\n")
    host(tmp_path, monkeypatch, repos=[a])
    from agentorc.ui import app as uiapp

    monkeypatch.setattr(uiapp, "read_boards", lambda run=None: ([], ""))
    board = str(a.resolve() / "docs/user_attention.md")
    Fake.fleet = [rec("ao-w1", "idle", repo=str(a)), rec("ao-w2", "idle", repo="/elsewhere")]
    Fake.inbox = {
        "entries": [
            entry("m-1", "note", text="from alpha"),
            entry("m-2", "note", from_="ao-w2", text="from elsewhere"),
            entry("m-3", "note", from_="system", text="from the home"),
        ],
        "trail": [
            {"id": "t-1", "sid": "ao-w1", "kind": "stalled", "text": "went quiet", "last": "2026-09-19T09:00:00Z"}
        ],
    }
    monkeypatch.setattr(uiapp, "LocalClient", Fake)
    with TestClient(uiapp.create_app()) as c:
        page = c.get("/inbox").text
        assert 'id="boardadd"' in page and f'<option value="{board}"' in page and ">alpha<" in page
        fyi = c.get("/api/person/inbox").json()["html"]["fyi"]
    by_id = {
        m: fyi[fyi.index(f'data-act="board_add" data-id="person" data-msg="{m}"') :].split(">")[0]
        for m in ("m-1", "m-2", "m-3", "t-1")
    }
    assert f'data-board="{board}"' in by_id["m-1"] and f'data-board="{board}"' in by_id["t-1"]
    assert 'data-board=""' in by_id["m-2"] and 'data-board=""' in by_id["m-3"]


@pytest.mark.unit
def test_a_host_with_no_board_says_so_in_the_form(tmp_path, monkeypatch):
    host(tmp_path, monkeypatch)
    from agentorc.ui import app as uiapp

    monkeypatch.setattr(uiapp, "read_boards", lambda run=None: ([], ""))
    Fake.fleet, Fake.inbox = [], {"entries": [], "trail": []}
    monkeypatch.setattr(uiapp, "LocalClient", Fake)
    with TestClient(uiapp.create_app()) as c:
        page = c.get("/inbox").text
    assert 'id="banone"' in page and 'id="bago"' not in page


@pytest.mark.unit
def test_put_it_on_goes_to_the_write_backs_one_add_and_the_boards_are_read_again(tmp_path, monkeypatch):
    """The route hands the form's board, text and Due and the entry's id to `board_edit` with
    `action: add`, caller-less — the person's own act — and reads the boards again at once, so the
    new item is a row the moment it is due. What the form must name is refused before the agent."""
    host(tmp_path, monkeypatch)
    from agentorc.ui import app as uiapp

    reads = []
    monkeypatch.setattr(uiapp, "read_boards", lambda run=None: (reads.append(1), ([], ""))[1])
    Fake.calls, Fake.fleet, Fake.inbox = [], [], {"entries": [], "trail": []}
    monkeypatch.setattr(uiapp, "LocalClient", Fake)
    board = str(tmp_path / "r/docs/user_attention.md")
    good = {"action": "add", "msg": "m-1", "board": board, "text": "check it", "due": "2026-09-30"}
    with TestClient(uiapp.create_app()) as c:
        c.get("/api/person/inbox")
        r = c.post("/api/person/board", json=good)
        assert r.status_code == 200 and r.json()["commit"] == "abc123" and r.json()["dismissed"] == ["m-1"]
        assert len(reads) == 2
        for k in ("msg", "board", "text", "due"):
            assert c.post("/api/person/board", json={**good, k: ""}).status_code == 400
    assert Fake.calls == [
        (None, {"board": board, "action": "add", "text": "check it", "due": "2026-09-30", "entry": "m-1"})
    ]


@pytest.mark.unit
def test_the_script_opens_the_form_and_draws_a_refusal_in_it():
    """The client half (§4.5a): the button opens `#boardadd` rather than posting, the post is the
    board route's `add`, and a refusal is written into the form (`#baerr`) — the form stays open."""
    import pathlib

    js = (pathlib.Path(__file__).parents[1] / "src/agentorc/ui/static/app.js").read_text()
    fn = js[js.index("AO.boardAdd = function") : js.index('document.addEventListener("click", async (ev)')]
    assert '$("#boardadd")' in fn and 'act("person", "board", body)' in fn and 'action: "add"' in fn
    assert "err.textContent = `not put on the board: ${e.message}`" in fn
    assert 'if (action === "board_add")' in js and "await AO.boardAdd(b)" in js
