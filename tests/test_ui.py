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


def test_pages_and_shell_flow(client, tmp_path):
    r = client.get("/")
    assert r.status_code == 200 and "Org" in r.text and "No sessions" in r.text
    assert 'id="usagechip"' in r.text  # the per-profile usage figure (TD-001), empty until a poll lands
    r = client.get("/new")
    assert r.status_code == 200 and "claude-code" in r.text and "shell" in r.text
    # the Role pick-list (design §4.5a): the built-ins, plus what the directory's repo defines
    assert 'name="role"' in r.text and "lead [built-in] · grants control" in r.text
    (tmp_path / ".agentorc.yml").write_text("controllers: [orc]\nroles: {reviewer: {lane: [ui]}}\n")
    r = client.get(f"/new?dir={tmp_path}")
    assert "reviewer [repo]" in r.text and 'data-default="orc"' in r.text
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
        # both of these were read once, which races the attach (TD-033): the option is set by the
        # attach and the pane leaves whatever mode it started in, so wait for each
        assert wait_for(
            lambda: tmux.run("show-options", "-t", f"={sid}:", "mouse", check=False).stdout.strip() == "mouse on"
        ), "the attach never set `mouse on`"
        assert wait_for(lambda: mode() == "0"), "the pane never settled on the live screen"
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
    assert bad.value.status_code == 400 and "Unattended" in bad.value.detail
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
    assert "code 4090" in html  # the tail is still there under it


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
            ws.receive_bytes()  # tmux's own error, then the close
    assert e.value.code == 4404


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
    assert ">ao-vanished<" in html and "chip scraped" in html and "re-attach or remove it" in html

    # the live pages
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "mw"}, follow_redirects=False)
    worker = r.headers["location"].rsplit("/", 1)[-1]
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "morc"}, follow_redirects=False)
    orc_id = r.headers["location"].rsplit("/", 1)[-1]

    page = client.get(f"/focus/{worker}").text
    assert 'id="fcontrollers"' in page and "no controller — nobody may act on this session" in page
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
            "role": "lead",
            "grant": "control",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    sid = r.headers["location"].rsplit("/", 1)[-1]
    got = next(x for x in client.get("/api/sessions").json() if x["id"] == sid)
    assert "control" in (got.get("capabilities") or [])

    # unticked on a `lead` preset: the person's decision stands over the preset's grants.
    # A second directory, since one agent session per directory is refused (§9 invariant 2).
    other = tmp_path / "other"
    other.mkdir()
    r = client.post(
        "/new",
        data={"name": "g2", "dir": str(other), "adapter": "hookstub", "role": "lead"},
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
    assert 'v.state === "exited" && v.adapter_id' in dead and "start a new session here" in dead

    # `stalled?` is a working session that stopped producing output (§4.2) — a turn in flight, so
    # it steers. `limited` must not claim a turn starts now: §4.2 says nothing the person does
    # unblocks a cap, and §4.5a's controls for it are Switch profile and Wait.
    assert 'v.state === "working" || v.state === "stalled?"' in js
    assert "the profile is at its cap: what you send waits" in js
    assert "this session looks stalled" in js

    # and the design says it where §4.5a points: the Send row and the §4.3 rule
    design = (pathlib.Path(__file__).parents[1] / "docs" / "design.md").read_text()
    send_row = next(ln for ln in design.split("\n") if ln.startswith("| Focus composer | **Send** |"))
    assert "Steer" in send_row and "starts a new turn" in send_row
    assert "to an `idle` session it starts a turn; to a `working` one it steers the turn in flight" in design


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
    """TD-052 step 8, design §4.5a Org top bar **person inbox** (§4.10): the Org page renders the
    unread count from the `inbox` RPC; the panel's feed lists an entry a session sent with
    `ao msg person`, with its sender's id and name, as a person's read (no `read_at`); **Reply**
    lands a `reply` from the person in the sender's inbox and closes its `ask`; delete removes the
    entry from the person inbox and leaves the sender's copy."""
    import asyncio

    from sessionorc.client import LocalClient

    r = client.post("/shell", data={"dir": str(tmp_path), "name": "asker"}, follow_redirects=False)
    sender = r.headers["location"].rsplit("/", 1)[-1]
    assert 'class="badge unread hidden" id="personunread"></span>' in client.get("/").text  # nothing at zero

    async def as_sender(**kw):
        async with LocalClient(caller=sender) as c:
            return await c.call("msg", to="person", **kw)

    ask = asyncio.run(as_sender(text="merge PR 9?", kind="ask", about="TD-052"))["entry"]["id"]
    assert 'class="badge unread" id="personunread">1</span>' in client.get("/").text

    got = client.get("/api/person/inbox").json()
    [e] = got["entries"]
    assert got["unread"] == 1 and (e["id"], e["from"], e["from_name"], e["kind"], e["about"]) == (
        ask, sender, "asker", "ask", "TD-052"
    )  # fmt: skip
    assert e["read_at"] is None and e["bound"] and e["closed_by"] is None
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
    assert client.post("/api/person/unmail", json={"msg": ask}).json() == {"ok": True, "unread": 0}
    assert client.get("/api/person/inbox").json()["entries"] == []
    assert client.post("/api/person/unmail", json={"msg": ask}).status_code == 400  # already gone
    assert client.post("/api/person/nope", json={}).status_code == 404


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
    assert "out of work 2h" in html and why in html  # the words, and the reason on hover
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
    assert "out of work" not in client.get(f"/focus/{sid}").text

    why = "nothing open that this brief does not exclude"
    call_sync("progress", id=sid, status="none", why=why, caller=sid)
    page = client.get(f"/focus/{sid}").text
    assert "out of work" in page and why in page
    assert "out of work" in client.get("/").text  # and the card on the Org page

    # a claim means it has work again — the record clears the declaration, and so does the header
    call_sync("progress", id=sid, ref="TD-053", status="claimed", caller=sid)
    assert "out of work" not in client.get(f"/focus/{sid}").text
    call_sync("kill", id=sid)


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
    assert "permission: Bash: git push — host unreachable" in html and "answer it at laptop" in html
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
