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
    assert (
        repoconfig.resolve_role(cfg_with(tmp_path, "roles:\n  designer: {brief: d.md}\n"), "designer").kind == "worker"
    )


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
    # `build` and `build-review` are usable anywhere; `td` wherever a `designer` role resolves
    plain = cfg_with(tmp_path)
    assert flowdefs.load("build", plain).usable and flowdefs.load("build-review", plain).usable
    assert flowdefs.load("td", cfg_with(tmp_path, "roles:\n  designer: {brief: d.md}\n")).usable
    assert "unknown role 'designer'" in flowdefs.load("td", plain).problems[0]


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
    # td: no designer resolves in this repo — not usable; defined, but no member of it — not followable
    with pytest.raises(teams.TeamError, match=r"td is not usable — .*unknown role 'designer'"):
        teams.plan(_with(tmp_path, flows=["td"], techlead=techlead), "ao-grind", HOST)
    (root / ".agentorc.yml").write_text("held: [src/sessionorc/**]\nroles:\n  designer: {brief: docs/d.md}\n")
    with pytest.raises(teams.TeamError, match=r"td cannot be followed by ao-grind: no designer — add one to members:"):
        teams.plan(orgmod.load(), "ao-grind", HOST)
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
