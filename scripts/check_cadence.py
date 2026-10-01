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
                                     (run verdict pass | fail | unknown | none — one per exit
                                     code 0 | 1 | 3 | 2; none = an empty --since window)

Rows (status pass | fail | na | unknown — `unknown` is evidence gh could not give,
never a fake pass):

    pr        the PR exists; when merged, the merge commit has one parent (squash is
              inferred from that — GitHub records no merge method)
    review    a `cadence-review:` comment (format below) exists, the latest one is not
              BLOCK, and — for a merged PR — it was created AND last edited before the
              merge. The row NAMES the first such comment (the review); a later one
              (before the merge) is listed after it, never read as the review — an author's follow-up
              `FIXED · <own model>` is not who reviewed (TD-076). A standing BLOCK is
              the one thing a later comment decides. Self-attested: the session that
              ran the review also posts the comment, so this row proves the ritual was
              recorded, not that it was honest. Weigh it accordingly.
              A merged PR whose only review comments came AFTER the merge still fails
              — nothing a session does later changes that — but the row carries
              `"acknowledged": true` (text: `acknowledged`): the late review is on
              record and there is nothing left to do — as self-attested as any review
              comment, and a late BLOCK is a plain fail, never acknowledged. A caller that acts on a repeated
              fail skips an acknowledged one (cadence §4.9).
    ci        every check run on the head commit either succeeded or was SKIPPED by
              the workflow, and at least one actually ran. A skip never fails the row
              — a job gated off on purpose must not block a merge — but it is named
              rather than counted green, because "not red" is not "ran". (na: none
              configured; unknown: the head is gone, as it is on old merged PRs once
              the branch was auto-deleted, OR every check was skipped, which is no
              evidence either way and must never read as a pass). An OPEN PR with no
              check run listed is `na` only when nothing says one is coming (TD-079):
              a workflow run already registered for the head commit, or a PR updated
              within CI_REGISTER_GRACE seconds in a repo with an active workflow, is
              unknown — the seconds after a push look exactly like "no CI here", and
              a caller that merges on exit 0 would merge ahead of CI. Re-run.
    base      the PR's base is the default branch (cadence §1, §9 "Default-branch rule").
              A PR on another branch is the wrong-base stack of §1: merged, its work
              is on that branch and not on the default — the `worktree` row sees that
              only while a worktree is on the head branch, and only as "not landed".
              A deliberate stack passes when the PR body has a line starting
              `cadence-stack:` (say why); the row then names what it waits on.
              Closed unmerged: na. No clone: na (the default is read from origin);
              no origin/<default> to read: unknown, never an assumed `main`.
    ledger    a TD-NNN named in the PR TITLE means the PR touches the ledger or the
              archive (status line, new entry, or the move to the archive). And a PR
              that edits the ledger — title or no title — gets `ledger.py --check
              --since` run on that edit (TD-073): the ledger at the PR's head against
              its merge-base with the default branch (merged: the merge commit against
              its parent). A bad field in an entry THIS PR changed or added fails the
              row with the flag's text; a ledger that was never clean does not. A
              `<repo>#TD-NNN` this machine cannot resolve is named, never a fail (it is
              a fact about the machine's roster). Commits not in this clone, or no
              `ledger.py` beside this script: not checked, and the row says so.
    worktree  a merged PR's local worktree is gone or reapable (clean including
              untracked, and landed by the §1 scoped diff); it is normal for it to
              still exist until the anchor's next pull, so existing is not the fault
              — dirty or unlanded is. Open or closed-unmerged PR: na. Not this
              machine's branch (no local `refs/heads/<branch>`): na — a worktree on
              another machine is out of this row's sight, never a pass.
    pushed    an open PR's local branch has no commits origin lacks (§3: unpushed
              work is invisible to every other machine)
    deploy    if the consumer ships an executable `scripts/cadence_deploy_check.sh`
              (adapt per repo, cadence §5) it is run as `<script> <pr> <sha>`; exit
              0 pass, 1 fail, 2 na, anything else unknown. Absent: na. The hook on
              disk is compared with origin/<default>'s (fetched): when origin has
              changed the hook since this checkout's merge-base with it and the disk
              copy is missing or differs, the row answers unknown, never na or a
              stale hook's verdict (TD-076). Otherwise the checkout runs its own — a
              branch that adds, edits or removes the hook is judged as it stands, behind
              or not. No origin/<default> to read: the disk copy, as before.

