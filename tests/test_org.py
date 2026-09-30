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
            "manager": {"role": "manager", "name": "manager-ao-1"},
            "members": [
                {"role": "grinder", "count": 2, "name": "tdgrind-ao", "lane": "free-pick"},
                {"role": "hunter", "name": "hunter-ao", "lane": "ui"},
            ],
        },
        "guardians": {
            "projects": ["guardians"],
            "manager": {"home": "guardians", "profile": "orc"},
            "members": [
                {"role": "grinder", "home": "guardians-api", "brief": "docs/briefs/api-grinder.md"},
                {"team": "guardians-ui"},
            ],
        },
        "guardians-ui": {
            "projects": ["guardians"],
            "manager": {"role": "person"},
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
    assert grind.manager == org.ManagerDef(role="manager", name="manager-ao-1", home="agentorc", profile=None)
    two, one = grind.members
    # count 1, name = the role, unattended true, grants/brief/profile = the role's, home = the only repo
    assert one == org.MemberDef(role="hunter", name="hunter-ao", home="agentorc", lane=["ui"])
    assert one.count == 1 and one.unattended is True and one.grants is None and one.brief is None
    assert two.count == 2 and two.lane == ["free-pick"] and two.names() == ["tdgrind-ao-1", "tdgrind-ao-2"]
    assert one.names() == ["hunter-ao"]
    # manager defaults: role manager, name <team>-lead
    g = o.teams["guardians"]
    assert g.manager.role == "manager" and g.manager.name == "guardians-lead" and g.manager.profile == "orc"
    assert g.members[0].home == "guardians-api" and g.members[0].brief == "docs/briefs/api-grinder.md"
    assert g.members[1] == org.MemberDef(team="guardians-ui")
    ui = o.teams["guardians-ui"]
    assert ui.manager.role == "person" and ui.manager.home == ""  # no manager session, so no home to require
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
    assert t.projects == ["p"] and t.manager.name == "t-lead" and t.manager.home == "r"
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
                "teams": {"t": {"projects": ["p"], "manager": {"home": "z"}}},
            },
            "teams.t.manager.home: 'z' is not a repo of the team's projects",
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
    with pytest.raises(
        ValueError, match=r"teams\.g\.manager\.home is required when .*\['guardians', 'guardians-api'\]"
    ):
        org.load(write(tmp_path, doc))
    doc["teams"]["g"]["manager"] = {"home": "guardians"}
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
        "grind": {"manager": {"name": "orc"}, "members": [{"role": "grinder", "count": 2, "name": "tdgrind"}]},
        "ao-grind": {"members": [{"role": "impostor"}]},  # collides with the org file: the org wins
    }
    merged = org.merge_repo_teams(base, repo, repo_teams)
    assert "grind" not in base.teams and "myrepo" not in base.projects  # the input is untouched
    from sessionorc.hosts import local_host

    assert merged.projects["myrepo"].repos == {"myrepo": {local_host().name: repo.resolve()}}
    assert merged.checkout("myrepo", "myrepo", local_host().name) == repo.resolve()
    g = merged.teams["grind"]
    assert g.projects == ["myrepo"] and g.source == repo / ".agentorc.yml"
    assert g.manager == org.ManagerDef(role="manager", name="orc", home="myrepo")
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


def test_a_manager_may_name_its_own_brief_lane_grants_and_mode(tmp_path):
    """§4.9's lead had only role, name, home and profile, so a `brief:` on it was read by nobody —
    and a lead's brief is the one a repo most often keeps its own copy of. Found while
    writing the first real org.yml (2026-09-13)."""
    doc = {
        "projects": {"p": {"repos": {"r": {"kmaster": str(tmp_path)}}}},
        "teams": {
            "t": {
                "projects": ["p"],
                "manager": {"role": "manager", "brief": "docs/briefs/orc.md", "lane": "TD-1", "unattended": False},
            }
        },
    }
    (tmp_path / "org.yml").write_text(yaml.safe_dump(doc))
    lead = org.load(tmp_path / "org.yml").teams["t"].manager
    assert lead.brief == "docs/briefs/orc.md" and lead.lane == ["TD-1"] and lead.unattended is False
    assert lead.grants is None  # unsaid: the role's, as for a member


