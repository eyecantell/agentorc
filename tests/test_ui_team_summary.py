"""TD-176 slice 3, design §4.5 screen 1 *The Org, team-first*: a live team's card carries a summary
of three facets — Repo, TDs in motion, Answer needed / Doing — and its members are compact cards."""

from __future__ import annotations

import pathlib
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


def test_tds_in_motion_carry_the_entrys_priority_as_a_letter_and_sort_by_it():
    """TD-232 slice 1 (design §4.5a *team card: TDs in motion*, **Priority**): one letter between the
    phase and the reference, a chip in a slot of fixed width; the slot is empty for a reference the
    ledger reading does not hold and for an entry with no priority or another word; within a phase,
    High first and an unmarked row last."""
    r = reading("/r/s")
    r["ledger"]["entries"] += [
        {"id": "TD-100", "title": "a low one", "for_page": "pickable", "priority": "low"},
        {"id": "TD-200", "title": "no priority", "for_page": "pickable", "priority": ""},
        {"id": "TD-250", "title": "a word outside the three", "for_page": "pickable", "priority": "critical"},
    ]
    ms = [
        member("g1", progress=[claim("TD-100"), claim("TD-200")]),
        member("g2", progress=[claim("TD-301"), claim("TD-250"), claim("TD-999")]),
    ]
    rows = ui.motion_rows(ms, r)
    # High, Low, then the three unmarked (none, a word outside the three, not in the reading) by reference
    assert [(x["ref"], x["priority"]) for x in rows] == [
        ("TD-301", "high"),
        ("TD-100", "low"),
        ("TD-200", ""),
        ("TD-250", ""),
        ("TD-999", ""),
    ]
    ms[0]["progress"][0]["pr"] = 5  # a phase comes before a priority: a Low in review sorts after them all
    assert [x["ref"] for x in ui.motion_rows(ms, r)][-1] == "TD-100"
    ms[0]["progress"][0]["pr"] = None
    ms.append(member("g3", progress=[claim("TD-301")]))
    r["ledger"]["entries"].append({"id": "TD-050", "title": "high", "for_page": "pickable", "priority": "High"})
    ms.append(member("g4", progress=[claim("TD-050")]))
    rows = ui.motion_rows(ms, r)
    assert [x["ref"] for x in rows][:3] == ["TD-050", "TD-301", "TD-100"]  # High first, by reference
    s = ui.team_summary("grind", ms, {"/r/samscrape": r}, {}, now=NOW)
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    chips = re.findall(r'<span class="mprio[^"]*"[^>]*>[^<]*</span>', html)
    assert len(chips) == len(rows)  # one slot per row, filled or empty
    assert '<span class="mprio bseg p-high" title="High" aria-label="priority High">H</span>' in html
    assert '<span class="mprio bseg p-low" title="Low" aria-label="priority Low">L</span>' in html
    assert chips.count('<span class="mprio"></span>') == 3  # TD-200, TD-250, TD-999
    row = html[html.index('<div class="mrow">') :]
    assert row.index('class="phase') < row.index('class="mprio') < row.index("TD-050")  # between the two


