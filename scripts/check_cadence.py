#!/usr/bin/env python3
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/check_cadence.py
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
"""The cadence check: did a PR follow cadence.md §1–§5? One row per rule, read from git and gh.

A DETECTOR, never a gate (cadence.md §7): it is not wired into a hook and blocks
nothing. Three callers run the same code — the working session before it merges
(the /cadence skill), an orchestrator after a worker reports a PR done, and the
weekly /stranded-work sweep over the window (`--since 7d`), which is the backstop
for the two that were skipped.

    check_cadence.py --pr N          one PR, open or merged
    check_cadence.py --branch NAME   the PR whose head is NAME (a worker's worktree branch)
    check_cadence.py --since 7d      every PR merged in the window (also 24h, 2w, or YYYY-MM-DD)
    --json                           machine form: {"prs": [{"pr", "url", "rows", "verdict"}], "verdict"}

Rows (status pass | fail | na | unknown — `unknown` is evidence gh could not give,
never a fake pass):

    pr        the PR exists; when merged, the merge commit has one parent (squash is
              inferred from that — GitHub records no merge method)
    review    a `cadence-review:` comment (format below) exists, its verdict is not
              BLOCK, and — for a merged PR — it was created AND last edited before the
              merge. Self-attested: the session that ran the review also posts the
              comment, so this row proves the ritual was recorded, not that it was
              honest. Weigh it accordingly.
    ci        every check run on the head commit succeeded (na: none configured;
              unknown: the head is gone, as it is on old merged PRs once the branch
              was auto-deleted)
    ledger    a TD-NNN named in the PR TITLE means the PR touches the ledger or the
              archive (status line, new entry, or the move to the archive)
    worktree  a merged PR's local worktree is gone or reapable (clean including
              untracked, and landed by the §1 scoped diff); it is normal for it to
              still exist until the anchor's next pull, so existing is not the fault
              — dirty or unlanded is. Open PR: na. Not this machine's branch: na.
    pushed    an open PR's local branch has no commits origin lacks (§3: unpushed
              work is invisible to every other machine)
    deploy    if the consumer ships an executable `scripts/cadence_deploy_check.sh`
              (adapt per repo, cadence §5) it is run as `<script> <pr> <sha>`; exit
              0 pass, 1 fail, 2 na, anything else unknown. Absent: na.

Exit: 0 every row pass or na; 1 any fail; 2 nothing could be checked (no gh, no PR,
or every row unknown). `unknown` rows never turn a 0 into a 1.

The review comment (writer: the /cadence skill; reader: REVIEW_RE below — a parity
pair, cadence §7) has a machine-readable first line:

    cadence-review: SHIP | FIXED | BLOCK · <reviewer model> · <code|docs> · <n> findings

and the findings in prose after it. SHIP: nothing to change; FIXED: findings were
fixed before merge; BLOCK: do not merge. Posted with `gh pr comment` by the session,
never edited afterwards (an edit after the merge fails the row).

Out of scope, deliberately: cadence §3's board rule and §4's auto-merge threshold
are judgements git cannot settle; §2 numbering collisions surface as CI or merge
conflicts already.

Stdlib only. Never prompts. Works from the anchor checkout, a worktree, or a
directory with no clone at all (then worktree/pushed/deploy are na).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys

REVIEW_RE = re.compile(
    r"^\s*cadence-review:\s*(?P<verdict>SHIP|FIXED|BLOCK)\b(?P<rest>.*)$", re.IGNORECASE
)
TD_RE = re.compile(r"\bTD-0*(\d+)\b")
LEDGER_FILES = ("docs/technical_debt.md", "docs/technical_debt_archive.md")
DEPLOY_HOOK = os.path.join("scripts", "cadence_deploy_check.sh")


class GhError(Exception):
    pass


def run(cmd, cwd=None):
    try:
        p = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=60, check=False
        )
    except FileNotFoundError:
        raise GhError(f"{cmd[0]} not installed") from None
    except subprocess.TimeoutExpired:
        raise GhError(f"{cmd[0]} timed out") from None
    if p.returncode != 0:
        raise GhError(
            (p.stderr or p.stdout).strip().splitlines()[-1]
            if (p.stderr or p.stdout).strip()
            else f"{cmd[0]} failed"
        )
    return p.stdout


def gh_json(args):
    return json.loads(run(["gh", *args]))


def parse_ts(s):
    if not s:
        return None
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def git(args, cwd=None):
    try:
        return run(["git", *args], cwd=cwd)
    except GhError:
        return None


def repo_root():
    top = git(["rev-parse", "--show-toplevel"])
    return top.strip() if top else None


# --- rows -------------------------------------------------------------------------


def row(rule, status, detail):
    return {"rule": rule, "status": status, "detail": detail}


def row_pr(pr):
    if pr["state"] != "MERGED":
        return row("pr", "pass", f"open PR #{pr['number']} from {pr['headRefName']}")
    sha = (pr.get("mergeCommit") or {}).get("oid")
    if not sha:
        return row("pr", "unknown", "merged but gh gave no merge commit")
    try:
        parents = gh_json(["api", f"repos/{{owner}}/{{repo}}/commits/{sha}"]).get(
            "parents", []
        )
    except GhError as e:
        return row("pr", "unknown", f"merge commit unreadable: {e}")
    if len(parents) == 1:
        return row("pr", "pass", f"merged {sha[:7]}, one parent (squash inferred)")
    return row(
        "pr",
        "fail",
        f"merge commit {sha[:7]} has {len(parents)} parents — not a squash",
    )


def row_review(pr):
    try:
        comments = gh_json(
            [
                "api",
                "--paginate",
                f"repos/{{owner}}/{{repo}}/issues/{pr['number']}/comments",
            ]
        )
    except GhError as e:
        return row("review", "unknown", f"comments unreadable: {e}")
    merged_at = parse_ts(pr.get("mergedAt"))
    if pr["state"] == "MERGED" and merged_at is None:
        return row(
            "review",
            "unknown",
            "merged but gh gave no mergedAt — cannot place the review before the merge",
        )
    found = []
    for c in comments:
        first = (c.get("body") or "").strip().splitlines()[:1]
        m = REVIEW_RE.match(first[0]) if first else None
        if m:
            found.append(
                (
                    parse_ts(c["created_at"]),
                    parse_ts(c["updated_at"]),
                    m.group("verdict").upper(),
                    m.group("rest").strip(" ·-"),
                )
            )
    if not found:
        return row(
            "review", "fail", "no `cadence-review:` comment on the PR (cadence §4)"
        )
    found.sort(key=lambda t: t[0])
    if merged_at:
        before = [f for f in found if f[0] <= merged_at]
        if not before:
            return row(
                "review",
                "fail",
                f"review comment posted after the merge ({found[0][0].isoformat()} > merge)",
            )
        created, updated, verdict, rest = before[-1]
        if updated and updated > merged_at:
            return row(
                "review",
                "fail",
                f"review comment edited after the merge ({updated.isoformat()})",
            )
    else:
        created, updated, verdict, rest = found[-1]
    if verdict == "BLOCK":
        return row("review", "fail", f"latest review says BLOCK ({rest})")
    when = (
        f"{(merged_at - created).total_seconds() / 60:.0f} min before merge"
        if merged_at
        else created.isoformat()
    )
    return row("review", "pass", f"{verdict} · {rest} · {when} (self-attested)")


def row_ci(pr):
    sha = pr.get("headRefOid")
    if not sha:
        return row("ci", "unknown", "no head sha")
    try:
        # object-shaped endpoint: --paginate walks the pages, --jq flattens each page's runs to one object per line
        out = run(
            [
                "gh",
                "api",
                "--paginate",
                f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs?per_page=100",
                "--jq",
                ".check_runs[]",
            ]
        )
        runs = [json.loads(line) for line in out.splitlines() if line.strip()]
    except GhError as e:
        return row(
            "ci",
            "unknown",
            f"check runs for {sha[:7]} unreadable ({e}) — a deleted head reads this way",
        )
    if not runs:
        return row("ci", "na", "no check runs on the head commit")
    pending = [r["name"] for r in runs if r.get("status") != "completed"]
    if pending:
        return row("ci", "unknown", f"still running on {sha[:7]}: {', '.join(pending)}")
    bad = [
        r["name"]
        for r in runs
        if r.get("conclusion") not in ("success", "skipped", "neutral")
    ]
    if bad:
        return row("ci", "fail", f"not green on {sha[:7]}: {', '.join(bad)}")
    return row("ci", "pass", f"{len(runs)} check run(s) green on {sha[:7]}")


def row_ledger(pr):
    tds = sorted({f"TD-{int(n):03d}" for n in TD_RE.findall(pr.get("title") or "")})
    if not tds:
        return row("ledger", "na", "no TD named in the title")
    paths = {f["path"] for f in pr.get("files") or []}
    touched = [p for p in LEDGER_FILES if p in paths]
    if touched:
        return row(
            "ledger", "pass", f"{', '.join(tds)} named; touches {', '.join(touched)}"
        )
    return row(
        "ledger",
        "fail",
        f"{', '.join(tds)} named in the title but the PR touches neither ledger file (cadence §2)",
    )


def worktrees(root):
    out = git(["worktree", "list", "--porcelain"], cwd=root) or ""
    found, cur = {}, {}
    for line in out.splitlines() + [""]:
        if not line:
            if "branch" in cur:
                found[cur["branch"].removeprefix("refs/heads/")] = cur.get("worktree")
            cur = {}
            continue
        k, _, v = line.partition(" ")
        cur[k] = v
    return found


def landed(root, wt, branch):
    """cadence §1 scoped diff: the branch's own files identical to origin/main."""
    git(["fetch", "-q", "origin"], cwd=root)
    base = git(["merge-base", "origin/main", branch], cwd=root)
    if not base:
        return None
    files = git(["diff", "--name-only", "-z", base.strip(), branch], cwd=root)
    if files is None:
        return None
    names = [f for f in files.split("\0") if f]
    if not names:
        return False  # nothing committed is not landed
    p = subprocess.run(
        ["git", "diff", "--quiet", "origin/main", branch, "--", *names],
        cwd=root,
        capture_output=True,
        check=False,
    )
    return p.returncode == 0


