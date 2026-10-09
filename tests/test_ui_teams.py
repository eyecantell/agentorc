"""The Org page's **Teams** strip and New session's **Project** picker (design §4.5a, §4.9), with
the RPC mocked the way `test_cli_teams.py` mocks it — no agent, no tmux, no session ever created.

The strip's Start and Stop are `agentorc.teamrun`'s, the very sequence `ao team start|stop` runs;
what is tested here is that the routes call it, that a pre-flight refusal creates nothing and comes
back as the agent's own message, and that the page renders what §4.5a's row describes.
"""

from __future__ import annotations

import pathlib
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
        self.repos: dict[str, dict] = {}
        self.waiting: dict[str, list] = {}  # the `host` reading's (TD-274)

    def handle(self, method: str, params: dict):
        self.calls.append((method, params))
        if method == "list":
            return list(self.sessions)
        if method == "usage":
            return {}
        if method == "repos":  # the home's repo readings: the Start's lane line reads their ledgers (TD-265)
            return dict(self.repos)
        if method == "recent_dirs":
            return []
        if method == "adapters":
            return ["claude-code", "shell"]
        if method == "name_check":
            return self.verdicts.get(params["name"], {"name": params["name"], "verdict": "free"})
        if method == "get":
            return next(s for s in self.sessions if s["id"] == params["id"])
        if method == "create":
            rec = {
                "id": f"ao-{params['name']}",
                "name": params["name"],
                "state": "working",
                "dir": params["dir"],
                "kind": "agent",
                "adapter": params.get("adapter") or "claude-code",
                "controllers": list(params.get("controllers") or []),
                "unattended": bool(params.get("unattended")),
                "team": params.get("team", ""),
                "project": params.get("project", ""),
                "previous_run": None,
            }
            self.sessions.append(rec)
            return rec
        if method == "host":  # the Org page's node line (design §4.4a): this fake is a home
            return {
                "host": "kmaster", "home": "kmaster", "mode": "home", "home_reachable": True, "links": {},
                "waiting": self.waiting,
            }  # fmt: skip
        if method == "inbox":  # the Org top bar's person inbox count (design §4.5a)
            return {"id": "person", "entries": [], "threads": {}, "sends": [], "unread": 0}
        if method == "identity":  # the teams line's identity note (design §4.8a, TD-077 step 2)
            return {"host": HOST, "mode": "off", "detached_check": False, "tally": {}, "alarms": [], "sessions": {}}
        if method == "set_settings":  # the person's write (§5): what it wrote comes back, as the agent's does
            return {"teams": params.get("teams") or {}}
        if method == "relaunch":  # §4.9c *Switching*: the home's half, kept in the call log (TD-309)
            return next(s for s in self.sessions if s["id"] == params["id"])
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
                "manager": {"role": "manager", "name": "orc-ao"},
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


def badged(name, team, project="ao", state="working", unattended=True):
    return {
        "id": name, "name": name, "state": state, "dir": "/tmp", "kind": "agent", "team": team, "project": project,
        "unattended": unattended,
    }  # fmt: skip


# ── the strip's contents (design §4.5a Org **Teams** strip) ───────────────────────────────────


def test_the_strip_lists_every_definition_with_its_source_projects_members_and_live_count(world):
    tmp_path, fleet = world
    v = uiapp.teams_view([])
    (row,) = v["teams"]
    assert row["name"] == "ao-grind" and row["manager"] == "orc-ao"
    assert row["projects"] == ["ao"] and row["members"] == 2 and row["live"] == 0  # stopped
    assert row["source"] == str(tmp_path / "home" / "org.yml") and v["source"] == row["source"]
    # a team is live when a session carrying its badge is; a dead one does not count
    live = uiapp.teams_view([badged("orc-ao", "ao-grind"), badged("grind-1", "ao-grind", state="exited")])
    assert live["teams"][0]["live"] == 1


def test_a_team_whose_sessions_all_declared_reads_wound_down_not_stopped(world):
    """design §4.5a **Teams** strip *wound down* note (§4.9a, TD-053 step 6): *nothing running* and
    *nothing left to run* are different facts about a team, and only the second is an answer — a
    team stopped by a person, a clock or a crash looks identical otherwise. All-or-nothing on
    purpose: one member's exhaustion is not the team's (§4.9a)."""
    tmp_path, fleet = world
    at, later = "2026-09-17T20:00:00Z", "2026-09-17T21:30:00Z"

    def done(name, when, state="closed"):
        """A member that declared and was then stopped — `ao team stop --close` (§4.9a step 3) is
        what makes the team's sessions dead, and the declaration stays on each record."""
        return {**badged(name, "ao-grind", state=state), "out_of_work": {"at": when, "why": "nothing open"}}

    # nothing has ever carried the badge: never run, not wound down
    assert uiapp.teams_view([])["teams"][0]["wound_down"] is None
    # one declared, one did not: the team stopped for some other reason and says so
    half = uiapp.teams_view([done("orc-ao", at), badged("grind-1", "ao-grind", state="exited")])
    assert half["teams"][0]["wound_down"] is None
    # every one of them declared: the strip says when, from the latest instant
    all_done = uiapp.teams_view([done("orc-ao", at), done("grind-1", later)])
    row = all_done["teams"][0]
    assert row["live"] == 0 and row["wound_down"] == later and row["wound_down_age"]
    assert uiapp.teams_view([done("orc-ao", at), done("grind-1", later, state="exited")])["teams"][0]["wound_down"]
    # a declaration in any other shape is not one — the strip is on the same page as every card,
    # so a raise here would empty the grid rather than one row (review of PR #203)
    junk = [{**badged("orc-ao", "ao-grind", state="closed"), "out_of_work": j} for j in ("x", ["y"], 7)]
    assert all(uiapp.teams_view([s])["teams"][0]["wound_down"] is None for s in junk)
    # ... and a team still running is described by what it is doing, never by a stale declaration:
    # a worker that declared but has not been stopped still sits `idle` at its prompt (§4.9a step 3
    # is what closes it), and until then the team is live and its group card, not the strip, is the
    # surface — the strip row is hidden for a live definition
    running = uiapp.teams_view([done("orc-ao", at), done("grind-1", later, state="idle")])
    assert running["teams"][0]["live"] == 1 and running["teams"][0]["wound_down"] is None


