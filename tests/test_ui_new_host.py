"""New session on another host (design §4.4a *The New session form on another host*, §4.5a New
session **the form** *Another host*; TD-294 slice 2): every reading the form draws about a place —
the Repo list, *another directory…*'s check, the occupancy, the Where chips, the roles and the
team's reader — is the picked host's, read through the home's `host_*` reads; a host that does not
answer is said in the note's place, never a refusal. Start (slice 3) resolves the role there too: its
brief, the ledger and the team's reader come from that host's files, never this host's disk."""

import pytest
from fastapi.testclient import TestClient

REPO_YML = (
    "roles:\n  scout:\n    brief: docs/scout.md\n  stray:\n    brief: /etc/stray.md\n"
    "controllers: [lead-1]\nledger: docs/ledger.md\n"
)
NODE_FILES = {".agentorc.yml": REPO_YML, "docs/scout.md": "Scout the node's own alpha.\n"}


@pytest.fixture
def form(tmp_path, monkeypatch):
    from agentorc.ui import app as uiapp
    from sessionorc.client import AgentError

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    (home / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    calls: list[tuple[str, dict]] = []

    def answer(method, p):
        if p.get("host") == "silent":
            raise AgentError("silent did not answer: the link dropped")
        if method == "host_repos":
            return {"host": p["host"], "repos": ["/srv/node/alpha", "/srv/node/beta"]}
        if method == "host_dir":
            there = p["dir"].startswith("/srv/node/alpha")
            return {"host": p["host"], "dir": p["dir"], "exists": there, "root": "/srv/node/alpha" if there else ""}
        if method == "host_files":
            assert p["dir"] == "/srv/node/alpha"
            return {"host": p["host"], "dir": p["dir"], "files": {k: NODE_FILES.get(k) for k in p["paths"]}}
        if method == "create":
            return {"id": f"ao-alpha-{p['name']}@{p['host']}", "state": "starting"}
        if method == "host_worktrees":
            return {"host": p["host"], "repo": p["repo"], "worktrees": [
                {"name": "td-1", "path": "/srv/node/alpha/.claude/worktrees/td-1", "occupied": False},
                {"name": "td-2", "path": "/srv/node/alpha/.claude/worktrees/td-2", "occupied": True},
            ]}  # fmt: skip
        if method == "host_occupancy":
            return {"host": p["host"], "dir": p["dir"], "occupants": ["w1@node1 (working)"], "git": True}
        return {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def call(self, method, **params):
            calls.append((method, params))
            return answer(method, params)

    monkeypatch.setattr(uiapp, "LocalClient", FakeClient)
    with TestClient(uiapp.create_app()) as c:
        yield c, calls


@pytest.mark.unit
def test_the_repo_list_and_the_checks_are_the_picked_hosts(form):
    c, calls = form
    got = c.get("/api/repos", params={"host": "node1"}).json()
    assert got["repos"] == [{"name": "alpha", "path": "/srv/node/alpha"}, {"name": "beta", "path": "/srv/node/beta"}]
    assert c.get("/api/dir_check", params={"dir": "/srv/node/alpha", "host": "node1"}).json()["exists"] is True
    gone = c.get("/api/dir_check", params={"dir": "/srv/node/gone", "host": "node1"}).json()
    assert gone["exists"] is False and gone["why"] == "no such directory on node1"
    occ = c.get("/api/occupancy", params={"dir": "/srv/node/alpha", "host": "node1"}).json()
    assert occ["occupants"] == ["w1@node1 (working)"] and occ["git"] is True
    chips = c.get("/api/worktrees", params={"repo": "/srv/node/alpha", "host": "node1"}).json()["worktrees"]
    assert chips == [{"name": "td-1", "path": "/srv/node/alpha/.claude/worktrees/td-1"}]  # the occupied one is no chip
    # this host's own name reads this host as it always has: no host_* read is made for it
    calls.clear()
    c.get("/api/dir_check", params={"dir": "/nowhere", "host": "kmaster"})
    assert not [m for m, _ in calls if m.startswith("host_")]


@pytest.mark.unit
def test_the_roles_are_read_from_the_picked_hosts_repo(form):
    c, _calls = form
    got = c.get("/api/roles", params={"dir": "/srv/node/alpha/.claude/worktrees/td-1", "host": "node1"}).json()
    assert "scout" in [r["name"] for r in got["roles"]] and got["controllers"] == ["lead-1"]
    assert got["file"] == "node1:/srv/node/alpha/.agentorc.yml"
    outside = c.get("/api/roles", params={"dir": "/srv/node/gone", "host": "node1"}).json()
    assert "scout" not in [r["name"] for r in outside["roles"]] and "error" not in outside


@pytest.mark.unit
def test_a_host_that_does_not_answer_is_said_not_refused(form):
    c, _calls = form
    q = {"host": "silent"}
    assert c.get("/api/repos", params=q).json() == {
        "host": "silent",
        "repos": [],
        "why": "silent did not answer: the link dropped",
    }
    d = c.get("/api/dir_check", params={**q, "dir": "/x"}).json()
    assert d["exists"] is None and "did not answer" in d["why"]
    assert "did not answer" in c.get("/api/occupancy", params={**q, "dir": "/x"}).json()["why"]
    assert c.get("/api/worktrees", params={**q, "repo": "/x"}).json()["worktrees"] == []
    roles = c.get("/api/roles", params={**q, "dir": "/x"}).json()
    assert roles["roles"] and "did not answer" in roles["error"]  # the built-ins still stand


@pytest.mark.unit
def test_the_page_passes_the_picked_host_on_every_read():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui" / "static" / "app.js").read_text()
    for api in ("dir_check", "worktrees", "occupancy", "roles", "team_review"):
        line = next(ln for ln in js.splitlines() if f"/api/{api}?" in ln)
        assert "${hq()}" in line, api
    assert "|| away()" not in js and "away() ||" not in js  # nothing is skipped for another host now
    assert (
        "fetch(`/api/repos?host=" in js and 'hostSel.addEventListener("change", () => { gate(); loadRepos(); })' in js
    )


@pytest.mark.unit
def test_the_form_reads_its_own_directory_field():
    """The top bar's Shell form carries a hidden `dir` first in the page; the New session script once
    bound it with a bare `[name=dir]`, so a Repo pick never reached the field Start posts."""
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui" / "static" / "app.js").read_text()
    body = js[js.index("AO.newSession = function") :]
    assert "const dir = $(\"form[action='/new'] [name=dir]\")" in body
    base = (Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui" / "templates" / "base.html").read_text()
    assert 'id="shellform"' in base and 'name="dir"' in base  # the reason the selector is scoped


@pytest.mark.unit
def test_start_on_another_host_carries_that_hosts_brief_and_ledger(form):
    c, calls = form
    r = c.post(
        "/new",
        data={"name": "w1", "dir": "/srv/node/alpha", "role": "scout", "host": "node1"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    made = next(p for m, p in calls if m == "create")
    assert made["host"] == "node1" and made["role"] == "scout"
    assert "Scout the node's own alpha." in made["prompt"] and made["ledger"] == "docs/ledger.md"
    # the brief was read on node1, by its path in the checkout there
    assert ("host_files", {"host": "node1", "dir": "/srv/node/alpha", "paths": ["docs/scout.md"]}) in calls


@pytest.mark.unit
def test_a_brief_outside_the_checkout_on_another_host_stops_start(form):
    c, calls = form
    r = c.post(
        "/new",
        data={"name": "w1", "dir": "/srv/node/alpha", "role": "stray", "host": "node1"},
        follow_redirects=False,
    )
    assert r.status_code == 400 and "outside the checkout" in r.text
    assert "create" not in [m for m, _ in calls]  # nothing was started


@pytest.mark.unit
def test_start_on_a_host_that_does_not_answer_creates_nothing(form):
    c, calls = form
    r = c.post("/new", data={"name": "w1", "dir": "/x", "role": "scout", "host": "silent"}, follow_redirects=False)
    assert r.status_code == 400 and "did not answer" in r.text
    assert "create" not in [m for m, _ in calls]
