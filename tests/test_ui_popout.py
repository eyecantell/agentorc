"""Pop out (design §4.5 screen 2 *Pop out*, §4.5a **Pop out** / **Focus** / **title**, TD-046): a
session's Focus in its own browser window, for the OS window switcher. The window's mechanics are
the browser's, so what is pinned here is what the page decides: the chromeless render, the controls
that open it, the title a switcher shows, and the JavaScript rules run as themselves under node."""

import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest
from fastapi.testclient import TestClient

UI = pathlib.Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui"

BASE = {
    "id": "ao-x-9", "name": "w", "kind": "agent", "adapter": "claude-code", "dir": "/tmp",
    "state": "idle", "since": "2026-09-23T16:00:00Z", "confidence": "hook", "pane": True, "tail": ["…"],
    "created": "2026-09-23T15:00:00Z", "seen_at": "2026-09-23T16:00:01Z",
}  # fmt: skip


def _focus(monkeypatch, tmp_path, popped, **rec):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    v = view({**BASE, "dir": str(tmp_path), **rec})
    return templates.get_template("focus.html").render(
        s={**v, "grants_all": [], "ready": []}, host="h", active="Org", popped=popped
    )


@pytest.mark.unit
def test_a_popped_out_focus_is_the_session_and_nothing_else(tmp_path, monkeypatch):
    """`?window=1` drops the top bar and the Org link and keeps everything Focus carries — the
    composer, the acts, the side panels: *a window that cannot answer a permission or wrap up is a
    worse Focus, not a lighter one*. A Focus in a tab keeps its chrome and offers Pop out."""
    tab = _focus(monkeypatch, tmp_path, False)
    assert 'class="topbar"' in tab and "← Org" in tab and 'data-act="popout"' in tab
    assert "AO.focus(" in tab and ", false);" in tab
    win = _focus(monkeypatch, tmp_path, True)
    assert 'class="topbar"' not in win and "← Org" not in win and 'data-act="popout"' not in win
    assert '<body data-host="h" class="popped">' in win and ", true);" in win
    for kept in (
        'id="composer"',
        'data-act="wrapup"',
        'data-act="kill"',
        'id="checks"',
        'id="inboxcard"',
        'id="mailbox"',
    ):
        assert kept in win, kept


@pytest.mark.unit
def test_every_focus_is_titled_by_its_name_and_state(tmp_path, monkeypatch):
    """§4.5a **title**: `<name> · <state>`, `▲ ` in front while it needs you — popped out or not,
    since the tab's title is what a switcher shows too. The page keeps it current (`AO.focusTitle`)."""
    assert "<title>w · idle</title>" in _focus(monkeypatch, tmp_path, False)
    pend = {"kind": "permission", "text": "Bash: ls", "tool_use_id": "t"}
    assert "<title>▲ w · needs you</title>" in _focus(monkeypatch, tmp_path, True, state="needs-you", pending=pend)
    assert "<title>w · working</title>" in _focus(monkeypatch, tmp_path, True, state="working")


@pytest.mark.unit
def test_the_card_offers_pop_out_in_more_and_marks_its_focus_link(tmp_path, monkeypatch):
    """§4.5a **Pop out** sits in the card's *more ▾*; **Focus** stays a plain link — a modifier is
    the browser's — carrying the id the page relabels to *Focus window* while it is popped out."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    html = templates.get_template("card.html").render(s=view({**BASE, "dir": str(tmp_path)}))
    menu = html[html.index('class="menu"') :]
    assert 'data-act="popout" data-id="ao-x-9"' in menu
    assert re.search(r'<a class="btn sm \w+" href="/focus/ao-x-9" data-focus="ao-x-9"', html)


PROBE = """
const fs = require("fs");
const noop = () => {};
const el = (o) => Object.assign({
  dataset: {}, style: {}, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [], contains: () => false,
}, o);
const link = el({ dataset: { focus: "a" }, textContent: "▣ Focus", classList: { toggle: noop } });
const other = el({ dataset: { focus: "b" }, textContent: "▣ Details", classList: { toggle: noop } });
const document = { documentElement: el(), body: el(),
  querySelector: () => null, querySelectorAll: (s) => (s === "a[data-focus]" ? [link, other] : []),
  addEventListener: noop, createElement: () => el() };
