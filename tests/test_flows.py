"""Flows (design §4.9c, TD-309 slice 1): a flow found and read — the package's built-ins and a repo's
`.agentorc/flows/<name>/` — judged usable when whole, a role's `kind`, the repo's top-level `held:`,
and a team's `flows:` refused at its start when a flow is unknown, not usable or cannot be followed."""

from __future__ import annotations

import json
import shutil
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
    # a key-only role defines nothing (§4.9c, TD-313): refused when the file is read, and never resolved
    (tmp_path / ".agentorc.yml").write_text("roles:\n  scout: {brief: d.md}\n")
    with pytest.raises(ValueError, match="`roles`.scout: unknown role 'scout'"):
        repoconfig.load(tmp_path)
    with pytest.raises(KeyError, match="unknown role 'scout'"):
        repoconfig.resolve_role(cfg_with(tmp_path, "roles:\n  scout: {brief: d.md}\n"), "scout")


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


@pytest.mark.unit
def test_an_org_flow_is_found_over_a_repos_and_never_over_a_built_in(tmp_path, monkeypatch):
    # §4.9c *Where flows and roles live* (TD-313): the org's `~/.agentorc/flows/<name>/` over a
    # repo's, whole; a directory taking a built-in's name is never read, in either place
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    hunt = [{"name": "find", "role": "hunter", "lane": ["free"], "brief": "find.md"}]
    repo_flow(tmp_path / "r", "hunt", hunt, {"find.md": "the repo's find words\n"})
    assert flowdefs.find("hunt", tmp_path / "r").place == "repo"
    org_flow = flowdefs.org_dir() / "hunt"
    assert org_flow == home / "flows" / "hunt"
    org_flow.mkdir(parents=True)
    (org_flow / "flow.yml").write_text(yaml.safe_dump({"stages": hunt}))
    (org_flow / "find.md").write_text("the org's find words\n")
    got = flowdefs.load("hunt", cfg_with(tmp_path / "r"))
    assert got.place == "org" and got.dir == org_flow and got.usable, got.problems
    assert got.stages[0].path == org_flow / "find.md"
    # without a repo too: an org flow is every team's
    assert flowdefs.find("hunt").place == "org"
    (flowdefs.org_dir() / "td").mkdir()
    (flowdefs.org_dir() / "td" / "flow.yml").write_text("stages: []\n")
    assert flowdefs.find("td").place == "package"
    rows = {(r["name"], r["source"]): r for r in flowdefs.visible([tmp_path / "r"])}
    assert rows[("hunt", str(org_flow))]["usable"]
    assert "the org's flow hunt" in rows[("hunt", str(tmp_path / "r" / ".agentorc" / "flows" / "hunt"))]["shadowed"]
    assert "built-in's name" in rows[("td", str(flowdefs.org_dir() / "td"))]["shadowed"]
    (flowdefs.org_dir() / ".hidden").mkdir()
    (flowdefs.org_dir() / ".hidden" / "flow.yml").write_text("stages: []\n")
    assert ".hidden" not in {r["name"] for r in flowdefs.visible([])}  # never found, so never listed


@pytest.mark.unit
def test_an_org_flow_cannot_be_followed_by_a_team_on_a_node(tmp_path, monkeypatch):
    # §4.9c: a node team's briefs are read on the node, and the org's directories are the home's
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    d = flowdefs.org_dir() / "hunt"
    d.mkdir(parents=True)
    (d / "flow.yml").write_text("stages:\n  - {name: find, role: hunter, lane: [free], brief: find.md}\n")
    (d / "find.md").write_text("find\n")
    hunt = flowdefs.load("hunt", cfg_with(tmp_path))
    assert flowdefs.unfollowable(hunt, {"hunter"}, techlead=False, held=(), team="cm-grind") == []
    why = flowdefs.unfollowable(hunt, {"hunter"}, techlead=False, held=(), team="cm-grind", node="contractmatch")
    # and a member started under it reads its stage brief as a flow's, so no *flow changed* is raised
    from agentorc import teamrun

    assert teamrun._is_stage(d / "find.md")
    assert flowdefs.cannot_follow("hunt", "cm-grind", why) == (
        "hunt cannot be followed by cm-grind: an org flow, and cm-grind runs on contractmatch — define it in "
        "the repo, or drop hunt from flows:"
    )


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


def test_a_session_started_into_a_team_reads_its_current_flow(world, tmp_path):  # noqa: F811
    # §4.9c item 5, TD-309 slice 2a: `ao new --team` and the form's Team pick (`teams.brief_ids` with
    # the role and its repo) fill `{flow}` and `{stage}` from the team's current flow
    root = tmp_path / "agentorc"
    (root / ".agentorc.yml").write_text("held: [src/sessionorc/**]\n")
    cfg = repoconfig.load(root)
    org = _with(tmp_path, flows=["build-review", "build"], techlead={"name": "techlead-ao", "home": "agentorc"})
    ids = teams.brief_ids(org, "ao-grind", HOST, "grinder", cfg)
    seat = ids["techlead"]
    assert seat and ids["flow"].text == (
        f"build-review: **build** (grinder) → review ({seat}, on src/sessionorc/**) → you, through {seat}."
    )
    text, made = repoconfig.resolve_role(cfg, "grinder").compose(**ids)
    assert made["slots"]["{stage}"] == {"file": str(flowdefs.PACKAGE_DIR / "build-review" / "build.md")}
    assert f"ask --pr <n> {seat}" in text
    # without the role and repo, or for a team with no flows, nothing of a flow: `{flow}` reads none
    assert "flow" not in teams.brief_ids(org, "ao-grind", HOST)
    assert "flow" not in teams.brief_ids(_with(tmp_path, flows=[]), "ao-grind", HOST, "grinder", cfg)