def test_a_key_nobody_reads_is_an_error_naming_it(tmp_path):
    """Silence about a stray key is how a lead's `brief:` disappeared into a file that looked
    right. A typo must stop the load, not be ignored (2026-09-13)."""
    base = {"projects": {"p": {"repos": {"r": {"kmaster": str(tmp_path)}}}}}
    for block, bad in (
        ({"projects": ["p"], "leed": {}}, "leed"),
        ({"projects": ["p"], "manager": {"role": "manager", "breif": "x"}}, "breif"),
        ({"projects": ["p"], "members": [{"role": "grinder", "profil": "grind"}]}, "profil"),
    ):
        (tmp_path / "org.yml").write_text(yaml.safe_dump({**base, "teams": {"t": block}}))
        with pytest.raises(ValueError, match=bad):
            org.load(tmp_path / "org.yml")


def test_grants_orchestrate_in_a_team_is_an_unknown_grant(tmp_path):
    """TD-107: `grants: [orchestrate]` on a team's manager or member is refused when the file loads."""
    f = tmp_path / "org.yml"
    f.write_text(
        "projects: {p: {repos: {r: {kmaster: /tmp}}}}\n"
        "teams:\n"
        "  t: {projects: [p], members: [{role: grinder, grants: [orchestrate, control]}]}\n"
    )
    with pytest.raises(ValueError, match="unknown grant 'orchestrate'"):
        org.load(f)


def test_a_team_may_name_the_host_it_lands_on(tmp_path):
    """Design §4.4a "Teams across hosts", TD-057 step 4a: `host:` on a team definition; unsaid, the
    host the start runs on (`teams.plan` reads it); still a stray key anywhere else."""
    f = tmp_path / "org.yml"
    f.write_text(
        "projects: {p: {repos: {r: {contractmatch: /home/x/r}}}}\n"
        "teams:\n"
        "  t: {projects: [p], host: contractmatch, manager: {role: manager}, members: [{role: grinder}]}\n"
        "  u: {projects: [p], manager: {role: manager}}\n"
    )
    loaded = org.load(f)
    assert loaded.teams["t"].host == "contractmatch" and loaded.teams["u"].host == ""
    f.write_text("projects: {p: {repos: {r: {k: /x}}}}\nteams: {t: {projects: [p], members: [{role: g, host: k}]}}\n")
    with pytest.raises(ValueError, match="host"):
        org.load(f)


def test_the_old_lead_key_is_a_stray_key(tmp_path):
    """TD-107: `lead:` was read as `manager:` for a release that never came; it is now refused as
    any unknown key on a team is."""
    f = tmp_path / "org.yml"
    f.write_text("projects: {p: {repos: {r: {kmaster: /tmp}}}}\nteams:\n  t: {projects: [p], lead: {name: orc}}\n")
    with pytest.raises(ValueError, match=r"teams\.t: unknown key\(s\) \['lead'\]"):
        org.load(f)


def test_techlead_is_an_org_role_like_any_other(tmp_path):
    """TD-075 step 1: `techlead` is a preset now (design §4.9b), so `org.yml`'s `roles:` may carry
    it — the usual place to give the go-between a stronger profile."""
    f = tmp_path / "org.yml"
    f.write_text("roles:\n  techlead: {profile: x}\n")
    assert org.load(f).roles["techlead"] == {"profile": "x"}


