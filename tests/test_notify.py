"""Told on Telegram when nobody is looking (design §4.10, TD-319 slice 1): the setting, the lines, and
the home's pass — told once after the hold, not when answered inside it, not when snoozed, the burst's
*and more*, a failed send kept and never a row, and no token anywhere. The child is replaced by a
recorder: no test sends anything."""

import asyncio
import io
import json
import sys
from datetime import UTC, datetime, timedelta

import pytest

from sessionorc import notify
from sessionorc import settings as settings_mod
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import MailEntry, Pending, Session

pytestmark = pytest.mark.integration

TOKEN = "bot123456:AAsecretsecretsecret"


def _z(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _on(link: str = "http://kmaster:8765") -> None:
    settings_mod.save({"notify": {"telegram": {"on": True, "secrets": "samscrape/prd", "link": link}}})


@pytest.fixture
def sent(agent, monkeypatch):
    """Every send the home makes, as `(secrets, text)`, in place of the Doppler child."""
    got: list[tuple[str, str]] = []

    async def record(secrets: str, text: str) -> str | None:
        got.append((secrets, text))
        return None

    monkeypatch.setattr(agent, "_notify_run", record)
    return got


def _session(agent, tmp_path, sid="ao-x-w", name="grinder-x-1", team="x-grind", **kw) -> Session:
    s = Session(id=sid, name=name, kind="agent", adapter="shell", dir=str(tmp_path), team=team)
    for k, v in kw.items():
        setattr(s, k, v)
    agent.sessions[s.id] = s
    return s


async def _pass(agent, now: datetime) -> None:
    agent._note_attention(now)
    agent._notify_pass(now)
    for _ in range(20):  # the send is a detached task
        if not agent._bg:
            break
        await asyncio.sleep(0.01)


# -- the setting ---------------------------------------------------------------------------------


def test_the_setting_takes_a_switch_a_doppler_name_and_a_link():
    got = settings_mod.parse_telegram({"on": True, "secrets": " samscrape/prd ", "link": "http://kmaster:8765/"})
    assert got == {"on": True, "secrets": "samscrape/prd", "link": "http://kmaster:8765"}
    for bad in ({"on": "yes"}, {"secrets": "TELEGRAM_BOT_TOKEN=123"}, {"link": "kmaster:8765"}, {"chat": 1}):
        with pytest.raises(ValueError):
            settings_mod.parse_telegram(bad)
    # the reader drops what a hand edit made invalid, field by field, and the tick reads a switch only
    doc = {"notify": {"telegram": {"on": True, "secrets": "nope", "link": "http://k"}}}
    assert settings_mod.notify(doc) == {"telegram": {"on": True, "link": "http://k"}}
    assert settings_mod.telegram(doc) is None, "on with no secrets sends nothing"
    assert settings_mod.telegram({}) is None
    doc["notify"]["telegram"]["secrets"] = "p/c"
    assert settings_mod.telegram(doc) == {"secrets": "p/c", "link": "http://k"}


async def test_set_settings_writes_notify_and_refuses_on_without_secrets(agent):
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="notify.telegram.secrets"):
            await person.call("set_settings", notify={"telegram": {"on": True}})
        out = await person.call("set_settings", notify={"telegram": {"secrets": "samscrape/prd"}})
        assert out["notify"] == {"telegram": {"secrets": "samscrape/prd"}}
        out = await person.call("set_settings", notify={"telegram": {"on": True, "link": "https://k.example/"}})
        assert out["notify"] == {"telegram": {"on": True, "secrets": "samscrape/prd", "link": "https://k.example"}}
        with pytest.raises(AgentError, match="notify.telegram.secrets"):
            await person.call("set_settings", notify={"telegram": {"secrets": None}})  # it is on
        with pytest.raises(AgentError, match="unknown key"):
            await person.call("set_settings", notify={"telegram": {"token": "x"}})
        assert (await person.call("settings"))["notify"]["telegram"]["on"] is True
        await person.call("set_settings", notify={"telegram": None})
        assert (await person.call("settings"))["notify"] == {}
        assert "notify" not in settings_mod.load()