@pytest.mark.unit
def test_a_node_members_repo_stage_brief_is_read_on_its_checkout(tmp_path):
    # TD-309 (2): a repo flow's stage brief for a node member is read across the link with the rest of
    # its compose, never from this host's disk; a `package:` brief is this host's own, as `check` reads
    node = tmp_path / "on-node"  # the node's checkout: nothing of it is on this disk
    there = {
        node / ".agentorc" / "flows" / "mine" / "flow.yml": (
            "stages:\n  - {name: build, role: grinder, lane: [free-pick], brief: build.md}\n"
            "  - {name: review, role: techlead, brief: 'package:td/review.md'}\n"
        ),
        node / ".agentorc" / "flows" / "mine" / "build.md": "the node's own build words\n",
    }

    def read(path: Path) -> str | None:
        if not Path(path).is_relative_to(node):
            raise OSError(f"{path} is outside the checkout")
        return there.get(Path(path))

    cfg = repoconfig.load_text("held: [src/**]\n", node)
    mine = flowdefs.load("mine", cfg, read=read)
    assert mine is not None and mine.usable, mine.problems
    text, made = repoconfig.resolve_role(cfg, "grinder").compose(read=read, flow=flowdefs.under(mine, "grinder"))
    assert "the node's own build words" in text
    assert made["slots"]["{stage}"] == {"file": str(node / ".agentorc" / "flows" / "mine" / "build.md")}
    _, made = repoconfig.resolve_role(cfg, "techlead").compose(read=read, flow=flowdefs.under(mine, "techlead"))
    assert made["slots"]["{stage}"] == {"file": str(flowdefs.PACKAGE_DIR / "td" / "review.md")}
    # a node that stops answering between the check and the compose refuses the member, by its file
    del there[node / ".agentorc" / "flows" / "mine" / "build.md"]
    with pytest.raises(ValueError, match="stage brief .*build.md cannot be read"):
        repoconfig.resolve_role(cfg, "grinder").compose(read=read, flow=flowdefs.under(mine, "grinder"))


@pytest.mark.unit
def test_an_edit_to_a_stage_brief_is_brief_changed(tmp_path):
    # TD-309's *Done when*: `{stage}` is a file slot, so rule 7 reads an edit to it as it reads one to
    # the repo's brief (design §6 rule 7, §4.9c *Briefs*)
    from sessionorc import brief

    flow_dir = tmp_path / ".agentorc" / "flows" / "mine"
    flow_dir.mkdir(parents=True)
    stage = "{name: build, role: grinder, lane: [free-pick], brief: build.md}"
    (flow_dir / "flow.yml").write_text(f"stages:\n  - {stage}\n")
    (flow_dir / "build.md").write_text("build words\n")
    cfg = repoconfig.load(tmp_path)
    mine = flowdefs.load("mine", cfg)
    assert mine is not None and mine.usable, mine.problems
    _, made = repoconfig.resolve_role(cfg, "grinder").compose(flow=flowdefs.under(mine, "grinder"))
    was = brief.record(made)
    assert was is not None and brief.changed(was)[0] == []
    (flow_dir / "build.md").write_text("other build words\n")
    assert brief.changed(was)[0] == [str(flow_dir / "build.md")]
    assert "other build words" in brief.fill(made)[0]


def test_a_session_started_into_a_node_team_under_an_org_flow_composes_as_with_no_flow(world, tmp_path):  # noqa: F811
    # review of TD-313 slice 1: `ao new --team` and the form reach a node's checkout through `read`;
    # an org flow is not followable there, so `{flow}` reads none rather than a brief from the wrong host
    d = flowdefs.org_dir() / "hunt"
    d.mkdir(parents=True)
    (d / "flow.yml").write_text("stages:\n  - {name: find, role: hunter, lane: [free], brief: find.md}\n")
    (d / "find.md").write_text("find\n")
    org = _with(tmp_path, flows=["hunt"])
    team, cfg = org.teams["ao-grind"], repoconfig.load(tmp_path / "agentorc")
    assert teams.flow_for(org, team, cfg, "hunter").stage == d / "find.md"
    assert teams.flow_for(org, team, cfg, "hunter", read=lambda p: None) is None


def test_a_flow_that_cannot_be_read_on_its_host_composes_as_no_flow(world, tmp_path):  # noqa: F811
    # review of TD-309 slice 2a: a node's checkout that does not answer is never a 500 past the form
    root = tmp_path / "agentorc"
    cfg = repoconfig.load(root)
    org = _with(tmp_path, flows=["mine"])

    def down(path: Path) -> str | None:
        raise OSError("contractmatch is not answering")

    team = org.teams["ao-grind"]
    assert teams.flow_for(org, team, cfg, "grinder", read=down) is None


# ── the compile at start (TD-309 slice 3a) ──────────────────────────────────────────────────────────


def _team_doc(tmp_path: Path) -> dict:
    return yaml.safe_load((tmp_path / "home" / "org.yml").read_text())


def _write(tmp_path: Path, doc: dict) -> orgmod.Org:
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))
    return orgmod.load()


def test_a_start_under_a_flow_compiles_lanes_the_reader_and_the_path(world, tmp_path):  # noqa: F811
    """§4.9c *What a flow compiles to*: a member's lane is its own, else its stage's; a member stage's
    role takes the techlead on the repos' `held:`, its own `review:` set aside and said once; a
    seat, the manager and a member outside the flows take none; each brief carries the path."""
    (tmp_path / "agentorc" / ".agentorc.yml").write_text("held: [src/sessionorc/**, docs/briefs/**]\n")
    doc = _team_doc(tmp_path)
    t = doc["teams"]["ao-grind"]
    t.update(flows=["build-review"], techlead={"name": "techlead-ao", "home": "agentorc"})
    del t["members"][0]["lane"]  # the grinders take the stage's lane
    doc["roles"]["grinder"]["review"] = {"reader": "techlead", "held": ["src/agentorc/**"]}
    p = teams.plan(_write(tmp_path, doc), "ao-grind", HOST)
    assert p.flow == "build-review" and not p.sit_out
    grinders = [x for x in p.members if x.role == "grinder"]
    assert [x.lane for x in grinders] == [["free-pick", "owner:grinder"]] * 2
    reader = {"reader": "techlead", "held": ["docs/briefs/**", "src/sessionorc/**"], "bound": teams.REVIEW_BOUND}
    assert all(x.review == reader for x in grinders)
    hunter = next(x for x in p.members if x.role == "hunter")
    assert hunter.lane == ["ui"] and hunter.review is None  # outside the flows: its own lane, no reader
    assert p.techlead.review is None and p.lead.review is None
    assert "grinder's review: set aside — the flow says what waits" in p.notes
    seat = p.techlead_id
    assert f"build-review: **build** (grinder) → review ({seat}, on src/sessionorc/**, docs/briefs/**)" in (
        grinders[0].prompt
    )
    assert p.techlead.prompt_from["slots"]["{stage}"] == {"file": str(flowdefs.PACKAGE_DIR / "td" / "review.md")}
    assert "You stand outside it." in hunter.prompt and "You stand outside it." in p.lead.prompt
    # `build` has no review stage: nobody gets a reader
    t["flows"] = ["build"]
    p = teams.plan(_write(tmp_path, doc), "ao-grind", HOST)
    assert p.flow == "build" and all(x.review is None for x in p.launches)
    # no flows: today's start, the role's own `review:` kept and the written lane
    t["flows"] = []
    t["members"][0]["lane"] = "free-pick"
    p = teams.plan(_write(tmp_path, doc), "ao-grind", HOST)
    assert p.flow is None and not any("set aside" in n for n in p.notes)
    assert next(x for x in p.members if x.role == "grinder").review["held"] == ["src/agentorc/**"]