With --since, a second section audits the window's direct commits (TD-040, cadence §4
"The carve-outs' audit"): every first-parent commit on the default branch (cadence §9
"Default-branch rule") that no PR made — not a listed PR's merge commit, no `(#N)` subject
suffix, not the root — classified as

    stamp         only Swept:/Swept-deep: lines on docs/user_attention.md
    board-append  whole `- [ ]` items with a Due:, under a `## Needs…` heading, nothing removed
    board-edit    one item's tick (+ Closed:), Due:, Decided: or reply tails changed, under board_edit.py's fixed
                  message (BOARD_EDIT_MSG_RE — a parity pair with board_edit.py, cadence §7)
    fail          anything else: landed without review (the files it touched are named)

A fail there fails the run (exit 1); a window with direct commits and no PRs is not
empty. JSON: "direct": {"ref", "status", "detail", "commits": [{"sha", "subject",
"class", "status", "detail"}]}, or null with no clone to read. "acknowledged": the
numbers of the PRs whose every fail is an acknowledged one (present when there are any).

Exit: 0 every row pass or na; 1 any fail; 2 nothing could be checked (no gh, a gh
error, no PR, or an empty window); 3 no fail, but at least one row is `unknown` (CI
still running, check runs or comments unreadable, a `--since` window truncated) —
LOOK before merging, it is not a pass. `unknown` rows never turn a 0 into a 1, and
never leave it a 0 either: a caller that merges on exit 0 must not merge on no evidence.

The review comment (writer: the /cadence skill; reader: REVIEW_RE below — a parity
pair, cadence §7) has a machine-readable first line:

    cadence-review: SHIP | FIXED | BLOCK · <reviewer model> · <code|docs> · <n> findings

and the findings in prose after it. SHIP: nothing to change; FIXED: findings were
fixed before merge; BLOCK: do not merge. Posted with `gh pr comment` by the session,
never edited afterwards (an edit after the merge fails the row). A review done after
the merge is posted the same way, and says so: it acknowledges the fail, it never
clears it.

Out of scope, deliberately: cadence §3's board rule and §4's auto-merge threshold
are judgements git cannot settle; §2 numbering collisions surface as CI or merge
conflicts already.

Stdlib only. Never prompts. Works from the anchor checkout, a worktree, or a
directory with no clone at all (then base/worktree/pushed/deploy are na).
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
STACK_RE = re.compile(r"^\s*cadence-stack:(.*)$", re.IGNORECASE | re.MULTILINE)
TD_RE = re.compile(r"\bTD-0*(\d+)\b")
LEDGER_FILES = ("docs/technical_debt.md", "docs/technical_debt_archive.md")
DEPLOY_HOOK = os.path.join("scripts", "cadence_deploy_check.sh")


class GhError(Exception):
    pass


def run(cmd, cwd=None, env=None):
    try:
        p = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=60, check=False, env=env
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


# "Never prompts" (module docstring) includes the network: a fetch against an https
# remote with no cached credential would otherwise ask on the terminal, and an ssh one
# would ask for a passphrase — hang-proofed the way nudge_user_attention.py's fetch is.
GIT_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=5",
}


def git(args, cwd=None):
    try:
        return run(["git", *args], cwd=cwd, env=GIT_ENV)
    except GhError:
        return None


def repo_root():
    top = git(["rev-parse", "--show-toplevel"])
    return top.strip() if top else None


# --- rows -------------------------------------------------------------------------


def row(rule, status, detail):
    return {"rule": rule, "status": status, "detail": detail}


def row_pr(pr):
    if pr["state"] == "CLOSED":
        return row("pr", "na", f"PR #{pr['number']} was closed without merging")
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
        if not before and found[-1][2] == "BLOCK":
            # a late review that says BLOCK is something to act on, never "nothing to do"
            return row(
                "review",
                "fail",
                f"merged with no review, and the late one says BLOCK ({found[-1][3]})",
            )
        if not before:
            # Nothing a session does now can turn this row green: the merge went out
            # unreviewed. But the late review IS what an honest author does next, and a
            # caller counting "a second fail on the same PR" must be able to tell that
            # author from one who did nothing (TD-076, agentorc PR #128).
            out = row(
                "review",
                "fail",
                f"acknowledged — merged with no review; a late one is on record "
                f"({found[0][2]} · {found[0][3]}, {found[0][0].isoformat()} > merge). "
                "Nothing clears this row; nothing more to do",
            )
            out["acknowledged"] = True
            return out
        edited = [f for f in before if f[1] and f[1] > merged_at]
        if edited:
            return row(
                "review",
                "fail",
                f"review comment edited after the merge ({edited[0][1].isoformat()})",
            )
        seen = before
    else:
        seen = found
    # The FIRST comment is the review: a second `cadence-review:` line is usually the
    # author saying what they did about the findings, and naming that one made the row
    # read "reviewed by <the author's own model>" (TD-076, samscrape #830/#833).
    created, _, verdict, rest = seen[0]
    later = seen[1:]
    if seen[-1][2] == "BLOCK":
        # the one thing the latest comment decides: an older SHIP never outvotes a BLOCK
        return row("review", "fail", f"latest review says BLOCK ({seen[-1][3]})")
    when = (
        f"{(merged_at - created).total_seconds() / 60:.0f} min before merge"
        if merged_at
        else created.isoformat()
    )
    tail = ""
    if later:
        tail = f"; then {len(later)} later, not the review: " + "; ".join(
            f"{v} · {r}" for _, _, v, r in later
        )
    return row("review", "pass", f"{verdict} · {rest} · {when}{tail} (self-attested)")


