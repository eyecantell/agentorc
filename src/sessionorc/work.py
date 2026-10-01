"""Rule 8's and rule 9's readings (design §6 *Work for a team that wound down*, TD-227; *Finished
is the home's reading*, TD-241): whether a team wound down and whether a live one has finished,
each read once for the team card and for the home's tick alike, and what its members' lanes
gained since. Adapter-neutral and definition-free: the home reads no org file, so a seat is the
record's `seat` field here and the definition's name on the card, and a test holds the two equal."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from typing import Any

from sessionorc import ledger as ledger_mod
from sessionorc.models import Session, has_control

DEAD = ("exited", "closed")


def wound_down(sessions: list[dict[str, Any]], seats: Collection[str] = ()) -> str | None:
    """When a team's sessions all declared they were out of work, the latest of those instants
    (design §4.9a, §4.5a **Teams** strip, TD-053 step 6) — else None.

    *Nothing running* and *nothing left to run* are different facts about a team, and only the
    second is an answer: a team stopped by a person, by a clock or by a crash looks identical on the
    strip otherwise. The rule is deliberately all-or-nothing and reads the records rather than
    counting ledger rows, exactly as §4.9a asks: one member's exhaustion is not the team's, and a
    single session that never declared means the team stopped for some other reason. A team with no
    session carrying its badge has never run, or has been forgotten, and is neither.

    `seats` are the names the team's seats run under (§4.9b, TD-075 step 4, TD-098): a seat is empty
    or filled, never finished, so it never declares and is not counted. Nor is a manager the tick
    closed under rule 9 (`closed_for` says `finished`, §6): that rule's premise is a manager that
    never declared, and the team it wound down reads *wound down* all the same.
    """
    seen = [
        d if isinstance(d := s.get("out_of_work"), dict) else {}
        for s in sessions
        if s.get("name") not in seats and not closed_finished(s)
    ]
    if not seen or not all(d.get("at") for d in seen):
        return None
    # `str` before `max`: two declarations of different types would otherwise be a TypeError, and
    # the strip is on the same page as every card (review of PR #203)
    return max(str(d["at"]) for d in seen)


def closed_finished(record: Any) -> bool:
    """Whether a record is a manager rule 9 closed (design §6): `closed_for: {why: finished}`."""
    mark = record.get("closed_for") if isinstance(record, Mapping) else getattr(record, "closed_for", None)
    return isinstance(mark, Mapping) and mark.get("why") == "finished"


def crew(records: Iterable[Session]) -> list[Session]:
    """A team's own records: its unattended ones (§4.9 *A person in the team*) — a person's session
    in the team keeps nothing live and never winds it down. Every record the home holds, as the card
    reads every record the list gives it."""
    return [r for r in records if r.unattended]


def team_wound_down(records: Iterable[Session]) -> str | None:
    """`wound_down` over a team's records as the home holds them: none of its crew live, and every
    one that is not a seat — by the record's `seat` field — having declared `out_of_work`."""
    mine = crew(records)
    if any(r.state not in DEAD for r in mine):
        return None
    seats = {r.name for r in mine if r.seat is not None}
    said = [{"name": r.name, "out_of_work": r.out_of_work, "closed_for": r.closed_for} for r in mine]
    return wound_down(said, seats)


def _f(r: Any, key: str) -> Any:
    """A record's field, off the home's `Session` or off the view a client was given."""
    return r.get(key) if isinstance(r, Mapping) else getattr(r, key, None)


def _said(r: Any, key: str) -> str | None:
    """The instant of a declaration on a record (`out_of_work`, `restart_wanted`), or None: a field
    another build left in any other shape is no declaration, never a raise (review of PR #203)."""
    d = _f(r, key)
    return str(d["at"]) if isinstance(d, Mapping) and d.get("at") else None