def test_tds_in_motion_are_drawn_in_columns_with_every_cell():
    """TD-252 (design §4.5a *team card: TDs in motion*, **Columns**): each row carries the six cells
    in order — phase, priority, reference, title, holders, PR — an empty one where the row has no
    priority or no PR, and the facet the two widths the server set from its rows."""
    r = reading("/r/s")
    ms = [
        member("g1", progress=[claim("TD-301", pr=7), claim("TD-999")]),
        member("grinder-with-a-long-name", progress=[claim("TD-999")], unattended=False),
    ]
    s = ui.team_summary("grind", ms, {"/r/samscrape": r}, {}, now=NOW)
    assert s["ref_w"] == len("TD-301")
    assert s["who_w"] == ui.HOLDERS_WIDTH  # g1, grinder-with-a-long-name and the glyph: cut at the bound
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    assert s["pr_w"] == len("#7")
    assert f'<div class="facet fmotion" style="--ref-n: 6; --who-n: {ui.HOLDERS_WIDTH}; --pr-n: 2">' in html
    bare = ui.team_summary("grind", [member("g1", progress=[claim("TD-999")])], {"/r/samscrape": r}, {}, now=NOW)
    assert (bare["ref_w"], bare["who_w"], bare["pr_w"]) == (6, 2, 0)  # no PR in the facet: no room kept for one
    rows = html.split('<div class="mrow">')[1:]
    assert len(rows) == 2
    for row in rows:
        cells = [row.index(f'class="{c}') for c in ("mphase", "mprio", "mref", "mtitle", "mwho", 'mpr"')]
        assert cells == sorted(cells)
    by_ref = {("TD-999" if ">TD-999<" in row else "TD-301"): row for row in rows}
    assert '<span class="mpr"></span>' in by_ref["TD-999"] and '<span class="mprio"></span>' in by_ref["TD-999"]
    assert '<span class="mpr"><' in by_ref["TD-301"] and "#7" in by_ref["TD-301"]
    assert 'title="g1, grinder-with-a-long-name"' in by_ref["TD-999"]  # the whole list on hover
    css = (pathlib.Path(ui.__file__).parent / "static" / "app.css").read_text(encoding="utf-8")
    assert (
        ".mrow { --mch: .62em; display: grid; grid-template-columns: 58px 18px calc(var(--ref-n, 6) * var(--mch))"
        in css
    )
    assert ".mrow .mpr { grid-column: 4 / -1; }" in css  # a phone: the PR under the title


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


def test_the_doing_list_reads_a_short_age_in_columns_and_the_script_spells_it_the_same():
    """TD-232 slice 2 (design §4.5a *team card: Answer needed / Doing*, **Ages and columns**): one
    unit, *0m* under a minute and ahead of the clock (TD-418; *just now* until then), nothing for an
    unreadable instant; the doer's width is the server's, the longest name up to eighteen; a longer
    name is cut and whole in its tooltip; the script ages the cell once a minute in the same words."""
    from agentorc.ui.common import _short_age

    def ago(**kw):
        return _iso(NOW - timedelta(**kw))

    assert _short_age(ago(seconds=40), NOW) == "0m"
    assert _short_age(ago(minutes=5), NOW) == "5m" and _short_age(ago(minutes=59, seconds=59), NOW) == "59m"
    assert _short_age(ago(hours=1, minutes=50), NOW) == "1h" and _short_age(ago(hours=26), NOW) == "1d"
    assert _short_age(_iso(NOW + timedelta(minutes=3)), NOW) == "0m"  # ahead of the clock
    assert _short_age("half six", NOW) == "" and _short_age(None, NOW) == "" and _short_age(7, NOW) == ""
    long = "grinder-with-a-long-nm"  # twenty-two characters
    ms = [member("g1"), member("t1", name=long)]
    doing = {
        "grind": [
            {"id": "t1", "text": "answering grinder-ao-1's held PR #628", "at": ago(minutes=5)},
            {"id": "g1", "text": "reading", "at": "not a time"},
        ]
    }
    s = ui.team_summary("grind", ms, {}, doing, now=NOW)
    assert [(d["age"], d["name"]) for d in s["doing"]] == [("", "g1"), ("5m", long)]
    assert s["doing"][0]["at"] == ""  # an unreadable instant: the cell stays empty and the tick skips it
    assert s["doer_w"] == 18  # the longest name, cut at eighteen
    assert ui.team_summary("grind", [member("g1")], {}, {"grind": [{"id": "g1", "text": "x"}]}, now=NOW)["doer_w"] == 2
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    assert 'style="--doer-n: 18"' in html
    assert f'data-doing-at="{ago(minutes=5)}" title="{ago(minutes=5)}">5m</span>' in html
    assert f'<a class="name" href="/focus/t1" title="{long}">{long}</a>' in html  # whole in its tooltip
    assert '<span class="dage meta mono" data-doing-at="" title=""></span>' in html
    css = (ui.Path(ui.__file__).parent / "static" / "app.css").read_text()
    assert (
        "width: calc(var(--doer-n, 12) * 1ch)" in css
        and ".drow .dage { flex: none; width: 4ch; text-align: right; }" in css  # fits *59m*
    )
    js = (ui.Path(ui.__file__).parent / "static" / "app.js").read_text()
    fn = js[js.index("function fmtShortAge(iso)") :]
    fn = fn[: fn.index("\n  }\n")]
    assert fn  # the server's shape, unit for unit, read by running it: below
    ahead = _iso(NOW + timedelta(minutes=3))
    ages = _script_ages([ago(seconds=40), ago(minutes=5), ago(hours=2), ago(days=3), ahead, "x"])
    assert ages == ["0m", "5m", "2h", "3d", "0m", ""]  # ahead of the clock is *0m* too
    assert "setInterval(() => showDoingAges(), 60000)" in js  # once a minute, not the one-second tick
    assert '$$("[data-doing-at]"' in js and ".age[data-doing-at]" not in js


