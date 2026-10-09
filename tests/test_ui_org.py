"""The Org page's team groups (design §4.5a **team groups** / **team badge**, §4.9): the pure
grouping function, and one render of the page template over its output."""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import pytest

from agentorc.ui.app import team_groups, templates

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
    rows = [{"name": "zz-idle", "manager": "orc", "members": 2, "projects": ["p"], "wound_down": None}]
    views = [sess("ao-a", "a", team="ao-grind", state="exited"), sess("ao-b", "b"), sess("ao-c", "c", team="live")]
    groups = team_groups(views, rows)
    assert [g["team"] for g in groups] == ["live", "", "ao-grind", "zz-idle"]  # live, No team, stopped
    idle = groups[-1]
    assert idle["defined"] and idle["ids"] == [] and idle["def_manager"] == "orc" and idle["projects"] == ["p"]
    # a definition alone turns grouping on: a stopped team is a card, not a line above a flat grid
    assert [g["team"] for g in team_groups([sess("ao-b", "b")], rows)] == ["", "zz-idle"]
    # a definition with a techlead seat (TD-075, design §4.9b) names it on the card, as `ao team list` does
    assert idle["def_techlead"] is None
    (seated,) = team_groups([], [{**rows[0], "techlead": "tl-1"}])
    head = templates.get_template("group_head.html").render(g=seated)
    assert "Manager orc · Tech Lead tl-1 · 2 members" in head
    assert "Tech Lead" not in templates.get_template("group_head.html").render(g=idle)


def test_a_stopped_team_offers_forget_all_but_never_on_a_card_with_unpushed_work():
    """Design §4.5a team card **Forget all** (TD-071 item 1): on a team with nothing live, one
    confirm, then each card's Forget — never a card with the dirty / unpushed flag, which the confirm
    names apart. Absent while anything of the team is live, and when every card is flagged."""
    head = templates.get_template("group_head.html")
    a = {**sess("ao-a", "a", team="t", state="exited"), "flag": ""}
    b = {**sess("ao-b", "b", team="t", state="closed"), "flag": ""}
    c = {**sess("ao-c", "c", team="t", state="exited"), "flag": "dirty · 3 unpushed"}
    (g,) = team_groups([a, b, c])
    assert [m["id"] for m in g["forget"]] == ["ao-a", "ao-b"] and [m["id"] for m in g["forget_kept"]] == ["ao-c"]
    html = head.render(g=g)
    assert 'data-forget-all="t" data-ids="ao-a ao-b"' in html and ">Forget all</button>" in html
    assert "Forget 2 sessions of t: a, b?" in html and "c (dirty · 3 unpushed)" in html
    (live,) = team_groups([a, {**sess("ao-w", "w", team="t", state="working"), "flag": ""}])
    assert live["forget"] == [] and "Forget all" not in head.render(g=live)
    # an on-call seat is never forgotten — its card offers no Forget while the definition names it
    seat = {**sess("ao-s", "s", team="t", state="exited"), "flag": "", "seat": True}
    (seated,) = team_groups([a, seat])
    assert [m["id"] for m in seated["forget"]] == ["ao-a"] and seated["forget_kept"] == []
    (flagged,) = team_groups([c])
    assert "Forget all" not in head.render(g=flagged)
    # *No team* is not a team: no Forget all there, whatever it holds
    groups = team_groups([a, {**sess("ao-n", "n", state="exited"), "flag": ""}])
    assert [g["forget"] for g in groups if not g["team"]] == [[]]


def test_every_team_header_says_how_much_unread_mail_its_cards_hold():
    """Design §4.5a team header **✉ n** (TD-071 item 2): the sum of the cards' unread chips, nothing
    at zero, drawn on any team (TD-194) and shown folded or not (TD-418: no fold-only class)."""
    head = templates.get_template("group_head.html")
    mailed = {**sess("ao-a", "a", team="t", state="exited"), "unread": 19}
    (g,) = team_groups([mailed, sess("ao-b", "b", team="t", state="exited")])
    html = head.render(g=g)
    assert g["unread"] == 19 and 'class="badge unread teammail"' in html and "✉ 19" in html
    assert "foldmail" not in html and 'foldonly">✉' not in html
    (quiet,) = team_groups([sess("ao-a", "a", team="t", state="exited")])
    assert "teammail" not in head.render(g=quiet)
    (live,) = team_groups([{**sess("ao-a", "a", team="t", state="working"), "unread": 2}])
    assert 'class="badge unread teammail"' in head.render(g=live)


def test_a_folded_live_teams_header_carries_its_counts_by_state():
    """Design §4.5a *team card: fold* (TD-194), *team groups* (TD-418): a live team's header carries
    the session count once, on the fold, and the counts by state only for when it is folded (CSS);
    *n ready to close* is a mark on every header, and so is the needs-you pill."""
    head = templates.get_template("group_head.html")
    members = [sess("ao-a", "a", team="t", state="needs-you"), sess("ao-b", "b", team="t", state="working")]
    (g,) = team_groups(members)
    assert g["summary"]
    html = head.render(g=g)
    assert "unfoldonly" not in html and html.count("2 sessions") == 1 and ">▾ 2 sessions</button>" in html
    assert 'class="meta counts foldonly">·' in html and "1 working" in html
    assert "1 needs you" in html and 'data-fold="t" data-n="2" aria-expanded="true"' in html
    assert "ready to close" not in html
    html = head.render(g={**g, "ready": 2})
    assert '<span class="meta readymark"' in html and ">2 ready to close</span>" in html


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
    assert ao["manager"]["name"] == "orchestrator-ao-1" and ao["ids"][0] == "ao-orc"  # the lead's card first
    assert ao["projects"] == ["agentorc"]
    assert gu["projects"] == ["guardians", "guardians-api"]  # deduplicated across the members
    assert gu["manager"]["id"] == "gu-orc"


def test_a_team_with_no_lead_and_the_needs_you_count():
    views = [
        sess("ao-a", "a", team="solo", state="needs-you"),
        sess("ao-b", "b", team="solo", state="needs-you"),
        sess("ao-c", "c", team="solo", state="exited"),
        # holds `control` but nobody lists it as a controller: not this group's lead
        sess("ao-d", "d", team="solo", caps=["control"]),
    ]
    (g,) = team_groups(views)
    assert g["manager"] is None  # the header says "managed by you"
    assert g["needs"] == 2
    assert g["live"] == 3


def test_unbadged_sessions_form_the_no_team_group_last():
    views = [sess("ao-x", "x"), sess("ao-orc", "orc", team="t", caps=["control"])]
    groups = team_groups(views)
    assert [g["label"] for g in groups] == ["t", "No team"]
    assert groups[-1]["team"] == "" and groups[-1]["manager"] is None


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
    assert '<span class="h1">Org</span>' in html and "Org · ShiftLead" in html
    # the top bar's tabs are the pages that exist: no dead placeholder tabs (design §4.5 screens 4, 5, 7; TD-123)
    assert 'class="tab off"' not in html
    assert not any(f">{dead}<" in html for dead in ("Resumable", "Commands", "Attention"))
    assert 'data-team="ao-grind"' in html and "No team" in html
    # a live team's members are compact cards, which carry no team badge (TD-176, §4.5a *card: compact*)
    assert ' compact"' in html and 'class="badge team ingroup"' not in html
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
    assert groups["t"]["manager"]["name"] == "orc" and groups["t"]["manager_elsewhere"] is True
    assert groups["t"]["ids"] == ["w1", "w2"]  # the lead's card is not moved into this group
    assert groups["other"]["ids"] == ["o"] and groups["other"]["manager"] is None


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
        "tree clean", "branch pushed", "no subagents running", "outcomes reported", "mail read",
    ]  # fmt: skip
    # design §4.2, §4.10 *Outcomes* (TD-079): the person answered and has not been told what came
    # of it — the same fact `ao progress none` is refused on, for a session that never declares
    owing = view({**worker, "mail": {"owed": ["m-1", "m-2"]}}, fleet)["ready"]
    assert owing[-2] == ("outcomes reported (2 owed)", False)
    # design §4.2, §4.9a (TD-141): mail nobody read — the fact `ao progress none` is refused on
    unread = view({**worker, "unread": 3}, fleet)["ready"]
    assert unread[-1] == ("mail read (3 unread — `ao inbox`)", False)
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


def test_the_org_says_restart_pending_when_hosts_yml_moved_under_the_agent(tmp_path, monkeypatch):
    """design §5 (TD-149 (5)): `local.name`, `home:` and `local.identity` are the agent's from its
    start and the page's per request, so a hand edit since is named — each value that moved, what
    the agent runs as against what the file says — and nothing is said while they agree."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("home: hub\nlocal:\n  name: box\n  identity: enforce\n")
    from agentorc.ui.app import restart_note, templates

    agrees = {"host": "box", "home": "hub"}
    assert restart_note(agrees, {"mode": "enforce"}) == ""
    assert restart_note(None, None) == "" and restart_note({}, {}) == ""  # nothing answered: nothing compared
    note = restart_note({"host": "kmaster", "home": "kmaster"}, {"mode": "observe"})
    assert "name kmaster → box" in note and "home kmaster → hub" in note and "identity observe → enforce" in note
    assert "restart pending" in note
    assert restart_note(agrees, {"mode": "observe"}).count("→") == 1

    html = templates.get_template("org.html").render(
        sessions=[], groups=None, strip={"teams": [{"name": "t"}], "source": "x", "notes": [], "elsewhere": ""},
        counts={}, host="box", active="Org", agent_down=False, volatile=False, usage={},
        person_needs=0, node_banner="", identity_note="", restart_note=note,
    )  # fmt: skip
    assert 'id="restartnote"' in html and "restart pending" in html


def test_usage_chip_prints_each_profiles_worst_window(tmp_path, monkeypatch):
    """TD-073: the top bar's chip is one span per profile showing that profile's **worst** window —
    the label and number the adapter gave — with every window on hover. No field name of any one
    tool appears in the template, so a profile with one daily window renders the same way."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from datetime import timedelta

    from agentorc.ui.app import templates

    now = datetime.now(UTC)  # the page draws against the clock, so the reading is a minute old
    usage = {
        "grind": {
            "windows": [
                {"label": "5h", "pct": 19, "resets": (now + timedelta(hours=2)).isoformat()},
                {"label": "week", "pct": 88, "resets": (now + timedelta(days=4)).isoformat()},
            ],
            "fetched": (now - timedelta(minutes=1)).isoformat(),
        },
        "openai": {"windows": [{"label": "day", "pct": 100, "resets": None}], "fetched": "x"},
        "quietly": {"windows": [], "fetched": "x"},  # an adapter that reports no quota: no chip
    }
    html = templates.get_template("org.html").render(
        sessions=[], groups=None, strip={"teams": [], "source": "", "notes": [], "elsewhere": ""},
        counts={}, host="kmaster", active="Org", agent_down=False, volatile=False, usage=usage,
        person_needs=0, node_banner="", identity_note="",
    )  # fmt: skip
    assert "grind · week 88%" in html and "5h 19%" not in html.split("grind · week 88%")[1].split("</span>")[0]
    assert 'data-pct="88" data-near="1" class="near"' in html
    # the reading the chip was drawn from rides on it, so `app.js` can draw it again as it ages (TD-233)
    assert 'data-account="grind" data-usage=\'{' in html
    wk, fh = usage["grind"]["windows"][1]["resets"], usage["grind"]["windows"][0]["resets"]
    assert f"week 88% (resets {wk})\n5h 19% (resets {fh})" in html
    assert 'data-pct="100" data-near="1" class="cap"' in html and "openai · day 100%" in html
    assert 'data-account="quietly"' not in html  # no windows, no chip
    assert "five_hour" not in html and "weekly" not in html