def test_a_repos_own_teams_are_folded_in_and_the_org_file_wins(world):
    tmp_path, fleet = world
    (tmp_path / "agentorc" / ".agentorc.yml").write_text(
        yaml.safe_dump(
            {
                "teams": {
                    "ao-grind": {"manager": {"name": "shadow"}},  # the org file wins this name
                    "repo-grind": {"manager": {"role": "person"}, "members": [{"role": "grinder"}]},
                }
            }
        )
    )
    rows = {r["name"]: r for r in uiapp.teams_view([])["teams"]}
    assert rows["ao-grind"]["manager"] == "orc-ao"  # not `shadow`
    assert rows["repo-grind"]["manager"] == "person" and rows["repo-grind"]["members"] == 1
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


def test_a_stopped_team_is_a_card_with_start_and_none_defined_is_a_line(world, client):
    tmp_path, fleet = world
    html = client.get("/").text
    # Nothing is live: the definition is a card with Start, no sessions in it, and no Stop.
    assert 'data-team-act="start"' in html and 'data-team-act="stop' not in html
    assert '<section class="tgroup" data-team="ao-grind"' in html and "Manager orc-ao · 2 members" in html
    assert "team-row" not in html  # the strip's rows are retired (design §4.5a, 2026-09-18)
    (tmp_path / "home" / "org.yml").unlink()
    html = client.get("/").text
    assert "Teams: none defined" in html and "data-team-act" not in html


def test_a_wound_down_note_says_who_closed_it_how_soon_and_why(world, client):
    """design §4.5a team card *wound down* note (TD-262, built by TD-265): who closed the members from
    their records' closer — the manager's name when it closed any — *after 40 s* for a run under ten
    minutes, and the manager's own reason; `ao team list` says the same from the same row."""
    from agentorc import teamrun

    _tmp, fleet = world
    t0, t1 = "2026-10-01T10:00:00Z", "2026-10-01T10:00:40Z"

    def closed(name, by, **kw):
        oow = {"at": t1, "why": "nothing pickable on dev-cadence's ledger\nand the rest"}
        rec = {**badged(name, "ao-grind", state="closed"), "tail": [], "created": t0, "closed_at": t1}
        return {**rec, "out_of_work": oow, "closer": {"by": by, "at": t1}, **kw}

    fleet.sessions = [closed("orc-ao", "orc-ao"), closed("grind-1", "orc-ao")]
    row = next(r for r in uiapp.teams_view(fleet.sessions)["teams"] if r["name"] == "ao-grind")
    assert (row["closed_by"], row["run_seconds"], row["after"]) == ("orc-ao", 40, "40 s")
    assert row["manager_why"] == "nothing pickable on dev-cadence's ledger"
    assert teamrun.wound_down_words(row) == " · after 40 s · by orc-ao — nothing pickable on dev-cadence's ledger"
    html = client.get("/").text
    head = html[html.index('<section class="tgroup" data-team="ao-grind"') :].split('<div class="grid">', 1)[0]
    assert "· after 40 s</span> · by orc-ao<span" in head and "— nothing pickable on dev-cadence&#39;s ledger" in head
    # a person's close, a run of an hour: no *after*, *by you*; a manager that gave no reason, no why
    late = "2026-10-01T11:00:00Z"
    fleet.sessions = [
        {**closed("orc-ao", "person", closed_at=late), "out_of_work": {"at": t1, "why": ""}},
        closed("grind-1", "person", closed_at=late),
    ]
    row = next(r for r in uiapp.teams_view(fleet.sessions)["teams"] if r["name"] == "ao-grind")
    assert row["wound_down"] and row["run_seconds"] == 3600 and teamrun.wound_down_words(row) == " · by you"
    # a record from before the closer: nothing said that the records do not hold
    fleet.sessions = [
        {**badged(n, "ao-grind", state="closed"), "tail": [], "out_of_work": {"at": t1, "why": ""}}
        for n in ("orc-ao", "grind-1")
    ]
    row = next(r for r in uiapp.teams_view(fleet.sessions)["teams"] if r["name"] == "ao-grind")
    assert row["wound_down"] and teamrun.wound_down_words(row) == ""


def test_a_stopped_teams_card_reads_wound_down_where_it_would_have_read_stopped(world, client):
    """The rendered half of the same row: the words a person actually sees. A wound-down team is
    startable like any other — `ao team start` is the restart (§4.9) — so **Start** stays, its
    sessions' cards sit on the team's card, and the header offers the fold."""
    _tmp, fleet = world
    assert ">stopped<" in client.get("/").text

    at = "2026-09-17T20:00:00Z"
    fleet.sessions = [
        {**badged(n, "ao-grind", state="closed"), "tail": [], "out_of_work": {"at": at, "why": "nothing open"}}
        for n in ("orc-ao", "grind-1")
    ]
    html = client.get("/").text
    assert "wound down" in html and ">stopped<" not in html and "by the tick" not in html
    # the manager never declared and rule 9 closed it: the same words, and who ended it (TD-241)
    closed_for = {"why": "finished", "closed_at": at}
    fleet.sessions[0] = {**badged("orc-ao", "ao-grind", state="closed"), "tail": [], "closed_for": closed_for}
    html = client.get("/").text
    assert "wound down" in html and "> · by the tick</span>" in html and ">stopped<" not in html
    head = html[html.index('<section class="tgroup" data-team="ao-grind"') :]
    assert 'data-live="0"' in head[:200]  # what the fold and the quieter card key on
    head, grid = head.split('<div class="grid">', 1)
    assert 'data-fold="ao-grind" data-n="2"' in head and "2 sessions" in head
    assert 'data-team-act="start"' in head and 'data-team-act="stop' not in head
    assert "declared it was out of work" in head  # the hover says why the word is different
    assert "orc-ao" in grid  # the dead cards are the team's


