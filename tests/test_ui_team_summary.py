"""TD-176 slice 3, design §4.5 screen 1 *The Org, team-first*: a live team's card carries a summary
of three facets — Repo, TDs in motion, Answer needed / Doing — and its members are compact cards."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from agentorc.ui import app as ui

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def reading(root: str, **kw) -> dict:
    return {
        "name": "samscrape",
        "root": root,
        "remote": "git@github.com:eye/samscrape.git",
        "ledger": {
            "entries": [
                {"id": "TD-301", "title": "recover stuck notices", "for_page": "pickable", "priority": "high"},
                {"id": "TD-310", "title": "what the composer says", "for_page": "design-first", "kind": "design-first",
                 "priority": "medium"},
            ],
            "by_priority": {"high": 1, "medium": 1, "low": 0},
            "by_kind": {"pickable": 1, "design-first": 1, "for-you": 0, "other": 0},
            "windows": {
                "day": {"opened": 1, "closed": 0},
                "week": {"opened": 3, "closed": 4},
                "month": {"opened": 5, "closed": 6},
            },
        },
        "prs": {
            "open": [{"number": 811, "created": _iso(NOW - timedelta(days=3)), "url": "https://github.com/eye/samscrape/pull/811"}],
            "recent": [{"number": 437, "state": "merged", "url": "https://github.com/eye/samscrape/pull/437"}],
            "windows": {
                "day": {"opened": 3, "closed": 2},
                "week": {"opened": 7, "closed": 7},
                "month": {"opened": 9, "closed": 9},
            },
        },
        "at": _iso(NOW - timedelta(minutes=2)),
        **kw,
    }  # fmt: skip


def member(sid: str, state: str = "working", repo: str = "/r/samscrape", **kw) -> dict:
    return {"id": sid, "name": sid, "state": state, "team": "grind", "repo": repo, "unattended": True, **kw}


def claim(ref: str, pr: int | None = None, status: str = "claimed") -> dict:
    return {"ref": ref, "status": status, "pr": pr}


def test_the_repo_is_the_one_most_members_work_in_and_absent_is_no_repo_here():
    repos = {"/r/samscrape": reading("/r/samscrape"), "/r/other": {**reading("/r/other"), "name": "other"}}
    ms = [member("a"), member("b"), member("c", repo="/r/other")]
    assert ui.team_repo(ms, repos)["name"] == "samscrape"
    assert ui.team_repo([member("d", repo="/elsewhere")], repos) is None
    s = ui.team_summary("grind", [member("d", repo="/elsewhere")], repos, {}, now=NOW)
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    assert "no repo here" in html


def test_the_repo_facet_draws_the_bars_the_blocks_and_could_not_look():
    f = ui.repo_facet(reading("/r/s"), NOW, {"n": 2, "age": "40m"})
    assert [b["key"] for b in f["ledger"]["priority"]] == ["high", "medium"]  # a zero is left out
    assert sum(b["pct"] for b in f["ledger"]["kind"]) == 100
    assert f["prs"]["windows"]["week"] == {"opened": 7, "closed": 7, "opct": 50.0}
    assert f["prs"]["oldest"] == "3d" and f["read"] == "2m" and f["web"] == "https://github.com/eye/samscrape"
    html = ui.templates.get_template("team_summary.html").render(
        g={
            "team": "grind",
            "summary": ui.team_summary("grind", [member("a")], {"/r/samscrape": reading("/r/samscrape")}, {}, now=NOW),
        }
    )
    assert "Technical debt (2 open)" in html and "Pull requests (1 open)" in html and "1 High" in html
    assert 'href="/repo/samscrape"' in html
    # a PR read that failed with no reading before it: *could not look*, never zero
    bad = reading("/r/samscrape", prs={"error": "gh: offline", "failed_at": _iso(NOW)})
    s = ui.team_summary("grind", [member("a")], {"/r/samscrape": bad}, {}, now=NOW)
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    assert "could not look" in html and "(0 open)" not in html
    # …and one with a reading before it keeps the numbers, dimmed, the error on hover
    kept = reading("/r/samscrape")
    kept["prs"] = {**kept["prs"], "error": "gh: offline"}
    s = ui.team_summary("grind", [member("a")], {"/r/samscrape": kept}, {}, now=NOW)
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    assert "Pull requests (1 open)" in html and "the last read failed: gh: offline" in html


def test_tds_in_motion_derive_the_phase_and_merge_a_shared_reference():
    r = reading("/r/s")
    ms = [
        member("g1", progress=[claim("TD-301", 811)]),
        member("g2", progress=[claim("TD-296", 437), claim("TD-100", status="done")]),
        member("g3", progress=[claim("TD-290")]),
        member("d1", progress=[claim("TD-310")]),
        member("me", unattended=False, progress=[claim("TD-290")]),
    ]
    rows = {x["ref"]: x for x in ui.motion_rows(ms, r)}
    assert set(rows) == {"TD-301", "TD-296", "TD-290", "TD-310"}  # a done claim is not in motion
    assert rows["TD-310"]["phase"] == "design" and rows["TD-290"]["phase"] == "grind"
    assert rows["TD-301"]["phase"] == "review" and rows["TD-301"]["pr_url"].endswith("/pull/811")
    assert rows["TD-296"]["phase"] == "review" and rows["TD-296"]["pr_state"] == "merged"  # review until done
    assert [w["name"] for w in rows["TD-290"]["members"]] == ["g3", "me"] and rows["TD-290"]["members"][1]["mine"]
    assert [x["phase"] for x in ui.motion_rows(ms, r)] == ["design", "grind", "review", "review"]
    assert rows["TD-301"]["title"] == "recover stuck notices" and rows["TD-296"]["title"] == ""  # not in the ledger


def test_tds_in_motion_read_the_entrys_kind_and_find_a_claims_pr_by_its_branch():
    # TD-197, the screenshot's three rows: a pickable design-first entry (the page's bucket says
    # *pickable*) reads *design*; a claim whose PR waits for its reader while the grinder has moved
    # to another branch reads *review*, found by the PR's head branch; a claim with nothing reads *grind*
    r = reading("/r/s")
    r["ledger"]["entries"] += [
        {"id": "TD-175", "title": "round log", "for_page": "pickable", "kind": "design-first", "priority": "medium"},
        {"id": "TD-186", "title": "held", "for_page": "pickable", "kind": "build", "priority": "medium"},
    ]
    r["prs"]["open"] += [
        {"number": 628, "branch": "td186-held-thing", "url": "https://github.com/eye/samscrape/pull/628"},
        {"number": 629, "branch": "fix-something"},
    ]
    r["prs"]["recent"] += [{"number": 600, "state": "merged", "branch": "td146-slice-1"}]
    ms = [member("g1", progress=[claim("TD-146"), claim("TD-186")]), member("d1", progress=[claim("TD-175")])]
    rows = {x["ref"]: x for x in ui.motion_rows(ms, r)}
    assert rows["TD-175"]["phase"] == "design"
    assert rows["TD-186"]["phase"] == "review" and rows["TD-186"]["pr"] == 628
    assert rows["TD-186"]["pr_url"].endswith("/pull/628")
    # a merged slice's branch does not make the entry's next slice *review*
    assert rows["TD-146"]["phase"] == "grind" and rows["TD-146"]["pr"] is None


def test_answer_needed_opens_the_facet_and_doing_is_newest_first():
    perm = {"kind": "permission", "text": "Bash git push -u origin td301-fix", "tool_use_id": "t1"}
    ms = [member("g1", state="needs-you", pending=perm), member("g2")]
    doing = {
        "grind": [
            {"id": "g2", "text": "first", "at": _iso(NOW - timedelta(minutes=5))},
            {"id": "g1", "text": "second", "at": _iso(NOW)},
        ]
    }
    s = ui.team_summary("grind", ms, {}, doing, now=NOW)
    assert s["face"] == "answer" and s["answer_key"] == "g1:permission"
    assert [d["text"] for d in s["doing"]] == ["second", "first"]
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    assert (
        'data-act="allow" data-id="g1"' in html and 'class="denywhy"' in html and "git push -u origin td301-fix" in html
    )
    assert 'data-face-default="answer"' in html
    quiet = ui.team_summary("grind", [member("g2")], {}, doing, now=NOW)
    assert quiet["face"] == "doing" and quiet["answer_key"] == ""


def test_the_doing_feed_keeps_its_scroll_across_the_summary_swap():
    # TD-205: the summary is swapped whole on every delta; the feed names itself for the swap to
    # read its scrollTop, and the position goes back once syncSummaries has shown the face again
    s = ui.team_summary("grind", [member("g2")], {}, {}, now=NOW)
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    assert '<div class="dfeed" data-keep-scroll="doing">' in html
    js = (ui.Path(ui.__file__).parent / "static" / "app.js").read_text()
    sync = js.index("function syncSummaries()")
    restore = js.index("AO.restoreScrolls(sum, scrollKept[sum.dataset.team])")
    assert restore > js.index("showSummary(sum);", sync) > sync  # the face is shown, then the box scrolled
    # …and the Repo page, which includes the same summary and re-reads its part whole
    assert "scrolled = AO.scrolls(box);" in js and "AO.restoreScrolls(box, scrolled);" in js


def test_a_live_teams_members_are_compact_and_the_header_drops_its_chips():
    ms = [member("g1", progress=[claim("TD-301", 811)], role_label="Grinder"), member("g2", state="exited")]
    for m in ms:
        m.update(rank=1, slot={"text": "exited · code 0", "caption": ""}, place="kmaster / samscrape")
    (g,) = ui.team_groups(ms, (), {"/r/samscrape": reading("/r/samscrape")}, {})
    assert g["summary"] and all(m["compact"] for m in g["members"])
    by = {m["id"]: m for m in g["members"]}
    assert by["g1"]["compact_line"] == "Grinder · TD-301 → #811"
    assert by["g2"]["compact_line"] == "exited · code 0"
    head = ui.templates.get_template("group_head.html").render(g=g)
    # the chips come back only for the fold (TD-194): drawn as `foldonly`, which CSS shows while folded
    unfolded = re.sub(r'<span class="meta counts foldonly">[^<]*</span>', "", head)
    assert "· 2 sessions" in head and "working" not in unfolded and "1 working" in head


def test_a_wound_down_team_shows_what_it_left():
    """§4.5a *team card: summary*, *card: compact* (TD-181, built by TD-192): a team whose members
    have all exited carries the three facets — the Repo facet from the live readings, TDs in motion
    from the claims the last records hold, and Doing, since nobody can be waiting — and its members
    are compact; *No team* has neither, and the rollup sums only live teams."""
    perm = {"kind": "permission", "text": "Bash x", "tool_use_id": "t1"}
    ms = [
        member("g1", state="exited", progress=[claim("TD-301", 811)], role_label="Grinder", pending=perm),
        member("g2", state="closed"),
        {**member("n1", state="exited"), "team": ""},
    ]
    for m in ms:
        m.update(rank=1, slot={"text": "exited · code 0", "caption": ""}, place="kmaster / samscrape")
    doing = {"grind": [{"at": "2026-09-26T09:00:00Z", "id": "g1", "text": "TD-301: the last test"}]}
    groups = ui.team_groups(ms, (), {"/r/samscrape": reading("/r/samscrape")}, doing)
    g = next(x for x in groups if x["team"] == "grind")
    none = next(x for x in groups if not x["team"])
    assert g["live"] == 0 and g["summary"] and all(m["compact"] for m in g["members"])
    s = g["summary"]
    assert s["repo"] and s["repo"]["name"] == "samscrape"
    assert [x["ref"] for x in s["motion"]] == ["TD-301"]  # the claim as the last record holds it
    assert s["face"] == "doing" and not s["answers"]  # nobody can be waiting
    html = ui.templates.get_template("team_summary.html").render(g=g)
    assert "TD-301" in html and "TD-301: the last test" in html
    assert none["summary"] is None and not any(m.get("compact") for m in none["members"])
    assert ui.rollup(groups) is None  # nothing live: no rollup, whatever the summaries hold
    # nothing claimed: the facet says so
    (quiet,) = ui.team_groups([member("q", state="exited", rank=1, slot={}, place="x")], (), {}, {})
    assert quiet["summary"]["motion"] == [] and "nothing claimed" in ui.templates.get_template(
        "team_summary.html"
    ).render(g=quiet)


def test_compact_in_marks_every_team_member_and_no_team_none():
    v = member("g1")
    assert ui.compact_in(dict(v), [member("x", state="exited")])["compact"]  # a wound-down team too (TD-192)
    assert ui.compact_in(dict(v), [member("x")])["compact"]
    assert ui.compact_in({**v, "team": ""}, [member("x")]).get("compact") is None


# -- the Org rollup (TD-176 slice 4, §4.5a *Org: rollup*) --------------------------------------------


def test_the_rollup_sums_the_live_teams_and_counts_a_shared_repo_once():
    perm = {"kind": "permission", "text": "Bash x", "tool_use_id": "t1"}
    a = [
        member("g1", state="needs-you", pending=perm, progress=[claim("TD-301", 811)]),
        member("g2", progress=[claim("TD-290")]),
    ]
    b = [{**member("h1", progress=[claim("TD-310")]), "team": "other"}]
    for m in (*a, *b):
        m.update(
            rank=1,
            slot={},
            place="kmaster / samscrape",
            pill_word="needs-you" if m["state"] == "needs-you" else "working",
        )
    groups = ui.team_groups([*a, *b], (), {"/r/samscrape": reading("/r/samscrape")}, {})
    ro = ui.rollup(groups)
    assert [(x["word"], x["n"]) for x in ro["agents"]] == [("needs-you", 1), ("working", 2)]
    assert {p["key"]: p["n"] for p in ro["phases"]} == {"design": 1, "grind": 1, "review": 1} and ro["motion"] == 3
    assert ro["prs"]["day"] == {"opened": 3, "closed": 2, "opct": 60.0}  # one repo, counted once
    assert ro["prs_open"] == 1 and ro["answer_needed"] == 1 and ro["answer_team"] == "grind"
    html = ui.templates.get_template("rollup.html").render(ro=ro, person_needs=4)
    assert 'data-state-filter="needs-you"' in html and "needs you (1)" in html
    assert 'href="#tsum-grind"' in html and "data-inbox-needs>4<" in html
    # …and the fold's `aria-controls` never renames the summary the link lands on (TD-194)
    js = (ui.Path(ui.__file__).parent / "static" / "app.js").read_text()
    assert "if (!el.id) el.id = `tgrid-${team}`" in js
    assert "Agents (3)" in html and "TDs in motion (3)" in html and "PRs in motion (1)" in html
    assert ui.rollup(None) is None and ui.rollup([g for g in groups if not g["team"]]) is None
    # a wound-down team beside them carries a summary now (TD-192), and adds nothing to the sums
    gone = [{**member("z1", state="exited", progress=[claim("TD-290")]), "team": "gone", "rank": 1, "slot": {}}]
    more = ui.team_groups([*a, *b, *gone], (), {"/r/samscrape": reading("/r/samscrape")}, {})
    assert next(g for g in more if g["team"] == "gone")["summary"]
    assert ui.rollup(more) == ro


def test_every_card_carries_the_word_the_state_filter_matches():
    assert ui.view({"id": "a", "name": "a", "state": "needs-you"})["pill_word"] == "needs-you"
    assert ui.view({"id": "a", "name": "a", "state": "stalled?"})["pill_word"] == "stalled"
    seat = ui.view({"id": "t", "name": "t", "state": "exited"}, seats={"t": "comes on the next question"})
    assert seat["pill_word"] == "on-call"
    html = ui.templates.get_template("card.html").render(s=ui.view({"id": "a", "name": "a", "state": "working"}))
    assert 'data-pill="working"' in html
    js = (ui.Path(ui.__file__).parent / "static" / "app.js").read_text()
    assert "/^state:/i" in js and "c.dataset.pill" in js


def test_the_rollup_counts_the_board_items_past_their_date_beside_in_the_inbox():
    """§4.5 screen 1 and §4.5a *Org: rollup* (TD-178): *n in the Inbox · m overdue* — the Needs you
    board items whose `Due:` date has passed, never one due today; nothing overdue draws no count."""
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 9, 26, 18, tzinfo=UTC)
    today = now.astimezone().date()
    boards = [
        {"row": "board", "id": "b1", "due": (today - timedelta(days=3)).isoformat(), "text": "x", "at": ""},
        {"row": "board", "id": "b2", "due": today.isoformat(), "text": "y", "at": ""},
    ]
    secs = ui.inbox_sections([], boards=boards, now=now)
    assert secs["count"] == 2 and secs["overdue_n"] == 1
    assert ui.inbox_sections([], boards=boards[1:], now=now)["overdue_n"] == 0
    ro = {"agents": [], "n_agents": 0, "phases": [], "motion": 0, "prs_open": 0, "has_repo": False,
          "answer_needed": 0, "answer_team": ""}  # fmt: skip
    html = ui.templates.get_template("rollup.html").render(ro=ro, person_needs=2, person_overdue=1)
    assert "data-inbox-needs>2<" in html and "data-inbox-overdue>1</span> overdue" in html
    assert 'class="meta overdue hidden"' in ui.templates.get_template("rollup.html").render(ro=ro, person_needs=2)


def test_an_ended_member_keeps_its_last_reference_with_the_prs_mark():
    """§4.5 screen 1 *A PR that is no longer open says so*, §4.5a *card: compact* and card **report
    line** (TD-182, built by TD-193): *Grinder · exited · code 0 · TD-066 → #158 merged* — read from
    the readings of the record's repo, the card's report line and the Members list the same."""
    readings = {
        "/r/samscrape": {**reading("/r/samscrape"), "prs": {"open": [], "recent": [{"number": 158, "state": "merged"}]}}
    }
    m = member("g1", state="exited", progress=[claim("TD-066", 158, status="done")], role_label="Grinder")
    m.update(rank=1, slot={"text": "exited · code 0", "caption": ""}, place="kmaster / samscrape")
    v = ui.view(m, [m], repos=readings)
    assert v["report"] == "TD-066 → #158 merged · 1/1 done" and v["pr_marks"] == {"158": "merged"}
    v.update(role_label="Grinder", slot={"text": "exited · code 0", "caption": ""})  # what the view derives live
    (g,) = ui.team_groups([v], (), readings, {})
    assert g["members"][0]["compact_line"] == "Grinder · exited · code 0 · TD-066 → #158 merged"
    # no readings: nothing is guessed
    bare = ui.view(m, [m])
    assert bare["report"] == "TD-066 → #158 · 1/1 done" and bare["pr_marks"] == {}
    # a live claim in review carries the mark too, once its PR has merged under it
    live = member("g2", progress=[claim("TD-067", 158)], role_label="Grinder")
    live.update(rank=1, slot={}, place="x")
    lv = {**ui.view(live, [live], repos=readings), "role_label": "Grinder"}
    assert ui.compact_line(lv) == "Grinder · TD-067 → #158 merged"
    # the lead's Members list says the same
    lead = {**member("m", role_label="Manager"), "rank": 1}
    kid = {**m, "controllers": ["m"]}
    assert ui.view(lead, [lead, kid], repos=readings)["members"][0]["report"] == "TD-066 → #158 merged · 1/1 done"
