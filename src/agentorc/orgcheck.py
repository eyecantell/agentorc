"""`ao org` and `ao org check` (design §4.7, §4.9 *The org is an aggregate*; TD-229 slice 6): the org
as the clients aggregate it, printed, and the same reading as a verdict.

Both read what `org.with_repos` already made — the org file plus every registered checkout's
`teams:` — and what a start would check (`teams.plan`), and write nothing: no file, no record, no
RPC that changes anything. The terminal's half (the words in a line, the exit code) is `cli`'s."""

from __future__ import annotations

import subprocess
from collections.abc import Collection
from pathlib import Path
from typing import Any

from agentorc import flowdefs, teams
from agentorc import org as orgmod
from sessionorc import board, defs, gitinfo, paths
from sessionorc import settings as settings_mod

GIT_TIMEOUT = 5.0


def where(org: orgmod.Org, team: orgmod.TeamDef, here: str) -> tuple[str, str]:
    """The host a team lands on and why, in `ao org`'s words: a repo's team by the landing rule
    (*place*, *registered here*, *registered on <node>*), the org file's by its own `host:` or,
    with none, the host a start runs on. A landing that cannot be told is `("", <the reason>)`."""
    if team.name in org.unlanded:
        return "", org.unlanded[team.name]
    if team.name in org.landed:
        return org.landed[team.name]
    return (team.host, "host:") if team.host else (here, "no host: — where it is started")


