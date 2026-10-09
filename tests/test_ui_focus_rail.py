"""The Focus side panel put away (design §4.5 *The panel put away*, §4.5a **» put away** / **«**,
TD-408, built by TD-412): one control puts the whole panel away to a 28px rail and the terminal
takes the width; **«** and every glyph bring it back, a glyph opening its card; the choice is this
browser's (`focus.side`), read before the first paint; `s` on Focus presses whichever is drawn."""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest
from test_ui import client  # noqa: F401 — the fixture, by name

UI = pathlib.Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui"

PROBE = """
const fs = require("fs");
const noop = () => {};
const el = () => ({ dataset: {}, style: {}, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [] });
const document = { documentElement: el(), body: el(), querySelector: () => null, querySelectorAll: () => [],
  addEventListener: noop, createElement: el };
global.window = {}; global.document = document;
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO;
const busy = { state: "needs-you", doing: { text: "reading the fetcher" }, progress: [{}, {}], findings: [{}] };
console.log(JSON.stringify({
  all: AO.railGlyphs(busy, true, 2, { reports: "2 progress · 1 filed", inbox: "1 unread · 2" }),
  quiet: AO.railGlyphs({ state: "idle" }, false, 0, {}),
  key: AO.keyEntry("focus", "s"),
}));
"""


