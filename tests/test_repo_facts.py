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

## TD-011: a design question

**Priority:** Medium
**Owner:** designer
**Kind:** design-first
**Blocked by:** TD-014

## TD-012: waiting on the person

**Priority:** Low
**Owner:** paul (the call is his)
**Kind:** evaluation

## TD-013: a decision

**Priority:** Medium
**Owner:** anchor
**Kind:** decision

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
        "type": "debt",
        "blocked_by": [],
        "pickable": "yes",
        "for_page": "pickable",
    }
    # an anchor's `Kind: decision` is no longer *for you* (TD-367): it waits on no person, and is the
    # anchor's *evaluation*; a design-first waiting on an open entry is *blocked* (TD-418)
    assert [got[t]["for_page"] for t in got] == ["pickable", "blocked", "for-you", "evaluation", "live-check"]
    # derived (TD-228): no Blocked by is pickable, and an open blocker is not
    assert got["TD-014"]["pickable"] == "yes" and got["TD-011"]["pickable"] == "no"
    assert got["TD-011"]["blocked_by"] == ["TD-014"]


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
    return f"## {tid}: {title}\n\n**Priority:** Medium\n**Kind:** live-check\n\n"


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
    assert r["by_kind"] == dict.fromkeys(ledger.KINDS, 0) | {"live-check": 3}  # the seven kinds (TD-418)


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
    reg = hosts.default_repos_registry()
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
                "entries": [
                    {"id": "TD-010", "title": "a build", "for_page": "pickable", "priority": "low", "owner": "grinder"},
                    {"id": "TD-011", "title": "no priority", "for_page": "pickable", "priority": ""},
                    {
                        "id": "TD-015",
                        "title": "a high feature",
                        "for_page": "pickable",
                        "priority": "high",
                        "type": "feature",
                    },
                    {
                        "id": "TD-012",
                        "title": "a first high",
                        "for_page": "pickable",
                        "priority": "high",
                        "type": "debt",
                    },
                    {"id": "TD-013", "title": "a medium", "for_page": "pickable", "priority": "medium"},
                    {"id": "TD-014", "title": "a second high", "for_page": "pickable", "priority": "high"},
                ],
                "by_kind": {"pickable": 1, "design": 0, "for-you": 0, "other": 0},
            },
            "at": now.isoformat(),
        },
        "/elsewhere": {"name": "other", "root": "/elsewhere", "prs": {"error": "no gh"}, "ledger": {"error": "gone"}},
    }
    fleet = [
        {
            "id": "g1",
            "team": "t",
            "repo": str(repo),
            "state": "working",
            "progress": [{"ref": "TD-010", "status": "claimed", "pr": 9}],
        },
        {"id": "tl", "team": "t", "repo": str(repo), "state": "exited", "role": "techlead"},
        {"id": "x", "team": "u", "repo": "/elsewhere"},
    ]
    inbox = {"entries": [{"kind": "ask", "pr": 9, "at": (now - timedelta(minutes=40)).isoformat()}]}
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "user_attention.md").write_text("## Needs the user\n")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "nudge_user_attention.py").write_text(
        "import json\n"
        "print(json.dumps({'boards': [{'items': [{'text': 'decide TD-283', 'due_tag': '3d overdue'}]}]}))\n"
    )
    log = {
        "t": [
            {"team": "t", "id": "g1", "text": "reading the ledger", "at": (now - timedelta(minutes=5)).isoformat()},
            {"team": "t", "id": "g1", "text": "pushing TD-010", "at": now.isoformat()},
        ],
        "u": [{"team": "u", "id": "x", "text": "not this repo's", "at": now.isoformat()}],
    }
    monkeypatch.setattr(
        cli, "call_sync", lambda rpc, **kw: {"repos": reading, "list": fleet, "doing_log": log, "inbox": inbox}[rpc]
    )
    monkeypatch.chdir(repo)
    assert cli.main(["repo"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("r  1 open PRs, oldest 3d · this week 4 opened, 5 closed (could not look")
    assert "6 open entries: 1 pickable, 0 design" in out  # the counts are the reading's `by_kind`
    assert "#9" in out and "pickable     TD-010  Low     grinder      a build" in out
    # the pick order (§4.8 *Choosing in a free-pick lane*, TD-202): High, Medium, Low, none; ties in file order
    order = [i for i in ("TD-012", "TD-014", "TD-013", "TD-010", "TD-011") if f"pickable     {i}" in out]
    assert sorted(order, key=out.index) == ["TD-012", "TD-014", "TD-013", "TD-010", "TD-011"]
    # each line names the entry's owner, `-` where its header has none (TD-228): pickable reads no owner
    assert "pickable     TD-011  -       -            no priority" in out
    # cadence's order (§4.4 *Repo facts*, TD-228): within a priority, debt before a feature
    assert out.index("pickable     TD-014") < out.index("pickable     TD-015") < out.index("pickable     TD-013")
    # slice 6: the reader's standing on each open PR, what members hold, the board items due
    assert "waiting on tl · 40m" in out and "holds        TD-010 → #9  g1" in out
    assert "due          3d overdue  decide TD-283" in out
    # the servicing team's doing log, newest first; another team's is not this repo's
    assert out.index("g1: pushing TD-010") < out.index("g1: reading the ledger") and "not this repo's" not in out
    # a team over its line in this repo is the last line, in the card's words (§6 *Balance*, TD-239)
    assert "over its line" not in out
    reading[str(repo)]["balance"] = {"t": {"since": "", "crossed": [{"line": "prs", "value": 9, "limit": 8}]}}
    assert cli.main(["repo"]) == 0
    assert capsys.readouterr().out.rstrip().endswith("  t is over its line: 9 open PRs, line 8")
    assert cli.main(["repo", "--all"]) == 0
    out = capsys.readouterr().out
    assert "other  PRs: could not look (no gh) · ledger: could not look (gone)" in out and "#9" not in out
    assert cli.main(["--json", "repo", "other"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["root"] == "/elsewhere"
    assert cli.main(["repo", "nope"]) != 0
    # a decided board line is a work order, first among the pickable rows and counted (§4.7, TD-384)
    order = {"id": "board:0123abcd", "title": "Keep the backup?", "for_page": "pickable", "pickable": "yes",
             "work_order": True, "priority": "High", "decided": {"text": "keep", "date": "2026-10-07"}}  # fmt: skip
    reading[str(repo)]["work_orders"] = {"orders": [order]}
    assert cli.main(["repo"]) == 0
    out = capsys.readouterr().out
    assert "6 open entries, 1 decided board line: 2 pickable, 0 design" in out
    assert "pickable     board:0123abcd  High    board        Keep the backup? · decided keep 2026-10-07" in out
    assert out.index("board:0123abcd") < out.index("pickable     TD-012")
    # the lanes take it beside the entries: the out-of-work line names it (§4.4 *In a team's lanes*)
    fleet.append({"id": "g2", "name": "g2", "team": "t", "repo": str(repo), "state": "idle", "lane": ["free-pick"],
                  "out_of_work": {"at": now.isoformat()}})  # fmt: skip
    assert cli.main(["repo"]) == 0
    assert "g2 is out of work with" in (out := capsys.readouterr().out) and "board:0123abcd" in out.split("g2 is")[1]
    assert cli.main(["--json", "repo"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["work_orders"]["orders"][0]["id"] == "board:0123abcd"


def test_ao_repo_marks_a_live_check_and_lists_the_ones_that_wait(repo, monkeypatch, capsys):
    """TD-323 slice 2 (design §4.9b): a live check whose build is live is among the pickable lines,
    marked *live check* with its build's PR, in the pick order; one whose build is not live is a
    `live-check` line saying it waits; one the person judges (*for you*) is on neither, nor one blocked
    by a decision alone, which is counted under *live check* and listed nowhere (§4.7, TD-418)."""
    from agentorc import cli

    def e(i, page, prio, live, built, owner="grinder", blocked=()):
        return {"id": i, "title": f"check {i}", "for_page": page, "priority": prio, "owner": owner,
                "kind": "live-check", "live": live, "built": built, "blocked_by": list(blocked),
                "pickable": "no" if blocked else "yes"}  # fmt: skip

    entries = [
        {"id": "TD-010", "title": "a build", "for_page": "pickable", "priority": "medium", "kind": "build"},
        e("TD-020", "live-check", "high", "yes", [1051]),
        e("TD-021", "live-check", "low", "no", []),
        e("TD-022", "live-check", "high", "no", [7, 8]),
        e("TD-023", "for-you", "high", "no", [9], owner="paul"),
        e("TD-024", "live-check", "high", "yes", [9], owner="anchor", blocked=["decision (anchor)"]),
    ]
    reading = {str(repo): {"name": "r", "root": str(repo), "prs": {"open": []}, "ledger": {"entries": entries}}}
    monkeypatch.setattr(
        cli, "call_sync", lambda rpc, **kw: {"repos": reading, "list": [], "doing_log": {}, "inbox": {}}[rpc]
    )
    monkeypatch.chdir(repo)
    assert cli.main(["repo"]) == 0
    out = capsys.readouterr().out
    assert "pickable     TD-020  High    grinder      live check #1051: check TD-020" in out
    assert out.index("pickable     TD-020") < out.index("pickable     TD-010")
    assert "live-check   TD-022  High    grinder      waits for its build to be live (#7 #8): check TD-022" in out
    assert "live-check   TD-021  Low     grinder      waits for its build to be live (no PR on its Kind: line)" in out
    assert out.index("live-check   TD-022") < out.index("live-check   TD-021") and "TD-023" not in out
    assert "TD-024" not in out
    assert "a build" in out and "live check #" not in out.split("TD-010")[1].split("\n")[0]


def test_ao_repo_marks_a_design_first_row_that_is_a_designer_decision(repo, monkeypatch, capsys):
    """TD-368 slice 2 (design §4.7, TD-367): a build on `decision (designer)` is on the design list
    and says *decision* after its owner; a `Kind: design-first` row does not."""
    from agentorc import cli

    entries = [
        {"id": "TD-151", "title": "metered", "for_page": "design", "priority": "low", "owner": "grinder",
         "kind": "build", "blocked_by": ["decision (designer)"]},
        {"id": "TD-230", "title": "a question", "for_page": "design", "priority": "high", "owner": "designer",
         "kind": "design-first", "blocked_by": []},
    ]  # fmt: skip
    reading = {str(repo): {"name": "r", "root": str(repo), "prs": {"open": []}, "ledger": {"entries": entries}}}
    monkeypatch.setattr(
        cli, "call_sync", lambda rpc, **kw: {"repos": reading, "list": [], "doing_log": {}, "inbox": {}}[rpc]
    )
    monkeypatch.chdir(repo)
    assert cli.main(["repo"]) == 0
    out = capsys.readouterr().out
    assert "design       TD-151  Low     grinder decision  metered" in out
    assert "design       TD-230  High    designer     a question" in out


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


# -- the doing log (TD-176 slice 2, design §4.8 *the doing log*) --------------------------------------


def test_the_doing_log_keeps_the_last_calls_per_team_and_compacts_its_file(tmp_path):
    from sessionorc.store import DoingLogStore

    f = tmp_path / "doing.jsonl"
    log = DoingLogStore(f, keep=3, compact_at=8)
    for i in range(5):
        log.append({"team": "a", "id": "g", "text": f"a{i}", "at": str(i)})
    log.append({"team": "b", "id": "h", "text": "b0", "at": "9"})
    assert [e["text"] for e in log.rings["a"]] == ["a2", "a3", "a4"]  # the fourth call dropped the first
    assert len(f.read_text().splitlines()) == 6  # appended, not yet compacted
    for i in range(5, 8):
        log.append({"team": "a", "id": "g", "text": f"a{i}", "at": str(i)})
    assert len(f.read_text().splitlines()) <= 8  # compacted to the rings
    f.write_text(f.read_text() + "not json\n")
    again = DoingLogStore(f, keep=3)
    assert [e["text"] for e in again.rings["a"]] == ["a5", "a6", "a7"] and [e["text"] for e in again.rings["b"]] == [
        "b0"
    ]


async def test_a_team_members_doing_calls_are_logged_pushed_and_read(agent, tmp_path):
    import asyncio

    async with LocalClient() as person:
        s = await person.call(
            "create", name="doer", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"], team="grind"
        )
        lone = await person.call("create", name="lone", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        events = []

        async def listen():
            async with LocalClient() as sub:
                async for ev in sub.subscribe():
                    if ev.get("event") == "doing":
                        events.append(ev)

        listener = asyncio.create_task(listen())
        await asyncio.sleep(0.2)
        async with LocalClient(caller=s["id"]) as me:
            await me.call("doing", id=s["id"], text="reading the ledger")
            await me.call("doing", id=s["id"], text="pushing the branch")
            await me.call("doing", id=s["id"], clear=True)  # a clear is not a call the feed shows
        async with LocalClient(caller=lone["id"]) as me:
            await me.call("doing", id=lone["id"], text="on my own")  # no team: no team's feed
        got = await person.call("doing_log")
        assert list(got) == ["grind"]
        assert [e["text"] for e in got["grind"]] == ["reading the ledger", "pushing the branch"]
        assert got["grind"][0]["id"] == s["id"] and got["grind"][0]["at"]
        assert await person.call("doing_log", team="nobody") == {"nobody": []}
        assert [json.loads(line)["text"] for line in paths.doing_log_file().read_text().splitlines()] == [
            "reading the ledger",
            "pushing the branch",
        ]
        await asyncio.sleep(0.2)
        assert [e["entry"]["text"] for e in events] == ["reading the ledger", "pushing the branch"]
        assert events[0]["team"] == "grind"
        listener.cancel()


def test_a_doing_log_cut_short_mid_character_loads_without_it(tmp_path):
    """Review of PR #596: a crash mid-append can leave a truncated UTF-8 sequence at the file's end;
    the log loads, the cut line skipped."""
    from sessionorc.store import DoingLogStore

    f = tmp_path / "doing.jsonl"
    f.write_bytes(b'{"team": "a", "id": "g", "text": "ok", "at": "1"}\n{"team": "a", "text": "caf\xc3')
    assert [e["text"] for e in DoingLogStore(f).rings["a"]] == ["ok"]


def test_the_grinder_template_says_the_pick_order_and_names_the_tool():
    """TD-202, design §4.8 *Choosing in a free-pick lane*: the order spelled out, `ao repo` named,
    and the priority in the claim's first `ao doing` line."""
    from importlib import resources

    text = resources.files("agentorc").joinpath("briefs", "grinder.md").read_text(encoding="utf-8")
    assert "High, then Medium, then Low" in text and "`ao repo` lists" in text
    assert "put the priority there" in text and "Choosing in a free-pick lane" in text


def _oct6():
    """The ledger of 2026-10-06 (TD-357): 24 pickable, 20 the anchor's and 4 dev-cadence's, one
    design entry a designer's lane takes and a designed one waiting on its build, *blocked* (TD-418)."""

    def e(i, page, owner, kind="build"):
        return {"id": i, "title": i, "for_page": page, "owner": owner, "kind": kind, "pickable": "yes"}

    entries = [e(f"TD-{100 + i}", "pickable", "anchor") for i in range(20)]
    entries += [e(f"TD-{200 + i}", "pickable", "dev-cadence") for i in range(4)]
    entries += [e("TD-300", "design", "designer", "design-first")]
    entries += [{**e("TD-301", "blocked", "designer", "design-first"), "pickable": "no", "blocked_by": ["TD-999"]}]
    entries += [e("TD-400", "for-you", "paul", "decision")]
    grind = ["free-pick", "owner:grinder"]
    out = {"at": "2026-10-06T20:00:00Z", "why": "nothing pickable"}
    records = [
        {"id": "g1", "name": "grinder-ao-1", "team": "ao-grind", "lane": grind, "state": "idle", "out_of_work": out},
        {"id": "g2", "name": "grinder-ao-2", "team": "ao-grind", "lane": grind, "state": "idle", "out_of_work": out},
        {"id": "d1", "name": "designer-ao-1", "team": "ao-grind", "lane": ["design-first", "owner:designer"]},
        {"id": "m", "name": "manager-ao-1", "team": "ao-grind", "state": "idle"},  # no lane: contributes nothing
    ]
    return entries, records


def test_in_lanes_splits_the_repos_count_by_the_teams_lanes():
    """§4.4 *In a team's lanes* (TD-357, TD-361) on the 2026-10-06 case: 0 pickable and 1 design
    in ao-grind's lanes, the rest by owner in falling count; a grinder entry filed since is in the
    lanes and in the warning, and out of it once a live member holds it; an unowned build is in a
    `free-pick` lane, and in the rest of a team whose only lane is a designer's."""
    from sessionorc import ledger

    entries, records = _oct6()
    got = ledger.in_lanes(entries, records)
    assert got["pickable"] == [] and got["design"] == ["TD-300"] and got["live_check"] == []
    assert [(r["owner"], r["n"]) for r in got["rest"]] == [("anchor", 20), ("dev-cadence", 4)]
    # TD-301 waits on its build: *blocked*, in no lane's count, and *design* keeps no rest (TD-418)
    assert "design_first_rest" not in got and got["out_of_work"] == []
    grinders = {"title": "g", "for_page": "pickable", "owner": "grinder", "kind": "build", "pickable": "yes"}
    entries += [{"id": f"TD-35{i}", **grinders} for i in (5, 8, 9)]
    got = ledger.in_lanes(entries, records, held={"TD-358"})
    assert got["pickable"] == ["TD-355", "TD-358", "TD-359"]  # a held entry is still the team's work
    assert [(m["name"], m["ids"]) for m in got["out_of_work"]] == [
        ("grinder-ao-1", ["TD-355", "TD-359"]),
        ("grinder-ao-2", ["TD-355", "TD-359"]),
    ]
    entries.append(
        {"id": "TD-500", "title": "u", "for_page": "pickable", "owner": "", "kind": "build", "pickable": "yes"}
    )
    assert "TD-500" in ledger.in_lanes(entries, records)["pickable"]
    designers = ledger.in_lanes(entries, records[2:])
    assert ("unowned", 1) in [(r["owner"], r["n"]) for r in designers["rest"]]
    assert ledger.in_lanes(entries, records[3:]) is None  # no lane on any record: nothing drawn
    assert ledger.in_lanes(entries, []) is None


def test_in_lanes_orders_the_rest_by_falling_count_and_reads_an_owner_in_any_case():
    """§4.4 *In a team's lanes* (TD-363): the rest is in falling count, then name — a larger owner
    comes first even when its name sorts last — and `Owner: Anchor` counts with `anchor`."""
    from sessionorc import ledger

    def e(i, owner):
        return {"id": i, "title": i, "for_page": "pickable", "owner": owner, "kind": "build", "pickable": "yes"}

    entries = [e("TD-1", "alpha"), e("TD-2", "zeta"), e("TD-3", "zeta"), e("TD-4", "Zeta"), e("TD-5", "mid")]
    records = [{"id": "g", "name": "g", "team": "t", "lane": ["free-pick", "owner:grinder"]}]
    got = ledger.in_lanes(entries, records)
    assert [(r["owner"], r["n"]) for r in got["rest"]] == [("zeta", 3), ("alpha", 1), ("mid", 1)]
    assert got["rest"][0]["ids"] == ["TD-2", "TD-3", "TD-4"]


def test_repo_lanes_reads_the_repos_own_records_and_a_claim_only_while_its_holder_lives(repo, tmp_path):
    """§4.4 *In a team's lanes* (TD-361, TD-363): a record of another repo is no team of this one,
    lane or not, and a claim held by an ended record holds nothing — the entry is unheld and the
    member out of work with it in its lane is warned; held by a live record, it is not."""
    from agentorc import teamrun

    other = tmp_path / "other"
    other.mkdir()
    grinder = {"title": "g", "for_page": "pickable", "owner": "grinder", "kind": "build", "pickable": "yes"}
    entries = [{"id": "TD-355", **grinder}]
    lane = ["free-pick", "owner:grinder"]
    out = {"at": "2026-10-06T20:00:00Z", "why": "nothing pickable"}
    claim = [{"ref": "TD-355", "status": "claimed"}]
    sessions = [
        {"id": "g1", "name": "grinder-ao-1", "team": "ao-grind", "repo": str(repo), "lane": lane, "state": "idle",
         "out_of_work": out},
        {"id": "g0", "name": "grinder-ao-0", "team": "ao-grind", "repo": str(repo), "lane": lane, "state": "closed",
         "progress": claim},
        {"id": "x", "name": "grinder-x-1", "team": "x-grind", "repo": str(other), "lane": lane, "state": "working"},
    ]  # fmt: skip
    got = teamrun.repo_lanes(entries, repo, sessions)
    assert list(got) == ["ao-grind"]  # the other repo's team is not this repo's
    assert [(m["name"], m["ids"]) for m in got["ao-grind"]["out_of_work"]] == [("grinder-ao-1", ["TD-355"])]
    sessions[1]["state"] = "working"
    assert teamrun.repo_lanes(entries, repo, sessions)["ao-grind"]["out_of_work"] == []  # a live holder holds it


def test_ao_repo_splits_its_first_line_by_the_teams_lanes_and_names_who_is_out_of_work(repo, monkeypatch, capsys):
    """§4.7 `ao repo` (TD-361): the first line's parentheses, one per servicing team; a member out of
    work with unheld work in its lane on a line of its own; `--json` carries `lanes` by team;
    `--all` reads no members and prints the counts alone."""
    from agentorc import cli

    entries, records = _oct6()
    entries.append(
        {"id": "TD-355", "title": "g", "for_page": "pickable", "owner": "grinder", "kind": "build", "pickable": "yes"}
    )
    by = {"pickable": 25, "design": 1, "for-you": 1, "other": 0}
    reading = {str(repo): {"name": "r", "root": str(repo), "prs": {"open": []},
                           "ledger": {"entries": entries, "by_kind": by}}}  # fmt: skip
    fleet = [{**r, "repo": str(repo)} for r in records]
    fleet.append({"id": "old", "name": "grinder-ao-1", "team": "ao-grind", "repo": str(repo), "lane": ["free-pick"],
                  "superseded_by": "g1", "state": "closed"})  # fmt: skip
    monkeypatch.setattr(
        cli, "call_sync", lambda rpc, **kw: {"repos": reading, "list": fleet, "doing_log": {}, "inbox": {}}[rpc]
    )
    monkeypatch.chdir(repo)
    assert cli.main(["repo"]) == 0
    out = capsys.readouterr().out
    first = out.splitlines()[0]
    assert "28 open entries: 25 pickable (ao-grind 1 · anchor 20, dev-cadence 4), 1 design (ao-grind 1)," in first
    assert "  grinder-ao-1 is out of work with 1 in its lane: TD-355" in out
    assert "  grinder-ao-2 is out of work with 1 in its lane: TD-355" in out
    fleet[0]["progress"] = [{"ref": "TD-355", "status": "claimed"}]
    fleet[0]["state"] = "working"
    assert cli.main(["--json", "repo"]) == 0
    lanes = json.loads(capsys.readouterr().out)[0]["lanes"]
    assert lanes["ao-grind"]["pickable"] == ["TD-355"] and lanes["ao-grind"]["out_of_work"] == []  # held now
    reading[str(repo)].pop("lanes")  # the fake hands back the dict the last call filled; the home's is fresh
    assert cli.main(["repo", "--all"]) == 0
    assert "(ao-grind" not in capsys.readouterr().out


def _own_counts():
    """A ledger and a team for each member's own count (TD-418, built by TD-428): grinder builds, one
    held; a live check whose build is live and one whose build is not; an anchor evaluation; a
    designer's design-first entry."""

    def e(i, owner, kind="build", **kw):
        return {"id": i, "title": i, "for_page": "pickable", "owner": owner, "kind": kind, "pickable": "yes", **kw}

    entries = [e("TD-355", "grinder"), e("TD-358", "grinder"), e("TD-360", "grinder", "live-check", live="yes")]
    entries += [e("TD-361", "grinder", "live-check", live="no"), e("TD-370", "anchor", "evaluation")]
    entries += [{**e("TD-300", "designer", "design-first"), "for_page": "design"}]
    grind = ["free-pick", "owner:grinder"]
    out = {"at": "2026-10-09T09:00:00Z", "why": "nothing pickable"}
    records = [
        {"id": "g1", "name": "grinder-ao-1", "team": "ao-grind", "lane": grind, "state": "idle"},
        {"id": "g2", "name": "grinder-ao-2", "team": "ao-grind", "lane": list(reversed(grind)), "state": "working"},
        {"id": "d1", "name": "designer-ao-1", "team": "ao-grind", "lane": ["design-first"], "state": "idle"},
        {"id": "a1", "name": "anchor-ao-1", "team": "ao-grind", "lane": ["anchor"], "state": "closed",
         "out_of_work": out},
        {"id": "m", "name": "manager-ao-1", "team": "ao-grind", "state": "idle"},
    ]  # fmt: skip
    return entries, records


def test_in_lanes_gives_each_member_its_own_count_and_reads_out_of_work_from_it():
    """§4.4 *A member's own count* (TD-418, built by TD-428): each record with a lane gets what
    `lane_matches` takes for its own lane that `held` does not hold — a held entry left out, a live
    check whose build is live counted by a `free-pick` lane and one not live by none, an `anchor` lane
    counting its pickable `Owner: anchor` evaluation, whatever the page kind — and `k`, the live records
    carrying the identical lane, word order aside; `out_of_work` is a declared member whose count is
    above 0, an ended anchor seat among them; no lane, no row."""
    from sessionorc import ledger

    entries, records = _own_counts()
    got = ledger.in_lanes(entries, records, held={"TD-358"})
    own = {m["id"]: (m["ids"], m["k"]) for m in got["members"]}
    assert own == {
        "g1": (["TD-355", "TD-360"], 2),  # shared by two live records: 2/2 on each card
        "g2": (["TD-355", "TD-360"], 2),
        "d1": (["TD-300"], 1),  # a lone lane
        "a1": (["TD-370"], 0),  # ended: counted for its declaration, never for `k`
    }
    assert [(m["name"], m["ids"]) for m in got["out_of_work"]] == [("anchor-ao-1", ["TD-370"])]
    records[0]["out_of_work"] = {"at": "2026-10-09T09:00:00Z", "why": "x"}
    got = ledger.in_lanes(entries, records, held={"TD-355", "TD-358", "TD-360"})
    assert [m["name"] for m in got["out_of_work"]] == ["anchor-ao-1"]  # its own count is 0 once all are held


def test_ao_repo_names_an_out_of_work_member_by_its_own_count(repo, monkeypatch, capsys):
    """§4.7 `ao repo` (TD-428): the *is out of work with n* line reads the per-member count — an
    out-of-work anchor seat with an unheld evaluation, which the pickable and design-first kinds alone
    never named, and a member that declared and is waiting on you, whatever its pill reads."""
    from agentorc import cli

    entries, records = _own_counts()
    records[1]["out_of_work"] = {"at": "2026-10-09T09:00:00Z", "why": "x"}
    records[1]["state"] = "idle"  # *waiting* on you on the page: the line names it all the same
    by = {"pickable": 5, "design": 1, "for-you": 0, "other": 0}
    reading = {str(repo): {"name": "r", "root": str(repo), "prs": {"open": []},
                           "ledger": {"entries": entries, "by_kind": by}}}  # fmt: skip
    fleet = [{**r, "repo": str(repo)} for r in records]
    monkeypatch.setattr(
        cli, "call_sync", lambda rpc, **kw: {"repos": reading, "list": fleet, "doing_log": {}, "inbox": {}}[rpc]
    )
    monkeypatch.chdir(repo)
    assert cli.main(["repo"]) == 0
    out = capsys.readouterr().out
    assert "  anchor-ao-1 is out of work with 1 in its lane: TD-370" in out
    assert "  grinder-ao-2 is out of work with 3 in its lane: TD-355, TD-358, TD-360" in out


async def test_the_repo_facts_read_live_as_unknown_until_the_promote_has_read_once(agent, repo, monkeypatch):
    """TD-516: `_refresh_repos` hands `_read_repos` no live commits (`None`, unknown) until the promote
    pass has assigned its readings since the start, and the promote's live commits after."""
    await park_ticks(agent)
    _register(repo)
    monkeypatch.setattr(reports, "pr_reading", lambda d, now, **kw: {"open": [], "at": now.isoformat()})
    handed = []
    real = agent._read_repos

    def spy(todo, prev, full, roots=None, live=None):
        handed.append(live)
        return real(todo, prev, full, roots, live)

    monkeypatch.setattr(agent, "_read_repos", spy)
    agent._promotes, agent._promotes_read = {"r": {"live": "abc"}}, False
    agent._repos_read_at = float("-inf")
    await agent._refresh_repos()
    agent._promotes_read, agent._repos_read_at = True, float("-inf")
    await agent._refresh_repos()
    assert handed == [None, {"r": "abc"}]