def test_a_member_the_current_flow_does_not_use_sits_out(world, tmp_path):  # noqa: F811
    """§4.9c *Members the flow does not use sit out*: a designer, a stage of `td` and of no stage of
    `build-review`, is not started while `build-review` is current, and is under `td`."""
    (tmp_path / "agentorc" / ".agentorc.yml").write_text("held: [src/sessionorc/**]\n")
    doc = _team_doc(tmp_path)
    t = doc["teams"]["ao-grind"]
    t.update(flows=["build-review", "td"], techlead={"name": "techlead-ao", "home": "agentorc"})
    t["members"].append({"role": "designer", "name": "designer-ao", "home": "agentorc"})
    p = teams.plan(_write(tmp_path, doc), "ao-grind", HOST)
    assert p.sit_out == ["designer-ao"] and "designer-ao" not in [x.name for x in p.launches]
    assert "designer-ao: sits out under build-review" in p.notes
    assert "hunt" in [x.name for x in p.members]  # outside every flow: runs
    t["flows"] = ["td", "build-review"]
    p = teams.plan(_write(tmp_path, doc), "ao-grind", HOST)
    designer = next(x for x in p.members if x.name == "designer-ao")
    assert not p.sit_out and designer.review == {
        "reader": "techlead",
        "held": ["src/sessionorc/**"],
        "bound": teams.REVIEW_BOUND,
    }


def test_a_session_started_into_a_team_takes_the_flows_reader(world, tmp_path):  # noqa: F811
    """§4.9c items 2 and 3 outside a start: a person's session and a member stage's role take the
    flow's reader; a role with no member stage takes none; a team with no flow keeps today's rule."""
    root = tmp_path / "agentorc"
    (root / ".agentorc.yml").write_text("held: [src/sessionorc/**]\n")
    cfg = repoconfig.load(root)
    org = _with(tmp_path, flows=["build-review"], techlead={"name": "techlead-ao", "home": "agentorc"})
    team = org.teams["ao-grind"]
    reader = {"reader": "techlead", "held": ["src/sessionorc/**"], "bound": teams.REVIEW_BOUND}
    assert teams.flow_review(org, team, cfg) == (True, reader)
    assert teams.flow_review(org, team, cfg, "grinder") == (True, reader)
    assert teams.flow_review(org, team, cfg, "hunter") == (True, None)
    bare = _with(tmp_path, flows=[])
    assert teams.flow_review(bare, bare.teams["ao-grind"], cfg) == (False, None)


# ── Members… and Add entry under flows (TD-309 slice 3b) ─────────────────────────────────────────


def test_remove_refuses_the_last_member_of_a_role_a_listed_flow_needs(world, tmp_path):  # noqa: F811
    """§4.9c *Every listed flow must be followable*: **Remove** refuses the last designer while `td`
    is listed, naming the flow, and leaves the file as it was; a grinder of two goes."""
    from test_cli_teams import org_doc

    from agentorc import cli, teamrun

    (tmp_path / "agentorc" / ".agentorc.yml").write_text("held: [src/sessionorc/**]\n")
    doc = org_doc(tmp_path)
    t = doc["teams"]["ao-grind"]
    t.update(flows=["build-review", "td"], techlead={"name": "techlead-ao", "home": "agentorc"})
    t["members"].append({"role": "designer", "name": "designer-ao", "home": "agentorc"})
    path = tmp_path / "home" / "org.yml"
    path.write_text(yaml.safe_dump(doc, default_flow_style=None))
    before = path.read_text()
    with pytest.raises(teams.TeamError, match=r"^td needs a designer: drop td from flows: first$"):
        teamrun.remove_member(cli.call_sync, path, "ao-grind", index=2, role="designer", host=HOST)
    assert path.read_text() == before
    got = teamrun.remove_member(cli.call_sync, path, "ao-grind", index=0, role="grinder", host=HOST)
    assert got["did"] == "grind count: 2 → 1"
    with pytest.raises(teams.TeamError, match=r"^build-review needs a grinder"):
        teamrun.remove_member(cli.call_sync, path, "ao-grind", index=0, role="grinder", host=HOST)
    # a role no listed flow stages goes, as before
    assert teamrun.remove_member(cli.call_sync, path, "ao-grind", index=1, role="hunter", host=HOST)["did"] == (
        "removed hunt"
    )


def test_a_feature_entry_opens_the_flows_design_stage(world, tmp_path):  # noqa: F811
    """§4.9c item 4: `entries.feature` defaults to the role of the current flow's stage whose lane
    holds `design-first`; a debt stays the techlead's; the team's own `entries:` wins."""
    (tmp_path / "agentorc" / ".agentorc.yml").write_text("held: [src/sessionorc/**]\n")
    org = _with(tmp_path, flows=["td"], techlead={"name": "techlead-ao", "home": "agentorc"})
    team = org.teams["ao-grind"]
    assert teams.entry_role(org, team, "feature", HOST, HOST) == "designer"
    assert teams.entry_role(org, team, "debt", HOST, HOST) == "techlead"
    org = _with(tmp_path, flows=["build-review"])
    assert teams.entry_role(org, org.teams["ao-grind"], "feature", HOST, HOST) == "techlead"  # no design stage
    org = _with(tmp_path, flows=["td"], entries={"feature": "grinder"})
    assert teams.entry_role(org, org.teams["ao-grind"], "feature", HOST, HOST) == "grinder"


# ── the setting: which listed flow runs (TD-309 slice 4) ──────────────────────────────────────────


def test_the_flow_setting_is_a_name_the_agent_takes_without_a_definition():
    from sessionorc import settings as settings_mod

    assert settings_mod.parse_team({"flow": "build-review"}) == {"flow": "build-review"}
    for bad in ("", "  ", "td review", 3):
        with pytest.raises(ValueError, match="flow is the name of a flow"):
            settings_mod.parse_team({"flow": bad})


