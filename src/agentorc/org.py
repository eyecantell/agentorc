"""Org definitions: projects, teams and an org-wide `roles:` overlay, declared once per UI host in
`~/.agentorc/org.yml` (design §4.9, §5).

```yaml
projects:
  agentorc:  {repos: {agentorc: {kmaster: ~/agentorc}}}
  guardians: {repos: {guardians: {devenv: /workspaces/guardians}, guardians-api: {devenv: /workspaces/guardians/api}}}
teams:
  ao-grind:
    projects: [agentorc]
    manager: {role: manager, name: manager-ao-1}
    techlead: {name: techlead-ao-1, context: docs/briefs/techlead-context.md}   # optional: the go-between (§4.9b)
    # anchor: false                   # every team has an anchor seat, `<team>-anchor`, unless it says so (§4.9b)
    seats: [{name: docs-audit-ao-1, role: auditor, trigger: {prs: 10}}]   # optional: seats with a trigger (§4.9b)
    entries: {feature: designer}      # optional: the role Add entry's session takes, per Type (§4.9)
    members:
      - {role: grinder, count: 2, name: grinder-ao, lane: free-pick}
      - {team: ao-ui}                 # a nested team
  cm-grind:
    projects: [contractmatch]
    host: contractmatch               # every session lands on that node (§4.4a "Teams across hosts")
    manager: {role: manager}
    members: [{role: grinder}]
roles:
  grinder: {profile: grind}
place:
  sam-grind: devenv                   # a repo-defined team that lands on a node (§4.9 *Where a repo's team lands*)
```

A repo's `.agentorc.yml` may carry `teams:` of its own (teams whose only project is that repo), and
the org a client sees is the aggregate (§4.9 *The org is an aggregate*, TD-229): `with_repos` folds
in the teams of every checkout in the host's registry, the org file winning a name collision (the
repo's reads *shadowed*) and a name two repos define refused in both, and says where each of a
repo's teams lands (`landing`: the org file's `place:`, else this host). The file is read by the
clients (`ao team`, `ao new`, the UI) on every use and cached nowhere; the host agent never reads
it — it stores `team` and `project` as two plain strings on the record and nothing keys on them
(§9 invariant 9). With no file the org is empty.
"""

from __future__ import annotations

import copy
import dataclasses
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agentorc import repoconfig
from sessionorc import hosts, paths
from sessionorc import settings as settings_mod
from sessionorc.models import GRANTS

DEFAULT_MANAGER_ROLE = "manager"
PERSON = "person"  # a manager role meaning the person manages: no manager session is started (§4.9)

# A host's registered checkouts, in its registry's order (the home's `host_repos`). Raises `OSError`
# when they cannot be told — an unreachable node, an older build — which is never *holds no repo*.
ReposOf = Callable[[str], list[str]]


@dataclass
class Project:
    name: str
    repos: dict[str, dict[str, Path]] = field(default_factory=dict)  # repo name → host name → checkout


# What a `manager:` with no `on_call` means (design §4.9): a seat on call, since the tick reads the
# `team` trigger and the mail sweep spares a question to a closed seat (TD-259 slices 2 and 3). A team
# keeps the shape it was started with until its next Start; `on_call: false` asks for a standing one.
ON_CALL_DEFAULT = True
# What fills a manager on call, in the card's words (design §4.5 *The card's anatomy*, TD-259)
MANAGER_WHEN = "comes when a member needs a reading"


@dataclass
class ManagerDef:
    role: str = DEFAULT_MANAGER_ROLE
    name: str = ""  # default `<team>-lead`, filled by the loader
    home: str = ""  # a repo name from the team's projects
    profile: str | None = None  # overrides the role's
    lane: list[str] = field(default_factory=list)
    brief: str | None = None  # overrides the role's template — the manager brief a repo keeps
    grants: list[str] | None = None  # None: the role's (`control` for `manager`)
    unattended: bool = True
    # a seat filled on §6 rule 3's `team` trigger and closed when it has acted, or a standing session (§4.9, TD-247)
    on_call: bool = ON_CALL_DEFAULT


@dataclass
class TechleadDef:
    """A team's techlead seat (design §4.9b): always the `techlead` role, always a session — a
    person answering questions is the person, which every team already has. The last three fields
    are fixed, so a launch reads the seat as it reads a member: no lane, the role's grants (none)."""

    name: str = ""  # default `<team>-techlead`, filled by the loader
    home: str = ""
    profile: str | None = None
    brief: str | None = None  # overrides the preset's `techlead.md`
    context: str | None = None  # the primer, a path in its home checkout: the brief's `{context}`
    lane: list[str] = field(default_factory=list)
    grants: list[str] | None = None
    unattended: bool = True


# Whether a team that writes no `anchor:` has the seat (design §4.9b *The anchor seat*: by default, as
# it has a manager). The suite turns it off (tests/conftest.py), as it turns identity off: a test of
# something else starts the team it writes, and the anchor's own tests turn it back on.
ANCHOR_DEFAULT = True
# What fills the anchor seat, in the card's words (design §4.5a *card: on call — the anchor seat's words*)
ANCHOR_WHEN = "comes when the checkout's lane gains work"


@dataclass
class AnchorDef:
    """A team's anchor seat (design §4.9b *The anchor seat*, TD-381): present by default as the
    manager is, always the `anchor` role, run in the home repo's **main checkout** rather than a
    worktree, filled on §6 rule 3's `work` trigger. Its lane and grants are the preset's."""

    name: str = ""  # default `<team>-anchor`, filled by the loader
    home: str = ""  # default the manager's home, by the `home` rule
    profile: str | None = None
    brief: str | None = None  # overrides the preset's `anchor.md`
    lane: list[str] = field(default_factory=list)
    grants: list[str] | None = None
    unattended: bool = True
    implied: bool = False  # the definition wrote no `anchor:`: the default's seat


