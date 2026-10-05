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

from agentorc import flowdefs, profiles, repoconfig
from agentorc import org as orgmod
from sessionorc import naming
from sessionorc.models import REVIEW_BOUND

# The one wrap-up text (design §4.5a **Wrap up**, §4.9 `ao team stop`), kept in `sessionorc.models` since the
# home sends it too (a sit-out, §4.9c). The UI imports it from here, so the card, the CLI and the home send
# the same words — one code path, not two.
from sessionorc.models import WRAPUP_PROMPT as WRAPUP_PROMPT

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
    adapter: str = (
        profiles.DEFAULT_ADAPTER
    )  # the profile's own tool (§4.2a): a record never names one it is not running
    prompt: str | None = None
    grants: list[str] = field(default_factory=list)
    lane: list[str] = field(default_factory=list)
    unattended: bool = True
    lead: bool = False
    seat: bool = False  # a seat of the team: its techlead, or one with a trigger (§4.9b)
    trigger: dict[str, str] | None = None  # a seat's `{trigger, after?}`, written on its record (§6, TD-103)
    ledger: str | None = None
    host: str = ""  # the host this session lands on; "" is the host the start runs on
    review: dict[str, Any] | None = None  # who reads its PRs, from its role (design §4.9b *The reader*)
    context_bound: int | None = None  # tokens past which rule 5 tells it to end its run (§4.8, TD-190)
    prompt_from: dict[str, Any] | None = None  # what `prompt` was made from (§6 rule 7, TD-217)
    set_aside: bool = False  # its role's own `review:` was set aside under the team's flow (§4.9c item 2)

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
            "adapter": self.adapter,
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
            # every session a team start creates is one someone chose to keep running (§6, TD-103)
            "supervised": True,
            # a seat's ending is its own: the record says it is one, so no crash restart acts on it
            **({"seat": dict(self.trigger)} if self.trigger else {}),
            **({"review": dict(self.review)} if self.review else {}),
            **({"context_bound": self.context_bound} if self.context_bound else {}),
            **({"prompt_from": self.prompt_from} if self.prompt_from else {}),
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
    manager_id: str = ""  # the id the manager will take, which every brief's `{manager}` names
    seats: list[Launch] = field(default_factory=list)  # seats with a trigger (§4.9b, TD-098)
    members: list[Launch] = field(default_factory=list)
    host: str = ""  # where the team lands when that is not the host the start runs on (§4.4a)
    warnings: list[str] = field(default_factory=list)
    """Briefs that name one run (TD-042). Said out loud, like `out_of_reach`; never a refusal."""
    notes: list[str] = field(default_factory=list)
    """Other things the start says and starts anyway: a techlead seat without its primer (§4.9b)."""
    flow: str | None = None  # the flow the team runs now, compiled into the launches (§4.9c); None: no flow
    sit_out: list[str] = field(default_factory=list)
    """Members not started: their role is a stage of another listed flow and of none of the current one (§4.9c)."""

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
        if name in org.refused:  # two repos define it (§4.9 *Names are the org's*): say so, not "unknown"
            raise TeamError(org.refused[name]) from None
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


def reader_on(files: Files, host: str, checkout: Path) -> repoconfig.Reader:
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
    lane: list[str],
    read: repoconfig.Reader | None = None,
    techlead: str = "",
    context: str = "",
    manager: str = "",
    flow: repoconfig.UnderFlow | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """The role's template with the member's `brief:` in its `{repo}` slot — in place of the role's
    own `roles.<name>.brief`, never beside it (design §4.8, TD-114) — and `{lane}`, `{techlead}`,
    `{manager}` and `{context}` filled. The `brief:` is read from the home checkout, where `role`
    was resolved. A manager on call takes the template's seat shape (§6 rule 3, TD-259). Beside it,
    what it was made from (`Role.compose`, §6 rule 7)."""
    supplement = member.brief if member is not None and member.brief else None
    return role.compose(
        lane,
        read=read,
        techlead=techlead,
        context=context,
        manager=manager,
        supplement=supplement,
        on_call=isinstance(member, orgmod.ManagerDef) and member.on_call,
        flow=flow,
    )


