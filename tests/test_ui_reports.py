"""The Focus side panel's **Reports** by state (design §4.5a *Focus side panel → Reports*, TD-143's
design, TD-150 slice 1): the four groups, a claim with a PR drawn *claimed · in review #n*, the
heading's **i** mark, and **Drop** behind the panel's more ▾ on in-progress claims only."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest
from fastapi.testclient import TestClient

UI = pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui"

GROUPS_PROBE = """
const fs = require("fs");
const noop = () => {};
const el = (o) => Object.assign({
  dataset: {}, style: {}, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [], contains: () => false,
}, o);
const document = { documentElement: el(), body: el(), activeElement: null,
  querySelector: () => null, querySelectorAll: () => [], addEventListener: noop, createElement: () => el() };
const window = {};
global.window = window; global.document = document;
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const g = window.AO.reportGroups([
  { ref: "TD-127", status: "claimed", source: "declared", review_pr: 532 },
  { ref: "TD-128", status: "claimed", source: "declared", pr: 540 },
  { ref: "TD-129", status: "claimed", source: "declared" },
  { ref: "TD-130", status: "done", source: "declared", pr: 541 },
  { ref: "TD-131", status: "dropped", source: "declared" },
  { ref: "TD-132", status: "claimed", source: "derived" },
  { ref: "TD-133", status: "claimed", source: "derived", pr: 542 },
]);
const refs = (xs) => xs.map((p) => [p.ref, p.review_pr || null, p.source]);
console.log(JSON.stringify({ progress: refs(g.progress), review: refs(g.review), done: refs(g.done),
  dropped: refs(g.dropped), none: window.AO.reportGroups(null) }));
"""


@pytest.mark.unit
def test_a_claim_with_a_pr_is_in_review_and_one_without_is_in_progress():
    """TD-143: a claim whose PR is known — its own `pr`, or the branch's `review_pr` the record
    carries (slice 3) — is *in review*; one with none is *in progress*."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the panel's rule is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "groups_probe.js"
    probe.write_text(GROUPS_PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["review"] == [["TD-127", 532, "declared"], ["TD-128", 540, "declared"], ["TD-133", 542, "derived"]]
    assert got["progress"] == [["TD-129", None, "declared"], ["TD-132", None, "derived"]]
    assert got["done"] == [["TD-130", None, "declared"]] and got["dropped"] == [["TD-131", None, "declared"]]
    assert got["none"] == {"progress": [], "review": [], "done": [], "dropped": []}


@pytest.mark.unit
def test_drop_is_behind_more_on_in_progress_claims_and_its_confirm_names_the_cost():
    """§4.5a: never a primary button — the panel's more ▾, on a declared in-progress claim only —
    and the confirm says what it does: the lease ends, the branch stays, who can claim it again."""
    js = (UI / "static" / "app.js").read_text()
    body = js[js.index("function renderReports(v)") : js.index("function renderInbox(v)")]
    assert 'const drops = g.progress.filter((p) => p.status === "claimed"' in body
    assert "The lease ends and another session may take it; " in body and "can claim it again." in body
    assert "v.git.branch" not in body  # the checked-out branch may be another claim's
    assert '$("#reportsmenu").innerHTML = drops.map' in body and '$("#reportsmore").hidden = !drops.length' in body
    assert "claimed · in review ${prLink(p.review_pr)}" in body
    assert body.count('data-act="drop"') == 1  # only the menu's; the rows carry no control


@pytest.fixture
def client(subprocess_agent):
    from agentorc.ui.app import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.mark.integration
def test_the_panel_carries_the_i_mark_the_menu_and_the_pr_base(client, tmp_path):
    """The page half: the heading's **i** mark and its paragraph replace the note under the list,
    the more ▾ menu is there to fill, and the PR base rides the panel for the links."""
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "rep"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    page = client.get(f"/focus/{sid}").text
    assert 'id="i-reports"' in page and 'id="info-reports" hidden' in page
    assert 'id="reportsmore"' in page and 'id="reportsmenu"' in page and 'data-pr-base=""' in page
    assert "Drop records that a claim was let go" not in page
    client.post(f"/api/sessions/{sid}/remove", json={})


@pytest.mark.unit
def test_repo_web_is_the_github_origin_or_nothing(tmp_path):
    from agentorc import review

    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:eyecantell/agentorc.git"], check=True
    )
    assert review.repo_web(str(repo)) == "https://github.com/eyecantell/agentorc"
    assert review.repo_web(None) == "" and review.repo_web(str(tmp_path)) == ""
