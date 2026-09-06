"""Claude Code adapter: hook translation, launch spec, profiles, locators, and the real hook script
talking to a live host agent."""

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from agentorc import profiles
from agentorc.adapters.claude_code import ClaudeCodeAdapter, hooks_settings, munge, parse_usage, pretrust
from agentorc.adapters.claude_code.hook import translate
from sessionorc import paths
from sessionorc.agent import HostAgent
from sessionorc.client import LocalClient
from sessionorc.tmux import Tmux

# -- pure -------------------------------------------------------------------------------------


def test_translate_state_events():
    assert translate({"hook_event_name": "SessionStart", "session_id": "u1"}) == {
        "adapter_id": "u1",
        "state": "working",
        "pending": None,
    }
    assert translate({"hook_event_name": "Stop"})["state"] == "idle"
    assert translate({"hook_event_name": "SessionEnd", "reason": "prompt_input_exit"})["state"] == "exited"
    assert translate({"hook_event_name": "SessionEnd", "reason": "clear"}) is None  # process still alive
    assert translate({"hook_event_name": "SessionEnd", "reason": "resume"}) is None
    assert translate({"hook_event_name": "PreCompact"}) is None


def test_translate_permission_and_questions():
    p = translate(
        {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin td-301"},
            "tool_use_id": "tu9",
        }
    )
    assert p == {"kind": "permission", "text": "Bash: git push origin td-301", "tool_use_id": "tu9"}
    q = translate(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{"question": "Which db?"}]},
        }
    )
    assert q["state"] == "needs-you" and q["pending"] == {"kind": "question", "text": "Which db?"}
    n = translate({"hook_event_name": "Notification", "notification_type": "permission_prompt", "message": "m"})
    assert n["pending"]["kind"] == "question"  # dialog is in the terminal now
    assert translate({"hook_event_name": "Notification", "notification_type": "auth_success"}) is None
    assert translate({"hook_event_name": "SubagentStart"}) == {"subagent_delta": 1}


def test_hooks_settings_shape():
    prof = profiles.Profile(name="p", permission_wait=120)
    h = hooks_settings(prof, "/x/agentorc-hook")["hooks"]
    assert set(h) >= {"PermissionRequest", "Stop", "Notification", "SessionEnd", "SubagentStop"}
    pr = h["PermissionRequest"][0]["hooks"][0]
    assert pr["command"] == "/x/agentorc-hook" and pr["timeout"] == 135
    assert h["Stop"][0]["hooks"][0]["timeout"] == 10


def test_launch_argv_and_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "profiles.yml").write_text(
        "default: paul\nprofiles:\n  paul: {account: paul, model: opus, config_dir: ~/.claude}\n"
        "  grind: {account: grind, model: sonnet, config_dir: /tmp/cc-grind, permission_wait: 30}\n"
    )
    ad = ClaudeCodeAdapter(binary="claude")
    spec = ad.launch(profile="", resume=None, prompt="do it", unattended=False, cwd=tmp_path, name="t1")
    assert spec.argv[0] == "claude" and "--settings" in spec.argv and "--session-id" in spec.argv
    assert spec.adapter_id and uuid.UUID(spec.adapter_id)
    assert spec.argv[-1] == "do it" and "--model" in spec.argv and "--name" in spec.argv
    assert "CLAUDE_CONFIG_DIR" not in spec.env or spec.env["CLAUDE_CONFIG_DIR"].endswith(".claude")
    assert spec.env["AGENTORC_PERMISSION_WAIT"] == "600"
    hooks_path = Path(spec.argv[spec.argv.index("--settings") + 1])
    assert json.loads(hooks_path.read_text())["hooks"]["Stop"]

    g = ad.launch(profile="grind", resume="abc-123", prompt=None, unattended=True, cwd=tmp_path)
    assert g.adapter_id == "abc-123" and "--resume" in g.argv and "--session-id" not in g.argv
    assert "--dangerously-skip-permissions" in g.argv
    assert g.env["CLAUDE_CONFIG_DIR"] == "/tmp/cc-grind" and g.env["AGENTORC_PERMISSION_WAIT"] == "30"
    dashed = ad.launch(profile="", resume=None, prompt="-1 is the answer", unattended=False, cwd=tmp_path)
    assert dashed.argv[-2:] == ["--", "-1 is the answer"]
    with pytest.raises(KeyError, match="unknown profile"):
        ad.launch(profile="nope", resume=None, prompt=None, unattended=False, cwd=tmp_path)


def test_pretrust_writes_once_and_keeps_other_state(tmp_path):
    prof = profiles.Profile(name="t", config_dir=tmp_path)
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"oauthAccount": {"x": 1}, "projects": {"/other": {"hasTrustDialogAccepted": True}}}))
    assert pretrust(tmp_path / "repo", prof) is True
    d = json.loads(cfg.read_text())
    assert d["oauthAccount"] == {"x": 1} and d["projects"]["/other"]["hasTrustDialogAccepted"] is True
    assert d["projects"][str(tmp_path / "repo")]["hasTrustDialogAccepted"] is True
    assert pretrust(tmp_path / "repo", prof) is False  # already trusted: no write
    assert oct(cfg.stat().st_mode & 0o777) == "0o600"


