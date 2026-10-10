"""The watch's one run (design §4.10 *When the home itself is down: the watch*, TD-497), driven by a
fake socket (answering or silent), a fake `systemctl` and a fake clock through `watch.json`."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from agentorc import watch

T0 = datetime(2026, 10, 9, 11, 31, tzinfo=UTC)
EVERY = watch.WATCH_EVERY


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


def drive(home, runs, secrets="samscrape/prd"):
    """Each run `(minutes after T0, answered, started)`; the lines each run sent, in order."""
    sent: list[list[str]] = []
    for minutes, answered, started in runs:
        got: list[str] = []
        watch.run_once(
            home=home,
            now=T0 + timedelta(minutes=minutes),
            answered=answered,
            started=started,
            host="kmaster",
            secrets=secrets,
            send=lambda line, s, log: got.append(line),
        )
        sent.append(got)
    return sent


def hhmm(minutes):
    return (T0 + timedelta(minutes=minutes)).astimezone().strftime("%H:%M")


def test_silence_is_told_at_ten_minutes_and_its_recovery_once(home):
    sent = drive(home, [(0, True, False), (5, False, False), (10, False, False), (15, False, False)])
    assert sent[:3] == [[], [], []], "five minutes silent is not yet an outage"
    assert sent[3] == [f"agentorc · the host agent on kmaster has not answered since {hhmm(5)}"]
    state = json.loads((home / "watch.json").read_text())
    assert state["told"] == ["silent"] and state["silent_since"] == (T0 + timedelta(minutes=5)).isoformat()
    later = drive(home, [(20, False, False), (265, False, False), (270, True, False), (275, True, False)])
    assert later[:2] == [[], []], "one message an outage"
    assert later[2] == ["agentorc · the host agent on kmaster answers again (silent 4h25m)"]
    assert later[3] == [], "one message a recovery"
    assert json.loads((home / "watch.json").read_text())["told"] == []


def test_a_stopped_manager_is_started_told_and_its_recovery_told(home):
    sent = drive(home, [(0, False, True), (5, True, False)])
    assert sent[0] == [
        f"agentorc · kmaster's user manager was stopped; started again at {hhmm(0)}, sessions kept running"
    ]
    assert sent[1] == ["agentorc · the host agent on kmaster answers again (silent 5 min)"]
    state = json.loads((home / "watch.json").read_text())
    assert state == {"silent_since": None, "manager_started_at": None, "told": [], "last_run": state["last_run"]}


def test_a_manager_started_and_answering_in_one_run_is_one_line(home):
    assert drive(home, [(0, True, True), (5, True, False)]) == [[watch.manager_line("kmaster", T0)], []]


def test_a_started_manager_then_ten_minutes_silent_is_both_lines_once(home):
    sent = drive(home, [(0, False, True), (5, False, False), (10, False, False), (15, False, False)])
    assert [len(s) for s in sent] == [1, 0, 1, 0]
    assert sent[2] == [watch.silent_line("kmaster", T0)]


def test_nothing_is_sent_with_the_switch_off_and_the_state_still_kept(home):
    sent = drive(home, [(0, False, True), (10, False, False), (15, True, False)], secrets=None)
    assert sent == [[], [], []]
    assert not (home / "watch.log").exists()
    assert json.loads((home / "watch.json").read_text())["told"] == []


def test_a_failed_send_is_logged_and_not_retried(home, monkeypatch):
    class Failed:
        returncode, stdout, stderr = 1, "", "Doppler Error: you must be logged in\n"

    calls = []
    monkeypatch.setattr(watch.subprocess, "run", lambda argv, **kw: calls.append((argv, kw["input"])) or Failed())
    for minutes in (0, 10, 15, 20):
        watch.run_once(
            home=home, now=T0 + timedelta(minutes=minutes), answered=False, started=False, host="kmaster", secrets="a/b"
        )
    assert len(calls) == 1, "the silent line once, and the failure not retried"
    argv, line = calls[0]
    assert argv[:6] == ["doppler", "run", "--project", "a", "--config", "b"] and argv[-2:] == ["-m", "sessionorc.notify"]
    assert line.startswith("agentorc · the host agent on kmaster has not answered")
    assert (home / "watch.log").read_text().endswith("send failed: Doppler Error: you must be logged in\n")


def test_a_broken_state_file_starts_afresh(home):
    home.mkdir()
    (home / "watch.json").write_text("{not json")
    assert drive(home, [(0, False, False)]) == [[]]
    assert json.loads((home / "watch.json").read_text())["silent_since"] == T0.isoformat()


def fake_systemctl(entered, began):
    def run(argv):
        assert argv[:2] == ["systemctl", "show"] and argv[-1] == "--value"
        return {"user@1000.service": entered, watch.SERVICE: began}[argv[2]] + "\n"

    return run


def test_the_manager_start_is_read_from_systemd_s_monotonic_stamps():
    """Started by this run: the manager became active after the watch service began, just now."""
    assert watch.manager_started(fake_systemctl("500000000", "499000000"), uid=1000, mono_now=510.0)
    assert not watch.manager_started(fake_systemctl("100000000", "499000000"), uid=1000, mono_now=510.0), (
        "a manager running since before the run"
    )
    assert not watch.manager_started(fake_systemctl("500000000", "499000000"), uid=1000, mono_now=900.0), (
        "a run by hand long after the timer's last activation claims no start"
    )
    assert not watch.manager_started(fake_systemctl("0", "499000000"), uid=1000, mono_now=510.0)
    assert not watch.manager_started(fake_systemctl("", ""), uid=1000, mono_now=510.0), "no systemd"


def test_ask_is_one_bounded_never_gated_read(monkeypatch):
    from sessionorc import client

    asked = []
    monkeypatch.setattr(client, "call_sync", lambda method, **kw: asked.append((method, kw)) or "pong")
    assert watch.ask() is True
    assert asked == [("ping", {"_timeout": watch.ASK_SECONDS})]

    def down(method, **kw):
        raise client.AgentUnavailable("host agent not reachable")

    monkeypatch.setattr(client, "call_sync", down)
    assert watch.ask() is False


def test_the_spans_read_as_a_person_says_them():
    assert watch._span(timedelta(minutes=260)) == "4h20m"
    assert watch._span(timedelta(minutes=15)) == "15 min"
    assert watch.WATCH_SILENCE == 2 * EVERY
