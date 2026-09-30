"""TD-219 slices 3 and 4, design §4.9 *Add an entry to the ledger* and §4.5a **Add entry…** / **Open a
session**: one form from the Repo page and the team card, and the session way out of it — an
interactive session in a new worktree `entry-<n>`, with the role the team's `entries:` names for the
Type, at the prompt; its composer text comes back to the page and nothing is typed into the pane."""

from __future__ import annotations

import subprocess

import pytest
import yaml
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def world(tmp_path, *, team: bool = True, entries: dict | None = None, seat: bool = True):
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
        t = {"projects": ["sam"], "manager": {"name": "manager-sam"}}
        if seat:
            t["techlead"] = {"name": "techlead-sam"}
        t["members"] = [{"role": "grinder"}]
        if entries:
            t["entries"] = entries
        doc["teams"] = {"sam-grind": t}
    (home / "org.yml").write_text(yaml.safe_dump(doc))
    return repo


def fake(fleet, taken=(), created=None, handed=None):
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
            if method == "entry_add":
                handed.append(kw)
                seat = kw["teams"][0]["seat"]
                return {"id": "m-1", "to": seat, "team": kw["teams"][0]["team"], "read_when": "fills this seat"}
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
    assert 'id="entryhand"' in html and "Hand to the techlead" in html  # slice 4


def test_hand_to_the_techlead_sends_the_words_to_the_repos_first_seat_and_names_the_message(tmp_path, monkeypatch):
    """§4.5a Add entry form → **Hand to the techlead** (TD-219 slice 4): the plan says who takes it and
    when it is read — a seat nobody fills reads *fills this seat* — and the press hands `entry_add` the
    repo's checkout, the Type, the words and the servicing teams with their seats; the toast names
    the seat and the message and links its page."""
    repo = world(tmp_path)
    handed: list[dict] = []
    c = client(monkeypatch, tmp_path, fake([], handed=handed))
    hand = c.get("/api/entry/plan", params={"repo": "samscrape", "type": "debt"}).json()["hand"]
    assert (hand["team"], hand["name"], hand["why"]) == ("sam-grind", "techlead-sam", "")
    assert hand["to"].endswith("techlead-sam")
    assert hand["line"] == "fills this seat: a session starts on the next tick and reads it first"
    r = c.post("/api/entry/hand", json={"repo": "samscrape", "type": "feature", "words": "a board line is hidden"})
    assert r.status_code == 200, r.text
    (kw,) = handed
    assert (kw["repo"], kw["type"], kw["text"]) == (str(repo.resolve()), "feature", "a board line is hidden")
    assert kw["teams"] == [{"team": "sam-grind", "seat": hand["to"]}]
    got = r.json()
    assert got["text"] == "handed to techlead-sam · m-1" and got["href"] == "/inbox/m-1"


def test_a_live_seat_says_its_own_sentence(tmp_path, monkeypatch):
    """The sentence under the button is the seat's record's own when one is live (the agent's
    `refill`, an ask's sentence with no bound), not the one for a seat nobody fills."""
    world(tmp_path)
    from agentorc import org as orgmod
    from agentorc import teams

    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    org = orgmod.load(tmp_path / "home" / "org.yml")
    t = org.teams["sam-grind"]
    sid = teams.seat_id(org, t, "kmaster", "kmaster")
    live = {"id": sid, "name": "techlead-sam", "state": "working", "read_when": {"refill": "read when its turn ends"}}
    c = client(monkeypatch, tmp_path, fake([live]))
    assert c.get("/api/entry/plan", params={"repo": "samscrape", "type": "debt"}).json()["hand"]["line"] == (
        "read when its turn ends"
    )


@pytest.mark.parametrize(("team", "seat", "why"), [(True, False, "this team has no techlead seat"),
                                                   (False, True, "no team services this repo")])  # fmt: skip
def test_hand_is_disabled_with_its_reason(tmp_path, monkeypatch, team, seat, why):
    """With no techlead seat, or no team at all, the plan carries the reason the button is disabled,
    in the design's words, and names no seat to hand to."""
    world(tmp_path, team=team, seat=seat)
    c = client(monkeypatch, tmp_path, fake([]))
    hand = c.get("/api/entry/plan", params={"repo": "samscrape", "type": "debt"}).json()["hand"]
    assert hand["why"] == why and hand["to"] == ""


def test_a_seat_with_no_checkout_is_not_read_as_no_seat():
    """From the review of slice 4: `teams.seat_id` answers "" both for a team with no techlead and
    for a seat whose home has no checkout on its host, and `entry_add` refuses both — but the reason
    under the button says which, so the person knows whether to add a seat or a checkout."""
    from datetime import UTC, datetime

    from agentorc.ui.repo import entry_hand

    now = datetime(2026, 9, 29, tzinfo=UTC)
    none = entry_hand([{"team": "t", "seat": "", "name": "", "techlead": ""}], {}, now)
    assert none["why"] == "this team has no techlead seat" and none["to"] == ""
    bare = entry_hand([{"team": "t", "seat": "", "name": "", "techlead": "techlead-sam"}], {}, now)
    assert bare["why"] == "techlead-sam: the techlead seat has no checkout on its host" and bare["to"] == ""
