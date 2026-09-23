"""Whether a PR waits for its reader (design §4.9b *The reader*, TD-093): the author's own `ao`
reads its record's `review` and checks the PR's changed files against `held:`. The host agent only
stores the setting; this is the one place it is applied.

`held:` is a list of path globs as a person writes them in a `.gitignore`-ish way: `**` spans any
number of directories, `*` and `?` stay inside one, and a pattern names paths from the repo root.
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
    out, i, g = [], 0, glob.strip().lstrip("/")
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
            ["gh", "pr", "view", str(pr), "--json", "files"],
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
        return [str(f["path"]) for f in json.loads(cp.stdout).get("files") or []]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError(f"gh gave PR #{pr}'s files in a shape this does not read") from None
