"""How a session's close is said (design §4.5 *The card's anatomy* row 5 (b), §4.5a **doing**, TD-262,
built by TD-265): the record's `closer: {by, why, at}` in the card's words, one function the page
and `ao status -v` both call so the two cannot drift. Plain Python with nothing of the page in it,
since `ao` runs without the `ui` extra."""

from __future__ import annotations

import contextlib
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sessionorc.models import tokens_short

# the tick's closes (§6): rule 9's, rule 2's, rule 7's two (its brief, its team's flow, §4.9c), rule 3's and a
# sit-out's (§4.9c) — rule 8's start closes nothing
TICK_WHY = {
    "finished": "team finished",
    "wanted": "for a restart",
    "brief": "brief changed",
    "flow": "flow changed",
    "sit_out": "flow sat it out",
    "seat": "seat done",
    "cache": "cache lapsed",
}


def closer_words(s: Mapping[str, Any], names: Mapping[str, str] | None = None) -> str:
    """*closed by you* (a person's: the UI, or `ao` outside a session), *closed itself* (the record's
    own id), *closed by <name>* (another session: its name where `names` knows the id, else the id),
    *closed by the tick · <why>* (the home's), and bare *closed* for a record with no closer — every
    one written before the field, and a node older than it. Never raises: a malformed field reads as
    none."""
    c = s.get("closer")
    by = str(c.get("by") or "") if isinstance(c, dict) else ""
    if not by:
        return "closed"
    if by == "person":
        return "closed by you"
    if by == s.get("id"):
        return "closed itself"
    if by == "tick":
        why = TICK_WHY.get(str(c.get("why") or ""))
        return "closed by the tick" + (f" · {why}" if why else "")
    return f"closed by {(names or {}).get(by) or by}"


# The tool's own end, said for a reason it gives without a code (§4.5 row 5 (b), TD-490): the one reason
# §4.5 words for a person; any other (`logout`, `other`) is said as the tool gave it.
EXIT_REASON = {"prompt_input_exit": "the composer closed"}


def _instant(iso: Any) -> datetime | None:
    """An instant off a record, a naive one read as UTC; None for anything unreadable."""
    if not isinstance(iso, str) or not iso:
        return None
    try:
        at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def clock(iso: Any, now: datetime | None = None) -> str:
    """An instant as the host's local clock, *12:31*, with the day once it is not today (*Thu 12:31*) —
    the card's form. "" for anything unreadable."""
    at = _instant(iso)
    if at is None:
        return ""
    with contextlib.suppress(OverflowError, ValueError, OSError):  # a year off a hand-repaired file: UTC
        at = at.astimezone()
    today = (now or datetime.now(UTC)).astimezone().date()
    return ("" if at.date() == today else at.strftime("%a ")) + f"{at:%H:%M}"


def exit_words(s: Mapping[str, Any], names: Mapping[str, str] | None = None, now: datetime | None = None) -> str:
    """**An exit, and how** (design §4.5 row 5 (b), §4.2 the `SessionEnd` row, TD-490), from the record's
    `ended`: *exited · code N* for a dead pane or a tool end that carried its code, *exited · <reason>*
    for the tool's own end without one (*the composer closed* for `prompt_input_exit`), *killed by you* /
    *killed by <name>* / *killed itself* / *killed by the tick · <why>* for a kill, *pane gone · found
    12:31* when the tick found the tmux session gone, and *… by a host agent down since 12:30* when
    that was the agent's first tick after a start; *· after wrap-up* when `wrapup_at` precedes the
    exit. A record with no `ended` (one older than the field, a node older than it) reads *exited*,
    with its `exit_code` where it has one. Never raises: a malformed field reads as none."""
    e = s.get("ended") if isinstance(s.get("ended"), dict) else {}
    how = str(e.get("how") or "")
    code = e.get("code") if "code" in e else (s.get("exit_code") if not how else None)
    code = code if isinstance(code, int) and not isinstance(code, bool) else None
    if how == "kill":
        by = str(e.get("by") or "")
        if by == "person":
            words = "killed by you"
        elif by and by == s.get("id"):
            words = "killed itself"
        elif by == "tick":
            words = "killed by the tick" + (f" · {e['why']}" if isinstance(e.get("why"), str) and e["why"] else "")
        elif by:
            words = f"killed by {(names or {}).get(by) or by}"
        else:
            words = "killed"
    elif how == "gone":
        found = clock(e.get("found") or e.get("at"), now)
        words = "pane gone" + (f" · found {found}" if found else "")
        if found and (down := clock(e.get("down_since"), now)):
            words += f" by a host agent down since {down}"
    elif code is not None:
        words = f"exited · code {code}"
    elif how == "tool" and isinstance(e.get("reason"), str) and e["reason"]:
        words = f"exited · {EXIT_REASON.get(e['reason'], e['reason'])}"
    else:
        words = "exited"
    wrap, at = _instant(s.get("wrapup_at")), _instant(e.get("at"))
    if wrap is not None and at is not None and wrap < at:
        words += " · after wrap-up"
    return words