def test_the_doing_feed_keeps_its_scroll_across_the_summary_swap():
    # TD-205: the summary is swapped whole on every delta; the feed names itself for the swap to
    # read its scrollTop, and the position goes back once syncSummaries has shown the face again
    s = ui.team_summary("grind", [member("g2")], {}, {}, now=NOW)
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    assert '<div class="dfeed" data-keep-scroll="doing" style="--doer-n: 1">' in html
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
    assert "▾ 2 sessions" in head and "working" not in unfolded and "1 working" in head


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
    # nothing live: nothing summed, whatever the summaries hold — Needs you alone (TD-406)
    assert ui.rollup(groups) == {"live": False}
    assert s["drawn"] == ["repo", "motion", "face"]  # each of the three holds something
    # nothing here, nothing claimed, no doing row: no facet, and no summary drawn (TD-418)
    (quiet,) = ui.team_groups([member("q", state="exited", rank=1, slot={}, place="x")], (), {}, {})
    assert quiet["summary"]["motion"] == [] and quiet["summary"]["drawn"] == []


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
    # the repos whose PRs could not be read, one per line on the hover (TD-270)
    bad = ui.templates.get_template("rollup.html").render(ro={**ro, "prs_errors": ["a: x", "b: y"]}, person_needs=4)
    assert 'title="a: x\nb: y"' in bad
    assert ui.rollup(None) == ui.rollup([g for g in groups if not g["team"]]) == {"live": False}
    # a wound-down team beside them carries a summary now (TD-192), and adds nothing to the sums
    gone = [{**member("z1", state="exited", progress=[claim("TD-290")]), "team": "gone", "rank": 1, "slot": {}}]
    more = ui.team_groups([*a, *b, *gone], (), {"/r/samscrape": reading("/r/samscrape")}, {})
    assert next(g for g in more if g["team"] == "gone")["summary"]
    assert ui.rollup(more) == ro


def test_with_no_team_live_the_rollup_is_needs_you_alone():
    """§4.5a *Org: rollup* (TD-406): with no team live there is nothing to sum, and the Inbox's count
    and its overdue still need a place — the Needs you facet alone, *in the Inbox*, no team rows."""
    html = ui.templates.get_template("rollup.html").render(ro=ui.rollup([]), person_needs=7, person_overdue=2)
    assert 'class="rollup quiet"' in html and "Needs you" in html
    assert "data-inbox-needs>7<" in html and "data-inbox-overdue>2<" in html and 'href="/inbox"' in html
    assert "Agents (" not in html and "TDs in motion" not in html and "PRs in motion" not in html
    assert "answer needed" not in html and "asked you" not in html


def _live(team: str, *ms: dict) -> list[dict]:
    for m in ms:
        m.update(team=team, rank=1, slot={}, place="kmaster / samscrape", pill_word=m["state"])
    return list(ms)


