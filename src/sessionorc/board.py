"""Board write-back (design §4.4, TD-069 step 3): **Snooze**, **Done** and **Reply** (TD-142) on
one item of a repo's `docs/user_attention.md`, made by the host agent and committed in that repo's
main checkout — and its one **add**, *Put on the board* (TD-140), the only line this system ever adds to a board.

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
import threading
from datetime import date
from pathlib import Path

BOARD = Path("docs") / "user_attention.md"
ACTIONS = ("snooze", "done", "reply")
# Any item, open or done: the add goes above the first of them, the top of the open items (§4.4).
ANY_ITEM_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s")
NEEDS_RE = re.compile(r"^##\s+Needs the user\b")
# dev-cadence's own shapes (`nudge_user_attention.py`'s ITEM_RE and DUE_RE): an open item, and its date.
ITEM_RE = re.compile(r"^(?P<lead>\s*-\s*)\[ \](?P<gap>\s+)(?P<text>.+)$")
DUE_RE = re.compile(r"\b(?P<key>Due:\s*)(?P<due>\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
SESSION_RE = re.compile(r"\(session `?(?P<name>[^`\s,)]+)")
HEAD_RE = re.compile(r"\*\*(?P<head>.+?)\*\*")
HEAD_MAX = 60
GIT_TIMEOUT = 20.0
# One edit at a time on this host: each is a read, a write and a commit, and two interleaved would
# leave a commit saying *done* over a file where the other's write undid it (review of PR #474).
_EDIT = threading.Lock()


class Refused(Exception):
    """The edit was not made, and why, in words a person reads on the Inbox."""


def reply_tail(reply: str, by: str, today: str) -> str:
    """The words **Reply** appends to an item's own line (design §4.4, TD-142): ` — <by>, <date>:
    <reply>`. On the line, not under it: an item is one line, and the reader and the SessionStart
    hook read lines, so a reply typed over several lines is joined onto it with spaces. Refused for
    a reply that is empty."""
    words = " ".join(str(reply or "").split())
    if not words:
        raise Refused("a reply needs its text: what the next session should do")
    return f" — {by}, {today}: {words}"


def edit_line(
    line: str, text: str, action: str, due: str | None = None, *, reply: str = "", by: str = "", today: str = ""
) -> str:
    """`line` with the item done, snoozed to `due`, or replied to (`reply`, by `by` on `today`).
    Refused unless `line` is an open item whose text is exactly `text` — the item the person was
    shown, not whatever moved onto that line."""
    m = ITEM_RE.match(line.rstrip("\n"))
    if m is None or m.group("text").strip() != text.strip():
        raise Refused("that line of the board no longer holds this item: reload the Inbox and try again")
    end = "\n" if line.endswith("\n") else ""
    body = line.rstrip("\n")
    if action == "done":
        return f"{m.group('lead')}[x]{m.group('gap')}{m.group('text')}{end}"
    if action == "reply":
        if not DUE_RE.search(body) and DUE_RE.search(str(reply or "")):
            # the reader and a later Snooze take a line's first `Due:`: on an undated item the
            # reply's would become the item's date (review of PR #581)
            raise Refused("this item has no Due: date, so a reply may not carry one: Snooze sets its date")
        return f"{body.rstrip()}{reply_tail(reply, by, today or date.today().isoformat())}{end}"
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
    what = {"snooze": f"snooze {head} to {due}", "reply": f"reply on {head}"}.get(action, f"done {head}")
    return f"agentorc: {what} (session {who})"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """One git call; a git that cannot be run or does not finish is a refusal, never a raw error."""
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=GIT_TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired:
        raise Refused(f"git {args[0]} did not finish in {GIT_TIMEOUT:g} s in {root}") from None
    except OSError as e:
        raise Refused(f"git could not be run in {root}: {e}") from None


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


def author(root: Path) -> str:
    """Who a reply is signed by: the checkout's git `user.name` — the name the commit is authored
    under — else *the person*."""
    try:
        name = _git(root, "config", "user.name").stdout.strip()
    except Refused:
        name = ""
    return name or "the person"


def write_back(
    root: str | Path, line: int, text: str, action: str, due: str | None = None, *, reply: str = ""
) -> dict[str, str]:
    """Make one Snooze, Done or Reply on the board of the checkout `root` and commit it there
    (§4.4). Returns `{commit, message}`; raises `Refused` with nothing changed."""
    root = Path(root)
    if action not in ACTIONS:
        raise Refused(f"unknown board action {action!r}: {' or '.join(ACTIONS)}")
    with _EDIT:
        return _write_back(root, line, text, action, due, reply)


def _write_back(root: Path, line: int, text: str, action: str, due: str | None, reply: str = "") -> dict[str, str]:
    ready(root)
    path = root / BOARD
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as e:
        raise Refused(f"cannot read {path}: {e}") from None
    if not isinstance(line, int) or not 1 <= line <= len(lines):
        raise Refused("that line of the board no longer holds this item: reload the Inbox and try again")
    was = "".join(lines)
    by = author(root) if action == "reply" else ""
    lines[line - 1] = edit_line(lines[line - 1], text, action, due, reply=reply, by=by)
    msg = message(text, action, due)
    path.write_text("".join(lines), encoding="utf-8")
    try:
        cp = _git(root, "commit", "--quiet", "-m", msg, "--only", "--", str(BOARD))
    except Refused as e:
        path.write_text(was, encoding="utf-8")  # the board as it was: a failed commit leaves no dirty file
        raise Refused(f"the commit failed, and the board is as it was: {e}") from None
    if cp.returncode != 0:
        path.write_text(was, encoding="utf-8")
        why = (cp.stderr or cp.stdout).strip().splitlines()
        raise Refused(f"the commit failed, and the board is as it was: {why[-1] if why else cp.returncode}")
    return {"commit": _git(root, "rev-parse", "--short", "HEAD").stdout.strip(), "message": msg}


def item_line(text: str, due: str, today: str, session: str | None, host: str | None, context: str | None) -> str:
    """The one line *Put on the board* writes (design §4.5a), in the board's own `Format:` —
    `- [ ] <today> (session `<name>` on <host>, or n/a) — <text>. Context: <about>. Due: <date>.`
    Refused for text that is empty or more than one line: an item is one line."""
    words = " ".join(str(text or "").split()).rstrip(".")
    if not words:
        raise Refused("a board line needs its text: what is needed, in your words")
    if "\n" in str(text).strip():
        raise Refused("a board line is one line: put the rest in the text on one line")
    try:
        date.fromisoformat(str(due))
    except ValueError:
        raise Refused(f"a board line needs a Due date as YYYY-MM-DD, not {due!r}") from None
    who = f"session `{session}` on {host}" if session else "n/a"
    ctx = " ".join(str(context or "").split()) or "none"
    return f"- [ ] {today} ({who}) — {words}. Context: {ctx}. Due: {due}.\n"


def _top_of_needs(lines: list[str]) -> int:
    """Where the add goes: above the first item **of the *Needs the user* section** — another
    section's items (a board's parked in-flight work) are never where a person's line lands — or,
    when that section holds none, right under its heading and blank line. A board with no such
    heading takes it above its first item, else at the end."""
    head = next((i for i, ln in enumerate(lines) if NEEDS_RE.match(ln)), None)
    if head is None:
        return next((i for i, ln in enumerate(lines) if ANY_ITEM_RE.match(ln)), len(lines))
    end = next((i for i in range(head + 1, len(lines)) if lines[i].startswith("#")), len(lines))
    first = next((i for i in range(head + 1, end) if ANY_ITEM_RE.match(lines[i])), None)
    if first is not None:
        return first
    at = head + 1
    while at < end and not lines[at].strip():
        at += 1
    return at


def add(
    root: str | Path,
    text: str,
    due: str,
    *,
    entry: str,
    session: str | None = None,
    host: str | None = None,
    context: str | None = None,
    today: str | None = None,
) -> dict[str, str | int]:
    """**Put on the board** (design §4.4, the write-back's one add; TD-140): one new line at the top
    of the open items of the checkout `root`'s board, committed there as `agentorc: board <item
    head> (from <entry id>)`, never pushed. Refused on the same conditions as an edit, touching
    nothing. Returns `{commit, message, line}` — `line` the new item's line number."""
    root = Path(root)
    line = item_line(text, due, today or date.today().isoformat(), session, host, context)
    with _EDIT:
        ready(root)
        path = root / BOARD
        try:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        except OSError as e:
            raise Refused(f"cannot read {path}: {e}") from None
        at = _top_of_needs(lines)
        if lines and not lines[-1].endswith("\n") and at == len(lines):
            lines[-1] += "\n"
        was = "".join(lines)
        lines.insert(at, line)
        head = " ".join(str(text).split()).rstrip(".")
        if len(head) > HEAD_MAX:
            head = head[: HEAD_MAX - 1].rstrip() + "…"
        msg = f"agentorc: board {head} (from {entry})"
        path.write_text("".join(lines), encoding="utf-8")
        try:
            cp = _git(root, "commit", "--quiet", "-m", msg, "--only", "--", str(BOARD))
        except Refused as e:
            path.write_text(was, encoding="utf-8")
            raise Refused(f"the commit failed, and the board is as it was: {e}") from None
        if cp.returncode != 0:
            path.write_text(was, encoding="utf-8")
            why = (cp.stderr or cp.stdout).strip().splitlines()
            raise Refused(f"the commit failed, and the board is as it was: {why[-1] if why else cp.returncode}")
        return {"commit": _git(root, "rev-parse", "--short", "HEAD").stdout.strip(), "message": msg, "line": at + 1}
