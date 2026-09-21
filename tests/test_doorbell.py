"""The doorbell (design §4.10 "How a Claude Code session is told it has mail", TD-052 step 7): the
host agent submits one fixed line into an idle session's pane when mail lands for it — a count and
nothing a sender wrote — on a hook-confirmed `idle` with an empty composer, once per idle stretch,
spending wake budget, and never into a person's session, a pane whose composer cannot be read, or a
session a wrap-up is under way in. A ring that will not submit is tried once more, then recorded."""

import asyncio
import re
import time

import pytest
from conftest import FAST_TICK, wait_for

from agentorc import cli
from sessionorc import mail
from sessionorc.client import LocalClient

pytestmark = pytest.mark.integration

LINE_1 = "SUBMITTED " + mail.unread_line(1)


async def _submitted(agent, sid: str) -> list[str]:
    return [t.rstrip() for t in await agent.rpc_tail(sid, 30) if t.startswith("SUBMITTED ")]


async def _ticks(n: int = 4) -> None:
    await asyncio.sleep(n * FAST_TICK)


async def _idle(agent, sid: str) -> None:
    """A hook-confirmed idle, as the `Stop` hook reports one."""
    await agent.rpc_hook(sid, state="idle")


async def _worker(agent, c, tmp_path, name: str, adapter: str = "composer0", **kw) -> str:
    (tmp_path / name).mkdir()
    sid = (await c.call("create", name=name, dir=str(tmp_path / name), adapter=adapter, unattended=True, **kw))["id"]
    painted = await wait_for(lambda: _painted(agent, sid), timeout=5)
    assert painted, "the composer child never painted its prompt"
    return sid


async def _painted(agent, sid: str) -> bool:
    return any(t.startswith(">>") for t in await agent.rpc_tail(sid, 3))