def test_the_rollup_with_zero_one_and_two_live_teams():
    """§4.5a *Org: rollup* (TD-418, built by TD-428 slice 2): no team live — Needs you alone (TD-406);
    one — Agents and Needs you, that team's own facets saying the rest; two — all four. Agents' blurb is
    an *i* (a tooltip, nothing to press) and nothing is written under the pills; *answer needed* is
    drawn above 0 only."""
    render = ui.templates.get_template("rollup.html").render
    repos = {"/r/samscrape": reading("/r/samscrape")}
    zero = render(ro=ui.rollup(ui.team_groups([], (), repos, {})), person_needs=1)
    assert 'class="rollup quiet"' in zero and "Agents (" not in zero and "answer needed" not in zero
    one = ui.team_groups(_live("grind", member("g1", progress=[claim("TD-301")]), member("g2")), (), repos, {})
    ro = ui.rollup(one)
    assert ro["lone"] and ro["answer_needed"] == 0
    html = render(ro=ro, person_needs=1)
    assert 'class="rollup lone"' in html and "Agents (2)" in html and "Needs you" in html
    assert "TDs in motion" not in html and "PRs in motion" not in html
    assert "answer needed" not in html  # 0: not drawn
    tip = "in urgency order, as the cards sort · a pill filters the page to that state"
    assert f'<span class="tipmark" tabindex="0" role="img" aria-label="{tip}" title="{tip}">i</span>' in html
    assert f'<div class="meta">{tip}</div>' not in html and "helpmark" not in html  # no panel to open
    perm = {"kind": "permission", "text": "Bash x", "tool_use_id": "t1"}
    two = ui.team_groups(
        [
            *_live("grind", member("g1", progress=[claim("TD-301")])),
            *_live("other", member("h1", state="needs-you", pending=perm)),
        ],
        (),
        repos,
        {},
    )
    ro2 = ui.rollup(two)
    html2 = render(ro=ro2, person_needs=1)
    assert not ro2["lone"] and 'class="rollup"' in html2
    assert "Agents (2)" in html2 and "TDs in motion (1)" in html2 and "PRs in motion (1)" in html2
    assert '>1</b> <a href="#tsum-other"' in html2 and "answer needed</a>" in html2
    css = (ui.Path(ui.__file__).parent / "static" / "app.css").read_text()
    assert ".rollup.lone { grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); }" in css


def test_a_stopped_team_draws_only_the_facets_that_hold_something():
    """§4.5a *team card: summary* (TD-418, built by TD-428 slice 2): a team with nothing live draws a
    facet only where it holds something — no *no repo here*, no *nothing claimed*, no empty Doing — and
    no summary at all where none does; a live team draws all three, empty or not."""
    repos = {"/r/samscrape": reading("/r/samscrape")}
    render = ui.templates.get_template("team_summary.html").render

    def stopped(*ms: dict, doing: dict | None = None) -> dict:
        for m in ms:
            m.update(state="exited", rank=1, slot={}, place="x")
        (g,) = [g for g in ui.team_groups(list(ms), (), repos, doing or {}) if g["team"] == "grind"]
        return g

    # two: a repo here and a claim, no doing row
    g = stopped(member("a", progress=[claim("TD-301")]))
    assert g["summary"]["drawn"] == ["repo", "motion"]
    html = render(g=g)
    assert 'class="facet frepo"' in html and 'class="facet fmotion"' in html and "fface" not in html
    # one: the doing log alone, no repo here and nothing claimed
    g = stopped(member("b", repo="/elsewhere"), doing={"grind": [{"id": "b", "text": "x", "at": _iso(NOW)}]})
    assert g["summary"]["drawn"] == ["face"]
    html = render(g=g)
    assert "fface" in html and "frepo" not in html and "no repo here" not in html and "nothing claimed" not in html
    # none: no summary drawn, on the page or in a delta's groups
    g = stopped(member("c", repo="/elsewhere"))
    assert g["summary"]["drawn"] == []
    page = ui.templates.get_template("org.html").render(
        sessions=g["members"], groups=[g], counts=dict.fromkeys(("needs-you", "limited", "stalled?"), 0),
        strip={"teams": [{"name": "grind"}], "source": "", "notes": []}, host="h", active="Org",
        agent_down=False, volatile=False, usage={},
    )  # fmt: skip
    assert 'class="tsum"' not in page
    # a live team draws all three, however empty
    (live,) = [x for x in ui.team_groups(_live("grind", member("d", repo="/elsewhere")), (), repos, {}) if x["team"]]
    assert live["summary"]["drawn"] == ["repo", "motion", "face"] and "no repo here" in render(g=live)