@dataclass
class SeatDef:
    """A seat with a trigger (design §4.9b *Seats with a trigger*, TD-098): a session its manager
    fills when `trigger` is met and that ends on its own — `asks` (a question landed, the
    techlead's), `prs` (that many PRs merged since it last came) or `every` (a duration since it
    last came, `6h`). It holds no grants, declares nothing and is not counted in a wind-down, so
    `grants` is fixed empty and there is no lane; the role is any preset, `auditor` the designed one."""

    name: str = ""  # default `<team>-<role>`, filled by the loader
    role: str = ""
    trigger: str = "asks"  # `asks` | `prs` | `every`
    after: str = ""  # the trigger's value: `prs`' count, `every`'s duration as written; "" for `asks`
    home: str = ""
    profile: str | None = None
    brief: str | None = None  # overrides the role's template
    lane: list[str] = field(default_factory=list)
    grants: list[str] | None = field(default_factory=list)  # [], never None: the role's grants are not read (§4.9b)
    unattended: bool = True

    def when(self) -> str:
        """What would make it come, in the card's words (§4.5 *on call — runs after 10 PRs*)."""
        if self.trigger == "prs":
            return f"runs after {self.after} PR" + ("" if self.after == "1" else "s")
        if self.trigger == "every":
            return f"runs every {self.after}"
        return "comes on the next question"


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
    manager: ManagerDef = field(default_factory=ManagerDef)
    members: list[MemberDef] = field(default_factory=list)
    techlead: TechleadDef | None = None  # the seat (§4.9b), when the definition has one
    anchor: AnchorDef | None = None  # the anchor seat (§4.9b, TD-381): present unless `anchor: false`
    seats: list[SeatDef] = field(default_factory=list)  # seats with a trigger (§4.9b, TD-098)
    source: Path | None = None  # the file it was read from (`ao team list` names it)
    host: str = ""  # where every session lands (design §4.4a "Teams across hosts"); "" is the host the start runs on
    # the role a person's entry session takes, per Type (design §4.9 *Add an entry to the ledger*, TD-219):
    # only the keys the definition says; `entry_role` reads a missing one as the techlead
    entries: dict[str, str] = field(default_factory=dict)
    # the flows the team may run, the first its default (design §4.9c, TD-309): names only — a flow
    # is found and judged where the team's repo is read (`teams.plan`, `flowdefs`); empty: no flow
    flows: list[str] = field(default_factory=list)
    # `teams.<team>.flow` in `settings.yml` (§4.9c, §5): the flow a person picked — the setting, not
    # the definition, so no file sets it; `with_settings` does, on the client's own copy. "" is none
    flow: str = ""

    def entry_role(self, type_: str) -> str:
        """The role **Open a session** on the Add entry form starts for `type_` (`debt` | `feature`)."""
        return self.entries.get(type_, TECHLEAD_ROLE)

    def session_names(self) -> list[str]:
        """Every session name the definition names, as often as it names it: the manager (when a
        session manages), the techlead seat, the anchor seat, then each member entry's (design §4.9 *Add
        or remove a member from the team card*: a name the definition holds is never added again, TD-268)."""
        out = [self.manager.name] if self.manager.role != PERSON and self.manager.name else []
        if self.techlead is not None:
            out.append(self.techlead.name)
        if self.anchor is not None:
            out.append(self.anchor.name)
        return out + [n for m in self.members if m.team is None for n in m.names()]

    def twice_named(self) -> list[str]:
        """The names the definition holds more than once, in order — what `ao org check` and the
        Members dialog say (TD-268)."""
        seen: list[str] = []
        names = self.session_names()
        for n in names:
            if names.count(n) > 1 and n not in seen:
                seen.append(n)
        return seen

    def next_name(self, role: str) -> str:
        """The name **Add member** defaults to for `role` (design §4.9): the next of a counted entry
        of that role (the `count:` an add bumps), else the team's pattern for it — the last entry of
        the role, its trailing number dropped — with the first number no name of the team holds
        (`grinder-dc-1` → `grinder-dc-2`); a role the team has no entry of, the role itself while
        free. Otherwise never a name the definition already holds (TD-268); a counted entry's next is
        what its bump makes whatever the box says, and Add refuses it when another entry holds it."""
        taken = set(self.session_names())
        same = [m for m in self.members if m.team is None and m.role == role]
        counted = next((m for m in same if m.count > 1), None)
        if counted is not None:
            return f"{counted.name}-{counted.count + 1}"
        base = same[-1].name if same else role
        stem = re.sub(r"-\d+$", "", base)
        if not same and base not in taken:
            return base
        n = 1 if stem != base else 2
        while f"{stem}-{n}" in taken:
            n += 1
        return f"{stem}-{n}"


