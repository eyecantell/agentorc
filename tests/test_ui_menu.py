"""A *more ▾* / *Snooze* menu escapes the box it sits in (TD-121): the card keeps its one height and
its `overflow: hidden`, so the menu is drawn fixed to the viewport and placed from its summary's rect
when it opens. The placement is JavaScript, run as itself under node."""

import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

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
const P = window.AO.placeMenu;
const r = (left, top, w, h) => ({ left, top, right: left + w, bottom: top + h });
console.log(JSON.stringify({
  below: P(r(500, 100, 24, 24), 170, 200, 1200, 800),
  above: P(r(500, 700, 24, 24), 170, 200, 1200, 800),
  no_room_either: P(r(500, 150, 24, 24), 170, 700, 1200, 800),
  left_edge: P(r(20, 100, 24, 24), 170, 200, 1200, 800),
  right_edge: P(r(1190, 100, 24, 24), 170, 200, 1200, 800),
}));
"""


@pytest.mark.unit
def test_a_menu_is_placed_under_its_button_and_kept_on_screen():
    """Right-aligned under the summary; above it where the space below is short and above is not;
    below when neither fits; never past either side of the viewport (8px in)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "menu_probe.js"
    probe.write_text(PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["below"] == {"top": 128, "left": 354}
    assert got["above"] == {"top": 496, "left": 354}
    assert got["no_room_either"] == {"top": 178, "left": 354}
    assert got["left_edge"] == {"top": 128, "left": 8}
    assert got["right_edge"] == {"top": 128, "left": 1022}


@pytest.mark.unit
def test_the_menu_is_fixed_and_the_card_still_clips():
    """The card's one height (TD-095) stands — `.sc` keeps `overflow: hidden` — and the menu no
    longer lives inside that box: fixed, placed by the page on `toggle`, re-placed on scroll and resize."""
    css = (UI / "static" / "app.css").read_text()
    assert re.search(r"^\.sc \{[^}]*overflow: hidden", css, re.M)
    menu = re.search(r"^details\.more \.menu \{([^}]*)\}", css, re.M).group(1)
    assert "position: fixed" in menu and "position: absolute" not in menu
    js = (UI / "static" / "app.js").read_text()
    assert 'document.addEventListener("toggle"' in js and 'document.addEventListener("scroll", placeOpen, true)' in js
