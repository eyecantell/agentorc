"""How a session's close is said (design §4.5 *The card's anatomy* row 5 (b), §4.5a **doing**, TD-262,
built by TD-265): the record's `closer: {by, why, at}` in the card's words, one function the page
and `ao status -v` both call so the two cannot drift. Plain Python with nothing of the page in it,
since `ao` runs without the `ui` extra."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# the tick's four closes (§6): rule 9's, rule 2's, rule 7's and rule 3's — rule 8's start closes nothing
TICK_WHY = {"finished": "team finished", "wanted": "for a restart", "brief": "brief changed", "seat": "seat done"}


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
