"""Derived report entries (design §4.8, TD-028 step 3): branch → claimed, merged PR → done, the
ledger rows a merged PR put on main → findings. `gh` is stubbed (a test never reaches the network);
git is real, because the squash-merge commit and its ledger diff are the whole point."""

import subprocess

import pytest

from sessionorc import reports
from sessionorc.models import Session

pytestmark = pytest.mark.unit

LEDGER = "docs/technical_debt.md"


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    """A clone with an `origin` whose default branch is `main`, so `origin/main` is a real ref."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    git(work, "config", "user.email", "t@example.com")
    git(work, "config", "user.name", "t")
    (work / "docs").mkdir()
    (work / LEDGER).write_text("| ID | Title |\n|---|---|\n| TD-001 | old |\n\n## TD-001: old\n")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "ledger")
    git(work, "push", "-q", "origin", "main")
    git(work, "remote", "set-head", "origin", "-a")
    return work


def stub_prs(monkeypatch, prs, *, head=...):
    """`_prs` answers the repo-wide query; `_prs_for_head` answers the by-branch one TD-045's
    retirement asks, where `None` means "`gh` could not be asked" and `[]` means "no PR, ever"."""
    monkeypatch.setattr(reports, "_prs", lambda directory, **kw: prs)
    found = prs if head is ... else head
    monkeypatch.setattr(
        reports,
        "_prs_for_head",
        lambda directory, branch, **kw: None if found is None else [p for p in found if p.get("headRefName") == branch],
    )


def test_branch_ref_reads_only_the_work_branch_shape():
    assert reports.branch_ref("td028-report-channels") == "TD-028"
    assert reports.branch_ref("TD-28") == reports.branch_ref("td-028_x") == "TD-028"
    for other in ("main", "tdgrind-ao-1", "release-2", "ui-3-fix", "", None, "todo-4"):
        assert reports.branch_ref(other) is None


def test_nothing_is_derived_without_a_work_branch(repo, monkeypatch):
    stub_prs(monkeypatch, [{"number": 77, "state": "MERGED", "mergedAt": "now", "headRefName": "td077-x"}])
    assert reports.derive(repo, "main") == ([], [], [])
    assert reports.derive(repo, None) == ([], [], [])


def test_branch_claims_its_reference_and_a_merged_pr_finishes_it(repo, monkeypatch):
    git(repo, "checkout", "-qb", "td077-cap")
    # no PR yet: the branch alone is a claim, with no number to show
    stub_prs(monkeypatch, [])
    progress, findings, _ = reports.derive(repo, "td077-cap")
    assert [(p.ref, p.status, p.pr, p.source) for p in progress] == [("TD-077", "claimed", None, "derived")]
    assert findings == []
    # an open PR: still claimed, now with the number the card links to
    stub_prs(monkeypatch, [{"number": 77, "state": "OPEN", "mergedAt": None, "headRefName": "td077-cap"}])
    progress, findings, _ = reports.derive(repo, "td077-cap")
    assert [(p.ref, p.status, p.pr) for p in progress] == [("TD-077", "claimed", 77)]
    assert findings == []
    # merged, but this clone has not fetched the squash commit yet: done, and no findings guessed at
    stub_prs(monkeypatch, [{"number": 77, "state": "MERGED", "mergedAt": "2026-09-11", "headRefName": "td077-cap"}])
    progress, findings, _ = reports.derive(repo, "td077-cap")
    assert [(p.ref, p.status, p.pr) for p in progress] == [("TD-077", "done", 77)]
    assert findings == []


def test_ledger_rows_a_merged_pr_put_on_main_become_findings(repo, monkeypatch):
    """The squash-merge commit is found by its `(#N)` subject — the one link that survives GitHub
    deleting the merged head and the session checking out its next branch."""
    ledger = repo / LEDGER
    ledger.write_text(
        ledger.read_text() + "| TD-077 | capped |\n| TD-078 | found on the way |\n\n"
        "## TD-077: capped\n\n#### TD-078: found on the way\n"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "TD-077: cap the thing, and file TD-078 (#77)")  # a squash merge's shape
    git(repo, "push", "-q", "origin", "main")
    git(repo, "checkout", "-qb", "td077-cap")  # the branch is still around, PR merged
    stub_prs(monkeypatch, [{"number": 77, "state": "MERGED", "mergedAt": "2026-09-11", "headRefName": "td077-cap"}])
    progress, findings, _ = reports.derive(repo, "td077-cap")
    assert [(p.ref, p.status, p.pr) for p in progress] == [("TD-077", "done", 77)]
    # TD-077 is the reference in hand, not a finding; TD-001 was already on main before the commit
    assert [(f.ref, f.source) for f in findings] == [("TD-078", "derived")]
    # a merge this session has moved on from: `pending` carries the PR, so the merge still lands
    git(repo, "checkout", "-q", "main")
    progress, findings, _ = reports.derive(repo, "main", pending=[("TD-077", 77)])
    assert [(p.ref, p.status, p.pr) for p in progress] == [("TD-077", "done", 77)]
    assert [f.ref for f in findings] == ["TD-078"]
    # a PR number `gh` does not answer for derives nothing at all
    stub_prs(monkeypatch, [])
    assert reports.derive(repo, "main", pending=[("TD-077", 77)]) == ([], [], [])


def test_a_broken_repo_derives_what_it_can_and_a_broken_toolchain_derives_nothing(tmp_path, monkeypatch):
    """`gh`, git, the network: any of them missing yields fewer entries, never an exception — this
    fills in what a session forgot and may not gate anything (design §4.8)."""
    stub_prs(monkeypatch, [{"number": 1, "state": "MERGED", "mergedAt": "x", "headRefName": "td001-a"}])
    progress, findings, _ = reports.derive(tmp_path, "td001-a")  # not a git repo at all
    assert [(p.ref, p.status, p.pr) for p in progress] == [("TD-001", "done", 1)] and findings == []
    monkeypatch.undo()
    monkeypatch.setattr(reports.subprocess, "run", _boom)  # no `gh` and no `git` on PATH
    progress, findings, retire = reports.derive(tmp_path, "td001-a")
    # the branch name is the one signal that needs no subprocess at all, so the claim survives
    assert [(p.ref, p.status, p.pr) for p in progress] == [("TD-001", "claimed", None)] and findings == []
    # and with no toolchain at all, a claim the session left is kept, never retired (TD-045)
    assert reports.derive(tmp_path, "main", left=[("TD-001", "td001-a")]) == ([], [], [])
    assert retire == []
    assert reports._prs_for_head(tmp_path, "td001-a") is None  # the real function, through the real failure


def _boom(*a, **kw):
    raise OSError("nothing on PATH")


def test_only_the_record_holding_a_directory_is_credited_with_what_is_checked_out(tmp_path):
    """TD-034: a worktree is reused run after run, so occupancy in time — not the `dir` string —
    says whose branch is checked out there. The live record holds it; when none is live the most
    recently created one does, so a finished run still reads as the last occupant."""

    def rec(sid, directory, state, created):
        return Session(
            id=sid, name=sid, kind="agent", adapter="shell", dir=str(directory), state=state, created=created
        )

    other = tmp_path / "other"
    old = rec("ao-1", tmp_path, "exited", "2026-09-10T00:00:00Z")
    new = rec("ao-2", tmp_path, "idle", "2026-09-11T00:00:00Z")
    alone = rec("ao-3", other, "exited", "2026-09-09T00:00:00Z")
    assert reports.holds_directory([old, new, alone]) == {"ao-2", "ao-3"}
    # a trailing slash is the same directory, and `closed` is not live either
    assert reports.holds_directory([rec("ao-4", f"{tmp_path}/", "closed", "2026-09-12T00:00:00Z"), old, new]) == {
        "ao-2"
    }
    # none live: the most recently created record is the last occupant
    newer_exit = rec("ao-5", tmp_path, "exited", "2026-09-12T00:00:00Z")
    assert reports.holds_directory([old, newer_exit]) == {"ao-5"}
    # nothing to attribute when a record has no directory at all
    assert reports.holds_directory([rec("ao-6", "", "idle", "2026-09-12T00:00:00Z")]) == set()


def test_a_branch_only_claim_the_session_left_is_re_checked_and_then_retired(repo, monkeypatch):
    """TD-045: a `tdNNN-*` branch created and abandoned before its PR existed left a `claimed` entry
    nothing could ever remove — the by-number re-check needs a number, and invariant 10 only lets a
    *declaration* replace a derived entry. `left` is the last look: a PR from that branch makes the
    claim real, no PR retires it."""
    git(repo, "checkout", "-qb", "td077-cap")
    stub_prs(monkeypatch, [])
    progress, _, retire = reports.derive(repo, "td077-cap")
    assert progress[0].branch == "td077-cap" and retire == []  # on the branch: nothing to retire
    # the session moved on and the branch never grew a PR: the reference is retired, not derived again
    git(repo, "checkout", "-q", "main")
    progress, findings, retire = reports.derive(repo, "main", left=[("TD-077", "td077-cap")])
    assert (progress, findings, retire) == ([], [], ["TD-077"])
    # the same branch *did* grow a PR while the session was elsewhere: the claim is kept, with its
    # number, so the five-minute window between "branch seen" and "PR opened" costs nothing
    stub_prs(monkeypatch, [{"number": 77, "state": "OPEN", "mergedAt": None, "headRefName": "td077-cap"}])
    progress, _, retire = reports.derive(repo, "main", left=[("TD-077", "td077-cap")])
    assert [(p.ref, p.status, p.pr, p.branch) for p in progress] == [("TD-077", "claimed", 77, "td077-cap")]
    assert retire == []
    # and a merge lands the same way
    stub_prs(monkeypatch, [{"number": 77, "state": "MERGED", "mergedAt": "2026-09-13", "headRefName": "td077-cap"}])
    progress, _, retire = reports.derive(repo, "main", left=[("TD-077", "td077-cap")])
    assert [(p.ref, p.status, p.pr) for p in progress] == [("TD-077", "done", 77)] and retire == []
    # an entry written before the branch was recorded has nothing to re-check: it is the immortal one
    assert reports.derive(repo, "main", left=[("TD-077", None)]) == ([], [], ["TD-077"])


def test_an_unreachable_gh_retires_nothing(repo, monkeypatch):
    """The PR #126 review's finding: `_prs` answers every failure — no `gh`, no auth, no network, a
    timeout — with an empty list, which is right for a source that only fills things in and fatal
    for one that deletes. An outage would have retired every branch-only claim on every session at
    once. Retirement asks `_prs_for_head`, which says *could not ask* and is obeyed."""
    git(repo, "checkout", "-q", "main")
    stub_prs(monkeypatch, [], head=None)  # gh unreachable
    assert reports.derive(repo, "main", left=[("TD-077", "td077-cap")]) == ([], [], [])
    # a PR older than the page `_prs` fetches is found by the by-branch query, so it is not retired
    stub_prs(monkeypatch, [], head=[{"number": 77, "state": "OPEN", "mergedAt": None, "headRefName": "td077-cap"}])
    progress, _, retire = reports.derive(repo, "main", left=[("TD-077", "td077-cap")])
    assert [(p.ref, p.pr) for p in progress] == [("TD-077", 77)] and retire == []
    # and a genuine "no PR from that branch" still retires
    stub_prs(monkeypatch, [], head=[])
    assert reports.derive(repo, "main", left=[("TD-077", "td077-cap")]) == ([], [], ["TD-077"])


def test_a_declared_claims_review_pr_is_rechecked_by_number_and_a_closed_pr_says_so(repo, monkeypatch):
    """TD-150: `reviews` — a declared claim's `review_pr` after the session left its branch — is
    looked up by number: merged is `done`, closed without a merge is a claim marked `PR_CLOSED`
    (both clear the claim's `review_pr`), open is nothing; the checked-out branch's closed PR is
    marked the same way."""
    from sessionorc.models import PR_CLOSED

    stub_prs(
        monkeypatch,
        [
            {"number": 50, "state": "CLOSED", "mergedAt": None, "headRefName": "td050-a"},
            {"number": 51, "state": "MERGED", "mergedAt": "now", "headRefName": "td051-b"},
            {"number": 52, "state": "OPEN", "mergedAt": None, "headRefName": "td052-c"},
        ],
    )
    progress, _, _ = reports.derive(repo, "main", reviews=[("TD-050", 50), ("TD-051", 51), ("TD-052", 52)])
    assert [(p.ref, p.status, p.pr, p.why) for p in progress] == [
        ("TD-050", "claimed", 50, PR_CLOSED),
        ("TD-051", "done", 51, None),
    ]
    git(repo, "checkout", "-qb", "td050-a")
    (e,) = reports.derive(repo, "td050-a")[0]
    assert (e.status, e.pr, e.why) == ("claimed", 50, PR_CLOSED)