def test_profiles_default_when_missing(tmp_path):
    p, d = profiles.load(tmp_path / "none.yml")
    assert d == "default" and p["default"].config_dir is None
    bad = tmp_path / "bad.yml"
    bad.write_text("default: x\nprofiles:\n  y: {}\n")
    with pytest.raises(ValueError, match="not a declared profile"):
        profiles.load(bad)


def test_munge_and_transcript_path(tmp_path):
    assert munge("/home/p/work_history") == "-home-p-work-history"
    prof = profiles.Profile(name="t", config_dir=tmp_path)
    d = tmp_path / "projects" / munge(tmp_path / "repo")
    d.mkdir(parents=True)
    (d / "abc.jsonl").write_text("{}\n")
    ad = ClaudeCodeAdapter()
    assert ad.transcript_path("abc", tmp_path / "repo", prof) == d / "abc.jsonl"
    assert ad.transcript_path("zzz", tmp_path / "repo", prof) is None


def test_parse_usage_and_credentials(tmp_path):
    u = parse_usage(
        {"five_hour": {"utilization": 42.7, "resets_at": "2026-09-07T02:00:00Z"}, "seven_day": {"utilization": 9}}
    )
    assert u and u.five_hour_pct == 42 and u.weekly_pct == 9 and u.five_hour_resets.startswith("2026")
    assert parse_usage({}) is None
    prof = profiles.Profile(name="t", config_dir=tmp_path)
    ad = ClaudeCodeAdapter()
    assert ad.credentials_ok(prof) is None
    (tmp_path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "x", "expiresAt": 1}}))
    assert ad.credentials_ok(prof) is False
    (tmp_path / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "x", "refreshTokenExpiresAt": 4102444800000}})
    )
    assert ad.credentials_ok(prof) is True


# -- the real hook script against a live agent ---------------------------------------------------


@pytest.fixture
async def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("sessionorc.agent.TICK_SECONDS", 0.3)
    tmux = Tmux(socket_name=f"ao-test-{uuid.uuid4().hex[:8]}")
    a = HostAgent(tmux=tmux)
    task = asyncio.create_task(a.serve(paths.socket_path()))
    for _ in range(50):
        if paths.socket_path().exists():
            break
        await asyncio.sleep(0.05)
    yield a
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    tmux.kill_server()


def run_hook(session: str, payload: dict, wait: str = "5") -> subprocess.CompletedProcess:
    env = {**os.environ, "AGENTORC_SESSION": session, "AGENTORC_PERMISSION_WAIT": wait}
    return subprocess.run(
        [sys.executable, "-m", "agentorc.adapters.claude_code.hook"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


async def test_hook_script_end_to_end(agent, tmp_path):
    async with LocalClient() as c:
        s = await c.call("create", name="h", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        sid = s["id"]
        # Stop → idle, carrying the Claude session id
        cp = await asyncio.to_thread(run_hook, sid, {"hook_event_name": "Stop", "session_id": "cc-uuid"})
        assert cp.returncode == 0 and cp.stdout == ""
        x = await c.call("get", id=sid)
        assert x["adapter_id"] == "cc-uuid"
        # PermissionRequest blocks; Allow from the UI prints the decision JSON
        payload = {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "git push"},
            "tool_use_id": "tu-e2e",
        }
        fut = asyncio.get_running_loop().run_in_executor(None, run_hook, sid, payload)
        for _ in range(50):
            x = await c.call("get", id=sid)
            if x["state"] == "needs-you":
                break
            await asyncio.sleep(0.1)
        assert x["pending"]["kind"] == "permission" and x["pending"]["text"] == "Bash: git push"
        await c.call("decide", id=sid, tool_use_id="tu-e2e", behavior="deny", reason="not yet")
        cp = await fut
        out = json.loads(cp.stdout)
        assert out["hookSpecificOutput"]["decision"] == {"behavior": "deny", "reason": "not yet"}
        # timeout → no output, session pending becomes a terminal question
        cp = await asyncio.to_thread(run_hook, sid, {**payload, "tool_use_id": "tu-late"}, "0.3")
        assert cp.returncode == 0 and cp.stdout == ""
        x = await c.call("get", id=sid)
        assert x["pending"]["kind"] == "question"


def test_hook_script_without_session_is_noop():
    env = {k: v for k, v in os.environ.items() if k != "AGENTORC_SESSION"}
    cp = subprocess.run(
        [sys.executable, "-m", "agentorc.adapters.claude_code.hook"],
        input='{"hook_event_name": "Stop"}',
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert cp.returncode == 0 and cp.stdout == ""


def test_hook_script_queues_when_agent_down(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    cp = run_hook("ao-x-y", {"hook_event_name": "Stop", "session_id": "u"})
    assert cp.returncode == 0
    q = (tmp_path / "home" / "events" / "ao-x-y.jsonl").read_text().strip()
    assert json.loads(q) == {"adapter_id": "u", "state": "idle", "pending": None}
