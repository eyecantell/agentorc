"""The Org's page chrome (design §4.5a top bar **+ New ▾**, Org **filter…**; TD-418, built by TD-428
slice 1): one **+ New ▾** button whose menu holds **Session** and **Shell**, and the filter box's words
`mine` and `kind:command` in place of the ***mine*** toggle and the *show command runs* box, each word
composing with `team:` and `state:` — a card is shown when it passes them all."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest
from test_ui import client  # noqa: F401 — the fixture, by name

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
const cards = JSON.parse(process.argv[3]), filters = JSON.parse(process.argv[4]);
const out = {};
filters.forEach((f) => {
  const w = AO.orgWords(f);
  out[f] = Object.keys(cards).filter((k) => AO.orgPasses(cards[k], w));
});
console.log(JSON.stringify(out));
"""

# what each card says of itself: its `data-*` and its text (`data-mine` is "1" or absent)
CARDS = {
    "me": {"kind": "agent", "team": "ao-grind", "pill": "idle", "mine": "1", "text": "me agentorc main"},
    "worker": {"kind": "agent", "team": "ao-grind", "pill": "working", "text": "worker agentorc td428"},
    "mine-waiting": {"kind": "agent", "team": "sam", "pill": "waiting", "mine": "1", "text": "mine-waiting samscrape"},
    "run": {"kind": "command", "team": "ao-grind", "pill": "working", "text": "run pdm test"},
    "my-run": {"kind": "command", "team": "", "pill": "idle", "mine": "1", "text": "my-run ls"},
    "loose": {"kind": "agent", "team": "", "pill": "idle", "text": "loose scratch"},
}


def _passes(*filters: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the Org's filter is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "org_filter_probe.js"
    probe.write_text(PROBE)
    run = [node, str(probe), str(UI / "static" / "app.js"), json.dumps(CARDS), json.dumps(list(filters))]
    out = subprocess.run(run, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return {k: set(v) for k, v in json.loads(out.stdout).items()}


@pytest.mark.unit
def test_the_bare_page_hides_command_runs_and_kind_command_shows_them():
    got = _passes("", "kind:command", "KIND:COMMAND")
    assert got[""] == {"me", "worker", "mine-waiting", "loose"}
    assert got["kind:command"] == set(CARDS) == got["KIND:COMMAND"]


@pytest.mark.unit
def test_mine_is_the_persons_own_and_the_whole_word_alone():
    got = _passes("mine", "mine kind:command", "mined")
    assert got["mine"] == {"me", "mine-waiting"}
    assert got["mine kind:command"] == {"me", "mine-waiting", "my-run"}
    assert got["mined"] == set()  # text, not the word: no card's text holds it


@pytest.mark.unit
def test_mine_and_kind_command_compose_with_team_and_state():
    got = _passes(
        "team:ao-grind mine",
        "mine team:ao-grind",
        "state:idle mine",
        "state:working kind:command",
        "team:ao-grind kind:command state:working",
        "team:ao-grind state:working",
        "mine state:idle kind:command",
        "team:sam state:waiting mine",
        "team:ao-grind mine state:working",
    )
    assert got["team:ao-grind mine"] == {"me"} == got["mine team:ao-grind"]
    assert got["state:idle mine"] == {"me"}
    assert got["state:working kind:command"] == {"worker", "run"}
    assert got["team:ao-grind kind:command state:working"] == {"worker", "run"}
    assert got["team:ao-grind state:working"] == {"worker"}  # the run stays hidden without the word
    assert got["mine state:idle kind:command"] == {"me", "my-run"}
    assert got["team:sam state:waiting mine"] == {"mine-waiting"}
    assert got["team:ao-grind mine state:working"] == set()


@pytest.mark.unit
def test_text_words_each_must_match_beside_the_others():
    got = _passes("agentorc", "agentorc td428", "mine agentorc", "scratch kind:command")
    assert got["agentorc"] == {"me", "worker"}
    assert got["agentorc td428"] == {"worker"}
    assert got["mine agentorc"] == {"me"}
    assert got["scratch kind:command"] == {"loose"}


def test_the_top_bar_draws_one_new_button_whose_menu_is_session_and_shell(client):  # noqa: F811
    html = client.get("/").text
    top = html[html.index('id="newmenu"') : html.index("</details>", html.index('id="newmenu"'))]
    assert ">+ New ▾</summary>" in top
    assert '<a href="/new" id="newsession"' in top and ">Session</a>" in top
    assert 'id="shellbtn"' in top and ">Shell</button>" in top
    assert html.count('href="/new"') == 1  # `n` presses the one link (§4.5a *keys*)
    assert "+ New session<" not in html and "⌘ Shell" not in html
    assert 'id="mine"' not in html and 'id="showcmd"' not in html and "show command runs" not in html


def test_no_template_carries_the_toggle_or_the_box_and_settings_forgets_mine():
    from agentorc.ui import settings_page

    for t in (UI / "templates").glob("*.html"):
        body = t.read_text()
        assert 'id="mine"' not in body and 'id="showcmd"' not in body, t.name
    assert "mine" not in [k for k, _, _ in settings_page.BROWSER_KEYS]
    js = (UI / "static" / "app.js").read_text()
    assert 'store.get("mine"' not in js and "#showcmd" not in js
    assert """{ keys: ["n"], page: "all", control: "New session", sel: '.topbar a[href="/new"]' }""" in js
