"""TD-132 slice 1, design §6 *Promote*: the `promote:` block read by key alone, the three readings, the
three preconditions, the run detached with its intent file written first, its outcome read from
`check` and never from its exit code, and the home's pass that keeps `promotes` on `host` and files the
*promoted …* note."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import park_ticks

from sessionorc import hosts, promote
from sessionorc.client import AgentError, LocalClient

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(root: Path, *args: str) -> str:
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), **GIT_ENV}
    cp = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=env)
    return cp.stdout.strip()


def _commit(root: Path, name: str) -> str:
    (root / name).write_text(name)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", name)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A bare origin and the home's checkout of it on main, clean; `live` is a file outside the
    checkout that `check` prints and `run` writes — the promote made of two shell lines."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "remote", "add", "origin", str(origin))
    live = tmp_path / "live"
    (root / ".agentorc.yml").write_text(
        f"promote:\n  run: git rev-parse HEAD > {live}\n  check: cat {live}\n"
    )  # fmt: skip
    first = _commit(root, "a")
    _git(root, "push", "-q", "origin", "main")
    live.write_text(first + "\n")
    monkeypatch.setattr(promote, "read_checks", lambda root, sha: ("green", ""))
    return root


def _merge(root: Path, name: str) -> str:
    """A merge to main as the home sees it: pushed to origin, and the checkout fast-forwarded (a
    checkout left behind reads as not on main's head, precondition 1)."""
    sha = _commit(root, name)
    _git(root, "push", "-q", "origin", "main")
    return sha


def _finished(pid: int) -> bool:
    """Exited — a zombie or gone — without reaping it: the pass under test is what must reap."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] == "Z"
    except OSError:
        return True


def _wait_gone(pid: int) -> None:
    deadline = time.monotonic() + 10
    while not _finished(pid) and time.monotonic() < deadline:
        time.sleep(0.05)


def test_the_block_is_read_by_key_alone(tmp_path):
    assert promote.block(tmp_path) is None  # no file
    (tmp_path / ".agentorc.yml").write_text("roles: {}\n")
    assert promote.block(tmp_path) is None  # no key
    (tmp_path / ".agentorc.yml").write_text("promote:\n  run: x\n  check: y\nroles: {nonsense: 1}\n")
    assert promote.block(tmp_path) == {"run": "x", "check": "y"}
    (tmp_path / ".agentorc.yml").write_text("promote:\n  run: x\n")
    with pytest.raises(ValueError, match="needs check"):
        promote.block(tmp_path)
    (tmp_path / ".agentorc.yml").write_text("promote: [x]\n")
    with pytest.raises(ValueError, match="mapping"):
        promote.block(tmp_path)
    (tmp_path / ".agentorc.yml").write_text("promote: {run: x\n")
    with pytest.raises(ValueError, match="cannot be read"):
        promote.block(tmp_path)


def test_check_is_a_full_commit_or_the_reason(tmp_path):
    assert promote.read_live(tmp_path, "echo " + "a" * 40) == ("a" * 40, "")
    assert promote.read_live(tmp_path, "echo not deployed >&2; exit 1") == (None, "not deployed")
    assert promote.read_live(tmp_path, "echo abc") == (None, "check did not print one full commit")


def test_main_is_read_after_a_fetch_and_the_tree_is_precondition_one(checkout):
    r = promote.read_main(checkout)
    assert r["main"] == _git(checkout, "rev-parse", "HEAD") and r["tree"] is None and "fetch_why" not in r
    (checkout / "a").write_text("changed")
    assert promote.read_main(checkout)["tree"] == "the checkout has 1 uncommitted change"
    _git(checkout, "checkout", "-q", "--", "a")
    _git(checkout, "checkout", "-q", "-b", "td1-x")
    _commit(checkout, "b")
    assert promote.read_main(checkout)["tree"].startswith("the checkout is on td1-x at ")


def test_the_checks_verdict(monkeypatch, tmp_path):
    def fake(runs, rc=0, err=""):
        out = json.dumps({"check_runs": runs})
        monkeypatch.setattr(
            promote.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, rc, stdout=out, stderr=err)
        )

    done = {"status": "completed", "conclusion": "success"}
    fake([done, {"status": "completed", "conclusion": "skipped"}])
    assert promote.read_checks(tmp_path, "f" * 40) == ("green", "")
    fake([done, {"status": "in_progress", "conclusion": None}])
    assert promote.read_checks(tmp_path, "f" * 40) == ("pending", "")
    fake([{"status": "in_progress"}, {"status": "completed", "conclusion": "failure"}])
    assert promote.read_checks(tmp_path, "f" * 40) == ("failed", "")
    fake([])
    assert promote.read_checks(tmp_path, "f" * 40) == ("unknown", "no check runs on fffffff")
    fake([], rc=1, err="HTTP 403: API rate limit exceeded")
    assert promote.read_checks(tmp_path, "f" * 40) == ("unknown", "HTTP 403: API rate limit exceeded")


def test_the_preconditions_in_order_and_the_press_goes_through_the_checks():
    ok = {"main": "m", "tree": None, "checks": "green"}
    assert promote.unmet(ok) is None
    assert promote.unmet({**ok, "tree": "dirty"}) == ("tree", "dirty")
    assert promote.unmet({"main": "m", "checks": "green"})[0] == "tree"  # not read yet
    assert promote.unmet({**ok, "checks": "pending"}) == ("checks", "checks on main are pending")
    assert promote.unmet({**ok, "checks": "pending"}, press=True) is None
    assert promote.unmet({**ok, "inflight": {"sha": "abcdef12"}}, press=True)[0] == "inflight"
    assert promote.unmet({**ok, "failed": {"sha": "abcdef12"}}, press=True)[0] == "failed"


def test_by_hand_the_pass_reads_and_starts_nothing(checkout):
    _merge(checkout, "b")
    readings, notes, bad = promote.survey([str(checkout)], {}, True, {}, datetime.now(UTC))
    r = readings["repo"]
    assert (r["ahead"], r["checks"], r["auto"], r["inflight"], r["unmet"], notes, bad) == (
        1,
        "green",
        False,
        None,
        None,
        [],
        {},
    )
    assert r["live"] != r["main"]


def test_auto_waits_for_main_to_settle(checkout, monkeypatch):
    _merge(checkout, "b")
    readings, _, _ = promote.survey([str(checkout)], {}, True, {"repo": True}, datetime.now(UTC))
    assert readings["repo"]["inflight"] is None  # main moved a moment ago
    later = datetime.now(UTC) + timedelta(seconds=promote.PROMOTE_SETTLE + 1)
    readings, _, _ = promote.survey([str(checkout)], readings, True, {"repo": True}, later)
    assert readings["repo"]["inflight"]["by"] == "auto"


def test_auto_promotes_once_and_the_outcome_is_read_from_check(checkout, monkeypatch):
    monkeypatch.setattr(promote, "PROMOTE_SETTLE", 0.0)
    _merge(checkout, "b")
    main = _merge(checkout, "c")
    readings, notes, _ = promote.survey([str(checkout)], {}, True, {"repo": True}, datetime.now(UTC))
    intent = readings["repo"]["inflight"]
    assert intent["sha"] == main and intent["ahead"] == 2 and notes == []
    assert json.loads((promote.repo_dir("repo") / "inflight.json").read_text())["pid"] == intent["pid"]
    _wait_gone(intent["pid"])
    readings, notes, _ = promote.survey([str(checkout)], readings, False, {"repo": True}, datetime.now(UTC))
    assert notes == [f"promoted `repo` `{main[:7]}` — 2 commits"]
    assert readings["repo"]["inflight"] is None and readings["repo"]["live"] == main
    assert not (promote.repo_dir("repo") / "inflight.json").exists()
    assert (promote.repo_dir("repo") / f"{main}.log").exists()
    with pytest.raises(ChildProcessError):  # reaped: no zombie left behind
        os.waitpid(intent["pid"], os.WNOHANG)
    readings, notes, _ = promote.survey([str(checkout)], readings, True, {"repo": True}, datetime.now(UTC))
    assert readings["repo"]["inflight"] is None and notes == []  # live is main: nothing to do


def test_a_run_that_leaves_live_elsewhere_fails_and_stops_the_repo(checkout, monkeypatch, tmp_path):
    monkeypatch.setattr(promote, "PROMOTE_SETTLE", 0.0)
    (checkout / ".agentorc.yml").write_text(
        f"promote:\n  run: echo it broke; exit 3\n  check: cat {tmp_path / 'live'}\n"
    )
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-q", "-m", "block")
    _git(checkout, "push", "-q", "origin", "main")
    readings, _, _ = promote.survey([str(checkout)], {}, True, {"repo": True}, datetime.now(UTC))
    _wait_gone(readings["repo"]["inflight"]["pid"])
    readings, notes, _ = promote.survey([str(checkout)], readings, False, {"repo": True}, datetime.now(UTC))
    f = readings["repo"]["failed"]
    assert notes == [] and f["why"].startswith("the run ended and live is ") and f["tail"] == ["it broke"]
    assert f["exit"] == 3
    assert readings["repo"]["unmet"]["name"] == "failed"
    readings, _, _ = promote.survey([str(checkout)], readings, True, {"repo": True}, datetime.now(UTC))
    assert readings["repo"]["inflight"] is None  # nothing further until the person clears it
    promote.clear("repo", "failed")
    readings, _, _ = promote.survey([str(checkout)], readings, True, {"repo": True}, datetime.now(UTC))
    assert readings["repo"]["inflight"] is not None


def test_a_run_past_the_bound_is_killed_and_failed(checkout, monkeypatch, tmp_path):
    monkeypatch.setattr(promote, "PROMOTE_SETTLE", 0.0)
    (checkout / ".agentorc.yml").write_text(f"promote:\n  run: sleep 60\n  check: cat {tmp_path / 'live'}\n")
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-q", "-m", "block")
    _git(checkout, "push", "-q", "origin", "main")
    readings, _, _ = promote.survey([str(checkout)], {}, True, {"repo": True}, datetime.now(UTC))
    pid = readings["repo"]["inflight"]["pid"]
    assert promote.alive(pid)
    later = datetime.now(UTC) + timedelta(seconds=promote.PROMOTE_BOUND + 1)
    readings, _, _ = promote.survey([str(checkout)], readings, False, {"repo": True}, later)
    assert readings["repo"]["failed"]["why"] == "still running after 20 minutes: killed"
    _wait_gone(pid)
    assert not promote.alive(pid)


def test_a_malformed_block_is_skipped_never_fatal(checkout, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / ".agentorc.yml").write_text("promote: {run: x}\n")
    readings, _, bad = promote.survey(
        [str(other), str(checkout), str(tmp_path / "gone")], {}, True, {}, datetime.now(UTC)
    )
    assert list(readings) == ["repo"] and list(bad) == [str(other)]


def _register(*roots: Path) -> None:
    reg = Path(hosts.DEFAULT_REPOS_REGISTRY)
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text("".join(f"{r}\n" for r in roots))


async def test_a_fresh_agent_concludes_a_run_it_did_not_start(agent, checkout, monkeypatch):
    """This repo's own promote restarts the agent mid-run: a fresh agent finding `inflight.json` with
    `check` reading the wanted commit files the note and clears the file."""
    await park_ticks(agent)
    main = _merge(checkout, "b")
    promote.repo_dir("repo").mkdir(parents=True)
    intent = {"sha": main, "at": datetime.now(UTC).isoformat(), "pid": 999999999, "log": "x", "by": "auto", "ahead": 1}
    (promote.repo_dir("repo") / "inflight.json").write_text(json.dumps(intent))
    (checkout.parent / "live").write_text(main)  # the run finished under the old agent
    _register(checkout)
    agent._promote_read_at = float("-inf")  # the fixture's first tick took the read before the registry
    await agent._refresh_promotes()
    assert not (promote.repo_dir("repo") / "inflight.json").exists()
    assert [e.text for e in agent.person_inbox if e.from_ == "system"] == [f"promoted `repo` `{main[:7]}` — 1 commit"]
    async with LocalClient() as person:
        got = (await person.call("host"))["promotes"]["repo"]
    assert (got["live"], got["main"], got["ahead"], got["inflight"], got["unmet"]) == (main, main, 0, None, None)


# ── slice 2: the press, Dismiss's half, and `ao promote` (§4.7, §6) ────────────────────────────


async def test_the_press_is_a_persons_and_refused_to_a_session(agent, checkout):
    await park_ticks(agent)
    _register(checkout)
    async with LocalClient(caller="ao-some-worker") as worker:
        with pytest.raises(AgentError, match="a person's own: refused to a session"):
            await worker.call("promote", repo="repo")
        with pytest.raises(AgentError, match="a person's own"):
            await worker.call("clear_promote", repo="repo")


async def test_the_press_refuses_on_one_and_three_and_goes_through_the_checks(agent, checkout, monkeypatch):
    await park_ticks(agent)
    _register(checkout)
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="nothing to promote"):
            await person.call("promote", repo="repo")  # live is main already
        live = checkout.parent / "live"
        (checkout / ".agentorc.yml").write_text(
            f"promote:\n  run: sleep 1; git rev-parse HEAD > {live}\n  check: cat {live}\n"
        )
        main = _merge(checkout, "b")
        (checkout / "b").write_text("dirty")
        with pytest.raises(AgentError, match="uncommitted change.*precondition: tree"):
            await person.call("promote", repo=str(checkout))
        _git(checkout, "checkout", "-q", "--", "b")
        monkeypatch.setattr(promote, "read_checks", lambda root, sha: ("pending", ""))
        got = await person.call("promote", repo="repo")
        assert got["sha"] == main and got["checks"] == "pending" and got["log"].endswith(f"{main}.log")
        assert agent._promotes["repo"]["inflight"]["by"] == "person"
        with pytest.raises(AgentError, match="in flight.*precondition: inflight"):
            await person.call("promote", repo="repo")
        _wait_gone(agent._promotes["repo"]["inflight"]["pid"])
        await agent._refresh_promotes()
        assert agent._promotes["repo"]["inflight"] is None and agent._promotes["repo"]["live"] == main
        with pytest.raises(AgentError, match="no registered repo"):
            await person.call("promote", repo="elsewhere")
        with pytest.raises(AgentError, match="--sha is not built"):
            await person.call("promote", repo="repo", sha=main)


async def test_a_failure_standing_refuses_the_press_until_dismissed(agent, checkout):
    await park_ticks(agent)
    _register(checkout)
    _merge(checkout, "b")
    promote.repo_dir("repo").mkdir(parents=True)
    (promote.repo_dir("repo") / "failed.json").write_text(json.dumps({"sha": "f" * 40, "why": "it broke"}))
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="failed and is not cleared.*precondition: failed"):
            await person.call("promote", repo="repo")
        assert (await person.call("clear_promote", repo="repo")) == {"repo": "repo", "cleared": True}
        assert (await person.call("clear_promote", repo="repo"))["cleared"] is False
        assert (await person.call("promote", repo="repo"))["repo"] == "repo"


async def test_a_repo_without_the_block_has_no_press(agent, tmp_path):
    await park_ticks(agent)
    plain = tmp_path / "plain"
    plain.mkdir()
    _register(plain)
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="no promote: block"):
            await person.call("promote", repo="plain")


def test_a_pid_taken_again_is_not_the_run_and_is_never_killed():
    p = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        started = promote.proc_start(p.pid)
        assert isinstance(started, int) and promote.alive(p.pid, started)
        assert not promote.alive(p.pid, started + 1)  # another process under the same number
        promote.kill(p.pid, started + 1)
        assert p.poll() is None  # not killed
    finally:
        p.kill()
        p.wait()


def test_the_press_is_a_home_edit_refused_offline_at_a_node():
    from sessionorc import modes

    for method in ("promote", "clear_promote"):
        why = modes.offline_refusal(method, None, {}, host="laptop", home="kmaster")
        assert why and "kmaster (home) is unreachable" in why


def test_ao_promote_status_prints_the_readings(monkeypatch, capsys):
    from agentorc import cli

    reading = {
        "live": "4" * 40, "main": "9" * 40, "ahead": 3, "checks": "green", "auto": False,
        "failed": {"sha": "9" * 40, "at": "t", "why": "it broke", "log": "/l", "tail": ["boom"]},
    }  # fmt: skip
    monkeypatch.setattr(cli, "call_sync", lambda m, **p: {"home": "kmaster", "promotes": {"agentorc": reading}})
    assert cli.main(["promote", "status"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("agentorc · live 4444444 · main 9999999, 3 ahead · checks green · auto off")
    assert "FAILED 9999999 at t: it broke" in out and "    boom" in out


# ── slice 3: the row's snooze, and the press's notes (§4.5a *Inbox row: promote*) ──────────────


async def test_the_promote_row_snoozes_by_repo_in_the_attention_store(agent):
    await park_ticks(agent)
    async with LocalClient() as person:
        got = await person.call("attention_snooze", id="promote:agentorc", kind="promote", until="2030-01-01T00:00:00Z")
    assert got["row"] == "promote:agentorc|promote" and agent.attention_snoozed[got["row"]] == "2030-01-01T00:00:00Z"


async def test_a_press_that_finds_its_run_done_files_the_note(agent, checkout):
    """The techlead's read of #666: a run that reached its commit as the person pressed is concluded
    by the press's own reading — its *promoted …* note must still reach the person inbox."""
    await park_ticks(agent)
    _register(checkout)
    main = _merge(checkout, "b")
    promote.repo_dir("repo").mkdir(parents=True)
    intent = {"sha": main, "at": datetime.now(UTC).isoformat(), "pid": 999999999, "log": "x", "by": "auto", "ahead": 1}
    (promote.repo_dir("repo") / "inflight.json").write_text(json.dumps(intent))
    (checkout.parent / "live").write_text(main)
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="nothing to promote"):
            await person.call("promote", repo="repo")
    assert [e.text for e in agent.person_inbox if e.from_ == "system"] == [f"promoted `repo` `{main[:7]}` — 1 commit"]
    assert agent._promotes["repo"]["inflight"] is None
