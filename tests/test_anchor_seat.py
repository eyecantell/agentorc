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


def _fake(held: dict | None = None):
    calls: list[tuple[str, dict]] = []

    def call(method, **params):
        calls.append((method, params))
        if method == "name_check":
            return {"name": params["name"], "verdict": "free"}
        if method == "checkout_held":
            return {"dir": params["dir"], "host": params["host"] or HOST, "held": held}
        if method == "create":
            return {"id": f"ao-alpha-{params['name']}", "name": params["name"]}
        raise AssertionError(method)

    return call, calls


def test_a_start_creates_the_seat_after_the_techlead_under_the_manager(tmp_path, monkeypatch):
    org = _org(tmp_path, monkeypatch, {"t": _team()})
    call, calls = _fake()
    _, out = teamrun.start(call, org, "t", HOST)
    made = [p for m, p in calls if m == "create"]
    assert [p["name"] for p in made] == ["lead", "tl", "t-anchor", "g"]
    seat = made[2]
    assert seat["worktree"] is None and seat["controllers"] == ["ao-alpha-lead"] and seat["role"] == "anchor"
    assert "held" not in seat and "held_reason" not in seat
    # the fill's own reading (TD-395), never occupancy alone
    assert ("checkout_held", {"host": "", "dir": str(tmp_path / "alpha")}) in calls
    assert not any(m in ("occupancy", "host_occupancy") for m, _ in calls)


def test_a_held_checkout_starts_the_team_and_writes_the_seat_alone_saying_who_holds_it(tmp_path, monkeypatch):
    """TD-386: the seat's record is written `held` — closed, no pane — for rule 3 to fill once free."""
    org = _org(tmp_path, monkeypatch, {"t": _team()})
    held = {"by": "claude-1", "why": "held by claude-1 (claude-code, outside agentorc)"}
    call, calls = _fake(held)
    _, out = teamrun.start(call, org, "t", HOST)
    made = [p for m, p in calls if m == "create"]
    assert [p["name"] for p in made] == ["lead", "tl", "t-anchor", "g"]
    assert made[2].get("held") is True and not any(p.get("held") for p in made if p["name"] in ("tl", "g"))
    assert made[2]["held_reason"] == held
    # the manager on call is written held too, with nothing holding it (§6 rule 3, TD-410)
    assert made[0].get("held") is True and "held_reason" not in made[0]
    (said,) = [n for n in out["notes"] if n.startswith("t-anchor")]
    assert "held by claude-1 (claude-code, outside agentorc)" in said and "filled once the checkout is free" in said


@pytest.mark.parametrize("why", ["1 file uncommitted", "branch td-x", "branch td-x, 2 files uncommitted"])
def test_a_dirty_or_off_branch_checkout_writes_the_seat_held_saying_why(tmp_path, monkeypatch, why):
    """TD-395 (§4.9b *The anchor seat*): a free checkout the fill would wait on — files uncommitted, or
    another branch checked out — is a held Start too: no pane over the person's work, the reason kept."""
    org = _org(tmp_path, monkeypatch, {"t": _team()})
    call, calls = _fake({"by": "checkout", "why": why})
    _, out = teamrun.start(call, org, "t", HOST)
    made = {p["name"]: p for m, p in calls if m == "create"}
    assert made["t-anchor"]["held"] is True and made["t-anchor"]["held_reason"] == {"by": "checkout", "why": why}
    assert not made["g"].get("held")  # the rest of the team starts
    (said,) = [n for n in out["notes"] if n.startswith("t-anchor")]
    assert said.startswith(f"t-anchor waits: {why}") and "filled once the checkout is free" in said


def test_ao_team_list_names_the_seat_and_reads_it_as_a_seat(tmp_path, monkeypatch):
    org = _org(tmp_path, monkeypatch, {"t": _team(), "off": _team(anchor=False)}, repos=("alpha",))
    rows = {r["name"]: r for r in teamrun.rows(org, [])}
    assert rows["t"]["anchor"] == {"name": "t-anchor", "home": "alpha"} and rows["off"]["anchor"] is None
    assert {"role": "anchor", "names": ["t-anchor"], "seat": True} in rows["t"]["roles"]
    rec = {"id": "ao-alpha-t-anchor", "name": "t-anchor", "team": "t", "state": "closed", "seat": {"trigger": "work"}}
    assert teamrun.seat_ids(org, [rec]) == {"ao-alpha-t-anchor": orgmod.ANCHOR_WHEN}