def row_worktree(pr, root):
    if pr["state"] != "MERGED":
        return row("worktree", "na", "open PR — the worktree is in use")
    if not root:
        return row("worktree", "na", "no clone here")
    branch = pr["headRefName"]
    wts = worktrees(root)
    if branch not in wts:
        return row("worktree", "pass", f"no local worktree on {branch}")
    wt = wts[branch]
    status = git(["status", "--porcelain", "--untracked-files=all"], cwd=wt)
    if status is None:
        return row("worktree", "unknown", f"cannot read {wt}")
    if status.strip():
        n = len(status.strip().splitlines())
        return row(
            "worktree",
            "fail",
            f"{wt} still has {n} modified/untracked file(s) after the merge (§1: scratch must not be lost)",
        )
    ok = landed(root, wt, branch)
    if ok is None:
        return row("worktree", "unknown", f"cannot compute landed for {branch}")
    if ok:
        return row(
            "worktree",
            "pass",
            f"{wt} is clean and landed (reapable; the anchor's next pull removes it)",
        )
    return row(
        "worktree", "fail", f"{wt} has commits on {branch} that are not on origin/main"
    )


def row_pushed(pr, root):
    if pr["state"] == "MERGED":
        return row("pushed", "na", "merged")
    if not root:
        return row("pushed", "na", "no clone here")
    branch = pr["headRefName"]
    if git(["rev-parse", "--verify", "-q", f"refs/heads/{branch}"], cwd=root) is None:
        return row("pushed", "na", f"{branch} is not a local branch here")
    if (
        git(["rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}"], cwd=root)
        is None
    ):
        return row("pushed", "fail", f"{branch} has never been pushed")
    ahead = git(["rev-list", "--count", f"origin/{branch}..{branch}"], cwd=root)
    if ahead is None:
        return row("pushed", "unknown", "rev-list failed")
    n = int(ahead.strip() or 0)
    if n:
        return row(
            "pushed", "fail", f"{n} local commit(s) on {branch} not on origin (§3)"
        )
    return row("pushed", "pass", f"{branch} is pushed")


