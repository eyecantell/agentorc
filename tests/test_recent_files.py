"""A record's recent files (design §4.2 *The record's `files`*, TD-538): what the session's work changed,
read from git with the status — the porcelain's paths and the branch's commits since `merge-base HEAD
origin/<default>` — `{path, at, sha}` newest first, a path once, the newest `RECENT_FILES`; computed on each
read, never kept, and nothing a hook reports."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from agentorc.adapters.claude_code.hook import translate
from sessionorc.client import LocalClient
from sessionorc.gitinfo import changed_files
from sessionorc.models import NODE_OWNED, RECENT_FILES, Session


def run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def commit(repo, message, when):
    env = {**os.environ, "GIT_COMMITTER_DATE": f"@{when} +0000", "GIT_AUTHOR_DATE": f"@{when} +0000"}
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True, capture_output=True, env=env)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()


def cloned(tmp_path):
    """A clone of a bare origin whose `main` holds `base.txt` and `old.txt`, on a branch `work` cut from it."""
    origin = tmp_path / "origin.git"
    run("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    repo = tmp_path / "r"
    run("git", "clone", "-q", str(origin), str(repo), cwd=tmp_path)
    run("git", "config", "user.email", "t@t", cwd=repo)
    run("git", "config", "user.name", "t", cwd=repo)
    run("git", "checkout", "-q", "-b", "main", cwd=repo)
    (repo / "base.txt").write_text("b")
    (repo / "old.txt").write_text("o")
    run("git", "add", ".", cwd=repo)
    commit(repo, "base", 1_600_000_000)
    run("git", "push", "-q", "-u", "origin", "main", cwd=repo)
    run("git", "checkout", "-q", "-b", "work", cwd=repo)
    return repo


def test_no_hook_reports_a_file():
    for tool, ti in (("Edit", {"file_path": "/r/a.py"}), ("Write", {"file_path": "/r/b.md"}),
                     ("NotebookEdit", {"notebook_path": "/r/n.ipynb"})):  # fmt: skip
        got = translate({"hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": ti})
        assert "file" not in got and got["event"] == f"PostToolUse:{tool}"


def test_files_is_the_nodes_and_new_records_start_empty():
    assert "files" in NODE_OWNED
    assert Session(id="ao-x", name="x", kind="interactive", dir="/d", adapter="shell").files == []
    assert (
        Session.from_dict({"id": "ao-x", "name": "x", "kind": "interactive", "dir": "/d", "adapter": "shell"}).files
        == []
    )


def test_the_branchs_commits_and_the_tree_newest_first_a_path_once(tmp_path):
    repo = cloned(tmp_path)
    (repo / "a.py").write_text("1")
    (repo / "b c.py").write_text("1")
    run("git", "add", ".", cwd=repo)
    first = commit(repo, "one", 1_700_000_000)
    (repo / "a.py").write_text("2")
    run("git", "add", ".", cwd=repo)
    second = commit(repo, "two", 1_700_000_100)
    (repo / "base.txt").write_text("dirty")  # changed in the tree, and on main before the branch
    (repo / "new.md").write_text("u")  # untracked
    os.utime(repo / "base.txt", (1_700_000_300, 1_700_000_300))
    os.utime(repo / "new.md", (1_700_000_200, 1_700_000_200))
    (repo / "old.txt").unlink()  # deleted and uncommitted: the read's time
    got = changed_files(repo, "origin/main", RECENT_FILES)
    by = {os.path.relpath(f["path"], repo): f for f in got}
    assert list(by) == ["old.txt", "base.txt", "new.md", "a.py", "b c.py"]
    assert all(os.path.isabs(f["path"]) for f in got)
    # a dirty path is the file's, with no sha; a committed one the newest commit that touched it
    assert by["base.txt"] == {"path": str(repo / "base.txt"), "at": "2023-11-14T22:18:20Z"}
    assert by["a.py"] == {"path": str(repo / "a.py"), "at": "2023-11-14T22:15:00Z", "sha": second}
    assert by["b c.py"]["sha"] == first and by["b c.py"]["at"] == "2023-11-14T22:13:20Z"
    assert "sha" not in by["old.txt"] and "sha" not in by["new.md"]
    assert changed_files(repo, "origin/main", 2) == got[:2]  # the newest `limit`


def test_a_merged_branch_lists_nothing_and_a_tree_with_no_origin_lists_its_own(tmp_path):
    repo = cloned(tmp_path)
    (repo / "a.py").write_text("1")
    run("git", "add", ".", cwd=repo)
    commit(repo, "one", 1_700_000_000)
    assert [os.path.basename(f["path"]) for f in changed_files(repo, "origin/main", RECENT_FILES)] == ["a.py"]
    # a path changed and changed back since the base is no change
    (repo / "a.py").unlink()
    run("git", "add", "-A", cwd=repo)
    commit(repo, "back", 1_700_000_100)
    assert changed_files(repo, "origin/main", RECENT_FILES) == []
    run("git", "push", "-q", "origin", "work:main", cwd=repo)  # merged: the base caught up with HEAD
    run("git", "fetch", "-q", "origin", cwd=repo)
    assert changed_files(repo, "origin/main", RECENT_FILES) == []
    # no origin: the tree alone, committed work not listed
    (repo / "x.txt").write_text("x")
    assert [os.path.basename(f["path"]) for f in changed_files(repo, None, RECENT_FILES)] == ["x.txt"]
    # no merge base with the base it names (unborn, unrelated, gone) reads as no origin: the tree alone
    assert [os.path.basename(f["path"]) for f in changed_files(repo, "origin/no-such", RECENT_FILES)] == ["x.txt"]
    # a read that cannot be made is None, so the record keeps its list
    assert changed_files(tmp_path / "not-a-repo", None, RECENT_FILES) is None


def test_a_rename_lists_its_new_path_alone(tmp_path):
    repo = cloned(tmp_path)
    run("git", "mv", "old.txt", "new name.txt", cwd=repo)
    assert [os.path.basename(f["path"]) for f in changed_files(repo, "origin/main", RECENT_FILES)] == ["new name.txt"]


@pytest.mark.integration
async def test_the_tick_reads_the_list_when_the_status_moves_and_a_shell_session_shows_it(agent, tmp_path, monkeypatch):
    from sessionorc import agent_tick

    repo = cloned(tmp_path)
    reads = []
    real = agent_tick._recent_files
    monkeypatch.setattr(agent_tick, "_recent_files", lambda d: reads.append(d) or real(d))

    days = iter(range(1, 100))

    async def tick():
        await agent._refresh_git(datetime.now(UTC) + timedelta(days=next(days)))  # due, whatever the tick did
        return [os.path.basename(f["path"]) for f in (await person.call("get", id=sid))["files"]]

    async with LocalClient() as person:
        sid = (await person.call("create", name="sh", dir=str(repo), adapter="shell", argv=["bash", "--norc"]))["id"]
        assert await tick() == []
        (repo / "made by a script.py").write_text("1")  # no hook, no tool: git sees it
        assert await tick() == ["made by a script.py"]
        n = len(reads)
        assert await tick() == ["made by a script.py"] and len(reads) == n  # a quiet status reads nothing again
        run("git", "add", ".", cwd=repo)
        sha = commit(repo, "one", 1_700_000_000)
        await tick()
        assert (await person.call("list"))[0]["files"][0]["sha"] == sha  # on the pushed view too
        run("git", "push", "-q", "origin", "work:main", cwd=repo)
        run("git", "fetch", "-q", "origin", cwd=repo)
        agent._files_read.pop(sid)  # the fetch moved the base, not the status: a restart's first read
        assert await tick() == []  # merged: an empty read empties the list
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)
        assert sid not in agent._files_read
