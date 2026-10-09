"""TD-259 slice 2, design §6 rule 3 *A manager on call is a seat of this rule*: the `team` trigger.
The tick writes `idle_open` on a member still idle after rule 4's nudge, sets a manager on call's
`seat_due` from four readings of the records — a question waiting, a member's permission, a member
`stalled?`, a member *idle · open work* — fills the seat once per cause (`seat_filled`), and closes
it when it has acted."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks

from sessionorc import reports
from sessionorc.agent import FILL_CEILING, IDLE_NUDGE, SEAT_IDLE_GRACE
from sessionorc.client import LocalClient
from sessionorc.models import ProgressEntry

pytestmark = pytest.mark.integration

SHELL = {"adapter": "shell", "argv": ["bash", "--norc", "--noprofile"], "unattended": True, "supervised": True}


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


@pytest.fixture(autouse=True)
def _no_gh(monkeypatch):
    monkeypatch.setattr(reports, "merged_prs", lambda *a, **kw: None)


async def _manager(person, tmp_path, name="mgr", **kw) -> str:
    (tmp_path / name).mkdir(exist_ok=True)
    params = {"name": name, "dir": str(tmp_path / name), "prompt": "the seat's brief", "seat": {"trigger": "team"}}
    return (await person.call("create", **{**SHELL, **params, **kw}))["id"]


async def _member(person, tmp_path, name: str, manager: str | None, **kw) -> str:
    (tmp_path / name).mkdir(exist_ok=True)
    params = {"name": name, "dir": str(tmp_path / name), "controllers": [manager] if manager else []}
    return (await person.call("create", **{**SHELL, **params, **kw}))["id"]


def _end(agent, sid: str) -> None:
    rec = agent.sessions[sid]
    rec.state, rec.pane, rec.exit_code = "exited", True, 0
    agent.store.save(rec)


def _idle(agent, sid: str, now: datetime) -> None:
    rec = agent.sessions[sid]
    rec.state, rec.confidence, rec.since = "idle", "hook", _iso(now - SEAT_IDLE_GRACE - timedelta(seconds=1))
    rec.git = {"branch": "main", "dirty": 0, "unpushed": 0}


async def test_idle_open_is_written_after_the_nudge_and_cleared_with_the_stretch(agent, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(person, tmp_path, "w", None, lane=["TD-7"])
        rec = agent.sessions[sid]
        rec.state, rec.confidence, rec.since = "idle", "hook", _iso(now - 2 * IDLE_NUDGE)
        rec.nudged_at = _iso(now - IDLE_NUDGE + timedelta(minutes=1))
        await agent._idle_open(rec, now)
        assert rec.idle_open is None, "not yet twenty minutes after the nudge"
        rec.nudged_at = _iso(now - IDLE_NUDGE)
        await agent._idle_open(rec, now)
        assert rec.idle_open and rec.idle_open["ref"] == "TD-007"
        mark = dict(rec.idle_open)
        await agent._idle_open(rec, now + timedelta(minutes=5))
        assert rec.idle_open == mark, "it stands while the stretch does"
        # the work closed: nothing is open
        rec.progress = [ProgressEntry(ref="TD-007", status="dropped", why="not mine")]
        await agent._idle_open(rec, now)
        assert rec.idle_open is None
        # the state changed, and a new stretch has no nudge of its own yet
        rec.progress, rec.idle_open = [], dict(mark)
        rec.state = "working"
        await agent._idle_open(rec, now)
        assert rec.idle_open is None
        rec.state, rec.since = "idle", _iso(now)
        await agent._idle_open(rec, now + IDLE_NUDGE)
        assert rec.idle_open is None, "a nudge before this stretch is not this stretch's"
        # a seat, an unsupervised member and one that declared are never marked
        rec.since = _iso(now - 2 * IDLE_NUDGE)
        never = (("seat", {"trigger": "asks"}), ("supervised", False), ("out_of_work", {"at": "x", "why": "y"}))
        for field, value in never:
            old = getattr(rec, field)
            setattr(rec, field, value)
            await agent._idle_open(rec, now)
            assert rec.idle_open is None, field
            setattr(rec, field, old)
        await person.call("kill", id=sid)


async def test_each_reading_of_a_member_fills_the_manager_once(agent, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        mgr = await _manager(person, tmp_path)
        first = agent.sessions[mgr]
        _end(agent, mgr)
        a = await _member(person, tmp_path, "a", mgr)
        b = await _member(person, tmp_path, "b", mgr)
        other = await _member(person, tmp_path, "other", "ao-x-else")
        ra, rb, ro = agent.sessions[a], agent.sessions[b], agent.sessions[other]
        await agent._keep_running(now)
        assert agent.sessions[mgr] is first and first.seat_due is None, "nothing to read, nothing due"
        # a question or a menu is a person's; another manager's member is not this one's
        ra.state, ra.confidence, ra.pending = "needs-you", "hook", {"kind": "question", "text": "which?"}
        ro.state = "stalled?"
        await agent._keep_running(now)
        assert agent.sessions[mgr] is first and first.seat_due is None
        # a permission fills it, the cause on the record that was due and on the fill's memory
        ra.pending = {"kind": "permission", "text": "Bash(rm)"}
        await agent._keep_running(now)
        second = agent.sessions[mgr]
        assert second is not first and first.seat_due["by"] == "pending" and first.seat_due["member"] == a
        assert second.seat_due is None and [(e["by"], e["member"]) for e in second.seat_filled] == [("pending", a)]
        assert second.seat == {"trigger": "team"} and [r["why"] for r in second.restarts] == ["fill"]
        # the manager left it as it was and exited: the same standing cause does not refill
        _end(agent, mgr)
        await agent._keep_running(now + timedelta(minutes=3))
        assert agent.sessions[mgr] is second and second.seat_due is None
        # a second member stalls: a new cause, a new fill, and both are remembered
        rb.state = "stalled?"
        await agent._keep_running(now + timedelta(minutes=4))
        third = agent.sessions[mgr]
        assert third is not second and second.seat_due["by"] == "stalled" and second.seat_due["member"] == b
        assert [(e["by"], e["member"]) for e in third.seat_filled] == [("pending", a), ("stalled", b)]
        # the permission was answered: its entry goes, and the same member asking again is a new cause
        _end(agent, mgr)
        ra.state, ra.pending = "working", None
        await agent._keep_running(now + timedelta(minutes=5))
        assert agent.sessions[mgr] is third and [e["by"] for e in third.seat_filled] == ["stalled"]
        ra.state, ra.pending = "needs-you", {"kind": "permission", "text": "Bash(rm)"}
        await agent._keep_running(now + timedelta(minutes=6))
        fourth = agent.sessions[mgr]
        assert fourth is not third and third.seat_due["by"] == "pending"
        # idle · open work is the fourth reading
        _end(agent, mgr)
        ra.state, ra.pending, rb.state = "idle", None, "idle"
        rb.idle_open = {"at": _iso(now), "ref": "TD-9"}
        await agent._keep_running(now + timedelta(minutes=7))
        fifth = agent.sessions[mgr]
        assert fifth is not fourth and fourth.seat_due["by"] == "open" and fourth.seat_due["member"] == b
        assert [(e["by"], e["member"]) for e in fifth.seat_filled] == [("open", b)]
        for sid in (mgr, a, b, other):
            await person.call("kill", id=sid)


async def test_a_held_manager_has_no_pane_and_its_first_reading_fills_it(agent, tmp_path):
    """TD-413 slice 1 (§6 rule 3 *A Start writes the seat and never fills it*, TD-410): the Start's
    `create` with `held` writes the manager on call closed with no pane and no `seat_held`, its launch
    record beside it; its members name its id; nothing due, the tick neither fills nor closes it; a
    member's permission fills it — a pane, from the launch record, never another held record."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        mgr = await _manager(person, tmp_path, held=True)
        first = agent.sessions[mgr]
        assert first.state == "closed" and first.closer == {"by": "start", "why": "held"}
        assert first.seat == {"trigger": "team"} and first.seat_held is None
        assert not await asyncio.to_thread(agent.tmux.has_session, mgr), "no pane"
        assert agent._read_launch(mgr).get("seat") == {"trigger": "team"} and "held" not in agent._read_launch(mgr)
        a = await _member(person, tmp_path, "a", mgr)
        ra = agent.sessions[a]
        assert ra.controllers == [mgr]
        await agent._keep_running(now)
        assert agent.sessions[mgr] is first and first.state == "closed" and first.seat_due is None
        ra.state, ra.confidence, ra.pending = "needs-you", "hook", {"kind": "permission", "text": "Bash(rm)"}
        await agent._keep_running(now)
        second = agent.sessions[mgr]
        assert second is not first and second.state not in ("exited", "closed")
        assert [r["why"] for r in second.restarts] == ["fill"] and first.seat_due["by"] == "pending"
        assert await asyncio.to_thread(agent.tmux.has_session, mgr)
        for sid in (mgr, a):
            await person.call("kill", id=sid)