@dataclass
class Org:
    projects: dict[str, Project] = field(default_factory=dict)
    teams: dict[str, TeamDef] = field(default_factory=dict)
    roles: dict[str, dict[str, Any]] = field(default_factory=dict)  # the org-wide overlay (§4.9 precedence)
    path: Path | None = None
    # a repo's definition the org file's of the same name wins over: team → the repo files (§4.9)
    shadowed: dict[str, list[Path]] = field(default_factory=dict)
    # a name two repos define, refused in both (§4.9 *Names are the org's*): team → why, naming each
    refused: dict[str, str] = field(default_factory=dict)
    # where a repo-defined team lands when no repo may say (§4.9 *Where a repo's team lands*): team → host
    place: dict[str, str] = field(default_factory=dict)
    # where each repo-defined team lands and why: team → (host, *place* | *registered here*)
    landed: dict[str, tuple[str, str]] = field(default_factory=dict)
    # a repo-defined team whose landing cannot be told, which no start goes past: team → why
    unlanded: dict[str, str] = field(default_factory=dict)

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

    def anchor_first(self, team: TeamDef) -> TeamDef | None:
        """The team before `team`, in the org's order, whose anchor seat has the same home repo on
        the same host (design §4.9b *One per repo*: two seats in one checkout) — the one `ao team start` and
        `ao org check` name when they refuse `team`'s. None when `team` has no anchor or is first."""
        if team.anchor is None:
            return None

        for other in self.teams.values():
            if other is team:
                return None
            if other.name in self.unlanded or other.anchor is None:
                continue  # a team whose landing cannot be told starts nowhere, so it holds no checkout
            # `host` is where it lands — a repo's team landing elsewhere has it set (`_land`); "" here
            if other.anchor.home == team.anchor.home and other.host == team.host:
                return other
        return None


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
        # checked per key as a repo's `roles:` is (TD-149 (4)): a typo is a line naming the key when
        # the file is read, never a preset that silently gains a key nothing reads
        org.roles[str(rname)] = repoconfig._role_block(str(rname), raw, f"{label}: roles")
    for tname, raw in _mapping(data.get("place"), f"{label}: place").items():
        host = _str(raw, f"{label}: place.{tname}").strip()
        if not host:
            raise ValueError(f"{label}: place.{tname}: name the host the team lands on")
        if str(tname) in org.teams:
            raise ValueError(
                f"{label}: place.{tname}: {tname!r} is defined in this file, where its own `host:` places it — "
                "`place:` is for a team a repo defines (design §4.9 *Where a repo's team lands*)"
            )
        org.place[str(tname)] = host
    _validate(org, label)
    for team in org.teams.values():
        _entries_resolve(org, team, f"{label}: teams.{team.name}.entries", ())
    return org


def merge_repo_teams(
    org: Org, repo_root: Path, repo_teams: dict[str, Any] | None, repo_roles: Collection[str] = ()
) -> Org:
    """Fold a repo's `.agentorc.yml` `teams:` into the org (design §4.9): each is a team whose only
    project is that repo, so a repo can ship its own grind team beside its code. The org file wins
    a name collision, and the repo's is recorded in `shadowed`; each definition keeps its `source`.
    Returns a new Org; `org` is untouched. `repo_roles` names the roles the repo's own file defines,
    which its teams' `entries:` may name.

    The repo is the team's project, unsaid: the checkout's directory name, on this host at
    `repo_root`; an org project of that name is used as it stands. Spanning, placing and nesting
    are the org file's, so a `projects:` key, a `host:` and a nested `{team: …}` member are each
    refused naming the key (§4.9 *The org is an aggregate*)."""
    repo_root = Path(repo_root).expanduser().resolve()
    rname = repo_root.name
    merged = Org(
        projects=dict(org.projects),
        teams=dict(org.teams),
        roles=dict(org.roles),
        path=org.path,
        shadowed={k: list(v) for k, v in org.shadowed.items()},
        refused=dict(org.refused),
        place=dict(org.place),
        landed=dict(org.landed),
        unlanded=dict(org.unlanded),
    )
    source = repo_root / ".agentorc.yml"
    label = f"{source}"
    teams = _mapping(repo_teams, f"{label}: teams")
    for tname, raw in teams.items():
        key = f"{label}: teams.{tname}"
        raw = _mapping(raw, key)
        for k in ("projects", "host"):
            if k in raw:
                raise ValueError(
                    f"{key}.{k}: a repo's team is on its own repo and lands where the org places it — "
                    f"`{k}:` is the org file's (design §4.9 *The org is an aggregate*)"
                )
        for i, m in enumerate(raw.get("members") or []):
            if isinstance(m, dict) and "team" in m:
                raise ValueError(
                    f"{key}.members[{i}].team: a nested team is the org file's, not a repo's "
                    "(design §4.9 *The org is an aggregate*)"
                )
    if teams and rname not in merged.projects:
        merged.projects[rname] = Project(rname, {rname: {hosts.local_host().name: repo_root}})
    for tname, raw in teams.items():
        tname = str(tname)
        if tname in org.teams:
            merged.shadowed.setdefault(tname, []).append(source)  # the org file wins
            continue
        raw = dict(_mapping(raw, f"{label}: teams.{tname}"))
        raw["projects"] = [rname]
        merged.teams[tname] = _team(tname, raw, f"{label}: teams.{tname}", source=source)
    _validate(merged, label)
    for tname in teams:  # only the teams this repo adds: each is checked once, against its own repo's roles
        team = merged.teams.get(str(tname))
        if team is not None and team.source == source:
            _entries_resolve(merged, team, f"{label}: teams.{tname}.entries", repo_roles)
    return merged


def landing(team: str, place: dict[str, str], here: str) -> tuple[str, str]:
    """Where a repo-defined team lands, and why (design §4.9 *Where a repo's team lands*): `place:`
    when it names the team, else this host — a definition is read at the home and only there, from a
    checkout its registry holds (§4.9 *A definition is read at the home*). `(host, why)`, the why in
    `ao org`'s words."""
    if place.get(team):
        return place[team], "place"
    return here, "registered here"