def test_the_person_picks_a_listed_flow_and_an_unlisted_pick_reads_as_the_first(world, tmp_path):  # noqa: F811
    """§4.9c *A team lists its flows, and the person picks one*: `teams.<team>.flow` turns the team
    to a flow it lists; absent, the first; a value it no longer lists reads as the first, said."""
    (tmp_path / "agentorc" / ".agentorc.yml").write_text("held: [src/sessionorc/**]\n")
    doc = _team_doc(tmp_path)
    t = doc["teams"]["ao-grind"]
    t.update(flows=["td", "build-review"], techlead={"name": "techlead-ao", "home": "agentorc"})
    t["members"].append({"role": "designer", "name": "designer-ao", "home": "agentorc"})
    org = _write(tmp_path, doc)
    assert teams.current_flow(org.teams["ao-grind"]) == "td"
    picked = orgmod.with_settings(org, {"ao-grind": {"flow": "build-review"}, "other": {"flow": "td"}})
    assert picked is not org and org.teams["ao-grind"].flow == ""  # a copy: the definition never carries it
    team = picked.teams["ao-grind"]
    assert teams.current_flow(team) == "build-review" and teams.flow_unlisted(team) == ""
    p = teams.plan(picked, "ao-grind", HOST)  # the start compiles the pick
    assert p.flow == "build-review" and p.sit_out == ["designer-ao"]
    gone = orgmod.with_settings(org, {"ao-grind": {"flow": "build"}}).teams["ao-grind"]
    assert teams.current_flow(gone) == "td"
    assert teams.flow_unlisted(gone) == "teams.ao-grind.flow is build, which ao-grind does not list — it runs td"
    assert orgmod.with_settings(org, {"ao-grind": {"on_work": "ask"}}) is org  # no pick: the org as read


