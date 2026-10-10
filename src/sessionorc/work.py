"""Rule 8's and rule 9's readings (design §6 *Work for a team that wound down*, TD-227; *Finished
is the home's reading*, TD-241): whether a team wound down and whether a live one has finished,
each read once for the team card and for the home's tick alike, and what its members' lanes
gained since. Adapter-neutral and definition-free: the home reads no org file, so a seat is the
record's `seat` field here and the definition's name on the card, and a test holds the two equal."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from datetime import datetime
from typing import Any

from sessionorc import ledger as ledger_mod
from sessionorc.models import Session, has_control, reference_of

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
        if s.get("name") not in seats and not closed_finished(s) and not sat_out(s)
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


def sat_out(record: Any) -> bool:
    """Whether a record is a member its team's flow sat out (design §4.9c *Switching*): `closed_for: {why:
    sit_out}`. Rules 1, 2 and 7 never recreate it, rule 9 and `wound_down` pass over it whatever it
    declared, and rule 8 watches none of its lanes."""
    mark = record.get("closed_for") if isinstance(record, Mapping) else getattr(record, "closed_for", None)
    return isinstance(mark, Mapping) and mark.get("why") == "sit_out"


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


def finished_alone(records: Iterable[Session]) -> list[Session]:
    """The crew members that finished while their team runs on (design §6 rule 8 *A member that
    finished while its team runs on*, TD-457): not a seat, not the manager, `closed` or `exited`
    after declaring `out_of_work` — closed by rule 9's pass, by its manager or by itself — and not
    one a person closed (its `closer` names the person: *stopped*, left alone) or that was killed (a
    kill destroys the pane, `pane: False`, and writes no closer: never read as a finish), nor one its
    flow sat out, nor a record a successor took over. Only for a team that **runs on** — a seat or a
    member live: one with nobody live that is not wound down is *stopped*, which rule 8 leaves alone."""
    mine = crew(records)
    if not any(r.state not in DEAD for r in mine):
        return []
    manager = manager_of(mine)
    return [
        r
        for r in mine
        if r.seat is None
        and not (r.state == "exited" and r.pane is False)
        and r is not manager
        and r.state in DEAD
        and _said(r, "out_of_work")
        and not r.superseded_by
        and not sat_out(r)
        and (r.closer or {}).get("by") != "person"
    ]


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


WAITING_KINDS = ("ask", "steer")  # the questions a member can wait on the person's answer to (§4.9a)


def waiting_of(entries: Iterable[Any]) -> dict[str, list[dict[str, Any]]]:
    """What each session waits on (design §4.9a *Waiting is read, never declared*, TD-274): from the
    person inbox's entries — the home's `MailEntry`s or a client's dicts — the open `ask`s and
    `steer`s whose `about` names a reference (`reference_of`'s two shapes; prose holds nothing), keyed
    by the sender as the inbox names it, each `{id, ref, bound}`, the sooner bound first and an
    `ask`'s, which has none, last. Read on every call and never stored."""
    out: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        if _f(e, "kind") not in WAITING_KINDS:
            continue
        closed = _f(e, "closed_reason") or _f(e, "closed_by") or _f(e, "expired_at")
        ref = reference_of(_f(e, "about"))
        sender = _f(e, "from_") if not isinstance(e, Mapping) else e.get("from")
        if closed or ref is None or not sender:
            continue
        out.setdefault(str(sender), []).append({"id": _f(e, "id"), "ref": ref, "bound": _f(e, "bound")})
    for qs in out.values():
        qs.sort(key=lambda q: (q["bound"] is None, str(q["bound"] or "")))
    return out


def bound_words(bound: Any) -> str:
    """A question's bound as a person reads it, in this host's clock (*09:57*); empty for none."""
    try:
        return datetime.fromisoformat(str(bound)).astimezone().strftime("%H:%M") if bound else ""
    except ValueError:
        return ""


def waiting_clause(name: str, questions: list[dict[str, Any]]) -> str:
    """A waiting member's clause in `finished`'s `why`: *designer-ao-1 waiting on the person: TD-222,
    until 09:57* — the sooner bound's question, in this host's clock, *and n more* for the rest; an
    `ask` has no bound and says none."""
    q = questions[0]
    until = f", until {hhmm}" if (hhmm := bound_words(q.get("bound"))) else ""
    more = f" and {len(questions) - 1} more" if len(questions) > 1 else ""
    return f"{name} waiting on the person: {q['ref']}{until}{more}"


def finished(
    records: Iterable[Any], seats: Collection[str] = (), waiting: Mapping[str, list[dict[str, Any]]] | None = None
) -> dict[str, Any] | None:
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
      record, and a superseded one, is passed over;
    - a live member **waiting** on the person — `waiting` (`waiting_of` over the person inbox) holds
      a question from it, keyed by its `id` — blocks the reading whatever it declared, its clause
      naming the reference and the bound (§4.9a *Waiting is read, never declared*, TD-274). A
      caller that passes no `waiting` reads nobody as waiting.
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
        if sat_out(r):
            continue  # the flow sat it out (§4.9c): passed over whatever it declared
        name, state = str(_f(r, "name") or _f(r, "id")), str(_f(r, "state"))
        live = state not in DEAD
        if r is manager or _f(r, "seat") is not None or _f(r, "name") in seats:
            if live and state != "idle":
                why.append(f"{name} {state}")
            continue
        wants, out = _said(r, "restart_wanted"), _said(r, "out_of_work")
        asked = (waiting or {}).get(str(_f(r, "id"))) if live else None
        if asked:
            why.append(waiting_clause(name, asked))
            continue
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


def reread(
    member: Session, entries: list[Any], lane: list[str] | None = None
) -> tuple[dict[str, Any] | None, list[str]]:
    """The one reading of a lane's memory (design §6 rule 6 *`lane_seen` is the lane's memory, never
    the ledger's*, TD-407, TD-411), for rule 6, rule 8 and the anchor seat's `work` trigger alike:
    `member`'s `lane_seen` pruned of every id the reading holds and `lane` (the member's own when
    not given) no longer matches — its `dropped` mark with it — and the ids that match now and the
    pruned memory lacks, in the reading's order. An id the reading does not hold is kept: an entry
    absent from the file is archived, or the checkout's moment, never the lane's. The memory comes
    back as the record's own object when nothing was pruned, else a new one, so a caller writes
    only what changed; `(None, [])` before rule 6 has written `lane_seen`, since there is nothing to
    be new against. Reads only: the caller writes."""
    seen = member.lane_seen
    if seen is None:
        return None, []
    lane = member.lane if lane is None else lane
    held: set[str] = set()
    matching: list[str] = []
    for e in entries:
        if not (isinstance(e, dict) and e.get("id")):
            continue
        i = str(e["id"])
        held.add(i)
        if i not in matching and ledger_mod.lane_matches(lane, e):
            matching.append(i)
    ids = list(seen.get("ids") or [])
    left = {i for i in ids if i in held and i not in matching}
    if left:
        seen = {**seen, "ids": [i for i in ids if i not in left]}
        dropped = seen.get("dropped")
        if isinstance(dropped, dict) and any(i in dropped for i in left):
            seen["dropped"] = {i: at for i, at in dropped.items() if i not in left}
    kept = set(seen.get("ids") or [])
    return seen, [i for i in matching if i not in kept]


def gained(member: Session, entries: list[Any]) -> list[str]:
    """The ids in a ledger reading that match `member`'s lane and are not in its `lane_seen` as
    `reread` prunes it, in the ledger's order; none before rule 6 has written `lane_seen`."""
    return reread(member, entries)[1]
