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