def test_one_per_repo_is_per_host_and_a_team_that_lands_nowhere_holds_nothing(tmp_path, monkeypatch):
    # review of #1245: the same repo name on two hosts is two checkouts, and a repo's team whose
    # landing cannot be told starts nowhere
    org = _org(tmp_path, monkeypatch, {"here": _team(), "there": _team(host="nodeb")})
    assert org.anchor_first(org.teams["there"]) is None
    org = _org(tmp_path, monkeypatch, {"lost": _team(), "found": _team()})
    org.unlanded["lost"] = "its landing cannot be told"
    assert org.anchor_first(org.teams["found"]) is None


def _running(p: teams.Plan) -> list[dict]:
    """A live record of every launch but the anchor's: the team as it ran before it had the seat."""
    rec = {"team": "t", "state": "working"}
    return [
        {**rec, "id": f"ao-alpha-{x.name}", "name": x.name, "lane": x.lane, "review": x.review}
        for x in p.launches
        if not x.in_checkout
    ]


def test_a_seat_the_run_lacks_is_a_start_under_a_flow_or_none_and_one_with_a_record_is_not(tmp_path, monkeypatch):
    """TD-400 (§4.9c *A seat the definition names that the run has no record of is a difference too*):
    the anchor a team predates reads `start`, a seat, under a flow or with none; a record of it — live,
    closed between fills, or held — is nothing; a team with no flow compares its seats alone."""
    org = _org(tmp_path, monkeypatch, {"t": _team()})
    p = teams.plan(org, "t", HOST)
    live = _running(p)
    assert p.flow is None
    (d,) = teamrun.differences(p, live)
    assert (d.name, d.act, d.seat, d.line(None)) == ("t-anchor", "start", True, "t-anchor: starts")
    # the techlead too, and a member a team with no flow lacks is no difference (no flow compiles it)
    lacking = [r for r in live if r["name"] not in ("tl", "g")]
    assert [x.name for x in teamrun.differences(p, lacking)] == ["tl", "t-anchor"]
    for state in ("working", "closed"):
        rec = {"id": "ao-alpha-t-anchor", "name": "t-anchor", "team": "t", "state": state, "seat": {"trigger": "work"}}
        assert teamrun.differences(p, [*live, rec]) == []
    # under a flow: the same seat start, beside whatever the flow reads of the members
    p.flow = "td"
    starts = [d for d in teamrun.differences(p, live) if d.act == "start"]
    assert [(d.name, d.seat) for d in starts] == [("t-anchor", True)]
    assert starts[0].line("td") == "t-anchor: starts under td"
    # nothing live: nothing to compare
    assert teamrun.differences(p, [{**r, "state": "exited"} for r in live]) == []


def test_the_mark_reads_definition_changed_where_seats_are_all_that_differ_or_no_flow_runs():
    seat = {"name": "t-anchor", "act": "start", "seat": True, "line": "t-anchor: starts"}
    member = {"name": "g", "act": "relaunch", "seat": False, "line": "g: relaunched — lane a → b"}
    assert teamrun.definition_changed([seat], "td") and teamrun.definition_changed([member], None)
    assert not teamrun.definition_changed([seat, member], "td") and not teamrun.definition_changed([], None)
    from agentorc.ui.repo import flow_head

    assert flow_head({"flow": None, "differences": [seat]}, live=True)["definition_changed"] is True
    assert flow_head({"flow": None, "differences": [seat]}, live=False)["flow_changed"] == []
    head = flow_head({"flow": "td", "flows": [], "differences": [seat, member]}, live=True)
    assert head["flow_changed"] == [seat, member] and head["definition_changed"] is False


def _applying(tmp_path, monkeypatch, held):
    org = _org(tmp_path, monkeypatch, {"t": _team()})
    p = teams.plan(org, "t", HOST)
    live = _running(p)
    calls: list[tuple[str, dict]] = []

    def call(method, **params):
        calls.append((method, params))
        if method == "list":
            return live
        if method == "name_check":
            return {"name": params["name"], "verdict": "free"}
        if method == "checkout_held":
            return {"held": held}
        if method == "create":
            return {"id": f"ao-alpha-{params['name']}", "name": params["name"]}
        raise AssertionError(method)

    return org, call, calls


def test_apply_creates_the_anchor_a_team_lacks_held_with_why_while_its_checkout_is_not_free(tmp_path, monkeypatch):
    """TD-400: Apply's start of the anchor reads `checkout_held` first — not free, `create` with `held`
    and the reason, the line saying why; free, the plain create in the checkout, under the manager."""
    held = {"by": "ao-alpha-paul", "why": "held by ao-alpha-paul (working)"}
    org, call, calls = _applying(tmp_path, monkeypatch, held)
    got = teamrun.apply(call, org, "t", HOST)
    (made,) = [p for m, p in calls if m == "create"]
    assert made["name"] == "t-anchor" and made["held"] is True and made["held_reason"] == held
    assert made["controllers"] == ["ao-alpha-lead"] and made["worktree"] is None
    assert [r["line"] for r in got["applied"]] == ["t-anchor: waits — held by ao-alpha-paul (working)"]
    assert ("checkout_held", {"host": "", "dir": str(tmp_path / "alpha")}) in calls  # this host's, as start asks
    org, call, calls = _applying(tmp_path, monkeypatch, None)
    got = teamrun.apply(call, org, "t", HOST)
    (made,) = [p for m, p in calls if m == "create"]
    assert "held" not in made and [r["line"] for r in got["applied"]] == ["t-anchor: starts"]


