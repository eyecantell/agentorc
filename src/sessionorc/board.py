"""Board write-back (design §4.4, TD-069 step 3): **Snooze** and **Done** on one item of a repo's
`docs/user_attention.md`, made by the host agent and committed in that repo's main checkout.

A board is dev-cadence's file and its items are read by dev-cadence's reader
(`nudge_user_attention.py --report --json`), which gives each item's line number and text; this
module only edits the one line it is pointed at, and only when that line still holds the item the
person was shown. The commit is cadence §4's carve-out for tool-made board edits: one line, one
commit, a fixed message, on the checkout's default branch, never pushed. Where the checkout is not
in a state to take that commit — another branch, the board already edited, a merge or rebase under
way — the edit is refused in words and nothing is touched: the person's click is never turned into
a dirty file under the anchor session's feet, or a commit on someone's feature branch.
"""

from __future__ import annotations

import re
import subprocess
from datetime import date
from pathlib import Path

BOARD = Path("docs") / "user_attention.md"
ACTIONS = ("snooze", "done")
# dev-cadence's own shapes (`nudge_user_attention.py`'s ITEM_RE and DUE_RE): an open item, and its date.
ITEM_RE = re.compile(r"^(?P<lead>\s*-\s*)\[ \](?P<gap>\s+)(?P<text>.+)$")
DUE_RE = re.compile(r"\b(?P<key>Due:\s*)(?P<due>\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
SESSION_RE = re.compile(r"\(session `?(?P<name>[^`\s,)]+)")
HEAD_RE = re.compile(r"\*\*(?P<head>.+?)\*\*")
HEAD_MAX = 60
GIT_TIMEOUT = 20.0


class Refused(Exception):
    """The edit was not made, and why, in words a person reads on the Inbox."""


def edit_line(line: str, text: str, action: str, due: str | None = None) -> str:
    """`line` with the item done or snoozed to `due`. Refused unless `line` is an open item whose
    text is exactly `text` — the item the person was shown, not whatever moved onto that line."""
    m = ITEM_RE.match(line.rstrip("\n"))
    if m is None or m.group("text").strip() != text.strip():
        raise Refused("that line of the board no longer holds this item: reload the Inbox and try again")
    end = "\n" if line.endswith("\n") else ""
    body = line.rstrip("\n")
    if action == "done":
        return f"{m.group('lead')}[x]{m.group('gap')}{m.group('text')}{end}"
    if action != "snooze":
        raise Refused(f"unknown board action {action!r}: {' or '.join(ACTIONS)}")
    try:
        date.fromisoformat(str(due))
    except ValueError:
        raise Refused(f"a snooze needs a date as YYYY-MM-DD, not {due!r}") from None
    if DUE_RE.search(body):
        return DUE_RE.sub(lambda d: f"{d.group('key')}{due}", body, count=1) + end
    return f"{body.rstrip()} Due: {due}.{end}"


def message(text: str, action: str, due: str | None = None) -> str:
    """The fixed commit message (design §4.4, cadence §4's carve-out): the tool, the action, the
    item's head, and the session that raised the item — what a reviewer greps for."""
    h = HEAD_RE.search(text)
    head = (h.group("head") if h else text).strip()
    if len(head) > HEAD_MAX:
        head = head[: HEAD_MAX - 1].rstrip() + "…"
    s = SESSION_RE.search(text)
    who = s.group("name") if s else "n/a"
    what = f"snooze {head} to {due}" if action == "snooze" else f"done {head}"
    return f"agentorc: {what} (session {who})"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=GIT_TIMEOUT, check=False
    )


def default_branch(root: Path) -> str:
    """`origin`'s default branch as this clone last saw it, else `main`."""
    cp = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    ref = cp.stdout.strip() if cp.returncode == 0 else ""
    return ref.split("/", 1)[1] if "/" in ref else "main"


def ready(root: Path) -> None:
    """Refused unless the checkout can take the commit without touching anyone's work: on its
    default branch, the board clean, and no merge, rebase, cherry-pick or index lock under way."""
    top = _git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise Refused(f"{root} is not a git checkout")
    gitdir = Path(_git(root, "rev-parse", "--absolute-git-dir").stdout.strip())
    busy = [
        n
        for n in ("index.lock", "MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD")
        if (gitdir / n).exists()
    ]
    if busy:
        raise Refused(f"{root} has a git operation under way ({', '.join(busy)}): try again once it is finished")
    branch = _git(root, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
    want = default_branch(root)
    if branch != want:
        raise Refused(
            f"{root} is on {branch or 'a detached HEAD'}, not {want}: a board edit is committed on {want} only "
            "(cadence §4), so make it there by hand or once the checkout is back on it"
        )
    if _git(root, "status", "--porcelain", "--", str(BOARD)).stdout.strip():
        raise Refused(f"{root}'s board has uncommitted changes: commit or discard them first, then try again")


def write_back(root: str | Path, line: int, text: str, action: str, due: str | None = None) -> dict[str, str]:
    """Make one Snooze or Done on the board of the checkout `root` and commit it there (§4.4).
    Returns `{commit, message}`; raises `Refused` with nothing changed."""
    root = Path(root)
    if action not in ACTIONS:
        raise Refused(f"unknown board action {action!r}: {' or '.join(ACTIONS)}")
    ready(root)
    path = root / BOARD
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as e:
        raise Refused(f"cannot read {path}: {e}") from None
    if not isinstance(line, int) or not 1 <= line <= len(lines):
        raise Refused("that line of the board no longer holds this item: reload the Inbox and try again")
    was = "".join(lines)
    lines[line - 1] = edit_line(lines[line - 1], text, action, due)
    msg = message(text, action, due)
    path.write_text("".join(lines), encoding="utf-8")
    cp = _git(root, "commit", "--quiet", "-m", msg, "--only", "--", str(BOARD))
    if cp.returncode != 0:
        path.write_text(was, encoding="utf-8")  # the board as it was: a failed commit leaves no dirty file
        why = (cp.stderr or cp.stdout).strip().splitlines()
        raise Refused(f"the commit failed, and the board is as it was: {why[-1] if why else cp.returncode}")
    return {"commit": _git(root, "rev-parse", "--short", "HEAD").stdout.strip(), "message": msg}
