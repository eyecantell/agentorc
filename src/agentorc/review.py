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


def setting(review: Any) -> dict[str, Any] | None:
    """A record's `review` with the design's defaults filled in — `held` every PR, `bound` two
    hours — whatever wrote it. A `ValueError` for a shape that holds nothing and says nothing: an
    empty `held:` is not *hold everything*, and not *hold nothing* either."""
    if not review:
        return None
    if not isinstance(review, dict) or not review.get("reader"):
        raise ValueError(f"review is not a setting: {review!r}")
    held = review.get("held", ["**"])
    if isinstance(held, str):
        held = [held]
    if not held or not all(isinstance(g, str) and g.strip() for g in held):
        raise ValueError(f"review: held is a list of path globs, not {held!r}")
    return {"reader": str(review["reader"]), "held": list(held), "bound": str(review.get("bound") or "2h")}


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
