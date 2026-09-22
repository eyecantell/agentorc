"""`ao team`: turning a team definition (§4.9) into the sequence of `create` calls that starts it.

The definitions are read by the clients (`agentorc.org`, `agentorc.repoconfig`); this module holds
what a start *means* — which session runs where, under which role, profile and brief — and nothing
about the transport: `plan` touches no RPC and no tmux, so every check it makes is testable on its
own. `agentorc.cli` runs the plan: the name checks (the `name_check` RPC, §4.1), then the lead,
then each member with `controllers: [lead id]`.

Design §4.9 "Starting and stopping" and "Home and reach". Two things that section describes are
deliberately not built here and say so rather than pretending: a member that is `{team: <name>}` (a
nested team) is refused with its name, and a repo whose checkout entry names a host the team is not
on is reported as out of reach.

A team lands on one host (design §4.4a "Teams across hosts", TD-057 step 4a): its definition's
`host:`, else the host the start runs on. Checkouts are resolved on *that* host; the roles and
briefs are read from the checkout's path here when it is a directory here (this host, or a
container node sharing the path), and otherwise from the checkout on that host, through `files`
— the home's `host_files`, a read across the link (step 4b.3) — by the same loader.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentorc import org as orgmod
from agentorc import profiles, repoconfig
from sessionorc import naming

WRAPUP_PROMPT = (
    "agentorc: this session is being wrapped up. Stop starting new work now. Commit and push whatever "
    "is in flight, make sure the ledger and user_attention.md reflect any undone steps (ledger before "
    "idle), then stop."
)
"""The one wrap-up text (design §4.5a **Wrap up**, §4.9 `ao team stop`). The UI imports it from here,
so the card and the CLI send the same words — one code path, not two."""

PAUSE_PROMPT = (
    "agentorc: pause — the usage gate's line for your profile was crossed. Finish the step in hand, commit "
    "and push what you have, then stop and wait for a resume; do not start anything new."
)
RESUME_PROMPT = "agentorc: resume — the usage gate's line is clear again. Carry on from where you paused."
"""The usage gate's two texts (design §6 *Usage gate*, TD-100): set on the record at create, beside
the wrap-up's, because the host agent types them and `sessionorc` must not know what a brief says."""


def gate_prompts(unattended: bool) -> dict[str, str]:
    """The `create` arguments that let the usage gate pause and resume this session — an unattended
    one only: the gate never touches an interactive session (§6), so it gets nothing to type."""
    return {"pause_prompt": PAUSE_PROMPT, "resume_prompt": RESUME_PROMPT} if unattended else {}


REACH_NOTE = (
    "These checkouts exist on this host and that is the whole of the reach: no credential and no "
    "permission comes with it. Your home is the one marked above — the anchor rule holds there — "
    "and you never work in another session's worktree."
)


# A brief describes the **job**, not the night it was written (design §4.9 `brief:`, TD-042). The
# run-specific facts come from the definition or the record — the lane from `--lane` or `lane:`, the
# members from `ao status -v`, the stop from the usage gate or the lead — so a brief that names one
# run cannot start the next. The two markers below are what the real failures carried, every time:
# `started 2026-09-11 17:15 MDT (run 5 …)` and `Stop … at 05:30 MDT (11:30 UTC) on 2026-09-12`. A
# bare date is deliberately *not* a marker — briefs cite dated ADRs and say what was true on a day,
# and warning about those would teach the reader to ignore the warning.
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b")
# `pdm run test 2>&1` is a command, not a fifth run: a digit that opens a shell redirect is not one.
_RUN = re.compile(r"\bruns?\s+\d+\b(?!\s*[>&|])", re.I)
_FENCE = re.compile(r"^\s*(```|~~~)")


def unrepeatable(text: str) -> list[str]:
    """The lines in a brief that tie it to one run, as findings a person can act on.

    A warning, never an error: a brief is prose and the judgement is the author's. `ao team start`
    says it once and starts the team anyway — the alternative, refusing, would make a team
    unstartable over a sentence."""
    found, fenced = [], False
    lines = text.split("\n")
    # An unterminated fence would put every line after it out of reach, so a brief with an unclosed
    # ``` would be silently unchecked (review of PR #139). An odd number of fence lines means the
    # file does not close what it opens; check the whole text rather than trusting the toggle.
    if sum(1 for line in lines if _FENCE.match(line)) % 2:
        fenced = None  # skip the fence logic entirely: check every line
    for n, line in enumerate(lines, 1):
        if fenced is not None:
            if _FENCE.match(line):  # an example command may legitimately show a clock time
                fenced = not fenced
                continue
            if fenced:
                continue
        for what, hit in (("a clock time", _CLOCK.search(line)), ("a run number", _RUN.search(line))):
            if hit:
                found.append(f"line {n}: {what} ({hit.group(0)!r}) — {line.strip()[:80]}")
    return found


