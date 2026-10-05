"""Flows (design §4.9c, TD-309 slice 1): a flow found and read — the package's built-ins and a repo's
`.agentorc/flows/<name>/` — judged usable when whole, a role's `kind`, the repo's top-level `held:`,
and a team's `flows:` refused at its start when a flow is unknown, not usable or cannot be followed."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from test_cli_teams import HOST, world  # noqa: F401 — the fixture, by name

from agentorc import flowdefs, repoconfig, teams
from agentorc import org as orgmod

pytestmark = pytest.mark.unit


def cfg_with(tmp_path: Path, text: str = "") -> repoconfig.RepoConfig:
    return repoconfig.load_text(text or None, tmp_path)


def repo_flow(root: Path, name: str, stages: list[dict], briefs: dict[str, str] | None = None, extra: str = "") -> Path:
    d = root / ".agentorc" / "flows" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "flow.yml").write_text(yaml.safe_dump({"stages": stages}) + extra)
    for f, text in (briefs or {}).items():
        (d / f).parent.mkdir(parents=True, exist_ok=True)
        (d / f).write_text(text)
    return d


# ── kind and held ────────────────────────────────────────────────────────────────────────────────


def test_every_preset_has_a_kind_and_an_overlay_may_not_write_one(tmp_path):
    kinds = {n: repoconfig.resolve_role(cfg_with(tmp_path), n).kind for n in repoconfig.PRESETS}
    assert kinds == {
        "designer": "worker",
        "grinder": "worker",
        "hunter": "worker",
        "manager": "manager",
        "techlead": "seat",
        "auditor": "seat",
        "plain": "plain",
    }
    with pytest.raises(ValueError, match=r"grinder.kind: a role's kind is its definition's"):
        cfg_with(tmp_path, "roles:\n  grinder: {kind: seat}\n")
    # a key-only role writes no kind and is a worker
    assert repoconfig.resolve_role(cfg_with(tmp_path, "roles:\n  scout: {brief: d.md}\n"), "scout").kind == "worker"


def test_held_is_a_top_level_list_of_paths(tmp_path):
    assert cfg_with(tmp_path).held is None
    assert cfg_with(tmp_path, "held: [src/sessionorc/**, docs/briefs/**]\n").held == [
        "src/sessionorc/**",
        "docs/briefs/**",
    ]
    with pytest.raises(ValueError, match="names no path"):
        cfg_with(tmp_path, "held: []\n")


# ── the built-ins ────────────────────────────────────────────────────────────────────────────────


def test_the_built_in_flows_read_as_the_design_writes_them(tmp_path):
    """`td`, `build-review` and `build` (§4.9c): their stages, lanes and briefs; `build-review`'s
    review brief is `td`'s, by `../td/review.md`. Each brief is a file."""
    got = {n: flowdefs.find(n) for n in flowdefs.BUILTIN}
    shape = {n: [(s.name, s.role, s.lane) for s in f.stages] for n, f in got.items()}
    build = ("build", "grinder", ["free-pick", "owner:grinder"])
    assert shape == {
        "td": [("design", "designer", ["design-first", "owner:designer"]), build, ("review", "techlead", [])],
        "build-review": [build, ("review", "techlead", [])],
        "build": [build],
    }
    assert got["build-review"].stages[1].path == flowdefs.PACKAGE_DIR / "td" / "review.md"
    assert all(s.path is not None and s.path.is_file() for f in got.values() for s in f.stages)
    # all three are usable anywhere: `designer` is a preset (slice 2)
    plain = cfg_with(tmp_path)
    assert all(flowdefs.load(n, plain).usable for n in flowdefs.BUILTIN)


# ── a repo's flow ────────────────────────────────────────────────────────────────────────────────


