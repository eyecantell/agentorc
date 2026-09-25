#!/usr/bin/env python3
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/board_edit.py
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
"""Make one tool-made edit to one attention-board item, the same way for every tool (TD-036).

    board_edit.py snooze --board PATH --line N --expect HEAD --to YYYY-MM-DD
    board_edit.py done   --board PATH --line N --expect HEAD
    board_edit.py decide --board PATH --line N --expect HEAD --answer TEXT
        [--tool NAME] [--session NAME] [--no-commit]

snooze  sets the item's ``Due:`` date (adds one when it has none).
done    ticks the item: ``- [ ]`` becomes ``- [x]``, and the readers stop listing it.
decide  records the person's answer: ``Decided: TEXT (today).`` at the end of the line,
        replacing an earlier one. TEXT may be one of the item's ``Answers:`` or any
        free text. A decided item stays on the board, and stays due, until a session
        acts on it and removes it — it is the session's work order now (cadence.md §3).

``--line`` is the 1-based line ``nudge_user_attention.py --report --json`` prints for
the item, and ``--expect`` the start of its text (or of the raw line) as the tool last
showed it. The edit is refused, exit 3, when line N is not an open item or no longer
starts with HEAD — the board moved under the tool. Only line N is rewritten; the
``Swept:``/``Swept-deep:`` stamps and every other line keep their bytes.

Unless ``--no-commit``, the edit is committed on its own, board file only, with the
fixed message of cadence.md §4's tool-made-edit carve-out —
``<tool>: <action> <item head>[ to <date> | : <answer>] (session <name>)`` — where
``<name>`` is the session the item names (``(session <id> …``), or ``--session``. A
board with uncommitted changes is refused before anything is written, since the commit
would carry them. The script never pushes: the carve-out's push happens whenever the
checkout next pushes, under ``ALLOW_MAIN_PUSH=1``.

The field formats are ``nudge_user_attention.py``'s (imported, never re-typed: one
reader, one writer — cadence.md §7 parity pairs). Every edit is re-parsed before it is
written, and refused if the reader would not read back what was meant.

Exit: 0 edited (and committed); 1 git failed (the board is restored); 2 usage; 3 refused.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nudge_user_attention as N  # noqa: E402  # sibling SYNC script: the reader whose formats this writes

SESSION_RE = re.compile(r"\(session ([^\s,)]+)")
HEAD_CHARS = 60


class Refused(Exception):
    pass


FIELDS_RE = re.compile(r"\s+\b(?:Context|Due|Answers|Decided):")


def item_head(text: str) -> str:
    """What the item is about: after its `date (session …) — ` prefix, before its
    `Context:`/`Due:`/`Answers:`/`Decided:` fields, markdown bold dropped, clipped."""
    what = text.split(" — ", 1)[1] if " — " in text else text
    what = FIELDS_RE.split(what, 1)[0].replace("*", "").strip() or text
    return what if len(what) <= HEAD_CHARS else what[: HEAD_CHARS - 1] + "…"


def _trailing_field_start(body: str) -> int | None:
    """Where the item's Answers:/Decided: trailer begins, for inserting a Due: before it."""
    starts = [m.start() for m in (N.ANSWERS_RE.search(body), N.DECIDED_RE.search(body)) if m]
    return min(starts) if starts else None


