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
from sessionorc import paths
from sessionorc.agent import RESTART_SETTLE
from sessionorc.agent_common import FLOW_CLAUSE
from sessionorc.client import AgentError, LocalClient

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
