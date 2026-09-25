"""design §4.5 *Type scale* (TD-130, built by TD-144): the pages set their sizes as tokens on
`:root`, and no rule outside the token block names a pixel size — so a literal cannot creep back
in beside the scale, and moving `--t-body` moves every page."""

from __future__ import annotations

import pathlib
import re

import pytest

CSS = pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui" / "static" / "app.css"
TOKENS = ("--t-body", "--t-small", "--t-cap", "--t-title", "--t-mono", "--t-mono-s", "--t-btn", "--lh", "--lh-mono")

pytestmark = pytest.mark.unit


def _rules(css: str) -> list[tuple[str, str]]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(" ".join(sel.split()), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def test_the_tokens_are_on_root():
    root = next(body for sel, body in _rules(CSS.read_text(encoding="utf-8")) if sel == ":root")
    for t in TOKENS:
        assert re.search(rf"{re.escape(t)}:\s*[\d.]+(px)?;", root), f"{t} is not set on :root"


def test_no_rule_outside_root_names_a_pixel_font_size():
    bad = [
        f"{sel} {{ {decl.strip()} }}"
        for sel, body in _rules(CSS.read_text(encoding="utf-8"))
        if sel != ":root" and "@font-face" not in sel
        for decl in re.findall(r"font(?:-size)?\s*:[^;]*", body)
        if re.search(r"\d(px|pt)\b", decl)
    ]
    assert bad == [], "size literals outside the type scale (design §4.5):\n" + "\n".join(bad)