def test_the_facets_heads_and_bars_are_one_height_and_the_summary_is_sized_by_what_it_holds():
    """§4.5a *Org: rollup*, *team card: summary* and *Repo facet* (TD-418): every facet head one height
    (the window picker's), the bars and blocks one height so they line up, a label that never wraps
    with the selector dropping to its own line at the right, the summary in 1.15 : 0.85 : 1.2, and
    Doing's head the word alone."""
    css = (ui.Path(ui.__file__).parent / "static" / "app.css").read_text()
    assert ".facet .fhead { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-height: 26px; }" in css
    assert "white-space: nowrap; }\n.facet .fhead > .seg { margin-left: auto; }" in css
    assert ".bar, .blocks { display: flex; gap: 2px; height: 26px; }" in css and "min-height: 30px" not in css
    assert "grid-template-columns: minmax(0, 1.15fr) minmax(0, .85fr) minmax(0, 1.2fr);" in css
    s = ui.team_summary("grind", [member("g1")], {}, {"grind": [{"id": "g1", "text": "x", "at": _iso(NOW)}]}, now=NOW)
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    assert '<span class="kind">Doing</span><span class="grow"></span>' in html
    assert "the team's ao doing calls" not in html


def test_every_card_carries_the_word_the_state_filter_matches():
    assert ui.view({"id": "a", "name": "a", "state": "needs-you"})["pill_word"] == "needs-you"
    assert ui.view({"id": "a", "name": "a", "state": "stalled?"})["pill_word"] == "stalled"
    seat = ui.view({"id": "t", "name": "t", "state": "exited"}, seats={"t": "comes on the next question"})
    assert seat["pill_word"] == "on-call"
    html = ui.templates.get_template("card.html").render(s=ui.view({"id": "a", "name": "a", "state": "working"}))
    assert 'data-pill="working"' in html
    js = (ui.Path(ui.__file__).parent / "static" / "app.js").read_text()
    assert 'low.startsWith("state:")' in js and "c.pill" in js  # the words' behaviour: test_ui_org_chrome.py


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


def test_a_team_over_its_line_says_so_on_its_header_and_only_while_live():
    """§4.5a team card **over its line** note (§6 *Balance*, TD-239 slice 4): the home's mark, from the
    `repos` reading, in its own numbers on the header's second line; display only, and gone with the mark."""
    ms = [member("g1", role_label="Grinder"), member("g2", state="exited")]
    for m in ms:
        m.update(rank=1, slot={"text": "", "caption": ""}, place="kmaster / samscrape")
    mark = {"since": "", "repo": "/r/samscrape", "crossed": [{"line": "prs", "value": 9, "limit": 8}]}
    over = {"/r/samscrape": {**reading("/r/samscrape"), "balance": {ms[0]["team"]: mark}}}
    (g,) = ui.team_groups(ms, (), over, {})
    assert g["balance_note"] == "over its line: 9 open PRs, line 8"
    head = ui.templates.get_template("group_head.html").render(g=g)
    assert '<div class="meta overline" title="its members take no new claim until it clears · Settings">' in head
    assert "over its line: 9 open PRs, line 8</div>" in head
    (clear,) = ui.team_groups(ms, (), {"/r/samscrape": reading("/r/samscrape")}, {})
    assert clear["balance_note"] == ""
    assert "overline" not in ui.templates.get_template("group_head.html").render(g=clear)
    (dead,) = ui.team_groups([{**m, "state": "exited"} for m in ms], (), over, {})
    assert dead["balance_note"] == ""
    # a mark with no repo is in no checkout's reading: the home's `host` read carries it (TD-330)
    unhomed = {**mark, "repo": ""}
    (g,) = ui.team_groups(ms, (), {"/r/samscrape": reading("/r/samscrape")}, {}, balance={ms[0]["team"]: unhomed})
    assert g["balance_note"] == "over its line: 9 open PRs, line 8"


def test_the_repo_pages_rows_keep_their_id_tags_and_columns():
    """TD-296 #16: a debt row with a long title wrapped its id (*TD-* / *122*) and cut its tags to
    *Hi… · pa…*, and at 700 px a long Doing line dropped below its row. The id and the tags keep
    their width, the title gives; the narrow page's wrap leaves a Doing row's columns standing."""
    css = (ui.Path(ui.__file__).parent / "static" / "app.css").read_text()
    assert ".repopage .rrow > .mono.strong, .repopage .rrow > .meta { flex: none; white-space: nowrap; }" in css
    assert "@media (max-width: 720px) { .repopage .rrow:not(.drow) { flex-wrap: wrap; } }" in css


