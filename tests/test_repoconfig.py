"""`.agentorc.yml` (design §5) and the role presets over it (§4.8): defaults, the per-key merge,
a malformed file, the brief templates."""

import pytest

from agentorc import repoconfig

pytestmark = pytest.mark.unit


def test_missing_file_gives_the_defaults(tmp_path):
    cfg = repoconfig.load(tmp_path)
    assert cfg.path is None and cfg.root == tmp_path
    assert cfg.adapter == "claude-code" and cfg.worktrees == ".claude/worktrees"
    assert cfg.ready_when == ["tree_clean", "branch_pushed", "no_subagents"]
    assert cfg.commands == [] and cfg.unattended is None and cfg.controllers == [] and cfg.teams == {}
    assert cfg.ledger == "docs/technical_debt.md"
    assert [r.name for r in repoconfig.roles(cfg)] == ["grinder", "hunter", "lead", "plain"]


def test_the_section_5_example_loads(tmp_path):
    (tmp_path / ".agentorc.yml").write_text(
        """
adapter: claude-code
worktrees: .claude/worktrees
anchor: main-checkout-single
unattended:
  workers: 3
  window: {weekday: "20:00-06:00", weekend: all}
roles:
  grinder: {brief: docs/briefs/grinder.md, lane: free-pick, profile: grind}
  hunter: {brief: docs/briefs/hunter.md}
  lead: {brief: docs/briefs/orchestrator.md, grants: [control]}
  reviewer: {grants: [], lane: [ui, tests], controllers: [ui-orc]}
controllers: [orchestrator-ao-1]
ledger: docs/debt.md
teams:
  grind: {lead: {role: lead, name: orc}, members: [{role: grinder, count: 2}]}
ready_when: [tree_clean, branch_pushed, pr_merged, no_subagents, ledger_touched]
commands:
  - {name: test, run: pdm run test}
"""
    )
    cfg = repoconfig.load(tmp_path)
    assert cfg.path == tmp_path / ".agentorc.yml"
    assert cfg.unattended == {"workers": 3, "window": {"weekday": "20:00-06:00", "weekend": "all"}}
    assert cfg.controllers == ["orchestrator-ao-1"] and cfg.ledger == "docs/debt.md"
    assert cfg.teams["grind"]["lead"] == {"role": "lead", "name": "orc"}
    assert cfg.ready_when[2] == "pr_merged" and cfg.commands == [{"name": "test", "run": "pdm run test"}]
    # a repo's `roles:` overrides per key: what it does not say stays built-in
    g = repoconfig.resolve_role(cfg, "grinder")
    assert (g.brief, g.brief_source, g.lane, g.grants, g.profile) == (
        "docs/briefs/grinder.md", "repo", ["free-pick"], [], "grind"
    )  # fmt: skip
    assert g.source == "built-in + repo"
    o = repoconfig.resolve_role(cfg, "lead")
    assert o.grants == ["control"] and o.lane == [] and o.controllers == []
    r = repoconfig.resolve_role(cfg, "reviewer")
    assert r.source == "repo" and r.brief is None and r.lane == ["ui", "tests"] and r.controllers == ["ui-orc"]
    assert [x.name for x in repoconfig.roles(cfg)] == ["grinder", "hunter", "lead", "plain", "reviewer"]
    with pytest.raises(KeyError, match="unknown role 'nope'; known: grinder, hunter"):
        repoconfig.resolve_role(cfg, "nope")


def test_the_org_overlay_sits_between_built_in_and_repo(tmp_path):
    (tmp_path / ".agentorc.yml").write_text("roles: {grinder: {lane: [TD-001]}}\n")
    cfg = repoconfig.load(tmp_path)
    overlay = {"grinder": {"profile": "grind", "lane": ["free-pick"]}, "auditor": {"grants": ["control"]}}
    g = repoconfig.resolve_role(cfg, "grinder", roles_overlay=overlay)
    assert g.profile == "grind" and g.lane == ["TD-001"] and g.source == "built-in + org + repo"
    assert repoconfig.resolve_role(cfg, "auditor", roles_overlay=overlay).source == "org"
    assert "auditor" in repoconfig.role_names(cfg, overlay)


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("- a\n- b\n", "the top level must be a mapping"),
        ("ledger: [x]\n", "`ledger` must be a non-empty string"),
        ("controllers: orc\n", None),  # a single name is a list of one, not an error
        ("controllers: [1, 2]\n", "`controllers` must be a list of names"),
        ("roles: [grinder]\n", "`roles` must be a mapping"),
        ("roles: {grinder: {schedule: nightly}}\n", "`roles`.grinder.schedule is not a role key"),
        ("roles: {grinder: {grants: [fly]}}\n", "`roles`.grinder.grants: unknown grant 'fly'"),
        ("roles: {grinder: 3}\n", "`roles`.grinder must be a mapping"),
        ("commands: [{name: t}]\n", r"`commands`\[0\] needs `name` and `run`"),
        ("unattended: 3\n", "`unattended` must be a mapping"),
        ("windows: 3\n", "`windows` is not a `.agentorc.yml` key"),
        ("roles: {a: {brief: x}\n", "not valid YAML"),
    ],
)
def test_a_malformed_file_names_the_key(tmp_path, text, names):
    (tmp_path / ".agentorc.yml").write_text(text)
    if names is None:
        assert repoconfig.load(tmp_path).controllers == ["orc"]
        return
    with pytest.raises(ValueError, match=names) as e:
        repoconfig.load(tmp_path)
    assert str(tmp_path / ".agentorc.yml") in str(e.value)


