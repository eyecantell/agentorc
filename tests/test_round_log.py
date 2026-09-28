"""A session's round log (design §4.8 *A session's round log*, §4.6, §4.5a, TD-191): `ao log` appends
a stamped line for the caller's own record, beside the run logs, keyed by the name in its repo;
`log_tail` reads it back; the prune keeps it while a record of the name is live; the manager
template says `ao log` and commits nothing; Focus draws the last two lines."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from importlib import resources

import pytest

from sessionorc import paths
from sessionorc.client import AgentError, LocalClient

SHELL = {"adapter": "shell", "argv": ["bash", "--norc"], "unattended": True}


async def _mk(person, tmp_path, name="m", **kw) -> str:
    return (await person.call("create", name=name, dir=str(tmp_path), **SHELL, **kw))["id"]


async def test_only_the_session_writes_its_round_log_and_anyone_reads_it(agent, tmp_path):
    async with LocalClient() as person:
        m = await _mk(person, tmp_path)
        other = await _mk(person, tmp_path, name="o")
        with pytest.raises(AgentError, match="only .* may write its round log"):
            await person.call("log", id=m, text="a person's line")
        async with LocalClient(caller=other) as oc:
            with pytest.raises(AgentError, match="only .* may write its round log"):
                await oc.call("log", id=m, text="a sibling's line")
        assert await person.call("log_tail", id=m) == []
        async with LocalClient(caller=m) as mc:
            with pytest.raises(AgentError, match="needs a line"):
                await mc.call("log", id=m, text="  \n")
            e = await mc.call("log", id=m, text="20:10  w1: idle → nudged\nsecond line dropped\x1b[31m")
            assert e["text"] == "20:10  w1: idle → nudged" and len(e["at"]) == len("2026-09-27T20:10Z")
            await mc.call("log", id=m, text="21:10  w1: working → ok")
            await mc.call("log", id=m, text="x" * 900)
        got = await person.call("log_tail", id=m, n=2)
        assert [g["text"] for g in got] == ["21:10  w1: working → ok", "x" * 500]
        assert len(await person.call("log_tail", id=m)) == 3
        path = paths.rounds_log(m)  # the base id: `ao-<repo or dir>-<name>`
        assert path.name == f"{m}.rounds.log" and path.parent == paths.runs_dir()


async def test_a_second_record_under_the_name_continues_the_file(agent, tmp_path):
    async with LocalClient() as person:
        m = await _mk(person, tmp_path)
        async with LocalClient(caller=m) as mc:
            await mc.call("log", id=m, text="run 1")
        await person.call("kill", id=m)
        m2 = await _mk(person, tmp_path)  # the name's next start: its record takes the base id
        assert m2 == m or paths.rounds_log(m).exists()
        async with LocalClient(caller=m2) as mc:
            await mc.call("log", id=m2, text="run 2")
        assert [g["text"] for g in await person.call("log_tail", id=m2)] == ["run 1", "run 2"]


async def test_the_prune_keeps_a_live_names_round_log_and_drops_an_old_one(agent, tmp_path):
    async with LocalClient() as person:
        m = await _mk(person, tmp_path)
        async with LocalClient(caller=m) as mc:
            await mc.call("log", id=m, text="kept")
    old = time.time() - 90 * 86400
    kept, gone = paths.rounds_log(m), paths.rounds_log("ao-elsewhere-gone")
    gone.write_text("2026-06-01T00:00Z long ago\n")
    for f in (kept, gone):
        os.utime(f, (old, old))
    ended = ("exited", "closed")
    live = {str(agent._rounds_log(s)) for s in agent.sessions.values() if s.state not in ended}
    await asyncio.to_thread(agent._prune_runs, datetime.now(UTC), live)
    assert kept.exists() and not gone.exists()


def test_ao_log_writes_and_reads_its_own(subprocess_agent, tmp_path, capsys, monkeypatch):
    from agentorc import cli

    assert cli.main(["--json", "shell", "m", "-d", str(tmp_path)]) == 0
    sid = json.loads(capsys.readouterr().out)["id"]
    monkeypatch.setenv("AGENTORC_SESSION", sid)
    assert cli.main(["log", "--tail"]) == 0
    assert capsys.readouterr().out.strip() == f"{sid}: no round log"
    assert cli.main(["log", "20:10  w1:", "idle → ok"]) == 0
    assert "logged" in capsys.readouterr().out
    assert cli.main(["--json", "log", "--tail", "5"]) == 0
    assert [e["text"] for e in json.loads(capsys.readouterr().out)] == ["20:10  w1: idle → ok"]
    assert cli.main(["log", "--id", sid, "a line"]) == 2
    assert "--id goes with --tail" in capsys.readouterr().err
    assert cli.main(["log"]) == 2
    assert "needs a line" in capsys.readouterr().err
    monkeypatch.delenv("AGENTORC_SESSION")
    assert cli.main(["log", "--tail", "1", "--id", sid]) == 0  # a person reads it by id
    assert capsys.readouterr().out.endswith("20:10  w1: idle → ok\n")
    cli.main(["kill", sid])


def test_the_manager_template_logs_and_commits_nothing():
    text = resources.files("agentorc").joinpath("briefs", "manager.md").read_text(encoding="utf-8")
    assert '`ao log "HH:MM' in text and "Nothing is committed" in text
    assert "push your log" not in text and "on your launch branch:" not in text
    assert "`ao log --tail 20`" in text


def test_the_focus_rounds_line(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import rounds_lines

    s = {"created": "2026-09-27T20:00:30Z"}
    assert rounds_lines(s, None) is None  # the host agent could not say: no line at all
    assert rounds_lines(s, []) == {"lines": []}  # *no round log*
    got = rounds_lines(
        s, [{"at": "2026-09-26T23:50Z", "text": "last night"}, {"at": "2026-09-27T20:00Z", "text": "now"}]
    )
    assert [(x["text"], x["earlier"]) for x in got["lines"]] == [("last night", True), ("now", False)]