def test_a_repo_flow_is_found_by_its_directory_and_reaches_package_and_sibling_briefs(tmp_path):
    repo_flow(
        tmp_path, "base", [{"name": "build", "role": "grinder", "lane": ["free-pick"], "brief": "b.md"}], {"b.md": "x"}
    )
    repo_flow(
        tmp_path,
        "mine",
        [
            {"name": "build", "role": "grinder", "lane": ["free-pick"], "brief": "../base/b.md"},
            {"name": "review", "role": "techlead", "brief": "package:td/review.md"},
        ],
    )
    f = flowdefs.load("mine", cfg_with(tmp_path))
    assert f.place == "repo" and f.usable, f.problems
    assert f.stages[0].path == tmp_path / ".agentorc" / "flows" / "base" / "b.md"
    assert f.stages[1].path == flowdefs.PACKAGE_DIR / "td" / "review.md"
    assert flowdefs.load("nothing", cfg_with(tmp_path)) is None
    # a repo directory taking a built-in's name is never read: the built-in is the flow
    repo_flow(tmp_path, "build", [{"name": "x", "role": "grinder", "lane": ["free-pick"], "brief": "nope.md"}])
    assert flowdefs.load("build", cfg_with(tmp_path)).place == "package"


@pytest.mark.parametrize(
    ("stages", "extra", "says"),
    [
        ([{"name": "b", "role": "grinder", "lane": ["free-pick"], "brief": "gone.md"}], "", "is not a file"),
        ([{"name": "b", "role": "grinder", "lane": ["free-pick"], "brief": "b.md", "when": 1}], "", "not a stage key"),
        ([{"name": "b", "role": "grinder", "lane": ["free-pick"], "brief": "b.md"}], "owner: x\n", "not a flow key"),
        ([{"name": "b", "role": "grinder", "lane": ["free-pick"]}], "", "brief is required"),
        ([{"name": "b", "role": "techlead", "lane": ["free-pick"], "brief": "b.md"}], "", "names a worker"),
        ([{"name": "r", "role": "grinder", "brief": "b.md"}], "", "a review stage names a seat"),
        ([{"name": "r", "role": "auditor", "brief": "b.md"}], "", "TD-314"),
        ([{"name": "b", "role": "nobody", "lane": ["free-pick"], "brief": "b.md"}], "", "unknown role 'nobody'"),
        ([{"name": "b", "role": "grinder", "lane": ["whatever"], "brief": "b.md"}], "", "lane word 'whatever'"),
        ([{"name": "b", "role": "grinder", "lane": ["free-pick"], "brief": "../../x.md"}], "", "nothing further"),
        ([{"name": "b", "role": "grinder", "lane": ["free-pick"], "brief": "/etc/passwd"}], "", "never absolute"),
        (
            [
                {"name": "a", "role": "grinder", "lane": ["free-pick"], "brief": "b.md"},
                {"name": "b", "role": "grinder", "lane": ["TD-001"], "brief": "b.md"},
            ],
            "",
            "two stages name the role 'grinder'",
        ),
        ([], "", "non-empty list"),
        ([{"name": "b", "role": "grinder", "lane": ["free-pick"], "brief": "."}], "", "name a file"),
        ([{"name": "b", "role": "grinder", "lane": ["free-pick"], "brief": "sub"}], "", "is not a file"),
    ],
)
def test_a_flow_that_is_not_whole_is_not_usable_and_says_why(tmp_path, stages, extra, says):
    repo_flow(tmp_path, "f", stages, {"b.md": "brief", "sub/x.md": "a directory's file"}, extra)
    f = flowdefs.load("f", cfg_with(tmp_path))
    assert not f.usable and any(says in p for p in f.problems), f.problems


def test_lane_words_are_rule_6s_a_named_items_or_a_hunters_area(tmp_path):
    ok = [
        {"name": "b", "role": "grinder", "lane": ["free-pick", "owner:grinder", "TD-042", "#17"], "brief": "b.md"},
        {"name": "h", "role": "hunter", "lane": ["ui"], "brief": "b.md"},
    ]
    repo_flow(tmp_path, "f", ok, {"b.md": "x"})
    assert flowdefs.load("f", cfg_with(tmp_path)).usable


# ── a team's flows: ──────────────────────────────────────────────────────────────────────────────