def _probe() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rail's glyphs are JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "rail_probe.js"
    probe.write_text(PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.unit
def test_the_rail_draws_one_glyph_per_card_with_something_to_say_in_the_panels_order():
    got = _probe()
    assert [(g["card"], g["text"]) for g in got["all"]] == [
        ("", "!"),  # needs you: the prompt is the identity line's, so no card opens
        ("working", "✎"),
        ("reports", "3"),
        ("inbox", "2"),
        ("ready", "✓"),
    ]
    titles = [g["title"] for g in got["all"]]
    assert titles[1] == "Working — reading the fetcher" and titles[2] == "Reports — 2 progress · 1 filed"
    assert titles[3] == "Inbox — 1 unread · 2" and titles[4].startswith("Ready to close")
    assert got["quiet"] == []  # a quiet session's rail is « alone


@pytest.mark.unit
def test_s_on_focus_presses_whichever_of_put_away_and_bring_back_is_drawn():
    k = _probe()["key"]
    assert k["page"] == "focus" and k["keys"] == ["s"] and not k.get("ring")
    assert k["sel"] == ".side:not(.rail) #sideaway, .side.rail #sideback"
    assert "side panel" in k["control"]


@pytest.mark.unit
def test_the_stylesheet_gives_the_rail_28px_and_the_phone_a_row_of_44px_targets():
    css = (UI / "static" / "app.css").read_text()
    assert ".focus .side.rail { width: 28px; }" in css
    assert ".focus .side.rail > :not(.siderail):not(script) { display: none; }" in css
    phone = css[css.index("@media (max-width: 720px)") :]
    assert ".focus .side.rail { width: 100%; }" in phone and ".focus .siderail { flex-direction: row;" in phone
    assert ".focus .railbtn { width: 44px; height: 44px; }" in phone


def test_focus_draws_put_away_and_the_rail_read_before_the_first_paint(client, tmp_path):  # noqa: F811
    d = tmp_path / "railed"
    d.mkdir()
    r = client.post("/new", data={"name": "rl", "dir": str(d), "adapter": "hookstub"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    html = client.get(f"/focus/{sid}").text
    side = html[html.index('<div class="side" id="side">') :]
    # the choice is read inside the panel, before any card is drawn, so a put-away panel never flashes
    script = side[: side.index("</script>")]
    assert 'localStorage.getItem("ao.focus.side")) === "away"' in script and 'classList.add("rail")' in script
    assert side.index("</script>") < side.index('id="sideaway"') < side.index('data-side="working"')
    assert re.search(
        r'id="sideaway" title="Puts the side panel away to a narrow rail[^"]*\(s\)">» put away</button>', side
    )
    assert 'id="siderail"' in side and 'id="sideback"' in side and ">«</button>" in side
    assert 'id="railglyphs"' in side
    client.post(f"/api/sessions/{sid}/kill")


# The presses themselves (TD-417): `AO.wireRail` with fake elements and a real localStorage map, and
# the template's inline script run against what the presses stored, so the two keys must agree.
PRESS = """
const fs = require("fs");
const noop = () => {};
const els = {};
function el(name) {
  const cls = new Set(), on = {};
  return els[name] = { name, dataset: {}, style: {}, open: false, scrolled: false, cls,
    classList: { toggle: (c, f) => (f ? cls.add(c) : cls.delete(c)), add: (c) => cls.add(c),
      remove: (c) => cls.delete(c), contains: (c) => cls.has(c) },
    addEventListener: (k, f) => { on[k] = f; }, fire: (k, e) => on[k] && on[k](e || {}),
    scrollIntoView() { this.scrolled = true; } };
}
const mem = {};
global.localStorage = { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); } };
const document = { documentElement: el("html"), body: el("body"), querySelector: () => null,
  querySelectorAll: () => [], addEventListener: noop, createElement: () => el("x"),
  getElementById: (i) => els[i] };
global.window = {}; global.document = document;
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const inline = process.argv[3];
const AO = window.AO;
const side = el("side"), away = el("sideaway"), back = el("sideback"), glyphs = el("railglyphs");
const cards = { inbox: el("inbox") };
AO.wireRail({ side, away, back, glyphs, card: (n) => cards[n] || null });
// the template's script on a fresh page load: a new `side` element, the same storage
const reload = () => { const s = el("side"); eval(inline); return s.cls.has("rail"); };
const glyph = (rail) => ({ target: { closest: (sel) => (sel === "[data-rail]" ? { dataset: { rail } } : null) } });
const out = {};
away.fire("click");
out.away = { rail: side.cls.has("rail"), stored: mem["ao.focus.side"], reload: reload() };
back.fire("click");
out.back = { rail: side.cls.has("rail"), reload: reload() };
away.fire("click");
glyphs.fire("click", glyph("inbox"));
out.glyph = { rail: side.cls.has("rail"), open: cards.inbox.open, scrolled: cards.inbox.scrolled, reload: reload() };
away.fire("click");
glyphs.fire("click", glyph(""));  // needs-you: the panel comes back, no card to open
out.needs = { rail: side.cls.has("rail") };
away.fire("click");
glyphs.fire("click", { target: { closest: () => null } });  // a press between glyphs
out.between = { rail: side.cls.has("rail") };
console.log(JSON.stringify(out));
"""


@pytest.mark.unit
def test_put_away_bring_back_and_a_glyph_press_and_the_choice_survives_a_reload():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rail's presses are JavaScript, and nothing else runs it")
    html = (UI / "templates" / "focus.html").read_text()
    inline = re.search(
        r'<script>(try \{ if \(JSON\.parse\(localStorage\.getItem\("ao\.focus\.side"\).*?)</script>', html
    )
    assert inline, "the template's before-first-paint script"
    probe = pathlib.Path(tempfile.mkdtemp()) / "rail_press.js"
    probe.write_text(PRESS)
    run = [node, str(probe), str(UI / "static" / "app.js"), inline.group(1)]
    out = subprocess.run(run, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    # » put away: the rail, remembered as this browser's choice, and drawn again on the next load
    assert got["away"] == {"rail": True, "stored": '"away"', "reload": True}
    # «: the panel back, and the next load draws it so
    assert got["back"] == {"rail": False, "reload": False}
    # a glyph: the panel back with that card open and in view
    assert got["glyph"] == {"rail": False, "open": True, "scrolled": True, "reload": False}
    assert got["needs"] == {"rail": False}  # needs-you's glyph opens no card, but brings the panel back
    assert got["between"] == {"rail": True}  # a press that is not on a glyph does nothing


@pytest.mark.unit
def test_focus_wires_the_rail_to_the_templates_ids():
    js = (UI / "static" / "app.js").read_text()
    focus = js[js.index("AO.focus = function") :]
    call = focus[focus.index("AO.wireRail({") :][:300]
    for part in ('side: $("#side")', 'away: $("#sideaway")', 'back: $("#sideback")', 'glyphs: $("#railglyphs")'):
        assert part in call, part
    assert 'details.side[data-side="${name}"]' in call
