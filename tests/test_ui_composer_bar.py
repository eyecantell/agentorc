"""The composer's bar (design §4.5a *Focus composer* **the bar**, TD-491, built by TD-500): folded by
default, Focus's composer is one bar under the terminal and opens over the terminal's foot — never by
resizing it — folding back on Esc and on Send with the draft named on the bar; `person.composer: open`
draws the old shape; an unattended session draws neither. The page's rules run as themselves under node."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest

from sessionorc import settings as settings_mod

UI = pathlib.Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui"

BASE = {
    "id": "ao-x-9", "name": "w", "kind": "agent", "adapter": "claude-code", "dir": "/tmp",
    "state": "idle", "since": "2026-10-10T08:00:00Z", "confidence": "hook", "pane": True, "tail": ["…"],
    "created": "2026-10-10T07:00:00Z", "seen_at": "2026-10-10T08:00:01Z",
}  # fmt: skip


def _focus(monkeypatch, tmp_path, composer=None, **rec):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    v = view({**BASE, "dir": str(tmp_path), **rec})
    ctx = {"s": {**v, "grants_all": [], "ready": []}, "host": "h", "active": "Org", "popped": False}
    if composer:
        ctx["composer"] = composer
    return templates.get_template("focus.html").render(**ctx)


@pytest.mark.unit
def test_person_composer_is_folded_or_open_and_nothing_else():
    assert settings_mod.parse_person({"composer": "folded"}) == {"composer": "folded"}
    assert settings_mod.parse_person({"composer": "open"}) == {"composer": "open"}
    with pytest.raises(ValueError, match="composer is folded or open, not 'sideways'"):
        settings_mod.parse_person({"composer": "sideways"})
    assert settings_mod.parse_person({"composer": "sideways"}, drop=True) == {}


@pytest.mark.unit
def test_an_interactive_focus_draws_the_bar_and_the_composer_folded_over_the_terminal(tmp_path, monkeypatch):
    page = _focus(monkeypatch, tmp_path)
    assert 'class="termwrap cfold" id="termwrap"' in page
    assert 'class="card composer overlay folded" id="composer"' in page and 'rows="6"' in page
    assert 'class="card composerbar row gap" id="composerbar"' in page
    assert "✎ Compose a prompt… (c · or paste / drop a file)" in page and "Esc folds it back · Ctrl+Enter sends" in page
    for el in ('id="cbattach"', 'id="cbsend"', 'id="cbcancel"'):
        assert el in page, el
    # the overlay lives inside the terminal's box, the bar under it: the pane is never resized by either
    assert page.index('id="term"') < page.index('id="composer"') < page.index('id="composerbar"')


@pytest.mark.unit
def test_an_unattended_focus_draws_neither(tmp_path, monkeypatch):
    page = _focus(monkeypatch, tmp_path, unattended=True)
    assert 'class="card composer overlay folded hidden" id="composer"' in page
    assert 'class="card composerbar row gap hidden" id="composerbar"' in page


@pytest.mark.unit
def test_person_composer_open_draws_the_old_shape(tmp_path, monkeypatch):
    page = _focus(monkeypatch, tmp_path, composer="open")
    assert 'class="termwrap" id="termwrap"' in page and 'class="card composer" id="composer"' in page
    assert 'id="composerbar"' not in page and "Esc folds it back" not in page


PROBE = """
const fs = require("fs");
const noop = () => {};
const el = () => ({ dataset: {}, style: {}, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [] });
const document = { documentElement: el(), body: el(), querySelector: () => null, querySelectorAll: () => [],
  addEventListener: noop, createElement: el };
global.window = {}; global.document = document; global.Event = class { constructor(t) { this.type = t; } };
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO;
const cls = (init) => { const set = new Set(init); return { set, contains: (c) => set.has(c),
  add: (c) => set.add(c), remove: (c) => set.delete(c), toggle: (c, on) => (on ? set.add(c) : set.delete(c)) }; };
const stub = (extra) => { const on = {}; return Object.assign({ on, classList: cls([]),
  addEventListener: (k, f) => { on[k] = f; }, dispatchEvent: (e) => on[e.type] && on[e.type](e),
  focus() { focused = this.name; },
  click() { on.click && on.click({}); } }, extra); };
