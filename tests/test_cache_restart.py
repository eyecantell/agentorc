"""§4.10 *A lapsed cache is started again, not rung* (TD-459, built by TD-467): the doorbell hands a
member idle past `CACHE_LIFETIME` with a context over `CACHE_FLOOR` to rule 7's tick restart, `why:
cache`, when rule 7's precondition holds, and rings it as before when it does not."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks, wait_for

from sessionorc import agent_common, mail
from sessionorc.agent import RESTART_SETTLE
from sessionorc.client import LocalClient
from sessionorc.models import ProgressEntry, Session

LINE_1 = "SUBMITTED " + mail.unread_line(1)


def _ago(d: timedelta) -> str:
    return (datetime.now(UTC) - d).isoformat().replace("+00:00", "Z")


def _lapsed(rec, *, idle: timedelta = timedelta(hours=2), tokens: int = 150_000) -> None:
    """A clean member idle past the hour with a long context: what the doorbell restarts."""
    rec.state, rec.confidence, rec.pending = "idle", "hook", None
    rec.since = _ago(idle)
    rec.context = {"tokens": tokens, "at": _ago(timedelta(minutes=1))}
    rec.git = {"dirty": 0, "unpushed": 0}


async def _member(agent, c, tmp_path, name: str) -> str:
    (tmp_path / name).mkdir()
    sid = (
        await c.call(
            "create", name=name, dir=str(tmp_path / name), adapter="composer0", unattended=True, supervised=True
        )
    )["id"]
    painted = await wait_for(lambda: _painted(agent, sid), timeout=5)
    assert painted, "the composer child never painted its prompt"
    return sid


async def _painted(agent, sid: str) -> bool:
    return any(t.startswith(">>") for t in await agent.rpc_tail(sid, 3))


async def _submitted(agent, sid: str) -> list[str]:
    return [t.rstrip() for t in await agent.rpc_tail(sid, 30) if t.startswith("SUBMITTED ")]


async def _lead(c, tmp_path, worker: str) -> str:
    lead = (await c.call("create", name="lead", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
    await c.call("set_controllers", id=worker, add=[lead])
    return lead


@pytest.mark.integration
async def test_a_lapsed_cache_is_restarted_on_its_brief_and_nothing_is_typed(agent, composerstubs, tmp_path):
    """Idle two hours with 150k tokens and clean: replayed `why: cache` with its hours and tokens, the
    wake charged once and carried to the new record, its mail kept unread for the new run, no line typed."""
    await park_ticks(agent)
    async with LocalClient() as c:
        w = await _member(agent, c, tmp_path, "w")
        lead = await _lead(c, tmp_path, w)
        _lapsed(agent.sessions[w])
        async with LocalClient(caller=lead) as ld:
            await ld.call("msg", to=w, text="new work")
        old = agent.sessions[w]
        await agent._ring_once(w, retry=False)
        new = agent.sessions[w]
        assert new is not old
        assert [(r["why"], r["idle"], r["context"]) for r in new.restarts] == [("cache", 2.0, 150_000)]
        assert "error" not in new.restarts[-1]
        assert [(x["cause"], x["charged"], x["via"]) for x in new.wakes] == [("mail", True, "doorbell")]
        assert new.unread() == 1  # the mail was kept with the name: the new run reads it first
        assert await wait_for(lambda: _painted(agent, w), timeout=5)
        assert await _submitted(agent, w) == []
        # the new record is not idle past the hour: a later ring is a ring
        assert agent_common.cache_lapsed(new, datetime.now(UTC)) is None
        await c.call("kill", id=w)


@pytest.mark.integration
@pytest.mark.parametrize(
    "case",
    ["claim", "dirty", "unknown", "short_idle", "short_context", "seat", "declared", "node", "ceiling"],
)
async def test_a_member_that_fails_the_precondition_is_rung(agent, composerstubs, tmp_path, case):
    """With a claim in progress, a dirty or unknown tree, idle only 30 minutes, 60k tokens, a seat, a
    declaration, a node's member or a full restart window, the doorbell rings as it always has."""
    await park_ticks(agent)
    async with LocalClient() as c:
        w = await _member(agent, c, tmp_path, "w")
        lead = await _lead(c, tmp_path, w)
        rec = agent.sessions[w]
        _lapsed(
            rec,
            idle=timedelta(minutes=30) if case == "short_idle" else timedelta(hours=2),
            tokens=60_000 if case == "short_context" else 150_000,
        )
        five = _ago(timedelta(minutes=5))
        fields = {
            "claim": {"progress": [ProgressEntry(ref="TD-1", status="claimed", source="declared")]},
            "dirty": {"git": {"dirty": 2, "unpushed": 0}},
            "unknown": {"git": {}},
            "seat": {"seat": {"trigger": "asks"}},
            "declared": {"out_of_work": {"at": five, "why": "x"}},
            "node": {"host": "elsewhere"},
            "ceiling": {"restarts": [{"at": five, "why": "crash"}] * 3},
        }.get(case, {})
        async with LocalClient(caller=lead) as ld:
            await ld.call("msg", to=w, text="new work")
        for k, v in fields.items():  # after the send: a node's member is the home's to address, not here
            setattr(rec, k, v)
        await agent._ring_once(w, retry=False)
        rec = agent.sessions[w]
        assert not [r for r in rec.restarts if r.get("why") == "cache"], case
        assert await _submitted(agent, w) == [LINE_1], case
        rec.host = agent.host
        await c.call("kill", id=w)