async def test_a_start_that_keeps_the_mail_fills_the_held_manager_on_the_first_tick(agent, tmp_path):
    """TD-413 slice 1: a held create with `keep_mail` moves the last run's mail as a fill does — it
    did not before, so a held record began with an empty inbox — and a question in it is due at
    once: the first tick fills the seat for it."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        old = await _manager(person, tmp_path)
        a = await _member(person, tmp_path, "a", old)
        _end(agent, old)
        async with LocalClient(caller=a) as me:
            sent = await me.call("msg", to=[old], text="which entry next?", kind="ask")
        ask = sent["entry"]["id"]
        before = agent.sessions[old]
        mgr = await _manager(person, tmp_path, held=True, keep_mail=True)
        first = agent.sessions[mgr]
        assert first is not before and first.state == "closed"
        assert [e.id for e in first.inbox] == [ask] and before.inbox == []
        assert first.supersedes[0]["mail"] is True and first.asks_ids() == [ask]
        await agent._keep_running(now)
        second = agent.sessions[mgr]
        assert second is not first and first.seat_due["by"] == "asks" and first.seat_due["ask"] == ask
        assert [e.id for e in second.inbox] == [ask], "the fill keeps it as it came"
        for sid in (mgr, a):
            await person.call("kill", id=sid)


async def test_a_fill_prompt_ends_with_the_cause_it_came_for_and_no_other_replay_adds_one(agent, tmp_path):
    """TD-413 slice 2 (§6 rule 3 *A fill says why it came*, TD-410): the prompt a fill hands `create`
    ends with one line in fixed words from `seat_due`, for each of the four causes, and the `restarts`
    entry says `for: seat_due`; the line never stacks on the last fill's, and a crash restart's
    prompt carries none."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        mgr = await _manager(person, tmp_path)
        a = await _member(person, tmp_path, "a", mgr)
        ra = agent.sessions[a]

        async def fill_for(at: datetime) -> str:
            _end(agent, mgr)
            before = agent.sessions[mgr]
            await agent._keep_running(at)
            assert agent.sessions[mgr] is not before, "filled"
            return agent._read_launch(mgr)["prompt"]

        agent.sessions[mgr].asks_ids = lambda **kw: ["m-1"]
        assert await fill_for(now) == "the seat's brief\n\n[agentorc] you are filled for: asks — m-1"
        assert agent.sessions[mgr].restarts[-1]["for"] == "seat_due"
        ra.state, ra.confidence, ra.pending = "needs-you", "hook", {"kind": "permission", "text": "Bash(rm)"}
        assert await fill_for(now) == f"the seat's brief\n\n[agentorc] you are filled for: pending — {a}"
        ra.state, ra.pending = "stalled?", None
        assert (
            await fill_for(now + timedelta(minutes=1))
            == f"the seat's brief\n\n[agentorc] you are filled for: stalled — {a}"
        )
        ra.state, ra.idle_open = "idle", {"at": _iso(now), "ref": "TD-9"}
        assert (
            await fill_for(now + timedelta(minutes=2))
            == f"the seat's brief\n\n[agentorc] you are filled for: open — {a}"
        )
        # any other replay hands the brief alone, the last fill's line taken off
        _end(agent, mgr)
        await agent._replay(agent.sessions[mgr], "crash", keep_mail=True)
        assert agent._read_launch(mgr)["prompt"] == "the seat's brief"
        assert "for" not in agent.sessions[mgr].restarts[-1]
        for sid in (mgr, a):
            await person.call("kill", id=sid)


