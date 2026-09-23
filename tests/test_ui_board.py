"""Board items in the Inbox (design §4.5 screen 6, §4.4; TD-069 step 3): the due items of each
repo's `docs/user_attention.md`, read by dev-cadence's own reader, as counted **Needs you** rows."""

from __future__ import annotations

import pathlib
import shutil
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

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
    assert argv[2:5] == ["--report", "--due-only", "--json"]
    assert argv[5:] == ["--board", str(a / "docs/user_attention.md"), "--board", str(b / "docs/user_attention.md")]


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
def test_a_board_row_is_text_says_how_it_leaves_and_offers_no_unbuilt_control(tmp_path, monkeypatch):
    """§4.5a *Due strip / Attention*: the item's text and *open board in VS Code* at that line. Snooze
    and Done are §4.4's write-back, not built — the row says so rather than drawing them. The text is
    the board's, escaped: nothing on the page is a control made from it (TD-071 item 8)."""
    host(tmp_path, monkeypatch)
    from agentorc.ui.app import board_rows, templates

    root = tmp_path / "samscrape"
    rows = board_rows(
        report(root, item(9, "<b>Allow</b> the rm. Due: 2026-09-20.", "2026-09-20", "2d overdue")),
        {str(root.resolve()): "sam"},
    )
    html = templates.get_template("inbox_rows.html").render(rows=rows, section="needs")
    assert 'data-kind="board"' in html and 'data-team="sam"' in html and 'class="mailrow boardrow"' in html
    assert "&lt;b&gt;Allow&lt;/b&gt;" in html and "<b>Allow</b>" not in html
    assert ">samscrape<" in html and "2d overdue" in html and ">board<" in html
    assert "Open board" in html and "user_attention.md:9" in html
    assert "data-act" not in html  # no Snooze, no Done, no Dismiss: nothing here the page could act on
    assert "not built" in html


@pytest.mark.unit
def test_read_boards_runs_dev_cadences_reader_and_says_when_it_cannot(tmp_path, monkeypatch):
    """End to end over a real board and the real reader: an overdue item and one due today are
    rows, an undated one and one due next week are not; the team comes from the org's projects."""
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
    assert [r["text"].split(".")[0] for r in rows] == ["overdue thing", "due today"]
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
    rows = uiapp.board_rows(report(root, item(3, "decide the thing", "2026-09-20", "2d overdue")))
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
        assert "decide the thing" in page and '<span id="needsn">1</span> needs you' in page
        assert "a note from the reader" in page and 'id="boardnote"' in page
        got = c.get("/api/person/inbox").json()
        assert got["needs"] == 1 and got["sections"]["needs"] == [rows[0]["id"]]
        assert got["board_note"] == "a note from the reader" and "decide the thing" in got["html"]["needs"]
        assert 'id="personneeds">1</span>' in c.get("/").text
    assert len(calls) == 1  # read once for all three: the page and the top bar poll every few seconds
