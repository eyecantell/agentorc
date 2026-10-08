"""The anchor seat's definition and its start (design §4.9b *The anchor seat*, §4.9 `anchor`, TD-385):
a team that writes no `anchor:` has one, `<team>-anchor`, role `anchor`, in its home repo's main
checkout with the trigger `work`; `anchor: false` has none; one per repo across the org. The tick's
fill (TD-386) and the surface's words (TD-387) are tested where they are built."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from agentorc import org as orgmod
from agentorc import orgcheck, teamrun, teams

pytestmark = pytest.mark.unit

HOST = "kmaster"


@pytest.fixture(autouse=True)
def _anchor_on(monkeypatch):
    """The shipped default, which tests/conftest.py turns off for every other test."""
    monkeypatch.setattr(orgmod, "ANCHOR_DEFAULT", True)


def _org(tmp_path: Path, monkeypatch, teams_doc: dict, *, repos=("alpha",)) -> orgmod.Org:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    for r in repos:
        (tmp_path / r / ".git").mkdir(parents=True, exist_ok=True)
    doc = {"projects": {"p": {"repos": {r: {HOST: str(tmp_path / r)} for r in repos}}}, "teams": teams_doc}
    (home / "org.yml").write_text(yaml.safe_dump(doc))
    return orgmod.load()


def _team(home: str = "", **extra) -> dict:
    at = {"home": home} if home else {}
    return {
        "projects": ["p"],
        "manager": {"role": "manager", "name": "lead", **at},
        "techlead": {"name": "tl", **at},
        "members": [{"role": "grinder", "name": "g", **at}],
        **extra,
    }


def test_the_shipped_default_is_a_seat_on_every_team():
    # §4.9b: *a team has an anchor seat as it has a manager: by default* — the suite's off is the suite's
    assert re.search(r"^ANCHOR_DEFAULT = True$", Path(orgmod.__file__).read_text(), re.M)


def test_a_team_that_writes_nothing_has_the_seat_and_false_has_none(tmp_path, monkeypatch):
    org = _org(tmp_path, monkeypatch, {"t": _team(), "off": _team(anchor=False)}, repos=("alpha",))
    seat = org.teams["t"].anchor
    assert seat is not None and seat.name == "t-anchor" and seat.home == "alpha" and seat.implied
    assert org.teams["off"].anchor is None
    assert org.teams["t"].session_names() == ["lead", "tl", "t-anchor", "g"]


def test_a_written_anchor_takes_its_keys_and_refuses_any_other(tmp_path, monkeypatch):
    org = _org(
        tmp_path,
        monkeypatch,
        {"t": _team("alpha", anchor={"name": "keeper", "home": "beta", "profile": "x", "brief": "docs/a.md"})},
        repos=("alpha", "beta"),
    )
    seat = org.teams["t"].anchor
    assert (seat.name, seat.home, seat.profile, seat.brief, seat.implied) == ("keeper", "beta", "x", "docs/a.md", False)
    for bad, said in (
        ({"lane": ["free-pick"]}, r"unknown key\(s\) \['lane'\]"),
        ({"role": "grinder"}, r"unknown key\(s\) \['role'\]"),
        ("yes", "must be a mapping"),
    ):
        with pytest.raises(ValueError, match=said):
            _org(tmp_path, monkeypatch, {"t": _team(anchor=bad)})
    seats = [{"name": "a2", "role": "anchor", "trigger": "asks"}]
    with pytest.raises(ValueError, match="own `techlead:` and `anchor:` keys"):
        _org(tmp_path, monkeypatch, {"t": _team(seats=seats)})


def test_home_is_the_managers_and_required_only_where_written(tmp_path, monkeypatch):
    two = ("alpha", "beta")
    doc = _team("beta")
    assert _org(tmp_path, monkeypatch, {"t": doc}, repos=two).teams["t"].anchor.home == "beta"
    # a person leads across two repos: nothing to tell the home from, and nothing written — no seat
    person = {"projects": ["p"], "manager": {"role": "person"}, "members": [{"role": "grinder", "home": "alpha"}]}
    assert _org(tmp_path, monkeypatch, {"t": person}, repos=two).teams["t"].anchor is None
    with pytest.raises(ValueError, match="anchor.home is required"):
        _org(tmp_path, monkeypatch, {"t": {**person, "anchor": {"name": "k"}}}, repos=two)


def test_the_plan_launches_the_seat_after_the_techlead_in_the_main_checkout(tmp_path, monkeypatch):
    org = _org(tmp_path, monkeypatch, {"t": _team()})
    p = teams.plan(org, "t", HOST)
    assert [x.name for x in p.launches] == ["lead", "tl", "t-anchor", "g"]
    x = p.anchor
    assert (x.role, x.seat, x.lane, x.grants, x.trigger) == ("anchor", True, ["anchor"], [], {"trigger": "work"})
    params = x.create_params(["ao-alpha-lead"])
    assert params["worktree"] is None and params["dir"] == str(tmp_path / "alpha")  # the checkout itself
    assert params["controllers"] == ["ao-alpha-lead"] and params["seat"] == {"trigger": "work"}
    assert p.members[0].create_params([])["worktree"] == "g"  # everyone else keeps a worktree
    # the brief: its lane, the checkout rule and the never list, the techlead and manager named
    assert "## Lane: anchor" in x.prompt and "git pull --ff-only" in x.prompt
    assert "never promote" in x.prompt and "`org.yml`" in x.prompt and "ao progress none" in x.prompt
    assert "ao-alpha-tl" in x.prompt and "ao-alpha-lead" in x.prompt
    assert "{repo}" not in x.prompt and "{lane}" not in x.prompt
    assert teams.plan(_org(tmp_path, monkeypatch, {"t": _team(anchor=False)}), "t", HOST).anchor is None


def test_two_teams_on_one_repo_refuse_the_second_naming_the_first(tmp_path, monkeypatch):
    org = _org(tmp_path, monkeypatch, {"first": _team(), "second": _team()})
    teams.plan(org, "first", HOST)
    with pytest.raises(teams.TeamError, match=r"team second: first already has the anchor seat of alpha"):
        teams.plan(org, "second", HOST)
    got = orgcheck.check(org, [], HOST, [str(tmp_path / "alpha")], [])
    assert not got["ok"] and any("first already has the anchor seat of alpha" in x for x in got["lacks"])
    org = _org(tmp_path, monkeypatch, {"first": _team(), "second": _team(anchor=False)})
    assert teams.plan(org, "second", HOST).anchor is None
    assert orgcheck.check(org, [], HOST, [str(tmp_path / "alpha")], [])["ok"]


def _fake(occupants: list[str]):
    calls: list[tuple[str, dict]] = []

    def call(method, **params):
        calls.append((method, params))
        if method == "name_check":
            return {"name": params["name"], "verdict": "free"}
        if method == "occupancy":
            return {"dir": params["dir"], "occupants": list(occupants), "git": True}
        if method == "create":
            return {"id": f"ao-alpha-{params['name']}", "name": params["name"]}
        raise AssertionError(method)

    return call, calls


def test_a_start_creates_the_seat_after_the_techlead_under_the_manager(tmp_path, monkeypatch):
    org = _org(tmp_path, monkeypatch, {"t": _team()})
    call, calls = _fake([])
    _, out = teamrun.start(call, org, "t", HOST)
    made = [p for m, p in calls if m == "create"]
    assert [p["name"] for p in made] == ["lead", "tl", "t-anchor", "g"]
    seat = made[2]
    assert seat["worktree"] is None and seat["controllers"] == ["ao-alpha-lead"] and seat["role"] == "anchor"
    assert ("occupancy", {"dir": str(tmp_path / "alpha")}) in calls


def test_a_held_checkout_starts_the_team_without_the_seat_and_says_who_holds_it(tmp_path, monkeypatch):
    org = _org(tmp_path, monkeypatch, {"t": _team()})
    call, calls = _fake(["claude-1 (claude-code, outside agentorc)"])
    _, out = teamrun.start(call, org, "t", HOST)
    assert [p["name"] for m, p in calls if m == "create"] == ["lead", "tl", "g"]
    (said,) = [n for n in out["notes"] if n.startswith("t-anchor")]
    assert "held by claude-1 (claude-code, outside agentorc)" in said and "next Start" in said


def test_ao_team_list_names_the_seat_and_reads_it_as_a_seat(tmp_path, monkeypatch):
    org = _org(tmp_path, monkeypatch, {"t": _team(), "off": _team(anchor=False)}, repos=("alpha",))
    rows = {r["name"]: r for r in teamrun.rows(org, [])}
    assert rows["t"]["anchor"] == {"name": "t-anchor", "home": "alpha"} and rows["off"]["anchor"] is None
    assert {"role": "anchor", "names": ["t-anchor"], "seat": True} in rows["t"]["roles"]
    rec = {"id": "ao-alpha-t-anchor", "name": "t-anchor", "team": "t", "state": "closed", "seat": {"trigger": "work"}}
    assert teamrun.seat_ids(org, [rec]) == {"ao-alpha-t-anchor": orgmod.ANCHOR_WHEN}
