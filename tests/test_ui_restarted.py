"""The **restarted** chip (design §4.5a, §6 *Keeping a team running*, §4.10 *A lapsed cache is started
again, not rung*; TD-485, built by TD-487): a member the tick or the doorbell restarted said so in
`ao status -v` alone, and read like a fresh start on its card and its Focus."""

from __future__ import annotations

import html as htmllib
from datetime import UTC, datetime, timedelta

import pytest

from agentorc import cli
from agentorc.ui.cards import restarted_view

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def ago(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


CACHE = {"why": "cache", "idle": 5.2, "context": 191_000}

# one table: the entry, what `ao status -v` says of it, the Focus chip, the card's short form
WORDS = [
    (CACHE, "cache lapsed · idle 5h · 191k", "restarted · cache lapsed · idle 5h · 191k", "restarted · cache lapsed"),
    ({"why": "brief"}, "brief", "restarted · brief", "restarted · brief"),
    ({"why": "wanted"}, "wanted", "restarted · wanted", "restarted · wanted"),
    ({"why": "person"}, "person", "restarted · person", "restarted · person"),
    ({"why": "brief", "error": "close: gone"}, "brief · failed", "restarted · brief · failed", "restarted · brief"),
]


@pytest.mark.unit
@pytest.mark.parametrize(("entry", "line", "text", "short"), WORDS)
def test_the_chip_and_status_say_a_restart_in_the_same_words(entry, line, text, short):
    got = restarted_view([{**entry, "at": ago(minutes=10)}], NOW)
    assert got["text"] == text and got["short"] == short
    assert cli.restarts_line([{**entry}]) == line  # `ao status -v`'s words, the chip's after *restarted ·*
    assert got["text"] == f"restarted · {line}"


@pytest.mark.unit
def test_a_restart_inside_the_window_draws_and_nothing_else_does():
    """A `cache` entry ten minutes old draws; a `start` alone, a `fill` alone, an entry three hours old,
    an unreadable instant and a malformed field draw none."""
    assert restarted_view([{**CACHE, "at": ago(minutes=10)}], NOW)["text"] == (
        "restarted · cache lapsed · idle 5h · 191k"
    )
    for restarts in (
        [{"why": "start", "at": ago(minutes=10)}],
        [{"why": "fill", "at": ago(minutes=10)}],
        [{"why": "brief", "at": ago(hours=3)}],
        [{"why": "brief", "at": ago(hours=2)}],  # the window's edge: gone with the ceiling's clock
        [{"why": "brief", "at": "half six"}, "brief", None],
        None,
        "brief",
        [],
    ):
        assert restarted_view(restarts, NOW) is None, restarts


@pytest.mark.unit
def test_the_hover_lists_the_window_newest_first_and_counts_toward_the_ceiling():
    restarts = [
        {"why": "start", "at": ago(hours=1, minutes=50)},
        {"why": "brief", "at": ago(hours=1, minutes=30)},
        {"why": "brief", "at": ago(hours=4)},  # outside the window: neither listed nor counted
        {**CACHE, "at": ago(minutes=10)},
    ]
    got = restarted_view(restarts, NOW)
    assert got["short"] == "restarted · cache lapsed"
    lines = got["hover"].split("\n")
    assert lines[:2] == ["cache lapsed · idle 5h · 191k, 10m ago", "brief, 1h 30m ago"]
    assert lines[2].endswith(" of 3 in 2 h")
    assert got["until"] == (NOW - timedelta(minutes=10) + timedelta(hours=2)).isoformat()  # the newest's clock
    # the count is the ceiling's own rule: a `person` restart never counts
    person = restarted_view([{"why": "person", "at": ago(minutes=5)}, {"why": "brief", "at": ago(minutes=20)}], NOW)
    assert person["hover"].split("\n")[-1] == "1 of 3 in 2 h"
    assert person["text"] == "restarted · person"
    # a malformed `done` costs the count its rule, never the page (view() runs for every card)
    bad = restarted_view(
        [{"why": "brief", "at": ago(minutes=30), "done": 5}, {"why": "wanted", "at": ago(minutes=5), "done": 5}], NOW
    )
    assert bad["text"] == "restarted · wanted" and bad["hover"].split("\n")[-1] == "2 of 3 in 2 h"


@pytest.mark.unit
def test_the_card_and_the_focus_draw_the_chip_and_a_seat_never(tmp_path, monkeypatch):
    """The card's row 4 carries the short form beside the report line and the Focus header the whole
    words beside *brief changed*, each with the hover as its title; a seat draws neither."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    at = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    base = {"id": "ao-x-9", "name": "w", "kind": "agent", "adapter": "claude-code", "dir": str(tmp_path),
            "state": "idle", "since": at, "confidence": "hook", "pane": True, "tail": ["…"], "created": at,
            "restarts": [{"why": "brief", "at": at}]}  # fmt: skip
    v = view(base)
    assert v["restarted"]["short"] == "restarted · brief"
    card = templates.get_template("card.html").render(s=v)
    r4 = card[card.index('<div class="row r4">') :]
    r4 = r4[: r4.index("</div>")]
    assert (
        f'<span class="badge restarted" data-until="{v["restarted"]["until"]}" title="brief, 10m ago\n' in r4
        and ">restarted · brief</span>" in r4
    )
    focus = templates.get_template("focus.html").render(
        s={**v, "grants_all": [], "ready": []}, host="h", active="Org", popped=False
    )
    chip = focus[focus.index('id="frestarted"') - 40 :]
    chip = chip[: chip.index("</span>")]
    assert 'class="badge restarted" id="frestarted" data-until="' in chip and "hidden" not in chip
    assert chip.endswith(">restarted · brief") and htmllib.escape("of 3 in 2 h") in chip
    assert focus.index('id="fbc"') < focus.index('id="frestarted"')
    for seat in ({"seat": {"on": "ask"}}, {}):
        sv = view({**base, **seat}, seats={"ao-x-9": "on a question"} if not seat else None)
        assert sv["restarted"] is None
        assert 'class="badge restarted"' not in templates.get_template("card.html").render(s=sv)
    plain = view({**base, "restarts": []})
    focus = templates.get_template("focus.html").render(
        s={**plain, "grants_all": [], "ready": []}, host="h", active="Org", popped=False
    )
    assert 'class="badge restarted hidden" id="frestarted" data-until=""' in focus


@pytest.mark.unit
def test_the_focus_keeps_the_chip_current_from_the_delta():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui" / "static" / "app.js").read_text()
    body = js[js.index('const rs = $("#frestarted");') :]
    body = body[: body.index("\n      }\n")]
    assert 'rs.classList.toggle("hidden", !v.restarted);' in body
    assert "rs.title = (v.restarted && v.restarted.hover)" in body
    assert "rs.textContent = (v.restarted && v.restarted.text)" in body


@pytest.mark.unit
def test_the_page_hides_the_chip_when_its_window_passes(tmp_path):
    """Between deltas: a chip whose `data-until` has passed is hidden by the page's minute look, so an
    idle card does not keep it past the window; an unreadable instant hides nothing."""
    import json
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    app = Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui" / "static" / "app.js"
    probe = tmp_path / "until_probe.js"
    probe.write_text("""
const fs = require("fs"); const noop = () => {};
const el = () => ({ dataset: {}, style: {}, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [], contains: () => false });
global.window = {}; global.document = { documentElement: el(), body: el(), querySelector: () => null,
  querySelectorAll: () => [], addEventListener: noop, createElement: () => el() };
global.localStorage = { getItem: () => null, setItem: noop }; global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const at = Date.parse("2026-10-10T14:00:00+00:00");
console.log(JSON.stringify([
  window.AO.restartedExpired("2026-10-10T14:00:00+00:00", at - 1),
  window.AO.restartedExpired("2026-10-10T14:00:00+00:00", at),
  window.AO.restartedExpired("", at), window.AO.restartedExpired("half six", at),
]));
""")
    run = subprocess.run([node, str(probe), str(app)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == [False, True, False, False]
    js = app.read_text()
    assert 'document.querySelectorAll(".badge.restarted[data-until]")' in js
    assert "rs.dataset.until = (v.restarted && v.restarted.until)" in js
