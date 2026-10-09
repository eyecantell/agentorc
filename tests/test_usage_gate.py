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


@pytest.mark.unit
def test_the_four_keys_read_what_is_valid_and_parse_refuses_the_rest(tmp_path):
    """design §5 (TD-146): `teams`, `repos` and `person` beside `usage_gate` — the readers drop
    whatever a hand edit made invalid, field by field; the parsers, which `set_settings` writes
    through, refuse it naming the key."""
    f = tmp_path / "settings.yml"
    f.write_text(
        "teams:\n"
        "  ao-grind: {until: '2026-09-26T06:00:00-06:00', reserve: 10, schedule: {start: reset}}\n"
        "  bad: {reserve: 130, until: tomorrow}\n"
        "  odd: {colour: red}\n"
        "  stray: {reserve: 5, colour: red}\n"
        "repos:\n  agentorc: {promote: {auto: false}}\n  x: {promote: {auto: maybe}}\n"
        "person:\n  open_in: none\n  terminal: {size: 13, face: JetBrains Mono, copy_on_select: 7}\n"
    )
    doc = settings.load(f)
    assert settings.teams(doc) == {
        "ao-grind": {"until": "2026-09-26T12:00:00Z", "reserve": 10, "schedule": {"start": "reset"}},
        "stray": {"reserve": 5},  # one stray key costs that key, not the team
    }
    assert settings.repos(doc) == {"agentorc": {"promote": {"auto": False}}}
    assert settings.person(doc) == {"open_in": "none", "terminal": {"size": 13, "face": "JetBrains Mono"}}
    assert settings.team_extra(doc, "ao-grind") == 10 and settings.team_extra(doc, "") == 0
    assert settings.team_extra(doc, "bad") == 0
    for bad in ({"reserve": 101}, {"until": "2026-09-26T06:00:00"}, {"schedule": "reset"}, {"colour": 1}, "x"):
        with pytest.raises(ValueError):
            settings.parse_team(bad)
    for bad in ({"promote": {"auto": "yes"}}, {"promote": {"run": "x"}}, {"auto": True}):
        with pytest.raises(ValueError):
            settings.parse_repo(bad)
    for bad in ({"open_in": ""}, {"open_in": {"label": "Z"}}, {"terminal": {"size": 99}}, {"theme": "dark"}):
        with pytest.raises(ValueError):
            settings.parse_person(bad)
    # a team's reserve priority lowers every line its profile's reserve makes, and makes none
    rows = settings.lines({"5h": 30}, [{"label": "5h", "pct": 61}, {"label": "wk", "pct": 99}], NOW, extra=10)
    assert [(r["label"], r["line"], r["extra"]) for r in rows] == [("5h", 60, 10)]
    assert settings.crossed(rows)["label"] == "5h"
    assert settings.merge({"t": {"until": "a", "reserve": 5}}, {"t": {"until": None}}) == {"t": {"reserve": 5}}
    assert settings.merge({"t": {"reserve": 5}}, {"t": None, "u": {"reserve": 1}}) == {"u": {"reserve": 1}}
    from sessionorc.agent import BACKUP_MEMBERS

    assert "settings.yml" in BACKUP_MEMBERS  # the nightly backup carries the person's settings


@pytest.mark.integration
async def test_a_teams_reserve_priority_pauses_its_sessions_first(agent, tmp_path):
    """§6 *Usage gate* (TD-146): grind at 30 and ao-grind's priority at 10 — the team's session
    pauses at 60%, the profile's plain one at 70%, and the team's mark says whose line it was."""
    await park_ticks(agent)
    async with LocalClient() as person:
        plain = await _worker(person, tmp_path, name="p")
        teamed = await _worker(person, tmp_path, name="t", team="ao-grind")
        agent._usage[""] = {"windows": [{"label": "5h", "pct": 65, "resets": None}], "reason": "ok"}
        await person.call("set_settings", profile="", reserves={"5h": 30}, teams={"ao-grind": {"reserve": 10}})
        await agent._enforce_usage_gate(datetime.now(UTC))
        assert agent.sessions[plain].gated is None, "65% is under the profile's 70% line"
        g = agent.sessions[teamed].gated
        assert g and g["line"] == 60 and g["team_extra"] == {"team": "ao-grind", "n": 10}
        assert (await person.call("gate"))["profiles"][""]["windows"][0]["line"] == 70  # the chip: the profile's
        for s in (plain, teamed):
            await person.call("kill", id=s)
            await person.call("remove", id=s)


