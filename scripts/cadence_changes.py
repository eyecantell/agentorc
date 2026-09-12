#!/usr/bin/env python3
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/cadence_changes.py
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
"""Tell a session which cadence conventions changed since this worktree last heard.

    cadence_changes.py --hook          SessionStart: print unseen entries once per worktree, then remember them
    cadence_changes.py --list [--since 30d]   plain listing of origin's entries (no marker written)
    cadence_changes.py --json          origin's entries as JSON: heading, date, body, landed (commit time on origin)

Source of truth is ``docs/cadence-changes.md`` on ``origin/<default>`` — read with
``git show``, never fetched here: the attention hook that runs just before this one in
the seeded settings already fetches origin under its own budget, and a second fetch
per session start would be redundant and racy. Offline, the ref is whatever the last
fetch left, and that is fine — a convention change is not urgent by the minute.

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
Stdlib only. Five local git plumbing calls at most in --hook, each bounded to 5 s; no network.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

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
    """origin's default branch, or None when no candidate ref exists here (never a guess that misfires)."""
    d = git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=root)
    candidates = [d.strip().removeprefix("origin/")] if d and d.strip() else []
    candidates += [b for b in ("main", "master") if b not in candidates]
    for b in candidates:
        if (
            git(["rev-parse", "--verify", "-q", f"refs/remotes/origin/{b}"], cwd=root)
            is not None
        ):
            return b
    return None


def origin_entries(root, with_landed=False):
    branch = default_branch(root)
    if branch is None:
        return None, []
    ref = f"origin/{branch}"
    text = git(["show", f"{ref}:{CHANGES}"], cwd=root)
    entries = parse(text)
    if with_landed:
        for e in entries:
            # the commit that introduced the heading on origin's default branch — the earliest -S hit
            log = git(
                ["log", "--format=%cI", "-S", e["heading"], ref, "--", CHANGES],
                cwd=root,
                timeout=15,
            )
            e["landed"] = log.strip().splitlines()[-1] if log and log.strip() else None
    return ref, entries


def local_headings(root):
    path = os.path.join(root, CHANGES)
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


def hook(root):
    ref, entries = origin_entries(root)
    if not entries:
        return 0
    local = local_headings(root)
    mp = marker_path(root)
    seen = read_marker(mp or "")
    today = dt.datetime.now(dt.timezone.utc).date()
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
            "cannot read origin: no origin/HEAD, origin/main or origin/master here (git fetch origin; git remote set-head origin -a)",
            file=sys.stderr,
        )
        return 2
    if since:
        m = re.fullmatch(r"(\d+)d", since)
        days = int(m.group(1)) if m else None
        if days is None:
            print(f"bad --since {since!r}: use Nd", file=sys.stderr)
            return 2
        today = dt.datetime.now(dt.timezone.utc).date()
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
        if args.hook:
            return 0
        print("not in a git repo", file=sys.stderr)
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
