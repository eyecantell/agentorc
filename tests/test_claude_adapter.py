"""Claude Code adapter: hook translation, launch spec, profiles, locators, and the real hook script
talking to a live host agent."""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from conftest import run_hook, wait_for

from agentorc import profiles
from agentorc.adapters.claude_code import (
    CADENCE_HOOK_LINE,
    HOOK_EVENTS,
    ClaudeCodeAdapter,
    _pid_alive,
    hooks_settings,
    munge,
    parse_usage,
    pretrust,
    repo_wires_cadence,
    write_hooks_file,
)
from agentorc.adapters.claude_code.hook import translate
from sessionorc.adapters import short_model
from sessionorc.client import LocalClient

pytestmark = pytest.mark.integration  # the hook-script tests spawn processes; split later if wanted

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


def test_a_compaction_is_not_a_start():
    """TD-090: a compaction ends in SessionStart(source=compact); a manual `/compact` fires nothing
    after it, so reading it as `working` left an idle session `stalled?` for ever."""
    ev = {"hook_event_name": "SessionStart", "session_id": "u1", "source": "compact"}
    assert translate(ev) == {"adapter_id": "u1"}  # no state: it stays what it was
    assert translate({**ev, "model": "claude-opus-5"}) == {"adapter_id": "u1", "model": "claude-opus-5"}
    assert translate({"hook_event_name": "SessionStart", "source": "compact"}) is None
    for source in ("startup", "resume", "clear"):
        assert translate({**ev, "source": source})["state"] == "working"


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
    assert translate({"hook_event_name": "Notification", "notification_type": "idle_prompt", "message": "w"}) is None
    assert translate({"hook_event_name": "SubagentStart"}) == {"subagent_delta": 1}


def test_hooks_settings_shape():
    prof = profiles.Profile(name="p", permission_wait=120)
    h = hooks_settings(prof, "/x/agentorc-hook")["hooks"]
    assert set(h) >= {"PermissionRequest", "Stop", "Notification", "SessionEnd", "SubagentStop"}
    pr = h["PermissionRequest"][0]["hooks"][0]
    assert pr["command"] == "/x/agentorc-hook" and pr["timeout"] == 135
    assert h["Stop"][0]["hooks"][0]["timeout"] == 10
    assert len(h["SessionStart"][0]["hooks"]) == 1  # no cadence line unless asked
    hc = hooks_settings(prof, "/x/agentorc-hook", cadence_line=True)["hooks"]
    ss = hc["SessionStart"][0]["hooks"]
    assert [x["command"] for x in ss] == ["/x/agentorc-hook", CADENCE_HOOK_LINE] and ss[1]["timeout"] >= 130
    assert CADENCE_HOOK_LINE.startswith('f="$CLAUDE_PROJECT_DIR/scripts/cadence_hooks.sh"; if [ -x "$f" ]')
    assert len(hc["Stop"][0]["hooks"]) == 1  # only SessionStart gains it


