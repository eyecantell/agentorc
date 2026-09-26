"""TD-176 slice 1, design §4.4 *Repo facts*: the ledger's entry headers and their history, the PR
reading, and the home's `repos` reading of every registered checkout — kept across a restart, a
failed read keeping the last reading with the error beside it, never zero."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import park_ticks

from sessionorc import hosts, ledger, modes, paths, reports
from sessionorc.client import LocalClient

LEDGER = """# Technical debt

<!-- Entry template:
## TD-001: the template's heading

**Priority:** Medium
-->

## TD-010: a pickable build

**Priority:** High
**Owner:** grinder
**Kind:** build
**Pickable:** yes — slice 1 first

## TD-011: a design question

**Priority:** Medium
**Owner:** designer
**Kind:** design-first
**Pickable:** no

## TD-012: waiting on the person

**Priority:** Low
**Owner:** paul (the call is his)
**Kind:** evaluation
**Pickable:** no

## TD-013: a decision

**Priority:** Medium
**Owner:** anchor
**Kind:** decision
**Pickable:** no

## TD-014: the rest

**Priority:** Medium
**Owner:** anchor
**Kind:** live-check
"""


def test_entries_read_the_header_fields_and_the_page_kind():
    got = {e["id"]: e for e in ledger.entries(LEDGER)}
    assert list(got) == ["TD-010", "TD-011", "TD-012", "TD-013", "TD-014"]  # not the template's TD-001
    assert got["TD-010"] == {
        "id": "TD-010",
        "title": "a pickable build",
        "priority": "high",
        "owner": "grinder",
        "kind": "build",
        "pickable": "yes",
        "for_page": "pickable",
    }
    assert [got[t]["for_page"] for t in got] == ["pickable", "design-first", "for-you", "for-you", "other"]
    assert got["TD-014"]["pickable"] == ""  # absent is empty, never a guess


def test_the_repos_own_ledger_parses_every_entry_the_heading_regex_finds():
    text = (Path(__file__).parents[1] / "docs" / "technical_debt.md").read_text()
    got = ledger.entries(text)
    assert [e["id"] for e in got] == [t for t, _ in ledger.HEADING.findall(ledger.strip_comments(text))]
    assert all(e["priority"] in ledger.PRIORITIES for e in got), "every entry carries a Priority the page counts"


def test_ledger_path_reads_the_repo_file_and_defaults(tmp_path):
    assert ledger.ledger_path(tmp_path) == "docs/technical_debt.md"
    (tmp_path / ".agentorc.yml").write_text("ledger: notes/debt.md\n")
    assert ledger.ledger_path(tmp_path) == "notes/debt.md"
    (tmp_path / ".agentorc.yml").write_text(": not yaml [\n")
    assert ledger.ledger_path(tmp_path) == "docs/technical_debt.md"


def _git(root: Path, *args: str, when: datetime | None = None) -> None:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    if when:
        env |= {"GIT_AUTHOR_DATE": when.isoformat(), "GIT_COMMITTER_DATE": when.isoformat()}
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env={**_path_env(), **env})


def _path_env() -> dict[str, str]:
    import os

    return {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}


def _commit(root: Path, text: str, when: datetime) -> None:
    f = root / "docs" / "technical_debt.md"
    f.parent.mkdir(exist_ok=True)
    f.write_text(text)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "ledger", when=when)


def _entry(tid: str, title: str) -> str:
    return f"## {tid}: {title}\n\n**Priority:** Medium\n**Pickable:** no\n\n"


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    return root


def test_history_opens_at_the_first_commit_holding_a_section_and_closes_at_the_first_without(repo):
    now = datetime.now(UTC)
    t0, t1, t2 = now - timedelta(days=40), now - timedelta(days=3), now - timedelta(hours=2)
    head = "# ledger\n<!-- template\n## TD-001: template\n-->\n\n"
    _commit(repo, head + _entry("TD-002", "old") + _entry("TD-003", "moves"), t0)
    # TD-002 archived, TD-004 filed, TD-003 moved below TD-004 with its title edited: it stays open
    _commit(repo, head + _entry("TD-004", "new") + _entry("TD-003", "moved"), t1)
    _commit(repo, head + _entry("TD-004", "new") + _entry("TD-003", "moved") + _entry("TD-005", "today"), t2)
    life = ledger.history(repo, "docs/technical_debt.md", (repo / "docs/technical_debt.md").read_text())
    assert life is not None
    assert set(life) == {"TD-002", "TD-003", "TD-004", "TD-005"}  # the template's heading is not an entry
    assert life["TD-002"].closed and abs(life["TD-002"].closed - t1) < timedelta(seconds=2)
    assert life["TD-003"].closed is None and life["TD-003"].title == "moved"
    assert abs(life["TD-004"].opened - t1) < timedelta(seconds=2)

    r = ledger.reading(repo, now)
    assert r["windows"] == {
        "day": {"opened": 1, "closed": 0},
        "week": {"opened": 2, "closed": 1},
        "month": {"opened": 2, "closed": 1},
    }
    # newest first (TD-004's opening and TD-002's close are one commit); TD-003 opened before the month
    assert [e["id"] for e in r["recent"]][0] == "TD-005"
    assert {e["id"] for e in r["recent"]} == {"TD-005", "TD-004", "TD-002"}
    assert r["by_kind"] == {"pickable": 0, "design-first": 0, "for-you": 0, "other": 3}


def test_history_is_none_when_git_cannot_be_asked(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "technical_debt.md").write_text(_entry("TD-002", "x"))
    assert ledger.history(tmp_path, "docs/technical_debt.md") is None
    r = ledger.reading(tmp_path, datetime.now(UTC))
    assert len(r["entries"]) == 1 and "windows" not in r and r["history_error"]


def test_a_missing_ledger_is_an_error_not_an_empty_ledger(tmp_path):
    r = ledger.reading(tmp_path, datetime.now(UTC))
    assert "entries" not in r and "docs/technical_debt.md" in r["error"]


# -- the PR reading --------------------------------------------------------------------------------


def _pr(n: int, state: str, created: datetime, closed: datetime | None = None, **kw) -> dict:
    return {
        "number": n,
        "title": f"pr {n}",
        "url": f"https://x/{n}",
        "state": state.upper(),
        "createdAt": created.isoformat().replace("+00:00", "Z"),
        "closedAt": closed.isoformat().replace("+00:00", "Z") if closed and state == "closed" else None,
        "mergedAt": closed.isoformat().replace("+00:00", "Z") if closed and state == "merged" else None,
        "headRefName": f"b{n}",
        "author": {"login": "grinder"},
        "isDraft": False,
        **kw,
    }


def _fake_gh(monkeypatch, open_prs, all_prs, fail=None):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if fail:
            return subprocess.CompletedProcess(argv, 1, "", fail)
        body = open_prs if "open" in argv else all_prs
        return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")

    monkeypatch.setattr(reports.subprocess, "run", run)
    return calls


def test_pr_reading_counts_openings_and_closes_per_window(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    old_open = _pr(1, "open", now - timedelta(days=60))
    fresh = [
        _pr(2, "merged", now - timedelta(days=2), now - timedelta(hours=3)),
        _pr(3, "closed", now - timedelta(days=10), now - timedelta(days=5)),
        _pr(4, "open", now - timedelta(hours=1), isDraft=True),
    ]
    calls = _fake_gh(monkeypatch, [fresh[2], old_open], fresh)
    r = reports.pr_reading(tmp_path, now)
    assert [p["number"] for p in r["open"]] == [1, 4]  # oldest first, the old one from the open read
    assert r["open"][1]["draft"] and r["open"][0]["author"] == "grinder"
    assert r["windows"] == {
        "day": {"opened": 1, "closed": 1},
        "week": {"opened": 2, "closed": 2},
        "month": {"opened": 3, "closed": 2},
    }
    assert [p["number"] for p in r["recent"]] == [4, 2, 3]
    assert "truncated" not in r
    assert any("updated:>=" in " ".join(c) for c in calls)


def test_pr_reading_that_failed_is_an_error_never_zero(tmp_path, monkeypatch):
    _fake_gh(monkeypatch, [], [], fail="HTTP 401: Bad credentials")
    assert reports.pr_reading(tmp_path, datetime.now(UTC)) == {"error": "HTTP 401: Bad credentials"}
    assert "error" in reports.pr_reading(tmp_path / "nowhere", datetime.now(UTC))


# -- the home's reading ----------------------------------------------------------------------------


def _register(*roots: Path) -> None:
    reg = Path(hosts.DEFAULT_REPOS_REGISTRY)
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text("".join(f"{r}\n" for r in roots))


async def test_the_home_reads_every_registered_checkout_and_keeps_the_last_reading_on_failure(agent, repo, monkeypatch):
    await park_ticks(agent)
    _commit(repo, _entry("TD-002", "one"), datetime.now(UTC) - timedelta(hours=1))
    _register(repo)
    asked = []

    def good(d, now, **kw):
        asked.append(d)
        return {"open": [{"number": 7, "created": now.isoformat()}], "windows": {}, "recent": [], "at": now.isoformat()}

    monkeypatch.setattr(reports, "pr_reading", good)
    async with LocalClient() as person:
        events = []

        async def listen():
            async with LocalClient() as sub:
                async for ev in sub.subscribe():
                    if ev.get("event") == "repos":
                        events.append(ev)

        import asyncio

        listener = asyncio.create_task(listen())
        await asyncio.sleep(0.2)
        await agent._refresh_repos()
        got = await person.call("repos")
        r = got[str(repo)]
        assert r["name"] == "r" and r["prs"]["open"][0]["number"] == 7
        assert [e["id"] for e in r["ledger"]["entries"]] == ["TD-002"]
        assert r["ledger"]["windows"]["day"]["opened"] == 1
        assert json.loads(paths.repos_file().read_text())[str(repo)]["name"] == "r"
        await asyncio.sleep(0.2)
        assert events and events[-1]["root"] == str(repo) and events[-1]["repo"]["name"] == "r"

        # not due: the ledger's mtime alone moves a re-read, and the PRs are not asked again
        n = len(asked)
        await agent._refresh_repos()
        assert len(asked) == n

        # the next due read fails: the last reading stays, with the error beside it
        agent._repos_read_at = float("-inf")
        monkeypatch.setattr(reports, "pr_reading", lambda d, now, **kw: {"error": "gh: offline"})
        await agent._refresh_repos()
        r = (await person.call("repos"))[str(repo)]
        assert r["prs"]["open"][0]["number"] == 7 and r["prs"]["error"] == "gh: offline" and r["prs"]["failed_at"]

        # an entry filed: the mtime moved, so the ledger is re-read on the next tick, history kept
        (repo / "docs" / "technical_debt.md").write_text(_entry("TD-002", "one") + _entry("TD-003", "two"))
        await agent._refresh_repos()
        r = (await person.call("repos"))[str(repo)]
        assert [e["id"] for e in r["ledger"]["entries"]] == ["TD-002", "TD-003"]
        assert r["ledger"]["windows"]["day"]["opened"] == 1  # the history is read on the cadence

        # dropped from the registry: gone from the reading, and a `repo: null` event says so
        _register()
        await agent._refresh_repos()
        assert await person.call("repos") == {}
        await asyncio.sleep(0.2)
        assert events[-1] == {"event": "repos", "root": str(repo), "repo": None}
        listener.cancel()


async def test_two_checkouts_of_one_remote_are_one_pr_read(agent, tmp_path, monkeypatch):
    await park_ticks(agent)
    roots = []
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        _git(root, "init", "-q", "-b", "main")
        _git(root, "remote", "add", "origin", "https://github.com/x/same.git")
        roots.append(root)
    _register(*roots)
    asked = []

    def good(d, now, **kw):
        asked.append(d)
        return {"open": [], "windows": {}, "recent": [], "at": now.isoformat()}

    monkeypatch.setattr(reports, "pr_reading", good)
    await agent._refresh_repos()
    assert len(asked) == 1
    assert set(agent._repos) == {str(r) for r in roots}
    assert all(r["remote"] == "https://github.com/x/same.git" for r in agent._repos.values())


def test_a_node_refuses_the_repo_facts_while_its_home_is_unreachable():
    why = modes.offline_refusal("repos", None, {}, host="laptop", home="kmaster")
    assert why and "kmaster (home)" in why and "repo facts" in why


# -- ao repo ---------------------------------------------------------------------------------------


def test_ao_repo_prints_the_numbers_and_says_could_not_look(repo, monkeypatch, capsys):
    from agentorc import cli

    now = datetime.now(UTC)
    reading = {
        str(repo): {
            "name": "r",
            "root": str(repo),
            "prs": {
                "open": [
                    {"number": 9, "title": "the fix", "created": (now - timedelta(days=3)).isoformat(), "author": "g"}
                ],
                "windows": {"week": {"opened": 4, "closed": 5}},
                "error": "gh: offline",
                "failed_at": now.isoformat(),
            },
            "ledger": {
                "entries": [{"id": "TD-010", "title": "a build", "for_page": "pickable"}],
                "by_kind": {"pickable": 1, "design-first": 0, "for-you": 0, "other": 0},
            },
            "at": now.isoformat(),
        },
        "/elsewhere": {"name": "other", "root": "/elsewhere", "prs": {"error": "no gh"}, "ledger": {"error": "gone"}},
    }
    monkeypatch.setattr(cli, "call_sync", lambda rpc, **kw: reading)
    monkeypatch.chdir(repo)
    assert cli.main(["repo"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("r  1 open PRs, oldest 3d · this week 4 opened, 5 closed (could not look")
    assert "1 open entries: 1 pickable, 0 design-first" in out
    assert "#9" in out and "pickable     TD-010  a build" in out
    assert cli.main(["repo", "--all"]) == 0
    out = capsys.readouterr().out
    assert "other  PRs: could not look (no gh) · ledger: could not look (gone)" in out and "#9" not in out
    assert cli.main(["--json", "repo", "other"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["root"] == "/elsewhere"
    assert cli.main(["repo", "nope"]) != 0


async def test_one_checkouts_failure_keeps_its_reading_and_costs_the_others_nothing(agent, tmp_path, monkeypatch):
    """Review of PR #595: a read that raises (not a `gh` outage — a surprise) is that checkout's
    error, its last reading kept; the other checkouts are read; and the clock does not advance past
    a pass that raised, so the next tick tries again."""
    await park_ticks(agent)
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        root.mkdir()
    _register(a, b)

    def pr(d, now, **kw):
        if Path(d) == a:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return {"open": [], "windows": {}, "recent": [], "at": now.isoformat()}

    monkeypatch.setattr(reports, "pr_reading", pr)
    await agent._refresh_repos()
    got = agent._repos
    assert got[str(b)]["prs"]["open"] == [] and "error" not in got[str(b)]["prs"]
    assert got[str(a)]["prs"]["error"] == "the read failed: UnicodeDecodeError"
    assert got[str(a)]["prs"].get("open") is None  # nothing before it: *could not look*, never zero

    # a checkout registered between two due reads is read whole at once
    c = tmp_path / "c"
    c.mkdir()
    _register(a, b, c)
    await agent._refresh_repos()
    assert agent._repos[str(c)]["prs"]["open"] == []


def test_history_survives_bytes_that_are_not_utf8(repo):
    _commit(repo, _entry("TD-002", "caf\xe9"), datetime.now(UTC))
    (repo / "docs" / "technical_debt.md").write_bytes(_entry("TD-002", "x").encode() + b"\xff\xfe junk\n")
    _git(repo, "commit", "-qam", "bytes", when=datetime.now(UTC))
    assert ledger.history(repo, "docs/technical_debt.md") is not None
