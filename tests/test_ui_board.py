"""Board items in the Inbox (design §4.5 screen 6, §4.4; TD-069 step 3): the due items of each
repo's `docs/user_attention.md`, read by dev-cadence's own reader, as counted **Needs you** rows."""

from __future__ import annotations

import pathlib
import shutil
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_ui_inbox import needs_line

READER = pathlib.Path(__file__).parents[1] / "scripts" / "nudge_user_attention.py"


def host(tmp_path, monkeypatch, repos=()):
    """A host whose roster lists `repos`; returns the roster file."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    roster = tmp_path / "repos.txt"
    roster.write_text("".join(f"{r}\n" for r in repos))
    (tmp_path / "home" / "hosts.yml").write_text(
        f"local:\n  name: kmaster\n  local: true\n  repos_registry: {roster}\n"
    )
    return roster


def repo(tmp_path, name, board=None, *, reader=True):
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    if board is not None:
        (root / "docs").mkdir()
        (root / "docs" / "user_attention.md").write_text(board)
    if reader:
        (root / "scripts").mkdir()
        shutil.copy(READER, root / "scripts" / "nudge_user_attention.py")
    return root


def report(root, *items):
    return {
        "today": "2026-09-22",
        "due_only": True,
        "boards": [
            {
                "label": pathlib.Path(root).name,
                "board": f"{root}/docs/user_attention.md",
                "root": str(root),
                "items": list(items),
            }
        ],
    }


def item(line, text, due, tag):
    return {"line": line, "text": text, "due": due, "overdue_days": 1, "due_tag": tag}


@pytest.mark.unit
def test_the_reader_runs_over_every_board_here_with_the_first_script_found(tmp_path):
    from agentorc.ui.app import board_argv

    assert board_argv([]) == (None, "")
    bare = repo(tmp_path, "bare", reader=False)
    assert board_argv([bare]) == (None, "")  # no board anywhere is not a fault: nothing to say
    unread = repo(tmp_path, "unread", "- [ ] x. Due: 2026-09-01.\n", reader=False)
    argv, note = board_argv([unread])
    assert argv is None and "nudge_user_attention.py" in note  # a board nobody can read is said
    a = repo(tmp_path, "a", "- [ ] x\n", reader=False)
    b = repo(tmp_path, "b", "- [ ] y\n")
    argv, note = board_argv([a, bare, b])
    assert note == "" and argv[1] == str(b / "scripts" / "nudge_user_attention.py")
    assert argv[2:4] == ["--report", "--json"]  # every open item: the page sorts what is due (TD-220)
    assert argv[4:] == ["--board", str(a / "docs/user_attention.md"), "--board", str(b / "docs/user_attention.md")]


@pytest.mark.unit
def test_a_board_row_carries_its_repo_team_due_words_text_and_the_board_at_its_line(tmp_path, monkeypatch):
    host(tmp_path, monkeypatch)
    from agentorc.ui.app import board_rows

    root = tmp_path / "samscrape"
    rows = board_rows(
        report(root, item(7, "Decide X. Due: 2026-09-20.", "2026-09-20", "2d overdue")),
        {str(root.resolve()): "sam-grind"},
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["row"] == "board" and r["repo"] == "samscrape" and r["team"] == "sam-grind"
    assert r["due_tag"] == "2d overdue" and r["at"] == "2026-09-20" and r["line"] == 7
    assert r["id"] == f"board:{root}:7"
    assert r["editor"].startswith("vscode://file") and "user_attention.md:7" in r["editor"]
    assert "decide x" in r["find"] and "samscrape" in r["find"]
    # a repo no team's projects hold carries none (the filter's `team:`), and junk is skipped
    assert board_rows(report(root, item(1, "t", "2026-09-20", "due")), {})[0]["team"] == ""
    assert board_rows({"boards": [{"root": str(root), "items": [{"line": 2}, "x"]}, 3]}) == []
    assert board_rows(None) == [] and board_rows({"boards": "x"}) == []


@pytest.mark.unit
def test_a_repo_maps_to_the_first_team_whose_projects_hold_it(tmp_path):
    from agentorc import org as orgmod
    from agentorc.ui.app import repo_teams

    here = tmp_path / "ao"
    org = orgmod.Org(
        projects={
            "agentorc": orgmod.Project("agentorc", {"agentorc": {"kmaster": here, "other": tmp_path / "x"}}),
            "cm": orgmod.Project("cm", {"cm": {"other": tmp_path / "cm"}}),
        },
        teams={
            "ao-grind": orgmod.TeamDef("ao-grind", projects=["agentorc"]),
            "ao-audit": orgmod.TeamDef("ao-audit", projects=["agentorc"]),
            "cm-grind": orgmod.TeamDef("cm-grind", projects=["cm", "gone"]),
        },
    )
    assert repo_teams(org, "kmaster") == {str(here.resolve()): "ao-grind"}


@pytest.mark.unit
def test_due_board_items_are_counted_in_needs_you_oldest_first_among_the_mail(tmp_path, monkeypatch):
    """§4.5 screen 6: *a due board item* is in **Needs you**, and the section is oldest first
    across every kind — so an item a week overdue sits above a question asked this morning."""
    host(tmp_path, monkeypatch)
    from agentorc.ui.app import board_rows, inbox_sections

    root = tmp_path / "r"
    boards = board_rows(
        report(root, item(3, "old", "2026-09-12", "10d overdue"), item(4, "today", "2026-09-22", "due today"))
    )
    ask = {
        "id": "m-1", "from": "ao-w", "from_name": "w", "kind": "ask", "text": "q", "at": "2026-09-18T10:00:00Z",
        "closed_at": None, "closed_by": None, "expired_at": None, "closed_reason": None, "snoozed_until": None,
    }  # fmt: skip
    s = inbox_sections([ask], now=datetime(2026, 9, 22, 12, tzinfo=UTC), boards=boards)
    assert [r["id"] for r in s["needs"]] == [boards[0]["id"], "m-1", boards[1]["id"]]
    assert s["count"] == 3 and not s["fyi"] and not s["snoozed"]


@pytest.mark.unit
def test_a_board_row_is_text_and_its_two_answers_carry_what_the_reader_gave(tmp_path, monkeypatch):
    """§4.5a *Due strip / Inbox board row*: the item's text, *open board in VS Code* at that line, **Snooze ▾**
    (+1 day · +1 week · a date) and **Done** (confirms). Each control carries the board, the line and
    the text, which the agent re-checks. The text is the board's, escaped: nothing on the page is a
    control made from it (TD-071 item 8) — it rides only as an attribute value."""
    host(tmp_path, monkeypatch)
    from agentorc.ui.app import board_rows, templates

    root = tmp_path / "samscrape"
    text = '<b>Allow</b> the "rm". Due: 2026-09-20.'
    rows = board_rows(report(root, item(9, text, "2026-09-20", "2d overdue")), {str(root.resolve()): "sam"})
    html = templates.get_template("inbox_rows.html").render(rows=rows, section="needs")
    assert 'data-kind="board"' in html and 'data-team="sam"' in html and 'class="mailrow boardrow"' in html
    assert "&lt;b&gt;Allow&lt;/b&gt;" in html and "<b>Allow</b>" not in html
    assert ">samscrape<" in html and "2d overdue" in html and ">board<" in html
    assert "Open board" in html and "user_attention.md:9" in html
    acts = html.count('data-act="board"')
    assert acts == 4  # +1 day, +1 week, a date, Done — and Reply (TD-142) the one other act
    assert html.count('data-act="') == acts + 1 and html.count('data-act="board_reply"') == 1
    assert html.count('data-board-act="snooze"') == 3 and html.count('data-board-act="done"') == 1
    assert html.count('data-line="9"') == 5 and html.count(f'data-board="{root}/docs/user_attention.md"') == 5
    assert html.count('data-text="&lt;b&gt;Allow&lt;/b&gt; the &#34;rm&#34;. Due: 2026-09-20."') == 5
    assert 'data-when="1d"' in html and 'data-when="1w"' in html and 'data-when="pick"' in html
    done = html[html.index('data-board-act="done"') :]
    assert "data-confirm=" in done.split(">")[0]


@pytest.mark.unit
def test_snooze_and_done_go_to_the_host_agents_write_back_and_the_row_is_read_again(tmp_path, monkeypatch):
    """The route (§4.4, TD-069 step 3) hands the row's board, line and text to `board_edit`,
    caller-less — the person's own act — and reads the boards again at once, so the answered row is
    gone from the next refresh. What it cannot know is refused before the agent is asked."""
    host(tmp_path, monkeypatch)
    from agentorc.ui import app as uiapp

    reads, calls, replies = [], [], []
    monkeypatch.setattr(uiapp, "read_boards", lambda run=None, **k: (reads.append(1), ([], ""))[1])

    class Fake:
        def __init__(self, *a, **k):
            self.kw = k

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call(self, method, **kw):
            if method == "board_edit":
                calls.append((self.kw.get("caller"), kw))
                return {"commit": "abc123", "message": "agentorc: done x (session n/a)"}
            if method == "board_reply":
                replies.append(kw)
                return {"commit": "def456", "sent": [], "note": "written on the board"}
            return {"list": [], "inbox": {"entries": [], "trail": []}}.get(method, {})

    monkeypatch.setattr(uiapp, "LocalClient", Fake)
    board = str(tmp_path / "r/docs/user_attention.md")
    with TestClient(uiapp.create_app()) as c:
        c.get("/api/person/inbox")
        assert len(reads) == 1
        r = c.post("/api/person/board", json={"action": "done", "board": board, "line": 4, "text": "x"})
        assert r.status_code == 200 and r.json()["commit"] == "abc123"
        assert len(reads) == 2  # read again at once, not at the next minute
        r = c.post(
            "/api/person/board",
            json={"action": "snooze", "board": board, "line": "4", "text": "x", "due": "2026-09-30"},
        )
        assert r.status_code == 200
        assert [k for _, k in calls] == [
            {"board": board, "line": 4, "text": "x", "action": "done", "due": None},
            {"board": board, "line": 4, "text": "x", "action": "snooze", "due": "2026-09-30"},
        ]
        assert all(caller is None for caller, _ in calls)  # a person is not a session
        # TD-142: Reply goes to `board_reply` with the reader's refs, and the boards are read again
        r = c.post(
            "/api/person/board",
            json={"action": "reply", "board": board, "line": 4, "text": "x", "reply": " rebase it ", "refs": ["TD-9"]},
        )
        assert r.status_code == 200 and replies == [
            {"board": board, "line": 4, "text": "x", "reply": "rebase it", "refs": ["TD-9"]}
        ]
        assert len(reads) == 4
        for bad in ({"reply": ""}, {"line": "four"}, {"board": ""}):
            body = {"action": "reply", "board": board, "line": 4, "text": "x", "reply": "y", **bad}
            assert c.post("/api/person/board", json=body).status_code == 400
        assert len(replies) == 1
        for bad in (
            {"action": "delete", "board": board, "line": 4, "text": "x"},
            {"action": "done", "board": board, "line": "four", "text": "x"},
            {"action": "done", "board": "", "line": 4, "text": "x"},
            {"action": "snooze", "board": board, "line": 4, "text": "x"},
        ):
            assert c.post("/api/person/board", json=bad).status_code == 400
        assert len(calls) == 2


@pytest.mark.unit
def test_read_boards_runs_dev_cadences_reader_and_says_when_it_cannot(tmp_path, monkeypatch):
    """End to end over a real board and the real reader: an overdue item and one due today are
    due-now rows, an undated one and one due next week are rows not yet due (TD-220); the team
    comes from the org's projects."""
    past = (date.today() - timedelta(days=3)).isoformat()
    later = (date.today() + timedelta(days=7)).isoformat()
    board = (
        "# Board\n\n"
        f"- [ ] overdue thing. Due: {past}.\n"
        f"- [ ] due today. Due: {date.today().isoformat()}.\n"
        "- [ ] undated thing.\n"
        f"- [ ] next week. Due: {later}.\n"
        f"- [x] done already. Due: {past}.\n"
    )
    root = repo(tmp_path, "proj", board)
    host(tmp_path, monkeypatch, [root])
    (tmp_path / "home" / "org.yml").write_text(
        f"projects:\n  proj:\n    repos:\n      proj: {{kmaster: {root}}}\n"
        "teams:\n  proj-grind:\n    projects: [proj]\n    manager: {role: manager}\n"
    )
    from agentorc.ui import app as uiapp

    rows, note = uiapp.read_boards()
    assert note == ""
    # every open item is read (TD-220), the ones due now marked; the closed one is not an item
    assert [(r["text"].split(".")[0], r["due_now"]) for r in rows] == [
        ("overdue thing", True),
        ("due today", True),
        ("undated thing", False),
        ("next week", False),
    ]
    assert [r["ahead"] for r in rows[2:]] == [
        "no due date",
        f"due in 7 d · {date.today() + timedelta(days=7):%b} {(date.today() + timedelta(days=7)).day}",
    ]
    rows = [r for r in rows if r["due_now"]]
    assert rows[0]["line"] == 3 and rows[0]["repo"] == "proj" and rows[0]["team"] == "proj-grind"
    assert "overdue" in rows[0]["due_tag"]

    class Done:
        returncode, stdout, stderr = 2, "", "Traceback…\nValueError: boom\n"

    rows, note = uiapp.read_boards(run=lambda *a, **k: Done())
    assert rows == [] and "exited 2" in note and "boom" in note
    Done.returncode, Done.stdout = 0, "not json"
    assert uiapp.read_boards(run=lambda *a, **k: Done()) == (
        [],
        "board items are not shown: the reader's output is not JSON",
    )

    def slow(*a, **k):
        import subprocess

        raise subprocess.TimeoutExpired(a[0], 20)

    rows, note = uiapp.read_boards(run=slow)
    assert rows == [] and "did not finish" in note


