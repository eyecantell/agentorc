"""The Org page's **Teams** strip and New session's **Project** picker (design §4.5a, §4.9), with
the RPC mocked the way `test_cli_teams.py` mocks it — no agent, no tmux, no session ever created.

The strip's Start and Stop are `agentorc.teamrun`'s, the very sequence `ao team start|stop` runs;
what is tested here is that the routes call it, that a pre-flight refusal creates nothing and comes
back as the agent's own message, and that the page renders what §4.5a's row describes.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from agentorc import teamrun
from agentorc.ui import app as uiapp

pytestmark = pytest.mark.unit

HOST = "kmaster"


class Fleet:
    """The agent, as far as these tests are concerned: a list of records and a call log."""

    def __init__(self) -> None:
        self.sessions: list[dict] = []
        self.calls: list[tuple[str, dict]] = []
        self.verdicts: dict[str, dict] = {}

    def handle(self, method: str, params: dict):
        self.calls.append((method, params))
        if method == "list":
            return list(self.sessions)
        if method == "usage":
            return {}
        if method == "recent_dirs":
            return []
        if method == "adapters":
            return ["claude-code", "shell"]
        if method == "name_check":
            return self.verdicts.get(params["name"], {"name": params["name"], "verdict": "free"})
        if method == "create":
            rec = {
                "id": f"ao-{params['name']}",
                "name": params["name"],
                "state": "working",
                "dir": params["dir"],
                "kind": "agent",
                "adapter": params.get("adapter") or "claude-code",
                "controllers": list(params.get("controllers") or []),
                "team": params.get("team", ""),
                "project": params.get("project", ""),
                "previous_run": None,
            }
            self.sessions.append(rec)
            return rec
        if method == "inbox":  # the Org top bar's person inbox count (design §4.5a)
            return {"id": "person", "entries": [], "threads": {}, "sends": [], "unread": 0}
        if method in ("send", "kill", "seen"):
            for s in self.sessions:
                if s["id"] == params["id"]:
                    s["state"] = "closed" if method == "kill" else "idle"
            return None
        raise AssertionError(method)

    def creates(self) -> list[dict]:
        return [p for m, p in self.calls if m == "create"]

    def sent(self, method: str) -> list[str]:
        return [p["id"] for m, p in self.calls if m == method]


def org_doc(root: Path) -> dict:
    return {
        "projects": {
            "ao": {"repos": {"agentorc": {HOST: str(root / "agentorc")}}},
            "wide": {
                "repos": {
                    "agentorc": {HOST: str(root / "agentorc")},
                    "ao-api": {HOST: str(root / "ao-api"), "devenv": "/workspaces/api"},
                }
            },
        },
        "teams": {
            "ao-grind": {
                "projects": ["ao"],
                "lead": {"role": "lead", "name": "orc-ao"},
                "members": [{"role": "grinder", "count": 2, "name": "grind", "lane": "free-pick"}],
            }
        },
    }


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A temp AGENTORC_HOME with `hosts.yml`, `org.yml`, two checkouts and a registry naming them,
    plus a fleet standing in for the agent on both the async and the blocking path."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    registry = home / "repos.txt"
    for repo in ("agentorc", "ao-api"):
        (tmp_path / repo / ".git").mkdir(parents=True)
    registry.write_text(f"{tmp_path / 'agentorc'}\n{tmp_path / 'ao-api'}\n")
    (home / "hosts.yml").write_text(
        yaml.safe_dump({"local": {"name": HOST, "local": True, "repos_registry": str(registry)}})
    )
    (home / "profiles.yml").write_text(yaml.safe_dump({"default": "paul", "profiles": {"paul": {"account": "paul"}}}))
    (home / "org.yml").write_text(yaml.safe_dump(org_doc(tmp_path)))
    fleet = Fleet()

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def call(self, method, **params):
            return fleet.handle(method, params)

    monkeypatch.setattr(uiapp, "LocalClient", FakeClient)
    monkeypatch.setattr(uiapp, "rpc", lambda method, **params: fleet.handle(method, params))
    return tmp_path, fleet


@pytest.fixture
def client(world):
    with TestClient(uiapp.create_app()) as c:
        yield c


def write_org(tmp_path, doc):
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))


def badged(name, team, project="ao", state="working"):
    return {"id": name, "name": name, "state": state, "dir": "/tmp", "kind": "agent", "team": team, "project": project}


# ── the strip's contents (design §4.5a Org **Teams** strip) ───────────────────────────────────


def test_the_strip_lists_every_definition_with_its_source_projects_members_and_live_count(world):
    tmp_path, fleet = world
    v = uiapp.teams_view([])
    (row,) = v["teams"]
    assert row["name"] == "ao-grind" and row["lead"] == "orc-ao"
    assert row["projects"] == ["ao"] and row["members"] == 2 and row["live"] == 0  # stopped
    assert row["source"] == str(tmp_path / "home" / "org.yml") and v["source"] == row["source"]
    # a team is live when a session carrying its badge is; a dead one does not count
    live = uiapp.teams_view([badged("orc-ao", "ao-grind"), badged("grind-1", "ao-grind", state="exited")])
    assert live["teams"][0]["live"] == 1


def test_a_repos_own_teams_are_folded_in_and_the_org_file_wins(world):
    tmp_path, fleet = world
    (tmp_path / "agentorc" / ".agentorc.yml").write_text(
        yaml.safe_dump(
            {
                "teams": {
                    "ao-grind": {"lead": {"name": "shadow"}},  # the org file wins this name
                    "repo-grind": {"lead": {"role": "person"}, "members": [{"role": "grinder"}]},
                }
            }
        )
    )
    rows = {r["name"]: r for r in uiapp.teams_view([])["teams"]}
    assert rows["ao-grind"]["lead"] == "orc-ao"  # not `shadow`
    assert rows["repo-grind"]["lead"] == "person" and rows["repo-grind"]["members"] == 1
    assert rows["repo-grind"]["source"].endswith("agentorc/.agentorc.yml")


def test_no_definitions_anywhere_is_an_empty_strip_that_still_names_the_file(world):
    tmp_path, fleet = world
    (tmp_path / "home" / "org.yml").unlink()
    v = uiapp.teams_view([])
    assert v["teams"] == [] and v["source"].endswith("org.yml") and v["notes"] == []


def test_a_malformed_definition_is_a_note_not_a_500(world):
    tmp_path, fleet = world
    (tmp_path / "home" / "org.yml").write_text("teams: {bad: {projects: [nope]}}\n")
    v = uiapp.teams_view([])
    assert v["teams"] == [] and "not a defined project" in v["notes"][0]
    # and one broken repo file does not empty the strip of the definitions that do parse
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(org_doc(tmp_path)))
    (tmp_path / "ao-api" / ".agentorc.yml").write_text("teams: {x: {members: [{nope: 1}]}}\n")
    v = uiapp.teams_view([])
    assert [r["name"] for r in v["teams"]] == ["ao-grind"] and "ao-api" in v["notes"][0]


def test_the_page_renders_a_row_per_team_and_collapses_to_a_line_when_none_is_defined(world, client):
    tmp_path, fleet = world
    html = client.get("/").text
    # Nothing is live: the strip lists the definition with Start, and Stop has no card to sit on.
    assert 'data-team-act="start"' in html and 'data-team-act="stop' not in html
    assert 'class="team-row" data-team="ao-grind"' in html and "lead orc-ao · 2 members" in html
    (tmp_path / "home" / "org.yml").unlink()
    html = client.get("/").text
    assert "Teams: none defined" in html and "data-team-act" not in html


def test_a_live_team_leaves_the_strip_and_its_card_carries_stop_and_stop_now(world, client):
    """Design §4.5a **team groups** (2026-09-16): the control sits on the thing it stops."""
    _tmp, fleet = world
    fleet.sessions = [{**badged("orc-ao", "ao-grind"), "tail": []}, {**badged("adhoc-1", "adhoc"), "tail": []}]
    html = client.get("/").text
    assert 'class="team-row" data-team="ao-grind" data-live="1" hidden' in html  # out of the strip
    head = html[html.index('<section class="tgroup" data-team="ao-grind"') :]
    head = head[: head.index('<div class="grid">')]
    assert 'data-team-act="stop" data-team="ao-grind" title' in head  # shown: no `hidden`
    assert 'data-team-act="stopnow" data-team="ao-grind" title' in head
    # A badge with no definition has nothing `ao team stop` could read: its buttons stay hidden.
    adhoc = html[html.index('<section class="tgroup" data-team="adhoc"') :]
    adhoc = adhoc[: adhoc.index('<div class="grid">')]
    assert 'data-team-act="stop" data-team="adhoc" hidden' in adhoc


# ── Start: the shared planner, every check before any create ──────────────────────────────────


def test_start_runs_the_shared_sequence_and_the_members_carry_controllers_lead(world, client):
    tmp_path, fleet = world
    r = client.post("/api/teams/ao-grind/start")
    assert r.status_code == 200
    body = r.json()
    assert [s["name"] for s in body["sessions"]] == ["orc-ao", "grind-1", "grind-2"]
    made = fleet.creates()
    assert made[0]["controllers"] == [] and made[0]["worktree"] == "orc-ao"
    assert all(p["controllers"] == ["ao-orc-ao"] for p in made[1:])
    assert {p["team"] for p in made} == {"ao-grind"} and {p["project"] for p in made} == {"ao"}
    # every name was checked before the first create (§4.9: never half a team)
    order = [m for m, _ in fleet.calls]
    assert order.count("name_check") == 3 and order.index("create") > max(
        i for i, m in enumerate(order) if m == "name_check"
    )


def test_a_pre_flight_failure_reports_the_agents_message_and_creates_nothing(world, client):
    tmp_path, fleet = world
    fleet.verdicts["grind-2"] = {
        "name": "grind-2",
        "verdict": "live",
        "holder": "ao-grind-2",
        "holder_state": "working",
    }
    r = client.post("/api/teams/ao-grind/start")
    assert r.status_code == 400
    assert "was not started" in r.json()["detail"] and "grind-2 is working as ao-grind-2" in r.json()["detail"]
    assert not fleet.creates()


def test_a_missing_checkout_stops_the_start_before_any_name_check(world, client):
    tmp_path, fleet = world
    doc = org_doc(tmp_path)
    doc["projects"]["ao"]["repos"]["agentorc"][HOST] = str(tmp_path / "gone")
    write_org(tmp_path, doc)
    r = client.post("/api/teams/ao-grind/start")
    assert r.status_code == 400 and "does not exist on kmaster" in r.json()["detail"]
    assert not fleet.creates() and "name_check" not in [m for m, _ in fleet.calls]


def test_an_unknown_team_names_the_defined_ones(world, client):
    r = client.post("/api/teams/nope/start")
    assert r.status_code == 400 and "unknown team 'nope'" in r.json()["detail"]


# ── Stop: what was sent comes back, the wait does not hold the page ───────────────────────────


def test_stop_now_kills_every_badged_session_in_one_call(world, client):
    tmp_path, fleet = world
    fleet.sessions += [badged("orc-ao", "ao-grind"), badged("grind-1", "ao-grind")]
    r = client.post("/api/teams/ao-grind/stop", json={"now": True})
    assert r.status_code == 200
    body = r.json()
    assert sorted(fleet.sent("kill")) == ["grind-1", "orc-ao"] and not fleet.sent("send")
    assert body["lead"] is None and "killed 2 sessions" in body["text"]


def test_stop_sends_the_wrap_up_to_the_members_and_leaves_the_lead_to_the_background(world, client, monkeypatch):
    tmp_path, fleet = world
    later: list[teamrun.Stopping] = []
    monkeypatch.setattr(teamrun, "stop_lead", lambda call, st, **kw: later.append(st) or st)
    fleet.sessions += [badged("orc-ao", "ao-grind"), badged("grind-1", "ao-grind")]
    body = client.post("/api/teams/ao-grind/stop", json={}).json()
    # only the member was sent to in the request: the lead follows once they settle (§4.9)
    assert fleet.sent("send") == ["grind-1"] and not fleet.sent("kill")
    assert [e["name"] for e in body["sessions"]] == ["grind-1"]
    assert body["lead"] == "orc-ao" and "orc-ao follows when they settle" in body["text"]
    for _ in range(40):  # the second half runs behind the response; another request lets the loop turn
        if later:
            break
        client.get("/api/teams")
        time.sleep(0.05)
    assert [st.lead["name"] for st in later] == ["orc-ao"]  # the second half was handed the lead


def test_stopping_a_team_with_nothing_live_says_so(world, client):
    r = client.post("/api/teams/ao-grind/stop", json={})
    assert r.status_code == 400 and "nothing to stop" in r.json()["detail"]


# ── New session's Project picker (design §4.5a **Project** picker) ────────────────────────────


def test_the_picker_offers_each_project_with_its_repos_checkouts_on_this_host(world, client):
    tmp_path, fleet = world
    html = client.get("/new").text
    assert 'name="project"' in html and ">No project<" in html
    assert f'"path": "{tmp_path / "agentorc"}"' in html  # the narrowing data rides with the option
    assert '"repo": "ao-api"' in html and '"hosts": ["devenv", "kmaster"]' in html
    projects = {p["name"]: p for p in uiapp.projects_view()}
    assert [r["repo"] for r in projects["wide"]["repos"]] == ["agentorc", "ao-api"]
    assert projects["ao"]["repos"][0]["path"] == str(tmp_path / "agentorc")


def test_no_project_is_the_default_and_changes_nothing(world, client):
    tmp_path, fleet = world
    r = client.post(
        "/new", data={"name": "x", "dir": str(tmp_path / "agentorc"), "prompt": "go"}, follow_redirects=False
    )
    assert r.status_code == 303
    (made,) = fleet.creates()
    assert made["project"] == "" and made["prompt"] == "go"


def test_choosing_a_project_badges_the_session_and_puts_the_project_block_in_front(world, client):
    tmp_path, fleet = world
    r = client.post(
        "/new",
        data={"name": "x", "dir": str(tmp_path / "agentorc"), "prompt": "go", "project": "wide"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    (made,) = fleet.creates()
    assert made["project"] == "wide"
    assert made["prompt"].startswith("## Project: wide")
    assert f"- agentorc: {tmp_path / 'agentorc'}  — your home" in made["prompt"]
    assert "ao-api: not checked out on kmaster" not in made["prompt"]  # it is checked out here
    assert made["prompt"].endswith("go")


def test_a_one_repo_project_badges_without_a_block_and_an_undefined_name_still_badges(world, client):
    tmp_path, fleet = world
    for project in ("ao", "nosuch"):
        client.post(
            "/new",
            data={"name": project, "dir": str(tmp_path / "agentorc"), "prompt": "go", "project": project},
            follow_redirects=False,
        )
    assert [p["project"] for p in fleet.creates()] == ["ao", "nosuch"]
    assert {p["prompt"] for p in fleet.creates()} == {"go"}  # no reach to describe either way


def test_a_background_lead_stop_that_fails_is_reported_rather_than_dropped(world, client, monkeypatch):
    """§4.5 "Errors": there is no silent failure path. The lead's stop happens after the response,
    so its outcome reaches the page through the strip's own refresh, once (review of PR #124)."""
    tmp_path, fleet = world

    def boom(call, st, **kw):
        raise RuntimeError("agent said no")

    monkeypatch.setattr(teamrun, "stop_lead", boom)
    fleet.sessions += [badged("orc-ao", "ao-grind"), badged("grind-1", "ao-grind")]
    assert client.post("/api/teams/ao-grind/stop", json={}).json()["lead"] == "orc-ao"
    row = None
    for _ in range(40):  # the failure lands behind the response; each request lets the loop turn
        row = next(t for t in client.get("/api/teams").json()["teams"] if t["name"] == "ao-grind")
        if row.get("error"):
            break
        time.sleep(0.05)
    assert row and "orc-ao did not stop" in row["error"] and "agent said no" in row["error"]
    again = next(t for t in client.get("/api/teams").json()["teams"] if t["name"] == "ao-grind")
    assert "error" not in again  # reported once, then forgotten


def test_two_stop_presses_do_not_start_two_lead_stops(world, client, monkeypatch):
    """A second press while the first is still waiting out the wrap-up window would send the lead a
    second wrap-up or kill, and the first task would swallow the failure (review of PR #124)."""
    tmp_path, fleet = world
    started: list[str] = []

    def slow(call, st, **kw):
        started.append(st.lead["name"])
        time.sleep(0.4)  # hold the members' settle wait open, as the real one does for minutes
        return st

    monkeypatch.setattr(teamrun, "stop_lead", slow)
    fleet.sessions += [badged("orc-ao", "ao-grind"), badged("grind-1", "ao-grind")]
    first = client.post("/api/teams/ao-grind/stop", json={}).json()
    second = client.post("/api/teams/ao-grind/stop", json={}).json()
    assert first["lead"] == "orc-ao"
    assert second["lead"] is None and "already stopping" in second["text"]
    assert started == ["orc-ao"]  # one task, not two
