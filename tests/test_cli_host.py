"""`ao host rebuild|forget` never ends a live session without being told to (design §4.4a, TD-316):
the refusal that names them by team, `--wind-down` and `--force`, an unreachable home. The
container itself is faked at `host_up` / `host_forget`: what is checked is whether it was touched."""

import json

import pytest

from agentorc import cli, teamrun
from sessionorc import containers
from sessionorc.client import AgentUnavailable

pytestmark = pytest.mark.unit

CLEAN = {"dirty": 0, "unpushed": 0}


def rec(id, state, team="", host="cm", unattended=True, git=CLEAN):
    return {"id": id, "name": id.split("@")[0], "state": state, "team": team, "host": host,
            "unattended": unattended, "git": dict(git)}  # fmt: skip


@pytest.fixture
def world(monkeypatch):
    """A home with records, a container that records whether it was rebuilt or forgotten."""
    w = {"records": [], "touched": [], "calls": [], "down": False}

    def fake_call(method, **params):
        w["calls"].append((method, params))
        if w["down"]:
            raise AgentUnavailable("no host agent")
        if method == "list":
            return [dict(s) for s in w["records"]]
        if method == "close":
            next(s for s in w["records"] if s["id"] == params["id"])["state"] = "closed"
            return {}
        if method == "forget_host":
            return {"closed": 0}
        raise AssertionError(method)

    def host_up(name, rebuild=False):
        w["touched"].append(("rebuild" if rebuild else "up", name))
        return {"node": name, "container": "new123456789abc", "user": "dev", "wheel": "w.whl", "started": True,
                "pid": None}  # fmt: skip

    def host_forget(name, purge=False):
        w["touched"].append(("forget", name))
        return {"node": name, "container": "abc", "entry_removed": True, "volume_kept": not purge}

    monkeypatch.setattr(cli, "call_sync", fake_call)
    monkeypatch.setattr(containers, "host_up", host_up)
    monkeypatch.setattr(containers, "host_forget", host_forget)
    return w


def run(*argv):
    return cli.main(["--json", "host", *argv])


def test_live_on_node_reads_the_nodes_records_that_are_not_ended():
    records = [rec("a@cm", "idle"), rec("b@cm", "exited"), rec("c@cm", "closed"), rec("d", "working", host="km")]
    assert [s["id"] for s in containers.live_on_node(records, "cm")] == ["a@cm"]
    named = containers.by_team([rec("a@cm", "working", team="cm-grind"), rec("z@cm", "idle")])
    assert named == "no team: z (idle); team cm-grind: a (working)"


@pytest.mark.parametrize("action", ["rebuild", "forget"])
def test_a_node_with_no_live_session_goes_on_as_before(world, capsys, action):
    world["records"] = [rec("a@cm", "exited", team="cm-grind"), rec("k", "working", host="kmaster")]
    assert run(action, "cm") == 0
    assert world["touched"] == [(action, "cm")]
    assert json.loads(capsys.readouterr().out)["ended"] == []


@pytest.mark.parametrize("action", ["rebuild", "forget"])
def test_live_sessions_refuse_and_name_them_by_team_and_the_container_is_unchanged(world, capsys, action):
    world["records"] = [rec("g@cm", "working", team="cm-grind"), rec("m@cm", "idle", team="cm-grind")]
    assert run(action, "cm") == 1
    assert world["touched"] == []
    out = json.loads(capsys.readouterr().out)
    assert "team cm-grind: g (working), m (idle)" in out["error"] and "unchanged" in out["error"]
    assert out["sessions"] == ["g@cm", "m@cm"] and "--wind-down" in out["hint"]


@pytest.mark.parametrize("action", ["rebuild", "forget"])
def test_force_goes_on_and_says_which_sessions_it_ended(world, capsys, action):
    world["records"] = [rec("g@cm", "working", team="cm-grind")]
    assert cli.main(["host", action, "cm", "--force"]) == 0
    assert world["touched"] == [(action, "cm")]
    assert "ended with the container, no wrap-up: team cm-grind: g (working)" in capsys.readouterr().out


def test_an_unreachable_home_refuses_and_force_still_goes_on(world, capsys):
    world["down"] = True
    assert run("rebuild", "cm") == 1
    assert world["touched"] == [] and "cannot ask the home" in json.loads(capsys.readouterr().out)["error"]
    assert run("rebuild", "cm", "--force") == 0
    assert world["touched"] == [("rebuild", "cm")]


@pytest.fixture
def stops(world, monkeypatch):
    """`ao team stop`'s two halves, faked: the wrap-up settles each of the team's records to idle."""
    stopped = []
    monkeypatch.setattr(cli, "_org_here", lambda: object())

    def stop_members(call, org, team, now=False, caller=None):
        stopped.append(team)
        return team

    def stop_lead(call, team, timeout=0, close=False):
        assert close is True
        for s in world["records"]:
            if s["team"] == team and s["state"] == "working":
                s["state"] = "idle"

    monkeypatch.setattr(teamrun, "stop_members", stop_members)
    monkeypatch.setattr(teamrun, "stop_lead", stop_lead)
    monkeypatch.setattr(teamrun, "wait_settled", lambda call, ids, timeout: {})
    return stopped


def test_wind_down_stops_each_team_closes_what_settled_clean_and_rebuilds(world, stops, capsys):
    world["records"] = [
        rec("g@cm", "working", team="cm-grind"),
        rec("m@cm", "idle", team="cm-grind"),
        rec("d@cm", "working", team="cm-design"),
        rec("lead", "working", team="cm-grind", host="kmaster"),  # the lead elsewhere is the stop's own
    ]
    assert run("rebuild", "cm", "--wind-down") == 0
    assert stops == ["cm-design", "cm-grind"]
    assert world["touched"] == [("rebuild", "cm")]
    closed = [p["id"] for m, p in world["calls"] if m == "close"]
    assert sorted(closed) == ["d@cm", "g@cm", "m@cm"]


def test_wind_down_refuses_after_when_a_session_holds_work_and_the_container_is_unchanged(world, stops, capsys):
    world["records"] = [rec("g@cm", "working", team="cm-grind", git={"dirty": 2, "unpushed": 0})]
    assert run("forget", "cm", "--wind-down") == 1
    assert stops == ["cm-grind"] and world["touched"] == []
    out = json.loads(capsys.readouterr().out)
    assert "still live there: team cm-grind: g (idle)" in out["error"] and out["sessions"] == ["g@cm"]


@pytest.mark.parametrize(
    "stray", [rec("s@cm", "idle"), rec("p@cm", "idle", team="cm-grind", unattended=False)], ids=["no team", "person"]
)
def test_wind_down_refuses_before_sending_anything_for_a_session_no_team_stops(world, stops, capsys, stray):
    world["records"] = [rec("g@cm", "working", team="cm-grind"), stray]
    assert run("rebuild", "cm", "--wind-down") == 1
    assert stops == [] and world["touched"] == []
    assert stray["id"] in json.loads(capsys.readouterr().out)["sessions"]