class TeamError(Exception):
    """A definition that cannot start: the message names the thing that stopped it."""


@dataclass
class Launch:
    """One session a team start creates, with everything `create` needs but the controllers."""

    name: str
    role: str
    home: str  # the repo name in the team's projects
    dir: Path  # its checkout on this host; the session runs in a worktree of it
    team: str
    project: str  # the badge: the project the home repo came from
    profile: str = ""
    prompt: str | None = None
    grants: list[str] = field(default_factory=list)
    lane: list[str] = field(default_factory=list)
    unattended: bool = True
    lead: bool = False
    seat: bool = False  # a seat of the team: its techlead, or one with a trigger (§4.9b)
    ledger: str | None = None
    host: str = ""  # the host this session lands on; "" is the host the start runs on

    def create_params(self, controllers: list[str]) -> dict[str, Any]:
        """The `create` RPC's arguments. `worktree=name` is §4.9 "Home and reach": every team
        session lives in `<repo>/.claude/worktrees/<name>`, so the main checkout stays the
        person's and the anchor rule (§9 invariant 2) holds per member without anyone counting.
        `host` is sent only when the session lands elsewhere (§4.4, a client never sends a
        parameter it has not set): the home routes the create to that node."""
        return {
            **({"host": self.host} if self.host else {}),
            "name": self.name,
            "dir": str(self.dir),
            "adapter": repoconfig.DEFAULT_ADAPTER,
            "repo": str(self.dir),
            "worktree": self.name,
            "unattended": self.unattended,
            "resume": None,
            "profile": self.profile,
            "prompt": self.prompt,
            "capabilities": list(self.grants),
            "controllers": list(controllers),
            "lane": list(self.lane),
            "role": self.role,
            "ledger": self.ledger,
            "team": self.team,
            "project": self.project,
            **gate_prompts(self.unattended),
        }


@dataclass
class Plan:
    """What `ao team start` would do: the lead (None when a person leads), the techlead seat (None
    when the definition has none) and the members in order."""

    team: str
    source: Path | None = None
    lead: Launch | None = None
    techlead: Launch | None = None
    techlead_id: str = ""  # the id the seat will take, which every brief's `{techlead}` names
    seats: list[Launch] = field(default_factory=list)  # seats with a trigger (§4.9b, TD-098)
    members: list[Launch] = field(default_factory=list)
    host: str = ""  # where the team lands when that is not the host the start runs on (§4.4a)
    warnings: list[str] = field(default_factory=list)
    """Briefs that name one run (TD-042). Said out loud, like `out_of_reach`; never a refusal."""
    notes: list[str] = field(default_factory=list)
    """Other things the start says and starts anyway: a techlead seat without its primer (§4.9b)."""

    @property
    def launches(self) -> list[Launch]:
        """In creation order: the manager, the seats (their controller is the manager, §4.9b), the members."""
        return (
            ([self.lead] if self.lead else [])
            + ([self.techlead] if self.techlead else [])
            + list(self.seats)
            + list(self.members)
        )


def find(org: orgmod.Org, name: str) -> orgmod.TeamDef:
    try:
        return org.teams[name]
    except KeyError:
        known = ", ".join(sorted(org.teams)) or "none defined"
        raise TeamError(f"unknown team {name!r}; defined: {known}") from None


def project_of(org: orgmod.Org, projects: list[str], repo: str) -> str:
    """The badge a session whose home is `repo` carries: the first project that lists that repo."""
    for pname in projects:
        if repo in (org.projects.get(pname) or orgmod.Project(pname)).repos:
            return pname
    return ""


