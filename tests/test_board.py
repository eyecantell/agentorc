"""Board write-back (design §4.4, TD-069 step 3): Snooze and Done on one board item, committed in
the repo's main checkout with the fixed message — and refused, touching nothing, whenever the
checkout is not in a state to take that commit."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sessionorc import board
from sessionorc.client import AgentError, LocalClient

ITEM = (
    "2026-09-20 (session `grinder-ao-1` on kmaster) — **Merged, live check pending: the doorbell.** Look. "
    "Due: 2026-09-22."
)
BOARD_TEXT = f"# User attention\n\nFormat: `- [ ] …`\n\n## Needs the user\n\n- [ ] {ITEM}\n- [ ] n/a — undated.\n"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, forge) -> Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / board.BOARD).write_text(BOARD_TEXT)
    (root / "other.txt").write_text("x\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    forge(root)
    return root


def on_origin(root: Path, *args: str) -> str:
    """What origin's `main` holds after a landed edit (§4.4): fetched, then read."""
    git(root, "fetch", "-q", "origin")
    return git(root, *args)


def origin_board(root: Path) -> str:
    return on_origin(root, "show", f"origin/main:{board.BOARD.as_posix()}") + "\n"


def test_a_line_is_done_or_snoozed_only_while_it_holds_the_item():
    line = f"- [ ] {ITEM}\n"
    assert board.edit_line(line, ITEM, "done") == f"- [x] {ITEM}\n"
    assert board.edit_line(line, ITEM, "snooze", "2026-10-01") == f"- [ ] {ITEM.replace('2026-09-22', '2026-10-01')}\n"
    # an item with no date gains one
    assert board.edit_line("- [ ] n/a — undated.", "n/a — undated.", "snooze", "2026-10-01") == (
        "- [ ] n/a — undated. Due: 2026-10-01."
    )
    for bad in (
        lambda: board.edit_line("- [ ] something else\n", ITEM, "done"),  # another item moved onto the line
        lambda: board.edit_line(f"- [x] {ITEM}\n", ITEM, "done"),  # already done
        lambda: board.edit_line(line, ITEM, "snooze", "next week"),
        lambda: board.edit_line(line, ITEM, "delete"),
    ):
        with pytest.raises(board.Refused):
            bad()


def test_the_message_names_the_item_and_the_session_that_raised_it():
    assert board.message(ITEM, "snooze", "2026-10-01") == (
        "agentorc: snooze Merged, live check pending: the doorbell. to 2026-10-01 (session grinder-ao-1)"
    )
    assert board.message("n/a — undated.", "done") == "agentorc: done n/a — undated. (session n/a)"


def test_write_back_lands_the_one_line_on_origin_and_leaves_the_checkout_alone(repo):
    """TD-264 (design §4.4): the edit is made in the host agent's own tree at origin's head and lands
    there by a PR it opens and squash-merges — one file, the fixed message as its title — and the
    person's checkout, its own edits included, is never written."""
    (repo / "other.txt").write_text("the anchor's own edit, uncommitted\n")
    head = git(repo, "rev-parse", "HEAD")
    got = board.write_back(repo, 7, ITEM, "snooze", "2026-10-01")
    assert (
        got["pr"] == 1 and got["line"] == 7 and got["commit"] == on_origin(repo, "rev-parse", "--short", "origin/main")
    )
    assert on_origin(repo, "log", "-1", "--format=%s", "origin/main") == f"{got['message']} (#1)"
    assert on_origin(repo, "show", "--name-only", "--format=", "origin/main") == str(board.BOARD)
    assert "Due: 2026-10-01." in origin_board(repo)
    assert (repo / board.BOARD).read_text() == BOARD_TEXT and git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") == "M other.txt"  # someone else's edit, untouched
    assert "board/" not in git(repo, "ls-remote", "--heads", "origin")  # the PR's branch is deleted
    board.write_back(repo, 8, "n/a — undated.", "done")
    assert "- [x] n/a — undated." in origin_board(repo)
    assert board.tree_dir(repo).exists()  # kept between presses


@pytest.mark.parametrize("state", ["branch", "dirty", "rebase"])
def test_the_checkouts_state_refuses_nothing(repo, state):
    """§4.4: the checkout's branch, a dirty board or a rebase under way refuses nothing now."""
    if state == "branch":
        git(repo, "checkout", "-q", "-b", "td-feature")
    elif state == "dirty":
        (repo / board.BOARD).write_text(BOARD_TEXT + "- [ ] a line being written. Due: 2026-09-30.\n")
    elif state == "rebase":
        (Path(git(repo, "rev-parse", "--absolute-git-dir")) / "rebase-merge").mkdir()
    before = (repo / board.BOARD).read_text()
    board.write_back(repo, 7, ITEM, "done")
    assert f"- [x] {ITEM}" in origin_board(repo) and (repo / board.BOARD).read_text() == before


def test_a_line_moved_on_origin_is_found_by_its_text_and_a_line_only_the_checkout_holds_is_named(repo):
    """§4.4: the reader's line is a hint — origin's item is found by its text wherever it sits; a
    line the checkout alone holds is *not on origin yet*; an item origin no longer holds open is
    the moved-line refusal. Nothing lands for either refusal."""
    (repo / board.BOARD).write_text(BOARD_TEXT.replace("## Needs the user\n\n", "## Needs the user\n\n- [ ] new.\n"))
    git(repo, "commit", "-qam", "a line above")
    git(repo, "push", "-q", "origin", "main")
    got = board.write_back(repo, 7, ITEM, "done")  # the reader read line 7; origin's item is on 8
    assert got["line"] == 8 and f"- [x] {ITEM}" in origin_board(repo)
    before = on_origin(repo, "rev-parse", "origin/main")
    with pytest.raises(board.Refused, match="no longer holds this item"):
        board.write_back(repo, 8, ITEM, "done")  # done on origin; the checkout, behind, still shows it
    (repo / board.BOARD).write_text(BOARD_TEXT + "- [ ] unpushed. Due: 2026-09-30.\n")
    with pytest.raises(board.Refused, match="not on origin yet: push the checkout first"):
        board.write_back(repo, 9, "unpushed. Due: 2026-09-30.", "done")  # uncommitted
    git(repo, "commit", "-qam", "unpushed")
    with pytest.raises(board.Refused, match="not on origin yet: push the checkout first"):
        board.write_back(repo, 9, "unpushed. Due: 2026-09-30.", "done")  # committed, not pushed
    assert on_origin(repo, "rev-parse", "origin/main") == before


