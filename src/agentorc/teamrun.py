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

import re
import time
from collections.abc import Callable, Collection
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
    """§4.1's rule refused a start: one or more names are held by a live session, or by a record a
    person **suspended** over an identity alarm (§4.8a, TD-077 a2 — the same refusal, since the
    answer is the same: that name is not this start's to take). Nothing was created — the whole
    start is off, so there is never half a team."""

    def __init__(self, team: str, holders: list[dict[str, Any]]):
        self.team, self.holders = team, holders
        names = "; ".join(
            f"{v['name']} is suspended as {v['holder']} — a person lifts it"
            if v.get("verdict") == "suspended"
            else f"{v['name']} is {v.get('holder_state', 'running')} as {v['holder']}"
            for v in holders
        )
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
    # The lead ran the stop itself — the wind-down of §4.9a. It cannot be typed at mid-command, and
    # killing it would kill the command: it is told what is left, and ends itself.
    lead_is_caller: bool = False

    @property
    def member_ids(self) -> list[str]:
        """The members the stop reached: a refused one (its host unreachable, §4.4a) is not waited on."""
        return [e["id"] for e in self.acted if e["role"] == "member" and not e.get("refused")]


def badged(name: str, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in sessions if s.get("team") == name]


def live(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in sessions if s["state"] not in DEAD]


def split(name: str, sessions: list[dict[str, Any]], org: orgmod.Org) -> tuple[dict | None, list[dict]]:
    """The lead and the members among the sessions carrying a team's badge: the lead is the session
    the definition names (a team with a `person` lead has none), the rest are members in name order."""
    team = org.teams.get(name)
    lead_name = team.manager.name if team and team.manager.role != orgmod.PERSON else None
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


def wound_down(sessions: list[dict[str, Any]], seats: Collection[str] = ()) -> str | None:
    """When a team's sessions all declared they were out of work, the latest of those instants
    (design §4.9a, §4.5a **Teams** strip, TD-053 step 6) — else None.

    *Nothing running* and *nothing left to run* are different facts about a team, and only the
    second is an answer: a team stopped by a person, by a clock or by a crash looks identical on the
    strip otherwise. The rule is deliberately all-or-nothing and reads the records rather than
    counting ledger rows, exactly as §4.9a asks: one member's exhaustion is not the team's, and a
    single session that never declared means the team stopped for some other reason. A team with no
    session carrying its badge has never run, or has been forgotten, and is neither.

    `seats` are the names the team's seats run under — its techlead and any seat with a trigger
    (`seat_names`; design §4.9b, TD-075 step 4, TD-098): a seat is empty or filled, never
    finished, so it never declares and is not counted — read from the definition, never from a
    role badge (§9 invariant 9).
    """
    seen = [d if isinstance(d := s.get("out_of_work"), dict) else {} for s in sessions if s.get("name") not in seats]
    if not seen or not all(d.get("at") for d in seen):
        return None
    # `str` before `max`: two declarations of different types would otherwise be a TypeError, and
    # the strip is on the same page as every card (review of PR #203)
    return max(str(d["at"]) for d in seen)


def _seat_whens(team: orgmod.TeamDef) -> dict[str, str]:
    """Every seat the definition names — the techlead and each seat with a trigger (§4.9b, TD-098) —
    with what would make it come, in the card's words (`SeatDef.when`)."""
    out = {team.techlead.name: "comes on the next question"} if team.techlead is not None else {}
    out.update({s.name: s.when() for s in team.seats})
    return out


def _seat_of(team: orgmod.TeamDef, sessions: list[dict[str, Any]]) -> dict[str, str]:
    """name → when-words, for the names among `sessions` that are one of the team's seats: the
    definition's name, or that name with the numeric suffix a session takes when a stale tmux
    session held its id (§4.1, the note `ao team start` prints then) — and never a name the
    definition gives one of its members."""
    whens = _seat_whens(team)
    if not whens:
        return {}
    members = {n for m in team.members if m.team is None for n in m.names()}
    out: dict[str, str] = {}
    for s in sessions:
        n = str(s.get("name") or "")
        if not n or n in members:
            continue
        base = n if n in whens else re.sub(r"-\d+$", "", n)
        if base in whens:
            out[n] = whens[base]
    return out


