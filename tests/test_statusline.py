"""The status line's report (design §4.4 *A report*, *The person's own status line still shows*;
§4.3 `usage_report`; TD-233 slice 2, the adapter's half): `agentorc-hook --statusline` reads the
limits Claude Code hands its status line, reports them as `usage_report` when a number moved or a
minute passed, and runs the status line the launch's layer displaced."""

from __future__ import annotations

import io
import json
import socket
import threading
from pathlib import Path

import pytest

from agentorc import profiles
from agentorc.adapters.claude_code import (
    context_file,
    displaced_status_line,
    hooks_settings,
    usage_report,
    write_hooks_file,
)
from agentorc.adapters.claude_code import hook as hook_mod

R5 = 1790000000  # 2026-09-21T14:13:20Z
RW = 1790500000


def payload(five: float | None = 61.54, week: float | None = 30.2, work: int = 1200, sid: str = "u1") -> dict:
    limits = {}
    if five is not None:
        limits["five_hour"] = {"used_percentage": five, "resets_at": R5}
    if week is not None:
        limits["seven_day"] = {"used_percentage": week, "resets_at": RW}
    p = {"session_id": sid, "cost": {"total_api_duration_ms": work, "total_cost_usd": 0.4}}
    if limits:
        p["rate_limits"] = limits
    return p


def test_usage_report_reads_the_two_windows_by_the_endpoints_labels():
    got = usage_report(payload())
    assert got == {
        "windows": [
            {"label": "5h", "pct": 61.5, "resets": "2026-09-21T14:13:20Z"},
            {"label": "week", "pct": 30.2, "resets": "2026-09-27T09:06:40Z"},
        ],
        "work": 1200,
        "sid": "u1",
    }
    assert [w["label"] for w in usage_report(payload(week=None))["windows"]] == ["5h"]  # a window may be absent
    # no `rate_limits` (an old client, a metered profile, before the first response): nothing
    assert usage_report(payload(five=None, week=None)) is None
    assert usage_report({"rate_limits": {"spend_limit": {"used_percentage": 3, "resets_at": R5}}}) is None
    assert usage_report({"rate_limits": {"five_hour": {"used_percentage": "61"}}}) is None
    odd = usage_report({"rate_limits": {"five_hour": {"used_percentage": 7, "resets_at": "soon"}}})
    assert odd == {"windows": [{"label": "5h", "pct": 7.0, "resets": None}], "work": None, "sid": None}


def test_the_layer_names_the_status_line_with_a_minutes_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    prof = profiles.Profile(name="p", config_dir=tmp_path / "cfg")
    sl = hooks_settings(prof, "/x y/agentorc-hook")["statusLine"]
    assert sl == {"type": "command", "command": "'/x y/agentorc-hook' --statusline", "refreshInterval": 60}
    assert hooks_settings(prof, padding=2)["statusLine"]["padding"] == 2
    # the displaced line's padding is carried into the layer, in a file of its own
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": "x", "padding": 3}})
    )
    f = write_hooks_file(prof, proj)
    assert f.name == "p+cadence+pad3.json" and json.loads(f.read_text())["statusLine"]["padding"] == 3
    plain = tmp_path / "plain"
    plain.mkdir()
    assert "padding" not in json.loads(write_hooks_file(prof, plain).read_text())["statusLine"]


def _line(d: Path, name: str, command: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps({"statusLine": {"type": "command", "command": command}}))


def test_the_displaced_status_line_is_the_directorys_before_the_profiles(tmp_path):
    proj, cfg = tmp_path / "proj", tmp_path / "cfg"
    proj.mkdir()
    assert displaced_status_line(proj, cfg) is None
    _line(cfg, "settings.json", "profile-line")
    assert displaced_status_line(proj, cfg)["command"] == "profile-line"
    _line(proj / ".claude", "settings.json", "project-line")
    assert displaced_status_line(proj, cfg)["command"] == "project-line"  # the project's, though the profile names one
    _line(proj / ".claude", "settings.local.json", "local-line")
    assert displaced_status_line(proj, cfg)["command"] == "local-line"
    # ours is never the displaced one — it would run itself — and an unreadable file names none
    _line(proj / ".claude", "settings.local.json", "/bin/agentorc-hook --statusline")
    (proj / ".claude" / "settings.json").write_text("{not json")
    assert displaced_status_line(proj, cfg)["command"] == "profile-line"


def test_report_due_on_a_change_or_a_minute_and_fresh_on_new_work():
    rep = usage_report(payload())
    first = hook_mod.report_due(rep, None, now=1000.0)
    assert first == {"windows": rep["windows"], "fresh": True}
    last = {**rep, "sent": 1000.0}
    assert hook_mod.report_due(rep, last, now=1030.0) is None  # a redraw inside the minute: nothing
    again = hook_mod.report_due(rep, last, now=1061.0)
    assert again == {"windows": rep["windows"], "fresh": False}  # the minute passed, but no response since
    moved = usage_report(payload(five=62.0, work=1500))
    assert hook_mod.report_due(moved, last, now=1010.0)["fresh"] is True
    # a new process under the same name counts its API time from nothing again
    restarted = usage_report(payload(work=300, sid="u2"))
    assert hook_mod.report_due(restarted, {**last, "sent": 0.0}, now=1070.0)["fresh"] is True
    # and so does the same tool session resumed in a new process: its running total started over
    resumed = usage_report(payload(work=300))
    assert hook_mod.report_due(resumed, {**last, "sent": 0.0}, now=1070.0)["fresh"] is True


