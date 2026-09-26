"""TD-176 slice 3, design §4.5 screen 1 *The Org, team-first*: a live team's card carries a summary
of three facets — Repo, TDs in motion, Answer needed / Doing — and its members are compact cards."""

from __future__ import annotations

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
                {"id": "TD-310", "title": "what the composer says", "for_page": "design-first", "priority": "medium"},
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
    assert "· 2 sessions" in head and "working" not in head
    # a team with nothing live: no summary, full cards
    dead = [{**m, "state": "exited"} for m in ms]
    for m in dead:
        m.pop("compact", None)
    (g,) = ui.team_groups(dead, (), {}, {})
    assert g["summary"] is None and not any(m.get("compact") for m in g["members"])


def test_compact_in_marks_a_delta_by_the_fleet():
    v = member("g1")
    assert ui.compact_in(dict(v), [member("x", state="exited")]).get("compact") is None
    assert ui.compact_in(dict(v), [member("x")])["compact"]
    assert ui.compact_in({**v, "team": ""}, [member("x")]).get("compact") is None