def edit_line(body: str, action: str, *, to: date | None = None, answer: str | None = None,
              today: date | None = None) -> str:
    """The item line `body` (no line ending) after `action`; raises Refused."""
    today = today or date.today()  # noqa: DTZ011  # local civil date, as the readers use
    if action == "done":
        new = re.sub(r"^(\s*-\s*)\[ \]", r"\1[x]", body, count=1)
        if N.ITEM_RE.match(new):
            raise Refused("the item still reads as open after ticking it")
        return new
    text = N.ITEM_RE.match(body).group("text")
    if action == "snooze":
        m = N.DUE_RE.search(body)
        if m:
            new = body[: m.start("due")] + to.isoformat() + body[m.end("due"):]
        else:
            at = _trailing_field_start(body)
            if at is None:
                head = body.rstrip()
                new = f"{head}{'' if head.endswith(('.', '?', '!')) else '.'} Due: {to.isoformat()}."
            else:
                new = f"{body[:at].rstrip()} Due: {to.isoformat()}. {body[at:]}"
        got = N.parse_board(new, warn=False)
        if not got or got[0].due != to or got[0].answers != N.parse_answers(text) \
                or got[0].decided != N.parse_decided(text):
            raise Refused(f"after the edit the reader does not read Due: {to.isoformat()} "
                          "with the item's other fields unchanged")
        return new
    if action == "decide":
        answer = " ".join(answer.split())
        if not answer:
            raise Refused("--answer is empty")
        field = f"Decided: {answer} ({today.isoformat()})."
        if N.parse_decided(text) is not None:
            m = N.DECIDED_RE.search(body)
            new = f"{body[: m.start()].rstrip()} {field}"
        else:
            # the field starts a sentence (the reader's rule): end an unpunctuated item first
            head = body.rstrip()
            new = f"{head}{'' if head.endswith(('.', '?', '!')) else '.'} {field}"
        got = N.parse_board(new, warn=False)
        if not got or got[0].decided != (answer, today) or got[0].answers != N.parse_answers(text) \
                or got[0].due != N.parse_board(body, warn=False)[0].due:
            raise Refused(f"the reader would not read back 'Decided: {answer}' with the item's "
                          "other fields unchanged — rephrase the answer")
        return new
    raise Refused(f"unknown action {action}")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, check=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("action", choices=("snooze", "done", "decide"))
    ap.add_argument("--board", required=True, help="the board file (docs/user_attention.md)")
    ap.add_argument("--line", type=int, required=True, help="1-based line of the item")
    ap.add_argument("--expect", required=True, help="the start of the item's text as last shown")
    ap.add_argument("--to", help="snooze: the new Due: date, YYYY-MM-DD")
    ap.add_argument("--answer", help="decide: the person's answer")
    ap.add_argument("--tool", default="board_edit", help="names the tool in the commit message")
    ap.add_argument("--session", help="the session named in the commit message (default: the item's)")
    ap.add_argument("--no-commit", action="store_true", help="edit the file only")
    a = ap.parse_args(argv)

    to = None
    if a.action == "snooze":
        if not a.to or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.to):
            ap.error("snooze needs --to YYYY-MM-DD")
        try:
            to = date.fromisoformat(a.to)
        except ValueError:
            ap.error(f"--to {a.to} is not a date")
    elif a.to:
        ap.error("--to is for snooze only")
    if a.action == "decide" and a.answer is None:
        ap.error("decide needs --answer")
    if a.action != "decide" and a.answer is not None:
        ap.error("--answer is for decide only")
    if not a.expect.strip():
        ap.error("--expect is empty — pass the item's text as the tool showed it")

    board = Path(a.board).resolve()
    try:
        raw = board.read_bytes()
    except OSError as e:
        print(f"board_edit: cannot read {board}: {e}", file=sys.stderr)
        return 3
    lines = raw.decode("utf-8", errors="surrogateescape").splitlines(keepends=True)
    root = None
    try:
        if not 1 <= a.line <= len(lines):
            raise Refused(f"line {a.line} is outside the board ({len(lines)} lines)")
        line = lines[a.line - 1]
        body = line.rstrip("\r\n")
        eol = line[len(body):]
        m = N.ITEM_RE.match(body)
        if not m:
            raise Refused(f"line {a.line} is not an open item: {body[:80]!r}")
        text = m.group("text").strip()
        exp = a.expect.strip()
        if not (text.startswith(exp) or body.lstrip().startswith(exp)):
            raise Refused(f"line {a.line} no longer starts with the expected item — the board "
                          f"moved; re-read it. Line {a.line} is: {body[:80]!r}")
        if not a.no_commit:
            r = _git(board.parent, "rev-parse", "--show-toplevel")
            if r.returncode != 0:
                raise Refused(f"{board} is not in a git work tree (pass --no-commit to edit only)")
            root = Path(r.stdout.strip())
            r = _git(root, "status", "--porcelain", "--", str(board))
            if r.returncode != 0 or r.stdout.strip():
                raise Refused(f"{board} has uncommitted changes; the carve-out commit would carry "
                              "them — commit or discard them first")
        new_body = edit_line(body, a.action, to=to, answer=a.answer)
    except Refused as e:
        print(f"board_edit: refused: {e}", file=sys.stderr)
        return 3

    what = (f" to {a.to}" if a.action == "snooze"
            else f": {' '.join(a.answer.split())}" if a.action == "decide" else "")
    lines[a.line - 1] = new_body + eol
    board.write_bytes("".join(lines).encode("utf-8", errors="surrogateescape"))
    if a.no_commit:
        print(f"board_edit: {a.action} line {a.line}{what} (not committed)")
        return 0
    sm = SESSION_RE.search(text)
    session = a.session or (sm.group(1) if sm else "n/a")
    msg = f"{a.tool}: {a.action} {item_head(text)}{what} (session {session})"
    r = _git(root, "commit", "-q", "-m", msg, "--", str(board))
    if r.returncode != 0:
        board.write_bytes(raw)
        print(f"board_edit: git commit failed, board restored: {r.stderr.strip()}", file=sys.stderr)
        return 1
    sha = _git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    print(f"board_edit: {a.action} line {a.line}{what} — committed {sha} (not pushed; "
          "cadence.md §4: push with ALLOW_MAIN_PUSH=1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