@pytest.mark.unit
def test_the_page_the_top_bar_and_the_poll_carry_the_board_rows_and_the_note(tmp_path, monkeypatch):
    """One count in three places (§4.5a **Inbox page**): the board rows join the page, the poll and
    the Org's top bar through the same `inbox_sections`; the reader's note is drawn and polled."""
    host(tmp_path, monkeypatch)
    from agentorc.ui import app as uiapp

    root = tmp_path / "proj"
    later = {**item(4, "look next week", "2026-09-30", "due 2026-09-30"), "overdue_days": None}
    rows = uiapp.board_rows(report(root, item(3, "decide the thing", "2026-09-20", "2d overdue"), later))
    calls = []

    def fake(run=None):
        calls.append(1)
        return rows, "a note from the reader"

    monkeypatch.setattr(uiapp, "read_boards", fake)

    class Fake:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call(self, method, **kw):
            return {
                "list": [],
                "inbox": {"entries": [], "trail": []},
                "usage": {},
                "gate": {},
                "host": {"name": "kmaster", "mode": "home"},
                "identity": {},
            }.get(method, {})

    monkeypatch.setattr(uiapp, "LocalClient", Fake)
    with TestClient(uiapp.create_app()) as c:
        page = c.get("/inbox").text
        assert "decide the thing" in page and needs_line(page) == "1"
        # not yet due: drawn under *Board, coming up*, counted nowhere (TD-220 slice 3)
        assert "look next week" in page and "Board, coming up" in page and needs_line(page) == "1"
        assert "a note from the reader" in page and 'id="boardnote"' in page
        got = c.get("/api/person/inbox").json()
        assert got["needs"] == 1 and got["sections"]["needs"] == [rows[0]["id"]]
        assert got["board_note"] == "a note from the reader" and "decide the thing" in got["html"]["needs"]
        assert 'id="personneeds">1</span>' in c.get("/").text
    assert len(calls) == 1  # read once for all three: the page and the top bar poll every few seconds


