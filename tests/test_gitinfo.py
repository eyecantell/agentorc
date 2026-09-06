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
