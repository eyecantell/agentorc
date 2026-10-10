"""The web UI against a live host agent (a separate process on a private tmux server): pages,
actions, the /events stream, and the /term pty bridge.

The agent is `subprocess_agent`, not a thread: the sync TestClient needs an agent whose loop runs
on its own. TestClient still keeps an anyio portal thread alive, so ptyprocess's forkpty() runs
multi-threaded; the warning is filtered in pyproject.toml (TD-007, accepted)."""

import json
import os
import pathlib
import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

import pytest
from conftest import wait_for_sync as wait_for
from conftest import wait_screen
from design_doc import section
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

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


def pane_bytes(ws, words=None):
    """The next pane frame of a `/term/` socket. The attach's own words — text frames, the bridge's
    `{clients, window}` reading among them (design §4.6, TD-480) — are not pane output: collected
    into `words` when given, and passed over."""
    while True:
        msg = ws.receive()
        if msg.get("type") == "websocket.close":
            raise WebSocketDisconnect(msg.get("code", 1000), msg.get("reason"))
        if msg.get("bytes") is not None:
            return msg["bytes"]
        if words is not None:
            words.append(json.loads(msg["text"]))


def test_pages_and_shell_flow(client, tmp_path):
    r = client.get("/")
    assert r.status_code == 200 and "Org" in r.text and "No sessions" in r.text
    assert 'id="usagechip"' in r.text  # the per-profile usage figure (TD-001), empty until a poll lands
    r = client.get("/new")
    assert r.status_code == 200 and "claude-code" in r.text and "shell" in r.text
    # the Role pick-list (design §4.5a): the built-ins, plus what the directory's repo defines
    assert 'name="role"' in r.text and 'title="built-in · grants control">manager · unattended</option>' in r.text
    (tmp_path / ".agentorc.yml").write_text("controllers: [orc]\nroles: {reviewer: {lane: [ui]}}\n")
    (tmp_path / ".agentorc" / "roles" / "reviewer").mkdir(parents=True)  # a role directory (§4.9c)
    (tmp_path / ".agentorc" / "roles" / "reviewer" / "role.yml").write_text("")
    (tmp_path / ".agentorc" / "roles" / "reviewer" / "template.md").write_text("review {lane}\n")
    r = client.get(f"/new?dir={tmp_path}")
    assert 'title="repo role + repo">reviewer · unattended</option>' in r.text and 'data-default="orc"' in r.text
    roles = client.get(f"/api/roles?dir={tmp_path}").json()
    assert roles["controllers"] == ["orc"] and [x["name"] for x in roles["roles"]][-1] == "reviewer"
    (tmp_path / ".agentorc.yml").write_text("roles: {grinder: {grants: [fly]}}\n")
    assert "unknown grant 'fly'" in client.get(f"/api/roles?dir={tmp_path}").json()["error"]
    r = client.post("/new", data={"name": "x", "dir": str(tmp_path), "role": "grinder"}, follow_redirects=False)
    assert r.status_code == 400 and "unknown grant" in r.text  # a malformed file: Start says so, nothing starts
    (tmp_path / ".agentorc.yml").unlink()
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
    # Wrap up is marked as one, which holds the doorbell off (design §4.10); a plain send clears it
    assert client.post(f"/api/sessions/{sid}/wrapup").json() == {"ok": True}
    got = next(x for x in client.get("/api/sessions").json() if x["id"] == sid)
    assert got["wrapup_at"]
    assert got["pr_marks"] == {}  # the list carries the PR's marks as the cards do (TD-193); no readings here
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
            buf += pane_bytes(ws)
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
    # the mouse is the browser's (§4.6, TD-174): the attach sets no option on the session
    assert a == ["tmux", "-L", "s", "attach", "-t", "=ao-x:"] and "set-option" not in a
    assert attach_argv("ao-x")[0:2] == ["tmux", "attach"]
    # a page, the keyboard's: no `lines`
    assert scroll_argv("ao-x", "up", socket_name="s")[3:] == ["copy-mode", "-e", "-u", "-t", "=ao-x:"]
    assert scroll_argv("ao-x", "down")[1:] == ["send-keys", "-X", "-t", "=ao-x:", "page-down"]
    # lines, the wheel's: copy mode (a no-op when in it), then that many lines
    t = "=ao-x:"
    assert scroll_argv("ao-x", "up", 3)[1:] == [
        "copy-mode",
        "-e",
        "-t",
        t,
        ";",
        "send-keys",
        "-X",
        "-N",
        "3",
        "-t",
        t,
        "scroll-up",
    ]
    assert scroll_argv("ao-x", "down", 1)[-1] == "scroll-down" and scroll_argv("ao-x", "up", 9999)[9] == "500"
    for bad in (("sideways", None), ("up", 0), ("up", -2), ("up", True)):
        with pytest.raises(ValueError):
            scroll_argv("ao-x", *bad)


def test_terminal_scrollback_reaches_tmux(client, subprocess_agent, tmp_path):
    """TD-022: a `scroll` bridge message moves tmux into copy mode over its history (up) and back
    out on reaching the live screen (down); the wheel's `lines` scroll lines (TD-174). The attach
    sets no mouse option: the mouse is the browser's (§4.6)."""
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
            buf += pane_bytes(ws)
        assert b"SCROLL-DONE" in buf
        # read once, this raced the attach (TD-033): the pane leaves whatever mode it started in
        assert wait_for(lambda: mode() == "0"), "the pane never settled on the live screen"
        assert tmux.run("show-options", "-t", f"={sid}:", "mouse", check=False).stdout.strip() == ""
        ws.send_text(json.dumps({"scroll": "up"}))
        assert wait_for(lambda: mode() == "1"), "scroll up did not enter copy mode"
        ws.send_text(json.dumps({"scroll": "sideways"}))  # ignored, the bridge stays up
        ws.send_text(json.dumps({"scroll": "down"}))
        assert wait_for(lambda: mode() == "0"), "scroll down to the live screen did not leave copy mode"
        # the wheel (TD-174): lines up enter copy mode, as many lines down leave it at the bottom
        ws.send_text(json.dumps({"scroll": "up", "lines": 3}))
        assert wait_for(lambda: mode() == "1"), "the wheel's lines did not enter copy mode"
        ws.send_text(json.dumps({"scroll": "down", "lines": 3}))
        assert wait_for(lambda: mode() == "0"), "the wheel back to the bottom did not leave copy mode"
        ws.send_text("echo STILL-$((1+1))\r")
        buf = b""
        deadline = time.time() + 8
        while time.time() < deadline and b"STILL-2" not in buf:
            buf += pane_bytes(ws)
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
            buf += pane_bytes(ws)
        assert b"SIZE-OK" in buf
    client.post(f"/api/sessions/{sid}/kill")


def test_term_unknown_session(client):
    with client.websocket_connect("/term/ao-none") as ws:
        assert b"no session" in ws.receive_bytes()


def test_org_renders_with_agent_down(tmp_path, monkeypatch):
    """No bare 503: the Org shows the down banner and Retry when the agent socket is absent."""
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


def test_the_inbox_poll_says_the_agent_is_down_instead_of_a_bare_503(tmp_path, monkeypatch):
    """TD-069's leftover, design §4.5 *there is no silent failure path*: the Inbox **page** already
    answered a down host agent with a banner; its **poll** answered a bare 503, which a client can
    only drop — leaving the rows on screen looking current. The poll now answers in a shape the
    page can say, and claims no count: `needs: null` is *not known*, which is not *nothing*."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "nohome"))
    from agentorc.ui.app import create_app

    with TestClient(create_app()) as c:
        r = c.get("/api/person/inbox")
        assert r.status_code == 200
        got = r.json()
        assert got["agent_down"] is True and "unreachable" in got["why"]
        assert got["needs"] is None and got["entries"] == [] and got["html"] == {}
        # the page carries the banner at all times, hidden until it is true, so the poll can show it
        page = c.get("/inbox").text
        assert 'id="agentdown"' in page and 'id="agentdownwhy"' in page
        import agentorc.ui as ui

        js = (pathlib.Path(ui.__file__).parent / "static" / "app.js").read_text()
        assert "agentdown" in js and "got.needs !== null" in js
        # …and the two things the review of PR #271 caught: the banner is hidden by the **class**,
        # because `.warn` sets `display` and beats the UA's `[hidden]` rule (the trap `.badge[hidden]`
        # is commented for in app.css); and an agent that is down blanks no rows and claims no count
        tpl = (pathlib.Path(ui.__file__).parent / "templates" / "inbox.html").read_text()
        assert 'class="warn{% if not agent_down %} hidden{% endif %}" id="agentdown"' in tpl
        assert 'class="warn" id="agentdown"' in page  # this run's agent *is* down: shown
        assert 'down.classList.toggle("hidden", !isDown);' in js  # after the grace (TD-372)
        assert "if (!got || got.agent_down || !got.html) return;" in js


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
    """TD-017: an idle session nobody has looked at renders "idle · unseen" and sorts just above
    plain idle; opening Focus (or acting on the card) marks it seen; a later finish is unseen again."""
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "unseen"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]  # the redirect to Focus is not followed: never seen
    s = wait_state(client, sid, "idle")
    assert s["unseen"] is True and s["state_label"] == "idle · unseen" and s["rank"] == 4.5
    r = client.get("/")
    assert f'id="card-{sid}"' in r.text and "idle · unseen" in r.text and 'data-unseen="1"' in r.text
    assert client.get(f"/focus/{sid}").status_code == 200  # Focus = seen
    s = next(x for x in client.get("/api/sessions").json() if x["id"] == sid)
    assert s["unseen"] is False and s["state_label"] == "idle" and s["rank"] == 5 and s["seen_at"]
    # a new turn that finishes after that look is unseen again. Whole-second stamps: the send
    # itself counts as a look, so the turn must outlast the second it was sent in (a tie is "seen").
    assert client.post(f"/api/sessions/{sid}/send", json={"text": "sleep 2"}).json() == {"ok": True}
    # the paste can take a moment to reach the pane, and `sleep 2` is only `working` for two
    # seconds: wait for the command on the screen first, so the state wait starts once it is
    # running (TD-033)
    wait_screen(
        sid,
        lambda: session_tail(client, sid),
        lambda t: any("sleep 2" in line for line in t),
        what="the sent command on the pane",
    )
    wait_state(client, sid, "working")
    s = wait_state(client, sid, "idle")
    assert s["unseen"] is True
    assert client.post(f"/api/sessions/{sid}/seen").json() == {"ok": True}  # what the open Focus page sends
    s = next(x for x in client.get("/api/sessions").json() if x["id"] == sid)
    assert s["unseen"] is False
    client.post(f"/api/sessions/{sid}/kill")


def test_the_terminal_palette_is_complete_and_dark(tmp_path):
    """TD-038 (b), design goal 12 and §4.6: the pane carries VS Code's Dark Modern terminal palette,
    so the same Claude Code output is the same colour in Focus as in the editor's terminal beside
    it. This repo has no JavaScript harness, so the check is over the source: every ANSI name
    present, every value a hex colour, and the background a literal rather than a theme token —
    the pane is dark whatever the page is, which is the whole of goal 12.
    """
    js = (pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui" / "static" / "app.js").read_text()
    block = js[js.index("AO.TERM_THEME = {") : js.index("};", js.index("AO.TERM_THEME = {"))]
    colours = dict(re.findall(r'(\w+): "(#[0-9a-fA-F]{6})"', block))
    base = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]
    wanted = [*base, *(f"bright{n.capitalize()}" for n in base), "foreground", "background", "cursor"]
    assert not [n for n in wanted if n not in colours], sorted(set(wanted) - set(colours))
    assert "var(--" not in block, "goal 12: the pane does not follow the page theme"
    # and the one place the font lives, so it cannot drift back to three
    opts = js[js.index("AO.TERM_OPTS = {") : js.index("};", js.index("AO.TERM_OPTS = {"))]
    assert "fontFamily" in opts and "fontSize" in opts
    assert js.count("new Terminal(") == 1 and "AO.TERM_OPTS" in js[js.index("new Terminal(") :]


def test_the_terminal_font_and_renderer_are_bundled_and_served(client):
    """TD-038 (a) and (c): the monospace face ships under `static/vendor/fonts/` with its licence and
    an `@font-face` for every file, the WebGL addon loads after xterm.js on the Focus page with the
    DOM renderer as its fallback, and every vendored file is named with its version in the vendor
    README — the files carry none, which is what held (c) up. Served, the font is `font/woff2`."""
    static = pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui" / "static"
    css = (static / "app.css").read_text()
    faces = re.findall(r'@font-face \{[^}]*url\("/static/vendor/(fonts/[^"]+)"\)', css)
    assert faces and all((static / "vendor" / f).is_file() for f in faces)
    assert (static / "vendor" / "fonts" / "OFL.txt").is_file()
    assert '"calt" 0' in css  # no ligatures in a pane you type into
    html = (static.parent / "templates" / "focus.html").read_text()
    assert html.index("vendor/xterm.js") < html.index("vendor/addon-webgl.js")
    js = (static / "app.js").read_text()
    assert "onContextLoss" in js and "AO.termRenderer(term)" in js and "AO.termFont(term, fit)" in js
    readme = (static / "vendor" / "README.md").read_text()
    for f in (static / "vendor").rglob("*"):
        if f.suffix in (".js", ".css", ".woff2"):
            assert f.name.split("-latin")[0] in readme or f.name in readme, f.name
    r = client.get("/static/" + "vendor/" + faces[0])
    assert r.status_code == 200 and r.headers["content-type"] == "font/woff2"


def test_the_page_links_its_stylesheet_and_script_by_their_content(client):
    """2026-09-26: after a promote the browser drew the new markup with its cached old `app.css`.
    The page's two links carry a hash of the file, so a changed file is a new link, and each link
    serves the file it names."""
    import hashlib

    static = pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui" / "static"
    html = client.get("/").text
    for name in ("app.css", "app.js"):
        digest = hashlib.sha256((static / name).read_bytes()).hexdigest()[:12]
        link = f"/static/{name}?v={digest}"
        assert link in html and f'"/static/{name}"' not in html
        assert client.get(link).content == (static / name).read_bytes()


def test_a_card_says_when_the_session_stops_and_only_then(tmp_path, monkeypatch):
    """design §4.5a **stops** note (§6, TD-026): a session with a stop time says so on its card and
    in its Focus header, in the host's local clock; every session without one says nothing, which is
    most of them."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    base = {
        "id": "ao-x-2", "name": "w", "kind": "interactive", "adapter": "claude-code", "dir": str(tmp_path),
        "state": "idle", "since": "2026-09-13T16:00:00Z", "confidence": "hook", "pane": True, "tail": [],
        "unattended": True,
    }  # fmt: skip
    card = templates.get_template("card.html")
    assert "stops" not in card.render(s=view(base))
    # the expected local time is the UTC instant converted back, exactly as `stop_note` does it:
    # local arithmetic on a fixed-offset `astimezone()` would be an hour off across a DST change
    when = (datetime.now(UTC) + timedelta(hours=3)).astimezone()
    stopping = {**base, "run_until": when.astimezone(UTC).isoformat().replace("+00:00", "Z")}
    html = card.render(s=view(stopping))
    # the reader's clock, never the record's UTC; `stop_note` adds a weekday once the stop is not
    # today, so a run after 21:00 local must not expect the bare time right after "stops" (TD-054)
    assert re.search(rf"stops (?:[A-Z][a-z]{{2}} )?{when:%H:%M}", html)
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).astimezone()
    later = {**base, "run_until": tomorrow.astimezone(UTC).isoformat().replace("+00:00", "Z")}
    assert f"stops {tomorrow:%a %H:%M}" in card.render(s=view(later))
    assert "wrap-up sent" not in html
    asked = {**stopping, "wrapup_sent_at": "2026-09-13T16:00:00Z"}
    assert "wrap-up sent" in card.render(s=view(asked))
    # a record whose `run_until` is not a time costs that card its note and nothing else: `view` runs
    # for every session on the grid, so a raise here would take down the page (PR #131 review)
    assert "stops" not in card.render(s=view({**base, "run_until": "half six"}))
    assert view({**base, "run_until": "half six"})["stop_note"] == ""