@pytest.mark.unit
def test_with_the_host_agent_down_no_surface_counts_the_board(tmp_path, monkeypatch):
    """The three surfaces agree while the host agent is down (review of PR #472): the Org's top bar
    and the poll claim nothing, so the Inbox page does not count the board rows alone."""
    host(tmp_path, monkeypatch)
    from agentorc.ui import app as uiapp
    from sessionorc.client import AgentUnavailable

    root = tmp_path / "proj"
    rows = uiapp.board_rows(report(root, item(3, "decide the thing", "2026-09-20", "2d overdue")))
    monkeypatch.setattr(uiapp, "read_boards", lambda run=None, **k: (rows, ""))

    class Down:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise AgentUnavailable("down")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(uiapp, "LocalClient", Down)
    with TestClient(uiapp.create_app()) as c:
        page = c.get("/inbox").text
        assert "decide the thing" not in page and needs_line(page) == "0"
        assert c.get("/api/person/inbox").json()["needs"] is None


def _row(line, due, team="t", *, due_now=False, **kw):
    return {
        "row": "board",
        "id": f"board:r:{line}",
        "line": line,
        "repo": "r",
        "root": "/r",
        "team": team,
        "due": due,
        "due_now": due_now,
        "today": "2026-09-28",
        **kw,
    }


