"""`ao` against a live agent in another process. Plain `def` tests: `cli.main` -> `call_sync` ->
`asyncio.run`, which needs an agent whose loop runs on its own (tests/README.md rule 4)."""

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import run_hook, wait_for_sync

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
    raise AssertionError(f"{sid} never reached {state}: {s['state']} {s.get('tail')}")


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
    assert "no pending permission" in capsys.readouterr().err

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
