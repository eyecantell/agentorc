"""Whether a PR waits for its reader (design §4.9b *The reader*, TD-093): the author's own `ao`
reads its record's `review` and checks the PR's changed files against `held:`. The host agent only
stores the setting; this is the one place it is applied.

`held:` is a list of path globs: a pattern names paths from the repo root, `**` spans any number
of directories, `*` and `?` stay inside one, and a pattern ending in `/` names everything under
that directory. Nothing else is special — `[abc]` is those five characters, not a class.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

GH_TIMEOUT = 30.0


@lru_cache(maxsize=256)
def _pattern(glob: str) -> re.Pattern[str]:
    g = glob.strip().lstrip("/")
    if g.endswith("/"):
        g = g.rstrip("/") + "/**"  # `src/` and `src/**/` both mean everything under src
    out, i = [], 0
    while i < len(g):
        if g.startswith("**/", i):
            out.append("(?:.*/)?")  # any number of directories, none included
            i += 3
        elif g.startswith("**", i):
            out.append(".*")
            i += 2
        elif g[i] == "*":
            out.append("[^/]*")
            i += 1
        elif g[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(g[i]))
            i += 1
    return re.compile("".join(out))


def matches(path: str, glob: str) -> bool:
    """Whether a repo-relative path is named by one `held:` glob."""
    return _pattern(glob).fullmatch(path.lstrip("/")) is not None


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


def held_paths(files: Iterable[str], review: dict[str, Any] | None) -> list[str]:
    """The PR's changed files that its record's `review` holds — empty when there is no `review`,
    which is the case §4.9b says merges as the cadence does."""
    if not review:
        return []
    globs = list(review.get("held") or ["**"])
    return [f for f in files if any(matches(f, g) for g in globs)]


def pr_files(pr: int, cwd: str | None = None) -> list[str]:
    """The PR's changed files, from `gh` in the checkout it belongs to. A `RuntimeError` saying why
    when `gh` cannot say: *not held* is never guessed from a failed read."""
    try:
        cp = subprocess.run(
            ["gh", "pr", "view", str(pr), "--json", "files,changedFiles"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=GH_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"gh could not read PR #{pr}: {e}") from None
    if cp.returncode != 0:
        why = (cp.stderr or cp.stdout).strip().splitlines()
        raise RuntimeError(f"gh could not read PR #{pr}: {why[-1] if why else cp.returncode}")
    try:
        got = json.loads(cp.stdout)
        files = [str(f["path"]) for f in got.get("files") or []]
        total = int(got.get("changedFiles") or 0)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError(f"gh gave PR #{pr}'s files in a shape this does not read") from None
    if total > len(files):
        # `gh` returns one page of a large PR's files: a held path past it would read as *not held*
        raise RuntimeError(f"gh listed {len(files)} of PR #{pr}'s {total} files: too many to judge; ask the reader")
    return files


_REMOTE = re.compile(r"^(?:https://|ssh://git@|git@)github\.com[:/](?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?/?$")


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


def pr_url(directory: str | None, pr: int) -> str:
    """The web link of PR `pr` in the checkout at `directory` — its `origin` on GitHub — or "" when
    that cannot be said (no directory, no origin, another forge): the Inbox then draws `#<n>` bare
    (design §4.5a, TD-093). The remote is read once per directory."""
    base = _github(str(directory)) if directory else ""
    return f"{base}/pull/{int(pr)}" if base else ""