def _session_id(org: orgmod.Org, team: orgmod.TeamDef, name: str, home: str, host: str, here: str) -> str:
    """The id a team's session will take, known before anything is created: §4.1's
    `ao-<scope>-<name>` from its checkout, qualified `@<host>` when the team lands on another
    host, as the home addresses that host's records. "" without a checkout (the session's own
    launch then refuses the start, naming it)."""
    checkout = org.checkout(project_of(org, team.projects, home), home, host)
    if checkout is None:
        return ""
    checkout = Path(checkout).expanduser()
    sid = naming.base_id(checkout, str(checkout), name)
    return sid if host == here else f"{sid}@{host}"


def seat_id(org: orgmod.Org, team: orgmod.TeamDef, host: str, here: str) -> str:
    """The id the team's techlead will take (design §4.9b `{techlead}`), known before anything is
    created so the manager's brief — the first session started — can already name it. "" without
    a seat, or without a checkout."""
    if team.techlead is None:
        return ""
    return _session_id(org, team, team.techlead.name, team.techlead.home, host, here)


def manager_id(org: orgmod.Org, team: orgmod.TeamDef, host: str, here: str) -> str:
    """The id the team's manager will take (`{manager}`, TD-113 (a)), which every member's brief
    names as the one its `done` lines go to. "" when a person leads the team."""
    if team.manager.role == orgmod.PERSON:
        return ""
    return _session_id(org, team, team.manager.name, team.manager.home, host, here)


def current_flow(team: orgmod.TeamDef) -> str | None:
    """The flow `team` runs now (design §4.9c *A team lists its flows*): the person's pick,
    `teams.<team>.flow` (`orgmod.with_settings` puts it on the team), where the team lists it; else the
    first of `flows:` — or None for a team with no flow."""
    if not team.flows:
        return None
    return team.flow if team.flow in team.flows else team.flows[0]


def flow_unlisted(team: orgmod.TeamDef) -> str:
    """What the card and `ao team flow` say when the setting names a flow the definition no longer
    lists (§4.9c: it *reads as the first, said on the card*), or ""."""
    if not team.flow or team.flow in team.flows:
        return ""
    if not team.flows:
        return f"teams.{team.name}.flow is {team.flow}, and {team.name} lists no flows: — it runs none"
    return f"teams.{team.name}.flow is {team.flow}, which {team.name} does not list — it runs {team.flows[0]}"


def flow_rows(
    org: orgmod.Org, team: orgmod.TeamDef, host: str, here: str, files: Files | None = None
) -> list[dict[str, Any]]:
    """Each flow `team` lists, in order, for `ao team flow <team>` and `ao team list` (design §4.7,
    §4.9c): `{name, current, strip, cannot}` — `strip` the stages and the person, as the card's flow
    strip reads (*design → build → review → you, through techlead-ao-1*), and `cannot` why the team
    cannot use it (not found, not usable, cannot be followed), "" when it can. A team whose repo
    cannot be read from here (a node's checkout with no `files` to reach it) is not judged: each row
    carries `unread`, the reason, and no `cannot`."""
    if not team.flows:
        return []
    now = current_flow(team)
    techlead = seat_id(org, team, team.host or here, here) if team.techlead is not None else ""
    try:
        checkout, read = _checkout(org, team, org.team_repos(team)[0], host, here, files, f"team {team.name}")
        cfg = repoconfig.load(checkout, read=read)
    except (TeamError, ValueError, OSError) as e:
        why = str(e).strip(chr(34))
        return [{"name": n, "current": n == now, "strip": "", "cannot": "", "unread": why} for n in team.flows]
    staffed = {m.role for m in team.members if m.team is None and m.role}
    out: list[dict[str, Any]] = []
    for name in team.flows:
        row: dict[str, Any] = {"name": name, "current": name == now, "strip": "", "cannot": ""}
        try:
            flow = flowdefs.load(name, cfg, org.roles, read=read)
        except (OSError, ValueError) as e:
            flow, row["cannot"] = None, str(e).strip(chr(34))
        if flow is None:
            row["cannot"] = row["cannot"] or f"no flow {name!r}"
        elif flow.problems:
            row["cannot"] = f"not usable — {'; '.join(flow.problems)}"
        else:
            row["strip"] = flowdefs.strip(flow, techlead=techlead)
            if reasons := flowdefs.unfollowable(
                flow,
                staffed,
                techlead=team.techlead is not None,
                held=cfg.held or (),
                team=team.name,
                node=host if host != here else "",
            ):
                row["cannot"] = flowdefs.cannot_follow(name, team.name, reasons)
        out.append(row)
    return out


