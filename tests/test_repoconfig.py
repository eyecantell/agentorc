"""`.agentorc.yml` (design §5) and the role presets over it (§4.8): defaults, the per-key merge,
a malformed file, the brief templates."""

import pytest

from agentorc import repoconfig

pytestmark = pytest.mark.unit


def test_missing_file_gives_the_defaults(tmp_path):
    cfg = repoconfig.load(tmp_path)
    assert cfg.path is None and cfg.root == tmp_path
    assert cfg.ready_when == ["tree_clean", "branch_pushed", "no_subagents"]
    assert cfg.commands == [] and cfg.unattended is None and cfg.controllers == [] and cfg.teams == {}
    assert cfg.ledger == "docs/technical_debt.md"
    assert [r.name for r in repoconfig.roles(cfg)] == ["grinder", "hunter", "manager", "techlead", "auditor", "plain"]


@pytest.mark.parametrize("key", ["adapter", "worktrees", "anchor"])
def test_a_retired_key_is_refused_naming_where_it_lives(tmp_path, key):
    # TD-149 (1): read by nothing, so a repo writing one is told where it is decided instead
    (tmp_path / ".agentorc.yml").write_text(f"{key}: x\n")
    with pytest.raises(ValueError, match=f"`{key}` is not a `.agentorc.yml` key any more") as e:
        repoconfig.load(tmp_path)
    assert repoconfig.RETIRED[key] in str(e.value)


