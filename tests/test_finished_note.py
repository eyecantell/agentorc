"""Rule 9's note says more (design §4.9a *The home's note says more*, §6 rule 9, TD-468): under its
first lines, the claims a team's members left and dropped, their restarts, the identity alarms on its
records, the questions still open to the person, and each profile's usage against the team's start —
each only when it has something to say — and `teams.<team>.usage_at_start`, written by each start."""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import park_ticks

from sessionorc import work
from sessionorc.client import LocalClient
from sessionorc.models import PERSON, SYSTEM, MailEntry, ProgressEntry, Session

START = "2026-10-09T06:00:00Z"
AFTER = "2026-10-09T07:00:00Z"
BEFORE = "2026-10-08T07:00:00Z"


def _rec(name: str, **kw) -> Session:
    base = dict(
        id=f"ao-n-{name}", name=name, kind="interactive", adapter="shell", dir="/tmp/x", state="closed",
        team="g", unattended=True, created=START, profile="grind",
        out_of_work={"at": AFTER, "why": f"{name} found nothing pickable"},
    )  # fmt: skip
    return Session(**{**base, **kw})


def _note(agent, records: list[Session]) -> str:
    for r in records:
        r.host = agent.host
        agent.sessions[r.id] = r
    agent._finished_tell("g", records, None, datetime.now(UTC))
    (note,) = [e.text for e in agent.person_inbox if e.from_ == SYSTEM]
    return note


async def test_a_clean_teams_note_is_its_first_lines_alone(agent):
    await park_ticks(agent)
    agent._usage.clear()
    g1 = _rec("g1", progress=[ProgressEntry(ref="TD-1", status="done", pr=812, at=AFTER)])
    note = _note(agent, [g1])
    assert note.splitlines() == [
        "g finished and the host agent wound it down: every member declared out of work. Nothing is asked of you.",
        "",
        "Pull requests reported done since the team started: #812.",
        "g1: g1 found nothing pickable",
    ]


async def test_the_note_says_what_the_records_hold(agent):
    await park_ticks(agent)
    g1 = _rec(
        "g1",
        progress=[
            ProgressEntry(ref="TD-5", status="claimed", at=AFTER),
            ProgressEntry(ref="TD-6", status="dropped", why="asked: m-1", at=AFTER),
            ProgressEntry(ref="TD-4", status="dropped", why="before the start", at=BEFORE),
        ],
        restarts=[
            {"at": AFTER, "why": "wanted"},
            {"at": AFTER, "why": "cache"},
            {"at": BEFORE, "why": "crash"},
        ],
    )
    g2 = _rec(
        "g2",
        restarts=[{"at": AFTER, "why": "wanted"}],
        restart_ceiling={"at": AFTER, "count": 5},
        identity_alarms=[{"channel": "x", "claimed": "y", "rpc": "msg", "count": 2, "at": AFTER, "last": AFTER}],
    )
    agent.person_inbox.append(
        MailEntry(
            id="m-s",
            from_=g1.id,
            to=[PERSON],
            at=AFTER,
            kind="steer",
            text="go?",
            about="TD-5",
            bound="2026-10-10T09:57:00Z",
            default="go",
        )  # fmt: skip
    )
    agent._usage["grind"] = {"windows": [{"label": "week", "pct": 61.2}, {"label": "5h", "pct": 12}]}
    agent._host_rec.setdefault("teams", {})["g"] = {"usage_at_start": {"grind": {"week": 48}}}
    lines = _note(agent, [g1, g2]).splitlines()
    more = lines[lines.index("g2: g2 found nothing pickable") + 1 :]
    assert more == [
        "",
        "Claims left — g1: TD-5 left claimed; TD-6 dropped — asked: m-1",
        "Restarts — 3 restarts: 2 wanted, 1 cache lapsed; at the restart ceiling: g2",
        "Alarms — 1 identity alarm standing on g2 (§4.8a)",
        f"Open to you — g1: TD-005 (m-s, until {work.bound_words('2026-10-10T09:57:00Z')})",
        "Usage — grind: week 61%, from 48%; 5h 12%",
    ]


async def test_a_start_keeps_the_usage_reading_and_a_running_team_does_not(agent):
    await park_ticks(agent)
    agent._usage.clear()
    agent._usage["grind"] = {"windows": [{"label": "week", "pct": 48}]}
    done = _rec("g1")
    agent.sessions[done.id] = done
    agent._mark_team_start("g")  # nothing of g runs: a start
    assert agent._host_rec["teams"]["g"]["usage_at_start"] == {"grind": {"week": 48}}
    live = _rec("g2", state="idle")
    agent.sessions[live.id] = live
    agent._usage["grind"] = {"windows": [{"label": "week", "pct": 70}]}
    agent._mark_team_start("g")  # g2 runs: a member started beside it is no start
    assert agent._host_rec["teams"]["g"]["usage_at_start"] == {"grind": {"week": 48}}
    agent._mark_team_start("g", but=live)  # rule 8's or a schedule's replay of the one that runs
    assert agent._host_rec["teams"]["g"]["usage_at_start"] == {"grind": {"week": 70}}
    agent._usage.clear()
    del agent.sessions[live.id]
    agent._mark_team_start("g")  # no reading: the key goes, never a stale one
    assert "usage_at_start" not in (agent._host_rec["teams"].get("g") or {})


async def test_the_tick_marks_a_start_and_rule_8s_but_no_restart(agent, monkeypatch):
    await park_ticks(agent)
    seen: list[str] = []
    monkeypatch.setattr(agent, "_mark_team_start", lambda team, but=None: seen.append(team))
    s = _rec("g1")
    agent.sessions[s.id] = s
    for why in ("start", "work", "crash", "wanted", "cache", "fill"):
        await agent._replay(s, why)  # no launch record: each replay fails after the mark
    assert seen == ["g", "g"]


async def test_ao_team_starts_create_marks_the_start(agent, tmp_path):
    await park_ticks(agent)
    agent._usage.clear()
    agent._usage["grind"] = {"windows": [{"label": "week", "pct": 33}]}
    async with LocalClient() as person:
        await person.call(
            "create", name="g9", dir=str(tmp_path), adapter="shell", unattended=True, team="h", team_start=True
        )
        assert agent._host_rec["teams"]["h"]["usage_at_start"] == {"grind": {"week": 33}}
        agent._usage["grind"] = {"windows": [{"label": "week", "pct": 50}]}
        (tmp_path / "b").mkdir()
        await person.call("create", name="g10", dir=str(tmp_path / "b"), adapter="shell", unattended=True, team="h")
        assert agent._host_rec["teams"]["h"]["usage_at_start"] == {"grind": {"week": 33}}, "a create alone is no start"