def test_a_concluded_team_is_drawn_like_a_stopped_one_with_start_alone(world, client):
    """TD-099, design §4.5a **team groups** and **Start / Wind down / Stop now**: a live team whose
    every live session is idle and declared stops offering a wind-down that would only wake its
    manager. It reads *concluded <t> ago · restart wanted*, sorts with the stopped teams, and carries
    Start alone, whose confirm names the sessions it closes first. It is **never folded by the
    page** (TD-156 (b)): its idle cards wait for a person's Close, and folded they read as already
    closed — so it offers the fold (TD-194) but opens unfolded, the button drawn open."""
    _tmp, fleet = world
    rw = {"at": "2026-09-22T21:00:00Z", "why": "usage window, resets 06:00", "early": True}
    out = {"at": "2026-09-22T20:00:00Z", "why": "nothing open"}
    fleet.sessions = [
        {**badged("orc-ao", "ao-grind", state="idle"), "tail": [], "restart_wanted": rw},
        {**badged("grind-1", "ao-grind", state="idle"), "tail": [], "out_of_work": out},
        {**badged("adhoc-1", "adhoc"), "tail": []},
    ]
    html = client.get("/").text
    sec = html[html.index('<section class="tgroup" data-team="ao-grind"') :]
    assert 'data-live="2"' in sec[:200]  # what the fold's default keys on: still live, so open
    head = sec[: sec.index('<div class="grid">')]
    assert "concluded" in head and "ago · restart wanted" in head and ">stopped<" not in head
    assert 'data-fold="ao-grind" data-n="2" aria-expanded="true"' in head and "▾ 2 sessions" in head
    assert "foldmail" not in head  # nothing unread
    assert 'data-team-act="start"' in head and 'data-team-act="stop' not in head
    assert "It first closes grind-1, orc-ao" in head  # the confirm names what the Start closes
    assert html.index('data-team="adhoc"') < html.index('data-team="ao-grind"')  # sorted with the stopped
    # every declaration out of work: the header says so
    fleet.sessions[0] = {**badged("orc-ao", "ao-grind", state="idle"), "tail": [], "out_of_work": out}
    assert "ago · out of work" in client.get("/").text
    # a member waiting on the person's answer (TD-274): not concluded, on the page and on /api/teams
    fleet.waiting = {"grind-1": [{"id": "m-1", "ref": "TD-222", "bound": None}]}
    assert "grind-1 waiting on the person: TD-222" in client.get("/").text
    (row,) = [r for r in client.get("/api/teams").json()["teams"] if r["name"] == "ao-grind"]
    assert row["concluded"] is None and row["not_concluded"] == ["grind-1 waiting on the person: TD-222"]
    fleet.waiting = {}
    # one member took a turn: not concluded, and Wind down is back
    fleet.sessions[1]["state"] = "working"
    head = (
        client.get("/").text.split('<section class="tgroup" data-team="ao-grind"', 1)[1].split('<div class="grid">')[0]
    )
    assert (
        "every session still here is idle" not in head and ">Wind down</button>" in head
    )  # the tmp path says concluded
    # …and the header says why there is no Start, a clause per session (TD-241 slice 2)
    assert '<div class="meta notconcluded"' in head and "not concluded: grind-1 working</div>" in head
    assert 'title="Says why this team has no Start yet:' in head
    lead = {"tail": [], "capabilities": ["control"]}  # the manager, as the records say it: never declares
    member = {"tail": [], "controllers": ["orc-ao"]}
    fleet.sessions[0] = {**badged("orc-ao", "ao-grind", state="needs-you"), **lead}
    fleet.sessions[1] = {**badged("grind-1", "ao-grind", state="idle"), **member}
    html = client.get("/").text
    assert "not concluded: orc-ao needs-you · grind-1 idle, not declared</div>" in html
    # a concluded team, and one with nothing live, carry no such line
    fleet.sessions[0] = {**badged("orc-ao", "ao-grind", state="idle"), **lead}
    fleet.sessions[1] = {**badged("grind-1", "ao-grind", state="idle"), **member, "out_of_work": out}
    assert "concluded" in client.get("/").text
    assert "not concluded:" not in client.get("/").text
    fleet.sessions[:2] = [{**badged(n, "ao-grind", state="exited"), "tail": []} for n in ("orc-ao", "grind-1")]
    assert "not concluded:" not in client.get("/").text
    assert 'data-fold="ao-grind"' in head  # a live team folds too (TD-194)


def test_a_live_teams_card_carries_stop_and_stop_now_and_its_fold(world, client):
    """Design §4.5a **team groups** (2026-09-16): the control sits on the thing it stops. Any team
    folds (§4.5a *team card: fold*, TD-194): a live one's button is drawn open, since it opens so."""
    _tmp, fleet = world
    fleet.sessions = [{**badged("orc-ao", "ao-grind"), "tail": []}, {**badged("adhoc-1", "adhoc"), "tail": []}]
    html = client.get("/").text
    head = html[html.index('<section class="tgroup" data-team="ao-grind"') :]
    head = head[: head.index('<div class="grid">')]
    assert 'data-team-act="stop" data-team="ao-grind" title' in head
    assert ">Wind down</button>" in head and ">Stop</button>" not in head  # the label says how it differs from Stop now
    assert 'data-team-act="stopnow" data-team="ao-grind" title' in head
    assert 'data-team-act="start"' not in head
    assert 'data-fold="ao-grind" data-n="1" aria-expanded="true"' in head and "▾ 1 session<" in head
    # A badge with no definition has nothing `ao team start|stop` could read: no control at all.
    adhoc = html[html.index('<section class="tgroup" data-team="adhoc"') :]
    adhoc = adhoc[: adhoc.index('<div class="grid">')]
    assert "data-team-act" not in adhoc


def test_the_state_pill_has_a_glyph_for_every_state_the_view_can_name():
    """Design §4.5a **state icon**: the glyph is CSS keyed on the pill's state class, so a state
    class with no rule is a pill with no icon — pinned here, since there is no JS/CSS harness."""
    css = (pathlib.Path(uiapp.__file__).parent / "static" / "app.css").read_text()
    for cls in ("needs", "limited", "stalled", "working", "idle", "exited", "closed", "unreachable"):
        assert f".pill.s-{cls}::before" in css, cls


# ── Start: the shared planner, every check before any create ──────────────────────────────────


