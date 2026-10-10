"""The kill guard and the process rule (design §4.3 *A kill the guard refuses*, §4.8, TD-489/TD-496)."""

import json
from pathlib import Path

import pytest
from conftest import run_hook

import agentorc
from agentorc.adapters.claude_code.guard import REASON, refuse
from agentorc.adapters.claude_code.hook import refused_command, translate

# the 2026-10-09 loop that killed `systemd --user`, as it was run
THE_LOOP = (
    "for p in $(ps -eo pid,args | grep '[s]leep 90' | awk '{print $1}'); do pp=$(ps -o ppid= -p $p); kill $pp $p; done"
)

REFUSED = [
    "pkill -f x",
    "killall sleep",
    "kill -1",
    "kill -9 -1",
    "kill 1",
    "kill $PPID",
    "kill -TERM ${PPID}",
    "kill $(pgrep x)",
    "kill -9 $(ps -eo pid,args | grep '[s]leep' | awk '{print $1}')",
    "kill `ps -o pid= -C sleep`",
    'kill "$(pgrep -f worker)"',
    'kill -9 "$PPID"',
    "{ kill 1; }",
    THE_LOOP,
    "sleep 1; sudo pkill python",
    "true && /usr/bin/killall -9 tmux",
    "timeout 5 pkill x",
    "pp=$(ps -o ppid= -p 1234)\nkill $pp",
    "sudo -u bob pkill x",
    "timeout -s KILL 5 pkill x",
    "nice -n 5 killall x",
    "\\pkill x",
    "cat <<EOF\nnothing here\nEOF\npkill x",  # past the body, a command again
    "kill -- -1",  # the targets past `--` (TD-502)
    "kill $(sudo pgrep x)",  # a search run under sudo is still a search
    # each wrapper is seen through to the command it runs, and so is a wrapper's option that takes a value
    "env pkill x",
    "env -u X pkill x",
    "nohup killall x",
    "exec pkill x",
    "command pkill x",
    "builtin pkill x",
    "setsid pkill x",
    "time pkill x",
    "echo x | xargs pkill",
    "xargs -I {} pkill x",
]

ALLOWED = [
    "kill 12345",
    "kill $PID",
    "kill -9 $PID",
    "kill %1",
    "kill $(cat x.pid)",
    "kill -1 12345",  # SIGHUP to one pid
    "timeout 600 sleep 90",
    "echo 'pkill -f x'",
    'echo "never killall sleep or kill $PPID"',
    'kill "$PID"',
    'git commit -m "guard: pkill; killall x; kill -1"',
    "grep -rn pkill src/",
    "ps -o ppid= -p $$",  # reading a parent is not killing it
    "git commit -m 'kill the guard: pkill, killall, kill -1'",
    "pdm run test tests/test_kill_guard.py",
    # a here-document's body is text a command reads (this repo's own commit idiom), and so is a comment
    "cat <<'EOF'\npkill -f x\nEOF",
    "cat <<EOF\nkill -9 $PPID\nEOF",
    "cat <<-EOF\n\tkillall x\n\tEOF",
    "git commit -m \"$(cat <<'EOF'\nTD-489: the pkill guard\nkill -1\nEOF\n)\"",
    "kill 1234 # ppid note",
    "ls # pkill x",
    "grep -c x <<< 'pkill'",
    "tmux kill-session -t scratch",
]


@pytest.mark.parametrize("command", REFUSED)
def test_the_guard_refuses_each_listed_shape(command):
    assert refuse(command) == REASON


@pytest.mark.parametrize("command", ALLOWED)
def test_the_guard_passes_what_the_rule_allows(command):
    assert refuse(command) is None


def bash(command: str) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "u",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


def test_the_hook_prints_the_deny_and_still_reports_the_event(tmp_path, monkeypatch):
    """No host agent: the hook still refuses on its own, and the event is queued as any `PreToolUse`."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    cp = run_hook("ao-x-y", bash("pkill -f sleep"))
    assert cp.returncode == 0
    assert json.loads(cp.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": REASON,
        }
    }
    got = json.loads((tmp_path / "home" / "events" / "ao-x-y.jsonl").read_text().strip())
    assert got["state"] == "working" and got["event"] == "PreToolUse:Bash"


def test_an_allowed_command_prints_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    cp = run_hook("ao-x-y", bash("kill 12345"))
    assert cp.returncode == 0 and cp.stdout == ""
    assert (tmp_path / "home" / "events" / "ao-x-y.jsonl").exists()


def test_a_subagents_kill_is_refused_too(tmp_path, monkeypatch):
    """A subagent's tool says nothing about the state (TD-201), but its kill is no less a kill."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    cp = run_hook("ao-x-y", {**bash("killall sleep"), "agent_id": "a1"})
    assert json.loads(cp.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_another_tools_command_is_not_read(tmp_path, monkeypatch):
    """Only `Bash` runs a command: a `command` on any other tool's input is never the guard's."""
    other = {**bash("pkill -f sleep"), "tool_name": "Read"}
    assert refused_command(other) is None
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    cp = run_hook("ao-x-y", other)
    assert cp.returncode == 0 and cp.stdout == ""
    assert translate(other)["event"] == "PreToolUse:Read"


# The rule's words (design §4.8): the skill and every template brief that carries a never-list.
RULE = "**Signal only a pid you started and still hold**"
PACKAGE = Path(agentorc.__file__).parent
# the supplements and the entry template carry no never-list of their own
NO_NEVER_LIST = {"entry.md", "grinder.stage.md", "techlead.stage.md"}


def test_the_skill_carries_the_process_rule():
    assert RULE in (PACKAGE / "skill.md").read_text(encoding="utf-8")


def test_every_template_brief_carries_the_process_rule():
    briefs = sorted((PACKAGE / "briefs").glob("*.md"))
    assert {p.name for p in briefs} >= NO_NEVER_LIST
    for p in briefs:
        if p.name not in NO_NEVER_LIST:
            assert RULE in p.read_text(encoding="utf-8"), p.name
