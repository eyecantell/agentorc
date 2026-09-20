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


def test_a_team_with_nothing_live_keeps_its_card_below_the_live_ones_and_no_team():
    """Design §4.5a **team groups** (2026-09-18): the page used to go flat the moment the last badged
    session exited. Dead cards under a team's name are that team's; a definition nothing carries is
    a group with no members, because its card is where Start lives."""
    (g,) = team_groups([sess("ao-a", "a", team="ao-grind", state="exited")])
    assert g["team"] == "ao-grind" and g["live"] == 0 and g["ids"] == ["ao-a"] and not g["defined"]
    rows = [{"name": "zz-idle", "lead": "orc", "members": 2, "projects": ["p"], "wound_down": None}]
    views = [sess("ao-a", "a", team="ao-grind", state="exited"), sess("ao-b", "b"), sess("ao-c", "c", team="live")]
    groups = team_groups(views, rows)
    assert [g["team"] for g in groups] == ["live", "", "ao-grind", "zz-idle"]  # live, No team, stopped
    idle = groups[-1]
    assert idle["defined"] and idle["ids"] == [] and idle["def_lead"] == "orc" and idle["projects"] == ["p"]
    # a definition alone turns grouping on: a stopped team is a card, not a line above a flat grid
    assert [g["team"] for g in team_groups([sess("ao-b", "b")], rows)] == ["", "zz-idle"]


def test_two_teams_each_with_a_lead():
    views = [
        sess("ao-orc", "orchestrator-ao-1", team="ao-grind", project="agentorc", caps=["control"]),
        sess("ao-g1", "tdgrind-ao-1", team="ao-grind", project="agentorc", controllers=["ao-orc"], state="working"),
        sess("ao-g2", "tdgrind-ao-2", team="ao-grind", project="agentorc", controllers=["ao-orc"], state="needs-you"),
        sess("gu-orc", "orchestrator-gu", team="guardians", project="guardians", caps=["control"]),
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
        # holds `control` but nobody lists it as a controller: not this group's lead
        sess("ao-d", "d", team="solo", caps=["control"]),
    ]
    (g,) = team_groups(views)
    assert g["lead"] is None  # the header says "led by you"
    assert g["needs"] == 2
    assert g["live"] == 3


def test_unbadged_sessions_form_the_no_team_group_last():
    views = [sess("ao-x", "x"), sess("ao-orc", "orc", team="t", caps=["control"])]
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
            "capabilities": ["control"],
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
        strip={"teams": [], "source": "", "notes": []},  # the Teams strip: its own tests are in test_ui_teams.py
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


def test_a_lead_whose_own_badge_differs_is_still_found_but_keeps_its_card():
    """`ao team start` gives the lead its team's badge, but a hand-typed `ao new --team` need not —
    and a group whose members plainly name a controller must not claim it is led by the person.
    The lead is found across the fleet; its card stays under its own badge (review of PR #117)."""
    orc = sess("o", "orc", team="other", caps=["control"])
    w1 = sess("w1", "w1", team="t", controllers=["o"])
    w2 = sess("w2", "w2", team="t", controllers=["o"])
    groups = {g["team"]: g for g in team_groups([orc, w1, w2])}
    assert groups["t"]["lead"]["name"] == "orc" and groups["t"]["lead_elsewhere"] is True
    assert groups["t"]["ids"] == ["w1", "w2"]  # the lead's card is not moved into this group
    assert groups["other"]["ids"] == ["o"] and groups["other"]["lead"] is None


