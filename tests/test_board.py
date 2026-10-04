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
            await me.call("board_reply", board=path, line=8, text=ITEM, reply="x")
        with pytest.raises(AgentError, match="names the item's line"):
            await me.call("board_reply", board=path, text=ITEM, reply="x")
        got = await me.call("board_reply", board=path, line=7, text=ITEM, reply="rebase it", refs=["TD-122"])
    assert got["action"] == "reply" and got["sent"] == [] and got["note"] == "written on the board"
    assert git(repo, "log", "-1", "--format=%s") == got["message"] and got["message"].startswith("agentorc: reply on")
    assert (repo / board.BOARD).read_text().splitlines()[6].endswith(": rebase it")


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
        assert (repo / board.BOARD).read_text().splitlines()[6].endswith(": rebase it")  # the file half first
        for sid in (h, both):
            notes = [e for e in (await me.call("inbox", id=sid))["entries"] if e["from"] == "person"]
            assert len(notes) == 1 and notes[0]["kind"] == "note" and notes[0]["handed"]
            assert (
                notes[0]["about"] == ("TD-122" if sid == h else "#1020") and notes[0]["outcome"] is None
            )  # owes an outcome (§4.10)
            assert notes[0]["text"].startswith("board: Merged, live check pending: the doorbell.\n\n")
            assert notes[0]["text"].endswith(": rebase it")
        assert not [e for e in (await me.call("inbox", id=idle))["entries"] if e["from"] == "person"]
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
        line = (repo / board.BOARD).read_text().splitlines()[got["line"] - 1]
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
        assert ": go with the default: drop it. Context: td-149." in (repo / board.BOARD).read_text()
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
    agent, repo, tmp_path
):
    """TD-216: a dirty board refuses the press and the entry stays open; a second press while the
    first is in flight is refused; a reply naming another addressee, or a suggested answer that is
    not word for word, is refused; Pause stays refused (no session to hold)."""
    import asyncio

    _registry(tmp_path, repo)
    async with LocalClient() as me:
        q = await _orphan(me, repo, tmp_path, "Which?", "#702", kind="ask", answers=["this", "that"])
        (repo / board.BOARD).write_text(BOARD_TEXT + "dirty\n")
        with pytest.raises(AgentError, match="uncommitted changes"):
            await me.call("msg", text="this", kind="reply", reply_to=q, answer=0)
        e = [e for e in (await me.call("inbox"))["entries"] if e["id"] == q][0]
        assert e["closed_reason"] is None and e["orphaned"]
        (repo / board.BOARD).write_text(BOARD_TEXT)
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
        assert (repo / board.BOARD).read_text().count(": this. Context: #702.") == 1
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
        assert (repo / board.BOARD).read_text().count(": this one. Context: TD-5.") == 1
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
    got = board.write_back(repo, 9, ASKED, "decide", answer="Keep it")
    assert got["message"] == "agentorc: decide Which window?: Keep it (session grinder-ao-1)"
    assert git(repo, "log", "-1", "--format=%s") == got["message"] and git(repo, "status", "--porcelain") == ""
    assert board.DECIDED_RE.search((repo / board.BOARD).read_text().splitlines()[8]).group("text") == "Keep it"
    before, head = (repo / board.BOARD).read_text(), git(repo, "rev-parse", "HEAD")
    for line, state in ((9, "decided"), (6, "moved"), (7, "dirty")):
        if state == "dirty":
            (repo / board.BOARD).write_text(before + "- [ ] a line being written.\n")
        with pytest.raises(board.Refused):
            board.write_back(repo, line, ASKED if line != 7 else ITEM, "decide", answer="Keep it")
        assert git(repo, "rev-parse", "HEAD") == head


async def test_board_edit_decides_only_with_one_of_the_items_answers(agent, repo, tmp_path):
    """The RPC's half: the answer is one of the reader's `answers` word for word, or `Not right:`
    and words where the pair is among them; anything else is a Reply, and a session is refused."""
    (repo / board.BOARD).write_text(BOARD_TEXT + f"- [ ] {ASKED}\n- [ ] {LOOK}\n")
    git(repo, "commit", "-qam", "asked")
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
    lines = (repo / board.BOARD).read_text().splitlines()
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