const opened = [];
let existing = null;
const window = { open(url, name, features) {
  opened.push({ url, name, features });
  if (existing) return existing;
  return { location: { pathname: "blank", href: "about:blank" }, focus: noop };
} };
global.window = window; global.document = document;
const saved = {};
global.localStorage = { getItem: (k) => (k in saved ? saved[k] : null), setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO;
const out = {};
out.title_idle = AO.focusTitle({ name: "w", state: "idle", state_label: "idle · unseen" });
out.title_needs = AO.focusTitle({ name: "w", state: "needs-you", state_label: "needs you" });
out.features_default = AO.popFeatures(null);
out.features_saved = AO.popFeatures({ w: 900, h: 700, x: 10, y: 20 });
out.features_junk = AO.popFeatures({ w: 0, h: "x" });
// a first press: a blank window comes back and is sent to the chromeless Focus
saved["ao.win.a"] = JSON.stringify({ w: 900, h: 700, x: 1, y: 2 });
let w1 = null; window.open = ((o) => (url, name, f) => { w1 = o(url, name, f); return w1; })(window.open);
AO.popOut("a");
out.first = { call: opened[0], href: w1.location.href };
// a second press finds the window by name and does not reload it
existing = { location: { pathname: "/focus/a", href: "keep" }, focus() { this.raised = true; } };
AO.popOut("a");
out.second = { call: opened[1], href: existing.location.href, raised: !!existing.raised };
// the cards: only a popped session's link reads *window*, and it reads back when the window goes
AO.popped.add("a"); AO.markPopped();
out.marked = [link.textContent, other.textContent];
AO.popped.delete("a"); AO.markPopped();
out.unmarked = link.textContent;
console.log(JSON.stringify(out));
"""


@pytest.mark.unit
def test_the_pop_out_rules_run_as_themselves():
    """The JavaScript half, under node: the title, the window's features (the person's size and
    position, else one that fits 100 columns), a first press that opens the chromeless Focus, a
    second that finds the window by name and raises it without a reload, and the card relabel."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "popout_probe.js"
    probe.write_text(PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["title_idle"] == "w · idle · unseen" and got["title_needs"] == "▲ w · needs you"
    assert got["features_default"] == "popup,width=1184,height=860"
    assert got["features_saved"] == "popup,width=900,height=700,left=10,top=20"
    assert got["features_junk"] == "popup,width=1184,height=860"
    assert got["first"] == {
        "call": {"url": "", "name": "ao-focus-a", "features": "popup,width=900,height=700,left=1,top=2"},
        "href": "/focus/a?window=1",
    }
    assert got["second"]["call"] == got["first"]["call"]  # the same name, and still no URL: no reload
    assert got["second"]["href"] == "keep" and got["second"]["raised"] is True
    assert got["marked"] == ["▣ Focus window", "▣ Details"] and got["unmarked"] == "▣ Focus"


@pytest.fixture
def client(subprocess_agent):
    from agentorc.ui.app import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.mark.integration
def test_the_focus_route_renders_chromeless_on_window_1(client, tmp_path):
    """The route: `/focus/{sid}?window=1` is the popped-out render, `/focus/{sid}` the full page —
    one route, one record, nothing written to it about the window."""
    r = client.post("/shell", data={"dir": str(tmp_path), "name": "pop"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    full, win = client.get(f"/focus/{sid}"), client.get(f"/focus/{sid}?window=1")
    assert full.status_code == win.status_code == 200
    assert 'class="topbar"' in full.text and 'class="topbar"' not in win.text
    assert "<title>pop · " in win.text
    rec = next(x for x in client.get("/api/sessions").json() if x["id"] == sid)
    assert not any("window" in k or "popped" in k for k in rec)