# One profile's usage as the host agent holds it after a poll (design §4.2, TD-087), for the chip's
# rule in both its homes — `usage_chip` for the server's render and `AO.usageChip` for a pushed event.
USAGE_CASES = {
    "fresh": {"windows": [{"label": "5h", "pct": 19, "resets": "r1"}, {"label": "week", "pct": 88, "resets": None}],
              "fetched": "2026-09-20T20:00:00Z", "reason": "ok"},
    "legacy": {"windows": [{"label": "day", "pct": 40, "resets": "r2"}], "fetched": "x"},  # before TD-087: no reason
    "held_429": {"windows": [{"label": "week", "pct": 49, "resets": "r3"}], "fetched": "2026-09-20T20:00:00Z",
                 "reason": "rate_limited", "retry_after": 1800},
    "held_cap": {"windows": [{"label": "5h", "pct": 100, "resets": "r4"}], "fetched": "f", "reason": "error"},
    "held_odd_wait": {"windows": [{"label": "week", "pct": 49}], "reason": "rate_limited", "retry_after": 150},
    "never_read": {"reason": "no_credentials"},
    "unknown_word": {"reason": "brand_new_reason"},
    "no_quota": {"windows": [], "fetched": "x", "reason": "ok"},
    "junk_window": {"windows": [{"label": "5h", "pct": "19"}, "nonsense"], "reason": "ok"},
    "not_a_dict": "garbage",
    # the gate's reading attached (§6, TD-100): a line per reserved window, and worst is the smallest gap
    "lined": {"windows": [{"label": "5h", "pct": 40, "resets": "r5"}, {"label": "week", "pct": 61, "resets": "r6"}],
              "reason": "ok", "lines": [{"label": "week", "pct": 61, "line": 70, "resets": "r6",
                                         "next": "2026-09-24T07:00:00Z", "reserve": {"per_day": 10}}]},
    "unreserved_outranks": {"windows": [{"label": "5h", "pct": 97, "resets": "r7"}, {"label": "week", "pct": 40}],
                            "reason": "ok", "lines": [{"label": "week", "line": 70, "next": None, "reserve": 30}]},
    "flat_far": {"windows": [{"label": "week", "pct": 85}], "reason": "ok",
                 "lines": [{"label": "week", "line": 100, "next": "n", "reserve": 0}, {"label": "5h", "line": 1}]},
    "no_line_made": {"windows": [{"label": "week", "pct": 85}], "reason": "ok",
                     "lines": [{"label": "week", "line": None, "reserve": {"per_day": 10}}]},
    # one account's chip (TD-122): the profiles sharing it, their lines and sessions, on hover
    "shared": {"windows": [{"label": "week", "pct": 24, "resets": "r8"}], "reason": "ok", "account": "paul",
               "tool": "Claude", "lines": [{"label": "week", "line": 70.5, "next": None, "reserve": 20}],
               "profiles": [{"name": "grind", "lines": [{"label": "week", "line": 70.5, "next": "n1",
                                                  "reserve": {"per_day": 10}}, {"label": "5h", "line": 50}],
                             "sessions": ["grinder-ao-1", "grinder-ao-2"]},
                            {"name": "default", "lines": [], "sessions": []}, "junk"]},
    "shared_unread": {"reason": "rate_limited", "profiles": [{"name": "grind", "sessions": ["g1"]}]},
    # a metered account (§4.5a **usage**, TD-151 slice 5): spend over amount, tokens where unpriced
    "metered": {"reason": "ok", "windows": [
        {"label": "day", "pct": 82, "resets": "d1", "amount": {"value": 5.0, "unit": "$"},
         "spent": {"tokens": {"input": 4_100_000, "output": 2_000, "cache_read": 0, "cache_write": 0},
                   "total": 4_102_000, "cost": 4.1}},
        {"label": "week", "pct": None, "resets": "w1",
         "spent": {"tokens": {"input": 12_300_000}, "total": 12_300_000, "cost": 1234.5}},
        {"label": "month", "pct": 100, "resets": "m1", "amount": {"value": 10_000_000, "unit": "tok"},
         "spent": {"tokens": {}, "total": 12_300_000, "cost": 1234.5}}]},
    # each profile's own amount on the hover (TD-151): the chip is over the smallest
    "metered_shared": {"reason": "ok", "tool": "Claude", "account": "key", "windows": [
        {"label": "day", "pct": 82, "resets": "d1", "amount": {"value": 5.0, "unit": "$"},
         "spent": {"tokens": {"input": 4_100_000}, "total": 4_100_000, "cost": 4.1}}],
        "profiles": [{"name": "api", "lines": [], "sessions": ["w1"], "amounts": [
                         {"label": "day", "amount": {"value": 5.0, "unit": "$"}},
                         {"label": "week", "amount": {"value": 2_000_000, "unit": "tok"}}, "junk",
                         {"label": "month", "amount": {"value": True, "unit": "$"}}]},
                     {"name": "api2", "lines": [], "sessions": [], "amounts": [
                         {"label": "day", "amount": {"value": 10.0, "unit": "$"}}]}]},
    "metered_ties": {"reason": "ok", "windows": [  # half to even in both homes: 2k and 1.2M
        {"label": "day", "pct": 3, "resets": "d", "amount": {"value": 1_250_000, "unit": "tok"},
         "spent": {"tokens": {"input": 2_500, "output": 3_500}, "total": 1_250_000, "cost": None}}]},
    # the window's turns and pace on hover (TD-151, decided 2026-10-07)
    "metered_paced": {"reason": "ok", "windows": [
        {"label": "day", "pct": 64, "resets": "2026-09-21T06:00:00Z", "amount": {"value": 5.0, "unit": "$"},
         "spent": {"tokens": {"input": 3_000_000}, "total": 3_000_000, "cost": 3.2}, "turns": 412,
         "pace": {"per_hour": 0.4, "unit": "$", "at": "2026-09-20T22:30:00Z"}},
        {"label": "week", "pct": 30, "resets": "2026-09-28T06:00:00Z", "amount": {"value": 10_000_000, "unit": "tok"},
         "spent": {"tokens": {"input": 3_000_000}, "total": 3_000_000, "cost": 3.2}, "turns": 1,
         "pace": {"per_hour": 2_000.0, "unit": "tok", "at": "2026-09-23T10:00:00Z"}},
        {"label": "month", "pct": None, "resets": "m", "spent": {"tokens": {}, "total": 0, "cost": None},
         "turns": True, "pace": {"per_hour": True, "unit": "$", "at": None}},
        {"label": "5h", "pct": None, "resets": "f", "spent": {"tokens": {}, "total": 0, "cost": None},
         "pace": {"per_hour": 3, "unit": "x", "at": "2026-09-20T22:30:00Z"}}]},
    "metered_unpriced": {"reason": "error: OSError", "windows": [
        {"label": "day", "pct": None, "resets": None,
         "spent": {"tokens": {"input": 900}, "total": 900, "cost": None}}]},
    # the age (§4.5a *The age*, TD-233 slice 1), against USAGE_NOW
    "aged_7m": {"windows": [{"label": "week", "pct": 40, "resets": "2026-09-25T00:00:00Z"}],
                "fetched": "2026-09-20T19:56:00Z", "reason": "ok"},
    "aged_1h": {"windows": [{"label": "week", "pct": 100, "resets": "2026-09-25T00:00:00Z"}],
                "fetched": "2026-09-20T18:59:00Z", "reason": "rate_limited", "source": "reported"},
    "aged_4h": {"windows": [{"label": "week", "pct": 88, "resets": "2026-09-25T00:00:00Z"}],
                "fetched": "2026-09-20T16:00:00Z", "reason": "rate_limited"},
    "aged_2d": {"windows": [{"label": "week", "pct": 88, "resets": "2026-09-25T00:00:00Z"}],
                "fetched": "2026-09-18T16:00:00Z", "reason": "ok"},
    "past_reset": {"windows": [{"label": "5h", "pct": 97, "resets": "2026-09-20T19:00:00Z"},
                               {"label": "week", "pct": 40, "resets": "2026-09-25T00:00:00Z"}],
                   "fetched": "2026-09-20T18:00:00Z", "reason": "ok"},
    "all_reset": {"windows": [{"label": "5h", "pct": 97, "resets": "2026-09-20T19:00:00Z"}],
                  "fetched": "2026-09-20T18:00:00Z", "reason": "ok"},
    # the gate projecting (§6 *A reading the gate can no longer trust*, TD-233): the projection is shown
    "projected": {"windows": [{"label": "week", "pct": 88, "resets": "2026-09-25T00:00:00Z"},
                              {"label": "5h", "pct": 50, "resets": "2026-09-20T21:00:00Z"}],
                  "fetched": "2026-09-20T14:00:00Z", "reason": "ok",
                  "lines": [{"label": "week", "pct": 96.0, "line": 95, "next": None, "reserve": 5,
                             "projected": {"from": 88, "rate": 1.33, "age": 21780}}]},
    "projected_under": {"windows": [{"label": "week", "pct": 60, "resets": "2026-09-25T00:00:00Z"}],
                        "fetched": "2026-09-20T18:00:00Z", "reason": "ok",
                        "lines": [{"label": "week", "pct": 62.5, "line": 95, "next": None, "reserve": 5,
                                   "projected": {"from": 60, "rate": 1.2, "age": 7380}}]},
    "no_rate": {"windows": [{"label": "week", "pct": 60, "resets": "2026-09-25T00:00:00Z"}],
                "fetched": "2026-09-20T18:00:00Z", "reason": "ok",
                "lines": [{"label": "week", "pct": 60, "line": 95, "next": None, "reserve": 5, "unknown": "rate"}]},
}  # fmt: skip
USAGE_NOW = datetime(2026, 9, 20, 20, 3, tzinfo=UTC)  # three minutes after `fresh` was read