def test_a_start_writes_the_manager_on_call_held_and_its_members_name_it(tmp_path, monkeypatch, capsys):
    """TD-413 slice 1 (§6 rule 3 *A Start writes the seat and never fills it*, TD-410): the manager
    on call is created with `held` and no reason, before the rest, and every other record names its id
    in `controllers`; `ao team start` says *on call — comes when a member needs a reading* for it; a
    standing manager (`on_call: false`) is created live as before."""
    org = _org(
        tmp_path,
        monkeypatch,
        {"t": _team(), "s": _team(manager={"role": "manager", "name": "boss", "on_call": False}, anchor=False)},
    )
    call, calls = _fake()
    _, out = teamrun.start(call, org, "t", HOST)
    made = [p for m, p in calls if m == "create"]
    assert made[0]["name"] == "lead" and made[0]["held"] is True and "held_reason" not in made[0]
    assert made[0]["seat"]["trigger"] == "team" and made[0]["unattended"] is True
    assert all(p["controllers"] == ["ao-alpha-lead"] for p in made[1:]) and out["manager"] == "ao-alpha-lead"
    calls.clear()
    teamrun.start(call, org, "s", HOST)
    (boss,) = [p for m, p in calls if m == "create" and p["name"] == "boss"]
    assert "held" not in boss
    # a manager on call the definition asks to be watched starts live: `create` writes a held seat unattended only
    watched = _org(tmp_path, monkeypatch, {"w": _team(manager={"role": "manager", "name": "eye", "unattended": False})})
    calls.clear()
    teamrun.start(call, watched, "w", HOST)
    (eye,) = [p for m, p in calls if m == "create" and p["name"] == "eye"]
    assert eye["unattended"] is False and "held" not in eye
    from agentorc import cli

    org = _org(tmp_path, monkeypatch, {"t": _team()})
    p = teams.plan(org, "t", HOST)
    lead = {"id": "ao-alpha-lead", "name": "lead", "state": "closed", "dir": "/d"}
    assert (
        cli._team_line(lead, "t", p)
        == "ao-alpha-lead  manager manager  /d  on call — comes when a member needs a reading"
    )
    assert cli._team_line({**lead, "state": "working"}, "t", p) == "ao-alpha-lead  manager manager  /d"


def test_apply_creates_a_manager_on_call_the_run_lacks_held(tmp_path, monkeypatch):
    """TD-413 slice 1 (§4.9c, TD-410): a live team whose run has no manager record reads it as a seat
    to start, and Apply writes it held, as a Start does, the line saying when it comes; a member Apply
    starts beside a manager closed on call names it as its controller."""
    org, call, calls = _applying(tmp_path, monkeypatch, None)
    p = teams.plan(org, "t", HOST)
    live = [r for r in _running(p) if r["name"] != "lead"]
    original = call

    def without_lead(method, **params):
        return live if method == "list" else original(method, **params)

    got = teamrun.apply(without_lead, org, "t", HOST)
    made = {p["name"]: p for m, p in calls if m == "create"}
    assert made["lead"]["held"] is True and "held_reason" not in made["lead"] and made["lead"]["controllers"] == []
    assert "lead: on call — comes when a member needs a reading" in [r["line"] for r in got["applied"]]
    # the manager closed between fills: a seat Apply starts names it, as a live one is named
    calls.clear()
    closed = [
        *live,
        {"team": "t", "state": "closed", "id": "ao-alpha-lead", "name": "lead", "seat": {"trigger": "team"}},
    ]
    teamrun.apply(lambda method, **params: closed if method == "list" else original(method, **params), org, "t", HOST)
    (anchor,) = [p for m, p in calls if m == "create"]
    assert anchor["name"] == "t-anchor" and anchor["controllers"] == ["ao-alpha-lead"]


