"""TD-103 slice (2), design §6 *Keeping a team running* rule 1: the crash restart and its ceiling. A
supervised, unattended member that exited on its own with nothing declared is restarted from its
launch record under its name; each restart is counted on the record and the count survives the
restart; at three inside two hours the tick stops and the session is a person's."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks

from sessionorc import paths
from sessionorc.agent import RESTART_CEILING, RESTART_SETTLE, RESTART_WINDOW
from sessionorc.client import AgentError, LocalClient

pytestmark = pytest.mark.integration


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


async def _member(person, tmp_path, name="w", **kw):
    return (
        await person.call(
            "create",
            name=name,
            dir=str(tmp_path),
            adapter="shell",
            argv=["bash", "--norc", "--noprofile"],
            unattended=True,
            supervised=True,
            prompt="the brief",
            **kw,
        )
    )["id"]


def _crash(agent, sid: str) -> None:
    """What the tick records for a tool that left on its own: `exited`, the pane kept (§4.2, TD-023)."""
    rec = agent.sessions[sid]
    rec.state, rec.pane, rec.exit_code = "exited", True, 1
    agent.store.save(rec)


async def test_a_member_that_exited_on_its_own_is_restarted_from_its_launch_record(agent, tmp_path):
    await park_ticks(agent)  # the test owns the clock: only the calls below act
    async with LocalClient() as person:
        sid = await _member(person, tmp_path, lane=["TD-1"], role="grinder", team="t")
        first = agent.sessions[sid]
        _crash(agent, sid)
        now = datetime.now(UTC)
        await agent._keep_running(now)
        new = agent.sessions[sid]
        assert new is not first and new.state != "exited", "superseded in place under its name (§4.1)"
        assert (new.name, new.lane, new.role, new.team, new.supervised, new.unattended) == (
            "w",
            ["TD-001"],
            "grinder",
            "t",
            True,
            True,
        )
        assert [r["why"] for r in new.restarts] == ["crash"] and "error" not in new.restarts[0]
        # an exit read at once is the replaced run ending, not the new one's crash (TD-186): held
        _crash(agent, sid)
        await agent._keep_running(now)
        assert [r["why"] for r in agent.sessions[sid].restarts] == ["crash"]
        # the count survives the restart it counts: a later crash appends to the same list
        await agent._keep_running(now + RESTART_SETTLE + timedelta(seconds=1))
        assert [r["why"] for r in agent.sessions[sid].restarts] == ["crash", "crash"]
        # a person's own Resume or fresh start of the name starts the count again
        await person.call("kill", id=sid)
        again = await _member(person, tmp_path)
        assert agent.sessions[again].restarts == []
        await person.call("kill", id=again)


async def test_what_rule_one_leaves_alone(agent, tmp_path):
    """A kill is never undone; a declaration, a wrap-up, a passed stop time, a pause, a suspension
    and a seat are endings of their own; an attended or unsupervised session is a person's."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    cases = {
        "killed": {"pane": False},
        "declared": {"out_of_work": {"at": _iso(now), "why": "nothing open"}},
        "wanted": {"restart_wanted": {"at": _iso(now), "why": "context"}},
        "wrapped": {"wrapup_sent_at": _iso(now)},
        "asked": {"wrapup_at": _iso(now)},
        "stopped": {"run_until": _iso(now - timedelta(minutes=1))},
        "gated": {"gated": {"profile": "", "label": "5h", "pct": 80, "line": 70}},
        "suspended": {"suspended": {"at": _iso(now), "why": "alarm"}},
        "seat": {"seat": {"trigger": "asks"}},
        "attended": {"unattended": False},
        "unsupervised": {"supervised": False},
    }
    async with LocalClient() as person:
        ids = {}
        for n, fields in cases.items():
            sid = await _member(person, tmp_path, name=n)
            _crash(agent, sid)
            rec = agent.sessions[sid]
            for k, v in fields.items():
                setattr(rec, k, v)
            ids[n] = (sid, rec)
        await agent._keep_running(now)
        for n, (sid, was) in ids.items():
            rec = agent.sessions[sid]
            assert rec is was and rec.state == "exited" and rec.restarts == [], n
            await person.call("kill", id=sid)


