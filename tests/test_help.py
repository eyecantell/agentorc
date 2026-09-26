"""The help text (design §4.5a *The help text*, §4.5 screen 10 *Help*; TD-157, built by TD-167).

The design's list is the one place the text is written: `help.py` must equal it word for word and
key for key, every mark and every titled control of the set in the templates must name a key in
`help.py`, and the Help page must carry every paragraph once. This test is what makes that so."""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).parents[1]
UI = ROOT / "src" / "agentorc" / "ui"

pytestmark = pytest.mark.unit


def design_help() -> list[tuple[str, str, str]]:
    """`(name, where, paragraph)` for each bullet under *The help text* in docs/design.md."""
    s = (ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    block = s[s.index("**The help text (TD-157).**") :]
    block = block[: block.index("\n\n", block.index("\n- **"))]  # the list ends at its blank line
    items = re.findall(r"^- \*\*(.+?)\*\* \((.+?)\) — (.*?)(?=^- \*\*|\Z)", block, re.S | re.M)
    return [(n, w, " ".join(p.split())) for n, w, p in items]


def test_help_py_is_the_designs_list_word_for_word():
    from agentorc.ui.help import HELP

    assert [(h.name, h.where, h.text) for h in HELP] == design_help()
    assert len({h.key for h in HELP}) == len(HELP)


def test_every_mark_and_titled_control_names_a_key():
    from agentorc.ui.help import BY_KEY, GROUPS, SCREENS

    text = "".join(p.read_text(encoding="utf-8") for p in (UI / "templates").glob("*.html"))
    titled = set(re.findall(r"help_title\('([\w-]+)'\)", text))
    marks = set(re.findall(r"\bhm2?\.(?:mark|panel)\('([\w-]+)'", text))
    assert titled and titled <= set(BY_KEY), titled - set(BY_KEY)
    assert marks == set(GROUPS), marks
    for keys in GROUPS.values():
        assert set(keys) <= set(BY_KEY)
    assert sorted(k for _, _, ks in SCREENS for k in ks) == sorted(BY_KEY)  # each paragraph on the page once
    # the set whose titles §4.5a names: the team card's controls, the card's foot, the Focus header
    for key in (
        "start",
        "wind-down",
        "stop-now",
        "forget-all",
        "fold",
        "forget",
        "close",
        "kill",
        "wrap-up",
        "message",
    ):
        assert key in titled, key


def test_a_title_is_its_paragraphs_first_sentence():
    from agentorc.ui.help import BY_KEY, first_sentence

    assert first_sentence("forget") == "Drops this session's record: the card, its report line and its mail."
    for h in BY_KEY.values():
        assert h.text.startswith(first_sentence(h.key)) and first_sentence(h.key).endswith((".", "!", "?"))


def test_the_marks_draw_the_inbox_shape_and_the_panel_ends_in_help(monkeypatch, tmp_path):
    """§4.5a the ***i*** mark: a `<button>` labelled *About these controls* with `aria-expanded`,
    `aria-controls` and `aria-describedby` on a panel always in the page and `hidden`, one paragraph
    per control of the group, then *every control → Help* to the group's screen."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates

    mod = templates.get_template("help_mark.html").module
    mark, panel = str(mod.mark("focus", "focus")), str(mod.panel("focus", "focus"))
    assert 'aria-label="About these controls"' in mark and 'aria-controls="help-focus"' in mark
    assert 'aria-expanded="false"' in mark and 'aria-describedby="help-focus"' in mark
    assert 'id="help-focus"' in panel and " hidden>" in panel and panel.count("<p><b>") == 4
    assert '<a href="/help#focus">every control → Help</a>' in panel


def test_the_help_page_carries_every_paragraph_once(subprocess_agent):
    from fastapi.testclient import TestClient

    from agentorc.ui.app import create_app
    from agentorc.ui.help import HELP

    with TestClient(create_app()) as c:
        page = c.get("/help")
    assert page.status_code == 200
    for h in HELP:
        assert page.text.count(h.text.replace("'", "&#39;").replace('"', "&#34;")) == 1, h.key
        assert f'id="{h.key}"' in page.text
    assert 'id="org"' in page.text and 'id="focus"' in page.text
    body = page.text.split('class="page helppage"')[1]
    assert "<button" not in body and "data-act" not in body  # nothing on it is a control