def test_a_start_reads_the_lanes_and_every_lane_empty_asks_start_anyway(world, client):
    """design §4.5a team card **Start**, *What the lanes hold, before the start* (TD-262, built by
    TD-265): the press reads the lanes; every one empty opens the Start anyway / Cancel dialog in
    place of starting, a concluded team's closes as its second line, and a start that reaches the
    page without **Start anyway** is refused before anything is created."""
    tmp_path, fleet = world
    checkout = str(tmp_path / "agentorc")
    some = [{"id": "TD-1", "pickable": "yes", "kind": "build", "owner": ""}]
    fleet.repos = {checkout: {"ledger": {"entries": some}}}
    got = client.get("/api/teams/ao-grind/lanes").json()
    assert (got["line"], got["empty"]) == ("grinder: 1 pickable", False)
    fleet.repos = {checkout: {"ledger": {"entries": [{**some[0], "pickable": "no"}]}}}
    got = client.get("/api/teams/ao-grind/lanes").json()
    assert (got["line"], got["empty"]) == ("grinder: 0", True)
    r = client.post("/api/teams/ao-grind/start", json={})
    assert r.status_code == 409 and r.json()["detail"].startswith("nothing to pick") and not fleet.creates()
    bad = client.post("/api/teams/ao-grind/start", content=b"not json", headers={"content-type": "application/json"})
    assert bad.status_code == 409 and not fleet.creates()  # a body that is not JSON is none: asked, never a 500
    assert client.post("/api/teams/ao-grind/start", json=[1]).status_code == 409
    assert client.post("/api/teams/ao-grind/start", json={"anyway": True}).status_code == 200 and fleet.creates()
    # unreadable: unknown, never empty — no dialog
    fleet.repos = {checkout: {"ledger": {"error": "gone"}}}
    assert client.get("/api/teams/ao-grind/lanes").json()["empty"] is False
    # the dialog and the press: one confirm, the closes in it on a concluded team
    page = client.get("/").text
    assert '<dialog id="startdlg"' in page and ">Start anyway</button>" in page and ">Cancel</button>" in page
    head = uiapp.templates.get_template("group_head.html").render(
        g={
            "team": "t",
            "label": "t",
            "defined": True,
            "stopped": True,
            "members": [],
            "live": 0,
            "concluded": {"names": ["a", "b"], "restart": False},
        },  # fmt: skip
        help_title=lambda k: k,
    )
    assert 'data-closes="a, b"' in head
    js = (Path(__file__).parents[1] / "src" / "agentorc" / "ui" / "static" / "app.js").read_text()
    assert 'if (b && b.dataset.teamAct === "start") return startPress(b);' in js
    assert "Nothing to pick: every member's lane is empty, so the team will wind down as soon as it starts." in js
    assert "if (picks && picks.empty)" in js and "It first closes ${b.dataset.closes}." in js


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
    assert body["manager"] is None and "killed 2 sessions" in body["text"]


def test_stop_sends_the_wrap_up_to_the_members_and_leaves_the_lead_to_the_background(world, client, monkeypatch):
    tmp_path, fleet = world
    later: list[teamrun.Stopping] = []
    monkeypatch.setattr(teamrun, "stop_lead", lambda call, st, **kw: later.append(st) or st)
    fleet.sessions += [badged("orc-ao", "ao-grind"), badged("grind-1", "ao-grind")]
    body = client.post("/api/teams/ao-grind/stop", json={}).json()
    # only the member was sent to in the request: the lead follows once they settle (§4.9)
    assert fleet.sent("send") == ["grind-1"] and not fleet.sent("kill")
    assert [e["name"] for e in body["sessions"]] == ["grind-1"]
    assert body["manager"] == "orc-ao" and "orc-ao follows when they settle" in body["text"]
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
    assert client.post("/api/teams/ao-grind/stop", json={}).json()["manager"] == "orc-ao"
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
    assert first["manager"] == "orc-ao"
    assert second["manager"] is None and "already stopping" in second["text"]
    assert started == ["orc-ao"]  # one task, not two


def test_the_terminal_of_another_hosts_session_is_refused_by_name(world, client):
    """TD-057 step 4a (the 3b leftover): `/term/<id@host>` says what is not built rather than
    leaving it to tmux's miss."""
    _tmp, fleet = world
    fleet.sessions.append({"id": "ao-w@laptop", "name": "w", "state": "working", "host": "laptop", "pane": True})
    with client.websocket_connect("/term/ao-w@laptop") as ws:
        got = ws.receive_bytes()
    assert b"runs on laptop: no terminal reaches it from here" in got


def test_a_container_nodes_session_is_reached_by_docker_exec_and_vs_code_attaches_to_it(world, client, monkeypatch):
    """3c.5: the home put `host_link.reach` on the record when the node dialed in; the Focus
    terminal runs `docker exec … tmux attach` from it and the card's VS Code link attaches to
    that container."""
    tmp_path, fleet = world
    reach = {
        "container": "abc123def456",
        "user": "developer",
        "vscode": "vscode://vscode-remote/attached-container+7b7d/home/x/repo?windowId=_blank",
    }
    fleet.sessions.append(
        {
            "id": "ao-repo-w@cm",
            "name": "w",
            "state": "working",
            "host": "cm",
            "pane": True,
            "dir": "/home/x/repo",
            "kind": "interactive",
            "adapter": "claude-code",
            "tail": [],
            "host_link": {"up": True, "since": "t", "why": "linked", "reach": reach},
        }
    )
    seen = {}

    class NoPty:
        def __init__(self, argv, *, cols, rows):
            seen["argv"] = argv
            raise RuntimeError("no pty in this test")

    monkeypatch.setattr(uiapp, "PtySession", NoPty)
    with client.websocket_connect("/term/ao-repo-w@cm") as ws:
        got = ws.receive_bytes()
    assert b"could not attach a terminal: RuntimeError: no pty in this test" in got
    assert seen["argv"][:6] == ["docker", "exec", "-u", "developer", "-it", "abc123def456"]
    assert seen["argv"][6:10] == ["tmux", "attach", "-t", "=ao-repo-w:"]  # the bare id inside the container
    page = client.get("/").text
    assert 'href="vscode://vscode-remote/attached-container+7b7d/home/x/repo?windowId=_blank"' in page


def test_stop_now_leaves_a_persons_session_and_counts_only_what_it_killed(world, client):
    """Design §4.9 *A person in the team* (TD-173): Stop now kills the workers and names the
    person's session as staying — it is neither killed nor counted."""
    tmp_path, fleet = world
    fleet.sessions += [
        badged("orc-ao", "ao-grind"),
        badged("grind-1", "ao-grind"),
        badged("me", "ao-grind", unattended=False),
    ]
    body = client.post("/api/teams/ao-grind/stop", json={"now": True}).json()
    assert sorted(fleet.sent("kill")) == ["grind-1", "orc-ao"]
    assert "killed 2 sessions" in body["text"]
    assert "your session me stays: a team act never stops an interactive session" in body["text"]