def ending_hover(s: Mapping[str, Any], names: Mapping[str, str] | None = None, now: datetime | None = None) -> str:
    """The state pill's hover on an `exited` or a `closed` record (§4.5a **state pill hover**, TD-490):
    row 5 (b)'s words and the time — *killed by you · 12:31*, *closed by the tick · team finished ·
    09:12*; a gone pane's words carry their own time. "" for any other state."""
    state = s.get("state")
    if state == "closed":
        words, at = closer_words(s, names), s.get("closed_at")
    elif state == "exited":
        e = s.get("ended") if isinstance(s.get("ended"), dict) else {}
        words, at = exit_words(s, names, now), (None if e.get("how") == "gone" else e.get("at"))
    else:
        return ""
    when = clock(at, now)
    return f"{words} · {when}" if when else words


def restart_words(entry: Mapping[str, Any]) -> str:
    """One `restarts` entry as `ao status -v` says it (design §4.7, §4.10 *A lapsed cache is started
    again, not rung*, TD-467): *cache lapsed · idle 5h · 191k* for the doorbell's restart — the hours
    idle and the context's tokens the entry carries — and the `why` as written for the rest, *· failed*
    where its close or replay did not take. Never raises: a malformed field is left out."""
    why = str(entry.get("why") or "") or "restarted"
    words = why
    if why == "cache":
        words = "cache lapsed"
        if isinstance(idle := entry.get("idle"), int | float) and not isinstance(idle, bool) and math.isfinite(idle):
            words += f" · idle {round(idle)}h"
        if isinstance(tokens := entry.get("context"), int) and not isinstance(tokens, bool):
            words += f" · {tokens_short(tokens)}"
    return words + (" · failed" if entry.get("error") else "")


# why a close can read bare *closed* (§4.5 row 5 (b), design-history §4.4 2026-10-02): the hover says so,
# so a missing closer is not read as a bug
NO_CLOSER = (
    "who closed it is not recorded: a close made before agentorc kept it, on a node older than the field, "
    "or a person's Close made at the node itself, which the home's copy never sees"
)


def declaration(s: Mapping[str, Any]) -> tuple[str, str] | None:
    """A record's declaration (§4.9a) as `(words, why)` — *out of work* or *restart wanted*, and the
    reason as written — or None. What follows an ending on a closed or exited record, since the
    declaration is what explains the close (§4.5 row 5 (b))."""
    for key, words in (("out_of_work", "out of work"), ("restart_wanted", "restart wanted")):
        got = s.get(key)
        if isinstance(got, dict) and got.get("at"):
            return words, str(got.get("why") or "").strip()
    return None


def waiting_words(questions: Any) -> str:
    """*waiting on you: TD-222 until 09:57* (design §4.5a **waiting** mark, §4.9a *Waiting is read,
    never declared*, TD-274): the sooner bound's question — `work.waiting_of`'s order — in this
    host's clock, the day too once it is not today, *and n more* for the rest; an `ask` has no bound
    and says none. "" for no questions, so a caller tests the words. The card, Focus and `ao status
    -v` all say it with this."""
    qs = [q for q in questions if isinstance(q, Mapping) and q.get("ref")] if isinstance(questions, list) else []
    if not qs:
        return ""
    until = ""
    if bound := qs[0].get("bound"):
        try:
            at = datetime.fromisoformat(str(bound).replace("Z", "+00:00")).astimezone()
        except ValueError:
            at = None
        if at is not None:
            day = "" if at.date() == datetime.now().astimezone().date() else at.strftime("%a ")
            until = f" until {day}{at:%H:%M}"
    more = f" and {len(qs) - 1} more" if len(qs) > 1 else ""
    return f"waiting on you: {qs[0]['ref']}{until}{more}"