def project_block(org: orgmod.Org, projects: list[str], host: str, home: str = "") -> str:
    """The **Project** block that prefixes a brief when the reach is more than one repo (§4.9 "Home
    and reach"): each repo's checkout on this host and which one is home. Reach is that and nothing
    else. Empty when one repo (or none) is in reach, which is why a one-repo team's brief is
    untouched."""
    repos: dict[str, dict[str, Path]] = {}
    for pname in projects:
        for rname, by_host in (org.projects.get(pname) or orgmod.Project(pname)).repos.items():
            repos.setdefault(rname, by_host)
    if len(repos) < 2:
        return ""
    lines = [f"## Project: {', '.join(projects)}", ""]
    for rname, by_host in repos.items():
        path = by_host.get(host)
        if path is None:
            # §4.9: an entry for another host is noted, not an error — phase 2's transport reaches it
            elsewhere = ", ".join(sorted(by_host)) or "nowhere"
            lines.append(f"- {rname}: not checked out on {host} (declared on {elsewhere}) — out of reach")
        else:
            lines.append(f"- {rname}: {path}" + ("  — your home" if rname == home else ""))
    return "\n".join([*lines, "", REACH_NOTE, "", ""])


def reach_block(org: orgmod.Org, project: str, here: Path | str, host: str) -> tuple[str, str]:
    """The Project block a *hand-started* session gets from `ao new --project` and the New session
    form's **Project** picker (design §4.9 "Home and reach"): the same block a team member gets,
    with home taken to be whichever of the project's repos `here` is. Returns `(block, note)`.

    An undefined project name is not a refusal: the badge is a plain string and nothing keys on it
    (§9 invariant 9), so the session is still badged and the note says there is no reach to
    describe. A one-repo project has no block either — there is nothing to name."""
    if not project:
        return "", ""
    if project not in org.projects:
        known = ", ".join(sorted(org.projects)) or "none"
        return "", f"project: {project!r} is not defined in {org.path} ({known}) — badge only, no reach block"
    where = Path(here).expanduser().resolve()
    home = next(
        (
            r
            for r, by_host in org.projects[project].repos.items()
            if (p := by_host.get(host)) and Path(p).expanduser().resolve() == where
        ),
        "",  # started outside the project's repos: the block still names them, nothing is home
    )
    return project_block(org, [project], host, home), ""


# A lead, a member and the techlead seat are shaped alike where a launch is concerned (§4.9): each
# names a lane, a brief override, grants, a profile and whether it is unattended. `None` is the
# person-lead case.
Spec = orgmod.MemberDef | orgmod.ManagerDef | orgmod.TechleadDef | orgmod.SeatDef | None


# `(host, checkout, [paths relative to it])` → `{path: text, or None when there is no such file}`:
# another host's checkout, read there (the home's `host_files`). Raises `OSError` for a failure.
Files = Callable[[str, str, list[str]], dict[str, "str | None"]]


def _reader_on(files: Files, host: str, checkout: Path) -> repoconfig.Reader:
    """A `repoconfig.Reader` for a checkout on another host: only its own files, by their path
    relative to it — a brief outside the checkout is not read across the link at all."""

    def read(path: Path) -> str | None:
        try:
            rel = str(Path(path).relative_to(checkout))
        except ValueError:
            raise OSError(
                f"{path} is outside the checkout {checkout}: only a checkout's own files are read on {host}"
            ) from None
        return files(host, str(checkout), [rel]).get(rel)

    return read


def _brief(
    role: repoconfig.Role,
    member: Spec,
    checkout: Path,
    lane: list[str],
    read: repoconfig.Reader | None = None,
    techlead: str = "",
    context: str = "",
) -> str | None:
    """The role's template with `{lane}`, `{techlead}` and `{context}` filled, or the member's
    `brief:` override read from its home checkout. A lead may override its brief too — a lead's is
    the one a repo most often keeps its own copy of (2026-09-13)."""
    if member is not None and member.brief:
        override = repoconfig.Role(name=role.name, brief=member.brief, brief_source="repo", root=checkout)
        return override.brief_text(lane, read=read, techlead=techlead, context=context)
    return role.brief_text(lane, read=read, techlead=techlead, context=context)


def seat_id(org: orgmod.Org, team: orgmod.TeamDef, host: str, here: str) -> str:
    """The id the team's techlead will take (design §4.9b `{techlead}`), known before anything is
    created so the manager's brief — the first session started — can already name it: §4.1's
    `ao-<scope>-<name>` from its checkout, qualified `@<host>` when the team lands on another
    host, as the home addresses that host's records. "" without a seat, or without a checkout
    (the seat's own launch then refuses the start, naming it)."""
    if team.techlead is None:
        return ""
    checkout = org.checkout(project_of(org, team.projects, team.techlead.home), team.techlead.home, host)
    if checkout is None:
        return ""
    checkout = Path(checkout).expanduser()
    sid = naming.base_id(checkout, str(checkout), team.techlead.name)
    return sid if host == here else f"{sid}@{host}"