def flow_for(
    org: orgmod.Org,
    team: orgmod.TeamDef,
    cfg: repoconfig.RepoConfig,
    role: str | None,
    techlead: str = "",
    *,
    read: repoconfig.Reader | None = None,
) -> repoconfig.UnderFlow | None:
    """What a session of `role` in `team` is told of the team's current flow (§4.9c item 5): its
    `{flow}` line and its stage brief, for `Role.compose(flow=…)`. None for a team with no flow, and
    for one whose current flow is not there, cannot be read or is not usable — the start refuses such a team; a single
    session started into it composes as with no flow. `cfg` is the team's repo, whose `held:` the
    review stage names."""
    name = current_flow(team)
    try:
        flow = flowdefs.load(name, cfg, org.roles, read=read) if name else None
    except OSError:  # a node's checkout that did not answer: no flow read, never a crash past the form
        return None
    if flow is None or not flow.usable:
        return None
    if read is not None and flow.place == "org":  # another host's team: an org flow is not followable there
        return None
    return flowdefs.under(flow, role, techlead=techlead, held=cfg.held or ())


def brief_ids(
    org: orgmod.Org,
    name: str,
    here: str,
    role: str | None = None,
    cfg: repoconfig.RepoConfig | None = None,
    *,
    read: repoconfig.Reader | None = None,
) -> dict[str, Any]:
    """What a brief's `{techlead}`, `{manager}` and `{context}` slots take for a session started
    into the team `name` outside a team start — `ao new --team` and the New session form's Team
    pick (design §4.9 *A person in the team*, TD-253) — as `plan` fills them for its members: the
    ids the seat and the manager take (`seat_id`, `manager_id`) and the seat's primer. Empty for a
    team the org does not define, which stays a badge, so each slot reads `none`. With the `role`
    and the repo's `cfg`, and a team that lists flows, `flow` too: what the member is told of its
    team's current flow (`flow_for`), so `{flow}` and `{stage}` read as a start's would."""
    team = org.teams.get(name)
    if team is None:
        return {}
    host = team.host or here
    ids: dict[str, Any] = {
        "techlead": seat_id(org, team, host, here),
        "manager": manager_id(org, team, host, here),
        "context": (team.techlead.context or "") if team.techlead is not None else "",
    }
    # the team's current flow (§4.9c, TD-309 slice 2a), where the caller names the role and its repo
    if role and cfg is not None and (flow := flow_for(org, team, cfg, role, ids["techlead"], read=read)):
        ids["flow"] = flow
    return ids


def entry_teams(org: orgmod.Org, root: str, host: str) -> list[dict[str, str]]:
    """The teams that service the checkout `root` on `host`, in definition order, each `{team,
    seat, name}` — `seat` the id its techlead takes (`teams.seat_id`), empty where it defines none,
    and `name` that seat's name — which is what `entry_add` is handed by the
    Add entry form and `ao td add` (TD-218: the host agent does
    not read `org.yml`). The first is the one **Hand to the techlead** hands to, as `repo_teams`
    gives a repo its first team's badge."""
    out = []
    for tname, t in org.teams.items():
        paths = {
            str(Path(path).expanduser().resolve())
            for pname in t.projects
            for by in (org.projects[pname].repos.values() if pname in org.projects else ())
            if (path := by.get(host))
        }
        if root in paths:
            seat = seat_id(org, t, t.host or host, host)
            out.append(
                {
                    "team": tname,
                    "seat": seat,
                    "name": t.techlead.name if seat and t.techlead else "",
                    # a seat the team defines whose home has no checkout on its host: `seat_id` is ""
                    # for it as for no seat at all, and the reason under the button tells them apart
                    "techlead": t.techlead.name if t.techlead else "",
                }
            )
    return out


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
            found = reader_on(files, host, checkout)(checkout / path) is not None
        except OSError:
            return ""  # the seat's own brief read the same checkout; a failure here is not the primer's
    else:
        return ""
    if found:
        return ""
    return f"{seat.name}: its `context:` {seat.context} is not in {checkout} — the seat will start without its primer"


