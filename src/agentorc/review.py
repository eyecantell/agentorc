"""Whether a PR waits for its reader (design §4.9b *The reader*, TD-093): the author's own `ao`
reads its record's `review` and checks the PR's changed files against `held:`. For that read the host agent only
stores the setting; once the PR has merged, the home checks the same thing itself (§6 rule 11).

`held:` is a list of path globs: a pattern names paths from the repo root, `**` spans any number
of directories, `*` and `?` stay inside one, and a pattern ending in `/` names everything under
that directory. Nothing else is special — `[abc]` is those five characters, not a class.
"""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from typing import Any

# the glob match and the files read are `sessionorc`'s since §6 rule 11 reads them at the home
# (TD-258 slice 2): the package rule runs one way, so `ao pr held` reads them from there
from sessionorc.held import GH_TIMEOUT, held_paths, matches, pr_files  # noqa: F401
from sessionorc.models import review_links


def setting(review: Any) -> dict[str, Any] | None:
    """A record's `review` with the design's defaults filled in — `held` every PR, `bound` two
    hours — whatever wrote it. A `ValueError` for a shape that holds nothing and says nothing: an
    empty `held:` is not *hold everything*, and not *hold nothing* either."""
    if not review:
        return None
    if isinstance(review, dict) and isinstance(review.get("chain"), list):
        # a flow's chain (§4.9c, TD-315): its links as written, `held` their union — the record's
        # shape is the host agent's check (`normalize_review`); whose turn it is, is slice 4's
        links = [dict(x) for x in review_links(review)]
        if not links:
            raise ValueError(f"review is not a setting: {review!r}")
        held = list(dict.fromkeys(g for x in links for g in x.get("held") or ["**"]))
        return {"chain": links, "held": held, "bound": str(review.get("bound") or "2h")}
    if not isinstance(review, dict) or not review.get("reader"):
        raise ValueError(f"review is not a setting: {review!r}")
    held = review.get("held", ["**"])
    if isinstance(held, str):
        held = [held]
    if not held or not all(isinstance(g, str) and g.strip() for g in held):
        raise ValueError(f"review: held is a list of path globs, not {held!r}")
    return {"reader": str(review["reader"]), "held": list(held), "bound": str(review.get("bound") or "2h")}


def addressed(addr: str, readers: list[str]) -> str | None:
    """Which of a chain's `readers` — each the seat's name as the definition gives it (§4.9c) — a
    mail address names: the session id `ao-<scope>-<name>`, on this host or `@<host>`, or the name
    itself. The longest name that fits wins, so `ui-reader` is never taken for `reader`. None: none."""
    base = str(addr).split("@", 1)[0]
    fits = [r for r in readers if base == r or base.endswith(f"-{r}")]
    return max(fits, key=len) if fits else None


def walk(links: list[dict[str, Any]], asks: list[dict[str, Any]] | None, *, older: bool = False) -> dict[str, Any]:
    """Where a held PR stands along its chain (§4.9c *Whose turn it is*, TD-315 slice 4): `links` the
    record's links that hold the PR, in the flow's order, and `asks` its author's asks carrying the
    PR as `pr_reads` gives them (None: the home did not say). Each link gets a `state` — `passed` (its
    reader's last answer was `pass` or `merged`), `asked` (an ask is open, or answered with no verdict),
    `findings`, `next` (the first link not passed, never asked: the author's turn to ask it) or
    `later` — and `at`, the time of what set it. `turn` is the first link not passed, None once every
    link has passed or when the home did not say; `merges` the last link's reader, the one that
    merges. The older `{reader, held}` (`older`) takes every ask of the PR as its reader's, whoever it
    was addressed to; a chain matches each ask to a link by its addressee. A chain names each seat
    once — two stages of one role are refused — so no ask counts for two links."""
    rows: list[dict[str, Any]] = []
    turn: dict[str, Any] | None = None
    readers = [str(x.get("reader") or "") for x in links]
    for link in links:
        row = {"stage": link.get("stage"), "reader": link.get("reader"), "held": list(link.get("paths") or [])}
        if asks is None:
            row["state"], row["at"] = "unknown", None
        else:
            mine = [a for a in asks if older or any(addressed(x, readers) == link["reader"] for x in a["to"])]
            last = mine[-1] if mine else None
            if last is None:
                row["state"], row["at"] = ("later" if turn is not None else "next"), None
            elif last.get("verdict") in ("pass", "merged"):
                row["state"], row["at"] = "passed", last.get("replied_at")
            elif last.get("verdict") == "findings":
                row["state"], row["at"] = "findings", last.get("replied_at")
            else:
                row["state"], row["at"] = "asked", last.get("at")
        if turn is None and row["state"] != "passed":
            turn = row
        rows.append(row)
    return {
        "chain": rows,
        "turn": turn["reader"] if turn is not None and asks is not None else None,
        "merges": rows[-1]["reader"] if rows else None,
    }


_REMOTE = re.compile(
    r"^(?:https://(?:[^@/\s]+@)?|ssh://git@|git@)github\.com[:/](?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?/?$"
)


@lru_cache(maxsize=64)
def _github(directory: str) -> str:
    try:
        cp = subprocess.run(
            ["git", "-C", directory, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    m = _REMOTE.match(cp.stdout.strip()) if cp.returncode == 0 else None
    return f"https://github.com/{m.group('slug')}" if m else ""


def repo_web(directory: str | None) -> str:
    """The web address of the checkout at `directory` — its `origin` on GitHub — or "" when that
    cannot be said; `pr_url`'s base, for a page that links PR numbers it learns later (TD-150)."""
    return _github(str(directory)) if directory else ""


def pr_url(directory: str | None, pr: int) -> str:
    """The web link of PR `pr` in the checkout at `directory` — its `origin` on GitHub — or "" when
    that cannot be said (no directory, no origin, another forge): the Inbox then draws `#<n>` bare
    (design §4.5a, TD-093). The remote is read once per directory."""
    base = _github(str(directory)) if directory else ""
    return f"{base}/pull/{int(pr)}" if base else ""
