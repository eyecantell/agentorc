"""Design §6 *Keeping a team running*, rule 10 (TD-247, TD-258): the cadence check as a policy of
the tick. The script's verdict needs no reader — `scripts/check_cadence.py --pr <n> --json` gives a
status per row — so the home runs it on every `done` a supervised member reports with a PR, keeps
what it read on the record's `checks`, and tells the member in fixed words. Nothing here is typed
by a session, and the words name the script's `rule` field, never its `detail`.

The two reads (`head`, `check`) shell out and are called in a thread; the rest is pure."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from sessionorc import naming
from sessionorc.models import PERSON, Session

SCRIPT = "scripts/check_cadence.py"
VERDICTS = ("pass", "fail", "unknown")


def has_script(root: Path | str) -> bool:
    """Whether `root` is a checkout the home can see that carries the script: a repo not on
    dev-cadence, or a path that is not a directory here, gives no reading."""
    return (Path(root) / SCRIPT).is_file()


def head(root: Path | str, pr: int, timeout: float = 20.0) -> tuple[str, bool] | None:
    """The PR's head commit and whether it is merged, or None when `gh` could not say (no such PR,
    no network): an entry keeps the head it was read at, so a PR whose head moved is read again."""
    try:
        cp = subprocess.run(
            ["gh", "pr", "view", str(pr), "--json", "headRefOid,state"],
            capture_output=True, text=True, timeout=timeout, cwd=str(root),
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    if cp.returncode != 0:
        return None
    try:
        out = json.loads(cp.stdout or "{}")
    except json.JSONDecodeError:
        return None
    sha = out.get("headRefOid") if isinstance(out, dict) else None
    if not isinstance(sha, str) or not sha:
        return None
    return sha, out.get("state") == "MERGED"


def check(root: Path | str, pr: int, timeout: float = 120.0) -> dict[str, Any] | None:
    """One run of the script on one PR in `root`: `{verdict, failed}` — `failed` the `rule` of each
    row whose status is `fail`, in the script's order — or None for **no reading**: no script, an
    exit of 2 (no such PR), a run that could not be made, or output that is not the script's."""
    if not has_script(root):
        return None
    try:
        cp = subprocess.run(
            ["python3", SCRIPT, "--pr", str(pr), "--json"],
            capture_output=True, text=True, timeout=timeout, cwd=str(root),
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    if cp.returncode == 2:
        return None
    return parse(cp.stdout, pr)


def parse(text: str, pr: int) -> dict[str, Any] | None:
    """`{verdict, failed}` from the script's `--json`, or None when it is not that."""
    try:
        out = json.loads(text or "")
    except json.JSONDecodeError:
        return None
    prs = out.get("prs") if isinstance(out, dict) else None
    got = next((p for p in prs or [] if isinstance(p, dict) and p.get("pr") == pr), None)
    if got is None or got.get("verdict") not in VERDICTS:
        return None
    rows = [r for r in got.get("rows") or [] if isinstance(r, dict)]
    return {"verdict": got["verdict"], "failed": [str(r.get("rule")) for r in rows if r.get("status") == "fail"]}


def entry_of(checks: list[dict[str, Any]], pr: int) -> dict[str, Any] | None:
    return next((c for c in checks if c.get("pr") == pr), None)


def stale(old: dict[str, Any] | None, done_at: str) -> bool:
    """Whether the entry must be looked at again **without asking for the head**: never read, an
    `unknown` kept from the last cadence, or a `done` naming the PR after the read. A merged PR's
    head no longer moves, so a settled read of one stands."""
    return old is None or old.get("verdict") == "unknown" or done_at > str(old.get("at") or "")


def record(old: dict[str, Any] | None, pr: int, sha: str, merged: bool, got: dict[str, Any], at: str) -> dict[str, Any]:
    """The entry one read leaves: `{pr, at, sha, verdict, failed}`, `merged` once the PR is, and
    the marks. **`pass`** removes `row`. **`fail`** on a merged PR — which no re-report cures — or
    read again after the member was told (`told`) sets `row`, the Inbox row's mark. **`unknown`**
    tells nothing and changes no mark. `told` and `read_by` are carried."""
    new: dict[str, Any] = {"pr": pr, "at": at, "sha": sha, "verdict": got["verdict"], "failed": list(got["failed"])}
    if merged:
        new["merged"] = True
    for key in ("told", "row", "read_by"):
        if old and old.get(key):
            new[key] = old[key]
    if got["verdict"] == "pass":
        new.pop("row", None)
    elif got["verdict"] == "fail" and (merged or (old and old.get("verdict") == "fail" and old.get("told"))):
        new.setdefault("row", at)
    return new


def untold(c: dict[str, Any]) -> bool:
    """A first fail the member has not been told of. A merged PR's fail is the row's, not a line's."""
    return c.get("verdict") == "fail" and not c.get("told") and not c.get("merged")


def _what(c: dict[str, Any]) -> str:
    return ", ".join(str(r) for r in c.get("failed") or []) or "no row named"


def line(c: dict[str, Any], ref: str) -> str:
    """The fixed line typed into an idle member's composer."""
    return (
        f"[agentorc] PR #{c['pr']} failed the cadence check: {_what(c)} — fix it, then report "
        f"`ao progress done {ref} --pr {c['pr']}` again"
    )


def clause(checks: list[dict[str, Any]]) -> str:
    """The clause at the end of every `ao` reply while an open PR of the member's fails: *PR #842
    fails the cadence check: review, ledger*. "" when none does."""
    return "; ".join(
        f"PR #{c['pr']} fails the cadence check: {_what(c)}"
        for c in checks
        if c.get("verdict") == "fail" and not c.get("merged")
    )


def read_by(s: Session, pr: int) -> str | None:
    """Who read the PR, as the home alone knows it (§4.9b *The reader*): the sender of a `reply` on
    the thread of an `ask` of this record's that carries `pr`, when that sender was one the ask
    named and is not the person. None otherwise — the `review` row is then *recorded*, the
    script's own word for a comment that was posted, and never *verified*."""
    asks = {e.root or e.id: e for e in s.outbox if e.kind == "ask" and e.pr == pr}
    for r in s.inbox:
        ask = asks.get(r.root) if r.kind == "reply" else None
        if ask is not None and r.from_ != PERSON and _bare(r.from_) in {_bare(x) for x in ask.to}:
            return r.from_
    return None


def _bare(address: str) -> str:
    return naming.split_address(address)[0]


def said(c: dict[str, Any]) -> str:
    """One check as `ao status -v` prints it: *#842 fail: review, ledger · review recorded*."""
    text = f"#{c.get('pr')} {c.get('verdict')}"
    if c.get("verdict") == "fail":
        text += f": {_what(c)}"
    who = c.get("read_by")
    return text + (f" · review read by {who}" if who else " · review recorded")
