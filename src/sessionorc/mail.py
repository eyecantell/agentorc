"""The gates, written as functions over a record map (design §4.4a, TD-057 step 1).

Both gates read several records at once — the caller's grant, the target's `controllers`, a
shared controlled target — and the process that holds every record is the home host agent
(§4.4a). Writing them over a plain mapping rather than as methods reaching into the agent is what
lets a node forward a request and the home answer it from one graph. Nothing here mutates a record
or touches the store: each function returns a reason, or None when the call passes. Every gate
takes `controllers`, how a record's `controllers` read from the caller's host (§4.4a "Every address
crosses in the reader's form", step 5): the home passes a function that re-addresses another host's
record's list into its own form, and the records themselves are never copied.

Every number the mail rules need was `None` — unlimited — until TD-052 step 5 measured a team and
step 6 set it from the evidence (2026-09-18, design §4.10 "The numbers"). What was measured: eight
hours of a four-session team, a lead over three free-pick grinders, on 2026-09-17.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from sessionorc.models import GRANTS, PERSON, SYSTEM, MailEntry, Session, has_control, start_note

# -- bounds (design §4.10 "The bounds are part of the design"); numbers are TD-052 step 6's --------
RECIPIENT_CAP = 5  # addressees the *sender* names; automatic copies are exempt
TEXT_CAP = 4096  # bytes of `text`; a `conflict` cites `sends` by id rather than quoting them
THREAD_BOUND: int | None = 40  # entries per thread before a send is refused; measured: no thread past 2
PAIR_BOUND: int | None = 300  # reply-less entries between one pair inside PAIR_WINDOW; measured: 48 in 8 h
PAIR_WINDOW = timedelta(hours=24)  # the rolling window a reply-less pair is counted in
MAILBOX_DEPTH: int | None = 100  # unread entries an inbox holds before a send to it is refused; measured: 19
# The person inbox's depths are two counts from 2026-10-04 (TD-324), each with its own figures.
# **Questions** — the open `ask`s, `steer`s and `conflict`s (`MailEntry.open`) — refuse a question to
# the person, and a pass-up, so one worker cannot fill the Inbox with questions that never lapse.
PERSON_INBOX_DEPTH: int | None = 200  # open questions the person inbox holds before one more is refused
# …and of those, how many one sender may hold there: 100 since 2026-09-28 (Paul), 20 before, when a
# techlead seat's replies to the person were refused for a day with every one of its 20 slots held
PERSON_SENDER_DEPTH: int | None = 100
# **FYIs** — every other entry still in the person inbox: notes, replies, the answered-FYI, a closed
# question — refuse any other send to the person. A person's read marks nothing, so a note counts
# until a Dismiss takes it (a closed question ages out with retention); until 2026-10-04 they
# counted against the questions' figures, and a seat's 100 notes stopped its verdicts and
# escalations reaching the person (Paul).
PERSON_FYI_DEPTH: int | None = 1000  # Paul's figure
PERSON_FYI_SENDER_DEPTH: int | None = 500  # so one looping sender leaves the others room
ASK_BOUND = timedelta(hours=24)  # an `ask`'s default bound, wall-clock on the home's clock
DEFAULT_CAP = 200  # characters of a `steer`'s `default`, cleaned and capped as a `doing` line is (§4.8)
SOURCE_CAP = 200  # characters of a reply's `source` (§4.9b): one line, where the answer is written down
# Suggested answers (§4.10 *Suggested answers*, 2026-09-20, TD-070): how many an `ask`, a `steer`
# or a `conflict` may carry, and how long each may be. They are a field of their own and do not
# count toward `TEXT_CAP`. Each is cleaned **more strictly than displayed text is** (the tail's
# cleaning and also every Unicode format character), because an answer becomes the label of
# something a person presses.
ANSWERS_MAX = 4
ANSWER_CAP = 80
# A look's screenshots (§4.10 *A look*, TD-290, TD-292): up to four repo-relative paths, each a `.png`
# directly under `docs/mockups/reviews/`, matched whole — the page serves that one directory and
# nothing else, so a path of any other shape is refused rather than cleaned into one.
SHOTS_MAX = 4
SHOT_NAME = re.compile(r"[A-Za-z0-9._-]+\.png")  # a screenshot's file name, as the page serves it
SHOT_RE = re.compile(r"docs/mockups/reviews/" + SHOT_NAME.pattern)
OPEN_ASK_ADVICE = 3  # open `ask`s to the person at which `ao msg` advises asking whether this one is a steer
# Outcomes owed to the person before an `ask` or a `steer` to it is refused (design §4.10
# *Outcomes*, TD-079). Ten, because the remedy is one line each and a worker with a long night of
# answered questions must be able to keep asking — the debt bounds the *unreported*, never the
# mailbox: an owing question does not hold its sender's slot in the person-inbox depths.
OUTCOMES_OWED_MAX: int | None = 10
OUTCOME_STATES = ("done", "blocked", "dropped")  # what an asker may report; the home writes two more
MAIL_RETENTION: timedelta | None = timedelta(hours=12)  # how long a read entry is kept; an open `ask` is exempt
# A reply that carries a `source` (§4.9b) is kept in its sender's outbox this long from when it was
# sent, whatever else is pruned: `ao inbox --sent` is how a techlead started cold answers two
# questions in one night alike, and a seat filled by `--keep-mail` carries the outbox with it.
SOURCED_RETENTION = timedelta(days=7)
SENDS_KEEP = 20  # `sends` entries a record keeps
NONCES_KEEP = 256  # verdicts remembered per host agent for a client's same-nonce retry
WAKE_BUDGET: int | None = 30  # mail-caused wakes a session may take per WAKE_WINDOW; measured: a lead took 4 an hour
WAKE_WINDOW = timedelta(hours=1)  # the rolling window the wake budget counts charged wakes in
WAKES_KEEP = 50  # wake decisions a record keeps (`wakes`): what step 5 measures
# How long a declared claim holds its reference against another live session's claim (design §4.8
# "A claim is a lease", TD-056). Renewed by claiming again; released sooner by done/dropped or the
# holder's record ending. Long enough for one medium TD without a renewal, short enough that a
# stood-down worker does not hold a reference into the next day. The standing of an orphaned
# question (below) reads it as the claim's refusal does.
LEASE_TTL = timedelta(hours=12)

# RPCs that act on a session (design §4.8): a caller that is a session needs the `control`
# grant to run one of these on a session other than itself (§9 invariant 11). `create` targets a
# session that is by definition not the caller; `set_grants` is gated so a session cannot grant
# itself. `decide` answers another session's permission prompt, the same class of act as typing
# at it (TD-116). Reads are never listed here, and neither is `msg`: messaging is not acting (§4.10).
ACTING_RPCS = frozenset(
    {
        "send",
        "keys",
        "kill",
        "close",
        "set_mode",
        "remove",
        "create",
        "decide",
        "set_grants",
        "set_controllers",
        "set_stop",
        "set_start",
    }
)


# A person's own acts (design §4.8 *A person's own act is a named RPC, and the gate is one list*,
# TD-317): RPCs no session may call, whatever grant or membership it holds. Each begins with
# `agent_common.person_only`, in the RPC and never in the dispatcher, since the link's `act` calls
# an RPC directly; `tests/test_person_only.py` holds this list and those checks to each other.
PERSON_ONLY = frozenset(
    {
        "inbox_delete",
        "inbox_snooze",
        "inbox_hand",
        "inbox_dismiss",
        "attention_snooze",
        "inbox_pause",
        "inbox_resume",
        "inbox_go_with_it",
        "thread",
        "identity_ack",
        "identity_log",
        "suspend",
        "board_edit",
        "board_reply",
        "board_reply_hand",
        "entry_add",
        "settings",
        "set_settings",
        "commit_defs",
        "promote",
        "clear_promote",
        "clear_work",
        "work_start",
        "clear_mark",
        "restart",
        "relaunch",
        "notify_test",
        "forget_host",
        "attach",
    }
)


def is_person(caller: Any) -> bool:
    """`caller is None` — the field absent from the envelope — is the only thing that reads as a
    person. Anything else present is a session, however odd its type: `not caller` would have
    let `"caller": 0` (or `""`, `[]`, `{}`) past both halves of the gate, since the socket
    takes raw JSON from any local process and only `LocalClient` bothers to send a real id
    (review 2026-09-13)."""
    return caller is None


Controllers = Callable[[Session], list[str]]


def _own(s: Session) -> list[str]:
    return s.controllers


def self_decide_refusal(caller: Any) -> str:
    """TD-119, design §4.8: whatever it holds, a session never answers its own permission prompt."""
    return f"{caller} cannot decide itself: a session does not answer its own permission prompt (design §4.8)"


def act_gate(
    records: Mapping[str, Session],
    caller: Any,
    method: str,
    params: Mapping[str, Any],
    *,
    controllers: Controllers = _own,
) -> str | None:
    """Design §4.8, §9 invariant 11: an acting RPC from a session onto a *different* session
    needs the `control` grant on the caller's record **and** the caller in the target's
    `controllers` (TD-036). Both halves are read from the records here, on every call, so a
    revoke or a membership edit takes effect on the session's next call and neither is cached.
    No caller (a person's terminal, the UI) passes; a session acting on itself passes, except
    for the two RPCs that edit authority — a session may no more hand itself a grant than
    remove the controller watching it — and `decide`, which is refused outright on oneself: a
    permission prompt exists so that someone other than the session approves the call (TD-119).
    A caller this agent does not know is a session (the id came from `AGENTORC_SESSION`) and
    holds no grant. Reads are never gated; this is a guard against a confused worker, not a
    security boundary.

    Third, §9 invariant 5 (TD-041): a target that is a person's session (`kind: interactive`
    and not `unattended`) is refused to every session, grant and membership notwithstanding.

    Returns the refusal, or None when the call passes."""
    if is_person(caller) or method not in ACTING_RPCS:
        return None
    if method == "decide" and params.get("id") == caller:
        return self_decide_refusal(caller)
    if method not in ("create", "set_grants", "set_controllers") and params.get("id") == caller:
        return None
    me = records.get(str(caller))
    if me is None or not has_control(me.capabilities):
        target = "a new session" if method == "create" else params.get("id", "?")
        return f"{caller} cannot {method} {target}: needs the control grant (design §4.8)"
    if method == "create":
        # No target to be a member of yet. What create is gated on instead is attenuation: the
        # child's grants must be a subset of the creator's (design §4.8, capability attenuation).
        excess = [
            g for g in dict.fromkeys(params.get("capabilities") or []) if g in GRANTS and g not in me.capabilities
        ]
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
    if target is not None and str(caller) not in (ctl := controllers(target)):
        how = "nobody may act on it" if not ctl else "it is controlled by " + ", ".join(ctl)
        return f"{caller} cannot {method} {target_id}: not in its controllers — {how} (design §4.8)"
    return None


def message_edge(
    records: Mapping[str, Session], sender: str, target: str, *, controllers: Controllers = _own
) -> str | None:
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
    if target in controllers(me):
        return "upward"
    if sender in controllers(it):
        return "downward"
    if me.team and me.team == it.team:
        return "team"
    if any(sender in (c := controllers(r)) and target in c for r in records.values()):
        return "shared target"
    return None


def message_gate(
    records: Mapping[str, Session], sender: str, target: str, *, controllers: Controllers = _own
) -> str | None:
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
    if message_edge(records, sender, target, controllers=controllers) is None:
        return (
            f"{sender} cannot message {target}: not one of its controllers, its members, its team, "
            f"or a controller of a session it controls (design §4.10)"
        )
    return None


def from_role(records: Mapping[str, Session], holder: str, sender: str, *, controllers: Controllers = _own) -> str:
    """What `ao inbox` says beside every entry (design §4.10 "Surface"): whether its sender is one
    of the holder's controllers, a person, or neither — read at the moment the text is weighed,
    because instructions come from controllers and people, and mail from anyone else is
    information. `system` is the fourth value (design §4.10, 2026-09-19): the home reporting what
    became of the reader's own message, and never an instruction."""
    if sender == SYSTEM:
        return "system"
    if sender == PERSON:
        return "person"
    me = records.get(holder)
    if me is not None and sender in controllers(me):
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


