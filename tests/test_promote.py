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
import yaml
from conftest import park_ticks

from sessionorc import hosts, promote
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import Session

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
    monkeypatch.setenv("SHIFTLEAD_HOME", str(tmp_path / "home"))
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "remote", "add", "origin", str(origin))
    live = tmp_path / "live"
    (root / ".shiftlead.yml").write_text(
        f"promote:\n  run: git rev-parse HEAD > {live}\n  check: cat {live}\n"
    )  # fmt: skip
    first = _commit(root, "a")
    _git(root, "push", "-q", "origin", "main")
    live.write_text(first + "\n")
    monkeypatch.setattr(promote, "read_checks", lambda root, sha, keep=True: ("green", "", None))
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
    (tmp_path / ".shiftlead.yml").write_text("roles: {}\n")
    assert promote.block(tmp_path) is None  # no key
    (tmp_path / ".shiftlead.yml").write_text("promote:\n  run: x\n  check: y\nroles: {nonsense: 1}\n")
    assert promote.block(tmp_path) == {"run": "x", "check": "y"}
    (tmp_path / ".shiftlead.yml").write_text("promote:\n  run: x\n")
    with pytest.raises(ValueError, match="needs check"):
        promote.block(tmp_path)
    (tmp_path / ".shiftlead.yml").write_text("promote: [x]\n")
    with pytest.raises(ValueError, match="mapping"):
        promote.block(tmp_path)
    (tmp_path / ".shiftlead.yml").write_text("promote: {run: x\n")
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
    assert promote.read_checks(tmp_path, "f" * 40) == ("green", "", None)
    fake([done, {"status": "in_progress", "conclusion": None}])
    assert promote.read_checks(tmp_path, "f" * 40) == ("pending", "", None)
    fake([{"status": "in_progress"}, {"status": "completed", "conclusion": "failure"}])
    assert promote.read_checks(tmp_path, "f" * 40) == ("failed", "", None)
    fake([])
    assert promote.read_checks(tmp_path, "f" * 40) == ("unknown", "no check runs on fffffff", None)
    fake([], rc=1, err="HTTP 403: API rate limit exceeded")
    assert promote.read_checks(tmp_path, "f" * 40) == ("unknown", "HTTP 403: API rate limit exceeded", None)


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


def test_the_pass_reads_live_at_and_the_pending_commits_for_the_build_chip(checkout):
    """§6 *Promote* (TD-539): beside the three readings, `live_at` — live's committer time — and
    `pending`, main's commits past live, newest first, with `pending_more` past ten; empty at main."""
    readings, _, _ = promote.survey([str(checkout)], {}, True, {}, datetime.now(UTC))
    r = readings["repo"]
    assert r["live_at"] == _git(checkout, "log", "-1", "--format=%cI", r["live"])
    assert (r["pending"], r["pending_more"]) == ([], 0)
    shas = [_merge(checkout, n) for n in ("b", "c", "d")]
    readings, _, _ = promote.survey([str(checkout)], readings, True, {}, datetime.now(UTC))
    r = readings["repo"]
    assert r["ahead"] == 3 and r["pending"] == [
        {"sha": s, "subject": n} for s, n in zip(shas[::-1], "dcb", strict=True)
    ]
    assert r["pending_more"] == 0
    for i in range(10):
        _merge(checkout, f"m{i}")
    r = promote.survey([str(checkout)], readings, True, {}, datetime.now(UTC))[0]["repo"]
    assert len(r["pending"]) == 10 and r["pending_more"] == 3 and r["pending"][0]["subject"] == "m9"
    # an unknown live or main leaves what it cannot read absent
    assert promote.read_since(checkout, None, r["main"]) == {}
    assert set(promote.read_since(checkout, r["live"], None)) == {"live_at"}
    assert promote.read_since(checkout, "0" * 40, r["main"]) == {}


def test_auto_waits_for_main_to_settle(checkout, monkeypatch):
    _merge(checkout, "b")
    readings, _, _ = promote.survey([str(checkout)], {}, True, {"repo": True}, datetime.now(UTC))
    assert readings["repo"]["inflight"] is None  # main moved a moment ago
    later = datetime.now(UTC) + timedelta(seconds=promote.PROMOTE_SETTLE + 1)
    readings, _, _ = promote.survey([str(checkout)], readings, True, {"repo": True}, later)
    assert readings["repo"]["inflight"]["by"] == "auto"


def test_auto_waits_for_a_reading_of_live(checkout, monkeypatch, tmp_path):
    """§6 (Paul, 2026-09-28): `auto` never acts on a live it could not read; the press still can."""
    monkeypatch.setattr(promote, "PROMOTE_SETTLE", 0.0)
    (tmp_path / "live").unlink()  # `check` fails: live is unknown, with why
    main = _merge(checkout, "b")
    readings, _, _ = promote.survey([str(checkout)], {}, True, {"repo": True}, datetime.now(UTC))
    r = readings["repo"]
    assert r["live"] is None and r["live_why"] and r["inflight"] is None
    assert r["unmet"] is None and promote.unmet(r, press=True) is None  # nothing refuses the person's press
    (tmp_path / "live").write_text(_git(checkout, "rev-parse", "HEAD~1") + "\n")  # a reading lands
    readings, _, _ = promote.survey([str(checkout)], readings, True, {"repo": True}, datetime.now(UTC))
    assert readings["repo"]["inflight"]["sha"] == main


