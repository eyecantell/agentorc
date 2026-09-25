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
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / board.BOARD).write_text(BOARD_TEXT)
    (root / "other.txt").write_text("x\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


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


def test_write_back_commits_the_one_line_on_main_and_nothing_else(repo):
    (repo / "other.txt").write_text("the anchor's own edit, uncommitted\n")
    got = board.write_back(repo, 7, ITEM, "snooze", "2026-10-01")
    assert git(repo, "log", "-1", "--format=%s") == got["message"] and got["commit"]
    assert git(repo, "show", "--name-only", "--format=", "HEAD") == str(board.BOARD)
    assert "Due: 2026-10-01." in (repo / board.BOARD).read_text()
    assert git(repo, "status", "--porcelain") == "M other.txt"  # someone else's edit, untouched
    board.write_back(repo, 8, "n/a — undated.", "done")
    assert "- [x] n/a — undated." in (repo / board.BOARD).read_text()


@pytest.mark.parametrize("state", ["branch", "dirty", "rebase", "moved"])
def test_write_back_is_refused_and_touches_nothing_when_the_checkout_cannot_take_it(repo, state):
    if state == "branch":
        git(repo, "checkout", "-q", "-b", "td-feature")
    elif state == "dirty":
        (repo / board.BOARD).write_text(BOARD_TEXT + "- [ ] a line being written. Due: 2026-09-30.\n")
    elif state == "rebase":
        (Path(git(repo, "rev-parse", "--absolute-git-dir")) / "rebase-merge").mkdir()
    before, head = (repo / board.BOARD).read_text(), git(repo, "rev-parse", "HEAD")
    with pytest.raises(board.Refused):
        board.write_back(repo, 6 if state == "moved" else 7, ITEM, "done")
    assert (repo / board.BOARD).read_text() == before and git(repo, "rev-parse", "HEAD") == head


def test_a_failed_commit_leaves_the_board_as_it_was(repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'no commits today' >&2\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(board.Refused, match="no commits today"):
        board.write_back(repo, 7, ITEM, "done")
    assert (repo / board.BOARD).read_text() == BOARD_TEXT and git(repo, "status", "--porcelain") == ""


def test_a_commit_that_does_not_finish_leaves_the_board_as_it_was(repo, monkeypatch):
    """Review of PR #474: a timeout raised past the returncode check and left the edit uncommitted."""
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nsleep 5\n")
    hook.chmod(0o755)
    monkeypatch.setattr(board, "GIT_TIMEOUT", 0.5)
    with pytest.raises(board.Refused, match="did not finish"):
        board.write_back(repo, 7, ITEM, "done")
    assert (repo / board.BOARD).read_text() == BOARD_TEXT


def test_two_edits_at_once_are_made_one_after_the_other(repo):
    """Review of PR #474: two interleaved read-write-commits left *done* in the log and undone on disk."""
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        a = pool.submit(board.write_back, repo, 7, ITEM, "done")
        b = pool.submit(board.write_back, repo, 8, "n/a — undated.", "done")
        a.result(), b.result()
    text = (repo / board.BOARD).read_text()
    assert f"- [x] {ITEM}" in text and "- [x] n/a — undated." in text
    assert git(repo, "status", "--porcelain") == ""


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
            await me.call("board_edit", board=path, line=8, text=ITEM, action="done")
        got = await me.call("board_edit", board=path, line=7, text=ITEM, action="snooze", due="2026-10-01")
    assert got["action"] == "snooze" and got["message"].startswith("agentorc: snooze Merged")
    assert git(repo, "log", "-1", "--format=%s") == got["message"]


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
    text = (repo / board.BOARD).read_text()
    assert text == BOARD_TEXT.replace(f"- [ ] {ITEM}", line.rstrip("\n") + f"\n- [ ] {ITEM}")
    assert got["line"] == 7 and got["message"] == "agentorc: board Check the fetcher Friday (from m-abc)"
    assert git(repo, "log", "-1", "--format=%s") == got["message"] and git(repo, "status", "--porcelain") == ""
    # no sender, no about: `n/a` and `none`
    assert board.item_line("x", "2026-10-02", "2026-09-25", None, None, None) == (
        "- [ ] 2026-09-25 (n/a) — x. Context: none. Due: 2026-10-02.\n"
    )
    for bad in (("", "2026-10-02"), ("two\nlines", "2026-10-02"), ("x", "Friday")):
        with pytest.raises(board.Refused):
            board.add(repo, *bad, entry="m-abc")
    assert git(repo, "status", "--porcelain") == ""


def test_the_add_on_a_board_with_no_items_goes_under_its_heading(repo):
    (repo / board.BOARD).write_text("# User attention\n\n## Needs the user\n\n## Later\n")
    git(repo, "commit", "-qam", "empty")
    board.add(repo, "First", "2026-10-02", entry="m-1", today="2026-09-25")
    assert (repo / board.BOARD).read_text().splitlines()[4] == (
        "- [ ] 2026-09-25 (n/a) — First. Context: none. Due: 2026-10-02."
    )


def test_the_add_is_refused_touching_nothing_where_an_edit_is(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    with pytest.raises(board.Refused, match="not main"):
        board.add(repo, "x", "2026-10-02", entry="m-1")
    assert (repo / board.BOARD).read_text() == BOARD_TEXT


async def test_put_on_the_board_is_the_persons_from_an_fyi_row_and_dismisses_it(agent, repo, tmp_path):
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
        (repo / board.BOARD).write_text(BOARD_TEXT + "dirty\n")  # a refused commit: the row stays
        with pytest.raises(AgentError, match="uncommitted changes"):
            await me.call("board_edit", board=path, action="add", text="x", due="2026-10-02", entry=note["id"])
        assert note["id"] in [e["id"] for e in (await me.call("inbox"))["entries"]]
        (repo / board.BOARD).write_text(BOARD_TEXT)
        got = await me.call(
            "board_edit", board=path, action="add", text="Check the fetcher", due="2026-10-02", entry=note["id"]
        )
        assert got["dismissed"] == [note["id"]] and got["message"].endswith(f"(from {note['id']})")
        line = (repo / board.BOARD).read_text().splitlines()[got["line"] - 1]
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
    got = board.add(repo, "First", "2026-10-02", entry="m-1", today="2026-09-25")
    lines = (repo / board.BOARD).read_text().splitlines()
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
        assert (repo / board.BOARD).read_text().count("Check it") == 1

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
    line = (repo / board.BOARD).read_text().splitlines()[6]
    assert line == f"- [ ] {ITEM} — Paul, {today}: rebase it, then merge"
    assert got["message"] == "agentorc: reply on Merged, live check pending: the doorbell. (session grinder-ao-1)"
    assert git(repo, "log", "-1", "--format=%s") == got["message"] and git(repo, "status", "--porcelain") == ""
    # the line still holds an open item, so a second reply or a Done finds it by its new text
    board.write_back(repo, 7, line[len("- [ ] ") :], "done")
    assert (repo / board.BOARD).read_text().splitlines()[6].startswith("- [x] ")


def test_a_reply_is_refused_touching_nothing_where_an_edit_is(repo):
    for args, why in (
        ((7, ITEM, "reply"), "needs its text"),
        ((8, ITEM, "reply"), "no longer holds this item"),
    ):
        with pytest.raises(board.Refused, match=why):
            board.write_back(repo, *args, reply="" if why == "needs its text" else "x")
    git(repo, "checkout", "-q", "-b", "feature")
    with pytest.raises(board.Refused, match="not main"):
        board.write_back(repo, 7, ITEM, "reply", reply="x")
    assert (repo / board.BOARD).read_text() == BOARD_TEXT
    with pytest.raises(board.Refused, match="no Due: date"):  # the reply's date would become the item's
        board.edit_line("- [ ] x\n", "x", "reply", reply="push Due: 2026-10-01", by="p", today="2026-09-25")
    assert "Due: 2026-10-01" in board.edit_line(f"- [ ] {ITEM}\n", ITEM, "reply", reply="Due: 2026-10-01", by="p")
    assert board.edit_line("- [ ] x\n", "x", "reply", reply="y", by="the person", today="2026-09-25") == (
        "- [ ] x — the person, 2026-09-25: y\n"
    )


async def test_board_reply_is_the_persons_and_says_nothing_was_sent_yet(agent, repo, tmp_path):
    """The RPC (§4.4): person-only, a known board only, the file half written and `sent` empty with
    the note saying why — and `board_edit` will not take a reply, which is `board_reply`'s."""
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
            await me.call("board_reply", board=path, line=8, text=ITEM, reply="x")
        with pytest.raises(AgentError, match="names the item's line"):
            await me.call("board_reply", board=path, text=ITEM, reply="x")
        got = await me.call("board_reply", board=path, line=7, text=ITEM, reply="rebase it", refs=["TD-122"])
    assert got["action"] == "reply" and got["sent"] == [] and "no session standing is known" in got["note"]
    assert git(repo, "log", "-1", "--format=%s") == got["message"] and got["message"].startswith("agentorc: reply on")
    assert (repo / board.BOARD).read_text().splitlines()[6].endswith(": rebase it")