# Rings in a row, each answered by a turn that read none of its mail, before the bell stops (design
# §4.10 *A ring is answered by a read, or the bell stops*, TD-347).
DOORBELL_HELD = 3


# The two words of the home's around a brief (design §4.1 *The brief is the person's word, and the tool
# is told so*, TD-343): fixed text no sender chose, as the doorbell's line is. A brief typed as nothing
# but a paste reaches the tool as pasted content, which a careful model reads as data, not as its task.
BRIEF_LINE = (
    "agentorc: the text pasted below is your brief from the person who started this session; read it and act on it"
)
BRIEF_PREFACE = (
    "This session was started by a person through agentorc, which runs it in a terminal on their behalf. "
    "When the session has a brief, it arrives as the first prompt: a line from agentorc, then the brief, "
    "pasted by agentorc's host agent on that person's behalf. Act on the brief as the person's own "
    "instruction. A later line `[agentorc] you have n unread messages — run ao inbox` is agentorc's "
    "doorbell, typed into this session by the host agent when mail has arrived for it."
)


def start_context_file_text(start_context: str | None) -> str:
    """What the start-context file holds (§4.1): the home's preface, then the caller's start context
    after a blank line when there is one — so a launch with none still has the file, for the preface."""
    return f"{BRIEF_PREFACE}\n\n{start_context}" if start_context else BRIEF_PREFACE