def test_a_team_may_carry_one_techlead_seat(tmp_path):
    """TD-075 step 1, design §4.9b: `techlead: {name, home, profile, brief}` beside `manager:`; the
    name defaults to `<team>-techlead` and `home` follows the members' rule. The seat is the role
    and holds no grant, so `role:` and `grants:` are refused as unknown keys, and so is a bare
    string — `techlead: person` included: a person answering questions is the person."""
    f = tmp_path / "org.yml"
    base = "projects: {p: {repos: {r: {kmaster: /tmp/r}}}}\nteams:\n  t:\n    projects: [p]\n"
    f.write_text(base + "    techlead: {profile: strong}\n")
    seat = org.load(f).teams["t"].techlead
    assert (seat.name, seat.home, seat.profile, seat.brief, seat.grants) == ("t-techlead", "r", "strong", None, None)
    assert seat.context is None
    f.write_text(base + "    techlead: {context: docs/primer.md}\n")  # the primer (§4.9b *Its standing context*)
    assert org.load(f).teams["t"].techlead.context == "docs/primer.md"
    f.write_text(base)
    assert org.load(f).teams["t"].techlead is None
    for bad, why in (("{role: grinder}", r"unknown key\(s\) \['role'\]"), ("{grants: [control]}", "unknown key"),
                     ("person", "must be a mapping"), ("{home: nope}", "not a repo of the team")):  # fmt: skip
        f.write_text(base + f"    techlead: {bad}\n")
        with pytest.raises(ValueError, match=why):
            org.load(f)


def test_a_team_may_carry_seats_with_a_trigger(tmp_path):
    """TD-098 step 1, design §4.9b *Seats with a trigger*: `seats:` — each `{name, role, trigger,
    brief, profile, home}` — with `trigger` one of `asks`, `{prs: n}` or `{every: <duration>}`, and
    required. The name defaults to `<team>-<role>` and `home` follows the members' rule; a seat
    holds no grants and has no lane, so neither key is read, and it is never the person, the
    manager or the techlead, which have keys of their own."""
    f = tmp_path / "org.yml"
    base = "projects: {p: {repos: {r: {kmaster: /tmp/r}}}}\nteams:\n  t:\n    projects: [p]\n"
    f.write_text(
        base + "    seats:\n"
        "      - {name: docs-audit, role: auditor, brief: docs/briefs/docs-audit.md, trigger: {prs: 10}}\n"
        "      - {role: auditor, trigger: {every: 6h}, profile: strong}\n"
        "      - {name: asker, role: hunter, trigger: asks}\n"
    )
    seats = org.load(f).teams["t"].seats
    assert [(s.name, s.role, s.trigger, s.after, s.home) for s in seats] == [
        ("docs-audit", "auditor", "prs", "10", "r"),
        ("t-auditor", "auditor", "every", "6h", "r"),
        ("asker", "hunter", "asks", "", "r"),
    ]
    assert seats[0].brief == "docs/briefs/docs-audit.md" and seats[1].profile == "strong"
    assert all(s.grants == [] and s.lane == [] for s in seats)
    assert [s.when() for s in seats] == ["runs after 10 PRs", "runs every 6h", "comes on the next question"]
    f.write_text(base)
    assert org.load(f).teams["t"].seats == []
    for bad, why in (
        ("[{role: auditor}]", "trigger is required"),
        ("[{trigger: asks}]", "role is required"),
        ("[{role: auditor, trigger: {prs: 0}}]", "whole number"),
        ("[{role: auditor, trigger: {prs: true}}]", "whole number"),
        ("[{role: auditor, trigger: {every: 6}}]", "duration"),
        ("[{role: auditor, trigger: {every: 6w}}]", "duration"),
        ("[{role: auditor, trigger: {daily: 1}}]", "expected"),
        ("[{role: auditor, trigger: sometimes}]", "expected"),
        ("[{role: auditor, trigger: asks, grants: [control]}]", "unknown key"),
        ("[{role: auditor, trigger: asks, lane: [TD-1]}]", "unknown key"),
        ("[{role: techlead, trigger: asks}]", "not a seat's role"),
        ("[{role: person, trigger: asks}]", "not a seat's role"),
        ("[{role: auditor, trigger: asks, home: nope}]", "not a repo of the team"),
        ("{role: auditor}", "must be a list"),
    ):
        f.write_text(base + f"    seats: {bad}\n")
        with pytest.raises(ValueError, match=why):
            org.load(f)


