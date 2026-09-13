"""The Org page's team groups (design §4.5a **team groups** / **team badge**, §4.9): the pure
grouping function, and one render of the page template over its output."""

from __future__ import annotations

import pytest

from agentorc.ui.app import team_groups

pytestmark = pytest.mark.unit


def sess(sid, name, *, team=None, project=None, state="idle", rank=5, controllers=(), caps=()):
    """A session view as `view()` hands it to the template: only the keys grouping reads."""
    return {
        "id": sid,
        "name": name,
        "team": team,
        "project": project,
        "state": state,
        "state_class": state,
        "state_label": state,
        "scraped": False,
        "rank": rank,
        "controllers": list(controllers),
        "capabilities": list(caps),
    }


def test_no_badge_anywhere_is_the_flat_grid():
    views = [sess("ao-a", "a"), sess("ao-b", "b")]
    assert team_groups(views) is None
    # a badge on a *dead* session does not turn grouping on: the page is about live work
    assert team_groups([sess("ao-a", "a", team="ao-grind", state="exited")]) is None


def test_two_teams_each_with_a_lead():
    views = [
        sess("ao-orc", "orchestrator-ao-1", team="ao-grind", project="agentorc", caps=["orchestrate"]),
        sess("ao-g1", "tdgrind-ao-1", team="ao-grind", project="agentorc", controllers=["ao-orc"], state="working"),
        sess("ao-g2", "tdgrind-ao-2", team="ao-grind", project="agentorc", controllers=["ao-orc"], state="needs-you"),
        sess("gu-orc", "orchestrator-gu", team="guardians", project="guardians", caps=["orchestrate"]),
        sess("gu-1", "api-grinder", team="guardians", project="guardians-api", controllers=["gu-orc"]),
    ]
    groups = team_groups(views)
    assert [g["team"] for g in groups] == ["ao-grind", "guardians"]
    ao, gu = groups
    assert ao["lead"]["name"] == "orchestrator-ao-1" and ao["ids"][0] == "ao-orc"  # the lead's card first
    assert ao["projects"] == ["agentorc"]
    assert gu["projects"] == ["guardians", "guardians-api"]  # deduplicated across the members
    assert gu["lead"]["id"] == "gu-orc"


def test_a_team_with_no_lead_and_the_needs_you_count():
    views = [
        sess("ao-a", "a", team="solo", state="needs-you"),
        sess("ao-b", "b", team="solo", state="needs-you"),
        sess("ao-c", "c", team="solo", state="exited"),
        # holds `orchestrate` but nobody lists it as a controller: not this group's lead
        sess("ao-d", "d", team="solo", caps=["orchestrate"]),
    ]
    (g,) = team_groups(views)
    assert g["lead"] is None  # the header says "led by you"
    assert g["needs"] == 2
    assert g["live"] == 3


def test_unbadged_sessions_form_the_no_team_group_last():
    views = [sess("ao-x", "x"), sess("ao-orc", "orc", team="t", caps=["orchestrate"])]
    groups = team_groups(views)
    assert [g["label"] for g in groups] == ["t", "No team"]
    assert groups[-1]["team"] == "" and groups[-1]["lead"] is None


def test_members_sort_urgent_first_within_a_group():
    views = [
        sess("ao-i", "idle-one", team="t", rank=5),
        sess("ao-n", "needs", team="t", rank=1, state="needs-you"),
        sess("ao-w", "working", team="t", rank=4),
        sess("ao-i2", "idle-also", team="t", rank=5),
    ]
    (g,) = team_groups(views)
    assert g["ids"] == ["ao-n", "ao-w", "ao-i2", "ao-i"]  # rank, then name


def test_the_page_renders_its_groups_and_team_badges(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import templates, view

    records = [
        {
            "id": "ao-orc",
            "name": "orchestrator-ao-1",
            "state": "idle",
            "dir": "/tmp/agentorc",
            "kind": "agent",
            "team": "ao-grind",
            "project": "agentorc",
            "capabilities": ["orchestrate"],
        },
        {
            "id": "ao-g1",
            "name": "tdgrind-ao-1",
            "state": "working",
            "dir": "/tmp/agentorc/wt",
            "kind": "agent",
            "team": "ao-grind",
            "project": "agentorc",
            "controllers": ["ao-orc"],
            "tail": ["…"],
        },
        {"id": "ao-sh", "name": "sh1", "state": "idle", "dir": "/tmp/x", "kind": "agent", "adapter": "shell"},
    ]
    vs = [view(r, records) for r in records]
    groups = team_groups(vs)
    html = templates.get_template("org.html").render(
        sessions=vs,
        groups=groups,
        counts=dict.fromkeys(("needs-you", "limited", "stalled?"), 0),
        host="kmaster",
        active="Org",
        agent_down=False,
        volatile=False,
        usage={},
    )
    assert '<span class="h1">Org</span>' in html and "Org · agentorc" in html
    assert 'data-team="ao-grind"' in html and "No team" in html
    assert 'class="badge team"' in html  # the card's team badge
    assert "orchestrator-ao-1" in html and 'id="card-ao-g1"' in html
    assert html.index('data-team="ao-grind"') < html.index('id="card-ao-sh"')  # No team last