def row_deploy(pr, root):
    if not root:
        return row("deploy", "na", "no clone here")
    hook = os.path.join(root, DEPLOY_HOOK)
    if not (os.path.isfile(hook) and os.access(hook, os.X_OK)):
        return row(
            "deploy", "na", f"no executable {DEPLOY_HOOK} (cadence §5, adapt per repo)"
        )
    sha = ((pr.get("mergeCommit") or {}).get("oid")) or pr.get("headRefOid") or ""
    try:
        p = subprocess.run(
            [hook, str(pr["number"]), sha],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return row("deploy", "unknown", f"{DEPLOY_HOOK}: {e}")
    msg = (
        (p.stdout.strip() or p.stderr.strip()).splitlines()[-1]
        if (p.stdout.strip() or p.stderr.strip())
        else ""
    )
    status = {0: "pass", 1: "fail", 2: "na"}.get(p.returncode, "unknown")
    return row("deploy", status, msg or f"{DEPLOY_HOOK} exit {p.returncode}")


# --- driver -----------------------------------------------------------------------

PR_FIELDS = (
    "number,url,state,title,body,headRefName,headRefOid,mergedAt,mergeCommit,files"
)


def check_pr(number, root):
    pr = gh_json(["pr", "view", str(number), "--json", PR_FIELDS])
    rows = []
    for rule, fn in (
        ("pr", lambda: row_pr(pr)),
        ("review", lambda: row_review(pr)),
        ("ci", lambda: row_ci(pr)),
        ("ledger", lambda: row_ledger(pr)),
        ("worktree", lambda: row_worktree(pr, root)),
        ("pushed", lambda: row_pushed(pr, root)),
        ("deploy", lambda: row_deploy(pr, root)),
    ):
        try:
            rows.append(fn())
        except Exception as e:  # a tool malfunction is `unknown`, never a verdict and never a traceback
            rows.append(row(rule, "unknown", f"{type(e).__name__}: {e}"))
    return {
        "pr": pr["number"],
        "url": pr["url"],
        "title": pr["title"],
        "state": pr["state"],
        "rows": rows,
        "verdict": verdict(rows),
    }


def verdict(rows):
    st = {r["status"] for r in rows}
    if "fail" in st:
        return "fail"
    if st <= {"unknown", "na"} and "unknown" in st:
        return "unknown"
    return "pass"


def since_date(spec):
    m = re.fullmatch(r"(\d+)([hdw])", spec)
    if m:
        n, u = int(m.group(1)), m.group(2)
        delta = {
            "h": dt.timedelta(hours=n),
            "d": dt.timedelta(days=n),
            "w": dt.timedelta(weeks=n),
        }[u]
        return (dt.datetime.now(dt.timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
    dt.date.fromisoformat(spec)  # raises on garbage
    return spec


def select_prs(args):
    if args.pr:
        return [args.pr]
    if args.branch:
        prs = gh_json(
            [
                "pr",
                "list",
                "--head",
                args.branch,
                "--state",
                "all",
                "--limit",
                "5",
                "--json",
                "number,state",
            ]
        )
        if not prs:
            raise GhError(f"no PR with head {args.branch}")
        prs.sort(key=lambda p: (p["state"] != "OPEN", -p["number"]))
        return [prs[0]["number"]]
    when = since_date(args.since)
    prs = gh_json(
        [
            "pr",
            "list",
            "--state",
            "merged",
            "--search",
            f"merged:>={when}",
            "--limit",
            "200",
            "--json",
            "number",
        ]
    )
    return [p["number"] for p in prs]


def print_text(results):
    for res in results:
        print(
            f"PR #{res['pr']} {res['state'].lower()} — {res['title']}  [{res['verdict'].upper()}]"
        )
        for r in res["rows"]:
            print(f"  {r['status'].upper():7} {r['rule']:9} {r['detail']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="cadence check — a detector, not a gate")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pr", type=int)
    g.add_argument("--branch")
    g.add_argument(
        "--since", help="7d, 24h, 2w, or YYYY-MM-DD: every PR merged since then"
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if shutil.which("gh") is None:
        print("CANNOT CHECK: gh is not installed", file=sys.stderr)
        return 2
    root = repo_root()
    if args.since:
        try:
            since_date(args.since)
        except ValueError as e:
            print(f"bad --since: {e}", file=sys.stderr)
            return 2
    try:
        numbers = select_prs(args)
        results = [check_pr(n, root) for n in numbers]
    except GhError as e:
        print(f"CANNOT CHECK: {e}", file=sys.stderr)
        return 2
    except (
        Exception
    ) as e:  # malformed gh output, a missing field: cannot check, not a verdict
        print(f"CANNOT CHECK: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    overall = "pass"
    if any(r["verdict"] == "fail" for r in results):
        overall = "fail"
    elif not results or all(r["verdict"] == "unknown" for r in results):
        overall = "unknown"
    if args.json:
        print(json.dumps({"prs": results, "verdict": overall}, indent=1))
    else:
        print_text(results)
        if not results:
            print("no PRs in the window")
    return {"pass": 0, "fail": 1, "unknown": 2}[overall]


if __name__ == "__main__":
    sys.exit(main())