def test_origin_out_of_reach_refuses_the_press_and_lands_nothing(repo, monkeypatch, tmp_path):
    """§4.4: no `gh`, a PR the forge will not open, an origin that cannot be fetched — each is
    *origin could not be reached (<why>): the edit was not made*, the tree reset, no branch left."""
    before = on_origin(repo, "rev-parse", "origin/main")
    monkeypatch.setenv("FAKE_GH_FAIL", "create")
    with pytest.raises(board.Refused, match=r"origin could not be reached \(gh pr create: .*\): the edit was not made"):
        board.write_back(repo, 7, ITEM, "done")
    assert "board/" not in git(repo, "ls-remote", "--heads", "origin")
    assert git(board.tree_dir(repo), "status", "--porcelain") == ""
    assert git(board.tree_dir(repo), "rev-parse", "HEAD") == before
    monkeypatch.delenv("FAKE_GH_FAIL")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # no gh at all
    if __import__("shutil").which("gh") is None:
        with pytest.raises(board.Refused, match="gh is not installed"):
            board.write_back(repo, 7, ITEM, "done")
    monkeypatch.undo()
    git(repo, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    with pytest.raises(board.Refused, match=r"origin could not be reached \(git fetch: "):
        board.write_back(repo, 7, ITEM, "done")


def test_a_merge_the_forge_refuses_closes_the_pr_and_names_a_moved_line(repo, monkeypatch, forge):
    """§4.4: origin moving elsewhere between fetch and merge refuses nothing (the squash lands); a
    refused merge closes the PR and deletes its branch, and is the moved-line refusal when origin
    no longer holds the item, else unreachable."""
    import json

    monkeypatch.setenv("FAKE_GH_FAIL", "merge")
    with pytest.raises(board.Refused, match=r"origin could not be reached \(gh pr merge: "):
        board.write_back(repo, 7, ITEM, "done")
    bare = Path(git(repo, "remote", "get-url", "origin"))
    assert json.loads((bare / "fake-gh.json").read_text())["prs"]["1"]["state"] == "closed"
    assert "board/" not in git(repo, "ls-remote", "--heads", "origin")
    # the line itself moved on origin while the press was out: the merge fails, and says so
    real = board._land

    def land(root, t, want, msg, clock, still):
        other = Path(str(t) + "-other")
        git(repo, "clone", "-q", str(bare), str(other))
        b = other / board.BOARD
        b.write_text(b.read_text().replace(f"- [ ] {ITEM}", f"- [x] {ITEM}"))
        git(other, "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qam", "done elsewhere")
        git(other, "push", "-q", "origin", "main")
        return real(root, t, want, msg, clock, still)

    monkeypatch.setattr(board, "_land", land)
    with pytest.raises(board.Refused, match="no longer holds this item"):
        board.write_back(repo, 7, ITEM, "snooze", "2026-10-09")
    # origin moved elsewhere in between — another file — refuses nothing: the squash lands on it
    monkeypatch.delenv("FAKE_GH_FAIL")

    def land_elsewhere(root, t, want, msg, clock, still):
        other = Path(str(t) + "-third")
        git(repo, "clone", "-q", str(bare), str(other))
        (other / "other.txt").write_text("moved on\n")
        git(other, "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qam", "elsewhere")
        git(other, "push", "-q", "origin", "main")
        return real(root, t, want, msg, clock, still)

    monkeypatch.setattr(board, "_land", land_elsewhere)
    git(repo, "fetch", "-q", "origin")
    board.write_back(repo, 8, "n/a — undated.", "done")
    assert (
        "- [x] n/a — undated." in origin_board(repo) and on_origin(repo, "show", "origin/main:other.txt") == "moved on"
    )


def test_a_failed_commit_leaves_the_board_as_it_was(repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'no commits today' >&2\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(board.Refused, match="no commits today"):
        board.write_back(repo, 7, ITEM, "done")
    assert origin_board(repo) == BOARD_TEXT and git(board.tree_dir(repo), "status", "--porcelain") == ""


def test_a_commit_that_does_not_finish_leaves_the_board_as_it_was(repo, monkeypatch):
    """Review of PR #474: a timeout raised past the returncode check and left the edit uncommitted."""
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nsleep 5\n")
    hook.chmod(0o755)
    monkeypatch.setattr(board, "GIT_TIMEOUT", 0.5)
    with pytest.raises(board.Refused, match="did not finish"):
        board.write_back(repo, 7, ITEM, "done")
    assert origin_board(repo) == BOARD_TEXT


def test_two_edits_at_once_are_made_one_after_the_other(repo):
    """Review of PR #474: two interleaved read-write-commits left *done* in the log and undone on disk."""
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        a = pool.submit(board.write_back, repo, 7, ITEM, "done")
        b = pool.submit(board.write_back, repo, 8, "n/a — undated.", "done")
        a.result(), b.result()
    text = origin_board(repo)
    assert f"- [x] {ITEM}" in text and "- [x] n/a — undated." in text


async def test_board_edit_is_the_persons_and_only_on_a_known_board(agent, repo, tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "repos.txt").write_text(f"{repo}\n")
    (home / "hosts.yml").write_text(f"local:\n  repos_registry: {home / 'repos.txt'}\n")
    path = str(repo / board.BOARD)
    async with LocalClient(caller="ao-some-worker") as worker:
        with pytest.raises(AgentError, match="the person's own"):
            await worker.call("board_edit", board=path, line=7, text=ITEM, action="done")
    async with LocalClient() as me:
        with pytest.raises(AgentError, match="not the board of a repo this host knows"):
            await me.call("board_edit", board=str(tmp_path / "elsewhere.md"), line=7, text=ITEM, action="done")
        with pytest.raises(AgentError, match="no longer holds this item"):
            await me.call("board_edit", board=path, line=7, text="something else", action="done")
        got = await me.call("board_edit", board=path, line=7, text=ITEM, action="snooze", due="2026-10-01")
    assert got["action"] == "snooze" and got["message"].startswith("agentorc: snooze Merged")
    assert on_origin(repo, "log", "-1", "--format=%s", "origin/main") == f"{got['message']} (#{got['pr']})"


def test_put_on_the_board_writes_one_line_at_the_top_of_the_items_and_commits_it(repo):
    """TD-140 (design §4.4, §4.5a): the one add — a line in the board's own format above the first
    item, committed with the message naming the entry, nothing else in the file touched."""
    got = board.add(
        repo, "Check the fetcher Friday.", "2026-10-02", entry="m-abc", session="grinder-ao-2", host="kmaster",
        context="TD-900", today="2026-09-25",
    )  # fmt: skip
    line = (
        "- [ ] 2026-09-25 (session `grinder-ao-2` on kmaster) — Check the fetcher Friday. Context: TD-900. "
        "Due: 2026-10-02.\n"
    )
    text = origin_board(repo)
    assert text == BOARD_TEXT.replace(f"- [ ] {ITEM}", line.rstrip("\n") + f"\n- [ ] {ITEM}")
    assert got["line"] == 7 and got["message"] == "agentorc: board Check the fetcher Friday (from m-abc)"
    assert on_origin(repo, "log", "-1", "--format=%s", "origin/main") == f"{got['message']} (#{got['pr']})"
    assert (repo / board.BOARD).read_text() == BOARD_TEXT  # the checkout catches up by the pull
    # no sender, no about: `n/a` and `none`
    assert board.item_line("x", "2026-10-02", "2026-09-25", None, None, None) == (
        "- [ ] 2026-09-25 (n/a) — x. Context: none. Due: 2026-10-02.\n"
    )
    for bad in (("", "2026-10-02"), ("two\nlines", "2026-10-02"), ("x", "Friday")):
        with pytest.raises(board.Refused):
            board.add(repo, *bad, entry="m-abc")
    assert on_origin(repo, "rev-list", "--count", "origin/main") == "2"


def test_the_add_on_a_board_with_no_items_goes_under_its_heading(repo):
    (repo / board.BOARD).write_text("# User attention\n\n## Needs the user\n\n## Later\n")
    git(repo, "commit", "-qam", "empty")
    git(repo, "push", "-q", "origin", "main")
    board.add(repo, "First", "2026-10-02", entry="m-1", today="2026-09-25")
    assert origin_board(repo).splitlines()[4] == ("- [ ] 2026-09-25 (n/a) — First. Context: none. Due: 2026-10-02.")


def test_the_add_is_refused_touching_nothing_where_an_edit_is(repo, monkeypatch):
    git(repo, "checkout", "-q", "-b", "feature")  # refuses nothing now (§4.4)
    board.add(repo, "x", "2026-10-02", entry="m-1")
    before = on_origin(repo, "rev-parse", "origin/main")
    monkeypatch.setenv("FAKE_GH_FAIL", "merge")
    with pytest.raises(board.Refused, match="origin could not be reached"):
        board.add(repo, "y", "2026-10-02", entry="m-2")
    assert on_origin(repo, "rev-parse", "origin/main") == before


async def test_put_on_the_board_is_the_persons_from_an_fyi_row_and_dismisses_it(agent, repo, tmp_path, monkeypatch):
    """TD-140: `board_edit` with `action: add` takes an FYI row — a `note` here — writes the line
    naming its sender and `about`, commits, then dismisses the row; an open question is refused, as
    is an entry the person inbox does not hold, a session, and a refused commit, which leaves the
    row where it was."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "repos.txt").write_text(f"{repo}\n")
    (home / "hosts.yml").write_text(f"local:\n  repos_registry: {home / 'repos.txt'}\n")
    path = str(repo / board.BOARD)
    async with LocalClient() as me:
        w = (await me.call("create", name="w", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        async with LocalClient(caller=w) as worker:
            note = (await worker.call("msg", to="person", text="check this Friday", about="TD-900"))["entry"]
            asked = (await worker.call("msg", to="person", text="which?", kind="ask"))["entry"]
            with pytest.raises(AgentError, match="the person's own"):
                await worker.call("board_edit", board=path, action="add", text="x", due="2026-10-02", entry=note["id"])
        with pytest.raises(AgentError, match="open ask"):
            await me.call("board_edit", board=path, action="add", text="x", due="2026-10-02", entry=asked["id"])
        with pytest.raises(AgentError, match="holds no entry m-nope"):
            await me.call("board_edit", board=path, action="add", text="x", due="2026-10-02", entry="m-nope")
        with monkeypatch.context() as m:  # a refused landing: the row stays
            m.setenv("FAKE_GH_FAIL", "create")
            with pytest.raises(AgentError, match="origin could not be reached"):
                await me.call("board_edit", board=path, action="add", text="x", due="2026-10-02", entry=note["id"])
        assert note["id"] in [e["id"] for e in (await me.call("inbox"))["entries"]]
        got = await me.call(
            "board_edit", board=path, action="add", text="Check the fetcher", due="2026-10-02", entry=note["id"]
        )
        assert got["dismissed"] == [note["id"]] and got["message"].endswith(f"(from {note['id']})")
        line = origin_board(repo).splitlines()[got["line"] - 1]
        assert line.startswith("- [ ] ") and "(session `w` on " in line and "Context: TD-900. Due: 2026-10-02." in line
        assert note["id"] not in [e["id"] for e in (await me.call("inbox"))["entries"]]
        await me.call("kill", id=w)


def test_the_add_lands_in_needs_the_user_never_above_another_sections_items(repo):
    """Review of PR #578: with *Needs the user* empty and a parked in-flight item below it, the
    line goes under *Needs the user*, not above the parked item."""
    (repo / board.BOARD).write_text(
        "# User attention\n\n## Needs the user\n\n## In-flight (parked by a session)\n\n- [ ] 2026-09-20 parked.\n"
    )
    git(repo, "commit", "-qam", "parked only")
    git(repo, "push", "-q", "origin", "main")
    got = board.add(repo, "First", "2026-10-02", entry="m-1", today="2026-09-25")
    lines = origin_board(repo).splitlines()
    assert got["line"] == 5 and lines[4].startswith("- [ ] 2026-09-25 (n/a) — First.")
    assert lines[5] == "## In-flight (parked by a session)"


async def test_put_on_the_board_twice_at_once_writes_one_line_and_a_refused_dismiss_is_said(
    agent, repo, tmp_path, monkeypatch
):
    """Review of PR #578: two presses on one entry at once write one line, the second refused; and
    a dismiss refused after the commit landed is reported beside the commit, not raised."""
    import asyncio

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "repos.txt").write_text(f"{repo}\n")
    (home / "hosts.yml").write_text(f"local:\n  repos_registry: {home / 'repos.txt'}\n")
    path = str(repo / board.BOARD)
    async with LocalClient() as me:
        w = (await me.call("create", name="w", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        async with LocalClient(caller=w) as worker:
            one = (await worker.call("msg", to="person", text="one"))["entry"]
            two = (await worker.call("msg", to="person", text="two"))["entry"]
        args = dict(board=path, action="add", text="Check it", due="2026-10-02")
        async with LocalClient() as a, LocalClient() as b:
            got = await asyncio.gather(
                a.call("board_edit", **args, entry=one["id"]), b.call("board_edit", **args, entry=one["id"]),
                return_exceptions=True,
            )  # fmt: skip
        assert sum(isinstance(g, dict) for g in got) == 1
        # refused while the first is under way — or, had it already finished, because the row is gone
        refused = str(next(g for g in got if not isinstance(g, dict)))
        assert "already being put on the board" in refused or "holds no entry" in refused
        assert origin_board(repo).count("Check it") == 1

        async def refuse(**_):
            from sessionorc.agent import RpcError

            raise RpcError("refused for the test")

        monkeypatch.setattr(agent, "rpc_inbox_dismiss", refuse)
        got = await me.call("board_edit", **args, entry=two["id"])
        assert got["dismissed"] == [] and got["dismiss_refused"] == "refused for the test" and got["commit"]
        await me.call("kill", id=w)


def test_a_reply_is_appended_to_the_items_own_line_signed_and_committed(repo):
    """TD-142 slice 1 (design §4.4): Reply appends ` — <name>, <date>: <reply>` to the item's line,
    the name the checkout's git `user.name`, one commit with the fixed message, the line still open."""
    git(repo, "config", "user.name", "Paul")
    got = board.write_back(repo, 7, ITEM, "reply", reply="  rebase it,\nthen merge  ")
    today = __import__("datetime").date.today().isoformat()
    line = origin_board(repo).splitlines()[6]
    assert line == f"- [ ] {ITEM} — Paul, {today}: rebase it, then merge"
    assert got["message"] == "agentorc: reply on Merged, live check pending: the doorbell. (session grinder-ao-1)"
    assert (
        on_origin(repo, "log", "-1", "--format=%s", "origin/main") == f"{got['message']} (#{got['pr']})"
        and git(repo, "status", "--porcelain") == ""
    )
    # the line still holds an open item, so a second reply or a Done finds it by its new text
    board.write_back(repo, 7, line[len("- [ ] ") :], "done")
    assert origin_board(repo).splitlines()[6].startswith("- [x] ")


def test_a_reply_is_refused_touching_nothing_where_an_edit_is(repo):
    for args, why in (
        ((7, ITEM, "reply"), "needs its text"),
        ((8, "not an item", "reply"), "no longer holds this item"),
    ):
        with pytest.raises(board.Refused, match=why):
            board.write_back(repo, *args, reply="" if why == "needs its text" else "x")
    assert origin_board(repo) == BOARD_TEXT
    with pytest.raises(board.Refused, match="no Due: date"):  # the reply's date would become the item's
        board.edit_line("- [ ] x\n", "x", "reply", reply="push Due: 2026-10-01", by="p", today="2026-09-25")
    assert "Due: 2026-10-01" in board.edit_line(f"- [ ] {ITEM}\n", ITEM, "reply", reply="Due: 2026-10-01", by="p")
    assert board.edit_line("- [ ] x\n", "x", "reply", reply="y", by="the person", today="2026-09-25") == (
        "- [ ] x — the person, 2026-09-25: y\n"
    )


async def test_board_reply_is_the_persons_and_mails_nobody_without_a_holder(agent, repo, tmp_path):
    """The RPC (§4.4): person-only, a known board only, the file half written and, with nobody
    holding a lease on the line's refs, `sent` empty — and `board_edit` will not take a reply,
    which is `board_reply`'s."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "repos.txt").write_text(f"{repo}\n")
    (home / "hosts.yml").write_text(f"local:\n  repos_registry: {home / 'repos.txt'}\n")
    path = str(repo / board.BOARD)
    async with LocalClient(caller="ao-some-worker") as worker:
        with pytest.raises(AgentError, match="the person's own"):
            await worker.call("board_reply", board=path, line=7, text=ITEM, reply="x")
    async with LocalClient() as me:
        with pytest.raises(AgentError, match="not the board of a repo this host knows"):
            await me.call("board_reply", board=str(tmp_path / "elsewhere.md"), line=7, text=ITEM, reply="x")
        with pytest.raises(AgentError, match="is board_reply"):
            await me.call("board_edit", board=path, line=7, text=ITEM, action="reply")
        with pytest.raises(AgentError, match="no longer holds this item"):
            await me.call("board_reply", board=path, line=8, text="not an item", reply="x")
        with pytest.raises(AgentError, match="names the item's line"):
            await me.call("board_reply", board=path, text=ITEM, reply="x")
        got = await me.call("board_reply", board=path, line=7, text=ITEM, reply="rebase it", refs=["TD-122"])
    assert got["action"] == "reply" and got["sent"] == [] and got["note"] == "written on the board"
    assert on_origin(repo, "log", "-1", "--format=%s", "origin/main") == f"{got['message']} (#{got['pr']})" and got[
        "message"
    ].startswith("agentorc: reply on")
    assert origin_board(repo).splitlines()[6].endswith(": rebase it")
    async with LocalClient() as me:  # no holder: no mail, and the trail line is still owed (§4.5a)
        trail = [t for t in (await me.call("inbox"))["trail"] if t["kind"] == "board reply"]
    assert [t["how"] for t in trail] == ["written on the board"]


def test_board_refs_are_named_as_a_lease_names_them():
    """The reader's `refs` (§4.4) against a claim's reference (§4.8): canonical ids, `PR #N` as `#N`."""
    from sessionorc.agent_inbox import board_refs

    assert board_refs(["td-122", "PR #1020", "TD-122", " ", "pr#7"]) == ["TD-122", "#1020", "#7"]
    assert board_refs(None) == []


async def test_board_reply_hands_a_note_to_each_live_lease_holder(agent, repo, tmp_path):
    """TD-142 slice 2 (§4.5a *Inbox board row → Reply*, §4.10 *A board reply owes an outcome too*):
    after the commit, each live record with a declared lease on one of the line's refs gets one
    `note` from the person, `about` the ref, marked `handed` — a holder of two refs once, a
    session holding none nothing — and the result says where it went."""
    _registry(tmp_path, repo)
    path = str(repo / board.BOARD)
    async with LocalClient() as me:
        mk = lambda n: me.call("create", name=n, dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])  # noqa: E731
        h, both, idle = [(await mk(n))["id"] for n in ("holder", "both", "idle")]
        for sid, refs in ((h, ["td-122"]), (both, ["#1020", "TD-123"])):
            async with LocalClient(caller=sid) as s:
                for ref in refs:
                    await s.call("progress", id=sid, ref=ref)
        got = await me.call(
            "board_reply", board=path, line=7, text=ITEM, reply="rebase it", refs=["TD-122", "PR #1020", "td-123"]
        )
        assert sorted((x["session"], x["ref"]) for x in got["sent"]) == [("both", "#1020"), ("holder", "TD-122")]
        assert got["note"].startswith("written on the board · sent to ") and "holder (holds TD-122)" in got["note"]
        assert origin_board(repo).splitlines()[6].endswith(": rebase it")  # the file half first
        for sid in (h, both):
            notes = [e for e in (await me.call("inbox", id=sid))["entries"] if e["from"] == "person"]
            assert len(notes) == 1 and notes[0]["kind"] == "note" and notes[0]["handed"]
            assert (
                notes[0]["about"] == ("TD-122" if sid == h else "#1020") and notes[0]["outcome"] is None
            )  # owes an outcome (§4.10)
            assert notes[0]["text"].startswith("board: Merged, live check pending: the doorbell.\n\n")
            assert notes[0]["text"].endswith(": rebase it")
        assert not [e for e in (await me.call("inbox", id=idle))["entries"] if e["from"] == "person"]
        # the trail says what the result says (§4.5a *Inbox board row → Reply*): one entry, no record
        trail = [t for t in (await me.call("inbox"))["trail"] if t["kind"] == "board reply"]
        assert len(trail) == 1 and trail[0]["how"] == got["note"] and trail[0]["sid"] == ""
        assert trail[0]["name"] == repo.name and trail[0]["text"].endswith(": rebase it")
        for sid in (h, both, idle):
            await me.call("kill", id=sid)


async def _orphan(me, repo, tmp_path, text, about, **kw):
    """A question from a session in `repo` that names `about`, orphaned by closing its asker."""
    w = (await me.call("create", name="asker", dir=str(repo), adapter="shell", argv=["bash", "--norc"]))["id"]
    async with LocalClient(caller=w) as s:
        mid = (await s.call("msg", to="person", text=text, about=about, **kw))["entry"]["id"]
    await me.call("close", id=w)
    return mid


def _registry(tmp_path, repo):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "repos.txt").write_text(f"{repo}\n")
    (home / "hosts.yml").write_text(f"local:\n  repos_registry: {home / 'repos.txt'}\n")


async def test_an_answer_to_an_orphaned_question_is_written_on_the_board_and_sent_to_its_holder(agent, repo, tmp_path):
    """TD-216 (design §4.10 *A question about a reference outlives its asker*, §4.4 the second add):
    with no holder the answer is one line on the asker's repo's board and nothing is mailed; with a
    holder it is the line and one `handed` note that owes an outcome; *Go with it* writes the
    default; the entry closes, its own debt settled, since nobody is left to report it."""
    _registry(tmp_path, repo)
    async with LocalClient() as me:
        q = await _orphan(
            me, repo, tmp_path, "Which fetcher first?\n\nThe reading, for the record.", "TD-149", kind="ask"
        )
        got = await me.call("msg", text="the DIU one", kind="reply", reply_to=q)
        assert got["sent"] == [] and got["closed"] == q
        assert got["note"] == "written on the board · left in asker's mailbox for its next run"
        # the closed asker's own record keeps the handed note for the name's next create (TD-271)
        left = [e for e in (await me.call("inbox", id=got["asker"]))["entries"] if e["from"] == "person"]
        assert len(left) == 1 and left[0]["handed"] and left[0]["text"].startswith("the DIU one")
        assert got["message"].startswith("agentorc: answer Which fetcher first?") and got["message"].endswith(
            f"(from {q})"
        )
        line = origin_board(repo).splitlines()[got["line"] - 1]
        assert line.startswith("- [ ] ") and "(session `asker` on " in line
        assert "— Which fetcher first? — t, " in line and ": the DIU one. Context: TD-149. Due: " in line
        assert "The reading" not in line  # the first paragraph only
        e = [e for e in (await me.call("inbox"))["entries"] if e["id"] == q][0]
        assert e["closed_reason"] == "replied" and e["outcome"]["state"] == "asker_gone"  # owes nothing
        assert git(repo, "status", "--porcelain") == ""  # committed, never left dirty

        # with a holder: the line, and one handed note to the session that holds the reference
        h = (await me.call("create", name="holder", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        async with LocalClient(caller=h) as s:
            await s.call("progress", id=h, ref="td-149")
        steer = await _orphan(me, repo, tmp_path, "Drop the key?", "td-149", kind="steer", default="drop it")
        got = await me.call("inbox_go_with_it", msg=steer)
        assert (
            got["sent"] == [h] and got["closed_reason"] == "go_with_it" and f"sent to {h} (holds TD-149)" in got["note"]
        )
        assert ": go with the default: drop it. Context: td-149." in origin_board(repo)
        handed = [e for e in (await me.call("inbox", id=h))["entries"] if e["from"] == "person"]
        assert len(handed) == 1 and handed[0]["handed"] and handed[0]["about"] == "TD-149"
        assert handed[0]["text"].startswith("go with the default: drop it")
        assert handed[0]["outcome"] is None  # handed and unsettled: the holder owes an outcome (§4.10)
        assert agent.sessions[h].inbox[-1].owes_for(session_inbox=True)
        await me.call("kill", id=h)


async def test_a_full_closed_askers_mailbox_never_keeps_the_answer_from_the_holder(agent, repo, tmp_path, monkeypatch):
    """TD-274 slice 3 (review of the slice): the holder's note and the closed asker's are two
    sends, so an asker whose mailbox is at depth is said beside the press and the holder is still
    mailed."""
    from sessionorc import mail

    _registry(tmp_path, repo)
    async with LocalClient() as me:
        h = (await me.call("create", name="holder", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        async with LocalClient(caller=h) as s:
            await s.call("progress", id=h, ref="TD-149")
        q = await _orphan(me, repo, tmp_path, "Which?", "TD-149", kind="ask")
        asker = next(r.id for r in agent.sessions.values() if r.name == "asker")
        monkeypatch.setattr(mail, "MAILBOX_DEPTH", 1)
        agent._system_note(asker, "filler")  # the asker's mailbox at depth
        got = await me.call("msg", text="this one", kind="reply", reply_to=q)
        assert got["sent"] == [h] and "asker" not in got and got["asker_refused"]
        assert "not left in asker's mailbox" in got["note"]
        assert [e["text"] for e in (await me.call("inbox", id=h))["entries"] if e["from"] == "person"][0].startswith(
            "this one"
        )
        await me.call("kill", id=h)


async def test_an_answer_to_an_orphaned_question_is_refused_touching_nothing_when_the_board_cannot_take_it(
    agent, repo, tmp_path, monkeypatch
):
    """TD-216: origin out of reach refuses the press and the entry stays open; a second press while the
    first is in flight is refused; a reply naming another addressee, or a suggested answer that is
    not word for word, is refused; Pause stays refused (no session to hold)."""
    import asyncio

    _registry(tmp_path, repo)
    async with LocalClient() as me:
        q = await _orphan(me, repo, tmp_path, "Which?", "#702", kind="ask", answers=["this", "that"])
        with monkeypatch.context() as m:
            m.setenv("FAKE_GH_FAIL", "merge")
            with pytest.raises(AgentError, match="origin could not be reached"):
                await me.call("msg", text="this", kind="reply", reply_to=q, answer=0)
        e = [e for e in (await me.call("inbox"))["entries"] if e["id"] == q][0]
        assert e["closed_reason"] is None and e["orphaned"]
        with pytest.raises(AgentError, match="suggested answers"):
            await me.call("msg", text="thus", kind="reply", reply_to=q, answer=0)
        async with LocalClient() as a, LocalClient() as b:
            got = await asyncio.gather(
                a.call("msg", text="this", kind="reply", reply_to=q, answer=0),
                b.call("msg", text="this", kind="reply", reply_to=q, answer=0),
                return_exceptions=True,
            )  # fmt: skip
        assert sum(isinstance(g, dict) for g in got) == 1
        refused = str(next(g for g in got if not isinstance(g, dict)))
        assert "already being answered" in refused or "already closed" in refused
        assert origin_board(repo).count(": this. Context: #702.") == 1
        e = [e for e in (await me.call("inbox"))["entries"] if e["id"] == q][0]
        assert e["answer"] == 0


async def test_a_refused_note_to_the_holder_is_said_beside_the_committed_line_and_closes_the_entry(
    agent, repo, tmp_path, monkeypatch
):
    """Review of PR #717: the line is committed before the holder is mailed, so a refused send is
    reported with the result and the entry closes — a second press must not write the line twice.
    A reply naming another addressee, and a reply to a closed orphaned entry, are refused."""
    from sessionorc.agent_common import RpcError

    _registry(tmp_path, repo)
    async with LocalClient() as me:
        h = (await me.call("create", name="holder", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        async with LocalClient(caller=h) as s:
            await s.call("progress", id=h, ref="TD-5")
        q = await _orphan(me, repo, tmp_path, "Which?", "TD-5", kind="ask")
        with pytest.raises(AgentError, match="a reply alone"):
            await me.call("msg", to=[h], text="x", kind="reply", reply_to=q)

        real = agent._msg

        async def full(sender, text, to, kind, *a, **k):
            if kind == "note":  # the holder's note, not the person's reply that leads to it
                raise RpcError("its mailbox is full")
            return await real(sender, text, to, kind, *a, **k)

        monkeypatch.setattr(agent, "_msg", full)
        got = await me.call("msg", text="this one", kind="reply", reply_to=q)
        monkeypatch.undo()
        assert got["sent"] == [] and got["mail_refused"] == "its mailbox is full" and "not sent to" in got["note"]
        e = [e for e in (await me.call("inbox"))["entries"] if e["id"] == q][0]
        assert e["closed_reason"] == "replied" and "its mailbox is full" in e["outcome"]["text"]
        with pytest.raises(AgentError, match="already closed"):
            await me.call("msg", text="again", kind="reply", reply_to=q)
        assert origin_board(repo).count(": this one. Context: TD-5.") == 1
        await me.call("kill", id=h)


ASKED = (
    "decide 2026-09-27 (session `grinder-ao-1` on kmaster) — **Which window?** Say. Context: TD-190. "
    "Due: 2026-10-04. Answers: Keep it (default) | Lift it."
)
LOOK = (
    "watch 2026-09-30 (session `grinder-ao-2` on kmaster) — **Merged, live look pending: the keys.** Look. "
    "Due: 2026-10-01. Answers: Works | Not right: <what>."
)


def test_a_decide_writes_the_field_at_the_lines_end_and_its_message():
    """TD-255 slice 1 (design §4.4 *Decide*): the edit `board_edit.py decide` makes."""
    line = f"- [ ] {ASKED}\n"
    got = board.edit_line(line, ASKED, "decide", answer="Lift  it", today="2026-10-01")
    assert got == f"- [ ] {ASKED} Decided: Lift it (2026-10-01).\n"
    assert board.DECIDED_RE.search(got.rstrip("\n")).group("text", "date") == ("Lift it", "2026-10-01")
    # an item that ends no sentence is ended first: the field starts one, as the reader asks
    assert board.edit_line("- [ ] n/a — undated", "n/a — undated", "decide", answer="Yes", today="2026-10-01") == (
        "- [ ] n/a — undated. Decided: Yes (2026-10-01)."
    )
    assert board.message(ASKED, "decide", answer="Lift it") == (
        "agentorc: decide Which window?: Lift it (session grinder-ao-1)"
    )
    long = board.message(ASKED, "decide", answer="Not right: " + "the ring is amber " * 9)  # a sentence is clipped
    assert long.endswith("… (session grinder-ao-1)") and len(long) < 140
    for bad in (
        lambda: board.edit_line(line, ASKED, "decide", answer="  "),  # no answer
        lambda: board.edit_line(got, ASKED, "decide", answer="Keep it"),  # the line moved: it is decided
        lambda: board.edit_line(got, got[6:].strip(), "decide", answer="Keep it"),  # already decided
        lambda: board.edit_line(line, ASKED, "decide", answer="No. Decided: yes"),  # the reader would misread it
        lambda: board.edit_line(line, ASKED, "decide", answer="Not right: bad. Answers: z"),  # review of PR #861
        lambda: board.edit_line(line, ASKED, "reply", reply="x. Decided: yes (2026-01-01)", by="t"),
        lambda: board.edit_line("- [ ] n/a — undated.", "n/a — undated.", "reply", reply="x. Answers: a | b", by="t"),
    ):
        with pytest.raises(board.Refused):
            bad()


READER = Path(__file__).parents[1] / "scripts" / "nudge_user_attention.py"


def read_back(tmp_path: Path, line: str) -> dict:
    """What dev-cadence's own reader makes of `line`: the fields the Inbox draws."""
    import json
    import sys

    (tmp_path / "docs").mkdir(exist_ok=True)
    path = tmp_path / board.BOARD
    path.write_text(f"# b\n\n## Needs the user\n\n{line}\n")
    out = subprocess.run(
        [sys.executable, str(READER), "--board", str(path), "--report", "--json"],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return json.loads(out)["boards"][0]["items"][0]


def test_a_reply_and_a_decide_compose_in_either_order(tmp_path):
    """§4.4 *The two compose*: a reply goes ahead of the line's `Answers:` and `Decided:` fields,
    which stay at its end and readable — by the board's own reader — and a decide after a reply
    is still the line's last field."""
    decided = f"{ASKED} Decided: Lift it (2026-10-01)."
    got = board.edit_line(f"- [ ] {decided}", decided, "reply", reply="the node first", by="Paul", today="2026-10-02")
    assert got == (
        "- [ ] decide 2026-09-27 (session `grinder-ao-1` on kmaster) — **Which window?** Say. Context: TD-190. "
        "Due: 2026-10-04. — Paul, 2026-10-02: the node first. Answers: Keep it (default) | Lift it. "
        "Decided: Lift it (2026-10-01)."
    )
    read = read_back(tmp_path, got)
    assert read["decided"] == {"text": "Lift it", "date": "2026-10-01"}
    assert (read["answers"], read["default"], read["due"]) == (["Keep it", "Lift it"], "Keep it", "2026-10-04")
    replied = board.edit_line(f"- [ ] {ASKED}", ASKED, "reply", reply="say more", by="Paul", today="2026-10-02")[6:]
    assert replied.endswith("— Paul, 2026-10-02: say more. Answers: Keep it (default) | Lift it.")
    got = board.edit_line(f"- [ ] {replied}", replied, "decide", answer="Keep it", today="2026-10-03")
    read = read_back(tmp_path, got)
    assert read["decided"] == {"text": "Keep it", "date": "2026-10-03"} and read["answers"] == ["Keep it", "Lift it"]
    # with no fields on the line a reply is its tail, and a decide follows it
    bare = "n/a — undated."
    replied = board.edit_line(f"- [ ] {bare}", bare, "reply", reply="go on", by="Paul", today="2026-10-02")[6:]
    assert board.edit_line(f"- [ ] {replied}", replied, "decide", answer="Yes", today="2026-10-03") == (
        "- [ ] n/a — undated. — Paul, 2026-10-02: go on. Decided: Yes (2026-10-03)."
    )


def test_a_decide_is_committed_and_refused_where_every_edit_is(repo):
    (repo / board.BOARD).write_text(BOARD_TEXT + f"- [ ] {ASKED}\n")
    git(repo, "commit", "-qam", "asked")
    git(repo, "push", "-q", "origin", "main")
    got = board.write_back(repo, 9, ASKED, "decide", answer="Keep it")
    assert got["message"] == "agentorc: decide Which window?: Keep it (session grinder-ao-1)"
    assert (
        on_origin(repo, "log", "-1", "--format=%s", "origin/main") == f"{got['message']} (#{got['pr']})"
        and git(repo, "status", "--porcelain") == ""
    )
    assert board.DECIDED_RE.search(origin_board(repo).splitlines()[8]).group("text") == "Keep it"
    head = on_origin(repo, "rev-parse", "origin/main")
    git(repo, "pull", "-q", "--ff-only", "origin", "main")  # the checkout catches up (§6 *Pull*)
    for line, text in ((9, ASKED), (6, "not an item")):  # already decided; moved
        with pytest.raises(board.Refused):
            board.write_back(repo, line, text, "decide", answer="Keep it")
        assert on_origin(repo, "rev-parse", "origin/main") == head


async def test_board_edit_decides_only_with_one_of_the_items_answers(agent, repo, tmp_path):
    """The RPC's half: the answer is one of the reader's `answers` word for word, or `Not right:`
    and words where the pair is among them; anything else is a Reply, and a session is refused."""
    (repo / board.BOARD).write_text(BOARD_TEXT + f"- [ ] {ASKED}\n- [ ] {LOOK}\n")
    git(repo, "commit", "-qam", "asked")
    git(repo, "push", "-q", "origin", "main")
    path, answers, pair = str(repo / board.BOARD), ["Keep it", "Lift it"], ["Works", "Not right: <what>"]
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "repos.txt").write_text(f"{repo}\n")
    (home / "hosts.yml").write_text(f"local:\n  repos_registry: {home / 'repos.txt'}\n")
    async with LocalClient(caller="ao-some-worker") as worker:
        with pytest.raises(AgentError, match="person's own"):
            await worker.call("board_edit", board=path, line=9, text=ASKED, action="decide", answer="Keep it")
    async with LocalClient() as me:
        for bad, offered in (("Keep", answers), ("", answers), ("Keep it", None), ("Not right: x", answers)):
            with pytest.raises(AgentError, match="word for word"):
                await me.call(
                    "board_edit", board=path, line=9, text=ASKED, action="decide", answer=bad, answers=offered
                )
        for bad in ("Not right:", "Not right: <what>"):
            with pytest.raises(AgentError, match="needs its words"):
                await me.call("board_edit", board=path, line=10, text=LOOK, action="decide", answer=bad, answers=pair)
        got = await me.call("board_edit", board=path, line=9, text=ASKED, action="decide", answer="Keep it",
                            answers=["Keep  it", "Lift it"])  # fmt: skip
        assert got["answer"] == "Keep it" and got["message"].startswith("agentorc: decide Which window?: Keep it")
        got = await me.call("board_edit", board=path, line=10, text=LOOK, action="decide",
                            answer="Not right: the ring is amber", answers=pair)  # fmt: skip
        assert got["answer"] == "Not right: the ring is amber"
    lines = origin_board(repo).splitlines()
    assert board.DECIDED_RE.search(lines[8]).group("text") == "Keep it"
    assert board.DECIDED_RE.search(lines[9]).group("text") == "Not right: the ring is amber"


@pytest.mark.unit
def test_an_offered_answer_word_for_word_comes_before_the_not_right_form():
    """Review of PR #863: an item whose own answer is a complete *Not right: wrong repo* is decided
    by it word for word; the form is only `Not right: <what>` (or the bare prefix), pressed as
    written it is refused, and typed words pass only where the form is offered."""
    from sessionorc.agent_inbox import InboxMixin as A

    own = ["Right repo", "Not right: wrong repo"]
    assert A._board_answer("decide", "Not right:  wrong repo", own) == "Not right: wrong repo"
    with pytest.raises(board.Refused, match="word for word"):
        A._board_answer("decide", "Not right: something else", own)  # no form offered: a Reply
    both = ["Works", "Not right: <what>", "Not right: wrong repo"]
    assert A._board_answer("decide", "Not right: wrong repo", both) == "Not right: wrong repo"
    assert A._board_answer("decide", "Not right: the ring is amber", both) == "Not right: the ring is amber"
    for bad in ("Not right: <what>", "Not right:"):
        with pytest.raises(board.Refused, match="needs its words"):
            A._board_answer("decide", bad, both)
    assert A._board_answer("snooze", "anything", both) == ""


def test_board_reply_hand_is_the_homes_edit():
    """The mail half is the home's (§4.4a, review of PR #1019): a node forwards it, never serves it."""
    from sessionorc import modes

    assert "board_reply_hand" in modes.HOME_EDITS and "board_reply" not in modes.HOME_EDITS
    assert "kmaster (home) is unreachable" in modes.offline_refusal(
        "board_reply_hand", None, {}, host="laptop", home="kmaster"
    )


async def test_a_node_writes_the_board_and_hands_the_mail_to_the_home(agent, repo, tmp_path, monkeypatch):
    """At a node (§4.4a) `board_reply` writes the line where the registry holds the repo and hands
    the mail half to the home as `board_reply_hand`; with the link down it mails nobody and says
    so beside the committed line. Nothing is written to the node's own mailbox."""
    _registry(tmp_path, repo)
    path = str(repo / board.BOARD)
    async with LocalClient() as me:
        h = (await me.call("create", name="holder", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        async with LocalClient(caller=h) as s:
            await s.call("progress", id=h, ref="TD-122")
        with pytest.raises(AgentError, match="the person's own"):
            async with LocalClient(caller=h) as s:
                await s.call("board_reply_hand", head="x", by="p", reply="y", refs=["TD-122"])
        monkeypatch.setattr(agent, "mode", "node")
        monkeypatch.setattr(agent, "home", "kmaster")
        monkeypatch.setitem(agent.home_link, "up", False)
        got = await agent.rpc_board_reply(board=path, line=7, text=ITEM, reply="rebase it", refs=["TD-122"])
        assert got["sent"] == [] and "mail is the home's: kmaster (home) is unreachable" in got["note"]
        assert origin_board(repo).splitlines()[6].endswith(": rebase it")
        forwarded = []

        async def forward(rid, name, params, caller):
            forwarded.append((name, params, caller))
            return {
                "id": rid,
                "result": {"sent": [{"session": "holder", "id": "h@kmaster", "ref": "TD-122"}], "refused": []},
            }

        monkeypatch.setattr(agent, "_forward", forward)
        monkeypatch.setitem(agent.home_link, "up", True)
        line = origin_board(repo).splitlines()[6][len("- [ ] ") :]
        got = await agent.rpc_board_reply(board=path, line=7, text=line, reply="and push", refs=["TD-122"])
        assert got["note"] == "written on the board · sent to holder (holds TD-122)"
        assert [(n, p["head"], p["reply"], p["repo"], c) for n, p, c in forwarded] == [
            ("board_reply_hand", "Merged, live check pending: the doorbell.", "and push", repo.name, None)
        ]
        assert not [e for e in agent.sessions[h].inbox if e.from_ == "person"]  # the node's store untouched
        # the trail is the home's too (§4.10): handed over with no refs as well, never written here
        line = origin_board(repo).splitlines()[6][len("- [ ] ") :]
        await agent.rpc_board_reply(board=path, line=7, text=line, reply="thanks", refs=[])
        assert forwarded[-1][1]["refs"] == [] and forwarded[-1][1]["repo"] == repo.name
        assert not [t for t in agent.trail if t["kind"] == "board reply"]

        async def broken(rid, name, params, caller):
            return {"id": rid, "error": "link dropped"}

        monkeypatch.setattr(agent, "_forward", broken)  # a failed hand with nobody to mail: nothing to say
        line = origin_board(repo).splitlines()[6][len("- [ ] ") :]
        got = await agent.rpc_board_reply(board=path, line=7, text=line, reply="once more", refs=[])
        assert got["note"] == "written on the board"
        monkeypatch.setitem(agent.home_link, "up", False)  # down, and nobody to mail: nothing to say
        line = origin_board(repo).splitlines()[6][len("- [ ] ") :]
        got = await agent.rpc_board_reply(board=path, line=7, text=line, reply="again", refs=None)
        assert got["note"] == "written on the board"
        monkeypatch.setattr(agent, "mode", "home")
        await me.call("kill", id=h)


def test_a_merge_that_landed_though_the_forge_said_otherwise_is_a_landed_edit(repo, monkeypatch):
    """Review of PR #1036: `gh pr merge` failing after the forge took the merge (a timeout, a lost
    reply) is read from origin — the edit landed — so the press succeeds and a retried add never
    writes its line twice."""
    monkeypatch.setenv("FAKE_GH_FAIL", "merge-after")
    got = board.add(repo, "Once only", "2026-10-02", entry="m-1", today="2026-09-25")
    assert got["pr"] == 1 and origin_board(repo).count("Once only") == 1
    board.write_back(repo, 8, ITEM, "done")
    assert f"- [x] {ITEM}" in origin_board(repo)


def test_a_merge_the_forge_took_while_origins_board_moved_elsewhere_is_a_landed_edit(repo, monkeypatch):
    """TD-264 (d): the merge's reply lost **and** origin's board changed elsewhere in the same moment —
    the board differs from the commit's, so only the forge's word on the PR says it landed: a Done
    is not refused as a moved line, and an add is not refused as unreachable (a retry would write
    its line twice)."""
    monkeypatch.setenv("FAKE_GH_FAIL", "merge-after-moved")
    got = board.add(repo, "Once only", "2026-10-02", entry="m-1", today="2026-09-25")
    assert got["pr"] == 1 and origin_board(repo).count("Once only") == 1
    assert "A neighbour's edit." in origin_board(repo)
    board.write_back(repo, 8, ITEM, "done")
    assert f"- [x] {ITEM}" in origin_board(repo)


async def test_the_home_writes_the_trail_line_a_node_handed_it(agent):
    """`board_reply_hand` with `repo` (a node's Reply, §4.4a): the home writes the trail line in the
    result's words, holder or none — the trail is the home's, beside the person inbox (§4.10)."""
    got = await agent.rpc_board_reply_hand(head="A line.", by="Paul", reply="go on", refs=["TD-999"], repo="cm")
    assert got == {"sent": [], "refused": []}
    t = next(t for t in agent.trail if t["kind"] == "board reply")
    assert (t["name"], t["sid"], t["how"], t["text"]) == ("cm", "", "written on the board", "A line. — Paul: go on")
    await agent.rpc_board_reply_hand(head="B", by="Paul", reply="x", refs=["TD-999"])  # no repo: no line
    assert len([t for t in agent.trail if t["kind"] == "board reply"]) == 1