async def test_the_ceiling_stops_the_tick_and_a_failed_replay_counts(agent, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(person, tmp_path)
        # three restarts inside the window: the fourth exit is the person's
        _crash(agent, sid)
        rec = agent.sessions[sid]
        rec.restarts = [{"at": _iso(now - timedelta(minutes=m)), "why": "crash"} for m in (90, 40, 5)]
        created = rec
        await agent._keep_running(now)
        rec = agent.sessions[sid]
        assert rec is created and rec.state == "exited", "not restarted at the ceiling"
        assert rec.restart_ceiling and rec.restart_ceiling["count"] == RESTART_CEILING
        # the window rolls on, and the ceiling still stands: only a person lifts it (§6)
        await agent._keep_running(now + RESTART_WINDOW + timedelta(minutes=1))
        assert agent.sessions[sid] is created
        # the same three, older than the window: restarted
        rec.restart_ceiling = None
        rec.restarts = [{"at": _iso(now - RESTART_WINDOW - timedelta(minutes=m)), "why": "crash"} for m in (3, 2, 1)]
        await agent._keep_running(now)
        assert agent.sessions[sid] is not created and len(agent.sessions[sid].restarts) == 4
        # a replay that fails is a restart that failed: it keeps its entry, with the error, and counts
        _crash(agent, sid)
        created = agent.sessions[sid]
        created.restarts = []
        (paths.launch_dir() / f"{sid}.json").unlink()
        for _ in range(RESTART_CEILING):
            await agent._keep_running(now)
        rec = agent.sessions[sid]
        assert rec is created
        failed = [r for r in rec.restarts if r.get("error")]
        assert len(failed) == RESTART_CEILING and "no launch record" in failed[0]["error"]
        assert all(r["why"] == "crash" for r in failed)
        await agent._keep_running(now)
        assert agent.sessions[sid].restart_ceiling, "an unrepairable record reaches the ceiling"
        await person.call("kill", id=sid)


async def test_a_node_restarts_nothing_and_a_seat_is_refused_malformed(agent, tmp_path):
    """The restarts run at the home (§4.4a: policies that start run at the home)."""
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = await _member(person, tmp_path)
        _crash(agent, sid)
        created = agent.sessions[sid]
        agent.mode = "node"
        try:
            await agent._keep_running(datetime.now(UTC))
        finally:
            agent.mode = "home"
        assert agent.sessions[sid] is created
        await person.call("kill", id=sid)
        with pytest.raises(AgentError, match="seat is"):
            await _member(person, tmp_path, name="bad", seat={"after": "10"})
        ok = await _member(person, tmp_path, name="auditor", seat={"trigger": "prs", "after": "10"})
        assert agent.sessions[ok].seat == {"trigger": "prs", "after": "10"}
        await person.call("kill", id=ok)


async def test_a_crash_on_a_paused_profile_waits_for_the_gate(agent, tmp_path):
    """The gate clears `gated` on an exited record, so rule 1 reads the profile's usage itself: a
    session is not restarted into a pause (§6 *Usage gate*), and is once the profile is under."""
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = await _member(person, tmp_path)
        _crash(agent, sid)
        created = agent.sessions[sid]
        agent._profile_gated = lambda *a: True
        await agent._keep_running(datetime.now(UTC))
        assert agent.sessions[sid] is created
        del agent._profile_gated
        await agent._keep_running(datetime.now(UTC))
        assert agent.sessions[sid] is not created
        await person.call("kill", id=sid)


async def test_a_teams_reserve_priority_holds_its_crashed_member_where_a_plain_one_restarts(agent, tmp_path):
    """TD-146: the line rule 1 reads is the one the gate would pause at — grind at 30, ao-grind's
    priority 10, the profile at 65% — so a teamed member is not restarted into a pause on the next
    tick, while a plain member on the same profile is."""
    await park_ticks(agent)
    agent._usage[""] = {"windows": [{"label": "5h", "pct": 65, "resets": None}], "reason": "ok"}
    async with LocalClient() as person:
        await person.call("set_settings", profile="", reserves={"5h": 30}, teams={"ao-grind": {"reserve": 10}})
        plain = await _member(person, tmp_path, name="p")
        teamed = await _member(person, tmp_path, name="t", team="ao-grind")
        _crash(agent, plain)
        _crash(agent, teamed)
        before = {sid: agent.sessions[sid] for sid in (plain, teamed)}
        await agent._keep_running(datetime.now(UTC))
        assert agent.sessions[plain] is not before[plain], "65% is under the profile's 70%: restarted"
        assert agent.sessions[teamed] is before[teamed], "65% is over ao-grind's 60%: held"
        await person.call("kill", id=plain)


async def test_a_replays_entry_carries_what_the_run_it_replaced_reported(agent, tmp_path):
    """TD-249 slice 1, design §4.9a *early from the record*, §6 rule 1: `done: [{ref, pr}]` since the
    record's `created` and `left: [ref]`, its declared claims not closed — on a failed replay too,
    and on the new record, which keeps none of the old one's `progress`."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(person, tmp_path)
        async with LocalClient(caller=sid) as w:
            await w.call("progress", id=sid, ref="TD-1", status="done", pr=812)
            await w.call("progress", id=sid, ref="TD-2", status="done")
            await w.call("progress", id=sid, ref="TD-3")
            await w.call("progress", id=sid, ref="TD-4")
            await w.call("progress", id=sid, ref="TD-4", status="dropped", why="too large")
            await w.call("progress", id=sid, ref="TD-5", source="derived")
        old = agent.sessions[sid]
        # one reported before this run began is an earlier run's, and is not this one's `done`
        old.progress.insert(0, type(old.progress[0])(ref="TD-0", status="done", pr=700, at="2026-09-01T00:00:00Z"))
        _crash(agent, sid)
        await agent._keep_running(now)
        new = agent.sessions[sid]
        assert new is not old and new.progress == []
        (entry,) = new.restarts
        assert entry["why"] == "crash" and "error" not in entry
        assert entry["done"] == [{"ref": "TD-001", "pr": 812}, {"ref": "TD-002", "pr": None}]
        assert entry["left"] == ["TD-003"], "a dropped claim is closed, and a derived one is not the run's word"

        # a run that reported nothing carries the two lists empty, and a failed replay carries them too
        async with LocalClient(caller=sid) as w:
            await w.call("progress", id=sid, ref="TD-6")
        _crash(agent, sid)
        (paths.launch_dir() / f"{sid}.json").unlink()
        await agent._keep_running(now + RESTART_SETTLE + timedelta(minutes=1))
        rec = agent.sessions[sid]
        assert rec is new and rec.restarts[0] == entry
        assert rec.restarts[1]["error"] and (rec.restarts[1]["done"], rec.restarts[1]["left"]) == ([], ["TD-006"])
        await person.call("kill", id=sid)
