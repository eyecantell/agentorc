"""TD-103 slice (4), design §6 *Keeping a team running* rules 2 and 4. The wanted restart: a member
that declared `restart_wanted` is closed and restarted when its work is pushed, sent one line naming
what is left when it is not, and the Inbox's after `IDLE_NUDGE`. The idle nudge: a member idle for
`IDLE_NUDGE` with open work is sent one fixed line naming it, once per idle stretch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks, wait_for

from sessionorc.agent import IDLE_NUDGE, RESTART_CEILING
from sessionorc.client import LocalClient
from sessionorc.models import MailEntry, ProgressEntry

pytestmark = pytest.mark.integration

CLEAN = {"branch": "w", "dirty": 0, "unpushed": 0, "upstream": "origin/w"}


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


async def _member(agent, person, tmp_path, name="w", **kw) -> str:
    (tmp_path / name).mkdir()
    params = {
        "name": name,
        "dir": str(tmp_path / name),
        "adapter": "composer0",
        "unattended": True,
        "supervised": True,
        "prompt": "the brief",
    }
    sid = (await person.call("create", **{**params, **kw}))["id"]
    assert await wait_for(lambda: _painted(agent, sid), timeout=5), "the composer child never painted its prompt"
    return sid


async def _painted(agent, sid: str) -> bool:
    return any(t.startswith(">>") for t in await agent.rpc_tail(sid, 3))


async def _shows(agent, sid: str, text: str) -> bool:
    return any(text in t.replace(" ", "") for t in await agent.rpc_tail(sid, 3))


async def _submitted(agent, sid: str) -> list[str]:
    return [t.rstrip() for t in await agent.rpc_tail(sid, 30) if t.startswith("SUBMITTED ")]


async def _idle_for(agent, sid: str, now: datetime, long: timedelta) -> None:
    """A hook-confirmed idle that began `long` before `now`."""
    await agent.rpc_hook(sid, state="idle")
    agent.sessions[sid].since = _iso(now - long)


async def test_an_idle_member_with_open_work_is_nudged_once_per_stretch(agent, composerstubs, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path, lane=["TD-1", "TD-2"])
        rec = agent.sessions[sid]
        await _idle_for(agent, sid, now, IDLE_NUDGE - timedelta(minutes=1))
        await agent._keep_running(now)
        assert await _submitted(agent, sid) == [] and rec.nudged_at is None, "not yet twenty minutes"
        await _idle_for(agent, sid, now, IDLE_NUDGE + timedelta(minutes=1))
        await agent._keep_running(now)
        lines = await _submitted(agent, sid)
        assert len(lines) == 1 and "`TD-001` open" in lines[0] and "ao progress restart --why" in lines[0]
        assert rec.nudged_at and rec.sends[-1].from_ == "system"
        await agent._keep_running(now + timedelta(minutes=30))
        assert len(await _submitted(agent, sid)) == 1, "never a second in the same stretch"
        # it answered by working and went idle again: a new stretch, and the next open reference
        await agent.rpc_hook(sid, state="working")
        rec.progress = [ProgressEntry(ref="TD-001", status="done", pr=5)]
        await _idle_for(agent, sid, now + timedelta(hours=1), IDLE_NUDGE + timedelta(minutes=1))
        await agent._keep_running(now + timedelta(hours=1))
        lines = await _submitted(agent, sid)
        assert len(lines) == 2 and "`TD-002` open" in lines[1]
        await person.call("kill", id=sid)


async def test_what_the_nudge_leaves_alone(agent, composerstubs, tmp_path):
    """Nothing open, a declaration, an unsupervised or attended member, and a pane with words in
    its composer are not nudged."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        cases = {
            "done": {"progress": [ProgressEntry(ref="TD-001", status="done", pr=1)]},
            "declared": {"out_of_work": {"at": _iso(now), "why": "nothing open"}},
            "unsupervised": {"supervised": False},
            "attended": {"unattended": False},
        }
        ids = {}
        for n, fields in cases.items():
            sid = await _member(agent, person, tmp_path, name=n, lane=["TD-1"])
            await _idle_for(agent, sid, now, IDLE_NUDGE + timedelta(minutes=1))
            for k, v in fields.items():
                setattr(agent.sessions[sid], k, v)
            ids[n] = sid
        typed = await _member(agent, person, tmp_path, name="typed", lane=["TD-1"])
        await agent.rpc_keys(typed, ["h", "i"])  # someone's half-typed words in the composer
        assert await wait_for(lambda: _shows(agent, typed, ">>hi"), timeout=5)
        await _idle_for(agent, typed, now, IDLE_NUDGE + timedelta(minutes=1))
        await agent._keep_running(now)
        for n, sid in ids.items():
            assert await _submitted(agent, sid) == [] and agent.sessions[sid].nudged_at is None, n
            await person.call("kill", id=sid)
        assert await _submitted(agent, typed) == [] and agent.sessions[typed].nudged_at is None, "words typed"
        await person.call("kill", id=typed)