def test_the_section_5_example_loads(tmp_path):
    (tmp_path / ".agentorc.yml").write_text(
        """
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
    assert [x.name for x in repoconfig.roles(cfg)] == [
        "grinder",
        "hunter",
        "manager",
        "techlead",
        "auditor",
        "plain",
        "reviewer",
    ]
    with pytest.raises(KeyError, match="unknown role 'nope'; known: grinder, hunter"):
        repoconfig.resolve_role(cfg, "nope")


def test_the_org_overlay_sits_between_built_in_and_repo(tmp_path):
    (tmp_path / ".agentorc.yml").write_text("roles: {grinder: {lane: [TD-001]}}\n")
    cfg = repoconfig.load(tmp_path)
    overlay = {"grinder": {"profile": "grind", "lane": ["free-pick"]}, "scout": {"grants": ["control"]}}
    g = repoconfig.resolve_role(cfg, "grinder", roles_overlay=overlay)
    assert g.profile == "grind" and g.lane == ["TD-001"] and g.source == "built-in + org + repo"
    assert repoconfig.resolve_role(cfg, "scout", roles_overlay=overlay).source == "org"
    assert "scout" in repoconfig.role_names(cfg, overlay)


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
    # design §4.9a *A wind-down is announced* (TD-053 step 5): the note to the person comes before the close (TD-125)
    assert orc.index("ran out of work at <t>. Merged this run") < orc.index("ao close $AGENTORC_SESSION")
    assert repoconfig.resolve_role(cfg, "plain").brief_text() is None
    for name in ("grinder", "hunter", "manager"):  # one screen each
        assert len(repoconfig.resolve_role(cfg, name).brief_text().splitlines()) < 40


def test_a_repo_brief_is_read_relative_to_the_repo(tmp_path):
    (tmp_path / ".agentorc.yml").write_text("roles: {grinder: {brief: briefs/g.md}, hunter: {brief: briefs/h.md}}\n")
    (tmp_path / "briefs").mkdir()
    (tmp_path / "briefs" / "g.md").write_text("grind {lane} now\n")
    cfg = repoconfig.load(tmp_path)
    # a supplement (TD-114): the template's, with the repo's text in *This repo's rules*
    text = repoconfig.resolve_role(cfg, "grinder").brief_text(["TD-001"])
    assert "## This repo's rules" in text and "\n\ngrind TD-001 now\n\n" in text and "**grinder**" in text
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


def test_old_role_names_are_unknown_roles(tmp_path):
    """TD-107: the renamed-roles table is gone. `orchestrator` and `lead` resolve to nothing unless a
    layer defines them, and a layer that does defines a role of that name, not `manager`."""
    for old in ("orchestrator", "lead"):
        with pytest.raises(KeyError, match=f"unknown role '{old}'"):
            repoconfig.resolve_role(repoconfig.RepoConfig(), old)
    (tmp_path / ".agentorc.yml").write_text("roles:\n  lead: {profile: repo-prof}\n")
    role = repoconfig.resolve_role(repoconfig.load(tmp_path), "lead")
    assert (role.name, role.profile, role.grants) == ("lead", "repo-prof", [])


def test_techlead_is_a_preset(tmp_path):
    """TD-075 step 1, design §4.9b: `techlead` is a built-in preset — `techlead.md`, no lane, no
    grants, icon `book`, label *Tech Lead* — and a repo may override its keys like any other's."""
    tl = repoconfig.resolve_role(repoconfig.RepoConfig(), "techlead")
    assert (tl.brief, tl.lane, tl.grants, tl.icon, tl.display) == ("techlead.md", [], [], "book", "Tech Lead")
    assert "**techlead**" in tl.brief_text()
    (tmp_path / ".agentorc.yml").write_text("roles:\n  techlead: {profile: strong}\n")
    assert repoconfig.resolve_role(repoconfig.load(tmp_path), "techlead").profile == "strong"


def test_techlead_placeholder_names_the_seat_or_says_none():
    """Design §4.9b `{techlead}`: a member's and a manager's brief take it, filled with the seat's
    id at a team start and `none` for a session started by hand, so no brief ever shows the
    placeholder itself. (The seat's own brief reads its id from `$AGENTORC_SESSION`.)"""
    cfg = repoconfig.RepoConfig()
    for name in ("grinder", "hunter", "manager"):
        role = repoconfig.resolve_role(cfg, name)
        assert "{techlead}" not in role.brief_text() and "`none`" in role.brief_text()
        assert "`ao-agentorc-techlead-ao-1`" in role.brief_text(techlead="ao-agentorc-techlead-ao-1")


def test_entry_rules_are_written_once_and_read_both_ways():
    """TD-219 slice 1, design §4.9 *Add an entry to the ledger*: `entry.md` holds what a new ledger
    entry needs, with `{repo}`, `{type}` and `{ledger}` slots. The session way fills them from the form;
    the techlead's brief takes the same text through `{entry}`, the slots said in words since the repo
    and type ride on the handed message — and no other preset carries it."""
    filled = repoconfig.entry_text("agentorc", "feature", "docs/technical_debt.md")
    assert "ledger of agentorc: docs/technical_debt.md, with Type feature" in filled
    assert "`**Type:** feature`" in filled and "open PR" in filled and "Summary row" in filled
    assert not any(slot in filled for slot in repoconfig.ENTRY_SLOTS)
    tl = repoconfig.resolve_role(repoconfig.RepoConfig(), "techlead")
    text, made = tl.compose([])
    assert "{entry}" not in text and "## An entry handed to you" in text
    assert repoconfig.entry_text(*(repoconfig.HANDED_ENTRY[s] for s in repoconfig.ENTRY_SLOTS)) in text
    assert list(made["slots"])[:2] == ["{repo}", "{entry}"]  # a replay fills it after the repo's brief
    for name in ("grinder", "hunter", "manager", "auditor"):
        _, made = repoconfig.resolve_role(repoconfig.RepoConfig(), name).compose([])
        assert "{entry}" not in made["slots"]


def test_grants_orchestrate_in_a_role_is_an_unknown_grant(tmp_path):
    """TD-107: `grants: [orchestrate]` in a `roles:` block is refused like any unknown grant."""
    (tmp_path / ".agentorc.yml").write_text("roles:\n  reviewer: {grants: [orchestrate, control]}\n")
    with pytest.raises(ValueError, match="unknown grant 'orchestrate'"):
        repoconfig.load(tmp_path)


def test_a_role_has_a_display_label_and_the_default_is_its_name_raised(tmp_path):
    """design §4.8 *The names* (TD-076 step 3): a preset or a `roles:` entry may carry `label:` —
    what the role badge, a team header and an Inbox row **show** in place of the key; the key is
    what everything else reads. The built-ins' are *Manager*, *Grinder*, *Hunter*; the default is
    the role's name with its first letter raised — an old badge too: `orchestrator` reads
    *Orchestrator* (TD-107)."""
    builtin = {r.name: r.display for r in repoconfig.roles(repoconfig.RepoConfig())}
    assert builtin == {
        "grinder": "Grinder",
        "hunter": "Hunter",
        "manager": "Manager",
        "techlead": "Tech Lead",
        "auditor": "Auditor",
        "plain": "Plain",
    }
    assert "label" in repoconfig.ROLE_KEYS
    assert repoconfig.default_label("orchestrator") == "Orchestrator"
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
    assert builtin == {
        "grinder": "wrench",
        "hunter": "search",
        "manager": "flag",
        "techlead": "book",
        "auditor": "eye",
        "plain": None,
    }
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


def test_the_auditor_preset_is_a_hunter_for_a_seat():
    """TD-098 step 3, design §4.8's preset table and §4.9b *Seats with a trigger*: `auditor` holds no
    grants and no lane — a seat's area is its brief's — and its built-in brief says so, fills the
    techlead, never fixes, and declares nothing."""
    role = repoconfig.resolve_role(repoconfig.RepoConfig(), "auditor")
    assert (role.grants, role.lane, role.display) == ([], [], "Auditor")
    text = role.brief_text([], techlead="ao-x-techlead")
    assert "**auditor**" in text and "## Area: (none given)" in text and "`ao-x-techlead`" in text
    assert "never fix" in text and "You declare nothing" in text
    assert "{" not in text.replace("{lane}", "")  # every placeholder filled


def test_promote_is_accepted_and_checked_ahead_of_its_build(tmp_path):
    """TD-149 (2): writing §5's `promote:` block no longer breaks `ao new` in that repo — it is
    read, both commands required, and `auto` refused as `settings.yml`'s (TD-132 runs it)."""
    ok = repoconfig.load_text("promote:\n  run: scripts/promote.sh\n  check: scripts/live_sha.sh\n", tmp_path)
    assert ok.promote == {"run": "scripts/promote.sh", "check": "scripts/live_sha.sh"}
    assert repoconfig.load_text("ledger: docs/debt.md\n", tmp_path).promote is None
    for text, names in (
        ("promote:\n  run: x\n", "needs check"),
        ("promote:\n  run: x\n  check: y\n  auto: true\n", "is not this file's"),
        ("promote:\n  run: x\n  check: ''\n", "must be a command"),
        ("promote:\n  run: x\n  check: y\n  when: nightly\n", "is not a promote key"),
        ("promote: scripts/promote.sh\n", "must be a mapping"),
    ):
        with pytest.raises(ValueError, match=names):
            repoconfig.load_text(text, tmp_path)


def test_a_role_says_when_to_message_it(tmp_path):
    """design §4.8 *A role says when to message it* (TD-162, built by TD-171): the built-ins carry a
    `message:` default, `plain` none; a layer overrides it per key, `null` takes the default away;
    one line of 120 characters at most, checked when the file is read; `to_dict` carries it."""
    cfg = repoconfig.RepoConfig()
    want = {
        "manager": "the team's work: what it picks, its pace, a member that is stuck or should stop",
        "grinder": "its own card only: the entry it holds, a finding on its PR",
        "hunter": "an area to look at; it files, never fixes",
        "auditor": "what its trigger counts: the last n PRs, the period",
    }
    for name, line in want.items():
        assert repoconfig.resolve_role(cfg, name).message == line
    assert repoconfig.resolve_role(cfg, "techlead").message.endswith("— an ask fills the seat")
    assert repoconfig.resolve_role(cfg, "plain").message is None
    (tmp_path / ".agentorc.yml").write_text(
        "roles:\n  grinder: {message: the ledger entry it claimed}\n  designer: {message: a design-first entry}\n"
        "  hunter: {message: null}\n"
    )
    loaded = repoconfig.load(tmp_path)
    assert repoconfig.resolve_role(loaded, "grinder").message == "the ledger entry it claimed"
    assert repoconfig.resolve_role(loaded, "designer").to_dict()["message"] == "a design-first entry"
    assert repoconfig.resolve_role(loaded, "hunter").message is None
    for bad, why in (
        ("message: 7", "one line"),
        ('message: "a\\nb"', "one line"),
        ("message: " + "x" * 121, "longer than 120"),
    ):
        (tmp_path / ".agentorc.yml").write_text(f"roles:\n  grinder: {{{bad}}}\n")
        with pytest.raises(ValueError, match=why):
            repoconfig.load(tmp_path)


def test_a_role_has_saved_prompts_replaced_whole_per_layer(tmp_path):
    """design §4.8 *A role has saved prompts* (TD-161, built by TD-170): `prompts:` is a list of
    `{label, text}`, the label one line of 24 characters at most, the text non-empty and verbatim;
    checked when the file is read, naming the role and the entry; the key replaced whole at each
    layer, never merged; the built-ins carry none."""
    assert repoconfig.resolve_role(repoconfig.RepoConfig(), "grinder").prompts == []
    (tmp_path / ".agentorc.yml").write_text(
        "roles:\n  plain:\n    prompts:\n      - {label: review PR, text: 'Review PR $n as the cadence says.'}\n"
        "      - {label: sweep, text: /stranded-work}\n"
    )
    cfg = repoconfig.load(tmp_path)
    org = {"plain": {"prompts": [{"label": "org one", "text": "from the org"}]}}
    got = repoconfig.resolve_role(cfg, "plain", org)
    assert got.prompts == [{"label": "review PR", "text": "Review PR $n as the cadence says."},
                           {"label": "sweep", "text": "/stranded-work"}]  # fmt: skip
    assert got.to_dict()["prompts"][1]["label"] == "sweep"
    assert repoconfig.resolve_role(repoconfig.RepoConfig(), "plain", org).prompts[0]["label"] == "org one"
    for bad, why in (
        ("prompts: sweep", "must be a list"),
        ("prompts: [{label: x}]", r"prompts\[0\]\.text"),
        ("prompts: [{text: y}]", r"prompts\[0\]\.label must be one line"),
        ("prompts: [{label: " + "x" * 25 + ", text: y}]", r"grinder\.prompts\[0\]\.label is longer than 24"),
        ("prompts: [{label: a, text: b, run: c}]", "mapping of label and text"),
    ):
        (tmp_path / ".agentorc.yml").write_text(f"roles:\n  grinder: {{{bad}}}\n")
        with pytest.raises(ValueError, match=why):  # the error names the role and the entry
            repoconfig.load(tmp_path)