def _settings(d: Path, commands: list[str]) -> None:
    (d / ".claude").mkdir(parents=True, exist_ok=True)
    hooks = [{"type": "command", "command": c, "timeout": 20} for c in commands]
    (d / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": hooks}]}}))


def test_repo_wires_cadence_and_layer_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    prof = profiles.Profile(name="p")
    bare = tmp_path / "bare"  # no settings at all: a plain directory, or a repo that is no consumer
    bare.mkdir()
    assert repo_wires_cadence(bare) is False
    runner = tmp_path / "runner"  # post-2026-09-11: the one line
    _settings(
        runner, ['f="$CLAUDE_PROJECT_DIR/scripts/cadence_hooks.sh"; if [ -x "$f" ]; then "$f" --session-start; fi']
    )
    assert repo_wires_cadence(runner) is True
    legacy = tmp_path / "legacy"  # the pre-migration per-hook block, recognised by its attention line
    _settings(
        legacy,
        [
            '"$CLAUDE_PROJECT_DIR/scripts/check_anchor.py" --hook',
            '"$CLAUDE_PROJECT_DIR/scripts/nudge_user_attention.py" --report --due-only',
        ],
    )
    assert repo_wires_cadence(legacy) is True
    other = tmp_path / "other"  # SessionStart hooks of its own, none of them dev-cadence's
    _settings(other, ["echo hi"])
    assert repo_wires_cadence(other) is False
    broken = tmp_path / "broken"
    (broken / ".claude").mkdir(parents=True)
    (broken / ".claude" / "settings.json").write_text("{not json")
    assert repo_wires_cadence(broken) is False
    (broken / ".claude" / "settings.json").write_text('{"hooks": {"SessionStart": "oops"}}')
    assert repo_wires_cadence(broken) is False

    # the layer file follows the choice, by name, so two launches into different dirs do not collide
    plain = write_hooks_file(prof, runner)
    plus = write_hooks_file(prof, bare)
    assert plain.name == "p.json" and plus.name == "p+cadence.json"
    assert len(json.loads(plain.read_text())["hooks"]["SessionStart"][0]["hooks"]) == 1
    assert json.loads(plus.read_text())["hooks"]["SessionStart"][0]["hooks"][1]["command"] == CADENCE_HOOK_LINE
    assert write_hooks_file(prof).name == "p.json"  # no cwd: the plain layer, as before


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


def test_translate_carries_the_model_when_the_payload_has_one():
    """TD-031: only SessionStart carries `model`, and not always; a `/model` mid-session reports
    itself as PostModelSwitch's `to_model`. Every other event says nothing about the model."""
    assert translate({"hook_event_name": "SessionStart", "session_id": "u1", "model": "claude-opus-5"}) == {
        "adapter_id": "u1",
        "model": "claude-opus-5",
        "state": "working",
        "pending": None,
    }
    assert "model" not in translate({"hook_event_name": "SessionStart", "session_id": "u1"})
    assert "model" not in translate({"hook_event_name": "Stop"})
    switch = translate({"hook_event_name": "PostModelSwitch", "from_model": "claude-opus-5", "to_model": "x-1"})
    assert switch == {"model": "x-1"} and "state" not in switch  # a switch is not a state change
    assert translate({"hook_event_name": "PostModelSwitch"}) is None  # nothing to report
    assert "PostModelSwitch" in HOOK_EVENTS  # or the switch never reaches the hook at all


def test_model_in_use_reads_the_last_top_level_assistant_turn(tmp_path, monkeypatch):
    """TD-031: the transcript entry's own `message.model`, never a grep, and never a sidechain
    entry — a subagent's model is not the session's."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "profiles.yml").write_text(
        f"default: t\nprofiles:\n  t: {{account: t, model: opus, config_dir: {tmp_path / 'cc'}}}\n"
    )
    ad = ClaudeCodeAdapter()
    assert ad.model_in_use("abc", tmp_path / "repo", "t") is None  # no transcript at all: not an error
    d = tmp_path / "cc" / "projects" / munge(tmp_path / "repo")
    d.mkdir(parents=True)
    entries = [
        {"type": "assistant", "message": {"model": "claude-opus-5"}},
        # an Agent call that *requests* a subagent model: a grep for "model" would read this one
        {"type": "user", "message": {"content": [{"tool_use": {"input": {"model": "sonnet"}}}]}},
        {"type": "assistant", "message": {"model": "claude-fable-5-1"}},
        {"type": "assistant", "isSidechain": True, "message": {"model": "claude-haiku-4-5"}},
        {"type": "system", "message": {"model": "<synthetic>"}},
    ]
    (d / "abc.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))
    assert ad.model_in_use("abc", tmp_path / "repo", "t") == "claude-fable-5-1"
    assert ad.model_in_use("abc", tmp_path / "repo") == "claude-fable-5-1"  # the default profile is this one
    assert ad.model_in_use("abc", tmp_path / "repo", "nope") is None  # an unknown profile, not an exception
    # a truncated first line (the tail is read from the end, not the start) is skipped, not fatal
    (d / "abc.jsonl").write_text('del": "fragment"}\n' + json.dumps(entries[0]) + "\n")
    assert ad.model_in_use("abc", tmp_path / "repo", "t") == "claude-opus-5"
    assert short_model("claude-code", "claude-fable-5-1") == "fable-5-1"
    assert short_model("shell", "claude-fable-5-1") == "claude-fable-5-1"  # an adapter with no opinion
    assert short_model("claude-code", None) == ""


def test_parse_usage_and_credentials(tmp_path):
    u = parse_usage(
        {"five_hour": {"utilization": 42.7, "resets_at": "2026-09-07T02:00:00Z"}, "seven_day": {"utilization": 9}}
    )
    # TD-073: the endpoint's two windows become two labelled entries of a list; nothing above the
    # adapter knows what `5h` and `week` are, and a tool with other windows says what it has instead.
    assert u and [(w.label, w.pct) for w in u.windows] == [("5h", 42), ("week", 9)]
    assert u.windows[0].resets.startswith("2026") and u.windows[1].resets is None
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


async def test_hook_script_end_to_end(agent, hookstub, tmp_path):
    async with LocalClient() as c:
        s = await c.call("create", name="h", dir=str(tmp_path), adapter=hookstub.name)
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

        async def needs_you():
            return (await c.call("get", id=sid))["state"] == "needs-you"

        assert await wait_for(needs_you)
        x = await c.call("get", id=sid)
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


def test_pid_alive_corroborates_procstart():
    me = os.getpid()
    stat = Path(f"/proc/{me}/stat").read_text()
    my_start = int(stat[stat.rindex(")") + 2 :].split()[19])
    assert _pid_alive(me, my_start) is True
    assert _pid_alive(me, str(my_start)) is True  # the registry writes it as a string
    assert _pid_alive(me, my_start + 1) is False  # same pid, a different process
    assert _pid_alive(me, None) is True  # entry predating the field: existence only
    assert _pid_alive(2**22 + 12345, my_start) is False  # no such pid


def test_external_sessions_filters_dead_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "profiles.yml").write_text(f"profiles:\n  t: {{config_dir: {tmp_path}}}\n")
    (tmp_path / "sessions").mkdir()
    me = os.getpid()
    stat = Path(f"/proc/{me}/stat").read_text()
    start = stat[stat.rindex(")") + 2 :].split()[19]
    (tmp_path / "sessions" / "a.json").write_text(
        json.dumps(
            {"pid": me, "procStart": start, "cwd": "/w/live", "name": "live", "sessionId": "s1", "status": "busy"}
        )
    )
    (tmp_path / "sessions" / "b.json").write_text(
        json.dumps({"pid": me, "procStart": str(int(start) + 7), "cwd": "/w/stale", "sessionId": "s2"})
    )
    (tmp_path / "sessions" / "c.json").write_text(json.dumps({"pid": 2**22 + 999, "cwd": "/w/gone", "sessionId": "s3"}))
    got = ClaudeCodeAdapter().external_sessions()
    assert [(e.cwd, e.name, e.status) for e in got] == [("/w/live", "live", "busy")]


def test_external_sessions_reads_every_profiles_registry(tmp_path, monkeypatch):
    """TD-013: a second account's registry lives under its own CLAUDE_CONFIG_DIR; the anchor rule
    must see a session started there. One read per distinct dir; other tools' profiles are skipped."""
    home, a, b = tmp_path / "home", tmp_path / "claude-a", tmp_path / "claude-b"
    for d in (home, a / "sessions", b / "sessions", tmp_path / "other" / "sessions"):
        d.mkdir(parents=True)
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    (home / "profiles.yml").write_text(
        f"default: a\nprofiles:\n  a: {{config_dir: {a}}}\n  a2: {{config_dir: {a}, model: sonnet}}\n"
        f"  b: {{config_dir: {b}}}\n  x: {{adapter: other-tool, config_dir: {tmp_path / 'other'}}}\n"
    )
    me = os.getpid()
    for d, name in ((a, "in-a"), (b, "in-b"), (tmp_path / "other", "not-ours")):
        (d / "sessions" / "s.json").write_text(json.dumps({"pid": me, "cwd": f"/w/{name}", "name": name}))
    got = ClaudeCodeAdapter().external_sessions()
    assert sorted(e.name for e in got) == ["in-a", "in-b"]  # once per dir, never the other tool's


def test_usage_for_by_profile_name_and_why_when_there_is_no_reading(tmp_path, monkeypatch):
    """TD-001: the core asks by profile name and gets a plain dict. TD-087: and when there is no
    reading it is told **why**, in a word it can key on — *rate-limited*, *no credentials*, *no
    profile* and *an error* were one silence, so the chip stayed empty, the journal said nothing,
    and the poll asked again a minute later against an endpoint answering 429."""
    import unittest.mock as um

    from agentorc.adapters.claude_code import Usage, UsageRefused, Window

    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))  # no profiles.yml: only `default` exists
    ad = ClaudeCodeAdapter()
    assert ad.usage_for("no-such-profile") == {"reason": "no_profile"}
    u = Usage(
        windows=[Window(label="5h", pct=42, resets="2026-09-10T04:00:00Z"), Window(label="wk", pct=7, resets=None)],
        fetched="x",
    )
    with um.patch.object(ClaudeCodeAdapter, "usage", return_value=u):
        assert ad.usage_for("") == {
            "windows": [
                {"label": "5h", "pct": 42, "resets": "2026-09-10T04:00:00Z"},
                {"label": "wk", "pct": 7, "resets": None},
            ],
            "fetched": "x",
            "reason": "ok",
        }
    with um.patch.object(ClaudeCodeAdapter, "usage", return_value=None):
        assert ad.usage_for("default") == {"reason": "error"}
    for refused, want in (
        (UsageRefused("rate_limited", 90.0), {"reason": "rate_limited", "retry_after": 90.0}),
        (UsageRefused("rate_limited"), {"reason": "rate_limited", "retry_after": None}),
        (UsageRefused("no_credentials"), {"reason": "no_credentials", "retry_after": None}),
    ):
        with um.patch.object(ClaudeCodeAdapter, "usage", side_effect=refused):
            assert ad.usage_for("default") == want


