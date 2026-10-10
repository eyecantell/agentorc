"""TD-428 slice 6, design §4.4 *Repo facts* (TD-418): the page's seven kinds — one entry of each read
from a ledger, drawn on the team card's kind bar and in the Repo page's lists, counted and listed by
`ao repo` by §4.7's mapping, and counted in a team's lanes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from test_ui_repo_page import client, fake
from test_ui_team_summary import NOW, member

from agentorc.ui import app as ui
from sessionorc import ledger

LEDGER = """# Technical debt

## TD-010: a build

**Priority:** Medium
**Owner:** grinder
**Kind:** build

## TD-011: a question

**Priority:** Medium
**Owner:** designer
**Kind:** design-first

## TD-012: the person's

**Priority:** Medium
**Owner:** paul

## TD-013: a check whose build is live

**Priority:** Medium
**Owner:** grinder
**Kind:** live-check #5

## TD-014: a check whose build is not

**Priority:** Medium
**Owner:** grinder
**Kind:** live-check #7

## TD-015: a check on the anchor's decision

**Priority:** Medium
**Owner:** anchor
**Kind:** live-check #5
**Blocked by:** decision (anchor)

## TD-016: a build on another

**Priority:** Medium
**Owner:** grinder
**Kind:** build
**Blocked by:** TD-010

## TD-017: the anchor's call

**Priority:** Medium
**Owner:** anchor
**Kind:** evaluation

## TD-018: on nobody we know