def _clock(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%H:%M")


def test_a_refused_usage_poll_keeps_the_held_reading_and_its_age_says_how_old_it_is():
    """design §4.5a **usage** chip, TD-087, TD-230 (TD-233 slice 1). The chip was empty through three
    promotes because the endpoint answered 429 and every failure was one silence, so the host agent
    keeps the last good reading with the adapter's `reason` beside it and the chip draws it — **held,
    not out**. It then said *· stale*, which on 2026-09-28 read the same at five minutes and at six
    hours while the account ran to two points from its line. So the word went for **the age**: past
    five minutes it follows the number, past fifteen the chip is dimmed (still red at a cap), past
    three hours — or once the window's reset has passed — the number is no longer offered as the
    account's: *unknown since 16:00 (was 88%)*. The hover says when it was read, from where, and why
    the poll since failed. A refusal with nothing ever held is *no reading yet*; a tool that reports
    no quota has no chip."""
    from agentorc.ui.app import usage_chip

    got = {k: usage_chip("grind", u, USAGE_NOW) for k, u in USAGE_CASES.items()}
    read = f"read at {_clock('2026-09-20T20:00:00Z')}, 3m ago, asked of the endpoint"
    assert got["fresh"] == {"text": "grind · week 88%", "title": f"{read}\nweek 88% (resets ?)\n5h 19% (resets r1)",
                            "pct": 88, "cls": "near", "near": True}  # fmt: skip
    assert got["legacy"]["text"] == "grind · day 40%" and got["legacy"]["cls"] == ""  # no time on it: no age
    held = got["held_429"]  # three minutes old and refused since: no mark, the why on hover
    assert held["text"] == "grind · week 49%" and held["cls"] == "" and held["pct"] == 49
    assert held["title"].startswith(f"{read}\nthe last poll was refused: ")
    assert "rate-limited by the usage endpoint, which asked to be left 30 min" in held["title"]
    assert held["title"].endswith("week 49% (resets r3)")  # every window is still on hover
    assert got["held_cap"]["cls"] == "cap" and "could not be read" in got["held_cap"]["title"]
    assert "asked to be left 3 min" in got["held_odd_wait"]["title"]  # rounded up, in both homes alike
    assert got["never_read"]["text"] == "grind: no reading yet" and got["never_read"]["cls"] == "unknown"
    assert "no credentials for this profile" in got["never_read"]["title"]
    assert "brand_new_reason" in got["unknown_word"]["title"]  # a word we do not know is shown, not dropped
    assert got["no_quota"] is None and got["not_a_dict"] is None
    assert got["junk_window"] is None  # a window whose number is not a number is not drawn from
    # the age: printed past five minutes, dimmed past fifteen, still red at a cap
    assert got["aged_7m"]["text"] == "grind · week 40% · 7m" and got["aged_7m"]["cls"] == ""
    assert got["aged_1h"]["text"] == "grind · week 100% · 1h" and got["aged_1h"]["cls"] == "cap old"
    one = _clock("2026-09-20T18:59:00Z")
    assert got["aged_1h"]["title"].startswith(f"read at {one}, 1h ago, reported by a session")
    # past three hours the number is no longer the account's; it stays on the hover
    assert got["aged_4h"] == {
        "text": f"grind · week unknown since {_clock('2026-09-20T16:00:00Z')} (was 88%)",
        "title": f"read at {_clock('2026-09-20T16:00:00Z')}, 4h ago, asked of the endpoint\nthe last poll was"
        " refused: rate-limited by the usage endpoint\nweek 88% (resets 2026-09-25T00:00:00Z)",
        "pct": 0, "cls": "unknown", "near": False,
    }  # fmt: skip
    days = datetime(2026, 9, 18, 16, tzinfo=UTC).astimezone().strftime("%a %H:%M")
    assert got["aged_2d"]["text"] == f"grind · week unknown since {days} (was 88%)"  # a day old: the day too
    # a window past its reset is unknown until read again, and never the worst while another is not
    assert got["past_reset"]["text"] == "grind · week 40% · 2h" and got["past_reset"]["near"] is False
    assert f"5h unknown since its reset at {_clock('2026-09-20T19:00:00Z')} (was 97%)" in got["past_reset"]["title"]
    assert got["all_reset"]["text"] == f"grind · 5h unknown since {_clock('2026-09-20T19:00:00Z')} (was 97%)"
    assert got["all_reset"]["cls"] == "unknown" and got["all_reset"]["near"] is False
    assert all("stale" not in (c or {}).get("text", "") for c in got.values())  # the word is gone
    # a junk instant costs the chip its number, never the page, whatever the zone (review of TD-233 slice 1)
    year_one = {"windows": [{"label": "week", "pct": 5, "resets": "9999-12-31T23:59:59Z"}],
                "fetched": "0001-01-01T00:00:00Z", "reason": "ok"}  # fmt: skip
    assert usage_chip("grind", year_one, USAGE_NOW)["cls"] == "unknown"


def test_the_usage_chip_prints_the_line_its_reserve_makes_and_ranks_by_the_gap():
    """design §4.5a **usage** chip, §6 *Usage gate* (TD-100 slice 3, the chip's line): a window the
    profile has a reserve for prints its line after the number, *grind · week 61% / 70%*, with the
    reserve, the days left and when the line next moves on hover. *Worst* is the smallest gap to a
    line — the tool's 100% where there is none, so an unreserved 97% outranks a reserved 40% of a
    70% line — and *near* is within ten points of a line, 80% without one."""
    from agentorc.ui.app import usage_chip, with_lines

    got = {k: usage_chip("grind", u, USAGE_NOW) for k, u in USAGE_CASES.items()}
    lined = got["lined"]
    assert lined["text"] == "grind · week 61% / 70%" and lined["pct"] == 61
    assert lined["near"] is True and lined["cls"] == "near"  # 9 points under its line
    assert lined["title"] == (
        "week 61% / line 70% (reserve 10% a day, 3 days left; line moves 2026-09-24T07:00:00Z; resets r6)"
        "\n5h 40% (resets r5)"
    )
    assert got["unreserved_outranks"]["text"] == "grind · 5h 97%" and got["unreserved_outranks"]["cls"] == "near"
    assert "week 40% / line 70% (reserve 30%; line moves ?; resets ?)" in got["unreserved_outranks"]["title"]
    # 85% of a 100% line is 15 points off it: not near, where 85% with no line at all is
    assert got["flat_far"]["text"] == "grind · week 85% / 100%" and got["flat_far"]["near"] is False
    assert got["no_line_made"]["text"] == "grind · week 85%" and got["no_line_made"]["near"] is True
    # the page's join of the two reads: a profile the gate has no reserves for is left as it was
    usage = {"grind": {"windows": [], "reason": "ok"}, "paul": {"windows": []}, "gone": None}
    gate = {"profiles": {"grind": {"windows": [{"label": "week", "line": 70}]}}}
    assert with_lines(usage, gate) == {
        "grind": {"windows": [], "reason": "ok", "lines": [{"label": "week", "line": 70}]},
        "paul": {"windows": []},
        "gone": None,
    }
    assert with_lines(usage, None) == usage and with_lines(None, gate) == {}


def test_the_usage_chip_shows_the_gates_projection():
    """design §4.5a **usage** chip, §6 *A reading the gate can no longer trust* (TD-233): while the
    gate projects a window, the chip says so — *week 88% · 6h · projected 96% / 95%* — ranks and
    colours by the projection, and a reading past `USAGE_UNKNOWN` it projects is not *unknown*;
    a window with no rate to project by is the reading as ever."""
    from agentorc.ui.app import usage_chip

    got = {k: usage_chip("grind", USAGE_CASES[k], USAGE_NOW) for k in ("projected", "projected_under", "no_rate")}
    assert got["projected"]["text"] == "grind · week 88% · 6h · projected 96% / 95%"
    assert got["projected"]["pct"] == 96.0 and got["projected"]["cls"] == "near old" and got["projected"]["near"]
    assert "week 88%, projected 96% / line 95% (reserve 5%" in got["projected"]["title"]
    assert got["projected_under"]["text"] == "grind · week 60% · 2h · projected 62.5% / 95%"
    assert got["projected_under"]["cls"] == "old" and not got["projected_under"]["near"]
    assert got["no_rate"]["text"] == "grind · week 60% / 95% · 2h"


def test_the_usage_chip_is_one_per_account_and_names_the_tool_and_the_account():
    """design §4.5a **usage** chip, §4.2a (TD-122). Four profiles split by role on one login drew
    four chips named by profile — *grind · week 21% / 40% · stale +1* — where the person knows the
    quota as *Claude, paul*. The readings are grouped by account: one chip, `<tool> · <account>`,
    the lowest line among the profiles sharing it, and each profile with its line and sessions on
    hover. A reading from an agent that names no account stays its profile's own chip."""
    from agentorc.ui.app import usage_accounts, usage_chip

    reading = {"windows": [{"label": "week", "pct": 24, "resets": "r"}], "fetched": "f", "reason": "ok",
               "account": "paul", "tool": "Claude"}  # fmt: skip
    usage = {
        "grind": {**reading, "lines": [{"label": "week", "line": 70, "reserve": 30}]},
        "grind-sonnet": {**reading, "lines": [{"label": "week", "line": 60, "reserve": 40}]},
        "default": reading,
        "old": {"windows": [{"label": "5h", "pct": 3}], "fetched": "f"},  # an agent before TD-122
        "gone": None,
    }
    sessions = [
        {"id": "a", "name": "grinder-ao-1", "profile": "grind", "state": "working"},
        {"id": "b", "name": "grinder-ao-2", "profile": "grind", "state": "idle"},
        {"id": "c", "name": "done", "profile": "grind", "state": "exited"},
        {"id": "d", "name": "paul", "profile": "default", "state": "idle"},
    ]
    got = usage_accounts(usage, sessions)
    assert list(got) == ["Claude · paul", "old"]
    acc = got["Claude · paul"]
    assert acc["windows"] == reading["windows"] and acc["lines"] == [{"label": "week", "line": 60, "reserve": 40}]
    assert [(p["name"], p["sessions"]) for p in acc["profiles"]] == [
        ("grind", ["grinder-ao-1", "grinder-ao-2"]),
        ("grind-sonnet", []),
        ("default", ["paul"]),
    ]
    c = usage_chip("Claude · paul", acc)
    assert c["text"] == "Claude · paul · week 24% / 60%"
    assert c["title"].endswith(
        "\n\nprofiles on this account:\ngrind [week line 70% (reserve 30%; line moves ?)]: grinder-ao-1, grinder-ao-2"
        "\ngrind-sonnet [week line 60% (reserve 40%; line moves ?)]\ndefault: paul"
    )
    assert "grind" not in c["text"]  # never a profile's name in the chip
    # a per-day reserve on a fractional line: the days left are whole in the page as in `app.js`
    assert usage_chip("Claude · paul", USAGE_CASES["shared"])["title"].endswith(
        "grind [week line 70.5% (reserve 10% a day, 2 days left; line moves n1),"
        " 5h line 50% (reserve ?; line moves ?)]: grinder-ao-1, grinder-ao-2\ndefault"
    )
    assert usage_chip("old", got["old"])["text"] == "old · 5h 3%"
    assert usage_accounts(None) == {}


def test_the_usage_chip_rule_is_the_same_in_the_page_and_in_app_js(tmp_path):
    """The chip is drawn twice — server-side at page load, and by `app.js` on each pushed `usage`
    event — so the rule lives twice, and a rule kept in two places is held to one set of cases here
    or the two drift (the page would print an age until the first push, and then not). Both are
    handed one `now`, so the ages agree."""
    import json
    import shutil
    import subprocess

    from agentorc.ui.app import usage_chip

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript too, and nothing else runs it")
    probe = tmp_path / "usage_probe.js"
    probe.write_text(USAGE_PROBE)
    app_js = pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui" / "static" / "app.js"
    out = subprocess.run([node, str(probe), str(app_js), json.dumps(USAGE_CASES), USAGE_NOW.isoformat()],
                         capture_output=True, text=True, timeout=30)  # fmt: skip
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {k: usage_chip("grind", u, USAGE_NOW) for k, u in USAGE_CASES.items()}
    assert "AO.usageChip(ev.account, ev.usage)" in app_js.read_text()  # and the push really uses it


USAGE_PROBE = """
const fs = require("fs");
const noop = () => {};
const el = () => ({ dataset: {}, style: {}, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [], contains: () => false });
global.window = {};
global.document = { documentElement: el(), body: el(), activeElement: null,
  querySelector: () => null, querySelectorAll: () => [], addEventListener: noop, createElement: el };
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const cases = JSON.parse(process.argv[3]), out = {};
for (const k of Object.keys(cases)) out[k] = window.AO.usageChip("grind", cases[k], Date.parse(process.argv[4]));
console.log(JSON.stringify(out));
"""


def test_the_brief_changed_chip_names_the_files_and_is_never_pressable(tmp_path, monkeypatch):
    """design §4.5a **brief changed** (§6 rule 7, TD-217 slice 3): fixed words, the files by name and
    when on hover, drawn where *restart wanted* is; a mark, never pressable, and on Focus always in
    the page and hidden until true."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import card_slot, templates, view

    base = {"id": "ao-m1", "name": "m1", "kind": "agent", "adapter": "claude-code", "dir": str(tmp_path),
            "state": "idle", "since": "2026-09-21T01:00:00Z", "confidence": "hook", "pane": True,
            "tail": ["…"], "created": "2026-09-21T00:00:00Z"}  # fmt: skip
    card, focus = templates.get_template("card.html"), templates.get_template("focus.html")
    assert view(base)["brief_changed"] is None and "brief changed" not in card.render(s=view(base))
    hidden = focus.render(s={**view(base), "grants_all": [], "ready": []}, host="h", active="Org")
    assert 'class="badge bc hidden" id="fbc"' in hidden

    bc = {"at": "2026-09-26T20:02:00Z", "paths": ["/r/docs/briefs/manager-ao-1.md", "/v/briefs/manager.md"]}
    v = view({**base, "brief_changed": bc})
    assert v["brief_changed"]["text"] == "brief changed"
    assert "manager-ao-1.md, manager.md · changed 2026-09-2" in v["brief_changed"]["full"]
    pages = [("card", card.render(s=v))]
    pages.append(("focus", focus.render(s={**v, "grants_all": [], "ready": []}, host="h", active="Org")))
    for where, html in pages:
        assert "brief changed" in html and "manager-ao-1.md" in html, where
        if where == "focus":
            chip = html.split('id="fbc"')[1].split("</span>")[0]
            assert "data-act" not in chip and 'class="badge bc" id="fbc"' in html
    # not an ending: a working member's `doing` line keeps the slot, the mark takes it from the tail
    said = {"text": "TD-9: reading the fetcher", "at": "2026-09-21T01:00:00Z"}
    working = view({**base, "state": "working", "doing": said, "brief_changed": bc})
    assert "TD-9: reading the fetcher" in card.render(s=working)
    assert ">brief changed<" not in card.render(s=working).replace("\n", "")
    assert card_slot(view({**base, "state": "working", "brief_changed": bc}))["text"] == "brief changed"
    # a malformed field costs the chip, never the grid
    assert view({**base, "brief_changed": "yes"})["brief_changed"] is None
    js = (pathlib.Path(__file__).parents[1] / "src/agentorc/ui/static/app.js").read_text()
    assert '$("#fbc")' in js  # kept current from the pushed delta


def test_the_restart_wanted_chip_says_early_because_a_controller_does_not_act_on_those(tmp_path, monkeypatch):
    """design §4.5a **restart wanted** (§4.9a *A run that ends with work left*, TD-083): the third
    ending — *my run is over and my lane is not*. A **mark**, never pressable, and not a state: the
    session still reads `idle` or `exited`. Shaped like *out of work*, which is the chip it stands
    beside and the rule it follows.

    **The one thing the design did not have to say, and the field now does:** `early`. The home
    marks a restart asked for inside the record's own first half hour, and a **controller does not
    act on it** — a run that was over before it began did not run out of context. So an early one
    must not look like an ordinary one: a person reading the same chip would expect the same thing
    to happen next, and nothing will."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import templates, view

    base = {"id": "ao-w1", "name": "w1", "kind": "agent", "adapter": "claude-code", "dir": str(tmp_path),
            "state": "idle", "since": "2026-09-21T01:00:00Z", "confidence": "hook", "pane": True,
            "tail": ["…"], "created": "2026-09-21T00:00:00Z"}  # fmt: skip
    card, focus = templates.get_template("card.html"), templates.get_template("focus.html")

    assert view(base)["restart_wanted"] is None and "restart wanted" not in card.render(s=view(base))

    said = {"at": "2026-09-21T01:30:00Z", "why": "TD-090 is half done and my context is full"}
    v = view({**base, "restart_wanted": said})
    assert v["restart_wanted"]["why"].startswith("TD-090") and v["restart_wanted"]["early"] is False
    pages = [("card", card.render(s=v))]
    pages.append(("focus", focus.render(s={**v, "grants_all": [], "ready": []}, host="h", active="Org")))
    for where, html in pages:
        assert "restart wanted" in html, where
        assert "TD-090 is half done" in html, where  # the why is the hover: a card cannot hold it
        assert "· early" not in html, where
        if where == "focus":  # on a card it is the slot's text since TD-095, which carries no control
            assert "data-act" not in html.split("badge rw")[1].split("</span>")[0], where  # never pressable

    early = view({**base, "restart_wanted": {**said, "early": True}})
    assert early["restart_wanted"]["early"] is True
    pages = [("card", card.render(s=early))]
    pages.append(("focus", focus.render(s={**early, "grants_all": [], "ready": []}, host="h", active="Org")))
    for where, html in pages:
        assert "restart wanted · early" in html, where
        assert "a controller does not act on it" in html, where  # the hover says why it is different
        if where == "focus":
            assert 'class="badge rw early"' in html, where
        else:  # the slot says it wants a person, in words (design §4.5 *The card's anatomy*, TD-095)
            assert "restart wanted · early — for a person" in html
    css = (pathlib.Path(__file__).parents[1] / "src/agentorc/ui/static/app.css").read_text()
    assert ".badge.rw.early" in css  # …and it does not look the same

    # what made it early is the home's reading, on the mark (§4.9a, TD-249 slice 6): the hover ends
    # with its words, and a repeat names its entry in place of *early*
    words = "repeats TD-229: reported done by an earlier run too"
    again = view({**base, "restart_wanted": {**said, "early": True, "repeat": {"ref": "TD-229"}, "decided": words}})
    assert again["restart_wanted"]["repeat"] == "TD-229" and early["restart_wanted"]["repeat"] == ""
    pages = [("card", card.render(s=again))]
    pages.append(("focus", focus.render(s={**again, "grants_all": [], "ready": []}, host="h", active="Org")))
    for where, html in pages:
        assert "restart wanted · repeats TD-229" in html and "· early" not in html, where
        assert f"{words}, so a controller does not act on it" in html, where
    assert "restart wanted · repeats TD-229 — for a person" in pages[0][1]
    js = (pathlib.Path(__file__).parents[1] / "src/agentorc/ui/static/app.js").read_text()
    assert "repeats ${r.repeat}" in js and "r.decided" in js  # the delta draws the same words

    # on Focus both report-line marks are **always in the page, hidden until true**, and the header's
    # render keeps them current: Focus re-renders its header in place rather than being replaced
    # whole like a card, so a chip drawn only at load would go stale the moment a watched session
    # declared a restart or claimed again (review of PR #323) — the `#fsuspended` shape
    plain = focus.render(s={**view(base), "grants_all": [], "ready": []}, host="h", active="Org")
    assert 'id="frw"' in plain and 'id="foow"' in plain
    assert "badge rw hidden" in plain and "badge oow hidden" in plain  # present, not shown
    js = (pathlib.Path(__file__).parents[1] / "src/agentorc/ui/static/app.js").read_text()
    assert 'rw.classList.toggle("hidden", !r);' in js and 'rw.classList.toggle("early", early);' in js
    assert 'oow.classList.toggle("hidden", !o);' in js

    # a malformed record costs that card its chip and never the grid — the rule every chip here has
    for junk in ("nonsense", 7, [], {"why": "no at, so nothing was said"}):
        assert view({**base, "restart_wanted": junk})["restart_wanted"] is None, junk
    assert view({**base, "restart_wanted": {"at": "2026-09-21T01:30:00Z"}})["restart_wanted"]["why"] == ""


# ── the card's anatomy (design §4.5, TD-095): six rows, one text in the slot, the next act first ──


def _card(**kw):
    """A record as the host agent lists it, idle and clean unless told otherwise."""
    rec = {
        "id": "ao-w",
        "name": "w",
        "kind": "agent",
        "adapter": "claude-code",
        "dir": "/r/.claude/worktrees/w",
        "repo": "/r",
        "state": "idle",
        "since": "2026-09-21T01:00:00Z",
        "confidence": "hook",
        "pane": True,
        "tail": ["done.", "❯ "],
        "seen_at": "2026-09-21T02:00:00Z",
        "unattended": True,
        "git": {"branch": "w", "dirty": 1},
    }  # fmt: skip — dirty, so it does not read ready to close
    rec.update(kw)
    return rec


def test_the_slot_holds_one_text_the_first_that_applies_and_a_caption(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import view

    perm = {"kind": "permission", "text": "Bash: rm -rf build", "tool_use_id": "tu", "deadline": "2026-09-21T03:00:00Z"}
    doing = {"text": "TD-095: the rows", "at": "2026-09-21T01:30:00Z"}
    oow = {"at": "2026-09-21T01:40:00Z", "why": "the ledger is empty\nand the second line is on hover"}
    # (a) what needs a person comes before what the session says it is doing
    slot = view(_card(state="needs-you", pending=perm, doing=doing))["slot"]
    assert (slot["kind"], slot["text"]) == ("needs", "Bash: rm -rf build")
    assert (slot["caption"], slot["ccls"]) == ("via hook", "countdown")
    q = view(_card(state="needs-you", pending={"kind": "question", "text": "a or b?"}))["slot"]
    assert q["text"] == "question: a or b?" and q["caption"] == ""
    assert view(_card(state="limited", pending={"kind": "limit", "text": "resets 18:00"}))["slot"]["kind"] == "lim"
    # (b) an ending: exited, and a declaration — its first line, the rest on hover
    ex = view(_card(state="exited", exit_code=2, doing=doing))["slot"]
    assert ex["text"] == "exited · code 2" and ex["kind"] == "bad"
    # the crash restart's ceiling (§6 *Keeping a team running* rule 1, TD-103): an ending of its own
    ceil = view(_card(state="exited", exit_code=1, restart_ceiling={"at": "2026-09-22T20:00:00Z", "count": 3}))["slot"]
    assert ceil["text"] == "restarts exhausted · 3 in 2 h" and ceil["kind"] == "bad" and "Resume" in ceil["full"]
    # the idle nudge (§6 rule 4), still idle twenty minutes later: the tick's own reading (§6 rule 3
    # `idle_open`, TD-259) is the slot's, and a nudge alone — `nudged_at` — is not: the page derives nothing
    marked = view(_card(since="2026-09-21T01:00:00Z", idle_open={"at": "2026-09-21T01:40:00Z", "ref": "TD-070"}))
    assert marked["slot"]["text"] == "idle · open work" and marked["slot"]["kind"] == "lim"
    assert marked["open_work"] is True
    nudged = view(_card(since="2026-09-21T01:00:00Z", nudged_at="2026-09-21T01:20:00Z"))
    assert nudged["open_work"] is False and nudged["slot"]["text"] != "idle · open work"
    assert not view(_card(state="working", idle_open={"at": "2026-09-21T01:40:00Z", "ref": "TD-070"}))["open_work"]
    # a manager on call (§4.9, TD-259): the slot says what fills it, and it *came*, as the techlead does
    oncall = view(_card(state="closed", pane=False), seats={"ao-w": "comes when a member needs a reading"})["slot"]
    assert oncall["text"] == "on call — comes when a member needs a reading" and oncall["caption"].startswith(
        "last came"
    )
    assert "a member's permission, a stalled member or one idle with its work open fills the seat" in oncall["full"]
    said = view(_card(out_of_work=oow, doing=doing))["slot"]
    assert said["text"] == "out of work — the ledger is empty" and "second line" in said["full"]
    assert not said["caption"].startswith("says")  # an ending hides the line that caption would date
    # (c) the doing line, with *says* as its caption; (d) the last output line, or *at prompt*
    d = view(_card(doing=doing))["slot"]
    assert d["kind"] == "doing" and d["text"] == "TD-095: the rows" and d["caption"].startswith("says")
    assert view(_card())["slot"]["text"] == "last: ❯ "
    assert view(_card(tail=[]))["slot"]["text"] == "at prompt"
    w = view(_card(state="working", tail=["a", "b", "c"]))["slot"]
    assert w["kind"] == "tail" and w["text"] == "b\nc"  # two lines, as the slot is


def test_ready_to_close_is_the_caption_and_the_next_act_is_the_foots_first_button(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    clean = {"branch": "w", "dirty": 0, "unpushed": 0, "upstream": "origin/w"}
    ready = view(_card(git=clean, doing={"text": "done with TD-1", "at": "2026-09-21T01:30:00Z"}))
    assert ready["ready_ok"] and ready["slot"]["caption"] == "ready to close ✓"
    assert ready["slot"]["text"] == "done with TD-1"  # its last word stays in the slot
    assert ready["next_act"] == "close"
    # a team member that passes keeps the caption — a fact — but its foot leads with Focus: its
    # team closes it, and Close stays in more ▾ (§4.5 *Whose session it is*, TD-156)
    member = view(_card(git=clean, team="t", unattended=True))
    assert member["slot"]["caption"] == "ready to close ✓" and member["next_act"] == "focus" and not member["own"]
    assert view(_card(git=clean, team="t", unattended=False))["next_act"] == "close"  # taken over: the person's
    # exited reads ready to close too, and its first button is still Forget: nothing left to close
    ex = view(_card(state="exited", exit_code=0, git=clean))
    assert ex["slot"]["caption"] == "ready to close ✓" and ex["next_act"] == "forget"
    assert view(_card(state="closed", pane=False))["next_act"] == "forget"  # nothing to close there either (TD-266)
    assert view(_card(state="idle", pane=False))["next_act"] == "details"  # a gone pane on any other state
    assert view(_card(state="working"))["next_act"] == "focus"
    perm = {"kind": "permission", "text": "Bash: ls", "tool_use_id": "tu"}
    assert view(_card(state="needs-you", pending=perm))["next_act"] == "allow"
    html = templates.get_template("card.html").render(s=ready)
    foot = html.split('class="sc-foot"')[1]
    assert foot.index('data-act="close"') < foot.index("/focus/ao-w")  # the next act comes first
    assert 'data-act="close"' not in html.split('class="sc-foot"')[0]  # and nothing is left in the slot


def test_a_closed_card_leads_with_forget_its_menu_draws_what_applies_and_its_hover_says_when_it_goes(
    tmp_path, monkeypatch
):
    """§4.5 row 6 and §4.5a **Forget** / **Details** / **more ▾** (TD-266, built by TD-267)."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    gone = ('data-act="wrapup"', 'data-act="kill"', 'data-act="close"', 'data-act="mode"',
            'data-act="shell-here"', 'data-act="popout"', "data-copy=")  # fmt: skip

    def menu(html: str) -> str:
        return html.split('<div class="menu">')[1].split("</div>")[0]

    def acts(html: str) -> list[str]:
        return [x.split('"')[0] for x in menu(html).split('data-act="')[1:]]

    card = templates.get_template("card.html")
    closed = view(_card(state="closed", closed_at="2026-09-21T01:00:00Z", pane=False))
    assert closed["next_act"] == "forget"
    html = card.render(s=closed)
    foot = html.split('class="sc-foot"')[1].split('<details class="more">')[0]
    assert foot.index('data-act="remove"') < foot.index("/focus/ao-w") and "▣ Details" in foot
    assert acts(html) == ["message", "remove"] and not any(g in menu(html) for g in gone)
    assert acts(card.render(s={**closed, "restartable": True})) == ["message", "restart", "remove"]
    # the hover: the time, then the fixed words, the day read from the reap's own constant
    assert closed["slot"]["full"].startswith(
        "closed at 2026-09-21T01:00:00Z · forgotten by itself a day after the close — who closed it is not recorded"
    )
    assert closed["closed_keep"] == "forgotten by itself a day after the close"
    # a closed record with no `closed_at` is never reaped, so nothing says it will be
    bare = view(_card(state="closed", pane=False))
    assert bare["closed_keep"] == "" and "forgotten" not in bare["slot"]["full"]
    # an exited card with its pane keeps the whole menu; one whose pane is gone gets the short one
    kept = card.render(s=view(_card(state="exited", exit_code=0)))
    assert all(g in menu(kept) for g in gone) and 'data-act="remove"' not in menu(kept)
    assert acts(card.render(s=view(_card(state="exited", exit_code=0, pane=False)))) == ["message", "remove"]
    # the Details banner says the same beside its Forget, from the view's field
    js = (pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui" / "static" / "app.js").read_text()
    assert 'v.closed_keep ? ` <span class="meta">${esc(v.closed_keep)}</span>`' in js


def test_a_closed_card_says_who_closed_it_and_keeps_the_declaration_after_the_ending(tmp_path, monkeypatch):
    """§4.5 row 5 (b) and §4.5a **doing** (TD-262, built by TD-265): the record's `closer` in the
    card's words — a person's, a session's by its name, the session's own, the tick's four — bare
    *closed* with none, and a closed or exited record that declared keeps the declaration after."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import view

    at = "2026-10-01T10:00:00Z"
    mgr = _card(id="ao-r-manager-dc-1", name="manager-dc-1", state="closed", pane=False)

    def text(closer, **kw):
        closer = {**closer, "at": at} if isinstance(closer, dict) else closer
        rec = _card(state="closed", pane=False, closed_at=at, closer=closer, **kw)
        return view(rec, [rec, mgr])["slot"]["text"]

    assert text({"by": "person", "why": None}) == "closed by you"
    assert text({"by": "ao-r-manager-dc-1", "why": None}) == "closed by manager-dc-1"
    assert text({"by": "ao-gone-1", "why": None}) == "closed by ao-gone-1"  # not in the fleet: its id
    assert text({"by": "ao-w", "why": None}) == "closed itself"
    for why, words in (("finished", "team finished"), ("wanted", "for a restart"), ("brief", "brief changed"),
                       ("seat", "seat done")):  # fmt: skip
        assert text({"by": "tick", "why": why}) == f"closed by the tick · {words}"
    assert text(None) == "closed"
    bare = view(_card(state="closed", pane=False, closed_at=at))["slot"]["full"]
    assert "a person's Close made at the node itself" in bare  # a missing closer says why, not a bug
    assert text({"by": "tick", "why": "nonsense"}) == "closed by the tick"
    assert text("not a dict") == "closed"  # a malformed field costs the words, never the card
    # the hover: the words, the time and the day the record goes
    full = view(_card(state="closed", pane=False, closed_at=at, closer={"by": "person", "at": at}))["slot"]["full"]
    assert full == f"closed by you at {at} · forgotten by itself a day after the close"
    # the declaration after the ending, its reason's first line; the hover holds both in full
    oow = {"at": at, "why": "nothing pickable on dev-cadence's ledger\nTD-1 is design-first"}
    slot = view(_card(state="closed", pane=False, closed_at=at, closer={"by": "ao-r-manager-dc-1", "at": at},
                      out_of_work=oow), [mgr])["slot"]  # fmt: skip
    assert slot["text"] == "closed by manager-dc-1 — out of work — nothing pickable on dev-cadence's ledger"
    assert slot["full"].startswith(f"closed by manager-dc-1 at {at} · ")
    assert slot["full"].endswith("TD-1 is design-first")
    ex = view(_card(state="exited", exit_code=0, restart_wanted={"at": at, "why": "context bound"}))["slot"]
    assert ex["text"] == "exited · code 0 — restart wanted — context bound"
    assert view(_card(state="exited", exit_code=0))["slot"]["text"] == "exited · code 0"


def test_ready_to_close_on_focus_says_close_session_as_the_header_does(tmp_path, monkeypatch):
    """TD-272: the *Ready to close* card's button read **Close**, as if it closed the card."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    s = view(_card(state="idle", created="2026-09-21T00:00:00Z"))
    html = templates.get_template("focus.html").render(s={**s, "grants_all": [], "ready": []}, host="h", active="Org")
    btn = html.split('id="closebtn"')[1].split("</button>")[0]
    assert btn.endswith(">Close session") and 'title="Kills the session' in btn


def test_row_three_says_where_once_and_the_group_hides_what_it_already_says(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import _middle, templates, view

    # the worktree named for the session is not repeated; one named otherwise is
    v = view(_card(git={"branch": "td095-rows"}))
    assert (v["wt_prefix"], v["branch_full"]) == ("", "branch td095-rows")
    assert view(_card(dir="/r/.claude/worktrees/other"))["wt_prefix"] == "wt/other · "
    assert view(_card(git={"branch": "(detached)", "oid": "89de0bd1234567"}))["branch_full"] == "detached at 89de0bd"
    assert view(_card(git={"branch": "(detached)"}))["branch_full"] == "detached HEAD"  # an older agent's record
    shell = view(_card(repo=None, dir="/etc/wireguard", adapter="shell", git={}))
    assert shell["branch_full"] == "/etc/wireguard" and shell["place_prefix"].endswith(" / ")
    # a long branch keeps both ends, and the whole of it on hover
    assert _middle("td095-a-really-long-branch-name-rows", 20) == "td095-a-re…name-rows"
    # the title is drawn only when it differs from the name
    assert view(_card(title="w"))["title_shown"] == "" and view(_card(title="Fix it"))["title_shown"] == "Fix it"
    # *under <manager>* is marked for the group to hide when that manager is the only controller
    mgr = _card(id="ao-m", name="m", team="t", capabilities=["control"])
    member = _card(team="t", controllers=["ao-m"])
    assert view(member, [mgr, member])["under_is_manager"] is True
    assert view({**member, "controllers": ["ao-m", "ao-x"]}, [mgr, member])["under_is_manager"] is False
    html = templates.get_template("card.html").render(s=view(member, [mgr, member]))
    assert 'class="meta under ingroup"' in html and 'class="badge team ingroup"' in html
    css = (pathlib.Path(__file__).parents[1] / "src/agentorc/ui/static/app.css").read_text()
    assert '.tgroup:not([data-team=""]):not(.filtering) .sc .ingroup { display: none; }' in css


def test_the_mode_is_a_word_on_the_card_and_its_toggle_is_in_more(tmp_path, monkeypatch):
    """Design §4.5a **unattended / interactive** (TD-095): a mark, never pressable, on the card;
    *interactive* — the person's own — carries the `person` mark; the toggle is an entry of *more*."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view
    from agentorc.ui.icons import ICON_PATHS

    card = templates.get_template("card.html")
    worker, mine = card.render(s=view(_card())), card.render(s=view(_card(unattended=False)))
    head = worker.split('class="sc-foot"')[0]
    assert 'data-act="mode"' not in head and 'class="meta mode"' in head
    assert '<button data-act="mode" data-id="ao-w" class="on">Switch to interactive</button>' in worker
    assert ICON_PATHS["person"] in mine.split('class="meta mode mine"')[1].split("</span>")[0]
    assert ">Switch to unattended</button>" in mine


def test_the_foot_is_quiet_and_only_allow_is_filled(tmp_path, monkeypatch):
    """Design §4.5 *The card's anatomy*, row 6 (TD-095, second pass): the next act is outlined, the
    rest are plain links, and the one filled button a card carries is Allow on a *needs you* card.
    *more ▾ → Close* is enabled only when Ready to close passes (§4.5) — dimmed means disabled, and
    its hover says what it waits on."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    card = templates.get_template("card.html")

    def foot(rec):
        return card.render(s=view(rec)).split('class="sc-foot"')[1]

    working = foot(_card(state="working"))
    assert "primary" not in working and 'class="btn sm next" href="/focus/ao-w"' in working
    assert 'class="btn sm link"' in working  # the editor button and more are plain links
    perm = {"kind": "permission", "text": "Bash: ls", "tool_use_id": "tu"}
    asking = foot(_card(state="needs-you", pending=perm))
    assert asking.count("primary") == 1 and 'class="btn sm primary" data-act="allow"' in asking
    assert 'class="btn sm link" href="/focus/ao-w"' in asking  # Focus is not the next act here
    # the default card is dirty: Close in more is disabled, and says why
    assert 'data-confirm="Close w?" disabled title="not ready to close — tree clean' in working
    clean = {"branch": "w", "dirty": 0, "unpushed": 0}
    closes = 'title="Kills the session and reaps its worktree; the record reads closed.">Close</button>'  # TD-167
    assert f'data-confirm="Close w?" {closes}' in foot(_card(git=clean))


def test_the_header_says_where_once_and_counts_by_state_and_no_team_says_its_count():
    """Design §4.5 *The card's anatomy* (TD-095): the header carries the host / repo its sessions
    share — *mixed* where they do not — and the counts by state in the grid's order, *needs you*
    being its ringed mark instead; *No team* is headed by its count and nothing else."""
    from agentorc.ui.app import group_place, state_counts, team_groups, templates

    def v(sid, state, place, unseen=False, **kw):
        return {**sess(sid, sid, state=state, **kw), "place": place, "unseen": unseen}

    same = [v("a", "working", "kmaster / agentorc", team="t"), v("b", "idle", "kmaster / agentorc", team="t")]
    assert group_place(same) == "kmaster / agentorc" and group_place([]) == ""
    assert group_place([*same, v("c", "idle", "vps / agentorc", team="t")]) == "mixed"
    many = [*same, v("c", "needs-you", "x"), v("d", "idle", "x", unseen=True), v("e", "limited", "x")]
    assert state_counts(many) == ["1 limited", "1 working", "1 unseen", "1 idle"]  # urgency order
    groups = team_groups([*same, v("n1", "idle", "kmaster / wg"), v("n2", "exited", "kmaster / wg")])
    team, none = groups
    head = templates.get_template("group_head.html").render(g=team)
    # a live team's header carries no state chips since TD-176: its compact cards say it — but
    # folded it does, so they are drawn for the fold alone and CSS shows them only then (TD-194)
    assert "kmaster / agentorc" in head and "▾ 2 sessions" in head and " live<" not in head
    assert head.count("1 working") == 1 and 'class="meta counts foldonly">· 1 working · 1 idle<' in head
    nohead = templates.get_template("group_head.html").render(g=none)
    assert ">No team</span>" in nohead and ">2 sessions</span>" in nohead and "kmaster / wg" not in nohead


def test_within_one_urgency_the_persons_own_sort_first_and_the_card_says_it_is_mine(tmp_path, monkeypatch):
    """Design §4.5 *One order, no control*, second pass (TD-095): the key is (rank, interactive
    first, name) — a worker that needs a person still outranks the person's own idle session, and a
    manager's card is placed first before any of this. The filter's word `mine` (§4.5a, TD-428)
    shows only the interactive sessions; the card says which it is, the page's script does the rest."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import card_order, team_groups, templates, view

    a = _card(id="ao-a", name="a")  # unattended, idle
    z = _card(id="ao-z", name="z", unattended=False)  # interactive, idle
    needs = _card(id="ao-n", name="n", state="needs-you", pending={"kind": "question", "text": "?"})
    vs = [view(r) for r in (a, z, needs)]
    assert [v["name"] for v in sorted(vs, key=card_order)] == ["n", "z", "a"]
    mgr = _card(id="ao-m", name="zz-manager", team="t", capabilities=["control"], state="working")
    team = [mgr, {**a, "team": "t", "controllers": ["ao-m"]}, {**z, "team": "t", "controllers": ["ao-m"]}]
    (g,) = team_groups([view(r, team) for r in team])
    assert g["ids"] == ["ao-m", "ao-z", "ao-a"]  # the manager first, then the person's own
    card = templates.get_template("card.html")
    assert 'data-mine="1"' in card.render(s=vs[1]) and "data-mine" not in card.render(s=vs[0])
    js = (pathlib.Path(__file__).parents[1] / "src/agentorc/ui/static/app.js").read_text()
    assert "(!!b.dataset.mine - !!a.dataset.mine)" in js  # the page's re-sort keeps the same key
    html = templates.get_template("org.html").render(
        sessions=vs, groups=None, counts=dict.fromkeys(("needs-you", "limited", "stalled?"), 0),
        strip={"teams": [{"name": "t"}], "source": "", "notes": []}, host="h", active="Org",
        agent_down=False, volatile=False, usage={},
    )  # fmt: skip
    assert 'id="mine"' not in html and 'id="showcmd"' not in html  # the words replaced them (TD-428)
    assert 'placeholder="filter… team: state: mine kind:command"' in html


def test_a_seats_last_came_is_its_fill_and_a_compact_flag_keeps_its_count(tmp_path, monkeypatch):
    """TD-200: (1) a seat that just left reads *last came* from its record's `created` (the fill),
    never from `since` (when it left: *last came · 0s ago*); (2) the compact card's flag is the
    count alone, never clipped — *47 unpushed* once read *⚠ 4* — with the full words on hover."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from datetime import UTC, datetime, timedelta

    from agentorc.ui.app import templates, view

    now = datetime.now(UTC)
    iso = lambda t: t.isoformat().replace("+00:00", "Z")  # noqa: E731
    left = _card(state="exited", pane=False, created=iso(now - timedelta(hours=2)), since=iso(now))
    v = view(left, seats={"ao-w": "comes on the next question"})
    assert v["slot"]["caption"] == "last came · 2h 0m ago"
    git = {"branch": "w", "dirty": 1, "unpushed": 47, "upstream": "origin/w"}
    c = view(_card(git=git))
    assert c["flag"] == "dirty · 47 unpushed" and c["flag_short"] == "dirty · 47"
    html = templates.get_template("card.html").render(s={**c, "compact": True, "compact_line": "Manager"})
    assert '<span class="flag" title="dirty · 47 unpushed">⚠ dirty · 47</span>' in html
    css = (pathlib.Path(__file__).parents[1] / "src/agentorc/ui/static/app.css").read_text()
    assert ".sc .r2 .flag { flex-shrink: 0; }" in css  # the line of its own gives way, never the count


def test_a_seat_with_nobody_in_it_reads_on_call_and_its_first_button_is_message(tmp_path, monkeypatch):
    """Design §4.5 *The card's anatomy*, TD-097: an `exited` or `closed` record the team definition
    names as a seat is drawn *◇ on call* — composed, as *idle · unseen* is, so the state stays what
    it is — the slot says what would make it come, the caption when it last came, and the foot's
    first button is Message…, never Forget or Close session. A seat that is filled is an ordinary
    card, and a record the definition does not name is the `exited` it always was."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import state_counts, templates, view

    clean = {"branch": "w", "dirty": 0, "unpushed": 0, "upstream": "origin/w"}
    for state in ("exited", "closed"):
        v = view(_card(state=state, exit_code=0, git=clean, pane=False), seats={"ao-w": "comes on the next question"})
        assert v["state"] == state and (v["state_class"], v["state_label"]) == ("oncall", "on call")
        assert v["slot"]["text"] == "on call — comes on the next question"
        assert v["slot"]["caption"].startswith("last came")  # never *ready to close ✓*: it is not closed
        assert v["next_act"] == "message"
        html = templates.get_template("card.html").render(s=v)
        foot = html.split('class="sc-foot"')[1]
        assert foot.index('data-act="message"') < foot.index("/focus/ao-w")  # Message… first, then Details
        assert 'data-act="remove"' not in html and "Close session" not in html
        assert 'class="pill s-oncall' in html and ">on call</span>" in html
        # read from the seat's record, so never the dashed *guessed from the screen* pill (TD-296 #10)
        rec = _card(state=state, exit_code=0, git=clean, pane=False, confidence="scraped")
        v_scraped = view(rec, seats={"ao-w": "x"})
        assert v_scraped["scraped"] is False
        oncall = templates.get_template("card.html").render(s=v_scraped)
        assert "s-oncall scraped" not in oncall and "guessed from the screen" not in oncall
    # filled: an ordinary card in its live state
    live = view(_card(state="working"), seats={"ao-w": "comes on the next question"})
    assert live["state_label"] == "working" and not live["seat"] and live["next_act"] == "focus"
    # not a seat: exited as ever, Forget first
    other = view(_card(state="exited", exit_code=0))
    assert other["state_label"] == "exited" and other["next_act"] == "forget"
    # the team's header counts them apart
    assert state_counts([v, other, live]) == ["1 working", "1 on call", "1 exited"]
    # a seat with a trigger says its own (TD-098): what makes it come, and that it *ran*
    audit = view(_card(state="exited", exit_code=0, git=clean, pane=False), seats={"ao-w": "runs after 10 PRs"})
    assert audit["slot"]["text"] == "on call — runs after 10 PRs"
    assert audit["slot"]["caption"].startswith("last ran") and audit["next_act"] == "message"
    # the tick's count toward it (§6 rule 3, TD-103 slice 3), drawn until the seat is due
    prs = {"trigger": "prs", "after": "10"}
    counted = {"seat": prs, "seat_count": {"prs": 4, "at": "2026-09-22T20:00:00Z"}}
    on = view(_card(state="exited", pane=False, **counted), seats={"ao-w": "runs after 10 PRs"})
    assert on["slot"]["text"] == "on call — runs after 10 PRs · 4 of 10"
    due = view(
        _card(state="exited", pane=False, seat_due={"at": "x", "by": "prs"}, **counted),
        seats={"ao-w": "runs after 10 PRs"},
    )
    assert due["slot"]["text"] == "on call — runs after 10 PRs"
    # the fill ceiling: an ending, as the crash ceiling's is
    full = view(
        _card(state="exited", pane=False, seat=prs, restart_ceiling={"at": "x", "count": 6, "why": "fill"}),
        seats={"ao-w": "runs after 10 PRs"},
    )["slot"]
    assert full["text"] == "fills exhausted · 6 in 1 h" and full["kind"] == "bad"


def test_unseen_is_drawn_only_on_an_interactive_session(tmp_path, monkeypatch):
    """Design §4.2 *Unseen idle*, TD-095 (f), Paul 2026-09-21: an unattended session that finished
    while nobody looked is plain `idle` — its manager read the result and its slot says how it
    ended; the person's own interactive session is *idle · unseen* and sorts above idle."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import view

    finished = {"since": "2026-09-21T03:00:00Z", "seen_at": "2026-09-21T02:00:00Z"}
    worker = view(_card(**finished))  # `_card` is unattended
    assert not worker["unseen"] and worker["state_label"] == "idle"
    mine = view(_card(**finished, unattended=False))
    assert mine["unseen"] and mine["state_label"] == "idle · unseen" and mine["rank"] < worker["rank"]


def test_the_usage_gates_pause_is_a_mark_in_the_slot_and_the_focus_header(tmp_path, monkeypatch):
    """design §4.5a **paused · usage** (§6 *Usage gate*, TD-100 slice 3): the record's `gated`,
    composed by the page from the mark's own fields — never from what the session said. It is (a)
    in the slot, *what explains a stop*, and waits behind a permission, a question, a limit or a
    stall; the Focus header shows it regardless. A mark, not a state: the pill stays `idle`."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import gated_view, templates, view

    mark = {"profile": "grind", "label": "week", "pct": 75, "line": 70, "since": "2026-09-21T01:00:00Z",
            "next": "2026-09-21T14:00:00Z", "sent_at": None}  # fmt: skip
    g = gated_view(mark)
    assert g["text"].startswith("paused · usage — grind week 75% ≥ 70%, line moves ")
    assert "pause sent" not in g["text"] and "waits for a clear composer" in g["full"] and "ao gate" in g["full"]
    assert gated_view({**mark, "sent_at": "2026-09-21T01:00:05Z"})["text"].endswith(" · pause sent")
    # a line that moves only at the window's reset (a flat reserve, or the last day) says *resets*
    at_reset = gated_view({**mark, "resets": mark["next"]})["text"]
    assert ", resets " in at_reset and "line moves" not in at_reset
    assert "line moves" in gated_view({**mark, "resets": "2026-09-30T07:00:00Z"})["text"]
    assert gated_view({**mark, "profile": "", "next": None})["text"] == "paused · usage — default week 75% ≥ 70%"
    # under a team's reserve priority the line is the team's, and the words say whose (§6, TD-146)
    team = gated_view({**mark, "line": 60, "team_extra": {"team": "ao-grind", "n": 10}})["text"]
    assert team.startswith("paused · usage (ao-grind +10) — grind week 75% ≥ 60%")
    assert "teams.ao-grind.reserve" in gated_view({**mark, "team_extra": {"team": "ao-grind", "n": 10}})["full"]
    assert gated_view({**mark, "team_extra": {"team": "t", "n": True}})["text"].startswith("paused · usage — ")
    # one malformed record costs its card the mark, never the grid
    assert all(
        gated_view(j) is None
        for j in (None, "x", [1], {"pct": "75", "line": 70}, {"pct": 75}, {"pct": True, "line": 0})
    )

    v = view(_card(gated=mark, doing={"text": "TD-1: the rows", "at": "2026-09-21T01:30:00Z"}))
    assert v["state"] == "idle" and v["slot"]["kind"] == "lim" and v["slot"]["text"] == g["text"]
    assert v["slot"]["full"] == g["full"]
    # a person's answer comes first: the mark waits behind the question in the slot…
    q = view(_card(state="needs-you", pending={"kind": "question", "text": "a or b?"}, gated=mark))
    assert q["slot"]["text"] == "question: a or b?"
    # …and the Focus header shows it regardless, hidden (not absent) when there is none
    focus = templates.get_template("focus.html")
    html = focus.render(
        s={**q, "grants_all": [], "ready": [], "created": "2026-09-21T00:00:00Z"}, host="h", active="Org"
    )
    assert 'id="fgated"' in html and g["text"] in html
    plain = view(_card())
    assert plain["gated"] is None
    html = focus.render(
        s={**plain, "grants_all": [], "ready": [], "created": "2026-09-21T00:00:00Z"}, host="h", active="Org"
    )
    assert 'class="badge gated hidden" id="fgated"' in html


def test_members_is_on_an_org_defined_team_and_a_note_on_a_repo_defined_one():
    """§4.5a *team card: Members…* (TD-172): on the *i* panel's Definition line (TD-418) of a team
    `org.yml` defines; on a team a repo defines, drawn disabled with its reason and **Open file**
    (TD-229); never on *No team*. The exited banner's *one member back* line and the team skill's say the same thing."""
    from agentorc.ui.app import templates

    head = templates.get_template("group_head.html")
    base = {"team": "ao-grind", "label": "ao-grind", "defined": True, "stopped": True, "members": [], "live": 0}
    assert 'data-members="ao-grind"' in head.render(g={**base, "in_org": True})
    repo = head.render(g={**base, "in_org": False, "source": "/r/.agentorc.yml", "source_repo": "r"})
    # a repo's team (§4.5a *Members… on a repo-defined team*, TD-229 slice 4): disabled, its reason
    # beside it, and **Open file** on its `.agentorc.yml` through the person's `open_in`
    assert "data-members" not in repo and "defined in r&#39;s .agentorc.yml — changed by PR</span>" in repo
    assert '<button class="btn sm ghost" disabled title="defined in r&#39;s .agentorc.yml' in repo
    assert ">Open file</a>" in repo and "/r/.agentorc.yml" in repo
    panel = repo[repo.index('class="note secinfo helppanel"') :]
    assert panel.index('<div class="defline"><b>Definition</b>') < panel.index("Open file")
    assert "Flow on Settings →" in panel and repo.index("helppanel") < repo.index("changed by PR")
    assert "data-members" not in head.render(g={"team": "", "label": "No team", "members": []})
    ui_dir = pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui"
    js = (ui_dir / "static" / "app.js").read_text()
    # TD-250 slice 3: Restart is the way back into the team's run; Resume with changes… is for a
    # record with no launch record
    assert "To put it back in ${esc(v.team)}'s run: <b>Restart</b> in its card's <b>more ▾</b>" in js
    assert "With no launch record: <b>Resume with changes…</b> and tick <b>Unattended</b>" in js
    skill = (ui_dir.parent / "team_skill.md").read_text()
    assert "**One member back**" in skill and "**Members…** on the team card" in skill
    back = skill[skill.index("**One member back**") :]
    assert "`ao restart <id>`" in back and back.index("**Restart**") < back.index("**Resume with changes…**")


def test_a_metered_accounts_chip_reads_spend_over_its_amount():
    """design §4.5a **usage** (TD-151 slice 5): *day $4.10 / $5*, the worst window the one nearest its
    amount, amber from eight tenths and red at it, tokens by kind on hover, *spend unknown* when the
    adapter could not read, and a window with no amount printing its spend alone."""
    from agentorc.ui.app import usage_accounts, usage_chip

    got = usage_chip("Claude · key", USAGE_CASES["metered"])
    assert got["text"] == "Claude · key · month 12.3M tok / 10M tok" and got["cls"] == "cap" and got["pct"] == 100
    assert got["title"].startswith("day $4.10 / $5 (82%) — 4.1M in, 2k out, 0 cache read, 0 cache write (resets d1)")
    assert "\nweek $1,234.50 — 12.3M in" in got["title"]
    u = usage_chip("Claude · key", USAGE_CASES["metered_unpriced"])
    assert u["text"] == "Claude · key · day 900 tok · spend unknown" and u["cls"] == "" and u["pct"] == 0
    # two profiles on one key: one sum, the chip over the smaller amount (the higher pct)
    day = USAGE_CASES["metered"]["windows"][0]
    a = {
        "tool": "Claude",
        "account": "key",
        "reason": "ok",
        "windows": [day | {"pct": 41, "amount": {"value": 10.0, "unit": "$"}}],
    }
    b = {"tool": "Claude", "account": "key", "reason": "ok", "windows": [day]}
    acc = usage_accounts({"api2": a, "api": b})["Claude · key"]
    chip = usage_chip("Claude · key", acc)
    assert chip["text"] == "Claude · key · day $4.10 / $5"
    # the hover names each profile's own amount (TD-151): the chip alone hid which profile had which
    assert chip["title"].endswith("\n\nprofiles on this account:\napi2 [day amount $10]\napi [day amount $5]")
    # what is not a number is not an amount, as in `amountSays` (the review of #900)
    odd = [{"label": "day", "amount": {"value": v, "unit": "tok"}} for v in (float("inf"), float("nan"))]
    assert usage_chip("k", USAGE_CASES["metered"] | {"profiles": [{"name": "api", "amounts": odd}]})["title"].endswith(
        "profiles on this account:\napi"
    )
    bare = usage_accounts({"p": {"reason": "ok", "windows": [day | {"amount": None}]}})
    assert "amounts" not in bare["p"]["profiles"][0]
    shared = usage_chip("Claude · key", USAGE_CASES["metered_shared"])["title"]
    assert shared.endswith(":\napi [day amount $5, week amount 2M tok]: w1\napi2 [day amount $10]")
    # the turns and the pace (TD-151): *day $3.20 / $5 · 412 turns · $0.40/h · at this pace $5 by 12:30*,
    # the clock time the home's local one, its weekday in front when it is not today; a junk field draws nothing
    paced = usage_chip("Claude · key", USAGE_CASES["metered_paced"], USAGE_NOW)["title"].split("\n")
    by = datetime(2026, 9, 20, 22, 30, tzinfo=UTC).astimezone()
    today = by.date() == USAGE_NOW.astimezone().date()
    clock = by.strftime("%H:%M" if today else "%a %H:%M")
    assert paced[0].startswith(f"day $3.20 / $5 (64%) · 412 turns · $0.40/h · at this pace $5 by {clock} — 3M in")
    wed = datetime(2026, 9, 23, 10, tzinfo=UTC).astimezone().strftime("%a %H:%M")
    assert paced[1].startswith(f"week 3M tok / 10M tok (30%) · 1 turn · 2k tok/h · at this pace 10M tok by {wed} — ")
    assert paced[2].startswith("month 0 tok — ")
    assert paced[3].startswith("5h 0 tok — "), "a pace in no known unit draws nothing (TD-375)"


def test_a_card_says_what_it_waits_on_after_its_declaration_or_alone(tmp_path, monkeypatch):
    """§4.5a **waiting** mark (TD-271, built by TD-274): an open `ask` or `steer` from the session in
    the person inbox whose `about` names a reference puts *waiting on you: <ref> until <time>* in the
    slot's ending — after the declaration's words, before its reason; alone on one that declared
    nothing; after the ending of a closed one — the sooner bound first, *and n more*, an `ask` with no
    time, the question's first paragraph on hover. Prose holds nothing, and a closed entry leaves."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import view, waits_of

    bound = datetime.now(UTC).replace(hour=9, minute=57, second=0, microsecond=0)
    clock = bound.astimezone().strftime("%H:%M")
    if bound.astimezone().date() != datetime.now().astimezone().date():
        clock = bound.astimezone().strftime("%a ") + clock

    def entry(id, kind="steer", about="TD-222", **kw):
        return {
            "id": id,
            "from": "ao-w",
            "to": ["person"],
            "kind": kind,
            "about": about,
            "text": f"{id}?\n\nmore",
            **kw,
        }

    steer = entry("m-s", bound=bound.isoformat())
    waits = waits_of([steer, entry("m-p", about="the colour of the button")])
    oow = {"at": "2026-10-02T09:00:00Z", "why": "nothing pickable\nTD-1 is design-first"}
    v = view(_card(state="idle", out_of_work=oow), waits=waits)
    assert v["slot"]["text"] == f"out of work · waiting on you: TD-222 until {clock} — nothing pickable"
    assert v["slot"]["full"].endswith(f"waiting on you: TD-222 until {clock} — m-s?")  # its first paragraph
    alone = view(_card(state="working"), waits=waits_of([entry("m-a", kind="ask"), steer]))["slot"]
    assert alone["text"] == f"waiting on you: TD-222 until {clock} and 1 more"
    closed = view(_card(state="closed", pane=False, closer={"by": "person"}), waits=waits)["slot"]["text"]
    assert closed == f"closed by you · waiting on you: TD-222 until {clock}"
    assert view(_card(state="idle"), waits=waits_of([entry("m-p", about="prose")]))["waiting"] is None
    assert view(_card(state="idle"), waits=waits_of([entry("m-c", closed_reason="answered")]))["waiting"] is None
    assert view(_card(state="idle"), waits=waits_of([entry("m-k", kind="ask")]))["slot"]["text"] == (
        "waiting on you: TD-222"
    )  # an ask has no bound
    assert view(_card(state="idle"))["waiting"] is None  # no inbox read: nothing said
    # the Focus header's chip is drawn from the same view: its words and its hover
    v = view(_card(state="idle"), waits=waits)
    assert v["waiting"] == {
        "text": f"waiting on you: TD-222 until {clock}",
        "full": f"waiting on you: TD-222 until {clock} — m-s?",
    }


def test_the_usage_chip_prints_a_whole_float_as_the_script_does():
    """TD-296 #1: the endpoint's `52.0` drew *week 52.0%* on the pages the server renders (Settings,
    Inbox, Help, New session) and *week 52%* where `AO.usageChip` redraws it (JS prints a whole
    float bare). The server now prints `:g`, as the script does; a fraction keeps its decimal."""
    from agentorc.ui.app import usage_chip

    u = {"windows": [{"label": "week", "pct": 52.0, "resets": "r"}, {"label": "5h", "pct": 7.5, "resets": "r"}],
         "fetched": "2026-09-20T20:00:00Z", "reason": "ok"}  # fmt: skip
    c = usage_chip("grind", u, USAGE_NOW)
    assert c["text"] == "grind · week 52%"
    assert "week 52% (resets r)" in c["title"] and "5h 7.5% (resets r)" in c["title"]


def test_a_defined_team_ends_its_grid_in_the_plus_card_and_no_team_has_none(monkeypatch, tmp_path):
    """§4.5a *team card: + card* (TD-377, built by TD-379): the last card in every defined team's grid,
    live or not, whose press is the New session form on that team; none on *No team*, and none on a
    badge no definition carries (there is no team for the form to pick)."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import view

    records = [
        {"id": "ao-g1", "name": "grinder-1", "state": "working", "dir": "/tmp/r", "kind": "agent", "team": "live"},
        {"id": "ao-x1", "name": "stray-1", "state": "idle", "dir": "/tmp/r", "kind": "agent", "team": "badge-only"},
        {"id": "ao-sh", "name": "sh1", "state": "idle", "dir": "/tmp/x", "kind": "agent", "adapter": "shell"},
    ]
    rows = [
        {"name": "live", "manager": "orc", "members": 1, "projects": ["p"], "wound_down": None},
        {"name": "never-run", "manager": "orc", "members": 2, "projects": ["p"], "wound_down": None},
    ]
    vs = [view(r, records) for r in records]
    groups = team_groups(vs, rows)
    html = templates.get_template("org.html").render(
        sessions=vs,
        groups=groups,
        counts=dict.fromkeys(("needs-you", "limited", "stalled?"), 0),
        strip={"teams": [], "source": "", "notes": []},
        host="kmaster",
        active="Org",
        agent_down=False,
        volatile=False,
        usage={},
    )
    sections = {s.split('"', 1)[0]: s for s in html.split('<section class="tgroup" data-team="')[1:]}
    assert set(sections) == {"live", "never-run", "badge-only", ""}
    for team in ("live", "never-run"):
        sec = sections[team]
        assert sec.count('class="card sc plus"') == 1, team
        assert f'href="/new?team={team}"' in sec and f"your session in {team}</span>" in sec
        # the last card in the grid, after the members; not a session: no id the delta client swaps
        grid = sec[sec.index('<div class="grid">') :]
        assert grid.rindex('class="card sc') == grid.index('class="card sc plus"')
        plus = grid[grid.index('class="card sc plus"') :]
        assert 'id="card-' not in plus and "data-id=" not in plus and 'tabindex="0"' in plus
        # no session's Focus link: `markPopped` relabels every `a[data-focus]` (review of #1234)
        assert "data-focus" not in plus[: plus.index("</div>")]
    assert sections["live"].index('id="card-ao-g1"') < sections["live"].index('class="card sc plus"')
    assert "plus" not in sections[""] and "plus" not in sections["badge-only"]
    # its hover is the help entry's first sentence, and the help entry says no definition changes
    from agentorc.ui.help import BY_KEY, first_sentence

    assert f'title="{first_sentence("plus")}"'.replace("'", "&#39;") in sections["live"]
    assert "Nothing is written to org.yml" in BY_KEY["plus"].text


def test_a_delta_carries_the_plus_card_for_a_defined_team_and_none_for_the_rest(monkeypatch, tmp_path):
    """§4.5a *team card: + card* (TD-379; pinned by TD-389): `render_heads` is what a delta sends, and
    its `plus` is how the client puts the card back after a sort or a group swap — the card for a
    defined team, sessions or none, and nothing for *No team* or a badge no definition carries."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import render_heads, view

    records = [
        {"id": "ao-g1", "name": "grinder-1", "state": "working", "dir": "/tmp/r", "kind": "agent", "team": "live"},
        {"id": "ao-x1", "name": "stray-1", "state": "idle", "dir": "/tmp/r", "kind": "agent", "team": "badge-only"},
        {"id": "ao-sh", "name": "sh1", "state": "idle", "dir": "/tmp/x", "kind": "agent", "adapter": "shell"},
    ]
    rows = [
        {"name": "live", "manager": "orc", "members": 1, "projects": ["p"], "wound_down": None},
        {"name": "never-run", "manager": "orc", "members": 2, "projects": ["p"], "wound_down": None},
    ]
    heads = {h["team"]: h for h in render_heads(team_groups([view(r, records) for r in records], rows))}
    assert set(heads) == {"live", "never-run", "badge-only", ""}
    for team in ("live", "never-run"):
        plus = heads[team]["plus"]
        assert plus.lstrip().startswith('<div class="card sc plus"') and f'data-plus="{team}"' in plus, team
        assert f'href="/new?team={team}"' in plus
    assert heads[""]["plus"] == "" and heads["badge-only"]["plus"] == ""
    assert render_heads(None) is None  # the flat page has no groups to carry


# ── the anchor seat's words (§4.5a *card: on call — the anchor seat's words*, TD-387) ──


def test_the_anchor_seats_slot_says_what_brings_it_and_why_a_fill_waits():
    from agentorc.org import ANCHOR_WHEN
    from agentorc.ui.app import view

    seats = {"ao-w": ANCHOR_WHEN}
    slot = view(_card(state="closed", pane=False), seats=seats)["slot"]
    assert slot["text"] == "on call — comes when the checkout's lane gains work"
    assert "in the checkout itself" in slot["full"] and slot["caption"].startswith("last ran")
    # held off by a session in the checkout: the tick's `seat_held` (§6 rule 3, TD-386) names it
    held = {"by": "ao-alpha-paul", "why": "held by ao-alpha-paul (working)"}
    slot = view(_card(state="closed", pane=False, seat_held=held), seats=seats)["slot"]
    assert slot["text"] == "on call — the checkout is yours · a session holds it: ao-alpha-paul"
    assert "fills the seat once the checkout is free" in slot["full"]
    # …or by the tree itself: the tick's own words
    tree = {"by": "checkout", "why": "branch td-x, 2 files uncommitted"}
    slot = view(_card(state="closed", pane=False, seat_held=tree), seats=seats)["slot"]
    assert slot["text"] == "on call — the checkout is yours · branch td-x, 2 files uncommitted"
    # a malformed mark costs the words, never the card; another seat never reads it
    assert view(_card(state="closed", pane=False, seat_held="junk"), seats=seats)["slot"]["text"].endswith(
        "lane gains work"
    )
    other = view(_card(state="closed", pane=False, seat_held=tree), seats={"ao-w": "comes on the next question"})
    assert other["slot"]["text"] == "on call — comes on the next question"


def test_the_occupancy_check_names_a_seat_with_its_state_and_claim():
    from agentorc.ui.cards import seat_occupant

    now = datetime(2026, 10, 8, 7, 0, tzinfo=UTC)
    seat = {
        "id": "ao-alpha-anchor-ao-1",
        "name": "anchor-ao-1",
        "state": "working",
        "seat": {"trigger": "work"},
        "progress": [
            {"ref": "TD-200", "status": "claimed", "at": "2026-10-07T01:00:00Z"},  # past its lease
            {"ref": "TD-299", "status": "claimed", "at": "2026-10-08T06:00:00Z"},
            {"ref": "TD-301", "status": "claimed", "at": "2026-10-08T06:30:00Z", "source": "derived"},
        ],
    }
    paul = {"id": "ao-alpha-paul", "name": "paul", "state": "idle"}
    fleet = [seat, paul]
    assert seat_occupant(["ao-alpha-anchor-ao-1 (working)"], fleet, now) == (
        "anchor-ao-1 holds it (a seat, working · TD-299)"
    )
    assert seat_occupant(["ao-alpha-anchor-ao-1@node1 (idle)"], fleet, now) == (
        "anchor-ao-1 holds it (a seat, idle · TD-299)"
    )
    assert seat_occupant(["ao-alpha-anchor-ao-1 (idle)"], [{**seat, "progress": []}], now) == (
        "anchor-ao-1 holds it (a seat, idle)"
    )
    # not a seat, outside agentorc, or nothing: the plain *in use by* words stand
    assert seat_occupant(["ao-alpha-paul (idle)"], fleet, now) == ""
    assert seat_occupant(["claude-1 (claude-code, outside agentorc)"], fleet, now) == ""
    assert seat_occupant([], fleet, now) == ""
    # a seat listed after another holder is still named (review of #1253)
    assert seat_occupant(["claude-1 (claude-code, outside agentorc)", "ao-alpha-anchor-ao-1 (idle)"], fleet, now) == (
        "anchor-ao-1 holds it (a seat, idle · TD-299)"
    )
    js = (pathlib.Path(__file__).parents[1] / "src/agentorc/ui/static/app.js").read_text()
    assert "(o.seat || `in use by ${o.occupants.join" in js


def test_an_idle_session_that_waits_on_someone_reads_waiting(tmp_path, monkeypatch):
    """§4.2 *Waiting*, §4.5 *The card's anatomy* (TD-418, built by TD-428): an `idle` record with a
    claim whose PR the repo reading holds open, or an open `ask`/`steer` to the person whose `about`
    names a reference, draws the teal *waiting* pill — its own filter word and Agents pill, ranked after
    `working` — with the wait as the slot's ending; the payload's state stays `idle`."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import card_order, rollup, state_counts, view, waits_of
    from sessionorc.models import STATE_RANK

    repos = {
        "/r": {
            "name": "r",
            "prs": {"open": [{"number": 1302, "branch": "td009-x"}, {"number": 7, "branch": "td428-pill"}]},
        }
    }

    def claim(**kw):
        return [{"ref": "TD-009", "status": "claimed", "at": "2026-10-09T10:00:00Z", **kw}]

    # a claim's own PR, open in the reading: *waiting · review #1302*
    v = view(_card(progress=claim(pr=1302)), repos=repos)
    assert (v["state"], v["state_class"], v["state_label"], v["pill_word"]) == ("idle", "waiting", "waiting", "waiting")
    assert v["slot"]["text"] == "waiting · review #1302"
    assert STATE_RANK["working"] < v["rank"] < STATE_RANK["idle"] - 0.5  # after working, before an unseen idle
    # the tick's `review_pr`, and an open PR whose head branch names the reference, are the same reading
    assert view(_card(progress=claim(review_pr=1302)), repos=repos)["pill_word"] == "waiting"
    assert view(_card(progress=claim()), repos=repos)["slot"]["text"] == "waiting · review #1302"
    # *with the techlead* while the seat's `prs_waiting.asks` holds this record's ask for that PR
    asks = [{"from": "ao-w@kmaster", "pr": 1302}]
    seat = {"id": "ao-tl", "name": "techlead-1", "role": "techlead", "state": "idle",
            "prs_waiting": {"n": 1, "oldest": "2026-10-09T10:00:00Z", "asks": asks}}  # fmt: skip
    rec = _card(progress=claim(pr=1302))
    assert view(rec, [rec, seat], repos=repos)["slot"]["text"] == "waiting · review #1302 with the techlead"
    other = {**seat, "prs_waiting": {"n": 1, "asks": [{"from": "ao-other", "pr": 1302}]}}
    assert view(rec, [rec, other], repos=repos)["slot"]["text"] == "waiting · review #1302"
    # a PR the reading does not hold open (merged, closed, unknown), no reading at all, a done entry: idle
    for idle in (
        view(_card(progress=claim(pr=1301)), repos=repos),
        view(_card(progress=claim(pr=1302))),
        view(_card(progress=[{"ref": "TD-009", "status": "done", "pr": 1302}]), repos=repos),
        view(_card(progress=[{"ref": "TD-1", "status": "claimed"}]), repos=repos),
    ):
        assert (idle["state_class"], idle["pill_word"]) == ("idle", "idle")
    # only an idle record: a working one with the same claim is working
    assert view(_card(state="working", progress=claim(pr=1302)), repos=repos)["pill_word"] == "working"

    # the person-inbox wait (TD-274): an ask naming a reference makes a wait; one in prose makes none
    def ask(about):
        return {"id": "m-a", "from": "ao-w", "to": ["person"], "kind": "ask", "about": about, "text": "q?"}

    v = view(_card(), waits=waits_of([ask("TD-222")]))
    assert v["pill_word"] == "waiting" and v["slot"]["text"] == "waiting on you: TD-222"
    assert view(_card(), waits=waits_of([ask("the colour of the button")]))["pill_word"] == "idle"
    # a member that declared out of work and waits on you reads *waiting*, the declaration in the slot
    oow = {"at": "2026-10-09T09:00:00Z", "why": "nothing pickable"}
    v = view(_card(out_of_work=oow), waits=waits_of([ask("TD-222")]))
    assert v["pill_word"] == "waiting"
    assert v["slot"]["text"] == "out of work · waiting on you: TD-222 — nothing pickable"
    # *idle · unseen* wins on an interactive session that waits; the slot still says the wait
    mine = view(_card(unattended=False, seen_at=None), waits=waits_of([ask("TD-222")]))
    assert (mine["state_label"], mine["pill_word"]) == ("idle · unseen", "unseen")
    assert mine["slot"]["text"] == "waiting on you: TD-222"

    # the sort, the header's counts and the rollup's Agents pill
    waiting = view(_card(name="a", progress=claim(pr=1302)), repos=repos)
    cards = [
        view(_card(name="b")),
        mine,
        waiting,
        view(_card(name="c", state="working")),
    ]
    assert [c["pill_word"] for c in sorted(cards, key=card_order)] == ["working", "waiting", "unseen", "idle"]
    assert state_counts(cards) == ["1 working", "1 waiting", "1 unseen", "1 idle"]
    for c in cards:
        c["team"] = "t"
    agents = rollup(
        [{"team": "t", "live": True, "members": cards, "summary": {"phases": {}, "answers": [], "asked": None}}]
    )["agents"]
    assert [(a["word"], a["cls"], a["n"]) for a in agents] == [
        ("working", "working", 1),
        ("waiting", "waiting", 1),
        ("unseen", "idle", 1),
        ("idle", "idle", 1),
    ]