def test_auto_waits_while_check_stops_answering_though_a_last_reading_is_kept(checkout, monkeypatch, tmp_path):
    monkeypatch.setattr(promote, "PROMOTE_SETTLE", 0.0)
    readings, _, _ = promote.survey([str(checkout)], {}, True, {"repo": True}, datetime.now(UTC))
    first = readings["repo"]["live"]
    assert first and readings["repo"]["inflight"] is None  # live is main: nothing to do
    (tmp_path / "live").unlink()  # `check` stops answering
    _merge(checkout, "b")
    readings, _, _ = promote.survey([str(checkout)], readings, True, {"repo": True}, datetime.now(UTC))
    r = readings["repo"]
    assert r["live"] == first and r["live_why"] and r["inflight"] is None


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
    (checkout / ".shiftlead.yml").write_text(
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
    (checkout / ".shiftlead.yml").write_text(f"promote:\n  run: sleep 60\n  check: cat {tmp_path / 'live'}\n")
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
    (other / ".shiftlead.yml").write_text("promote: {run: x}\n")
    readings, _, bad = promote.survey(
        [str(other), str(checkout), str(tmp_path / "gone")], {}, True, {}, datetime.now(UTC)
    )
    assert list(readings) == ["repo"] and list(bad) == [str(other)]


def _register(*roots: Path) -> None:
    reg = hosts.default_repos_registry()
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
        with pytest.raises(AgentError, match="a person's own act, refused to every session"):
            await worker.call("promote", repo="repo")
        with pytest.raises(AgentError, match="a person's own"):
            await worker.call("clear_promote", repo="repo")


async def test_the_persons_own_shell_presses_and_the_press_is_recorded_as_the_shell(agent, checkout, monkeypatch):
    """§4.8 *The person's own shell is the person* (TD-580): a shell the person opened — interactive,
    no role, no team, no controller — runs `promote` as the person does, and `by` names the shell;
    the same shell given a controller is refused on its next call."""
    await park_ticks(agent)
    _register(checkout)
    main = _merge(checkout, "b")
    monkeypatch.setattr(promote, "read_checks", lambda root, sha, keep=True: ("pending", "", None))
    shell = Session(id="ao-repo-sh", name="sh", kind="interactive", adapter="shell", dir=str(checkout))
    agent.sessions[shell.id] = shell
    async with LocalClient(caller=shell.id) as sh:
        assert (await sh.call("promote", repo="repo"))["sha"] == main
        assert agent._promotes["repo"]["inflight"]["by"] == "ao-repo-sh"
        _wait_gone(agent._promotes["repo"]["inflight"]["pid"])
        shell.controllers = ["ao-repo-lead"]
        with pytest.raises(AgentError, match="refused to every session but the person's own shell"):
            await sh.call("clear_promote", repo="repo")


async def test_the_press_refuses_on_one_and_three_and_goes_through_the_checks(agent, checkout, monkeypatch):
    await park_ticks(agent)
    _register(checkout)
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="nothing to promote"):
            await person.call("promote", repo="repo")  # live is main already
        live = checkout.parent / "live"
        (checkout / ".shiftlead.yml").write_text(
            f"promote:\n  run: sleep 1; git rev-parse HEAD > {live}\n  check: cat {live}\n"
        )
        main = _merge(checkout, "b")
        (checkout / "b").write_text("dirty")
        with pytest.raises(AgentError, match="uncommitted change.*precondition: tree"):
            await person.call("promote", repo=str(checkout))
        _git(checkout, "checkout", "-q", "--", "b")
        monkeypatch.setattr(promote, "read_checks", lambda root, sha, keep=True: ("pending", "", None))
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
        with pytest.raises(AgentError, match="is live already"):
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
        assert (await person.call("clear_promote", repo="repo")) == {"repo": "repo", "cleared": True, "which": "failed"}
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
    from shiftlead import cli

    reading = {
        "live": "4" * 40, "main": "9" * 40, "ahead": 3, "checks": "green", "auto": False,
        "failed": {"sha": "9" * 40, "at": "t", "why": "it broke", "log": "/l", "tail": ["boom"]},
    }  # fmt: skip
    monkeypatch.setattr(cli, "call_sync", lambda m, **p: {"home": "kmaster", "promotes": {"shiftlead": reading}})
    assert cli.main(["promote", "status"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("shiftlead · live 4444444 · main 9999999, 3 ahead · checks green · auto off")
    assert "FAILED 9999999 at t: it broke" in out and "    boom" in out


# ── slice 3: the row's snooze, and the press's notes (§4.5a *Inbox row: promote*) ──────────────


async def test_the_promote_row_snoozes_by_repo_in_the_attention_store(agent):
    await park_ticks(agent)
    async with LocalClient() as person:
        got = await person.call(
            "attention_snooze", id="promote:shiftlead", kind="promote", until="2030-01-01T00:00:00Z"
        )
    assert got["row"] == "promote:shiftlead|promote" and agent.attention_snoozed[got["row"]] == "2030-01-01T00:00:00Z"


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


def test_this_repos_block_is_the_promote_pair_read_from_the_checkout():
    """TD-132 slice 4: shiftlead's own `.shiftlead.yml` carries `promote:` with `run`, `check` and `smoke`
    (TD-812) — `run` is CLAUDE.md's pair with the checkout's path taken from where it runs, `check` the live
    venv's build record, `smoke` `scripts/smoke.py` under the live venv. Its `teams:` and `roles:` are
    TD-229 slice 2's (`tests/test_org.py`)."""
    root = Path(__file__).resolve().parent.parent
    doc = yaml.safe_load((root / ".shiftlead.yml").read_text())
    assert "promote" in doc and set(doc["promote"]) == {"run", "check", "smoke"}  # `auto` is settings.yml's
    b = promote.block(root)
    assert "/shiftlead-venv/bin" in b["run"] and '"$(pwd -P)[ui]"' in b["run"] and b["run"].endswith("service install")
    assert "/home/kmaster" not in b["run"] + b["check"] + b["smoke"]  # never a hard-coded checkout or home
    assert "build.info()" in b["check"] and "sys.exit(" in b["check"]  # no record → exit 1 saying why


# ── TD-226: the rollback — `--sha`, `--back`, the worktree, the hold (§6 *A rollback*) ─────────


def _rollback_block(checkout: Path, run_extra: str = "") -> Path:
    """The fixture's block, its `run` also recording where it ran and what it was handed."""
    live = checkout.parent / "live"
    seen = checkout.parent / "seen"
    handed = "$SHIFTLEAD_PROMOTE_SHA $SHIFTLEAD_PROMOTE_FROM $SHIFTLEAD_PROMOTE_ROOT"
    run = f'{run_extra}git rev-parse HEAD > {live}; echo "$(pwd -P) {handed}" > {seen}'
    (checkout / ".shiftlead.yml").write_text(yaml.safe_dump({"promote": {"run": run, "check": f"cat {live}"}}))
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-q", "-m", "block")
    _git(checkout, "push", "-q", "origin", "main")
    live.write_text(_git(checkout, "rev-parse", "HEAD") + "\n")  # live is main's head
    return seen


async def _promote_to_main(agent, person, checkout) -> str:
    """A plain press that concludes, so `last.json` holds what was live before it."""
    got = await person.call("promote", repo="repo")
    _wait_gone(agent._promotes["repo"]["inflight"]["pid"])
    await agent._refresh_promotes()
    assert agent._promotes["repo"]["live"] == got["sha"]
    return got["sha"]


async def _settle(agent) -> None:
    _wait_gone(agent._promotes["repo"]["inflight"]["pid"])
    agent._promote_watch_at = float("-inf")
    await agent._refresh_promotes()


async def test_a_rollback_runs_in_a_worktree_and_leaves_the_checkout_alone(agent, checkout):
    await park_ticks(agent)
    _register(checkout)
    seen = _rollback_block(checkout)
    old = _git(checkout, "rev-parse", "HEAD")
    (checkout.parent / "live").write_text(old + "\n")
    _merge(checkout, "b")
    async with LocalClient() as person:
        new = await _promote_to_main(agent, person, checkout)
        assert promote.last("repo")["from"] == old
        # the person's checkout on a branch with a change left in it: not what a rollback installs
        _git(checkout, "checkout", "-q", "-b", "td9-wip")
        (checkout / "b").write_text("dirty")
        before = (_git(checkout, "rev-parse", "HEAD"), _git(checkout, "status", "--porcelain"))
        got = await person.call("promote", repo="repo", back=True)
        assert (got["kind"], got["sha"], got["from"], got["checks"]) == ("rollback", old, new, "green")
        tree = promote.repo_dir("repo") / "tree"
        assert agent._promotes["repo"]["inflight"]["tree"] == str(tree)
        await _settle(agent)
    r = agent._promotes["repo"]
    assert r["live"] == old and r["inflight"] is None and r["held"]["from"] == new and r["held"]["sha"] == old
    assert seen.read_text().split() == [str(tree.resolve()), old, new, str(checkout)]
    assert (_git(checkout, "rev-parse", "HEAD"), _git(checkout, "status", "--porcelain")) == before
    assert _git(checkout, "rev-parse", "--abbrev-ref", "HEAD") == "td9-wip"
    assert tree.exists()  # kept: what was installed from it may name it as its source
    notes = [e.text for e in agent.person_inbox if e.from_ == "system"]
    assert notes[-1] == f"rolled back `repo` to `{old[:7]}` from `{new[:7]}` — main is 1 commit ahead"
    assert promote.last("repo")["sha"] == old


async def test_the_commit_is_refused_by_name(agent, checkout):
    await park_ticks(agent)
    _register(checkout)
    first = _git(checkout, "rev-parse", "HEAD")
    _git(checkout, "tag", "v1")
    _git(checkout, "checkout", "-q", "-b", "td9-side")
    side = _commit(checkout, "side")
    _git(checkout, "push", "-q", "origin", "td9-side")
    _git(checkout, "checkout", "-q", "main")
    _merge(checkout, "b")
    async with LocalClient() as person:
        for bad in ("td9-side", "v1", "HEAD~1", "abc12"):
            with pytest.raises(AgentError, match="is not a commit's hex"):
                await person.call("promote", repo="repo", sha=bad)
        with pytest.raises(AgentError, match="is not on main: a branch is never promoted"):
            await person.call("promote", repo="repo", sha=side[:9])
        with pytest.raises(AgentError, match="names no commit"):
            await person.call("promote", repo="repo", sha="0" * 12)
        with pytest.raises(AgentError, match="is live already"):
            await person.call("promote", repo="repo", sha=first)
        with pytest.raises(AgentError, match="is main's head: that is the plain press"):
            await person.call("promote", repo="repo", sha=_git(checkout, "rev-parse", "HEAD"))
        with pytest.raises(AgentError, match="--sha or --back, not both"):
            await person.call("promote", repo="repo", sha=first, back=True)
        with pytest.raises(AgentError, match="no promote of repo has concluded"):
            await person.call("promote", repo="repo", back=True)


def test_an_ambiguous_prefix_names_more_than_one_commit(checkout, monkeypatch):
    a, b = "abcdef1" + "0" * 33, "abcdef1" + "1" * 33
    real = promote._git

    def fake(root, *args, **kw):
        if args[0] == "rev-parse" and args[1] == "--verify":
            return None, "git rev-parse: short object ID abcdef1 is ambiguous"
        if args[0] == "rev-parse" and args[1].startswith("--disambiguate="):
            return f"{a}\n{b}", ""
        if args[0] == "cat-file":
            return "commit", ""
        return real(root, *args, **kw)

    monkeypatch.setattr(promote, "_git", fake)
    assert promote.resolve(checkout, "ABCDEF1") == (None, "abcdef1 names 2 commits: give more of it")


async def test_back_is_refused_when_what_was_live_was_never_read(agent, checkout):
    await park_ticks(agent)
    _register(checkout)
    main = _merge(checkout, "b")
    promote.repo_dir("repo").mkdir(parents=True)
    (promote.repo_dir("repo") / "last.json").write_text(json.dumps({"sha": main, "from": None}))
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="was never read"):
            await person.call("promote", repo="repo", back=True)


async def test_a_rollback_goes_through_a_failure_and_clears_it(agent, checkout):
    await park_ticks(agent)
    _register(checkout)
    _rollback_block(checkout)
    old = _git(checkout, "rev-parse", "HEAD~1")
    promote.repo_dir("repo").mkdir(parents=True)
    (promote.repo_dir("repo") / "failed.json").write_text(json.dumps({"sha": "f" * 40, "why": "it broke"}))
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="precondition: failed"):
            await person.call("promote", repo="repo")
        await person.call("promote", repo="repo", sha=old[:7])
        await _settle(agent)
    r = agent._promotes["repo"]
    assert r["live"] == old and r["failed"] is None and promote.failed("repo") is None and r["held"]