def _answered(mid: str, at: str) -> MailEntry:
    """The member's own copy of a question the person answered: it owes an outcome (§4.10)."""
    return MailEntry(id=mid, from_="w", to=["person"], at=at, kind="ask", text="which?", closed_reason="replied")


async def test_an_owed_outcome_is_open_work_and_named_apart(agent, composerstubs, tmp_path):
    """Rule 4's owed clause (TD-258 slice 4): a member with nothing in its lane and an outcome owed
    is nudged about the outcome alone; one with both reads both, the debt after its reference; four
    owed are three named and one counted; a finished member is never sent to."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    long = IDLE_NUDGE + timedelta(minutes=1)
    async with LocalClient() as person:
        alone = await _member(agent, person, tmp_path, name="alone")
        rec = agent.sessions[alone]
        rec.outbox = [_answered("m-1", _iso(now))]
        assert rec.owed() == ["m-1"]
        await _idle_for(agent, alone, now, long)
        await agent._keep_running(now)
        lines = await _submitted(agent, alone)
        assert len(lines) == 1 and "open" not in lines[0]
        assert "idle 20 minutes: you owe 1 outcome on m-1 — `ao msg person --outcome" in lines[0]
        assert lines[0].endswith("--for m-1`") and rec.nudged_at
        await agent._keep_running(now + timedelta(minutes=30))
        assert len(await _submitted(agent, alone)) == 1, "once per stretch"

        both = await _member(agent, person, tmp_path, name="both", lane=["TD-1"])
        agent.sessions[both].outbox = [_answered(f"m-{i}", _iso(now)) for i in range(4)]
        await _idle_for(agent, both, now, long)
        await agent._keep_running(now)
        lines = await _submitted(agent, both)
        assert len(lines) == 1 and "`TD-001` open" in lines[0]
        assert lines[0].endswith("; you owe 4 outcomes on m-0, m-1, m-2 and 1 more — "
                                 '`ao msg person --outcome done|blocked|dropped "…" --for <id>`')  # fmt: skip

        settled = await _member(agent, person, tmp_path, name="settled")
        e = _answered("m-9", _iso(now))
        e.outcome = {"status": "done", "text": "PR #1", "at": _iso(now)}
        agent.sessions[settled].outbox = [e]
        finished = await _member(agent, person, tmp_path, name="finished")
        agent.sessions[finished].outbox = [_answered("m-8", _iso(now))]
        agent.sessions[finished].out_of_work = {"at": _iso(now), "why": "declared before the answer"}
        for sid in (settled, finished):
            await _idle_for(agent, sid, now, long)
        await agent._keep_running(now)
        for sid in (settled, finished):
            assert await _submitted(agent, sid) == [] and agent.sessions[sid].nudged_at is None
        for sid in (alone, both, settled, finished):
            await person.call("kill", id=sid)


async def test_a_seat_owing_an_outcome_is_nudged_with_no_question_waiting(agent, composerstubs, tmp_path):
    """Seat or not: a seat's own question the person answered is named, with nothing in its inbox."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path, name="tl")
        rec = agent.sessions[sid]
        rec.seat = {"role": "techlead"}
        rec.outbox = [_answered("m-5", _iso(now))]
        await _idle_for(agent, sid, now, IDLE_NUDGE + timedelta(minutes=1))
        assert agent._nudge_line(rec) == (
            '[agentorc] you owe 1 outcome on m-5 — `ao msg person --outcome done|blocked|dropped "…" --for m-5`'
        )
        rec.outbox = []
        assert agent._nudge_line(rec) is None
        await person.call("kill", id=sid)


