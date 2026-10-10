"""The Settings page's **You** card as its own script runs it (design §4.5a *Settings page: You, file link*,
TD-536, built by TD-537; the test gap TD-546): `AO.settings()` under node against a fake form — what
**Save** posts for the file link and a template's **file**, and what the editor pick and the switch do
to the rows and the wait. `test_ui_settings.py` posts the JSON itself and reads the server's page, so no
line of this script runs there."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest

UI = pathlib.Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui"

PROBE = r"""
const fs = require("fs");
const noop = () => {};
// an element: its listeners kept by kind, its classes a set, its own children found by selector
const el = (props = {}) => {
  const on = {}, cls = new Set(props.classes || []);
  return Object.assign({
    dataset: {}, style: {}, on, children: {},
    addEventListener: (k, f) => { (on[k] = on[k] || []).push(f); },
    fire(k, ev) { (on[k] || []).forEach((f) => f(ev || { target: this })); },
    classList: { toggle: (c, f) => (f ? cls.add(c) : cls.delete(c)), add: (c) => cls.add(c),
      remove: (c) => cls.delete(c), contains: (c) => cls.has(c) },
    hidden: false, querySelector(sel) { return this.children[sel] || null; }, querySelectorAll: () => [],
  }, props);
};
const byId = {};
const document = { documentElement: el(), body: el(), createElement: () => el(), addEventListener: noop,
  querySelector: (sel) => byId[sel] || null, querySelectorAll: () => [] };
global.window = {}; global.document = document;
global.localStorage = { getItem: () => null, setItem: noop, removeItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/settings", protocol: "http:", host: "x", reload: noop };
const posted = [];
global.fetch = (url, opts) => {
  posted.push([url, JSON.parse(opts.body)]);
  return Promise.resolve({ ok: true, json: () => ({ ok: true }) });
};
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO;
AO.toast = noop;

// the You card's fields, by name, iterable as a form's `elements` are
const field = (name, props = {}) => el({ name, type: "text", value: "", tagName: "INPUT", defaultValue: "", ...props });
const names = {
  open_in: field("open_in", { tagName: "SELECT", value: "template", options: [] }),
  label: field("label", { value: "Zed" }), url: field("url", { value: "zed://ssh/{remote}{path}" }),
  file: field("file", { value: "" }),
  folder_first: field("folder_first", { type: "checkbox", checked: true }),
  wait: field("wait", { type: "number", value: "1" }),
  size: field("size"), face: field("face"),
  copy_on_select: field("copy_on_select", { type: "checkbox", checked: false }),
  attach_max: field("attach_max"), composer: field("composer", { tagName: "SELECT", value: "folded", options: [] }),
  board_show: field("board_show", { value: "next" }), board_next: field("board_next"), board_days: field("board_days"),
};
const elements = Object.assign(Object.values(names), names);
const you = el({ elements, dataset: { section: "you" }, reset: noop });
you.closest = (sel) => (sel === "form.setcard" ? you : null);
const page = el({ dataset: {} });
page.querySelectorAll = (sel) => (sel === "form.setcard" ? [you] : []);
const fileLink = el({ classes: [] }), template = el({ classes: [] }), cancel = el();
you.children[".setcancel"] = cancel;
Object.assign(byId, { "#setpage": page, "#setopenin": names.open_in, "#setfilelink": fileLink,
  "#settemplate": template, "#setyou": you, "#setreset": el() });
AO.settings();

const save = async () => {
  posted.length = 0;
  page.fire("submit", { target: you, preventDefault: noop });
  await new Promise((r) => setImmediate(r));
  return posted.map(([url, body]) => ({ url, open_in: body.open_in, file_link: body.file_link }));
};
(async () => {
  const out = {};
  // Save as drawn, then with the wait edited, emptied, the switch off and a template's file written
  out.drawn = await save();
  names.wait.value = "2.5"; out.edited = await save();
  names.wait.value = ""; out.emptied = await save();
  names.folder_first.checked = false; names.wait.value = "1"; out.off = await save();
  names.file.value = "  zed://f/{remote}{path}:{line} "; out.file = await save();
  names.open_in.value = "vscode"; out.vscode = await save();
  // the editor pick: `none` hides the file link's rows, any editor draws them
  const hid = (x) => x.classList.contains("hidden");
  const rows = () => ({ fileLink: hid(fileLink), template: hid(template) });
  names.open_in.value = "none"; names.open_in.fire("change"); out.none = rows();
  names.open_in.value = "template"; names.open_in.fire("change"); out.template = rows();
  // the switch: off disables the wait, on enables it again
  names.folder_first.checked = false; names.folder_first.fire("change"); out.switchOff = names.wait.disabled;
  names.folder_first.checked = true; names.folder_first.fire("change"); out.switchOn = names.wait.disabled;
  // Cancel puts back what the drawn values show: here `none` and the switch off
  names.open_in.value = "none"; names.folder_first.checked = false; names.wait.disabled = false;
  cancel.fire("click"); out.cancelled = { ...rows(), wait: names.wait.disabled };
  console.log(JSON.stringify(out));
})();
"""


def _probe() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the You card's form is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "settings_you_probe.js"
    probe.write_text(PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.unit
def test_save_posts_the_file_links_switch_and_wait_as_the_form_holds_them():
    got = _probe()
    assert [p["url"] for p in got["drawn"]] == ["/api/settings/you"]
    assert got["drawn"][0]["file_link"] == {"folder_first": True, "wait": 1}
    assert got["edited"][0]["file_link"] == {"folder_first": True, "wait": 2.5}  # an edited wait is saved
    assert got["emptied"][0]["file_link"] == {"folder_first": True, "wait": None}  # an emptied one is cleared
    assert got["off"][0]["file_link"] == {"folder_first": False, "wait": 1}  # the switch can be saved off


@pytest.mark.unit
def test_save_sends_a_templates_file_only_when_written():
    got = _probe()
    assert got["drawn"][0]["open_in"] == {"label": "Zed", "url": "zed://ssh/{remote}{path}"}  # empty: the url's road
    assert got["file"][0]["open_in"] == {
        "label": "Zed",
        "url": "zed://ssh/{remote}{path}",
        "file": "zed://f/{remote}{path}:{line}",
    }
    assert got["vscode"][0]["open_in"] == "vscode"  # an editor by name carries no template's file


@pytest.mark.unit
def test_the_editor_pick_hides_the_file_link_under_none_and_the_switch_disables_the_wait():
    got = _probe()
    assert got["none"] == {"fileLink": True, "template": True}
    assert got["template"] == {"fileLink": False, "template": False}
    assert got["switchOff"] is True and got["switchOn"] is False
    # Cancel redraws both from the form's values, without the handlers' side effects
    assert got["cancelled"] == {"fileLink": True, "template": True, "wait": True}
