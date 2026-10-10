"""A path in the pane is a link (design §4.6 *A path in the pane is a link*, §4.5a **a path is a link**,
TD-493, built by TD-501): the page is served the editor button's file form beside the button
(`editor.file`), a link provider of its own asks the `paths` read once per row through
`POST /api/sessions/<id>/paths`, and only what the host resolved underlines and opens, on Ctrl or Cmd,
the line riding on the `vscode://file` form alone. The page's functions run as themselves under node."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest
import yaml
from fastapi.testclient import TestClient

from agentorc.ui import help as helpmod
from agentorc.ui import uiconf

UI = pathlib.Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui"
REACH = "vscode://vscode-remote/attached-container+7b22636f6e7461696e65724e616d65223a222f6e31227d/workspaces/r?windowId=_blank"


def _person(text: str) -> None:
    uiconf.set_read({"person": yaml.safe_load(text) or {}, "migrate": []})


@pytest.fixture
def person():
    yield _person
    uiconf.set_read({"person": {}, "migrate": []})


@pytest.mark.unit
def test_the_file_form_is_the_buttons_template_with_path_unfilled(person):
    person("")
    assert uiconf.editor_file(local=True, remote="km") == "vscode://file{path}?windowId=_blank"
    assert uiconf.editor_file(local=False, remote="km") == "vscode://vscode-remote/ssh-remote+km{path}?windowId=_blank"
    # a container's record: the reach link's own prefix, the file in its folder's place
    got = uiconf.editor_file(local=False, remote="km", reach=REACH)
    assert got == REACH.replace("/workspaces/r?", "{path}?")
    assert uiconf.editor_file(local=False, remote="km", reach="vscode://vscode-remote/x") is None
    person("open_in: {label: Zed, url: 'zed://ssh/{remote}{path}'}")
    assert uiconf.editor_file(local=False, remote="km") == "zed://ssh/km{path}"
    # a template names no container, and one without {path} names no file: no file links
    assert uiconf.editor_file(local=False, remote="km", reach=REACH) is None
    person("open_in: {label: Zed, url: 'zed://ssh/{remote}'}")
    assert uiconf.editor_file(local=False, remote="km") is None
    person("open_in: none")
    assert uiconf.editor_file(local=True, remote="km") is None


@pytest.mark.unit
def test_focus_is_served_editor_file_only_beside_its_editor_button(tmp_path, monkeypatch, person):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import view

    rec = {"id": "ao-x-1", "name": "w", "kind": "agent", "dir": str(tmp_path), "state": "idle"}
    person("")
    v = view(rec)
    assert v["editor"]["file"] == "vscode://file{path}?windowId=_blank" and v["editor"]["url"].startswith("vscode://")
    person("open_in: none")
    assert view(rec)["editor"] is None
    # a record on another host the button cannot reach: no button, so no file form
    person("")
    assert view({**rec, "host": "elsewhere"})["editor"] is None


@pytest.mark.unit
def test_the_route_forwards_the_runs_to_the_paths_read(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui import app as ui

    asked: list[tuple[str, dict]] = []

    class Fake:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call(self, method, **kw):
            asked.append((method, kw))
            if method == "paths" and kw["id"] == "gone":
                raise ui.AgentError("no session gone")
            return {"paths": {"src/a.py": "/r/src/a.py", "build/review": None}} if method == "paths" else {}

    monkeypatch.setattr(ui, "LocalClient", Fake)
    c = TestClient(ui.create_app())
    r = c.post("/api/sessions/ao-x-1/paths", json={"runs": ["src/a.py", "build/review"]})
    assert r.status_code == 200 and r.json() == {"paths": {"src/a.py": "/r/src/a.py", "build/review": None}}
    assert ("paths", {"id": "ao-x-1", "runs": ["src/a.py", "build/review"]}) in asked
    # the host agent's refusal is the route's, as the transcript's is; not the generic action route's
    r = c.post("/api/sessions/gone/paths", json={"runs": ["x/y"]})
    assert r.status_code == 400 and "no session gone" in r.text


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
const runs = (t) => AO.pathRuns(t).map((r) => [r.run, r.path, r.line, r.col, r.start, r.end]);
const press = (event, file, resolved, line, col) => {
  const opened = [];
  const r = AO.pathLink(event, file, resolved, line, col, (...a) => opened.push(a));
  return { r, opened };
};
const FILE = "vscode://file{path}?windowId=_blank", SSH = "vscode://vscode-remote/ssh-remote+km{path}?windowId=_blank";
(async () => {
  const rows = ["see src/agentorc/cli.py:364 and build/review.", "nothing here at all",
    "Edit(scripts/x.py) then https://github.com/eyecantell/agentorc/pull/1"];
  const term = { buffer: { active: { getLine: (y) => rows[y] === undefined ? undefined
    : { translateToString: () => rows[y] } } } };
  const asked = [];
  let fail = false;
  const answer = { "src/agentorc/cli.py": "/r/src/agentorc/cli.py", "build/review": null,
    "scripts/x.py": "/r/scripts/x.py" };
  const p = AO.pathProvider(term, "ao-x-1", FILE, (r) => { asked.push(r);
    const paths = Object.fromEntries(r.map((k) => [k, answer[k] ?? null]));
    return fail ? Promise.reject(500) : Promise.resolve({ paths }); });
  const shape = (k) => ({ text: k.text, range: k.range, decorations: k.decorations });
  const hover = (y) => new Promise((res) => p.provideLinks(y, (l) => res(l ? l.map(shape) : null)));
  const first = await hover(1), again = await hover(1), empty = await hover(2), third = await hover(3);
  const askedBefore = asked.length;
  p.clear(); fail = true;
  const failed = await hover(1);
  fail = false;
  const after = await hover(1);
  let link;
  p.provideLinks(1, (l) => { link = l; });
  await new Promise((r) => setImmediate(r));
  const opened = [];
  const savedOpen = AO.openEditor; AO.openEditor = (...a) => opened.push(a);
  const plainClick = link[0].activate({}), ctrlClick = link[0].activate({ ctrlKey: true });
  AO.openEditor = savedOpen;
  console.log(JSON.stringify({
    one: runs("see src/agentorc/cli.py:364 and build/review."),
    shapes: runs("(./a/b.py) ~/x/y.md, /etc/hosts; ../up/z.ts:3:7] word a/"),
    url: runs("https://github.com/a/b/pull/1 and http://x/y/z"),
    bare: runs("no paths, just words and a.b and ./ and /"),
    first, again, empty, third, askedBefore, asked, failed, after, plainClick, ctrlClick, opened,
    file: press({ ctrlKey: true }, FILE, "/r/my dir/a#b.py", 12, 3),
    fileNoLine: press({ metaKey: true }, FILE, "/r/a.py", null, null),
    ssh: press({ ctrlKey: true }, SSH, "/r/a.py", 12, null),
    plain: press({}, FILE, "/r/a.py", 1, null),
    none: press({ ctrlKey: true }, FILE, null, 1, null),
  }));
})();
"""


