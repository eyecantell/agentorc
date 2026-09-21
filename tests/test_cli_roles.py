"""`ao new --role` and `ao roles` (design §4.7, §4.8, §5): what the preset and the repo's
`.agentorc.yml` fill in, with the RPC mocked — the agent's side of `create` is tested elsewhere."""

import json
import re

import pytest

from agentorc import cli

pytestmark = pytest.mark.unit

ORC = "ao-repo-orc"
FLEET = [
    {"id": ORC, "name": "orc", "state": "idle", "dir": "", "repo": "", "capabilities": ["control"]},
    {"id": "ao-repo-w1", "name": "w1", "state": "idle", "dir": "", "repo": ""},
]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # A temp AGENTORC_HOME or `orgmod.load()` reads the machine's real `~/.agentorc/org.yml`, whose
    # `roles:` overlay would change every source label here (found 2026-09-13, once one existed).
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    (tmp_path / ".git").mkdir()
    for s in FLEET:
        s["dir"] = s["repo"] = str(tmp_path)
    calls: list[tuple[str, dict]] = []

    def fake_call(method, **params):
        calls.append((method, params))
        if method == "list":
            return FLEET
        if method == "create":
            return {"id": "ao-repo-new", "name": params["name"], "adapter": params["adapter"], "dir": params["dir"],
                    "controllers": params.get("controllers") or [], "previous_run": None}  # fmt: skip
        raise AssertionError(method)

    monkeypatch.setattr(cli, "call_sync", fake_call)
    monkeypatch.chdir(tmp_path)
    return tmp_path, calls


def created(calls):
    return next(p for m, p in calls if m == "create")


def test_role_fills_brief_lane_grants_profile_and_the_record(repo, capsys):
    root, calls = repo
    (root / ".agentorc.yml").write_text("roles: {grinder: {profile: grind}}\nledger: docs/debt.md\n")
    assert cli.main(["new", "g1", "--role", "grinder", "--lane", "TD-027,TD-019"]) == 0
    p = created(calls)
    assert p["role"] == "grinder" and p["lane"] == ["TD-027", "TD-019"] and p["capabilities"] == []
    assert p["profile"] == "grind" and p["ledger"] == "docs/debt.md"
    assert "## Lane: TD-027, TD-019" in p["prompt"] and p["controllers"] == []
    assert "starts with no controller" in capsys.readouterr().out  # the no-controller line still prints

    calls.clear()
    # the flags win: --profile, --prompt, --lane; --grant adds to the preset's grants
    assert cli.main(["new", "o1", "--role", "manager", "-p", "paul", "--prompt", "hi", "--grant", "control"]) == 0
    p = created(calls)
    assert p["profile"] == "paul" and p["prompt"] == "hi" and p["capabilities"] == ["control"]
    assert p["role"] == "manager" and p["lane"] == []

    calls.clear()
    assert cli.main(["new", "h1", "--role", "hunter"]) == 0
    assert created(calls)["lane"] == ["free"] and "## Area: free" in created(calls)["prompt"]

    calls.clear()
    assert cli.main(["new", "p1"]) == 0  # no role: nothing filled, as before
    p = created(calls)
    assert p["role"] == "" and p["prompt"] is None and p["capabilities"] == [] and p["ledger"] == "docs/debt.md"


def test_role_orchestrator_or_lead_still_starts_a_manager_and_says_so(repo, capsys, monkeypatch):
    """TD-055 step 2, repointed by TD-076 step 2: `--role orchestrator` and `--role lead` are
    deprecated aliases for one release — the session is started as `manager`, with the manager
    brief and grants, and stderr names the new word. `--role techlead` is refused by name."""
    from agentorc import repoconfig

    monkeypatch.setattr(repoconfig, "_warned", set())
    _, calls = repo
    for old in ("orchestrator", "lead"):
        calls.clear()
        assert cli.main(["new", f"o-{old}", "--role", old]) == 0
        p = created(calls)
        assert p["role"] == "manager" and p["capabilities"] == ["control"] and "**manager**" in p["prompt"]
        assert f"role `{old}` is now `manager` (TD-076)" in capsys.readouterr().err
    calls.clear()
    assert cli.main(["new", "t1", "--role", "techlead"]) != 0
    assert not any(m == "create" for m, _ in calls)
    assert "`techlead` is reserved" in capsys.readouterr().err


def test_grant_orchestrate_on_the_command_line_is_sent_as_control(repo, capsys):
    """TD-055 step 3: `--grant orchestrate` is accepted for a release, sent as `control`, and named."""
    _, calls = repo
    assert cli.main(["new", "o3", "--grant", "orchestrate"]) == 0
    assert created(calls)["capabilities"] == ["control"]
    assert "grant `orchestrate` is now `control`" in capsys.readouterr().err