# TD-079: how long after a PR was last updated "no check run listed" is too early to call
# "no CI". A workflow registers its run within seconds of a push; two minutes is generous,
# and short enough that a repo whose workflows never run on PRs waits only that long.
CI_REGISTER_GRACE = 120


def _ci_coming(pr, sha):
    """Why an open PR's empty check-run list is not yet "no CI", or None. Two pieces of
    evidence, each one gh call; a call that fails is no evidence, and the row stays na."""
    try:
        wr = [
            json.loads(line)
            for line in run(
                [
                    "gh",
                    "api",
                    f"repos/{{owner}}/{{repo}}/actions/runs?head_sha={sha}&per_page=20",
                    "--jq",
                    ".workflow_runs[] | {status, conclusion}",
                ]
            ).splitlines()
            if line.strip()
        ]
    except (GhError, ValueError):
        wr = []
    if wr:
        live = [w for w in wr if w.get("status") != "completed"]
        if live:
            return (
                f"{len(live)} workflow run(s) registered for {sha[:7]} with no check run "
                "listed yet — CI is starting; re-run in a moment"
            )
        ends = ", ".join(sorted({str(w.get("conclusion")) for w in wr}))
        return (
            f"{len(wr)} workflow run(s) for {sha[:7]} ended ({ends}) without a check run — "
            "look at the run: a workflow that fails to start reads this way"
        )
    updated = parse_ts(pr.get("updatedAt"))
    if updated is None:
        return None
    age = (dt.datetime.now(dt.timezone.utc) - updated).total_seconds()
    if age >= CI_REGISTER_GRACE:
        return None
    try:
        active = int(
            run(
                [
                    "gh",
                    "api",
                    "repos/{owner}/{repo}/actions/workflows",
                    "--jq",
                    '[.workflows[] | select(.state == "active")] | length',
                ]
            ).strip()
            or 0
        )
    except (GhError, ValueError):
        return None
    if not active:
        return None
    return (
        f"no check run on {sha[:7]} yet, but the PR changed {age:.0f}s ago and the repo has "
        f"{active} active workflow(s) — too early to call it no CI; re-run in "
        f"{CI_REGISTER_GRACE - age:.0f}s"
    )


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
        coming = _ci_coming(pr, sha) if pr["state"] == "OPEN" else None
        if coming:
            return row("ci", "unknown", coming)
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
    # A skipped or neutral check does not FAIL the row — a job a workflow deliberately
    # gates off must never block a merge — but it did not run either, and the old message
    # said "N check run(s) green" for a commit where N-2 of them never started. That reads
    # as "the suite passed in CI" when the suite was skipped, which is the one reading a
    # cadence check must not invite. Named, so "not red" stops being read as "ran".
    # `.get("name", "?")`: every other branch reaches a name only for a run it is already
    # reporting, so a check run without one used to be impossible to crash on. GitHub always
    # sends it; keeping that property costs nothing and losing it would be a regression
    # introduced by a message change.
    ran = [r.get("name", "?") for r in runs if r.get("conclusion") == "success"]
    skipped = [r.get("name", "?") for r in runs if r.get("conclusion") in ("skipped", "neutral")]
    if skipped and not ran:
        # NOTHING ran. Not a pass: an unattended session that self-merges on this row would
        # read a fully-skipped workflow — a misconfigured trigger, or every job behind a
        # condition that never fires — exactly as it reads a clean suite. `unknown` is what
        # this file already reserves for "no evidence", and no evidence is what this is.
        return row(
            "ci",
            "unknown",
            f"no check ran on {sha[:7]} — all {len(skipped)} were skipped "
            f"({', '.join(sorted(skipped))}); that is no evidence, not a pass",
        )
    if skipped:
        return row(
            "ci",
            "pass",
            f"{len(ran)} green, {len(skipped)} NOT RUN on {sha[:7]} "
            f"({', '.join(sorted(skipped))} — skipped, not passed)",
        )
    return row("ci", "pass", f"{len(runs)} check run(s) green on {sha[:7]}")


