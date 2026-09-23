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
