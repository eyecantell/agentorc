"""The Session card's recent files and its editor button (design §4.5 item 4, §4.5a **Focus side panel,
Session card: VS Code** and **recent files**, **» put away**'s **‹›**, TD-525, built by TD-527): the
record's `files` drawn as links by the editor's file form, **M** before one `git.files` holds, the
editor button on the card's summary line and nowhere else on Focus, and the rail's **‹›** carrying the
button's press. The page's functions run as themselves under node."""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest
from test_ui import client  # noqa: F401 — the fixture, by name

from agentorc.ui import help as helpmod

UI = pathlib.Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui"

PROBE = """
const fs = require("fs");
const noop = () => {};
const el = () => ({ dataset: {}, style: {}, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [] });
const clicks = [];
const document = { documentElement: el(), body: el(), querySelector: () => null, querySelectorAll: () => [],
  addEventListener: (k, f) => { if (k === "click") clicks.push(f); }, createElement: el };
global.window = {}; global.document = document;
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
const timers = [];
global.setInterval = noop; global.setTimeout = (f, ms) => timers.push([f, ms]); global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO;
const editor = { url: "vscode://file/w/wt?windowId=_blank", label: "VS Code", file: "vscode://file{path}" };
const v = {
  dir: "/w/wt", repo: "/w/main",
  files: [
    { path: "/w/wt/src/new file.py", at: "2026-10-10T20:05:09Z" },
    { path: "/w/wt/README.md", at: "2026-10-10T20:01:00Z", sha: "3f2a9c1d0e8b" },
    { path: "/w/main/docs/x.md", at: "2026-10-10T19:00:00Z" },
    { path: "/etc/hosts", at: "2026-10-10T18:00:00Z" },
  ],
  git: { files: ["M src/new file.py", "?? notes.txt"] },
};
// the Session card's row and term, and the rail, as elements the page's paint writes into
const box = () => { const cls = new Set(["hidden"]); return { innerHTML: "", cls,
  classList: { toggle: (c, f) => (f ? cls.add(c) : cls.delete(c)) } }; };
const row = box(), dt = box();
AO.paintRecent(v, editor, row, dt);
const painted = { html: row.innerHTML, rowHidden: row.cls.has("hidden"), dtHidden: dt.cls.has("hidden") };
AO.paintRecent({ dir: "/w/wt", files: [] }, editor, row, dt);
const emptied = { html: row.innerHTML, rowHidden: row.cls.has("hidden"), dtHidden: dt.cls.has("hidden") };
// a click on an editor link, through the page's own handlers (TD-535): a recent file's link carries
// its folder and sends the pair; the Session card's button carries none and sends one launch
const click = (a) => {
  const opened = [];
  const saved = AO.openEditor; AO.openEditor = (...x) => opened.push(x);
  timers.length = 0;
  const ev = { target: { closest: (sel) => (sel === "a.editor" ? a : null) }, preventDefault: noop };
  clicks.forEach((f) => { try { f(ev); } catch (e) {} });
  timers.splice(0).forEach(([f]) => f());
  AO.openEditor = saved;
  return opened;
};
const recentClick = click({ href: "vscode://file/w/wt/README.md:1",
  dataset: { folder: editor.url, label: "VS Code" } });
const buttonClick = click({ href: editor.url, dataset: { label: "VS Code" } });
// the person's `file_link` (TD-536): a row drawn with the folder off carries none; with a wait, its
// seconds, which the click reads
const off = { ...editor, first: false, wait: 2 }, slow = { ...editor, first: true, wait: 3 };
const waitClick = (() => {
  const saved = AO.openEditor; AO.openEditor = noop;
  timers.length = 0;
  const a = { href: "vscode://file/w/wt/README.md:1", dataset: { folder: editor.url, label: "VS Code", wait: "3" } };
  const ev = { target: { closest: (sel) => (sel === "a.editor" ? a : null) }, preventDefault: noop };
  clicks.forEach((f) => { try { f(ev); } catch (e) {} });
  const waits = timers.splice(0).map(([, ms]) => ms);
  AO.openEditor = saved;
  return waits;
})();
console.log(JSON.stringify({
  recentClick, buttonClick, waitClick,
  linkedOff: AO.recentFiles(v, off), linkedSlow: AO.recentFiles(v, slow),
  painted, emptied,
  railHtml: AO.railHtml({ state: "idle" }, true, 0, {}, editor),
  railHtmlNone: AO.railHtml({ state: "idle" }, true, 0, {}, null),
  linked: AO.recentFiles(v, editor),
  text: AO.recentFiles(v, { url: "zed://x", label: "Zed", file: null }),
  none: AO.recentFiles({ dir: "/w/wt", files: [] }, editor),
  noFiles: AO.recentFiles({ dir: "/w/wt" }, editor),
  sub: AO.recentFiles({ dir: "/w/wt/sub", files: [{ path: "/w/wt/sub/a.py" }, { path: "/w/wt/sub/b.py" }],
    git: { files: ["M sub/a.py", "M b.py"] } }, editor),
  rail: AO.railGlyphs({ state: "idle" }, true, 0, {}, editor),
  railNone: AO.railGlyphs({ state: "idle" }, true, 0, {}, null),
}));
"""