def seat_names(team: orgmod.TeamDef, sessions: list[dict[str, Any]]) -> set[str]:
    """The names among `sessions` that are one of the team's seats (`_seat_of`)."""
    return set(_seat_of(team, sessions))


def seat_ids(org: orgmod.Org, sessions: list[dict[str, Any]]) -> dict[str, str]:
    """The ids among `sessions` that are a seat of the team whose badge they carry (`seat_names`,
    design §4.9b), each with what would make it come (*comes on the next question*, *runs after
    10 PRs*) — what the Org page draws *on call* while nobody is in one, and its slot (§4.5 *The
    card's anatomy*, TD-097, TD-098). Keyed by the definition and the name, never by a role (§9
    invariant 9)."""
    out: dict[str, str] = {}
    for t in org.teams.values():
        mine = badged(t.name, sessions)
        whens = _seat_of(t, mine)
        out.update({str(s["id"]): whens[s["name"]] for s in mine if s.get("id") and s.get("name") in whens})
    return out


def rows(org: orgmod.Org, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per definition for `ao team list` and the Org page's **Teams** strip (design §4.5a):
    the name, the file it came from, its projects, how many sessions it starts, how many carrying
    its badge are live, and — when none are and every one of them said why — when it wound down.
    There is no team record — a team that is stopped is only its definition, so both are counted
    across the fleet on every call."""
    up = live(sessions)
    rows_out = []
    for t in org.teams.values():
        mine = badged(t.name, sessions)
        n_live = len(badged(t.name, up))
        rows_out.append(
            {
                "name": t.name,
                "source": str(t.source) if t.source else None,
                "projects": list(t.projects),
                "manager": t.manager.name if t.manager.role != orgmod.PERSON else "person",
                "techlead": t.techlead.name if t.techlead else None,  # the seat (§4.9b), if any
                # seats with a trigger (§4.9b, TD-098): what the manager reads to fill each one
                "seats": [{"name": s.name, "role": s.role, "trigger": s.trigger, "after": s.after} for s in t.seats],
                "members": sum(len(m.names()) for m in t.members if m.team is None),
                "live": n_live,
                # only when nothing is live: a team still running is described by what it is doing
                "wound_down": None if n_live else wound_down(mine, seat_names(t, mine)),
            }
        )
    return rows_out


# ── start ─────────────────────────────────────────────────────────────────────────────────────


def files_via(call: Call) -> teams.Files:
    """How a plan reads a checkout on another host that is not a directory here (a machine node):
    the home's `host_files`, confined to the checkout on that host (§4.4a, TD-057 step 4b.3). Any
    refusal — the host unreachable, a path outside the checkout — is the plan's `OSError`, so the
    start stops before anything is created."""

    def files(host: str, directory: str, paths: list[str]) -> dict[str, str | None]:
        try:
            got = call("host_files", host=host, dir=directory, paths=paths)
        except Exception as e:  # noqa: BLE001 — whatever the transport raised, nothing was started
            raise OSError(f"{host}: {e}") from e
        return dict((got or {}).get("files") or {})

    return files


def start(
    call: Call, org: orgmod.Org, name: str, host: str, *, profile: str | None = None
) -> tuple[teams.Plan, dict[str, Any]]:
    """`ao team start <name>` and the strip's **Start** (design §4.9): resolve the definition, check
    *everything* — checkouts, roles, profiles, briefs, and every session name under §4.1's rule —
    then create the lead and each member with `controllers: [lead id]`.

    Raises `TeamError` (or `NamesHeld`) before anything is created, and `PartialStart` if a create
    fails after the checks passed. Returns the plan and the result the callers render."""
    plan = teams.plan(org, name, host, profile=profile, files=files_via(call))
    if not plan.launches:
        raise teams.TeamError(f"team {name} starts nothing: a person leads it and it has no members")
    on = {"host": plan.host} if plan.host else {}
    if plan.host:
        # Design §4.4a "Teams across hosts": *every checkout exists on the record's host*, asked of
        # that host's node through the home — and the whole team refused while it is unreachable.
        for d in dict.fromkeys(str(x.dir) for x in plan.launches):
            try:
                seen = call("host_dir", host=plan.host, dir=d)
            except Exception as e:  # noqa: BLE001 — whatever the transport raised, the answer is the same
                raise teams.TeamError(f"team {name} was not started — {plan.host}: {e}") from e
            if not seen.get("exists"):
                raise teams.TeamError(f"team {name} was not started — {d} does not exist on {plan.host}")
    # §4.1's rule, asked of the agent rather than reimplemented here (`name_check`, the same verdict
    # `create` and the New session form use), for every session before any of them exists.
    held = [
        v
        for v in (call("name_check", dir=str(x.dir), name=x.name, repo=str(x.dir), **on) for x in plan.launches)
        if v.get("verdict") in ("live", "suspended")
    ]
    if held:
        raise NamesHeld(name, held)
    created: list[dict[str, Any]] = []
    notes: list[str] = list(plan.notes)  # said, and the start goes ahead (a seat without its primer, §4.9b)
    lead_id = ""
    try:
        if plan.lead:
            rec = call("create", **plan.lead.create_params([]))
            created.append(rec)
            lead_id = str(rec["id"])
        if plan.techlead:
            # The seat is its manager's member (design §4.9b): the manager is its controller, as
            # for any member. Its id was named in every brief before it existed (`{techlead}`).
            rec = call("create", **plan.techlead.create_params([lead_id] if lead_id else []))
            created.append(rec)
            if str(rec["id"]) != plan.techlead_id:
                notes.append(
                    f"the techlead started as {rec['id']}, but the briefs name {plan.techlead_id} — "
                    "a stale tmux session holds that id; tell the team, or restart it once that session is gone"
                )
        for x in plan.seats:
            # A seat with a trigger (§4.9b, TD-098): started with the team, as the techlead is, so it
            # runs once now and is on call after; its manager fills it again when its trigger is met.
            created.append(call("create", **x.create_params([lead_id] if lead_id else [])))
        for m in plan.members:
            # A person runs a team start, so no attenuation applies (§4.8 create rule); a
            # lead running it is subject to it as for any create, in the host agent.
            created.append(call("create", **m.create_params([lead_id] if lead_id else [])))
    except Exception as e:
        raise PartialStart(name, created, plan, e) from e
    # §9 invariant 5, as TD-041 made it a gate: no session acts on an interactive one, so a member
    # the definition starts interactive carries `controllers: [lead]` that can never fire. The list
    # is set and the start stands — it is a fact about the definition — but it is said out loud.
    out_of_reach = [x.name for x in plan.members if not x.unattended] if lead_id else []
    # TD-042: a brief that names one run cannot start the next. The start stands — the brief is
    # prose and the judgement is the author's — but it is said out loud, like `out_of_reach`.
    return plan, {
        "team": name,
        "manager": lead_id or None,
        "sessions": created,
        "out_of_reach": out_of_reach,
        "unrepeatable": list(plan.warnings),
        "notes": notes,
    }


# ── stop ──────────────────────────────────────────────────────────────────────────────────────


def stop_members(call: Call, org: orgmod.Org, name: str, *, now: bool = False, caller: str | None = None) -> Stopping:
    """The first half of `ao team stop` (design §4.9): the wrap-up prompt — the one the card's
    **Wrap up** sends — to every member, or a kill with `--now`. The lead is stopped by `stop_lead`
    once the members have settled, which is what makes the order the design's one.

    Split in two so a caller that must not block (the Org page) can send this half and let the rest
    run behind it, while `ao team` runs both in a row.

    `caller` is the session running the command, if one is (`AGENTORC_SESSION`). When it is the
    team's own lead this is the wind-down (design §4.9a): the same sequence under a different
    trigger, except that the lead is never typed at or killed by its own command."""
    up = live(badged(name, call("list")))
    if not up:
        raise teams.TeamError(f"no live session carries the team {name} badge — nothing to stop")
    lead, members = split(name, up, org)
    st = Stopping(team=name, now=now, lead=lead, lead_is_caller=bool(caller and lead and lead["id"] == caller))
    for s in members:
        if not now and s.get("out_of_work") and s["state"] in SETTLED:
            # Finished (§4.9a): it declared, and it is not mid-turn. There is nothing to wrap up, and
            # a prompt would only start a turn that `--close` could then land in the middle of.
            st.acted.append(
                {**_entry(s, "member"), "action": "finished (out of work), nothing sent", "state": s["state"]}
            )
            continue
        st.acted.append(_stop_one(call, s, "member", now=now))
    if now and lead is not None:  # a kill has nothing to wait for: the lead goes with them
        st.acted.append(_own_end(lead) if st.lead_is_caller else _stop_one(call, lead, "manager", now=True))
        st.lead = None
    return st


def stop_lead(call: Call, st: Stopping, *, timeout: float = STOP_TIMEOUT, close: bool = False) -> Stopping:
    """The second half: wait for each member to go idle, exited or closed — or for the wrap-up
    window to pass — and then stop the lead. A no-op when `--now` already killed everything.

    `close` also closes each member that settled with nothing to lose (design §4.9a, 2026-09-17: a
    wrapped-up Claude Code session sits `idle` rather than leaving, and `ao team start` refuses
    while it does). A member still working, or holding uncommitted or unpushed work, is left open
    and named: a stop never strands work to look finished."""
    if st.now and st.lead is None:
        return st
    states = wait_settled(call, st.member_ids, timeout)
    for entry in st.acted:
        entry["state"] = states.get(entry["id"], entry.get("state") or "?")
    if close and not st.now:
        records = {s["id"]: s for s in call("list")}
        for entry in st.acted:
            if entry["role"] == "member":
                _close_settled(call, entry, records.get(entry["id"]))
    if st.lead is not None:
        st.acted.append(_own_end(st.lead) if st.lead_is_caller else _stop_one(call, st.lead, "manager", now=st.now))
        st.lead = None
    return st


def _close_settled(call: Call, entry: dict[str, Any], record: dict[str, Any] | None) -> None:
    if record is None or record["state"] == "closed":
        return
    if record["state"] not in SETTLED:
        entry["left_open"] = f"still {record['state']}"
        return
    unsafe = _unsafe_to_close(record)
    if unsafe:
        entry["left_open"] = unsafe
        return
    call("close", id=entry["id"])
    entry["action"] += ", closed"
    entry["state"] = "closed"


def _unsafe_to_close(record: dict[str, Any]) -> str | None:
    """Why this member's checkout is not *clean and pushed*, or None when it is. Unknown is unsafe:
    a record with no `git` yet is left open, as Ready to close leaves it unchecked.

    **One measure** (design §4.2, 2026-09-20, TD-080): `git.unpushed` is computed once by the host
    agent — against the branch's own `origin/<branch>` where it has one, the upstream where it does
    not, and the remote-tracking branches where there is neither — and read here, by the card's
    flag and by Ready to close alike. This function used to run a third test of its own
    (`git branch -r --contains HEAD` for a branch with no upstream), which is now rule 3 of the
    measure and needs no subprocess here."""
    git = record.get("git")
    if not git:
        return "git state unknown"
    if git.get("dirty"):
        return f"{git['dirty']} uncommitted"
    if git.get("unpushed"):
        against = git.get("pushed_against") or "its remote"
        return f"{git['unpushed']} unpushed (vs {against})"
    return None


def _own_end(lead: dict[str, Any]) -> dict[str, Any]:
    """The lead's entry when the lead is the one running the stop: nothing is sent to it."""
    return {
        **_entry(lead, "manager"),
        "action": f"is you — finish your last acts, then `ao close {lead['id']}`",
        "state": lead.get("state") or "?",
    }


def _stop_one(call: Call, s: dict[str, Any], role: str, *, now: bool) -> dict[str, Any]:
    """One member's or the lead's stop. A refusal — its host unreachable (§4.4a), a pending prompt
    — is this entry's outcome, not the stop's: the rest of the team is still wrapped up, and the
    refused one is named with the reason (TD-057 step 4a, the 3b leftover)."""
    try:
        if now:
            call("kill", id=s["id"])
        else:
            call("send", id=s["id"], text=teams.WRAPUP_PROMPT, wrapup=True)
    except Exception as e:  # noqa: BLE001 — the transport's error is the reason, whichever it is
        return {**_entry(s, role), "action": f"refused: {e}", "state": s.get("state") or "?", "refused": True}
    return {**_entry(s, role), "action": "killed" if now else "wrap-up sent", "state": "killed" if now else "?"}


def _entry(s: dict[str, Any], role: str) -> dict[str, Any]:
    return {"id": s["id"], "name": s.get("name") or s["id"], "role": role}


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
