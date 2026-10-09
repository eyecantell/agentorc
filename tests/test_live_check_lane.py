"""A live check is a grinder's once its build is live (design §4.9b, TD-323 slice 1): the build's PRs
on the `Kind:` line, the home's `live` reading of them against the promote's live commit, and
`free-pick` taking a live check that reads `live: yes`. Unknown is never live."""

from __future__ import annotations

import os
import subprocess

import pytest

from sessionorc import ledger
from sessionorc.ledger import LiveReader, built, entries, kind_of, lane_matches


def _git(root, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t"}
    env["GIT_COMMITTER_EMAIL"] = "t@t"
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=env).stdout


def _ledger(kind: str, owner: str = "grinder") -> str:
    return f"# L\n\n## TD-001: a check\n\n**Priority:** High\n**Owner:** {owner}\n**Kind:** {kind}\n**Status:** Built\n"


@pytest.mark.parametrize(
    "value, want",
    [
        ("live-check #1036", [1036]),
        ("live-check #1036 #1045", [1036, 1045]),
        ("live-check #1036, #1045", [1036, 1045]),
        ("live-check", []),
        ("live-check — the anchor's, after a promote", []),
        ("live-check #1036 then a word", []),
        ("live-check 1036", []),
        ("", []),
    ],
)
def test_the_kind_line_names_its_builds_after_the_word(value, want):
    assert built(value) == want


def test_a_live_check_reads_live_only_when_every_build_named_is_live():
    def live(n: int) -> bool:
        return n in (5, 6)

    def one(kind: str, owner: str = "grinder", with_live=live) -> dict:
        (e,) = entries(_ledger(kind, owner), live=with_live)
        return e

    yes = one("live-check #5 #6")
    assert yes["kind"] == "live-check" and yes["built"] == [5, 6] and yes["live"] == "yes"
    assert lane_matches(["free-pick"], yes) and kind_of(yes) == "live-check"  # its own kind (TD-418)
    assert lane_matches(["free-pick", "owner:grinder"], yes)
    assert not lane_matches(["free-pick", "owner:grinder"], one("live-check #5", owner="anchor")), "the owner narrows"
    assert not lane_matches(["design-first"], yes)
    for no in (
        one("live-check #5 #7"),  # one build not live
        one("live-check"),  # no build named
        one("live-check — after a promote"),  # prose, no build
        one("live-check #5", with_live=None),  # no reading
    ):
        assert no["live"] == "no" and not lane_matches(["free-pick"], no) and kind_of(no) == "live-check"
    (b,) = entries(_ledger("build"), live=live)
    assert "live" not in b and "built" not in b and lane_matches(["free-pick"], b), "a build is as it was"


def test_the_home_reads_a_build_live_by_its_squash_commit_against_the_live_commit(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    shas = {}
    for n in (5, 6):
        (tmp_path / f"f{n}").write_text(str(n))
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-qm", f"TD-00{n}: a change (#{n})")
        shas[n] = _git(tmp_path, "rev-parse", "HEAD").strip()
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    at_5 = LiveReader(tmp_path, shas[5])
    assert at_5(5) and not at_5(6), "a PR after the live commit is not live"
    assert not at_5(7), "a PR whose commit is not found"
    assert LiveReader(tmp_path, shas[6])(5) and LiveReader(tmp_path, shas[6])(6)
    assert not LiveReader(tmp_path, None)(5), "no live commit"
    assert not LiveReader(tmp_path, "0" * 40)(5), "a live commit git cannot read"
    assert not LiveReader(tmp_path / "nowhere", shas[6])(5), "no checkout"


def test_the_repo_facts_carry_live_from_the_promote_reading(tmp_path, monkeypatch):
    """`_read_repos` takes the live commits by repo name (`_live_commits`) and reads each checkout's
    live checks against its own; a repo with no promote reading reads every live check `no`."""
    from sessionorc.agent_tick import TickMixin

    root = tmp_path / "r"
    (root / "docs").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    (root / "docs" / "technical_debt.md").write_text(_ledger("live-check #5"))
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "the build (#5)")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    sha = _git(root, "rev-parse", "HEAD").strip()
    got = TickMixin._read_repos([str(root)], {}, set(), None, {"r": sha})
    assert got[str(root)]["ledger"]["entries"][0]["live"] == "yes"
    assert TickMixin._read_repos([str(root)], {}, set(), None, {})[str(root)]["ledger"]["entries"][0]["live"] == "no"

    class Home(TickMixin):
        _promotes = {
            "r": {"live": sha},
            "s": {"live": sha, "live_why": "the check could not be run"},
            "t": {"live": None, "live_why": "no live commit"},
        }

    assert Home()._live_commits() == {"r": sha}, "a reading that carries live_why names no live commit"
    assert ledger.lane_matches(["free-pick"], got[str(root)]["ledger"]["entries"][0])