def row_base(pr, root):
    base = pr.get("baseRefName")
    if not base:
        return row("base", "unknown", "gh gave no base branch")
    if pr["state"] == "CLOSED":
        return row("base", "na", "closed unmerged")
    if not root:
        return row("base", "na", "no clone here — the default branch is read from origin")
    default = default_branch(root)
    if git(["rev-parse", "--verify", "-q", f"refs/remotes/origin/{default}"], cwd=root) is None:
        # default_branch() ends in a bare "main" when nothing resolves; a row that
        # compared against that guess would pass or fail on no evidence (TD-037).
        return row("base", "unknown", "cannot tell the default branch: no origin/<default> here")
    if base == default:
        return row("base", "pass", f"base is {default}")
    m = STACK_RE.search(pr.get("body") or "")
    if m:
        why = m.group(1).strip()
        return row(
            "base",
            "pass",
            f"stacked on {base} on purpose ({why or 'no reason given'}) — its work reaches "
            f"{default} only when {base} lands (§1)",
        )
    if pr["state"] == "MERGED":
        return row(
            "base",
            "fail",
            f"merged into {base}, not {default} — the work is not on {default} (§1). Recover with "
            f"one more PR {base} → {default}; a deliberate stack says so in a `cadence-stack:` body line",
        )
    return row(
        "base",
        "fail",
        f"base is {base}, not {default} (§1) — `gh pr edit {pr['number']} --base {default}`, "
        "or mark a deliberate stack with a `cadence-stack:` line in the PR body",
    )


def ledger_edit_check(pr, root):
    """TD-073: `ledger.py --check --since` on this PR's ledger edit. Returns (errors, note):
    errors is a list of flag texts that fail the row, note a clause for the row's detail
    ("" when there is nothing to say). Reads commits, never the working tree — the check
    may run from the anchor, from another worktree, or a week later in a --since sweep."""
    if not root or LEDGER_FILES[0] not in {f["path"] for f in pr.get("files") or []}:
        return [], ""
    if pr["state"] == "MERGED":
        new = (pr.get("mergeCommit") or {}).get("oid")
        old = f"{new}^" if new else None
    else:
        new = pr.get("headRefOid")
        old = None
    if not new:
        return [], "ledger fields not checked: gh gave no commit"
    if git(["cat-file", "-e", f"{new}^{{commit}}"], cwd=root) is None:
        fetch_once(root)
        if git(["cat-file", "-e", f"{new}^{{commit}}"], cwd=root) is None:
            return [], f"ledger fields not checked: {new[:7]} is not in this clone"
    if old is None:
        base = git(["merge-base", default_ref(root), new], cwd=root)
        if not base:
            return [], f"ledger fields not checked: no merge-base of {new[:7]} with {default_ref(root)}"
        old = base.strip()
    text = git(["show", f"{new}:{LEDGER_FILES[0]}"], cwd=root)
    if text is None:
        return [], ""  # the PR removes the ledger: nothing to check
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import ledger as L  # sibling SYNC script
    except ImportError:
        return [], "ledger fields not checked: no ledger.py beside this script"
    recs = L.check(
        text,
        git(["show", f"{new}:{LEDGER_FILES[1]}"], cwd=root) or "",
        git(["show", f"{old}:{LEDGER_FILES[0]}"], cwd=root) or "",
    )
    errors = [r["text"] for r in recs if r["level"] == "error" and r["kind"] != "cross-repo"]
    xrepo = [r["text"] for r in recs if r["level"] == "error" and r["kind"] == "cross-repo"]
    note = "ledger fields checked"
    if xrepo:
        note += f" ({len(xrepo)} cross-repo blocker(s) this machine cannot resolve: {xrepo[0]})"
    return errors, note


