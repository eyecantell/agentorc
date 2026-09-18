"""`agentorc.org` — the `org.yml` reader (design §4.9, TD-040 step b): the defaults, the
validation, a repo's own `teams:` folded in, and the per-host checkout lookup."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentorc import org

ORG = {
    "projects": {
        "agentorc": {"repos": {"agentorc": {"kmaster": "~/agentorc"}}},
        "guardians": {
            "repos": {
                "guardians": {"devenv": "/workspaces/guardians"},
                "guardians-api": {"devenv": "/workspaces/guardians/api"},
            }
        },
    },
    "teams": {
        "ao-grind": {
            "projects": ["agentorc"],
            "lead": {"role": "lead", "name": "orchestrator-ao-1"},
            "members": [
                {"role": "grinder", "count": 2, "name": "tdgrind-ao", "lane": "free-pick"},
                {"role": "hunter", "name": "hunter-ao", "lane": "ui"},
            ],
        },
        "guardians": {
            "projects": ["guardians"],
            "lead": {"home": "guardians", "profile": "orc"},
            "members": [
                {"role": "grinder", "home": "guardians-api", "brief": "docs/briefs/api-grinder.md"},
                {"team": "guardians-ui"},
            ],
        },
        "guardians-ui": {
            "projects": ["guardians"],
            "lead": {"role": "person"},
            "members": [{"role": "grinder", "home": "guardians", "unattended": False, "grants": ["control"]}],
        },
    },
    "roles": {"grinder": {"profile": "grind"}},
}


def write(tmp_path: Path, doc: object) -> Path:
    p = tmp_path / "org.yml"
    p.write_text(yaml.safe_dump(doc) if not isinstance(doc, str) else doc)
    return p


def test_missing_file_is_an_empty_org(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    o = org.load()
    assert o.projects == {} and o.teams == {} and o.roles == {}
    assert o.path == tmp_path / "org.yml"
    assert o.checkout("agentorc", "agentorc", "kmaster") is None


def test_projects_and_checkout_expand_home(tmp_path):
    o = org.load(write(tmp_path, ORG))
    assert o.checkout("agentorc", "agentorc", "kmaster") == Path("~/agentorc").expanduser()
    assert o.checkout("guardians", "guardians-api", "devenv") == Path("/workspaces/guardians/api")
    assert o.checkout("guardians", "guardians-api", "kmaster") is None  # not on this host: phase 2
    assert o.checkout("guardians", "nope", "devenv") is None and o.checkout("nope", "x", "devenv") is None
    assert o.roles == {"grinder": {"profile": "grind"}}
    assert o.path == tmp_path / "org.yml"


def test_every_default_of_section_4_9(tmp_path):
    o = org.load(write(tmp_path, ORG))
    grind = o.teams["ao-grind"]
    assert grind.source == tmp_path / "org.yml" and grind.projects == ["agentorc"]
    assert grind.lead == org.LeadDef(role="lead", name="orchestrator-ao-1", home="agentorc", profile=None)
    two, one = grind.members
    # count 1, name = the role, unattended true, grants/brief/profile = the role's, home = the only repo
    assert one == org.MemberDef(role="hunter", name="hunter-ao", home="agentorc", lane=["ui"])
    assert one.count == 1 and one.unattended is True and one.grants is None and one.brief is None
    assert two.count == 2 and two.lane == ["free-pick"] and two.names() == ["tdgrind-ao-1", "tdgrind-ao-2"]
    assert one.names() == ["hunter-ao"]
    # lead defaults: role lead, name <team>-lead
    g = o.teams["guardians"]
    assert g.lead.role == "lead" and g.lead.name == "guardians-lead" and g.lead.profile == "orc"
    assert g.members[0].home == "guardians-api" and g.members[0].brief == "docs/briefs/api-grinder.md"
    assert g.members[1] == org.MemberDef(team="guardians-ui")
    ui = o.teams["guardians-ui"]
    assert ui.lead.role == "person" and ui.lead.home == ""  # no lead session, so no home to require
    assert ui.members[0].unattended is False and ui.members[0].grants == ["control"]
    assert o.team_repos(g) == ["guardians", "guardians-api"]


def test_member_name_defaults_to_the_role_and_lane_forms(tmp_path):
    doc = {
        "projects": {"p": {"repos": {"r": {"h": "/r"}}}},
        "teams": {
            "t": {
                "projects": "p",
                "members": [{"role": "grinder", "lane": "TD-027, TD-019"}, {"role": "x", "lane": ["a"]}],
            }
        },
    }
    t = org.load(write(tmp_path, doc)).teams["t"]
    assert t.projects == ["p"] and t.lead.name == "t-lead" and t.lead.home == "r"
    assert [m.name for m in t.members] == ["grinder", "x"]
    assert t.members[0].lane == ["TD-027", "TD-019"] and t.members[1].lane == ["a"]


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ("- a list", "the top level must be a mapping"),
        ({"projects": [1]}, "projects must be a mapping"),
        ({"projects": {"p": {"repos": "x"}}}, "projects.p.repos must be a mapping"),
        ({"projects": {"p": {"repos": {"r": "x"}}}}, "projects.p.repos.r must be a mapping"),
        ({"teams": {"t": [1]}}, "teams.t must be a mapping"),
        ({"teams": {"t": {"projects": ["p"]}}}, "teams.t.projects: 'p' is not a defined project"),
        ({"projects": {"p": {"repos": {"r": {"h": "/r"}}}}, "teams": {"t": {}}}, "teams.t.projects: a team is on one"),
        ({"projects": {"p": {}}, "teams": {"t": {"projects": ["p"]}}}, "teams.t: its projects ['p'] list no repos"),
        (
            {"projects": {"p": {"repos": {"r": {"h": "/r"}}}}, "teams": {"t": {"projects": ["p"], "members": {}}}},
            "teams.t.members must be a list",
        ),
        (
            {"projects": {"p": {"repos": {"r": {"h": "/r"}}}}, "teams": {"t": {"projects": ["p"], "members": [{}]}}},
            "teams.t.members[0].role is required",
        ),
        (
            {
                "projects": {"p": {"repos": {"r": {"h": "/r"}}}},
                "teams": {"t": {"projects": ["p"], "members": [{"role": "g", "count": 0}]}},
            },
            "teams.t.members[0].count must be a positive integer",
        ),
        (
            {
                "projects": {"p": {"repos": {"r": {"h": "/r"}}}},
                "teams": {"t": {"projects": ["p"], "members": [{"role": "g", "unattended": "no"}]}},
            },
            "teams.t.members[0].unattended must be true or false",
        ),
        (
            {
                "projects": {"p": {"repos": {"r": {"h": "/r"}}}},
                "teams": {"t": {"projects": ["p"], "members": [{"team": "x", "role": "g"}]}},
            },
            "teams.t.members[0]: a nested team member is `{team: <name>}` alone",
        ),
        (
            {
                "projects": {"p": {"repos": {"r": {"h": "/r"}}}},
                "teams": {"t": {"projects": ["p"], "members": [{"team": "x"}]}},
            },
            "teams.t.members[0].team: 'x' is not a defined team",
        ),
        (
            {
                "projects": {"p": {"repos": {"r": {"h": "/r"}}}},
                "teams": {"t": {"projects": ["p"], "members": [{"team": "t"}]}},
            },
            "teams.t nests itself",
        ),
        (
            {
                "projects": {"p": {"repos": {"r": {"h": "/r"}}}},
                "teams": {"t": {"projects": ["p"], "lead": {"home": "z"}}},
            },
            "teams.t.lead.home: 'z' is not a repo of the team's projects",
        ),
        (
            {
                "projects": {"p": {"repos": {"r": {"h": "/r"}}}},
                "teams": {"t": {"projects": ["p"], "members": [{"role": "g", "home": "z"}]}},
            },
            "teams.t.members[0].home: 'z' is not a repo",
        ),
        ({"roles": {"g": [1]}}, "roles.g must be a mapping"),
    ],
)
def test_malformed_files_name_the_key(tmp_path, doc, message):
    with pytest.raises(ValueError, match="org.yml: " + __import__("re").escape(message)):
        org.load(write(tmp_path, doc))


def test_home_is_required_above_one_repo(tmp_path):
    doc = dict(ORG, teams={"g": {"projects": ["guardians"], "members": [{"role": "grinder"}]}})
    with pytest.raises(ValueError, match=r"teams\.g\.lead\.home is required when .*\['guardians', 'guardians-api'\]"):
        org.load(write(tmp_path, doc))
    doc["teams"]["g"]["lead"] = {"home": "guardians"}
    with pytest.raises(ValueError, match=r"teams\.g\.members\[0\]\.home is required"):
        org.load(write(tmp_path, doc))
    doc["teams"]["g"]["members"][0]["home"] = "guardians-api"
    assert org.load(write(tmp_path, doc)).teams["g"].members[0].home == "guardians-api"


def test_an_indirect_cycle_is_refused(tmp_path):
    doc = {
        "projects": {"p": {"repos": {"r": {"h": "/r"}}}},
        "teams": {
            "a": {"projects": ["p"], "members": [{"team": "b"}]},
            "b": {"projects": ["p"], "members": [{"team": "a"}]},
        },
    }
    with pytest.raises(ValueError, match="nests itself"):
        org.load(write(tmp_path, doc))


def test_merge_repo_teams(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))  # no hosts.yml: the short hostname
    repo = tmp_path / "myrepo"
    repo.mkdir()
    base = org.load(write(tmp_path, ORG))
    repo_teams = {
        "grind": {"lead": {"name": "orc"}, "members": [{"role": "grinder", "count": 2, "name": "tdgrind"}]},
        "ao-grind": {"members": [{"role": "impostor"}]},  # collides with the org file: the org wins
    }
    merged = org.merge_repo_teams(base, repo, repo_teams)
    assert "grind" not in base.teams and "myrepo" not in base.projects  # the input is untouched
    from sessionorc.hosts import local_host

    assert merged.projects["myrepo"].repos == {"myrepo": {local_host().name: repo.resolve()}}
    assert merged.checkout("myrepo", "myrepo", local_host().name) == repo.resolve()
    g = merged.teams["grind"]
    assert g.projects == ["myrepo"] and g.source == repo / ".agentorc.yml"
    assert g.lead == org.LeadDef(role="lead", name="orc", home="myrepo")
    assert g.members[0].names() == ["tdgrind-1", "tdgrind-2"] and g.members[0].home == "myrepo"
    assert merged.teams["ao-grind"] is base.teams["ao-grind"]  # org wins, source still org.yml
    assert merged.teams["ao-grind"].source == tmp_path / "org.yml"
    assert merged.roles == base.roles
    # nothing to merge leaves the org as it was, and the repo is not made a project for nothing
    assert org.merge_repo_teams(base, repo, None).projects.keys() == base.projects.keys()
    # a repo team can nest an org team, and its errors name the repo's file
    merged = org.merge_repo_teams(base, repo, {"outer": {"members": [{"team": "ao-grind"}]}})
    assert merged.teams["outer"].members[0].team == "ao-grind"
    with pytest.raises(
        ValueError, match=r"\.agentorc\.yml: teams\.bad\.members\[0\]\.team: 'nope' is not a defined team"
    ):
        org.merge_repo_teams(base, repo, {"bad": {"members": [{"team": "nope"}]}})
    # an org project of the repo's name is used as it stands
    doc = dict(ORG, projects={**ORG["projects"], "myrepo": {"repos": {"myrepo": {"elsewhere": "/x"}}}})
    merged = org.merge_repo_teams(org.load(write(tmp_path, doc)), repo, repo_teams)
    assert merged.projects["myrepo"].repos == {"myrepo": {"elsewhere": Path("/x")}}


def test_a_lead_may_name_its_own_brief_lane_grants_and_mode(tmp_path):
    """§4.9's lead had only role, name, home and profile, so a `brief:` on it was read by nobody —
    and a lead's brief is the one a repo most often keeps its own copy of. Found while
    writing the first real org.yml (2026-09-13)."""
    doc = {
        "projects": {"p": {"repos": {"r": {"kmaster": str(tmp_path)}}}},
        "teams": {
            "t": {
                "projects": ["p"],
                "lead": {"role": "lead", "brief": "docs/briefs/orc.md", "lane": "TD-1", "unattended": False},
            }
        },
    }
    (tmp_path / "org.yml").write_text(yaml.safe_dump(doc))
    lead = org.load(tmp_path / "org.yml").teams["t"].lead
    assert lead.brief == "docs/briefs/orc.md" and lead.lane == ["TD-1"] and lead.unattended is False
    assert lead.grants is None  # unsaid: the role's, as for a member


def test_a_key_nobody_reads_is_an_error_naming_it(tmp_path):
    """Silence about a stray key is how a lead's `brief:` disappeared into a file that looked
    right. A typo must stop the load, not be ignored (2026-09-13)."""
    base = {"projects": {"p": {"repos": {"r": {"kmaster": str(tmp_path)}}}}}
    for block, bad in (
        ({"projects": ["p"], "leed": {}}, "leed"),
        ({"projects": ["p"], "lead": {"role": "lead", "breif": "x"}}, "breif"),
        ({"projects": ["p"], "members": [{"role": "grinder", "profil": "grind"}]}, "profil"),
    ):
        (tmp_path / "org.yml").write_text(yaml.safe_dump({**base, "teams": {"t": block}}))
        with pytest.raises(ValueError, match=bad):
            org.load(tmp_path / "org.yml")


def test_grants_orchestrate_in_a_team_is_read_as_control(tmp_path, capsys, monkeypatch):
    """TD-055 step 3: `grants: [orchestrate]` on a team's lead or member still loads, as `control`,
    and says so once."""
    from agentorc import repoconfig

    monkeypatch.setattr(repoconfig, "_warned", set())
    f = tmp_path / "org.yml"
    f.write_text(
        "projects: {p: {repos: {r: {kmaster: /tmp}}}}\n"
        "teams:\n"
        "  t: {projects: [p], lead: {grants: [orchestrate]},\n"
        "      members: [{role: grinder, grants: [orchestrate, control]}]}\n"
    )
    g = org.load(f).teams["t"]
    assert g.lead.grants == ["control"] and g.members[0].grants == ["control"]
    assert capsys.readouterr().err.count("grant `orchestrate` is now `control`") == 1


def test_a_team_may_name_the_host_it_lands_on(tmp_path):
    """Design §4.4a "Teams across hosts", TD-057 step 4a: `host:` on a team definition; unsaid, the
    host the start runs on (`teams.plan` reads it); still a stray key anywhere else."""
    f = tmp_path / "org.yml"
    f.write_text(
        "projects: {p: {repos: {r: {contractmatch: /home/x/r}}}}\n"
        "teams:\n"
        "  t: {projects: [p], host: contractmatch, lead: {role: lead}, members: [{role: grinder}]}\n"
        "  u: {projects: [p], lead: {role: lead}}\n"
    )
    loaded = org.load(f)
    assert loaded.teams["t"].host == "contractmatch" and loaded.teams["u"].host == ""
    f.write_text("projects: {p: {repos: {r: {k: /x}}}}\nteams: {t: {projects: [p], members: [{role: g, host: k}]}}\n")
    with pytest.raises(ValueError, match="host"):
        org.load(f)