def test_the_new_session_form_refuses_a_stop_time_on_an_interactive_session(tmp_path, monkeypatch):
    """The form's half of `--until`'s rule (design §6): a stop time is a policy, policies leave
    interactive sessions alone (§4.2), and storing one nothing acts on is TD-026's failure
    inverted."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from fastapi import HTTPException

    from agentorc.ui.app import stop_fields

    assert stop_fields("", False) == {} and stop_fields("   ", True) == {}
    with pytest.raises(HTTPException) as bad:
        stop_fields("06:00", False)
    assert bad.value.status_code == 400 and "pick a role that runs unattended" in bad.value.detail
    with pytest.raises(HTTPException) as nonsense:
        stop_fields("half six", True)
    assert nonsense.value.status_code == 400
    sent = stop_fields("+2h", True)
    assert sent["run_until"].endswith("Z") and sent["wrapup_prompt"]


def test_a_stalled_card_says_why_when_a_screen_rule_explained_it(tmp_path, monkeypatch):
    """TD-032: a worker that stood down under a Remote Control takeover is `stalled?` with a note
    from the screen rule. Design §4.2 promises "a `stalled?` that can say why", so the note goes on
    the card above the tail; a `stalled?` with no explanation still shows the tail alone."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    s = {
        "id": "ao-x-1", "name": "w1", "kind": "interactive", "adapter": "claude-code", "dir": str(tmp_path),
        "state": "stalled?", "since": "2026-09-11T16:00:00Z", "confidence": "scraped", "pane": True,
        "tail": ["  This device is standing down (code 4090)."],
    }  # fmt: skip
    card = templates.get_template("card.html")
    assert "stood down" not in card.render(s=view(s))
    stood_down = {**s, "pending": {"kind": "note", "text": "stood down: another device took over this session"}}
    html = card.render(s=view(stood_down))
    assert "stood down: another device took over this session" in html
    # the slot holds one text, the first that applies (design §4.5 *The card's anatomy*, TD-095):
    # the note that explains the stop, not the tail under it
    assert "code 4090" not in html and view(stood_down)["slot"]["kind"] == "needs"


def test_the_name_check_endpoint_answers_before_start(client, tmp_path):
    """TD-030 step 4: the New session form asks what Start would do, the way it already asks about
    directory occupancy — free, a live holder to switch to, or a replacement with the log kept."""
    empty = client.get("/api/name_check", params={"dir": "", "name": ""}).json()
    assert empty["verdict"] == "free" and empty["message"] == ""
    free = client.get("/api/name_check", params={"dir": str(tmp_path), "name": "nobody"}).json()
    assert free["verdict"] == "free" and free["holder"] is None and free["id"].endswith("-nobody")
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "held"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    wait_state(client, sid, "idle")
    live = client.get("/api/name_check", params={"dir": str(tmp_path), "name": "held"}).json()
    assert live["verdict"] == "live" and live["holder"] == sid and "switch to it" in live["message"]
    client.post(f"/api/sessions/{sid}/close")
    wait_state(client, sid, "closed")
    again = client.get("/api/name_check", params={"dir": str(tmp_path), "name": "held"}).json()
    assert again["verdict"] == "supersede" and again["message"] == "replaces the closed held — run log kept"


def test_the_shell_button_twice_gives_two_shells(client, tmp_path):
    """TD-030: the Org's Shell button sends no name, so the agent names it (`shell`, `shell-2`).
    With a name of its own it would hit §4.1's rule on the second click and be refused."""
    ids = []
    for expected in ("shell", "shell-2"):
        r = client.post("/shell", data={"dir": str(tmp_path)}, follow_redirects=False)
        assert r.status_code == 303
        sid = r.headers["location"].rsplit("/", 1)[-1]
        ids.append(sid)
        assert next(x for x in client.get("/api/sessions").json() if x["id"] == sid)["name"] == expected
    for sid in ids:
        client.post(f"/api/sessions/{sid}/kill")


def test_the_profile_line_says_which_model_is_in_use(tmp_path, monkeypatch):
    """TD-031, design §4.2a: tool · account · model, where the third part is the model actually
    observed. The profile's declared model is an intent, so a line falling back to it says so."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "profiles.yml").write_text(
        "default: paul\nprofiles:\n  paul: {account: paul, model: fable-5-1}\n  bare: {account: b}\n"
    )
    from agentorc.ui.app import view

    s = {
        "id": "ao-r-w", "name": "w", "kind": "interactive", "adapter": "claude-code", "dir": str(tmp_path),
        "state": "working", "since": "2026-09-11T16:00:00Z", "confidence": "hook", "tail": [], "profile": "paul",
    }  # fmt: skip
    assert view(s)["profile_line"] == "claude-code · paul · fable-5-1 (profile)"
    assert view({**s, "model": "claude-opus-5"})["profile_line"] == "claude-code · paul · opus-5"
    assert view({**s, "profile": "bare"})["profile_line"] == "claude-code · b"  # nothing declared, nothing observed
    assert view({**s, "profile": "bare", "model": "claude-sonnet-5"})["profile_line"] == "claude-code · b · sonnet-5"
    assert view({**s, "profile": "gone", "model": "claude-opus-5"})["profile_line"] == "claude-code · gone · opus-5"
    assert view({**s, "adapter": "shell", "profile": ""})["profile_line"] == "shell"


def test_the_card_draws_the_context_reading_after_the_model(tmp_path, monkeypatch):
    """TD-190, design §4.5 *The card's anatomy* row 4: *· 231k* after tool · account · model, the
    window on hover and in Focus; nothing where the adapter cannot tell."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    s = {
        "id": "ao-r-w", "name": "w", "kind": "interactive", "adapter": "claude-code", "dir": str(tmp_path),
        "state": "idle", "since": "2026-09-11T16:00:00Z", "confidence": "hook", "tail": [], "model": "claude-opus-5-5",
    }  # fmt: skip
    card = templates.get_template("card.html")
    assert view(s)["context_short"] == "" and 'class="context"' not in card.render(s=view(s))
    read = view({**s, "context": {"tokens": 231_203, "at": "2026-09-27T20:00:00Z", "window": 1_000_000}})
    assert (read["context_short"], read["context_line"]) == ("231k", "231k of 1M")
    html = card.render(s=read)
    assert "· opus-5-5 · <span" in html and ">231k</span>" in html and "context 231k of 1M" in html


def test_a_dead_attach_is_final(client, subprocess_agent, tmp_path):
    """TD-029, the reproduction: a record that still claims a pane whose tmux session is gone (a
    tmux server restart, or a close the record has not caught up with) used to let `/term/` run
    `tmux attach`, print tmux's "can't find session", and end normally — which the client treats as
    retryable, so it reconnected twice a second until Forget. A dead attach is now final: the
    server closes 4404, the one code the client never retries. This is the server half only —
    parts (a) and (c) of the fix are JavaScript, which this repo has no harness for."""
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "dead"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    wait_state(client, sid, "idle")
    # the pane goes without the agent's knowledge; the record still says `pane: true`
    Tmux(socket_name=subprocess_agent.sock_name).kill_session(sid)
    assert next(x for x in client.get("/api/sessions").json() if x["id"] == sid)["pane"] is True
    with pytest.raises(WebSocketDisconnect) as e, client.websocket_connect(f"/term/{sid}") as ws:
        while True:
            pane_bytes(ws)  # tmux's own error, then the close
    assert e.value.code == 4404


def test_focus_watches_an_unattended_session(client, subprocess_agent, tmp_path):
    """TD-096, design §4.6 *A read-only attach*: an unattended session's attach says it is read-only
    in its first frame and drops every key frame — the rule is the server's, so a devtools console
    cannot undo it — while the wheel still reaches tmux, which scrolls its history with it. The
    header toggle is named for what it does, and after **Take over** the next attach takes keys."""
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "watch"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    wait_state(client, sid, "idle")
    tmux = Tmux(socket_name=subprocess_agent.sock_name)

    def pane() -> str:
        return tmux.run("capture-pane", "-p", "-t", f"={sid}:", check=False).stdout

    def in_mode() -> str:
        return tmux.run("display", "-p", "-t", f"={sid}:", "#{pane_in_mode}", check=False).stdout.strip()

    tmux.run("send-keys", "-t", f"={sid}:", "seq 1 200", "Enter")  # history for the wheel to scroll
    assert wait_for(lambda: "200" in pane())
    assert client.post(f"/api/sessions/{sid}/mode", json={"unattended": True}).json()["ok"] is True
    rec = next(x for x in client.get("/api/sessions").json() if x["id"] == sid)
    assert rec.get("pause_prompt"), "the gate's texts ride the toggle (TD-100)"
    page = client.get(f"/focus/{sid}").text
    assert 'id="fmodeact"' in page and ">Take over</button>" in page and 'data-unattended="1"' in page
    assert re.search(r'class="card composer hidden" id="composer"', page)  # the composer types: closed
    with client.websocket_connect(f"/term/{sid}?cols=100&rows=20") as ws:
        assert json.loads(ws.receive_text()) == {"read_only": True}
        ws.send_text("echo TYPED-$((40+2))\r")
        ws.send_bytes(b"echo BYTES-$((40+3))\r")
        ws.send_text("\x1b[<0;5;5M\x1b[<0;5;5m")  # a click: dropped with the keys
        ws.send_text("\x1b[<64;5;5M")  # a wheel report too, since TD-174: the wheel is a scroll message
        assert wait_for(lambda: in_mode() == "0")
        ws.send_text(json.dumps({"scroll": "up", "lines": 2}))  # the wheel, up: tmux enters copy mode
        assert wait_for(lambda: in_mode() == "1"), "the wheel did not reach tmux on a read-only attach"
    time.sleep(0.3)
    assert "TYPED-42" not in pane() and "BYTES-43" not in pane() and "echo TYPED" not in pane()
    tmux.run("send-keys", "-t", f"={sid}:", "-X", "cancel", check=False)

    # Take over: the toggle is the mode's, and the next attach sends no read-only word and takes keys
    assert client.post(f"/api/sessions/{sid}/mode", json={"unattended": False}).json()["ok"] is True
    page = client.get(f"/focus/{sid}").text
    assert ">Switch to unattended</button>" in page  # no controller and no team: nobody to hand it to
    with client.websocket_connect(f"/term/{sid}?cols=100&rows=20") as ws:
        ws.send_text("echo TOOK-$((40+4))\r")
        buf, words = b"", []
        deadline = time.time() + 8
        while time.time() < deadline and b"TOOK-44" not in buf:
            buf += pane_bytes(ws, words)
        assert b"TOOK-44" in buf
        assert not [w for w in words if "read_only" in w]  # no read-only word: it takes keys
    client.post(f"/api/sessions/{sid}/kill")


