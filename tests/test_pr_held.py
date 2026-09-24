"""`ao pr held <n>` (design §4.9b *The reader*, TD-093 slice 2): the author's own `ao` checks a
PR's changed files against its record's `review.held` — the host agent only stores the setting."""

from __future__ import annotations

import json
import subprocess

import pytest

from agentorc import cli
from agentorc import review as reviewmod


def test_held_globs_read_as_written():
    assert reviewmod.matches("src/sessionorc/agent.py", "**")
    assert reviewmod.matches("src/sessionorc/agent.py", "src/**")
    assert reviewmod.matches("src/sessionorc/agent.py", "src/sessionorc/**")
    assert not reviewmod.matches("src/agentorc/cli.py", "src/sessionorc/**")
    assert reviewmod.matches("org.yml", "org.yml") and not reviewmod.matches("docs/org.yml", "org.yml")
    assert reviewmod.matches("docs/briefs/a.md", "docs/briefs/*.md")
    assert not reviewmod.matches("docs/briefs/archive/a.md", "docs/briefs/*.md")  # `*` stays in one directory
    assert reviewmod.matches("a/b/c/x.py", "**/x.py") and reviewmod.matches("x.py", "**/x.py")
    assert reviewmod.matches("docs/design.md", "/docs/design.md")  # a leading slash is the repo root
    assert reviewmod.matches("src/a/b.py", "src/") and reviewmod.matches("src/a/b.py", "src/**/")
    assert not reviewmod.matches("docs/a.md", "docs/[abc].md")  # brackets are literal


def test_held_paths_are_nothing_without_a_review():
    files = ["src/sessionorc/agent.py", "docs/design.md"]
    assert reviewmod.held_paths(files, None) == []
    assert reviewmod.held_paths(files, {"reader": "techlead", "held": ["src/sessionorc/**"], "bound": "2h"}) == [
        "src/sessionorc/agent.py"
    ]
    assert reviewmod.held_paths(files, {"reader": "techlead"}) == files  # `held` defaults to every PR


def test_the_setting_is_filled_in_whatever_wrote_it():
    assert reviewmod.setting(None) is None
    assert reviewmod.setting({"reader": "techlead"}) == {"reader": "techlead", "held": ["**"], "bound": "2h"}
    with pytest.raises(ValueError, match="held"):
        reviewmod.setting({"reader": "techlead", "held": []})


def test_a_page_of_a_large_prs_files_is_never_judged(monkeypatch):
    body = json.dumps({"files": [{"path": "docs/a.md"}], "changedFiles": 140})
    monkeypatch.setattr(reviewmod.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, body, ""))
    with pytest.raises(RuntimeError, match="1 of PR #7's 140 files"):
        reviewmod.pr_files(7)


def test_a_failed_read_is_said_never_taken_for_not_held(monkeypatch):
    def gh(argv, **_kw):
        return subprocess.CompletedProcess(argv, 1, "", "no pull requests found for 99\n")

    monkeypatch.setattr(reviewmod.subprocess, "run", gh)
    with pytest.raises(RuntimeError, match="no pull requests found"):
        reviewmod.pr_files(99)


def _world(monkeypatch, review, files):
    monkeypatch.setenv("AGENTORC_SESSION", "ao-r-w")
    monkeypatch.setattr(cli, "call_sync", lambda method, **kw: {"id": kw["id"], "dir": "/r", "review": review})
    body = json.dumps({"files": [{"path": f} for f in files]})
    monkeypatch.setattr(reviewmod.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, body, ""))


