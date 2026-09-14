"""Running a team definition: the RPC sequence `ao team start|stop|list` performs, in one place so
the CLI and the Org page's **Teams** strip cannot drift (design §4.9 "Starting and stopping",
§4.5a Org **Teams** strip).

`agentorc.teams` decides what a start *means* (`plan`, which touches no RPC); this module is the
one that talks to the agent, and it does so through an injected blocking `call(method, **params)`
so neither caller's transport leaks in: `ao team` hands it `cli.call_sync`, and the UI hands it the
same blocking client from a worker thread, so a start or a wrap-up never blocks the event loop.

Every check happens before any create (§4.9: there is never half a team). Nothing here formats for
a terminal or a page — the callers do that from the returned records.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentorc import org as orgmod
from agentorc import repoconfig, teams

Call = Callable[..., Any]

SETTLED = ("idle", "exited", "closed")  # what "wrapped up" looks like from outside (design §4.2)
DEAD = ("exited", "closed")
WRAPUP_POLL = 2.0  # seconds between reads while a stop waits for the members to settle
STOP_TIMEOUT = 300.0  # the default wrap-up window (§4.9)


class NamesHeld(teams.TeamError):
    """§4.1's rule refused a start: one or more names are held by a live session. Nothing was
    created — the whole start is off, so there is never half a team."""

    def __init__(self, team: str, holders: list[dict[str, Any]]):
        self.team, self.holders = team, holders
        names = "; ".join(f"{v['name']} is {v.get('holder_state', 'running')} as {v['holder']}" for v in holders)
        super().__init__(f"team {team} was not started — {names}")


class PartialStart(teams.TeamError):
    """A create failed part-way through a start that had passed every check. The sessions already
    created are named rather than silently left behind."""

    def __init__(self, team: str, created: list[dict[str, Any]], plan: teams.Plan, error: Exception):
        self.team, self.created, self.plan, self.error = team, created, plan, error
        super().__init__(f"team {team}: {error} — {len(created)} session(s) already started")


@dataclass
class Stopping:
    """A stop in progress: what has been sent, and the lead that is still to be stopped."""

    team: str
    now: bool
    acted: list[dict[str, Any]] = field(default_factory=list)
    lead: dict[str, Any] | None = None  # the record, until `stop_lead` acts on it

    @property
    def member_ids(self) -> list[str]:
        return [e["id"] for e in self.acted if e["role"] == "member"]


def badged(name: str, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in sessions if s.get("team") == name]


def live(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in sessions if s["state"] not in DEAD]


def split(name: str, sessions: list[dict[str, Any]], org: orgmod.Org) -> tuple[dict | None, list[dict]]:
    """The lead and the members among the sessions carrying a team's badge: the lead is the session
    the definition names (a team with a `person` lead has none), the rest are members in name order."""
    team = org.teams.get(name)
    lead_name = team.lead.name if team and team.lead.role != orgmod.PERSON else None
    lead = next((s for s in sessions if s.get("name") == lead_name), None)
    return lead, sorted((s for s in sessions if s is not lead), key=lambda s: s.get("name") or s["id"])


def org_with_repo_teams(org: orgmod.Org, roots: list[Path | str]) -> tuple[orgmod.Org, list[str]]:
    """Fold each repo's own `teams:` into the org (design §4.9: a repo may ship its own grind team;
    the org file wins a name collision). A repo whose `.agentorc.yml` cannot be read is skipped and
    named in the returned notes — one broken file must not empty the strip or the pick-list."""
    notes: list[str] = []
    for root in roots:
        try:
            cfg = repoconfig.load(Path(root).expanduser())
            if cfg.teams and cfg.root:
                org = orgmod.merge_repo_teams(org, cfg.root, cfg.teams)
        except (OSError, ValueError) as e:
            notes.append(f"{root}: {str(e).strip(chr(34))}")
    return org, notes


def rows(org: orgmod.Org, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per definition for `ao team list` and the Org page's **Teams** strip (design §4.5a):
    the name, the file it came from, its projects, how many sessions it starts, and how many
    carrying its badge are live. There is no team record — a team that is stopped is only its
    definition, so `live` is counted across the fleet on every call."""
    up = live(sessions)
    return [
        {
            "name": t.name,
            "source": str(t.source) if t.source else None,
            "projects": list(t.projects),
            "lead": t.lead.name if t.lead.role != orgmod.PERSON else "person",
            "members": sum(len(m.names()) for m in t.members if m.team is None),
            "live": len(badged(t.name, up)),
        }
        for t in org.teams.values()
    ]


# ── start ─────────────────────────────────────────────────────────────────────────────────────


