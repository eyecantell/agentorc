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
    load = "term.loadAddon(new WebLinksAddon.WebLinksAddon((e, uri) => AO.paneLink(e, uri)));"
    assert load in focus
    # beside the fit addon, before the attach decides read-only: no condition on the mode
    assert focus.index("new FitAddon.FitAddon()") < focus.index(load) < focus.index("let readOnly = false")


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
