"""The web UI against a live host agent on a private tmux server: pages, actions, the /events
stream, and the /term pty bridge."""

import asyncio
import contextlib
import json
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from sessionorc import paths
from sessionorc.agent import HostAgent
from sessionorc.tmux import Tmux

SOCK = f"ao-test-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def agent_thread(tmp_path_factory):
    """One agent for the module, on its own loop in a thread, so the sync TestClient can talk to it.
    Module-scoped monkeypatch: env and tick restored at teardown, whatever the test order."""
    mp = pytest.MonkeyPatch()
    home = tmp_path_factory.mktemp("home")
    mp.setenv("AGENTORC_HOME", str(home))
    mp.setenv("AGENTORC_TMUX_SOCKET", SOCK)
    mp.setattr("sessionorc.agent.TICK_SECONDS", 0.3)
    tmux = Tmux(socket_name=SOCK)
    loop = asyncio.new_event_loop()
    a = HostAgent(tmux=tmux)
    task_holder = {}

    def run():
        asyncio.set_event_loop(loop)
        task_holder["t"] = loop.create_task(a.serve(paths.socket_path()))
        with contextlib.suppress(Exception):
            loop.run_forever()

    th = threading.Thread(target=run, daemon=True)
    th.start()
    for _ in range(50):
        if paths.socket_path().exists():
            break
        time.sleep(0.05)
    yield a

    async def shutdown():
        task_holder["t"].cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task_holder["t"]

    asyncio.run_coroutine_threadsafe(shutdown(), loop).result(timeout=5)
    loop.call_soon_threadsafe(loop.stop)
    th.join(timeout=5)
    tmux.kill_server()
    mp.undo()


@pytest.fixture
def client(agent_thread):
    from agentorc.ui.app import create_app

    with TestClient(create_app()) as c:
        yield c


def wait_state(client, sid, state, timeout=6.0):
    for _ in range(int(timeout / 0.1)):
        s = next(x for x in client.get("/api/sessions").json() if x["id"] == sid)
        if s["state"] == state:
            return s
        time.sleep(0.1)
    raise AssertionError(f"{sid} never reached {state}: {s['state']}")


def test_pages_and_shell_flow(client, tmp_path):
    r = client.get("/")
    assert r.status_code == 200 and "Herd" in r.text and "No sessions" in r.text
    r = client.get("/new")
    assert r.status_code == 200 and "claude-code" in r.text and "shell" in r.text
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "sh1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/focus/ao-")
    sid = r.headers["location"].rsplit("/", 1)[-1]
    wait_state(client, sid, "idle")
    r = client.get(f"/focus/{sid}")
    assert r.status_code == 200 and sid in r.text and "Ready to close" in r.text and "xterm.js" in r.text
    r = client.get("/")
    assert f'id="card-{sid}"' in r.text and 'class="status' in r.text  # idle shell slot
    # send through the composer path, then kill from the card
    assert client.post(f"/api/sessions/{sid}/send", json={"text": "echo via-ui"}).json() == {"ok": True}
    for _ in range(30):
        tail = agent_thread_tail(client, sid)
        if any("via-ui" in line for line in tail):
            break
        time.sleep(0.1)
    else:
        raise AssertionError("composer text never reached the pane")
    assert client.post(f"/api/sessions/{sid}/kill").json() == {"ok": True}
    wait_state(client, sid, "exited")
    r = client.post(f"/api/sessions/{sid}/allow")
    assert r.status_code == 409  # no pending permission
    assert client.post(f"/api/sessions/{sid}/remove").json() == {"ok": True}
    assert all(x["id"] != sid for x in client.get("/api/sessions").json())


def agent_thread_tail(client, sid):
    s = next(x for x in client.get("/api/sessions").json() if x["id"] == sid)
    return s.get("tail") or []


def test_new_form_errors_are_clean(client, tmp_path):
    r = client.post(
        "/new", data={"name": "x", "dir": str(tmp_path / "nope"), "adapter": "shell"}, follow_redirects=False
    )
    assert r.status_code == 400 and "not a directory" in r.text
    r = client.post("/new", data={"name": "x", "dir": str(tmp_path), "adapter": "no-such"}, follow_redirects=False)
    assert r.status_code == 400 and "unknown adapter" in r.text


def test_events_stream_and_permission_buttons(client, tmp_path):
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "ev"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    wait_state(client, sid, "idle")
    with client.websocket_connect("/events") as ws:
        # a hook-side permission request flips the card; the pushed html carries Allow/Deny
        import subprocess
        import sys

        env = {**__import__("os").environ, "AGENTORC_SESSION": sid, "AGENTORC_PERMISSION_WAIT": "10"}
        payload = {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf x"},
            "tool_use_id": "tu-ui",
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "agentorc.adapters.claude_code.hook"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )
        proc.stdin.write(json.dumps(payload))
        proc.stdin.close()  # the hook reads stdin to EOF before it talks to the agent
        seen = None
        for _ in range(40):
            ev = json.loads(ws.receive_text())
            if ev.get("event") == "session" and ev["id"] == sid and ev["state"] == "needs-you":
                seen = ev
                break
        assert seen and 'data-act="allow"' in seen["html"] and "rm -rf x" in seen["html"]
        assert seen["session"]["pending"]["deadline"]
        assert client.post(f"/api/sessions/{sid}/deny", json={"reason": "nope"}).json() == {"ok": True}
        assert proc.wait(timeout=10) == 0
        out = proc.stdout.read()
        assert json.loads(out)["hookSpecificOutput"]["decision"] == {"behavior": "deny", "reason": "nope"}
        for _ in range(40):
            ev = json.loads(ws.receive_text())
            if ev.get("event") == "session" and ev["id"] == sid and ev["state"] == "working":
                break
        else:
            raise AssertionError("no working delta after deny")
    client.post(f"/api/sessions/{sid}/kill")


def test_terminal_bridge(client, tmp_path):
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "term"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    wait_state(client, sid, "idle")
    with client.websocket_connect(f"/term/{sid}?cols=100&rows=20") as ws:
        ws.send_text(json.dumps({"resize": [120, 30]}))
        ws.send_text("echo BRIDGE-$((40+2))\r")
        buf = b""
        deadline = time.time() + 8
        while time.time() < deadline and b"BRIDGE-42" not in buf:
            buf += ws.receive_bytes()
        assert b"BRIDGE-42" in buf
    # the pane is still alive after the viewer disconnects (attach detached, session kept)
    s = next(x for x in client.get("/api/sessions").json() if x["id"] == sid)
    assert s["state"] in ("idle", "working")
    tmux = Tmux(socket_name=SOCK)
    assert (
        tmux.run("display", "-p", "-t", f"={sid}:", "#{window_width}x#{window_height}", check=False).stdout.strip()
        == "120x29"  # tmux's status line takes one of the 30 rows
    )
    client.post(f"/api/sessions/{sid}/kill")


def test_term_unknown_session(client):
    with client.websocket_connect("/term/ao-none") as ws:
        assert b"no session" in ws.receive_bytes()


def test_herd_renders_with_agent_down(tmp_path, monkeypatch):
    """No bare 503: the Herd shows the down banner and Retry when the agent socket is absent."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "nohome"))
    from agentorc.ui.app import create_app

    with TestClient(create_app()) as c:
        r = c.get("/")
        assert (
            r.status_code == 200
            and 'id="agentdown"' in r.text
            and "hidden" not in r.text.split('id="agentdown"')[0][-60:]
        )
        assert "host agent unreachable" in r.text
        r = c.get("/focus/ao-x", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
        r = c.post("/api/sessions/ao-x/kill")
        assert r.status_code == 503
