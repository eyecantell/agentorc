"""The Focus terminal's mark (design §4.6 *Reconnect contract*, *Attach behaviour with another client
present*; §4.5a **terminal mark**; TD-474, TD-480): a frozen or jittery terminal has three causes the
person could not tell apart — the socket reconnecting, another tmux client sizing the pane, a working
session whose pane draws nothing — and the page now names the one that holds."""

from __future__ import annotations

import asyncio
import json
import pathlib
import shutil
import subprocess
import uuid

import pytest

from agentorc.ui.pty_bridge import clients_argv, clients_frame, watch_clients

UI = pathlib.Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui"
APP = UI / "static" / "app.js"

PROBE = """
const fs = require("fs");
const noop = () => {};
const el = () => ({ dataset: {}, style: {}, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [], contains: () => false });
const document = { documentElement: el(), body: el(), querySelector: () => null, querySelectorAll: () => [],
  addEventListener: noop, createElement: () => el() };
global.window = {}; global.document = document;
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO;
const base = { retryAt: null, clients: 1, window: [120, 32], grid: [120, 32], open: true, state: "working",
  lastByte: 0 };
const mark = (o, now) => { const m = AO.termMark({ ...base, ...o }, now); return m && m.text; };
console.log(JSON.stringify({
  quiet: mark({}, 1000),
  retry_in_grace: mark({ retryAt: 0, open: false }, 2999),
  retry_past_grace: mark({ retryAt: 0, open: false }, 3000),
  retry_cleared: mark({ retryAt: null, open: false }, 60000),
  resized: mark({ clients: 2, window: [80, 24] }, 1000),
  resized_alone: mark({ clients: 1, window: [80, 24] }, 1000),
  resized_agrees: mark({ clients: 2, window: [120, 32] }, 1000),
  resized_rows: mark({ clients: 2, window: [120, 31] }, 1000),
  silent_before: mark({}, 29999),
  silent: mark({}, 30000),
  silent_counts: mark({}, 45900),
  silent_idle: mark({ state: "idle" }, 90000),
  silent_closed: mark({ open: false }, 90000),
  silent_no_byte: mark({ lastByte: null }, 90000),
  precedence_retry: mark({ retryAt: 0, clients: 2, window: [80, 24] }, 40000),
  precedence_resize: mark({ clients: 2, window: [80, 24] }, 40000),
  titles: ["reconnecting", "resized", "silent"].map((k) => {
    const o = { reconnecting: { retryAt: 0 }, resized: { clients: 2, window: [1, 1] }, silent: {} }[k];
    const m = AO.termMark({ ...base, ...o }, 40000); return [m.kind, m.title];
  }),
  grace: [AO.TERM_GRACE, AO.TERM_SILENT],
}));
"""


