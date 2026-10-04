"""The closed markdown subset a message to the person is drawn in, and the fold (design §4.10 *How a
message to a person is written*, §4.5a **Inbox row: details**; TD-138): one test per construct and
one per refusal, because *no raw HTML reaches a page* is a property of `agentorc.ui.render`'s code
and these are what hold it there."""

from __future__ import annotations

import pytest

from agentorc.ui.render import FOLD_CHARS, fold, inline, render

pytestmark = pytest.mark.unit
ORIGIN = "http://kmaster:8765"


# -- the subset ------------------------------------------------------------------------------------


def test_paragraphs_split_on_blank_lines_and_keep_their_line_breaks():
    assert render("one\ntwo\n\nthree") == "<p>one<br>two</p><p>three</p>"


def test_emphasis_strong_and_code():
    assert render("a *b* **c** `d`") == "<p>a <em>b</em> <strong>c</strong> <code>d</code></p>"


def test_code_span_contents_are_characters():
    assert render("`**x** <b>`") == "<p><code>**x** &lt;b&gt;</code></p>"


def test_a_lone_star_is_not_emphasis():
    assert render("2 * 3 * 4 and snake_case_name") == "<p>2 * 3 * 4 and snake_case_name</p>"


def test_fenced_code_keeps_its_blank_lines_and_escapes():
    assert render("```\na <i>\n\nb\n```\nafter") == "<pre><code>a &lt;i&gt;\n\nb</code></pre><p>after</p>"


def test_bullet_and_numbered_lists():
    got = render("verdict\n- one\n- two\n  continued\n1. a\n2. b")
    assert got == "<p>verdict</p><ul><li>one</li><li>two<br>continued</li></ul><ol><li>a</li><li>b</li></ol>"


def test_a_deeper_item_opens_a_list_inside_the_item_above_it():
    """§4.5a *Inbox board row: detail block* (TD-312): lists nest by indent — a `-` or `1.` deeper
    than the item above opens a list inside it, a shallower one closes back out, every character
    still escaped, no tag beyond `ul`, `ol` and `li`."""
    got = render("- **Context:** a <b>\n  - x\n  - y\n    1. deep\n      more\n- **Question:** q?")
    assert got == (
        "<ul><li><strong>Context:</strong> a &lt;b&gt;<ul><li>x</li><li>y<ol><li>deep<br>more</li></ol></li></ul></li>"
        "<li><strong>Question:</strong> q?</li></ul>"
    )
    # the other kind at the same indent is a list of its own; a first item deeper than the next stands alone
    assert render("- a\n1. b") == "<ul><li>a</li></ul><ol><li>b</li></ol>"
    assert render("  - a\n- b") == "<ul><li>a</li></ul><ul><li>b</li></ul>"
    assert render("- a\n\t- b") == "<ul><li>a<ul><li>b</li></ul></li></ul>"


def test_the_allowed_link_opens_a_new_tab_and_names_its_host():
    got = render("see [docs](https://example.com/p?q=1&r=2)", ORIGIN)
    assert got == (
        '<p>see <a href="https://example.com/p?q=1&amp;r=2" target="_blank" rel="noopener noreferrer">docs</a>'
        ' <small class="lhost">example.com</small></p>'
    )


# -- the refusals: each is drawn as the characters typed -------------------------------------------


@pytest.mark.parametrize(
    ("src", "out"),
    [
        ("<b>x</b>", "<p>&lt;b&gt;x&lt;/b&gt;</p>"),
        ("<script>alert(1)</script>", "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"),
        ("![a](https://example.com/i.png)", "<p>![a](https://example.com/i.png)</p>"),
        ("# a heading", "<p># a heading</p>"),
        ("| a | b |\n|---|---|", "<p>| a | b |<br>|---|---|</p>"),
        ("[Allow](/api/x)", "<p>[Allow](/api/x)</p>"),
        ("[x](javascript:alert(1))", "<p>[x](javascript:alert(1))</p>"),
        ("[x](http://kmaster:8765/api/sessions/a/kill)", "<p>[x](http://kmaster:8765/api/sessions/a/kill)</p>"),
    ],
)
def test_outside_the_subset_is_its_characters(src, out):
    assert render(src, ORIGIN) == out


def test_a_quote_in_a_link_target_stays_inside_its_attribute():
    got = render('[x](https://e.com/"onmouseover=1)', ORIGIN)
    assert 'href="https://e.com/&quot;onmouseover=1"' in got and " onmouseover" not in got


def test_link_text_is_escaped_not_rendered():
    got = render("[<b>x</b>](https://example.com)", ORIGIN)
    assert "&lt;b&gt;x&lt;/b&gt;</a>" in got and "<b>" not in got


# -- the fold ---------------------------------------------------------------------------------------


def test_a_shaped_text_folds_at_its_blank_line():
    assert fold("merged #517 — one gap left\n\n- read against §4.2a\n- gate green") == (
        "merged #517 — one gap left",
        "- read against §4.2a\n- gate green",
    )


def test_an_unshaped_long_text_folds_at_the_last_sentence_before_the_backstop():
    text = ("This sentence is about forty characters. " * 50).strip()
    lead, rest = fold(text)
    assert len(lead) <= FOLD_CHARS and lead.endswith(".") and rest and f"{lead} {rest}" == text


def test_an_unbroken_long_text_folds_at_the_backstop():
    lead, rest = fold("x" * 2000)
    assert (len(lead), len(rest)) == (FOLD_CHARS, 2000 - FOLD_CHARS)


def test_a_short_text_has_nothing_to_fold():
    assert fold("a" * 200) == ("a" * 200, "")


def test_a_blank_line_inside_a_code_block_is_not_the_fold():
    assert fold("```\na\n\nb\n```\nverdict\n\nreading") == ("```\na\n\nb\n```\nverdict", "reading")


@pytest.mark.unit
def test_without_links_a_link_is_its_characters_everywhere_in_the_text():
    """TD-283's **Told at start**: text in which nothing may be pressable — a link stays its
    characters, in a paragraph, a list item and inside emphasis, while the rest of the subset renders."""
    text = "See [a](https://example.com).\n\n- **[b](https://example.org)** and `c`"
    html = render(text, links=False)
    assert "<a " not in html and "[a](https://example.com)" in html and "[b](https://example.org)" in html
    assert "<strong>" in html and "<code>c</code>" in html and "<li>" in html
    assert "<a " in render(text)  # the default draws them, as before
    assert "<a " not in inline("[a](https://example.com)", links=False)
