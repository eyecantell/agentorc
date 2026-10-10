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
const editor = { url: "vscode://file/w/wt?windowId=_blank", label: "VS Code", file: "vscode://file{path}" };
const v = {
  dir: "/w/wt", repo: "/w/main",
  files: [
    { path: "/w/wt/src/new file.py", at: "2026-10-10T20:05:09Z" },
    { path: "/w/wt/README.md", at: "2026-10-10T20:01:00Z" },
    { path: "/w/main/docs/x.md", at: "2026-10-10T19:00:00Z" },
    { path: "/etc/hosts", at: "2026-10-10T18:00:00Z" },
  ],
  git: { files: ["M src/new file.py", "?? notes.txt"] },
};
console.log(JSON.stringify({
  linked: AO.recentFiles(v, editor),
  text: AO.recentFiles(v, { url: "zed://x", label: "Zed", file: null }),
  none: AO.recentFiles({ dir: "/w/wt", files: [] }, editor),
  noFiles: AO.recentFiles({ dir: "/w/wt" }, editor),
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
    assert 'class="editor" href="vscode://file/w/wt/src/new%20file.py:1" data-label="VS Code"' in rows[0]
    # the absolute path and the edit's time on hover
    assert 'title="/w/wt/src/new file.py — edited 2026-10-10 20:05Z"' in rows[0]


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
    assert text.endswith("without an editor button on the Session card there are no file links.")