def _checkout(
    org: orgmod.Org, team: orgmod.TeamDef, home: str, host: str, here: str, files: Files | None, where: str
) -> tuple[Path, repoconfig.Reader | None]:
    """Where repo `home` is checked out on `host`, and how to read it: this host's disk (None), or
    the node's files across the link for a checkout that is not a directory here (step 4b.3)."""
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
        read = reader_on(files, host, checkout)
    return checkout, read


def _check_flows(org: orgmod.Org, team: orgmod.TeamDef, host: str, here: str, files: Files | None) -> None:
    """A team's `flows:` and `entries:` against its repo (design §4.9c): each listed flow is one that
    exists, is **usable** and that the team can **follow**, and each `entries:` role is a worker or a
    seat — else the start is refused in the shared words, and `ao org check` says so (it runs
    `plan`). A team with neither key is not read here at all."""
    if not team.flows and not team.entries:
        return
    repos = org.team_repos(team)
    home = repos[0]  # every team today is one repo (§4.9c item 2: the repos' `held:` is their union)
    where = f"team {team.name}"
    checkout, read = _checkout(org, team, home, host, here, files, where)
    try:
        cfg = repoconfig.load(checkout, read=read)
    except (ValueError, OSError) as e:
        raise TeamError(f"{where}: {str(e).strip(chr(34))}") from None
    for t, rname in team.entries.items():
        try:
            kind = repoconfig.resolve_role(cfg, rname, org.roles).kind
        except (KeyError, ValueError):
            continue  # an unknown role is `org._entries_resolve`'s line, said where the file is read
        if kind not in ("worker", "seat"):
            raise TeamError(f"{where}: entries.{t} takes a worker or a seat, and {rname!r} is a {kind} (design §4.9c)")
    staffed = {m.role for m in team.members if m.team is None and m.role}
    for name in team.flows:
        try:
            flow = flowdefs.load(name, cfg, org.roles, read=read)
        except (OSError, ValueError) as e:  # its flow.yml across the link, unreadable
            raise TeamError(f"{where}: flows: {name}: {str(e).strip(chr(34))}") from None
        if flow is None:
            raise TeamError(
                f"{where}: flows: no flow {name!r} — not a built-in ({', '.join(flowdefs.BUILTIN)}), no "
                f"{flowdefs.org_dir() / name}/ and no {flowdefs.REPO_DIR / name}/ in {home}"
            )
        if flow.problems:
            raise TeamError(f"{where}: flows: {name} is not usable — {'; '.join(flow.problems)}")
        reasons = flowdefs.unfollowable(
            flow,
            staffed,
            techlead=team.techlead is not None,
            held=cfg.held or (),
            team=team.name,
            node=host if host != here else "",
        )
        if reasons:
            raise TeamError(flowdefs.cannot_follow(name, team.name, reasons))


