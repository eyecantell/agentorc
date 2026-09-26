#!/usr/bin/env python3
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/cadence_changes.py
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
"""Tell a session which cadence conventions changed since this worktree last heard.

    cadence_changes.py --hook          SessionStart: print unseen entries once per worktree, then remember them
    cadence_changes.py --list [--since 30d]   plain listing of origin's entries (no marker written)
    cadence_changes.py --json          origin's entries as JSON: heading, date, body, landed (commit time on origin)

Source of truth is ``docs/cadence-changes.md`` on ``origin/<default>`` — read with
``git show``. The attention hook that runs just before this one fetches this repo
FIRST under its budget (TD-044), so ``--hook`` normally reads a fresh ref and does not
fetch. It fetches only when that did not happen — this repo is not on the machine
roster, or the budget ran out — judged by ``FETCH_HEAD`` older than FETCH_FRESH_S:
then one bounded fetch of the default branch (FETCH_TIMEOUT s, SIGTERM before SIGKILL
so no .lock survives, never --prune). Offline, the ref is whatever the last fetch
left, and that is fine — a convention change is not urgent by the minute.

Why origin and not the worktree's file: a worktree created before a sync PR merged has
old docs, and its sessions would otherwise never hear about the change until they
rebase. Why a per-worktree marker (``<git-dir>/cadence-changes-seen``, one heading per
line, untracked, gone with the worktree) and not a time window: an entry printed at
every session start for two weeks is an entry that gets skimmed past.

What counts as unseen in ``--hook``: a heading in origin's copy that is not in this
worktree's copy of the file (missing file → all of them), or dated within the last
14 days (a fresh, up-to-date worktree still hears about this week's change) — minus
the headings in the marker. Entries are append-only (an amendment is a new dated
entry), which is what makes a heading diff sufficient.

Silent when nothing. Exit 0 on every path; never a traceback into a session's context.
Stdlib only. In --hook: local git plumbing calls, each bounded to 5 s against a hang (not to
fit a sum: with every one at its bound the total passes 25 s), and at most one network
fetch (above). The runner's 25 s child bound (cadence_hooks.sh) is the real ceiling — it
stops the child, and a detector stopped early says nothing rather than something wrong.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import signal
import subprocess
import sys
import time

CHANGES = "docs/cadence-changes.md"
MARKER = "cadence-changes-seen"
RECENT_DAYS = 14
HEADING_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2}) — (.+?)\s*$")


def git(args, cwd=None, timeout=5):
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def parse(text):
    """[{heading, date, title, body}] in file order (newest first by convention)."""
    entries, cur = [], None
    for line in (text or "").splitlines():
        m = HEADING_RE.match(line)
        if m:
            cur = {
                "heading": line.strip(),
                "date": m.group(1),
                "title": m.group(2),
                "body": [],
            }
            entries.append(cur)
        elif cur is not None and line.strip():
            cur["body"].append(line.rstrip())
    for e in entries:
        e["body"] = "\n".join(e["body"])
    return entries


def default_branch(root):
    """The repo's default branch NAME — cadence.md §9 "Default-branch rule": the first of
    origin/HEAD's target, init.defaultBranch, main, master that exists on origin (an
    unset or dangling origin/HEAD falls through); else main. Parity: the shell default_branch() copies and the other Python scripts
    follow the same rule; tests/test_default_branch.sh runs every copy."""
    head = git(["symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"], cwd=root)
    cfg = git(["config", "init.defaultBranch"], cwd=root)
    for b in ((head or "").strip().removeprefix("origin/"), (cfg or "").strip(), "main", "master"):
        if b and git(["rev-parse", "--verify", "-q", f"refs/remotes/origin/{b}"], cwd=root) is not None:
            return b
    return "main"


def origin_default(root):
    """default_branch(), or None when origin has no such ref here (never a guess that misfires)."""
    b = default_branch(root)
    ok = git(["rev-parse", "--verify", "-q", f"refs/remotes/origin/{b}"], cwd=root) is not None
    return b if ok else None


def changes_path(root):
    """The changes file's path in the repo: beside this script's ``../docs`` when the
    script lives inside ``root`` (``scripts/`` in a consumer, ``files/scripts/`` in
    dev-cadence itself, TD-038), else the consumer layout."""
    here = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
    top = os.path.realpath(root)
    if os.path.commonpath([here, top]) == top:
        return os.path.relpath(os.path.join(here, "..", CHANGES), top).replace(os.sep, "/")
    return CHANGES


def origin_entries(root, with_landed=False):
    branch = origin_default(root)
    if branch is None:
        return None, []
    ref = f"origin/{branch}"
    text = git(["show", f"{ref}:{changes_path(root)}"], cwd=root)
    entries = parse(text)
    if with_landed:
        for e in entries:
            # the commit that introduced the heading on origin's default branch — the earliest -S hit
            log = git(
                ["log", "--format=%cI", "-S", e["heading"], ref, "--", changes_path(root)],
                cwd=root,
                timeout=15,
            )
            e["landed"] = log.strip().splitlines()[-1] if log and log.strip() else None
    return ref, entries


def local_headings(root):
    path = os.path.join(root, changes_path(root))
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return {e["heading"] for e in parse(f.read())}
    except OSError:
        return None


def marker_path(root):
    gd = git(["rev-parse", "--git-dir"], cwd=root)
    if not gd:
        return None
    gd = gd.strip()
    return (
        os.path.join(root, gd, MARKER)
        if not os.path.isabs(gd)
        else os.path.join(gd, MARKER)
    )


def read_marker(path):
    try:
        with open(path, encoding="utf-8") as f:
            return {line.rstrip("\n") for line in f if line.strip()}
    except OSError:
        return set()


def write_marker(path, headings):
    try:
        with open(path, "a", encoding="utf-8") as f:
            for h in headings:
                f.write(h + "\n")
    except OSError:
        pass  # a marker that cannot be written costs one repeat, never a failure


def recent(entry, today):
    try:
        return (today - dt.date.fromisoformat(entry["date"])).days <= RECENT_DAYS
    except ValueError:
        return False


FETCH_FRESH_S = 300  # FETCH_HEAD younger than this: the attention hook fetched this start
FETCH_TIMEOUT = 5.0
FETCH_TERM_GRACE = 1.0


def fetch_if_stale(root):
    """TD-044: one bounded, unpruned fetch of the default branch when nothing fetched this
    repo recently. Never raises; a failure leaves the ref as the last fetch left it."""
    common = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=root)
    if not common:
        return
    try:
        age = time.time() - os.path.getmtime(os.path.join(common.strip(), "FETCH_HEAD"))
    except OSError:
        age = None
    if age is not None and age < FETCH_FRESH_S:
        return
    # From the MAIN checkout: in a linked worktree `git -C <worktree> fetch` writes the
    # per-worktree FETCH_HEAD, which the check above never reads, and every start refetched.
    # An explicit refspec updates origin/<branch> even where remote.origin.fetch is narrowed.
    main = os.path.dirname(common.strip())
    branch = default_branch(main)
    cmd = ["git", "-C", main, "fetch", "-q", "--no-prune", "origin",
           f"+refs/heads/{branch}:refs/remotes/origin/{branch}"]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0",
           "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=5"}
    try:
        with subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, env=env, start_new_session=True) as p:
            try:
                p.wait(timeout=FETCH_TIMEOUT)
            except subprocess.TimeoutExpired:
                # the whole group (git and its ssh/helper children); git removes its .lock
                # files on SIGTERM, SIGKILL leaves them
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=FETCH_TERM_GRACE)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
    except OSError:  # ProcessLookupError/PermissionError included: the group already went
        pass


def hook(root):
    fetch_if_stale(root)
    ref, entries = origin_entries(root)
    if not entries:
        return 0
    local = local_headings(root)
    mp = marker_path(root)
    seen = read_marker(mp or "")
    today = dt.date.today()  # noqa: DTZ011  # local, like nudge's due dates (TD-053)
    new = [
        e
        for e in entries
        if (local is None or e["heading"] not in local or recent(e, today))
        and e["heading"] not in seen
    ]
    if not new:
        return 0
    behind = local is None or any(e["heading"] not in local for e in entries)
    n = len(new)
    print(
        f"📣 {n} cadence change{'s' if n != 1 else ''} this worktree has not seen (docs/cadence-changes.md on {ref}):"
    )
    for e in new:
        print(f"  {e['heading']}")
        for line in e["body"].splitlines():
            print(f"    {line}")
    if behind:
        print(
            f"  (this worktree's docs/ are behind {ref} — rebase, or read `git show {ref}:docs/cadence.md`)"
        )
    if mp:
        write_marker(mp, [e["heading"] for e in new])
    return 0


def listing(root, since, as_json):
    ref, entries = origin_entries(root, with_landed=as_json)
    if ref is None:
        print(
            "cannot read origin: no default branch on origin here (cadence.md §9 rule; git fetch origin; git remote set-head origin -a)",
            file=sys.stderr,
        )
        return 2
    if since:
        m = re.fullmatch(r"(\d+)d", since)
        days = int(m.group(1)) if m else None
        if days is None:
            print(f"bad --since {since!r}: use Nd", file=sys.stderr)
            return 2
        today = dt.date.today()  # noqa: DTZ011  # local, like nudge's due dates (TD-053)
        entries = [
            e
            for e in entries
            if (today - dt.date.fromisoformat(e["date"])).days <= days
        ]
    if as_json:
        print(json.dumps({"ref": ref, "entries": entries}, indent=1))
        return 0
    if not entries:
        print("no cadence changes")
        return 0
    for e in entries:
        print(e["heading"])
        for line in e["body"].splitlines():
            print(f"  {line}")
    return 0


def _not_a_repo_or_error():
    """None when cwd is simply not a git repo; else the first line of why git failed."""
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5, check=False,
            env={**os.environ, "LC_ALL": "C", "LANGUAGE": ""},  # git's text is matched below
        )
    except subprocess.TimeoutExpired:
        return "git rev-parse timed out after 5s"
    except OSError as e:
        return f"git could not run ({e.strerror or e})"
    err = (p.stderr or "").strip()
    # silent when there is simply no checkout here (same pair as check_anchor's NOT_A_CHECKOUT)
    if p.returncode == 0 or "not a git repository" in err or "must be run in a work tree" in err:
        return None
    return (err.splitlines() or [f"git rev-parse exited {p.returncode}"])[0]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="cadence convention changes a session has not seen"
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--hook",
        action="store_true",
        help="SessionStart: print unseen entries once per worktree",
    )
    g.add_argument("--list", action="store_true", help="list origin's entries")
    g.add_argument(
        "--json", action="store_true", help="origin's entries as JSON with landed times"
    )
    ap.add_argument("--since", help="with --list: only entries dated within Nd")
    args = ap.parse_args(argv)
    root = git(["rev-parse", "--show-toplevel"])
    if not root:
        why = _not_a_repo_or_error()
        if args.hook:
            # silent only when this is simply not a repo; any other git failure
            # (dubious ownership, timeout, no git) says so in one line (TD-043)
            if why:
                print(f"⚠ cadence-changes check did not run: {why}")
            return 0
        print(why or "not in a git repo", file=sys.stderr)
        return 2
    root = root.strip()
    try:
        if args.hook:
            return hook(root)
        return listing(root, args.since, args.json)
    except Exception as e:  # never a traceback into a session's context
        if args.hook:
            return 0
        print(f"cadence_changes: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
