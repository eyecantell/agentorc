"""Org definitions: projects, teams and an org-wide `roles:` overlay, declared once per UI host in
`~/.agentorc/org.yml` (design §4.9, §5).

```yaml
projects:
  agentorc:  {repos: {agentorc: {kmaster: ~/agentorc}}}
  guardians: {repos: {guardians: {devenv: /workspaces/guardians}, guardians-api: {devenv: /workspaces/guardians/api}}}
teams:
  ao-grind:
    projects: [agentorc]
    lead: {role: lead, name: orchestrator-ao-1}
    members:
      - {role: grinder, count: 2, name: tdgrind-ao, lane: free-pick}
      - {team: ao-ui}                 # a nested team
roles:
  grinder: {profile: grind}
```

A repo's `.agentorc.yml` may carry `teams:` of its own (teams whose only project is that repo);
`merge_repo_teams` folds them in, the org file winning a name collision. The file is read by the
clients (`ao team`, `ao new`, the UI) on every use and cached nowhere; the host agent never reads
it — it stores `team` and `project` as two plain strings on the record and nothing keys on them
(§9 invariant 9). With no file the org is empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from sessionorc import hosts, paths
from sessionorc.models import GRANT_ALIASES

DEFAULT_LEAD_ROLE = "lead"
PERSON = "person"  # a lead role meaning the person leads: no lead session is started (§4.9)


@dataclass
class Project:
    name: str
    repos: dict[str, dict[str, Path]] = field(default_factory=dict)  # repo name → host name → checkout


@dataclass
class LeadDef:
    role: str = DEFAULT_LEAD_ROLE
    name: str = ""  # default `<team>-lead`, filled by the loader
    home: str = ""  # a repo name from the team's projects
    profile: str | None = None  # overrides the role's
    lane: list[str] = field(default_factory=list)
    brief: str | None = None  # overrides the role's template — the lead brief a repo keeps
    grants: list[str] | None = None  # None: the role's (`control` for `lead`)
    unattended: bool = True


@dataclass
class MemberDef:
    role: str = ""
    count: int = 1
    name: str = ""  # the prefix; default the role
    home: str = ""
    lane: list[str] = field(default_factory=list)
    brief: str | None = None  # overrides the role's template
    profile: str | None = None
    grants: list[str] | None = None  # None: the role's
    unattended: bool = True
    team: str | None = None  # set: this member is a nested team, and the other fields are unused

    def names(self) -> list[str]:
        """The session names this member starts: the prefix, suffixed `-1`, `-2`, … above one."""
        if self.count == 1:
            return [self.name]
        return [f"{self.name}-{i}" for i in range(1, self.count + 1)]


@dataclass
class TeamDef:
    name: str
    projects: list[str] = field(default_factory=list)
    lead: LeadDef = field(default_factory=LeadDef)
    members: list[MemberDef] = field(default_factory=list)
    source: Path | None = None  # the file it was read from (`ao team list` names it)


@dataclass
class Org:
    projects: dict[str, Project] = field(default_factory=dict)
    teams: dict[str, TeamDef] = field(default_factory=dict)
    roles: dict[str, dict[str, Any]] = field(default_factory=dict)  # the org-wide overlay (§4.9 precedence)
    path: Path | None = None

    def checkout(self, project: str, repo: str, host: str) -> Path | None:
        """Where `repo` of `project` is checked out on `host`; None when any of the three is unknown."""
        p = self.projects.get(project)
        if p is None:
            return None
        return (p.repos.get(repo) or {}).get(host)

    def team_repos(self, team: TeamDef) -> list[str]:
        """Every repo name across the team's projects, in definition order, deduped."""
        out: list[str] = []
        for pname in team.projects:
            for rname in (self.projects.get(pname) or Project(pname)).repos:
                if rname not in out:
                    out.append(rname)
        return out


def org_file() -> Path:
    return paths.home() / "org.yml"


