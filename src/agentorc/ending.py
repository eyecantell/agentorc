"""How a session's close is said (design §4.5 *The card's anatomy* row 5 (b), §4.5a **doing**, TD-262,
built by TD-265): the record's `closer: {by, why, at}` in the card's words, one function the page
and `ao status -v` both call so the two cannot drift. Plain Python with nothing of the page in it,
since `ao` runs without the `ui` extra."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
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
