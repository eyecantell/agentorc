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


LEDGER_PROBE = """
const fs = require("fs");
const noop = () => {};
const el = (o) => Object.assign({
  dataset: {}, style: {}, hidden: false, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [], contains: () => false,
}, o);
const document = { documentElement: el(), body: el(), activeElement: null,
  querySelector: () => null, querySelectorAll: () => [], addEventListener: noop, createElement: () => el() };
const window = {};
const kept = new Map();
global.window = window; global.document = document;
global.localStorage = { getItem: (k) => (kept.has(k) ? kept.get(k) : null), setItem: (k, v) => kept.set(k, String(v)) };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/inbox", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO;
// the fold as `ledger_for_you.html` draws it: two repos' groups, each `.ledgerrow` with its team and find words
const row = (team, find) => el({ dataset: { team, find } });
const a1 = row("ao-grind", "td-156 entry 156 agentorc ao-grind"), a2 = row("", "td-002 entry 2 agentorc");
const s1 = row("sam-grind", "td-400 entry 400 samscrape sam-grind");
const ga = el({ querySelectorAll: (s) => (s === ".ledgerrow" ? [a1, a2] : []) });
const gs = el({ querySelectorAll: (s) => (s === ".ledgerrow" ? [s1] : []) });
const all = { ".inboxpage .ledgerrow": [a1, a2, s1], ".inboxpage .ledgergroup": [ga, gs] };
const root = el({ querySelectorAll: (s) => all[s] || [] });
const shown = () => [a1, a2, s1, ga, gs].map((x) => !x.hidden);
const out = {};
AO.ledgerFilter(root, [], []); out.none = shown();
AO.ledgerFilter(root, ["ao-grind"], []); out.team = shown();
AO.ledgerFilter(root, ["none"], []); out.no_team = shown();
AO.ledgerFilter(root, [], AO.findWords("samscrape")); out.find = shown();
AO.ledgerFilter(root, ["ao-grind"], AO.findWords("entry 2")); out.both = shown();
AO.ledgerFilter(root, [], []); out.cleared = shown();
// the poll's swap: new markup is put in and remembered; the same markup again is left alone
const lf = el({ innerHTML: "drawn by the page" });
out.swap_first = AO.ledgerSwap(lf, "<details>v1</details>"); out.html_first = lf.innerHTML;
lf.innerHTML = "the person's open fold";
out.swap_same = AO.ledgerSwap(lf, "<details>v1</details>"); out.html_same = lf.innerHTML;
out.swap_new = AO.ledgerSwap(lf, "<details>v2</details>"); out.html_new = lf.innerHTML;
out.swap_missing = [AO.ledgerSwap(null, "x"), AO.ledgerSwap(lf, undefined)];
// the fold: closed by default, opened by its toggle and remembered, so the next draw opens it
let toggle = null;
const d = el({ open: true, addEventListener: (ev, f) => { if (ev === "toggle") toggle = f; } });
AO.ledgerFold(d); out.fold_default = d.open;
d.open = true; toggle();
const d2 = el({ open: false }); AO.ledgerFold(d2); out.fold_remembered = d2.open;
d.open = false; toggle(); const d3 = el({ open: true }); AO.ledgerFold(d3); out.fold_closed_again = d3.open;
console.log(JSON.stringify(out));
"""


def test_the_folds_rows_follow_the_teams_picks_and_the_find_the_poll_and_the_remembered_open():
    """§4.5 screen 6 *The ledger's entries that wait on you* (TD-374): the fold's client side, run
    under node — a *Teams* pick and a find word hide the rows that do not match, and a repo's group
    with none left; the poll puts new markup back and leaves the same markup alone; the fold opens
    as this browser left it."""
    import json
    import pathlib
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rules are JavaScript, and nothing else runs them")
    probe = pathlib.Path(tempfile.mkdtemp()) / "ledger_probe.js"
    probe.write_text(LEDGER_PROBE)
    app_js = pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui" / "static" / "app.js"
    out = subprocess.run([node, str(probe), str(app_js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    # [ao-grind row, no-team row, sam-grind row, agentorc group, samscrape group]
    assert got["none"] == [True, True, True, True, True]
    assert got["team"] == [True, False, False, True, False]  # samscrape's name goes with its last row
    assert got["no_team"] == [False, True, False, True, False]  # a row with no team is *no team*'s
    assert got["find"] == [False, False, True, False, True]
    assert got["both"] == [False, False, False, False, False]  # the pick and the find compose
    assert got["cleared"] == [True, True, True, True, True]
    assert got["swap_first"] is True and got["html_first"] == "<details>v1</details>"
    assert got["swap_same"] is False and got["html_same"] == "the person's open fold"
    assert got["swap_new"] is True and got["html_new"] == "<details>v2</details>"
    assert got["swap_missing"] == [False, False]
    assert got["fold_default"] is False  # closed by default
    assert got["fold_remembered"] is True and got["fold_closed_again"] is False
    # …and the page calls them where the rules apply
    js = app_js.read_text()
    assert "AO.ledgerFilter(document, rail.team, words);" in js
    assert 'if (AO.ledgerSwap($("#ledgerforyou"), got.html.ledger)) ledgerFold();' in js
    assert "AO.ledgerFold(d);" in js