def test_a_live_teams_stop_time_is_on_its_header_once(monkeypatch):
    """§4.5a team card **stops** note (§6 *Team stop time*, TD-337): the team's own `until`, from the
    settings read, on a live team's header by the card's formatter; a member's compact line says
    *stops <t>* only where its own time differs, and *wrapping up* once asked; nothing on a team with
    nothing live, and nothing once the time is cleared or has passed."""
    from agentorc.ui import uiconf
    from sessionorc.models import stop_note

    later = (datetime.now(UTC) + timedelta(hours=2)).replace(second=30, microsecond=0)
    team_at, own_at = _iso(later), _iso(later + timedelta(hours=1))
    ms = [member("g1", role_label="Grinder", run_until=team_at), member("g2", role_label="Grinder", run_until=own_at)]
    for m in ms:
        m.update(rank=1, slot={"text": "", "caption": ""}, place="kmaster / samscrape")
    team = ms[0]["team"]
    monkeypatch.setattr(uiconf, "_read", {"person": {}, "migrate": [], "teams": {}})
    uiconf.set_read({"teams": {team: {"until": team_at, "passed": False}}})
    (g,) = ui.team_groups(ms, (), {}, {})
    assert g["stops_note"] == stop_note({"run_until": team_at}) and g["stops_note"].startswith("stops ")
    head = ui.templates.get_template("group_head.html").render(g=g)
    assert (
        f'<div class="meta stopsnote" title="every member and seat stops here · Settings">{g["stops_note"]}</div>'
        in head
    )
    lines = {m["id"]: m["compact_line"] for m in g["members"]}
    assert lines["g1"] == "Grinder"  # the team's own time: on the header, not on the member
    second_off = {**ms[0], "run_until": _iso(later - timedelta(seconds=1))}  # reads the same: not drawn
    assert ui.compact_line(second_off) == "Grinder"
    assert lines["g2"] == f"Grinder · {stop_note({'run_until': own_at})}"  # its own differs
    ms[0]["wrapup_sent_at"] = _iso(datetime.now(UTC))
    (g,) = ui.team_groups(ms, (), {}, {})
    assert {m["id"]: m["compact_line"] for m in g["members"]}["g1"] == "Grinder · wrapping up"
    # nothing live: the slot is the *starts* note's
    (dead,) = ui.team_groups([{**m, "state": "exited"} for m in ms], (), {}, {})
    assert dead["stops_note"] == "" and "stopsnote" not in ui.templates.get_template("group_head.html").render(g=dead)
    # cleared, passed, or a time the read marks passed: none, and every member's own time is its own again
    for teams in (
        {},
        {team: {"until": _iso(datetime.now(UTC) - timedelta(hours=1))}},
        {team: {"until": team_at, "passed": True}},
    ):
        uiconf.set_read({"teams": teams})
        (g,) = ui.team_groups(ms, (), {}, {})
        assert g["stops_note"] == "" and {m["id"]: m["compact_line"] for m in g["members"]}["g1"].startswith(
            "Grinder · stops "
        )


def _ask(mid: str, frm: str, kind: str = "ask", minutes: int = 10, **kw) -> dict:
    """A *Needs you* mail row as `inbox_sections` hands it on (TD-350)."""
    return {
        "id": mid,
        "from": frm,
        "kind": kind,
        "text": f"Which branch for {mid}?\nthe reading",
        "at": _iso(NOW - timedelta(minutes=minutes)),
        **kw,
    }