@dataclass
class Compiled:
    """A team's current flow as a start compiles it (design §4.9c *What a flow compiles to*): the
    flow, the union of its repos' `held:`, and every listed flow read, for the sit-outs."""

    flow: flowdefs.Flow
    held: list[str]
    listed: list[flowdefs.Flow]
    techlead: bool  # the team has a `techlead:` seat
    cfg: repoconfig.RepoConfig | None = None  # the home repo's file, for what `flow_redundant` reads

    @property
    def reader(self) -> dict[str, Any] | None:
        """The reader a member stage's role gets (item 2): the techlead on the repos' `held:` when the
        flow holds a review stage the team can staff — never a `review:` with no `held:`, which would
        hold every path — else None."""
        if self.flow.review_stage is None or not self.techlead or not self.held:
            return None
        return {"reader": "techlead", "held": sorted(self.held), "bound": REVIEW_BOUND}

    def sits_out(self, role: str) -> bool:
        """A member of `role` sits out: its role is a member stage's of another listed flow and of no
        stage of the current one (§4.9c *Members the flow does not use sit out*)."""
        if self.flow.stage_of(role) is not None:
            return False
        return any(f.stage_of(role) is not None for f in self.listed if f.name != self.flow.name)


def compiled(org: orgmod.Org, team: orgmod.TeamDef, host: str, here: str, files: Files | None) -> Compiled | None:
    """The team's current flow, read against its home repo, or None: a team with no `flows:`, or one
    whose current flow is missing, unreadable or not usable — which `_check_flows` refuses in its
    own words, so nothing here raises."""
    name = current_flow(team)
    if name is None:
        return None
    try:
        checkout, read = _checkout(org, team, org.team_repos(team)[0], host, here, files, f"team {team.name}")
        cfg = repoconfig.load(checkout, read=read)
        # a listed flow that does not load counts for no sit-out: `_check_flows` refuses the start anyway
        listed = [f for n in team.flows if (f := flowdefs.load(n, cfg, org.roles, read=read)) is not None]
    except (TeamError, ValueError, OSError):
        return None
    flow = next((f for f in listed if f.name == name), None)
    if flow is None or not flow.usable:
        return None
    return Compiled(flow=flow, held=list(cfg.held or ()), listed=listed, techlead=team.techlead is not None, cfg=cfg)


def flow_needs(org: orgmod.Org, team: orgmod.TeamDef, role: str, host: str, here: str, files: Files | None) -> str:
    """The first listed flow with a member stage of `role`, or "" — what **Members…** names when it
    refuses to remove that role's last member (design §4.9c *Every listed flow must be followable*).
    A flow that cannot be read needs nothing here: the start says what is wrong with it."""
    if not team.flows:
        return ""
    try:
        checkout, read = _checkout(org, team, org.team_repos(team)[0], host, here, files, f"team {team.name}")
        cfg = repoconfig.load(checkout, read=read)
        for name in team.flows:
            flow = flowdefs.load(name, cfg, org.roles, read=read)
            stage = flow.stage_of(role) if flow is not None else None
            if stage is not None and not stage.review:
                return name
    except (TeamError, ValueError, OSError):
        return ""
    return ""


def entry_role(
    org: orgmod.Org, team: orgmod.TeamDef, type_: str, host: str, here: str, files: Files | None = None
) -> str:
    """The role **Open a session** starts for `type_` (§4.9, §4.9c item 4): the team's `entries:`
    word, else, for a feature, the role of the current flow's stage whose lane holds `design-first`,
    else the techlead (`TeamDef.entry_role`)."""
    if type_ == "feature" and type_ not in team.entries:
        under = compiled(org, team, host, here, files)
        stage = next((st for st in under.flow.stages if "design-first" in st.lane), None) if under else None
        if stage is not None:
            return stage.role
    return team.entry_role(type_)


