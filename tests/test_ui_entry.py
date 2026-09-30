"""TD-219 slice 3, design §4.9 *Add an entry to the ledger* and §4.5a **Add entry…** / **Open a
session**: one form from the Repo page and the team card, and the session way out of it — an
interactive session in a new worktree `entry-<n>`, with the role the team's `entries:` names for the
Type, at the prompt; its composer text comes back to the page and nothing is typed into the pane."""

from __future__ import annotations

import subprocess

import pytest
import yaml
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def world(tmp_path, *, team: bool = True, entries: dict | None = None):
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "samscrape"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    roster = tmp_path / "repos.txt"
    roster.write_text(f"{repo}\n")
    (home / "hosts.yml").write_text(f"local:\n  name: kmaster\n  local: true\n  repos_registry: {roster}\n")
    doc: dict = {"projects": {"sam": {"repos": {"samscrape": {"kmaster": str(repo)}}}}, "roles": {"designer": {}}}
    if team:
        t = {"projects": ["sam"], "manager": {"name": "manager-sam"}, "techlead": {"name": "techlead-sam"}}
        t["members"] = [{"role": "grinder"}]
        if entries:
            t["entries"] = entries
        doc["teams"] = {"sam-grind": t}
    (home / "org.yml").write_text(yaml.safe_dump(doc))
    return repo


def fake(fleet, taken=(), created=None):
    class Fake:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call(self, method, **kw):
            if method == "name_check":
                return {"verdict": "live" if kw["name"] in taken else "free"}
            if method == "create":
                created.append(kw)
                return {"id": f"ao-samscrape-{kw['name']}", "name": kw["name"], "state": "starting"}
            return {"list": fleet, "repos": {}, "doing_log": {}, "usage": {}, "host": {"name": "kmaster"}}.get(
                method, {}
            )

    return Fake


def client(monkeypatch, tmp_path, Fake):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    from agentorc.ui import app as ui

    monkeypatch.setattr(ui, "LocalClient", Fake)
    monkeypatch.setattr(ui, "read_boards", lambda: ([], ""))
    return TestClient(ui.create_app())


def test_open_a_session_starts_the_types_role_interactive_in_a_new_worktree_with_no_prompt(tmp_path, monkeypatch):
    repo = world(tmp_path, entries={"feature": "designer"})
    created: list[dict] = []
    manager = {"id": "ao-samscrape-manager-sam", "name": "manager-sam", "state": "idle", "team": "sam-grind"}
    c = client(monkeypatch, tmp_path, fake([manager], taken={"entry-1"}, created=created))
    # the line under the button, as the Type changes: the first free number, the role entries: names
    got = c.get("/api/entry/plan", params={"repo": "samscrape", "type": "feature"}).json()
    assert (got["team"], got["role"], got["name"]) == ("sam-grind", "designer", "entry-2")
    assert got["line"] == "an interactive designer session, entry-2, in a new worktree"
    assert c.get("/api/entry/plan", params={"repo": "samscrape", "type": "debt"}).json()["role"] == "techlead"
    # the press: interactive, in worktree entry-2, the role, the team's badge, its live manager, no prompt
    words = "The Inbox lists a board item only once it is due."
    r = c.post("/api/entry/session", json={"repo": "samscrape", "type": "feature", "words": words})
    assert r.status_code == 200, r.text
    (kw,) = created
    assert (kw["name"], kw["worktree"], kw["dir"], kw["repo"]) == (
        "entry-2",
        "entry-2",
        str(repo.resolve()),
        str(repo.resolve()),
    )
    assert kw["unattended"] is False and kw["prompt"] == "" and kw["role"] == "designer" and kw["team"] == "sam-grind"
    assert kw["controllers"] == ["ao-samscrape-manager-sam"]
    # the composer's text: entry.md's lines with the repo, the type and the ledger, then the words
    text = r.json()["text"]
    assert text.startswith(
        "The person asked for a new entry in the ledger of samscrape: docs/technical_debt.md, with Type feature"
    )
    assert text.endswith(words) and r.json()["id"] == "ao-samscrape-entry-2"


def test_a_repo_no_team_services_starts_plain_with_no_badge(tmp_path, monkeypatch):
    world(tmp_path, team=False)
    created: list[dict] = []
    c = client(monkeypatch, tmp_path, fake([], created=created))
    got = c.get("/api/entry/plan", params={"repo": "samscrape", "type": "debt"}).json()
    assert got["role"] == "plain" and got["team"] == "" and got["line"].endswith("no team services this repo")
    r = c.post("/api/entry/session", json={"repo": "samscrape", "type": "debt", "words": ""})
    (kw,) = created
    assert kw["role"] == "plain" and kw["team"] == "" and kw["controllers"] == [] and kw["unattended"] is False
    # with **What** empty the composer holds the lines alone
    from agentorc import repoconfig

    assert r.json()["text"] == repoconfig.entry_text("samscrape", "debt", "docs/technical_debt.md")


def test_a_bad_type_or_an_unknown_repo_is_refused_and_nothing_starts(tmp_path, monkeypatch):
    world(tmp_path)
    created: list[dict] = []
    c = client(monkeypatch, tmp_path, fake([], created=created))
    assert c.get("/api/entry/plan", params={"repo": "samscrape", "type": "bug"}).status_code == 400
    assert c.post("/api/entry/session", json={"repo": "nope", "type": "debt"}).status_code == 404
    assert created == []


def test_the_button_is_drawn_on_the_repo_page_and_the_form_in_every_page(tmp_path, monkeypatch):
    repo = world(tmp_path)
    root = str(repo)
    reading = {"name": "samscrape", "root": root, "ledger": {"path": "docs/technical_debt.md", "entries": []}}

    class Fake(fake([])):
        async def call(self, method, **kw):
            if method == "repos":
                return {root: reading}
            return await super().call(method, **kw)

    c = client(monkeypatch, tmp_path, Fake)
    html = c.get("/repo/samscrape").text
    assert 'data-addentry="samscrape"' in html and 'id="entrydlg"' in html
    assert "Hand to the techlead" not in html  # slice 4, with TD-218
