"""The Org page's team groups (design §4.5a **team groups** / **team badge**, §4.9): the pure
grouping function, and one render of the page template over its output."""

from __future__ import annotations

import pathlib

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


def test_a_folded_team_says_how_much_unread_mail_its_cards_hold():
    """Design §4.5a team header **✉ n** (TD-071 item 2): the sum of the folded cards' unread chips,
    nothing at zero, and only on a team that folds — a live team's cards show their own."""
    head = templates.get_template("group_head.html")
    mailed = {**sess("ao-a", "a", team="t", state="exited"), "unread": 19}
    (g,) = team_groups([mailed, sess("ao-b", "b", team="t", state="exited")])
    assert g["unread"] == 19 and 'class="badge unread foldmail"' in head.render(g=g) and "✉ 19" in head.render(g=g)
    (quiet,) = team_groups([sess("ao-a", "a", team="t", state="exited")])
    assert "foldmail" not in head.render(g=quiet)
    (live,) = team_groups([{**sess("ao-a", "a", team="t", state="working"), "unread": 2}])
    assert "foldmail" not in head.render(g=live)


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
    assert 'data-team="ao-grind"' in html and "No team" in html
    # the card's team badge: drawn, and marked for the stylesheet to hide inside its own group (TD-095)
    assert 'class="badge team ingroup"' in html
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
                {"label": "week", "pct": 88, "resets": "2026-09-24T00:00:00Z"},
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
    assert "grind · week 88%" in html and "5h 19%" not in html.split("grind · week 88%")[1].split("</span>")[0]
    assert 'data-profile="grind" data-pct="88" data-near="1" class="near"' in html
    assert "week 88% (resets 2026-09-24T00:00:00Z) · 5h 19% (resets 2026-09-20T22:00:00Z)" in html
    assert 'data-profile="openai" data-pct="100" data-near="1" class="cap"' in html and "openai · day 100%" in html
    assert 'data-profile="quietly"' not in html  # no windows, no chip
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
}  # fmt: skip


def test_a_refused_usage_poll_draws_the_held_reading_stale_rather_than_nothing():
    """design §4.5a **usage** chip, TD-087. The chip was empty through three promotes because the
    endpoint answered 429 and every failure was one silence. The host agent now keeps the last good
    reading with the adapter's `reason` beside it (PR #307); this is the page's half — **stale, not
    out**. The windows still print (a five-hour window does not change while we are refused), dimmed
    with *· stale*, and the hover says when the reading was taken and why the poll since failed. A
    refusal with nothing ever held is *no reading*, drawn the same way: a chip that silently went
    out is the thing this entry was. A tool that reports no quota still has no chip."""
    from agentorc.ui.app import usage_chip

    got = {k: usage_chip("grind", u) for k, u in USAGE_CASES.items()}
    assert got["fresh"] == {"text": "grind · week 88%", "title": "week 88% (resets ?) · 5h 19% (resets r1)",
                            "pct": 88, "cls": "near", "near": True}  # fmt: skip
    assert got["legacy"]["text"] == "grind · day 40%" and got["legacy"]["cls"] == ""  # no reason is ok, not stale
    held = got["held_429"]
    assert held["text"] == "grind · week 49% · stale" and held["cls"] == "stale" and held["pct"] == 49
    assert held["title"].startswith("held reading from 2026-09-20T20:00:00Z — the last poll was refused: ")
    assert "rate-limited by the usage endpoint, which asked to be left 30 min" in held["title"]
    assert held["title"].endswith("week 49% (resets r3)")  # every window is still on hover
    # a stale reading at a cap is still red: it is the best evidence there is
    assert got["held_cap"]["cls"] == "cap stale" and "could not be read" in got["held_cap"]["title"]
    assert "asked to be left 3 min" in got["held_odd_wait"]["title"]  # rounded up, in both homes alike
    assert got["never_read"]["text"] == "grind: no reading yet" and got["never_read"]["cls"] == "stale"
    assert "no credentials for this profile" in got["never_read"]["title"]
    assert "brand_new_reason" in got["unknown_word"]["title"]  # a word we do not know is shown, not dropped
    assert got["no_quota"] is None and got["not_a_dict"] is None
    assert got["junk_window"] is None  # a window whose number is not a number is not drawn from


