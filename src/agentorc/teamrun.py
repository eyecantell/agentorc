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

import contextlib
import re
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentorc import flowdefs, repoconfig, teams
from agentorc import org as orgmod
from sessionorc import balance as balance_mod
from sessionorc import hosts, naming
from sessionorc import ledger as ledger_mod
from sessionorc.client import AgentError
from sessionorc.gitinfo import work_left
from sessionorc.work import (
    closed_finished,
    finished,
    sat_out,
    wound_down,
)  # one reading each for the card and the home (rules 8, 9)

Call = Callable[..., Any]
Waiting = dict[str, list[dict[str, Any]]]  # sender id → its open questions (`work.waiting_of`, TD-274)


def waiting_of_home(call: Call, *, strict: bool = False) -> Waiting:
    """What each live session waits on, from the home's `host` reading (§4.9a *Waiting is read,
    never declared*, TD-274): the person inbox is a person's to read, so a session's `ao team status`
    asks the home for the references and bounds, never the text. `{}` from an agent without it —
    unless `strict`, for a caller about to close sessions, which a failed read must not license."""
    try:
        got = (call("host") or {}).get("waiting")
    except AgentError:  # an agent that answers no `host`: an older one, a stub
        if strict:
            raise
        return {}
    return {str(k): list(v) for k, v in got.items() if isinstance(v, list)} if isinstance(got, dict) else {}


SETTLED = ("idle", "exited", "closed")  # what "wrapped up" looks like from outside (design §4.2)
DEAD = ("exited", "closed")
WRAPUP_POLL = 2.0  # seconds between reads while a stop waits for the members to settle
STOP_TIMEOUT = 300.0  # the default wrap-up window (§4.9)
# a run under this many seconds, first start to last close, says *after 40 s* on the wound-down note
# (§4.5a, TD-262): a start that ended at once is the thing to notice
SHORT_RUN = 600


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


def persons(s: dict[str, Any]) -> bool:
    """A person's own session (design §4.9 *A person in the team*): one whose record says
    `unattended: false`. A team's own facts — live, concluded, wound down, what a stop reaches —
    read the others. Every record carries the field; one that lacks it is read as a worker's, which
    is what every badged session was before TD-173."""
    return s.get("unattended") is False


def badged(name: str, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in sessions if s.get("team") == name]


