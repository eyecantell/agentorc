"""The Inbox's **For you in the ledger (n)** fold (design §4.5 screen 6 *The ledger's entries that
wait on you*, §4.5a, TD-368 slice 3): the entries the home's repo facts sort *for you*, grouped by
repo, under *Needs you* — closed by default, counted nowhere, each row one link to the Repo page."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def _e(i, page="for-you", pri="medium", owner="paul", **kw):
    return {"id": i, "title": f"entry {i}", "for_page": page, "priority": pri, "owner": owner, **kw}


def _repos(tmp_path):
    a, b, c = (str(tmp_path / n) for n in ("agentorc", "samscrape", "quiet"))
    return {
        a: {"name": "agentorc", "root": a, "ledger": {"entries": [
            _e("TD-319", pri="low", owner="grinder", blocked_by=["decision (paul)"]),
            _e("TD-156"),
            _e("TD-133", pri="low", owner="grinder", blocked_by=["decision (Paul)"]),
            _e("TD-010", page="pickable", owner="grinder"),
            _e("TD-002", pri="high"),
        ]}},
        b: {"name": "samscrape", "root": b, "ledger": {"entries": [_e("TD-400")]}},
        c: {"name": "quiet", "root": c, "ledger": {"entries": [_e("TD-1", page="other")]}},
    }  # fmt: skip


def _fleet(tmp_path):
    a, b = str(tmp_path / "agentorc"), str(tmp_path / "samscrape")
    claim = [{"ref": "TD-319", "status": "claimed"}]
    return [
        {"id": "g1", "name": "grinder-ao-1", "team": "ao-grind", "repo": a, "state": "working", "progress": claim},
        {"id": "g0", "name": "grinder-ao-0", "team": "ao-grind", "repo": a, "state": "closed", "progress":
         [{"ref": "TD-156", "status": "claimed"}]},
        {"id": "s1", "name": "sam-1", "team": "sam-grind", "repo": b, "state": "idle"},
    ]  # fmt: skip


def test_the_fold_lists_each_repos_for_you_entries_in_the_repo_pages_order(tmp_path):
    """Grouped by repo in the reading's order, a repo with none left out; within one, priority then
    id; *yours* for `Owner: paul`, *your decision* otherwise; *held by* a live record's claim and not
    an ended one's; the team the definitions give the repo, else its live records', else none; each
    row the Repo page opened on its id; `n` every row."""
    from agentorc.ui.app import ledger_for_you

    got = ledger_for_you(_repos(tmp_path), _fleet(tmp_path), {str(tmp_path / "samscrape"): "sam-defined"})
    assert got["n"] == 5
    assert [g["repo"] for g in got["groups"]] == ["agentorc", "samscrape"]
    rows = got["groups"][0]["rows"]
    assert [r["id"] for r in rows] == ["TD-002", "TD-156", "TD-133", "TD-319"]
    whys = [("H", "yours"), ("M", "yours"), ("L", "your decision"), ("L", "your decision")]
    assert [(r["pri"], r["why"]) for r in rows] == whys
    assert [r["held"] for r in rows] == ["", "", "", "grinder-ao-1"]  # the closed record holds nothing
    assert {r["team"] for r in rows} == {"ao-grind"} and rows[0]["url"] == "/repo/agentorc#TD-002"
    assert got["groups"][1]["rows"][0]["team"] == "sam-defined"  # the definitions' team first
    assert ledger_for_you(_repos(tmp_path), [], {})["groups"][0]["rows"][0]["team"] == ""  # none: *no team*
    assert ledger_for_you({}, [], {}) == {"n": 0, "groups": []}


def _render(lf, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import inbox_sections, templates

    sections = inbox_sections([])
    return templates.get_template("inbox.html").render(
        sections=sections, person_needs=sections["count"], host="kmaster", active="Inbox",
        agent_down=False, volatile=False, usage={}, ledger=lf,
    )  # fmt: skip


def test_the_fold_sits_under_needs_you_closed_and_counted_nowhere(tmp_path, monkeypatch):
    """§4.5a: under *Needs you*'s rows and the board's, closed by default, the heading's count with
    *entries that wait on you · not counted*; each row one link with the id, the title, the letter,
    why, *held by* and the team, carrying the team and the find for the rail's filter; no control;
    nothing at zero."""
    from agentorc.ui.app import ledger_for_you

    html = _render(ledger_for_you(_repos(tmp_path), _fleet(tmp_path), {}), tmp_path, monkeypatch)
    needs = html[html.index('id="sec-needs"') : html.index('id="sec-steering"')]
    fold = needs[needs.index('id="ledgerforyou"') :]
    assert needs.index('id="boardhorizon"') < needs.index('id="ledgerforyou"')
    assert '<details class="fold ledgerfold" id="ledgerfold">' in fold  # no `open`: closed by default
    assert "For you in the ledger</span> <span class=\"meta\">(5)</span>" in fold
    assert "entries that wait on you · not counted" in fold
    at = fold.index('href="/repo/agentorc#TD-319"')
    row = fold[fold.rindex("<a ", 0, at) :]
    row = row[: row.index("</a>")]
    assert 'class="ledgerrow"' in row and 'data-team="ao-grind"' in row and "td-319" in row
    assert "L · your decision · held by grinder-ao-1" in row and "<button" not in row
    assert 'class="meta mono ledgerrepo">samscrape<' in fold
    assert 'id="n-needs"' in html and "ledgerrow" not in html[: html.index('id="sec-needs"')]
    assert 'id="ledgerfold"' not in _render({"n": 0, "groups": []}, tmp_path, monkeypatch)


def test_the_page_and_its_poll_draw_the_fold_from_the_home_repo_facts(tmp_path, monkeypatch):
    """The route reads the home's `repos` and the fleet; the poll's `html.ledger` is the same box,
    so the page keeps it current."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    from agentorc.ui import app as ui

    repos, fleet = _repos(tmp_path), _fleet(tmp_path)

    class Fake:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call(self, method, **kw):
            got = {"repos": repos, "list": fleet, "inbox": {"entries": []}, "host": {"name": "kmaster"}}
            return got.get(method, {})

    monkeypatch.setattr(ui, "LocalClient", Fake)
    monkeypatch.setattr(ui, "read_boards", lambda *a, **k: ([], ""))
    c = TestClient(ui.create_app())
    assert 'href="/repo/agentorc#TD-156"' in c.get("/inbox").text
    got = c.get("/api/person/inbox").json()
    assert 'href="/repo/samscrape#TD-400"' in got["html"]["ledger"]
    assert got["needs"] == 0  # counted nowhere