def row_ledger(pr, root=None):
    errors, note = ledger_edit_check(pr, root)
    if errors:
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        return row(
            "ledger",
            "fail",
            f"this PR's ledger edit has {len(errors)} bad field(s): {errors[0]}{more} — "
            "`ledger.py --check --since origin/<default>` lists them (cadence §2.12)",
        )
    note = f"; {note}" if note else ""
    tds = sorted({f"TD-{int(n):03d}" for n in TD_RE.findall(pr.get("title") or "")})
    if not tds:
        return row("ledger", "na", f"no TD named in the title{note}")
    paths = {f["path"] for f in pr.get("files") or []}
    touched = [p for p in LEDGER_FILES if p in paths]
    if touched:
        return row(
            "ledger", "pass", f"{', '.join(tds)} named; touches {', '.join(touched)}{note}"
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


_FETCHED = set()


def fetch_once(root):
    if root not in _FETCHED:  # once per run, not once per merged PR in a --since window
        _FETCHED.add(root)
        git(["fetch", "-q", "origin"], cwd=root)


def landed(root, wt, branch, fetch=True):
    """cadence §1 scoped diff: the branch's own files identical to origin/<default>.
    fetch=False reads origin/<default> as it stands (check_base.py at SessionStart,
    which must not touch the network — the attention hook fetched just before it)."""
    if fetch:
        fetch_once(root)
    ref = default_ref(root)
    base = git(["merge-base", ref, branch], cwd=root)
    if not base:
        return None
    files = git(["diff", "--name-only", "-z", base.strip(), branch], cwd=root)
    if files is None:
        return None
    names = [f for f in files.split("\0") if f]
    if not names:
        return False  # nothing committed is not landed
    p = subprocess.run(
        ["git", "diff", "--quiet", ref, branch, "--", *names],
        cwd=root,
        capture_output=True,
        check=False,
    )
    return p.returncode == 0


def _patch_id(root, args):
    """`git patch-id --stable` of `git <args>`'s diff, or None."""
    diff = git(args, cwd=root)
    if not diff:
        return None
    p = subprocess.run(["git", "patch-id", "--stable"], input=diff, cwd=root,
                       capture_output=True, text=True, check=False)
    out = p.stdout.split()
    return out[0] if p.returncode == 0 and out else None


def _main_patch_ids(root, base, ref, names):
    """The patch-ids, over `names`, of the first-parent commits in base..ref that could carry
    the branch's patch — or None when they cannot be had this way (a git without
    `--no-diff-merges`), and the caller asks commit by commit. TD-078: one `git show` and one
    `git patch-id` per commit on main was 46 of the reaper's 49 s in a repo with five old
    branches. Two cuts: only a commit that touches EVERY one of `names` is a candidate (a
    patch over fewer files is another patch; listing names costs no diff), and the
    candidates' diffs go through one `git log -p | git patch-id`. Merges are left out, as
    `git show <merge> -- <names>` gives them no patch-id either."""
    if any("\x01" in f or f != f.strip("\n") for f in names):
        return None  # a name the listing below cannot be split on: ask commit by commit
    listing = git(["log", "--first-parent", "--no-diff-merges", "--name-only", "-z", "--format=%x01%H",
                   f"{base}..{ref}", "--", *names], cwd=root)
    if listing is None:
        return None
    want, cands = set(names), []
    for chunk in listing.split("\x01")[1:]:
        sha, _, rest = chunk.partition("\n")
        if {f.strip("\n") for f in rest.split("\0")} - {""} == want:
            cands.append(sha.strip("\0\n"))
    ids = set()
    for i in range(0, len(cands), 200):  # bounded argv
        # an explicit format: under a user's format.pretty the message is not indented, and a
        # diff pasted into one would be patch-id'd as the commit's own
        log = git(["log", "--no-walk=unsorted", "--no-diff-merges", "--format=commit %H", "-p",
                   *cands[i:i + 200], "--", *names], cwd=root)
        if log is None:
            return None
        p = subprocess.run(["git", "patch-id", "--stable"], input=log, cwd=root,
                           capture_output=True, text=True, check=False)
        if p.returncode != 0:
            return None
        ids |= {line.split()[0] for line in p.stdout.splitlines() if line.split()}
    return ids


def landed_since_edited(root, branch, squash=None):
    """TD-059: the scoped test answers "is the branch's content on main NOW", so a branch
    whose squash later had its files edited again on main reads "not landed" forever. The
    second question — did it land on main, which has edited those files since?

    Returns "yes", "reverted" (it landed, and main's tip has undone it: those files are
    back to the merge-base — work someone decided against, or that still needs doing, never
    "done"), or False. A known squash decides alone: it must be on main (an ancestor of
    origin/<default>, not a commit merged elsewhere) and carry exactly the branch's files;
    when it differs, nothing looser is consulted. Unknown squash: offline, a first-parent
    commit on main since the merge-base whose diff over the branch's files has the branch's
    own patch-id. A coincidental identical change on main still matches — the accepted cost
    of answering offline."""
    ref = default_ref(root)
    base = git(["merge-base", ref, branch], cwd=root)
    if not base:
        return False
    base = base.strip()
    names = [f for f in (git(["diff", "--name-only", "-z", base, branch], cwd=root) or "").split("\0") if f]
    if not names:
        return False
    found = False
    if squash and git(["cat-file", "-e", f"{squash}^{{commit}}"], cwd=root) is not None:
        on_main = subprocess.run(["git", "merge-base", "--is-ancestor", squash, ref],
                                 cwd=root, capture_output=True, check=False).returncode == 0
        same = subprocess.run(["git", "diff", "--quiet", squash, branch, "--", *names],
                              cwd=root, capture_output=True, check=False).returncode == 0
        if not (on_main and same):
            return False
        found = True
    else:
        mine = _patch_id(root, ["diff", base, branch, "--", *names])
        if mine is None:
            return False
        ids = _main_patch_ids(root, base, ref, names)
        if ids is not None:
            found = mine in ids
        else:
            commits = git(["rev-list", "--first-parent", f"{base}..{ref}", "--", *names], cwd=root) or ""
            found = any(_patch_id(root, ["show", "--format=", c, "--", *names]) == mine
                        for c in commits.split())
    if not found:
        return False
    undone = subprocess.run(["git", "diff", "--quiet", base, ref, "--", *names],
                            cwd=root, capture_output=True, check=False).returncode == 0
    return "reverted" if undone else "yes"


def row_worktree(pr, root):
    if pr["state"] != "MERGED":
        why = "closed unmerged"
        if pr["state"] != "CLOSED":
            why = "open PR — the worktree is in use"
        return row("worktree", "na", why)
    if not root:
        return row("worktree", "na", "no clone here")
    branch = pr["headRefName"]
    wts = worktrees(root)
    if branch not in wts and (
        git(["rev-parse", "--verify", "-q", f"refs/heads/{branch}"], cwd=root) is None
    ):
        # Not this machine's branch: the PR was made elsewhere, and whatever worktree it
        # lives in there — clean or dirty — is out of sight. `pass` would give --since a
        # green row for work this check never saw.
        return row("worktree", "na", f"{branch} is not a local branch here")
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
    since = landed_since_edited(root, branch, (pr.get("mergeCommit") or {}).get("oid"))
    if since == "yes":
        return row(
            "worktree",
            "pass",
            f"{wt} is clean and landed, and main has edited its files since (TD-059) — "
            "reapable (the reaper asks the same question)",
        )
    if since == "reverted":
        return row(
            "worktree",
            "fail",
            f"{wt}: {branch} landed and main has since reverted it — decide whether the work is "
            "still wanted before removing the worktree",
        )
    return row(
        "worktree", "fail", f"{wt} has commits on {branch} that are not on {default_ref(root)}"
    )


def row_pushed(pr, root):
    if pr["state"] in ("MERGED", "CLOSED"):
        return row("pushed", "na", pr["state"].lower())
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


def _origin_hook(root, ref):
    """(known, blob sha or None) of an EXECUTABLE deploy hook at `ref`; known is False
    when `ref` cannot be read at all."""
    out = git(["ls-tree", ref, "--", DEPLOY_HOOK.replace(os.sep, "/")], cwd=root)
    if out is None:
        return False, None
    parts = out.split()
    if len(parts) >= 3 and parts[0] == "100755":
        return True, parts[2]
    return True, None


def row_deploy(pr, root):
    if not root:
        return row("deploy", "na", "no clone here")
    hook = os.path.join(root, DEPLOY_HOOK)
    here = os.path.isfile(hook) and os.access(hook, os.X_OK)
    # TD-076: the hook is the consumer's, and this row used to answer from whatever the
    # invoker's checkout held — a checkout behind its remote, where the hook had not
    # arrived yet, turned a `fail` into `na` (samscrape #791–#796). So ask origin too.
    fetch_once(root)
    ref = default_ref(root)
    known, theirs = _origin_hook(root, ref)
    if known:
        mine = (git(["hash-object", "--", hook], cwd=root) or "").strip() if here else None
        # Behind = origin changed the hook after this checkout's merge-base with it. A
        # branch that is merely behind, or that changed the hook itself while origin's
        # stood still, runs its own: its difference is the branch's, not staleness.
        base = (git(["merge-base", ref, "HEAD"], cwd=root) or "").strip()
        moved = not base or _origin_hook(root, base) != (True, theirs)
        if moved and mine != theirs:
            what = "has none" if not here else "has a different one"
            if not theirs:
                what = f"has one {ref} has since removed"
            return row(
                "deploy",
                "unknown",
                f"this checkout is behind {ref} and {what}: {DEPLOY_HOOK} — "
                f"pull, or run the check from a checkout at {ref}",
            )
    if not here:
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

# --- direct commits (TD-040) -------------------------------------------------------
# §4's carve-outs let three kinds of commit reach the default branch without a PR, each
# "audited in review" — but a direct push has no review. This is that audit: every
# first-parent commit in the --since window that no PR made is classified by what it
# touches, and anything outside the carve-outs is a fail ("landed without review").
BOARD = "docs/user_attention.md"
PR_SUBJECT_RE = re.compile(r"\(#\d+\)$|^Merge pull request #\d+ ")
# board_edit.py's fixed message (cadence §4, tool-made board edits) — a parity pair (§7)
BOARD_EDIT_MSG_RE = re.compile(r"^\S+: (?:snooze|done|decide|reply) .+ \(session [^)]+\)$")
STAMP_LINE_RE = re.compile(r"^(?:Swept|Swept-deep):\s")
OPEN_ITEM_RE = re.compile(r"^\s*-\s*\[ \]\s+\S")
ANY_ITEM_RE = re.compile(r"^(\s*-\s*)\[[ xX]\]")
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_PR_MERGE_SHAS = set()  # merge commits of the PRs the --since listing returned


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


def default_ref(root):
    return f"origin/{default_branch(root)}"


def _board_diff(root, sha):
    """(removed lines, [(new line number, added line)]) of the board in `sha`, or None."""
    out = git(["diff", "-U0", "--no-color", f"{sha}^", sha, "--", BOARD], cwd=root)
    if out is None:
        return None
    removed, added, nxt = [], [], 0
    for line in out.splitlines():
        m = HUNK_RE.match(line)
        if m:
            nxt = int(m.group(1))
        elif line.startswith(("---", "+++")):
            continue
        elif line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append((nxt, line[1:]))
            nxt += 1
    return removed, added


def _edit_key(line):
    """An item line with what a tool-made edit may change taken out: the tick, the
    Closed:, Due: and Decided: fields and the reply tails — the board reader's own item_key (cadence §7),
    imported, never re-typed."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import nudge_user_attention as N  # sibling SYNC script, as board_edit.py imports it

    return N.item_key(line)


def classify_direct(root, sha, subject, files):
    """(class, detail) for one direct commit; class is stamp | board-append | board-edit | fail."""
    if files != [BOARD]:
        return "fail", "touches " + (", ".join(files[:6]) + (" …" if len(files) > 6 else "") or "nothing")
    diff = _board_diff(root, sha)
    if diff is None:
        return "fail", "board diff unreadable"
    removed, added = diff
    new = [a for _, a in added]
    changed = [x for x in removed + new if x.strip()]  # a stamp's first write brings a blank line
    if changed and all(STAMP_LINE_RE.match(x) for x in changed):
        return "stamp", "only the Swept:/Swept-deep: stamp lines"
    items = [(n, a) for n, a in added if a.strip()]  # blank lines may come with an append
    if not removed and items and all(OPEN_ITEM_RE.match(a) and "Due:" in a for _, a in items):
        post = (git(["show", f"{sha}:{BOARD}"], cwd=root) or "").splitlines()
        heads = []
        for n, _ in items:
            above = [x for x in post[: n - 1] if x.startswith("## ")]
            heads.append(above[-1] if above else "")
        if all(h.startswith("## Needs") for h in heads):
            return "board-append", f"{len(items)} whole item line(s) appended under {heads[0]}"
        return "fail", "appended items outside the ## Needs section"
    if len(removed) == 1 and len(new) == 1 and OPEN_ITEM_RE.match(removed[0]) \
            and ANY_ITEM_RE.match(new[0]) and _edit_key(removed[0]) == _edit_key(new[0]):
        if BOARD_EDIT_MSG_RE.match(subject):
            return "board-edit", "one item's Due:/tick/Closed:/Decided:/reply, under board_edit.py's message"
        return "fail", "a one-item board edit without board_edit.py's fixed message"
    return "fail", f"board change outside the carve-outs (-{len(removed)} +{len(new)} lines)"


def direct_commits(root, when):
    """Every first-parent commit on the default branch since `when` that no PR made,
    classified. None when there is no clone to read (the section is then n/a)."""
    if not root:
        return None
    fetch_once(root)
    ref = default_ref(root)
    # --since-as-filter (git 2.34+), not --since: --since stops walking at the first commit
    # older than the cutoff, so one backdated commit would hide every in-window commit
    # behind it — silently, in the one check that exists to find what slipped past.
    log = git(["log", "--first-parent", f"--since-as-filter={when}", "--format=%H%x00%P%x00%s", ref],
              cwd=root)
    if log is None:
        return {"ref": ref, "status": "unknown", "commits": [],
                "detail": f"git log {ref} failed (no such ref, or git older than 2.34)"}
    commits = []
    for line in log.splitlines():
        sha, parents, subject = (line.split("\0") + ["", ""])[:3]
        if not parents or PR_SUBJECT_RE.search(subject) or sha in _PR_MERGE_SHAS:
            continue  # a root commit, or a PR's merge
        if len(parents.split()) > 1:
            cls, detail = "fail", "a merge commit no PR made"
        else:
            names = git(["diff-tree", "--no-commit-id", "--name-only", "-r", "-z", sha], cwd=root)
            if names is None:
                cls, detail = "fail", "files unreadable"
            else:
                cls, detail = classify_direct(root, sha, subject, [f for f in names.split("\0") if f])
        commits.append({"sha": sha[:7], "subject": subject, "class": cls,
                        "status": "fail" if cls == "fail" else "pass", "detail": detail})
    status = "fail" if any(c["status"] == "fail" for c in commits) else "pass"
    return {"ref": ref, "status": status, "detail": f"{len(commits)} direct commit(s)", "commits": commits}


PR_FIELDS = (
    "number,url,state,title,body,baseRefName,headRefName,headRefOid,mergedAt,mergeCommit,files,updatedAt"
)


def check_pr(number, root):
    pr = gh_json(["pr", "view", str(number), "--json", PR_FIELDS])
    rows = []
    for rule, fn in (
        ("pr", lambda: row_pr(pr)),
        ("review", lambda: row_review(pr)),
        ("ci", lambda: row_ci(pr)),
        ("base", lambda: row_base(pr, root)),
        ("ledger", lambda: row_ledger(pr, root)),
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
    # Any unknown row makes the PR unknown: one passing row (and `pr` nearly always
    # passes) must not carry a CI that is still running to a green verdict (TD-041).
    st = {r["status"] for r in rows}
    if "fail" in st:
        return "fail"
    if "unknown" in st:
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


# gh pr list --limit for --since; a list that comes back full is reported as truncated
SINCE_LIMIT = 200


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
            str(SINCE_LIMIT),
            "--json",
            "number,mergeCommit",
        ]
    )
    _PR_MERGE_SHAS.update((p.get("mergeCommit") or {}).get("oid", "") for p in prs)
    return [p["number"] for p in prs]


def print_text(results):
    for res in results:
        print(
            f"PR #{res['pr']} {res['state'].lower()} — {res['title']}  [{res['verdict'].upper()}]"
        )
        for r in res["rows"]:
            print(f"  {r['status'].upper():7} {r['rule']:9} {r['detail']}")


def acknowledged(results):
    """The PRs whose every fail is an acknowledged one (TD-076): still a fail — the verdict
    and the exit code do not move — but nothing a session can act on, so a sweep reports
    them apart and a caller that acts on a repeated fail leaves them alone."""
    out = []
    for res in results:
        fails = [r for r in res["rows"] if r["status"] == "fail"]
        if fails and all(r.get("acknowledged") for r in fails):
            out.append(res["pr"])
    return out


def print_direct(direct):
    if direct is None:
        print("direct commits: n/a — no clone here to read")
        return
    print(f"Direct commits on {direct['ref']} (no PR) — {direct['detail']}  [{direct['status'].upper()}]")
    for c in direct["commits"]:
        print(f"  {c['status'].upper():7} {c['class']:12} {c['sha']} {c['subject'][:70]} — {c['detail']}")


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
    # A window that filled the list may have lost PRs past the limit: say so, and never
    # let the verdict read as a clean week (it was not all checked).
    truncated = bool(args.since) and len(numbers) >= SINCE_LIMIT
    # TD-040: the window's commits that no PR made — the carve-outs' audit
    direct = direct_commits(root, since_date(args.since)) if args.since else None
    dstat = direct["status"] if direct else None
    overall = "pass"
    if any(r["verdict"] == "fail" for r in results) or dstat == "fail":
        overall = "fail"
    elif dstat == "unknown":  # before "none": an unreadable history is not an empty window
        overall = "unknown"
    elif not results and not (direct and direct["commits"]):
        overall = "none"
    elif truncated or dstat == "unknown" or any(r["verdict"] == "unknown" for r in results):
        overall = "unknown"
    acked = acknowledged(results)
    if args.json:
        out = {"prs": results, "verdict": overall}
        if args.since:
            out["direct"] = direct
        if truncated:
            out["truncated"] = SINCE_LIMIT
        if acked:
            out["acknowledged"] = acked
        print(json.dumps(out, indent=1))
    else:
        print_text(results)
        if not results:
            print("no PRs in the window")
        if args.since:
            print_direct(direct)
        if acked:
            nfail = sum(r["verdict"] == "fail" for r in results)
            print(
                f"ACKNOWLEDGED: {len(acked)} of {nfail} failing PR(s) fail only on a late review "
                f"already on record ({', '.join(f'#{n}' for n in acked)}) — nothing to act on there"
            )
        if truncated:
            print(
                f"TRUNCATED: the window returned {SINCE_LIMIT} PRs, the list limit — "
                "older merges in it were not checked; narrow --since"
            )
    return {"pass": 0, "fail": 1, "none": 2, "unknown": 3}[overall]


if __name__ == "__main__":
    sys.exit(main())
