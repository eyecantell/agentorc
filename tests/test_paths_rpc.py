"""The `paths` read (design §4.6 *A path in the pane is a link*, TD-501): which runs a pane named are
regular files inside the record's checkout, answered by the record's host."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sessionorc.agent_common import NODE_READS, PATHS_RUN_CHARS, PATHS_RUNS_MAX
from sessionorc.client import AgentError, LocalClient


def checkout(tmp_path: Path) -> Path:
    """A repo with a worktree beside its files: `dir` is the worktree, `repo` the main checkout."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "x.py").write_text("x\n")
    (repo / "top.md").write_text("top\n")
    wt = repo / ".claude" / "worktrees" / "w"
    (wt / "src" / "sub").mkdir(parents=True)
    (wt / "src" / "a.py").write_text("a\n")
    (wt / "out.txt").symlink_to("/etc/passwd")
    (wt / "in.txt").symlink_to(wt / "src" / "a.py")
    (tmp_path / "outside.txt").write_text("no\n")
    (tmp_path / "repo-evil").mkdir()  # a sibling whose name starts with the repo's
    (tmp_path / "repo-evil" / "f.txt").write_text("no\n")
    return wt


@pytest.mark.integration
async def test_a_run_answers_only_as_a_regular_file_inside_the_checkout(agent, tmp_path, monkeypatch):
    assert "paths" in NODE_READS  # a node's record answers through `read`, as a transcript does
    wt = checkout(tmp_path)
    repo = wt.parent.parent.parent
    home = tmp_path / "userhome"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (home / "h.txt").write_text("h\n")
    async with LocalClient() as person:
        sid = (await person.call("create", name="sh", dir=str(wt), adapter="shell", argv=["bash", "--norc"]))["id"]
        agent.sessions[sid].repo = str(repo)  # a worktree's record carries its main checkout
        runs = [
            "src/a.py",  # relative to `dir`
            "./src/a.py",
            "src/a.py:12",  # the page strips `:line` before it asks: a run with one is not a file
            "src/sub",  # a directory is the header button's
            "src/missing.py",
            "out.txt",  # a symlink out of the checkout
            "in.txt",  # a symlink inside it answers as its target
            "../../../src/x.py",  # up out of the worktree, into the repo: still ours
            "../../../../outside.txt",  # and up out of both
            f"{repo}/top.md",  # absolute, inside the repo
        ]
        got = {}
        for i in range(0, len(runs), PATHS_RUNS_MAX):  # one row asks about eight at most
            got |= (await person.call("paths", id=sid, runs=runs[i : i + PATHS_RUNS_MAX]))["paths"]
        assert got == {
            "src/a.py": str(wt / "src" / "a.py"),
            "./src/a.py": str(wt / "src" / "a.py"),
            "src/a.py:12": None,
            "src/sub": None,
            "src/missing.py": None,
            "out.txt": None,
            "in.txt": str(wt / "src" / "a.py"),
            "../../../src/x.py": str(repo / "src" / "x.py"),
            "../../../../outside.txt": None,
            f"{repo}/top.md": str(repo / "top.md"),
        }
        odd = [f"{repo}-evil/f.txt", "src/a\x00.py", "~nosuchuser-td501/x"]
        assert (await person.call("paths", id=sid, runs=odd))["paths"] == dict.fromkeys(odd)  # a prefix is not a root
        with pytest.raises(AgentError, match="runs, a list of strings"):
            await person.call("paths", id=sid, runs=["src/a.py", 3])
        assert (await person.call("paths", id=sid, runs=["~/h.txt", "/etc/passwd"]))["paths"] == {
            "~/h.txt": None,  # home expanded, and outside the checkout
            "/etc/passwd": None,
        }
        # the bounds, refused naming them
        with pytest.raises(AgentError, match=f"past the {PATHS_RUNS_MAX} one row may ask about"):
            await person.call("paths", id=sid, runs=["a/b"] * (PATHS_RUNS_MAX + 1))
        with pytest.raises(AgentError, match=f"past the {PATHS_RUN_CHARS} one may be"):
            await person.call("paths", id=sid, runs=["a/" * PATHS_RUN_CHARS])
        with pytest.raises(AgentError, match="runs, a list of strings"):
            await person.call("paths", id=sid, runs="src/a.py")
        with pytest.raises(AgentError, match="no session ao-nope"):
            await person.call("paths", id="ao-nope", runs=[])
        async with LocalClient(caller=sid) as other:
            assert (await other.call("paths", id=sid, runs=["src/a.py"]))["paths"]["src/a.py"]  # a read: ungated
        # a `~/` run inside the checkout answers: the home is the checkout's parent here
        monkeypatch.setenv("HOME", str(repo))
        assert (await person.call("paths", id=sid, runs=["~/top.md"]))["paths"] == {"~/top.md": str(repo / "top.md")}
        # a node serves it to the home through `read`
        old_mode = agent.mode, agent.home
        agent.mode, agent.home = "node", "kmaster"
        reply = await agent._from_home("read", {"rpc": "paths", "params": {"id": sid, "runs": ["src/a.py"]}})
        assert reply == {"paths": {"src/a.py": str(wt / "src" / "a.py")}}
        agent.mode, agent.home = old_mode
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)


@pytest.mark.integration
async def test_an_unreadable_file_and_a_record_with_no_repo(agent, tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    (d / "f.txt").write_text("f\n")
    locked = d / "locked"
    locked.mkdir()
    (locked / "g.txt").write_text("g\n")
    async with LocalClient() as person:
        sid = (await person.call("create", name="sh", dir=str(d), adapter="shell", argv=["bash", "--norc"]))["id"]
        assert agent.sessions[sid].repo is None
        os.chmod(locked, 0)
        try:
            got = (await person.call("paths", id=sid, runs=["f.txt", "locked/g.txt"]))["paths"]
        finally:
            os.chmod(locked, 0o755)
        assert got["f.txt"] == str(d / "f.txt")
        assert got["locked/g.txt"] is None or os.geteuid() == 0  # root stats through a mode of 0
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)