@pytest.mark.unit
def test_board_show_takes_the_four_modes_and_the_reader_drops_the_rest():
    """§5 `person.inbox.board_show`, §4.5 screen 6 *The board's horizon* (TD-220 slice 1): `next:<n>`
    with n from 1 to 50, `due`, `<n>d` with n from 1 to 365, or `all`; the parser refuses anything
    else naming the shapes, and the reader drops it so the page falls back to `next:10`."""
    for word, kept in (
        ("next:10", "next:10"),
        ("next:1", "next:1"),
        ("next:50", "next:50"),
        ("due", "due"),
        ("7d", "7d"),
        ("365d", "365d"),
        ("all", "all"),
        (" 07d ", "7d"),
    ):
        assert settings.parse_board_show(word) == kept
    for bad in ("next:0", "next:51", "0d", "366d", "soon", "next:", "d", "", 10, None, "7 d", "next:-1", "\u0667d"):
        with pytest.raises(ValueError, match="board_show is next:<n>"):
            settings.parse_board_show(bad)
    assert settings.BOARD_SHOW_DEFAULT == "next:10"
    doc = {"person": {"open_in": "none", "inbox": {"board_show": "7d"}}}
    assert settings.person(doc) == {"open_in": "none", "inbox": {"board_show": "7d"}}
    doc = {"person": {"open_in": "none", "inbox": {"board_show": "soon", "colour": 1}}}
    assert settings.person(doc) == {"open_in": "none", "inbox": {}}
    for bad in ({"inbox": {"board_show": "next:51"}}, {"inbox": {"horizon": "all"}}, {"inbox": "all"}):
        with pytest.raises(ValueError):
            settings.parse_person(bad)


@pytest.mark.integration
async def test_set_settings_writes_any_subset_and_the_settings_read_says_what_it_makes(agent, tmp_path):
    """§5 (TD-146): teams, repos and person through one RPC, each validated before anything is
    written; a field set to null cleared; a past stop time refused; `settings` a person's own read,
    naming a `ui.yml` left on disk as *migrate*."""
    from sessionorc import paths

    await park_ticks(agent)
    later = _iso(datetime.now(UTC).replace(microsecond=0) + timedelta(hours=2))
    async with LocalClient() as person:
        got = await person.call(
            "set_settings",
            teams={"ao-grind": {"until": later, "reserve": 10}},
            repos={"agentorc": {"promote": {"auto": False}}},
            person={"open_in": "none", "terminal": {"size": 14}},
        )
        assert got["teams"] == {"ao-grind": {"until": later, "reserve": 10}}
        assert got["repos"] == {"agentorc": {"promote": {"auto": False}}}
        assert got["person"] == {"open_in": "none", "terminal": {"size": 14}}
        # nothing is written when any part is refused
        with pytest.raises(AgentError, match="teams.ao-grind: .*whole percent"):
            await person.call("set_settings", teams={"ao-grind": {"reserve": 130}}, person={"open_in": "vscode"})
        assert settings.person(settings.load())["open_in"] == "none"
        with pytest.raises(AgentError, match="has passed"):
            await person.call("set_settings", teams={"ao-grind": {"until": "2020-01-01T00:00:00Z"}})
        with pytest.raises(AgentError, match="person: unknown key theme"):
            await person.call("set_settings", person={"theme": None})
        with pytest.raises(AgentError, match="person.terminal: unknown key sise"):
            await person.call("set_settings", person={"terminal": {"sise": None}})
        # the board's horizon (TD-220): laid over what is there field by field, refused outside its shapes
        got = await person.call("set_settings", person={"inbox": {"board_show": "07d"}})
        assert got["person"] == {"open_in": "none", "terminal": {"size": 14}, "inbox": {"board_show": "7d"}}
        for bad in ("next:0", "next:51", "0d", "soon"):
            with pytest.raises(AgentError, match="person: inbox.board_show is next:<n>"):
                await person.call("set_settings", person={"inbox": {"board_show": bad}})
        with pytest.raises(AgentError, match="person.inbox: unknown key horizon"):
            await person.call("set_settings", person={"inbox": {"horizon": None}})
        assert settings.load()["person"]["inbox"] == {"board_show": "7d"}  # written as parsed
        await person.call("set_settings", person={"inbox": {"board_show": None}})
        assert "inbox" not in settings.person(settings.load())
        with pytest.raises(AgentError, match="unknown key resrve"):
            await person.call("set_settings", teams={"ao-grind": {"resrve": None}})
        with pytest.raises(AgentError, match="needs reserves, teams, repos, person, usage or notify"):
            await person.call("set_settings")
        await person.call("set_settings", teams={"ao-grind": {"until": None}}, person={"terminal": {"size": None}})
        doc = settings.load()
        assert settings.teams(doc) == {"ao-grind": {"reserve": 10}} and settings.person(doc) == {"open_in": "none"}
        read = await person.call("settings")
        assert read["teams"] == {"ao-grind": {"reserve": 10}} and read["migrate"] == []
        (paths.home() / "ui.yml").write_text("open_in: none\n")
        assert "ui.yml is no longer read" in (await person.call("settings"))["migrate"][0]
        sid = await _worker(person, tmp_path)
        async with LocalClient(caller=sid) as itself:
            for method, kw in (("settings", {}), ("set_settings", {"teams": {"t": {"reserve": 1}}})):
                with pytest.raises(AgentError, match="a person's own"):
                    await itself.call(method, **kw)
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)


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