# -- the lines -------------------------------------------------------------------------------------


def test_a_line_is_structured_fields_and_a_link_never_what_a_session_wrote():
    assert notify.state_line("grinder-ao-1", "ao-grind", "permission") == (
        "agentorc · grinder-ao-1 (ao-grind) needs you: permission"
    )
    assert notify.state_line("w", "", "needs") == "agentorc · w needs you"
    assert notify.ask_line("grinder-ao-1", "ao-grind", "TD-229") == (
        "agentorc · grinder-ao-1 (ao-grind) asks you a question · TD-229"
    )
    assert notify.alarm_line("w", "t") == "agentorc · identity alarm on w (t)"
    assert notify.row_link("http://k:1", "ao-x-w|permission") == "http://k:1/inbox?row=ao-x-w%7Cpermission"
    assert notify.mail_link("http://k:1", "m-ab12") == "http://k:1/inbox/m-ab12"
    assert notify.row_link("", "k") == "" and notify.message("x", "") == "x"
    assert notify.argv("samscrape/prd")[:6] == ["doppler", "run", "--project", "samscrape", "--config", "prd"]
    assert notify.argv("samscrape/prd")[-2:] == ["-m", "sessionorc.notify"]
    assert notify.reason(f"x\nPOST https://api.telegram.org/{TOKEN}/sendMessage failed\n") == (
        "POST https://api.telegram.org/bot…/sendMessage failed"
    )


def test_the_child_sends_one_request_and_never_prints_the_url(monkeypatch, capsys):
    calls = []

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        calls.append((req.full_url, json.loads(req.data), timeout))
        return Resp()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN[3:])
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(sys, "stdin", io.StringIO("agentorc · w needs you\nhttp://k/inbox\n"))
    assert notify.main() == 0
    assert calls[0][1] == {
        "chat_id": "42",
        "text": "agentorc · w needs you\nhttp://k/inbox",
        "disable_web_page_preview": True,
    }

    def refused(req, timeout):
        raise notify.urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(notify.urllib.request, "urlopen", refused)
    monkeypatch.setattr(sys, "stdin", io.StringIO("x"))
    assert notify.main() == 1

    def broken(req, timeout):
        raise OSError(f"could not reach {req.full_url}")

    monkeypatch.setattr(notify.urllib.request, "urlopen", broken)
    monkeypatch.setattr(sys, "stdin", io.StringIO("x"))
    assert notify.main() == 1
    err = capsys.readouterr().err
    assert "HTTP 401" in err and "OSError" in err
    assert TOKEN[3:] not in err and "api.telegram.org" not in err
    monkeypatch.delenv("TELEGRAM_CHAT_ID")
    monkeypatch.setattr(sys, "stdin", io.StringIO("x"))
    assert notify.main() == 1


# -- the home's pass -----------------------------------------------------------------------------


async def test_a_row_is_told_once_after_the_hold_and_again_only_as_a_new_row(agent, sent, tmp_path):
    _on()
    t0 = datetime.now(UTC)
    s = _session(agent, tmp_path, state="needs-you", since=_z(t0))
    s.pending = Pending(kind="permission", text="rm -rf /secret/path", tool_use_id="t1")
    try:
        await _pass(agent, t0 + timedelta(seconds=30))
        assert sent == [], "inside the hold"
        await _pass(agent, t0 + timedelta(seconds=61))
        assert sent == [
            (
                "samscrape/prd",
                "agentorc · grinder-x-1 (x-grind) needs you: permission\n"
                "http://kmaster:8765/inbox?row=ao-x-w%7Cpermission",
            )
        ]
        assert "/secret/path" not in sent[0][1], "never what a session wrote"
        await _pass(agent, t0 + timedelta(seconds=90))
        assert len(sent) == 1, "one message per row"
        assert json.loads(agent.attention_store.path.read_text())["notified"].keys() == {"ao-x-w|permission"}
        # the row ends: its key leaves; a row that begins again is a new row
        s.state, s.pending = "idle", None
        await _pass(agent, t0 + timedelta(seconds=100))
        assert agent.attention_store.notified == {}
        t1 = t0 + timedelta(seconds=200)
        s.state, s.since, s.pending = "needs-you", _z(t1), Pending(kind="question", text="which?")
        await _pass(agent, t1 + timedelta(seconds=61))
        assert [t.split("\n")[0] for _, t in sent][1] == "agentorc · grinder-x-1 (x-grind) needs you: question"
        assert (await agent.rpc_host())["notify"]["last_ok"]
    finally:
        agent.sessions.pop(s.id, None)