def test_the_usage_chip_prints_the_line_its_reserve_makes_and_ranks_by_the_gap():
    """design §4.5a **usage** chip, §6 *Usage gate* (TD-100 slice 3, the chip's line): a window the
    profile has a reserve for prints its line after the number, *grind · week 61% / 70%*, with the
    reserve, the days left and when the line next moves on hover. *Worst* is the smallest gap to a
    line — the tool's 100% where there is none, so an unreserved 97% outranks a reserved 40% of a
    70% line — and *near* is within ten points of a line, 80% without one."""
    from agentorc.ui.app import usage_chip, with_lines

    got = {k: usage_chip("grind", u) for k, u in USAGE_CASES.items()}
    lined = got["lined"]
    assert lined["text"] == "grind · week 61% / 70%" and lined["pct"] == 61
    assert lined["near"] is True and lined["cls"] == "near"  # 9 points under its line
    assert lined["title"] == (
        "week 61% / line 70% (reserve 10% a day, 3 days left; line moves 2026-09-24T07:00:00Z; resets r6)"
        " · 5h 40% (resets r5)"
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


def test_the_usage_chip_rule_is_the_same_in_the_page_and_in_app_js(tmp_path):
    """The chip is drawn twice — server-side at page load, and by `app.js` on each pushed `usage`
    event — so the rule lives twice, and a rule kept in two places is held to one set of cases here
    or the two drift (the page would say *stale* until the first push, and then not)."""
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
    out = subprocess.run([node, str(probe), str(app_js), json.dumps(USAGE_CASES)],
                         capture_output=True, text=True, timeout=30)  # fmt: skip
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {k: usage_chip("grind", u) for k, u in USAGE_CASES.items()}
    assert "AO.usageChip(ev.profile, ev.usage)" in app_js.read_text()  # and the push really uses it


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
for (const k of Object.keys(cases)) out[k] = window.AO.usageChip("grind", cases[k]);
console.log(JSON.stringify(out));
"""


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
    # the idle nudge (§6 rule 4): nudged in this stretch and still idle twenty minutes later
    stale = view(_card(since="2026-09-21T01:00:00Z", nudged_at="2026-09-21T01:20:00Z"))["slot"]
    assert stale["text"] == "idle · open work" and stale["kind"] == "lim"
    # a nudge from an earlier stretch says nothing about this one
    assert view(_card(since="2026-09-21T02:00:00Z", nudged_at="2026-09-21T01:20:00Z"))["slot"]["text"] != stale["text"]
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
    # exited reads ready to close too, and its first button is still Forget: nothing left to close
    ex = view(_card(state="exited", exit_code=0, git=clean))
    assert ex["slot"]["caption"] == "ready to close ✓" and ex["next_act"] == "forget"
    assert view(_card(state="closed", pane=False))["next_act"] == "details"
    assert view(_card(state="working"))["next_act"] == "focus"
    perm = {"kind": "permission", "text": "Bash: ls", "tool_use_id": "tu"}
    assert view(_card(state="needs-you", pending=perm))["next_act"] == "allow"
    html = templates.get_template("card.html").render(s=ready)
    foot = html.split('class="sc-foot"')[1]
    assert foot.index('data-act="close"') < foot.index("/focus/ao-w")  # the next act comes first
    assert 'data-act="close"' not in html.split('class="sc-foot"')[0]  # and nothing is left in the slot


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
    assert 'data-confirm="Close w?">Close</button>' in foot(_card(git=clean))


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
    assert "kmaster / agentorc" in head and "· 1 working · 1 idle" in head and " live<" not in head
    nohead = templates.get_template("group_head.html").render(g=none)
    assert ">No team</span>" in nohead and ">2 sessions</span>" in nohead and "kmaster / wg" not in nohead


def test_within_one_urgency_the_persons_own_sort_first_and_mine_shows_only_them(tmp_path, monkeypatch):
    """Design §4.5 *One order, no control*, second pass (TD-095): the key is (rank, interactive
    first, name) — a worker that needs a person still outranks the person's own idle session, and a
    manager's card is placed first before any of this. §4.5a ***mine***: one press shows only the
    interactive sessions; the card says which it is, the page's script does the rest."""
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
    assert "(mine && !c.dataset.mine)" in js  # …and *mine* composes with the box
    html = templates.get_template("org.html").render(
        sessions=vs, groups=None, counts=dict.fromkeys(("needs-you", "limited", "stalled?"), 0),
        strip={"teams": [{"name": "t"}], "source": "", "notes": []}, host="h", active="Org",
        agent_down=False, volatile=False, usage={},
    )  # fmt: skip
    assert 'id="mine" aria-pressed="false"' in html


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