async def test_a_wanted_restart_with_its_work_pushed_is_closed_and_restarted(agent, composerstubs, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path, lane=["TD-1"])
        first = agent.sessions[sid]
        await _idle_for(agent, sid, now, timedelta(minutes=1))
        first.restart_wanted = {"at": _iso(now), "why": "context is long"}
        first.git = None
        await agent._keep_running(now)
        assert agent.sessions[sid] is first, "an unknown git state is left alone"
        first.restart_wanted = {**first.restart_wanted, "early": True}
        first.git = dict(CLEAN)
        await agent._keep_running(now)
        assert agent.sessions[sid] is first, "an early one is the person's (§4.9a)"
        first.restart_wanted = {"at": _iso(now), "why": "context is long"}
        await agent._keep_running(now)
        new = agent.sessions[sid]
        assert new is not first and first.state == "closed"
        assert [r["why"] for r in new.restarts] == ["wanted"] and new.restart_wanted is None
        assert new.restarts[0]["said"] == "context is long", "the member's own why, for rule 9's note (TD-468)"
        assert new.lane == ["TD-001"] and new.supervised
        await person.call("kill", id=sid)


async def test_a_wanted_restart_with_work_left_is_held_then_runs_once_pushed(agent, composerstubs, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path)
        rec = agent.sessions[sid]
        await _idle_for(agent, sid, now, timedelta(minutes=1))
        rec.restart_wanted = {"at": _iso(now), "why": "context is long"}
        rec.git = {**CLEAN, "dirty": 2, "unpushed": 1}
        await agent._keep_running(now)
        lines = await _submitted(agent, sid)
        assert len(lines) == 1 and "2 uncommitted and 1 unpushed" in lines[0]
        assert agent.sessions[sid] is rec and rec.restart_blocked_sent_at and rec.restart_blocked is None
        await agent._keep_running(now + timedelta(minutes=5))
        assert len(await _submitted(agent, sid)) == 1, "one send, once"
        await agent._keep_running(now + IDLE_NUDGE + timedelta(minutes=1))
        assert rec.restart_blocked == {"at": rec.restart_blocked["at"], "dirty": 2, "unpushed": 1}
        rec.git = dict(CLEAN)  # someone pushed
        await agent._keep_running(now + IDLE_NUDGE + timedelta(minutes=2))
        new = agent.sessions[sid]
        assert new is not rec and new.restart_blocked is None and [r["why"] for r in new.restarts] == ["wanted"]
        await person.call("kill", id=sid)


async def test_a_wanted_restart_counts_toward_the_ceiling_and_a_failed_close_is_retried(
    agent, composerstubs, tmp_path, monkeypatch
):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path)
        rec = agent.sessions[sid]
        await _idle_for(agent, sid, now, timedelta(minutes=1))
        rec.restart_wanted = {"at": _iso(now), "why": "context is long"}
        rec.git = dict(CLEAN)
        rec.restarts = [{"at": _iso(now - timedelta(minutes=m)), "why": "crash"} for m in (50, 30, 10)]
        await agent._keep_running(now)
        assert agent.sessions[sid] is rec and rec.restart_ceiling["count"] == RESTART_CEILING
        # below the ceiling, a close whose tail fails after marking it closed is retried next tick
        rec.restart_ceiling, rec.restarts = None, []
        real_close = agent.rpc_close

        async def close_then_fail(id, closer=None):
            await real_close(id, closer=closer)
            raise RuntimeError("push failed")

        monkeypatch.setattr(agent, "rpc_close", close_then_fail)
        await agent._keep_running(now)
        assert agent.sessions[sid] is rec and rec.state == "closed"
        assert rec.restarts[-1]["why"] == "wanted" and "push failed" in rec.restarts[-1]["error"]
        monkeypatch.setattr(agent, "rpc_close", real_close)
        await agent._keep_running(now)
        new = agent.sessions[sid]
        assert new is not rec and [r["why"] for r in new.restarts] == ["wanted", "wanted"]
        await person.call("kill", id=sid)