async def test_not_told_when_answered_inside_the_hold_snoozed_off_or_a_backlog(agent, sent, tmp_path):
    t0 = datetime.now(UTC)
    s = _session(agent, tmp_path, state="needs-you", since=_z(t0), pending=Pending(kind="question", text="q"))
    try:
        await _pass(agent, t0 + timedelta(seconds=61))
        assert sent == [], "the switch is off: nothing"
        _on()
        # answered inside the hold
        await _pass(agent, t0 + timedelta(seconds=20))
        s.state, s.pending = "idle", None
        await _pass(agent, t0 + timedelta(seconds=61))
        assert sent == []
        # snoozed by the person
        t1 = t0 + timedelta(seconds=100)
        s.state, s.since, s.pending = "needs-you", _z(t1), Pending(kind="question", text="q")
        agent.attention_snoozed["ao-x-w|question"] = _z(t1 + timedelta(hours=1))
        await _pass(agent, t1 + timedelta(seconds=61))
        assert sent == []
        agent.attention_snoozed.clear()
        # standing since before the switch was on, or the home was down: a backlog is not told
        s.state, s.pending = "idle", None
        await _pass(agent, t1 + timedelta(seconds=62))
        old = t1 - timedelta(hours=2)
        s.state, s.since, s.pending = "needs-you", _z(old), Pending(kind="question", text="q")
        await _pass(agent, t1 + timedelta(seconds=70))
        assert sent == []
        # and on a node nothing is sent at all
        agent.mode = "node"
        t2 = t1 + timedelta(seconds=200)
        s.state, s.since = "needs-you", _z(t2)
        await _pass(agent, t2 + timedelta(seconds=61))
        assert sent == []
    finally:
        agent.mode = "home"
        agent.sessions.pop(s.id, None)
        agent.attention_snoozed.clear()


async def test_an_ask_and_an_alarm_are_told_and_a_steer_is_not(agent, sent, tmp_path):
    _on(link="")
    t0 = datetime.now(UTC)
    s = _session(agent, tmp_path, state="idle", since=_z(t0))
    ask = MailEntry(id="m-a1", from_=s.id, to=["person"], at=_z(t0), kind="ask", text="secret words", about="TD-229")
    steer = MailEntry(id="m-s1", from_=s.id, to=["person"], at=_z(t0), kind="steer", text="x", default="go")
    agent.person_inbox.extend([ask, steer])
    s.identity_alarms = [{"at": _z(t0), "kind": "x"}]
    try:
        await _pass(agent, t0 + timedelta(seconds=61))
        texts = sorted(t for _, t in sent)
        assert "agentorc · grinder-x-1 (x-grind) asks you a question · TD-229" in texts
        assert "secret words" not in "".join(texts)
        assert len(texts) == 2 and any("identity alarm on grinder-x-1" in t for t in texts), texts
    finally:
        agent.person_inbox[:] = [e for e in agent.person_inbox if e.id not in ("m-a1", "m-s1")]
        agent.sessions.pop(s.id, None)


