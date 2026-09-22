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
    assert [r.name for r in repoconfig.roles(cfg)] == ["grinder", "hunter", "manager", "techlead", "plain"]


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
  manager: {brief: docs/briefs/manager.md, grants: [control]}
  reviewer: {grants: [], lane: [ui, tests], controllers: [ui-orc]}
controllers: [orchestrator-ao-1]
ledger: docs/debt.md
teams:
  grind: {manager: {role: manager, name: orc}, members: [{role: grinder, count: 2}]}
ready_when: [tree_clean, branch_pushed, pr_merged, no_subagents, ledger_touched]
commands:
  - {name: test, run: pdm run test}
"""
    )
    cfg = repoconfig.load(tmp_path)
    assert cfg.path == tmp_path / ".agentorc.yml"
    assert cfg.unattended == {"workers": 3, "window": {"weekday": "20:00-06:00", "weekend": "all"}}
    assert cfg.controllers == ["orchestrator-ao-1"] and cfg.ledger == "docs/debt.md"
    assert cfg.teams["grind"]["manager"] == {"role": "manager", "name": "orc"}
    assert cfg.ready_when[2] == "pr_merged" and cfg.commands == [{"name": "test", "run": "pdm run test"}]
    # a repo's `roles:` overrides per key: what it does not say stays built-in
    g = repoconfig.resolve_role(cfg, "grinder")
    assert (g.brief, g.brief_source, g.lane, g.grants, g.profile) == (
        "docs/briefs/grinder.md", "repo", ["free-pick"], [], "grind"
    )  # fmt: skip
    assert g.source == "built-in + repo"
    o = repoconfig.resolve_role(cfg, "manager")
    assert o.grants == ["control"] and o.lane == [] and o.controllers == []
    r = repoconfig.resolve_role(cfg, "reviewer")
    assert r.source == "repo" and r.brief is None and r.lane == ["ui", "tests"] and r.controllers == ["ui-orc"]
    assert [x.name for x in repoconfig.roles(cfg)] == ["grinder", "hunter", "manager", "techlead", "plain", "reviewer"]
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
    orc = repoconfig.resolve_role(cfg, "manager").brief_text()
    assert "## Members: (none given)" in orc and "control" in orc
    # design §4.9a *A wind-down is announced* (TD-053 step 5): the board line comes before the close
    assert orc.index("the team ran out of work at") < orc.index("ao close $AGENTORC_SESSION")
    assert repoconfig.resolve_role(cfg, "plain").brief_text() is None
    for name in ("grinder", "hunter", "manager"):  # one screen each
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


def test_orchestrator_and_lead_are_deprecated_aliases_for_manager(tmp_path, capsys, monkeypatch):
    """TD-055 step 2, repointed by TD-076 step 2: `orchestrator` and `lead` are both `manager` — one
    lookup, never a chain (design §4.8 *The names*). Each old name still resolves for one release —
    as a name and as a `roles:` key in the org overlay or the repo's file — with one line naming the
    new word, and nothing lists or records either old name any more."""
    monkeypatch.setattr(repoconfig, "_warned", set())
    assert set(repoconfig.ROLE_ALIASES.values()).isdisjoint(repoconfig.ROLE_ALIASES)  # no chain
    (tmp_path / ".agentorc.yml").write_text("roles:\n  orchestrator: {profile: repo-prof}\n")
    cfg = repoconfig.load(tmp_path)
    role = repoconfig.resolve_role(
        cfg, "orchestrator", {"orchestrator": {"lane": ["TD-1"]}, "manager": {"profile": "org"}}
    )
    assert role.name == "manager" and role.grants == ["control"] and role.brief == "manager.md"
    assert role.lane == ["TD-1"]  # the org overlay's old key folded into `manager`
    assert role.profile == "repo-prof"  # the repo layer still wins over the org layer
    for _ in range(2):  # met twice, said once
        assert repoconfig.resolve_role(repoconfig.RepoConfig(), "lead").name == "manager"
    names = repoconfig.role_names(cfg, {"orchestrator": {}, "lead": {}})
    assert "orchestrator" not in names and "lead" not in names
    err = capsys.readouterr().err
    assert err.count("role `orchestrator` is now `manager` (TD-076)") == 1  # once per process
    assert err.count("role `lead` is now `manager` (TD-076)") == 1
    # within one layer, a new key the person wrote beside the old one wins per key
    both = repoconfig.resolve_role(
        repoconfig.RepoConfig(), "manager", {"lead": {"profile": "old", "lane": ["x"]}, "manager": {"profile": "new"}}
    )
    assert both.profile == "new" and both.lane == ["x"]


def test_techlead_is_a_preset_and_a_reserved_word_is_still_refused_by_name(tmp_path, monkeypatch):
    """TD-075 step 1, design §4.9b: `techlead` is a built-in preset — `techlead.md`, no lane, no
    grants, icon `book`, label *Tech Lead* — and a repo may override its keys like any other's.
    The reserved-words table it left stays (§4.8 *The names*): a word in it is still refused with
    its reason wherever a role is named or defined, never as an unknown role."""
    tl = repoconfig.resolve_role(repoconfig.RepoConfig(), "techlead")
    assert (tl.brief, tl.lane, tl.grants, tl.icon, tl.display) == ("techlead.md", [], [], "book", "Tech Lead")
    assert "**techlead**" in tl.brief_text()
    (tmp_path / ".agentorc.yml").write_text("roles:\n  techlead: {profile: strong}\n")
    assert repoconfig.resolve_role(repoconfig.load(tmp_path), "techlead").profile == "strong"
    assert repoconfig.RESERVED_ROLES == {}
    monkeypatch.setitem(repoconfig.RESERVED_ROLES, "advisor", "reserved for a test")
    with pytest.raises(ValueError, match="`advisor` is reserved for a test"):
        repoconfig.resolve_role(repoconfig.RepoConfig(), "advisor")
    (tmp_path / ".agentorc.yml").write_text("roles:\n  advisor: {grants: [control]}\n")
    with pytest.raises(ValueError, match=r"`roles`\.advisor: role `advisor` is reserved"):
        repoconfig.load(tmp_path)


def test_techlead_placeholder_names_the_seat_or_says_none():
    """Design §4.9b `{techlead}`: a member's and a manager's brief take it, filled with the seat's
    id at a team start and `none` for a session started by hand, so no brief ever shows the
    placeholder itself. (The seat's own brief reads its id from `$AGENTORC_SESSION`.)"""
    cfg = repoconfig.RepoConfig()
    for name in ("grinder", "hunter", "manager"):
        role = repoconfig.resolve_role(cfg, name)
        assert "{techlead}" not in role.brief_text() and "`none`" in role.brief_text()
        assert "`ao-agentorc-techlead-ao-1`" in role.brief_text(techlead="ao-agentorc-techlead-ao-1")


def test_grants_orchestrate_in_a_role_is_read_as_control(tmp_path, capsys, monkeypatch):
    """TD-055 step 3: `grants: [orchestrate]` in a `roles:` block still loads, as `control`, and says so."""
    monkeypatch.setattr(repoconfig, "_warned", set())
    (tmp_path / ".agentorc.yml").write_text("roles:\n  reviewer: {grants: [orchestrate, control]}\n")
    role = repoconfig.resolve_role(repoconfig.load(tmp_path), "reviewer")
    assert role.grants == ["control"]
    assert "grant `orchestrate` is now `control`" in capsys.readouterr().err


def test_a_role_has_a_display_label_and_the_default_is_its_name_raised(tmp_path):
    """design §4.8 *The names* (TD-076 step 3): a preset or a `roles:` entry may carry `label:` —
    what the role badge, a team header and an Inbox row **show** in place of the key; the key is
    what everything else reads. The built-ins' are *Manager*, *Grinder*, *Hunter*; the default is
    the role's name with its first letter raised, and an old name is read through the renamed-roles
    table, so a record badged `orchestrator` or `lead` reads *Manager*."""
    builtin = {r.name: r.display for r in repoconfig.roles(repoconfig.RepoConfig())}
    assert builtin == {
        "grinder": "Grinder",
        "hunter": "Hunter",
        "manager": "Manager",
        "techlead": "Tech Lead",
        "plain": "Plain",
    }
    assert "label" in repoconfig.ROLE_KEYS
    assert repoconfig.default_label("orchestrator") == repoconfig.default_label("lead") == "Manager"
    assert repoconfig.default_label("reviewer") == "Reviewer" and repoconfig.default_label("") == ""
    # a layer's label wins per key, as every other key does, and to_dict says what the page shows
    (tmp_path / ".agentorc.yml").write_text("roles:\n  grinder: {label: TD grinder}\n  reviewer: {icon: eye}\n")
    cfg = repoconfig.load(tmp_path)
    grinder = repoconfig.resolve_role(cfg, "grinder")
    assert grinder.display == "TD grinder" and grinder.icon == "wrench" and grinder.to_dict()["label"] == "TD grinder"
    assert repoconfig.resolve_role(cfg, "reviewer").display == "Reviewer"  # no label: the default
    assert repoconfig.resolve_role(cfg, "hunter", {"hunter": {"label": "Bug hunter"}}).display == "Bug hunter"
    # a label is one short line of text, checked when the file is read
    for bad, why in (("label: ''", "one line"), ("label: 7", "one line"), ("label: [a]", "one line"),
                     ("label: \"a\\nb\"", "one line"), ("label: " + "x" * 41, "longer than 40")):  # fmt: skip
        (tmp_path / ".agentorc.yml").write_text(f"roles:\n  grinder: {{{bad}}}\n")
        with pytest.raises(ValueError, match=why):
            repoconfig.load(tmp_path)


def test_a_role_may_carry_an_icon_from_the_fixed_set(tmp_path):
    """TD-074 step 4, design §4.8 *Role presets*: a preset may carry an `icon:` — one name from the
    set the UI ships, never markup from a config file. The built-ins carry `manager: flag`,
    `grinder: wrench`, `hunter: search` and `plain` none; a layer overrides it per key like every
    other key; an unknown name is refused when the file is read, as an unknown grant is."""
    builtin = {r.name: r.icon for r in repoconfig.roles(repoconfig.RepoConfig())}
    assert builtin == {"grinder": "wrench", "hunter": "search", "manager": "flag", "techlead": "book", "plain": None}
    assert "icon" in repoconfig.ROLE_KEYS
    assert set(builtin.values()) - {None} <= set(repoconfig.ICONS)
    # the repo's own file overrides it, and its other keys are untouched
    (tmp_path / ".agentorc.yml").write_text("roles:\n  grinder: {icon: terminal}\n  reviewer: {icon: eye}\n")
    cfg = repoconfig.load(tmp_path)
    grinder = repoconfig.resolve_role(cfg, "grinder")
    assert grinder.icon == "terminal" and grinder.brief == "grinder.md" and grinder.lane == ["free-pick"]
    assert repoconfig.resolve_role(cfg, "reviewer").icon == "eye"
    assert grinder.to_dict()["icon"] == "terminal"
    # built-in < org < repo, per key: the org layer speaks for a role the repo's file leaves alone
    overlay = {"hunter": {"icon": "book"}, "grinder": {"icon": "shield"}}
    assert repoconfig.resolve_role(cfg, "hunter", overlay).icon == "book"
    assert repoconfig.resolve_role(cfg, "grinder", overlay).icon == "terminal"  # the repo still wins
    # an explicit null is "no icon", and the layer that says so is the one that counts
    (tmp_path / ".agentorc.yml").write_text("roles:\n  lead: {icon: null}\n")
    assert repoconfig.resolve_role(repoconfig.load(tmp_path), "lead").icon is None
    # an unknown name is refused when the file is read, naming the key and what is known
    (tmp_path / ".agentorc.yml").write_text("roles:\n  lead: {icon: rocket}\n")
    with pytest.raises(ValueError, match=r"icon: unknown icon 'rocket'"):
        repoconfig.load(tmp_path)
    # `person` is the card's interactive mark (TD-095): reserved, and the refusal says so
    (tmp_path / ".agentorc.yml").write_text("roles:\n  lead: {icon: person}\n")
    with pytest.raises(ValueError, match=r"icon: 'person' is reserved for the card's mark of an interactive"):
        repoconfig.load(tmp_path)
    (tmp_path / ".agentorc.yml").write_text("roles:\n  lead: {icon: 7}\n")
    with pytest.raises(ValueError, match="icon must be a string"):
        repoconfig.load(tmp_path)
