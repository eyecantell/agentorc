"""TD-195, design §6 *Keeping a team running* rule 6: a member that declared out of work is told,
by one `note` from `system`, when its lane gains ledger entries — each entry once, by the entry's
header and never its prose, and only while the member is live and not winding down."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks

from sessionorc.agent_common import RpcError, _lane
from sessionorc.client import LocalClient
from sessionorc.ledger import entries_before, lane_matches
from sessionorc.models import lane_refs, report_line

pytestmark = pytest.mark.integration


def _e(id: str, pickable: str = "yes", kind: str = "build") -> dict:
    return {"id": id, "title": id, "priority": "medium", "owner": "grinder", "kind": kind, "pickable": pickable}


def test_a_lane_word_matches_by_the_header():
    assert lane_matches(["free-pick"], _e("TD-1")) and not lane_matches(["free-pick"], _e("TD-1", pickable="no"))
    assert not lane_matches(["free-pick"], _e("TD-1", kind="design-first"))
    assert lane_matches(["design-first"], _e("TD-1", kind="design-first"))
    assert not lane_matches(["design-first"], _e("TD-1", kind="design-first", pickable="no"))
    assert not lane_matches(["design-first"], _e("TD-1"))
    for word in ("TD-001", "hunt", ""):
        assert not lane_matches([word], _e("TD-1"))


def test_an_owner_word_narrows_the_lane_and_is_no_reference():
    """TD-227 slice 1: `[free-pick, owner:grinder]` takes an entry owned by grinder or by nobody and
    leaves the anchor's out; the word alone matches nothing; it is never counted as a reference."""
    lane = ["free-pick", "owner:grinder"]
    assert lane_matches(lane, _e("TD-1")) and lane_matches(lane, {**_e("TD-1"), "owner": ""})
    assert not lane_matches(lane, {**_e("TD-1"), "owner": "anchor"})
    assert lane_matches(["free-pick", "owner:anchor", "owner:Grinder"], _e("TD-1"))
    assert lane_matches(["free-pick"], {**_e("TD-1"), "owner": "anchor"}), "no owner word: as it was"
    assert not lane_matches(["owner:grinder"], _e("TD-1"))
    assert lane_refs(["free-pick", "owner:grinder"]) == [] and lane_refs(["TD-001", "owner:x", "design-first"]) == [
        "TD-001"
    ]
    assert report_line({"lane": ["TD-001", "owner:grinder"], "progress": []}) == "0/1 done"
    assert _lane(["free-pick", "Owner:Grinder"]) == ["free-pick", "owner:grinder"]
    assert _lane(["td-7", "owner:grinder", "owner:grinder"]) == ["TD-007", "owner:grinder"]
    for bad in (["owner:grinder"], ["free-pick", "owner:"], ["free-pick", "TD-001", "owner:grinder"]):
        with pytest.raises(RpcError):
            _lane(bad)


def _git(root, *args: str, when: str | None = None) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t"}
    env["GIT_COMMITTER_EMAIL"] = "t@t"
    if when:
        env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = when
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=env).stdout


def _ledger(ids: list[str]) -> str:
    return "".join(f"## {i}: {i}\n\n**Pickable:** yes\n**Kind:** build\n\n" for i in ids)


def _commit(root, ids: list[str], when: str) -> None:
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "technical_debt.md").write_text(_ledger(ids))
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "ledger", when=when)
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")


def test_the_ledger_before_an_instant_is_read_from_origin(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, ["TD-001"], "2026-09-27T10:00:00Z")
    _commit(tmp_path, ["TD-001", "TD-002"], "2026-09-28T10:00:00Z")
    got, why = entries_before(tmp_path, "docs/technical_debt.md", datetime(2026, 9, 27, 20, tzinfo=UTC))
    assert [e["id"] for e in got] == ["TD-001"] and why.startswith("origin/main at ")
    assert entries_before(tmp_path, "docs/technical_debt.md", datetime(2026, 9, 1, tzinfo=UTC))[0] is None
    assert entries_before(tmp_path, "docs/nope.md", datetime(2026, 9, 29, tzinfo=UTC))[0] is None
    assert entries_before(tmp_path / "docs", "technical_debt.md", datetime(2026, 9, 29, tzinfo=UTC))[0] is None
    assert [e["id"] for e in entries_before(tmp_path, "docs/technical_debt.md", None)[0]] == ["TD-001", "TD-002"]