@pytest.mark.unit
def test_what_is_due_is_the_readers_due_only_set():
    """§4.5 screen 6 *The board's horizon* (TD-220): due now is what `--due-only` surfaced — due today
    or earlier, decided whatever its date, a date that could not be read — and never an undecided fyi."""
    from agentorc.ui.app import board_due_now

    assert board_due_now({"overdue_days": 0}) and board_due_now({"overdue_days": 3})
    assert board_due_now({"overdue_days": None, "decided": {"text": "go", "date": "2026-09-27"}})
    assert board_due_now({"overdue_days": None, "due_error": "Due: soonish"})
    assert not board_due_now({"overdue_days": None, "due": "2026-10-04"})
    assert not board_due_now({"overdue_days": None})  # undated
    assert not board_due_now({"overdue_days": 2, "kind": "fyi"})
    assert board_due_now({"overdue_days": None, "kind": "fyi", "decided": {"text": "x", "date": "2026-09-27"}})


@pytest.mark.unit
def test_the_horizon_sorts_the_rows_by_the_mode():
    """§4.5 screen 6 *The board's horizon*, §5 `person.inbox.board_show` (TD-220 slice 2)."""
    from agentorc.ui.app import board_horizon

    def ids(rows):
        return [r["line"] for r in rows]

    # next:10 — a team with twelve due shows twelve and none ahead; one with two due shows eight ahead
    busy = [_row(i, "2026-09-20", "busy", due_now=True) for i in range(12)] + [_row(99, "2026-09-29", "busy")]
    got = board_horizon(busy, None)
    assert got["mode"] == "next:10" and len(got["due"]) == 12 and got["ahead"] == [] and ids(got["hidden"]) == [99]
    quiet = [_row(i, "2026-09-20", due_now=True) for i in range(2)] + [
        _row(100 + i, f"2026-10-{i + 1:02d}") for i in range(12)
    ]
    got = board_horizon(quiet, "next:10")
    assert ids(got["ahead"]) == [100 + i for i in range(8)] and ids(got["hidden"]) == [108, 109, 110, 111]
    assert got["next_due"] == "2026-10-09"
    # a team that works two boards shows n across both; a board no team works is its repo's own group
    two = [_row(1, "2026-10-01", root="/a"), _row(2, "2026-10-02", root="/b"), _row(3, "2026-10-03", root="/a")]
    assert ids(board_horizon(two, "next:2")["ahead"]) == [1, 2]
    lone = [_row(1, "2026-10-01", ""), _row(2, "2026-10-02", "", root="/other")]
    assert ids(board_horizon(lone, "next:1")["ahead"]) == [1, 2]
    # a due_error row is a due row and takes no place of the n
    bad = [_row(1, "", due_now=True, due_error=True), _row(2, "2026-10-01")]
    got = board_horizon(bad, "next:1")
    assert ids(got["due"]) == [1] and ids(got["ahead"]) == [2]
    # 7d: six days out is coming up, forty is hidden and counted by the line; undated is hidden
    week = [_row(1, "2026-10-04"), _row(2, "2026-11-07"), _row(3, None), _row(4, "2026-09-27", due_now=True)]
    got = board_horizon(week, "7d")
    assert ids(got["due"]) == [4] and ids(got["ahead"]) == [1] and ids(got["hidden"]) == [2, 3]
    assert got["next_due"] == "2026-11-07"
    # due: nothing ahead; all: everything, the undated last
    got = board_horizon(week, "due")
    assert ids(got["due"]) == [4] and got["ahead"] == [] and ids(got["hidden"]) == [1, 2, 3]
    got = board_horizon(week, "all")
    assert ids(got["ahead"]) == [1, 2, 3] and got["hidden"] == [] and got["next_due"] == ""
    # an undated item takes a place under next: once the dated ones have theirs
    assert ids(board_horizon(week, "next:3")["ahead"]) == [1, 2]
    assert ids(board_horizon(week, "next:4")["ahead"]) == [1, 2, 3]
    # the due rows never move with the mode, and a mode that does not parse is next:10
    for mode in ("next:1", "due", "7d", "all", "soon", None):
        assert ids(board_horizon(week, mode)["due"]) == [4]
    assert board_horizon(week, "soon")["mode"] == "next:10"
    # a past-dated fyi is not due now: it reads the reader's words, and is never the line's next date
    from agentorc.ui.app import board_rows

    fyi = {
        "line": 5,
        "text": "for your read",
        "due": "2026-09-25",
        "overdue_days": 3,
        "due_tag": "3d overdue",
        "kind": "fyi",
        "decided": None,
    }
    (row,) = board_rows({"today": "2026-09-28", "boards": [{"root": "/r", "board": "/r/b.md", "items": [fyi]}]})
    assert not row["due_now"] and row["ahead"] == "3d overdue"
    got = board_horizon([row, _row(6, "2026-10-12")], "due")
    assert ids(got["hidden"]) == [5, 6] and got["next_due"] == "2026-10-12"
    # a row from before TD-220 carries no due_now: it was the --due-only read's, so it is due
    assert ids(board_horizon([{"line": 7, "due": "2026-10-10"}], "due")["due"]) == [7]