@pytest.mark.unit
def test_the_mark_is_one_of_three_in_order_each_after_its_grace(tmp_path):
    """Under node: *reconnecting…* only three seconds after a retry was scheduled and until a pane byte
    clears it; *resized by another client* while more than one client is attached and the window is
    not this grid; *no output for Ns* after thirty seconds of silence on an open socket of a working
    session, counting up — never on idle, closed, or before any byte. One mark, in that order; each
    hover in §4.5a's words."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    probe = tmp_path / "mark_probe.js"
    probe.write_text(PROBE)
    run = subprocess.run([node, str(probe), str(APP)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    got = json.loads(run.stdout)
    assert got.pop("titles") == [
        ["reconnecting", "the terminal socket dropped and retries on its own — reload if it stays"],
        ["resized", "another tmux client (a VS Code attach, a second tab) is sizing this pane"
                    " — use one, or detach the other"],
        ["silent", "the session reads working and the pane has drawn nothing"
                   " — Transcript or `ao tail` say whether it is thinking or stuck"],
    ]  # fmt: skip
    assert got == {
        "quiet": None, "retry_in_grace": None, "retry_past_grace": "reconnecting…", "retry_cleared": None,
        "resized": "resized by another client", "resized_alone": None, "resized_agrees": None,
        "resized_rows": "resized by another client",
        "silent_before": None, "silent": "no output for 30s", "silent_counts": "no output for 45s",
        "silent_idle": None, "silent_closed": None, "silent_no_byte": None,
        "precedence_retry": "reconnecting…", "precedence_resize": "resized by another client",
        "grace": [3000, 30000],
    }  # fmt: skip


@pytest.mark.unit
def test_the_focus_wires_the_mark_to_the_socket_and_the_feed():
    """The wiring around `AO.termMark`: the bridge's `{clients, window}` frame is read beside the
    `read_only` one, before the backoff reset (the attach's own word, not pane output); a pane byte
    clears a pending reconnect and restarts the silence; a retry stamps `retryAt` once; the feed's
    state reaches the mark; each event goes to the console as `[agentorc] terminal …`; the page
    carries the slot, hidden."""
    js = APP.read_text()
    body = js[js.index("ws.onmessage = (m) => {\n") :]
    body = body[: body.index("\n      };\n")]
    assert body.index('if (c && "clients" in c)') < body.index("delay = 500;")
    assert body.index("mk.lastByte = t;") < body.index("delay = 500;")
    assert "if (mk.retryAt != null) { mk.retryAt = null; drawMark(); }" in body
    assert "if (mk.retryAt == null) mk.retryAt = Date.now();" in js
    assert "mk.state = ev.session.state; drawMark();" in js
    assert 'console.info("[agentorc] terminal", ...a, new Date().toISOString())' in js
    for event in ('tlog("open")', 'tlog("closed", e.code, e.reason || "")', 'tlog("clients"', "tlog(`output after"):
        assert event in js, event
    html = (UI / "templates" / "focus.html").read_text()
    assert '<span class="badge termmark hidden" id="ftermmark"></span>' in html


@pytest.mark.unit
def test_the_bridge_reads_clients_and_window_through_tmux():
    """The argv the bridge runs every five seconds, on the session's socket, and its output as the
    page's frame: rows counted as a client's grid counts them, the status bar's lines added back."""
    assert clients_argv("ao-x", socket_name="s") == [
        "tmux", "-L", "s", "display-message", "-p", "-t", "=ao-x:",
        "#{session_attached} #{window_width} #{window_height} #{status}",
    ]  # fmt: skip
    assert clients_argv("ao-x")[:2] == ["tmux", "display-message"]
    assert clients_frame("2 120 31 on\n") == {"clients": 2, "window": [120, 32]}
    assert clients_frame("1 80 24 off") == {"clients": 1, "window": [80, 24]}
    assert clients_frame("1 80 22 2") == {"clients": 1, "window": [80, 24]}
    for bad in ("", "no server running", "1 80 on", "x 80 24 on"):
        assert clients_frame(bad) is None, bad


@pytest.mark.unit
def test_the_bridge_sends_a_frame_when_the_reading_changes_and_nothing_else():
    """With the tmux read stubbed: the first reading is sent, an unchanged one is not, a changed one is;
    a failed read (an error, or no output) sends nothing and leaves the last reading standing; a send
    that fails ends the watch."""
    readings = ["1 120 31 on", "1 120 31 on", None, "boom", "2 80 23 on", "2 80 23 on", "1 120 31 on"]
    sent: list[dict] = []

    async def read():
        if not readings:
            raise asyncio.CancelledError
        r = readings.pop(0)
        if r == "boom":
            raise OSError("tmux went away")
        return r

    async def send_text(t: str):
        sent.append(json.loads(t))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(watch_clients(read, send_text, every=0))
    assert sent == [
        {"clients": 1, "window": [120, 32]},
        {"clients": 2, "window": [80, 24]},
        {"clients": 1, "window": [120, 32]},
    ]

    async def broken(t: str):
        raise RuntimeError("socket closed")

    asyncio.run(watch_clients(lambda: asyncio.sleep(0, "1 1 1 on"), broken, every=0))  # returns: no loop


@pytest.mark.integration
def test_the_reading_is_what_tmux_says_for_a_second_client():
    """Against a private tmux server: a detached session reads no clients and its window's size."""
    if not shutil.which("tmux"):
        pytest.skip("tmux is not installed")
    sock = f"ao-mark-{uuid.uuid4().hex[:8]}"
    try:
        new = ["new", "-d", "-s", "m", "-x", "90", "-y", "20"]
        subprocess.run(["tmux", "-L", sock, "-f", "/dev/null", *new], check=True, timeout=10)
        out = subprocess.run(clients_argv("m", socket_name=sock), capture_output=True, text=True, timeout=10)
        assert out.returncode == 0, out.stderr
        assert clients_frame(out.stdout) == {"clients": 0, "window": [90, 21]}
    finally:
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True, timeout=10)