def _probe() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the provider is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "path_probe.js"
    probe.write_text(PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.unit
def test_the_runs_a_row_yields():
    got = _probe()
    assert got["one"] == [["src/agentorc/cli.py:364", "src/agentorc/cli.py", 364, None, 4, 27],
                          ["build/review", "build/review", None, None, 32, 44]]  # fmt: skip
    # relative, ./, ~/, ../ and absolute; the bracket or sentence stop at the tail is not the run's
    assert [r[0] for r in got["shapes"]] == ["./a/b.py", "~/x/y.md", "/etc/hosts", "../up/z.ts:3:7"]
    assert got["shapes"][3][2:4] == [3, 7]
    # a run inside a URL is the URL's, and a bare word, `./` or `/` is never a candidate
    assert got["url"] == [] and got["bare"] == []


@pytest.mark.unit
def test_a_row_is_asked_once_and_only_what_the_host_resolved_is_a_link():
    got = _probe()
    link = {"text": "src/agentorc/cli.py:364", "range": {"start": {"x": 5, "y": 1}, "end": {"x": 27, "y": 1}},
            "decorations": {"underline": True, "pointerCursor": True}}  # fmt: skip
    # build/review in the same row is not a file: text
    assert got["first"] == [link] and got["again"] == [link]
    assert got["empty"] is None  # a row with no candidates asks nothing
    assert [k["text"] for k in got["third"]] == ["scripts/x.py"]  # the URL on the row is the addon's
    assert got["askedBefore"] == 2  # rows 1 and 3, once each; the second hover was answered from memory
    assert got["asked"][0] == ["src/agentorc/cli.py", "build/review"]
    # a failed ask is no link and remembered nowhere: the next hover asks again
    assert got["failed"] is None and got["after"] == [link]


@pytest.mark.unit
def test_only_ctrl_or_cmd_opens_and_the_line_rides_on_the_vscode_file_form_alone():
    got = _probe()
    assert got["plainClick"] is False and got["ctrlClick"] is True
    assert got["opened"] == [["vscode://file/r/src/agentorc/cli.py:364?windowId=_blank", "the editor"]]
    # percent-encoded, `/` kept (§5, TD-011); the line and column after the path, before the query
    assert got["file"]["opened"] == [["vscode://file/r/my%20dir/a%23b.py:12:3?windowId=_blank", "the editor"]]
    assert got["fileNoLine"]["opened"] == [["vscode://file/r/a.py?windowId=_blank", "the editor"]]
    # the ssh-remote form opens the file, no line
    assert got["ssh"]["opened"] == [["vscode://vscode-remote/ssh-remote+km/r/a.py?windowId=_blank", "the editor"]]
    assert got["plain"] == {"r": False, "opened": []} and got["none"] == {"r": False, "opened": []}


@pytest.mark.unit
def test_focus_registers_the_provider_only_where_the_header_draws_its_editor_button():
    js = (UI / "static" / "app.js").read_text()
    focus = js[js.index("AO.focus = function") :]
    made = "const pathLinks = s.editor && s.editor.file ? AO.pathProvider(term, id, s.editor.file) : null;"
    assert made in focus and "if (pathLinks) term.registerLinkProvider(pathLinks);" in focus
    # beside the web-links addon, before the attach decides read-only; a re-attach forgets the answers
    assert focus.index("new WebLinksAddon.WebLinksAddon") < focus.index(made) < focus.index("let readOnly = false")
    assert "if (pathLinks) pathLinks.clear();" in focus


@pytest.mark.unit
def test_the_help_carries_the_path_link_under_focus():
    h = {x.key: x for x in helpmod.HELP}["pane-path"]
    assert h.name == "a path is a link" and h.where == "Focus pane"
    assert "Ctrl+click (Cmd+click on a Mac)" in h.text and "no file links" in h.text
    assert "pane-path" in dict((g[0], g[2]) for g in helpmod.SCREENS)["focus"]