def test_hand_back_clears_a_stop_time_that_passed_while_the_person_held_it(client, subprocess_agent, tmp_path):
    """TD-096, design §4.5a **Hand back**: a stop time that fell due while the session was
    interactive is not a deadline any more — nothing acted on it, and handing back must not have the
    next tick kill it — so it is cleared; one still ahead stays."""
    from agentorc.ui.app import view

    assert view({"id": "a", "state": "idle", "unattended": False, "team": "t"})["mode_act"] == "Hand back"
    assert view({"id": "a", "state": "idle", "unattended": False, "controllers": ["m"]})["mode_act"] == "Hand back"
    assert view({"id": "a", "state": "idle", "unattended": False})["mode_act"] == "Switch to unattended"
    assert view({"id": "a", "state": "idle", "unattended": True})["mode_act"] == "Take over"

    r = client.post("/shell", data={"dir": str(tmp_path), "name": "handback"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    wait_state(client, sid, "idle")

    def rec() -> dict:
        return next(x for x in client.get("/api/sessions").json() if x["id"] == sid)

    soon = (datetime.now(UTC) + timedelta(seconds=2)).isoformat()
    later = (datetime.now(UTC) + timedelta(hours=3)).isoformat()
    client.post(f"/api/sessions/{sid}/mode", json={"unattended": True})
    assert client.post(f"/api/sessions/{sid}/stop", json={"until": soon}).json()["run_until"]
    client.post(f"/api/sessions/{sid}/mode", json={"unattended": False})  # taken over before it fell due
    assert wait_for(lambda: datetime.now(UTC).isoformat() > soon, timeout=5)
    client.post(f"/api/sessions/{sid}/mode", json={"unattended": True})  # handed back
    assert rec()["run_until"] is None and rec()["unattended"] is True
    assert client.post(f"/api/sessions/{sid}/stop", json={"until": later}).json()["run_until"]
    client.post(f"/api/sessions/{sid}/mode", json={"unattended": False})
    client.post(f"/api/sessions/{sid}/mode", json={"unattended": True})
    assert rec()["run_until"]  # still ahead: it stays
    client.post(f"/api/sessions/{sid}/kill")


def test_the_card_report_line_and_the_focus_reports_panel(client, tmp_path):
    """TD-028 step 4, design §4.5a: the card's **report line** (only when a channel is non-empty,
    dashed for what the agent derived), the Focus **Reports** panel with **Drop**, and the header
    **grants** chip. The panel and the chip are rendered by the client from the pushed record, so
    the page is asserted to carry their containers and the grant list the chip offers."""
    from agentorc.ui.app import templates, view

    base = {
        "id": "ao-x", "name": "w", "kind": "agent", "adapter": "shell", "dir": str(tmp_path),
        "state": "working", "since": "2026-09-12T10:00:00Z", "confidence": "hook", "tail": [],
    }  # fmt: skip
    card = templates.get_template("card.html")
    assert 'class="row report"' not in card.render(s=view(base))  # nothing declared, nothing derived
    declared = {
        **base,
        "lane": ["TD-027", "TD-019"],
        "progress": [{"ref": "TD-027", "status": "claimed", "pr": 60, "source": "declared", "at": ""}],
        "findings": [{"ref": "TD-029", "source": "declared", "at": ""}],
    }
    html = card.render(s=view(declared))
    assert "TD-027 → #60 · 0/2 done" in html and "1 filed" in html and "meta scraped" not in html
    # an entry the agent derived is dashed, like a scraped state, and says so
    derived = {**declared, "progress": [{**declared["progress"][0], "source": "derived"}]}
    html = card.render(s=view(derived))
    assert "TD-027~ → #60" in html and 'class="meta scraped"' in html and "did not declare it" in html

    r = client.post("/shell", data={"dir": str(tmp_path), "name": "rep"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    page = client.get(f"/focus/{sid}").text
    assert 'id="reportscard"' in page and 'id="progresslist"' in page and 'id="findinglist"' in page
    assert 'id="fgrants"' in page and 'data-grants="control"' in page
    # Drop is the person letting a claim go: it lands as a *declaration*, so the tick cannot undo it
    assert client.post(f"/api/sessions/{sid}/nonsense", json={}).status_code == 404
    assert client.post(f"/api/sessions/{sid}/drop", json={}).status_code == 400  # a drop needs a reference
    assert client.post(f"/api/sessions/{sid}/drop", json={"ref": "td-27"}).json() == {"ok": True}
    s = next(x for x in client.get("/api/sessions").json() if x["id"] == sid)
    assert [(p["ref"], p["status"], p["source"], p["why"]) for p in s["progress"]] == [
        ("TD-027", "dropped", "declared", "dropped from Focus")
    ]
    assert s["report"] == "TD-027 · 0/1 done" and s["report_derived"] is False
    # the grants chip: one click grants, the next revokes, and the record is what answers
    assert client.post(f"/api/sessions/{sid}/grants", json={"add": ["control"]}).json()["capabilities"] == ["control"]
    assert client.post(f"/api/sessions/{sid}/grants", json={"remove": ["control"]}).json()["capabilities"] == []
    assert client.post(f"/api/sessions/{sid}/grants", json={"add": ["sudo"]}).status_code == 400  # unknown grant
    client.post(f"/api/sessions/{sid}/kill")


def test_every_class_the_controllers_chips_name_is_one_the_stylesheet_draws(tmp_path):
    """TD-065: the chips named `chip`, and `chip` was in no stylesheet — so the one control that
    says who may act on a session was drawn as bare text, differently on the card (an `<a>`) and in
    the Focus header (a `<button>`). The two halves are one §4.5a row and the page rewrites the
    second of them itself, so all three places are checked: both templates and the `app.js` line
    that rebuilds the chips on a live delta."""
    import agentorc.ui as ui

    root = pathlib.Path(ui.__file__).parent
    css = (root / "static" / "app.css").read_text()
    sources = {
        name: (root / name).read_text() for name in ("templates/card.html", "templates/focus.html", "static/app.js")
    }
    for name, text in sources.items():
        assert "badge controller" in text, f"{name} does not draw the controllers chip as a badge"
        assert 'class="chip' not in text, f"{name} still names the undefined class"
    for rule in (".badge.controller", ".badge.controller.scraped"):
        assert rule in css, f"{rule} is named by the chips and defined by nothing"


def test_the_membership_controls(client, tmp_path):
    """TD-036 step 3, design §4.5a: the card's **under `<controller>`** chip, the Focus **controllers**
    chip, the lead's **Members** list, and the New session **Controllers** picker. Both
    directions are derived from the fleet on render, never stored, so the assertions go through
    the real pages rather than a hand-built view."""
    from agentorc.ui.app import templates, view

    base = {
        "id": "ao-w", "name": "w", "kind": "agent", "adapter": "shell", "dir": str(tmp_path),
        "state": "working", "since": "2026-09-12T10:00:00Z", "confidence": "hook", "tail": [],
    }  # fmt: skip
    orc = {**base, "id": "ao-orc", "name": "orc", "capabilities": ["control"]}
    card = templates.get_template("card.html")
    # no controllers: no chip at all — a person's own session has none, and that is the common case
    assert "under" not in card.render(s=view(base, [base]))
    # a controller that exists shows by *name* and links to it; one that is gone keeps its entry
    held = {**base, "controllers": ["ao-orc"]}
    html = card.render(s=view(held, [held, orc]))
    assert 'href="/focus/ao-orc"' in html and ">orc<" in html and "scraped" not in html
    gone = {**base, "controllers": ["ao-vanished"]}
    html = card.render(s=view(gone, [gone]))
    assert ">ao-vanished<" in html and "badge controller scraped" in html and "re-attach or remove it" in html

    # the live pages
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "mw"}, follow_redirects=False)
    worker = r.headers["location"].rsplit("/", 1)[-1]
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "morc"}, follow_redirects=False)
    orc_id = r.headers["location"].rsplit("/", 1)[-1]

    page = client.get(f"/focus/{worker}").text
    assert 'id="fcontrollers"' in page and "none — nobody may act on this session" in page
    assert 'id="members"' not in page  # not a lead: no member list

    # the chip adds one, and the agent is what actually decides
    assert client.post(f"/api/sessions/{worker}/controllers", json={"add": [orc_id]}).json() == {
        "ok": True,
        "controllers": [orc_id],
    }
    assert client.post(f"/api/sessions/{worker}/controllers", json={"add": [worker]}).status_code == 400
    page = client.get(f"/focus/{worker}").text
    assert f'data-act="uncontrol" data-id="{worker}" data-who="{orc_id}"' in page
    assert ">morc ×<" in page

    # the Members list appears once the session holds the grant, and lists what names it
    client.post(f"/api/sessions/{orc_id}/grants", json={"add": ["control"]})
    page = client.get(f"/focus/{orc_id}").text
    assert 'id="members"' in page and f'href="/focus/{worker}"' in page and ">mw<" in page
    # the card of the worker now says who is over it
    assert ">morc<" in client.get("/").text

    # the New session picker offers the grant-holders, and nothing is ticked by default
    form = client.get("/new").text
    assert f'name="controller" value="{orc_id}"' in form and "None selected: nobody may" in form
    assert f'value="{orc_id}" checked' not in form

    # a typed *name* is resolved to an id here, where the fleet is known — the agent stores what
    # it is given, so an unresolved name would sit in the list as a controller that can never act
    client.post(f"/api/sessions/{worker}/controllers", json={"remove": [orc_id]})
    assert client.post(f"/api/sessions/{worker}/controllers", json={"add": ["morc"]}).json()["controllers"] == [orc_id]
    r = client.post(f"/api/sessions/{worker}/controllers", json={"add": ["ghost"]})
    assert r.status_code == 400 and "no session 'ghost'" in r.json()["detail"]
    # removing takes whatever it is given: an entry naming a session that is gone is exactly the
    # one a person most needs to remove
    assert client.post(f"/api/sessions/{worker}/controllers", json={"remove": ["ao-vanished"]}).status_code == 200
    # the body is validated: a bare string would otherwise become one controller per character
    for bad in ({"add": "ao-x"}, {"add": [None]}, {"add": [""]}):
        assert client.post(f"/api/sessions/{worker}/controllers", json=bad).status_code == 400
    assert client.post(f"/api/sessions/{worker}/grants", json={"add": "control"}).status_code == 400

    # the picker's tick reaches the created session (the form round trip, not just its rendering)
    r = client.post(
        "/new",
        data={"name": "picked", "dir": str(tmp_path), "adapter": "shell", "where": "here", "controller": [orc_id]},
        follow_redirects=False,
    )
    picked = r.headers["location"].rsplit("/", 1)[-1]
    picked_v = next(x for x in client.get("/api/sessions").json() if x["id"] == picked)
    # the record keeps ids; the display list is `under`, so the two shapes never share a key
    assert picked_v["controllers"] == [orc_id] and [c["id"] for c in picked_v["under"]] == [orc_id]
    # …and a form with nothing ticked starts with nobody able to act on it
    r = client.post(
        "/new",
        data={"name": "unpicked", "dir": str(tmp_path), "adapter": "shell", "where": "here"},
        follow_redirects=False,
    )
    unpicked = r.headers["location"].rsplit("/", 1)[-1]
    assert next(x for x in client.get("/api/sessions").json() if x["id"] == unpicked)["controllers"] == []

    # the Members list carries the lane the §4.5a row promises
    from agentorc.ui.app import view as _view

    laned = {**base, "id": "ao-m", "lane": ["TD-027", "TD-019"], "controllers": ["ao-orc"]}
    assert _view(orc, [orc, laned])["members"][0]["lane"] == "TD-027, TD-019"

    # removing the last controller leaves the session running, with nobody able to act (§4.8)
    client.post(f"/api/sessions/{picked}/controllers", json={"remove": [orc_id]})
    assert client.post(f"/api/sessions/{worker}/controllers", json={"remove": [orc_id]}).json()["controllers"] == []
    assert next(x for x in client.get("/api/sessions").json() if x["id"] == worker)["state"] != "closed"
    page = client.get(f"/focus/{orc_id}").text
    assert "no members yet" in page