def test_flows_on_a_team_is_a_list_of_names_each_once(tmp_path):
    t = orgmod._team("t", {"projects": ["p"], "flows": ["td", "build"]}, "teams.t", source=tmp_path)
    assert t.flows == ["td", "build"]
    assert orgmod._team("t", {"projects": ["p"]}, "teams.t", source=tmp_path).flows == []
    with pytest.raises(ValueError, match="listed twice"):
        orgmod._team("t", {"projects": ["p"], "flows": ["td", "td"]}, "teams.t", source=tmp_path)
    with pytest.raises(ValueError, match="list of flow names"):
        orgmod._team("t", {"projects": ["p"], "flows": "td"}, "teams.t", source=tmp_path)


def _with(tmp_path: Path, **team) -> orgmod.Org:
    doc = yaml.safe_load((tmp_path / "home" / "org.yml").read_text())
    doc["teams"]["ao-grind"].update(team)
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))
    return orgmod.load()


def test_a_team_listing_a_flow_it_can_follow_plans_and_one_it_cannot_is_refused(world, tmp_path):  # noqa: F811
    root = tmp_path / "agentorc"
    techlead = {"name": "techlead-ao", "home": "agentorc"}
    # nothing held: the review stage would hold every path
    org = _with(tmp_path, flows=["build-review"], techlead=techlead)
    with pytest.raises(
        teams.TeamError, match=r"build-review cannot be followed by ao-grind: nothing held — write held:"
    ):
        teams.plan(org, "ao-grind", HOST)
    (root / ".agentorc.yml").write_text("held: [src/sessionorc/**]\n")
    teams.plan(org, "ao-grind", HOST)  # followable now
    # no techlead seat
    doc = yaml.safe_load((tmp_path / "home" / "org.yml").read_text())
    del doc["teams"]["ao-grind"]["techlead"]
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))
    with pytest.raises(teams.TeamError, match="no techlead seat"):
        teams.plan(orgmod.load(), "ao-grind", HOST)
    # `build` needs no seat and holds nothing
    teams.plan(_with(tmp_path, flows=["build"]), "ao-grind", HOST)
    # td: a designer resolves (a preset), but the team starts no member of it — not followable
    with pytest.raises(teams.TeamError, match=r"td cannot be followed by ao-grind: no designer — add one to members:"):
        teams.plan(_with(tmp_path, flows=["td"], techlead=techlead), "ao-grind", HOST)
    # an unknown flow
    with pytest.raises(teams.TeamError, match=r"flows: no flow 'hunt'"):
        teams.plan(_with(tmp_path, flows=["hunt"]), "ao-grind", HOST)


def test_a_team_with_no_flows_plans_as_before_and_its_slots_take_their_kind(world, tmp_path):  # noqa: F811
    teams.plan(orgmod.load(), "ao-grind", HOST)  # the fixture's team: no flows, unchanged
    doc = yaml.safe_load((tmp_path / "home" / "org.yml").read_text())
    doc["teams"]["ao-grind"]["members"].append({"role": "techlead", "name": "tl", "home": "agentorc"})
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))
    with pytest.raises(teams.TeamError, match="a member takes a worker role, and 'techlead' is a seat"):
        teams.plan(orgmod.load(), "ao-grind", HOST)
    doc["teams"]["ao-grind"]["members"].pop()
    doc["teams"]["ao-grind"]["manager"]["role"] = "grinder"
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))
    with pytest.raises(teams.TeamError, match="manager: takes a manager role, and 'grinder' is a worker"):
        teams.plan(orgmod.load(), "ao-grind", HOST)
    doc["teams"]["ao-grind"]["manager"]["role"] = "manager"
    doc["teams"]["ao-grind"]["entries"] = {"feature": "manager"}
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))
    with pytest.raises(teams.TeamError, match="entries.feature takes a worker or a seat, and 'manager' is a manager"):
        teams.plan(orgmod.load(), "ao-grind", HOST)