@pytest.mark.unit
def test_the_mode_is_the_persons_setting_as_last_read():
    """§5 `person.inbox.board_show` through the agent's `settings` read (TD-220): unset or bad is next:10."""
    from agentorc.ui import uiconf

    try:
        uiconf.set_read({"person": {}})
        assert uiconf.board_show() == "next:10"
        uiconf.set_read({"person": {"inbox": {"board_show": "7d"}}})
        assert uiconf.board_show() == "7d"
        uiconf.set_read({"person": {"inbox": {"board_show": "next:0"}}})
        assert uiconf.board_show() == "next:10"
        uiconf.set_read({"person": {"inbox": "all"}})
        assert uiconf.board_show() == "next:10"
    finally:
        uiconf.set_read({"person": {}, "migrate": []})


@pytest.mark.unit
def test_the_line_says_the_mode_and_what_it_hides():
    """§4.5 screen 6 *The board's horizon*: the line that closes the board rows, in each mode's words."""
    from agentorc.ui.app import board_line

    def line(mode, hidden=0, next_due=""):
        return board_line({"mode": mode, "hidden": [{}] * hidden, "next_due": next_due})

    assert line("next:10", 14, "2026-10-12") == {
        "says": "showing the next 10 board items per team",
        "rest": "14 not shown, the next due Oct 12",
        "n": 14,
    }
    assert line("next:10")["rest"] == "nothing hidden" and line("next:10")["n"] == 0
    assert line("next:1")["says"] == "showing the next 1 board item per team"
    assert line("due", 5, "2026-10-04")["says"] == "showing board items that are due"
    assert line("7d", 3)["says"] == "showing board items due this week" and line("7d", 3)["rest"] == "3 not shown"
    assert line("14d")["says"] == "showing board items due within 14 days"
    assert line("all")["says"] == "showing every board item"