def test_controllers_default_from_the_role_then_the_repo_and_names_resolve(repo, capsys):
    root, calls = repo
    (root / ".agentorc.yml").write_text("controllers: [orc]\nroles: {hunter: {controllers: [w1]}}\n")
    assert cli.main(["new", "g1", "--role", "grinder"]) == 0
    assert created(calls)["controllers"] == [ORC]  # the repo's, resolved from the name to the id
    assert "starts with no controller" not in capsys.readouterr().out
    calls.clear()
    assert cli.main(["new", "h1", "--role", "hunter"]) == 0
    assert created(calls)["controllers"] == ["ao-repo-w1"]  # the preset's own list wins over the repo's
    calls.clear()
    assert cli.main(["new", "x", "--controller", "ao-other-id"]) == 0
    assert created(calls)["controllers"] == ["ao-other-id"]  # --controller given: the config is not consulted
    calls.clear()
    assert cli.main(["shell", "s"]) == 0
    assert created(calls)["controllers"] == [] and created(calls)["role"] == ""  # a shell asks for nothing


def test_a_configured_controller_that_is_not_running_is_skipped_not_refused(repo, capsys):
    """Design §4.8: a stale `controllers:` default must not block every start in the repo — each
    name that does not resolve is dropped with one line, and the no-controller line prints when
    none remain. An explicit `--controller` naming an unknown session is still an error."""
    root, calls = repo
    (root / ".agentorc.yml").write_text("controllers: [ghost-orc, orc]\nroles: {hunter: {controllers: [gone]}}\n")
    assert cli.main(["new", "g1"]) == 0
    out, err = capsys.readouterr()
    assert err.strip() == "controllers: `ghost-orc` from .agentorc.yml (repo) is not running — skipped"
    assert created(calls)["controllers"] == [ORC] and "starts with no controller" not in out
    calls.clear()
    assert cli.main(["new", "h1", "--role", "hunter"]) == 0
    out, err = capsys.readouterr()
    assert err.strip() == "controllers: `gone` from .agentorc.yml (role hunter) is not running — skipped"
    assert created(calls)["controllers"] == [] and "starts with no controller: nobody may act on it" in out
    calls.clear()
    assert cli.main(["new", "x", "--controller", "ghost-orc"]) == 1
    assert "no session named ghost-orc" in capsys.readouterr().err
    assert not [m for m, _ in calls if m == "create"]


def test_a_role_with_an_empty_controllers_list_means_nobody_not_the_repo_default(repo, capsys):
    """`controllers: []` on a preset is a deliberate *nobody* (the module docstring's own example),
    which a truth test cannot tell from a preset that never mentioned controllers — so the repo's
    default must not fill it in. Found by the review of PR #116."""
    root, calls = repo
    (root / ".agentorc.yml").write_text("controllers: [orc]\nroles: {hunter: {controllers: []}}\n")
    assert cli.main(["new", "h1", "--role", "hunter"]) == 0
    out, _ = capsys.readouterr()
    assert created(calls)["controllers"] == [] and "starts with no controller: nobody may act on it" in out
    calls.clear()
    assert cli.main(["new", "g1", "--role", "grinder"]) == 0  # says nothing → the repo's list applies
    assert created(calls)["controllers"] == [ORC]


def test_an_unknown_role_or_grant_is_an_error_naming_it(repo, capsys):
    root, calls = repo
    assert cli.main(["--json", "new", "g1", "--role", "sage"]) == 1
    assert json.loads(capsys.readouterr().out)["error"].startswith("unknown role 'sage'; known: grinder")
    (root / ".agentorc.yml").write_text("roles: {grinder: {grants: [fly]}}\n")
    assert cli.main(["new", "g1", "--role", "grinder"]) == 1
    assert "grants: unknown grant 'fly'" in capsys.readouterr().err


def test_ao_roles_lists_built_ins_and_the_repo_overrides_marking_the_source(repo, capsys):
    root, calls = repo
    assert cli.main(["roles"]) == 0
    out = capsys.readouterr().out
    assert (
        out.startswith("roles: built-in only")
        and re.search(r"^manager +\[built-in\]", out, re.M)
        and "grants: control" in out
    )
    (root / ".agentorc.yml").write_text(
        "controllers: [orc]\nroles:\n  grinder: {profile: grind, brief: docs/briefs/g.md}\n  reviewer: {lane: [ui]}\n"
    )
    assert cli.main(["roles"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"roles from {root / '.agentorc.yml'}"
    assert "grinder   [built-in + repo]  lane: free-pick  grants: none  profile: grind  controllers: orc" in out
    assert "brief: docs/briefs/g.md" in out and "reviewer  [repo]  lane: ui" in out
    assert "label: Grinder" in out and "label: Reviewer" in out  # what the page shows (design §4.8 *The names*)
    assert cli.main(["roles", "--json", "-d", str(root)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["controllers"] == ["orc"] and [r["name"] for r in data["roles"]][-1] == "reviewer"
    assert next(r for r in data["roles"] if r["name"] == "grinder")["source"] == "built-in + repo"
    (root / ".agentorc.yml").write_text("roles: {grinder: {grants: [fly]}}\n")
    assert cli.main(["roles"]) == 1 and "unknown grant 'fly'" in capsys.readouterr().err