def last_commit(home: Path, name: str) -> str:
    """`<short sha> <date> <subject>` of the last commit that touched `name` under the home, or ""
    when there is none (never committed, or git could not say)."""
    try:
        cp = subprocess.run(
            ["git", "-C", str(home), "log", "-1", "--format=%h %cs %s", "--", name],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return cp.stdout.strip() if cp.returncode == 0 else ""


def remainder(home: Path | None = None) -> dict[str, Any]:
    """What is left at the home (§4.9 *What is left at the home has a history*): each of the three
    definition files, whether it is there, and its last commit; `tree` false when the home is not
    a work tree yet, so nothing has a history."""
    home = home or paths.home()
    tree = defs.is_tree(home)
    files = [
        {
            "name": name,
            "path": str(home / name),
            "exists": (home / name).is_file(),
            "commit": last_commit(home, name) if tree else "",
        }
        for name in defs.TRACKED
    ]
    return {"home": str(home), "tree": tree, "files": files}


def view(org: orgmod.Org, here: str, home: Path | None = None, roots: Collection[str] = ()) -> dict[str, Any]:
    """The org as the clients aggregate it: each team with its source file, its repos, the host it
    lands on and why; the names a repo's definition lost to the org file's (`shadowed`) and the
    ones two repos define (`refused`); then the remainder's files with their last commit."""
    rows = []
    for name in sorted(org.teams):
        team = org.teams[name]
        host, why = where(org, team, here)
        rows.append(
            {
                "name": name,
                "source": str(team.source) if team.source else "",
                "repos": org.team_repos(team),
                "host": host,
                "why": why,
            }
        )
    return {
        "teams": rows,
        "shadowed": {n: [str(f) for f in fs] for n, fs in org.shadowed.items()},
        "refused": dict(org.refused),
        "flows": flowdefs.visible(roots, org.roles),  # every flow the org can see here (§4.9c)
        "remainder": remainder(home),
    }


def checkout_warnings(root: Path) -> list[str]:
    """What `ao org check` warns of on a registered checkout that is there: off its default branch,
    or holding changes — a team's definition and its briefs are read from it *as it is checked
    out* (§4.9), so either changes the org while it sits there."""
    info = gitinfo.git_info(root, timeout=GIT_TIMEOUT)
    if info is None:
        return [f"{root}: git could not read it, so its branch and its changes are unknown"]
    out: list[str] = []
    try:
        default = board.default_branch(root)
    except board.Refused:
        default = ""
    if default and info.branch != default:
        out.append(f"{root}: on {info.branch}, not its default branch {default} — the org reads it as checked out")
    if info.dirty:
        out.append(f"{root}: holds {info.dirty} change{'' if info.dirty == 1 else 's'} — the org reads it as it stands")
    return out


def check(  # noqa: PLR0913 — each argument is one thing the verdict reads
    org: orgmod.Org,
    notes: Collection[str],
    here: str,
    roots: Collection[str],
    linked: Collection[str],
    *,
    files: teams.Files | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`ao org check`: the aggregate as a verdict — `{ok, lacks, warnings}`, `ok` false when
    anything is lacking. `notes` are `with_repos`' own (a repo file that cannot be read, a name
    defined twice, a landing that cannot be told), `roots` this host's registry, `linked` the
    hosts a `place:` may name beside this one, `settings` the settings file as read (None: absent
    or broken, which lacks nothing here).

    A lack, each on its own line: a registered checkout that is not there; a team a start would
    refuse — its role's profile not in `profiles.yml`, a brief not in the checkout, a checkout
    missing, each in the start's own words (`teams.plan`, which creates nothing); a name defined
    twice; a team whose definition names one session twice (TD-268); a `place:` naming no linked
    host; a team in `settings.yml` no definition names. A
    warning, which does not fail it: a registered checkout off its default branch or holding
    changes, a `place:` naming a team no registered repo defines, and, per team under a flow, each
    key it writes that the flow would fill with the same value (`teams.flow_redundant`)."""
    lacks: list[str] = []
    warnings: list[str] = []

    def lack(line: str) -> None:
        if line not in lacks:
            lacks.append(line)

    for root in roots:
        path = Path(root).expanduser()
        if not path.is_dir():
            lack(f"{root}: a registered checkout that is not there — remove its line from the registry, or clone it")
        else:
            warnings.extend(checkout_warnings(path))
    placed_nowhere = {t for t in org.place if t not in org.teams and t not in org.refused}
    for note in notes:
        if any(note.startswith(f"place.{t}:") for t in placed_nowhere):
            warnings.append(note)
        else:
            lack(note)  # an unreadable repo file, a name twice, a landing that cannot be told
    for team, host in org.place.items():
        if team in org.unlanded:
            continue  # its landing's own note says the host could not be asked: one cause, one line
        if host != here and host not in linked:
            lack(
                f"place.{team}: {host} is no linked host of {here} ({', '.join(sorted(linked)) or 'none linked'}) — "
                "name a node under `nodes:` in hosts.yml, or this host"
            )
    for name in sorted(org.teams):
        team = org.teams[name]
        for twice in team.twice_named():
            src = team.source.name if team.source else "its definition"
            # Members… edits the org file only; a repo's team is changed by PR (TD-229)
            hint = " (Members… → Remove keeps the session)" if org.path and team.source == org.path else ""
            lack(f"team {name} names {twice} twice — take one of its lines out of {src}{hint}")
        if name in org.unlanded:
            continue  # said above, in the landing's own words
        try:
            teams.plan(org, name, here, files=files)
        except (teams.TeamError, ValueError, OSError) as e:
            lack(str(e).strip('"'))
        else:
            warnings.extend(teams.flow_redundant(org, team, here, here, files))
    # a flow nobody lists that is not usable (§4.9c): a listed one is the start's refusal, said above
    listed = {f for t in org.teams.values() for f in t.flows}
    for f in flowdefs.visible(roots, org.roles):
        if not f["usable"] and not f.get("shadowed") and f["name"] not in listed:
            lack(f"flow {f['name']} ({f['source']}) is not usable — {'; '.join(f['problems'])}")
    for name in sorted(settings_mod.teams(settings or {})):
        if name not in org.teams and name not in org.refused:
            lack(
                f"settings.yml: teams.{name} — no definition names a team {name!r} (a repo renamed its team?); "
                "its settings are read by nothing"
            )
    return {"ok": not lacks, "lacks": lacks, "warnings": warnings}