@pytest.mark.unit
def test_every_template_carries_the_path_slots_and_the_path_words_moved_beside_it():
    # §4.9c *A brief has three layers*: every role template gains `{flow}` and `{stage}`; the path
    # words a team with no flow is told ship as `<role>.stage.md` and fill its `{stage}`
    for name, preset in repoconfig.PRESETS.items():
        if preset.get("brief"):
            text = (Path(repoconfig.__file__).parent / "briefs" / preset["brief"]).read_text()
            assert "{flow}" in text and "{stage}" in text, name
    cfg = repoconfig.RepoConfig()
    text, made = repoconfig.resolve_role(cfg, "grinder").compose(techlead="ao-r-techlead-1")
    assert "**This team's flow:** none" in text
    assert "Never merge a held PR yourself" in text and "ask --pr <n> ao-r-techlead-1" in text
    assert "a PR you have asked a reader about is the reader's to merge" in text  # stays in the template
    assert made["slots"]["{stage}"] == {"file": str(Path(repoconfig.__file__).parent / "briefs" / "grinder.stage.md")}
    assert list(made["slots"])[:3] == ["{repo}", "{flow}", "{stage}"]  # before the slots a stage brief may use
    text, _ = repoconfig.resolve_role(cfg, "techlead").compose()
    assert "### A held PR" in text
    text, made = repoconfig.resolve_role(cfg, "hunter").compose()  # the package ships no hunter.stage.md
    assert made["slots"]["{stage}"] == {"text": "none"} and "{stage}" not in text


@pytest.mark.unit
def test_under_a_flow_a_member_reads_its_path_line_and_its_stage_brief():
    td = flowdefs.find("td")
    held = ["src/sessionorc/**", "docs/briefs/**"]
    line = flowdefs.path_line(td, "grinder", techlead="ao-r-techlead-1", held=held)
    assert line == (
        "td: design (designer) → **build** (grinder) → review (ao-r-techlead-1, on src/sessionorc/**, "
        "docs/briefs/**) → you, through ao-r-techlead-1."
    )
    assert flowdefs.path_line(td, "manager").endswith("→ you, directly. You stand outside it.")
    cfg = repoconfig.RepoConfig()
    text, made = repoconfig.resolve_role(cfg, "grinder").compose(
        techlead="ao-r-techlead-1", flow=flowdefs.under(td, "grinder", techlead="ao-r-techlead-1", held=held)
    )
    assert f"**This team's flow:** {line}" in text
    assert "A `design-first` entry is the design stage's" in text and "ask --pr <n> ao-r-techlead-1" in text
    assert made["slots"]["{stage}"] == {"file": str(flowdefs.PACKAGE_DIR / "td" / "build.md")}
    assert made["slots"]["{flow}"] == {"text": line}
    # the techlead reads the review stage's brief; under `build` it reads none
    _, made = repoconfig.resolve_role(cfg, "techlead").compose(flow=flowdefs.under(td, "techlead"))
    assert made["slots"]["{stage}"] == {"file": str(flowdefs.PACKAGE_DIR / "td" / "review.md")}
    text, made = repoconfig.resolve_role(cfg, "techlead").compose(
        flow=flowdefs.under(flowdefs.find("build"), "techlead")
    )
    assert made["slots"]["{stage}"] == {"text": "none"} and "### A held PR" not in text


@pytest.mark.unit
def test_the_designer_is_a_preset_whose_template_wraps_it_only_under_a_flow(tmp_path):
    # §4.9c *The designer gets a template*: outside a flow its repo's brief is the whole brief (until
    # TD-310), and with none it is refused; under a flow the template wraps that brief as a supplement
    designer = repoconfig.PRESETS["designer"]
    assert designer["kind"] == "worker" and designer["lane"] == ["design-first", "owner:designer"]
    assert "icon" not in designer and "label" not in designer
    (tmp_path / "d.md").write_text("the repo's designer brief, {techlead}\n")
    cfg = repoconfig.load_text("roles: {designer: {brief: d.md}}\n", tmp_path)
    role = repoconfig.resolve_role(cfg, "designer")
    text, made = role.compose(techlead="T")
    assert text == "the repo's designer brief, T\n" and made["base"] == str(tmp_path / "d.md")
    text, made = role.compose(techlead="T", flow=flowdefs.under(flowdefs.find("td"), "designer", techlead="T"))
    assert made["base"].endswith("briefs/designer.md") and "the repo's designer brief, T" in text
    assert made["slots"]["{stage}"] == {"file": str(flowdefs.PACKAGE_DIR / "td" / "design.md")}
    with pytest.raises(ValueError, match="designer needs a brief outside a flow"):
        repoconfig.resolve_role(repoconfig.RepoConfig(), "designer").compose()
