"""The host agent's down banner waits out a grace (design §4.5 *The host agent's down banner*,
TD-372): the page's event socket closing — a navigation, a promote's two-second restart — drew
*host agent unreachable* at once, though nothing had failed."""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

APP = pathlib.Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui" / "static" / "app.js"

PROBE = """
const fs = require("fs");
const noop = () => {};
let now = 0, timers = [];
const advance = (ms) => {
  now += ms;
  const due = timers.filter((t) => t.at <= now);
  timers = timers.filter((t) => t.at > now);
  due.forEach((t) => t.f());
};
const cls = () => {
  const s = new Set();
  return { toggle: (c, on) => (on ? s.add(c) : s.delete(c)), add: (c) => s.add(c), remove: (c) => s.delete(c),
    contains: (c) => s.has(c) };
};
const el = () => ({ dataset: {}, style: {}, addEventListener: noop, appendChild: noop, classList: cls(),
  querySelector: () => null, querySelectorAll: () => [], contains: () => false });
const banner = el(), dot = el();
banner.classList.add("hidden");
const document = { documentElement: el(), body: el(), addEventListener: noop, createElement: () => el(),
  querySelector: (s) => (s === "#agentdown" ? banner : s === "#hostdot" ? dot : null), querySelectorAll: () => [] };
global.window = {}; global.document = document;
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop;
global.setTimeout = (f, ms) => { const t = { f, at: now + (ms || 0) }; timers.push(t); return t; };
global.clearTimeout = (t) => { timers = timers.filter((x) => x !== t); };
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO;
const shown = () => !banner.classList.contains("hidden") && dot.classList.contains("down");
const out = { grace: AO.DOWN_GRACE };
AO.setDown(true); advance(2000); out.at_2s = shown();
AO.setDown(false); advance(5000); out.back_in_time = shown();
AO.setDown(true); advance(1000); AO.setDown(true); advance(2100); out.second_close_keeps_the_first = shown();
AO.setDown(false); out.cleared = shown();
banner.classList.remove("hidden"); AO.setDown(false); out.rendered_banner_cleared = banner.classList.contains("hidden");
console.log(JSON.stringify(out));
"""


@pytest.mark.unit
def test_the_banner_shows_only_after_the_socket_stays_down_for_the_grace(tmp_path):
    """Under node with a fake clock: a close draws nothing for three seconds and nothing at all
    when a message comes first; a second close while one is pending does not restart the wait; a
    message clears it, a banner the server rendered at load included."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    probe = tmp_path / "down_probe.js"
    probe.write_text(PROBE)
    run = subprocess.run([node, str(probe), str(APP)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {
        "grace": 3000, "at_2s": False, "back_in_time": False, "second_close_keeps_the_first": True,
        "cleared": False, "rendered_banner_cleared": True,
    }  # fmt: skip


@pytest.mark.unit
def test_a_close_the_page_caused_by_leaving_and_one_failed_inbox_poll_draw_nothing():
    """The `pagehide` guard on the socket's close, and the Inbox poll asking again after the grace
    rather than drawing the banner on one failed poll (TD-372)."""
    js = APP.read_text()
    connect = js[js.index("function connectEvents(") :]
    connect = connect[: connect.index("\n  }\n")]
    assert 'window.addEventListener("pagehide", () => { leaving = true; });' in connect
    assert 'window.addEventListener("pageshow", () => { leaving = false; });' in connect
    assert re.search(r"ws\.onclose = \(\) => \{ if \(!leaving\) setDown\(true\);", connect)
    assert "inboxDownLong = true; refreshInbox(); }, AO.DOWN_GRACE);" in js
    assert "if (!isDown || shown || inboxDownLong) {" in js