# ── a person in the team (design §4.9 *A person in the team*, TD-173 slice 2) ─────────────────


def _reader_org(tmp_path) -> None:
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["techlead"] = {"name": "tl-ao"}
    doc["roles"] = {"grinder": {"review": {"reader": "techlead", "held": ["src/sessionorc/**"]}}}
    write_org(tmp_path, doc)


def test_a_team_whose_only_live_session_is_a_persons_reads_stopped_and_offers_start(world):
    tmp_path, fleet = world
    rows = uiapp.teams_view([badged("me", "ao-grind", unattended=False)])["teams"]
    assert rows[0]["live"] == 0
    views = [
        {**badged("me", "ao-grind", unattended=False), "rank": 5},
        {**badged("grind-1", "ao-grind", state="exited"), "rank": 5},
    ]
    (g,) = [g for g in uiapp.team_groups(views, rows) if g["team"] == "ao-grind"]
    assert g["stopped"] and g["live"] == 0
    assert [m["name"] for m in g["forget"]] == ["grind-1"]  # never the person's live session
    closed_me = {**badged("me", "ao-grind", state="closed", unattended=False), "rank": 5}
    (g2,) = [x for x in uiapp.team_groups([closed_me, views[1]], rows) if x["team"] == "ao-grind"]
    assert [m["name"] for m in g2["forget"]] == ["grind-1"]  # nor a person's closed one
    org, _ = uiapp.org_here()
    assert teamrun.members_view(org, "ao-grind", [badged("me", "ao-grind", unattended=False)])["live"] is False
    assert g["stays"] == ["your session me stays: a team act never stops an interactive session"]


def test_wind_down_and_stop_now_name_a_persons_session_in_their_confirm(world, client):
    tmp_path, fleet = world
    fleet.sessions += [
        badged("orc-ao", "ao-grind"),
        badged("grind-1", "ao-grind"),
        badged("me", "ao-grind", unattended=False),
    ]
    html = client.get("/").text
    assert (
        'data-confirm="Wind down ao-grind? your session me stays: a team act never stops an interactive session."'
        in html
    )
    assert 'data-confirm="Stop ao-grind now? your session me stays' in html


def test_the_new_session_form_offers_the_teams_and_says_their_reader(world, client):
    tmp_path, fleet = world
    _reader_org(tmp_path)
    fleet.sessions.append({**badged("orc-ao", "ao-grind"), "id": "ao-agentorc-orc-ao"})
    html = client.get("/new").text
    assert '<select class="input" name="team" id="team">' in html
    assert 'value="ao-grind"' in html and 'data-manager="ao-agentorc-orc-ao"' in html
    assert f"data-dirs='[\"{tmp_path / 'agentorc'}\"]'" in html and 'data-roles="grinder"' in html
    got = client.get("/api/team_review", params={"team": "ao-grind", "dir": str(tmp_path / "agentorc")}).json()
    assert got["line"] == "held PRs read by ao-agentorc-tl-ao on src/sessionorc/**"
    write_org(tmp_path, org_doc(tmp_path))  # no seat
    got = client.get("/api/team_review", params={"team": "ao-grind", "dir": str(tmp_path / "agentorc")}).json()
    assert got == {"review": None, "line": "no reader: this team has no techlead seat"}


def test_a_seat_on_call_is_a_controller_on_the_form_and_the_team_pick_ticks_it(world, client):
    """TD-269, built by TD-276: since TD-259 a manager is a seat on call, closed until a reading
    fills it; its id and grant survive the close, so the form lists it and the Team pick ticks it."""
    tmp_path, fleet = world
    _reader_org(tmp_path)
    html = client.get("/new").text
    assert "No session holds the <code>control</code> grant and no seat is on call" in html
    seat = {**badged("orc-ao", "ao-grind", state="closed"), "id": "ao-agentorc-orc-ao", "seat": {"trigger": "team"},
            "capabilities": ["control"]}  # fmt: skip
    fleet.sessions.append(seat)
    html = client.get("/new").text
    assert 'data-manager="ao-agentorc-orc-ao"' in html  # the Team pick ticks it
    box = html.split('value="ao-agentorc-orc-ao"')[1].split("</label>")[0]
    assert "orc-ao" in box and 'class="pill s-oncall"' in box and ">on call</span>" in box
    assert "A seat on call holds it for whoever fills it." in html
    # a closed record that is no seat is no controller, and neither is it the Team pick's manager
    fleet.sessions[-1] = {**seat, "seat": None}
    html = client.get("/new").text
    assert 'data-manager=""' in html and 'value="ao-agentorc-orc-ao"' not in html


def test_start_with_a_team_badges_the_session_and_fills_the_teams_reader(world, client):
    tmp_path, fleet = world
    _reader_org(tmp_path)
    data = {"name": "me", "dir": str(tmp_path / "agentorc"), "team": "ao-grind"}
    r = client.post("/new", data=data, follow_redirects=False)
    assert r.status_code == 303
    (made,) = fleet.creates()
    assert made["team"] == "ao-grind"
    assert made["review"] == {"reader": "techlead", "held": ["src/sessionorc/**"], "bound": "2h"}


def test_under_a_flow_the_form_gives_a_persons_session_the_flows_reader(world, client):
    """§4.9c items 2 and 3 (TD-309 slice 3a): a team running `build-review` holds a person's session
    on the repos' `held:`, not the union of its roles' own `review:`, and the line under the picker
    says so."""
    tmp_path, fleet = world
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"].update(techlead={"name": "tl-ao"}, flows=["build-review"])
    doc["roles"] = {"grinder": {"review": {"reader": "techlead", "held": ["src/agentorc/**"]}}}
    write_org(tmp_path, doc)
    (tmp_path / "agentorc" / ".agentorc.yml").write_text("held: [src/sessionorc/**, docs/briefs/**]\n")
    got = client.get("/api/team_review", params={"team": "ao-grind", "dir": str(tmp_path / "agentorc")}).json()
    assert got["line"] == "held PRs read by ao-agentorc-tl-ao on docs/briefs/**, src/sessionorc/**"
    data = {"name": "me", "dir": str(tmp_path / "agentorc"), "team": "ao-grind"}
    assert client.post("/new", data=data, follow_redirects=False).status_code == 303
    (made,) = fleet.creates()
    assert made["review"] == {"reader": "techlead", "held": ["docs/briefs/**", "src/sessionorc/**"], "bound": "2h"}


