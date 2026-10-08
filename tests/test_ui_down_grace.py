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


POLL_PROBE = PROBE.split("const AO = window.AO;")[0] + """
const AO = window.AO;
const why = { textContent: "" };
const base = document.querySelector;
document.querySelector = (s) => (s === "#agentdownwhy" ? why : base(s));
const settle = async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r)); };
let polls = 0, isDown = true;
const DOWN = { agent_down: true, why: "refused" }, UP = { agent_down: false };
AO.refreshInboxCount = async () => { polls++; return isDown ? DOWN : UP; };
const step = async (ms) => { advance(ms); await settle(); };
const seen = () => ({ banner: !banner.classList.contains("hidden"), polls, pending: timers.length });
const out = {};
async function run() {
  await AO.inboxPoll(); out.first_failure = seen();             // nothing drawn, one retry pending
  await step(2000); out.within_grace = seen();                   // still nothing
  await step(1100); out.after_grace = seen();                    // the retry failed too: drawn, nothing pending
  out.why = why.textContent;
  await AO.inboxPoll(); out.failing_while_drawn = seen();        // drawn stays drawn, no retry armed
  await step(10000); out.no_stray_retry = seen();                // no poll the grace left behind
  isDown = false; await AO.inboxPoll(); out.recovered = seen();  // cleared
  isDown = true; await AO.inboxPoll(); out.down_again = seen();  // the grace again, not the banner at once
  isDown = false; await step(1000); await AO.inboxPoll(); out.back_within_grace = seen();  // cancels the retry
  await step(5000); out.after_cancelled = seen();                // nothing drawn, nothing polled
  isDown = true; await AO.inboxPoll(); await step(3100); out.down_for_the_grace_again = seen();
  console.log(JSON.stringify(out));
}
run().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.mark.unit
def test_the_inbox_polls_banner_waits_out_the_grace_and_a_recovery_resets_it(tmp_path):
    """The Inbox poll's own grace (TD-372, pinned by TD-383), `refreshInbox` run under node with a fake
    clock and a `refreshInboxCount` that fails, recovers and fails: a first failed poll draws nothing and
    asks again after `AO.DOWN_GRACE`; the second failure draws the banner and arms nothing more; a
    successful poll clears it and resets the grace, so the next outage waits again; a recovery within
    the grace cancels the retry."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    probe = tmp_path / "poll_probe.js"
    probe.write_text(POLL_PROBE)
    run = subprocess.run([node, str(probe), str(APP)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    got = json.loads(run.stdout)
    assert got["first_failure"] == {"banner": False, "polls": 1, "pending": 1}
    assert got["within_grace"] == {"banner": False, "polls": 1, "pending": 1}
    assert got["after_grace"] == {"banner": True, "polls": 2, "pending": 0}
    assert got["why"] == "refused"
    assert got["failing_while_drawn"] == {"banner": True, "polls": 3, "pending": 0}
    assert got["no_stray_retry"] == {"banner": True, "polls": 3, "pending": 0}
    assert got["recovered"] == {"banner": False, "polls": 4, "pending": 0}
    assert got["down_again"] == {"banner": False, "polls": 5, "pending": 1}  # the grace was reset
    assert got["back_within_grace"] == {"banner": False, "polls": 6, "pending": 0}
    assert got["after_cancelled"] == {"banner": False, "polls": 6, "pending": 0}
    assert got["down_for_the_grace_again"] == {"banner": True, "polls": 8, "pending": 0}
