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


def test_ao_pr_held_reads_a_flows_chain(monkeypatch, capsys):
    # TD-315 slice 3: the compile writes a chain, and `ao pr held` reads it — each link that holds
    # the PR, in the flow's order; whose turn it is, is slice 4's
    chain = {
        "chain": [
            {"stage": "ui-review", "reader": "ui-reader-ao", "held": ["src/agentorc/ui/**"]},
            {"stage": "review", "reader": "techlead-ao", "held": ["src/sessionorc/**"]},
        ],
        "bound": "2h",
    }
    got = reviewmod.setting(chain)
    assert got["held"] == ["src/agentorc/ui/**", "src/sessionorc/**"] and got["chain"] == chain["chain"]
    _world(monkeypatch, chain, ["src/agentorc/ui/app.py", "src/sessionorc/agent.py"])
    assert cli.main(["pr", "held", "12"]) == 0
    out = capsys.readouterr().out
    assert "is held by 2 readers, in order (bound 2h)" in out
    assert "  ui-review · ui-reader-ao · src/agentorc/ui/app.py · your turn to ask" in out
    assert "  review · techlead-ao · src/sessionorc/agent.py · later — it merges" in out
    _world(monkeypatch, chain, ["src/sessionorc/agent.py"])
    assert cli.main(["pr", "held", "12"]) == 0
    out = capsys.readouterr().out
    assert "held by 1 reader" in out and "your turn to ask — it merges" in out and "ui-reader" not in out


