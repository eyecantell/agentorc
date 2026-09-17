"""The gates, written as functions over a record map (design §4.4a, TD-057 step 1).

Both gates read several records at once — the caller's grant, the target's `controllers`, a
shared controlled target — and the process that holds every record is the home host agent
(§4.4a). Writing them over a plain mapping rather than as methods reaching into the agent is what
lets a node forward a request and the home answer it from one graph. Nothing here mutates a record
or touches the store: each function returns a reason, or None when the call passes.

Every number the mail rules need is a placeholder until TD-052 step 5 measures a team and step 6
sets it from the evidence; `None` means *unlimited*, and the refusal path it guards is built and
tested by monkeypatching the constant.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from sessionorc.models import GRANTS, PERSON, MailEntry, Session

# -- bounds (design §4.10 "The bounds are part of the design"); numbers are TD-052 step 6's --------
RECIPIENT_CAP = 5  # addressees the *sender* names; automatic copies are exempt
TEXT_CAP = 4096  # bytes of `text`; a `conflict` cites `sends` by id rather than quoting them
THREAD_BOUND: int | None = None  # entries per thread (and per pair inside PAIR_WINDOW) before a send is refused
PAIR_WINDOW = timedelta(hours=24)  # the rolling window a reply-less pair is counted in (the wake budget's)
MAILBOX_DEPTH: int | None = None  # unread entries an inbox holds before a send to it is refused
PERSON_INBOX_DEPTH: int | None = None  # unread entries the org's person inbox holds before a send is refused
PERSON_SENDER_DEPTH: int | None = None  # …and of those, how many one sender may hold there
ASK_BOUND = timedelta(hours=24)  # an `ask`'s default bound, wall-clock on the home's clock
MAIL_RETENTION: timedelta | None = None  # how long a read entry is kept; an open `ask` is exempt
SENDS_KEEP = 20  # `sends` entries a record keeps
NONCES_KEEP = 256  # verdicts remembered per host agent for a client's same-nonce retry
WAKE_BUDGET: int | None = None  # mail-caused wakes a session may take per WAKE_WINDOW (§4.10 "wake budget")
WAKE_WINDOW = timedelta(hours=1)  # the rolling window the wake budget counts charged wakes in
WAKES_KEEP = 50  # wake decisions a record keeps (`wakes`): what step 5 measures

# RPCs that act on a session (design §4.8): a caller that is a session needs the `orchestrate`
# grant to run one of these on a session other than itself (§9 invariant 11). `create` targets a
# session that is by definition not the caller; `set_grants` is gated so a session cannot grant
# itself. Reads are never listed here, and neither is `msg`: messaging is not acting (§4.10).
ACTING_RPCS = frozenset(
    {"send", "keys", "kill", "close", "set_mode", "remove", "create", "set_grants", "set_controllers", "set_stop"}
)


def is_person(caller: Any) -> bool:
    """`caller is None` — the field absent from the envelope — is the only thing that reads as a
    person. Anything else present is a session, however odd its type: `not caller` would have
    let `"caller": 0` (or `""`, `[]`, `{}`) past both halves of the gate, since the socket
    takes raw JSON from any local process and only `LocalClient` bothers to send a real id
    (review 2026-09-13)."""
    return caller is None


def act_gate(records: Mapping[str, Session], caller: Any, method: str, params: Mapping[str, Any]) -> str | None:
    """Design §4.8, §9 invariant 11: an acting RPC from a session onto a *different* session
    needs the `orchestrate` grant on the caller's record **and** the caller in the target's
    `controllers` (TD-036). Both halves are read from the records here, on every call, so a
    revoke or a membership edit takes effect on the session's next call and neither is cached.
    No caller (a person's terminal, the UI) passes; a session acting on itself passes, except
    for the two RPCs that edit authority — a session may no more hand itself a grant than
    remove the controller watching it. A caller this agent does not know is a session (the id
    came from `AGENTORC_SESSION`) and holds no grant. Reads are never gated; this is a guard
    against a confused worker, not a security boundary.

    Third, §9 invariant 5 (TD-041): a target that is a person's session (`kind: interactive`
    and not `unattended`) is refused to every session, grant and membership notwithstanding.

    Returns the refusal, or None when the call passes."""
    if is_person(caller) or method not in ACTING_RPCS:
        return None
    if method not in ("create", "set_grants", "set_controllers") and params.get("id") == caller:
        return None
    me = records.get(str(caller))
    if me is None or "orchestrate" not in me.capabilities:
        target = "a new session" if method == "create" else params.get("id", "?")
        return f"{caller} cannot {method} {target}: needs the orchestrate grant (design §4.8)"
    if method == "create":
        # No target to be a member of yet. What create is gated on instead is attenuation: the
        # child's grants must be a subset of the creator's (design §4.8, capability attenuation).
        excess = [g for g in (params.get("capabilities") or []) if g in GRANTS and g not in me.capabilities]
        if excess:
            return (
                f"{caller} cannot create a session holding {', '.join(excess)}: "
                f"a session it creates gets no grant it does not hold itself (design §4.8)"
            )
        return None
    target_id = str(params.get("id", ""))
    target = records.get(target_id)
    # An id this agent has no record of falls through to the method, which answers "no session
    # <id>" — a membership refusal here would say more about the org than the caller may read.
    # A missing `id` lands here as `None` too and is refused by the method rather than by this
    # gate: it is a required argument on every acting RPC and so fails as bad params (pinned by a
    # test; an acting RPC that ever gave `id` a default would need its own membership check here).
    if target is not None and target.kind == "interactive" and not target.unattended:
        # §9 invariant 5 (TD-041): a person's session — `kind: interactive` says conversation,
        # `unattended: false` says not a worker — is out of every session's reach, whatever the
        # grant and whatever `controllers` says, `set_controllers` included. Checked before
        # membership because no edit to the list changes the answer; read from the record on
        # every call, so `ao mode <id> interactive` takes effect on the controller's next call
        # and its list entry merely goes inert. A person (no caller) never reaches this line.
        return (
            f"{caller} cannot {method} {target_id}: it is interactive, and no session acts on an "
            f"interactive session — only a person does (design §9 invariant 5)"
        )
    if target is not None and str(caller) not in target.controllers:
        how = (
            "nobody may act on it" if not target.controllers else "it is controlled by " + ", ".join(target.controllers)
        )
        return f"{caller} cannot {method} {target_id}: not in its controllers — {how} (design §4.8)"
    return None


def message_edge(records: Mapping[str, Session], sender: str, target: str) -> str | None:
    """The edge that lets `sender` message `target` (design §4.10 "The message gate is weaker than
    `control`"), or None when there is none. No new list, no new grant: the graph is read on every
    call, so a membership edit changes who may talk on the next call.

    - **upward**: the target is in the sender's own `controllers`;
    - **downward**: the sender is in the target's `controllers` (its `members:`);
    - **team**: both carry the same non-empty `team` badge — §9 invariant 9's one named exception;
    - **shared target**: some record names both in its `controllers` (two leads over one worker).
    """
    me, it = records.get(sender), records.get(target)
    if me is None or it is None:
        return None
    if target in me.controllers:
        return "upward"
    if sender in it.controllers:
        return "downward"
    if me.team and me.team == it.team:
        return "team"
    if any(sender in r.controllers and target in r.controllers for r in records.values()):
        return "shared target"
    return None


def message_gate(records: Mapping[str, Session], sender: str, target: str) -> str | None:
    """Design §4.10: may `sender` message `target`? A person (`sender == PERSON`) always may — they
    are not a session and may message anyone (§4.8) — and any session may message the person
    inbox (`target == PERSON`). Otherwise a session may message along one of
    `message_edge`'s four edges and nothing else, refused by naming §4.10's graph rather than
    invariant 11: messaging is not acting. Returns the refusal, or None when the message may land."""
    if sender == PERSON or target == PERSON:
        return None  # the person inbox is ungated (§4.10 "A session reaches a person")
    if sender == target:
        return f"{sender} cannot message itself: a note to yourself is a ledger entry (design §4.10)"
    if sender not in records:
        return f"{sender} cannot message {target}: this host agent has no record of the sender (design §4.10)"
    if message_edge(records, sender, target) is None:
        return (
            f"{sender} cannot message {target}: not one of its controllers, its members, its team, "
            f"or a controller of a session it controls (design §4.10)"
        )
    return None


def from_role(records: Mapping[str, Session], holder: str, sender: str) -> str:
    """What `ao inbox` says beside every entry (design §4.10 "Surface"): whether its sender is one
    of the holder's controllers, a person, or neither — read at the moment the text is weighed,
    because instructions come from controllers and people, and mail from anyone else is
    information."""
    if sender == PERSON:
        return "person"
    me = records.get(holder)
    if me is not None and sender in me.controllers:
        return "controller"
    return "other"


# -- the wake decision (design §4.10 "The host agent decides each wake") -------------------------


def undecided_mail(s: Session) -> list[MailEntry]:
    """The unread entries in `s`'s inbox that no wake has covered: those after the `mail_decided`
    watermark. The inbox is in arrival order, so the watermark's id is where the covered part
    ends. If that entry has since left the inbox (a person deleted it, retention pruned it), its
    `at` stands in — compared with `>=`, since `at` is whole seconds: a sibling from the same
    second may be decided twice, a redundant wake, never a missed one."""
    mark = s.mail_decided
    if not mark:
        return [e for e in s.inbox if not e.read_at]
    ids = [e.id for e in s.inbox]
    if mark.get("id") in ids:
        return [e for e in s.inbox[ids.index(mark["id"]) + 1 :] if not e.read_at]
    return [e for e in s.inbox if not e.read_at and e.at >= str(mark.get("at") or "")]


def charged_wakes(s: Session, now: datetime) -> int:
    """Mail-caused wakes inside the rolling window and since the last refill (a person's act
    restores the budget in full; time restores it as the window rolls)."""
    since = (now - WAKE_WINDOW).isoformat(timespec="microseconds")
    if s.wake_refilled_at and s.wake_refilled_at > since:
        since = s.wake_refilled_at
    return sum(1 for w in s.wakes if w.get("charged") and str(w.get("at", "")) > since)


def wake_budget_spent(s: Session, now: datetime) -> bool:
    return WAKE_BUDGET is not None and charged_wakes(s, now) >= WAKE_BUDGET


def mail_wakes(s: Session) -> bool:
    """A person's session is never woken by mail (§9 invariant 5, §4.10 *Done when*): it gets the
    unread line and the chip, and nothing returns or rings for it."""
    return not (s.kind == "interactive" and not s.unattended)