def test_start_with_a_team_and_a_role_names_the_teams_seat_and_manager_in_the_brief(world, client):
    """TD-253: the form's Team pick fills the brief's `{techlead}` and `{manager}` slots, stored in
    `prompt_from`; with no team each reads `none`."""
    tmp_path, fleet = world
    _reader_org(tmp_path)
    data = {"name": "grind-9", "dir": str(tmp_path / "agentorc"), "role": "grinder", "team": "ao-grind"}
    assert client.post("/new", data=data, follow_redirects=False).status_code == 303
    (made,) = fleet.creates()
    slots = made["prompt_from"]["slots"]
    assert slots["{techlead}"] == {"text": "ao-agentorc-tl-ao"} and slots["{manager}"] == {"text": "ao-agentorc-orc-ao"}
    assert "Your team's techlead is `ao-agentorc-tl-ao`" in made["prompt"]
    fleet.calls.clear()
    assert client.post("/new", data={**data, "team": ""}, follow_redirects=False).status_code == 303
    slots = fleet.creates()[0]["prompt_from"]["slots"]
    assert slots["{techlead}"] == {"text": "none"} and slots["{manager}"] == {"text": "none"}


def test_start_with_a_team_that_lists_flows_composes_under_its_current_flow(world, client):
    """TD-309 slice 2a (design §4.9c item 5): the form's Team pick hands the role's brief the team's
    current flow — `{flow}` its line, `{stage}` its stage brief as a file slot."""
    tmp_path, fleet = world
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["techlead"] = {"name": "tl-ao"}
    doc["teams"]["ao-grind"]["flows"] = ["build-review"]
    write_org(tmp_path, doc)
    (tmp_path / "agentorc" / ".agentorc.yml").write_text("held: [src/sessionorc/**]\n")
    data = {"name": "grind-9", "dir": str(tmp_path / "agentorc"), "role": "grinder", "team": "ao-grind"}
    assert client.post("/new", data=data, follow_redirects=False).status_code == 303
    (made,) = fleet.creates()
    slots = made["prompt_from"]["slots"]
    assert slots["{stage}"]["file"].endswith("flows/build-review/build.md")
    assert slots["{flow}"]["text"].startswith("build-review: **build** (grinder) → review (ao-agentorc-tl-ao")
    assert "**This team's flow:** build-review: **build**" in made["prompt"]


def test_start_with_a_role_hands_create_what_its_brief_was_made_from(world, client):
    """TD-217 slice 1 (design §6 rule 7): the form's preset brief goes with `prompt_from`, which filled
    again gives the prompt; a prompt the person typed fills nothing and sends none."""
    tmp_path, fleet = world
    data = {"name": "h", "dir": str(tmp_path / "agentorc"), "role": "hunter", "lane": "TD-9"}
    assert client.post("/new", data=data, follow_redirects=False).status_code == 303
    (made,) = fleet.creates()
    base = pathlib.Path(made["prompt_from"]["base"]).read_text(encoding="utf-8")
    for slot, spec in made["prompt_from"]["slots"].items():
        base = base.replace(slot, spec["text"])
    assert base == made["prompt"] and made["prompt_from"]["slots"]["{lane}"] == {"text": "TD-9"}
    fleet.calls.clear()
    assert client.post("/new", data={**data, "prompt": "typed"}, follow_redirects=False).status_code == 303
    (made,) = fleet.creates()
    assert made["prompt"] == "typed" and "prompt_from" not in made


def test_a_placed_teams_host_registry_is_asked_once_per_ttl_and_a_refusal_is_unknown(monkeypatch):
    """Design §4.9 *Where a repo's team lands* (TD-229 slice 3): the page asks the home's `host_repos`
    for a placed team's checkout on its host — on a thread of its own, kept for `PLACE_TTL`, and a
    refusal is `OSError` (the landing's *unknown*), kept as long as an answer."""
    import asyncio

    from agentorc.ui import common

    calls: list[tuple[str, dict]] = []

    def rpc(method: str, **params):
        calls.append((method, params))
        if params["host"] == "down":
            raise RuntimeError("down did not answer: the link dropped")
        if params["host"] == "old":
            return {"host": "old"}  # no list: never read as *holds no repo*
        return {"host": params["host"], "repos": ["/workspaces/sam"]}

    monkeypatch.setattr(common, "_call_sync", rpc)
    monkeypatch.setattr(common, "_place_cache", {})
    monkeypatch.setattr(common, "_place_asking", set())

    async def on_the_loop() -> list[str]:
        return common.repos_of("devenv")  # `org_here` is called on the event loop too

    assert asyncio.run(on_the_loop()) == ["/workspaces/sam"]
    assert common.repos_of("devenv") == ["/workspaces/sam"]
    assert calls == [("host_repos", {"host": "devenv"})]
    for host, why in (("down", "the link dropped"), ("old", "no list of checkouts")):
        for _ in range(2):
            with pytest.raises(OSError, match=why):
                common.repos_of(host)
    assert [p["host"] for _, p in calls] == ["devenv", "down", "old"]
    monkeypatch.setattr(common, "PLACE_TTL", -1.0)  # past its time: asked again
    common.repos_of("devenv")
    assert len(calls) == 4

    # a node that hangs holds a page `PLACE_WAIT` and no longer: unknown, one call out, its answer kept
    import threading

    gate = threading.Event()

    def slow(method: str, **params):
        calls.append((method, params))
        gate.wait(5)
        return {"host": params["host"], "repos": ["/w/sam"]}

    monkeypatch.setattr(common, "_call_sync", slow)
    monkeypatch.setattr(common, "PLACE_TTL", 5.0)
    monkeypatch.setattr(common, "PLACE_WAIT", 0.05)
    for _ in range(2):
        with pytest.raises(OSError, match="hung has not answered"):
            common.repos_of("hung")
    assert [p["host"] for _, p in calls].count("hung") == 1
    gate.set()
    deadline = time.monotonic() + 5
    while "hung" in common._place_asking and time.monotonic() < deadline:
        time.sleep(0.01)
    assert common.repos_of("hung") == ["/w/sam"]


