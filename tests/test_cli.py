"""`ao` against a live agent in another process. Plain `def` tests: `cli.main` -> `call_sync` ->
`asyncio.run`, which needs an agent whose loop runs on its own (tests/README.md rule 4)."""

import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import pane_line, run_hook, wait_for_sync, wait_screen

from agentorc import cli
from sessionorc.client import AgentError, call_sync

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
    # the send above is on the record with who typed it (design §4.10 `sends`), and `-v` prints it
    sends = call_sync("get", id=sid)["sends"]
    assert [(e["from"], e["text"]) for e in sends] == [("person", "echo CLI-$((20+1))")]
    assert f"send {sends[0]['id']} from person" in out and "unread" not in out
    call_sync("msg", to=sid, text="a person's note")  # lands; the unread count shows, the body never
    assert cli.main(["status", "-v"]) == 0
    out = capsys.readouterr().out
    assert "mail:   1 unread" in out and "a person's note" not in out
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
    assert cli.main(["shell", "-d", str(tmp_path), "live"]) == 0
    sid3 = capsys.readouterr().out.split()[0]
    wait_state(sid3, "idle")
    assert cli.main(["forget", sid3]) != 0  # a live record is refused: "kill it first"
    assert "kill it first" in capsys.readouterr().err
    assert cli.main(["forget", sid2]) == 0
    assert capsys.readouterr().out.strip() == f"forgot {sid2}"
    assert all(s["id"] != sid2 for s in call_sync("list"))
    assert cli.main(["kill", sid3]) == 0
    wait_state(sid3, "exited")
    call_sync("remove", id=sid)
    call_sync("remove", id=sid3)  # sid2 is already forgotten


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
    flipped = out()
    assert flipped["unattended"] is True and flipped["pause_prompt"]  # the gate's texts ride along (TD-100)
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
    got = out()  # and the running build (design §4.4, TD-062 (c)); this test's agent runs the checkout
    assert got["status"] == "agentorc-agent: active" and got["build"]["line"].startswith("host agent: build")
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

    # Turns until the send returns, rather than one turn on a timer (TD-063). Until TD-204 `rpc_send`
    # read the revision it waits for *after* `_submit`, so on a slow runner a single working→idle
    # pair could land before that read and the stall window expired (CI: `showed no activity within
    # 4.69821 s`). The baseline is now read before the paste, but a pair that lands before the send
    # begins typing is still no start of it: whichever cycle lands after the paste satisfies both
    # waits, at any load, and the loop ends on idle so the settled state the command prints is unchanged.
    done = threading.Event()

    def turns():
        while not done.is_set():
            call_sync("hook", session=sid, state="working")
            call_sync("hook", session=sid, state="idle")
            done.wait(0.05)

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(turns)
        try:
            assert cli.main(["send", sid, "--wait", "--timeout", "5", "go"]) == 0
        finally:
            done.set()
        fut.result(timeout=5)  # `rpc_hook` applies the event inline, so the last idle is on the record
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
    assert "set-option" not in out["attach"]  # the mouse is the browser's (TD-174)
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

    def explain() -> str:
        assert cli.main(["explain", sid]) == 0
        return capsys.readouterr().out

    # `idle` says the shell is the foreground process, which is true before its prompt has painted:
    # `explain` then has an empty screen and prints no `screen:` section at all. Wait for the
    # section being asserted on, not for a duration (TD-033).
    out = wait_screen(sid, explain, lambda o: "screen:" in o, what="the pane's screen in `ao explain`")
    assert out.startswith(f"{sid}  idle (scraped)") and "why: shell has no screen rules" in out
    call_sync("kill", id=sid)