def test_ao_team_list_and_flow_say_definition_changed_and_apply_says_the_definition(tmp_path, monkeypatch, capsys):
    """TD-402 (§4.5a *flow changed — Apply*, §4.7): a live team with no flow that lacks its anchor reads
    *definition changed — Apply* with the seat's line in `ao team list` and `ao team flow <team>`, and
    `--apply` says *applied the definition:*, never a flow's name it does not have."""
    from agentorc import cli

    org, call, calls = _applying(tmp_path, monkeypatch, None)
    monkeypatch.setattr(cli.hosts, "local_host", lambda: cli.hosts.Host(name=HOST, vscode_host=HOST, local=True))
    monkeypatch.setattr(cli, "_org_here", lambda: org)
    monkeypatch.setattr(
        cli, "call_sync", lambda method, **params: {} if method in ("host", "repos") else call(method, **params)
    )
    head = "definition changed — Apply (ao team flow t --apply):"
    assert cli.main(["team", "list"]) == 0
    lines = [x.strip() for x in capsys.readouterr().out.splitlines()]
    assert lines[lines.index(head) + 1] == "t-anchor: starts"
    assert cli.main(["team", "flow", "t"]) == 0
    assert capsys.readouterr().out.splitlines()[-2:] == [head, "  t-anchor: starts"]
    assert cli.main(["team", "flow", "t", "--apply"]) == 0
    assert capsys.readouterr().out.splitlines()[-2:] == ["applied the definition:", "  t-anchor: starts"]
    assert [p["name"] for m, p in calls if m == "create"] == ["t-anchor"]


def test_members_and_ao_team_list_name_the_seat(tmp_path, monkeypatch, capsys):
    """TD-387 (§4.5a *card: on call — the anchor seat's words*): Members… lists the seat beside the
    techlead, with no Remove (it is no member entry), and `ao team list` names it."""
    org = _org(tmp_path, monkeypatch, {"t": _team(), "off": _team(anchor=False)})
    rec = {"id": "ao-alpha-t-anchor", "name": "t-anchor", "team": "t", "state": "closed", "unattended": True}
    v = teamrun.members_view(org, "t", [rec])
    assert v["anchor"] == {"name": "t-anchor", "id": "ao-alpha-t-anchor", "state": "closed"}
    assert "t-anchor" not in [e.get("name") for e in v["members"]]
    assert teamrun.members_view(org, "off", [])["anchor"] is None
    js = (Path(__file__).parents[1] / "src/agentorc/ui/static/app.js").read_text()
    assert '<span class="meta">anchor seat</span> ${who(v.anchor)}' in js
    from agentorc import cli

    def call(method, **params):
        if method == "list":
            return [rec]
        raise cli.AgentError(f"{method}: not in this test")

    monkeypatch.setattr(cli, "_org_here", lambda: org)
    monkeypatch.setattr(cli, "call_sync", call)
    monkeypatch.setattr(teamrun, "flow_view", lambda *a, **k: {})
    assert cli.main(["team", "list"]) == 0
    out = {line.split()[0]: line for line in capsys.readouterr().out.splitlines() if line.strip()}
    assert "techlead: tl  anchor: t-anchor  " in out["t"] and "anchor:" not in out["off"]


def test_a_seat_on_a_node_asks_that_hosts_occupancy_and_a_held_one_is_written_alone(tmp_path, monkeypatch):
    """TD-391, TD-395: a team whose `host:` is a node asks the one reading (`checkout_held`) for that
    host's checkout — which reads its occupancy there, never this host's (tests/test_anchor_tick.py) —
    and held there, the team starts and the seat is the `held` create."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    doc = {"projects": {"p": {"repos": {"alpha": {"nodeb": "/srv/alpha"}}}}, "teams": {"t": _team(host="nodeb")}}
    (home / "org.yml").write_text(yaml.safe_dump(doc))
    org = orgmod.load()
    for occupants in ([], ["ao-alpha-paul@nodeb (idle)"]):
        calls: list[tuple[str, dict]] = []

        def call(method, calls=calls, occupants=occupants, **params):
            calls.append((method, params))
            if method == "host_dir":
                return {"exists": True}
            if method == "host_files":
                return {"files": {k: None for k in params["paths"]}}
            if method == "name_check":
                return {"name": params["name"], "verdict": "free"}
            if method == "checkout_held":
                held = {"by": occupants[0].split(" ")[0], "why": f"held by {occupants[0]}"} if occupants else None
                return {"host": params["host"], "dir": params["dir"], "held": held}
            if method == "create":
                return {"id": f"ao-alpha-{params['name']}", "name": params["name"]}
            raise AssertionError(method)  # `occupancy` and `host_occupancy` among them

        _, out = teamrun.start(call, org, "t", HOST)
        assert [p for m, p in calls if m == "checkout_held"] == [{"host": "nodeb", "dir": "/srv/alpha"}]
        made = [p for m, p in calls if m == "create"]
        assert [p["name"] for p in made] == ["lead", "tl", "t-anchor", "g"]
        assert bool(made[2].get("held")) is bool(occupants)
        if occupants:
            assert any(n.startswith("t-anchor waits") and "ao-alpha-paul@nodeb" in n for n in out["notes"])
