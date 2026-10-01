"""TD-227 (the review of #824): the Inbox's team start row links an entry as `/repo/<name>#TD-n`, and
the Repo page folds each Technical debt list past its fourth row. A folded row is `display: none`,
which a browser cannot scroll to, so the page unfolds the list its hash names. The hash's reading is
JavaScript, run as itself under node; the wiring is read from the source and the template."""

import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest

UI = pathlib.Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui"
JS = UI / "static" / "app.js"

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
const h = window.AO.hashId;
console.log(JSON.stringify([h("#TD-227"), h(""), h("#"), h(undefined), h("#a%20b"), h("#%E0%A4%A")]));
"""


@pytest.mark.unit
def test_the_hash_names_an_id_or_nothing():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "hash_probe.js"
    probe.write_text(PROBE)
    out = subprocess.run([node, str(probe), str(JS)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    # a hash that does not decode names nothing, and never throws on the page's first run
    assert json.loads(out.stdout) == ["TD-227", "", "", "", "a b", ""]


@pytest.mark.unit
def test_the_repo_page_unfolds_the_list_its_hash_names():
    js = JS.read_text()
    start = js.index("AO.repo = function ()")
    body = js[start : js.index("\n  };\n", start)]
    show = body[body.index("function showHash()") :]
    show = show[: show.index("\n    }\n")]
    # only a ledger row of this page (it carries its list), unfolded before it is scrolled to
    assert "box.contains(row)" in show and "row.dataset.list" in show
    assert show.index("unfold(row.dataset.list)") < show.index("row.scrollIntoView(")
    # on the first draw, and when a link on the page itself changes the hash
    assert body.index("showHash();") < body.index("setInterval(reread")
    assert 'window.addEventListener("hashchange", showHash)' in body
    # the unfolding is the *+n more* press's own, so a re-read keeps it (`data-unfolded`)
    assert "unfolded.forEach((k) => unfold(k))" in body
    # …and the template gives each row the id the link names and the list the fold is keyed on
    part = (UI / "templates" / "repo_part.html").read_text()
    assert 'id="{{ e.id }}" data-list="{{ l.key }}"' in part
    assert 'href="/repo/{{ e.repo }}#{{ i }}"' in (UI / "templates" / "inbox_row.html").read_text()