@pytest.mark.integration
async def test_a_teams_stop_time_reaches_its_live_members_and_clear_takes_it_back(agent, tmp_path):
    """§6 *Team stop time* (TD-146 slice 2): every live unattended session with the team's badge
    whose stop time is unset or later takes the team's instant on the tick, as `set_stop` gives
    one; a member's own earlier one is kept; a member started after it is stamped at create;
    **Clear** takes the team's instant back from the members that carry it, and only from those."""
    await park_ticks(agent)
    now = datetime.now(UTC).replace(microsecond=0)
    team_at, own_at = _iso(now + timedelta(hours=2)), _iso(now + timedelta(hours=1))
    async with LocalClient() as person:
        plain = await _worker(person, tmp_path, name="p")
        member = await _worker(person, tmp_path, name="m", team="ao-grind", run_until=_iso(now + timedelta(hours=5)))
        early = await _worker(person, tmp_path, name="e", team="ao-grind", run_until=own_at)
        agent.sessions[member].wrapup_sent_at = _iso(now)  # a wrap-up asked under the old time is spent
        await person.call("set_settings", teams={"ao-grind": {"until": team_at}})
        await agent._team_stop_times(now)
        assert agent.sessions[member].run_until == team_at and agent.sessions[member].wrapup_sent_at is None
        assert agent.sessions[early].run_until == own_at, "its own earlier stop time is kept"
        assert agent.sessions[plain].run_until is None
        later = await _worker(person, tmp_path, name="l", team="ao-grind")
        assert agent.sessions[later].run_until == team_at, "a start after the instant is set is stamped"
        await person.call("set_settings", teams={"ao-grind": {"until": None}})
        assert agent.sessions[member].run_until is None and agent.sessions[later].run_until is None
        assert agent.sessions[early].run_until == own_at, "Clear leaves a member's own earlier time"
        # a team started again after its stop time has passed is not stopped by it
        settings.save({"teams": {"ao-grind": {"until": _iso(now - timedelta(minutes=5))}}})
        again = await _worker(person, tmp_path, name="a", team="ao-grind")
        await agent._team_stop_times(now)
        assert agent.sessions[again].run_until is None
        for s in (plain, member, early, later, again):
            await person.call("kill", id=s)
            await person.call("remove", id=s)


@pytest.mark.integration
async def test_a_moved_team_stop_time_leaves_a_member_a_person_took_over(agent, tmp_path):
    """The techlead's read of #630: a member taken over with `ao mode` still carries the old
    instant, but a move is a policy's and skips it (§4.2); a Clear still takes the instant back."""
    await park_ticks(agent)
    now = datetime.now(UTC).replace(microsecond=0)
    first, moved = _iso(now + timedelta(hours=2)), _iso(now + timedelta(hours=3))
    async with LocalClient() as person:
        await person.call("set_settings", teams={"ao-grind": {"until": first}})
        sid = await _worker(person, tmp_path, name="m", team="ao-grind")
        assert agent.sessions[sid].run_until == first
        await person.call("set_mode", id=sid, unattended=False)
        await person.call("set_settings", teams={"ao-grind": {"until": moved}})
        assert agent.sessions[sid].run_until == first, "a move leaves a person's session alone"
        await person.call("set_settings", teams={"ao-grind": {"until": first}})  # back to what it carries
        await person.call("set_settings", teams={"ao-grind": {"until": None}})
        assert agent.sessions[sid].run_until is None, "a Clear takes the team's instant back all the same"
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)


