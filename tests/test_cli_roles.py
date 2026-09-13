"""`ao new --role` and `ao roles` (design §4.7, §4.8, §5): what the preset and the repo's
`.agentorc.yml` fill in, with the RPC mocked — the agent's side of `create` is tested elsewhere."""

import json

import pytest

from agentorc import cli

pytestmark = pytest.mark.unit

ORC = "ao-repo-orc"
FLEET = [
    {"id": ORC, "name": "orc", "state": "idle", "dir": "", "repo": "", "capabilities": ["orchestrate"]},
    {"id": "ao-repo-w1", "name": "w1", "state": "idle", "dir": "", "repo": ""},
]


@pytest.fixture
def repo(tmp_path, monkeypatch):
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
    assert (
        cli.main(["new", "o1", "--role", "orchestrator", "-p", "paul", "--prompt", "hi", "--grant", "orchestrate"]) == 0
    )
    p = created(calls)
    assert p["profile"] == "paul" and p["prompt"] == "hi" and p["capabilities"] == ["orchestrate"]
    assert p["role"] == "orchestrator" and p["lane"] == []

    calls.clear()
    assert cli.main(["new", "h1", "--role", "hunter"]) == 0
    assert created(calls)["lane"] == ["free"] and "## Area: free" in created(calls)["prompt"]

    calls.clear()
    assert cli.main(["new", "p1"]) == 0  # no role: nothing filled, as before
    p = created(calls)
    assert p["role"] == "" and p["prompt"] is None and p["capabilities"] == [] and p["ledger"] == "docs/debt.md"


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


def test_an_unknown_controller_name_or_role_is_an_error_naming_it(repo, capsys):
    root, calls = repo
    (root / ".agentorc.yml").write_text("controllers: [ghost-orc]\n")
    assert cli.main(["new", "g1"]) == 1
    err = capsys.readouterr().err
    assert "controllers: from" in err and ".agentorc.yml (repo): ghost-orc: no session named ghost-orc" in err
    assert not [m for m, _ in calls if m == "create"]
    assert cli.main(["--json", "new", "g1", "--role", "sage"]) == 1
    assert json.loads(capsys.readouterr().out)["error"].startswith("unknown role 'sage'; known: grinder")
    (root / ".agentorc.yml").write_text("roles: {grinder: {grants: [fly]}}\n")
    assert cli.main(["new", "g1", "--role", "grinder"]) == 1
    assert "grants: unknown grant 'fly'" in capsys.readouterr().err


def test_ao_roles_lists_built_ins_and_the_repo_overrides_marking_the_source(repo, capsys):
    root, calls = repo
    assert cli.main(["roles"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("roles: built-in only") and "orchestrator  [built-in]" in out and "grants: orchestrate" in out
    (root / ".agentorc.yml").write_text(
        "controllers: [orc]\nroles:\n  grinder: {profile: grind, brief: docs/briefs/g.md}\n  reviewer: {lane: [ui]}\n"
    )
    assert cli.main(["roles"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"roles from {root / '.agentorc.yml'}"
    assert "grinder       [built-in + repo]  lane: free-pick  grants: none  profile: grind  controllers: orc" in out
    assert "brief: docs/briefs/g.md" in out and "reviewer      [repo]  lane: ui" in out
    assert cli.main(["roles", "--json", "-d", str(root)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["controllers"] == ["orc"] and [r["name"] for r in data["roles"]][-1] == "reviewer"
    assert next(r for r in data["roles"] if r["name"] == "grinder")["source"] == "built-in + repo"
    (root / ".agentorc.yml").write_text("roles: {grinder: {grants: [fly]}}\n")
    assert cli.main(["roles"]) == 1 and "unknown grant 'fly'" in capsys.readouterr().err
