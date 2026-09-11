"""Derived report entries (design §4.8, TD-028 step 3): what the tick can work out about a
session's `progress` and `findings` from its branch, its PRs, and the ledger rows those PRs put on
main — so a worker that forgot to declare still shows something, marked `derived`.

Everything here runs in a thread (subprocesses only) and returns plain data. A failure — no git, no
`gh`, no network, a repo without an origin — yields nothing and is never an error: this fills in
what a session forgot and may never gate anything. §9 invariant 10 keeps what it produces out of
the way of what the session declared; that check lives on the record, not here.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from sessionorc.models import FindingEntry, ProgressEntry, normalize_ref

# A work branch is named for its ledger reference (cadence §4: `tdNNN-<slug>`). Only that one shape
# is read: anything looser would turn `release-2` and `ui-3-fix` into ledger references.
BRANCH_RE = re.compile(r"^td-?(\d{1,4})(?:[-_].*)?$", re.IGNORECASE)
# A row the ledger gained: an entry body's heading, or its row in the summary table.
LEDGER_ROW_RE = re.compile(r"^\+(?:#{1,4}\s*(TD-\d+)|\|\s*(TD-\d+)\s*\|)", re.IGNORECASE | re.MULTILINE)
LEDGER_DEFAULT = "docs/technical_debt.md"  # §5's `ledger:` key overrides this once it lands (step 5)
DEFAULT_REFS = ("origin/HEAD", "origin/main", "origin/master")


def branch_ref(branch: str | None) -> str | None:
    """`td028-report-channels` → `TD-028`; anything else → None."""
    m = BRANCH_RE.match((branch or "").strip())
    return normalize_ref(f"TD-{m[1]}") if m else None


def derive(
    directory: Path | str,
    branch: str | None,
    pending: list[tuple[str, int]] | None = None,
    ledger: str = LEDGER_DEFAULT,
) -> tuple[list[ProgressEntry], list[FindingEntry]]:
    """Everything this session's repo can say about it, all entries `derived` (design §4.8):

    - the checked-out branch is named for a reference → that reference is `claimed`;
    - a PR whose head is that branch → the reference carries its number, and a *merged* PR makes it
      `done`. `pending` is the (ref, pr) pairs already derived as claimed, re-checked here so a
      merge still lands after the session has moved on to its next branch;
    - the ledger rows a merged PR of this session put on main, other than the reference itself →
      findings. The squash-merge commit is found by its `(#N)` subject, which is what survives the
      branch (GitHub deletes a merged head, and the session checks out something else next).
    """
    prs = _prs(directory)
    by_head = {p.get("headRefName"): p for p in prs if p.get("headRefName")}
    by_number = {p["number"]: p for p in prs if isinstance(p.get("number"), int)}
    progress: list[ProgressEntry] = []
    merged: dict[int, str] = {}  # PR number → the reference it carries

    if ref := branch_ref(branch):
        pr = by_head.get(branch)
        number = pr.get("number") if pr else None
        done = bool(pr) and _merged(pr)
        progress.append(ProgressEntry(ref=ref, status="done" if done else "claimed", pr=number, source="derived"))
        if done and isinstance(number, int):
            merged[number] = ref
    for ref, number in pending or []:
        pr = by_number.get(number)
        if pr and _merged(pr):
            progress.append(ProgressEntry(ref=ref, status="done", pr=number, source="derived"))
            merged[number] = ref

    findings: list[FindingEntry] = []
    seen = {p.ref for p in progress}
    for number, ref in merged.items():
        for row in _ledger_rows_on_main(directory, number, ledger):
            if row != ref and row not in seen:
                seen.add(row)
                findings.append(FindingEntry(ref=row, source="derived"))
    return progress, findings


def _merged(pr: dict[str, Any]) -> bool:
    return bool(pr.get("mergedAt")) or str(pr.get("state", "")).upper() == "MERGED"


def _git(directory: Path | str, *args: str, timeout: float = 10.0) -> str | None:
    """stdout, or None for anything that went wrong — including a non-zero exit."""
    try:
        cp = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return cp.stdout if cp.returncode == 0 else None


def _prs(directory: Path | str, limit: int = 30, timeout: float = 20.0) -> list[dict[str, Any]]:
    """This repo's recent PRs through `gh` — the only thing that knows a squash merge happened.
    No `gh`, no auth, no network, not a GitHub repo: an empty list, and nothing is derived."""
    try:
        cp = subprocess.run(
            [
                "gh", "pr", "list",
                "--state", "all",
                "--json", "number,state,mergedAt,headRefName",
                "--limit", str(limit),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(directory),
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return []
    if cp.returncode != 0:
        return []
    try:
        out = json.loads(cp.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return [p for p in out if isinstance(p, dict)] if isinstance(out, list) else []


def _default_ref(directory: Path | str) -> str | None:
    for ref in DEFAULT_REFS:
        if _git(directory, "rev-parse", "--verify", "-q", ref) is not None:
            return ref
    return None


def _ledger_rows_on_main(directory: Path | str, pr: int, ledger: str) -> list[str]:
    """The ledger references a merged PR added to main, from its squash-merge commit. Empty unless
    that commit is in this clone's `origin/<default>` — a clone that has not fetched since the merge
    simply derives nothing yet."""
    base = _default_ref(directory)
    if base is None:
        return []
    sha = _git(directory, "log", base, "-n", "1", "--format=%H", "-F", f"--grep=(#{pr})")
    if not (sha := (sha or "").strip()):
        return []
    diff = _git(directory, "show", "--format=", "-U0", sha, "--", ledger)
    if not diff:
        return []
    rows = [normalize_ref(m[1] or m[2]) for m in LEDGER_ROW_RE.finditer(diff)]
    return list(dict.fromkeys(rows))