def test_the_org_roles_overlay_is_checked_key_by_key_as_a_repos_is(tmp_path):
    """TD-149 (4): an org `roles:` preset used to be taken as it stood — a typo was a key nothing
    reads. It is checked by `repoconfig._role_block` now, so it fails when read, naming the key."""
    good = org.load(write(tmp_path, {"roles": {"grinder": {"profile": "grind", "review": {"reader": "techlead"}}}}))
    assert good.roles["grinder"]["profile"] == "grind" and good.roles["grinder"]["review"]["reader"] == "techlead"
    for bad, names in (
        ({"grinder": {"profil": "grind"}}, "roles.grinder.profil is not a role key"),
        ({"grinder": {"icon": "rocket"}}, "unknown icon 'rocket'"),
        ({"grinder": "grind"}, "roles.grinder must be a mapping"),
    ):
        with pytest.raises(ValueError, match=names):
            org.load(write(tmp_path, {"roles": bad}))


# -- Members… (design §4.9 *Add or remove a member from the team card*, TD-163, built by TD-172) ---

MEMBERS_YML = """\
# the org, by hand
projects:
  agentorc: {repos: {agentorc: {kmaster: ~/agentorc}}}

teams:
  # the grind team
  ao-grind:
    projects: [agentorc]   # one repo
    manager: {role: manager, name: manager-ao-1}
    members:
      # two grinders, numbered
      - {role: grinder, count: 2, name: grinder-ao, lane: [free-pick]}   # the lane
      - {role: hunter, name: hunter-ao, lane: [ui]}
    # after the members
  other:
    projects: [agentorc]
    manager: {role: manager}
    members:
      - role: grinder
        name: long-one
roles: {}
"""


def _members(tmp_path: Path) -> Path:
    p = tmp_path / "org.yml"
    p.write_text(MEMBERS_YML)
    return p


def test_add_bumps_a_counted_members_count_in_place_and_keeps_every_other_byte(tmp_path):
    p = _members(tmp_path)
    did = org.edit_members(p, "ao-grind", add={"role": "grinder", "name": "grinder-ao"})
    after = p.read_text()
    assert did == "grinder-ao count: 2 → 3"
    assert after == MEMBERS_YML.replace("count: 2, name: grinder-ao", "count: 3, name: grinder-ao")
    assert org.load(p).teams["ao-grind"].members[0].names()[-1] == "grinder-ao-3"


def test_add_of_another_role_appends_one_line_after_the_last_item(tmp_path):
    p = _members(tmp_path)
    org.edit_members(p, "ao-grind", add={"role": "auditor", "name": "audit-ao", "lane": ["docs"]})
    after = p.read_text()
    want = MEMBERS_YML.replace(
        "      - {role: hunter, name: hunter-ao, lane: [ui]}\n",
        "      - {role: hunter, name: hunter-ao, lane: [ui]}\n      - {role: auditor, name: audit-ao, lane: [docs]}\n",
    )
    assert after == want  # comments above, beside and after the block stand
    assert [m.role for m in org.load(p).teams["ao-grind"].members] == ["grinder", "hunter", "auditor"]


def test_remove_decrements_a_count_or_deletes_the_one_line(tmp_path):
    p = _members(tmp_path)
    assert org.edit_members(p, "ao-grind", remove=0, role="grinder") == "grinder-ao count: 2 → 1"
    assert "count: 1, name: grinder-ao" in p.read_text()
    assert org.edit_members(p, "ao-grind", remove=1, role="hunter") == "removed hunter-ao"
    after = p.read_text()
    assert "hunter-ao" not in after and "# the lane" in after and "# after the members" in after
    assert len(after.splitlines()) == len(MEMBERS_YML.splitlines()) - 1


def test_an_edit_that_cannot_be_one_line_is_refused_and_the_file_untouched(tmp_path):
    p = _members(tmp_path)
    for kw, why in (
        ({"remove": 0, "role": "grinder"}, "spans several lines"),
        ({"remove": 5}, "no member entry 6"),
        ({"remove": 0, "role": "hunter"}, "not a hunter any more"),
    ):
        team = "other" if why == "spans several lines" else "ao-grind"
        with pytest.raises(ValueError, match=why):
            org.edit_members(p, team, **kw)
    with pytest.raises(ValueError, match="does not define a team nope"):
        org.edit_members(p, "nope", add={"role": "grinder"})
    assert p.read_text() == MEMBERS_YML