# ── the header's flow (design §4.5a *team groups*, *flow changed — Apply*; §4.9c; TD-309 slice 6b) ──


def _flow_org(tmp_path, flows):
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"].update(techlead={"name": "tl-ao"}, flows=flows)
    write_org(tmp_path, doc)
    (tmp_path / "agentorc" / ".agentorc.yml").write_text("held: [src/sessionorc/**]\n")


def _head(html, team="ao-grind"):
    head = html[html.index(f'<section class="tgroup" data-team="{team}"') :]
    return head[: head.index('<div class="grid">')]


def test_a_teams_header_reads_its_flow_and_strip_and_why_it_cannot_follow_it(world, client):
    tmp_path, fleet = world
    _flow_org(tmp_path, ["build-review"])
    head = _head(client.get("/").text)
    assert "· flow: build-review — build → review → you, through ao-agentorc-tl-ao</span>" in head
    assert "flowcannot" not in head and "data-flow-apply" not in head  # stopped: nothing to compare
    # a team whose current flow needs a role it has none of: the reason, and the flow it could follow
    _flow_org(tmp_path, ["td", "build-review"])
    head = _head(client.get("/").text)
    assert '<div class="meta flowcannot">td cannot be followed by ao-grind: no designer' in head
    assert "(it could follow build-review)</div>" in head
    # …and a listed flow that is not the current one, said too
    _flow_org(tmp_path, ["build-review", "td"])
    head = _head(client.get("/").text)
    assert '<div class="meta flowcannot">td cannot be followed by ao-grind: no designer' in head
    assert "it could follow" not in head
    # a team that lists no flow says none
    write_org(tmp_path, org_doc(tmp_path))
    assert "flow:" not in _head(client.get("/").text)


def test_a_live_team_whose_records_differ_reads_flow_changed_and_apply_relaunches(world, client):
    """§4.9c *Switching*: the header compares a live team's records with its current flow when the
    page is drawn; **Apply** is `ao team flow <team> --apply`, and the flows are read again after it."""
    tmp_path, fleet = world
    _flow_org(tmp_path, ["build-review"])
    fleet.sessions = [
        {**badged("ao-agentorc-grind-1", "ao-grind", state="idle"), "name": "grind-1", "tail": []},
    ]
    head = _head(client.get("/").text)
    # the one live member is relaunched, and the ones the flow needs and nobody runs are started
    assert "grind-1: relaunched — lane none → free-pick; reader none → techlead&#10;" in head
    assert '<span class="meta flowchanged" title="orc-ao: starts under build-review&#10;' in head
    assert 'data-flow-apply="ao-grind"' in head and 'data-confirm="Apply build-review to ao-grind? orc-ao:' in head
    got = client.post("/api/teams/ao-grind/flow/apply").json()
    assert got["ok"] and {d["name"]: d["act"] for d in got["applied"]}["grind-1"] == "relaunch"
    (sent,) = [p for m, p in fleet.calls if m == "relaunch"]
    assert sent["id"] == "ao-agentorc-grind-1" and sorted(sent["launch"]) == ["lane", "prompt", "prompt_from", "review"]
    assert client.post("/api/teams/nobody/flow/apply").status_code == 400


def test_a_live_team_with_no_flow_that_lacks_a_seat_reads_definition_changed(world, client, monkeypatch):
    """§4.9c, §4.5a (TD-399, built by TD-400): a live team that runs no flow is compared on its seats
    alone — the anchor it lacks reads *definition changed* and **Apply** names the start; so does the
    manager on call it has no record of (§6 rule 3, TD-410, built by TD-413)."""
    from agentorc import org as orgmod

    monkeypatch.setattr(orgmod, "ANCHOR_DEFAULT", True)
    tmp_path, fleet = world
    write_org(tmp_path, org_doc(tmp_path))
    fleet.sessions = [{**badged("ao-agentorc-grind-1", "ao-grind", state="idle"), "name": "grind-1", "tail": []}]
    head = _head(client.get("/").text)
    lines = "orc-ao: starts&#10;ao-grind-anchor: starts"
    assert f'<span class="meta flowchanged" title="{lines}">definition changed</span>' in head
    assert (
        'data-confirm="Apply the definition to ao-grind? orc-ao: starts; ao-grind-anchor: starts.">Apply</button>'
        in head
    )
    assert "flow:" not in head and ">flow changed<" not in head


def test_the_flow_pick_lists_the_teams_flows_and_disables_one_it_cannot_follow(world, client):
    """§4.5a team card **Flow** pick (TD-309 slice 4b): on a team that lists more than one flow, the
    current selected, each it cannot follow disabled with the reason; one flow, no pick."""
    tmp_path, fleet = world
    _flow_org(tmp_path, ["build-review", "build", "td"])
    head = _head(client.get("/").text)
    assert 'data-flow-pick="ao-grind" data-was="build-review"' in head
    assert '<option value="build-review" selected>build-review</option><option value="build">build</option>' in head
    assert '<option value="td" disabled title="td cannot be followed by ao-grind: no designer' in head
    _flow_org(tmp_path, ["build-review"])
    assert "data-flow-pick" not in _head(client.get("/").text)


def test_a_flow_pick_writes_the_setting_and_nothing_more(world, client):
    """§4.5a team card **Flow** pick, §4.9c *A switch takes one gate, Apply* (TD-356, TD-359): the pick
    writes `teams.<team>.flow` through `set_settings` and relaunches nothing; its answer carries the
    records' differences, which the toast reads (*— Apply to switch the running team*), and **Apply**
    then moves the team. The preview route is gone with the pick's confirm."""
    tmp_path, fleet = world
    _flow_org(tmp_path, ["build-review", "build"])
    fleet.sessions = [{**badged("ao-agentorc-grind-1", "ao-grind", state="idle"), "name": "grind-1", "tail": []}]
    assert client.get("/api/teams/ao-grind/flow", params={"flow": "build"}).status_code == 405
    no = client.post("/api/teams/ao-grind/flow", json={"flow": "hunt"})
    assert no.status_code == 400 and "lists build-review, build, not 'hunt'" in no.json()["detail"]
    got = client.post("/api/teams/ao-grind/flow", json={"flow": "build"}).json()
    assert ("set_settings", {"teams": {"ao-grind": {"flow": "build"}}}) in fleet.calls
    assert not [m for m, _ in fleet.calls if m in ("relaunch", "create")]  # nothing moves on a pick
    assert got["flow"] == "build" and "grind-1: relaunched — lane none → free-pick" in [
        d["line"] for d in got["differences"]
    ]
    assert "applied" not in got and "not_applied" not in got
    # a flow it cannot follow is refused before anything is written, in the flow's own words
    _flow_org(tmp_path, ["build-review", "td"])
    fleet.calls.clear()
    no = client.post("/api/teams/ao-grind/flow", json={"flow": "td"})
    assert no.status_code == 400 and "td cannot be followed by ao-grind: no designer" in no.json()["detail"]
    assert not [m for m, _ in fleet.calls if m == "set_settings"]