@pytest.mark.unit
def test_the_repo_page_cuts_the_inboxs_horizon_to_its_repo():
    """§4.5 screen 11 *Waiting on you*: the Inbox's rows, lists and line filtered to the repo."""
    from agentorc.ui.app import board_horizon, horizon_of

    rows = [
        _row(1, "2026-09-20", due_now=True, root="/a"),
        _row(2, "2026-10-01", root="/a"),
        _row(3, "2026-10-02", root="/b"),
        _row(4, "2026-10-20", root="/a"),
        _row(5, "2026-10-05", root="/b"),
    ]
    h = board_horizon(rows, "next:3")  # one team: the due row, then 2 and 3 take the places
    got = horizon_of(h, "/a")
    assert [r["line"] for r in got["due"]] == [1] and [r["line"] for r in got["ahead"]] == [2]
    assert [r["line"] for r in got["hidden"]] == [4] and got["next_due"] == "2026-10-20"
    assert h["next_due"] == "2026-10-05" and got["mode"] == "next:3"
    # the line is the repo's own: its hidden count and next date, not the Inbox's
    got = horizon_of({**h, "line": {"says": "x", "rest": "y", "n": 2}}, "/a")
    assert got["line"]["rest"] == "1 not shown, the next due Oct 20" and got["line"]["n"] == 1
    assert horizon_of(h, "/a").get("line") is None


@pytest.mark.unit
def test_coming_up_the_fold_and_the_line_are_drawn_and_counted_nowhere(tmp_path, monkeypatch):
    """§4.5 screen 6 *The board's horizon* (TD-220 slice 3): under the due board rows, *Board, coming
    up (n)* with its due words, the *not shown* fold closed, and the line with **show** and Settings;
    the counts are the due rows' alone under every mode, and the poll brings the same markup."""
    host(tmp_path, monkeypatch)
    from agentorc.ui import app as uiapp
    from agentorc.ui import uiconf

    root = tmp_path / "proj"

    def ahead(line, text, due):
        return {"line": line, "text": text, "due": due, "overdue_days": None, "due_tag": f"due {due}"}

    items = [item(3, "decide the thing", "2026-09-20", "2d overdue")]
    items += [ahead(10 + i, f"later item {i}", f"2026-10-{i + 1:02d}") for i in range(12)]
    rows = uiapp.board_rows({**report(root, *items), "today": "2026-09-28"})
    monkeypatch.setattr(uiapp, "read_boards", lambda run=None, **k: (rows, ""))

    class Fake:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call(self, method, **kw):
            return {"list": [], "inbox": {"entries": [], "trail": []}, "host": {"name": "kmaster"}}.get(method, {})

    monkeypatch.setattr(uiapp, "LocalClient", Fake)
    try:
        with TestClient(uiapp.create_app()) as c:
            page = c.get("/inbox").text
            assert needs_line(page) == "1"
            assert 'Board, coming up</span><span class="meta">(9)</span>' in page  # ten places, one due
            assert "due in 3 d · Oct 1" in page and 'data-section="coming"' in page
            assert "<summary>not shown (3)</summary>" in page and "later item 11" in page
            assert "showing the next 10 board items per team · 3 not shown, the next due Oct 10" in page
            assert 'class="boardshow"' in page and 'href="/settings#sec-you"' in page
            # the rail's *board items* counts every board row on the page; its *Needs you* the due one
            assert 'data-value="board"' in page
            got = c.get("/api/person/inbox").json()
            assert got["needs"] == 1 and got["sections"]["needs"] == [rows[0]["id"]]
            assert "Board, coming up" in got["html"]["horizon"] and "later item 0" not in got["html"]["needs"]
            uiconf.set_read({"person": {"inbox": {"board_show": "due"}}})
            page = c.get("/inbox").text
            assert "Board, coming up" not in page and needs_line(page) == "1"
            assert "showing board items that are due · 12 not shown, the next due Oct 1" in page
            uiconf.set_read({"person": {"inbox": {"board_show": "all"}}})
            page = c.get("/inbox").text
            assert "showing every board item · nothing hidden" in page and "not shown (" not in page
            assert "boardshow" not in page and needs_line(page) == "1"
    finally:
        uiconf.set_read({"person": {}, "migrate": []})


