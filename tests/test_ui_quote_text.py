"""TD-296 #4: the composer's *re:* quote showed a board line's raw `**` and backticks, which the row
itself draws as markup (TD-279). `AO.quoteText` takes the closed subset's inline marks off before
the quote is drawn — JavaScript, run as itself under node."""

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
const q = window.AO.quoteText;
console.log(JSON.stringify(process.argv.slice(3).map(q)));
"""

CASES = {
    "**A fixture look.** Say whether `ao repo` reads right.": "A fixture look. Say whether ao repo reads right.",
    "see [the PR](https://github.com/x/y/pull/9) and *this* one": "see the PR and this one",
    "2 * 3 * 4 and a_b_c stay": "2 * 3 * 4 and a_b_c stay",
    "![an image](x.png) stays": "![an image](x.png) stays",
    "[a](https://u)[b](https://u) side by side": "ab side by side",
    "": "",
}


@pytest.mark.unit
def test_the_quote_drops_the_inline_marks_and_nothing_else():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(PROBE)
    out = subprocess.run([node, f.name, str(JS), *CASES], capture_output=True, text=True, timeout=30)
    pathlib.Path(f.name).unlink()
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == list(CASES.values())
    assert "const q = AO.quoteText(o.quote);" in JS.read_text()  # the composer draws the quote through it
