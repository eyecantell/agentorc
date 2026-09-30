"""A team's balance lines read against the repo's numbers (design §6 *Balance*, TD-239).

The home's tick reads each team whose `teams.<team>.balance` is set against the repo facts it
already keeps (§4.4 *Repo facts*) and the reader's queue on the team's seats, and writes the mark on
its own `host` record. This module is the reading alone — no records, no files, no clock of its own
— so the rule is tested as the numbers it keys on.

A crossed line is `{line, value, limit}`: `prs` in open pull requests, `oldest` and `review` in
seconds, so a reader formats both sides the same way (*oldest PR 3d, line 2d*).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

REVIEW_BOUND = timedelta(hours=2)  # §6 *Balance*: the queue's bound where no live member carries one


def _when(text: Any) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo is not None else None


def span(text: Any) -> timedelta | None:
    """A duration as a setting writes one (`90m`, `12h`, `2d`), or None when it is not one."""
    m = re.fullmatch(r"([1-9][0-9]*)([mhd])", str(text or "").strip())
    if not m:
        return None
    return timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[m.group(2)]: int(m.group(1))})


def crossed(
    balance: dict[str, Any],
    roots: list[str],
    repos: dict[str, dict[str, Any]],
    oldest_waiting: str | None,
    bound: timedelta,
    now: datetime,
) -> tuple[list[dict[str, Any]], str] | None:
    """The lines `balance` crosses, and the repo that crossed them, or None when **it cannot be
    told**: a repo the `prs` or `oldest` line needs has no PR reading, or its last read failed —
    *could not look* is never *under the line* (§6: a failed reading crosses nothing and clears
    nothing, so the caller leaves the mark as it stands). `([], root)` is under every line.

    A team over two repos is read against each and either crossing counts; the first root in
    `roots` that crosses is the mark's `repo`, and a `review` crossing alone names the first root.
    `oldest_waiting` is the oldest `ask` carrying a `pr` at the team's seats (`prs_waiting.oldest`),
    `bound` the shortest `review.bound` of the team's live members."""
    oldest_limit = span(balance.get("oldest"))
    out: list[dict[str, Any]] = []
    where = roots[0] if roots else ""
    unknown = False
    wants_prs = "prs" in balance or oldest_limit is not None
    for root in roots if wants_prs else []:
        prs = (repos.get(root) or {}).get("prs")
        if not isinstance(prs, dict) or "error" in prs or not isinstance(prs.get("open"), list):
            unknown = True
            continue
        mine: list[dict[str, Any]] = []
        n = len(prs["open"])
        if "prs" in balance and n > balance["prs"]:
            mine.append({"line": "prs", "value": n, "limit": balance["prs"]})
        if oldest_limit is not None:
            born = [t for p in prs["open"] if isinstance(p, dict) and (t := _when(p.get("created")))]
            if born and (age := now - min(born)) > oldest_limit:
                limit = int(oldest_limit.total_seconds())
                mine.append({"line": "oldest", "value": int(age.total_seconds()), "limit": limit})
        if mine and not out:
            out, where = mine, root
    if balance.get("review") and (since := _when(oldest_waiting)) and (waited := now - since) > bound:
        out.append({"line": "review", "value": int(waited.total_seconds()), "limit": int(bound.total_seconds())})
    if not out and unknown:
        return None
    return out, where
