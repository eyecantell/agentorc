"""`ao team start|stop|status|list`, `ao new --project` and the role `profile` precedence (design
§4.9, TD-040 step c), with the RPC mocked as `test_cli_roles.py` does — no tmux, no real session.
What the agent does with `create` is tested against the agent elsewhere."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agentorc import cli, teamrun, teams

pytestmark = pytest.mark.unit

HOST = "kmaster"


def org_doc(root: Path, *, two_repos: bool = False) -> dict:
    repos = {"agentorc": {HOST: str(root / "agentorc")}}
    if two_repos:
        repos["ao-api"] = {HOST: str(root / "ao-api"), "devenv": "/workspaces/api"}
    return {
        "projects": {"ao": {"repos": repos}},
        "teams": {
            "ao-grind": {
                "projects": ["ao"],
                "manager": {"role": "manager", "name": "orc-ao", "home": "agentorc"},
                "members": [
                    {"role": "grinder", "count": 2, "name": "grind", "lane": "free-pick", "home": "agentorc"},
                    {"role": "hunter", "name": "hunt", "lane": "ui", "home": "agentorc"},
                ],
            }
        },
        "roles": {"grinder": {"profile": "org-grind"}},
    }


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A temp AGENTORC_HOME with `org.yml`, `profiles.yml` and two checkouts, plus a mocked RPC."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    monkeypatch.setattr(cli.hosts, "local_host", lambda: cli.hosts.Host(name=HOST, vscode_host=HOST, local=True))
    for repo in ("agentorc", "ao-api"):
        (tmp_path / repo / ".git").mkdir(parents=True)
    (home / "profiles.yml").write_text(
        yaml.safe_dump(
            {
                "default": "paul",
                "profiles": {p: {"account": p} for p in ("paul", "org-grind", "repo-grind", "member-grind")},
            }
        )
    )
    (home / "org.yml").write_text(yaml.safe_dump(org_doc(tmp_path)))

    state: dict = {"sessions": [], "calls": [], "verdicts": {}}

    def fake_call(method, **params):
        state["calls"].append((method, params))
        if method == "list":
            return state["sessions"]
        if method == "name_check":
            return state["verdicts"].get(params["name"], {"name": params["name"], "verdict": "free"})
        if method == "host_dir":
            if isinstance(state.get("elsewhere"), Exception):
                raise state["elsewhere"]
            return {"host": params["host"], "dir": params["dir"], "exists": params["dir"] in state.get("elsewhere", ())}
        if method == "host_files":
            files = state.get("files")
            if isinstance(files, Exception):
                raise files
            there = (files or {}).get(params["dir"], {})
            return {"host": params["host"], "dir": params["dir"], "files": {p: there.get(p) for p in params["paths"]}}
        if method == "create":
            rec = {
                "id": f"ao-{Path(params['dir']).name}-{params['name']}",
                "name": params["name"],
                "state": "working",
                "dir": params["dir"],
                "adapter": params["adapter"],
                "controllers": list(params.get("controllers") or []),
                "team": params.get("team", ""),
                "project": params.get("project", ""),
                "lane": list(params.get("lane") or []),
                "previous_run": None,
            }
            state["sessions"].append(rec)
            return rec
        if method in ("send", "kill", "close"):
            if (why := state.get("refuse", {}).get(params["id"])) is not None:
                raise cli.AgentError(why)
            for s in state["sessions"]:
                if s["id"] == params["id"]:
                    s["state"] = "idle" if method == "send" else "closed"
            return None
        raise AssertionError(method)

    monkeypatch.setattr(cli, "call_sync", fake_call)
    monkeypatch.chdir(tmp_path / "agentorc")
    return tmp_path, state


def creates(state):
    return [p for m, p in state["calls"] if m == "create"]


def write_repo_roles(tmp_path, profile):
    doc = {"roles": {"grinder": {"profile": profile}}}
    (tmp_path / "agentorc" / ".agentorc.yml").write_text(yaml.safe_dump(doc))


def write_org(tmp_path, doc):
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))


# ── pre-flight: everything is checked before anything is created ──────────────────────────────


def test_a_live_name_holder_aborts_the_whole_start_and_names_it(world, capsys):
    tmp_path, state = world
    state["verdicts"]["hunt"] = {
        "name": "hunt", "verdict": "live", "holder": "ao-agentorc-hunt", "holder_state": "working",
    }  # fmt: skip
    assert cli.main(["team", "start", "ao-grind"]) == 1
    assert not creates(state)  # never half a team (§4.9): the lead was not created either
    err = capsys.readouterr().err
    assert "was not started" in err and "hunt is working as ao-agentorc-hunt" in err
    assert cli.main(["--json", "team", "start", "ao-grind"]) == 1
    assert json.loads(capsys.readouterr().out)["holders"][0]["holder"] == "ao-agentorc-hunt"


def test_a_suspended_holder_aborts_the_whole_start_too(world, capsys):
    """§4.8a *An alarm's answers* (TD-077 a2): a record a person suspended over an identity alarm
    refuses every session's create under its name — **the one exception to §4.1's rule that an
    exited holder is superseded** — so `ao team start` refuses the **whole** start and names the
    member, exactly as it does for a live holder (§4.9: there is never half a team). The person
    who suspended it lifts it, forgets it, or takes it out of the team."""
    tmp_path, state = world
    state["verdicts"]["hunt"] = {
        "name": "hunt", "verdict": "suspended", "holder": "ao-agentorc-hunt", "holder_state": "exited",
        "message": "hunt was suspended by a person at 2026-09-20T23:00:00Z over an identity alarm",
    }  # fmt: skip
    assert cli.main(["team", "start", "ao-grind"]) == 1
    assert not creates(state)  # not even the lead
    err = capsys.readouterr().err
    assert "was not started" in err and "hunt is suspended as ao-agentorc-hunt — a person lifts it" in err


def test_an_exited_holder_is_superseded_and_the_start_goes_ahead(world):
    tmp_path, state = world
    state["verdicts"]["grind-1"] = {"name": "grind-1", "verdict": "supersede", "holder_state": "exited"}
    assert cli.main(["team", "start", "ao-grind"]) == 0
    assert [p["name"] for p in creates(state)] == ["orc-ao", "grind-1", "grind-2", "hunt"]


def test_a_missing_checkout_aborts_naming_it(world, capsys):
    tmp_path, state = world
    doc = org_doc(tmp_path)
    doc["projects"]["ao"]["repos"]["agentorc"][HOST] = str(tmp_path / "gone")
    write_org(tmp_path, doc)
    assert cli.main(["team", "start", "ao-grind"]) == 1
    err = capsys.readouterr().err
    assert str(tmp_path / "gone") in err and "does not exist on kmaster" in err
    assert not creates(state) and not [m for m, _ in state["calls"] if m == "name_check"]


def test_an_unresolvable_role_or_profile_aborts(world, capsys):
    tmp_path, state = world
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["members"][0]["role"] = "sage"
    write_org(tmp_path, doc)
    assert cli.main(["team", "start", "ao-grind"]) == 1
    assert "unknown role 'sage'" in capsys.readouterr().err and not creates(state)
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["members"][0]["profile"] = "nope"
    write_org(tmp_path, doc)
    assert cli.main(["team", "start", "ao-grind"]) == 1
    assert "unknown profile 'nope'" in capsys.readouterr().err and not creates(state)


def test_a_nested_team_member_says_it_is_not_built_rather_than_pretending(world, capsys):
    tmp_path, state = world
    doc = org_doc(tmp_path)
    doc["teams"]["inner"] = {"projects": ["ao"], "manager": {"role": "person"}, "members": []}
    doc["teams"]["ao-grind"]["members"].append({"team": "inner"})
    write_org(tmp_path, doc)
    assert cli.main(["team", "start", "ao-grind"]) == 1
    assert "nested team, which is not built yet" in capsys.readouterr().err and not creates(state)


def test_an_unknown_team_names_the_defined_ones(world, capsys):
    assert cli.main(["team", "start", "nope"]) == 1
    assert "unknown team 'nope'; defined: ao-grind" in capsys.readouterr().err


# ── the create sequence ───────────────────────────────────────────────────────────────────────


def test_the_lead_is_created_first_and_members_carry_controllers_lead(world, capsys):
    tmp_path, state = world
    assert cli.main(["team", "start", "ao-grind"]) == 0
    made = creates(state)
    assert [p["name"] for p in made] == ["orc-ao", "grind-1", "grind-2", "hunt"]
    lead, grind1, hunt = made[0], made[1], made[3]
    assert lead["controllers"] == [] and lead["capabilities"] == ["control"] and lead["role"] == "manager"
    lead_id = "ao-agentorc-orc-ao"
    assert all(p["controllers"] == [lead_id] for p in made[1:])
    # a worktree per session in its home repo (§4.9 "Home and reach"), and both badges
    assert grind1["worktree"] == "grind-1" and grind1["dir"] == grind1["repo"] == str(tmp_path / "agentorc")
    assert grind1["team"] == "ao-grind" and grind1["project"] == "ao" and grind1["unattended"] is True
    assert grind1["lane"] == ["free-pick"] and hunt["lane"] == ["ui"]
    assert "## Lane: free-pick" in grind1["prompt"] and "## Area: ui" in hunt["prompt"]
    assert "## Project:" not in grind1["prompt"]  # one repo: no reach to describe
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("ao-agentorc-orc-ao  manager manager")
    assert "member grinder" in out


def test_a_definition_still_naming_lead_or_orchestrator_starts_a_manager(world, capsys, monkeypatch):
    """TD-055 step 2, then TD-076 step 2: an `org.yml` written before the renames — the `lead:` key,
    `role: orchestrator` and a `roles: orchestrator:` overlay — starts the manager with the manager
    brief, grants and the overlay's profile, records `role: manager`, and says once per old word
    that the name changed."""
    from agentorc import repoconfig

    monkeypatch.setattr(repoconfig, "_warned", set())
    tmp_path, state = world
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["lead"] = {**doc["teams"]["ao-grind"].pop("manager"), "role": "orchestrator"}
    doc["roles"]["orchestrator"] = {"profile": "org-grind"}
    write_org(tmp_path, doc)
    assert cli.main(["team", "start", "ao-grind"]) == 0
    lead = creates(state)[0]
    assert lead["role"] == "manager" and lead["capabilities"] == ["control"] and lead["profile"] == "org-grind"
    assert "You are a **manager**" in lead["prompt"]
    err = capsys.readouterr().err
    assert err.count("role `orchestrator` is now `manager`") == 1 and err.count("`lead:` is now `manager:`") == 1


def test_an_interactive_member_is_started_but_said_to_be_out_of_its_leads_reach(world, capsys):
    """§9 invariant 5 is a gate since TD-041: no session acts on an interactive one. A member the
    definition starts interactive keeps its `controllers: [lead]`, and they are inert until someone
    flips it — so the start says so rather than leaving a list that silently never fires (review of
    PR #118)."""
    tmp_path, state = world
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["members"][1]["unattended"] = False  # the hunter, watched by a person
    write_org(tmp_path, doc)
    assert cli.main(["team", "start", "ao-grind"]) == 0
    made = creates(state)
    hunt = next(p for p in made if p["name"] == "hunt")
    assert hunt["unattended"] is False and hunt["controllers"] == ["ao-agentorc-orc-ao"]  # recorded, inert
    err = capsys.readouterr().err
    assert "hunt is interactive, so orc-ao cannot act on it" in err and "invariant 5" in err
    assert "grind-1" not in err  # the unattended members say nothing


def test_a_person_lead_starts_no_lead_session_and_members_are_controlled_by_nobody(world):
    tmp_path, state = world
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["manager"] = {"role": "person"}
    write_org(tmp_path, doc)
    assert cli.main(["team", "start", "ao-grind"]) == 0
    made = creates(state)
    assert [p["name"] for p in made] == ["grind-1", "grind-2", "hunt"]
    assert all(p["controllers"] == [] for p in made)


def test_the_project_block_names_every_repo_on_this_host_and_which_is_home(world):
    tmp_path, state = world
    doc = org_doc(tmp_path, two_repos=True)
    doc["teams"]["ao-grind"]["members"][0]["home"] = "ao-api"
    write_org(tmp_path, doc)
    assert cli.main(["team", "start", "ao-grind"]) == 0
    made = {p["name"]: p for p in creates(state)}
    block = made["grind-1"]["prompt"]
    assert block.startswith("## Project: ao")
    assert f"- ao-api: {tmp_path / 'ao-api'}  — your home" in block
    assert f"- agentorc: {tmp_path / 'agentorc'}\n" in block
    assert "no credential and no permission" in block and "## Lane: free-pick" in block
    assert made["grind-1"]["dir"] == str(tmp_path / "ao-api")  # its home is where it runs
    assert f"- agentorc: {tmp_path / 'agentorc'}  — your home" in made["hunt"]["prompt"]


def test_a_repo_on_another_host_is_a_note_not_a_failure(world):
    tmp_path, state = world
    doc = org_doc(tmp_path, two_repos=True)
    doc["projects"]["ao"]["repos"]["ao-api"] = {"devenv": "/workspaces/api"}  # nothing on this host
    write_org(tmp_path, doc)
    assert cli.main(["team", "start", "ao-grind"]) == 0
    prompt = creates(state)[1]["prompt"]
    assert "- ao-api: not checked out on kmaster (declared on devenv) — out of reach" in prompt


# ── a team on another host (design §4.4a "Teams across hosts", TD-057 step 4a) ────────────────


def on_devenv(tmp_path):
    """The team's `host:` is `devenv`, and the repo is checked out there at the same path it has
    here — a container node's shape, where the roles and briefs are read."""
    doc = org_doc(tmp_path)
    doc["projects"]["ao"]["repos"]["agentorc"]["devenv"] = str(tmp_path / "agentorc")
    doc["teams"]["ao-grind"]["host"] = "devenv"
    write_org(tmp_path, doc)
    return str(tmp_path / "agentorc")


def test_a_team_with_a_host_is_checked_there_and_created_there(world, capsys):
    tmp_path, state = world
    checkout = on_devenv(tmp_path)
    state["elsewhere"] = {checkout}
    assert cli.main(["team", "start", "ao-grind"]) == 0
    calls = state["calls"]
    assert [p for m, p in calls if m == "host_dir"] == [{"host": "devenv", "dir": checkout}]  # once per checkout
    assert all(p["host"] == "devenv" for m, p in calls if m == "name_check")
    assert all(p["host"] == "devenv" and p["dir"] == checkout for p in creates(state))
    order = [m for m, _ in calls]
    assert order[0] == "host_dir" and order.index("create") > order.index("name_check")


def test_a_team_whose_host_is_unreachable_is_refused_whole(world, capsys):
    tmp_path, state = world
    on_devenv(tmp_path)
    state["elsewhere"] = cli.AgentError("runs on devenv: unreachable since t — ssh failed; refused, not queued")
    assert cli.main(["team", "start", "ao-grind"]) == 1
    assert "was not started — devenv: runs on devenv: unreachable" in capsys.readouterr().err and not creates(state)
    state["elsewhere"] = set()  # reachable, and the checkout is not there
    assert cli.main(["team", "start", "ao-grind"]) == 1
    assert "does not exist on devenv" in capsys.readouterr().err and not creates(state)


def test_a_machine_nodes_roles_and_briefs_are_read_on_that_node(world, capsys):
    """TD-057 step 4b.3: a checkout that is not a directory here — a machine node's — has its
    `.agentorc.yml` and its briefs read on that node (`host_files`), by the same loader; the start
    stays all-or-nothing when that read fails, and a brief outside the checkout is never asked for."""
    tmp_path, state = world
    doc = org_doc(tmp_path)
    doc["projects"]["ao"]["repos"]["agentorc"]["devenv"] = "/workspaces/agentorc"  # a laptop's path, not here
    doc["teams"]["ao-grind"]["host"] = "devenv"
    write_org(tmp_path, doc)
    there = "/workspaces/agentorc"
    state["elsewhere"] = {there}
    repo_cfg = yaml.safe_dump({"roles": {"grinder": {"brief": "docs/grind.md", "profile": "repo-grind"}}})
    state["files"] = {there: {".agentorc.yml": repo_cfg, "docs/grind.md": "grind on the node: {lane}"}}
    assert cli.main(["team", "start", "ao-grind"]) == 0
    asked = [p for m, p in state["calls"] if m == "host_files"]
    assert asked and all(p["host"] == "devenv" and p["dir"] == there for p in asked)
    assert {x for p in asked for x in p["paths"]} == {".agentorc.yml", "docs/grind.md"}
    made = {p["name"]: p for p in creates(state)}
    assert "grind on the node: free-pick" in made["grind-1"]["prompt"] and made["grind-1"]["profile"] == "repo-grind"
    assert all(p["host"] == "devenv" and p["dir"] == there for p in made.values())
    # the node cannot be read: nothing is started
    state["sessions"], state["calls"] = [], []
    state["files"] = cli.AgentError("runs on devenv: unreachable since t — ssh failed; refused, not queued")
    assert cli.main(["team", "start", "ao-grind"]) == 1
    assert "devenv: runs on devenv: unreachable" in capsys.readouterr().err and not creates(state)
    # a brief the repo keeps outside its checkout is refused here, never asked of the node
    state["files"] = {there: {".agentorc.yml": yaml.safe_dump({"roles": {"grinder": {"brief": "/etc/passwd"}}})}}
    assert cli.main(["team", "start", "ao-grind"]) == 1
    assert "outside the checkout" in capsys.readouterr().err and not creates(state)
    assert "/etc/passwd" not in str([p for m, p in state["calls"] if m == "host_files"])
    del doc["projects"]["ao"]["repos"]["agentorc"]["devenv"]  # declared nowhere on that host
    write_org(tmp_path, doc)
    assert cli.main(["team", "start", "ao-grind"]) == 1
    assert "has no checkout on devenv (declared on kmaster)" in capsys.readouterr().err


def test_stop_degrades_per_member_when_one_is_refused(world, capsys):
    """The 3b leftover: a member whose host is unreachable is named with the reason, and the rest
    are still wrapped up — the stop never aborts on the first refusal."""
    tmp_path, state = world
    started(state)
    state["refuse"] = {"ao-agentorc-grind-2": "runs on devenv: unreachable since t — ssh failed; refused, not queued"}
    capsys.readouterr()
    assert cli.main(["team", "stop", "ao-grind", "--timeout", "0"]) == 0
    sent = [p["id"] for m, p in state["calls"] if m == "send"]  # every member was tried, the lead last
    assert sent == ["ao-agentorc-grind-1", "ao-agentorc-grind-2", "ao-agentorc-hunt", "ao-agentorc-orc-ao"]
    out = capsys.readouterr().out
    assert "ao-agentorc-grind-2  member: refused: runs on devenv: unreachable" in out
    assert out.count("wrap-up sent") == 3 and "still working" not in out  # the refused one is not waited on


def test_a_members_brief_override_replaces_the_roles_template(world):
    tmp_path, state = world
    (tmp_path / "agentorc" / "docs").mkdir()
    (tmp_path / "agentorc" / "docs" / "b.md").write_text("mine: {lane}\n")
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["members"][0]["brief"] = "docs/b.md"
    write_org(tmp_path, doc)
    assert cli.main(["team", "start", "ao-grind"]) == 0
    assert creates(state)[1]["prompt"] == "mine: free-pick\n"


def test_a_repos_own_teams_are_folded_in_and_the_org_file_wins(world):
    tmp_path, state = world
    (tmp_path / "agentorc" / ".agentorc.yml").write_text(
        yaml.safe_dump({"teams": {"repo-team": {"manager": {"role": "person"}, "members": [{"role": "hunter"}]}}})
    )
    assert cli.main(["team", "start", "repo-team"]) == 0
    made = creates(state)
    assert [p["name"] for p in made] == ["hunter"] and made[0]["team"] == "repo-team"
    assert made[0]["project"] == "agentorc"  # the repo's own project, named for the checkout


# ── stop, status, list ────────────────────────────────────────────────────────────────────────


def started(state):
    assert cli.main(["team", "start", "ao-grind"]) == 0
    state["calls"].clear()
    return state


def test_stop_wraps_the_members_up_before_the_lead_and_now_kills(world, capsys):
    tmp_path, state = world
    started(state)
    capsys.readouterr()
    assert cli.main(["team", "stop", "ao-grind", "--timeout", "0"]) == 0
    sent = [p for m, p in state["calls"] if m == "send"]
    assert [p["id"] for p in sent] == [
        "ao-agentorc-grind-1", "ao-agentorc-grind-2", "ao-agentorc-hunt", "ao-agentorc-orc-ao",
    ]  # fmt: skip
    assert all(p["text"] == teams.WRAPUP_PROMPT and p["wrapup"] is True for p in sent)  # holds the doorbell off
    assert "wrap-up sent" in capsys.readouterr().out
    state["calls"].clear()
    for s in state["sessions"]:
        s["state"] = "working"
    assert cli.main(["team", "stop", "ao-grind", "--now"]) == 0
    killed = [p["id"] for m, p in state["calls"] if m == "kill"]
    assert killed[-1] == "ao-agentorc-orc-ao" and len(killed) == 4
    assert not [m for m, _ in state["calls"] if m == "send"]


def test_a_lead_stopping_its_own_team_is_the_wind_down_and_is_never_typed_at(world, capsys, monkeypatch):
    """Design §4.9a, TD-053 step 3: the same sequence under a different trigger. The lead's own
    command never sends to the lead or kills it, and `--close` closes what settled with nothing to
    lose — a member holding unpushed work is left open and named."""
    tmp_path, state = world
    started(state)
    capsys.readouterr()
    monkeypatch.setenv("AGENTORC_SESSION", "ao-agentorc-orc-ao")
    by_id = {s["id"]: s for s in state["sessions"]}
    # `unpushed` is the one measure the host agent computes (design §4.2, TD-080); `ahead` is kept
    # on the record beside it and is no longer what *pushed* is read from
    pushed = {"dirty": 0, "ahead": 0, "upstream": "origin/x", "unpushed": 0, "pushed_against": "origin/x"}
    by_id["ao-agentorc-grind-1"]["git"] = pushed
    by_id["ao-agentorc-hunt"]["git"] = pushed
    by_id["ao-agentorc-grind-2"]["git"] = {**pushed, "ahead": 2, "unpushed": 2}
    assert cli.main(["team", "stop", "ao-grind", "--timeout", "0", "--close"]) == 0
    assert [p["id"] for m, p in state["calls"] if m == "send"] == [
        "ao-agentorc-grind-1", "ao-agentorc-grind-2", "ao-agentorc-hunt",
    ]  # fmt: skip
    assert [p["id"] for m, p in state["calls"] if m == "close"] == ["ao-agentorc-grind-1", "ao-agentorc-hunt"]
    out = capsys.readouterr().out
    assert "manager: is you" in out and "ao close ao-agentorc-orc-ao" in out
    assert "left open: 2 unpushed (vs origin/x)" in out
    assert "still working" not in out  # the lead's own line is not a member that missed the window
    assert by_id["ao-agentorc-orc-ao"]["state"] == "working"  # untouched by its own command
    # `--now` from the lead kills the members and still spares the caller
    state["calls"].clear()
    by_id["ao-agentorc-grind-2"]["state"] = "working"
    assert cli.main(["team", "stop", "ao-grind", "--now"]) == 0
    assert [p["id"] for m, p in state["calls"] if m == "kill"] == ["ao-agentorc-grind-2"]


def test_a_finished_member_gets_no_wrap_up_prompt_and_is_closed(world, capsys, monkeypatch):
    """§4.9a *Finished means declared, not gone*: out of work and settled — nothing to wrap up."""
    tmp_path, state = world
    started(state)
    by_id = {s["id"]: s for s in state["sessions"]}
    by_id["ao-agentorc-grind-1"].update(
        state="idle",
        out_of_work={"at": "2026-09-17T06:00:00Z", "why": "ledger empty"},
        git={"dirty": 0, "ahead": 0, "upstream": "origin/x"},
    )
    by_id["ao-agentorc-grind-2"]["out_of_work"] = {"at": "2026-09-17T06:00:00Z", "why": "x"}  # declared, still working
    monkeypatch.setenv("AGENTORC_SESSION", "ao-agentorc-orc-ao")
    assert cli.main(["team", "stop", "ao-grind", "--timeout", "0", "--close"]) == 0
    assert [p["id"] for m, p in state["calls"] if m == "send"] == ["ao-agentorc-grind-2", "ao-agentorc-hunt"]
    assert "ao-agentorc-grind-1" in [p["id"] for m, p in state["calls"] if m == "close"]
    assert "finished (out of work), nothing sent, closed" in capsys.readouterr().out


def test_close_needs_proof_of_pushed_not_an_absent_count(world, capsys):
    """Review of PR #192, and design §4.2's **one measure** (TD-080): a record with no `git` yet is
    unknown, not clean, and *pushed* is `git.unpushed` — computed once by the host agent, against
    the branch's own remote ref where it has one and the remote branches where it has neither ref
    nor upstream. A wind-down runs no git of its own any more: `ahead: 0` with no upstream, which
    is what a never-pushed branch looks like, is rule 3's business and not this function's."""
    tmp_path, state = world
    started(state)
    by_id = {s["id"]: s for s in state["sessions"]}
    # a merged worker sitting on a detached `origin/main`: nothing is only on this machine
    by_id["ao-agentorc-grind-1"]["git"] = {
        "dirty": 0, "ahead": 0, "upstream": None, "unpushed": 0, "pushed_against": "remote branches",
    }  # fmt: skip
    by_id["ao-agentorc-hunt"].pop("git", None)  # unknown
    assert cli.main(["team", "stop", "ao-grind", "--timeout", "0", "--close"]) == 0
    assert [p["id"] for m, p in state["calls"] if m == "close"] == ["ao-agentorc-grind-1"]
    out = capsys.readouterr().out
    assert out.count("left open: git state unknown") == 2  # grind-2 and hunt
    # the same checkout with a commit origin has never seen: left open, and told against what
    by_id["ao-agentorc-grind-1"]["git"].update(unpushed=1)
    by_id["ao-agentorc-grind-1"]["state"] = "idle"
    state["calls"].clear()
    assert cli.main(["team", "stop", "ao-grind", "--timeout", "0", "--close"]) == 0
    assert not [m for m, _ in state["calls"] if m == "close"]
    assert "1 unpushed (vs remote branches)" in capsys.readouterr().out


def test_a_persons_stop_without_close_closes_nothing(world, capsys):
    tmp_path, state = world
    started(state)
    assert cli.main(["team", "stop", "ao-grind", "--timeout", "0"]) == 0
    assert not [m for m, _ in state["calls"] if m == "close"]
    assert "still working" not in capsys.readouterr().out  # every member settled; the lead's `?` is not a miss


def test_stop_with_no_live_session_says_so(world, capsys):
    tmp_path, state = world
    assert cli.main(["team", "stop", "ao-grind"]) == 1
    assert "no live session carries the team ao-grind badge" in capsys.readouterr().err


def test_status_lists_the_members_with_state_lane_and_report_line(world, capsys):
    tmp_path, state = world
    started(state)
    capsys.readouterr()
    state["sessions"][1]["progress"] = [{"ref": "TD-027", "status": "done", "source": "declared"}]
    state["sessions"] = [s for s in state["sessions"] if s["name"] != "hunt"]  # never started
    assert cli.main(["team", "status", "ao-grind"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("ao-agentorc-orc-ao") and "working" in out[0]  # the lead first
    assert "lane: free-pick" in out[1] and "report: TD-027 · 1/1 done" in out[1]
    assert out[-1].startswith("hunt") and "not started" in out[-1]
    assert cli.main(["--json", "team", "status", "ao-grind"]) == 0
    rows = json.loads(capsys.readouterr().out)["sessions"]
    assert [r["name"] for r in rows] == ["orc-ao", "grind-1", "grind-2", "hunt"]
    assert rows[-1]["running"] is False


def test_list_shows_every_definition_its_source_and_whether_it_is_live(world, capsys):
    tmp_path, state = world
    (tmp_path / "agentorc" / ".agentorc.yml").write_text(
        yaml.safe_dump({"teams": {"repo-team": {"manager": {"role": "person"}, "members": [{"role": "hunter"}]}}})
    )
    assert cli.main(["team", "list"]) == 0
    out = capsys.readouterr().out
    assert "ao-grind" in out and "stopped" in out and str(tmp_path / "home" / "org.yml") in out
    assert "repo-team" in out and "manager: person" in out and str(tmp_path / "agentorc" / ".agentorc.yml") in out
    started(state)
    capsys.readouterr()
    assert cli.main(["--json", "team", "list"]) == 0
    rows = {r["name"]: r for r in json.loads(capsys.readouterr().out)["teams"]}
    assert rows["ao-grind"]["live"] == 4 and rows["ao-grind"]["members"] == 3
    assert rows["repo-team"]["live"] == 0
    # *stopped* and *wound down* are different facts about a team (§4.9a, TD-053 step 6), and the
    # CLI says which from the same rows the Org strip reads, or the two would disagree about one
    # definition. Every session that carried the badge declared, and each was then stopped.
    for s in state["sessions"]:
        if s.get("team") == "ao-grind":
            s["state"], s["out_of_work"] = "closed", {"at": "2026-09-17T20:00:00Z", "why": "nothing open"}
    capsys.readouterr()
    assert cli.main(["team", "list"]) == 0
    out = capsys.readouterr().out
    assert "ao-grind" in out and "wound down" in out
    assert "repo-team" in out and "stopped" in out  # one that never ran is not wound down
    # live, and every live session idle and declared: *concluded*, said beside the live count (TD-099)
    for s in state["sessions"]:
        if s.get("team") == "ao-grind":
            s["state"] = "idle"
    capsys.readouterr()
    assert cli.main(["team", "list"]) == 0
    assert "4 live, concluded" in capsys.readouterr().out


def test_a_live_team_is_concluded_when_every_live_session_is_idle_and_declared(world):
    """TD-099, design §4.5a **team groups** and §4.9a *A person's Start on a concluded team*: the
    team's word, taking either declaration; the state is part of the test; the dead are ignored; a
    seat never declares and is concluded only while it is idle."""
    tmp_path, _state = world
    out = {"at": "2026-09-22T20:00:00Z", "why": "nothing open"}
    rw = {"at": "2026-09-22T21:00:00Z", "why": "usage window", "early": True}

    def rec(name, state="idle", **kw):
        return {"id": f"ao-{name}", "name": name, "team": "ao-grind", "state": state, **kw}

    c = teamrun.concluded([rec("orc-ao", restart_wanted=rw), rec("grind-1", out_of_work=out), rec("x", "exited")])
    assert c == {"at": rw["at"], "restart": True, "names": ["grind-1", "orc-ao"]}
    assert teamrun.concluded([rec("grind-1", out_of_work=out)])["restart"] is False
    # declared and then took a turn: the word is still on the record, the team is not concluded
    assert teamrun.concluded([rec("grind-1", "working", out_of_work=out)]) is None
    # idle without the word is merely idle — Wind down is owed to it (a paused team, TD-100)
    assert teamrun.concluded([rec("grind-1", out_of_work=out), rec("grind-2")]) is None
    # a declaration in any other shape is none, never a raise (review of PR #203)
    assert all(teamrun.concluded([rec("grind-1", out_of_work=j)]) is None for j in ("x", ["y"], 7, {"why": "no at"}))
    # nothing live: that is `wound_down`'s word, not this one
    assert teamrun.concluded([rec("grind-1", "closed", out_of_work=out)]) is None
    # a seat: idle and undeclared is fine, and it is closed with the rest; working is answering somebody
    seat = {"techlead-ao"}
    c = teamrun.concluded([rec("grind-1", out_of_work=out), rec("techlead-ao")], seat)
    assert c and c["names"] == ["grind-1", "techlead-ao"]
    assert teamrun.concluded([rec("grind-1", out_of_work=out), rec("techlead-ao", "working")], seat) is None
    assert teamrun.concluded([rec("techlead-ao")], seat) is None  # only a seat live: nobody said anything
    # the definition's row carries it only while something is live
    org = cli._org_here(tmp_path / "agentorc")
    (row,) = teamrun.rows(org, [rec("orc-ao", out_of_work=out)])
    assert row["live"] == 1 and row["concluded"]["at"] == out["at"] and row["wound_down"] is None
    (row,) = teamrun.rows(org, [rec("orc-ao", "closed", out_of_work=out)])
    assert row["concluded"] is None and row["wound_down"] == out["at"]


# ── the profile precedence chain, and `ao new --project` ──────────────────────────────────────


def test_profile_precedence_built_in_org_repo_member_flag(world):
    tmp_path, state = world

    def profile_of(name="grind-1", argv=("team", "start", "ao-grind")):
        state["calls"].clear()
        state["sessions"].clear()
        assert cli.main(list(argv)) == 0
        return next(p["profile"] for p in creates(state) if p["name"] == name)

    # built-in: no profile at all (profile names are the person's, §4.2a) — the host's default
    doc = org_doc(tmp_path)
    doc.pop("roles")
    write_org(tmp_path, doc)
    assert profile_of() == ""
    write_org(tmp_path, org_doc(tmp_path))  # org.yml's roles: overlay
    assert profile_of() == "org-grind"
    write_repo_roles(tmp_path, "repo-grind")
    assert profile_of() == "repo-grind"
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["members"][0]["profile"] = "member-grind"
    write_org(tmp_path, doc)
    assert profile_of() == "member-grind"
    assert profile_of(argv=("team", "start", "ao-grind", "-p", "paul")) == "paul"
    # the lead's own `profile:` beats the role's, and `--profile` beats that
    doc["teams"]["ao-grind"]["manager"]["profile"] = "paul"
    write_org(tmp_path, doc)
    assert profile_of("orc-ao") == "paul"
    assert profile_of("orc-ao", argv=("team", "start", "ao-grind", "-p", "org-grind")) == "org-grind"


def test_ao_new_and_ao_roles_read_the_org_overlay_too(world, capsys):
    tmp_path, state = world
    assert cli.main(["new", "g1", "--role", "grinder"]) == 0
    assert creates(state)[0]["profile"] == "org-grind"  # org.yml's roles: under the repo's file
    write_repo_roles(tmp_path, "repo-grind")
    state["calls"].clear()
    assert cli.main(["new", "g2", "--role", "grinder"]) == 0
    assert creates(state)[0]["profile"] == "repo-grind"
    assert cli.main(["roles"]) == 0
    assert "profile: repo-grind" in capsys.readouterr().out


def test_ao_new_project_adds_the_reach_block_and_the_badge(world, capsys):
    tmp_path, state = world
    write_org(tmp_path, org_doc(tmp_path, two_repos=True))
    assert cli.main(["new", "solo", "--project", "ao", "--role", "hunter"]) == 0
    p = creates(state)[0]
    assert p["project"] == "ao" and p["prompt"].startswith("## Project: ao")
    assert f"- agentorc: {tmp_path / 'agentorc'}  — your home" in p["prompt"]  # started in that checkout
    assert "## Area: free" in p["prompt"]
    state["calls"].clear()
    # an undefined project is still a badge (step b landed it as a plain string): one line, no block
    assert cli.main(["new", "x", "--project", "nope"]) == 0
    assert "not defined in" in capsys.readouterr().err
    assert creates(state)[0]["project"] == "nope" and "## Project" not in (creates(state)[0]["prompt"] or "")
    state["calls"].clear()
    # a one-repo project has no reach to describe: the badge is set and the brief is untouched
    write_org(tmp_path, org_doc(tmp_path))
    assert cli.main(["new", "y", "--project", "ao", "--role", "hunter"]) == 0
    assert creates(state)[0]["project"] == "ao" and "## Project" not in creates(state)[0]["prompt"]


# ── TD-042: a brief describes the job, not the run ────────────────────────────────────────────

BRIEFS = Path(__file__).parents[1] / "src" / "agentorc" / "briefs"
REPO_BRIEFS = Path(__file__).parents[1] / "docs" / "briefs"


def test_the_packaged_brief_templates_name_no_run_and_no_clock():
    """Design §4.9 `brief:`: the templates `ao team start` hands out must be repeatable.

    The first real `ao team start ao-grind` brought up two sessions whose brief told them to stop
    at a time that had already passed, and one did — within 62 seconds it read its lane, found
    every item merged and stopped taking work. These are the files that would do it again.
    """
    templates = sorted(BRIEFS.glob("*.md"))
    assert templates, "no packaged brief templates found"
    for t in templates:
        assert not teams.unrepeatable(t.read_text()), f"{t.name} is written for one run"


def test_the_repos_own_briefs_name_no_run_and_no_clock():
    """The same rule for `docs/briefs/`, which is where a repo keeps the briefs its teams name."""
    briefs = sorted(REPO_BRIEFS.glob("*.md"))
    assert briefs, "no briefs found under docs/briefs/"
    for b in briefs:
        assert not teams.unrepeatable(b.read_text()), f"{b.name} is written for one run"


def test_a_dated_adr_link_is_not_a_run_and_a_fenced_example_is_not_a_clock():
    """The warning has to be worth reading. A bare date is deliberately not a marker — briefs cite
    dated ADRs and say what was true on a day — and a clock inside a fenced example is an example."""
    assert teams.unrepeatable("see [ADR](decisions/2026-09-12-orchestrator-membership-prior-art.md)") == []
    assert teams.unrepeatable("Since 2026-09-13 the grant is only half the rule.") == []
    assert teams.unrepeatable("```\nao until 05:30\n```") == []
    # the two shapes that actually broke a start, both from the briefs of 2026-09-11
    assert teams.unrepeatable("started 2026-09-11 17:15 MDT (run 5)")
    assert teams.unrepeatable("**Stop when the list is done, or at 05:30 MDT on 2026-09-12**")


def test_a_team_start_says_when_a_brief_names_one_run_and_starts_it_anyway(world, capsys):
    """A warning, never a refusal: the team starts, and the finding names the session and the line."""
    tmp_path, state = world
    (tmp_path / "agentorc" / "docs").mkdir()
    (tmp_path / "agentorc" / "docs" / "b.md").write_text("You are a worker.\n\nStop at 05:30 on 2026-09-12 (run 5).\n")
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["members"][0]["brief"] = "docs/b.md"
    write_org(tmp_path, doc)

    assert cli.main(["team", "start", "ao-grind"]) == 0  # started, not refused
    assert len(creates(state)) == 4  # the whole team started
    err = capsys.readouterr().err
    assert "line 3" in err and "TD-042" in err
    assert "a clock time" in err and "a run number" in err


def test_an_unclosed_fence_does_not_hide_the_rest_of_the_brief():
    """A brief with an unterminated ``` would put every line after it out of reach, so a stale stop
    time below a typo'd fence would never be found (review of PR #139)."""
    assert teams.unrepeatable("```\nan example\n\nStop at 05:30 on 2026-09-12.\n")
    # a *closed* fence still hides its example
    assert teams.unrepeatable("```\nao until 05:30\n```\n\nplain prose\n") == []


def test_a_shell_redirect_is_not_a_run_number():
    """`pdm run test 2>&1` is a command, not a fifth run (review of PR #139)."""
    assert teams.unrepeatable("Loop: `pdm run test 2>&1 | tee log`, then `pdm run lint 2>&1`.") == []
    assert teams.unrepeatable("this is run 2 of the night")


def test_a_partial_start_still_says_the_brief_names_one_run(world, capsys, monkeypatch):
    """The sessions that *did* start are running on that brief, so the warning matters here most."""
    tmp_path, state = world
    (tmp_path / "agentorc" / "docs").mkdir()
    (tmp_path / "agentorc" / "docs" / "b.md").write_text("Stop at 05:30 on 2026-09-12 (run 5).\n")
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["members"][0]["brief"] = "docs/b.md"
    write_org(tmp_path, doc)

    real = cli.call_sync
    calls = {"n": 0}

    def flaky(method, **params):
        if method == "create":
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("host went away")
        return real(method, **params)

    monkeypatch.setattr(cli, "call_sync", flaky)
    assert cli.main(["team", "start", "ao-grind"]) == 1
    err = capsys.readouterr().err
    assert "already started" in err
    assert "TD-042" in err and "a clock time" in err

    # a techlead seat without its primer (§4.9b) is said on this path too (review of PR #370)
    doc["teams"]["ao-grind"]["techlead"] = {"name": "techlead-ao"}
    write_org(tmp_path, doc)
    state["sessions"].clear()
    calls["n"] = 0
    assert cli.main(["team", "start", "ao-grind"]) == 1
    assert "has no `context:`" in capsys.readouterr().err


def test_on_a_node_the_org_lives_on_the_home_and_status_says_what_the_listing_is(world, capsys):
    """Design §4.4a, TD-057 step 2: `org.yml` lives on the home, so a node reads no local copy; and
    what `ao status` shows there is this host's sessions only, labelled — on stderr, so `--json`
    stays the records.

    From TD-084 the line no longer says *offline … unreachable* on every node: this fixture's stub
    agent answers no `host`, so the link state cannot be read, and **not knowing is not knowing it
    is down**. What is always true is still said."""
    tmp_path, state = world
    (tmp_path / "home" / "hosts.yml").write_text("home: elsewhere\n")  # this host is `kmaster` (the fixture)
    assert cli.main(["team", "start", "ao-grind"]) == 1
    err = capsys.readouterr().err
    assert "the org lives on elsewhere (home)" in err and not creates(state)
    assert cli.main(["status", "--json"]) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "[]" and "node of elsewhere" in out.err
    assert "this host's sessions only" in out.err and "the org and your mail are at the home" in out.err
    assert "is not known" in out.err and "unreachable" not in out.err  # it was never asked


# ── ao focus on a container node's session (design §4.4a "Reach", TD-057 3c.5) ───────────────


def test_focus_on_a_container_nodes_session_runs_docker_exec_and_a_machine_nodes_is_refused(world, capsys, monkeypatch):
    tmp_path, state = world
    records = {
        "ao-repo-w@cm": {
            "id": "ao-repo-w@cm",
            "state": "working",
            "host": "cm",
            "host_link": {"up": True, "reach": {"container": "abc123def456", "user": "developer"}},
        },
        "ao-repo-l@laptop": {"id": "ao-repo-l@laptop", "state": "working", "host": "laptop", "host_link": {"up": True}},
    }
    monkeypatch.setattr(cli, "call_sync", lambda method, **p: records[p["id"]] if method == "get" else None)
    assert cli.main(["--json", "focus", "ao-repo-w@cm"]) == 0
    argv = json.loads(capsys.readouterr().out)["attach"]
    assert argv[:6] == ["docker", "exec", "-u", "developer", "-it", "abc123def456"] and argv[6:9] == [
        "tmux",
        "attach",
        "-t",
    ]
    assert argv[9] == "=ao-repo-w:"
    assert cli.main(["focus", "ao-repo-l@laptop"]) == 1
    assert "runs on laptop: no terminal reaches it from here" in capsys.readouterr().err


# ── `ao team --skill`: the recipe for standing a team up (TD-067) ────────────────────────────────


@pytest.mark.unit
def test_ao_team_skill_prints_the_recipe_without_an_agent_and_exits(capsys):
    """TD-067: Paul's most likely cadence is telling a Claude session *stand up a grind team for
    repo X*, and until now that session had to read design.md and guess. `ao team --skill` prints
    the recipe and exits **during parsing** — so it needs no host agent, and no `action`, even
    though `team`'s subcommand is `required=True`. That last part is the whole reason it is an
    `argparse.Action` rather than a subcommand of its own."""
    from agentorc import cli as climod

    with pytest.raises(SystemExit) as e:
        climod.main(["team", "--skill"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("---\nname: ao-team\n")  # front matter, as `ao --skill` has
    # every command the recipe tells someone to run, and every key it tells them to write
    for must in (
        "ao team start",
        "ao team status",
        "ao team stop",
        "ao team list",
        "ao roles",
        "ao host up",
        "ao status -v",
        "org.yml",
        ".agentorc.yml",
        "projects:",
        "members:",
        "manager: {role: person}",  # the form the loader accepts — the bare string is refused
        "--close",
    ):
        assert must in out, must
    # it is the other half of the pair, and says so rather than repeating it
    assert "ao --skill" in out and "design §4.9" in out


@pytest.mark.unit
def test_the_recipe_ships_in_the_package_so_it_prints_where_there_is_no_checkout():
    """It has to print inside a container node, which has no checkout of this repo — so it lives
    beside `skill.md` in the package (`includes` in pyproject covers `src/agentorc`), and there is
    **no copy under docs/** to drift from it. The README points at it instead."""
    import pathlib

    from agentorc.cli import team_skill_text

    root = pathlib.Path(__file__).parents[1]
    assert (root / "src/agentorc/team_skill.md").is_file()
    assert not list((root / "docs").rglob("stand-up-a-team.md"))
    assert team_skill_text() == (root / "src/agentorc/team_skill.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "ao team --skill" in readme and "## Stand up a team" in readme
    # and the older document says which one case it still owns
    briefs = (root / "docs/briefs/README.md").read_text(encoding="utf-8")
    assert "ao team --skill" in briefs and "launched by hand" in briefs


@pytest.mark.unit
def test_every_yaml_block_in_the_recipe_is_read_by_the_loader_it_claims(tmp_path, monkeypatch):
    """The failure a guide dies of is a key that was never real. The recipe's `org.yml` block is
    fed to the loader it names, and its `.agentorc.yml` block to `repoconfig`, so a key either
    round-trips or this test says which one did not."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    import importlib

    import yaml

    from agentorc import org as orgmod
    from agentorc import repoconfig

    importlib.reload(orgmod)
    org_yml = {
        "projects": {"contractmatch": {"repos": {"contractmatch": {"kmaster": "~/contractmatch"}}}},
        "teams": {
            "cm-grind": {
                "projects": ["contractmatch"],
                "host": "contractmatch",
                "manager": {"role": "manager", "name": "manager-cm"},
                "members": [{"role": "grinder", "count": 2, "name": "grinder-cm", "lane": "free-pick"}],
            }
        },
    }
    (tmp_path / "org.yml").write_text(yaml.safe_dump(org_yml))
    org = orgmod.load()
    team = org.teams["cm-grind"]
    assert team.host == "contractmatch" and team.manager.name == "manager-cm"
    assert team.members[0].names() == ["grinder-cm-1", "grinder-cm-2"]  # the prefix rule the recipe states
    assert org.checkout("contractmatch", "contractmatch", "kmaster") is not None

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".agentorc.yml").write_text(
        "roles:\n"
        "  grinder: {brief: docs/briefs/grinder.md, lane: free-pick, profile: grind, icon: wrench}\n"
        "  manager: {brief: docs/briefs/manager.md, grants: [control]}\n"
        "controllers: [manager-cm]\n"
        "ledger: docs/technical_debt.md\n"
    )
    cfg = repoconfig.discover(repo)
    assert cfg.controllers == ["manager-cm"] and cfg.ledger == "docs/technical_debt.md"
    got = {r.name: r for r in repoconfig.roles(cfg)}
    assert got["grinder"].profile == "grind" and got["grinder"].icon == "wrench"
    assert got["manager"].grants == ["control"]
    # and the built-in roles the recipe's table names all resolve
    for name in ("manager", "grinder", "hunter", "plain"):
        assert name in got, name


@pytest.mark.unit
def test_the_recipe_does_not_tell_anyone_to_write_what_the_planner_refuses(tmp_path, monkeypatch):
    """The failure a guide dies of, second kind: a shape the **loader** accepts and the **planner**
    refuses. `{team: other-team}` parses — `org.py` carries it in its own docstring example — and
    `ao team list` shows it, so it reads as built; `teams.plan()` raises *is a nested team, which
    is not built yet*. The first draft of this file told people to use it (fact-check of PR #302).

    So the recipe is held to the planner as well as to the loader: it may not present a shape as
    working unless `plan()` accepts it, and where it names one that does not work it must say so."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    import importlib

    import yaml

    from agentorc import org as orgmod
    from agentorc import teams
    from agentorc.cli import team_skill_text

    importlib.reload(orgmod)
    (tmp_path / "org.yml").write_text(
        yaml.safe_dump({
            "projects": {"p": {"repos": {"r": {"h": str(tmp_path)}}}},
            "teams": {
                "outer": {"projects": ["p"], "manager": {"role": "manager"}, "members": [{"team": "inner"}]},
                "inner": {"projects": ["p"], "manager": {"role": "manager"}, "members": [{"role": "grinder"}]},
            },
        })
    )  # fmt: skip
    org = orgmod.load()
    assert org.teams["outer"].members[0].team == "inner"  # the loader takes it…
    with pytest.raises(teams.TeamError, match="is a nested team, which is not built yet"):
        teams.plan(org, "outer", host="h")  # …and the planner does not

    # so the recipe says exactly that, rather than presenting it as a feature
    text = team_skill_text()
    assert "{team: other-team}" in text and "refused at `start`" in text
    assert "not\n  built" in text  # the sentence wraps in the file; the claim is what matters


def test_a_techlead_seat_starts_under_the_manager_and_every_brief_names_it(world, capsys, monkeypatch):
    """TD-075 step 1, design §4.9b *The seat*: a definition's `techlead:` starts after the manager
    and before the members, as the `techlead` preset — no lane, no grants — with the manager as its
    controller, and `{techlead}` in every brief is the id it takes, worked out before anything was
    created. A team without a seat reads `none`. Should the seat come up under another id, the
    start says so; `ao team list` names the seat."""
    tmp_path, state = world
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["techlead"] = {"name": "techlead-ao", "profile": "paul"}
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))
    assert cli.main(["team", "start", "ao-grind"]) == 0
    made = creates(state)
    assert [p["name"] for p in made] == ["orc-ao", "techlead-ao", "grind-1", "grind-2", "hunt"]
    seat = made[1]
    assert seat["role"] == "techlead" and seat["capabilities"] == [] and seat["lane"] == []
    assert seat["controllers"] == ["ao-agentorc-orc-ao"] and seat["team"] == "ao-grind"
    assert seat["profile"] == "paul" and "**techlead**" in seat["prompt"]
    for p in (made[0], *made[2:]):
        assert "`ao-agentorc-techlead-ao`" in p["prompt"] and "{techlead}" not in p["prompt"]
    out = capsys.readouterr()
    assert "ao-agentorc-techlead-ao  techlead techlead" in out.out and "stale tmux" not in out.err
    assert cli.main(["team", "list"]) == 0
    assert "techlead: techlead-ao" in capsys.readouterr().out

    # the seat came up under another id: the start stands and says so
    state["sessions"].clear()
    state["calls"].clear()
    real = cli.call_sync

    def suffixed(method, **params):
        rec = real(method, **params)
        if method == "create" and params["name"] == "techlead-ao":
            rec["id"] += "-2"
        return rec

    monkeypatch.setattr(cli, "call_sync", suffixed)
    assert cli.main(["team", "start", "ao-grind"]) == 0
    err = capsys.readouterr().err
    assert "started as ao-agentorc-techlead-ao-2, but the briefs name ao-agentorc-techlead-ao" in err

    # no seat: every brief says `none`, and nothing is started for it
    monkeypatch.setattr(cli, "call_sync", real)
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(org_doc(tmp_path)))
    state["sessions"].clear()
    state["calls"].clear()
    assert cli.main(["team", "start", "ao-grind"]) == 0
    made = creates(state)
    assert "techlead" not in [p["role"] for p in made]
    assert all("techlead is `none`" in p["prompt"] for p in made)


def test_a_techlead_seat_reads_its_primer_first_and_the_start_warns_without_one(world, capsys):
    """TD-075 step 1b, design §4.9b *Its standing context*: `context:` on the seat is its primer —
    a path in its home checkout, filled into the seat's brief as `{context}` — and `ao team start`
    says so, and starts anyway, when the seat has none or the file is not there."""
    tmp_path, state = world
    primer = tmp_path / "agentorc" / "docs" / "primer.md"
    primer.parent.mkdir(parents=True, exist_ok=True)
    primer.write_text("# primer\n")
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["techlead"] = {"name": "techlead-ao", "context": "docs/primer.md"}
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))
    assert cli.main(["team", "start", "ao-grind"]) == 0
    seat = next(p for p in creates(state) if p["role"] == "techlead")
    assert "`docs/primer.md`" in seat["prompt"] and "{context}" not in seat["prompt"]
    assert "primer" not in capsys.readouterr().err  # there, so nothing is said

    for context, said in ((None, "has no `context:`"), ("docs/gone.md", "docs/gone.md is not in")):
        doc["teams"]["ao-grind"]["techlead"] = {"name": "techlead-ao", **({"context": context} if context else {})}
        (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))
        state["sessions"].clear()
        state["calls"].clear()
        assert cli.main(["team", "start", "ao-grind"]) == 0  # said, and the team started
        assert said in capsys.readouterr().err
        assert [p["name"] for p in creates(state)] == ["orc-ao", "techlead-ao", "grind-1", "grind-2", "hunt"]
        seat = next(p for p in creates(state) if p["role"] == "techlead")
        assert f"`{context or 'none'}`" in seat["prompt"]  # as written; `none` sends it to the repo's map


def test_the_techlead_seat_is_not_counted_when_a_team_winds_down(world):
    """TD-075 step 4, design §4.9b *A seat is empty or filled — never finished*: a team whose members
    all declared reads *wound down* although its techlead never did — the seat is known by the name
    its definition gives it, not by a role badge (§9 invariant 9). Without a seat, one member that
    never declared still means the team stopped for another reason."""

    tmp_path, state = world
    done = {"at": "2026-09-21T06:00:00Z", "why": "nothing left"}
    sessions = [
        {"id": "a", "name": "grind-1", "team": "ao-grind", "state": "exited", "out_of_work": done},
        {"id": "b", "name": "techlead-ao", "team": "ao-grind", "state": "exited", "out_of_work": None},
    ]
    assert teamrun.wound_down(sessions) is None
    assert teamrun.wound_down(sessions, {"techlead-ao"}) == "2026-09-21T06:00:00Z"
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["techlead"] = {"name": "techlead-ao"}
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))
    org = cli._org_here(tmp_path / "agentorc")
    (row,) = teamrun.rows(org, sessions)
    assert row["wound_down"] == "2026-09-21T06:00:00Z" and row["techlead"] == "techlead-ao"
    # a seat that came up suffixed (a stale tmux session held its id) is still the seat; a member
    # whose definition name only looks like one is never taken for it (review of PR #350)
    sessions[1]["name"] = "techlead-ao-2"
    assert teamrun.rows(org, sessions)[0]["wound_down"] == "2026-09-21T06:00:00Z"
    doc["teams"]["ao-grind"]["members"].append({"role": "grinder", "name": "techlead-ao-3", "home": "agentorc"})
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))
    org = cli._org_here(tmp_path / "agentorc")
    assert teamrun.seat_names(org.teams["ao-grind"], [*sessions, {"name": "techlead-ao-3"}]) == {"techlead-ao-2"}
    # the page's *on call* keys on the same rule, by id, and only under the team's own badge (TD-097)
    other = {"id": "c", "name": "techlead-ao", "team": "other", "state": "exited"}
    assert teamrun.seat_ids(org, [*sessions, {"id": "d", "name": "techlead-ao-3", "team": "ao-grind"}, other]) == {
        "b": "comes on the next question"
    }