def _named(repos: Collection[str], repo: str) -> list[str]:
    """The registry entries that are `repo`: a checkout is its directory's name (§4.9)."""
    return [str(r) for r in repos if Path(str(r)).name == repo]


def _land(org: Org, base: Collection[str], here: str, repos_of: ReposOf | None) -> None:
    """Give each repo-defined team of `org` its landing, in place (`org` is `with_repos`' own copy):
    `landed`, and for one that lands on another host its `host` and the repo's path there — that
    host's registry entry (§4.9 *The repo is the team's project*), asked through `repos_of`. A
    project the org file defines (`base`) is used as it stands and nothing is asked. A landing that
    cannot be told is `unlanded`, with the reason a start gives."""
    for team in org.teams.values():
        if team.source is None or team.source == org.path or len(team.projects) != 1:
            continue  # the org file's own: its `host:` places it
        repo = team.projects[0]
        host, why = landing(team.name, org.place, here)
        org.landed[team.name] = (host, why)
        if host == here:
            continue
        team.host = host
        if host in org.projects[repo].repos.get(repo, {}):
            continue
        if repo in base:  # the org file's project, as it stands: it says no path there, and nothing is asked
            org.unlanded[team.name] = (
                f"team {team.name}: `place:` puts it on {host}, and org.yml's project {repo} names no checkout "
                f"there — add `{host}: <path>` under `projects.{repo}.repos.{repo}`"
            )
            continue
        try:
            if repos_of is None:
                raise OSError("nothing here asks a host for its registry")
            found = _named(repos_of(host), repo)
        except OSError as e:
            org.unlanded[team.name] = (
                f"team {team.name}: `place:` puts it on {host}, whose registry could not be read ({e}) — "
                "nothing was started"
            )
            continue
        if len(found) != 1:
            org.unlanded[team.name] = (
                f"team {team.name}: `place:` puts it on {host}, whose registry "
                + (
                    f"holds {repo} {len(found)} times ({', '.join(found)})"
                    if found
                    else f"holds no checkout named {repo}"
                )
                + f" — register the one checkout there, or name its path under `projects.{repo}` in org.yml"
            )
            continue
        by_host = dict(org.projects[repo].repos.get(repo, {}))
        by_host[host] = Path(found[0])
        org.projects[repo] = Project(repo, {**org.projects[repo].repos, repo: by_host})


def with_repos(org: Org, roots: Collection[Path | str], *, repos_of: ReposOf | None = None) -> tuple[Org, list[str]]:
    """The org a client sees (design §4.9 *The org is an aggregate*, TD-229): `org` — the org file —
    plus the `teams:` of every checkout in `roots` (the host's repos registry), as each is checked
    out. `ao team` and the pages both read this one function, so they cannot disagree.

    A name two repos define is refused in both, each naming the other, and kept in `refused`: a
    team's name keys its settings and its badge, and the later repo winning silently is how a start
    would run the wrong team. The org file still wins a name over any repo (`shadowed`). A repo
    whose `.agentorc.yml` cannot be read, or whose teams are refused, is skipped and named in the
    returned notes — one broken file must not empty the page or `ao team list`.

    Each repo's team is then given where it lands (`landing`, `_land`): the org file's `place:`,
    else this host, whose registry is what `roots` is. `repos_of` asks another host for its
    registry — the path of a placed team's checkout there — and is only called for a team that
    `place:` puts on another host. One that cannot be landed stays listed, `unlanded` saying why."""
    notes: list[str] = []
    base, file_org = set(org.projects), org
    found: list[tuple[repoconfig.RepoConfig, dict[str, Any]]] = []
    seen: set[Path] = set()
    for root in roots:
        where = Path(root).expanduser().resolve()
        if where in seen:  # one checkout written twice (a trailing slash, a symlink) is one repo, not two
            continue
        seen.add(where)
        try:
            cfg = repoconfig.load(Path(root).expanduser())
        except (OSError, ValueError) as e:
            notes.append(f"{root}: {str(e).strip(chr(34))}")
            continue
        if cfg.teams and cfg.root:
            found.append((cfg, dict(cfg.teams)))
    by_name: dict[str, list[Path]] = {}
    for cfg, teams in found:
        for tname in teams:
            by_name.setdefault(str(tname), []).append(Path(cfg.root) / repoconfig.FILE)
    twice = {n: files for n, files in by_name.items() if len(files) > 1 and n not in org.teams}
    for cfg, teams in found:
        mine = {k: v for k, v in teams.items() if str(k) not in twice}
        try:
            org = merge_repo_teams(org, Path(cfg.root), mine, repoconfig.role_names(cfg)) if mine else org
        except (OSError, ValueError) as e:
            notes.append(f"{cfg.root}: {str(e).strip(chr(34))}")
    if twice or org is file_org:  # a copy of our own before anything is written on it
        org = Org(
            projects=dict(org.projects),
            teams=dict(org.teams),
            roles=org.roles,
            path=org.path,
            shadowed=org.shadowed,
            refused=dict(org.refused),
            place=dict(org.place),
            landed=dict(org.landed),
            unlanded=dict(org.unlanded),
        )
    if twice:
        for n, files in twice.items():
            org.refused[n] = (
                f"team {n!r} is defined twice — in {' and in '.join(map(str, files))} — so neither starts: "
                "a team's name is the org's; rename one (design §4.9 *Names are the org's*)"
            )
            notes.append(org.refused[n])
    _land(org, base, hosts.local_host().name, repos_of)
    notes.extend(org.unlanded.values())
    for tname, host in org.place.items():  # a mistyped name would otherwise place nothing, silently
        if tname not in org.teams and tname not in org.refused:
            notes.append(f"place.{tname}: no registered repo defines a team {tname!r} — nothing is placed on {host}")
    return org, notes