def start(
    call: Call, org: orgmod.Org, name: str, host: str, *, profile: str | None = None
) -> tuple[teams.Plan, dict[str, Any]]:
    """`ao team start <name>` and the strip's **Start** (design §4.9): resolve the definition, check
    *everything* — checkouts, roles, profiles, briefs, and every session name under §4.1's rule —
    then create the lead and each member with `controllers: [lead id]`.

    Raises `TeamError` (or `NamesHeld`) before anything is created, and `PartialStart` if a create
    fails after the checks passed. Returns the plan and the result the callers render."""
    plan = teams.plan(org, name, host, profile=profile)
    if not plan.launches:
        raise teams.TeamError(f"team {name} starts nothing: a person leads it and it has no members")
    # §4.1's rule, asked of the agent rather than reimplemented here (`name_check`, the same verdict
    # `create` and the New session form use), for every session before any of them exists.
    held = [
        v
        for v in (call("name_check", dir=str(x.dir), name=x.name, repo=str(x.dir)) for x in plan.launches)
        if v.get("verdict") == "live"
    ]
    if held:
        raise NamesHeld(name, held)
    created: list[dict[str, Any]] = []
    lead_id = ""
    try:
        if plan.lead:
            rec = call("create", **plan.lead.create_params([]))
            created.append(rec)
            lead_id = str(rec["id"])
        for m in plan.members:
            # A person runs a team start, so no attenuation applies (§4.8 create rule); an
            # orchestrator running it is subject to it as for any create, in the agent.
            created.append(call("create", **m.create_params([lead_id] if lead_id else [])))
    except Exception as e:
        raise PartialStart(name, created, plan, e) from e
    # §9 invariant 5, as TD-041 made it a gate: no session acts on an interactive one, so a member
    # the definition starts interactive carries `controllers: [lead]` that can never fire. The list
    # is set and the start stands — it is a fact about the definition — but it is said out loud.
    out_of_reach = [x.name for x in plan.members if not x.unattended] if lead_id else []
    return plan, {"team": name, "lead": lead_id or None, "sessions": created, "out_of_reach": out_of_reach}


# ── stop ──────────────────────────────────────────────────────────────────────────────────────


def stop_members(call: Call, org: orgmod.Org, name: str, *, now: bool = False) -> Stopping:
    """The first half of `ao team stop` (design §4.9): the wrap-up prompt — the one the card's
    **Wrap up** sends — to every member, or a kill with `--now`. The lead is stopped by `stop_lead`
    once the members have settled, which is what makes the order the design's one.

    Split in two so a caller that must not block (the Org page) can send this half and let the rest
    run behind it, while `ao team` runs both in a row."""
    up = live(badged(name, call("list")))
    if not up:
        raise teams.TeamError(f"no live session carries the team {name} badge — nothing to stop")
    lead, members = split(name, up, org)
    st = Stopping(team=name, now=now, lead=lead)
    for s in members:
        st.acted.append(_stop_one(call, s, "member", now=now))
    if now and lead is not None:  # a kill has nothing to wait for: the lead goes with them
        st.acted.append(_stop_one(call, lead, "lead", now=True))
        st.lead = None
    return st


def stop_lead(call: Call, st: Stopping, *, timeout: float = STOP_TIMEOUT) -> Stopping:
    """The second half: wait for each member to go idle, exited or closed — or for the wrap-up
    window to pass — and then stop the lead. A no-op when `--now` already killed everything."""
    if st.now and st.lead is None:
        return st
    states = wait_settled(call, st.member_ids, timeout)
    for entry in st.acted:
        entry["state"] = states.get(entry["id"], entry.get("state") or "?")
    if st.lead is not None:
        st.acted.append(_stop_one(call, st.lead, "lead", now=st.now))
        st.lead = None
    return st


def _stop_one(call: Call, s: dict[str, Any], role: str, *, now: bool) -> dict[str, Any]:
    if now:
        call("kill", id=s["id"])
    else:
        call("send", id=s["id"], text=teams.WRAPUP_PROMPT)
    return {
        "id": s["id"],
        "name": s.get("name") or s["id"],
        "role": role,
        "action": "killed" if now else "wrap-up sent",
        "state": "killed" if now else "?",
    }


def wait_settled(call: Call, ids: list[str], timeout: float) -> dict[str, str]:
    """Read the records until every id is idle, exited or closed, or the window passes (design §4.9:
    *waits for each to go idle or the wrap-up window to pass*). Returns each id's last state."""
    if not ids:
        return {}
    end = time.monotonic() + max(timeout, 0.0)
    while True:
        states = {s["id"]: s["state"] for s in call("list") if s["id"] in ids}
        if all(st in SETTLED for st in states.values()) or time.monotonic() >= end:
            return states
        time.sleep(min(WRAPUP_POLL, max(end - time.monotonic(), 0.0)) or WRAPUP_POLL)