async def test_the_hold_stops_auto_until_the_person_clears_it(agent, checkout, monkeypatch):
    await park_ticks(agent)
    monkeypatch.setattr(promote, "PROMOTE_SETTLE", 0.0)
    monkeypatch.setattr(type(agent), "_promote_auto", staticmethod(lambda: {"repo": True}))
    _register(checkout)
    _rollback_block(checkout)
    old = _git(checkout, "rev-parse", "HEAD~1")
    async with LocalClient() as person:
        await person.call("promote", repo="repo", sha=old)
        await _settle(agent)
        first = promote.held("repo")
        assert first["sha"] == old
        with pytest.raises(AgentError, match="a rollback's hold stands"):
            await person.call("promote", repo="repo", back=True)
        _merge(checkout, "c")
        agent._promote_read_at = float("-inf")
        await agent._refresh_promotes()
        r = agent._promotes["repo"]
        assert r["inflight"] is None and r["unmet"]["name"] == "held"  # however far main moves
        # a second rollback under the hold keeps what the person first went back from
        other = _git(checkout, "rev-parse", "HEAD~1")  # the block's commit: neither live nor main
        await person.call("promote", repo="repo", sha=other)
        await _settle(agent)
        assert promote.held("repo")["sha"] == other and promote.held("repo")["from"] == first["from"]
        # Dismiss with a failure and a hold standing clears the failure first
        (promote.repo_dir("repo") / "failed.json").write_text(json.dumps({"sha": "f" * 40, "why": "x"}))
        assert (await person.call("clear_promote", repo="repo"))["which"] == "failed"
        assert (await person.call("clear_promote", repo="repo"))["which"] == "held"
        assert (await person.call("clear_promote", repo="repo"))["which"] is None
        agent._promote_read_at = float("-inf")
        await agent._refresh_promotes()
        assert agent._promotes["repo"]["inflight"]["by"] == "auto"  # auto goes on as if nothing were held
        await _settle(agent)