def crew(name: str, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The sessions carrying a team's badge that are not a person's (`persons`): what a team's own
    facts and acts read — whether it is live or concluded, whom a stop or a concluded Start closes,
    whether Add member starts the new one now (design §4.9 *A person in the team*)."""
    return [s for s in badged(name, sessions) if not persons(s)]


def live(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in sessions if s["state"] not in DEAD]


def on_call(s: dict[str, Any]) -> bool:
    """A seat with nobody in it (§4.9b, §6 rule 3): a record carrying `seat` that is `exited` or
    `closed`. Its id and its grants survive the close, so whoever fills it is the controller a
    session names now (design §4.5a New session **Controllers**, TD-269)."""
    return bool(s.get("seat")) and s.get("state") in ("exited", "closed")


# A Start's and an Apply's line for a manager on call, written held (§6 rule 3, TD-410): the card's words
ON_CALL_LINE = f"on call — {orgmod.MANAGER_WHEN}"


def on_call_gate(x: teams.Launch) -> dict[str, Any]:
    """`create`'s `held` for a team's lead on call (§6 rule 3 *A Start writes the seat and never
    fills it*, TD-410): the launch carries a seat's trigger only when its manager is on call, and a
    standing one (`on_call: false`) starts live as before. No `held_reason`: nothing holds it. A
    manager the definition asks to be watched (`unattended: false`) starts live too: a held record is
    a seat's, which `create` writes only unattended."""
    return {"held": True} if x.lead and x.trigger and x.unattended else {}


def can_control(sessions: list[dict[str, Any]], sid: str) -> dict[str, Any] | None:
    """The record `sid` when it may stand as a new session's controller — live, or a seat on call
    (TD-269, built by TD-276) — else None."""
    s = next((s for s in sessions if s.get("id") == sid), None)
    return s if s is not None and (s["state"] not in DEAD or on_call(s)) else None


def balance_marks(repos: dict[str, dict[str, Any]], host: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """The teams over their line, `{team: mark}`, from the `repos` reading (design §6 *Balance*,
    TD-239): each checkout's reading carries the marks whose `repo` it is. `host` is the home's `host`
    read, whose `balance` carries every mark — one with no `repo` too, which no checkout's reading
    holds (TD-330); an older home's read has none."""
    out: dict[str, dict[str, Any]] = {}
    for r in repos.values():
        marks = r.get("balance") if isinstance(r, dict) else None
        for team, mark in (marks if isinstance(marks, dict) else {}).items():
            if isinstance(mark, dict):
                out.setdefault(str(team), mark)
    marks = (host or {}).get("balance")
    for team, mark in (marks if isinstance(marks, dict) else {}).items():
        if isinstance(mark, dict):
            out.setdefault(str(team), mark)
    return out


def _clock(iso: Any) -> str:
    """An instant in this host's clock, the weekday before it when it is not today."""
    try:
        at = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return ""
    day = "" if at.date() == datetime.now().astimezone().date() else at.strftime("%a ")
    return f"{day}{at:%H:%M}"


def _crossed_words(c: dict[str, Any]) -> str:
    v, lim, dur = c.get("value"), c.get("limit"), balance_mod.duration
    if c.get("line") == "prs":
        return f"{v} open PR{'' if v == 1 else 's'}, line {lim}"
    if c.get("line") == "oldest":
        return f"oldest PR {dur(v)}, line {dur(lim)}"
    if c.get("line") == "review":
        return f"review waiting {dur(v)}, bound {dur(lim)}"
    return f"{c.get('line')} {v}, line {lim}"


def balance_note(mark: dict[str, Any]) -> str:
    """A mark in the team card's words (design §4.5a **over its line** note), which `ao team list`
    and `ao repo` say too: *over its line since 14:02: 9 open PRs, line 8* — each crossed line in
    the mark's own numbers."""
    since = f" since {t}" if (t := _clock(mark.get("since"))) else ""
    lines = ", ".join(_crossed_words(c) for c in mark.get("crossed") or [] if isinstance(c, dict))
    return f"over its line{since}" + (f": {lines}" if lines else "")


def balance_now(
    team: str, sessions: list[dict[str, Any]], repos: dict[str, dict[str, Any]], now: datetime | None = None
) -> dict[str, Any]:
    """The numbers a team's balance lines are read against, as they read now (design §4.7 `ao team
    balance`, §6 *Balance*), gathered as the home's tick gathers them: the registered repos the
    team's live sessions name, the most open pull requests any of them has (`prs`) and the age of
    the oldest in seconds (`oldest`), how long the oldest pull request has waited at the team's
    seats (`review`) and the bound it is read against (`bound`, the shortest `review.bound` a live
    member carries, two hours where none). A number that cannot be told is None: no live member,
    no reading, nothing waiting; `oldest` is 0 where the reading holds no open pull request."""
    now = now or datetime.now(UTC)
    members = [s for s in sessions if s.get("team") == team and s.get("state") not in DEAD]
    members = [s for s in members if not s.get("superseded_by")]
    roots = sorted({str(s["repo"]) for s in members if s.get("repo") and s["repo"] in repos})

    def age(iso: Any) -> int | None:
        try:
            return int((now - datetime.fromisoformat(str(iso).replace("Z", "+00:00"))).total_seconds())
        except (TypeError, ValueError):
            return None

    counts: list[int] = []
    ages: list[int] = []
    for root in roots:
        prs = repos[root].get("prs")
        if not isinstance(prs, dict) or "error" in prs or not isinstance(prs.get("open"), list):
            continue  # could not look, as `balance.crossed` reads a failed reading
        counts.append(len(prs["open"]))
        ages += [a for p in prs["open"] if isinstance(p, dict) and (a := age(p.get("created"))) is not None]
    waits = [
        a
        for s in members
        if s.get("seat") and isinstance(s.get("prs_waiting"), dict)
        if (a := age(s["prs_waiting"].get("oldest"))) is not None
    ]
    bounds = [d for s in members if (d := balance_mod.span((s.get("review") or {}).get("bound")))]
    return {
        "members": len(members),
        "repos": roots,
        "prs": max(counts) if counts else None,
        "oldest": max(ages) if ages else (0 if counts else None),
        "review": max(waits) if waits else None,
        "bound": int((min(bounds) if bounds else balance_mod.REVIEW_BOUND).total_seconds()),
    }


def balance_rows(bal: dict[str, Any], now: dict[str, Any]) -> list[str]:
    """`ao team balance`'s three lines, which the Settings page draws under **balance** (§4.5a): each
    number as it reads now, the line it is read against where one is drawn, and *over* where it is
    crossed."""
    dur = balance_mod.duration

    def row(what: str, value: Any, limit: Any, word: str, nothing: str, show: Callable[[Any], str]) -> str:
        said = nothing if value is None else show(value)
        if limit is None:
            return f"  {what}: {said} (no line)"
        over = " — over" if value is not None and value > limit else ""
        return f"  {what}: {said} ({word} {show(limit)}){over}"

    oldest = balance_mod.span(bal.get("oldest"))
    return [
        row("open PRs", now["prs"], bal.get("prs"), "line", "could not look", str),
        row("oldest PR", now["oldest"], int(oldest.total_seconds()) if oldest else None, "line", "could not look", dur),
        row(
            "reader's queue",
            now["review"],
            now["bound"] if bal.get("review") else None,
            "bound",
            "nothing waiting",
            dur,
        ),
    ]


def split(name: str, sessions: list[dict[str, Any]], org: orgmod.Org) -> tuple[dict | None, list[dict]]:
    """The lead and the members among the sessions carrying a team's badge: the lead is the session
    the definition names (a team with a `person` lead has none), the rest are members in name order."""
    team = org.teams.get(name)
    lead_name = team.manager.name if team and team.manager.role != orgmod.PERSON else None
    lead = next((s for s in sessions if s.get("name") == lead_name), None)
    return lead, sorted((s for s in sessions if s is not lead), key=lambda s: s.get("name") or s["id"])


def concluded(
    sessions: list[dict[str, Any]], seats: Collection[str] = (), waiting: Waiting | None = None
) -> dict[str, Any] | None:
    """When a live team has said everything it has to say (design §4.5a **team groups**, §4.9a
    *A person's Start on a concluded team*, TD-099): the home's reading of *finished* (§6 rule 9,
    `sessionorc.work.finished`, TD-241) where it holds — every live member `idle` and declared,
    `out_of_work` or `restart_wanted`, its seats not there or `idle`, and its manager `idle`,
    declared or not. Then `{at, restart, names}`: the latest declaration's instant (None where only
    seats and the manager are left), whether any of them asks for a restart, and the live sessions
    a Start would close first. Else None — and None for a team with nothing live, which is
    `wound_down`'s.

    *Concluded* is the team's word, not a member's *finished*: it takes either declaration, since
    either says the run is over. The state is part of the test because a declaration is cleared only
    by a later declared claim — a session that declared and then took a turn is `working` with the
    word still on its record, and its team is not concluded while it is. A seat never declares; one
    that is `working` is answering somebody. `seats` are the definition's names for them, beside
    the record's own `seat` field. `waiting` is the home's `host` reading's (`waiting_of` over the
    person inbox, §4.9a *Waiting is read, never declared*, TD-274): a live member with a question
    about a reference out to the person is not concluded, whatever it declared."""
    f = finished(sessions, seats, waiting)
    if f is None or f["why"]:
        return None
    return {"at": f["at"], "restart": f["restart"], "names": f["names"]}


def _seat_whens(team: orgmod.TeamDef) -> dict[str, str]:
    """Every seat the definition names — the techlead, each seat with a trigger (§4.9b, TD-098) and
    a manager on call (§4.9, TD-259) — with what would make it come, in the card's words
    (`SeatDef.when`, `org.MANAGER_WHEN`)."""
    out = {team.techlead.name: "comes on the next question"} if team.techlead is not None else {}
    if team.anchor is not None:  # §6 rule 3's `work` trigger (§4.9b *The anchor seat*, TD-381)
        out[team.anchor.name] = orgmod.ANCHOR_WHEN
    out.update({s.name: s.when() for s in team.seats})
    if team.manager.on_call and team.manager.name:  # §4.9, §6 rule 3's `team` trigger (TD-259)
        out[team.manager.name] = orgmod.MANAGER_WHEN
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
    # a manager is a seat by its **record**, not by the definition alone: a team keeps the shape it was
    # started with until its next Start (§4.9 `on_call`), so a standing manager started before the
    # definition read *on call* is no seat — nothing would fill it, and it still declares
    lead = team.manager.name if team.manager.on_call else None
    named = (
        {s.name for s in team.seats}
        | ({team.techlead.name} if team.techlead is not None else set())
        | ({team.anchor.name} if team.anchor is not None else set())
    )
    out: dict[str, str] = {}
    for s in sessions:
        n = str(s.get("name") or "")
        if not n or n in members:
            continue
        base = n if n in whens else re.sub(r"-\d+$", "", n)
        if base == lead and base not in named and not isinstance(s.get("seat"), dict):
            continue
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


def role_holders(t: orgmod.TeamDef) -> list[dict[str, Any]]:
    """The roles a definition names, in its order — the manager, the techlead seat, the seats with a
    trigger, then the members — each once, with the session names that hold it and whether it is a
    seat (an empty seat reads *on call*). A person leading the team is not a role here."""
    out: dict[str, dict[str, Any]] = {}

    def add(role: str, names: list[str], seat: bool) -> None:
        if not role:
            return
        got = out.setdefault(role, {"role": role, "names": [], "seat": seat})
        got["names"] += [n for n in names if n not in got["names"]]

    if t.manager.role != orgmod.PERSON:
        add(t.manager.role, [t.manager.name], False)
    if t.techlead:
        add("techlead", [t.techlead.name], True)
    if t.anchor:
        add(orgmod.ANCHOR_ROLE, [t.anchor.name], True)
    for seat in t.seats:
        add(seat.role, [seat.name], True)
    for m in t.members:
        if m.team is None:
            add(m.role, m.names(), False)
    return list(out.values())


def _when(iso: Any) -> datetime | None:
    try:
        at = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def wound_down_note(t: orgmod.TeamDef, mine: list[dict[str, Any]], names: dict[str, str]) -> dict[str, Any]:
    """**Who, how soon, and why** on a wound-down team's note (design §4.5a *wound down* note, TD-262,
    built by TD-265): `closed_by` from the run's records' closer — the manager's name when it closed
    any member, else the first member's closer (*you*, *the tick*, a session's name) — `run_seconds`
    from the run's first `created` to its last `closed_at`, `after` set only under `SHORT_RUN`, and
    `manager_why`, the first line of the manager's own `out_of_work.why` (none when it closed without
    declaring). The run is the records not superseded by a later start; anything unreadable is left
    out, never guessed."""
    run = [s for s in mine if not s.get("superseded_by")]
    mgr_name = t.manager.name if t.manager.role != orgmod.PERSON else ""
    mgr = next((s for s in run if mgr_name and s.get("name") == mgr_name), None)
    closers = [
        (s, str(c["by"])) for s in run if s is not mgr and isinstance(c := s.get("closer"), dict) and c.get("by")
    ]
    closed_by = ""
    if mgr and any(by == mgr.get("id") for _, by in closers):
        closed_by = mgr_name
    elif closers:
        s, by = closers[0]
        closed_by = {"person": "you", "tick": "the tick"}.get(by) or names.get(by) or by
    starts = [w for s in run if (w := _when(s.get("created")))]
    ends = [w for s in run if (w := _when(s.get("closed_at")))]
    secs = int((max(ends) - min(starts)).total_seconds()) if starts and ends else None
    after = ""
    if secs is not None and 0 <= secs < SHORT_RUN:
        after = f"{secs} s" if secs < 60 else f"{secs // 60} min"
    oow = mgr.get("out_of_work") if mgr else None
    why = str(oow.get("why") or "").strip() if isinstance(oow, dict) else ""
    return {"closed_by": closed_by, "run_seconds": secs, "after": after, "manager_why": why.split("\n", 1)[0]}


def wound_down_words(r: dict[str, Any]) -> str:
    """The note's tail after *wound down <t>*, as the card and `ao team list` both say it:
    * · after 40 s · by manager-dc-1 — nothing pickable*. *by the tick* where rule 9 ended it."""
    by = "the tick" if r.get("by_tick") else r.get("closed_by")
    return (
        (f" · after {r['after']}" if r.get("after") else "")
        + (f" · by {by}" if by else "")
        + (f" — {r['manager_why']}" if r.get("manager_why") else "")
    )


def rows(org: orgmod.Org, sessions: list[dict[str, Any]], waiting: Waiting | None = None) -> list[dict[str, Any]]:
    """One row per definition for `ao team list` and the Org page's **Teams** strip (design §4.5a):
    the name, the file it came from, its projects, how many sessions it starts, how many carrying
    its badge are live, and — when none are and every one of them said why — when it wound down;
    when some are and every one is idle and declared, when it concluded (TD-099), or else why it
    has not (`not_concluded`, the reading's `why`; TD-241).
    There is no team record — a team that is stopped is only its definition, so both are counted
    across the fleet on every call. `waiting`: the home's (`waiting_of_home`), so a member with a
    question out keeps its team from *concluded* as it keeps it from the tick's wind-down (TD-274)."""
    # a team's own facts read its **unattended** sessions: a person's session in the team keeps
    # nothing live and never winds it down (design §4.9 *A person in the team*, TD-173)
    crew = [s for s in sessions if not persons(s)]
    up = live(crew)
    names = {str(s.get("id")): str(s.get("name") or "") for s in sessions}
    rows_out = []
    for t in org.teams.values():
        mine = badged(t.name, crew)
        n_live = len(badged(t.name, up))
        down = None if n_live else wound_down(mine, seat_names(t, mine))
        # the home's reading (§6 rule 9), asked once: it holds when `why` is empty
        f = finished(mine, seat_names(t, mine), waiting) if n_live else None
        rows_out.append(
            {
                "name": t.name,
                "source": str(t.source) if t.source else None,
                # the org file's own definition, which the card's Members… may edit (TD-172); a repo's is read
                "in_org": bool(t.source and org.path and Path(t.source) == Path(org.path)),
                "projects": list(t.projects),
                "manager": t.manager.name if t.manager.role != orgmod.PERSON else "person",
                # a seat on call, filled when a member needs a reading, or a standing session (§4.9, TD-247)
                "on_call": t.manager.on_call,
                "techlead": t.techlead.name if t.techlead else None,  # the seat (§4.9b), if any
                # the anchor seat (§4.9b, TD-381): the name and the home repo, None under `anchor: false`
                "anchor": {"name": t.anchor.name, "home": t.anchor.home} if t.anchor else None,
                # the role Add entry's **Open a session** starts, per Type (§4.9, TD-219), each said
                "entries": {k: t.entry_role(k) for k in orgmod.ENTRY_TYPES},
                # seats with a trigger (§4.9b, TD-098): what the manager reads to fill each one
                "seats": [{"name": s.name, "role": s.role, "trigger": s.trigger, "after": s.after} for s in t.seats],
                "members": sum(len(m.names()) for m in t.members if m.team is None),
                # the definition's roles in its order, each with the names that hold it — the team's
                # *who for what* lines (design §4.5a ***i*** mark, TD-171)
                "roles": role_holders(t),
                "live": n_live,
                # only when nothing is live: a team still running is described by what it is doing
                "wound_down": down,
                # …who closed it, how soon and why (§4.5a *wound down* note, TD-265): only when it did
                **(wound_down_note(t, mine, names) if down else {}),
                # …and whether rule 9 ended it: its manager carries the tick's mark (§6, TD-241)
                "by_tick": not n_live and any(closed_finished(s) and not s.get("superseded_by") for s in mine),
                # …and when something is live but every live session is idle and declared (TD-099)
                "concluded": {k: f[k] for k in ("at", "restart", "names")} if f and not f["why"] else None,
                # …and when it is not, what keeps it from the reading, a clause per session (TD-241)
                "not_concluded": list(f["why"]) if f else [],
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


def repos_via(call: Call) -> orgmod.ReposOf:
    """How the aggregate asks another host for its registered checkouts (design §4.9 *Where a
    repo's team lands*, TD-229 slice 3): the home's `host_repos`. Any refusal — the host
    unreachable, a node on a build without the `repos` link method — is `OSError`, which the
    landing reads as *unknown*, never as *holds no repo*."""

    def repos_of(host: str) -> list[str]:
        try:
            got = call("host_repos", host=host)
        except Exception as e:  # noqa: BLE001 — whatever the transport raised, the registry is unknown
            raise OSError(str(e)) from e
        repos = (got or {}).get("repos")
        if not isinstance(repos, list):
            raise OSError(f"{host} answered its registry with no list of checkouts")
        return [str(r) for r in repos]

    return repos_of


NOTHING_TO_PICK = "nothing to pick: every member's lane is empty, so the team will wind down as soon as it starts"


def lanes(call: Call, org: orgmod.Org, name: str, host: str) -> dict[str, Any] | None:
    """What the members' lanes hold before a start (design §4.5a team card **Start**, *What the lanes
    hold, before the start*; TD-262, built by TD-265): each role with a lane, once, read against its
    checkout's ledger as rules 6 and 8 read it — `ledger.lane_matches` over the home's `repos`
    reading — as `{line, empty, roles}`: *grinder: 3 pickable · designer: 0*. `empty` only when every
    lane was read and none matched; a ledger that cannot be read is *unknown*, never empty. None when
    there is nothing to say: no member has a lane, or the plan itself fails, which the start reports
    in its own words."""
    try:
        plan = teams.plan(org, name, host, files=files_via(call))
    except (teams.TeamError, ValueError, OSError):
        return None
    holders = [x for x in plan.members if x.lane]
    if not holders:
        return None
    try:
        got = call("repos")
        readings = got if isinstance(got, dict) else {}
    except Exception:  # noqa: BLE001 — no reading is every lane unknown, never a refused start
        readings = {}
    roles: dict[str, int | None] = {}
    for x in holders:
        if x.role in roles:
            continue  # a role once: its members share a lane (§4.9)
        led = (readings.get(str(x.dir)) or {}).get("ledger") or {}
        entries = led.get("entries") if "error" not in led else None
        roles[x.role] = (
            sum(1 for e in entries if isinstance(e, dict) and ledger_mod.lane_matches(x.lane, e))
            if isinstance(entries, list)
            else None
        )
    line = " · ".join(
        f"{role}: {'ledger unreadable' if n is None else (f'{n} pickable' if n else '0')}" for role, n in roles.items()
    )
    return {"line": line, "empty": all(n == 0 for n in roles.values()), "roles": roles}


def keep_mail_for(
    call: Call, verdicts: Mapping[str, Mapping[str, Any]], caller: str | None, *, concluded: bool = True
) -> tuple[set[str], list[str]]:
    """The members whose create asks `keep_mail` (§4.10 *The name coming back adopts it*, TD-274):
    each whose name a **closed** record holds — or, with `concluded`, a live one a Start on a
    concluded team closes first — so a lapse note or an answer left there while the name was gone
    is the successor's. Asked only where the host agent grants it, a person or one of that record's
    controllers (§4.9b, §9 invariant 9), so the start stays all or nothing; anyone else's member
    starts empty, as §4.1 has it, and a note names it and why. Returns `(names, notes)`."""
    closed = {
        n: v for n, v in verdicts.items() if v.get("verdict") == "supersede" and v.get("holder_state") == "closed"
    }
    if concluded:
        closed |= {n: v for n, v in verdicts.items() if v.get("verdict") == "live"}
    if not closed or caller is None:
        return set(closed), []
    ctl = {str(s.get("id")): s.get("controllers") or [] for s in call("list")}
    keeps, notes = set(), []
    for n, v in closed.items():
        holder = str(v.get("holder") or "")
        if caller in ctl.get(holder, []):
            keeps.add(n)
        else:
            notes.append(
                f"{n} starts with an empty mailbox: {caller} is not a controller of {holder}, the closed record "
                "whose mail it would keep — a person's start keeps it (design §4.9b)"
            )
    return keeps, notes


def start(
    call: Call, org: orgmod.Org, name: str, host: str, *, profile: str | None = None, caller: str | None = None
) -> tuple[teams.Plan, dict[str, Any]]:
    """`ao team start <name>` and the strip's **Start** (design §4.9): resolve the definition, check
    *everything* — checkouts, roles, profiles, briefs, and every session name under §4.1's rule —
    then create the lead and each member with `controllers: [lead id]`.

    Raises `TeamError` (or `NamesHeld`) before anything is created, and `PartialStart` if a create
    fails after the checks passed. Returns the plan and the result the callers render. `caller` is
    the session running it (`AGENTORC_SESSION`), None for a person: it decides where a closed
    member's mail is kept (`keep_mail_for`)."""
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
    verdicts = {x.name: call("name_check", dir=str(x.dir), name=x.name, repo=str(x.dir), **on) for x in plan.launches}
    held = [v for v in verdicts.values() if v.get("verdict") in ("live", "suspended")]
    # Only when a name is held: a concluded team's declared sessions hold its members' names, so a
    # concluded team whose names are all free would be one whose definition renamed every member.
    # who asks a closed member's mail kept, decided before anything is closed or created (TD-274)
    keeps, notes = keep_mail_for(call, verdicts, caller)
    closed = _close_concluded(call, org, name, held) if held else []

    def params(x: teams.Launch, controllers: list[str]) -> dict[str, Any]:
        return {**x.create_params(controllers), **({"keep_mail": True} if x.name in keeps else {})}

    created: list[dict[str, Any]] = []
    notes += plan.notes  # said, and the start goes ahead (a seat without its primer, §4.9b)
    lead_id = ""
    try:
        if plan.lead:
            # A manager on call (§6 rule 3 *A Start writes the seat and never fills it*, TD-410): its
            # record written alone, closed with no pane, the launch record beside it — the tick fills
            # it the first time a reading is due, a question in the mail kept here included. Its id
            # is the name's either way, so every member's `controllers` and `{manager}` name it.
            rec = call("create", **params(plan.lead, []), **on_call_gate(plan.lead))
            created.append(rec)
            lead_id = str(rec["id"])
            if plan.manager_id and lead_id != plan.manager_id:
                notes.append(
                    f"the manager started as {lead_id}, but the members' briefs name {plan.manager_id} — "
                    "a stale tmux session holds that id; its members' done lines will not reach it until a restart"
                )
        if plan.techlead:
            # The seat is its manager's member (design §4.9b): the manager is its controller, as
            # for any member. Its id was named in every brief before it existed (`{techlead}`).
            rec = call("create", **params(plan.techlead, [lead_id] if lead_id else []))
            created.append(rec)
            if str(rec["id"]) != plan.techlead_id:
                notes.append(
                    f"the techlead started as {rec['id']}, but the briefs name {plan.techlead_id} — "
                    "a stale tmux session holds that id; tell the team, or restart it once that session is gone"
                )
        if plan.anchor:
            # The anchor seat (§4.9b, TD-381): created after the techlead, in the home checkout itself,
            # gated as §6 rule 3's fill is (TD-395) — a session holding it (§9 invariant 2), or a tree
            # dirty or off its default branch (the person's work) — then the rest of the team starts,
            # the seat's record is written alone, closed with no pane and `seat_held` saying why, and
            # rule 3 fills it once the checkout is free (TD-386), said rather than failing the start
            x = plan.anchor
            held = call("checkout_held", host=x.host, dir=str(x.dir)).get("held")
            if held:
                created.append(call("create", **params(x, [lead_id] if lead_id else []), held=True, held_reason=held))
                notes.append(
                    f"{x.name} waits: {held['why']} ({x.dir}) — the anchor seat works only in a free, clean "
                    "checkout on its default branch (§4.9b); it is on call, filled once the checkout is free"
                )
            else:
                created.append(call("create", **params(x, [lead_id] if lead_id else [])))
        for x in plan.seats:
            # A seat with a trigger (§4.9b, TD-098): started with the team, as the techlead is, so it
            # runs once now and is on call after; its manager fills it again when its trigger is met.
            created.append(call("create", **params(x, [lead_id] if lead_id else [])))
        for m in plan.members:
            # A person runs a team start, so no attenuation applies (§4.8 create rule); a
            # lead running it is subject to it as for any create, in the host agent.
            created.append(call("create", **params(m, [lead_id] if lead_id else [])))
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
        "closed": closed,  # the concluded sessions closed before the creates (§4.9a, TD-099)
        "manager": lead_id or None,
        "sessions": created,
        "out_of_reach": out_of_reach,
        "unrepeatable": list(plan.warnings),
        "notes": notes,
    }


# ── a switch of flow: the records against the compile, and Apply (design §4.9c *Switching*) ───


@dataclass
class Difference:
    """One member whose record differs from what the team's current flow compiles to (§4.9c): `act`
    is what Apply does — `sit_out` (wind it down by the home's `relaunch {sit_out}`), `start` (create
    it as **Members…**'s add does), `relaunch` (hand the home its new launch) — or `left`, what Apply
    never touches (a person's session the flow sits out). `what` names each field that differs."""

    name: str
    act: str
    what: list[str] = field(default_factory=list)
    record: dict[str, Any] | None = None
    launch: teams.Launch | None = None
    seat: bool = False  # a seat the definition names that the run has no record of (§4.9c, TD-399)

    def line(self, flow: str | None) -> str:
        if self.act == "sit_out":
            return f"{self.name}: sits out under {flow}"
        if self.act == "start":
            return f"{self.name}: starts under {flow}" if flow else f"{self.name}: starts"
        if self.act == "left":
            return f"{self.name}: sits out under {flow} — a person's session, left running: close it yourself"
        return f"{self.name}: relaunched — {'; '.join(self.what)}"

    def as_dict(self, flow: str | None) -> dict[str, Any]:
        rec = self.record or {}
        return {
            "name": self.name,
            "id": rec.get("id"),
            "act": self.act,
            "what": list(self.what),
            "line": self.line(flow),
            "seat": self.seat,
        }


def definition_changed(diffs: list[dict[str, Any]], flow: str | None) -> bool:
    """Whether the team header's mark reads **definition changed — Apply** rather than *flow changed*
    (§4.5a, §4.9c, TD-399): the team runs no flow, or every difference is a seat to start."""
    return bool(diffs) and (not flow or all(d.get("seat") and d.get("act") == "start" for d in diffs))


def stage_of(prompt_from: Any) -> str | None:
    """The `{stage}` file a launch's `prompt_from` names, or None where the slot is text (§4.9c)."""
    slots = prompt_from.get("slots") if isinstance(prompt_from, dict) else None
    slot = slots.get(repoconfig.STAGE_PLACEHOLDER) if isinstance(slots, dict) else None
    f = slot.get("file") if isinstance(slot, dict) else None
    return f if isinstance(f, str) else None


def _record_stage(record: dict[str, Any]) -> tuple[bool, str | None]:
    """The stage file a record's `brief` read, as `(known, path)`: a stage brief is a flow's file or a
    template's `<stem>.stage.md`. Unknown where the home recorded no `brief` (a node member's, an
    unreadable file): nothing is claimed about what was not read."""
    sources = (record.get("brief") or {}).get("sources") if isinstance(record.get("brief"), dict) else None
    if not isinstance(sources, list):
        return False, None
    for src in sources:
        path = src.get("path") if isinstance(src, dict) else None
        if isinstance(path, str) and _is_stage(Path(path)):
            return True, path
    return True, None


def _is_stage(path: Path) -> bool:
    """A template's `<stem>.stage.md`, or a brief of a flow: the package's, the org's `~/.agentorc/flows/`
    or a repo's `.agentorc/flows/`."""
    if path.name.endswith(repoconfig.STAGE_SUFFIX) or path.is_relative_to(flowdefs.PACKAGE_DIR):
        return True
    if path.is_relative_to(flowdefs.org_dir()):
        return True
    parts = path.parts
    return any(parts[i : i + 2] == flowdefs.REPO_DIR.parts for i in range(len(parts) - 1))


def _short(path: str | None) -> str:
    return "/".join(Path(path).parts[-2:]) if path else "none"


def _reader(review: Any) -> str:
    if isinstance(review, dict) and isinstance(review.get("chain"), list):  # a flow's chain (§4.9c, TD-315)
        return " then ".join(str(x.get("reader") or "?") for x in review["chain"] if isinstance(x, dict)) or "none"
    return str(review.get("reader") or "none") if isinstance(review, dict) else "none"


def _current(name: str, records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The record a member's name stands for: the live one, else the newest not resumed as another."""
    mine = [r for r in records if r.get("name") == name and not r.get("superseded_by")]
    up = [r for r in mine if r.get("state") not in DEAD]
    return (up or mine or [None])[-1]


def _next_run(record: dict[str, Any]) -> dict[str, Any]:
    """The lane and `review` a member runs next: what a person's Apply handed the home, carried on the
    record's `relaunch` mark until the create that runs it (§4.9c *Switching*, TD-355), else the
    record's own. A mark written before it carried them (`{at}` alone) says nothing of them."""
    mark = record.get("relaunch")
    return mark if isinstance(mark, dict) and "lane" in mark and "review" in mark else record


def _review_key(review: Any) -> Any:
    """A `review` as the flow means it (§4.9c): its reader, or each chain link's reader in order, each
    with its `held` as a set, and its bound — so a record whose `held` is the compiled set in another
    order is no difference (`teams.flow_redundant` compares so)."""
    if not isinstance(review, dict) or not review:
        return None
    bound = review.get("bound", teams.REVIEW_BOUND)
    if isinstance(review.get("chain"), list):
        links = tuple((x.get("reader"), frozenset(x.get("held") or ())) for x in review["chain"] if isinstance(x, dict))
        return ("chain", links, bound)
    return ("reader", review.get("reader"), frozenset(review.get("held") or ()), bound)


def differences(plan: teams.Plan, sessions: list[dict[str, Any]]) -> list[Difference]:
    """How a live team's records differ from what its current flow compiles to (design §4.9c
    *Switching*), member by member: lane, `review`, the `{stage}` file's path, and who should be
    running — never `{flow}`'s text, which names no member, and nothing of a person's session but
    that the flow sits it out. A seat the definition names — the techlead, the anchor, a `seats:`
    entry, a manager on call (§6 rule 3, TD-410) — with no record is a `start` on any live team (§4.9c,
    TD-399), and a team that runs no flow is compared on its seats alone. Empty with nothing live."""
    mine = badged(plan.team, sessions)
    if not live(crew(plan.team, sessions)):
        return []
    on_call_lead = plan.lead if plan.lead is not None and on_call_gate(plan.lead) else None
    seats = {x.name for x in (plan.techlead, plan.anchor, on_call_lead, *plan.seats) if x is not None}
    if plan.flow is None:
        return [
            Difference(x.name, "start", launch=x, seat=True)
            for x in plan.launches
            if x.name in seats and _current(x.name, mine) is None
        ]
    out: list[Difference] = []
    for name in plan.sit_out:
        rec = _current(name, mine)
        if rec is None or rec.get("state") == "closed" or rec.get("sit_out") or sat_out(rec):
            continue
        if rec.get("seat") is not None or rec.get("state") == "scheduled":
            continue  # the home never sits one out (`_sit_out`)
        out.append(Difference(name, "left" if persons(rec) else "sit_out", record=rec))
    for x in plan.launches:
        rec = _current(x.name, mine)
        if rec is None or sat_out(rec):
            # a seat the run lacks — the anchor a team predates among them — is Apply's to create (TD-399)
            out.append(Difference(x.name, "start", record=rec, launch=x, seat=rec is None and x.name in seats))
            continue
        if rec.get("sit_out"):
            continue  # still winding down from a sit-out: once the home has closed it, it reads `start`
        if persons(rec) or rec.get("state") == "closed":
            continue  # a person's lane and brief are theirs; a closed member is the next Start's
        what = []
        runs = _next_run(rec)
        if list(runs.get("lane") or []) != list(x.lane):
            what.append(f"lane {', '.join(runs.get('lane') or []) or 'none'} → {', '.join(x.lane) or 'none'}")
        if _review_key(runs.get("review")) != _review_key(x.review):
            what.append(f"reader {_reader(runs.get('review'))} → {_reader(x.review)}")
        known, was = _record_stage(rec)
        now = stage_of(x.prompt_from)
        if known and was != now:
            what.append(f"stage {_short(was)} → {_short(now)}")
        if what:
            out.append(Difference(x.name, "relaunch", what, record=rec, launch=x))
    return out


def flow_changed(
    call: Call, org: orgmod.Org, name: str, host: str, sessions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """What a client that reads a live team says of its records against its current flow (§4.9c
    *Switching*: the Org page, `ao team list`, `ao team flow`), one `Difference.as_dict` per member;
    empty with nothing live, which compiles nothing until its next start; a team with no `flows:` is
    read on its seats alone (TD-399). Raises what `teams.plan` raises: the caller says why the records
    were not compared."""
    t = org.teams.get(name)
    if t is None or not live(crew(name, sessions)):
        return []
    plan = teams.plan(org, name, host, files=files_via(call))
    return [d.as_dict(plan.flow) for d in differences(plan, sessions)]


def flow_view(call: Call, org: orgmod.Org, name: str, here: str, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """A team's flows as every client that reads a team shows them (design §4.9c *What is shown*,
    *Switching*; `ao team list`, the Org page's team header): `flow`, the current one or None for a
    team that lists none; `flows`, `teams.flow_rows`; `flow_note`, `teams.flow_unlisted`; and
    `differences`, a live team's records against its current flow (`flow_changed`) — empty where the
    plan fails, which the flows' own lines say. Reads files: once per page load or listing, never
    per delta."""
    t = org.teams.get(name)
    if t is None:
        return {"flow": None, "flows": [], "flow_note": "", "differences": []}
    if not t.flows:  # no flow: its seats alone, which a live team may lack (§4.9c, TD-399)
        out: dict[str, Any] = {"flow": None, "flows": [], "flow_note": "", "differences": []}
        with contextlib.suppress(teams.TeamError, ValueError, OSError, AgentError):
            out["differences"] = flow_changed(call, org, name, here, sessions)
        return out
    out = {
        "flow": teams.current_flow(t),
        "flows": teams.flow_rows(org, t, t.host or here, here),
        "flow_note": teams.flow_unlisted(t),
        "differences": [],
    }
    with contextlib.suppress(teams.TeamError, ValueError, OSError, AgentError):  # said by the flows' lines
        out["differences"] = flow_changed(call, org, name, here, sessions)
    return out


def check_pick(call: Call, org: orgmod.Org, name: str, here: str, flow: str) -> orgmod.Org:
    """A pick of `flow` checked before it is written (design §4.5a team card **Flow** pick, the
    Settings page's **flow**; §4.9c *Switching*): refused, in `teams.TeamError`, for a flow the team
    does not list or one it cannot follow — the pick draws those disabled, with the reason. Returns
    the org as if picked."""
    t = teams.find(org, name)
    if flow not in t.flows:
        raise teams.TeamError(f"team {name} lists {', '.join(t.flows) or 'no flows'}, not {flow!r} (design §4.9c)")
    picked = orgmod.with_settings(org, {name: {"flow": flow}})
    rows = teams.flow_rows(picked, picked.teams[name], t.host or here, here, files=files_via(call))
    if cannot := next((r["cannot"] for r in rows if r["name"] == flow), ""):
        raise teams.TeamError(cannot)
    return picked


def pick_flow(call: Call, org: orgmod.Org, name: str, here: str, flow: str) -> dict[str, Any]:
    """The **Flow** pick, the Settings page's **flow** on Save and `ao team flow <team> <flow>` (§4.5a,
    §4.7, §4.9c *Switching*): checked (`check_pick`), written to `teams.<team>.flow` through
    `set_settings` — a person's own — and nothing more: Apply is the one gate that moves the running
    team (TD-356). Returns `{team, flow, differences, feature}` read against the written org — the
    records' differences (`flow_changed`, empty where they cannot be read: the flows' own lines say
    why), and `feature`, the role **Add entry**'s *feature* now opens where the pick moved it (§4.9c
    item 4), else None. What a switch leaves with a reader is Apply's to say (`apply`'s `stays`, §4.9c
    *What a switch leaves alone*), since a pick moves nothing (TD-365)."""
    check_pick(call, org, name, here, flow)
    t = teams.find(org, name)
    host = t.host or here
    was = _feature_role(org, t, host, here, call)
    got = call("set_settings", teams={name: {"flow": flow}})
    written = orgmod.with_settings(org, (got or {}).get("teams"))
    sessions = call("list")
    diffs: list[dict[str, Any]] = []
    with contextlib.suppress(teams.TeamError, ValueError, OSError, AgentError):  # said by the flows' lines
        diffs = flow_changed(call, written, name, here, sessions)
    now = _feature_role(written, written.teams[name], host, here, call)
    return {
        "team": name,
        "flow": flow,
        "differences": diffs,
        "feature": now if now != was else None,
    }


def _feature_role(org: orgmod.Org, team: orgmod.TeamDef, host: str, here: str, call: Call) -> str | None:
    try:
        return teams.entry_role(org, team, "feature", host, here, files=files_via(call))
    except (teams.TeamError, ValueError, OSError, AgentError):
        return None


def work_orders(r: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """A repo reading's work orders — its board's open decided lines, `board:<key>` (§4.4 *Board
    write-back*, TD-384) — as entries; none from an agent that predates them."""
    got = ((r or {}).get("work_orders") or {}).get("orders")
    return [e for e in got if isinstance(e, dict) and e.get("id")] if isinstance(got, list) else []


def lane_entries(r: Mapping[str, Any] | None) -> list[dict[str, Any]] | None:
    """What a team's lanes read of a repo (§4.4 *In a team's lanes*): the ledger's open entries and
    the board's work orders beside them (TD-384), or None where the ledger was not read."""
    entries = ((r or {}).get("ledger") or {}).get("entries")
    return None if entries is None else [*entries, *work_orders(r)]


def lane_kinds(r: Mapping[str, Any] | None) -> dict[str, Any]:
    """The ledger's counts by the page's kind, a work order counted *pickable* (§4.5a *Repo page:
    decided lines*, TD-384) — never in the priorities, which are the ledger's alone."""
    kinds = dict(((r or {}).get("ledger") or {}).get("by_kind") or {})
    if n := len(work_orders(r)):
        kinds["pickable"] = int(kinds.get("pickable") or 0) + n
    return kinds


def repo_lanes(
    entries: list[dict[str, Any]] | None, root: str | Path, sessions: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Each team servicing a repo, and what its lanes take of the repo's open entries (design §4.4
    *In a team's lanes*, TD-361): `ledger_mod.in_lanes` over the team's records in the repo — live or
    ended, never one resumed as another — with what a live record of the repo holds claimed. A team
    whose records carry no lane, and a repo whose ledger was not read, give nothing."""
    if entries is None:
        return {}
    here = Path(root).resolve()
    mine = [
        s for s in sessions if s.get("repo") and Path(str(s["repo"])).resolve() == here and not s.get("superseded_by")
    ]
    held = {
        str(p.get("ref"))
        for s in mine
        if s.get("state") not in DEAD
        for p in s.get("progress") or []
        if isinstance(p, dict) and p.get("status") == "claimed"
    }
    out: dict[str, dict[str, Any]] = {}
    for team in sorted({str(s["team"]) for s in mine if s.get("team")}):
        got = ledger_mod.in_lanes(entries, [s for s in mine if s.get("team") == team], held)
        if got is not None:
            out[team] = got
    return out


def stays_with(name: str, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What a switch leaves with a reader (design §4.9c *What a switch leaves alone*): each open PR
    `ask` at a session of the team, from the reader's `prs_waiting.asks` (§4.9b, TD-333) in the order
    they came — `{from, pr, reader, line}`, the line *grinder-ao-1's PR #1020 stays with
    techlead-ao-1*. The sender is named by its record where this home lists it, else by its address.
    A reader not running now is still the reader: a seat comes back on its next question (§4.9b)."""
    names = {str(s["id"]): str(s.get("name") or s["id"]) for s in sessions if s.get("id")}
    out: list[dict[str, Any]] = []
    for s in badged(name, sessions):
        waiting = s.get("prs_waiting")
        if s.get("superseded_by") or not isinstance(waiting, dict):
            continue
        reader = str(s.get("name") or s.get("id"))
        for a in waiting.get("asks") or []:
            sent = str(a.get("from"))
            # this host's own sender may be addressed `id@home`, which the list names by its bare id
            sender = names.get(sent) or names.get(naming.split_address(sent)[0], sent)
            line = f"{sender}'s PR #{a.get('pr')} stays with {reader}"
            out.append({"from": a.get("from"), "pr": a.get("pr"), "reader": reader, "line": line})
    return out


def relaunch_params(x: teams.Launch) -> dict[str, Any]:
    """The `relaunch` RPC's `launch`: the four keys it replaces, an absent one sent as None so the
    home removes it (a switch to `build` takes `review` off)."""
    return {"prompt": x.prompt, "prompt_from": x.prompt_from, "lane": list(x.lane), "review": x.review}


def apply(call: Call, org: orgmod.Org, name: str, host: str, *, caller: str | None = None) -> dict[str, Any]:
    """**Apply** and `ao team flow <team> --apply` (design §4.9c *Switching*): compile the team's
    current flow, read its records against it, and per member sit it out, start it or relaunch it —
    a person's own, since the home's `relaunch` is. A member that cannot be reached, or whose call is
    refused, is skipped and said; the others are applied. Returns `{team, flow, applied, skipped}`,
    each entry `{name, act, line}`, and `stays`, the PRs already asked of a reader, which stay its
    (`stays_with`); nothing applied for a stopped team, whose next start compiles the flow."""
    team = teams.find(org, name)
    sessions = call("list")
    stays = stays_with(name, sessions)
    if not live(crew(name, sessions)):  # a stopped team: its next start compiles the flow
        flow = teams.current_flow(team)
        return {"team": name, "flow": flow, "applied": [], "skipped": [], "stays": stays}
    plan = teams.plan(org, name, host, files=files_via(call))
    diffs = differences(plan, sessions)
    # a manager on call is the controller closed as live (TD-269): the members Apply starts name it
    lead = next(
        (
            s
            for s in badged(name, sessions)
            if (s["state"] not in DEAD or on_call(s)) and plan.manager_id in (s.get("name"), s.get("id"))
        ),
        None,
    )
    lead_id = str(lead["id"]) if lead else ""
    out: dict[str, Any] = {"team": name, "flow": plan.flow, "applied": [], "skipped": [], "stays": stays}
    for d in diffs:
        row = d.as_dict(plan.flow)
        rec = d.record or {}
        if d.act == "left":
            out["skipped"].append(row)
            continue
        if rec.get("state") == "unreachable":
            row["line"] = f"{d.name}: {rec.get('host') or 'its host'} is not answering — applied to the others"
            out["skipped"].append(row)
            continue
        try:
            if d.act == "sit_out":
                call("relaunch", id=rec["id"], sit_out=True)
            elif d.act == "relaunch":
                call("relaunch", id=rec["id"], launch=relaunch_params(d.launch))
            else:
                x = d.launch
                on = {"host": x.host} if x.host else {}
                verdict = call("name_check", dir=str(x.dir), name=x.name, repo=str(x.dir), **on)
                if verdict.get("verdict") in ("live", "suspended"):
                    raise teams.TeamError(f"held by a live session ({verdict.get('id') or x.name})")
                keeps, said = keep_mail_for(call, {x.name: verdict}, caller, concluded=False)
                keep = {"keep_mail": True} if keeps else {}
                ctl = [lead_id] if lead_id and x is not plan.lead else []
                held = None
                if x is plan.anchor:
                    # the anchor seat, gated as `start` and the fill are (§6 rule 3, TD-395, TD-399): not
                    # free, its record is written alone with `seat_held`, and rule 3 fills it once it is
                    held = call("checkout_held", host=x.host, dir=str(x.dir)).get("held")
                gate = {"held": True, "held_reason": held} if held else on_call_gate(x) if x is plan.lead else {}
                got = call("create", **x.create_params(ctl), **keep, **gate)
                row["id"] = got.get("id")
                if held:
                    row["line"] = f"{x.name}: waits — {held.get('why') or 'the checkout is not free'}"
                elif gate:
                    row["line"] = f"{x.name}: {ON_CALL_LINE}"
                row["line"] += "".join(f" — {n}" for n in said)
        except (AgentError, teams.TeamError) as e:
            row["line"] = f"{d.name}: not applied — {e}"
            out["skipped"].append(row)
            continue
        out["applied"].append(row)
    return out


def _close_concluded(call: Call, org: orgmod.Org, name: str, held: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A person's Start on a **concluded** team (design §4.9a, §4.5a *team groups*, TD-099): the
    names `held` are refused as on any start unless the team is concluded and every holder is one
    of its concluded sessions. Then each of those sessions is closed — all of them, not only the
    ones holding a name — under the wrap-up's own check, so one with uncommitted or unpushed work
    refuses the whole start by name, and nothing is closed until every one has passed it. Returns
    what was closed; raises `NamesHeld` or `TeamError` before anything is closed or created."""
    team = org.teams.get(name)
    mine = crew(name, call("list"))  # a person's session beside a concluded team neither blocks nor is closed
    # a member waiting on the person's answer is not concluded, so a Start never closes it (TD-274)
    done = concluded(mine, seat_names(team, mine), waiting_of_home(call, strict=True)) if team is not None else None
    up = {str(s["id"]): s for s in live(mine)} if done else {}
    still = [v for v in held if v.get("verdict") != "live" or str(v.get("holder")) not in up]
    if still:
        raise NamesHeld(name, still)
    unsafe = [f"{s.get('name') or i} has {why}" for i, s in up.items() if (why := _unsafe_to_close(s))]
    if unsafe:
        raise teams.TeamError(
            f"team {name} was not started — it is concluded, but a session it would close first is not "
            f"clean and pushed: {'; '.join(unsafe)}"
        )
    out = []
    for i, s in up.items():
        call("close", id=i)
        out.append({**_entry(s, "concluded"), "action": "closed", "state": "closed"})
    return out


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
    # A person's session in the team is left alone and named (design §4.9 *A person in the team*):
    # §9 invariant 5 would refuse it anyway, and a stop must not end on that refusal.
    people = [s for s in up if persons(s)]
    up = [s for s in up if not persons(s)]
    if not up and people:
        stays = "; ".join(stays_line(s) for s in people)
        raise teams.TeamError(f"no live unattended session carries the team {name} badge — nothing to stop ({stays})")
    if not up:
        raise teams.TeamError(f"no live session carries the team {name} badge — nothing to stop")
    lead, members = split(name, up, org)
    st = Stopping(team=name, now=now, lead=lead, lead_is_caller=bool(caller and lead and lead["id"] == caller))
    for s in people:
        st.acted.append({**_entry(s, "person"), "action": stays_line(s), "state": s["state"]})
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
    return work_left(record.get("git"))


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


def stays_line(s: dict[str, Any]) -> str:
    """The words Wind down's and Stop now's confirm and `ao team stop` use for a person's session in
    the team (design §4.9 *A person in the team*)."""
    return f"your session {s.get('name') or s['id']} stays: a team act never stops an interactive session"


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


# ── Members… (design §4.9 *Add or remove a member from the team card*, TD-163, built by TD-172) ──


def members_view(
    org: orgmod.Org, name: str, sessions: list[dict[str, Any]], roles: list[str] | None = None
) -> dict[str, Any]:
    """The Members dialog's listing: the manager, the techlead seat, and each member entry as the
    definition writes it — role · name · lane · count — with the sessions holding it and their
    states. `editable` is whether the page may edit it: a team `org.yml` defines, never one a
    repo's `.agentorc.yml` does (*edit it in the repo; this card only reads it*). `next` is Add
    member's default name for each of `roles` (and the members' own), `twice` the names the
    definition holds more than once, each entry holding one marked `twice` (TD-268)."""
    team = teams.find(org, name)
    twice = team.twice_named()
    by_name = {str(s.get("name") or ""): s for s in crew(name, sessions)}

    def held(n: str) -> dict[str, Any]:
        s = by_name.get(n)
        return {"name": n, "id": s["id"] if s else "", "state": s.get("state") if s else "not live"}

    editable = bool(team.source and org.path and Path(team.source) == Path(org.path))
    repo = Path(team.source).parent.name if team.source else "the repo"
    entries = []
    for i, m in enumerate(team.members):
        if m.team is not None:
            entries.append({"index": i, "nested": m.team, "sessions": []})
            continue
        entries.append(
            {
                "index": i,
                "role": m.role,
                "name": m.name,
                "lane": list(m.lane),
                "count": m.count,
                "sessions": [held(n) for n in m.names()],
                "twice": m.names()[-1] in twice,  # its Remove leaves the session to the other entry
            }
        )
    out: dict[str, Any] = {
        "team": team.name,
        "source": str(team.source) if team.source else None,
        "editable": editable,
        # the reason the card's disabled Members… gives (§4.5a *Members… on a repo-defined team*, TD-229)
        "note": "" if editable else f"defined in {repo}'s .agentorc.yml — changed by PR",
        "live": bool(live(crew(name, sessions))),
        "manager": held(team.manager.name) if team.manager.role != orgmod.PERSON else None,
        "techlead": held(team.techlead.name) if team.techlead else None,
        # the anchor seat (§4.5a *card: on call — the anchor seat's words*, TD-387): listed, never removable
        "anchor": held(team.anchor.name) if team.anchor else None,
        "members": entries,
        "twice": twice,
        "next": {r: team.next_name(r) for r in [*(roles or []), *(m.role for m in team.members if m.team is None)]},
    }
    return out


def _names(org: orgmod.Org, name: str) -> list[str]:
    team = teams.find(org, name)
    return [n for m in team.members if m.team is None for n in m.names()]


def _commit(call: Call, message: str) -> None:
    """The home commits the org file with the act's words (design §4.9 *What is left at the home has
    a history*): the edit is written already, so a refusal or an older agent costs only the entry."""
    with contextlib.suppress(Exception):
        call("commit_defs", message=message)


def add_member(
    call: Call,
    path: Path,
    name: str,
    host: str,
    *,
    role: str,
    member: str = "",
    lane: list[str] | None = None,
    caller: str | None = None,
) -> dict[str, Any]:
    """**Add member** (design §4.9): the definition edited as text (`orgmod.edit_members`); on a
    live team, the one new member created under the manager as `ao team start` creates one — the
    plan's own launch, the name check first — so its card appears without a restart. On a stopped
    team, the definition only: the next Start brings the new shape."""
    before = _names(orgmod.load(path), name)
    did = orgmod.edit_members(path, name, add={"role": role, "name": member, "lane": lane or []})
    _commit(call, f"org: {name} {did}")
    org = orgmod.with_home_settings(orgmod.load(path))  # under the picked flow (§4.9c)
    new = [n for n in _names(org, name) if n not in before]
    up = live(crew(name, call("list")))
    out: dict[str, Any] = {"team": name, "did": did, "created": [], "text": f"org.yml: {did}"}
    if not up or not new:
        out["text"] += " — the team is stopped: its next Start brings the member" if not up else ""
        return out
    plan = teams.plan(org, name, host, files=files_via(call))
    sits = [n for n in new if n in plan.sit_out]
    if sits:
        # §4.9c *Members the flow does not use sit out*: written, not started under the current flow
        out["text"] += f" — {', '.join(sits)} sits out under {plan.flow}: not started"
        new = [n for n in new if n not in sits]
        if not new:
            return out
    lead = next((s for s in up if s.get("name") == plan.manager_id or s.get("id") == plan.manager_id), None)
    lead_id = str(lead["id"]) if lead else ""
    for x in [m for m in plan.members if m.name in new]:
        verdict = call("name_check", dir=str(x.dir), name=x.name, repo=str(x.dir))
        if verdict.get("verdict") in ("live", "suspended"):
            raise teams.TeamError(
                f"org.yml: {did}, but {x.name} is held by a live session, so nothing was created — "
                "the definition names it for the next Start"
            )
        keeps, said = keep_mail_for(call, {x.name: verdict}, caller, concluded=False)
        keep = {"keep_mail": True} if keeps else {}
        out["created"].append(call("create", **x.create_params([lead_id] if lead_id else []), **keep))
        out["text"] += "".join(f" — {n}" for n in said)
    out["text"] += (
        f" — started {', '.join(str(r.get('name') or r.get('id')) for r in out['created'])} under {lead_id or 'you'}"
    )
    return out


def remove_member(call: Call, path: Path, name: str, *, index: int, role: str, host: str = "") -> dict[str, Any]:
    """**Remove** (design §4.9): the entry's `count:` decremented — the highest-numbered member goes
    — or its line deleted; a live member so removed is sent Wrap up's wind-down, never a kill, and
    its record stays a card until Forget. A member not live: the definition only. Under a team that
    lists flows, the last member of a role a listed flow's stage needs is refused, naming the flow
    (§4.9c), the file untouched; `host` is where the team's checkout is read."""
    org = orgmod.with_home_settings(orgmod.load(path))  # under the picked flow (§4.9c)
    team = teams.find(org, name)
    if not 0 <= index < len(team.members):
        raise teams.TeamError(f"team {name} has no member entry {index + 1} — reload and try again")
    m = team.members[index]
    if m.team is None and m.role and team.flows:
        left = sum(len(x.names()) for x in team.members if x.team is None and x.role == m.role) - 1
        here = hosts.local_host().name
        need = teams.flow_needs(org, team, m.role, team.host or host or here, here, files_via(call)) if left < 1 else ""
        if need:
            raise teams.TeamError(f"{need} needs a {m.role}: drop {need} from flows: first")
    gone = team.members[index].names()[-1] if team.members[index].team is None else ""
    did = orgmod.edit_members(path, name, remove=index, role=role)
    _commit(call, f"org: {name} {did}")
    out: dict[str, Any] = {"team": name, "did": did, "wound_down": None, "text": f"org.yml: {did}"}
    if gone and gone in teams.find(orgmod.load(path), name).session_names():
        # a name the definition held twice: another entry still names the session, so it runs on (TD-268)
        out["text"] += f" — another entry still names {gone}, so it runs on"
        return out
    s = next((s for s in live(crew(name, call("list"))) if s.get("name") == gone), None)
    if s is not None:
        out["wound_down"] = _stop_one(call, s, "member", now=False)
        out["text"] += f" — {gone} is sent the wrap-up; its card stays until Forget"
    return out
