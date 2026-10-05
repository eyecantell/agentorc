"""TD-263, design §6 *Pull*: the home's pass fast-forwards every registered main checkout when git
allows and no session in its root is mid-turn — only `git fetch` and `git merge --ff-only`, the
outcome a reading under `pulls` on `host`, and `repos.<repo>.pull: false` leaving it to the person."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import park_ticks

from sessionorc import adapters, hosts, promote, settings
from sessionorc.adapters import ExternalSession
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import Session

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _git(root: Path, *args: str) -> str:
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), **GIT_ENV}
    cp = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=env)
    return cp.stdout.strip()


def _commit(root: Path, name: str, text: str | None = None) -> str:
    (root / name).write_text(text if text is not None else name)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", name)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A bare origin, the home's checkout of it on main (no `promote:` block — the pull does not need
    one), and `other`, a second clone whose pushes leave the checkout behind."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "remote", "add", "origin", str(origin))
    _commit(root, "a")
    _git(root, "push", "-q", "-u", "origin", "main")
    _git(root, "remote", "set-head", "origin", "main")
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(origin), str(other))
    return root, other


def _behind(other: Path, *names: str) -> str:
    for n in names:
        sha = _commit(other, n)
    _git(other, "push", "-q", "origin", "main")
    return sha


def test_a_checkout_behind_with_no_one_in_it_is_pulled(repo):
    root, other = repo
    before = _git(root, "rev-parse", "HEAD")
    head = _behind(other, "b", "c")
    r, fetched = promote.pull(root, True, None, NOW)
    assert (r["outcome"], r["from"], r["to"], r["commits"]) == ("pulled", before, head, 2)
    assert _git(root, "rev-parse", "HEAD") == head
    assert fetched == ""  # the fetch of main this pass, for the promote's readings to reuse
    r, _ = promote.pull(root, True, None, NOW)
    assert r["outcome"] == "current"


def test_a_session_mid_turn_is_waited_on_and_named(repo):
    root, other = repo
    before = _git(root, "rev-parse", "HEAD")
    _behind(other, "b")
    r, _ = promote.pull(root, True, "main", NOW)
    assert (r["outcome"], r["occupant"]) == ("waiting", "main")
    r, _ = promote.pull(root, True, "", NOW)
    assert (r["outcome"], r["occupant"], r["why"]) == ("waiting", None, "unreadable")
    assert _git(root, "rev-parse", "HEAD") == before


def test_what_git_does_not_allow_is_refused_and_says_why(repo):
    root, other = repo
    _behind(other, "b")
    _git(root, "checkout", "-q", "-b", "topic")
    r, _ = promote.pull(root, True, None, NOW)
    assert (r["outcome"], r["why"]) == ("refused", "on topic")
    _git(root, "checkout", "-q", "main")
    # a promote in flight: its run installs from this tree
    promote.repo_dir("repo").mkdir(parents=True)
    (promote.repo_dir("repo") / "inflight.json").write_text('{"sha": "x"}')
    assert promote.pull(root, True, None, NOW)[0]["why"] == "a promote in flight"
    promote.clear("repo", "inflight")
    # a git operation under way
    lock = root / ".git" / "index.lock"
    lock.write_text("")
    assert "a git operation under way (index.lock)" in promote.pull(root, True, None, NOW)[0]["why"]
    lock.unlink()
    # a dirty file the merge would touch: git's own first line, nothing overwritten
    (root / "b").write_text("mine")
    r, _ = promote.pull(root, True, None, NOW)
    assert r["outcome"] == "refused" and "would be overwritten" in r["why"]
    assert (root / "b").read_text() == "mine"


def test_a_checkout_with_a_commit_of_its_own_is_left_alone(repo):
    root, other = repo
    mine = _commit(root, "local")
    r, _ = promote.pull(root, True, None, NOW)
    assert (r["outcome"], r["why"]) == ("refused", "1 commit of its own, not on origin — the person's to push")
    _behind(other, "b")  # diverged: still the person's
    assert promote.pull(root, True, None, NOW)[0]["outcome"] == "refused"
    assert _git(root, "rev-parse", "HEAD") == mine


def test_a_dirty_file_elsewhere_stops_nothing(repo):
    root, other = repo
    (root / "notes.md").write_text("an unsaved memory note")
    head = _behind(other, "b")
    r, _ = promote.pull(root, True, None, NOW)
    assert r["outcome"] == "pulled" and _git(root, "rev-parse", "HEAD") == head
    assert (root / "notes.md").read_text() == "an unsaved memory note"


def test_auto_stash_in_the_persons_config_is_never_used(repo):
    root, other = repo
    _git(root, "config", "merge.autoStash", "true")
    _behind(other, "b")
    (root / "b").write_text("mine")  # untracked, and the merge would write it
    r, _ = promote.pull(root, True, None, NOW)
    assert r["outcome"] == "refused" and (root / "b").read_text() == "mine"
    assert not _git(root, "stash", "list")


def test_the_promote_reuses_the_pulls_fetch_of_main(repo, monkeypatch):
    root, other = repo
    head = _behind(other, "b")
    _, fetched = promote.pull(root, True, "main", NOW)  # fetches, and waits on the occupant
    calls = []
    real = promote._git
    monkeypatch.setattr(promote, "_git", lambda r, *a, **k: calls.append(a) or real(r, *a, **k))
    assert promote.read_main(root, fetched)["main"] == head  # the pull's fetch went through: none here
    assert promote.read_main(root, "git fetch: no route")["fetch_why"] == "git fetch: no route"
    assert not [a for a in calls if a[0] == "fetch"]
    promote.read_main(root)  # no pull this pass: the promote fetches for itself
    assert [a for a in calls if a[0] == "fetch"] == [("fetch", "-q", "origin", "main")]


def test_a_default_branch_other_than_main_is_followed_and_main_is_not_fetched_for_the_promote(repo):
    root, other = repo
    _git(other, "checkout", "-q", "-b", "trunk")
    _git(other, "push", "-q", "origin", "trunk")
    _git(root, "fetch", "-q", "origin")
    _git(root, "checkout", "-q", "-b", "trunk", "origin/trunk")
    _git(root, "remote", "set-head", "origin", "trunk")
    _commit(other, "t")
    _git(other, "push", "-q", "origin", "trunk")
    r, fetched = promote.pull(root, True, None, NOW)
    assert r["outcome"] == "pulled" and fetched is None  # the promote fetches main itself


def test_off_reads_off_and_fetches_nothing(repo):
    root, other = repo
    before = _git(root, "rev-parse", "HEAD")
    _behind(other, "b")
    r, fetched = promote.pull(root, False, None, NOW)
    assert (r["outcome"], fetched) == ("off", None)
    assert _git(root, "rev-parse", "HEAD") == before


def test_the_pass_covers_every_root_and_one_failure_stops_none(repo, tmp_path):
    root, other = repo
    head = _behind(other, "b")
    gone = tmp_path / "gone"
    gone.mkdir()
    readings, fetched = promote.pulls([str(gone), str(root)], {}, {}, NOW)
    assert readings["repo"]["outcome"] == "pulled" and _git(root, "rev-parse", "HEAD") == head
    assert readings["gone"]["outcome"] == "refused"
    assert fetched[str(root)] == "" and fetched[str(gone)].startswith("git fetch")  # a failed fetch's why, reused


def test_the_switch_is_a_repo_setting_absent_is_true():
    assert settings.parse_repo({"pull": False}) == {"pull": False}
    assert settings.repos({"repos": {"r": {"promote": {"auto": True}, "pull": True}}}) == {
        "r": {"promote": {"auto": True}, "pull": True}
    }
    with pytest.raises(ValueError, match="pull is true or false"):
        settings.parse_repo({"pull": "no"})


# ── at the home: the read of the root, the pass, the reading on `host` ──────────────────────────


def _rec(name: str, directory: Path, state: str, adapter: str = "shell") -> Session:
    return Session(id=f"ao-x-{name}", name=name, kind="interactive", adapter=adapter, dir=str(directory), state=state)


async def test_the_root_is_at_rest_only_when_every_session_there_is(agent, repo, monkeypatch):
    root, _ = repo
    await park_ticks(agent)
    monkeypatch.setattr(adapters, "external_sessions", lambda: [])
    assert agent.pull_occupant(root) is None  # no session at all is idle
    for state in ("idle", "exited", "closed"):
        agent.sessions["ao-x-main"] = _rec("main", root, state, adapter="claude-code")
        assert agent.pull_occupant(root) is None
    for state in ("working", "needs-you", "stalled?", "limited", "unreachable"):
        agent.sessions["ao-x-main"] = _rec("main", root, state, adapter="claude-code")
        assert agent.pull_occupant(root) == "main"
    agent.sessions["ao-x-main"] = _rec("main", root, "idle", adapter="claude-code")
    agent.sessions["ao-x-sh"] = _rec("sh", root, "working")  # a shell's foreground command may be git
    assert agent.pull_occupant(root) == "sh"
    del agent.sessions["ao-x-sh"]
    agent.sessions["ao-x-else"] = _rec("else", root.parent, "working")  # another directory
    assert agent.pull_occupant(root) is None

    def ext(status):
        return [ExternalSession(adapter="claude-code", cwd=str(root), name="vscode", tool_id="t-1", status=status)]

    for status, want in (("idle", None), ("busy", "vscode"), ("shell", "vscode"), (None, "")):
        monkeypatch.setattr(adapters, "external_sessions", lambda status=status: ext(status))
        assert agent.pull_occupant(root) == want


async def test_the_home_pass_pulls_and_host_carries_the_reading(agent, repo, monkeypatch):
    root, other = repo
    await park_ticks(agent)
    monkeypatch.setattr(adapters, "external_sessions", lambda: [])
    reg = hosts.default_repos_registry()
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(f"{root}\n")
    agent.sessions["ao-x-main"] = _rec("main", root, "working", adapter="claude-code")
    before = _git(root, "rev-parse", "HEAD")
    head = _behind(other, "b")
    agent._promote_read_at = float("-inf")
    await agent._refresh_promotes()
    async with LocalClient() as person:
        got = (await person.call("host"))["pulls"]["repo"]
    assert (got["outcome"], got["occupant"]) == ("waiting", "main")
    assert _git(root, "rev-parse", "HEAD") == before
    agent.sessions["ao-x-main"].state = "idle"
    agent._promote_read_at = float("-inf")
    await agent._refresh_promotes()
    async with LocalClient() as person:
        got = (await person.call("host"))["pulls"]["repo"]
    assert (got["outcome"], got["commits"]) == ("pulled", 1)
    assert _git(root, "rev-parse", "HEAD") == head
    # the person's switch: off leaves the checkout to them
    async with LocalClient() as person:
        await person.call("set_settings", repos={"repo": {"pull": False}})
    assert settings.load()["repos"] == {"repo": {"pull": False}}
    _behind(other, "c")
    agent._promote_read_at = float("-inf")
    await agent._refresh_promotes()
    assert agent._pulls["repo"]["outcome"] == "off" and _git(root, "rev-parse", "HEAD") == head
    async with LocalClient(caller="ao-some-worker") as worker:
        with pytest.raises(AgentError, match="a person's own"):
            await worker.call("set_settings", repos={"repo": {"pull": True}})


def test_a_failed_fetch_says_what_went_wrong_not_gits_advice(repo):
    """TD-322: git's fetch error ends *… and the repository exists.*; the reading keeps its first
    `fatal:` line, which names the remote."""
    root, _ = repo
    _git(root, "remote", "set-url", "origin", str(root.parent / "nowhere.git"))
    out, why = promote._git(root, "fetch", "origin")
    assert out is None and why.startswith("git fetch: fatal:") and "nowhere.git" in why
    assert "repository exists" not in why