async def test_a_plain_press_that_concludes_ends_the_hold(agent, checkout):
    await park_ticks(agent)
    _register(checkout)
    _rollback_block(checkout)
    old = _git(checkout, "rev-parse", "HEAD~1")
    async with LocalClient() as person:
        await person.call("promote", repo="repo", sha=old)
        await _settle(agent)
        assert promote.held("repo")
        got = await person.call("promote", repo="repo")  # the press is not refused by the hold
        await _settle(agent)
    assert agent._promotes["repo"]["live"] == got["sha"] and promote.held("repo") is None
    assert agent._promotes["repo"]["held"] is None


async def test_a_run_that_refuses_before_changing_anything_is_a_failure(agent, checkout):
    await park_ticks(agent)
    _register(checkout)
    live = checkout.parent / "live"
    (checkout / ".shiftlead.yml").write_text(
        yaml.safe_dump({"promote": {"run": "echo too old to go back to; exit 2", "check": f"cat {live}"}})
    )
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-q", "-m", "block")
    _git(checkout, "push", "-q", "origin", "main")
    before = live.read_text().strip()
    target = _git(checkout, "rev-parse", "HEAD~1")
    live.write_text(_git(checkout, "rev-parse", "HEAD") + "\n")
    async with LocalClient() as person:
        await person.call("promote", repo="repo", sha=target)
        await _settle(agent)
    f = agent._promotes["repo"]["failed"]
    assert f["tail"] == ["too old to go back to"] and f["exit"] == 2
    assert agent._promotes["repo"]["live"] != before and promote.held("repo") is None


async def test_a_leftover_tree_does_not_stop_the_next_rollback(agent, checkout):
    await park_ticks(agent)
    _register(checkout)
    _rollback_block(checkout)
    old = _git(checkout, "rev-parse", "HEAD~1")
    tree = promote.repo_dir("repo") / "tree"
    tree.mkdir(parents=True)
    (tree / "stale").write_text("left by a run the agent never saw end")
    async with LocalClient() as person:
        await person.call("promote", repo="repo", sha=old)
        await _settle(agent)
    assert agent._promotes["repo"]["live"] == old and not (tree / "stale").exists()


