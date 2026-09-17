"""`ao team start|stop|status|list`, `ao new --project` and the role `profile` precedence (design
§4.9, TD-040 step c), with the RPC mocked as `test_cli_roles.py` does — no tmux, no real session.
What the agent does with `create` is tested against the agent elsewhere."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agentorc import cli, teams

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
                "lead": {"role": "lead", "name": "orc-ao", "home": "agentorc"},
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
    doc["teams"]["inner"] = {"projects": ["ao"], "lead": {"role": "person"}, "members": []}
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
    assert lead["controllers"] == [] and lead["capabilities"] == ["control"] and lead["role"] == "lead"
    lead_id = "ao-agentorc-orc-ao"
    assert all(p["controllers"] == [lead_id] for p in made[1:])
    # a worktree per session in its home repo (§4.9 "Home and reach"), and both badges
    assert grind1["worktree"] == "grind-1" and grind1["dir"] == grind1["repo"] == str(tmp_path / "agentorc")
    assert grind1["team"] == "ao-grind" and grind1["project"] == "ao" and grind1["unattended"] is True
    assert grind1["lane"] == ["free-pick"] and hunt["lane"] == ["ui"]
    assert "## Lane: free-pick" in grind1["prompt"] and "## Area: ui" in hunt["prompt"]
    assert "## Project:" not in grind1["prompt"]  # one repo: no reach to describe
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("ao-agentorc-orc-ao  lead lead")
    assert "member grinder" in out


def test_a_definition_still_naming_role_orchestrator_starts_a_lead(world, capsys, monkeypatch):
    """TD-055 step 2: an `org.yml` written before the rename — `lead: {role: orchestrator}` and a
    `roles: orchestrator:` overlay, the live shape — starts the lead with the lead brief, grants and
    the overlay's profile, records `role: lead`, and says once that the name changed."""
    from agentorc import repoconfig

    monkeypatch.setattr(repoconfig, "_warned", set())
    tmp_path, state = world
    doc = org_doc(tmp_path)
    doc["teams"]["ao-grind"]["lead"]["role"] = "orchestrator"
    doc["roles"]["orchestrator"] = {"profile": "org-grind"}
    write_org(tmp_path, doc)
    assert cli.main(["team", "start", "ao-grind"]) == 0
    lead = creates(state)[0]
    assert lead["role"] == "lead" and lead["capabilities"] == ["control"] and lead["profile"] == "org-grind"
    assert "You are a **lead**" in lead["prompt"]
    assert capsys.readouterr().err.count("role `orchestrator` is now `lead`") == 1


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
    doc["teams"]["ao-grind"]["lead"] = {"role": "person"}
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
    assert "- ao-api: not checked out on kmaster (declared on devenv) — out of reach until phase 2" in prompt


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
        yaml.safe_dump({"teams": {"repo-team": {"lead": {"role": "person"}, "members": [{"role": "hunter"}]}}})
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
    assert all(p["text"] == teams.WRAPUP_PROMPT for p in sent)
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
    pushed = {"dirty": 0, "ahead": 0, "upstream": "origin/x"}
    by_id["ao-agentorc-grind-1"]["git"] = pushed
    by_id["ao-agentorc-hunt"]["git"] = pushed
    by_id["ao-agentorc-grind-2"]["git"] = {"dirty": 0, "ahead": 2, "upstream": "origin/x"}
    assert cli.main(["team", "stop", "ao-grind", "--timeout", "0", "--close"]) == 0
    assert [p["id"] for m, p in state["calls"] if m == "send"] == [
        "ao-agentorc-grind-1", "ao-agentorc-grind-2", "ao-agentorc-hunt",
    ]  # fmt: skip
    assert [p["id"] for m, p in state["calls"] if m == "close"] == ["ao-agentorc-grind-1", "ao-agentorc-hunt"]
    out = capsys.readouterr().out
    assert "lead: is you" in out and "ao close ao-agentorc-orc-ao" in out
    assert "left open: 2 unpushed" in out
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


def test_close_needs_proof_of_pushed_not_an_absent_count(world, capsys, tmp_path_factory):
    """Review of PR #192: `ahead: 0` with no upstream is what a never-pushed branch looks like, and
    a record with no `git` yet is unknown, not clean. With no upstream the commit is looked for on
    the remote branches — a merged worker on a detached `origin/main` is pushed."""
    import subprocess

    tmp_path, state = world
    started(state)
    by_id = {s["id"]: s for s in state["sessions"]}
    repo = tmp_path_factory.mktemp("wt")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "merged"], check=True)
    subprocess.run([*git, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
    no_upstream = {"dirty": 0, "ahead": 0, "upstream": None}
    by_id["ao-agentorc-grind-1"].update(dir=str(repo), git=no_upstream)  # detached on what origin has
    by_id["ao-agentorc-hunt"].pop("git", None)  # unknown
    assert cli.main(["team", "stop", "ao-grind", "--timeout", "0", "--close"]) == 0
    assert [p["id"] for m, p in state["calls"] if m == "close"] == ["ao-agentorc-grind-1"]
    out = capsys.readouterr().out
    assert out.count("left open: git state unknown") == 2  # grind-2 and hunt
    # the same checkout with a commit origin has never seen
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "never pushed"], check=True)
    by_id["ao-agentorc-grind-1"]["state"] = "idle"
    state["calls"].clear()
    assert cli.main(["team", "stop", "ao-grind", "--timeout", "0", "--close"]) == 0
    assert not [m for m, _ in state["calls"] if m == "close"]
    assert "no upstream, and its commit is on no remote branch" in capsys.readouterr().out


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
        yaml.safe_dump({"teams": {"repo-team": {"lead": {"role": "person"}, "members": [{"role": "hunter"}]}}})
    )
    assert cli.main(["team", "list"]) == 0
    out = capsys.readouterr().out
    assert "ao-grind" in out and "stopped" in out and str(tmp_path / "home" / "org.yml") in out
    assert "repo-team" in out and "lead: person" in out and str(tmp_path / "agentorc" / ".agentorc.yml") in out
    started(state)
    capsys.readouterr()
    assert cli.main(["--json", "team", "list"]) == 0
    rows = {r["name"]: r for r in json.loads(capsys.readouterr().out)["teams"]}
    assert rows["ao-grind"]["live"] == 4 and rows["ao-grind"]["members"] == 3
    assert rows["repo-team"]["live"] == 0


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
    doc["teams"]["ao-grind"]["lead"]["profile"] = "paul"
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