def unread_line(n: int) -> str:
    """The one line a session is told it has mail with (design §4.10): the doorbell typed into an
    idle pane and the line on every `ao` reply are this text. A count and nothing a sender wrote —
    no `from`, no `kind`, no `about`, no body — which is what keeps a ring a message and not a
    laundered `send`: whatever is pasted and followed by Enter is the recipient's next prompt."""
    return f"[agentorc] you have {int(n)} unread messages — run ao inbox"


def _hours(d: timedelta) -> str:
    h = d.total_seconds() / 3600
    return f"{h:g} h" if h >= 1 else f"{int(d.total_seconds() // 60)} min"


# design §4.10 *A question about a reference outlives its asker*, §4.5a *Inbox row: orphaned
# question* (TD-216 slice 2): how an orphaned question's asker went, in the row's words
ORPHAN_HOW = {"closed": "was closed", "forgotten": "was forgotten", "cancelled": "was cancelled"}
_NOT_LIVE = ("exited", "closed", "scheduled")  # a record that holds no lease on anything (§4.8)


def lease_holders(ref: str, records: Iterable[Mapping[str, Any]], now: datetime) -> list[Mapping[str, Any]]:
    """Every live record, as the `list` RPC hands it over, with an unexpired declared lease on
    `ref` (design §4.8, `LEASE_TTL`) — the reading `_lease_holders` makes at the home when the
    person's answer is mailed, made again here from the records a page or `ao inbox` already holds."""
    out: list[Mapping[str, Any]] = []
    for r in records:
        if not isinstance(r, Mapping) or r.get("state") in _NOT_LIVE:
            continue
        for p in r.get("progress") or ():
            if not isinstance(p, Mapping) or p.get("ref") != ref or p.get("status") != "claimed":
                continue
            if (p.get("source") or "declared") != "declared":
                continue
            try:
                at = datetime.fromisoformat(str(p.get("at") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if at.tzinfo is not None and now - at < LEASE_TTL and r not in out:
                out.append(r)
    return out


def orphan_standing(e: Mapping[str, Any], records: Iterable[Mapping[str, Any]], now: datetime) -> dict[str, Any] | None:
    """The **standing** of an orphaned question (design §4.5a *Inbox row: orphaned question*), or
    None for an entry that carries no `orphaned`: where the person's answer will go, said before
    the press, in the board row's words — *its session was closed — grinder-ao-2 holds TD-149:
    your answer reaches it, and agentorc's board*, or *… — nobody holds TD-180: your answer is
    written on agentorc's board*. Display only: the home reads the leases again at the press.
    `holders` is `[{id, name}]`, `text` the line."""
    o = e.get("orphaned")
    if not isinstance(o, Mapping):
        return None
    ref = str(o.get("ref") or e.get("about") or "")
    repo = str(o.get("repo") or "")
    board = f"{repo.rstrip('/').rsplit('/', 1)[-1]}'s board" if repo else ""
    held = lease_holders(ref, records, now) if ref else []
    holders = [{"id": str(r.get("id") or ""), "name": str(r.get("name") or r.get("id") or "")} for r in held]
    went = f"its session {ORPHAN_HOW.get(str(o.get('how') or ''), 'is gone')}"
    names = ", ".join(h["name"] for h in holders)
    if holders and board:
        verb, whom = ("holds", "it") if len(holders) == 1 else ("hold", "them")
        text = f"{went} — {names} {verb} {ref}: your answer reaches {whom}, and {board}"
    elif board:
        text = f"{went} — nobody holds {ref}: your answer is written on {board}"
    else:  # the home refuses the press in these words' sense (TD-216 slice 1): no repo, no board
        text = f"{went} and named no repo: there is no board for your answer — Delete declines it"
    return {"ref": ref, "repo": repo, "board": board, "holders": holders, "text": text}


def read_when(
    s: Session | None,
    kind: str,
    now: datetime,
    *,
    person: bool = True,
    seat: bool = False,
    bound: timedelta | None = None,
    unreachable: bool = False,
    rings: bool = True,
    refills: bool = False,
    lapses: bool = True,
    cache: bool = False,
) -> str:
    """**When it is read** (design §4.10 *When it is read: the sentence the sender sees*, TD-158,
    built by TD-168): one sentence saying when a message of `kind` to `s` will be read — the first
    case of the design's table that applies, in the doorbell's own order (`_bell_blocked`, then the
    budget), so it never promises a ring the doorbell would not give. A pure function of the record
    and the kind, never stored. `s` None with `seat` is a seat nobody fills (the placeholder card);
    `person` is whether the sender is a person, whose message refills the budget (*Time and a
    person restore it*), so only a session's reads *budget spent*; `unreachable` is the home's word
    that the record's host link is down (§4.4a), which the record's own state may not say yet;
    `rings` is whether its adapter has a composer the doorbell can type into (`_bell_blocked`).
    `refills` is a person's `reply` on a handed entry's thread (TD-218): to a seat on call it
    closes the seat's question and the entry counts toward the seat again, so it fills it as an
    `ask` does. `lapses` False is an `ask` that carries no bound — a handed entry, which never lapses
    (§4.10 *An entry handed to a seat*) — so the sentence names none. `cache` is the home's word that a
    ring would be a restart instead (§4.10 *A lapsed cache is started again, not rung*, TD-467:
    `agent_common.cache_restarts` on a record of its own host). Advice, never a refusal."""
    ask = kind == "ask"
    tail = f" — an ask takes the default bound of {_hours(bound or ASK_BOUND)}" if ask and lapses else ""
    if s is not None and not mail_wakes(s):
        return (
            "lands in its inbox and wakes nothing: a person's session is never rung (invariant 5)"
            " — the card's unread chip shows it" + tail
        )
    state = s.state if s is not None else "exited"
    if state == "unreachable" or unreachable:
        # before the seat and exited rows: a down host's record reads *unreachable* on its card
        # whatever its last state, so the sentence under that card must too (techlead, PR #583)
        return "lands at the home; its host cannot be reached, so it is delivered when the link is back" + tail
    on_call = (seat or (s is not None and bool(s.seat))) and state in ("exited", "closed")
    if on_call:
        if ask or refills:
            return "fills this seat: a session starts on the next tick and reads it first" + tail
        return (
            "waits in the seat's mailbox: a note fills no seat, and is read at the next fill, which a question causes"
        )
    if s is None or state in ("exited", "closed"):
        return "read when this session is resumed, or started again under this name" + tail
    if state == "scheduled":
        # §6 *Start time* (TD-152): its mail moves to the session it becomes, which reads it first
        at = start_note({"state": state, "start_at": s.start_at}).removeprefix("starts ") or "its start time"
        return f"read when it starts, at {at}" + tail
    stop = s.wrapup_sent_at or s.wrapup_at
    if not stop and s.run_until:
        try:
            stop = datetime.fromisoformat(s.run_until.replace("Z", "+00:00")) <= now
        except ValueError:
            stop = False
    if stop or s.gated:
        why = "it is wrapping up" if stop else "it is paused for usage"
        return f"lands and waits: {why}, and mail never pushes a session past a stop" + tail
    if state == "limited":
        cap = s.pending.text if s.pending and s.pending.text else "a usage cap"
        return f"lands and waits: its account is capped ({cap}), and it is rung after" + tail
    if state == "needs-you":
        what = "question" if s.pending and s.pending.kind == "question" else "permission"
        return f"read once its {what} is answered and its turn ends" + tail
    if state in ("working", "stalled?"):
        return "read when its turn ends: it is rung on the tick after its Stop" + tail
    if s.confidence == "hook" and not rings:
        return "lands; nothing is typed into this tool's pane — read on its next look or its next `ao` reply" + tail
    if s.confidence == "hook":
        # the budget gates the ring alone: a busy session reads its mail at its Stop regardless
        # (review of PR #583)
        if not person and wake_budget_spent(s, now):
            return "lands without waking it: its wake budget is spent, read on its next look" + tail
        if cache:
            return "started again on its brief within a tick, and reads it first" + tail
        return 'rung within a tick: the doorbell types "you have n unread" into its pane' + tail
    return (
        "lands; its idle is a guess from the screen, so nothing is typed into it"
        " — read on its next look or its next `ao` reply" + tail
    )
