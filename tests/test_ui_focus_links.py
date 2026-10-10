"""A URL in the pane is a link (design §4.6 *A URL in the pane is a link*, §4.5a **a URL is a link**,
TD-421, built by TD-422): xterm.js's web-links addon, vendored at the release paired with the
vendored xterm, underlines an http or https URL and calls `AO.paneLink` on a click of it; only
Ctrl+click or Cmd+click opens it, in a new tab with `noopener`, and only http and https."""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest

from agentorc.ui import help as helpmod

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
const press = (event, uri) => {
  const opened = [];
  const r = AO.paneLink(event, uri, (...a) => opened.push(a));
  return { r, opened };
};
const url = "https://github.com/eyecantell/agentorc/pull/1";
console.log(JSON.stringify({
  ctrl: press({ ctrlKey: true }, url),
  meta: press({ metaKey: true }, "http://127.0.0.1:8787/focus/x"),
  plain: press({}, url),
  shift: press({ shiftKey: true }, url),
  none: press(null, url),
  js: press({ ctrlKey: true }, "javascript:alert(1)"),
  file: press({ ctrlKey: true }, "file:///etc/passwd"),
  junk: press({ ctrlKey: true }, "not a url"),
}));
"""


def _probe() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the link's handler is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "link_probe.js"
    probe.write_text(PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.unit
def test_only_ctrl_or_cmd_click_opens_and_only_http_and_https():
    got = _probe()
    url = "https://github.com/eyecantell/agentorc/pull/1"
    assert got["ctrl"] == {"r": True, "opened": [[url, "_blank", "noopener"]]}
    assert got["meta"] == {"r": True, "opened": [["http://127.0.0.1:8787/focus/x", "_blank", "noopener"]]}
    # a plain click falls through to the selection: nothing opens
    for case in ("plain", "shift", "none"):
        assert got[case] == {"r": False, "opened": []}, case
    # the addon's regex is the addon's: the handler opens no other scheme, and nothing it cannot parse
    for case in ("js", "file", "junk"):
        assert got[case] == {"r": False, "opened": []}, case


@pytest.mark.unit
def test_focus_loads_the_addon_with_the_handler_on_every_focus():
    html = (UI / "templates" / "focus.html").read_text()
    assert html.index("vendor/xterm.js") < html.index("vendor/addon-web-links.js") < html.index("AO.focus(")
    js = (UI / "static" / "app.js").read_text()
    focus = js[js.index("AO.focus = function") :]
    made = "const links = new WebLinksAddon.WebLinksAddon((e, uri) => AO.paneLink(e, uri));"
    load = "term.loadAddon({ activate: (t) => links.activate(AO.cutRows(t)), dispose: () => links.dispose() });"
    assert made in focus and load in focus
    # beside the fit addon, before the attach decides read-only: no condition on the mode
    fit, ro = focus.index("new FitAddon.FitAddon()"), focus.index("let readOnly = false")
    assert fit < focus.index(made) < focus.index(load) < ro


@pytest.mark.unit
def test_the_addon_is_vendored_at_its_release_with_its_licence():
    vendor = UI / "static" / "vendor"
    data = (vendor / "addon-web-links.js").read_bytes()
    # the file @xterm/addon-web-links 0.11.0 publishes (npm and jsdelivr, 2026-10-08)
    assert hashlib.sha256(data).hexdigest() == "f230a6c8211ce4614dda5441f27b603c7c1ca95151a655bc0efac6377ee643f0"
    assert "Permission is hereby granted, free of charge" in (vendor / "LICENSE-addon-web-links.txt").read_text()
    readme = (vendor / "README.md").read_text()
    assert "| `addon-web-links.js` | `@xterm/addon-web-links` | 0.11.0" in readme


@pytest.mark.unit
def test_the_help_carries_the_pane_link_under_focus():
    h = {x.key: x for x in helpmod.HELP}["pane-link"]
    assert h.name == "a URL is a link" and h.where == "Focus pane"
    assert "Ctrl+click (Cmd+click on a Mac)" in h.text and "noopener" not in h.text
    assert "pane-link" in dict((g[0], g[2]) for g in helpmod.SCREENS)["focus"]


# The vendored addon itself reads a fake screen through `AO.cutRows` (TD-494): rows are strings, a
# cell is a character, and `links(y)` is what the addon's provider hands xterm for 1-based row y.
CUT_PROBE = """
const fs = require("fs");
const noop = () => {};
const el = () => ({ dataset: {}, style: {}, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [] });
const document = { documentElement: el(), body: el(), querySelector: () => null, querySelectorAll: () => [],
  addEventListener: noop, createElement: el };