def with_settings(org: Org, teams: Mapping[str, Any] | None) -> Org:
    """`org` with each team's `teams.<team>.flow` from the home's `settings.yml` (§4.9c *A team lists
    its flows, and the person picks one*), as the agent's `settings` read gives `teams`. A copy: the
    definition is read and cached elsewhere, and a setting is never written into it. A value the
    team no longer lists is kept as read — `teams.current_flow` reads it as the first, and says so."""
    picked = {
        name: str(t["flow"])
        for name, t in (teams or {}).items()
        if isinstance(t, dict) and t.get("flow") and name in org.teams
    }
    if not picked:
        return org
    out = copy.copy(org)
    out.teams = {n: dataclasses.replace(t, flow=picked[n]) if n in picked else t for n, t in org.teams.items()}
    return out


def with_home_settings(org: Org) -> Org:
    """`with_settings` from the home's own `settings.yml`, read here: a client that reads the org runs
    on the home (a node reads no org, §4.4a), and the agent's `settings` read is a person's own — so a
    session's `ao new --team` or a lead's start would otherwise compile a flow the person did not pick."""
    return with_settings(org, settings_mod.teams(settings_mod.load()))


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
    grants = list(dict.fromkeys(_str(g, key) for g in raw))
    if bad := [g for g in grants if g not in GRANTS]:
        raise ValueError(f"{key}: unknown grant {bad[0]!r} (known: {', '.join(GRANTS)})")
    return grants


MANAGER_KEYS = ("role", "name", "home", "profile", "lane", "brief", "grants", "unattended", "on_call")
# a member is never on call (§4.9): the key is the manager's alone, so on a member it is a stray one
MEMBER_KEYS = (*(k for k in MANAGER_KEYS if k != "on_call"), "count", "team")
TEAM_KEYS = ("projects", "manager", "techlead", "anchor", "seats", "members", "host", "entries", "flows")
# the ledger's `Type:` values (cadence §2.11), each a key `entries:` may carry (§4.9, TD-219)
ENTRY_TYPES = ("debt", "feature")
TECHLEAD_KEYS = ("name", "home", "profile", "brief", "context")
TECHLEAD_ROLE = "techlead"
ANCHOR_KEYS = ("name", "home", "profile", "brief")
ANCHOR_ROLE = "anchor"
SEAT_KEYS = ("name", "role", "trigger", "brief", "profile", "home")
TRIGGERS = ("asks", "prs", "every")
_DURATION = re.compile(r"[1-9]\d*[mhd]")


def _no_stray(raw: dict[str, Any], known: tuple[str, ...], key: str) -> None:
    """A key nobody reads is a typo, and silence about it is how a manager's `brief:` disappears into
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
    manager_raw = _mapping(raw.get("manager"), f"{key}.manager")
    _no_stray(manager_raw, MANAGER_KEYS, f"{key}.manager")
    manager_role = _str(manager_raw.get("role"), f"{key}.manager.role", default=DEFAULT_MANAGER_ROLE)
    if manager_role == PERSON and "on_call" in manager_raw:
        raise ValueError(f"{key}.manager.on_call: a person manages this team, so no session is started to fill")
    manager = ManagerDef(
        role=manager_role,
        name=_str(manager_raw.get("name"), f"{key}.manager.name", default=f"{name}-lead"),
        home=_str(manager_raw.get("home"), f"{key}.manager.home"),
        profile=_opt_str(manager_raw.get("profile"), f"{key}.manager.profile"),
        lane=_lane(manager_raw.get("lane"), f"{key}.manager.lane"),
        brief=_opt_str(manager_raw.get("brief"), f"{key}.manager.brief"),
        grants=_grants(manager_raw.get("grants"), f"{key}.manager.grants"),
        unattended=_flag(manager_raw.get("unattended"), f"{key}.manager.unattended", default=True),
        # a person's team starts nothing, so it is never on call whatever the default; and the default
        # is the `manager` role's alone — the one whose template has a seat's shape
        # (`repoconfig.ON_CALL_BRIEFS`): another role managing is a seat only where its definition says so
        on_call=manager_role != PERSON
        and _flag(
            manager_raw.get("on_call"),
            f"{key}.manager.on_call",
            default=ON_CALL_DEFAULT and manager_role == DEFAULT_MANAGER_ROLE,
        ),
    )
    members_raw = raw.get("members")
    if members_raw is None:
        members_raw = []
    if not isinstance(members_raw, list):
        raise ValueError(f"{key}.members must be a list")
    members = [_member(m, f"{key}.members[{i}]") for i, m in enumerate(members_raw)]
    projects = [_str(p, f"{key}.projects") for p in projects]
    return TeamDef(
        name,
        projects,
        manager,
        members,
        techlead=_techlead(name, raw.get("techlead"), f"{key}.techlead") if "techlead" in raw else None,
        anchor=_anchor(name, raw["anchor"], f"{key}.anchor")
        if "anchor" in raw
        else (AnchorDef(name=f"{name}-anchor", implied=True) if ANCHOR_DEFAULT else None),
        seats=_seats(name, raw.get("seats"), f"{key}.seats"),
        source=source,
        host=_str(raw.get("host"), f"{key}.host"),
        entries=_entries(raw.get("entries"), f"{key}.entries"),
        flows=_flows(raw.get("flows"), f"{key}.flows"),
    )


def _flows(raw: Any, key: str) -> list[str]:
    """`flows: [td, build-review]` (design §4.9c): flow names, each once. Whether each names a usable
    flow the team can follow is read with the team's repo (`teams.plan`)."""
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(f, str) and f.strip() for f in raw):
        raise ValueError(f"{key} must be a list of flow names, e.g. [td, build-review]")
    names = [f.strip() for f in raw]
    if twice := [n for n in dict.fromkeys(names) if names.count(n) > 1]:
        raise ValueError(f"{key}: {twice[0]!r} is listed twice")
    return names