def flow_redundant(
    org: orgmod.Org, team: orgmod.TeamDef, host: str, here: str, files: Files | None = None
) -> list[str]:
    """What `ao org check` warns of, per team, under its current flow (design §4.9c *What is shown*):
    each key written that the flow would fill with the same value — what a repo may delete. A
    member's `lane:` equal to its stage's, compared in its written order (its pick order);
    `entries.feature` equal to the role of the stage whose lane holds `design-first` (item 4); and a
    member stage's role's `review:` in the repo's own `roles:` equal to the reader the flow gives
    (item 2), its `held` compared as a set. A team with no usable current flow warns of nothing."""
    under = compiled(org, team, host, here, files)
    if under is None:
        return []
    flow, out = under.flow, []
    src = team.source.name if team.source else "its definition"
    for m in team.members:
        stage = flow.stage_of(m.role) if m.team is None and m.lane else None
        if stage is not None and not stage.review and m.lane == stage.lane:
            out.append(
                f"team {team.name}: {m.name}'s lane: {', '.join(m.lane)} is what flow {flow.name} fills — "
                f"{src} may drop it"
            )
    design = next((st for st in flow.stages if "design-first" in st.lane), None)
    if design is not None and team.entries.get("feature") == design.role:
        out.append(
            f"team {team.name}: entries.feature: {design.role} is what flow {flow.name} fills — {src} may drop it"
        )
    reader, cfg = under.reader, under.cfg
    if reader is None or cfg is None:
        return out
    roles = dict.fromkeys(m.role for m in team.members if m.team is None and m.role)
    for role in roles:
        stage = flow.stage_of(role)
        review = (cfg.roles.get(role) or {}).get("review")
        if stage is None or stage.review or not review:
            continue
        same = review.get("reader") == reader["reader"] and review.get("bound", REVIEW_BOUND) == reader["bound"]
        if same and set(review.get("held") or ()) == set(reader["held"]):
            where = cfg.path.name if cfg.path else repoconfig.FILE
            out.append(
                f"team {team.name}: {role}'s review: in {where} is the reader flow {flow.name} gives — "
                f"the repo may drop it (a session outside a flow still reads it)"
            )
    return out


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
    manager: str = "",
    seat: bool = False,
    under: Compiled | None = None,
) -> Launch:
    where = f"team {team.name}: {name}"
    checkout, read = _checkout(org, team, home, host, here, files, where)
    try:
        cfg = repoconfig.load(checkout, read=read)
        role = repoconfig.resolve_role(cfg, role_name, org.roles)
    except (KeyError, ValueError, OSError) as e:
        raise TeamError(f"{where}: {str(e).strip(chr(34))}") from None
    # §4.9c *Roles and flows are orthogonal* (TD-309): a slot takes the kind it names — `manager:` a
    # manager role, a member a worker, the techlead seat and each `seats:` entry a seat
    want = "manager" if lead else "seat" if seat else "worker"
    if role.kind != want:
        slot = "manager:" if lead else "a seat" if seat else "a member"
        raise TeamError(f"{where}: {slot} takes a {want} role, and {role_name!r} is a {role.kind} (design §4.9c)")
    # §4.9c item 1: a member's lane is its own `lane:`, else its stage's, else its role's
    stage = under.flow.stage_of(role.name) if under is not None else None
    if isinstance(member, orgmod.SeatDef):
        lane: list[str] = []  # a seat has no lane, whatever its role's (§4.9b): its area is its brief's
    elif member is not None and member.lane:
        lane = list(member.lane)
    else:
        lane = list(stage.lane) if stage is not None and stage.lane else list(role.lane)
    # §4.9c item 2: under a flow every role's own `review:` is set aside, and a member stage's role
    # takes the flow's reader; a seat, the manager and a member outside the flows take none
    review = dict(role.review) if role.review else None
    if under is not None:
        member_stage = stage is not None and not stage.review and not lead and not seat
        review = under.reader if member_stage else None
    told = flowdefs.under(under.flow, role.name, techlead=techlead, held=under.held) if under is not None else None
    grants = list(member.grants) if member is not None and member.grants is not None else list(role.grants)
    # Profile precedence (§4.9 "Roles gain a profile"), lowest first: the package's built-ins,
    # `org.yml`'s `roles:` and the repo's `.agentorc.yml` (those three inside `resolve_role`), the
    # member's own `profile`, then `--profile` on the command line.
    profile = profile_override or (member.profile if member is not None else None) or role.profile or ""
    adapter = profiles.DEFAULT_ADAPTER
    if profile:
        try:
            adapter = profiles.get(profile).adapter  # checked here so a typo stops the start rather than one session
        except (KeyError, ValueError) as e:
            raise TeamError(f"{where}: {str(e).strip(chr(34))}") from None
        if adapter == profiles.SHELL_ADAPTER:
            # a team session runs a brief, which a shell cannot (§4.9, TD-329); "an agent's" is §4.1's
            # test — its adapter is not the shell's — whatever the role
            raise TeamError(
                f"{where}: profile {profile!r} runs a shell, and a team session is an agent's (design §4.9)"
            )
    try:
        prompt, prompt_from = _brief(role, member, lane, read, techlead, context, manager, told)
    except ValueError as e:
        raise TeamError(f"{where}: {e}") from None
    if block:
        prompt = block + prompt if prompt else block
        prompt_from = repoconfig.prefixed(prompt_from, block)
    return Launch(
        name=name,
        role=role.name,
        home=home,
        dir=checkout,
        team=team.name,
        project=project_of(org, team.projects, home),
        profile=profile,
        adapter=adapter,
        prompt=prompt,
        grants=grants,
        lane=lane,
        unattended=member.unattended if member is not None else True,  # a lead may ask to be watched
        lead=lead,
        seat=seat,
        trigger=_trigger(member) if seat or (lead and member.on_call) else None,
        ledger=cfg.ledger,
        host=host if host != here else "",
        review=review,
        # a seat takes no default bound (§4.8 *The bound has two layers*): only one its role's definition wrote
        context_bound=None if seat and role.context_default else role.context_bound,
        prompt_from=prompt_from,
        set_aside=under is not None and bool(role.review),
    )


