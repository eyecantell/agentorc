"""Board write-back (design §4.4, TD-069 step 3): **Snooze**, **Done**, **Reply** (TD-142) and
**Decide** (TD-255) on one item of a repo's `docs/user_attention.md`, made by the host agent — and
its two **adds**, *Put on the board* (TD-140) and an orphaned question's answer (TD-216), the only
lines this system ever adds to a board.

A board is dev-cadence's file and its items are read by dev-cadence's reader
(`nudge_user_attention.py --report --json`), which gives each item's line number and text; this
module only edits the one item it is pointed at, and only while origin's board still holds the item
the person was shown, open, word for word. **The edit is made on origin's head and landed there**
(TD-222, built by TD-264): in the host agent's own detached tree for the repo
(`~/.agentorc/boards/<repo>/tree`), reset to `origin/<default>` at every press, committed with a
fixed message, pushed on a branch of its own and carried onto the default branch by a pull request
it opens and squash-merges at once — cadence §4.5's tool-made board edit. The person's checkout is
never written, so its branch, a dirty file or a rebase under way refuses nothing; it catches up by
the pull (design §6 *Pull*). Origin unreachable, a line only the checkout holds, and a line that
moved are each refused in words, with nothing landed.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sessionorc import paths

BOARD = Path("docs") / "user_attention.md"
ACTIONS = ("snooze", "done", "reply", "decide")
# Any item, open or done: the add goes above the first of them, the top of the open items (§4.4).
ANY_ITEM_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s")
NEEDS_RE = re.compile(r"^##\s+Needs the user\b")
# dev-cadence's own shapes (`nudge_user_attention.py`'s ITEM_RE and DUE_RE): an open item, and its date.
ITEM_RE = re.compile(r"^(?P<lead>\s*-\s*)\[ \](?P<gap>\s+)(?P<text>.+)$")
DUE_RE = re.compile(r"\b(?P<key>Due:\s*)(?P<due>\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
SESSION_RE = re.compile(r"\(session `?(?P<name>[^`\s,)]+)")
# The reader's own field (`DECIDED_RE` in dev-cadence's `nudge_user_attention.py`): a sentence of
# its own, anchored at the line's end. Kept the same shape here so a line this module calls
# decided is one the reader reads as decided.
DECIDED_RE = re.compile(
    r"(?:^|(?<=[.?!]\s))Decided:\s*(?P<text>(?:(?!\bDecided:).)+?)\s*\((?P<date>\d{4}-\d{2}-\d{2})\)\.?\s*$"
)
ANSWERS_RE = re.compile(r"(?:^|(?<=[.?!]\s))Answers:\s*(?P<answers>(?:(?!\bAnswers:).)*?)\.?\s*(?:\bDecided:.*)?$")
# A live look's second answer (design §4.5a **Works** / **Not right…**): the person's words follow.
NOT_RIGHT = "Not right:"
# The form itself, as cadence §3.5 writes it: `Not right: <what>` — a slot in angle brackets, or nothing.
NOT_RIGHT_FORM = re.compile(r"Not right:\s*(<[^<>]*>)?")
# A field's name inside the person's own words would be read as the field (review of PR #861).
FIELD_NAME_RE = re.compile(r"\b(?:Answers|Decided):")
HEAD_RE = re.compile(r"\*\*(?P<head>.+?)\*\*")
HEAD_MAX = 60
ANSWER_MAX = 80  # an answer in a commit subject: a *Not right:* carries a sentence
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


def _sentence(body: str) -> str:
    """`body` ending a sentence, so a field written after it starts one (the reader's rule)."""
    head = body.rstrip()
    return head if head.endswith((".", "?", "!")) else f"{head}."


def _fields_start(body: str) -> int | None:
    """Where the line's trailing `Answers:` / `Decided:` fields begin, as the reader finds them
    (after the item's `Due:`), or None: what a reply is written ahead of, since words after
    `Answers:` would be read as part of its last answer."""
    due = DUE_RE.search(body)
    off = due.end() if due else 0
    starts = [m.start() for m in (ANSWERS_RE.search(body[off:]), DECIDED_RE.search(body[off:])) if m]
    return off + min(starts) if starts else None


def edit_line(
    line: str,
    text: str,
    action: str,
    due: str | None = None,
    *,
    reply: str = "",
    by: str = "",
    today: str = "",
    answer: str = "",
) -> str:
    """`line` with the item done, snoozed to `due`, replied to (`reply`, by `by` on `today`) or
    decided (`answer`, on `today`). Refused unless `line` is an open item whose text is exactly
    `text` — the item the person was shown, not whatever moved onto that line."""
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
        if FIELD_NAME_RE.search(str(reply or "")):
            raise Refused("a reply may not carry Answers: or Decided: — the board's reader would read it as the field")
        tail = reply_tail(reply, by, today or date.today().isoformat())
        if (at := _fields_start(body)) is not None:
            # ahead of the fields the reader anchors at the line's end (§4.4 *The two compose*):
            # a reply never un-decides an item, and never becomes part of its last answer
            return f"{_sentence(body[:at].rstrip() + tail)} {body[at:].rstrip()}{end}"
        return f"{body.rstrip()}{tail}{end}"
    if action == "decide":
        # the edit `board_edit.py decide` makes (cadence §4.5), with one difference: a line
        # already decided is refused here, never rewritten (§4.4 *Decide*)
        words = " ".join(str(answer or "").split())
        if not words:
            raise Refused("a decide needs its answer: one of the item's Answers:")
        if FIELD_NAME_RE.search(words):
            raise Refused("an answer may not carry Answers: or Decided: — say it in other words")
        if DECIDED_RE.search(body):
            raise Refused("this item is already decided: Reply says more to its session, Done closes it")
        new = f"{_sentence(body)} Decided: {words} ({today or date.today().isoformat()})."
        d = DECIDED_RE.search(new)
        if d is None or d.group("text") != words:
            raise Refused(f"the board's reader would not read back 'Decided: {words}': say it in other words")
        return new + end
    if action != "snooze":
        raise Refused(f"unknown board action {action!r}: {' or '.join(ACTIONS)}")
    try:
        date.fromisoformat(str(due))
    except ValueError:
        raise Refused(f"a snooze needs a date as YYYY-MM-DD, not {due!r}") from None
    if DUE_RE.search(body):
        return DUE_RE.sub(lambda d: f"{d.group('key')}{due}", body, count=1) + end
    return f"{body.rstrip()} Due: {due}.{end}"


def _clip(words: str, most: int) -> str:
    return words if len(words) <= most else words[: most - 1].rstrip() + "…"


def message(text: str, action: str, due: str | None = None, *, answer: str = "") -> str:
    """The fixed commit message (design §4.4, cadence §4's carve-out): the tool, the action, the
    item's head, and the session that raised the item — what a reviewer greps for."""
    h = HEAD_RE.search(text)
    head = _clip((h.group("head") if h else text).strip(), HEAD_MAX)
    s = SESSION_RE.search(text)
    who = s.group("name") if s else "n/a"
    what = {
        "snooze": f"snooze {head} to {due}",
        "reply": f"reply on {head}",
        "decide": f"decide {head}: {_clip(' '.join(str(answer or '').split()), ANSWER_MAX)}",
    }.get(action, f"done {head}")
    return f"agentorc: {what} (session {who})"


def _git(root: Path, *args: str, timeout: float = GIT_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """One git call; a git that cannot be run or does not finish is a refusal, never a raw error."""
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        raise Refused(f"git {args[0]} did not finish in {timeout:g} s in {root}") from None
    except OSError as e:
        raise Refused(f"git could not be run in {root}: {e}") from None


def default_branch(root: Path) -> str:
    """`origin`'s default branch as this clone last saw it, else `main`."""
    cp = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    ref = cp.stdout.strip() if cp.returncode == 0 else ""
    return ref.split("/", 1)[1] if "/" in ref else "main"


BUSY = ("index.lock", "MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD")


def busy(root: Path) -> list[str]:
    """The git operations under way in the checkout — a merge, rebase, cherry-pick or index lock —
    by their marker's name; read by the pull (design §6 *Pull*). A board edit no longer reads it:
    the edit is made in the host agent's own tree, never in the checkout (§4.4, TD-264)."""
    gitdir = Path(_git(root, "rev-parse", "--absolute-git-dir").stdout.strip())
    return [n for n in BUSY if (gitdir / n).exists()]


def author(root: Path) -> str:
    """Who a reply is signed by: the checkout's git `user.name` — the name the commit is authored
    under — else *the person*."""
    try:
        name = _git(root, "config", "user.name").stdout.strip()
    except Refused:
        name = ""
    return name or "the person"


# -- origin's head (design §4.4 *The edit is made on origin's head, and landed there*, TD-264) ----

BOUND = 40.0  # seconds from the fetch to the merge (§4.4)
MOVED = "that line of the board no longer holds this item: reload the Inbox and try again"
NOT_ON_ORIGIN = "this line is not on origin yet: push the checkout first"
PR_BODY = "agentorc's board write-back: one line of the board, at the person's press (cadence §4.5)."


def unreachable(why: str) -> Refused:
    return Refused(f"origin could not be reached ({why}): the edit was not made")


class _Clock:
    """The press's one bound, shared by every git and forge call in it."""

    def __init__(self, bound: float = BOUND) -> None:
        self.end = time.monotonic() + bound

    def left(self, step: str) -> float:
        left = self.end - time.monotonic()
        if left <= 0:
            raise unreachable(f"{step}: the {BOUND:g} s bound ran out")
        return min(left, GIT_TIMEOUT)


def _why(cp: subprocess.CompletedProcess[str], what: str) -> str:
    lines = (cp.stderr or cp.stdout or "").strip().splitlines()
    return f"{what}: {lines[-1][:200] if lines else f'exit {cp.returncode}'}"


def tree_dir(root: Path) -> Path:
    """The host agent's own tree for the repo at `root`: `~/.agentorc/boards/<repo>/tree` (§4.4)."""
    return paths.home() / "boards" / Path(root).name / "tree"


def _tree(root: Path, clock: _Clock) -> tuple[Path, str]:
    """Fetch origin's default branch and give the tree at its head: kept between presses, made as
    the rollback's tree is (§6 *A rollback*) when it is missing, and remade once when it cannot be
    reset. Raises the unreachable refusal when the fetch fails."""
    if _git(root, "rev-parse", "--show-toplevel").returncode != 0:
        raise Refused(f"{root} is not a git checkout")
    want = default_branch(root)
    if _git(root, "remote", "get-url", "origin").returncode != 0:
        raise unreachable(f"{root} has no origin")
    cp = _git(root, "fetch", "-q", "origin", want, timeout=clock.left("git fetch"))
    if cp.returncode != 0:
        raise unreachable(_why(cp, "git fetch"))
    t = tree_dir(root)
    common = _common_dir(root)
    why = ""
    for _ in range(2):
        if not (t / ".git").exists() or _common_dir(t) != common:
            # missing, broken, or another clone's of the same name: this checkout's own, made afresh
            _drop_tree(root, t)
            t.parent.mkdir(parents=True, exist_ok=True)
            cp = _git(root, "worktree", "add", "-q", "--detach", str(t), f"origin/{want}", timeout=clock.left("tree"))
            if cp.returncode != 0:
                why = _why(cp, "git worktree add")
                continue
        cp = _git(t, "reset", "-q", "--hard", f"origin/{want}", timeout=clock.left("tree"))
        if cp.returncode == 0:
            return t, want
        why = _why(cp, "git reset")
        _drop_tree(root, t)
    raise Refused(f"the board's own tree {t} cannot be made or reset ({why}): the edit was not made")


def _common_dir(root: Path) -> str:
    cp = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return cp.stdout.strip() if cp.returncode == 0 else ""


def _drop_tree(root: Path, t: Path) -> None:
    if t.exists():
        _git(root, "worktree", "remove", "--force", str(t))
        if t.exists():
            shutil.rmtree(t, ignore_errors=True)
    _git(root, "worktree", "prune")


def _reset(t: Path, want: str) -> None:
    """The tree back at origin's head after a failure: what it held is never the next press's."""
    with contextlib.suppress(Refused):
        _git(t, "reset", "-q", "--hard", f"origin/{want}")


def _find(lines: list[str], line: int | None, text: str) -> int | None:
    """The index of the open item whose text is `text`, word for word: the reader's line first,
    then the whole file — origin's lines may sit elsewhere than the checkout's (§4.4)."""

    def holds(ln: str) -> bool:
        m = ITEM_RE.match(ln.rstrip("\n"))
        return m is not None and m.group("text").strip() == text.strip()

    if isinstance(line, int) and 1 <= line <= len(lines) and holds(lines[line - 1]):
        return line - 1
    return next((i for i, ln in enumerate(lines) if holds(ln)), None)


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as e:
        raise Refused(f"cannot read {path}: {e}") from None


def _gh(t: Path, clock: _Clock, step: str, *args: str) -> subprocess.CompletedProcess[str]:
    gh = shutil.which("gh")
    if gh is None:
        raise unreachable("gh is not installed")
    try:
        return subprocess.run(
            [gh, *args], cwd=str(t), capture_output=True, text=True, timeout=clock.left(step), check=False
        )
    except subprocess.TimeoutExpired:
        raise unreachable(f"{step} did not finish within the bound") from None
    except OSError as e:
        raise unreachable(f"gh could not be run: {e}") from None


def _land(root: Path, t: Path, want: str, msg: str, clock: _Clock, still: Any) -> dict[str, Any]:
    """Commit the tree's board and land it on `origin/<want>` by the one road the forge's rule
    allows (§4.4): a branch of the host agent's own, a PR it opens and squash-merges at once. A
    refused merge closes the PR and deletes the branch, and refuses the press as a moved line when
    `still()` — origin's board read again — no longer holds the item, else as unreachable."""
    cp = _git(t, "commit", "--quiet", "-m", msg, "--only", "--", str(BOARD), timeout=clock.left("git commit"))
    if cp.returncode != 0:
        _reset(t, want)
        raise Refused(f"the commit failed, and the board is as it was: {_why(cp, 'git commit')}")
    sha = _git(t, "rev-parse", "HEAD").stdout.strip()
    branch = f"board/{Path(root).name}/{datetime.now(UTC):%Y%m%d%H%M%S}-{sha[:7]}"
    pushed = False
    try:
        cp = _git(t, "push", "-q", "origin", f"HEAD:refs/heads/{branch}", timeout=clock.left("git push"))
        if cp.returncode != 0:
            raise unreachable(_why(cp, "git push"))
        pushed = True
        cp = _gh(
            t,
            clock,
            "gh pr create",
            "pr",
            "create",
            "--base",
            want,
            "--head",
            branch,
            "--title",
            msg,
            "--body",
            PR_BODY,
        )
        if cp.returncode != 0:
            raise unreachable(_why(cp, "gh pr create"))
        url = (cp.stdout or "").strip().splitlines()
        try:
            n = int(url[-1].rstrip("/").rsplit("/", 1)[1])
        except (IndexError, ValueError):
            raise unreachable(
                f"gh pr create: no pull request number in {url[-1][:120] if url else 'its output'!r}"
            ) from None
        try:
            cp = _gh(t, clock, "gh pr merge", "pr", "merge", str(n), "--squash")
        except Refused as e:  # timed out: it may have landed all the same, so it is read below
            cp = subprocess.CompletedProcess([], 1, "", str(e))
        if cp.returncode != 0:
            why = _why(cp, "gh pr merge")
            moved = landed = False
            with contextlib.suppress(Refused):
                if _git(t, "fetch", "-q", "origin", want, timeout=GIT_TIMEOUT).returncode == 0:
                    # a merge that failed after the forge took it (a timeout, an error on the reply):
                    # origin's board is the edit's, so it landed — a retried add must not write twice
                    landed = _git(t, "diff", "--quiet", sha, f"origin/{want}", "--", str(BOARD)).returncode == 0
                    moved = not landed and not still()
            if not landed:
                _gh_quiet(t, "pr", "close", str(n))
                raise Refused(MOVED) if moved else unreachable(why)
    except Refused:
        if pushed:
            _git_quiet(t, "push", "-q", "origin", "--delete", branch)
        _reset(t, want)
        raise
    _git_quiet(t, "push", "-q", "origin", "--delete", branch)  # a repo that auto-deletes has done it
    _git_quiet(t, "fetch", "-q", "origin", want)
    head = _git(t, "rev-parse", "--short", f"origin/{want}").stdout.strip()
    _reset(t, want)
    return {"commit": head, "message": msg, "pr": n}


def _git_quiet(t: Path, *args: str) -> None:
    with contextlib.suppress(Refused):
        _git(t, *args)


def _gh_quiet(t: Path, *args: str) -> None:
    gh = shutil.which("gh")
    if gh is None:
        return
    with contextlib.suppress(subprocess.TimeoutExpired, OSError):
        subprocess.run([gh, *args], cwd=str(t), capture_output=True, text=True, timeout=GIT_TIMEOUT, check=False)


def write_back(
    root: str | Path, line: int, text: str, action: str, due: str | None = None, *, reply: str = "", answer: str = ""
) -> dict[str, Any]:
    """Make one Snooze, Done, Reply or Decide on the board on origin's head and land it there (§4.4):
    in the host agent's own tree for the repo of the checkout `root`, which is never written.
    Returns `{commit, message, pr}` — `commit` origin's head after the merge; raises `Refused`
    with nothing changed on origin."""
    root = Path(root)
    if action not in ACTIONS:
        raise Refused(f"unknown board action {action!r}: {' or '.join(ACTIONS)}")
    with _EDIT:
        return _write_back(root, line, text, action, due, reply, answer)


def _write_back(
    root: Path, line: int, text: str, action: str, due: str | None, reply: str = "", answer: str = ""
) -> dict[str, Any]:
    if not isinstance(line, int):
        raise Refused(MOVED)
    clock = _Clock()
    if shutil.which("gh") is None:
        raise unreachable("gh is not installed")
    t, want = _tree(root, clock)
    path = t / BOARD
    lines = _read_lines(path) if path.exists() else []
    at = _find(lines, line, text)
    if at is None:
        raise Refused(NOT_ON_ORIGIN if _ahead(root, want, line, text) else MOVED)
    by = author(root) if action == "reply" else ""
    lines[at] = edit_line(lines[at], text, action, due, reply=reply, by=by, answer=answer)
    msg = message(text, action, due, answer=answer)
    path.write_text("".join(lines), encoding="utf-8")

    def still() -> bool:
        return _find(_read_origin(t, want), None, text) is not None

    return {**_land(root, t, want, msg, clock, still), "line": at + 1}


def _ahead(root: Path, want: str, line: int | None, text: str) -> bool:
    """Whether the checkout holds the item where origin does not because the checkout's board is
    *ahead* — an uncommitted edit, or a board commit origin lacks — rather than behind: a checkout
    that has not pulled still shows a line origin has since done or replied to, which is a moved
    line, not an unpushed one."""
    mine = _read_lines(root / BOARD) if (root / BOARD).exists() else []
    if _find(mine, line, text) is None:
        return False
    if _git(root, "status", "--porcelain", "--", str(BOARD)).stdout.strip():
        return True
    last = _git(root, "log", "-1", "--format=%H", "--", str(BOARD)).stdout.strip()
    return bool(last) and _git(root, "merge-base", "--is-ancestor", last, f"origin/{want}").returncode != 0


def _read_origin(t: Path, want: str) -> list[str]:
    cp = _git(t, "show", f"origin/{want}:{BOARD.as_posix()}")
    return cp.stdout.splitlines(keepends=True) if cp.returncode == 0 else []


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
    answer: str | None = None,
) -> dict[str, str | int]:
    """**Put on the board** (design §4.4, the write-back's first add; TD-140): one new line at the top
    of the open items of the checkout `root`'s board, committed there as `agentorc: board <item
    head> (from <entry id>)`, never pushed. Refused on the same conditions as an edit, touching
    nothing. Returns `{commit, message, line}` — `line` the new item's line number.

    With `answer`, the **second add** (§4.4, §4.10 *A question about a reference outlives its asker*,
    TD-216): `text` is the orphaned question's first paragraph and the person's answer follows it on
    the line as a reply does (` — <name>, <date>: <answer>`), committed as `agentorc: answer <item
    head> (from <entry id>)`."""
    root = Path(root)
    today = today or date.today().isoformat()
    words = text
    if answer is not None:
        words = " ".join(str(text or "").split()).rstrip(".") + reply_tail(answer, author(root), today)
    line = item_line(words, due, today, session, host, context)
    with _EDIT:
        clock = _Clock()
        if shutil.which("gh") is None:
            raise unreachable("gh is not installed")
        t, want = _tree(root, clock)
        path = t / BOARD
        lines = _read_lines(path)
        at = _top_of_needs(lines)
        if lines and not lines[-1].endswith("\n") and at == len(lines):
            lines[-1] += "\n"
        lines.insert(at, line)
        head = " ".join(str(text).split()).rstrip(".")
        if len(head) > HEAD_MAX:
            head = head[: HEAD_MAX - 1].rstrip() + "…"
        msg = f"agentorc: {'board' if answer is None else 'answer'} {head} (from {entry})"
        path.write_text("".join(lines), encoding="utf-8")
        # an add moves no line, so a refused merge is never a moved line: always the unreachable refusal
        return {**_land(root, t, want, msg, clock, lambda: True), "line": at + 1}
