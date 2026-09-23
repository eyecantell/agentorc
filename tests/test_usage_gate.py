"""The usage gate (design §6 *Usage gate*, §5 `settings.yml`, §4.7 `ao gate`; TD-100 slice 1): lines
computed from a person's reserves, a pause that is a send and never a kill, the resume, and what the
gate holds off while it stands."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks, wait_for

from sessionorc import settings
from sessionorc.agent import RESUME_MIN
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import Pending

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


@pytest.mark.unit
def test_a_line_is_computed_from_a_reserve():
    """Paul's *about 30% of each session and 10% of the weekly*: a flat reserve is `100 − r`; a
    per-day one is `100 − r × days left`, today counted whole, so the line rises as the week goes —
    30% on the reset day, 90% on the last."""
    assert settings.line(30, None, NOW) == 70
    week = {"per_day": 10}
    assert settings.line(week, _iso(NOW + timedelta(days=6, hours=23)), NOW) == 30  # seven days, today whole
    assert settings.line(week, _iso(NOW + timedelta(days=3, hours=1)), NOW) == 60
    assert settings.line(week, _iso(NOW + timedelta(hours=2)), NOW) == 90  # the last day
    assert settings.line(week, _iso(NOW - timedelta(hours=1)), NOW) == 90  # never below one day
    assert settings.line(week, None, NOW) is None  # no `resets`: no line, as no reserve
    assert settings.line({"per_day": 40}, _iso(NOW + timedelta(days=5)), NOW) == 0  # clamped
    # the line moves at the next day boundary counted back from the reset, or at the reset
    resets = NOW + timedelta(days=3, hours=1)
    assert settings.moves(week, _iso(resets), NOW) == NOW + timedelta(hours=1)
    assert settings.moves(30, _iso(resets), NOW) == resets
    assert settings.moves(week, _iso(NOW + timedelta(hours=2)), NOW) == NOW + timedelta(hours=2)
    assert settings.moves(week, _iso(NOW - timedelta(hours=1)), NOW) is None  # a stale reading's past reset
    assert settings.moves(30, _iso(NOW - timedelta(hours=1)), NOW) is None


@pytest.mark.unit
def test_reserves_drop_whatever_is_not_one(tmp_path):
    """A hand edit can put anything in the file; a malformed entry gates nothing."""
    f = tmp_path / "settings.yml"
    f.write_text(
        'usage_gate:\n  grind: {"5h": 30, wk: {per_day: 10}, bad: 130, worse: {per_hour: 1}, t: true}\n  x: 5\n'
    )
    assert settings.reserves(settings.load(f)) == {"grind": {"5h": 30, "wk": {"per_day": 10}}}
    f.write_text("{not yaml")
    assert settings.load(f) == {}
    assert settings.load(tmp_path / "missing.yml") == {}
    for bad in (101, -1, "30", True, 3.5, {"per_day": 200}, {"per_day": 5, "flat": 1}):
        with pytest.raises(ValueError):
            settings.parse_reserve(bad)
    rows = settings.lines({"wk": 10}, [{"label": "5h", "pct": 99}, {"label": "wk", "pct": 90, "resets": None}], NOW)
    assert [(r["label"], r["line"]) for r in rows] == [("wk", 90)]  # a window with no reserve is left out
    assert settings.crossed(rows)["label"] == "wk"
    assert settings.crossed(settings.lines({"wk": 11}, [{"label": "wk", "pct": 88}], NOW)) is None


async def _worker(person, tmp_path, name="w", **kw):
    return (
        await person.call(
            "create",
            name=name,
            dir=str(tmp_path),
            adapter="shell",
            argv=["bash", "--norc", "--noprofile"],
            unattended=True,
            pause_prompt="echo PAUSE-NOW",
            resume_prompt="echo RESUME-NOW",
            **kw,
        )
    )["id"]


async def _typed(agent, sid: str, text: str) -> bool:
    return any(text in t for t in await agent.rpc_tail(sid, 20))


@pytest.mark.integration
async def test_a_crossed_line_pauses_by_a_send_and_the_resume_follows(agent, tmp_path):
    await park_ticks(agent)  # the test owns the clock: no tick types, polls or re-reads a state
    async with LocalClient() as person:
        sid = await _worker(person, tmp_path)
        agent._usage[""] = {"windows": [{"label": "wk", "pct": 75, "resets": None}], "reason": "ok"}
        got = await person.call("set_settings", profile="", reserves={"wk": 30})
        assert got["reserves"] == {"wk": 30} and got["windows"][0]["line"] == 70 and got["unchecked"] is False
        rec = agent.sessions[sid]
        now = datetime.now(UTC)
        await agent._enforce_usage_gate(now)
        g = rec.gated
        assert g and (g["profile"], g["label"], g["pct"], g["line"]) == ("", "wk", 75, 70) and g["sent_at"]
        assert "resets" in g and g["resets"] is None  # the window's reset, for the card's *resets* word
        assert rec.state not in ("exited", "closed"), "a pause is a send, never a kill"
        assert await wait_for(lambda: _typed(agent, sid, "PAUSE-NOW"), timeout=10)
        # the pause is typed once: a second tick over the line types nothing more and keeps `since`
        since, sent = g["since"], g["sent_at"]
        typed: list[str] = []
        real = agent._submit

        async def counting(sid_, adapter, text):
            typed.append(text)
            await real(sid_, adapter, text)

        agent._submit = counting
        await agent._enforce_usage_gate(now + timedelta(minutes=1))
        assert rec.gated["since"] == since and rec.gated["sent_at"] == sent and typed == []
        # under the line, but inside RESUME_MIN: still paused
        agent._usage[""]["windows"][0]["pct"] = 60
        await agent._enforce_usage_gate(now + timedelta(minutes=1))
        assert rec.gated is not None
        await agent._enforce_usage_gate(now + RESUME_MIN + timedelta(seconds=5))
        assert rec.gated is None and typed == ["echo RESUME-NOW"]
        assert await wait_for(lambda: _typed(agent, sid, "RESUME-NOW"), timeout=10)
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)


@pytest.mark.integration
async def test_the_gate_waits_out_a_dialog_and_leaves_interactive_sessions_alone(agent, tmp_path):
    """A session on a permission or a question is not typed at (§4.2): the mark stands and the send
    lands the tick after the dialog clears. A session a person took over is out of the gate's reach
    (§9 invariant 5): its mark goes and nothing is typed."""
    await park_ticks(agent)  # the test owns the clock: no tick types, polls or re-reads a state
    async with LocalClient() as person:
        sid = await _worker(person, tmp_path)
        agent._usage[""] = {"windows": [{"label": "wk", "pct": 95, "resets": None}], "reason": "ok"}
        await person.call("set_settings", profile="", reserves={"wk": 10})
        rec = agent.sessions[sid]
        rec.set_state("needs-you", confidence="hook", pending=Pending(kind="question", text="which?"))
        await agent._enforce_usage_gate(datetime.now(UTC))
        assert rec.gated and rec.gated["sent_at"] is None, "marked, not typed at"
        rec.set_state("idle", confidence="hook")
        await agent._enforce_usage_gate(datetime.now(UTC))
        assert rec.gated["sent_at"], "sent once the dialog cleared"
        await person.call("set_mode", id=sid, unattended=False)
        await agent._enforce_usage_gate(datetime.now(UTC))
        assert agent.sessions[sid].gated is None
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)


@pytest.mark.integration
async def test_no_reading_gates_nothing_and_a_cleared_reserve_resumes(agent, tmp_path):
    await park_ticks(agent)  # the test owns the clock: no tick types, polls or re-reads a state
    async with LocalClient() as person:
        sid = await _worker(person, tmp_path)
        rec = agent.sessions[sid]
        # before any reading nothing can be checked: accepted, and said to be unchecked
        got = await person.call("set_settings", profile="", reserves={"wk": {"per_day": 10}})
        assert got["unchecked"] is True
        await agent._enforce_usage_gate(datetime.now(UTC))
        assert rec.gated is None
        agent._usage[""] = {"windows": [{"label": "wk", "pct": 99, "resets": None}], "reason": "ok"}
        await agent._enforce_usage_gate(datetime.now(UTC))
        assert rec.gated is None, "a per-day reserve on a window with no reset makes no line"
        await person.call("set_settings", profile="", reserves={"wk": 20})
        await agent._enforce_usage_gate(datetime.now(UTC))
        assert rec.gated
        # a failed poll keeps the last reading (TD-087) — and a profile with none keeps the mark
        agent._usage[""] = {"reason": "error"}
        await agent._enforce_usage_gate(datetime.now(UTC) + RESUME_MIN * 2)
        assert rec.gated, "no reading is no word: the mark stands"
        agent._usage[""] = {"windows": [{"label": "wk", "pct": 99, "resets": None}], "reason": "ok"}
        await person.call("set_settings", profile="", reserves={"wk": None})
        assert settings.reserves(settings.load()) == {}
        await agent._enforce_usage_gate(datetime.now(UTC) + RESUME_MIN * 2)
        assert rec.gated is None, "no reserve, no line: the pause ends"
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)


@pytest.mark.integration
async def test_set_settings_is_a_persons_and_checks_the_labels(agent, tmp_path):
    await park_ticks(agent)  # the test owns the clock: no tick types, polls or re-reads a state
    async with LocalClient() as person:
        sid = await _worker(person, tmp_path)
        agent._usage[""] = {"windows": [{"label": "5h", "pct": 1}, {"label": "wk", "pct": 2}], "reason": "ok"}
        async with LocalClient(caller=sid) as itself:
            with pytest.raises(AgentError, match="a person's own"):
                await itself.call("set_settings", profile="", reserves={"wk": 10})
            assert (await itself.call("gate"))["profiles"] == {}  # a read: never gated
        with pytest.raises(AgentError, match="reports no window week: its windows are 5h, wk"):
            await person.call("set_settings", profile="", reserves={"week": 10})
        with pytest.raises(AgentError, match="whole percent"):
            await person.call("set_settings", profile="", reserves={"wk": 130})
        await person.call("set_settings", profile="", reserves={"5h": 30, "wk": {"per_day": 10}})
        gate = await person.call("gate")
        prof = gate["profiles"][""]
        assert prof["reserves"] == {"5h": 30, "wk": {"per_day": 10}} and prof["labels"] == ["5h", "wk"]
        assert [(w["label"], w["line"]) for w in prof["windows"]] == [("5h", 70), ("wk", None)]
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)


@pytest.mark.integration
async def test_while_gated_a_controllers_send_is_refused_and_the_doorbell_holds(agent, composerstubs, tmp_path):
    """§6: the doorbell does not ring a paused session, and a controller's send is refused at the
    home with the gate as the reason; a person's own send is not (§9 invariant 11)."""
    await park_ticks(agent)  # the test owns the clock: no tick types, polls or re-reads a state
    async with LocalClient() as person:
        lead = await person.call("create", name="lead", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        lead = lead["id"]
        await person.call("set_grants", id=lead, add=["control"])
        # a pane with a composer (TD-027's stub): the doorbell's other rules pass, so the gate is what holds it
        sid = (
            await person.call(
                "create", name="w", dir=str(tmp_path), adapter="composer0", unattended=True,
                controllers=[lead], pause_prompt="pause now",
            )
        )["id"]  # fmt: skip
        assert await wait_for(lambda: _typed(agent, sid, ">>"), timeout=5)
        agent._usage[""] = {"windows": [{"label": "wk", "pct": 90, "resets": None}], "reason": "ok"}
        await person.call("set_settings", profile="", reserves={"wk": 30})
        await agent._enforce_usage_gate(datetime.now(UTC))
        rec = agent.sessions[sid]
        assert rec.gated
        rec.set_state("idle", confidence="hook")
        assert agent._bell_blocked(rec, datetime.now(UTC)) == "paused by the usage gate"
        rec.gated = None
        assert agent._bell_blocked(rec, datetime.now(UTC)) is None  # the gate was the only thing holding it
        await agent._enforce_usage_gate(datetime.now(UTC))
        assert rec.gated
        async with LocalClient(caller=lead) as ctl:
            with pytest.raises(AgentError, match="paused by the usage gate"):
                await ctl.call("send", id=sid, text="echo from-lead")
        await person.call("send", id=sid, text="echo from-person")  # a person's own send is not refused
        for s in (sid, lead):
            await person.call("kill", id=s)
            await person.call("remove", id=s)


@pytest.mark.integration
async def test_a_session_made_unattended_later_can_carry_the_two_texts(agent, tmp_path):
    """`set_mode` takes the gate's texts as `set_stop` takes the wrap-up's, so a session flipped to
    unattended after its create is not gated with nothing to type (review of TD-100 slice 2)."""
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = (await person.call("create", name="i", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        assert agent.sessions[sid].pause_prompt is None
        got = await person.call("set_mode", id=sid, unattended=True, pause_prompt="p", resume_prompt="r")
        assert (got["unattended"], got["pause_prompt"], got["resume_prompt"]) == (True, "p", "r")
        got = await person.call("set_mode", id=sid, unattended=False)
        assert got["pause_prompt"] == "p", "left alone when not given"
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)
