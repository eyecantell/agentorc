# ruff: noqa: E501 — the fixture rows read as one line each
"""TD-176 slice 5, design §4.5 screen 11 *Repo*: the servicing team's three facets, then Open PRs,
Technical debt, Waiting on you and Doing — and a page for a registered repo no team services."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


NOW = datetime.now(UTC)


def reading(root: str) -> dict:
    return {
        "name": "samscrape",
        "root": root,
        "remote": "git@github.com:eye/samscrape.git",
        "ledger": {
            "path": "docs/technical_debt.md",
            "entries": [
                {"id": "TD-301", "title": "recover stuck notices", "for_page": "pickable", "priority": "high", "owner": "grinder"},
                *(
                    {"id": f"TD-3{n}", "title": f"entry {n}", "for_page": "pickable", "priority": "medium", "owner": "grinder"}
                    for n in range(10, 15)
                ),
                {"id": "TD-283", "title": "decide the trail rows", "for_page": "for-you", "priority": "low", "owner": "paul"},
            ],
            "by_priority": {"high": 1, "medium": 5, "low": 1},
            "by_kind": {"pickable": 6, "design-first": 0, "for-you": 1, "other": 0},
        },
        "prs": {
            "open": [
                {"number": 805, "title": "docs: the cadence week", "created": _iso(NOW - timedelta(days=3)), "author": "eye", "branch": "docs", "url": "https://x/805"},
                {"number": 811, "title": "TD-301: recover", "created": _iso(NOW - timedelta(days=2)), "author": "eye", "branch": "td301", "url": "https://x/811", "draft": True},
            ],
            "windows": {w: {"opened": 1, "closed": 1} for w in ("day", "week", "month")},
        },
        "at": _iso(NOW),
    }  # fmt: skip


def fake(repos, fleet, doing=None, inbox=None):
    class Fake:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call(self, method, **kw):
            if method == "inbox":
                return (inbox or {}).get(kw.get("id"), {"entries": []})
            return {
                "repos": repos,
                "doing_log": doing or {},
                "list": fleet,
                "usage": {},
                "host": {"name": "kmaster"},
            }.get(method, {})

    return Fake


def rec(sid, root, **kw):
    return {
        "id": sid,
        "name": sid,
        "state": "working",
        "kind": "agent",
        "team": "grind",
        "repo": root,
        "dir": root,
        "unattended": True,
        **kw,
    }


def client(monkeypatch, tmp_path, Fake):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    from agentorc.ui import app as ui

    monkeypatch.setattr(ui, "LocalClient", Fake)
    monkeypatch.setattr(ui, "read_boards", lambda: ([], ""))
    return TestClient(ui.create_app())


def test_the_repo_page_draws_the_facets_and_the_four_lists(tmp_path, monkeypatch):
    root = str(tmp_path / "samscrape")
    fleet = [
        rec("tdgrind-1", root, git={"branch": "td301"}, progress=[{"ref": "TD-301", "status": "claimed", "pr": 811}]),
        rec("techlead-1", root, state="exited", role="techlead"),
    ]
    doing = {
        "grind": [
            {"id": "tdgrind-1", "text": "pushing the branch", "at": _iso(NOW)},
            {"id": "techlead-1", "text": "read #805", "at": _iso(NOW)},
        ]
    }
    inbox = {
        "techlead-1": {
            "entries": [
                {"kind": "ask", "pr": 811, "at": _iso(NOW - timedelta(minutes=40))},
                {"kind": "ask", "pr": 805, "at": _iso(NOW), "closed_by": "m-1"},
            ]
        }
    }
    c = client(monkeypatch, tmp_path, fake({root: reading(root)}, fleet, doing, inbox))
    html = c.get("/repo/samscrape").text
    assert 'class="tsum"' in html and "Technical debt (7 open)" in html  # the team's facets
    # Open PRs: newest last, the author the member whose branch it is, draft, the standing
    prs = html[html.index('id="prs"') : html.index('id="debt"')]
    assert prs.index("#805") < prs.index("#811")
    assert 'href="/focus/tdgrind-1">tdgrind-1</a>' in prs and ">draft<" in prs
    assert "waiting on review · 40m" in prs and ">reviewed<" in prs
    # Technical debt: four lists, held by, folded past four with +n more
    assert 'id="debt-pickable"' in html and "held by tdgrind-1" in html and "+2 more" in html
    assert html.count('class="rrow folded"') == 2
    # Doing: the chips and the rows
    assert 'data-who="tdgrind-1"' in html and "all (2)" in html and "pushing the branch" in html
    # the part is the same content without the chrome, for the page's re-read
    part = c.get("/repo/samscrape?part=1").text
    assert "<html" not in part and "Open PRs" in part


def test_a_repo_no_team_services_has_its_repo_facet_alone_and_an_unknown_one_is_404(tmp_path, monkeypatch):
    root = str(tmp_path / "samscrape")
    c = client(monkeypatch, tmp_path, fake({root: reading(root)}, []))
    html = c.get("/repo/samscrape").text
    assert "no team services this repo" in html and "Answer needed" not in html
    assert "Technical debt (7 open)" in html
    assert c.get("/repo/nope").status_code == 404


def test_the_ledger_lists_sort_by_priority_then_id_and_the_chips_count():
    from agentorc.ui.app import doing_chips, ledger_lists, pr_standing

    lists = {x["key"]: x for x in ledger_lists(reading("/r"), [])}
    assert [e["id"] for e in lists["pickable"]["rows"]][:2] == ["TD-301", "TD-310"] and lists["pickable"]["fold"] == 2
    assert lists["design-first"]["rows"] == []
    rows = [{"name": "a"}, {"name": "b"}, {"name": "a"}]
    assert [(c["label"], c["n"]) for c in doing_chips(rows)] == [("all", 3), ("a", 2), ("b", 1)]
    got = pr_standing(
        [{"kind": "ask", "pr": 5, "at": _iso(NOW), "closed_reason": "expired"}, {"kind": "note", "pr": 6}], NOW
    )
    assert got == {}  # an expired ask stands for nothing; a note is not a request for review