def test_a_seat_with_a_trigger_starts_with_the_team_and_is_a_seat_everywhere(world, capsys):
    """TD-098 step 1, design §4.9b *Seats with a trigger*: each of `seats:` starts with the team,
    after the techlead and before the members, under the manager and with no grants whatever its
    role holds; `ao team list` carries the triggers the manager fills them by; and a seat is a seat
    wherever the techlead is one — not counted in a wind-down, and *on call* on the page with its
    own words."""
    tmp_path, state = world
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["techlead"] = {"name": "techlead-ao"}
    doc["teams"]["ao-grind"]["seats"] = [
        {"name": "audit-ao", "role": "manager-ish", "trigger": {"prs": 10}},
    ]
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))
    assert cli.main(["team", "start", "ao-grind"]) != 0  # an unknown role stops the start, as a member's does
    assert creates(state) == []
    capsys.readouterr()
    doc["teams"]["ao-grind"]["seats"] = [
        {"name": "audit-ao", "role": "manager", "trigger": {"prs": 10}},
    ]
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))
    assert cli.main(["team", "start", "ao-grind"]) != 0  # the manager role is refused as a seat's
    assert "not a seat's role" in capsys.readouterr().err
    doc["teams"]["ao-grind"]["seats"] = [{"name": "audit-ao", "role": "hunter", "trigger": {"every": "6h"}}]
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))
    assert cli.main(["team", "start", "ao-grind"]) == 0
    made = creates(state)
    assert [p["name"] for p in made] == ["orc-ao", "techlead-ao", "audit-ao", "grind-1", "grind-2", "hunt"]
    audit = made[2]
    # neither the role's grants nor its lane (the hunter preset has both): a seat's area is its brief's
    assert audit["role"] == "hunter" and audit["capabilities"] == [] and audit["lane"] == []
    assert "(none given)" in audit["prompt"]
    assert audit["controllers"] == ["ao-agentorc-orc-ao"] and audit["team"] == "ao-grind"
    assert "`ao-agentorc-techlead-ao`" in audit["prompt"]
    assert "ao-agentorc-audit-ao  seat hunter" in capsys.readouterr().out
    assert cli.main(["team", "list", "--json"]) == 0
    (row,) = json.loads(capsys.readouterr().out)["teams"]
    assert row["seats"] == [{"name": "audit-ao", "role": "hunter", "trigger": "every", "after": "6h"}]
    org = cli._org_here(tmp_path / "agentorc")
    done = {"at": "2026-09-21T06:00:00Z", "why": "nothing left"}
    sessions = [
        {"id": "a", "name": "grind-1", "team": "ao-grind", "state": "exited", "out_of_work": done},
        {"id": "b", "name": "techlead-ao", "team": "ao-grind", "state": "exited"},
        {"id": "c", "name": "audit-ao-2", "team": "ao-grind", "state": "exited"},
    ]
    assert teamrun.seat_names(org.teams["ao-grind"], sessions) == {"techlead-ao", "audit-ao-2"}
    assert teamrun.rows(org, sessions)[0]["wound_down"] == "2026-09-21T06:00:00Z"
    assert teamrun.seat_ids(org, sessions) == {"b": "comes on the next question", "c": "runs every 6h"}
