"""TD-224: the Org page threw its reader back to the top on a delta, once scrolled past the first
team. Every delta re-appended each section and card, and a layout forced halfway through the swap
(a summary's scroll read) let the browser's scroll anchoring chase a section being moved. A node
now moves only when it is out of the server's order, the reads come before anything moves, and a
fresh summary carries the person's faces before it is inserted. The placement is JavaScript, run as
itself under node; the order of the swap is read from the source."""

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
  addEventListener: noop, createElement: el };
global.window = {}; global.document = document;
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
// a parent that counts every node it detaches
function parent(names) {
  const p = { children: [], moves: 0 };
  p.insertBefore = (node, at) => {
    const i = p.children.indexOf(node);
    if (i >= 0) { p.children.splice(i, 1); p.moves++; }
    const j = at === null ? p.children.length : p.children.indexOf(at);
    p.children.splice(j, 0, node);
  };
  p.children.push(...names.map((n) => ({ n })));
  return p;
}
const order = (p, want) => {
  want.forEach((n, i) => window.AO.placeAt(p, p.children.find((c) => c.n === n), i));
  return p;
};
const same = order(parent(["a", "b", "c", "d"]), ["a", "b", "c", "d"]);
const swapped = order(parent(["a", "b", "c", "d"]), ["b", "a", "c", "d"]);
const fresh = parent(["a", "b"]); window.AO.placeAt(fresh, { n: "new" }, 2);
console.log(JSON.stringify({
  same: { moves: same.moves, order: same.children.map((c) => c.n) },
  swapped: { moves: swapped.moves, order: swapped.children.map((c) => c.n) },
  fresh: { moves: fresh.moves, order: fresh.children.map((c) => c.n) },
}));
"""


@pytest.mark.unit
def test_a_node_already_in_place_is_not_moved():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "order_probe.js"
    probe.write_text(PROBE)
    out = subprocess.run([node, str(probe), str(JS)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["same"] == {"moves": 0, "order": ["a", "b", "c", "d"]}  # a delta that moved nothing detaches nothing
    assert got["swapped"] == {"moves": 1, "order": ["b", "a", "c", "d"]}
    assert got["fresh"] == {"moves": 0, "order": ["a", "b", "new"]}


@pytest.mark.unit
def test_the_swap_reads_first_and_moves_only_what_is_out_of_order():
    js = JS.read_text()
    start = js.index("function syncGroups(gs)")
    body = js[start : js.index("\n  }\n", start)]
    # every summary's scroll positions are read before the first node is replaced or moved
    read = body.index("scrollKept[sum.dataset.team] = AO.scrolls(sum)")
    assert read < body.index("sum.replaceWith(fresh)") and read < body.index("AO.placeAt(box, sec")
    assert body.count("AO.scrolls(") == 1  # …and never again inside the loop, where it forces a layout
    # the fresh summary shows the person's faces before it goes in, at the height it will keep
    assert body.index("showSummary(fresh)") < body.index("sum.replaceWith(fresh)")
    assert "box.appendChild(sec)" not in body
    # the cards' order inside each grid is kept the same way
    lay = js[js.index("function layout()") :]
    assert "AO.placeAt(grid, c, i)" in lay[: lay.index("applyFilter();")]