def test_built_in_briefs_fill_the_lane_and_plain_has_none(tmp_path):
    cfg = repoconfig.load(tmp_path)
    text = repoconfig.resolve_role(cfg, "grinder").brief_text(["TD-027", "TD-019"])
    assert "## Lane: TD-027, TD-019" in text and "{lane}" not in text
    assert "## Lane: free-pick" in repoconfig.resolve_role(cfg, "grinder").brief_text()  # the role's default
    assert "## Area: free" in repoconfig.resolve_role(cfg, "hunter").brief_text()
    orc = repoconfig.resolve_role(cfg, "lead").brief_text()
    assert "## Members: (none given)" in orc and "control" in orc
    assert repoconfig.resolve_role(cfg, "plain").brief_text() is None
    for name in ("grinder", "hunter", "lead"):  # one screen each
        assert len(repoconfig.resolve_role(cfg, name).brief_text().splitlines()) < 40


def test_a_repo_brief_is_read_relative_to_the_repo(tmp_path):
    (tmp_path / ".agentorc.yml").write_text("roles: {grinder: {brief: briefs/g.md}, hunter: {brief: briefs/h.md}}\n")
    (tmp_path / "briefs").mkdir()
    (tmp_path / "briefs" / "g.md").write_text("grind {lane} now\n")
    cfg = repoconfig.load(tmp_path)
    assert repoconfig.resolve_role(cfg, "grinder").brief_text(["TD-001"]) == "grind TD-001 now\n"
    with pytest.raises(ValueError, match="role 'hunter': brief .*briefs/h.md cannot be read"):
        repoconfig.resolve_role(cfg, "hunter").brief_text()


def test_discover_walks_up_to_the_file_or_the_git_root(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agentorc.yml").write_text("ledger: docs/l.md\n")
    wt = repo / ".claude" / "worktrees" / "x" / "src"
    wt.mkdir(parents=True)
    assert repoconfig.discover(wt).ledger == "docs/l.md"  # the checked-in file, found from a subdirectory
    bare = tmp_path / "bare"
    (bare / ".git").mkdir(parents=True)
    (bare / "deep").mkdir()
    cfg = repoconfig.discover(bare / "deep")
    assert cfg.path is None and cfg.root == bare  # a repo without the file: defaults, and no walk past .git
    plain = tmp_path / "plain"
    plain.mkdir()
    assert repoconfig.discover(plain).root == plain


def test_orchestrator_is_a_deprecated_alias_for_lead(tmp_path, capsys, monkeypatch):
    """TD-055 step 2: the `orchestrator` role is `lead`. The old name still resolves for one release —
    as a name and as a `roles:` key in the org overlay or the repo's file — with one line naming the
    new word, and nothing lists or records `orchestrator` any more."""
    monkeypatch.setattr(repoconfig, "_warned", set())
    (tmp_path / ".agentorc.yml").write_text("roles:\n  orchestrator: {profile: repo-prof}\n")
    cfg = repoconfig.load(tmp_path)
    role = repoconfig.resolve_role(
        cfg, "orchestrator", {"orchestrator": {"lane": ["TD-1"]}, "lead": {"profile": "org"}}
    )
    assert role.name == "lead" and role.grants == ["control"] and role.brief == "lead.md"
    assert role.lane == ["TD-1"]  # the org overlay's old key folded into `lead`
    assert role.profile == "repo-prof"  # the repo layer still wins over the org layer
    assert "orchestrator" not in repoconfig.role_names(cfg, {"orchestrator": {}})
    err = capsys.readouterr().err
    assert err.count("role `orchestrator` is now `lead`") == 1  # once per process, however often it is met
    # within one layer, a new key the person wrote beside the old one wins per key
    both = repoconfig.resolve_role(
        repoconfig.RepoConfig(), "lead", {"orchestrator": {"profile": "old", "lane": ["x"]}, "lead": {"profile": "new"}}
    )
    assert both.profile == "new" and both.lane == ["x"]


def test_grants_orchestrate_in_a_role_is_read_as_control(tmp_path, capsys, monkeypatch):
    """TD-055 step 3: `grants: [orchestrate]` in a `roles:` block still loads, as `control`, and says so."""
    monkeypatch.setattr(repoconfig, "_warned", set())
    (tmp_path / ".agentorc.yml").write_text("roles:\n  reviewer: {grants: [orchestrate, control]}\n")
    role = repoconfig.resolve_role(repoconfig.load(tmp_path), "reviewer")
    assert role.grants == ["control"]
    assert "grant `orchestrate` is now `control`" in capsys.readouterr().err