async def test_the_first_write_is_the_ledger_at_the_declaration(agent, tmp_path):
    """TD-227 slice 1: declared at 20:00, an entry merged at 21:00, the first tick a day later — the
    entry is new and is told on that first tick; a checkout with no history falls back."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, ["TD-001"], "2026-09-27T10:00:00Z")
    _commit(repo, ["TD-001", "TD-002"], "2026-09-27T21:00:00Z")
    async with LocalClient() as person:
        # the checkout's reading also holds TD-009, an entry only a branch here has: never told as new
        led = [_e("TD-001"), _e("TD-002"), _e("TD-009")]
        sid = await _finished(agent, person, repo, "w", ["free-pick"], led)
        agent._repos[str(repo)]["ledger"]["path"] = "docs/technical_debt.md"
        rec = agent.sessions[sid]
        await agent._lane_news(rec, now)
        assert rec.lane_seen["ids"] == ["TD-001", "TD-009", "TD-002"] and "TD-009" not in _notes(agent, sid)[0]
        await agent._lane_news(rec, now + timedelta(minutes=1))
        assert len(_notes(agent, sid)) == 1, "the checkout's own entry is not told on a later tick either"
        # a second `none` looks afresh: the first write again sees the checkout's own entry, untold
        rec.out_of_work, rec.lane_seen = {"at": "2026-09-28T00:00:00Z", "why": "nothing pickable"}, None
        await agent._lane_news(rec, now + timedelta(minutes=2))
        await agent._lane_news(rec, now + timedelta(minutes=3))
        assert rec.lane_seen["ids"] == ["TD-001", "TD-002", "TD-009"] and len(_notes(agent, sid)) == 1
        assert (
            len(_notes(agent, sid)) == 1
            and "gained 1 entry since you declared out of work: TD-002" in _notes(agent, sid)[0]
        )
        await person.call("kill", id=sid)


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
        await agent._lane_news(rec, now)
        notes = _notes(agent, sid)
        assert len(notes) == 1
        assert "your lane gained 2 entries since you declared out of work: TD-002, TD-004" in notes[0]
        assert rec.lane_seen["ids"] == ["TD-001", "TD-002", "TD-004"]
        await agent._lane_news(rec, now + timedelta(minutes=1))
        assert len(_notes(agent, sid)) == 1, "an entry is told once"
        # more than five: named up to five, and the rest counted
        agent._repos[str(tmp_path)]["ledger"]["entries"] += [_e(f"TD-01{i}") for i in range(7)]
        await agent._lane_news(rec, now)
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
            await agent._lane_news(agent.sessions[ids[n]], now)
            agent._repos[str(sub)]["ledger"]["entries"].append(_e("TD-002"))
            await agent._lane_news(agent.sessions[ids[n]], now)
        for n, sid in ids.items():
            assert _notes(agent, sid) == [], n
        assert agent.sessions[ids["refs"]].lane_seen == {"at": agent.sessions[ids["refs"]].lane_seen["at"], "ids": []}
        assert agent.sessions[ids["seat"]].lane_seen is None and agent.sessions[ids["unsupervised"]].lane_seen is None
        # the reading failed: nothing written, and the tick looks again
        broken = tmp_path / "broken"
        broken.mkdir()
        failed = await _finished(agent, person, broken, "broken", ["free-pick"], [_e("TD-001")])
        agent._repos[str(broken)]["ledger"]["error"] = "docs/technical_debt.md: gone"
        await agent._lane_news(agent.sessions[failed], now)
        assert agent.sessions[failed].lane_seen is None
        for sid in [*ids.values(), failed]:
            await person.call("kill", id=sid)
