"""Derived report entries (design §4.8, TD-028 step 3): branch → claimed, merged PR → done, the
ledger rows a merged PR put on main → findings. `gh` is stubbed (a test never reaches the network);
git is real, because the squash-merge commit and its ledger diff are the whole point."""

import subprocess

import pytest

from sessionorc import reports

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


def stub_prs(monkeypatch, prs):
    monkeypatch.setattr(reports, "_prs", lambda directory, **kw: prs)


def test_branch_ref_reads_only_the_work_branch_shape():
    assert reports.branch_ref("td028-report-channels") == "TD-028"
    assert reports.branch_ref("TD-28") == reports.branch_ref("td-028_x") == "TD-028"
    for other in ("main", "tdgrind-ao-1", "release-2", "ui-3-fix", "", None, "todo-4"):
        assert reports.branch_ref(other) is None


def test_nothing_is_derived_without_a_work_branch(repo, monkeypatch):
    stub_prs(monkeypatch, [{"number": 77, "state": "MERGED", "mergedAt": "now", "headRefName": "td077-x"}])
    assert reports.derive(repo, "main") == ([], [])
    assert reports.derive(repo, None) == ([], [])


def test_branch_claims_its_reference_and_a_merged_pr_finishes_it(repo, monkeypatch):
    git(repo, "checkout", "-qb", "td077-cap")
    # no PR yet: the branch alone is a claim, with no number to show
    stub_prs(monkeypatch, [])
    progress, findings = reports.derive(repo, "td077-cap")
    assert [(p.ref, p.status, p.pr, p.source) for p in progress] == [("TD-077", "claimed", None, "derived")]
    assert findings == []
    # an open PR: still claimed, now with the number the card links to
    stub_prs(monkeypatch, [{"number": 77, "state": "OPEN", "mergedAt": None, "headRefName": "td077-cap"}])
    progress, findings = reports.derive(repo, "td077-cap")
    assert [(p.ref, p.status, p.pr) for p in progress] == [("TD-077", "claimed", 77)]
    assert findings == []
    # merged, but this clone has not fetched the squash commit yet: done, and no findings guessed at
    stub_prs(monkeypatch, [{"number": 77, "state": "MERGED", "mergedAt": "2026-09-11", "headRefName": "td077-cap"}])
    progress, findings = reports.derive(repo, "td077-cap")
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
    progress, findings = reports.derive(repo, "td077-cap")
    assert [(p.ref, p.status, p.pr) for p in progress] == [("TD-077", "done", 77)]
    # TD-077 is the reference in hand, not a finding; TD-001 was already on main before the commit
    assert [(f.ref, f.source) for f in findings] == [("TD-078", "derived")]
    # a merge this session has moved on from: `pending` carries the PR, so the merge still lands
    git(repo, "checkout", "-q", "main")
    progress, findings = reports.derive(repo, "main", pending=[("TD-077", 77)])
    assert [(p.ref, p.status, p.pr) for p in progress] == [("TD-077", "done", 77)]
    assert [f.ref for f in findings] == ["TD-078"]
    # a PR number `gh` does not answer for derives nothing at all
    stub_prs(monkeypatch, [])
    assert reports.derive(repo, "main", pending=[("TD-077", 77)]) == ([], [])


def test_a_broken_repo_derives_what_it_can_and_a_broken_toolchain_derives_nothing(tmp_path, monkeypatch):
    """`gh`, git, the network: any of them missing yields fewer entries, never an exception — this
    fills in what a session forgot and may not gate anything (design §4.8)."""
    stub_prs(monkeypatch, [{"number": 1, "state": "MERGED", "mergedAt": "x", "headRefName": "td001-a"}])
    progress, findings = reports.derive(tmp_path, "td001-a")  # not a git repo at all
    assert [(p.ref, p.status, p.pr) for p in progress] == [("TD-001", "done", 1)] and findings == []
    monkeypatch.undo()
    monkeypatch.setattr(reports.subprocess, "run", _boom)  # no `gh` and no `git` on PATH
    progress, findings = reports.derive(tmp_path, "td001-a")
    # the branch name is the one signal that needs no subprocess at all, so the claim survives
    assert [(p.ref, p.status, p.pr) for p in progress] == [("TD-001", "claimed", None)] and findings == []


def _boom(*a, **kw):
    raise OSError("nothing on PATH")
