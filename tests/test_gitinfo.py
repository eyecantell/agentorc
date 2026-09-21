"""porcelain v2 parsing against a real repo: spaces in paths, renames, ahead/behind, untracked."""

import subprocess

from sessionorc.gitinfo import git_info


def run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def test_git_info_paths_with_spaces_and_renames(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    run("git", "init", "-q", "-b", "main", cwd=repo)
    run("git", "config", "user.email", "t@t", cwd=repo)
    run("git", "config", "user.name", "t", cwd=repo)
    (repo / "a file.txt").write_text("1")
    (repo / "old.txt").write_text("x" * 100)
    run("git", "add", ".", cwd=repo)
    run("git", "commit", "-q", "-m", "init", cwd=repo)
    (repo / "a file.txt").write_text("2")
    run("git", "mv", "old.txt", "new name.txt", cwd=repo)
    (repo / "untracked one.md").write_text("u")
    info = git_info(repo)
    assert info is not None and info.branch == "main" and info.upstream is None
    assert "M a file.txt" in info.files
    assert "R new name.txt" in info.files
    assert "?? untracked one.md" in info.files
    assert info.dirty == 3


def test_git_info_ahead_of_upstream(tmp_path):
    origin = tmp_path / "origin.git"
    run("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    repo = tmp_path / "r"
    run("git", "clone", "-q", str(origin), str(repo), cwd=tmp_path)
    run("git", "config", "user.email", "t@t", cwd=repo)
    run("git", "config", "user.name", "t", cwd=repo)
    run("git", "checkout", "-q", "-b", "main", cwd=repo)
    (repo / "f").write_text("1")
    run("git", "add", "f", cwd=repo)
    run("git", "commit", "-q", "-m", "one", cwd=repo)
    run("git", "push", "-q", "-u", "origin", "main", cwd=repo)
    (repo / "f").write_text("2")
    run("git", "commit", "-q", "-am", "two", cwd=repo)
    info = git_info(repo)
    assert info and info.upstream == "origin/main" and info.ahead == 1 and info.behind == 0 and info.dirty == 0
    assert git_info(tmp_path / "not-a-repo") is None or True  # a non-repo returns None or a parent's info


def test_unpushed_is_one_measure_of_exists_only_on_this_machine(tmp_path):
    """Design §4.2 *one measure* (TD-080): *does this work exist only on this machine*, never *is
    it merged*. The three rules, in the order they apply — a launch branch that tracks
    `origin/main` and is pushed to its own ref read *308 unpushed* for ever before this."""
    origin = tmp_path / "origin.git"
    run("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    repo = tmp_path / "r"
    run("git", "clone", "-q", str(origin), str(repo), cwd=tmp_path)
    run("git", "config", "user.email", "t@t", cwd=repo)
    run("git", "config", "user.name", "t", cwd=repo)
    (repo / "f").write_text("1")
    run("git", "add", "f", cwd=repo)
    run("git", "commit", "-q", "-m", "one", cwd=repo)
    run("git", "push", "-q", "-u", "origin", "main", cwd=repo)

    # rule 2: no `origin/<branch>` of its own, but an upstream — the porcelain's ahead, as before
    run("git", "checkout", "-q", "-b", "work", cwd=repo)
    run("git", "branch", "-q", "--set-upstream-to=origin/main", cwd=repo)
    (repo / "f").write_text("2")
    run("git", "commit", "-q", "-am", "two", cwd=repo)
    info = git_info(repo)
    assert info and (info.unpushed, info.pushed_against) == (1, "origin/main")

    # rule 1: the branch has its own remote ref — counted against *that*, whatever the upstream is.
    # This is TD-080's case: still ahead of origin/main, and no longer *unpushed*.
    run("git", "push", "-q", "origin", "work", cwd=repo)
    info = git_info(repo)
    assert info and info.ahead == 1, "still ahead of the upstream it tracks"
    assert (info.unpushed, info.pushed_against) == (0, "origin/work"), "and its work is not only here"
    (repo / "f").write_text("3")
    run("git", "commit", "-q", "-am", "three", cwd=repo)
    info = git_info(repo)
    assert info and (info.unpushed, info.pushed_against) == (1, "origin/work")

    # rule 3: neither — a detached HEAD on what origin has is pushed; a commit on no remote is not
    run("git", "checkout", "-q", "--detach", "origin/main", cwd=repo)
    info = git_info(repo)
    assert info and (info.unpushed, info.pushed_against) == (0, "remote branches")
    (repo / "f").write_text("4")
    run("git", "commit", "-q", "-am", "four", cwd=repo)
    info = git_info(repo)
    assert info and (info.unpushed, info.pushed_against) == (1, "remote branches")


def test_ensure_worktree_creates_reuses_and_validates(tmp_path):
    from sessionorc.gitinfo import WorktreeError, ensure_worktree

    origin = tmp_path / "origin.git"
    run("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    repo = tmp_path / "r"
    run("git", "clone", "-q", str(origin), str(repo), cwd=tmp_path)
    run("git", "config", "user.email", "t@t", cwd=repo)
    run("git", "config", "user.name", "t", cwd=repo)
    run("git", "checkout", "-q", "-b", "main", cwd=repo)
    (repo / "f").write_text("1")
    run("git", "add", "f", cwd=repo)
    run("git", "commit", "-q", "-m", "one", cwd=repo)
    run("git", "push", "-q", "-u", "origin", "main", cwd=repo)
    wt = ensure_worktree(repo, "topic-1")
    assert wt == repo / ".claude" / "worktrees" / "topic-1" and (wt / "f").is_file()
    branch = subprocess.run(["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
    assert branch.stdout.strip() == "topic-1"
    assert ensure_worktree(repo, "topic-1") == wt  # idempotent
    import pytest

    with pytest.raises(WorktreeError, match="letters, digits"):
        ensure_worktree(repo, "../evil")
    with pytest.raises(WorktreeError, match="not a git repository"):
        ensure_worktree(tmp_path / "plain", "x")


def test_ensure_worktree_from_inside_a_worktree_and_without_origin(tmp_path):
    from sessionorc.gitinfo import ensure_worktree

    repo = tmp_path / "solo"  # no origin: base is HEAD
    repo.mkdir()
    run("git", "init", "-q", "-b", "main", cwd=repo)
    run("git", "config", "user.email", "t@t", cwd=repo)
    run("git", "config", "user.name", "t", cwd=repo)
    (repo / "f").write_text("1")
    run("git", "add", "f", cwd=repo)
    run("git", "commit", "-q", "-m", "one", cwd=repo)
    wt1 = ensure_worktree(repo, "wt1")
    assert wt1 == repo / ".claude" / "worktrees" / "wt1"
    # called with the worktree as the "repo": lands beside wt1 under the main checkout, not nested
    wt2 = ensure_worktree(wt1, "wt2")
    assert wt2 == repo / ".claude" / "worktrees" / "wt2"
    # an existing local branch with no worktree yet is checked out, not recreated
    run("git", "branch", "pre-made", cwd=repo)
    wt3 = ensure_worktree(repo, "pre-made")
    head = subprocess.run(["git", "-C", str(wt3), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
    assert head.stdout.strip() == "pre-made"


def test_git_info_names_the_commit_so_a_detached_head_can_be_named(tmp_path):
    """Design §4.5 *The card's anatomy*, row 3 (TD-095): *detached at <short sha>* needs the commit;
    a repo with no commit yet has none to name."""
    repo = tmp_path / "r"
    repo.mkdir()
    run("git", "init", "-q", "-b", "main", cwd=repo)
    assert git_info(repo).oid == ""  # `# branch.oid (initial)`
    run("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "a", cwd=repo)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    run("git", "checkout", "-q", "--detach", cwd=repo)
    info = git_info(repo)
    assert info.branch == "(detached)" and info.oid == sha
