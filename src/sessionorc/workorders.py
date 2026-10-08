"""Work orders (design §4.4 *Board write-back*, §6 rule 6, TD-380; built — TD-384): every open
*decided* line of a repo's attention board, listed by the repo reading beside the ledger's entries
as **`board:<key>`** — the first eight hex digits of a sha256 of the line's `item_key()`, which a
snooze, a reply and the decide itself leave unchanged — pickable, High, in every `free-pick` lane
of a team servicing the repo, and counted by rule 8, until a session closes the line.

The board is dev-cadence's file and its reader is dev-cadence's own `nudge_user_attention.py
--report --json` (never a second parser here, as the Inbox's §4.4 rule says): the repo's copy is run
with its own `--fetch`, so a board merely behind origin is read from origin, as the Inbox reads it,
and `item_key` is that same file's function, loaded from it. A `fyi` line and a closed line are
never work orders; a line whose `Decided:` is gone leaves on the next reading."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

BOARD = Path("docs") / "user_attention.md"
READER = Path("scripts") / "nudge_user_attention.py"
PREFIX = "board:"
FETCH_TIMEOUT = 45.0  # the reader's fetch is serial and its own; a read past this is a plain one
READ_TIMEOUT = 20.0
HEAD_MOST = 120
_REF_RE = re.compile(r"board:[0-9a-f]{8}")
# one loaded reader per file, its `item_key` or None for one that will not load, kept while its mtime holds
_keys: dict[str, tuple[float, Callable[[str], str] | None]] = {}


def is_work_order(ref: str) -> bool:
    """Whether a reference names a work order: `board:` and eight hex digits."""
    return bool(_REF_RE.fullmatch(str(ref)))


def _item_key(script: Path) -> Callable[[str], str] | None:
    """The reader's own `item_key`, loaded from that file and kept while its mtime holds — a reader
    that will not load is kept as None too, so a page asking per row never runs it again (TD-384)."""
    try:
        stamp = script.stat().st_mtime
    except OSError:
        return None
    held = _keys.get(str(script))
    if held is not None and held[0] == stamp:
        return held[1]
    got: Callable[[str], str] | None = None
    spec = importlib.util.spec_from_file_location(f"_ao_board_reader_{abs(hash(str(script)))}", script)
    if spec is not None and spec.loader is not None:
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
            got = getattr(mod, "item_key", None)
        except (Exception, SystemExit):  # noqa: BLE001 — a reader that will not load lists no work orders
            got = None
    got = got if callable(got) else None
    _keys[str(script)] = (stamp, got)
    return got


def key(text: str, item_key: Callable[[str], str]) -> str:
    """`board:<key>` for an open item's text as the report gives it — its line less the open tick."""
    return PREFIX + hashlib.sha256(item_key(f"- [ ] {text}").encode("utf-8")).hexdigest()[:8]


def ref(root: str | Path, text: str) -> str:
    """`board:<key>` for an open line of the board under `root`, by that repo's own reader — what a
    page that holds the line but not the reading names it by (§4.5 screen 6, TD-384) — or empty
    where the repo has no reader or its key cannot take the line."""
    item_key = _item_key(Path(root) / READER) if root and text else None
    if item_key is None:
        return ""
    try:
        return key(text, item_key)
    except Exception:  # noqa: BLE001 — a line the reader's own key cannot take has no reference
        return ""


def head(text: str) -> str:
    """A line's head text, the work order's title: its bold head where it has one, else what
    follows the `(session …) —` lead, clipped."""
    bold = re.search(r"\*\*(.+?)\*\*", text)
    out = bold.group(1) if bold else text.split(" — ", 1)[-1]
    out = " ".join(out.split()).rstrip(" .")
    return out if len(out) <= HEAD_MOST else out[: HEAD_MOST - 1].rstrip() + "…"


def _report(argv: list[str], timeout: float) -> dict[str, Any] | None:
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0:
        return None
    try:
        got = json.loads(cp.stdout)
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def read(root: str | Path, *, fetch: bool = True) -> dict[str, Any]:
    """The repo's work orders: `{orders: [...], at_origin}` — each `{id, title, text, line, decided:
    {text, date}, refs, session, kind}` — or `{error}` when the board could not be read. No board,
    or no reader in the repo, is no work orders and no error."""
    root = Path(root)
    board, script = root / BOARD, root / READER
    if not board.is_file() or not script.is_file():
        return {"orders": []}
    base = [sys.executable, str(script), "--report", "--json", "--board", str(board)]
    got = _report([*base, "--fetch"], FETCH_TIMEOUT) if fetch else None
    if got is None:
        got = _report(base, READ_TIMEOUT)
    if got is None:
        return {"error": f"{READER} --report --json could not be read"}
    item_key = _item_key(script)
    if item_key is None:
        return {"error": f"{READER} has no item_key to name a line by"}
    orders: list[dict[str, Any]] = []
    for b in got.get("boards") or []:
        for it in (b.get("items") or []) if isinstance(b, dict) else []:
            if not isinstance(it, dict) or not isinstance(it.get("decided"), dict) or it.get("kind") == "fyi":
                continue
            text = str(it.get("text") or "")
            if not text:
                continue
            try:
                ref = key(text, item_key)
            except Exception:  # noqa: BLE001 — a line the reader's own key cannot take is skipped, not fatal
                continue
            orders.append(
                {
                    "id": ref,
                    "title": head(text),
                    "text": text,
                    "line": it.get("line"),
                    "decided": {"text": str(it["decided"].get("text") or ""), "date": it["decided"].get("date")},
                    "refs": [str(r) for r in it.get("refs") or []],
                    "session": it.get("session"),
                    "kind": it.get("kind"),
                }
            )
    return {"orders": orders}


def entry(order: dict[str, Any]) -> dict[str, Any]:
    """A work order as the lanes and the pages read an entry (§4.4 *Repo facts*): High, pickable,
    `free-pick`'s kind, no owner — `owner:<word>` never narrows one out (§6 rule 6) — and marked
    `work_order`, so a reader that counts the ledger's priorities can leave it out."""
    return {
        "id": order["id"],
        "title": order.get("title") or "",
        "priority": "High",
        "owner": "",
        "kind": "",
        "type": "debt",
        "blocked_by": [],
        "pickable": "yes",
        "for_page": "pickable",
        "work_order": True,
        "decided": order.get("decided"),
        "refs": order.get("refs") or [],
        "session": order.get("session"),
        "line": order.get("line"),
        "text": order.get("text") or "",
    }