def _entries(raw: Any, key: str) -> dict[str, str]:
    """`entries: {feature: <role>, debt: <role>}` (design §4.9, TD-219): either key optional; an
    unknown key is refused naming it, and so is a value that is not a role name. Whether the role
    resolves is `_validate`'s, which knows the org's roles."""
    raw = _mapping(raw, key)
    stray = sorted(str(k) for k in raw if k not in ENTRY_TYPES)
    if stray:
        raise ValueError(f"{key}: unknown key(s) {stray}; known: {list(ENTRY_TYPES)} (the ledger's Type)")
    out: dict[str, str] = {}
    for t in ENTRY_TYPES:
        if t in raw:
            role = _str(raw[t], f"{key}.{t}")
            if not role:
                raise ValueError(f"{key}.{t}: a role name, e.g. `techlead`")
            if role == PERSON:
                raise ValueError(f"{key}.{t}: {PERSON!r} is not a role a session takes")
            out[t] = role
    return out


def _techlead(team: str, raw: Any, key: str) -> TechleadDef:
    """`techlead: {name, home, profile, brief, context}` (design §4.9b). No `role:` — the seat is the role —
    and no `grants:`: the preset holds none (identity alarms go to the person, §4.8a).
    `techlead: person` is refused as any non-mapping is."""
    raw = _mapping(raw, key)
    _no_stray(raw, TECHLEAD_KEYS, key)
    return TechleadDef(
        name=_str(raw.get("name"), f"{key}.name", default=f"{team}-techlead"),
        home=_str(raw.get("home"), f"{key}.home"),
        profile=_opt_str(raw.get("profile"), f"{key}.profile"),
        brief=_opt_str(raw.get("brief"), f"{key}.brief"),
        context=_opt_str(raw.get("context"), f"{key}.context"),
    )


def _anchor(team: str, raw: Any, key: str) -> AnchorDef | None:
    """`anchor: {name, home, profile, brief}` or `anchor: false` (design §4.9b *The anchor seat*): a
    team that says neither has one, as it has a manager. No `role:`, `lane:` or `grants:` — the seat
    is the role, and its lane and grants are the preset's. `anchor: true` reads as the default."""
    if raw is False:
        return None
    if raw is True or raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"{key} must be a mapping {{name, home, profile, brief}} or false, not {raw!r}")
    _no_stray(raw, ANCHOR_KEYS, key)
    return AnchorDef(
        name=_str(raw.get("name"), f"{key}.name", default=f"{team}-anchor"),
        home=_str(raw.get("home"), f"{key}.home"),
        profile=_opt_str(raw.get("profile"), f"{key}.profile"),
        brief=_opt_str(raw.get("brief"), f"{key}.brief"),
    )