let focused = null, sent = 0;
// the terminal's box: what the bar must never touch
const term = stub({ name: "term", style: { height: "612px" } });
const composer = stub({ name: "composer", classList: cls(["overlay", "folded"]) });
const compose = stub({ name: "compose", value: "", disabled: false, selectionStart: 0, selectionEnd: 0 });
const text = stub({ name: "text", textContent: "" }), send = stub({ name: "send", disabled: false, textContent: "" });
const sendBtn = stub({ name: "sendBtn", textContent: "→ Steer", click() { sent++; } });
const hint = { textContent: "steers the turn in flight" }, bar = stub({ name: "bar" });
const cb = AO.wireComposerBar({ bar, text, send, composer, compose, sendBtn, hint, term });
const out = {};
cb.redraw(); out.idle = { text: text.textContent, send: send.textContent, draft: text.classList.contains("draft") };
text.click();
out.opened = { folded: cb.folded(), focused, term: term.style.height, under: bar.classList.contains("under") };
compose.value = "please read the fetcher before choosing a fix, then write the test first";
compose.dispatchEvent(new Event("input"));
cb.fold(); out.folded = { folded: cb.folded(), focused, term: term.style.height, text: text.textContent,
  under: bar.classList.contains("under"),
  draft: text.classList.contains("draft") };
send.click(); out.bar_send = sent;
compose.value = ""; send.click(); out.empty_send = { sent, folded: cb.folded() };
cb.fold(); compose.disabled = true; hint.textContent = "a permission is pending: answer above"; cb.redraw();
out.off = { text: text.textContent, send_disabled: send.disabled, off: text.classList.contains("off") };
const pe = { clipboardData: { types: ["text/plain"], getData: () => "hello" },
  preventDefault() { this.prevented = true; } };
compose.disabled = false; cb.fold(); bar.on.paste(pe);
out.paste = { folded: cb.folded(), value: compose.value, prevented: !!pe.prevented };
out.texts = {
  none: AO.barText("", ""), short: AO.barText("  fix   the race ", ""),
  long: AO.barText("one two three four five six seven eight nine ten", ""),
  reason: AO.barText("a draft", "answer in the terminal above"),
};
out.fill = { roomy: AO.termFill(1000, 120, 70), cramped: AO.termFill(500, 200, 70) };
out.key = AO.keyEntry("focus", "c");
console.log(JSON.stringify(out));
"""


def _probe() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the bar is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "bar_probe.js"
    probe.write_text(PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.unit
def test_the_bar_opens_and_folds_the_composer_and_never_touches_the_terminal():
    got = _probe()
    assert got["idle"] == {
        "text": "✎ Compose a prompt… (c · or paste / drop a file)",
        "send": "→ Steer",
        "draft": False,
    }
    # open, the composer takes the bar's place; folded, the bar is back
    assert got["opened"] == {"folded": False, "focused": "compose", "term": "612px", "under": True}
    assert got["folded"]["under"] is False
    assert got["folded"]["folded"] is True and got["folded"]["focused"] == "term" and got["folded"]["term"] == "612px"
    assert got["folded"]["text"] == "✎ draft · please read the fetcher before choosing a fix,…"
    assert got["folded"]["draft"] is True
    assert got["bar_send"] == 1  # the bar's Send is the composer's, with the draft
    assert got["empty_send"] == {"sent": 1, "folded": False}  # nothing to send: it opens the box instead
    assert got["off"] == {"text": "a permission is pending: answer above", "send_disabled": True, "off": True}
    assert got["paste"] == {"folded": False, "value": "hello ", "prevented": True}


@pytest.mark.unit
def test_the_bars_words_and_the_terminals_fill():
    got = _probe()
    t = got["texts"]
    assert t["none"].startswith("✎ Compose a prompt…") and t["short"] == "✎ draft · fix the race"
    assert t["long"] == "✎ draft · one two three four five six seven eight…"
    assert t["reason"] == "answer in the terminal above"
    assert got["fill"] == {"roomy": 810, "cramped": 360}  # the 360px floor kept


@pytest.mark.unit
def test_c_on_focus_opens_the_composer_where_a_bar_is_drawn():
    k = _probe()["key"]
    assert k["page"] == "focus" and k["keys"] == ["c"] and k["sel"] == "#composerbar:not(.hidden) #cbtext"
