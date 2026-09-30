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
FLAP = timedelta(minutes=10)  # §6 *Balance*: a mark that comes and goes inside this tells once
CLEAR = "the line is clear again: pick as your lane says (design §6 *Balance*)"  # the ring of a refused member


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


def duration(seconds: Any) -> str:
    """Seconds as the page and the refusal write them: the largest unit and the next (`3d 4h`,
    `5h 10m`, `40m`), a zero second part left off, so a line of `2d` reads `2d`."""
    try:
        n = max(0, int(seconds))
    except (TypeError, ValueError):
        return "?"
    d, rest = divmod(n, 86400)
    h, rest = divmod(rest, 3600)
    m = rest // 60
    parts = [(d, "d"), (h, "h"), (m, "m")]
    while len(parts) > 1 and not parts[0][0]:
        parts.pop(0)
    big, small = parts[0], parts[1] if len(parts) > 1 else (0, "")
    return f"{big[0]}{big[1]}" + (f" {small[0]}{small[1]}" if small[0] else "")


def _line_words(c: dict[str, Any]) -> str:
    v, lim = c.get("value"), c.get("limit")
    if c.get("line") == "prs":
        return f"{v} open PR{'' if v == 1 else 's'}, the line is {lim}"
    if c.get("line") == "oldest":
        return f"the oldest PR open {duration(v)}, the line is {duration(lim)}"
    if c.get("line") == "review":
        return f"the reader's queue waiting {duration(v)}, the bound is {duration(lim)}"
    return f"{c.get('line')} {v}, the line is {lim}"


def _since(mark: dict[str, Any]) -> str:
    """The mark's `since` in the home's clock, the weekday before it when it is not today."""
    if (at := _when(mark.get("since"))) is None:
        return ""
    at = at.astimezone()
    day = "" if at.date() == datetime.now().astimezone().date() else at.strftime("%a ")
    return f"{day}{at:%H:%M}"


def _lines(mark: dict[str, Any]) -> str:
    return "; ".join(_line_words(c) for c in mark.get("crossed") or [] if isinstance(c, dict))


def crossing(team: str, mark: dict[str, Any]) -> str:
    """The `system` note a crossing sends the team's manager and the person (design §6 *Balance*):
    what crossed, in the mark's numbers, and what it means — neither crashed nor finished."""
    since = _since(mark)
    return (
        f"{team} is over its line{f' since {since}' if since else ''}: {_lines(mark)} — its members take no new "
        "claim until it clears; work in hand goes on, and nobody is crashed or finished (design §6 *Balance*)"
    )


def clearing(team: str, mark: dict[str, Any] | None) -> str:
    """The note the clearing sends the same two: the line is clear, and since when it was over."""
    over = f" (over since {since})" if (since := _since(mark or {})) else ""
    return f"{team} is under its line again{over}: its members claim as their lanes say"


def refusal(team: str, mark: dict[str, Any]) -> str:
    """The words a refused claim — and a refused `none` — answers with (design §6 *Balance*), in the
    mark's own numbers and its `since` in the home's clock."""
    since = f" (since {s})" if (s := _since(mark)) else ""
    return (
        f"{team} is over its line: {_lines(mark)}{since}. Take nothing new: finish, rebase or answer what is open "
        "of yours, then end your turn — you are told when the line clears (design §6 *Balance*)"
    )