def _seats(team: str, raw: Any, key: str) -> list[SeatDef]:
    """`seats: [{name, role, trigger, brief, profile, home}]` (design §4.9b *Seats with a trigger*).
    `trigger` is `asks`, `{prs: <n>}` or `{every: <n>m|h|d}`, and is required: a seat nobody says
    when to fill is a member that never runs. A seat is a session and never `person`, and holds no
    `grants:` and no `lane:` — the design gives it none, so a key for them is refused as a typo is."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{key} must be a list")
    out: list[SeatDef] = []
    for i, item in enumerate(raw):
        skey = f"{key}[{i}]"
        item = _mapping(item, skey)
        _no_stray(item, SEAT_KEYS, skey)
        role = _str(item.get("role"), f"{skey}.role")
        if not role:
            raise ValueError(f"{skey}.role is required")
        if role in (PERSON, DEFAULT_MANAGER_ROLE, TECHLEAD_ROLE, ANCHOR_ROLE):
            raise ValueError(
                f"{skey}.role: {role!r} is not a seat's role — a seat is a session its manager fills on a trigger "
                "(the techlead and the anchor have their own `techlead:` and `anchor:` keys)"
            )
        trigger, after = _trigger(item.get("trigger"), f"{skey}.trigger")
        out.append(
            SeatDef(
                name=_str(item.get("name"), f"{skey}.name", default=f"{team}-{role}"),
                role=role,
                trigger=trigger,
                after=after,
                home=_str(item.get("home"), f"{skey}.home"),
                profile=_opt_str(item.get("profile"), f"{skey}.profile"),
                brief=_opt_str(item.get("brief"), f"{skey}.brief"),
            )
        )
    return out


def _trigger(raw: Any, key: str) -> tuple[str, str]:
    """`asks` | `{prs: <n>}` | `{every: <duration>}` → `(kind, value as written)`."""
    if raw is None:
        raise ValueError(f"{key} is required: `asks`, `{{prs: <n>}}` or `{{every: <n>m|h|d}}`")
    if raw == "asks":
        return "asks", ""
    if not isinstance(raw, dict) or len(raw) != 1 or next(iter(raw)) not in TRIGGERS[1:]:
        raise ValueError(f"{key}: expected `asks`, `{{prs: <n>}}` or `{{every: <n>m|h|d}}`, got {raw!r}")
    kind, value = next(iter(raw.items()))
    if kind == "prs":
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key}.prs must be a whole number of PRs, 1 or more (got {value!r})")
        return "prs", str(value)
    if not isinstance(value, str) or not _DURATION.fullmatch(value):
        raise ValueError(f"{key}.every must be a duration like 30m, 6h or 1d (got {value!r})")
    return "every", value


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
        if team.manager.role != PERSON:  # a person manages from nowhere: no session, so no home
            team.manager.home = _home(team.manager.home, repos, f"{key}.manager.home")
        if team.techlead is not None:
            team.techlead.home = _home(team.techlead.home, repos, f"{key}.techlead.home")
        if team.anchor is not None:  # `home` as the manager's (§4.9): unsaid, the manager's home where it has one
            mhome = team.manager.home if team.manager.role != PERSON else ""
            if team.anchor.implied and not (team.anchor.home or mhome) and len(repos) > 1:
                team.anchor = None  # no home to tell, and nothing written to say one: no seat, never a refusal
            else:
                team.anchor.home = _home(team.anchor.home or mhome, repos, f"{key}.anchor.home")
        for i, seat in enumerate(team.seats):
            seat.home = _home(seat.home, repos, f"{key}.seats[{i}].home")
        for i, m in enumerate(team.members):
            mkey = f"{key}.members[{i}]"
            if m.team is not None:
                if m.team not in org.teams:
                    raise ValueError(f"{mkey}.team: {m.team!r} is not a defined team ({sorted(org.teams)})")
                continue
            m.home = _home(m.home, repos, f"{mkey}.home")
    _no_cycles(org, label)


def _entries_resolve(org: Org, team: TeamDef, key: str, repo_roles: Collection[str]) -> None:
    """Each role `entries:` names resolves (design §4.9, TD-219): a built-in preset, the org's
    `roles:`, an org role directory, a role the repo defines (its directories, design §4.9c), or one
    the team's own definition starts."""
    known = [*repoconfig.role_names(repoconfig.RepoConfig()), *org.roles, *repo_roles]
    known += [m.role for m in team.members if m.team is None and m.role] + [s.role for s in team.seats]
    for t, role in team.entries.items():
        if role not in known:
            raise ValueError(f"{key}.{t}: unknown role {role!r}; known: {', '.join(dict.fromkeys(known))}")


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


# ── Members… (design §4.9 *Add or remove a member from the team card*, TD-163, built by TD-172) ──

_KEY = re.compile(r"^(?P<ind>[ \t]*)(?P<key>[^\s#:][^:#]*?):[ \t]*(?P<rest>[^\n]*?)\s*$")
_ITEM = re.compile(r"^(?P<ind>[ \t]*)- (?P<body>.*?)\s*$")
_COUNT = re.compile(r"(\bcount:\s*)(\d+)")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _quiet(line: str) -> bool:
    """A blank line or a comment: neither ends a block nor belongs to it."""
    return not line.strip() or line.lstrip().startswith("#")


def _members_block(lines: list[str], team: str) -> tuple[int, list[tuple[int, dict[str, Any], bool]]]:
    """Where `teams.<team>.members` is in the text, and its items: `(members line index, [(line index,
    the item parsed, whether it is one line)])`. Raises ValueError with the reason when the team or
    its block cannot be edited as one line (design §4.9)."""
    top = next(
        (i for i, ln in enumerate(lines) if _indent(ln) == 0 and re.match(r"^teams:\s*(#.*)?$", ln.rstrip())), None
    )
    if top is None:
        raise ValueError(f"org.yml has no teams: block to find {team} in")
    t_at = t_ind = child = None
    for i in range(top + 1, len(lines)):
        ln = lines[i]
        if _quiet(ln):
            continue
        if _indent(ln) == 0:
            break
        child = _indent(ln) if child is None else child  # the teams' own keys sit at the first indent
        if _indent(ln) != child:
            continue
        m = _KEY.match(ln.rstrip("\n"))
        if m and m["key"].strip().strip("'\"") == team:
            t_at, t_ind = i, _indent(ln)
            if m["rest"] and not m["rest"].startswith("#"):
                raise ValueError(f"team {team} is written on one line in org.yml — edit it by hand")
            break
    if t_at is None:
        raise ValueError(f"org.yml does not define a team {team}")
    m_at = m_ind = None
    for i in range(t_at + 1, len(lines)):
        ln = lines[i]
        if _quiet(ln):
            continue
        if _indent(ln) <= t_ind:
            break
        m = _KEY.match(ln.rstrip("\n"))
        if m and m["key"].strip() == "members":
            if m["rest"] and not m["rest"].startswith("#"):
                raise ValueError(f"team {team}'s members: is written on one line — edit it by hand")
            m_at, m_ind = i, _indent(ln)
            break
    if m_at is None:
        raise ValueError(f"team {team} has no members: block in org.yml — add the first member by hand")
    items: list[tuple[int, dict[str, Any], bool]] = []
    dash = None  # the items' own indent: a dash deeper than it is inside an item, not a new one
    for i in range(m_at + 1, len(lines)):
        ln = lines[i]
        if _quiet(ln):
            continue
        if _indent(ln) < m_ind or (_indent(ln) == m_ind and not ln.lstrip().startswith("- ")):
            break
        it = _ITEM.match(ln.rstrip("\n"))
        if dash is None and it is not None:
            dash = _indent(ln)
        if it is None or _indent(ln) != dash:
            if items:
                items[-1] = (items[-1][0], items[-1][1], False)  # a continuation: that item is multi-line
                continue
            raise ValueError(f"team {team}'s members: block is not a list of - items")
        try:
            body = yaml.safe_load(it["body"])
        except yaml.YAMLError:
            body = None
        items.append((i, body if isinstance(body, dict) else {}, isinstance(body, dict)))
    return m_at, items