def test_ao_pr_held_answers_from_the_record_and_the_files(monkeypatch, capsys):
    _world(monkeypatch, {"reader": "techlead", "held": ["src/sessionorc/**"], "bound": "2h"}, ["src/sessionorc/x.py"])
    assert cli.main(["pr", "held", "12", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["held"] is True and got["paths"] == ["src/sessionorc/x.py"] and got["id"] == "ao-r-w"
    assert cli.main(["pr", "held", "12"]) == 0
    assert "held for the techlead (bound 2h)" in capsys.readouterr().out
    _world(monkeypatch, {"reader": "techlead"}, ["src/x.py"])  # the design's own minimal setting
    assert cli.main(["pr", "held", "12"]) == 0
    assert "held for the techlead (bound 2h)" in capsys.readouterr().out
    _world(monkeypatch, {"reader": "techlead", "held": ["src/sessionorc/**"], "bound": "2h"}, ["docs/a.md"])
    assert cli.main(["pr", "held", "12", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["held"] is False
    _world(monkeypatch, None, ["src/sessionorc/x.py"])  # no review: never held, and gh is not asked
    assert cli.main(["pr", "held", "12"]) == 0
    assert "has no review on its record" in capsys.readouterr().out


# -- the page half (design §4.5a *PRs waiting*, TD-093 slice 3) --------------------------------------


def test_a_github_origin_makes_a_pr_link_and_anything_else_draws_it_bare(monkeypatch):
    remotes = {
        "/a": "git@github.com:eyecantell/agentorc.git\n",
        "/b": "https://github.com/eyecantell/samscrape\n",
        "/c": "https://gitlab.com/x/y.git\n",
        "/d": "https://x-access-token@github.com/eyecantell/agentorc.git\n",  # a CI-style clone
    }

    def git(argv, **_kw):
        d = argv[2]
        return subprocess.CompletedProcess(argv, 0 if d in remotes else 2, remotes.get(d, ""), "")

    reviewmod._github.cache_clear()
    monkeypatch.setattr(reviewmod.subprocess, "run", git)
    assert reviewmod.pr_url("/a", 12) == "https://github.com/eyecantell/agentorc/pull/12"
    assert reviewmod.pr_url("/b", 3) == "https://github.com/eyecantell/samscrape/pull/3"
    assert reviewmod.pr_url("/d", 7) == "https://github.com/eyecantell/agentorc/pull/7"
    assert reviewmod.pr_url("/c", 3) == "" and reviewmod.pr_url("/nope", 3) == "" and reviewmod.pr_url(None, 3) == ""
    reviewmod._github.cache_clear()


def test_the_team_header_counts_prs_waiting_and_an_ask_row_draws_its_pr():
    from datetime import UTC, datetime

    from agentorc.ui.app import prs_waiting, team_groups, templates

    now = datetime(2026, 9, 23, 12, tzinfo=UTC)
    seat = {"prs_waiting": {"n": 2, "oldest": "2026-09-23T10:30:00Z"}}
    assert prs_waiting([seat, {"prs_waiting": None}, {}], now) == {"n": 2, "age": "1h 30m"}
    assert prs_waiting([{"prs_waiting": None}, {}], now) is None  # nothing waits, or an older agent
    two = [seat, {"prs_waiting": {"n": 1, "oldest": "2026-09-23T09:00:00Z"}}]
    assert prs_waiting(two, now) == {"n": 3, "age": "3h 0m"}  # the sum, and the oldest across records

    def v(sid, **kw):
        return {"id": sid, "name": sid, "team": "t", "state": "idle", "state_class": "idle", "state_label": "idle",
                "scraped": False, "rank": 5, "controllers": [], "capabilities": [], **kw}  # fmt: skip

    (team,) = team_groups([v("w"), v("tl", prs_waiting={"n": 1, "oldest": "2026-09-23T10:30:00Z"})])
    head = templates.get_template("group_head.html").render(g=team)
    assert "1 PR waiting · oldest" in head
    (quiet,) = team_groups([v("w")])
    assert "PR waiting" not in templates.get_template("group_head.html").render(g=quiet)

    e = {"id": "m-1", "from": "ao-w", "from_name": "w", "from_open": "ao-w", "from_role": "other", "to": ["person"],
         "at": "2026-09-23T10:00:00Z", "age": "2h", "kind": "ask", "text": "held: the doorbell", "about": None,
         "read_at": None, "reply_to": None, "team": "t", "pr": 12,
         "pr_url": "https://github.com/eyecantell/agentorc/pull/12"}  # fmt: skip
    html = templates.get_template("inbox_rows.html").render(rows=[e], section="needs")
    assert 'href="https://github.com/eyecantell/agentorc/pull/12"' in html and ">#12</a>" in html
    html = templates.get_template("inbox_rows.html").render(rows=[{**e, "pr_url": ""}], section="needs")
    assert ">#12</span>" in html and "/pull/12" not in html
