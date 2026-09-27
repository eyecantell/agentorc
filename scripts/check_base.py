#!/usr/bin/env python3
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/check_base.py
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
"""What a session resumed on a topic branch should know about its own base (TD-061).

    check_base.py --hook      SessionStart (cadence_hooks.sh): print only what is worth saying
    check_base.py             the same, by hand, plus one line when there is nothing to say

A resumed session's first instinct is to continue its task, and the obvious hand-rolled
test — `git log origin/<default>..<branch>` — reports a squash-merged branch as pending forever
(TD-059). Measured 2026-09-25 in a consumer: three of five resumed sessions were on
branches that had already landed, each spent a round rediscovering it, and one nearly
merged an eleven-day-stale tree over main. So the session is told, in its first context,
up to four facts in this order, and nothing when the branch is current and unlanded:

    landed     the branch's work is on origin/<default> — check_cadence.py's own tests:
               the §1 scoped diff (landed()), else TD-059's landed-since-edited
               (landed_since_edited()), never a copy of either (cadence §7 parity)
    conflict   merging the branch into origin/<default> now would conflict
               (`git merge-tree --write-tree`, git >= 2.38; older git: not checked)
    behind     origin/<default> is BEHIND_WARN or more commits past the fork point
    fetch      origin was last fetched more than FETCH_STALE_HOURS ago (or never), so every
               line above is about an old picture of origin — said only beside one of
               them: alone it is a clone nobody fetched, not a fact about this branch

OFFLINE by design: it reads origin/<default> as the last fetch left it and never fetches —
the attention hook, which runs just before it in the runner, fetched this repo under its
own budget when the repo is on the roster; the `fetch` line says when nothing did. No gh:
naming the PR is left to `gh pr list --head <branch> --state merged`, which the landed
line prints. Silent on the default branch, on a detached HEAD, outside a repo, and when
origin/<default> does not exist. Always exits 0 — a detector, never a gate (cadence §7).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_cadence import default_branch, git, landed, landed_since_edited  # noqa: E402

BEHIND_WARN = 20          # commits on origin/<default> past the fork point
FETCH_STALE_HOURS = 24


def facts(root):
    branch = (git(["symbolic-ref", "-q", "--short", "HEAD"], cwd=root) or "").strip()
    if not branch:
        return []  # detached: no branch of its own to judge
    default = default_branch(root)
    ref = f"origin/{default}"
    if branch == default or git(["rev-parse", "--verify", "-q", f"refs/remotes/{ref}"], cwd=root) is None:
        return []
    out = []
    ok = landed(root, None, branch, fetch=False)
    since = False if ok else landed_since_edited(root, branch)
    if ok or since == "yes":
        # By CONTENT, like every landed test here: a change main made independently, byte
        # for byte, reads the same — its work is then on main either way, which is what
        # the line says; it cannot say whose commit put it there.
        how = "its files match, by content" if ok else "main has edited its files since"
        out.append(
            f"⚠ this branch ({branch}) has LANDED on {ref} ({how}) — its work is done; start new "
            f"work on a fresh branch off {ref}, and never merge or rebase this one onto main "
            f"(cadence §1). Its PR: gh pr list --head {branch} --state merged"
        )
    elif since == "reverted":
        out.append(
            f"⚠ this branch ({branch}) landed on {ref} and main has since REVERTED it — decide "
            "whether the work is still wanted before building on it (cadence §1)"
        )
    else:
        p = subprocess.run(["git", "merge-tree", "--write-tree", "--name-only", "--no-messages", ref, "HEAD"],
                           cwd=root, capture_output=True, text=True, check=False)
        # 0 clean, 1 conflicts; anything else is merge-tree failing (or a git before 2.38
        # without --write-tree) — not evidence of a conflict, so nothing is said.
        if p.returncode == 1:
            files = [f for f in p.stdout.splitlines()[1:] if f.strip()]
            shown = ", ".join(files[:5]) + (" …" if len(files) > 5 else "")
            out.append(f"⚠ merging {branch} into {ref} now would CONFLICT ({shown}) — rebase or merge "
                       f"{ref} in before you build further")
    n = (git(["rev-list", "--count", f"HEAD..{ref}"], cwd=root) or "").strip()
    if n.isdigit() and int(n) >= BEHIND_WARN:
        out.append(f"ℹ {ref} is {n} commits past this branch's fork point — the repo has moved "
                   "under it; read what changed before relying on your picture of main")
    real = len(out)
    # FETCH_HEAD is per worktree: the attention hook fetches from the main checkout (the
    # common dir's copy), a session's own `git fetch` writes its worktree's. The newer counts.
    ages = []
    for args in (["--git-common-dir"], ["--git-path", "FETCH_HEAD"]):
        p = (git(["rev-parse", "--path-format=absolute", *args], cwd=root) or "").strip()
        if p:
            fh = p if args[0] == "--git-path" else os.path.join(p, "FETCH_HEAD")
            try:
                ages.append((time.time() - os.stat(fh).st_mtime) / 3600)
            except OSError:
                pass
    if not ages:
        out.append(f"ℹ origin has never been fetched in this clone — the lines above read {ref} as cloned")
    elif min(ages) > FETCH_STALE_HOURS:
        out.append(f"ℹ origin was last fetched {min(ages) / 24:.1f} days ago — {ref} here may be behind "
                   "the real one; `git fetch origin` before trusting any of this")
    # A fetch note alone is not worth a session's attention: say it only beside a real fact.
    return out if real else []


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    hook = "--hook" in argv
    root = (git(["rev-parse", "--show-toplevel"], cwd=os.environ.get("CLAUDE_PROJECT_DIR") or None) or "").strip()
    lines = facts(root) if root else []
    for line in lines:
        print(line)
    if not lines and not hook:
        print("base: nothing to report (current, unlanded, or not on a topic branch)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