def _set_count(line: str, body: dict[str, Any], n: int) -> str:
    """`line` with its entry's `count:` set to `n` — the one `count: <digits>` whose replacement makes
    the entry parse as it did with only its count changed, since a name or a lane may hold the same
    characters (review of PR #608). None fits: a ValueError, and the file is not written."""
    it = _ITEM.match(line.rstrip("\r\n"))
    want = {**body, "count": n}
    for m in _COUNT.finditer(line):
        cand = line[: m.start(2)] + str(n) + line[m.end(2) :]
        got = _ITEM.match(cand.rstrip("\r\n"))
        try:
            parsed = yaml.safe_load(got["body"]) if got else None
        except yaml.YAMLError:
            continue
        if parsed == want and it is not None:
            return cand
    raise ValueError("this member's count: cannot be edited as one field of its line — edit it by hand")


def edit_members(
    path: Path, team: str, *, add: dict[str, Any] | None = None, remove: int | None = None, role: str = ""
) -> str:
    """Add or remove one member of `team` in `org.yml`, **as text, in place** (design §4.9 *Add or
    remove a member from the team card*): comments, order and spacing stay, because nothing is
    re-serialised. **Add** (`{role, name, lane}`) bumps the `count:` of the member line of that
    role that carries a count above one — the name pattern gives the next member — else appends one
    `- {role, name, lane}` line after the block's last item. **Remove** (`remove`, the entry's index
    in the block, `role` its role, checked) decrements that entry's `count:` — the highest-numbered
    member goes — or deletes its one line. Refused with the reason when the edit cannot be one
    line: a multi-line member, a `{team: …}` member, a team without a `members:` block. After the
    write the file is parsed again; a parse that fails restores the bytes and refuses, so a
    definition is never left unreadable. Returns what was done, in words."""
    if path.name == repoconfig.FILE:  # a repo's team is changed by PR (§4.9 *The org is an aggregate*, TD-229)
        raise ValueError(f"{path}: a repo's .agentorc.yml is changed by PR — Members… edits the org file only")
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    lines = text.splitlines(keepends=True)
    m_at, items = _members_block(lines, team)
    if add is not None:
        want = str(add.get("role") or "")
        if not want:
            raise ValueError("a member needs a role")
        try:
            held = load(path).teams[team].session_names()
        except Exception:  # an unreadable file is refused by the re-parse below, in its words
            held = []
        hit = next(
            (
                x
                for x in items
                if x[2] and x[1].get("role") == want and isinstance(x[1].get("count"), int) and x[1]["count"] > 1
            ),
            None,
        )
        if hit is not None:
            i, body, _ = hit
            n = body["count"]
            if f"{body.get('name') or want}-{n + 1}" in held:  # the bump would name a session twice (TD-268)
                raise ValueError(f"{team} already has {body.get('name') or want}-{n + 1}")
            lines[i] = _set_count(lines[i], body, n + 1)
            did = f"{body.get('name') or want} count: {n} → {n + 1}"
        else:
            entry = {"role": want}
            if add.get("name"):
                entry["name"] = str(add["name"])
            if entry.get("name", want) in held:  # refused before anything is written (TD-268)
                raise ValueError(f"{team} already has {entry.get('name', want)}")
            lane = [str(x) for x in add.get("lane") or [] if str(x).strip()]
            if lane:
                entry["lane"] = lane
            flow = yaml.safe_dump(entry, default_flow_style=True, sort_keys=False, width=10_000).strip()
            if items:
                last = items[-1][0]
                ind = " " * _indent(lines[last])
                at = last + 1
                while at < len(lines) and not _quiet(lines[at]) and _indent(lines[at]) > _indent(lines[last]):
                    at += 1  # past a multi-line last item's continuation
            else:
                ind = " " * (_indent(lines[m_at]) + 2)
                at = m_at + 1
            if at > 0 and not lines[at - 1].endswith("\n"):
                lines[at - 1] += "\n"
            lines.insert(at, f"{ind}- {flow}\n")
            did = f"added {entry.get('name') or want} ({want})"
    elif remove is not None:
        if not 0 <= remove < len(items):
            raise ValueError(f"team {team} has no member entry {remove + 1} — reload and try again")
        i, body, one = items[remove]
        if not one:
            raise ValueError(f"team {team}'s member entry {remove + 1} spans several lines — edit it by hand")
        if "team" in body:
            raise ValueError(f"member entry {remove + 1} is the nested team {body['team']} — edit it by hand")
        if role and body.get("role") != role:
            raise ValueError(f"team {team}'s member entry {remove + 1} is not a {role} any more — reload and try again")
        n = body.get("count", 1)
        if isinstance(n, int) and n > 1:
            lines[i] = _set_count(lines[i], body, n - 1)
            did = f"{body.get('name') or body.get('role')} count: {n} → {n - 1}"
        else:
            del lines[i]
            did = f"removed {body.get('name') or body.get('role')}"
    else:
        raise ValueError("nothing to edit: add or remove")
    path.write_text("".join(lines), encoding="utf-8")
    try:
        load(path)
    except Exception as e:
        path.write_bytes(raw)  # restored: a definition is never left unreadable (design §4.9)
        raise ValueError(f"the edit would not parse, so org.yml is as it was: {e}") from e
    return did