async def test_a_question_fills_once_and_a_cause_gone_before_the_fill_clears_the_due(agent, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        mgr = await _manager(person, tmp_path)
        first = agent.sessions[mgr]
        _end(agent, mgr)
        first.asks_ids = lambda **kw: ["m-1"]
        agent._profile_gated = lambda *a: True  # paused: due, never filled into a pause
        await agent._keep_running(now)
        assert agent.sessions[mgr] is first and first.seat_due["by"] == "asks" and first.seat_due["ask"] == "m-1"
        at = first.seat_due["at"]
        await agent._keep_running(now + timedelta(minutes=1))
        assert first.seat_due["at"] == at, "one due per cause, kept until the fill"
        first.asks_ids = lambda **kw: []
        await agent._keep_running(now)
        assert first.seat_due is None, "answered elsewhere"
        del agent._profile_gated
        first.asks_ids = lambda **kw: ["m-1"]
        await agent._keep_running(now)
        second = agent.sessions[mgr]
        assert second is not first and second.seat_filled[0]["ask"] == "m-1" and "member" not in second.seat_filled[0]
        # left unanswered: not filled for it again; a second question is a cause of its own
        _end(agent, mgr)
        second.asks_ids = lambda **kw: ["m-1"]
        await agent._keep_running(now)
        assert agent.sessions[mgr] is second and second.seat_due is None
        second.asks_ids = lambda **kw: ["m-1", "m-2"]
        await agent._keep_running(now)
        assert agent.sessions[mgr] is not second and second.seat_due["ask"] == "m-2"
        await person.call("kill", id=mgr)


async def test_a_manager_on_call_is_closed_idle_and_a_reading_that_came_while_it_ran_is_the_next_fill(agent, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        mgr = await _manager(person, tmp_path)
        a = await _member(person, tmp_path, "a", mgr)
        rec = agent.sessions[mgr]
        _idle(agent, mgr, now)
        rec.since = _iso(now)
        await agent._keep_running(now)
        assert rec.state == "idle", "not before SEAT_IDLE_GRACE: its prompt may not have landed"
        _idle(agent, mgr, now)
        await agent._keep_running(now)
        assert rec.state == "closed", "it found nothing and was closed"
        assert agent.sessions[a].controllers == [mgr], "its members name it across the close"
        # a member stalls: the closed seat is filled for it; then a second reading comes while that
        # run is up — it is closed once idle, and the fill that follows is for the reading
        agent.sessions[a].state = "stalled?"
        await agent._keep_running(now)
        run = agent.sessions[mgr]
        assert run is not rec and run.seat_filled[0]["by"] == "stalled"
        agent.sessions[a].state = "working"
        b = await _member(person, tmp_path, "b", mgr)
        agent.sessions[b].state = "stalled?"
        run.state = "working"
        await agent._keep_running(now)
        assert agent.sessions[mgr] is run and run.state == "working" and run.seat_due["member"] == b, "never mid-turn"
        _idle(agent, mgr, now)
        await agent._keep_running(now)
        assert run.state == "closed"
        await agent._keep_running(now)
        assert agent.sessions[mgr] is not run and agent.sessions[mgr].seat_filled[-1]["member"] == b
        for sid in (mgr, a, b):
            await person.call("kill", id=sid)


async def test_the_fill_ceilings_groups(agent, tmp_path):
    """A person-led team's seats, with no controller, are each a group of their own; in a managed
    team the techlead and the auditors share one and the manager seat is alone."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    spent = [{"at": _iso(now - timedelta(minutes=5 * (i + 1))), "why": "fill"} for i in range(FILL_CEILING)]
    async with LocalClient() as person:
        mgr = await _manager(person, tmp_path)
        tl = await _member(person, tmp_path, "tl", mgr, seat={"trigger": "every", "after": "6h"})
        aud = await _member(person, tmp_path, "aud", mgr, seat={"trigger": "every", "after": "6h"})
        p1 = await _member(person, tmp_path, "p1", None, seat={"trigger": "every", "after": "6h"})
        p2 = await _member(person, tmp_path, "p2", None, seat={"trigger": "every", "after": "6h"})
        w = await _member(person, tmp_path, "w", mgr)
        agent.sessions[w].state = "stalled?"
        recs = {sid: agent.sessions[sid] for sid in (mgr, tl, aud, p1, p2)}
        for sid in (tl, aud, p1, p2):
            agent.sessions[sid].seat_due = {"at": _iso(now), "by": "every"}
        for sid in recs:
            _end(agent, sid)
        recs[tl].restarts = list(spent)
        recs[p1].restarts = list(spent)
        await agent._keep_running(now)
        assert agent.sessions[mgr] is not recs[mgr], "the manager seat is a group of one"
        assert agent.sessions[tl] is recs[tl] and agent.sessions[aud] is recs[aud], "one group: both held"
        assert agent.sessions[p1] is recs[p1] and recs[p1].restart_ceiling
        assert agent.sessions[p2] is not recs[p2], "a person-led team's seat has six of its own"
        for sid in (mgr, tl, aud, p1, p2, w):
            await person.call("kill", id=sid)
