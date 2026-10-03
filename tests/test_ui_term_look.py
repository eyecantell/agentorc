"""TD-288: a terminal size saved on Settings reached an open Focus (`AO.setTermLook` set the size
and refitted the grid) and tmux was never told: only the pane box's observer sent a resize, so the
pane kept drawing at the old width under a grid of another. The look's change is JavaScript, run as
itself under node; where the resize is sent from is read from the source."""

import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest

JS = pathlib.Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui" / "static" / "app.js"

PROBE = """
const fs = require("fs");
const noop = () => {};
const el = () => ({ dataset: {}, style: {}, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [] });
const document = { documentElement: el(), body: el(), querySelector: () => null, querySelectorAll: () => [],
  addEventListener: noop, createElement: el, fonts: { check: () => true } };
global.window = {}; global.document = document;
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO;
// a terminal whose grid follows its cell size in a fixed box, as xterm.js and the fit addon do
const sent = [];
const term = { options: { fontSize: 13, fontFamily: "" }, cols: 100, rows: 22,
  onResize(f) { this.listener = f; } };
term.onResize(({ cols, rows }) => sent.push([cols, rows]));
const fit = { fit() {
  const cols = Math.floor(700 / (term.options.fontSize * 0.54));
  const rows = Math.floor(400 / (term.options.fontSize * 1.38));
  if (cols !== term.cols || rows !== term.rows) { term.cols = cols; term.rows = rows; term.listener({ cols, rows }); }
} };
AO.terms.push({ term, fit });
AO.setTermLook({ size: 17 });
const after = { size: term.options.fontSize, grid: [term.cols, term.rows] };
AO.setTermLook({ size: 99 });  // out of range: the default
console.log(JSON.stringify({ after, fallback: term.options.fontSize, sent }));
"""


@pytest.mark.unit
def test_a_saved_size_reaches_an_open_terminal_and_refits_it():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "look_probe.js"
    probe.write_text(PROBE)
    out = subprocess.run([node, str(probe), str(JS)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["after"]["size"] == 17
    assert got["after"]["grid"] != [100, 22]  # the grid was refitted to the larger cells
    assert got["sent"][0] == got["after"]["grid"]  # …and the grid's change is what the terminal reports
    assert got["fallback"] == 13


@pytest.mark.unit
def test_focus_tells_tmux_every_change_of_the_grid():
    js = JS.read_text()
    start = js.index("const term = new Terminal(")
    body = js[start : start + 20000]
    # the resize goes from the terminal's own resize event, so a refit by a look change sends it too…
    sends = "ws && ws.readyState === 1 && ws.send(JSON.stringify({ resize: [cols, rows] }))"
    assert "term.onResize(({ cols, rows }) => { " + sends in body
    # …and the box's observer only fits, never sends a resize of its own
    obs = body[body.index("new ResizeObserver(") :]
    assert "ws.send" not in obs[: obs.index(".observe(")]