def test_the_top_bar_inbox_opens_the_page_and_shows_its_count_only_above_zero(monkeypatch, tmp_path):
    """design §4.5a Org top bar **Inbox** (TD-069 step 1, 2026-09-19): the control is a link to
    `/inbox` — the dialog it opened until then is retired — and its number, the **Needs you**
    section, shows at one and not at zero. Its hover text says what it counts and that it is not
    the Org's needs-you count."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import templates

    def render(n):
        return templates.get_template("org.html").render(
            sessions=[],
            groups=None,
            counts=dict.fromkeys(("needs-you", "limited", "stalled?"), 0),
            strip={"teams": [], "source": "", "notes": []},
            host="kmaster",
            active="Org",
            agent_down=False,
            volatile=False,
            usage={},
            person_needs=n,
        )

    zero, one = render(0), render(1)
    for html in (zero, one):
        assert 'id="personinbox"' in html and 'href="/inbox"' in html
        assert 'id="personbox"' not in html and 'id="personlist"' not in html  # the dialog is gone
        # §4.5a, TD-069 step 2: the two counts say precisely how they differ, not merely that they do
        assert "the session states the Org counts too" in html and "the states alone" in html
    assert 'class="badge needs hidden" id="personneeds"></span>' in zero
    assert 'class="badge needs" id="personneeds">1</span>' in one


def test_ready_to_close_needs_no_live_member_and_reads_it_from_the_control_graph(monkeypatch, tmp_path):
    """Design §4.2 (2026-09-17): a lead idle between rounds, log pushed, used to read *ready to
    close ✓* over working members. The item comes from `controllers`, not a role — and a session
    that controls nothing never sees it."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    clean = {"dirty": 0, "ahead": 0, "upstream": "origin/x", "branch": "x"}
    lead = {"id": "ao-orc", "name": "orc", "state": "idle", "dir": "/tmp/a", "kind": "agent", "git": clean, "tail": []}
    worker = {"id": "ao-g1", "name": "g1", "state": "working", "dir": "/tmp/b", "kind": "agent", "git": clean,
              "controllers": ["ao-orc"], "tail": []}  # fmt: skip
    fleet = [lead, worker]
    v = view(lead, fleet)
    assert v["ready"][-1] == ("no live members (g1 — stop the team first)", False)
    assert "ready to close ✓" not in templates.get_template("card.html").render(s=v)
    # a plain worker's checklist is what it always was
    assert [n for n, _ in view(worker, fleet)["ready"]] == [
        "tree clean", "branch pushed", "no subagents running", "outcomes reported",
    ]  # fmt: skip
    # design §4.2, §4.10 *Outcomes* (TD-079): the person answered and has not been told what came
    # of it — the same fact `ao progress none` is refused on, for a session that never declares
    owing = view({**worker, "mail": {"owed": ["m-1", "m-2"]}}, fleet)["ready"]
    assert owing[-1] == ("outcomes reported (2 owed)", False)
    # the member gone: the item passes, and the card says so
    worker["state"] = "closed"
    v = view(lead, fleet)
    assert v["ready"][-1] == ("no live members", True)
    assert "ready to close ✓" in templates.get_template("card.html").render(s=v)
    # no role and no grant involved: any session a live one lists as a controller
    assert view({**lead, "id": "ao-x"}, [{**worker, "state": "idle", "controllers": ["ao-x"]}])["ready"][-1][1] is False
    # the fleet asked for and not got: unknown is not none (review of PR #195)
    unknown = view(lead, [lead], fleet_known=False)["ready"][-1]
    assert unknown[1] is False and unknown[0].startswith("members unknown")


def test_a_live_team_that_needs_a_person_comes_above_the_other_live_teams():
    """Design §4.5 screen 1 (2026-09-18): one order, no control. The Urgent first / Pinned toggle is
    gone; what it did between cards still happens inside a team, and between teams too."""
    views = [sess("ao-a", "a", team="alpha"), sess("ao-z", "z", team="zeta", state="needs-you"), sess("ao-n", "n")]
    assert [g["team"] for g in team_groups(views)] == ["zeta", "alpha", ""]


def test_the_teams_line_says_the_identity_mode_unless_the_host_enforces_it(tmp_path, monkeypatch):
    """design §4.8a: the Org's teams line says *identity: observe* or *identity: off* — a host that
    is not enforcing is not yet protected — and says **nothing** under `enforce`, which is the host
    that is. A note that was always there would stop being read."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import identity_note, templates

    assert "observe" in identity_note({"mode": "observe"}) and "not enforcing" in identity_note({"mode": "observe"})
    assert identity_note({"mode": "off"}).startswith("identity: off")
    assert identity_note({"mode": "enforce"}) == "" and identity_note(None) == "" and identity_note({}) == ""

    def page(note):
        return templates.get_template("org.html").render(
            sessions=[], groups=None, strip={"teams": [{"name": "t"}], "source": "x", "notes": [], "elsewhere": ""},
            counts={}, host="kmaster", active="Org", agent_down=False, volatile=False, usage={},
            person_needs=0, node_banner="", identity_note=note,
        )  # fmt: skip

    html = page(identity_note({"mode": "observe"}))
    assert 'id="identitynote"' in html and "identity: observe" in html
    assert 'id="identitynote"' not in page(identity_note({"mode": "enforce"}))


def test_usage_chip_prints_each_profiles_worst_window(tmp_path, monkeypatch):
    """TD-073: the top bar's chip is one span per profile showing that profile's **worst** window —
    the label and number the adapter gave — with every window on hover. No field name of any one
    tool appears in the template, so a profile with one daily window renders the same way."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import templates

    usage = {
        "grind": {
            "windows": [
                {"label": "5h", "pct": 19, "resets": "2026-09-20T22:00:00Z"},
                {"label": "wk", "pct": 88, "resets": "2026-09-24T00:00:00Z"},
            ],
            "fetched": "2026-09-20T20:00:00Z",
        },
        "openai": {"windows": [{"label": "day", "pct": 100, "resets": None}], "fetched": "x"},
        "quietly": {"windows": [], "fetched": "x"},  # an adapter that reports no quota: no chip
    }
    html = templates.get_template("org.html").render(
        sessions=[], groups=None, strip={"teams": [], "source": "", "notes": [], "elsewhere": ""},
        counts={}, host="kmaster", active="Org", agent_down=False, volatile=False, usage=usage,
        person_needs=0, node_banner="", identity_note="",
    )  # fmt: skip
    assert "grind wk 88%" in html and "5h 19%" not in html.split("grind wk 88%")[1].split("</span>")[0]
    assert 'data-profile="grind" data-pct="88" class="near"' in html
    assert "wk 88% (resets 2026-09-24T00:00:00Z) · 5h 19% (resets 2026-09-20T22:00:00Z)" in html
    assert 'data-profile="openai" data-pct="100" class="cap"' in html and "openai day 100%" in html
    assert 'data-profile="quietly"' not in html  # no windows, no chip
    assert "five_hour" not in html and "weekly" not in html