def manager_of(records: Collection[Any]) -> Any | None:
    """The team's manager among its own records, read without a definition (§6 rule 9): the record
    that holds `control` and that the team's other records list in `controllers`. Where a lead
    under the manager fits too (manager → lead → worker), it is the one no other such record
    controls; the first by name where that still leaves two. None where a person leads the team.
    Only the team's own records are read: a manager carrying another badge, which no team start
    produces, is not found, and a lead under it would be read as the manager."""
    named = {str(c) for r in records for c in (_f(r, "controllers") or []) if str(c) != str(_f(r, "id"))}
    fit = [r for r in records if has_control(_f(r, "capabilities")) and str(_f(r, "id")) in named]
    ids = {str(_f(r, "id")) for r in fit}
    top = [r for r in fit if not ids & ({str(c) for c in (_f(r, "controllers") or [])} - {str(_f(r, "id"))})]
    return min(top or fit, key=lambda r: str(_f(r, "name") or _f(r, "id")), default=None)


def finished(records: Iterable[Any], seats: Collection[str] = ()) -> dict[str, Any] | None:
    """Whether a live team has finished, read from the records carrying its badge and nothing else
    (design §6 rule 9, §4.9a *A team's finished is the home's reading*, TD-241) — the one reading
    the home's tick and the page's *concluded* both take, so the two never disagree. `records` are
    the home's `Session`s or the views a client holds; `{at, restart, names, why}`, or None for a
    team with no unattended session live, which is `wound_down`'s to describe.

    **It holds when `why` is empty.** `why` is one clause per unattended session that keeps the
    reading from holding, in the records' order — *grinder-dc-1 working*, *grinder-dc-2 idle, not
    declared* — which is the card's *not concluded:* line. `at` is the latest declaration counted,
    or None where nobody declared (a team whose only live sessions are its seats and its manager
    has finished too: nobody is working and nobody has anything to take). `restart` is whether a
    counted member asks for one: the page reads *concluded · restart wanted* then, and rule 9
    winds down only a team where it is false. `names` are the live sessions, which is what a
    Start, or the tick, would close.

    Who is what comes from the records: a person's session is `unattended: false` and counts for
    nothing; a seat is a record with `seat` (a client may add the names its definition gives the
    seats, `seats`, for a record written before the field); the manager is `manager_of`. Then:

    - a **seat** or the **manager**, when live, must be `idle` — declared or not; a seat never
      declares and the manager's word is a judgement about these same records;
    - a live **member** must be `idle` with `out_of_work` or `restart_wanted` on its record: one
      that declared and then took a turn is `working` with the word still there;
    - a dead member with `out_of_work` is counted; one `exited` with its pane and no declaration
      (rule 1's crash), or dead with `restart_wanted` (rule 2's), **blocks** the reading until that
      rule has acted, since a team about to have a member back has not finished; any other dead
      record, and a superseded one, is passed over.
    """
    mine = [r for r in records if _f(r, "unattended") is not False and not _f(r, "superseded_by")]
    up = [r for r in mine if _f(r, "state") not in DEAD]
    if not up:
        return None
    manager = manager_of(mine)
    said: list[str] = []
    restart = False
    why: list[str] = []
    for r in mine:
        name, state = str(_f(r, "name") or _f(r, "id")), str(_f(r, "state"))
        live = state not in DEAD
        if r is manager or _f(r, "seat") is not None or _f(r, "name") in seats:
            if live and state != "idle":
                why.append(f"{name} {state}")
            continue
        wants, out = _said(r, "restart_wanted"), _said(r, "out_of_work")
        if live:
            if state != "idle":
                why.append(f"{name} {state}")
            elif wants is None and out is None:
                why.append(f"{name} idle, not declared")
            else:
                said.append(wants or out or "")
                restart = restart or wants is not None
        elif wants is not None:
            why.append(f"{name} {state}, restart wanted")
        elif out is not None:
            said.append(out)
        elif state == "exited" and _f(r, "pane"):
            why.append(f"{name} crashed")
    return {
        "at": max(said) if said and not why else None,
        "restart": restart,
        "names": sorted(str(_f(r, "name") or _f(r, "id")) for r in up),
        "why": why,
    }


def gained(member: Session, entries: list[Any]) -> list[str]:
    """The ids in a ledger reading that match `member`'s lane and are not in its `lane_seen`, in the
    ledger's order; none before rule 6 has written `lane_seen`, since there is nothing to be new
    against."""
    if member.lane_seen is None:
        return []
    seen = set(member.lane_seen.get("ids") or [])
    return [
        str(e["id"])
        for e in entries
        if isinstance(e, dict) and e.get("id") and str(e["id"]) not in seen and ledger_mod.lane_matches(member.lane, e)
    ]