def test_ao_promote_status_reads_rolled_back_and_held(monkeypatch, capsys):
    from shiftlead import cli

    reading = {
        "live": "4" * 40, "main": "9" * 40, "ahead": 3, "checks": "green", "auto": True,
        "held": {"sha": "4" * 40, "from": "9" * 40, "main": "9" * 40, "at": "t"},
    }  # fmt: skip
    monkeypatch.setattr(cli, "call_sync", lambda m, **p: {"home": "kmaster", "promotes": {"shiftlead": reading}})
    assert cli.main(["promote", "status"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(
        "shiftlead · live 4444444, rolled back from 9999999 · main 9999999, 3 ahead · checks green · auto on · held"
    )
    calls = []
    monkeypatch.setattr(cli, "call_sync", lambda m, **p: calls.append((m, p)) or {"repo": "shiftlead", "which": "held"})
    assert cli.main(["promote", "clear", "shiftlead"]) == 0
    assert calls == [("clear_promote", {"repo": "shiftlead"})] and "the hold is cleared" in capsys.readouterr().out


def test_ao_promote_status_reads_checking_live_and_rolled_back_by_the_smoke(monkeypatch, capsys):
    """§4.7 `ao promote status` (§6 *The smoke*, TD-811 slice 2): the row's words, and `--json` the
    reading itself, `phase` and `held.by` / `held.why` with it."""
    from shiftlead import cli

    why = "smoke failed on 9999999: boom"
    flight = {"sha": "9" * 40, "at": "t0", "by": "auto", "log": "/l", "phase": "smoke", "smoke_at": "t1"}
    held = {"sha": "4" * 40, "from": "9" * 40, "main": "9" * 40, "at": "t", "by": "smoke", "why": why}
    for r, want in (
        ({"inflight": flight}, "\n  checking 9999999 live · started t1 · log /l"),
        ({"held": held}, f"shiftlead · live 4444444, rolled back by the smoke from 9999999 — {why} · main"),
    ):
        reading = {"live": "4" * 40, "main": "9" * 40, "ahead": 3, "checks": "green", "auto": True, **r}
        monkeypatch.setattr(
            cli, "call_sync", lambda m, rd=reading, **p: {"home": "kmaster", "promotes": {"shiftlead": rd}}
        )
        assert cli.main(["promote", "status"]) == 0
        assert want in capsys.readouterr().out
        assert cli.main(["--json", "promote", "status"]) == 0
        assert json.loads(capsys.readouterr().out)["shiftlead"] == reading


# ── TD-581: the cap on the settle, when the home will act, and the press under `auto` ──────────


def _merge_at(root: Path, name: str, when: datetime) -> str:
    """A merge to main whose committer time is `when`, as a squash merge stamps it."""
    (root / name).write_text(name)
    _git(root, "add", "-A")
    stamp = when.isoformat()
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), **GIT_ENV}
    env |= {"GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp}
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", name], check=True, capture_output=True, env=env)
    _git(root, "push", "-q", "origin", "main")
    return _git(root, "rev-parse", "HEAD")


def test_ahead_since_is_when_main_first_moved_past_live_kept_and_cleared(checkout, tmp_path):
    """§6 *Promote* (TD-581): `ahead_since`, the time main first moved past the live commit — the
    oldest of main's commits live does not hold — kept while live stays behind, cleared at main."""
    t0 = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    readings, _, _ = promote.survey([str(checkout)], {}, True, {}, datetime.now(UTC))
    assert readings["repo"]["ahead_since"] is None  # live is main
    _merge_at(checkout, "b", t0)
    readings, _, _ = promote.survey([str(checkout)], readings, True, {}, datetime.now(UTC))
    assert datetime.fromisoformat(readings["repo"]["ahead_since"]) == t0
    main = _merge_at(checkout, "c", t0 + timedelta(minutes=20))
    readings, _, _ = promote.survey([str(checkout)], readings, True, {}, datetime.now(UTC))
    r = readings["repo"]
    assert datetime.fromisoformat(r["ahead_since"]) == t0 and r["ahead"] == 2  # kept: still the first
    assert datetime.fromisoformat(r["moved"]) == t0 + timedelta(minutes=20)
    (tmp_path / "live").write_text(main + "\n")  # live catches up
    readings, _, _ = promote.survey([str(checkout)], readings, True, {}, datetime.now(UTC))
    assert readings["repo"]["ahead_since"] is None
    assert promote.read_ahead_since(checkout, None, main) is None


def test_auto_promotes_a_busy_main_at_the_max_wait_and_the_settle_holds_the_tick_before(checkout):
    """§6 *Promote* (TD-581): under `auto` a main that never stands still for `PROMOTE_SETTLE` is
    promoted once it has been ahead of live for `PROMOTE_MAX_WAIT` — with `moved` four minutes old."""
    now = datetime.now(UTC).replace(microsecond=0)
    _merge_at(checkout, "b", now - timedelta(seconds=promote.PROMOTE_MAX_WAIT - 60))
    _merge_at(checkout, "c", now - timedelta(minutes=4))
    readings, _, _ = promote.survey([str(checkout)], {}, True, {"repo": True}, now)
    r = readings["repo"]
    assert r["inflight"] is None  # neither clock has run out: the settle batches the burst
    assert r["acts"] == {"settles_in": 60}  # the cap's remainder, sooner than the settle's six minutes
    assert promote.acts_words(r["acts"]) == "settles in 1 min"
    later = now + timedelta(seconds=61)
    readings, _, _ = promote.survey([str(checkout)], readings, True, {"repo": True}, later)
    r = readings["repo"]
    assert r["inflight"]["by"] == "auto" and r["inflight"]["sha"] == r["main"]
    assert r["acts"] is None  # a run in flight: the row is the note's now
    _wait_gone(r["inflight"]["pid"])


def test_what_the_home_will_do_under_auto_in_the_rows_words():
    now = datetime(2026, 10, 12, 18, 0, tzinfo=UTC)
    base = {"auto": True, "live": "4" * 40, "main": "9" * 40, "tree": None, "checks": "green"}
    base |= {"moved": "2026-10-12T17:56:00+00:00", "ahead_since": "2026-10-12T17:40:00+00:00"}
    assert promote.acts(base, now) == {"settles_in": 360}  # settle: 6 min left; cap: 10 min left
    assert promote.acts({**base, "ahead_since": "2026-10-12T17:35:00+00:00"}, now) == {"settles_in": 300}
    assert promote.acts({**base, "moved": "2026-10-12T17:50:00+00:00"}, now) == {"at": "next tick"}
    assert promote.acts({**base, "ahead_since": "2026-10-12T17:00:00+00:00"}, now) == {"at": "next tick"}
    assert promote.acts({**base, "checks": "pending"}, now) == {"checks": "pending"}
    assert promote.acts({**base, "live_why": "check exited 1"}, now) == {"waiting": "live"}
    assert promote.acts({**base, "live": None}, now) == {"waiting": "live"}
    for off in ({"auto": False}, {"live": "9" * 40}, {"inflight": {"sha": "9" * 40}}, {"tree": "dirty"}):
        assert promote.acts({**base, **off}, now) is None, off
    assert promote.acts({**base, "failed": {"sha": "9" * 40}}, now) is None
    assert promote.acts({**base, "held": {"sha": "4" * 40}}, now) is None
    # a failure or hold stands whatever the checks read; and no clock read is no promise (review of #1589)
    assert promote.acts({**base, "checks": "pending", "failed": {"sha": "9" * 40}}, now) is None
    assert promote.acts({**base, "checks": "pending", "held": {"sha": "4" * 40}}, now) is None
    assert promote.acts({**base, "moved": None, "ahead_since": None}, now) is None
    words = [promote.acts_words(a) for a in ({"settles_in": 360}, {"at": "next tick"}, {"checks": "failed"})]
    assert words == ["settles in 6 min", "promotes at the next tick", "checks failed — promotes when green"]
    assert promote.acts_words({"waiting": "live"}) == "waiting for a live reading"
    assert promote.acts_words(None) == "" and promote.acts_words({"settles_in": 1}) == "settles in 1 min"


def test_ao_promote_status_says_when_the_home_will_act_under_auto(monkeypatch, capsys):
    from shiftlead import cli

    reading = {"live": "4" * 40, "main": "9" * 40, "ahead": 3, "checks": "green", "auto": True}
    reading |= {"acts": {"settles_in": 360}}
    monkeypatch.setattr(cli, "call_sync", lambda m, **p: {"home": "kmaster", "promotes": {"shiftlead": reading}})
    assert cli.main(["promote", "status"]) == 0
    assert capsys.readouterr().out.startswith(
        "shiftlead · live 4444444 · main 9999999, 3 ahead · checks green · auto on · settles in 6 min"
    )
    reading["acts"] = None
    assert cli.main(["promote", "status"]) == 0
    assert capsys.readouterr().out.rstrip().endswith("checks green · auto on")


async def test_the_press_under_auto_promotes_at_once_skipping_the_settle(agent, checkout):
    """§6 *Promote*, §4.7 (TD-581): the settle and the wait are the policy's clocks, not
    preconditions — a person's press under `auto: true` promotes main's head a moment after it moved."""
    await park_ticks(agent)
    for task in (agent._promote_task, agent._repos_task):  # TD-768: the first tick's passes, finished before
        if task is not None:  # the test sets `_promotes`, which an in-flight promote pass replaced on CI
            await task
    _register(checkout)
    main = _merge(checkout, "b")
    agent._promotes["repo"] = {"auto": True}
    assert not promote.settled(_git(checkout, "log", "-1", "--format=%cI"), datetime.now(UTC))
    async with LocalClient() as person:
        got = await person.call("promote", repo="repo")
    assert got["sha"] == main and agent._promotes["repo"]["inflight"]["by"] == "person"
    assert agent._promotes["repo"]["auto"] is True
    _wait_gone(agent._promotes["repo"]["inflight"]["pid"])


# ── the smoke (§6 *The smoke*, TD-804, built by TD-811) ────────────────────────────────────────


def _smoke_block(checkout: Path, smoke: str | None = "default") -> tuple[Path, Path]:
    """The fixture's block with a smoke that records where it ran and what it was handed, and exits
    with what `smoke_exit` says (0 until a test says otherwise); live is the block's commit."""
    live, seen, code = (checkout.parent / n for n in ("live", "smoke_seen", "smoke_exit"))
    code.write_text("0")
    handed = "$SHIFTLEAD_PROMOTE_KIND $SHIFTLEAD_PROMOTE_SHA $SHIFTLEAD_PROMOTE_FROM $SHIFTLEAD_PROMOTE_ROOT"
    if smoke == "default":
        smoke = f'echo "$(pwd -P) {handed}" > {seen}; echo smoke says $(cat {code}); exit $(cat {code})'
    b = {"run": f"git rev-parse HEAD > {live}", "check": f"cat {live}"} | ({"smoke": smoke} if smoke else {})
    (checkout / ".shiftlead.yml").write_text(yaml.safe_dump({"promote": b}))
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-q", "-m", "smoke block")
    _git(checkout, "push", "-q", "origin", "main")
    live.write_text(_git(checkout, "rev-parse", "HEAD") + "\n")
    return seen, code


def _pass(checkout: Path, readings: dict, now: datetime | None = None) -> tuple[dict, list[str]]:
    """One watch pass (`check` alone) once the run or the smoke in flight has ended."""
    f = readings["repo"]["inflight"]
    if f:
        _wait_gone(f["smoke_pid"] if f.get("phase") == "smoke" else f["pid"])
    readings, notes, _ = promote.survey([str(checkout)], readings, False, {}, now or datetime.now(UTC))
    return readings, notes


def _press(checkout: Path, sha: str, kind: str = "promote", frm: str | None = None) -> dict:
    readings, _, _ = promote.survey([str(checkout)], {}, True, {}, datetime.now(UTC))
    run = promote.block(checkout)["run"]
    readings["repo"]["inflight"] = promote.start(checkout, "repo", sha, run, "person", 1, kind, frm)
    return readings


def test_a_block_with_a_smoke_is_accepted_by_both_readers(tmp_path):
    from shiftlead import repoconfig

    text = "promote:\n  run: x\n  check: y\n  smoke: scripts/smoke.sh\n"
    (tmp_path / ".shiftlead.yml").write_text(text)
    assert promote.block(tmp_path) == {"run": "x", "check": "y", "smoke": "scripts/smoke.sh"}
    assert repoconfig.load_text(text, tmp_path).promote["smoke"] == "scripts/smoke.sh"
    with pytest.raises(ValueError, match="must be a command"):
        repoconfig.load_text("promote:\n  run: x\n  check: y\n  smoke: ''\n", tmp_path)


def test_the_smoke_runs_once_check_reads_the_commit_and_the_promote_concludes_on_its_pass(checkout):
    seen, _ = _smoke_block(checkout)
    frm = _git(checkout, "rev-parse", "HEAD")
    main = _merge(checkout, "b")
    promote.repo_dir("repo").mkdir(parents=True)
    (promote.repo_dir("repo") / "held.json").write_text(json.dumps({"sha": frm, "from": "f" * 40}))
    readings = _press(checkout, main, frm=frm)
    assert readings["repo"]["inflight"]["smoke"].startswith('echo "$(pwd -P)')
    readings, notes = _pass(checkout, readings)  # check reads main: the smoke starts, the promote stays in flight
    f = readings["repo"]["inflight"]
    assert notes == [] and f["phase"] == "smoke" and f["smoke_pid"] and f["smoke_at"]
    assert promote.last("repo")["sha"] == main and promote.held("repo")  # the hold waits for the smoke
    assert promote.unmet(readings["repo"], press=True)[0] == "inflight"  # precondition (3) refuses a press
    readings, notes = _pass(checkout, readings)
    assert len(notes) == 1 and notes[0].startswith(f"promoted `repo` `{main[:7]}` — 1 commit · smoke passed in ")
    assert seen.read_text().split() == [str(checkout.resolve()), "promote", main, frm, str(checkout)]
    assert readings["repo"]["inflight"] is None and promote.held("repo") is None
    assert not (promote.repo_dir("repo") / "smoke.json").exists()
    log = (promote.repo_dir("repo") / f"{main}.log").read_text().splitlines()
    assert log[-2:] == ["── smoke ──", "smoke says 0"]


def test_a_failed_smoke_rolls_back_to_from_with_the_hold_and_why(checkout):
    _, code = _smoke_block(checkout)
    frm = _git(checkout, "rev-parse", "HEAD")
    main = _merge(checkout, "b")
    readings = _press(checkout, main, frm=frm)
    code.write_text("4")
    readings, _ = _pass(checkout, readings)
    readings, notes = _pass(checkout, readings)  # the smoke failed: the rollback starts at once
    f = readings["repo"]["inflight"]
    why = f"smoke failed on {main[:7]}: smoke says 4"
    assert notes == [] and readings["repo"]["failed"] is None
    assert (f["kind"], f["by"], f["sha"], f["from"], f["why"]) == ("rollback", "smoke", frm, main, why)
    code.write_text("0")
    readings, _ = _pass(checkout, readings)  # check reads `from`: the hold, before its own smoke
    assert promote.held("repo") | {"at": None} == {"sha": frm, "from": main, "main": main, "at": None} | {
        "by": "smoke",
        "why": why,
    }
    assert readings["repo"]["inflight"]["phase"] == "smoke"
    readings, notes = _pass(checkout, readings)
    assert notes[0].startswith(f"rolled back `repo` to `{frm[:7]}`: the smoke failed on `{main[:7]}` — smoke says 4")
    assert "· smoke passed in " in notes[0] and readings["repo"]["live"] == frm
    assert readings["repo"]["held"]["by"] == "smoke" and readings["repo"]["unmet"]["name"] == "held"


def test_a_rollbacks_failed_smoke_stands_as_a_failure_beside_its_hold(checkout):
    _, code = _smoke_block(checkout)
    old = _git(checkout, "rev-parse", "HEAD")
    new = _merge(checkout, "b")
    (checkout.parent / "live").write_text(new + "\n")
    readings = _press(checkout, old, "rollback", new)
    code.write_text("1")
    readings, _ = _pass(checkout, readings)
    readings, notes = _pass(checkout, readings)
    f = readings["repo"]["failed"]
    assert notes == [] and readings["repo"]["inflight"] is None and readings["repo"]["live"] == old
    assert f["why"] == f"smoke failed on {old[:7]}: smoke says 1" and f["exit"] == 1
    assert promote.held("repo")["sha"] == old  # never rolled back: the failure stands beside the hold


@pytest.mark.parametrize("frm", [None, "main"])
def test_no_rollback_for_an_unread_from_or_one_that_is_the_commit(checkout, frm):
    _, code = _smoke_block(checkout)
    main = _merge(checkout, "b")
    readings = _press(checkout, main, frm=main if frm else None)
    code.write_text("2")
    readings, _ = _pass(checkout, readings)
    readings, _ = _pass(checkout, readings)
    assert readings["repo"]["inflight"] is None and readings["repo"]["failed"]["why"].startswith("smoke failed on")


def test_a_smoke_gone_with_no_result_fails(checkout):
    _smoke_block(checkout, smoke="sleep 60")
    main = _merge(checkout, "b")
    readings = _press(checkout, main)
    readings, _ = _pass(checkout, readings)
    pid = readings["repo"]["inflight"]["smoke_pid"]
    os.killpg(pid, 9)
    readings, _ = _pass(checkout, readings)
    f = readings["repo"]["failed"]
    assert f["why"] == f"smoke failed on {main[:7]}: the smoke ended with no result written"


def test_a_smoke_past_its_bound_is_killed(checkout):
    _smoke_block(checkout, smoke="sleep 60")
    main = _merge(checkout, "b")
    readings = _press(checkout, main)
    readings, _ = _pass(checkout, readings)
    pid = readings["repo"]["inflight"]["smoke_pid"]
    past_run = datetime.now(UTC) + timedelta(seconds=promote.SMOKE_BOUND - 5)
    readings, _, _ = promote.survey([str(checkout)], readings, False, {}, past_run)
    assert readings["repo"]["inflight"]["phase"] == "smoke" and promote.alive(pid)  # past no bound yet
    later = datetime.now(UTC) + timedelta(seconds=promote.SMOKE_BOUND + 1)
    readings, _, _ = promote.survey([str(checkout)], readings, False, {}, later)
    assert (
        readings["repo"]["failed"]["why"]
        == f"smoke failed on {main[:7]}: the smoke was still running after 10 minutes: killed"
    )
    _wait_gone(pid)
    assert not promote.alive(pid)


def test_a_run_and_its_smoke_past_the_runs_bound_pass(checkout, monkeypatch):
    _smoke_block(checkout)
    main = _merge(checkout, "b")
    readings = _press(checkout, main)
    readings, _ = _pass(checkout, readings)
    monkeypatch.setattr(promote, "PROMOTE_BOUND", 0.0)
    readings, notes = _pass(checkout, readings)
    assert "smoke passed" in notes[0] and readings["repo"]["failed"] is None


def test_the_smokes_rollback_that_cannot_start_leaves_the_hold_beside_both_reasons(checkout, monkeypatch):
    _, code = _smoke_block(checkout)
    frm = _git(checkout, "rev-parse", "HEAD")
    main = _merge(checkout, "b")
    promote.repo_dir("repo").mkdir(parents=True)
    hold = {"sha": frm, "from": "f" * 40, "main": main, "at": "x"}
    (promote.repo_dir("repo") / "held.json").write_text(json.dumps(hold))  # a press under a hold
    readings = _press(checkout, main, frm=frm)
    code.write_text("3")
    readings, _ = _pass(checkout, readings)

    def no_tree(root, repo, sha):
        raise ValueError("the rollback's worktree cannot be made: git worktree: no room")

    monkeypatch.setattr(promote, "make_tree", no_tree)
    readings, _ = _pass(checkout, readings)
    why = f"smoke failed on {main[:7]}: smoke says 3; its rollback could not start: the rollback's worktree"
    assert readings["repo"]["failed"]["why"].startswith(why) and readings["repo"]["live"] == main
    assert promote.held("repo") == hold  # neither cleared nor rewritten


def test_the_smokes_rollback_whose_run_cannot_be_spawned_is_a_failure_with_both_reasons(
    checkout, monkeypatch, tmp_path
):
    """The techlead's note on #2033: `start`'s Popen raising (an OSError, not the tree's ValueError)
    is a rollback that cannot start too — a failure with both reasons, no intent left in flight."""
    _, code = _smoke_block(checkout)
    frm = _git(checkout, "rev-parse", "HEAD")
    main = _merge(checkout, "b")
    readings = _press(checkout, main, frm=frm)
    code.write_text("3")
    readings, _ = _pass(checkout, readings)
    monkeypatch.setattr(promote, "make_tree", lambda root, repo, sha: tmp_path / "gone")  # Popen's cwd is missing
    readings, _ = _pass(checkout, readings)
    why = f"smoke failed on {main[:7]}: smoke says 3; its rollback could not start: "
    assert readings["repo"]["failed"]["why"].startswith(why) and readings["repo"]["live"] == main
    assert promote.inflight("repo") is None and readings["repo"]["inflight"] is None


def test_a_press_under_a_hold_whose_smoke_fails_rewrites_the_hold(checkout):
    _, code = _smoke_block(checkout)
    frm = _git(checkout, "rev-parse", "HEAD")
    main = _merge(checkout, "b")
    promote.repo_dir("repo").mkdir(parents=True)
    (promote.repo_dir("repo") / "held.json").write_text(json.dumps({"sha": frm, "from": "f" * 40}))
    readings = _press(checkout, main, frm=frm)
    code.write_text("5")
    readings, _ = _pass(checkout, readings)
    readings, _ = _pass(checkout, readings)
    code.write_text("0")
    readings, _ = _pass(checkout, readings)
    readings, _ = _pass(checkout, readings)
    h = promote.held("repo")
    assert (h["sha"], h["from"], h["by"]) == (frm, "f" * 40, "smoke")  # its first `from` kept


def test_a_rollback_to_a_tree_with_no_smoke_concludes_on_check_and_says_so(checkout):
    _smoke_block(checkout, smoke=None)
    old = _git(checkout, "rev-parse", "HEAD")
    _smoke_block(checkout)
    new = _git(checkout, "rev-parse", "HEAD")
    readings = _press(checkout, old, "rollback", new)
    assert readings["repo"]["inflight"]["smoke"] is None
    readings, notes = _pass(checkout, readings)
    assert notes == [
        f"rolled back `repo` to `{old[:7]}` from `{new[:7]}` — main is 1 commit ahead · no smoke in `{old[:7]}`"
    ]
    # a tree whose block cannot be read runs none either, and says why
    (checkout / ".shiftlead.yml").write_text("promote: {run: x\n")
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-q", "-m", "broken block")
    broken = _git(checkout, "rev-parse", "HEAD")
    _smoke_block(checkout)
    readings = _press(checkout, broken, "rollback", old)
    readings, notes = _pass(checkout, readings)
    assert f"· no smoke read in `{broken[:7]}`: " in notes[0] and "cannot be read" in notes[0]


def test_the_smoke_is_kept_on_the_intent_when_the_checkout_moves(checkout):
    seen, _ = _smoke_block(checkout)
    main = _merge(checkout, "b")
    readings = _press(checkout, main)
    _wait_gone(readings["repo"]["inflight"]["pid"])  # the run has read the checkout's HEAD
    _smoke_block(checkout, smoke="exit 9")  # a pull moves the checkout before `check` reads main
    (checkout.parent / "live").write_text(main + "\n")
    readings, _ = _pass(checkout, readings)
    readings, notes = _pass(checkout, readings)
    assert "smoke passed" in notes[0] and seen.read_text().split()[2] == main


def test_an_agent_restarted_mid_smoke_reads_its_result(checkout):
    _smoke_block(checkout)
    main = _merge(checkout, "b")
    d = promote.repo_dir("repo")
    d.mkdir(parents=True)
    log = d / f"{main}.log"
    log.write_text("── smoke ──\nall well\n")
    at = datetime.now(UTC)
    intent = {"sha": main, "at": at.isoformat(), "pid": 999999999, "log": str(log), "by": "auto", "ahead": 1}
    mine = os.getpid()  # a live process another agent started: signalled, never polled
    intent |= {"smoke": "x", "phase": "smoke", "smoke_pid": mine, "smoke_started": promote.proc_start(mine)}
    intent["smoke_at"] = at.isoformat()
    (d / "inflight.json").write_text(json.dumps(intent))
    (checkout.parent / "live").write_text(main + "\n")
    readings, _, _ = promote.survey([str(checkout)], {}, False, {}, at)
    assert readings["repo"]["inflight"]["phase"] == "smoke"  # no result yet, the smoke not ours: in the bound
    (d / "smoke.json").write_text(json.dumps({"sha": main, "exit": 0, "at": (at + timedelta(seconds=48)).isoformat()}))
    readings, notes, _ = promote.survey([str(checkout)], readings, False, {}, at)
    assert notes == [f"promoted `repo` `{main[:7]}` — 1 commit · smoke passed in 48 s"]