async def test_the_seventh_in_ten_minutes_is_and_more_and_the_eighth_nothing(agent, sent, tmp_path):
    _on()
    t0 = datetime.now(UTC)
    ids = [f"ao-x-w{i}" for i in range(8)]
    try:
        for i, sid in enumerate(ids):
            _session(
                agent,
                tmp_path,
                sid=sid,
                name=f"w{i}",
                state="needs-you",
                since=_z(t0),
                pending=Pending(kind="question", text="q"),
            )
        await _pass(agent, t0 + timedelta(seconds=61))
        lines = [t.split("\n")[0] for _, t in sent]
        assert len(lines) == 7 and lines[-1] == notify.MORE
        await _pass(agent, t0 + timedelta(seconds=90))
        assert len(sent) == 7, "held back rows are not told afterwards"
    finally:
        for sid in ids:
            agent.sessions.pop(sid, None)


async def test_a_failed_send_is_kept_on_the_host_read_and_is_no_row(agent, tmp_path, monkeypatch):
    _on()

    async def fail(secrets: str, text: str) -> str | None:
        return "doppler: not logged in"

    monkeypatch.setattr(agent, "_notify_run", fail)
    t0 = datetime.now(UTC)
    s = _session(agent, tmp_path, state="needs-you", since=_z(t0), pending=Pending(kind="question", text="q"))
    try:
        await _pass(agent, t0 + timedelta(seconds=61))
        got = (await agent.rpc_host())["notify"]
        assert got["last_error"]["reason"] == "doppler: not logged in" and "last_ok" not in got
        await _pass(agent, t0 + timedelta(seconds=90))
        assert len(agent.trail) == 0, "a failure is not a row"
    finally:
        agent.sessions.pop(s.id, None)


