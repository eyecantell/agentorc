"""TD-195, design §6 *Keeping a team running* rule 6: a member that declared out of work is told,
by one `note` from `system`, when its lane gains ledger entries — each entry once, by the entry's
header and never its prose, and only while the member is live and not winding down."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks

from sessionorc.client import LocalClient
from sessionorc.ledger import lane_matches

pytestmark = pytest.mark.integration


def _e(id: str, pickable: str = "yes", kind: str = "build") -> dict:
    return {"id": id, "title": id, "priority": "medium", "owner": "grinder", "kind": kind, "pickable": pickable}


def test_a_lane_word_matches_by_the_header():
    assert lane_matches("free-pick", _e("TD-1")) and not lane_matches("free-pick", _e("TD-1", pickable="no"))
    assert not lane_matches("free-pick", _e("TD-1", kind="design-first"))
    assert lane_matches("design-first", _e("TD-1", kind="design-first"))
    assert not lane_matches("design-first", _e("TD-1", kind="design-first", pickable="no"))
    assert not lane_matches("design-first", _e("TD-1"))
    for word in ("TD-001", "hunt", ""):
        assert not lane_matches(word, _e("TD-1"))


async def _finished(agent, person, tmp_path, name: str, lane: list[str], entries: list[dict], **fields) -> str:
    """A supervised, unattended member of `tmp_path`'s repo that declared out of work, and the
    repo's ledger reading holding `entries`."""
    (tmp_path / name).mkdir()
    sid = (
        await person.call(
            "create", name=name, dir=str(tmp_path / name), adapter="shell", argv=["bash", "--norc"],
            unattended=True, supervised=True, lane=lane,
        )
    )["id"]  # fmt: skip
    rec = agent.sessions[sid]
    rec.repo = str(tmp_path)
    rec.out_of_work = {"at": "2026-09-27T20:00:00Z", "why": "nothing pickable"}
    for k, v in fields.items():
        setattr(rec, k, v)
    agent._repos[str(tmp_path)] = {"name": "r", "root": str(tmp_path), "ledger": {"entries": entries}}
    return sid


def _notes(agent, sid: str) -> list[str]:
    return [e.text for e in agent.sessions[sid].inbox if e.from_ == "system"]


async def test_an_entry_filed_after_the_declaration_is_told_once(agent, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        led = [_e("TD-001"), _e("TD-002", pickable="no"), _e("TD-003", kind="design-first")]
        sid = await _finished(agent, person, tmp_path, "w", ["free-pick"], led)
        rec = agent.sessions[sid]
        await agent._keep_running(now)
        assert rec.lane_seen and rec.lane_seen["ids"] == ["TD-001"], "the first tick writes what was there"
        assert _notes(agent, sid) == [], "an entry present at the declaration is not new"
        # an entry filed since, one that turned pickable, and a design-first one that is not this lane's
        agent._repos[str(tmp_path)]["ledger"]["entries"] = [
            _e("TD-001"), _e("TD-002"), _e("TD-003", kind="design-first"), _e("TD-004"),
        ]  # fmt: skip
        agent._lane_news(rec, now)
        notes = _notes(agent, sid)
        assert len(notes) == 1
        assert "your lane gained 2 entries since you declared out of work: TD-002, TD-004" in notes[0]
        assert rec.lane_seen["ids"] == ["TD-001", "TD-002", "TD-004"]
        agent._lane_news(rec, now + timedelta(minutes=1))
        assert len(_notes(agent, sid)) == 1, "an entry is told once"
        # more than five: named up to five, and the rest counted
        agent._repos[str(tmp_path)]["ledger"]["entries"] += [_e(f"TD-01{i}") for i in range(7)]
        agent._lane_news(rec, now)
        assert "7 entries" in _notes(agent, sid)[1] and "TD-014 and 2 more —" in _notes(agent, sid)[1]
        # a claim takes the declaration back, and rule 6's memory with it
        await person.call("progress", id=sid, ref="TD-2", status="claimed", caller=sid)
        assert rec.out_of_work is None and rec.lane_seen is None
        await person.call("kill", id=sid)


async def test_what_rule_six_leaves_alone(agent, tmp_path):
    """A lane of references, a seat, an exited member, a gated one, one past its stop, one asked to
    wrap up and one not supervised are told nothing; a failed ledger reading writes nothing."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    past = (now - timedelta(minutes=5)).isoformat()
    async with LocalClient() as person:
        cases = {
            "refs": (["TD-001"], {}),
            "seat": (["free-pick"], {"seat": "techlead"}),
            "exited": (["free-pick"], {"state": "exited"}),
            "gated": (["free-pick"], {"gated": {"since": past, "profile": "p"}}),
            "stopped": (["free-pick"], {"run_until": past}),
            "wrapping": (["free-pick"], {"wrapup_sent_at": past}),
            "unsupervised": (["free-pick"], {"supervised": False}),
        }
        ids = {}
        for n, (lane, fields) in cases.items():
            sub = tmp_path / n
            sub.mkdir()
            ids[n] = await _finished(agent, person, sub, n, lane, [_e("TD-001")], **fields)
            agent._lane_news(agent.sessions[ids[n]], now)
            agent._repos[str(sub)]["ledger"]["entries"].append(_e("TD-002"))
            agent._lane_news(agent.sessions[ids[n]], now)
        for n, sid in ids.items():
            assert _notes(agent, sid) == [], n
        assert agent.sessions[ids["refs"]].lane_seen == {"at": agent.sessions[ids["refs"]].lane_seen["at"], "ids": []}
        assert agent.sessions[ids["seat"]].lane_seen is None and agent.sessions[ids["unsupervised"]].lane_seen is None
        # the reading failed: nothing written, and the tick looks again
        broken = tmp_path / "broken"
        broken.mkdir()
        failed = await _finished(agent, person, broken, "broken", ["free-pick"], [_e("TD-001")])
        agent._repos[str(broken)]["ledger"]["error"] = "docs/technical_debt.md: gone"
        agent._lane_news(agent.sessions[failed], now)
        assert agent.sessions[failed].lane_seen is None
        for sid in [*ids.values(), failed]:
            await person.call("kill", id=sid)
