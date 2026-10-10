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


def test_the_chip_is_always_drawn_as_the_live_commits_local_time(repo):
    """Design §4.5a top bar **build** chip (TD-539): always drawn — *live <MM-DD HH:MM>*, the live
    commit's committer time in this process's zone, `at` the ISO the page reprints — then *· main +n*
    with the pending subjects on hover, measured here when no reading holds the build."""
    built = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    b = {"commit": built, "source": str(repo), "built_at": "t"}
    at = _git(repo, "log", "-1", "--format=%cI", built)
    c = build.chip(b, "s")
    assert c["cls"] == "live" and c["at"] == at and c["rest"] == ""
    assert c["text"] == f"live {build.stamp(at)}" and c["title"].startswith(f"{built[:7]} one\nhost agent: built from")
    assert "not live yet" not in c["title"] and "measured from this checkout's origin/main" in c["title"]
    _commit(repo, "2\n")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    c = build.chip(b, "s")
    assert (c["cls"], c["rest"], c["text"]) == ("behind", " · main +1", f"live {build.stamp(at)} · main +1")
    assert "1 commit ahead" in c["title"] and "\nnot live yet:\n" in c["title"]
    assert f"\n{_git(repo, 'rev-parse', 'HEAD')[:7]} 2" in c["title"]
    assert build.chip(b, a={"ref": "origin/main", "ahead": 4})["text"].endswith(" · main +4")
    assert build.chip({}) == {"text": "build unknown", "title": build.line({}), "cls": "unknown", "at": "", "rest": ""}
    gone = build.chip({"commit": built, "source": str(repo / "gone")})
    # no checkout to read its time from: the sha stands in, and nothing for the page to reprint
    assert gone["text"] == f"live {built[:7]} · main unknown" and gone["at"] == ""
    assert "not on this host" in gone["title"]


def test_the_chip_says_promoting_held_the_page_and_a_stale_node():
    """§4.5a (TD-539): *promoting…* while a run is in flight, *held* after a rollback, the pending
    list from the reading with *… and n more*, the page's own build where it differs, and a stale node."""
    b = {"commit": "a" * 40, "source": "/nonexistent", "built_at": "t"}
    a = {"ref": "origin/main", "ahead": 12}
    rows = [{"sha": f"{i:x}" * 40, "subject": f"s{i}"} for i in range(10)]
    reading = {"live_at": "2026-10-10T15:41:00+00:00", "pending": rows, "pending_more": 2}
    c = build.chip(b, "", a, **reading)
    assert c["cls"] == "behind" and c["at"] == reading["live_at"] and c["text"].startswith("live ")
    assert c["title"].endswith("s9\n… and 2 more") and "measured from" not in c["title"]
    assert build.chip(b, "", a, **reading, inflight={"sha": "b"})["rest"] == " · promoting…"
    assert build.chip(b, "", a, **reading, held={"sha": "a"})["cls"] == "held"
    page = {"commit": "b" * 40, "source": "/nonexistent", "built_at": "2026-10-10T16:00:00Z"}
    c = build.chip(b, "", a, page=page, nodes=[("box", "c" * 40)])
    assert "\nthis page: bbbbbbb 2026-10-10T16:00:00Z" in c["title"] and c["title"].endswith("box: ccccccc, stale")
    assert "this page" not in build.chip(b, "", a, page=dict(b))["title"]
    assert build.stamp("2025-01-02T03:04:00+00:00").startswith("2025-")


def test_the_org_chip_reads_the_homes_promote_reading_so_it_agrees_with_the_row(repo):
    """The techlead's read of TD-132 slice 5: where the home's `promotes` reading holds the checkout
    the build came from, at the same live commit, the chip takes main's distance from it — the
    Inbox's Promote row's number — rather than this checkout's last fetch; otherwise it measures here."""
    from agentorc.ui.common import build_chip

    built = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "2\n")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")  # the checkout: 1 ahead
    b = {"commit": built, "source": str(repo), "built_at": "t"}
    reading = {"root": str(repo), "live": built, "main": "9" * 40, "ahead": 3}  # the home's fetch: 3 ahead
    info = {"built_from": b, "started_at": "s", "promotes": {"src": reading}}
    got = build_chip(info)
    assert got["text"].endswith(" · main +3") and "3 commits ahead" in got["title"]
    assert "measured from" not in got["title"]
    pend = {"pending": [{"sha": "9" * 40, "subject": "the fix"}], "pending_more": 0, "inflight": {"sha": "9" * 40}}
    got = build_chip({**info, "promotes": {"src": {**reading, **pend}}})
    assert got["cls"] == "inflight" and "\nnot live yet:\n9999999 the fix" in got["title"]
    assert build_chip({**info, "promotes": {"src": {**reading, "ahead": 0, "main": built}}})["cls"] == "live"
    # main moved to a line the build is not on (a force-push, a branch build): the row shows, and so
    # does the chip, with no count (the review of #743)
    for n in (0, None):
        off = build_chip({**info, "promotes": {"src": {**reading, "ahead": n}}})
        assert off["text"].endswith(" · main unknown") and "cannot count back to" in off["title"]
    # a reading of another live commit, another checkout, or no count: measured here
    for other in ({**reading, "live": "0" * 40}, {**reading, "root": str(repo.parent)}, {**reading, "main": None}):
        assert build_chip({**info, "promotes": {"src": other}})["text"].endswith(" · main +1")
    assert build_chip(None) is None