def team_review(team: orgmod.TeamDef, roles: dict[str, Any]) -> dict[str, Any] | None:
    """The reader a person's session in the team takes when its role carries no `review:` of its own
    (design §4.9 *A person in the team*, TD-160): `{reader: techlead, held, bound}` when the
    definition holds a techlead seat — `held` the union of the `held:` lists of the team's member
    roles, so a person is held to the paths its workers are held to, no more — else None. `roles`
    maps a member role's name to its resolved role (anything with `.review`); a role it lacks, or
    one with no `review:`, adds nothing, and a team whose members hold no path gives None."""
    if team.techlead is None:
        return None
    held: set[str] = set()
    for m in team.members:
        if m.team is not None:
            continue  # a nested team is its own definition, with its own reader
        review = getattr(roles.get(m.role), "review", None)
        if review:
            held.update(review.get("held") or [])
    if not held:
        return None
    return {"reader": "techlead", "held": sorted(held), "bound": REVIEW_BOUND}


def flow_review(
    org: orgmod.Org,
    team: orgmod.TeamDef,
    cfg: repoconfig.RepoConfig,
    role: str | None = None,
    *,
    read: repoconfig.Reader | None = None,
) -> tuple[bool, dict[str, Any] | None]:
    """The reader a session started into `team` outside a start takes under the team's current flow
    (design §4.9c items 2 and 3): `(True, reader)` when the team runs a usable flow — a person's
    session (`role` None) and a member stage's role take the flow's reader, any other role none, its
    own `review:` set aside — else `(False, None)`, and the caller keeps today's rule. `cfg` is the
    team's repo."""
    name = current_flow(team)
    try:
        flow = flowdefs.load(name, cfg, org.roles, read=read) if name else None
    except (OSError, ValueError):
        return False, None
    if flow is None or not flow.usable:
        return False, None
    # `listed` is the current flow alone: only `.reader` is read here, never `.sits_out`
    c = Compiled(flow=flow, held=list(cfg.held or ()), listed=[flow], techlead=team.techlead is not None)
    stage = flow.stage_of(role) if role else None
    takes = role is None or (stage is not None and not stage.review)
    return True, c.reader if takes else None