async def test_a_wanted_restart_and_its_next_ticks_leave_one_entry(agent, composerstubs, tmp_path):
    """TD-186: the run a wanted restart closed may end after the new one is created under the same
    id. Its `SessionEnd`, carrying the old run's tool id, is not the new run's exit and is ignored;
    and a crash read inside `RESTART_SETTLE` of a restart is not judged. So the ticks that follow
    leave the one `wanted` entry, where they once wrote two failed crash replays and a ceiling."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path)
        first = agent.sessions[sid]
        first.adapter_id = "old-run"
        await _idle_for(agent, sid, now, timedelta(minutes=1))
        first.restart_wanted = {"at": _iso(now), "why": "context is long"}
        first.git = dict(CLEAN)
        await agent._keep_running(now)
        new = agent.sessions[sid]
        assert new is not first and [r["why"] for r in new.restarts] == ["wanted"]
        new.adapter_id = "new-run"
        await agent.rpc_hook(sid, state="exited", adapter_id="old-run")  # the old run's late SessionEnd
        assert new.state != "exited" and new.adapter_id == "new-run"
        # even an exit read at once (a tool with no id to tell the runs apart) waits out the settle
        new.state, new.pane = "exited", True
        for tick in (now, now + timedelta(seconds=2), now + timedelta(seconds=4)):
            await agent._keep_running(tick)
        assert agent.sessions[sid] is new and [r["why"] for r in new.restarts] == ["wanted"]
        assert new.restart_ceiling is None
        # the run's own exit still lands: same tool id
        new.state, new.pane = "idle", True
        await agent.rpc_hook(sid, state="exited", adapter_id="new-run")
        assert new.state == "exited"
        await person.call("kill", id=sid)


async def test_a_ceiling_whose_window_emptied_lets_a_clean_wanted_restart_run(agent, composerstubs, tmp_path):
    """TD-186: the ceiling guards against a crash loop, not a member that has worked for hours
    since. Inside the window it holds a clean `restart_wanted`; once the window holds fewer than
    `RESTART_CEILING` restarts, the declaration is acted on and the new record carries no mark."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path)
        rec = agent.sessions[sid]
        stamps = [now - timedelta(minutes=m) for m in (30, 20, 10)]
        rec.restarts = [{"at": _iso(t), "why": "crash"} for t in stamps]
        rec.restart_ceiling = {"at": _iso(stamps[-1]), "count": RESTART_CEILING}
        await _idle_for(agent, sid, now, timedelta(minutes=1))
        rec.restart_wanted = {"at": _iso(now), "why": "context is long"}
        rec.git = dict(CLEAN)
        await agent._keep_running(now)
        assert agent.sessions[sid] is rec, "three restarts inside two hours: the ceiling holds"
        later = now + timedelta(hours=2)
        await _idle_for(agent, sid, later, timedelta(minutes=1))
        await agent._keep_running(later)
        new = agent.sessions[sid]
        assert new is not rec and new.restart_ceiling is None
        assert [r["why"] for r in new.restarts][-1] == "wanted"
        await person.call("kill", id=sid)


async def test_a_person_s_close_after_a_wanted_restart_is_never_undone(agent, composerstubs, tmp_path):
    """TD-235: a successful wanted restart leaves its `wanted` entry, with no `error`, on the new
    record. That run declares again and a person closes it: the `closed` record is the person's,
    and the tick never starts it again (design §6 rule 2)."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path)
        first = agent.sessions[sid]
        await _idle_for(agent, sid, now, timedelta(minutes=1))
        first.restart_wanted = {"at": _iso(now), "why": "context is long"}
        first.git = dict(CLEAN)
        await agent._keep_running(now)
        new = agent.sessions[sid]
        assert new is not first and [r["why"] for r in new.restarts] == ["wanted"]
        assert "error" not in new.restarts[-1]
        later = now + timedelta(hours=1)
        await _idle_for(agent, sid, later, timedelta(minutes=1))
        new.restart_wanted = {"at": _iso(later), "why": "context is long again"}
        new.git = dict(CLEAN)
        await person.call("close", id=sid)
        assert new.state == "closed"
        for tick in (later, later + timedelta(minutes=5)):
            await agent._keep_running(tick)
        assert agent.sessions[sid] is new and new.state == "closed"
        assert [r["why"] for r in new.restarts] == ["wanted"], "a person's Close is never undone"


async def test_a_person_s_close_after_a_failed_wanted_restart_is_never_undone(agent, composerstubs, tmp_path):
    """TD-236: a member whose wanted restart already failed without being closed (a replay that
    failed on an exited run, a close that failed before it marked the record) carries a `wanted`
    entry with `error`. A person's Close after that entry is the person's: the tick's own close is
    stamped before its failure, a person's after it."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path)
        rec = agent.sessions[sid]
        await _idle_for(agent, sid, now, timedelta(minutes=1))
        rec.restart_wanted = {"at": _iso(now), "why": "context is long"}
        rec.git = dict(CLEAN)
        rec.restarts = [{"at": _iso(now - timedelta(minutes=5)), "why": "wanted", "error": "create failed"}]
        await person.call("close", id=sid)
        assert rec.state == "closed" and rec.closed_at > rec.restarts[-1]["at"]
        for tick in (now, now + timedelta(minutes=5)):
            await agent._keep_running(tick)
        assert agent.sessions[sid] is rec and rec.state == "closed" and len(rec.restarts) == 1