def load(path: Path | None = None) -> Org:
    """Read `org.yml`; a missing file is an empty Org, a malformed one a ValueError naming the key."""
    path = path or org_file()
    if not path.is_file():
        return Org(path=path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    label = path.name
    if not isinstance(data, dict):
        raise ValueError(f"{label}: the top level must be a mapping with `projects:`, `teams:`, `roles:`")
    org = Org(path=path)
    for pname, raw in _mapping(data.get("projects"), f"{label}: projects").items():
        org.projects[str(pname)] = _project(str(pname), raw, f"{label}: projects.{pname}")
    for tname, raw in _mapping(data.get("teams"), f"{label}: teams").items():
        org.teams[str(tname)] = _team(str(tname), raw, f"{label}: teams.{tname}", source=path)
    for rname, raw in _mapping(data.get("roles"), f"{label}: roles").items():
        org.roles[str(rname)] = dict(_mapping(raw, f"{label}: roles.{rname}"))
    _validate(org, label)
    return org


def merge_repo_teams(org: Org, repo_root: Path, repo_teams: dict[str, Any] | None) -> Org:
    """Fold a repo's `.agentorc.yml` `teams:` into the org (design §4.9): each is a team whose only
    project is that repo, so a repo can ship its own grind team beside its code. The org file wins
    a name collision; each definition keeps its `source`. Returns a new Org; `org` is untouched.

    The repo's project is the checkout's directory name, on this host at `repo_root`; an org
    project of that name is used as it stands."""
    repo_root = Path(repo_root).expanduser().resolve()
    rname = repo_root.name
    merged = Org(projects=dict(org.projects), teams=dict(org.teams), roles=dict(org.roles), path=org.path)
    source = repo_root / ".agentorc.yml"
    label = f"{source}"
    teams = _mapping(repo_teams, f"{label}: teams")
    if teams and rname not in merged.projects:
        merged.projects[rname] = Project(rname, {rname: {hosts.local_host().name: repo_root}})
    for tname, raw in teams.items():
        tname = str(tname)
        if tname in org.teams:
            continue  # the org file wins
        raw = dict(_mapping(raw, f"{label}: teams.{tname}"))
        raw.setdefault("projects", [rname])
        merged.teams[tname] = _team(tname, raw, f"{label}: teams.{tname}", source=source)
    _validate(merged, label)
    return merged


# ── parsing ───────────────────────────────────────────────────────────────────────────────────


def _mapping(raw: Any, key: str) -> dict[Any, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{key} must be a mapping, not {type(raw).__name__}")
    return raw


def _str(raw: Any, key: str, default: str = "") -> str:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, str | int | float):
        raise ValueError(f"{key} must be a string, not {type(raw).__name__}")
    return str(raw)


def _opt_str(raw: Any, key: str) -> str | None:
    return None if raw is None else _str(raw, key)


def _project(name: str, raw: Any, key: str) -> Project:
    raw = _mapping(raw, key)
    repos: dict[str, dict[str, Path]] = {}
    for rname, by_host in _mapping(raw.get("repos"), f"{key}.repos").items():
        hosts_map = _mapping(by_host, f"{key}.repos.{rname}")
        repos[str(rname)] = {
            str(h): Path(_str(p, f"{key}.repos.{rname}.{h}")).expanduser() for h, p in hosts_map.items()
        }
    return Project(name, repos)


def _lane(raw: Any, key: str) -> list[str]:
    """`lane: free-pick`, `lane: TD-027,TD-019` or `lane: [TD-027, TD-019]` → the list, as `ao new
    --lane` takes it; the agent canonicalises the references at create."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [_str(r, key) for r in raw]
    return [r.strip() for r in _str(raw, key).split(",") if r.strip()]


def _count(raw: Any, key: str) -> int:
    if raw is None:
        return 1
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError(f"{key} must be a positive integer, not {raw!r}")
    return raw


def _flag(raw: Any, key: str, default: bool) -> bool:
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise ValueError(f"{key} must be true or false, not {raw!r}")
    return raw


def _grants(raw: Any, key: str) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError(f"{key} must be a list of grants")
    grants = [_str(g, key) for g in raw]
    # TD-055: a renamed grant is read under its new name for one release, saying so where it is met
    from agentorc.repoconfig import _deprecated_grant

    for old in dict.fromkeys(g for g in grants if g in GRANT_ALIASES):
        _deprecated_grant(old, key)
    return list(dict.fromkeys(GRANT_ALIASES.get(g, g) for g in grants))


LEAD_KEYS = ("role", "name", "home", "profile", "lane", "brief", "grants", "unattended")
MEMBER_KEYS = (*LEAD_KEYS, "count", "team")
TEAM_KEYS = ("projects", "lead", "members")


def _no_stray(raw: dict[str, Any], known: tuple[str, ...], key: str) -> None:
    """A key nobody reads is a typo, and silence about it is how a lead's `brief:` disappears into
    a file that looks right (found while writing the first real org.yml, 2026-09-13)."""
    stray = sorted(k for k in raw if k not in known)
    if stray:
        raise ValueError(f"{key}: unknown key(s) {stray}; known: {list(known)}")


def _member(raw: Any, key: str) -> MemberDef:
    raw = _mapping(raw, key)
    if "team" in raw:
        extra = sorted(k for k in raw if k != "team")
        if extra:
            raise ValueError(f"{key}: a nested team member is `{{team: <name>}}` alone; unexpected {extra}")
        return MemberDef(team=_str(raw["team"], f"{key}.team"))
    _no_stray(raw, MEMBER_KEYS, key)
    role = _str(raw.get("role"), f"{key}.role")
    if not role:
        raise ValueError(f"{key}.role is required (or `team:` for a nested team)")
    return MemberDef(
        role=role,
        count=_count(raw.get("count"), f"{key}.count"),
        name=_str(raw.get("name"), f"{key}.name", default=role),
        home=_str(raw.get("home"), f"{key}.home"),
        lane=_lane(raw.get("lane"), f"{key}.lane"),
        brief=_opt_str(raw.get("brief"), f"{key}.brief"),
        profile=_opt_str(raw.get("profile"), f"{key}.profile"),
        grants=_grants(raw.get("grants"), f"{key}.grants"),
        unattended=_flag(raw.get("unattended"), f"{key}.unattended", default=True),
    )


def _team(name: str, raw: Any, key: str, *, source: Path) -> TeamDef:
    raw = _mapping(raw, key)
    _no_stray(raw, TEAM_KEYS, key)
    projects = raw.get("projects")
    if projects is None:
        projects = []
    if isinstance(projects, str):
        projects = [projects]
    if not isinstance(projects, list):
        raise ValueError(f"{key}.projects must be a list of project names")
    lead_raw = _mapping(raw.get("lead"), f"{key}.lead")
    _no_stray(lead_raw, LEAD_KEYS, f"{key}.lead")
    lead = LeadDef(
        role=_str(lead_raw.get("role"), f"{key}.lead.role", default=DEFAULT_LEAD_ROLE),
        name=_str(lead_raw.get("name"), f"{key}.lead.name", default=f"{name}-lead"),
        home=_str(lead_raw.get("home"), f"{key}.lead.home"),
        profile=_opt_str(lead_raw.get("profile"), f"{key}.lead.profile"),
        lane=_lane(lead_raw.get("lane"), f"{key}.lead.lane"),
        brief=_opt_str(lead_raw.get("brief"), f"{key}.lead.brief"),
        grants=_grants(lead_raw.get("grants"), f"{key}.lead.grants"),
        unattended=_flag(lead_raw.get("unattended"), f"{key}.lead.unattended", default=True),
    )
    members_raw = raw.get("members")
    if members_raw is None:
        members_raw = []
    if not isinstance(members_raw, list):
        raise ValueError(f"{key}.members must be a list")
    members = [_member(m, f"{key}.members[{i}]") for i, m in enumerate(members_raw)]
    return TeamDef(name, [_str(p, f"{key}.projects") for p in projects], lead, members, source)


# ── validation ────────────────────────────────────────────────────────────────────────────────


def _validate(org: Org, label: str) -> None:
    """The cross-references and the `home` rule (§4.9), reported one at a time, first error first."""
    for team in org.teams.values():
        key = f"{label}: teams.{team.name}"
        if not team.projects:
            raise ValueError(f"{key}.projects: a team is on one or more projects")
        for pname in team.projects:
            if pname not in org.projects:
                raise ValueError(f"{key}.projects: {pname!r} is not a defined project ({sorted(org.projects)})")
        repos = org.team_repos(team)
        if not repos:
            raise ValueError(f"{key}: its projects {team.projects} list no repos")
        if team.lead.role != PERSON:  # a person leads from nowhere: no session, so no home
            team.lead.home = _home(team.lead.home, repos, f"{key}.lead.home")
        for i, m in enumerate(team.members):
            mkey = f"{key}.members[{i}]"
            if m.team is not None:
                if m.team not in org.teams:
                    raise ValueError(f"{mkey}.team: {m.team!r} is not a defined team ({sorted(org.teams)})")
                continue
            m.home = _home(m.home, repos, f"{mkey}.home")
    _no_cycles(org, label)


def _home(home: str, repos: list[str], key: str) -> str:
    """`home` names a repo of the team's projects; required above one repo, defaulted to the only one."""
    if not home:
        if len(repos) == 1:
            return repos[0]
        raise ValueError(f"{key} is required when the team's projects span more than one repo ({repos})")
    if home not in repos:
        raise ValueError(f"{key}: {home!r} is not a repo of the team's projects ({repos})")
    return home


def _no_cycles(org: Org, label: str) -> None:
    """A team that nests itself, directly or through others, would never finish starting."""
    for root in org.teams:
        seen: list[str] = []
        stack = [root]
        while stack:
            t = stack.pop()
            if t in seen:
                if t == root:
                    raise ValueError(f"{label}: teams.{root} nests itself ({' → '.join([*seen, root])})")
                continue
            seen.append(t)
            stack.extend(m.team for m in org.teams[t].members if m.team is not None and m.team in org.teams)