def test_a_members_open_ask_to_the_person_is_the_teams_asked_you_line():
    """TD-354 (§4.5a *team card: Answer needed / Doing*, TD-353): an idle member's open `ask` to the
    person is the team's *asked you · n · <age>* line — one link to the oldest's Inbox row, the
    members and first lines its tooltip — drawn in either face, and the rollup's *asked you*; it is
    not a block, not in Answer needed (n), and neither opens nor tints the facet."""
    ms = [member("g1", state="idle"), member("g2")]
    needs = [_ask("m-1", "g1"), _ask("m-0", "g2", minutes=40)]
    s = ui.team_summary("grind", ms, {}, {}, now=NOW, needs=needs)
    assert s["face"] == "doing" and s["answers"] == [] and s["answer_key"] == ""
    assert s["asked"] == {
        "n": 2,
        "id": "m-0",
        "at": needs[1]["at"],
        "age": "40m",
        "who": [{"name": "g2", "text": "Which branch for m-0?"}, {"name": "g1", "text": "Which branch for m-1?"}],
    }
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    assert html.count('href="/inbox?row=m-0"') == 2, "one line under each face's head"
    assert "asked you · 2 · " in html and 'title="g2: Which branch for m-0?\ng1: Which branch for m-1?"' in html
    assert "the reading" not in html and "Answer needed (0)" in html and 'href="/inbox/m-0"' not in html
    for m in ms:
        m.update(rank=1, slot={}, place="kmaster / samscrape", pill_word=m["state"])
    ro = ui.rollup(ui.team_groups(ms, (), {}, {}, needs=needs))
    assert ro["answer_needed"] == 0 and ro["asked"] == 2 and ro["asked_team"] == "grind"
    assert ro["asked_at"] == needs[1]["at"], "the oldest ask's age"
    out = ui.templates.get_template("rollup.html").render(ro=ro, person_needs=2)
    assert '<a href="#tsum-grind"' in out and ">asked you</a>" in out and "or whose ask to you is open" not in out
    # at 0 the row is hidden; nothing open, no line on the card
    zero = ui.templates.get_template("rollup.html").render(ro={**ro, "asked": 0}, person_needs=0)
    assert ">asked you</a>" not in zero
    assert ui.team_summary("grind", ms, {}, {}, now=NOW)["asked"] is None


def test_a_pane_prompt_is_still_answer_needed_beside_the_line():
    """TD-354: a member's pane question is still **Answer needed (1)**, opening and tinting the facet,
    with the team's open ask beside it as the line, counted only there."""
    ms = [member("g1", state="needs-you", pending={"kind": "question", "text": "which?"}), member("g2", state="idle")]
    s = ui.team_summary("grind", ms, {}, {}, now=NOW, needs=[_ask("m-1", "g2")])
    assert s["face"] == "answer" and [a["kind"] for a in s["answers"]] == ["question"] and s["asked"]["n"] == 1
    assert s["answer_key"] == "g1:question"
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    assert "Answer needed (1)" in html and "asked you · 1 · " in html


def test_only_an_open_ask_from_the_team_is_on_the_line():
    """Closed, a `steer`, a state row, a board row, or a sender outside the team: not on the line. Two
    asks on one member: both, oldest first; a node's `id@host` sender is that record, and no other.
    Answer needed holds the pane's question alone, whatever the asks."""
    ms = [member("g1", state="needs-you", pending={"kind": "question", "text": "which?"}), member("g2", state="idle")]

    def asks(needs):
        return [a["id"] for a in ui.ask_blocks(ms, needs, NOW)]

    assert asks([_ask("m-1", "g2", closed_reason="replied")]) == []
    assert asks([_ask("m-1", "g2", kind="steer")]) == []
    assert asks([{"id": "g2:question", "row": "question", "from": "g2", "kind": "ask"}]) == []
    assert asks([_ask("m-1", "h9")]) == []
    assert asks([_ask("m-2", "g2", minutes=1), _ask("m-1", "g2", minutes=30)]) == ["m-1", "m-2"]
    # a node's record is `id@host` in the reader's form on both sides (§4.4a): matched whole, so a
    # session of the same id on another host is not this member
    ms.append(member("g3@node", state="idle"))
    assert asks([_ask("m-3", "g3@node"), _ask("m-4", "g2@other")]) == ["m-3"]
    assert [(a["kind"], a["id"]) for a in ui.answer_blocks(ms)] == [("question", "g1")]
    assert ui.asked_line(ms, [], NOW) is None