def _primer_missing(seat: orgmod.TechleadDef, checkout: Path, host: str, here: str, files: Files | None) -> str:
    """Design §4.9b *Its standing context*: a techlead seat with no `context:`, or one naming a
    file its home checkout does not hold, starts cold and answers narrowly — said at the start,
    which goes ahead (a primer helps; it is never what an answer rests on). "" when it is there,
    and when it cannot be looked for (another host with nothing here to read it)."""
    if not seat.context:
        return (
            f"{seat.name}: the techlead seat has no `context:` — it will start from the repo's own map "
            "(CLAUDE.md, the design's headings); write the repo's primer and name it (design §4.9b, `ao team --skill`)"
        )
    path = Path(seat.context).expanduser()
    if checkout.is_dir():
        found = (path if path.is_absolute() else checkout / path).is_file()
    elif files is not None and host != here and not path.is_absolute():
        try:
            found = _reader_on(files, host, checkout)(checkout / path) is not None
        except OSError:
            return ""  # the seat's own brief read the same checkout; a failure here is not the primer's
    else:
        return ""
    if found:
        return ""
    return f"{seat.name}: its `context:` {seat.context} is not in {checkout} — the seat will start without its primer"


def _launch(  # noqa: PLR0913 — every argument is a distinct part of one definition; one call site
    *,
    org: orgmod.Org,
    team: orgmod.TeamDef,
    name: str,
    role_name: str,
    home: str,
    host: str,
    here: str,
    profile_override: str | None,
    member: Spec,  # the lead's own definition or a member's: both carry lane, brief, grants, profile
    lead: bool,
    block: str,
    files: Files | None = None,
    techlead: str = "",
    context: str = "",
    seat: bool = False,
) -> Launch:
    where = f"team {team.name}: {name}"
    project = project_of(org, team.projects, home)
    checkout = org.checkout(project, home, host)
    if checkout is None:
        declared = ", ".join(sorted((org.projects[project].repos.get(home) or {}) if project in org.projects else []))
        raise TeamError(
            f"{where}: repo {home!r} has no checkout on {host} (declared on {declared or 'no host'}) — "
            f"add `{host}: <path>` under it in org.yml, or set the team's `host:`"
        )
    checkout = Path(checkout).expanduser()
    read: repoconfig.Reader | None = None  # this host's disk
    if not checkout.is_dir():
        if host == here:
            raise TeamError(f"{where}: {checkout} does not exist on {host} (repo {home!r}) — nothing was started")
        if files is None:
            raise TeamError(
                f"{where}: {checkout} is not a directory on {here}, and nothing here reads it on {host} "
                f"(repo {home!r}) — nothing was started"
            )
        # Another host's checkout that is not a directory here — a machine node (a container node
        # shares the path): its roles and briefs are read there, by the same loader (step 4b.3).
        read = _reader_on(files, host, checkout)
    try:
        cfg = repoconfig.load(checkout, read=read)
        role = repoconfig.resolve_role(cfg, role_name, org.roles)
    except (KeyError, ValueError, OSError) as e:
        raise TeamError(f"{where}: {str(e).strip(chr(34))}") from None
    if isinstance(member, orgmod.SeatDef):
        lane: list[str] = []  # a seat has no lane, whatever its role's (§4.9b): its area is its brief's
    else:
        lane = list(member.lane) if member is not None and member.lane else list(role.lane)
    grants = list(member.grants) if member is not None and member.grants is not None else list(role.grants)
    # Profile precedence (§4.9 "Roles gain a profile"), lowest first: the package's built-ins,
    # `org.yml`'s `roles:` and the repo's `.agentorc.yml` (those three inside `resolve_role`), the
    # member's own `profile`, then `--profile` on the command line.
    profile = profile_override or (member.profile if member is not None else None) or role.profile or ""
    if profile:
        try:
            profiles.get(profile)  # checked here so a typo stops the start rather than one session
        except (KeyError, ValueError) as e:
            raise TeamError(f"{where}: {str(e).strip(chr(34))}") from None
    try:
        prompt = _brief(role, member, checkout, lane, read, techlead, context)
    except ValueError as e:
        raise TeamError(f"{where}: {e}") from None
    if block:
        prompt = block + prompt if prompt else block
    return Launch(
        name=name,
        role=role.name,
        home=home,
        dir=checkout,
        team=team.name,
        project=project,
        profile=profile,
        prompt=prompt,
        grants=grants,
        lane=lane,
        unattended=member.unattended if member is not None else True,  # a lead may ask to be watched
        lead=lead,
        seat=seat,
        ledger=cfg.ledger,
        host=host if host != here else "",
    )