def test_a_429_is_read_as_rate_limited_with_its_retry_after(tmp_path, monkeypatch):
    """TD-087: the endpoint answers HTTP 429 `rate_limit_error` when the account's allowance is
    spent — by us, by the tool, by anything else on the same account. It is the one answer that
    means *ask less often*, so it is the one the caller must be able to tell from a network
    error; and its `Retry-After`, when it sends a number, is the endpoint's own word on when."""
    import unittest.mock as um
    import urllib.error

    from agentorc.adapters.claude_code import UsageRefused

    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    ad = ClaudeCodeAdapter()
    prof = profiles.get(None)
    with um.patch.object(ClaudeCodeAdapter, "_creds", return_value={"accessToken": "t"}):
        for err, reason, after in (
            (urllib.error.HTTPError("u", 429, "rate_limit_error", {"Retry-After": "120"}, None), "rate_limited", 120.0),
            (urllib.error.HTTPError("u", 429, "rate_limit_error", {}, None), "rate_limited", None),
            (urllib.error.HTTPError("u", 429, "x", {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, None), "rate_limited", None),  # noqa: E501
            (urllib.error.HTTPError("u", 500, "boom", {}, None), "error", None),
            (OSError("no route to host"), "error", None),
        ):
            with um.patch("urllib.request.urlopen", side_effect=err), pytest.raises(UsageRefused) as e:
                ad.usage(prof)
            assert (e.value.reason, e.value.retry_after) == (reason, after)
    # and no credentials never reaches the network at all
    with um.patch.object(ClaudeCodeAdapter, "_creds", return_value=None), pytest.raises(UsageRefused) as e:
        ad.usage(prof)
    assert e.value.reason == "no_credentials"


def test_composer_reads_painted_text_only():
    """TD-027: the composer is the last `❯` row; faint text (a suggested next prompt, the first-run
    placeholder) is not content; a slash command's colour is; no `❯` row means no opinion."""
    ad = ClaudeCodeAdapter()
    status = "  ⏸ manual mode on · ? for shortcuts"
    assert (
        ad.composer(["\x1b[39m❯ reply with the word tok015", "● tok015", "\x1b[39m❯\xa0real text", status])
        == "real text"
    )
    assert ad.composer(["\x1b[39m❯\xa0\x1b[2mreply with the word tok017\x1b[0m", status]) == ""
    assert ad.composer(['\x1b[39m❯\xa0\x1b[2mTry "fix typecheck errors"\x1b[0m', status]) == ""
    assert ad.composer(["\x1b[39m❯\xa0\x1b[38;5;153m/exit\x1b[39m", status]) == "/exit"
    assert ad.composer(["Do you trust the files in this folder?", "  Yes, proceed", "  No, exit"]) is None
    assert ad.composer([]) is None


def test_title_is_the_tools_own_name_for_the_session_or_nothing():
    """TD-074 step 3, design §4.3 `title()` / §4.5a **title**: the terminal title the tool set, with
    its own decoration off. What is left is a name — often the person's own, given with the tool's
    rename — unless there is nothing left, it is the tool's default, or it is a shell's
    `user@host: dir` on a pane the tool never titled. Display only: agentorc never sets it."""
    ad = ClaudeCodeAdapter()
    assert ad.title("✳ Error Checker") == "Error Checker"  # seen on a live pane 2026-09-19
    assert ad.title("  ✻  TD-074 steps 3 and 4  ") == "TD-074 steps 3 and 4"
    assert ad.title("⣷ Error Checker") == "Error Checker"  # a spinner frame is a glyph too
    assert ad.title("Error Checker") == "Error Checker"  # undecorated, and still a name
    # one mark and only before a space: a name a person chose may open with a character of its own
    assert ad.title("* priority fix") == "* priority fix" and ad.title("•bullet") == "•bullet"
    # not a name: nothing there, the tool's own default, a shell's
    assert ad.title("") is None and ad.title("   ") is None and ad.title("✳") is None
    assert ad.title("Claude Code") is None and ad.title("✳ claude") is None
    assert ad.title("kmaster@dev: ~/agentorc") is None
    # a name that merely contains an @ is still a name
    assert ad.title("mail @ the inbox") == "mail @ the inbox"