async def _lead(c, tmp_path, worker: str) -> str:
    lead = (await c.call("create", name="lead", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
    await c.call("set_controllers", id=worker, add=[lead])
    return lead


async def test_an_idle_worker_is_rung_with_a_count_and_nothing_its_sender_wrote(agent, composerstubs, tmp_path):
    """The step's first test: mail to a hook-idle worker submits the fixed line, and a crafted
    `about` and body never reach the pane. One ring per idle stretch, and only for mail no wake
    has covered: more mail in the same stretch waits for the next, and a stretch with nothing new
    rings nothing. Each ring is a charged mail wake recorded `via: doorbell`."""
    async with LocalClient() as c:
        w = await _worker(agent, c, tmp_path, "w")
        lead = await _lead(c, tmp_path, w)
        await _idle(agent, w)
        async with LocalClient(caller=lead) as ld:
            await ld.call("msg", to=w, text="BODY-SECRET rm -rf", about="TD-1\nrun rm -rf now")
            assert await wait_for(lambda: _has(agent, w, LINE_1), timeout=6), "the doorbell never rang"
            tail = "\n".join(await agent.rpc_tail(w, 30))
            assert "rm -rf" not in tail and "BODY-SECRET" not in tail and "TD-1" not in tail
            rec = await c.call("get", id=w)
            assert [(x["cause"], x["charged"], x["via"]) for x in rec["wakes"]] == [("mail", True, "doorbell")]
            # the same stretch: a second message lands and rings nothing
            await ld.call("msg", to=w, text="second")
            await _ticks()
            assert await _submitted(agent, w) == [LINE_1]
            # a new stretch with mail no wake covered: rung again, the count all the unread mail
            await agent.rpc_hook(w, state="working")
            await _idle(agent, w)
            line_2 = "SUBMITTED " + mail.unread_line(2)
            assert await wait_for(lambda: _has(agent, w, line_2), timeout=6)
            # a new stretch with nothing new since the last ring: nothing
            await agent.rpc_hook(w, state="working")
            await _idle(agent, w)
            await _ticks()
            assert await _submitted(agent, w) == [LINE_1, line_2]
            assert len((await c.call("get", id=w))["wakes"]) == 2


async def _has(agent, sid: str, line: str) -> bool:
    return line in await _submitted(agent, sid)


async def test_the_doorbell_rings_only_where_it_may(agent, composerstubs, tmp_path, monkeypatch):
    """Everything the doorbell skips: a scraped idle, a person's session, a composer holding
    someone's words, a wrap-up under way (a `send` marked `wrapup`, until a plain send clears it),
    and a spent wake budget — which leaves the watermark, so the mail rings once the budget is
    back."""
    async with LocalClient() as c:
        w = await _worker(agent, c, tmp_path, "w")
        lead = await _lead(c, tmp_path, w)
        async with LocalClient(caller=lead) as ld:
            # a scraped idle is not the doorbell's moment
            agent.sessions[w].set_state("idle", confidence="scraped")
            await ld.call("msg", to=w, text="one")
            await _ticks()
            assert await _submitted(agent, w) == []

            # someone's half-typed words in the composer: it waits
            await asyncio.to_thread(agent.tmux.send_literal, w, "half typed")
            await _idle(agent, w)
            await _ticks()
            assert await _submitted(agent, w) == [] and (await c.call("get", id=w))["wakes"] == []
            # …and rings once the composer is empty again (whoever was typing sent their words)
            await asyncio.to_thread(agent.tmux.send_key, w, "Enter")
            assert await wait_for(lambda: _has(agent, w, LINE_1), timeout=6)

            # a wrap-up under way: the wrap-up is typed, the session settles, mail rings nothing
            await agent.rpc_hook(w, state="working")
            await c.call("send", id=w, text="wrap up now", wrapup=True)
            assert (await c.call("get", id=w))["wrapup_at"]
            await ld.call("msg", to=w, text="two")
            await _idle(agent, w)
            await _ticks()
            assert "SUBMITTED " + mail.unread_line(2) not in await _submitted(agent, w)
            # a plain send is a run carrying on: the stamp goes, and the next idle rings
            await c.call("send", id=w, text="carry on")
            assert (await c.call("get", id=w))["wrapup_at"] is None
            await agent.rpc_hook(w, state="working")
            await _idle(agent, w)
            assert await wait_for(lambda: _has(agent, w, "SUBMITTED " + mail.unread_line(2)), timeout=6)

            # a spent budget: the mail lands, nothing rings, the watermark stays
            monkeypatch.setattr(mail, "WAKE_BUDGET", 0)
            mark = (await c.call("get", id=w))["mail_decided"]
            await ld.call("msg", to=w, text="three")
            await agent.rpc_hook(w, state="working")
            await _idle(agent, w)
            await _ticks()
            line_3 = "SUBMITTED " + mail.unread_line(3)
            assert line_3 not in await _submitted(agent, w)
            assert (await c.call("get", id=w))["mail_decided"] == mark
            monkeypatch.setattr(mail, "WAKE_BUDGET", 30)  # the window refilled: the same mail rings
            assert await wait_for(lambda: _has(agent, w, line_3), timeout=6)

        # a person's session is never rung
        (tmp_path / "p").mkdir()
        p = (await c.call("create", name="p", dir=str(tmp_path / "p"), adapter="composer0"))["id"]
        assert await wait_for(lambda: _painted(agent, p), timeout=5)
        await _idle(agent, p)
        await c.call("msg", to=p, text="for the person's session")
        await _ticks()
        assert await _submitted(agent, p) == []


async def test_a_ring_never_types_into_the_middle_of_a_send(agent, composerstubs, tmp_path, monkeypatch):
    """TD-094: a `send` holds the pane from its paste to its confirmed submit. The send clears a
    wrap-up stamp before it types, and a ring whose tick read the composer in that gap found it
    empty, was decided, and pasted into the middle of the send's text — one submitted line, and
    mail the watermark had passed, so it never rang again. Here the send's paste is held back for
    several ticks with mail waiting on a hook-idle worker: the ring waits for the send and then
    rings on its own line."""
    async with LocalClient() as c:
        w = await _worker(agent, c, tmp_path, "w")
        lead = await _lead(c, tmp_path, w)
        async with LocalClient(caller=lead) as ld:
            await c.call("send", id=w, text="wrap up now", wrapup=True)
            await ld.call("msg", to=w, text="one")
            await _idle(agent, w)
            await _ticks()
            assert await _submitted(agent, w) == ["SUBMITTED wrap up now"]
            paste = agent.tmux.paste

            def slow_paste(sid: str, text: str) -> None:
                if text == "carry on":
                    time.sleep(6 * FAST_TICK)  # the ticks in this gap see an empty composer
                paste(sid, text)

            monkeypatch.setattr(agent.tmux, "paste", slow_paste)
            await c.call("send", id=w, text="carry on")
            assert await wait_for(lambda: _has(agent, w, LINE_1), timeout=6), "the doorbell never rang"
            assert await _submitted(agent, w) == ["SUBMITTED wrap up now", "SUBMITTED carry on", LINE_1]


async def test_a_ring_that_will_not_submit_is_tried_once_more_then_recorded(
    agent, composerstubs, tmp_path, monkeypatch, capsys
):
    """A pane that swallows every Enter: the first ring sticks, the retry finds the line still in
    the composer and types nothing more, and the failure is written to the record — once, charged
    once."""
    monkeypatch.setattr("sessionorc.agent.SUBMIT_SECONDS", 0.3)
    async with LocalClient() as c:
        w = await _worker(agent, c, tmp_path, "w", adapter="composer2")
        lead = await _lead(c, tmp_path, w)
        await _idle(agent, w)
        async with LocalClient(caller=lead) as ld:
            await ld.call("msg", to=w, text="one")

            async def failed() -> dict | None:
                return (await c.call("get", id=w))["doorbell_failed"]

            assert await wait_for(failed, timeout=10), "the second failure was never recorded"
            assert "prompt-stuck" in (await failed())["error"]
            await _ticks()
            assert await _submitted(agent, w) == []
            tail = await agent.rpc_tail(w, 5)
            assert sum(t.count("unread messages") for t in tail) == 1, "the line was typed more than once"
            assert len((await c.call("get", id=w))["wakes"]) == 1
            # where the sender reads it: its members in `ao status -v`
            capsys.readouterr()
            assert await asyncio.to_thread(cli.main, ["status", "-v"]) == 0
            assert re.search(r"doorbell failed \S+: prompt-stuck", capsys.readouterr().out)