def team_roles(team: orgmod.TeamDef, cfg: repoconfig.RepoConfig, overlay: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The team's member roles resolved against one repo's file and the org's overlay, for
    `team_review`: a role that does not resolve there is left out, never an error — the reader is a
    safety net, and a start must not fail on a role it would only have read."""
    out: dict[str, Any] = {}
    for m in team.members:
        if m.team is None and m.role and m.role not in out:
            try:
                out[m.role] = repoconfig.resolve_role(cfg, m.role, overlay)
            except (KeyError, ValueError):
                continue
    return out


def _trigger(member: Spec) -> dict[str, str]:
    """A seat's trigger as its record carries it (design §4.9b, §6 rule 3): a seat with a trigger
    gives its own, `{trigger, after}`; the techlead's is a question landing, `asks`; a manager on
    call's is `team`, a member needing a reading no policy makes (§6 *A manager on call*, TD-247)."""
    if isinstance(member, orgmod.ManagerDef):
        return {"trigger": "team"}
    if isinstance(member, orgmod.SeatDef):
        return {"trigger": member.trigger, **({"after": member.after} if member.after else {})}
    return {"trigger": "asks"}


def plan(org: orgmod.Org, name: str, host: str, *, profile: str | None = None, files: Files | None = None) -> Plan:
    """Resolve a definition into the sessions it starts, checking everything that can be checked
    without the agent: the checkouts are declared on the team's host (and exist, when that is this
    one — `host` is the host the start runs on), every role, profile and brief resolves, and no
    member asks for something this phase does not build. Raises `TeamError` on the first thing
    that would have stopped the start — nothing is created here (§4.9: never half a team)."""
    team = find(org, name)
    if name in org.unlanded:  # a repo's team whose landing cannot be told (§4.9 *Where a repo's team lands*)
        raise TeamError(org.unlanded[name])
    here, host = host, team.host or host  # the team lands on its `host:`, else where the start runs (§4.4a)
    p = Plan(team=team.name, source=team.source, host=host if host != here else "")
    reach = bool(project_block(org, team.projects, host))
    p.techlead_id = tid = seat_id(org, team, host, here)
    p.manager_id = mid = manager_id(org, team, host, here)
    ctx = (team.techlead.context or "") if team.techlead is not None else ""  # the primer (§4.9b)
    under = compiled(org, team, host, here, files)  # §4.9c: the current flow, compiled into every launch
    p.flow = under.flow.name if under is not None else None
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
            manager=mid,
            under=under,
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
            manager=mid,
            under=under,
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
                manager=mid,
                under=under,
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
        if under is not None and under.sits_out(member.role):
            # §4.9c: not started at a Start, interactive or not — a definition's member is the team's
            # to start; what an interactive member is spared is the switch's wind-down (slice 5)
            p.sit_out.extend(member.names())
            continue
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
                    manager=mid,
                    under=under,
                )
            )
    _check_flows(org, team, host, here, files)
    if under is not None:
        # §4.9c item 2: a role's own `review:` set aside under a flow is said once, at start
        aside = sorted({x.role for x in p.launches if x.set_aside})
        for role in aside:
            p.notes.append(f"{role}'s review: set aside — the flow says what waits")
        if p.sit_out:
            p.notes.append(f"{', '.join(p.sit_out)}: sits out under {under.flow.name}")
    # One line per finding, not per session: a team's members share a brief, and four copies of
    # the same sentence is how a warning gets ignored.
    by_finding: dict[str, list[str]] = {}
    for x in p.launches:
        for finding in unrepeatable(x.prompt or ""):
            by_finding.setdefault(finding, []).append(x.name)
    for finding, who in by_finding.items():
        p.warnings.append(f"{', '.join(who)}: the brief names one run — {finding}")
    # TD-114's transition (design §4.8): a repo's brief that is still a whole brief repeats the
    # template's headings, and by the precedence rule its stale copy wins. Said, and started anyway.
    by_heading: dict[str, list[str]] = {}
    for x in p.launches:
        for heading in repoconfig.repeated_headings(x.prompt or ""):
            by_heading.setdefault(heading, []).append(x.name)
    for heading, who in by_heading.items():
        p.notes.append(
            f"{', '.join(who)}: the repo's brief repeats the template's heading {heading!r} — it is a "
            "supplement now, filled under *This repo's rules*, and where it disagrees it wins: cut it to "
            "the repo's own rules (design §4.8, TD-114)"
        )
    return p