**Priority:** Medium
**Owner:** grinder
**Blocked by:** decision (techlead)
"""

KIND_OF = {
    "TD-010": "pickable",
    "TD-011": "design",
    "TD-012": "for-you",
    "TD-013": "live-check",
    "TD-014": "live-check",
    "TD-015": "live-check",
    "TD-016": "blocked",
    "TD-017": "evaluation",
    "TD-018": "other",
}
FLAG = "TD-018: fits no page kind — check its Owner, Kind and Blocked by"


def _entries() -> list[dict]:
    return ledger.entries(LEDGER, live=lambda n: n == 5)


def _reading(root: str) -> dict:
    got = _entries()
    return {
        "name": "samscrape",
        "root": root,
        "remote": "git@github.com:eye/samscrape.git",
        "ledger": {
            "path": "docs/technical_debt.md",
            "entries": got,
            "by_priority": {"high": 0, "medium": len(got), "low": 0},
            "by_kind": {k: sum(1 for e in got if e["for_page"] == k) for k in ledger.KINDS},
        },
        "prs": {"open": []},
    }


def test_one_entry_of_each_kind_and_the_residue_flagged():
    got = _entries()
    assert {e["id"]: e["for_page"] for e in got} == KIND_OF
    assert ledger.flags(got) == [FLAG]


def test_the_kind_bar_draws_the_seven_kinds_in_order_with_a_legend():
    r = _reading("/r/samscrape")
    s = ui.team_summary("grind", [member("g1", "idle")], {"/r/samscrape": r}, {}, now=NOW)
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    bar = html[html.index('aria-label="open entries by kind"') :]
    bar = bar[: bar.index("</div>")]
    keys = ["pickable", "design", "for-you", "live-check", "blocked", "evaluation", "other"]
    assert [k for k in keys if f'class="bseg k-{k}"' in bar] == keys
    assert bar.index("k-pickable") < bar.index("k-design") < bar.index("k-live-check") < bar.index("k-other")
    assert 'href="/repo/samscrape#debt-live-check"' in bar and 'title="3 live check"' in bar
    legend = html[html.index('<div class="legend">') :]
    legend = legend[: legend.index("</div>")]
    assert [x.split("</i>")[1].split("<")[0] for x in legend.split("<i ")[1:]] == [
        "pickable",
        "design",
        "for you",
        "live check",
        "blocked",
        "evaluation",
        "other",
    ]


def test_a_priority_segment_keeps_its_word_apart_so_a_narrow_one_drops_it():
    """TD-506: the word is its own element, which a segment too narrow for it wraps out of sight (the CSS
    below), then the count; the hover keeps both. The browser's half is the PR's UI check."""
    r = _reading("/r/samscrape")
    r["ledger"]["by_priority"] = {"high": 4, "medium": 26, "low": 34}
    s = ui.team_summary("grind", [member("g1", "idle")], {"/r/samscrape": r}, {}, now=NOW)
    html = ui.templates.get_template("team_summary.html").render(g={"team": "grind", "summary": s})
    bar = html[html.index('aria-label="open entries by priority"') :]
    bar = bar[: bar.index("</div>")]
    assert 'title="4 High">4<span class="bw"> High</span></a>' in bar
    assert 'title="26 Medium">26<span class="bw"> Medium</span></a>' in bar
    css = (Path(ui.__file__).parent / "static" / "app.css").read_text(encoding="utf-8")
    assert ".bar .bseg { flex-wrap: wrap; align-content: flex-start; }" in css
    assert '.bar .bseg::before { content: ""; height: 100%; }' in css


def test_the_repo_page_draws_a_list_per_kind_and_flags_the_other(tmp_path, monkeypatch):
    root = str(tmp_path / "samscrape")
    html = client(monkeypatch, tmp_path, fake({root: _reading(root)}, [])).get("/repo/samscrape").text
    for key, label in ui.LEDGER_LISTS:
        n = sum(1 for k in KIND_OF.values() if k == key)
        assert f'id="debt-{key}">{label} · {n}</div>' in html, key
    for tid, key in KIND_OF.items():
        assert f'id="{tid}" data-list="{key}"' in html, tid
    row = html[html.index('id="TD-018"') :]
    assert (
        '<span class="meta flag">· fits no page kind — check its Owner, Kind and Blocked by</span>'
        in row[: row.index("</div>")]
    )
    row = html[html.index('id="TD-017"') :]
    assert "fits no page kind" not in row[: row.index("</div>")]


def test_ao_repo_reads_the_seven_counts_and_lists_by_the_mapping(tmp_path, monkeypatch, capsys):
    """§4.7: pickable rows are *pickable* plus a live check nothing blocks whose build is live; design
    rows *design*; the live-check line a live check nothing blocks whose build is not live; a live
    check blocked by a decision alone is counted and listed nowhere; the *other* flag under the line."""
    from agentorc import cli

    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)  # `ao repo` finds the checkout
    reading = {str(repo): {**_reading(str(repo)), "name": "r"}}
    monkeypatch.setattr(
        cli, "call_sync", lambda rpc, **kw: {"repos": reading, "list": [], "doing_log": {}, "inbox": {}}[rpc]
    )
    monkeypatch.chdir(repo)
    assert cli.main(["repo"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].endswith(
        "9 open entries: 1 pickable, 1 design, 1 for you, 3 live check, 1 blocked, 1 evaluation, 1 other · read ? ago"
    )
    assert lines[1] == f"  {FLAG}"
    rows = [ln.split()[:2] for ln in lines[2:]]
    assert rows == [["pickable", "TD-010"], ["pickable", "TD-013"], ["design", "TD-011"], ["live-check", "TD-014"]]
    assert cli.main(["--json", "repo"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["ledger"]["by_kind"]["design"] == 1


def test_a_live_check_whose_build_is_live_is_in_a_free_pick_lane():
    """§4.4 *In a team's lanes*: the lanes count *pickable*, *design* and *live check*; a live check is
    taken by a `free-pick` lane once its build is live, and never one whose build is not."""
    records = [{"id": "g1", "name": "g1", "lane": ["free-pick", "owner:grinder"], "state": "idle"}]
    got = ledger.in_lanes(_entries(), records)
    assert got["pickable"] == ["TD-010"] and got["live_check"] == ["TD-013"] and got["design"] == []
    assert got["members"][0]["ids"] == ["TD-010", "TD-013"]
    designer = ledger.in_lanes(_entries(), [{"id": "d1", "lane": ["design-first"], "state": "idle"}])
    assert designer["design"] == ["TD-011"] and designer["live_check"] == []
    anchor = ledger.in_lanes(_entries(), [{"id": "a1", "lane": ["anchor"], "state": "idle"}])
    # the anchor seat takes its evaluation and the check on its decision; the team sum keeps to the three kinds
    assert anchor["members"][0]["ids"] == ["TD-015", "TD-017"] and anchor["live_check"] == ["TD-015"]
