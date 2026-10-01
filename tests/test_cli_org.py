"""`ao org` and `ao org check` (design §4.7, §4.9 *The org is an aggregate*; TD-229 slice 6): the
org as the clients aggregate it, and the same reading as a verdict. The RPC is mocked as
`test_cli_teams.py` mocks it; the checkouts are real git repos, since the warnings are git's."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from agentorc import cli
from sessionorc import defs

pytestmark = pytest.mark.unit

HOST = "kmaster"
TEAM = {"manager": {"role": "person"}, "members": [{"role": "hunter"}]}


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


def repo(root: Path, teams: dict | None = None) -> Path:
    """A checkout on `main` with one commit, `origin/HEAD` naming `main`, and its `.agentorc.yml`."""
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_text(".worktrees/\n")
    if teams is not None:
        (root / ".agentorc.yml").write_text(yaml.safe_dump({"teams": teams}))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "first")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return root


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A temp home with `org.yml` and `profiles.yml`, a registry of two checkouts (`alpha` defines
    a team, `beta` none), no node, and an agent that answers nothing — a check needs none."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    registry = home / "repos.txt"
    registry.write_text(f"{tmp_path / 'alpha'}\n{tmp_path / 'beta'}\n")
    monkeypatch.setattr(
        cli.hosts,
        "local_host",
        lambda: cli.hosts.Host(name=HOST, vscode_host=HOST, local=True, repos_registry=registry),
    )
    monkeypatch.setattr(cli.hosts, "is_node", lambda: False)
    monkeypatch.setattr(cli.hosts, "nodes", lambda: {"devenv": {}})
    repo(tmp_path / "alpha", {"alpha-grind": TEAM})
    repo(tmp_path / "beta")
    (home / "profiles.yml").write_text(yaml.safe_dump({"default": "paul", "profiles": {"paul": {"account": "paul"}}}))
    org = {
        "projects": {"both": {"repos": {"alpha": {HOST: str(tmp_path / "alpha")}}}},
        "teams": {"span": {"projects": ["both"], "manager": {"role": "person"}, "members": [{"role": "hunter"}]}},
    }
    (home / "org.yml").write_text(yaml.safe_dump(org))

    def no_agent(method, **params):
        raise cli.AgentError(f"unknown method: {method}")

    monkeypatch.setattr(cli, "call_sync", no_agent)
    monkeypatch.chdir(home)
    return tmp_path, home, org


def check(capsys) -> tuple[int, dict]:
    code = cli.main(["--json", "org", "check"])
    return code, json.loads(capsys.readouterr().out)


def test_ao_org_prints_each_team_its_file_its_repo_and_where_it_lands(world, capsys):
    tmp_path, home, _ = world
    assert cli.main(["--json", "org"]) == 0
    got = json.loads(capsys.readouterr().out)
    rows = {r["name"]: r for r in got["teams"]}
    assert rows["alpha-grind"] == {
        "name": "alpha-grind",
        "source": str(tmp_path / "alpha" / ".agentorc.yml"),
        "repos": ["alpha"],
        "host": HOST,
        "why": "registered here",
    }
    # the org file's own team has no landing rule: its `host:`, or where it is started
    assert rows["span"]["source"] == str(home / "org.yml") and rows["span"]["host"] == HOST
    assert rows["span"]["why"].startswith("no host:")
    # the remainder: the three files, none with a history until the home is a work tree
    rem = got["remainder"]
    assert rem["tree"] is False and [f["name"] for f in rem["files"]] == list(defs.TRACKED)
    assert {f["name"]: f["exists"] for f in rem["files"]} == {
        "org.yml": True,
        "profiles.yml": True,
        "settings.yml": False,
    }
    assert cli.main(["org"]) == 0
    out = capsys.readouterr().out
    assert f"alpha-grind  repo: alpha  on {HOST} (registered here)  [{tmp_path / 'alpha' / '.agentorc.yml'}]" in out
    assert "no history yet" in out and "settings.yml  not there" in out


def test_ao_org_says_a_placed_a_shadowed_and_a_twice_named_team_and_each_files_last_commit(world, capsys):
    tmp_path, home, org = world
    (tmp_path / "alpha" / ".agentorc.yml").write_text(
        yaml.safe_dump({"teams": {"alpha-grind": TEAM, "twice": TEAM, "span": TEAM}})
    )
    (tmp_path / "beta" / ".agentorc.yml").write_text(yaml.safe_dump({"teams": {"twice": TEAM}}))
    org["projects"]["alpha"] = {"repos": {"alpha": {HOST: str(tmp_path / "alpha"), "devenv": "/work/alpha"}}}
    assert defs.init(home)
    (home / "org.yml").write_text(yaml.safe_dump({**org, "place": {"alpha-grind": "devenv"}}))
    assert defs.commit("org: placed alpha-grind", home=home)
    assert cli.main(["org"]) == 0
    out = capsys.readouterr().out
    assert "alpha-grind  repo: alpha  on devenv (place)" in out
    assert f"span         shadowed by org.yml  [{tmp_path / 'alpha' / '.agentorc.yml'}]" in out
    assert "twice        refused: team 'twice' is defined twice" in out and out.count("defined twice") == 1
    # each file's last commit, the one never written said so
    assert "org: placed alpha-grind" in out and "no history yet" not in out
    assert "the home's definitions, first tracked" in out and "settings.yml  not there" in out


def test_a_whole_org_checks_ok_and_exits_0(world, capsys):
    code, got = check(capsys)
    assert (code, got) == (0, {"ok": True, "lacks": [], "warnings": []})
    assert cli.main(["org", "check"]) == 0
    assert capsys.readouterr().out.strip() == "ok: 2 teams"


def test_a_registered_checkout_that_is_not_there_is_lacking(world, capsys):
    tmp_path, home, _ = world
    (home / "repos.txt").write_text(f"{tmp_path / 'alpha'}\n{tmp_path / 'beta'}\n{tmp_path / 'gone'}\n")
    code, got = check(capsys)
    assert code == 1 and got["ok"] is False
    assert [x for x in got["lacks"] if "a registered checkout that is not there" in x] == [got["lacks"][0]]
    assert str(tmp_path / "gone") in got["lacks"][0]
    assert cli.main(["org", "check"]) == 1
    out = capsys.readouterr().out
    assert f"lacking: {tmp_path / 'gone'}" in out and out.strip().endswith("1 lacking")


def test_a_settings_team_no_definition_names_is_lacking(world, capsys):
    _, home, _ = world
    kept = {"alpha-grind": {"reserve": 5}, "old": {"reserve": 5}}
    (home / "settings.yml").write_text(yaml.safe_dump({"teams": kept}))
    code, got = check(capsys)
    assert code == 1 and len(got["lacks"]) == 1
    assert got["lacks"][0].startswith("settings.yml: teams.old — no definition names a team 'old'")


def test_a_profile_not_held_a_missing_brief_and_a_place_on_no_linked_host_are_each_lacking(world, capsys):
    tmp_path, home, org = world
    teams_ = {
        "alpha-grind": {**TEAM, "members": [{"role": "hunter", "profile": "nope"}]},
        "alpha-brief": {**TEAM, "members": [{"role": "hunter", "brief": "docs/briefs/absent.md"}]},
        "alpha-far": TEAM,
    }
    (tmp_path / "alpha" / ".agentorc.yml").write_text(yaml.safe_dump({"teams": teams_}))
    git(tmp_path / "alpha", "commit", "-qam", "teams")
    org["projects"]["alpha"] = {"repos": {"alpha": {HOST: str(tmp_path / "alpha"), "mars": "/work/alpha"}}}
    (home / "org.yml").write_text(yaml.safe_dump({**org, "place": {"alpha-far": "mars"}}))
    code, got = check(capsys)
    assert code == 1
    lacks = "\n".join(got["lacks"])
    assert "unknown profile 'nope'" in lacks
    assert "absent.md" in lacks
    assert "place.alpha-far: mars is no linked host of kmaster (devenv)" in lacks
    assert len([x for x in got["lacks"] if x.startswith("team alpha-grind:")]) == 1  # one line per lack


def test_a_name_defined_twice_is_lacking_once(world, capsys):
    tmp_path, _, _ = world
    (tmp_path / "beta" / ".agentorc.yml").write_text(yaml.safe_dump({"teams": {"alpha-grind": TEAM}}))
    git(tmp_path / "beta", "add", "-A")
    git(tmp_path / "beta", "commit", "-qm", "a team")
    code, got = check(capsys)
    assert code == 1 and len(got["lacks"]) == 1 and "defined twice" in got["lacks"][0]


def test_a_checkout_off_its_default_branch_or_holding_changes_warns_and_does_not_fail(world, capsys):
    tmp_path, home, org = world
    git(tmp_path / "beta", "checkout", "-q", "-b", "topic")
    (tmp_path / "alpha" / "scratch.txt").write_text("x")
    # a `place:` naming a team no registered repo defines is the aggregate's note, and a warning here
    (home / "org.yml").write_text(yaml.safe_dump({**org, "place": {"alpha-grnd": "devenv"}}))
    code, got = check(capsys)
    assert code == 0 and got["ok"] is True and got["lacks"] == []
    warned = "\n".join(got["warnings"])
    assert f"{tmp_path / 'beta'}: on topic, not its default branch main" in warned
    assert f"{tmp_path / 'alpha'}: holds 1 change " in warned
    assert "place.alpha-grnd: no registered repo defines a team 'alpha-grnd'" in warned
    assert cli.main(["org", "check"]) == 0
    assert capsys.readouterr().out.strip().endswith("ok: 2 teams, 3 warnings")


def test_on_a_node_both_refuse_and_name_the_home(world, capsys, monkeypatch):
    monkeypatch.setattr(cli.hosts, "is_node", lambda: True)
    monkeypatch.setattr(cli.hosts, "home_name", lambda: "homebox")
    assert cli.main(["org"]) == 1
    assert "the org lives on homebox (home): run `ao org` there" in capsys.readouterr().err
    assert cli.main(["org", "check"]) == 1
    assert "run `ao org check` there" in capsys.readouterr().err


def test_an_org_file_that_cannot_be_read_fails_the_check_in_its_own_words(world, capsys):
    _, home, _ = world
    (home / "org.yml").write_text(yaml.safe_dump({"teams": {"x": {"lead": "y"}}}))
    assert cli.main(["org", "check"]) == 1
    assert "org.yml: teams.x" in capsys.readouterr().err


def test_neither_writes_a_file_or_calls_anything_that_changes_a_record(world, capsys, monkeypatch):
    tmp_path, home, _ = world
    calls: list[str] = []

    def recording(method, **params):
        calls.append(method)
        raise cli.AgentError(f"unknown method: {method}")

    monkeypatch.setattr(cli, "call_sync", recording)
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file() and ".git" not in p.parts}
    assert cli.main(["org"]) == 0 and cli.main(["org", "check"]) == 0
    capsys.readouterr()
    after = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file() and ".git" not in p.parts}
    assert after == before
    assert set(calls) <= {"host_repos", "host_files", "host_dir"}  # reads, when anything is asked at all