@pytest.mark.unit
def test_the_rail_counts_coming_up_as_board_items_and_in_no_section():
    """§4.5 screen 6: the rail's *board items* counts the rows coming up; *Needs you* does not, and a
    *Needs you* pick keeps them, as they sit in its section."""
    from agentorc.ui.app import rail_counts, rail_picks, rail_rows

    due = {"row": "board", "id": "b1", "team": "t", "find": "now"}
    later = {"row": "board", "id": "b2", "team": "t", "find": "later"}
    rows = rail_rows({"needs": [due]}, [later])
    got = rail_counts(rows, rail_picks({}))
    assert got["kinds"]["board"]["all"] == 2 and got["sections"]["needs"]["all"] == 1
    assert got["heads"]["needs"]["all"] == 1 and got["teams"]["t"]["all"] == 1
    picked = rail_counts(rows, rail_picks({"sec": "needs", "kind": "board"}))
    assert picked["kinds"]["board"]["shown"] == 2 and picked["heads"]["needs"]["shown"] == 1


@pytest.mark.unit
def test_a_row_coming_up_snoozes_from_its_own_date_and_show_draws_forty_days_out(tmp_path, monkeypatch):
    """§4.5 screen 6 *The board's horizon*, §4.5a *Due strip / Inbox board row* **Snooze ▾** (TD-220
    slice 6): a row coming up carries Snooze with its own date, and +1 day / +1 week count from the
    later of today and that date, so a Snooze moves it later, never nearer; under `7d` an item due in
    forty days is in the closed fold, which **show** opens without a request — no setting is written."""
    host(tmp_path, monkeypatch)
    from agentorc.ui import app as uiapp
    from agentorc.ui import uiconf

    root = tmp_path / "proj"

    def ahead(line, text, due):
        return {"line": line, "text": text, "due": due, "overdue_days": None, "due_tag": f"due {due}"}

    items = [ahead(10, "next week's item", "2026-10-04"), ahead(11, "forty days out", "2026-11-07")]
    rows = uiapp.board_rows({**report(root, *items), "today": "2026-09-28"})
    monkeypatch.setattr(uiapp, "read_boards", lambda run=None, **k: (rows, ""))
    calls = []

    class Fake:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call(self, method, **kw):
            calls.append(method)
            return {"list": [], "inbox": {"entries": [], "trail": []}, "host": {"name": "kmaster"}}.get(method, {})

    monkeypatch.setattr(uiapp, "LocalClient", Fake)
    try:
        with TestClient(uiapp.create_app()) as c:
            c.get("/inbox")  # the first request reads the settings; the mode is set after it
            uiconf.set_read({"person": {"inbox": {"board_show": "7d"}}})
            page = c.get("/inbox").text
            coming = page[page.index('id="rows-coming"') : page.index("<summary>not shown (")]
        for when in ("1d", "1w", "pick"):
            assert f'data-board-act="snooze" data-when="{when}" data-due="2026-10-04"' in coming
        fold = page[page.index('<details class="fold boardfold"') :]
        fold = fold[: fold.index("</details>")]
        assert "forty days out" in fold and "next week's item" not in fold
        assert "3 d" not in fold and 'data-due="2026-11-07"' in fold
        assert "1 not shown, the next due Nov 7 — " in page and 'class="boardshow"' in page
        assert "set_settings" not in calls
    finally:
        uiconf.set_read({"person": {}, "migrate": []})
    js = (pathlib.Path(uiapp.__file__).parent / "static" / "app.js").read_text()
    assert "boardDue(b.dataset.when, b.dataset.due)" in js
    body = js[js.index("function boardDue(when, from)") :]
    body = body[: body.index("\n  }\n")]
    assert "if (own > d) d.setTime(own.getTime())" in body  # the later of today and its own date
    show = js[js.index('closest("a.boardshow")') :]
    show = show[: show.index("});")]
    assert "fetch" not in show and "settings" not in show  # show writes nothing


# -- the board read against origin (§4.5 screen 6 *Boards are read against origin*, TD-221) ------


def _git(*args, cwd):
    import subprocess

    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.mark.unit
def test_a_board_line_only_on_origin_is_read_from_origin_on_a_clone_behind(tmp_path, monkeypatch):
    """A clone one commit behind its origin: the fetching read shows the line merged on origin, the
    row says it was read from origin (`source`) and keeps the reader's `fetch_note`; the plain read
    does not see it. Real git, the real reader."""
    due = date.today().isoformat()
    origin, seed, clone = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "proj"
    _git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    _git("clone", "-q", str(origin), str(seed), cwd=tmp_path)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        _git("config", k, v, cwd=seed)
    (seed / "docs").mkdir()
    (seed / "docs" / "user_attention.md").write_text(f"# Board\n\n- [ ] the old line. Due: {due}.\n")
    (seed / "scripts").mkdir()
    shutil.copy(READER, seed / "scripts" / "nudge_user_attention.py")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "board", cwd=seed)
    _git("push", "-q", "origin", "main", cwd=seed)
    _git("clone", "-q", str(origin), str(clone), cwd=tmp_path)
    with (seed / "docs" / "user_attention.md").open("a") as f:
        f.write(f"- [ ] the line only on origin. Due: {due}.\n")
    _git("commit", "-q", "-am", "a line", cwd=seed)
    _git("push", "-q", "origin", "main", cwd=seed)
    host(tmp_path, monkeypatch, [clone])
    from agentorc.ui import app as uiapp

    rows, note = uiapp.read_boards()
    assert note == "" and [r["text"].split(".")[0] for r in rows] == ["the old line"]
    assert rows[0]["source"] == "" and rows[0]["fetch_note"] == ""
    rows, note = uiapp.read_boards(fetch=True)
    assert note == "" and [r["text"].split(".")[0] for r in rows] == ["the old line", "the line only on origin"]
    assert all(r["source"] for r in rows) and rows[0]["fetch_note"].startswith("fetched; local clone is behind")