global.window = {}; global.document = document; global.self = global;
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO;
const WebLinksAddon = require(process.argv[3]);
const cell = (ch) => ({ getChars: () => ch, getWidth: () => 1 });
const screen = (rows, cols) => {
  const line = (text) => {
    const cells = text.padEnd(cols, "\\0").slice(0, cols).split("").map((c) => (c === "\\0" ? "" : c));
    return { isWrapped: false, length: cols, getCell: (x) => cell(cells[x]),
      translateToString: (trim) => { const t = cells.map((c) => c || " ").join(""); return trim ? t.trimEnd() : t; } };
  };
  const lines = rows.map(line);
  let provider = null;
  const term = { registerLinkProvider: (p) => { provider = p; return { dispose: noop }; },
    buffer: { active: { getLine: (y) => lines[y], getNullCell: () => cell("") } } };
  const addon = new WebLinksAddon.WebLinksAddon(noop);
  addon.activate(AO.cutRows(term));
  return (y) => { let got; provider.provideLinks(y, (l) => { got = l; });
    return got.map((k) => ({ text: k.text, range: k.range })); };
};
const cols = 80, url = "https://example.com/" + "a".repeat(180);
const wrapped = [];
for (let i = 0; i < url.length; i += cols) wrapped.push(url.slice(i, i + cols));
const meet = ["x".repeat(cols - 22) + "https://a.example/abcd", "https://b.example/efgh and more"];
const prose = ["y".repeat(cols - 4) + "word", "next line, no link"];
const short = url.slice(0, 120), tail = ["see " + short.slice(0, cols - 4), short.slice(cols - 4) + " done"];
const unfull = ["see https://example.com/foo", "bar/baz qux"];
const spaced = ["x".repeat(cols - 23) + "https://a.example/abcd ", "efgh/ij"];
const indent = ["x".repeat(cols - 22) + "https://a.example/abcd", "    https://b.example/efgh"];
const one = screen(wrapped, cols), two = screen(meet, cols), three = screen(prose, cols), four = screen(tail, cols);
const five = screen(unfull, cols), six = screen(spaced, cols), seven = screen(indent, cols);
console.log(JSON.stringify({
  url, short, wrapped: [one(1), one(2), one(3)],
  meet: [two(1), two(2)],
  prose: [three(1), three(2)],
  tail: [four(1), four(2)],
  unfull: [five(1), five(2)],
  spaced: [six(1), six(2)],
  indent: [seven(1), seven(2)],
}));
"""


def _cut_probe() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the join is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "cut_probe.js"
    probe.write_text(CUT_PROBE)
    vendor = UI / "static" / "vendor" / "addon-web-links.js"
    out = subprocess.run(
        [node, str(probe), str(UI / "static" / "app.js"), str(vendor)], capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.unit
def test_a_url_tmux_cut_across_rows_is_one_link_from_every_row():
    got = _cut_probe()
    whole = {"text": got["url"], "range": {"start": {"x": 1, "y": 1}, "end": {"x": 40, "y": 3}}}
    # 200 characters on an 80-column pane: three rows, each of them the whole URL, spanning all three
    assert got["wrapped"] == [[whole], [whole], [whole]]
    # a URL after words on its first row, and words after it on its last
    span = {"text": got["short"], "range": {"start": {"x": 5, "y": 1}, "end": {"x": 44, "y": 2}}}
    assert got["tail"] == [[span], [span]]


@pytest.mark.unit
def test_two_rows_that_merely_meet_at_the_width_stay_two():
    got = _cut_probe()
    # a row full to its last column, and the next starts a scheme of its own: two links, not one (a
    # link that ends on the last column ends at x 0 of the next row: the addon's own range, unjoined too)
    first = {"text": "https://a.example/abcd", "range": {"start": {"x": 59, "y": 1}, "end": {"x": 0, "y": 2}}}
    second = {"text": "https://b.example/efgh", "range": {"start": {"x": 1, "y": 2}, "end": {"x": 22, "y": 2}}}
    assert got["meet"] == [[first], [second]]
    # prose that fills a row and goes on: joined, and still no link, since the regex finds none
    assert got["prose"] == [[], []]


@pytest.mark.unit
def test_a_row_is_joined_only_below_a_full_row_and_only_when_it_begins_with_a_url_character():
    # TD-518: each of the join rule's three other conditions, alone, keeps two rows two
    got = _cut_probe()
    # the row above is not full: a URL ending a short line does not run on into the words below it
    foo = {"text": "https://example.com/foo", "range": {"start": {"x": 5, "y": 1}, "end": {"x": 27, "y": 1}}}
    assert got["unfull"] == [[foo], []]
    # the row above is full, but its last cell is a written space: the URL before it ends there
    abcd = {"text": "https://a.example/abcd", "range": {"start": {"x": 58, "y": 1}, "end": {"x": 79, "y": 1}}}
    assert got["spaced"] == [[abcd], []]
    # the row above is full to its last column, and the next begins with whitespace: two links, and
    # the first row's link is not joined to the indented one below it
    first = {"text": "https://a.example/abcd", "range": {"start": {"x": 59, "y": 1}, "end": {"x": 0, "y": 2}}}
    second = {"text": "https://b.example/efgh", "range": {"start": {"x": 5, "y": 2}, "end": {"x": 26, "y": 2}}}
    assert got["indent"] == [[first], [second]]
