"""A look's screenshots, read where the repo is (design §4.5a **Inbox row: a look**, §4.10 *A look*;
TD-292 slice 3, TD-300): one `.png` of `docs/mockups/reviews/` of a checkout in this host's registry,
as `origin/<default>` holds it, and nothing else. The page serves this host's from here, and asks
another host's through the home (`host_shot`, the link's `shot`), so a look whose sender runs on a
node draws its images too. Never the working tree: what a look shows is what was merged.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from typing import Any

from sessionorc import hosts, mail
from sessionorc import ledger as ledger_mod

SHOT_DIR = Path("docs") / "mockups" / "reviews"
SHOT_BYTES_MAX = 4 * 1024 * 1024  # one image; a frame on the link is 8 MiB and base64 grows it by a third


def root_of(repo: str) -> Path | None:
    """The registered checkout of this host named `repo` (its directory's name, as `/repo/<name>`
    and Add entry name a repo), or None — the only checkouts a screenshot is read from."""
    for p in hosts.local_host().repos():
        if repo and Path(p).expanduser().name == repo:
            return Path(p).expanduser().resolve()
    return None


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=10.0)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _spec(root: Path, name: str) -> str | None:
    if not mail.SHOT_NAME.fullmatch(name):
        return None
    ref = ledger_mod.default_ref(root)
    return f"{ref}:{(SHOT_DIR / name).as_posix()}" if ref is not None else None


def read(root: Path, name: str) -> bytes | None:
    """`docs/mockups/reviews/<name>` as `origin/<default>` holds it in `root`, or None: a name that is
    not a bare `.png` name, a checkout with no origin, a file origin does not hold, one past
    `SHOT_BYTES_MAX`."""
    spec = _spec(root, name)
    cp = _git(root, "show", spec) if spec else None
    if cp is None or cp.returncode != 0 or len(cp.stdout) > SHOT_BYTES_MAX:
        return None
    return cp.stdout


def exists(root: Path, name: str) -> bool:
    """Whether `read` would return `docs/mockups/reviews/<name>` in `root` — origin's default holds
    it, at most `SHOT_BYTES_MAX` — the row's address test, without reading the image."""
    spec = _spec(root, name)
    cp = _git(root, "cat-file", "-s", spec) if spec else None
    if cp is None or cp.returncode != 0:
        return False
    try:
        return int(cp.stdout.strip()) <= SHOT_BYTES_MAX
    except ValueError:
        return False


def reading(repo: str, name: str, head: bool) -> dict[str, Any]:
    """`host_shot`'s reading on this host, and the link's `shot` on a node: `{exists, png}`, the image
    in base64 (empty with `head`). A repo this host does not register reads as not there. Blocking."""
    root = root_of(repo)
    if root is None:
        return {"exists": False, "png": ""}
    if head:
        return {"exists": exists(root, name), "png": ""}
    data = read(root, name)
    return {"exists": data is not None, "png": base64.b64encode(data).decode("ascii") if data else ""}