def test_the_settings_pages_flow_is_written_and_nothing_more_on_save(world, client):
    """§4.5a *Settings page: Teams* **flow**: a pick of the team's flows, written on Save and nothing
    more, as the card's Flow pick (TD-356); a flow it cannot follow is refused in place, naming why."""
    from agentorc.ui import settings_page as setmod

    tmp_path, fleet = world
    _flow_org(tmp_path, ["build-review", "td"])
    fleet.sessions = [{**badged("ao-agentorc-grind-1", "ao-grind", state="idle"), "name": "grind-1", "tail": []}]
    rows = [{"name": "build-review", "current": True, "strip": "build → review → you", "cannot": ""}]
    rows.append({"name": "td", "current": False, "strip": "", "cannot": "td cannot be followed by ao-grind"})
    (card,) = setmod.team_cards(uiapp.org_here()[0].teams, {}, flows={"ao-grind": rows})
    assert card["flow"] == "build-review" and card["flow_strip"] == "build → review → you" and card["flows"] == rows
    no = client.post("/api/settings/teams", json={"team": "ao-grind", "flow": "td", "reserve": "5"})
    assert no.status_code == 400 and no.json()["detail"].startswith("flow not set — td cannot be followed")
    assert not [m for m, _ in fleet.calls if m == "set_settings"]  # refused before anything was written
    _flow_org(tmp_path, ["build-review", "build"])
    got = client.post("/api/settings/teams", json={"team": "ao-grind", "flow": "build"}).json()
    assert ("set_settings", {"teams": {"ao-grind": {"flow": "build"}}}) in fleet.calls
    assert not [m for m, _ in fleet.calls if m in ("relaunch", "create")]
    assert got["ok"] and got["pick"]["flow"] == "build" and got["pick"]["differences"]


def test_a_members_flow_mark_says_what_a_switch_did_or_left_on_it(monkeypatch):
    """§4.5a *flow changed — Apply*'s member marks (§4.9c *Switching*, TD-309): *sits out under
    <flow>* winding down or closed for it, *— close it yourself* on a person's session, *flow changed*
    on a node member Apply skipped, and the node's own words on an idle one relaunched."""
    from agentorc.ui import cards

    monkeypatch.setattr(cards, "FLOWS", {"t": "build"})
    monkeypatch.setattr(cards, "FLOW_ACTS", {"p": "left", "n@devenv": "relaunch"})
    mark = lambda **s: (cards.flow_mark({"team": "t", **s}, here="kmaster") or {}).get("text")  # noqa: E731
    assert mark(id="d", sit_out={"at": "x"}) == "sits out under build"
    assert mark(id="d", state="closed", closed_for={"why": "sit_out"}) == "sits out under build"
    assert mark(id="p") == "sits out under build — close it yourself"
    assert mark(id="n@devenv", host="devenv", state="unreachable") == "flow changed"  # Apply skipped it
    assert mark(id="n@devenv", host="devenv", state="idle") is None  # Apply reaches it: nothing to keep
    monkeypatch.setitem(cards.FLOW_ACTS, "d", "start")
    assert mark(id="d", state="closed", closed_for={"why": "sit_out"}) is None  # the flow starts it again
    del cards.FLOW_ACTS["d"]
    # winding down is what it does, ahead of its doing line; the other marks only where none stands
    v = {"state": "working", "pending": {}, "doing": {"text": "pushing", "age": ""}, "ready_ok": False}
    v.update(out_of_work=None, restart_wanted=None, waiting=None, open_work=None, seat=None, brief_changed=None)
    sat = {"text": "sits out under build", "full": "x", "first": True}
    assert cards.card_slot({**v, "flow_mark": sat})["text"] == "sits out under build"
    assert cards.card_slot({**v, "flow_mark": {"text": "flow changed", "full": "x"}})["text"] == "pushing"
    assert mark(id="n2@devenv", host="devenv", relaunch={"at": "x"}, state="idle") == cards.NODE_RESTART
    assert mark(id="n2@devenv", host="devenv", relaunch={"at": "x"}, state="working") is None  # it is told
    assert mark(id="h", host="kmaster", relaunch={"at": "x"}, state="idle") is None  # the home's tick restarts it
    assert mark(id="h") is None


def test_a_member_closed_for_a_sit_out_keeps_its_card_saying_so(world, client):
    tmp_path, fleet = world
    _flow_org(tmp_path, ["build-review"])
    rec = {**badged("ao-agentorc-designer", "ao-grind", state="closed"), "name": "designer", "tail": []}
    rec.update(closed_for={"why": "sit_out"}, closer={"by": "tick", "why": "sit_out"})
    fleet.sessions = [rec, {**badged("ao-agentorc-grind-1", "ao-grind", state="idle"), "name": "grind-1", "tail": []}]
    html = client.get("/").text
    assert "sits out under build-review" in html and "its card stays until Forget · closed by the tick" in html
    # …and a person's session the flow sits out, live: on its compact line, since nothing winds it down
    _flow_org(tmp_path, ["build-review", "td"])
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"].update(techlead={"name": "tl-ao"}, flows=["build-review", "td"])
    doc["teams"]["ao-grind"]["members"].append({"role": "designer", "name": "des"})
    write_org(tmp_path, doc)
    me = {**badged("ao-agentorc-des", "ao-grind", state="idle", unattended=False), "name": "des", "tail": []}
    fleet.sessions = [me, {**badged("ao-agentorc-grind-1", "ao-grind", state="idle"), "name": "grind-1", "tail": []}]
    assert "interactive · sits out under build-review — close it yourself" in client.get("/").text