def _probe() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the Session card's row is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "recent_probe.js"
    probe.write_text(PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.unit
def test_the_row_draws_the_records_files_newest_first_relative_with_their_marks_and_links():
    got = _probe()
    rows = re.findall(r"<div>(.*?)</div>", got["linked"])
    assert len(rows) == 4
    # newest first, as the record keeps them; relative to the directory, else the repo, else absolute
    assert [re.sub(r"<[^>]+>", "", r) for r in rows] == ["M src/new file.py", "README.md", "docs/x.md", "/etc/hosts"]
    # **M** only before the one the worktree's porcelain lines hold
    assert rows[0].startswith('<span class="fmark"') and "fmark" not in "".join(rows[1:])
    # a link by the file form at line 1, opened by the `a.editor` handler on a plain click
    assert (
        'class="editor" href="vscode://file/w/wt/src/new%20file.py:1" data-folder="vscode://file/w/wt?windowId=_blank"'
        ' data-label="VS Code"'
    ) in rows[0]
    # the absolute path and the edit's time on hover
    assert 'title="/w/wt/src/new file.py — edited 2026-10-10 20:05Z"' in rows[0]
    # a committed one: the commit's time and its short sha (TD-538)
    assert 'title="/w/wt/README.md — committed 3f2a9c1 2026-10-10 20:01Z"' in rows[1]


@pytest.mark.unit
def test_m_reads_the_porcelain_from_the_worktrees_root_when_the_directory_is_below_it():
    rows = re.findall(r"<div>(.*?)</div>", _probe()["sub"])
    # the record carries no git root, so a root-level `b.py` and the directory's own read alike: only
    # the directory's path below the root is pinned here
    assert re.sub(r"<[^>]+>", "", rows[0]) == "M a.py"


@pytest.mark.unit
def test_a_recent_file_click_sends_the_folder_then_the_file_and_the_cards_button_one_launch():
    got = _probe()
    assert got["recentClick"] == [
        ["vscode://file/w/wt?windowId=_blank", "VS Code"],
        ["vscode://file/w/wt/README.md:1", "VS Code", True],
    ]
    assert got["buttonClick"] == [["vscode://file/w/wt?windowId=_blank", "VS Code"]]


@pytest.mark.unit
def test_a_recent_file_follows_the_persons_file_link():
    """§5 `person.file_link` (TD-536, built by TD-537): with the folder off a row carries no folder, so a
    click is the file alone; with a wait, the row carries its seconds and the click waits them."""
    got = _probe()
    assert "data-folder" not in got["linkedOff"] and 'href="vscode://file/w/wt/README.md:1"' in got["linkedOff"]
    assert 'data-folder="vscode://file/w/wt?windowId=_blank" data-wait="3" data-label="VS Code"' in got["linkedSlow"]
    assert got["waitClick"] == [3000]


@pytest.mark.unit
def test_no_file_form_draws_text_and_no_edit_draws_nothing():
    got = _probe()
    assert "<a " not in got["text"] and "src/new file.py" in got["text"]
    assert got["none"] == "" and got["noFiles"] == ""


@pytest.mark.unit
def test_the_rails_last_glyph_is_the_editor_button_opening_the_worktree():
    got = _probe()
    last = got["rail"][-1]
    assert [g["text"] for g in got["rail"]] == ["✓", "‹›"]
    assert last["href"] == "vscode://file/w/wt?windowId=_blank" and last["title"] == "VS Code — opens the worktree"
    assert [g["text"] for g in got["railNone"]] == ["✓"]  # no button, no glyph


@pytest.mark.unit
def test_the_paint_shows_the_row_with_the_first_edit_and_hides_it_again_with_none():
    got = _probe()
    assert not got["painted"]["rowHidden"] and not got["painted"]["dtHidden"]
    assert got["painted"]["html"].count('<a class="editor"') == 4
    assert got["emptied"] == {"html": "", "rowHidden": True, "dtHidden": True}


@pytest.mark.unit
def test_the_rails_last_child_is_the_editor_link_with_the_buttons_href():
    got = _probe()
    html = got["railHtml"]
    assert html.startswith('<button class="railbtn g-ready"')  # ✓ opens its card
    assert html.endswith(
        '<a class="railbtn editor g-editor" href="vscode://file/w/wt?windowId=_blank" data-label="VS Code"'
        ' title="VS Code — opens the worktree">‹›</a>'
    ), html
    assert "railbtn editor" not in got["railHtmlNone"]


@pytest.mark.unit
def test_focus_paints_the_row_and_the_rail_from_each_view_with_its_editor():
    """The two call sites the probes cannot reach: Focus's render paints the row from every view, and
    the rail is drawn with the page's editor, so the **‹›** glyph has a button to carry (TD-534)."""
    js = (UI / "static" / "app.js").read_text()
    focus = js[js.index("AO.focus = function") :]
    render = focus[focus.index("    function render(v) {") :]
    render = render[: render.index("\n    }\n")]
    assert '      AO.paintRecent(v, s.editor, $("#frecent"), $("#frecentdt"));' in render
    # the person's file_link with each view, onto the object the pane's link provider holds (TD-537)
    assert "if (s.editor && v.editor) { s.editor.first = v.editor.first; s.editor.wait = v.editor.wait; }" in render
    rail = focus[focus.index("    function renderRail(v, ready) {") :]
    rail = rail[: rail.index("\n    }\n")]
    assert "      const html = AO.railHtml(railV, railReady, inboxN, lines, s.editor);" in rail
    assert '$("#railglyphs")' in rail and "renderRail(v, ready);" in render


def test_focus_draws_the_editor_button_on_the_session_cards_summary_and_not_the_header(client, tmp_path):  # noqa: F811
    d = tmp_path / "edited"
    d.mkdir()
    r = client.post("/new", data={"name": "ed", "dir": str(d), "adapter": "hookstub"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    html = client.get(f"/focus/{sid}").text
    assert html.count('class="btn sm link editor"') == 1
    card = html[html.index('data-side="session"') :]
    summary = card[: card.index("</summary>")]
    assert 'id="feditor"' in summary and "‹› VS Code</a>" in summary
    # the row is the script's, from the view: there, under *run log*'s place, hidden until an edit
    assert 'id="frecentdt" class="hidden">recent files</dt>' in card and 'id="frecent"' in card
    client.post(f"/api/sessions/{sid}/kill")


@pytest.mark.unit
def test_the_help_says_file_links_follow_the_session_cards_button():
    text = {h.key: h.text for h in helpmod.HELP}["pane-path"]
    assert "without an editor button on the Session card there are no file links." in text