def test_the_lanes_line_says_what_the_teams_lanes_take_and_who_is_out_of_work():
    """§4.5a *team card: Repo facet*'s lanes line (§4.4 *In a team's lanes*, TD-361): under the
    legend, what the team's lanes take and the rest by owner, each count a link; the two segments'
    hovers say the same; a member out of work with unheld work in its lane tinted, its ids on hover,
    its name a Focus link; a team with no lane on any record draws the bars alone."""
    from agentorc.ui import app as ui

    def e(i, page, owner, kind="build"):
        return {"id": i, "title": i, "for_page": page, "owner": owner, "kind": kind, "pickable": "yes"}

    entries = [e(f"TD-{100 + i}", "pickable", "anchor") for i in range(20)]
    entries += [e(f"TD-{200 + i}", "pickable", "dev-cadence") for i in range(4)]
    entries += [e("TD-300", "design-first", "designer", "design-first")] + [
        e("TD-301", "design-first", "x", "design-first")
    ]
    r = reading("/r/samscrape")
    r["ledger"].update(entries=entries, by_kind={"pickable": 24, "design-first": 2, "for-you": 0, "other": 0})
    oow = {"at": _iso(NOW), "why": "nothing"}
    lane = ["free-pick", "owner:grinder"]
    members = [
        member("g1", "idle", lane=lane, out_of_work=oow, name="grinder-ao-1"),
        member("d1", "idle", lane=["design-first", "owner:designer"]),
    ]
    s = ui.team_summary("grind", members, {"/r/samscrape": r}, {}, now=NOW)
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    line = html[html.index('class="meta laneline"') :].split("</div>")[0]
    assert "in grind's lanes: " in line and ">0 pickable</a>, " in line and ">1 design-first</a>" in line
    assert "· the other 24 pickable: " in line and ">anchor 20</a>, " in line and ">dev-cadence 4</a>" in line
    assert 'href="/repo/samscrape#debt-pickable"' in line and "lanewarn" not in line
    assert 'title="24 pickable · 0 in grind&#39;s lanes · anchor 20, dev-cadence 4"' in html
    assert 'title="2 design-first · 1 in grind&#39;s lanes · 1 wait on a build"' in html
    entries.append(e("TD-355", "pickable", "grinder"))
    s = ui.team_summary("grind", members, {"/r/samscrape": r}, {}, now=NOW)
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    warn = '<span class="lanewarn" title="TD-355 — in its lane, held by nobody">· '
    assert warn + '<a class="strong" href="/focus/g1">grinder-ao-1</a> out of work with 1 in its lane</span>' in html
    # a kind the Repo page draws no list for (none of it open): its count is not a link to nothing
    lanes = {"pickable": [], "design_first": [], "rest": [], "design_first_rest": [], "out_of_work": []}
    ln = ui.lanes_line("grind", lanes, {"pickable": 0, "design-first": 2})
    line = ui.templates.get_template("lanes_line.html").render(ln=ln, url="/repo/samscrape")
    assert '<span class="strong">0 pickable</span>' in line and "#debt-pickable" not in line
    assert '<a class="strong" href="/repo/samscrape#debt-design-first">0 design-first</a>' in line
    # a team with no lane on any record: the bars alone
    s = ui.team_summary("grind", [member("g1"), member("g2")], {"/r/samscrape": r}, {}, now=NOW)
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    assert "laneline" not in html and 'title="24 pickable"' in html


def _script_ages(stamps: list[str]) -> list[str]:
    """`AO.fmtShortAge` run in node over `stamps`, against the real clock: `NOW` is the test's, so each
    stamp is moved by the difference first."""
    import json
    import shutil
    import subprocess
    import tempfile

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the Doing ages are JavaScript too, and nothing else runs it")
    shift = datetime.now(UTC) - NOW
    moved = [_iso(datetime.fromisoformat(t.replace("Z", "+00:00")) + shift) if t.endswith("Z") else t for t in stamps]
    probe = ui.Path(tempfile.mkdtemp()) / "age_probe.js"
    probe.write_text(
        'const fs = require("fs"); const noop = () => {};'
        " const el = () => ({ dataset: {}, style: {}, addEventListener: noop, appendChild: noop,"
        " classList: { toggle: noop, add: noop, remove: noop, contains: () => false },"
        " querySelector: () => null, querySelectorAll: () => [] });"
        " global.window = {}; global.document = { documentElement: el(), body: el(), querySelector: () => null,"
        " querySelectorAll: () => [], addEventListener: noop, createElement: el };"
        " global.localStorage = { getItem: () => null, setItem: noop }; global.matchMedia = () => ({ matches: false });"
        " global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;"
        ' global.location = { pathname: "/", protocol: "http:", host: "x" };'
        ' global.fetch = () => Promise.reject(new Error("no calls"));'
        ' eval(fs.readFileSync(process.argv[2], "utf8"));'
        " console.log(JSON.stringify(JSON.parse(process.argv[3]).map((t) => window.AO.fmtShortAge(t))));"
    )
    app = ui.Path(ui.__file__).parent / "static" / "app.js"
    out = subprocess.run([node, str(probe), str(app), json.dumps(moved)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)
