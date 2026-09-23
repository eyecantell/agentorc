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