@pytest.mark.unit
def test_a_fetch_that_is_stopped_or_fails_is_followed_by_a_plain_read(tmp_path, monkeypatch):
    """A dead remote costs the origin view and never the board: the fetching read stopped at its
    bound (or failed) is followed at once by a plain read, and each board says *fetch skipped*."""
    import subprocess

    root = repo(tmp_path, "proj", "# Board\n")
    host(tmp_path, monkeypatch, [root])
    from agentorc.ui import app as uiapp

    seen = []

    class Done:
        returncode, stderr = 0, ""
        stdout = __import__("json").dumps(report(root, item(3, "a thing. Due: 2026-09-20.", "2026-09-20", "overdue")))

    def run(argv, **kw):
        seen.append(("--fetch" in argv, kw["timeout"]))
        if "--fetch" in argv:
            raise subprocess.TimeoutExpired(argv, kw["timeout"])
        return Done()

    rows, note = uiapp.read_boards(run=run, fetch=True)
    assert seen == [(True, uiapp.BOARD_FETCH_TIMEOUT), (False, uiapp.BOARD_TIMEOUT)]
    assert note == "" and len(rows) == 1
    assert rows[0]["source"] == "" and rows[0]["fetch_note"] == "fetch skipped (timeout)"

    class Failed:
        returncode, stdout, stderr = 1, "", "boom\n"

    rows, _ = uiapp.read_boards(run=lambda argv, **kw: Failed() if "--fetch" in argv else Done(), fetch=True)
    assert rows[0]["fetch_note"] == "fetch skipped (the reader exited 1 — boom)"


@pytest.mark.unit
def test_one_fetching_read_at_a_time_and_a_press_never_waits_on_it(tmp_path, monkeypatch):
    """The first request reads plainly; from then a stale reading starts one fetching read, however
    many requests find it stale, and each is answered from the last reading meanwhile; a press
    reads its own board plainly, laid over the last reading, and does not wait on the fetch. A
    fetch that began before the press does not put the answered row back."""
    import threading

    host(tmp_path, monkeypatch)
    from agentorc.ui import app as uiapp

    board = str(tmp_path / "r/docs/user_attention.md")
    other = str(tmp_path / "s/docs/user_attention.md")

    def row(b, text):
        return {
            "row": "board",
            "id": f"board:{b}:{text}",
            "board": b,
            "text": text,
            "due_now": True,
            "at": "2026-09-01",
        }

    gate, reads, board_ = threading.Event(), [], board

    def fake(run=None, *, fetch=False, board=""):
        reads.append("fetch" if fetch else board or "plain")
        if fetch:
            assert gate.wait(10)
            return [row(board_, "answered"), row(other, "from origin")], ""
        if board:
            return [], ""  # the answered row is gone from its board
        return [row(board_, "answered"), row(other, "old")], ""

    monkeypatch.setattr(uiapp, "read_boards", fake)
    monkeypatch.setattr(uiapp, "BOARD_TTL", -1.0)

    class Fake:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call(self, method, **kw):
            if method == "board_edit":
                return {"commit": "abc"}
            return {"list": [], "inbox": {"entries": [], "trail": []}}.get(method, {})

    monkeypatch.setattr(uiapp, "LocalClient", Fake)
    with TestClient(uiapp.create_app()) as c:
        assert c.get("/api/person/inbox").json()["needs"] == 2 and reads == ["plain"]
        c.get("/api/person/inbox")
        c.get("/api/person/inbox")
        assert reads == ["plain", "fetch"]  # one fetching read, the requests answered meanwhile
        r = c.post("/api/person/board", json={"action": "done", "board": board, "line": 3, "text": "answered"})
        assert r.status_code == 200 and reads == ["plain", "fetch", board]
        assert c.get("/api/person/inbox").json()["needs"] == 1  # the press's read, laid over
        gate.set()
        for _ in range(50):
            got = c.get("/api/person/inbox").json()
            if "from origin" in got["html"]["needs"]:
                break
            threading.Event().wait(0.05)
        assert "from origin" in got["html"]["needs"] and "answered" not in got["html"]["needs"]