def plan(org: orgmod.Org, name: str, host: str, *, profile: str | None = None, files: Files | None = None) -> Plan:
    """Resolve a definition into the sessions it starts, checking everything that can be checked
    without the agent: the checkouts are declared on the team's host (and exist, when that is this
    one — `host` is the host the start runs on), every role, profile and brief resolves, and no
    member asks for something this phase does not build. Raises `TeamError` on the first thing
    that would have stopped the start — nothing is created here (§4.9: never half a team)."""
    team = find(org, name)
    here, host = host, team.host or host  # the team lands on its `host:`, else where the start runs (§4.4a)
    p = Plan(team=team.name, source=team.source, host=host if host != here else "")
    reach = bool(project_block(org, team.projects, host))
    p.techlead_id = tid = seat_id(org, team, host, here)
    ctx = (team.techlead.context or "") if team.techlead is not None else ""  # the primer (§4.9b)
    if team.manager.role != orgmod.PERSON:
        p.lead = _launch(
            org=org,
            team=team,
            name=team.manager.name,
            role_name=team.manager.role,
            home=team.manager.home,
            host=host,
            here=here,
            profile_override=profile or team.manager.profile,
            member=team.manager,  # its lane, brief, grants and unattended read like a member's
            lead=True,
            block=project_block(org, team.projects, host, team.manager.home) if reach else "",
            files=files,
            techlead=tid,
            context=ctx,
        )
    seen: set[str] = {p.lead.name} if p.lead else set()
    if team.techlead is not None:
        seat = team.techlead
        if seat.name in seen:
            raise TeamError(f"team {team.name}: two sessions would be called {seat.name!r} — a name is one session")
        seen.add(seat.name)
        p.techlead = _launch(
            org=org,
            team=team,
            name=seat.name,
            role_name=orgmod.TECHLEAD_ROLE,
            home=seat.home,
            host=host,
            here=here,
            profile_override=profile or seat.profile,
            member=seat,
            lead=False,
            block=project_block(org, team.projects, host, seat.home) if reach else "",
            files=files,
            techlead=tid,
            context=ctx,
            seat=True,
        )
        missing = _primer_missing(seat, p.techlead.dir, host, here, files)
        if missing:
            p.notes.append(missing)
    for trig in team.seats:
        # A seat with a trigger (§4.9b, TD-098): launched as the techlead is — its manager its
        # controller, no grants — and filled again by the manager when its trigger is met.
        if trig.name in seen:
            raise TeamError(f"team {team.name}: two sessions would be called {trig.name!r} — a name is one session")
        seen.add(trig.name)
        p.seats.append(
            _launch(
                org=org,
                team=team,
                name=trig.name,
                role_name=trig.role,
                home=trig.home,
                host=host,
                here=here,
                profile_override=profile or trig.profile,
                member=trig,
                lead=False,
                block=project_block(org, team.projects, host, trig.home) if reach else "",
                files=files,
                techlead=tid,
                context=ctx,
                seat=True,
            )
        )
    for member in team.members:
        if member.team is not None:
            raise TeamError(
                f"team {team.name}: member {{team: {member.team}}} is a nested team, which is not built yet "
                f"(design §4.9: the flat case ships first) — start it on its own with `ao team start {member.team}`"
            )
        block = project_block(org, team.projects, host, member.home) if reach else ""
        for mname in member.names():
            if mname in seen:
                raise TeamError(f"team {team.name}: two sessions would be called {mname!r} — a name is one session")
            seen.add(mname)
            p.members.append(
                _launch(
                    org=org,
                    team=team,
                    name=mname,
                    role_name=member.role,
                    home=member.home,
                    host=host,
                    here=here,
                    profile_override=profile,
                    member=member,
                    lead=False,
                    block=block,
                    files=files,
                    techlead=tid,
                    context=ctx,
                )
            )
    # One line per finding, not per session: a team's members share a brief, and four copies of
    # the same sentence is how a warning gets ignored.
    by_finding: dict[str, list[str]] = {}
    for x in p.launches:
        for finding in unrepeatable(x.prompt or ""):
            by_finding.setdefault(finding, []).append(x.name)
    for finding, who in by_finding.items():
        p.warnings.append(f"{', '.join(who)}: the brief names one run — {finding}")
    return p