def test_the_new_session_form_shows_the_grants_it_would_give_and_only_starts_what_is_ticked(client, tmp_path):
    """Design §4.5a New session **Grants** checkboxes (TD-028 step 5).

    A preset's grants used to apply unseen: picking `lead` in the form started a session
    that could send to, wrap up and kill other sessions, and nothing on the page said so. That is
    the one power in the system a person should never acquire without seeing it — and the reverse
    matters as much: a tick the person removed has to be honoured, not overridden by the preset.
    """
    from agentorc.ui.app import GRANT_NOTES
    from sessionorc.models import GRANTS

    r = client.get("/new")
    assert r.status_code == 200
    for g in GRANTS:
        assert f'name="grant" value="{g}"' in r.text
        assert GRANT_NOTES[g] in r.text  # §4.5a: "a one-line warning of what the grant allows"
    # the role options carry their grants, which is what ticks the boxes as the Role changes
    assert 'data-grants="control"' in r.text and 'data-grants=""' in r.text

    # ticked: the session gets it
    r = client.post(
        "/new",
        data={
            "name": "g1",
            "dir": str(tmp_path),
            "adapter": "hookstub",
            "role": "manager",
            "grant": "control",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    sid = r.headers["location"].rsplit("/", 1)[-1]
    got = next(x for x in client.get("/api/sessions").json() if x["id"] == sid)
    assert "control" in (got.get("capabilities") or [])
    # attended, so the default bound of a role that sets none is not written (§4.8, TD-249): the
    # worker presets' own is, and so is the default on an unattended start
    assert got["context_bound"] == ""  # the card's text: no bound
    for name, role, extra, bound in (("g4", "grinder", {}, "300k"), ("g5", "manager", {"unattended": "on"}, "300k")):
        form = {"name": name, "dir": str(tmp_path / name), "adapter": "hookstub", "role": role, **extra}
        (tmp_path / name).mkdir()
        made = client.post("/new", data=form, follow_redirects=False)
        assert made.status_code == 303, made.text
        sid = made.headers["location"].rsplit("/", 1)[-1]
        assert next(x for x in client.get("/api/sessions").json() if x["id"] == sid)["context_bound"] == bound

    # unticked on a `manager` preset: the person's decision stands over the preset's grants.
    # A second directory, since one agent session per directory is refused (§9 invariant 2).
    other = tmp_path / "other"
    other.mkdir()
    r = client.post(
        "/new",
        data={"name": "g2", "dir": str(other), "adapter": "hookstub", "role": "manager"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    sid2 = r.headers["location"].rsplit("/", 1)[-1]
    got2 = next(x for x in client.get("/api/sessions").json() if x["id"] == sid2)
    assert (got2.get("capabilities") or []) == []
    for s_ in (sid, sid2):
        client.post(f"/api/sessions/{s_}/kill")


def test_every_grant_has_the_one_line_warning_the_control_table_promises():
    """A grant added to `GRANTS` without a note would ship an unexplained checkbox."""
    from agentorc.ui.app import GRANT_NOTES
    from sessionorc.models import GRANTS

    assert set(GRANT_NOTES) == set(GRANTS)
    assert all(GRANT_NOTES[g].strip() for g in GRANTS)


def test_the_composer_says_whether_send_starts_a_turn_or_steers_one():
    """Design §4.3/§4.5a (TD-047): one button does two jobs and the person cannot tell which.

    Send is enabled while the agent works — Claude Code queues input typed at it — so the same
    button starts a new piece of work on an idle session and redirects work already in flight on a
    working one. The design had the behaviour right and no word for it; `turn` and `steer` are the
    words, and the composer has to say the same thing the prose does without the reader having to
    decode the state pill.
    """
    js = (pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui" / "static" / "app.js").read_text()
    assert '"→ Steer" : "→ Send"' in js
    assert "steers the turn in flight" in js and "starts a new turn" in js

    # A dead or out-of-reach session has no turn to start or steer, and the composer used to sit
    # enabled there saying nothing. Merely useless before; "starts a new turn" at a killed session
    # would be a lie, so those states are excluded ahead of the branch that says it.
    dead = js[js.index('} else if (v.state === "exited"') : js.index("design §4.3: one button, two jobs")]
    assert '"closed"' in dead and '"unreachable"' in dead and "compose.disabled = true" in dead
    assert "this session's process has ended" in dead and "cannot be reached" in dead
    # the banner offers Resume only on `exited` with an adapter id, so the hint must not promise it
    # from TD-081 step 2 a `closed` record resumes too: the hint promises Resume exactly where the
    # banner offers it — on a record that holds a tool session id, whichever of the two states
    assert '(v.state === "exited" || v.state === "closed") && v.adapter_id' in dead
    assert "start a new session here" in dead and "resume it under its own name" in dead

    # `stalled?` is a working session that stopped producing output (§4.2) — a turn in flight, so
    # it steers. `limited` must not claim a turn starts now: §4.2 says nothing the person does
    # unblocks a cap, and §4.5a's controls for it are Switch profile and Wait.
    assert 'v.state === "working" || v.state === "stalled?"' in js
    assert "the profile is at its cap: what you send waits" in js
    assert "this session looks stalled" in js

    # and the design says it where §4.5a points: the Send row and the §4.3 rule
    send_row = next(ln for ln in section("4.5a").split("\n") if ln.startswith("| Focus composer | **Send** |"))
    assert "Steer" in send_row and "starts a new turn" in send_row
    assert "to an `idle` session it starts a turn; to a `working` one it steers the turn in flight" in section("4.3")


def test_a_stop_time_can_be_set_and_cleared_from_focus(client, tmp_path):
    """Design §4.5a card/Focus **stops** note, its editing half (§6, TD-026).

    The stop time was settable at New session and from `ao until`, and nowhere else: a person who
    set `+8h` and wanted another hour had to leave the page for the CLI. The agent already had the
    RPC; this is the control. Clearing it is a decision made out loud — the alternative was
    restarting the session.
    """
    r = client.post(
        "/new",
        data={"name": "st", "dir": str(tmp_path), "adapter": "hookstub", "unattended": "on", "until": "+8h"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    sid = r.headers["location"].rsplit("/", 1)[-1]
    assert "stops " in client.get(f"/focus/{sid}").text

    # move it
    got = client.post(f"/api/sessions/{sid}/stop", json={"until": "+2h"}).json()
    assert got["ok"] and got["stop_note"].startswith("stops ") and got["run_until"]

    # clear it: the fact a person most needs is that nothing will stop it
    got = client.post(f"/api/sessions/{sid}/stop", json={"until": ""}).json()
    assert got["run_until"] is None and got["stop_note"] == ""
    page = client.get(f"/focus/{sid}").text
    assert "no stop time" in page and 'data-act="stop"' in page

    # a time the agent cannot parse is refused, not stored
    assert client.post(f"/api/sessions/{sid}/stop", json={"until": "half six"}).status_code == 400
    client.post(f"/api/sessions/{sid}/kill")


def test_focus_offers_no_stop_control_on_an_interactive_session(client, tmp_path):
    """A stop time is a policy and policies leave interactive sessions alone (§4.2): the agent
    refuses one either way, and a control that is always refused is worse than none."""
    other = tmp_path / "interactive"
    other.mkdir()
    r = client.post("/new", data={"name": "si", "dir": str(other), "adapter": "hookstub"}, follow_redirects=False)
    assert r.status_code == 303
    sid = r.headers["location"].rsplit("/", 1)[-1]
    assert 'data-act="stop"' not in client.get(f"/focus/{sid}").text
    assert client.post(f"/api/sessions/{sid}/stop", json={"until": "+1h"}).status_code >= 400
    client.post(f"/api/sessions/{sid}/kill")


def test_the_stop_control_round_trips_and_can_actually_be_hidden():
    """Two traps this walked into, both caught before merge.

    The edit box used to be filled from the badge's own text. `stop_note` reads *stops Mon 06:00*
    once the stop is not today and gains *· wrap-up sent* after the agent has asked, and both come
    back as a time `stop_time` refuses — so editing a stop time on any day but today would have
    failed with "not a time". It is filled from the record's own value instead.

    And `.badge` is `display: inline-block`, which beats the `hidden` attribute — exactly the trap
    the stylesheet already documents for `.btn`.
    """
    root = pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui"
    js = (root / "static" / "app.js").read_text()
    css = (root / "static" / "app.css").read_text()
    assert "b.dataset.until" in js and "el.dataset.until = v.run_until" in js
    assert "textContent.slice" not in js, "the edit box must not be filled from the badge's text"
    assert ".badge[hidden]" in css

    # the two formats that would have come back refused
    from agentorc.cli import stop_time
    from sessionorc.client import AgentError
    from sessionorc.models import stop_note

    note = stop_note({"run_until": "2030-01-07T06:00:00Z", "wrapup_sent_at": "2030-01-07T05:50:00Z"})
    assert note.startswith("stops ") and "wrap-up sent" in note
    with pytest.raises(AgentError):
        stop_time(note[len("stops ") :])


def test_the_inbox_panel_the_unread_chip_message_reply_and_delete(client, tmp_path):
    """TD-052 step 8, design §4.5a's mail rows (§4.10): the card's **unread** chip only above zero,
    the Focus **Inbox** panel fed by the `inbox` RPC as a person's read (no `read_at`), **Message**
    landing a `note` from the person, **Reply** landing a `reply` from the person in the sender's
    inbox and closing the `ask`, and delete removing this session's copy only."""
    import asyncio

    from agentorc.ui.app import templates, view
    from sessionorc.client import LocalClient

    base = {
        "id": "ao-x", "name": "w", "kind": "agent", "adapter": "shell", "dir": str(tmp_path),
        "state": "idle", "since": "2026-09-12T10:00:00Z", "confidence": "hook", "tail": [],
    }  # fmt: skip
    card = templates.get_template("card.html")
    assert "badge unread" not in card.render(s=view({**base, "unread": 0}))
    one = card.render(s=view({**base, "unread": 1}))
    assert 'class="badge unread" href="/focus/ao-x#inbox"' in one and "✉ 1" in one
    assert 'data-act="message"' in one  # **Message…** in the card's more ▾

    def mk(name):
        r = client.post("/shell", data={"dir": str(tmp_path), "name": name}, follow_redirects=False)
        return r.headers["location"].rsplit("/", 1)[-1]

    lead, worker = mk("mlead"), mk("mworker")
    client.post(f"/api/sessions/{worker}/controllers", json={"add": [lead]})

    async def as_lead():
        async with LocalClient(caller=lead) as c:
            return await c.call("msg", to=worker, text="are you done?", kind="ask", about="TD-052")

    ask = asyncio.run(as_lead())["entry"]["id"]
    rec = lambda sid: next(x for x in client.get("/api/sessions").json() if x["id"] == sid)  # noqa: E731
    assert rec(worker)["unread"] == 1 and "inbox" not in rec(worker)  # counts ride the record, bodies do not
    org = client.get("/").text
    assert f'href="/focus/{worker}#inbox"' in org and f'href="/focus/{lead}#inbox"' not in org

    page = client.get(f"/focus/{worker}").text
    assert 'id="inboxcard"' in page and 'id="inboxlist"' in page and 'data-act="message"' in page
    got = client.get(f"/api/sessions/{worker}/inbox").json()
    [e] = got["entries"]
    assert (e["id"], e["from"], e["from_name"], e["kind"], e["about"]) == (ask, lead, "mlead", "ask", "TD-052")
    assert e["read_at"] is None and e["bound"] and e["closed_by"] is None
    # the row's two halves, server-rendered (TD-138)
    assert (e["lead_html"], e["rest_html"]) == ("<p>are you done?</p>", "")
    assert rec(worker)["unread"] == 1  # the panel's read marked nothing: a person is not the session

    # **Message**: a note from the person, into this session's inbox
    assert client.post(f"/api/sessions/{worker}/message", json={"kind": "reply", "text": "x"}).status_code == 400
    assert client.post(f"/api/sessions/{worker}/message", json={"kind": "note", "text": " "}).status_code == 400
    sent = client.post(f"/api/sessions/{worker}/message", json={"kind": "note", "text": "hold off", "about": ""})
    assert sent.status_code == 200 and sent.json()["delivered"] == [worker]
    note = sent.json()["id"]
    entries = client.get(f"/api/sessions/{worker}/inbox").json()["entries"]
    assert [(x["id"], x["from"], x["from_name"], x["kind"]) for x in entries][-1] == (note, "person", "person", "note")
    assert rec(worker)["unread"] == 2

    # **Reply**: a `reply` from the person, addressed to the entry's sender, closing the ask
    assert client.post(f"/api/sessions/{worker}/reply", json={"text": "yes"}).status_code == 400
    r = client.post(f"/api/sessions/{worker}/reply", json={"reply_to": ask, "text": "yes, merged"})
    assert r.status_code == 200 and r.json()["delivered"] == [lead]
    [back] = client.get(f"/api/sessions/{lead}/inbox").json()["entries"]
    assert (back["from"], back["kind"], back["reply_to"], back["text"]) == ("person", "reply", ask, "yes, merged")
    asked = next(x for x in client.get(f"/api/sessions/{worker}/inbox").json()["entries"] if x["id"] == ask)
    assert asked["closed_by"] == r.json()["id"]

    # delete: this session's copy only
    before = [x["id"] for x in client.get(f"/api/sessions/{worker}/inbox").json()["entries"]]
    assert client.post(f"/api/sessions/{worker}/unmail", json={}).status_code == 400
    assert client.post(f"/api/sessions/{worker}/unmail", json={"msg": ask}).json() == {"ok": True}
    after = [x["id"] for x in client.get(f"/api/sessions/{worker}/inbox").json()["entries"]]
    assert after == [x for x in before if x != ask]
    assert client.post(f"/api/sessions/{worker}/unmail", json={"msg": ask}).status_code == 400  # already gone
    assert [x["id"] for x in client.get(f"/api/sessions/{lead}/inbox").json()["entries"]] == [back["id"]]
    # the panel refetches bodies through `inbox` when the record's counts or marks change — never
    # from the pushed record, which carries none (§4.10 "A bounded body")
    js = (pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui" / "static" / "app.js").read_text()
    assert "JSON.stringify([v.unread || 0, v.mail || {}])" in js and "/inbox`" in js
    for sid in (lead, worker):
        client.post(f"/api/sessions/{sid}/kill")


def test_the_top_bar_person_inbox_lists_replies_and_deletes(client, tmp_path):
    """TD-052 step 8, design §4.5a Org top bar **Inbox** (§4.10): the Org page renders the top
    bar's number — the **Needs you** count since TD-069 step 1, so one open `ask` to the person
    makes it 1; the feed lists an entry a session sent with `ao msg person`, with its sender's id
    and name, as a person's read (no `read_at`); **Reply** lands a `reply` from the person in the
    sender's inbox and closes its `ask`; delete removes the entry from the person inbox and leaves
    the sender's copy."""
    import asyncio

    from sessionorc.client import LocalClient

    r = client.post("/shell", data={"dir": str(tmp_path), "name": "asker"}, follow_redirects=False)
    sender = r.headers["location"].rsplit("/", 1)[-1]
    assert 'class="badge needs hidden" id="personneeds"></span>' in client.get("/").text  # nothing at zero

    async def as_sender(**kw):
        async with LocalClient(caller=sender) as c:
            return await c.call("msg", to="person", **kw)

    ask = asyncio.run(as_sender(text="merge PR 9?", kind="ask", about="TD-052"))["entry"]["id"]
    assert 'class="badge needs" id="personneeds">1</span>' in client.get("/").text

    got = client.get("/api/person/inbox").json()
    [e] = got["entries"]
    assert got["unread"] == 1 and (e["id"], e["from"], e["from_name"], e["kind"], e["about"]) == (
        ask, sender, "asker", "ask", "TD-052"
    )  # fmt: skip
    # an `ask` to the person carries no bound and never expires (§4.10, 2026-09-19, TD-069 step 0)
    assert e["read_at"] is None and e["bound"] is None and e["closed_by"] is None
    assert client.get("/api/person/inbox").json()["entries"][0]["read_at"] is None  # a person's read sets nothing

    # **Reply**: into the sender's inbox, from the person, closing the ask
    assert client.post("/api/person/reply", json={"text": "yes"}).status_code == 400
    rep = client.post("/api/person/reply", json={"reply_to": ask, "text": "yes, merge it"})
    assert rep.status_code == 200 and rep.json()["delivered"] == [sender]
    [back] = client.get(f"/api/sessions/{sender}/inbox").json()["entries"]
    assert (back["from"], back["kind"], back["reply_to"], back["text"]) == ("person", "reply", ask, "yes, merge it")
    [held] = client.get("/api/person/inbox").json()["entries"]
    assert held["closed_by"] == rep.json()["id"]

    # delete: from the person inbox only
    assert client.post("/api/person/unmail", json={}).status_code == 400
    # the ask was closed by the Reply above, so deleting it strips it; an *open* one would be
    # declined and kept instead (§4.10 *Deleting is declining*, TD-069 step 0 — pinned in test_mail)
    assert client.post("/api/person/unmail", json={"msg": ask}).json() == {
        "ok": True,
        "unread": 0,
        "declined": False,
    }
    assert client.get("/api/person/inbox").json()["entries"] == []
    assert client.post("/api/person/unmail", json={"msg": ask}).status_code == 400  # already gone
    assert client.post("/api/person/nope", json={}).status_code == 404


def test_the_person_inbox_feed_carries_a_steer_and_its_delete_declines(client, tmp_path):
    """TD-069 step 0, design §4.10 and §4.5a **Inbox**: the fields the new kinds added are on the
    entries the person-inbox route hands its surfaces — a `steer`'s default and bound, an `ask` to
    the person with no bound — and the Focus Inbox panel's renderer, which stayed when step 1
    retired the top bar's dialog, still draws them and offers no Reply on a `system` note. Delete
    on an open question goes through the **decline** semantics, which the entry itself then says.
    The Inbox page's own rows are tests/test_ui_inbox.py."""
    import asyncio
    import pathlib

    from sessionorc.client import LocalClient

    r = client.post("/shell", data={"dir": str(tmp_path), "name": "steerer"}, follow_redirects=False)
    sender = r.headers["location"].rsplit("/", 1)[-1]

    async def as_sender(**kw):
        async with LocalClient(caller=sender) as c:
            return await c.call("msg", to="person", **kw)

    steer = asyncio.run(as_sender(text="which branch?", kind="steer", default="off main"))["entry"]
    ask = asyncio.run(as_sender(text="merge PR 9?", kind="ask"))["entry"]["id"]
    got = {e["id"]: e for e in client.get("/api/person/inbox").json()["entries"]}
    # every field a surface draws is on the entry the API hands it
    assert got[steer["id"]]["default"] == "off main" and got[steer["id"]]["bound"]
    assert got[steer["id"]]["paused_at"] is None and got[steer["id"]]["snoozed_until"] is None
    assert got[ask]["bound"] is None and got[ask]["closed_reason"] is None

    js = (pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui" / "static" / "app.js").read_text()
    assert 'e.kind === "steer"' in js and "e.default" in js  # the default and the lapse line
    assert 'e.closed_reason === "lapsed"' in js and 'e.from === "system"' in js  # no Reply on a system note

    # Delete on an open ask declines it: the entry stays, closed, and the sender is told
    assert client.post("/api/person/unmail", json={"msg": ask}).json()["declined"] is True
    held = {e["id"]: e for e in client.get("/api/person/inbox").json()["entries"]}
    assert held[ask]["closed_reason"] == "declined" and held[ask]["closed_at"]
    [told] = client.get(f"/api/sessions/{sender}/inbox").json()["entries"]
    assert told["from"] == "system" and told["text"] == f"ask {ask} declined by the person"
    client.post(f"/api/sessions/{sender}/kill")


def test_a_declaration_of_no_work_is_a_chip_on_the_card_and_the_focus_header(tmp_path, monkeypatch):
    """design §4.5a **out of work** chip (§4.9a, TD-053 step 6): a session that declared it found
    nothing left says so where a person looks, with the reason on hover. It is not a state — the
    record still reads `idle` — and it is drawn for any session that declared, since a hand-started
    worker may run out too. A card with neither report channel still draws the row for it."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    base = {
        "id": "ao-x-2", "name": "w", "kind": "interactive", "adapter": "claude-code", "dir": str(tmp_path),
        "state": "idle", "since": "2026-09-17T16:00:00Z", "confidence": "hook", "pane": True, "tail": [],
    }  # fmt: skip
    card = templates.get_template("card.html")
    assert "out of work" not in card.render(s=view(base))
    assert view(base)["out_of_work"] is None

    # no apostrophe: Jinja escapes one, and a test that reads the raw HTML would be asserting the
    # escaping rather than the hover
    why = "every open entry is parked on a person or belongs to another team"
    at = (datetime.now(UTC) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    done = {**base, "out_of_work": {"at": at, "why": why}}
    html = card.render(s=view(done))
    # in the slot since TD-095 (an ending): the words and the reason's first line, and on hover the
    # whole reason and when it was said — the card's one clock is the state's
    assert f"out of work — {why}" in html and "out of work 2h" in view(done)["slot"]["full"]
    assert 'class="badge oow' not in html
    assert view(done)["out_of_work"]["age"].startswith("2h")
    # the chip does not pretend to be a state: the card still reads `idle` (§4.2's unseen-idle rule)
    assert 'data-state="idle"' in html and "out-of-work" not in html
    # a declaration with no reason is still a declaration — the hover says so rather than being empty
    bare = card.render(s=view({**base, "out_of_work": {"at": at, "why": ""}}))
    assert "out of work" in bare and "no reason recorded" in bare
    # and a record whose declaration has no instant is not one (the agent refuses to write it)
    assert view({**base, "out_of_work": {"why": why}})["out_of_work"] is None

    # A malformed declaration costs that card its chip and nothing else. `view` runs for every
    # session on the grid, so a raise here would take down the page rather than the one card — the
    # failure PR #131's review caught for a `run_until` of *half six*, and the same guard is owed to
    # a field a different build or a hand repair could leave in any shape (review of PR #203).
    for junk in ("out of work", ["nope"], 7, {"at": 12345}, {"at": "half six"}, {"at": {"nested": 1}}):
        d = view({**base, "out_of_work": junk})
        assert d["out_of_work"] is None or d["out_of_work"]["age"] == ""
        assert "out of work" not in card.render(s=d) or d["out_of_work"] is not None


def test_the_focus_header_carries_the_out_of_work_chip_from_the_record(client, tmp_path):
    """The other half of §4.5a's row (§4.9a, TD-053 step 6), on the live page: the declaration the
    session itself wrote is what the header reads — `ao progress none --why` is refused from anyone
    but the session (invariant 14), so this goes through the agent rather than a hand-built record."""
    from sessionorc.client import call_sync

    r = client.post("/shell", data={"dir": str(tmp_path), "name": "oow"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]

    # From PR #323 the chip is **always in the Focus page and hidden until true**, so the header's
    # render can bring it and take it away on a pushed delta without a reload; what the record
    # decides is therefore whether it is *shown*, and that is what is asserted — not whether the
    # words are in the page source.
    def shown(page: str) -> bool:
        chip = page.split('id="foow"')[0].rsplit("<span", 1)[-1]
        return "hidden" not in chip

    assert not shown(client.get(f"/focus/{sid}").text)

    why = "nothing open that this brief does not exclude"
    call_sync("progress", id=sid, status="none", why=why, caller=sid)
    page = client.get(f"/focus/{sid}").text
    assert shown(page) and why in page
    assert "out of work" in client.get("/").text  # and the card on the Org page

    # a claim means it has work again — the record clears the declaration, and so does the header
    call_sync("progress", id=sid, ref="TD-053", status="claimed", caller=sid)
    assert not shown(client.get(f"/focus/{sid}").text)
    call_sync("kill", id=sid)


def test_the_card_shows_what_the_session_says_it_is_doing_before_the_tail(tmp_path, monkeypatch):
    """design §4.5a card **doing** line (§4.8, TD-074): the slot shows what needs a person first,
    then the `doing` line with its age, then the tail. A session that has said nothing keeps exactly
    the tail it showed before 2026-09-19, which is what a `shell` — whose tail *is* the work — always
    does. The text is a model's: escaped, shown, and never a control."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    base = {
        "id": "ao-x-3", "name": "w", "kind": "agent", "adapter": "claude-code", "dir": str(tmp_path),
        "state": "working", "since": "2026-09-19T16:00:00Z", "confidence": "hook", "pane": True,
        "tail": ["▸▸ bypass permissions on (shift+tab…"],
    }  # fmt: skip
    card = templates.get_template("card.html")
    # said nothing: the tail, exactly as before
    assert view(base)["doing"] is None
    assert "bypass permissions on" in card.render(s=view(base))

    at = (datetime.now(UTC) - timedelta(minutes=11)).isoformat().replace("+00:00", "Z")
    text = "rebasing the branch onto main and re-running the suite"
    said = view({**base, "doing": {"text": text, "at": at}})
    assert said["doing"] == {"text": text, "age": "11m"}
    html = card.render(s=said)
    assert text in html and "says · 11m ago" in html
    assert "bypass permissions on" not in html  # the tool's chrome gives way to the session's word
    # and the same line for an idle session, in place of *last: …* (one that is not ready to close:
    # that checklist and its Close button are what an idle card shows when it has earned them)
    idle = card.render(s=view({**base, "state": "idle", "subagents": 1, "doing": {"text": text, "at": at}}))
    assert text in idle and "last:" not in idle
    # it is shown, never acted on: no button, and the text is escaped (Jinja autoescape)
    marked = card.render(s=view({**base, "doing": {"text": "<b>claim</b> & go", "at": at}}))
    assert "&lt;b&gt;claim&lt;/b&gt; &amp; go" in marked and 'data-act="doing' not in marked
    # a malformed field costs that card its line and nothing else — `view` runs for every card
    for junk in ("doing", ["nope"], 7, {"at": at}, {"text": "", "at": at}, {"text": 7, "at": at}):
        d = view({**base, "doing": junk})
        assert d["doing"] is None
        assert "bypass permissions on" in card.render(s=d)
    # an instant it cannot read is still a line: the age is empty, the words stand
    noage = view({**base, "doing": {"text": text, "at": "half six"}})
    assert noage["doing"] == {"text": text, "age": ""}
    assert text in card.render(s=noage)


def test_a_teams_header_does_not_repeat_its_managers_card(tmp_path, monkeypatch):
    """design §4.5a **team groups** (§4.8, TD-074; reversed by Paul 2026-09-21, TD-095): the header
    carried the manager's name, state and line, and the manager's card said them again directly
    beneath it. The line is on the card, the first in the group; the header says where the team's
    sessions are and how many are in each state."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import team_groups, templates, view

    at = (datetime.now(UTC) - timedelta(minutes=11)).isoformat().replace("+00:00", "Z")
    records = [
        {"id": "ao-orc", "name": "orc", "state": "idle", "dir": str(tmp_path), "kind": "agent",
         "team": "ao-grind", "capabilities": ["control"], "doing": {"text": "round 3: reviewing PR 236", "at": at}},
        {"id": "ao-g1", "name": "g1", "state": "working", "dir": str(tmp_path), "kind": "agent",
         "team": "ao-grind", "controllers": ["ao-orc"], "tail": ["…"]},
    ]  # fmt: skip
    (g,) = team_groups([view(r, records) for r in records])
    head = templates.get_template("group_head.html").render(g=g)
    assert "round 3: reviewing PR 236" not in head and ">orc<" not in head and "s-idle" not in head
    # the counts stay in the group (a stopped team's row draws them), and on a live team's header
    # only for its fold (TD-176, TD-194): unfolded, CSS hides them, since the compact cards say it
    assert g["counts"] == ["1 working", "1 unseen"] and "▾ 2 sessions" in head  # the count once, on the fold (TD-418)
    assert 'class="meta counts foldonly">· 1 working · 1 unseen<' in head
    assert g["place"].endswith(f" / {tmp_path}") and g["place"] in head  # no repo: host / directory
    card = templates.get_template("card.html").render(s=g["members"][0])
    assert "round 3: reviewing PR 236" in card  # the manager's line is on its own card


def test_the_role_label_is_what_the_badge_the_team_header_and_the_inbox_row_show(tmp_path, monkeypatch):
    """design §4.8 *The names* (TD-076 step 3): a role's **label** is what the page shows in place of
    the bare key — the role badge on a card, the team header's word for its manager, an Inbox row —
    and the key stays on hover, since it is what `--role` and `--json` read. A repo's own label wins;
    one this host cannot read falls back to the default, the name raised, never to nothing. It is a
    person's text and is drawn escaped."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    import asyncio

    from agentorc.ui import app as uiapp

    roles = "roles:\n  manager: {label: Shift <lead>}\n  grinder: {label: TD grinder}\n"
    (tmp_path / ".agentorc.yml").write_text(roles)
    base = {"kind": "agent", "adapter": "claude-code", "dir": str(tmp_path), "repo": str(tmp_path),
            "state": "idle", "since": "2026-09-20T16:00:00Z", "team": "ao-grind"}  # fmt: skip
    records = [
        {**base, "id": "ao-m", "name": "manager-ao-1", "role": "manager", "capabilities": ["control"]},
        {**base, "id": "ao-g", "name": "grinder-ao-1", "role": "grinder", "controllers": ["ao-m"]},
        {**base, "id": "ao-x", "name": "elsewhere", "role": "grinder", "repo": "/no/such/repo", "team": None},
    ]
    uiapp._icon_cache.clear()
    icons = asyncio.run(uiapp.role_icons(records))
    views = [uiapp.view(r, records, icons=icons) for r in records]
    card = uiapp.templates.get_template("card.html")
    assert ">TD grinder</span>" in card.render(s=views[1])
    assert "the role preset it was started under — grinder" in card.render(s=views[1])  # the key, on hover
    assert ">Grinder</span>" in card.render(s=views[2])  # a repo this host cannot read: the default
    # the manager's card carries its label, escaped; the team header names a manager only when its
    # card is in another group (TD-095: the header no longer repeats the manager's card)
    assert ">Shift &lt;lead&gt;</span>" in card.render(s=views[0])
    (g,) = [g for g in uiapp.team_groups(views) if g["team"] == "ao-grind"]
    head = uiapp.templates.get_template("group_head.html").render(g={**g, "manager_elsewhere": True})
    assert 'Shift &lt;lead&gt; <a class="name" href="/focus/ao-m">manager-ao-1</a> elsewhere' in head
    assert "manager-ao-1" not in uiapp.templates.get_template("group_head.html").render(g=g)
    # …and an Inbox row carries the badge the card does
    row = uiapp.state_rows([{**views[1], "state": "stalled?"}])[0]
    assert row["role_label"] == "TD grinder"


def test_a_permission_on_an_unreachable_host_sends_the_person_to_the_hosts_own_dialog(tmp_path, monkeypatch):
    """§4.4a "Permission prompts follow the same line" (TD-057 step 4b.1): with the host's link down
    the waiter is out of reach, so the card offers no Allow / Deny — whose `decide` would be refused —
    and says where the prompt can still be answered."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    s = {
        "id": "ao-x-w@laptop", "name": "w", "kind": "interactive", "adapter": "claude-code", "dir": "/x",
        "host": "laptop", "state": "unreachable", "last_state": "needs-you", "since": "2026-09-18T10:00:00Z",
        "confidence": "hook", "pane": True, "tail": [], "host_link": {"up": False, "why": "the lid closed"},
        "pending": {"kind": "permission", "text": "Bash: git push", "tool_use_id": "tu", "host_unreachable": True},
    }  # fmt: skip
    html = templates.get_template("card.html").render(s=view(s))
    assert "permission: Bash: git push — answer it at laptop" in html
    assert 'data-act="allow"' not in html


async def test_a_nodes_org_page_says_its_link_and_where_the_org_is(agent):
    """TD-057 step 4b.3 (*Left for step 4*): the Org page on a node reads the `host` RPC and says
    whether its link to the home is up — here it has never dialed, so *offline* — and still
    renders this host's sessions although the node refuses the mailbox; the Teams strip names where
    the org is, and a Start or Stop pressed there answers with that note rather than *no team*."""
    import asyncio

    from sessionorc import paths

    (paths.home() / "hosts.yml").write_text("home: elsewhere\n")
    agent.mode, agent.home = "node", "elsewhere"
    try:
        from agentorc.ui.app import create_app

        def browse():
            with TestClient(create_app()) as c:
                return c.get("/"), c.post("/api/teams/ao-grind/start"), c.post("/api/teams/ao-grind/stop")

        page, start, stop = await asyncio.to_thread(browse)
        assert page.status_code == 200, page.text
        assert "node of elsewhere: unreachable since" in page.text and "offline: this host" in page.text
        assert "the org lives on elsewhere (home)" in page.text and "a definition could not be read" not in page.text
        for r in (start, stop):
            assert r.status_code == 409 and "the org lives on elsewhere (home)" in r.json()["detail"]
    finally:
        agent.mode, agent.home = "home", agent.host


def test_the_node_banner_reads_the_host_rpc():
    from agentorc.ui.app import node_banner

    assert node_banner({"mode": "home"}) == "" and node_banner(None) == ""
    up = node_banner({"mode": "node", "home": "kmaster", "home_reachable": True, "link": {"up": True}})
    assert up.startswith("node of kmaster: linked")
    down = {"mode": "node", "home": "kmaster", "home_reachable": False, "link": {"since": "t", "why": "ssh failed"}}
    assert node_banner(down).startswith("node of kmaster: unreachable since t — ssh failed · offline")


def test_the_card_and_the_focus_git_line_read_one_measure_of_pushed(tmp_path, monkeypatch):
    """TD-080, design §4.2: *pushed* is `git.unpushed` — **only on this machine** — and every place
    that says it reads that one number. `ahead` stays and still means *ahead of the upstream*,
    which is *unmerged*: a launch branch tracking `origin/main` and pushed to its own ref is ahead
    and not unpushed, and the card must not call that stranded work. Three places, as ever: the
    card's flag, the Focus template's git line, and the `app.js` line that redraws it live."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    base = {
        "id": "ao-x-9", "name": "w", "kind": "agent", "adapter": "claude-code", "dir": str(tmp_path),
        "state": "exited", "since": "2026-09-19T16:00:00Z", "confidence": "hook", "tail": [],
        "created": "2026-09-19T15:00:00Z", "pane": False,
        "git": {"branch": "orc-1", "dirty": 0, "ahead": 308, "behind": 0, "upstream": "origin/main",
                "unpushed": 0, "pushed_against": "origin/orc-1", "files": []},
    }  # fmt: skip
    v = view(base)
    assert v["flag"] == "", "308 ahead of origin/main is unmerged, not unpushed (TD-080)"
    assert ("branch pushed", True) in v["ready"]
    html = templates.get_template("focus.html").render(
        s={**v, "grants_all": [], "ready": v["ready"]}, host="h", active="Org"
    )  # noqa: E501
    assert "308 ahead" in html and "unpushed" not in html.split('id="gitline"')[1].split("</span>")[0]

    only_here = view({**base, "git": {**base["git"], "unpushed": 2}})
    assert only_here["flag"] == "2 unpushed"
    assert ("branch pushed (vs origin/orc-1)", False) in only_here["ready"]
    html = templates.get_template("focus.html").render(
        s={**only_here, "grants_all": [], "ready": only_here["ready"]}, host="h", active="Org"
    )
    assert "2 unpushed vs origin/orc-1" in html
    # the page redraws that line itself on a delta, so it says the same thing (the chips' lesson)
    js = (pathlib.Path(templates.env.loader.searchpath[0]).parent / "static" / "app.js").read_text()
    assert "unpushed" in js and "pushed_against" in js


def test_the_card_and_focus_show_the_tools_own_title_beside_the_name(tmp_path, monkeypatch):
    """TD-074 step 3, design §4.5a **title**: the session's name as its tool holds it, shown beside
    agentorc's own name on the card and in the Focus header — always when there is one, since it is
    a name and not a status. Display only: no control sets it, and the text is escaped. The Org
    filter matches a card's text, so it matches this too."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    base = {
        "id": "ao-x-9", "name": "w", "kind": "agent", "adapter": "claude-code", "dir": str(tmp_path),
        "state": "idle", "since": "2026-09-19T16:00:00Z", "confidence": "hook", "pane": True, "tail": ["…"],
        "created": "2026-09-19T15:00:00Z",
    }  # fmt: skip
    card, focus = templates.get_template("card.html"), templates.get_template("focus.html")
    assert view(base)["title"] == ""  # no title: nothing drawn, and no empty chip
    assert "tool-title" not in card.render(s=view(base))

    titled = view({**base, "title": "Error Checker"})
    assert titled["title"] == "Error Checker"
    html = card.render(s=titled)
    assert "Error Checker" in html and "tool-title" in html
    assert 'data-act="title' not in html and 'data-act="rename' not in html  # agentorc has no rename
    # the whole of it is on hover, so the card may clip it; the Focus header carries it too
    assert "title=\"the session's name as its tool holds it" in html
    assert "Error Checker" in focus.render(s={**titled, "grants_all": [], "ready": []}, host="h", active="Org")
    # it is a model's text: escaped, never markup
    marked = card.render(s=view({**base, "title": "<b>x</b> & y"}))
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in marked
    # a malformed field costs the card its title and nothing else
    for junk in (7, ["nope"], None):
        assert view({**base, "title": junk})["title"] == ""


def test_the_filter_matches_the_tools_own_title(tmp_path, monkeypatch):
    """TD-074 step 3, design §4.5a **title**: *the filter box matches it*. The Org filter matches a
    card's own text (`applyFilter`), so the title is matched by being rendered in it — this pins
    both halves: the filter still reads the card's text, and the title is part of that text."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    js = (pathlib.Path(__file__).parents[1] / "src/agentorc/ui/static/app.js").read_text()
    assert "text: c.textContent" in js  # each text word is matched in it: test_ui_org_chrome.py
    html = templates.get_template("card.html").render(
        s=view(
            {
                "id": "ao-x-8",
                "name": "w",
                "kind": "agent",
                "adapter": "claude-code",
                "dir": str(tmp_path),
                "state": "idle",
                "since": "2026-09-19T16:00:00Z",
                "title": "Error Checker",
            }
        )  # fmt: skip
    )
    assert re.search(r">\s*Error Checker\s*<", html)  # text of the card, not an attribute alone


def test_a_role_badge_draws_its_icon_and_a_role_without_one_draws_nothing(tmp_path, monkeypatch):
    """TD-074 step 4, design §4.8 *Role presets*: the preset's icon inside the role badge — one of
    the eight the UI ships, small, monochrome and `currentColor`, with the badge's word beside it.
    It is resolved from the role's *name* at render time, through `repoconfig` (nothing in the core
    keys on a role, §9 invariant 9); a role with no icon, or a name this build does not know, draws
    nothing rather than an error."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    import asyncio

    from agentorc.repoconfig import ICONS, RESERVED_ICONS
    from agentorc.ui import app as uiapp
    from agentorc.ui.icons import ICON_PATHS, role_svg

    # every name the config accepts has a picture, and so does the one it reserves (TD-095)
    assert sorted(ICON_PATHS) == sorted([*ICONS, *RESERVED_ICONS])
    assert 'stroke="currentColor"' in role_svg("flag") and 'aria-hidden="true"' in role_svg("flag")
    assert "M5 21V4M5 4h11l-2 4 2 4H5" in role_svg("flag")  # the flag, as the design gives it
    assert 'fill="none"' in role_svg("flag")  # stroked, never filled: it is not something to press
    assert role_svg(None) == "" and role_svg("rocket") == ""  # never an error on the page

    (tmp_path / ".agentorc.yml").write_text("roles:\n  grinder: {icon: terminal}\n")

    def rec(sid, role=None, repo=None):
        r = {"id": sid, "name": sid, "kind": "agent", "adapter": "claude-code", "dir": str(tmp_path),
             "state": "idle", "since": "2026-09-19T16:00:00Z"}  # fmt: skip
        if role:
            r["role"] = role
        if repo:
            r["repo"] = repo
        return r

    records = [
        rec("ao-m", "manager"),
        rec("ao-g", "grinder", str(tmp_path)),
        rec("ao-p", "plain"),
        rec("ao-n"),
        rec("ao-l", "lead"),
    ]
    uiapp._icon_cache.clear()
    icons = asyncio.run(uiapp.role_icons(records))
    card = uiapp.templates.get_template("card.html")
    manager, grinder, plain, none, lead = (card.render(s=uiapp.view(r, records, icons=icons)) for r in records)
    # the picture, and the **label** beside it (design §4.8 *The names*, TD-076)
    assert ICON_PATHS["flag"] in manager and ">Manager</span>" in manager
    assert 'title="the role preset it was started under — manager' in manager  # the key, on hover
    # a record badged with an old word keeps its badge as text, labelled by its own name (TD-107)
    assert ">Lead</span>" in lead and ICON_PATHS["flag"] not in lead
    # the repo's own `roles:` wins, exactly as it does for every other key
    assert ICON_PATHS["terminal"] in grinder and ICON_PATHS["wrench"] not in grinder

    # `plain` carries no icon, and a session with no role carries no badge at all
    # (the mode's own `person` mark on an interactive card is not the role's: look at the badge alone)
    def role_badge(html):
        return html.split('title="the role preset')[1].split("</span>")[0]

    assert "ricon" not in role_badge(plain) and 'title="the role preset' not in none
    # a caller that resolved no icons still renders the badge's word — the default label — and nothing breaks
    bare = card.render(s=uiapp.view(records[0], records))
    assert "ricon" not in role_badge(bare) and ">Manager</span>" in bare


def test_a_permission_with_nothing_to_answer_offers_no_allow_on_the_card(tmp_path, monkeypatch):
    """Review of PR #251: the Allow / Deny route 409s without a `tool_use_id`, and the Inbox's row
    already read such a permission as something to answer in the terminal — the card now agrees,
    instead of offering two buttons that cannot work."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    s = {
        "id": "ao-x-w", "name": "w", "kind": "interactive", "adapter": "claude-code", "dir": "/x",
        "state": "needs-you", "since": "2026-09-18T10:00:00Z", "confidence": "hook", "pane": True, "tail": [],
        "pending": {"kind": "permission", "text": "Bash: git push"},
    }  # fmt: skip
    html = templates.get_template("card.html").render(s=view(s))
    # the foot's next act is Focus, where the tool's own dialog is (design §4.5 *The card's anatomy*)
    assert 'data-act="allow"' not in html and view(s)["next_act"] == "focus"
    with_id = {**s, "pending": {**s["pending"], "tool_use_id": "tu"}}
    assert 'data-act="allow"' in templates.get_template("card.html").render(s=view(with_id))


@pytest.mark.unit
def test_the_focus_header_wraps_and_the_name_is_never_what_shrinks(tmp_path, monkeypatch):
    """TD-085, design §4.5a **Focus header**: the row had no wrap, and three things were added to
    it on 2026-09-19 and -20 — the tool's title, the **out of work** chip and the **doing** line.
    Without wrap a flex row shrinks its shrinkable children rather than moving anything to a second
    line, and the session's path is the most shrinkable thing there: redrawn at 1440 it broke over
    four lines, the title was squeezed to nothing and the doing line never appeared.

    The rule is *the name and its state must never be the things that shrink*, so it is pinned
    here: the row wraps, those two do not shrink, and the two long derived strings do.

    TD-156 (2026-09-25) split the header in two — the identity line (`#fhead`) and the acts line
    (`#facts`) — and moved the grants and controllers chips to the Session card; the pieces are
    pinned where they now live."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    css = (pathlib.Path(__file__).parents[1] / "src/agentorc/ui/static/app.css").read_text()

    def rule(prefix):
        """The CSS rule beginning `prefix`, as an assertion rather than a `StopIteration` — a
        reformat or a rename should say *which* rule went, not raise from inside a generator."""
        found = [ln for ln in css.splitlines() if ln.startswith(prefix)]
        assert len(found) == 1, f"expected exactly one rule starting {prefix!r}, found {len(found)}"
        return found[0]

    assert "flex-wrap: wrap" in rule(".focus #fhead {")
    keep = rule(".focus #fhead > .title,")
    assert "#fstate" in keep and "flex: 0 0 auto" in keep  # the name and the state: never shrunk
    give = rule(".focus #fhead > .tool-title {")
    assert "flex: 0 1 auto" in give and "text-overflow: ellipsis" in give  # this gives way instead

    # …and every one of the things that crowded it is still in the row it now wraps
    v_src = {
        "id": "ao-x-9", "name": "w", "kind": "agent", "adapter": "claude-code", "dir": str(tmp_path),
        "state": "idle", "since": "2026-09-19T16:00:00Z", "confidence": "hook", "pane": True, "tail": ["…"],
        "created": "2026-09-19T15:00:00Z", "title": "Error Checker", "unattended": True,
        "doing": {"text": "rebasing #269", "at": "2026-09-19T15:50:00Z"},
        "out_of_work": {"why": "the lane is done", "at": "2026-09-19T15:55:00Z"},
        "team": "ao-grind", "role": "grinder",
    }  # fmt: skip
    v = view(v_src)
    html = templates.get_template("focus.html").render(s={**v, "grants_all": [], "ready": []}, host="h", active="Org")
    head_html = html[html.index('id="fhead"') : html.index('id="facts"')]
    for piece in ("Error Checker", "out of work", "fstop"):
        assert piece in head_html, piece
    assert str(tmp_path) not in head_html  # the name alone: host, repo and directory are the Session card's
    working_html = html[html.index('data-side="working"') : html.index('data-side="git"')]
    assert "rebasing #269" in working_html and "says · " in working_html  # the doing line is the Working card
    acts_html = html[html.index('id="facts"') : html.index('id="fbanner"')]
    for piece in ("fclose", "fmodeact", "wrapup", "shell-here", 'class="more"', "tcopy", "tpaste", '"kill"'):
        assert piece in acts_html, piece  # the acts line: the next act, the plain ones, more ▾ with Kill last
    assert acts_html.rindex('"kill"') > acts_html.rindex("tpaste")
    session_html = html[html.index('data-side="session"') :]
    for piece in ("fgrants", "fcontrollers", "fstopset"):
        assert piece in session_html, piece  # read at a session's start, rarely pressed after: the folded card
    # Session folded; Ready to close folded too on this unattended team member, and Close session
    # not offered — its team closes it (§4.5 *Whose session it is*); an own session has both open
    assert 'data-side="session">' in html and 'data-side="ready">' in html and 'id="fclose"' in html
    assert "hidden" in html[html.index('id="fready"') - 80 : html.index('id="fready"')] and not v["own"]
    own = view({**v_src, "unattended": False})
    html = templates.get_template("focus.html").render(
        s={**own, "grants_all": [], "ready": [], "ready_ok": True}, host="h", active="Org"
    )
    assert own["own"] and 'data-side="ready" open>' in html
    # the Working card's heading reads *says · <age> ago*, the words alone in its body (TD-156) —
    # the age is measured against now, so the shape is what is pinned, not the number
    assert ">says · " in working_html and " ago</span>" in working_html and "· says ·" not in working_html


def test_the_focus_reports_panel_shows_a_reference_once():
    """§4.5a **report line** (TD-095): an entry whose reference is its PR reads `#359`, never
    `#359 → #359` — on the Focus Reports panel as on the card. The panel is drawn inside `AO.focus`'s
    closure, which the node probe cannot reach, so the rule is pinned where it is written."""
    js = (pathlib.Path(__file__).parents[1] / "src/agentorc/ui/static/app.js").read_text()
    assert '(p.pr && String(p.ref) !== `#${p.pr}` ? ` <span class="st">→ ${prLink(p.pr)}</span>` : "")' in js


def test_a_team_winding_down_keeps_its_cards_compact_and_draws_no_empty_summary(client, tmp_path):
    """§4.5a *card: compact*, *team card: summary* (TD-176 slice 3; TD-181, built by TD-192): a
    member of a team is a compact card, live or not, so a team whose last live session goes changes
    no card's shape — the exit's own delta is compact. A team with nothing live draws only the facets
    that hold something (TD-418): this one has no repo, no claim and no doing row, so its group carries
    no summary."""
    from sessionorc.client import call_sync

    made = [
        call_sync("create", name=n, dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"], team="wind")["id"]
        for n in ("w1", "w2")
    ]
    call_sync("kill", id=made[1])
    wait_state(client, made[1], "exited")
    assert " compact" in client.get("/").text.split(f'id="card-{made[1]}"')[0].rsplit("<div", 1)[-1]
    with client.websocket_connect("/events") as ws:
        call_sync("kill", id=made[0])
        for _ in range(80):
            ev = json.loads(ws.receive_text())
            if ev.get("event") == "session" and ev["id"] == made[0] and ev["state"] == "exited":
                break
        assert " compact" in ev["html"].split(">", 1)[0]
        (g,) = [g for g in ev["groups"] if g["team"] == "wind"]
        assert g["live"] == 0 and g["summary"] == ""
    page = client.get("/").text
    for sid in made:
        assert " compact" in page.split(f'id="card-{sid}"')[0].rsplit("<div", 1)[-1]
        client.post(f"/api/sessions/{sid}/remove")


def test_a_roles_message_line_is_on_the_view_the_titles_and_the_team_header(tmp_path, monkeypatch):
    """design §4.8 *A role says when to message it*, §4.5a **Message** and *team groups* **who for
    what** (TD-162, built by TD-171): the view carries the role's `message:` line, resolved as the
    label is; the Message controls carry it as their `title`'s head and as `data-line` for the
    composer; the team header's line names, in the definition's order, the session holding each
    role — *(on call)* for an empty seat — and a role several sessions hold by its label."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    import asyncio

    from agentorc.ui import app as uiapp

    (tmp_path / ".agentorc.yml").write_text("roles:\n  grinder: {message: its own card}\n")
    base = {"kind": "agent", "adapter": "claude-code", "dir": str(tmp_path), "repo": str(tmp_path),
            "state": "idle", "since": "2026-09-20T16:00:00Z", "team": "ao-grind"}  # fmt: skip
    records = [
        {**base, "id": "ao-m", "name": "manager-ao-1", "role": "manager", "capabilities": ["control"]},
        {**base, "id": "ao-g1", "name": "grinder-ao-1", "role": "grinder", "controllers": ["ao-m"]},
        {**base, "id": "ao-g2", "name": "grinder-ao-2", "role": "grinder", "controllers": ["ao-m"]},
        {**base, "id": "ao-p", "name": "mine", "role": "plain", "team": None},
    ]
    uiapp._icon_cache.clear()
    icons = asyncio.run(uiapp.role_icons(records))
    views = [uiapp.view(r, records, icons=icons) for r in records]
    assert views[0]["message_line"].startswith("the team's work") and views[1]["message_line"] == "its own card"
    assert views[3]["message_line"] == ""  # plain: none
    card = uiapp.templates.get_template("card.html").render(s=views[1])
    assert 'data-line="its own card" title="for its own card&#10;Mails a question' in card
    assert 'data-line="" title="Mails a question' in uiapp.templates.get_template("card.html").render(s=views[3])
    roles = [
        {"role": "manager", "names": ["manager-ao-1"], "seat": False},
        {"role": "techlead", "names": ["techlead-ao-1"], "seat": True},
        {"role": "grinder", "names": ["grinder-ao-1", "grinder-ao-2"], "seat": False},
        {"role": "plain", "names": ["x"], "seat": False},
    ]
    who = uiapp.who_for_what(roles, views)
    assert who[0] == f"{views[0]['message_line']} → manager-ao-1"
    assert who[1].endswith("— an ask fills the seat → techlead-ao-1 (on call)")  # nobody holds the seat
    assert who[2] == "Grinder: its own card" and len(who) == 3  # plain carries no line
    row = {"name": "ao-grind", "roles": roles, "live": 3, "source": None, "projects": [], "manager": "manager-ao-1",
           "techlead": "techlead-ao-1", "seats": [], "members": 2, "wound_down": None, "concluded": None}  # fmt: skip
    (g,) = [g for g in uiapp.team_groups(views, [row]) if g["team"] == "ao-grind"]
    head = uiapp.templates.get_template("group_head.html").render(g=g)
    # TD-252: the header draws no line; the team's help panel carries the lines under their paragraph
    assert '<div class="meta whofor"' not in head and "manager-ao-1 · " not in head
    panel = head[head.index('<div class="note secinfo helppanel"') :]
    assert "<p><b>who for what</b> — Lists whom to write to for what on a team" in panel
    assert f'<ul class="whofor"><li>{views[0]["message_line"]} → manager-ao-1</li>' in panel.replace("&#39;", "'")
    assert "<li>Grinder: its own card</li></ul>" in panel
    g["who"] = []  # a team whose roles carry no line: neither the paragraph nor the list
    bare = uiapp.templates.get_template("group_head.html").render(g=g)
    assert "<b>who for what</b>" not in bare and "whofor" not in bare and "<b>Start</b>" in bare
    assert uiapp.who_for_what([{"role": "plain", "names": ["a"], "seat": False}], views) == []  # no line at all


def test_the_definition_names_its_roles_in_order_with_their_holders():
    """`teamrun.role_holders` (TD-171): the manager, the techlead seat, the seats, then the members,
    each role once with the names holding it; a person leading the team is not a role."""
    from agentorc import org as orgmod
    from agentorc import teamrun

    t = orgmod.TeamDef(
        name="ao-grind",
        manager=orgmod.ManagerDef(role="manager", name="manager-ao-1"),
        techlead=orgmod.TechleadDef(name="techlead-ao-1"),
        members=[orgmod.MemberDef(role="grinder", count=2, name="grinder-ao")],
    )
    got = teamrun.role_holders(t)
    assert [r["role"] for r in got] == ["manager", "techlead", "grinder"]
    assert (
        got[1]["seat"]
        and not got[2]["seat"]
        and got[2]["names"] == orgmod.MemberDef(role="grinder", count=2, name="grinder-ao").names()
    )


def test_a_roles_saved_prompts_are_chips_on_focus_and_new_session(tmp_path, monkeypatch):
    """design §4.5a *Focus composer* and *New session* **prompt chips** (§4.8, TD-161, built by
    TD-170): the role's `prompts:` in the file's order, each a chip whose `title` and `data-text` are
    its text — the definition's words, escaped; none for a record whose role has none; `/api/roles`
    and New session's options carry them for the Role pick."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    from agentorc.ui import app as uiapp

    (tmp_path / ".agentorc.yml").write_text(
        "roles:\n  plain:\n    prompts:\n      - {label: review PR, text: 'Review PR <9> now.'}\n"
        "      - {label: sweep, text: /stranded-work}\n"
    )
    rec = {"id": "ao-p", "name": "mine", "kind": "agent", "adapter": "claude-code", "dir": str(tmp_path),
           "repo": str(tmp_path), "state": "idle", "role": "plain", "unattended": False, "pane": True, "tail": [],
           "since": "2026-09-19T16:00:00Z", "created": "2026-09-19T15:00:00Z", "confidence": "hook"}  # fmt: skip
    prompts = uiapp.role_prompts(rec)
    assert [p["label"] for p in prompts] == ["review PR", "sweep"]
    assert uiapp.role_prompts({**rec, "role": "grinder"}) == [] and uiapp.role_prompts({**rec, "role": ""}) == []
    v = uiapp.view(rec)
    html = uiapp.templates.get_template("focus.html").render(
        s={**v, "grants_all": [], "ready": []}, host="h", active="Org", prompts=prompts
    )
    chips = html[html.index('id="promptchips"') : html.index('id="send"')]
    assert chips.index(">review PR<") < chips.index(">sweep<")  # the file's order
    assert 'data-text="Review PR &lt;9&gt; now." title="Review PR &lt;9&gt; now."' in chips
    bare = uiapp.templates.get_template("focus.html").render(
        s={**v, "grants_all": [], "ready": []}, host="h", active="Org"
    )
    assert 'id="promptchips"' not in bare  # a role without prompts, or an unattended record: no chips
    roles = {r.name: r for r in uiapp.repoconfig.roles(uiapp.repoconfig.load(tmp_path))}
    assert roles["plain"].to_dict()["prompts"][1]["text"] == "/stranded-work"
    new = (uiapp.HERE / "templates" / "new.html").read_text()  # the Role pick's options carry the list
    assert 'id="newchips"' in new and "data-prompts='{{ (r.prompts or []) | tojson }}'" in new


def test_the_page_reads_person_through_the_agents_settings_read(client, tmp_path):
    """design §5 (TD-146): the editor button comes from `person.open_in` in the home's settings.yml,
    read through the agent's `settings` read before a page is drawn — never from the file, and never
    from a retired ui.yml, which the Org names as *migrate*."""
    from agentorc.ui import uiconf
    from sessionorc import paths
    from sessionorc.client import call_sync

    try:
        call_sync("set_settings", person={"open_in": "none"})
        (paths.home() / "ui.yml").write_text("open_in: vscode\n")
        r = client.get("/")
        assert r.status_code == 200 and uiconf.open_in().kind == "none"
        assert 'id="migratenote"' in r.text
    finally:  # the module's agent outlives this test: what it wrote there is taken back (TD-275)
        call_sync("set_settings", person={"open_in": None})
        (paths.home() / "ui.yml").unlink(missing_ok=True)
        uiconf.set_read({"person": {}, "migrate": []})


def test_copy_on_select_is_the_persons_and_on_by_default(client, tmp_path):
    """design §4.5a *Focus: copy on select*, §5 `person.terminal.copy_on_select` (TD-164, built by
    TD-174): the toggle in Focus's *more ▾* is drawn checked until the person turns it off; a press
    writes `settings.yml` through `set_settings`, the next page draws it off, and only a boolean is
    taken."""
    from agentorc.ui import uiconf
    from sessionorc.client import call_sync

    uiconf.set_read({"person": {}, "migrate": []})
    assert uiconf.copy_on_select() is True  # nothing written: on
    uiconf.set_read({"person": {"terminal": {"copy_on_select": "yes"}}, "migrate": []})
    assert uiconf.copy_on_select() is True  # not a boolean: the default
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "cos"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    wait_state(client, sid, "idle")
    page = client.get(f"/focus/{sid}").text
    assert re.search(r'<input type="checkbox" id="tcopysel" checked>', page) and "Copy on select" in page
    assert "Shift+click to grow or shrink it" in page  # Copy's tooltip (§4.5a *Focus: Copy / Paste*)
    for bad in ({"terminal": {"copy_on_select": "no"}}, {"terminal": {"size": 14}}, {}):
        assert client.post("/api/settings/person", json=bad).status_code == 400
    assert client.post("/api/settings/person", json={"terminal": {"copy_on_select": False}}).json()["ok"]
    assert call_sync("settings")["person"]["terminal"]["copy_on_select"] is False
    assert re.search(r'<input type="checkbox" id="tcopysel">', client.get(f"/focus/{sid}").text)
    assert client.post("/api/settings/person", json={"terminal": {"copy_on_select": True}}).json()["ok"]
    assert 'id="tcopysel" checked' in client.get(f"/focus/{sid}").text
    client.post(f"/api/sessions/{sid}/kill")
    call_sync("set_settings", person={"terminal": {"copy_on_select": None}})  # TD-275


def test_a_copy_on_release_says_so_with_copys_own_toast():
    """TD-273: a drag-and-release in the Focus terminal copied and said nothing, and a refused copy
    was silent too. It goes through Copy's own path, which toasts *copied* or *clipboard blocked*."""
    js = (pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui" / "static" / "app.js").read_text()
    release = js.split('document.addEventListener("mouseup", () => {')[1].split("});")[0]
    assert "copySel()" in release and "writeText" not in release
    copy = js.split("const copySel = () => {")[1].split("};")[0]
    assert 'AO.toast("copied", true)' in copy and 'AO.toast("clipboard blocked (needs https or localhost)")' in copy


def test_restart_from_the_page_is_the_rpc_and_its_refusal_is_the_toast(client, subprocess_agent, tmp_path):
    """§4.5a **Restart** (§6 rule 2 *A person's restart*, TD-250 slice 2): the page's press is the
    `restart` RPC, a person's own, and what the host agent refuses comes back in its words."""
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "plain"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    wait_state(client, sid, "idle")
    got = client.post(f"/api/sessions/{sid}/restart")
    assert got.status_code == 400 and "no launch record" in got.json()["detail"]  # nothing supervises a shell
    assert next(x for x in client.get("/api/sessions").json() if x["id"] == sid)["state"] == "idle"  # untouched


def test_the_new_session_form_asks_in_the_order_a_person_starts_a_session(client, tmp_path):
    """§4.5a New session **the reworked form** (TD-277; TD-284 slice 1): Name and Host first, then
    Role and Profile, Team and Project, the directory, Where, Lane, At and Until, the opening prompt,
    Controllers, and Grants and the Resume id under a folded **More ▸** that opens when a Resume id
    is set; the fixed *One agent session per directory* block is gone, an occupied checkout being the
    Where choice's pill."""
    page = client.get("/new").text
    labels = ["<label>Name</label>", "<label>Host</label>", "<label>Role</label>", "<label>Profile</label>",
              "<label>Team</label>", "<label>Project</label>", "<label>Repo</label>", "<label>Where</label>",
              "<label>Lane</label>", "<label>At (optional)</label>", "<label>Until (optional)</label>",
              "<label>Opening prompt (optional)</label>", "<label>Controllers</label>", "<summary>More ▸",
              "<label>Grants</label>", "<label>Resume (optional)</label>"]  # fmt: skip
    at = [page.index(x) for x in labels]
    assert at == sorted(at)
    assert "One agent session per directory." not in page
    assert '<details class="fold" id="morefields">' in page and 'id="hereinuse" hidden' in page
    assert '<details class="fold" id="morefields" open>' in client.get("/new?resume=abc-123").text


def test_profile_is_the_one_tool_pick(client, tmp_path):
    """§4.5a New session **the reworked form** (TD-284 slice 2): no Adapter field — every profile
    names its adapter and the two could disagree — the Profile list ending with *shell (no agent)*,
    and each of Host, Project and Profile naming its file; a shell picked there starts a shell."""
    from agentorc.ui.app import SHELL_PICK

    page = client.get("/new").text
    assert "<label>Adapter</label>" not in page and 'name="adapter"' not in page
    pick = page[page.index('<select class="input" name="profile"') :]
    pick = pick[: pick.index("</select>")]
    assert pick.rstrip().endswith(f'<option value="{SHELL_PICK}" data-adapter="shell">shell (no agent)</option>')
    assert 'id="rolefield"' in page and 'id="lanefield"' in page  # what picking the shell hides
    for name in ("hosts.yml", "profiles.yml", "org.yml"):
        assert f'from <span class="mono">{name}</span>' in page
    # a shell's Resume with changes… lands with the shell picked, and nothing else
    again = client.get("/new?adapter=shell").text
    assert f'<option value="{SHELL_PICK}" data-adapter="shell" selected>' in again
    assert '<option value="" selected>' not in again
    r = client.post("/new", data={"name": "sh", "dir": str(tmp_path), "profile": SHELL_PICK}, follow_redirects=False)
    assert r.status_code == 303, r.text
    sid = r.headers["location"].rsplit("/", 1)[-1]
    rec = next(x for x in client.get("/api/sessions").json() if x["id"] == sid)
    assert rec["adapter"] == "shell" and not rec.get("profile")


def test_the_adapter_a_profile_pick_starts(tmp_path, monkeypatch):
    """The form sends no adapter (TD-284 slice 2): the picked profile's, else the default's."""
    from agentorc.ui.app import profile_adapter

    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    assert profile_adapter("") == "claude-code"  # no profiles.yml: the default profile, Claude Code's
    (tmp_path / "profiles.yml").write_text(
        "default: paul\nprofiles:\n  paul: {adapter: claude-code, account: paul}\n"
        "  other: {adapter: codex, account: o}\n"
    )
    assert profile_adapter("other") == "codex"
    assert profile_adapter("") == "claude-code" and profile_adapter("paul") == "claude-code"
    assert profile_adapter("gone") == "claude-code"  # an unknown name is the create's to refuse


def test_a_tool_with_no_profile_lands_on_a_profile_of_that_tool():
    """A Resume with changes… of a record that names a tool and no profile (review of #948)."""
    from agentorc.profiles import Profile
    from agentorc.ui.app import tool_profile

    profs = {"paul": Profile(name="paul"), "o": Profile(name="o", adapter="codex")}
    assert tool_profile(profs, "paul", "codex") == "o"
    assert tool_profile(profs, "paul", "claude-code") == ""  # the default's tool: the default pick
    assert tool_profile(profs, "paul", "shell") == "" and tool_profile(profs, "paul", "") == ""
    assert tool_profile(profs, "paul", "nothing-has-it") == ""


def test_the_repo_pick_and_another_directory(client, tmp_path):
    """§4.5a New session **the reworked form** (TD-284 slice 4): Repo lists the registered checkouts
    on this host and ends with *another directory…*, whose typed path is checked as it is typed; a
    prefilled directory that is no registered checkout lands on *another directory…* with it typed."""
    from agentorc.ui.app import form_repos, repo_of

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    picks = form_repos([str(a), str(tmp_path / "gone"), str(b)])
    assert picks == [{"name": "a", "path": str(a)}, {"name": "b", "path": str(b)}]  # a gone checkout is left out
    assert repo_of(picks, "") == str(a)  # a blank form starts on the first
    assert repo_of(picks, str(b) + "/") == str(b) and repo_of(picks, str(tmp_path)) == ""
    page = client.get(f"/new?dir={tmp_path}").text
    assert '<option value="" data-other="1" selected>another directory…</option>' in page
    assert f'name="dir" list="recent" value="{tmp_path}"' in page and 'id="dirfield" style="gap: 5px;">' in page
    assert client.get("/api/dir_check", params={"dir": str(a)}).json()["exists"] is True
    gone = client.get("/api/dir_check", params={"dir": str(tmp_path / "nope")}).json()
    assert gone["exists"] is False and gone["why"].startswith("no such directory on ")
    # **Where** offers a worktree for a git repo only (TD-296 #3): `git` says whether the typed
    # directory is in a checkout, and the form hides the worktree choice where it is not
    assert client.get("/api/dir_check", params={"dir": str(a)}).json()["git"] is False
    subprocess.run(["git", "init", "-q", str(b)], check=True)
    assert client.get("/api/dir_check", params={"dir": str(b)}).json()["git"] is True
    assert gone["git"] is None
    ui = pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui"
    js, css = (ui / "static" / "app.js").read_text(), (ui / "static" / "app.css").read_text()
    assert "offerWorktree(o.git !== false);" in js and ".radio[hidden] { display: none; }" in css
    # the field's heading is upper-case, never the labels of its choices (Where, Controllers)
    assert ".field > label { font-size: var(--t-cap)" in css and ".field label {" not in css


def test_where_is_worktree_first_with_the_free_worktrees(client, tmp_path):
    """§4.5a New session **the reworked form** (TD-284 slice 4): Where offers a new worktree named as
    the session first, the checkout itself second; `/api/worktrees` lists the repo's worktrees under
    `.claude/worktrees/` that nobody is in, for the chips whose press takes its name."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    for n in ("spike", "td-290"):
        subprocess.run([*git, "worktree", "add", "-q", str(repo / ".claude" / "worktrees" / n)], check=True)
    (repo / ".claude" / "worktrees" / "not-a-worktree").mkdir()
    got = client.get("/api/worktrees", params={"repo": str(repo)}).json()["worktrees"]
    assert [w["name"] for w in got] == ["spike", "td-290"]
    assert client.get("/api/worktrees", params={"repo": str(tmp_path / "plain")}).json()["worktrees"] == []
    page = client.get(f"/new?dir={repo}").text
    where = page[page.index("<label>Where</label>") :]
    assert where.index('value="worktree"') < where.index('value="here"')  # worktree first
    assert 'id="wtchips" hidden' in where and 'type="hidden" name="worktree"' in where
    assert "The checkout itself" in where
    # a Resume with changes… keeps the Where it was filled in with, and its worktree's own name
    again = client.get(f"/new?dir={repo}&where=worktree&worktree=wt-1&name=w&prefilled=1").text
    assert 'data-prefilled="1"' in again and '<code id="wtname">wt-1</code>' in again
    assert 'name="where" value="worktree" checked' in again


def test_the_worktree_line_slugs_a_name_as_the_server_does():
    """The Where line shows the worktree a Name makes, which is `naming.slug` of it (review of #951):
    the page's `slugOf` is that function in JavaScript, run here under node beside the Python one."""
    import shutil

    from sessionorc import naming

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    js = (pathlib.Path(__file__).resolve().parent.parent / "src/agentorc/ui/static/app.js").read_text()
    line = next(x for x in js.splitlines() if "const slugOf = " in x).strip()
    names = ["TD-302", "My Feature", "  --x--  ", "Spike_2.feat", "a" * 40 + "-b", "!!!", "td-290"]
    out = subprocess.run(
        [node, "-e", f"{line}\nconsole.log(JSON.stringify({json.dumps(names)}.map(slugOf)))"],
        capture_output=True, text=True, timeout=30,
    )  # fmt: skip
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [naming.slug(n) for n in names]


def test_the_role_pick_begins_with_interactive_and_retires_the_switch(client):
    """§4.5a New session **the reworked form** (TD-284 slice 5): the Role pick's first choice is
    *Interactive*, each role reads *<role> · unattended*, the Unattended switch is gone — the posted
    `unattended` is the pick's, kept in a hidden field — and At and Until are drawn for an
    unattended pick only."""
    page = client.get("/new").text
    pick = page[page.index('<select class="input" name="role"') :]
    pick = pick[: pick.index("</select>")]
    assert pick.index('data-interactive="1" selected>Interactive</option>') < pick.index("plain · unattended")
    assert 'type="checkbox" name="unattended"' not in page and '<input type="hidden" name="unattended"' in page
    assert 'id="whenfields" hidden' in page and 'id="rolemode"' in page
    # a role's Resume with changes… lands unattended, its At and Until drawn
    again = client.get("/new?role=grinder&unattended=on&prefilled=1").text
    assert 'name="unattended" value="on" data-under=""' in again and 'id="whenfields" hidden' not in again
    # …and one that ran under the person lands *under you*
    mine = client.get("/new?role=grinder&prefilled=1").text
    assert 'name="unattended" value="" data-under="1"' in mine
    # an unattended record with no role comes back as *plain · unattended*, not as Interactive (review of #952)
    bare = client.get("/new?unattended=on&prefilled=1").text
    assert '<option value="plain"' in bare and bare.split('<option value="plain"', 1)[1].startswith(" selected")
