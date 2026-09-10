"""The web UI against a live host agent (a separate process on a private tmux server): pages,
actions, the /events stream, and the /term pty bridge.

The agent is `subprocess_agent`, not a thread: the sync TestClient needs an agent whose loop runs
on its own. TestClient still keeps an anyio portal thread alive, so ptyprocess's forkpty() runs
multi-threaded; the warning is filtered in pyproject.toml (TD-007, accepted)."""

import json
import os
import subprocess
import sys
import time

import pytest
from conftest import wait_for_sync as wait_for
from fastapi.testclient import TestClient

from sessionorc.tmux import Tmux

pytestmark = pytest.mark.integration


@pytest.fixture
def client(subprocess_agent):
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
    assert 'id="usagechip"' in r.text  # the per-profile usage figure (TD-001), empty until a poll lands
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
        tail = session_tail(client, sid)
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


def session_tail(client, sid):
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
    # hook-fed adapter (the child registers `hookstub`): a shell's scraped state would race the hook
    r = client.post("/new", data={"dir": str(tmp_path), "name": "ev", "adapter": "hookstub"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    sid = r.headers["location"].rsplit("/", 1)[-1]
    wait_state(client, sid, "working")
    with client.websocket_connect("/events") as ws:
        # a hook-side permission request flips the card; the pushed html carries Allow/Deny
        env = {**os.environ, "AGENTORC_SESSION": sid, "AGENTORC_PERMISSION_WAIT": "10"}
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


def test_terminal_bridge(client, subprocess_agent, tmp_path):
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
    tmux = Tmux(socket_name=subprocess_agent.sock_name)
    assert (
        tmux.run("display", "-p", "-t", f"={sid}:", "#{window_width}x#{window_height}", check=False).stdout.strip()
        == "120x29"  # tmux's status line takes one of the 30 rows
    )
    client.post(f"/api/sessions/{sid}/kill")


def test_bridge_argv_shapes():
    from agentorc.ui.pty_bridge import attach_argv, scroll_argv

    a = attach_argv("ao-x", socket_name="s")
    assert a[:6] == ["tmux", "-L", "s", "attach", "-t", "=ao-x:"]  # attach first: a chain stops at a failure
    assert a[6:] == [";", "set-option", "-t", "=ao-x:", "mouse", "on"]  # a session option, never -g
    assert attach_argv("ao-x")[0:2] == ["tmux", "attach"]
    assert scroll_argv("ao-x", "up", socket_name="s")[3:] == ["copy-mode", "-e", "-u", "-t", "=ao-x:"]
    assert scroll_argv("ao-x", "down")[1:] == ["send-keys", "-X", "-t", "=ao-x:", "page-down"]
    with pytest.raises(ValueError):
        scroll_argv("ao-x", "sideways")


def test_terminal_scrollback_reaches_tmux(client, subprocess_agent, tmp_path):
    """TD-022: the attach sets `mouse on` on the session, and a `scroll` bridge message moves tmux
    into copy mode over its history (up) and back out on reaching the live screen (down)."""
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "scroll"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    wait_state(client, sid, "idle")
    tmux = Tmux(socket_name=subprocess_agent.sock_name)

    def mode() -> str:
        return tmux.run("display", "-p", "-t", f"={sid}:", "#{pane_in_mode}", check=False).stdout.strip()

    with client.websocket_connect(f"/term/{sid}?cols=100&rows=20") as ws:
        ws.send_text("seq 1 200; echo SCROLL-DONE\r")
        buf = b""
        deadline = time.time() + 8
        while time.time() < deadline and b"SCROLL-DONE" not in buf:
            buf += ws.receive_bytes()
        assert b"SCROLL-DONE" in buf
        assert tmux.run("show-options", "-t", f"={sid}:", "mouse", check=False).stdout.strip() == "mouse on"
        assert mode() == "0"
        ws.send_text(json.dumps({"scroll": "up"}))
        assert wait_for(lambda: mode() == "1"), "scroll up did not enter copy mode"
        ws.send_text(json.dumps({"scroll": "sideways"}))  # ignored, the bridge stays up
        ws.send_text(json.dumps({"scroll": "down"}))
        assert wait_for(lambda: mode() == "0"), "scroll down to the live screen did not leave copy mode"
        ws.send_text("echo STILL-$((1+1))\r")
        buf = b""
        deadline = time.time() + 8
        while time.time() < deadline and b"STILL-2" not in buf:
            buf += ws.receive_bytes()
        assert b"STILL-2" in buf
    client.post(f"/api/sessions/{sid}/kill")


def test_term_tolerates_bad_size_params(client, tmp_path):
    """A garbage size must not reject the handshake (the browser only sees a 1006 for that)."""
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "sz"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    wait_state(client, sid, "idle")
    with client.websocket_connect(f"/term/{sid}?cols=NaN&rows=") as ws:
        ws.send_text("echo SIZE-OK\r")
        buf = b""
        deadline = time.time() + 8
        while time.time() < deadline and b"SIZE-OK" not in buf:
            buf += ws.receive_bytes()
        assert b"SIZE-OK" in buf
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


def test_vscode_url_opens_a_new_window(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  vscode_host: kmaster\n")
    from agentorc.ui.app import vscode_url

    assert vscode_url("/tmp/ao-test") == "vscode://vscode-remote/ssh-remote+kmaster/tmp/ao-test?windowId=_blank"
    # a space or `?` in the directory is percent-encoded, the separators are not (TD-011)
    assert (
        vscode_url("/tmp/my repo/a?b")
        == "vscode://vscode-remote/ssh-remote+kmaster/tmp/my%20repo/a%3Fb?windowId=_blank"
    )
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    assert vscode_url("/tmp/ao-test") == "vscode://file/tmp/ao-test?windowId=_blank"
    assert vscode_url("/tmp/my repo") == "vscode://file/tmp/my%20repo?windowId=_blank"


def test_closed_session_terminal_is_final_and_occupancy_endpoint(client, tmp_path):
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "cl"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    wait_state(client, sid, "idle")
    assert client.post(f"/api/sessions/{sid}/close").json() == {"ok": True}
    wait_state(client, sid, "closed")
    with client.websocket_connect(f"/term/{sid}") as ws:
        assert b"pane is gone" in ws.receive_bytes()
    r = client.get("/")
    assert "▣ Details" in r.text
    # a killed session is `exited` with no pane (TD-023): Details, and the terminal is final too
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "kd"}, follow_redirects=False)
    kid = r.headers["location"].rsplit("/", 1)[-1]
    wait_state(client, kid, "idle")
    assert client.post(f"/api/sessions/{kid}/kill").json() == {"ok": True}
    assert wait_state(client, kid, "exited")["pane"] is False
    with client.websocket_connect(f"/term/{kid}") as ws:
        assert b"pane is gone" in ws.receive_bytes()
    card = client.get("/").text.split(f'id="card-{kid}"', 1)[1].split('id="card-', 1)[0]
    assert "▣ Details" in card and "▣ Focus" not in card
    client.post(f"/api/sessions/{kid}/remove")
    assert client.get("/api/occupancy", params={"dir": str(tmp_path)}).json()["occupants"] == []
    assert client.get("/api/occupancy", params={"dir": ""}).json()["occupants"] == []


def test_unseen_idle_until_focused(client, tmp_path):
    """TD-017: an idle session nobody has looked at renders "finished · unseen" and sorts just above
    plain idle; opening Focus (or acting on the card) marks it seen; a later finish is unseen again."""
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "unseen"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]  # the redirect to Focus is not followed: never seen
    s = wait_state(client, sid, "idle")
    assert s["unseen"] is True and s["state_label"] == "finished · unseen" and s["rank"] == 4.5
    r = client.get("/")
    assert f'id="card-{sid}"' in r.text and "finished · unseen" in r.text and 'data-unseen="1"' in r.text
    assert client.get(f"/focus/{sid}").status_code == 200  # Focus = seen
    s = next(x for x in client.get("/api/sessions").json() if x["id"] == sid)
    assert s["unseen"] is False and s["state_label"] == "idle" and s["rank"] == 5 and s["seen_at"]
    # a new turn that finishes after that look is unseen again. Whole-second stamps: the send
    # itself counts as a look, so the turn must outlast the second it was sent in (a tie is "seen").
    assert client.post(f"/api/sessions/{sid}/send", json={"text": "sleep 2"}).json() == {"ok": True}
    wait_state(client, sid, "working")
    s = wait_state(client, sid, "idle")
    assert s["unseen"] is True
    assert client.post(f"/api/sessions/{sid}/seen").json() == {"ok": True}  # what the open Focus page sends
    s = next(x for x in client.get("/api/sessions").json() if x["id"] == sid)
    assert s["unseen"] is False
    client.post(f"/api/sessions/{sid}/kill")


def test_registry_only_card_renders_read_only(tmp_path, monkeypatch):
    """TD-010 (a): a card built from the tool's registry (no tmux) offers Details and nothing that
    acts — no mode toggle, no ⋯ menu — and says where it came from."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    s = {
        "id": "ext-u-1", "name": "editor", "kind": "interactive", "adapter": "claude-code", "dir": str(tmp_path),
        "state": "working", "since": "2026-09-10T16:00:00Z", "confidence": "scraped", "external": True,
        "pane": False, "adapter_id": "u-1", "tail": [],
    }  # fmt: skip
    html = templates.get_template("card.html").render(s=view(s))
    assert "▣ Details" in html and "▣ Focus" not in html
    assert ">registry<" in html and 'data-act="mode"' not in html and 'data-act="kill"' not in html
    assert "started outside agentorc" in html
    html = templates.get_template("card.html").render(s=view({**s, "state": "idle"}))
    assert 'data-act="close"' not in html and "ready to close" not in html  # nothing to close either
