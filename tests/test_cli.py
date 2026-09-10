"""`ao` against a live agent in another process. Plain `def` tests: `cli.main` -> `call_sync` ->
`asyncio.run`, which needs an agent whose loop runs on its own (tests/README.md rule 4)."""

import json
import pathlib
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import pane_line, run_hook, wait_for_sync

from agentorc import cli
from sessionorc.client import call_sync

pytestmark = pytest.mark.integration


def wait_state(sid: str, state: str, timeout: float = 6.0) -> dict:
    s = call_sync("get", id=sid)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if s["state"] == state:
            return s
        time.sleep(0.1)
        s = call_sync("get", id=sid)
    raise AssertionError(f"{sid} never reached {state}: {s['state']} {s.get('tail')} {pane_line(sid)}")


def test_status_empty_and_json(subprocess_agent, capsys):
    assert cli.main(["status"]) == 0
    assert capsys.readouterr().out.strip() == "no sessions"
    assert cli.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_shell_send_tail_status_kill_close(subprocess_agent, tmp_path, capsys):
    assert cli.main(["shell", "cli sh", "-d", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    sid = out.split()[0]
    assert sid.startswith("ao-") and f"attach: tmux attach -t {sid}" in out
    wait_state(sid, "idle")

    assert cli.main(["send", sid, "echo", "CLI-$((20+1))"]) == 0
    assert wait_for_sync(lambda: any("CLI-21" in line for line in call_sync("tail", id=sid, lines=10)))
    assert cli.main(["tail", sid, "-n", "10"]) == 0
    assert "CLI-21" in capsys.readouterr().out

    # `status -v` shows the record's tail, which the tick refreshes: wait for it (flaked on CI 2026-09-10)
    assert wait_for_sync(lambda: any("CLI-21" in line for line in call_sync("get", id=sid)["tail"]))
    assert cli.main(["status", "-v"]) == 0
    out = capsys.readouterr().out
    assert sid in out and "shell" in out and "│" in out  # the verbose tail rows
    assert cli.main(["status", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in rows] == [sid] and rows[0]["state"] == "idle"

    assert cli.main(["mode", sid, "unattended"]) == 0
    assert capsys.readouterr().out.strip() == f"{sid}: unattended"
    assert call_sync("get", id=sid)["unattended"] is True
    assert cli.main(["mode", sid, "interactive"]) == 0
    assert capsys.readouterr().out.strip() == f"{sid}: interactive"

    assert cli.main(["kill", sid]) == 0
    assert capsys.readouterr().out.strip() == f"killed {sid}"
    wait_state(sid, "exited")

    assert cli.main(["shell", "-d", str(tmp_path)]) == 0  # name defaults to "shell"
    sid2 = capsys.readouterr().out.split()[0]
    wait_state(sid2, "idle")
    assert cli.main(["close", sid2]) == 0
    assert capsys.readouterr().out.strip() == f"closed {sid2}"
    assert call_sync("get", id=sid2)["state"] == "closed"
    call_sync("remove", id=sid)
    call_sync("remove", id=sid2)


def test_keys_reach_the_pane(subprocess_agent, tmp_path, capsys):
    assert cli.main(["shell", "keys", "-d", str(tmp_path)]) == 0
    sid = capsys.readouterr().out.split()[0]
    wait_state(sid, "idle")
    assert cli.main(["keys", sid, "e", "c", "h", "o", "Space", "K", "E", "Y", "S", "Enter"]) == 0
    assert wait_for_sync(lambda: any(line.strip() == "KEYS" for line in call_sync("tail", id=sid, lines=10)))
    call_sync("kill", id=sid)


def test_allow_and_deny(subprocess_agent, tmp_path, capsys):
    # a hook-fed adapter (the child registers `hookstub`): a shell's scraped state would race the hook
    assert cli.main(["new", "perm", "-a", "hookstub", "-d", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    sid = out.split()[0]
    assert "(hookstub, " in out
    call_sync("hook", session=sid, state="idle")
    wait_state(sid, "idle")

    assert cli.main(["allow", sid]) == 1
    assert capsys.readouterr().err == f"{sid} has no pending permission\n"  # exact: the pre-TD-018 line

    payload = {
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {"command": "git push"},
        "tool_use_id": "tu-cli",
    }

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(run_hook, sid, payload, "10")
        wait_state(sid, "needs-you")
        assert cli.main(["allow", sid, "looks fine"]) == 0
        assert capsys.readouterr().out.strip() == "allow: Bash: git push"
        cp = fut.result(timeout=15)
    assert json.loads(cp.stdout)["hookSpecificOutput"]["decision"] == {"behavior": "allow", "reason": "looks fine"}
    wait_state(sid, "working")

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(run_hook, sid, {**payload, "tool_use_id": "tu-cli-2"}, "10")
        wait_state(sid, "needs-you")
        assert cli.main(["deny", sid]) == 0
        cp = fut.result(timeout=15)
    assert json.loads(cp.stdout)["hookSpecificOutput"]["decision"]["behavior"] == "deny"
    call_sync("kill", id=sid)


def test_errors_map_to_exit_codes(subprocess_agent, tmp_path, monkeypatch, capsys):
    assert cli.main(["kill", "ao-does-not-exist"]) == 1
    assert "error:" in capsys.readouterr().err
    assert cli.main(["new", "x", "-d", str(tmp_path / "missing")]) == 1
    assert "not a directory" in capsys.readouterr().err
    # no agent behind this home: exit 3 with the hint
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "empty-home"))
    assert cli.main(["status"]) == 3
    err = capsys.readouterr().err
    assert "not reachable" in err and "agentorc-agent serve" in err


def test_json_on_every_subcommand(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-018: `--json` (global or after the subcommand) prints the RPC result and nothing else on
    stdout; errors are `{"error": …}` with the same exit codes."""

    def out():
        return json.loads(capsys.readouterr().out)

    assert cli.main(["--json", "shell", "js", "-d", str(tmp_path)]) == 0
    s = out()
    sid = s["id"]
    assert s["adapter"] == "shell" and s["dir"] == str(tmp_path)
    wait_state(sid, "idle")
    assert cli.main(["send", sid, "--json", "echo", "J-$((40+2))"]) == 0  # flag after the subcommand
    assert out() == {"ok": True, "id": sid}
    assert wait_for_sync(lambda: any("J-42" in line for line in call_sync("tail", id=sid, lines=10)))
    assert cli.main(["--json", "tail", sid, "-n", "5"]) == 0
    lines = out()
    assert isinstance(lines, list) and any("J-42" in line for line in lines)
    assert cli.main(["--json", "keys", sid, "Enter"]) == 0
    assert out() == {"ok": True, "id": sid}
    assert cli.main(["--json", "mode", sid, "unattended"]) == 0
    assert out()["unattended"] is True
    assert cli.main(["--json", "status"]) == 0
    assert sid in [r["id"] for r in out()]  # the module agent holds other tests' sessions too
    assert cli.main(["--json", "kill", sid]) == 0
    assert out()["state"] == "exited"
    assert cli.main(["--json", "close", sid]) == 0
    assert out()["state"] == "closed"
    call_sync("remove", id=sid)

    # allow / deny: the hook-fed stub, a real hook process blocking on the permission
    assert cli.main(["--json", "new", "jperm", "-a", "hookstub", "-d", str(tmp_path)]) == 0
    pid = out()["id"]
    call_sync("hook", session=pid, state="idle")
    wait_state(pid, "idle")
    assert cli.main(["--json", "allow", pid]) == 1
    assert out() == {"error": f"{pid} has no pending permission"}
    payload = {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(run_hook, pid, {**payload, "tool_use_id": "tu-json"}, "10")
        wait_state(pid, "needs-you")
        assert cli.main(["--json", "deny", pid, "nope"]) == 0
        assert out() == {"ok": True, "id": pid, "behavior": "deny", "tool_use_id": "tu-json"}
        cp = fut.result(timeout=15)
    assert json.loads(cp.stdout)["hookSpecificOutput"]["decision"] == {"behavior": "deny", "reason": "nope"}
    call_sync("kill", id=pid)

    # errors keep their exit codes, on stdout as JSON
    assert cli.main(["--json", "kill", "ao-nope"]) == 1
    assert out() == {"error": "no session ao-nope"}
    assert capsys.readouterr().err == ""
    monkeypatch.setattr("agentorc.service.status", lambda: "agentorc-agent: active")
    assert cli.main(["--json", "service", "status"]) == 0
    assert out() == {"status": "agentorc-agent: active"}
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "empty-home"))
    assert cli.main(["--json", "status"]) == 3
    e = out()
    assert "not reachable" in e["error"] and "agentorc-agent serve" in e["hint"]


def test_send_wait(subprocess_agent, tmp_path, capsys):
    """`ao send --wait` prints the settled state; the errors map to exit 1 like any RPC error."""
    assert cli.main(["new", "sw", "-a", "hookstub", "-d", str(tmp_path)]) == 0
    sid = capsys.readouterr().out.split()[0]
    call_sync("hook", session=sid, state="idle")
    wait_state(sid, "idle")

    def turn():
        time.sleep(0.3)
        call_sync("hook", session=sid, state="working")
        time.sleep(0.3)
        call_sync("hook", session=sid, state="idle")

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(turn)
        assert cli.main(["send", sid, "--wait", "--timeout", "5", "go"]) == 0
        fut.result(timeout=5)
    assert capsys.readouterr().out.strip() == f"{sid}: idle"
    assert cli.main(["send", sid, "--wait", "--timeout", "1", "nothing happens"]) == 1
    assert "prompt-stalled" in capsys.readouterr().err
    call_sync("kill", id=sid)


def test_focus_and_attach_print_the_attach_argv_under_json(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-010 (b): `ao focus` / `--attach` exec `tmux attach`; under --json the argv is printed instead
    (an exec cannot be tested here). The socket comes from the environment, as the pty bridge's does."""
    assert cli.main(["--json", "shell", "att", "-d", str(tmp_path), "--attach"]) == 0
    out = json.loads(capsys.readouterr().out)
    sid = out["id"]
    assert out["attach"][:6] == ["tmux", "-L", subprocess_agent.sock_name, "attach", "-t", f"={sid}:"]
    assert out["attach"][6:] == [";", "set-option", "-t", f"={sid}:", "mouse", "on"]  # TD-022
    wait_state(sid, "idle")
    assert cli.main(["--json", "focus", sid]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["id"] == sid and out["state"] == "idle" and out["attach"][5] == f"={sid}:"
    assert cli.main(["focus", "ao-nope"]) == 1
    assert "no session ao-nope" in capsys.readouterr().err
    # a real focus runs tmux attach as a child: patch it (no tty here) and check the argv
    seen = {}
    monkeypatch.setattr("agentorc.cli.subprocess.call", lambda argv: seen.update(argv=argv) or 0)
    assert cli.main(["focus", sid]) == 0
    assert seen["argv"][0] == "tmux" and seen["argv"][3:6] == ["attach", "-t", f"={sid}:"]
    # killed: `exited` like a natural exit, but the record says the pane is gone (TD-023) — agentorc's
    # own line, exit 1, and tmux is never run
    assert call_sync("kill", id=sid)["pane"] is False
    wait_state(sid, "exited")
    monkeypatch.setattr("agentorc.cli.subprocess.call", lambda argv: pytest.fail(f"tmux was run: {argv}"))
    assert cli.main(["focus", sid]) == 1
    assert "pane is gone" in capsys.readouterr().err
    monkeypatch.undo()
    call_sync("close", id=sid)
    assert cli.main(["focus", sid]) == 1
    assert "closed" in capsys.readouterr().err
    call_sync("remove", id=sid)


def test_explain_file_and_session(subprocess_agent, tmp_path, capsys):
    """TD-015: `ao explain --file` classifies a saved screen with the adapter's rules; `ao explain <id>`
    shows a live session's screen and reason; both take --json."""
    fixture = str(pathlib.Path(__file__).parent / "fixtures" / "screens" / "usage-limit-reached.txt")
    assert cli.main(["explain", "--file", fixture]) == 0
    out = capsys.readouterr().out
    assert "rule: usage-limit → limited" in out and "evidence │" in out and "usage limit reached" in out.lower()
    assert cli.main(["--json", "explain", "--file", fixture]) == 0
    x = json.loads(capsys.readouterr().out)
    assert x["match"]["rule"] == "usage-limit" and x["match"]["pending"]["kind"] == "limit"
    assert cli.main(["explain", "--file", fixture, "-a", "shell"]) == 1
    assert "no screen rules" in capsys.readouterr().err
    assert cli.main(["explain"]) == 2
    assert cli.main(["explain", "--file", str(tmp_path / "nope.txt")]) == 1
    assert "cannot read" in capsys.readouterr().err
    capsys.readouterr()
    assert cli.main(["shell", "ex", "-d", str(tmp_path)]) == 0
    sid = capsys.readouterr().out.split()[0]
    wait_state(sid, "idle")
    assert cli.main(["explain", sid]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"{sid}  idle (scraped)") and "why: shell has no screen rules" in out and "screen:" in out
    call_sync("kill", id=sid)


def test_skill_prints_the_rules(capsys):
    """TD-019: `ao --skill` prints the packaged skill and exits 0 without an agent."""
    with pytest.raises(SystemExit) as e:
        cli.main(["--skill"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("---\nname: ao\n")
    for must in ("AGENTORC_SESSION", "ao status --json", "prompt-stuck", "invariant 1", "never"):
        assert must in out.lower() or must in out, must
    assert out.count("\n") <= 120
