"""TD-217 slice 2, design §6 *Keeping a team running* rule 7's first half: a replay fills the brief
again from what it was made from (`prompt_from`), a file in a checkout read as merged
(`origin/<default>:<path>`) and never from the working tree; a file that cannot be read, or no
`prompt_from`, replays the stored prompt and the `restarts` entry says `prompt: stored`. The record
carries `brief: {at, sources: [{path, sha}]}` from each create."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import park_ticks

from sessionorc import brief, paths
from sessionorc.agent import RESTART_SETTLE
from sessionorc.agent_common import BRIEF_SETTLE
from sessionorc.client import LocalClient


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "docs").mkdir(parents=True)
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    return r


def _merge(repo: Path, text: str) -> None:
    """A commit of the supplement, then `origin/main` moved to it: what a merged PR and a fetch leave."""
    (repo / "docs" / "b.md").write_text(text)
    _git(repo, "add", "docs/b.md")
    _git(repo, "commit", "-q", "-m", "brief")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")


def _made_from(tmp_path: Path, repo: Path) -> dict:
    base = tmp_path / "grinder.md"  # the installed template: outside any checkout, read from disk
    base.write_text("base: {repo} / lane {lane}\n")
    return {
        "base": str(base),
        "slots": {"{repo}": {"file": str(repo / "docs" / "b.md")}, "{lane}": {"text": "TD-1"}},
        "prefix": "P\n",
    }


@pytest.mark.unit
def test_fill_reads_a_checkouts_file_as_merged_and_the_rest_from_disk(tmp_path):
    repo = _repo(tmp_path)
    _merge(repo, "merged {lane}\n")
    (repo / "docs" / "b.md").write_text("an unmerged edit\n")  # the working tree is never a running brief
    made = _made_from(tmp_path, repo)
    text, sources = brief.fill(made)
    assert text == "P\nbase: merged TD-1 / lane TD-1\n"
    assert [s["path"] for s in sources] == [made["base"], str(repo / "docs" / "b.md")]
    assert sources[1]["sha"] == _git(repo, "rev-parse", "origin/main:docs/b.md")  # git's own blob id
    assert sources[0]["sha"] == _git(tmp_path, "hash-object", str(tmp_path / "grinder.md"))
    # a client's create reads what it read: the working tree
    assert brief.fill(made, merged=False)[0] == "P\nbase: an unmerged edit / lane TD-1\n"
    # an empty supplement is the `none` word, as the client fills it
    _merge(repo, "  \n")
    assert brief.fill(made)[0] == "P\nbase: none / lane TD-1\n"
    assert brief.record(made)["sources"][1]["sha"] == _git(repo, "rev-parse", "origin/main:docs/b.md")


@pytest.mark.unit
def test_what_cannot_be_read_is_said_and_never_guessed(tmp_path):
    repo = _repo(tmp_path)
    _merge(repo, "x\n")
    (repo / "docs" / "new.md").write_text("only here\n")  # in the checkout, never merged
    made = _made_from(tmp_path, repo)
    made["slots"]["{repo}"] = {"file": str(repo / "docs" / "new.md")}
    with pytest.raises(brief.Unreadable, match="docs/new.md is not on origin/"):
        brief.fill(made)
    with pytest.raises(brief.Unreadable):
        brief.fill({**made, "base": str(tmp_path / "gone.md")})
    with pytest.raises(brief.Unreadable, match="no base"):
        brief.fill({"slots": {}})
    assert brief.record({**made, "base": str(tmp_path / "gone.md")}) is None


def _crash(agent, sid: str) -> None:
    rec = agent.sessions[sid]
    rec.state, rec.pane, rec.exit_code = "exited", True, 1
    agent.store.save(rec)


def _launched(sid: str) -> str:
    return json.loads((paths.launch_dir() / f"{sid}.json").read_text())["prompt"]


@pytest.mark.integration
async def test_a_replay_hands_the_merged_brief_and_the_record_names_what_it_read(agent, tmp_path):
    await park_ticks(agent)
    repo = _repo(tmp_path)
    _merge(repo, "first {lane}\n")
    made = _made_from(tmp_path, repo)
    work = tmp_path / "work"
    work.mkdir()
    async with LocalClient() as person:
        sid = (
            await person.call(
                "create",
                name="w",
                dir=str(work),
                adapter="shell",
                argv=["bash", "--norc", "--noprofile"],
                unattended=True,
                supervised=True,
                prompt="P\nbase: first TD-1 / lane TD-1\n",
                prompt_from=made,
            )
        )["id"]
        first = agent.sessions[sid].brief
        assert first and [s["path"] for s in first["sources"]] == [made["base"], str(repo / "docs" / "b.md")]

        _merge(repo, "second {lane}\n")  # a merged change to the supplement
        (repo / "docs" / "b.md").write_text("a branch's edit\n")  # and an unmerged one on top
        _crash(agent, sid)
        now = datetime.now(UTC)
        await agent._keep_running(now)
        new = agent.sessions[sid]
        assert [r["why"] for r in new.restarts] == ["crash"] and "prompt" not in new.restarts[0]
        assert _launched(sid) == "P\nbase: second TD-1 / lane TD-1\n"  # what `create` was handed
        assert new.brief["sources"][1]["sha"] == _git(repo, "rev-parse", "origin/main:docs/b.md")
        assert new.brief["sources"][1]["sha"] != first["sources"][1]["sha"]

        # a file that cannot be read now: the stored prompt, and the entry says so
        Path(made["base"]).unlink()
        _crash(agent, sid)
        await agent._keep_running(now + RESTART_SETTLE + timedelta(seconds=1))
        again = agent.sessions[sid]
        assert [r["why"] for r in again.restarts] == ["crash", "crash"] and again.restarts[1]["prompt"] == "stored"
        assert again.brief is None  # what the create would record is not what the prompt was made from
        assert _launched(sid) == "P\nbase: second TD-1 / lane TD-1\n"
        await person.call("kill", id=sid)


@pytest.mark.unit
def test_changed_names_what_reads_otherwise_as_merged_and_skips_what_cannot_be_read(tmp_path):
    repo = _repo(tmp_path)
    _merge(repo, "one\n")
    made = _made_from(tmp_path, repo)
    rec = brief.record(made)
    assert brief.changed(rec)[0] == []
    (repo / "docs" / "b.md").write_text("an unmerged edit\n")  # the working tree is no change
    assert brief.changed(rec)[0] == []
    _merge(repo, "two\n")
    paths_, shas, _whole = brief.changed(rec)
    assert paths_ == [str(repo / "docs" / "b.md")] and shas[1] == _git(repo, "rev-parse", "origin/main:docs/b.md")
    Path(made["base"]).unlink()  # unreadable now: nothing is claimed about it
    assert brief.changed(rec)[0] == [str(repo / "docs" / "b.md")] and brief.changed(rec)[2] is False
    assert brief.changed(None) == ([], (), True)


@pytest.mark.integration
async def test_a_merged_change_marks_brief_changed_after_the_settle(agent, tmp_path, monkeypatch):
    """Rule 7's mark (TD-217 slice 3): a source that reads otherwise as merged marks the record once
    the difference has stood `BRIEF_SETTLE`; another merge restarts the settle and keeps a mark
    already made; the files as recorded again take it away."""
    await park_ticks(agent)
    repo = _repo(tmp_path)
    _merge(repo, "first\n")
    made = _made_from(tmp_path, repo)
    work = tmp_path / "work"
    work.mkdir()
    async with LocalClient() as person:
        sid = (
            await person.call(
                "create",
                name="w",
                dir=str(work),
                adapter="shell",
                argv=["bash", "--norc", "--noprofile"],
                unattended=True,
                supervised=True,
                prompt="x",
                prompt_from=made,
            )
        )["id"]
        rec = agent.sessions[sid]
        t0 = datetime.now(UTC)
        await agent._brief_pass(t0)
        assert rec.brief_changed is None and sid not in agent._brief_differs
        _merge(repo, "second\n")
        await agent._brief_pass(t0)
        await agent._brief_pass(t0 + BRIEF_SETTLE - timedelta(seconds=1))
        assert rec.brief_changed is None  # shorter than the settle: nothing
        await agent._brief_pass(t0 + BRIEF_SETTLE)
        b = str(repo / "docs" / "b.md")
        assert rec.brief_changed == {"at": t0.isoformat().replace("+00:00", "Z"), "paths": [b]}
        got = (await person.call("get", id=sid))["brief_changed"]
        assert got["paths"] == [b]
        # another merge: the settle restarts, and the mark already made stands
        _merge(repo, "third\n")
        t1 = t0 + BRIEF_SETTLE + timedelta(minutes=1)
        await agent._brief_pass(t1)
        assert agent._brief_differs[sid][1] == t1 and rec.brief_changed["paths"] == [b]
        # a source that cannot be read this time: the mark and the settle stand
        base = Path(made["base"])
        kept = base.read_text()
        base.unlink()
        await agent._brief_pass(t1 + timedelta(seconds=30))
        assert rec.brief_changed["paths"] == [b] and agent._brief_differs[sid][1] == t1
        base.write_text(kept)
        # a record forgotten while the files were read is never saved back
        real = brief.changed

        def forget_meanwhile(bf):
            agent.sessions.pop(sid, None)
            return real(bf)

        saved = []
        with monkeypatch.context() as m:
            m.setattr(brief, "changed", forget_meanwhile)
            m.setattr(agent, "_save", lambda r: saved.append(r.id))
            await agent._brief_pass(t1 + timedelta(seconds=40))
        assert saved == []
        agent.sessions[sid] = rec
        # the files read as recorded again: the mark goes
        _merge(repo, "first\n")
        await agent._brief_pass(t1 + timedelta(minutes=1))
        assert rec.brief_changed is None and sid not in agent._brief_differs
        await person.call("kill", id=sid)


@pytest.mark.integration
async def test_a_prompt_typed_whole_replays_as_stored(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = (
            await person.call(
                "create",
                name="w",
                dir=str(tmp_path),
                adapter="shell",
                argv=["bash", "--norc", "--noprofile"],
                unattended=True,
                supervised=True,
                prompt="just this",
            )
        )["id"]
        assert agent.sessions[sid].brief is None
        _crash(agent, sid)
        await agent._keep_running(datetime.now(UTC))
        new = agent.sessions[sid]
        assert new.restarts[0]["prompt"] == "stored" and _launched(sid) == "just this"
        await person.call("kill", id=sid)


async def test_a_member_on_a_node_replays_its_stored_prompt(agent, tmp_path):
    """The techlead's read of #745: a member on a node replays as stored — its `prompt_from` names
    files on that host, and the home reads its own disk — even when the home could read a file of
    that path; the `restarts` entry says `prompt: stored`."""
    from types import SimpleNamespace

    repo = _repo(tmp_path)
    _merge(repo, "merged\n")
    made = _made_from(tmp_path, repo)
    params = {"prompt": "as handed", "prompt_from": made}
    entry: dict = {}
    node = SimpleNamespace(id="ao-w", host=f"{agent.host}-node")
    assert await agent._refill_prompt(node, params, entry) is None
    assert entry == {"prompt": "stored"} and params["prompt"] == "as handed"
    # the same launch record on the home refills from the merged file
    entry = {}
    here = SimpleNamespace(id="ao-w", host=agent.host)
    assert (await agent._refill_prompt(here, params, entry))["sources"]
    assert entry == {} and params["prompt"] == "P\nbase: merged / lane TD-1\n"


@pytest.mark.integration
async def test_a_scheduled_start_keeps_prompt_from_and_fills_it_at_the_instant(agent, tmp_path):
    """The techlead's note on #714: a create with `start_at` keeps `prompt_from` through
    `launch_params`, and the start — a replay — reads the files as they are then."""
    await park_ticks(agent)
    repo = _repo(tmp_path)
    _merge(repo, "then {lane}\n")
    made = _made_from(tmp_path, repo)
    work = tmp_path / "work"
    work.mkdir()
    async with LocalClient() as person:
        sid = (
            await person.call(
                "create",
                name="w",
                dir=str(work),
                adapter="shell",
                argv=["bash", "--norc", "--noprofile"],
                unattended=True,
                prompt="P\nbase: then TD-1 / lane TD-1\n",
                prompt_from=made,
                start_at=(datetime.now(UTC) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
            )
        )["id"]
        assert agent._read_launch(sid)["prompt_from"] == made
        _merge(repo, "now {lane}\n")
        await person.call("set_start", id=sid, start_at="now")
        await agent._keep_running(datetime.now(UTC) + timedelta(seconds=1))
        new = agent.sessions[sid]
        assert [r["why"] for r in new.restarts] == ["start"] and "prompt" not in new.restarts[0]
        assert _launched(sid) == "P\nbase: now TD-1 / lane TD-1\n"
        await person.call("kill", id=sid)


@pytest.mark.unit
def test_a_crlf_file_fills_as_the_client_reads_it(tmp_path):
    """The review of #745: the client reads with universal newlines, so the replay does too."""
    base = tmp_path / "b.md"
    base.write_bytes(b"one\r\ntwo {x}\r\n")
    assert brief.fill({"base": str(base), "slots": {"{x}": {"text": "X"}}})[0] == "one\ntwo X\n"