def test_skill_prints_the_rules(capsys):
    """TD-019: `ao --skill` prints the packaged skill and exits 0 without an agent."""
    with pytest.raises(SystemExit) as e:
        cli.main(["--skill"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("---\nname: ao\n")
    for must in (
        "AGENTORC_SESSION",
        "ao status --json",
        "prompt-stuck",
        "invariant 1",
        "never",
        # the membership half of the gate (TD-036 step 2): both refusals, and who may hand control
        # on — a session that already controls the target, not only a person (review 2026-09-13)
        "not in its controllers",
        "needs the control grant",
        "or one of its current controllers",
        "ao control",
        # the mail rules (design §4.10, TD-052 step 2), the instructions rule above the rest
        "instructions come from your controllers and from people",
        "read your inbox before acting",
        "answer an `ask`",
        "never broadcast",
        "ao msg person",
        "ao progress none",  # TD-053 step 1: declared before exiting, or the exit reads as a crash
    ):
        assert must in out.lower() or must in out, must
    # A budget, not a law: the skill is read in full by every session that runs `ao --skill`, so
    # it stays skimmable. Raised from 120 to 125 on 2026-09-20 (TD-079 step 3) for the outcome
    # rule — a command no session is told to run is half-shipped, which is this test's own
    # argument. Raise it only for a rule a session cannot work without, and compress first.
    assert out.count("\n") <= 125


def test_grant_revoke_and_the_caller(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-028 step 1: `ao` sends AGENTORC_SESSION as the caller; `ao grant` / `ao revoke` edit
    `capabilities`, shown by `ao status -v` and `--json`; `ao new --grant` sets it at create.
    TD-036: the grant is now only half the gate — the caller must also be in the target's
    `controllers`."""

    def out():
        return json.loads(capsys.readouterr().out)

    assert cli.main(["--json", "shell", "ga", "-d", str(tmp_path)]) == 0
    a = out()["id"]
    assert cli.main(["--json", "shell", "gb", "-d", str(tmp_path)]) == 0
    b = out()["id"]
    wait_state(a, "idle")
    wait_state(b, "idle")
    call_sync("set_mode", id=b, unattended=True)  # a worker; an interactive b is §9 invariant 5's case
    monkeypatch.setenv("AGENTORC_SESSION", a)  # now `ao` runs inside session a
    assert cli.main(["kill", b]) == 1
    assert "needs the control grant" in capsys.readouterr().err
    assert cli.main(["--json", "grant", a, "control"]) == 1  # no self-grant
    assert "needs the control grant" in out()["error"]
    assert cli.main(["--json", "status"]) == 0  # reads pass
    assert {s["id"] for s in out()} >= {a, b}
    assert cli.main(["send", a, "echo", "self-ok"]) == 0  # self passes
    monkeypatch.delenv("AGENTORC_SESSION")  # a person at a terminal
    assert cli.main(["grant", a, "control"]) == 0
    assert capsys.readouterr().out.strip() == f"{a}: grants control"
    assert cli.main(["status", "-v"]) == 0
    assert "grants: control" in capsys.readouterr().out
    monkeypatch.setenv("AGENTORC_SESSION", a)
    # The grant alone is no longer enough (TD-036): a is not in b's controllers, and an empty list
    # means nobody may act. `ao control` lands in step 2; here the RPC stands in for it.
    assert cli.main(["--json", "kill", b]) == 1
    assert "not in its controllers" in out()["error"]
    call_sync("set_controllers", id=b, add=[a])
    assert cli.main(["--json", "kill", b]) == 0
    assert out()["state"] == "exited"
    monkeypatch.delenv("AGENTORC_SESSION")
    assert cli.main(["--json", "revoke", a, "control"]) == 0
    assert out()["capabilities"] == []
    assert cli.main(["--json", "new", "gc", "-a", "shell", "-d", str(tmp_path), "--grant", "control"]) == 0
    c = out()
    assert c["capabilities"] == ["control"]
    for sid in (a, c["id"]):
        call_sync("kill", id=sid)


def test_control_and_new_controller(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-036 step 2: `ao control <controller> add|remove <session>…` edits membership from the
    lead's side, `ao new --controller` sets it at create, `ao status -v` prints both
    directions, and `ao new` says so when a session starts with nobody able to act on it."""

    def out():
        return json.loads(capsys.readouterr().out)

    monkeypatch.chdir(tmp_path)  # `ao control orc add w1` resolves bare names against the cwd
    assert cli.main(["--json", "shell", "orc", "-d", str(tmp_path)]) == 0
    orc = out()["id"]
    assert cli.main(["shell", "w1", "-d", str(tmp_path)]) == 0
    # the no-controller line, printed once, in the person's own words
    said = capsys.readouterr().out
    assert "starts with no controller: nobody may act on it" in said
    w1 = call_sync("list")
    w1 = next(s_["id"] for s_ in w1 if s_["name"] == "w1")
    wait_state(orc, "idle")
    wait_state(w1, "idle")
    call_sync("set_grants", id=orc, add=["control"])

    # a bare name works on both sides (design §4.1), and the output says who is over the session
    assert cli.main(["control", "orc", "add", "w1"]) == 0
    assert capsys.readouterr().out.strip() == f"{w1}: under {orc}"
    assert call_sync("get", id=w1)["controllers"] == [orc]
    # …so the lead can now act on it — once it is a worker. Interactive, it is a person's
    # session and out of reach whatever the list says (§9 invariant 5, TD-041); `ao mode` is how
    # a person hands it over, and the CLI shows the refusal as it came
    monkeypatch.setenv("AGENTORC_SESSION", orc)
    assert cli.main(["send", w1, "echo", "ok"]) == 1
    assert "invariant 5" in capsys.readouterr().err
    assert cli.main(["control", "orc", "add", "w1"]) == 1  # nor may a session add itself to one
    assert "invariant 5" in capsys.readouterr().err
    monkeypatch.delenv("AGENTORC_SESSION")
    assert cli.main(["mode", w1, "unattended"]) == 0
    assert capsys.readouterr().out.strip() == f"{w1}: unattended"
    monkeypatch.setenv("AGENTORC_SESSION", orc)
    assert cli.main(["send", w1, "echo", "ok"]) == 0
    monkeypatch.delenv("AGENTORC_SESSION")

    # both directions in `status -v`
    assert cli.main(["status", "-v"]) == 0
    shown = capsys.readouterr().out
    assert f"under:  {orc}" in shown
    assert f"members: {w1}" in shown

    # one refusal does not lose the rest, and the exit code says something was refused
    assert cli.main(["--json", "control", "orc", "add", "w1", "nosuchsession", "nosuchsession"]) == 1
    res = out()
    assert [s_["id"] for s_ in res["sessions"]] == [w1]
    # one entry per attempt, not per name: the same bad name twice is two answers
    assert [r["session"] for r in res["refused"]] == ["nosuchsession", "nosuchsession"]
    # a lead may not be made to control itself — refused by the agent, not by the CLI
    assert cli.main(["control", "orc", "add", "orc"]) == 1
    assert "cannot be its own controller" in capsys.readouterr().err

    assert cli.main(["control", "orc", "remove", "w1"]) == 0
    assert capsys.readouterr().out.strip() == f"{w1}: under nobody"
    assert call_sync("get", id=w1)["controllers"] == []

    # --controller at create, and it is resolved from a name like every other id
    assert cli.main(["--json", "new", "w2", "-a", "shell", "-d", str(tmp_path), "--controller", "orc"]) == 0
    w2 = out()
    assert w2["controllers"] == [orc]
    # a --controller that resolves to nothing refuses *before* the create RPC, so no session, no
    # pane, and nothing to clean up (review 2026-09-13)
    before = {s_["id"] for s_ in call_sync("list")}
    assert cli.main(["--json", "new", "w3", "-a", "shell", "-d", str(tmp_path), "--controller", "ghost"]) == 1
    assert "no session named ghost here" in out()["error"]
    assert {s_["id"] for s_ in call_sync("list")} == before
    for sid in (orc, w1, w2["id"]):
        call_sync("kill", id=sid)


def test_until_parses_the_shapes_a_person_types_and_refuses_the_rest():
    """`ao new --until` / `ao until` (design §6, TD-026). The friendly parsing is the client's,
    because "the next 06:00" is a question about the caller's clock — the agent only ever stores the
    instant."""
    from agentorc.cli import stop_time

    now = datetime.datetime.now().astimezone()
    plus = datetime.datetime.fromisoformat(stop_time("+90m"))
    assert datetime.timedelta(minutes=89) < plus - now.astimezone(datetime.UTC) < datetime.timedelta(minutes=91)
    at = datetime.datetime.fromisoformat(stop_time("06:00")).astimezone()
    assert (at.hour, at.minute) == (6, 0) and at > now  # the *next* 06:00, never one in the past
    assert (at - now) < datetime.timedelta(days=1)
    assert stop_time("2026-09-14T06:00:00Z") == "2026-09-14T06:00:00+00:00".replace("+00:00", "Z")
    naive = datetime.datetime.fromisoformat(stop_time("2026-09-14T06:00:00")).astimezone()
    assert (naive.hour, naive.minute) == (6, 0)  # no zone means the caller's, for the same reason
    for bad in ("", "nope", "25:00", "06:99"):
        with pytest.raises(AgentError):
            stop_time(bad)


def test_new_until_needs_unattended_and_carries_the_wrap_up_words(tmp_path, monkeypatch, capsys):
    """A stop time is a policy and policies leave interactive sessions alone (§4.2), so `--until`
    without `--unattended` is refused rather than stored where nothing will act on it. The wrap-up
    wording travels with the record — `sessionorc` must not know what a brief is — and it is the one
    `ao team stop` sends, so both ways of ending a worker say the same thing."""
    from agentorc import teams
    from agentorc.cli import _stop

    args = argparse.Namespace(until="+2h", unattended=False)
    with pytest.raises(AgentError, match="unattended"):
        _stop(args)
    assert _stop(argparse.Namespace(until=None, unattended=True)) == {}
    sent = _stop(argparse.Namespace(until="+2h", unattended=True))
    assert sent["wrapup_prompt"] == teams.WRAPUP_PROMPT and sent["run_until"].endswith("Z")


def test_stop_note_reads_in_the_local_clock(monkeypatch):
    from agentorc.cli import stop_note

    assert stop_note({}) == ""
    when = datetime.datetime.now().astimezone() + datetime.timedelta(hours=2)
    iso = when.astimezone(datetime.UTC).isoformat().replace("+00:00", "Z")
    note = stop_note({"run_until": iso})
    assert note.startswith("stops ") and f"{when:%H:%M}" in note  # the reader's clock, not UTC
    assert "wrap-up sent" in stop_note({"run_until": iso, "wrapup_sent_at": iso})


def test_progress_and_finding_report_on_the_calling_session(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-028 step 2: `ao progress` / `ao finding` land on the caller's own record (or `--id`),
    print the report line, and show up in `ao status -v` and `--json`; `ao new --lane` sets the lane."""

    def out():
        return json.loads(capsys.readouterr().out)

    assert cli.main(["--json", "new", "grinder", "-a", "shell", "-d", str(tmp_path), "--lane", "td-27,TD-19"]) == 0
    sid = out()["id"]
    wait_state(sid, "idle")
    # a person at a terminal with no session of their own is told, not guessed at
    monkeypatch.delenv("AGENTORC_SESSION", raising=False)
    assert cli.main(["progress", "claim", "TD-027"]) == 2
    assert "run this inside an agentorc session" in capsys.readouterr().err
    monkeypatch.setenv("AGENTORC_SESSION", sid)  # now `ao` runs inside the grinder
    assert cli.main(["progress", "claim", "td-27"]) == 0
    assert capsys.readouterr().out.strip() == f"{sid}: TD-027 · 0/2 done"
    assert cli.main(["--json", "progress", "done", "TD-027", "--pr", "60"]) == 0
    assert out()["progress"][0] == {
        "ref": "TD-027",
        "status": "done",
        "pr": 60,
        "why": None,
        "source": "declared",
        "branch": None,  # a declaration never carries one (TD-045)
        "review_pr": None,  # TD-150: only the tick sets it, beside a declared claim
        "slices": [],  # TD-325: a merged PR on the claim, declared with --slice or derived
        "at": call_sync("get", id=sid)["progress"][0]["at"],
    }
    assert cli.main(["finding", "TD-029", "--priority", "low"]) == 0
    assert capsys.readouterr().out.strip() == f"{sid}: filed TD-029 (low)"
    assert cli.main(["progress", "drop", "TD-019", "--why", "phase 5"]) == 0
    capsys.readouterr()
    call_sync("hook", session=sid, model="claude-opus-5")  # TD-031: as a hook would report it
    assert cli.main(["status", "-v"]) == 0
    shown = capsys.readouterr().out
    assert "report: TD-027 → #60 · 1/2 done" in shown and "filed:  TD-029 (low)" in shown
    # `shell` has no opinion on how a model name shortens, so it prints as observed (TD-031)
    assert "model:  claude-opus-5" in shown
    monkeypatch.delenv("AGENTORC_SESSION")
    assert cli.main(["--json", "finding", "#67", "--id", sid]) == 0  # a person, or a lead, for a worker
    assert [f["ref"] for f in out()["findings"]] == ["TD-029", "#67"]
    call_sync("kill", id=sid)


def test_ao_new_says_which_session_holds_the_name(subprocess_agent, tmp_path, capsys):
    """TD-030: `ao new` prints §4.1's refusal with the id to switch to, and `--json` carries that id
    as a field; `ao shell` sends no name at all, so the agent names it."""
    assert cli.main(["new", "aotest", "-a", "shell", "-d", str(tmp_path)]) == 0
    sid = capsys.readouterr().out.split()[0]
    wait_state(sid, "idle")
    assert cli.main(["new", "aotest", "-a", "shell", "-d", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "aotest is running — switch to it" in err and f"ao focus {sid}" in err  # the message, then the hint
    assert cli.main(["--json", "new", "aotest", "-a", "shell", "-d", str(tmp_path)]) == 1
    refused = json.loads(capsys.readouterr().out)
    assert refused["holder"] == sid and refused["holder_state"] == "idle" and f"ao focus {sid}" in refused["hint"]
    assert cli.main(["shell", "-d", str(tmp_path)]) == 0  # named by the agent, not by `ao`
    other = capsys.readouterr().out.split()[0]
    assert other.endswith("-shell") and call_sync("get", id=other)["name"] == "shell"
    for x in (sid, other):
        call_sync("kill", id=x)


def test_every_subcommand_takes_a_bare_name(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-030 step 5, design §4.1: a bare name resolves to the one session of that name here; a
    full id always means itself; ambiguity and absence are errors, never a guess."""
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    assert cli.main(["--json", "new", "w", "-a", "shell", "-d", str(tmp_path / "one")]) == 0
    first = json.loads(capsys.readouterr().out)["id"]
    wait_state(first, "idle")
    monkeypatch.chdir(tmp_path / "one")
    assert cli.main(["--json", "tail", "w", "-n", "1"]) == 0  # the name, not the id
    capsys.readouterr()
    assert cli.main(["--json", "mode", "w", "unattended"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == first
    assert cli.main(["--json", "progress", "claim", "TD-030", "--id", "w"]) == 0  # `--id` too
    assert json.loads(capsys.readouterr().out)["id"] == first
    assert cli.main(["--json", "tail", first]) == 0  # the full id still means itself
    capsys.readouterr()
    assert cli.main(["tail", "nobody"]) == 1
    assert "no session named nobody here" in capsys.readouterr().err
    # a second session of that name in a sibling directory: from the parent, both are "here"
    assert cli.main(["--json", "new", "w", "-a", "shell", "-d", str(tmp_path / "two")]) == 0
    second = json.loads(capsys.readouterr().out)["id"]
    wait_state(second, "idle")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["tail", "w"]) == 1
    assert f"w is ambiguous here — {', '.join(sorted([first, second]))}" in capsys.readouterr().err
    monkeypatch.chdir(tmp_path / "two")
    assert cli.main(["--json", "tail", "w", "-n", "1"]) == 0  # unambiguous again in its own directory
    capsys.readouterr()
    # an exited session of the name does not shadow the live one, and loses to nothing else
    call_sync("kill", id=second)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--json", "mode", "w", "interactive"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == first  # the live one wins
    call_sync("kill", id=first)


def test_ao_new_team_and_project_badges(subprocess_agent, tmp_path, capsys):
    """TD-040 step (b), design §4.9: `ao new --team --project` pass two plain strings through to
    the record; `status --json` carries them and `status -v` prints one line each when set."""
    argv = ["--json", "new", "g", "-a", "shell", "-d", str(tmp_path), "--team", "ao-grind", "--project", "agentorc"]
    assert cli.main(argv) == 0
    s = json.loads(capsys.readouterr().out)
    assert (s["team"], s["project"]) == ("ao-grind", "agentorc")
    assert cli.main(["--json", "shell", "-d", str(tmp_path)]) == 0  # `ao shell` has no flags: empty badges
    sh = json.loads(capsys.readouterr().out)
    assert (sh["team"], sh["project"]) == ("", "")
    assert cli.main(["--json", "status"]) == 0
    by_id = {r["id"]: r for r in json.loads(capsys.readouterr().out)}
    assert (by_id[s["id"]]["team"], by_id[s["id"]]["project"]) == ("ao-grind", "agentorc")
    assert cli.main(["status", "-v"]) == 0
    shown = capsys.readouterr().out
    assert "team:   ao-grind" in shown and "project: agentorc" in shown
    assert shown.count("team:") == 1  # the shell session shows neither line
    for sid in (s["id"], sh["id"]):
        call_sync("kill", id=sid)


# ── TD-049: a manager blocks instead of sleeping (design §4.8 "Waking a manager") ──────────────


def test_the_wake_vocabulary_ignores_what_moves_every_tick_and_notices_what_a_lead_acts_on():
    """The exclusions are the whole point (design §4.8).

    `last_output`, `tail`, `since`, `seen_at` and `git` move on almost every tick of a healthy
    session. A digest over the whole record would wake a lead continuously and be worth less than
    the poll it replaces, so the vocabulary is short and deliberate.
    """
    from sessionorc.models import wake_digest

    base = {
        "id": "a",
        "state": "working",
        "exit_code": None,
        "pending": None,
        "progress": [],
        "findings": [],
        "controllers": ["lead"],
    }
    same = wake_digest(base)
    for noise in ("last_output", "tail", "since", "seen_at", "git", "subagents", "model", "name"):
        assert wake_digest({**base, noise: "moved"}) == same, f"{noise} must not wake a lead"
    # a permission's countdown is not the event; the question is
    asking = {**base, "state": "needs-you", "pending": {"kind": "permission", "text": "rm -rf x", "deadline": "1"}}
    assert wake_digest(asking) == wake_digest({**asking, "pending": {**asking["pending"], "deadline": "2"}})
    assert wake_digest(asking) != same

    # and the four things a lead exists to react to
    assert wake_digest({**base, "state": "exited"}) != same
    assert wake_digest({**base, "progress": [{"ref": "TD-1", "status": "done", "pr": 7}]}) != same
    assert wake_digest({**base, "findings": [{"ref": "TD-2", "priority": "high"}]}) != same
    assert wake_digest({**base, "controllers": []}) != same
    # a techlead seat's questions (§4.9b): the count leaving zero wakes its manager, 1→2 does not
    assert wake_digest({**base, "asks_waiting": 0}) == same
    assert wake_digest({**base, "asks_waiting": 1}) != same
    assert wake_digest({**base, "asks_waiting": 1}) == wake_digest({**base, "asks_waiting": 2})


def test_a_lead_waits_on_what_it_controls_and_a_person_sees_everything():
    """Scope reuses the authority rule (§4.8): a lead waits on exactly what it may act on."""
    from sessionorc.waits import wait_scope

    ss = [{"id": "m1", "controllers": ["lead"]}, {"id": "m2", "controllers": ["other"]}, {"id": "m3"}]
    assert [s["id"] for s in wait_scope(ss, "lead", "controlled")] == ["m1"]
    assert [s["id"] for s in wait_scope(ss, "lead", "all")] == ["m1", "m2", "m3"]
    assert [s["id"] for s in wait_scope(ss, None, "controlled")] == ["m1", "m2", "m3"]  # a person


def test_an_event_that_fired_while_the_lead_was_busy_is_still_there_when_it_comes_back():
    """The case that decides whether this is worth having (design §4.8, TD-049 step 4).

    A lead is not blocked mid-turn — it is running a cadence check or writing a board line — and a
    lead that misses the one event it existed for is worse than a poll. The comparison is against
    what this caller last *saw*, not against what happened to be streamed while it listened, so
    the `subscribe` snapshot answers it before the stream is ever read.
    """
    from sessionorc.models import wake_digest
    from sessionorc.waits import wake_changes

    working = {"id": "m1", "state": "working", "controllers": ["lead"], "progress": []}
    done = {**working, "state": "idle", "progress": [{"ref": "TD-1", "status": "done", "pr": 7}]}

    # first wait ever: record where we are, wake on nothing — otherwise every lead's first call
    # returns its whole fleet and it learns to ignore the result
    changed, cursor = wake_changes({}, [working])
    assert changed == [] and cursor == {"m1": wake_digest(working)}

    # the worker finishes while the lead is mid-turn; the next wait still sees it
    changed, cursor2 = wake_changes(cursor, [done])
    assert [s["id"] for s in changed] == ["m1"]
    # and does not report it twice
    assert wake_changes(cursor2, [done]) == ([], cursor2)


def test_a_session_that_went_away_is_a_wake_of_its_own():
    """The one a lead most needs: a member that exited and was forgotten has no record to diff."""
    from sessionorc.waits import wake_changes

    _, cursor = wake_changes({}, [{"id": "m1", "state": "working", "controllers": ["lead"]}])
    changed, cursor2 = wake_changes(cursor, [])
    assert changed == [{"id": "m1", "gone": True}] and cursor2 == {}


def test_wait_returns_at_the_timeout_when_nothing_changes(subprocess_agent, capsys):
    """A quiet fleet costs one blocked connection and returns at the fallback interval."""
    import time

    t0 = time.monotonic()
    assert cli.main(["wait", "--timeout", "1.2", "--scope", "all"]) == 0
    assert 1.0 <= time.monotonic() - t0 < 12.0
    assert "nothing changed" in (out := capsys.readouterr().out)
    assert cli.END_THE_TURN not in out  # no session: a person at a terminal is never rung


def test_a_poll_that_finds_nothing_tells_a_session_to_end_its_turn(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-153 (design §4.10 *Waiting on mail is ending the turn*): a session looping on `ao wait` or
    `ao inbox --unread` stays `working` and is never rung, so the reply that found nothing says so —
    on stdout in prose, on stderr under `--json` so the JSON still parses — and a person at a
    terminal, who is never rung, is not told it."""
    sid = call_sync("create", name="poller", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])["id"]
    assert cli.main(["inbox", "--unread"]) == 0  # the person inbox: no line
    assert cli.END_THE_TURN not in capsys.readouterr().out
    monkeypatch.setenv("AGENTORC_SESSION", sid)
    assert cli.main(["inbox", "--unread"]) == 0
    assert cli.END_THE_TURN in capsys.readouterr().out
    assert cli.main(["--json", "inbox", "--unread"]) == 0
    got = capsys.readouterr()
    assert json.loads(got.out)["entries"] == [] and cli.END_THE_TURN in got.err
    assert cli.main(["wait", "--timeout", "1.2"]) == 0
    assert cli.END_THE_TURN in (out := capsys.readouterr().out) and "nothing changed" in out
    call_sync("msg", to=sid, text="from the person")
    # TD-327: a wait that times out while mail is unread — mail it does not wake on, or that an earlier
    # wait reported — prints the unread line alone, never *nothing unread*
    assert cli.main(["wait", "--timeout", "1.2"]) == 0
    out = capsys.readouterr().out
    assert "nothing changed" in out and "you have 1 unread" in out and cli.END_THE_TURN not in out
    assert cli.main(["inbox", "--unread"]) == 0  # found something: no line
    assert cli.END_THE_TURN not in capsys.readouterr().out
    call_sync("kill", id=sid)


def test_a_worker_marking_done_wakes_a_waiting_lead_within_seconds(subprocess_agent, tmp_path, capsys):
    """TD-049's own "Done when": a worker marking `done --pr N` reaches a *blocked* lead in
    seconds rather than within a tick. This is the stream half — the snapshot half is the test
    below — so the waiter has to be blocked before the change is made.

    It runs as a subprocess, not a thread: `ao wait` blocks, and a thread sharing this process's
    stdout would race `capsys` with the main thread. It waits in the **default** scope, under a
    real lead that controls exactly this worker, which is both how a lead uses it and what keeps
    the rest of the suite's churn in this shared agent out of the result.
    """
    (tmp_path / "lead").mkdir()
    assert cli.main(["--json", "shell", "-d", str(tmp_path / "lead"), "lead1"]) == 0
    lead = json.loads(capsys.readouterr().out)["id"]
    assert cli.main(["--json", "shell", "-d", str(tmp_path), "w1"]) == 0
    sid = json.loads(capsys.readouterr().out)["id"]
    assert cli.main(["control", lead, "add", sid]) == 0
    env = {**os.environ, "AGENTORC_SESSION": lead}

    def wait_once(timeout: str) -> subprocess.Popen:
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from agentorc.cli import main; raise SystemExit(main())",
                "--json",
                "wait",
                "--timeout",
                timeout,
            ],
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )

    wait_once("2").communicate(timeout=20)  # the lead's first wait: records the cursor, wakes on nothing
    waiter = wait_once("25")
    try:
        time.sleep(1.5)  # let it get past `list` and settle into the stream
        t0 = time.monotonic()
        assert cli.main(["progress", "done", "TD-999", "--pr", "7", "--id", sid]) == 0
        out, _ = waiter.communicate(timeout=30)
        assert time.monotonic() - t0 < 20.0  # seconds, not a tick
        woke = json.loads(out or "{}").get("changed") or []
        assert any(
            r.get("id") == sid and any(p.get("ref") == "TD-999" and p.get("pr") == 7 for p in r.get("progress") or [])
            for r in woke
        ), f"the lead did not wake on the worker's done: {out!r}"
    finally:
        if waiter.poll() is None:
            waiter.kill()
        for x in (sid, lead):
            cli.main(["kill", x])


def test_the_snapshot_is_a_list_call_not_a_timed_burst(subprocess_agent, tmp_path, capsys):
    """The first shape of this was a heuristic: read `subscribe`'s opening burst until it goes
    quiet for 0.4 s, then judge it. A burst has no end marker, so a gap in a slow or large one
    reads as "that is all" — and `wake_changes` then reports every record not yet received as
    **gone** and drops it from the cursor. A lead would be told its whole fleet had vanished.

    `list` has a definite answer, so there is no window to get wrong. This pins the shape: a wait
    that returns immediately because something changed while the caller was busy must do so
    without ever reading the stream.
    """
    from sessionorc.waits import wake_changes

    # the bug itself, at the level it bit: a partial view is not an empty fleet
    changed, cursor = wake_changes({"m1": "A", "m2": "B"}, [{"id": "m1", "state": "working"}])
    assert {c["id"] for c in changed if c.get("gone")} == {"m2"}, "a missing record still reads as gone"
    assert "m2" not in cursor, "and is dropped from the cursor — which is why the input must be complete"

    # end to end: a change made before the wait starts returns at once, from `list`
    assert cli.main(["--json", "shell", "-d", str(tmp_path), "w2"]) == 0
    sid = json.loads(capsys.readouterr().out)["id"]
    assert cli.main(["wait", "--timeout", "1", "--scope", "all"]) == 0  # record the cursor
    capsys.readouterr()
    assert cli.main(["progress", "claim", "TD-998", "--id", sid]) == 0
    import time

    t0 = time.monotonic()
    assert cli.main(["--json", "wait", "--timeout", "30", "--scope", "all"]) == 0
    assert time.monotonic() - t0 < 5.0, "a change already in the record needs no stream at all"
    assert "TD-998" in capsys.readouterr().out
    cli.main(["kill", sid])


def test_a_cursor_that_cannot_be_read_wakes_on_everything_rather_than_on_nothing(tmp_path, monkeypatch):
    """Three cursors, three answers (review of PR #145).

    The dangerous one is the middle: a corrupt cursor that read as `{}` would look like a first
    wait and *swallow* everything that changed since the last good write — silently, which is the
    one failure this command must not have. Unknown has to mean "wake on everything".
    """
    from sessionorc.waits import read_cursor, wake_changes, write_cursor

    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    rec = [{"id": "m1", "state": "working", "controllers": ["lead"]}]

    assert read_cursor("lead") == {}  # never waited
    assert wake_changes({}, rec) == ([], {"m1": wake_digest_of(rec[0])})

    write_cursor("lead", {"m1": wake_digest_of(rec[0])})
    assert read_cursor("lead") == {"m1": wake_digest_of(rec[0])}
    assert wake_changes(read_cursor("lead"), rec)[0] == []  # nothing moved

    # a cursor that exists and is unreadable is *unknown*, not empty
    from sessionorc.waits import cursor_file as _cursor_file

    _cursor_file("lead").write_text("{ truncated")
    assert read_cursor("lead") is None
    assert wake_changes(None, rec)[0] == rec


def wake_digest_of(s):
    from sessionorc.models import wake_digest

    return wake_digest(s)


def test_two_callers_whose_ids_differ_only_past_the_slug_do_not_share_a_cursor(tmp_path, monkeypatch):
    """`naming.slug` truncates, so a digest keeps the filename unique (review of PR #145)."""
    from sessionorc.waits import cursor_file as _cursor_file

    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    a = "ao-agentorc-a-very-long-orchestrator-session-name-one"
    b = "ao-agentorc-a-very-long-orchestrator-session-name-two"
    assert _cursor_file(a) != _cursor_file(b)


def test_the_skill_tells_a_session_that_ao_wait_exists():
    """A command no session knows about is half-shipped: `ao --skill` is where a supervising
    session learns what it can do, and `ao wait` changes how such a session is written."""
    skill = (pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "skill.md").read_text()
    assert "ao wait" in skill
    assert "silence is not an event" in skill.lower()  # the limit, where the reader will act on it


def test_the_grinder_preset_says_what_a_worker_does_with_a_contradiction():
    """TD-134 (design §4.10 *A conflict, worked*): the kind and its citations are named, the worker
    never picks one, and the unanswered case goes to the person — never a stalled worker."""
    text = (pathlib.Path(__file__).parents[1] / "src/agentorc/briefs/grinder.md").read_text()
    line = next(ln for ln in text.splitlines() if "--kind conflict" in ln)
    example = re.search(r"`(ao msg [^`]*)`", line).group(1)
    # the example parses as written: a worker that types it raises the conflict, not a usage error
    argv = example.split()[1:]
    args = cli.build_parser().parse_args([a.replace("<", "").replace(">", "") for a in argv])
    assert args.kind == "conflict" and len(cli._refs(args.cites)) == 2
    assert "never pick one" in text and "the first reply is the ruling" in text
    assert "ask the person yourself" in text


def test_the_skill_and_the_presets_say_waiting_on_mail_is_ending_the_turn():
    """TD-153 (design §4.10): the doorbell rings only a hook-confirmed idle, so a session that
    polls for mail in a loop is never rung. Where a session reads how to wait, it reads that."""
    root = pathlib.Path(__file__).parents[1]
    skill = (root / "src" / "agentorc" / "skill.md").read_text()
    assert "Waiting on mail is ending the turn" in skill and "never a loop" in skill
    briefs = sorted((root / "src" / "agentorc" / "briefs").glob("*.md")) + [root / "docs/briefs/designer-ao-1.md"]
    for p in briefs:
        text = p.read_text()
        assert "never wait for input" not in text, p.name  # read as *never be idle*
        if "Nobody is driving you" in text:
            assert "mail it, then end the turn" in text, p.name


def test_the_grinder_preset_says_a_board_line_is_named_with_its_due_date():
    """TD-220 slice 5 (design §4.5 screen 6 *The board's horizon*): the Inbox draws a line not yet due
    only as far as the person's mode reaches, so a worker that names one says when it is due."""
    text = (pathlib.Path(__file__).parents[1] / "src/agentorc/briefs/grinder.md").read_text()
    assert "*on the board, due <date>*" in text and "is dated today" in text


def test_the_grinder_preset_reads_the_inbox_before_a_claim_and_before_the_declaration():
    """TD-141 (design §4.9a): the briefs say it twice — before a claim and before the declaration —
    and the refusal is what holds when they are not read."""
    text = (pathlib.Path(__file__).parents[1] / "src/agentorc/briefs/grinder.md").read_text()
    claim, declare = text.index("Read your inbox before claiming"), text.index("read your inbox once more")
    assert claim < declare < text.index('ao progress none --why "<what you searched')
    assert "refused while mail is unread" in text


def test_the_briefs_and_the_skill_say_to_report_an_outcome(tmp_path):
    """TD-079 step 3 (design §4.10 *Outcomes*): a command a session is never told to run is
    half-shipped. Every brief a session is started from — the package's role templates and this
    repo's own — and `ao --skill` say that an answered question owes an outcome and name the one
    command that settles it. The manager's says it chases its members' debts and never
    reports one for them; and all of them say that a `note` is not a way to ask."""
    root = pathlib.Path(__file__).parents[1]
    from agentorc import repoconfig

    cfg = repoconfig.load(root)

    def started_from(rel: str) -> str:
        # this repo's own briefs are supplements (design §4.8, TD-114): what a session is started
        # from is the role's template with the repo's file in its slot, never the file alone
        if rel.startswith("docs/briefs/"):
            role = "manager" if "manager" in rel else "grinder"
            return repoconfig.resolve_role(cfg, role).brief_text(supplement=rel) or ""
        return (root / rel).read_text()

    workers = ["src/agentorc/briefs/grinder.md", "src/agentorc/briefs/hunter.md", "docs/briefs/grinder-ao-1.md"]
    for rel in [*workers, "src/agentorc/skill.md"]:
        text = started_from(rel)
        assert "--outcome done|blocked|dropped" in text, rel
        assert "--for" in text and "--thread" in text, rel
        assert "tell me if you want less" in text, rel  # the kind, in the words the mistake was made in
    for rel in ("src/agentorc/briefs/manager.md", "docs/briefs/manager-ao-1.md"):
        text = started_from(rel)
        assert "owed:" in text, rel  # where a manager reads a member's debt
        assert "report an outcome for a" in text, rel  # and never in its place
        # the chase does not reach past the one rule §4.9a makes absolute (review of PR #295)
        assert "finished member is never sent to" in text or "finished worker is still never sent to" in text, rel
        assert "not ready to close" in text, rel  # what keeps a finished member's debt from vanishing
        assert "tell me if you want less" in text, rel  # the same kind rule as the workers', not a second one


SHAPE = (
    "A message to the person is read cold. Its first paragraph is the whole of what they need — what it is about, "
    "what was decided or is being asked, and what they must do — in one to three plain sentences. A blank line, "
    "then the reading, for the record. A reply with `--source` begins with its verdict."
)


def test_the_presets_ask_for_the_shape_of_a_message_to_the_person():
    """TD-139 (design §4.10 *How a message to a person is written*): the rule lives with the writers,
    in the same words in every preset whose session writes to the person, and in the designer's."""
    root = pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "briefs"
    for name in ("techlead.md", "manager.md", "grinder.md", "designer.md"):  # the designer a preset since TD-309
        assert SHAPE in (root / name).read_text(encoding="utf-8"), name
    # since TD-310 the repo's designer brief is the template's `{repo}` supplement, so it says it no
    # longer: the rule is in the template around it, once (§4.9c *The designer gets a template*)
    designer = pathlib.Path(__file__).parents[1] / "docs" / "briefs" / "designer-ao-1.md"
    assert SHAPE not in designer.read_text(encoding="utf-8")


@pytest.mark.unit
def test_the_shape_warning_counts_the_first_paragraph():
    """design §4.10: past `FIRST_PARA_WORDS` in the first paragraph, or no blank line and past the
    Inbox's `FOLD_CHARS`, `ao msg` says what the person reads; a shaped message says nothing."""
    ninety = " ".join(["word"] * 90)
    assert cli.shape_warning(ninety + "\n\nthe reading").startswith("the person reads the first paragraph: 90 words")
    assert cli.shape_warning("merged #517 — one gap left\n\n" + ninety) is None  # shaped: a long reading is fine
    assert cli.shape_warning("merge #517?") is None
    unbroken = ("a sentence of eight words said at length. " * 10).strip()  # 80 words, over 300 characters
    assert "80 words" in (cli.shape_warning(unbroken) or "")
    assert cli.shape_warning("x" * 250) is None  # one long word under the backstop: nothing to fold
    # the blank line is the Inbox row's own: `\r\n` counts, and one inside a code block does not
    assert cli.shape_warning("merged #9\r\n\r\n" + ninety) is None
    fenced = "```\na\n\nb\n```\n" + unbroken
    assert "words" in (cli.shape_warning(fenced) or "")


def test_msg_to_the_person_warns_on_an_unshaped_message_and_sends_it(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-139 (design §4.10 *The home warns, never refuses*): `ao msg person` with a 90-word first
    paragraph prints the warning and the mail arrives; a shaped one prints nothing; a message to a
    session is not checked."""
    sid = call_sync("create", name="shaper", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])["id"]
    (tmp_path / "o").mkdir()
    other = call_sync("create", name="other", dir=str(tmp_path / "o"), adapter="shell", argv=["bash", "--norc"])["id"]
    call_sync("set_controllers", id=other, add=[sid])
    monkeypatch.setenv("AGENTORC_SESSION", sid)
    long = " ".join(["word"] * 90)
    assert cli.main(["msg", "person", long, "--kind", "note"]) == 0
    got = capsys.readouterr()
    assert "note → person" in got.out and "the person reads the first paragraph: 90 words" in got.err
    assert cli.main(["msg", "person", "merged #9 — nothing to do\n\n" + long, "--kind", "note"]) == 0
    assert "the person reads" not in capsys.readouterr().err
    assert cli.main(["msg", other, long]) == 0
    assert "the person reads" not in capsys.readouterr().err
    for x in (sid, other):
        call_sync("kill", id=x)


def test_the_manager_brief_leaves_the_seat_the_wanted_restart_and_the_nudge_to_the_tick():
    """TD-103 slice (5), design §6 *Keeping a team running* rules 2–4: the tick fills and closes
    the techlead seat, carries out a wanted restart and sends the idle nudge, so the preset does
    none of them by hand (each would race the tick); it reads the marks the tick leaves."""
    text = (pathlib.Path(__file__).parents[1] / "src/agentorc/briefs/manager.md").read_text()
    assert "ao new --keep-mail" not in text and "ao new --supervised" not in text
    assert "**The host agent keeps the seat**" in text and "`seat_due`" in text and "why: fill" in text
    assert "restart_blocked" in text and "**the host agent sends it one fixed line**" in text
    assert "ao wait --timeout 3540" in text and "run_in_background: true" in text
    assert "**The exception is a member on another host**" in text  # the tick nudges no node's pane yet


def test_the_manager_brief_announces_a_wind_down_as_a_note_and_boards_only_what_waits_on_the_person():
    """TD-125, design §4.9a *A wind-down is announced*: the report is an FYI `note` to the person
    inbox, two lines — what the run merged, and each member's search — and the board carries only
    what waits on the person. The 23:47Z line was two hundred counted words whose one act was a PR
    waiting on its reader, which is never on the board and never in the note."""
    text = (pathlib.Path(__file__).parents[1] / "src/agentorc/briefs/manager.md").read_text()
    assert "ran out of work at <t>. Merged this run: #a, #b (or: nothing)." in text
    assert "<member>: <what it looked for and did not find, from the `why` on its record>; <member>: …" in text
    assert "a `note`, two lines, nothing else in it" in text
    assert "a question you passed up that nobody answered" in text
    assert "a member you left open with uncommitted or unpushed work" in text
    assert "a start or a close the host agent refused" in text
    assert "A PR held for its reader is never on the board and never in the note" in text
    assert "for each member what it looked for" not in text  # the old board line is gone


def test_the_manager_brief_reads_the_records_each_round_and_takes_finished_from_the_home():
    """TD-241 slice 4, design §6 rule 9 and §4.9a *A team's finished is the home's reading*: a
    manager's round reads its members' states from the records and carries none over (manager-dc-1
    held a grinder *interactive under Paul* for two days), the team's finished is `finished` on
    `ao team status --json`, and the tick's line means the members are closed and the last acts are due."""
    root = pathlib.Path(__file__).parents[1]
    halves = (
        "[agentorc] your team is finished: every member has declared. Make your last acts — ",
        "`ao progress none`, the note to the person — then `ao close` yourself",
    )
    tick = (root / "src/sessionorc/agent_tick.py").read_text()
    assert all(f'"{h}"' in tick for h in halves)  # the line as the tick types it
    text = (root / "src/agentorc/briefs/manager.md").read_text()
    assert "`ao team status <your team> --json` (or `ao status --json`)" in text
    assert "every round, and never carried from an earlier round" in text
    assert "its record says `unattended: false` **now**" in text
    assert "**Whether the team has finished is the home's reading, not your memory**" in text
    assert "as `finished` on the reply" in text and "holds when its `why` is empty" in text
    # the manager is `working` while it reads, so `finished.why` names it in every round of its own
    assert "**Your own clause is expected**" in text and "when `why` names no session but you" in text
    assert "**Your round may still end the team first**" in text
    assert "read `ao team status <your team> --json` once for any member still live" in text
    assert "".join(halves) in text  # the brief quotes it word for word
    assert "skip step (1), do steps (2) to (4) at once" in text and "*manager did not close*" in text


def test_the_manager_brief_names_none_of_the_four_jobs_the_tick_took():
    """TD-258 slice 6, design §6 *What is left is judgement* and rules 10–12: the cadence check, the
    merged-held-PR check, the relay of a convention change and the chase of an owed outcome are the
    tick's, so the preset and this repo's supplement tell a manager to do none of them — a member
    told by both hears each thing twice — and to read the marks the policies leave instead."""
    root = pathlib.Path(__file__).parents[1]
    text = (root / "src/agentorc/briefs/manager.md").read_text()
    supplement = (root / "docs/briefs/manager-ao-1.md").read_text()
    for gone in (
        "check_cadence.py",
        "cadence-changes.md` on",
        "relayed.json",
        "cadence_changes.py",
        "Chase it while",
        "Put those ids on",
    ):
        assert gone not in text and gone not in supplement, gone
    assert "one `ao send --wait` naming the PR" not in text and "naming the PR and the rule" not in supplement
    assert "gh pr list --state merged" not in supplement and "`held_missed`" in text  # the template says it (TD-114)
    # rule 4 types into no node's pane, so the one line a manager still sends is to a member on another host
    assert "except to a member on another host" in text and "for the debt alone" in text
    assert "`checks` in `--json`" in text and "`held_missed` on the member's record" in text
    assert "**The reminder is the host agent's**" in text and "as a `system` note" in text
    assert "say *recorded*, never *verified*" in text  # rule 10: the review row is self-attested
    steps = [ln[:2] for ln in text.splitlines() if ln[:1].isdigit() and ln[1:3] == ". "]
    assert steps == ["1.", "2.", "3.", "4.", "5."]  # the relay step went, the round's end is step 5


def test_the_manager_brief_leaves_the_crash_restart_to_the_tick():
    """TD-113 (0), design §6 *Keeping a team running* rule 1: a supervised member that exits with no
    declaration is restarted by the host agent's tick, so the preset must not send its manager down
    a hand `ao new` for it (which would race the tick and ship `{lane}` unfilled); a member past
    `restart_ceiling` reaches the person's Inbox by itself (TD-118 cut the board line)."""
    text = (pathlib.Path(__file__).parents[1] / "src/agentorc/briefs/manager.md").read_text()
    assert "**the host agent restarts it**" in text and "restart_ceiling" in text
    assert "restart it once with `ao new`" not in text


def test_msg_and_inbox(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-052 step 2 (design §4.10 "Surface"): `ao msg` prints what landed and refusals as the host
    agent words them; `ao inbox` opens with the fixed header, names who sent the last keystrokes,
    marks each entry controller / person / other, honours `--unread` and `--json`; with no session
    it reads the person inbox."""

    def mk(name: str) -> str:
        return call_sync("create", name=name, dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])["id"]

    lead, worker, peer = mk("lead"), mk("worker"), mk("peer")
    call_sync("set_controllers", id=worker, add=[lead])
    call_sync("send", id=worker, text="echo typed by a person")
    call_sync("msg", to=worker, text="from the person")
    monkeypatch.setenv("AGENTORC_SESSION", lead)
    assert cli.main(["msg", worker, "rebase first", "--kind", "ask", "--about", "TD-001"]) == 0
    out = capsys.readouterr().out
    assert f"ask → {worker}" in out and "bound" in out
    # a refusal prints as the RPC words it, naming its rule
    assert cli.main(["msg", peer, "hi"]) == 1
    assert "design §4.10" in capsys.readouterr().err
    monkeypatch.setenv("AGENTORC_SESSION", peer)
    assert cli.main(["--json", "msg", "person", "a question for Paul", "--kind", "ask"]) == 0
    to_person = json.loads(capsys.readouterr().out)
    assert to_person["delivered"] == ["person"]
    monkeypatch.setenv("AGENTORC_SESSION", worker)
    assert cli.main(["inbox"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(cli.INBOX_HEADER)
    assert "from person" in out.splitlines()[1]  # the last send into this pane was the person's
    assert "[person] person" in out and f"[controller] {lead}" in out and "rebase first" in out
    assert "open, 23h left" in out and "about TD-001" in out  # the bound, as the time left (TD-069 step 0)
    # the read above marked both: `--unread` now shows nothing, and `--json` is the RPC result
    assert cli.main(["inbox", "--unread"]) == 0
    assert "0 shown, 0 unread" in capsys.readouterr().out
    assert cli.main(["--json", "inbox"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["id"] == worker and [e["from_role"] for e in got["entries"]] == ["person", "controller"]
    ask_id = got["entries"][1]["id"]
    assert cli.main(["msg", "--reply-to", ask_id, "rebased"]) == 0
    assert f"reply → {lead}" in (out := capsys.readouterr().out) and f"closed {ask_id}" in out
    # a person at a terminal reads the person inbox, and replies from it to the sender
    monkeypatch.delenv("AGENTORC_SESSION")
    assert cli.main(["inbox"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(cli.INBOX_HEADER) and "person inbox:" in out and f"[other] {peer}" in out
    assert cli.main(["msg", "--reply-to", to_person["entry"]["id"], "yes"]) == 0
    assert f"reply → {peer}" in capsys.readouterr().out
    for sid in (lead, worker, peer):
        call_sync("kill", id=sid)


def test_inbox_thread_prints_one_thread_whole_for_the_person(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-136 slice 1 (design §4.7): `ao inbox --thread <id>` prints the thread oldest first, the
    person's own reply in it and the named entry marked; `--json` is the RPC result; a session is
    refused in the host agent's words; `--unread` does not combine with it."""
    worker = call_sync("create", name="w", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])["id"]
    monkeypatch.setenv("AGENTORC_SESSION", worker)
    assert cli.main(["--json", "msg", "person", "rebase or merge?", "--kind", "ask"]) == 0
    asked = json.loads(capsys.readouterr().out)["entry"]["id"]
    monkeypatch.delenv("AGENTORC_SESSION")
    assert cli.main(["msg", "--reply-to", asked, "merge it"]) == 0
    capsys.readouterr()
    assert cli.main(["inbox", "--thread", asked]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"thread of {asked}: 2 entries, oldest first")
    assert out.index("rebase or merge?") < out.index("merge it") and "← this one" in out
    assert cli.main(["--json", "inbox", "--thread", asked]) == 0
    assert [e["from"] for e in json.loads(capsys.readouterr().out)["entries"]] == [worker, "person"]
    assert cli.main(["inbox", "--thread", asked, "--unread"]) == 2
    monkeypatch.setenv("AGENTORC_SESSION", worker)
    assert cli.main(["inbox", "--thread", asked]) == 1
    assert "cannot read a thread" in capsys.readouterr().err
    call_sync("kill", id=worker)


def test_msg_verdict_rides_a_readers_reply_to_a_prs_ask(subprocess_agent, tmp_path, capsys, monkeypatch):
    """§4.9c (TD-315 slice 1): `ao msg --reply-to <id> --verdict merged` carries the word; the home
    refuses the reply without it, and argparse a word that is none of the three."""
    w = call_sync("create", name="w", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"], team="t")["id"]
    tl = call_sync("create", name="tl", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"], team="t")["id"]
    monkeypatch.setenv("AGENTORC_SESSION", w)
    assert cli.main(["--json", "msg", "--kind", "ask", "--pr", "12", tl, "#12 is green"]) == 0
    asked = json.loads(capsys.readouterr().out)["entry"]["id"]
    monkeypatch.setenv("AGENTORC_SESSION", tl)
    assert cli.main(["msg", "--reply-to", asked, "merged #12"]) == 1
    assert "--verdict" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["msg", "--reply-to", asked, "--verdict", "lgtm", "merged #12"])
    capsys.readouterr()
    assert cli.main(["--json", "msg", "--reply-to", asked, "--verdict", "merged", "merged #12"]) == 0
    assert json.loads(capsys.readouterr().out)["entry"]["verdict"] == "merged"
    monkeypatch.delenv("AGENTORC_SESSION")
    for sid in (w, tl):
        call_sync("kill", id=sid)


def test_msg_steer_and_the_inbox_line_that_shows_it(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-069 step 0 (design §4.10 *What a person is asked*): `ao msg --kind steer --default` sends
    the line the session will go with; `ao msg person --kind ask --bound` is refused and the
    refusal names `steer`; `ao inbox` prints a steer's default and how long is left, `[system]`
    beside a note from the home, and `lapsed` once the bound has passed."""
    sid = call_sync("create", name="steerer", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])["id"]
    monkeypatch.setenv("AGENTORC_SESSION", sid)
    assert cli.main(["msg", "person", "which branch?", "--kind", "steer", "--default", "off main"]) == 0
    out = capsys.readouterr().out
    assert "steer → person" in out and "bound" in out and "unless told otherwise: off main" in out
    # a steer with no default, and an ask to the person with a bound, are both refused
    assert cli.main(["msg", "person", "which?", "--kind", "steer"]) == 1
    assert "a steer says what it will do" in capsys.readouterr().err
    assert cli.main(["msg", "person", "merge?", "--kind", "ask", "--bound", "60"]) == 1
    err = capsys.readouterr().err
    assert "never expires" in err and "steer" in err
    # …and the unbounded ask says so where it is read
    assert cli.main(["msg", "person", "merge?", "--kind", "ask"]) == 0
    capsys.readouterr()
    monkeypatch.delenv("AGENTORC_SESSION")
    assert cli.main(["inbox"]) == 0
    out = capsys.readouterr().out
    assert "steer" in out and "default: off main" in out and "left" in out
    assert "open, no bound — it never expires" in out
    call_sync("kill", id=sid)


def test_msg_answer_and_pick_and_the_inbox_lines_that_show_them(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-070 step 1 (design §4.10 *Suggested answers*): `ao msg --answer` offers the likely
    answers on a question and is refused on a `note`; `ao inbox` prints an open entry's answers
    **numbered from 1**, marks the one that is a `steer`'s default word for word, and says which
    one a reply picked; `ao msg --reply-to <id> --pick <n>` takes that same number, sends that
    answer's own text, and says clearly what is not one."""
    sid = call_sync("create", name="asker", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])["id"]
    monkeypatch.setenv("AGENTORC_SESSION", sid)
    ask_argv = ["--json", "msg", "person", "merge PR 9?", "--kind", "ask"]
    assert cli.main([*ask_argv, "--answer", "merge it", "--answer", "hold it"]) == 0
    ask = json.loads(capsys.readouterr().out)["entry"]
    assert ask["answers"] == ["merge it", "hold it"]
    steer_argv = ["--json", "msg", "person", "which branch?", "--kind", "steer", "--default", "off main"]
    assert cli.main([*steer_argv, "--answer", "off main", "--answer", "off develop"]) == 0
    steer = json.loads(capsys.readouterr().out)["entry"]
    # only a question carries them: the refusal is the host agent's own words
    assert cli.main(["msg", "person", "fyi", "--answer", "sure"]) == 1
    assert "only a question carries answers" in capsys.readouterr().err
    # the person reads them numbered from 1, with the steer's default marked and nothing else
    monkeypatch.delenv("AGENTORC_SESSION")
    assert cli.main(["inbox"]) == 0
    out = capsys.readouterr().out
    assert "  1. merge it" in out and "  2. hold it" in out
    assert "  1. off main — default" in out and "  2. off develop" in out
    assert "hold it — default" not in out
    # --pick sends the answer itself, by the number printed above, and closes the question
    assert cli.main(["--json", "msg", "--reply-to", ask["id"], "--pick", "2"]) == 0
    sent = json.loads(capsys.readouterr().out)
    assert sent["entry"]["text"] == "hold it" and sent["entry"]["answer"] == 1 and sent["closed"] == ask["id"]
    # …and each way of getting it wrong is a clear error, exit 2, before anything is sent
    for argv, said in (
        (["msg", "--pick", "1"], "--reply-to"),
        (["msg", "--reply-to", "m-nope", "--pick", "1"], "holds no entry"),
        (["msg", "--reply-to", steer["id"], "--pick", "9"], "offers 2, numbered 1-2"),
        (["msg", "--reply-to", steer["id"], "--pick", "0"], "offers 2, numbered 1-2"),
    ):
        assert cli.main(argv) == 2, argv
        assert said in capsys.readouterr().err, argv
    # words with a pick follow the answer after a blank line, the index kept (TD-070)
    assert cli.main(["--json", "msg", "--reply-to", steer["id"], "--pick", "2", "rebase it first"]) == 0
    sent = json.loads(capsys.readouterr().out)
    assert sent["entry"]["text"] == "off develop\n\nrebase it first" and sent["entry"]["answer"] == 1
    # a closed question keeps its answers on the record but is no longer offered them to press
    assert cli.main(["inbox"]) == 0
    out = capsys.readouterr().out
    assert "  1. merge it" not in out
    # …and says which was pressed in that answer's words, never the question's (TD-296 #14)
    assert 'answered 2: "hold it"' in out and 'answered 2: "merge PR 9?"' not in out
    assert 'answered 2: "off develop"' in out
    # the sender reads which answer it was, so it branches on the number and not on the text
    monkeypatch.setenv("AGENTORC_SESSION", sid)
    assert cli.main(["inbox"]) == 0
    out = capsys.readouterr().out
    assert 'answered 2: "hold it"' in out
    # a reply that picked one and said more reads as the answer alone, from the reply itself (TD-415)
    assert 'answered 2: "off develop"' in out and 'answered 2: "off develop\n' not in out
    # words that are blank send the answer alone; an addressee word in front changes nothing (TD-415)
    # the home strips a reply's text, so what the CLI itself sends is read on its way out
    said, real = [], cli.call_sync
    monkeypatch.setattr(
        cli, "call_sync", lambda m, **kw: (said.append(kw.get("text")) if m == "msg" else None) or real(m, **kw)
    )
    sent = {}
    for name, extra in (("blank", ["   "]), ("bare", ["rebase it first"]), ("addressed", [sid, "rebase it first"])):
        monkeypatch.setenv("AGENTORC_SESSION", sid)
        assert cli.main([*ask_argv, "--answer", "merge it", "--answer", "hold it"]) == 0
        q = json.loads(capsys.readouterr().out)["entry"]
        monkeypatch.delenv("AGENTORC_SESSION")
        assert cli.main(["--json", "msg", "--reply-to", q["id"], "--pick", "2", *extra]) == 0, name
        got = json.loads(capsys.readouterr().out)
        assert got["closed"] == q["id"], name
        sent[name] = (said[-1], got["entry"]["text"], got["entry"]["answer"], got["delivered"])
    assert sent["blank"] == ("hold it", "hold it", 1, [sid])
    both = "hold it\n\nrebase it first"
    assert sent["bare"] == sent["addressed"] == (both, both, 1, [sid])
    call_sync("kill", id=sid)


def test_every_ao_reply_ends_with_the_unread_line_while_the_caller_has_mail(subprocess_agent, tmp_path, capsys):
    """Design §4.10 "Busy for hours: a line on every `ao` reply": present while the calling session
    has unread mail — on a refusal too — absent without it, and on stderr under `--json`, so a
    caller parsing stdout never meets it."""
    assert cli.main(["--json", "shell", "-d", str(tmp_path), "mailed"]) == 0
    sid = json.loads(capsys.readouterr().out)["id"]
    line = "[agentorc] you have 1 unread messages — run ao inbox"
    os.environ["AGENTORC_SESSION"] = sid
    try:
        assert cli.main(["status"]) == 0
        assert line not in capsys.readouterr().out
        del os.environ["AGENTORC_SESSION"]
        assert cli.main(["msg", sid, "a note for you"]) == 0  # a person's message
        capsys.readouterr()
        os.environ["AGENTORC_SESSION"] = sid
        assert cli.main(["status"]) == 0
        out = capsys.readouterr().out
        assert out.rstrip().endswith(line)
        assert cli.main(["--json", "status"]) == 0
        got = capsys.readouterr()
        json.loads(got.out)  # stdout stays parseable
        assert line in got.err
        assert cli.main(["kill", "ao-no-such-session"]) == 1  # a refusal carries it too
        assert line in capsys.readouterr().out
        assert cli.main(["inbox"]) == 0  # reading it clears the line from this very reply
        assert "[agentorc]" not in capsys.readouterr().out
    finally:
        os.environ.pop("AGENTORC_SESSION", None)
        cli.main(["kill", sid])


def test_the_unread_line_says_when_the_wake_budget_is_spent(capsys):
    """The ` (wake budget spent)` suffix, from the envelope's `mail` field (design §4.10)."""
    import argparse

    from sessionorc import client as clientmod

    args = argparse.Namespace(json=False)
    clientmod.last_mail = {"unread": 3, "wake_budget_spent": True}
    cli.unread_line(args)
    assert capsys.readouterr().out == "[agentorc] you have 3 unread messages — run ao inbox (wake budget spent)\n"
    clientmod.last_mail = None
    cli.unread_line(args)
    assert capsys.readouterr().out == ""


def test_the_same_line_says_what_outcomes_are_owed(capsys):
    """Design §4.10 *Outcomes* (TD-079): the person answered and is waiting to hear what came of
    it, so every `ao` reply says so — a line of its own beside the unread one, and present when
    there is no unread mail at all, which is the usual case for a session that is working."""
    import argparse

    from sessionorc import client as clientmod

    args = argparse.Namespace(json=False)
    clientmod.last_mail = {"unread": 0, "wake_budget_spent": False, "owed": ["m-1", "m-2"]}
    cli.unread_line(args)
    assert capsys.readouterr().out == "[agentorc] you owe 2 outcomes: m-1, m-2\n"
    clientmod.last_mail = {"unread": 1, "wake_budget_spent": False, "owed": ["m-1"]}
    cli.unread_line(args)
    out = capsys.readouterr().out.splitlines()
    assert out == ["[agentorc] you have 1 unread messages — run ao inbox", "[agentorc] you owe 1 outcome: m-1"]
    clientmod.last_mail = None


def test_ao_wait_against_an_agent_without_the_wait_rpc_says_so_and_exits(monkeypatch, capsys):
    """The running lead's agent may predate the RPC: one line, non-zero, never a loop."""
    from sessionorc import client as clientmod
    from sessionorc.client import AgentError

    async def unknown(self, method, **params):
        raise AgentError(f"unknown method {method!r}")

    async def enter(self):
        return self

    async def leave(self, *exc):
        return None

    # patched on the class in `sessionorc.client`, which is where `wait_rpc` builds its
    # connections since TD-086 item 1 — `ao wait` no longer opens one itself
    monkeypatch.setattr(clientmod.LocalClient, "call", unknown)
    monkeypatch.setattr(clientmod.LocalClient, "__aenter__", enter)
    monkeypatch.setattr(clientmod.LocalClient, "__aexit__", leave)
    assert cli.main(["wait", "--timeout", "1"]) == 1
    err = capsys.readouterr().err
    assert "predates the wait RPC" in err and err.count("\n") == 1


def test_progress_none_declares_out_of_work(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-053 step 1 (design §4.9a): `ao progress none --why` takes no reference, needs its reason,
    and shows in `ao status -v` and `--json`."""
    assert cli.main(["--json", "shell", "oow", "-d", str(tmp_path)]) == 0
    sid = json.loads(capsys.readouterr().out)["id"]
    monkeypatch.setenv("AGENTORC_SESSION", sid)
    assert cli.main(["progress", "none", "TD-001", "--why", "x"]) == 2
    assert "takes no reference" in capsys.readouterr().err
    assert cli.main(["progress", "claim", "TD-900", "--force"]) == 0  # TD-056: --force reaches the RPC
    capsys.readouterr()
    assert cli.main(["progress", "claim"]) == 2
    assert "needs a reference" in capsys.readouterr().err
    assert cli.main(["progress", "none"]) != 0
    assert "needs --why" in capsys.readouterr().err
    assert cli.main(["progress", "none", "--why", "no open entry I may pick"]) == 0
    assert capsys.readouterr().out.strip() == f"{sid}: out of work — no open entry I may pick"
    monkeypatch.delenv("AGENTORC_SESSION")
    assert cli.main(["status", "-v"]) == 0
    assert re.search(r"out of work \S+: no open entry I may pick", capsys.readouterr().out)
    assert cli.main(["--json", "status"]) == 0
    assert next(x for x in json.loads(capsys.readouterr().out) if x["id"] == sid)["out_of_work"]["why"]
    call_sync("kill", id=sid)


def test_doing_says_one_line_and_status_shows_it_with_its_age(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-074 step 1 (design §4.8 `doing`): `ao doing "<line>"` lands on this session's own record,
    the last line replaces the one before, `--clear` empties it, and `ao status -v` prints it with
    its age. A person with no session of their own is told, not guessed at."""
    assert cli.main(["--json", "shell", "doer", "-d", str(tmp_path)]) == 0
    sid = json.loads(capsys.readouterr().out)["id"]
    monkeypatch.setenv("AGENTORC_SESSION", sid)
    assert cli.main(["doing"]) == 2
    assert "needs a line" in capsys.readouterr().err
    assert cli.main(["doing", "--clear", "reading the ledger"]) == 2
    assert "takes no line" in capsys.readouterr().err
    assert cli.main(["doing", "reading the ledger for the next entry"]) == 0
    assert capsys.readouterr().out.strip() == f"{sid}: doing — reading the ledger for the next entry"
    assert cli.main(["doing", "opening", "the", "PR"]) == 0  # a line typed unquoted is still one line
    assert capsys.readouterr().out.strip() == f"{sid}: doing — opening the PR"
    assert cli.main(["status", "-v"]) == 0
    assert re.search(r"doing \S+ ago: opening the PR", capsys.readouterr().out)
    assert cli.main(["--json", "status"]) == 0
    assert next(x for x in json.loads(capsys.readouterr().out) if x["id"] == sid)["doing"]["text"] == "opening the PR"
    assert cli.main(["doing", "--clear"]) == 0
    assert capsys.readouterr().out.strip() == f"{sid}: doing cleared"
    assert call_sync("get", id=sid)["doing"] is None
    monkeypatch.delenv("AGENTORC_SESSION")
    assert cli.main(["doing", "nothing of mine to say"]) == 2
    assert "no session" in capsys.readouterr().err
    call_sync("kill", id=sid)


def test_progress_sends_force_only_when_asked(monkeypatch, capsys):
    """A host agent older than TD-056 refuses an unknown `force` keyword, and the CLI is routinely
    newer than the running agent until its restart: a plain claim must not send it (2026-09-17).
    The command now says *unset* by passing `None` and the client drops it from the envelope
    (TD-062 fix (a), tests/test_rpc_skew.py) — one rule for every command instead of one dance
    per new parameter."""
    sent = []

    def fake(method, **params):
        sent.append(params)
        return {"id": "ao-x", "progress": [], "lane": []}

    monkeypatch.setattr(cli, "call_sync", fake)
    monkeypatch.setenv("AGENTORC_SESSION", "ao-x")
    assert cli.main(["progress", "claim", "TD-900"]) == 0
    assert cli.main(["progress", "claim", "TD-900", "--force"]) == 0
    assert sent[0]["force"] is None and sent[1]["force"] is True


def test_progress_done_slice_keeps_the_claim(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-325 slice 2 (design §4.8, §4.9a *A slice is work done*): `ao progress done <ref> --pr <n>
    --slice` holds the PR as a slice of a claim still held and says the entry stays claimed; it is
    refused without a `--pr` or with any action but `done`, and on a reference with no claim the
    host agent's refusal names `ao progress claim`; `ao status -v` prints the slices."""
    assert cli.main(["--json", "new", "slicer", "-a", "shell", "-d", str(tmp_path)]) == 0
    sid = json.loads(capsys.readouterr().out)["id"]
    wait_state(sid, "idle")
    monkeypatch.setenv("AGENTORC_SESSION", sid)
    assert cli.main(["progress", "done", "TD-325", "--slice"]) == 2  # no --pr
    assert cli.main(["progress", "claim", "TD-325", "--pr", "9", "--slice"]) == 2  # not done
    assert "--slice goes with ao progress done TD-325 --pr <n>" in capsys.readouterr().err
    assert cli.main(["progress", "done", "TD-325", "--pr", "1025", "--slice"]) == 1  # no claim
    assert "ao progress claim TD-325 first" in capsys.readouterr().err
    assert cli.main(["progress", "claim", "TD-325"]) == 0
    capsys.readouterr()
    assert cli.main(["progress", "done", "TD-325", "--pr", "1025", "--slice"]) == 0
    assert "#1025 held as a slice of TD-325; TD-325 stays claimed" in capsys.readouterr().out
    assert cli.main(["progress", "done", "TD-325", "--pr", "1027", "--slice"]) == 0
    entry = call_sync("get", id=sid)["progress"][0]
    assert entry["status"] == "claimed" and [x["pr"] for x in entry["slices"]] == [1025, 1027]
    capsys.readouterr()
    assert cli.main(["status", "-v"]) == 0
    assert "slices: TD-325 #1025, #1027" in capsys.readouterr().out
    call_sync("kill", id=sid)


def test_progress_sends_slice_only_when_asked(monkeypatch, capsys):
    """As `force` (TD-062 fix (a)): a plain done sends no `slice`, so an agent older than TD-325
    still answers it."""
    sent = []

    def fake(method, **params):
        sent.append(params)
        return {"id": "ao-x", "progress": [], "lane": []}

    monkeypatch.setattr(cli, "call_sync", fake)
    monkeypatch.setenv("AGENTORC_SESSION", "ao-x")
    assert cli.main(["progress", "done", "TD-900", "--pr", "5"]) == 0
    assert cli.main(["progress", "done", "TD-900", "--pr", "5", "--slice"]) == 0
    assert sent[0]["slice"] is None and sent[1]["slice"] is True


def test_slices_line_marks_a_derived_slice_and_skips_a_finished_entry():
    """`ao status -v`'s *slices:* line (TD-325): a claim's slices, a derived one marked `~`; an
    entry no longer claimed prints none."""
    s = {
        "progress": [
            {
                "ref": "TD-1",
                "status": "claimed",
                "slices": [{"pr": 3, "source": "declared"}, {"pr": 4, "source": "derived"}],
            },
            {"ref": "TD-2", "status": "done", "pr": 9, "slices": [{"pr": 8, "source": "declared"}]},
        ]
    }
    assert cli.slices_line(s) == "TD-1 #3, #4~"
    assert cli.slices_line({"progress": []}) == ""


def test_whoami_and_identity_say_what_the_host_agent_sees(subprocess_agent, capsys, monkeypatch):
    """Design §4.8a: `ao whoami` is the connection's classification, `ao identity` the host's mode,
    tally and alarms. The suite's agent runs identity `off` (conftest), and both say so plainly;
    the alarm lines are checked against a canned reply, since `off` raises none."""
    assert cli.main(["whoami"]) == 0
    assert "identity is off on this host" in capsys.readouterr().out
    assert cli.main(["identity"]) == 0
    out = capsys.readouterr().out
    assert "identity off" in out and "no connection classified yet" in out and "no identity alarms" in out

    alarm = {"channel": "session ao-a", "claimed": "ao-b", "rpc": "msg", "count": 3, "at": "t1", "last": "t3"}
    bare = {"channel": "outside", "claimed": "", "rpc": "hook", "count": 1, "at": "t0", "last": "t0"}
    canned = {
        "identity": {
            "host": "kmaster",
            "mode": "observe",
            "detached_check": True,
            "tally": {"outside": 880, "session:ancestry": 4102, "session:sid": 37},
            "alarms": [bare],
            "sessions": {"ao-a": [alarm]},
        },
        "whoami": {"channel": "session", "session": "ao-a", "signal": "sid"},
    }
    monkeypatch.setattr(cli, "call_sync", lambda method, **_: canned[method])
    assert cli.main(["identity"]) == 0
    out = capsys.readouterr().out
    assert "kmaster: identity observe · detached-process check on" in out
    assert "session:ancestry 4,102" in out and "session:sid 37" in out
    assert "ALARM (no record): outside claimed no caller on hook ×1 (t0)" in out
    assert "ALARM ao-a: session ao-a claimed ao-b on msg ×3 (t1 … t3)" in out
    assert cli.main(["whoami"]) == 0
    assert capsys.readouterr().out.strip() == "session ao-a (by sid)"


def test_status_v_says_which_identity_mode_this_host_is_in_once(subprocess_agent, capsys, tmp_path):
    """design §4.8a: *`ao status -v` and the Org's teams line say which mode a host is in*, since
    *observe* is a host that is not yet protected — and beside it whether the detached-process
    check is on, so a host where it is off is not taken for one where it is on. **One line for the
    host**, never one per session, and nothing at all without `-v`."""
    assert cli.main(["shell", "idline", "-d", str(tmp_path)]) == 0
    sid = capsys.readouterr().out.split()[0]
    wait_state(sid, "idle")
    assert cli.main(["status"]) == 0
    assert "identity" not in capsys.readouterr().out
    assert cli.main(["status", "-v"]) == 0
    out = capsys.readouterr().out
    # the suite's agent runs `off` (conftest, §4.8a *Tests*), which the line must say as plainly
    # as it would say `observe`
    lines = [ln for ln in out.splitlines() if "identity" in ln]
    assert len(lines) == 1 and "identity off" in lines[0] and "detached-process check" in lines[0]
    assert "not enforcing it yet" in lines[0]
    assert cli.main(["kill", sid]) == 0


@pytest.mark.unit
def test_a_nodes_status_line_says_unreachable_only_when_it_is(monkeypatch, capsys):
    """TD-084, design §4.4a: `ao status` on a node printed *offline — … which is unreachable*
    whatever the link was doing. On 2026-09-20 it said that inside the contractmatch container
    while the home's journal showed the link **up**, and sent a reader looking for an outage that
    was not there. Three answers now, one per state the agent can be in — and a failed read is the
    third, not the bad one: not knowing is not the same as knowing it is down."""
    from agentorc import cli as climod

    monkeypatch.setattr(climod.hosts, "is_node", lambda: True)
    monkeypatch.setattr(climod.hosts, "home_name", lambda: "kmaster")
    monkeypatch.setattr(climod.hosts, "local_host", lambda: types.SimpleNamespace(name="contractmatch"))

    def answers(value):
        def call(method, **kw):
            if method == "host":
                if isinstance(value, Exception):
                    raise value
                return {"home_reachable": value}
            return []

        return call

    monkeypatch.setattr(climod, "call_sync", answers(True))
    assert climod.cmd_status(argparse.Namespace(json=False, verbose=False)) == 0
    up = capsys.readouterr().err
    assert "contractmatch is a node of kmaster, and the link is up" in up
    assert "this host's sessions only" in up and "unreachable" not in up and "offline" not in up

    monkeypatch.setattr(climod, "call_sync", answers(False))
    assert climod.cmd_status(argparse.Namespace(json=False, verbose=False)) == 0
    down = capsys.readouterr().err
    assert down.startswith("offline —") and "which is unreachable" in down and "no mail, no org" in down

    monkeypatch.setattr(climod, "call_sync", answers(RuntimeError("socket gone")))
    assert climod.cmd_status(argparse.Namespace(json=False, verbose=False)) == 0
    unknown = capsys.readouterr().err
    assert "is not known" in unknown and "unreachable" not in unknown and "offline" not in unknown
    assert "this host's sessions only" in unknown  # what is true whatever the link is doing

    # and the line is stderr, so `--json` is the records and nothing else (§4.4a)
    monkeypatch.setattr(climod, "call_sync", answers(True))
    assert climod.cmd_status(argparse.Namespace(json=True, verbose=False)) == 0
    out = capsys.readouterr()
    assert json.loads(out.out) == [] and "node of kmaster" in out.err


@pytest.mark.unit
def test_the_host_line_is_drawn_only_on_a_node(monkeypatch, capsys):
    """The home says nothing: its listing is the whole org's, and a line about *this host only*
    would be false there (design §4.4a)."""
    from agentorc import cli as climod

    monkeypatch.setattr(climod.hosts, "is_node", lambda: False)
    monkeypatch.setattr(climod, "call_sync", lambda method, **kw: [])
    assert climod.cmd_status(argparse.Namespace(json=False, verbose=False)) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.unit
def test_exit_three_says_restarted_when_one_answers_and_never_tells_a_session_to_start_one(monkeypatch, capsys):
    """TD-086 item 2, `ao --skill`'s Never list. One line said two different things: the socket
    could not be opened, *and* a call's reply never came because the connection closed under it —
    which a promote causes (TD-062), three times on the evening of 2026-09-20 to a lead blocked in
    `ao wait`. Both printed *start it with: agentorc-agent serve*: wrong in fact, because the agent
    was `active` again at once, and wrong for a session, which its skill forbids to start one.

    So the CLI asks again rather than reading the exception's words, and the hint is never printed
    to a session whichever answer it gets. The exit code stays 3 in all three: what the caller
    could not do, it could not do."""
    from agentorc import cli as climod
    from sessionorc.client import AgentUnavailable

    def boom(args):
        raise AgentUnavailable("host agent closed the connection")

    args = argparse.Namespace(json=False, fn=boom, id=None)

    # (a) an agent answers now: it was restarting, and *run it again* is the whole of the advice
    monkeypatch.setattr(climod, "_agent_answers", lambda: True)
    monkeypatch.delenv("AGENTORC_SESSION", raising=False)
    assert climod._run(args) == 3
    err = capsys.readouterr().err
    assert "restarted under this command — run it again" in err and "agentorc-agent serve" not in err

    # …and a session gets the same sentence: it is the true one, and it names nothing to start
    monkeypatch.setenv("AGENTORC_SESSION", "ao-x-1")
    assert climod._run(args) == 3
    assert "agentorc-agent serve" not in capsys.readouterr().err

    # (b) nothing answers, and the caller is a session: told to stop, in its skill's own words
    monkeypatch.setattr(climod, "_agent_answers", lambda: False)
    assert climod._run(args) == 3
    err = capsys.readouterr().err
    assert "stop here; a session never starts one" in err and "agentorc-agent serve" not in err

    # (c) nothing answers, and the caller is a person: the hint is theirs, and only theirs — with the
    # ancestry probe stubbed, since this suite may itself be running inside an agentorc pane
    monkeypatch.delenv("AGENTORC_SESSION", raising=False)
    monkeypatch.setattr(climod, "_session_by_ancestry", lambda: None)
    assert climod._run(args) == 3
    assert "start it with: agentorc-agent serve" in capsys.readouterr().err

    # (d) TD-089: the variable is gone but a process above this one was started in a session's pane
    monkeypatch.setattr(climod, "_session_by_ancestry", lambda: "ao-x-1")
    assert climod._run(args) == 3
    err = capsys.readouterr().err
    assert "stop here; a session never starts one" in err and "agentorc-agent serve" not in err

    # --json says which it was without prose, for a caller that parses (TD-030)
    monkeypatch.setattr(climod, "_agent_answers", lambda: True)
    assert climod._run(argparse.Namespace(json=True, fn=boom, id=None)) == 3
    got = json.loads(capsys.readouterr().out)
    assert got["restarted"] is True and got["error"] == "host agent closed the connection"


def test_a_session_is_found_by_its_ancestors_environment_when_its_own_is_gone(tmp_path):
    """TD-089, design §4.8a *With no host agent to ask*: the launch sets `AGENTORC_SESSION` on the
    pane's first process, so a process that lost it still has an ancestor started with it. The walk
    reads `/proc/<pid>/stat` for the parent (a `comm` with spaces and parentheses included) and
    `/proc/<pid>/environ` for the variable; it stops at init, at a process it may not read, at a
    broken chain and at the hop bound, and every failure is *no session*."""
    from agentorc import cli as climod

    def proc(pid, ppid, env=None, comm="sh"):
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} {pid} {pid} 0 -1 4194560\n")
        if env is not None:
            (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in env.items()) + b"\0")

    proc(40, 30, {"PATH": "/bin"}, comm="ao (a) b")  # ourselves: our own environ is os.environ's, not read
    proc(30, 20, {"PATH": "/bin"})  # a hook's shell that scrubbed the variable
    proc(20, 10, {"AGENTORC_SESSION": "ao-agentorc-w-1", "PATH": "/bin"}, comm="claude")
    proc(10, 1, {"PATH": "/bin"}, comm="tmux: server")
    assert climod._session_by_ancestry(tmp_path, pid=40) == "ao-agentorc-w-1"
    assert climod._session_by_ancestry(tmp_path, pid=10) is None  # the chain reaches init: a person's
    proc(50, 60, {"PATH": "/bin"})
    proc(60, 1)  # no environ file: another user's process, so the chain is not ours
    assert climod._session_by_ancestry(tmp_path, pid=50) is None
    proc(70, 99, {})  # the parent exited under the walk
    assert climod._session_by_ancestry(tmp_path, pid=70) is None
    proc(80, 81, {"AGENTORC_SESSION": ""})
    proc(81, 80, {"AGENTORC_SESSION": ""})  # an empty value is not a session, and a loop is bounded
    assert climod._session_by_ancestry(tmp_path, pid=80) is None


@pytest.mark.unit
def test_the_probe_is_one_ping_it_never_raises_and_it_cannot_hang(monkeypatch, tmp_path):
    """`_agent_answers` only ever chooses which sentence to print, so it must not raise, must not
    turn a missing socket into a traceback on the way out of a command that already failed, and —
    the one that is not obvious — **must not hang**.

    Nothing in `sessionorc.client` times a read out, so an agent that is *accepting connections but
    not yet serving* (a restarting unit, for a moment — the state this entry is about) would leave
    an unbounded probe in `readline()` for ever, turning a deterministic exit 3 into a command that
    never returns (review of PR #300). The bound is asserted against a real socket that accepts and
    says nothing, because that is the only way to prove it."""
    import asyncio as aio
    import socket
    import time

    from agentorc import cli as climod

    # nothing is listening: immediate, and a `no`
    monkeypatch.setattr(climod.clientmod.paths, "socket_path", lambda: tmp_path / "nothing.sock")
    assert climod._agent_answers() is False

    # something accepts and never answers: bounded by `PROBE_TIMEOUT`, and still a `no`
    sock = tmp_path / "deaf.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock))
    server.listen(1)
    try:
        monkeypatch.setattr(climod.clientmod.paths, "socket_path", lambda: sock)
        monkeypatch.setattr(climod, "PROBE_TIMEOUT", 0.3)
        began = time.monotonic()
        assert climod._agent_answers() is False
        assert time.monotonic() - began < 3.0  # bounded at all, with room for a slow machine
    finally:
        server.close()

    # and a `pong` is a `yes`, over one call
    calls = []

    class Stub:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def call(self, method, **kw):
            calls.append(method)
            return "pong"

    monkeypatch.setattr(climod.clientmod, "LocalClient", Stub)
    assert climod._agent_answers() is True and calls == ["ping"]
    assert aio.get_event_loop_policy() is not None  # the probe left no loop of its own behind


def test_progress_restart_is_the_third_ending(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-083 step 1 (design §4.9a *A run that ends with work left*): a worker that ends a long
    run **on purpose**, with work still on the ledger, had no word for it — it was not out of
    work, its `/exit` did not leave, and a team sat parked for ninety minutes until a person
    acted. `ao progress restart --why` says it: my run is over and my lane is not.

    It is the session's own word (§9 invariant 14), refused without a reason, refused while an
    outcome is owed — the fresh run does not carry the conversation the debt was made in — and it
    and `none` refuse each other. A later declared claim takes it back."""
    assert cli.main(["--json", "shell", "again", "-d", str(tmp_path)]) == 0
    sid = json.loads(capsys.readouterr().out)["id"]
    monkeypatch.setenv("AGENTORC_SESSION", sid)

    assert cli.main(["progress", "restart"]) == 1
    assert "needs --why" in capsys.readouterr().err
    assert cli.main(["progress", "restart", "TD-1", "--why", "x"]) == 2
    assert "takes no reference" in capsys.readouterr().err

    assert cli.main(["progress", "restart", "--why", "context is long; the ledger still has work"]) == 0
    out = capsys.readouterr().out.strip()
    assert out == (
        f"{sid}: restart wanted (early — your controller will put it on the board, not act on it) "
        "— context is long; the ledger still has work\n  early: nothing reported done this run"
    )
    rec = call_sync("get", id=sid)
    assert rec["restart_wanted"]["why"] == "context is long; the ledger still has work"
    # a record started seconds ago: the word stands, and it is marked early (§4.9a)
    assert rec["restart_wanted"]["early"] is True

    # the two endings refuse each other
    assert cli.main(["progress", "none", "--why", "nothing left"]) == 1
    assert "already wants a restart" in capsys.readouterr().err

    # and a claim takes it back: the session went on after all
    assert cli.main(["progress", "claim", "TD-083"]) == 0
    capsys.readouterr()
    assert call_sync("get", id=sid)["restart_wanted"] is None

    # nobody else may declare it
    monkeypatch.delenv("AGENTORC_SESSION")
    try:
        call_sync("progress", id=sid, status="restart", why="not mine to say", caller="ao-someone")
        raise AssertionError("another session declared a restart")
    except AgentError as e:
        assert "only" in str(e) and "own word" in str(e)
    call_sync("kill", id=sid)


def test_inbox_sent_and_the_asks_waiting_line(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-075 step 4 (design §4.9b): `ao inbox --sent` prints this session's own sent mail, and
    `ao status -v` prints `asks waiting: N` under a record with open questions addressed to it."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    asker = call_sync("create", name="aw-asker", dir=str(tmp_path / "a"), adapter="shell", team="aw-t")["id"]
    seat = call_sync("create", name="aw-seat", dir=str(tmp_path / "b"), adapter="shell", team="aw-t")["id"]
    monkeypatch.setenv("AGENTORC_SESSION", asker)
    assert cli.main(["msg", seat, "squash or rebase?", "--kind", "ask"]) == 0
    capsys.readouterr()
    assert cli.main(["inbox", "--sent"]) == 0
    out = capsys.readouterr().out
    assert f"{asker}: 1 sent" in out and f"→ {seat}" in out and "squash or rebase?" in out
    assert cli.main(["status", "-v"]) == 0
    assert "asks waiting: 1" in capsys.readouterr().out
    for sid in (asker, seat):
        call_sync("kill", id=sid)


def test_msg_pass_up_and_the_inbox_line_that_shows_it(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-075 step 3 (design §4.9b): `ao msg --pass-up <id> --recommend "<line>"` hands a question
    to the person with the recommendation first among its answers; it takes no text and needs the
    recommendation; the person's `ao inbox` shows the asker's question, labelled as passed up by
    the passer with its recommendation, and numbers the answers for `--pick`."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    asker = call_sync("create", name="up-asker", dir=str(tmp_path / "a"), adapter="shell", team="up-t")["id"]
    passer = call_sync("create", name="up-passer", dir=str(tmp_path / "b"), adapter="shell", team="up-t")["id"]
    monkeypatch.setenv("AGENTORC_SESSION", asker)
    assert cli.main(["--json", "msg", passer, "delete the old branch?", "--kind", "ask"]) == 0
    q = json.loads(capsys.readouterr().out)["entry"]
    monkeypatch.setenv("AGENTORC_SESSION", passer)
    assert cli.main(["msg", "--pass-up", q["id"], "some text"]) == 2
    assert "leave the text out" in capsys.readouterr().err
    assert cli.main(["msg", "--pass-up", q["id"]]) == 2
    assert "--recommend" in capsys.readouterr().err
    assert cli.main(["msg", "--pass-up", q["id"], "--recommend", "keep it", "--answer", "delete it"]) == 0
    out = capsys.readouterr().out
    assert f"{q['id']} passed up → person, recommending: keep it" in out and "  2. delete it" in out
    monkeypatch.delenv("AGENTORC_SESSION")
    assert cli.main(["inbox"]) == 0
    out = capsys.readouterr().out
    assert "delete the old branch?" in out and f"passed up by {passer}, who recommends: keep it" in out
    assert "  1. keep it" in out and "  2. delete it" in out
    for sid in (asker, passer):
        call_sync("kill", id=sid)


def test_msg_source_and_the_answered_for_you_lines(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-075 step 2 (design §4.9b): `ao msg --reply-to <id> --source "<where>"` sends a reply
    answered from the record and says the person is told; the asker's `ao inbox` prints the source,
    and the person's prints the FYI — who asked what, who answered, from where."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    asker = call_sync("create", name="src-asker", dir=str(tmp_path / "a"), adapter="shell", team="src-t")["id"]
    answerer = call_sync("create", name="src-answerer", dir=str(tmp_path / "b"), adapter="shell", team="src-t")["id"]
    monkeypatch.setenv("AGENTORC_SESSION", asker)
    assert cli.main(["--json", "msg", answerer, "is the seat counted in a wind-down?", "--kind", "ask"]) == 0
    q = json.loads(capsys.readouterr().out)["entry"]
    monkeypatch.setenv("AGENTORC_SESSION", answerer)
    assert cli.main(["msg", "--reply-to", q["id"], "no, never", "--source", "design §4.9b, the seat"]) == 0
    out = capsys.readouterr().out
    assert "source: design §4.9b, the seat" in out and "the person is told: answered for you" in out
    monkeypatch.setenv("AGENTORC_SESSION", asker)
    assert cli.main(["inbox"]) == 0
    assert "  source: design §4.9b, the seat" in capsys.readouterr().out
    monkeypatch.delenv("AGENTORC_SESSION")
    assert cli.main(["inbox"]) == 0
    out = capsys.readouterr().out
    assert f"answered for you — {asker} asked: is the seat counted in a wind-down?" in out
    assert f"answered by {answerer} from design §4.9b, the seat; a reply here goes to the asker" in out
    for sid in (asker, answerer):
        call_sync("kill", id=sid)


def _iso(t: datetime.datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def test_gate_reserves_parse_and_the_line_reads_as_the_design_writes_it():
    """`ao gate` (design §4.7, TD-100 slice 2): `30` flat, `10/day` per day, empty clears; the line
    names the reserve, the line and, for a per-day reserve, when it moves."""
    from agentorc.cli import _gate_line, _reserve

    assert (_reserve("30"), _reserve("10/day"), _reserve("")) == (30, {"per_day": 10}, None)
    for bad in ("30%", "ten", "-5", "1.5/day"):
        with pytest.raises(AgentError, match="whole percent"):
            _reserve(bad)
    rows = [
        {"label": "5h", "reserve": 30, "line": 70, "pct": 12, "next": None},
        {
            "label": "week",
            "reserve": {"per_day": 10},
            "line": 60,
            "pct": 55,
            "resets": _iso(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=3, hours=1)),
            "next": "2026-09-24T13:00:00Z",
        },
        {"label": "x", "reserve": {"per_day": 5}, "line": None, "pct": 1, "next": None},
    ]
    line = _gate_line("grind", rows)
    assert line.startswith("grind · 5h 30 → line 70%, now 12% · week 10/day → line 60%, now 55% (4 days left, moves ")
    assert "x 5/day → no line" in line
    assert _gate_line("grind", [{"label": "5h", "reserve": 30, "unread": True}]) == "grind · 5h 30 → no reading yet"
    assert _gate_line("", []) == "(default)"
    # a window past its reset (TD-233): no number, so no line to be over
    past = {"label": "week", "reserve": 5, "line": 95, "pct": 99, "unknown": "reset", "next": None}
    assert _gate_line("grind", [past]) == "grind · week 5 → unknown since its reset (was 99%)"


def test_ao_gate_says_how_old_each_reading_is():
    """design §4.7 `ao gate` (TD-230, TD-233 slice 1): each window read from a reading ends with its
    age and source, *· read 6h ago (asked)*; a reading with no time on it, or none, says nothing."""
    from agentorc.cli import _gate_line, _gate_read

    six = _iso(datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=6, minutes=1))
    assert _gate_read({"fetched": six}) == " · read 6h ago (asked)"
    assert _gate_read({"fetched": six, "source": "reported"}) == " · read 6h ago (reported)"
    assert _gate_read({"fetched": "x"}) == _gate_read({"fetched": "2026-09-20T20:00:00"}) == _gate_read(None) == ""
    rows = [{"label": "5h", "reserve": 30, "line": 70, "pct": 12, "next": None}]
    got = _gate_line("grind", rows, _gate_read({"fetched": six}))
    assert got == "grind · 5h 30 → line 70%, now 12% · read 6h ago (asked)"
    unread = [{"label": "5h", "reserve": 30, "unread": True}]
    assert _gate_line("grind", unread, " · read 1m ago (asked)") == "grind · 5h 30 → no reading yet"


def test_ao_gate_prints_a_projection_and_a_window_with_no_rate():
    """design §4.7 `ao gate` (TD-233 slice 4): a window past `usage.max_age` prints the reading it
    held, then *· projected 96%*; one with no rate to project by says so, and pauses nothing."""
    from agentorc.cli import _gate_line, _max_age_said

    pr = {"label": "week", "reserve": 5, "line": 95, "pct": 96.0, "next": None,
          "projected": {"from": 88, "rate": 1.33, "age": 21600}}  # fmt: skip
    got = _gate_line("grind", [pr], " · read 6h ago (asked)")
    assert got == "grind · week 5 → line 95%, now 88% · read 6h ago (asked) · projected 96%"
    no_rate = {"label": "week", "reserve": 5, "line": 95, "pct": 88, "next": None, "unknown": "rate", "age": 7200}
    got = _gate_line("grind", [no_rate], " · read 2h ago (asked)")
    assert got == "grind · week 5 → line 95%, now 88% · read 2h ago (asked) · no rate to project by"
    assert _max_age_said("off") == "max_age off: the gate never projects a reading"
    assert _max_age_said("90m").startswith("max_age 90m: ")


def test_ao_gate_max_age_sets_the_setting(subprocess_agent, capsys, monkeypatch):
    """`ao gate --max-age` (design §4.7, §5 `usage.max_age`, TD-233): an age or `off` lands in
    settings.yml through `set_settings`, `default` clears it back to the hour, a bad age is the
    host agent's refusal, and a session is refused as for the reserves."""
    from sessionorc import settings as settings_mod

    monkeypatch.delenv("AGENTORC_SESSION", raising=False)
    try:
        assert cli.main(["gate", "--max-age", "90m"]) == 0
        assert capsys.readouterr().out.startswith("max_age 90m: ")
        assert settings_mod.load()["usage"] == {"max_age": "90m"}
        assert cli.main(["--json", "gate", "--max-age", "off"]) == 0
        assert json.loads(capsys.readouterr().out)["usage"] == {"max_age": "off"}
        assert cli.main(["--json", "gate"]) == 0
        assert json.loads(capsys.readouterr().out)["max_age"] == "off"
        assert cli.main(["gate", "--max-age", "default"]) == 0
        assert "max_age 1h" in capsys.readouterr().out and "usage" not in settings_mod.load()
        assert cli.main(["gate", "--max-age", "2m"]) != 0
        assert "from 5m to 7d" in capsys.readouterr().err
        assert cli.main(["gate", "--max-age", "3h", "grind", "5h=200"]) != 0  # a bad reserve writes neither
        capsys.readouterr()
        assert settings_mod.load().get("usage") is None
        assert cli.main(["gate", "--max-age", "2h", "grind", "5h=30"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("max_age 2h: ") and "grind · 5h 30" in out
        monkeypatch.setenv("AGENTORC_SESSION", "s-nobody")
        assert cli.main(["gate", "--max-age", "off"]) != 0
        assert settings_mod.load()["usage"] == {"max_age": "2h"}
    finally:
        monkeypatch.delenv("AGENTORC_SESSION", raising=False)
        assert cli.main(["gate", "--max-age", "default", "grind", "5h="]) == 0  # the agent is shared: leave it bare
    assert "usage" not in settings_mod.load() and not settings_mod.load().get("usage_gate")


def test_ao_gate_sets_shows_and_is_a_persons(subprocess_agent, tmp_path, capsys, monkeypatch):
    """`ao gate` end to end: the reserves land in settings.yml through `set_settings`, `ao gate`
    shows them, a session's `ao gate` is refused by the host agent, and `ao new --unattended`
    carries the pause and resume texts onto the record."""
    from agentorc import teams

    monkeypatch.delenv("AGENTORC_SESSION", raising=False)
    assert cli.main(["gate"]) == 0
    assert "no usage gate" in capsys.readouterr().out
    assert cli.main(["gate", "grind", "5h=30", "week=10/day"]) == 0
    out = capsys.readouterr().out
    assert "grind · 5h 30 → no reading yet" in out and "not checked" in out  # no reading for `grind` here
    assert cli.main(["gate"]) == 0
    assert "grind · 5h 30 → no reading yet · week 10/day → no reading yet" in capsys.readouterr().out
    assert cli.main(["--json", "gate"]) == 0
    assert json.loads(capsys.readouterr().out)["profiles"]["grind"]["reserves"] == {"5h": 30, "week": {"per_day": 10}}
    assert cli.main(["gate", "grind", "5h=", "week="]) == 0
    assert "no reserves" in capsys.readouterr().out
    assert cli.main(["--json", "new", "w", "-a", "shell", "-d", str(tmp_path), "--unattended"]) == 0
    rec = json.loads(capsys.readouterr().out)
    assert (rec["pause_prompt"], rec["resume_prompt"]) == (teams.PAUSE_PROMPT, teams.RESUME_PROMPT)
    monkeypatch.setenv("AGENTORC_SESSION", rec["id"])
    assert cli.main(["gate", "grind", "5h=30"]) != 0
    assert "a person's own" in capsys.readouterr().err
    monkeypatch.delenv("AGENTORC_SESSION")
    assert cli.main(["kill", rec["id"]]) == 0


def test_ao_team_until_and_reserve_and_ao_settings(subprocess_agent, tmp_path, capsys, monkeypatch):
    """TD-146 slice 3 end to end (design §4.7): `ao team until` and `ao team reserve` write
    `teams.<team>` through `set_settings`, a team the org does not define is refused naming the
    defined ones, `ao settings` prints each key with what it makes, and `--where` names the files."""
    from sessionorc import paths

    monkeypatch.delenv("AGENTORC_SESSION", raising=False)
    monkeypatch.chdir(tmp_path)
    (paths.home() / "org.yml").write_text(
        f"projects:\n  ao: {{repos: {{ao: {{kmaster: {tmp_path}}}}}}}\n"
        "teams:\n  ao-grind:\n    projects: [ao]\n    manager: {role: manager, name: m}\n"
        "    members: [{role: grinder, name: g}]\n"
    )
    assert cli.main(["team", "until", "nope", "+2h"]) != 0
    assert "no team 'nope': the org defines ao-grind" in capsys.readouterr().err
    assert cli.main(["team", "until", "ao-grind", "+2h"]) == 0
    assert capsys.readouterr().out.startswith("ao-grind: members stop ")
    assert cli.main(["team", "reserve", "ao-grind", "10"]) == 0
    assert "reserve priority +10" in capsys.readouterr().out
    assert cli.main(["settings"]) == 0
    out = capsys.readouterr().out
    assert "teams:\n  ao-grind: members stop " in out and "reserve priority +10" in out
    assert "open_in: vscode (default)" in out and "every repo promotes by hand" in out
    assert cli.main(["--json", "settings"]) == 0
    assert json.loads(capsys.readouterr().out)["teams"]["ao-grind"]["reserve"] == 10
    assert cli.main(["team", "until", "ao-grind", "--clear"]) == 0
    assert cli.main(["team", "reserve", "ao-grind", "0"]) == 0
    assert "no stop time · no reserve priority" in capsys.readouterr().out.splitlines()[-1]
    # `ao team on-work` (§6 rule 8, TD-227 slice 3): the key written, said in the page's words
    assert cli.main(["team", "on-work", "nope", "start"]) != 0
    assert "no team 'nope'" in capsys.readouterr().err
    assert cli.main(["team", "on-work", "ao-grind", "start"]) == 0
    assert capsys.readouterr().out.strip().endswith("when work appears: start the team")
    assert cli.main(["--json", "settings"]) == 0
    assert json.loads(capsys.readouterr().out)["teams"]["ao-grind"] == {"on_work": "start"}
    with pytest.raises(SystemExit):
        cli.main(["team", "on-work", "ao-grind", "maybe"])
    capsys.readouterr()
    assert cli.main(["team", "on-work", "ao-grind", "ask"]) == 0
    assert "when work appears: ask me" in capsys.readouterr().out
    # `ao team balance` (§6 *Balance*, TD-239 slice 4): a line not named is left as it was
    assert cli.main(["team", "balance", "nope", "--prs", "8"]) != 0
    assert "no team 'nope'" in capsys.readouterr().err
    assert cli.main(["team", "balance", "ao-grind"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("ao-grind: no balance line") and "no live member" in out
    assert "  reader's queue: nothing waiting (no line)" in out and "over its line" not in out
    assert cli.main(["team", "balance", "ao-grind", "--prs", "8", "--review", "on"]) == 0
    assert capsys.readouterr().out.startswith("ao-grind: balance prs 8, review\n")
    assert cli.main(["team", "balance", "ao-grind", "--oldest", "2d", "--review", "off"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("ao-grind: balance prs 8, oldest 2d\n") and "(line 2d)" in out
    assert cli.main(["--json", "team", "balance", "ao-grind"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["balance"] == {"prs": 8, "oldest": "2d"} and got["mark"] is None and got["now"]["members"] == 0
    assert cli.main(["settings"]) == 0
    assert "balance prs 8, oldest 2d" in capsys.readouterr().out
    assert cli.main(["team", "balance", "ao-grind", "--oldest", "soon"]) != 0  # the home's parser refuses it
    assert "balance.oldest is a duration" in capsys.readouterr().err
    assert cli.main(["team", "balance", "ao-grind", "--clear", "--prs", "3"]) != 0
    capsys.readouterr()
    assert cli.main(["team", "balance", "ao-grind", "--clear"]) == 0
    assert capsys.readouterr().out.startswith("ao-grind: no balance line")
    assert cli.main(["team", "balance", "ao-grind", "--review", "off"]) != 0  # nothing would be left
    assert "leaves ao-grind no line" in capsys.readouterr().err
    assert cli.main(["settings", "--where"]) == 0
    where = capsys.readouterr().out
    assert "settings.yml" in where and "at the host agent's start" in where and "ao service install" in where


@pytest.mark.unit
def test_status_v_marks_a_pr_that_is_no_longer_open_and_never_guesses(tmp_path, monkeypatch, capsys):
    """Design §4.7 `ao status -v`, §4.5a card **report line** → **the PR's mark** (TD-182, built by
    TD-193): the home's repo readings are read once per call, and a PR they hold as merged carries
    the word; the read refused (a node offline), the line is printed unmarked. `--json` is the
    records as they are."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    monkeypatch.setattr(cli.hosts, "is_node", lambda: False)
    rec = {
        "id": "ao-x-g",
        "name": "g",
        "state": "exited",
        "confidence": "hook",
        "since": "2026-09-27T10:00:00Z",
        "adapter": "shell",
        "repo": str(tmp_path / "r"),
        "progress": [{"ref": "TD-066", "status": "done", "pr": 158, "source": "declared"}],
    }
    reading = {"prs": {"open": [], "recent": [{"number": 158, "state": "merged"}]}}
    calls: list[str] = []

    def fake(method, **params):
        calls.append(method)
        if method == "list":
            return [rec]
        if method == "repos":
            if refused:
                raise AgentError("repos: the home is not in reach")
            return {str(tmp_path / "r"): reading}
        raise AgentError(f"unknown method {method}")

    monkeypatch.setattr(cli, "call_sync", fake)
    refused = False
    assert cli.main(["status", "-v"]) == 0
    assert "report: TD-066 → #158 merged · 1/1 done" in capsys.readouterr().out
    assert calls.count("repos") == 1  # once per call, not per record
    refused = True
    assert cli.main(["status", "-v"]) == 0
    assert "report: TD-066 → #158 · 1/1 done" in capsys.readouterr().out  # unmarked, never guessed
    assert cli.main(["--json", "status"]) == 0
    assert json.loads(capsys.readouterr().out) == [rec]


def test_status_v_says_a_brief_changed_with_its_files(tmp_path, monkeypatch, capsys):
    """Design §4.5a **brief changed**, §6 rule 7 (TD-217 slice 3): `ao status -v` prints
    `brief: changed <when> (<files>)`, and `--json` carries the field as it is."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    monkeypatch.setattr(cli.hosts, "is_node", lambda: False)
    rec = {
        "id": "ao-x-m",
        "name": "m",
        "state": "idle",
        "confidence": "hook",
        "since": "2026-09-27T10:00:00Z",
        "adapter": "shell",
        "brief_changed": {"at": "2026-09-27T10:00:00Z", "paths": ["/r/docs/briefs/m.md", "/v/briefs/manager.md"]},
    }

    def fake(method, **params):
        if method == "list":
            return [rec]
        raise AgentError(f"unknown method {method}")

    monkeypatch.setattr(cli, "call_sync", fake)
    assert cli.main(["status", "-v"]) == 0
    assert re.search(r"brief:  changed \S+ ago \(m\.md, manager\.md\)", capsys.readouterr().out)
    assert cli.main(["--json", "status"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["brief_changed"] == rec["brief_changed"]


def test_ao_restart_prints_the_new_records_line_and_a_refusal_in_the_agents_words(monkeypatch, capsys):
    """TD-250 slice 3 (design §4.7 `ao restart <session>`, §6 rule 2 *A person's restart*): the
    `restart` RPC from a terminal — the reply printed as `ao status` prints a record, a refusal the
    host agent's own sentence and exit 1 — and the manager's board line names the press."""
    new = {
        "id": "ao-x-w",
        "name": "w",
        "state": "working",
        "confidence": "hook",
        "since": "2026-10-01T10:00:00Z",
        "adapter": "claude-code",
        "unattended": True,
        "restarts": [{"at": "2026-10-01T10:00:00Z", "why": "person"}],
    }
    calls = []

    def fake(method, **params):
        calls.append((method, params))
        if method == "list":
            return [new]
        if params["id"] == "ao-x-dirty":
            raise AgentError("w has work left: 2 uncommitted")
        return new

    monkeypatch.setattr(cli, "call_sync", fake)
    assert cli.main(["restart", "ao-x-w"]) == 0
    assert calls == [("restart", {"id": "ao-x-w"})]
    out = capsys.readouterr().out
    assert re.fullmatch(r"ao-x-w  working\s+\S+  claude-code \[unattended\]\n", out)
    assert cli.main(["status"]) == 0
    assert out.split() == capsys.readouterr().out.split(), "the line `ao status` prints for it"
    assert cli.main(["--json", "restart", "ao-x-w"]) == 0
    assert json.loads(capsys.readouterr().out)["restarts"][0]["why"] == "person"
    assert cli.main(["restart", "ao-x-dirty"]) == 1
    assert "w has work left: 2 uncommitted" in capsys.readouterr().err
    manager = (pathlib.Path(__file__).parents[1] / "src/agentorc/briefs/manager.md").read_text()
    assert "on the board once — the person's press is the Inbox row's Restart, or `ao restart <id>`." in manager


def test_the_presets_say_what_over_its_line_means():
    """TD-239 slice 5 (design §6 *Balance*): a worker refused *over its line* takes nothing new, ends
    its turn and never declares `none`; the manager logs one line and treats nobody as crashed."""
    briefs = pathlib.Path(__file__).parents[1] / "src/agentorc/briefs"
    grinder = (briefs / "grinder.md").read_text()
    assert "**Over its line** (design §6 *Balance*)" in grinder and "`--force` does not pass it" in grinder
    assert "a branch cut for a new entry is no way round it" in grinder and "Never declare `none` on it" in grinder
    assert grinder.index("A claim is a lease") < grinder.index("**Over its line**") < grinder.index("**Out of work**")
    hunter = (briefs / "hunter.md").read_text()
    assert "**Over its line** (design §6 *Balance*)" in hunter and "Never declare `none` on it" in hunter
    assert "over its line" not in (briefs / "auditor.md").read_text().lower()  # a seat is never refused
    manager = (briefs / "manager.md").read_text()
    assert "**A team over its line** (design §6 *Balance*) is neither crashed nor finished" in manager
    assert "one line in your round log" in manager and "do not restart or wind them down" in manager


def test_status_v_says_who_closed_a_record_in_the_cards_words(monkeypatch, capsys):
    """design §4.7 (TD-262, built by TD-265): `ao status -v` prints a closed record's closer in the
    card's words — one function, `ending.closer_words`, for both — and a session by its name."""
    at = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).isoformat()

    def rec(sid, name, **kw):
        base = {"id": sid, "name": name, "state": "closed", "confidence": "hook", "since": at, "adapter": "shell"}
        return {**base, "closed_at": at, **kw}

    records = [
        rec("ao-r-manager-1", "manager-1", closer={"by": "ao-r-manager-1", "at": at}),
        rec("ao-r-grinder-1", "grinder-1", closer={"by": "ao-r-manager-1", "at": at}),
        rec("ao-r-grinder-2", "grinder-2", closer={"by": "tick", "why": "finished", "at": at}),
        rec("ao-r-old", "old"),
        {**rec("ao-r-live", "live"), "state": "idle", "closer": None},
    ]
    monkeypatch.setattr(cli, "call_sync", lambda method, **_: records if method == "list" else {})
    assert cli.main(["status", "-v"]) == 0
    out = capsys.readouterr().out
    assert "closed itself 5m ago" in out and "closed by manager-1 5m ago" in out
    assert "closed by the tick · team finished 5m ago" in out
    assert re.search(r"^\s+closed 5m ago$", out, re.M)  # no closer: bare *closed*
    assert len(re.findall(r"^\s+closed.* ago$", out, re.M)) == 4  # the idle record has no such line


def test_status_v_says_a_records_restarts_and_the_caches_in_its_words(monkeypatch, capsys):
    """design §4.7 and §4.10 *A lapsed cache is started again, not rung* (TD-467): `ao status -v` prints
    the record's `restarts`, newest first — the doorbell's as *cache lapsed · idle 5h · 191k* — three of
    them and *+n earlier*; and a record the cache restart closed reads *closed by the tick · cache lapsed*."""
    now = datetime.datetime.now(datetime.UTC)
    at = (now - datetime.timedelta(minutes=5)).isoformat()
    older = (now - datetime.timedelta(hours=3)).isoformat()
    base = {"confidence": "hook", "since": at, "adapter": "shell"}
    restarts = [
        {"at": older, "why": "start"},
        {"at": older, "why": "brief"},
        {"at": older, "why": "cache", "idle": 2.4, "context": 150_000, "error": "close: gone"},
        {"at": older, "why": "person"},
        {"at": at, "why": "cache", "idle": 5.4, "context": 191_000},
    ]
    records = [
        {**base, "id": "ao-r-g", "name": "g", "state": "idle", "restarts": restarts},
        {
            **base,
            "id": "ao-r-c",
            "name": "c",
            "state": "closed",
            "closed_at": at,
            "closer": {"by": "tick", "why": "cache", "at": at},
        },
        {**base, "id": "ao-r-n", "name": "n", "state": "idle", "restarts": []},
    ]
    monkeypatch.setattr(cli, "call_sync", lambda method, **_: records if method == "list" else {})
    assert cli.main(["status", "-v"]) == 0
    out = capsys.readouterr().out
    lines = [ln.strip() for ln in out.splitlines() if "restarts:" in ln]
    assert lines == [
        "restarts: cache lapsed · idle 5h · 191k, 5m ago; person, 3h ago; cache lapsed · idle 2h · 150k · failed, "
        "3h ago; +2 earlier"
    ]  # the record with none prints no line
    assert "closed by the tick · cache lapsed 5m ago" in out


def test_status_v_says_what_a_session_waits_on_on_its_declarations_line(monkeypatch, capsys):
    """§4.5a **waiting** mark (TD-274): `ao status -v` prints the wait on the declaration's line, or on
    a line of its own where the session declared nothing, from the home's `host` reading."""
    at = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).isoformat()
    base = {"state": "idle", "confidence": "hook", "since": at, "adapter": "shell"}
    records = [
        {**base, "id": "ao-r-d", "name": "d", "out_of_work": {"at": at, "why": "nothing pickable"}},
        {**base, "id": "ao-r-g", "name": "g"},
        {**base, "id": "ao-r-x", "name": "x"},
    ]
    waiting = {"ao-r-d": [{"id": "m-1", "ref": "TD-222", "bound": None}], "ao-r-g": [{"id": "m-2", "ref": "#9"}]}

    def call(method, **_):
        return records if method == "list" else {"waiting": waiting} if method == "host" else {}

    monkeypatch.setattr(cli, "call_sync", call)
    assert cli.main(["status", "-v"]) == 0
    out = capsys.readouterr().out
    assert "out of work 5m · waiting on you: TD-222: nothing pickable" in out
    assert re.search(r"^\s+waiting on you: #9$", out, re.M)
    assert out.count("waiting on you") == 2


def test_a_shot_path_is_sent_relative_to_its_repo(tmp_path, monkeypatch):
    """Design §4.10 *A look* (TD-292): `ao msg --shot` sends a screenshot in this checkout as the
    repo-relative path the page finds on origin's default, however it was typed; a path that names
    no file here goes as given, for the host agent to refuse or keep."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    shots = tmp_path / "docs" / "mockups" / "reviews"
    shots.mkdir(parents=True)
    (shots / "2026-10-03-td292-row.png").write_bytes(b"\x89PNG")
    want = "docs/mockups/reviews/2026-10-03-td292-row.png"
    assert cli._shot_path(str(shots / "2026-10-03-td292-row.png")) == want
    monkeypatch.chdir(shots)
    assert cli._shot_path("2026-10-03-td292-row.png") == want
    assert cli._shot_path("docs/mockups/reviews/elsewhere.png") == "docs/mockups/reviews/elsewhere.png"
    args = cli.build_parser().parse_args(["msg", "--kind", "ask", "--shot", "a.png", "--shot", "b.png", "person", "x"])
    assert args.shot == ["a.png", "b.png"]


def test_the_grinder_preset_takes_a_decided_board_line_first_with_its_four_outcomes():
    """TD-384 slice 3 (design §4.9a *What a session does with a decided line*): the work order is
    claimed as `board:<key>` before any entry, ends one of four ways in a PR that closes the line, and
    an unclaimed one keeps the lane from out of work."""
    text = (pathlib.Path(__file__).parents[1] / "src/agentorc/briefs/grinder.md").read_text()
    assert "A decided board line comes first" in text and "Take it before any entry" in text
    assert "`ao progress claim board:<key>`" in text and "`ao progress done board:<key> --pr <n>`" in text
    for outcome in (
        "needs nothing but the line closed",
        "is your own work",
        "is someone else's",
        "asks the person something back",
    ):
        assert f"**the answer {outcome}**" in text, outcome
    done = re.search(r"`(python3 scripts/board_edit\.py done [^`]*)`", text).group(1)
    assert all(f in done for f in ("--board", "--line", "--expect", "--why"))
    assert "the board holds no decided line nobody has claimed" in text
