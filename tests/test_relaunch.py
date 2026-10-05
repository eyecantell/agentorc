"""TD-309 slice 5, design §4.9c *Switching*: a person's Apply relaunches a live member — `relaunch {id,
launch}` replaces `prompt`, `prompt_from`, `lane` and `review` in its launch record (an absent one
removed, the rest standing), re-records `brief`, and marks the record `relaunch: {at}`, which rule 7
reads as its second trigger: an idle member with its work pushed is closed and replayed by the tick,
`why: flow`, from the new launch record; a working one is told on its `ao` replies."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks
from test_brief_replay import _made_from, _merge, _repo

from agentorc.ending import closer_words
from sessionorc import paths, work
from sessionorc.agent import RESTART_SETTLE
from sessionorc.agent_common import FLOW_CLAUSE
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import WRAPUP_PROMPT

SHELL = {"adapter": "shell", "argv": ["bash", "--norc", "--noprofile"]}


def _launch(sid: str) -> dict:
    return json.loads((paths.launch_dir() / f"{sid}.json").read_text())


def _idle(rec) -> None:
    rec.state, rec.confidence, rec.pending = "idle", "hook", None
    rec.git = {"dirty": 0, "unpushed": 0}


@pytest.mark.integration
async def test_a_relaunch_replaces_the_launch_record_and_the_tick_restarts_on_it_once(agent, tmp_path):
    await park_ticks(agent)
    repo = _repo(tmp_path)
    _merge(repo, "first {lane}\n")
    made = _made_from(tmp_path, repo)
    work = tmp_path / "work"
    work.mkdir()
    review = {"reader": "techlead", "held": ["src/**"]}
    async with LocalClient() as person:
        sid = (
            await person.call(
                "create",
                name="m",
                dir=str(work),
                unattended=True,
                supervised=True,
                prompt="old",
                prompt_from=made,
                lane=["TD-1"],
                review=review,
                **SHELL,
            )
        )["id"]
        rec = agent.sessions[sid]
        rec.brief_changed = {"at": "2026-10-05T07:00:00Z", "paths": ["/r/b.md"]}
        new_from = {**made, "slots": {**made["slots"], "{lane}": {"text": "TD-9"}}}
        view = await person.call(
            "relaunch", id=sid, launch={"prompt": "composed", "prompt_from": new_from, "lane": ["TD-9"]}
        )
        assert view["relaunch"]["at"] and rec.relaunch and rec.brief_changed is None
        got = _launch(sid)
        assert got["lane"] == ["TD-9"] and got["prompt_from"] == new_from and got["prompt"] == "composed"
        assert "review" not in got  # absent from the launch handed: removed (a switch to `build`)
        assert got["dir"] == str(work) and got["adapter"] == "shell" and got["name"] == "m"  # the rest stands
        assert rec.brief and rec.brief["sources"]  # re-recorded from the new prompt_from
        assert rec.lane == ["TD-001"]  # the running record is not touched: the replay brings the new launch
        # idle, holding nothing, its work pushed: the tick closes and replays it from the new record
        _idle(rec)
        now = datetime.now(UTC)
        await agent._keep_running(now)
        new = agent.sessions[sid]
        assert new is not rec and [r["why"] for r in new.restarts] == ["flow"]
        assert closer_words(rec.view()) == "closed by the tick · flow changed"  # the card says why it closed
        assert new.relaunch is None and new.lane == ["TD-009"] and new.review is None
        assert _launch(sid)["prompt"] == "P\nbase: first TD-9 / lane TD-9\n"  # refilled from the new prompt_from
        _idle(new)
        await agent._keep_running(now + RESTART_SETTLE + timedelta(seconds=1))
        assert [r["why"] for r in agent.sessions[sid].restarts] == ["flow"]  # the create cleared the mark
        with contextlib.suppress(Exception):
            await person.call("kill", id=sid)


@pytest.mark.integration
async def test_a_relaunch_of_a_record_a_failed_brief_restart_left_closed_restarts_it(agent, tmp_path):
    """TD-334, the techlead's read of #1099: the tick closed a member for `brief` and the replay failed, so
    the record stands closed with the `brief` mark; the person's Apply clears `brief_changed` and marks
    `relaunch`, and rule 7 retries it under `flow` rather than leaving it closed for good."""
    await park_ticks(agent)
    repo = _repo(tmp_path)
    _merge(repo, "first {lane}\n")
    made = _made_from(tmp_path, repo)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    async with LocalClient() as person:
        params = {"name": "m", "dir": str(work_dir), "unattended": True, "supervised": True, **SHELL}
        sid = (await person.call("create", prompt="old", prompt_from=made, lane=["TD-1"], **params))["id"]
        await person.call("kill", id=sid)
        rec = agent.sessions[sid]
        failed = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        rec.state, rec.closed_at = "closed", failed
        rec.closed_for = {"why": "brief", "closed_at": failed}
        rec.restarts = [{"at": failed, "why": "brief", "error": "create: boom"}]
        rec.brief_changed = {"at": failed, "paths": ["/r/b.md"]}
        rec.git = {"dirty": 0, "unpushed": 0}
        await person.call("relaunch", id=sid, launch={"prompt": "composed", "prompt_from": made, "lane": ["TD-9"]})
        assert rec.brief_changed is None and rec.relaunch
        # the retry fails as well: the mark follows the trigger it was replayed under, so the next tick takes
        # it again rather than leaving the record closed for good (the techlead's read of #1116)
        real = agent.rpc_create

        async def boom(**kw):
            raise RuntimeError("create: boom again")

        agent.rpc_create = boom
        try:
            await agent._keep_running(datetime.now(UTC))
        finally:
            agent.rpc_create = real
        assert agent.sessions[sid] is rec and rec.state == "closed"
        assert [(r["why"], bool(r.get("error"))) for r in rec.restarts] == [("brief", True), ("flow", True)]
        assert rec.closed_for == {"why": "flow", "closed_at": failed}  # a person's later Close still never matches
        later = datetime.now(UTC) + RESTART_SETTLE + timedelta(seconds=1)
        await agent._keep_running(later)
        new = agent.sessions[sid]
        assert new is not rec and [r["why"] for r in new.restarts] == ["brief", "flow", "flow"]
        assert new.state != "closed" and new.relaunch is None and new.lane == ["TD-009"]
        with contextlib.suppress(Exception):
            await person.call("kill", id=sid)


@pytest.mark.integration
async def test_a_relaunch_is_a_persons_and_refused_touching_nothing(agent, tmp_path):
    await park_ticks(agent)
    params = {"dir": str(tmp_path), **SHELL}
    async with LocalClient() as person:
        sid = (await person.call("create", name="a", unattended=True, supervised=True, prompt="p", **params))["id"]
        loose = (await person.call("create", name="b", unattended=True, prompt="p", **params))["id"]
        mine = (await person.call("create", name="c", supervised=True, prompt="p", **params))["id"]
        before = _launch(sid)
        refusals = [
            ({"id": sid}, "composed launch"),
            ({"id": sid, "launch": {"dir": "/x"}}, "alone, not dir"),
            ({"id": sid, "launch": {"lane": "TD-1"}}, "lane is a list, not str"),
            ({"id": loose, "launch": {"prompt": "x"}}, "no launch record"),
            ({"id": mine, "launch": {"prompt": "x"}}, "interactive"),
        ]
        for kw, words in refusals:
            with pytest.raises(AgentError, match=words):
                await person.call("relaunch", **kw)
        async with LocalClient(caller=sid) as member:
            with pytest.raises(AgentError, match="person"):
                await member.call("relaunch", id=sid, launch={"prompt": "x"})
        assert _launch(sid) == before and agent.sessions[sid].relaunch is None
        for x in (sid, loose, mine):
            with contextlib.suppress(Exception):
                await person.call("kill", id=x)


async def test_a_relaunched_member_is_told_on_its_replies_and_its_restart_is_never_early(agent, tmp_path):
    from sessionorc import client as clientmod

    async with LocalClient() as person:
        params = {"dir": str(tmp_path), "adapter": "shell", "argv": ["bash", "--norc"], "unattended": True}
        w = (await person.call("create", name="w", **params))["id"]
    async with LocalClient(caller=w) as wc:
        agent.sessions[w].relaunch = {"at": "2026-10-05T07:00:00Z"}
        await wc.call("get", id=w)
        assert clientmod.last_mail["brief"] == FLOW_CLAUSE
        await wc.call("progress", id=w, ref=None, status="restart", why="flow changed")
        assert agent.sessions[w].restart_wanted.get("early") is None
        assert not (clientmod.last_mail or {}).get("brief")  # declared: the clause is not repeated
        agent.sessions[w].relaunch = None
    clientmod.last_mail = None


# -- slice 5b: the sit-out form -------------------------------------------------------------------------


@pytest.mark.integration
async def test_a_sit_out_sends_the_wrap_up_and_the_tick_closes_and_marks_it_once_settled(agent, tmp_path):
    await park_ticks(agent)
    params = {"dir": str(tmp_path), **SHELL}
    async with LocalClient() as person:
        sid = (await person.call("create", name="d", unattended=True, supervised=True, prompt="p", **params))["id"]
        rec = agent.sessions[sid]
        view = await person.call("relaunch", id=sid, sit_out=True)
        assert view["sit_out"]["at"] and rec.wrapup_at  # the wrap-up, as Members… remove sends it
        assert rec.sends[-1].text == WRAPUP_PROMPT
        again = await person.call("relaunch", id=sid, sit_out=True)  # said twice: nothing more is sent
        assert again["sit_out"] == view["sit_out"] and len(rec.sends) == 1
        with pytest.raises(AgentError, match="sat out by its team's flow"):  # nor relaunched while it sits out
            await person.call("relaunch", id=sid, launch={"prompt": "x"})
        # settled with work left: it stays open, tick after tick, with no restart and no mark
        _idle(rec)
        rec.git = {"dirty": 1, "unpushed": 0}
        now = datetime.now(UTC)
        await agent._keep_running(now)
        assert rec.state == "idle" and rec.closed_for is None and rec.restarts == []
        # pushed: closed by the tick and marked, never started again
        rec.git = {"dirty": 0, "unpushed": 0}
        await agent._keep_running(now + timedelta(seconds=1))
        assert rec.state == "closed" and rec.closed_for == {"why": "sit_out", "closed_at": rec.closed_at}
        assert work.sat_out(rec) and rec.restarts == [] and rec.closer["why"] == "sit_out"
        assert closer_words(rec.view()) == "closed by the tick · flow sat it out"
        rec.restart_wanted = {"at": "2026-10-05T07:00:00Z", "why": "x"}  # a declaration from its wrap-up
        rec.relaunch = {"at": "2026-10-05T07:00:00Z"}
        await agent._keep_running(now + RESTART_SETTLE + timedelta(seconds=2))
        assert agent.sessions[sid] is rec and rec.state == "closed" and rec.restarts == []  # rules 2 and 7 pass
        with contextlib.suppress(Exception):
            await person.call("remove", id=sid)


@pytest.mark.integration
async def test_a_sit_out_of_an_exited_member_is_marked_as_it_stands_and_never_crash_restarted(agent, tmp_path):
    await park_ticks(agent)
    params = {"dir": str(tmp_path), **SHELL}
    async with LocalClient() as person:
        sid = (await person.call("create", name="e", unattended=True, supervised=True, prompt="p", **params))["id"]
        rec = agent.sessions[sid]
        await person.call("relaunch", id=sid, sit_out=True)
        rec.state, rec.pane, rec.exit_code, rec.since = "exited", True, 0, "2026-10-05T08:00:00Z"
        await agent._keep_running(datetime.now(UTC))
        assert rec.closed_for == {"why": "sit_out", "closed_at": "2026-10-05T08:00:00Z"}
        assert agent.sessions[sid] is rec and rec.restarts == []  # rule 1 never restarts it
        with contextlib.suppress(Exception):
            await person.call("remove", id=sid)


@pytest.mark.integration
async def test_a_sit_out_is_refused_touching_nothing(agent, tmp_path):
    from sessionorc.models import Pending

    await park_ticks(agent)
    params = {"dir": str(tmp_path), **SHELL}
    async with LocalClient() as person:
        sid = (await person.call("create", name="a", unattended=True, supervised=True, prompt="p", **params))["id"]
        mine = (await person.call("create", name="c", supervised=True, prompt="p", **params))["id"]
        seat = (await person.call("create", name="t", unattended=True, supervised=True, prompt="p", **params))["id"]
        agent.sessions[seat].seat = {"trigger": "asks"}
        refusals = [
            ({"id": sid, "launch": {"prompt": "x"}}, "takes no launch"),
            ({"id": mine}, "interactive"),
            ({"id": seat}, "is a seat"),
        ]
        for kw, words in refusals:
            with pytest.raises(AgentError, match=words):
                await person.call("relaunch", sit_out=True, **kw)
        agent.sessions[sid].pending = Pending(kind="question", text="q")
        with pytest.raises(AgentError, match="the wrap-up could not be sent"):
            await person.call("relaunch", id=sid, sit_out=True)
        assert all(agent.sessions[x].sit_out is None and not agent.sessions[x].sends for x in (sid, mine, seat))
        agent.sessions[sid].pending = None
        for x in (sid, mine, seat):
            with contextlib.suppress(Exception):
                await person.call("kill", id=x)


@pytest.mark.unit
def test_a_sat_out_member_is_passed_over_by_wound_down_and_finished():
    out = {"at": "2026-10-05T07:00:00Z", "why": "x"}
    sat = {"name": "designer-1", "closed_for": {"why": "sit_out", "closed_at": "t"}}
    assert work.wound_down([{"name": "grinder-1", "out_of_work": out}, sat]) == out["at"]
    grinder = {"id": "g", "name": "grinder-1", "unattended": True, "state": "idle", "out_of_work": out}
    gone = {**sat, "id": "d", "unattended": True, "state": "closed", "restart_wanted": out}
    assert work.finished([grinder, gone])["why"] == []  # its restart word is not read: the flow sat it out
