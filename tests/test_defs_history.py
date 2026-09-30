"""Design §4.9 *What is left at the home has a history* (TD-210, TD-229 slice 5): at the home
`~/.agentorc` is a git work tree tracking `org.yml`, `profiles.yml` and `settings.yml`, and the home's
host agent is its one committer — after `set_settings`, on `commit_defs`, and for a hand edit."""

from __future__ import annotations

import asyncio
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import park_ticks

from agentorc import service
from sessionorc import defs, hosts, modes, paths
from sessionorc.client import AgentError, LocalClient


def _log(home: Path, *paths_: str) -> list[str]:
    cp = subprocess.run(["git", "-C", str(home), "log", "--format=%s", "--", *paths_], capture_output=True, text=True)
    return cp.stdout.splitlines()


def _tracked(home: Path) -> set[str]:
    return set(subprocess.run(["git", "-C", str(home), "ls-files"], capture_output=True, text=True).stdout.split())



async def _committed(agent) -> None:
    """The commit after `set_settings` runs detached (`_bg`): wait for it."""
    while agent._bg:
        await asyncio.gather(*list(agent._bg), return_exceptions=True)

def test_init_tracks_the_three_files_and_ignores_the_rest(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    for f in ("org.yml", "settings.yml", "hosts.yml"):
        (home / f).write_text("a: 1\n")
    (home / "sessions").mkdir()
    (home / "sessions" / "x.json").write_text("{}")
    assert defs.init(home) is True
    assert _tracked(home) == {".gitignore", "org.yml", "settings.yml"}, "hosts.yml and state are never tracked"
    assert _log(home) == ["the home's definitions, first tracked"]
    assert defs.init(home) is False, "a work tree already: nothing done"
    (home / "profiles.yml").write_text("p: {}\n")
    (home / "hosts.yml").write_text("a: 2\n")
    assert defs.commit("edited by hand", home=home) is True
    assert _tracked(home) == {".gitignore", "org.yml", "settings.yml", "profiles.yml"}
    assert defs.commit("edited by hand", home=home) is False, "nothing changed: no commit"


def test_a_gitignore_of_the_persons_own_is_kept_and_the_three_let_in(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitignore").write_text("*.bak\n")
    (home / "org.yml").write_text("teams: {}\n")
    (home / "org.yml.bak-1").write_text("x\n")
    (home / "hosts.yml").write_text("a: 1\n")
    defs.init(home)
    assert (home / ".gitignore").read_text().startswith("*.bak\n")
    assert _tracked(home) == {".gitignore", "org.yml"}


def test_a_half_written_file_waits_until_it_parses(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "org.yml").write_text("teams: {}\n")
    defs.init(home)
    (home / "org.yml").write_text("teams: [unclosed\n")
    assert defs.commit("edited by hand", home=home) is False
    assert _log(home, "org.yml") == ["the home's definitions, first tracked"]
    (home / "org.yml").write_text("teams: {g: {}}\n")
    assert defs.commit("edited by hand", home=home) is True
    assert _log(home, "org.yml")[0] == "edited by hand"
    (home / "org.yml").unlink()
    assert defs.commit("edited by hand", home=home) is True, "a removal is a change"
    assert "org.yml" not in _tracked(home)


def test_a_home_that_is_no_work_tree_commits_nothing(tmp_path):
    (tmp_path / "settings.yml").write_text("a: 1\n")
    assert defs.commit("settings: a 1 → 2", home=tmp_path) is False
    assert not (tmp_path / ".git").exists()


def test_the_settings_message_names_each_change():
    before = {"usage_gate": {"grind": {"week": 30}}, "teams": {"g": {"until": "x"}}}
    after = {"usage_gate": {"grind": {"week": 20}}, "teams": {"g": {"balance": {"prs": 8}}}}
    assert defs.settings_message(before, after) == (
        "settings: teams.g.balance.prs none → 8; teams.g.until x → none; usage_gate.grind.week 30 → 20"
    )
    assert defs.settings_message({}, {}) == "settings: rewritten, unchanged"
    assert len(defs.settings_message({}, {"k": "v" * 500})) == len("settings: ") + 200


async def test_set_settings_leaves_one_commit_and_a_failed_commit_leaves_the_write(agent, monkeypatch):
    await park_ticks(agent)
    home = paths.home()
    defs.init(home)
    async with LocalClient() as person:
        await person.call("set_settings", person={"open_in": "tab"})
        await _committed(agent)
        assert _log(home, "settings.yml")[0] == "settings: person.open_in none → tab"

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(defs, "commit", boom)
        got = await person.call("set_settings", person={"open_in": "window"})
        await _committed(agent)
        assert got["person"]["open_in"] == "window", "the write stands"
    assert "window" in (home / "settings.yml").read_text()
    assert len(_log(home, "settings.yml")) == 1, "the failed commit made none"



async def test_a_wedged_commit_holds_no_save(agent, monkeypatch):
    """The commit after `set_settings` is detached, as the tick's is: a git that hangs holds the
    history back, never the reply (the techlead's read of #800)."""
    await park_ticks(agent)
    defs.init(paths.home())
    release = threading.Event()
    monkeypatch.setattr(defs, "commit", lambda *a, **k: release.wait(10) and False)
    try:
        async with LocalClient() as person:
            got = await asyncio.wait_for(person.call("set_settings", person={"open_in": "tab"}), 5)
        assert got["person"]["open_in"] == "tab" and agent._bg, "answered while the commit still runs"
    finally:
        release.set()
    await _committed(agent)

async def test_commit_defs_is_a_persons_own_and_the_homes(agent):
    await park_ticks(agent)
    home = paths.home()
    (home / "org.yml").write_text("teams: {}\n")
    defs.init(home)
    (home / "org.yml").write_text("teams: {g: {}}\n")
    async with LocalClient(caller="ao-t-someone") as session:
        with pytest.raises(AgentError, match="a person's own"):
            await session.call("commit_defs", message="org: g added x")
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="needs the act's words"):
            await person.call("commit_defs", message="  ")
        assert await person.call("commit_defs", message="org: g added x (grinder)") == {"committed": True}
        assert await person.call("commit_defs", message="org: again") == {"committed": False}
    assert _log(home, "org.yml")[0] == "org: g added x (grinder)"
    assert "commit_defs" in modes.HOME_EDITS


async def test_a_hand_edit_is_committed_on_the_reports_cadence(agent):
    await park_ticks(agent)
    home = paths.home()
    (home / "profiles.yml").write_text("grind: {}\n")
    defs.init(home)
    (home / "profiles.yml").write_text("grind: {adapter: claude-code}\n")
    agent._defs_read_at = datetime.min.replace(tzinfo=UTC)  # the cadence due: the fixture's first tick read it
    await agent.tick()
    await agent._defs_task
    assert _log(home, "profiles.yml")[0] == "edited by hand"
    task = agent._defs_task
    await agent.tick()
    assert agent._defs_task is task, "not again inside the cadence"


def test_service_install_makes_the_home_a_work_tree_at_the_home_only(tmp_path, monkeypatch):
    home = tmp_path / "h"
    monkeypatch.setattr(hosts, "home_name", lambda: "elsewhere")
    assert service._home_history(str(home)) is None and not (home / ".git").exists()
    monkeypatch.setattr(hosts, "home_name", lambda: hosts.local_host().name)
    assert service._home_history(str(home)) == str(home / ".git")
    assert service._home_history(str(home)) is None, "already one"
