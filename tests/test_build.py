"""Which commit a build came from, and how far `main` has moved past it (design §4.4 *Version skew
is survivable*, TD-062 (c)): the build hook writes it, the host agent reports it on `host`, and
`ao status -v` / `ao service status` say when `origin/main` is ahead."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from sessionorc import build
from sessionorc.client import LocalClient

ROOT = Path(__file__).resolve().parent.parent


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "src"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "a").write_text("1\n")
    _git(r, "add", "a")
    _git(r, "commit", "-q", "-m", "one")
    return r


def _commit(repo: Path, text: str) -> str:
    (repo / "a").write_text(text)
    _git(repo, "commit", "-q", "-am", text)
    return _git(repo, "rev-parse", "HEAD")


def _hook():
    spec = importlib.util.spec_from_file_location("pdm_build", ROOT / "pdm_build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _context(root: Path, out: Path, target: str = "wheel"):
    def ensure_build_dir() -> Path:
        out.mkdir(parents=True, exist_ok=True)
        return out

    return SimpleNamespace(target=target, root=root, ensure_build_dir=ensure_build_dir)


def test_the_hook_writes_the_commit_into_the_wheel_and_only_the_wheel(repo, tmp_path):
    hook = _hook()
    head = _git(repo, "rev-parse", "HEAD")
    hook.pdm_build_update_files(_context(repo, tmp_path / "b"), {})
    data = json.loads((tmp_path / "b" / "sessionorc" / "_build.json").read_text())
    assert data["commit"] == head and data["dirty"] is False and data["source"] == str(repo.resolve())
    assert data["built_at"].endswith("Z")

    (repo / "a").write_text("changed\n")  # a tracked change on top of the commit is said
    hook.pdm_build_update_files(_context(repo, tmp_path / "d"), {})
    assert json.loads((tmp_path / "d" / "sessionorc" / "_build.json").read_text())["dirty"] is True

    for target in ("sdist", "editable"):  # an editable install runs the checkout; an sdist has no wheel path
        hook.pdm_build_update_files(_context(repo, tmp_path / target, target), {})
        assert not (tmp_path / target).exists()


def test_the_hook_writes_nothing_outside_a_git_checkout(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    _hook().pdm_build_update_files(_context(plain, tmp_path / "b"), {})
    assert not (tmp_path / "b").exists()


def test_info_reads_the_record_and_is_empty_without_one(tmp_path, monkeypatch):
    monkeypatch.setattr(build.resources, "files", lambda _pkg: tmp_path)
    assert build.info() == {}  # none: an editable install, or a wheel from an sdist
    (tmp_path / "_build.json").write_text("not json")
    assert build.info() == {}
    (tmp_path / "_build.json").write_text(json.dumps({"commit": ""}))
    assert build.info() == {}
    rec = {"commit": "abc", "dirty": False, "source": "/x", "built_at": "2026-09-21T00:00:00Z"}
    (tmp_path / "_build.json").write_text(json.dumps(rec))
    assert build.info() == rec


def test_ahead_counts_what_origin_main_holds_that_the_build_does_not(repo):
    built = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    b = {"commit": built, "source": str(repo)}
    assert build.ahead(b) == {"ref": "origin/main", "ahead": 0}
    assert "current with origin/main" in build.line(b)

    _commit(repo, "2\n")
    _commit(repo, "3\n")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert build.ahead(b) == {"ref": "origin/main", "ahead": 2}
    line = build.line(b, "2026-09-21T01:00:00Z")
    assert line.startswith(f"host agent: built from {built[:12]}")
    assert "started 2026-09-21T01:00:00Z" in line and "origin/main is 2 commits ahead of it" in line


def test_ahead_says_why_when_it_cannot_compare(repo, tmp_path):
    assert "does not say" in build.ahead({})["why"]
    assert "not on this host" in build.ahead({"commit": "abc", "source": str(tmp_path / "gone")})["why"]
    # a checkout that was never fetched has no origin/main, and a commit it does not hold is not counted
    assert "is not in" in build.ahead({"commit": _git(repo, "rev-parse", "HEAD"), "source": str(repo)})["why"]
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert "is not in" in build.ahead({"commit": "0" * 40, "source": str(repo)})["why"]
    assert "cannot compare with origin/main" in build.line({"commit": "0" * 40, "source": str(repo)})
    assert build.line({}).startswith("host agent: build unknown")  # an old agent is said, not skipped


async def test_host_reports_the_build_and_when_the_agent_started(agent):
    async with LocalClient() as c:
        me = await c.call("host")
    assert me["built_from"] == agent.build == build.info()
    assert me["started_at"] == agent.started_at and me["started_at"].endswith("Z")


async def test_status_v_prints_the_running_build(agent, repo, capsys):
    from agentorc import cli

    built = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "2\n")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    agent.build = {"commit": built, "dirty": False, "source": str(repo), "built_at": "2026-09-21T00:00:00Z"}
    assert await asyncio.to_thread(cli.main, ["status", "-v"]) == 0
    out = capsys.readouterr().out
    assert f"host agent: built from {built[:12]} at 2026-09-21T00:00:00Z" in out
    assert "origin/main is 1 commit ahead of it, not live until the next promote" in out

    agent.build = {}  # a build with no record says so, rather than nothing
    assert await asyncio.to_thread(cli.main, ["status", "-v"]) == 0
    assert "host agent: build unknown" in capsys.readouterr().out