def test_an_edit_that_would_not_parse_restores_the_bytes(tmp_path, monkeypatch):
    p = _members(tmp_path)

    def broken(path=None):
        raise ValueError("org.yml: teams.ao-grind.members[2]: nonsense")

    monkeypatch.setattr(org, "load", broken)
    with pytest.raises(ValueError, match="would not parse, so org.yml is as it was"):
        org.edit_members(p, "ao-grind", add={"role": "auditor", "name": "x"})
    assert p.read_text() == MEMBERS_YML


def test_a_count_is_edited_as_the_field_never_as_text_that_looks_like_it(tmp_path):
    """Review of PR #608: a name holding the characters `count: 9` before the real `count:` must not
    be the one edited — the entry's own field is, and nothing else in the line changes."""
    p = tmp_path / "org.yml"
    p.write_text(
        MEMBERS_YML.replace(
            "{role: grinder, count: 2, name: grinder-ao,", '{role: grinder, name: "prod count: 9 today", count: 2,'
        )
    )
    before = p.read_text()
    assert org.edit_members(p, "ao-grind", add={"role": "grinder"}) == "prod count: 9 today count: 2 → 3"
    assert p.read_text() == before.replace('"prod count: 9 today", count: 2,', '"prod count: 9 today", count: 3,')
    org.edit_members(p, "ao-grind", remove=0, role="grinder")
    assert p.read_text() == before


def test_entries_names_the_role_a_persons_entry_session_takes_per_type(tmp_path, monkeypatch):
    """TD-219 slice 2, design §4.9 *Add an entry to the ledger*: `entries: {feature: <role>, debt:
    <role>}` on a team, either key optional and read as `techlead` where unsaid; an unknown key, or a
    role nothing resolves, is an error naming it when the definition is read. A role resolves from the
    presets, the org's `roles:`, the team's own members and seats, or the repo's roles for its own teams."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    f = tmp_path / "org.yml"
    base = (
        "projects: {p: {repos: {r: {kmaster: /tmp/r}}}}\nroles: {designer: {profile: x}}\n"
        "teams:\n  t:\n    projects: [p]\n"
    )
    f.write_text(base)
    t = org.load(f).teams["t"]
    assert t.entries == {} and (t.entry_role("debt"), t.entry_role("feature")) == ("techlead", "techlead")
    f.write_text(base + "    entries: {feature: designer}\n")
    t = org.load(f).teams["t"]
    assert (t.entry_role("feature"), t.entry_role("debt")) == ("designer", "techlead")
    f.write_text(base + "    members: [{role: sage}]\n    entries: {debt: sage}\n")  # the team's own role
    assert org.load(f).teams["t"].entry_role("debt") == "sage"
    for bad, why in (
        ("{bug: grinder}", r"unknown key\(s\) \['bug'\]"),
        ("{feature: wizard}", "unknown role 'wizard'"),
        ("{debt: person}", "not a role"),
        ("{debt: ''}", "a role name"),
        ("[designer]", "must be a mapping"),
    ):
        f.write_text(base + f"    entries: {bad}\n")
        with pytest.raises(ValueError, match=why):
            org.load(f)
    # a repo's own team may name a role its own file defines
    repo = tmp_path / "myrepo"
    repo.mkdir()
    f.write_text(base)
    teams = {"g": {"entries": {"feature": "scribe"}}}
    with pytest.raises(ValueError, match="unknown role 'scribe'"):
        org.merge_repo_teams(org.load(f), repo, teams)
    assert org.merge_repo_teams(org.load(f), repo, teams, ["scribe"]).teams["g"].entry_role("feature") == "scribe"
    # …and a second repo's merge does not check the first's team against its own roles (review of #761)
    other = tmp_path / "other"
    other.mkdir()
    merged = org.merge_repo_teams(org.merge_repo_teams(org.load(f), repo, teams, ["scribe"]), other, {"h": {}})
    assert merged.teams["g"].entry_role("feature") == "scribe" and "h" in merged.teams
    # `ao team list --json` (the rows) carries both types, said
    from agentorc import teamrun

    f.write_text(base + "    entries: {feature: designer}\n")
    (row,) = teamrun.rows(org.load(f), [])
    assert row["entries"] == {"debt": "techlead", "feature": "designer"}