def test_the_walk_says_whose_turn_it_is(monkeypatch, capsys):
    # §4.9c *Whose turn it is* (TD-315 slice 4): each link's state from the author's asks, the
    # first not passed its turn, the last its merger; a reader asks with `--id <the asker>`
    links = [
        {"stage": "ui-review", "reader": "ui-reader-ao", "paths": ["src/agentorc/ui/app.py"]},
        {"stage": "review", "reader": "techlead-ao", "paths": ["src/sessionorc/agent.py"]},
    ]

    def ask(to, verdict=None, at="2026-10-05T13:40:00Z", replied="2026-10-05T14:02:00Z"):
        return {"id": "m", "to": [to], "at": at, "open": verdict is None, "verdict": verdict,
                "replied_at": replied if verdict else None}  # fmt: skip

    def states(asks):
        got = reviewmod.walk(links, asks)
        return [r["state"] for r in got["chain"]], got["turn"], got["merges"]

    assert states([]) == (["next", "later"], "ui-reader-ao", "techlead-ao")
    assert states([ask("ao-agentorc-ui-reader-ao")]) == (["asked", "later"], "ui-reader-ao", "techlead-ao")
    assert states([ask("ao-agentorc-ui-reader-ao", "findings")])[0] == ["findings", "later"]
    passed = ask("ao-agentorc-ui-reader-ao@node1", "pass")
    assert states([passed]) == (["passed", "next"], "techlead-ao", "techlead-ao")
    assert states([passed, ask("ao-agentorc-techlead-ao", "merged")]) == (["passed", "passed"], None, "techlead-ao")
    # an ask asked again after findings: the newest counts
    again = [ask("ao-agentorc-ui-reader-ao", "findings"), ask("ao-agentorc-ui-reader-ao")]
    assert states(again)[0] == ["asked", "later"]
    # a home that did not say: unknown, never a guess
    assert states(None)[0] == ["unknown", "unknown"]
    assert reviewmod.addressed("ao-agentorc-other-ui-reader-aox", ["ui-reader-ao"]) is None
    # a seat name that ends another's is never taken for it (the review of slice 4)
    assert reviewmod.addressed("ao-agentorc-ui-reader", ["reader", "ui-reader"]) == "ui-reader"
    assert reviewmod.addressed("ao-agentorc-reader@node1", ["reader", "ui-reader"]) == "reader"
    # a PR one link holds: an ask to another reader is not its read
    one = [links[1]]
    assert reviewmod.walk(one, [passed])["chain"][0]["state"] == "next"
    assert reviewmod.walk(one, [passed], older=True)["chain"][0]["state"] == "passed"  # the older shape: any ask
    assert reviewmod.walk(links, None)["turn"] is None
    # the command, with the home's reads
    chain = {"chain": [{"stage": x["stage"], "reader": x["reader"], "held": x["paths"]} for x in links], "bound": "2h"}
    _world(monkeypatch, chain, ["src/agentorc/ui/app.py", "src/sessionorc/agent.py"])
    base = cli.call_sync
    monkeypatch.setattr(cli, "call_sync", lambda m, **kw: {"asks": [passed]} if m == "pr_reads" else base(m, **kw))
    assert cli.main(["--json", "pr", "held", "12"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["turn"] == "techlead-ao" and got["merges"] == "techlead-ao" and got["unread"] == ""
    assert [r["state"] for r in got["chain"]] == ["passed", "next"]
    assert cli.main(["pr", "held", "12"]) == 0
    out = capsys.readouterr().out
    assert "ui-reader-ao · src/agentorc/ui/app.py · passed 14:02 UTC" in out and "your turn to ask — it merges" in out


# -- the page half (design §4.5a *PRs waiting*, TD-093 slice 3) --------------------------------------


def test_the_standing_names_each_reader_and_says_its_verdict():
    """TD-315 slice 5b (design §4.9c *What is shown*): the PR standing over every seat — each seat's
    latest ask for a number, by its reply's verdict, in the order the asks were sent."""

    def ask(pr, at, **kw):
        return {"kind": "ask", "pr": pr, "at": f"2026-10-05T{at}:00+00:00", **kw}

    ui = (
        "ui-reader-ao-1",
        [ask(7, "10:00", closed_by="r1"), ask(8, "10:00", closed_by="r2"), {"kind": "note", "pr": 9}],
        [{"id": "r1", "kind": "reply", "verdict": "pass"}, {"id": "r2", "kind": "reply", "verdict": "pass"}],
    )
    tl = (
        "techlead-ao-1",
        [
            ask(7, "11:00"),
            ask(8, "11:00", closed_by="r3"),
            ask(8, "12:00", closed_by="r4"),  # asked again on findings: the latest ask wins
            ask(10, "11:00", closed_by="r5"),
            ask(11, "11:00", closed_by="p1"),  # the person's word past the bound: no verdict
            ask(12, "11:00", closed_reason="expired"),  # closed unanswered: stands for nothing
            ask(10, "10:00", closed_by="r5"),
            ask(13, "10:00", closed_by="r5"),
            ask(13, "11:00", closed_reason="expired"),  # the latest closed unanswered: the earlier word goes
        ],
        [
            {"id": "r3", "kind": "reply", "verdict": "findings"},
            {"id": "r4", "kind": "reply", "verdict": "merged"},
            {"id": "r5", "kind": "reply", "verdict": "findings"},
        ],
    )
    got = reviewmod.standing([tl, ui], lambda at: "40m")
    assert got[7] == {"word": "passed by ui-reader-ao-1 · waiting on techlead-ao-1 · 40m", "cls": "wait"}
    assert got[8] == {"word": "passed by ui-reader-ao-1 · merged by techlead-ao-1", "cls": "done"}
    assert got[10] == {"word": "findings from techlead-ao-1", "cls": "wait"}
    assert got[11] == {"word": "reviewed by techlead-ao-1", "cls": "done"}
    assert 9 not in got and 12 not in got and 13 not in got


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
    seat = {"role": "techlead", "prs_waiting": {"n": 2, "oldest": "2026-09-23T10:30:00Z"}}
    assert prs_waiting([seat, {"prs_waiting": None}, {}], now) == {"n": 2, "age": "1h 30m", "by": "techlead"}
    assert prs_waiting([{"prs_waiting": None}, {}], now) is None  # nothing waits, or an older agent
    two = [seat, {"name": "x", "prs_waiting": {"n": 1, "oldest": "2026-09-23T09:00:00Z"}}]
    # the sum, the oldest across records, and each seat named by its role (its name with none; TD-428)
    assert prs_waiting(two, now) == {"n": 3, "age": "3h 0m", "by": "techlead and the x"}

    def v(sid, **kw):
        return {"id": sid, "name": sid, "team": "t", "state": "idle", "state_class": "idle", "state_label": "idle",
                "scraped": False, "rank": 5, "controllers": [], "capabilities": [], **kw}  # fmt: skip

    (team,) = team_groups([v("w"), v("tl", role="techlead", prs_waiting={"n": 1, "oldest": "2026-09-23T10:30:00Z"})])
    head = templates.get_template("group_head.html").render(g=team)
    assert "1 PR waiting · oldest" in head
    assert 'title="held PRs waiting for the techlead (design §4.9b)' in head  # the seat by its role (TD-428)
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


def test_the_inbox_mark_of_a_chain_names_its_readers():
    # review of TD-315 slice 3: a chain has no top-level `reader`, so the mark says *its readers'*
    from agentorc.ui.inbox import held_mark
    from sessionorc import held

    v = {"id": "w", "dir": "/nowhere", "held_missed": [held.crossing(n, ["src/a.py"], "t") for n in (845, 846)]}
    texts = {}
    for name, review in (("old", {"reader": "techlead", "held": ["src/**"]}), ("chain", {"chain": [], "bound": "2h"})):
        mark = held_mark({**v, "review": review})
        assert mark is not None, name
        texts[name] = json.dumps(mark["parts"])
    assert "the techlead's read" in texts["old"] and "its readers' read" in texts["chain"]