async def test_the_real_child_without_doppler_says_so(agent, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    assert await agent._notify_run("p/c", "x") == "doppler: not installed"


async def test_a_child_past_its_time_is_killed_with_its_group(agent, monkeypatch, tmp_path):
    """The child runs in a group of its own, so a timeout ends Doppler's child as well (review of #1086)."""
    script = tmp_path / "doppler"
    script.write_text("#!/bin/sh\nsleep 30 &\nwait\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setattr(notify, "CHILD_SECONDS", 0.3)
    assert await agent._notify_run("p/c", "x") == "no answer in 0.3 s"


# -- slice 2: the other rows, the watching signal, Send a test -------------------------------------


async def test_a_blocked_outcome_a_restart_row_and_a_team_with_work_are_told(agent, sent, tmp_path):
    _on(link="")
    doc = settings_mod.load()
    doc["teams"] = {t: {"on_work": "ask"} for t in ("cm-grind", "held-grind", "run-grind")}  # `start` is the default
    settings_mod.save(doc)
    t0 = datetime.now(UTC)
    s = _session(agent, tmp_path, state="exited", since=_z(t0))
    s.restart_ceiling = {"at": _z(t0), "count": 3}
    q = MailEntry(
        id="m-q1", from_=s.id, to=["person"], at=_z(t0 - timedelta(hours=1)), kind="ask", text="w", about="TD-142"
    )
    q.closed_at, q.closed_reason = _z(t0), "replied"
    q.outcome = {"state": "blocked", "text": "the box is down", "at": _z(t0), "by": s.id}
    agent.person_inbox.append(q)
    teams = agent._host_rec.setdefault("teams", {})
    teams["cm-grind"] = {
        "work_waiting": {"at": _z(t0), "repo": "/r", "members": {"a": ["TD-1", "TD-2"], "b": ["TD-2", "TD-3"]}}
    }
    teams["held-grind"] = {"work_waiting": {"at": _z(t0), "members": {"a": ["TD-9"]}, "held": {"why": "gate"}}}
    # a team that runs on names its finished member (§6 rule 8, TD-466): a live seat, a closed grinder
    _session(agent, tmp_path, sid="ao-run-seat", name="anchor-run", team="run-grind", state="idle", unattended=True)
    _session(agent, tmp_path, sid="ao-run-g", name="grinder-run-1", team="run-grind", state="closed", unattended=True)
    # a person's own session in a wound-down team keeps nothing live (§4.9 *A person in the team*): still *wound down*
    _session(agent, tmp_path, sid="ao-cm-person", name="ao-paul", team="cm-grind", state="idle")
    teams["run-grind"] = {"work_waiting": {"at": _z(t0), "repo": "/r", "members": {"grinder-run-1": ["TD-7"]}}}
    try:
        await _pass(agent, t0 + timedelta(seconds=61))
        texts = sorted(t for _, t in sent)
        assert texts == [
            "agentorc · cm-grind wound down and has work: 3 entries",
            "agentorc · grinder-x-1 (x-grind) reports blocked · TD-142",
            "agentorc · run-grind's grinder-run-1 finished and has work: 1 entry",
            "agentorc · x-grind: grinder-x-1 was not restarted",
        ], texts
        assert "the box is down" not in "".join(texts)
    finally:
        agent.person_inbox[:] = [e for e in agent.person_inbox if e.id != "m-q1"]
        teams.pop("cm-grind", None)
        teams.pop("held-grind", None)
        teams.pop("run-grind", None)
        agent.sessions.pop(s.id, None)
        agent.sessions.pop("ao-run-seat", None)
        agent.sessions.pop("ao-run-g", None)
        agent.sessions.pop("ao-cm-person", None)


async def test_a_team_with_no_on_work_key_is_told_as_start(agent, sent):
    # an absent `on_work` means `start` (design §5, §6 rule 8; TD-457, TD-466): the home's own start, so
    # nobody is told, for a team with other settings as for one with none; `ask` is told
    settings_mod.save(
        {
            "notify": {"telegram": {"on": True, "secrets": "samscrape/prd", "link": ""}},
            "teams": {
                "cm-grind": {"reserve": 20},
                "ask-grind": {"reserve": 20, "on_work": "ask"},
                "off-grind": {"reserve": 20, "on_work": "off"},
            },
        }
    )
    t0 = datetime.now(UTC)
    teams = agent._host_rec.setdefault("teams", {})
    for team in ("cm-grind", "ask-grind", "off-grind", "bare-grind"):
        teams[team] = {"work_waiting": {"at": _z(t0), "repo": "/r", "members": {"a": ["TD-1"]}}}
    try:
        await _pass(agent, t0 + timedelta(seconds=61))
        assert [t for _, t in sent] == ["agentorc · ask-grind wound down and has work: 1 entry"], sent
    finally:
        for team in ("cm-grind", "ask-grind", "off-grind", "bare-grind"):
            teams.pop(team, None)


async def test_a_row_whose_hold_ends_while_a_page_is_visible_is_not_told_then_or_later(agent, sent, tmp_path):
    _on()
    t0 = datetime.now(UTC)
    s = _session(agent, tmp_path, state="needs-you", since=_z(t0), pending=Pending(kind="question", text="q"))
    try:
        async with LocalClient() as person:
            await person.call("inbox", watching=True)  # the page's poll, visible
        agent._notify_watched_at = t0 + timedelta(seconds=10)
        await _pass(agent, t0 + timedelta(seconds=61))
        await _pass(agent, t0 + timedelta(minutes=4))
        assert sent == []
        # once nobody looks for longer than NOTIFY_WATCHED, a new row is told
        s.state, s.pending = "idle", None
        await _pass(agent, t0 + timedelta(minutes=5))
        t1 = t0 + timedelta(minutes=6)
        s.state, s.since, s.pending = "needs-you", _z(t1), Pending(kind="question", text="q")
        await _pass(agent, t1 + timedelta(seconds=61))
        assert len(sent) == 1
    finally:
        agent._notify_watched_at = None
        agent.sessions.pop(s.id, None)


async def test_after_and_more_nothing_until_ten_minutes_pass(agent, sent, tmp_path):
    _on()
    t0 = datetime.now(UTC)
    ids = []
    try:
        for i in range(7):
            sid = f"ao-x-a{i}"
            ids.append(sid)
            _session(
                agent,
                tmp_path,
                sid=sid,
                name=f"a{i}",
                state="needs-you",
                since=_z(t0),
                pending=Pending(kind="question", text="q"),
            )
        await _pass(agent, t0 + timedelta(seconds=61))
        assert [t.split("\n")[0] for _, t in sent][-1] == notify.MORE and len(sent) == 7
        # nine minutes on, the first six have not all left the window: still nothing
        t1 = t0 + timedelta(minutes=9)
        _session(
            agent,
            tmp_path,
            sid="ao-x-b",
            name="b",
            state="needs-you",
            since=_z(t1),
            pending=Pending(kind="question", text="q"),
        )
        ids.append("ao-x-b")
        await _pass(agent, t1 + timedelta(seconds=61))
        assert len(sent) == 7
        t2 = t0 + timedelta(minutes=12)
        _session(
            agent,
            tmp_path,
            sid="ao-x-c",
            name="c",
            state="needs-you",
            since=_z(t2),
            pending=Pending(kind="question", text="q"),
        )
        ids.append("ao-x-c")
        await _pass(agent, t2 + timedelta(seconds=61))
        assert sent[-1][1].startswith("agentorc · c (x-grind) needs you: question") and len(sent) == 8
    finally:
        for sid in ids:
            agent.sessions.pop(sid, None)
        agent._notify_quiet_until = None
        agent._notify_sent.clear()


async def test_send_a_test_is_the_persons_and_sends_whatever_on_says(agent, sent, tmp_path):
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="notify.telegram.secrets is not set"):
            await person.call("notify_test")
        settings_mod.save({"notify": {"telegram": {"on": False, "secrets": "samscrape/prd", "link": "http://k:1"}}})
        got = await person.call("notify_test")
        assert got["sent"] is True and got["result"].startswith("sent at")
        assert sent[-1][0] == "samscrape/prd" and sent[-1][1].startswith("agentorc · a test from ")
        assert sent[-1][1].endswith("\nhttp://k:1/inbox")
        assert (await agent.rpc_host())["notify"]["last_ok"] == got["at"]
        sid = (
            await person.call(
                "create", name="w", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc", "--noprofile"]
            )
        )["id"]
        try:
            async with LocalClient(caller=sid) as itself:
                with pytest.raises(AgentError, match="a person's own"):
                    await itself.call("notify_test")
                await itself.call("inbox", watching=True)  # a session's read never says it is watching
            assert agent._notify_watched_at is None
        finally:
            await person.call("kill", id=sid)
            await person.call("remove", id=sid)


def test_notify_test_is_a_persons_and_the_homes():
    from sessionorc import mail, modes

    assert "notify_test" in mail.PERSON_ONLY and "notify_test" in modes.HOME_EDITS


async def test_a_dismissed_restart_row_and_a_handed_entrys_blocked_outcome(agent, sent, tmp_path):
    """Review of #1092: the restart row's Dismiss (`dismissed:<mark at>`) is that row gone, as the page
    reads it; a handed entry's `blocked` outcome sits on the holder's copy and is counted, so it is told."""
    _on(link="")
    t0 = datetime.now(UTC)
    s = _session(agent, tmp_path, state="exited", since=_z(t0))
    s.restart_ceiling = {"at": _z(t0), "count": 3}
    agent.attention_snoozed[f"{s.id}|restart"] = f"dismissed:{_z(t0)}"
    h = _session(agent, tmp_path, sid="ao-x-seat", name="seat-x", state="idle", since=_z(t0))
    e = MailEntry(
        id="m-h1", from_="person", to=[h.id], at=_z(t0 - timedelta(hours=1)), kind="ask", text="w", about="TD-077"
    )
    e.handed = True
    e.outcome = {"state": "blocked", "text": "no access", "at": _z(t0), "by": h.id}
    h.inbox.append(e)
    try:
        await _pass(agent, t0 + timedelta(seconds=61))
        assert [t for _, t in sent] == ["agentorc · seat-x (x-grind) reports blocked · TD-077"]
    finally:
        agent.attention_snoozed.clear()
        agent.sessions.pop(s.id, None)
        agent.sessions.pop(h.id, None)
