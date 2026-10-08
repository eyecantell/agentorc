"""A team's + card is no session to the client (design §4.5a *team card: + card*, TD-379; TD-390):
the Org page's sort, filter and group swap run as themselves under node, over a small fake DOM —
the sort leaves the card last, a filter hides it and clearing the filter shows it again, a delta
whose group lacks it removes it and one that carries it puts it back last, and a group the server
no longer lists hands its sessions on and never the card."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

APP = pathlib.Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui" / "static" / "app.js"

# The fake DOM: elements with ids, classes, dataset, attributes, children and parent; selectors of
# compound parts (`tag`, `#id`, `.class`, `:not(.class)`, `[attr]`) joined by descendant spaces —
# what the Org functions ask for. A part it cannot read matches nothing.
PROBE = r"""
const fs = require("fs");
const noop = () => {};
const camel = (a) => a.replace(/^data-/, "").replace(/-([a-z])/g, (_, c) => c.toUpperCase());
class El {
  constructor(tag, attrs = {}) {
    this.tagName = tag.toUpperCase(); this.children = []; this.parentElement = null; this.hidden = false;
    this.dataset = {}; this.attrs = {}; this.style = {}; this.classes = new Set(); this._text = "";
    const self = this;
    this.classList = { add: (c) => self.classes.add(c), remove: (c) => self.classes.delete(c),
      contains: (c) => self.classes.has(c),
      toggle: (c, on) => (on ?? !self.classes.has(c) ? self.classes.add(c) : self.classes.delete(c)) };
    Object.entries(attrs).forEach(([k, v]) => this.setAttribute(k, v));
  }
  setAttribute(k, v) {
    if (k === "class") String(v).split(/\s+/).filter(Boolean).forEach((c) => this.classes.add(c));
    else if (k === "id") this.id = v;
    else if (k.startsWith("data-")) this.dataset[camel(k)] = String(v);
    else this.attrs[k] = String(v);
  }
  getAttribute(k) { return k.startsWith("data-") ? this.dataset[camel(k)] ?? null : this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
  hasAttr(k) { return k.startsWith("data-") ? camel(k) in this.dataset : k in this.attrs; }
  get className() { return [...this.classes].join(" "); }
  set className(v) { this.classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get firstElementChild() { return this.children[0] || null; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(" "); }
  set textContent(v) { this._text = String(v); this.children = []; }
  set innerHTML(html) {  // the shapes the Org swap writes: elements nested by their tags, no text that matters
    this.children = [];
    const stack = [this];
    for (const m of String(html).matchAll(/<(\/?)([a-z]+)([^>]*)>/g)) {
      if (m[1]) { stack.pop(); continue; }
      const attrs = {};
      for (const a of m[3].matchAll(/([\w-]+)="([^"]*)"/g)) attrs[a[1]] = a[2];
      const e = new El(m[2], attrs);
      stack[stack.length - 1].appendChild(e);
      if (!["input", "br", "img"].includes(m[2])) stack.push(e);
    }
  }
  appendChild(c) { if (c.parentElement) c.remove(); c.parentElement = this; this.children.push(c); return c; }
  insertBefore(c, at) {
    if (c.parentElement) c.remove();
    c.parentElement = this;
    const i = at ? this.children.indexOf(at) : -1;
    if (i < 0) this.children.push(c); else this.children.splice(i, 0, c);
    return c;
  }
  prepend(c) { this.insertBefore(c, this.children[0] || null); }
  remove() {
    const p = this.parentElement;
    if (p) { p.children.splice(p.children.indexOf(this), 1); this.parentElement = null; }
  }
  contains(x) { for (let e = x; e; e = e.parentElement) if (e === this) return true; return false; }
  addEventListener() {}
  all() { return this.children.flatMap((c) => [c, ...c.all()]); }
  querySelectorAll(sel) {
    return sel.split(",").flatMap((s) => this.all().filter((e) => matches(e, s.trim().split(/\s+/))));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
function one(e, part) {
  const m = part.match(/^([a-z]*)((?:[#.][\w-]+|:not\(\.[\w-]+\)|\[[\w-]+\])*)$/);
  if (!m) return false;
  if (m[1] && e.tagName !== m[1].toUpperCase()) return false;
  for (const t of m[2].match(/[#.][\w-]+|:not\(\.[\w-]+\)|\[[\w-]+\]/g) || []) {
    if (t[0] === "#" && e.id !== t.slice(1)) return false;
    if (t[0] === "." && !e.classes.has(t.slice(1))) return false;
    if (t[0] === ":" && e.classes.has(t.slice(6, -1))) return false;
    if (t[0] === "[" && !e.hasAttr(t.slice(1, -1))) return false;
  }
  return true;
}
function matches(e, parts) {
  if (!one(e, parts[parts.length - 1])) return false;
  let rest = parts.length - 2;
  for (let a = e.parentElement; a && rest >= 0; a = a.parentElement) if (one(a, parts[rest])) rest--;
  return rest < 0;
}
class Template extends El { get content() { return { firstElementChild: this.children[0] || null }; } }
const body = new El("body");
const document = { documentElement: new El("html"), body, activeElement: null, visibilityState: "visible",
  addEventListener: noop, createElement: (t) => (t === "template" ? new Template(t) : new El(t)),
  querySelector: (s) => body.querySelector(s), querySelectorAll: (s) => body.querySelectorAll(s) };
global.window = {}; global.document = document; global.CSS = { escape: (s) => s };
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO;

// the page: the filter box, the counts, and two groups — team `t`, its + card drawn **first** so the
// sort has to leave it last, and the headerless *No team*
const filter = body.appendChild(new El("input", { id: "filter" })); filter.value = "";
["count", "empty", "badges"].forEach((id) => body.appendChild(new El("div", { id })));
const groups = body.appendChild(new El("div", { id: "groups" }));
const card = (id, name, rank) => Object.assign(new El("div", { id: `card-${id}`, class: "card sc", "data-id": id,
  "data-name": name, "data-rank": rank, "data-state": "working", "data-team": "t" }), { textContent: name });
const PLUS = '<div class="card sc plus" data-plus="t"><a class="plusgo" href="/new?team=t">+</a></div>';
const t = groups.appendChild(new El("section", { class: "tgroup", "data-team": "t", "data-manager": "m" }));
const tg = t.appendChild(new El("div", { class: "grid" }));
const tpl = document.createElement("template"); tpl.innerHTML = PLUS; tg.appendChild(tpl.content.firstElementChild);
[["b", "beta", 2], ["a", "alpha", 2], ["m", "manager", 3]].forEach(([id, n, r]) => tg.appendChild(card(id, n, r)));
const none = groups.appendChild(new El("section", { class: "tgroup", "data-team": "" }));
none.appendChild(new El("div", { class: "grid" })).appendChild(card("x", "xray", 1)).dataset.team = "";

const group = (team) => groups.querySelectorAll(".tgroup").find((s) => s.dataset.team === team);
const order = (team) => {
  const s = group(team);
  return s ? s.querySelector(".grid").children.map((c) => (c.classes.has("plus") ? "+" : c.dataset.id)) : null;
};
const plus = () => groups.querySelector(".sc.plus");
const out = {};
AO.orgLayout();
out.sorted = order("t"); out.count = body.querySelector("#count").textContent;
filter.value = "alpha"; AO.orgLayout();
const shown = () => groups.querySelectorAll(".sc").filter((c) => !c.hidden).map((c) => c.dataset.id || "+");
out.filtered = { plus_hidden: plus().hidden, shown: shown() };
filter.value = ""; AO.orgLayout();
out.cleared = { plus_hidden: plus().hidden, count: body.querySelector("#count").textContent };
const delta = (withPlus, both = true) => AO.orgSyncGroups([
  { team: "t", manager: "m", ids: ["m", "a", "b"], plus: withPlus ? PLUS : "" },
  ...(both ? [{ team: "", manager: "", ids: ["x"], plus: "" }] : []),
]);
delta(false); out.delta_without = order("t");
delta(true); AO.orgLayout(); out.delta_with = order("t");
AO.orgSyncGroups([{ team: "", manager: "", ids: ["x"], plus: "" }]); AO.orgLayout();
out.group_gone = { t: order("t"), none: order(""), plus_anywhere: !!plus() };
console.log(JSON.stringify(out));
"""


@pytest.mark.unit
def test_the_plus_card_is_no_session_to_the_client(tmp_path):
    """§4.5a *team card: + card* (TD-379): the sort leaves it last and the count leaves it out; a
    filter hides it and clearing the filter shows it; a delta whose group lacks it removes it, one
    that carries it puts it back last; a removed group moves its sessions to *No team*, never it."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    probe = tmp_path / "plus_probe.js"
    probe.write_text(PROBE)
    run = subprocess.run([node, str(probe), str(APP)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {
        "sorted": ["m", "a", "b", "+"],  # the manager first, then rank and name; the card last
        "count": "4 sessions",  # m, a, b and x: the card is not counted
        "filtered": {"plus_hidden": True, "shown": ["a"]},
        "cleared": {"plus_hidden": False, "count": "4 sessions"},
        "delta_without": ["m", "a", "b"],
        "delta_with": ["m", "a", "b", "+"],
        # sorted there as *No team*'s own: rank, then name — no manager in that group
        "group_gone": {"t": None, "none": ["x", "a", "b", "m"], "plus_anywhere": False},
    }


@pytest.mark.unit
def test_the_enter_key_presses_the_plus_cards_link():
    """§4.5a *Org: keys*: `Enter` / `o` on the ringed + card press its link."""
    js = APP.read_text()
    assert 'sel: "a[data-focus], a.plusgo" }' in js
    css = (APP.parent / "app.css").read_text()
    assert ".sc[hidden] { display: none; }" in css  # a filter hides a card, the + card among them