class FakeAgent:
    """A socket that answers every request with `{taken: true}` and keeps what it was sent."""

    def __init__(self, path: Path):
        self.got: list[dict] = []
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        self.sock.listen(8)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                req = json.loads(buf)
                self.got.append(req)
                conn.sendall((json.dumps({"id": req["id"], "result": {"taken": True}}) + "\n").encode())


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = Path("/tmp") / f"aosl-{tmp_path.name[-12:]}"  # a unix socket path must stay short
    h.mkdir(exist_ok=True)
    monkeypatch.setenv("AGENTORC_HOME", str(h))
    monkeypatch.setenv("AGENTORC_SESSION", "ao-x-w1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    yield h
    for f in sorted(h.rglob("*"), reverse=True):
        f.unlink() if not f.is_dir() else f.rmdir()
    h.rmdir()


def run_statusline(monkeypatch, capsys, p: dict | str, now: float) -> str:
    monkeypatch.setattr(hook_mod.sys, "stdin", io.StringIO(p if isinstance(p, str) else json.dumps(p)))
    monkeypatch.setattr(hook_mod.sys, "argv", ["agentorc-hook", "--statusline"])
    monkeypatch.setattr(hook_mod.time, "time", lambda: now)
    assert hook_mod.main() == 0
    return capsys.readouterr().out


def test_the_command_reports_once_for_ten_redraws_and_prints_the_persons_line(home, tmp_path, monkeypatch, capsys):
    agent = FakeAgent(home / "agent.sock")
    proj = tmp_path / "proj"
    _line(proj / ".claude", "settings.json", "cat > seen.json; echo 'main · 61%'")
    p = {**payload(), "workspace": {"project_dir": str(proj), "current_dir": str(proj)}}
    outs = [run_statusline(monkeypatch, capsys, p, now=1000.0 + i * 5) for i in range(10)]
    assert outs == ["main · 61%\n"] * 10  # the person's own line, every redraw
    assert json.loads((proj / "seen.json").read_text())["session_id"] == "u1"  # handed the same stdin
    assert len(agent.got) == 1
    req = agent.got[0]
    assert req["method"] == "usage_report" and req["caller"] == "ao-x-w1"
    assert req["params"] == {"windows": usage_report(p)["windows"], "fresh": True}
    run_statusline(monkeypatch, capsys, {**p, "cost": {"total_api_duration_ms": 1900}}, now=1070.0)
    assert len(agent.got) == 2 and agent.got[1]["params"]["fresh"] is True


def test_no_limits_and_no_agent_break_nothing(home, tmp_path, monkeypatch, capsys):
    proj = tmp_path / "proj"
    _line(proj / ".claude", "settings.json", "echo mine")
    ws = {"workspace": {"project_dir": str(proj)}}
    # no host agent listening: the report is lost, the line still prints, and nothing is remembered
    assert run_statusline(monkeypatch, capsys, {**payload(), **ws}, now=1000.0) == "mine\n"
    assert not hook_mod.last_report_file("ao-x-w1").exists()
    agent = FakeAgent(home / "agent.sock")
    assert run_statusline(monkeypatch, capsys, {**payload(five=None, week=None), **ws}, now=1001.0) == "mine\n"
    assert agent.got == []  # a payload with no `rate_limits` reports nothing
    assert run_statusline(monkeypatch, capsys, "not json", now=1002.0) == ""  # no directory known, no line of ours
    # with no line displaced, it prints nothing at all
    plain = tmp_path / "plain"
    plain.mkdir()
    assert (
        run_statusline(monkeypatch, capsys, {**payload(), "workspace": {"project_dir": str(plain)}}, now=1003.0) == ""
    )
    assert len(agent.got) == 1


def test_the_command_keeps_the_reported_context_window_once_per_change(home, tmp_path, monkeypatch, capsys):
    """TD-295: the window and tokens the tool hands its status line are kept for the adapter's
    `context`, keyed by the tool's session id and stamped by the redraw that first saw them."""
    plain = tmp_path / "plain"
    plain.mkdir()
    use = {"input_tokens": 5, "cache_read_input_tokens": 100_000, "cache_creation_input_tokens": 0}
    cw = {"context_window_size": 200_000, "current_usage": use}
    p = {**payload(), "workspace": {"project_dir": str(plain)}, "context_window": cw}
    run_statusline(monkeypatch, capsys, p, now=R5)
    kept = json.loads(context_file("u1").read_text())
    assert kept == {"window": 200_000, "tokens": 100_005, "at": "2026-09-21T14:13:20.000Z"}
    run_statusline(monkeypatch, capsys, p, now=R5 + 30)  # a redraw: the first sighting's time stands
    assert json.loads(context_file("u1").read_text())["at"] == "2026-09-21T14:13:20.000Z"
    p["context_window"] = {**cw, "current_usage": {**use, "input_tokens": 9}}
    run_statusline(monkeypatch, capsys, p, now=R5 + 60)
    assert json.loads(context_file("u1").read_text())["tokens"] == 100_009
