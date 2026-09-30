"""Rule 8's readings (design §6 *Work for a team that wound down*, TD-227): whether a team wound
down, read once for the team card and for the home's tick alike, and what its members' lanes
gained since. Adapter-neutral and definition-free: the home reads no org file, so a seat is the
record's `seat` field here and the definition's name on the card, and a test holds the two equal."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Any

from sessionorc import ledger as ledger_mod
from sessionorc.models import Session

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
    or filled, never finished, so it never declares and is not counted.
    """
    seen = [d if isinstance(d := s.get("out_of_work"), dict) else {} for s in sessions if s.get("name") not in seats]
    if not seen or not all(d.get("at") for d in seen):
        return None
    # `str` before `max`: two declarations of different types would otherwise be a TypeError, and
    # the strip is on the same page as every card (review of PR #203)
    return max(str(d["at"]) for d in seen)


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
    return wound_down([{"name": r.name, "out_of_work": r.out_of_work} for r in mine], seats)


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
