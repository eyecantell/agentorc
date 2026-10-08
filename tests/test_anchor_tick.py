"""The anchor seat's tick (design §6 rule 3 *The anchor seat*, rule 6's lane word `anchor`, §4.9b,
TD-386): the lane takes the anchor's entries and every work order; `seat_due` with `by: work` when
the lane gains an id its `lane_seen` does not hold, once per stretch; the fill gated by the
checkout — occupancy, then a clean tree on its default branch — with `seat_held` saying why; and a
Start over a held checkout writing the seat's record alone, closed, for rule 3 to fill."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import park_ticks

from sessionorc import ledger as ledger_mod
from sessionorc.client import LocalClient


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _checkout(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "README").write_text("x\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return root


def _entry(i: str, **kw) -> dict:
    return {"id": i, "pickable": "yes", "owner": "anchor", "kind": "", "blocked_by": [], **kw}


def _reading(agent, root: Path, *entries: dict, orders: tuple = ()) -> None:
    agent._repos[str(root)] = {
        "root": str(root),
        "ledger": {"entries": list(entries)},
        "work_orders": {"orders": list(orders)},
    }


async def _anchor(person, root: Path, **kw) -> str:
    params = {
        "name": "t-anchor",
        "dir": str(root),
        "repo": str(root),
        "adapter": "shell",
        "argv": ["bash", "--norc", "--noprofile"],
        "unattended": True,
        "supervised": True,
        "lane": ["anchor"],
        "prompt": "the anchor's brief",
        "seat": {"trigger": "work"},
    }
    return (await person.call("create", **{**params, **kw}))["id"]


def _end(agent, sid: str) -> None:
    rec = agent.sessions[sid]
    rec.state, rec.pane, rec.exit_code = "exited", True, 0
    agent.store.save(rec)


@pytest.mark.unit
def test_the_lane_word_anchor_takes_the_anchors_entries_its_decisions_and_every_work_order():
    lane = ["anchor"]
    assert ledger_mod.lane_matches(lane, _entry("TD-1", kind="evaluation"))  # of any kind
    assert ledger_mod.lane_matches(lane, _entry("TD-2", kind="live-check", live="yes"))
    assert not ledger_mod.lane_matches(lane, _entry("TD-3", kind="live-check", live="no"))  # not live yet
    assert not ledger_mod.lane_matches(lane, _entry("TD-4", owner="grinder"))
    assert not ledger_mod.lane_matches(lane, _entry("TD-5", pickable="no"))
    # a decision the anchor owes is its to make, pickable or not, as the designer's is
    owed = _entry("TD-6", owner="grinder", pickable="no", blocked_by=["decision (anchor)"])
    assert ledger_mod.lane_matches(lane, owed)
    assert not ledger_mod.lane_matches(["free-pick"], owed)
    order = {"id": "board:1a2b3c4d", "work_order": True, "pickable": "yes"}
    assert ledger_mod.lane_matches(lane, order) and ledger_mod.lane_matches(["free-pick"], order)
    assert not ledger_mod.lane_matches(["design-first"], order)
    # a grinder's lane never takes the anchor's entries
    assert not ledger_mod.lane_matches(["free-pick", "owner:grinder"], _entry("TD-1"))


@pytest.mark.integration
async def test_the_lane_gaining_an_id_fills_the_ended_seat_once_per_stretch(agent, tmp_path):
    await park_ticks(agent)
    root = _checkout(tmp_path)
    _reading(agent, root, _entry("TD-1"))
    async with LocalClient() as person:
        sid = await _anchor(person, root)
        first = agent.sessions[sid]
        now = datetime.now(UTC)
        await agent._keep_running(now)
        assert first.lane_seen["ids"] == ["TD-1"] and first.seat_due is None, "what stood at create is this run's"
        _end(agent, sid)
        await agent._keep_running(now)
        assert agent.sessions[sid] is first and first.seat_due is None, "nothing new: no fill"
        order = {"id": "board:1a2b3c4d", "work_order": True, "pickable": "yes"}
        _reading(agent, root, _entry("TD-1"), _entry("TD-2"), orders=(order,))
        await agent._keep_running(now)
        new = agent.sessions[sid]
        assert new is not first and [r["why"] for r in new.restarts] == ["fill"]
        assert first.seat_due["by"] == "work" and first.seat_due["ids"] == ["TD-2", "board:1a2b3c4d"]
        assert new.lane_seen["ids"] == ["TD-1", "TD-2", "board:1a2b3c4d"] and new.seat_due is None
        # it declares out of work on the same ids: the next reading raises no second fill
        async with LocalClient(caller=sid) as me:
            await me.call("progress", id=sid, status="none", why="nothing I can do")
        assert new.lane_seen["ids"] == ["TD-1", "TD-2", "board:1a2b3c4d"], "the reading at the declaration"
        _end(agent, sid)
        await agent._keep_running(now)
        assert agent.sessions[sid] is new and new.seat_due is None
        assert new.lane_seen["ids"] == ["TD-1", "TD-2", "board:1a2b3c4d"]
        await person.call("kill", id=sid)


@pytest.mark.integration
async def test_an_idle_seat_whose_lane_gained_work_is_closed_for_a_fill_from_cold(agent, tmp_path):
    from sessionorc.agent import SEAT_IDLE_GRACE

    await park_ticks(agent)
    root = _checkout(tmp_path)
    _reading(agent, root, _entry("TD-1"))
    async with LocalClient() as person:
        sid = await _anchor(person, root)
        rec = agent.sessions[sid]
        now = datetime.now(UTC)
        await agent._keep_running(now)
        _reading(agent, root, _entry("TD-1"), _entry("TD-2"))
        rec.state, rec.confidence = "idle", "hook"
        rec.since = (now - SEAT_IDLE_GRACE * 2).isoformat()
        rec.created = (now - SEAT_IDLE_GRACE * 4).isoformat()
        rec.git = {"branch": "main", "dirty": 0, "unpushed": 0}
        await agent._keep_running(now)
        assert rec.seat_due["ids"] == ["TD-2"]
        assert rec.state == "closed" and (rec.closer or {}).get("why") == "seat"
        await person.call("kill", id=agent.sessions[sid].id)


@pytest.mark.integration
async def test_the_fill_waits_on_a_holder_or_the_persons_tree_and_says_why(agent, tmp_path, monkeypatch):
    await park_ticks(agent)
    root = _checkout(tmp_path)
    _reading(agent, root, _entry("TD-1"))
    async with LocalClient() as person:
        sid = await _anchor(person, root)
        first = agent.sessions[sid]
        now = datetime.now(UTC)
        await agent._keep_running(now)
        _end(agent, sid)
        _reading(agent, root, _entry("TD-1"), _entry("TD-2"))
        # a person's session in the checkout: the fill waits, the record says who
        holders = ["ao-repo-paul (working)"]
        monkeypatch.setattr(agent, "occupants", lambda d: list(holders))
        await agent._keep_running(now)
        assert agent.sessions[sid] is first and first.seat_due["ids"] == ["TD-2"]
        assert first.seat_held == {"by": "ao-repo-paul", "why": "held by ao-repo-paul (working)"}
        # the person left, but their work is uncommitted on a branch: still the person's
        holders.clear()
        _git(root, "switch", "-q", "-c", "td-x")
        (root / "a.txt").write_text("a\n")
        (root / "b.txt").write_text("b\n")
        await agent._keep_running(now)
        assert agent.sessions[sid] is first
        assert first.seat_held == {"by": "checkout", "why": "branch td-x, 2 files uncommitted"}
        # back on main and clean: the fill lands and nothing waits any more
        _git(root, "switch", "-q", "main")
        (root / "a.txt").unlink()
        (root / "b.txt").unlink()
        await agent._keep_running(now)
        new = agent.sessions[sid]
        assert new is not first and new.seat_held is None and new.seat_due is None
        await person.call("kill", id=sid)


@pytest.mark.integration
async def test_a_held_create_writes_the_seat_closed_with_no_pane_and_its_whole_lane_is_the_first_fill(
    agent, tmp_path, monkeypatch
):
    from sessionorc.client import AgentError

    await park_ticks(agent)
    root = _checkout(tmp_path)
    _reading(agent, root, _entry("TD-1"))
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="held writes a seat's record alone"):
            await _anchor(person, root, held=True, seat=None)
        sid = await _anchor(person, root, held=True)
        rec = agent.sessions[sid]
        assert rec.state == "closed" and rec.lane_seen["ids"] == []
        assert sid not in {p.session for p in agent.tmux.list_panes()}, "no pane"
        monkeypatch.setattr(agent, "occupants", lambda d: ["ao-repo-paul (working)"])
        await agent._keep_running(datetime.now(UTC))
        assert rec.seat_due["ids"] == ["TD-1"] and rec.seat_held["by"] == "ao-repo-paul"
        monkeypatch.setattr(agent, "occupants", lambda d: [])
        await agent._keep_running(datetime.now(UTC))
        new = agent.sessions[sid]
        assert new is not rec and [r["why"] for r in new.restarts] == ["fill"]
        await person.call("kill", id=sid)


@pytest.mark.integration
async def test_an_id_landing_between_the_declaration_and_the_tick_still_fills(agent, tmp_path):
    """Review of TD-386: a `work` seat's `none` writes `lane_seen` from the reading then, not at the
    next tick, so an entry filed in between is new."""
    await park_ticks(agent)
    root = _checkout(tmp_path)
    _reading(agent, root, _entry("TD-1"))
    async with LocalClient() as person:
        sid = await _anchor(person, root)
        first = agent.sessions[sid]
        async with LocalClient(caller=sid) as me:
            await me.call("progress", id=sid, status="none", why="looked")
        assert first.lane_seen["ids"] == ["TD-1"]
        _reading(agent, root, _entry("TD-1"), _entry("TD-2"))
        _end(agent, sid)
        await agent._keep_running(datetime.now(UTC))
        assert agent.sessions[sid] is not first and first.seat_due["ids"] == ["TD-2"]
        await person.call("kill", id=sid)


@pytest.mark.integration
async def test_a_held_create_never_writes_over_a_running_seat(agent, tmp_path):
    from sessionorc.client import AgentError

    await park_ticks(agent)
    root = _checkout(tmp_path)
    async with LocalClient() as person:
        sid = await _anchor(person, root)
        with pytest.raises(AgentError, match="is running|the seat is already there"):
            await _anchor(person, root, held=True)
        assert agent.sessions[sid].state not in ("exited", "closed")
        for bad in ({"unattended": False}, {"resume": "abc"}):
            with pytest.raises(AgentError, match="held writes a seat's record alone"):
                await _anchor(person, root, held=True, **bad)
        await person.call("kill", id=sid)


@pytest.mark.integration
async def test_a_master_default_with_no_origin_is_not_held_as_a_branch(agent, tmp_path, monkeypatch):
    await park_ticks(agent)
    root = _checkout(tmp_path)
    _git(root, "branch", "-m", "main", "master")
    _reading(agent, root, _entry("TD-1"))
    async with LocalClient() as person:
        sid = await _anchor(person, root, held=True)
        monkeypatch.setattr(agent, "occupants", lambda d: [])
        await agent._keep_running(datetime.now(UTC))
        assert agent.sessions[sid].id == sid and [r["why"] for r in agent.sessions[sid].restarts] == ["fill"]
        await person.call("kill", id=sid)