@pytest.mark.integration
async def test_a_cache_restart_whose_replay_failed_is_retried_by_the_tick(agent, composerstubs, tmp_path, monkeypatch):
    """A replay that fails leaves the record closed under the tick's `cache` mark with the entry's
    `error`; the tick replays it once `RESTART_SETTLE` has passed, keeping the hours and tokens, and the
    doorbell does not decide the ring again."""
    await park_ticks(agent)
    async with LocalClient() as c:
        w = await _member(agent, c, tmp_path, "w")
        lead = await _lead(c, tmp_path, w)
        _lapsed(agent.sessions[w])
        async with LocalClient(caller=lead) as ld:
            await ld.call("msg", to=w, text="new work")
        real = agent._read_launch

        def broken(address):
            raise OSError("launch record unreadable")

        monkeypatch.setattr(agent, "_read_launch", broken)
        await agent._ring_once(w, retry=False)
        rec = agent.sessions[w]
        assert rec.state == "closed" and (rec.closed_for or {}).get("why") == "cache"
        assert [(r["why"], "error" in r) for r in rec.restarts] == [("cache", True)]
        monkeypatch.setattr(agent, "_read_launch", real)
        await agent._keep_running(datetime.now(UTC) + RESTART_SETTLE + timedelta(seconds=1))
        new = agent.sessions[w]
        assert [(r["why"], r.get("idle"), r.get("context"), "error" in r) for r in new.restarts] == [
            ("cache", 2.0, 150_000, True),
            ("cache", 2.0, 150_000, False),
        ]
        assert len(new.wakes) == 1  # one decision for the stretch, the retry took none
        with contextlib.suppress(Exception):
            await c.call("kill", id=w)


@pytest.mark.unit
def test_read_when_says_a_lapsed_cache_is_started_again():
    """`read_when`'s sentence for a hook-confirmed `idle` says which it will be (§4.10's table)."""
    now = datetime.now(UTC)
    s = Session(id="w", name="w", kind="agent", dir="/tmp", adapter="claude_code", unattended=True, supervised=True)
    _lapsed(s)
    assert agent_common.cache_restarts(s, now)
    assert mail.read_when(s, "note", now, cache=True) == "started again on its brief within a tick, and reads it first"
    assert mail.read_when(s, "note", now).startswith("rung within a tick")
    s.git = {"dirty": 1, "unpushed": 0}
    assert not agent_common.cache_restarts(s, now)
    s.git = {"dirty": 0, "unpushed": 0}
    s.context = {"tokens": agent_common.CACHE_FLOOR}
    assert agent_common.cache_lapsed(s, now) is None  # over the floor, not at it
    s.context, s.since = {"tokens": 150_000}, _ago(timedelta(minutes=59))
    assert agent_common.cache_lapsed(s, now) is None