def test_ao_team_flow_lists_the_flows_and_writes_the_pick(world, tmp_path, capsys, monkeypatch):  # noqa: F811
    """`ao team flow <team> [<flow>]` (design §4.7): the flows, the current marked, each strip and why
    one cannot be followed; a pick of a listed flow is written through `set_settings`, any other refused."""
    from agentorc import cli

    _, state = world
    (tmp_path / "agentorc" / ".agentorc.yml").write_text("held: [src/sessionorc/**]\n")
    doc = _team_doc(tmp_path)
    doc["teams"]["ao-grind"].update(
        flows=["build", "td", "build-review"], techlead={"name": "techlead-ao", "home": "agentorc"}
    )
    _write(tmp_path, doc)
    assert cli.main(["team", "flow", "ao-grind"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "* build         build → you, through ao-agentorc-techlead-ao",
        "  td            design → build → review → you, through ao-agentorc-techlead-ao",
        "                td cannot be followed by ao-grind: no designer — add one to members:, or drop td from flows:",
        "  build-review  build → review → you, through ao-agentorc-techlead-ao",
    ]
    assert cli.main(["team", "flow", "ao-grind", "hunt"]) != 0
    assert "lists build, td, build-review, not 'hunt'" in capsys.readouterr().err
    assert cli.main(["--json", "team", "flow", "ao-grind", "build-review"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert state["teams"]["ao-grind"] == {"flow": "build-review"}
    assert got["flow"] == "build-review" and [r["current"] for r in got["flows"]] == [False, False, True]
    assert cli.main(["team", "flow", "ao-grind"]) == 0  # read back from the home's setting
    assert capsys.readouterr().out.splitlines()[-1].startswith("* build-review ")
    # a session's `ao` reads the pick as the person's does: the file, not the person-only `settings` read
    monkeypatch.setenv("AGENTORC_SESSION", "ao-agentorc-grinder-ao-1")
    assert teams.current_flow(cli._org_here().teams["ao-grind"]) == "build-review"
    monkeypatch.delenv("AGENTORC_SESSION")
    del doc["teams"]["ao-grind"]["flows"]
    _write(tmp_path, doc)
    assert cli.main(["team", "flow", "ao-grind"]) != 0
    assert "lists no flows:" in capsys.readouterr().err


# ── the listings (TD-309 slice 6) ────────────────────────────────────────────────────────────────


def test_ao_team_list_carries_the_flows_and_the_feature_role(world, tmp_path, capsys):  # noqa: F811
    """§4.9c *What is shown*: `ao team list` and its `--json` carry `flows`, `flow` and why a flow
    cannot be followed; `entries.feature` reads as the current flow fills it."""
    from agentorc import cli

    (tmp_path / "agentorc" / ".agentorc.yml").write_text("held: [src/sessionorc/**]\n")
    doc = _team_doc(tmp_path)
    t = doc["teams"]["ao-grind"]
    t.update(flows=["td", "build-review"], techlead={"name": "techlead-ao", "home": "agentorc"})
    t["members"].append({"role": "designer", "name": "designer-ao", "home": "agentorc"})
    _write(tmp_path, doc)
    assert cli.main(["--json", "team", "list"]) == 0
    row = next(r for r in json.loads(capsys.readouterr().out)["teams"] if r["name"] == "ao-grind")
    assert row["flow"] == "td" and [f["name"] for f in row["flows"]] == ["td", "build-review"]
    assert row["entries"]["feature"] == "designer" and row["flow_note"] == ""
    assert cli.main(["team", "list"]) == 0
    assert "  flow: td — design → build → review → you, through ao-agentorc-techlead-ao  (also lists build-review)" in (
        capsys.readouterr().out
    )
    t["members"].pop()  # no designer: td cannot be followed, said under the team
    _write(tmp_path, doc)
    assert cli.main(["team", "list"]) == 0
    assert "td cannot be followed by ao-grind: no designer" in capsys.readouterr().out
    assert cli.main(["--json", "team", "list"]) == 0
    other = [r for r in json.loads(capsys.readouterr().out)["teams"] if r["name"] != "ao-grind"]
    assert all(r["flow"] is None and r["flows"] == [] for r in other)  # a team with no flows: none


def test_ao_org_lists_every_flow_and_check_fails_on_one_not_usable(world, tmp_path, capsys):  # noqa: F811
    """§4.7: `ao org` lists the built-ins and each registered repo's flows with source and whether
    usable; `ao org check` fails on a flow that is not usable, and a repo flow taking a built-in's
    name is listed as not read."""
    from agentorc import cli

    root = tmp_path / "agentorc"
    repo_flow(
        root, "hunt", [{"name": "find", "role": "hunter", "lane": ["free"], "brief": "find.md"}], {"find.md": "x"}
    )
    repo_flow(root, "td", [{"name": "x", "role": "grinder", "lane": ["free-pick"], "brief": "x.md"}])
    assert cli.main(["--json", "org"]) == 0
    flows = {(f["name"], f["source"]): f for f in json.loads(capsys.readouterr().out)["flows"]}
    assert all(flows[(n, "package")]["usable"] for n in flowdefs.BUILTIN)
    hunt = flows[("hunt", str(root / ".agentorc" / "flows" / "hunt"))]
    assert hunt["usable"], hunt["problems"]
    assert flows[("td", str(root / ".agentorc" / "flows" / "td"))]["shadowed"].startswith("not read: td is a built-in")
    assert cli.main(["org"]) == 0
    assert f"flow hunt          usable  [{root / '.agentorc' / 'flows' / 'hunt'}]" in capsys.readouterr().out
    assert cli.main(["org", "check"]) == 0
    capsys.readouterr()
    repo_flow(root, "broken", [{"name": "b", "role": "grinder", "lane": ["free-pick"], "brief": "gone.md"}])
    assert cli.main(["org", "check"]) == 1
    assert "lacking: flow broken (" in capsys.readouterr().out


def test_a_flow_file_that_cannot_be_read_is_a_problem_of_the_flow_not_a_crash(tmp_path):
    """Review of #1068: `ao org` and its check list every flow, so one bad file is that flow's problem."""
    d = repo_flow(tmp_path, "bad", [{"name": "b", "role": "grinder", "lane": ["free-pick"], "brief": "b.md"}])
    (d / "flow.yml").write_bytes(b"stages: \xff\xfe\n")
    (tmp_path / ".agentorc" / "flows" / "stray.txt").write_text("not a flow\n")
    rows = {f["name"]: f for f in flowdefs.visible([tmp_path])}
    assert not rows["bad"]["usable"] and "could not be read" in rows["bad"]["problems"][0]
    assert "stray.txt" not in rows and all(rows[n]["usable"] for n in flowdefs.BUILTIN)


def test_ao_org_check_warns_of_each_key_the_flow_fills_the_same(world, tmp_path, capsys):  # noqa: F811
    """§4.9c *What is shown*: `ao org check` warns, per team, of each key it writes that its current
    flow would fill with the same value — a lane in its written order, `entries.feature`, a stage
    role's `review:` in the repo's `roles:` with `held` as a set — and fails on none of them."""
    from agentorc import cli

    held = "held: [src/sessionorc/**, docs/briefs/**]\n"
    review = "roles:\n  grinder:\n    review: {reader: techlead, held: [docs/briefs/**, src/sessionorc/**]}\n"
    (tmp_path / "agentorc" / ".agentorc.yml").write_text(held + review)
    doc = _team_doc(tmp_path)
    t = doc["teams"]["ao-grind"]
    t.update(flows=["td"], techlead={"name": "techlead-ao", "home": "agentorc"}, entries={"feature": "designer"})
    t["members"][0]["lane"] = ["free-pick", "owner:grinder"]
    t["members"].append({"role": "designer", "name": "design", "lane": ["owner:designer", "design-first"]})
    org = _write(tmp_path, doc)
    teams.plan(org, "ao-grind", HOST)
    got = teams.flow_redundant(org, org.teams["ao-grind"], HOST, HOST)
    assert got == [
        "team ao-grind: grind's lane: free-pick, owner:grinder is what flow td fills — org.yml may drop it",
        "team ao-grind: entries.feature: designer is what flow td fills — org.yml may drop it",
        "team ao-grind: grinder's review: in .agentorc.yml is the reader flow td gives — the repo may drop it "
        "(a session outside a flow still reads it)",
    ]  # the designer's lane is in another order, its pick order: not the same; the hunter is outside the flow
    assert cli.main(["org", "check"]) == 0
    out = capsys.readouterr().out
    assert "warning: team ao-grind: grind's lane: free-pick, owner:grinder is what flow td fills" in out
    # a held set that differs, and no flow at all: nothing to say
    (tmp_path / "agentorc" / ".agentorc.yml").write_text(held + review.replace("docs/briefs/**, ", ""))
    t["members"][0]["lane"] = "free-pick"
    t.pop("entries")
    org = _write(tmp_path, doc)
    assert teams.flow_redundant(org, org.teams["ao-grind"], HOST, HOST) == []
    t["flows"] = []
    t["members"][0]["lane"] = ["free-pick", "owner:grinder"]
    org = _write(tmp_path, doc)
    assert teams.flow_redundant(org, org.teams["ao-grind"], HOST, HOST) == []


# ── the switch: the records against the compile, and Apply (TD-309 slice 5c) ─────────────────────


def _switching(world, tmp_path, monkeypatch, flows):  # noqa: F811
    """A live ao-grind under `flows[0]`, its records as the start created them, each carrying the
    `brief` the home records (the stage file among its sources); `relaunch` calls answered and kept."""
    from agentorc import cli, teamrun

    _, state = world
    (tmp_path / "agentorc" / ".agentorc.yml").write_text("held: [src/sessionorc/**]\n")
    doc = _team_doc(tmp_path)
    t = doc["teams"]["ao-grind"]
    t.update(flows=flows, techlead={"name": "techlead-ao", "home": "agentorc"})
    del t["members"][0]["lane"]
    t["members"].append({"role": "designer", "name": "designer-ao", "home": "agentorc"})
    org = _write(tmp_path, doc)
    plan, _ = teamrun.start(cli.call_sync, org, "ao-grind", HOST)
    for x, rec in zip(plan.launches, state["sessions"], strict=True):
        stage = teamrun.stage_of(x.prompt_from)
        rec.update(brief={"sources": [{"path": stage}] if stage else []}, seat=(x.trigger if x.seat else None))
    fake = cli.call_sync

    def call(method, **params):
        if method == "relaunch":
            state["calls"].append((method, params))
            return next(s for s in state["sessions"] if s["id"] == params["id"])
        return fake(method, **params)

    monkeypatch.setattr(cli, "call_sync", call)
    state["calls"].clear()
    return state, doc


def test_a_team_running_its_flow_reads_no_difference(world, tmp_path, capsys, monkeypatch):  # noqa: F811
    from agentorc import cli

    _switching(world, tmp_path, monkeypatch, ["td", "build-review"])
    assert cli.main(["--json", "team", "flow", "ao-grind"]) == 0
    assert json.loads(capsys.readouterr().out)["differences"] == []
    assert cli.main(["team", "flow", "ao-grind"]) == 0
    assert "flow changed" not in capsys.readouterr().out


def test_a_live_add_under_the_flow_raises_no_flow_changed(world, tmp_path, capsys, monkeypatch):  # noqa: F811
    # TD-309's *Done when*: **Members…** Add starts the member with the current flow's compiled lane,
    # reader and stage brief (§4.5a *Members dialog*), so the team still reads as running its flow
    from agentorc import cli, teamrun

    state, _ = _switching(world, tmp_path, monkeypatch, ["td", "build-review"])
    got = teamrun.add_member(cli.call_sync, tmp_path / "home" / "org.yml", "ao-grind", HOST, role="grinder")
    (made,) = got["created"]
    (create,) = [p for m, p in state["calls"] if m == "create"]
    assert teamrun.stage_of(create["prompt_from"]).endswith("flows/td/build.md")
    rec = next(s for s in state["sessions"] if s["id"] == made["id"])
    rec.update(brief={"sources": [{"path": teamrun.stage_of(create["prompt_from"])}]})
    assert cli.main(["--json", "team", "flow", "ao-grind"]) == 0
    assert json.loads(capsys.readouterr().out)["differences"] == []


def test_a_switch_sits_the_designer_out_and_relaunches_whose_stage_or_reader_moved(
    world,  # noqa: F811
    tmp_path,
    capsys,
    monkeypatch,  # noqa: F811
):
    """§4.9c *Switching*: `ao team flow <team> <flow>` writes the pick and applies it — the designer
    `build-review` gives no stage sits out by the home's `relaunch {sit_out}`, a grinder whose stage
    brief moved is handed its new launch, and the seat whose review brief is the same is untouched;
    then `build` takes the grinders' reader off and the seat's stage brief away."""
    from agentorc import cli

    state, _ = _switching(world, tmp_path, monkeypatch, ["td", "build-review", "build"])
    assert cli.main(["team", "flow", "ao-grind", "build-review"]) == 0
    out = capsys.readouterr().out
    assert state["teams"]["ao-grind"] == {"flow": "build-review"}
    calls = [(m, p) for m, p in state["calls"] if m == "relaunch"]
    sat = [p for _, p in calls if p.get("sit_out")]
    assert [p["id"] for p in sat] == ["ao-agentorc-designer-ao"]
    handed = {p["id"]: p["launch"] for _, p in calls if "launch" in p}
    assert sorted(handed) == ["ao-agentorc-grind-1", "ao-agentorc-grind-2"]
    launch = handed["ao-agentorc-grind-1"]
    assert sorted(launch) == ["lane", "prompt", "prompt_from", "review"]
    assert launch["prompt_from"]["slots"]["{stage}"]["file"].endswith("build-review/build.md")
    assert "designer-ao: sits out under build-review" in out
    assert "grind-1: relaunched — stage td/build.md → build-review/build.md" in out
    # the home took them (the fake does not): the records now read as the new launch
    for s in state["sessions"]:
        if s["id"] in handed:
            s["brief"] = {"sources": [{"path": handed[s["id"]]["prompt_from"]["slots"]["{stage}"]["file"]}]}
        if s["id"] == "ao-agentorc-designer-ao":
            s.update(state="closed", closed_for={"why": "sit_out"})
    state["calls"].clear()
    assert cli.main(["--json", "team", "flow", "ao-grind"]) == 0
    assert json.loads(capsys.readouterr().out)["differences"] == []
    # `build`: no review stage — the reader comes off (sent as None, so the home removes it)
    assert cli.main(["--json", "team", "flow", "ao-grind", "build"]) == 0
    got = json.loads(capsys.readouterr().out)["apply"]
    handed = {p["id"]: p["launch"] for m, p in state["calls"] if m == "relaunch"}
    assert handed["ao-agentorc-grind-1"]["review"] is None
    assert handed["ao-agentorc-techlead-ao"]["prompt_from"]["slots"]["{stage}"].get("file") is None
    lines = [d["line"] for d in got["applied"]]
    assert any(x.startswith("grind-1: relaunched — reader techlead → none") for x in lines)


def test_a_switch_back_starts_the_member_that_sat_out(world, tmp_path, capsys, monkeypatch):  # noqa: F811
    from agentorc import cli

    state, _ = _switching(world, tmp_path, monkeypatch, ["build-review", "td"])
    assert "ao-agentorc-designer-ao" not in [s["id"] for s in state["sessions"]]  # sat out at the start
    assert cli.main(["--json", "team", "flow", "ao-grind"]) == 0
    assert json.loads(capsys.readouterr().out)["differences"] == []
    assert cli.main(["team", "flow", "ao-grind", "td"]) == 0
    assert "designer-ao: starts under td" in capsys.readouterr().out
    made = [p for m, p in state["calls"] if m == "create"]
    assert [p["name"] for p in made] == ["designer-ao"] and made[0]["controllers"] == ["ao-agentorc-orc-ao"]
    assert made[0]["lane"] == ["design-first", "owner:designer"]


def test_apply_leaves_a_persons_session_and_an_unreachable_member_and_says_so(
    world,  # noqa: F811
    tmp_path,
    capsys,
    monkeypatch,  # noqa: F811
):
    from agentorc import cli

    state, _ = _switching(world, tmp_path, monkeypatch, ["td", "build-review"])
    for s in state["sessions"]:
        if s["name"] == "designer-ao":
            s["unattended"] = False  # a person's own session in the team (§4.9 *A person in the team*)
        if s["name"] == "grind-2":
            s.update(state="unreachable", host="devenv")
    assert cli.main(["--json", "team", "flow", "ao-grind", "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["apply"]["applied"] == []  # td is current: nothing differs
    assert cli.main(["--json", "team", "flow", "ao-grind", "build-review"]) == 0
    got = json.loads(capsys.readouterr().out)["apply"]
    assert [d["name"] for d in got["applied"]] == ["grind-1"]
    skipped = {d["name"]: d["line"] for d in got["skipped"]}
    assert skipped == {
        "designer-ao": "designer-ao: sits out under build-review — a person's session, left running: close it yourself",
        "grind-2": "grind-2: devenv is not answering — applied to the others",
    }
    assert not [p for m, p in state["calls"] if m == "relaunch" and p["id"] != "ao-agentorc-grind-1"]
    assert cli.main(["team", "flow", "ao-grind", "td", "--apply"]) == 2


def test_a_switch_says_which_prs_stay_with_their_reader(world, tmp_path, capsys, monkeypatch):  # noqa: F811
    """§4.9c *What a switch leaves alone*: a PR already asked of the techlead stays its, and the
    apply says so from the seat's `prs_waiting.asks` (TD-333) — the sender by its name, an address
    this home does not list as given."""
    from agentorc import cli

    state, _ = _switching(world, tmp_path, monkeypatch, ["td", "build"])
    tl = next(s for s in state["sessions"] if s["name"] == "techlead-ao")
    tl["prs_waiting"] = {
        "n": 3,
        "oldest": "2026-10-05T09:00:00Z",
        "asks": [
            {"from": "ao-agentorc-grind-1", "pr": 1020},
            {"from": "ao-x-w@devenv", "pr": 1021},
            {"from": f"ao-agentorc-grind-2@{HOST}", "pr": 1022},
        ],
    }
    assert cli.main(["team", "flow", "ao-grind", "build"]) == 0
    out = capsys.readouterr().out
    assert "  grind-1's PR #1020 stays with techlead-ao" in out
    assert "  ao-x-w@devenv's PR #1021 stays with techlead-ao" in out
    assert "  grind-2's PR #1022 stays with techlead-ao" in out
    assert cli.main(["--json", "team", "flow", "ao-grind", "--apply"]) == 0
    got = json.loads(capsys.readouterr().out)["apply"]
    assert got["stays"][0] == {
        "from": "ao-agentorc-grind-1",
        "pr": 1020,
        "reader": "techlead-ao",
        "line": "grind-1's PR #1020 stays with techlead-ao",
    }
    # a team with no ask waiting says nothing of it
    tl["prs_waiting"] = None
    assert cli.main(["team", "flow", "ao-grind", "--apply"]) == 0
    assert "stays with" not in capsys.readouterr().out


def test_ao_team_list_says_flow_changed_while_the_records_differ(world, tmp_path, capsys, monkeypatch):  # noqa: F811
    """§4.9c *Switching*: a client reading a live team compares it with its current flow — a pick
    written and not yet applied (the page's, before slice 4b applies at once) reads *flow changed*."""
    from agentorc import cli

    _switching(world, tmp_path, monkeypatch, ["td", "build-review"])
    assert cli.main(["--json", "team", "list"]) == 0
    assert json.loads(capsys.readouterr().out)["teams"][0]["differences"] == []
    cli.call_sync("set_settings", teams={"ao-grind": {"flow": "build-review"}})  # written, not applied
    assert cli.main(["--json", "team", "list"]) == 0
    row = next(r for r in json.loads(capsys.readouterr().out)["teams"] if r["name"] == "ao-grind")
    assert {(d["name"], d["act"]) for d in row["differences"]} == {
        ("designer-ao", "sit_out"),
        ("grind-1", "relaunch"),
        ("grind-2", "relaunch"),
    }
    assert cli.main(["team", "list"]) == 0
    out = capsys.readouterr().out
    assert "flow changed — Apply (ao team flow ao-grind --apply):" in out
    assert "designer-ao: sits out under build-review" in out


# ── role directories (TD-313 slice 2) ───────────────────────────────────────────────────────────────


def define_role(base: Path, name: str, yml: str = "kind: worker\n", template: str | None = "# {lane}\n") -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "role.yml").write_text(yml)
    if template is not None:
        (d / "template.md").write_text(template)
    return d


@pytest.mark.unit
def test_a_role_directory_defines_a_role_the_orgs_over_a_repos_whole(tmp_path, monkeypatch):
    # §4.9c *Where flows and roles live*: `role.yml` (the role keys and `kind`) and `template.md`, the
    # org's `~/.agentorc/roles/<name>/` over a repo's `.agentorc/roles/<name>/`, never merged by key
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    root = tmp_path / "r"
    repo_role = define_role(root / ".agentorc" / "roles", "security", "kind: seat\nicon: eye\nlane: [a]\n")
    cfg = repoconfig.load(root)
    sec = repoconfig.resolve_role(cfg, "security")
    assert (sec.kind, sec.icon, sec.lane, sec.source, sec.defined) == ("seat", "eye", ["a"], "repo role", repo_role)
    assert "security" in repoconfig.role_names(cfg)
    org_role = define_role(
        repoconfig.org_roles_dir(),
        "security",
        "icon: shield\nlabel: security\nprofile: grind\n",
        "sec: {lane}\n{repo}\n",
    )
    sec = repoconfig.resolve_role(cfg, "security")
    assert (sec.kind, sec.icon, sec.lane, sec.display, sec.profile) == ("worker", "shield", [], "security", "grind")
    assert sec.source == "org role" and sec.defined == org_role  # whole: the repo's `kind` and `lane` gone
    # its template is the brief, the repo's slots filled as a preset's; an overlay still lays over it per key
    text, made = sec.compose(["TD-1"])
    assert text == "sec: TD-1\nnone\n" and made["base"] == str(org_role / "template.md")
    (root / "s.md").write_text("the repo's security words\n")
    (root / ".agentorc.yml").write_text("roles:\n  security: {brief: s.md, lane: [b]}\n")
    sec = repoconfig.resolve_role(repoconfig.load(root), "security", {"security": {"profile": "fable"}})
    assert sec.source == "org role + org + repo" and sec.profile == "fable"
    assert sec.brief_text() == "sec: b\nthe repo's security words\n"
    rows = {(r["name"], r["source"]): r for r in repoconfig.visible([root])}
    assert rows[("security", str(org_role))]["usable"]
    assert "the org's role security" in rows[("security", str(repo_role))]["shadowed"]


@pytest.mark.unit
def test_a_role_directory_that_is_not_whole_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    base = repoconfig.org_roles_dir()
    cfg = cfg_with(tmp_path)
    for yml, template, why in (
        ("brief: x.md\n", "t\n", "`brief:` is not a defined role's"),
        ("kind: boss\n", "t\n", "kind 'boss' is not a kind"),
        ("icon: rocket\n", "t\n", "icon: unknown icon 'rocket'"),
        ("- a\n", "t\n", "the top level must be a mapping"),
        ("kind: worker\n", None, "no template.md"),
    ):
        d = define_role(base, "odd", yml, template)
        with pytest.raises(ValueError, match=why):
            repoconfig.resolve_role(cfg, "odd")
        assert not {r["name"]: r for r in repoconfig.visible()}["odd"]["usable"]
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
    # a directory taking a preset's name is never read, and is listed as refused
    define_role(base, "grinder", "kind: seat\n")
    assert repoconfig.resolve_role(cfg, "grinder").kind == "worker"
    assert "a preset's name" in repoconfig.visible()[0]["problems"][0]
    # a manager on call takes `template_on_call.md`; without one it is a standing manager only
    d = define_role(base, "boss", "kind: manager\n", "standing\n")
    boss = repoconfig.resolve_role(cfg, "boss")
    assert boss.brief_text() == "standing\n"
    with pytest.raises(ValueError, match="standing manager only"):
        boss.compose(on_call=True)
    (d / "template_on_call.md").write_text("on call\n")
    assert boss.compose(on_call=True)[0] == "on call\n"


@pytest.mark.unit
def test_an_org_flow_uses_an_org_role_and_a_node_team_cannot(tmp_path, monkeypatch):
    # TD-313's *Done when*, its first half: a role directory under `~/.agentorc/roles/` is used by an
    # org flow with no other file changed — and, on a node, the role is the home's (*security is an org role*)
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    d = flowdefs.org_dir() / "sweep"
    d.mkdir(parents=True)
    (d / "flow.yml").write_text("stages:\n  - {name: look, role: security, lane: [free], brief: look.md}\n")
    (d / "look.md").write_text("look\n")
    assert "unknown role 'security'" in flowdefs.load("sweep", cfg_with(tmp_path)).problems[0]
    define_role(repoconfig.org_roles_dir(), "security", "kind: worker\nlane: [free]\n", "sec {lane}\n{stage}\n")
    sweep = flowdefs.load("sweep", cfg_with(tmp_path))
    assert sweep.usable, sweep.problems
    text, _ = repoconfig.resolve_role(cfg_with(tmp_path), "security").compose(flow=flowdefs.under(sweep, "security"))
    assert text == "sec free\nlook\n"
    assert flowdefs.unfollowable(sweep, {"security"}, techlead=False, held=()) == []
    why = flowdefs.unfollowable(sweep, {"security"}, techlead=False, held=(), team="cm-grind", node="contractmatch")
    assert "security is an org role" in why
    # a seat where a stage gives a lane is not usable, as for a preset
    define_role(repoconfig.org_roles_dir(), "security", "kind: seat\n", "sec\n")
    assert "names a worker, and 'security' is a seat" in " ".join(flowdefs.load("sweep", cfg_with(tmp_path)).problems)


def test_a_hunt_flow_in_the_orgs_directory_starts_its_members_from_it(world, tmp_path):  # noqa: F811
    # TD-313's *Done when*, its second half: a `hunt` flow in the org's directory starts a hunter and
    # a grinder with their lanes and briefs from it, and an org role directory's member beside them
    d = flowdefs.org_dir() / "hunt"
    d.mkdir(parents=True)
    stages = [
        {"name": "find", "role": "hunter", "lane": ["free"], "brief": "find.md"},
        {"name": "build", "role": "grinder", "lane": ["free-pick"], "brief": "build.md"},
        {"name": "look", "role": "security", "lane": ["free"], "brief": "look.md"},
    ]
    (d / "flow.yml").write_text(yaml.safe_dump({"stages": stages}))
    for f in ("find", "build", "look"):
        (d / f"{f}.md").write_text(f"the org's {f} words\n")
    define_role(repoconfig.org_roles_dir(), "security", "kind: worker\nlane: [free]\n", "security: {lane}\n{stage}\n")
    doc = _team_doc(tmp_path)
    t = doc["teams"]["ao-grind"]
    t["flows"] = ["hunt"]
    for m in t["members"]:
        m.pop("lane", None)
    t["members"].append({"role": "security", "name": "security-ao", "home": "agentorc"})
    p = teams.plan(_write(tmp_path, doc), "ao-grind", HOST)
    assert p.flow == "hunt"
    by = {x.role: x for x in p.members}
    assert by["grinder"].lane == ["free-pick"] and "the org's build words" in by["grinder"].prompt
    assert by["hunter"].lane == ["free"] and "the org's find words" in by["hunter"].prompt
    assert by["security"].prompt == "security: free\nthe org's look words\n"


def test_ao_org_lists_every_role_directory_and_check_fails_on_one_not_whole(world, tmp_path, capsys):  # noqa: F811
    """§4.9c: `ao org` says a repo's role directory the org's shadows, as a shadowed team; `ao org
    check` fails on one that is not whole or takes a preset's name."""
    from agentorc import cli

    root = tmp_path / "agentorc"
    define_role(root / ".agentorc" / "roles", "security")
    define_role(repoconfig.org_roles_dir(), "security")
    assert cli.main(["org"]) == 0
    out = capsys.readouterr().out
    assert f"role security  usable  [{repoconfig.org_roles_dir() / 'security'}]" in out
    assert "role security  not read: the org's role security" in out
    assert cli.main(["org", "check"]) == 0
    capsys.readouterr()
    define_role(root / ".agentorc" / "roles", "hunter")
    assert cli.main(["org", "check"]) == 1
    assert "lacking: role hunter (" in capsys.readouterr().out
    shutil.rmtree(root / ".agentorc" / "roles" / "hunter")
    # a directory with no role.yml is not whole, and a broken one leaves every other role listed
    (root / ".agentorc" / "roles" / "stray").mkdir()
    (repoconfig.org_roles_dir() / "security" / "template.md").unlink()
    assert cli.main(["org", "check"]) == 1
    out = capsys.readouterr().out
    assert "lacking: role stray (" in out and "no role.yml" in out and "lacking: role security (" in out
    names = [r.name for r in repoconfig.roles(repoconfig.load(root))]
    assert "security" not in names and "grinder" in names
    assert cli.main(["roles"]) == 0
    capsys.readouterr()
    # org.yml's `roles:` key naming no definition is named, since the org file cannot see the repos
    (repoconfig.org_roles_dir() / "security" / "template.md").write_text("x\n")
    shutil.rmtree(root / ".agentorc" / "roles" / "stray")
    doc = yaml.safe_load((tmp_path / "home" / "org.yml").read_text())
    doc.setdefault("roles", {})["grnder"] = {"lane": ["free-pick"]}
    (tmp_path / "home" / "org.yml").write_text(yaml.safe_dump(doc))
    assert cli.main(["org", "check"]) == 1
    assert "roles.grnder — unknown role" in capsys.readouterr().out