@pytest.mark.unit
def test_a_window_past_its_reset_is_unknown_and_never_over_its_line():
    """TD-233, §6 *Usage gate*: *a window that is unknown pauses nothing*. Last week's 99%, held
    because the endpoint has refused since before the reset, is not this week's number."""
    past, ahead = _iso(NOW - timedelta(minutes=1)), _iso(NOW + timedelta(days=6))
    rows = settings.lines({"wk": 5}, [{"label": "wk", "pct": 99, "resets": past}], NOW)
    assert rows[0]["unknown"] == "reset" and rows[0]["pct"] == 99  # still shown as what was last read
    assert settings.crossed(rows) is None
    rows = settings.lines({"wk": 5}, [{"label": "wk", "pct": 99, "resets": ahead}], NOW)
    assert "unknown" not in rows[0] and settings.crossed(rows)["label"] == "wk"
    # another window still over its line is still crossed
    both = [{"label": "wk", "pct": 99, "resets": past}, {"label": "5h", "pct": 90, "resets": ahead}]
    assert settings.crossed(settings.lines({"wk": 5, "5h": 30}, both, NOW))["label"] == "5h"
    # no reset, or one that cannot be read, is not a past one
    for resets in (None, "soon"):
        rows = settings.lines({"wk": 5}, [{"label": "wk", "pct": 99, "resets": resets}], NOW)
        assert "unknown" not in rows[0] and settings.crossed(rows) is not None


@pytest.mark.integration
async def test_last_weeks_reading_pauses_nothing_and_a_pause_on_it_lifts(agent, tmp_path):
    """The night of 2026-09-29: the endpoint refused from 23:07Z, the week reset, and the gate
    paused every member twice on the old 99%."""
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = await _worker(person, tmp_path)
        now = datetime.now(UTC)
        resets = now + timedelta(minutes=30)
        agent._usage[""] = {"windows": [{"label": "wk", "pct": 99, "resets": _iso(resets)}], "reason": "ok"}
        await person.call("set_settings", profile="", reserves={"wk": 5})
        rec = agent.sessions[sid]
        await agent._enforce_usage_gate(now)
        assert rec.gated and rec.gated["pct"] == 99  # before the reset: a crossing
        assert agent._profile_gated("", now)
        # the reset passes with no new reading: the window is unknown, and the pause lifts
        later = resets + max(RESUME_MIN, timedelta(minutes=1))
        assert not agent._profile_gated("", later)
        await agent._enforce_usage_gate(later)
        assert rec.gated is None
        # and a fresh start on the same held reading is not paused
        await agent._enforce_usage_gate(later + timedelta(minutes=1))
        assert rec.gated is None
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)


@pytest.mark.integration
async def test_a_profile_with_no_copy_reads_its_accounts_reading_at_the_gate(agent, hookstub, tmp_path, monkeypatch):
    """TD-456: two profiles on one account, a reading over the line held under the live one. A start
    under the other (no live session, so no copy of its own, TD-073) reads the account's reading at
    the gate and in `ao gate`, with its age and source, rather than *no reading yet*, which is no gate."""
    await park_ticks(agent)
    monkeypatch.setattr(hookstub, "accounts", {"pa": "paul", "pg": "paul"})
    fetched = _iso(datetime.now(UTC).replace(microsecond=0))
    reading = {"windows": [{"label": "5h", "pct": 95, "resets": None}], "fetched": fetched, "source": "reported"}
    async with LocalClient() as person:
        s = await person.call("create", name="pa", dir=str(tmp_path), adapter="hookstub", profile="pa")
        agent._usage_acct["hookstub:paul"] = reading
        agent._usage["pa"] = {**reading, "account": "paul", "tool": "Stub"}
        await person.call("set_settings", profile="pg", reserves={"5h": 30})
        now = datetime.now(UTC)
        assert "pg" not in agent._usage
        assert agent._profile_over("pg", now)["label"] == "5h"
        assert agent._profile_gated("pg", now)
        got = (await person.call("gate"))["profiles"]["pg"]
        assert got["windows"][0]["pct"] == 95 and got["windows"][0]["line"] == 70
        assert got["fetched"] == fetched and got["source"] == "reported"
        # an account nobody reads is still no reading, and no gate
        monkeypatch.setattr(hookstub, "accounts", {"pa": "paul", "pg": "other"})
        assert not agent._profile_gated("pg", now)
        assert "fetched" not in (await person.call("gate"))["profiles"]["pg"]
        await person.call("kill", id=s["id"])