async def test_the_ticks_own_failed_close_is_marked_and_a_person_s_close_in_the_same_second_clears_it(
    agent, composerstubs, tmp_path, monkeypatch
):
    """TD-237: the tick writes `closed_for` after its own close, so its failed restart is retried; a
    person's Close clears it, even in the same second as the failed entry, and is never undone."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path)
        rec = agent.sessions[sid]
        await _idle_for(agent, sid, now, timedelta(minutes=1))
        rec.restart_wanted = {"at": _iso(now), "why": "context is long"}
        rec.git = dict(CLEAN)
        real_close = agent.rpc_close

        async def close_then_fail(id, closer=None):
            await real_close(id, closer=closer)
            raise RuntimeError("push failed")

        monkeypatch.setattr(agent, "rpc_close", close_then_fail)
        await agent._keep_running(now)
        monkeypatch.setattr(agent, "rpc_close", real_close)
        assert (
            rec.state == "closed"
            and rec.closed_for == {"why": "wanted", "closed_at": rec.closed_at}
            and rec.restarts[-1]["error"]
        )
        await person.call("close", id=sid)  # the person's Close, within the second
        assert rec.closed_for is None
        for tick in (now, now + timedelta(minutes=5)):
            await agent._keep_running(tick)
        assert agent.sessions[sid] is rec and rec.state == "closed" and len(rec.restarts) == 1


async def test_a_close_that_fails_before_marking_the_record_leaves_no_mark(agent, composerstubs, tmp_path, monkeypatch):
    """TD-237, from the review of #752: a close that raised before it marked the record closed leaves it
    idle with its `error` entry and no `closed_for`, so a later close some other way is never read as
    the tick's own; the next tick retries it from `idle`."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path)
        rec = agent.sessions[sid]
        await _idle_for(agent, sid, now, timedelta(minutes=1))
        rec.restart_wanted = {"at": _iso(now), "why": "context is long"}
        rec.git = dict(CLEAN)
        real_close = agent.rpc_close

        async def fail_at_once(id, closer=None):
            raise RuntimeError("kill failed")

        monkeypatch.setattr(agent, "rpc_close", fail_at_once)
        await agent._keep_running(now)
        assert rec.state == "idle" and rec.closed_for is None and "kill failed" in rec.restarts[-1]["error"]
        monkeypatch.setattr(agent, "rpc_close", real_close)
        await agent._keep_running(now)
        new = agent.sessions[sid]
        assert new is not rec and [r["why"] for r in new.restarts] == ["wanted", "wanted"]
        await person.call("kill", id=sid)


async def test_a_restart_twenty_minutes_in_is_replayed_with_new_work_and_early_without(agent, composerstubs, tmp_path):
    """TD-249 slice 7, design §4.9a *Early is decided from the record*: declared twenty minutes into
    the run, a `restart` whose run reported a new `done` is rule 2's within a tick, and one that
    reported nothing is early — the person's, and no tick replays it, however long it waits."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        for name, worked in (("busy", True), ("bare", False)):
            sid = await _member(agent, person, tmp_path, name=name, lane=["TD-1", "TD-2"])
            first = agent.sessions[sid]
            first.created = _iso(now - timedelta(minutes=20))
            async with LocalClient(caller=sid) as w:
                if worked:
                    await w.call("progress", id=sid, ref="TD-1", status="done", pr=812)
                got = await w.call("progress", id=sid, status="restart", why="context bound")
            assert bool(got["restart_wanted"].get("early")) is not worked, name
            await _idle_for(agent, sid, now, timedelta(minutes=1))
            first.git = dict(CLEAN)
            await agent._keep_running(now)
            if worked:
                new = agent.sessions[sid]
                assert new is not first and first.state == "closed", "replayed by the very next tick"
                assert new.restarts[-1]["why"] == "wanted" and new.restarts[-1]["done"] == [
                    {"ref": "TD-001", "pr": 812}
                ]
                assert new.restart_wanted is None
            else:
                await agent._keep_running(now + timedelta(hours=3))
                assert agent.sessions[sid] is first and first.restart_wanted["early"], "early is the person's"
            await person.call("kill", id=sid)
